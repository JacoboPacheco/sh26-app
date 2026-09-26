"""Forecast: a verdict before you run anything, and a size sweep that finds the cliff (owned by the
forecast track).

POST /api/forecast takes a case (grid.CaseIn) and answers, before the cascade is played:
  - verdict: "holds" (no line over its limit), "over_limit" (lines go over, but the cascade settles
    with nobody losing power) or "cascades" (people lose power), for the case's own mode (`firm`);
  - room_mw: the MW the main campus can take at its site before the first *new* line overloads (lines
    already over without it don't count), found with real solves of this case (its load level,
    knocked-out lines, upgrades and other campuses), and first_to_overload: the line that goes first;
  - cascade.flexible and cascade.firm: the full cascade in both modes (the very same
    Grid.cascade_case the cascade endpoint runs, so its numbers match what the replay shows);
  - sentence: one plain sentence that explains the outcome.

POST /api/forecast/sweep holds the case and varies the main campus's size from 100 to 2,000 MW in
100 MW steps: [{mw, over, people, steps, ...}] with both modes, the cliff (first size with a new
overload) and the first size where people lose power, plus a sentence about the curve's shape. It is
computed once per case in the background and answers within a few seconds with whatever is ready
(`complete`: false means ask again), so a slow case or a slow server shows a partial curve first.

Why the curve is often flat (measured, SPEC.md -> Expansion): at one site the blackout is a cliff,
not a slope. Below the site's room nothing happens; above it, in the flexible (plain) cascade the
campus's own connection usually trips and cuts the campus off, so its extra MW never reach anyone
else and 800 MW and 2,000 MW black out the same people. On firm service the campus is kept on and
other customers are cut instead, so size matters.

People are estimates (lost MW x the state's people per MW, grid.people_fields). The grid is a
synthetic model. Public like the grid endpoints (nothing is stored), with per-visitor rate limits.
"""

import json
import logging
import os
import threading
import time
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, HTTPException, Request

from grid import DEMO, MW_MAX, CaseIn, _case_header, case_firm_buses, check_case, region_code
from limiter import limiter
from powerflow import MAX_STEPS, OVER_PCT, Grid, area_of

router = APIRouter(tags=["forecast"])

SWEEP_MIN, SWEEP_MAX, SWEEP_STEP = 100, 2000, 100
ROOM_TOP = float(MW_MAX)  # room is searched up to the largest size a case accepts
ROOM_TOL = 0.05  # MW: bisect to this, so the room rounds like the heatmap's sensitivity headroom
AREAS_TOP = 5
FLAT_TOL = 0.02  # sizes whose blackout is within 2 % of the largest size's count as "the same"
_CACHE_MAX = 64

_room_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_lock = threading.Lock()


