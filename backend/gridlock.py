"""Build plans (GridLock): neighboring utilities' public construction plans, located, validated,
and compared for coordination opportunities.

This module is the ENGINE + API. The data pipeline (backend/demo/gridlock/build.py) writes
data/projects.json and data/basemap.json from the public filings; this module loads them once
(and again whenever the file changes on disk), precomputes every pair's distances, and serves:

  GET /api/gridlock/summary               counts per utility + the pipeline's report
  GET /api/gridlock/projects              every located project (map payload) + the quarantine
  GET /api/gridlock/projects/{id}         one project in full, with its flagged overlaps
  GET /api/gridlock/basemap               SC + GA outlines and OSM transmission lines (context)
  GET /api/gridlock/overlaps              ranked cross-utility pairs within max_km
  GET /api/gridlock/opportunities         the top overlaps with both projects inlined
  GET /api/gridlock/estimate/{overlap_id} a rough, sourced low-high estimate of what a pair could share
  GET /api/gridlock/sperry-check          Sperry's worked example reproduced live (center method)

Until projects.json exists (the pipeline runs separately), every endpoint works from Sperry's
worked example (demo/gridlock/sperry_example.json) and says so (`fallback: true`).

Geometry. Each project is a point (a substation, or a line with one located endpoint) or a
straight segment between its two located endpoints. The distance between two projects is the
distance between their CLOSEST POINTS (point-point, point-segment, segment-segment, 0 when two
segments cross), computed on a local equirectangular plane centred on the pair (cos of the pair's
mean latitude): at these distances that is within ~0.1 % of the geodesic. Sperry's own method is
kept alongside: center = midpoint of the located endpoints, haversine between centers (miles,
R = 3958.8 mi, which reproduces their sheet to the hundredth of a mile).

Tiers (closest points; closer is worth more):
  touching  < 0.1 km or crossing -> "Must coordinate"          (outage timing, crossing structures)
  row       < 1.6 km             -> "Share the land"           (right-of-way, access roads, permits)
  site      < 8 km               -> "Share site logistics"     (laydown yards, deliveries)
  crews     <= max_km (40)       -> "Share crews and equipment"

Score (0-100) = distance factor (tier weight, sliding down within the tier) x timeline factor
(build windows overlap 1, gap <= 1 yr .8, <= 3 yr .5, longer .25, unknown .5) x location
confidence (the weaker of the two: high 1, medium .8, low .5) x 1.1 when both are line work at the
same kV class. Every overlap carries its reasons in plain words.

Honesty rules (CLAUDE.md -> Decisions): real public filings; never say whether utilities are or
aren't coordinating ("could coordinate"); estimates are low-high ranges with their basis, sources
and assumptions, never a single falsely precise number.
"""

import json
import math
import os
import re
import threading
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

router = APIRouter(tags=["gridlock"])

HERE = Path(__file__).parent / "demo" / "gridlock"
DATA_DIR = Path(os.getenv("GRIDLOCK_DATA_DIR") or HERE / "data")  # override only for tests
PROJECTS_FILE = DATA_DIR / "projects.json"
BASEMAP_FILE = DATA_DIR / "basemap.json"
SPERRY_FILE = HERE / "sperry_example.json"

R_KM = 6371.0088  # mean Earth radius
R_MI = 3958.8  # the radius that reproduces Sperry's sheet (their distances to 0.01 mi)
KM_PER_MI = 1.609344
KM_PER_DEG = R_KM * math.pi / 180

UTILITIES = {
    "DESC": ("Dominion Energy South Carolina", "SC"),
    "GPC": ("Georgia Power", "GA"),
    "GTC": ("Georgia Transmission Corporation", "GA"),
    "MEAG": ("MEAG Power", "GA"),
    "DU": ("Dalton Utilities", "GA"),
}
UTILITY_ORDER = list(UTILITIES)
GEORGIA_ITS = ("GPC", "GTC", "MEAG", "DU")  # the four sponsors of Georgia's joint ITS 10-year plan
ALIASES = {"GA": list(GEORGIA_ITS), "SC": ["DESC"], "ALL": UTILITY_ORDER}
NAME_TO_CODE = {name.lower(): code for code, (name, _) in UTILITIES.items()}

# (id, outer edge km, weight at the inner edge, weight at the outer edge, label, what could be shared)
TIERS = [
    ("touching", 0.1, 1.00, 1.00, "Must coordinate", "the projects meet or cross, so outage timing and crossing structures need one plan"),
    ("row", 1.6, 0.90, 0.70, "Share the land", "close enough to share right-of-way, access roads and permits"),
    ("site", 8.0, 0.70, 0.45, "Share site logistics", "close enough to share laydown yards and deliveries"),
    ("crews", 40.0, 0.45, 0.20, "Share crews and equipment", "close enough to share line crews and heavy equipment"),
]
TIER_BY_ID = {t[0]: t for t in TIERS}
SHARE_LINE = {
    "touching": "One outage plan and one structure design where the projects meet or cross",
    "row": "Right-of-way, access roads and permits along the shared stretch",
    "site": "A laydown yard and material deliveries",
    "crews": "Line crews and heavy equipment, mobilized once",
}

MAX_KM_DEFAULT, MAX_KM_CAP = 40.0, 50.0
WINDOW_DEFAULT, WINDOW_MAX = 24, 120
OVERLAP_LIMIT_DEFAULT, OVERLAP_LIMIT_MAX = 500, 2000
OPP_LIMIT_DEFAULT, OPP_LIMIT_MAX = 10, 50
CONF_FACTOR = {"high": 1.0, "medium": 0.8, "low": 0.5}
CONF_RANK = {"high": 3, "medium": 2, "low": 1}
KV_CLASSES = (46, 69, 115, 138, 161, 230, 345, 500)
LINE_KINDS = {"new_line", "line_rebuild", "reconductor", "tap"}
SAME_KV_BONUS = 1.1

_lock = threading.Lock()
_current: dict = {"key": None}  # replaced whole on reload, so a request mid-reload keeps a consistent view


# ----------------------------------------------------------------------------- dates


def _date(s) -> date | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    days = [31, 29 if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, days))


def parse_sperry_date(raw) -> tuple[date | None, str]:
    """Sperry's sheet mixes text dates (M/D/YYYY) and Excel serial numbers (45809 = 2025-06-01).
    Returns (date, how) where how is 'text', 'excel_serial' or 'unparseable'."""
    s = str(raw).strip()
    if s.isdigit() and 20000 < int(s) < 80000:
        return date(1899, 12, 30) + timedelta(days=int(s)), "excel_serial"
    parts = s.split("/")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        mo, dy, yr = (int(p) for p in parts)
        if yr < 100:
            yr += 2000
        try:
            return date(yr, mo, dy), "text"
        except ValueError:
            pass
    return None, "unparseable"


# ----------------------------------------------------------------------------- loading


def _kv_from_name(name: str) -> list[int]:
    return sorted({int(v) for v in re.findall(r"(\d{2,3})\s*-?\s*kv", name, flags=re.I)}, reverse=True)


def _kind_from_name(name: str) -> str:
    n = name.lower()
    if "reconductor" in n:
        return "reconductor"
    if "rebuild" in n:
        return "line_rebuild"
    if "construct" in n or " new " in f" {n} ":
        return "new_line"
    if " tap" in n:
        return "tap"
    if "substation" in n or "reactor" in n or "transformer" in n:
        return "substation"
    return "other"


