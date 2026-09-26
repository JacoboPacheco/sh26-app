"""Incident briefing engine: the after-action report behind the Review Stage.

POST /api/briefing takes a case (grid.CaseIn, plus an optional catastrophe `preset`) and answers,
top to bottom, what happened and what would fix it. Every figure comes from the power-flow engine
(powerflow.py); the AI tracks (bulletin.py, ask.py) only write prose around this report's facts, and
check_text() rejects any number that is not one of them.

- timeline: every cascade step with the line that tripped, how loaded it was, which lines picked up
  its flow (the "why"), the areas that newly went dark and the running people count.
- root_cause: the first line to fail, its loading with and without the campus, and what caused it
  (the campus, the last straw on a hot line, the heat, or the storm).
- areas: the hardest-hit places (areas are the towns the synthetic substations are named after).
- fixes (M2), no_fix + recovery (M3): see those sections below.
- facts: the fact sheet the writer and Ask tracks quote (<= 160, stable dotted keys).

Honesty: a synthetic grid model (Breakthrough Energy / Texas A&M, CC-BY 4.0), never a real utility's
network; every people and cost number is an estimate; every fix is verified by re-running the
engine; "no fix exists" is a computed conclusion.

Python API (imported by bulletin.py and ask.py; signatures frozen):
    report_for(body) -> dict            sync + cached; call through run_in_threadpool
    report_by_key(key) -> dict | None
    fact_sheet(report, extra_facts=None, max_lines=160) -> str
    allowed_numbers(report, extra_facts=None) -> set[str]
    check_text(text, report, lang="en", extra_facts=None) -> (ok, reason, numbers_checked)
    FORBIDDEN                           compiled regex
    what_if(report_key, change) -> dict
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

import grid
from grid import DEFAULT_REGION, REGIONS, CaseIn, SiteIn, _case_header, check_case, grid_at, region_code, site_headroom
from limiter import limiter
from powerflow import OVER_PCT, Grid

try:  # the core agent added area_of to powerflow; keep working without it
    from powerflow import area_of
except ImportError:  # pragma: no cover

    def area_of(name: str) -> str:
        base = re.sub(r"\s+\d+$", "", str(name or "")).strip().lower()
        return re.sub(r"\b\w", lambda m: m.group(0).upper(), base)


router = APIRouter(tags=["briefing"])
log = logging.getLogger("uvicorn.error")

VERSION = 1
DEMO = Path(__file__).parent / "demo"
CREDIT = "Breakthrough Energy / Texas A&M, CC-BY 4.0"
MAX_PRESET_TRIPS = 2500
BUDGET_MIN, BUDGET_MAX = 300, 1800
TOP_AREAS = 8
STORM_LISTED = 12
MAX_FACTS = 160
CACHE_SIZE = 64

# the heat-wave clock's presets (frontend/src/features/heat/presets.js)
LEVELS = [
    (0.62, "at 3 AM", "3 AM"),
    (0.82, "at 9 AM", "9 AM"),
    (1.0, "at the 4 PM summer peak", "4 PM"),
    (1.04, "during a heat wave", "heat wave"),
    (1.08, "at the height of a heat wave", "height of a heat wave"),
]
DAY_LEVELS = (0.62, 0.82, 1.0, 1.04)


def load_word(f: float) -> str:
    for lf, word, _ in LEVELS:
        if abs(f - lf) < 0.005:
            return word
    return f"at {round(f * 100)}% of the summer peak load"


def level_name(f: float) -> str:
    for lf, _, name in LEVELS:
        if abs(f - lf) < 0.005:
            return name
    return f"{round(f * 100)}% of peak"


# ------------------------------------------------------------------------------------ helpers
def _title(name: str) -> str:
    """ "NORTH FORT MYERS 6" -> "North Fort Myers 6"."""
    return re.sub(r"[A-Za-z]+(?:'[A-Za-z]+)?", lambda m: m.group(0).capitalize(), str(name))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "x"


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


def _people_per_mw(code: str) -> float:
    fn = getattr(grid, "people_per_mw", None)
    if fn is not None:
        return float(fn(code))
    return 700 * 2.5  # before the core agent's population data: homes per MW x people per home


def _people(g: Grid, mw: float, code: str) -> int:
    fn = getattr(g, "people", None)
    if fn is not None and getattr(g, "people_per_mw", 0):
        return int(fn(mw))
    return int(round(max(float(mw), 0.0) * _people_per_mw(code)))


def _population(code: str) -> int | None:
    pop = getattr(grid, "POPULATION", None)
    return int(pop[code]) if pop and code in pop else None


def _big(n: float) -> str:
    """Newspaper style for people: 783,883 · 2.1 million."""
    n = int(round(n))
    if n >= 10_000_000:
        return f"{n / 1e6:.1f} million"
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}".rstrip("0").rstrip(".") + " million"
    return f"{n:,}"


def _mw(v: float) -> str:
    return f"{v:,.0f} MW"


class _Budget:
    def __init__(self, ms: float):
        self.t0 = time.perf_counter()
        self.ms = float(ms)

    def elapsed(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

    def left(self) -> float:
        return self.ms - self.elapsed()


def _ms(t0: float) -> int:
    return int(round((time.perf_counter() - t0) * 1000))


class EngineGap(RuntimeError):
    """The engine hit a known gap (the _balance divide-by-zero on statewide storms)."""


# ------------------------------------------------------------------------------------ the case
class BriefingIn(CaseIn):
    preset: str | None = None  # a catastrophes.json id
    budget_ms: int = 1800  # wall budget for the fix search + recovery; clamped to 300..1800


@dataclass
class _Case:
    code: str
    g: Grid
    sites: list
    buses: list
    extra: np.ndarray
    trip: list
    upgrades: dict
    firm: bool
    preset: dict | None
    header: dict
    rate: np.ndarray
    active: np.ndarray  # after the storm, before any cascade
    key: str
    body: dict  # the normalized request (what `apply` deltas build on)
    people_per_mw: float = 0.0
    extras: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- presets + corridor
_PRESETS_RAW = json.loads((DEMO / "catastrophes.json").read_text(encoding="utf-8"))["presets"]
PRESETS: dict[str, dict] = {p["id"]: p for p in _PRESETS_RAW}
_preset_trips: dict[str, list[int]] = {}
_preset_lock = threading.Lock()

KM_PER_DEG = 111.19


def _point_seg_dist(p, a, b):
    ab = b - a
    denom = np.einsum("...i,...i->...", ab, ab)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(denom > 1e-12, np.einsum("...i,...i->...", p - a, ab) / denom, 0.0)
    t = np.clip(t, 0.0, 1.0)
    closest = a + t[..., None] * ab
    return np.hypot(*np.moveaxis(p - closest, -1, 0))


def _cross(o, a, b):
    return (a[..., 0] - o[..., 0]) * (b[..., 1] - o[..., 1]) - (a[..., 1] - o[..., 1]) * (b[..., 0] - o[..., 0])


def _seg_seg(p0, p1, q0, q1):
    P0, P1 = p0[:, None, :], p1[:, None, :]
    Q0, Q1 = q0[None, :, :], q1[None, :, :]
    d = np.minimum.reduce([_point_seg_dist(P0, Q0, Q1), _point_seg_dist(P1, Q0, Q1), _point_seg_dist(Q0, P0, P1), _point_seg_dist(Q1, P0, P1)])
    crosses = (_cross(Q0, Q1, P0) * _cross(Q0, Q1, P1) < 0) & (_cross(P0, P1, Q0) * _cross(P0, P1, Q1) < 0)
    return np.where(crosses, 0.0, d)


def corridor(g: Grid, points: list, radius_km: float, code: str = DEFAULT_REGION) -> list[int]:
    """Branch ids between two different substations within `radius_km` of the path. Florida uses
    hurricane.py's projection (cos 27.7 deg) so the same track hits the same lines; other states
    use the track's mean latitude. No cap."""
    pts = np.asarray(points, dtype=float)
    lat0 = 27.7 if code == "FL" else float(pts[:, 1].mean())
    cos = math.cos(math.radians(lat0))
    fs, ts = g.bus_sub_idx[g.f], g.bus_sub_idx[g.t]
    between = np.flatnonzero(fs != ts)
    p0 = np.column_stack([g.sub_lon[fs[between]] * cos * KM_PER_DEG, g.sub_lat[fs[between]] * KM_PER_DEG])
    p1 = np.column_stack([g.sub_lon[ts[between]] * cos * KM_PER_DEG, g.sub_lat[ts[between]] * KM_PER_DEG])
    q = np.column_stack([pts[:, 0] * cos * KM_PER_DEG, pts[:, 1] * KM_PER_DEG])
    dist = np.empty(len(between))
    for s in range(0, len(between), 1024):
        e = min(s + 1024, len(between))
        dist[s:e] = _seg_seg(p0[s:e], p1[s:e], q[:-1], q[1:]).min(axis=1)
    return [int(b) for b in g.br_ids[between[dist <= radius_km]]]


