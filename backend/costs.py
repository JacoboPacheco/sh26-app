"""What a case costs (the cost track): four labeled estimates, each with its formula, its assumption and
its published source, plus a Gemini estimate over the same facts with the formula as its fallback.

POST /api/cost      a case (grid.CaseIn) + `hours_out` -> the deterministic estimate (fast, no AI)
POST /api/cost/ai   the same body -> the estimate plus Gemini's own low-high per line ("fallback": true
                    when Gemini wasn't used: no key, quota gone, timeout, or an unusable answer)

The case is re-run here, server-side: the cascade (with firm campuses honored) gives the lost load, and
the Fix it greedy (fixit.greedy_fix) gives the upgrades that keep every line under its limit. Then:

1. The blackout = existing load lost (MW, where the cascade ends) x hours out = MWh unserved, x a value of
   lost load (VoLL) built from LBNL's interruption cost estimates by customer class and duration
   (Sullivan, Schellenberg & Blundell, LBNL-6941E, 2015, Table ES-1, 2013 $), weighted by EIA's 2024
   retail sales by sector (Electric Power Annual Table 2.2: residential 37.3 %, the rest business),
   escalated to 2024 $ by CPI-U. Low: all business load at LBNL's medium-and-large business rate.
   High: one business MWh in ten at LBNL's small-business rate (an assumption; small businesses are
   customers under 50,000 kWh a year and cost far more per kWh unserved).
2. Upgrades to prevent it = every rating increase (the case's own Fix-it upgrades plus the greedy's),
   priced with Black & Veatch's capital costs for WECC (2014 $, escalated by CPI-U): a line by its
   straight-line length between its substations (x the report's short-line multipliers) at its
   voltage class's cost per mile, low = reconductoring (the conductor's share of a new line, ACSS),
   high = a new single-circuit line; above 2x its rating (more than advanced conductors can give, per
   GridLab / UC Berkeley's 2035 report) low = a new line, high = a new double-circuit line. A
   transformer: low = the added MVA as a unit alongside, high = a whole new unit at the new rating, at
   the report's $7,250-$13,450 per MVA.
3. The campus's power bill = MW x 8,760 h x a load factor of 50-80 % (LBNL's 2024 U.S. data center
   report: 50 % average capacity utilization; AI training servers run 80 % of the time) x the state's
   2024 average industrial price (EIA Electric Power Annual Table 2.10).
4. Who pays = the upgrade cost recovered over 40 years at 7 % a year (a capital recovery factor,
   illustrative) spread over the state's households (Census population / 2.5 people per household).
   Low: households pay their share of sales (EIA: 37.3 %); high: households pay it all.

Every number is an estimate on a SYNTHETIC grid model (Breakthrough Energy / Texas A&M), never a real
utility's network or a real event. Public like the grid endpoints (no login, nothing stored).
"""

import json
import logging
import math
import threading
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from fixit import greedy_fix
from grid import (
    MAX_UPGRADES,
    PEOPLE_PER_HOUSEHOLD,
    POPULATION,
    POPULATION_SOURCE,
    REGIONS,
    CaseIn,
    _case_header,
    _km,
    case_firm_buses,
    check_case,
    region_code,
)
from limiter import limiter
from llm import complete_json

router = APIRouter(tags=["costs"])
log = logging.getLogger("uvicorn.error")

HOURS_MIN, HOURS_MAX = 0.5, 72.0
DEFAULT_HOURS = 6.0

# ---------------------------------------------------------------------------------- published figures
# CPI-U annual averages (U.S. Bureau of Labor Statistics): 2013 232.957, 2014 236.736, 2024 313.689.
CPI_2013, CPI_2014, CPI_2024 = 232.957, 236.736, 313.689
TO_2024_FROM_2013 = CPI_2024 / CPI_2013
TO_2024_FROM_2014 = CPI_2024 / CPI_2014

# LBNL-6941E Table ES-1: cost per unserved kWh (2013 $) by interruption duration (hours) and class.
VOLL_HOURS = [0.5, 1.0, 4.0, 8.0, 16.0]
VOLL_LARGE = [37.4, 21.8, 12.1, 12.9, 12.7]  # medium and large C&I (over 50,000 kWh a year)
VOLL_SMALL = [474.1, 295.0, 214.3, 267.3, 258.0]  # small C&I (under 50,000 kWh a year)
VOLL_HOME = [5.9, 3.3, 1.6, 1.4, 1.3]  # residential
SMALL_SHARE_HIGH = 0.10  # the high case: one business MWh in ten at the small-business rate (an assumption)

# EIA Electric Power Annual 2024, Table 2.2: 2024 retail sales (MWh) — residential 1,482,873,586 of
# 3,975,381,832 total.
RES_SHARE = 1_482_873_586 / 3_975_381_832  # 0.373

