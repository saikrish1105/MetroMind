"""
Chennai Metro — Graph Prerun Script
=====================================
Run this once (or anytime you want to rebuild from scratch) to create
the full Neo4j graph for the Chennai Metro Blue + Green Line network.

This is plain Python using the official `neo4j` driver. No .cypher file,
no shell commands, no Docker exec needed — just run:

    python build_metro_graph.py

Requirements:
    pip install neo4j

-----------------------------------------------------------------------
DATA SOURCE
-----------------------------------------------------------------------
Station names, order, and interchange points were verified directly
against the official CMRL line map (visually inspected station-by-
station), not scraped from secondary sources. Two lines, current
network only — Phase 2 stations are NOT included here.

-----------------------------------------------------------------------
GRAPH MODEL
-----------------------------------------------------------------------
Each node = one (station, line) pair, e.g. Chennai Central has TWO
nodes: one tagged line="Blue", one tagged line="Green". This is what
lets a routing query later detect line changes for free — when a path
crosses from one line's node to the other line's node for the same
station, that crossing IS the transfer point. No separate "is this a
transfer" logic needed downstream.

Two relationship types:
  NEXT_STOP  — adjacent stations on the SAME line (a ride)
  TRANSFER   — same station, different line (a platform change)

-----------------------------------------------------------------------
WHAT EACH NODE STORES (and why)
-----------------------------------------------------------------------
Every Station node carries enough metadata that later agents/tools can
answer real user questions WITHOUT going back to redesign the schema:

  name            - canonical station name (exact string match target)
  line            - "Blue" or "Green"
  sequence        - position along the line, start to end (1, 2, 3...)
  is_interchange  - True if this station connects to another line
                    (lets a query answer "which stations can I change
                    lines at?" without walking relationships)
  is_terminal     - True if this is a line's first or last station
                    (useful for "what's the last train to X" type
                    questions, and for sanity-checking the graph)
  zone            - placeholder grouping for future use (e.g. North/
                    Central/South Chennai) — left as None for now,
                    fill in later if station-services or geo-based
                    queries need it. Cheap to add now, annoying to
                    backfill later.

NOTE ON FARE: fare is intentionally NOT stored on these nodes. Fare in
the real system is a distance/tier lookup against your existing fare
table, not a graph property — so the Fare tool will take this graph's
route output (station sequence) and use it as an INPUT to that table,
rather than the graph storing fare data itself.

NOTE ON TIMING: same logic — actual train timings live in your
timetable database (populated via Docling from the CMRL PDF). The
graph only stores topology (which stations connect to which), not
schedules.
"""

from neo4j import GraphDatabase


# =======================================================================
# CONNECTION CONFIG — edit these to match your running Docker container
# =======================================================================
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password123"   # whatever you set when starting the container


# =======================================================================
# STATION DATA — verified against the official CMRL map
# =======================================================================

# Blue Line: Wimco Nagar Depot <-> Chennai Airport, 26 stations, in order.
BLUE_LINE_STATIONS = [
    "Wimco Nagar Depot", "Wimco Nagar", "Thiruvottriyur", "Thiruvottriyur Theradi",
    "Kaladipet", "Tollgate", "New Washermanpet", "Tondiarpet",
    "Sir Theagaraya College", "Washermanpet", "Mannadi", "High Court",
    "Chennai Central", "Government Estate", "LIC", "Thousand Lights",
    "AG-DMS", "Teynampet", "Nandanam", "Saidapet", "Little Mount", "Guindy",
    "Arignar Anna Alandur", "Nanganallur Road", "Meenambakkam", "Chennai Airport",
]

# Green Line: Chennai Central <-> St. Thomas Mount, 17 stations, in order.
GREEN_LINE_STATIONS = [
    "Chennai Central", "Egmore", "Nehru Park", "Kilpauk", "Pachaiyappa's College",
    "Shenoy Nagar", "Anna Nagar East", "Anna Nagar Tower", "Thirumangalam",
    "Koyambedu", "CMBT", "Arumbakkam", "Vadapalani", "Ashok Nagar",
    "Ekkattuthangal", "Arignar Anna Alandur", "St. Thomas Mount",
]

# Stations confirmed as true in-station interchanges on the official map
# (same square interchange marker serves both lines at these names).
INTERCHANGE_STATIONS = {"Chennai Central", "Arignar Anna Alandur"}

