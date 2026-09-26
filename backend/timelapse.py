"""The AI boom, year by year: a time-lapse 2026 -> 2035 of the reported data-center campuses in one state, all
connected together on the state's SYNTHETIC grid model, year after year (owned by the timelapse track).

  GET /api/timelapse?region=FL[&statuses=operating,under construction,announced][&fresh=1]
      -> {"state": "done", ...result} | {"state": "running", "estimate_s", "elapsed_s"} | {"state": "error", "detail"}
      A job keyed by (region, statuses): the first call starts it in a background thread, later calls poll the same
      URL. Florida with the default statuses is computed at startup (TIMELAPSE_WARM=0 turns that off); every other
      state or status set only when someone asks for it (LAZY).

THE CAMPUSES. The catalog (backend/demo/datacenters_us.json, read through catalog.catalog(): cleaned, duplicates folded,
no speculation about undisclosed tenants). Only REPORTED years are used (parse_arrivals):
  - a campus counts at its full reported size from the first year it is reported to come online, unless the report
    gives a first phase's size ("2027 (first 150 MW)", "2026 (first 200 MW); 2027 (+300 MW)"): then each reported
    phase arrives in its year and the rest of the reported size has no reported year (left out, listed);
  - years that date an announcement, a groundbreaking, an approval, a withdrawal, a denial, a pause or an expansion
    filing are not online years; neither is a year the research inferred;
  - a campus reported as operating counts from 2026 (it is online now); a reported year before 2026 counts from 2026;
  - a campus with no reported online year is left out and listed, as is one the model can't place (no substation
    within reach) and the reported MW after 2035.
The status filter defaults to catalog.IN_PLAY (operating, under construction, announced): everything not paused,
canceled, withdrawn or denied, as reported.

THE GRID. Each year, every campus online so far is connected at once (its nearest substation's highest-voltage bus,
the engine's rule) and one steady-state DC power flow solves the state: the peak line loading, the lines and
transformers at 90 %+ and over their rating, the overload MW (flow above the rating, summed) and the busiest elements.
With LOAD GROWTH (Florida only, where a published forecast is wired in): the model's own existing load also grows
year by year in step with NERC's 2025 Long-Term Reliability Assessment forecast for SERC-Florida Peninsula (total
internal demand, summer), as published, applied as a share on the model's load. The forecast may already count some
of these campuses (utilities' forecasts include large loads they know of), so with growth on the two can overlap:
the higher end.

CONTEXT. The Strengthen page's capacity study (1 GW campuses all connected at once, the cheapest upgrades): read from
unlock's study cache or, for Florida, the committed baked study (backend/demo/strengthen/FL_1000.json). Cited, never
recomputed here. Its campuses sit at the sites its search picked, not at the reported places.

Everything describes a SYNTHETIC grid model (Breakthrough Energy / Texas A&M), not any real utility's network. Every
named campus is "a campus of the reported size at the reported place, tested on a synthetic grid model": not a
prediction about the real project or the real utility.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request

import capacity
import catalog
import unlock
from grid import DEFAULT_REGION, REGIONS, check_site, grid_at, region_code
from limiter import limiter

router = APIRouter(tags=["timelapse"])
log = logging.getLogger("uvicorn.error")

START, END = 2026, 2035
YEARS = list(range(START, END + 1))
STATUS_OPTIONS = ("operating", "under construction", "announced", "paused/canceled")
DEFAULT_STATUSES = tuple(catalog.IN_PLAY)
HOT_PCT = 90.0  # "lines at 90 %+ of their rating" (CLAUDE.md -> Decisions -> STRAIN)
OVER_PCT = 100.0
TOP_BUSIEST = 5
MAP_MIN_PCT = 60.0  # a branch is drawn warm on the time-lapse map once it reaches this in some year ...
MAP_RISE = 10.0  # ... or once the campuses raise it by this many points
MAP_MAX = 700  # branches sent for the map at most (the hottest)
CACHE_SIZE = 12
RUNNING_MAX = 2
ERROR_TTL_S = 30.0
DEMO = Path(__file__).parent / "demo"
BAKED_FL = DEMO / "strengthen" / "FL_1000.json"
CAMPUS_MW = 1000.0  # the Strengthen study the page opens with (1 GW campuses)

FRAME = (
    "Each campus is a campus of the reported size at the reported place, tested on a SYNTHETIC grid model: not a "
    "prediction about the real project or the real utility. Statuses and years as reported."
)
METHOD = (
    "Only reported years are used. A campus counts at its full reported size from the first year it is reported to "
    "come online, unless a first phase's size is reported (then each phase arrives in its year and the rest waits for a "
    "reported year). When a report dates only a first phase or a range without the first phase's size, the full size "
    "counts from its first year and is marked as the higher end. A campus reported as operating counts from 2026. Years "
    "that date an announcement, groundbreaking, approval, power availability, pause, withdrawal or denial are not online "
    "years. Each year, every campus online so far is connected at "
    "once to its nearest substation and one steady-state DC power flow solves the state's synthetic model."
)

# NERC, 2025 Long-Term Reliability Assessment (January 2026), SERC-Florida Peninsula, "Demand, Resources, and Reserve
# Margins", Total Internal Demand (summer, MW), p. 112. Fetched 2026-09-26 from the URL below; numbers as published.
GROWTH = {
    "FL": {
        "area": "SERC-Florida Peninsula",
        "measure": "Total internal demand, summer peak (MW)",
        "mw": {2026: 54477, 2027: 54933, 2028: 55377, 2029: 56099, 2030: 56666, 2031: 57414, 2032: 58267, 2033: 58972, 2034: 59724, 2035: 60415},
        "source": {
            "title": "NERC, 2025 Long-Term Reliability Assessment (January 2026): SERC-Florida Peninsula, Demand, Resources, and Reserve Margins, p. 112",
            "url": "https://www.nerc.com/globalassets/our-work/assessments/nerc_ltra_2025.pdf",
        },
    }
}
GROWTH_NOTE = (
    "The model's existing load grows in step with the published forecast (each year's demand as a share of 2026's), "
    "applied to the synthetic model's own load. The forecast may already count some of these campuses, so the two can "
    "overlap: the higher end."
)
NO_GROWTH_NOTE = "No published state forecast is wired in for this state: the model's own load stays at its snapshot."

# ------------------------------------------------------------------------------------------------ reported years
_YEAR = re.compile(r"(?<!\d)(20\d\d)(?!\d)")
# words that date something other than coming online (checked just around each year)
_NOT_ONLINE = re.compile(
    r"announc|groundbreak|withdr|denied|cancel|blocked|paused|began|approv|filed|topped out|hearing|dates from|"
    r"collapse|scheduled|permit|inferred|expan|availab",
    re.I,
)
_GONE = re.compile(r"withdr|denied|cancel|blocked|collapse|paused", re.I)  # a paused/canceled campus's year text
_PHASE = re.compile(r"(?:first|initial|\+)\s*(?:[a-z]+\s+){0,2}?(\d[\d,.]*)\s*(gw|mw)\b", re.I)
_FULL = re.compile(r"full\s+(\d[\d,.]*)\s*(gw|mw)\b", re.I)
_SIZE = re.compile(r"\d\s*(?:mw|gw)\b", re.I)
# a year text that dates a first phase without its size: the size note (mw_basis) may report it
_PHASE_REF = re.compile(r"phase\s*(?:1|i|one)\b|first phase|initial|first building", re.I)
# a year text that dates a first phase, a range or a ramp: counting the full size from its first year is the higher end
_PHASED = re.compile(r"first|initial|phase|ramp|(?<!\d)20\d\d\s*(?:-|\u2013|to)\s*(?:20)?\d\d(?!\d)", re.I)
_FULL_WORD = re.compile(r"\bfull\b", re.I)  # "2030 (full build-out)": that year is the full size, not a first phase
HIGHER_END = "the report dates a first phase or a range without the first phase's size, so the full size from its first year is the higher end"
_BASIS_PHASE = re.compile(
    r"phase\s*(?:1|i|one)\b[^.;]{0,40}?(\d[\d,.]*)\s*(mw|gw)\b|(?:first|initial) phase[^.;]{0,30}?(\d[\d,.]*)\s*(mw|gw)\b", re.I
)


def _mw_of(num: str, unit: str) -> float:
    v = float(num.replace(",", ""))
    return v * 1000.0 if unit.lower() == "gw" else v


def _basis_phase(year_text: str, basis: str) -> float | None:
    """A first phase's size from the size note, when the year text dates a first phase without one ("2026-2027 (phase 1)"
    with "Phase 1 is 240 MW")."""
    if not _PHASE_REF.search(year_text or "") or _SIZE.search(year_text or ""):
        return None
    m = _BASIS_PHASE.search(basis or "")
    if not m:
        return None
    num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return _mw_of(num, unit)


def parse_arrivals(year_text: str, status: str, mw: float, basis: str = "") -> tuple[list[tuple[int, float]], float, str, bool]:
    """(arrivals [(year, MW)], MW with no reported year, how it was read, higher end?) from a catalog entry's reported year
    text (and its size note, for a first phase's size). Years before START count at START; years after END are kept (the
    caller lists them as later). "Higher end" marks a full size counted from the first year of a first phase or a range
    whose first size isn't reported (the stated rule, flagged on screen)."""
    text = str(year_text or "").strip()
    total = float(mw or 0.0)
    if total <= 0:
        return [], 0.0, "no reported size", False
    if status == "paused/canceled" and _GONE.search(text):
        return [], total, "paused, canceled, withdrawn or denied, as reported: no online year", False
    clauses, raw = [], []  # (year, MW or None, full?), the clause's text
    for clause in re.split(r";", text):
        kept = None
        for m in _YEAR.finditer(clause):
            around = clause[max(0, m.start() - 40) : m.start()] + " " + clause[m.end() : m.end() + 16]
            if not _NOT_ONLINE.search(around):
                kept = int(m.group(1))
                break
        if kept is None:
            continue
        ph, fu = _PHASE.search(clause), _FULL.search(clause)
        amount = _mw_of(*ph.groups()) if ph else (_mw_of(*fu.groups()) if fu else None)
        clauses.append((kept, amount, bool(fu) and not ph))
        raw.append(clause)
    if not clauses:
        if status == "operating":
            return [(START, total)], 0.0, "operating, as reported: counted from 2026", False
        return [], total, "no reported online year", False
    first = _basis_phase(text, basis)
    from_basis = first is not None and first < total and not clauses[0][1]
    if from_basis:
        clauses[0] = (clauses[0][0], first, False)
    y0 = clauses[0][0]
    if not any(a for _, a, _ in clauses):
        how = "online before 2026, as reported: counted from 2026" if y0 < START else f"full reported size from {y0}, the first reported online year"
        # a campus reported as operating counts from 2026 because it is online now: no flag
        higher = status != "operating" and (len(clauses) > 1 or (bool(_PHASED.search(text)) and not _FULL_WORD.search(raw[0])))
        return [(max(y0, START), total)], 0.0, how + (f"; {HIGHER_END}" if higher else ""), higher
    if not clauses[0][1] and any(full for _, _, full in clauses[1:]):
        # "H2 2027 (initial); full 401 MW early 2028": the first phase's size isn't reported, so the full size counts from
        # the first reported online year (the rule), flagged as the higher end
        yf = next(y for y, _, full in clauses[1:] if full)
        how = f"full reported size from {y0}, the first reported online year (the full size is reported for {yf}); {HIGHER_END}"
        return [(max(y0, START), total)], 0.0, how, True
    arrivals, used, last = [], 0.0, None
    for k, (y, amount, full) in enumerate(clauses):
        if not amount:
            continue
        m = total - used if full else min(amount, total - used)
        if m > 0:
            arrivals.append((max(y, START), m))
            used += m
            last = k
    rest = max(total - used, 0.0)
    how = "reported phases: " + ", ".join(f"{_fmt(m)} MW in {y}" for y, m in arrivals) + (" (the first phase's size from the size note)" if from_basis else "")
    nxt = next((y for y, amount, _ in clauses[(last or 0) + 1 :] if not amount), None)
    if rest > 0.5 and nxt is not None:
        arrivals.append((max(nxt, START), rest))
        how += f"; the other {_fmt(rest)} MW in {nxt}, the next reported year"
        rest = 0.0
    elif rest > 0.5 and status == "operating":
        arrivals.append((START, rest))
        how += f"; the other {_fmt(rest)} MW is operating, as reported (counted from 2026)"
        rest = 0.0
    elif rest > 0.5:
        how += f"; the other {_fmt(rest)} MW has no reported year"
    merged: dict[int, float] = {}
    for y, m in arrivals:
        merged[y] = merged.get(y, 0.0) + m
    return sorted(merged.items()), rest, how, False