def _projects_from_sperry(example: dict) -> list[dict]:
    """Sperry's worked example in the projects.json shape — the fallback until the pipeline has run."""
    out = []
    for i, p in enumerate(example["projects"]):
        code = NAME_TO_CODE.get(p["utility"].lower(), p["utility"])
        endpoints, coords = [], []
        for k in "ab":
            lat, lon = p.get(f"lat_{k}"), p.get(f"lon_{k}")
            located = lat is not None and lon is not None
            endpoints.append(
                {
                    "name": p.get(f"name_{k}"),
                    "raw": p.get(f"name_{k}"),
                    "lat": lat,
                    "lon": lon,
                    "osm": None,
                    "confidence": "high" if located else None,
                    "match": "located by hand in Sperry's worked example" if located else "not located in Sperry's example",
                }
            )
            if located:
                coords.append([lon, lat])
        when, how = parse_sperry_date(p["in_service_date_raw"])
        checks = [
            {
                "id": "in_service_date",
                "status": "warn" if how == "excel_serial" else ("pass" if when else "fail"),
                "detail": (
                    f"stored as the Excel serial number {p['in_service_date_raw']}; normalized to {when.isoformat()}"
                    if how == "excel_serial"
                    else (f"parsed {p['in_service_date_raw']!r}" if when else f"could not parse {p['in_service_date_raw']!r}")
                ),
            },
            {
                "id": "endpoints_located",
                "status": "pass" if len(coords) == 2 else "warn",
                "detail": f"{len(coords)} of 2 endpoints located",
            },
        ]
        out.append(
            {
                "id": p["project_id"],
                "utility": code,
                "utility_name": UTILITIES.get(code, (p["utility"],))[0],
                "state": p.get("state"),
                "name": p["project_name"],
                "kv": _kv_from_name(p["project_name"]),
                "kind": _kind_from_name(p["project_name"]),
                "endpoints": endpoints,
                "geometry": {"type": "segment" if len(coords) == 2 else "point", "coords": coords} if coords else None,
                "center": [p["lat_center"], p["lon_center"]],
                "in_service": when.isoformat() if when else None,
                "in_service_raw": str(p["in_service_date_raw"]),
                "build_window": None,
                "status": None,
                "need": None,
                "description": None,
                "cost_usd": None,
                "cost_by_year": None,
                "zone": None,
                "teams_no": None,
                "miles": None,
                "tags": ["sperry_example"],
                "confidence": "high",
                "provenance": {"source": "sperry_example", "page": None, "text": f"row {i + 2}: {p['project_name']}"},
                "checks": checks,
            }
        )
    return out


def _finite(obj):
    """NaN / Infinity (which Python's json reads and writes) become null, so every payload is valid JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_finite(v) for v in obj]
    return obj


def _read_json(path: Path):
    return _finite(json.loads(path.read_text(encoding="utf-8")))


def _file_key(path: Path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _geometry_coords(p: dict) -> list[list[float]] | None:
    """[[lon, lat], ...] (1 point or a polyline), or None when the project can't be placed."""
    g = p.get("geometry")
    if g and g.get("coords"):
        coords = [[float(c[0]), float(c[1])] for c in g["coords"] if c and len(c) >= 2]
        if coords and all(math.isfinite(v) for c in coords for v in c):
            return coords
    c = p.get("center")
    if c and len(c) == 2 and c[0] is not None and c[1] is not None:
        return [[float(c[1]), float(c[0])]]
    return None


def _center(p: dict, coords: list[list[float]]) -> tuple[float, float]:
    c = p.get("center")
    if c and len(c) == 2 and c[0] is not None and c[1] is not None:
        return float(c[0]), float(c[1])
    ends = [coords[0], coords[-1]] if len(coords) > 1 else coords
    return sum(e[1] for e in ends) / len(ends), sum(e[0] for e in ends) / len(ends)


def _kv_classes(kvs) -> set[int]:
    out = set()
    for v in kvs or []:
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.add(min(KV_CLASSES, key=lambda c: abs(c - v)))
    return out


def _sperry_pairs(doc: dict, example: dict, fallback: bool) -> dict:
    """{frozenset(our id a, our id b): 'OVL_n'} for Sperry's six known overlaps, through the pipeline's
    own match of their projects to ours (report.sperry_example.projects); ids are theirs in the fallback."""
    if fallback:
        ours = {p["project_id"]: p["project_id"] for p in example["projects"]}
    else:
        rep = (doc.get("report") or {}).get("sperry_example") or {}
        ours = {m.get("sperry_id"): m.get("our_id") for m in rep.get("projects") or [] if isinstance(m, dict)}
    out = {}
    for o in example["overlaps"]:
        a, b = ours.get(o["project_id_a"]), ours.get(o["project_id_b"])
        if a and b:
            out[frozenset((a, b))] = o["overlap_id"]
    return out


def _load() -> dict:
    """The current data, reloaded whenever projects.json (or the fallback) changes on disk."""
    key = (_file_key(PROJECTS_FILE), _file_key(BASEMAP_FILE), _file_key(SPERRY_FILE))
    global _current
    if _current.get("key") == key:
        return _current
    with _lock:
        if _current.get("key") == key:
            return _current
        fallback_reason = None
        doc = None
        if key[0] is not None:
            try:
                doc = _read_json(PROJECTS_FILE)
                if not isinstance(doc, dict) or not isinstance(doc.get("projects"), list):
                    raise ValueError("projects.json has no projects list")
            except (OSError, ValueError) as e:
                doc, fallback_reason = None, f"projects.json could not be read ({e}); showing Sperry's worked example"
        else:
            fallback_reason = "the data pipeline hasn't written projects.json yet; showing Sperry's worked example"
        example = _read_json(SPERRY_FILE)
        if doc is None:
            doc = {
                "built_at": None,
                "sources": [
                    {
                        "id": "sperry_example",
                        "utility": "DESC + GPC",
                        "title": "Sperry Tech GridLock starter package: Projects_Overlaps.xlsx (worked example)",
                        "file": "backend/demo/gridlock/sperry_example.json",
                        "url": None,
                        "pages": None,
                        "sha256": None,
                    }
                ],
                "projects": _projects_from_sperry(example),
                "quarantine": [],
                "report": None,
            }
        projects = [p for p in doc["projects"] if isinstance(p, dict) and p.get("id") and p.get("utility")]
        quarantine = [q for q in (doc.get("quarantine") or []) if isinstance(q, dict)]

        # comparable projects: those with a place on the map
        placed, geoms = [], []
        for p in projects:
            coords = _geometry_coords(p)
            if coords:
                placed.append(p)
                geoms.append(coords)
        n = len(placed)
        centers = np.array([_center(p, g) for p, g in zip(placed, geoms)], dtype=float).reshape(n, 2)
        closest = _closest_matrix(geoms)
        center_km, center_mi = _haversine_matrix(centers)
        new_state = {
            "sperry_pairs": _sperry_pairs(doc, example, fallback_reason is not None),
            "key": key,
            "doc": doc,
            "fallback": fallback_reason is not None,
            "fallback_reason": fallback_reason,
            "example": example,
            "projects": projects,
            "by_id": {p["id"]: p for p in projects},
            "quarantine": quarantine,
            "placed": placed,
            "geoms": geoms,
            "index": {p["id"]: i for i, p in enumerate(placed)},
            "util": np.array([p["utility"] for p in placed], dtype=object),
            "closest_km": closest,
            "center_km": center_km,
            "center_mi": center_mi,
            "kv": [_kv_classes(p.get("kv")) for p in placed],
            "line": np.array([p.get("kind") in LINE_KINDS for p in placed], dtype=bool),
            "cache": {},
            "basemap": None,
        }
        _current = new_state
        return _current


# ----------------------------------------------------------------------------- geometry


def _haversine_matrix(centers: np.ndarray):
    lat = np.radians(centers[:, 0])
    lon = np.radians(centers[:, 1])
    dlat = lat[:, None] - lat[None, :]
    dlon = lon[:, None] - lon[None, :]
    h = np.sin(dlat / 2) ** 2 + np.cos(lat)[:, None] * np.cos(lat)[None, :] * np.sin(dlon / 2) ** 2
    ang = 2 * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))
    return ang * R_KM, ang * R_MI