def preset_trip(preset: dict) -> list[int]:
    """The union of every track's corridor, cached per preset."""
    pid = preset["id"]
    with _preset_lock:
        hit = _preset_trips.get(pid)
    if hit is not None:
        return hit
    g = grid_at(1.0, preset["region"])
    out: set[int] = set()
    for tr in preset["tracks"]:
        out.update(corridor(g, tr["points"], float(tr["radius_km"]), preset["region"]))
    trip = sorted(out)[:MAX_PRESET_TRIPS]
    with _preset_lock:
        _preset_trips[pid] = trip
    return trip


def presets_for(region: str | None = None) -> list[dict]:
    code = region_code(region) if region else None
    out = []
    for p in _PRESETS_RAW:
        if code and p["region"] != code:
            continue
        with _preset_lock:
            n = len(_preset_trips[p["id"]]) if p["id"] in _preset_trips else None
        out.append(
            {
                "id": p["id"],
                "region": p["region"],
                "name": p["name"],
                "description": p["description"],
                "tracks": p["tracks"],
                "load_factor": p["load_factor"],
                "campus": p.get("campus"),
                "hypothetical": True,
                "lines_out": n,
            }
        )
    return out


# ----------------------------------------------------------------------------- normalize + key
def _case_key(code, sites, lf, trip, upgrades, firm, preset_id) -> str:
    norm = {
        "v": VERSION,
        "region": code,
        "sites": [[round(s.lat, 5), round(s.lon, 5), round(s.mw, 3)] for s in sites],
        "lf": round(float(lf), 2),
        "trip": sorted(int(b) for b in trip) if not preset_id else [],
        "upgrades": sorted([int(k), round(float(v), 3)] for k, v in upgrades.items()),
        "firm": bool(firm),
        "preset": preset_id,
    }
    return hashlib.sha1(json.dumps(norm, sort_keys=True).encode()).hexdigest()


def build_case(body: BriefingIn) -> _Case:
    """Validate (422s via grid.check_case) and expand a preset. Raises HTTPException."""
    preset = None
    if body.preset:
        preset = PRESETS.get(str(body.preset).strip())
        if preset is None:
            raise HTTPException(status_code=422, detail=f"Unknown catastrophe preset {body.preset!r}")
        code = region_code(body.region)
        if preset["region"] != code:
            raise HTTPException(status_code=422, detail=f"The preset {preset['id']!r} is for {REGIONS[preset['region']]['name']}, not {REGIONS[code]['name']}")
        upd = {"trip": [], "load_factor": float(preset["load_factor"])}
        if body.lat is None and body.lon is None and body.mw is None and not body.sites:
            camp = preset.get("campus")
            upd.update({"lat": camp["lat"], "lon": camp["lon"], "mw": camp["mw"]} if camp else {})
        body = body.model_copy(update=upd)
    if not (math.isfinite(body.load_factor)):
        raise HTTPException(status_code=422, detail="Load level must be a number")
    g, sites, trip, upgrades = check_case(body)
    code = region_code(body.region)
    if preset is not None:
        trip = preset_trip(preset)
    extra, header = _case_header(g, sites, trip, upgrades)
    buses = [g.site_bus(s.lat, s.lon) for s in sites]
    rate = g.rates_with(upgrades)
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[int(bid)]] = False
    key = _case_key(code, sites, g.load_factor, trip, upgrades, body.firm, preset["id"] if preset else None)
    main = sites[0] if sites else None
    norm_body = {
        "region": code,
        "lat": main.lat if main else None,
        "lon": main.lon if main else None,
        "mw": main.mw if main else None,
        "sites": [{"lat": s.lat, "lon": s.lon, "mw": s.mw} for s in sites[1:]],
        "load_factor": g.load_factor,
        "trip": [] if preset else list(trip),
        "upgrades": {str(k): float(v) for k, v in upgrades.items()},
        "firm": bool(body.firm),
        "preset": preset["id"] if preset else None,
    }
    return _Case(code, g, sites, buses, extra, list(trip), dict(upgrades), bool(body.firm), preset, header, rate, active, key, norm_body, _people_per_mw(code))


# ----------------------------------------------------------------------------- engine wrappers
def _cascade(c: _Case, g: Grid, extra: np.ndarray, trip=None, upgrades=None, firm_buses=None) -> dict:
    """grid.cascade_case with the case's trips/upgrades by default; people filled in when the
    running powerflow predates the people estimate. A ZeroDivisionError is the known _balance gap."""
    trip = c.trip if trip is None else trip
    upgrades = c.upgrades if upgrades is None else upgrades
    try:
        if firm_buses:
            try:
                r = g.cascade_case(extra, trip, upgrades, firm_buses=firm_buses)
            except TypeError:
                return {}
        else:
            r = g.cascade_case(extra, trip, upgrades)
    except (ZeroDivisionError, FloatingPointError) as e:
        raise EngineGap(str(e)) from e
    if "people" not in r:
        r["people"] = _people(g, r["lost_mw"], c.code)
        for s in r["steps"]:
            s["people"] = _people(g, s["lost_mw"], c.code)
    return r