def _fmt(x: float) -> str:
    return f"{math.floor(float(x) + 0.5):,}"


# ------------------------------------------------------------------------------------------------ the campuses
def _campuses(code: str, statuses: tuple[str, ...]) -> tuple[list[dict], dict, dict]:
    """(the campuses in play with their arrivals, the left-out lists, the MW accounting) for one state."""
    cat = catalog.catalog()
    rows = [e for e in cat["entries"].values() if e["state"] == code and e["duplicate_of"] is None]
    campuses: list[dict] = []
    off_model = (f"the reported place is outside what the synthetic {REGIONS[code]['name']} model covers (no model substation "
                 "within reach): a limit of the model, not a statement about the real grid there")
    left = {"no_year": [], "later": [], "off_model": [], "status": [], "no_size": []}
    acc = {"catalog_mw": 0.0, "status_mw": 0.0, "placed_mw": 0.0, "later_mw": 0.0, "no_year_mw": 0.0, "off_model_mw": 0.0}
    for e in rows:
        mw = float(e["mw"] or 0.0)
        acc["catalog_mw"] += mw
        brief = _brief(e)
        if mw <= 0:
            left["no_size"].append({**brief, "why": "no reported size"})
            continue
        if e["status"] not in statuses:
            left["status"].append({**brief, "why": f"status {e['status']}, as reported (not selected)"})
            acc["status_mw"] += mw
            continue
        if e["lat"] is None or e["lon"] is None:
            left["off_model"].append({**brief, "why": "no reported place"})
            acc["off_model_mw"] += mw
            continue
        try:
            check_site(float(e["lat"]), float(e["lon"]), min(mw, 50000.0), code)
        except HTTPException:
            left["off_model"].append({**brief, "why": off_model})
            acc["off_model_mw"] += mw
            continue
        arrivals, rest, how, higher = parse_arrivals(e["year"], e["status"], mw, e.get("mw_basis") or "")
        now = [(y, m) for y, m in arrivals if y <= END]
        later = [(y, m) for y, m in arrivals if y > END]
        if rest > 0.5:
            acc["no_year_mw"] += rest
            if not now and not later:
                left["no_year"].append({**brief, "why": how})
        if later:
            lm = sum(m for _, m in later)
            acc["later_mw"] += lm
            left["later"].append({**brief, "why": f"{_fmt(lm)} MW reported for {', '.join(str(y) for y, _ in later)}", "later_mw": round(lm)})
        if not now:
            continue
        acc["placed_mw"] += sum(m for _, m in now)
        campuses.append({**brief, "arrivals": [{"year": y, "mw": round(m, 1)} for y, m in now], "first_year": now[0][0],
                         "placed_mw": round(sum(m for _, m in now), 1), "no_year_mw": round(rest, 1), "read": how,
                         "higher_end": higher})
    for k in acc:
        acc[k] = round(acc[k], 1)
    acc["selected_mw"] = round(acc["placed_mw"] + acc["later_mw"] + acc["no_year_mw"] + acc["off_model_mw"], 1)
    campuses.sort(key=lambda c: (c["first_year"], -c["placed_mw"]))
    return campuses, left, acc