def _haversine_mi(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R_MI * math.asin(math.sqrt(h))


def _segments(geoms):
    """Every project as segments (a point is a zero-length segment): arrays of [lon, lat] + owner."""
    a0, a1, owner = [], [], []
    for i, g in enumerate(geoms):
        if len(g) == 1:
            a0.append(g[0])
            a1.append(g[0])
            owner.append(i)
        else:
            for p, q in zip(g[:-1], g[1:]):
                a0.append(p)
                a1.append(q)
                owner.append(i)
    return np.array(a0, dtype=float).reshape(-1, 2), np.array(a1, dtype=float).reshape(-1, 2), np.array(owner, dtype=int)


def _pt_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    safe = np.where(l2 > 1e-12, l2, 1.0)
    t = np.where(l2 > 1e-12, ((px - ax) * dx + (py - ay) * dy) / safe, 0.0)
    t = np.clip(t, 0.0, 1.0)
    return np.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _closest_matrix(geoms) -> np.ndarray:
    """(P, P) closest-point distance in km between every pair of projects, vectorized in blocks."""
    n = len(geoms)
    out = np.full((n, n), np.inf)
    if n == 0:
        return out
    a0, a1, owner = _segments(geoms)
    s = len(owner)
    starts = np.flatnonzero(np.r_[True, owner[1:] != owner[:-1]])
    mid_lat = (a0[:, 1] + a1[:, 1]) / 2
    qx0, qy0, qx1, qy1 = a0[:, 0][None, :], a0[:, 1][None, :] * KM_PER_DEG, a1[:, 0][None, :], a1[:, 1][None, :] * KM_PER_DEG
    block = max(1, 250_000 // max(s, 1))
    # blocks of whole projects, so each block reduces cleanly to project rows
    pi = 0
    while pi < n:
        pj = min(n, pi + block)
        r0 = starts[pi]
        r1 = starts[pj] if pj < n else s
        c = np.cos(np.radians((mid_lat[r0:r1, None] + mid_lat[None, :]) / 2)) * KM_PER_DEG
        px0, py0 = a0[r0:r1, 0][:, None] * c, (a0[r0:r1, 1] * KM_PER_DEG)[:, None]
        px1, py1 = a1[r0:r1, 0][:, None] * c, (a1[r0:r1, 1] * KM_PER_DEG)[:, None]
        X0, X1 = qx0 * c, qx1 * c
        d = np.minimum.reduce(
            [
                _pt_seg(px0, py0, X0, qy0, X1, qy1),
                _pt_seg(px1, py1, X0, qy0, X1, qy1),
                _pt_seg(X0, qy0, px0, py0, px1, py1),
                _pt_seg(X1, qy1, px0, py0, px1, py1),
            ]
        )
        d1 = (X1 - X0) * (py0 - qy0) - (qy1 - qy0) * (px0 - X0)
        d2 = (X1 - X0) * (py1 - qy0) - (qy1 - qy0) * (px1 - X0)
        d3 = (px1 - px0) * (qy0 - py0) - (py1 - py0) * (X0 - px0)
        d4 = (px1 - px0) * (qy1 - py0) - (py1 - py0) * (X1 - px0)
        d = np.where((d1 * d2 < 0) & (d3 * d4 < 0), 0.0, d)
        rows = np.minimum.reduceat(d, starts[pi:pj] - r0, axis=0)
        out[pi:pj] = np.minimum.reduceat(rows, starts, axis=1)
        pi = pj
    return out


def _seg_closest(p0, p1, q0, q1):
    """Closest points between segments p0-p1 and q0-q1 (each [lon, lat]) on the pair's local plane.
    Returns (km, point on p [lat, lon], point on q [lat, lon], crosses)."""
    c = math.cos(math.radians(((p0[1] + p1[1]) / 2 + (q0[1] + q1[1]) / 2) / 2)) * KM_PER_DEG

    def xy(pt):
        return pt[0] * c, pt[1] * KM_PER_DEG

    def back(x, y):
        return [round(y / KM_PER_DEG, 6), round(x / c, 6)]

    P0, P1, Q0, Q1 = xy(p0), xy(p1), xy(q0), xy(q1)

    def proj(pt, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 <= 1e-12 else max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / l2))
        return (a[0] + t * dx, a[1] + t * dy)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    e1, e2 = cross(Q0, Q1, P0), cross(Q0, Q1, P1)
    e3, e4 = cross(P0, P1, Q0), cross(P0, P1, Q1)
    if e1 * e2 < 0 and e3 * e4 < 0:
        t = e1 / (e1 - e2)
        x, y = P0[0] + t * (P1[0] - P0[0]), P0[1] + t * (P1[1] - P0[1])
        return 0.0, back(x, y), back(x, y), True
    cands = []
    for pt, a, b, on_p in ((P0, Q0, Q1, True), (P1, Q0, Q1, True), (Q0, P0, P1, False), (Q1, P0, P1, False)):
        other = proj(pt, a, b)
        dist = math.hypot(pt[0] - other[0], pt[1] - other[1])
        cands.append((dist, (pt, other) if on_p else (other, pt)))
    dist, (pp, qq) = min(cands, key=lambda t: t[0])
    return dist, back(*pp), back(*qq), False


def _segs_of(coords):
    return [(coords[0], coords[0])] if len(coords) == 1 else list(zip(coords[:-1], coords[1:]))


def _pair_closest(ga, gb):
    best = None
    for p0, p1 in _segs_of(ga):
        for q0, q1 in _segs_of(gb):
            r = _seg_closest(p0, p1, q0, q1)
            if best is None or r[0] < best[0]:
                best = r
    return best


def _length_km(coords) -> float:
    tot = 0.0
    for p, q in zip(coords[:-1], coords[1:]):
        c = math.cos(math.radians((p[1] + q[1]) / 2))
        tot += math.hypot((q[0] - p[0]) * c * KM_PER_DEG, (q[1] - p[1]) * KM_PER_DEG)
    return tot


def _bearing_gap(a0, a1, b0, b1) -> float:
    """Angle in degrees (0-90) between two segments' directions, ignoring which way they run."""
    c = math.cos(math.radians((a0[1] + a1[1] + b0[1] + b1[1]) / 4))
    ax, ay = (a1[0] - a0[0]) * c, a1[1] - a0[1]
    bx, by = (b1[0] - b0[0]) * c, b1[1] - b0[1]
    na, nb = math.hypot(ax, ay), math.hypot(bx, by)
    if na == 0 or nb == 0:
        return 90.0
    cos = min(1.0, abs(ax * bx + ay * by) / (na * nb))
    return math.degrees(math.acos(cos))


def _length_within(ga, gb, within_km: float, samples: int = 80, max_angle: float = 30.0) -> float:
    """Length (km) of polyline ga that runs ALONGSIDE gb: within `within_km` of it and roughly parallel
    (within `max_angle` degrees) — two lines that merely cross don't share a corridor."""
    if len(ga) < 2 or len(gb) < 2:
        return 0.0
    total = _length_km(ga)
    if total <= 0:
        return 0.0
    near = 0
    count = 0
    for p, q in zip(ga[:-1], ga[1:]):
        seg_len = _length_km([p, q])
        k = max(2, int(round(samples * seg_len / total)))
        for i in range(k):
            t = (i + 0.5) / k
            pt = [p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]
            d, q0, q1 = min((_seg_closest(pt, pt, q0, q1)[0], q0, q1) for q0, q1 in _segs_of(gb))
            near += d <= within_km and _bearing_gap(p, q, q0, q1) <= max_angle
            count += 1
    return total * near / max(count, 1)


# ----------------------------------------------------------------------------- tiers, timeline, score


def tier_for(km: float) -> str:
    for tid, edge, *_ in TIERS:
        if km < edge:
            return tid
    return "crews"


def _distance_factor(km: float) -> float:
    inner = 0.0
    for tid, edge, top, bottom, *_ in TIERS:
        if km < edge or tid == "crews":
            if tid == "touching":
                return 1.0
            frac = min(1.0, max(0.0, (km - inner) / (edge - inner)))
            return top - (top - bottom) * frac
        inner = edge
    return TIERS[-1][3]


def _window_assumed(p: dict) -> bool:
    """True when the filing doesn't pin the build window's start, so the build-window setting decides it."""
    bw = p.get("build_window")
    if not isinstance(bw, dict):
        return bool(_date(p.get("in_service")))
    if "assumed" in bw:
        return bw.get("assumed") is True
    return "assum" in str(bw.get("basis") or "").lower()  # older projects.json without the flag


def _window(p: dict, months: int):
    """(start, end, basis): the filed/derived build window, or an assumed one before in-service. An assumed
    window is `months` before in-service, but never starts later than the filing allows (`latest_start`:
    DESC's 'Previous' spend means construction began before 2024)."""
    bw = p.get("build_window") or None
    ins = _date(p.get("in_service"))
    if isinstance(bw, dict):
        basis = str(bw.get("basis") or "")
        s, e = _date(bw.get("start")), _date(bw.get("end"))
        if s and e and s <= e and not _window_assumed(p):
            return s, e, basis or "from the filing"
        latest = _date(bw.get("latest_start"))
        end = e or ins
        if end and latest:
            start = min(_add_months(end, -months), latest)
            return start, end, f"spending began before {latest.year + 1}; assumed {months} months before in-service, starting no later than {latest:%b %Y}"
    if ins:
        return _add_months(ins, -months), ins, f"assumed: {months} months of construction before the in-service date"
    return None


PASSED_FACTOR = 0.6  # a shared build window that ended before today, as filed: still worth a check, ranked below live ones


def _today() -> date:
    return date.today()


def _timeline(pa: dict, pb: dict, months: int):
    """How the two build windows line up, and where today falls: a shared window still ahead or open now keeps
    its full weight; one that ended before today (as filed) is scored down, so the list leads with pairs that can
    still act on it."""
    ia, ib = _date(pa.get("in_service")), _date(pb.get("in_service"))
    gap_days = abs((ia - ib).days) if ia and ib else None
    wa, wb = _window(pa, months), _window(pb, months)
    if not wa or not wb:
        return {
            "time_gap_days": gap_days,
            "windows_overlap_months": None,
            "window_gap_days": None,
            "same_window": None,
            "factor": 0.5,
            "reason": "Timeline unknown: at least one project has no in-service date in its filing",
            "windows": [wa, wb],
            "ahead": None,
        }
    overlap = (min(wa[1], wb[1]) - max(wa[0], wb[0])).days
    if overlap > 0:
        months_ov = round(overlap / 30.44, 1)
        factor = 1.0
        s, e = max(wa[0], wb[0]), min(wa[1], wb[1])
        when = f"{s:%b %Y}" if (s.year, s.month) == (e.year, e.month) else f"{s:%b %Y} to {e:%b %Y}"
        reason = f"Build windows overlap by {_span(overlap)} ({when})"
        wgap = 0
        today = _today()
        if e < today:
            ahead = "past"
            factor = PASSED_FACTOR
            reason += f"; as filed, that shared window ended before today (x{PASSED_FACTOR:g})"
        elif s <= today:
            ahead = "open"
            reason += f"; open now, about {max(1, round((e - today).days / 30.44))} months left"
        else:
            ahead = "future"
            reason += "; still ahead"
    else:
        months_ov = 0.0
        wgap = -overlap
        factor = 0.8 if wgap <= 365 else (0.5 if wgap <= 1095 else 0.25)
        reason = "Build windows meet end to start" if wgap == 0 else f"Build windows don't overlap: {_span(wgap)} apart"
        ahead = "past" if max(wa[1], wb[1]) < _today() else "future"
        if ahead == "past":
            factor = round(factor * PASSED_FACTOR, 3)
            reason += f"; as filed, both windows ended before today (x{PASSED_FACTOR:g})"
    if gap_days is not None:
        reason += f"; in service {gap_days:,} days apart"
    return {
        "time_gap_days": gap_days,
        "windows_overlap_months": months_ov,
        "window_gap_days": wgap,
        "same_window": overlap > 0,
        "factor": factor,
        "reason": reason,
        "windows": [wa, wb],
        "ahead": ahead,  # the shared window (or both windows) against today: future, open or past
    }


def _span(days: int) -> str:
    if days < 60:
        return "1 day" if days == 1 else f"{days} days"
    if days < 730:
        return f"{round(days / 30.44)} months"
    return f"{days / 365.25:.1f} years"


def _share_line(tier: str, tl: dict) -> str:
    line = SHARE_LINE[tier]
    wa, wb = tl["windows"]
    if tl["same_window"]:
        s, e = max(wa[0], wb[0]), min(wa[1], wb[1])
        years = f"{s.year}" if s.year == e.year else f"{s.year}-{e.year}"
        return f"{line}, while both could be under construction ({years})"
    if tl["window_gap_days"] is not None:
        return f"{line}, if schedules were aligned (build windows {_span(tl['window_gap_days'])} apart)"
    return line


def _overlap_record(st: dict, i: int, j: int, months: int, method: str) -> dict:
    pa, pb = st["placed"][i], st["placed"][j]
    ga, gb = st["geoms"][i], st["geoms"][j]
    km, cpa, cpb, crosses = _pair_closest(ga, gb)
    tier = "touching" if crosses or km < 0.1 else tier_for(km)
    _, _, _, _, label, what = TIER_BY_ID[tier]
    center_mi = float(st["center_mi"][i, j])
    center_km = float(st["center_km"][i, j])
    score_km = km if method == "closest" else center_km
    df = _distance_factor(score_km)
    tl = _timeline(pa, pb, months)
    ca, cb = pa.get("confidence") or "low", pb.get("confidence") or "low"
    weaker = ca if CONF_RANK.get(ca, 1) <= CONF_RANK.get(cb, 1) else cb
    cf = CONF_FACTOR.get(weaker, 0.5)
    shared_kv = st["kv"][i] & st["kv"][j]
    kf = SAME_KV_BONUS if (st["line"][i] and st["line"][j] and shared_kv) else 1.0
    score = round(100 * df * tl["factor"] * cf * kf, 1)

    reasons = [
        (
            "The two projects cross" if crosses else f"Closest points {km:.2f} km ({km / KM_PER_MI:.2f} mi) apart"
        )
        + f": {label.lower()}, {what}",
        f"Centers {center_mi:.2f} mi apart (Sperry's center method)",
        tl["reason"],
    ]
    if kf > 1:
        reasons.append(f"Both are {max(shared_kv)} kV line work (x{SAME_KV_BONUS})")
    reasons.append(
        "Both locations high confidence"
        if ca == cb == "high"
        else f"Location confidence {weaker}: {pa['id']} is {ca}, {pb['id']} is {cb} (placed from name matches, check before relying on it)"
    )
    if pa["utility"] in GEORGIA_ITS and pb["utility"] in GEORGIA_ITS:
        reasons.append("Both are listed in the same Georgia ITS 10-year plan (one joint filing)")
    sperry = st["sperry_pairs"].get(frozenset((pa["id"], pb["id"])))
    if sperry:
        reasons.append(f"One of the six overlaps in Sperry's worked example ({sperry}), found here from the raw filings")
    reasons.append(
        f"Score {score:g} = distance {df:.2f} ({'closest points' if method == 'closest' else 'centers'})"
        f" x timeline {tl['factor']:g} x location {cf:g}" + (f" x same kV {SAME_KV_BONUS:g}" if kf > 1 else "")
    )
    return {
        "id": f"{pa['id']}~{pb['id']}",
        "a": pa["id"],
        "b": pb["id"],
        "a_utility": pa["utility"],
        "b_utility": pb["utility"],
        "distance_km": round(km, 3),
        "distance_mi": round(km / KM_PER_MI, 3),
        "center_distance_km": round(center_km, 3),
        "center_distance_mi": round(center_mi, 2),
        "crosses": crosses,
        "tier": tier,
        "tier_label": label,
        "closest_points": [cpa, cpb],
        "time_gap_days": tl["time_gap_days"],
        "windows_overlap_months": tl["windows_overlap_months"],
        "window_gap_days": tl["window_gap_days"],
        "same_window": tl["same_window"],
        "ahead": tl["ahead"],
        "score": score,
        "score_parts": {"distance": round(df, 3), "timeline": tl["factor"], "location": cf, "same_kv": kf},
        "share": _share_line(tier, tl),
        "sperry": sperry,
        "reasons": reasons,
    }


# ----------------------------------------------------------------------------- params


def _codes(raw: str, which: str) -> list[str]:
    out = []
    for part in (raw or "").split(","):
        code = part.strip().upper()
        if not code:
            continue
        if code in ALIASES:
            out.extend(ALIASES[code])
        elif code in UTILITIES:
            out.append(code)
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown utility {part.strip()!r} for {which}: use DESC, GPC, GTC, MEAG, DU, GA, SC or all",
            )
    if not out:
        raise HTTPException(status_code=422, detail=f"Pick at least one utility for {which}")
    return sorted(set(out), key=UTILITY_ORDER.index)


