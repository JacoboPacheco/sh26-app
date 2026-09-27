"""Build plans (GridLock): neighboring utilities' public construction plans, located, validated,
and compared for coordination opportunities.

This module is the ENGINE + API. The data pipeline (backend/demo/gridlock/build.py) writes
data/projects.json and data/basemap.json from the public filings; this module loads them once
(and again whenever the file changes on disk), precomputes every pair's distances, and serves:

  GET /api/gridlock/summary               counts per utility, the pipeline funnel (filings -> rows -> passed / set aside) + its report
  GET /api/gridlock/projects              every located project (map payload) + the quarantine
  GET /api/gridlock/projects/{id}         one project in full, with its flagged overlaps
  GET /api/gridlock/basemap               SC + GA outlines and OSM transmission lines (context)
  GET /api/gridlock/overlaps              ranked cross-utility pairs within max_km
  GET /api/gridlock/opportunities         the top overlaps with both projects inlined
  GET /api/gridlock/estimate/{overlap_id} a rough, sourced low-high estimate of what a pair could share
  GET /api/gridlock/sperry-check          Sperry's worked example reproduced live (center method), their ten projects matched to ours
  GET /api/gridlock/trace/{overlap_id}    one pair taken apart: rank, the score's terms, distance, each endpoint's match, provenance
  GET /api/gridlock/calendar              the coordination calendar: each project's build window (as filed / start derived)
                                          and each flagged pair's shared window, from the same windows /overlaps scores
  GET /api/gridlock/calendar.ics          that calendar as iCalendar (RFC 5545) for a planner's calendar (?pair=<id>: one pair)
  GET /api/gridlock/export.xlsx           every project + flagged overlap in Sperry's own table format (their columns first),
                                          plus a calendar sheet
  GET /api/gridlock/export.csv            one of those tables as CSV (?table=projects|overlaps|set_aside|calendar)
  GET /api/gridlock/export.geojson        every validated project as a GeoJSON feature
  GET /api/gridlock/fault-test            the fault-injection report (demo/gridlock/faults.py): bad records caught, by check
  GET /api/gridlock/changes               what changed since DESC's last filing (2026-2030 vs 2024-2028), both pages per row

DESC's CURRENT list is its 2026-2030 filing (see _merge_current): the ranking reads it, with the 2024-2028 projects it no
longer carries kept and marked; _load(AS_FILED) is projects.json exactly as the build wrote it. The list is grouped so what
can still be built together comes first (GROUPS, rank_key); the default cutoff is Sperry's 25 mi exactly (40.2336 km).

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

Same station (a class ranked ABOVE every distance tier). When an endpoint of one project and an endpoint
of the other are the same substation, the two utilities physically meet there (a tie line, a shared
station), so the pair is listed first whatever its score: the sort key is (same station first, then score).
The score itself is unchanged and still orders pairs inside each class. How a shared station is found:
  osm_feature      both endpoints resolve to the same OpenStreetMap feature (type + id). An endpoint the
                   pipeline placed by proxy (no OSM substation carries its name, so the nearest unnamed one
                   stands in) counts only when the two filed names also agree.
  name_within_1km  only when an endpoint has no OSM id (Sperry's worked example): the same station name once
                   qualifiers and generic words are dropped ('THURMOND DAM #5' ~ 'Thurmond Sub'), within 1 km.
The pair's `tier` stays its distance tier (other modules read it); `class` / `class_label` say "same_station"
and `shared_station` names the station, its OSM link and which endpoint of each project it is.

Honesty rules (CLAUDE.md -> Decisions): real public filings; never say whether utilities are or
aren't coordinating ("could coordinate"); estimates are low-high ranges with their basis, sources
and assumptions, never a single falsely precise number.
"""

import csv
import io
import json
import math
import os
import re
import threading
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from limiter import limiter

router = APIRouter(tags=["gridlock"])

HERE = Path(__file__).parent / "demo" / "gridlock"
DATA_DIR = Path(os.getenv("GRIDLOCK_DATA_DIR") or HERE / "data")  # override only for tests
PROJECTS_FILE = DATA_DIR / "projects.json"
BASEMAP_FILE = DATA_DIR / "basemap.json"
SPERRY_FILE = HERE / "sperry_example.json"
# DESC's next edition (2026-2030), read, checked and located by demo/gridlock/diff_filings.py, and its comparison with the
# 2024-2028 list: the app ranks the 2026-2030 list as DESC's CURRENT plan (see _merge_current)
DESC_CURRENT_FILE = DATA_DIR / "desc_2026_2030.json"
DESC_CHANGES_FILE = DATA_DIR / "desc_changes.json"
CURRENT = "current"  # _load(): Georgia + DESC 2026-2030 + the 2024-2028 projects the new list no longer carries
AS_FILED = "as_filed"  # _load(): projects.json exactly as the build wrote it (DESC 2024-2028 + Georgia)

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

# Sperry's cutoff is 25 miles: exactly 40.2336 km (not the 40 km that used to stand in for it, 24.85 mi)
SPERRY_MI = 25
MAX_KM_DEFAULT, MAX_KM_CAP = SPERRY_MI * KM_PER_MI, 50.0


def limit_text(max_km: float) -> str:
    """The distance limit in words: Sperry's '25 mi (40.2 km)', else '30 km (18.6 mi)'."""
    mi = max_km / KM_PER_MI
    if abs(mi - round(mi)) < 1e-9:
        return f"{round(mi)} mi ({max_km:.1f} km)"
    return f"{max_km:g} km ({mi:.1f} mi)"
WINDOW_DEFAULT, WINDOW_MAX = 24, 120
OVERLAP_LIMIT_DEFAULT, OVERLAP_LIMIT_MAX = 500, 2000
OPP_LIMIT_DEFAULT, OPP_LIMIT_MAX = 10, 50
CONF_FACTOR = {"high": 1.0, "medium": 0.8, "low": 0.5}
CONF_RANK = {"high": 3, "medium": 2, "low": 1}
KV_CLASSES = (46, 69, 115, 138, 161, 230, 345, 500)
LINE_KINDS = {"new_line", "line_rebuild", "reconductor", "tap"}
SAME_KV_BONUS = 1.1
SAME_STATION = ("same_station", "Same station", "both filings work at the same substation")
SAME_STATION_NAME_KM = 1.0  # the name rule's radius, used only when an endpoint has no OSM id
# short names for the reason lines (agreement.py's SHORT, kept here so gridlock doesn't import it)
UTILITY_SHORT = {"DESC": "DESC", "GPC": "Georgia Power", "GTC": "GTC", "MEAG": "MEAG Power", "DU": "Dalton Utilities"}
# words that don't tell two stations apart, dropped at either end of a name (the pipeline's locate.GENERIC)
_STATION_GENERIC = sorted(
    [
        "COMBINED CYCLE FACILITY", "ENERGY FACILITY", "ENERGY CENTER", "ELECTRIC GENERATING PLANT", "GENERATING PLANT",
        "GENERATING STATION", "POWER PLANT", "POWER STATION", "STEAM PLANT", "NUCLEAR PLANT", "NUCLEAR STATION",
        "HYDROELECTRIC STATION", "HYDROELECTRIC PLANT", "HYDROELECTRIC", "HYDRO", "PLANT", "STATION", "DAM", "TIE",
        "TAP", "SWITCHING", "PRIMARY", "NEW", "TRANSMISSION", "DISTRIBUTION", "SUBSTATION", "GEORGIA", "SOUTH CAROLINA",
    ],
    key=len,
    reverse=True,
)

_lock = threading.Lock()
# {edition: state}, each replaced whole on reload, so a request mid-reload keeps a consistent view
_states: dict = {}


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


def _sperry_ids(doc: dict, example: dict, fallback: bool) -> dict:
    """{Sperry's project id: ours}, through the pipeline's own match of their projects to ours
    (report.sperry_example.projects); ids are theirs in the fallback."""
    if fallback:
        return {p["project_id"]: p["project_id"] for p in example["projects"]}
    rep = (doc.get("report") or {}).get("sperry_example") or {}
    return {m.get("sperry_id"): m.get("our_id") for m in rep.get("projects") or [] if isinstance(m, dict) and m.get("our_id")}


def _sperry_pairs(doc: dict, example: dict, fallback: bool) -> dict:
    """{frozenset(our id a, our id b): 'OVL_n'} for Sperry's six known overlaps."""
    ours = _sperry_ids(doc, example, fallback)
    out = {}
    for o in example["overlaps"]:
        a, b = ours.get(o["project_id_a"]), ours.get(o["project_id_b"])
        if a and b:
            out[frozenset((a, b))] = o["overlap_id"]
    return out


# ----------------------------------------------------------------------------- DESC's current list (2026-2030)
#
# DESC published its 2026-2030 list after the 2024-2028 one Sperry's starter package (and their worked example) is built
# from. demo/gridlock/diff_filings.py reads it with the build's own code (extract, normalize, locate from the cached OSM,
# the checks, 3 more rules) and links it to the older list (desc_changes.json: carried over, dropped, new). The app ranks
# DESC's CURRENT plan: every 2026-2030 record (kept or set aside, as its checks decided), and, from the 2024-2028 list,
# only the projects the new list no longer carries (12, 11 of them in service before 2026 as filed; Sperry's example is
# built from four of them), each marked with its edition. A project in both lists is read from the 2026-2030 one only.
# Georgia's records are the build's, unchanged. The build's projects.json is never rewritten here.

EDITION_NEW, EDITION_OLD = "2026-2030", "2024-2028"
SOURCE_SHORT = {
    "desc_2026": "DESC 2026–2030 project list",
    "desc": "DESC 2024–2028 project list",
    "ga_irp": "Georgia Power 2025 IRP, Vol. 3 (public disclosure)",
    "sperry_example": "Sperry's worked example",
}
GA_PUBLIC_NOTE = (
    "The public-disclosure copy filed with the Georgia PSC: every page carries a CEII banner because it is the public version "
    "of a CEII document. Only its unredacted fields are used; redacted fields (such as costs) are left blank, never inferred, "
    "and no CEII-marked content is used."
)


def _page_of(r: dict):
    return (r.get("provenance") or {}).get("page")


def _merge_current(doc: dict, cur: dict, changes: dict) -> dict:
    """projects.json + the 2026-2030 edition -> DESC's current list with Georgia (see the section comment)."""
    by_new, by_old = {}, {}
    for r in changes.get("rows") or []:
        if isinstance(r.get("new"), dict):
            by_new[r["new"].get("page")] = r
        if isinstance(r.get("old"), dict):
            by_old[r["old"].get("page")] = r

    def is_old_desc(r):
        return (r.get("provenance") or {}).get("source") == "desc"

    def tag_old(r):
        row = by_old.get(_page_of(r)) or {}
        before = bool(row.get("in_service_before_the_new_list"))
        return {
            **r,
            "edition": EDITION_OLD,
            "edition_note": "In DESC's 2024–2028 list only; the 2026–2030 list no longer carries it"
            + ("; its in-service date, as filed, was before 2026" if before else ""),
        }

    def tag_new(r):
        row = by_new.get(_page_of(r)) or {}
        change = None
        if row.get("change") == "carried_over":
            old = row.get("old") or {}
            ins, cost = row.get("in_service") or {}, row.get("cost") or {}
            change = {
                "kind": "carried_over",
                "linked_by": row.get("linked_by"),
                "old_page": old.get("page"),
                "old_project_id": old.get("project_id"),
                "old_name": old.get("name"),
                "in_service": {k: ins.get(k) for k in ("old_raw", "new_raw", "changed", "months", "direction")} if ins else None,
                "cost": {k: cost.get(k) for k in ("old", "new", "changed", "delta", "pct", "direction")} if cost else None,
            }
            note = f"In DESC's 2026–2030 list; also in its 2024–2028 list (p. {old.get('page')})"
            if ins.get("changed") and ins.get("old_raw"):
                note += f", which gave the in-service date as {ins.get('old_raw')}"
        elif row.get("change") == "new":
            change = {"kind": "new"}
            note = "New in DESC's 2026–2030 list (not in the 2024–2028 list)"
        else:
            note = "In DESC's 2026–2030 list"
        return {**r, "edition": EDITION_NEW, "edition_note": note, "edition_change": change}

    dropped_pages = {p for p, r in by_old.items() if r.get("change") == "dropped"}
    new_p = [tag_new(p) for p in cur.get("projects") or [] if isinstance(p, dict)]
    new_q = [tag_new(q) for q in cur.get("set_aside") or [] if isinstance(q, dict)]
    taken = {r.get("id") for r in new_p + new_q}
    old_p = [tag_old(p) for p in doc.get("projects") or [] if is_old_desc(p) and _page_of(p) in dropped_pages and p.get("id") not in taken]
    old_q = [tag_old(q) for q in doc.get("quarantine") or [] if is_old_desc(q) and _page_of(q) in dropped_pages and q.get("id") not in taken]
    ga_p = [p for p in doc.get("projects") or [] if not is_old_desc(p)]
    ga_q = [q for q in doc.get("quarantine") or [] if not is_old_desc(q)]
    projects, quarantine = new_p + old_p + ga_p, new_q + old_q + ga_q

    src_new = {**(cur.get("source") or {}), "id": "desc_2026"}
    by_sid = {s.get("id"): s for s in doc.get("sources") or [] if isinstance(s, dict)}
    sources = [
        {**src_new, "short_title": SOURCE_SHORT["desc_2026"], "role": "current",
         "role_text": f"DESC's current list: {len(new_p) + len(new_q)} projects read, {len(new_p)} passed the checks"},
        {**by_sid.get("ga_irp", {}), "short_title": SOURCE_SHORT["ga_irp"], "role": "current", "public_note": GA_PUBLIC_NOTE,
         "role_text": "Georgia Power and the other Georgia ITS sponsors"},
        {**by_sid.get("desc", {}), "short_title": SOURCE_SHORT["desc"], "role": "earlier",
         "role_text": (f"DESC's earlier list: only the {len(old_p) + len(old_q)} projects the 2026–2030 list no longer carries "
                       "are compared (Sperry's worked example is built from this edition)")},
    ]
    counts = changes.get("counts") or {}
    edition = {
        "desc_current": EDITION_NEW,
        "desc_earlier": EDITION_OLD,
        "new_list": {"read": len(new_p) + len(new_q), "passed": len(new_p), "set_aside": len(new_q)},
        "earlier_kept": {"read": len(old_p) + len(old_q), "passed": len(old_p), "set_aside": len(old_q)},
        "carried_over": counts.get("carried_over"),
        "dropped": counts.get("dropped"),
        "new": counts.get("new"),
        "rule": ("DESC's current list is its 2026–2030 filing; the 2024–2028 projects it no longer carries stay in the comparison, "
                 "marked, and a project in both lists is read from the 2026–2030 one"),
    }
    out = {**doc, "projects": projects, "quarantine": quarantine, "sources": sources, "edition": edition}
    out["report"] = _merged_report(doc.get("report") or {}, cur, projects, quarantine, len(old_p) + len(old_q), edition)
    return out


