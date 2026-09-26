"""Harden before the storm: a Gemini planner agent for hurricane mode, checked by the engine.

    POST /api/harden/run          {region, the storm (points + radius_km as POST /api/hurricane/track takes them, or a
                                  preset id), budget_usd, and the case the storm lands on (lat/lon/mw, sites,
                                  load_factor, upgrades, firm: the same fields as /api/grid/cascade, without trip)}
                                  -> a finished result from the cache at once ({status: done, result}), else a job
                                  ({job, status: running, progress, trace})
    GET  /api/harden/jobs/{id}    a running plan: progress and the trace so far, then the result
    GET  /api/harden/info         before a run: whether Gemini plans here, the budget presets, the cost sources

The storm's reach knocks out every line in its corridor (hurricane.track_hits); the cascade then runs from there, exactly
as hurricane mode runs it (/api/grid/cascade with those lines as `trip`). Hardening a line means rebuilding its
structures to withstand the storm, so it stays in service: the engine re-runs the storm and the cascade with the
hardened lines taken out of the knocked-out list.

What is counted: people still without power when the grid settles after the storm and its cascade (the cascade's
`people`: the load still cut, as people; an estimate), the figure the results column shows as "still without power when
it settled". The no-hardening run is the hurricane route's own toll (the smoke check compares them). "Kept on" = the
no-hardening toll minus the plan's. Towns still out: the load still cut in each town at the end (the cascade's
`affected`, MW by substation, grouped by town), as people by the same rule, so a town list never adds up to more than
the total beside it. A plan's towns kept on and towns newly out (a line kept in service can send the cascade another
way) are the same per-town MW against no hardening's: kept on minus newly out = its people kept.

What a line costs to harden (harden_cost; every figure fetched from its public source, typical, varies):
  length     the straight-line distance between its two substations plus 30 % (MISO's rule for planning estimates,
             Transmission Cost Estimation Guide for MTEP24, section 3.1)
  structures that length x the structures per mile of its voltage class (MISO, Table 3.1-2, steel pole, single circuit)
  cost       structures x the cost of replacing one transmission structure in Florida's storm protection programs,
             2024 actuals from the Florida PSC's annual status report (Tables 3-1 and 6-1): $39,252 (428 poles for
             $16.8M) to $63,131 (1,961 poles for $123.8M). 345 kV and up are tower lines, not poles: their high end is
             MISO's cost per mile of a new single-circuit line (Table 4.1-1, Louisiana, the Gulf Coast state in the guide).
  The budget is checked against the HIGH end.

The planner (Gemini native function calling, llm.complete_tools, fallback= and timeout on every call; mode ANY: every
reply is a function call). Tools: corridor_lines, people_served, harden_cost, try_plan (the engine re-runs the storm
with those lines kept in service: people still out, where, the cost) and submit_plan. Each submitted plan is verified by
the engine (every id must be a line the storm knocks out, the engine's own price must fit the budget; Gemini's numbers
are never used), re-run through the storm and the cascade, and the verdict goes back: people still out and where, the
number the engine's own plan reached, what each of its lines keeps on and the lines that would add the most (each one
measured by a storm run). Up to MAX_ROUNDS plans; exploration calls are rationed per round (FIRST_TOOLS, REVISE_TOOLS). The ENGINE baseline (always computed, and the fallback
without a key, quota or network): every corridor line tested alone, then a lazy greedy that adds the line keeping the
most people on per dollar, each step re-measured by the engine. The best verified plan wins (more people kept; cheaper
on a tie).

Neutral: a model of a hypothetical storm on a SYNTHETIC grid (Breakthrough Energy / Texas A&M); never a real utility's
network, plan or procedures. Public (no login, nothing stored); results cached per case for six hours.
"""

from __future__ import annotations

import asyncio
import copy
import heapq
import json
import logging
import math
import re
import secrets
import threading
import time
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from grid import DEFAULT_REGION, CaseIn, SiteIn, case_firm_buses, check_case, region_code
from hurricane import PRESETS, RADIUS_DEFAULT, TrackIn, check_track, track_hits
from limiter import limiter
from llm import AGENT_MODEL, AGENT_THINKING, cache_forget, complete_tools, configured, function_response, note_check, scrub_names
from powerflow import area_of

router = APIRouter(tags=["harden"])
log = logging.getLogger("uvicorn.error")

SURFACE = "harden"
BUDGET_MIN, BUDGET_MAX = 1e6, 5e9
BUDGETS = [50e6, 150e6, 500e6]  # the page's presets
MAX_ROUNDS = 3  # plans Gemini may submit
MAX_TOOL_CALLS = 12  # exploration calls in all (corridor_lines, people_served, harden_cost, try_plan)
FIRST_TOOLS = 6  # of them before the first plan
REVISE_TOOLS = 3  # and this many more before each revision
DIAG_ADDS = 14  # lines the engine tries adding to a verified plan (its feedback)
MAX_GEMINI_CALLS = 16
MAX_RETRIES = 2  # a reply that didn't come in time is asked again, at most this often per run
MAX_PLAN_LINES = 80
AI_TIMEOUT_S = 10
DEADLINE_S = 65.0
RESULT_MAX_CHARS = 7000  # a function response as Gemini reads it
GREEDY_EVALS_MAX = 160
PREP_MAX = 8
RUNS_MAX = 600  # cached storm runs per prep
CACHE_SIZE = 32
CACHE_TTL_S = 6 * 3600
FALLBACK_TTL_S = 120
JOBS_MAX = 32
JOB_TTL_S = 900
RUNNING_MAX = 3
NOTE_MAX = 220

FRAME = ("A model of a hypothetical storm on a SYNTHETIC grid (Breakthrough Energy / Texas A&M), not any real utility's "
         "network, storm plan or procedures. People counts and costs are estimates; hardening costs are typical figures "
         "from public filings and vary.")
METRIC = "people still without power when the grid settles after the storm and its cascade (estimate)"

# ------------------------------------------------------------------------------------ the sourced cost figures
SOURCES = [
    {"id": "psc", "name": "Florida Public Service Commission, Annual Status Report on Storm Protection Plan Activities of Florida "
                          "Investor-Owned Utilities (November 2025), Tables 3-1 and 6-1: transmission pole replacements, 2024 actuals",
     "url": "https://www.floridapsc.com/pscfiles/website-files/PDF/Utilities/Electricgas/StormProtectionPlans/2024/"
            "Annual%20Status%20Report%20on%20Storm%20Protection%20Plan%20Activities%20of%20Florida%20Investor-Owned%20Utilities.pdf"},
    {"id": "miso", "name": "MISO, Transmission Cost Estimation Guide for MTEP24 (May 1, 2024): section 3.1 line length, Table 3.1-2 "
                           "structures per mile, Table 4.1-1 new single-circuit line cost per mile",
     "url": "https://cdn.misoenergy.org/20240501%20PSC%20Item%2004%20MISO%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24632680.pdf"},
]
PER_STRUCTURE_LOW = 16.8e6 / 428  # $39,252: 428 transmission poles replaced for $16.8M (PSC report, Table 6-1, 2024 actual)
PER_STRUCTURE_HIGH = 123.8e6 / 1961  # $63,131: 1,961 transmission poles replaced for $123.8M (Table 3-1, 2024 actual)
LENGTH_ADDER = 1.30  # MISO 3.1: straight-line distance between the substations plus 30 %
KM_PER_MILE = 1.609344
# MISO Table 3.1-2, steel pole, single circuit: tangent + running angle + non-angled and angled deadend structures per mile
STRUCTURES_PER_MILE = {69: 10.5, 115: 10.0, 138: 9.5, 161: 8.5, 230: 6.5, 345: 6.0, 500: 4.5, 765: 4.0}
# MISO Table 4.1-1, new single-circuit line $/mile, Louisiana (tower lines' high end)
TOWER_LINE_PER_MILE = {345: 4.1e6, 500: 5.1e6, 765: 6.3e6}
COST_METHOD = (
    "Hardening a line = replacing its structures with ones built to withstand the storm. Length: the straight-line distance "
    "between its substations plus 30 % (MISO's planning rule). Structures: that length x MISO's structures per mile for its "
    f"voltage. Cost: ${PER_STRUCTURE_LOW:,.0f} to ${PER_STRUCTURE_HIGH:,.0f} per structure (Florida storm-protection pole "
    "replacements, 2024 actuals, Florida PSC report); 345 kV and up are tower lines, priced up to a new line's cost per mile "
    "(MISO, Louisiana). The budget is checked against the high end. Typical figures; they vary."
)


