"""
Thin wrapper around the neo4j driver for routing queries against the
Chennai Metro graph (see prerun/build_metro_graph.py for schema).

Same separation-of-concerns pattern as
mcp_servers/qdrant_mcp/qdrant_client_wrapper.py:
  - this module: "how do I query the graph correctly, and how do I turn
    a raw path into the structured facts an LLM needs to phrase a
    real metro-style direction (e.g. 'towards Wimco Nagar Depot')"
  - server.py: MCP protocol concerns (tool schemas, error formatting)

WHY "DIRECTION" NEEDS EXPLICIT HANDLING:
Real Chennai Metro announcements describe a ride by the line's TERMINAL
in the direction of travel ("Blue Line towards Chennai Airport"), not
by the next station name. The graph already stores everything needed
to compute this (`sequence` and `is_terminal` per station), but no
query does it automatically — comparing the rider's boarding sequence
number to their alighting sequence number on each line segment tells
you which of the line's two terminals is "ahead" of them, which is
exactly the terminal name a real announcement would use.
"""

from __future__ import annotations

from neo4j import GraphDatabase

from common.settings import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

_driver = None


class Neo4jUnreachableError(RuntimeError):
    """Raised when Neo4j cannot be reached, with a message safe to show an LLM."""


def get_driver():
    """Lazily create and cache a single driver for the server's lifetime."""
    global _driver
    if _driver is None:
        try:
            _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            _driver.verify_connectivity()
        except Exception as exc:
            _driver = None
            raise Neo4jUnreachableError(
                f"Could not reach Neo4j: {type(exc).__name__}: {exc}"
            ) from exc
    return _driver


# ---------------------------------------------------------------------
# Pure Cypher shortest-path query — no APOC plugin required. For a
# ~43-node graph this is instant; variable-length path search up to a
# generous hop cap, picking the lowest total weight found.
# ---------------------------------------------------------------------
_SHORTEST_PATH_QUERY = """
MATCH (source:Station {name: $source_name})
MATCH (dest:Station {name: $dest_name})
MATCH path = (source)-[:NEXT_STOP|TRANSFER*1..40]-(dest)
WITH path, reduce(total = 0.0, r IN relationships(path) | total + r.weight) AS weight
RETURN path, weight
ORDER BY weight ASC
LIMIT 1
"""

# Used to compute "towards <terminal>" phrasing — given a line name,
# returns both terminal stations (is_terminal = true) with their
# sequence numbers, so we know which terminal sits at which end.
_LINE_TERMINALS_QUERY = """
MATCH (s:Station {line: $line, is_terminal: true})
RETURN s.name AS name, s.sequence AS sequence
ORDER BY s.sequence ASC
"""


def _path_to_raw_steps(path) -> list[dict]:
    """Convert a Neo4j path object into a list of
    {station, line, sequence, action} dicts, action in
    {"board", "ride", "transfer"}."""
    steps = []
    nodes = path.nodes
    rels = path.relationships

    steps.append({
        "station": nodes[0]["name"],
        "line": nodes[0]["line"],
        "sequence": nodes[0]["sequence"],
        "action": "board",
    })

    for i, rel in enumerate(rels):
        next_node = nodes[i + 1]
        steps.append({
            "station": next_node["name"],
            "line": next_node["line"],
            "sequence": next_node["sequence"],
            "action": "transfer" if rel.type == "TRANSFER" else "ride",
        })

    return steps


def _get_line_terminals(driver, line: str) -> list[dict]:
    """Returns the two terminal stations for a line, e.g.
    [{"name": "Wimco Nagar Depot", "sequence": 1},
     {"name": "Chennai Airport", "sequence": 26}]"""
    with driver.session() as session:
        result = session.run(_LINE_TERMINALS_QUERY, line=line)
        return [{"name": r["name"], "sequence": r["sequence"]} for r in result]


