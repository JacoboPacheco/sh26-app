"""The AI analyst: "What it would take" for one proposed data center, worked out by a Gemini agent that calls the
engine as tools, then checked number by number.

    POST /api/analyst/{catalog id}      the whole analysis for a catalog proposal (waits for it; cached per case)
    POST /api/analyst                   {"id": "<catalog id>"} or a case {"region", "lat", "lon", "mw"}; with
                                        "live": true it starts a job instead and returns {job, status, trace}
    GET  /api/analyst/jobs/{job id}     a running analysis: the trace so far, then the result

The agent is built on Gemini's native function calling (llm.complete_tools, with fallback= and timeout=10 on every
call): the six engine tools below are declared as functionDeclarations (FUNCTION_DECLARATIONS, generated from
TOOL_SPECS with the same bounds _clean_args enforces, plus a required "why": one sentence for the reader, shown under
the call in the trace once its numbers check). Gemini's first reply must call a tool (mode ANY); later replies may call
more (mode VALIDATED with the memo schema: calls that follow their declarations, several at once when useful) or write
the memo, up to MAX_TOOL_CALLS tool calls and MAX_GEMINI_CALLS Gemini calls in all. Each functionCall is validated like
a route's input and run on the engine (a repeat of an earlier call is answered "already called", not run again); its
result goes back as a functionResponse (same id), after the model's reply echoed verbatim with its thought signature.
The trace shows each real call ("whatif_size(mw=600)", via "function_call") and the engine's result:

    whatif_size {"mw": N}          the campus at N MW at this site: lines over their limit, the busiest line, lines at
                                     90 % or more, the site's room, and the cascade's people without power (estimate)
    site_report_nearby {}            the six nearest substations (sitereport.build_report): room, right-size, the verified
                                     upgrade and its cost where the full size doesn't fit
    best_sites_nearby {"radius_km"}  substations within the radius that take the full size (one solve each, fixit's screen)
    ways_to_build {}                 the verified ways to build the full size (briefing.py + solutions.py: the engine's fixes
                                     and the AI-proposed plans the engine re-ran), with their cost
    service {}                       flexible against firm service: who loses power in each cascade
    time_of_day {"when"}             the same campus at 3 AM, 9 AM, 4 PM or in a heat wave

Every tool runs the real DC power-flow engine on the SYNTHETIC grid model (Breakthrough Energy / Texas A&M), and its
result is fed back to Gemini. Then Gemini writes a short memo (structured output: MEMO_SCHEMA as the response schema,
in the same conversation): the biggest size that fits here without upgrades, what the full size needs (verified plans
and their cost), a nearby site that takes it, the grid strain. Every number in the
memo must match a tool result (the same check as the deck's: roundings, thousands, millions, billions; a number
spelled out, "eleven lines", is checked as 11); a memo that fails is sent back once with the numbers that matched
nothing, and if it fails again the plain memo built from the same tool results is used, and that run's Gemini turns are
dropped from the answer cache so the next try asks Gemini afresh. With no key (or no answer) a fixed plan of five engine runs and the template memo run instead,
labeled. Nothing here is a claim about the real project, its owners or its utility: the memo speaks of "a campus of this
reported size at this location on the synthetic model", and names no company, project or utility.

Public like the vote pages (no login, nothing stored). At most MAX_GEMINI_CALLS Gemini calls per analysis (usually 3),
and a finished analysis is cached per case for six hours (a fallback run while a key is set, for two minutes); each
Gemini turn is also in llm's answer cache (keyed on the whole conversation), so a replay is the same run call by call.
"""

from __future__ import annotations

import asyncio
import copy
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

import catalog as catalog_mod
from grid import MW_MAX, MW_MIN, REGIONS, check_site, grid_at, region_code, site_headroom
from limiter import limiter
from llm import AGENT_MODEL, AGENT_THINKING, cache_forget, complete_tools, configured, function_response, note_check
from powerflow import OVER_PCT, area_of

router = APIRouter(tags=["analyst"])
log = logging.getLogger("uvicorn.error")

ID_PATTERN = r"^[a-z0-9-]{1,120}$"
MAX_TOOL_CALLS = 5  # engine runs Gemini may make in all (parallel calls in one reply count one each)
MAX_GEMINI_CALLS = 5  # Gemini calls per analysis: the tool turns, the memo and one rewrite (the last one must write)
AI_TIMEOUT_S = 10
DEADLINE_S = 25.0  # all of Gemini's turns; then the plain memo from the results so far (a request stays under the smoke test's 30 s)
HOT_PCT = 90.0
NEAR_KM_DEFAULT, NEAR_KM_MAX = 100.0, 300.0
NEAR_SCREEN = 14  # substations screened (one solve each) by best_sites_nearby
NEAR_SHOWN = 4
TIMES = {"3am": (0.62, "3 AM (the overnight low)"), "9am": (0.82, "9 AM"), "4pm": (1.0, "4 PM (the summer afternoon peak)"), "heatwave": (1.04, "a heat wave")}
RESULT_MAX_CHARS = 1800  # a tool result as Gemini reads it (a function response is trimmed to about this size)
CACHE_SIZE = 64
CACHE_TTL_S = 6 * 3600
FALLBACK_TTL_S = 120  # a plain run while a key is set (Gemini was down or slow): retried soon
JOBS_MAX = 64
JOB_TTL_S = 900
RUNNING_MAX = 6
THOUGHT_MAX = 200

FRAME = "A campus of this reported size at this location, tested on a SYNTHETIC grid model (Breakthrough Energy / Texas A&M): not a prediction about the real project, its owners or its utility."
TOOLS = ("whatif_size", "site_report_nearby", "best_sites_nearby", "ways_to_build", "service", "time_of_day")
PLAIN_PLAN = [("whatif_size", None), ("site_report_nearby", {}), ("ways_to_build", {}), ("best_sites_nearby", {"radius_km": NEAR_KM_DEFAULT}), ("service", {})]
SECTIONS = (
    ("fits_here", "Biggest size that fits here today"),
    ("full_size", "What the full size needs"),
    ("nearby", "A nearby site that takes it"),
    ("strain", "Grid strain"),
)


# ------------------------------------------------------------------------------------ words
def _n(x) -> str:
    return f"{math.floor(float(x) + 0.5):,}"


def _pretty(name: str) -> str:
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), str(name).strip().lower())


def _approx(n: float) -> str:
    """'11,700': three significant figures, so an estimate never claims more precision than it has."""
    n = float(n)
    if n < 1000:
        return _n(n)
    q = 10 ** (int(math.floor(math.log10(n))) - 2)
    return _n(round(n / q) * q)


def _money(x: float) -> str:
    x = float(x)
    if x >= 999.5e6:
        v = x / 1e9
        return f"${v:.1f} billion".replace(".0 ", " ")
    if x >= 1e6:
        v = x / 1e6
        return f"${v:,.0f} million" if v >= 10 else f"${v:.1f} million".replace(".0 ", " ")
    return f"${round(x, -3):,.0f}"


def _range(lo: float, hi: float) -> str:
    a, b = _money(lo), _money(hi)
    for unit in (" billion", " million"):
        if a.endswith(unit) and b.endswith(unit):
            return f"{a[: -len(unit)]}–{b[1:]}"
    return f"{a}–{b}"


def _args_say(tool: str, args: dict) -> str:
    if tool == "whatif_size":
        return f"what if the campus is {_n(args.get('mw', 0))} MW?"
    if tool == "best_sites_nearby":
        return f"which substations within {_n(args.get('radius_km', NEAR_KM_DEFAULT))} km take the full size?"
    if tool == "time_of_day":
        return f"the same campus at {TIMES.get(args.get('when'), (0, 'another hour'))[1]}"
    return {
        "site_report_nearby": "the six nearest substations: room, right-size, upgrades",
        "ways_to_build": "the verified ways to build the full size, with their cost",
        "service": "flexible against firm service: who loses power in each",
    }.get(tool, tool)


