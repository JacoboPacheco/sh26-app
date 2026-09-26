"""Capacity: how many campuses of one size the grid carries AT ONCE — every one connected together at the study's
load level, with no line or transformer over its rating — and the cheapest upgrades that let one more in, campus
by campus. The Strengthen page's meter (CLAUDE.md -> Decisions -> STRENGTHEN IS THE HEART).

It runs inside an unlock study (unlock.Study: its model, candidate sites, cost figures and project records), after
the site-by-site plan, so it shares that job, its cache, peek and the Florida warm-up.

PLACE. Campuses go in one at a time. Before each, one sensitivity solve gives every candidate site's room (MW until
the first line or transformer reaches its rating, with the dispatch the engine uses for the next MW); the sites
closest to fitting are tried with an exact power-flow solve that has every campus so far. The first that fits (no
line past its rating, no load the plants can't supply) takes the campus. The count before the first upgrade is
"today".
UPGRADE. When none fits, each of the TRY sites closest to fitting is solved with the campus there; the lines it
pushes past their rating are raised to a standard level (50 MVA steps, carrying the flow at <= ~91 % of the new
rating, within 5x the original — unlock's rules) and priced with the cost track's figures (high end). The cheapest
bundle is built and the campus goes in. It stops at MAX_CAMPUSES, the COST_CAP, its time share, or when the next
campus needs more power than the model's plants can make ("plants") or a line raised past 5x ("no_fix").
FLEXIBLE. The same search with flexible campuses: each runs at full size whenever the state's load is at or below
FLEX_OFF of this level and cuts back to FLEX_SHARE of its size at it; a placement must fit at both levels. (Duke's
"Rethinking Load Growth" (2025) is the published case for flexible data centers; these numbers are the engine's.)
PLANTS. The model's spare generation at this level (the plants' maximum minus what the state needs from them), what
the state exports on its ties, and what is left after a planning reserve (RESERVE_PCT of the state's load; Florida's
reference margin level is 15 %, NERC LTRA). Past that, more campuses need new generation or flexibility, not wires.
VERIFY. The last set of each search (every campus at once, with its upgrades) runs through the full cascade engine
at each level: nothing may trip and no one may lose power.

Every number describes the SYNTHETIC grid model (Breakthrough Energy / Texas A&M), not any real utility's network;
costs are labeled estimates with their sources.
"""

import math
import time
from collections import Counter

import numpy as np

import costs
from grid import grid_at

FLEX_SHARE = 0.5  # a flexible campus runs at half its size at the afternoon peak ...
FLEX_OFF = 0.9  # ... and at full size whenever the state's load is at or below 90 % of that peak
RESERVE_PCT = 15.0  # planning reserve kept on the state's own load (FRCC's reference margin level, NERC LTRA)
TRY = 30  # sites tried for each campus, closest to fitting first
MAX_CAMPUSES = 40
COST_CAP = 6e9  # high end: the plan stops before this much is spent
TIME_S = {"FL": 30.0}  # per search, seconds at most (other states: TIME_S_OTHER)
TIME_S_OTHER = 30.0
_EPS = 1e-9


def _weights(g, st, extra: np.ndarray) -> np.ndarray:
    """How the engine dispatches the next MW at this state, per bus (Grid._balance: while an island needs less than its
    plants make in the dataset the next MW comes pro rata to that output; past it, from their headroom)."""
    w = np.zeros(g.n)
    for c in np.unique(st.comp):
        idx = np.flatnonzero(st.comp == c)
        gmax = g.pmax[idx]
        if gmax.sum() <= _EPS:
            w[idx] = 1.0 / len(idx)
            continue
        pg = g.pg[idx]
        base = float(pg.sum())
        need = float((g.pd[idx] + extra[idx]).sum() - g.tie[idx].sum())
        if 0 <= need < base and base > _EPS:
            w[idx] = pg / base
        else:
            head = gmax - pg
            w[idx] = head / head.sum() if head.sum() > _EPS else gmax / gmax.sum()
    return w