def _direction_terminal(driver, line: str, board_sequence: int, alight_sequence: int) -> str:
    """
    Given a line and the sequence numbers a rider boards/alights at,
    return the name of the terminal station in the direction of travel
    — i.e. the terminal a real metro announcement would name.

    Example: boarding Teynampet (seq 18) and alighting at Chennai
    Central (seq 13) on the Blue Line means sequence is DECREASING,
    so direction is "towards" the line's lower-sequence terminal
    (Wimco Nagar Depot, seq 1) — even though the rider gets off before
    reaching it.
    """
    terminals = _get_line_terminals(driver, line)
    if len(terminals) != 2:
        # Defensive: should never happen with a correctly-built graph,
        # but fail loudly with a clear message rather than silently
        # picking a terminal at random.
        raise RuntimeError(
            f"Expected exactly 2 terminal stations for line '{line}', "
            f"found {len(terminals)}: {terminals}"
        )

    low_seq_terminal, high_seq_terminal = sorted(terminals, key=lambda t: t["sequence"])

    if alight_sequence > board_sequence:
        return high_seq_terminal["name"]
    else:
        return low_seq_terminal["name"]


def _segments_from_steps(driver, steps: list[dict]) -> list[dict]:
    """
    Collapse the raw step-by-step path into ride SEGMENTS — one entry
    per continuous stretch on a single line, with the direction
    (towards which terminal) already resolved. This is the structured
    shape handed back to the agent/LLM for phrasing.

    Returns a list of:
        {
          "line": "Blue",
          "board_station": "Teynampet",
          "alight_station": "Chennai Central",
          "direction_towards": "Wimco Nagar Depot",
          "stop_count": 5
        }
    one entry per line segment, in travel order. Transfers are implicit:
    wherever one segment's alight_station differs from the next
    segment's board_station's predecessor, that's a transfer point —
    but since both will share the same physical station name, the
    calling code can just print "get down at <alight_station>, change
    to <next line>" directly from consecutive segments.
    """
    segments = []
    current_line = steps[0]["line"]
    board_step = steps[0]

    for step in steps[1:]:
        if step["action"] == "transfer":
            # Close out the segment that just ended
            alight_step = _previous_ride_step(steps, step)
            segments.append({
                "line": current_line,
                "board_station": board_step["station"],
                "alight_station": alight_step["station"],
                "direction_towards": _direction_terminal(
                    driver, current_line, board_step["sequence"], alight_step["sequence"]
                ),
                "stop_count": abs(alight_step["sequence"] - board_step["sequence"]),
            })
            current_line = step["line"]
            board_step = step

    # Final segment after the loop
    last_step = steps[-1]
    segments.append({
        "line": current_line,
        "board_station": board_step["station"],
        "alight_station": last_step["station"],
        "direction_towards": _direction_terminal(
            driver, current_line, board_step["sequence"], last_step["sequence"]
        ),
        "stop_count": abs(last_step["sequence"] - board_step["sequence"]),
    })

    return segments


def _previous_ride_step(steps: list[dict], transfer_step: dict) -> dict:
    """Given a transfer step, find the step immediately before it in
    the list (the station the rider alighted at right before
    transferring). Steps are processed in order so this is just an
    index lookup."""
    idx = steps.index(transfer_step)
    return steps[idx - 1]


def get_route(source_name: str, dest_name: str) -> dict:
    """
    Main entry point: find the shortest route between two stations and
    return it as structured segments (NOT a pre-phrased sentence — the
    calling agent/LLM phrases the final response from this data).

    Args:
        source_name: exact canonical station name to start from
        dest_name: exact canonical station name to travel to

    Returns:
        dict with:
          - "found": bool
          - "segments": list of segment dicts (see _segments_from_steps)
          - "transfer_count": int (== len(segments) - 1)
          - "total_stops": int
    """
    driver = get_driver()

    with driver.session() as session:
        result = session.run(
            _SHORTEST_PATH_QUERY, source_name=source_name, dest_name=dest_name
        )
        record = result.single()

    if record is None:
        return {
            "found": False,
            "segments": [],
            "transfer_count": 0,
            "total_stops": 0,
        }

    path = record["path"]
    steps = _path_to_raw_steps(path)
    segments = _segments_from_steps(driver, steps)

    return {
        "found": True,
        "segments": segments,
        "transfer_count": len(segments) - 1,
        "total_stops": sum(s["stop_count"] for s in segments),
    }


def list_all_station_names() -> list[str]:
    """Return every distinct station name known to the graph (across
    both lines) — used to validate input before running a route query."""
    driver = get_driver()
    with driver.session() as session:
        result = session.run("MATCH (s:Station) RETURN DISTINCT s.name AS name ORDER BY name")
        return [r["name"] for r in result]
