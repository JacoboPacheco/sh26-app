"""How we know (CLAUDE.md -> Decisions -> DEPTH: "show the engineering"). Three engine checks behind the headline
numbers, each the smallest honest version, fast enough for Render's 0.5 CPU (well under a second of solving on a
desktop; cached by case; nothing is computed at import).

GET  /api/evidence/validation?region=FL
     The engine against the dataset's own solved flows (backend/demo/validation.json, Milestone 0: correlation, median
     and 95th-percentile gap in MW, the share within 2 MW or 5 % of the dataset's flow), plus the same comparison in
     terms that matter for an overload call, computed once per model from its own base case: the gap as a share of
     each line's rating, and on the lines the dataset loads at 80 % or more, the gap in loading points.

POST /api/evidence/n1?auto_fix=true     a case (grid.CaseIn, the what-if / cascade body, plus an optional catastrophe
     `preset` id resolved as the briefing does)
     An N-1 SCREEN of the case's fix: with the fix's ratings, each of the N most loaded lines and transformers near the
     site (the fix's own elements first) is taken out one at a time and the network re-solved (Grid.solve, the
     cascade's own DC power flow; never a dense PTDF). Reported: how many were screened, how many leave a line over
     its rating that was within it with every line in (and how many past 115 %, an assumed emergency rating), how
     many cut off load, and the worst (which line out -> which line over, %). The same outages judged WITHOUT the
     upgrades for contrast: a rating change never moves flow in a DC power flow, so one solve per outage serves both.
     The fix judged, always named by what it is:
       auto_fix=false  the case as it is on screen ("applied": the map has a fix in it, e.g. the results panel's
                       "Run it again with the fix" — a smaller or moved campus, firm service or upgrades); its own
                       upgrades, when it has some, give the "without" contrast
       auto_fix=true   the case's own `upgrades` when it has some ("case"), else Fix it's re-rating (fixit.greedy_fix,
                       the engine's smallest set of higher ratings; "engine", flagged `partial` when lines are still
                       over where the search stopped), else nothing (nothing is over: the case itself is screened)
     "Near the site": an end within 100 km of any campus's substation, else of any substation on the storm's lines.
     A screen of the most loaded lines near the site, not a full N-1 study (the Strengthen page's capacity plan has
     its own statewide screen: capacity.n1).

POST /api/evidence/hours?auto_fix=true  the same case body
     HOW OFTEN it would overload: which load levels (a share of the model's summer peak, 40-140 %, the engine's range)
     put a line or transformer past its rating: a solve every 5 % across the whole range, each change between calm and
     over narrowed by bisection (Grid.solve at each level). The answer is the band that reaches the summer peak: the
     level where it starts, and hours a year at or above it on a COARSE step curve (the heat clock's hours: 3 AM,
     9 AM, 4 PM, heat wave, features/heat/presets.js, as steps, with round hour counts we assumed). An overload that
     only shows at LOW load and is gone by the peak is reported apart and not counted: a one-state model holds its
     flows to the neighbouring states fixed while its own load falls, so low-load overloads there are largely the
     model's cut, not the load (23 state models do this with nothing added). For the case without the fix, with it,
     and today's grid with nothing added. The repo has no hourly load data (no load-duration curve anywhere: costs,
     service_rules, solutions, forecast and timelapse were checked), so the hours are an order of magnitude, labeled
     so, never a forecast.

Every number describes the SYNTHETIC grid model (Breakthrough Energy / Texas A&M), never a real utility's network.
Public like the grid endpoints (no login, nothing stored).
"""

import json
import math
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from fixit import greedy_fix
from grid import DEFAULT_REGION, LOAD_FACTOR_MAX, LOAD_FACTOR_MIN, CaseIn, _case_header, check_case, grid_at, region_code
from limiter import limiter
from powerflow import OVER_PCT

router = APIRouter(tags=["evidence"])

DEMO = Path(__file__).parent / "demo"
GRID_SOURCE = {
    "name": "Breakthrough Energy Sciences U.S. Test System (PowerSimData usa_tamu), from Texas A&M's ACTIVSg synthetic grids (CC BY 4.0)",
    "url": "https://zenodo.org/records/3530898",
}
SYNTHETIC = "A synthetic grid model (Breakthrough Energy / Texas A&M): shaped like the real grid, not any utility's network."

