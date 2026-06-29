"""
Chennai Metro fare matrix — verified source data + parsing logic.

This module is the single source of truth for fare data, consumed by
prerun/build_fare_table.py. It contains three layers, in order:

  1. The raw station labels exactly as printed in the fare matrix PDF
     (FARE_MATRIX_STATIONS_RAW), in matrix order.
  2. A mapping from those raw labels to the GRAPH CANONICAL station
     names used in prerun/build_metro_graph.py (FARE_TO_GRAPH_NAME_MAP)
     — this is what lets fare data and graph route data share station
     names with zero translation needed at query time.
  3. The actual 41x41 fare values (FARE_MATRIX_VALUES), row-major, and
     build_fare_records() which flattens all of the above into insert-
     ready dicts.

VERIFICATION: this data was cross-checked two independent ways before
use — (a) the fare PDF's embedded text layer (real fonts, not a scan),
and (b) visual inspection of the rasterized PDF at 200 DPI, spot-
checking header order and several data cells against the extracted
text. run_checks() below re-verifies known values every time this
module's loader runs, so a future bad edit to the matrix gets caught
immediately rather than silently corrupting fare answers.
"""

from __future__ import annotations

# ---------------------------------------------------------------------
# Station labels exactly as printed in the fare matrix, in matrix order
# (left-to-right column order, which is also the top-to-bottom row order).
# ---------------------------------------------------------------------
FARE_MATRIX_STATIONS_RAW = [
    "Airport",
    "Meenambakkam",
    "Nanganallur Road",
    "Arignar Anna Alandur",
    "Guindy",
    "Little Mount",
    "Saidapet",
    "Nandanam",
    "Teynampet",
    "AG-DMS",
    "Thousand Light",
    "LIC",
    "Government Estate",
    "Puratchi Thalaivar Dr.M.G.Ramachandran Central",  # = Chennai Central
    "High Court",
    "Mannadi",
    "Washermenpet",
    "Thyagaraya College",
    "Tondiarpet",
    "New Washermenpet",
    "Toll Gate",
    "Kaladipet",
    "Thiruvotriyur Theredi",
    "Thiruvotriyur",
    "WimcoNagar",
    "WimcoNagar Depot",
    "Ekkattuthangal",
    "Ashok Nagar",
    "Vadapalani",
    "Arumbakkam",
    "Puratchi Thalaivi Dr.J.Jayalalitha CMBT",  # = CMBT
    "Koyambedu",
    "Thirumangalam",
    "Anna Nagar Tower",
    "Anna Nagar East",
    "Shenoy Nagar",
    "Pachiappas College",
    "Kilpauk",
    "Nehru Park",
    "Egmore",
    "St. Thomas Mount",
]

# Mapping from the fare matrix's exact label text -> graph canonical name
# (must match the `name` property used in prerun/build_metro_graph.py).
FARE_TO_GRAPH_NAME_MAP = {
    "Airport": "Chennai Airport",
    "Meenambakkam": "Meenambakkam",
    "Nanganallur Road": "Nanganallur Road",
    "Arignar Anna Alandur": "Arignar Anna Alandur",
    "Guindy": "Guindy",
    "Little Mount": "Little Mount",
    "Saidapet": "Saidapet",
    "Nandanam": "Nandanam",
    "Teynampet": "Teynampet",
    "AG-DMS": "AG-DMS",
    "Thousand Light": "Thousand Lights",
    "LIC": "LIC",
    "Government Estate": "Government Estate",
    "Puratchi Thalaivar Dr.M.G.Ramachandran Central": "Chennai Central",
    "High Court": "High Court",
    "Mannadi": "Mannadi",
    "Washermenpet": "Washermanpet",
    "Thyagaraya College": "Sir Theagaraya College",
    "Tondiarpet": "Tondiarpet",
    "New Washermenpet": "New Washermanpet",
    "Toll Gate": "Tollgate",
    "Kaladipet": "Kaladipet",
    "Thiruvotriyur Theredi": "Thiruvottriyur Theradi",
    "Thiruvotriyur": "Thiruvottriyur",
    "WimcoNagar": "Wimco Nagar",
    "WimcoNagar Depot": "Wimco Nagar Depot",
    "Ekkattuthangal": "Ekkattuthangal",
    "Ashok Nagar": "Ashok Nagar",
    "Vadapalani": "Vadapalani",
    "Arumbakkam": "Arumbakkam",
    "Puratchi Thalaivi Dr.J.Jayalalitha CMBT": "CMBT",
    "Koyambedu": "Koyambedu",
    "Thirumangalam": "Thirumangalam",
    "Anna Nagar Tower": "Anna Nagar Tower",
    "Anna Nagar East": "Anna Nagar East",
    "Shenoy Nagar": "Shenoy Nagar",
    "Pachiappas College": "Pachaiyappa's College",
    "Kilpauk": "Kilpauk",
    "Nehru Park": "Nehru Park",
    "Egmore": "Egmore",
    "St. Thomas Mount": "St. Thomas Mount",
}

