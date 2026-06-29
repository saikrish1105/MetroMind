"""
qdrant_retrieval_mcp — a custom MCP server wrapping Qdrant semantic search.

Why this exists (rather than agents calling Qdrant directly):
  - Keeps the "tool layer" uniform: every specialist agent calls MCP tools,
    never raw DB clients, matching the project's deterministic-tool design.
  - Lets multiple future agents reuse the same server against different
    collections (FAQ, policies, support KB, ...) just by varying the
    `collection_name` parameter — no new server needed per use case.
  - Gives you one place to add cross-cutting behavior later (logging,
    auth, rate limiting, score thresholds) without touching agent code.

Tools exposed:
  - qdrant_get_collections     : list all collections currently in Qdrant
  - qdrant_describe_collection : stats/config for one collection
  - qdrant_fetch_from_collection : semantic search — the main retrieval tool

Run standalone for local testing:
    python -m mcp_servers.qdrant_mcp.server

Run via the MCP Inspector:
    npx @modelcontextprotocol/inspector python -m mcp_servers.qdrant_mcp.server

CrewAI connects to this over stdio via the mcps=[MCPServerStdio(...)] field
on the Info Agent (see crew/agents.py).
"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from mcp.server.fastmcp import FastMCP

from mcp_servers.qdrant_mcp.qdrant_client_wrapper import (
    QdrantUnreachableError,
    describe_collection,
    list_collections_safe,
    search_collection,
)

mcp = FastMCP("qdrant_retrieval_mcp")


def _collection_not_found_response(collection_name: str, available: list[str]) -> str:
    """Shared formatting for the 'collection doesn't exist' case, used by
    both qdrant_describe_collection and qdrant_fetch_from_collection so the
    error shape is identical regardless of which tool triggered it."""
    return json.dumps(
        {
            "error": f"Collection '{collection_name}' not found.",
            "available_collections": available,
        },
        indent=2,
    )


def _unreachable_response(exc: QdrantUnreachableError) -> str:
    """Shared formatting for 'Qdrant itself is unreachable' errors."""
    return json.dumps({"error": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# Tool: list collections
# ---------------------------------------------------------------------------
@mcp.tool(
    name="qdrant_get_collections",
    annotations={
        "title": "List Qdrant Collections",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def qdrant_get_collections() -> str:
    """List every collection currently available in Qdrant.

    Use this first if you don't already know the exact collection name to
    query — e.g. before calling qdrant_fetch_from_collection.

    Returns:
        str: JSON object of the form:
            {"collections": ["metro_faq", "support_kb", ...]}
        Or, if Qdrant is unreachable:
            {"error": "Could not reach Qdrant: <details>"}
    """
    try:
        collections = list_collections_safe()
    except QdrantUnreachableError as exc:
        return _unreachable_response(exc)
    return json.dumps({"collections": collections}, indent=2)


# ---------------------------------------------------------------------------
# Tool: describe a collection
# ---------------------------------------------------------------------------
class DescribeCollectionInput(BaseModel):
    """Input model for describing a single Qdrant collection."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    collection_name: str = Field(
        ...,
        description=(
            "Exact name of the Qdrant collection to inspect, e.g. 'metro_faq'. "
            "Call qdrant_get_collections first if unsure of the name."
        ),
        min_length=1,
        max_length=200,
    )