def _params(max_km: float, window_months: int, method: str, a: str, b: str) -> dict:
    if not math.isfinite(max_km) or not (0 < max_km <= MAX_KM_CAP):
        raise HTTPException(status_code=422, detail=f"max_km must be more than 0 and at most {MAX_KM_CAP:g} km")
    if not (0 <= window_months <= WINDOW_MAX):
        raise HTTPException(status_code=422, detail=f"window_months must be between 0 and {WINDOW_MAX}")
    method = (method or "").strip().lower()
    if method not in ("closest", "center"):
        raise HTTPException(status_code=422, detail="method must be 'closest' (closest points) or 'center' (Sperry's center method)")
    ca, cb = _codes(a, "a"), _codes(b, "b")
    if len(set(ca) | set(cb)) < 2:
        raise HTTPException(status_code=422, detail="Pick two different utilities to compare")
    return {"max_km": max_km, "window_months": window_months, "method": method, "a": ca, "b": cb}


def _compute(st: dict, prm: dict) -> dict:
    """Every flagged pair, ranked (cached per data version and parameters)."""
    key = (prm["max_km"], prm["window_months"], prm["method"], tuple(prm["a"]), tuple(prm["b"]))
    hit = st["cache"].get(key)
    if hit is not None:
        return hit
    util = st["util"]
    n = len(util)
    in_a = np.isin(util, prm["a"]) if n else np.zeros(0, bool)
    in_b = np.isin(util, prm["b"]) if n else np.zeros(0, bool)
    m = (in_a[:, None] & in_b[None, :]) | (in_b[:, None] & in_a[None, :])
    m &= util[:, None] != util[None, :]
    m = np.triu(m, 1)
    total_pairs = int(m.sum())
    metric = st["closest_km"] if prm["method"] == "closest" else st["center_km"]
    flagged = m & (metric <= prm["max_km"])
    rows = []
    order = {c: k for k, c in enumerate(UTILITY_ORDER)}
    for i, j in zip(*np.nonzero(flagged)):
        i, j = int(i), int(j)
        a_ok, b_ok = in_a[i] and in_b[j], in_a[j] and in_b[i]
        if a_ok and b_ok:  # both orientations valid: the utility order decides (DESC first)
            if (order[util[j]], st["placed"][j]["id"]) < (order[util[i]], st["placed"][i]["id"]):
                i, j = j, i
        elif not a_ok:
            i, j = j, i
        rows.append(_overlap_record(st, i, j, prm["window_months"], prm["method"]))
    rows.sort(key=lambda r: (-r["score"], r["distance_km"], r["id"]))
    for k, r in enumerate(rows, 1):
        r["rank"] = k
    result = {"total_pairs": total_pairs, "overlaps": rows}
    if len(st["cache"]) > 64:
        st["cache"].clear()
    st["cache"][key] = result
    return result


