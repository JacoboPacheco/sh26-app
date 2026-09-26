"""Time to power: roughly when each campus on the Strengthen page's capacity meter could connect.

A campus of the meter (backend/capacity.py: campuses connected together, each where it fits with every one before it)
depends on every upgrade made for it and for the campuses before it, and, past the power plants' reserve line, on new
generation. Its time to power is the slowest thing it waits for: the ranges below are TYPICAL durations from published
sources (each range carries its source ids; every one varies by utility, region and project), never a date for a real
project. The grid is the SYNTHETIC model's; the ranges are about the kind of work, not about any real line or utility.

  connect ....... every campus: the large-load study and the campus's own build (no network upgrades to wait for)
  line .......... a line raised to at most twice its original rating: reconductored or rebuilt on its own corridor
  transformer ... a substation transformer added or replaced: its manufacturing lead time sets the pace
  line_doubled .. a line raised past twice, up to four times, its original rating (costs.py prices it as a new line
                  beside it): a 100-300 % raise on its existing corridor, the 2035 Report's voltage-uprate row
                  (3-5 years), NOT greenfield transmission (a 1-mile synthetic line doubled is no new corridor)
  generation .... past the plants' reserve line (capacity.plants): new power plants
  new_line ...... a line raised past four times its original rating: more than any upgrade on an existing corridor
                  gives in the 2035 Report's comparison (100-300 % at most), so new transmission (greenfield, 5-15 years)

A campus's range is the elementwise maximum over what it waits for (the work runs in parallel, so it can finish no
sooner than the slowest part); `item` names the one that sets it, ties broken by a fixed RANK (a line doubled ties
new plants at 3-5+ years: the line is named, since the meter already marks the plants' line; the order never depends on
set iteration, so the same study always names the same item). A flexible campus (full power except at the peak)
needs fewer upgrades, so its own plan's range can be sooner: the always-on plan carries the flexible plan's range for
the same count of campuses, and `flex_sooner` when it is faster.

GET /api/unlock/time-to-power?region=FL&mw=1000&load_factor=1.0 reads the CACHED study only (LAZY): 404 when the
study for that state, size and load level hasn't been run (it never starts one), 409 when it has no campuses-at-once
plan. Nothing here changes a number of the study (unlock.py, capacity.py and costs.py are only read)."""

import math
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request

import costs
import unlock
from grid import DEFAULT_REGION
from limiter import limiter

router = APIRouter(tags=["unlock"])

# Every source was read for the words quoted in `basis` (Sat 2026-09-26); a range whose source couldn't be fetched
# is left out rather than guessed. Same {name, url} shape as costs.SOURCES, plus `short` for a link's text.
SOURCES = {
    "gridlab_large_loads": {
        "short": "GridLab, 2025",
        "name": "GridLab, Practical Guidance and Considerations for Large Load Interconnections (working draft, March 2025), p. 8",
        "url": "https://gridlab.org/wp-content/uploads/2025/03/GridLab-Report-Large-Loads-Interim-Report.pdf",
    },
    "rmi_large_load": {
        "short": "RMI, 2026",
        "name": "RMI, Understanding Large Load Interconnection (Farrell, Wayner, Wang, Lozano and Teplin, April 2026)",
        "url": "https://rmi.org/resources/understanding-large-load-interconnection/",
    },
    "reconductoring_2035": {
        "short": "UC Berkeley and GridLab, 2024",
        "name": "Chojkiewicz et al. (UC Berkeley and GridLab), Reconductoring with Advanced Conductors Can Accelerate the Rapid Transmission Expansion Required for a Clean Grid (2035 Report 4.0, 2024), Figure 5",
        "url": "https://www.2035report.com/wp-content/uploads/2024/04/GridLab_2035-Reconductoring-Technical-Report.pdf",
    },
    "woodmac_transformers": {
        "short": "Wood Mackenzie, 2024",
        "name": "Wood Mackenzie, Supply shortages and an inflexible market give rise to high power transformer lead times (April 2024)",
        "url": "https://www.woodmac.com/news/opinion/supply-shortages-and-an-inflexible-market-give-rise-to-high-power-transformer-lead-times/",
    },
    "duke_report": {
        "short": "Norris et al. (Duke), 2025",
        "name": "Norris et al., Rethinking Load Growth (Duke University Nicholas Institute, 2025), citing the National Infrastructure Advisory Council (2024); public copy filed in Illinois Commerce Commission dockets 25-0677/25-0679, p. 7 of 43",
        "url": "https://www.icc.illinois.gov/docket/P2025-0679/documents/371138/files/650737.pdf",
    },
    "duke_flex": {
        "short": "Duke University, 2025",
        "name": "Duke University, Rethinking Load Growth (Norris et al., February 2025): load flexibility could let the power system absorb new demand more quickly",
        "url": "https://climate.duke.edu/annual-report/items/rethinking-load-growth/",
    },
    "belfer_dc": {
        "short": "Belfer Center, 2026",
        "name": "Belfer Center (Harvard Kennedy School), Data Centers and Large-Scale Electric Growth: The Virginia and Texas Experiences (Mural, Pherwani and Gupta, April 2026)",
        "url": "https://www.belfercenter.org/research-analysis/data-centers-texas-virginia-comparison",
    },
    "lbnl_queued_up": {
        "short": "Berkeley Lab via APPA, 2026",
        "name": "American Public Power Association on Berkeley Lab's Queued Up report (2026 edition): time from interconnection request to commercial operation",
        "url": "https://www.publicpower.org/periodical/article/backlog-power-plants-seeking-transmission-grid-connection-eased-somewhat-2025-lbnl",
    },
}

