"""
Thin wrapper around Ollama's embedding endpoint.

Used by:
  - prerun/ingest_info_docs.py   (embeds chunks at indexing time)
  - mcp_servers/qdrant_mcp        (embeds the query text at retrieval time)

Keeping this in one place guarantees the same model + same call pattern is
used for both indexing and querying — if these ever drift, retrieval quality
silently degrades, so don't duplicate this logic.
"""

from __future__ import annotations

import httpx

from common.settings import OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL

# A single shared timeout for embedding calls. Local Ollama on CPU can be
# slow on first call (model load), so this is generous on purpose.
_EMBED_TIMEOUT_SECONDS = 60.0


def embed_text(text: str, model: str = OLLAMA_EMBED_MODEL) -> list[float]:
    """
    Embed a single piece of text using Ollama's /api/embeddings endpoint.

    Args:
        text: The text to embed. Should already be cleaned/chunked.
        model: Ollama embedding model name. Defaults to the project-wide
            embedding model configured in common.settings.

    Returns:
        A list of floats representing the embedding vector.

    Raises:
        httpx.HTTPStatusError: If Ollama returns a non-2xx response.
        httpx.ConnectError: If Ollama isn't reachable at OLLAMA_BASE_URL.
    """
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text.")

    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=_EMBED_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()

    embedding = data.get("embedding")
    if not embedding:
        raise RuntimeError(
            f"Ollama returned no embedding for model '{model}'. "
            f"Is the model pulled? Try: ollama pull {model}"
        )
    return embedding


def embed_batch(texts: list[str], model: str = OLLAMA_EMBED_MODEL) -> list[list[float]]:
    """
    Embed multiple texts sequentially.

    Ollama's /api/embeddings endpoint handles one prompt per call, so this
    is a simple loop rather than a true batch call. Fine for prerun-scale
    ingestion (hundreds to low-thousands of chunks); if you outgrow this,
    parallelize with a thread pool or move to a model server that supports
    real batching.

    Args:
        texts: List of text chunks to embed.
        model: Ollama embedding model name.

    Returns:
        List of embedding vectors, in the same order as the input texts.
    """
    return [embed_text(t, model=model) for t in texts]
