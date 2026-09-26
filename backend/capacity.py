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
VERIFY. The last set of each search (every campus at once, with its upgrades) runs through the cascade engine at
each level with every line in service (N-0): nothing may trip and no one may lose power. That proves N-0 only.

N-1 (n1(), after the plan is published). The firm plan's HEADLINE set (the campuses the page's default budget connects,
the count the meter sells) and, when the search went further, its full set, each with its upgrades, are screened
against every single outage of a line or transformer of N1_KV and up: line outage distribution factors on the base
network's own LU factor (one sparse solve per 128 outages; never a dense PTDF of the whole network), each monitored
element judged against an emergency rating of EMERGENCY_PCT of its normal rating (a labeled assumption: the dataset has
one rating). A radial outage that cuts off load (or a tie) is skipped (the model would strand it on its own); one that
cuts off only a plant moves that plant's output to the others the way the engine dispatches the next MW, when they have
the room; when they don't (the plant sends more than the others can still add), the outage is a problem of its own,
"short of generation" (the state would have to stop exporting or cut customers), and its flows aren't judged. The
counts are single OUTAGES (not overloads), compared with the model's own N-1 baseline with no campuses: the outages
where the plan adds a violation or worsens one.

HOW SURE (sensitivity(), after the plan: Florida's warm study, or on request elsewhere). The firm search re-run on copies
of the model (the cached grid is never changed: the cascade routes share it) under other assumptions: the next MW pro
rata to the plants' output instead of their spare room; a 90 % planning limit; the ratings the build step estimated
doubled (the optimistic side) and with half their room above today's flow (the pessimistic side). Each case gets the
firm search's own time limit; a case that runs out says so. Each row gives the campuses today, what the shipped plan's
default budget buys, and what the headline's count costs; plus the chokepoints that bind in every case.

Every number describes the SYNTHETIC grid model (Breakthrough Energy / Texas A&M), not any real utility's network;
costs are labeled estimates with their sources.
"""

import copy
import math
import time
from collections import Counter

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

import costs
from grid import grid_at
from powerflow import BASE_MVA

FLEX_SHARE = 0.5  # a flexible campus runs at half its size at the afternoon peak ...
FLEX_OFF = 0.9  # ... and at full size whenever the state's load is at or below 90 % of that peak
RESERVE_PCT = 15.0  # planning reserve kept on the state's own load (FRCC's reference margin level, NERC LTRA)
TRY = 30  # sites tried for each campus, closest to fitting first
MAX_CAMPUSES = 40
COST_CAP = 6e9  # high end: the plan stops before this much is spent
TIME_S = {"FL": 30.0}  # per search, seconds at most (other states: TIME_S_OTHER)
TIME_S_OTHER = 30.0
_EPS = 1e-9
# the single-outage (N-1) screen of the firm plan's final set
EMERGENCY_PCT = 115.0  # emergency rating = 115 % of the normal rating (an assumption: the dataset has one rating)
N1_KV = 100.0  # every line and transformer of 100 kV and up is taken out once
WORSE_PP = 1.0  # a violation the model already has counts for the plan only when it gets worse by more than this
N1_CHUNK = 128  # outages per sparse solve (memory: branches x N1_CHUNK floats)
# how sure is the number: the firm search again under other assumptions
PLAN_LIMIT = 0.9  # a planning limit of 90 % of every rating
EST_ROOM = 0.5  # the pessimistic side of the estimated ratings: half their room above the flow they carry today
DEFAULT_BUDGET = 50e6  # the page's default budget: the largest paid step at or under this (capacity.js DEFAULT_CAP_BUDGET)
CHOKE_PAID = 3  # "binds": raised for one of a case's first CHOKE_PAID paid campuses, or what stopped its first
_bridge_cache: dict[tuple, dict] = {}  # unlock.model_key -> the model's bridges (insertion order: the oldest goes first)


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


def _search(study, levels: list[tuple[object, float]], by: str, frac: np.ndarray | None = None, time_s: float | None = None) -> dict:
    """One search. `levels`: [(grid at a load level, share of the campus it draws there)]; the first is the study's own
    level (where rooms are ranked); a placement must fit at every level. `frac` (per branch, default 1): the share of
    its rating a line may carry (the planning-limit case); a raise is sized so the new rating carries the flow at that
    share. Returns its steps, why it stopped, and `_final` (not published: each step's campus bus, MW at the first level
    and the ratings it raised, so n1() can rebuild the set after any step)."""
    import unlock  # the study's module: naming and the re-rating rules (imported here: unlock imports this module)

    mw = study.mw
    g0 = levels[0][0]
    buses = np.array([c[1] for c in study.cands], dtype=int)
    ns = len(buses)
    frac = np.ones(g0.m) if frac is None else frac
    rate = g0.rate * frac  # the limit each line is held to (its rating, as shipped)
    cap = np.floor(g0.rate * unlock.MAX_RERATE * 10) / 10  # ratings, MVA
    fresh = [(np.abs(g.base.flow) < g.rate * frac) & g.base.active for g, _ in levels]
    extras = [np.zeros(g.n) for g, _ in levels]
    used = np.zeros(ns, dtype=bool)
    raised: set[int] = set()
    steps: list[dict] = []
    trail: list[tuple[int, float, dict[int, float]]] = []  # per step: (campus bus, MW at the first level, {branch: rating})
    cum_lo = cum_hi = 0.0
    stop = "max"
    t_end = time.perf_counter() + (time_s or TIME_S.get(study.code, TIME_S_OTHER))
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
                    need = abs(float(sk.flow[i])) / frac[i]  # the rating that carries this flow at the limit
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
            lo = sum(study.cost(i, L)[0] - study.cost(i, float(rate[i] / frac[i]))[0] for i, L in lvl.items())
            hi = sum(study.cost(i, L)[1] - study.cost(i, float(rate[i] / frac[i]))[1] for i, L in lvl.items())
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
        projects = [study.project(i, float(rate[i] / frac[i]), L, len(steps) + 1, by) for i, L in sorted(lvl.items(), key=lambda kv: -kv[1])]
        for i, L in lvl.items():
            rate[i] = L * frac[i]
            raised.add(i)
        for k in range(len(levels)):
            extras[k] = new[k]
        trail.append((int(buses[j]), float(levels[0][1] * mw), {int(i): float(L) for i, L in lvl.items()}))
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
        ups = {int(g0.br_ids[i]): float(rate[i] / frac[i]) for i in raised}
        calm = True
        for k, (gk, _) in enumerate(levels):
            c = gk.cascade_case(extras[k], None, ups)
            study.verify_cascades += 1
            calm = calm and c["total_steps"] == 0 and unlock._hit(c) == 0 and c["lost_mw"] <= 0.5
        verified = {"campuses": len(steps), "calm": bool(calm), "n0": True}  # every line in service: N-0 only
    return {
        "today": today,
        "steps": steps,
        "stop": stop,
        "first_block": first_block,
        "verified": verified,
        "_final": {"trail": trail},  # not published: every step's set, for n1()
    }


def study(s, sensitivity_pending: bool = False) -> dict:
    """The capacity section of an unlock study `s` (after learn/plan/verify). The N-1 screen and the sensitivity
    cases are filled in after the study is published (unlock._extras): here they are "pending" (n1 always; the
    sensitivity when `sensitivity_pending`, else "not_run" with its estimated time: LAZY, on request)."""
    t0 = time.perf_counter()
    g = s.g
    firm = _search(s, [(g, 1.0)], "engine")
    firm["seconds"] = round(time.perf_counter() - t0, 1)
    money = _default_budget(firm["steps"])
    # what n1() screens: the headline's set (the count the page's default budget connects) and the whole search
    s._cap_final = {**firm.pop("_final"), "headline": headline_count(firm), "budget": money}
    lf_off = round(g.load_factor * FLEX_OFF, 2)
    flexible = _search(s, [(g, FLEX_SHARE), (grid_at(lf_off, s.code), 1.0)], "engine")
    flexible.pop("_final", None)
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
        "ratings": ratings(s, firm),
        "n1": {"status": "pending" if firm["steps"] else "skipped", "emergency_pct": EMERGENCY_PCT, "campuses": s._cap_final["headline"]},
        "sensitivity": {
            "status": ("pending" if sensitivity_pending else "not_run") if firm["steps"] else "skipped",
            "estimate_s": sens_estimate(firm["seconds"], len(planned_cases(g)), s.code),
            "cases": planned_cases(g),  # what it will re-run, for the fold's words before it has
        },
        "method": (
            "Campuses go in one at a time, each where it fits with every campus so far (an exact power-flow solve). When none fits, "
            f"the {TRY} sites closest to fitting are each solved with the campus there, the lines it would push past their rating are "
            "raised to a standard level and priced, and the cheapest bundle is built. With every line in service (N-0), the final set runs "
            "through the cascade engine: nothing trips. That checks N-0 only; a single-outage (N-1) screen of the headline's campuses follows. "
            "Flexible campuses must fit twice: at full size at 90 % of this load, and at half size at it."
        ),
        "sources": [costs.SOURCES[k] for k in ("nerc_ltra", "duke_flex", "bv_wecc")],
        "synthetic": True,
    }


def case_time_s(code: str) -> float:
    """The firm search's own time limit, which each sensitivity case gets too (a shorter one would read, on a slow
    host, as a case that connects fewer campuses)."""
    return TIME_S.get(code, TIME_S_OTHER)


def sens_estimate(firm_seconds: float | None, cases: int = 4, code: str = "") -> int:
    """Seconds the sensitivity cases should take: about one firm search each, each capped at the firm search's limit."""
    cap = case_time_s(code)
    per = cap if firm_seconds is None else float(firm_seconds)
    return int(max(5, math.ceil(max(cases, 1) * min(per * 1.3 + 2.0, cap + 2.0))))