def _brief(e: dict) -> dict:
    """The sourced facts shown for a named campus: name, reported developer, place, reported MW, status, year text."""
    return {
        "id": e["id"], "name": e["name"], "company": e["company"], "city": e["city"], "county": e["county"],
        "state": e["state"], "lat": e["lat"], "lon": e["lon"], "mw": e["mw"], "status": e["status"],
        "year_text": e["year"], "sources": [{"title": s["title"], "url": s["url"]} for s in (e.get("sources") or [])[:3]],
    }


# ------------------------------------------------------------------------------------------------ the grid, year by year
def _strain(g, st) -> dict:
    pct = np.where(st.active, st.loading_pct, 0.0)
    over = st.active & (st.loading_pct > OVER_PCT + 1e-6)
    xf = g.bus_sub_idx[g.f] == g.bus_sub_idx[g.t]
    rate = st.rate if st.rate is not None else g.rate
    return {
        "peak_pct": round(float(pct.max()) if pct.size else 0.0, 1),
        "hot": int(np.count_nonzero(pct >= HOT_PCT)),
        "over": int(np.count_nonzero(over)),
        "over_lines": int(np.count_nonzero(over & ~xf)),
        "over_transformers": int(np.count_nonzero(over & xf)),
        "over_mw": round(float(np.sum(np.abs(st.flow[over]) - rate[over])), 1),
        "unserved_mw": round(float(st.lost_mw), 1),  # load the model's plants can't supply (0 in almost every case)
    }


