"""Hospitals on backup power (an estimate): real hospitals from OpenStreetMap placed on the synthetic grid.

Every named hospital in the contiguous U.S. (backend/demo/hospitals_us.json, built once by
backend/demo/build_hospitals.py; © OpenStreetMap contributors, ODbL — credited wherever shown) is
matched to the nearest substation in its state's SYNTHETIC model that serves load, within
MAX_KM. A hospital farther than that is outside the model's reach and is never counted.

When a case loses load, a hospital is
  - "backup":   its substation lost at least BACKUP_SHARE of its load (the same rule that paints a
                substation dark on the map), so it would be running on its own generators;
  - "strained": its substation lost some load (at least 0.1 MW, the map's dimmed light) — partial
                outages in its area.
Both are estimates: a real hospital has its own feeders, priority restoration and generators.

    GET  /api/hospitals?region=ST   the state's hospitals, each with its substation and distance
    POST /api/hospitals/backup      a case (grid.CaseIn) → re-runs the cascade → who is on backup

`status_for(code, g, affected)` is the same classification for another router that already has a
cascade's `affected` ({sub id: MW lost}), e.g. a briefing.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Query, Request

from grid import REGIONS, CaseIn, _case_header, case_firm_buses, check_case, grid_at, people_fields, region_code
from limiter import limiter
from powerflow import Grid, area_of

router = APIRouter(tags=["hospitals"])

DATA = Path(__file__).parent / "demo" / "hospitals_us.json"
SOURCE = "© OpenStreetMap contributors, ODbL"
LICENSE_URL = "https://www.openstreetmap.org/copyright"
MAX_KM = 50.0  # farther than this from every load-serving substation: outside the model's reach
MIN_SUB_LOAD_MW = 1.0  # a substation that serves load (generator-only substations feed no hospital)
BACKUP_SHARE = 0.6  # matches the map (store.jsx view): a substation that lost >= 60 % of its load is dark
STRAINED_MW = 0.1  # matches Grid.lost_by_sub's min_mw: any loss the map shows as a dimmed light
ASSUMPTION = (
    "A hospital counts as on backup power when the substation nearest it in the synthetic model lost at "
    "least 60% of its load. Real hospitals have their own feeders, generators and priority restoration."
)
# hospitals of places with no model of their own, shown with a neighbor's model
EXTRA_STATES = {"MD": ["DC"]}


def _load() -> dict:
    try:
        doc = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    doc.setdefault("hospitals", [])
    doc.setdefault("source", SOURCE)
    doc.setdefault("license_url", LICENSE_URL)
    return doc


_DOC = _load()
_BY_STATE: dict[str, list[dict]] = {}
for _h in _DOC["hospitals"]:
    _BY_STATE.setdefault(str(_h.get("state", "")).upper(), []).append(_h)

_index: dict[str, dict] = {}
_lock = threading.Lock()


def _km_to(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    p1 = math.radians(lat)
    p2 = np.radians(lats)
    a = np.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(np.radians(lons - lon) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.minimum(a, 1.0)))


def region_index(code: str) -> dict:
    """The state's hospitals matched to its model's substations (computed once per region, cached).

    {hospitals: [row], sub_idx: np.ndarray (substation index per in-model hospital, -1 outside),
     base_load: np.ndarray (MW per substation at the base level)}"""
    with _lock:
        hit = _index.get(code)
    if hit is not None:
        return hit
    g = grid_at(1.0, code)
    base_load = np.bincount(g.bus_sub_idx, weights=g.pd, minlength=len(g.sub_ids))
    serving = np.flatnonzero(base_load >= MIN_SUB_LOAD_MW)
    lats, lons = g.sub_lat[serving], g.sub_lon[serving]
    src = _BY_STATE.get(code, []) + [h for extra in EXTRA_STATES.get(code, []) for h in _BY_STATE.get(extra, [])]
    rows, sub_idx = [], []
    for i, h in enumerate(src):
        d = _km_to(float(h["lat"]), float(h["lon"]), lats, lons) if len(serving) else np.array([np.inf])
        j = int(np.argmin(d))
        km = float(d[j])
        inside = km <= MAX_KM
        s = int(serving[j]) if inside else -1
        row = {
            "id": i,
            "name": h["name"],
            "lat": h["lat"],
            "lon": h["lon"],
            "state": h.get("state", code),
            "emergency": bool(h.get("emergency")),
            "beds": h.get("beds"),
            "in_model": inside,
            "sub": int(g.sub_ids[s]) if inside else None,
            "sub_name": g.sub_name[s] if inside else None,
            "area": area_of(g.sub_name[s]) if inside else None,
            "km": round(km, 1) if inside else None,
            # 3 decimals: the browser applies the 60 % rule to this, and must agree with status_for
            "sub_load_mw": round(float(base_load[s]), 3) if inside else None,
        }
        rows.append(row)
        sub_idx.append(s)
    out = {"hospitals": rows, "sub_idx": np.array(sub_idx, dtype=int), "base_load": base_load}
    with _lock:
        _index[code] = out
    return out


def status_for(code: str, g: Grid, affected: dict[int, float], first_step: dict[int, int] | None = None) -> dict:
    """Classify the region's hospitals against `affected` ({sub id: existing MW lost}) on grid `g`
    (the case's load level). Returns {counts, backup: [row], strained: [row]}; rows gain lost_mw,
    share (of the substation's load, 0..1), status and first_step (the cascade step at which its
    substation first lost load, from `first_step` {sub id: step}; None without one). Each list is in
    the order the cascade reached them, then by name (the UI uses the same order)."""
    idx = region_index(code)
    load = np.bincount(g.bus_sub_idx, weights=g.pd, minlength=len(g.sub_ids))
    backup, strained = [], []
    in_model = 0
    for row, s in zip(idx["hospitals"], idx["sub_idx"]):
        if s < 0:
            continue
        in_model += 1
        lost = float(affected.get(row["sub"], 0.0))
        if lost < STRAINED_MW:
            continue
        share = min(lost / max(float(load[s]), 0.1), 1.0)
        status = "backup" if lost >= BACKUP_SHARE * max(float(load[s]), 0.1) else "strained"
        step = (first_step or {}).get(row["sub"])
        item = {**row, "lost_mw": round(lost, 1), "share": round(share, 3), "status": status, "first_step": step}
        (backup if status == "backup" else strained).append(item)
    order = lambda r: (r["first_step"] if r["first_step"] is not None else 0, r["name"].lower())  # noqa: E731
    backup.sort(key=order)
    strained.sort(key=order)
    total = len(idx["hospitals"])
    return {
        "counts": {
            "backup": len(backup),
            "strained": len(strained),
            "ok": in_model - len(backup) - len(strained),
            "in_model": in_model,
            "outside_model": total - in_model,
            "total": total,
        },
        "backup": backup,
        "strained": strained,
    }


def _meta(code: str) -> dict:
    return {
        "region": code,
        "region_name": REGIONS[code]["name"],
        "source": _DOC["source"],
        "license_url": _DOC["license_url"],
        "generated": _DOC.get("generated"),
        "backup_share": BACKUP_SHARE,
        "strained_mw": STRAINED_MW,
        "max_km": MAX_KM,
        "assumption": ASSUMPTION,
    }


@router.get("/api/hospitals")
def list_hospitals(region: str = Query("FL")):
    """The state's hospitals, each with the model substation it's matched to (or in_model false)."""
    code = region_code(region)
    idx = region_index(code)
    rows = idx["hospitals"]
    inside = sum(1 for r in rows if r["in_model"])
    return {**_meta(code), "count": len(rows), "in_model": inside, "outside_model": len(rows) - inside, "hospitals": rows}


@router.post("/api/hospitals/backup")
@limiter.limit("60/minute")
def hospitals_backup(request: Request, body: CaseIn):
    """Re-run the case's cascade and list the hospitals on backup power (and in partial outages)."""
    g, sites, trip, upgrades = check_case(body)
    code = region_code(body.region)
    extra, header = _case_header(g, sites, trip, upgrades)
    firm = case_firm_buses(g, sites, body.firm)
    c = g.cascade_case(extra, trip, upgrades, firm_buses=firm)
    affected = {int(k): float(v) for k, v in c["affected"].items()}
    # the step at which each substation first lost load (0 = the storm, before any line tripped)
    first_step: dict[int, int] = {}
    for st in c["steps"]:
        for sid, _mw in st["newly_affected"]:
            first_step.setdefault(int(sid), int(st["n"]))
    res = status_for(code, g, affected, first_step)
    return {
        **_meta(code),
        "firm": body.firm,
        "load_factor": g.load_factor,
        "mw": header["mw"],
        "outcome": c["outcome"],
        "total_steps": c["total_steps"],
        "lost_mw": c["lost_mw"],
        # the cascade's own people count (from the unrounded MW), so it equals /api/grid/cascade's
        **({k: c[k] for k in ("people", "people_per_mw", "population")} if "people" in c else people_fields(g, c["lost_mw"])),
        **res,
    }