def headline_count(firm: dict) -> int:
    """The campuses the page's default budget connects at once: the meter's headline count (capacity.js)."""
    money = _default_budget(firm["steps"])
    return _within(firm["steps"], money) if money else int(firm["today"])


def ratings(s, firm: dict) -> dict:
    """How many of the model's ratings the build step made up (the dataset gives none), and how many of the firm plan's
    upgrades and first limits sit on one."""
    import unlock

    est, _ = unlock._rate_est(s.g)
    ids = {int(p["branch_id"]) for st in firm["steps"] for p in st["projects"]}
    est_ids = {b for b in ids if est[s.g.br_index[b]]}
    blocks = {int(st["blocked_by"]["branch_id"]) for st in firm["steps"] if st.get("blocked_by")}
    return {
        "estimated": int(est.sum()),
        "branches": int(s.g.m),
        "plan_upgrades": len(ids),
        "plan_upgrades_estimated": len(est_ids),
        "plan_blocks": len(blocks),
        "plan_blocks_estimated": sum(1 for b in blocks if est[s.g.br_index[b]]),
        "note": "The build step gave a rating to every line or transformer the dataset rates 0 (\"unlimited\"): its voltage class's default or 30 % above its base flow, whichever is larger.",
    }


# ---------------------------------------------------------------------------------- N-1: the single-outage screen
def _bridges(g) -> dict:
    """The model's bridges (branches whose outage splits it) and, for each, the buses it cuts off: the network's
    2-edge-connected pieces joined by the bridges make a tree, rooted at the largest piece; a bridge cuts off the
    subtree on its far side. Topology only (every branch in service), cached per model."""
    import unlock

    key = unlock.model_key(g)
    hit = _bridge_cache.get(key)
    if hit is not None:
        return hit
    n, m = g.n, g.m
    adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for k in range(m):
        a, b = int(g.f[k]), int(g.t[k])
        if a != b:
            adj[a].append((b, k))
            adj[b].append((a, k))
    tin = [-1] * n
    low = [0] * n
    clock = 0
    bridge = np.zeros(m, dtype=bool)
    for root in range(n):  # Tarjan, iterative; parallel branches are never bridges (the edge id, not the bus, is skipped)
        if tin[root] != -1:
            continue
        tin[root] = low[root] = clock
        clock += 1
        stack = [(root, -1, iter(adj[root]))]
        while stack:
            v, pe, it = stack[-1]
            for w, k in it:
                if k == pe:
                    continue
                if tin[w] == -1:
                    tin[w] = low[w] = clock
                    clock += 1
                    stack.append((w, k, iter(adj[w])))
                    break
                low[v] = min(low[v], tin[w])
            else:
                stack.pop()
                if stack:
                    u = stack[-1][0]
                    low[u] = min(low[u], low[v])
                    if low[v] > tin[u]:
                        bridge[pe] = True
    keep = ~bridge
    A = sp.coo_matrix((np.ones(int(keep.sum())), (g.f[keep], g.t[keep])), shape=(n, n))
    npc, piece = connected_components(A, directed=False)
    size = np.bincount(piece, minlength=npc)
    tree: list[list[tuple[int, int]]] = [[] for _ in range(npc)]
    for k in np.flatnonzero(bridge):
        a, b = int(piece[g.f[k]]), int(piece[g.t[k]])
        tree[a].append((b, int(k)))
        tree[b].append((a, int(k)))
    ptin = np.full(npc, -1)
    ptout = np.zeros(npc, dtype=int)
    child = np.full(m, -1)
    clock = 0
    for r in np.argsort(-size, kind="stable"):
        r = int(r)
        if ptin[r] != -1:
            continue
        ptin[r] = clock
        clock += 1
        stack = [(r, -1, iter(tree[r]))]
        while stack:
            v, pk, it = stack[-1]
            for w, k in it:
                if k == pk or ptin[w] != -1:
                    continue
                ptin[w] = clock
                clock += 1
                child[k] = w
                stack.append((w, k, iter(tree[w])))
                break
            else:
                ptout[v] = clock - 1
                stack.pop()
    out = {"bridge": bridge, "piece": piece, "ptin": ptin, "ptout": ptout, "child": child}
    _bridge_cache[key] = out
    while len(_bridge_cache) > unlock.MODEL_CACHE:
        _bridge_cache.pop(next(iter(_bridge_cache)))
    return out