def _solve(c: _Case, g: Grid, active: np.ndarray, extra, rate):
    try:
        return g.solve(active, extra, rate)
    except (ZeroDivisionError, FloatingPointError) as e:
        raise EngineGap(str(e)) from e


def _over(st) -> np.ndarray:
    return np.flatnonzero(st.active & (st.loading_pct > OVER_PCT + 1e-6))


def _line(g: Grid, i: int) -> dict:
    fs, ts = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
    fname, tname = _title(g.sub_name[fs]), _title(g.sub_name[ts])
    transformer = fs == ts
    return {
        "id": int(g.br_ids[i]),
        "label": f"the {fname} transformer" if transformer else f"the {fname} to {tname} line",
        "from_sub": int(g.sub_ids[fs]),
        "to_sub": int(g.sub_ids[ts]),
        "from_area": area_of(g.sub_name[fs]),
        "to_area": area_of(g.sub_name[ts]),
        "kv": float(g.br_kv[i]),
        "transformer": bool(transformer),
    }


def _line_km(g: Grid, i: int) -> float:
    fs, ts = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
    if fs == ts:
        return 0.0
    return _km(float(g.sub_lat[fs]), float(g.sub_lon[fs]), float(g.sub_lat[ts]), float(g.sub_lon[ts]))


def _group_areas(c: _Case, g: Grid, sub_mw) -> dict[str, dict]:
    """[(sub id, MW lost)] -> {area: {mw, sub_ids}}."""
    out: dict[str, dict] = {}
    for sid, mw in sub_mw:
        i = g.sub_index.get(int(sid))
        if i is None:
            continue
        a = area_of(g.sub_name[i])
        d = out.setdefault(a, {"mw": 0.0, "sub_ids": []})
        d["mw"] += float(mw)
        d["sub_ids"].append(int(sid))
    return out


# ----------------------------------------------------------------------------- report sections
def _timeline(c: _Case, inc: dict) -> tuple[list[dict], object]:
    """Replay the cascade's steps (the same deterministic solves) to record, per trip, how loaded
    the line was and which lines picked up its flow. Returns (rows, the step-0 state)."""
    g = c.g
    active = c.active.copy()
    st = _solve(c, g, active, c.extra, c.rate)
    first = st
    rows = []
    replay_ok = True
    for s in inc["steps"]:
        row = {
            "n": int(s["n"]),
            "action": s.get("action", "trip" if s["n"] else "storm"),
            "lines": [],
            "storm_lines": None,
            "held_line": None,
            "why": [],
            "newly_dark": [],
            "people_cum": int(s.get("people", 0)),
            "lost_mw_cum": float(s.get("lost_mw", 0.0)),
        }
        if row["action"] == "storm":
            idx = [g.br_index[int(b)] for b in c.trip if int(b) in g.br_index]
            idx.sort(key=lambda i: (-float(g.br_kv[i]), int(g.br_ids[i])))
            row["storm_lines"] = {"count": len(c.trip), "listed": [_line(g, i) for i in idx[:STORM_LISTED]]}
        elif row["action"] == "shed":
            hl = s.get("held_line")
            if hl is not None and int(hl) in g.br_index:
                row["held_line"] = {**_line(g, g.br_index[int(hl)]), "shed_mw_cum": s.get("shed_mw")}
            replay_ok = False  # the operator's per-bus cut isn't in the payload; stop replaying
        else:
            new_active = active.copy()
            for bid in s.get("tripped", []):
                i = g.br_index[int(bid)]
                info = _line(g, i)
                if replay_ok:
                    info["pct_before"] = round(float(st.loading_pct[i]), 1)
                    info["flow_mw"] = int(round(abs(float(st.flow[i]))))
                else:
                    info["pct_before"] = None
                    info["flow_mw"] = None
                row["lines"].append(info)
                new_active[i] = False
            if replay_ok:
                nst = _solve(c, g, new_active, c.extra, c.rate)
                delta = np.where(nst.active, np.abs(nst.flow) - np.abs(st.flow), -np.inf)
                top = np.argsort(-delta)[:3]
                row["why"] = [
                    {"id": int(g.br_ids[i]), "label": _line(g, i)["label"], "delta_mw": int(round(float(delta[i]))), "pct_after": round(float(nst.loading_pct[i]), 1)}
                    for i in top
                    if np.isfinite(delta[i]) and delta[i] > 0.5
                ]
                st = nst
            active = new_active
        areas = _group_areas(c, g, s.get("newly_affected", []))
        row["newly_dark"] = sorted(
            ({"area": a, "people": _people(g, d["mw"], c.code), "mw": round(d["mw"], 1), "sub_ids": d["sub_ids"]} for a, d in areas.items()),
            key=lambda x: -x["mw"],
        )
        rows.append(row)
    return rows, first


def _areas(c: _Case, inc: dict, rows: list[dict]) -> list[dict]:
    g = c.g
    first_step: dict[str, int] = {}
    for r in rows:
        for d in r["newly_dark"]:
            first_step.setdefault(d["area"], r["n"])
    groups = _group_areas(c, g, inc.get("affected", {}).items())
    out = []
    for a, d in groups.items():
        idx = [g.sub_index[s] for s in d["sub_ids"]]
        lons, lats = g.sub_lon[idx], g.sub_lat[idx]
        out.append(
            {
                "area": a,
                "people": _people(g, d["mw"], c.code),
                "mw": round(d["mw"], 1),
                "sub_ids": sorted(d["sub_ids"]),
                "center": [round(float(lons.mean()), 4), round(float(lats.mean()), 4)],
                "bbox": [round(float(lons.min()), 4), round(float(lats.min()), 4), round(float(lons.max()), 4), round(float(lats.max()), 4)],
                "first_step": first_step.get(a),
            }
        )
    out.sort(key=lambda x: (-x["people"], x["area"]))
    return out[:TOP_AREAS]


