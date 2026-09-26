"""Areas: "is my area at risk?" (owned by the areas track).

An *area* is the town a synthetic substation is named after ("NAPLES 12" -> "Naples",
powerflow.area_of), one list per region. The model is synthetic (Breakthrough Energy / Texas A&M,
CC-BY 4.0): an area is a group of synthetic substations named after a real town, not that town's
real network.

GET /api/areas?region=FL
    Every area: [{slug, name, subs, sub_ids, people, load_mw, lat, lon}], most people first.
    people = the area's model load x the state's people per MW (grid.people_per_mw), an estimate.
GET /api/areas/{region}/exposure?firm=&load_factor=
    The region's stress battery and, per area, how many of its tests leave that area without power.
GET /api/areas/{region}/{slug}?firm=&load_factor=
    One area: its substations, the room for a data center at its best substation (the linear
    headroom, then checked with a real what-if at that size), what a 1 GW campus in the area would
    do (`beyond`, when 1 GW doesn't fit; with the same test at 2 GW, which shows the cliff: a
    flexible campus's own connection trips first, so twice the size often darkens the same people),
    and its exposure.

The stress battery is fixed per region: every catalog campus in the state
(backend/demo/datacenters_us.json, tested at its reported size, capped at the model's 5,000 MW; at
most the 16 largest), a hypothetical 1 GW campus in each of the region's 10 largest areas (at the
area's highest-voltage substation), and a heat wave alone (every load x 1.04, no data center). The
load level is one of the heat clock's presets. Each campus runs the same
cascade as POST /api/grid/cascade with the same case (region, lat, lon, mw, firm, load level), so a
test opened in the workspace replays exactly. Results are cached per (region, firm, load level);
a region's battery takes well under a second here (about 20 cascades).

An area "loses power" in a test when, where the cascade ends, its substations have lost at least
max(0.5 MW, 1 % of the area's load) of existing load; `briefly` marks an area that lost power
during the cascade and had it back at the end.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request

from grid import DEMO, MW_MAX, POPULATION_SOURCE, REGIONS, check_site, grid_at, region_code
from limiter import limiter
from powerflow import OVER_PCT, Grid, area_of

router = APIRouter(tags=["areas"])

CATALOG_PATH = DEMO / "datacenters_us.json"
HEAT_WAVE = 1.04  # the heat clock's heat wave (frontend/src/features/heat/presets.js)
LEVELS = (0.62, 0.82, 1.0, 1.04)  # the heat clock's presets: the only load levels a battery runs at
HYPO_MW = 1000.0  # the hypothetical campuses' size
HYPO_AREAS = 10  # a hypothetical campus in each of the region's largest areas
MAX_CATALOG_TESTS = 16  # the largest reported campuses in the state
HIT_MIN_MW = 0.5
HIT_MIN_SHARE = 0.01
ROOM_CANDIDATES = 3  # substations (most linear headroom first) checked with a real what-if
MAX_BATTERIES = 24  # cached (region, firm, level) batteries
MAX_SUBSTATIONS = 80  # substations listed in a profile (largest first)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")

NOTE = (
    "Each test places a campus of the stated size at the stated location on a synthetic grid model "
    "(Breakthrough Energy / Texas A&M, CC-BY 4.0) and runs the cascade. It is not a prediction about "
    "the real project or the real utility. People are estimates: lost load x the state's people per MW."
)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s[:80] or "area"


def _floor_mw(mw: float) -> float:
    """Sizes offered to try: down to 10 MW from 10 MW up, else down to 1 MW."""
    return float(math.floor(mw / 10.0) * 10.0) if mw >= 10 else float(math.floor(mw))


def _rnd(v: float, nd: int = 1) -> float:
    return round(float(v), nd)


# ------------------------------------------------------------------ areas of a region
class Areas:
    """A region's areas, from its base model (load level 1.0)."""

    def __init__(self, code: str, g: Grid):
        self.code = code
        load_by_sub = np.zeros(len(g.sub_ids))
        np.add.at(load_by_sub, g.bus_sub_idx, g.pd)
        self.base_load = load_by_sub  # MW per substation at load 1.0
        groups: dict[str, list[int]] = {}
        for i, name in enumerate(g.sub_name):
            groups.setdefault(area_of(name) or f"Substation {g.sub_ids[i]}", []).append(i)
        self.items: list[dict] = []
        taken: set[str] = set()
        for name in sorted(groups):
            slug = base = slugify(name)
            n = 2
            while slug in taken:  # "O'Donnell" and "O Donnell" both slug to o-donnell
                slug = f"{base}-{n}"
                n += 1
            taken.add(slug)
            idx = groups[name]
            load = float(load_by_sub[idx].sum())
            w = np.maximum(load_by_sub[idx], 0.0)
            if w.sum() <= 1e-9:
                w = np.ones(len(idx))
            self.items.append(
                {
                    "slug": slug,
                    "name": name,
                    "idx": idx,
                    "load_mw": load,
                    "lat": float(np.average(g.sub_lat[idx], weights=w)),
                    "lon": float(np.average(g.sub_lon[idx], weights=w)),
                }
            )
        self.items.sort(key=lambda a: (-a["load_mw"], a["name"]))
        self.by_slug = {a["slug"]: a for a in self.items}
        self.slug_of_sub = [""] * len(g.sub_ids)
        for a in self.items:
            for i in a["idx"]:
                self.slug_of_sub[i] = a["slug"]