def _merged_report(rep: dict, cur: dict, projects: list, quarantine: list, n_old: int, edition: dict) -> dict:
    """The build's report re-counted over the records the app compares (every number from those records), with the
    2026-2030 edition's own extraction stage; the build's Sperry reproduction, sources and self-test are kept."""
    rep = dict(rep)
    records = projects + quarantine
    x = cur.get("extraction") or {}
    sr = x.get("second_reader") or {}
    manual = (cur.get("manual_spot_check") or {}).get("pages") or []
    by_id = {s.get("id"): s for s in rep.get("stages") or [] if isinstance(s, dict)}
    located = sum(1 for r in records if any(isinstance(e, dict) and e.get("lat") is not None for e in r.get("endpoints") or []))
    warned = sum(1 for p in projects if any(isinstance(c, dict) and c.get("status") == "warn" for c in p.get("checks") or []))
    old_x = (rep.get("extraction") or {})
    rules = (cur.get("checks") or {}).get("rules") or []
    stages = []
    if "selftest" in by_id:
        stages.append(by_id["selftest"])
    stages.append({
        "id": "extract_desc_2026", "label": "DESC 2026–2030: one project per page", "in": x.get("pages"), "out": x.get("projects"),
        "note": (f"{x.get('parsed_cleanly')} pages parsed cleanly, {len(x.get('anomalies') or [])} data anomalies recorded; a second PDF reader "
                 f"found {sr.get('all_found')} of {sr.get('pages_checked')}; {sum(1 for m in manual if m.get('same'))} of {len(manual)} "
                 "pages read by eye match"),
    })
    if "extract_ga" in by_id:
        stages.append(by_id["extract_ga"])
    stages.append({
        "id": "extract_desc", "label": "DESC 2024–2028: the projects the 2026–2030 list no longer carries",
        "in": old_x.get("desc_projects") or old_x.get("desc_pages"), "out": n_old,
        "note": (f"{edition.get('carried_over')} are also in the 2026–2030 list and are read from there; the {n_old} it no longer "
                 "carries stay, marked, so finished work and Sperry's worked example remain comparable"),
    })
    stages.append({"id": "normalize", "label": "dates, kV, kind, endpoint names, build windows", "in": len(records), "out": len(records),
                   "note": "the same code for all three lists"})
    stages.append({"id": "locate", "label": "OSM name match -> Nominatim for leftovers", "in": len(records), "out": located,
                   "note": (by_id.get("locate") or {}).get("note")})
    stages.append({"id": "checks", "label": f"{len(rules) or 16} named rules", "in": len(records), "out": len(projects),
                   "note": f"{len(quarantine)} set aside with reasons, {warned} kept with warnings"})
    rep["stages"] = stages
    # every check counted over these records (each carries its own results); the 3 rules added for the 2026-2030 edition
    # were also run over the 2024-2028 and Georgia records by diff_filings.py, which recorded who failed or warned
    extra = (cur.get("checks") or {}).get("new_rules_on_2024_2028_and_georgia") or {}
    labels = {c.get("id"): c for c in (rep.get("checks") or []) if isinstance(c, dict)}
    out = []
    for rule in rules or list(labels.values()):
        rid = rule.get("id")
        n = {"pass": 0, "warn": 0, "fail": 0}
        for r in records:
            st = next((c.get("status") for c in r.get("checks") or [] if isinstance(c, dict) and c.get("id") == rid), None)
            if st is None and rid in extra:
                e = extra[rid]
                st = "fail" if r.get("id") in (e.get("failed") or []) else "warn" if r.get("id") in (e.get("warned") or []) else "pass"
            if st in n:
                n[st] += 1
        out.append({"id": rid, "label": rule.get("label") or labels.get(rid, {}).get("label") or rid,
                    "blocking": bool(rule.get("blocking", labels.get(rid, {}).get("blocking"))),
                    "passed": n["pass"], "warned": n["warn"], "failed": n["fail"],
                    "new_for_2026_2030": bool(rule.get("new_for_this_filing"))})
    rep["checks"] = out
    rep["extraction"] = {**old_x, "desc_2026_pages": x.get("pages"), "desc_2026_projects": x.get("projects"),
                         "desc_2026_anomalies": len(x.get("anomalies") or []), "desc_2026_second_reader": sr}
    rep["coverage"] = None  # per utility: /summary counts (from the same records)
    return rep


def _load(edition: str = CURRENT) -> dict:
    """The data for `edition` (CURRENT: DESC's 2026-2030 list with Georgia, see _merge_current; AS_FILED: projects.json
    as the build wrote it), reloaded whenever one of its files changes on disk."""
    merge = edition == CURRENT
    key = (_file_key(PROJECTS_FILE), _file_key(BASEMAP_FILE), _file_key(SPERRY_FILE),
           _file_key(DESC_CURRENT_FILE) if merge else None, _file_key(DESC_CHANGES_FILE) if merge else None)
    have = _states.get(edition)
    if have is not None and have.get("key") == key:
        return have
    with _lock:
        have = _states.get(edition)
        if have is not None and have.get("key") == key:
            return have
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
        elif merge and key[3] is not None and key[4] is not None:
            try:
                cur, changes = _read_json(DESC_CURRENT_FILE), _read_json(DESC_CHANGES_FILE)
                if isinstance(cur, dict) and isinstance(cur.get("projects"), list) and isinstance(changes, dict):
                    doc = _merge_current(doc, cur, changes)
            except (OSError, ValueError):
                pass  # the build's list alone (it says so: no `edition` in the summary)
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
            "sperry_ids": _sperry_ids(doc, example, fallback_reason is not None),
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
            "stations": [_stations_of(p) for p in placed],
            "line": np.array([p.get("kind") in LINE_KINDS for p in placed], dtype=bool),
            "cache": {},
            "basemap": None,
            "edition": edition,
        }
        _states[edition] = new_state
        return new_state


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


# ----------------------------------------------------------------------------- same station


def station_core(name) -> str:
    """A station name reduced to what tells it apart: qualifiers ('(USA)', '#5'), punctuation and 'Sub' dropped,
    'Pri' spelled out, generic words (Dam, Primary, Plant, ...) removed at either end. 'THURMOND DAM (USA) #5',
    'Thurmond Dam' and 'Thurmond Sub' all give 'THURMOND'."""
    s = re.sub(r"\([^)]*\)|#\s*\d+\w*", " ", str(name or "")).upper()
    s = re.sub(r"[.'’]", "", s)
    s = re.sub(r"\b(SUBSTATION|SUB|SWITCHING STATION|SWITCHYARD)\b", " ", s)
    s = re.sub(r"\bPRI\b", "PRIMARY", s)
    s = re.sub(r"\bFT\b", "FORT", s)
    s = re.sub(r"\bMT\b", "MOUNT", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"BOROUGH\b", "BORO", re.sub(r"\s+", " ", s).strip())
    s = f" {s} "
    changed = True
    while changed:
        changed = False
        for g in _STATION_GENERIC:
            for pat in (rf"^ {g} ", rf" {g} $"):
                t = re.sub(pat, " ", s)
                if t != s and t.strip():
                    s, changed = t, True
    return s.strip()


def _station_display(name) -> str:
    """A filed station name for a sentence: qualifiers and a trailing 'Sub' dropped, capitals title-cased
    ('THURMOND DAM #5' -> 'Thurmond Dam', 'Thurmond Sub' -> 'Thurmond')."""
    s = re.sub(r"\([^)]*\)|#\s*\d+\w*", " ", str(name or ""))
    s = re.sub(r"\s+(sub|substation)\s*$", "", re.sub(r"\s+", " ", s).strip(), flags=re.I)
    return s.title() if s.isupper() else s


def _stations_of(p: dict) -> list[dict]:
    """The project's located endpoints as stations: which endpoint, the filed name, the OSM feature (if any) and
    whether the pipeline placed it by proxy (no OSM substation carries its name; a nearby one stands in)."""
    out = []
    for k, e in enumerate(p.get("endpoints") or []):
        if not isinstance(e, dict) or e.get("lat") is None or e.get("lon") is None:
            continue
        osm = e.get("osm") if isinstance(e.get("osm"), dict) else {}
        key = f"{osm['type']}/{osm['id']}" if osm.get("type") and osm.get("id") is not None else None
        url = osm.get("url") or (f"https://www.openstreetmap.org/{key}" if key else None)
        out.append(
            {
                "index": k,
                "name": e.get("name") or e.get("raw"),
                "core": station_core(e.get("name") or e.get("raw")),
                "lat": float(e["lat"]),
                "lon": float(e["lon"]),
                "osm_key": key,
                "osm_url": url,
                "osm_name": osm.get("name"),
                "proxy": str(e.get("match") or "").startswith("no OSM substation is named"),
                "confidence": e.get("confidence"),
            }
        )
    return out


def _km_between(a: dict, b: dict) -> float:
    return _haversine_mi((a["lat"], a["lon"]), (b["lat"], b["lon"])) * KM_PER_MI


def _shared_stations(sa: list[dict], sb: list[dict]) -> list[dict]:
    """Every station an endpoint of project a shares with an endpoint of project b (see the module docstring)."""
    out, seen = [], set()
    for ea in sa:
        for eb in sb:
            names_agree = bool(ea["core"]) and ea["core"] == eb["core"]
            if ea["osm_key"] and eb["osm_key"]:
                if ea["osm_key"] != eb["osm_key"] or ((ea["proxy"] or eb["proxy"]) and not names_agree):
                    continue
                rule = "osm_feature"
            elif names_agree and _km_between(ea, eb) <= SAME_STATION_NAME_KM:
                rule = "name_within_1km"
            else:
                continue
            key = ea["osm_key"] if rule == "osm_feature" else f"name:{ea['core']}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"a": ea, "b": eb, "rule": rule})
    return out


def _months_apart(d1: date, d2: date) -> int:
    return round(abs((d2 - d1).days) / 30.4375)


def _years_months(months: int) -> str:
    """'8 years 5 months', '11 months', '2 years'."""
    y, m = divmod(months, 12)
    parts = ([f"{y} year{'s' if y != 1 else ''}"] if y else []) + ([f"{m} month{'s' if m != 1 else ''}"] if m else [])
    return " ".join(parts)


def _shared_station_record(pa: dict, pb: dict, hits: list[dict]) -> dict:
    """The pair's shared station in the overlap's shape, with its neutral reason (as filed, never 'should')."""
    h = hits[0]
    ea, eb = h["a"], h["b"]
    # the fuller of the two filed names ('Thurmond Dam' over 'Thurmond', 'Bonaire Primary' over 'Bonaire Pri')
    name = max((_station_display(ea["name"]), _station_display(eb["name"])), key=len) or ea["osm_name"] or "the same substation"
    ua, ub = UTILITY_SHORT.get(pa["utility"], pa["utility"]), UTILITY_SHORT.get(pb["utility"], pb["utility"])
    da, db = _date(pa.get("in_service")), _date(pb.get("in_service"))
    months = _months_apart(da, db) if da and db else None
    if da and db:
        # the in-service DATES only: the pair's one time gap is its build windows' (the list, the sheet and the score
        # all use that one), so a second 'N years apart' here would read as a contradiction
        if (da.year, da.month) == (db.year, db.month):
            when = f"both in service {da:%b %Y}"
        else:
            when = f"{ua}'s in service {da:%b %Y}, {ub}'s {db:%b %Y}"
        reason = f"As filed, both projects work at {name}: {when}"
    else:
        known = [f"{u}'s in service in {d.year}" for u, d in ((ua, da), (ub, db)) if d]
        missing = [u for u, d in ((ua, da), (ub, db)) if not d]
        reason = f"As filed, both projects work at {name}" + (f": {known[0]}" if known else "") + (
            f"; {' and '.join(missing)}'s filing gives no in-service date" if missing else ""
        )
    # a stand-in: no OSM substation carries the filed name, so the pipeline placed that endpoint at the nearest
    # substation OpenStreetMap maps there (often unnamed); the link then points at the stand-in, not a named station
    proxies = int(ea["proxy"]) + int(eb["proxy"])
    stand_in = h["rule"] == "osm_feature" and proxies > 0
    if h["rule"] == "osm_feature":
        osm_what = f"{ea['osm_name']} ({ea['osm_key'].replace('/', ' ')})" if ea["osm_name"] else f"OpenStreetMap {ea['osm_key'].replace('/', ' ')}"
        how = f"Same OpenStreetMap substation: {ua}'s '{ea['name']}' and {ub}'s '{eb['name']}' both resolve to {osm_what}" + (
            (
                "; no OpenStreetMap substation carries that name, so on both sides the nearest one stands in"
                if proxies == 2
                else "; on one side no OpenStreetMap substation carries the filed name, so the nearest one stands in"
            )
            + ", and the filed names were checked too"
            if stand_in
            else ""
        )
    else:
        how = (
            f"Same station by name: {ua}'s '{ea['name']}' and {ub}'s '{eb['name']}' are one name once qualifiers and "
            f"generic words are dropped, {_km_between(ea, eb):.2f} km apart (no OpenStreetMap id on at least one side)"
        )
    return {
        "name": name,
        "osm_url": ea["osm_url"] if h["rule"] == "osm_feature" else (ea["osm_url"] or eb["osm_url"]),
        "osm_name": ea["osm_name"] or eb["osm_name"],
        "stand_in": stand_in,  # the OSM feature stands in for the filed name (the link's text must say so)
        "lat": round((ea["lat"] + eb["lat"]) / 2, 6),
        "lon": round((ea["lon"] + eb["lon"]) / 2, 6),
        "a_end": {"index": ea["index"], "name": ea["name"], "confidence": ea["confidence"]},
        "b_end": {"index": eb["index"], "name": eb["name"], "confidence": eb["confidence"]},
        "rule": h["rule"],
        "a_in_service": da.isoformat() if da else None,
        "b_in_service": db.isoformat() if db else None,
        "months_apart": months,
        "reason": reason,
        "how": how,
        "also": [x["a"]["name"] for x in hits[1:]],  # a pair can share both ends (two lines between the same stations)
    }


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

