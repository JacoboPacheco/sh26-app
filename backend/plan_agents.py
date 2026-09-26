"""Strengthen the grid: who decides what needs fixing? Three Gemini planner agents race the engine's own greedy search,
each with its own strategy, working through read-only engine tools with native function calling; a referee (code, no
AI) re-runs every submitted plan and ranks them. CLAUDE.md -> Decisions -> GEMINI MAX, MORE AGENTS, AI SURFACES,
STRENGTHEN IS THE HEART (the user, Sat 17:31: "is the thing that decides what needs fixing an agentic process?").

POST /api/strengthen/plan-race      {region, mw, load_factor, mode: firm|flexible, ai?} -> {id, status, cached}: starts (or
                                    joins, or returns the cached) race for a FINISHED Strengthen study; 409 when the study
                                    isn't done (LAZY: a race never starts a study)
GET  /api/strengthen/plan-race/{id} -> {status, knee, lanes: [each competitor's live trace, plan and verdict], result}
GET  /api/strengthen/knee           ?region=&mw=&load_factor=&mode= -> the budget bar (knee_budget) of a finished study

THE BAR. knee_budget(plan): the knee of the engine's cost curve (capacity.py's campuses-at-once search) -- the last
step before the cost of one more campus jumps: a paid step costing more than KNEE_JUMP x the average cost per paid
campus so far (Florida at 1 GW, always on: 7 campuses for $36.6 million; the 8th alone would cost $23.9 million).
THE COMPETITORS. The engine: its own plan cut at the knee. Three Gemini planners (llm.complete_tools, AGENT_MODEL,
tool mode ANY), each told the bar and one strategy: "Cheapest first" (fewest dollars per campus), "Corridors" (fix one
weak corridor fully, then place campuses along it), "Flexible-aware" (sites with room first; in a firm race the campuses
still stay always on, so it prefers sites with room). None sees the engine's sites.
THE TOOLS (read-only on the shared grid: every agent has its own working plan). weak_points() -- the lines and
transformers that stop the next campus (a sensitivity solve at every candidate site, with the plan so far);
sites(n, near_site_id) -- candidate sites with the room each has left (and, for a site that doesn't fit, the lines a
campus there pushes past their rating: a linear estimate); try_add(site_id, fix, raises, keep) -- the engine connects
one campus with an exact power-flow solve at every load level: holds, or which lines go over and by how much; with
fix the engine raises what goes over by its own rule (the flow plus a 10 % margin, 50 MVA steps, at most 5x the
original rating) and connects it when the budget allows; raise_cost(branch_id, to_mva) -- the engine's price;
current_plan(); undo_last(); submit_plan(reason). Each agent has AGENT_TOOL_CALLS tool calls, AGENT_TURNS Gemini turns
and AGENT_S seconds, and a plan of at most plan_max_for(knee) campuses (PLAN_MAX, and always PLAN_OVER past the bar, so
every bar can be beaten); every call and result goes into its trace (the frontend's AgentTrace row shape).
THE REFEREE (code). Every submitted plan is re-run the way capacity_ai.py verifies Gemini's challenge: one exact solve
with every campus and raise at once at every load level (raises snapped up to 50 MVA steps and capped at 5x; a raise
the working plan didn't size is sized by the engine's rule), priced by the engine (the model's figures are never read),
and a plan that holds also runs through the full cascade (nothing may trip, nobody may lose power). Rank: most
campuses within the knee budget, then cheapest, then fewest upgrades; an AI plan must beat the engine's (more
campuses, or the same number for more than a rounding margin less) to win, and plans within that margin of each other
share a place (a match is not a loss). A failed plan is shown with the engine's reason; no win is claimed unless verified.
FALLBACK. No key, no quota, offline, or ai=false: the engine's plan alone, with the reason, labeled.

Every number describes the SYNTHETIC grid model (Breakthrough Energy / Texas A&M), not any real utility's network;
costs are labeled estimates (high end); no real utility is named and the agents never speak for one.
"""

import asyncio
import copy
import json
import logging
import math
import re
import secrets
import threading
import time
from collections import Counter, OrderedDict

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import capacity
import danger
import llm
import unlock
from capacity_ai import _m
from grid import DEFAULT_REGION, REGIONS, grid_at
from limiter import limiter

router = APIRouter(tags=["strengthen"])
log = logging.getLogger("uvicorn.error")

SURFACE = "strengthen_race"  # the llm.SURFACES id for this feature (the lead adds the entry)
SURFACE_UNTIL = "unlock"  # used until that entry exists, so the AI panel's counters land on a surface it lists
MODES = ("firm", "flexible")
KNEE_JUMP = 2.5  # a paid step costing more than this x the average per paid campus so far ends the knee
AGENT_TOOL_CALLS = 32  # engine tool calls per agent before it must submit (several may come in one turn)
AGENT_TURNS = 28  # Gemini replies per agent
AGENT_S = 120.0  # wall clock per agent (the three run at once)
TURN_TIMEOUT_S = 15.0
RETRIES = 2  # a Gemini reply that didn't come: asked again this many times
RACE_HARD_S = 180.0  # the whole race, whatever happens
PLAN_MAX = 12  # campuses in an agent's plan at least (grid.MAX_SITES: such a plan can be re-checked on the public what-if)
PLAN_OVER = 5  # ... and always this many past the bar, so a bar above PLAN_MAX (Florida at 500 MW, flexible: 14) can be beaten
SITES_MAX = 20
POINTS_SHOWN = 8
NEEDS_SHOWN = 3
RAISES_MAX = 12  # raises in one try_add
SUBMIT_RAISES_MAX = 60
RESULT_MAX_CHARS = 3500  # a function response as Gemini reads it
NOTE_MAX = 200  # characters of an agent's "why" kept for the trace
TOL_SHARE, TOL_MIN = 0.02, 250_000.0  # "the same cost": within 2 % or $250k (capacity_ai's margin)
ROOM_CAP = 99_999.0
RACES_RUNNING_MAX = 2
JOBS_MAX = 24
JOB_TTL_S = 1800
CACHE_SIZE = 12
CACHE_TTL_S = 6 * 3600
FALLBACK_TTL_S = 90  # a race where every agent fell back while a key is set: tried again after this
STEP = unlock.STEP_MVA

SYSTEM = (
    "You are a careful transmission planner in a race. The grid is a SYNTHETIC model (Breakthrough Energy / Texas A&M), "
    "not any real utility's network, and describes no real project. Never name real companies, utilities or projects, "
    "and never speak for a utility. Act only through the tools: one or more function calls in every turn."
)

STRATEGIES = [
    {
        "id": "cheapest",
        "name": "Cheapest first",
        "short": "The fewest dollars per campus",
        "prompt": {
            "firm": (
                "connect the campuses that add the fewest dollars each. Take the free sites first (sites: fits true). Then, for each "
                "next campus, list sites(sort=\"cost\") (the engine's estimate of each site's price), price the two or three "
                "cheapest exactly with try_add(keep=false) in one turn, and connect the cheapest with try_add. Repeat until the "
                "budget can't buy another campus."
            ),
            "flexible": (
                "connect the campuses that add the fewest dollars each. Take the free sites first (sites: fits true). Then, for each "
                "next campus, list sites(sort=\"cost\") (the engine's estimate of each site's price), price the two or three "
                "cheapest exactly with try_add(keep=false) in one turn, and connect the cheapest with try_add. Repeat until the "
                "budget can't buy another campus."
            ),
        },
    },
    {
        "id": "corridors",
        "name": "Corridors",
        "short": "Fix one weak corridor fully, then build along it",
        "prompt": {
            "firm": (
                "find the weakest corridor (weak_points: the line or transformer that stops the most sites), raise it ONCE to a "
                "level that carries several campuses (price it with raise_cost, then pass that raise in try_add's raises), then "
                "place campuses near it (sites with near_site_id) before moving to the next corridor. One bigger raise shared by "
                "two or three campuses can beat several small ones. Take the free sites first and keep them (never undo a campus "
                "that fit for free)."
            ),
            "flexible": (
                "find the weakest corridor (weak_points: the line or transformer that stops the most sites), raise it ONCE to a "
                "level that carries several campuses (price it with raise_cost, then pass that raise in try_add's raises), then "
                "place campuses near it (sites with near_site_id) before moving to the next corridor. One bigger raise shared by "
                "two or three campuses can beat several small ones. Take the free sites first and keep them (never undo a campus "
                "that fit for free)."
            ),
        },
    },
    {
        "id": "flexible",
        "name": "Flexible-aware",
        "short": "Room first; upgrades last",
        "prompt": {
            "firm": (
                "this race is ALWAYS-ON service: every campus draws its full size at the peak, so there are no flexible hours to "
                "use. Prefer sites that still have room for a whole campus with no upgrade, spread across the state so they don't "
                "load the same lines (check sites again after each campus: room changes). Buy an upgrade only when no site has "
                "room, and then the one that leaves the most room for the next campus."
            ),
            "flexible": (
                "these campuses step down to half power at the afternoon peak, so many sites fit with no upgrade at all. Find every "
                "site with room at both load levels first (sites), spread them across the state so they don't load the same lines "
                "(check sites again after each campus: room changes), and buy upgrades last, only where one raise opens room for "
                "more than one campus."
            ),
        },
    },
]


# ---------------------------------------------------------------------------------- the budget bar
def knee_budget(plan: dict) -> dict:
    """The budget bar for a campuses-at-once plan (capacity.py's firm or flexible search: {"today", "steps": [{"n",
    "free", "cost": {"high"}, "cum_cost": {"high"}}]}): the knee of its cost curve, the last step before the cost of one
    more campus jumps.

    The walk: every free step is kept (it costs nothing). The first paid step is always kept (the bar is never below
    one upgrade). After it, a paid step whose cost is more than KNEE_JUMP x the average cost per PAID campus so far ends
    the knee (free campuses are left out of that average: they would make the first paid step look like a jump). With
    no jump the knee is the whole plan.

    Returns {"budget": high-end $ at the knee (0: no paid step), "budget_low", "campuses": campuses at once there,
    "today", "paid": upgraded campuses in it, "avg": average $ per upgraded campus, "next": the step that jumped
    ({"n", "cost_high", "times"}: its cost as a multiple of the average) or None, "jump": KNEE_JUMP, "rule", "sentence"}.
    Florida at 1,000 MW, always on (the baked study): 7 campuses for $36.6 million; the 8th alone costs $23.9 million,
    3.3x the $7.3 million per upgraded campus so far."""
    steps = list((plan or {}).get("steps") or [])
    today = int((plan or {}).get("today") or 0)
    rule = (
        f"The knee of the engine's cost curve: the last campus before one more would cost over {KNEE_JUMP:g}x the average "
        "per upgraded campus so far."
    )
    out = {"budget": 0.0, "budget_low": 0.0, "campuses": today, "today": today, "paid": 0, "avg": 0.0, "next": None, "jump": KNEE_JUMP, "rule": rule}
    if not steps:
        out["sentence"] = "No campus of this size fits, and no upgrade makes room for one."
        return out
    paid_sum = 0.0
    paid_n = 0
    knee = None  # the index of the last kept step
    nxt = None
    for k, st in enumerate(steps):
        cost = float(st["cost"]["high"])
        paid = not st.get("free") and cost > 0.5
        if not paid:
            knee = k
            continue
        if paid_n and cost > KNEE_JUMP * (paid_sum / paid_n):
            nxt = {"n": int(st["n"]), "cost_high": round(cost), "times": _times(cost / (paid_sum / paid_n))}
            break
        paid_sum += cost
        paid_n += 1
        knee = k
    if knee is None:  # (a first step that is paid is always kept above: unreachable, kept as a guard)
        knee = 0
    at = steps[knee]
    out.update(
        budget=float(at["cum_cost"]["high"]),
        budget_low=float(at["cum_cost"].get("low", 0.0)),
        campuses=int(at["n"]),
        paid=paid_n,
        avg=round(paid_sum / paid_n) if paid_n else 0.0,
        next=nxt,
    )
    at_once = _plural(out["campuses"], "campus", "campuses") + " at once"
    if not paid_n:
        out["sentence"] = f"Every campus in the engine's plan ({out['campuses']}) fits with no upgrade: nothing to buy."
    elif nxt:
        out["sentence"] = (
            f"{at_once} for {_m(out['budget'])}: the {_ordinal(nxt['n'])} alone would cost {_m(nxt['cost_high'])}, "
            f"{nxt['times']:g}x the {_m(out['avg'])} per upgraded campus so far."
        )
    else:
        out["sentence"] = f"{at_once} for {_m(out['budget'])}: the whole plan, with no jump in the cost per campus."
    return out


