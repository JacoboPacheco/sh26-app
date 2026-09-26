"""The grid endpoints (SPEC.md → New endpoints): the drawable grid, a what-if, the cascade, and the
headroom heatmap. All the math lives in powerflow.py; this module validates input and shapes the
JSON. Public (no login) — the grid is the same for everyone — with per-visitor rate limits.

A request describes a *case*: one or more data centers (`lat/lon/mw` for the main one, `sites` for
more — AI-boom mode), a `load_factor` (heat-wave clock), branches knocked out first (`trip` —
hurricane mode) and rating `upgrades` (Fix it). Every field but the site is optional, so the
original `{lat, lon, mw}` body still works.

Regions: every state in the lower 48 has its own validated model (backend/demo/build_states.py →
grids/index.json). A case names its `region` (default "FL"); a state's model is loaded on first use
and each (region, load level) Grid stays in a small LRU cache — Render's free tier has 512 MB.
Florida at load 1.0 is loaded at import and never evicted.

People without power (an estimate): every model's load stands for its state's residents
(backend/demo/population.json, Census Vintage 2024), so people = lost MW x population / the model's
base load (`people_per_mw`, the same at every load level), capped at the population.

Firm vs flexible (`firm` on a case): a firm campus is kept on by cutting other customers instead
(powerflow.cascade_case → firm_buses); flexible, the default, is the plain cascade.
"""

import json
import math
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from limiter import limiter
from powerflow import Grid, area_of

router = APIRouter(tags=["grid"])

DEMO = Path(__file__).parent / "demo"
_INDEX = json.loads((DEMO / "grids" / "index.json").read_text(encoding="utf-8"))
REGIONS: dict[str, dict] = _INDEX["regions"]
REGION_SOURCE: str = _INDEX.get("source", "")
DEFAULT_REGION = "FL"
_POP = json.loads((DEMO / "population.json").read_text(encoding="utf-8"))
POPULATION: dict[str, int] = _POP["states"]
POPULATION_SOURCE: str = _POP["source"]
PEOPLE_PER_HOUSEHOLD: float = float(_POP["people_per_household"])


def people_per_mw(code: str) -> float:
    """Residents per MW of the state model's base load (an estimate)."""
    return POPULATION.get(code, 0) / max(float(REGIONS[code]["load_mw"]), 1.0)


def _with_people(g: Grid, code: str) -> Grid:
    g.people_per_mw = people_per_mw(code)
    g.population = POPULATION.get(code)
    return g


GRID_PATH = DEMO / "florida_grid.json"
GRID = _with_people(Grid.from_file(str(GRID_PATH)), DEFAULT_REGION)
DRAWABLE = GRID.drawable()
GRID.headroom_all()  # fill the base level's headroom cache at startup

# Florida's bounding box (a little wider than the state so the offshore synthetic subs fit)
LAT_MIN, LAT_MAX = 24.3, 31.1
LON_MIN, LON_MAX = -87.7, -79.4
MW_MIN, MW_MAX = 1, 50000  # up to 50 GW: the custom size is for "what if" extremes
# the grid model is peninsular Florida only; a drop farther than this from any substation is refused
MAX_SNAP_KM = 75.0
LOAD_FACTOR_MIN, LOAD_FACTOR_MAX = 0.4, 1.4
MAX_SITES = 12
MAX_TRIPS = 400
MAX_UPGRADES = 200

MAX_LOADED = 8  # (region, level) models kept in memory

_cache: "OrderedDict[tuple[str, float], Grid]" = OrderedDict({(DEFAULT_REGION, 1.0): GRID})
_drawables: "OrderedDict[str, dict]" = OrderedDict()  # filled by drawable(); Florida stamped at import below
_lock = threading.Lock()


def region_code(region: str | None) -> str:
    code = (region or DEFAULT_REGION).strip().upper()
    if code not in REGIONS:
        raise HTTPException(status_code=422, detail=f"Unknown region {region!r} — use a two-letter state code")
    return code