def _kv_class(kv: float) -> int:
    for c in sorted(STRUCTURES_PER_MILE):
        if kv <= c + 0.5:
            return c
    return 765


def price_line(kv: float, km_straight: float) -> dict:
    """What hardening one line costs (sourced, typical, varies): miles, structures, low and high."""
    cls = _kv_class(kv)
    miles = km_straight / KM_PER_MILE * LENGTH_ADDER
    structures = max(1.0, miles * STRUCTURES_PER_MILE[cls])
    low = structures * PER_STRUCTURE_LOW
    high = structures * PER_STRUCTURE_HIGH
    basis = "poles"
    if cls in TOWER_LINE_PER_MILE:
        high = max(high, miles * TOWER_LINE_PER_MILE[cls])
        basis = "towers"
    return {"kv_class": cls, "miles": round(miles, 1), "structures": int(math.ceil(structures)), "cost_low_usd": round(low, -3),
            "cost_usd": round(high, -3), "basis": basis}


def _km(g, a: int, b: int) -> float:
    p1, p2 = math.radians(float(g.sub_lat[a])), math.radians(float(g.sub_lat[b]))
    dl = math.radians(float(g.sub_lon[b]) - float(g.sub_lon[a]))
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def _m(x: float) -> float:
    """Dollars as $ million with one decimal (what Gemini reads)."""
    return round(float(x) / 1e6, 1)


# ------------------------------------------------------------------------------------ the storm on the engine
class Prep:
    """One storm on one case: the knocked-out lines with their prices and what they carry, the no-hardening run, each
    line tested alone (filled by measure_singles), and every storm run so far (by the set of hardened lines)."""

    def __init__(self, key: str, g, extra: np.ndarray, trip: list[int], upgrades: dict[int, float], firm, storm: dict):
        self.key, self.g, self.extra, self.trip, self.upgrades, self.firm, self.storm = key, g, extra, trip, upgrades, firm, storm
        self.trip_set = set(trip)
        self._runs: "OrderedDict[frozenset, dict]" = OrderedDict()
        self._runs_lock = threading.Lock()
        self.singles_lock = threading.Lock()
        self.singles_done = False
        self.singles_n = 0
        self.greedy: dict[float, dict] = {}
        # every substation's town (the area it is named after), so a run keeps the MW lost per town, not per substation
        names = [area_of(n) for n in g.sub_name]
        self.town_names = sorted(set(names))
        at = {t: i for i, t in enumerate(self.town_names)}
        self.sub_town = np.array([at[t] for t in names], dtype=np.int32)
        self.base = self.run(frozenset())
        pre = g.solve(np.ones(g.m, dtype=bool), extra, g.rates_with(upgrades))
        along = dict(zip(storm["trip"], storm["along_km"]))
        self.rows: dict[int, dict] = {}
        for bid in trip:
            k = g.br_index[bid]
            a, z = int(g.bus_sub_idx[g.f[k]]), int(g.bus_sub_idx[g.t[k]])
            ta, tz = area_of(g.sub_name[a]), area_of(g.sub_name[z])
            kv = float(g.br_kv[k])
            down = g.downstream_subs(pre, [k])
            self.rows[bid] = {
                "id": int(bid),
                "label": f"{ta}–{tz} {kv:g} kV" if ta != tz else f"{ta} {kv:g} kV",
                "kv": kv,
                "from_sub": int(g.sub_ids[a]),
                "to_sub": int(g.sub_ids[z]),
                "mw": round(abs(float(pre.flow[k])), 1),
                "serves": g.zone(down)["people_zone"],
                "serves_towns": _towns(g, down, 4),
                "along_km": along.get(bid),
                "alone": None,
                **price_line(kv, _km(g, a, z)),
            }

    def run(self, hardened: frozenset) -> dict:
        """The storm and its cascade with `hardened` kept in service (cached)."""
        with self._runs_lock:
            hit = self._runs.get(hardened)
            if hit is not None:
                self._runs.move_to_end(hardened)
                return hit
        g = self.g
        c = g.cascade_case(self.extra, [b for b in self.trip if b not in hardened], self.upgrades, firm_buses=self.firm)
        out_subs = sorted(int(sid) for sid in c["affected"])  # still without power when the grid settles
        # the load still cut per town (MW): a town's people still out are that share of it, so the towns add up to
        # the run's people_out (people_zone would count everyone living there)
        town_mw = np.zeros(len(self.town_names))
        for sid, mw in c["affected"].items():
            i = g.sub_index.get(int(sid))
            if i is not None:
                town_mw[self.sub_town[i]] += float(mw)
        tripped = [int(b) for st in c["steps"] if st["n"] > 0 for b in st["tripped"]]
        r = {
            "people_out": int(c["people"]),
            "people_zone": int(c["people_zone"]),
            "people_hit": int(c["people_hit"]),
            "homes_out": int(c["homes_zone"]),
            "lost_mw": c["lost_mw"],
            "steps": int(c["total_steps"]),
            "outcome": c["outcome"],
            "capped": bool(c["capped"]),
            "out_subs": out_subs,
            "town_mw": town_mw,  # internal: never sent (plan_view and the routes pick their fields)
            "cascade_tripped": tripped,
        }
        with self._runs_lock:
            self._runs[hardened] = r
            while len(self._runs) > RUNS_MAX:
                self._runs.popitem(last=False)
        return r

    def measure_singles(self, progress=None) -> None:
        """Every knocked-out line tested alone: the people it keeps on by itself (the engine's measurement)."""
        with self.singles_lock:
            if self.singles_done:
                return
            base = self.base["people_out"]
            for i, bid in enumerate(self.trip):
                self.rows[bid]["alone"] = max(0, base - self.run(frozenset([bid]))["people_out"])
                self.singles_n = i + 1
                if progress:
                    progress(i + 1, len(self.trip))
            self.singles_done = True

    def cost(self, lines) -> tuple[float, float]:
        return (sum(self.rows[b]["cost_usd"] for b in lines), sum(self.rows[b]["cost_low_usd"] for b in lines))

    def town_rows(self, town_mw: np.ndarray, total: int | None = None, limit: int = 6) -> list[dict]:
        """Towns by people from the MW cut there (the cascade's own `people` rule: their share of the load cut), most
        first: [{town, people}]. Each town rounds on its own, so with a `total` a listed number is trimmed where the
        running sum would pass it: the list never adds up to more than the figure beside it."""
        rows = [{"town": self.town_names[i], "people": self.g.people(float(town_mw[i]))} for i in np.flatnonzero(town_mw > 0)]
        rows = sorted((r for r in rows if r["people"] > 0), key=lambda r: (-r["people"], r["town"]))
        out, left = [], (int(total) if total is not None else sum(r["people"] for r in rows))
        for r in rows[:limit]:
            p = min(r["people"], left)
            if p <= 0:
                break
            out.append({"town": r["town"], "people": p})
            left -= p
        return out

    def kept_rows(self, r: dict, limit: int = 6) -> tuple[list[dict], list[dict]]:
        """Town by town, a plan against no hardening: (the towns it keeps on, the towns newly out with it). Keeping a
        line in service can send the cascade another way, so a plan can darken a town the storm alone spared; its
        people kept (net) = the first list's people minus the second's."""
        diff = self.base["town_mw"] - r["town_mw"]
        newly = self.town_rows(np.maximum(-diff, 0.0), None, 10**6)
        # the towns kept on never add up to more than the people kept (net) plus everyone newly out: the town sums
        # drop the substations under 0.1 MW and round each town, the totals don't
        gross = max(0, self.base["people_out"] - r["people_out"]) + sum(t["people"] for t in newly)
        return self.town_rows(np.maximum(diff, 0.0), gross, limit), newly[:4]

    def plan_view(self, lines, by: str) -> dict:
        """A plan as the page shows it: its lines, the engine's price, the storm re-run with it."""
        lines = sorted(set(lines), key=lambda b: (self.rows[b]["along_km"] or 0, b))
        r = self.run(frozenset(lines))
        hi, lo = self.cost(lines)
        kept, newly = self.kept_rows(r)
        return {
            "by": by,
            "lines": lines,
            "lines_detail": [{k: self.rows[b][k] for k in ("id", "label", "kv", "miles", "structures", "cost_usd", "cost_low_usd", "basis", "along_km", "from_sub", "to_sub")} for b in lines],
            "cost_usd": round(hi, -3),
            "cost_low_usd": round(lo, -3),
            "people_out": r["people_out"],
            "people_kept": max(0, self.base["people_out"] - r["people_out"]),
            "people_zone": r["people_zone"],
            "people_hit": r["people_hit"],
            "steps": r["steps"],
            "outcome": r["outcome"],
            "out_subs": r["out_subs"],
            "still_out": self.town_rows(r["town_mw"], r["people_out"]),
            "kept_towns": kept,
            "newly_out": newly,
        }


