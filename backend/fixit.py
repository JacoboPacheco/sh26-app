"""Fix it + best sites (SPEC.md → nice-to-have 4): the answer after the problem.

POST /api/fix takes a case (grid.CaseIn: sites, load level, trips, existing upgrades) and finds a small
set of rating upgrades that clears every overload, greedily: solve, take the most overloaded branch,
raise its rating one standard step (the next multiple of 50 MVA at or above 1.25x its flow, so it
lands at <= 80 %), re-solve, repeat. No cost modeling (out of scope): "smallest" is measured in MVA
added. A rating change never moves flow in a DC power flow, so the greedy converges in one pass over
the overloaded branches; the per-round re-solve keeps it honest if upgrades ever change reactance.

GET /api/best-sites answers the inverse: the substations with the most headroom for a campus of `mw`
at a load level, one per town. Each one is checked with a real what-if at that size, so placing the
campus there shows no line over limit. When nowhere fits, `closest` lists the towns (of the roomiest)
whose fix is smallest in MVA added, so the answer is still "where", plus what it takes.

Public like the grid endpoints (the grid is the same for everyone), with per-visitor rate limits.
"""

import dataclasses
import math
import re
import threading

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request

from grid import DEFAULT_REGION, MAX_UPGRADES, MW_MAX, MW_MIN, CaseIn, _case_header, check_case, grid_at, region_code
from limiter import limiter
from powerflow import OVER_PCT, Grid

router = APIRouter(tags=["fixit"])

STEP_MVA = 50.0  # ratings come in standard 50 MVA steps
STEP_MARGIN = 1.25  # an upgraded branch carries its flow at <= 80 % (out of the "hot" band)
MAX_RERATE = 5.0  # grid.check_case refuses a rating above 5x the original
MAX_ROUNDS = 60
BEST_LIMIT_MAX = 25
VERIFY_FACTOR = 4  # best sites: check at most this many candidates per requested site
CLOSEST_POOL = 3  # when nothing fits: price the fix at this many of the roomiest towns per requested site


def _town(name: str) -> str:
    """ "NORTH FORT MYERS 6" -> "North Fort Myers" (the frontend's townOf)."""
    base = re.sub(r"\s+\d+$", "", str(name)).strip().lower()
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), base)


def _cap(g: Grid, i: int) -> float:
    """The highest rating check_case accepts for branch i, rounded down to 0.1 MVA."""
    return math.floor(float(g.rate[i]) * MAX_RERATE * 10) / 10


def greedy_fix(
    g: Grid,
    active: np.ndarray,
    extra: np.ndarray,
    rate0: np.ndarray,
    room: int = MAX_UPGRADES,
    resolve: bool = True,
    existing: frozenset = frozenset(),
):
    """Raise the most overloaded branch one standard step, re-solve, repeat until nothing is over
    100 % (or MAX_ROUNDS, or `room` branches upgraded that aren't in `existing` — the case's own
    upgrades, which can be raised again for free). Returns (first state, final state, final ratings,
    chosen branch indices in order, rounds, branches that hit the 5x cap, whether a round or upgrade
    limit stopped it with lines still over).

    `resolve=False` re-judges the solved flows against the new ratings instead of re-solving: the
    same answer in a DC power flow (ratings never move flow), for ranking many sites quickly."""
    rate = rate0.copy()
    state = g.solve(active, extra, rate.copy())  # a State keeps its ratings by reference; rate changes below
    first = state
    chosen: list[int] = []
    maxed: set[int] = set()
    rounds = 0
    limited = False
    while True:
        over = [int(i) for i in np.flatnonzero(state.active & (state.loading_pct > OVER_PCT + 1e-6)) if int(i) not in maxed]
        if not over:
            break
        if rounds >= MAX_ROUNDS:
            limited = True
            break
        w = max(over, key=lambda i: state.loading_pct[i])
        need = math.ceil(STEP_MARGIN * abs(float(state.flow[w])) / STEP_MVA) * STEP_MVA
        new = min(need, _cap(g, w))
        if new <= rate[w] + 1e-6:  # already at the cap: re-rating alone can't fix this one
            maxed.add(w)
            continue
        if need > new:
            maxed.add(w)
        if w not in chosen:
            if w not in existing and sum(1 for c in chosen if c not in existing) >= room:
                limited = True
                break
            chosen.append(w)
        rate[w] = new
        rounds += 1
        if resolve:
            state = g.solve(active, extra, rate.copy())
        else:
            state = dataclasses.replace(state, rate=rate.copy(), loading_pct=np.abs(state.flow) / rate * 100.0)
    return first, state, rate, chosen, rounds, maxed, limited