_areas: dict[str, Areas] = {}
_areas_lock = threading.Lock()


def areas_of(code: str) -> Areas:
    with _areas_lock:
        a = _areas.get(code)
    if a is None:
        a = Areas(code, grid_at(1.0, code))
        with _areas_lock:
            _areas.setdefault(code, a)
    return a


# ------------------------------------------------------------------ the stress battery
def _catalog_entries(code: str) -> list[dict]:
    """Catalog campuses in the state, deduplicated by id and by name. [] when the catalog is
    missing or unreadable (the battery then uses hypothetical campuses only)."""
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        entries = data.get("entries", data) if isinstance(data, dict) else data
    except (OSError, ValueError):
        return []
    out, seen = [], set()
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or str(e.get("state", "")).upper() != code:
            continue
        key_id = str(e.get("id", ""))
        key_name = re.sub(r"\W+", " ", str(e.get("name", ""))).strip().lower()
        if not key_id or key_id in seen or key_name in seen:
            continue
        seen.update({key_id, key_name})
        out.append(e)
    return out


def _source(e: dict) -> dict | None:
    for s in e.get("sources") or []:
        if isinstance(s, dict) and str(s.get("url", "")).startswith(("http://", "https://")):
            return {"title": str(s.get("title") or s["url"])[:200], "url": s["url"]}
    return None


def _click_point(g: Grid, i: int) -> tuple[float, float] | None:
    """Coordinates (5 decimals) that snap back to substation i when dropped there, or None."""
    lat, lon = round(float(g.sub_lat[i]), 5), round(float(g.sub_lon[i]), 5)
    return (lat, lon) if g.nearest_sub(lat, lon) == i else None


def _campus_tests(code: str) -> tuple[list[dict], list[dict], str]:
    """The region's campus tests (catalog first, then hypothetical), the catalog campuses that
    weren't tested ({id, label, why: 'outside' | 'limit' | 'invalid', reason}), and the basis
    ('catalog' | 'mixed' | 'hypothetical')."""
    g = grid_at(1.0, code)
    tests, skipped = [], []
    entries = _catalog_entries(code)
    entries.sort(key=lambda e: -float(e.get("mw") or 0))
    for e in entries:
        try:
            lat, lon, mw = float(e["lat"]), float(e["lon"]), float(e["mw"])
        except (KeyError, TypeError, ValueError):
            skipped.append({"id": str(e.get("id")), "label": str(e.get("name", "")), "why": "invalid", "reason": "No usable location or size"})
            continue
        if not all(math.isfinite(v) for v in (lat, lon, mw)) or mw <= 0:
            skipped.append({"id": str(e.get("id")), "label": str(e.get("name", "")), "why": "invalid", "reason": "No usable location or size"})
            continue
        tested = min(mw, float(MW_MAX))
        try:
            check_site(lat, lon, tested, code)
        except HTTPException as err:
            outside = "outside" in str(err.detail) or "No grid here" in str(err.detail)
            why, reason = ("outside", "Outside the area the synthetic model covers") if outside else ("invalid", str(err.detail))
            skipped.append({"id": str(e["id"]), "label": str(e.get("name", "")), "why": why, "reason": reason})
            continue
        if len(tests) >= MAX_CATALOG_TESTS:
            skipped.append({"id": str(e["id"]), "label": str(e.get("name", "")), "why": "limit", "reason": f"Only the {MAX_CATALOG_TESTS} largest are tested"})
            continue
        tests.append(
            {
                "id": str(e["id"]),
                "kind": "catalog",
                "label": str(e.get("name", "")),
                "company": str(e.get("company") or ""),
                "place": str(e.get("city") or ""),
                "status": str(e.get("status") or ""),
                "reported_mw": mw,
                "mw": tested,
                "capped": tested < mw,
                "lat": lat,
                "lon": lon,
                "source": _source(e),
            }
        )
    n_catalog = len(tests)
    areas = areas_of(code)
    for a in areas.items[:HYPO_AREAS]:
        pt = host_point(g, areas, a)
        if pt is None:
            continue
        tests.append(
            {
                "id": f"hypothetical-{a['slug']}",
                "kind": "hypothetical",
                "label": f"A hypothetical 1 GW campus in {a['name']}",
                "company": "",
                "place": a["name"],
                "status": "",
                "reported_mw": None,
                "mw": HYPO_MW,
                "capped": False,
                "lat": pt[0],
                "lon": pt[1],
                "source": None,
            }
        )
    basis = "catalog" if n_catalog == len(tests) else ("hypothetical" if n_catalog == 0 else "mixed")
    return tests, skipped, basis