def _root_cause(c: _Case, first, inc: dict, floor: dict) -> dict:
    g = c.g
    over = _over(first)
    people_inc = int(inc["people"])
    people_wo = int(floor["people"])
    due = max(people_inc - people_wo, 0)
    base = {
        "line": None,
        "pct_with": None,
        "pct_without": None,
        "campus_mw_on_line": None,
        "campus_share_pct": None,
        "people_incident": people_inc,
        "people_without_campus": people_wo,
        "people_due_to_campus": due,
    }
    storm = bool(c.trip)
    floor_cascades = floor["total_steps"] > 0 or floor["people"] > 0
    if not len(over):
        if storm and people_inc > 0:
            cause = "storm"
            sentence = f"The storm cut {len(c.trip):,} lines; no line went over its limit afterwards, so the outage is the storm's damage itself."
        else:
            cause = "none"
            sentence = "No line went over its limit."
        return {**base, "cause": cause, "sentence": sentence}
    i = int(over[np.argmax(first.loading_pct[over])])
    wo = _solve(c, g, c.active, None, c.rate) if c.sites else first
    f_with, f_wo = abs(float(first.flow[i])), abs(float(wo.flow[i]))
    pct_with, pct_wo = float(first.loading_pct[i]), float(wo.loading_pct[i])
    on_line = max(f_with - f_wo, 0.0) if c.sites else 0.0
    share = 100.0 * on_line / f_with if f_with > 1e-6 else 0.0
    info = _line(g, i)
    label = info["label"]
    if storm and people_wo >= 0.95 * people_inc and floor_cascades:
        cause = "storm"
    elif floor_cascades and not c.sites:
        cause = "storm" if storm else "heat"
    elif floor_cascades:
        cause = "storm" if storm else "heat"
    elif pct_wo >= 90.0:
        cause = "last_straw"
    else:
        cause = "campus"
    pw, po = f"{pct_with:.0f}%", f"{pct_wo:.0f}%"
    if cause == "campus":
        sentence = f"The first line to fail was {label} at {pw} of its rating; without the data center it would carry {po}, so the campus pushed it over."
    elif cause == "last_straw":
        sentence = f"{label[0].upper() + label[1:]} was already at {po} of its rating; the data center's {on_line:,.0f} MW on it was the last straw, taking it to {pw}."
    elif cause == "heat":
        sentence = f"At this load level the grid overloads without any data center: {label} reaches {po} of its rating on its own."
    else:
        sentence = (
            f"The storm cut {len(c.trip):,} lines and pushed {label} to {pw} of its rating"
            + (f" ({po} without the data center)" if c.sites else "")
            + (f"; without the data center the same {_big(people_wo)} people lose power (estimate)." if c.sites else ".")
        )
    return {
        **base,
        "line": {k: info[k] for k in ("id", "label", "from_sub", "to_sub", "from_area", "to_area", "kv", "transformer")},
        "pct_with": round(pct_with, 1),
        "pct_without": round(pct_wo, 1),
        "campus_mw_on_line": round(on_line, 1),
        "campus_share_pct": round(min(max(share, 0.0), 100.0), 1),
        "cause": cause,
        "sentence": sentence,
    }


def _kind(c: _Case, inc: dict, floor: dict) -> str:
    if c.preset:
        return "catastrophe"
    if c.trip:
        return "storm"
    if inc["total_steps"] == 0 and inc["people"] == 0:
        return "calm"
    if c.g.load_factor > 1.0 + 1e-9 and (floor["total_steps"] > 0 or floor["people"] > 0):
        return "heat"
    return "cascade"


def _headline(c: _Case, kind: str, inc: dict, areas: list[dict]) -> str:
    n = inc["total_steps"]
    ppl = inc["people"]
    total_mw = sum(s.mw for s in c.sites)
    where = c.header.get("sub_area") or "the site"
    if len(c.sites) == 1:
        who = f"A {total_mw:,.0f} MW data center at {where}"
    elif c.sites:
        who = f"{len(c.sites)} data centers ({total_mw:,.0f} MW in total)"
    else:
        who = None
    lost = f"an estimated {_big(ppl)} people lost power" if ppl else "no one lost power"
    if kind == "catastrophe":
        return f"{c.preset['name']}: {len(c.trip):,} lines down and an estimated {_big(ppl)} people without power."
    if kind == "storm":
        more = f" and {n} more tripped" if n else ""
        return f"A storm knocked out {len(c.trip):,} lines{more}; {lost}."
    if kind == "calm":
        if who:
            return f"{who} fits {load_word(c.g.load_factor)}: no line goes over its limit."
        return f"The grid holds {load_word(c.g.load_factor)}: no line goes over its limit."
    if kind == "heat":
        return f"{load_word(c.g.load_factor)[0].upper() + load_word(c.g.load_factor)[1:]} the grid overloads on its own: a {n}-step cascade, and {lost}."
    step = f"a {n}-step cascade" if n != 1 else "a 1-step cascade"
    return f"{who or 'The load'} set off {step} {load_word(c.g.load_factor)}; {lost}."


# ----------------------------------------------------------------------------- facts
def _fact(key, label, value, unit="", estimate=False, source="engine", text=None) -> dict:
    if text is None:
        if isinstance(value, str):
            text = value
        elif unit == "people":
            text = f"{int(value):,} people" + (" (estimate)" if estimate else "")
        elif unit == "%":
            text = f"{value:.1f}%" + (" (estimate)" if estimate else "")
        elif unit == "MW":
            text = f"{value:,.1f} MW".replace(".0 MW", " MW") + (" (estimate)" if estimate else "")
        elif unit in ("MVA", "km"):
            text = f"{value:,.1f} {unit}".replace(f".0 {unit}", f" {unit}")
        elif unit == "USD":
            text = f"${value:,.0f}" + (" (estimate)" if estimate else "")
        elif isinstance(value, float) and not float(value).is_integer():
            text = f"{value:,.2f}".rstrip("0").rstrip(".") + (f" {unit}" if unit else "")
        else:
            text = f"{int(value):,}" + (f" {unit}" if unit else "")
    return {"key": key, "label": label, "value": value, "unit": unit, "text": text, "estimate": bool(estimate), "source": source}