def _limits(flow: np.ndarray, dF: np.ndarray, rate: np.ndarray, fresh: np.ndarray) -> np.ndarray:
    """Per column: MW until each line reaches its rating (inf when it never does); lines already over with no campus
    don't count (not a new overload). Shape (m, columns)."""
    r = rate[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        tp = (r - flow[:, None]) / dF
        tn = (-r - flow[:, None]) / dF
    t = np.where(dF > _EPS, tp, np.where(dF < -_EPS, tn, np.inf))
    t = np.where(fresh[:, None], t, np.inf)
    return np.maximum(t, 0.0)


def _check(g, extra: np.ndarray, rate: np.ndarray, fresh: np.ndarray):
    st = g.solve(g.base.active, extra, rate)
    over = np.flatnonzero((np.abs(st.flow) > rate + 1e-6) & fresh)
    return st, over


def _plants(g) -> dict:
    """The model's generation at this level: spare output, exports, and what is left after the planning reserve."""
    pmax = float(g.pmax.sum())
    load = float(g.pd.sum())
    tie = float(g.tie.sum())  # negative: the state exports to its neighbors in the model
    need = load - tie
    spare = max(pmax - need, 0.0)
    exports = max(-tie, 0.0)
    # the reserve covers the new load too: the plants (less what the state exports) must stay RESERVE_PCT above the
    # state's whole load, old and new, so the room is (pmax - exports) / (1 + r) - load (Florida: 4.27 GW, not 4.91)
    room = max((pmax - exports) / (1.0 + RESERVE_PCT / 100.0) - load, 0.0)
    reserve = max(spare - room, 0.0)
    return {
        "max_mw": round(pmax),
        "load_mw": round(load),
        "export_mw": round(exports),
        "spare_mw": round(spare),  # before the state stops exporting
        "reserve_pct": RESERVE_PCT,
        "reserve_mw": round(reserve),  # what the reserve holds back of the spare output, on the old and new load together
        "room_mw": round(room),  # new load the plants carry at this level with the reserve kept
        "sentence": (
            f"The model's power plants can make {spare / 1000:,.1f} GW more at this load level before the state stops exporting; "
            f"keeping a {RESERVE_PCT:.0f} % planning reserve on the whole load, new campuses included, leaves {room / 1000:,.1f} GW for new load."
        ),
    }


def _search(study, levels: list[tuple[object, float]], by: str) -> dict:
    """One search. `levels`: [(grid at a load level, share of the campus it draws there)]; the first is the study's own
    level (where rooms are ranked); a placement must fit at every level. Returns its steps and why it stopped."""
    import unlock  # the study's module: naming and the re-rating rules (imported here: unlock imports this module)

    mw = study.mw
    g0 = levels[0][0]
    buses = np.array([c[1] for c in study.cands], dtype=int)
    ns = len(buses)
    rate = g0.rate.copy()
    cap = np.floor(g0.rate * unlock.MAX_RERATE * 10) / 10
    fresh = [(np.abs(g.base.flow) < g.rate) & g.base.active for g, _ in levels]
    extras = [np.zeros(g.n) for g, _ in levels]
    used = np.zeros(ns, dtype=bool)
    raised: set[int] = set()
    steps: list[dict] = []
    cum_lo = cum_hi = 0.0
    stop = "max"
    t_end = time.perf_counter() + TIME_S.get(study.code, TIME_S_OTHER)
    first_block = None
    while len(steps) < MAX_CAMPUSES:
        if time.perf_counter() > t_end:
            stop = "time"
            break
        g, share = levels[0]
        st = g.solve(g.base.active, extras[0], rate)
        dF = g._sensitivity(buses, _weights(g, st, extras[0]))
        t = _limits(st.flow, dF, rate, fresh[0])
        room = t.min(axis=0) / share  # in campus MW
        room[used] = -1.0
        order = [int(j) for j in np.argsort(-room, kind="stable") if room[j] >= 0][:TRY]
        best = None
        short = no_fix = 0
        for j in order:
            ok, lost, lvl, new = True, False, {}, []
            for k, (gk, ak) in enumerate(levels):
                e = extras[k].copy()
                e[buses[j]] += ak * mw
                sk, over = _check(gk, e, rate, fresh[k])
                study.solves += 1
                if sk.lost_mw > 0.5:
                    lost = True
                    break
                for i in over:
                    need = abs(float(sk.flow[i]))
                    if need > cap[i] + 1e-6:
                        ok = False
                        break
                    L = min(math.ceil(need * unlock.MARGIN / unlock.STEP_MVA) * unlock.STEP_MVA, float(cap[i]))
                    lvl[int(i)] = max(lvl.get(int(i), 0.0), L)
                if not ok:
                    break
                new.append(e)
            if lost:
                short += 1
                continue
            if not ok:
                no_fix += 1
                continue
            lo = sum(study.cost(i, L)[0] - study.cost(i, float(rate[i]))[0] for i, L in lvl.items())
            hi = sum(study.cost(i, L)[1] - study.cost(i, float(rate[i]))[1] for i, L in lvl.items())
            if best is None or hi < best[0]:
                best = (hi, lo, j, lvl, new)
            if hi <= 0:
                break
        if best is None:
            stop = "plants" if short >= no_fix else "no_fix"
            break
        hi, lo, j, lvl, new = best
        if cum_hi + hi > COST_CAP:
            stop = "cap"
            break
        block = None
        if lvl:
            # what stopped this campus: the line that reaches its rating first at most of the sites tried
            firsts = Counter(int(np.argmin(t[:, jj])) for jj in order if np.isfinite(t[:, jj]).any())
            i0, n0 = firsts.most_common(1)[0] if firsts else (next(iter(lvl)), 1)
            block = {**unlock._branch(g0, i0), "blocks": int(n0), "of": len(order)}
            if first_block is None:
                first_block = {**block, "at_campus": len(steps) + 1}
        projects = [study.project(i, float(rate[i]), L, len(steps) + 1, by) for i, L in sorted(lvl.items(), key=lambda kv: -kv[1])]
        for i, L in lvl.items():
            rate[i] = L
            raised.add(i)
        for k in range(len(levels)):
            extras[k] = new[k]
        used[j] = True
        cum_lo += lo
        cum_hi += hi
        s0, _ = _check(g0, extras[0], rate, fresh[0])
        c = study.cands[j]
        steps.append(
            {
                "n": len(steps) + 1,  # campuses connected at once after this step
                "site": {"id": int(g0.sub_ids[c[0]]), "area": c[2], "lat": round(float(g0.sub_lat[c[0]]), 4), "lon": round(float(g0.sub_lon[c[0]]), 4)},
                "free": not lvl,
                "cost": {"low": round(lo), "high": round(hi)},
                "cum_cost": {"low": round(cum_lo), "high": round(cum_hi)},
                "projects": projects,
                "upgrades_total": len(raised),
                "blocked_by": block,  # what stopped this campus before its upgrades (None when it fit as is)
                "busiest_pct": round(float(np.max(np.where(s0.active, s0.loading_pct, 0.0))), 1),
            }
        )
    today = 0
    for s in steps:
        if not s["free"]:
            break
        today += 1
    # the whole last set, through the cascade engine at every level: nothing may trip, no one may lose power
    verified = None
    if steps:
        ups = {int(g0.br_ids[i]): float(rate[i]) for i in raised}
        calm = True
        for k, (gk, _) in enumerate(levels):
            c = gk.cascade_case(extras[k], None, ups)
            study.verify_cascades += 1
            calm = calm and c["total_steps"] == 0 and unlock._hit(c) == 0 and c["lost_mw"] <= 0.5
        verified = {"campuses": len(steps), "calm": bool(calm)}
    return {
        "today": today,
        "steps": steps,
        "stop": stop,
        "first_block": first_block,
        "verified": verified,
    }


def study(s) -> dict:
    """The capacity section of an unlock study `s` (after learn/plan/verify)."""
    t0 = time.perf_counter()
    g = s.g
    firm = _search(s, [(g, 1.0)], "engine")
    lf_off = round(g.load_factor * FLEX_OFF, 2)
    flexible = _search(s, [(g, FLEX_SHARE), (grid_at(lf_off, s.code), 1.0)], "engine")
    plants = _plants(g)
    per = s.mw  # campus MW at the peak: firm draws all of it, flexible FLEX_SHARE of it
    plants["campuses_firm"] = int(plants["room_mw"] // per) if per > 0 else 0
    plants["campuses_flexible"] = int(plants["room_mw"] // (per * FLEX_SHARE)) if per > 0 else 0
    return {
        "mw": s.mw,
        "load_factor": g.load_factor,
        "unit": f"campuses of {s.mw:,.0f} MW, all connected at once at this load level, with no line or transformer over its rating",
        "firm": firm,
        "flexible": {**flexible, "share": FLEX_SHARE, "off_peak": FLEX_OFF, "off_peak_load_factor": lf_off},
        "plants": plants,
        "try_sites": TRY,
        "sites": len(s.cands),
        "seconds": round(time.perf_counter() - t0, 1),
        "method": (
            "Campuses go in one at a time, each where it fits with every campus so far (an exact power-flow solve). When none fits, "
            f"the {TRY} sites closest to fitting are each solved with the campus there, the lines it would push past their rating are "
            "raised to a standard level and priced, and the cheapest bundle is built. The final set runs through the full cascade: "
            "nothing trips. Flexible campuses must fit twice: at full size at 90 % of this load, and at half size at it."
        ),
        "sources": [costs.SOURCES[k] for k in ("nerc_ltra", "duke_flex", "bv_wecc")],
        "synthetic": True,
    }