# EIA Electric Power Annual 2024, Table 2.10: average price to industrial customers, 2024, cents/kWh.
INDUSTRIAL_PRICE_2024 = {
    "AL": 7.25, "AR": 6.61, "AZ": 7.90, "CA": 21.53, "CO": 8.62, "CT": 17.12, "DE": 8.49, "FL": 8.50,
    "GA": 7.21, "IA": 6.80, "ID": 7.69, "IL": 8.83, "IN": 8.15, "KS": 7.73, "KY": 6.50, "LA": 5.61,
    "MA": 18.19, "MD": 10.01, "ME": 12.46, "MI": 8.26, "MN": 9.15, "MO": 7.87, "MS": 6.81, "MT": 7.59,
    "NC": 7.77, "ND": 7.25, "NE": 7.66, "NH": 16.21, "NJ": 11.93, "NM": 5.43, "NV": 8.64, "NY": 9.17,
    "OH": 7.10, "OK": 5.84, "OR": 8.05, "PA": 7.87, "RI": 19.70, "SC": 6.84, "SD": 8.28, "TN": 6.21,
    "TX": 6.12, "UT": 7.86, "VA": 8.99, "VT": 11.58, "WA": 6.61, "WI": 8.54, "WV": 7.81, "WY": 7.96,
}  # fmt: skip
US_INDUSTRIAL_PRICE_2024 = 8.13

# LBNL 2024 United States Data Center Energy Usage Report (Shehabi et al., Dec 2024): "an average capacity
# utilization rate of 50%"; AI servers doing training "a constant 80% operational time".
LOAD_FACTOR_LOW, LOAD_FACTOR_HIGH = 0.50, 0.80
HOURS_PER_YEAR = 8760

# Black & Veatch, "Capital Costs for Transmission and Substations: Updated Recommendations for WECC
# Transmission Expansion Planning" (2014). Table 2-1, new line cost per mile, 2014 $, ACSR, > 10 miles.
LINE_SINGLE = {230: 959_700, 345: 1_343_800, 500: 1_919_450}
LINE_DOUBLE = {230: 1_536_400, 345: 2_150_300, 500: 3_071_750}
# Section 2.3, re-conductoring: the conductor is 35 / 45 / 55 % of a new line's capital cost.
CONDUCTOR_SHARE = {230: 0.35, 345: 0.45, 500: 0.55}
ACSS_MULT = 1.08  # Table 2-2: ACSS conductor multiplier (a higher-temperature conductor than ACSR)
# Table 2-4: length multipliers (short lines cost more per mile): under 3 miles x1.5, 3-10 miles x1.2.
SHORT_MULT, MID_MULT = 1.5, 1.2
MIN_MILES = 1.0  # a synthetic line between two substations at (almost) one spot is priced as 1 mile
# Table 3-3: transformer capital cost, $7,250 to $13,450 per MVA across voltage classes (2014 $).
XFMR_LOW, XFMR_HIGH = 7_250, 13_450
# GridLab / UC Berkeley 2035 report on reconductoring: advanced conductors "can double existing
# transmission line capacity at less than half the cost" of new lines — beyond 2x, a new line.
RECONDUCTOR_MAX_RATIO = 2.0

# Who pays (illustrative): recovered over 40 years at 7 % a year.
RECOVERY_YEARS, RECOVERY_RATE = 40, 0.07
CRF = RECOVERY_RATE * (1 + RECOVERY_RATE) ** RECOVERY_YEARS / ((1 + RECOVERY_RATE) ** RECOVERY_YEARS - 1)

SOURCES = {
    "lbnl_voll": {
        "name": "LBNL, Updated Value of Service Reliability Estimates for Electric Utility Customers in the United States (Sullivan, Schellenberg & Blundell, LBNL-6941E, 2015), Table ES-1",
        "url": "https://eta-publications.lbl.gov/sites/default/files/lbnl-6941e.pdf",
    },
    "eia_sales": {
        "name": "EIA, Electric Power Annual 2024, Table 2.2 (retail sales by sector)",
        "url": "https://www.eia.gov/electricity/annual/html/epa_02_02.html",
    },
    "eia_price": {
        "name": "EIA, Electric Power Annual 2024, Table 2.10 (average price by sector and state)",
        "url": "https://www.eia.gov/electricity/annual/html/epa_02_10.html",
    },
    "bv_wecc": {
        "name": "Black & Veatch for WECC, Capital Costs for Transmission and Substations (2014), Tables 2-1 to 2-4 and 3-3",
        "url": "https://efis.psc.mo.gov/mpsc/commoncomponents/viewdocument.asp?DocId=936076825",
    },
    "gridlab_2035": {
        "name": "GridLab and UC Berkeley, 2035 Report: Reconductoring with Advanced Conductors (2024)",
        "url": "https://www.2035report.com/reconductoring/",
    },
    "lbnl_dc": {
        "name": "LBNL, 2024 United States Data Center Energy Usage Report (Shehabi et al., December 2024)",
        "url": "https://escholarship.org/uc/item/32d6m0d1",
    },
    "census": {"name": POPULATION_SOURCE, "url": "https://www.census.gov/data/tables/time-series/demo/popest/2020s-state-total.html"},
    "cpi": {"name": "U.S. Bureau of Labor Statistics, CPI-U annual averages (2013, 2014, 2024)", "url": "https://www.bls.gov/cpi/"},
}