def _island(br: dict, k: int) -> np.ndarray:
    """The buses bridge k cuts off (bool per bus)."""
    c = int(br["child"][k])
    t = br["ptin"][br["piece"]]
    return (t >= br["ptin"][c]) & (t <= br["ptout"][c])


def _brief(g, i: int) -> dict:
    import unlock

    b = unlock._branch(g, i)
    return {k: b[k] for k in ("branch_id", "kind", "kv", "label", "short", "where", "mid", "rate_est", "rate_note") if k in b}


def _collect(out: dict, post: np.ndarray, rate: np.ndarray, ks: np.ndarray, mon: np.ndarray) -> None:
    """Every (outage, element) pair past the emergency rating in `post` (branches x outages): {(k, l): % of rating}."""
    pct = np.abs(post) / rate[:, None] * 100.0
    pct[~mon] = 0.0
    li, cj = np.nonzero(pct > EMERGENCY_PCT + 1e-9)
    for l, c in zip(li.tolist(), cj.tolist()):
        out[(int(ks[c]), int(l))] = float(pct[l, c])


def _set_of(g, trail: list, n: int) -> tuple[np.ndarray, np.ndarray]:
    """The set after the first n steps of a search's trail: campus MW per bus, and every rating with those steps' raises."""
    extra = np.zeros(g.n)
    rate = np.array(g.rate, dtype=float)
    for bus, mw, lvl in trail[:n]:
        extra[bus] += mw
        for i, L in lvl.items():
            rate[i] = L  # a line raised again later ends at its last (highest) rating
    return extra, rate


