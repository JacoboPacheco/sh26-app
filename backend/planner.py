"""The siting planner (owned by the planner track): give it a goal — "place 2,000 MW of AI campuses
in Texas without blacking anyone out" — and it answers with sites, sizes and any upgrades, every one
checked by the grid engine.

POST /api/planner {region, goal, total_mw, max_sites, load_factor, firm, use_ai, fresh} runs a whole
plan and returns it. POST /api/planner/start runs the same thing in the background and
GET /api/planner/jobs/{id} returns its steps so far, so the panel can show them as they happen (the
AI boom mode's "Let the AI place them" trace). Each step: {n, by: gemini|planner, tool, text, ok,
thought (Gemini's one-sentence reason), why (the built-in planner's rule), call {tool, args}, result
(the engine's answer alone), ms (engine time), ai_ms and call_n (Gemini's), sites, upgrade_lines?}.

The agent: Gemini (llm.complete_json with a JSON schema, fallback= and a 10 s timeout on every call,
at most AI_CALLS calls and AI_DEADLINE_S in all, JOB_DEADLINE_S in a job) picks one action at a time from headroom_top / whatif / fix /
cascade / finish. Each action runs on the real engine (grid.py, powerflow.py, fixit.py) and its
result is fed back. A "finish" is then checked the way /api/grid/whatif and /api/grid/cascade would
check the plan's case: no line over its limit (after the plan's upgrades, if any) and nobody
without power. Gemini not configured, unavailable, out of calls or time, or a plan the check
rejects → the deterministic planner makes the plan and the result says fallback: true.

The deterministic planner (greedy): rank towns by the MW their roomiest substation can take alone
(the linear headroom estimate); take the top `k` at least SPACING_KM apart; split the total in
proportion to their room; solve all of them together. When a line goes over, move the campus that
sends the most power over it to the next roomy site that sends little. When every site loads it
(typically a line next to the generators that pick up any new load), find the most the sites take
together without upgrades (bisection) and the smallest re-rating that carries the whole total
(fixit.greedy_fix, the same search as /api/fix). Then the check.

Public like the grid endpoints (no login, no database), 30 requests a minute per visitor. Plans
Gemini made are cached in memory by their request, so a replayed demo costs no quota. Every number
in a step comes from the engine; people without power are estimates (grid.people_fields)."""

import asyncio
import copy
import inspect
import json
import logging
import math
import re
import secrets
import threading
import time
import weakref
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator

from fixit import _towns_by_headroom, greedy_fix
from grid import (
    DEFAULT_REGION,
    MW_MAX,
    MW_MIN,
    REGIONS,
    CaseIn,
    SiteIn,
    _case_header,
    _km,
    case_firm_buses,
    check_case,
    grid_at,
    region_code,
)
import llm
from limiter import limiter
from llm import complete_json, configured
from powerflow import OVER_PCT, area_of

router = APIRouter(tags=["planner"])
log = logging.getLogger("uvicorn.error")

MAX_SITES = 6
SITE_MAX_MW = min(5000, MW_MAX)  # the planner's biggest campus (the engine allows extreme what-ifs; a plan doesn't)
TOTAL_MIN, TOTAL_MAX = 10, 30_000
GOAL_MAX = 300
MIN_CAMPUS_MW = 100  # the greedy doesn't split the total into campuses smaller than this
MIN_ROOM_MW = 50  # a substation with less room than this is not a candidate (unless nothing has more)
STEP_MW = 10  # sizes are multiples of 10 MW
SPACING_KM = 30  # campuses at least this far apart...
NEAR_SPACING_KM = 8  # ...or this far in a plan kept near one town
NEAR_KM = 80  # "near Dallas": within this distance of the town's center
AVOID_KM = 40  # "avoid Austin": nothing within this distance
WORSE_PCT = 1.0  # a line already over before any campus counts as new if a plan adds more than this
MAX_MOVES = 6
POOL_MAX = 120  # candidates kept per plan
SHOW_TOP = 10
BISECT_STEPS = 12
RERATE_BISECT_STEPS = 7  # each probe runs a whole re-rating search, so fewer (within ~1 % of the most)
AI_CALLS = 6  # Gemini calls per plan, a retry included (one user action stays small)
AI_CALL_TIMEOUT_S = 10.0  # one call; a call that hangs is asked again once (AI_RETRIES)
AI_RETRIES = 1
RATE_WAIT_S = 4.0  # before the retry of a call Google refused for its request limit (a 429; per-minute limits clear fast)
BUSY_WAIT_S = 2.0  # before the retry of a call Google answered with 503 (the model is overloaded)
AI_DEADLINE_S = 22.0  # all of Gemini's calls in POST /api/planner: the whole request stays under the smoke test's 30 s
JOB_DEADLINE_S = 40.0  # ...in a background job (the panel's live trace): room for a slower fallback model
AI_TOP_MAX = 15
HISTORY_MAX = 1500  # characters of one tool result fed back to Gemini (ten ranked sites fit whole)
JOBS_MAX = 64
JOB_TTL_S = 900
RUNNING_MAX = 6
CACHE_SIZE = 64
THOUGHT_MAX = 220


# ---------------------------------------------------------------------------------- helpers
def _pretty(name: str) -> str:
    """ "NORTH FORT MYERS 6" -> "North Fort Myers 6" (the frontend's prettyName)."""
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), str(name or "").lower())


def _n(x: float) -> str:
    """A whole number the way the panel rounds it (half up, like Math.round), with thousands commas."""
    return f"{math.floor(float(x) + 0.5):,}"


def _mw(x: float) -> str:
    return f"{_n(x)} MW"


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _pct(x: float) -> str:
    return f"{math.floor(float(x) + 0.5)} %"