class CostIn(CaseIn):
    hours_out: float | None = None  # how long the lost load stays dark; None = estimated from the incident's size (outage_hours)


# ---------------------------------------------------------------------------------- outage time
# How long the lights stay out, by the size of the incident (people without power). Our own rule of thumb,
# on the long side by design (a big cascade takes days to rebuild, not hours), interpolated on a log scale.
# Not a forecast: real restoration depends on the cause and on crews. Shown next to the cost, with this sentence.
OUTAGE_POINTS = [(1_000, 2.0), (10_000, 4.0), (100_000, 8.0), (1_000_000, 24.0), (5_000_000, 48.0), (20_000_000, HOURS_MAX)]
OUTAGE_BASIS = (
    "Estimated from the size of the incident: about 4 hours for 10,000 people, 8 hours for 100,000, a day for a million "
    "and up to 3 days for many millions. A rule of thumb on the long side, not a forecast."
)


def outage_hours(people: float) -> float:
    """Estimated hours without power for an incident that leaves `people` in the dark (0 when nobody is)."""
    p = float(people or 0)
    if p <= 0:
        return 0.0
    xs = [math.log10(x) for x, _ in OUTAGE_POINTS]
    ys = [y for _, y in OUTAGE_POINTS]
    return round(float(np.interp(math.log10(max(p, 1.0)), xs, ys)), 1)


def outage_label(h: float, lang: str = "en") -> str:
    """'about 8 hours', 'about a day', 'about 3 days' (or the Spanish)."""
    en = lang == "en"
    if h <= 0:
        return "no outage" if en else "sin apagón"
    if h < 1.5:
        return "about an hour" if en else "aproximadamente una hora"
    if h < 22:
        n = int(round(h))
        return f"about {n} hours" if en else f"unas {n} horas"
    if h < 36:
        return "about a day" if en else "aproximadamente un día"
    d = int(round(h / 24))
    return f"about {d} days" if en else f"unos {d} días"


# ---------------------------------------------------------------------------------- helpers
def _interp(h: float, ys: list[float]) -> float:
    return float(np.interp(min(max(h, VOLL_HOURS[0]), VOLL_HOURS[-1]), VOLL_HOURS, ys))


def voll_per_mwh(hours: float) -> tuple[float, float]:
    """(low, high) value of lost load in 2024 $ per MWh unserved for an outage of `hours`."""
    home, large, small = _interp(hours, VOLL_HOME), _interp(hours, VOLL_LARGE), _interp(hours, VOLL_SMALL)
    biz = 1.0 - RES_SHARE
    low = RES_SHARE * home + biz * large
    high = RES_SHARE * home + biz * ((1 - SMALL_SHARE_HIGH) * large + SMALL_SHARE_HIGH * small)
    k = TO_2024_FROM_2013 * 1000.0  # $/kWh (2013) -> $/MWh (2024)
    return low * k, high * k


def _length_mult(miles: float) -> float:
    return SHORT_MULT if miles < 3 else MID_MULT if miles <= 10 else 1.0


def _kv_class(kv: float) -> int:
    """The Black & Veatch voltage class a line is priced at (230 kV is the guide's lowest)."""
    return 230 if kv <= 287 else 345 if kv <= 420 else 500


def _money(x: float) -> str:
    """$1.2 billion / $340 million / $48,000 — for the formula sentences."""
    x = float(x)
    for div, word in ((1e9, "billion"), (1e6, "million")):
        if x >= div:
            v = x / div
            return f"${v:,.0f} {word}" if v >= 100 else f"${v:,.1f} {word}".replace(".0 ", " ")
    if x >= 100:
        return f"${round(x, -2):,.0f}" if x >= 10_000 else f"${x:,.0f}"
    return f"${x:,.2f}"


def _rng(lo: float, hi: float) -> str:
    return _money(lo) if abs(hi - lo) < 0.005 else f"{_money(lo)}–{_money(hi)}"


def _mid(item: dict) -> float:
    return (item["low"] + item["high"]) / 2


def _check_hours(h: float) -> float:
    if not math.isfinite(h) or not (HOURS_MIN <= h <= HOURS_MAX):
        raise HTTPException(status_code=422, detail=f"Hours without power must be between {HOURS_MIN:g} and {HOURS_MAX:g}")
    return round(float(h), 2)