# ---------------------------------------------------------------------
# Fare values (rupees), row-major, exactly matching FARE_MATRIX_STATIONS_RAW
# order in both dimensions. Row i = fares FROM station i TO every station
# in the same column order. Diagonal is always 0 (same station).
# ---------------------------------------------------------------------
FARE_MATRIX_VALUES = [
    [0,10,20,20,30,30,30,30,30,40,40,40,40,40,40,40,50,50,50,50,50,50,50,50,50,50,30,30,30,30,40,40,40,40,40,40,40,50,40,40,30],
    [10,0,20,20,20,30,30,30,30,30,40,40,40,40,40,40,40,50,50,50,50,50,50,50,50,50,20,30,30,30,30,40,40,40,40,40,40,40,40,40,20],
    [20,20,0,10,20,20,20,30,30,30,30,30,40,40,40,40,40,40,40,50,50,50,50,50,50,50,20,20,30,30,30,30,40,40,40,40,40,40,40,40,20],
    [20,20,10,0,10,20,20,30,30,30,30,30,30,40,40,40,40,40,40,40,50,50,50,50,50,50,10,20,30,30,30,30,30,30,40,40,40,40,40,40,10],
    [30,20,20,10,0,10,20,20,20,30,30,30,30,30,40,40,40,40,40,40,40,40,50,50,50,50,20,20,30,30,30,30,40,40,40,40,40,40,40,40,20],
    [30,30,20,20,10,0,10,20,20,20,30,30,30,30,30,40,40,40,40,40,40,40,40,50,50,50,20,30,30,30,30,30,40,40,40,40,40,40,40,30,20],
    [30,30,20,20,20,10,0,10,20,20,30,30,30,30,30,30,40,40,40,40,40,40,40,40,50,50,30,30,30,30,30,40,40,40,40,40,40,40,30,30,30],
    [30,30,30,30,20,20,10,0,10,10,20,20,30,30,30,30,30,40,40,40,40,40,40,40,40,40,30,30,30,40,40,40,40,40,40,40,30,30,30,30,30],
    [30,30,30,30,20,20,20,10,0,10,20,20,20,30,30,30,30,30,40,40,40,40,40,40,40,40,30,30,30,40,40,40,40,40,40,40,30,30,30,30,30],
    [40,30,30,30,30,20,20,10,10,0,10,20,20,30,30,30,30,30,30,40,40,40,40,40,40,40,30,30,40,40,40,40,40,40,40,30,30,30,30,30,30],
    [40,40,30,30,30,30,30,20,20,10,0,10,10,20,30,30,30,30,30,30,40,40,40,40,40,40,30,40,40,40,40,40,40,30,30,30,30,30,30,30,30],
    [40,40,30,30,30,30,30,20,20,20,10,0,10,20,20,30,30,30,30,30,30,30,40,40,40,40,30,40,40,40,40,40,30,30,30,30,30,30,30,20,30],
    [40,40,40,30,30,30,30,30,20,20,10,10,0,10,20,20,30,30,30,30,30,30,40,40,40,40,40,40,40,40,40,40,30,30,30,30,30,30,20,20,40],
    [40,40,40,40,30,30,30,30,30,30,20,20,10,0,10,20,20,30,30,30,30,30,30,30,40,40,40,40,40,40,30,30,30,30,30,30,20,20,20,10,40],
    [40,40,40,40,40,30,30,30,30,30,30,20,20,10,0,10,20,20,20,30,30,30,30,30,30,30,40,40,40,40,40,40,30,30,30,30,30,20,20,20,40],
    [40,40,40,40,40,40,30,30,30,30,30,30,20,20,10,0,10,20,20,20,30,30,30,30,30,30,40,40,40,40,40,40,30,30,30,30,30,30,20,20,40],
    [50,40,40,40,40,40,40,30,30,30,30,30,30,20,20,10,0,10,10,20,20,20,30,30,30,30,40,40,40,40,40,40,40,40,30,30,30,30,30,30,40],
    [50,50,40,40,40,40,40,40,30,30,30,30,30,30,20,20,10,0,10,10,20,20,20,30,30,30,40,50,40,40,40,40,40,40,30,30,30,30,30,30,40],
    [50,50,40,40,40,40,40,40,40,30,30,30,30,30,20,20,10,10,0,10,20,20,20,30,30,30,40,50,40,40,40,40,40,40,40,30,30,30,30,30,40],
    [50,50,50,40,40,40,40,40,40,40,30,30,30,30,30,20,20,10,10,0,10,10,20,20,30,30,50,50,50,40,40,40,40,40,40,40,30,30,30,30,50],
    [50,50,50,50,40,40,40,40,40,40,40,30,30,30,30,30,20,20,20,10,0,10,10,20,20,20,50,50,50,50,40,40,40,40,40,40,40,30,30,30,50],
    [50,50,50,50,40,40,40,40,40,40,40,30,30,30,30,30,20,20,20,10,10,0,10,20,20,20,50,50,50,50,50,40,40,40,40,40,40,40,30,30,50],
    [50,50,50,50,50,40,40,40,40,40,40,40,40,30,30,30,30,20,20,20,10,10,0,10,20,20,50,50,50,50,50,40,40,40,40,40,40,40,40,30,50],
    [50,50,50,50,50,50,40,40,40,40,40,40,40,30,30,30,30,30,30,20,20,20,10,0,10,10,50,50,50,50,50,50,40,40,40,40,40,40,40,40,50],
    [50,50,50,50,50,50,50,40,40,40,40,40,40,40,30,30,30,30,30,30,20,20,20,10,0,10,50,50,50,50,50,50,50,40,40,40,40,40,40,40,50],
    [50,50,50,50,50,50,50,40,40,40,40,40,40,40,30,30,30,30,30,30,20,20,20,10,10,0,50,50,50,50,50,50,50,50,40,40,40,40,40,40,50],
    [30,20,20,10,20,20,30,30,30,30,30,30,40,40,40,40,40,40,40,50,50,50,50,50,50,50,0,20,20,30,30,30,30,30,30,40,40,40,40,40,20],
    [30,30,20,20,20,30,30,30,30,30,40,40,40,40,40,40,40,50,50,50,50,50,50,50,50,50,20,0,10,20,20,30,30,30,30,30,30,40,40,40,20],
    [30,30,30,30,30,30,30,30,30,40,40,40,40,40,40,40,40,40,40,50,50,50,50,50,50,50,20,10,0,10,20,20,30,30,30,30,30,30,30,40,30],
    [30,30,30,30,30,30,30,40,40,40,40,40,40,40,40,40,40,40,40,40,50,50,50,50,50,50,30,20,10,0,10,20,20,30,30,30,30,30,30,30,30],
    [40,30,30,30,30,30,30,40,40,40,40,40,40,30,40,40,40,40,40,40,40,50,50,50,50,50,30,20,20,10,0,10,20,20,20,30,30,30,30,30,30],
    [40,40,30,30,30,30,40,40,40,40,40,40,40,30,40,40,40,40,40,40,40,40,40,50,50,50,30,30,20,20,10,0,10,20,20,20,30,30,30,30,30],
    [40,40,40,30,40,40,40,40,40,40,40,30,30,30,30,30,40,40,40,40,40,40,40,40,50,50,30,30,30,20,20,10,0,10,10,20,20,30,30,30,40],
    [40,40,40,30,40,40,40,40,40,40,30,30,30,30,30,30,40,40,40,40,40,40,40,40,40,50,30,30,30,30,20,20,10,0,10,20,20,20,30,30,40],
    [40,40,40,40,40,40,40,40,40,40,30,30,30,30,30,30,30,30,40,40,40,40,40,40,40,40,30,30,30,30,20,20,10,10,0,10,20,20,20,30,40],
    [40,40,40,40,40,40,40,40,40,30,30,30,30,30,30,30,30,30,30,40,40,40,40,40,40,40,40,30,30,30,30,20,20,20,10,0,10,20,20,20,40],
    [40,40,40,40,40,40,40,30,30,30,30,30,30,20,30,30,30,30,30,30,40,40,40,40,40,40,40,30,30,30,30,30,20,20,20,10,0,10,10,20,40],
    [50,40,40,40,40,40,40,30,30,30,30,30,30,20,20,30,30,30,30,30,30,40,40,40,40,40,40,40,30,30,30,30,30,20,20,20,10,0,10,10,40],
    [40,40,40,40,40,40,30,30,30,30,30,30,20,20,20,20,30,30,30,30,30,30,40,40,40,40,40,40,30,30,30,30,30,30,20,20,10,10,0,10,40],
    [40,40,40,40,40,30,30,30,30,30,30,20,20,10,20,20,30,30,30,30,30,30,30,40,40,40,40,40,40,30,30,30,30,30,30,20,20,10,10,0,40],
    [30,20,20,10,20,20,30,30,30,30,30,30,40,40,40,40,40,40,40,50,50,50,50,50,50,50,20,20,30,30,30,30,40,40,40,40,40,40,40,40,0],
]