def _busiest(g, st, n: int = TOP_BUSIEST) -> list[dict]:
    pct = np.where(st.active, st.loading_pct, 0.0)
    idx = np.argsort(-pct)[:n]
    out = []
    for i in idx:
        b = unlock._branch(g, int(i))
        out.append({"branch_id": b["branch_id"], "kind": b["kind"], "kv": b["kv"], "label": b["label"], "short": b["short"], "where": b["where"],
                    "mid": b["mid"], "pct": round(float(pct[i]), 1)})
    return out


def _series(g, buses: list[int], campuses: list[dict], factors: dict[int, float] | None) -> tuple[list[dict], list[np.ndarray]]:
    """One frame per year (plus the grid alone first): (frames, per-branch loading % per frame)."""
    ones = np.ones(g.m, dtype=bool)
    frames = [{"year": None, "label": "The grid alone", "strain": _strain(g, g.base), "busiest": _busiest(g, g.base), "load_factor": 1.0}]
    pcts = [np.where(g.base.active, g.base.loading_pct, 0.0)]
    last_key, last = None, None
    for y in YEARS:
        on = [(buses[k], sum(a["mw"] for a in c["arrivals"] if a["year"] <= y)) for k, c in enumerate(campuses)]
        on = [(b, m) for b, m in on if m > 0]
        f = round(factors[y], 4) if factors else 1.0
        key = (tuple(on), f)
        if key != last_key:
            gy = g if f == 1.0 else g.variant(f)
            st = gy.solve(ones, gy.extra_load(on))
            alone = gy.base
            last = (gy, st, alone)
            last_key = key
        gy, st, alone = last
        frames.append({
            "year": y,
            "load_factor": f,
            "strain": _strain(gy, st),
            "alone": _strain(gy, alone) if factors else None,  # with growth: the grid alone at that year's load
            "busiest": _busiest(gy, st),
        })
        pcts.append(np.where(st.active, st.loading_pct, 0.0))
    return frames, pcts