# ---------------------------------------------------------------------------------- the estimate
def _upgrade_items(g, rate0_orig: np.ndarray, rate_final: np.ndarray, idx: list[int], applied: set[int]) -> list[dict]:
    items = []
    for i in idx:
        old, new = float(rate0_orig[i]), float(rate_final[i])
        if new <= old + 1e-6:
            continue
        fs, ts = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
        kv = float(g.br_kv[i])
        base = {
            "id": int(g.br_ids[i]),
            "from_name": g.sub_name[fs],
            "to_name": g.sub_name[ts],
            "kv": kv,
            "old_mva": round(old, 1),
            "new_mva": round(new, 1),
            "added_mva": round(new - old, 1),
            "applied": i in applied,  # already in the case (Fix it applied)
        }
        if fs == ts:  # both ends in one substation: a transformer
            low = (new - old) * XFMR_LOW * TO_2024_FROM_2014
            high = new * XFMR_HIGH * TO_2024_FROM_2014
            method = f"add {new - old:,.0f} MVA alongside (low) or a new {new:,.0f} MVA transformer (high)"
            items.append({**base, "kind": "transformer", "miles": None, "low": low, "high": high, "method": method})
            continue
        km = _km(float(g.sub_lat[fs]), float(g.sub_lon[fs]), float(g.sub_lat[ts]), float(g.sub_lon[ts]))
        miles = max(km / 1.609344, MIN_MILES)
        cls = _kv_class(kv)
        per_mile_new = LINE_SINGLE[cls] * _length_mult(miles) * TO_2024_FROM_2014
        if new <= RECONDUCTOR_MAX_RATIO * old:
            low = per_mile_new * CONDUCTOR_SHARE[cls] * ACSS_MULT * miles
            high = per_mile_new * miles
            method = f"reconductor (low) or rebuild (high) {miles:,.1f} miles at the {cls} kV class"
        else:
            low = per_mile_new * miles
            high = LINE_DOUBLE[cls] * _length_mult(miles) * TO_2024_FROM_2014 * miles
            method = f"more than double its rating: a new line (low) or a new double-circuit line (high), {miles:,.1f} miles at the {cls} kV class"
        items.append({**base, "kind": "line", "miles": round(miles, 1), "low": low, "high": high, "method": method})
    for it in items:
        it["low"], it["high"] = round(it["low"]), round(it["high"])
    items.sort(key=lambda it: -it["high"])
    return items