def grid_at(load_factor: float = 1.0, region: str | None = None) -> Grid:
    """The model of `region` at a load level (rounded to 0.01), from the LRU cache. Thread-safe."""
    code = region_code(region)
    f = round(float(load_factor), 2)
    if not (LOAD_FACTOR_MIN <= f <= LOAD_FACTOR_MAX):
        raise HTTPException(status_code=422, detail=f"Load level must be between {LOAD_FACTOR_MIN} and {LOAD_FACTOR_MAX}")
    key = (code, f)
    with _lock:
        g = _cache.get(key)
        if g is None:
            base = _cache.get((code, 1.0))
            if base is None:
                base = _with_people(Grid.from_file(str(DEMO / REGIONS[code]["file"])), code)
                _cache[(code, 1.0)] = base
            g = base if f == 1.0 else base.variant(f)
            _cache[key] = g
            while len(_cache) > MAX_LOADED:
                old = next(k for k in _cache if k != (DEFAULT_REGION, 1.0) and k != key and k != (code, 1.0))
                del _cache[old]
        _cache.move_to_end(key)
    return g


def site_headroom(g: Grid, bus: int) -> float:
    """MW this bus can take before the first overload: from the cached vector when the heatmap was
    computed, else one solve (so the first drop in a big state doesn't wait for the whole map)."""
    hb = g._headroom_bus
    return float(min(hb[bus], 1e6)) if hb is not None else float(g.headroom_bus(bus))


def _region_meta(code: str, d: dict) -> dict:
    """Stamp a drawable payload with its region: name, bbox, load, population (people per MW)."""
    r = REGIONS[code]
    d["meta"].update(
        {
            "region": code,
            "region_name": r["name"],
            "bbox": r["bbox"],
            "center": r["center"],
            "interconnect": r["interconnect"],
            "load_mw": r["load_mw"],
            "population": POPULATION.get(code),
            "people_per_mw": round(people_per_mw(code), 2),
            "population_source": POPULATION_SOURCE,
        }
    )
    return d


def drawable(region: str | None = None) -> dict:
    code = region_code(region)
    with _lock:
        d = _drawables.get(code)
        if d is not None:
            _drawables.move_to_end(code)
            return d
    d = _region_meta(code, grid_at(1.0, code).drawable())
    with _lock:
        _drawables[code] = d
        while len(_drawables) > MAX_LOADED:
            old = next((k for k in _drawables if k != DEFAULT_REGION), None)
            if old is None:
                break
            del _drawables[old]
    return d


class SiteIn(BaseModel):
    lat: float
    lon: float
    mw: float


class CaseIn(BaseModel):
    region: str = DEFAULT_REGION  # a two-letter state code (grids/index.json)
    lat: float | None = None  # the main data center (optional: a hurricane or heat wave can run alone)
    lon: float | None = None
    mw: float | None = None
    sites: list[SiteIn] = Field(default_factory=list)  # more data centers (AI-boom mode)
    load_factor: float = 1.0  # 1.0 = the dataset's snapshot; the heat-wave clock scales it
    trip: list[int] = Field(default_factory=list)  # branch ids knocked out first (hurricane mode)
    upgrades: dict[int, float] = Field(default_factory=dict)  # branch id -> new rating MVA (Fix it)
    firm: bool = False  # every campus in the case on firm service (kept on; others are cut instead)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def check_site(lat: float, lon: float, mw: float, region: str | None = None) -> None:
    """422 with a sentence the UI can show as-is. Shared with scenarios.py."""
    code = region_code(region)
    name = REGIONS[code]["name"]
    if not all(math.isfinite(v) for v in (lat, lon, mw)):
        raise HTTPException(status_code=422, detail="lat, lon and mw must be numbers")
    x0, y0, x1, y1 = REGIONS[code]["bbox"]
    if code == DEFAULT_REGION:
        x0, y0, x1, y1 = LON_MIN, LAT_MIN, LON_MAX, LAT_MAX
    if not (y0 - 0.5 <= lat <= y1 + 0.5 and x0 - 0.5 <= lon <= x1 + 0.5):
        raise HTTPException(status_code=422, detail=f"That point is outside {name} — drop the data center on the map")
    if not (MW_MIN <= mw <= MW_MAX):
        raise HTTPException(status_code=422, detail=f"Size must be between {MW_MIN} and {MW_MAX:,} MW")
    g = grid_at(1.0, code)
    s = g.nearest_sub(lat, lon)
    if _km(lat, lon, g.sub_lat[s], g.sub_lon[s]) > MAX_SNAP_KM:
        where = "peninsular Florida" if code == DEFAULT_REGION else name
        raise HTTPException(status_code=422, detail=f"No grid here — the model covers {where} only")