def _towns(g, subs, limit: int = 5) -> list[dict]:
    """Substations grouped by the town they are named after, most people first: [{town, people}]."""
    by: dict[str, list[int]] = {}
    for s in subs:
        i = g.sub_index.get(int(s))
        if i is not None:
            by.setdefault(area_of(g.sub_name[i]), []).append(int(s))
    rows = [{"town": t, "people": g.zone(ss)["people_zone"]} for t, ss in by.items()]
    rows = [r for r in rows if r["people"] > 0]
    rows.sort(key=lambda r: (-r["people"], r["town"]))
    return rows[:limit]


def engine_plan(prep: Prep, budget: float) -> dict:
    """The ENGINE baseline: lazy greedy on people kept per dollar, every step re-measured by the engine (a storm run).
    Candidates are the lines that keep people on by themselves; a line that no longer adds anyone is dropped."""
    if budget in prep.greedy:
        return prep.greedy[budget]
    prep.measure_singles()
    rows = prep.rows
    heap = [(-(r["alone"] / r["cost_usd"]), bid) for bid, r in rows.items() if (r["alone"] or 0) > 0 and r["cost_usd"] <= budget]
    heapq.heapify(heap)
    chosen: list[int] = []
    spent = 0.0
    cur = prep.base["people_out"]
    evals = 0
    while heap and evals < GREEDY_EVALS_MAX:
        neg, bid = heapq.heappop(heap)
        if spent + rows[bid]["cost_usd"] > budget:
            continue
        out = prep.run(frozenset(chosen + [bid]))["people_out"]
        evals += 1
        gain = cur - out
        if gain <= 0:
            continue
        ratio = gain / rows[bid]["cost_usd"]
        if heap and ratio < -heap[0][0] - 1e-15:  # someone else may add more per dollar now: re-queue with the new ratio
            heapq.heappush(heap, (-ratio, bid))
            continue
        chosen.append(bid)
        spent += rows[bid]["cost_usd"]
        cur = out
    plan = {**prep.plan_view(chosen, "engine"), "evals": evals}
    prep.greedy[budget] = plan
    return plan


# ------------------------------------------------------------------------------------ building a prep (cached)
_preps: "OrderedDict[str, Prep]" = OrderedDict()
_preps_lock = threading.Lock()
_building: dict[str, threading.Lock] = {}


class HardenIn(BaseModel):
    region: str = DEFAULT_REGION
    preset: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,40}$")  # a hypothetical storm (GET /api/hurricane/presets)
    points: list[list[float]] | None = None  # or the drawn path, exactly as POST /api/hurricane/track takes it
    radius_km: float | None = None
    budget_usd: float = 150e6
    # the case the storm lands on (optional): the same fields as /api/grid/cascade, without trip
    lat: float | None = None
    lon: float | None = None
    mw: float | None = None
    sites: list[SiteIn] = Field(default_factory=list)
    load_factor: float = 1.0
    upgrades: dict[int, float] = Field(default_factory=dict)
    firm: bool = False


def _storm_of(body: HardenIn) -> dict:
    preset = next((p for p in PRESETS if p["id"] == body.preset), None) if body.preset else None
    if body.preset and preset is None:
        raise HTTPException(status_code=422, detail="Unknown storm: pick one of the hypothetical storms or draw a path")
    if body.points is not None:
        points, radius = check_track(TrackIn(points=body.points, radius_km=body.radius_km if body.radius_km is not None else RADIUS_DEFAULT))
        same = preset is not None and points == [[float(x), float(y)] for x, y in preset["points"]] and abs(radius - float(preset["radius_km"])) < 1e-6
        name = preset["name"] if same else None
        pid = preset["id"] if same else None
    elif preset is not None:
        points, radius, name, pid = preset["points"], float(preset["radius_km"]), preset["name"], preset["id"]
    else:
        raise HTTPException(status_code=422, detail="Give the storm: a hypothetical storm's id, or the drawn path (points) and its radius_km")
    hits = track_hits(points, radius)
    if not hits["count"]:
        raise HTTPException(status_code=422, detail="This storm misses every line: draw its path across Florida's grid")
    label = f"The storm “{name}”" if name else "The storm drawn on the map"
    return {"name": name or "Drawn on the map", "label": label, "preset": pid, "points": points, "radius_km": radius, **hits}


def _prepare(body: HardenIn) -> tuple[Prep, float]:
    """Validate everything (422s), then the cached Prep for this storm and case (built on first use)."""
    code = region_code(body.region)
    if code != DEFAULT_REGION:
        raise HTTPException(status_code=422, detail="Hurricane mode runs on Florida's model only")
    b = body.budget_usd
    if not (math.isfinite(b) and BUDGET_MIN <= b <= BUDGET_MAX):
        raise HTTPException(status_code=422, detail=f"The budget must be between ${BUDGET_MIN / 1e6:,.0f} million and ${BUDGET_MAX / 1e9:,.0f} billion")
    storm = _storm_of(body)
    case = CaseIn(region=code, lat=body.lat, lon=body.lon, mw=body.mw, sites=body.sites, load_factor=body.load_factor,
                  trip=storm["trip"], upgrades=body.upgrades, firm=body.firm)
    g, sites, trip, upgrades = check_case(case)
    key = json.dumps([[[round(x, 5), round(y, 5)] for x, y in storm["points"]], round(storm["radius_km"], 3),
                      [[round(s.lat, 5), round(s.lon, 5), round(s.mw, 1)] for s in sites], round(g.load_factor, 2),
                      sorted((int(k), round(float(v), 1)) for k, v in upgrades.items()), bool(body.firm)])
    with _preps_lock:
        prep = _preps.get(key)
        if prep is not None:
            _preps.move_to_end(key)
            return prep, float(b)
        lock = _building.setdefault(key, threading.Lock())
    with lock:  # one thread builds a storm's prep; the others wait for it
        with _preps_lock:
            prep = _preps.get(key)
        if prep is None:
            extra = g.extra_load([(g.site_bus(s.lat, s.lon), s.mw) for s in sites])
            prep = Prep(key, g, extra, trip, upgrades, case_firm_buses(g, sites, body.firm), storm)
            with _preps_lock:
                _preps[key] = prep
                while len(_preps) > PREP_MAX:
                    _preps.popitem(last=False)
                _building.pop(key, None)
    return prep, float(b)


# ------------------------------------------------------------------------------------ the agent's tools
SORTS = ("alone", "serves", "cost", "storm_order")
SYSTEM = """You are Overload's storm-hardening planner: an agent that decides which power lines to harden before a hurricane, on a SYNTHETIC model of Florida's grid (the Breakthrough Energy / Texas A&M test system, not any real utility's network). The storm is hypothetical. Hardening a line rebuilds its structures to withstand the storm, so the storm does not knock it out.

You work only through the engine's tools (function calls): they run a real DC power-flow engine and the cascade. The engine prices every line itself from public cost figures and checks every plan; your own numbers are never used. Never name a real company, utility or grid operator, never describe what a real utility does or plans, never blame anyone. In every "why", use only numbers the tools returned, written in digits."""