def estimate(body: CostIn) -> dict:
    """The deterministic estimate for a case. Raises 422 with a readable sentence."""
    given = body.hours_out is not None
    hours = _check_hours(body.hours_out) if given else 0.0
    code = region_code(body.region)
    g, sites, trip, upgrades = check_case(body)
    if not sites and not trip and abs(g.load_factor - 1.0) < 1e-9:
        raise HTTPException(status_code=422, detail="Nothing to price yet: drop a data center, draw a storm or change the time of day")
    extra, header = _case_header(g, sites, trip, upgrades)
    casc = g.cascade_case(extra, trip, upgrades, firm_buses=case_firm_buses(g, sites, body.firm))
    if not given:  # the outage lasts as long as an incident this big takes to put right
        hours = outage_hours(casc.get("people") or 0) if float(casc["lost_mw"]) > 0.5 else 0.0

    # upgrades that keep every line under its limit: the case's own plus the Fix it greedy's
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[bid]] = False
    rate0 = g.rates_with(upgrades)
    applied = {g.br_index[b] for b in upgrades}
    firm_buses = case_firm_buses(g, sites, body.firm)
    _, final, rate, chosen, _, _, _ = greedy_fix(g, active, extra, rate0, room=MAX_UPGRADES - len(applied), existing=frozenset(applied))
    remaining = len(g.overloaded(final))
    items = _upgrade_items(g, g.rate, rate, sorted(applied | set(chosen)), applied)

    # Do the upgrades really prevent the blackout? Re-run the cascade with them: a storm can cut places off
    # by itself (no line is over, stronger lines can't reconnect them), and a search that runs out of
    # rounds leaves lines over. Only a cascade that ends with nobody dark earns the word "prevent".
    lost_after, people_after = 0.0, 0
    if float(casc["lost_mw"]) > 0.5:
        with_up = {int(g.br_ids[i]): float(rate[i]) for i in applied | set(chosen)}
        after = g.cascade_case(extra, trip, with_up, firm_buses=firm_buses) if with_up else casc
        if float(after["lost_mw"]) > 0.5:
            lost_after = float(after["lost_mw"])
            people_after = int(after.get("people") or g.people(lost_after))  # the engine's own count, as the map shows
    prevents = lost_after == 0.0
    # a partial re-rating can move the cascade somewhere worse; then "stay dark even with them" would mislead
    partial_helps = 0.0 < lost_after < float(casc["lost_mw"]) - 0.5

    region = REGIONS[code]
    name = region["name"]
    population = int(POPULATION.get(code) or 0)
    households = population / PEOPLE_PER_HOUSEHOLD if population else 0.0
    mw = float(header["mw"] or 0.0)
    lost_mw = float(casc["lost_mw"])
    lost_mw = lost_mw if lost_mw > 0.5 else 0.0  # the engine's "settled" threshold: crumbs of rounding aren't a blackout
    area = header.get("sub_area")

    # (1) the blackout
    v_low, v_high = voll_per_mwh(hours)
    mwh = lost_mw * hours
    blackout = {
        "key": "blackout",
        "label": "The blackout",
        "per": "one time",
        "low": round(mwh * v_low),
        "high": round(mwh * v_high),
        "formula": (
            f"{lost_mw:,.0f} MW of customers dark (about {int(casc.get('people') or 0):,} people, estimate) × {hours:g} h "
            f"= {mwh:,.0f} MWh unserved × {_rng(v_low, v_high)} per MWh"
            if lost_mw > 0
            else "No customer loses power in this case."
        ),
        "assumption": (
            f"The lights stay off for {hours:g} hours ({'as chosen' if given else 'estimated from the incident' + chr(39) + 's size: ' + OUTAGE_BASIS}). Value of lost load from LBNL's interruption costs by customer class "
            f"({'at the 16-hour rate, the longest LBNL estimates' if hours > 16 else 'interpolated by outage length'}), weighted "
            f"by 2024 U.S. sales: {RES_SHARE:.1%} homes, the rest businesses. Low: every business at the large-business rate. "
            f"High: one business MWh in ten at the small-business rate (our assumption). 2013 dollars raised to 2024 by CPI-U."
        ),
        "sources": [SOURCES["lbnl_voll"], SOURCES["eia_sales"], SOURCES["cpi"]],
        "mwh": round(mwh, 1),
        "voll_low": round(v_low),
        "voll_high": round(v_high),
    }

    # (2) upgrades to prevent it
    up_low = sum(it["low"] for it in items)
    up_high = sum(it["high"] for it in items)
    n_lines = sum(1 for it in items if it["kind"] == "line")
    n_xf = len(items) - n_lines
    miles = sum(it["miles"] or 0 for it in items)
    what = []
    if n_lines:
        what.append(f"{n_lines} {'line' if n_lines == 1 else 'lines'} ({miles:,.0f} miles)")
    if n_xf:
        what.append(f"{n_xf} {'transformer' if n_xf == 1 else 'transformers'}")
    formula = f"{' and '.join(what)} raised above their limits, priced by voltage class and length" if items else "No line goes over its limit, so nothing needs upgrading."
    if not prevents:
        formula += (
            f" {'Even with them' if items else 'Still'}, about {lost_after:,.0f} MW ({people_after:,} people, estimate) stay dark."
            if partial_helps or not items
            else " Even with them the cascade still ends in a blackout."
        )
    upgrades_line = {
        "key": "upgrades",
        "label": "Upgrades to prevent it" if prevents else "Upgrades against the overloads",
        "per": "one time",
        "low": up_low,
        "high": up_high,
        "formula": formula,
        "assumption": (
            "The Fix it search: raise the most overloaded line one 50 MVA step at a time until none is over. "
            "A line: its straight-line length between substations at Black & Veatch's cost per mile for its voltage class "
            "(lines under 230 kV at the 230 kV rate, the guide's lowest, so likely high); low = reconductoring, high = a new line; "
            "more than double the rating: a new line to a new double-circuit line. A transformer: $7,250–$13,450 per MVA. "
            "2014 dollars raised to 2024 by CPI-U; land, permits and substation work beyond transformers are not included."
        ),
        "sources": [SOURCES["bv_wecc"], SOURCES["gridlab_2035"], SOURCES["cpi"]],
        "items": items[:40],
        "count": len(items),
        "added_mva": round(sum(it["added_mva"] for it in items), 1),
        "calm": remaining == 0,
        "remaining": remaining,
        "prevents": prevents,  # the cascade re-run with these upgrades ends with nobody dark
        "lost_after_mw": round(lost_after, 1),  # what the cascade still takes with these upgrades (0 = prevented)
        "people_after": people_after,  # estimate
    }

    # (3) the campus's power bill
    price = INDUSTRIAL_PRICE_2024.get(code, US_INDUSTRIAL_PRICE_2024)
    per_mwh = price * 10.0  # cents/kWh -> $/MWh
    bill = {
        "key": "power_bill",
        "label": "The campus's power bill",
        "per": "year",
        "low": round(mw * HOURS_PER_YEAR * LOAD_FACTOR_LOW * per_mwh),
        "high": round(mw * HOURS_PER_YEAR * LOAD_FACTOR_HIGH * per_mwh),
        "formula": (
            f"{mw:,.0f} MW × 8,760 h × {LOAD_FACTOR_LOW:.0%}–{LOAD_FACTOR_HIGH:.0%} of the time × {price:.2f}¢ per kWh"
            if mw > 0
            else "No data center in this case."
        ),
        "assumption": (
            f"The campus draws {LOAD_FACTOR_LOW:.0%}–{LOAD_FACTOR_HIGH:.0%} of its size on average (LBNL: 50% average utilization; "
            f"AI training servers run 80% of the time) at {name}'s 2024 average industrial price, {price:.2f}¢ per kWh. "
            "Real large-load contracts can be lower or higher."
        ),
        "sources": [SOURCES["lbnl_dc"], SOURCES["eia_price"]],
        "price_cents_kwh": price,
        "mw": mw,
    }

    # (4) who pays
    per_hh = CRF / 12.0 / households if households else 0.0
    who = {
        "key": "who_pays",
        "label": "Who pays, per household (illustrative)",
        "per": "month",
        "low": round(up_low * RES_SHARE * per_hh, 4),  # cents matter here: keep 4 decimals
        "high": round(up_high * per_hh, 4),
        "formula": (
            f"{_rng(up_low, up_high)} of upgrades × {CRF:.1%} a year ÷ {households:,.0f} households ÷ 12"
            if up_high > 0 and households
            else "No upgrades, so nothing to pass on to households."
        ),
        "assumption": (
            f"Illustrative. The upgrades are paid back over {RECOVERY_YEARS} years at {RECOVERY_RATE:.0%} a year and spread over "
            f"{name}'s households ({population:,} people ÷ {PEOPLE_PER_HOUSEHOLD:g} per household, estimates). Low: households pay "
            f"their share of electricity sales ({RES_SHARE:.1%}); high: households pay it all. Who really pays is up to regulators."
        ),
        "sources": [SOURCES["census"], SOURCES["eia_sales"]],
        "households": round(households),
    }

    lines = [blackout, upgrades_line, bill, who]
    # the blackout and the upgrades are alternatives; the total is the path with no planning: it happens, then gets fixed
    total = {"label": "One blackout, then the fix", "low": blackout["low"] + up_low, "high": blackout["high"] + up_high}

    insights = []
    if lost_mw <= 0.5:
        insights.append("No one loses power in this case, so the blackout costs nothing.")
    elif not prevents:
        # the upgrades don't end the blackout: never price them as "preventing it"
        cause = (
            "the storm itself cuts places off, and stronger lines can't reconnect them"
            if trip and remaining == 0
            else "re-rating lines alone isn't enough here; it would take new lines or power plants"
        )
        if items and partial_helps:
            insights.append(f"Upgrading lines can't prevent all of it: about {people_after:,} people (estimate) stay dark even with them, because {cause}.")
        elif items or remaining:
            insights.append(f"Upgrading lines can't prevent this blackout: {cause}.")
        else:
            insights.append(f"No line goes over its limit, yet about {people_after:,} people (estimate) are dark: {cause}.")
    elif items:
        pct = _mid(upgrades_line) / max(_mid(blackout), 1.0) * 100
        if pct < 95:
            insights.append(f"Preventing it costs about {'under 1' if pct < 1 else f'{pct:.0f}'}% of what one {hours:g}-hour blackout costs.")
        else:
            insights.append(f"Preventing it costs about {pct / 100:.1f} times what one {hours:g}-hour blackout costs.")
    if items and mw > 0:
        pct = _mid(upgrades_line) / max(_mid(bill), 1.0) * 100
        insights.append(f"The upgrades equal about {'under 1' if pct < 1 else f'{pct:.0f}'}% of one year of the campus's power bill.")
    if not items and (lost_mw <= 0.5 or prevents):
        insights.append("No line goes over its limit, so no upgrades are needed.")

    notes = []
    if casc.get("site_cut_off"):
        notes.append(
            "The campus's own connection tripped and cut it off too. That is why a bigger campus at one site often "
            "blacks out about the same number of people: past the site's room, the cascade ends the same way."
        )
    if remaining:
        notes.append(
            f"{remaining:,} {'line stays' if remaining == 1 else 'lines stay'} over the limit after the search (it stops at "
            "60 rounds, and a re-rating tops out at 5x), so the upgrade cost is a floor."
        )
    notes.append("Not counted: the campus's own downtime, repairing tripped lines, and new power plants.")

    # the one-glance answer: how long, and how much (the high end of the range: we guess on the higher side)
    if lost_mw > 0.5:
        head = {"kind": "blackout", "label": "Cost of the blackout", "cost_high": blackout["high"], "cost_low": blackout["low"]}
    elif up_high > 0:
        head = {"kind": "upgrades", "label": "Upgrades to stop the overloads", "cost_high": up_high, "cost_low": up_low}
    else:
        head = {"kind": "none", "label": "No blackout, no upgrades needed", "cost_high": 0, "cost_low": 0}
    head.update(
        {
            "outage_hours": hours,
            "outage_label": outage_label(hours),
            "outage_label_es": outage_label(hours, "es"),
            "outage_estimated": not given,
            "outage_basis": OUTAGE_BASIS if not given else f"Outage length chosen: {hours:g} hours.",
            "people": int(casc.get("people") or 0),
        }
    )

    return {
        "region": code,
        "region_name": name,
        "synthetic": True,
        "headline": head,
        "hours_out": hours,
        "mw": mw,
        "sub_area": area,
        "sub_name": header.get("sub_name"),
        "sites": len(sites),
        "load_factor": g.load_factor,
        "firm": bool(body.firm),
        "trip_count": len(trip),
        "outcome": casc["outcome"],
        "total_steps": casc["total_steps"],
        "lost_mw": round(lost_mw, 1),
        "people": casc.get("people", 0),
        "population": population,
        "households": round(households),
        "site_cut_off": bool(casc.get("site_cut_off")),
        "lines": lines,
        "total_one_time": total,
        "insights": insights,
        "notes": notes,
        "estimate": True,
    }