def compute(code: str, statuses: tuple[str, ...]) -> dict:
    t0 = time.perf_counter()
    g = grid_at(1.0, code)
    campuses, left, acc = _campuses(code, statuses)
    buses = [g.site_bus(float(c["lat"]), float(c["lon"])) for c in campuses]
    for c, b in zip(campuses, buses):
        s = int(g.bus_sub_idx[b])
        c["sub"] = int(g.sub_ids[s])
        c["sub_name"] = g.sub_name[s]
        c["sub_lat"], c["sub_lon"] = round(float(g.sub_lat[s]), 4), round(float(g.sub_lon[s]), 4)
    frames, pcts = _series(g, buses, campuses, None)
    growth = GROWTH.get(code)
    gframes, gpcts = None, None
    if growth:
        base = growth["mw"][START]
        factors = {y: growth["mw"][y] / base for y in YEARS}
        gframes, gpcts = _series(g, buses, campuses, factors)
    # the map's warm lines: every branch that reaches MAP_MIN_PCT in some frame or that the campuses raise by MAP_RISE
    stack = np.vstack(pcts + (gpcts or []))
    peak = stack.max(axis=0)
    rise = peak - pcts[0]
    pick = np.flatnonzero((peak >= MAP_MIN_PCT) | (rise >= MAP_RISE))
    pick = pick[np.argsort(-peak[pick])][:MAP_MAX]
    lines = {
        "ids": [int(g.br_ids[i]) for i in pick],
        "pct": [[round(float(p[i]), 1) for i in pick] for p in pcts],
        "pct_growth": [[round(float(p[i]), 1) for i in pick] for p in gpcts] if gpcts else None,
    }
    mw_cum = []
    for y in YEARS:
        mw_cum.append(round(sum(a["mw"] for c in campuses for a in c["arrivals"] if a["year"] <= y), 1))
    for k, y in enumerate(YEARS, start=1):
        new = [c["id"] for c in campuses if any(a["year"] == y for a in c["arrivals"])]
        count = sum(1 for c in campuses if c["first_year"] <= y)
        for fr in (frames, gframes):
            if fr:
                fr[k]["mw"] = mw_cum[k - 1]
                fr[k]["new"] = new
                fr[k]["campuses"] = count
    for fr in (frames, gframes):
        if fr:
            fr[0]["mw"], fr[0]["new"], fr[0]["campuses"] = 0.0, [], 0
    name = REGIONS[code]["name"]
    return {
        "region": code,
        "region_name": name,
        "synthetic": True,
        "note": f"Synthetic grid model of {name} (Breakthrough Energy / Texas A&M), not any utility's network.",
        "frame": FRAME,
        "method": METHOD,
        "years": YEARS,
        "statuses": list(statuses),
        "status_options": _status_options(code),
        "hot_pct": HOT_PCT,
        "campuses": campuses,
        "left_out": left,
        "left_out_counts": {k: len(v) for k, v in left.items()},
        "accounting": acc,
        "frames": frames,
        "growth": (
            {"frames": gframes, "area": growth["area"], "measure": growth["measure"], "mw": {str(k): v for k, v in growth["mw"].items()},
             "source": growth["source"], "note": GROWTH_NOTE}
            if growth else None
        ),
        "growth_note": None if growth else NO_GROWTH_NOTE,
        "lines": lines,
        "capacity": capacity_context(code),  # replaced per request: the endpoint reads it fresh
        "catalog": {"generated": catalog.catalog().get("generated"), "file": "backend/demo/datacenters_us.json"},
        "seconds": round(time.perf_counter() - t0, 2),
    }