def _facts(c: _Case, rep: dict) -> list[dict]:
    """The fact sheet, highest priority first, capped at MAX_FACTS. Steps go last (they're many)."""
    ev, rc = rep["event"], rep["root_cause"]
    F: list[dict] = []
    code = c.code
    F.append(_fact("meta.region_name", "State", REGIONS[code]["name"]))
    F.append(_fact("meta.model", "Model", f"a synthetic grid model of {REGIONS[code]['name']}, not any utility's network"))
    F.append(_fact("meta.credit", "Model credit", CREDIT))
    pop = _population(code)
    if pop:
        F.append(_fact("meta.population", f"Residents of {REGIONS[code]['name']}", pop, "people", True, "census"))
    F.append(_fact("event.steps", "Cascade steps (lines tripped one after another)", int(ev["steps"]), "steps"))
    F.append(_fact("event.people_out", "People without power at the end", int(ev["people"]), "people", True))
    F.append(_fact("event.peak_people", "People without power at the worst moment", int(ev["peak_people"]), "people", True))
    F.append(_fact("event.lost_mw", "Load lost at the end", float(ev["lost_mw"]), "MW", True))
    if ev.get("people_share_pct") is not None:
        F.append(_fact("event.people_share_pct", "Share of the state's residents without power", float(ev["people_share_pct"]), "%", True))
    if c.sites:
        F.append(_fact("event.campus_mw", "Data center size" if len(c.sites) == 1 else "Data centers, total size", float(sum(s.mw for s in c.sites)), "MW"))
        F.append(_fact("event.site_area", "Data center location (the town its substation is named after)", c.header.get("sub_area") or ""))
        if c.header.get("headroom_mw") is not None:
            F.append(_fact("event.room_mw", "Room at the site before the first overload (linear estimate)", float(c.header["headroom_mw"]), "MW", True))
    F.append(_fact("event.load_factor", "Load level", round(float(c.g.load_factor), 2), "x summer peak", text=f"{c.g.load_factor:.2f} x the summer peak ({load_word(c.g.load_factor)})"))
    F.append(_fact("event.load_pct", "Load level, percent of the summer peak", round(float(c.g.load_factor) * 100), "%", text=f"{round(c.g.load_factor * 100)}%"))
    if c.trip:
        F.append(_fact("event.storm_lines_out", "Lines knocked out by the storm", len(c.trip), "lines"))
    if c.preset:
        F.append(_fact("event.preset", "Scenario", c.preset["name"] + " (hypothetical)"))
    if rc.get("line"):
        F.append(_fact("cause.line", "First line to fail", rc["line"]["label"]))
        F.append(_fact("cause.pct_with", "Its loading when it failed", rc["pct_with"], "%"))
        if c.sites:
            F.append(_fact("cause.pct_without", "Its loading without the data center", rc["pct_without"], "%"))
            F.append(_fact("cause.campus_mw_on_line", "Data center MW on that line", rc["campus_mw_on_line"], "MW"))
            F.append(_fact("cause.campus_share_pct", "Data center's share of that line's flow", rc["campus_share_pct"], "%"))
    F.append(_fact("cause.kind", "Cause", rc["cause"]))
    if c.sites:
        F.append(_fact("cause.people_without_campus", "People without power with no data center (same case)", int(rc["people_without_campus"]), "people", True))
        F.append(_fact("cause.people_due_to_campus", "People who lose power because of the data center", int(rc["people_due_to_campus"]), "people", True))
    for a in rep["areas"]:
        s = _slug(a["area"])
        F.append(_fact(f"area.{s}.name", "Area", a["area"]))
        F.append(_fact(f"area.{s}.people", f"People without power in {a['area']}", int(a["people"]), "people", True))
        F.append(_fact(f"area.{s}.mw", f"Load lost in {a['area']}", float(a["mw"]), "MW", True))
        if a.get("first_step") is not None:
            F.append(_fact(f"area.{s}.first_step", f"Step when {a['area']} first lost power", int(a["first_step"]), "step"))
    for fx in rep.get("fixes", []):
        fam = fx["family"]
        F.append(_fact(f"fix.{fam}.action", f"Fix ({fam})", fx["action"]))
        F.append(_fact(f"fix.{fam}.verdict", f"Fix ({fam}) verified result", fx["verdict"]))
        oc = fx.get("outcome") or {}
        if oc.get("people") is not None:
            F.append(_fact(f"fix.{fam}.people", f"People without power with this fix ({fam})", int(oc["people"]), "people", True))
        if oc.get("steps") is not None:
            F.append(_fact(f"fix.{fam}.steps", f"Cascade steps with this fix ({fam})", int(oc["steps"]), "steps"))
        d = fx.get("detail") or {}
        for k, unit in (("mw", "MW"), ("mva", "MVA"), ("km", "km"), ("lines", "lines"), ("onsite_mw", "MW"), ("room_mw", "MW")):
            if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool):
                F.append(_fact(f"fix.{fam}.{k}", f"Fix ({fam}) {k.replace('_', ' ')}", d[k], unit))
    if rep.get("firm_note"):
        fn = rep["firm_note"]
        F.append(_fact("firm.shed_mw", "If the campus were kept on (firm): neighbors' load cut", float(fn["shed_mw"]), "MW", True))
        F.append(_fact("firm.people", "If the campus were kept on (firm): people without power", int(fn["people"]), "people", True))
    if rep.get("bound"):
        b = rep["bound"]
        F.append(_fact("bound.people", "People cut off even with unlimited lines and no data center", int(b["people"]), "people", True))
        F.append(_fact("bound.share_pct", "Share of the outage no line upgrade can reach", float(b["share_pct"]), "%", True))
    if rep.get("split"):
        sp_ = rep["split"]
        F.append(_fact("split.physical", "People cut off by physical damage", int(sp_["physical"]), "people", True))
        F.append(_fact("split.cascade", "People lost to the cascade", int(sp_["cascade"]), "people", True))
        F.append(_fact("split.campus", "People lost because of the data center", int(sp_["campus"]), "people", True))
    rec = rep.get("recovery")
    if rec:
        F.append(_fact("recovery.method", "Restoration check", "line limits applied (verified)" if rec["method"] == "lp" else "connectivity only, line limits not applied"))
        if rec.get("wave0_people_back") is not None:
            F.append(_fact("recovery.wave0.people_back", "People back by careful re-energizing, before any repair", int(rec["wave0_people_back"]), "people", True))
        for w in rec["waves"]:
            F.append(_fact(f"recovery.wave.{w['n']}.lines", f"Wave {w['n']}: lines rebuilt (cumulative)", int(w["lines_total"]), "lines"))
            F.append(_fact(f"recovery.wave.{w['n']}.km", f"Wave {w['n']}: km of line rebuilt (cumulative)", float(w["km_total"]), "km"))
            F.append(_fact(f"recovery.wave.{w['n']}.people_back", f"Wave {w['n']}: people back (cumulative)", int(w["people_back"]), "people", True))
        for h in rec.get("hardening", []):
            F.append(_fact(f"harden.{h['k']}.people_kept_on", f"Hardening {h['k']} lines keeps this many on", int(h["people_kept_on"]), "people", True))
    cost = rep.get("cost")
    if cost:
        for k in ("blackout_usd", "upgrade_usd", "campus_bill_usd_per_year"):
            if cost.get(k):
                F.append(_fact(f"cost.{k}", k.replace("_", " "), float(cost[k]), "USD", True, cost.get("source", "engine-estimate")))
        if cost.get("duration_h_assumed"):
            F.append(_fact("cost.duration_h_assumed", "Assumed outage length for the cost estimate", float(cost["duration_h_assumed"]), "hours", True, "assumption"))
    # steps last, as many as fit
    for r in rep["timeline"]:
        if len(F) >= MAX_FACTS - 1:
            break
        n = r["n"]
        if r["action"] == "storm":
            F.append(_fact(f"step.{n}.line", "Step 0", f"the storm knocked out {len(c.trip):,} lines"))
        elif r["lines"]:
            ln = r["lines"][0]
            F.append(_fact(f"step.{n}.line", f"Step {n}: line that tripped", ln["label"]))
            if ln.get("pct_before") is not None and len(F) < MAX_FACTS - 2:
                F.append(_fact(f"step.{n}.pct_before", f"Step {n}: its loading when it tripped", ln["pct_before"], "%"))
                F.append(_fact(f"step.{n}.flow_mw", f"Step {n}: flow on it", ln["flow_mw"], "MW"))
        elif r.get("held_line"):
            F.append(_fact(f"step.{n}.line", f"Step {n}: line held in by cutting load", r["held_line"]["label"]))
        if len(F) < MAX_FACTS:
            F.append(_fact(f"step.{n}.people_cum", f"Step {n}: people without power so far", int(r["people_cum"]), "people", True))
    seen = set()
    out = []
    for f in F:
        if f["key"] in seen:
            continue
        seen.add(f["key"])
        out.append(f)
    return out[:MAX_FACTS]