def _short_by(g, st, ex: np.ndarray, isl: np.ndarray, b: int, E: float) -> float | None:
    """A radial outage that cuts off only a plant (the island `isl`, sending E MW into bus b): None when the plants still
    connected can add E; else the MW of customers the engine would cut once it has stopped the state's exports
    (Grid._balance: exports go before customers), 0 when stopping exports covers it."""
    if E <= 0.5:
        return None
    comp = st.comp == st.comp[b]
    rest = comp & ~isl
    room = float(np.clip(g.pmax[rest] - st.gen[rest], 0.0, None).sum())
    if E <= room + 0.5:
        return None
    load = float((g.pd[comp] + ex[comp]).sum())
    tie = float(g.tie[comp].sum())
    stopped = tie < 0 and load - tie > float(g.pmax[comp].sum()) + 1e-6  # this state has already stopped exporting
    exports = 0.0 if stopped else float(-np.minimum(g.tie[rest], 0.0).sum())
    return max(E - room - exports, 0.0)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _n1_words(v: dict) -> str:
    """One set's result, in words: the outages (not the overloads) where it adds or worsens a problem."""
    n = v["new_overloads"]
    out = (
        f"{_plural(n, 'single outage pushes', 'single outages push')} a line or transformer past {EMERGENCY_PCT:.0f} % of its rating "
        f"where the model alone doesn't, or {'pushes' if n == 1 else 'push'} it further"
    )
    if v["short"]:
        out += (
            f"; in {v['short']:,} more, the other plants can't make up the output of a plant the outage cuts off "
            + (f"({v['short_shed']:,} of them would cut customers, up to {v['short_shed_max_mw']:,} MW)" if v["short_shed"] else "(the state would have to stop exporting)")
        )
    return out


