"""Plant Down (supply side): every power plant in a state model, which areas it keeps lit, and what
losing it does. The other half of "whose lights go out" is "whose plant keeps them on".

Data: backend/demo/plants/<ST>.json, built offline by backend/demo/build_plants.py from the
dataset's own plant list — plants on the model's synthetic buses, named after its substations
("HOMESTEAD 20 nuclear"); every result describes the model, never a real plant or utility. A plant is every online unit of one fuel at one substation; its
`parts` are the exact [bus, Pg, Pmax] amounts the model's buses carry for it.

Endpoints (all public, like grid.py; every people number is an estimate):
- GET  /api/plants?region=FL                  the plants, per-fuel totals, what each produces now
- POST /api/plants/trip                       a case (grid.CaseIn) + `outages` (plant ids) +
                                              `retire_fuels` → the same shape as /api/grid/cascade,
                                              plus what was removed, the moment after, and the
                                              same case with every plant running (`baseline`)
- GET  /api/plants/{region}/{id}/trace        which areas the plant's power reaches (base case)
- POST /api/plants/{region}/{id}/trace        the same on a case (before any cascade)
- GET  /api/plants/ranking?region=FL          the plants a state can least afford to lose: each
                                              substation's plants tripped alone, the cascade run,
                                              people counted — computed in a background thread per
                                              (region, load level, campus), cached, polled

Tripping a plant rebuilds the state's Grid from its JSON with that plant's generation subtracted
from its buses, then runs the SAME cascade engine (powerflow.Grid.cascade_case) with the case's
campus, heat level, storm lines and upgrades. If what's left can't meet demand, the engine sheds
load island by island — reported as `supply.short_mw` and in the people count.

Tracing: proportional sharing (Bialek's upstream method) on the solved DC flows. Every bus's
throughflow P_i = generation + imports + line inflows; each line out of bus j carries the same mix
as j's throughflow. So the MW at bus i that came from source s solves (I - M) x = s with
M[i, j] = |flow j->i| / P_j — one sparse LU per network state, one solve per plant. Summed over
every source, x = P at every bus (`check.max_bus_error`), and a plant's MW delivered to loads and
exports adds back up to its output (`check.conservation_error`).
"""

from __future__ import annotations

import copy
import inspect
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field
from scipy.sparse.linalg import splu

import grid as gridmod
from grid import REGIONS, CaseIn, check_case, check_site, grid_at, region_code
import powerflow
from limiter import limiter
from powerflow import Grid

HOMES_PER_MW = getattr(powerflow, "HOMES_PER_MW", 700)  # the engine's own homes rule, if it still has one

try:  # the core track added area_of; keep working without it
    from powerflow import area_of
except ImportError:  # pragma: no cover
    import re

    def area_of(name: str) -> str:
        base = re.sub(r"\s+\d+$", "", str(name or "")).strip().lower()
        return re.sub(r"\b\w", lambda m: m.group(0).upper(), base)


router = APIRouter(tags=["plants"])

PLANTS_DIR = Path(__file__).parent / "demo" / "plants"
DEMO_DIR = Path(__file__).parent / "demo"
METHOD = "proportional sharing on the solved DC flows"
FUEL_ORDER = ["nuclear", "coal", "gas", "oil", "hydro", "geothermal", "wind", "offshore wind", "solar", "other"]
FUEL_ALIASES = {
    "ng": "gas", "natural gas": "gas", "dfo": "oil", "wind_offshore": "offshore wind", "offshore_wind": "offshore wind",
    "offshore": "offshore wind",
}
MAX_OUTAGES = 500
MAX_FUELS = len(FUEL_ORDER)
TRACE_TOP = 15  # areas listed by name; the rest are summed
SUB_SHARE_MIN = 0.05  # by_sub lists substations getting at least this share of their load from the plant
_EPS = 1e-9

_HAS_FIRM = "firm_buses" in inspect.signature(Grid.cascade_case).parameters


# ---------------------------------------------------------------------------------------- data
_plants: dict[str, dict] = {}
_plants_lock = threading.Lock()


def _summary(p: dict) -> dict:
    """A plant without its per-bus parts (what the UI needs)."""
    return {k: v for k, v in p.items() if k != "parts"}


