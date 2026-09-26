"""Who goes dark first? (CLAUDE.md -> Decisions; C:/dev/notes/BIG-IDEAS-SAT-1655.md #1). When the grid runs short,
someone loses power, and one sentence in the campus's service contract decides who. The SAME case (campus or
campuses, load level, storm, upgrades) under three rules, each computed by the engine:

  flexible   "Nobody planned": the plain cascade. Lines trip until the grid settles; the campus's own
             connection may trip too, cutting it off with its neighbors.
  firm       "Keep the campus on": the grid operator protects the campus and cuts other customers instead
             (powerflow.Grid.cascade_case(firm_buses=...), the same firm mode the cascade route runs).
  step_down  "The campus steps down first": every campus drops to the room this site has before the first line
             goes over its limit at this load level (one campus: the site headroom the verdict shows,
             grid.site_headroom; several, or a storm or upgrades in the case: the largest common share of each
             campus's size with no line over, found by bisection on what-if solves), rounded down to whole MW.
             Verified: a what-if at that level has no line over, and the cascade at that level trips nothing.
             When the grid is past its limits even with every campus off (a storm, a heat wave), stepping down can't
             clear it: the campus switches off (0 MW), labeled so.

POST /api/grid/service-rules   the cascade route's case body (grid.CaseIn; `firm` is ignored: all three are run)
  -> {rules: [flexible, firm, step_down], step_down: {level_mw, share_pct, sites, verified}, lens, sources}
  Each rule: people_dark (everyone in the areas that lose power: the engine's people_zone), people_cut (of them,
  the areas the operator cut on purpose to keep the campus on, firm only: a storm's own victims are not), people_hit (the
  counter's count: everyone hit along the way, each once), people_out (still without power when it settles),
  the campus MW served at this load level, lines tripped, customer load shed, and the outage length and cost
  priced with costs.py's own rules (outage_hours from the incident's size, value of lost load, the high end as
  the app shows it), plus the end state the map draws (tripped and held lines, the load lost per substation).

Estimates on a SYNTHETIC grid model (Breakthrough Energy / Texas A&M), never a real utility's network, contract or
event. The model computes steady-state power flows only: how many hours a year a campus would have to step down is
not something it can say (Duke's national estimate is quoted as theirs, with its source). Public like the grid
endpoints (no login, nothing stored); cached per case.
"""

import json
import math
import threading
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from costs import outage_hours, outage_label, voll_per_mwh
from grid import CaseIn, _case_header, check_case, region_code, site_headroom
from limiter import limiter

router = APIRouter(tags=["service-rules"])

CACHE_SIZE = 64
BISECT_STEPS = 24  # the common share to ~1e-7 of each campus's size
VERIFY_TRIES = 6  # a level that still trips something (rounding at the limit) is lowered 1 MW at a time

# Read before quoting (Sat 17:00): each line is what the fetched page says, with who said it and when.
SOURCES = [
    {
        "key": "texas_sb6",
        "short": "Texas SB 6 (2025)",
        "text": (
            "Texas's SB 6, signed June 20, 2025, lets utilities disconnect eligible large loads (75 MW or more, new "
            "connections) during firm load shed events, and requires shutoff equipment as a condition of connecting."
        ),
        "name": "Utility Dive (Brian Martucci), June 25, 2025: Texas law gives grid operator power to disconnect data centers during crisis",
        "url": "https://www.utilitydive.com/news/texas-law-gives-grid-operator-power-to-disconnect-data-centers-during-crisi/751587/",
    },
    {
        "key": "ferc_2026",
        "short": "FERC, June 2026",
        "text": (
            "RMI's summary of FERC's June 2026 large-load orders describes non-firm service for new large loads that can be "
            "curtailed when transmission capacity is constrained, and interim non-firm service while they wait for network upgrades."
        ),
        "name": "RMI, Understanding FERC's large load orders (July 6, 2026)",
        "url": "https://rmi.org/resources/understanding-fercs-large-load-orders/",
    },
    {
        "key": "duke_flex",
        "short": "Duke University, 2025",
        "text": (
            "Duke University's Nicholas Institute estimates the largest 22 U.S. balancing areas could take 76 GW of new "
            "load if it can be curtailed for 0.25 % of its maximum uptime. Curtailment would come in about 85 hours a year, "
            "mostly partial: on average, half the new load keeps running for 88 % of that time. A national estimate, "
            "theirs, not this model's."
        ),
        "name": "Norris et al., Rethinking Load Growth (Duke University Nicholas Institute, 2025), as reported by Utility Dive (Ethan Howland), Feb. 11, 2025",
        "url": "https://www.utilitydive.com/news/us-grid-headroom-flexible-load-data-center-ai-ev-duke-report/739767/",
    },
    {
        "key": "pjm_july_2026",
        "short": "Northern Virginia, July 22, 2026",
        "text": (
            "Why the question matters now: on July 22, 2026 a 230 kV line in Northern Virginia came out of service and "
            "nearly 3,800 MW of data center demand transferred to onsite generation (PJM), in waves as the system voltage "
            "changed. This model computes steady-state power flows, not voltage: it is context, not something it replays."
        ),
        "name": "Data Center Knowledge (Shane Snider), Aug. 12, 2026: 3.8 GW load drop prompts potential PJM rules",
        "url": "https://www.datacenterknowledge.com/regulations/3-8-gw-load-drop-prompts-potential-pjm-rules",
    },
]