def _branch_names(g: Grid, i: int) -> dict:
    fs = int(g.bus_sub_idx[g.f[i]])
    ts = int(g.bus_sub_idx[g.t[i]])
    return {
        "id": int(g.br_ids[i]),
        "from": int(g.sub_ids[fs]),
        "to": int(g.sub_ids[ts]),
        "from_name": g.sub_name[fs],
        "to_name": g.sub_name[ts],
        "kv": float(g.br_kv[i]),
        "transformer": fs == ts,  # both ends in one substation (the map can't draw it as a line)
    }


@router.post("/api/fix")
@limiter.limit("60/minute")
def fix(request: Request, body: CaseIn):
    g, sites, trip, upgrades = check_case(body)
    extra, header = _case_header(g, sites, trip, upgrades)
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[bid]] = False
    rate0 = g.rates_with(upgrades)
    existing = frozenset(g.br_index[b] for b in upgrades)
    room = MAX_UPGRADES - len(existing)
    first, final, rate, chosen, rounds, maxed, limited = greedy_fix(g, active, extra, rate0, room=room, existing=existing)

    out = []
    for i in chosen:
        out.append(
            {
                **_branch_names(g, i),
                "old_mva": round(float(rate0[i]), 1),
                "new_mva": round(float(rate[i]), 1),
                "added_mva": round(float(rate[i] - rate0[i]), 1),
                "pct_before": round(float(first.loading_pct[i]), 1),
                "pct_after": round(float(final.loading_pct[i]), 1),
                "flow_mw": int(round(abs(float(final.flow[i])))),
                "capped": i in maxed,  # hit the 5x limit: needs a new line, not a re-rating
            }
        )
    remaining = g.overloaded(final)
    apply = {str(k): float(v) for k, v in upgrades.items()}
    apply.update({str(int(g.br_ids[i])): float(rate[i]) for i in chosen})
    return {
        "mw": header["mw"],
        "sub_name": header["sub_name"],
        "load_factor": header["load_factor"],
        "trip": trip,
        "over_before": len(g.overloaded(first)),
        "upgrades": out,
        "added_mva": round(float(sum(rate[i] - rate0[i] for i in chosen)), 1),
        "rounds": rounds,
        "calm": not remaining,
        "remaining_count": len(remaining),
        "remaining": remaining[:20],  # still over limit after the greedy, worst first
        # why lines are left over: the search hit its round/upgrade limit, and/or branches that
        # would need more than 5x their rating (a new line, not a re-rating)
        "stopped_at_limit": limited,
        "max_rounds": MAX_ROUNDS,
        # the case's upgrades plus these reached grid.MAX_UPGRADES (the what-if accepts no more)
        "upgrade_cap_reached": bool(limited and len(set(apply)) >= MAX_UPGRADES),
        "max_upgrades": MAX_UPGRADES,
        "capped_count": sum(1 for i in maxed if final.loading_pct[i] > OVER_PCT + 1e-6),
        "apply": apply,  # the case's upgrades plus these: post it back as `upgrades`
    }


# ---------------------------------------------------------------------------------- best sites
_best_cache: dict[tuple, dict] = {}
_best_lock = threading.Lock()


def _busiest_pct(g: Grid, bus: int, mw: float) -> float:
    """With `mw` at `bus`, the highest loading (%) among the branches the campus loads up (by at least
    one point) that weren't already over in the base case. Over 100 means the site doesn't take it."""
    return _busiest(g, g.solve(np.ones(g.m, dtype=bool), g.extra_load([(bus, mw)])))


def _busiest(g: Grid, st) -> float:
    """_busiest_pct for a state already solved with the campus on the intact grid."""
    fresh = g.base.loading_pct < OVER_PCT
    worst = float(st.loading_pct[fresh].max()) if fresh.any() else 0.0
    if worst > OVER_PCT + 1e-6:
        return worst  # a new overload, however small the rise
    hit = fresh & (st.loading_pct >= g.base.loading_pct + 1.0)
    return float(st.loading_pct[hit].max()) if hit.any() else 0.0


def _towns_by_headroom(g: Grid):
    """(headroom, sub index, town) for the roomiest substation of each town, roomiest first."""
    hb = g.headroom_by_sub()
    order = sorted(
        range(len(g.sub_ids)),
        key=lambda i: (-round(hb[int(g.sub_ids[i])], 1), -float(g.bus_kv[g.connect_bus(i)]), int(g.sub_ids[i])),
    )
    seen = set()
    for i in order:
        town = _town(g.sub_name[i])
        if town in seen:
            continue
        seen.add(town)
        yield float(hb[int(g.sub_ids[i])]), i, town