def region_plants(code: str) -> dict:
    """The region's plant list with lookups, parsed once per process (all 48 total ~1 MB)."""
    with _plants_lock:
        idx = _plants.get(code)
    if idx is not None:
        return idx
    path = PLANTS_DIR / f"{code}.json"
    if not path.exists():
        raise HTTPException(status_code=422, detail=f"No plant list for {REGIONS[code]['name']} yet")
    raw = json.loads(path.read_text(encoding="utf-8"))
    plants = raw["plants"]
    by_fuel: dict[str, list[dict]] = {}
    by_sub: dict[int, list[dict]] = {}
    for p in plants:
        by_fuel.setdefault(p["fuel"], []).append(p)
        by_sub.setdefault(int(p["sub"]), []).append(p)
    idx = {
        "plants": plants,
        "by_id": {int(p["id"]): p for p in plants},
        "by_fuel": by_fuel,
        "by_sub": by_sub,
        "source": raw.get("source", ""),
    }
    with _plants_lock:
        _plants[code] = idx
    return idx


def normalize_fuel(fuel: str) -> str:
    f = str(fuel or "").strip().lower()
    f = FUEL_ALIASES.get(f, f)
    if f not in FUEL_ORDER:
        raise HTTPException(status_code=422, detail=f"Unknown fuel {fuel!r} — use one of: {', '.join(FUEL_ORDER)}")
    return f


def resolve_removed(code: str, outages: list[int], fuels: list[str]) -> list[dict]:
    """The plants a request takes out: each id in `outages` plus every plant of each retired fuel."""
    if len(outages) > MAX_OUTAGES:
        raise HTTPException(status_code=422, detail=f"At most {MAX_OUTAGES} plants at once")
    if len(fuels) > MAX_FUELS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_FUELS} fuels at once")
    idx = region_plants(code)
    chosen: dict[int, dict] = {}
    for pid in outages:
        p = idx["by_id"].get(pid)
        if p is None:
            raise HTTPException(status_code=422, detail=f"Unknown plant id {pid} in {REGIONS[code]['name']}")
        chosen[pid] = p
    for fuel in fuels:
        for p in idx["by_fuel"].get(normalize_fuel(fuel), []):
            chosen[int(p["id"])] = p
    return list(chosen.values())


_data_cache: "OrderedDict[str, dict]" = OrderedDict()


def _data_of(g: Grid, code: str) -> dict:
    """The parsed JSON behind a Grid (shared with grid.py's cache when the Grid keeps it)."""
    data = getattr(g, "_data", None)
    if isinstance(data, dict) and "buses" in data:
        return data
    with _plants_lock:
        if code in _data_cache:
            return _data_cache[code]
    data = json.loads((DEMO_DIR / REGIONS[code]["file"]).read_text(encoding="utf-8"))
    with _plants_lock:
        _data_cache[code] = data
        while len(_data_cache) > 3:
            _data_cache.popitem(last=False)
    return data


def grid_without(g: Grid, code: str, removed: list[dict]) -> Grid:
    """The same state model at the same load level with `removed` plants' generation taken off
    their buses. A fresh Grid (its own base state, no headroom cache); people-per-MW carried over."""
    if not removed:
        return g
    cut: dict[int, list[float]] = {}
    for p in removed:
        for bid, pg, pmax in p["parts"]:
            c = cut.setdefault(int(bid), [0.0, 0.0])
            c[0] += float(pg)
            c[1] += float(pmax)
    data = _data_of(g, code)

    def less(v: float, d: float) -> float:
        r = float(v) - d
        return r if r > 1e-6 else 0.0

    buses = []
    for b in data["buses"]:
        d = cut.get(int(b["id"]))
        buses.append(b if d is None else {**b, "pg": less(b.get("pg", 0.0), d[0]), "pmax": less(b.get("pmax", 0.0), d[1])})
    out = Grid({**data, "buses": buses}, g.load_factor)
    out.people_per_mw = getattr(g, "people_per_mw", 0.0)
    out.population = getattr(g, "population", None)
    return out


def grid_without_fast(g: Grid, removed: list[dict]) -> Grid:
    """grid_without by copying g's arrays instead of re-parsing the JSON — the ranking's hot loop
    (re-parsing is ~2/3 of a trip's time in a big state). The copy's lazy caches (private attributes
    other than _data) are cleared and its base state re-solved. _rank checks it against grid_without
    on its first plant and falls back to the rebuild if they ever disagree (powerflow.py changes)."""
    if not removed:
        return g
    c = copy.copy(g)
    pg, pmax = np.array(g.pg, dtype=float), np.array(g.pmax, dtype=float)
    for p in removed:
        for bid, dpg, dpm in p["parts"]:
            i = g.bus_index.get(int(bid))
            if i is not None:
                pg[i] -= float(dpg)
                pmax[i] -= float(dpm)
    c.pg = np.where(pg > 1e-6, pg, 0.0)
    c.pmax = np.maximum(np.where(pmax > 1e-6, pmax, 0.0), c.pg)
    for k in list(vars(c)):
        if k.startswith("_") and k != "_data":
            setattr(c, k, None)
    c.base = c.solve(np.ones(c.m, dtype=bool))
    return c