def build_fare_records() -> list[dict]:
    """
    Flatten the matrix into insert-ready records using GRAPH canonical
    station names (not the raw matrix labels).

    Returns:
        list of {"from_station": str, "to_station": str, "fare": int}
    """
    assert len(FARE_MATRIX_VALUES) == 41, \
        f"Expected 41 rows, got {len(FARE_MATRIX_VALUES)}"
    for i, row in enumerate(FARE_MATRIX_VALUES):
        assert len(row) == 41, \
            f"Row {i} ({FARE_MATRIX_STATIONS_RAW[i]}) has {len(row)} values, expected 41"

    records = []
    for i, from_raw in enumerate(FARE_MATRIX_STATIONS_RAW):
        from_station = FARE_TO_GRAPH_NAME_MAP[from_raw]
        for j, to_raw in enumerate(FARE_MATRIX_STATIONS_RAW):
            to_station = FARE_TO_GRAPH_NAME_MAP[to_raw]
            records.append({
                "from_station": from_station,
                "to_station": to_station,
                "fare": FARE_MATRIX_VALUES[i][j],
            })
    return records


def run_checks(records: list[dict]) -> bool:
    """Spot-check parsed records against values confirmed by visual
    inspection of the rasterized fare table PDF. Returns True if every
    check passes."""

    def fare_between(a, b):
        for r in records:
            if r["from_station"] == a and r["to_station"] == b:
                return r["fare"]
        return None

    checks = [
        ("Chennai Airport", "Chennai Airport", 0),
        ("Chennai Airport", "Meenambakkam", 10),
        ("Chennai Airport", "Wimco Nagar Depot", 50),
        ("Chennai Central", "Egmore", 10),
        ("Egmore", "Chennai Central", 10),
        ("St. Thomas Mount", "Arignar Anna Alandur", 10),
        ("St. Thomas Mount", "Chennai Airport", 30),
    ]

    all_passed = True
    for a, b, expected in checks:
        actual = fare_between(a, b)
        if actual != expected:
            all_passed = False
            print(f"  [MISMATCH] {a} -> {b}: expected {expected}, got {actual}")
    return all_passed


if __name__ == "__main__":
    # Standalone sanity run: python -m prerun.fare_matrix_data
    records = build_fare_records()
    passed = run_checks(records)
    print(f"Total records: {len(records)} (expected 1681)")
    print("All spot checks passed." if passed else "SOME CHECKS FAILED.")