def host_point(g: Grid, areas: Areas, a: dict) -> tuple[float, float] | None:
    """Where a hypothetical campus in area `a` connects: its highest-voltage substation, then the
    one with the most load (a point that snaps back to it), or None."""
    order = sorted(a["idx"], key=lambda i: (-float(g.bus_kv[g.connect_bus(i)]), -float(areas.base_load[i]), i))
    return next((p for p in (_click_point(g, i) for i in order) if p), None)


def _run(g: Grid, areas: Areas, extra: np.ndarray, firm_buses: list[int] | None) -> dict:
    """One cascade (the same call as POST /api/grid/cascade) and its loss per area."""
    r = g.cascade_case(extra, firm_buses=firm_buses)
    lost: dict[str, float] = {}
    for sid, mw in r["affected"].items():
        i = g.sub_index.get(int(sid))
        if i is not None:
            slug = areas.slug_of_sub[i]
            lost[slug] = lost.get(slug, 0.0) + float(mw)
    seen: set[str] = set()
    for st in r["steps"]:
        for sid, _ in st["newly_affected"]:
            i = g.sub_index.get(int(sid))
            if i is not None:
                seen.add(areas.slug_of_sub[i])
    return {
        "people": int(r["people"]),
        "lost_mw": float(r["lost_mw"]),
        "outcome": r["outcome"],
        "steps": int(r["total_steps"]),
        "site_cut_off": bool(r["site_cut_off"]),
        "firm_held": r["firm_held"],
        "area_lost": lost,
        "area_seen": seen,
    }


def _hit(areas: Areas, slug: str, lost_mw: float, lf: float) -> bool:
    load = areas.by_slug[slug]["load_mw"] * lf
    return lost_mw >= max(HIT_MIN_MW, HIT_MIN_SHARE * load)


_batteries: "OrderedDict[tuple[str, bool, float], dict]" = OrderedDict()
_heat: dict[str, dict] = {}  # region -> the heat wave alone (the same for every firm/level)
_cache_lock = threading.Lock()
_key_locks: dict[tuple, threading.Lock] = {}


def _key_lock(key: tuple) -> threading.Lock:
    with _cache_lock:
        return _key_locks.setdefault(key, threading.Lock())


def _heat_alone(code: str, areas: Areas) -> dict:
    with _key_lock(("heat", code)):
        h = _heat.get(code)
        if h is None:
            g = grid_at(HEAT_WAVE, code)
            h = _run(g, areas, np.zeros(g.n), None)
            h["load_factor"] = HEAT_WAVE
            with _cache_lock:
                _heat[code] = h
    return h