# ----------------------------------------------------------------------------- payloads

SLIM_FIELDS = (
    "id", "utility", "utility_name", "state", "name", "kv", "kind", "geometry", "center", "in_service",
    "build_window", "status", "cost_usd", "miles", "zone", "teams_no", "confidence", "tags",
)


def _slim(p: dict) -> dict:
    out = {k: p.get(k) for k in SLIM_FIELDS}
    prov = p.get("provenance") or {}
    out["provenance"] = {"source": prov.get("source"), "page": prov.get("page")}
    return out


def _counts(st: dict) -> dict:
    counts = {}
    for code in UTILITY_ORDER:
        mine = [p for p in st["projects"] if p.get("utility") == code]
        q = [r for r in st["quarantine"] if r.get("utility") == code]
        if not mine and not q:
            continue
        located = [p for p in mine if _geometry_coords(p)]
        conf = {c: sum(1 for p in located if p.get("confidence") == c) for c in ("high", "medium", "low")}
        counts[code] = {
            "name": UTILITIES[code][0],
            "extracted": len(mine) + len(q),
            "located": len(located),
            "quarantined": len(q),
            "by_confidence": conf,
        }
    return counts


def _fallback_report(st: dict) -> dict:
    n = len(st["projects"])
    return {
        "stages": [{"id": "load", "label": "Loaded Sperry's worked example (the pipeline hasn't run yet)", "in": n, "out": n, "ms": 0}],
        "checks": [
            {
                "id": c_id,
                "label": label,
                "passed": sum(1 for p in st["projects"] for c in p["checks"] if c["id"] == c_id and c["status"] == "pass"),
                "warned": sum(1 for p in st["projects"] for c in p["checks"] if c["id"] == c_id and c["status"] == "warn"),
                "failed": sum(1 for p in st["projects"] for c in p["checks"] if c["id"] == c_id and c["status"] == "fail"),
            }
            for c_id, label in (("in_service_date", "In-service date parses"), ("endpoints_located", "Both endpoints located"))
        ],
        "sperry_example": None,  # filled live by /summary
        "coverage": None,
        "changed_since_last_run": None,
    }


# ----------------------------------------------------------------------------- estimate

SOURCES = {
    "miso24": {
        "title": "MISO Transmission Cost Estimation Guide for MTEP24 (May 1, 2024)",
        "url": "https://cdn.misoenergy.org/20240501%20PSC%20Item%2004%20MISO%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24632680.pdf",
    },
    "miso18": {
        "title": "MISO Transmission and Substation Project Cost Estimation Guide for MTEP 2018",
        "url": "https://cdn.misoenergy.org/Transmission-and-Substation-Project-Cost-Estimation-Guide-for-MTEP-2018144804.pdf",
    },
    "usda26": {
        "title": "USDA NASS, Land Values 2026 Summary (July 31, 2026)",
        "url": "https://www.nass.usda.gov/Publications/Todays_Reports/reports/land0726.pdf",
    },
    "sce_wod": {
        "title": "SCE West of Devers Upgrade Project, PEA section 3.2.1.1 Staging Areas (Table 3.2-A)",
        "url": "https://ia.cpuc.ca.gov/environment/info/aspen/westofdevers/pea/3.0_project_description_part3.pdf",
    },
}

# MISO 2018 -> MTEP24: the guide's own like-for-like unit costs rose ~22 % (forested clearing
# $4,920 -> $5,995/acre, wetland matting $57,500 -> $69,975/acre, mitigation $46,125 -> $56,132/acre).
ESCALATE_2018 = 1.22
# MISO 2018 section 4.1.1.3: mobilization/demobilization per transmission line project, by kV
MOB_LINE_2018 = {46: 100_000, 69: 100_000, 115: 100_000, 138: 150_000, 161: 150_000, 230: 200_000, 345: 250_000, 500: 300_000}
# MISO 2018 section 4.2.1.2: substation mobilization/demobilization, existing-site upgrade / new site
MOB_SUB_2018 = (157_590, 262_660)
# MISO MTEP24 Table 3.1-1: indicative right-of-way width (ft) by kV
ROW_FT = {46: 80, 69: 80, 115: 90, 138: 95, 161: 100, 230: 125, 345: 175, 500: 200}
# MISO MTEP24 Table 2.1 (per acre, used for every MISO state listed): acquisition, regulatory + permitting
ROW_ACQ_PER_ACRE = 14_247
PERMIT_PER_ACRE = 2_968
# USDA NASS Land Values 2026: average pasture value per acre (MISO prices land from USDA pasture;
# cropland = 3x pasture, suburban = 5x)
PASTURE_2026 = {"GA": 5_100, "SC": 4_500}
# MISO 2018 section 4.1.1.6 road surfacing $10,250/mile (level) and 4.2.1.6 forested access road $51,250/mile
ROAD_PER_MILE_2018 = (10_250, 51_250)
# MISO MTEP24 Table 2.3-1: substation access road $593,636 per mile
SUB_ROAD_PER_MILE = 593_636
# Laydown/staging yard: SCE's West of Devers PEA lists yards of 2.8 to 30 acres; a pair of line
# projects of this size needs a small one (assume 3-5 acres). Prep = clearing (MISO 2018 level
# $260/acre, escalated / MTEP24 forested $5,995) + gravel (MISO 2018 road surfacing $10,250 per
# mile of a 16 ft road = 1.94 acres -> ~$5,300/acre, escalated).
YARD_ACRES = (3, 5)
YARD_PREP_PER_ACRE = (round(260 * ESCALATE_2018 + 5_300 * ESCALATE_2018), round(5_995 + 5_300 * ESCALATE_2018))
# MISO MTEP24 Table 2.2-1 (steel pole, single circuit) angled dead-end structure: material + installation
# + hardware + foundation, by kV
DEADEND_MTEP24 = {
    46: 165_089, 69: 165_089, 115: 185_861, 138: 201_820, 161: 222_590, 230: 270_995, 345: 414_894, 500: 695_767,
}
# MISO MTEP24 Tables 4.1-1 and 4.1-3 (exploratory $/mile incl. 30 % contingency + 7.5 % AFUDC; range
# across the 15 MISO states) — used only to show each project's rough scale when its cost is redacted
EXPLORATORY_PER_MILE = {
    "new_line": {69: (1.6e6, 2.0e6), 115: (1.8e6, 2.2e6), 138: (1.9e6, 2.3e6), 161: (1.9e6, 2.4e6), 230: (2.0e6, 2.6e6), 345: (3.2e6, 4.1e6), 500: (4.1e6, 5.1e6)},
    "line_rebuild": {69: (1.5e6, 1.5e6), 115: (1.7e6, 1.7e6), 138: (1.8e6, 1.8e6), 161: (1.8e6, 1.8e6), 230: (1.9e6, 1.9e6)},
    "reconductor": {69: (0.33e6, 0.33e6), 115: (0.39e6, 0.39e6), 138: (0.39e6, 0.39e6), 161: (0.39e6, 0.39e6), 230: (0.39e6, 0.39e6), 345: (0.62e6, 0.62e6), 500: (0.83e6, 0.83e6)},
}
# MISO MTEP24 Table 4.2-1 / 4.2-2: substation upgrade (add 1 position, ring bus) .. new 6-position double-breaker
SUBSTATION_RANGE = {69: (1.3e6, 13.4e6), 115: (1.5e6, 15.2e6), 138: (1.7e6, 16.8e6), 161: (1.8e6, 18.4e6), 230: (2.2e6, 21.3e6), 345: (3.4e6, 32.3e6), 500: (5.3e6, 47.4e6)}


