"""
Entrypoint for the Chennai Metro Assistant.

Usage:
    python -m crew.main "How much luggage am I allowed to carry?"

Before running this for the first time:
  1. Ollama must be running, with both the chat model and embedding model pulled:
       ollama pull <your chat model, e.g. llama3.1:8b or qwen2.5:7b>
       ollama pull nomic-embed-text
  2. Qdrant must be running (Docker), reachable at the URL in common/settings.py.
  3. Neo4j and Postgres must be running and populated (see prerun/build_metro_graph.py
     and prerun/build_fare_table.py) if you're testing routing/fare queries.
  4. You must have already run the prerun ingestion script at least once:
       python -m prerun.ingest_info_docs
     so that the 'metro_faq' collection exists and has content to retrieve.

A single query now runs through crew/flow.py's ChennaiMetroFlow, which
classifies intent via the Supervisor agent and then runs exactly one
specialist crew — see crew/flow.py's module docstring for why this
replaced the old Process.hierarchical crew.
"""

import sys

from crew.flow import ChennaiMetroFlow


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m crew.main "your question here"')
        sys.exit(1)

    query = " ".join(sys.argv[1:])

    flow = ChennaiMetroFlow()
    flow.kickoff(inputs={"query": query})

    print("\n" + "=" * 60)
    print("FINAL RESULT")
    print("=" * 60)
    print(flow.state.answer)


if __name__ == "__main__":
    main()