# ---------------------------------------------------------------------------------- 1. validation
try:
    _VALIDATION = json.loads((DEMO / "validation.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    _VALIDATION = None
REF_MIN_MW = 1.0  # as validate.py: branches the dataset gives under 1 MW are left out
NEAR_LIMIT_PCT = 80.0  # "lines near their limit": the dataset loads them at this share of their rating or more
_gap_cache: dict[str, dict] = {}


def _rating_gap(code: str) -> dict:
    """The validation's comparison as a share of each line's rating (what an overload call depends on), from the loaded
    model's own base case (the same flows validate.py compares). Once per region."""
    hit = _gap_cache.get(code)
    if hit is not None:
        return hit
    g = grid_at(1.0, code)
    ours, ref, rate = g.base.flow, g.pf_ref, g.rate
    mask = (np.abs(ref) > REF_MIN_MW) & (rate > 0)
    gap = np.abs(ours[mask] - ref[mask]) / rate[mask] * 100.0  # MW gap in % of the line's rating
    ld_ref = np.abs(ref[mask]) / rate[mask] * 100.0
    ld_ours = np.abs(ours[mask]) / rate[mask] * 100.0
    near = ld_ref >= NEAR_LIMIT_PCT
    pp = np.abs(ld_ours - ld_ref)[near]  # loading points apart, on the lines near their limit
    out = {
        "compared": int(mask.sum()),
        "median_gap_pct_of_rating": round(float(np.median(gap)), 2) if len(gap) else None,
        "p95_gap_pct_of_rating": round(float(np.percentile(gap, 95)), 1) if len(gap) else None,
        "within_5pct_of_rating_share": round(float((gap <= 5.0).mean()), 4) if len(gap) else None,
        "near_limit": {
            "threshold_pct": NEAR_LIMIT_PCT,
            "lines": int(near.sum()),
            "median_pp": round(float(np.median(pp)), 1) if len(pp) else None,
            "max_pp": round(float(pp.max()), 1) if len(pp) else None,
        },
    }
    _gap_cache[code] = out
    return out


def validation_for(code: str) -> dict:
    v = _VALIDATION
    if not isinstance(v, dict) or code not in (v.get("states") or {}):
        raise HTTPException(status_code=404, detail="The validation file is not built on this server (backend/demo/validate.py --all)")
    row = v["states"][code]
    gap = _rating_gap(code)
    name = row.get("name", code)

    def share(x: float) -> str:  # 0.9997 -> "99.97 %" (never rounded up to 100)
        return f"{x * 100:.2f} %" if x >= 0.995 else f"{x * 100:.1f} %"

    sentence = (
        f"{name}: our DC power flow reproduces the dataset's own solved flows with a correlation of {row['corr']:.4f} across "
        f"{row['compared']:,} lines; the median gap is {row['median_abs_err_mw']:g} MW and 95 % of lines are within "
        f"{row['p95_abs_err_mw']:g} MW."
    )
    if gap["compared"] and gap["within_5pct_of_rating_share"] is not None:  # a model with no comparable line has no share
        sentence += f" Measured against each line's rating, {share(gap['within_5pct_of_rating_share'])} are within 5 %" + (
            f"; on the {gap['near_limit']['lines']:,} lines the dataset loads at {NEAR_LIMIT_PCT:.0f} % or more, the median gap is "
            f"{gap['near_limit']['median_pp']:.1f} points of loading."
            if gap["near_limit"]["lines"]
            else "."
        )
    return {
        "region": code,
        "region_name": name,
        "state": row,
        "rating_gap": gap,
        "summary": v.get("summary"),
        "threshold": v.get("threshold"),
        "generated": v.get("generated"),
        "method": v.get("method"),
        "reference": v.get("reference"),
        "limits": v.get("limits"),
        "rating_gap_method": (
            "The same comparison as a share of each line's rating: |our flow - the dataset's flow| / the line's rating, the error that "
            f"matters when a line is called over its limit. Lines near their limit: the dataset loads them at {NEAR_LIMIT_PCT:.0f} % of their "
            "rating or more; the gap there is in points of loading (for example 96 % against 94 % is 2 points)."
        ),
        "sentence": sentence,
        "label": "Validation: our DC power flow against the dataset's own solved base case, branch by branch",
        "source": GRID_SOURCE,
        "synthetic": True,
    }


# ---------------------------------------------------------------------------------- shared: the case and its fix
CACHE_SIZE = 64
_fix_cache: "OrderedDict[str, dict]" = OrderedDict()
_n1_cache: "OrderedDict[str, dict]" = OrderedDict()
_hours_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


class EvidenceIn(CaseIn):
    """The what-if / cascade body, plus a catastrophe preset id (catastrophes.json) as the briefing takes it: the
    incident solution stage posts `preset` instead of the storm's line ids."""

    preset: str | None = None


def _key(body: CaseIn, auto_fix: bool = True) -> str:
    return json.dumps({**body.model_dump(), "auto_fix": bool(auto_fix)}, sort_keys=True, default=str)


def _remember(cache: OrderedDict, key: str, value):
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > CACHE_SIZE:
            cache.popitem(last=False)
    return value


def _cached(cache: OrderedDict, key: str):
    with _lock:
        v = cache.get(key)
        if v is not None:
            cache.move_to_end(key)
        return v


def _preset(body: EvidenceIn):
    """A catastrophe preset expanded exactly as the briefing expands it (briefing.build_case): its load level, its
    campus when the case has none, and its corridor's line ids as the trip (up to 2,500, past check_case's own cap, so
    they are set after it). (body to check, preset trip or None, preset name or None)."""
    if not body.preset:
        return body, None, None
    import briefing  # noqa: PLC0415 (lazy: only preset cases need its presets and corridor)

    p = briefing.PRESETS.get(str(body.preset).strip())
    if p is None:
        raise HTTPException(status_code=422, detail=f"Unknown catastrophe preset {body.preset!r}")
    if p["region"] != region_code(body.region):
        raise HTTPException(status_code=422, detail="That catastrophe preset is for another state")
    upd = {"trip": [], "load_factor": float(p["load_factor"])}
    if body.lat is None and body.lon is None and body.mw is None and not body.sites and p.get("campus"):
        c = p["campus"]
        upd.update({"lat": c["lat"], "lon": c["lon"], "mw": c["mw"]})
    return body.model_copy(update=upd), briefing.preset_trip(p), p.get("name")


def _case(body: EvidenceIn):
    """(grid at the case's level, sites, trip, upgrades, extra load, header, active mask). 422 when there is nothing to check."""
    body, preset_trip, preset_name = _preset(body)
    g, sites, trip, upgrades = check_case(body)
    if preset_trip is not None:
        trip = list(preset_trip)
    if not sites and not trip and abs(g.load_factor - 1.0) < 1e-9:
        raise HTTPException(status_code=422, detail="Nothing to check yet: drop a data center, draw a storm or change the time of day")
    extra, header = _case_header(g, sites, trip, upgrades)
    header["preset_name"] = preset_name
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[bid]] = False
    return g, sites, trip, upgrades, extra, header, active


def _fix(key: str, g, active: np.ndarray, extra: np.ndarray, upgrades: dict[int, float], auto_fix: bool) -> dict:
    """The fix the checks judge, named by what it is: {fix: {branch id: MVA}, source, still_over}.
      "applied"  auto_fix off: the case as it is on screen already has its fix in it (its own upgrades, if any)
      "case"     the case's own upgrades
      "engine"   Fix it's re-rating (fixit.greedy_fix at the case's level); `still_over` counts the lines left past
                 their rating where the search stopped (round or upgrade limit, or a line past 5x its rating)
      "none"     nothing is over with every line in: the case itself is checked"""
    ups = {int(k): float(v) for k, v in upgrades.items()}
    if not auto_fix:
        return {"fix": ups, "source": "applied", "still_over": None}
    if ups:
        return {"fix": ups, "source": "case", "still_over": None}
    hit = _cached(_fix_cache, key)
    if hit is not None:
        return hit
    _, final, rate, chosen, _, _, _ = greedy_fix(g, active, extra, g.rate.copy())
    fix = {int(g.br_ids[i]): float(rate[i]) for i in chosen}
    still = int((final.active & (final.loading_pct > OVER_PCT + 1e-6)).sum()) if fix else 0
    return _remember(_fix_cache, key, {"fix": fix, "source": "engine" if fix else "none", "still_over": still})


def _title(s: str) -> str:
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), str(s).strip().lower())


