# Chennai Metro Assistant

A multi-agent, mostly-offline AI assistant for the Chennai Metro (CMRL),
built on CrewAI. A Supervisor agent classifies each user query and routes
it to exactly one specialist:

```
                         User query
                             │
                             ▼
                  Supervisor Agent (classifies intent)
                             │
              ┌──────────────┼──────────────────┐
              ▼              ▼                  ▼
       General Info Agent  Train Info Agent   (not built yet)
       "info" queries      "train" queries    support / travel_history
              │              │                  │
              ▼              ▼                  ▼
        qdrant_mcp      graph_mcp + fare_mcp   honest "not available"
        (Qdrant FAQ     (Neo4j routing +        message naming which
         search)         Postgres fares)        agent would handle it
```

Routing is a real **CrewAI Flow** (`crew/flow.py`), not a keyword if/else:
the Supervisor LLM call decides the category, and the Flow's `@router`
guarantees only that one branch actually executes. (An earlier version of
this project used CrewAI's `Process.hierarchical`, which turned out to run
*every* task regardless of what the manager decided — see the comment at
the top of `crew/flow.py` if you're curious why that didn't work.)

Three specialist tasks are implemented today:

| Category | Agent | Tools | Status |
|---|---|---|---|
| `info` | General Info Agent | `qdrant_mcp` | ✅ implemented |
| `train` | Train Info Agent | `graph_mcp`, `fare_mcp` | ✅ implemented |
| `support`, `travel_history` | — | — | 🚧 not built yet — Supervisor explains this honestly instead of guessing |

---

## Architecture: what runs where

This matters for understanding the Docker setup below.

- **Ollama** (chat LLM + embedding model) — runs **natively on your host**,
  not in Docker. This avoids GPU-passthrough friction and lets Ollama use
  your hardware directly.
- **Qdrant, Neo4j, Postgres** — run **in Docker** via `docker-compose.yml`
  in this repo. These are the only three containers.
- **The three MCP servers** (`qdrant_mcp`, `graph_mcp`, `fare_mcp`) are
  **not separate services at all** — CrewAI spawns each one itself as a
  short-lived stdio subprocess exactly when an agent needs it (see the
  `MCPServerStdio(...)` calls in `crew/crew.py`). They live and die with
  the Python process running `crew.main`.
- **The crew/Flow itself** — runs as a plain Python process in your venv
  (`python -m crew.main "..."`), not containerized.

So: 1 Python venv + 1 native Ollama install + 3 Docker containers.

---

## Where things are

| Path | What's in it |
|---|---|
| `crew/flow.py` | The CrewAI Flow — routing logic, the actual "brain" of dispatch |
| `crew/crew.py` | Agent/task/crew wiring — which LLM, which MCP tools, per specialist |
| `crew/config/agents.yaml` | Agent role/goal/backstory prompts (text only) |
| `crew/config/tasks.yaml` | Task description/expected_output prompts (text only) |
| `crew/main.py` | Entrypoint — `python -m crew.main "your question"` |
| `crew/llm_config.py` | Shared Ollama LLM config used by every agent |
| `common/settings.py` | Every host/port/credential/model-name default, env-overridable |
| `common/embeddings.py` | Thin wrapper around Ollama's `/api/embeddings` endpoint |
| `mcp_servers/qdrant_mcp/` | MCP server: semantic search over the FAQ knowledge base |
| `mcp_servers/graph_mcp/` | MCP server: shortest-path routing over the metro graph (Neo4j) |
| `mcp_servers/fare_mcp/` | MCP server: station-to-station fare lookup (Postgres) |
| `prerun/build_metro_graph.py` | One-time script: builds the Neo4j station/line graph |
| `prerun/build_fare_table.py` | One-time script: builds the Postgres fare table |
| `prerun/ingest_info_docs.py` | Re-run-on-demand script: chunks+embeds FAQ docs into Qdrant |
| `docker-compose.yml` | Brings up Qdrant + Neo4j + Postgres (see above) |
| `.env.example` | Every overridable setting, with the defaults already baked into the code |

---

## 1. Prerequisites