def people(g: Grid, mw: float) -> int:
    f = getattr(g, "people", None)
    return int(f(mw)) if callable(f) else int(round(max(float(mw), 0.0) * HOMES_PER_MW))


def cascade(g: Grid, extra: np.ndarray, trip: list[int], upgrades: dict[int, float], firm: list[int] | None) -> dict:
    if _HAS_FIRM:
        return g.cascade_case(extra, trip, upgrades, firm_buses=firm)
    return g.cascade_case(extra, trip, upgrades)


def plant_output(g: Grid, gen: np.ndarray, p: dict) -> float:
    """MW the plant produces in a solved state: its share of each of its buses' dispatch, split the
    way powerflow._balance dispatches (scaled up by spare capacity, or down by output)."""
    out = 0.0
    for bid, pg, pmax in p["parts"]:
        i = g.bus_index.get(int(bid))
        if i is None:
            continue
        G, PG, PM = float(gen[i]), float(g.pg[i]), float(g.pmax[i])
        if G <= _EPS or PM <= _EPS:
            continue
        if G >= PG - 1e-9:
            head = PM - PG
            out += pg + (G - PG) * ((pmax - pg) / head if head > 1e-9 else pmax / PM)
        else:
            out += G * (pg / PG if PG > 1e-9 else pmax / PM)
    return out


# ---------------------------------------------------------------------------------------- tracing
_areas: dict[str, tuple] = {}


def _region_areas(g: Grid, code: str) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray]:
    """Per bus: its area (the town its substation is named after); per area: name, lat, lon."""
    got = _areas.get(code)
    if got is not None and len(got[0]) == g.n:
        return got
    sub_area = [area_of(n) for n in g.sub_name]
    names = sorted(set(sub_area))
    pos = {a: i for i, a in enumerate(names)}
    sub_area_idx = np.array([pos[a] for a in sub_area])
    bus_area = sub_area_idx[g.bus_sub_idx]
    cnt = np.bincount(sub_area_idx, minlength=len(names))
    lat = np.bincount(sub_area_idx, weights=g.sub_lat, minlength=len(names)) / np.maximum(cnt, 1)
    lon = np.bincount(sub_area_idx, weights=g.sub_lon, minlength=len(names)) / np.maximum(cnt, 1)
    got = (bus_area, names, lat, lon)
    _areas[code] = got
    return got


class Tracer:
    """Proportional sharing on one solved state (see the module docstring)."""

    def __init__(self, g: Grid, state):
        n = g.n
        act = np.asarray(state.active, dtype=bool)
        f, t, fl = g.f[act], g.t[act], np.asarray(state.flow)[act]
        net_out = np.bincount(f, weights=fl, minlength=n) - np.bincount(t, weights=fl, minlength=n)
        gen = np.asarray(state.gen, dtype=float)
        served = np.asarray(state.served_load, dtype=float)
        tie = net_out - gen + served  # what's left at a bus is its tie: + import, - export
        tie = np.where(np.abs(tie) > 1e-3, tie, 0.0)
        self.gen, self.served = gen, served
        self.imp, self.exp = np.maximum(tie, 0.0), np.maximum(-tie, 0.0)
        up, down, mag = np.where(fl >= 0, f, t), np.where(fl >= 0, t, f), np.abs(fl)
        k = mag > 1e-6
        up, down, mag = up[k], down[k], mag[k]
        src = gen + self.imp
        self.P = src + np.bincount(down, weights=mag, minlength=n)  # throughflow
        Psafe = np.where(self.P > _EPS, self.P, 1.0)
        M = sp.coo_matrix((mag / Psafe[up], (down, up)), shape=(n, n))
        self.lu = splu((sp.identity(n, format="csc") - M).tocsc())
        self.Psafe = Psafe
        x = self.lu.solve(src)
        self.max_bus_error = float(np.max(np.abs(x - self.P) / np.maximum(self.P, 1.0)))

    def share(self, source: np.ndarray) -> np.ndarray:
        """Per bus: the share of its throughflow (so of its load and exports) that came from `source`."""
        x = self.lu.solve(source)
        return np.clip(x / self.Psafe, 0.0, 1.0)


_tracers: "OrderedDict[tuple, Tracer]" = OrderedDict()
_tracer_lock = threading.Lock()