def _brief(g, i: int) -> dict:
    """A line or transformer in plain words (synthetic substation names, after the towns they sit in)."""
    fs, ts = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
    xf = fs == ts
    name = f"Transformer at {_title(g.sub_name[fs])}" if xf else f"{_title(g.sub_name[fs])} – {_title(g.sub_name[ts])}"
    a, b = sorted((round(float(g.bus_kv[g.f[i]])), round(float(g.bus_kv[g.t[i]]))), reverse=True)
    return {
        "id": int(g.br_ids[i]),
        "name": name,
        "kind": "transformer" if xf else "line",
        "kv": round(float(g.br_kv[i])),
        "kv_text": f"{a}/{b} kV" if a != b else f"{a} kV",
        "from": int(g.sub_ids[fs]),
        "to": int(g.sub_ids[ts]),
        # the build step made this rating up (the dataset gives none, or one below its own flow): build_grid.py's flag
        "rating_estimated": bool(g._data["branches"][i].get("rate_est")),
    }


def _pair(g, k: int, j: int, pct: float) -> dict:
    """One outage and the line it overloads most: {out, over, pct}. Two transformers at one substation share a name, so
    the second reads "another transformer at ..." (a parallel line: "a parallel ... line")."""
    out, over = _brief(g, k), _brief(g, j)
    if over["name"] == out["name"]:
        over["name"] = ("another transformer" + over["name"][len("Transformer"):]) if over["kind"] == "transformer" else f"a parallel {over['name']} line"
    return {"out": out, "over": over, "pct": round(float(pct), 1)}


def _labels(fx: dict) -> tuple[str, str, str | None]:
    """(key of the judged variant, its label, the label of the same case without the upgrades or None)."""
    src, has = fx["source"], bool(fx["fix"])
    if src == "applied":
        return ("with_fix", "With the fix applied", "Without its upgrades") if has else ("case", "With the fix applied", None)
    if src == "case":
        return "with_fix", "With its upgrades", "Without its upgrades"
    if src == "engine":
        return "with_fix", "With Fix it's partial re-rating" if fx["still_over"] else "With Fix it's re-rating", "As it is"
    return "case", "As it is", None


def _fix_fields(g, fx: dict, firm: bool = False) -> dict:
    fix, source, still = fx["fix"], fx["source"], fx["still_over"]
    items = []
    for bid, new in fix.items():
        i = g.br_index[bid]
        items.append({**_brief(g, i), "from_mva": round(float(g.rate[i])), "to_mva": round(float(new))})
    if source == "applied":
        words = "the case as it is on screen, with the fix applied on the map" + (
            ", with these upgrades"
            if items
            else " (firm service: it decides who is cut when a line trips, not the steady flows)"
            if firm
            else " (a change to the campus or how it is served, no upgrades)"
        )
    elif source == "case":
        words = "the upgrades on this case"
    elif source == "engine" and still:
        words = (
            f"Fix it's partial re-rating, with {_plural(still, 'line', 'lines')} still past {'its' if still == 1 else 'their'} rating where the search "
            "stopped (it raises ratings up to 5x; the rest would need new lines). It is not the plan the results panel offers"
        )
    elif source == "engine":
        words = (
            "Fix it's re-rating, the smallest set of higher ratings the engine finds that clears every overload with every line in service. "
            "It is sized for normal operation, not for outages, and it is not the plan the results panel offers"
        )
    else:
        words = "no fix: nothing is over its rating with every line in, so the case itself is checked"
    key, with_label, without_label = _labels(fx)
    return {
        "source": source,
        "words": words,
        "count": len(items),
        "elements": items[:12],
        "partial": bool(still),
        "still_over": still,
        "label": with_label,
        "without_label": without_label,
    }


