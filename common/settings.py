"""
Central configuration for the Chennai Metro Assistant.

Every other module (prerun ingestion, the Qdrant MCP server, and the crew)
imports from here instead of hardcoding hosts/ports/model names. Change a
value once, it's picked up everywhere.

All values can be overridden via environment variables (e.g. in a .env file
loaded by python-dotenv), so the same code works whether you're running
things on bare metal or pointing at containers.
"""

import os

# ---------------------------------------------------------------------------
# Ollama (local LLM + embedding server)
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Small, fast embedding model. nomic-embed-text is a good default for a
# local/offline setup — 768 dims, strong retrieval quality for its size.
# Pull it with: ollama pull nomic-embed-text
OLLAMA_EMBED_MODEL: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "768"))

# Chat/completion model used by CrewAI agents (Supervisor + specialists).
# Swap to any model you've pulled locally, e.g. "llama3.1:8b" or "qwen2.5:7b".
OLLAMA_CHAT_MODEL: str = os.getenv("OLLAMA_CHAT_MODEL", "llama3.1:8b")

# ---------------------------------------------------------------------------
# Qdrant (vector DB, running in Docker)
# ---------------------------------------------------------------------------
QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_URL: str = os.getenv("QDRANT_URL", f"http://{QDRANT_HOST}:{QDRANT_PORT}")

# Default collection used for the Info Agent's FAQ documents. Other agents
# added later can use their own collections (e.g. "support_kb", "policies")
# without touching this file's structure — just add new constants here.
INFO_AGENT_COLLECTION: str = os.getenv("INFO_AGENT_COLLECTION", "metro_faq")

# ---------------------------------------------------------------------------
# Neo4j (graph DB, running in Docker) — used by the Train Info Agent's
# graph_mcp tool for routing queries.
# ---------------------------------------------------------------------------
NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "password123")

# ---------------------------------------------------------------------------
# Postgres (relational DB, running in Docker) — used by the Train Info
# Agent's fare_finder MCP tool for station-to-station fare lookups.
# ---------------------------------------------------------------------------
POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB: str = os.getenv("POSTGRES_DB", "chennai_metro")
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "admin")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "password123")
POSTGRES_DSN: str = os.getenv(
    "POSTGRES_DSN",
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
)

# ---------------------------------------------------------------------------
# Chunking config used at ingestion time (prerun) — kept here too so the
# MCP server / agents can reason about chunk size if ever needed.
# ---------------------------------------------------------------------------
CHUNK_SIZE_TOKENS: int = int(os.getenv("CHUNK_SIZE_TOKENS", "400"))
CHUNK_OVERLAP_TOKENS: int = int(os.getenv("CHUNK_OVERLAP_TOKENS", "60"))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DOCUMENTS_DIR: str = os.getenv(
    "RAW_DOCUMENTS_DIR", os.path.join(PROJECT_ROOT, "data", "raw_documents")
)