def n1(s) -> dict:
    """The single-outage screen of the firm plan (the module doc: N-1): the headline's set and, when the search went
    further, its full set. `s` is the study (its grid and the trail the firm search left in s._cap_final)."""
    t0 = time.perf_counter()
    fin = getattr(s, "_cap_final", None) or {}
    g = s.g
    trail = fin.get("trail") or []
    if not trail:
        return {"status": "skipped", "emergency_pct": EMERGENCY_PCT}
    n_full = len(trail)
    n_head = min(max(int(fin.get("headline") or n_full), 1), n_full)
    sets = [n_head] + ([n_full] if n_full > n_head else [])
    active = g.base.active
    cases = [(g.base, np.array(g.rate, dtype=float), np.zeros(g.n))]  # the model on its own, then each set with its upgrades
    for n in sets:
        ex, R = _set_of(g, trail, n)
        cases.append((g.solve(active, ex, R), R, ex))
        s.solves = getattr(s, "solves", 0) + 1
    viol: list[dict] = [{} for _ in cases]  # per case: {(outage, element): % of rating} past the emergency rating
    short: list[dict] = [{} for _ in cases]  # per case: {outage: MW of customers cut} where the other plants fall short
    br = _bridges(g)
    K = np.flatnonzero((g.br_kv >= N1_KV - 1e-6) & active)
    Kn = K[~br["bridge"][K]]  # meshed: line outage distribution factors
    Kr = K[br["bridge"][K]]  # radial: cuts part of the model off
    lu, keep = g.base.lu, g.base.keep  # every branch in service: the same topology (and factor) as every set's state
    for a in range(0, len(Kn), N1_CHUNK):
        ks = Kn[a : a + N1_CHUNK]
        J = np.arange(len(ks))
        D = np.zeros((g.n, len(ks)))
        D[g.f[ks], J] = 1.0
        D[g.t[ks], J] = -1.0
        th = np.zeros((g.n, len(ks)))
        th[keep] = lu.solve(D[keep] / BASE_MVA)
        PT = (th[g.f] - th[g.t]) / g.x[:, None] * BASE_MVA  # MW on every branch per MW moved from f(k) to t(k)
        den = 1.0 - PT[ks, J]
        den = np.where(np.abs(den) < 1e-6, np.inf, den)  # (a bridge is never here; this only guards the division)
        for c, (st, R, _) in enumerate(cases):
            post = st.flow[:, None] + PT * (st.flow[ks] / den)[None, :]
            post[ks, J] = 0.0
            _collect(viol[c], post, R, ks, active)
        del D, th, PT
    # radial outages: skip the ones that strand load or a tie (a campus too: the largest set's); one that cuts off only a
    # plant moves its output to the others when they have the room, else it is short of generation
    strand = 0
    plants: list[tuple[int, np.ndarray, int]] = []  # (outage, island, the bus on the network's side)
    ex_all = cases[-1][2]
    for k in Kr.tolist():
        isl = _island(br, k)
        if float(g.pd[isl].sum() + ex_all[isl].sum() + np.abs(g.tie[isl]).sum()) > 0.5:
            strand += 1
            continue
        b = int(g.t[k]) if isl[g.f[k]] else int(g.f[k])
        if max(abs(float(st.flow[k])) for st, _, _ in cases) > 0.5:
            plants.append((k, isl, b))
    for c, (st, R, ex) in enumerate(cases):
        w = _weights(g, st, ex)
        live = []
        for k, isl, b in plants:
            E = float(st.flow[k]) if g.t[k] == b else -float(st.flow[k])  # the island's export into bus b
            gap = _short_by(g, st, ex, isl, b, E)
            if gap is None:
                live.append((k, isl, b, E))
            else:
                short[c][k] = gap  # its flows aren't judged: the engine would stop exports or cut customers first
        for a in range(0, len(live), N1_CHUNK):
            chunk = live[a : a + N1_CHUNK]
            D = np.zeros((g.n, len(chunk)))
            E = np.array([e for _, _, _, e in chunk])
            for j, (k, isl, b, _) in enumerate(chunk):
                wk = np.where(isl, 0.0, w)
                tot = float(wk.sum())
                D[:, j] = wk / tot if tot > _EPS else 0.0
                D[b, j] -= 1.0
            th = np.zeros((g.n, len(chunk)))
            th[keep] = lu.solve(D[keep] / BASE_MVA)
            dF = (th[g.f] - th[g.t]) / g.x[:, None] * BASE_MVA  # per MW more load at b, picked up by the plants still on
            post = st.flow[:, None] + dF * E[None, :]
            ks = np.array([k for k, _, _, _ in chunk], dtype=int)
            for j, (k, isl, _, _) in enumerate(chunk):
                post[isl[g.f] | isl[g.t], j] = 0.0  # the island's own branches and the bridge carry nothing now
            _collect(viol[c], post, R, ks, active)
    base_v, base_s = viol[0], short[0]
    outs = lambda v: {k for k, _ in v}  # noqa: E731

    def verdict(c: int, n: int) -> dict:
        pv, ps = viol[c], short[c]
        new = {p: v for p, v in pv.items() if p not in base_v or v > base_v[p] + WORSE_PP}
        new_short = {k: v for k, v in ps.items() if k not in base_s}
        shed = [v for v in new_short.values() if v > 0.5]
        worst = max(new.items(), key=lambda kv: kv[1]) if new else None
        return {
            "campuses": n,
            "with_plan_violations": len(outs(pv) | set(ps)),  # outages with any problem, this set connected
            "new": len(outs(new) | set(new_short)),  # outages where the set adds a problem or worsens one (by > WORSE_PP)
            "new_overloads": len(outs(new)),  # of those, the ones that push a line or transformer past the emergency rating
            "short": len(new_short),  # the ones where the other plants can't make up a plant the outage cuts off
            "short_shed": len(shed),  # of those, the ones where the engine would cut customers, not only exports
            "short_shed_max_mw": round(max(shed)) if shed else 0,
            "pairs": {"baseline": len(base_v), "with_plan": len(pv), "new": len(new)},  # (outage, element) overloads
            "worst": {"outage": _brief(g, worst[0][0]), "overloaded": _brief(g, worst[0][1]), "pct": round(worst[1], 1)} if worst else None,
        }

    head = verdict(1, n_head)
    full = verdict(2, n_full) if len(sets) > 1 else None
    screened = int(len(Kn) + (len(Kr) - strand))
    n_b = len(outs(base_v) | set(base_s))
    out = {
        "status": "done",
        "emergency_pct": EMERGENCY_PCT,
        "kv_min": N1_KV,
        "set": "headline",  # the campuses the page's default budget connects: the count the meter sells
        "budget": round(float(fin.get("budget") or 0.0)),
        **head,
        "outages": int(len(K)),  # single outages of 100 kV and up
        "screened": screened,
        "skipped_radial": strand,  # radial outages that strand load (or a tie): skipped
        "plants_cut_off": len(plants),  # radial outages that cut off only a plant
        "baseline_violations": n_b,  # outages that push something past the emergency rating (or fall short) with no campus
        "baseline_short": len(base_s),
        "full": full,  # the same for the search's whole set (None when the headline is the whole set)
        "seconds": round(time.perf_counter() - t0, 2),
        "method": (
            f"Every line and transformer of {N1_KV:.0f} kV and up is taken out once: with the headline's {n_head} campuses and their upgrades connected"
            + (f", with all {n_full} of the search and theirs" if full else "")
            + ", and with no campus (the model's own baseline). Flows after each outage come from line outage distribution factors on the "
            f"network's own factorization (DC power flow; the plants keep their output); a line or transformer is a problem past {EMERGENCY_PCT:.0f} % of its "
            "rating (an assumed emergency rating: the dataset gives one rating). Outages that cut off load are skipped, as the model would strand it on "
            "its own. One that cuts off only a plant moves its output to the other plants when they have the room; when they don't, it is a problem "
            "of its own (short of generation: the state would have to stop exporting or cut customers). The counts are outages, not overloads: one "
            "outage can push several lines past their rating."
        ),
    }
    out["sentence"] = (
        f"Single-outage screen (N-1) of {screened:,} outages, with the headline's {n_head} campuses and their upgrades: {_n1_words(head)} "
        f"(the model alone has {n_b:,} outages that push something past {EMERGENCY_PCT:.0f} %)."
        + (f" With all {n_full} campuses of the search: {_n1_words(full)}." if full else "")
    )
    return out