def base_tracer(g: Grid, code: str) -> Tracer:
    key = (code, round(float(g.load_factor), 2), id(g))
    with _tracer_lock:
        tr = _tracers.get(key)
        if tr is not None:
            _tracers.move_to_end(key)
            return tr
    tr = Tracer(g, g.base)
    with _tracer_lock:
        _tracers[key] = tr
        while len(_tracers) > 8:
            _tracers.popitem(last=False)
    return tr


def _no_trace(output: float) -> dict:
    return {"output_mw": round(output, 1), "serves": [], "rest": {"areas": 0, "mw": 0.0}, "by_sub": {}, "served_mw": 0.0, "exported_mw": 0.0}


def trace(g: Grid, code: str, tr: Tracer, p: dict) -> dict:
    source = np.zeros(g.n)  # the plant's own MW at each of its buses
    for part in p["parts"]:
        i = g.bus_index.get(int(part[0]))
        if i is not None:
            source[i] += plant_output(g, tr.gen, {"parts": [part]})
    output = float(source.sum())
    out = _no_trace(output)
    if output <= 0.05:
        out["check"] = {"max_bus_error": round(tr.max_bus_error, 6), "conservation_error": 0.0}
        return out
    frac = tr.share(source)
    to_load = tr.served * frac
    to_export = tr.exp * frac
    bus_area, names, alat, alon = _region_areas(g, code)
    na = len(names)
    area_load = np.bincount(bus_area, weights=tr.served, minlength=na)
    area_mw = np.bincount(bus_area, weights=to_load, minlength=na)
    order = np.argsort(-area_mw)
    listed = [a for a in order if area_mw[a] >= 0.5]
    serves = [
        {
            "area": names[a],
            "share": round(float(area_mw[a] / area_load[a]), 3) if area_load[a] > _EPS else 0.0,
            "mw": round(float(area_mw[a]), 1),
            "of_output": round(float(area_mw[a] / output), 3),
            "lat": round(float(alat[a]), 4),
            "lon": round(float(alon[a]), 4),
        }
        for a in listed[:TRACE_TOP]
    ]
    rest = listed[TRACE_TOP:]
    ns = len(g.sub_ids)
    sub_load = np.bincount(g.bus_sub_idx, weights=tr.served, minlength=ns)
    sub_mw = np.bincount(g.bus_sub_idx, weights=to_load, minlength=ns)
    with np.errstate(divide="ignore", invalid="ignore"):
        sub_share = np.where(sub_load > 0.1, sub_mw / sub_load, 0.0)
    by_sub = {int(g.sub_ids[s]): round(float(sub_share[s]), 2) for s in np.flatnonzero(sub_share >= SUB_SHARE_MIN)}
    served_mw, exported_mw = float(to_load.sum()), float(to_export.sum())
    out.update(
        {
            "serves": serves,
            "rest": {"areas": len(rest), "mw": round(float(area_mw[rest].sum()), 1) if rest else 0.0},
            "by_sub": by_sub,
            "served_mw": round(served_mw, 1),
            "exported_mw": round(exported_mw, 1),
            "check": {
                "max_bus_error": round(tr.max_bus_error, 6),
                "conservation_error": round(abs(served_mw + exported_mw - output) / max(output, 1.0), 6),
            },
        }
    )
    return out


# ---------------------------------------------------------------------------------------- cases
class PlantCaseIn(CaseIn):
    outages: list[int] = Field(default_factory=list)  # plant ids taken out
    retire_fuels: list[str] = Field(default_factory=list)  # every plant of these fuels taken out


def _header(g: Grid, sites, trip: list[int], upgrades: dict[int, float]) -> tuple[np.ndarray, dict]:
    """grid._case_header when the core module has it, else the minimum the cascade shape needs."""
    fn = getattr(gridmod, "_case_header", None)
    if callable(fn):
        return fn(g, sites, trip, upgrades)
    buses = [g.site_bus(s.lat, s.lon) for s in sites]
    infos = [
        {
            "bus": int(g.bus_ids[b]),
            "sub": int(g.sub_ids[g.bus_sub_idx[b]]),
            "sub_name": g.sub_name[g.bus_sub_idx[b]],
            "sub_lat": round(float(g.sub_lat[g.bus_sub_idx[b]]), 4),
            "sub_lon": round(float(g.sub_lon[g.bus_sub_idx[b]]), 4),
            "mw": s.mw,
        }
        for b, s in zip(buses, sites)
    ]
    main = infos[0] if infos else {k: None for k in ("bus", "sub", "sub_name", "sub_lat", "sub_lon")}
    header = {
        **main,
        "mw": sum(s.mw for s in sites),
        "sites": infos,
        "load_factor": g.load_factor,
        "total_load_mw": round(float(g.pd.sum())),
        "trip": trip,
        "upgrades": {str(k): v for k, v in upgrades.items()},
    }
    return g.extra_load([(b, s.mw) for b, s in zip(buses, sites)]), header