def _call_text(tool: str, args: dict) -> str:
    """The call as code: whatif_size(mw=1200)."""
    inner = ", ".join(f"{k}={_n(v) if isinstance(v, (int, float)) else v}".replace(",", "") if isinstance(v, (int, float)) else f"{k}={v}" for k, v in args.items())
    return f"{tool}({inner})"


# ------------------------------------------------------------------------------------ the case
class _Case:
    """What is being analysed: a place, a size, a state model, and (for a catalog proposal) its reported facts."""

    def __init__(self, region: str, lat: float, lon: float, mw: float, entry: dict | None = None):
        self.region = region_code(region)
        self.lat, self.lon = float(lat), float(lon)
        self.mw = round(float(mw), 1)
        self.entry = entry
        g = grid_at(1.0, self.region)
        self.bus = g.site_bus(self.lat, self.lon)
        s = int(g.bus_sub_idx[self.bus])
        self.sub_name = _pretty(g.sub_name[s])
        self.town = area_of(g.sub_name[s])
        self.kv = float(g.bus_kv[self.bus])
        self.state_name = REGIONS[self.region]["name"]
        self.key = json.dumps([self.region, round(self.lat, 4), round(self.lon, 4), self.mw])

    def describe(self) -> dict:
        out = {"region": self.region, "state_name": self.state_name, "lat": round(self.lat, 5), "lon": round(self.lon, 5), "mw": self.mw,
               "substation": self.sub_name, "town": self.town, "kv": self.kv}
        if self.entry:
            out["id"] = self.entry["id"]
            out["reported_mw"] = self.entry.get("mw")
        return out


def _case_for_id(eid: str) -> _Case:
    cat = catalog_mod.catalog()
    e = cat["entries"].get(eid)
    if e is None:
        raise HTTPException(status_code=404, detail="No proposal with that id in the catalog")
    if e.get("duplicate_of"):
        e = cat["entries"][e["duplicate_of"]]
    if e["state"] not in REGIONS:
        raise HTTPException(status_code=422, detail=f"No grid model for {e.get('state_name') or 'this place'}: the models cover the lower 48 states.")
    if e.get("lat") is None or e.get("lon") is None or not e.get("mw"):
        raise HTTPException(status_code=422, detail="This catalog entry has no usable location or size to test.")
    mw = float(min(float(e["mw"]), MW_MAX))
    check_site(float(e["lat"]), float(e["lon"]), mw, e["state"])  # a readable 422 when the model can't test it here
    return _Case(e["state"], e["lat"], e["lon"], mw, e)


# ------------------------------------------------------------------------------------ the tools (the real engine)
_tool_cache: "OrderedDict[str, dict]" = OrderedDict()
_tool_lock = threading.Lock()
TOOL_CACHE_SIZE = 256


def _strain(st) -> dict:
    pct = np.where(st.active, st.loading_pct, 0.0)
    return {"busiest_line_pct": round(float(pct.max()) if pct.size else 0.0, 1), "lines_at_90pct_or_more": int(np.count_nonzero(pct >= HOT_PCT)),
            "lines_over_limit": int(np.count_nonzero(st.active & (st.loading_pct > OVER_PCT + 1e-6)))}


def _line_label(g, i: int) -> str:
    fs, ts = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
    a, z = _pretty(g.sub_name[fs]), _pretty(g.sub_name[ts])
    return f"{a} transformer" if fs == ts else f"{a} to {z} line"


def _whatif(c: _Case, mw: float, load_factor: float = 1.0) -> dict:
    g = grid_at(load_factor, c.region)
    bus = g.site_bus(c.lat, c.lon)
    ones = np.ones(g.m, dtype=bool)
    st = g.solve(ones, g.extra_load([(bus, mw)]))
    over = g.overloaded(st)
    out = {
        "mw": round(mw, 1),
        "substation": c.sub_name,
        "kv": c.kv,
        "room_mw": round(float(site_headroom(g, bus)), 1),
        "fits": not over and st.lost_existing_mw <= 0.5,
        "lines_over_limit": len(over),
        "worst_lines": [{"line": _line_label(g, g.br_index[o["id"]]), "loading_pct": o["pct"], "rating_mva": round(o["rate"])} for o in over[:3]],
        "strain_with_campus": _strain(st),
        "strain_grid_alone": _strain(g.base),
        "strain_threshold_pct": HOT_PCT,  # "lines at 90 % or more of their rating"
        "limit_pct": OVER_PCT,
    }
    if over:
        cc = g.cascade_case(g.extra_load([(bus, mw)]), [], {})
        out["cascade"] = {"steps": int(cc["total_steps"]), "people_without_power": int(cc["people"]), "campus_cut_off": bool(cc["site_cut_off"])}
    else:
        out["cascade"] = {"steps": 0, "people_without_power": g.people(st.lost_existing_mw), "campus_cut_off": False}
    return out


def t_whatif_size(c: _Case, args: dict) -> dict:
    mw = float(args.get("mw", c.mw))
    return _whatif(c, mw)


def t_site_report_nearby(c: _Case, args: dict) -> dict:
    import sitereport

    r = sitereport.build_report(c.region, c.lat, c.lon, c.mw, 1.0, False)
    subs = []
    for s in r["substations"]:
        up = s.get("upgrade") or {}
        row = {"substation": s["name"], "distance_km": s["distance_km"], "kv": s["kv"], "room_mw": round(min(s["headroom_mw"], 1e5), 1),
               "fits_full_size": s["fits"], "right_size_mw": s["right_size_mw"]}
        if up:
            row["upgrade"] = {"lines": up["count"], "mva_added": up["mva_added"], "cost_low_usd": up["cost_low_usd"], "cost_high_usd": up["cost_high_usd"], "verified": up["verified"]}
        subs.append(row)
    best = next((x for x in subs if x["substation"] == next((s["name"] for s in r["substations"] if s["id"] == r["best_id"]), None)), subs[0] if subs else None)
    n1 = r.get("n_minus_1") or {}
    return {"mw": c.mw, "verdict": r["verdict"], "verdict_kind": r["verdict_kind"], "best": best, "substations": subs,
            "n_minus_1": {"checked": n1.get("checked"), "insecure": n1.get("insecure_count"), "campus_tips_over": n1.get("campus_adds_count")} if n1 else None}


def t_best_sites_nearby(c: _Case, args: dict) -> dict:
    import fixit
    from sitereport import _km_all

    radius = float(args.get("radius_km", NEAR_KM_DEFAULT))
    g = grid_at(1.0, c.region)
    km = _km_all(g, c.lat, c.lon)
    hb = g.headroom_by_sub()
    here = int(g.bus_sub_idx[c.bus])
    cand = [i for i in np.flatnonzero(km <= radius) if int(i) != here and hb[int(g.sub_ids[i])] >= c.mw]
    cand.sort(key=lambda i: (-min(hb[int(g.sub_ids[i])], 1e5), float(km[i])))
    found, screened, seen_towns = [], 0, set()
    for i in cand:
        if screened >= NEAR_SCREEN or len(found) >= NEAR_SHOWN:
            break
        town = area_of(g.sub_name[i])
        if town in seen_towns:
            continue
        pct, strain, _k = fixit._screen(g, g.connect_bus(int(i)), c.mw)
        screened += 1
        if pct > OVER_PCT + 1e-6:
            continue
        seen_towns.add(town)
        found.append({"substation": _pretty(g.sub_name[i]), "town": town, "distance_km": round(float(km[i]), 1), "kv": float(g.bus_kv[g.connect_bus(int(i))]),
                      "room_mw": round(min(float(hb[int(g.sub_ids[i])]), 1e5), 1), "busiest_line_pct_with_campus": round(pct, 1),
                      "lines_at_90pct_or_more": strain["hot"], "lat": round(float(g.sub_lat[i]), 4), "lon": round(float(g.sub_lon[i]), 4)})
    found.sort(key=lambda s: s["distance_km"])
    return {"mw": c.mw, "radius_km": round(radius), "sites_that_take_it": found, "screened": screened,
            "note": "One DC power-flow solve per site with the full campus there: no line over its limit."}