# The kinds of work a campus can wait for, as typical ranges in years (lo, hi); `plus`: the sources say it often runs
# longer. `short` is the meter's tick; `label` names the work in a sentence; `basis` is what the sources say.
ITEMS = {
    "connect": {
        "lo": 1.5,
        "hi": 2.0,
        "plus": False,
        "short": "no upgrades",
        "label": "the large-load study and the campus’s own build, its substation included",
        "basis": "No network upgrades to wait for: a data center takes 1.5 to 2 years to build (GridLab), while the utility’s large-load study runs. A load of 150 MW or more is served by its own dedicated substation at 230 kV or higher (GridLab), part of the campus’s build. The whole process from request to power ranges from several months to several years (RMI).",
        "sources": ["gridlab_large_loads", "rmi_large_load"],
    },
    "line": {
        "lo": 1.5,
        "hi": 4.0,
        "plus": False,
        "short": "line upgrade",
        "label": "a line reconductored or rebuilt on its own corridor",
        "basis": "Reconductoring with advanced conductors takes 18 to 36 months; rebuilding with double-circuit towers, 2 to 4 years (the 2035 Report, from industry interviews, the average case).",
        "sources": ["reconductoring_2035"],
    },
    "transformer": {
        "lo": 2.0,
        "hi": 4.0,
        "plus": False,
        "short": "transformer",
        "label": "a substation transformer’s lead time",
        "basis": "Large power transformers took 80 to 210 weeks to arrive in 2024, 120 weeks on average (Wood Mackenzie); two to five years by 2024 (National Infrastructure Advisory Council, cited by Duke).",
        "sources": ["woodmac_transformers", "duke_report"],
    },
    "generation": {
        "lo": 3.0,
        "hi": 5.0,
        "plus": True,
        "short": "new plants",
        "label": "new power plants",
        "basis": "New generation plants take 3 to 5 years (GridLab) and can spend 4 to 5 years in interconnection queues (Belfer Center); Berkeley Lab finds the time from request to operation now exceeds 5 years.",
        "sources": ["gridlab_large_loads", "belfer_dc", "lbnl_queued_up"],
    },
    "line_doubled": {
        "lo": 3.0,
        "hi": 5.0,
        "plus": True,
        "short": "line doubled",
        "label": "a line more than doubled on its corridor",
        "basis": "Raising a line’s capacity by 100 to 300 percent on its existing corridor (a voltage uprate) takes 3 to 5 years (the 2035 Report, from industry interviews, the average case); high-voltage transmission upgrades can take 7 to 10 years to plan, approve and build (Belfer Center).",
        "sources": ["reconductoring_2035", "belfer_dc"],
    },
    "new_line": {
        "lo": 5.0,
        "hi": 15.0,
        "plus": False,
        "short": "new line",
        "label": "a new transmission line",
        "basis": "A raise past four times a line’s rating is more than any upgrade on an existing corridor gives in the 2035 Report’s comparison (100 to 300 percent at most), so it means new transmission: new (greenfield) lines take 5 to 15 years (the 2035 Report); high-voltage upgrades often take 7 to 10 years to plan, approve and build (Belfer Center).",
        "sources": ["reconductoring_2035", "belfer_dc"],
    },
}