def case_sites(body: CaseIn, region: str | None = None) -> list[SiteIn]:
    """Every data center in the case, the main one first. Validates each."""
    sites = []
    if body.lat is not None or body.lon is not None or body.mw is not None:
        if body.lat is None or body.lon is None or body.mw is None:
            raise HTTPException(status_code=422, detail="A data center needs lat, lon and mw together")
        sites.append(SiteIn(lat=body.lat, lon=body.lon, mw=body.mw))
    sites += body.sites
    if len(sites) > MAX_SITES:
        raise HTTPException(status_code=422, detail=f"At most {MAX_SITES} data centers at once")
    for s in sites:
        check_site(s.lat, s.lon, s.mw, region or body.region)
    return sites


def check_case(body: CaseIn) -> tuple[Grid, list[SiteIn], list[int], dict[int, float]]:
    g = grid_at(body.load_factor, body.region)
    sites = case_sites(body, body.region)
    if len(body.trip) > MAX_TRIPS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_TRIPS} lines knocked out at once")
    unknown = [b for b in body.trip if b not in g.br_index]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown line id {unknown[0]}")
    if len(body.upgrades) > MAX_UPGRADES:
        raise HTTPException(status_code=422, detail=f"At most {MAX_UPGRADES} upgrades at once")
    for bid, new in body.upgrades.items():
        if bid not in g.br_index:
            raise HTTPException(status_code=422, detail=f"Unknown line id {bid}")
        cur = float(g.rate[g.br_index[bid]])
        if not (math.isfinite(new) and cur <= new <= cur * 5):
            raise HTTPException(status_code=422, detail="An upgrade must be between the line's rating and 5x it")
    return g, sites, list(dict.fromkeys(body.trip)), dict(body.upgrades)


def case_firm_buses(g: Grid, sites: list[SiteIn], firm: bool) -> list[int] | None:
    """The bus indices of the case's campuses when the case is firm, for Grid.cascade_case(firm_buses=...)."""
    return [g.site_bus(s.lat, s.lon) for s in sites] if firm and sites else None


def people_fields(g: Grid, lost_mw: float) -> dict:
    """The people-without-power fields every what-if carries (estimates)."""
    return {"people": g.people(lost_mw), "people_per_mw": round(g.people_per_mw, 2), "population": g.population}


def site_summary(lat: float, lon: float, mw: float) -> dict:
    """The what-if at one site on the base grid, without the per-branch array (scenarios.py stores this)."""
    bus = GRID.site_bus(lat, lon)
    state = GRID.whatif(bus, mw)
    return {
        "bus": bus,
        "state": state,
        "overloaded": GRID.overloaded(state),
        "headroom_mw": round(float(min(GRID.headroom_all()[bus], 1e6)), 1),
    }


def _site_info(g: Grid, bus: int, mw: float) -> dict:
    s = int(g.bus_sub_idx[bus])
    return {
        "bus": int(g.bus_ids[bus]),
        "sub": int(g.sub_ids[s]),
        "sub_name": g.sub_name[s],
        "sub_area": area_of(g.sub_name[s]),  # the town the substation is named after, e.g. "Fort Myers"
        "sub_lat": round(float(g.sub_lat[s]), 4),
        "sub_lon": round(float(g.sub_lon[s]), 4),
        "kv": float(g.bus_kv[bus]),
        "mw": mw,
        "headroom_mw": round(site_headroom(g, bus), 1),  # this site alone, at this load level
    }