# ------------------------------------------------------------------------------------ the case
class _Case:
    """A validated case split into the main campus (whose size the forecast varies) and the rest."""

    def __init__(self, body: CaseIn):
        g, sites, trip, upgrades = check_case(body)
        self.g: Grid = g
        self.body = body
        self.region = region_code(body.region)
        self.sites = sites
        self.trip = trip
        self.upgrades = upgrades
        self.has_main = body.lat is not None  # check_case refused lat/lon/mw given only in part
        self.main = sites[0] if self.has_main else None
        others = sites[1:] if self.has_main else sites
        self.main_bus = g.site_bus(self.main.lat, self.main.lon) if self.main else None
        self.other_buses = [g.site_bus(s.lat, s.lon) for s in others]
        self.other_extra = g.extra_load(list(zip(self.other_buses, [s.mw for s in others])))
        self.active = np.ones(g.m, dtype=bool)
        for bid in trip:
            self.active[g.br_index[bid]] = False
        self.rate = g.rates_with(upgrades)
        self.extra, self.header = _case_header(g, sites, trip, upgrades)
        self._base_over: set[int] | None = None

    def key(self) -> tuple:
        """Everything but the main campus's size and the firm switch (neither changes the room)."""
        return (
            self.region,
            round(self.g.load_factor, 2),
            self.main_bus,
            tuple(sorted(self.trip)),
            tuple(sorted((int(k), round(float(v), 1)) for k, v in self.upgrades.items())),
            tuple(sorted(zip(self.other_buses, [round(float(s.mw), 1) for s in self.sites[1 if self.has_main else 0 :]]))),
        )

    def with_main(self, mw: float) -> np.ndarray:
        extra = self.other_extra.copy()
        if self.main_bus is not None:
            extra[self.main_bus] += mw
        return extra

    def firm_buses(self, firm: bool) -> list[int] | None:
        return case_firm_buses(self.g, self.sites, firm)

    def firm_buses_at(self, firm: bool) -> list[int] | None:
        """The firm buses for a sweep size (the main campus plus the others)."""
        if not firm:
            return None
        buses = ([self.main_bus] if self.main_bus is not None else []) + self.other_buses
        return buses or None

    def over_at(self, extra: np.ndarray):
        """(state, indices of branches over their rating) with `extra` added load."""
        st = self.g.solve(self.active, extra, self.rate)
        return st, set(np.flatnonzero(st.active & (st.loading_pct > OVER_PCT + 1e-6)).tolist())

    @property
    def base_over(self) -> set[int]:
        """Branches over their rating without the main campus (a heat wave or a storm can do that)."""
        if self._base_over is None:
            self._base_over = self.over_at(self.other_extra)[1]
        return self._base_over

    def new_over(self, mw: float):
        """(state, branches the main campus at `mw` pushes over their rating that weren't over without it)."""
        st, over = self.over_at(self.with_main(mw))
        return st, over - self.base_over


_pending: dict[tuple, threading.Event] = {}


def _cached(cache: OrderedDict, key: tuple, make):
    """`make()` once per key (an LRU of _CACHE_MAX); a second request for a key being computed waits
    for the first instead of computing it again (the startup prewarm and the demo's first click)."""
    while True:
        with _lock:
            hit = cache.get(key)
            if hit is not None:
                cache.move_to_end(key)
                return hit
            wait = _pending.get((id(cache), key))
            if wait is None:
                done = _pending[(id(cache), key)] = threading.Event()
                break
        if not wait.wait(timeout=30):
            done = threading.Event()  # the first computation is stuck: compute it here too, unregistered
            break
    try:
        value = make()
        with _lock:
            cache[key] = value
            while len(cache) > _CACHE_MAX:
                cache.popitem(last=False)
        return value
    finally:
        with _lock:
            if _pending.get((id(cache), key)) is done:
                del _pending[(id(cache), key)]
        done.set()


