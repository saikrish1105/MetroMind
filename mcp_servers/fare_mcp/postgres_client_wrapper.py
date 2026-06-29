"""
Thin wrapper around psycopg2 for fare lookups against the
`station_fares` table (see prerun/build_fare_table.py for schema +
data loading).

Same separation-of-concerns pattern as
mcp_servers/qdrant_mcp/qdrant_client_wrapper.py:
  - this module: "how do I talk to Postgres correctly"
  - server.py: MCP protocol concerns (tool schemas, error formatting)
"""

from __future__ import annotations

import psycopg2

from common.settings import POSTGRES_DSN

_connection = None


class PostgresUnreachableError(RuntimeError):
    """Raised when Postgres cannot be reached, with a message safe to show an LLM."""


def get_connection():
    """Lazily create and cache a single connection for the server's
    lifetime. Avoids reconnecting on every tool call."""
    global _connection
    if _connection is None or _connection.closed:
        try:
            _connection = psycopg2.connect(POSTGRES_DSN)
        except Exception as exc:
            raise PostgresUnreachableError(
                f"Could not reach Postgres: {type(exc).__name__}: {exc}"
            ) from exc
    return _connection


def get_fare(from_station: str, to_station: str) -> int | None:
    """
    Look up the fare (in rupees) between two stations.

    Args:
        from_station: exact canonical station name (e.g. "Egmore")
        to_station: exact canonical station name (e.g. "Chennai Central")

    Returns:
        int fare in rupees, or None if no matching row exists (e.g. one
        or both station names don't match anything in the table).
    """
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fare FROM station_fares WHERE from_station = %s AND to_station = %s;",
            (from_station, to_station),
        )
        row = cur.fetchone()
        return row[0] if row else None


def list_all_station_names() -> list[str]:
    """
    Return every distinct station name known to the fare table.

    Useful for the MCP tool to validate a station name before claiming
    "no fare found" — distinguishes "this station doesn't exist" from
    "this exact pair has no fare row for some other reason."
    """
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT from_station FROM station_fares ORDER BY from_station;")
        return [row[0] for row in cur.fetchall()]