# The list's groups, in order: what a planner can still act on comes first (the score orders pairs inside a group).
# together: the two build windows share months still ahead or open now; apart: both projects are still to be built, in
# windows that share no months; unknown: a filing gives no date; passed: the months they shared have passed, or one
# project's build window is already over, as filed (nothing left to build together, as filed).
GROUPS = {
    "together": (0, "Building in the same months", "both build windows share months that are still ahead or open now"),
    "apart": (1, "Building at different times", "both projects are still to be built, in build windows that share no months"),
    "unknown": (2, "Timing unknown", "at least one filing gives no in-service date"),
    "passed": (3, "Time passed, as filed", "the months they shared have passed, or one project's build window is already over"),
}


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
            "group": "unknown",
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
    if overlap > 0:
        group = "passed" if ahead == "past" else "together"
    else:
        group = "passed" if min(wa[1], wb[1]) < _today() else "apart"
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
        "group": group,  # the list's group (GROUPS): together, apart or passed
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
    hits = _shared_stations(st["stations"][i], st["stations"][j])
    station = _shared_station_record(pa, pb, hits) if hits else None

    reasons = []
    if station:
        reasons += [
            f"{SAME_STATION[1]}: {station['reason']}",
            station["how"],
        ]
    reasons += [
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
    g_rank, g_label, g_what = GROUPS[tl["group"]]
    reasons.append(f"Listed under '{g_label}': {g_what}")
    reasons.append(
        f"Score {score:g} = distance {df:.2f} ({'closest points' if method == 'closest' else 'centers'})"
        f" x timeline {tl['factor']:g} x location {cf:g}" + (f" x same kV {SAME_KV_BONUS:g}" if kf > 1 else "")
        + ("; within its group, same-station pairs are listed first, then by score" if station else "")
    )
    cls = SAME_STATION if station else (tier, label, what)
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
        # the ranking class: "same_station" (listed first) or the distance tier
        "class": cls[0],
        "class_label": cls[1],
        "class_what": cls[2],
        "shared_station": station,
        "closest_points": [cpa, cpb],
        "time_gap_days": tl["time_gap_days"],
        "windows_overlap_months": tl["windows_overlap_months"],
        "window_gap_days": tl["window_gap_days"],
        "same_window": tl["same_window"],
        "ahead": tl["ahead"],
        # the list's group: together (a shared build window still ahead or open now), apart, unknown, passed (GROUPS)
        "group": tl["group"],
        "group_label": g_label,
        "score": score,
        "score_parts": {"distance": round(df, 3), "timeline": tl["factor"], "location": cf, "same_kv": kf, "same_station": bool(station)},
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


def rank_key(r: dict):
    """The default order: the group first (what can still be built together, then what could be aligned, then what has
    passed, as filed: GROUPS), then same-station pairs before every distance tier, then score, then distance."""
    return (GROUPS.get(r.get("group"), GROUPS["unknown"])[0], 0 if r.get("shared_station") else 1, -r["score"], r["distance_km"], r["id"])


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
    rows.sort(key=rank_key)
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

    # Savings that need both crews in the field at once (one mobilization, one laydown yard) are counted only when the
    # build windows share months: with no shared window there is nothing to mobilize together, so they are left out
    # (listed in `left_out` with the reason) rather than shown as a $0-to-something range
    left_out = []
    ma, mb = _mobilization(pa), _mobilization(pb)
    small = ma if ma[1] <= mb[1] else mb
    if aligned:
        item(
            "mobilization",
            "Crews and equipment mobilized once",
            0.5 * small[0],
            small[1],
            "USD",
            f"Half to all of the smaller project's mobilization/demobilization ({small[2]}: MISO 2018 unit cost x {ESCALATE_2018})",
            "miso18",
            needs="same build window",
        )
    else:
        left_out.append({"id": "mobilization", "label": "Crews and equipment mobilized once", "why": "needs the two build windows to share months"})

    if "site" in tiers_on:
        if aligned:
            y_lo = YARD_ACRES[0] * YARD_PREP_PER_ACRE[0]
            y_hi = YARD_ACRES[1] * YARD_PREP_PER_ACRE[1]
            item(
                "laydown_yard",
                "One laydown yard instead of two",
                y_lo,
                y_hi,
                "USD",
                f"A {YARD_ACRES[0]}-{YARD_ACRES[1]} acre staging yard not built twice: clearing + gravel at "
                f"${YARD_PREP_PER_ACRE[0]:,}-${YARD_PREP_PER_ACRE[1]:,} per acre (MISO unit costs; yard sizes from SCE's West of Devers PEA, which also plans to reuse yards other projects vacate)",
                "sce_wod",
                needs="same build window",
            )
            used.add("miso18")
        else:
            left_out.append({"id": "laydown_yard", "label": "One laydown yard instead of two", "why": "needs the two build windows to share months"})

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
        "Savings that need both crews in the field at once (mobilization, laydown yard) are left out when the build windows share no months.",
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
        "left_out": left_out,
        "total_low": sum(it["low"] for it in usd),
        "total_high": sum(it["high"] for it in usd),
        "unit": "USD",
        "context": context,
        "assumptions": assumptions,
        "sources": [SOURCES[k] for k in SOURCES if k in used],
    }


# ----------------------------------------------------------------------------- Sperry check

SPERRY_TOL_MI = 0.01  # their sheet gives miles to the hundredth
SPERRY_TOL_DAYS = 0  # and whole days: ours must be the same day count


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
        ok = (
            ours_mi is not None
            and abs(ours_mi - o["distance_mi"]) <= SPERRY_TOL_MI + 1e-9
            and ours_days is not None
            and abs(ours_days - o["time_gap_days"]) <= SPERRY_TOL_DAYS
        )
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
        # a row matches when our miles are within tolerance.mi of theirs and our days within tolerance.days (0: to the day)
        "tolerance": {"mi": SPERRY_TOL_MI, "days": SPERRY_TOL_DAYS},
        "projects_in_example": len(projects),
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


# A set-aside record's reason starts with the label of the check it failed; the funnel says it the failing way round.
SET_ASIDE_WORDS = {
    "located": "No endpoint could be placed on the map",
    "place_named": "The title names no place",
    "span_plausible": "Line endpoints 150 km or more apart",
    "date_valid": "In-service date unreadable or outside 2020-2040",
    "id_unique": "Project id used twice",
    "extract_complete": "A field could not be read from the PDF",
    "in_region": "Placed outside SC and GA",
    "in_territory": "Placed out of reach of the filer's state",
}


def _funnel(st: dict, report: dict) -> dict:
    """The pipeline in numbers, every one counted from the data: the filings, the rows read from them, the rows that
    passed the blocking checks and those set aside (with the checks they failed). The pair counts (compared, flagged)
    depend on the comparison settings, so /overlaps gives those (`total_pairs`, `flagged`, `compared`)."""
    sources = [s for s in st["doc"].get("sources") or [] if isinstance(s, dict)]
    rows_by_source: dict[str, int] = {}
    for r in st["projects"] + st["quarantine"]:
        key = str((r.get("provenance") or {}).get("source") or "unknown")
        rows_by_source[key] = rows_by_source.get(key, 0) + 1
    checks = [c for c in report.get("checks") or [] if isinstance(c, dict)]
    by_label = {str(c.get("label")): c for c in checks if c.get("label")}
    failed: dict[str, dict] = {}
    for q in st["quarantine"]:
        seen = set()
        for reason in q.get("reasons") or []:
            label = str(reason).split(":", 1)[0].strip()
            c = by_label.get(label)
            cid = c.get("id") if c else label
            if cid in seen:
                continue
            seen.add(cid)
            f = failed.setdefault(cid, {"id": cid, "check": label, "label": SET_ASIDE_WORDS.get(cid, f"Failed: {label}"), "records": 0})
            f["records"] += 1
    extract = [s for s in report.get("stages") or [] if str(s.get("id", "")).startswith("extract")]
    return {
        "filings": len(sources),
        "pages": sum(int(s.get("pages") or 0) for s in sources) or None,
        "by_source": [
            {"id": s.get("id"), "utility": s.get("utility"), "pages": s.get("pages"), "rows": rows_by_source.get(str(s.get("id")), 0)}
            for s in sources
        ],
        "extracted": len(st["projects"]) + len(st["quarantine"]),
        # the extractors' own count (their stage report), which must agree with the records kept
        "extracted_by_stages": sum(int(s.get("out") or 0) for s in extract) if extract else None,
        "passed": len(st["projects"]),
        "set_aside": len(st["quarantine"]),
        "set_aside_by_check": sorted(failed.values(), key=lambda f: (-f["records"], f["id"])),
        "placed": len(st["placed"]),
        "checks": len(checks),
        "blocking_checks": sum(1 for c in checks if c.get("blocking")),
    }


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
        # DESC's current list (2026-2030) and what stays from the 2024-2028 one (None: the build's list alone)
        "edition": doc.get("edition"),
        "limit": {"max_km": MAX_KM_DEFAULT, "mi": SPERRY_MI, "text": limit_text(MAX_KM_DEFAULT)},
        "counts": _counts(st),
        "compared_projects": len(st["placed"]),
        "funnel": _funnel(st, report),
        "report": report,
        "rebuild_command": "backend/venv/Scripts/python backend/demo/gridlock/build.py",
        "rebuild_commands": [
            "backend/venv/Scripts/python backend/demo/gridlock/build.py",
            "backend/venv/Scripts/python backend/demo/gridlock/diff_filings.py",
        ],
    }


@router.get("/api/gridlock/changes")
def changes():
    """What changed since DESC's last filing: its 2026-2030 list against the 2024-2028 one (desc_changes.json, written by
    demo/gridlock/diff_filings.py), each row with both PDF pages, plus where the current comparison stands against the
    earlier list's (the same engine, the default settings: DESC x Georgia Power within 25 mi)."""
    st = _load()
    try:
        ch = _read_json(DESC_CHANGES_FILE)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="The 2026-2030 comparison hasn't been built (demo/gridlock/diff_filings.py)")
    return _json_bytes(st, "changes_bytes", lambda: _changes_payload(st, ch))


def _pdf_page(url: str | None, page) -> str | None:
    if url and page and re.search(r"\.pdf($|[?#])", url, re.I):
        return f"{url}#page={page}"
    return url


def _changes_payload(st: dict, ch: dict) -> dict:
    srcs = {s.get("years"): s for s in ch.get("sources") or [] if isinstance(s, dict)}
    old_src, new_src = srcs.get(EDITION_OLD) or {}, srcs.get(EDITION_NEW) or {}
    placed = {p["id"] for p in st["placed"]}
    by_page = {}
    for r in st["projects"] + st["quarantine"]:
        prov = r.get("provenance") or {}
        by_page[(prov.get("source"), prov.get("page"))] = r
    rows = []
    for r in ch.get("rows") or []:
        old, new = r.get("old") or None, r.get("new") or None
        rec = by_page.get(("desc_2026", new.get("page"))) if new else by_page.get(("desc", old.get("page"))) if old else None
        ins, cost = r.get("in_service") or {}, r.get("cost") or {}
        kind = r.get("change")
        tags = []
        if kind == "carried_over":
            if ins.get("changed"):
                tags.append("later" if ins.get("direction") == "later" else "earlier" if ins.get("direction") == "earlier" else "date")
            if cost.get("changed"):
                tags.append("cost_higher" if cost.get("direction") == "higher" else "cost_lower")
            if not tags:
                tags.append("same")
        rows.append({
            "change": kind,
            "tags": tags,
            "linked_by": r.get("linked_by"),
            "name": (new or old or {}).get("name"),
            "old": ({"page": old.get("page"), "project_id": old.get("project_id"), "name": old.get("name"),
                     "in_service_raw": ins.get("old_raw") or old.get("in_service_raw"), "cost": cost.get("old") or old.get("cost_total"),
                     "url": _pdf_page(old_src.get("url"), old.get("page"))} if old else None),
            "new": ({"page": new.get("page"), "project_id": new.get("project_id"), "name": new.get("name"),
                     "in_service_raw": ins.get("new_raw"), "cost": cost.get("new"), "url": _pdf_page(new_src.get("url"), new.get("page")),
                     "kept": bool((new.get("checks") or {}).get("kept", True)), "reasons": (new.get("checks") or {}).get("reasons") or []}
                    if new else None),
            "in_service": {k: ins.get(k) for k in ("changed", "months", "direction", "note")} if ins else None,
            "cost": {k: cost.get(k) for k in ("changed", "delta", "pct", "direction")} if cost else None,
            "before_the_new_list": r.get("in_service_before_the_new_list"),
            "summary": r.get("summary"),
            # the record the app compares for this row (None when it was set aside or superseded) and whether it is on the map
            "id": rec.get("id") if rec else None,
            "on_map": bool(rec and rec.get("id") in placed),
        })
    order = {"carried_over": 0, "new": 1, "dropped": 2}
    rows.sort(key=lambda x: (order.get(x["change"], 9), -((x["in_service"] or {}).get("months") or 0), x["name"] or ""))
    c = ch.get("counts") or {}
    # the comparison, now and with the earlier list (same engine, default settings)
    prm = _params(MAX_KM_DEFAULT, WINDOW_DEFAULT, "closest", "DESC", "GPC")
    now_rows = _compute(st, prm)["overlaps"]
    was = _load(AS_FILED)
    was_rows = _compute(was, prm)["overlaps"] if not was["fallback"] else []

    def tally(rows_):
        return {
            "flagged": len(rows_),
            "together": sum(1 for x in rows_ if x["group"] == "together"),
            "still_ahead": sum(1 for x in rows_ if x["same_window"] and x["ahead"] == "future"),
            "open_now": sum(1 for x in rows_ if x["same_window"] and x["ahead"] == "open"),
            "passed": sum(1 for x in rows_ if x["group"] == "passed"),
        }

    return {
        "about": ch.get("_about"),
        "command": ch.get("command"),
        "sources": [{"years": s.get("years"), "title": s.get("title"), "url": s.get("url")} for s in (old_src, new_src) if s],
        "method": ch.get("method"),
        "counts": {
            "old_projects": c.get("old_projects"),
            "new_projects": c.get("new_projects"),
            "carried_over": c.get("carried_over"),
            "later": c.get("new_in_service_date_later"),
            "earlier": c.get("new_in_service_date_earlier"),
            "cost_higher": c.get("new_cost_higher"),
            "cost_lower": c.get("new_cost_lower"),
            "unchanged": c.get("carried_over_same_date_and_cost"),
            "dropped": c.get("dropped"),
            "dropped_due_before_the_new_list": c.get("dropped_due_before_the_new_list"),
            "new": c.get("new"),
            "total_cost_old_list": c.get("total_cost_old_list"),
            "total_cost_new_list": c.get("total_cost_new_list"),
        },
        "comparison": {"today": _today().isoformat(), "limit": limit_text(prm["max_km"]), "with_2026_2030": tally(now_rows),
                       "with_2024_2028": tally(was_rows)},
        "rows": rows,
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
        "overlaps": [
            {k: o[k] for k in ("id", "a", "b", "distance_km", "tier", "tier_label", "class", "class_label", "score")}
            | {"shared_station": o["shared_station"]["name"] if o["shared_station"] else None}
            for o in flagged[:50]
        ],
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
        # the placed projects on each side: with no utility on both sides, total_pairs = a_projects x b_projects
        "compared": {
            "a_projects": sum(1 for p in st["placed"] if p["utility"] in prm["a"]),
            "b_projects": sum(1 for p in st["placed"] if p["utility"] in prm["b"]),
            "disjoint": not (set(prm["a"]) & set(prm["b"])),
            # the rest of the projects that passed the checks, so the funnel adds up from "passed" to "pairs":
            # passed = a_projects + b_projects + not_compared + unplaced (with disjoint sides)
            "not_compared": [
                {"utility": u, "projects": n}
                for u in UTILITY_ORDER
                if u not in chosen and (n := sum(1 for p in st["placed"] if p["utility"] == u))
            ],
            "unplaced": len(st["projects"]) - len(st["placed"]),
        },
        "flagged": len(rows),
        "by_tier": tiers,
        "tiers": [{"id": t[0], "label": t[4], "under_km": t[1], "what": t[5]} for t in TIERS],
        # the class listed above every tier: pairs whose filings work at the same substation
        "same_station": {
            "id": SAME_STATION[0], "label": SAME_STATION[1], "what": SAME_STATION[2],
            "count": sum(1 for r in rows if r["shared_station"]),
            "stations": sorted({r["shared_station"]["name"] for r in rows if r["shared_station"]}),
            "rule": "within each group, listed before every distance tier, then by score",
        },
        # the list's groups in order (rank_key): what can still be built together first
        "groups": [
            {"id": gid, "label": g[1], "what": g[2], "count": sum(1 for r in rows if r["group"] == gid)}
            for gid, g in sorted(GROUPS.items(), key=lambda kv: kv[1][0])
        ],
        "rank_rule": RANK_RULE,
        "limit_text": limit_text(prm["max_km"]),
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
            {"id": o["id"], "rank": o["rank"], "distance_km": o["distance_km"], "tier": o["tier"], "tier_label": o["tier_label"],
             "class": o["class"], "class_label": o["class_label"], "score": o["score"],
             "shared_station": o["shared_station"]["name"] if o["shared_station"] else None,
             # the whole record, so the page can draw the pair at the start (Sperry's example alone), whatever the filters
             "overlap": o}
            if o
            else ({"id": "~".join(sorted(pair)), "rank": None} if pair else None)
        )
    # their ten projects and the record each became in the full filings (the pipeline's own match, by title and place)
    theirs = {p["project_id"]: p for p in st["example"]["projects"]}
    out["projects"] = []
    for sid, p in theirs.items():
        ours = st["sperry_ids"].get(sid) if st["sperry_ids"].get(sid) in st["by_id"] else None
        rec = st["by_id"].get(ours) if ours else None
        out["projects"].append({
            "sperry_id": sid,
            "our_id": ours,
            "utility": NAME_TO_CODE.get(str(p.get("utility", "")).lower(), p.get("utility")),
            "name": p.get("project_name"),
            # which DESC list the record the app compares comes from (None: Georgia)
            "edition": rec.get("edition") if rec else None,
        })
    desc = [p for p in out["projects"] if p["utility"] == "DESC" and p["our_id"]]
    earlier_only = {p["sperry_id"] for p in desc if p["edition"] == EDITION_OLD}
    out["found_in_filings"] = {
        "flagged": sum(1 for r in out["rows"] if r["ours"] and r["ours"].get("rank")),
        "of": len(out["rows"]),
        "settings": f"closest points within {limit_text(MAX_KM_DEFAULT)}, DESC x Georgia Power",
        "projects_matched": sum(1 for p in out["projects"] if p["our_id"]),
        # their sheet is built from DESC's 2024-2028 list: how many of their DESC projects the 2026-2030 list still carries
        "desc_in_current_list": sum(1 for p in desc if p["edition"] == EDITION_NEW),
        "desc_only_in_earlier_list": len(earlier_only),
        # their pairs with one of those DESC projects in them (a separate count from "passed": a pair can have passed
        # because the Georgia project's window has ended while its DESC project is still in the current list)
        "pairs_with_earlier_only_desc": sum(1 for r in out["rows"] if r["a"] in earlier_only or r["b"] in earlier_only),
        "passed": sum(1 for r in out["rows"] if r["ours"] and (r["ours"].get("overlap") or {}).get("group") == "passed"),
    }
    return _fast_json(_finite(out))


