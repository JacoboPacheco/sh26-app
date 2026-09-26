"""Distances and state polygons for the pipeline (no GIS dependency).

Earth radius 6,371 km = 3,958.8 mi: with it, the haversine distances between Sperry's project centers match
their worked example to within 0.005 mi (their sheet rounds to 0.01).
"""

from __future__ import annotations

import math
import sys
from functools import lru_cache
from pathlib import Path

R_KM = 6371.0
R_MI = 3958.8
DEMO = Path(__file__).resolve().parent.parent  # backend/demo


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """a, b = (lat, lon)."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R_KM * math.asin(min(1.0, math.sqrt(h)))


def haversine_mi(a: tuple[float, float], b: tuple[float, float]) -> float:
    return haversine_km(a, b) * R_MI / R_KM


def midpoint(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """Arithmetic midpoint of (lat, lon), the method in Sperry's guide (fine at these distances)."""
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def point_in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


@lru_cache(maxsize=1)
def census_states() -> dict[str, dict]:
    """{code: {name, rings}} for every state, full resolution, from the Census file build_outline.py reads."""
    sys.path.insert(0, str(DEMO))
    import build_outline as bo  # reads backend/demo/raw/census/cb_2024_us_state_20m.{shp,dbf}

    recs = bo.dbf_records(bo.RAW.with_suffix(".dbf"))
    shapes = bo.shp_polygons(bo.RAW.with_suffix(".shp"))
    out = {}
    for rec, shape in zip(recs, shapes):
        rings = [[[lon, lat] for lon, lat in ring] for ring in shape if len(ring) >= 4]
        out[rec["STUSPS"]] = {"name": rec["NAME"], "rings": rings}
    return out


def state_of(lat: float, lon: float, codes: tuple[str, ...] = ("SC", "GA", "NC", "AL", "FL", "TN")) -> str | None:
    """Which state polygon contains the point (checks the given codes only). Even-odd over all rings."""
    states = census_states()
    for code in codes:
        s = states.get(code)
        if s and sum(point_in_ring(lon, lat, r) for r in s["rings"]) % 2 == 1:
            return code
    return None


def km_to_state(lat: float, lon: float, code: str) -> float:
    """Rough distance (km) from a point to a state's outline vertices (0 if inside)."""
    if state_of(lat, lon, (code,)) == code:
        return 0.0
    ring_pts = (p for r in census_states()[code]["rings"] for p in r)
    return min(haversine_km((lat, lon), (p[1], p[0])) for p in ring_pts)