# ---------------------------------------------------------------------------------- how sure is the number
class _Ctx:
    """What _search needs from a study, on one variant of its grid (a sensitivity case): the size, the candidate sites,
    costs priced from that grid's own ratings, and lean project records."""

    def __init__(self, code: str, mw: float, cands: list, g):
        self.code, self.mw, self.cands, self.g = code, float(mw), cands, g
        self.solves = 0
        self.verify_cascades = 0
        self._cost: dict = {}

    def cost(self, i: int, level: float):
        import unlock

        return unlock.Study.cost(self, i, level)  # the study's own pricing (its cache lives on this object)

    def project(self, i: int, before: float, after: float, step: int | None, by: str) -> dict:
        b = _brief(self.g, i)
        lo, hi, _ = self.cost(i, after)
        lo0, hi0, _ = self.cost(i, before)
        return {**b, "rating_before_mva": round(before, 1), "rating_after_mva": round(after, 1), "cost": {"low": round(lo - lo0), "high": round(hi - hi0)}, "step": step, "by": by}


def _variant(g, **arrays):
    """A shallow copy of a model with some arrays replaced (never changed in place: the cached grid is shared by every
    route) and its base state solved again."""
    v = copy.copy(g)
    for k, a in arrays.items():
        setattr(v, k, a)
    v._marginal = None
    v._headroom_bus = None
    v.base = v.solve(np.ones(v.m, dtype=bool))
    return v