def _km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def _sub_km(g, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Per substation, km to the nearest of the given points (in chunks: never a points x substations matrix at once)."""
    out = np.full(len(g.sub_lat), np.inf)
    for s in range(0, len(lat), 256):
        d = _km(lat[s : s + 256, None], lon[s : s + 256, None], g.sub_lat[None, :], g.sub_lon[None, :])
        out = np.minimum(out, d.min(axis=0))
    return out


def _near(g, header: dict, trip: list[int]) -> tuple[np.ndarray | None, dict | None]:
    """Where "near the site" is measured from, and each branch's distance (km, its nearer end) to it: any campus's
    substation; else any substation on the storm's lines (the whole corridor, not its middle); else nowhere (a heat
    wave alone: the whole state). (per-branch km or None, {what, where})."""
    sites = [s for s in header.get("sites") or [] if s.get("sub_lat") is not None]
    if sites:
        lat = np.array([s["sub_lat"] for s in sites], dtype=float)
        lon = np.array([s["sub_lon"] for s in sites], dtype=float)
        if len(sites) == 1:
            what, where = f"the campus's substation ({_title(header.get('sub_name') or '')})", "near the site"
        else:
            what, where = f"any of the {len(sites)} campuses' substations", "near the campuses"
    elif trip:
        idx = np.array([g.br_index[b] for b in trip], dtype=int)
        subs = np.unique(np.concatenate([g.bus_sub_idx[g.f[idx]], g.bus_sub_idx[g.t[idx]]]))
        lat, lon = g.sub_lat[subs], g.sub_lon[subs]
        what, where = f"any of the {len(subs):,} substations on the storm's lines", "near the storm's lines"
    else:
        return None, None
    dsub = _sub_km(g, lat, lon)
    d = np.minimum(dsub[g.bus_sub_idx[g.f]], dsub[g.bus_sub_idx[g.t]])
    return d, {"what": what, "where": where, "lat": round(float(lat[0]), 4) if len(sites) == 1 else None, "lon": round(float(lon[0]), 4) if len(sites) == 1 else None}


# ---------------------------------------------------------------------------------- 2. the N-1 screen
N1_K = 30  # contingencies screened at most
NEAR_KM = 100.0  # "near the site": a line or transformer with an end within this distance
N1_TIME_S = 2.0  # solving time at most (Render's 0.5 CPU: each solve is a few times slower than a desktop's ~3 ms)
N1_MIN_LOOP_S = 0.8  # the outages always get at least this, however long the fix search took
EMERGENCY_PCT = 115.0  # an assumed emergency rating (the dataset gives one rating), as the Strengthen page's N-1 screen
MIN_FLOW_MW = 1.0  # a line carrying less than this is not a meaningful outage to screen


def _variant(g, st0, rate: np.ndarray, key: str, label: str, sol: str) -> dict:
    """One way of judging the outages: the case's flows (`sol` "case") or today's grid with nothing added ("alone"),
    against a set of ratings."""
    ld = np.abs(st0.flow) / rate * 100.0
    over0 = st0.active & (ld > OVER_PCT + 1e-6)
    j = int(np.argmax(np.where(st0.active, ld, -1.0)))
    return {
        "key": key,
        "label": label,
        "sol": sol,
        "rate": rate,
        "over0": over0,
        "over_before": int(over0.sum()),
        "worst_before": {"branch": _brief(g, j), "pct": round(float(ld[j]), 1)},
        "fails": 0,  # outages that push a line past its rating that was within it with every line in
        "fails_any": 0,  # outages after which any line is past its rating (without the fix: all of them when lines are over already)
        "fails_emergency": 0,
        "worst": None,
        "examples": [],
    }


def n1_screen(body: EvidenceIn, auto_fix: bool = True) -> dict:
    key = _key(body, auto_fix)
    hit = _cached(_n1_cache, key)
    if hit is not None:
        return hit
    t0 = time.perf_counter()
    g, sites, trip, upgrades, extra, header, active = _case(body)
    fx = _fix(key, g, active, extra, upgrades, auto_fix)
    fix = fx["fix"]
    main_key, main_label, without_label = _labels(fx)
    rate_fix = g.rates_with(fix)
    st0 = g.solve(active, extra, rate_fix)
    variants = [_variant(g, st0, rate_fix, main_key, main_label, "case")]
    if without_label:
        variants.append(_variant(g, st0, g.rate, "without_fix", without_label, "case"))
    # the weakness already in today's grid: the same outages with nothing added (no campus, no storm) at this hour
    alone = bool(sites or trip)
    full = np.ones(g.m, dtype=bool)
    zero = np.zeros(g.n)
    if alone:
        variants.append(_variant(g, g.base, g.rate, "grid_alone", "Today's grid, nothing added", "alone"))

    # the outages to screen: the fix's own elements first (what if the upgraded line itself fails?), then the lines and
    # transformers near the site that carry the most power for their (original) rating
    dist, center = _near(g, header, trip)
    flow0 = np.abs(st0.flow)
    carrying = active & (flow0 >= MIN_FLOW_MW)
    cand = carrying & (dist <= NEAR_KM) if dist is not None else carrying
    if not cand.any() and trip and sites:  # the storm has taken out every line near the campus: screen along the storm
        dist, center = _near(g, {"sites": []}, trip)
        cand = carrying & (dist <= NEAR_KM)
        center["campus_dark"] = True
    near = int(cand.sum())
    first = [g.br_index[b] for b in fix if cand[g.br_index[b]]]
    seen = set(first)
    rest = [int(i) for i in np.flatnonzero(cand)[np.argsort(-(flow0 / g.rate)[cand], kind="stable")] if int(i) not in seen]
    order = (first + rest)[:N1_K]

    lost0, extra0 = st0.lost_mw, st0.lost_extra_mw
    # the outage loop's own budget: what is left of N1_TIME_S after the case and its fix, never under N1_MIN_LOOP_S
    t_loop, budget = time.perf_counter(), max(N1_TIME_S - (time.perf_counter() - t0), N1_MIN_LOOP_S)
    screened, capped = 0, False
    strands: list[dict] = []
    for k in order:
        if time.perf_counter() - t_loop > budget:
            capped = True
            break
        a = active.copy()
        a[k] = False
        st = g.solve(a, extra, rate_fix)  # the cascade's own DC power flow, one outage
        sols = {"case": (np.abs(st.flow), a)}
        if alone:
            b = full.copy()
            b[k] = False
            sols["alone"] = (np.abs(g.solve(b, zero).flow), b)
        screened += 1
        cut = st.lost_mw - lost0
        if cut > 0.5:
            strands.append({"out": _brief(g, k), "lost_mw": round(float(cut), 1), "campus": bool(st.lost_extra_mw - extra0 > 0.5)})
        for v in variants:
            fl, live = sols[v["sol"]]
            ld = fl / v["rate"] * 100.0
            j = int(np.argmax(np.where(live, ld, -1.0)))
            over = live & (ld > OVER_PCT + 1e-6)
            new = over & ~v["over0"]
            if over.any():
                v["fails_any"] += 1
            if new.any():
                v["fails"] += 1
                jn = int(np.flatnonzero(new)[np.argmax(ld[new])])
                if len(v["examples"]) < 3:
                    v["examples"].append(_pair(g, k, jn, ld[jn]))
                if (ld[new] > EMERGENCY_PCT + 1e-6).any():
                    v["fails_emergency"] += 1
            if v["worst"] is None or ld[j] > v["worst"]["pct"]:
                v["worst"] = {**_pair(g, k, j, ld[j]), "new": bool(new[j])}

    for v in variants:
        v.pop("rate")
        v.pop("over0")
        v.pop("sol")
        v["holds"] = screened - v["fails_any"]
    n = screened
    where = center["where"] if center else "in the state"
    label = f"N-1 screen: the {n} most loaded lines {where}, one at a time, DC power flow on the synthetic model"
    judged = {
        "applied": "With the case's own ratings (the fix applied on the map)",
        "case": "With the case's upgrades",
        "engine": "With Fix it's re-rating",
        "none": "With today's ratings",
    }[fx["source"]]
    out = {
        "region": region_code(body.region),
        "load_factor": g.load_factor,
        "preset": header.get("preset_name"),
        "label": label,
        "screened": n,
        "asked": N1_K,
        "near": near,
        "radius_km": NEAR_KM if center else None,
        "center": center,
        "time_capped": capped,
        "fix": _fix_fields(g, fx, body.firm),
        "variants": variants,
        "strands": {"count": len(strands), "examples": strands[:3]},
        # a storm can cut the campus off before anything else: then its load adds no flow to screen
        "campus_cut_off": bool(sites) and float(st0.lost_extra_mw) > 0.5,
        "emergency_pct": EMERGENCY_PCT,
        "seconds": round(time.perf_counter() - t0, 2),
        "method": (
            f"{judged}, each of up to {N1_K} lines and transformers is taken out on its own and the network is re-solved with "
            "the same DC power flow the cascade uses: the upgraded elements first, then the ones "
            + (f"with an end within {NEAR_KM:.0f} km of {center['what']}" if center else "in the state")
            + " that carry the most power for their rating. An outage fails the screen when it pushes a line or transformer past its "
            "rating."
            + (
                " The same outages are judged against today's ratings without the upgrades (a rating change does not move flow in a DC "
                "power flow)."
                if without_label
                else ""
            )
            + (" They are also re-solved on today's grid with nothing added, to show the weakness that is already there." if alone else "")
            + f" Judged at the normal rating; {EMERGENCY_PCT:.0f} % stands in for a short-term emergency rating, which the dataset does not give. "
            "Outages that cut off load are counted apart."
            + (f" It stopped after {n} outages to keep the answer quick." if capped else "")
        ),
        "limits": (
            "A screen of the most loaded lines near the site, not a full N-1 study: one outage at a time, steady state, no voltages, "
            "and nothing the operator would do in between. Some of the model's ratings were estimated when it was built (the dataset gives "
            "none for them); those are marked. " + SYNTHETIC
        ),
        "source": GRID_SOURCE,
        "synthetic": True,
    }
    out["sentence"] = _n1_sentence(out)
    return _remember(_n1_cache, key, out)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _lower(label: str) -> str:
    return label[0].lower() + label[1:] if label and not label.startswith("Fix it") else label


def _n1_sentence(r: dict) -> str:
    n = r["screened"]
    vs = {v["key"]: v for v in r["variants"]}
    main = vs.get("with_fix") or vs.get("case")
    who = main["label"]
    if not n:
        return f"No line {r['center']['where'] if r.get('center') else 'in the state'} carries enough power to screen."
    f = main["fails_any"]
    if f == 0:
        s = f"{who}, none of the {n} single outages screened pushes a line past its rating"
    else:
        s = f"{who}, {f:,} of the {n} single outages screened {'pushes' if f == 1 else 'push'} a line past its rating"
    if main["over_before"]:
        s += f" ({_plural(main['over_before'], 'line is', 'lines are')} over before anything fails)"
    no = vs.get("without_fix")
    if no:
        if no["over_before"] and no["fails_any"] == n:
            s += f"; {_lower(no['label'])}, {_plural(no['over_before'], 'line is', 'lines are')} over before anything fails, so every one of them leaves an overload."
        else:
            s += f"; {_lower(no['label'])}, {no['fails_any']:,} of the {n} {'does' if no['fails_any'] == 1 else 'do'}."
    else:
        s += "."
    al = vs.get("grid_alone")
    if al:
        s += (
            f" Today's grid with nothing added: {al['fails_any']:,} of the same {n} already {'does' if al['fails_any'] == 1 else 'do'}."
            if al["fails_any"]
            else f" Today's grid with nothing added rides through all {n}."
        )
    if r.get("campus_cut_off"):
        s += " The storm has already cut the campus off, so its load adds no flow here" + (
            "; no line near it carries power, so the screen follows the storm's lines." if (r.get("center") or {}).get("campus_dark") else "."
        )
    c = r["strands"]["count"]
    if c:
        s += f" {_plural(c, 'outage cuts', 'outages cut')} off load on {'its' if c == 1 else 'their'} own (the only path to it)."
    return s


# ---------------------------------------------------------------------------------- 3. hours a year
SCAN_STEP = 0.05
BISECT = 6  # the first failing step narrowed to under 0.1 % of the peak
# The coarse step curve: (share of the summer peak, hours a year at or above it, what the step stands for). The shares
# are the heat clock's hours (features/heat/presets.js); the hour counts are OUR round assumptions for a summer-peaking
# grid, not measured: the model has no hourly load, so these give an order of magnitude only.
STEP_CURVE = [
    (0.40, 8760, "the engine's lowest level: every hour of the year"),
    (0.62, 4000, "the 3 AM level: a bit under half the year's hours"),
    (0.82, 1200, "the 9 AM level: summer days from mid-morning into the evening"),
    (1.00, 100, "the 4 PM summer peak: the hottest afternoons"),
    (1.04, 20, "a heat wave: a few afternoons a year"),
]
CURVE_LABEL = (
    "Coarse: the heat clock's four hours used as steps (3 AM = 62 %, 9 AM = 82 %, 4 PM = 100 %, a heat wave = 104 % of the summer peak), "
    "with round hour counts we assumed for a summer-peaking grid. The model has no hourly load data, so this is an order of magnitude, "
    "not a forecast; the same curve is used for every state."
)


def _sig2(x: float) -> int:
    if x <= 0:
        return 0
    p = 10 ** max(0, int(math.floor(math.log10(x))) - 1)
    return int(round(x / p) * p)


def hours_at(level: float | None) -> dict:
    """Hours a year at or above `level` (a share of the summer peak) on the coarse step curve: {about, low, high, words}."""
    top = STEP_CURVE[-1]
    if level is None:
        return {"about": 0, "low": 0, "high": 0, "words": f"never, up to {LOAD_FACTOR_MAX * 100:.0f} % of the summer peak (the engine's range)"}
    if level <= STEP_CURVE[0][0] + 1e-9:
        return {"about": 8760, "low": 8760, "high": 8760, "words": "every hour of the year"}
    if level > top[0] + 1e-9:
        return {"about": None, "low": 0, "high": top[1], "words": f"fewer than about {top[1]} hours a year: only in a heat wave past the heat clock's"}
    for (la, ha, _), (lb, hb, _) in zip(STEP_CURVE, STEP_CURVE[1:]):
        if la < level <= lb + 1e-9:
            if abs(level - lb) < 1e-9:
                about = hb
            else:  # between two steps: log-linear, rounded to two figures (the range is the honest answer)
                about = _sig2(math.exp(np.interp(level, [la, lb], [math.log(ha), math.log(hb)])))
            words = f"about {about:,} hours a year" + (f" (between {hb:,} and {ha:,} on the coarse curve)" if about not in (ha, hb) else "")
            return {"about": about, "low": hb, "high": ha, "words": words}
    return {"about": None, "low": 0, "high": top[1], "words": f"fewer than about {top[1]} hours a year"}


def _hours_above(level: float) -> float:
    """Hours a year at or above `level` on the coarse curve, unrounded (hours_at's interpolation); 0 past its top step."""
    if level <= STEP_CURVE[0][0] + 1e-9:
        return float(STEP_CURVE[0][1])
    if level > STEP_CURVE[-1][0] + 1e-9:
        return 0.0
    return float(math.exp(np.interp(level, [s[0] for s in STEP_CURVE], [math.log(s[1]) for s in STEP_CURVE])))


def _over_at(g1, active: np.ndarray, extra_case: np.ndarray, rate: np.ndarray, level: float) -> bool:
    """Is any line or transformer past its rating with every existing load at `level` x its peak value? The same solve
    as grid.grid_at(level) (its loads are the base loads x the level) without adding a model to the shared cache."""
    extra = g1.pd * (level - 1.0) + extra_case
    st = g1.solve(active, extra, rate)
    return bool((st.active & (st.loading_pct > OVER_PCT + 1e-6)).any())


PEAK = 1.0
LEVELS = [float(x) for x in np.round(np.arange(LOAD_FACTOR_MIN, LOAD_FACTOR_MAX + 1e-9, SCAN_STEP), 2)]


def over_bands(g1, active: np.ndarray, extra_case: np.ndarray, rate: np.ndarray, memo: dict | None = None) -> tuple[list[tuple[float, float]], int]:
    """Every band of load levels (shares of the summer peak) where something is past its rating: a solve at every
    SCAN_STEP across the engine's range, each change between calm and over narrowed by bisection (under 0.1 %); a band
    that reaches an end of the range keeps that end. ([(lowest level over, highest level over)], solves).
    `memo` {level: (|flow|, live)} shares solves between ratings of the same case: a rating never moves flow in a DC
    power flow, so the case with and without its upgrades is one set of solves judged twice."""
    solves = 0
    memo = {} if memo is None else memo

    def over(lv: float) -> bool:
        nonlocal solves
        hit = memo.get(lv)
        if hit is None:
            solves += 1
            st = g1.solve(active, g1.pd * (lv - 1.0) + extra_case)  # grid_at(lv)'s loads, as _over_at
            hit = memo[lv] = (np.abs(st.flow), st.active)
        fl, live = hit
        return bool((live & (fl / rate * 100.0 > OVER_PCT + 1e-6)).any())

    def edge(calm: float, hot: float) -> float:  # the over side of the change, within |calm - hot| / 2**BISECT
        for _ in range(BISECT):
            mid = (calm + hot) / 2
            if over(mid):
                hot = mid
            else:
                calm = mid
        return hot

    flags = [over(lv) for lv in LEVELS]
    bands, i, last = [], 0, len(LEVELS) - 1
    while i <= last:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < last and flags[j + 1]:
            j += 1
        lo = LEVELS[0] if i == 0 else edge(LEVELS[i - 1], LEVELS[i])
        hi = LEVELS[-1] if j == last else edge(LEVELS[j + 1], LEVELS[j])
        bands.append((lo, hi))
        i = j + 1
    return bands, solves


def _pct(level: float | None) -> float | None:
    return None if level is None else round(level * 100, 1)


def read_bands(bands: list[tuple[float, float]]) -> dict:
    """What the bands say about how often. Every band counts (hours a year the load is inside it, on the coarse curve)
    except a LOW-LOAD band: one that starts at the bottom of the range and is calm again before the summer peak. That
    one is reported apart and never counted: a one-state model holds its flows to the neighbouring states fixed while
    its own load falls, so low-load overloads there are largely the model's cut (23 state models show one with nothing
    added). `level` is where the lowest counted band starts: "overloads at or above" when it is the only one and runs
    to the top of the range, the usual case."""
    low = next((b for b in bands if b[0] <= LOAD_FACTOR_MIN + 1e-9 and b[1] < PEAK - 1e-9), None)
    counted = [b for b in bands if b is not low]
    level = counted[0][0] if counted else None
    to_top = lambda b: b[1] >= LOAD_FACTOR_MAX - 1e-9  # noqa: E731
    if not counted:
        hours = hours_at(None) if not bands else {
            "about": 0, "low": 0, "high": 0,
            "words": f"none counted: within its ratings from {_pct(low[1]):g} % of the summer peak up to {LOAD_FACTOR_MAX * 100:.0f} %",
        }
    elif len(counted) == 1 and to_top(counted[0]):
        hours = hours_at(level)  # at or above one level: the curve's own steps bracket it
    else:  # inside each band: at or above its start, less at or above its end
        about = _sig2(sum(_hours_above(a) - (0.0 if to_top((a, b)) else _hours_above(b)) for a, b in counted))
        hours = {"about": about, "low": about, "high": about, "words": f"about {about:,} hours a year inside {'that band' if len(counted) == 1 else 'those bands'}"}
    return {
        "level": None if level is None else round(level, 4),
        "pct": _pct(level),
        "hours": hours,
        "bands": [[_pct(a), _pct(b)] for a, b in bands],
        "counted": [[_pct(a), _pct(b)] for a, b in counted],
        "calm_from_pct": _pct(low[1]) if low and not counted else None,
        "low_to_pct": _pct(low[1]) if low else None,
    }


_alone_cache: dict[str, tuple[list[tuple[float, float]], int]] = {}


def hours_check(body: EvidenceIn, auto_fix: bool = True) -> dict:
    key = _key(body, auto_fix)
    hit = _cached(_hours_cache, key)
    if hit is not None:
        return hit
    t0 = time.perf_counter()
    g, sites, trip, upgrades, extra, header, active = _case(body)
    fx = _fix(key, g, active, extra, upgrades, auto_fix)
    main_key, main_label, without_label = _labels(fx)
    code = region_code(body.region)
    g1 = grid_at(1.0, code)  # the campus's MW per bus: every level of a state's model shares its bus numbering
    lf = float(g.load_factor)
    rows = []

    memo: dict = {}  # the case's solves by level, shared by its two sets of ratings

    def add(key_: str, label: str, rate: np.ndarray, act: np.ndarray, ext: np.ndarray, found=None) -> None:
        bands, n = found if found is not None else over_bands(g1, act, ext, rate, memo)
        rows.append({"key": key_, "label": label, **read_bands(bands), "solves": n, "at_case": _over_at(g1, act, ext, rate, lf)})

    if without_label:
        add("without_fix", without_label, g1.rate, active, extra)
    add(main_key, main_label, g1.rates_with(fx["fix"]), active, extra)
    full, zero = np.ones(g1.m, dtype=bool), np.zeros(g1.n)
    alone = _alone_cache.get(code)
    if alone is None:
        alone = _alone_cache[code] = over_bands(g1, full, zero, g1.rate)
    add("grid_alone", "Today's grid, nothing added", g1.rate, full, zero, alone)

    out = {
        "region": code,
        "preset": header.get("preset_name"),
        "case_level": lf,
        "case_pct": round(lf * 100, 1),
        "variants": rows,
        "fix": _fix_fields(g, fx, body.firm),
        "curve": [{"level": lv_, "pct": round(lv_ * 100), "hours": h, "what": w} for lv_, h, w in STEP_CURVE],
        "curve_label": CURVE_LABEL,
        "label": "How often: the load levels at which a line goes past its rating, then hours a year on a coarse curve",
        "method": (
            f"Every existing load in the model is scaled together, from {LOAD_FACTOR_MIN * 100:.0f} % to {LOAD_FACTOR_MAX * 100:.0f} % of its summer "
            f"peak (the heat clock's scale), solved every {SCAN_STEP * 100:.0f} % with the same DC power flow, and each change between calm and over "
            "narrowed by bisection (to under 0.1 %). The campus stays at full size at every level; generation re-dispatches to match. The answer is "
            "the band that is over at the summer peak: where it starts, and the hours a year the load is at or above that level, read off a coarse "
            "step curve. An overload seen only at low load, gone by the peak, is shown apart and not counted: a one-state model holds its flows to "
            "the neighbouring states fixed while its own load falls, so it overstates low-load flows on those ties."
        ),
        "limits": "Steady state at each level; real load does not scale evenly across a state, and the hour counts are assumed. " + SYNTHETIC,
        "source": GRID_SOURCE,
        "seconds": round(time.perf_counter() - t0, 2),
        "synthetic": True,
        "estimate": True,
    }
    out["sentence"] = _hours_sentence(out)
    return _remember(_hours_cache, key, out)


def _ranges(counted: list) -> str:
    """"between 68.4 and 77.3 % and from 100.3 %" (percentages of the summer peak)."""
    top = LOAD_FACTOR_MAX * 100 - 1e-6
    return " and ".join(f"from {a:g} %" if b >= top else f"between {a:g} and {b:g} %" for a, b in counted)


def _hours_part(v: dict) -> str:
    c = v["counted"]
    if not c:
        if v["calm_from_pct"] is not None:
            return f"it stays within its ratings from {v['calm_from_pct']:g} % of the summer peak up to {LOAD_FACTOR_MAX * 100:.0f} %: no hours counted"
        return f"it stays within its ratings up to {LOAD_FACTOR_MAX * 100:.0f} % of the summer peak: never on this curve"
    if len(c) == 1 and c[0][1] >= LOAD_FACTOR_MAX * 100 - 1e-6:
        if v["level"] <= LOAD_FACTOR_MIN + 1e-9:
            return f"it is past a rating at every level from {LOAD_FACTOR_MIN * 100:.0f} % of the summer peak up: {v['hours']['words']}"
        return f"it overloads at or above {v['pct']:g} % of the summer peak: {v['hours']['words']}"
    return f"it is past a rating {_ranges(c)} of the summer peak: {v['hours']['words']}"


def _hours_sentence(r: dict) -> str:
    rows = [v for v in r["variants"] if v["key"] != "grid_alone"]
    parts = [f"{v['label']}, {_hours_part(v)}" for v in rows]
    alone = next(v for v in r["variants"] if v["key"] == "grid_alone")
    ac = alone["counted"]
    if not ac:
        parts.append("Today's grid with nothing added stays within its ratings at the summer peak and above")
    elif len(ac) == 1 and ac[0][1] >= LOAD_FACTOR_MAX * 100 - 1e-6:
        parts.append(
            f"Today's grid with nothing added is past a rating at every level from {LOAD_FACTOR_MIN * 100:.0f} % up"
            if alone["level"] <= LOAD_FACTOR_MIN + 1e-9
            else f"Today's grid with nothing added first overloads at {alone['pct']:g} %"
        )
    else:
        parts.append(f"Today's grid with nothing added is past a rating {_ranges(ac)}")
    s = ". ".join(p[0].upper() + p[1:] for p in parts) + " (estimates, a coarse curve)."
    low = [v for v in r["variants"] if v["low_to_pct"] is not None]
    if low:
        top = max(v["low_to_pct"] for v in low)
        who = "every variant" if len(low) == len(r["variants"]) else ", ".join(_lower(v["label"]) for v in low)
        s += (
            f" Below {top:g} % of the peak a line is also over ({who}); not counted: a one-state model holds its flows to the "
            "neighbouring states fixed while its own load falls."
        )
    return s


# ---------------------------------------------------------------------------------- routes
@router.get("/api/evidence/validation")
@limiter.limit("120/minute")
def validation(request: Request, region: str = Query(DEFAULT_REGION)):
    """The engine against the dataset's own solved flows for one state model (module doc: 1)."""
    return validation_for(region_code(region))


@router.post("/api/evidence/n1")
@limiter.limit("60/minute")
async def n1(request: Request, body: EvidenceIn, auto_fix: bool = Query(True)):
    """The N-1 screen of the case's fix (module doc: 2). auto_fix=false: the case on screen already has its fix in it."""
    return await run_in_threadpool(_guarded, n1_screen, body, auto_fix)


@router.post("/api/evidence/hours")
@limiter.limit("60/minute")
async def hours(request: Request, body: EvidenceIn, auto_fix: bool = Query(True)):
    """How often the case would overload (module doc: 3)."""
    return await run_in_threadpool(_guarded, hours_check, body, auto_fix)


def _guarded(fn, body: EvidenceIn, auto_fix: bool) -> dict:
    """A plain 422 when the engine can't balance a case (a storm that splits the model into pieces it can't solve)."""
    try:
        return fn(body, auto_fix)
    except (ArithmeticError, FloatingPointError, np.linalg.LinAlgError) as e:
        raise HTTPException(status_code=422, detail="The engine can't check this case: the storm splits the model into pieces it can't balance") from e