# ----------------------------------------------------------------------------- trace one pair
#
# GET /api/gridlock/trace/{overlap_id}: one flagged pair taken apart, from its rank back to the PDF. The score's terms
# are recomputed here with the same functions the ranking uses (and must multiply back to the pair's score); each
# endpoint says how the pipeline placed it (its OSM feature, the matching rule, the confidence and the reasons it
# recorded); each project gives its source file (title, page, the SHA-256 the pipeline recorded when it read the PDF),
# the row's raw text and every field as parsed. The PDFs themselves are not in the repository: the trace shows only
# what the committed data holds and never computes anything from a file that isn't committed.

DISTANCE_RULE = (
    "Touching (under 0.1 km, or crossing) weighs 1.00; share the land (0.1-1.6 km) 0.90 down to 0.70; share site logistics "
    "(1.6-8 km) 0.70 down to 0.45; share crews and equipment (8-40 km) 0.45 down to 0.20, sliding down within each tier"
)
TIMELINE_RULE = (
    "Build windows overlap: 1. Apart by up to a year: 0.8; up to three years: 0.5; longer: 0.25; a date missing: 0.5. "
    f"Times {PASSED_FACTOR:g} when, as filed, the shared window (or both windows) ended before today"
)
LOCATION_RULE = "The weaker of the two projects' location confidence: high 1, medium 0.8, low 0.5"
KV_RULE = f"Times {SAME_KV_BONUS:g} when both are line work at the same kV class, else times 1"
RANK_RULE = (
    "Pairs whose build windows share months still ahead or open now come first, then pairs still to be built at different "
    "times, then pairs whose time has passed as filed; within each group, pairs whose filings work at the same substation "
    "first, then by score, then by distance"
)
TRACE_NOTE = (
    "The source PDFs are public filings and are not in this repository; this trace shows exactly what the committed data "
    "(backend/demo/gridlock/data/projects.json and desc_2026_2030.json) holds. The SHA-256 is the one the pipeline recorded when it read each PDF: "
    "download the filing from its link and compare to confirm it is the same file."
)
# the parsed fields of a row, in reading order, with their labels (values shown as stored)
TRACE_FIELDS = (
    ("project_id_raw", "Project ID, as filed"),
    ("teams_no", "TEAMS project no."),
    ("name", "Title"),
    ("sponsor_raw", "Sponsor, as filed"),
    ("zone", "Zone"),
    ("plan_year", "Plan year"),
    ("in_service_raw", "In-service date, as filed"),
    ("in_service", "In-service date, normalized"),
    ("kv", "Voltage (kV), read from the title"),
    ("kind", "Kind of work"),
    ("kind_basis", "Kind read from the word"),
    ("status", "Status"),
    ("cost_usd", "Estimated cost (USD)"),
    ("cost_by_year", "Cost by year (USD)"),
    ("miles", "Length (mi)"),
    ("description", "Description"),
    ("need", "Need"),
    ("change_ten_year", "Change since the last 10-year plan"),
    ("change_irp", "Change since the last IRP"),
    ("via", "Via"),
    ("customer_named", "Customer named"),
    ("name_prefixes", "Name prefixes"),
    ("notes", "Notes"),
)
MATCH_RULES = (
    # (id, test on the recorded match text, label)
    ("sperry_hand", lambda m: m.startswith("located by hand in Sperry"), "Placed by hand in Sperry's worked example"),
    ("stand_in", lambda m: "no OSM substation is named" in m,
     "No OpenStreetMap substation carries the name: a geocoded place, then the nearest substation of that voltage stands in"),
    ("name_generic", lambda m: "same name once generic words are dropped" in m, "Same name once generic words are dropped"),
    ("similar_spelling", lambda m: "similar spelling" in m, "Similar spelling"),
    ("same_name", lambda m: "same name" in m, "Same name"),
)


TRACE_PLAIN_NUMBERS = {"plan_year"}  # a year reads 2025, not 2,025


def _field_text(v, grouped: bool = True) -> str | None:
    """A parsed field as text: every number grouped the same way (5,000 beside 2,150,000), unless it is a year."""
    if v is None or v == "" or v == [] or v == {}:
        return None
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if not grouped:
            return str(v) if isinstance(v, int) else f"{v:g}"
        return f"{v:,}"
    if isinstance(v, list):
        return ", ".join(t for t in (_field_text(x, grouped) for x in v) if t) or None
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_field_text(x, grouped) if x is not None else '-'}" for k, x in v.items()) or None
    return str(v)


def _match_rule(match: str | None, located: bool) -> dict:
    if not located:
        return {"id": "not_located", "label": "Not located: no OpenStreetMap feature or place matched this name"}
    m = str(match or "")
    for rid, test, label in MATCH_RULES:
        if test(m):
            return {"id": rid, "label": label}
    return {"id": "other", "label": "Matched as recorded"}


def _trace_endpoint(e: dict) -> dict:
    located = e.get("lat") is not None and e.get("lon") is not None
    osm = e.get("osm") if isinstance(e.get("osm"), dict) else None
    match = e.get("match")
    clauses = [c.strip() for c in re.split(r";\s+", str(match)) if c.strip()] if match else []
    return {
        "name": e.get("name"),
        "raw": e.get("raw"),
        "located": located,
        "lat": e.get("lat"),
        "lon": e.get("lon"),
        "state": e.get("state"),
        "osm": (
            {"type": osm.get("type"), "id": osm.get("id"), "name": osm.get("name"), "operator": osm.get("operator"),
             "url": osm.get("url") or (f"https://www.openstreetmap.org/{osm['type']}/{osm['id']}" if osm.get("type") and osm.get("id") is not None else None)}
            if osm
            else None
        ),
        "confidence": e.get("confidence"),
        "rule": _match_rule(match, located),
        "from_description": str(match or "").startswith("from the description"),
        "assumption": "an assumption" in str(match or ""),
        "match": match,  # the reason exactly as the pipeline recorded it
        "clauses": clauses,
    }


def _trace_project(st: dict, p: dict, win, check_meta: dict) -> dict:
    prov = p.get("provenance") or {}
    src = {s.get("id"): s for s in st["doc"].get("sources") or [] if isinstance(s, dict)}.get(prov.get("source")) or {}
    fields = []
    for key, label in TRACE_FIELDS:
        text = _field_text(p.get(key), grouped=key not in TRACE_PLAIN_NUMBERS)
        if text is not None:
            fields.append({"field": key, "label": label, "value": text})
    checks = []
    for c in p.get("checks") or []:
        if not isinstance(c, dict):
            continue
        meta = check_meta.get(c.get("id")) or {}
        checks.append({"id": c.get("id"), "label": meta.get("label") or c.get("id"), "blocking": bool(meta.get("blocking")),
                       "status": c.get("status"), "detail": c.get("detail")})
    geom = p.get("geometry") or {}
    return {
        "id": p["id"],
        "utility": p.get("utility"),
        "utility_name": p.get("utility_name") or UTILITIES.get(p.get("utility"), (p.get("utility"),))[0],
        "state": p.get("state"),
        "name": p.get("name"),
        "confidence": p.get("confidence"),
        "geometry": {"type": geom.get("type"), "basis": geom.get("basis"), "coords": geom.get("coords")},
        "window": (
            {"start": win[0].isoformat(), "end": win[1].isoformat(), "basis": win[2], "filed": _window_filed(p)} if win else None
        ),
        "endpoints": [_trace_endpoint(e) for e in p.get("endpoints") or [] if isinstance(e, dict)],
        "source": {
            "id": src.get("id") or prov.get("source"),
            "title": src.get("title"),
            "publisher": src.get("publisher"),
            "file": src.get("file"),
            "package_file": PDF_NAMES.get(prov.get("source")),  # the same PDF's name in Sperry's starter package
            "url": src.get("url"),
            "pages": src.get("pages"),
            "sha256": src.get("sha256"),  # as recorded by the pipeline; None when it recorded none (never computed here)
        },
        "page": prov.get("page"),
        "detail_page": prov.get("detail_page"),
        "raw_text": prov.get("text"),
        "fields": fields,
        "checks": checks,
        "checks_summary": {k: sum(1 for c in checks if c["status"] == s) for k, s in (("passed", "pass"), ("warned", "warn"), ("failed", "fail"))},
    }


def _distance_term(km: float) -> tuple[float, str]:
    """The distance factor and how it was reached (the same arithmetic as _distance_factor)."""
    inner = 0.0
    for tid, edge, top, bottom, label, _what in TIERS:
        if km < edge or tid == "crews":
            if tid == "touching":
                return 1.0, f"{km:.2f} km: {label.lower()} (under 0.1 km, or crossing) weighs 1.00"
            frac = min(1.0, max(0.0, (km - inner) / (edge - inner)))
            val = top - (top - bottom) * frac
            return val, (
                f"{km:.3f} km: {label.lower()} ({inner:g}-{edge:g} km) slides from {top:.2f} to {bottom:.2f}: "
                # 4 decimals: what this arithmetic on a 3-decimal km supports (the term itself is served to 6)
                f"{top:.2f} - {top - bottom:.2f} x ({km:.3f} - {inner:g}) / {edge - inner:g} = {val:.4f}"
            )
        inner = edge
    return TIERS[-1][3], f"{km:.2f} km: beyond every tier"


def _timeline_term(tl: dict, pa: dict, pb: dict) -> tuple[float, str]:
    """The timeline factor and the branch of the rule it took (mirrors _timeline)."""
    wa, wb = tl["windows"]
    if not wa or not wb:
        missing = " and ".join(p["id"] for p, w in ((pa, wa), (pb, wb)) if not w)
        return 0.5, f"{missing} has no in-service date in its filing, so the timeline is unknown: 0.5"
    if tl["same_window"]:
        base, why = 1.0, f"the build windows share {tl['windows_overlap_months']:g} months: 1"
    else:
        gap = tl["window_gap_days"] or 0
        base = 0.8 if gap <= 365 else (0.5 if gap <= 1095 else 0.25)
        band = "up to a year" if gap <= 365 else ("up to three years" if gap <= 1095 else "more than three years")
        why = f"the build windows are {gap:,} days ({_span(gap)}) apart, {band}: {base:g}"
    if tl["ahead"] == "past":
        val = PASSED_FACTOR if tl["same_window"] else round(base * PASSED_FACTOR, 3)
        what = "that shared window" if tl["same_window"] else "both windows"
        return val, f"{why}; as filed, {what} ended before today, times {PASSED_FACTOR:g}: {val:g}"
    return base, why


