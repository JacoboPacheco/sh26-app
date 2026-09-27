"""Strengthen the grid (the unlock track): learn a state's structural weak points by simulation, test the
cheapest upgrades that fix them (AI proposes, the engine verifies), and show how many more data centers
they let connect. CLAUDE.md -> Decisions -> UNLOCK.

POST /api/unlock/start      {region, mw, load_factor} -> {id, status, cached}: a background study
GET  /api/unlock/jobs/{id}  -> {status, progress, partial (sites tested, weak points), result | error}
GET  /api/unlock/peek?region=&mw=&load_factor= -> {state: done | queued | running | none, id, estimate_s, sites}:
     what the Strengthen page can show at once (a finished or running study) without starting one
POST /api/unlock/sensitivity {region, mw, load_factor} -> {status, sensitivity}: "How sure is this number?" for a
     finished study (capacity.sensitivity: the always-on search again under other assumptions), in the background

After a study is published, its capacity section gets two more parts without holding it back (like Gemini's challenge):
the single-outage (N-1) screen of the always-on plan's headline campuses and its whole set (capacity.n1, every study,
about a second) and, for the warm
study only (LAZY), the sensitivity cases; elsewhere those run when the page's fold asks. Every line or transformer the
page names carries `rate_est` (and `rate_note`) when the build step made its rating up (the dataset gives none).

Outside Florida nothing runs until someone presses the button (LAZY): the study is a background job with
progress, cached per (region, size rounded to 50 MW, load level). One study computes at a time (CPU-bound).
Florida at 1,000 MW and today's load is warmed a few seconds after startup (the page opens with answers;
UNLOCK_WARM="FL:1000" by default, "0" turns it off) and is never evicted from the cache.

1  LEARN. A campus of `mw` is dropped at every candidate site, one per town (danger.candidates: the
   town's best-connected substation, snapped the way a click there snaps). One linear solve per site
   gives every line's flow with the campus on (the what-if's own dispatch, Grid._marginal_weights, so
   it equals a full solve); a line over its rating is an "entry" (site, line, MW it must carry). Then
   the full cascade (Grid.cascade_case) runs at every site that overloads something, likeliest first
   (danger._order), within a time budget. Per line or transformer we tally: how many sites it blocks
   (it goes over with the campus there), how often it is the first to fail, how often it trips at all,
   and the people hit by the blackouts it starts. Ranked, these are the STRUCTURAL POINTS.
2  PLAN. A greedy search over re-ratings, cheapest cost per unlocked site first. A move is one line
   raised to one standard level (50 MVA steps, carrying its flow at <= 1/MARGIN of the new rating), or
   the bundle one blocked site needs. Each move is priced with the cost track's published figures
   (costs._upgrade_items: Black & Veatch per-mile and per-MVA, CPI-adjusted; the HIGH end ranks) and
   judged by how many more sites then hold `mw` with no line over its limit. In a DC power flow a
   rating never moves flow, so this count is exact for the linear flows; ratings stay within the
   engine's 5x cap (grid.check_case).
3  VERIFY. Every site a step claims is re-run through the full cascade engine with that step's
   cumulative upgrades (it must end with nothing tripped and no one hit); a site that doesn't hold is
   not counted. The sites still blocked are re-run with the whole plan to measure what is left.
4  AI PROPOSES. Gemini (llm.complete_json, fallback + timeout) gets the ranked weak points and the
   engine's plan and proposes other bundles; each is verified the same way and kept only if the engine
   confirms it unlocks sites (tag "gemini"; the engine's own steps are tagged "engine"). With no key
   the engine's plan stands alone. At the same time Gemini tries to beat the capacity plan (capacity_ai.py:
   more campuses at once for the default budget, or the same for less; the engine re-solves, prices and
   judges it), in result["capacity"]["ai"]. The page never waits for that challenge: the study is published
   with its status "pending" and the verdict lands in the cached study when the engine has judged it.

Every proposed upgrade is a project-shaped record (id, name, kind, endpoints and geometry, rating before
and after, cost range, owner = the substation's area name — there is no real utility — and an empty
`window`), so a later feature can compare and assign upgrades across companies with the same overlap
logic as Build plans (gridlock.py).

The headline says it in siting language ("18 upgrades, $211 million (high end): 7 more sites can host 1,000 MW,
7 GW of site options (each site tested alone, not all at once)"): `mw_unlocked` is site options, each site tested
alone, never capacity the grid carries together (the sum can exceed the state's load). Its `strain` is the
study's own measure: line overloads summed over the tested sites, sites that set off a blackout, and the worst
blackout, today and with the whole plan (each step also carries `overloads_left` and `mw_unlocked`).

All numbers come from a SYNTHETIC grid model (Breakthrough Energy / Texas A&M), not any real utility's
network; costs are labeled estimates with their sources.
"""

import asyncio
import copy
import logging
import math
import os
import re
import secrets
import threading
import time
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import capacity
import capacity_ai
import costs
import danger
from grid import DEFAULT_REGION, LOAD_FACTOR_MAX, LOAD_FACTOR_MIN, REGIONS, grid_at, region_code
from limiter import limiter
from llm import complete_json, configured, note_check
from powerflow import OVER_PCT, Grid, area_of

router = APIRouter(tags=["unlock"])
log = logging.getLogger("uvicorn.error")

MW_LO, MW_HI, MW_STEP = 100, 5000, 50
STEP_MVA = 50.0  # ratings come in standard 50 MVA steps (the Fix it search's)
MARGIN = 1.10  # an upgraded line carries the flow that needed it at <= ~91 % of its new rating
MAX_RERATE = 5.0  # grid.check_case refuses a rating above 5x the original
MAX_PLAN_STEPS = 150  # greedy rounds at most (the chart shows the diminishing returns; the panel picks a budget on it)
PEOPLE_UNIT = 500_000  # in the greedy's benefit, a blackout of this many people (estimate) counts as one more site
TOP_POINTS = 20
CHUNK = 128  # sensitivity columns per solve (memory: m x CHUNK floats)
BUDGET_FL, BUDGET_OTHER = 60.0, 120.0  # seconds for a whole study (the task's bound)
LEARN_SHARE = 0.45  # of the budget for the learning cascades
PLAN_SHARE = 0.6  # the greedy stops here
VERIFY_SHARE = 0.75  # the plan's verification stops at this share of the budget
AFTER_SHARE = 0.85  # re-running the still-blocked sites stops here (AI bundles get the rest)
AI_TIMEOUT_S = 10
AI_BUNDLES = 3
AI_ROUNDS = 2  # one proposal, one revision with the engine's findings
AI_MAX_LINES = 16
AI_SITES = 14  # blocked sites (with what each needs) shown to Gemini
AI_POINTS = 12  # weak points shown to Gemini
AI_BAR_STEPS = 12  # the engine plan Gemini is asked to beat: its first steps
AI_VERIFY_MAX = 120  # engine cascades per AI bundle at most
CACHE_SIZE = 16
JOBS_MAX = 32
JOB_TTL_S = 900
RUNNING_MAX = 4
NOTE = (
    "Synthetic grid model (Breakthrough Energy / Texas A&M), not a real utility's network. Each site is a campus of this "
    "size tested alone, not all at once; costs are estimates from published figures."
)
ALONE = "each site tested alone, not all at once"  # the qualifier every site-options figure carries

_compute_lock = threading.Lock()  # one study computes at a time (CPU-bound: two only slow each other)
_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()
_PINNED: set[tuple] = set()  # studies warmed at startup: never evicted
_measured: dict[str, float] = {}  # region -> seconds per candidate site in its last study (for the page's estimate)
_studies: dict[tuple, "Study"] = {}  # warm studies, kept so a Gemini step that fell back can be tried again
_ai_tried: dict[tuple, float] = {}  # key -> time.monotonic() of its last Gemini attempt
AI_RETRY_S = 90.0  # a warm study whose Gemini step fell back (quota, network, timeout) tries it again at most this often
SECONDS_PER_SITE = 0.04  # before a region has run once (Florida: 234 sites in ~9 s with Gemini; Texas: 716 in ~27 s)
WARM_DELAY_S = 8.0  # after the other warm-ups (danger zones, plants) have had the CPU


# ---------------------------------------------------------------------------------- names
def _title(name: str) -> str:
    """ "FORT MYERS 6" -> "Fort Myers 6"."""
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), str(name or "").strip().lower())


def _ends(g: Grid, i: int) -> tuple[int, int]:
    return int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])


_circuits: dict[int, dict[int, int]] = {}  # id(grid JSON) -> {branch index: its number among parallel branches}


def _circuit(g: Grid, i: int) -> int:
    """1, 2, ... among branches between the same two buses (0 when it has no twin)."""
    key = id(g._data)
    table = _circuits.get(key)
    if table is None:
        groups: dict[tuple[int, int], list[int]] = {}
        for k in range(g.m):  # parallel = the same two buses (a transformer's twin at one voltage pair too)
            f, t = int(g.f[k]), int(g.t[k])
            groups.setdefault((min(f, t), max(f, t)), []).append(k)
        table = {k: n for grp in groups.values() if len(grp) > 1 for n, k in enumerate(sorted(grp, key=lambda k: int(g.br_ids[k])), 1)}
        _circuits[key] = table
    return table.get(i, 0)


_rate_notes: "OrderedDict[tuple, tuple[np.ndarray, np.ndarray]]" = OrderedDict()  # model -> (estimated?, why) per branch
RATE_NOTE = {
    1: "estimated (the dataset gives none)",
    2: "raised to carry the dataset's own flow",
}