# Ties on (hi, lo) are broken by this fixed order, never by set iteration: a line doubled (3-5+ years) is named over
# new plants (3-5+ years), since the meter's plants line already marks where new generation starts.
RANK = {"connect": 0, "line": 1, "transformer": 2, "generation": 3, "line_doubled": 4, "new_line": 5}
# The 2035 Report's Figure 5: an upgrade on an existing corridor raises a line's capacity by 300 % at most (voltage
# uprate, HVDC conversion), i.e. to four times its rating; past that, new transmission.
CORRIDOR_MAX_RATIO = 4.0


def _order(k: str) -> tuple:
    """Slower sorts later: the range's end, then its start, then the fixed rank (deterministic ties)."""
    return (ITEMS[k]["hi"], ITEMS[k]["lo"], RANK[k])


NOTE = (
    "Typical ranges from published sources, not a schedule: every one varies by utility, region and project. The campuses "
    "and their upgrades are the synthetic grid model’s, not any real utility’s or project’s. Each campus waits for the "
    "slowest thing it depends on: every upgrade made for it and for the campuses before it, and new power plants past "
    "the plants’ reserve line."
)
FLEX_NOTE = "Flexible campuses (full power except at the peak) need fewer upgrades, so they can often connect sooner (Duke University, 2025)."


def _as_of(today: date | None = None) -> float:
    """Today as a fractional year (2026.73 for late September 2026)."""
    d = today or date.today()
    return d.year + (d.timetuple().tm_yday - 1) / 365.25


def _line_kind(ps: list[dict]) -> str:
    """One line's raises so far, by how far above its original rating it ends: up to twice, reconductored or rebuilt;
    past twice (or priced as a new line beside it), more than doubled on its corridor; past four times, a new line."""
    orig = float(next((p.get("rating_original_mva") for p in ps if p.get("rating_original_mva")), None) or ps[0].get("rating_before_mva") or 0.0)
    top = max(float(p.get("rating_after_mva") or 0.0) for p in ps)
    if orig > 0 and top > CORRIDOR_MAX_RATIO * orig + 1e-6:
        return "new_line"
    priced_new = any("new line" in str((p.get("cost") or {}).get("method") or "") for p in ps)
    if priced_new or (orig > 0 and top > costs.RECONDUCTOR_MAX_RATIO * orig + 1e-6):
        return "line_doubled"
    return "line"


def _span(kinds: set[str], as_of: float) -> dict:
    """The range for a campus waiting on `kinds` (always with "connect"): elementwise max, and the item that sets it."""
    ks = {"connect", *kinds}
    lo = max(ITEMS[k]["lo"] for k in ks)
    hi = max(ITEMS[k]["hi"] for k in ks)
    item = max(ks, key=_order)
    plus = any(ITEMS[k]["plus"] and ITEMS[k]["hi"] >= hi for k in ks)
    return {
        "lo": lo,
        "hi": hi,
        "plus": plus,
        "item": item,
        "from_year": math.floor(as_of + lo),
        "to_year": math.floor(as_of + hi),
    }


def _name(p: dict) -> str:
    return p.get("short") or str(p.get("label") or "").removeprefix("the ") or f"#{p.get('branch_id')}"


def _plan(steps: list[dict], plants_n: int | None, as_of: float) -> list[dict]:
    """Per campus of one plan (the cumulative upgrades up to it, and the plants' line). `limit` names what sets the
    campus's time: the first upgrade of that kind in the plan and the campus it was made for (a line counts as doubled
    from the campus whose raise took it past twice its original rating, and as a new line past four times), or the
    plants' line."""
    out = []
    by_line: dict[int, list[dict]] = {}
    kind_of: dict[int, str] = {}
    first: dict[str, dict] = {}  # kind -> {name, n}: the first upgrade of that kind, in plan order
    for st in steps:
        n = int(st["n"])
        for p in st.get("projects") or []:
            if p.get("kind") == "transformer":
                first.setdefault("transformer", {"name": _name(p), "n": n})
                continue
            bid = int(p.get("branch_id", p.get("id", 0)))
            by_line.setdefault(bid, []).append(p)
            k = _line_kind(by_line[bid])
            if kind_of.get(bid) != k:
                kind_of[bid] = k
                first.setdefault(k, {"name": _name(p), "n": n})
        kinds = set(kind_of.values())
        if "transformer" in first:
            kinds.add("transformer")
        if plants_n is not None and n > plants_n:
            kinds.add("generation")
            first.setdefault("generation", {"name": None, "n": plants_n + 1})
        span = _span(kinds, as_of)
        out.append({"n": n, **span, "limit": first.get(span["item"]), "waits_for": sorted(kinds, key=_order)})
    return out