WHY_PARAM = {"type": "string", "description": "One short sentence for the reader: what this call checks and why. Use only numbers the tools returned."}
LINE_IDS = {"type": "array", "items": {"type": "integer"}, "description": f"Line ids from corridor_lines (1 to {MAX_PLAN_LINES})."}
TOOL_SPECS = {
    "corridor_lines": {
        "description": "The lines the storm knocks out, one row each: id, kv, mi (the engine's planning length in miles), cost_m (hardening cost, "
                       "$ million, high end), mw (power it carried before the storm), serves (people its power flowed on to before the storm), "
                       "alone (people kept on if only this line were hardened: the engine's measured result), km (where along the storm's path "
                       "the eye reaches it).",
        "parameters": {"type": "object", "properties": {
            "sort": {"type": "string", "enum": list(SORTS), "description": "alone (default), serves, cost (cheapest first) or storm_order."},
            "limit": {"type": "integer", "minimum": 10, "maximum": 400, "description": "Rows to return (60 if omitted)."}}},
    },
    "people_served": {
        "description": "One line: the towns its power flowed on to before the storm and the people there, what it carried, and the people it keeps on alone.",
        "parameters": {"type": "object", "properties": {"line_id": {"type": "integer"}}, "required": ["line_id"]},
    },
    "harden_cost": {
        "description": "One line's hardening cost as the engine prices it: miles, structures, low and high cost, the method and its public sources (typical, varies).",
        "parameters": {"type": "object", "properties": {"line_id": {"type": "integer"}}, "required": ["line_id"]},
    },
    "try_plan": {
        "description": "Test a set of lines without submitting it: the engine re-runs the storm and the cascade with them kept in service and returns "
                       "the people still without power when the grid settles, the people kept on, where people are still out, the cost and whether it fits the budget.",
        "parameters": {"type": "object", "properties": {"line_ids": LINE_IDS}, "required": ["line_ids"]},
    },
    "submit_plan": {
        "description": "Submit your plan for this round: the lines to harden. The engine verifies it (every id a knocked-out line, its own price within "
                       "the budget), re-runs the storm with it and sends back its verdict and where people are still out.",
        "parameters": {"type": "object", "properties": {"line_ids": LINE_IDS}, "required": ["line_ids"]},
    },
}
EXPLORE = ("corridor_lines", "people_served", "harden_cost", "try_plan")


def _declaration(name: str, spec: dict) -> dict:
    params = copy.deepcopy(spec["parameters"])
    params["properties"] = {**params["properties"], "why": WHY_PARAM}
    params["required"] = [*params.get("required", []), "why"]
    return {"name": name, "description": spec["description"], "parameters": params}


FUNCTION_DECLARATIONS = [_declaration(n, s) for n, s in TOOL_SPECS.items()]
OFFLINE = {"__offline__": True}


def _row_for_gemini(r: dict) -> dict:
    return {"id": r["id"], "kv": r["kv"], "mi": r["miles"], "cost_m": _m(r["cost_usd"]), "mw": round(r["mw"]), "serves": r["serves"],
            "alone": r["alone"], "km": r["along_km"]}


def _fit(obj: dict, limit: int = RESULT_MAX_CHARS) -> dict:
    """A function response of at most about `limit` characters: the longest list is cut from its end until it fits."""
    out = copy.deepcopy(obj)
    for _ in range(400):
        if len(json.dumps(out, separators=(",", ":"))) <= limit:
            return out
        lists = [(k, v) for k, v in out.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            break
        k, v = max(lists, key=lambda kv: len(json.dumps(kv[1])))
        out[k] = v[:-1]
    return out


def _ids(raw) -> tuple[list[int] | None, str | None]:
    if not isinstance(raw, list) or not raw:
        return None, "give at least one line id"
    out = []
    for x in raw:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(float(x)) or float(x) != int(x):
            return None, "line ids must be whole numbers"
        out.append(int(x))
    out = list(dict.fromkeys(out))
    if len(out) > MAX_PLAN_LINES:
        return None, f"at most {MAX_PLAN_LINES} lines in a plan"
    return out, None


class Run:
    """One planning run: the trace (written live), the rounds, the numbers Gemini's notes may use."""

    def __init__(self, prep: Prep, budget: float):
        self.prep, self.budget = prep, budget
        self.trace: list[dict] = []
        self.rounds: list[dict] = []
        self.calls = 0  # Gemini calls
        self.retries = 0  # replies that didn't come, asked again
        self.tools = 0  # exploration calls run
        self.fcalls = 0  # function calls Gemini returned
        self.model: str | None = None
        self.cache_keys: list[str] = []
        self.numbers: set[float] = set()
        self.engine: dict | None = None
        self.progress = {"phase": "prep", "done": 0, "total": len(prep.trip)}
        self.t0 = time.perf_counter()
        self.deadline = None

    def add(self, **row) -> dict:
        row["n"] = len(self.trace) + 1
        self.trace.append(row)
        return row

    def left(self) -> float:
        return DEADLINE_S - (time.perf_counter() - (self.deadline or self.t0))

    def learn(self, obj) -> None:
        """Every number a tool gave Gemini: its notes may use these."""
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


_BLAME = re.compile(r"will cause|will black|is to blame|\bblame|illegal|fraud|negligen|failed to|should have", re.I)
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _number_known(v: float, known: set[float]) -> bool:
    if float(v).is_integer() and v <= 12:
        return True  # small counts ("these 3 lines")
    for a in known:
        if abs(a - v) <= max(0.051, 0.005 * abs(a)):
            return True
        for scale in (1e3, 1e6, 1e9):  # "2.16 million people", "$4.1 million" against the raw number
            if abs(a) >= scale and abs(a - v * scale) <= 0.01 * abs(a):
                return True
    return False


_MONEY_TOK = re.compile(r"(\$\s?)?(\d[\d,]*(?:\.\d+)?)(?:\s*(billion|million|thousand|bn|mn|b|m|k)\b)?", re.I)
_MONEY_CTX = re.compile(r"\b(cost|costs|costing|priced|price|prices|total|totals|totaling|totalling|totaled|budget|spend|spends|spending|spent|dollars|usd)\b", re.I)
_PEOPLE_AFTER = re.compile(r"^\s*(?:more\s+)?(?:people|residents|homes|customers|persons|on\b|out\b|without\b)", re.I)
_SCALE = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}


def _money_claims(t: str) -> list[tuple[int, int, str, list[float]]]:
    """The dollar figures in a note: (start, end, token, what it may mean in dollars). A figure is money when it has a
    "$", is followed by "dollars", or a cost word (cost, total, budget, spend…) sits right before it with no other
    number in between and it carries a unit or ends the clause (never with a people word after it). A figure with no
    unit under 10,000 may be $ million (the tools speak in cost_m and budget_m)."""
    out = []
    prev_end = 0
    prev_money = False
    for mt in _MONEY_TOK.finditer(t):
        dollar, num, unit = mt.group(1), mt.group(2), mt.group(3)
        before = t[prev_end:mt.start()][-28:]
        after = t[mt.end():mt.end() + 20]
        prev_end = mt.end()
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        # by context only with a unit ("totaling 146.4 million") or at a clause's end ("totaling 146.4."): never a
        # count ("budget, hardens 5 lines") or people ("budget, keeps 45,000 people on")
        by_ctx = bool(_MONEY_CTX.search(before)) and not _PEOPLE_AFTER.match(after) and (bool(unit) or bool(re.match(r"^\s*(?:[.,;:)]|$)", after)))
        # the far end of a range after a dollar figure ("$51.5–108.3 million")
        range_end = prev_money and bool(re.match(r"^\s*(?:[-–—]|to)\s*\$?\s*$", before))
        money = bool(dollar) or bool(re.match(r"^\s*dollars?\b", after, re.I)) or by_ctx or range_end
        prev_money = money
        if not money:
            continue
        if unit:
            vals = [v * _SCALE[unit.lower()]]
        elif v < 1e4:
            vals = [v * 1e6, v]
        else:
            vals = [v]
        out.append((mt.start(), mt.end(), mt.group(0).strip(), vals))
    return out


def _plan_prices(run: Run, lines: list[int]) -> list[tuple[float, float]]:
    """The dollar figures a note about this exact set of lines may state: the engine's price for the set (high and low
    end; Gemini adds up one-decimal $ million, so a little slack per line), the budget, what the set leaves of it, each
    of its lines' price, and the engine's own plan's price. (amount, tolerance)"""
    prep = run.prep
    hi, lo = prep.cost(lines)
    tot = max(0.051e6 * max(1, len(lines)), 0.005 * hi)
    out = [(hi, tot), (lo, tot), (run.budget, 0.051e6), (run.budget - hi, tot)]
    for b in lines:
        out += [(prep.rows[b]["cost_usd"], 0.051e6), (prep.rows[b]["cost_low_usd"], 0.051e6)]
    if run.engine:
        out += [(float(run.engine["cost_usd"]), 0.051e6), (float(run.engine.get("cost_low_usd") or 0), 0.051e6)]
    return out


