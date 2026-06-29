"""
Thin wrapper around qdrant-client.

This isolates raw Qdrant calls from the MCP tool layer (server.py), so:
  - server.py stays focused on MCP protocol concerns (tool schemas,
    annotations, error formatting for the LLM)
  - this module stays focused on "how do I talk to Qdrant correctly"
  - either can be tested/swapped independently

This wrapper is intentionally generic — it knows nothing about "FAQs" or
"Info Agent". Any future agent (Support, Travel History, etc.) that wants
semantic search over its own collection can reuse the same MCP server by
just passing a different collection_name.
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http.models import CollectionInfo

from common.embeddings import embed_text
from common.settings import QDRANT_URL

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    """Lazily create and cache a single QdrantClient for the server's
    lifetime. Avoids reconnecting on every tool call."""
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL)
    return _client


def list_collections() -> list[str]:
    """Return the names of all collections currently in Qdrant."""
    client = get_client()
    return [c.name for c in client.get_collections().collections]


class QdrantUnreachableError(RuntimeError):
    """Raised when Qdrant cannot be reached, with a message safe to show an LLM."""


def list_collections_safe() -> list[str]:
    """Same as list_collections(), but raises QdrantUnreachableError with a
    clean message on connection failure instead of leaking a raw client
    exception. Tool functions in server.py should use this variant."""
    try:
        return list_collections()
    except Exception as exc:
        raise QdrantUnreachableError(
            f"Could not reach Qdrant: {type(exc).__name__}: {exc}"
        ) from exc


def collection_exists(collection_name: str) -> bool:
    return collection_name in list_collections()


def describe_collection(collection_name: str) -> dict:
    """Return basic stats/config about a collection (point count, vector
    size, distance metric) — useful for an agent to sanity-check before
    querying, or for a human debugging the system."""
    client = get_client()
    info: CollectionInfo = client.get_collection(collection_name)

    vector_params = info.config.params.vectors
    # vector_params can be a single VectorParams or a dict of named vectors;
    # this project uses a single unnamed vector per collection, so handle
    # that common case directly and fall back gracefully otherwise.
    if hasattr(vector_params, "size"):
        vector_size = vector_params.size
        distance = str(vector_params.distance)
    else:
        vector_size = None
        distance = None

    return {
        "collection_name": collection_name,
        "points_count": info.points_count,
        "vector_size": vector_size,
        "distance_metric": distance,
        "status": str(info.status),
    }


def search_collection(
    collection_name: str,
    query_text: str,
    top_k: int = 5,
    score_threshold: float | None = None,
) -> list[dict]:
    """
    Embed `query_text` and run a semantic similarity search against
    `collection_name`, returning the top_k matching chunks.

    Returns a list of dicts:
        {"text": ..., "score": ..., "source_file": ..., "chunk_index": ...}
    sorted by descending relevance score.
    """
    client = get_client()
    query_vector = embed_text(query_text)

    results = client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k,
        score_threshold=score_threshold,
        with_payload=True,
    ).points

    formatted = []
    for point in results:
        payload = point.payload or {}
        formatted.append(
            {
                "text": payload.get("text", ""),
                "score": round(point.score, 4),
                "source_file": payload.get("source_file"),
                "chunk_index": payload.get("chunk_index"),
            }
        )
    return formatted