def _ai_steps(cap: dict) -> list[dict] | None:
    """Gemini's verified capacity plan in the meter's order (frontend capacity.js aiCapPlan): the leading campuses the
    engine fits today need nothing; every raise lands on the first campus past them (the plan is priced as a set)."""
    ai = cap.get("ai") or {}
    if not ai.get("verified") or ai.get("status") not in ("beat", "matched") or not ai.get("placements"):
        return None
    eng_today = (cap.get("firm") or {}).get("today") or 0
    today = 0
    pl = ai["placements"]
    while today < len(pl) and pl[today].get("engine_n") is not None and pl[today]["engine_n"] <= eng_today:
        today += 1
    projects = ai.get("projects") or []
    return [{"n": k + 1, "projects": projects if (k == today and projects) else []} for k in range(len(pl))]


def time_to_power(result: dict, today: date | None = None) -> dict:
    """Per-campus time to power for a finished study's capacity section (always-on, flexible and Gemini's plan)."""
    cap = (result or {}).get("capacity")
    if not cap or (not (cap.get("firm") or {}).get("steps") and not (cap.get("flexible") or {}).get("steps")):
        raise ValueError("no campuses-at-once plan")
    as_of = _as_of(today)
    plants = cap.get("plants") or {}
    firm_n = plants.get("campuses_firm")
    flex_n = plants.get("campuses_flexible")
    firm = _plan((cap.get("firm") or {}).get("steps") or [], firm_n, as_of)
    flexible = _plan((cap.get("flexible") or {}).get("steps") or [], flex_n, as_of)
    for c in firm:
        f = flexible[c["n"] - 1] if c["n"] <= len(flexible) else None
        c["flex"] = {k: f[k] for k in ("lo", "hi", "plus", "item", "from_year", "to_year")} if f else None
        c["flex_sooner"] = bool(f and (f["hi"], f["lo"]) < (c["hi"], c["lo"]))
    ai_steps = _ai_steps(cap)
    used = {k for plan in (firm, flexible) for c in plan for k in c["waits_for"]} | {"connect"}
    return {
        "region": result.get("region"),
        "mw": result.get("mw"),
        "load_factor": result.get("load_factor"),
        "as_of": (today or date.today()).isoformat(),
        "year": as_of,
        "items": {k: {"id": k, "rank": RANK[k], **v} for k, v in ITEMS.items()},
        "used": sorted(used, key=_order),
        "firm": {"campuses": firm, "plants_n": firm_n},
        "flexible": {"campuses": flexible, "plants_n": flex_n},
        "ai": {"campuses": _plan(ai_steps, firm_n, as_of), "plants_n": firm_n} if ai_steps else None,
        "sources": SOURCES,
        "note": NOTE,
        "flex_note": FLEX_NOTE,
        "flex_sources": ["duke_flex"],
        "synthetic": True,
    }


@router.get("/api/unlock/time-to-power")
@limiter.limit("240/minute")
def time_to_power_route(
    request: Request,
    region: str = Query(DEFAULT_REGION, max_length=8),
    mw: float = Query(1000.0),
    load_factor: float = Query(1.0),
):
    """Roughly when each campus of the cached Strengthen study could connect (typical ranges with sources). Reads the
    study cache only: it never starts a study (LAZY)."""
    key = unlock._key_of(unlock.UnlockIn(region=region, mw=mw, load_factor=load_factor))
    with unlock._cache_lock:
        hit = unlock._cache.get(key)
    if hit is None:
        raise HTTPException(status_code=404, detail="No study for this state, size and load level yet: run it on Strengthen the grid first.")
    try:
        return time_to_power(hit)
    except ValueError:
        raise HTTPException(status_code=409, detail="This study has no campuses-at-once plan, so there is nothing to time.") from None