def t_ways_to_build(c: _Case, args: dict) -> dict:
    import briefing

    rep = briefing.report_for(briefing.BriefingIn(region=c.region, lat=c.lat, lon=c.lon, mw=c.mw))
    fixes = rep.get("fixes") or []
    ways = []
    for i in rep.get("solutions") or []:
        f = fixes[i]
        cost = f.get("cost")
        d = f.get("detail") or {}
        row = {"rank": f.get("rank"), "how": f.get("action"), "family": f.get("family"), "kept_mw": f.get("kept_mw"), "kept_pct": f.get("kept_pct"),
               "by": "gemini, verified by the engine" if f.get("by") == "gemini" else "engine", "people_without_power_after": (f.get("outcome") or {}).get("people", 0)}
        if cost:
            row["cost_low_usd"], row["cost_high_usd"] = cost["low"], cost["high"]
        if d.get("lines"):
            row["upgraded_lines"] = d.get("lines")
        if f.get("strain"):
            row["busiest_line_pct_after"] = f["strain"].get("peak_pct")
        ways.append(row)
    ev = rep.get("event") or {}
    ag = rep.get("agentic") or {}
    return {"mw": c.mw, "verdict": rep.get("verdict"), "people_without_power_if_built_as_is": int(ev.get("people") or 0), "cascade_steps": int(ev.get("steps") or 0),
            "verified_ways": ways, "ai_plans": {"status": ag.get("status"), "asked": ag.get("asked"), "verified": ag.get("verified")} if ag else None}


def t_service(c: _Case, args: dict) -> dict:
    g = grid_at(1.0, c.region)
    extra = g.extra_load([(c.bus, c.mw)])
    flex = g.cascade_case(extra, [], {})
    firm = g.cascade_case(extra, [], {}, firm_buses=[c.bus])
    return {
        "mw": c.mw,
        "flexible": {"meaning": "the campus can be cut off when its own lines trip", "people_without_power": int(flex["people"]), "steps": int(flex["total_steps"]),
                     "campus_cut_off": bool(flex["site_cut_off"])},
        "firm": {"meaning": "the grid operator keeps the campus on and cuts other customers instead", "people_without_power": int(firm["people"]),
                 "steps": int(firm["total_steps"]), "campus_kept_on": bool(firm.get("firm_held")), "load_cut_mw": round(float(firm.get("shed_mw") or 0.0), 1)},
    }


def t_time_of_day(c: _Case, args: dict) -> dict:
    when = args.get("when", "heatwave")
    level, label = TIMES[when]
    out = _whatif(c, c.mw, level)
    return {"when": label, "load_pct_of_model": round(level * 100), **{k: out[k] for k in ("mw", "room_mw", "fits", "lines_over_limit", "worst_lines", "strain_with_campus", "cascade")}}


RUNNERS = {"whatif_size": t_whatif_size, "site_report_nearby": t_site_report_nearby, "best_sites_nearby": t_best_sites_nearby,
           "ways_to_build": t_ways_to_build, "service": t_service, "time_of_day": t_time_of_day}


def _clean_args(c: _Case, tool: str, raw) -> tuple[dict | None, str | None]:
    """(args, None) or (None, why it was refused): the agent's arguments, validated like a route's input."""
    raw = raw if isinstance(raw, dict) else {}
    if tool == "whatif_size":
        try:
            mw = float(raw.get("mw", c.mw))
        except (TypeError, ValueError):
            return None, "mw must be a number"
        if not math.isfinite(mw) or not (MW_MIN <= mw <= MW_MAX):
            return None, f"mw must be between {MW_MIN} and {MW_MAX:,}"
        return {"mw": round(mw / 10.0) * 10.0 if mw >= 10 else round(mw, 1)}, None
    if tool == "best_sites_nearby":
        try:
            r = float(raw.get("radius_km", NEAR_KM_DEFAULT))
        except (TypeError, ValueError):
            return None, "radius_km must be a number"
        if not math.isfinite(r):
            return None, "radius_km must be a number"
        return {"radius_km": float(min(max(round(r), 20), NEAR_KM_MAX))}, None
    if tool == "time_of_day":
        when = str(raw.get("when", "")).lower().replace(" ", "").replace("-", "")
        when = {"heat": "heatwave", "3": "3am", "9": "9am", "4": "4pm", "night": "3am", "peak": "4pm"}.get(when, when)
        if when not in TIMES:
            return None, "when must be one of 3am, 9am, 4pm, heatwave"
        return {"when": when}, None
    return {}, None


def run_tool(c: _Case, tool: str, args: dict) -> tuple[dict, int, bool]:
    """(result, ms, cached). Sync: call it in the thread pool."""
    key = json.dumps([c.key, tool, args], sort_keys=True)
    with _tool_lock:
        hit = _tool_cache.get(key)
        if hit is not None:
            _tool_cache.move_to_end(key)
            return copy.deepcopy(hit), 0, True
    t0 = time.perf_counter()
    try:
        out = RUNNERS[tool](c, args)
    except HTTPException as e:  # the engine refused this check (e.g. a case it can't solve): the agent is told, not crashed
        return {"error": str(e.detail)[:200]}, round((time.perf_counter() - t0) * 1000), False
    except (ArithmeticError, ValueError, RuntimeError, KeyError, np.linalg.LinAlgError) as e:
        log.warning("analyst: %s failed on %s: %s", tool, c.key, e)
        return {"error": "The engine could not run this check."}, round((time.perf_counter() - t0) * 1000), False
    ms = round((time.perf_counter() - t0) * 1000)
    with _tool_lock:
        _tool_cache[key] = copy.deepcopy(out)
        while len(_tool_cache) > TOOL_CACHE_SIZE:
            _tool_cache.popitem(last=False)
    return out, ms, False


def for_gemini(tool: str, r: dict) -> dict:
    """A tool result as Gemini reads it: the same numbers, fewer keys (a shorter prompt answers faster)."""
    if r.get("error"):
        return r
    if tool == "site_report_nearby":
        subs = []
        for x in r["substations"]:
            row = {k: x[k] for k in ("substation", "distance_km", "room_mw", "right_size_mw", "fits_full_size")}
            up = x.get("upgrade")
            if up and up.get("verified"):
                row["upgrade_for_full_size"] = {"lines": up["lines"], "cost_low_usd": up["cost_low_usd"], "cost_high_usd": up["cost_high_usd"]}
            subs.append(row)
        return {"mw": r["mw"], "verdict": r["verdict"], "substations": subs}
    if tool == "ways_to_build":
        keep = ("how", "kept_pct", "cost_low_usd", "cost_high_usd", "by", "busiest_line_pct_after")
        return {"mw": r["mw"], "verdict": r["verdict"], "people_without_power_if_built_as_is": r["people_without_power_if_built_as_is"],
                "verified_ways": [{k: w[k] for k in keep if k in w} for w in r["verified_ways"][:4]]}
    if tool == "best_sites_nearby":
        return {"mw": r["mw"], "radius_km": r["radius_km"],
                "sites_that_take_it": [{k: x[k] for k in ("substation", "distance_km", "room_mw", "busiest_line_pct_with_campus")} for x in r["sites_that_take_it"]]}
    if tool in ("whatif_size", "time_of_day"):
        return {k: v for k, v in r.items() if k not in ("kv", "limit_pct")}
    return r