_cache: "OrderedDict[str, dict]" = OrderedDict()
_cache_lock = threading.Lock()
CACHE_SIZE = 64


def cached_estimate(body: CostIn) -> dict:
    """estimate() with a small LRU, so /api/cost then /api/cost/ai for one case solves it once."""
    key = json.dumps(body.model_dump(), sort_keys=True, default=str)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    result = estimate(body)
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return result


# ---------------------------------------------------------------------------------- the AI estimate
AI_KEYS = ("blackout", "upgrades", "power_bill", "who_pays")
AI_UNITS = {
    "blackout": "US dollars, one time",
    "upgrades": "US dollars, one time",
    "power_bill": "US dollars per year",
    "who_pays": "US dollars per household per month",
}
AI_SYSTEM = (
    "You are a careful power-system cost analyst. The case runs on a synthetic grid model (Breakthrough Energy / "
    "Texas A&M test system), not any real utility's network, and describes no real event. Give independent, "
    "plain-language cost ranges. Never name real companies, utilities or data-center projects. Reply with JSON only."
)
FORMULA_REASON = "Gemini wasn't used, so this repeats the formula."


def _formula_ai(det: dict) -> dict:
    """The AI column when Gemini isn't used: the formula's own numbers, flagged."""
    by = {ln["key"]: ln for ln in det["lines"]}
    return {k: {"low": by[k]["low"], "high": by[k]["high"], "reasoning": FORMULA_REASON, "fallback": True} for k in AI_KEYS}


