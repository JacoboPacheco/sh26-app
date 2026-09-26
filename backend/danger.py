"""Danger zones (owned by the danger track): at the size on the slider, where would a campus set off
the biggest blackouts? The inverse of dropping one campus and watching: every town at once.

GET /api/grid/danger?mw=1500&load_factor=1.0&region=FL&firm=false&limit=25

Candidates: one site per area (the town a substation is named after, powerflow.area_of), at that
area's best-connected substation (highest kV, then most load), snapped the way a click there snaps
(grid.check_site → Grid.site_bus), so clicking a zone runs exactly the case it was ranked on.

Prefilter: a site whose headroom is at least `mw` (with a small margin) overloads nothing, so nothing
trips and no one loses power; it is counted calm without a cascade. The rest are ordered by how
likely they are to cascade widely and run the real cascade (Grid.cascade_case — the same case
POST /api/grid/cascade solves for {lat, lon, mw, load_factor, firm} at that site), at most
MAX_CANDIDATES of them within a time budget (BUDGET_S; `partial` says when it ran out). The order
(measured on FL, GA, CA, TX, NY at 500 and 1,500 MW against every area's cascade: of the true top
10, 95 of 100 are in the first 150; Florida's top 10 at 1,500 MW by people hit are all in the first 60): the best of
three ranks — most headroom short of `mw` (big hubs that barely overload), most load in the area,
and least overload at the first solve (skipped past OVER_PRIOR_MAX sites: it costs a sensitivity
solve each). A blackout big enough to matter starts where one or two trunk lines are pushed just
past their rating. A cascade costs ~25-60 ms on a laptop (Florida: ~4-8 s cold).

Zones are ranked by `people_hit`, the app's counter (the engine's estimate of everyone whose power ran
through a failed line or went out, each once); `people_zone`, `homes_zone` and `people` (lost MW x
people per MW) ride along. Zones where no one is hit are left out.

At a load level where the model already cascades without any campus (a heat wave), the campus isn't
what darkens the grid: the answer says so (`already_failing`, `baseline_people_hit`) and lists no zones.

Results are cached per (region, size rounded to 50 MW, load level, firm) in a small LRU; one
computation runs at a time (CPU-bound: two at once only slow each other). A request that runs out of
its budget answers with what it has (`partial`, the likeliest sites first) and a background thread
finishes the rest a slice at a time, refreshing the cached answer (the panel refetches it). Florida
at 1,500 MW and at 500 MW (the demo's hero size and the slider's default) are started at startup and
finished by the same thread (DANGER_WARM=0 turns it off).

Public like the other grid routes (the grid is the same for everyone), rate-limited per visitor.
All numbers are from the synthetic model: the note says so on every answer.
"""

import logging
import math
import os
import threading
import time
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request

from grid import DEFAULT_REGION, LOAD_FACTOR_MAX, LOAD_FACTOR_MIN, REGIONS, grid_at, region_code
from limiter import limiter
from powerflow import OVER_PCT, Grid, area_of

router = APIRouter(tags=["danger"])
log = logging.getLogger("uvicorn.error")

MW_LO, MW_HI = 50, 5000
MW_STEP = 50  # sizes are computed (and cached) at the nearest 50 MW — the size slider's step
LIMIT_MAX = 50
MAX_CANDIDATES = 150  # cascades per answer at most (a big state has 800 towns)
OVER_PRIOR_MAX = 300  # rank by the first solve's overload only up to this many sites (NY: 6 s for 810)
HEADROOM_MARGIN = 1.02  # a site this far inside its headroom is calm without a cascade
BUDGET_S = float(os.getenv("DANGER_BUDGET_S", "8"))  # a request answers after this at most; the rest finishes in the background
CACHE_SIZE = 32
CHUNK = 128  # sensitivity columns per solve (memory: n x CHUNK floats)
NOTE = (
    "Synthetic grid model — where a campus of this size would cause the largest blackouts in the model, "
    "not a prediction about real places"
)

_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()
_compute_lock = threading.Lock()  # one danger computation at a time
_candidates: dict[str, list[tuple[int, int, str]]] = {}  # region -> [(sub index, bus index, area)]