def summarize(tool: str, r: dict) -> tuple[str, str]:
    """(one plain line on what the engine found, tone: over | holds | info) for the trace."""
    if r.get("error"):
        return f"could not run this check ({r['error']})", "muted"
    if tool in ("whatif_size", "time_of_day"):
        when = f" at {r['when']}" if r.get("when") else ""
        if r["fits"]:
            s = f"{_n(r['mw'])} MW{when}: every line within its limit; room for {_n(r['room_mw'])} MW; busiest line at {r['strain_with_campus']['busiest_line_pct']:.0f}%."
            return s, "holds"
        cc = r["cascade"]
        s = f"{_n(r['mw'])} MW{when}: {r['lines_over_limit']} {'line' if r['lines_over_limit'] == 1 else 'lines'} over the limit (room is {_n(r['room_mw'])} MW)"
        s += f"; the cascade leaves an estimated {_approx(cc['people_without_power'])} people without power." if cc["people_without_power"] else "; nobody loses power in the cascade."
        return s, "over"
    if tool == "site_report_nearby":
        b = r.get("best") or {}
        if not r.get("substations"):
            return "No substation near the site on the model.", "over"
        fit = [x for x in r["substations"] if x["fits_full_size"]]
        if fit:
            x = min(fit, key=lambda x: x["distance_km"])
            return f"{len(fit)} of {len(r['substations'])} nearest substations take {_n(r['mw'])} MW; the closest is {x['substation']}, {x['distance_km']:g} km away, with {_n(x['room_mw'])} MW of room.", "holds"
        up = b.get("upgrade") or {}
        s = f"None of the {len(r['substations'])} nearest substations takes {_n(r['mw'])} MW without upgrades; the most any takes is {_n(max(x['right_size_mw'] for x in r['substations']))} MW."
        if up.get("verified") and b.get("substation"):
            s += f" At {b['substation']} the full size needs {up['lines']} upgrades, {_range(up['cost_low_usd'], up['cost_high_usd'])} (verified)."
        return s, "over"
    if tool == "best_sites_nearby":
        found = r["sites_that_take_it"]
        if not found:
            return f"No substation within {_n(r['radius_km'])} km takes {_n(r['mw'])} MW without a line going over ({r['screened']} checked).", "over"
        x = found[0]
        return f"{len(found)} {'substation takes' if len(found) == 1 else 'substations take'} {_n(r['mw'])} MW within {_n(r['radius_km'])} km; the closest is {x['substation']}, {x['distance_km']:g} km away.", "holds"
    if tool == "ways_to_build":
        ways = r["verified_ways"]
        if r["verdict"] == "nothing_happened":
            return f"Nothing to fix: at {_n(r['mw'])} MW no line trips.", "holds"
        if not ways:
            return "The engine found no verified way to build the full size.", "over"
        full = [w for w in ways if (w.get("kept_pct") or 0) >= 90]
        w = (full or ways)[0]
        s = f"{len(ways)} verified {'way' if len(ways) == 1 else 'ways'}; the first: {w['how']}"
        if w.get("cost_high_usd"):
            s += f", {_range(w['cost_low_usd'], w['cost_high_usd'])}"
        return s + ".", "holds"
    if tool == "service":
        f, m = r["flexible"], r["firm"]
        who = lambda k: "nobody" if not k else f"an estimated {_approx(k)} people"  # noqa: E731
        return (f"Flexible: {who(f['people_without_power'])} without power"
                + (", campus cut off" if f["campus_cut_off"] else "")
                + f". Firm: {who(m['people_without_power'])}" + (" (campus kept on)." if m["campus_kept_on"] else " (the campus is cut off anyway).")), "info"
    return "Done.", "info"


# ------------------------------------------------------------------------------------ the number check
_NUM = re.compile(r"\d+(?:[,.]\d+)*")
_YEAR = re.compile(r"\b(?:19|20)\d\d\b(?!\s*(?:MW|MVA|megawatt|people|lines|km|%|percent))")
_REAL_NAMES = re.compile(
    r"\b(FPL|Florida Power|Duke Energy|TECO|Tampa Electric|JEA|ERCOT|Oncor|CenterPoint|Dominion|Georgia Power|Southern Company|PG&E|Con ?Ed(?:ison)?|Xcel|Entergy|AEP|PJM|MISO|CAISO|NYISO|TVA|NextEra)\b",
    re.I,
)
_BLAME = re.compile(r"will cause|will black|will trigger|is to blame|\bblame|illegal|fraud|scam|corrupt|guilty|negligen", re.I)
_GENERIC = set(
    "data center centers campus park project technology tech holdings compute computing solutions energy developer applicant partner infrastructure "
    "tenant undisclosed disclosed user places international airport county near town outside unincorporated the and with end not llc inc corp "
    "company group ventures capital partners properties development realty".split()
)


