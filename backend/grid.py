"""The grid endpoints (SPEC.md → New endpoints): the drawable grid, a what-if, the cascade, and the
headroom heatmap. All the math lives in powerflow.py; this module validates input and shapes the
JSON. Public (no login) — the grid is the same for everyone — with per-visitor rate limits.

A request describes a *case*: one or more data centers (`lat/lon/mw` for the main one, `sites` for
more — AI-boom mode), a `load_factor` (heat-wave clock), branches knocked out first (`trip` —
hurricane mode) and rating `upgrades` (Fix it). Every field but the site is optional, so the
original `{lat, lon, mw}` body still works.

The grid is loaded once at import from the committed backend/demo/florida_grid.json. Each load
level is its own Grid (a re-dispatched base case plus its headroom vector, ~0.6 s to build),
cached for the process's life — the UI offers a handful of levels, so the cache stays small.
"""

import math
import threading
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from limiter import limiter
from powerflow import Grid

router = APIRouter(tags=["grid"])

GRID_PATH = Path(__file__).parent / "demo" / "florida_grid.json"
GRID = Grid.from_file(str(GRID_PATH))
DRAWABLE = GRID.drawable()
GRID.headroom_all()  # fill the base level's headroom cache at startup

# Florida's bounding box (a little wider than the state so the offshore synthetic subs fit)
LAT_MIN, LAT_MAX = 24.3, 31.1
LON_MIN, LON_MAX = -87.7, -79.4
MW_MIN, MW_MAX = 1, 5000
# the grid model is peninsular Florida only; a drop farther than this from any substation is refused
MAX_SNAP_KM = 75.0
LOAD_FACTOR_MIN, LOAD_FACTOR_MAX = 0.4, 1.4
MAX_SITES = 12
MAX_TRIPS = 400
MAX_UPGRADES = 200

_levels: dict[float, Grid] = {1.0: GRID}
_levels_lock = threading.Lock()


def grid_at(load_factor: float) -> Grid:
    """The grid at a load level, rounded to 0.01 so the cache stays small. Thread-safe."""
    f = round(float(load_factor), 2)
    if not (LOAD_FACTOR_MIN <= f <= LOAD_FACTOR_MAX):
        raise HTTPException(status_code=422, detail=f"Load level must be between {LOAD_FACTOR_MIN} and {LOAD_FACTOR_MAX}")
    g = _levels.get(f)
    if g is None:
        with _levels_lock:
            g = _levels.get(f)
            if g is None:
                if len(_levels) >= 24:  # a script can't grow memory without bound
                    raise HTTPException(status_code=422, detail="Too many different load levels — pick a preset")
                g = GRID.variant(f)
                g.headroom_all()
                _levels[f] = g
    return g


class SiteIn(BaseModel):
    lat: float
    lon: float
    mw: float


class CaseIn(BaseModel):
    lat: float | None = None  # the main data center (optional: a hurricane or heat wave can run alone)
    lon: float | None = None
    mw: float | None = None
    sites: list[SiteIn] = Field(default_factory=list)  # more data centers (AI-boom mode)
    load_factor: float = 1.0  # 1.0 = the dataset's snapshot; the heat-wave clock scales it
    trip: list[int] = Field(default_factory=list)  # branch ids knocked out first (hurricane mode)
    upgrades: dict[int, float] = Field(default_factory=dict)  # branch id -> new rating MVA (Fix it)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def check_site(lat: float, lon: float, mw: float) -> None:
    """422 with a sentence the UI can show as-is. Shared with scenarios.py."""
    if not all(math.isfinite(v) for v in (lat, lon, mw)):
        raise HTTPException(status_code=422, detail="lat, lon and mw must be numbers")
    if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        raise HTTPException(status_code=422, detail="That point is outside Florida — drop the data center on the map")
    if not (MW_MIN <= mw <= MW_MAX):
        raise HTTPException(status_code=422, detail=f"Size must be between {MW_MIN} and {MW_MAX:,} MW")
    s = GRID.nearest_sub(lat, lon)
    if _km(lat, lon, GRID.sub_lat[s], GRID.sub_lon[s]) > MAX_SNAP_KM:
        raise HTTPException(status_code=422, detail="No grid here — the model covers peninsular Florida only")


def case_sites(body: CaseIn) -> list[SiteIn]:
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
        check_site(s.lat, s.lon, s.mw)
    return sites


def check_case(body: CaseIn) -> tuple[Grid, list[SiteIn], list[int], dict[int, float]]:
    g = grid_at(body.load_factor)
    sites = case_sites(body)
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
        "sub_lat": round(float(g.sub_lat[s]), 4),
        "sub_lon": round(float(g.sub_lon[s]), 4),
        "kv": float(g.bus_kv[bus]),
        "mw": mw,
        "headroom_mw": round(float(min(g.headroom_all()[bus], 1e6)), 1),  # this site alone, at this load level
    }


def _case_header(g: Grid, sites: list[SiteIn], trip: list[int], upgrades: dict[int, float]) -> tuple[np.ndarray, dict]:
    buses = [g.site_bus(s.lat, s.lon) for s in sites]
    infos = [_site_info(g, b, s.mw) for b, s in zip(buses, sites)]
    main = infos[0] if infos else {k: None for k in ("bus", "sub", "sub_name", "sub_lat", "sub_lon", "kv", "headroom_mw")}
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


@router.get("/api/grid")
def get_grid():
    return DRAWABLE


@router.post("/api/grid/whatif")
@limiter.limit("120/minute")  # cheap (one sparse solve); the size slider fires it on every settle
def whatif(request: Request, body: CaseIn):
    g, sites, trip, upgrades = check_case(body)
    extra, header = _case_header(g, sites, trip, upgrades)
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[bid]] = False
    state = g.solve(active, extra, g.rates_with(upgrades))
    return {
        **header,
        "loading_pct": np.round(state.loading_pct, 1).tolist(),  # aligned with /api/grid branches
        "flow_mw": np.round(state.flow).astype(int).tolist(),  # signed, from -> to positive
        "overloaded": g.overloaded(state),
        "lost_mw": round(state.lost_existing_mw, 1),
        "affected": g.lost_by_sub(state),
    }


@router.post("/api/grid/cascade")
@limiter.limit("120/minute")
def cascade(request: Request, body: CaseIn):
    g, sites, trip, upgrades = check_case(body)
    extra, header = _case_header(g, sites, trip, upgrades)
    return {**header, **g.cascade_case(extra, trip, upgrades)}


@router.get("/api/grid/headroom")
def headroom(load_factor: float = Query(1.0)):
    return {"by_sub": grid_at(load_factor).headroom_by_sub(), "load_factor": round(load_factor, 2)}