def battery(code: str, firm: bool, lf: float) -> dict:
    """The region's stress battery at (firm, load level), cached."""
    key = (code, bool(firm), float(lf))
    with _cache_lock:
        b = _batteries.get(key)
        if b is not None:
            _batteries.move_to_end(key)
            return b
    with _key_lock(key):
        with _cache_lock:
            b = _batteries.get(key)
        if b is not None:
            return b
        t0 = time.perf_counter()
        areas = areas_of(code)
        tests, skipped, basis = _campus_tests(code)
        g = grid_at(lf, code)
        runs = {}
        memo: dict[tuple[int, float], dict] = {}
        for t in tests:
            bus = g.site_bus(t["lat"], t["lon"])
            k = (bus, t["mw"])
            if k not in memo:  # two reported projects at one spot and size: one cascade
                memo[k] = _run(g, areas, g.extra_load([(bus, t["mw"])]), [bus] if firm else None)
            s = int(g.bus_sub_idx[bus])
            runs[t["id"]] = {**memo[k], "sub": int(g.sub_ids[s]), "sub_name": g.sub_name[s], "sub_area": area_of(g.sub_name[s]), "area_slug": areas.slug_of_sub[s]}
        heat = _heat_alone(code, areas)
        b = {
            "region": code,
            "firm": bool(firm),
            "load_factor": float(lf),
            "basis": basis,
            "tests": tests,
            "skipped": skipped,
            "runs": runs,
            "heat": heat,
            "seconds": round(time.perf_counter() - t0, 3),
        }
        with _cache_lock:
            _batteries[key] = b
            while len(_batteries) > MAX_BATTERIES:
                _batteries.popitem(last=False)
        return b


_warm = ThreadPoolExecutor(max_workers=1, thread_name_prefix="areas-warm")


def _warm_battery(code: str) -> None:
    """Start the default battery (flexible, summer peak) in the background when a region's areas
    are first listed, so the first area profile is instant."""
    with _cache_lock:
        if (code, False, 1.0) in _batteries:
            return
    _warm.submit(lambda: _safe(battery, code, False, 1.0))


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 — a warm-up failure is retried by the request that needs it
        pass


# ------------------------------------------------------------------ validation
def _level(load_factor: float) -> float:
    if not math.isfinite(load_factor):
        raise HTTPException(status_code=422, detail="Load level must be a number")
    lf = round(float(load_factor), 2)
    if lf not in LEVELS:
        raise HTTPException(status_code=422, detail="Load level must be one of the clock's presets: 0.62, 0.82, 1.0 or 1.04")
    return lf


def _area(code: str, slug: str) -> tuple[Areas, dict]:
    areas = areas_of(code)
    a = areas.by_slug.get(slug) if SLUG_RE.match(slug or "") else None
    if a is None:
        raise HTTPException(status_code=404, detail=f"No area called {slug!r} in the {REGIONS[code]['name']} model")
    return areas, a


# ------------------------------------------------------------------ payloads
def _case_payload(t: dict, run: dict, code: str, firm: bool, lf: float) -> dict:
    """A test as the UI lists it, with the exact case to open in the workspace."""
    return {
        **{k: t[k] for k in ("id", "kind", "label", "company", "place", "status", "reported_mw", "mw", "capped", "source")},
        "case": {"region": code, "lat": t["lat"], "lon": t["lon"], "mw": t["mw"], "firm": firm, "load_factor": lf},
        "sub": run["sub"],
        "sub_name": run["sub_name"],
        "sub_area": run["sub_area"],
        "outcome": run["outcome"],
        "steps": run["steps"],
        "total_people": run["people"],
        "total_lost_mw": _rnd(run["lost_mw"]),
        "site_cut_off": run["site_cut_off"],
        "firm_held": run["firm_held"],
    }


def _heat_payload(h: dict, code: str, firm: bool) -> dict:
    return {
        "id": "heat-wave",
        "kind": "heat",
        "label": "A heat wave alone, no data center",
        "case": {"region": code, "lat": None, "lon": None, "mw": None, "firm": firm, "load_factor": HEAT_WAVE},
        "load_factor": HEAT_WAVE,
        "outcome": h["outcome"],
        "steps": h["steps"],
        "total_people": h["people"],
        "total_lost_mw": _rnd(h["lost_mw"]),
    }


def _in_area(g: Grid, areas: Areas, slug: str, run: dict, lf: float) -> dict:
    lost = run["area_lost"].get(slug, 0.0)
    hit = _hit(areas, slug, lost, lf)
    load = areas.by_slug[slug]["load_mw"] * lf
    return {
        "hit": hit,
        "briefly": (not hit) and slug in run["area_seen"],
        "lost_mw": _rnd(lost),
        "people": g.people(lost) if hit else 0,
        "share": round(min(lost / load, 1.0), 3) if load > 1e-9 and hit else 0.0,
    }