# Placeholder ride time (minutes) between any two adjacent stations.
# Replace with real per-segment run times once pulled from the CMRL
# timetable PDF — right now every hop costs the same, so "shortest path"
# really means "fewest stops," not true travel time.
DEFAULT_RIDE_WEIGHT = 2.0

# Penalty (minutes) for walking across a platform to change lines at
# an in-station interchange.
DEFAULT_TRANSFER_WEIGHT = 3.0


def build_station_records(station_names, line_name):
    """
    Turn an ordered list of station names into full node records with
    all the metadata described in the module docstring.
    """
    records = []
    last_index = len(station_names) - 1
    for i, name in enumerate(station_names):
        records.append({
            "name": name,
            "line": line_name,
            "sequence": i + 1,
            "is_interchange": name in INTERCHANGE_STATIONS,
            "is_terminal": (i == 0 or i == last_index),
            "zone": None,  # reserved for future use, intentionally unfilled
        })
    return records


def wipe_existing_graph(session):
    session.run("MATCH (n:Station) DETACH DELETE n")
    print("Cleared existing Station nodes (if any).")


def create_constraint(session):
    session.run(
        """
        CREATE CONSTRAINT route_node_unique IF NOT EXISTS
        FOR (s:Station) REQUIRE (s.name, s.line) IS UNIQUE
        """
    )
    print("Ensured uniqueness constraint on (name, line).")


def create_station_nodes(session, records):
    session.run(
        """
        UNWIND $records AS rec
        CREATE (:Station {
            name: rec.name,
            line: rec.line,
            sequence: rec.sequence,
            is_interchange: rec.is_interchange,
            is_terminal: rec.is_terminal,
            zone: rec.zone
        })
        """,
        records=records,
    )
    print(f"Created {len(records)} station nodes for this line.")


def create_ride_edges(session, line_name, weight):
    session.run(
        """
        MATCH (a:Station {line: $line}), (b:Station {line: $line})
        WHERE b.sequence = a.sequence + 1
        CREATE (a)-[:NEXT_STOP {weight: $weight}]->(b),
               (b)-[:NEXT_STOP {weight: $weight}]->(a)
        """,
        line=line_name,
        weight=weight,
    )
    print(f"Created NEXT_STOP edges for {line_name} Line.")


def create_transfer_edges(session, station_name, weight):
    session.run(
        """
        MATCH (a:Station {name: $name, line: "Blue"}),
              (b:Station {name: $name, line: "Green"})
        CREATE (a)-[:TRANSFER {weight: $weight}]->(b),
               (b)-[:TRANSFER {weight: $weight}]->(a)
        """,
        name=station_name,
        weight=weight,
    )
    print(f"Created TRANSFER edge at {station_name} (Blue <-> Green).")


def run_sanity_checks(session):
    result = session.run("MATCH (s:Station) RETURN count(s) AS total")
    total = result.single()["total"]
    print(f"\nSanity check: total station nodes = {total} (expected 43)")

    result = session.run(
        "MATCH (s:Station {is_interchange: true}) RETURN s.name AS name, s.line AS line"
    )
    print("Interchange nodes:")
    for record in result:
        print(f"  - {record['name']} ({record['line']})")

    result = session.run(
        "MATCH ()-[t:TRANSFER]->() RETURN count(t) AS total"
    )
    transfer_count = result.single()["total"]
    print(f"Total TRANSFER edges (directed): {transfer_count} (expected 4 -> 2 interchanges x 2 directions)")


def main():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session() as session:
            wipe_existing_graph(session)
            create_constraint(session)

            blue_records = build_station_records(BLUE_LINE_STATIONS, "Blue")
            green_records = build_station_records(GREEN_LINE_STATIONS, "Green")

            create_station_nodes(session, blue_records)
            create_station_nodes(session, green_records)

            create_ride_edges(session, "Blue", DEFAULT_RIDE_WEIGHT)
            create_ride_edges(session, "Green", DEFAULT_RIDE_WEIGHT)

            for station_name in INTERCHANGE_STATIONS:
                create_transfer_edges(session, station_name, DEFAULT_TRANSFER_WEIGHT)

            run_sanity_checks(session)

        print("\nGraph build complete.")

    finally:
        driver.close()


if __name__ == "__main__":
    main()