def _default_budget(steps: list[dict]) -> float:
    """capacity.js defaultCapBudget: the largest paid step's running total at or under DEFAULT_BUDGET, else the first."""
    stops = [0.0]
    for st in steps:
        if not st["free"] and st["cum_cost"]["high"] > stops[-1] + 0.5:
            stops.append(float(st["cum_cost"]["high"]))
    if len(stops) < 2:
        return 0.0
    within = [v for v in stops if v <= DEFAULT_BUDGET]
    return within[-1] if len(within) > 1 else stops[1]


def _within(steps: list[dict], money: float) -> int:
    n = 0
    for st in steps:
        if st["cum_cost"]["high"] > money + 0.5:
            break
        n = st["n"]
    return n


def _binds(res: dict, early: bool) -> list[int]:
    """The lines and transformers that bind in one case, in plan order: what stopped a campus, or an upgrade the plan
    makes. `early`: only its first CHOKE_PAID paid campuses; else the whole plan (to where the search ends)."""
    out: dict[int, None] = {}
    paid = [st for st in res["steps"] if not st["free"]]
    for st in paid[:CHOKE_PAID] if early else paid:
        if st.get("blocked_by"):
            out[int(st["blocked_by"]["branch_id"])] = None
        for p in st["projects"]:
            out[int(p["branch_id"])] = None
    return list(out)


def _cases(g, est: np.ndarray) -> list[tuple]:
    """The other assumptions, in order: (id, label, phrase for a sentence, detail, a function that makes the variant grid,
    the share of its rating each line may carry or None). Nothing is built until a case runs."""
    out = []
    pg = float(g.pg.sum())
    if pg > _EPS:
        out.append(
            (
                "pro_rata",
                "Dispatch pro rata to output",
                "the next MW pro rata to the plants' output",
                "The next MW comes from every plant in proportion to what it already makes, not its spare room (the same total capacity).",
                lambda: _variant(g, pmax=g.pg * (float(g.pmax.sum()) / pg)),
                None,
            )
        )
    frac = np.where(np.abs(g.base.flow) > PLAN_LIMIT * g.rate, 1.0, PLAN_LIMIT)  # a line already past 90 % is held to its rating
    out.append(
        (
            "limit_90",
            f"A {PLAN_LIMIT * 100:.0f} % planning limit",
            f"a {PLAN_LIMIT * 100:.0f} % planning limit",
            f"No line or transformer past {PLAN_LIMIT * 100:.0f} % of its rating (upgrades sized to carry the flow at that share).",
            lambda: g,
            frac,
        )
    )
    if est.any():
        n = int(est.sum())
        flow = np.abs(g.base.flow)
        # the pessimistic side: half the room above today's flow (never under it: a line already past its rating with no
        # campus isn't a new overload, so a rating cut below the flow would hide it instead of testing it)
        tight = np.where(est & (g.rate > flow), flow + EST_ROOM * (g.rate - flow), g.rate)
        out.append(
            (
                "est_x2",
                "Estimated ratings doubled",
                "the estimated ratings doubled (the optimistic side)",
                f"The {n:,} ratings the build step made up (the dataset gives none) at twice their value: the optimistic side.",
                lambda: _variant(g, rate=np.where(est, g.rate * 2.0, g.rate)),
                None,
            )
        )
        out.append(
            (
                "est_tight",
                "Estimated ratings tighter",
                "the estimated ratings with half their room (the pessimistic side)",
                f"The same {n:,} ratings with half their room above the flow they carry today: the pessimistic side.",
                lambda: _variant(g, rate=tight),
                None,
            )
        )
    return out