def fact_sheet(report: dict, extra_facts: list | None = None, max_lines: int = MAX_FACTS) -> str:
    lines = []
    for f in list(report.get("facts", [])) + list(extra_facts or []):
        lines.append(f"[{f['key']}] {f['label']}: {f['text']} — {f['source']}")
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


# ----------------------------------------------------------------------------- validator
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_UTILITIES = (
    r"\b(FPL|TECO|JEA|OUC|FMPA|GRU|NextEra|FPUC|KUA|LCEC|ERCOT|FERC|NERC|CAISO|PJM|MISO|NYISO|SPP|ISO-NE|ConEd|Con Edison|PG&E|PGE|SCE|SDG&E|Entergy|Oncor|CenterPoint|Dominion|Xcel|Ameren|Exelon|ComEd|Southern Company|Georgia Power|TVA|BPA)\b"
    r"|(?i:\bflorida power\b|\bduke energy\b|\btampa electric\b|\bgulf power\b|\borlando utilities\b|\bseminole electric\b"
    r"|\blakeland electric\b|\bflorida public utilities\b|\bkissimmee utility\b|\blee county electric\b|\bclay electric\b"
    r"|\bwithlacoochee\b|\bpeace river electric\b|\bpacific gas\b|\bconsolidated edison\b|\bamerican electric power\b)"
)
_STORMS = r"\b(Andrew|Charley|Frances|Jeanne|Wilma|Irma|Michael|Ian|Idalia|Helene|Milton|Katrina|Harvey|Sandy|Maria|Rita|Ike|Hugo|Dorian|Matthew|Laura|Ida|Opal|Dennis|Elsa|Nicole|Debby|Beryl|Francine|Isaias)\b"
FORBIDDEN = re.compile(
    _UTILITIES
    + r"|\b[Hh]urricane [A-Z][a-z]+|\b[Hh]urac[aá]n [A-Z][a-z]+|\b[Tt]ropical [Ss]torm [A-Z][a-z]+"
    + r"|" + _STORMS
    + r"|(?i:emergency alert|alerta de emergencia|this is not a test|this is not a drill|esto no es (?:una prueba|un simulacro)"
    r"|national weather service|servicio meteorol[oó]gico nacional|evacuat|evacu[aá]|shelter in place|shelter-in-place|refugiarse en el lugar"
    r"|emergency broadcast|breaking news|[uú]ltima hora)"
    + r"|\bEAS\b|\bFEMA\b|\bNWS\b|\bNOAA\b"
    + r"|(?<![\d,.])\b(?:19|20)\d{2}\b(?![\d,.]|\s*(?:MW|MVA|GW|km|%|percent|por ciento|megawatts?|megavatios?|people|personas|lines|l[ií]neas|substations?|subestaciones))"
)
_DURATION = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(hours?|days?|weeks?|months?|horas?|d[ií]as?|semanas?|meses|mes)\b", re.I)
_DURATION_WORDS = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|a few|several|many|un|una|dos|tres|cuatro|cinco|varios|varias|muchos|muchas)\s+(?:days|weeks|months|d[ií]as|semanas|meses)\b",
    re.I,
)
_SCALE_WORD = re.compile(r"\b(million|millions|thousand|thousands|mill[oó]n|millones|mil)\b", re.I)


ROUND_TOL = 0.03  # "about 800,000" for 783,883 passes; "900,000" for 869,608 does not


def _norm(tok: str) -> str:
    t = tok.replace(",", "")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t or "0"


def _forms(v: float) -> set[str]:
    out: set[str] = set()
    try:
        a = abs(float(v))
    except (TypeError, ValueError):
        return out
    if not math.isfinite(a):
        return out
    out.add(_norm(f"{a:.0f}"))
    out.add(_norm(f"{a:.1f}"))
    out.add(_norm(f"{a:.2f}"))
    out.add(_norm(str(int(math.floor(a)))))
    # coarser roundings only where a newspaper would use them: within ROUND_TOL of the value
    for k in range(1, 7):
        r = round(a, -k)
        if r > 0 and abs(r - a) <= ROUND_TOL * a:
            out.add(_norm(f"{r:.0f}"))
    for s in (1e3, 1e6, 1e9):
        x = a / s
        for d in (0, 1, 2):
            r = round(x, d)
            if r > 0 and abs(r - x) <= ROUND_TOL * x:
                out.add(_norm(f"{x:.{d}f}"))
    return out


def allowed_numbers(report: dict, extra_facts: list | None = None) -> set[str]:
    allowed: set[str] = {str(i) for i in range(0, 11)}
    ev = report.get("event") or {}
    for i in range(0, int(ev.get("steps") or 0) + 2):
        allowed.add(str(i))
    for f in list(report.get("facts", [])) + list(extra_facts or []):
        v = f.get("value")
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            allowed |= _forms(v)
        for tok in _NUM.findall(str(f.get("text", "")) + " " + (v if isinstance(v, str) else "")):
            allowed |= _forms(float(_norm(tok))) if _norm(tok).replace(".", "", 1).isdigit() else set()
    return allowed


def _labels(report: dict, extra_facts: list | None) -> list[str]:
    out = []
    for f in list(report.get("facts", [])) + list(extra_facts or []):
        if isinstance(f.get("value"), str) and f.get("key", "").split(".")[0] in ("area", "cause", "step", "fix", "event"):
            out.append(f["value"])
    for a in report.get("areas", []):
        out.append(a["area"])
    return sorted(set(out), key=len, reverse=True)