- **Python 3.11+**
- **Docker Desktop** (or Docker Engine + Compose on Linux)
- **Ollama**, installed natively — [ollama.com](https://ollama.com)
  - Windows: `winget install Ollama.Ollama`
  - macOS/Linux: see ollama.com for the install script
- **Git** (to clone the repo, if you haven't already)

---

## 2. Set up Ollama

```bash
ollama --version          # confirm it's installed
ollama serve               # if it's not already running as a background service
```

Pull the two models this project needs. Check `.env.example` (or your own
`.env` if you've customized `OLLAMA_CHAT_MODEL`) for the exact chat model
name — at minimum you need:

```bash
ollama pull nomic-embed-text     # embedding model (fixed — used by Qdrant ingestion + retrieval)
ollama pull <your chat model>    # e.g. ollama pull qwen3.5:9b — see .env.example
```

Verify:
```bash
ollama list      # confirms both models are pulled
ollama ps         # confirms Ollama is actually serving
```

---

## 3. Set up the three databases (Docker)

From the project root (where `docker-compose.yml` lives):

```bash
docker compose up -d
docker compose ps        # all three should show "running" (Postgres/Neo4j also "healthy")
```

This starts:
- **Qdrant** → `localhost:6333` (REST), `localhost:6334` (gRPC)
- **Neo4j** → `localhost:7474` (Browser UI), `localhost:7687` (Bolt)
- **Postgres** → `localhost:5432`

All credentials in `docker-compose.yml` already match the defaults in
`common/settings.py` — **no `.env` file is required** to get a working
local setup. If you do create a `.env` to override any host/port/password,
make sure the matching value in `docker-compose.yml` is updated too (Python
reads `.env`; Docker reads the compose file — they don't share state).

Useful commands while developing:
```bash
docker compose logs -f              # tail logs from all three
docker compose down                 # stop containers, KEEP data
docker compose down -v              # stop containers, WIPE all data (fresh start)
```

You can open Neo4j's browser UI at **http://localhost:7474** (login:
`neo4j` / `password123`) to visually inspect the station graph once it's
built in the next step.

---

## 4. Python environment

```bash
git clone <this-repo-url>
cd chennai-metro-assistant

python3 -m venv myvenv
# Windows:
myvenv\Scripts\activate.bat
# macOS/Linux:
source myvenv/bin/activate

pip install -r requirements.txt
```

Run every command below from the **project root** — imports throughout
the codebase are absolute (`from common.settings import ...`, etc.), so
running from inside `crew/` or `mcp_servers/` will break imports.

(Optional) copy `.env.example` to `.env` if you want to override any
default — model name, ports, credentials, paths. Everything has a sane
localhost default already, so this is optional for a first run.

---

## 5. Run the prerun scripts (once each)

These populate the three databases. Run them in any order — they're
independent of each other.

### 5a. Build the metro graph (Neo4j) — powers `train` routing queries

```bash
python prerun/build_metro_graph.py
```

Note: this script is run directly (not as `python -m prerun....`) and has
its own `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` constants hardcoded at
the top of the file — if you changed Neo4j's password in `docker-compose.yml`,
edit those three lines in the script to match, since it does **not** read
`common/settings.py`.

Expected output ends with a sanity check: 43 station nodes, 2 interchange
stations (Chennai Central, Arignar Anna Alandur), 4 directed TRANSFER edges.

### 5b. Build the fare table (Postgres) — powers `train` fare queries

```bash
python -m prerun.build_fare_table
```

Expected output ends with: 1681 fare rows (41×41 stations), a couple of
spot-checked sample fares.

### 5c. Ingest FAQ documents (Qdrant) — powers `info` queries

First, drop your FAQ source documents (`.pdf`, `.txt`, or `.md`) into
`data/raw_documents/` (create the folder if it doesn't exist — the script
will also create it for you if empty, but won't have anything to ingest):

```bash
mkdir -p data/raw_documents
# copy your CMRL FAQ / rules / policy documents into data/raw_documents/
```

Then run:
```bash
python -m prerun.ingest_info_docs
```

Notes:
- **First run needs internet access once** — Docling's PDF pipeline and
  HybridChunker's tokenizer download small model/config files the first
  time they're used. Every run after that (and the actual
  ingest/embed/upload work) is fully offline.
- This script is **idempotent by recreation**: every run drops and
  rebuilds the `metro_faq` Qdrant collection from whatever is currently in
  `data/raw_documents/` — re-run it any time you add or change documents.
- If you don't have real CMRL FAQ documents handy yet, even a single `.md`
  or `.txt` file with a few Q&A pairs is enough to test the `info` flow
  end-to-end — just don't expect comprehensive answers from one test file.

---

## 6. Run the assistant

```bash
python -m crew.main "your question here"
```

### Three test prompts — one for each implemented path

**1. `info` — general FAQ (Qdrant + General Info Agent)**
```bash
python -m crew.main "How much luggage am I allowed to carry on the metro?"
```
Routes to the General Info Agent, which searches whatever you ingested
into `data/raw_documents/` in step 5c. The quality of this answer depends
entirely on what's actually in your FAQ documents — if you haven't loaded
real CMRL FAQ content yet, try a question that matches your test file
instead, or expect an honest "I don't have information on that" if nothing
relevant is retrieved.

**2. `train` — routing + fare (Neo4j + Postgres + Train Info Agent)**
```bash
python -m crew.main "How do I go from Chennai Central to Chennai Airport, and what's the fare?"
```
Routes to the Train Info Agent, which calls both `get_route` (Neo4j —
Blue Line, no transfer needed since Chennai Central is already on the
Blue Line) and `get_fare` (Postgres) since the query asks for both. Try
the route-only or fare-only versions too:
```bash
python -m crew.main "Which line do I take from Egmore to Vadapalani?"
python -m crew.main "What's the fare from Egmore to Chennai Central?"
```
(Egmore → Vadapalani crosses from Green Line through the Arignar Anna
Alandur interchange — good for seeing a transfer described.)

**3. `support` / `travel_history` — not yet implemented (graceful fallback)**
```bash
python -m crew.main "I lost my bag on the train, can you help me file a complaint?"
```
Routes to the `unsupported` branch — the Supervisor classifies this as
`support`, and the response honestly states that this isn't available
yet and names which specialist agent would eventually handle it, instead
of guessing or hallucinating help it can't actually provide.

---

## Troubleshooting

- **"Could not reach Qdrant" / connection refused on 6333, 7687, or 5432**
  → `docker compose ps` to confirm all three containers are up; `docker
  compose logs <service>` to see why one might have failed to start.
- **Ollama errors / "model not found"** → `ollama list` to confirm the
  exact model name/tag matches `OLLAMA_CHAT_MODEL` in your `.env` (or the
  default in `common/settings.py`); `ollama pull <model>` if missing.
- **Empty/odd answers from the `info` agent** → confirm step 5c actually
  ingested something: `docker exec -it metro-qdrant sh -c "wget -qO- http://localhost:6333/collections/metro_faq"`
  (or just check the ingestion script's own console output for chunk counts).
- **Station name not recognized in a `train` query** → station names must
  match the canonical names in `prerun/build_metro_graph.py` exactly
  (e.g. "Chennai Airport", not "the airport") — the agent is instructed
  to tell you plainly if a tool reports a name as unrecognized rather than
  guessing a substitute.