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
from scipy.optimize import linprog  # imported here so the first restoration plan doesn't pay ~0.5 s for it

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
WHY_STEPS = 8  # steps whose 'why' (lines that picked up the flow) is replayed; the writer groups the rest
WHY_STEPS_BIG = 3
MAX_FACTS = 160
CACHE_SIZE = 64

# the heat-wave clock's presets (frontend/src/features/heat/presets.js)
LEVELS = [
    (0.62, "at 3 AM", "3 AM"),
    (0.82, "at 9 AM", "9 AM"),
    (1.0, "at the 4 PM summer peak", "4 PM"),
    (1.04, "during a heat wave", "in a heat wave"),
    (1.08, "at the height of a heat wave", "at the height of a heat wave"),
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


def _a(n: int) -> str:
    """The article before a spoken number: 'an 8-step', 'an 18-step', 'a 9-step'."""
    s = str(int(n))
    return "an" if s.startswith("8") or s in ("11", "18") or (len(s) in (5, 8) and s[:2] in ("11", "18")) else "a"


def _join(items: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    items = [str(x) for x in items if x]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


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
    budget_ms: int = 1800  # wall budget for open-ended searches (towns tried for a move beyond the first 12); 300..1800


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
_ORDINAL = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth"}


def _why(g: Grid, nst, delta: np.ndarray, tripped_labels: set) -> list[dict]:
    """The top 3 pieces of equipment that picked up a tripped line's flow. Parallel units share a name
    (two North Fort Myers 6 transformers), so they are grouped — 'the 2 Cocoa 8 transformers', each
    +105 MW — and a unit parallel to the one that just tripped is 'the other ...'."""
    groups: dict[str, dict] = {}
    for i in np.argsort(-delta)[:24]:
        d = float(delta[i])
        if not (np.isfinite(d) and d > 0.5):
            break
        label = _line(g, int(i))["label"]
        grp = groups.get(label)
        if grp is None:
            if len(groups) >= 3:
                continue
            groups[label] = {"id": int(g.br_ids[i]), "ids": [int(g.br_ids[i])], "base": label, "delta_mw": int(round(d)), "pct_after": round(float(nst.loading_pct[i]), 1)}
        else:
            grp["ids"].append(int(g.br_ids[i]))
            grp["pct_after"] = max(grp["pct_after"], round(float(nst.loading_pct[i]), 1))
    out = []
    for grp in groups.values():
        n = len(grp["ids"])
        rest = grp.pop("base")[len("the ") :]
        other = rest in {t[len("the ") :] for t in tripped_labels}
        if n == 1:
            label = f"the other {rest}" if other else f"the {rest}"
        else:
            label = f"the {n} other {rest}s" if other else f"the {n} {rest}s"
        grp["label"] = label
        grp["count"] = n
        out.append(grp)
    return out


def _timeline(c: _Case, inc: dict, why_steps: int = WHY_STEPS) -> tuple[list[dict], object]:
    """Per step: the line that tripped and how loaded it was (from the previous state's hot list,
    exact), the areas that newly lost power, and — for the first WHY_STEPS trips — which lines picked
    up its flow, by replaying those steps (the same deterministic solves). Returns (rows, the step-0
    state)."""
    g = c.g
    active = c.active.copy()
    st = _solve(c, g, active, c.extra, c.rate)
    first = st
    rows = []
    replay_ok = True
    seen_labels: dict[str, int] = {}  # parallel equipment shares a name: the second one to trip says so
    prev_hot = {int(g.br_ids[i]): float(first.loading_pct[i]) for i in np.flatnonzero(first.active & (first.loading_pct > 80.0))}
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
            tripped_labels = set()
            for bid in s.get("tripped", []):
                i = g.br_index[int(bid)]
                info = _line(g, i)
                base_label = info["label"]
                tripped_labels.add(base_label)
                k = seen_labels.get(base_label, 0) + 1
                seen_labels[base_label] = k
                if k > 1:
                    info["label"] = "the " + _ORDINAL.get(k, f"{k}th") + " " + base_label[len("the ") :]
                    info["parallel_n"] = k
                pct = prev_hot.get(int(bid))
                info["pct_before"] = round(pct, 1) if pct is not None else None
                info["flow_mw"] = int(round(pct / 100.0 * float(c.rate[i]))) if pct is not None else None
                row["lines"].append(info)
                new_active[i] = False
            if replay_ok and row["n"] <= why_steps:
                nst = _solve(c, g, new_active, c.extra, c.rate)
                delta = np.where(nst.active, np.abs(nst.flow) - np.abs(st.flow), -np.inf)
                row["why"] = _why(g, nst, delta, tripped_labels)
                st = nst
            active = new_active
        prev_hot = {int(h["id"]): float(h["pct"]) for h in s.get("hot", [])}
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


def _storm_word(c: _Case) -> str:
    """'storms' for a preset with several tracks (the twenty-storm season), else 'storm'."""
    return "storms" if c.preset and len(c.preset.get("tracks") or []) > 1 else "storm"


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
            sw = _storm_word(c)
            sentence = f"The {sw} cut {len(c.trip):,} lines; no line went over its limit afterwards, so the whole outage is the {sw}' damage itself." if sw == "storms" else (
                f"The storm cut {len(c.trip):,} lines; no line went over its limit afterwards, so the whole outage is the storm's damage itself."
            )
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
    Label = label[0].upper() + label[1:]
    # a storm case whose first overload is there before the storm too (no campus): that's the heat's
    pct_no_storm = None
    if storm and g.load_factor > 1.0 + 1e-9:
        ns = _solve(c, g, np.ones(g.m, dtype=bool), None, c.rate)
        pct_no_storm = float(ns.loading_pct[i])
    if storm and pct_no_storm is not None and pct_no_storm > OVER_PCT and pct_wo > OVER_PCT:
        cause = "heat"
    elif floor_cascades:
        cause = "storm" if storm else "heat"
    elif pct_wo >= 90.0:
        cause = "last_straw"
    else:
        cause = "campus"
    # how far the nearest campus is from the line it overloaded (power flows by physics, not by distance)
    campus_km = None
    if c.sites and on_line > 0.5:
        fs, ts = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
        campus_km = round(min(_km(s.lat, s.lon, float(g.sub_lat[k]), float(g.sub_lon[k])) for s in c.sites for k in (fs, ts)))
    dc = "the data center" if len(c.sites) <= 1 else "the data centers"
    dc_is = "The data center is" if len(c.sites) <= 1 else "The nearest data center is"
    far = f" {dc_is} about {campus_km:,} km away; its power still flows through {label}." if campus_km and campus_km >= 50 else ""
    pw, po = f"{pct_with:.0f}%", f"{pct_wo:.0f}%"
    storm_w = _storm_word(c)
    after = f"After the {storm_w} cut {len(c.trip):,} lines, " if storm else ""
    if cause == "campus":
        first_ = f"{after}the first line to fail was {label}, at {pw} of its rating."
        sentence = first_[0].upper() + first_[1:] + f" Without {dc} it would carry {po}: the new load pushed it over." + far
    elif cause == "last_straw":
        body_ = f"{after}{label} was already at {po} of its rating."
        sentence = body_[0].upper() + body_[1:] + f" {dc[0].upper() + dc[1:]} added {on_line:,.0f} MW of flow and took it to {pw}: the last straw." + far
    elif cause == "heat" and storm:
        sentence = (
            f"The {storm_w} cut {len(c.trip):,} lines, but the first overload is the heat's: {label} is already at {pct_no_storm:.0f}% of its rating "
            f"before the {storm_w} and {pw} after" + (f", with or without {dc}." if c.sites else ".")
        )
    elif cause == "heat":
        sentence = f"At this load the grid overloads with no data center at all: {label} reaches {po} of its rating on its own."
    else:
        same = abs(pct_with - pct_wo) < 0.5
        sentence = f"The {storm_w} cut {len(c.trip):,} lines. The first line to overload afterwards was {label}, at {pw} of its rating" + (
            (f"; {dc} makes no difference to it." if same else f" ({po} without {dc}).") if c.sites else "."
        )
        if c.sites:
            if people_wo >= 0.95 * people_inc:
                sentence += f" Without {dc}, about the same number of people lose power: {_big(people_wo)} (estimate)."
            else:
                sentence += f" Without {dc}, {_big(people_wo)} people (estimate) would lose power instead of {_big(people_inc)}."
    return {
        **base,
        "line": {k: info[k] for k in ("id", "label", "from_sub", "to_sub", "from_area", "to_area", "kv", "transformer")},
        "pct_with": round(pct_with, 1),
        "pct_without": round(pct_wo, 1),
        "pct_before_storm": round(pct_no_storm, 1) if pct_no_storm is not None else None,
        "campus_mw_on_line": round(on_line, 1),
        "campus_share_pct": round(min(max(share, 0.0), 100.0), 1),
        "campus_km": campus_km,
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
        return f"{c.preset['name']} (hypothetical): {len(c.trip):,} lines down and an estimated {_big(ppl)} people without power."
    if kind == "storm":
        more = f" and {n} more tripped" if n else ""
        return f"A storm knocked out {len(c.trip):,} lines{more}; {lost}."
    if kind == "calm":
        if who:
            return f"{who} fits {load_word(c.g.load_factor)}: no line goes over its limit."
        return f"The grid holds {load_word(c.g.load_factor)}: no line goes over its limit."
    if kind == "heat":
        return f"{load_word(c.g.load_factor)[0].upper() + load_word(c.g.load_factor)[1:]} the grid overloads on its own: {_a(n)} {n}-step cascade, and {lost}."
    step = f"{_a(n)} {n}-step cascade"
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
            if rc.get("campus_km"):
                F.append(_fact("cause.campus_km", "Distance from the data center to that line", rc["campus_km"], "km"))
        if rc.get("pct_before_storm") is not None:
            F.append(_fact("cause.pct_before_storm", "Its loading before the storm (the heat alone)", rc["pct_before_storm"], "%"))
    F.append(_fact("cause.kind", "Cause", rc["cause"]))
    if rc.get("sentence"):
        F.append(_fact("cause.sentence", "Why it happened (engine sentence)", rc["sentence"]))
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
    nf = rep.get("no_fix")
    if nf:
        F.append(_fact("no_fix.people", "People no fix can reach (cut off even with unlimited lines and no data center)", int(nf["people"]), "people", True))
        F.append(_fact("no_fix.fixes_save_at_most_pct", "The most any fix saves", int(nf["fixes_save_at_most_pct"]), "%", True))
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
        if rec.get("still_out_after_all"):
            F.append(_fact("recovery.still_out_after_all", "People still without power after every line is rebuilt (at this load)", int(rec["still_out_after_all"]), "people", True))
        bl = rec.get("baseline")
        if bl:
            F.append(_fact("recovery.baseline.repairs", "Repairs compared (the plan vs biggest lines first)", int(bl["repairs"]), "lines"))
            F.append(_fact("recovery.baseline.people_out", "People still out after that many repairs, biggest lines first", int(bl["people_out"]), "people", True))
            if bl["plan_better_by"] > 0:
                F.append(_fact("recovery.baseline.plan_better_by", "People the plan's order brings back beyond biggest-first", int(bl["plan_better_by"]), "people", True))
        rc_ = rec.get("campus_reconnect")
        if rc_ is not None:
            F.append(_fact("recovery.campus_reconnect", "Once every line is rebuilt, the data center can reconnect", "yes, the grid holds" if rc_["ok"] else f"no: it sets off {_a(rc_['steps'])} {rc_['steps']}-step cascade on its own"))
    cost = rep.get("cost")
    if cost:
        names = {"blackout_usd": "Cost of the blackout", "upgrade_usd": "Cost of the upgrades that prevent it", "campus_bill_usd_per_year": "The campus's yearly power bill"}
        for k, name in names.items():
            if cost.get(k):
                F.append(_fact(f"cost.{k}", f"{name} (midpoint)", float(cost[k]), "USD", True, cost.get("source", "costs.py")))
                rng = (cost.get("ranges") or {}).get(k)
                if rng:
                    F.append(_fact(f"cost.{k}_range", f"{name} (low to high)", float(rng[1]), "USD", True, cost.get("source", "costs.py"), text=f"${rng[0]:,.0f} to ${rng[1]:,.0f} (estimate)"))
        if cost.get("who_pays"):
            F.append(_fact("cost.who_pays", "Who pays", cost["who_pays"], source=cost.get("source", "costs.py")))
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


# ----------------------------------------------------------------------------- fixes (verified)
FAMILY_ORDER = ["shrink", "move", "flexible", "time_of_day", "upgrade", "onsite", "combo", "remove"]
CAMPUS_FAMILIES = {"shrink", "move", "flexible", "time_of_day", "onsite", "combo"}
FAMILY_LABEL = {
    "shrink": "Build it smaller",
    "move": "Build it elsewhere",
    "flexible": "Make it flexible",
    "time_of_day": "Change the hour",
    "upgrade": "Upgrade the lines",
    "onsite": "Generate on site",
    "combo": "Smaller, plus upgrades",
    "remove": "Don't build it here",
}
MOVE_POOL = 40  # the roomiest towns (level 1.0) tried for a move
MOVE_KEEP = 3
MOVE_MIN = 12  # towns always checked, whatever the budget
BISECT_STEPS = 8


@dataclass
class _Judge:
    event_people: int
    event_steps: int
    bound_people: int
    bound_lost: float

    def verdict(self, oc: dict) -> str:
        """holds: no line trips and nobody beyond the physical bound loses power; partly: saves at
        least 10 %; fails otherwise. Always from a run, never inferred (the cascade is non-monotone)."""
        if oc["steps"] == 0 and oc["people"] <= self.bound_people + max(1000, 0.01 * self.event_people):
            return "holds"
        if self.event_people > 0:
            return "partly" if (self.event_people - oc["people"]) >= 0.10 * self.event_people else "fails"
        return "partly" if oc["steps"] < self.event_steps else "fails"


def _floor10(x: float) -> float:
    return float(math.floor(max(x, 0.0) / 10.0) * 10.0)


def _site_mws(c: _Case, s: float) -> list[float]:
    """Every campus scaled by s; a single campus is rounded down to 10 MW."""
    if len(c.sites) == 1:
        return [_floor10(c.sites[0].mw * s)]
    return [round(site.mw * s, 1) for site in c.sites]


def _extra_for(g: Grid, buses: list[int], mws: list[float]) -> np.ndarray:
    return g.extra_load([(b, m) for b, m in zip(buses, mws) if m > 0])


def _fits(c: _Case, g: Grid, extra: np.ndarray, rate=None) -> tuple[bool, object]:
    st = _solve(c, g, c.active, extra, c.rate if rate is None else rate)
    return (not len(_over(st))), st


def _verify(c: _Case, J: _Judge, g: Grid, extra: np.ndarray, upgrades: dict | None = None) -> tuple[dict, str, str]:
    """One what-if solve; a cascade only when a line is over (then the solve is not the end state).
    Returns (outcome, verdict, how it was checked)."""
    upgrades = c.upgrades if upgrades is None else upgrades
    rate = g.rates_with(upgrades)
    ok, st = _fits(c, g, extra, rate)
    if ok:
        lost = float(st.lost_existing_mw)
        oc = {"steps": 0, "people": _people(g, lost, c.code), "lost_mw": round(lost, 1)}
        how = "solve"
    else:
        r = _cascade(c, g, extra, upgrades=upgrades)
        oc = {"steps": int(r["total_steps"]), "people": int(r["people"]), "lost_mw": float(r["lost_mw"])}
        how = "cascade"
    return oc, J.verdict(oc), how


def _max_scale(c: _Case, g: Grid, s_hi: float, rate=None) -> tuple[float, int]:
    """The largest scale s in [0, s_hi] of every campus with no line over (a solve per try; bisection
    — a single injection's flows are linear in MW between dispatch regimes). Returns (s, solves)."""
    n = 0
    s_hi = max(min(s_hi, 1.0), 0.0)
    ok, _ = _fits(c, g, _extra_for(g, c.buses, _site_mws(c, s_hi)), rate)
    n += 1
    if ok:
        return s_hi, n
    lo, hi = 0.0, s_hi
    ok0, _ = _fits(c, g, np.zeros(g.n), rate)
    n += 1
    if not ok0:
        return 0.0, n
    for _ in range(BISECT_STEPS):
        mid = (lo + hi) / 2
        ok, _ = _fits(c, g, _extra_for(g, c.buses, _site_mws(c, mid)), rate)
        n += 1
        lo, hi = (mid, hi) if ok else (lo, mid)
    return lo, n


def _size_apply(c: _Case, mws: list[float]) -> dict:
    ap = {"mw": mws[0]}
    if len(c.sites) > 1:
        ap["sites"] = [{"lat": s.lat, "lon": s.lon, "mw": m} for s, m in zip(c.sites[1:], mws[1:])]
    return ap


def _fix(family: str, action: str, verdict: str, outcome: dict | None, tradeoff: str, detail: dict, apply: dict | None, ms: int) -> dict:
    return {
        "family": family,
        "label": FAMILY_LABEL[family],
        "action": action,
        "verdict": verdict,
        "outcome": outcome,
        "tradeoff": tradeoff,
        "detail": detail,
        "apply": apply,
        "ms": ms,
    }


def _upgrade_list(g: Grid, rate0: np.ndarray, rate: np.ndarray, chosen: list[int]) -> tuple[list[dict], float, float]:
    lines, mva, km = [], 0.0, 0.0
    for i in chosen:
        add = float(rate[i] - rate0[i])
        k = _line_km(g, i)
        mva += add
        km += k
        info = _line(g, i)
        lines.append({"id": info["id"], "label": info["label"], "transformer": info["transformer"], "old_mva": round(float(rate0[i]), 1), "new_mva": round(float(rate[i]), 1), "added_mva": round(add, 1), "km": round(k, 1)})
    return lines, round(mva, 1), round(km, 1)


def _what_upgraded(lines: list[dict]) -> str:
    nt = sum(1 for x in lines if x["transformer"])
    nl = len(lines) - nt
    parts = []
    if nl:
        parts.append(f"{nl} line{'s' if nl != 1 else ''}")
    if nt:
        parts.append(f"{nt} transformer{'s' if nt != 1 else ''}")
    return " and ".join(parts) or "nothing"


def _greedy(c: _Case, g: Grid, extra: np.ndarray):
    """fixit.greedy_fix on the step-0 network (after the storm) with the case's upgrades."""
    import fixit  # imported late: another track owns it

    existing = frozenset(g.br_index[int(b)] for b in c.upgrades)
    room = grid.MAX_UPGRADES - len(existing)
    first, final, rate, chosen, rounds, maxed, limited = fixit.greedy_fix(g, c.active, extra, c.rate, room=room, resolve=False, existing=existing)
    new_upg = dict(c.upgrades)
    for i in chosen:
        new_upg[int(g.br_ids[i])] = float(rate[i])
    return new_upg, rate, chosen, maxed, limited


def _fixes(c: _Case, B: _Budget, J: _Judge, inc: dict, floor: dict) -> tuple[list[dict], list[str], dict]:
    g = c.g
    fixes: list[dict] = []
    unchecked: list[str] = []
    notes: dict = {}
    total = float(sum(s.mw for s in c.sites))
    main_area = c.header.get("sub_area") or "the site"
    word = load_word(g.load_factor)
    shortcut = bool(c.sites) and inc["people"] > 0 and floor["people"] >= 0.95 * inc["people"]
    floor_oc = {"steps": int(floor["total_steps"]), "people": int(floor["people"]), "lost_mw": float(floor["lost_mw"])}
    families = [f for f in FAMILY_ORDER if c.sites or f in ("upgrade",) or (f == "time_of_day" and g.load_factor > 1.0 + 1e-9)]
    shrink_res: dict = {}

    def skip(fam: str) -> None:
        unchecked.append(fam)
        fixes.append(_fix(fam, FAMILY_LABEL[fam], "not_checked", None, "Not checked in the time budget.", {}, None, 0))

    for fam in families:
        t0 = time.perf_counter()
        if shortcut and fam in CAMPUS_FAMILIES:
            fixes.append(
                _fix(
                    fam,
                    FAMILY_LABEL[fam],
                    "not_needed",
                    floor_oc,
                    f"Without the data center the same {_big(floor['people'])} people (estimate) lose power, verified by the no-campus run, so changing the campus does not help.",
                    {},
                    None,
                    0,
                )
            )
            continue
        try:
            if fam == "shrink":
                s_hi = 1.0
                if len(c.sites) == 1:
                    room = float(c.header.get("headroom_mw") or 0.0)
                    s_hi = min(1.0, max(room, 0.0) / total) if total else 0.0
                s, solves = _max_scale(c, g, s_hi)
                mws = _site_mws(c, s)
                new_total = float(sum(mws))
                if new_total < 1.0:
                    oc, v = floor_oc, J.verdict(floor_oc)
                    trade = f"No size fits here without an overload {word}." + (
                        f" Even with no data center, {_big(floor['people'])} people (estimate) lose power." if floor["people"] else ""
                    )
                    fixes.append(_fix(fam, "Shrink the data center", "fails" if v == "holds" else v, oc, trade, {"mw": 0.0, "from_mw": total, "solves": solves}, None, _ms(t0)))
                    shrink_res = {"ok": False}
                    continue
                oc, v, how = _verify(c, J, g, _extra_for(g, c.buses, mws))
                pct = round(100 * new_total / total) if total else 0
                action = f"Shrink the data center to {new_total:,.0f} MW" if len(c.sites) == 1 else f"Shrink every campus to {pct}% of its size ({new_total:,.0f} MW in total)"
                detail = {"mw": new_total, "from_mw": total, "kept_pct": pct, "room_mw": c.header.get("headroom_mw"), "solves": solves, "checked_by": how}
                fixes.append(_fix(fam, action, v, oc, f"Keeps {new_total:,.0f} of the planned {total:,.0f} MW ({pct}%).", detail, _size_apply(c, mws), _ms(t0)))
                shrink_res = {"ok": True, "mws": mws, "total": new_total, "s": s, "oc": oc, "v": v}
            elif fam == "move":
                g1 = grid_at(1.0, c.code)
                import fixit

                cur_town = area_of(c.header.get("sub_name") or "")
                found, tried, first_try = [], 0, None
                # lines already over with no campus anywhere: no town can hold it, so the scan is
                # skipped and only the roomiest town is verified (by a cascade, below)
                grid_ok, _ = _fits(c, g, np.zeros(g.n))
                for h, i, town in list(fixit._towns_by_headroom(g1))[:MOVE_POOL]:
                    if town == cur_town:
                        continue
                    if (tried >= MOVE_MIN and B.left() < 20) or (not grid_ok and first_try is not None):
                        break  # the first MOVE_MIN towns are always checked; the rest while the budget lasts
                    bus = g.connect_bus(i)
                    buses = [bus] + list(c.buses[1:])
                    extra = _extra_for(g, buses, [s.mw for s in c.sites])
                    ok, st = _fits(c, g, extra)
                    tried += 1
                    if first_try is None:
                        first_try = (i, town, extra)
                    if ok and float(st.lost_existing_mw) <= J.bound_lost + 0.5:
                        found.append(
                            {
                                "town": town,
                                "sub": int(g.sub_ids[i]),
                                "name": g.sub_name[i],
                                "lat": round(float(g.sub_lat[i]), 4),
                                "lon": round(float(g.sub_lon[i]), 4),
                                "headroom_mw": round(min(h, 1e6), 1),
                                "max_pct": round(float(st.loading_pct[st.active].max()), 1) if st.active.any() else 0.0,
                                "people": _people(g, float(st.lost_existing_mw), c.code),
                                "label": f"substations named after {town} in a synthetic model",
                            }
                        )
                        if len(found) >= MOVE_KEEP:
                            break
                if found:
                    top = found[0]
                    oc = {"steps": 0, "people": top["people"], "lost_mw": 0.0}
                    others = ", ".join(x["town"] for x in found[1:])
                    trade = "Sites are substations named after towns in a synthetic model, not real addresses." + (f" Also fits: {others}." if others else "")
                    fixes.append(_fix(fam, f"Build it at {top['town']} instead", J.verdict(oc), oc, trade, {"sites": found, "tried": tried, "checked_by": "solve"}, {"lat": top["lat"], "lon": top["lon"]}, _ms(t0)))
                elif first_try is not None:
                    i, town, extra = first_try
                    oc, v, how = _verify(c, J, g, extra)
                    if grid_ok:
                        trade = f"None of the {tried} roomiest towns takes {total:,.0f} MW {word} without an overload; {town}, the roomiest, was run through the engine."
                    else:
                        trade = f"{word[0].upper() + word[1:]} the grid overloads even with no data center anywhere, so no town takes it cleanly; {town}, the roomiest, was run through the engine."
                    fixes.append(_fix(fam, f"Build it at {town} instead", v, oc, trade, {"sites": [], "tried": tried, "checked_by": how, "town": town}, None, _ms(t0)))
                else:
                    skip(fam)
            elif fam == "flexible":
                if not shrink_res.get("ok"):
                    fixes.append(_fix(fam, "Curtail at the peak", "fails", inc_oc(inc), "No size fits at this hour, so curtailing cannot hold it.", {}, None, _ms(t0)))
                    continue
                levels = []
                for lf in sorted(set(DAY_LEVELS) | {round(g.load_factor, 2)}):
                    if abs(lf - g.load_factor) < 0.005:
                        mws = shrink_res["mws"]
                    else:
                        gl = grid_at(lf, c.code)
                        s_hi = 1.0
                        if len(c.sites) == 1:
                            s_hi = min(1.0, site_headroom(gl, c.buses[0]) / total) if total else 0.0
                        s, _ = _max_scale(c, gl, s_hi, gl.rates_with(c.upgrades))
                        mws = _site_mws(c, s)
                    run = float(sum(mws))
                    levels.append({"level": lf, "name": level_name(lf), "word": load_word(lf), "runs_mw": run, "full": run >= total - 0.5})
                at_case = shrink_res["total"]
                others = [x for x in levels if abs(x["level"] - g.load_factor) >= 0.005]
                full_at = [x["name"] for x in others if x["full"]]
                partial = [f"{x['runs_mw']:,.0f} MW {x['word']}" for x in others if not x["full"]]
                trade = f"Drops to {at_case:,.0f} MW {word}, giving up {total - at_case:,.0f} MW."
                if full_at:
                    trade += f" It can run the full {total:,.0f} MW at {_join(full_at)}."
                if partial:
                    trade += f" Room at other hours: {_join(partial)}."
                fixes.append(
                    _fix(
                        fam,
                        f"Curtail to {at_case:,.0f} MW {word}",
                        shrink_res["v"],
                        shrink_res["oc"],
                        trade,
                        {"mw": at_case, "peak_cut_mw": round(total - at_case, 1), "levels": levels, "checked_by": "solve per level"},
                        _size_apply(c, shrink_res["mws"]),
                        _ms(t0),
                    )
                )
            elif fam == "time_of_day":
                levels = []
                for lf in DAY_LEVELS:
                    gl = grid_at(lf, c.code)
                    extra = _extra_for(gl, c.buses, [s.mw for s in c.sites]) if c.sites else np.zeros(gl.n)
                    ok, st = _fits(c, gl, extra, gl.rates_with(c.upgrades))
                    levels.append({"level": lf, "name": level_name(lf), "over_lines": int(len(_over(st))), "holds": bool(ok)})
                holds_at = [x for x in levels if x["holds"] and x["level"] <= g.load_factor + 1e-9]
                fails_at = [x["name"] for x in levels if not x["holds"]]
                what = f"the full {total:,.0f} MW" if c.sites else "the grid"
                if holds_at:
                    trade = f"{what[0].upper() + what[1:]} fits at {_join([x['name'] for x in holds_at])} but not {word}" + (
                        ", and a data center runs around the clock." if c.sites else "; the heat itself can't be scheduled away."
                    )
                else:
                    trade = f"{what[0].upper() + what[1:]} overloads the grid at every hour checked: {_join(fails_at)}."
                best_lf = max((x["level"] for x in holds_at), default=None)
                if c.sites:
                    tod_action = f"Run {what} only at quieter hours" if holds_at else f"Run {what} at a quieter hour"
                else:
                    tod_action = "Wait for a cooler hour"
                fixes.append(
                    _fix(
                        fam,
                        tod_action,
                        "fails",
                        inc_oc(inc),
                        trade,
                        {"levels": levels, "checked_by": "solve per level"},
                        {"load_factor": best_lf} if best_lf is not None else None,
                        _ms(t0),
                    )
                )
            elif fam == "upgrade":
                new_upg, rate, chosen, maxed, limited = _greedy(c, g, c.extra)
                new = list(chosen)
                if not new:
                    oc = inc_oc(inc)
                    fixes.append(_fix(fam, "Upgrade lines", J.verdict(oc) if oc["steps"] == 0 and oc["people"] == 0 else "fails", oc, "No line is over its limit, so an upgrade has nothing to relieve; it cannot reconnect areas cut off from supply.", {"lines": 0}, None, _ms(t0)))
                    continue
                oc, v, how = _verify(c, J, g, c.extra, new_upg)
                lst, mva, km = _upgrade_list(g, c.rate, rate, new)
                what = _what_upgraded(lst)
                detail = {"lines": len(lst), "mva": mva, "km": km, "list": lst[:20], "capped": len(maxed), "limited": bool(limited), "checked_by": how}
                trade = f"New equipment on {what}" + (f" ({km:,.1f} km of line)" if km else "")
                if J.bound_people > 0:
                    trade += (
                        f". It stops the cascade, but the {_big(J.bound_people)} people (estimate) the {_storm_word(c)} cut off stay dark until the downed lines are rebuilt."
                        if oc["steps"] == 0
                        else f". The people the {_storm_word(c)} cut off stay dark until the downed lines are rebuilt."
                    )
                else:
                    trade += "; the data center keeps its full size." if c.sites else "."
                fixes.append(_fix(fam, f"Upgrade {what} (+{mva:,.0f} MVA)", v, oc, trade, detail, {"upgrades": {str(k): float(v_) for k, v_ in new_upg.items()}}, _ms(t0)))
                notes["upgrade"] = {"mva": mva, "km": km, "lines": len(lst), "rate": rate, "chosen": list(chosen)}
            elif fam == "onsite":
                if not shrink_res.get("ok"):
                    fixes.append(_fix(fam, "Generate on site", "fails", inc_oc(inc), "No grid draw fits at this hour, so on-site generation would have to carry the whole campus.", {}, None, _ms(t0)))
                    continue
                net = shrink_res["total"]
                onsite = round(total - net, 1)
                fixes.append(
                    _fix(
                        fam,
                        f"Add {onsite:,.0f} MW of on-site generation",
                        shrink_res["v"],
                        shrink_res["oc"],
                        f"The grid supplies {net:,.0f} MW and the campus makes the rest itself (the same verified run as the smaller size).",
                        {"onsite_mw": onsite, "net_mw": net, "checked_by": "the shrink run"},
                        _size_apply(c, shrink_res["mws"]),
                        _ms(t0),
                    )
                )
            elif fam == "combo":
                s_fit = shrink_res.get("s", 0.0) if shrink_res.get("ok") else 0.0
                s_mid = (s_fit + 1.0) / 2.0
                mws = _site_mws(c, s_mid)
                extra = _extra_for(g, c.buses, mws)
                new_upg, rate, chosen, maxed, limited = _greedy(c, g, extra)
                oc, v, how = _verify(c, J, g, extra, new_upg)
                lst, mva, km = _upgrade_list(g, c.rate, rate, chosen)
                mid_total = float(sum(mws))
                what = _what_upgraded(lst)
                ap = {**_size_apply(c, mws), "upgrades": {str(k): float(v_) for k, v_ in new_upg.items()}}
                action = f"Shrink to {mid_total:,.0f} MW and upgrade {what} (+{mva:,.0f} MVA)" if lst else f"Shrink to {mid_total:,.0f} MW"
                trade = f"Keeps {mid_total:,.0f} of the planned {total:,.0f} MW"
                full_up = notes.get("upgrade")
                if lst and full_up and full_up.get("mva"):
                    trade += f" and needs +{mva:,.0f} MVA of upgrades, against +{full_up['mva']:,.0f} MVA at full size."
                elif lst:
                    trade += f" with +{mva:,.0f} MVA of upgrades."
                else:
                    trade += "; no upgrade needed."
                fixes.append(_fix(fam, action, v, oc, trade, {"mw": mid_total, "lines": len(lst), "mva": mva, "km": km, "list": lst[:20], "checked_by": how}, ap, _ms(t0)))
            elif fam == "remove":
                v = J.verdict(floor_oc)
                trade = "No campus at this site." if v == "holds" else f"Even with no data center, {_big(floor['people'])} people (estimate) lose power, verified by the no-campus run."
                fixes.append(_fix(fam, "Don't build the data center here", v, floor_oc, trade, {"checked_by": "the no-campus run"}, {"lat": None, "lon": None, "mw": None, "sites": []}, _ms(t0)))
        except EngineGap:
            raise
        except Exception as e:  # noqa: BLE001 — a family that breaks is reported unchecked, never claimed
            log.exception("briefing: fix family %s failed: %s", fam, e)
            skip(fam)
    # a fix that doesn't fully hold says what the engine found when it ran it
    for fx in fixes:
        oc = fx.get("outcome")
        if fx["verdict"] in ("partly", "fails") and oc and fx["family"] != "time_of_day" and "(estimate)" not in fx["tradeoff"]:
            n, ppl = int(oc["steps"]), int(oc["people"])
            if n == 0:
                fx["tradeoff"] += f" Engine check: no cascade, but {_big(ppl)} people (estimate) are still without power."
            elif ppl == 0:
                fx["tradeoff"] += f" Engine check: lines still trip ({n} {'step' if n == 1 else 'steps'}), though no one loses power."
            else:
                fx["tradeoff"] += f" Engine check: still {_a(n)} {n}-step cascade, with {_big(ppl)} people (estimate) without power."
    return fixes, unchecked, notes


def inc_oc(inc: dict) -> dict:
    return {"steps": int(inc["total_steps"]), "people": int(inc["people"]), "lost_mw": float(inc["lost_mw"])}


def _best_fix(fixes: list[dict]) -> int | None:
    for i, f in enumerate(fixes):
        if f["verdict"] == "holds" and f["family"] != "remove":
            return i
    part = [(f["outcome"]["people"], i) for i, f in enumerate(fixes) if f["verdict"] == "partly" and f["family"] != "remove" and f.get("outcome")]
    if part:
        return min(part)[1]
    return None


def _firm_note(c: _Case, inc: dict) -> dict | None:
    if not c.sites or c.firm:
        return None
    r = _cascade(c, c.g, c.extra, firm_buses=list(c.buses))
    if not r:
        return None
    if float(r.get("shed_mw", 0.0)) < 0.5 and int(r["people"]) == int(inc["people"]):
        return None  # keeping the campus on changes nothing here (it's the storm's outage): nothing to say
    return {"shed_mw": float(r.get("shed_mw", 0.0)), "steps": int(r["total_steps"]), "people": int(r["people"]), "campus_kept_on": r.get("firm_held")}


# ----------------------------------------------------------------------------- recovery (restoration waves)
WAVE_PCTS = (0.02, 0.05, 0.10, 0.25, 0.50, 1.0)
WAVE_PCTS_BIG = (0.02, 0.10, 0.50, 1.0)  # more than BIG_DAMAGE damaged lines
BIG_DAMAGE = 1500
HARDEN_KS = (10, 25)
EXPORT_PENALTY = 1e-3  # LP cost per MW of tie export curtailed: kept when free, cut before any customer (served load earns 1)
PRESET_BUDGET_MS = 5000  # a catastrophe preset gets this long (reported in timing_ms); cached after the first run


class _UF:
    """Union-find over buses carrying each component's load, generation limit and tie import."""

    def __init__(self, n: int, load: np.ndarray, gmax: np.ndarray, tie: np.ndarray):
        self.p = list(range(n))
        self.L = [float(x) for x in load]
        self.G = [float(x) for x in gmax]
        self.T = [float(x) for x in tie]
        self.members = [[i] for i in range(n)]

    def find(self, a: int) -> int:
        p = self.p
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def served(self, r: int) -> float:
        return min(self.L[r], self.G[r] + max(self.T[r], 0.0))

    def gain(self, a: int, b: int, cap: float) -> float:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return 0.0
        merged = min(self.L[ra] + self.L[rb], self.G[ra] + self.G[rb] + max(self.T[ra] + self.T[rb], 0.0))
        return max(min(merged - self.served(ra) - self.served(rb), cap), 0.0)

    def union(self, a: int, b: int) -> int:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if len(self.members[ra]) < len(self.members[rb]):
            ra, rb = rb, ra
        self.p[rb] = ra
        self.L[ra] += self.L[rb]
        self.G[ra] += self.G[rb]
        self.T[ra] += self.T[rb]
        self.members[ra].extend(self.members[rb])
        self.members[rb] = []
        return rb  # the side that was merged in

    def unserved(self) -> float:
        return float(sum(self.L[r] - self.served(r) for r in range(len(self.p)) if self.p[r] == r))


def _repair_order(g: Grid, base_active: np.ndarray, damaged: list[int], rate: np.ndarray) -> tuple[list[int], float]:
    """Capacity-aware connectivity greedy: repeatedly rebuild the damaged line that brings the most
    load back (merged supply minus the parts, capped by the line's rating). Gains are re-evaluated
    lazily and refreshed around every merge. No power-flow solves. Returns (order, unserved MW with
    no repair, by connectivity)."""
    import heapq

    uf = _UF(g.n, g.pd, g.pmax, g.tie)
    for k in np.flatnonzero(base_active):
        uf.union(int(g.f[k]), int(g.t[k]))
    unserved0 = uf.unserved()
    by_bus: dict[int, list[int]] = {}
    for k in damaged:
        by_bus.setdefault(int(g.f[k]), []).append(k)
        by_bus.setdefault(int(g.t[k]), []).append(k)
    tie_key = {k: (-float(g.br_kv[k]), -float(rate[k]), int(g.br_ids[k])) for k in damaged}
    heap = [(-uf.gain(int(g.f[k]), int(g.t[k]), float(rate[k])), tie_key[k], k) for k in damaged]
    heapq.heapify(heap)
    done: set[int] = set()
    order: list[int] = []
    while heap:
        neg, tk, k = heapq.heappop(heap)
        if k in done:
            continue
        cur = uf.gain(int(g.f[k]), int(g.t[k]), float(rate[k]))
        if cur <= 1e-6:
            if -neg > 1e-6:
                heapq.heappush(heap, (0.0, tk, k))
                continue
            break  # nothing left adds supply: the rest are capacity-only repairs
        if heap and cur + 1e-9 < -heap[0][0]:
            heapq.heappush(heap, (-cur, tk, k))
            continue
        done.add(k)
        order.append(k)
        gone = uf.union(int(g.f[k]), int(g.t[k]))
        root = uf.find(gone)
        # refresh the lines around whichever side was merged in (their gain may have risen)
        for b in uf.members[root] if len(uf.members[root]) < 400 else [int(g.f[k]), int(g.t[k])]:
            for j in by_bus.get(b, ()):
                if j not in done:
                    heapq.heappush(heap, (-uf.gain(int(g.f[j]), int(g.t[j]), float(rate[j])), tie_key[j], j))
    rest = sorted((k for k in damaged if k not in done), key=lambda k: tie_key[k])
    return order + rest, unserved0


def _lp_served(g: Grid, active: np.ndarray, rate: np.ndarray) -> np.ndarray | None:
    """Controlled pickup: the most existing load (MW per bus) these lines can serve when operators
    re-dispatch generators (0..Pmax) and route power with every line within its rating (a
    network-flow LP with explicit flow variables; loop flows are not modeled). Tie exports are cut
    before customers, as in the engine. scipy's HiGHS, dual simplex then interior point. None when
    neither solves."""
    import scipy.sparse as sp

    n = g.n
    A = np.flatnonzero(active)
    ma = len(A)
    Gb = np.flatnonzero(g.pmax > 1e-9)
    Lb = np.flatnonzero(g.pd > 1e-9)
    Tb = np.flatnonzero(np.abs(g.tie) > 1e-9)
    nG, nL, nT = len(Gb), len(Lb), len(Tb)
    o_g, o_s = ma, ma + nG
    o_t = o_s + nL
    nv = o_t + nT
    fa, ta = g.f[A], g.t[A]
    # bus balance: gen + tie - served - out + in = 0
    rows = np.concatenate([fa, ta, Gb, Lb, Tb])
    cols = np.concatenate([np.arange(ma), np.arange(ma), o_g + np.arange(nG), o_s + np.arange(nL), o_t + np.arange(nT)])
    vals = np.concatenate([-np.ones(ma), np.ones(ma), np.ones(nG), -np.ones(nL), np.ones(nT)])
    Aeq = sp.csr_matrix((vals, (rows, cols)), shape=(n, nv))
    lb = np.concatenate([-rate[A], np.zeros(nG), np.zeros(nL), np.minimum(g.tie[Tb], 0.0)])
    ub = np.concatenate([rate[A], g.pmax[Gb], g.pd[Lb], np.maximum(g.tie[Tb], 0.0)])
    cost = np.zeros(nv)
    cost[o_s:o_t] = -1.0
    # as in the engine's balance, an island short of supply stops exporting before it cuts its own
    # customers: an export is kept when it costs no load (a tiny EXPORT_PENALTY per MW curtailed)
    cost[o_t:] = np.where(g.tie[Tb] < 0, EXPORT_PENALTY, 0.0)
    for method in ("highs-ds", "highs-ipm"):
        try:
            res = linprog(cost, A_eq=Aeq, b_eq=np.zeros(n), bounds=np.column_stack([lb, ub]), method=method)
        except Exception as e:  # noqa: BLE001
            log.warning("briefing: LP %s raised %s", method, e)
            continue
        if res.status == 0 and res.x is not None:
            served = np.zeros(n)
            served[Lb] = np.clip(res.x[o_s:o_t], 0.0, g.pd[Lb])
            return served
    return None


_full_lp: "OrderedDict[tuple, np.ndarray]" = OrderedDict()


def _full_key(c: _Case, g: Grid) -> tuple:
    return (c.code, round(g.load_factor, 2), tuple(sorted(c.upgrades.items())))


def _lp_full(c: _Case, g: Grid) -> np.ndarray | None:
    """The every-line-rebuilt LP: the same for every storm on one (state, level, upgrades), cached."""
    key = _full_key(c, g)
    with _cache_lock:
        hit = _full_lp.get(key)
    if hit is not None:
        return hit
    out = _lp_served(g, np.ones(g.m, dtype=bool), c.rate)
    if out is not None:
        with _cache_lock:
            _full_lp[key] = out
            while len(_full_lp) > 16:
                _full_lp.popitem(last=False)
    return out


def _area_lost(g: Grid, lost_bus: np.ndarray) -> dict[str, float]:
    per = np.zeros(len(g.sub_ids))
    np.add.at(per, g.bus_sub_idx, lost_bus)
    out: dict[str, float] = {}
    for i in np.flatnonzero(per > 0.05):
        a = area_of(g.sub_name[i])
        out[a] = out.get(a, 0.0) + float(per[i])
    return out


def _recovery(c: _Case, B: _Budget, inc: dict, event: dict) -> dict | None:
    """Rebuild order for the lines that are down (storm + cascade), in waves of 2/5/10/25/50/100 %,
    each wave verified by the controlled-pickup LP; hardening the most valuable storm lines; the
    order against biggest-lines-first; and whether the campus could reconnect once all is rebuilt.
    If an LP fails, that wave and the rest fall back to connectivity (labeled, never called verified)."""
    g = c.g
    final_active = np.asarray(inc["final_active"], dtype=bool)
    damaged = [int(k) for k in np.flatnonzero(~final_active)]
    if not damaged:
        return None
    t0 = time.perf_counter()
    order, _unserved0 = _repair_order(g, final_active, damaged, c.rate)
    order_ms = _ms(t0)
    D = len(order)
    pcts = WAVE_PCTS_BIG if D > BIG_DAMAGE else WAVE_PCTS
    sizes: list[int] = []
    size_pct: list[float] = []  # the share of damaged lines each wave reaches (aligned with sizes)
    for p in pcts:
        k = min(D, max(1, int(math.ceil(p * D))))
        if not sizes or k > sizes[-1]:
            sizes.append(k)
            size_pct.append(p)
    lp_ms: list[int] = []

    def lp(active: np.ndarray, full: bool = False, force: bool = False) -> np.ndarray | None:
        """force: always solve (the plan must not depend on how busy the server is); otherwise only
        while the budget lasts."""
        est = (max(lp_ms) * 1.2) if lp_ms else 120
        if full:
            with _cache_lock:
                if _full_key(c, g) in _full_lp:
                    est = 0
        if not force and B.left() < est:
            return None
        t = time.perf_counter()
        out = _lp_full(c, g) if full else _lp_served(g, active, c.rate)
        if est:
            lp_ms.append(_ms(t))
        return out

    def uf_unserved(active: np.ndarray) -> float:
        uf = _UF(g.n, g.pd, g.pmax, g.tie)
        for k in np.flatnonzero(active):
            uf.union(int(g.f[k]), int(g.t[k]))
        return uf.unserved()

    extras = True  # the whole plan, whatever the server load: the same case always reads the same
    ev_people = int(event["people"])
    before: dict[str, float] = {}  # area -> MW lost, where the cascade ended
    for sid, mw in (inc.get("affected") or {}).items():
        i = g.sub_index.get(int(sid))
        if i is not None:
            a = area_of(g.sub_name[i])
            before[a] = before.get(a, 0.0) + float(mw)
    waves = []
    prev_k, prev_out = 0, ev_people
    km_total = 0.0
    all_lp = True
    for n, k in enumerate(sizes, start=1):
        act = final_active.copy()
        act[order[:k]] = True
        new = order[prev_k:k]
        km = round(sum(_line_km(g, j) for j in new), 1)
        km_total += km
        served = lp(act, full=(k == D), force=True) if all_lp else None
        relit: list[str] = []
        if served is not None:
            lost_now = g.pd - served
            out = min(_people(g, float(lost_now.sum()), c.code), prev_out)
            after = _area_lost(g, lost_now)
            back = sorted(((a, mw - after.get(a, 0.0)) for a, mw in before.items() if mw >= 1.0 and after.get(a, 0.0) <= 0.1 * mw), key=lambda x: -x[1])
            relit = [a for a, _ in back[:6]]
            before = after
            method = "lp"
        else:
            all_lp = False
            out = _people(g, uf_unserved(act), c.code)
            method = "connectivity"
        waves.append(
            {
                "n": n,
                "lines": [int(g.br_ids[j]) for j in new],
                "lines_count": len(new),
                "lines_total": k,
                "km": km,
                "km_total": round(km_total, 1),
                "people_out": int(out),
                "people_back": int(max(ev_people - out, 0)),
                "areas_relit": relit,
                "method": method,
            }
        )
        prev_k, prev_out = k, out
    # hardening: keep the k most valuable storm lines standing (the plan's order, storm lines only)
    hardening = []
    storm_set = {g.br_index[int(b)] for b in c.trip}
    if storm_set and all_lp:
        storm_active = np.ones(g.m, dtype=bool)
        storm_active[list(storm_set)] = False
        base = lp(storm_active, force=extras)
        if base is not None:
            base_out = _people(g, float((g.pd - base).sum()), c.code)
            storm_order = [k for k in order if k in storm_set]
            for kk in HARDEN_KS[-1:] if D > BIG_DAMAGE else HARDEN_KS:
                if kk > len(storm_order):
                    continue
                act = storm_active.copy()
                act[storm_order[:kk]] = True
                srv = lp(act, force=extras)
                if srv is None:
                    break
                o = _people(g, float((g.pd - srv).sum()), c.code)
                hardening.append(
                    {
                        "k": kk,
                        "lines": [int(g.br_ids[j]) for j in storm_order[:kk]],
                        "km": round(sum(_line_km(g, j) for j in storm_order[:kk]), 1),
                        "people_out_without": int(base_out),
                        "people_kept_on": int(max(base_out - o, 0)),
                    }
                )
    # the same number of repairs, biggest lines first: does the plan's order matter?
    baseline = None
    ten = next((i for i, p in enumerate(size_pct) if p >= 0.10 - 1e-9 and sizes[i] < D), None)
    if all_lp and ten is not None and ten < len(sizes) and ten < len(waves) and waves[ten]["method"] == "lp":
        k = sizes[ten]
        big = sorted(damaged, key=lambda j: (-float(g.br_kv[j]), -float(c.rate[j]), int(g.br_ids[j])))
        act = final_active.copy()
        act[big[:k]] = True
        srv = lp(act, force=extras)
        if srv is not None:
            b_out = _people(g, float((g.pd - srv).sum()), c.code)
            plan_out = waves[ten]["people_out"]
            baseline = {"order": "biggest lines first", "repairs": k, "people_out": int(b_out), "plan_people_out": int(plan_out), "plan_better_by": int(b_out - plan_out)}
    reconnect = None
    if c.sites:
        r = _cascade(c, g, c.extra, trip=[], upgrades=c.upgrades)
        reconnect = {"ok": r["total_steps"] == 0 and r["people"] == 0, "steps": int(r["total_steps"]), "people": int(r["people"])}
    method = "lp" if all_lp else "connectivity"
    return {
        "method": method,
        "method_note": (
            "line limits applied: a controlled-pickup LP (generators re-dispatched, every line within its rating; loop flows not modeled)"
            if method == "lp"
            else "line limits not applied (connectivity only)"
        ),
        "damaged_lines": D,
        # people still dark with every line rebuilt (a heat wave's own shortfall), when it's material
        "still_out_after_all": (
            int(waves[-1]["people_out"])
            if waves and waves[-1]["lines_total"] == D and waves[-1]["method"] == "lp" and waves[-1]["people_out"] >= max(1000, 0.01 * ev_people)
            else None
        ),
        "wave0_people_back": None,
        "waves": waves,
        "hardening": hardening,
        "baseline": baseline,
        "campus_reconnect": reconnect,
        "timing_ms": {"order": order_ms, "lp": lp_ms},
    }


# ----------------------------------------------------------------------------- cost + hospitals adapters
def _cost(c: _Case, rep: dict, notes: dict) -> dict | None:
    """What it costs, priced from this report's own runs with costs.py's published figures and
    helpers (LBNL value of lost load, Black & Veatch line and transformer costs, EIA prices, Census
    households) — the same formulas as the cost panel, without re-running the cascade. costs.py's own
    briefing_costs(report) wins when it exists. Midpoints of the low-high ranges, ranges alongside.
    Never zeros: a figure with nothing to price is None; nothing at all -> None."""
    try:
        import costs
    except Exception:  # noqa: BLE001
        return None
    fn = getattr(costs, "briefing_costs", None)
    try:
        if fn is not None:
            out = fn(rep)
            if isinstance(out, dict) and any(out.get(k) for k in ("blackout_usd", "upgrade_usd", "campus_bill_usd_per_year")):
                return {**out, "source": "costs.py"}
            return None
        g = c.g
        # how long the lights stay out: estimated from the incident's size (costs.outage_hours), like the cost panel
        hours = float(costs.outage_hours(int((rep.get("event") or {}).get("people") or 0))) or float(costs.DEFAULT_HOURS)
        S = costs.SOURCES
        lines: dict[str, dict] = {}
        lost_mw = max(float(rep["event"]["lost_mw"]), 0.0)
        if lost_mw > 0.5:
            v_lo, v_hi = costs.voll_per_mwh(hours)
            mwh = lost_mw * hours
            lines["blackout"] = {
                "low": round(mwh * v_lo),
                "high": round(mwh * v_hi),
                "assumption": f"{lost_mw:,.0f} MW of customers dark for about {hours:g} hours (estimated from the incident's size) at LBNL's value of lost load by customer class, weighted by U.S. sales (2024 dollars).",
                "sources": [S["lbnl_voll"], S["eia_sales"], S["cpi"]],
            }
        up = notes.get("upgrade") or {}
        if up.get("chosen"):
            applied = {g.br_index[int(b)] for b in c.upgrades}
            items = costs._upgrade_items(g, g.rate, up["rate"], sorted(applied | set(up["chosen"])), applied)
            if items:
                lines["upgrades"] = {
                    "low": sum(it["low"] for it in items),
                    "high": sum(it["high"] for it in items),
                    "assumption": "The upgrades that stop the cascade, priced by voltage class and length: reconductoring to a new line (Black & Veatch, 2014 dollars raised to 2024); transformers at $7,250 to $13,450 per MVA.",
                    "sources": [S["bv_wecc"], S["gridlab_2035"], S["cpi"]],
                }
        mw = float(sum(s.mw for s in c.sites))
        if mw > 0:
            price = costs.INDUSTRIAL_PRICE_2024.get(c.code, costs.US_INDUSTRIAL_PRICE_2024)
            per_mwh = price * 10.0
            lines["power_bill"] = {
                "low": round(mw * costs.HOURS_PER_YEAR * costs.LOAD_FACTOR_LOW * per_mwh),
                "high": round(mw * costs.HOURS_PER_YEAR * costs.LOAD_FACTOR_HIGH * per_mwh),
                "assumption": f"The campus draws {costs.LOAD_FACTOR_LOW:.0%} to {costs.LOAD_FACTOR_HIGH:.0%} of its size on average (LBNL) at {REGIONS[c.code]['name']}'s 2024 average industrial price, {price:.2f} cents per kWh (EIA).",
                "sources": [S["lbnl_dc"], S["eia_price"]],
            }
        pop = _population(c.code) or 0
        households = pop / float(getattr(grid, "PEOPLE_PER_HOUSEHOLD", 2.5)) if pop else 0.0
        if "upgrades" in lines and households:
            per_hh = costs.CRF / 12.0 / households
            lines["who_pays"] = {
                "low": round(lines["upgrades"]["low"] * costs.RES_SHARE * per_hh, 4),
                "high": round(lines["upgrades"]["high"] * per_hh, 4),
                "assumption": f"Illustrative: the upgrades paid back over {costs.RECOVERY_YEARS} years at {costs.RECOVERY_RATE:.0%} a year, spread over the state's households; low, households pay their share of sales; high, all of it. Who really pays is up to regulators.",
                "sources": [S["census"], S["eia_sales"]],
            }
    except Exception as e:  # noqa: BLE001 — a cost that can't be computed is left out, never guessed
        log.warning("briefing: costs adapter failed: %s", e)
        return None

    def mid(key):
        ln = lines.get(key)
        if not ln or not ln.get("high"):
            return None, None
        lo, hi = float(ln.get("low") or 0), float(ln["high"])
        return round((lo + hi) / 2), [round(lo), round(hi)]

    blackout, r_b = mid("blackout")
    upgrade, r_u = mid("upgrades")
    bill, r_c = mid("power_bill")
    if not any((blackout, upgrade, bill)):
        return None
    who = lines.get("who_pays")
    who_txt = None
    if who and who.get("high"):

        def money(v: float) -> str:  # never "$0.00": a fraction of a cent reads as "under 1 cent"
            if v < 0.005:
                return "under 1 cent"
            if v < 0.995:
                cents = round(v * 100)
                return f"{cents} cent{'s' if cents != 1 else ''}"
            return f"${v:,.2f}"

        lo_, hi_ = money(float(who.get("low") or 0)), money(float(who["high"]))
        span = hi_ if lo_ == hi_ else f"{lo_} to {hi_}"
        who_txt = f"{span} per household per month (illustrative)"
    assumptions = [{"key": "hours_out", "value": hours, "unit": "hours", "note": "How long the lost load stays dark (estimated from the incident's size, a rule of thumb)."}]
    sources = []
    for key in ("blackout", "upgrades", "power_bill", "who_pays"):
        ln = lines.get(key)
        if ln:
            assumptions.append({"key": key, "value": [ln.get("low"), ln.get("high")], "unit": "USD", "note": ln.get("assumption") or ""})
            for src in ln.get("sources") or []:
                if src not in sources:
                    sources.append(src)
    return {
        "source": "costs.py",
        "duration_h_assumed": hours,
        "outage_label": {"en": costs.outage_label(hours), "es": costs.outage_label(hours, "es")},
        "blackout_usd": blackout,
        "blackout_high_usd": r_b[1] if r_b else None,  # the top of the range: the headline figure (we guess on the higher side)
        "upgrade_usd": upgrade,
        "campus_bill_usd_per_year": bill,
        "ranges": {"blackout_usd": r_b, "upgrade_usd": r_u, "campus_bill_usd_per_year": r_c},
        "who_pays": who_txt,
        "assumptions": assumptions,
        "sources": sources,
    }


def _hospitals(c: _Case, inc: dict) -> dict | None:
    """How many hospitals sit where the model's substations went dark (counts per area, never names),
    from hospitals.status_for when it exists."""
    try:
        import hospitals
    except Exception:  # noqa: BLE001
        return None
    fn = getattr(hospitals, "status_for", None)
    if fn is None:
        return None
    try:
        affected = {int(k): float(v) for k, v in (inc.get("affected") or {}).items()}
        if not affected:
            return None
        res = fn(c.code, c.g, affected)
        per: dict[str, int] = {}
        for row in res.get("backup", []):
            i = c.g.sub_index.get(int(row.get("sub", -1)))
            if i is not None:
                a = area_of(c.g.sub_name[i])
                per[a] = per.get(a, 0) + 1
        n = int(res.get("counts", {}).get("backup", sum(per.values())))
        if not n:
            return None
        return {
            "count": n,
            "strained": int(res.get("counts", {}).get("strained", 0)),
            "areas": [{"area": a, "count": k} for a, k in sorted(per.items(), key=lambda x: (-x[1], x[0]))],
            "source": getattr(hospitals, "SOURCE", "OpenStreetMap contributors (ODbL)"),
            "assumption": getattr(hospitals, "ASSUMPTION", ""),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("briefing: hospitals adapter failed: %s", e)
        return None


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
    if c.preset:
        budget_ms = max(budget_ms, PRESET_BUDGET_MS)
    with _cache_lock:
        hit = _cache.get(c.key)
    if hit is not None and not (hit.get("unchecked") and budget_ms > hit.get("_budget_ms", BUDGET_MAX)):
        with _cache_lock:
            _cache.move_to_end(c.key)
        return {**hit, "cached": True, "timing_ms": {**hit["timing_ms"], "cached_total": _ms(t_all)}}
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

    # replaying a step costs about one cascade solve, slow on a storm-shattered network: a catastrophe
    # (more lines down than a drawn storm can take out) gets its first WHY_STEPS_BIG steps explained
    why_steps = WHY_STEPS if len(c.trip) <= grid.MAX_TRIPS else WHY_STEPS_BIG
    t0 = time.perf_counter()
    rows, first = _timeline(c, inc, why_steps)
    timing["timeline"] = _ms(t0)
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
    t0 = time.perf_counter()
    root = _root_cause(c, first, inc, floor)

    # the no-fix bound: infinite ratings, no campus, the storm's damage only
    bst = _solve(c, g, c.active, None, c.rate * 1e9)
    b_lost = float(bst.lost_existing_mw)
    b_people = min(_people(g, b_lost, c.code), event["people"]) if event["people"] else _people(g, b_lost, c.code)
    bound = {
        "people": int(b_people),
        "lost_mw": round(b_lost, 1),
        "share_pct": round(100.0 * b_people / event["people"], 1) if event["people"] else 0.0,
    }
    physical = min(bound["people"], event["people"])
    campus_ppl = min(int(root["people_due_to_campus"]), event["people"] - physical)
    split = {"physical": int(physical), "cascade": int(max(event["people"] - physical - campus_ppl, 0)), "campus": int(max(campus_ppl, 0))}
    J = _Judge(event["people"], event["steps"], bound["people"], b_lost)
    timing["cause_bound"] = _ms(t0)

    t0 = time.perf_counter()
    nothing = event["steps"] == 0 and event["people"] == 0
    fixes, unchecked, notes = ([], [], {}) if nothing else _fixes(c, B, J, inc, floor)
    firm_note = None
    if not nothing:
        try:
            firm_note = _firm_note(c, inc)
        except EngineGap:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("briefing: firm note failed: %s", e)
    timing["fixes"] = _ms(t0)
    best = _best_fix(fixes)
    if nothing:
        verdict = "nothing_happened"
    elif bound["people"] > 0.05 * event["people"]:
        verdict = "no_fix"
    elif best is not None and fixes[best]["verdict"] == "holds":
        verdict = "preventable"
    else:
        verdict = "partly"
    no_fix = None
    if verdict == "no_fix":
        tried = [f for f in fixes if f["verdict"] != "not_checked"]
        saved = max([event["people"] - f["outcome"]["people"] for f in tried if f.get("outcome")] + [0])
        saved_pct = round(100.0 * saved / event["people"]) if event["people"] else 0
        no_fix = {
            "people": bound["people"],
            "share_pct": bound["share_pct"],
            "fixes_save_at_most_pct": saved_pct,
            "proof": [{"family": f["family"], "verdict": f["verdict"], "people": (f.get("outcome") or {}).get("people")} for f in fixes],
            "sentence": (
                f"No fix exists for about {_big(bound['people'])} people (estimate). Even with unlimited line capacity and no data center, "
                f"the damage cuts them off from the power plants that could serve them; only rebuilding the downed lines brings them back. "
                + (
                    f"Every fix family was checked, and the best saves at most {saved_pct}% of the outage."
                    if saved_pct >= 1
                    else "Every fix family was checked, and the best saves less than 1% of the outage."
                    if saved > 0
                    else "Every fix family was checked; none of them brings anyone back."
                )
            ),
        }

    recovery = None
    t0 = time.perf_counter()
    if verdict == "no_fix" or c.trip:
        try:
            recovery = _recovery(c, B, inc, event)
        except EngineGap:
            raise
        except Exception as e:  # noqa: BLE001 — the plan is reported missing, never guessed
            log.exception("briefing: recovery failed: %s", e)
            unchecked.append("recovery")
    timing["recovery"] = _ms(t0)
    if no_fix and recovery and recovery.get("still_out_after_all"):
        left = int(recovery["still_out_after_all"])
        no_fix["after_rebuild_people"] = left
        no_fix["sentence"] += f" Even with every line rebuilt, about {_big(left)} people (estimate) stay dark at this load; only cutting demand could reach them."

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
        "verdict": verdict,
        "case": case_out,
        "headline": {"text": _headline(c, kind, inc, areas)},
        "event": event,
        "timeline": rows,
        "root_cause": root,
        "areas": areas,
        "hospitals": None,
        "cost": None,
        "fixes": fixes,
        "best_fix": best,
        "firm_note": firm_note,
        "bound": bound,
        "split": split,
        "no_fix": no_fix,
        "recovery": recovery,
        "replay": {**c.header, "region": c.code, **inc},
        "facts": [],
        "timing_ms": {},
        "unchecked": unchecked,
    }
    t0 = time.perf_counter()
    rep["hospitals"] = _hospitals(c, inc)
    timing["hospitals"] = _ms(t0)
    t0 = time.perf_counter()
    rep["cost"] = _cost(c, rep, notes)
    timing["cost"] = _ms(t0)
    rep["facts"] = _facts(c, rep)
    timing["total"] = _ms(t_all)
    rep["timing_ms"] = timing
    rep["_budget_ms"] = budget_ms
    rep["cached"] = False
    return rep


# ----------------------------------------------------------------------------- what-if (Ask tools)
def _ctx_for(report_key: str) -> _Case:
    with _cache_lock:
        c = _ctx.get(str(report_key))
    if c is None:
        raise KeyError("Report expired — fetch the briefing again")
    return c


def _num(v, what: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if not math.isfinite(x):
        raise ValueError(f"{what} must be a number")
    return x


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
            mw = _num(change["mw"], "Size")
            if not (math.isfinite(mw) and grid.MW_MIN <= mw <= grid.MW_MAX):
                raise ValueError(f"Size must be between {grid.MW_MIN} and {grid.MW_MAX:,} MW")
            sites[0] = SiteIn(lat=sites[0].lat, lon=sites[0].lon, mw=mw)
            delta = {"mw": mw}
            label = f"{mw:,.0f} MW at {where}"
        else:
            fct = _num(change["factor"], "The size factor")
            if not (math.isfinite(fct) and 0.1 <= fct <= 2.0):
                raise ValueError("The size factor must be between 0.1 and 2")
            sites = [SiteIn(lat=s.lat, lon=s.lon, mw=max(grid.MW_MIN, min(grid.MW_MAX, round(s.mw * fct, 1)))) for s in sites]
            delta = {"mw": sites[0].mw, "sites": [{"lat": s.lat, "lon": s.lon, "mw": s.mw} for s in sites[1:]]}
            label = f"{sum(s.mw for s in sites):,.0f} MW ({fct:g} x the size)"
    elif "load_factor" in change:
        lf = _num(change["load_factor"], "Load level")
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