# ---------------------------------------------------------------------------------- candidates
def candidates(g: Grid, code: str) -> list[tuple[int, int, str]]:
    """One (sub index, connect bus, area) per area: its highest-kV substation, then the most load,
    then the lowest id — moved to the substation a click at its (rounded) position snaps to, so the
    zone and a drop there are the same case. The same at every load level (same network, same names)."""
    hit = _candidates.get(code)
    if hit is not None:
        return hit
    load = np.zeros(len(g.sub_ids))
    np.add.at(load, g.bus_sub_idx, g.pd / max(g.load_factor, 1e-9))
    kv = np.zeros(len(g.sub_ids))
    np.maximum.at(kv, g.bus_sub_idx, g.bus_kv)
    best: dict[str, int] = {}
    for i in range(len(g.sub_ids)):
        a = area_of(g.sub_name[i])
        if not a:
            continue
        j = best.get(a)
        if j is None or (kv[i], load[i], -int(g.sub_ids[i])) > (kv[j], load[j], -int(g.sub_ids[j])):
            best[a] = i
    out, seen = [], set()
    for a, i in sorted(best.items()):
        s = g.nearest_sub(round(float(g.sub_lat[i]), 4), round(float(g.sub_lon[i]), 4))
        bus = g.connect_bus(s)
        if bus in seen:
            continue
        seen.add(bus)
        out.append((s, bus, a))
    _candidates[code] = out
    return out


def _first_overload_mw(g: Grid, buses: np.ndarray, mw: float) -> np.ndarray:
    """Per candidate: MW over rating, summed over the lines that weren't over already, at the first
    solve with `mw` at that bus — linear, with the what-if's dispatch (Grid._marginal_weights)."""
    w1, w2, room = g._marginal_weights()
    fresh = (np.abs(g.base.flow) < g.rate) & g.base.active
    out = np.zeros(len(buses))
    for s in range(0, len(buses), CHUNK):
        b = buses[s : s + CHUNK]
        r = np.minimum(room[b], mw)
        flow = g.base.flow[:, None] + r[None, :] * g._sensitivity(b, w1)
        more = np.flatnonzero(mw > room[b])
        if len(more):
            flow[:, more] += (mw - room[b][more])[None, :] * g._sensitivity(b[more], w2)
        over = np.maximum(np.abs(flow) - g.rate[:, None], 0.0)
        out[s : s + CHUNK] = (over * fresh[:, None]).sum(axis=0)
    return out


def _rank(v: np.ndarray) -> np.ndarray:
    r = np.empty(len(v))
    r[np.argsort(v, kind="stable")] = np.arange(len(v))
    return r


def _order(g: Grid, cands: list[tuple[int, int, str]], headroom: np.ndarray, mw: float) -> list[int]:
    """Candidate positions most likely to cascade widely first (see the module docstring)."""
    if not cands:
        return []
    buses = np.array([c[1] for c in cands])
    load = np.zeros(len(g.sub_ids))
    np.add.at(load, g.bus_sub_idx, g.pd)
    area_load: dict[str, float] = {}
    for i in range(len(g.sub_ids)):
        a = area_of(g.sub_name[i])
        area_load[a] = area_load.get(a, 0.0) + float(load[i])
    by_load = _rank(-np.array([area_load.get(c[2], 0.0) for c in cands]))
    by_head = _rank(-headroom)
    # the first solve's overload is the best of the three, but costs a sensitivity solve per site
    by_over = _rank(_first_overload_mw(g, buses, mw)) if len(cands) <= OVER_PRIOR_MAX else np.full(len(cands), np.inf)
    score = np.minimum(np.minimum(by_head, by_load), by_over) + 1e-3 * (by_head + by_load)
    return [int(i) for i in np.argsort(score, kind="stable")]


def _headroom(g: Grid, buses: np.ndarray) -> np.ndarray:
    """MW each bus takes before the first new overload: the cached vector when the heatmap has it,
    else only these buses (a big state's full heatmap takes ~20 s)."""
    if g._headroom_bus is not None:
        return np.minimum(g._headroom_bus[buses], 1e6)
    out = np.empty(len(buses))
    for s in range(0, len(buses), CHUNK):
        out[s : s + CHUNK] = g.headroom_for_buses(buses[s : s + CHUNK])
    return np.minimum(np.where(np.isfinite(out), out, 1e6), 1e6)


# ---------------------------------------------------------------------------------- the answer
def _hit(c: dict) -> int:
    """A cascade's people hit (the app's counter; an estimate), or its people on an engine without it."""
    return int(c.get("people_hit", c["people"]) or 0)


