"""Let a Gemini operator fight it: a grid-operator agent (Gemini, native function calling) fights the cascade step by
step on the SYNTHETIC grid model, against the same case with no operator and with the engine's own operator.

    POST /api/operator/run          a case (grid.CaseIn: the campus site(s)/MW, load_factor, trip, upgrades) -> a job:
                                    {job, status, progress, runs so far, trace so far}; a finished case comes back at
                                    once from the cache ({job: null, status: "done", result})
    GET  /api/operator/jobs/{id}    the job: progress, the runs finished so far and the trace so far, then the result

The cascade (powerflow.Grid.cascade_case: trip the most overloaded line, re-solve, repeat) is re-stepped here with the
engine's public API (Grid.solve, lost_by_sub, zone, hits, waves, downstream_subs, people) plus one hook: at each
decision point (a line is over its rating and is about to trip) an operator may act, up to MAX_ACTIONS actions:

    redispatch  raise one plant and lower another by the same MW, both in the same island, within the raised plant's
                room (its maximum minus its output) and the lowered plant's output: the balance holds (nothing is added
                or removed), only where the power comes from moves. Nobody loses power.
    shed        cut MW of existing load at a named substation, or across a named area (in proportion to each of its
                substations' load: rotating outages). Counted honestly: everyone those substations serve is hit (the
                people-hit counter's own rule for any substation that loses load), unless they were hit already.
    open_line   switch a line that is NOT over its rating out of service to redirect the flow. It counts like a
                tripped line: everyone its power was flowing to is hit. A line already over its rating can't be
                opened: the engine trips it anyway when it is still over after the actions.

The engine validates every action (the plant, substation, area or line exists; same island; the limits; the balance; at
most MAX_ACTIONS per step), applies the valid ones, re-solves, and then trips the most overloaded line still over its
rating, as the cascade does, until no line is over (or MAX_STEPS). The data center stays connected: cutting the campus
isn't one of the moves (it is the case being tested). Operators act at the first MAX_TURNS decision points; any steps
after that run on their own.

Three runs of every case:
    none    no operator: must reproduce /api/grid/cascade exactly (compared field by field on every run; the result
            carries `matches_cascade`, and the smoke check asserts it)
    engine  the engine's own operator, deterministic, with a look-ahead: at each decision point it builds a few plans for
            the line about to trip (plant pairs by relief per MW, area or substation load cuts by people per MW of
            relief, and mixes), plays each one forward to the end of the cascade (a rollout, no later moves), and keeps
            the plan with the fewest people hit, doing nothing included; the fallback when Gemini is unavailable
    gemini  the Gemini operator: at each decision point it gets the engine's alarm (the lines over their rating and what
            doing nothing leads to), calls list_overloads, line_detail, plants_near, loads_near and preview_actions (the
            engine's rollout of a set of moves) as function calls run on the engine, then apply_actions once; the engine
            plays a set forward first: one that ends no better than doing nothing, or that it rejects, goes back with the
            reasons and both numbers, for up to two revisions a step

Each run: the steps (the operator's actions, what the engine did, the lines tripped, people hit so far), the final toll
(people hit incl. the load cut, people still without power, the blackout's cost: the same value-of-lost-load formula as
costs.estimate, high end) and, for Gemini, the agent trace (every tool call, every rejected action with the reason).
Nothing here is a real utility's or grid operator's procedure: a game played on an open, synthetic model.

Public (no login, nothing stored but the in-memory cache); POST 30/minute per visitor. A finished case is cached for six
hours (a fallback run while a key is set, for two minutes); each Gemini turn is also in llm's answer cache, so a replay
is the same run call by call (scripts/prewarm_ai.py pre-runs the Fort Myers hero). A job runs in its own thread with its
own event loop, so the engine's solves never hold the server's loop.
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import logging
import math
import re
import secrets
import threading
import time
import weakref
from collections import OrderedDict

import os

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from starlette.concurrency import run_in_threadpool

from costs import outage_hours, voll_per_mwh
from grid import REGIONS, CaseIn, check_case, region_code
from limiter import limiter
from llm import AGENT_MODEL, AGENT_THINKING, complete_tools, configured, function_response, note_check
from powerflow import MAX_STEPS, OVER_PCT, PEOPLE_PER_HOME, area_of

try:  # the plant list (backend/demo/plants/<ST>.json): named plants; a state without one uses its generator buses
    from plants import plant_title, region_plants
except Exception:  # noqa: BLE001 - never block the router on the plants module
    region_plants = None  # type: ignore[assignment]
    plant_title = None  # type: ignore[assignment]

router = APIRouter(tags=["operator"])
log = logging.getLogger("uvicorn.error")

SURFACE = "operator"
OPERATOR_THINKING = os.getenv("GEMINI_OPERATOR_THINKING", "").strip() or AGENT_THINKING  # "low" lets the model reason a little more per call
OPERATOR_MODEL = os.getenv("GEMINI_OPERATOR_MODEL", "").strip() or AGENT_MODEL  # a stronger model for this agent when its latency allows
MAX_ACTIONS = 3  # actions per step
MAX_TURNS = 6  # decision points an operator acts at (both operators: the same rules)
MAX_CALLS_PER_TURN = 6  # Gemini calls in one step (the last one must apply)
MAX_PREVIEWS_PER_TURN = 3
MAX_GEMINI_CALLS = 24  # Gemini calls per run
AI_TIMEOUT_S = 10
DEADLINE_S = 26.0  # the whole Gemini run; past it the run continues without Gemini's actions (labeled)
TARGET = 0.97  # the engine's operator relieves a line to this share of its rating
MAX_REDISPATCH_MW = 3000.0
MAX_SHED_MW = 3000.0
MIN_EFFECT = 0.03  # MW of relief per MW moved: below this, a move is not worth making
MIN_RELIEF = 0.1  # MW of relief per MW cut
RESULT_MAX_CHARS = 1700
LIST_TOP = 5
CACHE_SIZE = 48
CACHE_TTL_S = 6 * 3600
FALLBACK_TTL_S = 120
JOBS_MAX = 48
JOB_TTL_S = 900
RUNNING_MAX = 3
THOUGHT_MAX = 220
_EPS = 1e-9

FRAME = ("A game on a SYNTHETIC grid model (Breakthrough Energy / Texas A&M), not any utility's network or any grid operator's "
         "procedures. People counts and costs are estimates.")
RULES = [
    "Move power between plants (redispatch): raise one plant, lower another by the same MW, within their limits. Nobody loses power.",
    "Cut load at a substation or across an area (shed): everyone those substations serve is counted as hit, since outages rotate.",
    "Open a line that isn't over its rating, to redirect the flow: it counts like a tripped line.",
    "At most 3 actions a step, at the first 6 steps. The engine checks each one, re-solves, then trips the worst line still over its rating.",
    "The data center stays connected: cutting the campus isn't one of the moves.",
]
_REAL_NAMES = re.compile(
    r"\b(FPL|Florida Power|Duke Energy|TECO|Tampa Electric|JEA|ERCOT|Oncor|CenterPoint|Dominion|Georgia Power|Southern Company|PG&E|Con ?Ed(?:ison)?|Xcel|Entergy|AEP|PJM|MISO|CAISO|NYISO|TVA|NextEra|NERC)\b",
    re.I,
)
_BLAME = re.compile(r"will cause|will black|will trigger|is to blame|\bblame|illegal|fraud|scam|corrupt|guilty|negligen", re.I)
_NUM = re.compile(r"\d+(?:[,.]\d+)*")


# ------------------------------------------------------------------------------------ words
def _title(name: str) -> str:
    return " ".join(w[:1].upper() + w[1:].lower() for w in str(name or "").split())


def _sub_title(g, s_idx: int) -> str:
    return _title(g.sub_name[int(s_idx)])


def _line_name(g, k: int) -> str:
    a, b = int(g.bus_sub_idx[g.f[k]]), int(g.bus_sub_idx[g.t[k]])
    if a == b:
        return f"{_sub_title(g, a)} transformer"
    return f"{_sub_title(g, a)} to {_sub_title(g, b)}"


def _n(x: float) -> str:
    return f"{int(round(float(x))):,}"


def _canon(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.2f}".rstrip("0").rstrip(".")


def _forms(v: float) -> set[str]:
    """A number as a sentence may print it (itself, rounded, in thousands or millions), within 5 % of it."""
    v = abs(float(v))
    out = {_canon(v), _canon(round(v, 1)), _canon(round(v)), _canon(math.floor(v)), _canon(math.ceil(v))}
    for k in range(1, 7):
        p = 10**k
        for r in (round(v / p) * p, math.floor(v / p) * p, math.ceil(v / p) * p):
            if r and abs(r - v) <= 0.05 * v:
                out.add(_canon(r))
    for scale in (1e3, 1e6):
        for d in (0, 1, 2):
            r = round(v / scale, d)
            if r and abs(r * scale - v) <= 0.05 * v:
                out.add(_canon(r))
    return out


def _walk(x, acc: set[str]) -> None:
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        if math.isfinite(float(x)):
            acc |= _forms(float(x))
    elif isinstance(x, str):
        for tok in _NUM.findall(x):
            try:
                acc |= _forms(float(tok.replace(",", "")))
            except ValueError:
                pass
    elif isinstance(x, dict):
        for v in x.values():
            _walk(v, acc)
    elif isinstance(x, (list, tuple)):
        if x:
            acc |= _forms(len(x))
        for v in x:
            _walk(v, acc)


def _said_ok(text, facts: list) -> str | None:
    """Gemini's own sentence (its "why"), shown in the trace only when it names no real company or utility, blames no
    one, and every number in it is one the engine gave (a tool result, the alarm, its own call's arguments)."""
    if not isinstance(text, str):
        return None
    t = re.sub(r"[*`#>]+", "", " ".join(text.split())).strip()
    if not t or _REAL_NAMES.search(t) or _BLAME.search(t):
        return None
    acc: set[str] = {"0", "1", "2", "3", "100"}
    _walk(facts, acc)
    for tok in _NUM.findall(t):
        try:
            v = float(tok.replace(",", ""))
        except ValueError:
            return None
        if _canon(v) not in acc:
            return None
    if len(t) > THOUGHT_MAX:
        t = t[:THOUGHT_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t


# ------------------------------------------------------------------------------------ plants and areas
class Plants:
    """The region's plants as the operator moves them: each plant's buses and its share of each bus's capacity."""

    def __init__(self, g, code: str):
        self.items: list[dict] = []
        raw = None
        if region_plants is not None:
            try:
                raw = region_plants(code)["plants"]
            except Exception:  # noqa: BLE001 - no plant list: the generator buses stand in
                raw = None
        if raw:
            for p in raw:
                buses, shares = [], []
                for bid, _pg, pmax in p.get("parts") or []:
                    i = g.bus_index.get(int(bid))
                    if i is None or g.pmax[i] <= _EPS:
                        continue
                    buses.append(i)
                    shares.append(min(1.0, max(0.0, float(pmax) / float(g.pmax[i]))))
                if not buses:
                    continue
                name = plant_title(p) if plant_title else _title(p.get("name"))
                self.items.append({"id": int(p["id"]), "name": name, "fuel": p.get("fuel") or "", "area": p.get("area") or area_of(p.get("sub_name")),
                                   "lat": float(p["lat"]), "lon": float(p["lon"]), "buses": np.array(buses), "share": np.array(shares)})
        else:
            for i in np.flatnonzero(g.pmax > _EPS):
                s = int(g.bus_sub_idx[i])
                self.items.append({"id": int(g.bus_ids[i]), "name": f"{_sub_title(g, s)} generator", "fuel": "", "area": area_of(g.sub_name[s]),
                                   "lat": float(g.sub_lat[s]), "lon": float(g.sub_lon[s]), "buses": np.array([int(i)]), "share": np.array([1.0])})
        self.by_id = {p["id"]: p for p in self.items}

    @staticmethod
    def output(g, p: dict, gen: np.ndarray, up: np.ndarray) -> float:
        return float((p["share"] * np.maximum(gen[p["buses"]] + up[p["buses"]], 0.0)).sum())

    @staticmethod
    def maximum(g, p: dict) -> float:
        return float((p["share"] * g.pmax[p["buses"]]).sum())

    @staticmethod
    def room(g, p: dict, gen: np.ndarray, up: np.ndarray) -> float:
        return float((p["share"] * np.maximum(g.pmax[p["buses"]] - gen[p["buses"]] - up[p["buses"]], 0.0)).sum())


_areas_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_areas_lock = threading.Lock()


def _areas(g) -> dict[str, np.ndarray]:
    """Area name (the town a substation is named after, lower case) -> its substation indices."""
    with _areas_lock:
        got = _areas_cache.get(g)
    if got is None:
        by: dict[str, list[int]] = {}
        for i, name in enumerate(g.sub_name):
            by.setdefault(area_of(name).lower(), []).append(i)
        got = {k: np.array(v) for k, v in by.items()}
        with _areas_lock:
            _areas_cache[g] = got
    return got


# ------------------------------------------------------------------------------------ one run of the cascade
class Sim:
    """The network as one run leaves it: lines in or out, the redispatch (MW of generation moved per bus), the load cut,
    and the moves made so far (`script`: per turn, the batches of accepted actions, so a rollout can replay them)."""

    def __init__(self, g, code: str, extra: np.ndarray, trip: list[int], upgrades: dict[int, float], campus_buses: list[int], plants: Plants, fresh=None):
        self.g, self.code = g, code
        self.extra = extra  # the campus load (MW per bus)
        self.trip = list(trip)
        self.rate = g.rates_with(upgrades)
        self.active = np.ones(g.m, dtype=bool)
        for bid in self.trip:
            self.active[g.br_index[int(bid)]] = False
        self.up = np.zeros(g.n)  # MW of generation added (+) or removed (-) at each bus by redispatch; sums to 0
        self.shed = np.zeros(g.n)  # existing load cut (MW per bus)
        self.campus_buses = sorted(set(int(b) for b in campus_buses))
        self.plants = plants
        self.opened: list[int] = []  # branch indices the operator opened
        self.opened_down: set[int] = set()  # the substations their power was flowing to (hit like a trip)
        self.state = None
        self.fresh = fresh  # () -> a new Sim of the same case (rollouts)
        self.script: list[list[list[dict]]] = []

    def solve(self):
        load = self.extra - self.up if self.up.any() else self.extra  # moved generation enters the solve as negative load
        return self.g.solve(self.active, load, self.rate, self.shed if self.shed.any() else None)

    def snapshot(self) -> tuple:
        return self.up.copy(), self.shed.copy(), self.active.copy(), list(self.opened), set(self.opened_down)

    def restore(self, snap: tuple) -> None:
        self.up, self.shed, self.active, self.opened, self.opened_down = snap[0].copy(), snap[1].copy(), snap[2].copy(), list(snap[3]), set(snap[4])

    def excess(self, st) -> float:
        """Total MW over the ratings (the overload the operators work down)."""
        over = st.active & (st.loading_pct > OVER_PCT + 1e-6)
        return float((np.abs(st.flow[over]) - self.rate[over]).sum())

    def overloads(self, st) -> np.ndarray:
        idx = np.flatnonzero(st.active & (st.loading_pct > OVER_PCT + 1e-6))
        return idx[np.argsort(-st.loading_pct[idx])]

    def line_row(self, st, k: int) -> dict:
        g = self.g
        return {"line": int(g.br_ids[k]), "name": _line_name(g, k), "kind": "transformer" if g.bus_sub_idx[g.f[k]] == g.bus_sub_idx[g.t[k]] else "line",
                "kv": float(g.br_kv[k]), "rating_mw": round(float(self.rate[k])), "flow_mw": round(abs(float(st.flow[k]))),
                "pct": round(float(st.loading_pct[k]), 1), "over_by_mw": round(max(0.0, abs(float(st.flow[k])) - float(self.rate[k])))}

    def campus_dark_mw(self, st) -> float:
        """The campus's own MW that lost supply (the served share of its buses' load)."""
        out = 0.0
        pd_eff = self.g.pd - self.shed
        for b in self.campus_buses:
            load = pd_eff[b] + self.extra[b] - self.up[b]
            if load > _EPS:
                out += float(self.extra[b]) * max(0.0, 1.0 - float(st.served_load[b]) / load)
        return out


def _ptdf(g, st, k: int) -> np.ndarray:
    """MW on branch k per MW injected at each bus (withdrawn at its island's reference bus): one sparse solve."""
    e = np.zeros(g.n)
    e[g.f[k]] += 1.0
    e[g.t[k]] -= 1.0
    y = np.zeros(g.n)
    if st.lu is not None and len(st.keep):
        y[st.keep] = st.lu.solve(e[st.keep])
    return y / g.x[k]


def _island_mean(st, y: np.ndarray, isl: int) -> float:
    m = st.comp == isl
    w = st.weights[m]
    return float((w * y[m]).sum() / w.sum()) if w.sum() > _EPS else float(y[m].mean())


# ------------------------------------------------------------------------------------ the engine's tools
def _plants_for(sim: Sim, st, k: int) -> dict:
    """Plants that could move line k: raise candidates (a negative effect relieves it) and lower candidates (positive).
    effect_per_100mw: the line's MW change (in its loading direction) per 100 MW raised at the plant, the rest of its
    island easing off; a pair moves the line by MW/100 x (raised effect - lowered effect)."""
    g = sim.g
    y = _ptdf(g, st, k)
    sgn = 1.0 if st.flow[k] >= 0 else -1.0
    isl = int(st.comp[g.f[k]])
    ybar = _island_mean(st, y, isl)
    mid_lat = float(g.sub_lat[g.bus_sub_idx[g.f[k]]] + g.sub_lat[g.bus_sub_idx[g.t[k]]]) / 2
    mid_lon = float(g.sub_lon[g.bus_sub_idx[g.f[k]]] + g.sub_lon[g.bus_sub_idx[g.t[k]]]) / 2
    cos = math.cos(math.radians(mid_lat))
    ups, downs = [], []
    for p in sim.plants.items:
        b = p["buses"]
        if int(st.comp[b[0]]) != isl:
            continue
        out = Plants.output(g, p, st.gen, sim.up)
        room = Plants.room(g, p, st.gen, sim.up)
        # the plant's buses weighted by where a raise (room) or a cut (output) would land
        wr = p["share"] * np.maximum(g.pmax[b] - st.gen[b] - sim.up[b], 0.0)
        wo = p["share"] * np.maximum(st.gen[b] + sim.up[b], 0.0)
        yr = float((wr * y[b]).sum() / wr.sum()) if wr.sum() > _EPS else float(y[b].mean())
        yo = float((wo * y[b]).sum() / wo.sum()) if wo.sum() > _EPS else float(y[b].mean())
        km = round(111.19 * math.hypot(p["lat"] - mid_lat, (p["lon"] - mid_lon) * cos))
        row = {"plant": p["id"], "name": p["name"], "fuel": p["fuel"], "output_mw": round(out), "max_mw": round(Plants.maximum(g, p)), "km": km}
        if room >= 10:
            ups.append({**row, "room_up_mw": math.floor(room), "effect_per_100mw": round(100 * sgn * (yr - ybar), 1), "_y": yr, "_room": room})
        if out >= 10:
            downs.append({**row, "can_lower_mw": math.floor(out), "effect_per_100mw": round(100 * sgn * (yo - ybar), 1), "_y": yo, "_out": out})
    ups.sort(key=lambda r: r["effect_per_100mw"])
    downs.sort(key=lambda r: -r["effect_per_100mw"])
    return {"sgn": sgn, "up": ups, "down": downs}


def _relief_by_sub(sim: Sim, st, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Per substation: the existing load still served in line k's island (MW) and the MW of relief per MW cut there
    (its buses weighted by load)."""
    g = sim.g
    y = _ptdf(g, st, k)
    sgn = 1.0 if st.flow[k] >= 0 else -1.0
    isl = int(st.comp[g.f[k]])
    ybar = _island_mean(st, y, isl)
    avail = np.where((st.comp == isl) & ~st.dark_bus, np.maximum(g.pd - sim.shed, 0.0), 0.0)
    rel = sgn * (ybar - y)
    mw = np.zeros(len(g.sub_ids))
    r = np.zeros(len(g.sub_ids))
    np.add.at(mw, g.bus_sub_idx, avail)
    np.add.at(r, g.bus_sub_idx, avail * rel)
    with np.errstate(divide="ignore", invalid="ignore"):
        per = np.where(mw > _EPS, r / mw, 0.0)
    return mw, per


def _loads_for(sim: Sim, st, k: int, hit: set[int]) -> list[dict]:
    """Substations whose load, cut, relieves line k: relief per MW cut, the load there, the people it serves."""
    g = sim.g
    mw, per = _relief_by_sub(sim, st, k)
    out = []
    for s in np.flatnonzero((mw > 1.0) & (per >= MIN_RELIEF)):
        sid = int(g.sub_ids[s])
        out.append({"substation": sid, "name": _sub_title(g, s), "area": area_of(g.sub_name[s]), "load_mw": math.floor(float(mw[s])),
                    "relief_per_mw": round(float(per[s]), 2), "people_served": g.zone([sid])["people_zone"], "already_hit": sid in hit,
                    "_mw": float(mw[s]), "_r": float(per[s])})
    return out


def _areas_near(sim: Sim, st, k: int, hit: set[int]) -> list[dict]:
    """Areas whose load, cut across all their substations in proportion to load, relieves line k."""
    g = sim.g
    mw, per = _relief_by_sub(sim, st, k)
    out = []
    for key, subs in _areas(g).items():
        m = float(mw[subs].sum())
        if m <= 1.0:
            continue
        r = float((mw[subs] * per[subs]).sum() / m)
        if r < MIN_RELIEF:
            continue
        live = [int(g.sub_ids[s]) for s in subs if mw[s] > _EPS]
        new = [s for s in live if s not in hit]
        out.append({"area": area_of(g.sub_name[int(subs[0])]), "substations": len(live), "load_mw": math.floor(m), "relief_per_mw": round(r, 2),
                    "people_served": g.zone(live)["people_zone"], "people_not_yet_hit": g.zone(new)["people_zone"] if new else 0, "_mw": m, "_r": r})
    return out


# ------------------------------------------------------------------------------------ validating and applying actions
def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _int(v) -> int | None:
    x = _num(v)
    return int(x) if x is not None and abs(x - round(x)) < 1e-9 else None


def _action_text(a: dict) -> str:
    t = a.get("type")
    if t == "redispatch":
        return f"redispatch(raise={a.get('raise_plant')}, lower={a.get('lower_plant')}, mw={a.get('mw')})"
    if t == "shed":
        where = f"area={json.dumps(str(a.get('area'))[:30])}" if a.get("area") and a.get("substation") is None else f"substation={a.get('substation')}"
        return f"shed({where}, mw={a.get('mw')})"
    if t == "open_line":
        return f"open_line(line={a.get('line')})"
    return f"{str(t)[:20]}(...)"


def apply_actions(sim: Sim, st, actions, hit: set[int]) -> tuple[list[dict], list[dict], object]:
    """Validate each action against the network as the earlier ones left it, apply the valid ones, re-solve once.
    Returns (applied, rejected, the new state). Each applied action keeps its request as `raw` (for replays)."""
    g = sim.g
    applied, rejected = [], []
    if not isinstance(actions, list):
        actions = []
    for n, raw in enumerate(actions):
        a = raw if isinstance(raw, dict) else {}
        kind = a.get("type")
        shown = _action_text(a)
        if n >= MAX_ACTIONS:
            rejected.append({"action": shown, "reason": f"at most {MAX_ACTIONS} actions a step"})
            continue
        if kind == "redispatch":
            pu, pd_ = _int(a.get("raise_plant")), _int(a.get("lower_plant"))
            mw = _num(a.get("mw"))
            A, B = sim.plants.by_id.get(pu), sim.plants.by_id.get(pd_)
            if A is None or B is None:
                rejected.append({"action": shown, "reason": "not a plant id plants_near gave (unknown plant)"})
                continue
            if pu == pd_:
                rejected.append({"action": shown, "reason": "the same plant can't be raised and lowered"})
                continue
            if mw is None or mw < 1 or mw > MAX_REDISPATCH_MW:
                rejected.append({"action": shown, "reason": f"mw must be between 1 and {MAX_REDISPATCH_MW:,.0f}"})
                continue
            if int(st.comp[A["buses"][0]]) != int(st.comp[B["buses"][0]]):
                rejected.append({"action": shown, "reason": "the two plants are in different islands: moving power between them can't balance"})
                continue
            room = Plants.room(g, A, st.gen, sim.up)
            out_b = Plants.output(g, B, st.gen, sim.up)
            if mw > room + 0.5:
                rejected.append({"action": shown, "reason": f"{A['name']} can rise only {_n(room)} MW more (output {_n(Plants.output(g, A, st.gen, sim.up))} of {_n(Plants.maximum(g, A))} MW)"})
                continue
            if mw > out_b + 0.5:
                rejected.append({"action": shown, "reason": f"{B['name']} produces only {_n(out_b)} MW, so it can't come down {_n(mw)} MW"})
                continue
            mw = min(mw, room, out_b)
            before_a = Plants.output(g, A, st.gen, sim.up)
            wr = A["share"] * np.maximum(g.pmax[A["buses"]] - st.gen[A["buses"]] - sim.up[A["buses"]], 0.0)
            wo = B["share"] * np.maximum(st.gen[B["buses"]] + sim.up[B["buses"]], 0.0)
            np.add.at(sim.up, A["buses"], mw * wr / wr.sum())
            np.add.at(sim.up, B["buses"], -mw * wo / wo.sum())
            applied.append({"type": "redispatch", "mw": round(mw, 1), "raw": dict(a),
                            "raise": {"plant": pu, "name": A["name"], "fuel": A["fuel"], "lat": A["lat"], "lon": A["lon"], "before_mw": round(before_a, 1),
                                      "after_mw": round(before_a + mw, 1), "max_mw": round(Plants.maximum(g, A), 1)},
                            "lower": {"plant": pd_, "name": B["name"], "fuel": B["fuel"], "lat": B["lat"], "lon": B["lon"], "before_mw": round(out_b, 1),
                                      "after_mw": round(out_b - mw, 1), "max_mw": round(Plants.maximum(g, B), 1)}})
        elif kind == "shed":
            sid, mw = _int(a.get("substation")), _num(a.get("mw"))
            area = a.get("area") if isinstance(a.get("area"), str) else None
            if sid is not None:
                s = g.sub_index.get(sid)
                if s is None:
                    rejected.append({"action": shown, "reason": "not a substation id (unknown substation)"})
                    continue
                subs, label, where = np.array([s]), _sub_title(g, s), {"substation": sid, "area": None}
            elif area and area.strip():
                subs = _areas(g).get(area_of(area).lower())
                if subs is None:
                    rejected.append({"action": shown, "reason": "not an area loads_near gave (unknown area)"})
                    continue
                label, where = area_of(area), {"substation": None, "area": area_of(area)}
            else:
                rejected.append({"action": shown, "reason": "a shed needs a substation id or an area"})
                continue
            if mw is None or mw < 1 or mw > MAX_SHED_MW:
                rejected.append({"action": shown, "reason": f"mw must be between 1 and {MAX_SHED_MW:,.0f}"})
                continue
            buses = np.flatnonzero(np.isin(g.bus_sub_idx, subs))
            avail = np.where(st.dark_bus[buses], 0.0, np.maximum(g.pd[buses] - sim.shed[buses], 0.0))
            total = float(avail.sum())
            if total < 1:
                rejected.append({"action": shown, "reason": f"{label} has no load left to cut"})
                continue
            if mw > total + 0.5:
                rejected.append({"action": shown, "reason": f"{label} serves only {_n(total)} MW"})
                continue
            cut = min(mw, total)
            sim.shed[buses] += cut * avail / total
            cut_subs = sorted({int(g.sub_ids[g.bus_sub_idx[b]]) for b, v in zip(buses, avail) if v > _EPS})
            new = [x for x in cut_subs if x not in hit]
            w = np.array([float(g.sub_load[g.sub_index[x]]) for x in cut_subs])
            lat = float(np.average([g.sub_lat[g.sub_index[x]] for x in cut_subs], weights=w)) if w.sum() > _EPS else float(g.sub_lat[g.sub_index[cut_subs[0]]])
            lon = float(np.average([g.sub_lon[g.sub_index[x]] for x in cut_subs], weights=w)) if w.sum() > _EPS else float(g.sub_lon[g.sub_index[cut_subs[0]]])
            applied.append({"type": "shed", **where, "name": label, "mw": round(cut, 1), "raw": dict(a), "subs": cut_subs,
                            "lat": round(lat, 4), "lon": round(lon, 4), "people": g.zone(cut_subs)["people_zone"],
                            "people_new": g.zone(new)["people_zone"] if new else 0, "already_hit": not new})
        elif kind == "open_line":
            bid = _int(a.get("line"))
            k = g.br_index.get(bid) if bid is not None else None
            if k is None:
                rejected.append({"action": shown, "reason": "not a line id (unknown line)"})
                continue
            if not sim.active[k]:
                rejected.append({"action": shown, "reason": "that line is already out of service"})
                continue
            if st.loading_pct[k] > OVER_PCT + 1e-6:
                rejected.append({"action": shown, "reason": "that line is over its rating: opening it is letting it trip (the engine trips it if it's still over)"})
                continue
            down = g.downstream_subs(st, [k])
            sim.active[k] = False
            sim.opened.append(k)
            sim.opened_down |= down
            fs, ts = int(g.bus_sub_idx[g.f[k]]), int(g.bus_sub_idx[g.t[k]])
            applied.append({"type": "open_line", "line": bid, "name": _line_name(g, k), "from_sub": int(g.sub_ids[fs]), "to_sub": int(g.sub_ids[ts]),
                            "raw": dict(a), "people": g.zone(down)["people_zone"]})
        else:
            rejected.append({"action": shown, "reason": "type must be redispatch, shed or open_line"})
    new = sim.solve() if applied else st
    return applied, rejected, new


def _apply_batches(sim: Sim, batches: list[list[dict]], hit: set[int]) -> list[dict]:
    """Replay batches of actions (each one apply_actions call, re-solved after it). Returns everything applied."""
    out = []
    for b in batches:
        applied, rejected, st = apply_actions(sim, sim.state, b, hit)
        if rejected:  # never expected: a replay of actions the engine accepted on the same network
            log.warning("operator: a replayed action was refused: %s", rejected[:2])
        sim.state = st
        out += applied
    return out


# ------------------------------------------------------------------------------------ the stepper
def _step_rows(sim: Sim, st, idx) -> list[dict]:
    return [{"id": int(sim.g.br_ids[k]), "pct": round(float(st.loading_pct[k]), 1)} for k in idx]


async def run_cascade(sim: Sim, act=None, max_turns: int = MAX_TURNS) -> dict:
    """The cascade, re-stepped: exactly Grid.cascade_case (flexible) when `act` is None. `act(sim, turn, alarm, hit)` is
    awaited at each decision point (at most `max_turns`) and returns the turn's record ({applied, batches, ...}); it
    applies its moves through apply_actions (which re-solves) and leaves sim.state at the state after them."""
    g = sim.g
    trip = sim.trip
    steps: list[dict] = []
    hit: set[int] = set()
    origin: list[int] = []
    down: set[int] = set()
    if trip:
        pre = g.solve(np.ones(g.m, dtype=bool), sim.extra, sim.rate)
        origin = [g.br_index[int(b)] for b in trip]
        down = g.downstream_subs(pre, origin)
    state = sim.solve()
    sim.state = state
    prev_dark = np.zeros(g.n, dtype=bool)
    seen: set[int] = set()
    capped = False
    n = 0
    tripped_ids = [int(b) for b in trip]
    tripped_rows: list[dict] = [{"id": int(b), "pct": None} for b in trip]
    opened_now: list[int] = []
    action = "storm"
    turn = 0
    pending = None  # the operator's record for the step being built
    while True:
        over = sim.overloads(state)
        newly_dark = state.dark_bus & ~prev_dark
        if n > 0 or len(trip):
            lost = g.lost_by_sub(state)
            fresh = [[sid, mw_] for sid, mw_ in lost.items() if sid not in seen]
            before = set(seen)
            seen.update(lost)
            hits = g.hits(down, origin, hit)
            wave_origin = tripped_ids + [int(g.br_ids[k]) for k in opened_now]
            waves = g.waves([f[0] for f in fresh], wave_origin, state.active, before)
            for w in waves:
                hit.update(w["subs"])
            hit.update(seen)
            steps.append({
                "n": n,
                "action": action,  # "storm" (step 0), "trip", or "held" (the operator's moves left no line over)
                "tripped": tripped_ids,
                "tripped_at": tripped_rows,  # each tripped line's loading when it went
                "opened": [int(g.br_ids[k]) for k in opened_now],
                "dark_subs": sorted({int(g.sub_ids[g.bus_sub_idx[i]]) for i in np.flatnonzero(newly_dark)}),
                "newly_affected": sorted(fresh, key=lambda x: -x[1]),
                "hit_subs": sorted({s for h in hits for s in h["subs"]} | {s for w in waves for s in w["subs"]}),
                "lost_mw": round(state.lost_existing_mw, 1),
                "people": g.people(state.lost_existing_mw),
                **g.zone(seen),
                "shed_mw": round(float(sim.shed.sum()), 1),
                "people_hit": g.zone(hit)["people_zone"],
                "operator": pending,
            })
            pending = None
        prev_dark = prev_dark | state.dark_bus
        if not len(over):
            break
        if n >= MAX_STEPS:
            capped = True
            break
        opened_now = []
        if act is not None and turn < max_turns:
            turn += 1
            k0 = len(sim.opened)
            sim.state = state
            pending = await act(sim, turn, _step_rows(sim, state, over), set(hit))
            sim.script.append([list(b) for b in (pending or {}).get("batches") or []])
            state = sim.state
            opened_now = sim.opened[k0:]
            over = sim.overloads(state)
            if pending is not None:
                pending["over_after"] = _step_rows(sim, state, over[:8])
            if opened_now or (pending and pending.get("applied")):
                origin = list(opened_now)
                down = set(sim.opened_down) if opened_now else set()
                sim.opened_down = set()
            if not len(over):
                n += 1
                action, tripped_ids, tripped_rows = "held", [], []
                if not opened_now:
                    origin, down = [], set()
                continue
        worst = int(over[np.argmax(state.loading_pct[over])])
        w_down = g.downstream_subs(state, [worst])
        if opened_now:
            origin = list(opened_now) + [worst]
            down = down | w_down
        else:
            origin, down = [worst], w_down
        tripped_rows = [{"id": int(g.br_ids[worst]), "pct": round(float(state.loading_pct[worst]), 1)}]
        active = state.active.copy()
        active[worst] = False
        sim.active = active
        tripped_ids = [int(g.br_ids[worst])]
        action = "trip"
        n += 1
        state = sim.solve()
        sim.state = state
    sim.state = state
    tripped_after = any(s["tripped"] for s in steps if s["action"] != "storm")
    return {
        "steps": steps,
        # islanded: areas went dark beyond the load an operator cut; held: an operator kept every line in; settled: lines
        # tripped but nobody went dark
        "outcome": "islanded" if state.lost_existing_mw - float(sim.shed.sum()) > 0.5 else ("held" if turn and not tripped_after else "settled"),
        "engine_outcome": "islanded" if state.lost_mw > 0.5 else "settled",  # Grid.cascade_case's own word (load cut counts)
        "capped": capped,
        "total_steps": n,
        "turns": turn,
        "lost_mw": round(state.lost_existing_mw, 1),
        "people": g.people(state.lost_existing_mw),
        **g.zone(seen),
        "people_hit": g.zone(hit | seen)["people_zone"],
        "shed_mw": round(float(sim.shed.sum()), 1),
        "site_dark_mw": round(sim.campus_dark_mw(state), 1),
        "affected": g.lost_by_sub(state),
        "tripped_total": sum(len(s["tripped"]) for s in steps if s["action"] != "storm"),
        "balance_mw": round(float(sim.up.sum()), 6),
    }


async def _rollout(sim: Sim, plan: list[list[dict]]) -> dict:
    """The whole cascade again from the start: the moves made so far (sim.script), then `plan` (batches) at this turn,
    then nothing more. The engine's look-ahead, and Gemini's preview_actions."""
    fresh = sim.fresh()
    script = [list(t) for t in sim.script] + [plan]

    async def act(s2: Sim, turn: int, alarm, hit):
        batches = script[turn - 1] if turn <= len(script) else []
        applied = _apply_batches(s2, batches, hit) if batches else []
        return {"turn": turn, "applied": applied, "batches": batches}

    return await run_cascade(fresh, act, max_turns=len(script))


def _same_as_cascade(mine: dict, ref: dict) -> tuple[bool, str | None]:
    """The no-operator run against Grid.cascade_case: every field the page shows, step by step."""
    for k in ("total_steps", "capped", "lost_mw", "people", "people_zone", "people_hit", "affected"):
        if mine.get(k) != ref.get(k):
            return False, f"{k}: {mine.get(k)!r} != {ref.get(k)!r}"
    if mine["engine_outcome"] != ref["outcome"]:
        return False, f"outcome: {mine['engine_outcome']} != {ref['outcome']}"
    if len(mine["steps"]) != len(ref["steps"]):
        return False, f"steps: {len(mine['steps'])} != {len(ref['steps'])}"
    for a, b in zip(mine["steps"], ref["steps"]):
        for k in ("n", "action", "tripped", "lost_mw", "people", "people_zone", "people_hit", "dark_subs", "newly_affected"):
            if a.get(k) != b.get(k):
                return False, f"step {b.get('n')} {k}: {a.get(k)!r} != {b.get(k)!r}"
    return True, None


# ------------------------------------------------------------------------------------ the engine's operator
def _pick(sim: Sim, st, kind: str, hit: set[int]) -> dict | None:
    """One action of `kind` for the line about to trip in `st`, sized to bring it under TARGET of its rating."""
    over = sim.overloads(st)
    if not len(over):
        return None
    k = int(over[0])
    need = abs(float(st.flow[k])) - TARGET * float(sim.rate[k])
    if kind == "redispatch":
        pl = _plants_for(sim, st, k)
        best, gain = None, 0.0
        for A in pl["up"][:8]:
            for B in pl["down"][:8]:
                if A["plant"] == B["plant"]:
                    continue
                eff = (B["_y"] - A["_y"]) * pl["sgn"]  # MW of relief per MW moved
                if eff < MIN_EFFECT:
                    continue
                mw = math.floor(min(need / eff, A["_room"], B["_out"], MAX_REDISPATCH_MW))
                if mw >= 1 and eff * mw > gain:
                    best, gain = {"type": "redispatch", "raise_plant": A["plant"], "lower_plant": B["plant"], "mw": mw}, eff * mw
        return best
    rows = _areas_near(sim, st, k, hit) if kind == "area" else _loads_for(sim, st, k, hit)
    best, cost = None, math.inf
    for r in rows:
        take = min(r["_mw"], need / r["_r"] * 1.02 + 1.0)
        new = r["people_not_yet_hit"] if kind == "area" else (0 if r["already_hit"] else r["people_served"])
        c = new / max(r["_r"] * take, 1e-6)  # people hit per MW of relief
        if c < cost - 1e-9:
            where = {"area": r["area"]} if kind == "area" else {"substation": r["substation"]}
            best, cost = {"type": "shed", **where, "mw": math.floor(take) if take >= 2 else 1}, c
    return best


PLANS = [("redispatch", "redispatch", "redispatch"), ("area", "area", "area"), ("redispatch", "area", "area"),
         ("sub", "sub", "sub"), ("redispatch", "sub", "sub")]
COMBO_AREAS = 6  # areas considered for whole-area cuts (1 to 3 at a time: up to 41 plans)
MAX_COMBOS = 10  # the most promising whole-area plans played forward (a count, not a clock: the same case gives the same run on any machine)
ENGINE_TURN_S = 20.0  # a safety valve only (never reached on a normal machine): the plans are counted, not timed


def _build(sim: Sim, kinds: tuple, hit: set[int]) -> list[list[dict]]:
    """A plan: one action per kind, each for the line most over after the ones before (checked by apply_actions and
    re-solved in between, exactly as it will be committed), as batches of one action. Nothing is committed."""
    snap = sim.snapshot()
    st = sim.state
    batches: list[list[dict]] = []
    try:
        for kind in kinds:
            a = _pick(sim, st, kind, hit)
            if a is None:
                continue
            applied, _, st2 = apply_actions(sim, st, [a], hit)
            if applied:
                batches.append([a])
                st = st2
    finally:
        sim.restore(snap)
    return batches


def _area_combos(sim: Sim, hit: set[int]) -> list[list[list[dict]]]:
    """Whole-area cuts for the lines over now: the areas that relieve the worst two lines most, one to three at a time,
    each cut in full (one batch), most promising first (relief per person not yet hit)."""
    st = sim.state
    over = sim.overloads(st)
    pool: dict[str, dict] = {}
    for k in over[:2]:
        for r in sorted(_areas_near(sim, st, int(k), hit), key=lambda r: -r["_r"])[:COMBO_AREAS]:
            pool.setdefault(r["area"], r)
    cands = sorted(pool.values(), key=lambda r: -(r["_r"] * r["_mw"]) / max(r["people_not_yet_hit"], 1))[:COMBO_AREAS]
    combos = [c for n in (1, 2, 3) for c in itertools.combinations(cands, n)]
    combos.sort(key=lambda c: -sum(r["_r"] * r["_mw"] for r in c) / max(sum(r["people_not_yet_hit"] for r in c), 1))
    return [[[{"type": "shed", "area": r["area"], "mw": math.floor(r["_mw"])} for r in c]] for c in combos[:MAX_COMBOS]]


def _engine_act_factory(recs: list[dict], job: "_Job | None" = None):
    """The engine's operator: every plan played forward to the end (a rollout); the one with the fewest people hit wins
    (then the least load cut, then the fewest moves). Doing nothing is always one of the plans, so it never does worse
    than no operator from where it stands. Plans: the greedy builds (PLANS), then whole-area cuts in combinations, as
    at most MAX_COMBOS of them."""
    async def act(sim: Sim, turn: int, alarm: list[dict], hit: set[int]) -> dict:
        t0 = time.perf_counter()
        plans, seen_plans = [[]], {"[]"}
        for p in [_build(sim, kinds, hit) for kinds in PLANS] + _area_combos(sim, hit):
            key = json.dumps(p, sort_keys=True)
            if p and key not in seen_plans:
                seen_plans.add(key)
                plans.append(p)
        scored = []
        for i, p in enumerate(plans):
            if i > len(PLANS) and time.perf_counter() - t0 > ENGINE_TURN_S:
                break
            r = await _rollout(sim, p)
            await asyncio.sleep(0)  # let Gemini's answers in between the engine's rollouts (the two runs go side by side)
            scored.append(((r["people_hit"], r["shed_mw"], sum(len(b) for b in p)), p, r))
        plans = plans[: len(scored)]
        scored.sort(key=lambda x: x[0])
        (_, best, r) = scored[0]
        nothing = next(x for x in scored if not x[1])
        applied = _apply_batches(sim, best, hit)
        rec = {"turn": turn, "by": "engine", "alarm": alarm, "applied": applied, "rejected": [], "batches": best,
               "plans": len(plans), "projected": r["people_hit"], "projected_if_nothing": nothing[2]["people_hit"]}
        recs.append(rec)
        if job is not None:
            job.progress["engine_turn"] = turn
        return rec
    return act


# ------------------------------------------------------------------------------------ the case
class _Case:
    def __init__(self, body: CaseIn):
        if body.firm:
            raise HTTPException(status_code=422, detail="The operator plays the flexible case: switch firm service off to let it fight the cascade")
        g, sites, trip, upgrades = check_case(body)
        if not sites and not trip:
            raise HTTPException(status_code=422, detail="Nothing to fight: drop a data center or draw a storm first")
        self.code = region_code(body.region)
        self.g, self.sites, self.trip, self.upgrades = g, sites, trip, upgrades
        self.buses = [g.site_bus(s.lat, s.lon) for s in sites]
        self.extra = g.extra_load([(b, s.mw) for b, s in zip(self.buses, sites)])
        main = int(g.bus_sub_idx[self.buses[0]]) if self.buses else None
        self.header = {
            "region": self.code,
            "region_name": REGIONS[self.code]["name"],
            "mw": round(sum(s.mw for s in sites), 1),
            "sites": [{"lat": s.lat, "lon": s.lon, "mw": s.mw} for s in sites],
            "sub_name": _sub_title(g, main) if main is not None else None,
            "sub_area": area_of(g.sub_name[main]) if main is not None else None,
            "load_factor": g.load_factor,
            "trip": trip,
            "upgrades": {str(k): v for k, v in upgrades.items()},
        }
        self.key = json.dumps([self.code, round(g.load_factor, 2), [[round(s.lat, 4), round(s.lon, 4), round(s.mw, 1)] for s in sites], sorted(trip),
                               sorted((int(k), round(float(v), 1)) for k, v in upgrades.items())])
        self.plants: Plants | None = None

    def sim(self) -> Sim:
        if self.plants is None:
            self.plants = Plants(self.g, self.code)
        return Sim(self.g, self.code, self.extra, self.trip, self.upgrades, self.buses, self.plants, fresh=self.sim)


def _toll(run: dict) -> dict:
    """The run's toll: people hit (incl. load cut), still out, homes, and the blackout's cost (costs.estimate's formula:
    MW dark x the outage's length from its size x the value of lost load; high end first)."""
    people = int(run.get("people") or 0)
    lost = float(run.get("lost_mw") or 0.0)
    hours = outage_hours(people) if lost > 0.5 else 0.0
    lo, hi = voll_per_mwh(hours) if hours > 0 else (0.0, 0.0)
    return {"people_hit": int(run["people_hit"]), "people_out": people, "homes_out": int(round(people / PEOPLE_PER_HOME)), "lost_mw": round(lost, 1),
            "shed_mw": run.get("shed_mw", 0.0), "hours": hours, "cost_low": round(lost * hours * lo), "cost_high": round(lost * hours * hi),
            "tripped": run.get("tripped_total", 0), "steps": run.get("total_steps", 0), "outcome": run.get("outcome")}


# ------------------------------------------------------------------------------------ the Gemini operator
SYSTEM = """You are the grid operator in a game played on a SYNTHETIC model of a U.S. state's power grid (the Breakthrough Energy / Texas A&M test system, not any real utility's network, and not any real grid operator's procedures). A new data-center campus has just connected and lines are over their ratings. Left alone, the engine trips the most overloaded line every step and the failure spreads (a cascade). Your goal: as few people hit as possible by the end.

How people are counted: everyone served by a substation where you cut load (outages rotate across the area), everyone the power of a tripped or opened line was flowing to, and everyone in areas that go dark. Each person once.

Your moves, sent with apply_actions (at most 3 per step):
- redispatch: raise one plant and lower another by the same MW, within the raised plant's room_up_mw and the lowered plant's can_lower_mw. Nobody loses power. From plants_near: the line changes by MW/100 x (raised plant's effect_per_100mw - lowered plant's effect_per_100mw); negative relieves it, so raise one with a negative effect and lower one with a positive effect.
- shed: cut MW of existing load at one substation ("substation": id) or across an area ("area": name, cut in proportion to its substations' load). From loads_near: relief_per_mw is the line's MW of relief per MW cut; mw_to_clear_this_line is what one cut needs to bring THIS line under its rating (clears_it_alone says whether that area has that much load), a cut smaller than that leaves the line over its rating, so it still trips and the cut hit people for nothing: size the cut to at least that (a plant pair likewise: best_pair_to_clear_this_line), and compare with preview_actions. Everyone those substations serve counts as hit unless already hit.
- open_line: switch a line that is NOT over its rating out of service to redirect flow. It counts like a tripped line.
The data center stays connected: it can't be cut. After your moves the engine re-solves and trips the worst line still over its rating; then the next step comes. You act at the first 6 steps.

preview_actions plays a set of moves forward to the end of the cascade (no later moves) and tells you the people hit, next to doing nothing this step. It is the best way to compare options: preview, then apply the better one.

Each step: read the alarm, call the tools you need (several at once when useful), then call apply_actions once with a one-sentence "why". An empty actions list does nothing this step, which is right when every option you previewed hits more people. Use only ids and area names the tools gave you. Try the cuts that clear the line before smaller ones, and do not give up after one preview: the first idea is often not the best. Never name a real company, utility or grid operator."""

_ACTIONS_PARAM = {
    "type": "array",
    "description": "Up to 3 actions; an empty list does nothing this step.",
    "items": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["redispatch", "shed", "open_line"]},
            "raise_plant": {"type": "integer", "description": "redispatch: the plant id to raise."},
            "lower_plant": {"type": "integer", "description": "redispatch: the plant id to lower."},
            "mw": {"type": "number", "description": "redispatch or shed: MW."},
            "substation": {"type": "integer", "description": "shed at one substation: its id."},
            "area": {"type": "string", "description": "shed across an area: its name from loads_near."},
            "line": {"type": "integer", "description": "open_line: the line id."},
        },
        "required": ["type"],
    },
}
TOOL_SPECS = {
    "list_overloads": {
        "description": "Every line and transformer over its rating now (loading %, flow, rating, MW over), the ones at 95% or more, and the people hit so far.",
    },
    "line_detail": {
        "description": "One line or transformer: its ends, rating, flow, loading, and the people its power flows on to (all hit if it trips).",
        "parameters": {"type": "object", "properties": {"line": {"type": "integer", "description": "A line id from the alarm or a tool result."}}, "required": ["line"]},
    },
    "plants_near": {
        "description": "Plants that can move this line's flow: raise candidates (negative effect_per_100mw relieves it) with room_up_mw, and lower candidates (positive effect) with can_lower_mw.",
        "parameters": {"type": "object", "properties": {"line": {"type": "integer", "description": "A line id over its rating."}}, "required": ["line"]},
    },
    "loads_near": {
        "description": "Where cutting load relieves this line: substations and areas with relief_per_mw, load_mw and the people counted as hit if you cut there.",
        "parameters": {"type": "object", "properties": {"line": {"type": "integer", "description": "A line id over its rating."}}, "required": ["line"]},
    },
    "preview_actions": {
        "description": "Plays these moves forward to the end of the cascade (no later moves) without applying them: the lines still over right after, and the people hit at the end, next to doing nothing this step. At most 3 previews a step.",
        "parameters": {"type": "object", "properties": {"actions": _ACTIONS_PARAM}, "required": ["actions"]},
    },
    "apply_actions": {
        "description": "Your moves this step (at most 3), checked and applied by the engine, which then re-solves and trips the worst line still over its rating. Call it once per step.",
        "parameters": {
            "type": "object",
            "properties": {
                "actions": _ACTIONS_PARAM,
                "why": {"type": "string", "description": "One short sentence for the reader: what these moves do. Use only numbers the tools gave."},
            },
            "required": ["actions", "why"],
        },
    },
}
WHY_PARAM = {"type": "string", "description": "Optional: one short sentence on what this call checks."}


def _declaration(name: str, spec: dict) -> dict:
    params = copy.deepcopy(spec.get("parameters") or {"type": "object", "properties": {}})
    if name != "apply_actions":
        params["properties"] = {**params["properties"], "why": WHY_PARAM}
    return {"name": name, "description": spec["description"], "parameters": params}


FUNCTION_DECLARATIONS = [_declaration(n, s) for n, s in TOOL_SPECS.items()]
OFFLINE = {"__offline__": True}


def _strip(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def _fit(obj: dict, limit: int = RESULT_MAX_CHARS) -> dict:
    out = copy.deepcopy(obj)
    for _ in range(40):
        if len(json.dumps(out, separators=(",", ":"))) <= limit:
            return out
        lists = [(k, v) for k, v in out.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            break
        k, v = max(lists, key=lambda kv: len(json.dumps(kv[1])))
        out[k] = v[:-1]
    return out


def _applied_brief(a: dict) -> dict:
    if a["type"] == "redispatch":
        return {"type": "redispatch", "mw": a["mw"], "raised": a["raise"]["name"], "raised_to_mw": a["raise"]["after_mw"], "lowered": a["lower"]["name"], "lowered_to_mw": a["lower"]["after_mw"]}
    if a["type"] == "shed":
        return {"type": "shed", "where": a["name"], "mw": a["mw"], "people_hit_by_it": a["people_new"]}
    return {"type": "open_line", "line": a["line"], "people_downstream": a["people"]}


class GeminiRun:
    """One Gemini operator: the conversation, the trace (written live), the counters."""

    def __init__(self, job: "_Job | None" = None):
        self.contents: list[dict] = []
        self.trace: list[dict] = job.trace if job is not None else []
        self.turns: list[dict] = []
        self.facts: list = []  # every tool result and alarm this run (the numbers Gemini's sentences may use)
        self.calls = 0
        self.fcalls = 0
        self.tool_calls = 0
        self.previews = 0
        self.model = OPERATOR_MODEL
        self.status = "used"  # "used" | "unavailable" | "slow" | "out_of_turns"
        self.stopped_at: int | None = None  # the turn Gemini stopped answering at (the rest ran without its actions)
        self.t0 = time.perf_counter()
        self.job = job

    def add(self, **row) -> dict:
        row["n"] = len(self.trace) + 1
        self.trace.append(row)
        return row

    def left(self) -> float:
        return DEADLINE_S - (time.perf_counter() - self.t0)

    async def tool(self, sim: Sim, name: str, args: dict, hit: set[int], turn_state: dict) -> tuple[dict, str, str]:
        """Run one tool on the engine: (result for Gemini, summary, tone)."""
        g, st = sim.g, sim.state
        if name == "list_overloads":
            over = sim.overloads(st)
            near = np.flatnonzero(st.active & (st.loading_pct > 95.0) & (st.loading_pct <= OVER_PCT + 1e-6))
            near = near[np.argsort(-st.loading_pct[near])][:LIST_TOP]
            res = {"over": [sim.line_row(st, k) for k in over[:8]], "over_count": int(len(over)), "near_limit": [sim.line_row(st, k) for k in near],
                   "people_hit_so_far": g.zone(hit)["people_zone"]}
            return res, f"{len(over)} {'line' if len(over) == 1 else 'lines'} over their rating, {len(near)} more at 95 % or above", "over" if len(over) else "holds"
        if name == "preview_actions":
            if turn_state["previews"] >= MAX_PREVIEWS_PER_TURN:
                return {"error": f"at most {MAX_PREVIEWS_PER_TURN} previews a step: apply your moves now"}, "Preview limit reached this step", "muted"
            turn_state["previews"] += 1
            self.previews += 1
            acts = args.get("actions") if isinstance(args.get("actions"), list) else []
            snap = sim.snapshot()
            try:
                applied, rejected, st2 = apply_actions(sim, st, acts, hit)
                over2 = sim.overloads(st2)
                right_after = [sim.line_row(st2, k) for k in over2[:4]]
            finally:
                sim.restore(snap)
            ok = [a["raw"] for a in applied]
            r = await _rollout(sim, [ok] if ok else [])
            base = turn_state.get("nothing") or await _rollout(sim, [])
            turn_state["nothing"] = base
            res = {"applied": [_applied_brief(a) for a in applied], "rejected": rejected, "right_after": {"over": right_after, "over_count": int(len(over2))},
                   "at_the_end": {"people_hit": r["people_hit"], "people_without_power": r["people"], "lines_tripped": r["tripped_total"], "load_cut_mw": r["shed_mw"]},
                   "if_you_do_nothing_this_step": {"people_hit": base["people_hit"], "people_without_power": base["people"], "lines_tripped": base["tripped_total"]}}
            better = r["people_hit"] < base["people_hit"]
            say = (f"Played forward: {_n(r['people_hit'])} people hit at the end, against {_n(base['people_hit'])} doing nothing this step"
                   + (f" ({len(rejected)} refused)" if rejected else ""))
            return res, say, "holds" if better else "over"
        k = g.br_index.get(_int(args.get("line")) or -1) if args.get("line") is not None else None
        if k is None:
            return {"error": "unknown line id: use an id from the alarm or a tool result"}, "Unknown line id", "muted"
        if name == "line_detail":
            row = sim.line_row(st, k)
            down = g.downstream_subs(st, [k]) if st.active[k] else set()
            res = {**row, "in_service": bool(st.active[k]), "people_downstream": g.zone(down)["people_zone"]}
            return res, f"{row['name']}: {row['pct']:g} % of its rating, power for about {_n(res['people_downstream'])} people", "over" if row["pct"] > OVER_PCT else "info"
        if not st.active[k]:
            return {"error": "that line is out of service"}, "That line is out of service", "muted"
        if name == "plants_near":
            pl = _plants_for(sim, st, k)
            res = {"line": int(g.br_ids[k]), "name": _line_name(g, k), "pct": round(float(st.loading_pct[k]), 1), "over_by_mw": sim.line_row(st, k)["over_by_mw"],
                   "raise_candidates": [_strip(r) for r in pl["up"][:LIST_TOP]], "lower_candidates": [_strip(r) for r in pl["down"][:LIST_TOP]]}
            best = (pl["down"][0]["effect_per_100mw"] - pl["up"][0]["effect_per_100mw"]) if pl["up"] and pl["down"] else 0
            if best > 0.5:  # what the best pair would have to move to bring the line under its rating, and whether it can
                need = res["over_by_mw"] / (best / 100.0) * 1.02 + 1.0
                room = min(pl["up"][0]["_room"], pl["down"][0]["_out"])
                res["best_pair_to_clear_this_line"] = {"raise": pl["up"][0]["plant"], "lower": pl["down"][0]["plant"], "mw_needed": math.ceil(need),
                                                       "mw_available": math.floor(room), "clears_it_alone": need <= room}
            say = (f"{len(res['raise_candidates'])} plants can rise and {len(res['lower_candidates'])} can come down; the best pair moves the line "
                   f"{abs(best):.0f} MW per 100 MW moved") if best > 0 else "No plant pair moves this line"
            return res, say, "info"
        if name == "loads_near":
            over_by = max(0.0, abs(float(st.flow[k])) - float(sim.rate[k]))
            subs = sorted(_loads_for(sim, st, k, hit), key=lambda r: -r["relief_per_mw"])[:5]
            areas = sorted(_areas_near(sim, st, k, hit), key=lambda r: -r["relief_per_mw"] * min(r["_mw"], 1e9))[:5]
            for r in subs + areas:  # how much to cut to bring THIS line back under its rating (the engine's arithmetic)
                need = over_by / max(r["_r"], 1e-6) * 1.02 + 1.0
                r["mw_to_clear_this_line"] = math.ceil(need)
                r["clears_it_alone"] = need <= r["_mw"]
            res = {"line": int(g.br_ids[k]), "name": _line_name(g, k), "over_by_mw": sim.line_row(st, k)["over_by_mw"],
                   "substations": [_strip(r) for r in subs], "areas": [_strip(r) for r in areas]}
            say = (f"{len(areas)} areas and {len(subs)} substations relieve it; e.g. {areas[0]['area']}: {areas[0]['relief_per_mw']:g} MW per MW cut, "
                   f"{_n(areas[0]['load_mw'])} MW, about {_n(areas[0]['people_served'])} people") if areas else ("Only single substations relieve it" if subs else "No load cut relieves this line")
            return res, say, "info"
        return {"error": "not one of the tools"}, "Not one of the tools", "muted"


def _alarm_text(sim: Sim, turn: int, alarm: list[dict], hit_people: int, nothing: dict) -> str:
    g = sim.g
    rows = []
    for a in alarm[:6]:
        r = sim.line_row(sim.state, g.br_index[a["id"]])
        rows.append(f"  line {r['line']}: {r['name']} ({r['kind']}, {r['kv']:g} kV), {r['pct']:g}% of its {r['rating_mw']} MW rating, over by {r['over_by_mw']} MW")
    more = f"\n  ...and {len(alarm) - 6} more" if len(alarm) > 6 else ""
    return (f"Step {turn} of at most {MAX_TURNS}. ALARM: {len(alarm)} {'line is' if len(alarm) == 1 else 'lines are'} over their rating; "
            f"the worst trips when you're done unless it's back under.\n" + "\n".join(rows) + more
            + f"\nPeople hit so far: {hit_people:,}. If you do nothing from here on, the cascade ends with {nothing['people_hit']:,} people hit "
            f"({nothing['tripped_total']} more lines tripped). Call the tools you need (several at once in one reply is fine), then apply_actions once.")


def _call_title(sim: Sim, name: str, args: dict) -> str:
    g = sim.g
    if name == "preview_actions":
        acts = args.get("actions") if isinstance(args.get("actions"), list) else []
        return "Previewed: " + _moves_text(sim, acts)[:1].lower() + _moves_text(sim, acts)[1:]
    k = g.br_index.get(_int(args.get("line")) or -1) if args.get("line") is not None else None
    line = _line_name(g, k) if k is not None else "the line"
    return {"list_overloads": "Looked at every line over its rating", "line_detail": f"Looked up {line}",
            "plants_near": f"Asked which plants can move {line}", "loads_near": f"Asked where cutting load relieves {line}"}.get(name, name)


def _moves_text(sim: Sim, acts: list) -> str:
    g = sim.g
    out = []
    for a in acts[:MAX_ACTIONS + 1]:
        a = a if isinstance(a, dict) else {}
        t = a.get("type")
        mw = _num(a.get("mw"))
        if t == "redispatch":
            A, B = sim.plants.by_id.get(_int(a.get("raise_plant"))), sim.plants.by_id.get(_int(a.get("lower_plant")))
            out.append(f"move {_n(mw or 0)} MW from {B['name'] if B else 'plant ' + str(a.get('lower_plant'))} to {A['name'] if A else 'plant ' + str(a.get('raise_plant'))}")
        elif t == "shed":
            s = g.sub_index.get(_int(a.get("substation")) or -1) if a.get("substation") is not None else None
            where = _sub_title(g, s) if s is not None else (f"across {area_of(str(a.get('area')))}" if a.get("area") else f"substation {a.get('substation')}")
            out.append(f"cut {_n(mw or 0)} MW {'at ' if s is not None else ''}{where}")
        elif t == "open_line":
            k = g.br_index.get(_int(a.get("line")) or -1)
            out.append(f"open {_line_name(g, k) if k is not None else 'line ' + str(a.get('line'))}")
        else:
            out.append(str(t)[:20])
    if not out:
        return "Hold: no moves this step"
    s = "; ".join(out)[:200]
    return s[:1].upper() + s[1:]


def _verdict_title(applied: list, rejected: list, acts: list, after: list, n_over: int) -> str:
    if not acts:
        head = "Nothing to apply"
    elif applied and not rejected:
        head = f"Applied {len(applied)} {'move' if len(applied) == 1 else 'moves'}"
    elif applied:
        head = f"Applied {len(applied)}, refused {len(rejected)}"
    else:
        head = f"Refused {'the move' if len(rejected) == 1 else 'all ' + str(len(rejected)) + ' moves'}"
    if not n_over:
        return f"{head}, re-solved: no line is over its rating"
    return f"{head}, re-solved: {after[0]['name']} still at {after[0]['pct']:.0f} %, it trips"


def _rejected_detail(rejected: list) -> str | None:
    if not rejected:
        return None
    return " · ".join(f"{r['action'].split('(')[0]}: {r['reason']}" for r in rejected[:3])


def _gemini_act_factory(run: GeminiRun):
    async def act(sim: Sim, turn: int, alarm: list[dict], hit: set[int]) -> dict:
        g = sim.g
        hit_people = g.zone(hit)["people_zone"]
        rec = {"turn": turn, "by": "gemini", "alarm": alarm, "applied": [], "rejected": [], "why": None, "batches": []}
        run.turns.append(rec)
        names = [sim.line_row(sim.state, g.br_index[a["id"]])["name"] for a in alarm[:1]]
        run.add(actor="engine", kind="alarm", tone="over", turn=turn,
                title=f"Step {turn}: {len(alarm)} {'line' if len(alarm) == 1 else 'lines'} over {'its' if len(alarm) == 1 else 'their'} rating"
                      + (f", worst {names[0]} at {alarm[0]['pct']:.0f} %" if names else ""))
        if run.stopped_at is not None:
            run.add(actor="engine", kind="skip", tone="muted", turn=turn, title="Gemini is no longer answering: no moves this step")
            return rec
        nothing = await _rollout(sim, [])
        turn_state = {"previews": 0, "nothing": nothing}
        rec["projected_if_nothing"] = nothing["people_hit"]
        run.facts.append({"alarm": alarm, "people_hit": hit_people, "nothing": {"people_hit": nothing["people_hit"], "tripped": nothing["tripped_total"]}})
        run.contents.append({"role": "user", "parts": [{"text": _alarm_text(sim, turn, alarm, hit_people, nothing)}]})
        calls_turn = 0
        retries = 0
        while True:
            left = run.left()
            if run.calls >= MAX_GEMINI_CALLS or left < 3:
                run.status = "out_of_turns" if run.calls >= MAX_GEMINI_CALLS else "slow"
                run.stopped_at = turn
                run.add(actor="engine", kind="skip", tone="muted", turn=turn,
                        title="Gemini's call budget for this run is used: the rest runs without its moves" if run.status == "out_of_turns" else "Gemini ran out of time: the rest runs without its moves")
                return rec
            must_apply = calls_turn >= MAX_CALLS_PER_TURN - 2 or retries > 0
            try:
                reply, offline = await asyncio.wait_for(
                    complete_tools(run.contents, FUNCTION_DECLARATIONS, system=SYSTEM, fallback=OFFLINE, timeout=min(AI_TIMEOUT_S, left), surface=SURFACE,
                                   model=run.model, thinking=OPERATOR_THINKING, tool_mode="ANY", allowed=["apply_actions"] if must_apply else None),
                    left)
            except asyncio.TimeoutError:
                reply, offline = None, True
            run.calls += 1
            calls_turn += 1
            if run.job is not None:
                run.job.progress.update(turn=turn, calls=run.calls)
            if offline or not isinstance(reply, dict) or reply.get("__offline__"):
                run.status = "unavailable"
                run.stopped_at = turn
                run.add(actor="engine", kind="skip", tone="muted", turn=turn, title="Gemini didn't answer: the rest runs without its moves")
                return rec
            run.model = reply.get("model") or run.model
            if not isinstance(reply.get("content"), dict):
                run.status = "unavailable"
                run.stopped_at = turn
                run.add(actor="engine", kind="skip", tone="muted", turn=turn, title="Gemini's reply was unreadable: the rest runs without its moves")
                return rec
            run.contents.append(reply["content"])
            calls = reply.get("calls") or []
            if not calls:
                run.contents.append({"role": "user", "parts": [{"text": "Call a tool, or apply_actions (an empty list does nothing this step)."}]})
                continue
            run.fcalls += len(calls)
            calls = sorted(calls, key=lambda c: c["name"] == "apply_actions")  # the lookups first, the moves last
            parts, done = [], False
            for call in calls:
                name = call["name"]
                args = dict(call.get("args") or {})
                why = args.pop("why", None)
                if name == "apply_actions":
                    if done:
                        parts.append(function_response(call, {"error": "one apply_actions per step"}))
                        continue
                    acts = args.get("actions") if isinstance(args.get("actions"), list) else []
                    say = _said_ok(why, run.facts + [args])
                    shown = "; ".join(_action_text(a if isinstance(a, dict) else {}) for a in acts[:4]) or "no actions"
                    run.add(actor="gemini", kind="propose", tone="info", turn=turn, via="function_call", call=f"apply_actions({shown})"[:170],
                            title=_moves_text(sim, acts), detail=say)
                    worse = None
                    if acts:  # the engine plays the moves forward first: a set that ends worse than doing nothing is sent back
                        snap = sim.snapshot()
                        try:
                            ok_now, _, _ = apply_actions(sim, sim.state, acts, hit)
                        finally:
                            sim.restore(snap)
                        if ok_now:
                            ahead = await _rollout(sim, [[a["raw"] for a in ok_now]])
                            if ahead["people_hit"] > turn_state["nothing"]["people_hit"] * 0.99 - 500:  # not at least 1 % better
                                worse = (ahead["people_hit"], turn_state["nothing"]["people_hit"])
                    if worse is not None:
                        applied, st = [], sim.state
                        rejected = [{"action": shown[:80], "reason": (f"played forward to the end, this ends with {_n(worse[0])} people hit against {_n(worse[1])} if you do nothing this step: "
                                                                       + ("worse, so not applied" if worse[0] > worse[1] else "no better, so not applied (size a cut to clear the line, or hold)"))}]
                    else:
                        applied, rejected, st = apply_actions(sim, sim.state, acts, hit)
                    sim.state = st
                    for a in applied:
                        note_check(SURFACE, True, f"a {a['type']} move within its limits")
                    for r in rejected:
                        note_check(SURFACE, False, f"a move the engine refused: {r['reason']}"[:150], r["action"][:40])
                    rec["applied"] += applied
                    rec["rejected"] += rejected
                    if applied:
                        rec["batches"].append([a["raw"] for a in applied])
                    rec["why"] = say or rec["why"]
                    over = sim.overloads(st)
                    after = [sim.line_row(st, k) for k in over[:5]]
                    final = bool(applied) or not acts or retries >= 2
                    res = {"applied": [_applied_brief(a) for a in applied], "rejected": rejected,
                           "after": {"over": after, "over_count": int(len(over)), "load_cut_mw_total": round(float(sim.shed.sum()), 1)},
                           "next": ("No line is over its rating: the grid holds." if not len(over) else
                                    f"The engine now trips {after[0]['name']} ({after[0]['pct']:g}%)." if final else
                                    "Nothing was applied. Revise (try a smaller or different move, or hold with an empty list) and call apply_actions again.")}
                    run.facts.append(res)
                    run.add(actor="engine", kind="verify", tone="holds" if not len(over) else "over", turn=turn, via="function_response",
                            title=_verdict_title(applied, rejected, acts, after, len(over)), detail=_rejected_detail(rejected))
                    parts.append(function_response(call, _fit(res)))
                    if final:
                        done = True
                    else:
                        retries += 1
                else:
                    if name not in TOOL_SPECS:
                        note_check(SURFACE, False, "a function call to a tool that doesn't exist", name[:40])
                        run.add(actor="engine", kind="refused", tone="muted", turn=turn, title=f"Refused {name[:40]}: not one of the tools")
                        parts.append(function_response(call, {"error": "not one of the tools"}))
                        continue
                    run.add(actor="gemini", kind="call", tone="info", turn=turn, via="function_call",
                            call=f"{name}({', '.join(f'{k}={json.dumps(v)}' for k, v in args.items())})"[:170], title=_call_title(sim, name, args))
                    res, summary, tone = await run.tool(sim, name, args, hit, turn_state)
                    run.trace[-1]["detail"] = _said_ok(why, run.facts + [args, res])
                    run.tool_calls += 1
                    run.facts.append(res)
                    run.add(actor="engine", kind="result", tone=tone, turn=turn, via="function_response", title=summary[:1].upper() + summary[1:])
                    parts.append(function_response(call, _fit(res)))
            run.contents.append({"role": "user", "parts": parts})
            if done:
                return rec
    return act


# ------------------------------------------------------------------------------------ the three runs
def _action_view(a: dict) -> dict:
    return {k: v for k, v in a.items() if k != "raw"}


def _run_view(run: dict, turns: list[dict], by: str) -> dict:
    """What the page replays of one run: its steps (compact) and its toll."""
    steps = []
    for s in run["steps"]:
        op = s.get("operator")
        steps.append({
            "n": s["n"], "action": s["action"], "tripped": s["tripped"], "tripped_at": s["tripped_at"], "opened": s["opened"],
            "dark_subs": s["dark_subs"], "newly_affected": [a[0] for a in s["newly_affected"]], "hit_subs": s["hit_subs"][:400],
            "people_hit": s["people_hit"], "people": s["people"], "lost_mw": s["lost_mw"], "shed_mw": s["shed_mw"],
            "operator": None if op is None else {"turn": op["turn"], "by": op.get("by"), "alarm": op["alarm"][:8], "applied": [_action_view(a) for a in op["applied"]],
                                                 "rejected": op.get("rejected") or [], "over_after": op.get("over_after") or [], "why": op.get("why"),
                                                 "projected_if_nothing": op.get("projected_if_nothing")},
        })
    acts = [a for t in turns for a in t["applied"]]
    return {
        "by": by,
        "steps": steps,
        "toll": _toll(run),
        "outcome": run["outcome"],
        "capped": run["capped"],
        "turns": run["turns"],
        "actions": {"redispatch": sum(1 for a in acts if a["type"] == "redispatch"), "shed": sum(1 for a in acts if a["type"] == "shed"),
                    "open_line": sum(1 for a in acts if a["type"] == "open_line"), "rejected": sum(len(t.get("rejected") or []) for t in turns),
                    "moved_mw": round(sum(a["mw"] for a in acts if a["type"] == "redispatch"), 1)},
        "balance_mw": run["balance_mw"],
        "site_dark_mw": run["site_dark_mw"],
    }


def _verdict(none: dict, eng: dict, gem: dict | None) -> dict:
    return _verdict_inner(none, eng, gem)


def _verdict_inner(none: dict, eng: dict, gem: dict | None) -> dict:
    """The honest outcome in words (people hit, fewer is better; within 1 % is a tie)."""
    n0 = none["toll"]["people_hit"]
    ne = eng["toll"]["people_hit"]
    fmt = lambda x: f"{x:,}"  # noqa: E731

    def cmp(a: int, b: int) -> int:
        if abs(a - b) <= 0.01 * max(a, b, 1):
            return 0
        return -1 if a < b else 1

    def with_note(v: dict) -> dict:
        """The honest words: a run only "did better" when the final blackout also shrinks by 5 % or more. When it doesn't, say so
        in the headline itself (the people-hit rule counts differently for a cut-load run), and flag real = False for the card."""
        v["real"] = True
        runs = {"none": none, "engine": eng, **({"gemini": gem} if gem else {})}
        best = runs.get(v["winner"]) if v["winner"] in runs else None
        if best is not None and v["winner"] != "none" and none["toll"]["people_out"] > 0:
            b, z = best["toll"], none["toll"]
            if b["people_out"] >= 0.95 * z["people_out"]:
                who = {"engine": "the engine's operator", "gemini": "Gemini's operator"}.get(v["winner"], "the operator")
                v["real"] = False
                v["text"] = (f"Counted by the people-hit rule, {who} hit fewer people ({fmt(b['people_hit'])} against {fmt(z['people_hit'])} with no operator), "
                             f"but the blackout at the end is about the same size ({fmt(z['people_out'])} people without power with no operator, {fmt(b['people_out'])} with it): "
                             f"the moves changed how far the failure spread on the way ({b['tripped']} lines tripped against {z['tripped']}) and who was counted, not how big the final blackout is.")
        return v

    if not n0 and not ne and (gem is None or not gem["toll"]["people_hit"]):
        return {"winner": "none", "text": "Nobody was hit in any of the three runs."}
    if gem is None:
        c = cmp(ne, n0)
        text = (f"The engine's operator kept {fmt(n0 - ne)} people from being hit: {fmt(ne)} against {fmt(n0)} with no operator." if c < 0 else
                f"No operator move helped here: the engine's operator found nothing better than letting it run ({fmt(ne)} people hit).")
        return with_note({"winner": "engine" if c < 0 else "none", "text": text})
    ng = gem["toll"]["people_hit"]
    c_ge, c_g0 = cmp(ng, ne), cmp(ng, n0)
    if c_ge < 0:
        text = f"Gemini's operator did best: {fmt(ng)} people hit, against {fmt(ne)} for the engine's operator and {fmt(n0)} with no operator."
        win = "gemini"
    elif c_ge == 0:
        text = f"Gemini's operator matched the engine's: {fmt(ng)} people hit, against {fmt(n0)} with no operator."
        win = "tie"
    else:
        text = (f"The engine's operator did better than Gemini's: {fmt(ne)} people hit against {fmt(ng)}"
                + (f" ({fmt(n0)} with no operator)." if c_g0 < 0 else
                   f". Gemini's moves did no better than no operator at all ({fmt(n0)})." if c_g0 == 0 else
                   f". Gemini's moves made it worse than no operator at all ({fmt(n0)})."))
        win = "engine"
    return with_note({"winner": win, "text": text})


def _engine_trace(turns: list[dict]) -> list[dict]:
    """The engine operator's decisions as trace rows (the fallback's trace: labeled engine, no AI)."""
    rows: list[dict] = []

    def add(**r):
        r["n"] = len(rows) + 1
        rows.append(r)

    for t in turns:
        a0 = t["alarm"][0] if t["alarm"] else None
        add(actor="engine", kind="alarm", tone="over", turn=t["turn"],
            title=f"Step {t['turn']}: {len(t['alarm'])} {'line' if len(t['alarm']) == 1 else 'lines'} over their rating" + (f", worst at {a0['pct']:.0f} %" if a0 else ""))
        tried = f"Played {t.get('plans', 1)} {'plan' if t.get('plans', 1) == 1 else 'plans'} forward to the end of the cascade"
        if not t["applied"]:
            add(actor="engine", kind="hold", tone="muted", turn=t["turn"], title=f"{tried}: doing nothing hits the fewest people ({_n(t.get('projected_if_nothing') or 0)})")
            continue
        words = []
        for a in t["applied"]:
            if a["type"] == "redispatch":
                words.append(f"move {_n(a['mw'])} MW from {a['lower']['name']} to {a['raise']['name']}")
            elif a["type"] == "shed":
                words.append(f"cut {_n(a['mw'])} MW {'at ' if a.get('substation') else 'across '}{a['name']}")
        s = "; ".join(words)
        add(actor="engine", kind="propose", tone="info", turn=t["turn"], title=s[:1].upper() + s[1:],
            detail=f"{tried}: {_n(t.get('projected') or 0)} people hit, against {_n(t.get('projected_if_nothing') or 0)} doing nothing")
        over = t.get("over_after") or []
        add(actor="engine", kind="verify", tone="over" if over else "holds", turn=t["turn"],
            title="Re-solved: no line is over its rating" if not over else f"Re-solved: {len(over)} still over, the worst at {over[0]['pct']:.0f} %: it trips")
    return rows


async def _compute(c: _Case, job: "_Job | None" = None) -> dict:
    t0 = time.perf_counter()
    ref = c.g.cascade_case(c.extra, c.trip, c.upgrades)  # the /api/grid/cascade result (flexible)
    none = await run_cascade(c.sim())
    same, why_not = _same_as_cascade(none, ref)
    if not same:
        log.error("operator: the no-operator run differs from the cascade route: %s", why_not)
    eng_turns: list[dict] = []
    none_v = _run_view(none, [], "none")
    if job is not None:
        job.runs = {"none": none_v["toll"]}
        job.progress["phase"] = "engine"

    async def engine_part():
        eng = await run_cascade(c.sim(), _engine_act_factory(eng_turns, job))
        view = _run_view(eng, eng_turns, "engine")
        if job is not None:
            job.runs = {**job.runs, "engine": view["toll"]}
        return view

    async def gemini_part():
        # the Gemini run and the engine's run go side by side: Gemini's waits for its answers are the engine's turn to compute
        if not configured():
            return None, None
        run = GeminiRun(job)
        if job is not None:
            job.progress["phase"] = "gemini"
        try:
            gem = await run_cascade(c.sim(), _gemini_act_factory(run))
        except Exception:  # noqa: BLE001 - a Gemini failure never takes the engine's finished result with it
            log.exception("operator: the Gemini run failed; the engine's operator is shown")
            return None, None
        return run, gem

    (gem_run, gem), eng_v = await asyncio.gather(gemini_part(), engine_part())
    if job is not None:
        job.progress["phase"] = "done"
    gem_v, by, why = None, "fallback", "Gemini is not configured on this server"
    if configured() and gem_run is None:
        why = "Gemini failed"
    if gem_run is not None:
        if gem_run.stopped_at is None or gem_run.stopped_at > 1:
            gem_v = _run_view(gem, gem_run.turns, "gemini")
            gem_v["stopped_at"] = gem_run.stopped_at
            gem_v["stop_reason"] = gem_run.status if gem_run.stopped_at else None
            by, why = "gemini", None
        else:
            why = {"unavailable": "Gemini unavailable", "slow": "Gemini too slow", "out_of_turns": "Gemini ran out of turns"}.get(gem_run.status, "Gemini unavailable")
    trace = gem_run.trace if gem_v is not None else _engine_trace(eng_turns)
    return {
        "case": c.header,
        "runs": {"none": none_v, "engine": eng_v, "gemini": gem_v},
        "shown": "gemini" if gem_v is not None else "engine",
        "by": by,
        "why": why,
        "verdict": _verdict(none_v, eng_v, gem_v),
        "matches_cascade": same,
        "mismatch": why_not,
        "trace": trace,
        "engine_trace": _engine_trace(eng_turns),
        "calls": gem_run.calls if gem_run else 0,
        "function_calls": gem_run.fcalls if gem_run else 0,
        "tool_calls": gem_run.tool_calls if gem_run else 0,
        "previews": gem_run.previews if gem_run else 0,
        "function_calling": bool(gem_run and gem_run.calls),
        "model": (gem_run.model if gem_run and gem_run.calls else None),
        "rules": RULES,
        "frame": FRAME,
        "limits": {"actions_per_step": MAX_ACTIONS, "turns": MAX_TURNS, "gemini_calls": MAX_GEMINI_CALLS},
        "ms": round((time.perf_counter() - t0) * 1000),
    }


# ------------------------------------------------------------------------------------ cache and jobs
_cache: "OrderedDict[str, tuple[float, float, dict]]" = OrderedDict()
_cache_lock = threading.Lock()


def _ckey(c: _Case) -> str:
    return json.dumps([c.key, configured()])


def cached(c: _Case) -> dict | None:
    with _cache_lock:
        hit = _cache.get(_ckey(c))
        if hit is None:
            return None
        ts, ttl, out = hit
        if time.time() - ts > ttl:
            _cache.pop(_ckey(c), None)
            return None
        _cache.move_to_end(_ckey(c))
    return {**copy.deepcopy(out), "cached": True}


def _store(c: _Case, out: dict) -> None:
    partial = out["by"] == "fallback" and configured() or bool((out["runs"]["gemini"] or {}).get("stopped_at"))
    with _cache_lock:
        _cache[_ckey(c)] = (time.time(), FALLBACK_TTL_S if partial else CACHE_TTL_S, copy.deepcopy(out))
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


class _Job:
    def __init__(self, c: _Case):
        self.id = secrets.token_urlsafe(12)
        self.c = c
        self.status = "running"
        self.progress: dict = {"phase": "engine", "turn": 0, "turns": MAX_TURNS, "calls": 0}
        self.runs: dict = {}
        self.trace: list[dict] = []
        self.result: dict | None = None
        self.error: str | None = None
        self.created = time.monotonic()


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


def _thread_main(job: _Job) -> None:
    """A job runs in its own thread with its own event loop: the engine's solves never block the server's loop."""
    try:
        out = asyncio.run(_compute(job.c, job))
        _store(job.c, out)
        job.result = {**out, "cached": False}
        job.progress["phase"] = "done"
        job.status = "done"
    except HTTPException as e:
        job.error = e.detail if isinstance(e.detail, str) else "The operator could not run this case"
        job.status = "error"
    except Exception:  # noqa: BLE001 - a background job always ends in a state the page can show
        log.exception("operator job failed")
        job.error = "The operator run failed on this case. Try again."
        job.status = "error"
    finally:
        with _jobs_lock:
            if _by_case.get(_ckey(job.c)) == job.id:
                del _by_case[_ckey(job.c)]


def _job_view(job: _Job) -> dict:
    out = {"job": job.id, "status": job.status, "progress": dict(job.progress), "runs": dict(job.runs), "trace": list(job.trace)}
    if job.status == "done":
        out["result"] = job.result
    elif job.status == "error":
        out["error"] = job.error
    return out


def start(c: _Case) -> dict:
    hit = cached(c)
    if hit is not None:
        return {"job": None, "status": "done", "progress": {"phase": "done"}, "runs": {k: (v or {}).get("toll") for k, v in hit["runs"].items()},
                "trace": hit["trace"], "result": hit}
    with _jobs_lock:
        _gc()
        jid = _by_case.get(_ckey(c))
        job = _jobs.get(jid) if jid else None
        if job is None or job.status != "running":
            if sum(1 for j in _jobs.values() if j.status == "running") >= RUNNING_MAX:
                raise HTTPException(status_code=429, detail="The operator is busy with other cases: try again in a moment")
            job = _Job(c)
            _jobs[job.id] = job
            _by_case[_ckey(c)] = job.id
            threading.Thread(target=_thread_main, args=(job,), daemon=True, name=f"operator-{job.id[:6]}").start()
    return _job_view(job)


# ------------------------------------------------------------------------------------ routes
@router.post("/api/operator/run")
@limiter.limit("30/minute")
async def operator_run(request: Request, body: CaseIn):
    """A case -> a job to poll ({job, status: running, progress, runs, trace}), or a finished run from the cache."""
    c = await run_in_threadpool(_Case, body)
    return start(c)


@router.get("/api/operator/jobs/{job_id}")
@limiter.limit("120/minute")
def operator_job(request: Request, job_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_-]{8,40}$")):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="This operator run is no longer available: run it again")
    return _job_view(job)