def _poss(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


class Cand:
    """A candidate site: one substation, where a campus there connects, and its room (MW it can take
    alone before a line overloads — the linear estimate the heatmap shows)."""

    __slots__ = ("i", "sub", "name", "town", "lat", "lon", "bus", "room", "kv", "km")

    def __init__(self, g, i: int, room: float, town: str, ref: tuple[float, float] | None = None):
        self.i = int(i)
        self.sub = int(g.sub_ids[i])
        self.name = _pretty(g.sub_name[i])
        self.town = town
        # the coordinates the plan's case sends; a case snaps them to this same substation (see ok)
        self.lat = round(float(g.sub_lat[i]), 4)
        self.lon = round(float(g.sub_lon[i]), 4)
        self.bus = int(g.site_bus(self.lat, self.lon))
        self.room = float(min(room, 1e6))
        self.kv = float(g.bus_kv[self.bus])
        self.km = None if ref is None else round(_km(ref[0], ref[1], self.lat, self.lon), 1)

    def ok(self, g) -> bool:
        """A drop at these coordinates connects here (two substations can share a spot)."""
        return int(g.nearest_sub(self.lat, self.lon)) == self.i

    def out(self, mw: float | None = None) -> dict:
        d = {"sub": self.sub, "name": self.name, "town": self.town, "lat": self.lat, "lon": self.lon, "room_mw": round(self.room), "kv": self.kv}
        if mw is not None:
            d["mw"] = int(mw)
        if self.km is not None:
            d["km"] = self.km
        return d


_towns_memo: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_towns_lock = threading.Lock()


def _towns(g) -> list[tuple[float, int, str]]:
    """fixit's ranking (headroom, sub index, town), roomiest substation per town first, kept per
    Grid (the ranking costs a pass over every substation)."""
    with _towns_lock:
        hit = _towns_memo.get(g)
    if hit is None:
        hit = list(_towns_by_headroom(g))
        with _towns_lock:
            _towns_memo[g] = hit
    return hit


def _areas(g) -> dict[str, dict]:
    """lower-case area name -> {town, lat, lon}: the center of the substations named after it."""
    acc: dict[str, list] = {}
    for i, name in enumerate(g.sub_name):
        town = area_of(name)
        if not town:
            continue
        a = acc.setdefault(town.lower(), [town, 0.0, 0.0, 0])
        a[1] += float(g.sub_lat[i])
        a[2] += float(g.sub_lon[i])
        a[3] += 1
    return {k: {"town": t, "lat": round(la / n, 4), "lon": round(lo / n, 4)} for k, (t, la, lo, n) in acc.items()}


_AVOID_RE = re.compile(
    r"\b(?:avoid(?:ing)?|away from|not (?:in|near|around)|except(?: for)?|stay out of|keep (?:it|them|campuses|everything) (?:out of|away from))\s+(?:the\s+)?(?=([a-z][a-z .'\-]{1,48}))",
    re.I,
)
# the place is read in a lookahead, so a match ends before it: "in Texas near Dallas" still finds "near Dallas"
_NEAR_RE = re.compile(r"\b(?:near|around|close to|next to|by|in|at)\s+(?:the\s+)?(?:city of\s+)?(?=([a-z][a-z .'\-]{1,48}))", re.I)


def _town_at(text: str, areas: dict[str, dict]) -> dict | None:
    """The longest known area name the text starts with ("dallas without blacking" -> Dallas)."""
    words = re.findall(r"[a-z.'\-]+", text.lower())
    for n in (4, 3, 2, 1):
        if len(words) >= n:
            key = " ".join(words[:n]).strip(".'-")
            if key in areas:
                return areas[key]
    return None


def parse_goal(goal: str, g, state_name: str = "") -> tuple[dict | None, list[dict]]:
    """Places the goal names: ("near Dallas", ["avoid Austin", ...]), each {town, lat, lon}. Only towns
    the region's model has a substation named after count; anything else is left to Gemini.
    `state_name`: "in New York" names the state, not its town New York, so a place after "in"/"at" that
    starts with the state's own name is skipped ("in New York City", "in the city of New York", "near
    New York" still name the town). The avoid list is untouched: "avoid New York" still works."""
    if not goal:
        return None, []
    areas = _areas(g)
    state = " ".join(re.findall(r"[a-z.'\-]+", state_name.lower()))
    avoid, spans = [], []
    for m in _AVOID_RE.finditer(goal):
        t = _town_at(m.group(1), areas)
        if t and t not in avoid:
            avoid.append(t)
        spans.append(m.span())
    near = None
    for m in _NEAR_RE.finditer(goal):
        if any(a <= m.start() < b for a, b in spans):
            continue
        if state and m.group(0).split()[0].lower() in ("in", "at") and "city of" not in m.group(0).lower():
            words = " ".join(re.findall(r"[a-z.'\-]+", m.group(1).lower()))
            if (words == state or words.startswith(state + " ")) and not words[len(state) :].lstrip().startswith("city"):
                continue  # "in New York": the state the plan is for
        t = _town_at(m.group(1), areas)
        if t and t not in avoid:
            near = t
            break
    return near, avoid


# ---------------------------------------------------------------------------------- the request
class PlanIn(BaseModel):
    region: str = DEFAULT_REGION
    goal: str = ""
    total_mw: float = 1000.0
    max_sites: int = 3
    load_factor: float = 1.0
    firm: bool = False
    use_ai: bool = True  # False: the deterministic planner only (the smoke checks; no quota)
    fresh: bool = False  # True: run Gemini again even when an earlier run of the same request is cached

    @field_validator("total_mw", "load_factor", "max_sites", mode="before")
    @classmethod
    def _no_huge_ints(cls, v):
        # a 400-digit JSON integer would overflow float() later; refuse it as a normal 422
        if isinstance(v, int) and abs(v) > 10**9:
            raise ValueError("number out of range")
        return v


def _validate(body: PlanIn) -> None:
    """422 with a sentence the panel can show as-is."""
    region_code(body.region)
    grid_at(body.load_factor, body.region)  # checks the load level (and loads the state's model)
    if len(body.goal) > GOAL_MAX:
        raise HTTPException(status_code=422, detail=f"Keep the goal under {GOAL_MAX} characters")
    if not math.isfinite(body.total_mw) or not (TOTAL_MIN <= body.total_mw <= TOTAL_MAX):
        raise HTTPException(status_code=422, detail=f"Total must be between {TOTAL_MIN} and {TOTAL_MAX:,} MW")
    if not (1 <= body.max_sites <= MAX_SITES):
        raise HTTPException(status_code=422, detail=f"Ask for between 1 and {MAX_SITES} sites")
    if math.ceil(round(body.total_mw) / SITE_MAX_MW) > body.max_sites:
        raise HTTPException(status_code=422, detail=f"A site takes at most {SITE_MAX_MW:,} MW: allow more sites or ask for less power")


# ---------------------------------------------------------------------------------- the planner
class Planner:
    """One plan: the region's model at the load level, the candidates, the tools both planners use,
    the deterministic planner and the check. Steps go into `steps` as they happen."""

    def __init__(self, req: PlanIn, steps: list):
        self.req = req
        self.code = region_code(req.region)
        self.state_name = REGIONS[self.code]["name"]
        self.g = g = grid_at(req.load_factor, self.code)
        self.lf = float(g.load_factor)
        self.total = int(round(req.total_mw))
        self.max_sites = int(req.max_sites)
        self.firm = bool(req.firm)
        self.goal = " ".join(req.goal.split())
        self.steps = steps
        self.active = np.ones(g.m, dtype=bool)
        base = g.base  # the model at this level with no campus
        over = np.flatnonzero(base.active & (base.loading_pct > OVER_PCT + 1e-6))
        self.base_over = {int(i): float(base.loading_pct[i]) for i in over}
        # people already without power before any campus: the model's generators can't cover the load
        self.base_people = int(g.people(base.lost_existing_mw)) if base.lost_existing_mw > 0.5 else 0
        self.base_lost = float(base.lost_existing_mw)
        self.supply_capped = False  # the model's generators, not its lines, limit the total (a small state)
        self.gemini_short = False  # Gemini's what-if at the full total left load unserved
        self.hb = g.headroom_by_sub()
        self.near, self.avoid = parse_goal(self.goal, g, self.state_name)
        self.spacing = NEAR_SPACING_KM if self.near else SPACING_KM
        self.pool = self._pool(self.near)
        self.near_dropped = False
        if self.near and not self.pool:
            self.near_dropped = True
            self.near = None
            self.spacing = SPACING_KM
            self.pool = self._pool(None)
        self.by_sub = {c.sub: c for c in self.pool}
        self.need = math.ceil(self.total / SITE_MAX_MW)  # sites needed at SITE_MAX_MW each
        self.k = max(self.need, min(self.max_sites, max(1, self.total // MIN_CAMPUS_MW)))
        self.ai_calls = 0
        self._t0: float | None = None  # when the engine started on the step being emitted (tick())
        self._ai_ms: int | None = None  # how long Gemini took to choose the step being emitted
        self._ai_cached = False  # ...and whether that choice came from llm.py's cache (the same question asked before)
        self.ai_cached_calls = 0
        self.fresh = bool(req.fresh)
        self.deadline = AI_DEADLINE_S  # Gemini's whole budget; run_plan sets a job's

    # -------------------------------------------------------------- candidates
    def _pool(self, near: dict | None, min_room: float = MIN_ROOM_MW) -> list[Cand]:
        """The candidates, roomiest first: one substation per town, outside every avoided town,
        within NEAR_KM of `near` when given; the first POOL_MAX of them (plenty for the moves)."""
        g = self.g
        ref = (near["lat"], near["lon"]) if near else None
        out = []
        for h, i, town in _towns(g):
            if h < min_room:
                break  # roomiest first
            lat, lon = float(g.sub_lat[i]), float(g.sub_lon[i])
            if any(_km(a["lat"], a["lon"], lat, lon) < AVOID_KM for a in self.avoid):
                continue
            if ref and _km(ref[0], ref[1], lat, lon) > NEAR_KM:
                continue
            c = Cand(g, i, h, town, ref)
            if not c.ok(g):
                continue
            out.append(c)
            if len(out) >= POOL_MAX:
                break
        if not out and min_room > 0:
            return self._pool(near, 0.0)  # nothing has room at this level: rank what there is
        return out

    def cand(self, sub: int) -> Cand:
        """Any substation of the region as a candidate (Gemini may name one outside the pool)."""
        c = self.by_sub.get(sub)
        if c is None:
            i = self.g.sub_index[sub]
            c = Cand(self.g, i, self.hb.get(sub, 0.0), area_of(self.g.sub_name[i]))
        return c

    def pick(self, k: int, keep: list[Cand] | None = None, tried: set[int] | None = None) -> list[Cand]:
        chosen = list(keep or [])
        for c in self.pool:
            if len(chosen) >= k:
                break
            if c in chosen or (tried and c.sub in tried):
                continue
            if any(_km(c.lat, c.lon, d.lat, d.lon) < self.spacing for d in chosen):
                continue
            chosen.append(c)
        return chosen

    def split(self, cands: list[Cand], total: int | None = None) -> list[int]:
        """The total in proportion to each site's room, in STEP_MW, each at most SITE_MAX_MW, summing exactly."""
        total = self.total if total is None else total
        rooms = np.array([max(c.room, MIN_ROOM_MW) for c in cands], dtype=float)
        share = total * rooms / rooms.sum()
        for _ in range(len(cands)):
            over = share > SITE_MAX_MW
            if not over.any():
                break
            spill = float((share[over] - SITE_MAX_MW).sum())
            share[over] = SITE_MAX_MW
            free = share < SITE_MAX_MW
            if not free.any():
                break
            share[free] += spill * rooms[free] / rooms[free].sum()
        mw = [max(STEP_MW, int(s // STEP_MW) * STEP_MW) for s in share]
        diff = total - sum(mw)
        for j in sorted(range(len(mw)), key=lambda j: -mw[j]):
            if diff == 0:
                break
            if diff > 0:
                add = min(diff, SITE_MAX_MW - mw[j])
            else:
                add = max(diff, STEP_MW - mw[j])
            mw[j] += add
            diff -= add
        return mw

    # -------------------------------------------------------------- the engine
    def case(self, sites: list[tuple[Cand, int]], upgrades: dict | None = None) -> CaseIn:
        """The case the workspace posts for this plan: the biggest campus as the main site, the rest
        biggest first (result() lists the plan's sites in this same order)."""
        sites = sorted(sites, key=lambda cm: -cm[1])
        (c0, m0), rest = sites[0], sites[1:]
        return CaseIn(
            region=self.code,
            lat=c0.lat,
            lon=c0.lon,
            mw=float(m0),
            sites=[SiteIn(lat=c.lat, lon=c.lon, mw=float(m)) for c, m in rest],
            load_factor=self.lf,
            upgrades={int(k): float(v) for k, v in (upgrades or {}).items()},
            firm=self.firm,
        )

    def extra(self, sites) -> np.ndarray:
        return self.g.extra_load([(c.bus, m) for c, m in sites])

    def solve(self, sites, upgrades: dict | None = None):
        g = self.g
        return g.solve(self.active, self.extra(sites), g.rates_with(upgrades))

    def new_over(self, st) -> list[int]:
        """Branches over their limit that the campuses put there (or push further), worst first."""
        over = np.flatnonzero(st.active & (st.loading_pct > OVER_PCT + 1e-6))
        out = [int(i) for i in over if int(i) not in self.base_over or st.loading_pct[i] > self.base_over[int(i)] + WORSE_PCT]
        return sorted(out, key=lambda i: -float(st.loading_pct[i]))

    def short(self, st) -> float:
        """MW of load that goes unserved because of the campuses (the model's generators and imports
        can't cover it: an island short of supply). 0 when every MW is served."""
        lost = float(st.lost_extra_mw) + max(0.0, float(st.lost_existing_mw) - self.base_lost)
        # a tiny threshold: the check counts even a fraction of a MW (a few dozen people) as a failure
        return lost if lost > 0.01 else 0.0

    def all_over(self, st) -> list[int]:
        over = np.flatnonzero(st.active & (st.loading_pct > OVER_PCT + 1e-6))
        return sorted((int(i) for i in over), key=lambda i: -float(st.loading_pct[i]))

    def shares(self, st, w: int) -> np.ndarray:
        """Share of a MW added at each bus that flows over branch w in the direction that loads it."""
        return self.g._line_sens(st, w) * float(np.sign(st.flow[w]) or 1.0)

    def busiest(self, st, fresh_only: bool = False) -> float:
        """The highest loading (%) on a line in service; `fresh_only` leaves out the lines that were
        over before any campus (a heat wave's), so "no new line over" never reads 103 %."""
        mask = st.active.copy()
        if fresh_only and self.base_over:
            mask[list(self.base_over)] = False
        return float(st.loading_pct[mask].max()) if mask.any() else 0.0

    def busiest_text(self, st) -> str:
        if self.base_over:
            return f"leaving aside the lines over before any campus, the busiest is at {_pct(self.busiest(st, True))}"
        return f"the busiest is at {_pct(self.busiest(st))}"

    def line_label(self, i: int) -> str:
        g = self.g
        a, b = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
        if a == b:
            return f"the transformer at {_pretty(g.sub_name[a])}"
        return f"{_pretty(g.sub_name[a])} – {_pretty(g.sub_name[b])}"

    def fix(self, sites) -> dict:
        """The smallest re-rating that clears every overload with these campuses (the /api/fix search)."""
        g = self.g
        first, final, rate, chosen, _, maxed, limited = greedy_fix(g, self.active, self.extra(sites), g.rate.copy())
        # fixit's cap (floor(rate * 5 * 10) / 10) can land a hair above 5x in floating point, which
        # check_case refuses: clamp to exactly 5x the original rating
        rate = np.minimum(rate, g.rate * 5.0)
        lines = [
            {
                "id": int(g.br_ids[i]),
                "from": int(g.sub_ids[g.bus_sub_idx[g.f[i]]]),  # substation ids of its ends (the map draws it)
                "to": int(g.sub_ids[g.bus_sub_idx[g.t[i]]]),
                "label": self.line_label(i),
                "kv": float(g.br_kv[i]),
                "old_mva": round(float(g.rate[i]), 1),
                "new_mva": round(float(rate[i]), 1),
                "added_mva": round(float(rate[i] - g.rate[i]), 1),
                "pct_before": round(float(first.loading_pct[i]), 1),
            }
            for i in chosen
        ]
        return {
            "upgrades": {str(int(g.br_ids[i])): float(rate[i]) for i in chosen},
            "lines": lines,
            "added_mva": round(float(sum(rate[i] - g.rate[i] for i in chosen)), 1),
            "calm": not g.overloaded(final),
            "remaining": len(g.overloaded(final)),
            "needs_new_lines": bool(maxed) or bool(limited),
        }

    def check(self, sites, upgrades: dict | None = None) -> tuple[dict, CaseIn]:
        """The verification: the plan's case through check_case and the same what-if and cascade the
        /api/grid endpoints run. OK = no line over its limit, the cascade leaves nobody without power
        and no campus is cut off."""
        body = self.case(sites, upgrades)
        g, s, trip, upg = check_case(body)
        extra, _ = _case_header(g, s, trip, upg)
        st = g.solve(self.active, extra, g.rates_with(upg))
        over = g.overloaded(st)
        c = g.cascade_case(extra, trip, upg, firm_buses=case_firm_buses(g, s, body.firm))
        ok = not over and c["people"] == 0 and not c["site_cut_off"]
        v = {
            "ok": bool(ok),
            "total_mw": int(sum(m for _, m in sites)),
            "sites": len(sites),
            "upgrades": len(upg),
            "lines_over": len(over),
            "busiest_pct": round(self.busiest(st), 1),
            "cascade_steps": int(c["total_steps"]),
            "outcome": c["outcome"],
            "people": int(c["people"]),
            "lost_mw": c["lost_mw"],
            "people_per_mw": c["people_per_mw"],
            "population": c["population"],
            "site_cut_off": bool(c["site_cut_off"]),
            "firm": bool(body.firm),
        }
        return v, body

    def most_without_upgrades(self, cands: list[Cand], sizes: list[int], supply_only: bool = False) -> list[int] | None:
        """The largest scale of these sizes (in STEP_MW) that puts no new line over its limit and
        leaves no load unserved, by bisection; None when the model is over before any campus or not
        even 10 MW fits. `supply_only`: only the unserved load counts (the most the model's
        generators can cover; lines may still need upgrades)."""
        if self.base_over and not supply_only:
            return None

        def sized(s: float) -> list[int]:
            return [max(STEP_MW, int(m * s // STEP_MW) * STEP_MW) for m in sizes]

        def fits(s: float) -> bool:
            st = self.solve(list(zip(cands, sized(s))))
            return not self.short(st) and (supply_only or not self.new_over(st))

        lo, hi = 0.0, 1.0
        if not fits(STEP_MW / max(sizes)):
            return None
        lo = STEP_MW / max(sizes)
        for _ in range(BISECT_STEPS):
            mid = (lo + hi) / 2
            if fits(mid):
                lo = mid
            else:
                hi = mid
        out = sized(lo)
        return out if fits(lo) else None

    def most_with_rerating(self, cands: list[Cand], sizes: list[int]) -> list[int] | None:
        """The largest scale of these sizes that re-rating alone makes calm (every load served), by a
        short bisection (each probe is a whole greedy_fix); None when not even 10 MW a site fits."""

        def sized(s: float) -> list[int]:
            return [max(STEP_MW, int(m * s // STEP_MW) * STEP_MW) for m in sizes]

        def fits(s: float) -> bool:
            sites = list(zip(cands, sized(s)))
            return not self.short(self.solve(sites)) and self.fix(sites)["calm"]

        lo, hi = STEP_MW / max(sizes), 1.0
        if not fits(lo):
            return None
        for _ in range(RERATE_BISECT_STEPS):
            mid = (lo + hi) / 2
            if fits(mid):
                lo = mid
            else:
                hi = mid
        return sized(lo)

    # -------------------------------------------------------------- steps
    def tick(self) -> None:
        """The engine starts work on the next step: its `ms` counts from here."""
        self._t0 = time.perf_counter()

    def emit(
        self,
        by: str,
        tool: str,
        text: str,
        ok: bool | None = None,
        thought: str | None = None,
        sites: list[dict] | None = None,
        call: dict | None = None,
        result: str | None = None,
        why: str | None = None,
        lines: list[dict] | None = None,
    ) -> dict:
        """One step of the trace. `text` is the whole sentence; for the panel's trace the same step also
        carries `call` (the tool and the arguments it ran with), `result` (the engine's answer alone),
        `ms` (the engine's time on it), for Gemini's steps `ai_ms` (how long Gemini took to choose it)
        and `call_n` (which of its calls), and for the built-in planner `why` (the rule it followed)."""
        now = time.perf_counter()
        ms = round((now - self._t0) * 1000) if self._t0 is not None and tool != "note" else None
        self._t0 = now
        st = {"n": len(self.steps) + 1, "by": by, "tool": tool, "text": text, "ok": ok, "thought": thought, "sites": sites or [],
              "call": call, "result": result, "why": why, "ms": ms}
        if by == "gemini":
            st["ai_ms"] = self._ai_ms
            st["ai_cached"] = self._ai_cached
            st["call_n"] = self.ai_calls
            self._ai_ms = None  # a call's time belongs to the first step it led to
            self._ai_cached = False
        if lines:
            st["upgrade_lines"] = lines
        self.steps.append(st)
        return st

    @staticmethod
    def call_sites(sites) -> list[dict]:
        return [{"sub": c.sub, "mw": int(m)} for c, m in sites]

    def up_lines(self, fx: dict | None) -> list[dict]:
        """A fix's re-rated branches for the map: id, ends (substation ids), MVA added."""
        if not fx or not fx.get("lines"):
            return []
        g = self.g
        out = []
        for u in fx["lines"]:
            i = g.br_index[int(u["id"])]
            out.append({"id": u["id"], "from": int(g.sub_ids[g.bus_sub_idx[g.f[i]]]), "to": int(g.sub_ids[g.bus_sub_idx[g.t[i]]]),
                        "label": u["label"], "added_mva": u["added_mva"], "new_mva": u["new_mva"]})
        return out

    def sites_text(self, sites) -> str:
        return _and([f"{c.town} {_mw(m)}" for c, m in sites])

    def over_text(self, st, over: list[int]) -> str:
        n = len(over)
        w = over[0]
        head = "1 line over its limit" if n == 1 else f"{n} lines over their limits"
        if n == 1:
            return f"{head}: {self.line_label(w)}, at {_pct(st.loading_pct[w])}"
        return f"{head}; the worst, {self.line_label(w)}, is at {_pct(st.loading_pct[w])}"

    def top_text(self, top: list[Cand], near: dict | None) -> str:
        where = f"within {NEAR_KM} km of {near['town']}" if near else f"in {self.state_name}"
        if not top:
            return f"Found no substation {where} with room for a campus at this demand."
        return f"Ranked the substations {where} by how much each can take alone before a line overloads (an estimate): {self.top_result(top)}."

    @staticmethod
    def top_result(top: list[Cand]) -> str:
        if not top:
            return "no substation with room for a campus at this demand"
        first = ", ".join(f"{c.town} {_mw(c.room)}" for c in top[:3])
        more = f" and {len(top) - 3} more" if len(top) > 3 else ""
        return f"{first}{more} (room alone, estimate)"

    def fix_result(self, fx: dict) -> str:
        n = len(fx["lines"])
        if not n:
            return "no upgrade needed: no line is over its limit"
        first = fx["lines"][0]
        head = f"+{_n(fx['added_mva'])} MVA on {n} {'line' if n == 1 else 'lines'} ({first['label']}, {_n(first['old_mva'])} to {_n(first['new_mva'])} MVA{', and more' if n > 1 else ''})"
        return head + ("; clears every overload" if fx["calm"] else f"; {fx['remaining']} still over: it needs new lines")

    def intro(self) -> None:
        """What the planner read from the request before it starts (both planners)."""
        if self.base_people:
            self.emit(
                "planner",
                "note",
                f"At {self.lf * 100:.0f} % of normal demand this synthetic model's generators can't cover all the load: an estimated "
                f"{self.base_people:,} people are without power before any campus is added, and no siting or line upgrade changes that.",
                ok=False,
            )
        if self.base_over:
            n = len(self.base_over)
            self.emit(
                "planner",
                "note",
                f"At {self.lf * 100:.0f} % of normal demand this synthetic model already has {n} {'line' if n == 1 else 'lines'} over "
                f"{'its limit' if n == 1 else 'their limits'} before any campus is added, so the plan has to include upgrades for {'it' if n == 1 else 'them'}.",
                ok=False,
            )
        if self.near_dropped:
            self.emit("planner", "note", f"No substation within {NEAR_KM} km of the town in your goal has room for a campus, so the search covers all of {self.state_name}.")
        elif self.near:
            self.emit("planner", "note", f"Your goal names {self.near['town']}: the search stays within {NEAR_KM} km of it.")
        if self.avoid:
            self.emit("planner", "note", f"Your goal rules out {_and([a['town'] for a in self.avoid])}: nothing within {AVOID_KM} km of {'it' if len(self.avoid) == 1 else 'them'}.")

    # -------------------------------------------------------------- the deterministic planner
    def greedy(self) -> dict:
        """Returns {sites, upgrades, fix, alt} for the check."""
        self.tick()
        top = self.pool[:SHOW_TOP]
        self.emit(
            "planner",
            "headroom_top",
            self.top_text(top, self.near),
            sites=[c.out() for c in top],
            call={"tool": "headroom_top", "args": {"n": SHOW_TOP, **({"near": self.near["town"]} if self.near else {})}},
            result=self.top_result(top),
            why="Start where the grid has the most room: rank the substations by what each can take alone.",
        )
        chosen = self.pick(self.k)
        if len(chosen) < self.need:
            # every candidate is closer than the spacing (a small "near" area): drop the spacing
            self.spacing = 0
            chosen = self.pick(self.k)
        if not chosen:
            self.emit("planner", "note", f"There is no substation in {self.state_name} to place a campus on.", ok=False)
            return {"sites": [], "upgrades": {}, "fix": None, "alt": None}
        sizes = self.split(chosen)
        apart = f", at least {self.spacing} km apart" if len(chosen) > 1 and self.spacing else ""
        if len(chosen) == 1:
            split_text = f"Put all {_mw(self.total)} at {chosen[0].town}, the roomiest site."
        else:
            split_text = f"Split {_mw(self.total)} over the {len(chosen)} roomiest towns{apart}, each in proportion to its room: {self.sites_text(list(zip(chosen, sizes)))}."
        if len(chosen) < self.k:
            split_text += f" Only {len(chosen)} {'site fits' if len(chosen) == 1 else 'sites fit'} the spacing."
        self.emit(
            "planner",
            "split",
            split_text,
            sites=[c.out(m) for c, m in zip(chosen, sizes)],
            call={"tool": "split", "args": {"total_mw": self.total, "sites": self.call_sites(zip(chosen, sizes))}},
            result=self.sites_text(list(zip(chosen, sizes))),
            why="Spread the total over the roomiest towns, each campus in proportion to its room.",
        )

        tried = {c.sub for c in chosen}
        moves = 0
        while True:
            sites = list(zip(chosen, sizes))
            st = self.solve(sites)
            over = self.new_over(st)
            call = {"tool": "whatif", "args": {"sites": self.call_sites(sites)}}
            if not over:
                together = "it" if len(sites) == 1 else ("both together" if len(sites) == 2 else f"all {len(sites)} together")
                lost = self.short(st)
                res = f"no new line over its limit ({self.busiest_text(st)})"
                if lost:
                    res += f", but {_mw(lost)} of load goes unserved: the model's generators and imports can't cover it all"
                self.emit(
                    "planner",
                    "whatif",
                    f"Solved the grid with {together}: {res}.",
                    ok=not lost,
                    sites=[c.out(m) for c, m in sites],
                    call=call,
                    result=res,
                    why="Solve the grid with every campus at once: sites that load the same lines have less room together.",
                )
                break
            w = over[0]
            share = self.shares(st, w)
            adds = [float(share[c.bus]) * m for c, m in sites]
            j = int(np.argmax(adds))
            culprit = chosen[j]
            res = self.over_text(st, over)
            text = f"Tried {self.sites_text(sites)}: {res}."
            if len(sites) > 1:
                blame = f"{culprit.town} sends the most power over it ({_pct(share[culprit.bus] * 100)} of its load)"
                text += f" {blame[0].upper()}{blame[1:]}."
                res += f"; {blame}"
            repl = None
            tiny = float(share[culprit.bus]) < 0.02  # every campus sends a sliver: moving one can't clear it
            roomy = 0  # other sites with room for this share (so the text says why no move was found)
            if moves < MAX_MOVES and not tiny:
                limit = 0.5 * float(share[culprit.bus])
                others = [d for d in chosen if d is not culprit]
                for c in self.pool:
                    if c.room < 0.5 * sizes[j]:
                        break  # roomiest first: the rest are smaller still
                    if c.sub in tried or any(_km(c.lat, c.lon, d.lat, d.lon) < self.spacing for d in others):
                        continue
                    roomy += 1
                    if float(share[c.bus]) <= limit:
                        repl = c
                        break
            if repl is not None:
                moved = f"moved {'its' if len(sites) == 1 else _poss(culprit.town)} share to {repl.town}, which sends {_pct(max(share[repl.bus], 0) * 100)} over that line"
                text += f" M{moved[1:]}."
                self.emit(
                    "planner",
                    "move",
                    text,
                    ok=False,
                    sites=[c.out(m) for c, m in sites],
                    call=call,
                    result=f"{res}; {moved}",
                    why="A line went over: move the campus that sends it the most power to a roomy site that sends it little.",
                )
                chosen[j] = repl
                tried.add(repl.sub)
                moves += 1
                sizes = self.split(chosen)
                continue
            if tiny:
                stop = (
                    "The campus sends only a sliver of its power over it (it's the added total that loads it), so moving it won't help."
                    if len(sites) == 1
                    else "Every campus sends only a sliver of its power over it (the whole total loads it), so moving one won't help."
                )
            elif moves >= MAX_MOVES:
                stop = "That was the last move the planner tries."
            elif not roomy:
                stop = f"No other site in reach has room for {'its' if len(sites) == 1 else _poss(culprit.town)} share, so moving won't help."
            elif len(sites) > 1:
                stop = "Every other roomy site sends as much over it, so moving won't help."
            else:
                stop = "No roomy site sends less over it."
            self.emit(
                "planner",
                "whatif",
                f"{text} {stop}",
                ok=False,
                sites=[c.out(m) for c, m in sites],
                call=call,
                result=res,
                why=stop,
            )
            break

        full = list(zip(chosen, sizes))
        st = self.solve(full)
        lost = self.short(st)
        if lost:
            # a small state: the generators, not the lines, run out — no upgrade helps, so place less
            less = self.most_without_upgrades(chosen, sizes, supply_only=True)
            if not less:
                self.emit(
                    "planner",
                    "note",
                    f"This synthetic model's generators and imports can't cover even {_mw(STEP_MW * len(chosen))} more load at these sites, so no plan here keeps everyone's power on.",
                    ok=False,
                )
                return {"sites": full, "upgrades": {}, "fix": None, "alt": None}
            self.supply_capped = True
            sizes = less
            # a smaller total on fewer campuses (no 50 MW campuses); the supply limit is the island's, so the sum still fits
            k2 = max(1, min(len(chosen), sum(less) // MIN_CAMPUS_MW))
            if k2 < len(chosen):
                chosen = chosen[:k2]
                sizes = self.split(chosen, sum(less))
            full = list(zip(chosen, sizes))
            self.emit(
                "planner",
                "shrink",
                f"With {_mw(self.total)} the model's generators and imports can't keep up ({_mw(lost)} of load would go unserved), and no line upgrade changes that. "
                f"The most it can take with every load served: {_mw(sum(sizes))}, placed as {self.sites_text(full)}.",
                ok=None,
                sites=[c.out(m) for c, m in full],
                call={"tool": "shrink", "args": {"sites": self.call_sites(full), "limit": "supply"}},
                result=f"{_mw(sum(sizes))} with every load served: {self.sites_text(full)}",
                why="The generators, not the lines, run out: find the most the model can serve (bisection on the engine).",
            )
            st = self.solve(full)
        target = sum(sizes)
        if not self.all_over(st):
            return {"sites": full, "upgrades": {}, "fix": None, "alt": None, "partial": self.supply_capped}
        # upgrades are needed: the most these sites take without them, then the smallest re-rating for all of it
        alt = None
        if self.new_over(st):
            less = self.most_without_upgrades(chosen, sizes)
            if less:
                alt = list(zip(chosen, less))
                w = self.new_over(self.solve([(c, m + STEP_MW) for c, m in alt]))  # one step more: the line that stops it
                what = f" before {self.line_label(w[0])} goes over" if w else ""
                self.emit(
                    "planner",
                    "shrink",
                    f"Without upgrades these sites take {_mw(sum(less))} together{what}: {self.sites_text(alt)}.",
                    ok=None,
                    sites=[c.out(m) for c, m in alt],
                    call={"tool": "shrink", "args": {"sites": self.call_sites(alt), "limit": "no upgrades"}},
                    result=f"{_mw(sum(less))} together{what}",
                    why="Measure the most these sites take with no upgrade at all (bisection on the engine).",
                )
        fx = self.fix(full)
        full_txt = _mw(target) if self.supply_capped else f"the full {_mw(target)}"
        if fx["lines"]:
            n = len(fx["lines"])
            lines = _and([f"{u['label']} ({_n(u['old_mva'])} to {_n(u['new_mva'])} MVA)" for u in fx["lines"][:3]]) + (f" and {n - 3} more" if n > 3 else "")
            if fx["calm"]:
                text = f"The smallest re-rating that carries {full_txt}: +{_n(fx['added_mva'])} MVA on {n} {'line' if n == 1 else 'lines'}, {lines}."
                if self.base_over:
                    text += " It includes the lines that were over before any campus."
            else:
                text = (
                    f"Re-rating {n} {'line' if n == 1 else 'lines'} (+{_n(fx['added_mva'])} MVA) still leaves {fx['remaining']} over: "
                    f"{full_txt} needs new lines, not just higher ratings."
                )
            self.emit(
                "planner",
                "fix",
                text,
                ok=fx["calm"],
                sites=[c.out(m) for c, m in full],
                call={"tool": "fix", "args": {"sites": self.call_sites(full)}},
                result=self.fix_result(fx),
                why="To build the full amount here, find the smallest set of line re-ratings that carries it.",
                lines=self.up_lines(fx),
            )
        if not fx["calm"] and alt is None:
            # lines over before any campus (a heat wave) leave no version without upgrades: find the
            # most these sites take with re-ratings alone instead of handing back a plan that fails
            more = self.most_with_rerating(chosen, sizes)
            if more:
                part = list(zip(chosen, more))
                fx2 = self.fix(part)
                n2 = len(fx2["lines"])
                self.emit(
                    "planner",
                    "shrink",
                    f"With re-ratings alone these sites take at most {_mw(sum(more))} together: {self.sites_text(part)}"
                    + (f", with +{_n(fx2['added_mva'])} MVA on {n2} {'line' if n2 == 1 else 'lines'}." if n2 else "."),
                    ok=None,
                    sites=[c.out(m) for c, m in part],
                    call={"tool": "shrink", "args": {"sites": self.call_sites(part), "limit": "re-ratings"}},
                    result=f"at most {_mw(sum(more))}" + (f" with +{_n(fx2['added_mva'])} MVA on {n2} {'line' if n2 == 1 else 'lines'}" if n2 else ""),
                    why="Re-rating can't carry the full total: measure the most it can (bisection on the engine).",
                    lines=self.up_lines(fx2),
                )
                return {"sites": part, "upgrades": fx2["upgrades"], "fix": fx2, "alt": None, "partial": True}
        if fx["calm"] or alt is None:
            return {"sites": full, "upgrades": fx["upgrades"], "fix": fx, "alt": alt, "partial": self.supply_capped}
        # re-rating can't carry the full total: the plan is the most that fits without upgrades
        return {"sites": alt, "upgrades": {}, "fix": fx, "alt": None, "partial": True}

    # -------------------------------------------------------------- the result
    def verify_step(self, v: dict, who: str, sites=None) -> None:
        call = {"tool": "check", "args": {**({"sites": self.call_sites(sites)} if sites else {}), "upgrades": v["upgrades"], "firm": v["firm"]}}
        why = "Re-runs the plan the way the workspace will (the what-if, then the cascade): it passes only with no line over and nobody out."
        if v["ok"]:
            steps = "nothing trips" if v["cascade_steps"] == 0 else f"{v['cascade_steps']} {'step' if v['cascade_steps'] == 1 else 'steps'}, nobody loses power"
            text = (
                f"Checked {who} with the engine: {_mw(v['total_mw'])} on {v['sites']} {'site' if v['sites'] == 1 else 'sites'}"
                f"{' with ' + str(v['upgrades']) + (' upgrade' if v['upgrades'] == 1 else ' upgrades') if v['upgrades'] else ''}, "
                f"no line over its limit (the busiest is at {_pct(v['busiest_pct'])}), the cascade: {steps}. an estimated 0 people without power."
            )
            result = f"passes: no line over its limit (busiest {_pct(v['busiest_pct'])}), cascade: {steps}, an estimated 0 people without power"
        else:
            bits = []
            if v["lines_over"]:
                bits.append(f"{v['lines_over']} {'line' if v['lines_over'] == 1 else 'lines'} over {'its limit' if v['lines_over'] == 1 else 'their limits'}")
            if v["people"]:
                bits.append(f"the cascade leaves an estimated {v['people']:,} people without power")
            if v["site_cut_off"]:
                bits.append("a campus is cut off")
            text = f"Checked {who} with the engine: it does not pass — {_and(bits) or 'it fails the check'}."
            result = f"does not pass: {_and(bits) or 'it fails the check'}"
            if self.base_people and v["people"]:
                text += f" An estimated {self.base_people:,} of them had no power before any campus."
        self.emit("planner", "check", text, ok=v["ok"], call=call, result=result, why=why, sites=[c.out(m) for c, m in sites] if sites else None)

    def result(self, plan: dict | None, v: dict | None, body: CaseIn | None, by: str, ai_status: str, alt_v: dict | None = None) -> dict:
        g = self.g
        out_plan = None
        if plan and plan["sites"]:
            sites = sorted(plan["sites"], key=lambda cm: -cm[1])
            fx = plan.get("fix") or {}
            out_plan = {
                "by": by,
                "sites": [c.out(m) for c, m in sites],
                "total_mw": int(sum(m for _, m in sites)),
                "upgrades": {str(k): v_ for k, v_ in (plan["upgrades"] or {}).items()},
                "upgrade_lines": fx.get("lines", []) if plan["upgrades"] else [],
                "added_mva": fx.get("added_mva", 0.0) if plan["upgrades"] else 0.0,
                "partial": bool(plan.get("partial")),
                # why a partial plan places less: "supply" (the model's generators) or "lines" (re-rating can't carry it)
                "partial_reason": ("supply" if self.supply_capped else "lines") if plan.get("partial") else None,
            }
        alt = None
        if plan and plan.get("alt") and alt_v:
            a = sorted(plan["alt"], key=lambda cm: -cm[1])
            alt = {"sites": [c.out(m) for c, m in a], "total_mw": int(sum(m for _, m in a)), "verification": alt_v[0], "case": alt_v[1].model_dump()}
        return {
            "region": self.code,
            "region_name": self.state_name,
            "goal": self.goal,
            "total_mw": self.total,
            "max_sites": self.max_sites,
            "load_factor": self.lf,
            "firm": self.firm,
            "near": {"town": self.near["town"], "km": NEAR_KM} if self.near else None,
            "avoid": [a["town"] for a in self.avoid],
            "baseline_over": len(self.base_over),
            "baseline_people": self.base_people,
            "steps": list(self.steps),
            "plan": out_plan,
            "verification": v,
            "case": body.model_dump() if body is not None else None,
            "without_upgrades": alt,
            "by": by,
            "fallback": bool(self.req.use_ai and by != "gemini"),
            "ai": {"status": ai_status, "calls": self.ai_calls, "cached_calls": self.ai_cached_calls, "configured": configured()},
            "synthetic": True,
            "people_per_mw": round(g.people_per_mw, 2),
            "population": g.population,
        }

    def run_greedy(self, ai_status: str) -> dict:
        """The deterministic planner from start to check (runs in a worker thread)."""
        note = {
            "not_configured": "Gemini isn't connected, so the built-in planner makes the plan: the same engine, step by step.",
            "unavailable": "Gemini stopped answering, so the built-in planner takes over.",
            "rate_limited": "Gemini is over its request limit right now, so the built-in planner takes over: the same engine, step by step.",
            "slow": "Gemini took too long, so the built-in planner takes over.",
            "rejected": "Gemini's plan didn't pass the engine's check, so the built-in planner takes over.",
            "out_of_calls": f"Gemini used its {AI_CALLS} calls without a plan that passes, so the built-in planner takes over.",
            "supply": "Gemini found that this synthetic model's generators can't cover the full total, so the built-in planner finds the most it can place.",
        }.get(ai_status)
        if note:
            self.emit("planner", "note", note)
        if ai_status in ("off", "not_configured"):
            self.intro()
        plan = self.greedy()
        if not plan["sites"]:
            return self.result(None, None, None, "planner", ai_status)
        self.tick()
        v, body = self.check(plan["sites"], plan["upgrades"])
        self.verify_step(v, "the plan", plan["sites"])
        alt_v = self.check(plan["alt"], {}) if plan.get("alt") else None
        return self.result(plan, v, body, "planner", ai_status, alt_v)

    # -------------------------------------------------------------- Gemini's tools
    def parse_sites(self, raw) -> list[tuple[Cand, int]]:
        if not isinstance(raw, list) or not raw:
            raise ValueError("sites must be a non-empty list of {sub, mw}")
        if len(raw) > self.max_sites:
            raise ValueError(f"at most {self.max_sites} sites")
        out, seen = [], set()
        for s in raw:
            if not isinstance(s, dict):
                raise ValueError("each site is {sub, mw}")
            try:
                sub = int(s.get("sub"))
                mw = float(s.get("mw"))
            except (TypeError, ValueError):
                raise ValueError("each site needs a numeric sub id and mw") from None
            if sub not in self.g.sub_index:
                raise ValueError(f"there is no substation {sub} in {self.state_name}")
            if sub in seen:
                raise ValueError("one campus per substation")
            if not math.isfinite(mw) or not (MW_MIN <= mw <= SITE_MAX_MW):
                raise ValueError(f"each size must be between {MW_MIN} and {SITE_MAX_MW:,} MW")
            c = self.cand(sub)
            if not c.ok(self.g):
                raise ValueError(f"substation {sub} shares its spot with another; pick a different one")
            seen.add(sub)
            out.append((c, int(round(mw))))
        return out

    def near_pool(self, town: str) -> tuple[list[Cand], dict | None]:
        t = _town_at(str(town), _areas(self.g))
        if t is None:
            raise ValueError(f"no town called {str(town)[:40]!r} in {self.state_name}'s model")
        return self._pool(t), t

    def agent_tool(self, action: str, args: dict, thought: str | None) -> tuple[dict, dict | None]:
        """Run one of Gemini's actions on the engine. Returns (what Gemini is told, a finished plan or None).
        Each action becomes a step of the trace: the call as it ran, the engine's answer, the time."""
        g = self.g
        self.tick()
        if action == "headroom_top":
            n = args.get("n", SHOW_TOP)
            n = int(n) if isinstance(n, (int, float)) and math.isfinite(n) else SHOW_TOP
            n = max(1, min(AI_TOP_MAX, n))
            near = args.get("near")
            if isinstance(near, str) and near.strip().lower() not in ("", "null", "none"):
                pool, t = self.near_pool(near)
            else:
                pool, t = self.pool, None
            top = pool[:n]
            self.emit(
                "gemini",
                "headroom_top",
                self.top_text(top, t),
                thought=thought,
                sites=[c.out() for c in top],
                call={"tool": "headroom_top", "args": {"n": n, **({"near": t["town"]} if t else {})}},
                result=self.top_result(top),
            )
            return {"sites": [{"sub": c.sub, "town": c.town, "room_mw": round(c.room), "kv": c.kv, "lat": round(c.lat, 2), "lon": round(c.lon, 2), **({"km_from_" + t["town"]: c.km} if t else {})} for c in top]}, None

        sites = self.parse_sites(args.get("sites"))
        use_up = bool(args.get("use_upgrades"))
        call = {"tool": action, "args": {"sites": self.call_sites(sites), **({"use_upgrades": True} if use_up and action in ("cascade", "finish") else {})}}
        if action == "whatif":
            st = self.solve(sites)
            over = self.new_over(st)
            worst = []
            for w in over[:3]:
                share = self.shares(st, w)
                item = {"line": self.line_label(w), "pct": round(float(st.loading_pct[w]), 1), "mw_added_by_site": {str(c.sub): round(float(share[c.bus]) * m) for c, m in sites}}
                sh = np.array([float(share[c.bus]) for c, _ in sites])
                if len(sites) > 1 and sh.min() > 0.005 and sh.max() <= 1.3 * sh.min():
                    item["hint"] = (
                        "every campus sends about the same share of its power over this line (it sits next to the generators that pick up any new load), "
                        "so moving or resizing campuses won't clear it: only less total MW or an upgrade will"
                    )
                worst.append(item)
            lost = self.short(st)
            res = self.over_text(st, over) if over else f"no new line over its limit ({self.busiest_text(st)})"
            if lost:
                res += f"; {_mw(lost)} of load goes unserved: the model's generators and imports can't cover it all"
            text = f"Tried {self.sites_text(sites)}: " + (self.over_text(st, over) + "." if over else f"no new line over its limit ({self.busiest_text(st)}).")
            if lost:
                text += f" {_mw(lost)} of load goes unserved: the model's generators and imports can't cover it all."
            self.emit("gemini", "whatif", text, ok=not over and not lost, thought=thought, sites=[c.out(m) for c, m in sites], call=call, result=res)
            told = {"total_mw": sum(m for _, m in sites), "new_lines_over": len(over), "worst": worst, "busiest_pct": round(self.busiest(st), 1), "people_without_power": g.people(st.lost_existing_mw)}
            if lost:
                if sum(m for _, m in sites) >= self.total:
                    self.gemini_short = True  # the full total can't be served anywhere: the built-in planner places less
                told["unserved_mw"] = round(lost)
                told["hint"] = "the model's generators and imports can't cover this much new load; no siting or upgrade fixes that, only a smaller total"
            return told, None
        if action == "fix":
            fx = self.fix(sites)
            n = len(fx["lines"])
            if not n:
                text = f"Asked for upgrades for {self.sites_text(sites)}: none needed, no line is over its limit."
            else:
                text = f"Asked for the smallest re-rating for {self.sites_text(sites)}: +{_n(fx['added_mva'])} MVA on {n} {'line' if n == 1 else 'lines'}" + (
                    "." if fx["calm"] else f", and {fx['remaining']} still over (it needs new lines)."
                )
            self.emit("gemini", "fix", text, ok=fx["calm"], thought=thought, sites=[c.out(m) for c, m in sites], call=call, result=self.fix_result(fx), lines=self.up_lines(fx))
            return {"lines": n, "added_mva": fx["added_mva"], "clears_all": fx["calm"], "upgraded": [u["label"] for u in fx["lines"][:5]]}, None
        if action == "cascade":
            upg = self.fix(sites)["upgrades"] if use_up else {}
            v, _ = self.check(sites, upg)
            res = "nothing trips" if v["cascade_steps"] == 0 else f"{v['cascade_steps']} steps, an estimated {v['people']:,} people without power"
            text = f"Ran the cascade on {self.sites_text(sites)}{' with upgrades' if upg else ''}: {res}."
            self.emit("gemini", "cascade", text, ok=v["people"] == 0 and not v["site_cut_off"], thought=thought, sites=[c.out(m) for c, m in sites], call=call, result=res)
            return {"steps": v["cascade_steps"], "people_without_power": v["people"], "campus_cut_off": v["site_cut_off"], "lines_over_before": v["lines_over"]}, None
        if action == "finish":
            # the goal's places bind the final plan too
            for c, _ in sites:
                for a in self.avoid:
                    if _km(a["lat"], a["lon"], c.lat, c.lon) < AVOID_KM:
                        raise ValueError(f"{c.town} is within {AVOID_KM} km of {a['town']}, which the goal rules out")
                if self.near and _km(self.near["lat"], self.near["lon"], c.lat, c.lon) > NEAR_KM:
                    raise ValueError(f"{c.town} is more than {NEAR_KM} km from {self.near['town']}, which the goal asks to stay near")
            total = sum(m for _, m in sites)
            if abs(total - self.total) > max(STEP_MW, 0.01 * self.total):
                raise ValueError(f"the sizes add up to {total:,} MW; the plan must place {self.total:,} MW")
            if total != self.total:  # within the tolerance: the biggest campus takes the difference
                j = max(range(len(sites)), key=lambda j: sites[j][1])
                c, m = sites[j]
                if not (MW_MIN <= m + self.total - total <= SITE_MAX_MW):
                    raise ValueError(f"the sizes add up to {total:,} MW; the plan must place {self.total:,} MW")
                sites[j] = (c, m + self.total - total)
                call["args"]["sites"] = self.call_sites(sites)
            fx = self.fix(sites) if use_up else None
            upg = fx["upgrades"] if fx else {}
            ups = f"+{_n(fx['added_mva'])} MVA of re-ratings on {len(fx['lines'])} {'line' if len(fx['lines']) == 1 else 'lines'}" if fx and fx["lines"] else ""
            self.emit(
                "gemini",
                "finish",
                f"Proposed {self.sites_text(sites)}" + (f", with {ups}." if ups else "."),
                thought=thought,
                sites=[c.out(m) for c, m in sites],
                call=call,
                result=f"{_mw(sum(m for _, m in sites))} on {len(sites)} {'site' if len(sites) == 1 else 'sites'}"
                + (f", with {ups}" if ups else ", no upgrades")
                + ": handed to the engine to check",
                lines=self.up_lines(fx),
            )
            v, body = self.check(sites, upg)
            self.verify_step(v, "Gemini's plan", sites)
            plan = {"sites": sites, "upgrades": upg, "fix": fx, "alt": None}
            if v["ok"] and upg:
                st = self.solve(sites)
                if self.new_over(st):
                    less = self.most_without_upgrades([c for c, _ in sites], [m for _, m in sites])
                    if less:
                        plan["alt"] = list(zip([c for c, _ in sites], less))
            told = {"passed": v["ok"], "lines_over": v["lines_over"], "people_without_power": v["people"], "campus_cut_off": v["site_cut_off"]}
            return told, ({"plan": plan, "v": v, "body": body} if v["ok"] else None)
        raise ValueError(f"unknown action {action!r}")


# ---------------------------------------------------------------------------------- the agent
SYSTEM = """You are Overload's siting planner: an agent that places AI data-center campuses on a SYNTHETIC model of one U.S. state's power grid (the Breakthrough Energy / Texas A&M test system, not any real utility's network). You work by calling tools that run a real DC power-flow engine, one tool per reply, and you end with a plan the engine verifies.

A good plan puts the requested total MW on at most the allowed number of substations so that no line goes over its limit and nobody loses power, and it follows any preference in the user's goal (a town to stay near or to avoid).

Reply with ONE JSON object and nothing else:
{"thought": "<one short plain-English sentence for the user, in the present tense: what you do next and why, e.g. \"Checking the roomiest substations first.\">", "action": "<tool>", "args": {...}}

Tools:
- "headroom_top" {"n": 10, "near": "<town, or an empty string>"}: the roomiest substations, one per town, each with room_mw = the MW it can take ALONE before a line overloads (an estimate; campuses that load the same lines have less room together). "near" keeps to 80 km around that town.
- "whatif" {"sites": [{"sub": <substation id>, "mw": <MW>}]}: solves the grid with all these campuses together: new lines over their limits (worst first, with the MW each campus adds to that line), the busiest line's loading, people without power, and unserved_mw when the model's generators can't cover that much new load (then only a smaller total works).
- "fix" {"sites": [...]}: the smallest set of line re-ratings (MVA added) that clears every overload for these campuses, and whether re-rating alone is enough.
- "cascade" {"sites": [...], "use_upgrades": false}: runs the cascading-failure simulation: steps, people without power (an estimate), whether a campus is cut off.
- "finish" {"sites": [...], "use_upgrades": <true only if the plan needs the fix's re-ratings>}: your final plan. The engine then checks it: no line over its limit (after the re-ratings if used) and nobody without power.

Rules:
- Use only substation ids a tool returned. Sizes are MW in multiples of 10, each between 1 and 5000.
- The sizes in "finish" must add up to exactly the requested total, on at most the allowed number of sites, one campus per substation.
- Spread campuses over different towns at least 30 km apart (headroom_top gives each site's lat/lon). If a line goes over, move the campus that adds the most MW to it to a site that adds little.
- Prefer a plan without upgrades; use them only when every spread you tried still overloads a line.
- Be efficient: at most 6 replies in all. A good run is headroom_top, one or two whatif, then finish.
- The thought is shown to the user beside the tool call: say what you learned from the last result and what you try now, in one sentence, with no numbers the tools did not give you.
- Never mention real utilities, real companies, real projects or real events."""

OFFLINE = {"action": "__offline__"}
ACTIONS = ("headroom_top", "whatif", "fix", "cascade", "finish")

# llm.complete_json's options the agent uses when this llm.py has them: the agent-step model
# (GEMINI_AGENT_MODEL) and the answer cache (on by default there; a "fresh" run turns it off)
_CJ = inspect.signature(complete_json).parameters


def _call_kw(fresh: bool) -> dict:
    kw = {}
    if "model" in _CJ and getattr(llm, "AGENT_MODEL", None):
        kw["model"] = llm.AGENT_MODEL
    if "cache" in _CJ:
        kw["cache"] = not fresh
    return kw


def _cached_count() -> int:
    """Planner answers llm.py has served from its cache so far (its per-feature counter)."""
    return int(llm._stats.get("by_surface", {}).get("planner", {}).get("cached", 0))
_SITE_LIST = {
    "type": "array",
    "items": {"type": "object", "properties": {"sub": {"type": "integer"}, "mw": {"type": "integer"}}, "required": ["sub", "mw"]},
}
# Gemini's structured output: one reply = one tool call (llm.complete_json retries without the schema
# if Google refuses it). "thought" comes first so the reason is written before the action.
AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "args": {
            "type": "object",
            "properties": {"n": {"type": "integer"}, "near": {"type": "string"}, "sites": _SITE_LIST, "use_upgrades": {"type": "boolean"}},
        },
    },
    "required": ["thought", "action", "args"],
}


def _safe_args(args: dict) -> dict:
    """Gemini's arguments as the trace shows them when the planner refused the call: known keys only,
    finite numbers and short strings (a reply is data, never trusted as it came)."""
    out: dict = {}

    def ok(v) -> bool:
        return isinstance(v, bool) or (isinstance(v, (int, float)) and math.isfinite(v)) or (isinstance(v, str) and len(v) <= 60)

    for k in ("n", "near", "use_upgrades"):
        if k in args and ok(args[k]):
            out[k] = args[k]
    sites = args.get("sites")
    if isinstance(sites, list):
        out["sites"] = [{"sub": s.get("sub"), "mw": s.get("mw")} for s in sites[:8] if isinstance(s, dict) and ok(s.get("sub")) and ok(s.get("mw"))]
    return out


# Gemini's thought is shown as its own words; one that names a real utility or grid operator is
# dropped (the model is synthetic, never a real network — CLAUDE.md → Honesty)
_REAL_NAMES = re.compile(
    r"\b(FPL|Florida Power|Duke Energy|TECO|Tampa Electric|ERCOT|Oncor|CenterPoint|Dominion|Georgia Power|Southern Company|PG&E|Con ?Ed(?:ison)?|Xcel|Entergy|AEP|PJM|MISO|CAISO|NYISO|TVA)\b",
    re.I,
)


def _thought(raw) -> str | None:
    if not isinstance(raw, str) or _REAL_NAMES.search(raw):
        return None
    t = re.sub(r"[*_`#>]+", "", " ".join(raw.split())).strip()
    if len(t) > THOUGHT_MAX:
        t = t[: THOUGHT_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t or None


def _prompt(p: Planner, history: list[str], calls_left: int, feedback: str | None) -> str:
    lines = [
        f'User goal: "{p.goal or f"place {p.total:,} MW of AI campuses in {p.state_name} without blacking anyone out"}"',
        f"State: {p.state_name} (synthetic model). Total to place: {p.total:,} MW. At most {p.max_sites} sites. "
        f"Demand: {p.lf * 100:.0f} % of the model's normal load. Campuses: {'firm (kept on in an emergency; other customers are cut instead)' if p.firm else 'flexible (the grid may cut them off in an emergency)'}.",
    ]
    if p.base_over:
        lines.append(f"Note: at this demand {len(p.base_over)} lines are over their limits before any campus, so the final plan must use upgrades.")
    if p.base_people:
        lines.append(f"Note: at this demand the model's generators cannot cover the load; about {p.base_people:,} people are without power before any campus, so no plan can pass the check. Finish with your best plan quickly.")
    if p.near:
        lines.append(f"The goal names a town to stay near: {p.near['town']}. Use headroom_top with near; every site in the final plan must be within {NEAR_KM} km of it.")
    if p.avoid:
        lines.append(f"The goal rules out: {_and([a['town'] for a in p.avoid])}. headroom_top already leaves them out; no site may be within {AVOID_KM} km of them.")
    lines.append("")
    lines.append("Your calls so far:" if history else "You have made no calls yet.")
    lines += history
    if feedback:
        lines.append(feedback)
    lines.append("")
    if calls_left <= 1:
        lines.append('This is your LAST reply: answer with action "finish" and your best plan.')
    else:
        lines.append(f"Replies left, including this one: {calls_left}.")
    lines.append('Reply with JSON only: {"thought": "...", "action": "...", "args": {...}}')
    return "\n".join(lines)


async def run_agent(p: Planner) -> tuple[str, dict | None]:
    """Gemini's loop. Returns (status, finished plan or None): status "used" with a plan that passed,
    else why it stopped ("unavailable", "rate_limited", "slow", "rejected", "out_of_calls", "supply").
    At most AI_CALLS calls (one retry of a failed call included) within p.deadline seconds."""
    await run_in_threadpool(p.intro)
    history: list[str] = []
    feedback = None
    rejected = False
    retries = 0
    start = time.monotonic()
    while p.ai_calls < AI_CALLS:
        left = p.deadline - (time.monotonic() - start)
        if left < 2:
            return "slow", None
        prompt = _prompt(p, history, AI_CALLS - p.ai_calls, feedback)
        t0 = time.monotonic()
        c0 = _cached_count()
        try:
            reply, offline = await asyncio.wait_for(
                complete_json(
                    prompt, system=SYSTEM, fallback=OFFLINE, timeout=min(AI_CALL_TIMEOUT_S, left), schema=AGENT_SCHEMA, surface="planner", **_call_kw(p.fresh)
                ),
                left,
            )
        except asyncio.TimeoutError:
            return "slow", None
        p.ai_calls += 1
        ai_ms = round((time.monotonic() - t0) * 1000)
        # the same question asked earlier: Gemini's earlier answer, served from the cache (said so in the trace)
        from_cache = not offline and _cached_count() > c0
        p.ai_cached_calls += int(from_cache)
        if offline:
            # one call that hangs or errors is asked again (the same prompt), in the open; a 429 (request
            # limit) or 503 (model busy) gets a short wait first
            last = str(llm._stats.get("last_error", ""))
            limited = "(429)" in last
            busy = "(503)" in last  # Google's "model overloaded": usually gone a moment later
            left = p.deadline - (time.monotonic() - start)
            wait = RATE_WAIT_S if limited else BUSY_WAIT_S if busy else 0.0
            if retries < AI_RETRIES and p.ai_calls < AI_CALLS and left > 4 + wait:
                retries += 1
                if limited:
                    why = f"Gemini is over its request limit (call {p.ai_calls}): waiting {wait:.0f} s, then asking once more."
                elif busy:
                    why = f"Gemini is busy (call {p.ai_calls}): waiting {wait:.0f} s, then asking once more."
                else:
                    why = f"Gemini didn't answer call {p.ai_calls} ({ai_ms / 1000:.1f} s): asking once more."
                await run_in_threadpool(p.emit, "planner", "note", why)
                if wait:
                    await asyncio.sleep(wait)
                continue
            return ("rate_limited" if limited else "unavailable"), None
        feedback = None
        n = len(history) + 1
        if not isinstance(reply, dict) or reply.get("action") not in ACTIONS:
            llm.note_check("planner", False, "a reply with no valid action")
            history.append(f"{n}. (invalid reply: it must be one JSON object with an action from {', '.join(ACTIONS)})")
            continue
        action = reply["action"]
        args = reply.get("args") if isinstance(reply.get("args"), dict) else {}
        thought = _thought(reply.get("thought"))
        p._ai_ms = ai_ms
        p._ai_cached = from_cache
        try:
            told, done = await run_in_threadpool(p.agent_tool, action, args, thought)
        except (ValueError, HTTPException) as e:
            why = e.detail if isinstance(e, HTTPException) else str(e)
            llm.note_check("planner", False, f"a step the planner refused: {why}", action)
            await run_in_threadpool(
                lambda: p.emit("gemini", action, f"The planner refused that request: {why}.", False, thought, call={"tool": action, "args": _safe_args(args)}, result=f"refused: {why}")
            )
            history.append(f"{n}. {action} {json.dumps(args)[:400]} -> error: {why}")
            continue
        history.append(f"{n}. {action} {json.dumps(args)[:400]} -> {json.dumps(told)[:HISTORY_MAX]}")
        llm.note_check("planner", action != "finish" or bool(done), "a finished siting plan the engine re-checked: a line over its limit or people without power")
        if p.gemini_short:
            return "supply", None  # a finish must place the full total, which can't pass: don't spend more calls
        if action == "finish":
            if done:
                return "used", done
            rejected = True
            feedback = "Your finish did NOT pass the engine's check (see the result above). Change the plan: move or shrink the campus that loads the overloaded line, or use upgrades."
    return ("rejected" if rejected else "out_of_calls"), None


# ---------------------------------------------------------------------------------- running a plan
_cache: "OrderedDict[str, dict]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(req: PlanIn) -> str:
    return json.dumps(
        [region_code(req.region), round(req.load_factor, 2), round(req.total_mw), req.max_sites, req.firm, " ".join(req.goal.lower().split())],
        separators=(",", ":"),
    )


async def run_plan(req: PlanIn, steps: list, deadline: float = AI_DEADLINE_S) -> dict:
    """A whole plan: Gemini's loop when asked for (and configured), else or after it the deterministic
    planner. `steps` fills as it goes (a job's live view); `deadline`: Gemini's whole time budget."""
    key = _cache_key(req)
    if req.use_ai and not req.fresh:
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None:
                _cache.move_to_end(key)
                hit = copy.deepcopy(hit)
        if hit is not None:
            steps.extend(hit["steps"])
            return {**hit, "cached": True}
    p = await run_in_threadpool(Planner, req, steps)
    p.deadline = deadline
    if not req.use_ai:
        return await run_in_threadpool(p.run_greedy, "off")
    if not configured():
        return await run_in_threadpool(p.run_greedy, "not_configured")
    status, done = await run_agent(p)
    if done is None:
        return await run_in_threadpool(p.run_greedy, status)
    alt_v = await run_in_threadpool(p.check, done["plan"]["alt"], {}) if done["plan"].get("alt") else None
    out = p.result(done["plan"], done["v"], done["body"], "gemini", "used", alt_v)
    with _cache_lock:
        _cache[key] = copy.deepcopy(out)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return out


class _Job:
    __slots__ = ("id", "steps", "status", "result", "error", "created", "task")

    def __init__(self):
        self.id = secrets.token_urlsafe(12)
        self.steps: list = []
        self.status = "running"
        self.result = None
        self.error = None
        self.created = time.monotonic()
        self.task = None


_jobs: "OrderedDict[str, _Job]" = OrderedDict()
_jobs_lock = threading.Lock()


def _gc_jobs() -> None:
    now = time.monotonic()
    for jid in [j for j, job in _jobs.items() if job.status != "running" and now - job.created > JOB_TTL_S]:
        del _jobs[jid]
    while len(_jobs) > JOBS_MAX:
        old = next((j for j, job in _jobs.items() if job.status != "running"), None)
        if old is None:
            break
        del _jobs[old]


async def _run_job(job: _Job, req: PlanIn) -> None:
    try:
        job.result = await run_plan(req, job.steps, JOB_DEADLINE_S)
        job.status = "done"
    except HTTPException as e:
        job.error = e.detail if isinstance(e.detail, str) else "The planner could not run this request"
        job.status = "error"
    except Exception:  # noqa: BLE001 — a background job must always end in a state the panel can show
        log.exception("planner job failed")
        job.error = "The planner failed on this request. Try again, or try a different size."
        job.status = "error"
    finally:
        job.task = None


# ---------------------------------------------------------------------------------- routes
@router.post("/api/planner")
@limiter.limit("30/minute")
async def plan(request: Request, body: PlanIn):
    """The whole plan in one response (can take up to ~AI_DEADLINE_S when Gemini plans)."""
    await run_in_threadpool(_validate, body)
    return await run_plan(body, [])


@router.post("/api/planner/start")
@limiter.limit("30/minute")
async def start_plan(request: Request, body: PlanIn):
    """Start a plan in the background; poll GET /api/planner/jobs/{id} for its steps and result."""
    await run_in_threadpool(_validate, body)
    with _jobs_lock:
        _gc_jobs()
        if sum(1 for j in _jobs.values() if j.status == "running") >= RUNNING_MAX:
            raise HTTPException(status_code=429, detail="The planner is busy with other plans — try again in a moment")
        job = _Job()
        _jobs[job.id] = job
    job.task = asyncio.create_task(_run_job(job, body))
    return {"id": job.id, "status": job.status}


@router.get("/api/planner/jobs/{job_id}")
def plan_job(job_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_-]{8,40}$")):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="This plan is no longer available — run it again")
    out = {"id": job.id, "status": job.status, "steps": list(job.steps)}
    if job.status == "done":
        out["result"] = job.result
    elif job.status == "error":
        out["error"] = job.error
    return out