def check_text(text: str, report: dict, lang: str = "en", extra_facts: list | None = None, require: tuple = ()) -> tuple[bool, str | None, int]:
    """Validate AI prose against the report: no number outside the facts, no forbidden phrase (real
    utilities, named storms, alert phrasing, dates, durations), required words present."""
    if not isinstance(text, str) or not text.strip():
        return False, "empty text", 0
    scrub = text
    for lab in _labels(report, extra_facts):
        if lab:
            scrub = scrub.replace(lab, " ")
    m = FORBIDDEN.search(scrub)
    if m:
        return False, f"forbidden phrase: {m.group(0)!r}", 0
    dur_ok = set()
    cost = report.get("cost") or {}
    if cost.get("duration_h_assumed"):
        dur_ok = _forms(cost["duration_h_assumed"])
    for dm in _DURATION.finditer(text):
        unit = dm.group(2).lower()
        if not (unit.startswith("hour") or unit.startswith("hora")) or _norm(dm.group(1)) not in dur_ok:
            return False, f"duration not in the facts: {dm.group(0)!r}", 0
    dw = _DURATION_WORDS.search(text)
    if dw:
        return False, f"duration not in the facts: {dw.group(0)!r}", 0
    for sm in _SCALE_WORD.finditer(text):
        before = text[: sm.start()].rstrip()
        if not before or not before[-1].isdigit():
            return False, f"'{sm.group(0)}' must follow a figure", 0
    allowed = allowed_numbers(report, extra_facts)
    n = 0
    for tok in _NUM.findall(text):
        t = _norm(tok.rstrip(",."))
        n += 1
        if t not in allowed:
            return False, f"number {tok.rstrip(',.')} is not in the facts", n
    low = text.lower()
    for word in require:
        alts = [w.lower() for w in (word if isinstance(word, (tuple, list)) else (word,))]
        if not any(a in low for a in alts):
            return False, f"missing required word {alts[0]!r}", n
    return True, None, n


# ----------------------------------------------------------------------------- the report
_cache: "OrderedDict[str, dict]" = OrderedDict()
_ctx: "OrderedDict[str, _Case]" = OrderedDict()
_cache_lock = threading.Lock()


def _remember(key: str, rep: dict, c: _Case) -> None:
    with _cache_lock:
        _cache[key] = rep
        _ctx[key] = c
        _cache.move_to_end(key)
        _ctx.move_to_end(key)
        while len(_cache) > CACHE_SIZE:
            old = next(iter(_cache))
            del _cache[old]
            _ctx.pop(old, None)


def report_by_key(key: str) -> dict | None:
    with _cache_lock:
        rep = _cache.get(str(key))
        if rep is not None:
            _cache.move_to_end(str(key))
        return rep


def _banner(code: str) -> str:
    name = REGIONS[code]["name"]
    return f"SIMULATION · synthetic grid model of {name} ({CREDIT}) · not any utility's network · every people and cost number is an estimate."


def report_for(body: BriefingIn) -> dict:
    """The whole report for a case. Sync and cached (LRU 64 by key)."""
    t_all = time.perf_counter()
    c = build_case(body)
    budget_ms = int(min(max(int(body.budget_ms or BUDGET_MAX), BUDGET_MIN), BUDGET_MAX))
    with _cache_lock:
        hit = _cache.get(c.key)
    if hit is not None and not (hit.get("unchecked") and budget_ms > hit.get("_budget_ms", BUDGET_MAX)):
        with _cache_lock:
            _cache.move_to_end(c.key)
        return hit
    try:
        rep = _build(c, budget_ms, t_all)
    except EngineGap as e:
        log.warning("briefing: engine gap on %s: %s", c.key, e)
        raise HTTPException(
            status_code=503,
            detail="This catastrophe needs an engine patch that is not in yet" if c.preset else "This storm hits a case the engine can't solve yet — try another path",
        )
    _remember(c.key, rep, c)
    return rep


def _build(c: _Case, budget_ms: int, t_all: float) -> dict:
    B = _Budget(budget_ms)
    timing: dict[str, int] = {}
    g = c.g

    t0 = time.perf_counter()
    inc = _cascade(c, g, c.extra)
    timing["incident"] = _ms(t0)

    t0 = time.perf_counter()
    floor = _cascade(c, g, np.zeros(g.n)) if c.sites else inc
    timing["floor"] = _ms(t0)

    rows, first = _timeline(c, inc)
    peak = max([int(inc["people"])] + [int(s.get("people", 0)) for s in inc["steps"]])
    pop = _population(c.code)
    event = {
        "steps": int(inc["total_steps"]),
        "capped": bool(inc.get("capped")),
        "outcome": inc["outcome"],
        "lost_mw": float(inc["lost_mw"]),
        "people": int(inc["people"]),
        "peak_people": peak,
        "people_share_pct": round(100.0 * inc["people"] / pop, 1) if pop else None,
        "campus_lost_power": bool(inc.get("site_cut_off")),
        "storm_lines_out": len(c.trip),
    }
    kind = _kind(c, inc, floor)
    areas = _areas(c, inc, rows)
    root = _root_cause(c, first, inc, floor)

    case_out = {
        **c.header,
        "trip_count": len(c.trip),
        "load_factor": g.load_factor,
        "load_word": load_word(g.load_factor),
        "preset": {"id": c.preset["id"], "name": c.preset["name"]} if c.preset else None,
        "body": c.body,
    }
    rep = {
        "version": VERSION,
        "key": c.key,
        "region": c.code,
        "region_name": REGIONS[c.code]["name"],
        "banner": _banner(c.code),
        "kind": kind,
        "verdict": "nothing_happened" if (event["steps"] == 0 and event["people"] == 0) else "partly",
        "case": case_out,
        "headline": {"text": _headline(c, kind, inc, areas)},
        "event": event,
        "timeline": rows,
        "root_cause": root,
        "areas": areas,
        "hospitals": None,
        "cost": None,
        "fixes": [],
        "best_fix": None,
        "firm_note": None,
        "bound": None,
        "split": None,
        "no_fix": None,
        "recovery": None,
        "replay": {**c.header, "region": c.code, **inc},
        "facts": [],
        "timing_ms": {},
        "unchecked": [],
    }
    rep["facts"] = _facts(c, rep)
    timing["total"] = _ms(t_all)
    rep["timing_ms"] = timing
    rep["_budget_ms"] = budget_ms
    return rep


# ----------------------------------------------------------------------------- what-if (Ask tools)
def _ctx_for(report_key: str) -> _Case:
    with _cache_lock:
        c = _ctx.get(str(report_key))
    if c is None:
        raise KeyError("Report expired — fetch the briefing again")
    return c