def _money_range(lo: float, hi: float) -> str:
    return f"${lo / 1e6:.2f}M" if lo == hi else f"${lo / 1e6:.2f}M-${hi / 1e6:.2f}M"


def _nearest(table: dict, kv: int):
    return table[min(table, key=lambda k: abs(k - kv))]


def _main_kv(p: dict) -> int | None:
    classes = _kv_classes(p.get("kv"))
    return max(classes) if classes else None


def _mobilization(p: dict) -> tuple[float, float, str]:
    kv = _main_kv(p)
    if p.get("kind") in LINE_KINDS:
        v = _nearest(MOB_LINE_2018, kv or 115) * ESCALATE_2018
        return v, v, f"{kv or 115} kV line project"
    lo, hi = MOB_SUB_2018
    return lo * ESCALATE_2018, hi * ESCALATE_2018, "substation or equipment project"


def _project_scale(st: dict, p: dict, coords) -> dict:
    """Rough scale of one project: its filed cost, or a MISO exploratory range when there is none."""
    base = {"project": p["id"], "utility": p["utility"], "name": p.get("name")}
    prov = p.get("provenance") or {}
    if p.get("cost_usd"):
        titles = {s.get("id"): s.get("title") for s in st["doc"].get("sources") or [] if isinstance(s, dict)}
        page = f", page {prov['page']}" if prov.get("page") else ""
        src = titles.get(prov.get("source")) or prov.get("source") or "the filing"
        return {**base, "cost_low": p["cost_usd"], "cost_high": p["cost_usd"], "basis": f"Filed estimated project cost ({src}{page})"}
    kv = _main_kv(p) or 115
    kind = p.get("kind")
    missing = "Cost redacted in the public filing" if p["utility"] in GEORGIA_ITS else "No filed cost for this project"
    if kind in EXPLORATORY_PER_MILE:
        miles = p.get("miles")
        basis_len = "filed length"
        if not miles and len(coords) >= 2:
            miles = _length_km(coords) / KM_PER_MI * 1.3
            basis_len = "straight line between endpoints + 30 % routing (MISO's exploratory adder)"
        if miles:
            lo, hi = _nearest(EXPLORATORY_PER_MILE[kind], kv)
            return {
                **base,
                "cost_low": round(lo * miles, -4),
                "cost_high": round(hi * miles, -4),
                "basis": f"{missing}; ~{miles:.1f} mi ({basis_len}) x MISO exploratory {_money_range(lo, hi)} per mile for a {kv} kV {kind.replace('_', ' ')}",
                "source": SOURCES["miso24"]["title"],
            }
    if kind in ("substation", "other") or not kind:
        lo, hi = _nearest(SUBSTATION_RANGE, kv)
        return {
            **base,
            "cost_low": lo,
            "cost_high": hi,
            "basis": f"{missing}; MISO exploratory range for {kv} kV substation work (add one position .. new 6-position station)",
            "source": SOURCES["miso24"]["title"],
        }
    return {**base, "cost_low": None, "cost_high": None, "basis": f"{missing}, and too little scope to scale it"}