def _extra(g: Grid, sites) -> np.ndarray:
    """Per-bus added load of the case's campuses (no header, no headroom solve)."""
    return g.extra_load([(g.site_bus(s.lat, s.lon), s.mw) for s in sites])


def _firm_buses(g: Grid, sites, firm: bool) -> list[int] | None:
    fn = getattr(gridmod, "case_firm_buses", None)
    if callable(fn):
        return fn(g, sites, firm)
    return [g.site_bus(s.lat, s.lon) for s in sites] if firm and sites else None


_baselines: "OrderedDict[tuple, dict]" = OrderedDict()


def _baseline(key: tuple, g: Grid, extra, trip, upgrades, firm) -> dict:
    """The same case with every plant running (cached: the UI trips plant after plant on one case)."""
    with _tracer_lock:
        got = _baselines.get(key)
        if got is not None:
            _baselines.move_to_end(key)
            return got
    r = cascade(g, extra, trip, upgrades, firm)
    got = {
        "people": int(r.get("people", r.get("homes", 0)) or 0),
        "lost_mw": r["lost_mw"],
        "steps": r["total_steps"],
        "outcome": r["outcome"],
    }
    with _tracer_lock:
        _baselines[key] = got
        while len(_baselines) > 32:
            _baselines.popitem(last=False)
    return got


def _case_key(code: str, g: Grid, sites, trip, upgrades, firm) -> tuple:
    return (
        code,
        round(float(g.load_factor), 2),
        tuple((round(s.lat, 5), round(s.lon, 5), round(s.mw, 3)) for s in sites),
        tuple(trip),
        tuple(sorted(upgrades.items())),
        bool(firm),
    )


def _supply(g: Grid, extra: np.ndarray) -> dict:
    load = float(g.pd.sum() + extra.sum())
    cap = float(g.pmax.sum())
    ties = float(g.tie.sum())
    return {
        "capacity_mw": round(cap),
        "demand_mw": round(load),
        "net_import_mw": round(ties),
        "short_mw": round(max(0.0, load - ties - cap)),  # not enough generation left at all
    }


# ---------------------------------------------------------------------------------------- endpoints
@router.get("/api/plants")
def list_plants(region: str = Query("FL"), load_factor: float = Query(1.0)):
    code = region_code(region)
    g = grid_at(load_factor, code)
    idx = region_plants(code)
    gen = g.base.gen
    plants, by_fuel = [], {}
    for p in idx["plants"]:
        out = round(plant_output(g, gen, p), 1)
        plants.append({**_summary(p), "output": out})
        f = by_fuel.setdefault(p["fuel"], {"count": 0, "pmax": 0.0, "pg": 0.0, "output": 0.0})
        f["count"] += 1
        f["pmax"] += p["pmax"]
        f["pg"] += p["pg"]
        f["output"] += out
    for f in by_fuel.values():
        for k in ("pmax", "pg", "output"):
            f[k] = round(f[k], 1)
    return {
        "region": code,
        "region_name": REGIONS[code]["name"],
        "load_factor": g.load_factor,
        "plants": plants,
        "by_fuel": by_fuel,
        "fuels": sorted(by_fuel, key=lambda k: -by_fuel[k]["pmax"]),
        "total_pmax": round(sum(p["pmax"] for p in idx["plants"]), 1),
        "total_output": round(sum(p["output"] for p in plants), 1),
        "total_load": round(float(g.pd.sum()), 1),
        "net_import_mw": round(float(g.tie.sum()), 1),
        "synthetic": True,
        "source": idx["source"],
    }


UNSOLVABLE = (
    "The model can't settle this case: part of the grid is left sending out more power than it can make. "
    "Try taking out fewer plants."
)


@router.post("/api/plants/trip")
@limiter.limit("60/minute")
def trip_plants(request: Request, body: PlantCaseIn):
    code = region_code(body.region)
    g, sites, trip, upgrades = check_case(body)
    removed = resolve_removed(code, body.outages, body.retire_fuels)
    try:
        return _trip(code, g, sites, trip, upgrades, removed, body)
    except (ArithmeticError, np.linalg.LinAlgError, RuntimeError):
        # an engine edge case (an islanded export tie with too little generation behind it) — a
        # readable refusal, never a 500; logged so it can be fixed in powerflow.py
        logging.getLogger("uvicorn.error").exception("plants trip %s %s could not be solved", code, [p["id"] for p in removed][:20])
        raise HTTPException(status_code=422, detail=UNSOLVABLE) from None