@mcp.tool(
    name="qdrant_describe_collection",
    annotations={
        "title": "Describe Qdrant Collection",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def qdrant_describe_collection(params: DescribeCollectionInput) -> str:
    """Get metadata about a Qdrant collection: point count, vector size,
    distance metric, and status.

    Useful for sanity-checking that a collection exists and is populated
    before running a search against it.

    Args:
        params (DescribeCollectionInput): Contains:
            - collection_name (str): Exact collection name.

    Returns:
        str: JSON object of the form:
            {
              "collection_name": "metro_faq",
              "points_count": 142,
              "vector_size": 768,
              "distance_metric": "Distance.COSINE",
              "status": "CollectionStatus.GREEN"
            }
        Or, if the collection doesn't exist:
            {"error": "Collection 'x' not found.", "available_collections": [...]}
        Or, if Qdrant is unreachable:
            {"error": "Could not reach Qdrant: <details>"}
    """
    try:
        available = list_collections_safe()
    except QdrantUnreachableError as exc:
        return _unreachable_response(exc)

    if params.collection_name not in available:
        return _collection_not_found_response(params.collection_name, available)

    info = describe_collection(params.collection_name)
    return json.dumps(info, indent=2)


# ---------------------------------------------------------------------------
# Tool: fetch / semantic search from a collection (the main retrieval tool)
# ---------------------------------------------------------------------------
def _coerce_optional_none(value):
    """Some local LLMs serialize an 'empty' optional field as the literal
    string 'null'/'none'/'' instead of omitting the key or sending JSON
    null. Treat those as None so a slightly-malformed tool call still
    succeeds instead of failing Pydantic validation outright."""
    if isinstance(value, str) and value.strip().lower() in ("null", "none", ""):
        return None
    return value


def _coerce_numeric_string(value):
    """Some local LLMs send numeric tool arguments as strings (e.g. '5'
    instead of 5). Pydantic's strict-ish JSON schema validation rejects
    that by default for tool calls — coerce numeric strings here instead
    of relying on every model to format arguments perfectly."""
    value = _coerce_optional_none(value)
    if isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value  # let Pydantic raise its normal error on real garbage
    return value


class FetchFromCollectionInput(BaseModel):
    """Input model for semantic search against a Qdrant collection."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    collection_name: str = Field(
        ...,
        description=(
            "Exact name of the Qdrant collection to search, e.g. 'metro_faq'. "
            "Call qdrant_get_collections if unsure of available collection names."
        ),
        min_length=1,
        max_length=200,
    )
    query_text: str = Field(
        ...,
        description=(
            "Natural language query to semantically search for, e.g. "
            "'how much luggage can I carry on the metro'. This will be "
            "embedded and compared against stored chunks — phrase it like "
            "a real question, not as keywords."
        ),
        min_length=1,
        max_length=2000,
    )
    top_k: Annotated[int, BeforeValidator(_coerce_numeric_string)] = Field(
        default=5,
        description="Maximum number of matching chunks to return.",
        ge=1,
        le=20,
    )
    score_threshold: Annotated[
        float | None, BeforeValidator(_coerce_numeric_string)
    ] = Field(
        default=None,
        description=(
            "Optional minimum similarity score (0.0-1.0, cosine similarity) "
            "below which results are discarded. Leave unset to just take "
            "the top_k best matches regardless of absolute score."
        ),
        ge=0.0,
        le=1.0,
    )


@mcp.tool(
    name="qdrant_fetch_from_collection",
    annotations={
        "title": "Semantic Search a Qdrant Collection",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def qdrant_fetch_from_collection(params: FetchFromCollectionInput) -> str:
    """Run a semantic similarity search against a Qdrant collection and
    return the most relevant text chunks.

    This is the main retrieval tool: embed the user's question, search the
    given collection, and return ranked chunks with their source metadata
    so the calling agent can ground its answer in the retrieved text.

    Args:
        params (FetchFromCollectionInput): Contains:
            - collection_name (str): Which collection to search.
            - query_text (str): The natural-language query to embed and search.
            - top_k (int): Max number of chunks to return (default 5).
            - score_threshold (Optional[float]): Minimum similarity score.

    Returns:
        str: JSON object of the form:
            {
              "collection_name": "metro_faq",
              "query_text": "...",
              "count": 3,
              "results": [
                {
                  "text": "...",
                  "score": 0.87,
                  "source_file": "faq_general.md",
                  "chunk_index": 4
                },
                ...
              ]
            }
        Or, if the collection doesn't exist:
            {"error": "Collection 'x' not found.", "available_collections": [...]}
        Or, if Qdrant is unreachable or the search itself fails:
            {"error": "<details>"}
    """
    try:
        available = list_collections_safe()
    except QdrantUnreachableError as exc:
        return _unreachable_response(exc)

    if params.collection_name not in available:
        return _collection_not_found_response(params.collection_name, available)

    try:
        results = search_collection(
            collection_name=params.collection_name,
            query_text=params.query_text,
            top_k=params.top_k,
            score_threshold=params.score_threshold,
        )
    except Exception as exc:
        return json.dumps(
            {"error": f"Search failed: {type(exc).__name__}: {exc}"}, indent=2
        )

    return json.dumps(
        {
            "collection_name": params.collection_name,
            "query_text": params.query_text,
            "count": len(results),
            "results": results,
        },
        indent=2,
    )


if __name__ == "__main__":
    # stdio transport — CrewAI spawns this exact command as a subprocess
    # and talks to it over stdin/stdout.
    mcp.run()