def _case_header(g: Grid, sites: list[SiteIn], trip: list[int], upgrades: dict[int, float]) -> tuple[np.ndarray, dict]:
    buses = [g.site_bus(s.lat, s.lon) for s in sites]
    infos = [_site_info(g, b, s.mw) for b, s in zip(buses, sites)]
    main = infos[0] if infos else {k: None for k in ("bus", "sub", "sub_name", "sub_area", "sub_lat", "sub_lon", "kv", "headroom_mw")}
    header = {
        **main,
        "mw": sum(s.mw for s in sites),  # total added load
        "sites": infos,
        "load_factor": g.load_factor,
        "total_load_mw": round(float(g.pd.sum())),
        "trip": trip,
        "upgrades": {str(k): v for k, v in upgrades.items()},
    }
    return g.extra_load([(b, s.mw) for b, s in zip(buses, sites)]), header


@router.get("/api/regions")
def regions():
    """Every state model: name, size, load, bounding box, validation numbers."""
    keep = ("code", "name", "interconnect", "buses", "subs", "branches", "load_mw", "gen_mw", "bbox", "center", "corr", "base_max_pct", "overload_500", "headroom_p50", "headroom_max", "valid")
    return {
        "regions": [
            {**{k: r[k] for k in keep}, "population": POPULATION.get(r["code"]), "people_per_mw": round(people_per_mw(r["code"]), 2)}
            for r in REGIONS.values()
        ],
        "default": DEFAULT_REGION,
        "source": REGION_SOURCE,
        "population_source": POPULATION_SOURCE,
    }


@router.get("/api/grid")
def get_grid(region: str = Query(DEFAULT_REGION)):
    return drawable(region)


@router.post("/api/grid/whatif")
@limiter.limit("120/minute")  # cheap (one sparse solve); the size slider fires it on every settle
def whatif(request: Request, body: CaseIn):
    g, sites, trip, upgrades = check_case(body)
    extra, header = _case_header(g, sites, trip, upgrades)
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[bid]] = False
    state = g.solve(active, extra, g.rates_with(upgrades))
    over = g.overloaded(state)
    # each overloaded line's loading WITHOUT the campus (same network, trips and upgrades): the cause in two
    # numbers ("76 % without this campus, 141 % with it"); the stored base state when nothing is tripped or raised
    alone = g.base if not trip and not upgrades else g.solve(active, np.zeros(g.n), g.rates_with(upgrades))
    for o in over:
        o["base_pct"] = round(float(alone.loading_pct[g.br_index[o["id"]]]), 1)
    # "Where the people are": each overloaded line with the people its power flows on to — the same
    # rule the cascade's people-hit counter uses when that line fails — and everyone in the path, each once
    at_risk, path = [], set()
    for o in over[:12]:
        k = g.br_index[o["id"]]
        subs = g.downstream_subs(state, [k])
        path |= subs
        at_risk.append({"id": o["id"], "mw": round(abs(float(state.flow[k])), 1), "people": g.zone(subs)["people_zone"]})
    return {
        **header,
        "region": region_code(body.region),
        "loading_pct": np.round(state.loading_pct, 1).tolist(),  # aligned with /api/grid branches
        "flow_mw": np.round(state.flow).astype(int).tolist(),  # signed, from -> to positive
        "overloaded": over,
        "at_risk": sorted(at_risk, key=lambda a: -a["people"]),
        "at_risk_people": g.zone(path)["people_zone"],
        "lost_mw": round(state.lost_existing_mw, 1),
        **people_fields(g, state.lost_existing_mw),
        "firm": body.firm,
        "affected": g.lost_by_sub(state),
    }


@router.post("/api/grid/cascade")
@limiter.limit("120/minute")
def cascade(request: Request, body: CaseIn):
    g, sites, trip, upgrades = check_case(body)
    extra, header = _case_header(g, sites, trip, upgrades)
    firm = case_firm_buses(g, sites, body.firm)
    return {**header, "region": region_code(body.region), **g.cascade_case(extra, trip, upgrades, firm_buses=firm)}


@router.get("/api/grid/headroom")
def headroom(load_factor: float = Query(1.0), region: str = Query(DEFAULT_REGION)):
    code = region_code(region)
    return {"by_sub": grid_at(load_factor, code).headroom_by_sub(), "load_factor": round(load_factor, 2), "region": code}


_drawables[DEFAULT_REGION] = _region_meta(DEFAULT_REGION, DRAWABLE)  # Florida: /api/grid carries its region meta too
