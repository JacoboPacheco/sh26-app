"""Site report: what a developer's siting screen shows, on open synthetic data, plus what nobody else shows.

POST /api/site/report {region, lat, lon, mw, load_factor, firm} answers, for a campus of `mw` at a point:

- the six nearest substations, each with its distance, voltage, the room it has (the heatmap's own number),
  the line that limits it, the most it takes without upgrades ("right-size"), and, when it doesn't fit, the
  smallest set of upgrades (the Fix it search, priced with costs.py's unit costs) verified by re-running the case;
- an N-1 screen at the best substation: the most loaded lines nearby tripped one at a time, one re-solve each;
- "if built anyway": the plain cascade at the substations that don't fit, with the people it would hit.

Everything runs on the SYNTHETIC Breakthrough Energy / Texas A&M grid model with a lossless DC power flow. It is a
screening estimate, not an interconnection study, and it is not any utility's network. Every people and dollar
figure is an estimate. Public like the grid endpoints (no login, nothing stored), computed only when asked.

Nothing is copied from costs.py or fixit.py: the unit costs come from costs._upgrade_items (the Black & Veatch
figures) and the upgrade search is fixit.greedy_fix, so a report and the Fix it panel never disagree.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from costs import SOURCES, _rng, _upgrade_items
from fixit import MAX_ROUNDS, greedy_fix
from grid import (
    DEFAULT_REGION,
    LOAD_FACTOR_MAX,
    LOAD_FACTOR_MIN,
    MAX_UPGRADES,
    MW_MAX,
    MW_MIN,
    REGIONS,
    _km,
    check_site,
    grid_at,
    region_code,
    site_headroom,
)
from limiter import limiter
from powerflow import OVER_PCT, Grid, area_of

router = APIRouter(tags=["site"])

NEAREST = 6  # substations listed
N1_MAX = 12  # lines tripped in the N-1 screen
N1_RADIUS_KM = 150.0  # ...among the most loaded lines within this distance of the site
ANYWAY_MAX = 3  # non-fitting substations that get a cascade
UNBOUNDED_MW = 1e5  # headroom at or past this means no line limits it (the engine caps it at 1e6)
UPGRADE_LINES_SHOWN = 15
CACHE_SIZE = 32

GRID_CREDIT = {
    "name": "Breakthrough Energy Sciences U.S. Test System, from Texas A&M ACTIVSg synthetic grids (CC-BY 4.0): a synthetic model, not any utility's network",
    "url": "https://electricgrids.engr.tamu.edu/",
}
DISCLAIMER = (
    "A screening estimate on a synthetic grid model (Breakthrough Energy / Texas A&M), not an interconnection study and not any "
    "utility's network. Every number is an estimate. Nothing here predicts what any real project or utility would face."
)


# ---------------------------------------------------------------------------------- names and units
def _km_all(g: Grid, lat: float, lon: float) -> np.ndarray:
    """Great-circle km from a point to every substation (the same haversine as grid._km)."""
    p1, p2 = math.radians(lat), np.radians(g.sub_lat)
    a = np.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(np.radians(g.sub_lon - lon) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def _pretty(name: str) -> str:
    """"FORT MYERS 3" -> "Fort Myers 3"."""
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), str(name).strip().lower())


def _label(g: Grid, k: int) -> str:
    fs, ts = int(g.bus_sub_idx[g.f[k]]), int(g.bus_sub_idx[g.t[k]])
    if fs == ts:
        return f"{_pretty(g.sub_name[fs])} transformer"
    return f"{_pretty(g.sub_name[fs])} → {_pretty(g.sub_name[ts])}"


def _mw(x: float) -> str:
    """240 MW, 1,500 MW, 3.5 MW (one decimal only for under 10 MW, and not when it is a whole number)."""
    if x >= 10 or abs(x - round(x)) < 0.05:
        return f"{x:,.0f} MW"
    return f"{x:,.1f} MW"


def _floor1(x: float) -> float:
    return math.floor(max(x, 0.0) * 10.0 + 1e-9) / 10.0


# ---------------------------------------------------------------------------------- one substation
class _Ctx:
    """What every row shares: the grid, the size, the intact-network mask of lines that were within their limits."""

    def __init__(self, g: Grid, mw: float):
        self.g = g
        self.mw = mw
        self.active = np.ones(g.m, dtype=bool)
        self.fresh = g.base.loading_pct < OVER_PCT  # lines within their limit before any campus: only these count as "new" overloads

    def solve(self, bus: int, mw: float, rate: np.ndarray | None = None):
        return self.g.solve(self.active, self.g.extra_load([(bus, mw)]), rate)

    def clean(self, st) -> bool:
        """No line that was within its limit is over it, and nobody loses power (a size the state's supply can't meet
        isn't a fit either: no re-rating fixes that)."""
        return not bool((self.fresh & st.active & (st.loading_pct > OVER_PCT + 1e-6)).any()) and st.lost_mw <= 0.5


def _limiting(ctx: _Ctx, bus: int, h: float, st_mw) -> dict | None:
    """The line that sets the headroom: the first fresh line to reach its rating as the campus grows. Found by solving
    just past the headroom (one solve) and taking the fresh line that is then the most loaded."""
    g = ctx.g
    if h >= UNBOUNDED_MW:
        probe = None
    else:
        probe = ctx.solve(bus, h * 1.005 + 1.0)
    st = probe if probe is not None else st_mw
    cand = np.flatnonzero(ctx.fresh & st.active)
    if not len(cand):
        return None
    k = int(cand[np.argmax(st.loading_pct[cand])])
    if probe is None and st.loading_pct[k] < 1.0:
        return None
    return {
        "id": int(g.br_ids[k]),
        "label": _label(g, k),
        "kv": float(g.br_kv[k]),
        "rating_mva": round(float(g.rate[k]), 1),
        "loading_pct_at_mw": round(float(st_mw.loading_pct[k]), 1),
        "loading_pct_now": round(float(g.base.loading_pct[k]), 1),
        "limits_headroom": probe is not None,  # False: nothing binds at any size (the busiest line is shown for context)
    }


def _right_size(ctx: _Ctx, bus: int, h: float) -> float:
    """The most this substation takes without upgrades, never more than the campus: the headroom rounded down to 0.1 MW,
    then checked with a real solve (backed off a little if the linear headroom was optimistic)."""
    cand = _floor1(min(h, ctx.mw))
    for _ in range(8):
        if cand < 0.1 or ctx.clean(ctx.solve(bus, cand)):
            break
        cand = _floor1(cand * 0.95)
    return cand if cand >= 0.1 else 0.0


def _upgrade(ctx: _Ctx, bus: int) -> dict:
    """The smallest re-rating set (Fix it's greedy) that lets the full campus in, priced with costs.py's unit costs and
    verified by re-running the case with exactly those ratings."""
    g = ctx.g
    extra = g.extra_load([(bus, ctx.mw)])
    first, final, rate, chosen, _rounds, _maxed, limited = greedy_fix(g, ctx.active, extra, g.rate, room=MAX_UPGRADES, resolve=False)
    # a line already over its limit before the campus (a heat-wave level) isn't the campus's bill, unless the campus pushes it further
    needed = [k for k in chosen if ctx.fresh[k] or abs(float(first.flow[k])) > abs(float(g.base.flow[k])) + 1.0]
    needed = sorted(needed)
    apply = {int(g.br_ids[k]): float(rate[k]) for k in needed}
    items = _upgrade_items(g, g.rate, rate, needed, set())
    after = g.solve(ctx.active, extra, g.rates_with(apply))  # the case re-run with the upgrades
    over = int((ctx.fresh & after.active & (after.loading_pct > OVER_PCT + 1e-6)).sum())
    supply_short = after.lost_mw > 0.5
    verified = over == 0 and not supply_short
    if verified:
        reason = None
    elif supply_short and not over:
        reason = "The state's power plants can't supply that much: no line upgrade changes that."
    elif limited:
        reason = f"The fix search stops at {MAX_ROUNDS} upgrades and lines are still over: a campus this size needs far more than re-rating."
    else:
        reason = f"{over} {'line stays' if over == 1 else 'lines stay'} over the limit even at 5x their rating: it would take new lines, not re-rating."
    lines = []
    for it in items[:UPGRADE_LINES_SHOWN]:
        lines.append(
            {
                "id": it["id"],
                "label": f"{_pretty(it['from_name'])} transformer" if it["kind"] == "transformer" else f"{_pretty(it['from_name'])} → {_pretty(it['to_name'])}",
                "kind": it["kind"],
                "kv": it["kv"],
                "old_mva": it["old_mva"],
                "new_mva": it["new_mva"],
                "cost_low_usd": it["low"],
                "cost_high_usd": it["high"],
                "method": it["method"],
            }
        )
    return {
        "count": len(items),
        "mva_added": round(sum(it["added_mva"] for it in items), 1),
        "cost_low_usd": int(sum(it["low"] for it in items)),
        "cost_high_usd": int(sum(it["high"] for it in items)),
        "lines": lines,
        "lines_shown": len(lines),
        "apply": {str(k): round(v, 1) for k, v in apply.items()},  # post back as `upgrades` to /api/grid/whatif
        "verified": bool(verified),
        "still_over": over,
        "reason": reason,
    }


def _row(ctx: _Ctx, i: int, lat: float, lon: float, rank: int) -> dict:
    g = ctx.g
    bus = g.connect_bus(i)
    h = site_headroom(g, bus)  # the heatmap's number (grid.site_headroom → the same vector /api/grid/headroom serves)
    st = ctx.solve(bus, ctx.mw)
    fits = ctx.clean(st)
    row = {
        "rank": rank,
        "id": int(g.sub_ids[i]),
        "name": _pretty(g.sub_name[i]),
        "area": area_of(g.sub_name[i]),
        "lat": round(float(g.sub_lat[i]), 4),
        "lon": round(float(g.sub_lon[i]), 4),
        "distance_km": round(_km(lat, lon, float(g.sub_lat[i]), float(g.sub_lon[i])), 1),
        "kv": float(g.bus_kv[bus]),
        "headroom_mw": round(min(h, 1e6), 1),
        "headroom_unbounded": bool(h >= UNBOUNDED_MW),
        "fits": bool(fits),
        "supply_short": bool(st.lost_mw > 0.5),
        "limiting": _limiting(ctx, bus, h, st),
        "right_size_mw": round(ctx.mw if fits else _right_size(ctx, bus, h), 1),
        "upgrade": None if fits else _upgrade(ctx, bus),
    }
    row["_bus"] = bus
    row["_i"] = i
    return row


# ---------------------------------------------------------------------------------- N-1
def _n_minus_1(ctx: _Ctx, best: dict, lat: float, lon: float) -> dict | None:
    """The most loaded lines within N1_RADIUS_KM of the site, tripped one at a time (one re-solve each, no cascade),
    with the campus at `best`: the full size with its upgrades when that is verified, else the size that fits."""
    g = ctx.g
    up = best["upgrade"]
    if best["fits"]:
        size, rate, how = ctx.mw, g.rate, "with no upgrades"
    elif up and up["verified"]:
        size, rate, how = ctx.mw, g.rates_with({int(k): v for k, v in up["apply"].items()}), "with the upgrades above"
    else:
        size, rate, how = best["right_size_mw"], g.rate, "right-sized to what fits"
    if size < 0.1:
        return None
    bus = best["_bus"]
    extra = g.extra_load([(bus, size)])
    pre = g.solve(ctx.active, extra, rate)
    pre_over = pre.active & (pre.loading_pct > OVER_PCT + 1e-6)
    near_sub = _km_all(g, lat, lon) <= N1_RADIUS_KM
    near_br = near_sub[g.bus_sub_idx[g.f]] | near_sub[g.bus_sub_idx[g.t]]
    cand = np.flatnonzero(near_br & pre.active & (np.abs(pre.flow) > 1.0))
    cand = cand[np.argsort(-pre.loading_pct[cand])][:N1_MAX]
    out = []
    for k in cand:
        act = ctx.active.copy()
        act[k] = False
        st = g.solve(act, extra, rate)
        new_over = act & (st.loading_pct > OVER_PCT + 1e-6) & ~pre_over
        lost = max(st.lost_existing_mw - pre.lost_existing_mw, 0.0)
        # the same loss without the campus: what the model's grid does by itself, so the campus isn't blamed for it
        st0 = g.solve(act, np.zeros(g.n), g.rate)
        over0 = act & (st0.loading_pct > OVER_PCT + 1e-6) & ctx.fresh
        lost0 = max(st0.lost_existing_mw - g.base.lost_existing_mw, 0.0)
        secure = bool(not new_over.any() and lost < 0.5)
        adds = bool((new_over & ~over0).any() or lost > lost0 + 0.5)
        w = int(np.argmax(np.where(act, st.loading_pct, -1.0)))
        out.append(
            {
                "id": int(g.br_ids[k]),
                "label": _label(g, int(k)),
                "kv": float(g.br_kv[k]),
                "pct_before": round(float(pre.loading_pct[k]), 1),
                "overloaded_after": int(new_over.sum()),
                "overloaded_without_campus": int(over0.sum()),
                "worst_pct": round(float(st.loading_pct[w]), 1),
                "worst_label": _label(g, w),
                "lost_mw": round(float(lost), 1),
                "secure": secure,
                "campus_adds": adds,  # with the campus this loss overloads a line (or drops load) that it doesn't without it
            }
        )
    n = len(out)
    secure_n = sum(1 for x in out if x["secure"])
    adds_n = sum(1 for x in out if x["campus_adds"])
    weak_n = sum(1 for x in out if not x["secure"] and not x["campus_adds"])
    where = f"{_mw(size)} at {best['name']}"
    if not n:
        summary = f"No loaded lines within {N1_RADIUS_KM:.0f} km to test."
    elif secure_n == n:
        summary = f"All {n} single-line losses near the site leave every line within its limit with {where} ({how})."
    else:
        bad_n = n - secure_n
        if adds_n and weak_n:
            who = (
                f"The campus is what tips {adds_n} of them over; "
                + ("the other one already overloads a line" if weak_n == 1 else f"the other {weak_n} already overload a line")
                + " in the model with no campus at all."
            )
        elif adds_n:
            who = "The campus is what tips it over." if bad_n == 1 else f"The campus is what tips {'both' if bad_n == 2 else f'all {bad_n}'} over."
        else:
            who = (
                "It is not the campus's doing: it already overloads a line in the model with no campus at all."
                if bad_n == 1
                else "None is the campus's doing: each already overloads a line in the model with no campus at all."
            )
        summary = (
            f"{bad_n} of {n} single-line losses near the site leave a line over its limit or customers without power with {where} ({how}). {who}"
        )
    return {
        "substation": {"id": best["id"], "name": best["name"]},
        "mw": round(size, 1),
        "how": how,
        "checked": n,
        "secure_count": secure_n,
        "insecure_count": n - secure_n,
        "campus_adds_count": adds_n,
        "already_weak_count": weak_n,
        "radius_km": N1_RADIUS_KM,
        "lines": out,
        "summary": summary,
    }


# ---------------------------------------------------------------------------------- if built anyway
def _anyway(ctx: _Ctx, rows: list[dict], firm: bool) -> dict | None:
    """The plain cascade (no upgrades) at up to three substations that don't take the campus."""
    g = ctx.g
    bad = [r for r in rows if not r["fits"]][:ANYWAY_MAX]
    if not bad:
        return None
    sites = []
    for r in bad:
        extra = g.extra_load([(r["_bus"], ctx.mw)])
        c = g.cascade_case(extra, [], None, firm_buses=[r["_bus"]] if firm else None)
        sites.append(
            {
                "substation": {"id": r["id"], "name": r["name"]},
                "steps": int(c["total_steps"]),
                "outcome": c["outcome"],
                "lost_mw": round(float(c["lost_mw"]), 1),
                "people_hit": int(c["people_hit"]),  # everyone the failures reached, each counted once (estimate)
                "people_lost": int(c["people"]),  # people-equivalent of the load lost where the cascade settles (estimate)
                "people_zone": int(c["people_zone"]),  # everyone in the areas that lose power (estimate)
                "site_cut_off": bool(c["site_cut_off"]),
            }
        )
    top = max(sites, key=lambda s: s["people_hit"])
    if top["people_hit"] > 0:
        summary = (
            f"If a {_mw(ctx.mw)} campus is built at {top['substation']['name']} without the fix, the model's cascade runs {top['steps']} "
            f"{'step' if top['steps'] == 1 else 'steps'} and hits about {top['people_hit']:,} people, with about {top['people_lost']:,} still without power where it "
            "settles (estimates)."
        )
    else:
        summary = f"If a {_mw(ctx.mw)} campus is built at {top['substation']['name']} without the fix, the model's lines overload but the cascade leaves no one without power."
    return {"sites": sites, "summary": summary, "estimate": True}


# ---------------------------------------------------------------------------------- the verdict
def _verdict(ctx: _Ctx, rows: list[dict], best: dict) -> tuple[str, str]:
    mw = ctx.mw
    N = _mw(mw)
    if best["fits"]:
        room = "no limit found up to 50,000 MW" if best["headroom_unbounded"] else f"{_mw(best['headroom_mw'])} of room before the first line overloads"
        return (
            "fits",
            f"A {N} campus fits at {best['name']} ({best['distance_km']:g} km away, {best['kv']:g} kV) with no upgrades: {room}.",
        )
    roomiest = max(rows, key=lambda r: r["right_size_mw"])
    up = best["upgrade"]
    right = roomiest["right_size_mw"]
    lead = f"Right-sized to {_mw(right)} it fits at {roomiest['name']} without upgrades; " if right >= 1 else f"There is no room at any of the {len(rows)} nearest substations; "
    if up and up["verified"]:
        what = f"{up['count']} {'line or transformer' if up['count'] == 1 else 'lines and transformers'}, {up['mva_added']:,.0f} MVA added"
        return (
            "fits_after_upgrades",
            f"{lead}the full {N} needs about {_rng(up['cost_low_usd'], up['cost_high_usd'])} of upgrades at {best['name']} ({what}), verified by re-running the model with them.",
        )
    reason = (up or {}).get("reason") or "no set of upgrades in the model clears it"
    return (
        "no_verified_fix",
        f"{lead}the full {N} is not fixed by re-rating lines here: {reason[0].lower() + reason[1:]}",
    )


def _pick_best(rows: list[dict]) -> dict:
    """The recommended substation: a fit with the most room (the nearest among equals); else the verified upgrade that
    costs least; else the roomiest."""
    fit = [r for r in rows if r["fits"]]
    if fit:
        return max(fit, key=lambda r: (round(min(r["headroom_mw"], 1e5)), -r["distance_km"]))
    fixable = [r for r in rows if r["upgrade"] and r["upgrade"]["verified"]]
    if fixable:
        return min(fixable, key=lambda r: (r["upgrade"]["cost_high_usd"], r["distance_km"]))
    return max(rows, key=lambda r: (r["right_size_mw"], -r["distance_km"]))


# ---------------------------------------------------------------------------------- the report
def build_report(region: str, lat: float, lon: float, mw: float, load_factor: float, firm: bool) -> dict:
    g = grid_at(load_factor, region)
    ctx = _Ctx(g, mw)
    order = [int(j) for j in np.argsort(_km_all(g, lat, lon), kind="stable")[:NEAREST]]
    rows = [_row(ctx, i, lat, lon, n) for n, i in enumerate(order, start=1)]
    best = _pick_best(rows)
    n1 = _n_minus_1(ctx, best, lat, lon)
    anyway = _anyway(ctx, rows, firm)
    kind, verdict = _verdict(ctx, rows, best)
    nearest = order[0]
    for r in rows:
        r.pop("_bus", None)
        r.pop("_i", None)
    return {
        "site": {
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "mw": round(mw, 1),
            "region": region,
            "region_name": REGIONS[region]["name"],
            "nearest_town": area_of(g.sub_name[nearest]),
            "load_factor": g.load_factor,
            "firm": bool(firm),
        },
        "substations": rows,
        "best_id": best["id"],
        "n_minus_1": n1,
        "if_built_anyway": anyway,
        "verdict": verdict,
        "verdict_kind": kind,
        "method": (
            f"DC power flow (lossless, active power only) on the synthetic model of {REGIONS[region]['name']}, solved fresh for every number. "
            "Headroom is the load a substation takes before the first line that was within its limit goes over it; the campus connects at the substation's "
            "highest-voltage bus. Screening level: no voltages, no stability, no interconnection queue."
        ),
        "assumptions": [
            f"The {NEAREST} nearest substations by straight-line distance. The size is {_mw(mw)}; the load level is {round(g.load_factor * 100)} % of the model's snapshot.",
            "Upgrades: the Fix it search (raise the most overloaded line in 50 MVA steps until none is over; a re-rating tops out at 5x). Priced by voltage class and length "
            "from Black & Veatch's capital costs for WECC (2014 dollars raised to 2024 by CPI-U): low = reconductoring or a unit alongside, high = a new line or a new unit. "
            "Land, permits and substation work beyond transformers are not included.",
            f"N-1: the {N1_MAX} most loaded lines within {N1_RADIUS_KM:.0f} km, lost one at a time with one re-solve each (no cascade, no re-dispatch beyond the balance).",
            "People: lost megawatts times the state's people per megawatt of model load (Census 2024 population), so a count is an estimate.",
        ],
        "sources": [GRID_CREDIT, SOURCES["bv_wecc"], SOURCES["gridlab_2035"], SOURCES["cpi"], SOURCES["census"]],
        "synthetic": True,
        "estimate": True,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------------- the route
class SiteReportIn(BaseModel):
    region: str = DEFAULT_REGION
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    mw: float = Field(ge=MW_MIN, le=MW_MAX)
    load_factor: float = Field(default=1.0, ge=LOAD_FACTOR_MIN, le=LOAD_FACTOR_MAX)
    firm: bool = False


_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


@router.post("/api/site/report")
@limiter.limit("30/minute")
def site_report(request: Request, body: SiteReportIn):
    """The site report for a campus at a point. Computed only when asked; the last 32 distinct requests are kept."""
    code = region_code(body.region)
    check_site(body.lat, body.lon, body.mw, code)  # a readable 422 when the point is off the model or the size out of range
    key = json.dumps([code, round(body.lat, 4), round(body.lon, 4), round(body.mw, 1), round(body.load_factor, 2), body.firm])
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    result = build_report(code, body.lat, body.lon, round(body.mw, 1), body.load_factor, body.firm)
    with _lock:
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return result