@router.get("/api/gridlock/trace/{overlap_id}")
# a cheap read (~15 ms); the venue shares one IP and the smoke suite sends ~31 traces a run, so two back-to-back runs fit
@limiter.limit("120/minute")
def trace(
    request: Request,
    overlap_id: str,
    max_km: float = Query(MAX_KM_DEFAULT),
    window_months: int = Query(WINDOW_DEFAULT),
    method: str = Query("closest"),
    a: str = Query("DESC"),
    b: str = Query("GPC"),
):
    """One pair taken apart: its rank, the score's terms with their values and where each comes from, the distance,
    each endpoint's OpenStreetMap match, and each project's provenance down to the PDF page and the file's hash."""
    prm = _params(max_km, window_months, method, a, b)
    st = _load()
    parts = overlap_id.split("~")
    if len(parts) != 2 or parts[0] not in st["index"] or parts[1] not in st["index"]:
        raise HTTPException(status_code=404, detail="No overlap with that id")
    i, j = st["index"][parts[0]], st["index"][parts[1]]
    if st["util"][i] == st["util"][j] or st["closest_km"][i, j] > MAX_KM_CAP:
        raise HTTPException(status_code=404, detail=f"Those two projects aren't a cross-utility pair within {MAX_KM_CAP:g} km")
    months = prm["window_months"]
    rec = _overlap_record(st, i, j, months, prm["method"])
    pa, pb = st["placed"][i], st["placed"][j]

    # the score's terms, recomputed with the ranking's own functions (on the unrounded distance, as the ranking does)
    score_km = _pair_closest(st["geoms"][i], st["geoms"][j])[0] if prm["method"] == "closest" else float(st["center_km"][i, j])
    df, d_how = _distance_term(score_km)
    tl = _timeline(pa, pb, months)
    tf, t_how = _timeline_term(tl, pa, pb)
    ca, cb = pa.get("confidence") or "low", pb.get("confidence") or "low"
    weaker = ca if CONF_RANK.get(ca, 1) <= CONF_RANK.get(cb, 1) else cb
    cf = CONF_FACTOR.get(weaker, 0.5)
    shared_kv = st["kv"][i] & st["kv"][j]
    both_lines = bool(st["line"][i] and st["line"][j])
    kf = SAME_KV_BONUS if (both_lines and shared_kv) else 1.0
    if kf > 1:
        k_how = f"both are line work at {max(shared_kv)} kV: times {SAME_KV_BONUS:g}"
    elif not both_lines:
        k_how = "not both line work (" + ", ".join(f"{p['id']} is {str(p.get('kind') or 'other').replace('_', ' ')}" for p in (pa, pb)) + "): times 1"
    else:
        k_how = "both are line work, at different kV classes: times 1"
    raw = 100 * df * tf * cf * kf
    parts_ok = (
        abs(df - rec["score_parts"]["distance"]) < 1e-3 and tf == rec["score_parts"]["timeline"]
        and cf == rec["score_parts"]["location"] and kf == rec["score_parts"]["same_kv"] and round(raw, 1) == rec["score"]
    )
    if not parts_ok:  # the trace must never disagree with the ranking it explains
        raise HTTPException(status_code=500, detail="The trace's terms don't reproduce this pair's score")
    terms = [
        # to 6 decimals, so the four values multiply back to the raw score to its 4th decimal (at 4 decimals the
        # distance factor alone can move it: rank 44's 0.2803 gave 9.2499 against 9.2506); the page shows as many as it needs
        {"id": "distance", "label": "Distance", "value": round(df, 6), "how": d_how, "rule": DISTANCE_RULE,
         "input": {"km": round(score_km, 3), "measured": "closest points" if prm["method"] == "closest" else "centers (Sperry's method)"}},
        {"id": "timeline", "label": "Timeline", "value": tf, "how": t_how, "rule": TIMELINE_RULE,
         "input": {"windows_overlap_months": tl["windows_overlap_months"], "window_gap_days": tl["window_gap_days"], "ahead": tl["ahead"]}},
        {"id": "location", "label": "Location", "value": cf, "rule": LOCATION_RULE,
         "how": f"{pa['id']} is {ca}, {pb['id']} is {cb}: the weaker, {weaker}, gives {cf:g}",
         "input": {"a": ca, "b": cb, "weaker": weaker}},
        {"id": "same_kv", "label": "Same kV", "value": kf, "how": k_how, "rule": KV_RULE,
         "input": {"a_kv": sorted(st["kv"][i]), "b_kv": sorted(st["kv"][j]), "both_line_work": both_lines}},
    ]
    listed = {o["id"]: o for o in _compute(st, prm)["overlaps"]}
    here = listed.get(rec["id"])
    check_meta = {c.get("id"): c for c in (st["doc"].get("report") or {}).get("checks") or [] if isinstance(c, dict)}
    wa, wb = tl["windows"]
    return _fast_json(_finite({
        "overlap_id": rec["id"],
        "params": prm,
        "rank": here["rank"] if here else None,  # None: the pair is outside these settings (it can still be traced)
        "flagged": len(listed),
        "rank_rule": RANK_RULE,
        "class": rec["class"],
        "class_label": rec["class_label"],
        "shared_station": rec["shared_station"],
        "sperry": rec["sperry"],
        "score": {
            "value": rec["score"],
            "raw": round(raw, 4),
            "formula": "100 x distance x timeline x location x same kV",
            "terms": terms,
            "reproduced": parts_ok,
        },
        "distance": {
            "km": rec["distance_km"],
            "mi": rec["distance_mi"],
            "crosses": rec["crosses"],
            "tier": rec["tier"],
            "tier_label": rec["tier_label"],
            "closest_points": rec["closest_points"],  # [lat, lon] on a, then on b
            "how": "the closest points of the two projects' drawn geometry (a line's segment, a substation's point), measured on a "
                   "flat plane centred on the pair: within about 0.1 % of the true distance at these ranges",
            "center_km": rec["center_distance_km"],
            "center_mi": rec["center_distance_mi"],
            "center_how": "Sperry's method: haversine between the midpoints of each project's located endpoints (R = 3958.8 mi)",
        },
        "timeline": {
            "a_window": {"start": wa[0].isoformat(), "end": wa[1].isoformat(), "basis": wa[2]} if wa else None,
            "b_window": {"start": wb[0].isoformat(), "end": wb[1].isoformat(), "basis": wb[2]} if wb else None,
            "reason": tl["reason"],
            "time_gap_days": tl["time_gap_days"],
        },
        "projects": [_trace_project(st, pa, wa, check_meta), _trace_project(st, pb, wb, check_meta)],
        "reasons": rec["reasons"],
        "built_at": st["doc"].get("built_at"),
        "fallback": st["fallback"],
        "note": TRACE_NOTE,
    }))


# ----------------------------------------------------------------------------- the coordination calendar
#
# The build windows the ranking scores, laid out in time: each compared project's window from _window (as filed, else the
# Comparison setting before in-service, "start derived"), and each flagged pair's SHARED window, the months both windows
# cover, taken from _timeline itself (the function that gives /overlaps its windows_overlap_months). No new rule: the
# calendar is the ranking's own timeline, drawn. Served as JSON (the page's Gantt), as iCalendar (RFC 5545) for a planner's
# calendar, and as the `calendar` sheet of the Excel export.

CAL_SCOPES = ("pairs", "all")
CAL_NOTE = (
    "Dates as filed; planned dates can change. A start marked 'derived' isn't in the filing: the build-window setting "
    "(the months before in-service) stands in for it, exactly as in the ranking. A shared window is the months both "
    "build windows cover. It says the two plans could coordinate, never whether the utilities do."
)
ICS_PRODID = "-//Overload//GridLock coordination calendar//EN"
ICS_UID_HOST = "overload-gridlock"
_ICS_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _span_months(s: date, e: date) -> float:
    """Months between two dates, the way _timeline counts shared months (days / 30.44, one decimal)."""
    return round(max(0, (e - s).days) / 30.44, 1)


def _now_of(s: date, e: date, today: date) -> str:
    """Where today falls in a window, with _timeline's words: 'future', 'open' or 'past'."""
    return "past" if e < today else ("open" if s <= today else "future")


def _source_ref(st: dict, p: dict) -> dict:
    """The project's filing and page, for a calendar entry's 'where this came from'."""
    prov = p.get("provenance") or {}
    src = {s.get("id"): s for s in st["doc"].get("sources") or [] if isinstance(s, dict)}.get(prov.get("source")) or {}
    url = src.get("url")
    page = prov.get("page")
    if url and page and re.search(r"\.pdf($|[?#])", url, re.I):
        url = f"{url}#page={page}"
    return {"id": prov.get("source"), "title": src.get("title"), "url": url, "page": page, "detail_page": prov.get("detail_page")}


def _calendar(st: dict, prm: dict) -> dict:
    """Every compared project's build window and every flagged pair's shared window (cached per data version, settings, day)."""
    today = _today()
    key = ("calendar", prm["max_km"], prm["window_months"], prm["method"], tuple(prm["a"]), tuple(prm["b"]), today)
    hit = st["cache"].get(key)
    if hit is not None:
        return hit
    months = prm["window_months"]
    res = _compute(st, prm)
    overlaps = res["overlaps"]
    chosen = set(prm["a"]) | set(prm["b"])
    pairs_of: dict[str, list[dict]] = {}
    for o in overlaps:  # rank order
        pairs_of.setdefault(o["a"], []).append(o)
        pairs_of.setdefault(o["b"], []).append(o)
    order = {c: k for k, c in enumerate(UTILITY_ORDER)}

    projects = []
    for p in st["placed"]:
        if p["utility"] not in chosen:
            continue
        win = _window(p, months)
        mine = pairs_of.get(p["id"], [])
        row = {
            "id": p["id"],
            "utility": p["utility"],
            "utility_name": p.get("utility_name") or UTILITIES.get(p["utility"], (p["utility"],))[0],
            "side": "a" if p["utility"] in prm["a"] else "b",
            "name": p.get("name"),
            "kind": p.get("kind"),
            "kv": p.get("kv") or [],
            "status": p.get("status"),
            "confidence": p.get("confidence"),
            "in_service": p.get("in_service"),
            "start": None,
            "end": None,
            "months": None,
            "start_as": None,
            "basis": None,
            "now": None,
            "pairs": [o["id"] for o in mine],
            "best_rank": mine[0]["rank"] if mine else None,
            "shared": sum(1 for o in mine if o["same_window"]),
            "source": _source_ref(st, p),
        }
        if win:
            s, e, basis = win
            row.update({
                "start": s.isoformat(),
                "end": e.isoformat(),
                "months": _span_months(s, e),
                # the start as filed (a filed start date, or DESC's first year of budgeted spend) or derived from the setting
                "start_as": "filed" if _window_filed(p) else "derived",
                "basis": basis,
                "now": _now_of(s, e, today),
            })
        projects.append(row)
    projects.sort(key=lambda r: (order.get(r["utility"], 99), r["start"] or "9999", r["id"]))
    by_id = {r["id"]: r for r in projects}

    shared = []
    for o in overlaps:
        if not o["same_window"]:
            continue
        pa, pb = st["by_id"][o["a"]], st["by_id"][o["b"]]
        tl = _timeline(pa, pb, months)  # the ranking's own timeline: the same windows, the same overlap
        (sa, ea, _), (sb, eb, _) = tl["windows"]
        s, e = max(sa, sb), min(ea, eb)
        st_rec = o.get("shared_station") or None
        shared.append({
            "id": o["id"],
            "rank": o["rank"],
            "a": o["a"],
            "b": o["b"],
            "a_utility": o["a_utility"],
            "b_utility": o["b_utility"],
            "start": s.isoformat(),
            "end": e.isoformat(),
            "months": tl["windows_overlap_months"],  # = the overlap's windows_overlap_months
            "ahead": tl["ahead"],
            "starts_as": {"a": by_id[o["a"]]["start_as"], "b": by_id[o["b"]]["start_as"]},
            "class": o["class"],
            "class_label": o["class_label"],
            "tier": o["tier"],
            "tier_label": o["tier_label"],
            "station": st_rec["name"] if st_rec else None,
            "distance_km": o["distance_km"],
            "distance_mi": o["distance_mi"],
            "score": o["score"],
            "sperry": o.get("sperry"),
            "share": o.get("share"),
        })

    # Same-station pairs whose build windows DON'T overlap (Thurmond Dam, Sperry's OVL_1, ranks first): no shared window
    # to draw, but the calendar still names them, with the gap between the two windows (the earlier one's end to the
    # later one's start), from the same _timeline.
    stations = []
    for o in overlaps:
        rec = o.get("shared_station")
        if not rec or o["same_window"]:
            continue
        pa, pb = st["by_id"][o["a"]], st["by_id"][o["b"]]
        tl = _timeline(pa, pb, months)
        wa, wb = tl["windows"]
        first = gap_from = gap_to = None
        if wa and wb:
            (first, (_, e1, _)), (_, (s2, _, _)) = sorted(((o["a"], wa), (o["b"], wb)), key=lambda x: (x[1][1], x[1][0]))
            gap_from, gap_to = e1.isoformat(), s2.isoformat()
        stations.append({
            "id": o["id"],
            "rank": o["rank"],
            "a": o["a"],
            "b": o["b"],
            "a_utility": o["a_utility"],
            "b_utility": o["b_utility"],
            "station": rec["name"],
            "first": first,  # the project whose build window ends first
            "gap_from": gap_from,  # that window's last day
            "gap_to": gap_to,  # the other window's first day
            "gap_days": tl["window_gap_days"],  # = the overlap's window_gap_days
            "in_service_months_apart": rec.get("months_apart"),
            "ahead": tl["ahead"],
            "starts_as": {"a": by_id[o["a"]]["start_as"], "b": by_id[o["b"]]["start_as"]},
            "sperry": o.get("sperry"),
        })

    dated = [r for r in projects if r["start"]]
    in_pairs = [r for r in projects if r["pairs"]]
    years = [int(r["start"][:4]) for r in dated] + [int(r["end"][:4]) for r in dated]
    n_assumed = sum(1 for r in dated if r["start_as"] == "derived")
    result = {
        "params": prm,
        "fallback": st["fallback"],
        "today": today.isoformat(),
        "range": {"from": min(years), "to": max(years)} if years else None,
        "counts": {
            "projects": len(projects),
            "dated": len(dated),
            "in_pairs": len(in_pairs),
            "flagged": len(overlaps),
            "shared": len(shared),
            "shared_open": sum(1 for w in shared if w["ahead"] == "open"),
            "shared_future": sum(1 for w in shared if w["ahead"] == "future"),
            "shared_start_derived": sum(1 for w in shared if "derived" in w["starts_as"].values()),
            "stations": len(stations),
            "start_derived": n_assumed,
        },
        "window_rule": (
            f"A project's build window is the one its filing supports (a filed start date, or DESC's yearly spending); for the "
            f"{n_assumed} of {len(dated)} projects here without one, the {months} months before in-service ('start derived')"
        ),
        "note": CAL_NOTE,
        "sources": [{"id": s.get("id"), "title": s.get("title"), "url": s.get("url")} for s in st["doc"].get("sources") or []],
        "projects": projects,
        "shared": shared,
        "stations": stations,
    }
    if len(st["cache"]) > 64:
        st["cache"].clear()
    st["cache"][key] = result
    return result


@router.get("/api/gridlock/calendar")
def calendar(
    max_km: float = Query(MAX_KM_DEFAULT),
    window_months: int = Query(WINDOW_DEFAULT),
    method: str = Query("closest"),
    a: str = Query("DESC"),
    b: str = Query("GPC"),
):
    """The coordination calendar: every compared project's build window (as filed, or 'start derived') with the flagged
    pairs it belongs to, each flagged pair's shared window (the months both build windows cover), and the same-station
    pairs whose windows don't overlap (`stations`, with the gap between them)."""
    prm = _params(max_km, window_months, method, a, b)
    return _fast_json(_finite(_calendar(_load(), prm)))


# --- iCalendar (RFC 5545) -------------------------------------------------------------------------------------------