def _times(ratio: float) -> float:
    """A jump as the sentence prints it: one decimal, or rounded UP to two when one decimal would read as no more than
    KNEE_JUMP (2.508x is "2.51x", never "2.5x" next to a rule that says "over 2.5x")."""
    t = round(ratio, 1)
    return t if t > KNEE_JUMP else math.ceil(ratio * 100 - 1e-9) / 100


def plan_max_for(knee: dict) -> int:
    """The most campuses an agent's plan may have: at least PLAN_MAX, and always PLAN_OVER past the bar (so every bar
    can be beaten)."""
    return max(PLAN_MAX, int(knee.get("campuses") or 0) + PLAN_OVER)


def _ordinal(n: int) -> str:
    s = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{s}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


# ---------------------------------------------------------------------------------- the race's world (read-only)
_engine_lock = threading.Lock()  # the race's engine work runs one call at a time (CPU-bound; the grid is shared)


class Env:
    """One race: the finished study's grid at each load level, its candidate sites, the engine's pricing, the knee."""

    def __init__(self, key: tuple, res: dict, mode: str):
        code, mw, lf = key
        self.key, self.code, self.mw, self.lf, self.mode = key, code, float(mw), float(lf), mode
        cap_sec = res["capacity"]
        self.plan = cap_sec[mode]
        g = self.g = grid_at(lf, code)
        if mode == "flexible":
            share = float(cap_sec["flexible"].get("share") or capacity.FLEX_SHARE)
            lf_off = float(cap_sec["flexible"].get("off_peak_load_factor") or round(lf * capacity.FLEX_OFF, 2))
            self.levels = [(g, share), (grid_at(lf_off, code), 1.0)]
            self.lf_off = lf_off
        else:
            self.levels = [(g, 1.0)]
            self.lf_off = None
        self.cands = danger.candidates(g, code)
        self.buses = np.array([c[1] for c in self.cands], dtype=int)
        self.ids = {int(g.sub_ids[c[0]]): j for j, c in enumerate(self.cands)}
        self.cap = np.floor(g.rate * unlock.MAX_RERATE * 10) / 10
        self.fresh = [(np.abs(gk.base.flow) < gk.rate) & gk.base.active for gk, _ in self.levels]
        self.ctx = capacity._Ctx(code, mw, self.cands, g)  # the study's own pricing (unlock.Study.cost)
        self.knee = knee_budget(self.plan)
        self.budget = float(self.knee["budget"])
        self.plan_max = plan_max_for(self.knee)
        self.points = {int(p["branch_id"]): p for p in res.get("points") or []}
        self.region_name = REGIONS[code]["name"]
        self._names: dict[int, dict] = {}
        self._lat = {j: round(float(g.sub_lat[c[0]]), 4) for j, c in enumerate(self.cands)}
        self._lon = {j: round(float(g.sub_lon[c[0]]), 4) for j, c in enumerate(self.cands)}

    # ------------------------------------------------------------------ names and prices
    def site_id(self, j: int) -> int:
        return int(self.g.sub_ids[self.cands[j][0]])

    def area(self, j: int) -> str:
        return self.cands[j][2]

    def branch(self, i: int) -> dict:
        b = self._names.get(i)
        if b is None:
            b = self._names[i] = unlock._branch(self.g, i)
        return b

    def short(self, i: int) -> str:
        return self.branch(i)["short"]

    def bid(self, i: int) -> int:
        return int(self.g.br_ids[i])

    def price(self, i: int, level: float) -> tuple[float, float]:
        """(low, high) $ to raise branch i from its ORIGINAL rating to `level` (0 at or below it)."""
        lo, hi, _ = self.ctx.cost(int(i), float(level))
        return lo, hi

    def plan_cost(self, raises: dict[int, float]) -> tuple[float, float]:
        lo = hi = 0.0
        for i, L in raises.items():
            a, b = self.price(i, L)
            lo += a
            hi += b
        return lo, hi

    def size(self, i: int, flow: float) -> float:
        """The rating the engine gives a line raised for `flow`: the flow plus the 10 % margin (unlock.MARGIN), up to
        the next 50 MVA step, at most 5x the original (capacity._search's own rule)."""
        return float(min(math.ceil(abs(float(flow)) * unlock.MARGIN / STEP - 1e-9) * STEP, float(self.cap[i])))

    def snap(self, i: int, to: float) -> tuple[float, str | None]:
        """A requested rating as the engine builds it: up to the next 50 MVA step, at most 5x the original."""
        level = math.ceil(float(to) / STEP - 1e-9) * STEP
        if level > self.cap[i] + 1e-6:
            return float(self.cap[i]), f"{self.bid(i)} to {to:,.0f} MVA is past 5x its rating: capped at {self.cap[i]:,.0f}"
        if abs(level - to) > 0.05:
            return float(level), f"{self.bid(i)} to {to:,.0f} MVA rounded up to {level:,.0f} (50 MVA steps)"
        return float(level), None

    # ------------------------------------------------------------------ the network with a plan
    def rate_of(self, raises: dict[int, float]) -> np.ndarray:
        rate = self.g.rate.copy()
        for i, L in raises.items():
            rate[i] = max(rate[i], L)
        return rate

    def extra(self, sites: list[int], k: int) -> np.ndarray:
        gk, share = self.levels[k]
        e = np.zeros(gk.n)
        for j in sites:
            e[self.buses[j]] += share * self.mw
        return e

    def km(self, a: int, b: int) -> float:
        return float(_km(self._lat[a], self._lon[a], self._lat[b], self._lon[b]))

    def project(self, i: int, level: float, step: int | None = None, by: str = "gemini") -> dict:
        """An upgrade as the page shows it (capacity's project shape, with both ends for the map)."""
        b = self.branch(i)
        lo, hi = self.price(i, level)
        return {
            "id": f"{self.code}-{b['branch_id']}-{int(round(level))}",
            "branch_id": b["branch_id"],
            "kind": b["kind"],
            "kv": b["kv"],
            "label": b["label"],
            "short": b["short"],
            "where": b["where"],
            "from": b["from"],
            "to": b["to"],
            "mid": b["mid"],
            "rate_est": b.get("rate_est", False),
            "rating_before_mva": round(float(self.g.rate[i]), 1),
            "rating_original_mva": round(float(self.g.rate[i]), 1),
            "rating_after_mva": round(float(level), 1),
            "cost": {"low": round(lo), "high": round(hi)},
            "step": step,
            "by": by,
        }

    def placement(self, j: int, n: int) -> dict:
        return {"n": n, "id": self.site_id(j), "area": self.area(j), "lat": self._lat[j], "lon": self._lon[j]}

    # ------------------------------------------------------------------ the engine's plan, cut at the knee
    def engine_plan(self) -> tuple[list[int], dict[int, float], list[dict]]:
        steps = self.plan["steps"][: int(self.knee["campuses"])]
        sites: list[int] = []
        raises: dict[int, float] = {}
        trail: list[dict] = []
        for st in steps:
            j = self.ids.get(int(st["site"]["id"]))
            if j is None:
                continue
            added = {}
            for p in st["projects"]:
                i = self.g.br_index[int(p["branch_id"])]
                added[i] = max(added.get(i, 0.0), float(p["rating_after_mva"]))
                raises[i] = max(raises.get(i, 0.0), float(p["rating_after_mva"]))
            sites.append(j)
            trail.append({"j": j, "added": added, "cost_high": float(st["cost"]["high"]), "cost_low": float(st["cost"].get("low", 0.0)),
                          "cum_high": float(st["cum_cost"]["high"]), "cum_low": float(st["cum_cost"].get("low", 0.0))})
        return sites, raises, trail


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


class Workspace:
    """One agent's working plan: campus sites in the order they connected, the raises, and what each campus added."""

    def __init__(self, env: Env):
        self.env = env
        self.sites: list[int] = []
        self.raises: dict[int, float] = {}
        self.trail: list[dict] = []  # per campus: {j, added {i: level}, prev {i: rating before or None}, cost_high, cum_high, ...}
        self._rooms: dict | None = None

    def sig(self) -> tuple:
        return (tuple(self.sites), tuple(sorted(self.raises.items())))

    def cost(self) -> tuple[float, float]:
        return self.env.plan_cost(self.raises)


