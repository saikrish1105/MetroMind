"""
Chennai Metro — Fare Table Prerun Script
===========================================
Run this once (or anytime you want to rebuild from scratch) to create
the `station_fares` table in Postgres and load the full station-to-
station fare matrix.

    python -m prerun.build_fare_table

Requirements:
    pip install psycopg2-binary

-----------------------------------------------------------------------
DATA SOURCE
-----------------------------------------------------------------------
Fare values were transcribed from the official CMRL Single Journey
Token fare table PDF (41x41 station matrix), verified two ways before
use: (1) the PDF's embedded text layer, and (2) visual inspection of
the rasterized page at 200 DPI to confirm row/column header order and
spot-check several values. See fare_extraction/parse_fare_matrix.py
(run standalone) for the full spot-check suite.

Station names used here are the GRAPH CANONICAL NAMES — identical to
the `name` property used in prerun/build_metro_graph.py — so any future
join between fare data and graph route data works without a name-
mapping step in the application layer.

-----------------------------------------------------------------------
TABLE SHAPE
-----------------------------------------------------------------------
One row per (from_station, to_station) pair, fare in rupees. The
matrix is symmetric (fare A->B always equals fare B->A on this
network), but rather than rely on the consuming code to know that and
swap arguments, every directed pair is stored explicitly. This keeps
the fare_finder MCP tool's query trivial: one exact-match lookup, no
"try both directions" logic, no risk of a silent bug if the network
ever becomes directionally asymmetric (e.g. future lines with one-way
sections) — that day will be a Phase 2 modeling concern.
"""

from __future__ import annotations

import psycopg2

from common.settings import POSTGRES_DSN
from prerun.fare_matrix_data import build_fare_records


def get_connection():
    return psycopg2.connect(POSTGRES_DSN)


def create_table(conn):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS station_fares;")
        cur.execute(
            """
            CREATE TABLE station_fares (
                id SERIAL PRIMARY KEY,
                from_station TEXT NOT NULL,
                to_station TEXT NOT NULL,
                fare INTEGER NOT NULL,
                UNIQUE (from_station, to_station)
            );
            """
        )
        # Index speeds up the fare_finder MCP tool's lookup pattern:
        # WHERE from_station = %s AND to_station = %s
        cur.execute(
            "CREATE INDEX idx_station_fares_lookup ON station_fares (from_station, to_station);"
        )
    conn.commit()
    print("Created station_fares table (and lookup index).")


def load_records(conn, records: list[dict]):
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO station_fares (from_station, to_station, fare)
            VALUES (%(from_station)s, %(to_station)s, %(fare)s)
            ON CONFLICT (from_station, to_station) DO UPDATE
                SET fare = EXCLUDED.fare;
            """,
            records,
        )
    conn.commit()
    print(f"Loaded {len(records)} fare records.")


def run_sanity_checks(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM station_fares;")
        total = cur.fetchone()[0]
        print(f"\nSanity check: total fare rows = {total} (expected 1681 = 41*41)")

        cur.execute("SELECT COUNT(DISTINCT from_station) FROM station_fares;")
        distinct_stations = cur.fetchone()[0]
        print(f"Distinct stations = {distinct_stations} (expected 41)")

        cur.execute(
            "SELECT fare FROM station_fares WHERE from_station = %s AND to_station = %s;",
            ("Egmore", "Chennai Central"),
        )
        sample = cur.fetchone()
        print(f"Spot check: Egmore -> Chennai Central fare = {sample[0] if sample else 'NOT FOUND'} (expected 10)")

        cur.execute(
            "SELECT fare FROM station_fares WHERE from_station = %s AND to_station = %s;",
            ("Chennai Airport", "Wimco Nagar Depot"),
        )
        sample = cur.fetchone()
        print(f"Spot check: Chennai Airport -> Wimco Nagar Depot fare = {sample[0] if sample else 'NOT FOUND'} (expected 50)")


def main():
    conn = get_connection()
    try:
        create_table(conn)
        records = build_fare_records()
        load_records(conn, records)
        run_sanity_checks(conn)
        print("\nFare table build complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
