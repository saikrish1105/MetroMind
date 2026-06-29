## STEPS TO FOLLOW TO SET UP THE PROJECT

### Set up OLLAMA 
1. Download ollama using this command on terminal : winget install Ollama.Ollama
2. Check it : ollama --version
3. download the models in .env.example using : ollama pull llama3.1:8b, ollama pull nomic-embed-text
4. See the list of model u have using : ollama list
5. See the list of current running models using : ollama ps

### Set up Docker
1. Download docker desktop. Start it before running any task by opening app.
2. Download qdrant by doing, docker pull qdrant/qdrant
3. run the docker image using : docker run -p 6333:6333 --name qdrant1 qdrant/qdrant
4. Stop the docker container using : docker stop qdrant1
5. restart it using (dont use docker run again) : docker start qdrant1
6. see that container is runnign by : docker ps


### Project code set up
1. git clone <name-of-repo>
2. py -3.11 -m venv myvenv 
3. myvenv\Scripts\activate.bat
4. pip install -r requirements.txt

# Chennai Metro Assistant

## Flow

```
User query
   │
   ▼
Supervisor Agent (routes by intent)
   │
   ▼
Info Agent  ──calls──▶  qdrant_retrieval_mcp (MCP tool)  ──▶  Qdrant
   │                                                            ▲
   ▼                                                            │
Answer (grounded in retrieved chunks)              prerun/ingest_info_docs.py
                                                     (chunks + embeds your docs
                                                      via Ollama, pushes to Qdrant)
```

- **prerun** (run once / whenever docs change): reads files in `data/raw_documents/`, chunks them, embeds chunks with Ollama, stores them in Qdrant.
- **MCP server**: a standalone tool server the Info Agent calls to search Qdrant. Doesn't know about CrewAI at all.
- **Crew**: Supervisor routes the query, Info Agent retrieves + answers.

## Where things are

| Folder | What's in it |
|---|---|
| `data/raw_documents/` | drop your FAQ `.txt`/`.md`/`.pdf` files here |
| `prerun/` | `ingest_info_docs.py` — the chunk+embed+upload script |
| `mcp_servers/qdrant_mcp/` | the custom MCP server (`server.py`) + Qdrant wrapper |
| `crew/config/` | `agents.yaml` / `tasks.yaml` — all prompt text (role/goal/backstory/task wording) |
| `crew/crew.py` | wiring — which LLM, which MCP tools, agent/task assembly |
| `crew/main.py` | entrypoint — `python -m crew.main "question"` |
| `common/` | shared config (`settings.py`) and embedding helper, used by both prerun and the MCP server |

## Setup

### 1. Docker — Qdrant
```bash
docker run -p 6333:6333 qdrant/qdrant
```

### 2. Ollama — local LLM + embeddings
Install from [ollama.com](https://ollama.com), then:
```bash
ollama pull llama3.1:8b        # chat model
ollama pull nomic-embed-text   # embedding model
ollama serve                   # if not already running
```

### 3. Python venv
```bash
cd chennai-metro-assistant
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Run
```bash
# one-time (or whenever you add/change files in data/raw_documents/):
python -m prerun.ingest_info_docs

# ask a question:
python -m crew.main "How much luggage can I carry on the metro?"
```

Run everything from the project root (not from inside `crew/` or `mcp_servers/`) — imports are absolute.