_SMALL = "two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_WORD_VALUE = {w: i + 2 for i, w in enumerate(_SMALL)}
_TENS = {w: (i + 2) * 10 for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_ONES = {w: i + 1 for i, w in enumerate("one two three four five six seven eight nine".split())}
_WORDNUM = re.compile(r"\b(?:(" + "|".join(_TENS) + r")(?:-(" + "|".join(_ONES) + r"))?|(" + "|".join(_SMALL) + r"))\b", re.I)


def _digits(text: str) -> str:
    """Numbers spelled out as words ("eleven lines", "twenty-four") written as digits, so the number check sees them
    ("one" is left alone: it is always allowed, and "no one" is not a number)."""
    def val(m: re.Match) -> str:
        if m.group(3):
            return str(_WORD_VALUE[m.group(3).lower()])
        return str(_TENS[m.group(1).lower()] + (_ONES[m.group(2).lower()] if m.group(2) else 0))

    return _WORDNUM.sub(val, text or "")


def _canon(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.2f}".rstrip("0").rstrip(".")


def _forms(v: float) -> set[str]:
    """A tool's number as a sentence may print it: itself, 1 decimal, the whole number (round, floor, ceil), rounded
    to tens..millions, and in thousands, millions or billions with 0-2 decimals ("$147 million", "1.2 GW") — every
    rounded form within 5 % of the number, so "about 12,000 people" passes for 11,694 and "20,000" does not."""
    v = abs(float(v))
    near = lambda r: r and abs(r - v) <= 0.05 * v  # noqa: E731
    out = {_canon(v), _canon(round(v, 1)), _canon(round(v)), _canon(math.floor(v)), _canon(math.ceil(v))}
    for k in range(1, 7):
        p = 10**k
        for r in (round(v / p) * p, math.floor(v / p) * p, math.ceil(v / p) * p):
            if near(r):
                out.add(_canon(r))
    for scale in (1e3, 1e6, 1e9):
        for d in (0, 1, 2):
            for r in (round(v / scale, d), math.floor(v / scale * 10**d) / 10**d, math.ceil(v / scale * 10**d) / 10**d):
                if near(r * scale):
                    out.add(_canon(r))
    return out


def _token_values(tok: str) -> list[float]:
    try:
        return [float(tok.replace(",", ""))]
    except ValueError:
        return []


def _walk_numbers(x, acc: set[str]) -> None:
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        if math.isfinite(float(x)):
            acc |= _forms(float(x))
        return
    if isinstance(x, str):
        for tok in _NUM.findall(x):
            for v in _token_values(tok):
                acc |= _forms(v)
        return
    if isinstance(x, dict):
        for v in x.values():
            _walk_numbers(v, acc)
        return
    if isinstance(x, (list, tuple)):
        if x:
            acc |= _forms(len(x))  # a count is a result too: "4 verified ways", "the six nearest substations"
        for v in x:
            _walk_numbers(v, acc)


def allowed_numbers(results: list[dict]) -> set[str]:
    """Every number any tool returned (and every argument it was called with), in every printed form, and the length
    of every list in them (how many ways, sites, substations)."""
    acc: set[str] = {"0", "1"}
    for r in results:
        _walk_numbers(r.get("args"), acc)
        _walk_numbers(r.get("result"), acc)
    return acc


def _names_to_avoid(c: _Case, results: list[dict]) -> list[str]:
    """Distinctive words of the proposal's reported project and company names (the memo never names them), minus
    words that are also place or substation names in the tool results (those may be said)."""
    if not c.entry:
        return []
    blob = json.dumps([r.get("result") for r in results]).lower() + " " + " ".join(str(c.entry.get(k) or "") for k in ("city", "county", "state_name")).lower()
    words = set(re.findall(r"[A-Za-z][A-Za-z0-9&'-]{3,}", f"{c.entry.get('name') or ''} {c.entry.get('company') or ''}"))
    return sorted(w for w in words if w.lower() not in _GENERIC and w.lower() not in blob)


def _numbers_ok(text: str, results: list[dict]) -> bool:
    allowed = allowed_numbers(results)
    return all(any(_canon(v) in allowed for v in _token_values(t)) for t in _NUM.findall(_digits(text)))


def check_memo(memo: dict, c: _Case, results: list[dict]) -> tuple[bool, list[str], int, str | None]:
    """(ok, numbers that matched no tool result, numbers checked, other reason)."""
    text = " ".join([memo.get("headline") or ""] + [s["text"] for s in memo.get("sections") or []])
    if _REAL_NAMES.search(text):
        return False, [], 0, "names a real utility or grid operator"
    if _BLAME.search(text):
        return False, [], 0, "blames or predicts about a real project"
    if _YEAR.search(text):
        return False, [], 0, "gives a year"
    for w in _names_to_avoid(c, results):
        if re.search(r"\b" + re.escape(w) + r"\b", text, re.I):
            return False, [], 0, "names the real project or company"
    allowed = allowed_numbers(results + [{"result": c.describe()}])
    toks = _NUM.findall(_digits(text))  # "eleven lines" is checked as 11
    bad = [t for t in toks if not any(_canon(v) in allowed for v in _token_values(t))]
    return not bad, bad, len(toks), None


# ------------------------------------------------------------------------------------ the memo
def _memo_from(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    secs = []
    for key, heading in SECTIONS:
        t = " ".join(str(raw.get(key) or "").split())[:420]
        if t:
            secs.append({"key": key, "heading": heading, "text": t})
    head = " ".join(str(raw.get("headline") or "").split())[:240]
    if not head or len(secs) < 3:
        return None
    return {"headline": head, "sections": secs}


def _get(results: list[dict], tool: str, pred=None) -> dict | None:
    for r in results:
        if r["tool"] == tool and (pred is None or pred(r)):
            return r["result"]
    return None


def plain_memo(c: _Case, results: list[dict]) -> dict:
    """The template memo, from the same tool results (every number in it is one of theirs)."""
    w = _get(results, "whatif_size", lambda r: abs(float(r["args"].get("mw", 0)) - c.mw) < 0.05)
    sr = _get(results, "site_report_nearby")
    ways = _get(results, "ways_to_build")
    near = _get(results, "best_sites_nearby")
    svc = _get(results, "service")
    mw = _n(c.mw)
    secs = {}
    here = next((x for x in (sr or {}).get("substations", []) if x["substation"] == c.sub_name), None)
    room = w["room_mw"] if w else None
    if w and w["fits"]:
        secs["fits_here"] = f"On the model the full {mw} MW fits at {c.sub_name} with no upgrades: there is room for {_n(room)} MW before the first line overloads."
    elif w:
        right = here["right_size_mw"] if here else room
        secs["fits_here"] = (f"On the model the substation this site connects to ({c.sub_name}) has room for about {_n(right)} MW before a line overloads. "
                             f"At the full {mw} MW, {w['lines_over_limit']} {'line goes' if w['lines_over_limit'] == 1 else 'lines go'} over the limit"
                             + (f" and the cascade leaves an estimated {_approx(w['cascade']['people_without_power'])} people without power." if w["cascade"]["people_without_power"] else "."))
    if ways:
        vw = ways["verified_ways"]
        if ways["verdict"] == "nothing_happened" or (w and w["fits"]):
            secs["full_size"] = f"Nothing on the model: at {mw} MW no line trips here, so no upgrade is needed. A real interconnection study is the utility's, not this model's."
        elif vw:
            full = [x for x in vw if (x.get("kept_pct") or 0) >= 99.5] or vw
            x = full[0]
            s = f"{x['how']}" + (f", an estimated {_range(x['cost_low_usd'], x['cost_high_usd'])}" if x.get("cost_high_usd") else "") + "; the engine re-ran it and it holds."
            ai = sum(1 for y in vw if y["by"].startswith("gemini"))
            s += f" {len(vw)} verified {'way' if len(vw) == 1 else 'ways'} in all" + (f", {ai} of them proposed by Gemini." if ai else ".")
            secs["full_size"] = s
        else:
            secs["full_size"] = "The engine found no verified way to build the full size here on the model."
    fit_near = [x for x in (sr or {}).get("substations", []) if x["fits_full_size"] and x["substation"] != c.sub_name]
    if fit_near:
        x = min(fit_near, key=lambda x: x["distance_km"])
        secs["nearby"] = f"{x['substation']}, {x['distance_km']:g} km away, takes the full {mw} MW with no upgrades on the model ({_n(x['room_mw'])} MW of room)."
    elif near and near["sites_that_take_it"]:
        x = near["sites_that_take_it"][0]
        secs["nearby"] = f"{x['substation']}, {x['distance_km']:g} km away, takes the full {mw} MW with no line over its limit on the model (busiest line at {x['busiest_line_pct_with_campus']:.0f}%)."
    elif near:
        secs["nearby"] = f"No substation within {_n(near['radius_km'])} km takes the full {mw} MW without a line going over its limit on the model."
    if w:
        s0, s1 = w["strain_grid_alone"], w["strain_with_campus"]
        k1, k0 = s1["lines_at_90pct_or_more"], s0["lines_at_90pct_or_more"]
        secs["strain"] = (f"With the campus on, the busiest line runs at {s1['busiest_line_pct']:.0f}% of its rating and {k1} {'line runs' if k1 == 1 else 'lines run'} "
                          f"at 90% or more; without it, {s0['busiest_line_pct']:.0f}% and {k0} {'line' if k0 == 1 else 'lines'}.")
        if svc and not w["fits"]:
            fl, fm = svc["flexible"]["people_without_power"], svc["firm"]["people_without_power"]
            if fl == fm:
                secs["strain"] += f" Firm service changes nothing here: an estimated {_approx(fl)} people lose power either way."
            elif svc["firm"]["campus_kept_on"]:
                secs["strain"] += f" On firm service the campus stays on and an estimated {_approx(fm)} people lose power instead of {_approx(fl)}."
            else:
                secs["strain"] += f" On firm service the campus is cut off too, and an estimated {_approx(fm)} people lose power."
    if w and w["fits"]:
        head = f"On the model a {mw} MW campus fits here as it is."
    elif ways and ways["verified_ways"]:
        head = f"On the model {mw} MW needs grid upgrades here, and the engine verified how."
    else:
        head = f"On the model {mw} MW is past what this site can take."
    return {"headline": head, "sections": [{"key": k, "heading": h, "text": secs[k]} for k, h in SECTIONS if secs.get(k)]}


# ------------------------------------------------------------------------------------ the agent
SYSTEM = """You are Overload's grid analyst: an agent that studies one proposed data-center campus on a SYNTHETIC model of a U.S. state's power grid (the Breakthrough Energy / Texas A&M test system, not any real utility's network). You call its tools (function calls) that run a real DC power-flow engine, read their results, then write a short memo.

Speak only of "a campus of this size at this location on the model". Never name a real company, project, utility or grid operator; never predict what a real project or utility will do; never blame anyone. Every number you write must be copied from a tool result, and written in digits, even small ones ("11 lines", "2 transformers", never "eleven lines"), so the engine can check it. Round the way a newspaper would: money as "$83-147 million", MW and people to whole numbers ("about 11,700 people", "244 MW"), loading as whole percentages ("330%"). Plain sentences, no years, no advice to vote either way. Use the tools by calling them, each call with its "why"; when you write the memo, reply with JSON only."""

# The engine's tools as Gemini function declarations (the REST API's OpenAPI-style schema subset). The bounds are the
# ones _clean_args enforces on every call anyway: Gemini's arguments are validated like a route's input, never trusted.
TOOL_SPECS = {
    "whatif_size": {
        "description": "Runs the DC power flow with the campus at N MW at this site: lines over their limit, the site's room (the MW it takes before the first line overloads), "
                       "the busiest line's loading, lines at 90% or more of their rating, and the cascade's people without power (estimate).",
        "parameters": {"type": "object", "properties": {"mw": {"type": "number", "description": f"The campus size in MW ({MW_MIN} to {MW_MAX}).", "minimum": MW_MIN, "maximum": MW_MAX}},
                       "required": ["mw"]},
    },
    "site_report_nearby": {
        "description": "The six nearest substations for the full size: distance, room, the most each takes without upgrades (right_size_mw), "
                       "and where the full size doesn't fit, the verified upgrade with its cost.",
    },
    "best_sites_nearby": {
        "description": "Substations within the radius that take the full size with no line over its limit (one power-flow solve per site).",
        "parameters": {"type": "object", "properties": {"radius_km": {"type": "number", "description": f"Search radius in km (20 to {NEAR_KM_MAX:g}; {NEAR_KM_DEFAULT:g} if omitted).",
                                                                      "minimum": 20, "maximum": NEAR_KM_MAX}}},
    },
    "ways_to_build": {
        "description": "The verified ways to build the full size here (grid upgrades, a smaller campus, another site), each re-run by the engine, with its estimated cost.",
    },
    "service": {
        "description": "Flexible service (the campus can be cut off) against firm service (other customers are cut instead): people without power in each cascade.",
    },
    "time_of_day": {
        "description": "The same campus at another hour or in a heat wave: lines over their limit, room and the cascade.",
        "parameters": {"type": "object", "properties": {"when": {"type": "string", "enum": list(TIMES), "description": "3am (overnight low), 9am, 4pm (summer peak) or heatwave."}},
                       "required": ["when"]},
    },
}
# Every declaration also takes a required "why": Gemini's one-sentence reason for the call, which the trace shows under
# it (Google's suggestion for notes before a tool call: put them in the call). Mode ANY allows function calls only, so
# without it the first turn's reasoning never reaches the reader. It is never passed to the engine.
WHY_PARAM = {"type": "string", "description": "One short sentence for the reader: what this call checks and why. Use only numbers the case or earlier results gave."}


def _declaration(name: str, spec: dict) -> dict:
    params = copy.deepcopy(spec.get("parameters") or {"type": "object", "properties": {}})
    params["properties"] = {**params["properties"], "why": WHY_PARAM}
    params["required"] = [*params.get("required", []), "why"]
    return {"name": name, "description": spec["description"], "parameters": params}


FUNCTION_DECLARATIONS = [_declaration(name, spec) for name, spec in TOOL_SPECS.items()]
assert [d["name"] for d in FUNCTION_DECLARATIONS] == list(TOOLS)

# Structured output for the memo (the response schema of every turn after the first: Gemini either calls functions or
# writes JSON that follows this; measured Sat on gemini-3.5-flash-lite, about 1 s a turn). Those turns run in mode
# VALIDATED (the documented mode for tools next to structured output: calls are held to their declarations), probed Sat
# 12:15 on all four models of the chain (3.5-flash, 3.5-flash-lite, 3.1-flash-lite, 3.6-flash): 200, 0.8-2 s.
MEMO_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "memo": {"type": "object", "properties": {
            "headline": {"type": "string"}, "fits_here": {"type": "string"}, "full_size": {"type": "string"}, "nearby": {"type": "string"}, "strain": {"type": "string"},
        }, "required": ["headline", "fits_here", "full_size", "nearby", "strain"]},
    },
    "required": ["memo"],
}
OFFLINE = {"__offline__": True}