def _title(name: str) -> str:
    """'FORT MYERS 3' -> 'Fort Myers 3' (keeps the number, unlike area_of)."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in str(name).split())


def _line(g: Grid, i: int, pct: float) -> dict:
    fs, ts = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
    kv = float(g.br_kv[i])
    a, b = _title(g.sub_name[fs]), _title(g.sub_name[ts])
    transformer = fs == ts
    name = f"the transformer at {a}" if transformer else f"the {kv:,.0f} kV line {a} to {b}"
    return {
        "id": int(g.br_ids[i]),
        "name": name,
        "from": int(g.sub_ids[fs]),
        "to": int(g.sub_ids[ts]),
        "from_name": g.sub_name[fs],
        "to_name": g.sub_name[ts],
        "kv": kv,
        "transformer": transformer,
        "pct": round(float(pct), 1),
    }


# ------------------------------------------------------------------------------------ room
def _room(case: _Case) -> dict:
    """The MW the main campus can take before the first new overload, by real solves (what-ifs):
    scan in SWEEP_STEP steps to the first size that overloads something new, then bisect to ROOM_TOL.
    Cached per case (the size and the firm switch don't change it)."""

    def sizes():
        # the sweep's own sizes first (so the cliff it finds sits right above the room), then doubling
        yield from range(SWEEP_STEP, SWEEP_MAX + 1, SWEEP_STEP)
        mw = SWEEP_MAX
        while mw < ROOM_TOP:
            mw = min(ROOM_TOP, mw * 2)
            yield mw

    def make():
        lo, hi, hi_st, hi_over = 0.0, None, None, set()
        for mw in sizes():
            st, over = case.new_over(float(mw))
            if over:
                hi, hi_st, hi_over = float(mw), st, over
                break
            lo = float(mw)
        if hi is None:
            return {"room_mw": ROOM_TOP, "room_capped": True, "first_line": None}
        while hi - lo > ROOM_TOL:
            mid = (lo + hi) / 2
            st, over = case.new_over(mid)
            if over:
                hi, hi_st, hi_over = mid, st, over
            else:
                lo = mid
        first = max(hi_over, key=lambda i: hi_st.loading_pct[i])
        return {"room_mw": round(lo, 1), "room_capped": False, "first_line": _line(case.g, first, hi_st.loading_pct[first])}

    if case.main_bus is None:
        return {"room_mw": None, "room_capped": False, "first_line": None}
    return _cached(_room_cache, case.key(), make)


# ------------------------------------------------------------------------------------ cascades
def _areas(g: Grid, affected: dict, people: int) -> list[dict]:
    """The hardest-hit areas (the towns substations are named after), by existing load lost. Each
    area's people are its share of the cascade's own total, so the list never adds up to more."""
    by: dict[str, float] = {}
    for sid, mw in affected.items():
        i = g.sub_index.get(int(sid))
        if i is None:
            continue
        a = area_of(g.sub_name[i])
        by[a] = by.get(a, 0.0) + float(mw)
    total = sum(by.values())
    top = sorted(by.items(), key=lambda kv: -kv[1])[:AREAS_TOP]
    return [{"area": a, "mw": round(mw, 1), "people": int(people * mw / total) if total > 0 else 0} for a, mw in top]


def _summary(g: Grid, c: dict, over: int, campus_mw: float, firm: bool) -> dict:
    """One mode's cascade, summarized for the forecast (the numbers are the cascade's own)."""
    steps = c["steps"]
    cut_at = None
    if campus_mw > 0:
        cut_at = next((s["n"] for s in steps if s["site_dark_mw"] >= campus_mw - 0.5), None)
    peak = max([s.get("people", 0) for s in steps], default=0)
    people = int(c["people"])
    verdict = "cascades" if people > 0 else ("over_limit" if over > 0 else "holds")
    return {
        "mode": "firm" if firm else "flexible",
        "verdict": verdict,
        "steps": int(c["total_steps"]),
        "capped": bool(c["capped"]),
        "people": people,
        "peak_people": int(max(peak, people)),
        "lost_mw": c["lost_mw"],
        "outcome": c["outcome"],
        "areas_top5": _areas(g, c["affected"], people),
        "campus_cut_off_at_step": cut_at,  # the step the campus lost all its supply (None: it stayed on)
        "site_cut_off": bool(c["site_cut_off"]),
        "site_dark_mw": c["site_dark_mw"],
        "firm_held": c["firm_held"],
        "shed_mw": c["shed_mw"],
    }


# ------------------------------------------------------------------------------------ sentences
def _n(x: float) -> str:
    return f"{round(x):,}"


def _about(people: int) -> str:
    """'about 784,000' (3 significant figures); the exact count is shown next to it."""
    if people < 1000:
        return f"about {people:,}"
    digits = len(str(int(people)))
    return f"about {round(people, 3 - digits):,}"


def _plural(k: int, one: str, many: str) -> str:
    return f"{k:,} {one if k == 1 else many}"


def _sentence(case: _Case, mode: dict, over: int, room: dict) -> str:
    """One plain sentence that explains the outcome of this mode."""
    mw = case.main.mw if case.main else case.header["mw"]
    campus = "your data center" if len(case.sites) <= 1 else "your data centers"
    at = f"At {_n(mw)} MW" if case.main else "In this case"
    where = area_of(case.header["sub_name"]) if case.main else None
    steps = mode["steps"]
    step_txt = _plural(steps, "step", "steps") + (" (the model's limit)" if mode["capped"] else "")
    lines = _plural(over, "line goes", "lines go") + (" over its limit" if over == 1 else " over their limits")
    people = _about(mode["people"])

    if mode["verdict"] == "holds":
        if case.main and room["room_mw"] is not None:
            cap = f"more than {_n(room['room_mw'])} MW" if room["room_capped"] else f"{_n(room['room_mw'])} MW"
            return f"{where} can take {cap} before the first line overloads, so {_n(mw)} MW holds and every line stays within its limit."
        return "Every line stays within its limit, so nothing trips and no one loses power."
    if mode["verdict"] == "over_limit":
        return f"{at}, {lines}, but the grid settles after {step_txt} without anyone losing power."

    # people lose power
    if mode["mode"] == "firm":
        shed = mode["shed_mw"]
        if mode["firm_held"] is False:
            own = "the campus's own line trips" if len(case.sites) <= 1 else "a campus's own line trips"
            return (
                f"{at} even firm service can't hold: the operator cuts {_n(shed)} MW of other customers, "
                f"then {own} anyway, and {people} people lose power (estimate)."
            )
        if shed > 0.5:
            return f"{at} on firm service the operator keeps {campus} on by cutting {_n(shed)} MW of other customers: {people} people lose power (estimate) over {step_txt}."
    head = f"{at}, {lines} and the overload spreads for {step_txt} until {people} people lose power (estimate)"
    if over == 0:
        head = f"{at}, {people} people lose power (estimate) over {step_txt}"
    cut = mode["campus_cut_off_at_step"]
    if cut is not None and mode["mode"] == "flexible" and case.sites:
        if cut >= steps:
            return f"{head}; {campus} {'was' if len(case.sites) <= 1 else 'were'} cut off at step {cut}, which kept the blackout from spreading further."
        return f"{head}; {campus} {'was' if len(case.sites) <= 1 else 'were'} cut off at step {cut}, and the cascade went on for {_plural(steps - cut, 'more step', 'more steps')}."
    if case.sites and not mode["site_cut_off"]:
        return f"{head}, and {campus} {'stays' if len(case.sites) <= 1 else 'stay'} on."
    return f"{head}."


# ------------------------------------------------------------------------------------ forecast
@router.post("/api/forecast")
@limiter.limit("120/minute")  # cheap (a few sparse solves, the room is cached); the size slider fires it on every settle
def forecast(request: Request, body: CaseIn):
    case = _Case(body)
    g = case.g
    if not case.sites and not case.trip and g.load_factor == 1.0 and not case.upgrades:
        raise HTTPException(status_code=422, detail="Drop a data center (or knock out lines, or change the load) first")
    st, over_all = case.over_at(case.extra)
    over = len(over_all)
    room = _room(case)
    campus_mw = float(case.header["mw"] or 0.0)

    flex = _summary(g, g.cascade_case(case.extra, case.trip, case.upgrades), over, campus_mw, False)
    firm = None
    if case.sites:
        firm = _summary(g, g.cascade_case(case.extra, case.trip, case.upgrades, firm_buses=case.firm_buses(True)), over, campus_mw, True)
    for m in (flex, firm):
        if m is not None:
            m["sentence"] = _sentence(case, m, over, room)
    mine = firm if (body.firm and firm is not None) else flex

    # the rest of the case without the main campus (a heat wave or a storm can black people out alone)
    without = None
    if case.has_main:
        base_st, base_over = case.over_at(case.other_extra)
        if base_over or case.trip or case.other_buses or g.load_factor != 1.0:
            # firm covers only the campuses still in this case (the main one is left out here)
            wc = g.cascade_case(case.other_extra, case.trip, case.upgrades, firm_buses=list(case.other_buses) if (body.firm and case.other_buses) else None)
            without = {"over": len(base_over), "people": int(wc["people"]), "steps": int(wc["total_steps"])}
        else:
            without = {"over": 0, "people": g.people(base_st.lost_existing_mw), "steps": 0}

    first = room["first_line"]
    return {
        "region": case.region,
        "sub_name": case.header["sub_name"],
        "sub_area": case.header["sub_area"],
        "sub_lat": case.header["sub_lat"],
        "sub_lon": case.header["sub_lon"],
        "mw": case.header["mw"],  # total added load
        "main_mw": case.main.mw if case.main else None,
        "load_factor": g.load_factor,
        "firm": bool(body.firm),
        "verdict": mine["verdict"],
        "sentence": mine["sentence"],
        "over": over,  # lines over their limit in this case now (the what-if)
        "over_lines": [_line(g, i, st.loading_pct[i]) for i in sorted(over_all, key=lambda i: -st.loading_pct[i])[:5]],
        "room_mw": room["room_mw"],  # the main campus's room at this site, in this case (MW; nothing new is over at this size)
        "room_capped": room["room_capped"],  # no new overload up to room_mw (the largest size a case takes)
        "first_to_overload": first["name"] if first else None,
        "first_line": first,
        "cascade": {"flexible": flex, "firm": firm},
        "without_campus": without,
        "people_per_mw": round(g.people_per_mw, 2),
        "population": g.population,
    }


# ------------------------------------------------------------------------------------ sweep
def _shape(rows: list[dict], mode: str) -> dict:
    """What the curve of one mode says: where people start losing power, and where it goes flat."""
    key = f"people_{mode}"
    blackout = next((r["mw"] for r in rows if r[key] > 0), None)
    last = rows[-1][key]
    flat_from = None
    if blackout is not None and last > 0:
        for r in reversed(rows):
            if r["mw"] < blackout or abs(r[key] - last) > FLAT_TOL * last:
                break
            flat_from = r["mw"]
    return {"blackout_mw": blackout, "flat_from_mw": flat_from if (flat_from is not None and flat_from < SWEEP_MAX) else None, "people_at_max": last}


def _shape_sentence(rows: list[dict], cliff: int | None, flex: dict, firm: dict) -> str:
    if cliff is None:
        return f"No size up to {SWEEP_MAX:,} MW overloads a line here, so no one loses power."
    if flex["blackout_mw"] is None:
        return f"From {cliff:,} MW lines go over their limits, but up to {SWEEP_MAX:,} MW the grid settles without anyone losing power."
    s = []
    flat = flex["flat_from_mw"]
    cut = all(r["cut_off"] for r in rows if flat is not None and r["mw"] >= flat)
    if flat is not None and cut:
        s.append(
            f"From {flat:,} MW up, the blackout ends at {_about(flex['people_at_max'])} people however big the campus is: "
            "its own connection trips and cuts it off, so the extra megawatts never reach anyone else."
        )
    elif flat is not None:
        s.append(f"From {flat:,} MW up, the blackout ends at {_about(flex['people_at_max'])} people however big the campus is.")
    else:
        s.append(f"The blackout starts at {flex['blackout_mw']:,} MW and changes with size, to {_about(flex['people_at_max'])} people at {SWEEP_MAX:,} MW.")
    if firm["blackout_mw"] is not None:
        b = firm["blackout_mw"]
        at_b = next(r["people_firm"] for r in rows if r["mw"] == b)
        held = [r for r in rows if r["mw"] >= b and r["firm_held"]]
        top = held[-1] if held else None
        if top is not None and top["mw"] > b:
            s.append(
                f"On firm service the campus is kept on and other customers are cut instead, so size matters: "
                f"{_about(at_b)} people at {b:,} MW, {_about(top['people_firm'])} at {top['mw']:,} MW."
            )
        elif top is not None:
            s.append(f"On firm service the campus is kept on only at {b:,} MW ({_about(at_b)} people cut).")
        if top is None or any(not r["firm_held"] for r in rows if r["mw"] > top["mw"]):
            past = f"Above {top['mw']:,} MW even" if top is not None else "Even"
            s.append(f"{past} firm service can't hold the campus's own connection, so it trips anyway.")
    else:
        s.append(f"On firm service no one loses power up to {SWEEP_MAX:,} MW.")
    return " ".join(s)


def _row(case: _Case, mw: int) -> dict:
    """One size of the sweep: the lines it pushes over and both cascades (people are estimates)."""
    g = case.g
    extra = case.with_main(float(mw))
    _, new = case.new_over(float(mw))
    flex = g.cascade_case(extra, case.trip, case.upgrades)
    firm = g.cascade_case(extra, case.trip, case.upgrades, firm_buses=case.firm_buses_at(True))
    campus_mw = float(mw + case.other_extra.sum())
    return {
        "mw": mw,
        "over": len(new),  # lines this size pushes over their limit (not counting any already over)
        "people_flexible": int(flex["people"]),  # estimate
        "steps_flexible": int(flex["total_steps"]),
        "cut_off": bool(flex["site_dark_mw"] >= campus_mw - 0.5),  # flexible: the campus lost all its supply
        "people_firm": int(firm["people"]),
        "steps_firm": int(firm["total_steps"]),
        "firm_held": firm["firm_held"],
        "shed_mw": firm["shed_mw"],
    }


# every other size first, so a partial answer already spans 100-2,000 MW; then the ones in between
SWEEP_ORDER = list(range(SWEEP_MIN + SWEEP_STEP, SWEEP_MAX + 1, 2 * SWEEP_STEP)) + list(range(SWEEP_MIN, SWEEP_MAX, 2 * SWEEP_STEP))
SWEEP_WAIT_S = 2.5  # a request answers within about this long; the page asks again for the rest
# A sweep nobody has asked about for this long stops, so a judge dropping the campus in five places
# doesn't queue five sweeps ahead of the one on screen. A request counts as asking for its whole wait
# and the page asks again ~0.3 s after an answer, so a watched sweep is never this stale; a stopped
# one resumes (keeping its tested sizes) on the next ask.
SWEEP_STALE_S = 4.0
_job_slots = threading.BoundedSemaphore(2)  # sweeps computing at once (the rest queue)


class _SweepJob:
    """A case's sweep, computed once in a background thread: the room first (what-ifs only, fast),
    then the sizes in SWEEP_ORDER. Requests read whatever is ready, so a slow case (long cascades, a
    slow server) answers progressively instead of making the page wait. A job nobody asks about stops
    (`abandoned`); asking again starts a new one that keeps the sizes already tested."""

    def __init__(self, case: _Case, prev: "_SweepJob | None" = None, keep: bool = False):
        self.case = case
        self.cond = threading.Condition()
        self.rows: dict[int, dict] = dict(prev.rows) if prev is not None else {}
        self.room: dict | None = prev.room if prev is not None else None
        self.final: dict | None = None
        self.error: Exception | None = None
        self.done = False
        self.abandoned = False
        self.keep = keep  # the startup prewarm: nobody asks yet, finish anyway
        self.asked = time.monotonic()
        threading.Thread(target=self._run, daemon=True, name="forecast-sweep").start()

    def touch(self):
        self.asked = time.monotonic()

    def _stale(self) -> bool:
        return not self.keep and time.monotonic() - self.asked > SWEEP_STALE_S

    def _run(self):
        try:
            with _job_slots:
                if self._stale():
                    self.abandoned = True
                    return
                room = self.room or _room(self.case)
                with self.cond:
                    self.room = room
                    self.cond.notify_all()
                for mw in SWEEP_ORDER:
                    if mw in self.rows:
                        continue
                    if self._stale():
                        self.abandoned = True
                        return
                    row = _row(self.case, mw)
                    with self.cond:
                        self.rows[mw] = row
                        self.cond.notify_all()
                rows = [self.rows[mw] for mw in sorted(self.rows)]
                cliff = next((r["mw"] for r in rows if r["over"] > 0), None)
                flex, firm = _shape(rows, "flexible"), _shape(rows, "firm")
                final = {"cliff_mw": cliff, "curves": {"flexible": flex, "firm": firm}, "shape_sentence": _shape_sentence(rows, cliff, flex, firm)}
                with self.cond:
                    self.final = final
        except Exception as e:  # noqa: BLE001 - the request reports it as a readable 503
            logging.getLogger("uvicorn.error").exception("forecast: sweep failed")
            self.error = e
        finally:
            with self.cond:
                self.done = True
                self.cond.notify_all()

    def wait(self, seconds: float) -> dict:
        """Wait until the sweep is done or `seconds` pass, then a snapshot of what is ready."""
        end = time.monotonic() + seconds
        with self.cond:
            while not self.done and time.monotonic() < end:
                self.touch()
                self.cond.wait(max(0.0, min(1.0, end - time.monotonic())))
            self.touch()
            return {"rows": [self.rows[mw] for mw in sorted(self.rows)], "room": self.room, "final": self.final, "error": self.error}


_jobs: "OrderedDict[tuple, _SweepJob]" = OrderedDict()


def _job_for(case: _Case, keep: bool = False) -> _SweepJob:
    """The case's sweep job: the running or finished one, or a new one (a failed one is retried, an
    abandoned one resumes from the sizes it had tested)."""
    key = case.key()
    with _lock:
        job = _jobs.get(key)
        if job is not None and job.error is None and not job.abandoned:
            job.touch()
            _jobs.move_to_end(key)
            return job
        prev = job if job is not None and job.abandoned else None
        job = _jobs[key] = _SweepJob(case, prev=prev, keep=keep)
        while len(_jobs) > _CACHE_MAX:
            old = next((k for k, j in _jobs.items() if j.done), None)
            if old is None:
                break
            del _jobs[old]
        return job


@router.post("/api/forecast/sweep")
@limiter.limit("60/minute")  # the request itself only reads the case's background sweep (and may start it)
def sweep(request: Request, body: CaseIn):
    """The case with its main campus's size swept (`mw` is ignored; lat and lon are required).
    `complete` is false while sizes are still being tested: ask again for the rest."""
    if body.lat is None or body.lon is None:
        raise HTTPException(status_code=422, detail="Drop a data center first: the sweep needs its site (lat and lon)")
    case = _Case(CaseIn(**{**body.model_dump(), "mw": float(SWEEP_MIN)}))
    g = case.g
    snap = _job_for(case).wait(SWEEP_WAIT_S)
    if snap["error"] is not None:
        raise HTTPException(status_code=503, detail="Testing sizes at this site failed. Try again.")
    mode = "firm" if body.firm else "flexible"
    room, final, rows = snap["room"], snap["final"], snap["rows"]
    return {
        "region": case.region,
        "sub_name": case.header["sub_name"],
        "sub_area": case.header["sub_area"],
        "load_factor": g.load_factor,
        "firm": bool(body.firm),
        "complete": final is not None,  # false: some sizes are still being tested (ask again)
        "tested": len(rows),
        "total": len(SWEEP_ORDER),
        # the sizes tested so far, smallest first; `people`/`steps` follow the requested mode, and both modes are there
        "sizes": [{**r, "people": r[f"people_{mode}"], "steps": r[f"steps_{mode}"]} for r in rows],
        "cliff_mw": final["cliff_mw"] if final else None,  # the first size that overloads a line
        "blackout_mw": final["curves"][mode]["blackout_mw"] if final else None,  # the first size where people lose power
        # each mode's curve: {blackout_mw, flat_from_mw (from here up the blackout stays within 2 % of the
        # 2,000 MW one, or null), people_at_max}
        "curves": final["curves"] if final else None,
        "room_mw": room["room_mw"] if room else None,
        "room_capped": room["room_capped"] if room else False,
        "first_to_overload": room["first_line"]["name"] if room and room["first_line"] else None,
        "shape_sentence": final["shape_sentence"] if final else None,
        "base_over": len(case.base_over),
        "people_per_mw": round(g.people_per_mw, 2),
        "min_mw": SWEEP_MIN,
        "max_mw": SWEEP_MAX,
        "step_mw": SWEEP_STEP,
        "max_steps": MAX_STEPS,
    }


def _prewarm():
    """Start the demo hero's sweep (Fort Myers, backend/demo/expected_whatif.json) after startup, so
    the demo's first chart is instant. Failure only logs."""
    try:
        hero = json.loads((DEMO / "expected_whatif.json").read_text(encoding="utf-8"))["hero"]
        _job_for(_Case(CaseIn(lat=hero["lat"], lon=hero["lon"], mw=float(SWEEP_MIN))), keep=True)
    except Exception:  # noqa: BLE001 - a warm cache is a nicety, never a startup failure
        logging.getLogger("uvicorn.error").warning("forecast: hero sweep prewarm failed", exc_info=True)


if os.getenv("FORECAST_PREWARM", "1") != "0":  # scripts that import this module can turn it off
    _warm = threading.Timer(2.0, _prewarm)
    _warm.daemon = True
    _warm.start()