def _prompt(det: dict) -> str:
    by = {ln["key"]: ln for ln in det["lines"]}
    up = by["upgrades"]
    n_lines = sum(1 for it in up["items"] if it["kind"] == "line")
    miles = sum(it["miles"] or 0 for it in up["items"])
    xf_mva = sum(it["added_mva"] for it in up["items"] if it["kind"] == "transformer")
    kvs = sorted({int(it["kv"]) for it in up["items"]})
    campus = (
        f"{det['sites']} data-center campus{'es' if det['sites'] != 1 else ''} totaling {det['mw']:,.0f} MW"
        + (f", the main one near {det['sub_area']}" if det.get("sub_area") else "")
        + (" on firm service (the operator cuts other customers to keep it on)" if det["firm"] else " on flexible service")
        if det["mw"] > 0
        else "no data center"
    )
    facts = [
        f"State: {det['region_name']} (synthetic grid model). Load level: {det['load_factor']:g} x the model's summer-afternoon snapshot.",
        f"Case: {campus}; {det['trip_count']} lines knocked out first by a storm." if det["trip_count"] else f"Case: {campus}.",
        f"Cascade result: {det['outcome']} after {det['total_steps']} steps; {det['lost_mw']:,.0f} MW of existing customer load lost; "
        f"about {det['people']:,} people without power (estimate)" + ("; the campus itself was cut off too." if det["site_cut_off"] else "."),
        f"Assumed outage length: {det['hours_out']:g} hours, so {by['blackout']['mwh']:,.0f} MWh unserved.",
        (
            f"Upgrades that keep every line under its limit: {n_lines} line(s) ({miles:,.0f} miles total, voltages {', '.join(map(str, kvs))} kV) "
            f"and {up['count'] - n_lines} transformer(s) ({xf_mva:,.0f} MVA added); total {up['added_mva']:,.0f} MVA added"
            + ("." if up["calm"] else f"; {up['remaining']} lines would still be over and need new lines.")
            if up["count"]
            else "Upgrades: none needed, no line goes over its limit."
        )
        + (
            ""
            if up["prevents"]
            else f" Even with these upgrades the cascade still leaves {up['lost_after_mw']:,.0f} MW dark, so they don't prevent the whole blackout."
        ),
        f"State facts: 2024 average industrial electricity price {by['power_bill']['price_cents_kwh']:.2f} cents/kWh (EIA); "
        f"about {det['households']:,} households (estimate).",
    ]
    ours = "\n".join(f"- {k}: {_rng(by[k]['low'], by[k]['high'])} ({AI_UNITS[k]}). Basis: {by[k]['assumption']}" for k in AI_KEYS)
    shape = ", ".join(f'"{k}": {{"low": number, "high": number, "reasoning": "one sentence"}}' for k in AI_KEYS)
    return (
        "Case facts:\n" + "\n".join(f"- {f}" for f in facts) + "\n\n"
        "Our formula's estimates:\n" + ours + "\n\n"
        "Give your own independent low and high for each item in plain numbers (US dollars, not thousands or millions): "
        + "; ".join(f"{k} in {u}" for k, u in AI_UNITS.items())
        + ". Say in one short sentence (at most 30 words, no jargon) why your range differs from ours or agrees. "
        "If an item is zero in this case, answer 0 for it.\n"
        "Answer as JSON: {" + shape + "}"
    )


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            v = float(str(v).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            return None
    v = float(v)
    return v if math.isfinite(v) and v >= 0 else None


def _clean_ai(raw, det: dict) -> tuple[dict, bool]:
    """Validate Gemini's answer per item; an unusable item falls back to the formula. Returns (items,
    all_fell_back)."""
    by = {ln["key"]: ln for ln in det["lines"]}
    out = {}
    raw = raw if isinstance(raw, dict) else {}
    for k in AI_KEYS:
        d_low, d_high = by[k]["low"], by[k]["high"]
        item = raw.get(k) if isinstance(raw.get(k), dict) else {}
        lo, hi = _num(item.get("low")), _num(item.get("high"))
        why = item.get("reasoning")
        why = " ".join(str(why).split())[:280] if isinstance(why, str) and why.strip() else None
        if d_high <= 0:  # nothing to price: zero, whatever the model said
            out[k] = {"low": 0, "high": 0, "reasoning": "Nothing to price in this case.", "fallback": False}
            continue
        ok = lo is not None and hi is not None and why is not None
        if ok and lo > hi:
            lo, hi = hi, lo
        # a unit slip (a factor of 10 or 1,000) lands far outside the formula's range: don't show it (the hero's upgrades once came back exactly 10x)
        if ok and (hi > d_high * 8 or hi < max(d_low, 1e-9) / 8):
            ok = False
        if not ok:
            out[k] = {"low": d_low, "high": d_high, "reasoning": "Gemini's answer for this line was unusable, so this repeats the formula.", "fallback": True}
            continue
        digits = 4 if k == "who_pays" else 0
        out[k] = {"low": round(lo, digits), "high": round(hi, digits), "reasoning": why, "fallback": False}
    return out, all(v["fallback"] for v in out.values())


# ---------------------------------------------------------------------------------- routes
class QuickIn(BaseModel):
    region: str | None = None
    lost_mw: float = Field(ge=0, le=1_000_000)
    people: int = Field(ge=0, le=400_000_000)
    hours_out: float | None = None


@router.post("/api/cost/quick")
@limiter.limit("60/minute")
def cost_quick(request: Request, body: QuickIn):
    """The headline (time without power, expected cost at the high end) for a cascade the caller already ran: the lost
    load and people come from it, nothing is re-run. What the map uses outside Florida, where nothing is computed
    ahead of the cascade. Same formulas and sources as /api/cost."""
    region_code(body.region)  # a readable 422 for an unknown state
    given = body.hours_out is not None
    hours = _check_hours(body.hours_out) if given else (outage_hours(body.people) if body.lost_mw > 0.5 else 0.0)
    lost = body.lost_mw if body.lost_mw > 0.5 else 0.0
    v_low, v_high = voll_per_mwh(hours)
    mwh = lost * hours
    low, high = round(mwh * v_low), round(mwh * v_high)
    head = {
        "kind": "blackout" if lost else "none",
        "label": "Cost of the blackout" if lost else "No blackout",
        "cost_high": high,
        "cost_low": low,
        "outage_hours": hours,
        "outage_label": outage_label(hours),
        "outage_label_es": outage_label(hours, "es"),
        "outage_estimated": not given,
        "outage_basis": OUTAGE_BASIS if not given else f"Outage length chosen: {hours:g} hours.",
        "people": int(body.people),
    }
    assumption = (
        f"{lost:,.0f} MW of customers dark for {hours:g} hours, at LBNL's value of lost load by customer class weighted by 2024 U.S. sales "
        f"({RES_SHARE:.1%} homes, the rest businesses); low: every business at the large-business rate, high: one business MWh in ten at the "
        "small-business rate. 2013 dollars raised to 2024 by CPI-U."
    )
    return {"synthetic": True, "estimate": True, "hours_out": hours, "headline": head, "lines": [{"key": "blackout", "low": low, "high": high, "assumption": assumption}]}


@router.post("/api/cost")
@limiter.limit("120/minute")  # cheap and cached; the size slider re-prices on every settle
async def cost(request: Request, body: CostIn):
    return await run_in_threadpool(cached_estimate, body)


_ai_cache: "OrderedDict[str, dict]" = OrderedDict()  # case -> Gemini's usable answer (fallbacks aren't kept)


@router.post("/api/cost/ai")
@limiter.limit("30/minute")
async def cost_ai(request: Request, body: CostIn):
    det = await run_in_threadpool(cached_estimate, body)
    key = json.dumps(body.model_dump(), sort_keys=True, default=str)
    with _cache_lock:
        hit = _ai_cache.get(key)
    if hit is not None:  # the same case again (a replay, a reload): don't spend the day's AI quota twice
        return {**det, "ai": hit, "fallback": False}
    raw, offline = await complete_json(_prompt(det), system=AI_SYSTEM, fallback={"_formula": True}, timeout=10, surface="cost")
    items, all_fell_back = (None, True) if offline else _clean_ai(raw, det)
    if all_fell_back:
        if not offline:
            log.warning("cost AI: every line unusable, showing the formula")
        return {**det, "ai": _formula_ai(det), "fallback": True}
    with _cache_lock:
        _ai_cache[key] = items
        while len(_ai_cache) > CACHE_SIZE:
            _ai_cache.popitem(last=False)
    return {**det, "ai": items, "fallback": False}