class _Job:
    """One answer (region, size, load level, firm), computed in slices: a request runs it until its
    time budget, the finisher thread runs the rest in the background, and each slice refreshes the
    cached answer — so a slow machine answers with the likeliest sites first, then completes."""

    def __init__(self, code: str, mw: float, load_factor: float, firm: bool):
        t0 = time.perf_counter()
        self.code, self.mw, self.firm = code, mw, firm
        self.g = g = grid_at(load_factor, code)
        self.zones: list[dict] = []
        self.checked = 0
        self.pos = 0
        self.baseline = None
        if (g.base.active & (g.base.loading_pct > OVER_PCT + 1e-6)).any():
            b = g.cascade_case(np.zeros(g.n))
            if _hit(b) > 0:
                self.baseline = b  # the model cascades with no campus at all: the campus isn't what darkens it
        if self.baseline is not None:
            self.cands, self.headroom, self.order, self.calm = [], np.zeros(0), [], 0
        else:
            self.cands = candidates(g, code)
            buses = np.array([c[1] for c in self.cands])
            self.headroom = _headroom(g, buses)
            risky = [i for i in range(len(self.cands)) if self.headroom[i] < mw * HEADROOM_MARGIN]
            order = [risky[j] for j in _order(g, [self.cands[i] for i in risky], self.headroom[risky], mw)]
            self.order = order[:MAX_CANDIDATES]
            self.calm = len(self.cands) - len(risky)  # take this size with no line over its limit: nothing trips
        self.ms = (time.perf_counter() - t0) * 1000

    @property
    def done(self) -> bool:
        return self.pos >= len(self.order)

    def run(self, until: float | None) -> None:
        """Cascade the next sites until `until` (a time.perf_counter() value) or the end; None: to the end."""
        t0 = time.perf_counter()
        g, mw = self.g, self.mw
        while not self.done and (until is None or time.perf_counter() < until):
            i = self.order[self.pos]
            self.pos += 1
            s, bus, area = self.cands[i]
            c = g.cascade_case(g.extra_load([(bus, mw)]), None, None, firm_buses=[bus] if self.firm else None)
            self.checked += 1
            if _hit(c) <= 0:
                continue
            self.zones.append(
                {
                    "id": int(g.sub_ids[s]),
                    "area": area,
                    "name": g.sub_name[s],
                    "lat": round(float(g.sub_lat[s]), 4),
                    "lon": round(float(g.sub_lon[s]), 4),
                    "kv": float(g.bus_kv[bus]),
                    # the cascade's counts (estimates): people_hit is the app's counter (everyone whose power ran
                    # through a failed line or went out, each once); people_zone everyone in the areas that lost
                    # power; people the load actually lost x people per MW
                    "people_hit": _hit(c),
                    "people_zone": c.get("people_zone"),
                    "homes_zone": c.get("homes_zone"),
                    "people": int(c["people"]),
                    "lost_mw": c["lost_mw"],
                    "steps": int(c["total_steps"]),
                    "dark_subs": sum(len(st["dark_subs"]) for st in c["steps"]),
                    "outcome": c["outcome"],
                    "headroom_mw": round(float(self.headroom[i]), 1),
                }
            )
        self.ms += (time.perf_counter() - t0) * 1000

    def answer(self) -> dict:
        """Every zone found so far, most people hit first (the route trims to `limit`)."""
        g, b = self.g, self.baseline
        return {
            "region": self.code,
            "region_name": REGIONS[self.code]["name"],
            "mw": self.mw,
            "load_factor": g.load_factor,
            "firm": self.firm,
            "note": NOTE,
            "people_per_mw": round(g.people_per_mw, 2),
            "population": g.population,
            "already_failing": b is not None,  # a heat wave: the model cascades with no campus
            "baseline_people_hit": _hit(b) if b is not None else 0,
            "baseline_people": int(b["people"]) if b is not None else 0,
            "zones": sorted(self.zones, key=lambda z: (-z["people_hit"], -z["people"], z["area"])),
            "candidates": len(self.cands),  # one site per town
            "checked": self.checked,  # cascades run so far
            "likely": len(self.order),  # sites that overload something (the likeliest MAX_CANDIDATES of them)
            "calm": self.calm,  # towns that take this size with no line over its limit: nothing trips
            "partial": not self.done,  # still checking (the finisher completes it in the background)
            "computed_ms": round(self.ms),
        }


_jobs: "OrderedDict[tuple, _Job]" = OrderedDict()  # unfinished answers (their partial answer is cached)
MAX_JOBS = 4
SLICE_S = 1.5  # the finisher holds the compute lock this long at a time: a new request waits at most that
_finisher: threading.Thread | None = None


