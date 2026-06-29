"""
One-off (or re-run-on-demand) ingestion script for the Info Agent's
knowledge base.

What it does:
  1. Walks data/raw_documents/ and converts every supported file (.pdf,
     .txt, .md) into a Docling document using the GraniteDocling VLM
     pipeline — this understands layout and tables, not just flat text,
     which plain pypdf text extraction cannot do.
  2. Chunks each Docling document using Docling's HybridChunker. This is
     structure-aware: table rows are kept intact (with the header repeated
     if a table ever spans multiple chunks), instead of being sliced by a
     blind word-count window. A table row like "Offence: nuisance |
     Penalty: Rs.2,500" can never be split across two chunks.
  3. Embeds each chunk using the local Ollama embedding model.
  4. Upserts each chunk (vector + text + metadata) into a Qdrant collection.

Run this whenever you add/change documents in data/raw_documents/:

    python -m prerun.ingest_info_docs

It is idempotent in the sense that it recreates the collection from scratch
each run (simplest correct behavior for a "few hundred FAQ docs" use case —
no stale chunks left behind from deleted/edited source files).

Note on first run: this needs internet access once. GraniteDocling
downloads its model weights (~a few hundred MB), and HybridChunker's
default tokenizer downloads a small HF tokenizer config the first time
it's used. Both are cached locally afterward — every later run, and the
actual ingestion/embedding/Qdrant work itself, is fully offline.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from docling.chunking import HybridChunker
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import VlmPipelineOptions
from docling.datamodel import vlm_model_specs
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.pipeline.vlm_pipeline import VlmPipeline
from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams

from common.embeddings import embed_batch
from common.settings import (
    EMBEDDING_DIM,
    INFO_AGENT_COLLECTION,
    QDRANT_URL,
    RAW_DOCUMENTS_DIR,
)

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

# Batch size for embedding + upserting, to avoid sending one giant request
# to Ollama / Qdrant when you have a lot of chunks.
BATCH_SIZE = 32

# GraniteDocling (VLM pipeline) for PDFs — understands layout + tables
# instead of just extracting a flat text stream. Built once and reused
# across all PDF conversions in this run.
# _pdf_converter = DocumentConverter(
#     format_options={
#         InputFormat.PDF: PdfFormatOption(
#             pipeline_cls=VlmPipeline,
#             pipeline_options=VlmPipelineOptions(
#                 vlm_options=vlm_model_specs.GRANITEDOCLING_TRANSFORMERS,
#             ),
#         ),
#     }
# )

pdf_pipeline_options = PdfPipelineOptions(
    artifacts_path=r"C:\Users\TRIDNT\Documents\Programming\DRDO\CodeRefactingServer2\CodeRefactingServer\docling_models",
    do_ocr = True,
    do_table_structure = True,
    ocr_options = EasyOcrOptions(
        model_storage_directory=r"C:\Users\TRIDNT\Documents\Programming\DRDO\CodeRefactingServer2\CodeRefactingServer\docling_models\EasyOcr",
        download_enabled=False  # Do not attempt to download from online repos
    )
)

pdf_pipeline_options.do_table_structure = True  # keeps table-aware parsing
_pdf_converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(
            pipeline_options=pdf_pipeline_options,
        ),
    }
)

# Intelligent, structure-aware chunker: keeps table rows intact, repeats
# table headers if a table spans multiple chunks, and avoids cutting
# paragraphs mid-sentence. Replaces the old manual word-window splitter.
_chunker = HybridChunker()


@dataclass
class Chunk:
    text: str
    source_file: str
    chunk_index: int


def convert_to_docling_document(path: Path):
    """Convert a supported file into a Docling document.

    PDFs go through the GraniteDocling VLM pipeline (layout- and
    table-aware). .txt/.md files are plain text, so Docling's default
    (non-VLM) converter handles them directly — no need for the heavier
    VLM pipeline on formats that have no layout to recover.
    """
    if path.suffix.lower() == ".pdf":
        return _pdf_converter.convert(source=str(path)).document
    return DocumentConverter().convert(source=str(path)).document


def chunk_document(doc, source_file: str) -> list[Chunk]:
    """Run Docling's HybridChunker over a converted document and return
    our simple Chunk records. This is structure-aware chunking: each
    DocChunk already respects table/paragraph boundaries, so no manual
    word-window logic is needed here."""
    chunks = []
    for idx, doc_chunk in enumerate(_chunker.chunk(dl_doc=doc)):
        text = doc_chunk.text.strip()
        if text:
            chunks.append(Chunk(text=text, source_file=source_file, chunk_index=idx))
    return chunks


def collect_chunks(raw_docs_dir: Path) -> list[Chunk]:
    """Walk the raw documents directory, convert each file with Docling,
    and chunk it with HybridChunker."""
    all_chunks: list[Chunk] = []

    files = sorted(
        p for p in raw_docs_dir.rglob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not files:
        print(
            f"⚠️  No supported documents found in {raw_docs_dir}. "
            f"Supported extensions: {sorted(SUPPORTED_EXTENSIONS)}"
        )
        return all_chunks

    for path in files:
        print(f"Converting {path.relative_to(raw_docs_dir)} (Docling) ...")
        try:
            doc = convert_to_docling_document(path)
        except Exception as exc:
            print(f"  ✗ Skipping {path.name}: {exc}")
            continue

        chunks = chunk_document(doc, source_file=path.name)
        all_chunks.extend(chunks)
        print(f"  ✓ {len(chunks)} chunks")

    return all_chunks


def recreate_collection(client: QdrantClient, collection_name: str, dim: int) -> None:
    """Drop and recreate the target collection so re-running this script
    always reflects exactly what's currently in raw_documents/."""
    existing = {c.name for c in client.get_collections().collections}
    if collection_name in existing:
        print(f"Collection '{collection_name}' already exists — recreating it.")
        client.delete_collection(collection_name)

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    print(f"Created collection '{collection_name}' (dim={dim}, distance=cosine).")


def index_chunks(client: QdrantClient, collection_name: str, chunks: list[Chunk]) -> None:
    """Embed and upsert chunks into Qdrant in batches."""
    total = len(chunks)
    for batch_start in range(0, total, BATCH_SIZE):
        batch = chunks[batch_start : batch_start + BATCH_SIZE]
        texts = [c.text for c in batch]

        print(
            f"Embedding + upserting batch "
            f"{batch_start + 1}-{batch_start + len(batch)} of {total} ..."
        )
        vectors = embed_batch(texts)

        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "text": chunk.text,
                    "source_file": chunk.source_file,
                    "chunk_index": chunk.chunk_index,
                },
            )
            for chunk, vector in zip(batch, vectors)
        ]
        client.upsert(collection_name=collection_name, points=points)

    print(f"✓ Indexed {total} chunks into '{collection_name}'.")


def main() -> None:
    raw_docs_dir = Path(RAW_DOCUMENTS_DIR)
    raw_docs_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading documents from: {raw_docs_dir}")
    print(f"Target Qdrant collection: {INFO_AGENT_COLLECTION} @ {QDRANT_URL}")
    print("-" * 60)

    chunks = collect_chunks(raw_docs_dir)
    if not chunks:
        print("Nothing to index. Add files to data/raw_documents/ and re-run.")
        sys.exit(0)

    client = QdrantClient(url=QDRANT_URL)
    recreate_collection(client, INFO_AGENT_COLLECTION, EMBEDDING_DIM)
    index_chunks(client, INFO_AGENT_COLLECTION, chunks)

    print("-" * 60)
    print("Done. The Info Agent can now query this collection via the Qdrant MCP server.")


if __name__ == "__main__":
    main()