def _status_options(code: str) -> list[dict]:
    cat = catalog.catalog()
    rows = [e for e in cat["entries"].values() if e["state"] == code and e["duplicate_of"] is None]
    return [{"id": s, "count": sum(1 for e in rows if e["status"] == s), "mw": round(sum(float(e["mw"] or 0) for e in rows if e["status"] == s)),
             "default": s in DEFAULT_STATUSES} for s in STATUS_OPTIONS]


# ------------------------------------------------------------------------------------------------ Strengthen, cited
_baked: dict = {}


def _baked_fl() -> dict | None:
    """Florida's committed baked study (about 1 MB): parsed once per file version, not on every request."""
    try:
        mtime = BAKED_FL.stat().st_mtime
    except OSError:
        return None
    if _baked.get("mtime") != mtime:
        try:
            result = json.loads(BAKED_FL.read_text(encoding="utf-8")).get("result")
        except (OSError, ValueError):
            result = None
        _baked.update(mtime=mtime, result=result)
    return _baked.get("result")


def capacity_context(code: str) -> dict:
    """The Strengthen page's answer for 1 GW campuses, read (not recomputed): the study cache, else Florida's baked file."""
    key = (code, CAMPUS_MW, 1.0)
    with unlock._cache_lock:
        res = unlock._cache.get(key)
    origin = "study"
    if res is None and code == DEFAULT_REGION:
        res = _baked_fl()
        origin = "baked"
    cap = (res or {}).get("capacity") or {}
    firm = cap.get("firm") or {}
    name = REGIONS[code]["name"]
    if not firm.get("steps") or firm.get("today") is None:
        return {"status": "not_run", "note": f"Open Strengthen the grid to compute {name}'s answer; nothing is computed here."}
    today = int(firm["today"])
    n = int(capacity.headline_count(firm))
    cost = 0.0
    if n > today:
        cost = float(next((st["cum_cost"]["high"] for st in firm["steps"] if st["n"] == n), 0.0))
    plants = cap.get("plants") or {}
    return {
        "status": "ready",
        "origin": origin,
        "campus_mw": CAMPUS_MW,
        "today": today,
        "today_mw": today * CAMPUS_MW,
        "with_upgrades": n,
        "with_upgrades_mw": n * CAMPUS_MW,
        "cost_high": round(cost),
        "reserve_room_mw": plants.get("room_mw"),
        "reserve_pct": plants.get("reserve_pct"),
        "plants_sentence": plants.get("sentence"),
        "unit": cap.get("unit"),
        "source": "Strengthen the grid: the capacity study for 1 GW campuses (all connected at once, no line or transformer over its rating; "
                  "the cheapest upgrades within the page's default budget, high-end cost)",
        "caveat": "Its campuses sit at the sites the Strengthen search picked, not at the reported places, so the two numbers compare sizes, not sites.",
    }