def planned_cases(g) -> list[dict]:
    """The cases sensitivity() will run on this grid, for the fold's words before it has run."""
    import unlock

    est, _ = unlock._rate_est(g)
    return [{"id": c[0], "label": c[1], "phrase": c[2]} for c in _cases(g, est)]


def _row(key: str, label: str, detail: str, res: dict, money: float, n_ref: int, seconds: float) -> dict:
    steps = res["steps"]
    n_b = _within(steps, money)
    ref = steps[n_ref - 1]["cum_cost"]["high"] if 0 < n_ref <= len(steps) else None
    return {
        "id": key,
        "label": label,
        "detail": detail,
        "today": int(res["today"]),
        "campuses": len(steps),
        "stop": res["stop"],  # "time": the case ran out of time, so its counts are lower bounds, not results
        "budget": {"money": round(money), "campuses": n_b, "cost_high": steps[n_b - 1]["cum_cost"]["high"] if n_b else 0},
        "headline": {"campuses": n_ref, "cost_high": ref},  # what the shipped headline's count costs here (None: out of reach)
        "calm": bool((res.get("verified") or {}).get("calm")) if steps else None,
        "seconds": round(seconds, 1),
    }


def sensitivity(code: str, mw: float, load_factor: float, cands: list, firm: dict, lock=None) -> dict:
    """The firm search again under other assumptions, on copies of the model (never the cached grid itself). `firm` is
    the shipped firm search (its row, its default budget and headline count). `lock` (a context manager) is held for
    each case's search, so other studies can run between cases. Each case gets the firm search's own time limit."""
    import contextlib

    import unlock

    t0 = time.perf_counter()
    lock = lock or contextlib.nullcontext()
    g = grid_at(load_factor, code)
    est, _ = unlock._rate_est(g)
    money = _default_budget(firm["steps"])
    n_ref = headline_count(firm)
    time_s = case_time_s(code)
    rows = [_row("shipped", "As shipped", "The engine's dispatch: past what the plants make in the dataset, the next MW comes from their spare room.", firm, money, n_ref, float(firm.get("seconds") or 0))]
    binds = [(_binds(firm, True), _binds(firm, False))]
    for key, label, _phrase, detail, make, fr in _cases(g, est):
        with lock:
            t = time.perf_counter()
            gv = make()
            ctx = _Ctx(code, mw, cands, gv)
            res = _search(ctx, [(gv, 1.0)], "engine", frac=fr, time_s=time_s)
            res.pop("_final", None)
            rows.append(_row(key, label, detail, res, money, n_ref, time.perf_counter() - t))
            binds.append((_binds(res, True), _binds(res, False)))
            del gv, ctx
    ids = [r["id"] for r in rows]
    # early: binds for one of the first CHOKE_PAID paid campuses in every case; anywhere: somewhere in every case's plan
    early = [b for b in binds[0][0] if all(b in x[0] for x in binds[1:])]
    order = list(dict.fromkeys([b for x in binds for b in x[1]]))  # the shipped plan's order first
    count = {b: [ids[k] for k, x in enumerate(binds) if b in x[1]] for b in order}
    anywhere = [b for b in order if len(count[b]) == len(binds)]
    most = [b for b in order if len(count[b]) == len(binds) - 1][:6] if len(binds) > 2 else []
    brief = lambda b: {**_brief(g, g.br_index[b]), "cases": count[b]}  # noqa: E731
    return {
        "status": "done",
        "rows": rows,
        "binds_every_case": [brief(b) for b in anywhere],  # somewhere in every case's plan
        "binds_early_every_case": [brief(b) for b in early],  # early (the first paid campuses) in every case
        "binds_all_but_one": [brief(b) for b in most],
        "binds_rule": "a line or transformer binds in a case when it stops a campus or the plan raises it; early: for one of its first "
        f"{CHOKE_PAID} paid campuses",
        "budget": round(money),
        "headline_campuses": n_ref,
        "estimated_ratings": int(est.sum()),
        "time_s": time_s,  # each case's time limit (the firm search's own)
        "timed_out": [r["id"] for r in rows if r["stop"] == "time"],
        "seconds": round(time.perf_counter() - t0, 1),
        "note": "Each case re-runs the always-on search on a copy of the model; the headline is the shipped case.",
    }