def _room(g: Grid, areas: Areas, a: dict) -> dict | None:
    """The most a campus can add at the area's best substation before any line goes over its
    rating: the linear headroom (the heatmap's number), then checked with a what-if at that size
    and bisected down if the check disagrees. None when nothing fits here."""
    hb = g._headroom_bus
    cands = []
    for i in a["idx"]:
        pt = _click_point(g, i)
        if pt is not None:
            cands.append((i, g.connect_bus(i), pt))
    if not cands:
        return None
    buses = np.array([c[1] for c in cands])
    lin = hb[buses] if hb is not None else g.headroom_for_buses(buses)
    lin = np.where(np.isfinite(lin), np.minimum(lin, 1e6), 1e6)
    fresh = g.base.loading_pct < OVER_PCT
    base_ok = g.base.active

    def fits(bus: int, mw: float) -> bool:
        st = g.solve(base_ok.copy(), g.extra_load([(bus, mw)]))
        return not bool((st.loading_pct[fresh] > OVER_PCT + 1e-6).any()) and st.lost_existing_mw < 0.5

    best = None
    for j in np.argsort(-lin)[:ROOM_CANDIDATES]:
        i, bus, pt = cands[int(j)]
        top = _floor_mw(min(float(lin[j]), float(MW_MAX)))
        size = top
        if size >= 1 and not fits(bus, size):
            lo, hi = 0.0, size
            for _ in range(10):
                mid = (lo + hi) / 2
                if fits(bus, mid):
                    lo = mid
                else:
                    hi = mid
            size = _floor_mw(lo)
        if size >= 1 and (best is None or size > best["mw"]):
            best = {
                "sub": int(g.sub_ids[i]),
                "sub_name": g.sub_name[i],
                "lat": pt[0],
                "lon": pt[1],
                "kv": float(g.bus_kv[bus]),
                "mw": size,  # checked: a campus this size here puts no line over its rating
                "headroom_mw": _rnd(lin[j]),  # the linear estimate (the heatmap's number)
                "at_least": bool(size >= MW_MAX),  # the model's cap: it may take more
            }
    if best is not None:
        best["load_factor"] = g.load_factor
    return best


# ------------------------------------------------------------------ routes
@router.get("/api/areas")
@limiter.limit("120/minute")
def list_areas(request: Request, region: str = Query("FL")):
    """Every area of a region's model, most people first (people are estimates)."""
    code = region_code(region)
    g = grid_at(1.0, code)
    areas = areas_of(code)
    _warm_battery(code)
    return [
        {
            "slug": a["slug"],
            "name": a["name"],
            "subs": len(a["idx"]),
            "sub_ids": [int(g.sub_ids[i]) for i in a["idx"]],
            "people": g.people(a["load_mw"]),
            "load_mw": _rnd(a["load_mw"]),
            "lat": round(a["lat"], 4),
            "lon": round(a["lon"], 4),
        }
        for a in areas.items
    ]


@router.get("/api/areas/{region}/exposure")
@limiter.limit("60/minute")
def region_exposure(request: Request, region: str, firm: bool = Query(False), load_factor: float = Query(1.0)):
    """The region's stress battery, and the areas it leaves without power (most tests first)."""
    code = region_code(region)
    lf = _level(load_factor)
    b = battery(code, firm, lf)
    g = grid_at(lf, code)
    areas = areas_of(code)
    per: dict[str, dict] = {}
    for t in b["tests"]:
        run = b["runs"][t["id"]]
        for slug, lost in run["area_lost"].items():
            if slug and _hit(areas, slug, lost, lf):
                p = per.setdefault(slug, {"hits": 0, "people_max": 0})
                p["hits"] += 1
                p["people_max"] = max(p["people_max"], g.people(lost))
    ranked = sorted(per.items(), key=lambda kv: (-kv[1]["hits"], -kv[1]["people_max"], areas.by_slug[kv[0]]["name"]))
    cases = [_case_payload(t, b["runs"][t["id"]], code, firm, lf) for t in b["tests"]]
    return {
        "region": code,
        "region_name": REGIONS[code]["name"],
        "firm": b["firm"],
        "load_factor": lf,
        "basis": b["basis"],
        "tested": len(b["tests"]),
        "cases": cases,
        "heat_wave": _heat_payload(b["heat"], code, firm),
        "skipped": b["skipped"],
        "areas": [{"slug": s, "name": areas.by_slug[s]["name"], **p} for s, p in ranked],
        "seconds": b["seconds"],
        "synthetic": True,
        "note": NOTE,
    }