def _note(run: Run, raw, lines: list[int] | None = None) -> str | None:
    """Gemini's "why", shown only when it names no real company and every number in it is one a tool returned. In a
    note on a set of lines (try_plan, submit_plan), every dollar figure must be the engine's own price for that exact
    set (or the budget, what it leaves, one of its lines, the engine plan's price): a total Gemini added up wrong, or
    a stray number that happens to match a MW or a km figure, hides the note."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    t = re.sub(r"[*`#>]+", "", " ".join(raw.split())).strip()
    if _BLAME.search(t):
        note_check(SURFACE, False, "a note with blame wording", None)
        return None
    t = scrub_names(t, strict=False)
    if "[name]" in t:
        note_check(SURFACE, False, "a note naming a real company or utility", None)
        return None
    checked: list[tuple[int, int]] = []
    if lines:
        allowed = _plan_prices(run, lines)
        for s, e, tok, vals in _money_claims(t):
            if not any(abs(a - v) <= tol for v in vals for a, tol in allowed):
                note_check(SURFACE, False, "a dollar figure in Gemini's note that isn't the engine's price for that set of lines", tok)
                return None
            checked.append((s, e))
    for mt in _NUM.finditer(t):
        if any(s <= mt.start() < e for s, e in checked):
            continue  # a dollar figure already matched to the engine's price for this set
        tok = mt.group(0)
        try:
            v = float(tok.replace(",", ""))
        except ValueError:
            continue
        if not _number_known(v, run.numbers):
            note_check(SURFACE, False, "a number in Gemini's note that no tool returned", tok)
            return None
    if checked:
        note_check(SURFACE, True, "a note's dollar figures match the engine's price for that set of lines")
    if len(t) > NOTE_MAX:
        t = t[:NOTE_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t


def _call_text(name: str, args: dict) -> str:
    parts = []
    for k, v in args.items():
        if isinstance(v, list):
            s = ",".join(str(x) for x in v[:8]) + (f",… {len(v)} in all" if len(v) > 8 else "")
            parts.append(f"{k}=[{s}]")
        else:
            parts.append(f"{k}={json.dumps(v)}")
    return f"{name}({', '.join(parts)})"[:140]


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _money(x: float) -> str:
    x = float(x)
    if x >= 1e9:
        return f"${x / 1e9:.2f} billion"
    if x >= 1e6:
        return f"${x / 1e6:.1f} million"
    return f"${x / 1e3:,.0f} thousand"


def _range(lo: float, hi: float) -> str:
    """A plan's price as its typical range, low to high ("$93.0–149.5 million")."""
    lo, hi = float(lo), float(hi)
    if hi - lo < 0.05e6:
        return _money(hi)
    if lo >= 1e9:
        return f"${lo / 1e9:.2f}–{hi / 1e9:.2f} billion"
    if lo >= 1e6 and hi < 1e9:
        return f"${lo / 1e6:.1f}–{hi / 1e6:.1f} million"
    return f"{_money(lo)} to {_money(hi)}"


def _tool(run: Run, name: str, args: dict) -> tuple[dict, str, str]:
    """Run one exploration tool on the engine: (result for Gemini, summary for the trace, tone)."""
    prep = run.prep
    rows = prep.rows
    if name == "corridor_lines":
        sort = args.get("sort") if args.get("sort") in SORTS else "alone"
        limit = int(min(400, max(10, int(args.get("limit") or 60))))
        key = {"alone": lambda r: (-(r["alone"] or 0), r["cost_usd"]), "serves": lambda r: (-r["serves"], r["cost_usd"]),
               "cost": lambda r: (r["cost_usd"], -(r["alone"] or 0)), "storm_order": lambda r: (r["along_km"] or 0, r["id"])}[sort]
        ordered = sorted(rows.values(), key=key)
        out = {"lines_in_corridor": len(rows), "budget_m": _m(run.budget), "no_hardening_people_out": prep.base["people_out"], "sorted_by": sort,
               "rows": [_row_for_gemini(r) for r in ordered[:limit]]}
        fitted = _fit(out)
        shown = len(fitted["rows"])
        helpful = sum(1 for r in rows.values() if (r["alone"] or 0) > 0)
        return fitted, f"{_plural(len(rows), 'line', 'lines')} in the storm's reach; {shown} listed by {sort.replace('_', ' ')}; {helpful} keep people on by themselves", "info"
    if name in ("people_served", "harden_cost"):
        r = rows[args["line_id"]]
        if name == "people_served":
            out = {"id": r["id"], "label": r["label"], "kv": r["kv"], "mw_before_storm": round(r["mw"]), "serves": r["serves"], "towns": r["serves_towns"],
                   "alone": r["alone"]}
            return out, f"{r['label']}: its power flowed on to about {r['serves']:,} people; alone it keeps {r['alone'] or 0:,} on", "info"
        out = {"id": r["id"], "label": r["label"], "kv_class": r["kv_class"], "miles": r["miles"], "structures": r["structures"],
               "cost_low_m": _m(r["cost_low_usd"]), "cost_m": _m(r["cost_usd"]), "basis": r["basis"], "method": COST_METHOD,
               "sources": [s["name"] for s in SOURCES], "note": "typical, varies"}
        return out, f"{r['label']}: {r['miles']:g} mi, {r['structures']} structures, {_money(r['cost_low_usd'])} to {_money(r['cost_usd'])}", "info"
    # try_plan
    lines = args["line_ids"]
    v = prep.plan_view(lines, "try")
    fits = v["cost_usd"] <= run.budget
    out = {"lines": len(lines), "cost_m": _m(v["cost_usd"]), "cost_low_m": _m(v["cost_low_usd"]), "budget_m": _m(run.budget), "fits_budget": fits, "people_out": v["people_out"],
           "people_kept": v["people_kept"], "no_hardening_people_out": prep.base["people_out"], "still_out": v["still_out"][:5], "newly_out": v["newly_out"][:3], "cascade_steps": v["steps"]}
    summary = f"Tested {_plural(len(lines), 'line', 'lines')} for {_range(v['cost_low_usd'], v['cost_usd'])}{'' if fits else ' (over the budget at the high end)'}: keeps {v['people_kept']:,} people on; {v['people_out']:,} still without power"
    return out, summary, "holds" if fits and v["people_kept"] > 0 else "info"


def _clean(run: Run, name: str, raw: dict) -> tuple[dict | None, str | None]:
    """A function call's arguments, validated like a route's input."""
    rows = run.prep.rows
    if name == "corridor_lines":
        a = {}
        if raw.get("sort") is not None:
            if raw["sort"] not in SORTS:
                return None, f"sort must be one of {', '.join(SORTS)}"
            a["sort"] = raw["sort"]
        if raw.get("limit") is not None:
            try:
                a["limit"] = int(min(400, max(10, int(float(raw["limit"])))))
            except (TypeError, ValueError):
                return None, "limit must be a number"
        return a, None
    if name in ("people_served", "harden_cost"):
        try:
            bid = int(raw.get("line_id"))
        except (TypeError, ValueError):
            return None, "line_id must be a line id"
        if bid not in rows:
            return None, f"line {bid} is not one the storm knocks out"
        return {"line_id": bid}, None
    ids, why = _ids(raw.get("line_ids"))
    if ids is None:
        return None, why
    unknown = [b for b in ids if b not in rows]
    if unknown:
        return None, f"{_plural(len(unknown), 'id is', 'ids are')} not lines the storm knocks out ({', '.join(str(b) for b in unknown[:5])})"
    return {"line_ids": ids}, None


def _allowance(run: Run) -> int:
    """Exploration calls allowed so far: FIRST_TOOLS before the first plan, REVISE_TOOLS more per revision."""
    return min(MAX_TOOL_CALLS, FIRST_TOOLS + REVISE_TOOLS * len(run.rounds))