def _estimate(st: dict, i: int, j: int, months: int) -> dict:
    pa, pb = st["placed"][i], st["placed"][j]
    ga, gb = st["geoms"][i], st["geoms"][j]
    rec = _overlap_record(st, i, j, months, "closest")
    tier = rec["tier"]
    tiers_on = [t[0] for t in TIERS[TIERS.index(TIER_BY_ID[tier]) :]]  # this tier and every looser one
    tl = _timeline(pa, pb, months)
    aligned = bool(tl["same_window"])
    items, assumptions = [], []
    used = set()

    def item(iid, label, lo, hi, unit, basis, src, needs=None):
        lo, hi = float(lo), float(hi)
        if unit == "USD":
            lo, hi = round(lo, -3), round(hi, -3)
        else:
            lo, hi = round(lo, 1), round(hi, 1)
        items.append({"id": iid, "label": label, "low": min(lo, hi), "high": max(lo, hi), "unit": unit, "basis": basis, "source": SOURCES[src]["title"], "needs": needs})
        used.add(src)

    # crews: one mobilization instead of two
    ma, mb = _mobilization(pa), _mobilization(pb)
    small = ma if ma[1] <= mb[1] else mb
    lo, hi = 0.5 * small[0], small[1]
    if not aligned:
        lo = 0.0
    item(
        "mobilization",
        "Crews and equipment mobilized once",
        lo,
        hi,
        "USD",
        f"Half to all of the smaller project's mobilization/demobilization ({small[2]}: MISO 2018 unit cost x {ESCALATE_2018})"
        + ("" if aligned else "; low end $0 because the build windows don't overlap"),
        "miso18",
        needs="same build window",
    )

    if "site" in tiers_on:
        y_lo = YARD_ACRES[0] * YARD_PREP_PER_ACRE[0]
        y_hi = YARD_ACRES[1] * YARD_PREP_PER_ACRE[1]
        item(
            "laydown_yard",
            "One laydown yard instead of two",
            0.0 if not aligned else y_lo,
            y_hi,
            "USD",
            f"A {YARD_ACRES[0]}-{YARD_ACRES[1]} acre staging yard not built twice: clearing + gravel at "
            f"${YARD_PREP_PER_ACRE[0]:,}-${YARD_PREP_PER_ACRE[1]:,} per acre (MISO unit costs; yard sizes from SCE's West of Devers PEA, which also plans to reuse yards other projects vacate)",
            "sce_wod",
            needs="same build window",
        )
        used.add("miso18")

    shared_km = 0.0
    if "row" in tiers_on:
        a_line, b_line = len(ga) >= 2, len(gb) >= 2
        if a_line and b_line:
            shared_km = min(_length_within(ga, gb, 1.6), _length_within(gb, ga, 1.6))
        shared_mi = shared_km / KM_PER_MI
        new_line = [p for p in (pa, pb) if p.get("kind") == "new_line"]
        if shared_mi >= 0.05 and new_line:
            nl = new_line[0]
            kv_pair = [k for k in (_main_kv(pa), _main_kv(pb)) if k]
            width = _nearest(ROW_FT, min(kv_pair) if kv_pair else 115)
            acres = shared_mi * 5280 * width / 43560
            a_lo, a_hi = 0.25 * acres, 0.5 * acres
            pasture = PASTURE_2026.get(nl.get("state") or "", PASTURE_2026["GA"])
            item(
                "shared_land",
                "Land the two projects could share",
                a_lo,
                a_hi,
                "acres",
                f"{shared_mi:.1f} mi within 1.6 km of each other x a {width} ft corridor (MISO's width for {min(kv_pair) if kv_pair else 115} kV); a shared or adjacent corridor saves a quarter to half of it",
                "miso24",
            )
            item(
                "row_cost",
                "Right-of-way bought, negotiated and permitted once",
                a_lo * (pasture + ROW_ACQ_PER_ACRE + PERMIT_PER_ACRE),
                a_hi * (3 * pasture + ROW_ACQ_PER_ACRE + PERMIT_PER_ACRE),
                "USD",
                f"Shared acres x (land ${pasture:,}/acre {nl.get('state') or 'GA'} pasture, up to 3x for cropland, + ${ROW_ACQ_PER_ACRE:,}/acre acquisition + ${PERMIT_PER_ACRE:,}/acre regulatory and permitting)",
                "miso24",
            )
            used.add("usda26")
        elif shared_mi >= 0.05:
            acres = shared_mi * 5280 * _nearest(ROW_FT, min([k for k in (_main_kv(pa), _main_kv(pb)) if k] or [115])) / 43560
            item(
                "permits",
                "Permitting and environmental review for the shared stretch done once",
                0.25 * acres * PERMIT_PER_ACRE,
                0.5 * acres * PERMIT_PER_ACRE,
                "USD",
                f"Both are work on existing corridors (no new land to buy); {shared_mi:.1f} mi run within 1.6 km, a quarter to half of the regulatory and permitting cost (${PERMIT_PER_ACRE:,}/acre) shared",
                "miso24",
            )
        if shared_mi >= 0.05:
            r_lo, r_hi = (v * ESCALATE_2018 for v in ROAD_PER_MILE_2018)
            item(
                "access_roads",
                "Access roads built once along the shared stretch",
                0.5 * shared_mi * r_lo,
                shared_mi * r_hi,
                "USD",
                f"{shared_mi:.1f} mi x ${r_lo:,.0f}-${r_hi:,.0f} per mile (level gravel to forested, MISO 2018 x {ESCALATE_2018}); half to all of it reused",
                "miso18",
            )
        else:
            item(
                "access_roads",
                "One access road into the shared area",
                0.1 * SUB_ROAD_PER_MILE,
                0.5 * SUB_ROAD_PER_MILE,
                "USD",
                f"0.1-0.5 mi of access road not built twice (MISO substation access road ${SUB_ROAD_PER_MILE:,} per mile)",
                "miso24",
            )

    if tier == "touching":
        kvs = [k for k in (_main_kv(pa), _main_kv(pb)) if k]
        kv = min(kvs) if kvs else 115
        unit = _nearest(DEADEND_MTEP24, kv)
        if rec["crosses"]:
            label = "Crossing structures designed and modified once"
            basis = (f"1-2 angled dead-end steel poles at {kv} kV (${unit:,} each, MISO MTEP24 Table 2.2-1) where the lines cross, "
                     "not reworked a second time; one outage plan for the crossing")
        else:
            label = "Structures where the projects meet, designed once"
            basis = (f"The projects meet at the same place (they don't cross): 1-2 angled dead-end steel poles at {kv} kV "
                     f"(${unit:,} each, MISO MTEP24 Table 2.2-1) at the shared terminal, designed and outaged once")
        item("crossing", label, unit, 2 * unit, "USD", basis, "miso24")

    usd = [it for it in items if it["unit"] == "USD"]
    assumptions += [
        "Straight lines between located endpoints stand in for the real routes, which are longer (MISO adds 30 % for routing).",
        "MISO's unit costs are a public national yardstick; Georgia and South Carolina are outside MISO, so local costs differ.",
        f"MISO 2018 unit costs are scaled by {ESCALATE_2018}, how much the guide's own like-for-like costs (forested clearing, wetland matting, mitigation credits) rose by MTEP24.",
        f"Land: USDA's 2026 average pasture value (GA ${PASTURE_2026['GA']:,}, SC ${PASTURE_2026['SC']:,} per acre) at the low end, MISO's cropland multiple (3x pasture) at the high end.",
        "Savings that need both crews in the field at once (mobilization, laydown yard) start at $0 when the build windows don't overlap.",
        "Georgia's project costs are redacted in the public filing; the project scale shown for them uses MISO exploratory costs per mile.",
        "A planning-level range, not a bid and not a statement about what either utility plans to do.",
    ]
    if tl["windows"][0] and tl["windows"][1]:
        assumptions.append(
            f"Build windows: {pa['id']} {tl['windows'][0][0]:%b %Y}-{tl['windows'][0][1]:%b %Y} ({tl['windows'][0][2]}); "
            f"{pb['id']} {tl['windows'][1][0]:%b %Y}-{tl['windows'][1][1]:%b %Y} ({tl['windows'][1][2]})."
        )
    context = [_project_scale(st, pa, ga), _project_scale(st, pb, gb)]
    if any(c.get("source") for c in context):
        used.add("miso24")
    return {
        "overlap_id": rec["id"],
        "a": pa["id"],
        "b": pb["id"],
        "tier": tier,
        "tier_label": rec["tier_label"],
        "distance_km": rec["distance_km"],
        "shared_km": round(shared_km, 2),
        "same_window": tl["same_window"],
        "label": "Rough estimate",
        "items": items,
        "total_low": sum(it["low"] for it in usd),
        "total_high": sum(it["high"] for it in usd),
        "unit": "USD",
        "context": context,
        "assumptions": assumptions,
        "sources": [SOURCES[k] for k in SOURCES if k in used],
    }


# ----------------------------------------------------------------------------- Sperry check


def sperry_check(example: dict) -> dict:
    """Reproduce Sperry's worked example with their own method: center = mean of the located
    endpoints, haversine between centers (miles), overlap if under 25 miles, days between dates."""
    projects = {p["project_id"]: p for p in example["projects"]}
    info, notes = {}, []
    for pid, p in projects.items():
        pts = [(p[f"lat_{k}"], p[f"lon_{k}"]) for k in "ab" if p.get(f"lat_{k}") is not None and p.get(f"lon_{k}") is not None]
        center = (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)) if pts else None
        when, how = parse_sperry_date(p["in_service_date_raw"])
        if how == "excel_serial":
            notes.append(f"{pid}: in-service date stored as Excel serial {p['in_service_date_raw']}, normalized to {when.isoformat()}")
        if center and (abs(center[0] - p["lat_center"]) > 1e-6 or abs(center[1] - p["lon_center"]) > 1e-6):
            notes.append(f"{pid}: our center {center} differs from the sheet's ({p['lat_center']}, {p['lon_center']})")
        info[pid] = (p, center, when)

    rows = []
    for o in example["overlaps"]:
        (pa, ca, da), (pb, cb, db) = info[o["project_id_a"]], info[o["project_id_b"]]
        ours_mi = round(_haversine_mi(ca, cb), 2) if ca and cb else None
        ours_days = abs((da - db).days) if da and db else None
        ok = ours_mi is not None and abs(ours_mi - o["distance_mi"]) <= 0.01 + 1e-9 and ours_days == o["time_gap_days"]
        rows.append(
            {
                "overlap_id": o["overlap_id"],
                "a": o["project_id_a"],
                "b": o["project_id_b"],
                "a_name": pa["project_name"],
                "b_name": pb["project_name"],
                "expected_mi": o["distance_mi"],
                "ours_mi": ours_mi,
                "expected_days": o["time_gap_days"],
                "ours_days": ours_days,
                "ok": bool(ok),
            }
        )
    # the flagged set must match too: every cross-utility pair under 25 miles, nothing more
    expected = {frozenset((o["project_id_a"], o["project_id_b"])) for o in example["overlaps"]}
    ours, compared = set(), 0
    ids = list(info)
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            (p1, c1, _), (p2, c2, _) = info[ids[x]], info[ids[y]]
            if p1["utility"] == p2["utility"] or not c1 or not c2:
                continue
            compared += 1
            if _haversine_mi(c1, c2) < 25:
                ours.add(frozenset((ids[x], ids[y])))
    extra = sorted("~".join(sorted(s)) for s in ours - expected)
    missing = sorted("~".join(sorted(s)) for s in expected - ours)
    all_ok = all(r["ok"] for r in rows) and not extra and not missing
    return {
        "method": "center = mean of located endpoints; haversine (R = 3958.8 mi); overlap if under 25 miles; days between in-service dates",
        "rows": rows,
        "pairs_compared": compared,
        "flagged": len(ours),
        "extra": extra,
        "missing": missing,
        "reproduced": sum(r["ok"] for r in rows),
        "all_ok": all_ok,
        "data_quality": notes,
    }