def _store(key: tuple, job: _Job) -> dict:
    ans = job.answer()
    with _cache_lock:
        _cache[key] = ans
        _cache.move_to_end(key)
        while len(_cache) > CACHE_SIZE:
            old, _ = _cache.popitem(last=False)
            _jobs.pop(old, None)
        if job.done:
            _jobs.pop(key, None)
        else:
            _jobs[key] = job
            _jobs.move_to_end(key)
            while len(_jobs) > MAX_JOBS:
                _jobs.popitem(last=False)  # the oldest stays partial
    return ans


def _finish():
    """Background: complete the unfinished answers, newest first, a slice at a time."""
    global _finisher
    while True:
        with _compute_lock:
            with _cache_lock:
                if not _jobs:
                    _finisher = None
                    return
                key, job = next(reversed(_jobs.items()))
            try:
                job.run(time.perf_counter() + SLICE_S)
                _store(key, job)
                if job.done:
                    log.info("danger zones: %s at %.0f MW (load %.2f, firm %s) done, %d sites in %.1f s", job.code, job.mw, key[2], job.firm, job.checked, job.ms / 1000)
            except Exception:  # noqa: BLE001 — leave it partial rather than loop on an error
                log.exception("danger zones: finishing %s failed", key)
                with _cache_lock:
                    _jobs.pop(key, None)
        time.sleep(0.05)  # let a waiting request take the lock


def _start_finisher():
    global _finisher
    with _cache_lock:
        if _finisher is not None or not _jobs:
            return
        _finisher = threading.Thread(target=_finish, name="danger-finisher", daemon=True)
        t = _finisher
    t.start()


def danger(code: str, mw: float, load_factor: float, firm: bool, budget_s: float | None = BUDGET_S) -> tuple[dict, bool]:
    """(the answer, whether it came from the cache). A cold answer computes for up to `budget_s`
    (None: to the end); what's left finishes in the background and refreshes the cached answer."""
    key = (code, mw, round(load_factor, 2), firm)

    def cached():
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None and (budget_s is not None or not hit["partial"]):
                _cache.move_to_end(key)
                return hit
        return None

    hit = cached()
    if hit is not None:
        return hit, True
    t0 = time.perf_counter()
    with _compute_lock:
        hit = cached()  # computed while this request waited for the lock
        if hit is not None:
            return hit, True
        with _cache_lock:
            job = _jobs.get(key)
        if job is None:
            job = _Job(code, mw, load_factor, firm)
        job.run(None if budget_s is None else t0 + budget_s)
        ans = _store(key, job)
    _start_finisher()
    return ans, False


@router.get("/api/grid/danger")
@limiter.limit("30/minute")
def get_danger(
    request: Request,
    mw: float = Query(1500.0),
    load_factor: float = Query(1.0),
    region: str = Query(DEFAULT_REGION),
    firm: bool = Query(False),
    limit: int = Query(25),
):
    if not math.isfinite(mw) or not (MW_LO <= mw <= MW_HI):
        raise HTTPException(status_code=422, detail=f"Size must be between {MW_LO} and {MW_HI:,} MW")
    if not math.isfinite(load_factor) or not (LOAD_FACTOR_MIN <= load_factor <= LOAD_FACTOR_MAX):
        raise HTTPException(status_code=422, detail=f"Load level must be between {LOAD_FACTOR_MIN} and {LOAD_FACTOR_MAX}")
    if not (1 <= limit <= LIMIT_MAX):
        raise HTTPException(status_code=422, detail=f"Ask for between 1 and {LIMIT_MAX} zones")
    if (region or "").strip().upper() == "US":
        raise HTTPException(status_code=422, detail="Open a state first — danger zones are computed on one state's model")
    code = region_code(region)
    size = float(min(MW_HI, max(MW_LO, math.floor(mw / MW_STEP + 0.5) * MW_STEP)))
    result, cached = danger(code, size, round(load_factor, 2), firm)
    return {**result, "zones": result["zones"][:limit], "zone_count": len(result["zones"]), "cached": cached}


# ---------------------------------------------------------------------------------- warm-up
def _warm():
    """Start Florida at the slider's default (500 MW) and the demo's hero size (1,500 MW, newest, so the
    finisher completes it first) — a slice each, the rest in the background between requests."""
    for size in (500.0, 1500.0):
        try:
            danger(DEFAULT_REGION, size, 1.0, False, budget_s=SLICE_S)
        except Exception:  # noqa: BLE001 — warming is best effort; a request computes it anyway
            log.exception("danger zones: warm-up failed")


if os.getenv("DANGER_WARM", "1") != "0":
    _warmer = threading.Timer(0.2, _warm)
    _warmer.daemon = True  # never holds the process open at shutdown
    _warmer.start()