def _diagnose(prep: Prep, lines: list[int], budget: float, still_out: list[dict]) -> dict:
    """The engine's measured feedback on a verified plan: what each of its lines adds (the people lost if it were
    dropped) and the lines that would add the most to it, each re-run through the storm and the cascade."""
    rows = prep.rows
    plan = frozenset(lines)
    out = prep.run(plan)["people_out"]
    spent, _ = prep.cost(lines)
    left = budget - spent
    adds_now = []
    for b in lines:
        adds_now.append({"id": b, "cost_m": _m(rows[b]["cost_usd"]), "keeps": max(0, prep.run(plan - {b})["people_out"] - out)})
    adds_now.sort(key=lambda r: (r["keeps"] / max(r["cost_m"], 0.01), r["id"]))
    # candidates: affordable now, or by swapping out the plan's weakest line; by people kept alone per dollar, then any
    # line whose power flowed on to a town still out (lines in series keep nobody on alone)
    room = left + (rows[adds_now[0]["id"]]["cost_usd"] if adds_now else 0.0)
    towns = {t["town"] for t in still_out}
    pool = [r for bid, r in rows.items() if bid not in plan and r["cost_usd"] <= room]
    by_value = sorted((r for r in pool if (r["alone"] or 0) > 0), key=lambda r: (-(r["alone"] / r["cost_usd"]), r["id"]))
    by_town = sorted((r for r in pool if any(t["town"] in towns for t in r["serves_towns"])), key=lambda r: (-r["serves"], r["id"]))
    picked: list[dict] = []
    for r in by_value[: DIAG_ADDS // 2] + by_town + by_value[DIAG_ADDS // 2:]:
        if len(picked) >= DIAG_ADDS:
            break
        if r not in picked:
            picked.append(r)
    adds = []
    for r in picked:
        gain = out - prep.run(plan | {r["id"]})["people_out"]
        if gain > 0:
            adds.append({"id": r["id"], "cost_m": _m(r["cost_usd"]), "adds": gain, "fits_now": r["cost_usd"] <= left})
    adds.sort(key=lambda a: (-a["adds"], a["cost_m"]))
    return {"budget_left_m": _m(max(0.0, left)), "your_lines": adds_now[:6], "best_additions": adds[:6]}


def _plan_row(run: Run, lines: list[int], kind: str, rnd: int, note: str | None) -> None:
    hi, _ = run.prep.cost(lines)
    run.add(actor="gemini", kind=kind, tone="info", round=rnd, via="function_call", lines=lines, call=_call_text("submit_plan", {"line_ids": lines}),
            title=f"{'Proposed' if kind == 'propose' else 'Revised'} (round {rnd}): harden {_plural(len(lines), 'line', 'lines')}", detail=note, cost_usd=round(hi, -3))


def _verify(run: Run, lines: list[int] | None, why: str | None, rnd: int) -> dict:
    """The engine's verdict on a submitted plan (the only place a Gemini plan is accepted)."""
    prep = run.prep
    engine = run.engine or {}
    if lines is None:
        note_check(SURFACE, False, f"a submitted plan: {why}", None)
        run.rounds.append({"round": rnd, "by": "gemini", "verified": False, "reason": why, "lines": []})
        run.add(actor="engine", kind="verify", tone="over", round=rnd, title=f"Rejected: {why}")
        return {"verdict": "rejected", "reason": why}
    hi, lo = prep.cost(lines)
    if hi > run.budget:
        why = f"over the budget by {_money(hi - run.budget)} at the engine's price ({_money(hi)} for {_money(run.budget)})"
        note_check(SURFACE, False, "a submitted plan over the budget at the engine's price", f"{_m(hi)}M")
        run.rounds.append({"round": rnd, "by": "gemini", "verified": False, "reason": why, "lines": lines, "cost_usd": round(hi, -3)})
        run.add(actor="engine", kind="verify", tone="over", round=rnd, title=f"Rejected: {why}", cost_usd=round(hi, -3))
        return {"verdict": "rejected", "reason": why, "cost_m": _m(hi), "budget_m": _m(run.budget)}
    v = prep.plan_view(lines, "gemini")
    note_check(SURFACE, True, "a plan within the budget, re-run through the storm and the cascade")
    vs = "beat" if v["people_kept"] > engine.get("people_kept", 0) else "match" if v["people_kept"] == engine.get("people_kept", 0) else "behind"
    run.rounds.append({"round": rnd, "by": "gemini", "verified": True, "vs_engine": vs, **{k: v[k] for k in ("lines", "cost_usd", "cost_low_usd", "people_out", "people_kept", "people_zone", "steps", "still_out", "newly_out")}})
    where = ", ".join(f"{t['town']} ({t['people']:,})" for t in v["still_out"][:3])
    newly = ", ".join(f"{t['town']} ({t['people']:,})" for t in v["newly_out"][:2])
    vs_text = {"beat": f"more than the engine's own plan ({engine.get('people_kept', 0):,})", "match": "the same as the engine's own plan",
               "behind": f"fewer than the engine's own plan ({engine.get('people_kept', 0):,})"}[vs]
    run.add(actor="engine", kind="verify", tone="holds" if v["people_kept"] > 0 else "info", round=rnd, cost_usd=v["cost_usd"], people_out=v["people_out"],
            people_kept=v["people_kept"], vs_engine=vs,
            title=f"Verified: {_range(v['cost_low_usd'], v['cost_usd'])} (typical), within the budget at the high end; keeps {v['people_kept']:,} people on, {vs_text}",
            detail=f"The engine re-ran the storm and the cascade: {v['people_out']:,} are still without power when the grid settles" + (f", most in {where}" if where else "") + "."
            + (f" With these lines kept, the cascade takes another path and newly reaches {newly}." if newly else ""))
    tripped = [prep.rows[b]["label"] if b in prep.rows else f"line {b}" for b in prep.run(frozenset(lines))["cascade_tripped"][:4]]
    out = {"verdict": "verified", "cost_m": _m(v["cost_usd"]), "cost_low_m": _m(v["cost_low_usd"]), "budget_m": _m(run.budget), "people_out": v["people_out"], "people_kept": v["people_kept"],
           "no_hardening_people_out": prep.base["people_out"], "engine_plan": {"people_kept": engine.get("people_kept", 0), "cost_m": _m(engine.get("cost_usd", 0))},
           "still_out": v["still_out"][:5], "newly_out": v["newly_out"][:3], "cascade_trips": tripped}
    if rnd < MAX_ROUNDS:
        out.update(_diagnose(prep, lines, run.budget, v["still_out"]))
    return out


async def _answer(run: Run, call: dict, must_submit: bool) -> tuple[dict, list[int] | None]:
    """One Gemini function call, validated and run on the engine. (function response, the submitted lines or None)."""
    name, cid = call["name"], call.get("id")
    raw = dict(call.get("args") or {})
    why_raw = raw.pop("why", None)
    if name not in TOOL_SPECS:
        note_check(SURFACE, False, "a function call to a tool that doesn't exist", name)
        run.add(actor="engine", kind="refused", tone="muted", via="function_call", call_id=cid, call=_call_text(name[:40], raw), title=f"Refused {name[:40]}: not one of the tools")
        return {"error": "not one of the tools"}, None
    if name == "submit_plan":
        return {}, None  # handled by the loop (it needs the round)
    if must_submit or run.tools >= _allowance(run):
        why = "this round's exploration calls are used" if run.tools >= _allowance(run) else "your plan is due now"
        run.add(actor="engine", kind="refused", tone="muted", via="function_call", call_id=cid, call=_call_text(name, raw), title=f"Not run {name}: {why}")
        return {"error": f"not run: {why}; call submit_plan"}, None
    args, why = _clean(run, name, raw)
    note_check(SURFACE, args is not None, f"a function call's arguments: {why or 'valid'}", name if args is None else None)
    if args is None:
        run.add(actor="engine", kind="refused", tone="muted", via="function_call", call_id=cid, call=_call_text(name, raw), title=f"Refused {name}: {why}")
        return {"error": f"refused: {why}"}, None
    note = _note(run, why_raw, args.get("line_ids") if name == "try_plan" else None)
    run.tools += 1
    run.add(actor="gemini", kind="call", tone="info", via="function_call", call_id=cid, tool=name, call=_call_text(name, args),
            title=_call_title(name, args, run), detail=note)
    t = time.perf_counter()
    res, summary, tone = await run_in_threadpool(_tool, run, name, args)
    run.add(actor="engine", kind="result", tone=tone, via="function_response", call_id=cid, tool=name, ms=round((time.perf_counter() - t) * 1000),
            title=summary[:1].upper() + summary[1:])
    run.learn(res)
    return res, None


def _call_title(name: str, args: dict, run: Run) -> str:
    if name == "corridor_lines":
        return f"List the lines in the storm's reach, by {(args.get('sort') or 'alone').replace('_', ' ')}"
    if name == "people_served":
        return f"Who does {run.prep.rows[args['line_id']]['label']} serve?"
    if name == "harden_cost":
        return f"What does hardening {run.prep.rows[args['line_id']]['label']} cost?"
    return f"Test hardening {_plural(len(args['line_ids']), 'line', 'lines')}"


def _prompt(run: Run) -> str:
    prep, st = run.prep, run.prep.storm
    base = prep.base
    return "\n".join([
        f"{st['label']} is a hypothetical hurricane on the synthetic model of Florida's grid. Its reach knocks out {len(prep.trip)} lines.",
        f"Without hardening, {base['people_out']:,} people are still without power when the grid settles (an estimate) after {base['steps']} cascade steps.",
        f"Your budget: {_money(run.budget)} ({_m(run.budget)} in $ million).",
        "Goal: choose the lines to harden, within the budget, that keep the most people's power on through the storm and its cascade.",
        "Tips: lines in series protect nothing until every line on that path is kept; 'alone' shows what one line does by itself; "
        "a pocket of towns may need two or more lines together; test combinations with try_plan.",
        f"Start with corridor_lines. You may make {FIRST_TOOLS} exploration calls (corridor_lines, people_served, harden_cost, try_plan) before your "
        f"first plan and {REVISE_TOOLS} more before each revision, several at once when useful. Then call submit_plan with your plan. The engine "
        "re-runs the storm with it and sends back its verdict, the number its own plan reached, what each of your lines adds, and the lines that "
        f"would add the most to it (measured); you may submit up to {MAX_ROUNDS} plans in all, each one a revision of the last. "
        'Give every call its "why": one short sentence shown to the reader.',
    ])


async def _agent(run: Run) -> str:
    """Gemini's function-calling loop. Returns why it stopped: "used" (at least one verified plan), or a reason."""
    contents: list[dict] = [{"role": "user", "parts": [{"text": _prompt(run)}]}]
    run.model = AGENT_MODEL
    run.deadline = time.perf_counter()
    run.learn([run.prep.base["people_out"], run.prep.base["steps"], len(run.prep.trip), run.budget, _m(run.budget)])
    last_plan: list[int] | None = None
    note: str | None = None
    status = "out_of_turns"
    while run.calls < MAX_GEMINI_CALLS and len(run.rounds) < MAX_ROUNDS:
        left = run.left()
        if left < 3:
            status = "slow"
            break
        must_submit = run.tools >= _allowance(run) or run.calls >= MAX_GEMINI_CALLS - 1 or left < 15
        if note:
            contents.append({"role": "user", "parts": [{"text": note}]})
            note = None
        try:
            reply, offline = await asyncio.wait_for(
                complete_tools(contents, FUNCTION_DECLARATIONS, system=SYSTEM, fallback=OFFLINE, timeout=min(AI_TIMEOUT_S, left), surface=SURFACE,
                               model=run.model, thinking=AGENT_THINKING, tool_mode="ANY", allowed=["submit_plan"] if must_submit else None), left)
        except asyncio.TimeoutError:
            status = "slow"
            break
        run.calls += 1
        if offline or not isinstance(reply, dict) or reply.get("__offline__"):
            # one slow or dropped reply shouldn't end the run: ask the same question again while time and retries last
            # (no key or no quota fails fast here too, and the retries are few)
            if configured() and run.retries < MAX_RETRIES and run.left() > 12 and run.calls < MAX_GEMINI_CALLS:
                run.retries += 1
                run.add(actor="engine", kind="retry", tone="muted", title="Gemini didn't answer in time: asked again")
                continue
            status = "unavailable"
            break
        run.model = reply.get("model") or run.model
        if reply.get("cache_key"):
            run.cache_keys.append(reply["cache_key"])
        contents.append(reply["content"])
        calls = reply["calls"]
        if not calls:
            note = "Reply with function calls only: explore with the tools, then call submit_plan."
            continue
        run.fcalls += len(calls)
        parts = []
        stop = False
        for call in calls:
            if call["name"] != "submit_plan" or stop:
                if call["name"] == "submit_plan":  # a second plan in the same reply: one plan per round
                    parts.append(function_response(call, {"error": "one plan per round: this one was not considered"}))
                    continue
                res, _ = await _answer(run, call, must_submit)
                parts.append(function_response(call, _fit(res)))
                continue
            raw = dict(call.get("args") or {})
            why_raw = raw.pop("why", None)
            lines, why = _ids(raw.get("line_ids"))
            if lines is not None:
                unknown = [b for b in lines if b not in run.prep.rows]
                if unknown:
                    lines, why = None, f"{_plural(len(unknown), 'id is', 'ids are')} not lines the storm knocks out ({', '.join(str(b) for b in unknown[:5])})"
            reason = _note(run, why_raw, lines)  # its dollar figures checked against the engine's price for these lines
            rnd = len(run.rounds) + 1
            if lines is not None and last_plan is not None and sorted(lines) == sorted(last_plan):
                run.add(actor="gemini", kind="keep", tone="muted", round=rnd, title="Kept its last plan: no better one found", detail=reason)
                parts.append(function_response(call, {"verdict": "unchanged", "note": "Same plan as your last round: the run ends here."}))
                stop = True
                status = "used"
                continue
            if lines is not None:
                _plan_row(run, lines, "propose" if rnd == 1 else "revise", rnd, reason)
            else:
                run.add(actor="gemini", kind="propose" if rnd == 1 else "revise", tone="info", round=rnd, via="function_call",
                        call=_call_text("submit_plan", raw), title=f"{'Proposed' if rnd == 1 else 'Revised'} (round {rnd}): a plan", detail=reason)
            verdict = await run_in_threadpool(_verify, run, lines, why, rnd)
            run.learn(verdict)
            if lines is not None:
                last_plan = lines
            if rnd >= MAX_ROUNDS:
                verdict["next"] = "That was the last round."
                stop = True
            else:
                verdict["next"] = ("Revise the plan to keep more people on within the budget. your_lines: the people each of your lines keeps on "
                                   "(weakest per dollar first); best_additions: the lines that would keep the most more on (fits_now false: only "
                                   f"by replacing a line); newly_out: towns the storm alone spared that go dark with your lines kept (the cascade takes another path). You have {REVISE_TOOLS} exploration calls (try_plan to test swaps), then submit_plan. "
                                   "If you cannot do better, submit the same plan again.")
                if verdict.get("verdict") == "verified":
                    tips = []
                    weak = (verdict.get("your_lines") or [None])[0]
                    if weak:
                        tips.append(f"its weakest line, {run.prep.rows[weak['id']]['label']}, keeps {weak['keeps']:,} on for {_money(weak['cost_m'] * 1e6)}")
                    top = (verdict.get("best_additions") or [None])[0]
                    if top:
                        tips.append(f"adding {run.prep.rows[top['id']]['label']} ({_money(top['cost_m'] * 1e6)}) would keep {top['adds']:,} more on"
                                    + ("" if top["fits_now"] else " if it replaced a line"))
                    run.add(actor="engine", kind="feedback", tone="info", round=rnd,
                            title=f"Sent back to Gemini: {verdict['people_out']:,} still out, the engine's own plan keeps {verdict['engine_plan']['people_kept']:,} on; revise",
                            detail=("Measured by the engine: " + "; ".join(tips) + ".") if tips else None)
                else:
                    run.add(actor="engine", kind="feedback", tone="info", round=rnd, title="Sent back to Gemini: the reason, and a request to revise within the budget")
            parts.append(function_response(call, _fit(verdict)))
        if stop or len(run.rounds) >= MAX_ROUNDS:
            if any(r.get("verified") for r in run.rounds):
                status = "used"
            break
        contents.append({"role": "user", "parts": parts})
    if any(r.get("verified") for r in run.rounds):
        return "used"
    if status == "used":
        status = "no_plan"
    if run.rounds:
        status = "rejected"
    cache_forget(run.cache_keys)  # a run with no verified plan: the next try asks afresh
    return status


WHY = {
    "not_configured": "Gemini is not configured on this server",
    "unavailable": "Gemini unavailable",
    "slow": "Gemini too slow",
    "rejected": "every plan Gemini submitted was rejected by the engine",
    "no_plan": "Gemini submitted no plan",
    "out_of_turns": "Gemini ran out of turns",
}


# ------------------------------------------------------------------------------------ one whole run
async def plan(run: Run) -> dict:
    prep, budget = run.prep, run.budget
    st, base = prep.storm, prep.base
    run.add(actor="engine", kind="case", tone="info",
            title=f"{st['label']} knocks out {_plural(len(prep.trip), 'line', 'lines')}; without hardening {base['people_out']:,} people are still without power when the grid settles (estimate)",
            detail=f"Budget {_money(budget)}. A hypothetical storm on the synthetic model of Florida's grid.")
    run.progress = {"phase": "singles", "done": prep.singles_n, "total": len(prep.trip)}

    def tick(i, n):
        run.progress = {"phase": "singles", "done": i, "total": n}

    t = time.perf_counter()
    await run_in_threadpool(prep.measure_singles, tick)
    helpful = sorted((r for r in prep.rows.values() if (r["alone"] or 0) > 0), key=lambda r: -r["alone"])
    run.add(actor="engine", kind="measure", tone="info", ms=round((time.perf_counter() - t) * 1000),
            title=f"Tested each of the {len(prep.trip)} lines alone: {_plural(len(helpful), 'keeps', 'keep')} people on by {'itself' if len(helpful) == 1 else 'themselves'}",
            detail=(f"The most: {helpful[0]['label']}, {helpful[0]['alone']:,} people." if helpful else "No single line keeps anyone on by itself: only combinations can."))
    run.progress = {"phase": "engine", "done": 0, "total": 1}
    t = time.perf_counter()
    run.engine = await run_in_threadpool(engine_plan, prep, budget)
    e = run.engine
    run.add(actor="engine", kind="baseline", tone="holds" if e["people_kept"] > 0 else "info", ms=round((time.perf_counter() - t) * 1000),
            cost_usd=e["cost_usd"], people_kept=e["people_kept"], lines=e["lines"],
            title=f"Engine's own plan (most people kept per dollar, re-measured at each step): {_plural(len(e['lines']), 'line', 'lines')}, {_range(e['cost_low_usd'], e['cost_usd'])}, keeps {e['people_kept']:,} on")
    status = "not_configured"
    if configured():
        run.progress = {"phase": "gemini", "done": 0, "total": MAX_ROUNDS}
        status = await _agent(run)
    if status != "used":
        run.add(actor="engine", kind="plain", tone="muted",
                title=f"{WHY.get(status, 'Gemini unavailable')}: the engine's own plan is the answer" if status != "not_configured"
                else "Gemini is not configured here: the engine's own plan is the answer (the plain version)")
    verified = [r for r in run.rounds if r.get("verified")]
    gem = None
    if verified:
        top = max(verified, key=lambda r: (r["people_kept"], -r["cost_usd"], -r["round"]))
        gem = {**prep.plan_view(top["lines"], "gemini"), "round": top["round"]}
    best = e
    if gem is not None and (gem["people_kept"] > e["people_kept"] or (gem["people_kept"] == e["people_kept"] and gem["cost_usd"] < e["cost_usd"])):
        best = gem
    by = "gemini" if status == "used" else "fallback"
    run.add(actor="engine", kind="check", tone="holds" if best["people_kept"] > 0 else "info",
            title=(f"Best verified plan: {'Gemini' if best['by'] == 'gemini' else 'the engine'}'s, {_plural(len(best['lines']), 'line', 'lines')} for {_range(best['cost_low_usd'], best['cost_usd'])} (typical): "
                   f"{best['people_kept']:,} people kept on ({best['people_out']:,} still without power instead of {base['people_out']:,})"))
    run.progress = {"phase": "done", "done": 1, "total": 1}
    none = {"people_out": base["people_out"], "people_zone": base["people_zone"], "people_hit": base["people_hit"], "steps": base["steps"],
            "outcome": base["outcome"], "out_subs": base["out_subs"], "still_out": prep.town_rows(base["town_mw"], base["people_out"])}
    return {
        "storm": {"name": st["name"], "label": st["label"], "preset": st["preset"], "points": st["points"], "radius_km": st["radius_km"], "lines": len(prep.trip),
                  "trip": st["trip"], "along_km": st["along_km"], "total_km": st["total_km"]},
        "budget_usd": budget,
        "budgets": BUDGETS,
        "metric": METRIC,
        "none": none,
        "engine": e,
        "gemini": gem,
        "best": {"by": best["by"], "lines": best["lines"], "cost_usd": best["cost_usd"], "cost_low_usd": best["cost_low_usd"], "people_out": best["people_out"], "people_kept": best["people_kept"]},
        "rounds": run.rounds,
        "trace": run.trace,
        "by": by,
        "why": None if by == "gemini" else WHY.get(status, "Gemini unavailable"),
        "verified": True,  # every plan shown was re-run by the engine
        "calls": run.calls,
        "function_calls": run.fcalls,
        "tool_calls": run.tools,
        "model": (run.model or AGENT_MODEL) if run.calls else None,
        "ms": round((time.perf_counter() - run.t0) * 1000),
        "cost_method": COST_METHOD,
        "sources": SOURCES,
        "note": FRAME,
    }


# ------------------------------------------------------------------------------------ cache and jobs
_cache: "OrderedDict[str, tuple[float, float, dict]]" = OrderedDict()
_cache_lock = threading.Lock()


def _ckey(prep: Prep, budget: float) -> str:
    return json.dumps([prep.key, round(budget), configured()])


def cached(prep: Prep, budget: float) -> dict | None:
    k = _ckey(prep, budget)
    with _cache_lock:
        hit = _cache.get(k)
        if hit is None:
            return None
        ts, ttl, out = hit
        if time.time() - ts > ttl:
            _cache.pop(k, None)
            return None
        _cache.move_to_end(k)
    return {**copy.deepcopy(out), "cached": True}


def _store(prep: Prep, budget: float, out: dict) -> None:
    ttl = FALLBACK_TTL_S if (out["by"] == "fallback" and configured()) else CACHE_TTL_S
    with _cache_lock:
        _cache[_ckey(prep, budget)] = (time.time(), ttl, copy.deepcopy(out))
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


class _Job:
    def __init__(self, prep: Prep, budget: float):
        self.id = secrets.token_urlsafe(12)
        self.run = Run(prep, budget)
        self.status = "running"
        self.result: dict | None = None
        self.error: str | None = None
        self.created = time.monotonic()
        self.task: asyncio.Task | None = None


_jobs: "OrderedDict[str, _Job]" = OrderedDict()
_by_case: dict[str, str] = {}
_jobs_lock = threading.Lock()


def _gc() -> None:
    now = time.monotonic()
    for jid in [j for j, job in _jobs.items() if job.status != "running" and now - job.created > JOB_TTL_S]:
        del _jobs[jid]
    while len(_jobs) > JOBS_MAX:
        old = next((j for j, job in _jobs.items() if job.status != "running"), None)
        if old is None:
            break
        del _jobs[old]


async def _run_job(job: _Job) -> None:
    k = _ckey(job.run.prep, job.run.budget)
    try:
        out = await plan(job.run)
        _store(job.run.prep, job.run.budget, out)
        job.result = {**out, "cached": False}
        job.status = "done"
    except HTTPException as e:
        job.error = e.detail if isinstance(e.detail, str) else "The planner could not run this storm"
        job.status = "error"
    except Exception:  # noqa: BLE001 - a background job always ends in a state the page can show
        log.exception("harden job failed")
        job.error = "The planner failed on this storm. Try again."
        job.status = "error"
    finally:
        with _jobs_lock:
            if _by_case.get(k) == job.id:
                del _by_case[k]


def _job_view(job: _Job) -> dict:
    out = {"job": job.id, "status": job.status, "progress": dict(job.run.progress), "trace": list(job.run.trace), "calls": job.run.calls}
    if job.status == "done":
        out["result"] = job.result
    elif job.status == "error":
        out["error"] = job.error
    return out


# ------------------------------------------------------------------------------------ routes
@router.post("/api/harden/run")
@limiter.limit("30/minute")
async def harden_run(request: Request, body: HardenIn):
    """Start planning (or return the finished plan from the cache). 422 with a sentence the page can show."""
    prep, budget = await run_in_threadpool(_prepare, body)
    hit = cached(prep, budget)
    if hit is not None:
        return {"job": None, "status": "done", "progress": {"phase": "done", "done": 1, "total": 1}, "trace": hit["trace"], "calls": hit["calls"], "result": hit}
    k = _ckey(prep, budget)
    with _jobs_lock:
        _gc()
        jid = _by_case.get(k)
        job = _jobs.get(jid) if jid else None
        if job is None or job.status != "running":
            if sum(1 for j in _jobs.values() if j.status == "running") >= RUNNING_MAX:
                raise HTTPException(status_code=429, detail="The planner is busy with other storms: try again in a moment")
            job = _Job(prep, budget)
            _jobs[job.id] = job
            _by_case[k] = job.id
            job.task = asyncio.get_running_loop().create_task(_run_job(job))
    return _job_view(job)


@router.get("/api/harden/info")
@limiter.limit("120/minute")
async def harden_info(request: Request):
    """What the control shows before a run: whether Gemini plans on this server (else the engine's plan is the plain
    version), the budget presets, the cost method and its sources."""
    return {"configured": configured(), "budgets": BUDGETS, "cost_method": COST_METHOD, "sources": SOURCES, "note": FRAME}


@router.get("/api/harden/jobs/{job_id}")
@limiter.limit("240/minute")  # the page polls every 0.8 s and the venue shares one IP (Show and Unlock use the same)
async def harden_job(request: Request, job_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_-]{8,40}$")):
    """A poll: a dict lookup and a copy, so it runs on the event loop and never waits for a worker thread."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="This plan is no longer available: run it again")
    return _job_view(job)