# ----------------------------------------------------------------------------- routes


@router.get("/api/gridlock/summary")
def summary():
    st = _load()
    doc = st["doc"]
    report = dict(doc.get("report") or _fallback_report(st))
    if not report.get("sperry_example"):
        chk = sperry_check(st["example"])
        report["sperry_example"] = {"reproduced": chk["all_ok"], "matched": chk["reproduced"], "rows": chk["rows"], "live": True}
    return {
        "built_at": doc.get("built_at"),
        "fallback": st["fallback"],
        "fallback_reason": st["fallback_reason"],
        "sources": doc.get("sources") or [],
        "counts": _counts(st),
        "compared_projects": len(st["placed"]),
        "report": report,
        "rebuild_command": "backend/venv/Scripts/python backend/demo/gridlock/build.py",
    }


def _fast_json(payload: dict) -> Response:
    """Plain json.dumps: several times faster than FastAPI's encoder on a thousand overlap rows."""
    return Response(content=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), media_type="application/json")


def _json_bytes(st: dict, key: str, build) -> Response:
    """Large, unchanging payloads are serialized once per data version, not per request."""
    if st.get(key) is None:
        st[key] = json.dumps(build(), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return Response(content=st[key], media_type="application/json")


@router.get("/api/gridlock/projects")
def projects():
    st = _load()
    return _json_bytes(
        st, "projects_bytes", lambda: {"fallback": st["fallback"], "projects": st["projects"], "quarantine": st["quarantine"]}
    )


@router.get("/api/gridlock/projects/{project_id}")
def project_detail(project_id: str):
    st = _load()
    p = st["by_id"].get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="No project with that id")
    prm = _params(MAX_KM_DEFAULT, WINDOW_DEFAULT, "closest", "all", "all")
    flagged = [o for o in _compute(st, prm)["overlaps"] if project_id in (o["a"], o["b"])]
    return {
        "project": p,
        "overlaps": [{k: o[k] for k in ("id", "a", "b", "distance_km", "tier", "tier_label", "score")} for o in flagged[:50]],
    }


@router.get("/api/gridlock/basemap")
def basemap():
    st = _load()
    return _json_bytes(st, "basemap_bytes", lambda: _basemap(st))


def _basemap(st: dict) -> dict:
    if st["basemap"] is None:
        bm = None
        if BASEMAP_FILE.exists():
            try:
                bm = _read_json(BASEMAP_FILE)
            except (OSError, ValueError):
                bm = None
        if not isinstance(bm, dict):
            coords = [c for g in st["geoms"] for c in g]
            if coords:
                lons, lats = [c[0] for c in coords], [c[1] for c in coords]
                bbox = [min(lons) - 0.5, min(lats) - 0.5, max(lons) + 0.5, max(lats) + 0.5]
            else:
                bbox = [-85.7, 30.3, -78.5, 35.3]  # SC + GA
            bm = {"states": {}, "lines": [], "bbox": bbox, "fallback": True}
        st["basemap"] = bm
    return st["basemap"]


@router.get("/api/gridlock/overlaps")
def overlaps(
    max_km: float = Query(MAX_KM_DEFAULT),
    window_months: int = Query(WINDOW_DEFAULT),
    method: str = Query("closest"),
    a: str = Query("DESC"),
    b: str = Query("GPC"),
    limit: int = Query(OVERLAP_LIMIT_DEFAULT),
):
    prm = _params(max_km, window_months, method, a, b)
    if not (1 <= limit <= OVERLAP_LIMIT_MAX):
        raise HTTPException(status_code=422, detail=f"limit must be between 1 and {OVERLAP_LIMIT_MAX}")
    st = _load()
    res = _compute(st, prm)
    rows = res["overlaps"]
    tiers = {t[0]: sum(1 for r in rows if r["tier"] == t[0]) for t in TIERS}
    chosen = set(prm["a"]) | set(prm["b"])
    in_play = [p for p in st["placed"] if p["utility"] in chosen]
    n_assumed = sum(1 for p in in_play if _window_assumed(p))
    return _fast_json({
        "params": prm,
        "fallback": st["fallback"],
        "total_pairs": res["total_pairs"],
        "flagged": len(rows),
        "by_tier": tiers,
        "tiers": [{"id": t[0], "label": t[4], "under_km": t[1], "what": t[5]} for t in TIERS],
        "window_rule": (
            f"A project's build window is the one its filing supports (a filed start date, or DESC's yearly spending); "
            f"for the {n_assumed} of {len(in_play)} projects here without one, the {window_months} months before in-service"
        ),
        "window_assumed": {"projects": n_assumed, "of": len(in_play)},
        "truncated": len(rows) > limit,
        "overlaps": rows[:limit],
    })


@router.get("/api/gridlock/opportunities")
def opportunities(
    limit: int = Query(OPP_LIMIT_DEFAULT),
    max_km: float = Query(MAX_KM_DEFAULT),
    window_months: int = Query(WINDOW_DEFAULT),
    method: str = Query("closest"),
    a: str = Query("DESC"),
    b: str = Query("GPC"),
):
    if not (1 <= limit <= OPP_LIMIT_MAX):
        raise HTTPException(status_code=422, detail=f"limit must be between 1 and {OPP_LIMIT_MAX}")
    prm = _params(max_km, window_months, method, a, b)
    st = _load()
    res = _compute(st, prm)
    out = []
    for o in res["overlaps"][:limit]:
        out.append({**o, "project_a": _slim(st["by_id"][o["a"]]), "project_b": _slim(st["by_id"][o["b"]])})
    return _fast_json({"params": prm, "fallback": st["fallback"], "flagged": len(res["overlaps"]), "opportunities": out})


@router.get("/api/gridlock/estimate/{overlap_id}")
def estimate(overlap_id: str, window_months: int = Query(WINDOW_DEFAULT)):
    if not (0 <= window_months <= WINDOW_MAX):
        raise HTTPException(status_code=422, detail=f"window_months must be between 0 and {WINDOW_MAX}")
    st = _load()
    parts = overlap_id.split("~")
    if len(parts) != 2 or parts[0] not in st["index"] or parts[1] not in st["index"]:
        raise HTTPException(status_code=404, detail="No overlap with that id")
    i, j = st["index"][parts[0]], st["index"][parts[1]]
    if st["util"][i] == st["util"][j] or st["closest_km"][i, j] > MAX_KM_CAP:
        raise HTTPException(status_code=404, detail=f"Those two projects aren't a cross-utility pair within {MAX_KM_CAP:g} km")
    return _estimate(st, i, j, window_months)


@router.get("/api/gridlock/sperry-check")
def sperry_check_route():
    st = _load()
    out = sperry_check(st["example"])
    # and where each of their pairs lands in OUR comparison of the full filings (default settings)
    prm = _params(MAX_KM_DEFAULT, WINDOW_DEFAULT, "closest", "DESC", "GPC")
    by_pair = {frozenset((o["a"], o["b"])): o for o in _compute(st, prm)["overlaps"]}
    ids = {ovl: pair for pair, ovl in st["sperry_pairs"].items()}
    for r in out["rows"]:
        pair = ids.get(r["overlap_id"])
        o = by_pair.get(pair) if pair else None
        r["ours"] = (
            {"id": o["id"], "rank": o["rank"], "distance_km": o["distance_km"], "tier": o["tier"], "tier_label": o["tier_label"], "score": o["score"]}
            if o
            else ({"id": "~".join(sorted(pair)), "rank": None} if pair else None)
        )
    out["found_in_filings"] = {
        "flagged": sum(1 for r in out["rows"] if r["ours"] and r["ours"].get("rank")),
        "of": len(out["rows"]),
        "settings": f"closest points within {MAX_KM_DEFAULT:g} km, DESC x Georgia Power",
    }
    return out