def _trip(code: str, g: Grid, sites, trip: list[int], upgrades: dict[int, float], removed: list[dict], body: PlantCaseIn) -> dict:
    firm_flag = bool(getattr(body, "firm", False))
    c = grid_without(g, code, removed)
    extra, header = _header(c, sites, trip, upgrades)  # same buses in g and c: one extra vector serves both
    firm = _firm_buses(c, sites, firm_flag)
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[bid]] = False
    rate = g.rates_with(upgrades)
    before = g.solve(active, extra, rate)  # the case with every plant running, before any cascade
    removed_output = sum(plant_output(g, before.gen, p) for p in removed)

    res = cascade(c, extra, trip, upgrades, firm)
    after = c.solve(active, extra, rate)  # the moment the plants go: before any line trips
    over = c.overloaded(after)
    lost_now = float(getattr(after, "lost_existing_mw", after.lost_mw))
    if not res["steps"] and lost_now > 0.5:
        # Not enough generation left, yet no line overloads: the engine sheds load at once and the
        # cascade has no steps. Report that moment as one step so a replay shows who went dark.
        lost = c.lost_by_sub(after)
        res["steps"] = [
            {
                "n": 0,
                "action": "plant",
                "tripped": [],
                "held_line": None,
                "dark_subs": sorted({int(c.sub_ids[c.bus_sub_idx[i]]) for i in np.flatnonzero(after.dark_bus)}),
                "newly_affected": sorted(([sid, mw] for sid, mw in lost.items()), key=lambda x: -x[1]),
                "hot": [],
                "lost_mw": round(lost_now, 1),
                "homes": int(round(lost_now * HOMES_PER_MW)),
                "people": people(c, lost_now),
                "shed_mw": 0.0,
                "site_dark_mw": round(float(getattr(after, "lost_extra_mw", 0.0)), 1),
            }
        ]
    base = _baseline(_case_key(code, g, sites, trip, upgrades, firm_flag), g, extra, trip, upgrades, firm)
    ppl = int(res.get("people", res.get("homes", 0)) or 0)
    return {
        **header,
        "region": code,
        **res,
        "removed_mw": round(sum(p["pmax"] for p in removed), 1),  # capacity taken out
        "removed_output_mw": round(removed_output, 1),  # what it was producing in this case
        "removed": [_summary(p) for p in removed],
        "retired_fuels": list(dict.fromkeys(normalize_fuel(f) for f in body.retire_fuels)),
        "initial": {
            "overloaded": len(over),
            "lines": over[:20],
            "lost_mw": round(lost_now, 1),
            "people": people(c, lost_now),
        },
        "baseline": base,
        "added_people": max(0, ppl - base["people"]),
        "supply": _supply(c, extra),
        "estimate": True,
    }


def _plant_or_422(code: str, plant_id: int) -> dict:
    p = region_plants(code)["by_id"].get(plant_id)
    if p is None:
        raise HTTPException(status_code=422, detail=f"Unknown plant id {plant_id} in {REGIONS[code]['name']}")
    return p