def _thought(raw) -> str | None:
    if not isinstance(raw, str) or _REAL_NAMES.search(raw) or _BLAME.search(raw):
        return None
    t = re.sub(r"[*`#>]+", "", " ".join(raw.split())).strip()  # not "_": it is in every tool name (whatif_size)
    if len(t) > THOUGHT_MAX:
        t = t[:THOUGHT_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t or None


MEMO_REPLY = '{"thought": "<one short sentence: what the results showed>", "memo": {"headline": "...", "fits_here": "...", "full_size": "...", "nearby": "...", "strain": "..."}}'
WRITE_NOW = "You have used the tool calls this analysis allows. Write the memo now from the function responses above, as JSON only: " + MEMO_REPLY


def _prompt(c: _Case) -> str:
    """The conversation's first message: the case and the memo it needs (the tools are the function declarations)."""
    return "\n".join([
        f"The case: a campus of {_n(c.mw)} MW at this location in {c.state_name}, on the synthetic model. It connects at the {c.sub_name} substation ({c.kv:g} kV), near {c.town}.",
        "Find out what it would take to build it here, then write a memo with these parts:",
        '  "headline": one sentence, the bottom line;',
        '  "fits_here": the biggest size that fits at this site today with no upgrades;',
        '  "full_size": what the full size needs: the verified ways to build it and what they cost;',
        '  "nearby": a nearby site that takes the full size, if any;',
        '  "strain": the grid strain: the busiest line\'s loading and the lines at 90% or more, with and without the campus.',
        "At most two sentences per part.",
        "",
        f"Call the engine's tools to find out: at most {MAX_TOOL_CALLS} calls in all, and you may call several at once. "
        'Give every call its "why": one short sentence, shown to the reader, on what the call checks and why. Never repeat a call you already made.',
        "When you have what the memo needs, reply with JSON only: " + MEMO_REPLY,
    ])


def _json_reply(text: str):
    """The memo turn's text as JSON (it is structured output; a stray code fence is tolerated)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        return None


def _fit(obj: dict, limit: int = RESULT_MAX_CHARS) -> dict:
    """A function response of at most about `limit` characters: the longest list is cut from its end until it fits."""
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


class Run:
    """One analysis: the trace (written live), the tool results, the counters."""

    def __init__(self, c: _Case):
        self.c = c
        self.trace: list[dict] = []
        self.results: list[dict] = []  # {tool, args, result}
        self.calls = 0  # Gemini calls
        self.tools = 0  # tool calls Gemini made (run on the engine)
        self.fcalls = 0  # function calls Gemini returned (run or refused)
        self.model: str | None = None  # the model that answered (the conversation stays on it)
        self.cache_keys: list[str] = []  # llm's answer-cache keys of this run's Gemini turns (dropped when the run fails)
        self.t0 = time.perf_counter()

    def forget(self) -> None:
        """A run whose memo failed: its Gemini turns leave the answer cache, so the next try is a fresh conversation
        instead of a replay of the same failing one (the turns were cached for 48 h, on disk)."""
        n = cache_forget(self.cache_keys)
        if n:
            log.info("analyst: dropped %d cached Gemini turns of a failed run (%s)", n, self.c.key)
        self.cache_keys = []

    def add(self, **row) -> dict:
        row["n"] = len(self.trace) + 1
        self.trace.append(row)
        return row

    def left(self) -> float:
        return DEADLINE_S - (time.perf_counter() - self.t0)

    async def tool(self, tool: str, args: dict, actor: str, thought: str | None = None, backfill: bool = False, call_id: str | None = None) -> dict:
        """Run one tool on the engine, with its call and result rows in the trace. actor "gemini": a Gemini function call
        (via "function_call"; its result goes back as the function response); "engine": the fixed plan's own run."""
        fc = actor == "gemini"
        call_row = dict(actor=actor, kind="call", tool=tool, args=args, tone="info", backfill=backfill, call=_call_text(tool, args),
                        title=_args_say(tool, args)[:1].upper() + _args_say(tool, args)[1:], detail=thought or ("Gemini function call" if fc else None))
        if fc:
            call_row.update(via="function_call", call_id=call_id)
        self.add(**call_row)
        res, ms, cached = await run_in_threadpool(run_tool, self.c, tool, args)
        summary, tone = summarize(tool, res)
        self.results.append({"tool": tool, "args": args, "result": res})
        res_row = dict(actor="engine", kind="result", tool=tool, args=args, tone=tone, result_summary=summary, result=res, ms=ms, cached=cached,
                       title=summary[:1].upper() + summary[1:])
        if fc:
            res_row.update(via="function_response", call_id=call_id)
        self.add(**res_row)
        return res


def _responses_last(contents: list[dict]) -> bool:
    last = contents[-1] if contents else {}
    return last.get("role") == "user" and any("functionResponse" in p for p in last.get("parts") or [])


async def _answer(run: Run, call: dict, must_write: bool) -> dict:
    """One Gemini function call: validated like a route's input, run on the engine, its result (trimmed) returned as
    the function response. A call past the budget or with bad arguments is refused, and Gemini is told why."""
    name, cid = call["name"], call.get("id")
    raw = dict(call.get("args") or {})
    reason = raw.pop("why", None)  # Gemini's note for the reader (not an engine argument)
    shown = f"{name}({', '.join(f'{k}={json.dumps(v)}' for k, v in raw.items())[:80]})"
    if name not in RUNNERS:
        note_check("analyst", False, "a function call to a tool that doesn't exist", name)
        run.add(actor="engine", kind="refused", tool=name[:40], tone="muted", via="function_call", call_id=cid, call=shown[:100], title=f"Refused {name[:40]}: not one of the tools")
        return {"error": "not one of the tools"}
    if must_write or run.tools >= MAX_TOOL_CALLS:
        why = f"all {MAX_TOOL_CALLS} tool calls this analysis allows are used" if run.tools >= MAX_TOOL_CALLS else "the memo is due now"
        run.add(actor="engine", kind="refused", tool=name, tone="muted", via="function_call", call_id=cid, call=shown, title=f"Not run {name}: {why}")
        return {"error": f"not run: {why}"}
    args, why = _clean_args(run.c, name, raw)
    note_check("analyst", args is not None, f"a function call's arguments: {why}", name)
    if args is None:
        run.add(actor="engine", kind="refused", tool=name, tone="muted", via="function_call", call_id=cid, call=shown, title=f"Refused {name}: {why}")
        return {"error": f"refused: {why}"}
    if any(r["tool"] == name and r["args"] == args for r in run.results):  # the same check again: not run, not counted
        run.add(actor="engine", kind="refused", tool=name, tone="muted", via="function_call", call_id=cid, call=_call_text(name, args),
                title=f"Not run again {name}: already called with these arguments")
        return {"error": "already called with these arguments: its result is in your earlier function response"}
    # Gemini's reason is shown only when every number in it is one the case, the results so far or this call gave
    note = _thought(reason)
    if note and not _numbers_ok(note, run.results + [{"result": run.c.describe()}, {"args": args}]):
        note = None
    run.tools += 1
    res = await run.tool(name, args, "gemini", thought=note, call_id=cid)
    return _fit(for_gemini(name, res))


async def _agent(run: Run) -> tuple[dict | None, str]:
    """Gemini's function-calling loop. (memo that passed the check, "used") or (None, why it stopped).

    contents is the conversation as the API keeps it: the case, then each model reply echoed verbatim (its function
    calls and thought signature), then one user turn answering every call with a functionResponse (same id)."""
    c = run.c
    contents: list[dict] = [{"role": "user", "parts": [{"text": _prompt(c)}]}]
    run.model = AGENT_MODEL
    memo_tries = 0
    note: str | None = None  # a user message before the next call: write now, or the number check's findings
    while run.calls < MAX_GEMINI_CALLS:
        left = run.left()
        if left < 3:
            return None, "slow"
        first = run.calls == 0
        must_write = not first and (run.calls >= MAX_GEMINI_CALLS - 1 or run.tools >= MAX_TOOL_CALLS or memo_tries > 0)
        if must_write and note is None and _responses_last(contents):
            note = WRITE_NOW
        if note:
            contents.append({"role": "user", "parts": [{"text": note}]})
            note = None
        # the first reply must call a tool (ANY: no memo without engine results); then call more or write (VALIDATED:
        # either, with calls held to their declarations); the memo and its rewrite (NONE). Every turn after the first
        # carries the memo's response schema.
        mode = "ANY" if first else ("NONE" if must_write else "VALIDATED")
        try:
            reply, offline = await asyncio.wait_for(
                complete_tools(contents, FUNCTION_DECLARATIONS, system=SYSTEM, fallback=OFFLINE, timeout=min(AI_TIMEOUT_S, left), surface="analyst",
                               model=run.model, thinking=AGENT_THINKING, tool_mode=mode, schema=None if first else MEMO_SCHEMA), left)
        except asyncio.TimeoutError:
            return None, "slow"
        run.calls += 1
        if offline or not isinstance(reply, dict) or reply.get("__offline__"):
            return None, "unavailable"
        run.model = reply.get("model") or run.model
        if reply.get("cache_key"):
            run.cache_keys.append(reply["cache_key"])
        contents.append(reply["content"])  # verbatim: every part, in order, with its thought signature
        calls = reply["calls"]
        if calls:
            run.fcalls += len(calls)
            words = _thought(reply.get("text"))
            call_args = [{"args": {k: v for k, v in (x.get("args") or {}).items() if k != "why"}} for x in calls]
            if words and not _numbers_ok(words, run.results + [{"result": c.describe()}] + call_args):
                words = None  # Gemini's own words are shown only when every number in them is one the engine or the case gave
            if words:
                run.add(actor="gemini", kind="think", tone="info", title=words)
            parts = [function_response(call, await _answer(run, call, must_write)) for call in calls]
            contents.append({"role": "user", "parts": parts})  # one response per call, after all the calls
            continue
        raw = _json_reply(reply.get("text"))
        memo = _memo_from(raw.get("memo")) if isinstance(raw, dict) else None
        thought = _thought(raw.get("thought")) if isinstance(raw, dict) else None
        if thought and not _numbers_ok(thought, run.results + [{"result": c.describe()}]):
            thought = None
        if memo is None:
            if must_write:
                memo_tries += 1
                if memo_tries >= 2:
                    run.forget()
                    return None, "no_memo"
                note = "Your reply had no complete memo. Write the memo now, all five parts, as JSON only: " + MEMO_REPLY
            else:
                note = "Your reply had neither a function call nor a complete memo. Call a tool, or write the memo as JSON only: " + MEMO_REPLY
            continue
        if not run.results:
            note_check("analyst", False, "a memo written before any tool was called")
            run.add(actor="engine", kind="check", tone="over", title="Rejected: no tool was called, so no number can be checked")
            note = "You wrote a memo without calling any tool: every number must come from a tool result. Call the tools first."
            continue
        # a memo (structured output): check every number against the tool results
        memo_tries += 1
        run.add(actor="gemini", kind="memo" if memo_tries == 1 else "revise", tone="info", via="structured_output",
                title="Wrote the memo" if memo_tries == 1 else "Rewrote the memo", detail=thought)
        ok, bad, n, reason = check_memo(memo, c, run.results)
        note_check("analyst", ok, reason or "a memo number that no tool returned", bad[0] if bad else None)
        if ok:
            run.add(actor="engine", kind="check", tone="holds", numbers_checked=n, title=f"Checked {n} {'number' if n == 1 else 'numbers'} in the memo against the tool results: every one matches")
            memo["numbers_checked"] = n
            return memo, "used"
        what = reason or f"{len(bad)} {'number matches' if len(bad) == 1 else 'numbers match'} no tool result ({', '.join(bad[:5])})"
        run.add(actor="engine", kind="check", tone="over", bad=bad[:8], numbers_checked=n, title=f"Checked the memo: {what}")
        if memo_tries >= 2:
            run.forget()
            return None, "rejected"
        note = (f"The engine checked your memo and rejected it: {what}. Rewrite the memo using only numbers that appear in the function responses above, "
                "written in digits" + (", and without that wording." if reason else ".") + " Do not call more tools. Reply with JSON only: " + MEMO_REPLY)
    run.forget()
    return None, "out_of_turns"


async def _backfill(run: Run) -> None:
    """The tools the plain memo needs that the agent didn't call, run by the engine itself (labeled)."""
    for tool, args in PLAIN_PLAN:
        args = {"mw": run.c.mw} if tool == "whatif_size" else args
        have = any(r["tool"] == tool and (tool != "whatif_size" or abs(float(r["args"].get("mw", 0)) - run.c.mw) < 0.05) for r in run.results)
        if not have:
            await run.tool(tool, args, "engine", backfill=True)


WHY = {
    "not_configured": "Gemini is not configured on this server",
    "unavailable": "Gemini unavailable",
    "slow": "Gemini too slow",
    "rejected": "Gemini's memo failed the number check twice",
    "no_memo": "Gemini wrote no usable memo",
    "out_of_turns": "Gemini ran out of turns",
}


async def analyse(c: _Case) -> dict:
    """The whole analysis for a case (the agent when configured, else the fixed plan), with its trace."""
    run = Run(c)
    return await _analyse(run)


async def _analyse(run: Run) -> dict:
    c = run.c
    memo, status = None, "not_configured"
    run.add(actor="engine", kind="case", tone="info",
            title=f"The case: {_n(c.mw)} MW at this location, connecting at {c.sub_name} ({c.kv:g} kV), on the synthetic {c.state_name} model")
    if configured():
        memo, status = await _agent(run)
    if memo is None:
        if status == "not_configured":
            run.add(actor="engine", kind="plain", tone="muted", title="Gemini is not configured here: the plain version runs a fixed plan of five engine checks")
        else:
            run.add(actor="engine", kind="plain", tone="muted", title=f"{WHY.get(status, 'Gemini unavailable')}: the plain memo is built from the engine's results")
        await _backfill(run)
        memo = plain_memo(c, run.results)
        ok, bad, n, _ = check_memo(memo, c, run.results)
        memo["numbers_checked"] = n
        if not ok:  # never expected: the template only prints tool numbers
            log.warning("analyst: the plain memo failed its own check: %s", bad)
        run.add(actor="engine", kind="check", tone="holds" if ok else "over", numbers_checked=n,
                title=f"Plain memo: {n} {'number' if n == 1 else 'numbers'}, each one from a tool result")
    by = "gemini" if status == "used" else "fallback"
    memo["frame"] = FRAME
    return {
        "case": c.describe(),
        "memo": memo,
        "trace": run.trace,
        "tools": [{"tool": r["tool"], "args": r["args"]} for r in run.results],
        "by": by,
        "why": None if by == "gemini" else WHY.get(status, "Gemini unavailable"),
        "verified": True,  # every number in the memo matched a tool result (Gemini's after the check; the plain one by construction)
        "calls": run.calls,
        "tool_calls": len(run.results),
        "function_calls": run.fcalls,  # functionCall parts Gemini returned (native function calling; run or refused)
        "function_calling": bool(run.calls),  # the agent ran on Gemini function calling (False: the fixed plan only)
        "model": (run.model or AGENT_MODEL) if run.calls else None,
        "ms": round((time.perf_counter() - run.t0) * 1000),
        "note": FRAME,
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
    ttl = FALLBACK_TTL_S if (out["by"] == "fallback" and configured()) else CACHE_TTL_S
    with _cache_lock:
        _cache[_ckey(c)] = (time.time(), ttl, copy.deepcopy(out))
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


class _Job:
    def __init__(self, c: _Case):
        self.id = secrets.token_urlsafe(12)
        self.run = Run(c)
        self.status = "running"
        self.result: dict | None = None
        self.error: str | None = None
        self.created = time.monotonic()
        self.task: asyncio.Task | None = None  # kept so the running task is not garbage-collected


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
    c = job.run.c
    try:
        out = await _analyse(job.run)
        _store(c, out)
        job.result = {**out, "cached": False}
        job.status = "done"
    except HTTPException as e:
        job.error = e.detail if isinstance(e.detail, str) else "The analyst could not run this case"
        job.status = "error"
    except Exception:  # noqa: BLE001 - a background job always ends in a state the page can show
        log.exception("analyst job failed")
        job.error = "The analyst failed on this case. Try again."
        job.status = "error"
    finally:
        with _jobs_lock:
            if _by_case.get(_ckey(c)) == job.id:
                del _by_case[_ckey(c)]


async def _whole(c: _Case) -> dict:
    hit = cached(c)
    if hit is not None:
        return hit
    with _jobs_lock:
        jid = _by_case.get(_ckey(c))
        job = _jobs.get(jid) if jid else None
    if job is not None and job.status == "running":  # the same case is already being analysed: wait for it
        while job.status == "running":
            await asyncio.sleep(0.25)
        if job.result is not None:
            return job.result
    out = await analyse(c)
    _store(c, out)
    return {**out, "cached": False}


def _job_view(job: _Job) -> dict:
    out = {"job": job.id, "status": job.status, "trace": list(job.run.trace), "calls": job.run.calls}
    if job.status == "done":
        out["result"] = job.result
    elif job.status == "error":
        out["error"] = job.error
    return out


# ------------------------------------------------------------------------------------ routes
class AnalystIn(BaseModel):
    id: str | None = Field(default=None, pattern=ID_PATTERN)
    region: str | None = None
    lat: float | None = None
    lon: float | None = None
    mw: float | None = None
    live: bool = False


def _case_of(body: AnalystIn) -> _Case:
    if body.id:
        return _case_for_id(body.id)
    if body.lat is None or body.lon is None or body.mw is None:
        raise HTTPException(status_code=422, detail="Give a catalog id, or a case with lat, lon and mw")
    code = region_code(body.region)
    check_site(body.lat, body.lon, body.mw, code)
    return _Case(code, body.lat, body.lon, body.mw)


@router.post("/api/analyst/{proposal_id}")
@limiter.limit("30/minute")
async def analyst_for(request: Request, proposal_id: str = PathParam(..., pattern=ID_PATTERN)):
    """The whole analysis of a catalog proposal (waits for it; cached per case)."""
    c = await run_in_threadpool(_case_for_id, proposal_id)
    return await _whole(c)


@router.post("/api/analyst")
@limiter.limit("30/minute")
async def analyst(request: Request, body: AnalystIn):
    """A catalog id or a case. live=false: the whole analysis. live=true: a finished one from the cache at once
    ({status: done, result}), else a job to poll ({job, status: running, trace})."""
    c = await run_in_threadpool(_case_of, body)
    if not body.live:
        return await _whole(c)
    hit = cached(c)
    if hit is not None:
        return {"job": None, "status": "done", "trace": hit["trace"], "calls": hit["calls"], "result": hit}
    with _jobs_lock:
        _gc()
        jid = _by_case.get(_ckey(c))
        job = _jobs.get(jid) if jid else None
        if job is None or job.status != "running":
            if sum(1 for j in _jobs.values() if j.status == "running") >= RUNNING_MAX:
                raise HTTPException(status_code=429, detail="The analyst is busy with other cases: try again in a moment")
            job = _Job(c)
            _jobs[job.id] = job
            _by_case[_ckey(c)] = job.id
            job.task = asyncio.get_running_loop().create_task(_run_job(job))
    return _job_view(job)


@router.get("/api/analyst/jobs/{job_id}")
@limiter.limit("120/minute")
def analyst_job(request: Request, job_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_-]{8,40}$")):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="This analysis is no longer available: run it again")
    return _job_view(job)