def what_if(report_key: str, change: dict) -> dict:
    """One engine run on a changed copy of a cached report's case (the Ask track's tools).
    change: {mw} | {factor} | {load_factor} | {area} | {fix: 'best'}. Raises KeyError (unknown or
    expired key) or ValueError (a change that doesn't apply), with a sentence to show."""
    c = _ctx_for(report_key)
    rep = report_by_key(report_key) or {}
    if not isinstance(change, dict) or len([k for k in change if k in ("mw", "factor", "load_factor", "area", "fix")]) != 1:
        raise ValueError("Give exactly one change: mw, factor, load_factor, area or fix")
    g = c.g
    sites = [SiteIn(lat=s.lat, lon=s.lon, mw=s.mw) for s in c.sites]
    buses = list(c.buses)
    upgrades = dict(c.upgrades)
    delta: dict = {}
    where = c.header.get("sub_area") or ""
    if "mw" in change or "factor" in change:
        if not sites:
            raise ValueError("This scenario has no data center to resize")
        if "mw" in change:
            mw = float(change["mw"])
            if not (math.isfinite(mw) and grid.MW_MIN <= mw <= grid.MW_MAX):
                raise ValueError(f"Size must be between {grid.MW_MIN} and {grid.MW_MAX:,} MW")
            sites[0] = SiteIn(lat=sites[0].lat, lon=sites[0].lon, mw=mw)
            delta = {"mw": mw}
            label = f"{mw:,.0f} MW at {where}"
        else:
            fct = float(change["factor"])
            if not (math.isfinite(fct) and 0.1 <= fct <= 2.0):
                raise ValueError("The size factor must be between 0.1 and 2")
            sites = [SiteIn(lat=s.lat, lon=s.lon, mw=max(grid.MW_MIN, min(grid.MW_MAX, round(s.mw * fct, 1)))) for s in sites]
            delta = {"mw": sites[0].mw, "sites": [{"lat": s.lat, "lon": s.lon, "mw": s.mw} for s in sites[1:]]}
            label = f"{sum(s.mw for s in sites):,.0f} MW ({fct:g} x the size)"
    elif "load_factor" in change:
        lf = float(change["load_factor"])
        if not (math.isfinite(lf) and grid.LOAD_FACTOR_MIN <= lf <= grid.LOAD_FACTOR_MAX):
            raise ValueError(f"Load level must be between {grid.LOAD_FACTOR_MIN} and {grid.LOAD_FACTOR_MAX}")
        g = grid_at(lf, c.code)
        delta = {"load_factor": g.load_factor}
        label = f"the same case {load_word(g.load_factor)}"
    elif "area" in change:
        if not sites:
            raise ValueError("This scenario has no data center to move")
        want = str(change["area"]).strip().casefold()
        g1 = grid_at(1.0, c.code)
        cand = [i for i, n in enumerate(g1.sub_name) if area_of(n).casefold() == want]
        if not cand:
            raise ValueError(f"No substation in this model is named after {change['area']!r}")
        best = max(cand, key=lambda i: (site_headroom(g1, g1.connect_bus(i)), float(g1.bus_kv[g1.connect_bus(i)]), -int(g1.sub_ids[i])))
        lat, lon = float(g.sub_lat[best]), float(g.sub_lon[best])
        sites[0] = SiteIn(lat=lat, lon=lon, mw=sites[0].mw)
        buses[0] = g.connect_bus(best)
        delta = {"lat": round(lat, 4), "lon": round(lon, 4)}
        where = area_of(g.sub_name[best])
        label = f"{sites[0].mw:,.0f} MW at {where}"
    else:
        if str(change.get("fix")) != "best":
            raise ValueError("The only fix tool is fix: 'best'")
        bf = rep.get("best_fix")
        if bf is None or not rep.get("fixes"):
            raise ValueError("This scenario has no verified fix to apply")
        fx = rep["fixes"][bf]
        ap = fx.get("apply") or {}
        delta = dict(ap)
        if ap.get("load_factor") is not None:
            g = grid_at(float(ap["load_factor"]), c.code)
        if "mw" in ap:
            if ap["mw"] is None:
                sites, buses = [], []
            else:
                sites[0] = SiteIn(lat=float(ap.get("lat", sites[0].lat)), lon=float(ap.get("lon", sites[0].lon)), mw=float(ap["mw"]))
        elif "lat" in ap:
            sites[0] = SiteIn(lat=float(ap["lat"]), lon=float(ap["lon"]), mw=sites[0].mw)
        if sites and ("lat" in ap):
            buses[0] = g.site_bus(sites[0].lat, sites[0].lon)
        if "sites" in ap and sites:
            sites = sites[:1] + [SiteIn(**s) for s in ap["sites"]]
            buses = [g.site_bus(s.lat, s.lon) for s in sites]
        if "upgrades" in ap:
            upgrades = {int(k): float(v) for k, v in ap["upgrades"].items()}
        label = f"the best fix: {fx['action']}"
    extra = g.extra_load([(b, s.mw) for b, s in zip(buses, sites)])
    rate = g.rates_with(upgrades)
    st = _solve(c, g, c.active, extra, rate)
    over = _over(st)
    if len(over):
        r = _cascade(c, g, extra, upgrades=upgrades)
        steps, people, lost, outcome = int(r["total_steps"]), int(r["people"]), float(r["lost_mw"]), r["outcome"]
    else:
        steps, lost = 0, float(st.lost_existing_mw)
        people, outcome = _people(g, lost, c.code), ("islanded" if st.lost_mw > 0.5 else "settled")
    facts = [
        _fact("whatif.change", "What-if change", label),
        _fact("whatif.steps", "What-if: cascade steps", steps, "steps"),
        _fact("whatif.people", "What-if: people without power", people, "people", True),
        _fact("whatif.lost_mw", "What-if: load lost", round(lost, 1), "MW", True),
        _fact("whatif.over_lines", "What-if: lines over their limit before any trip", int(len(over)), "lines"),
    ]
    if sites:
        facts.append(_fact("whatif.mw", "What-if: data center size", float(sum(s.mw for s in sites)), "MW"))
    facts.append(_fact("whatif.load_factor", "What-if: load level", round(g.load_factor, 2), "x summer peak", text=f"{g.load_factor:.2f} x the summer peak ({load_word(g.load_factor)})"))
    return {
        "label": f"Ran the engine: {label} → {steps} steps, {people:,} people (estimate)",
        "case_delta": delta,
        "steps": steps,
        "people": people,
        "lost_mw": round(lost, 1),
        "outcome": outcome,
        "over_lines": [_line(g, int(i))["label"] for i in over[np.argsort(-st.loading_pct[over])][:5]],
        "facts": facts,
    }


# ----------------------------------------------------------------------------- endpoints
def _public(rep: dict) -> dict:
    return {k: v for k, v in rep.items() if not k.startswith("_")}


@router.post("/api/briefing")
@limiter.limit("30/minute")
async def post_briefing(request: Request, body: BriefingIn):
    rep = await run_in_threadpool(report_for, body)
    return _public(rep)


@router.get("/api/briefing/presets")
def get_presets(region: str = Query(DEFAULT_REGION)):
    return {"presets": presets_for(region)}