@router.get("/api/plants/{region}/{plant_id}/trace")
@limiter.limit("120/minute")
def trace_base(request: Request, region: str, plant_id: int, load_factor: float = Query(1.0)):
    code = region_code(region)
    p = _plant_or_422(code, plant_id)
    g = grid_at(load_factor, code)
    t0 = time.perf_counter()
    tr = base_tracer(g, code)
    out = trace(g, code, tr, p)
    return {"region": code, "plant": _summary(p), "state": "base", "load_factor": g.load_factor, **out, "method": METHOD,
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


@router.post("/api/plants/{region}/{plant_id}/trace")
@limiter.limit("60/minute")
def trace_case(request: Request, region: str, plant_id: int, body: PlantCaseIn):
    code = region_code(region)
    p = _plant_or_422(code, plant_id)
    body = body.model_copy(update={"region": code})
    g, sites, trip, upgrades = check_case(body)
    removed = resolve_removed(code, body.outages, body.retire_fuels)
    t0 = time.perf_counter()
    is_out = any(int(r["id"]) == plant_id for r in removed)
    try:
        c = grid_without(g, code, removed)
        extra = _extra(c, sites)
        active = np.ones(c.m, dtype=bool)
        for bid in trip:
            active[c.br_index[bid]] = False
        state = c.solve(active, extra, c.rates_with(upgrades))
        if is_out:  # tripped in this case: it feeds no one
            out = _no_trace(0.0)
        else:
            out = trace(c, code, Tracer(c, state), p)
    except (ArithmeticError, np.linalg.LinAlgError, RuntimeError):
        logging.getLogger("uvicorn.error").exception("plants trace %s %s could not be solved", code, plant_id)
        raise HTTPException(status_code=422, detail=UNSOLVABLE) from None
    return {"region": code, "plant": _summary(p), "state": "case", "out": is_out, "load_factor": c.load_factor, **out,
            "method": METHOD + " (the case before any line trips)", "ms": round((time.perf_counter() - t0) * 1000, 1)}


# ---------------------------------------------------------------------------------------- ranking
class _Job:
    def __init__(self, key: tuple):
        self.key = key
        self.status = "computing"
        self.done = 0
        self.total = 0
        self.ranking: list[dict] | None = None
        self.baseline: dict | None = None
        self.unsolved: list[dict] = []  # plants the engine couldn't settle when tripped (edge cases)
        self.error: str | None = None
        self.elapsed: float | None = None
        self.future = None
        self.polled = time.monotonic()  # last time someone asked for it


class _Abandoned(Exception):
    pass


_jobs: "OrderedDict[tuple, _Job]" = OrderedDict()
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plants-ranking")
MAX_JOBS = 24
MAX_PENDING = 3
ABANDON_S = 10.0  # a running job nobody has polled for this long gives way to a queued one


def _stale(job: _Job) -> bool:
    """Nobody is watching this running job and another one is waiting for the worker (the user
    moved the heat clock or the campus on): stop, so the ranking they're looking at comes sooner."""
    if time.monotonic() - job.polled <= ABANDON_S:
        return False
    with _jobs_lock:
        return any(j is not job and j.status == "computing" for j in _jobs.values())


def _rank(job: _Job) -> None:
    code, lf, bus, mw, firm = job.key
    t0 = time.perf_counter()
    try:
        g = grid_at(lf, code)
        idx = region_plants(code)
        extra = np.zeros(g.n)
        if bus is not None:
            extra[bus] = mw
        firm_buses = [bus] if (firm and bus is not None) else None
        base = cascade(g, extra, [], {}, firm_buses)
        base_people = int(base.get("people", base.get("homes", 0)) or 0)
        job.baseline = {"people": base_people, "lost_mw": base["lost_mw"], "steps": base["total_steps"], "outcome": base["outcome"]}
        before = g.solve(np.ones(g.m, dtype=bool), extra)
        subs = sorted(idx["by_sub"].items(), key=lambda kv: -sum(p["pmax"] for p in kv[1]))
        job.total = len(subs)
        out, unsolved = [], []
        without = lambda ps: grid_without(g, code, ps)  # noqa: E731
        if subs:  # use the fast copy only if it gives exactly what the rebuild gives on a real trip
            ps0 = subs[0][1]
            try:
                a = cascade(grid_without_fast(g, ps0), extra, [], {}, firm_buses)
                b = cascade(grid_without(g, code, ps0), extra, [], {}, firm_buses)
                sig = lambda r: (r["people"], r["lost_mw"], r["total_steps"], r["outcome"], [s["tripped"] for s in r["steps"]])  # noqa: E731
                if sig(a) == sig(b):
                    without = lambda ps: grid_without_fast(g, ps)  # noqa: E731
                else:
                    logging.getLogger("uvicorn.error").warning("plants ranking %s: fast copy disagrees, rebuilding per plant", job.key)
            except Exception:  # noqa: BLE001 — the rebuild path below reports it per plant
                pass
        for sid, ps in subs:
            if _stale(job):
                raise _Abandoned
            fuels = sorted({p["fuel"] for p in ps}, key=FUEL_ORDER.index)
            first = ps[0]
            plant = {
                "sub": sid,
                "sub_name": first["sub_name"],
                "area": first.get("area") or area_of(first["sub_name"]),
                "name": f"{first['sub_name']} {' + '.join(fuels)}",
                "fuels": fuels,
                "ids": [int(p["id"]) for p in ps],
                "pmax": round(sum(p["pmax"] for p in ps), 1),
                "output": round(sum(plant_output(g, before.gen, p) for p in ps), 1),
                "lat": first["lat"],
                "lon": first["lon"],
            }
            try:
                r = cascade(without(ps), extra, [], {}, firm_buses)
            except (ArithmeticError, np.linalg.LinAlgError, RuntimeError):
                unsolved.append(plant)  # an engine edge case: left out of the ranking, listed apart
                job.done += 1
                continue
            ppl = int(r.get("people", r.get("homes", 0)) or 0)
            out.append(
                {
                    "plant": plant,
                    "people": ppl,
                    "added_people": max(0, ppl - base_people),
                    "lost_mw": r["lost_mw"],
                    "steps": r["total_steps"],
                    "outcome": r["outcome"],
                    "capped": bool(r.get("capped", False)),
                }
            )
            job.done += 1
        out.sort(key=lambda e: (-e["people"], -e["lost_mw"], -e["steps"], -e["plant"]["pmax"]))
        job.ranking = out
        job.unsolved = unsolved
        job.status = "ready"
    except _Abandoned:
        with _jobs_lock:  # forgotten: a later poll for this case starts it afresh
            if _jobs.get(job.key) is job:
                del _jobs[job.key]
        job.status = "abandoned"
    except Exception as e:  # noqa: BLE001 — surfaced through the status endpoint
        logging.getLogger("uvicorn.error").exception("plants ranking %s failed", job.key)
        job.error = str(e) or type(e).__name__
        job.status = "error"
    job.elapsed = round(time.perf_counter() - t0, 2)


def ranking_job(key: tuple) -> _Job:
    """The job for `key`, started if new. Old queued jobs past MAX_PENDING are dropped (a user
    dragging the heat slider only cares about the latest level)."""
    with _jobs_lock:
        job = _jobs.get(key)
        if job is not None and job.status == "error":
            del _jobs[key]  # let the next poll retry
            return job
        if job is not None:
            job.polled = time.monotonic()
            _jobs.move_to_end(key)
            return job
        job = _Job(key)
        _jobs[key] = job
        pending = [j for j in _jobs.values() if j.status == "computing" and j.done == 0 and j.total == 0 and j is not job]
        for old in pending[: max(0, len(pending) - MAX_PENDING + 1)]:
            if old.future is not None and old.future.cancel():
                _jobs.pop(old.key, None)
        while len(_jobs) > MAX_JOBS:
            oldest = next((k for k, j in _jobs.items() if j.status != "computing"), None)
            if oldest is None:
                break
            del _jobs[oldest]
        job.future = _executor.submit(_rank, job)
    return job


@router.get("/api/plants/ranking")
@limiter.limit("120/minute")
def ranking(
    request: Request,
    region: str = Query("FL"),
    load_factor: float = Query(1.0),
    lat: float | None = Query(None),
    lon: float | None = Query(None),
    mw: float | None = Query(None),
    firm: bool = Query(False),
):
    """The plants this state can least afford to lose, at this load level (and with this campus
    placed, if lat/lon/mw are given): each substation's plants tripped alone, the same cascade run.
    Polled: status "computing" with progress until "ready"."""
    code = region_code(region)
    g = grid_at(load_factor, code)
    given = [v is not None for v in (lat, lon, mw)]
    if any(given) and not all(given):
        raise HTTPException(status_code=422, detail="A data center needs lat, lon and mw together")
    bus = None
    if all(given):
        check_site(lat, lon, mw, code)
        bus = int(g.site_bus(lat, lon))
        mw = round(float(mw))
    job = ranking_job((code, round(float(g.load_factor), 2), bus, mw if bus is not None else None, bool(firm and bus is not None)))
    if job.status == "error":
        raise HTTPException(status_code=503, detail="The ranking couldn't be computed — try again")
    site = None
    if bus is not None:
        s = int(g.bus_sub_idx[bus])
        site = {"bus": int(g.bus_ids[bus]), "sub": int(g.sub_ids[s]), "sub_name": g.sub_name[s], "mw": mw, "firm": bool(firm)}
    return {
        "region": code,
        "load_factor": g.load_factor,
        "site": site,
        "status": "computing" if job.status == "abandoned" else job.status,  # the next poll restarts it
        "done": job.done,
        "total": job.total or len(region_plants(code)["by_sub"]),
        "baseline": job.baseline,
        "ranking": job.ranking if job.status == "ready" else [],
        "unsolved": job.unsolved if job.status == "ready" else [],
        "elapsed_s": job.elapsed,
        "estimate": True,
    }


def _warm() -> None:
    """Rank the demo state at today's load in the background once the server is up."""
    for code in [c.strip().upper() for c in os.getenv("PLANTS_WARM", "FL").split(",") if c.strip()]:
        if code in REGIONS:
            ranking_job((code, 1.0, None, None, False))


_timer = threading.Timer(3.0, _warm)
_timer.daemon = True
_timer.start()