LABELS = {
    "flexible": "Nobody planned",
    "firm": "Keep the campus on",
    "step_down": "The campus steps down first",
}
# the grid is past its limits even with every campus off (a storm, a heat wave): stepping down means switching off
OFF_LABEL = "The campus switches off"

_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


def _money_hi_lo(lost_mw: float, people_out: int) -> tuple[float, int, int]:
    """(hours out, cost low, cost high) the way costs.py prices a cascade: the lights stay out as long as an
    incident this size takes to put right (costs.outage_hours on the people still dark), x the value of lost load."""
    if lost_mw <= 0:
        return 0.0, 0, 0
    hours = outage_hours(people_out)
    v_low, v_high = voll_per_mwh(hours)
    return hours, round(lost_mw * hours * v_low), round(lost_mw * hours * v_high)


def _rule(g, key: str, casc: dict, campus_mw: float, full_mw: float) -> dict:
    """One rule's outcome from its cascade: `campus_mw` is what the campuses ask for under this rule (the full size,
    or the stepped-down level), `full_mw` their full size."""
    steps = casc["steps"]
    # the areas the operator cut on purpose to keep the campus on (firm): the substations that first lost load in a
    # "shed" step. Areas a storm or a tripped line had already darkened are not counted again: those people were
    # not cut for the campus. So people_cut <= people_dark, and equal only when everyone dark was cut on purpose.
    cut_subs = {int(sid) for s in steps if s["action"] == "shed" for sid, _mw in s.get("newly_affected") or []}
    lost = float(casc["lost_mw"])
    lost = lost if lost > 0.5 else 0.0  # the engine's "settled" threshold: crumbs of rounding aren't a blackout
    people_out = int(casc.get("people") or 0) if lost else 0
    hours, low, high = _money_hi_lo(lost, people_out)
    tripped = [int(b) for s in steps if s["action"] == "trip" for b in s["tripped"]]
    storm = [int(b) for s in steps if s["action"] == "storm" for b in s["tripped"]]
    held = sorted({int(s["held_line"]) for s in steps if s["action"] == "shed" and s.get("held_line") is not None})
    served = max(0.0, campus_mw - float(casc["site_dark_mw"]))
    dark = sorted({int(d) for s in steps for d in s["dark_subs"]})
    return {
        "key": key,
        "label": LABELS[key],
        "people_dark": int(casc.get("people_zone") or 0) if lost else 0,  # everyone in the areas that lose power
        "people_cut": int(g.zone(cut_subs)["people_zone"]) if (lost and cut_subs) else 0,  # of them, cut to keep the campus on
        "people_hit": int(casc.get("people_hit") or 0),  # the counter's count (everyone hit along the way, each once)
        "people_out": people_out,  # still without power when it settles (lost MW x people per MW)
        "homes_dark": int(casc.get("homes_zone") or 0) if lost else 0,
        "lost_mw": round(lost, 1),  # existing customers' load lost (shed load included)
        "shed_mw": float(casc.get("shed_mw") or 0.0),  # cut on purpose to keep the campus on (firm)
        "campus_mw": round(campus_mw, 1),
        "campus_served_mw": round(served, 1),
        "campus_share_pct": round(100.0 * served / full_mw, 1) if full_mw > 0 else None,
        "campus_cut_off": bool(casc.get("site_cut_off")),
        "firm_held": casc.get("firm_held"),
        "lines_tripped": len(tripped),
        "steps": int(casc.get("total_steps") or 0),
        "outcome": casc["outcome"],
        "capped": bool(casc.get("capped")),
        "outage_hours": hours,
        "outage_label": outage_label(hours),
        "cost_low": low,
        "cost_high": high,
        # the end state the map draws for this rule
        "end": {
            "tripped": tripped,
            "storm": storm,
            "held": held,
            "dark_subs": dark,
            "affected": {str(k): round(float(v), 1) for k, v in (casc.get("affected") or {}).items()},
        },
    }


