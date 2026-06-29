"""
fare_finder_mcp — a custom MCP server wrapping Postgres station fare
lookups.

Why this exists (rather than the agent querying Postgres directly):
  - Keeps the "tool layer" uniform with every other specialist agent —
    calls go through MCP tools, never raw DB clients, matching the
    project's deterministic-tool design.
  - The agent never computes or guesses a fare; it only calls this
    tool and phrases the number it gets back. Fare data is the kind of
    fact where hallucination is a genuine problem, not an annoyance.

Tools exposed:
  - get_fare : look up the fare between two named stations

Run standalone for local testing:
    python -m mcp_servers.fare_mcp.server

Run via the MCP Inspector:
    npx @modelcontextprotocol/inspector python -m mcp_servers.fare_mcp.server

CrewAI connects to this over stdio via mcps=[MCPServerStdio(...)] on the
Train Info Agent (see crew/crew.py).
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from mcp.server.fastmcp import FastMCP

from mcp_servers.fare_mcp.postgres_client_wrapper import (
    PostgresUnreachableError,
    get_fare,
    list_all_station_names,
)

mcp = FastMCP("fare_finder_mcp")


def _unreachable_response(exc: PostgresUnreachableError) -> str:
    return json.dumps({"error": str(exc)}, indent=2)


class GetFareInput(BaseModel):
    """Input model for a station-to-station fare lookup."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    from_station: str = Field(
        ...,
        description=(
            "Exact name of the station the journey starts from, e.g. "
            "'Egmore'. Must match an actual Chennai Metro station name — "
            "resolve colloquial names or typos to the canonical station "
            "name before calling this tool."
        ),
        min_length=1,
        max_length=100,
    )
    to_station: str = Field(
        ...,
        description=(
            "Exact name of the destination station, e.g. 'Chennai Central'. "
            "Same naming requirement as from_station."
        ),
        min_length=1,
        max_length=100,
    )


@mcp.tool(
    name="get_fare",
    annotations={
        "title": "Get Metro Fare Between Two Stations",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def get_fare_tool(params: GetFareInput) -> str:
    """Look up the Single Journey Token fare (in rupees) between two
    Chennai Metro stations.

    Use this whenever the user asks how much a ticket/journey costs
    between two stations. This is a direct table lookup — it does NOT
    calculate distance or guess a tier; it returns the official fare
    exactly as published by CMRL.

    Args:
        params (GetFareInput): Contains:
            - from_station (str): exact starting station name.
            - to_station (str): exact destination station name.

    Returns:
        str: JSON object of the form:
            {"from_station": "Egmore", "to_station": "Chennai Central", "fare": 10}
        Or, if either station name doesn't match any known station:
            {"error": "...", "available_stations": [...]}
        Or, if Postgres is unreachable:
            {"error": "Could not reach Postgres: <details>"}
    """
    try:
        known_stations = list_all_station_names()
    except PostgresUnreachableError as exc:
        return _unreachable_response(exc)

    unknown = [
        s for s in (params.from_station, params.to_station) if s not in known_stations
    ]
    if unknown:
        return json.dumps(
            {
                "error": (
                    f"Station name(s) not recognized: {unknown}. "
                    f"Station names must match exactly."
                ),
                "available_stations": known_stations,
            },
            indent=2,
        )

    fare = get_fare(params.from_station, params.to_station)

    if fare is None:
        # Shouldn't normally happen if both names are in known_stations
        # (the table is a complete 41x41 matrix), but handle it rather
        # than let a None silently become a malformed response.
        return json.dumps(
            {
                "error": (
                    f"No fare entry found for '{params.from_station}' -> "
                    f"'{params.to_station}', even though both station names "
                    f"are recognized. This indicates a data gap in the fare table."
                )
            },
            indent=2,
        )

    return json.dumps(
        {
            "from_station": params.from_station,
            "to_station": params.to_station,
            "fare": fare,
            "currency": "INR",
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