def _rate_est(g: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Per branch: did the build step make its rating up (build_grid.py flags it `rate_est`), and why: 1 the dataset
    gives none (rating 0, "unlimited": the kV-class default or 30 % above its base flow), 2 the dataset's rating was
    below its own solved flow (raised to that flow + 10 %). Read from the committed grid JSON; cached per model."""
    key = model_key(g)
    hit = _rate_notes.get(key)
    if hit is None:
        est = np.zeros(g.m, dtype=bool)
        why = np.zeros(g.m, dtype=np.int8)
        for k, b in enumerate(g._data["branches"]):
            if not b.get("rate_est"):
                continue
            est[k] = True
            rate, pf = float(b["rate"]), abs(float(b.get("pf_ref", 0.0)))
            raised = pf > 0 and abs(rate - round(pf * 1.1, 1)) < 0.051 and abs(rate - round(pf * 1.3, 1)) >= 0.051
            why[k] = 2 if raised else 1
        hit = _rate_notes[key] = (est, why)
        while len(_rate_notes) > MODEL_CACHE:
            _rate_notes.popitem(last=False)
    return hit


MODEL_CACHE = 16  # per-model tables kept (a state's model can be evicted and loaded again: grid.MAX_LOADED)


def model_key(g: Grid) -> tuple:
    """A key for one state model's topology: its parsed JSON's identity plus its size and first and last branch ids (the
    dataset's branch ids are unique across states), so a table cached for an evicted model never serves another."""
    return (id(g._data), int(g.m), int(g.br_ids[0]) if g.m else 0, int(g.br_ids[-1]) if g.m else 0)


def _rating_fields(g: Grid, i: int) -> dict:
    est, why = _rate_est(g)
    return {"rate_est": True, "rate_note": RATE_NOTE[int(why[i])]} if est[i] else {"rate_est": False}


def _label(g: Grid, i: int) -> str:
    """ "the Fort Myers to Cape Coral line", "the Naples 3 transformer" (", circuit 2" / ", unit 2" for a twin)."""
    fs, ts = _ends(g, i)
    n = _circuit(g, i)
    if fs == ts:
        return f"the {_title(g.sub_name[fs])} transformer" + (f" (unit {n})" if n else "")
    a, z = area_of(g.sub_name[fs]), area_of(g.sub_name[ts])
    if a == z or not a or not z:
        a, z = _title(g.sub_name[fs]), _title(g.sub_name[ts])
    return f"the {a} to {z} line" + (f" (circuit {n})" if n else "")


def _short(g: Grid, i: int) -> str:
    """The plain name a table row carries: "Naples 12 transformer", "Hollywood–Hallandale line",
    "Fort Lauderdale 20–35 line (circuit 2)" (the numbers are the synthetic model's substation names)."""
    fs, ts = _ends(g, i)
    n = _circuit(g, i)
    if fs == ts:
        return f"{_title(g.sub_name[fs])} transformer" + (f" (unit {n})" if n else "")
    a, z = area_of(g.sub_name[fs]), area_of(g.sub_name[ts])
    na, nz = _title(g.sub_name[fs]), _title(g.sub_name[ts])
    if a and z and a != z:
        name = f"{a}–{z}"
    elif a and a == z and na.startswith(a) and nz.startswith(a) and na != a and nz != a:
        name = f"{a} {na[len(a):].strip()}–{nz[len(a):].strip()}"  # both ends in one town: "Fort Lauderdale 20–35"
    else:
        name = f"{na}–{nz}"
    return f"{name} line" + (f" (circuit {n})" if n else "")


def _where(g: Grid, i: int) -> str:
    """The town(s) it sits in: "North Fort Myers", "Tampa · Oldsmar"."""
    fs, ts = _ends(g, i)
    a, z = area_of(g.sub_name[fs]) or _title(g.sub_name[fs]), area_of(g.sub_name[ts]) or _title(g.sub_name[ts])
    return a if a == z else f"{a} · {z}"


def _end(g: Grid, s: int) -> dict:
    return {"sub": int(g.sub_ids[s]), "name": g.sub_name[s], "area": area_of(g.sub_name[s]), "lat": round(float(g.sub_lat[s]), 4), "lon": round(float(g.sub_lon[s]), 4)}


def _branch(g: Grid, i: int) -> dict:
    """Where a line or transformer is: both ends, its midpoint, its kind, its plain name."""
    fs, ts = _ends(g, i)
    a, b = _end(g, fs), _end(g, ts)
    return {
        "branch_id": int(g.br_ids[i]),
        "kind": "transformer" if fs == ts else "line",
        "kv": float(g.br_kv[i]),
        "label": _label(g, i),
        "short": _short(g, i),
        "where": _where(g, i),
        "from": a,
        "to": b,
        "mid": [round((a["lat"] + b["lat"]) / 2, 4), round((a["lon"] + b["lon"]) / 2, 4)],
        **_rating_fields(g, i),  # rate_est: its rating was made up by the build step (the dataset gives none)
    }


def _n(x: float) -> str:
    x = float(x)
    if x >= 1e6:
        return f"{x / 1e6:.1f}M".replace(".0M", "M")
    if x >= 1e4:
        return f"{x / 1e3:.0f}k"
    return f"{x:,.0f}"


def _hit(c: dict) -> int:
    return int(c.get("people_hit", c.get("people", 0)) or 0)


def _money(x: float) -> str:
    """ "$211 million", "$10.2 billion", "$5.6 million" (the frontend's money())."""
    x = float(x)
    for div, unit in ((1e9, "billion"), (1e6, "million")):
        if x >= div:
            v = x / div
            d = 0 if v >= 100 else 1 if v >= 10 else 2
            s = f"{v:.{d}f}".rstrip("0").rstrip(".") if d else f"{v:.0f}"
            return f"${s} {unit}"
    return f"${x:,.0f}"


def _gw(mw: float) -> str:
    """ "7 GW", "1.5 GW", "750 MW"."""
    return f"{mw / 1000:,.1f}".rstrip("0").rstrip(".") + " GW" if mw >= 1000 else f"{mw:,.0f} MW"


# ---------------------------------------------------------------------------------- the study
class Study:
    """One (region, size, load level). `report(phase, done, total, message)` publishes progress; `partial`
    is read by the job route while the study runs (values are only ever replaced, never added)."""

    def __init__(self, code: str, mw: float, load_factor: float, report, partial: dict):
        self.t0 = time.perf_counter()
        self.code, self.mw = code, float(mw)
        self.budget = BUDGET_FL if code == DEFAULT_REGION else BUDGET_OTHER
        self.g = grid_at(load_factor, code)
        self.report = report
        self.partial = partial
        self.cascades = 0
        self.solves = 0
        self.moves = 0
        self.rounds = 0
        self.verify_cascades = 0
        self.baseline = None
        self._cost: dict[tuple[int, float], tuple[float, float, dict | None]] = {}

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    # ------------------------------------------------------------------ 1 learn
    def learn(self) -> None:
        g, mw = self.g, self.mw
        if (g.base.active & (g.base.loading_pct > OVER_PCT + 1e-6)).any():
            b = g.cascade_case(np.zeros(g.n))
            self.cascades += 1
            self.solves += b["total_steps"] + 1
            if _hit(b) > 0:
                self.baseline = b  # a heat wave: the model fails with no campus at all
                return
        self.cands = cands = danger.candidates(g, self.code)
        ns = self.ns = len(cands)
        buses = np.array([c[1] for c in cands], dtype=int)
        self.report("learn", 0, ns, f"Solving the power flow with a {mw:,.0f} MW campus at {ns} sites")
        # every line's flow with the campus at each site (linear, the what-if's dispatch): the lines over their rating
        w1, w2, room = g._marginal_weights()
        fresh = (np.abs(g.base.flow) < g.rate) & g.base.active  # a line already over is not a new overload
        L, S, N = [], [], []
        for s in range(0, ns, CHUNK):
            b = buses[s : s + CHUNK]
            r = np.minimum(room[b], mw)
            flow = g.base.flow[:, None] + r[None, :] * g._sensitivity(b, w1)
            more = np.flatnonzero(mw > room[b])
            if len(more):
                flow[:, more] += (mw - room[b][more])[None, :] * g._sensitivity(b[more], w2)
            over = (np.abs(flow) / g.rate[:, None] * 100.0 > OVER_PCT + 1e-6) & fresh[:, None]
            li, sj = np.nonzero(over)
            L.append(li)
            S.append(sj + s)
            N.append(np.abs(flow[li, sj]))
            self.solves += len(b)
        self.e_line = np.concatenate(L).astype(int) if L else np.zeros(0, dtype=int)
        self.e_site = np.concatenate(S).astype(int) if S else np.zeros(0, dtype=int)
        self.e_need = np.concatenate(N) if N else np.zeros(0)
        self._short_sites(buses, fresh)
        per_site = np.bincount(self.e_site, minlength=ns)
        self.ok0 = (per_site == 0) & ~self.short
        # the map's dots: every site, calm or blocked; the cascade fills `hit` as it goes
        self.site_rows = [
            {
                "id": int(g.sub_ids[c[0]]),
                "area": c[2],
                "lat": round(float(g.sub_lat[c[0]]), 4),
                "lon": round(float(g.sub_lon[c[0]]), 4),
                "ok0": bool(self.ok0[j]),
                "over0": int(per_site[j]),
                "short": bool(self.short[j]),  # the model's generators can't supply this campus there: no line upgrade fixes it
                "hit0": 0 if self.ok0[j] else None,
                "steps0": 0 if self.ok0[j] else None,
                "unlocked_at": None,  # filled by result() (keys exist up front: the job route reads these rows while they fill)
                "hit_after": None,
            }
            for j, c in enumerate(cands)
        ]
        self.partial["sites"] = self.site_rows
        # the full cascade at every blocked site, likeliest to cascade widely first
        risky = [j for j in range(ns) if not self.ok0[j]]
        head = danger._headroom(g, buses[risky]) if risky else np.zeros(0)
        order = [risky[k] for k in danger._order(g, [cands[j] for j in risky], head, mw)] if risky else []
        self.first_fail = np.zeros(g.m, dtype=int)
        self.trips = np.zeros(g.m, dtype=int)
        self.people_first = np.zeros(g.m)  # the biggest blackout a line's failure started (people hit)
        self.people_step = np.zeros(g.m)  # the most people hit in the step a line failed
        self.hit0 = np.full(ns, -1.0)  # -1: not simulated (out of time)
        self.hit0[self.ok0] = 0.0
        self.first_of = np.full(ns, -1, dtype=int)
        stop = self.t0 + self.budget * LEARN_SHARE
        done = 0
        for j in order:
            if time.perf_counter() > stop:
                break
            c = g.cascade_case(g.extra_load([(int(buses[j]), mw)]))
            self.cascades += 1
            self.solves += c["total_steps"] + 1
            hit = _hit(c)
            self.hit0[j] = hit
            prev = 0
            for k, st in enumerate(c["steps"]):
                now = int(st.get("people_hit", st.get("people_zone", 0)) or 0)
                for bid in st["tripped"]:
                    i = g.br_index[int(bid)]
                    self.trips[i] += 1
                    self.people_step[i] = max(self.people_step[i], now - prev)
                    if k == 0:
                        self.first_fail[i] += 1
                        self.first_of[j] = i
                        self.people_first[i] = max(self.people_first[i], hit)
                prev = now
            row = self.site_rows[j]
            row["hit0"], row["steps0"] = hit, int(c["total_steps"])
            done += 1
            if done % 8 == 0 or done == len(order):
                self.report("learn", done, len(order), f"Ran the full cascade at {done} of {len(order)} sites that overload a line")
        self.simulated = done
        self.risky = len(order)
        self.points = self._points()
        self.partial["points"] = self.points

    def _short_sites(self, buses: np.ndarray, fresh: np.ndarray) -> None:
        """Where the campus is more than its island's generators can pick up by re-dispatch, the engine first cuts
        exports, then sheds load: flows the linear pass doesn't model. Those sites get the full solve instead
        (ratings never move flow, so its overloads are exact entries); a site where load is still shed is
        `short`: the model can't supply a campus that big there at all, and no line upgrade fixes that."""
        g, mw = self.g, self.mw
        st = g.base
        k = int(st.comp.max()) + 1
        room = np.bincount(st.comp, weights=g.pmax, minlength=k) - np.bincount(st.comp, weights=g.pd - g.tie, minlength=k)
        self.short = np.zeros(self.ns, dtype=bool)
        odd = np.flatnonzero(mw > room[st.comp[buses]] - 1e-6)
        if not len(odd):
            return
        keep = ~np.isin(self.e_site, odd)
        L, S, N = [self.e_line[keep]], [self.e_site[keep]], [self.e_need[keep]]
        for j in odd:
            s = g.solve(np.ones(g.m, dtype=bool), g.extra_load([(int(buses[j]), mw)]))
            self.solves += 1
            if s.lost_mw > 0.5:
                self.short[j] = True
                continue
            li = np.flatnonzero(fresh & s.active & (s.loading_pct > OVER_PCT + 1e-6))
            L.append(li)
            S.append(np.full(len(li), int(j)))
            N.append(np.abs(s.flow[li]))
        self.e_line = np.concatenate(L).astype(int)
        self.e_site = np.concatenate(S).astype(int)
        self.e_need = np.concatenate(N)

    def _points(self) -> list[dict]:
        """The structural points: lines and transformers ranked by the sites they block, how often they
        fail first, and the people hit by the blackouts they start (each normalized to the largest)."""
        g = self.g
        blocks = np.bincount(self.e_line, minlength=g.m).astype(float)
        need_max = np.zeros(g.m)
        np.maximum.at(need_max, self.e_line, self.e_need)
        parts = [blocks, self.first_fail.astype(float), np.maximum(self.people_first, self.people_step)]
        score = sum(p / p.max() for p in parts if p.max() > 0)
        idx = [int(i) for i in np.argsort(-score, kind="stable") if score[i] > 0][:TOP_POINTS]
        top = float(score[idx[0]]) if idx else 1.0
        out = []
        for rank, i in enumerate(idx, 1):
            b = _branch(g, i)
            why = []
            if self.first_fail[i]:
                why.append(f"first to fail in {self.first_fail[i]} of {self.simulated} simulated campuses")
            elif self.trips[i]:
                why.append(f"trips in {self.trips[i]} of {self.simulated} simulated cascades")
            if blocks[i]:
                why.append(f"goes over its limit with a campus at {int(blocks[i])} of {self.ns} sites")
            ppl = max(self.people_first[i], self.people_step[i])
            if ppl > 0:
                why.append(f"the blackouts it starts reach up to an estimated {_n(ppl)} people")
            reason = "; ".join(why)
            out.append(
                {
                    "rank": rank,
                    **b,
                    "rating_mva": round(float(g.rate[i]), 1),
                    "need_max_mw": round(float(need_max[i]), 1),
                    "sites_blocked": int(blocks[i]),
                    "first_fail": int(self.first_fail[i]),
                    "trips": int(self.trips[i]),
                    "people_max": int(ppl),
                    "importance": round(float(score[i]) / top, 3),
                    "reason": reason[:1].upper() + reason[1:] + ".",
                }
            )
        return out

    # ------------------------------------------------------------------ costs
    def cost(self, i: int, level: float) -> tuple[float, float, dict | None]:
        """(low, high, the costs.py item) to raise branch i from its original rating to `level`."""
        key = (i, round(float(level), 1))
        hit = self._cost.get(key)
        if hit is not None:
            return hit
        g = self.g
        if level <= g.rate[i] + 1e-6:
            out = (0.0, 0.0, None)
        else:
            rate = g.rate.copy()
            rate[i] = level
            items = costs._upgrade_items(g, g.rate, rate, [i], set())
            it = items[0] if items else None
            out = (float(it["low"]), float(it["high"]), it) if it else (0.0, 0.0, None)
        self._cost[key] = out
        return out

    def plan_cost(self, rate: np.ndarray) -> tuple[float, float]:
        up = np.flatnonzero(rate > self.g.rate + 1e-6)
        lo = hi = 0.0
        for i in up:
            a, b, _ = self.cost(int(i), float(rate[i]))
            lo += a
            hi += b
        return lo, hi

    # ------------------------------------------------------------------ 2 plan
    def _setup_plan(self) -> None:
        g = self.g
        self.cap = np.floor(g.rate * MAX_RERATE * 10) / 10
        line, need = self.e_line, self.e_need
        fixable_e = need <= self.cap[line]
        self.site_fixable = (np.bincount(self.e_site[~fixable_e], minlength=self.ns) == 0) & ~self.short
        self.lvl = np.minimum(np.ceil(need * MARGIN / STEP_MVA) * STEP_MVA, self.cap[line])
        order = np.argsort(line, kind="stable")
        self.by_line: dict[int, np.ndarray] = {}
        if len(order):
            ls = line[order]
            cuts = np.flatnonzero(np.diff(ls)) + 1
            for grp in np.split(order, cuts):
                self.by_line[int(line[grp[0]])] = grp

    @staticmethod
    def _over(need: np.ndarray, rate: np.ndarray) -> np.ndarray:
        return need / rate * 100.0 > OVER_PCT + 1e-6

    def holds(self, rate: np.ndarray) -> np.ndarray:
        """Per site: does a campus of `mw` there leave every line within `rate`? (exact for the linear flows)"""
        bad = self._over(self.e_need, rate[self.e_line])
        return (np.bincount(self.e_site[bad], minlength=self.ns) == 0) & ~self.short

    def plan(self) -> None:
        """Greedy: each round takes the move with the lowest cost (high end) per unit of benefit, where the
        benefit is the sites it lets hold plus the blackouts those campuses would have set off (PEOPLE_UNIT
        people count as one more site: preventing the biggest blackouts comes first). A single-line move can
        only finish a site with exactly one line still over (each site has one entry per line), so single-line
        moves are generated from those; every blocked site also offers the bundle it needs."""
        g = self.g
        self._setup_plan()
        rate = g.rate.copy()
        unsat = self._over(self.e_need, rate[self.e_line])
        cnt = np.bincount(self.e_site[unsat], minlength=self.ns)
        self.steps: list[dict] = []
        weight = 1.0 + np.maximum(self.hit0, 0.0) / PEOPLE_UNIT
        order = np.argsort(self.e_site, kind="stable")
        by_site: dict[int, np.ndarray] = {}
        if len(order):
            cuts = np.flatnonzero(np.diff(self.e_site[order])) + 1
            for grp in np.split(order, cuts):
                by_site[int(self.e_site[grp[0]])] = grp
        stop = self.t0 + self.budget * PLAN_SHARE
        pending_total = int(((cnt > 0) & self.site_fixable).sum())
        self.report("plan", 0, pending_total, "Testing the cheapest upgrades")
        while len(self.steps) < MAX_PLAN_STEPS and time.perf_counter() < stop:
            pending = (cnt > 0) & self.site_fixable
            if not pending.any():
                break
            moves: list[dict[int, float]] = []
            last = unsat & (cnt == 1)[self.e_site] & pending[self.e_site]  # the one line left at a site
            for i in np.unique(self.e_line[last]):
                grp = self.by_line[int(i)]
                moves += [{int(i): float(v)} for v in np.unique(self.lvl[grp[last[grp]]]) if v > rate[i] + 1e-6]
            for j in np.flatnonzero(pending & (cnt > 1)):
                e = by_site[int(j)]
                e = e[unsat[e]]
                moves.append({int(self.e_line[x]): float(self.lvl[x]) for x in e})
            best = None
            for ch in moves:
                self.moves += 1
                idx = np.concatenate([self.by_line[i] for i in ch])
                tmp = rate.copy()
                for i, v in ch.items():
                    tmp[i] = max(tmp[i], v)
                fixed = idx[unsat[idx] & ~self._over(self.e_need[idx], tmp[self.e_line[idx]])]
                dec = np.bincount(self.e_site[fixed], minlength=self.ns)
                newly = np.flatnonzero((cnt > 0) & (cnt - dec == 0))
                if not len(newly):
                    continue
                lo = hi = 0.0
                for i, v in ch.items():
                    a1, b1, _ = self.cost(i, max(rate[i], v))
                    a0, b0, _ = self.cost(i, rate[i])
                    lo += a1 - a0
                    hi += b1 - b0
                benefit = float(weight[newly].sum())
                key = (hi / benefit, -benefit, hi)
                if best is None or key < best[0]:
                    best = (key, ch, newly, lo, hi)
            self.rounds += 1
            if best is None:
                break
            _, ch, newly, lo, hi = best
            before = rate.copy()
            for i, v in ch.items():
                rate[i] = max(rate[i], v)
                grp = self.by_line[i]
                unsat[grp] = self._over(self.e_need[grp], rate[i])
            cnt = np.bincount(self.e_site[unsat], minlength=self.ns)
            self.steps.append({"changes": {i: (float(before[i]), float(rate[i])) for i in ch}, "rate": rate.copy(), "newly": [int(j) for j in newly], "lo": lo, "hi": hi})
            left = int(((cnt > 0) & self.site_fixable).sum())
            self.report("plan", pending_total - left, pending_total, f"Step {len(self.steps)}: {int(self.ok0.sum()) + pending_total - left} sites hold the campus")
        self.rate_final = rate

    # ------------------------------------------------------------------ 3 verify
    def _calm(self, j: int, upgrades: dict[int, float]) -> tuple[bool, int]:
        """The engine's full cascade at site j with `upgrades`: (nothing tripped and no one hit, people hit)."""
        g = self.g
        c = g.cascade_case(g.extra_load([(self.cands[j][1], self.mw)]), None, upgrades)
        self.verify_cascades += 1
        self.solves += c["total_steps"] + 1
        # nothing tripped, no one hit, and nothing shed: a campus the generators can't supply sheds load with no line over
        calm = c["total_steps"] == 0 and _hit(c) == 0 and c["lost_mw"] <= 0.5 and c["site_dark_mw"] <= 0.5
        return calm, _hit(c)

    def overloads(self, rate: np.ndarray) -> int:
        """Line overloads summed over every tested site (one campus at a time) with ratings `rate`: the study's own
        measure of strain (each entry is a line over its limit with the campus at one site; exact for the linear flows)."""
        return int(np.count_nonzero(self._over(self.e_need, rate[self.e_line]))) if len(self.e_line) else 0

    def _ups(self, rate: np.ndarray) -> dict[int, float]:
        g = self.g
        return {int(g.br_ids[i]): float(rate[i]) for i in np.flatnonzero(rate > g.rate + 1e-6)}

    def verify(self) -> None:
        stop = self.t0 + self.budget * VERIFY_SHARE
        total = sum(len(s["newly"]) for s in self.steps)
        done = 0
        self.verified_all = True
        for st in self.steps:
            ups = self._ups(st["rate"])
            st["verified"], st["failed"], st["unverified"] = [], [], []
            for j in st["newly"]:
                if time.perf_counter() > stop:
                    st["unverified"].append(j)
                    self.verified_all = False
                    continue
                ok, _ = self._calm(j, ups)
                (st["verified"] if ok else st["failed"]).append(j)
                done += 1
                if done % 10 == 0:
                    self.report("verify", done, total, f"Re-ran the full cascade at {done} of {total} unlocked sites")
        # what's left: the sites still blocked, re-run with the whole plan
        final = self._ups(self.rate_final)
        held = self.holds(self.rate_final)
        self.hit_after = np.where(held, 0.0, -1.0)
        for st in self.steps:
            for j in st["failed"]:
                self.hit_after[j] = -1.0
        left = [j for j in range(self.ns) if self.hit_after[j] < 0 and self.hit0[j] != 0]
        left.sort(key=lambda j: -self.hit0[j])
        stop = self.t0 + self.budget * AFTER_SHARE
        for k, j in enumerate(left):
            if time.perf_counter() > stop:
                break
            _, h = self._calm(j, final)
            self.hit_after[j] = h
            if (k + 1) % 10 == 0:
                self.report("verify", k + 1, len(left), f"Re-ran {k + 1} of {len(left)} sites that are still blocked")
        for j in range(self.ns):  # sites calm before stay calm: upgrades only raise ratings
            if self.ok0[j]:
                self.hit_after[j] = 0.0

    # ------------------------------------------------------------------ 4 AI proposes, the engine verifies
    def ai_prompt(self, feedback: str = "") -> tuple[str, dict[int, float]]:
        """The weak points and the blocked sites (biggest blackouts first) with exactly what each needs, so Gemini
        can combine packages that share lines; only these ids may be used."""
        g = self.g
        pts = self.points[:AI_POINTS]
        allowed = {p["branch_id"]: p["rating_mva"] for p in pts}
        rows = []
        for p in pts:
            i = g.br_index[p["branch_id"]]
            rows.append(
                f"- {p['branch_id']}: {p['label']} ({p['kind']}, {p['kv']:.0f} kV), {p['rating_mva']:,.0f} MVA now, max {self.cap[i]:,.0f}; "
                f"blocks {p['sites_blocked']} sites, first to fail in {p['first_fail']} simulated cascades"
            )
        order = np.argsort(-self.hit0, kind="stable")
        sites = []
        for j in order:
            j = int(j)
            if self.ok0[j] or not self.site_fixable[j] or self.hit0[j] <= 0:
                continue
            e = np.flatnonzero(self.e_site == j)
            if len(e) > AI_MAX_LINES:
                continue
            need = {int(g.br_ids[self.e_line[x]]): float(self.lvl[x]) for x in e}
            hi = sum(self.cost(int(self.e_line[x]), float(self.lvl[x]))[1] for x in e)
            for x in e:
                allowed.setdefault(int(g.br_ids[self.e_line[x]]), float(g.rate[self.e_line[x]]))
            sites.append(
                f"- {self.cands[j][2]} (a campus there would hit ~{_n(self.hit0[j])} people): "
                + ", ".join(f"{bid} to {v:,.0f}" for bid, v in need.items())
                + f" (about ${hi / 1e6:,.0f}M)"
            )
            if len(sites) >= AI_SITES:
                break
        bar = self.engine_at(sum(st["hi"] for st in self.steps[:AI_BAR_STEPS]))
        prompt = (
            f"A SYNTHETIC grid model of {REGIONS[self.code]['name']} (not a real utility's network). A {self.mw:,.0f} MW data-center campus "
            f"was simulated at {self.ns} candidate sites, one per town: {int(self.ok0.sum())} take it with no line over its limit.\n\n"
            "The structural weak points (id: name, rating now in MVA, the most it can be raised to, what it does):\n"
            + "\n".join(rows)
            + "\n\nBlocked sites, biggest blackout first, with EVERY line each needs (line id to MVA) before it holds the campus:\n"
            + "\n".join(sites)
            + f"\n\nThe engine's own greedy plan (cheapest cost per unlocked site first), cut at about ${bar['cost_high'] / 1e6:,.0f}M (high end), "
            f"lets {bar['more_sites']} more sites hold the campus. Propose {AI_BUNDLES} DIFFERENT upgrade bundles that beat it: more sites for "
            "the same money, or the same sites for less (look for sites that share lines). A site only holds when every line it needs is raised. "
            "The engine re-runs every bundle, so name exact line ids and ratings. Rules: use only the ids listed above; a new rating must be above "
            f"the rating now and at most 5 times it; at most {AI_MAX_LINES} lines per bundle."
            + (f"\n\n{feedback}" if feedback else "")
            + '\n\nAnswer only as JSON: {"bundles": [{"name": "at most 6 words", "why": "one plain sentence", "upgrades": [{"line_id": 123, "to_mva": 900}]}]}'
        )
        return prompt, allowed

    def clean_bundle(self, b, allowed: dict[int, float]) -> tuple[dict[int, float], str, str] | None:
        if not isinstance(b, dict):
            return None
        g = self.g
        ch: dict[int, float] = {}
        for u in b.get("upgrades") or []:
            try:
                bid, to = int(u["line_id"]), float(u["to_mva"])
            except (KeyError, TypeError, ValueError):
                continue
            if bid not in allowed or bid not in g.br_index or not math.isfinite(to):
                continue
            i = g.br_index[bid]
            if to <= g.rate[i] + 1e-6:
                continue
            ch[i] = round(min(to, float(self.cap[i])), 1)
            if len(ch) >= AI_MAX_LINES:
                break
        if not ch:
            return None
        name = str(b.get("name") or "AI bundle").strip()[:48] or "AI bundle"
        why = str(b.get("why") or "").strip()[:220]
        return ch, name, why

    def check_bundle(self, ch: dict[int, float], name: str, why: str) -> tuple[dict | None, str]:
        """Run one bundle through the engine: the sites it lets hold, each re-run by the full cascade."""
        g = self.g
        rate = g.rate.copy()
        for i, v in ch.items():
            rate[i] = max(rate[i], v)
        newly = np.flatnonzero(self.holds(rate) & ~self.ok0)
        ups = self._ups(rate)
        newly = sorted(newly, key=lambda j: -self.hit0[j])
        verified, failed = [], []
        for j in newly[:AI_VERIFY_MAX]:
            ok, _ = self._calm(j, ups)
            (verified if ok else failed).append(int(j))
        if not verified:
            left = [p["label"] for p in self.points[:AI_POINTS] if rate[g.br_index[p["branch_id"]]] < p["need_max_mw"]][:4]
            return None, f"Bundle '{name}' unlocked no site in the engine" + (f"; still over for the busiest sites: {', '.join(left)}." if left else ".")
        lo, hi = self.plan_cost(rate)
        return {
            "name": name,
            "why": why,
            "by": "gemini",
            "changes": {i: (float(g.rate[i]), float(rate[i])) for i in ch},
            "verified": verified,
            "failed": failed,
            "unverified": [int(j) for j in newly[AI_VERIFY_MAX:]],
            "lo": lo,
            "hi": hi,
            "rate": rate,
        }, ""

    # ------------------------------------------------------------------ the answer
    def summary_engine(self) -> dict:
        lo, hi = self.plan_cost(self.rate_final)
        more = sum(len(s.get("verified", s["newly"])) + len(s.get("unverified", [])) for s in self.steps)
        return {"lines": int((self.rate_final > self.g.rate + 1e-6).sum()), "low": lo, "high": hi, "more": more}

    def engine_at(self, cost_high: float) -> dict:
        """The engine's plan cut at the last step that costs at most `cost_high` (high end): how many more sites hold."""
        cum = more = 0.0
        out = {"more_sites": 0, "cost_high": 0, "step": 0}
        for n, st in enumerate(self.steps, 1):
            cum += st["hi"]
            more += len(st.get("verified", st["newly"])) + len(st.get("unverified", []))
            if cum > cost_high + 1e-6:
                break
            out = {"more_sites": int(more), "cost_high": round(cum), "step": n}
        return out

    def project(self, i: int, before: float, after: float, step: int | None, by: str) -> dict:
        """A proposed upgrade as a project record: what a later feature needs to compare and assign upgrades
        across companies with Build plans' overlap logic (gridlock.py): where (both ends, a segment or a point),
        what (rating before and after), what it costs, whose area, and an empty build window."""
        g = self.g
        b = _branch(g, i)
        lo, hi, it = self.cost(i, after)
        lo0, hi0, _ = self.cost(i, before)
        line = b["kind"] == "line"
        return {
            "id": f"{self.code}-{b['branch_id']}-{int(round(after))}",
            "name": f"Raise {b['label']} ({b['kv']:.0f} kV) from {before:,.0f} to {after:,.0f} MVA",
            "branch_id": b["branch_id"],
            "kind": b["kind"],
            "kv": b["kv"],
            "label": b["label"],
            "short": b["short"],
            "where": b["where"],
            "from": b["from"],
            "to": b["to"],
            "geometry": {"type": "segment" if line else "point", "coords": [[b["from"]["lon"], b["from"]["lat"]], [b["to"]["lon"], b["to"]["lat"]]] if line else [[b["from"]["lon"], b["from"]["lat"]]]},
            "rating_before_mva": round(before, 1),
            "rating_after_mva": round(after, 1),
            "rating_original_mva": round(float(g.rate[i]), 1),
            **_rating_fields(g, i),  # the original rating is an estimate when the dataset gives none
            "cost": {"low": round(lo - lo0), "high": round(hi - hi0), "method": it["method"] if it else None, "miles": it["miles"] if it else None},
            "owner": b["from"]["area"] or _title(b["from"]["name"]),  # the substation's area: there is no real utility
            "window": None,  # the build window: empty until a later feature schedules it
            "weak_point": self._point_rank.get(int(i)),  # its rank among the structural points, if it is one
            "step": step,
            "by": by,
        }

    def _site_brief(self, j: int) -> dict:
        """An unlocked site, with the lines that were over with the campus there (`needs`: branch ids), so the
        panel can try that one site with only the upgrades it needs."""
        r = self.site_rows[j]
        needs = sorted({int(self.g.br_ids[i]) for i in self.e_line[self.e_site == j]})
        return {"id": r["id"], "area": r["area"], "lat": r["lat"], "lon": r["lon"], "hit0": int(max(self.hit0[j], 0)) if self.hit0[j] >= 0 else None, "needs": needs}

    def _worst(self, hits: np.ndarray) -> dict:
        known = np.flatnonzero(hits > 0)
        if not len(known):
            return {"people_hit": 0, "area": None}
        j = int(known[np.argmax(hits[known])])
        return {"people_hit": int(hits[j]), "area": self.cands[j][2]}

    def result(self, ai: dict) -> dict:
        g = self.g
        base = {
            "region": self.code,
            "region_name": REGIONS[self.code]["name"],
            "mw": self.mw,
            "load_factor": g.load_factor,
            "note": NOTE,
            "synthetic": True,
        }
        if self.baseline is not None:
            return {**base, "already_failing": True, "baseline_people_hit": _hit(self.baseline), "points": [], "steps": [], "sites": [], "ai": ai, "learned": self._learned()}
        steps = []
        cum_lo = cum_hi = 0.0
        ok_now = int(self.ok0.sum())
        self._point_rank = {g.br_index[p["branch_id"]]: p["rank"] for p in self.points}
        lines: set[int] = set()
        # the blackouts a campus would set off today, biggest first: `biggest_gone` counts how many of the biggest are
        # gone (in a row from the top) once a step's sites connect with no line over its limit
        ranked = [int(j) for j in np.argsort(-self.hit0, kind="stable") if self.hit0[j] > 0]
        rank_of = {j: k for k, j in enumerate(ranked)}
        gone: set[int] = set()
        lead = 0
        over_prev = self.overloads(g.rate)
        for n, st in enumerate(self.steps, 1):
            lines.update(st["changes"])
            good = st["verified"] + st["unverified"]  # verified, or not reached in time (flagged)
            ok_now += len(good)
            cum_lo += st["lo"]
            cum_hi += st["hi"]
            lost = [self.hit0[j] for j in good if self.hit0[j] > 0]
            gone.update(rank_of[j] for j in good if j in rank_of)
            while lead in gone:
                lead += 1
            over_now = self.overloads(st["rate"])
            steps.append(
                {
                    "n": n,
                    "by": "engine",
                    "projects": [self.project(i, a, b, n, "engine") for i, (a, b) in st["changes"].items()],
                    "cost": {"low": round(st["lo"]), "high": round(st["hi"])},
                    "cum_cost": {"low": round(cum_lo), "high": round(cum_hi)},
                    "cum_upgrades": len(lines),  # distinct lines and transformers upgraded so far
                    "sites_ok": ok_now,
                    "newly": [self._site_brief(j) for j in sorted(good, key=lambda j: -self.hit0[j])],
                    "newly_count": len(good),
                    "verified": len(st["verified"]),
                    "failed": len(st["failed"]),
                    "unverified": len(st["unverified"]),
                    "blackout_prevented_max": int(max(lost)) if lost else 0,
                    "blackout_sites_prevented": len(lost),
                    "mw_unlocked": round((ok_now - int(self.ok0.sum())) * self.mw),  # site capacity, each site on its own
                    "overloads_before": over_prev,  # line overloads across the tested sites before this step
                    "overloads_left": over_now,  # line overloads across the tested sites after this step
                    "biggest_gone": lead,  # the biggest blackouts (today's, in a row from the top) no longer set off
                }
            )
            over_prev = over_now
        # fresh rows for this answer (a later Gemini retry builds another; a cached answer is never changed in place)
        rows = [{**row, "unlocked_at": None, "hit_after": int(self.hit_after[j]) if self.hit_after[j] >= 0 else None} for j, row in enumerate(self.site_rows)]
        for s in steps:
            for x in s["newly"]:
                rows[self._site_index[x["id"]]]["unlocked_at"] = s["n"]
        eng = self.summary_engine()
        more = sum(s["newly_count"] for s in steps)
        simulated = self.hit0 >= 0
        before_bo = int(((self.hit0 > 0) & simulated).sum())
        after_known = self.hit_after >= 0
        after_bo = int((self.hit_after > 0).sum())
        apply = {str(k): v for k, v in self._ups(self.rate_final).items()}
        worst0, worst1 = self._worst(self.hit0), self._worst(self.hit_after)
        strain = {
            # the study's own numbers, today and with the whole plan (each site tested on its own)
            "line_overloads_before": self.overloads(g.rate),
            "line_overloads_after": self.overloads(self.rate_final),
            "blackout_sites_before": before_bo,
            "blackout_sites_after": after_bo,
            "worst_people_before": worst0["people_hit"],
            "worst_people_after": worst1["people_hit"],
        }
        strain["sentence"] = (
            f"Strain, with the whole plan: line overloads across the {self.ns} tested sites {strain['line_overloads_before']:,} to "
            f"{strain['line_overloads_after']:,}; sites that set off a blackout {before_bo:,} to {after_bo:,}"
            + (f"; the worst blackout an estimated {_n(worst0['people_hit'])} to {_n(worst1['people_hit'])} people." if worst0["people_hit"] else ".")
        )
        mw_unlocked = more * self.mw
        # site options, not capacity the grid carries together: the sum can exceed the state's whole load
        sentence = (
            f"{eng['lines']:,} {'upgrade' if eng['lines'] == 1 else 'upgrades'}, {_money(eng['high'])} (high end): {more:,} more "
            f"{'site' if more == 1 else 'sites'} can host {self.mw:,.0f} MW, {_gw(mw_unlocked)} of site options ({ALONE})."
            if more
            else f"No upgrade within five times a line's rating lets another site host {self.mw:,.0f} MW."
        )
        out = {
            **base,
            "already_failing": False,
            "sites_total": self.ns,
            "points": self.points,
            "steps": steps,
            "sites": rows,
            "before": {
                "sites_ok": int(self.ok0.sum()),
                "blackout_sites": before_bo,
                "simulated": int(simulated.sum()),
                "worst": self._worst(self.hit0),
                "short_sites": int(self.short.sum()),  # the generators can't supply the campus there: needs generation, not lines
            },
            "after": {
                "sites_ok": int(self.ok0.sum()) + more,
                "blackout_sites": after_bo,
                "known": int(after_known.sum()),
                "worst": self._worst(self.hit_after),
            },
            "headline": {
                "upgrades": eng["lines"],
                "cost_low": round(eng["low"]),
                "cost_high": round(eng["high"]),
                "more_sites": more,
                "gw": round(more * self.mw / 1000.0, 1),
                "mw_unlocked": round(mw_unlocked),  # site options: each site tested alone, not all at once (never a sum that connects together)
                "mw_unlocked_note": f"{_gw(mw_unlocked)} of site options: {ALONE}; not capacity the grid carries together." if more else None,
                "sentence": sentence,  # the plan in plain siting language
                "strain": strain,
            },
            "apply": apply,
            "ai": ai,
            "capacity": getattr(self, "capacity", None),  # campuses at once and the upgrades for each next one (capacity.py)
            "learned": self._learned(),
            "cost_basis": "Each upgrade priced with published figures: re-conductoring (low) or a rebuild (high) per mile at the line's voltage class, a new line past twice the rating, and $ per MVA for transformers (Black & Veatch 2014, CPI-adjusted to 2024 $). Ranked and totaled at the high end.",
            "sources": [costs.SOURCES[k] for k in ("bv_wecc", "gridlab_2035", "cpi")],
        }
        return out

    def _learned(self) -> dict:
        return {
            "sites": getattr(self, "ns", 0),
            "blocked": getattr(self, "risky", 0),
            "cascades": self.cascades,
            "verify_cascades": self.verify_cascades,
            "solves": self.solves,
            "moves_tested": self.moves,
            "rounds": self.rounds,
            "partial": bool(getattr(self, "simulated", 0) < getattr(self, "risky", 0)) or not getattr(self, "verified_all", True),
            "seconds": round(self.elapsed(), 1),
        }

    def finish_index(self) -> None:
        self._site_index = {r["id"]: j for j, r in enumerate(self.site_rows)}
        self._point_rank = {self.g.br_index[p["branch_id"]]: p["rank"] for p in self.points}


def _ai_bundle_out(study: Study, bundle: dict, eng: dict) -> dict:
    g = study.g
    n_ok = len(bundle["verified"]) + len(bundle["unverified"])
    per = bundle["hi"] / n_ok if n_ok else None
    same = study.engine_at(bundle["hi"])  # the engine's plan with the same money (high end): a fair head-to-head
    lost = [study.hit0[j] for j in bundle["verified"] if study.hit0[j] > 0]
    return {
        "name": bundle["name"],
        "why": bundle["why"],
        "by": "gemini",
        "projects": [study.project(i, a, b, None, "gemini") for i, (a, b) in bundle["changes"].items()],
        "cost": {"low": round(bundle["lo"]), "high": round(bundle["hi"])},
        "sites_ok": int(study.ok0.sum()) + n_ok,
        "more_sites": n_ok,
        "verified": len(bundle["verified"]),
        "failed": len(bundle["failed"]),
        "unverified": len(bundle["unverified"]),
        "cost_per_site_high": round(per) if per is not None else None,
        "engine_same_cost": same,  # {more_sites, cost_high, step}: the engine's plan cut at this bundle's cost
        "beats_engine": n_ok > same["more_sites"],
        "blackout_prevented_max": int(max(lost)) if lost else 0,
        "apply": {str(k): v for k, v in study._ups(bundle["rate"]).items()},
        "newly": [study._site_brief(j) for j in bundle["verified"][:40]],
    }


async def _ai(study: Study, report) -> dict:
    """Gemini proposes bundles over the weak points; the engine keeps only the ones it verifies."""
    if study.baseline is not None or not study.points or not study.steps:
        return {"status": "skipped", "bundles": [], "asked": 0, "rejected": []}
    if not configured():
        return {"status": "not_configured", "bundles": [], "asked": 0, "rejected": []}
    report("ai", 0, AI_BUNDLES, "Asking Gemini for other upgrade bundles")
    eng = study.summary_engine()
    kept: list[dict] = []
    rejected: list[str] = []
    asked = 0
    feedback = ""
    status = "used"
    for rnd in range(AI_ROUNDS):
        if rnd and study.elapsed() > study.budget * 0.9:
            break
        prompt, allowed = study.ai_prompt(feedback)
        raw, offline = await complete_json(prompt, system=SYSTEM, fallback={"bundles": []}, timeout=AI_TIMEOUT_S, surface="unlock")
        if offline:
            status = "offline" if rnd == 0 else status
            break
        listed = raw if isinstance(raw, list) else (raw.get("bundles") if isinstance(raw, dict) else None)
        failed = []
        for b in [x for x in (listed or []) if isinstance(x, dict)][:AI_BUNDLES]:
            clean = study.clean_bundle(b, allowed)
            if clean is None:
                note_check("unlock", False, "a bundle of upgrades that named no line it could raise")
                continue
            asked += 1
            report("ai", asked, AI_BUNDLES * AI_ROUNDS, f"The engine is checking Gemini's bundle '{clean[1]}'")

            def run(clean=clean):
                with _compute_lock:
                    return study.check_bundle(*clean)

            out, fb = await run_in_threadpool(run)
            note_check("unlock", out is not None, "a bundle of upgrades the engine re-scanned: it unlocked no site")
            if out is None:
                failed.append(fb)
                rejected.append(fb)
            elif not any(set(k["changes"]) == set(out["changes"]) for k in kept):
                kept.append(out)
        if kept or not failed:
            break
        feedback = "The engine checked your previous bundles:\n" + "\n".join(failed[:3]) + "\nPropose replacement bundles that raise the lines that block the most sites."
    bundles = [_ai_bundle_out(study, b, eng) for b in kept]
    bundles.sort(key=lambda b: (-(b["more_sites"]), b["cost"]["high"]))
    return {"status": status if (kept or status != "used") else "none_verified", "bundles": bundles, "asked": asked, "rejected": rejected[:4]}


SYSTEM = (
    "You are a careful transmission planner. The grid is a SYNTHETIC model (Breakthrough Energy / Texas A&M), not any real utility's "
    "network, and describes no real project. Never name real companies, utilities or projects. Reply with JSON only."
)


# ---------------------------------------------------------------------------------- jobs
class UnlockIn(BaseModel):
    region: str = DEFAULT_REGION
    mw: float = 1000.0
    load_factor: float = 1.0


def _key_of(body: UnlockIn) -> tuple[str, float, float]:
    if (body.region or "").strip().upper() == "US":
        raise HTTPException(status_code=422, detail="Open a state first: the weak points are found on one state's model")
    code = region_code(body.region)
    if not math.isfinite(body.mw) or not (MW_LO <= body.mw <= MW_HI):
        raise HTTPException(status_code=422, detail=f"Campus size must be between {MW_LO:,} and {MW_HI:,} MW")
    if not math.isfinite(body.load_factor) or not (LOAD_FACTOR_MIN <= body.load_factor <= LOAD_FACTOR_MAX):
        raise HTTPException(status_code=422, detail=f"Load level must be between {LOAD_FACTOR_MIN} and {LOAD_FACTOR_MAX}")
    mw = float(min(MW_HI, max(MW_LO, math.floor(body.mw / MW_STEP + 0.5) * MW_STEP)))
    return code, mw, round(float(body.load_factor), 2)


class _Job:
    __slots__ = ("id", "key", "status", "progress", "partial", "result", "error", "created", "task")

    def __init__(self, key: tuple):
        self.id = secrets.token_urlsafe(12)
        self.key = key
        self.status = "queued"
        self.progress = {"phase": "queued", "done": 0, "total": 0, "message": "Waiting for the engine"}
        self.partial: dict = {"sites": None, "points": None}
        self.result = None
        self.error = None
        self.created = time.monotonic()
        self.task = None


_jobs: "OrderedDict[str, _Job]" = OrderedDict()
_jobs_lock = threading.Lock()


def _gc_jobs() -> None:
    now = time.monotonic()
    for jid in [j for j, job in _jobs.items() if job.status in ("done", "error") and now - job.created > JOB_TTL_S]:
        del _jobs[jid]
    while len(_jobs) > JOBS_MAX:
        old = next((j for j, job in _jobs.items() if job.status in ("done", "error")), None)
        if old is None:
            break
        del _jobs[old]


def _found(key: tuple) -> tuple[_Job, bool] | None:
    """(a job for `key`, cached?) without starting one: a finished study as a fresh done job, or the job already
    computing it (the warm-up, or another visitor's run). Call with _jobs_lock held."""
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        _retry_ai(key, hit)
        job = _Job(key)
        job.result, job.status = hit, "done"
        job.progress = {"phase": "done", "done": 1, "total": 1, "message": "From the last run"}
        _jobs[job.id] = job
        return job, True
    same = next((j for j in _jobs.values() if j.key == key and j.status in ("queued", "running")), None)
    return (same, False) if same is not None else None


_AI_FELL = ("offline", "error")
_bg_tasks: set = set()  # Gemini's capacity challenges still running after their study was published


def _cap_ai_status(result: dict) -> str | None:
    return ((result.get("capacity") or {}).get("ai") or {}).get("status")


async def _bundles_step(study: Study, report, prev: dict | None = None) -> dict:
    """Gemini's site bundles (_ai). With `prev` (a cached result: the retry) it runs again only if it fell back."""
    if prev is not None and (prev.get("ai") or {}).get("status") not in _AI_FELL:
        return prev["ai"]
    try:
        return await _ai(study, report)
    except Exception:  # noqa: BLE001 — the AI step is a bonus; the engine's plan stands alone
        log.exception("unlock: AI step failed")
        return {"status": "error", "bundles": [], "asked": 0, "rejected": []}


async def _challenge_step(study: Study, prev: dict | None = None) -> dict | None:
    """Gemini's try at beating the capacity plan (capacity_ai.challenge; it reports no progress: the study is already
    published). With `prev` (the retry) it runs again only if it fell back; a pending one is still running elsewhere."""
    if getattr(study, "capacity", None) is None:
        return None
    if prev is not None and _cap_ai_status(prev) not in _AI_FELL:
        return (prev.get("capacity") or {}).get("ai")
    try:
        return await capacity_ai.challenge(study)
    except Exception:  # noqa: BLE001 — the capacity plan stands without Gemini's challenge
        log.exception("unlock: Gemini's capacity challenge failed")
        return capacity_ai.failed()


def _with_cap_ai(result: dict, cap_ai: dict | None) -> dict:
    """Gemini's challenge goes in result["capacity"]["ai"] (a fresh capacity dict: the study's own is never changed)."""
    if result.get("capacity") is not None and cap_ai is not None:
        result["capacity"] = {**result["capacity"], "ai": cap_ai}
    return result


def _attach_cap_ai(key: tuple, cap_ai: dict) -> None:
    """The challenge has landed: it replaces "pending" in the cached study for `key` and in every finished job that
    shows it (a fresh result dict each time, so a response already on its way is never changed under it)."""
    with _cache_lock:
        cur = _cache.get(key)
        if cur is not None and _cap_ai_status(cur) == "pending":
            _cache[key] = _with_cap_ai(dict(cur), cap_ai)
    with _jobs_lock:
        for j in list(_jobs.values()):
            if j.key == key and j.status == "done" and j.result is not None and _cap_ai_status(j.result) == "pending":
                j.result = _with_cap_ai(dict(j.result), cap_ai)


async def _land_challenge(key: tuple, task: "asyncio.Future") -> None:
    """Wait for the challenge started beside the bundles and put its verdict in the published study; whatever
    happens, "pending" never stays (an error lands as capacity_ai.failed())."""
    cap_ai = None
    try:
        cap_ai = await task
    except asyncio.CancelledError:
        _attach_cap_ai(key, capacity_ai.failed())
        raise
    except Exception:  # noqa: BLE001 — _challenge_step already catches; this is the last guard
        log.exception("unlock: Gemini's capacity challenge failed")
    _attach_cap_ai(key, cap_ai or capacity_ai.failed())
    log.info("unlock: Gemini's capacity challenge for %s at %.0f MW: %s", key[0], key[1], (cap_ai or {}).get("status"))


_EXTRAS = ("n1", "sensitivity")  # capacity fields filled in after the study is published
_sens_running: set[tuple] = set()  # studies whose sensitivity cases are computing
_sens_lock = threading.Lock()
SENS_RUNNING_MAX = 2  # sensitivity runs at once (each holds the engine a few seconds per case)


def _with_cap(result: dict | None, fields: dict) -> dict | None:
    """A fresh result with `fields` merged into its capacity section (the old dicts are never changed in place)."""
    if result is None or result.get("capacity") is None:
        return result
    return {**result, "capacity": {**result["capacity"], **fields}}


def _attach_cap(key: tuple, study: "Study | None", fields: dict) -> None:
    """The N-1 screen or the sensitivity cases have landed (or are now pending): into the study (a new capacity dict, so a
    later Gemini retry's result carries them), the cached study for `key`, and every finished job that shows it."""
    if study is not None and getattr(study, "capacity", None) is not None:
        study.capacity = {**study.capacity, **fields}
    with _cache_lock:
        cur = _cache.get(key)
        if cur is not None:
            _cache[key] = _with_cap(cur, fields)
    with _jobs_lock:
        for j in list(_jobs.values()):
            if j.key == key and j.status == "done" and j.result is not None:
                j.result = _with_cap(j.result, fields)


def _keep_extras(new: dict, cur: dict | None) -> dict:
    """A rebuilt result (a Gemini retry) keeps the N-1 screen and sensitivity cases that landed meanwhile."""
    cap, old = (new or {}).get("capacity"), (cur or {}).get("capacity")
    if not cap or not old:
        return new
    keep = {k: old[k] for k in _EXTRAS if (old.get(k) or {}).get("status") == "done" and (cap.get(k) or {}).get("status") != "done"}
    return _with_cap(new, keep) if keep else new


def _run_sensitivity(key: tuple, cands: list, firm: dict, study: "Study | None") -> None:
    """capacity.sensitivity for `key` (a thread: each case holds the engine's lock only for its own search)."""
    code, mw, lf = key
    try:
        out = capacity.sensitivity(code, mw, lf, cands, firm, lock=_compute_lock)
    except Exception:  # noqa: BLE001 — the plan stands without it; the fold says it didn't finish (and can try again)
        log.exception("unlock: sensitivity cases failed")
        try:
            cases = capacity.planned_cases(grid_at(lf, code))
        except Exception:  # noqa: BLE001 — the words only
            cases = []
        out = {"status": "error", "cases": cases, "estimate_s": capacity.sens_estimate(firm.get("seconds"), len(cases) or 4, code)}
    _attach_cap(key, study, {"sensitivity": out})
    with _sens_lock:
        _sens_running.discard(key)
    log.info("unlock: sensitivity for %s at %.0f MW: %s in %s s", code, mw, out.get("status"), out.get("seconds"))


def _extras(key: tuple, study: "Study", sens: bool) -> None:
    """After the study is published: the single-outage screen of the firm plan's headline set and whole set (every study:
    about a second), then the
    sensitivity cases for a warm study (elsewhere they run on request: POST /api/unlock/sensitivity)."""
    try:
        with _compute_lock:
            out = capacity.n1(study)
    except Exception:  # noqa: BLE001 — the plan stands without it; the card says the screen didn't run
        log.exception("unlock: N-1 screen failed")
        out = {"status": "error", "emergency_pct": capacity.EMERGENCY_PCT}
    _attach_cap(key, study, {"n1": out})
    log.info("unlock: N-1 screen for %s at %.0f MW: %s in %s s", key[0], key[1], out.get("status"), out.get("seconds"))
    if sens:
        with _sens_lock:
            if key in _sens_running:
                return
            _sens_running.add(key)
        _run_sensitivity(key, study.cands, study.capacity["firm"], study)


def _start_extras(key: tuple, study: "Study") -> None:
    if getattr(study, "capacity", None) is None or study.baseline is not None:
        return
    threading.Thread(target=_extras, args=(key, study, key in _PINNED), name="unlock-extras", daemon=True).start()


def _retry_ai(key: tuple, hit: dict) -> None:
    """A warm study whose Gemini step fell back (the free tier's per-minute quota, the network, the timeout) tries
    that step again in the background, at most every AI_RETRY_S; the engine's plan and its numbers are unchanged,
    and Gemini's bundles (and its capacity plan) are kept only when the engine verifies them, as in the first run."""
    study = _studies.get(key)
    fell = (hit.get("ai") or {}).get("status") in _AI_FELL or _cap_ai_status(hit) in _AI_FELL
    if study is None or not fell or not configured():
        return
    now = time.monotonic()
    if now - _ai_tried.get(key, 0.0) < AI_RETRY_S:
        return
    _ai_tried[key] = now

    async def again():
        ai, cap_ai = await asyncio.gather(_bundles_step(study, lambda *a: None, prev=hit), _challenge_step(study, prev=hit))
        _ai_tried[key] = time.monotonic()
        if ai["status"] in _AI_FELL and (cap_ai or {}).get("status") in _AI_FELL:
            return
        result = await run_in_threadpool(study.result, ai)
        with _cache_lock:
            # a challenge that landed meanwhile (or is still pending) is kept unless this retry got a real answer
            now_ai = ((_cache.get(key) or {}).get("capacity") or {}).get("ai")
            if now_ai is not None and (cap_ai is None or cap_ai.get("status") in _AI_FELL + ("pending",)):
                cap_ai = now_ai
            _cache[key] = _keep_extras(_with_cap_ai(result, cap_ai), _cache.get(key))
        log.info("unlock: Gemini steps retried for %s at %.0f MW: %s, %d bundles; capacity challenge %s", key[0], key[1], ai["status"], len(ai["bundles"]), (cap_ai or {}).get("status"))

    def run():
        try:
            asyncio.run(again())
        except Exception:  # noqa: BLE001 — a retry is best effort; the cached answer stands
            log.exception("unlock: Gemini retry failed")

    threading.Thread(target=run, name="unlock-ai-retry", daemon=True).start()


def _estimate(code: str) -> tuple[int, int]:
    """(seconds a study of `code` should take, candidate sites): from its last run, else a per-site rule of thumb."""
    g = grid_at(1.0, code)
    ns = len(danger.candidates(g, code))
    per = _measured.get(code) or (sum(_measured.values()) / len(_measured) if _measured else SECONDS_PER_SITE)
    cap = (BUDGET_FL if code == DEFAULT_REGION else BUDGET_OTHER) + AI_TIMEOUT_S * AI_ROUNDS
    return int(min(cap, max(5.0, math.ceil(per * ns + 2.0)))), ns


def _compute(job: _Job) -> Study:
    code, mw, lf = job.key

    def report(phase, done, total, message):
        job.progress = {"phase": phase, "done": int(done), "total": int(total), "message": message}

    with _compute_lock:
        job.status = "running"
        s = Study(code, mw, lf, report, job.partial)
        s.learn()
        if s.baseline is None:
            s.plan()
            s.verify()
            s.finish_index()
            # how many campuses the grid carries at once, and the cheapest upgrades for one more (capacity.py)
            report("capacity", 0, 1, f"Placing {mw:,.0f} MW campuses together, then the cheapest upgrade for each next one")
            try:
                # the N-1 screen and (warm studies only: LAZY) the sensitivity cases land after the study is published
                s.capacity = capacity.study(s, sensitivity_pending=job.key in _PINNED)
            except Exception:  # noqa: BLE001 — the site-by-site study still stands without it
                logging.getLogger("uvicorn.error").exception("capacity study failed")
                s.capacity = None
        return s


async def _run_job(job: _Job) -> None:
    chal = None  # Gemini's try at beating the capacity plan, running beside the bundles
    try:
        study = await run_in_threadpool(_compute, job)

        def report(phase, done, total, message):
            job.progress = {"phase": phase, "done": int(done), "total": int(total), "message": message}

        # Gemini's two steps start together: other upgrade bundles for single sites (the page waits for these, as
        # before), and its try at beating the capacity plan, which the page never waits for: the study is published
        # with capacity.ai "pending" and the verdict lands in it when the engine has judged it (_land_challenge)
        cap_ai = capacity_ai.opening(study)
        if cap_ai is not None and cap_ai["status"] == "pending":
            chal = asyncio.ensure_future(_challenge_step(study))
            _bg_tasks.add(chal)  # a strong reference while it runs (the event loop keeps only weak ones)
            chal.add_done_callback(_bg_tasks.discard)
        ai = await _bundles_step(study, report)
        _ai_tried[job.key] = time.monotonic()
        if job.key in _PINNED and study.baseline is None:
            _studies[job.key] = study
        if chal is not None and chal.done():
            cap_ai = chal.result() or capacity_ai.failed()  # it finished first: publish its verdict directly
            chal = None
        result = _with_cap_ai(await run_in_threadpool(study.result, ai), cap_ai)
        with _cache_lock:
            _cache[job.key] = result
            _cache.move_to_end(job.key)
            while len(_cache) > CACHE_SIZE:  # the oldest study goes first; the warm Florida study stays
                old = next((k for k in _cache if k not in _PINNED), None)
                if old is None:
                    break
                del _cache[old]
        if study.baseline is None and getattr(study, "ns", 0):
            _measured[job.key[0]] = result["learned"]["seconds"] / study.ns  # seconds per site, for the next estimate
        job.result = result
        job.progress = {"phase": "done", "done": 1, "total": 1, "message": f"Done in {result['learned']['seconds']:.0f} s"}
        job.status = "done"
        log.info("unlock: %s at %.0f MW (load %.2f) in %.1f s", job.key[0], job.key[1], job.key[2], result["learned"]["seconds"])
        _start_extras(job.key, study)  # the N-1 screen (and a warm study's sensitivity cases) land in the published study
    except HTTPException as e:
        job.error = e.detail if isinstance(e.detail, str) else "The study could not run"
        job.status = "error"
    except Exception:  # noqa: BLE001 — a background job must always end in a state the panel can show
        log.exception("unlock job failed")
        job.error = "The study failed on this request. Try again, or try a different size."
        job.status = "error"
    finally:
        job.task = None
        if chal is not None and job.status != "done":
            chal.cancel()  # the study failed: nothing to put the verdict in
            chal = None
    if chal is not None:
        # the published study waits for nothing; the verdict lands in it (a warm-up's own event loop stays open until then)
        await _land_challenge(job.key, chal)


# ---------------------------------------------------------------------------------- routes
@router.post("/api/unlock/start")
@limiter.limit("30/minute")
async def start(request: Request, body: UnlockIn):
    """Start (or join) a study in the background; poll GET /api/unlock/jobs/{id}. A cached study is done at once."""
    key = _key_of(body)
    with _jobs_lock:
        _gc_jobs()
        found = _found(key)
        if found is not None:
            job, cached = found
            return {"id": job.id, "status": job.status, "cached": cached}
        if sum(1 for j in _jobs.values() if j.status in ("queued", "running")) >= RUNNING_MAX:
            raise HTTPException(status_code=429, detail="The engine is busy with other studies. Try again in a minute.")
        job = _Job(key)
        _jobs[job.id] = job
    job.task = asyncio.create_task(_run_job(job))
    return {"id": job.id, "status": job.status, "cached": False}


@router.get("/api/unlock/jobs/{job_id}")
@limiter.limit("240/minute")
def job_status(request: Request, job_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_-]{8,40}$")):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="This study is no longer available. Run it again.")
    out = {"id": job.id, "status": job.status, "region": job.key[0], "mw": job.key[1], "load_factor": job.key[2], "progress": dict(job.progress)}
    if job.status == "done":
        out["result"] = job.result
    elif job.status == "error":
        out["error"] = job.error
    else:
        out["partial"] = {"sites": copy.copy(job.partial.get("sites")), "points": job.partial.get("points")}
    return out


@router.get("/api/unlock/peek")
@limiter.limit("240/minute")
def peek(
    request: Request,
    region: str = Query(DEFAULT_REGION, max_length=8),
    mw: float = Query(1000.0),
    load_factor: float = Query(1.0),
):
    """What the Strengthen page can show at once, without starting anything (LAZY): a finished study (a done job
    to fetch), the job already computing it, or nothing yet with how long a run should take."""
    key = _key_of(UnlockIn(region=region, mw=mw, load_factor=load_factor))
    with _jobs_lock:
        _gc_jobs()
        found = _found(key)
    est, ns = _estimate(key[0])
    out = {"region": key[0], "mw": key[1], "load_factor": key[2], "estimate_s": est, "sites": ns, "warm": key in _PINNED}
    if found is None:
        return {**out, "state": "none", "id": None}
    job, _ = found
    return {**out, "state": job.status, "id": job.id}


@router.post("/api/unlock/sensitivity")
@limiter.limit("30/minute")
def sensitivity(request: Request, body: UnlockIn):
    """How sure is the number: the firm search again under other assumptions (capacity.sensitivity), for a finished
    study. The warm Florida study has it already; elsewhere it runs on request (LAZY), in the background: the answer
    lands in the study's result (poll its job, as for Gemini's challenge)."""
    key = _key_of(body)
    with _cache_lock:
        cur = _cache.get(key)
    cap = (cur or {}).get("capacity")
    if cap is None or not (cap.get("firm") or {}).get("steps"):
        raise HTTPException(status_code=409, detail="Run the study first: this re-checks its plan under other assumptions.")
    sv = cap.get("sensitivity") or {}
    if sv.get("status") in ("pending", "done"):
        return {"status": sv["status"], "sensitivity": sv}
    with _sens_lock:
        if key not in _sens_running and len(_sens_running) >= SENS_RUNNING_MAX:
            raise HTTPException(status_code=429, detail="The engine is busy with other checks. Try again in a minute.")
        started = key not in _sens_running
        _sens_running.add(key)
    code, _, lf = key
    cases = sv.get("cases") or capacity.planned_cases(grid_at(lf, code))
    est_s = sv.get("estimate_s") or capacity.sens_estimate((cap.get("firm") or {}).get("seconds"), len(cases), code)
    pending = {"status": "pending", "estimate_s": est_s, "cases": cases, "requested": True}
    study = _studies.get(key)
    _attach_cap(key, study, {"sensitivity": pending})
    if started:
        cands = study.cands if study is not None else danger.candidates(grid_at(lf, code), code)
        threading.Thread(target=_run_sensitivity, args=(key, cands, cap["firm"], study), name="unlock-sensitivity", daemon=True).start()
    return {"status": "pending", "sensitivity": pending}


# ---------------------------------------------------------------------------------- warm-up
def _warm_keys() -> list[tuple[str, float, float]]:
    """UNLOCK_WARM: "FL:1000" (default), "FL:1000,FL:500", or "0" for none. Florida only (LAZY)."""
    out = []
    for item in os.getenv("UNLOCK_WARM", f"{DEFAULT_REGION}:1000").split(","):
        code, _, size = item.strip().partition(":")
        if code.strip().upper() != DEFAULT_REGION:
            continue
        try:
            out.append(_key_of(UnlockIn(region=code, mw=float(size or 1000), load_factor=1.0)))
        except (HTTPException, ValueError):
            log.warning("unlock: ignoring UNLOCK_WARM entry %r", item)
    return out


def _warm() -> None:
    """Compute the warm studies in this thread (its own event loop for the Gemini step); a visitor who opens the
    page meanwhile joins the running job and sees its progress."""
    for key in _WARM:
        with _jobs_lock:
            if _found(key) is not None:
                continue
            job = _Job(key)
            _jobs[job.id] = job
        try:
            asyncio.run(_run_job(job))
        except Exception:  # noqa: BLE001 — warming is best effort; the page's own request computes it anyway
            log.exception("unlock: warm-up failed")


_WARM = _warm_keys()
_PINNED.update(_WARM)
if _WARM:
    _warmer = threading.Timer(WARM_DELAY_S, _warm)
    _warmer.daemon = True  # never holds the process open at shutdown
    _warmer.start()