def _site(g: Grid, i: int, town: str, headroom: float) -> dict:
    return {
        "sub": int(g.sub_ids[i]),
        "name": g.sub_name[i],
        "town": town,
        "lat": round(float(g.sub_lat[i]), 4),
        "lon": round(float(g.sub_lon[i]), 4),
        "headroom_mw": round(min(headroom, 1e6), 1),
        "kv": float(g.bus_kv[g.connect_bus(i)]),
    }


def best_sites(g: Grid, mw: float, limit: int) -> dict:
    hb = g.headroom_by_sub()
    towns = list(_towns_by_headroom(g))
    checked = []
    for h, i, town in towns[: limit * VERIFY_FACTOR]:
        if h < mw:
            break
        pct = _busiest_pct(g, g.connect_bus(i), mw)
        if pct > OVER_PCT + 1e-6:
            continue  # the linear headroom was a little optimistic here; skip rather than mislead
        checked.append((h, i, town, pct))
    # most headroom first; among equals (many towns share one binding line), the coolest grid first
    checked.sort(key=lambda c: (-round(c[0]), c[3]))
    sites = [{**_site(g, i, town, h), "busiest_pct": round(pct, 1)} for h, i, town, pct in checked[:limit]]
    closest = []
    if not sites:
        # Nowhere takes it by the linear headroom: of the roomiest towns, the ones that need the
        # smallest upgrades (MVA added, the same greedy as /api/fix) to carry it, then the most headroom.
        # The greedy's first state is a full solve at this size, and that solve is the authority: a
        # town it shows taking the campus with no new overload is listed as a site that fits (the
        # headroom estimate can undershoot where generators re-dispatch), so the panel never says
        # "nowhere" while "Put it here" then shows a calm grid.
        active = np.ones(g.m, dtype=bool)
        solved_fits = []
        for h, i, town in towns[: limit * CLOSEST_POOL]:
            first, final, rate, chosen, _, _, _ = greedy_fix(g, active, g.extra_load([(g.connect_bus(i), mw)]), g.rate, resolve=False)
            pct = _busiest(g, first)
            if pct <= OVER_PCT + 1e-6:
                solved_fits.append((h, i, town, pct))
                continue
            closest.append(
                {
                    **_site(g, i, town, h),
                    "fix_mva": round(float(sum(rate[j] - g.rate[j] for j in chosen)), 1),
                    "fix_lines": len(chosen),
                    "fix_calm": not g.overloaded(final),
                }
            )
        closest.sort(key=lambda s: (not s["fix_calm"], s["fix_mva"], -s["headroom_mw"]))
        closest = closest[:limit]
        if solved_fits:
            # they take at least `mw` (checked); the coolest grid first
            solved_fits.sort(key=lambda c: c[3])
            sites = [
                {**_site(g, i, town, max(h, mw)), "busiest_pct": round(pct, 1), "headroom_at_least": True}
                for h, i, town, pct in solved_fits[:limit]
            ]
            closest = []
    for n, s in enumerate(sites or closest, start=1):
        s["rank"] = n
    return {
        "mw": mw,
        "load_factor": g.load_factor,
        "count_ok": int(sum(1 for v in hb.values() if v >= mw)),  # substations (not towns) that take it
        "max_headroom_mw": round(max(hb.values()), 1),  # the linear estimate (shown only when nothing fits)
        "sites": sites,
        "closest": closest,
    }


@router.get("/api/best-sites")
@limiter.limit("120/minute")  # cached per (level, size); the fix panel refetches when the size changes
def get_best_sites(
    request: Request,
    mw: float = Query(500.0),
    load_factor: float = Query(1.0),
    limit: int = Query(10),
    region: str = Query(DEFAULT_REGION),
):
    if not math.isfinite(mw) or not (MW_MIN <= mw <= MW_MAX):
        raise HTTPException(status_code=422, detail=f"Size must be between {MW_MIN} and {MW_MAX:,} MW")
    if not (1 <= limit <= BEST_LIMIT_MAX):
        raise HTTPException(status_code=422, detail=f"Ask for between 1 and {BEST_LIMIT_MAX} sites")
    if not math.isfinite(load_factor):
        raise HTTPException(status_code=422, detail="Load level must be a number")
    code = region_code(region)
    g = grid_at(load_factor, code)
    key = (code, round(g.load_factor, 2), round(mw, 1), limit)
    hit = _best_cache.get(key)
    if hit is not None:
        return hit
    result = {**best_sites(g, round(mw, 1), limit), "region": code}
    with _best_lock:
        if len(_best_cache) >= 128:  # a script can't grow memory without bound
            _best_cache.clear()
        _best_cache[key] = result
    return result