def _ics_text(v) -> str:
    """A TEXT value (RFC 5545 3.3.11): backslash, semicolon and comma escaped, line breaks as \\n, control characters out."""
    s = _ICS_BAD.sub("", str(v or "")).replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    """RFC 5545 3.1: a content line over 75 octets is folded (CRLF + one space), never inside a UTF-8 character."""
    parts, cur, n = [], [], 0
    for ch in line:
        size = len(ch.encode("utf-8"))
        if n + size > (75 if not parts else 74):  # a continuation line's leading space counts
            parts.append("".join(cur))
            cur, n = [], 0
        cur.append(ch)
        n += size
    parts.append("".join(cur))
    return "\r\n ".join(parts)


def _ics_date(iso: str, plus_days: int = 0) -> str:
    return (date.fromisoformat(iso) + timedelta(days=plus_days)).strftime("%Y%m%d")


def _ics_uid(kind: str, ident: str) -> str:
    return f"{kind}.{re.sub(r'[^A-Za-z0-9]+', '-', ident).strip('-')}@{ICS_UID_HOST}"


def _plain_name(name) -> str:
    """A filed name for a calendar title: capitals (Georgia's table) title-cased; tokens with a digit, short codes and
    'kV' kept ('MITCHELL - NORTH TIFTON 230KV RECONDUCTOR' -> 'Mitchell - North Tifton 230kV Reconductor')."""
    s = re.sub(r"\s+", " ", str(name or "")).strip()
    if not s or s.upper() != s:
        return s
    out = []
    for i, w in enumerate(s.split(" ")):
        core = re.sub(r"[^A-Za-z&]", "", w)
        if any(c.isdigit() for c in w):
            out.append(re.sub(r"KV(?=[^A-Za-z]|$)", "kV", w))
        elif w == "KV":
            out.append("kV")
        elif not core or "&" in w or re.fullmatch(r"[A-Z]{2,4}:", w) or re.fullmatch(r"\([A-Z]{2,4}\)[,.;:]?", w) \
                or core in {"II", "III", "IV", "VI", "CC", "XFMR", "DESC", "USA"}:
            out.append(w)
        elif i and w.lower() in {"and", "of", "to", "on", "at", "the", "for"}:
            out.append(w.lower())
        else:
            t = re.sub(r"(^|[(\-/])mc([a-z])", lambda m: f"{m.group(1)}Mc{m.group(2).upper()}", w.lower())
            out.append(re.sub(r"(^|[(\-/])([a-z])", lambda m: m.group(1) + m.group(2).upper(), t))
    return " ".join(out)