# ------------------------------------------------------------------------------------------------ jobs
_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_running: dict[tuple, float] = {}
_errors: dict[tuple, tuple[float, str]] = {}
_lock = threading.Lock()
_PINNED: set[tuple] = set()


def _estimate(code: str) -> int:
    buses = int(REGIONS[code].get("buses") or 2000)
    return max(2, int(round(1 + buses / 1500)))


def _run(key: tuple) -> None:
    code, statuses = key
    try:
        result = compute(code, statuses)
    except Exception as e:  # noqa: BLE001 — a readable error for the page; the next request tries again
        log.exception("timelapse: %s failed", key)
        with _lock:
            _running.pop(key, None)
            _errors[key] = (time.monotonic(), "The engine couldn't finish this time-lapse. Try again in a minute.")
        return
    with _lock:
        _running.pop(key, None)
        _errors.pop(key, None)
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > CACHE_SIZE:
            old = next((k for k in _cache if k not in _PINNED), None)
            if old is None:
                break
            del _cache[old]
    log.info("timelapse: %s %s in %.1f s", code, ",".join(statuses), result["seconds"])


def _start(key: tuple) -> None:
    _running[key] = time.monotonic()
    threading.Thread(target=_run, args=(key,), name="timelapse", daemon=True).start()


def _parse_statuses(raw: str | None) -> tuple[str, ...]:
    if raw is None or not raw.strip():
        return DEFAULT_STATUSES
    want = {s.strip().lower() for s in raw.split(",") if s.strip()}
    bad = sorted(want - set(STATUS_OPTIONS))
    if bad:
        raise HTTPException(status_code=422, detail=f"Unknown status {bad[0]!r}: use {', '.join(STATUS_OPTIONS)}")
    if not want:
        raise HTTPException(status_code=422, detail="Pick at least one status")
    return tuple(s for s in STATUS_OPTIONS if s in want)


@router.get("/api/timelapse")
@limiter.limit("120/minute")  # the page polls this URL while the job runs
def timelapse(
    request: Request,
    region: str = Query(DEFAULT_REGION, max_length=8),
    statuses: str | None = Query(None, max_length=120),
):
    if (region or "").strip().upper() == "US":
        raise HTTPException(status_code=422, detail="Open a state first: the time-lapse runs on one state's model")
    code = region_code(region)
    key = (code, _parse_statuses(statuses))
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
        elif key in _running:
            return {"state": "running", "region": code, "estimate_s": _estimate(code), "elapsed_s": round(time.monotonic() - _running[key], 1)}
        else:
            err = _errors.get(key)
            if err is not None and time.monotonic() - err[0] < ERROR_TTL_S:
                return {"state": "error", "region": code, "detail": err[1]}
            _errors.pop(key, None)
            if len(_running) >= RUNNING_MAX:
                raise HTTPException(status_code=429, detail="The engine is busy with other time-lapses. Try again in a minute.")
            _start(key)
    if hit is not None:
        # the Strengthen answer is read fresh on every hit (outside our lock), so a study run after the time-lapse was
        # computed shows up the next time the page opens
        return {"state": "done", **hit, "capacity": capacity_context(code)}
    return {"state": "running", "region": code, "estimate_s": _estimate(code), "elapsed_s": 0.0}


# ------------------------------------------------------------------------------------------------ warm-up (Florida only)
def _warm() -> None:
    key = (DEFAULT_REGION, DEFAULT_STATUSES)
    with _lock:
        if key in _cache or key in _running:
            return
        _running[key] = time.monotonic()
    _run(key)


if os.getenv("TIMELAPSE_WARM", "1") != "0":
    _PINNED.add((DEFAULT_REGION, DEFAULT_STATUSES))
    _warmer = threading.Timer(4.0, _warm)
    _warmer.daemon = True  # never holds the process open at shutdown
    _warmer.start()
