"""The grid endpoints (SPEC.md → New endpoints): the drawable grid, a what-if drop, the cascade,
and the headroom heatmap. All the math lives in powerflow.py; this module validates input and
shapes the JSON. Public (no login) — the grid is the same for everyone — with per-visitor
rate limits on the POSTs.

The grid is loaded once at import from the committed backend/demo/florida_grid.json, and the
per-bus headroom vector is computed then too (~1 s), so every request is a single sparse solve.
"""

import math
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from limiter import limiter
from powerflow import Grid

router = APIRouter(tags=["grid"])

GRID_PATH = Path(__file__).parent / "demo" / "florida_grid.json"
GRID = Grid.from_file(str(GRID_PATH))
DRAWABLE = GRID.drawable()
HEADROOM_BY_SUB = GRID.headroom_by_sub()  # also fills GRID.headroom_all()'s cache

# Florida's bounding box (a little wider than the state so the offshore synthetic subs fit)
LAT_MIN, LAT_MAX = 24.3, 31.1
LON_MIN, LON_MAX = -87.7, -79.4
MW_MIN, MW_MAX = 1, 5000
# the grid model is peninsular Florida only; a drop farther than this from any substation is refused
MAX_SNAP_KM = 75.0


class SiteIn(BaseModel):
    lat: float
    lon: float
    mw: float


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


def site_summary(lat: float, lon: float, mw: float) -> dict:
    """The what-if at a site, without the per-branch array (scenarios.py stores this)."""
    bus = GRID.site_bus(lat, lon)
    state = GRID.whatif(bus, mw)
    return {
        "bus": bus,
        "state": state,
        "overloaded": GRID.overloaded(state),
        "headroom_mw": round(float(min(GRID.headroom_all()[bus], 1e6)), 1),
    }


def _site_info(bus: int) -> dict:
    s = int(GRID.bus_sub_idx[bus])
    return {
        "bus": int(GRID.bus_ids[bus]),
        "sub": int(GRID.sub_ids[s]),
        "sub_name": GRID.sub_name[s],
        "sub_lat": round(float(GRID.sub_lat[s]), 4),
        "sub_lon": round(float(GRID.sub_lon[s]), 4),
        "kv": float(GRID.bus_kv[bus]),
    }


@router.get("/api/grid")
def get_grid():
    return DRAWABLE


@router.post("/api/grid/whatif")
@limiter.limit("120/minute")  # cheap (one sparse solve); the size slider fires it on every settle
def whatif(request: Request, body: SiteIn):
    check_site(body.lat, body.lon, body.mw)
    res = site_summary(body.lat, body.lon, body.mw)
    state = res["state"]
    return {
        **_site_info(res["bus"]),
        "mw": body.mw,
        "loading_pct": np.round(state.loading_pct, 1).tolist(),  # aligned with /api/grid branches
        "overloaded": res["overloaded"],
        "headroom_mw": res["headroom_mw"],
        "lost_mw": round(state.lost_existing_mw, 1),
    }


@router.post("/api/grid/cascade")
@limiter.limit("120/minute")
def cascade(request: Request, body: SiteIn):
    check_site(body.lat, body.lon, body.mw)
    bus = GRID.site_bus(body.lat, body.lon)
    out = GRID.cascade(bus, body.mw)
    return {**_site_info(bus), "mw": body.mw, **out}


@router.get("/api/grid/headroom")
def headroom():
    return {"by_sub": HEADROOM_BY_SUB}