@router.get("/api/areas/{region}/{slug}")
@limiter.limit("60/minute")
def area_profile(request: Request, region: str, slug: str, firm: bool = Query(False), load_factor: float = Query(1.0)):
    """One area: who lives on its lights (estimate), room for a data center, and its exposure."""
    code = region_code(region)
    lf = _level(load_factor)
    areas, a = _area(code, slug)
    g = grid_at(lf, code)
    b = battery(code, firm, lf)

    hb = g._headroom_bus
    idx = sorted(a["idx"], key=lambda i: (-areas.base_load[i], i))[:MAX_SUBSTATIONS]
    cb = np.array([g.connect_bus(i) for i in idx])
    lin = hb[cb] if hb is not None else g.headroom_for_buses(cb)
    substations = [
        {
            "id": int(g.sub_ids[i]),
            "name": g.sub_name[i],
            "lat": round(float(g.sub_lat[i]), 4),
            "lon": round(float(g.sub_lon[i]), 4),
            "load_mw": _rnd(areas.base_load[i] * lf),
            "people": g.people(areas.base_load[i]),
            "headroom_mw": _rnd(min(float(h), 1e6)) if math.isfinite(float(h)) else 1e6,
        }
        for i, h in zip(idx, lin)
    ]

    room = _room(g, areas, a)
    beyond = None
    pt = host_point(g, areas, a)
    if pt is not None and (room is None or room["mw"] < HYPO_MW):
        # what the cliff looks like here: a 1 GW campus at the area's highest-voltage substation
        # (the same point as its battery test when the area is one of the largest)
        bus = g.site_bus(pt[0], pt[1])
        run = _run(g, areas, g.extra_load([(bus, HYPO_MW)]), [bus] if firm else None)
        s = int(g.bus_sub_idx[bus])
        # twice the size at the same spot: the same people when the campus's own connection trips
        # first and cuts it off (the cliff: past the room, size stops mattering unless it's firm)
        run2 = _run(g, areas, g.extra_load([(bus, 2 * HYPO_MW)]), [bus] if firm else None)
        beyond = {
            "mw": HYPO_MW,
            "double_mw": 2 * HYPO_MW,
            "double_people": run2["people"],
            "double_site_cut_off": run2["site_cut_off"],
            "steps": run["steps"],
            "double_steps": run2["steps"],  # fewer steps at 2 GW: the campus was cut off sooner
            "sub": int(g.sub_ids[s]),
            "sub_name": g.sub_name[s],
            "case": {"region": code, "lat": pt[0], "lon": pt[1], "mw": HYPO_MW, "firm": bool(firm), "load_factor": lf},
            "outcome": run["outcome"],
            "total_people": run["people"],
            "areas_hit": sum(1 for s2, lost in run["area_lost"].items() if s2 and _hit(areas, s2, lost, lf)),
            "site_cut_off": run["site_cut_off"],
            "firm_held": run["firm_held"],
            **_in_area(g, areas, slug, run, lf),
        }

    cases = []
    for t in b["tests"]:
        run = b["runs"][t["id"]]
        cases.append({**_case_payload(t, run, code, firm, lf), **_in_area(g, areas, slug, run, lf), "own_area": run["area_slug"] == slug})
    # the tests that hit this area first (most people), then the rest (largest campus first)
    cases.sort(key=lambda c: (not c["hit"], -c["people"], not c["briefly"], -c["mw"]))
    g_heat = grid_at(HEAT_WAVE, code)
    heat = {**_heat_payload(b["heat"], code, firm), **_in_area(g_heat, areas, slug, b["heat"], HEAT_WAVE)}

    return {
        "region": code,
        "region_name": REGIONS[code]["name"],
        "slug": a["slug"],
        "name": a["name"],
        "synthetic": True,
        "subs": len(a["idx"]),
        "people": g.people(a["load_mw"]),  # who lives on these lights (estimate; base load x people per MW)
        "load_mw": _rnd(a["load_mw"] * lf),
        "lat": round(a["lat"], 4),
        "lon": round(a["lon"], 4),
        "substations": substations,
        "room": room,
        "beyond": beyond,
        "exposure": {
            "firm": bool(firm),
            "load_factor": lf,
            "basis": b["basis"],
            "tested": len(cases),
            "hits": sum(1 for c in cases if c["hit"]),
            "cases": cases,
            "heat_wave": heat,
            "skipped": b["skipped"],
            "seconds": b["seconds"],
        },
        "people_per_mw": round(g.people_per_mw, 2),
        "population": g.population,
        "population_source": POPULATION_SOURCE,
        "note": NOTE,
    }