# ---------------------------------------------------------------------------------- the tools (engine work)
def _regimes(g, st, extra: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """How the engine dispatches the next MW, per bus, in two pieces (Grid._balance, as capacity._weights reads it):
    (weights now, MW until they change, weights after). While an island needs less than its plants make in the
    dataset the next MW comes pro rata to that output; past it, from the plants' headroom. Florida's base case is in
    the first piece for about 1 GW, so a campus's room read with the first piece's weights alone is far too kind."""
    n = g.n
    w1, w2, r1 = np.zeros(n), np.zeros(n), np.zeros(n)
    for c in np.unique(st.comp):
        idx = np.flatnonzero(st.comp == c)
        gmax, pg = g.pmax[idx], g.pg[idx]
        if gmax.sum() <= 1e-9:
            w1[idx] = w2[idx] = 1.0 / len(idx)
            continue
        head = gmax - pg
        w2[idx] = head / head.sum() if head.sum() > 1e-9 else gmax / gmax.sum()
        base = float(pg.sum())
        need = float((g.pd[idx] + extra[idx]).sum() - g.tie[idx].sum())
        if 0 <= need < base and base > 1e-9:
            w1[idx] = pg / base
            r1[idx] = base - need
        else:
            w1[idx] = w2[idx]
    return w1, w2, r1


def _limits_at(F: np.ndarray, dF: np.ndarray, rate: np.ndarray, fresh: np.ndarray) -> np.ndarray:
    """capacity._limits with a flow per column (F: branches x sites): MW until each line reaches its rating."""
    r = rate[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        tp = (r - F) / dF
        tn = (-r - F) / dF
    t = np.where(dF > 1e-9, tp, np.where(dF < -1e-9, tn, np.inf))
    t = np.where(fresh[:, None], t, np.inf)
    return np.maximum(t, 0.0)


def _rooms(env: Env, ws: Workspace) -> dict:
    """Every candidate site's room with the plan so far (MW of campus until the first line or transformer reaches its
    rating, the smaller over the load levels; the dispatch's two pieces followed along the way, _regimes), the element
    that binds it, and for each site the lines a whole campus there pushes past their rating (MW each would carry).
    Linear estimates: try_add is the exact answer. Cached per plan."""
    sig = ws.sig()
    if ws._rooms is not None and ws._rooms["sig"] == sig:
        return ws._rooms
    rate = env.rate_of(ws.raises)
    ns = len(env.cands)
    room = np.full(ns, np.inf)
    bind = np.full(ns, -1, dtype=int)
    needs: list[dict[int, float]] = [{} for _ in range(ns)]
    flows = []
    for k, (gk, share) in enumerate(env.levels):
        extra = env.extra(ws.sites, k)
        st = gk.solve(gk.base.active, extra, rate)
        w1, w2, r1b = _regimes(gk, st, extra)
        full = env.mw * share
        r1 = np.minimum(r1b[env.buses], full)  # MW of this campus served by the first piece
        dF1 = gk._sensitivity(env.buses, w1)
        dF2 = dF1 if not np.any(r1 < full - 1e-9) else gk._sensitivity(env.buses, w2)
        t1 = capacity._limits(st.flow, dF1, rate, env.fresh[k])
        room1 = t1.min(axis=0)
        bind1 = t1.argmin(axis=0)
        F2 = st.flow[:, None] + dF1 * r1[None, :]  # the flows where the second piece starts
        t2 = _limits_at(F2, dF2, rate, env.fresh[k])
        room2 = r1 + t2.min(axis=0)
        in1 = room1 < r1 - 1e-9
        rk = np.where(in1, room1, room2) / share
        bk = np.where(in1, bind1, t2.argmin(axis=0))
        better = rk < room
        room = np.where(better, rk, room)
        bind = np.where(better, bk, bind)
        # the flows with a whole campus at each site, and the lines over their rating then
        Ffull = F2 + dF2 * (full - r1)[None, :]
        over = (np.abs(Ffull) > rate[:, None] + 1e-6) & env.fresh[k][:, None]
        li, sj = np.nonzero(over)
        for i, j in zip(li.tolist(), sj.tolist()):
            need = abs(float(Ffull[i, j]))
            if need > needs[j].get(i, 0.0):
                needs[j][i] = need
        flows.append(st.flow.copy())
        del dF1, dF2, t1, t2, F2, Ffull, over
    out = {"sig": sig, "room": room, "bind": bind, "needs": needs, "flows": flows, "rate": rate, "used": set(ws.sites)}
    ws._rooms = out
    return out


def _plain(obj):
    """JSON-safe: numpy scalars and arrays become Python numbers and lists (a function response and the published race)."""
    if isinstance(obj, dict):
        return {(int(k) if isinstance(k, np.integer) else k if isinstance(k, (str, int)) else str(k)): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _plain(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    return obj


def _money_m(x: float) -> float:
    """$ as millions for Gemini (2 decimals)."""
    return round(float(x) / 1e6, 2)


def _state_line(env: Env, ws: Workspace) -> dict:
    lo, hi = ws.cost()
    return {"campuses": len(ws.sites), "upgrades": len(ws.raises), "cost_m": _money_m(hi), "budget_m": _money_m(env.budget), "budget_left_m": _money_m(max(env.budget - hi, 0.0))}


def t_weak_points(env: Env, ws: Workspace, args: dict) -> tuple[dict, str, str]:
    R = _rooms(env, ws)
    ns = len(env.cands)
    blocked = [j for j in range(ns) if j not in R["used"] and R["room"][j] < env.mw - 1e-6]
    fits = sum(1 for j in range(ns) if j not in R["used"] and R["room"][j] >= env.mw - 1e-6)
    cnt = Counter(int(R["bind"][j]) for j in blocked if R["bind"][j] >= 0)
    rate = R["rate"]
    rows = []
    for i, c in cnt.most_common(POINTS_SHOWN):
        flow = max(abs(float(f[i])) for f in R["flows"])
        js = sorted((j for j in blocked if int(R["bind"][j]) == i), key=lambda j: -R["room"][j])
        j0 = js[0]
        need = R["needs"][j0].get(i, flow)
        to = env.size(i, need)
        adds = max(env.price(i, to)[1] - env.price(i, float(rate[i]))[1], 0.0)
        p = env.points.get(env.bid(i)) or {}
        row = {
            "branch_id": env.bid(i),
            "name": env.short(i),
            "kind": env.branch(i)["kind"],
            "kv": round(float(env.g.br_kv[i])),
            "flow_mw": round(flow, 1),
            "rating_mva": round(float(rate[i]), 1),
            "loading_pct": round(flow / float(rate[i]) * 100.0, 1),
            "stops_sites": int(c),
            "closest_site": {"site_id": env.site_id(j0), "town": env.area(j0), "room_mw": round(float(min(R["room"][j0], ROOM_CAP)))},
            "raise_for_that_site_mva": round(to, 1),
            "raise_adds_m": _money_m(adds),
            "fixable": need <= float(env.cap[i]) + 1e-6,
        }
        if float(rate[i]) > float(env.g.rate[i]) + 1e-6:
            row["original_mva"] = round(float(env.g.rate[i]), 1)
        if p.get("first_fail"):
            row["first_to_fail_in_simulated_cascades"] = int(p["first_fail"])
        rows.append(row)
    out = {**_state_line(env, ws), "sites_with_room": fits, "sites_blocked": len(blocked), "weak_points": rows,
           "note": "stops_sites: candidate sites where this element reaches its rating first (a linear estimate); try_add is exact."}
    if rows:
        top = rows[0]
        s = f"{top['name']} stops {_plural(top['stops_sites'], 'site', 'sites')} (at {top['loading_pct']:.0f} %)"
        if len(rows) > 1:
            s += f"; {rows[1]['name']} stops {rows[1]['stops_sites']}"
        summary = f"Weak points: {s}; {_plural(fits, 'site still has', 'sites still have')} room"
    else:
        summary = f"No weak point binds: {_plural(fits, 'site has', 'sites have')} room for a whole campus"
    return out, summary, "info"


def _est_cost(env: Env, R: dict, j: int) -> float | None:
    """The engine's linear price for a campus at site j next: every line it pushes past its rating raised by the engine's
    rule, from the rating the plan gives it now (0 when it fits; None when a line would need more than 5x)."""
    rate = R["rate"]
    total = 0.0
    for i, need in R["needs"][j].items():
        if need > float(env.cap[i]) + 1e-6:
            return None
        total += max(env.price(i, env.size(i, need))[1] - env.price(i, float(rate[i]))[1], 0.0)
    return total


def t_sites(env: Env, ws: Workspace, args: dict) -> tuple[dict, str, str]:
    R = _rooms(env, ws)
    n = int(args.get("n") or 10)
    near = args.get("near_site_id")
    rate = R["rate"]
    pool = [j for j in range(len(env.cands)) if j not in R["used"]]
    j0 = env.ids.get(int(near)) if near is not None else None
    est = {j: _est_cost(env, R, j) for j in pool}
    if j0 is not None:
        pool.sort(key=lambda j: (env.km(j, j0), -R["room"][j]))
    elif args.get("sort") == "cost":
        pool.sort(key=lambda j: (est[j] if est[j] is not None else math.inf, -R["room"][j]))
    else:
        pool.sort(key=lambda j: (-R["room"][j], env.site_id(j)))
    rows = []
    for j in pool[:n]:
        r = float(R["room"][j])
        fits = r >= env.mw - 1e-6
        row = {"site_id": env.site_id(j), "town": env.area(j), "room_mw": round(min(r, ROOM_CAP)), "fits": fits}
        if j0 is not None:
            row["km"] = round(env.km(j, j0), 1)
        b = int(R["bind"][j])
        if b >= 0 and math.isfinite(r):
            row["limit"] = {"branch_id": env.bid(b), "name": env.short(b)}
        if not fits:
            nd = sorted(R["needs"][j].items(), key=lambda kv: -(kv[1] / float(rate[kv[0]])))[:NEEDS_SHOWN]
            row["pushes_over"] = [
                {"branch_id": env.bid(i), "name": env.short(i), "to_mva": round(env.size(i, need), 1), "fixable": need <= float(env.cap[i]) + 1e-6}
                for i, need in nd
            ]
            if len(R["needs"][j]) > NEEDS_SHOWN:
                row["pushes_over_count"] = len(R["needs"][j])
            row["est_cost_m"] = _money_m(est[j]) if est[j] is not None else None
        rows.append(row)
    k = sum(1 for r in rows if r["fits"])
    out = {**_state_line(env, ws), "campus_mw": round(env.mw), "sites": rows,
           "note": "room_mw: MW of campus before the first line reaches its rating, with your plan so far; pushes_over: what a whole campus there would overload, with the rating the engine would give it; est_cost_m: those raises at the engine's price. Linear estimates: try_add is exact."}
    where = f" near {env.area(j0)}" if j0 is not None else " by estimated cost" if args.get("sort") == "cost" else ""
    towns = [r["town"] for r in rows if r["fits"]]
    lead = ", ".join(towns[:3]) + (f" and {len(towns) - 3} more" if len(towns) > 3 else "")
    stops = Counter(r["limit"]["name"] for r in rows if not r["fits"] and r.get("limit"))
    summary = f"{_plural(len(rows), 'site', 'sites')}{where}: " + (f"{k} with room for a whole campus ({lead})" if k else "none with room for a whole campus")
    if stops:
        name, c = stops.most_common(1)[0]
        summary += f"; the {name} stops {_plural(c, 'of the others', 'of the others')}" if c > 1 else f"; the {name} stops one"
    return out, summary, "info"


def t_try_add(env: Env, ws: Workspace, args: dict) -> tuple[dict, str, str]:
    j = args["j"]
    fix, keep = args.get("fix", True) is not False, args.get("keep", True) is not False
    asked_raw: dict[int, float] = args.get("raises") or {}
    rate0 = env.rate_of(ws.raises)
    rate = rate0.copy()
    notes: list[str] = []
    asked: dict[int, float] = {}
    for i, to in asked_raw.items():
        level, note = env.snap(i, to)
        if note:
            notes.append(note)
        if level > rate[i] + 1e-6:
            rate[i] = level
            asked[i] = level
    new_sites = ws.sites + [j]
    states = []
    for k, (gk, _) in enumerate(env.levels):
        states.append(gk.solve(gk.base.active, env.extra(new_sites, k), rate))
    lost = max(float(s.lost_mw) for s in states)
    town = env.area(j)
    base = {"site_id": env.site_id(j), "town": town, "tested_only": not keep}
    if lost > 0.5:
        out = {**base, "connected": False, "reason": f"the model's plants can't supply it: {lost:,.0f} MW of load would be shed (no line upgrade fixes that)", **_state_line(env, ws)}
        return out, f"the plants can't supply it ({lost:,.0f} MW would be shed)", "over"
    flows = np.max(np.abs(np.vstack([s.flow for s in states])), axis=0)
    over_mask = np.zeros(env.g.m, dtype=bool)
    for k, s in enumerate(states):
        over_mask |= (np.abs(s.flow) > rate + 1e-6) & env.fresh[k]
    over = [int(i) for i in np.flatnonzero(over_mask)]
    added: dict[int, float] = {}
    sized = []
    for i, L in asked.items():
        if flows[i] <= L + 1e-6:
            s = env.size(i, flows[i])
            if s > L + 1e-6:
                sized.append(f"{env.short(i)} {L:,.0f} to {s:,.0f} MVA")
                L = s
            added[i] = L
    unfixable = []
    if fix:
        for i in over:
            if flows[i] > float(env.cap[i]) + 1e-6:
                unfixable.append(i)
                continue
            added[i] = max(added.get(i, float(rate0[i])), env.size(i, flows[i]))
    still = [i for i in over if i not in added or flows[i] > added[i] + 1e-6]
    final = dict(ws.raises)
    for i, L in added.items():
        final[i] = max(final.get(i, 0.0), L)
    lo, hi = env.plan_cost(final)
    lo0, hi0 = ws.cost()

    def over_rows(idx):
        rows = []
        for i in sorted(idx, key=lambda i: -flows[i] / float(rate[i]))[:6]:
            to = env.size(i, flows[i])
            rows.append({
                "branch_id": env.bid(i), "name": env.short(i), "flow_mw": round(float(flows[i]), 1), "rating_mva": round(float(rate[i]), 1),
                "pct": round(float(flows[i]) / float(rate[i]) * 100.0, 1), "raise_to_mva": round(to, 1),
                "raise_adds_m": _money_m(max(env.price(i, to)[1] - env.price(i, float(rate0[i]))[1], 0.0)),
                "fixable": flows[i] <= float(env.cap[i]) + 1e-6,
            })
        return rows

    if still:
        rows = over_rows(still)
        o = rows[0]
        total_add = sum(r["raise_adds_m"] for r in rows if r["fixable"])
        out = {**base, "connected": False, "lines_over": rows, "lines_over_count": len(still),
               "raises_would_add_m": round(total_add, 2) if all(r["fixable"] for r in rows) else None, **_state_line(env, ws)}
        if unfixable:
            out["reason"] = "a line would need more than 5x its rating: no raise fixes this site"
        else:
            out["hint"] = "call try_add again with fix=true (the engine raises these lines by its rule) or with your own raises"
        if notes:
            out["adjusted"] = notes[:4]
        more = f" (+{len(still) - 1} more)" if len(still) > 1 else ""
        fixtxt = "; no raise within 5x fixes it" if unfixable else f"; raising it to {o['raise_to_mva']:,.0f} MVA would add {_m(o['raise_adds_m'] * 1e6)}"
        return out, f"the {o['name']} goes to {o['pct']:.0f} %{more}{fixtxt}", "over"
    if hi > env.budget + 0.5:
        out = {**base, "connected": False, "reason": f"over the budget: your plan would cost {_money_m(hi)}M of {_money_m(env.budget)}M",
               "would_add_m": _money_m(hi - hi0), "raises_needed": [{"branch_id": env.bid(i), "name": env.short(i), "to_mva": L} for i, L in added.items()], **_state_line(env, ws)}
        return out, f"it fits only with {_m(hi - hi0)} of upgrades: over the budget ({_m(hi)} of {_m(env.budget)})", "over"
    r_final = env.rate_of(final)
    busiest = max(float(np.max(np.where(s.active, np.abs(s.flow) / r_final * 100.0, 0.0))) for s in states)
    ups = [{"branch_id": env.bid(i), "name": env.short(i), "to_mva": round(L, 1), "adds_m": _money_m(max(env.price(i, L)[1] - env.price(i, float(rate0[i]))[1], 0.0))}
           for i, L in sorted(added.items(), key=lambda kv: -kv[1]) if L > float(rate0[i]) + 1e-6]
    if keep:
        prev = {i: ws.raises.get(i) for i in added}
        ws.sites.append(j)
        ws.raises = final
        ws.trail.append({"j": j, "added": {i: L for i, L in added.items() if L > float(rate0[i]) + 1e-6}, "prev": prev,
                         "cost_high": hi - hi0, "cost_low": lo - lo0, "cum_high": hi, "cum_low": lo})
        ws._rooms = None
    out = {**base, "connected": bool(keep), "holds": True, "campus_n": len(ws.sites) if keep else len(ws.sites) + 1, "raises_added": ups,
           "adds_m": _money_m(hi - hi0), "busiest_line_pct": round(busiest, 1), **_state_line(env, ws)}
    if not keep:
        out["plan_would_cost_m"] = _money_m(hi)
    if sized:
        out["sized_up"] = sized[:3]
    if notes:
        out["adjusted"] = notes[:4]
    if not ups:
        summary = "fits as is" + (f": campus {out['campus_n']} connected" if keep else " (tested only)") + f"; busiest line {busiest:.0f} %"
    else:
        what = "; ".join(f"{u['name']} to {u['to_mva']:,.0f} MVA" for u in ups[:2]) + (f" (+{len(ups) - 2} more)" if len(ups) > 2 else "")
        if keep:
            summary = f"connected as campus {out['campus_n']} with {_plural(len(ups), 'upgrade', 'upgrades')} ({what}) for {_m(hi - hi0)}; plan {_m(hi)} of {_m(env.budget)}"
        else:
            summary = f"it would need {what}: {_m(hi - hi0)} (tested only)"
    return out, summary, "holds" if keep else "info"


def t_raise_cost(env: Env, ws: Workspace, args: dict) -> tuple[dict, str, str]:
    i = args["i"]
    level, note = env.snap(i, args["to_mva"])
    now = float(env.rate_of(ws.raises)[i])
    lo, hi = env.price(i, level)
    add = max(hi - env.price(i, now)[1], 0.0)
    b = env.branch(i)
    out = {"branch_id": env.bid(i), "name": b["short"], "kind": b["kind"], "kv": round(float(env.g.br_kv[i])), "original_mva": round(float(env.g.rate[i]), 1),
           "in_your_plan_mva": round(now, 1), "to_mva": round(level, 1), "cost_from_original_m": _money_m(hi), "cost_low_m": _money_m(lo),
           "adds_to_your_plan_m": _money_m(add), "max_mva": round(float(env.cap[i]), 1)}
    if note:
        out["adjusted"] = note
    if level <= float(env.g.rate[i]) + 1e-6:
        out["note"] = "not a raise: at or below its original rating"
    return out, f"{b['short']} at {level:,.0f} MVA: {_m(hi)} from its {float(env.g.rate[i]):,.0f} MVA" + (f" (adds {_m(add)} to its plan)" if now > float(env.g.rate[i]) + 1e-6 else ""), "info"


def t_current_plan(env: Env, ws: Workspace, args: dict) -> tuple[dict, str, str]:
    lo, hi = ws.cost()
    camp = [{"n": k + 1, "site_id": env.site_id(t["j"]), "town": env.area(t["j"]), "adds_m": _money_m(t["cost_high"])} for k, t in enumerate(ws.trail)]
    ups = [{"branch_id": env.bid(i), "name": env.short(i), "original_mva": round(float(env.g.rate[i]), 1), "to_mva": round(L, 1), "cost_m": _money_m(env.price(i, L)[1])}
           for i, L in sorted(ws.raises.items(), key=lambda kv: -env.price(kv[0], kv[1])[1])]
    bar_n = int(env.knee["campuses"])
    out = {**_state_line(env, ws), "campuses_list": camp, "upgrades_list": ups[:20], "bar": {"campuses": bar_n, "cost_m": _money_m(env.budget)}}
    return out, f"Its plan: {_plural(len(ws.sites), 'campus', 'campuses')}, {_plural(len(ws.raises), 'upgrade', 'upgrades')}, {_m(hi)} of {_m(env.budget)}", "info"


def t_undo_last(env: Env, ws: Workspace, args: dict) -> tuple[dict, str, str]:
    if not ws.trail:
        return {"error": "nothing to undo: your plan has no campus"}, "Nothing to undo", "muted"
    t = ws.trail.pop()
    ws.sites.remove(t["j"])
    for i, before in t["prev"].items():
        if before is None:
            ws.raises.pop(i, None)
        else:
            ws.raises[i] = before
    ws._rooms = None
    out = {"removed": {"site_id": env.site_id(t["j"]), "town": env.area(t["j"])}, **_state_line(env, ws)}
    lo, hi = ws.cost()
    return out, f"Removed {env.area(t['j'])} and its {_plural(len(t['added']), 'upgrade', 'upgrades')}: plan back to {_m(hi)}", "info"


TOOLS = {
    "weak_points": t_weak_points,
    "sites": t_sites,
    "try_add": t_try_add,
    "raise_cost": t_raise_cost,
    "current_plan": t_current_plan,
    "undo_last": t_undo_last,
}


# ---------------------------------------------------------------------------------- the referee (code)
def referee(env: Env, sites: list[int], raises: dict[int, float], sized_from: dict[int, float] | None = None) -> dict:
    """Re-run a plan from scratch: one exact solve with every campus and raise at every load level (no line past its
    rating that wasn't already over with no campus, nothing shed), priced by the engine; a plan that holds also runs the
    full cascade at every level (nothing may trip, nobody may lose power). `sized_from`: the ratings the plan's own
    campus-by-campus build already sized (the working plan, the engine's steps); any other raise that holds is sized by
    the engine's rule (the flow plus 10 %, 50 MVA steps), never smaller than asked, so trimming the margin can't win.
    Returns the verdict: status verified | over_budget | failed | empty, with the engine's reason."""
    t0 = time.perf_counter()
    g = env.g
    levels = dict(raises)
    rate = env.rate_of(levels)
    n = len(sites)
    out = {"campuses": n, "upgrades": len(levels), "holds": False, "calm": None, "over": [], "lost_mw": 0.0, "tripped": None, "sized": [], "busiest_pct": None}
    if not n:
        lo, hi = env.plan_cost(levels)
        out.update(status="empty", cost={"low": round(lo), "high": round(hi)}, within_budget=True, reason="no campus in the plan", ms=round((time.perf_counter() - t0) * 1000))
        return out
    states = []
    extras = []
    for k, (gk, _) in enumerate(env.levels):
        e = env.extra(sites, k)
        extras.append(e)
        states.append(gk.solve(gk.base.active, e, rate))
    flows = np.max(np.abs(np.vstack([s.flow for s in states])), axis=0)
    sized_from = sized_from or {}
    for i, L in list(levels.items()):
        if abs(sized_from.get(i, -1.0) - L) <= 0.05:
            continue  # sized when the plan's own campus went in
        if flows[i] <= L + 1e-6:
            s = env.size(i, flows[i])
            if s > L + 1e-6:
                levels[i] = s
                out["sized"].append({"branch_id": env.bid(i), "short": env.short(i), "asked_mva": L, "sized_mva": s})
    rate = env.rate_of(levels)
    over_mask = np.zeros(g.m, dtype=bool)
    for k, s in enumerate(states):
        over_mask |= (np.abs(s.flow) > rate + 1e-6) & env.fresh[k]
    over = []
    for i in np.flatnonzero(over_mask).tolist():
        over.append({"branch_id": env.bid(i), "short": env.short(i), "flow_mw": round(float(flows[i]), 1), "rating_mva": round(float(rate[i]), 1),
                     "pct": round(float(flows[i]) / float(rate[i]) * 100.0, 1)})
    over.sort(key=lambda o: -o["pct"])
    lost = max(float(s.lost_mw) for s in states)
    lo, hi = env.plan_cost(levels)
    busiest = max(float(np.max(np.where(s.active, np.abs(s.flow) / rate * 100.0, 0.0))) for s in states)
    out.update(over=over[:5], over_count=len(over), lost_mw=round(lost, 1), cost={"low": round(lo), "high": round(hi)}, upgrades=len(levels),
               busiest_pct=round(busiest, 1), levels={str(env.bid(i)): round(L, 1) for i, L in levels.items()})
    out["_levels"] = dict(levels)  # by branch index, for the plan view (popped before it is published)
    out["holds"] = not over and lost <= 0.5
    if out["holds"]:
        ups = {env.bid(i): float(L) for i, L in levels.items()}
        calm = True
        for k, (gk, _) in enumerate(env.levels):
            c = gk.cascade_case(extras[k], None, ups)
            ok = c["total_steps"] == 0 and unlock._hit(c) == 0 and c["lost_mw"] <= 0.5
            if not ok and out["tripped"] is None:
                first = next((s for s in c.get("steps") or [] if s.get("tripped")), None)
                if first:
                    out["tripped"] = env.short(g.br_index[int(first["tripped"][0])])
            calm = calm and ok
        out["calm"] = bool(calm)
    out["within_budget"] = hi <= env.budget + 0.5
    if not out["holds"]:
        out["status"] = "failed"
        if over:
            o = over[0]
            out["reason"] = f"the {o['short']} at {o['pct']:.0f} % of its rating" + (f" (+{len(over) - 1} more over)" if len(over) > 1 else "")
        else:
            out["reason"] = f"the plants can't supply it: {lost:,.0f} MW would be shed"
    elif not out["calm"]:
        out["status"] = "failed"
        out["reason"] = "holds in one solve, but the full cascade trips " + (f"the {out['tripped']}" if out["tripped"] else "a line")
    elif not out["within_budget"]:
        out["status"] = "over_budget"
        out["reason"] = f"holds, but costs {_m(hi)}, over the {_m(env.budget)} budget"
    else:
        out["status"] = "verified"
        out["reason"] = "holds: one solve with every campus and upgrade" + (" at both load levels" if len(env.levels) > 1 else "") + "; full cascade with every line in service: nothing trips"
    out["ms"] = round((time.perf_counter() - t0) * 1000)
    return out


def _plan_view(env: Env, sites: list[int], raises: dict[int, float], trail: list[dict] | None, by: str, name: str, lane_id: str) -> dict:
    """A plan as the page shows it: the placements in order, the upgrades as project records (both ends for the map)
    and, when it was built campus by campus, steps in capacity's shape (for the meter)."""
    lo, hi = env.plan_cost(raises)
    view = {
        "lane": lane_id,
        "by": by,
        "name": name,
        "mode": env.mode,
        "mw": env.mw,
        "campuses": len(sites),
        "cost": {"low": round(lo), "high": round(hi)},
        "upgrades": len(raises),
        "placements": [env.placement(j, k + 1) for k, j in enumerate(sites)],
        "projects": [env.project(i, L, None, by) for i, L in sorted(raises.items(), key=lambda kv: -env.price(kv[0], kv[1])[1])],
        "steps": None,
        "synthetic": True,
    }
    if trail and [t["j"] for t in trail] == list(sites):
        steps = []
        for k, t in enumerate(trail):
            steps.append({
                "n": k + 1,
                "site": {"id": env.site_id(t["j"]), "area": env.area(t["j"]), "lat": env._lat[t["j"]], "lon": env._lon[t["j"]]},
                "free": not t["added"],
                "cost": {"low": round(t.get("cost_low", 0.0)), "high": round(t["cost_high"])},
                "cum_cost": {"low": round(t.get("cum_low", 0.0)), "high": round(t["cum_high"])},
                "projects": [env.project(i, L, k + 1, by) for i, L in t["added"].items()],
            })
        view["steps"] = steps
    return view


# ---------------------------------------------------------------------------------- the agents (Gemini)
WHY_PARAM = {"type": "string", "description": "One short sentence for the reader: why this call. Use only numbers the tools returned."}
RAISE_ITEMS = {
    "type": "array",
    "items": {"type": "object", "properties": {"branch_id": {"type": "integer"}, "to_mva": {"type": "number"}}, "required": ["branch_id", "to_mva"]},
    "description": "Line or transformer ids with the FINAL rating you want (MVA); the engine rounds up to 50 MVA steps and caps at 5x the original.",
}
TOOL_SPECS = {
    "weak_points": {
        "description": "The lines and transformers that stop the next campus with your plan so far: how many candidate sites each stops, its flow and rating, and the raise that would let the closest of those sites take a campus, with its price.",
        "parameters": {"type": "object", "properties": {}},
    },
    "sites": {
        "description": "Candidate sites (site ids) not in your plan, with the room each has left for a campus (MW before the first line reaches its rating, with your plan so far) and, for a site that doesn't fit, the lines a whole campus there would push over and the engine's estimated price of those raises. Sorted by room, by estimated cost, or by distance from near_site_id.",
        "parameters": {"type": "object", "properties": {
            "n": {"type": "integer", "description": f"How many sites (1 to {SITES_MAX}; default 10)."},
            "sort": {"type": "string", "enum": ["room", "cost"], "description": "room (default): the most room first; cost: the cheapest estimated next campus first (free sites first)."},
            "near_site_id": {"type": "integer", "description": "Optional: list the sites closest to this site id instead."}}},
    },
    "try_add": {
        "description": "The engine connects one campus at a site on top of your plan (an exact power-flow solve with every campus so far). By default (fix=true) the engine raises each line the campus pushes past its rating, by its own rule, and the campus joins your plan when the budget allows; you get the raises and their price. fix=false: only report the lines that go over and by how much. raises: your own raises to apply first (e.g. one bigger raise for a whole corridor). keep=false: test only (the price of connecting it); your plan doesn't change.",
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": {"type": "integer", "description": "A site id from sites()."},
                "fix": {"type": "boolean", "description": "true (default): the engine raises what goes over by its rule and connects the campus when the budget allows; false: only report what goes over."},
                "raises": RAISE_ITEMS,
                "keep": {"type": "boolean", "description": "false: only test (default true: connect when it holds)."},
            },
            "required": ["site_id"],
        },
    },
    "raise_cost": {
        "description": "The engine's price (high end of published cost figures) to raise one line or transformer from its original rating to a rating, and what it would add to your plan.",
        "parameters": {"type": "object", "properties": {"branch_id": {"type": "integer"}, "to_mva": {"type": "number"}}, "required": ["branch_id", "to_mva"]},
    },
    "current_plan": {"description": "Your plan so far: its campuses in order, its upgrades, its cost and the budget left.", "parameters": {"type": "object", "properties": {}}},
    "undo_last": {"description": "Remove your last campus and the raises it added.", "parameters": {"type": "object", "properties": {}}},
    "submit_plan": {
        "description": "End your run: submit your plan to the referee, which re-solves every campus and upgrade at once, prices it and runs the full cascade. Leave sites and raises out to submit your working plan exactly as the engine connected it (recommended); if you list them, the referee checks exactly what you list.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "One plain sentence: what your plan does and why (no cost figures: the engine prices it)."},
                "sites": {"type": "array", "items": {"type": "integer"}, "description": "Optional: every campus site id, in order."},
                "raises": RAISE_ITEMS,
            },
            "required": ["reason"],
        },
    },
}