def _fmt_month(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%b %Y") if iso else "no date"


def _fmt_day(iso: str | None) -> str:
    if not iso:
        return "no date"
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def _derived_sides(w: dict) -> list[str]:
    """The utilities (short names) whose side of a pair has a DERIVED build-window start (no start in the filing)."""
    return [UTILITY_SHORT.get(w[f"{k}_utility"], w[f"{k}_utility"]) for k in ("a", "b") if w["starts_as"].get(k) == "derived"]


def _dates_tag(w: dict) -> str:
    """How a pair's shared window is dated, for its title: 'as filed' only when both starts come from the filings."""
    d = _derived_sides(w)
    if not d:
        return "as filed"
    return f"{d[0]} start derived" if len(d) == 1 else f"{d[0]} and {d[1]} starts derived"


def _derived_line(w: dict, by_id: dict, months: int) -> str:
    """For a shared window resting on a derived start: which start, why, and whether the shared window begins on it."""
    d = _derived_sides(w)
    if not d:
        return ""
    ids = [w[k] for k in ("a", "b") if w["starts_as"].get(k) == "derived"]
    who = f"{d[0]}'s start is" if len(d) == 1 else "Both starts are"
    line = (f"{who} derived, not filed: the filing gives no start date, so the Comparison setting ({months} months "
            f"before in-service) stands in for it, as in the ranking.")
    if any(by_id[i]["start"] == w["start"] for i in ids):
        line += " The shared window begins on that derived start."
    return line


def _ics_project_lines(r: dict) -> list[str]:
    """One project in a calendar entry's description: who, what, the build window and where it came from."""
    who = UTILITY_SHORT.get(r["utility"], r["utility"])
    if r["start"]:
        how = "as filed" if r["start_as"] == "filed" else "start derived"
        win = f"Build window: {_fmt_month(r['start'])} to {_fmt_month(r['end'])} ({how}: {r['basis']})"
    else:
        win = "Build window: the filing gives no in-service date"
    src = r["source"] or {}
    page = f", page {src['page']}" if src.get("page") else ""
    page += f" (detail page {src['detail_page']})" if src.get("detail_page") else ""
    lines = [
        f"{who}: {_plain_name(r['name'])} ({r['id']})",
        f"  {win}",
        f"  In service: {_fmt_day(r['in_service'])} (as filed)",
        f"  Source: {src.get('title') or 'the public filing'}{page}" + (f" {src['url']}" if src.get("url") else ""),
    ]
    return lines


def _ics_shared_event(cal: dict, w: dict, by_id: dict, stamp: str) -> list[str]:
    pa, pb = by_id[w["a"]], by_id[w["b"]]
    ua, ub = UTILITY_SHORT.get(w["a_utility"], w["a_utility"]), UTILITY_SHORT.get(w["b_utility"], w["b_utility"])
    where = (f"both at {w['station']}" if w["station"] else
             f"closest points {w['distance_km']:.2f} km ({w['distance_mi']:.2f} mi) apart: {w['tier_label'].lower()}")
    tag = _dates_tag(w)
    summary = (f"Shared build window at {w['station']}: {ua} × {ub} ({tag})" if w["station"] else
               f"Shared build window: {ua} {_plain_name(pa['name'])} × {ub} {_plain_name(pb['name'])} ({tag})")
    when = f"{_fmt_month(w['start'])} to {_fmt_month(w['end'])}, about {w['months']:g} months"
    share = w.get("share") or ""
    derived = _derived_line(w, by_id, cal["params"]["window_months"])
    first = (f"Both build windows cover these months ({when}); {where}. {derived}" if derived else
             f"As filed, both projects' build windows cover these months ({when}); {where}.")
    desc = [
        first,
        f"Pair #{w['rank']} of the {cal['counts']['flagged']} pairs flagged by Overload's Build together comparison."
        + (f" The two plans could share: {share[0].lower()}{share[1:]}." if share else ""),
        "",
        *_ics_project_lines(pa),
        "",
        *_ics_project_lines(pb),
        "",
        f"Comparison: {_settings_line(cal['params'])}.",
        CAL_NOTE,
        EXPORT_DISCLAIMER,
    ]
    return [
        "BEGIN:VEVENT",
        f"UID:{_ics_uid('shared', w['id'])}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{_ics_date(w['start'])}",
        f"DTEND;VALUE=DATE:{_ics_date(w['end'], 1)}",  # all-day events end the day after (DTEND is exclusive)
        f"SUMMARY:{_ics_text(summary)}",
        f"DESCRIPTION:{_ics_text(chr(10).join(desc))}",
        f"CATEGORIES:{_ics_text('Shared build window')},{_ics_text(ua)},{_ics_text(ub)}",
        "STATUS:TENTATIVE",  # planned dates, as filed
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def _station_line(s: dict, me: str) -> str:
    """A same-station pair without a shared window, from one of its projects' side."""
    other = s["b"] if s["a"] == me else s["a"]
    gap = ("the two build windows meet end to start" if s["gap_days"] == 0 else
           f"the two build windows are {_span(s['gap_days'])} apart, no shared window" if s["gap_days"] is not None else
           "timing unknown")
    return f"Same station as {other}, at {s['station']} (pair #{s['rank']}): {gap}."


def _ics_project_event(cal: dict, r: dict, stamp: str, ranks: dict) -> list[str]:
    who = UTILITY_SHORT.get(r["utility"], r["utility"])
    tag = "as filed" if r["start_as"] == "filed" else "start derived"
    summary = f"{who} build window ({tag}): {_plain_name(r['name'])}"
    pairs = [f"#{ranks[pid]}" for pid in r["pairs"] if pid in ranks]
    stations = [s for s in cal.get("stations") or [] if r["id"] in (s["a"], s["b"])]
    desc = [
        *_ics_project_lines(r),
        "",
        (f"In {len(pairs)} flagged pair{'s' if len(pairs) != 1 else ''} ({', '.join(pairs[:12])}{', ...' if len(pairs) > 12 else ''}); "
         f"{r['shared']} of them share{'s' if r['shared'] == 1 else ''} build months with it." if pairs
         else "In no flagged pair at these settings."),
        *[_station_line(s, r["id"]) for s in stations],
        f"Comparison: {_settings_line(cal['params'])}.",
        CAL_NOTE,
        EXPORT_DISCLAIMER,
    ]
    return [
        "BEGIN:VEVENT",
        f"UID:{_ics_uid('build', r['id'])}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{_ics_date(r['start'])}",
        f"DTEND;VALUE=DATE:{_ics_date(r['end'], 1)}",
        f"SUMMARY:{_ics_text(summary)}",
        f"DESCRIPTION:{_ics_text(chr(10).join(desc))}",
        f"CATEGORIES:{_ics_text('Build window')},{_ics_text(who)}",
        "STATUS:TENTATIVE",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def build_ics(st: dict, prm: dict, pair: str | None = None, scope: str = "pairs", upcoming: bool = False) -> tuple[bytes, str]:
    """(the .ics bytes, a calendar name). Whole calendar: one event per shared window (rank order), then one per build
    window of the projects in flagged pairs (scope 'pairs') or of every compared project (scope 'all'); `upcoming` keeps
    only the windows that haven't ended before today (as filed). One pair: its shared window, naming both projects in
    full, or, when their windows don't overlap, the two build windows."""
    cal = _calendar(st, prm)
    by_id = {r["id"]: r for r in cal["projects"]}
    ranks = {w["id"]: w["rank"] for w in cal["shared"]} | {o["id"]: o["rank"] for o in _compute(st, prm)["overlaps"]}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    events: list[list[str]] = []
    if pair is not None:
        w = next((x for x in cal["shared"] if x["id"] == pair), None)
        if w is None:
            parts = pair.split("~")
            if len(parts) != 2 or not all(x in by_id for x in parts) or by_id[parts[0]]["utility"] == by_id[parts[1]]["utility"]:
                raise HTTPException(status_code=404, detail="No pair with that id among the projects compared at these settings")
            events = [_ics_project_event(cal, by_id[x], stamp, ranks) for x in parts if by_id[x]["start"]]
            if not events:
                raise HTTPException(status_code=404, detail="Neither filing gives an in-service date, so there is nothing to add")
            s = next((x for x in cal["stations"] if x["id"] == pair), None)
            name = (f"Overload: pair #{s['rank']} at {s['station']}, the two build windows" if s else
                    f"Overload: {parts[0]} and {parts[1]} build windows")
        else:
            events = [_ics_shared_event(cal, w, by_id, stamp)]
            name = f"Overload: shared build window, pair #{w['rank']} ({_dates_tag(w)})"
    else:
        events = [_ics_shared_event(cal, w, by_id, stamp) for w in cal["shared"] if not upcoming or w["ahead"] != "past"]
        rows = [r for r in cal["projects"] if r["start"] and (scope == "all" or r["pairs"]) and (not upcoming or r["now"] != "past")]
        rows.sort(key=lambda r: (r["start"], r["id"]))
        events += [_ics_project_event(cal, r, stamp, ranks) for r in rows]
        if not events:  # RFC 5545: a calendar holds at least one component
            raise HTTPException(status_code=404, detail="Nothing here is still ahead: every window ended before today, as filed")
        name = "Overload: coordination calendar, still ahead" if upcoming else "Overload: coordination calendar"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{ICS_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_text(name)}",
        f"X-WR-CALDESC:{_ics_text(CAL_NOTE + ' ' + EXPORT_DISCLAIMER)}",
    ]
    for ev in events:
        lines += ev
    lines.append("END:VCALENDAR")
    body = "\r\n".join(_ics_fold(x) for x in lines) + "\r\n"
    return body.encode("utf-8"), name


@router.get("/api/gridlock/calendar.ics")
@limiter.limit("30/minute")
def calendar_ics(
    request: Request,
    pair: str | None = Query(None, max_length=80),
    scope: str = Query("pairs"),
    upcoming: bool = Query(False),
    max_km: float = Query(MAX_KM_DEFAULT),
    window_months: int = Query(WINDOW_DEFAULT),
    method: str = Query("closest"),
    a: str = Query("DESC"),
    b: str = Query("GPC"),
):
    """The coordination calendar as iCalendar: every shared window and build window (?scope=pairs|all; ?upcoming=true
    leaves out what ended before today), or one pair's shared window (?pair=<id>), all-day, tentative (planned dates as
    filed; a derived start is said so)."""
    scope = (scope or "").strip().lower()
    if scope not in CAL_SCOPES:
        raise HTTPException(status_code=422, detail=f"scope must be one of: {', '.join(CAL_SCOPES)}")
    prm = _params(max_km, window_months, method, a, b)
    st = _load()
    body, _name = build_ics(st, prm, pair=pair, scope=scope, upcoming=upcoming and pair is None)
    tail = (re.sub(r"[^A-Za-z0-9]+", "_", pair).strip("_") if pair else
            f"{'-'.join(prm['a'])}_{'-'.join(prm['b'])}_{scope}{'_upcoming' if upcoming else ''}")
    return _download(body, "text/calendar; charset=utf-8", f"Overload_calendar_{tail}.ics")


# ----------------------------------------------------------------------------- exports in Sperry's table format
#
# Sperry Tech's worked example (Projects_Overlaps.xlsx in their ShellHacks 2026 starter package) has two sheets,
# `projects` and `overlaps`. The export uses their sheet names and exactly their columns in their order, filled from the
# whole pipeline; Overload's own columns sit to the right of theirs. No spreadsheet library: the workbook is a handful of
# SpreadsheetML parts written with zipfile (inline strings, real numbers and dates, a frozen header, filters).

SPERRY_PROJECT_COLS = [
    "project_id", "utility", "state", "project_name", "name_a", "lat_a", "lon_a", "name_b", "lat_b", "lon_b",
    "lat_center", "lon_center", "in_service_date", "overlap_count", "overlap_1", "overlap_2", "overlap_3",
]
SPERRY_OVERLAP_COLS = [
    "overlap_id", "distance_mi", "time_gap (day)", "utility_a", "project_id_a", "project_name_a",
    "utility_b", "project_id_b", "project_name_b",
]
EXPORT_DISCLAIMER = (
    "Generated from public filings (DESC 2026-2030 list, and the 2024-2028 projects it no longer carries; Georgia 2025 IRP "
    "Vol. 3 public disclosure, unredacted fields only); "
    "locations approximate where marked; not an official utility record."
)
OSM_ATTRIBUTION = "Locations: (c) OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright), via Overpass and Nominatim."
# the file names in Sperry's package ("Project Listings"), so a reader can open their own copy at the cited page
PDF_NAMES = {
    "desc_2026": "2026-2030-2million-and-above-project-descriptions.pdf",
    "desc": "2024-2028-2million-and-above-project-descriptions.pdf",
    "ga_irp": "2025 IRP Volume 3 PUBLIC DISCLOSURE.pdf",
    "sperry_example": "Projects_Overlaps.xlsx",
}
FAULT_FILE = DATA_DIR / "fault_report.json"
EXPORT_TABLES = ("projects", "overlaps", "set_aside", "calendar")
AHEAD_WORDS = {"future": "still ahead", "open": "open now", "past": "ended (as filed)"}
NO_SHARED_WINDOW = "no shared window"


def _shared_window(o: dict) -> str | None:
    """The overlaps sheet's shared_window: where today falls in the two build windows' SHARED period. `ahead` is also set
    for pairs whose windows never overlap (it then describes both windows), so only a real shared period gets a word."""
    if o.get("same_window"):
        return AHEAD_WORDS.get(o.get("ahead"))
    return NO_SHARED_WINDOW if o.get("same_window") is False else None  # None: a project has no in-service date


def _window_filed(p: dict) -> bool:
    """True when _window takes the build window from the filing (its first branch), not an assumed one."""
    bw = p.get("build_window")
    if not isinstance(bw, dict):
        return False
    s, e = _date(bw.get("start")), _date(bw.get("end"))
    return bool(s and e and s <= e and not _window_assumed(p))


class _Formula:
    """A cell formula ({row} is this row's number) with the value Excel would compute, cached in the file."""

    def __init__(self, expr: str, value):
        self.expr, self.value = expr, value


def _yn(v) -> str | None:
    return None if v is None else ("yes" if v else "no")


def _round(v, nd):
    return None if v is None else round(float(v), nd)


def _pdf(st: dict, prov: dict) -> tuple[str | None, str | None]:
    src = {s.get("id"): s for s in st["doc"].get("sources") or []}.get(prov.get("source")) or {}
    return PDF_NAMES.get(prov.get("source")) or src.get("file"), src.get("url")


def _center_of(a: dict, b: dict, p: dict) -> tuple[float | None, float | None]:
    """Sperry's rule: the midpoint of the located endpoints, or the one located endpoint (their IF(ISBLANK(...)))."""
    out = []
    for k in ("lat", "lon"):
        va, vb = a.get(k), b.get(k)
        if va is not None and vb is not None:
            out.append((va + vb) / 2)
        else:
            out.append(va if va is not None else vb)
    if out[0] is None:
        c = p.get("center") or [None, None]
        return c[0], c[1]
    return out[0], out[1]


def _sorted_projects(st: dict) -> list[dict]:
    order = {c: k for k, c in enumerate(UTILITY_ORDER)}
    return sorted(st["projects"], key=lambda p: (order.get(p.get("utility"), 99), p["id"]))


def _project_table(st: dict, overlaps: list[dict], chosen: set, months: int) -> tuple[list[tuple], list[list]]:
    partners: dict[str, list[str]] = {}
    for o in overlaps:  # already in rank order
        partners.setdefault(o["a"], []).append(o["b"])
        partners.setdefault(o["b"], []).append(o["a"])
    extra = [
        "compared", "confidence", "kv", "kind", "in_service_as_filed", "build_window_start", "build_window_end", "window_filed",
        "cost_usd", "zone", "teams_no", "confidence_a", "confidence_b", "osm_url_a", "osm_url_b", "match_a", "match_b",
        "geometry_basis", "pdf_file", "pdf_page", "detail_page", "source_url", "checks_passed", "checks_run", "check_warnings",
        "overlaps_all",
    ]
    cols = [(c, None) for c in SPERRY_PROJECT_COLS] + [(c, "extra") for c in extra]
    rows = []
    for p in _sorted_projects(st):
        eps = [e or {} for e in (p.get("endpoints") or [])] + [{}, {}]
        a, b = eps[0], eps[1]
        lat_c, lon_c = _center_of(a, b, p)
        mine = partners.get(p["id"], [])
        win = _window(p, months)  # the window the overlaps were scored with (as filed, else assumed at this setting)
        prov = p.get("provenance") or {}
        pdf_file, url = _pdf(st, prov)
        chk = [c for c in p.get("checks") or [] if isinstance(c, dict)]
        rows.append([
            p["id"], p.get("utility_name") or UTILITIES.get(p.get("utility"), (p.get("utility"),))[0], p.get("state"), p.get("name"),
            a.get("name"), a.get("lat"), a.get("lon"), b.get("name"), b.get("lat"), b.get("lon"),
            _Formula("IF(ISBLANK(I{row}), F{row}, IF(ISBLANK(F{row}), I{row}, (F{row}+I{row})/2))", lat_c),
            _Formula("IF(ISBLANK(J{row}), G{row}, IF(ISBLANK(G{row}), J{row}, (G{row}+J{row})/2))", lon_c),
            _date(p.get("in_service")), len(mine), *(mine[:3] + [None] * (3 - len(mine[:3]))),
            _yn(p.get("utility") in chosen), p.get("confidence"), "/".join(str(k) for k in p.get("kv") or []) or None,
            (p.get("kind") or "").replace("_", " ") or None, p.get("in_service_raw"),
            win[0] if win else None, win[1] if win else None, _yn(_window_filed(p)) if win else None, p.get("cost_usd"),
            p.get("zone"), p.get("teams_no"), a.get("confidence"), b.get("confidence"),
            (a.get("osm") or {}).get("url"), (b.get("osm") or {}).get("url"), a.get("match"), b.get("match"),
            (p.get("geometry") or {}).get("basis"), pdf_file, prov.get("page"), prov.get("detail_page"), url,
            sum(1 for c in chk if c.get("status") == "pass"), len(chk),
            "; ".join(f"{c.get('id')}: {c.get('detail')}" for c in chk if c.get("status") == "warn") or None,
            ", ".join(mine) or None,
        ])
    return cols, rows


def _overlap_table(st: dict, overlaps: list[dict], method: str) -> tuple[list[tuple], list[list]]:
    extra = [
        "rank", "pair_id", "closest_points_km", "closest_points_mi", "center_distance_mi", "crosses", "tier", "tier_label",
        "same_station", "same_station_osm", "score",
        "windows_overlap_months", "window_gap_days", "shared_window", "in_service_a", "in_service_b", "confidence_a",
        "confidence_b", "could_share", "in_sperry_example", "why",
    ]
    cols = [(c, None) for c in SPERRY_OVERLAP_COLS] + [(c, "extra") for c in extra]
    rows = []
    for o in overlaps:
        pa, pb = st["by_id"][o["a"]], st["by_id"][o["b"]]
        dist = o["distance_mi"] if method == "closest" else o["center_distance_mi"]
        rows.append([
            f"OVL_{o['rank']}", _round(dist, 2), o.get("time_gap_days"),
            pa.get("utility_name") or o["a_utility"], o["a"], pa.get("name"),
            pb.get("utility_name") or o["b_utility"], o["b"], pb.get("name"),
            o["rank"], o["id"], _round(o["distance_km"], 3), _round(o["distance_mi"], 2), _round(o["center_distance_mi"], 2),
            _yn(o.get("crosses")), o["tier"], o["tier_label"],
            (o.get("shared_station") or {}).get("name"), (o.get("shared_station") or {}).get("osm_url"),
            o["score"], o.get("windows_overlap_months"), o.get("window_gap_days"),
            _shared_window(o), _date(pa.get("in_service")), _date(pb.get("in_service")),
            pa.get("confidence"), pb.get("confidence"), o.get("share"),
            f"Sperry {o['sperry']}" if o.get("sperry") else None,  # their numbering, not this sheet's overlap_id
            "; ".join(o.get("reasons") or []),
        ])
    return cols, rows


def _set_aside_table(st: dict) -> tuple[list[tuple], list[list]]:
    cols = [(c, None) for c in ("project_id", "utility", "state", "project_name", "in_service_date", "reasons",
                                "pdf_file", "pdf_page", "detail_page", "source_url")]
    rows = []
    for q in st["quarantine"]:
        prov = q.get("provenance") or {}
        pdf_file, url = _pdf(st, prov)
        rows.append([
            q.get("id"), q.get("utility_name") or q.get("utility"), q.get("state"), q.get("name"), _date(q.get("in_service")),
            "; ".join(q.get("reasons") or []) or None, pdf_file, prov.get("page"), prov.get("detail_page"), url,
        ])
    return cols, rows


CAL_NOW_WORDS = {"future": "still ahead", "open": "open now", "past": "ended (as filed)"}


def _calendar_table(st: dict, prm: dict) -> tuple[list[tuple], list[list], str]:
    """The coordination calendar as a sheet (after Sperry's two, which stay exactly theirs): a 'shared window' row per
    flagged pair whose build windows overlap (rank order), then a 'build window' row per compared project. Returns
    (columns, rows, a one-line count for the about sheet)."""
    cal = _calendar(st, prm)
    by_id = {r["id"]: r for r in cal["projects"]}
    ranks = {o["id"]: o["rank"] for o in _compute(st, prm)["overlaps"]}
    cols = [(c, None) for c in (
        "row_type", "rank", "id", "utility", "project_id_a", "project_name_a", "project_id_b", "project_name_b",
        "start", "end", "months", "status", "start_as", "same_station", "in_service_a", "in_service_b", "pairs", "basis",
        "source_a", "source_b",
    )]

    def src(r):
        s = r["source"] or {}
        page = f", page {s['page']}" if s.get("page") else ""
        return f"{s.get('title') or s.get('id') or ''}{page}".strip(", ") or None

    def starts(pa, pb):
        derived = [x["id"] for x in (pa, pb) if x["start_as"] == "derived"]
        return "filed" if not derived else f"derived for {', '.join(derived)}"

    pair_rows = []  # (rank, row): shared windows and same-station pairs without one, merged in rank order
    for w in cal["shared"]:
        pa, pb = by_id[w["a"]], by_id[w["b"]]
        pair_rows.append((w["rank"], [
            "shared window", w["rank"], w["id"],
            f"{UTILITY_SHORT.get(w['a_utility'], w['a_utility'])} x {UTILITY_SHORT.get(w['b_utility'], w['b_utility'])}",
            w["a"], pa["name"], w["b"], pb["name"], _date(w["start"]), _date(w["end"]), w["months"],
            CAL_NOW_WORDS.get(w["ahead"]), starts(pa, pb), w["station"],
            _date(pa["in_service"]), _date(pb["in_service"]), None,
            "the months both build windows cover (the overlaps sheet's windows_overlap_months)", src(pa), src(pb),
        ]))
    for s in cal["stations"]:
        pa, pb = by_id[s["a"]], by_id[s["b"]]
        if s["gap_from"]:
            second = s["b"] if s["first"] == s["a"] else s["a"]
            gap = "build windows meet end to start" if s["gap_days"] == 0 else f"build windows {_span(s['gap_days'])} apart"
            basis = (f"both filings work at {s['station']}, but the build windows don't overlap: {s['first']}'s ends "
                     f"{_fmt_month(s['gap_from'])}, {second}'s starts {_fmt_month(s['gap_to'])}")
        else:
            gap, basis = "timing unknown", f"both filings work at {s['station']}; a filing gives no in-service date"
        pair_rows.append((s["rank"], [
            "same station, no shared window", s["rank"], s["id"],
            f"{UTILITY_SHORT.get(s['a_utility'], s['a_utility'])} x {UTILITY_SHORT.get(s['b_utility'], s['b_utility'])}",
            s["a"], pa["name"], s["b"], pb["name"], None, None, 0.0, gap, starts(pa, pb), s["station"],
            _date(pa["in_service"]), _date(pb["in_service"]), None, basis, src(pa), src(pb),
        ]))
    rows = [r for _, r in sorted(pair_rows, key=lambda x: x[0])]
    for r in cal["projects"]:
        if not r["start"]:
            continue
        pairs = [f"OVL_{ranks[pid]}" for pid in r["pairs"] if pid in ranks]
        rows.append([
            "build window", r["best_rank"], r["id"], r["utility_name"], r["id"], r["name"], None, None,
            _date(r["start"]), _date(r["end"]), r["months"], CAL_NOW_WORDS.get(r["now"]), r["start_as"], None,
            _date(r["in_service"]), None, ", ".join(pairs) or None, r["basis"], src(r), None,
        ])
    n_build = sum(1 for r in cal["projects"] if r["start"])
    n_st = len(cal["stations"])
    note = (f"{len(cal['shared'])} shared windows among {cal['counts']['flagged']} flagged pairs"
            + (f", {n_st} same-station pair{'s' if n_st != 1 else ''} without one" if n_st else "")
            + f", and {n_build} build windows ({cal['counts']['start_derived']} with the start derived)")
    return cols, rows, note


def _export_tables(st: dict, prm: dict) -> dict:
    res = _compute(st, prm)
    overlaps = res["overlaps"]
    chosen = set(prm["a"]) | set(prm["b"])
    return {
        "projects": _project_table(st, overlaps, chosen, prm["window_months"]),
        "overlaps": _overlap_table(st, overlaps, prm["method"]),
        "set_aside": _set_aside_table(st),
        "calendar": _calendar_table(st, prm),
        "total_pairs": res["total_pairs"],
    }


def _settings_line(prm: dict) -> str:
    def names(codes):
        return " + ".join(UTILITIES[c][0] for c in codes)

    how = "closest points" if prm["method"] == "closest" else "centers (Sperry's method)"
    return (f"{names(prm['a'])} x {names(prm['b'])}; an overlap is a pair whose {how} are within {limit_text(prm['max_km'])}; "
            f"build windows as filed, else {prm['window_months']} months before in-service")


def _about_rows(st: dict, prm: dict, t: dict) -> list[tuple[str, str]]:
    fault = None
    if FAULT_FILE.exists():
        try:
            fault = _read_json(FAULT_FILE).get("headline")
        except (OSError, ValueError, AttributeError):
            fault = None
    closest = prm["method"] == "closest"
    rows = [
        ("Disclaimer", EXPORT_DISCLAIMER),
        ("Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
         + f" by Overload (pipeline data built {st['doc'].get('built_at') or 'n/a'})"),
        ("Comparison", _settings_line(prm)),
        ("Counts", f"{len(t['projects'][1])} validated projects; {len(t['overlaps'][1])} flagged overlaps out of {t['total_pairs']:,} "
                   f"cross-utility pairs; {len(t['set_aside'][1])} records set aside by the checks (sheet set_aside, with reasons)"),
        ("Sheets", "projects and overlaps use exactly the columns of Sperry Tech's Projects_Overlaps.xlsx, in their order "
                   "(projects A-Q, overlaps A-I, gray headers). Overload's columns follow to the right (blue headers)."),
        ("distance_mi", "miles between the two projects' closest points (a line is its route or straight segment, a substation "
                        "its point); center_distance_mi is Sperry's center-to-center distance" if closest
         else "miles between the two projects' centers (Sperry's method); closest_points_mi is the closest-point distance"),
        ("time_gap (day)", "days between the two in-service dates, as in Sperry's sheet"),
        ("lat_center / lon_center", "Sperry's own formula: the midpoint of the located endpoints, or the one located endpoint"),
        ("overlap_1..3", "the project's top three partners by rank; overlap_count and overlaps_all cover every flagged pair"),
        ("in_service_date", "a real date cell; Excel serial numbers and two-digit years in the sources were normalized "
                            "(in_service_as_filed keeps the text as filed)"),
        ("confidence", "location confidence: high, medium or low. Low means 'in this area', not 'at this fence line'; "
                       "match_a / match_b say how each point was found"),
        ("build_window_start / _end", "the build window each overlap was scored with: as filed when window_filed is yes, else "
                                      f"assumed {prm['window_months']} months before in-service (the Comparison setting)"),
        ("shared_window", "for pairs whose build windows overlap (windows_overlap_months > 0): whether that shared period is still "
                          f"ahead, open now, or ended before today as filed; '{NO_SHARED_WINDOW}' when the windows don't overlap "
                          "(window_gap_days says how far apart); blank when a project has no in-service date"),
        ("same_station", "the substation both projects work at, when an endpoint of each resolves to the same OpenStreetMap "
                         "feature (same_station_osm links it); these pairs are ranked before every distance tier, then by score"),
        ("in_sperry_example", "for the six pairs in Sperry's worked example, their own id for the pair (e.g. 'Sperry OVL_1'); "
                              "their numbering, not this sheet's overlap_id, which follows our rank"),
        ("calendar", f"the coordination calendar ({t['calendar'][2]}): a 'shared window' row for each flagged pair whose build "
                     "windows overlap (the months both cover, exactly the windows_overlap_months the overlaps sheet gives; same-station "
                     "pairs name the station; start_as says when a shared window rests on a derived start) and a 'same station, "
                     "no shared window' row for a same-station pair whose windows don't overlap (status gives the gap), in rank "
                     "order, then a 'build window' row for every compared project with an in-service date: "
                     "start to in-service as filed, start_as 'derived' where the filing gives no start (the Comparison setting "
                     "before in-service). Dates as filed; planned dates can change. The same calendar downloads as .ics from the page"),
    ]
    for s in st["doc"].get("sources") or []:
        rows.append(("Source", f"{s.get('title')} {s.get('url') or ''}".strip()))
    rows += [
        ("Map data", OSM_ATTRIBUTION + " State outlines: U.S. Census Bureau cb_2024_us_state_20m (public domain)."),
        ("Checks", "every record passed the pipeline's blocking checks; checks_passed / checks_run and check_warnings say how it fared"),
    ]
    if fault:
        rows.append(("Fault test", f"{fault} (backend/demo/gridlock/faults.py; data/fault_report.json)"))
    rows.append(("Rebuild", "backend/venv/Scripts/python backend/demo/gridlock/build.py"))
    return rows


# --- SpreadsheetML -------------------------------------------------------------------------------------------------

_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
EXCEL_EPOCH = date(1899, 12, 30)
# cellXfs: 0 normal, 1 Sperry's header, 2 date, 3 coordinate, 4 two decimals, 5 integer, 6 three decimals, 7 wrapped,
# 8 title, 9 Overload's header
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="3"><numFmt numFmtId="164" formatCode="m/d/yyyy"/><numFmt numFmtId="165" formatCode="0.000000"/>'
    '<numFmt numFmtId="166" formatCode="0.000"/></numFmts>'
    '<fonts count="3"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="14"/><name val="Calibri"/><family val="2"/></font></fonts>'
    '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE7E6E6"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFDDEBF7"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="10">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="1" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)
_NUM_STYLE = {
    "lat_a": 3, "lon_a": 3, "lat_b": 3, "lon_b": 3, "lat_center": 3, "lon_center": 3, "distance_mi": 4,
    "closest_points_mi": 4, "center_distance_mi": 4, "closest_points_km": 6, "cost_usd": 5, "time_gap (day)": 5,
    "window_gap_days": 5,
}
_NS_MAIN = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _col_letter(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xs(v) -> str:
    return _xml_escape(_XML_BAD.sub("", str(v))[:32000])


def _cell(ref: str, v, style: int = 0, row: int = 0) -> str:
    s = f' s="{style}"' if style else ""
    if v is None or v == "":
        return ""
    if isinstance(v, _Formula):
        val = "" if v.value is None else f"<v>{v.value!r}</v>"
        return f'<c r="{ref}"{s}><f>{_xs(v.expr.format(row=row))}</f>{val}</c>'
    if isinstance(v, bool):
        v = "yes" if v else "no"
    if isinstance(v, date):
        return f'<c r="{ref}" s="2"><v>{(v - EXCEL_EPOCH).days}</v></c>'
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return ""
        return f'<c r="{ref}"{s}><v>{v!r}</v></c>'
    return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{_xs(v)}</t></is></c>'


def _sheet_xml(cols: list[tuple], rows: list[list], selected: bool = False) -> str:
    names = [c for c, _ in cols]
    widths = []
    for k, name in enumerate(names):
        cells = [r[k].value if isinstance(r[k], _Formula) else r[k] for r in rows[:400]]
        longest = max([len(name)] + [len(str(v)) for v in cells if v is not None])
        widths.append(min(60, max(9, longest + 2)))
    last = _col_letter(len(cols) - 1)
    tab = ' tabSelected="1"' if selected else ""
    out = [
        _XML_HEAD, f"<worksheet {_NS_MAIN} {_NS_R}>",
        f'<dimension ref="A1:{last}{len(rows) + 1}"/>',
        f'<sheetViews><sheetView{tab} workbookViewId="0">'
        '<pane xSplit="1" ySplit="1" topLeftCell="B2" activePane="bottomRight" state="frozen"/></sheetView></sheetViews>',
        '<sheetFormatPr defaultRowHeight="15"/>',
        "<cols>" + "".join(f'<col min="{k + 1}" max="{k + 1}" width="{w}" customWidth="1"/>' for k, w in enumerate(widths)) + "</cols>",
        "<sheetData>",
        '<row r="1">' + "".join(_cell(f"{_col_letter(k)}1", n, 9 if kind == "extra" else 1) for k, (n, kind) in enumerate(cols)) + "</row>",
    ]
    for i, r in enumerate(rows, start=2):
        cells = "".join(_cell(f"{_col_letter(k)}{i}", v, _NUM_STYLE.get(names[k], 0), i) for k, v in enumerate(r))
        out.append(f'<row r="{i}">{cells}</row>')
    out.append("</sheetData>")
    out.append(f'<autoFilter ref="A1:{last}{max(2, len(rows) + 1)}"/>')
    out.append("</worksheet>")
    return "".join(out)


def _about_xml(rows: list[tuple[str, str]]) -> str:
    body = ['<row r="1">' + _cell("A1", "Overload: GridLock export (Sperry Tech table format)", 8) + "</row>"]
    for i, (k, v) in enumerate(rows, start=3):
        body.append(f'<row r="{i}">' + _cell(f"A{i}", k, 1) + _cell(f"B{i}", v, 7) + "</row>")
    return (
        f"{_XML_HEAD}<worksheet {_NS_MAIN} {_NS_R}>"
        f'<dimension ref="A1:B{len(rows) + 2}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/><cols><col min="1" max="1" width="24" customWidth="1"/>'
        '<col min="2" max="2" width="120" customWidth="1"/></cols><sheetData>' + "".join(body) + "</sheetData></worksheet>"
    )


def _xlsx(sheets: list[tuple[str, str, str | None]], active: int, title: str) -> bytes:
    """sheets: [(name, worksheet xml, autofilter range like 'A1:AQ195' or None)]."""
    n = len(sheets)
    ct = (
        f'{_XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, n + 1))
        + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>"
    )
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    rels = (
        f'{_XML_HEAD}<Relationships xmlns="{pkg}">'
        f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/>'
        f'<Relationship Id="rId2" Type="{pkg}/metadata/core-properties" Target="docProps/core.xml"/>'
        f'<Relationship Id="rId3" Type="{_REL}/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )
    wb_rels = (
        f'{_XML_HEAD}<Relationships xmlns="{pkg}">'
        + "".join(f'<Relationship Id="rId{i}" Type="{_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
        + f'<Relationship Id="rId{n + 1}" Type="{_REL}/styles" Target="styles.xml"/></Relationships>'
    )
    defined = []
    for i, (name, _xml, rng) in enumerate(sheets):
        if rng:
            (c0, r0), (c1, r1) = (re.match(r"([A-Z]+)(\d+)", x).groups() for x in rng.split(":"))
            defined.append(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
                           f"{name}!${c0}${r0}:${c1}${r1}</definedName>")
    workbook = (
        f"{_XML_HEAD}<workbook {_NS_MAIN} {_NS_R}>"
        f'<bookViews><workbookView activeTab="{active}"/></bookViews><sheets>'
        + "".join(f'<sheet name="{_xs(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _x, _r) in enumerate(sheets, start=1))
        + "</sheets>" + (f"<definedNames>{''.join(defined)}</definedNames>" if defined else "") + "</workbook>"
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = (
        f"{_XML_HEAD}"
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{_xs(title)}</dc:title><dc:creator>Overload</dc:creator><dc:description>{_xs(EXPORT_DISCLAIMER)}</dc:description>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>'
    )
    app = (f"{_XML_HEAD}<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties\">"
           "<Application>Overload</Application></Properties>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, (_name, xml, _rng) in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return buf.getvalue()


def build_workbook(st: dict, prm: dict) -> bytes:
    t = _export_tables(st, prm)
    sheets = []
    # Sperry's two sheets first and unchanged; the calendar after the records set aside, so `overlaps` stays tab 1
    for name in ("projects", "overlaps", "set_aside", "calendar"):
        cols, rows = t[name][:2]
        rng = f"A1:{_col_letter(len(cols) - 1)}{max(2, len(rows) + 1)}"
        sheets.append((name, _sheet_xml(cols, rows, selected=(name == "overlaps")), rng))
    sheets.append(("about", _about_xml(_about_rows(st, prm, t)), None))
    return _xlsx(sheets, active=1, title="Overload GridLock export: projects and overlaps")


# --- CSV, GeoJSON ---------------------------------------------------------------------------------------------------


def _plain(v):
    """A cell's value for CSV / GeoJSON: formulas as their value, dates as ISO text."""
    if isinstance(v, _Formula):
        v = v.value
    if isinstance(v, date):
        return v.isoformat()
    return v


def _csv_safe(v):
    """Text a spreadsheet would run as a formula gets a leading apostrophe (CSV injection)."""
    if isinstance(v, str) and v and (v[0] in "=+@\t\r" or (v[0] == "-" and not re.match(r"-\d", v))):
        return "'" + v
    return v


def build_csv(st: dict, prm: dict, table: str) -> bytes:
    cols, rows = _export_tables(st, prm)[table][:2]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([c for c, _ in cols])
    for r in rows:
        w.writerow(["" if _plain(v) is None else _csv_safe(_plain(v)) for v in r])
    return buf.getvalue().encode("utf-8-sig")  # the BOM makes Excel read the titles' en dashes as UTF-8


def build_geojson(st: dict, prm: dict) -> dict:
    cols, rows = _export_tables(st, prm)["projects"]
    names = [c for c, _ in cols]
    feats = []
    for p, r in zip(_sorted_projects(st), rows):
        coords = _geometry_coords(p)
        if not coords:
            continue
        geom = {"type": "Point", "coordinates": coords[0]} if len(coords) == 1 else {"type": "LineString", "coordinates": coords}
        props = {n: _plain(v) for n, v in zip(names, r)}
        props["tags"] = p.get("tags")
        feats.append({"type": "Feature", "id": p["id"], "geometry": geom, "properties": props})
    return {
        "type": "FeatureCollection",
        "name": "overload_gridlock_projects",
        "disclaimer": EXPORT_DISCLAIMER,
        "attribution": OSM_ATTRIBUTION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "comparison": _settings_line(prm),
        "params": prm,
        "sources": [{"title": s.get("title"), "url": s.get("url")} for s in st["doc"].get("sources") or []],
        "features": feats,
    }


def _export_name(prm: dict, ext: str, table: str | None = None) -> str:
    lim = f"{SPERRY_MI}mi" if abs(prm["max_km"] - MAX_KM_DEFAULT) < 1e-9 else f"{prm['max_km']:g}km"
    who = f"{'-'.join(prm['a'])}_{'-'.join(prm['b'])}_{lim}" + ("" if prm["method"] == "closest" else "_centers")
    return f"Projects_Overlaps_Overload_{who}.{ext}" if table is None else f"Overload_{table}_{who}.{ext}"


def _download(body: bytes, media: str, filename: str) -> Response:
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/api/gridlock/export.xlsx")
@limiter.limit("30/minute")
def export_xlsx(
    request: Request,
    max_km: float = Query(MAX_KM_DEFAULT),
    window_months: int = Query(WINDOW_DEFAULT),
    method: str = Query("closest"),
    a: str = Query("DESC"),
    b: str = Query("GPC"),
):
    prm = _params(max_km, window_months, method, a, b)
    st = _load()
    body = build_workbook(st, prm)  # built per request (~20 ms): the about sheet's time is this file's, not the day's first
    return _download(body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _export_name(prm, "xlsx"))


@router.get("/api/gridlock/export.csv")
@limiter.limit("30/minute")
def export_csv(
    request: Request,
    table: str = Query("projects"),
    max_km: float = Query(MAX_KM_DEFAULT),
    window_months: int = Query(WINDOW_DEFAULT),
    method: str = Query("closest"),
    a: str = Query("DESC"),
    b: str = Query("GPC"),
):
    table = (table or "").strip().lower()
    if table not in EXPORT_TABLES:
        raise HTTPException(status_code=422, detail=f"table must be one of: {', '.join(EXPORT_TABLES)}")
    prm = _params(max_km, window_months, method, a, b)
    st = _load()
    body = build_csv(st, prm, table)
    return _download(body, "text/csv; charset=utf-8", _export_name(prm, "csv", table))


@router.get("/api/gridlock/export.geojson")
@limiter.limit("30/minute")
def export_geojson(
    request: Request,
    max_km: float = Query(MAX_KM_DEFAULT),
    window_months: int = Query(WINDOW_DEFAULT),
    method: str = Query("closest"),
    a: str = Query("DESC"),
    b: str = Query("GPC"),
):
    prm = _params(max_km, window_months, method, a, b)
    st = _load()
    body = json.dumps(_finite(build_geojson(st, prm)), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _download(body, "application/geo+json", _export_name(prm, "geojson", "projects"))


_fault_cache: dict = {"key": None, "bytes": None}


@router.get("/api/gridlock/fault-test")
@limiter.limit("60/minute")
def fault_test(request: Request):
    """The committed fault-injection report: the kinds of bad record this pipeline has met, injected into real validated
    records and pushed through the same checks. Regenerate with backend/demo/gridlock/faults.py."""
    key = _file_key(FAULT_FILE)
    if key is None:
        raise HTTPException(status_code=404, detail="No fault report yet: run backend/venv/Scripts/python backend/demo/gridlock/faults.py")
    if _fault_cache["key"] != key:
        try:
            doc = _read_json(FAULT_FILE)
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=500, detail=f"fault_report.json could not be read: {e}") from e
        _fault_cache["bytes"] = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        _fault_cache["key"] = key
    return Response(content=_fault_cache["bytes"], media_type="application/json")