def _step_down_levels(g, buses: list[int], sizes: list[float], active: np.ndarray, rate: np.ndarray, single: bool) -> tuple[list[float], str]:
    """The MW each campus can keep with no line over its limit on this network and load level (whole MW, each at
    most its full size), and how it was found."""

    def over(share: float) -> int:
        st = g.solve(active, g.extra_load([(b, share * m) for b, m in zip(buses, sizes)]), rate)
        return len(g.overloaded(st))

    if over(0.0):  # past its limits with every campus off (a heat wave, a storm): stepping down can't clear it
        return [0.0 for _ in sizes], "over without the campus"
    if single:
        room = site_headroom(g, buses[0])  # the verdict's number: MW before the first overload at this site
        return [float(min(sizes[0], math.floor(max(room, 0.0) + 1e-9)))], "site headroom"
    if not over(1.0):
        return [float(m) for m in sizes], "fits at full size"
    lo, hi = 0.0, 1.0
    for _ in range(BISECT_STEPS):
        mid = (lo + hi) / 2
        if over(mid):
            hi = mid
        else:
            lo = mid
    return [float(math.floor(lo * m + 1e-9)) for m in sizes], "common share of each campus (bisection)"


def service_rules(body: CaseIn) -> dict:
    """The three rules for a case. Raises 422 with a readable sentence."""
    code = region_code(body.region)
    g, sites, trip, upgrades = check_case(body)
    if not sites:
        raise HTTPException(status_code=422, detail="Drop a data center first: the rules decide who is cut when a campus is on the grid")
    extra, header = _case_header(g, sites, trip, upgrades)
    buses = [g.site_bus(s.lat, s.lon) for s in sites]
    sizes = [float(s.mw) for s in sites]
    full = float(sum(sizes))

    flex = g.cascade_case(extra, trip, upgrades)
    firm = g.cascade_case(extra, trip, upgrades, firm_buses=buses)

    # the step-down level, then its proof: a what-if with no line over and a cascade that trips nothing
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[bid]] = False
    rate = g.rates_with(upgrades)
    single = len(sites) == 1 and not trip and not upgrades
    levels, how = _step_down_levels(g, buses, sizes, active, rate, single)
    for _ in range(VERIFY_TRIES):
        low_extra = g.extra_load([(b, lv) for b, lv in zip(buses, levels)])
        st = g.solve(active, low_extra, rate)
        over = g.overloaded(st)
        casc = g.cascade_case(low_extra, trip, upgrades)
        trips = sum(len(s["tripped"]) for s in casc["steps"] if s["action"] == "trip")
        if (not over and not trips) or not any(lv > 0 for lv in levels):
            break
        levels = [max(0.0, lv - 1.0) for lv in levels]  # right at the limit: one MW lower
    level = float(sum(levels))
    live = st.active
    peak_i = int(np.argmax(np.where(live, st.loading_pct, -1.0))) if g.m else 0
    step = _rule(g, "step_down", casc, level, full)
    off = how == "over without the campus"
    if off:
        step["label"] = OFF_LABEL

    rules = [_rule(g, "flexible", flex, full, full), _rule(g, "firm", firm, full, full), step]
    lens = max(0, rules[1]["people_dark"] - step["people_dark"])
    return {
        "synthetic": True,
        "estimate": True,
        "region": code,
        "load_factor": g.load_factor,
        "storm": bool(trip),  # lines knocked out before the cascade (a hurricane's track): "after the storm"
        "mw": full,
        "sites": [
            {"sub": s["sub"], "sub_area": s["sub_area"], "mw": s["mw"], "room_mw": s["headroom_mw"], "step_down_mw": lv}
            for s, lv in zip(header["sites"], levels)
        ],
        "sub_lat": header.get("sub_lat"),
        "sub_lon": header.get("sub_lon"),
        "rules": rules,
        "step_down": {
            "level_mw": round(level, 1),
            "share_pct": round(100.0 * level / full, 1) if full > 0 else None,
            "how": how,
            "over_without": off,  # the grid is past its limits even with every campus off: the campus switches off
            "needed": level < full - 0.5,  # False: the campus fits at full size, nothing to step down
            "verified": {"overloaded": len(over), "lines_tripped": step["lines_tripped"], "people_dark": step["people_dark"]},
            "limit_line": int(g.br_ids[peak_i]) if len(over) == 0 and level > 0 else None,  # the line that sets the room
            "limit_pct": round(float(st.loading_pct[peak_i]), 2) if g.m else None,
        },
        # "One sentence in the contract was worth N people": keep the campus on vs the campus steps down first
        "lens": {"people": lens, "from": "firm", "to": "step_down"},
        "sources": SOURCES,
    }


def cached_rules(body: CaseIn) -> dict:
    key = json.dumps(body.model_dump(exclude={"firm"}), sort_keys=True, default=str)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    out = service_rules(body)
    with _lock:
        _cache[key] = out
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return out


@router.post("/api/grid/service-rules")
@limiter.limit("60/minute")  # three cascades and a few solves, cached per case
async def rules(request: Request, body: CaseIn):
    return await run_in_threadpool(cached_rules, body)