def _declaration(name: str, spec: dict) -> dict:
    params = copy.deepcopy(spec["parameters"])
    params["properties"] = {**params.get("properties", {}), "why": WHY_PARAM}
    params["required"] = [*params.get("required", []), "why"]
    return {"name": name, "description": spec["description"], "parameters": params}


FUNCTION_DECLARATIONS = [_declaration(n, s) for n, s in TOOL_SPECS.items()]
OFFLINE = {"__offline__": True}


def _surface() -> str:
    """This feature's SURFACES id once llm.SURFACES lists it; until then the Strengthen surface the panel shows."""
    return SURFACE if any(s.get("id") == SURFACE for s in getattr(llm, "SURFACES", [])) else SURFACE_UNTIL


def _fit(obj: dict, limit: int = RESULT_MAX_CHARS) -> dict:
    """A function response of at most about `limit` characters: the longest list is cut from its end until it fits."""
    out = copy.deepcopy(obj)
    for _ in range(400):
        if len(json.dumps(out, separators=(",", ":"), default=str)) <= limit:
            return out
        lists = [(k, v) for k, v in out.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            break
        k, v = max(lists, key=lambda kv: len(json.dumps(kv[1], default=str)))
        out[k] = v[:-1]
    return out


_BLAME = re.compile(r"will cause|will black|is to blame|\bblame|illegal|fraud|negligen|failed to|should have", re.I)
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _number_known(v: float, known: set[float]) -> bool:
    if float(v).is_integer() and v <= 12:
        return True  # small counts ("two campuses")
    for a in known:
        if abs(a - v) <= max(0.051, 0.005 * abs(a)):
            return True
        for scale in (1e3, 1e6, 1e9):
            if abs(a) >= scale and abs(a - v * scale) <= 0.01 * abs(a):
                return True
    return False


class Agent:
    """One planner's run: its working plan, its live trace (AgentTrace rows), the numbers its notes may use."""

    def __init__(self, env: Env, lane: dict, strat: dict):
        self.env, self.lane, self.strat = env, lane, strat
        self.ws = Workspace(env)
        self.calls = 0  # Gemini turns
        self.tools = 0  # engine tool calls run
        self.fcalls = 0  # function calls Gemini returned
        self.retries = 0
        self.model = llm.AGENT_MODEL
        self.cache_keys: list[str] = []
        self.numbers: set[float] = set()
        self.t0 = time.perf_counter()

    def add(self, **row) -> dict:
        tr = self.lane["trace"]
        row["n"] = len(tr) + 1
        tr.append(row)
        return row

    def left(self) -> float:
        return AGENT_S - (time.perf_counter() - self.t0)

    def learn(self, obj) -> None:
        if isinstance(obj, bool) or obj is None:
            return
        if isinstance(obj, (int, float)):
            if math.isfinite(float(obj)):
                self.numbers.add(float(obj))
            return
        if isinstance(obj, dict):
            for v in obj.values():
                self.learn(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                self.learn(v)

    def note(self, raw) -> str | None:
        """The agent's "why", shown only when it names no real company, blames no one and every number in it is one a
        tool returned (or the prompt gave)."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        t = re.sub(r"[*`#>]+", "", " ".join(raw.split())).strip()
        if _BLAME.search(t):
            llm.note_check(_surface(), False, "a note with blame wording", None)
            return None
        t = llm.scrub_names(t, strict=False)
        if "[name]" in t:
            llm.note_check(_surface(), False, "a note naming a real company or utility", None)
            return None
        for tok in _NUM.findall(t):
            try:
                v = float(tok.replace(",", ""))
            except ValueError:
                continue
            if not _number_known(v, self.numbers):
                llm.note_check(_surface(), False, "a number in a planner's note that no tool returned", tok)
                return None
        if len(t) > NOTE_MAX:
            t = t[:NOTE_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        return t

    def working(self) -> None:
        lo, hi = self.ws.cost()
        self.lane["working"] = {"campuses": len(self.ws.sites), "cost_high": round(hi), "upgrades": len(self.ws.raises),
                                "towns": [self.env.area(j) for j in self.ws.sites]}


def _call_text(name: str, args: dict) -> str:
    parts = []
    for k, v in args.items():
        if isinstance(v, list):
            s = ",".join(json.dumps(x, separators=(",", ":")) if isinstance(x, dict) else str(x) for x in v[:4]) + (f",… {len(v)} in all" if len(v) > 4 else "")
            parts.append(f"{k}=[{s}]")
        else:
            parts.append(f"{k}={json.dumps(v)}")
    return f"{name}({', '.join(parts)})"[:160]


def _parse_raises(env: Env, raw, limit: int) -> tuple[dict[int, float] | None, str | None]:
    if raw is None:
        return {}, None
    if not isinstance(raw, list):
        return None, "raises must be a list of {branch_id, to_mva}"
    out: dict[int, float] = {}
    for u in raw[: limit * 2]:
        try:
            bid, to = int(u["branch_id"]), float(u["to_mva"])
        except (KeyError, TypeError, ValueError):
            return None, "each raise needs branch_id and to_mva"
        if bid not in env.g.br_index:
            return None, f"line {bid} is not in the model"
        if not math.isfinite(to) or to <= 0:
            return None, f"line {bid}: to_mva must be a positive number"
        i = env.g.br_index[bid]
        if to <= float(env.g.rate[i]) + 1e-6:
            continue  # not a raise
        out[i] = max(out.get(i, 0.0), to)
        if len(out) > limit:
            return None, f"at most {limit} raises at once"
    return out, None


def _int(x) -> int | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return int(v) if math.isfinite(v) and v == int(v) else None


def _clean(ag: Agent, name: str, raw: dict) -> tuple[dict | None, str | None]:
    """A function call's arguments, validated like a route's input."""
    env, ws = ag.env, ag.ws
    if name in ("weak_points", "current_plan", "undo_last"):
        return {}, None
    if name == "sites":
        a: dict = {}
        if raw.get("n") is not None:
            n = _int(raw["n"])
            if n is None:
                return None, "n must be a whole number"
            a["n"] = min(SITES_MAX, max(1, n))
        if raw.get("near_site_id") is not None:
            s = _int(raw["near_site_id"])
            if s is None or s not in env.ids:
                return None, "near_site_id must be a site id from sites()"
            a["near_site_id"] = s
        if raw.get("sort") is not None:
            if raw["sort"] not in ("room", "cost"):
                return None, "sort must be room or cost"
            a["sort"] = raw["sort"]
        return a, None
    if name == "try_add":
        s = _int(raw.get("site_id"))
        if s is None or s not in env.ids:
            return None, "site_id must be a candidate site id from sites()"
        j = env.ids[s]
        if j in ws.sites:
            return None, f"site {s} is already in your plan"
        if len(ws.sites) >= env.plan_max and raw.get("keep", True) is not False:
            return None, f"your plan has {env.plan_max} campuses, the most a plan may have: submit it"
        raises, why = _parse_raises(env, raw.get("raises"), RAISES_MAX)
        if raises is None:
            return None, why
        return {"j": j, "site_id": s, "fix": raw.get("fix", True) is not False, "keep": raw.get("keep", True) is not False, "raises": raises}, None
    if name == "raise_cost":
        bid = _int(raw.get("branch_id"))
        if bid is None or bid not in env.g.br_index:
            return None, "branch_id must be a line or transformer id the tools gave you"
        try:
            to = float(raw.get("to_mva"))
        except (TypeError, ValueError):
            return None, "to_mva must be a number"
        if not math.isfinite(to) or to <= 0:
            return None, "to_mva must be a positive number"
        return {"i": env.g.br_index[bid], "branch_id": bid, "to_mva": to}, None
    return None, "not one of the tools"


def _call_title(ag: Agent, name: str, a: dict) -> str:
    env = ag.env
    if name == "weak_points":
        return "Asks for the weak points"
    if name == "sites":
        n = a.get("n", 10)
        if a.get("near_site_id") is not None:
            return f"Lists {n} sites near {env.area(env.ids[a['near_site_id']])}"
        if a.get("sort") == "cost":
            return f"Lists the {n} cheapest sites for the next campus"
        return f"Lists the {n} sites with the most room"
    if name == "try_add":
        town = env.area(a["j"])
        extra = []
        if a["raises"]:
            extra.append(f"with {_plural(len(a['raises']), 'raise', 'raises')} of its own")
        if not a["fix"]:
            extra.append("without upgrades")
        head = f"Prices a campus at {town}" if not a["keep"] else f"Tries {town}"
        return head + (f", {' and '.join(extra)}" if extra else "")
    if name == "raise_cost":
        return f"Prices the {env.short(a['i'])} at {a['to_mva']:,.0f} MVA"
    if name == "current_plan":
        return "Checks its plan"
    if name == "undo_last":
        return "Undoes its last campus"
    return name


def _locked(fn, *args):
    with _engine_lock:
        out = fn(*args)
    return out if isinstance(out, Env) else _plain(out)


async def _answer(ag: Agent, call: dict, must_submit: bool) -> dict:
    """One Gemini function call (not submit_plan), validated and run on the engine: the function response."""
    name, cid = call["name"], call.get("id")
    raw = dict(call.get("args") or {})
    why_raw = raw.pop("why", None)
    if name not in TOOLS:
        llm.note_check(_surface(), False, "a function call to a tool that doesn't exist", name[:40])
        ag.add(actor="engine", kind="refused", tone="muted", call=_call_text(name[:40], raw), title=f"Refused {name[:40]}: not one of the tools")
        return {"error": "not one of the tools"}
    if must_submit or ag.tools >= AGENT_TOOL_CALLS:
        why = "its tool calls are used" if ag.tools >= AGENT_TOOL_CALLS else "its plan is due now"
        ag.add(actor="engine", kind="refused", tone="muted", call=_call_text(name, raw), title=f"Not run: {why}")
        return {"error": f"not run: {why}; call submit_plan"}
    a, why = _clean(ag, name, raw)
    if a is None:
        llm.note_check(_surface(), False, f"a function call's arguments: {why}", name)
        ag.add(actor="engine", kind="refused", tone="muted", call=_call_text(name, raw), title=f"Refused {name}: {why}")
        return {"error": f"refused: {why}"}
    ag.tools += 1
    shown = {k: v for k, v in raw.items() if k != "why"}
    ag.add(actor="gemini", kind="call", tone="info", tool=name, call=_call_text(name, shown), title=_call_title(ag, name, a), detail=ag.note(why_raw))
    t = time.perf_counter()
    try:
        res, summary, tone = await run_in_threadpool(_locked, TOOLS[name], ag.env, ag.ws, a)
    except Exception:  # noqa: BLE001 - a tool that fails tells the agent so; the race goes on
        log.exception("plan race: tool %s failed", name)
        ag.add(actor="engine", kind="result", tone="muted", tool=name, title="The engine could not run this call")
        return {"error": "the engine could not run this call"}
    ag.working()
    w = ag.lane["working"]
    ag.add(actor="engine", kind="result", tone=tone, tool=name, ms=round((time.perf_counter() - t) * 1000), title=summary[:1].upper() + summary[1:],
           state={"campuses": w["campuses"], "cost_high": w["cost_high"], "upgrades": w["upgrades"]})
    ag.learn(res)
    return res


def _prompt(env: Env, strat: dict) -> str:
    k = env.knee
    firm = env.mode == "firm"
    mw = f"{env.mw:,.0f} MW"
    if firm:
        goal = (f"GOAL: connect as many ALWAYS-ON {mw} data-center campuses AT ONCE as you can: every campus connected together, no line or "
                "transformer past its rating, no load the plants can't supply.")
    else:
        share = env.levels[0][1]
        goal = (f"GOAL: connect as many FLEXIBLE {mw} data-center campuses AT ONCE as you can. Each runs at full size whenever the state's load is at or "
                f"below {capacity.FLEX_OFF * 100:.0f} % of this afternoon peak and at {share * 100:.0f} % of its size at the peak; every campus must fit at both "
                "levels (the tools check both): no line or transformer past its rating, no load the plants can't supply.")
    n_bar = _plural(k["campuses"], "campus", "campuses")
    why = (f"WHY THIS BUDGET: it is the knee of the engine's cost curve: its first {n_bar} cost {_m(env.budget)} ({k['today']} fit with no "
           f"upgrade); its {_ordinal(k['next']['n'])} alone would cost {_m(k['next']['cost_high'])}, more than {KNEE_JUMP:g}x the {_m(k['avg'])} it paid per upgraded campus so far."
           if k.get("next") else f"WHY THIS BUDGET: it is the engine's whole plan ({n_bar}); its cost per campus never jumps.")
    return "\n".join([
        f"A SYNTHETIC grid model of {env.region_name} (Breakthrough Energy / Texas A&M), not any real utility's network. Three Gemini planners and the "
        "engine's own greedy search race to decide what needs fixing so more data centers can connect.",
        goal,
        f"BUDGET: {_m(env.budget)} of upgrades in all (the engine prices every raise at the high end of published cost figures; your own numbers are not used).",
        why,
        f"THE BAR: the engine connects {n_bar} at once for {_m(env.budget)}. Beat it: more campuses within the budget, or the same number for less.",
        f"YOUR STRATEGY, {strat['name'].upper()}: {strat['prompt'][env.mode]}",
        "TOOLS: sites(n, sort, near_site_id) lists candidate sites with the room each has left (and an estimated price when it doesn't fit); weak_points() "
        "lists the lines and transformers that stop the next campus; try_add(site_id, fix, raises, keep) connects one campus (an exact solve; by default "
        "the engine raises what goes over and tells you the price; keep=false only tests); "
        "raise_cost(branch_id, to_mva) prices a raise; current_plan(); undo_last(); submit_plan(reason) ends your run: the referee re-solves your whole "
        "plan at once, prices it and runs the full cascade.",
        "PRICE RULES (the engine's own, from published figures): a line costs the same for any rating up to 2x its original (a rebuild) and "
        "more above that (a new double-circuit line); a transformer costs per MVA of its new rating. A raise already in your plan is paid for: "
        "raising it again costs only the difference.",
        f"RULES: at most {AGENT_TOOL_CALLS} tool calls before submit_plan (several in one turn are fine); at most {env.plan_max} campuses; use only ids the tools "
        "give you; give every call a short 'why' (one sentence the reader sees; only numbers the tools gave you). Start with sites or weak_points.",
    ])


async def _submit(ag: Agent, call: dict | None, auto: str | None = None) -> dict:
    """submit_plan (or, `auto`, the working plan when the run ended without one): the referee's verdict."""
    env, ws, lane = ag.env, ag.ws, ag.lane
    raw = dict((call or {}).get("args") or {})
    reason = ag.note(raw.pop("reason", None)) if call else None
    ag.note(raw.pop("why", None))
    sites, raises, trail, sized_from = list(ws.sites), dict(ws.raises), list(ws.trail), dict(ws.raises)
    explicit = False
    if call and (raw.get("sites") is not None or raw.get("raises") is not None):
        bad = None
        if raw.get("sites") is not None:
            xs = raw["sites"] if isinstance(raw["sites"], list) else None
            if xs is None:
                bad = "sites must be a list of site ids"
            else:
                out = []
                for x in xs:
                    s = _int(x)
                    if s is None or s not in env.ids:
                        bad = f"site {x} is not a candidate site"
                        break
                    if env.ids[s] not in out:
                        out.append(env.ids[s])
                if bad is None and len(out) > env.plan_max:
                    bad = f"at most {env.plan_max} campuses"
                if bad is None:
                    sites = out
        if bad is None and raw.get("raises") is not None:
            rs, why = _parse_raises(env, raw["raises"], SUBMIT_RAISES_MAX)
            if rs is None:
                bad = why
            else:
                raises = {i: env.snap(i, to)[0] for i, to in rs.items()}
        if bad is not None:
            llm.note_check(_surface(), False, f"a submitted plan: {bad}", None)
            ag.add(actor="gemini", kind="propose", tone="info", call=_call_text("submit_plan", raw), title="Submits a plan", detail=reason)
            ag.add(actor="engine", kind="verify", tone="over", title=f"Referee: not a plan it can run ({bad})")
            lane["verdict"] = {"status": "failed", "reason": bad, "holds": False, "campuses": 0, "cost": {"low": 0, "high": 0}, "upgrades": 0}
            lane["plan"] = None
            return {"verdict": "rejected", "reason": bad}
        explicit = (sites != list(ws.sites)) or (raises != dict(ws.raises))
        if explicit:
            trail = None
    lo, hi = env.plan_cost(raises)
    title = f"Submits {_plural(len(sites), 'campus', 'campuses')} at once, {_plural(len(raises), 'upgrade', 'upgrades')}"
    if auto:
        ag.add(actor="engine", kind="propose", tone="muted", title=f"{auto}: the referee judges its working plan ({_plural(len(sites), 'campus', 'campuses')}, {_plural(len(raises), 'upgrade', 'upgrades')})")
    else:
        ag.add(actor="gemini", kind="propose", tone="info", call=_call_text("submit_plan", {k: v for k, v in raw.items() if k in ("sites", "raises")}), title=title + (" (its own list)" if explicit else ""), detail=reason)
    lane["status"] = "judging"
    v = await run_in_threadpool(_locked, referee, env, sites, raises, sized_from)
    final = v.pop("_levels", None) or raises
    lane["plan"] = _plan_view(env, sites, final, trail if final == raises else None, "gemini", ag.strat["name"], ag.strat["id"]) if sites else None
    lane["verdict"] = v
    lane["reason"] = reason
    lane["auto_submitted"] = bool(auto)
    ok = v["status"] == "verified"
    llm.note_check(_surface(), ok, "a planner's plan the referee re-solved: " + ({"verified": "verified", "over_budget": "it holds but costs more than the budget", "empty": "no campus", "failed": v.get("reason", "failed")}.get(v["status"], v["status"])),
                   f"{v['over'][0]['pct']:g}%" if v.get("over") else None)
    _verdict_row(ag.add, env, v)
    return {"verdict": v["status"], "reason": v.get("reason"), "campuses": v["campuses"], "cost_m": _money_m(v["cost"]["high"]), "budget_m": _money_m(env.budget)}


def _verdict_row(add, env: Env, v: dict) -> None:
    n = v["campuses"]
    state = {"campuses": n, "cost_high": int((v.get("cost") or {}).get("high") or 0), "upgrades": int(v.get("upgrades") or 0), "verdict": v["status"]}
    base = add

    def add(**row):  # noqa: F811 - every verdict row carries the judged plan's state
        return base(**row, state=state)
    price = f"Priced by the engine: {_m(v['cost']['high'])} (high end)."
    if v.get("sized"):
        price += " Sized up by the engine's 10 % margin rule: " + "; ".join(f"{x['short']} {x['asked_mva']:,.0f} to {x['sized_mva']:,.0f} MVA" for x in v["sized"][:2]) + "."
    if v["status"] == "empty":
        add(actor="engine", kind="verify", tone="muted", ms=v.get("ms"), title="Referee: no campus to judge")
    elif v["status"] == "verified":
        add(actor="engine", kind="verify", tone="holds", holds=True, ms=v.get("ms"),
            title=f"Referee: verified, {_plural(n, 'campus', 'campuses')} at once for {_m(v['cost']['high'])}",
            detail=f"One solve with every campus and upgrade{' at both load levels' if len(env.levels) > 1 else ''}; the full cascade with every line in service: nothing trips. Busiest line {v['busiest_pct']:.0f} %. {price}")
    elif v["status"] == "over_budget":
        add(actor="engine", kind="verify", tone="muted", holds=True, ms=v.get("ms"),
            title=f"Referee: holds, but over the budget ({_m(v['cost']['high'])} of {_m(env.budget)})", detail=price)
    else:
        add(actor="engine", kind="verify", tone="over", holds=False, over=v.get("over"), ms=v.get("ms"),
            title=f"Referee: failed, {v.get('reason')}", detail=price)


async def _agent(ag: Agent) -> None:
    """One Gemini planner's function-calling loop; always ends with its lane judged (or offline)."""
    env, lane = ag.env, ag.lane
    lane["status"] = "working"
    prompt = _prompt(env, ag.strat)
    ag.learn([env.budget / 1e6, env.knee["campuses"], env.knee["today"], env.mw, (env.knee.get("next") or {}).get("cost_high", 0) / 1e6, env.knee["avg"] / 1e6, KNEE_JUMP])
    contents: list[dict] = [{"role": "user", "parts": [{"text": prompt}]}]
    submitted = False
    status = "out_of_turns"
    nudge = None
    while ag.calls < AGENT_TURNS:
        left = ag.left()
        if left < 3:
            status = "slow"
            break
        must = ag.tools >= AGENT_TOOL_CALLS or ag.calls >= AGENT_TURNS - 1 or left < 14
        if nudge:
            contents.append({"role": "user", "parts": [{"text": nudge}]})
            nudge = None
        try:
            reply, offline = await asyncio.wait_for(
                llm.complete_tools(contents, FUNCTION_DECLARATIONS, system=SYSTEM, fallback=OFFLINE, timeout=min(TURN_TIMEOUT_S, left), surface=_surface(),
                                   model=ag.model, thinking=llm.AGENT_THINKING, tool_mode="ANY", allowed=["submit_plan"] if must else None),
                left,
            )
        except asyncio.TimeoutError:
            status = "slow"
            break
        ag.calls += 1
        if offline or not isinstance(reply, dict) or reply.get("__offline__"):
            if llm.configured() and ag.retries < RETRIES and ag.left() > 12:
                ag.retries += 1
                ag.add(actor="engine", kind="retry", tone="muted", title="Gemini didn't answer in time: asked again")
                continue
            status = "unavailable"
            break
        ag.model = reply.get("model") or ag.model
        if reply.get("cache_key"):
            ag.cache_keys.append(reply["cache_key"])
        contents.append(reply["content"])
        calls = reply.get("calls") or []
        if not calls:
            nudge = "Reply with function calls only: use the tools, then call submit_plan."
            continue
        ag.fcalls += len(calls)
        parts = []
        for call in calls:
            if submitted:
                parts.append(llm.function_response(call, {"error": "the run has ended"}))
                continue
            if call["name"] == "submit_plan":
                res = await _submit(ag, call)
                parts.append(llm.function_response(call, res))
                submitted = True
                status = "submitted"
                continue
            res = await _answer(ag, call, must)
            parts.append(llm.function_response(call, _fit(res)))
        if submitted:
            break
        contents.append({"role": "user", "parts": parts})
    if not submitted:
        if status == "unavailable" and not ag.ws.sites and not ag.tools:
            lane["status"] = "offline"
            lane["why"] = "Gemini unavailable" if llm.configured() else "Gemini isn't set up on this server"
            ag.add(actor="engine", kind="offline", tone="muted", title=f"{lane['why']}: this planner didn't run")
        else:
            why = {"slow": "Its time ran out", "unavailable": "Gemini stopped answering", "out_of_turns": "Its turns ran out"}.get(status, "Its run ended")
            await _submit(ag, None, auto=why)
    if (lane.get("verdict") or {}).get("status") not in (None, "verified"):
        llm.cache_forget(ag.cache_keys)  # a run with no verified plan: the next race asks afresh
    lane["calls"] = ag.calls
    lane["tool_calls"] = ag.tools
    lane["function_calls"] = ag.fcalls
    lane["model"] = ag.model if ag.calls else None
    lane["ms"] = round((time.perf_counter() - ag.t0) * 1000)
    if lane["status"] != "offline":
        lane["status"] = "done"


# ---------------------------------------------------------------------------------- the race
def _lane(lid: str, name: str, short: str, by: str) -> dict:
    return {"id": lid, "name": name, "strategy": short, "by": by, "status": "waiting", "trace": [], "working": None, "plan": None, "verdict": None,
            "reason": None, "why": None, "auto_submitted": False, "calls": 0, "tool_calls": 0, "function_calls": 0, "model": None, "ms": None}


def _engine_lane(env: Env, lane: dict) -> None:
    """The engine's competitor: its own greedy plan cut at the knee, judged by the same referee."""
    k = env.knee
    add = lambda **row: (row.update(n=len(lane["trace"]) + 1), lane["trace"].append(row))  # noqa: E731
    sites, raises, trail = env.engine_plan()
    add(actor="engine", kind="plan", tone="info",
        title=f"Its greedy search: each campus where it fits; when none fits, the cheapest upgrades for the next one ({capacity.TRY} sites tried each time)")
    add(actor="engine", kind="propose", tone="info",
        title=f"Its plan cut at the knee: {_plural(len(sites), 'campus', 'campuses')} at once, {_plural(len(raises), 'upgrade', 'upgrades')}",
        detail=k.get("sentence"), state={"campuses": len(sites), "cost_high": round(env.plan_cost(raises)[1]), "upgrades": len(raises)})
    lane["working"] = {"campuses": len(sites), "cost_high": round(env.plan_cost(raises)[1]), "upgrades": len(raises), "towns": [env.area(j) for j in sites]}
    v = referee(env, sites, raises, sized_from=dict(raises))
    v.pop("_levels", None)
    lane["plan"] = _plan_view(env, sites, raises, trail, "engine", "The engine", "engine")
    lane["verdict"] = v
    _verdict_row(add, env, v)
    lane["status"] = "done"


def _tie(a: dict, b: dict, tol: float) -> bool:
    """Two verified plans the ranking can't tell apart: the same campuses, the cost within the margin (the same test as
    "matched": a plan that matches the engine's this way doesn't beat it, so it can't rank below it either)."""
    return a["campuses"] == b["campuses"] and abs(a["cost_high"] - b["cost_high"]) <= tol


def _rank(env: Env, lanes: list[dict]) -> dict:
    """The leaderboard: verified plans by most campuses, then cheapest, then fewest upgrades; an AI plan wins only by
    beating the engine's (more campuses, or the same for more than the rounding margin less). Plans the ranking can't
    tell apart (_tie: the same campuses, the cost within the margin) share a place, "tied": three planners that all
    reach the engine's 7 for $36.6M are all 1st with the engine's plan standing; the next place skips past them."""
    rows = []
    for order, ln in enumerate(lanes):
        v = ln.get("verdict") or {}
        p = ln.get("plan") or {}
        rows.append({
            "lane": ln["id"], "name": ln["name"], "by": ln["by"], "status": v.get("status") or ln["status"], "verified": v.get("status") == "verified",
            "campuses": int(v.get("campuses") or 0), "cost_high": int((v.get("cost") or {}).get("high") or 0), "upgrades": int(v.get("upgrades") or 0),
            "reason": v.get("reason") or ln.get("why"), "order": order, "place": None, "tied": False, "outcome": None,
            "auto_submitted": ln.get("auto_submitted", False), "busiest_pct": v.get("busiest_pct"), "has_plan": bool(p.get("campuses")),
        })
    eng = next((r for r in rows if r["by"] == "engine"), None)
    ok = sorted((r for r in rows if r["verified"]), key=lambda r: (-r["campuses"], r["cost_high"], r["upgrades"], r["order"]))
    for place, r in enumerate(ok, 1):
        r["place"] = place
    winner = ok[0] if ok else None
    for r in rows:
        if r["by"] == "engine":
            r["outcome"] = "bar" if r["verified"] else r["status"]
            continue
        if not r["verified"]:
            r["outcome"] = r["status"] if r["status"] in ("failed", "over_budget", "empty", "offline") else "no_plan"
            continue
        if eng is None or not eng["verified"]:
            r["outcome"] = "beat"
            continue
        tol = max(TOL_SHARE * eng["cost_high"], TOL_MIN)
        if r["campuses"] > eng["campuses"] or (r["campuses"] == eng["campuses"] and r["cost_high"] < eng["cost_high"] - tol):
            r["outcome"] = "beat"
        elif r["campuses"] == eng["campuses"] and abs(r["cost_high"] - eng["cost_high"]) <= tol:
            r["outcome"] = "matched"
        else:
            r["outcome"] = "behind"
    if winner is not None and winner["by"] != "engine" and winner["outcome"] != "beat" and eng is not None and eng["verified"]:
        winner = eng  # a tie within the margin: an AI plan must beat the engine's to win
    # the places follow the winner rule: the winner first, then the rest in their order; plans the ranking can't tell
    # apart share a place (competition ranking: 1, 1, 1, 4)
    if winner is not None:
        tol = max(TOL_SHARE * (eng["cost_high"] if eng is not None and eng["verified"] else winner["cost_high"]), TOL_MIN)
        rest = sorted((r for r in ok if r is not winner), key=lambda r: (not _tie(r, winner, tol), -r["campuses"], r["cost_high"], r["upgrades"], r["order"]))
        lead = None  # the first plan of the current group of ties
        for place, r in enumerate([winner] + rest, 1):
            if lead is not None and _tie(r, lead, tol):
                r["place"] = lead["place"]
                r["tied"] = lead["tied"] = True
            else:
                r["place"] = place
                lead = r
    ranked = sorted(rows, key=lambda r: (r["place"] is None, r["place"] or 0, r is not winner, r["order"]))
    return {"rows": [{k: v for k, v in r.items() if k != "order"} for r in ranked], "winner": winner["lane"] if winner else None, "engine": eng}


def _sentence(env: Env, board: dict, lanes: list[dict], fallback: str | None) -> str:
    eng = board["engine"]
    rows = {r["lane"]: r for r in board["rows"]}
    w = rows.get(board["winner"]) if board["winner"] else None
    base = f"{_plural(eng['campuses'], 'campus', 'campuses')} at once for {_m(eng['cost_high'])}" if eng else ""
    if fallback:
        return f"{fallback}: the engine's plan alone, {base}, checked by the referee."
    if w is None:
        return "No plan held when the referee re-ran it."
    if w["by"] != "engine":
        more = w["campuses"] - eng["campuses"] if eng else 0
        how = (f"{_plural(more, 'more campus', 'more campuses')} within the same budget" if more > 0
               else f"the same {eng['campuses']} for {_m(eng['cost_high'] - w['cost_high'])} less")
        return f"Gemini's {w['name']} planner wins: {_plural(w['campuses'], 'campus', 'campuses')} at once for {_m(w['cost_high'])}, {how}, verified by the referee (the engine's own plan: {base})."
    gem = [r for r in board["rows"] if r["by"] == "gemini"]
    best = next((r for r in gem if r["verified"]), None)
    if best is None:
        tail = "no Gemini planner's plan held" if any(r["status"] in ("failed", "over_budget") for r in gem) else "no Gemini planner submitted a plan that held"
        return f"The engine's plan stands: {base}; {tail}."
    if best["outcome"] == "matched":
        names = [r["name"] for r in gem if r["outcome"] == "matched"]
        got = f"{_plural(best['campuses'], 'campus', 'campuses')} for {_m(best['cost_high'])}"
        if len(names) == 1:
            return f"The engine's plan stands: {base}. Gemini's {names[0]} matched it ({got}) but didn't beat it."
        who = "All three Gemini planners" if len(names) == 3 else "Gemini's " + " and ".join(names)
        return f"The engine's plan stands: {base}. {who} matched it ({got} each), but none beat it."
    names = [r["name"] for r in gem if r["verified"] and r["place"] == best["place"]]
    if len(names) > 1:
        return f"The engine's plan stands: {base}. Best Gemini planners, tied: {' and '.join(names)}, {_plural(best['campuses'], 'campus', 'campuses')} for {_m(best['cost_high'])} each."
    return f"The engine's plan stands: {base}. Best Gemini planner: {best['name']}, {_plural(best['campuses'], 'campus', 'campuses')} for {_m(best['cost_high'])}."


class _Race:
    def __init__(self, key: tuple, mode: str, ai: bool):
        self.id = secrets.token_urlsafe(12)
        self.key, self.mode, self.ai = key, mode, ai
        self.status = "running"
        self.created = time.monotonic()
        self.t0 = time.perf_counter()
        self.knee: dict | None = None
        self.lanes: list[dict] = [_lane("engine", "The engine", "Its own greedy plan, cut at the knee", "engine")] + [
            _lane(s["id"], s["name"], s["short"], "gemini") for s in STRATEGIES
        ]
        self.result: dict | None = None
        self.error: str | None = None
        self.task = None


_jobs: "OrderedDict[str, _Race]" = OrderedDict()
_running: dict[tuple, str] = {}  # (key, mode, ai) -> the job computing it
_done: "OrderedDict[tuple, tuple[float, float, dict]]" = OrderedDict()  # (key, mode, ai) -> (time, ttl, result)
_jobs_lock = threading.Lock()


def _ck(key: tuple, mode: str, ai: bool) -> tuple:
    return (key, mode, bool(ai and llm.configured()))


def _cached(ck: tuple) -> dict | None:
    hit = _done.get(ck)
    if hit is None:
        return None
    ts, ttl, res = hit
    if time.time() - ts > ttl:
        _done.pop(ck, None)
        return None
    _done.move_to_end(ck)
    return res


def _gc() -> None:
    now = time.monotonic()
    for jid in [j for j, r in _jobs.items() if r.status != "running" and now - r.created > JOB_TTL_S]:
        del _jobs[jid]
    while len(_jobs) > JOBS_MAX:
        old = next((j for j, r in _jobs.items() if r.status != "running"), None)
        if old is None:
            break
        del _jobs[old]


async def _run(job: _Race, res: dict) -> None:
    ck = _ck(job.key, job.mode, job.ai)
    try:
        env = await run_in_threadpool(_locked, Env, job.key, res, job.mode)
        job.knee = env.knee
        await run_in_threadpool(_locked, _engine_lane, env, job.lanes[0])
        fallback = None
        if not job.ai:
            fallback = "Gemini not asked (ai: false)"
        elif not llm.configured():
            fallback = "Gemini isn't set up on this server"
        agents = []
        for s, ln in zip(STRATEGIES, job.lanes[1:]):
            if fallback:
                ln["status"] = "offline"
                ln["why"] = fallback
                ln["trace"].append({"n": 1, "actor": "engine", "kind": "offline", "tone": "muted", "title": f"{fallback}: this planner didn't run"})
            else:
                agents.append(Agent(env, ln, s))
        if agents:
            try:
                await asyncio.wait_for(asyncio.gather(*[_agent(a) for a in agents]), RACE_HARD_S)
            except asyncio.TimeoutError:
                log.warning("plan race: hit the hard time limit; judging the working plans")
                for a in agents:
                    if a.lane.get("verdict") is None and a.lane["status"] != "offline":
                        await _submit(a, None, auto="The race's time ran out")
                        a.lane["status"] = "done"
            if all(a.lane["status"] == "offline" for a in agents):
                fallback = agents[0].lane.get("why") or "Gemini unavailable"
        board = _rank(env, job.lanes)
        result = {
            "region": env.code, "region_name": env.region_name, "mw": env.mw, "load_factor": env.lf, "mode": env.mode,
            "off_peak_load_factor": env.lf_off,
            "knee": env.knee,
            "plan_max": env.plan_max,
            "lanes": job.lanes,
            "leaderboard": board["rows"],
            "winner": board["winner"],
            "sentence": _sentence(env, board, job.lanes, fallback),
            "fallback": bool(fallback),
            "why": fallback,
            "rules": ("The referee re-solves every plan at once (every campus and upgrade" + (", at both load levels" if env.mode == "flexible" else "")
                      + "), prices it with the engine's own figures (high end; the planners' numbers are never used) and runs the full cascade with every line in "
                      "service: nothing may trip and nobody may lose power. Most campuses within the budget wins, then the cheapest, then the fewest upgrades; "
                      "an AI plan must beat the engine's to win, and plans with the same campuses and costs within 2 % of each other share a place."),
            "note": ("Every number describes the synthetic grid model (Breakthrough Energy / Texas A&M), not any real utility's network; costs are "
                     "estimates (high end)."),
            "model": llm.AGENT_MODEL if not fallback else None,
            "ms": round((time.perf_counter() - job.t0) * 1000),
            "synthetic": True,
        }
        job.result = result
        job.status = "done"
        ttl = FALLBACK_TTL_S if (fallback and job.ai and llm.configured()) else CACHE_TTL_S
        with _jobs_lock:
            _done[ck] = (time.time(), ttl, result)
            while len(_done) > CACHE_SIZE:
                _done.popitem(last=False)
        log.info("plan race %s %.0f MW %s: winner %s in %.1f s", env.code, env.mw, env.mode, board["winner"], result["ms"] / 1000)
    except HTTPException as e:
        job.error = e.detail if isinstance(e.detail, str) else "The race could not run"
        job.status = "error"
    except Exception:  # noqa: BLE001 - a background job must always end in a state the panel can show
        log.exception("plan race failed")
        job.error = "The race failed on this request. Try again."
        job.status = "error"
    finally:
        with _jobs_lock:
            if _running.get(ck) == job.id:
                _running.pop(ck, None)
        job.task = None


# ---------------------------------------------------------------------------------- routes
class RaceIn(BaseModel):
    region: str = DEFAULT_REGION
    mw: float = 1000.0
    load_factor: float = 1.0
    mode: str = "firm"
    ai: bool = True


def _study(region: str, mw: float, load_factor: float, mode: str) -> tuple[tuple, dict]:
    """The finished study for this key (never started here: LAZY) and its plan for `mode`; 409 when it isn't done."""
    if mode not in MODES:
        raise HTTPException(status_code=422, detail="mode must be firm or flexible")
    key = unlock._key_of(unlock.UnlockIn(region=region, mw=mw, load_factor=load_factor))
    with unlock._cache_lock:
        res = unlock._cache.get(key)
    if res is None:
        raise HTTPException(status_code=409, detail="Run the Strengthen study for this state, size and load first: the race starts from its finished plan.")
    if res.get("already_failing"):
        raise HTTPException(status_code=409, detail="At this load level the model's grid fails with no data center at all: there is nothing to race.")
    plan = (res.get("capacity") or {}).get(mode) or {}
    if not plan.get("steps"):
        raise HTTPException(status_code=409, detail="This study has no campuses-at-once plan to race for this size.")
    if not any(not st.get("free") for st in plan["steps"]):
        raise HTTPException(status_code=409, detail="The engine's plan needs no upgrade for this size: there is nothing to race over.")
    return key, res


def _view(job: _Race) -> dict:
    out = {"id": job.id, "status": job.status, "region": job.key[0], "mw": job.key[1], "load_factor": job.key[2], "mode": job.mode, "ai": job.ai,
           "knee": job.knee, "elapsed_s": round(time.perf_counter() - job.t0, 1)}
    if job.status == "done" and job.result is not None:
        out["result"] = job.result
        out["lanes"] = job.result["lanes"]
        return out
    lanes = []
    for ln in job.lanes:
        lanes.append({**{k: v for k, v in ln.items() if k != "trace"}, "trace": list(ln["trace"])})
    out["lanes"] = lanes
    if job.status == "error":
        out["error"] = job.error
    return out


@router.post("/api/strengthen/plan-race")
@limiter.limit("30/minute")
async def start_race(request: Request, body: RaceIn):
    """Start (or join, or return the finished) race for a finished study; poll GET /api/strengthen/plan-race/{id}."""
    key, res = _study(body.region, body.mw, body.load_factor, body.mode)
    ck = _ck(key, body.mode, body.ai)
    with _jobs_lock:
        _gc()
        hit = _cached(ck)
        if hit is not None:
            job = _Race(key, body.mode, body.ai)
            job.status, job.result, job.knee = "done", hit, hit.get("knee")
            _jobs[job.id] = job
            return {"id": job.id, "status": "done", "cached": True}
        jid = _running.get(ck)
        if jid and jid in _jobs and _jobs[jid].status == "running":
            return {"id": jid, "status": "running", "cached": False}
        if sum(1 for r in _jobs.values() if r.status == "running") >= RACES_RUNNING_MAX:
            raise HTTPException(status_code=429, detail="The engine is busy with other races. Try again in a minute.")
        job = _Race(key, body.mode, body.ai)
        _jobs[job.id] = job
        _running[ck] = job.id
    job.task = asyncio.create_task(_run(job, res))
    return {"id": job.id, "status": job.status, "cached": False}


@router.get("/api/strengthen/knee")
@limiter.limit("240/minute")
def knee(
    request: Request,
    region: str = Query(DEFAULT_REGION, max_length=8),
    mw: float = Query(1000.0),
    load_factor: float = Query(1.0),
    mode: str = Query("firm", max_length=10),
):
    """The budget bar of a finished study (knee_budget): 409 when the study isn't done (nothing is started)."""
    key, res = _study(region, mw, load_factor, mode)
    k = knee_budget(res["capacity"][mode])
    return {"region": key[0], "mw": key[1], "load_factor": key[2], "mode": mode, **k, "plan_max": plan_max_for(k)}


@router.get("/api/strengthen/plan-race/{race_id}")
@limiter.limit("240/minute")
async def race_status(request: Request, race_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_-]{8,40}$")):
    job = _jobs.get(race_id)
    if job is None:
        raise HTTPException(status_code=404, detail="This race is no longer available. Start it again.")
    return _view(job)
