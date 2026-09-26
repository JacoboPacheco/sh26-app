"""Before the vote (the vote track): a community looks up a real proposed data center and learns what a campus
of that reported size could do to a grid (simulated, labeled synthetic), what it could cost and who pays,
what would have to be built to make it safe, what to ask before approving, and where to speak.

  GET /api/vote/search?q=&state=&limit=   proposals (and states) that match a name, company, place, county or
                                          state; an empty q lists the Florida five first, then the largest
                                          announced or contested ones
  GET /api/vote/proposal/{id}             one proposal: sourced facts (as reported), the civic facts that were
                                          researched (or nulls), the simulation, the cost estimates, the ranked
                                          "what would have to be built" plans, the questions, and the one-page brief

Nothing here is a claim about the real project, its owners or its utility. Every simulation is "a campus of this
reported size at this location, tested on a SYNTHETIC grid model" (Breakthrough Energy / Texas A&M, CC-BY 4.0), every
number that is not a reported fact is an estimate with its assumption shown, and anything that was not researched
stays null (the page says how to find it instead of guessing).

Public like the catalog: no login, nothing stored. The catalog (backend/catalog.py) supplies the facts and the test,
costs.py the prices, briefing.py + solutions.py the ranked plans (each re-run by the engine). backend/demo/civic.json
(local decision body, utility, docket cases, state commission and rules) is researched separately and re-read when the
file changes; backend/demo/questions.json holds the questions.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from starlette.concurrency import run_in_threadpool

import catalog as catalog_mod
import costs
from grid import MW_MAX, REGIONS
from limiter import limiter

router = APIRouter(tags=["vote"])
log = logging.getLogger("uvicorn.error")

DEMO = Path(__file__).parent / "demo"
CIVIC_PATH = DEMO / "civic.json"
QUESTIONS_PATH = DEMO / "questions.json"

ID_PATTERN = r"^[a-z0-9-]{1,120}$"
FEATURED = [  # the five real Florida proposals the demo opens with (CLAUDE.md -> Decisions: FLORIDA FIVE)
    "stonebridge-fort-meade",
    "atlas-compute-fort-pierce",
    "sentinel-grove-fort-pierce",
    "pba-holdings-loxahatchee",
    "nextnrg-near-jacksonville-international-airport",
]
FRAME = (
    "Synthetic grid model: a campus of this reported size at this location, not a prediction about the real project "
    "or the real utility."
)
GRID_CREDIT = "Grid model: Breakthrough Energy / Texas A&M synthetic U.S. test system (CC-BY 4.0). Not any real utility's network."
DISCLAIMER = (
    "Overload tests a campus of the reported size at the reported location on a synthetic grid model "
    "(Breakthrough Energy / Texas A&M, CC-BY 4.0). It is not a prediction about the real project, its owners or its "
    "utility. Facts are as reported by the linked sources; anything not found is left blank. Costs and people counts are "
    "estimates, and the assumption behind each is shown."
)
STATUS_TEXT = {
    "operating": "Operating",
    "under construction": "Under construction",
    "announced": "Announced",
    "paused/canceled": "Paused or canceled",
}
STATUS_RANK = {"announced": 0, "paused/canceled": 0, "under construction": 1, "operating": 2}
STOP_WORDS = {"county", "counties", "parish", "data", "center", "centers", "campus", "the", "of", "and", "in", "near"}
SPREAD_HOUSEHOLDS = (100_000, 1_000_000, 10_000_000)


# ------------------------------------------------------------------------------------ cleaning
def _s(v, n: int = 400) -> str | None:
    """A trimmed one-line string, or None."""
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        return None
    t = " ".join(str(v).split())
    return t[:n] if t else None


def _url(v) -> str | None:
    """Only http(s) links with a host are ever rendered as links."""
    t = _s(v, 600)
    if not t or not re.match(r"^https?://", t, re.I):
        return None
    return t if urlparse(t).netloc else None


def _links(raw, cap: int = 12) -> list[dict]:
    out = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        url = _url(s.get("url"))
        if url:
            out.append({"title": _s(s.get("title"), 300) or url, "url": url, "supports": _s(s.get("supports"), 400)})
        if len(out) >= cap:
            break
    return out


def _outlet(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _n(x) -> str:
    return f"{math.floor(float(x) + 0.5):,}"


def _hh(n: float) -> str:
    """9,348,886 households -> '9.3 million'; small counts stay whole numbers."""
    n = float(n)
    return f"{n / 1e6:.1f} million" if n >= 1e6 else _n(round(n, -3) if n >= 10_000 else n)


def _county(name: str) -> str:
    """'Polk' -> 'Polk County'; a name that already says County / Parish / Borough / City stays as it is."""
    name = (name or "").strip()
    if not name:
        return ""
    return name if re.search(r"\b(county|parish|borough|city|municipio)\b", name, re.I) else f"{name} County"


def _approx(n: float) -> str:
    """'about 11,700': three significant figures, so an estimate never claims more precision than it has."""
    n = float(n)
    if n < 100:
        return f"about {_n(round(n, -1) if n >= 10 else n)}"
    digits = int(math.floor(math.log10(n))) + 1
    q = 10 ** max(digits - 3, 0)
    return f"about {_n(round(n / q) * q)}"


def _money(x: float) -> str:
    """$1.3 billion / $147 million / $2.7 million / $48,000 / $0.10: short and honest about precision."""
    x = float(x)
    if 0 < x < 0.01:
        return "under 1 cent"
    if x >= 999.5e6:
        div, word = 1e9, "billion"  # 999.7 million reads as 1 billion
    elif x >= 1e6:
        div, word = 1e6, "million"
    else:
        div, word = 0, ""
    if div:
        v = x / div
        return f"${v:,.0f} {word}" if v >= 10 else f"${v:.1f} {word}".replace(".0 ", " ")
    if x >= 10_000:
        return f"${round(x, -3):,.0f}"
    if x >= 100:
        return f"${round(x, -1):,.0f}"
    return f"${x:,.2f}"


def _range(lo: float, hi: float) -> str:
    """'$83–147 million', '$900 million–$1.5 billion', '$0–$0.10': the low-high range as one short string."""
    if hi <= 0:
        return "$0"
    if 0 < hi < 0.01 or abs(hi - lo) < 0.005:
        return _money(hi)
    a = "$0" if lo < 0.005 else _money(lo)
    b = _money(hi)
    for unit in (" billion", " million"):
        if a.endswith(unit) and b.endswith(unit):
            return f"{a[: -len(unit)]}–{b[1:]}"
    return f"{a}–{b}"


# ------------------------------------------------------------------------------------ the files
_files_lock = threading.Lock()
_files: dict[str, dict] = {}


def _read(path: Path, clean) -> dict | None:
    """The cleaned JSON of `path`, re-read when the file changes. None when it is missing or unreadable."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _files_lock:
        hit = _files.get(path.name)
        if hit and hit["mtime"] == mtime:
            return hit["data"]
    try:
        data = clean(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        log.warning("vote: could not read %s: %s", path.name, e)
        with _files_lock:
            hit = _files.get(path.name)
            return hit["data"] if hit else None  # keep serving the last good copy
    data["_mtime"] = mtime
    with _files_lock:
        _files[path.name] = {"mtime": mtime, "data": data}
    return data


def _clean_civic(doc: dict) -> dict:
    states: dict[str, dict] = {}
    for code, raw in (doc.get("states") or {}).items():
        if not isinstance(raw, dict) or not re.match(r"^[A-Za-z]{2}$", str(code)):
            continue
        policy = []
        for p in raw.get("policy") or []:
            if isinstance(p, dict) and _s(p.get("title")):
                policy.append({"title": _s(p.get("title"), 240), "summary": _s(p.get("summary"), 500), "url": _url(p.get("url")), "date": _s(p.get("date"), 16)})
        states[str(code).upper()] = {
            "commission": _s(raw.get("commission"), 200),
            "url": _url(raw.get("url")),
            "comment_url": _url(raw.get("comment_url")),
            "note": _s(raw.get("note"), 500),
            "policy": policy[:8],
        }
    proposals: dict[str, dict] = {}
    for pid, raw in (doc.get("proposals") or {}).items():
        if not isinstance(raw, dict) or not re.match(ID_PATTERN, str(pid)):
            continue
        body = raw.get("decision_body")
        body = (
            {"name": _s(body.get("name"), 200), "url": _url(body.get("url")), "comment_url": _url(body.get("comment_url")), "how_to_comment": _s(body.get("how_to_comment"), 500)}
            if isinstance(body, dict) and _s(body.get("name"))
            else None
        )
        util = raw.get("utility")
        util = {"name": _s(util.get("name"), 200), "source": _url(util.get("source"))} if isinstance(util, dict) and _s(util.get("name")) else None
        cases = []
        for c in raw.get("cases") or []:
            if isinstance(c, dict) and (_s(c.get("number")) or _s(c.get("title"))):
                cases.append({"commission": _s(c.get("commission"), 200), "number": _s(c.get("number"), 80), "title": _s(c.get("title"), 300), "url": _url(c.get("url")), "why": _s(c.get("why"), 400)})
        proposals[str(pid)] = {
            "county": _s(raw.get("county"), 120),
            "municipality": _s(raw.get("municipality"), 120),
            "decision_body": body,
            "utility": util,
            "cases": cases[:8],
            "status_note": _s(raw.get("status_note"), 900),
            "sources": _links(raw.get("sources")),
            "checked": _s(raw.get("checked"), 16),
        }
    return {"generated": _s(doc.get("generated"), 16), "states": states, "proposals": proposals}


def _clean_questions(doc: dict) -> dict:
    qs = []
    for q in doc.get("questions") or []:
        if isinstance(q, dict) and _s(q.get("question")):
            qs.append(
                {
                    "id": _s(q.get("id"), 60) or f"q{len(qs) + 1}",
                    "topic": _s(q.get("topic"), 40),
                    "question": _s(q.get("question"), 400),
                    "why": _s(q.get("why"), 900),
                    "listen_for": _s(q.get("listen_for"), 600),
                    "sources": _links(q.get("sources"), 4),
                }
            )
    return {"questions": qs}


def civic() -> dict:
    return _read(CIVIC_PATH, _clean_civic) or {"generated": None, "states": {}, "proposals": {}, "_mtime": None}


def questions() -> list[dict]:
    return (_read(QUESTIONS_PATH, _clean_questions) or {"questions": []})["questions"]


# ------------------------------------------------------------------------------------ search
def _canonical(cat: dict, eid: str) -> dict | None:
    e = cat["entries"].get(eid)
    if e is None:
        return None
    return cat["entries"][e["duplicate_of"]] if e["duplicate_of"] else e


def _row(e: dict) -> dict:
    return {
        "id": e["id"],
        "name": e["name"],
        "company": e["company"],
        "city": e["city"],
        "county": e["county"],
        "county_text": _county(e["county"]),
        "state": e["state"],
        "state_name": e["state_name"],
        "mw": e["mw"],
        "status": e["status"],
        "status_text": STATUS_TEXT.get(e["status"], (e["status"] or "unknown").capitalize()),
        "year": e["year"],
        "featured": e["id"] in FEATURED,
    }


def _hay(e: dict) -> str:
    return " ".join([e["name"], e["company"], e["city"], e["county"], e["state"], e["state_name"]]).lower()


def _matches(e: dict, tokens: list[str]) -> bool:
    hay = _hay(e)
    return all(re.search(r"\b" + re.escape(t), hay) for t in tokens)


def search_rows(q: str, state: str | None, limit: int) -> dict:
    cat = catalog_mod.catalog()
    tokens = [t for t in re.findall(r"[a-z0-9]+", q.lower()) if t not in STOP_WORDS]
    want = state.upper() if state else None
    pool = [e for e in cat["entries"].values() if e["duplicate_of"] is None and (not want or e["state"] == want)]
    rows = [e for e in pool if _matches(e, tokens)] if tokens else pool

    def key(e):
        name_hits = sum(1 for t in tokens if re.search(r"\b" + re.escape(t), e["name"].lower())) if tokens else 0
        fi = FEATURED.index(e["id"]) if e["id"] in FEATURED else 99
        if tokens:
            return (-name_hits, fi, STATUS_RANK.get(e["status"], 3), -(e["mw"] or 0))
        return (fi, STATUS_RANK.get(e["status"], 3), -(e["mw"] or 0), e["name"])

    rows.sort(key=key)
    by_state: dict[str, int] = {}
    for e in rows:
        by_state[e["state"]] = by_state.get(e["state"], 0) + 1
    states = [{"code": c, "name": REGIONS[c]["name"] if c in REGIONS else c, "count": n} for c, n in by_state.items()]
    states.sort(key=lambda s: (-s["count"], s["name"]))
    return {
        "q": q,
        "total": len(rows),
        "proposals": [_row(e) for e in rows[:limit]],
        "states": states,
        "frame": FRAME,
        "generated": cat["generated"],
    }


@router.get("/api/vote/search")
@limiter.limit("60/minute")
def vote_search(
    request: Request,
    q: str = Query("", max_length=80),
    state: str | None = Query(None, pattern=r"^[A-Za-z]{2}$"),
    limit: int = Query(20, ge=1, le=50),
):
    """Proposals whose name, company, city, county or state match every word of q (word starts, any case);
    an empty q lists the Florida five first, then the largest announced or contested ones."""
    return search_rows(q.strip(), state, limit)


# ------------------------------------------------------------------------------------ the simulation
def _flex_text(t: dict, room: str) -> str:
    f = t["flexible"]
    mw = _n(t["tested_mw"])
    if t["overloaded"] == 0:
        return f"At {mw} MW nothing goes over its limit on the model: the site has room for {room} MW before a line does."
    over = f"{t['over_text']} goes over its limit" if t["overloaded"] == 1 else f"{t['over_text']} go over their limits"
    if f["people"] > 0:
        s = f"At {mw} MW {over} on the model and trip in turn; {_approx(f['people'])} people lose power in the cascade (estimate)"
        return s + (", and the campus itself is cut off." if f["site_cut_off"] else ".")
    if f["site_cut_off"]:
        return f"At {mw} MW {over} on the model and the campus itself is cut off; no other customers lose power."
    return f"At {mw} MW {over} on the model, and the cascade settles without cutting anyone off."


def _firm_text(t: dict) -> str:
    m = t["firm"]
    if t["overloaded"] == 0:
        return "Nothing goes over its limit, so firm service changes nothing here."
    if m.get("firm_held"):
        if m["people"] > 0:
            return f"The grid operator keeps the campus on and cuts other customers instead: {_approx(m['people'])} people lose power (estimate)."
        return "The grid operator can keep the campus on, and no other customers lose power on the model."
    if m.get("firm_held") is False:
        s = "Firm service cannot hold it here: no nearby cut relieves the lines that feed the campus enough, so it is cut off as well"
        return s + (f", with {_approx(m['people'])} people without power (estimate)." if m["people"] > 0 else ".")
    return "Firm service was not tested for this case."


def _round_people_text(text: str | None) -> str | None:
    """'about 11,694 people' -> 'about 11,700 people': one level of precision across the whole page."""
    if not text:
        return text
    return re.sub(r"about ([\d,]{4,}) people", lambda m: f"{_approx(int(m.group(1).replace(',', '')))} people", text)


def _simulation(e: dict) -> dict:
    """The catalog's test of this campus, reshaped for the page. Always carries the synthetic-model note."""
    try:
        t = catalog_mod.test_entry(e, full=True)
    except Exception:  # noqa: BLE001 - a readable answer, not a 500
        log.exception("vote: simulation failed for %s", e["id"])
        t = {"tested": False, "reason": "The engine could not settle this case."}
    if not t.get("tested"):
        return {"tested": False, "reason": t.get("reason") or "Not tested.", "verdict": "untested", "note": FRAME}
    room = _n(t["headroom_mw"])
    f, m = t["flexible"], t["firm"]
    if t["verdict"] == "fits":
        headline = f"On the model this size fits here: there is room for {room} MW before any line goes over its limit."
    elif t["verdict"] == "overloads":
        headline = f"On the model this size goes past the site's room of {room} MW and pushes {t['over_text']} over their limits, but no other customers lose power."
    else:
        headline = (
            f"On the model the site has room for {room} MW; at {_n(t['tested_mw'])} MW the cascade leaves {_approx(f['people'])} people without power (estimate)."
        )
    return {
        "tested": True,
        "verdict": t["verdict"],
        "headline": headline,
        "region_name": t["region_name"],
        "interconnect": t["interconnect"],
        "site": {"substation": t["sub_name"], "area": t["sub_area"], "kv": t["kv"], "km": t["km"], "lat": t["sub_lat"], "lon": t["sub_lon"]},
        "tested_mw": t["tested_mw"],
        "over_workspace_max": t["over_workspace_max"],
        "room_mw": t["headroom_mw"],
        "overloaded": t["overloaded"],
        "over_text": t["over_text"],
        "lines": t.get("lines") or [],
        "flexible": {
            "label": "Flexible service",
            "meaning": "The campus can be cut off when its own lines trip.",
            "people": f["people"],
            "people_text": _approx(f["people"]) if f["people"] else "no one",
            "lost_mw": f["lost_mw"],
            "steps": f["steps"],
            "outcome": f["outcome"],
            "site_cut_off": f["site_cut_off"],
            "areas": [{**a, "people_text": _approx(a["people"]) if a["people"] >= 1000 else _n(a["people"])} for a in (f.get("areas") or [])[:5]],
            "area_count": f.get("area_count", 0),
            "text": _flex_text(t, room),
        },
        "firm": {
            "label": "Firm service",
            "meaning": "The grid operator keeps the campus on and cuts other customers instead.",
            "people": m["people"],
            "people_text": _approx(m["people"]) if m["people"] else "no one",
            "held": m.get("firm_held"),
            "steps": m["steps"],
            "site_cut_off": m["site_cut_off"],
            "text": _firm_text(t),
        },
        "why": _round_people_text(t.get("why")),
        "people_per_mw": t["people_per_mw"],
        "population": t["population"],
        "note": FRAME,
    }


# ------------------------------------------------------------------------------------ the cost
def _line(by: dict, key: str) -> dict:
    ln = by[key]
    return {
        "low": ln["low"],
        "high": ln["high"],
        "range": _range(ln["low"], ln["high"]),
        "big": _money(ln["high"]) if ln["high"] > 0 else None,
        "formula": ln["formula"],
        "assumption": ln["assumption"],
        "sources": [{"title": s["name"], "url": s["url"]} for s in ln["sources"]],
    }


def _cost(e: dict, sim: dict) -> dict | None:
    """costs.py's estimate for this same case (a flexible campus at the reported size), every figure a low-high range."""
    if not sim.get("tested"):
        return _bill_only(e)
    body = costs.CostIn(region=e["state"], lat=e["lat"], lon=e["lon"], mw=float(min(e["mw"], MW_MAX)))
    try:
        c = costs.cached_estimate(body)
    except HTTPException as ex:
        log.warning("vote: cost refused for %s: %s", e["id"], ex.detail)
        return _bill_only(e)
    except Exception:  # noqa: BLE001
        log.exception("vote: cost failed for %s", e["id"])
        return _bill_only(e)
    by = {ln["key"]: ln for ln in c["lines"]}
    h = c["headline"]
    up, who = by["upgrades"], by["who_pays"]
    blackout = _line(by, "blackout")
    blackout.update({"hours": c["hours_out"], "outage_label": h["outage_label"], "people": h["people"], "people_text": _approx(h["people"]) if h["people"] else "no one", "mwh": by["blackout"]["mwh"]})
    upgrades = _line(by, "upgrades")
    upgrades.update({"count": up["count"], "prevents": up["prevents"], "calm": up["calm"], "added_mva": up["added_mva"], "people_after": up["people_after"]})
    who_pays = _line(by, "who_pays")
    who_pays.update({"households": who["households"], "households_text": _hh(who["households"]), "per": "month", "big": _money(who["high"]) if who["high"] > 0 else None})
    spread = []
    if up["high"] > 0:
        for hh in SPREAD_HOUSEHOLDS:
            spread.append({"households": hh, "per_month": round(up["high"] * costs.CRF / 12.0 / hh, 4), "text": _money(up["high"] * costs.CRF / 12.0 / hh)})
    return {
        "estimate": True,
        "region_name": c["region_name"],
        "blackout": blackout,
        "upgrades": upgrades,
        "power_bill": _line(by, "power_bill"),
        "who_pays": who_pays,
        "spread": spread,
        "insights": c["insights"],
        "notes": c["notes"],
        "note": "Every figure is an estimate on a synthetic model, with its formula and assumption shown; the big figure is the high end of the range.",
    }


def _bill_only(e: dict) -> dict | None:
    """When the campus can't be run on a model, the campus's own power bill is still a plain formula."""
    if not e.get("mw"):
        return None
    price = costs.INDUSTRIAL_PRICE_2024.get(e["state"], costs.US_INDUSTRIAL_PRICE_2024)
    per_mwh = price * 10.0
    lo = round(e["mw"] * costs.HOURS_PER_YEAR * costs.LOAD_FACTOR_LOW * per_mwh)
    hi = round(e["mw"] * costs.HOURS_PER_YEAR * costs.LOAD_FACTOR_HIGH * per_mwh)
    return {
        "estimate": True,
        "region_name": e["state_name"],
        "blackout": None,
        "upgrades": None,
        "who_pays": None,
        "spread": [],
        "power_bill": {
            "low": lo,
            "high": hi,
            "range": _range(lo, hi),
            "big": _money(hi),
            "formula": f"{_n(e['mw'])} MW × 8,760 h × {costs.LOAD_FACTOR_LOW:.0%}–{costs.LOAD_FACTOR_HIGH:.0%} of the time × {price:.2f}¢ per kWh",
            "assumption": (
                f"The campus draws {costs.LOAD_FACTOR_LOW:.0%}–{costs.LOAD_FACTOR_HIGH:.0%} of its size on average (LBNL: 50% average utilization; AI training "
                f"servers run 80% of the time) at the state's 2024 average industrial price, {price:.2f}¢ per kWh. Real large-load contracts can be lower or higher."
            ),
            "sources": [{"title": s["name"], "url": s["url"]} for s in (costs.SOURCES["lbnl_dc"], costs.SOURCES["eia_price"])],
        },
        "insights": [],
        "notes": ["The campus could not be run on a grid model here, so only its power bill is estimated."],
        "note": "Every figure is an estimate, with its formula and assumption shown; the big figure is the high end of the range.",
    }


# ------------------------------------------------------------------------------------ what would have to be built
FAMILY_TITLE = {
    "upgrade": "Strengthen the grid and build the full size",
    "combo": "Build a little smaller and strengthen the grid",
    "agentic": "AI-proposed plan",
    "move": "Build the same size at another site",
    "shrink": "Build a smaller campus",
    "onsite": "Add on-site generation",
    "flexible": "Agree to cut back at the peak hour",
}


def _solution_lines(fx: dict) -> list[str]:
    """The 'you have to do this' list (English), with the move plan worded as what the model found, not as advice."""
    lines = [str(x) for x in ((fx.get("must") or {}).get("en") or [])]
    if fx.get("family") == "move":
        d = fx.get("detail") or {}
        town = ((d.get("sites") or [{}])[0].get("town")) or d.get("town")
        if town:
            lines = [f"On the model, the same size fits at a substation near {town}"]
    return lines[:8]


def _safe_block(rep: dict | None, sim: dict, reason: str | None = None) -> dict:
    if rep is None:
        return {"status": "unavailable", "reason": reason or "Not worked out for this case.", "solutions": [], "agentic": None, "note": FRAME}
    fixes = rep.get("fixes") or []
    out = []
    for i in rep.get("solutions") or []:
        fx = fixes[i]
        cost = fx.get("cost")
        d = fx.get("detail") or {}
        title = FAMILY_TITLE.get(fx.get("family"), "A fix the engine checked")
        if fx.get("family") == "agentic" and d.get("name"):
            title = f"AI-proposed plan: {d['name']}"
        out.append(
            {
                "rank": fx.get("rank"),
                "family": fx.get("family"),
                "title": title,
                "action": fx.get("action"),
                "kept_mw": fx.get("kept_mw"),
                "kept_pct": fx.get("kept_pct"),
                "full_size": bool(fx.get("kept_pct") is not None and fx["kept_pct"] >= 99.5),
                "steps": _solution_lines(fx),
                "cost": {"low": cost["low"], "high": cost["high"], "range": _range(cost["low"], cost["high"]), "big": _money(cost["high"]), "parts": cost["lines"]} if cost else None,
                "by": fx.get("by") or "engine",
                "why": d.get("why") if fx.get("by") == "gemini" else None,
                "verified": fx.get("verdict") == "holds",
                "outcome": {"people": (fx.get("outcome") or {}).get("people", 0), "steps": (fx.get("outcome") or {}).get("steps", 0)},
            }
        )
    ag = rep.get("agentic")
    agentic = {"status": ag.get("status"), "added": ag.get("added", 0)} if isinstance(ag, dict) else None
    if out:
        full = [s for s in out if s["full_size"]]
        headline = (
            f"To build the full {_n(sim.get('tested_mw') or 0)} MW here, the model needs the upgrades below; each plan was re-run by the engine and holds."
            if full
            else "The engine found smaller plans that hold; none keeps the full size."
        )
        status = "solutions"
    elif sim.get("verdict") == "fits":
        headline = f"On the model nothing needs building for this size: it fits with room for {_n(sim.get('room_mw') or 0)} MW. A real interconnection study is the utility's, not this model's."
        status = "not_needed"
    else:
        headline = "The engine did not find a verified plan for this case."
        status = "none"
    return {
        "status": status,
        "headline": headline,
        "solutions": out,
        "agentic": agentic,
        "note": "Simulated on the synthetic model. Every plan is re-run by the same engine before it is listed; costs are estimates from published per-mile and per-MVA figures.",
    }


def _report(e: dict, sim: dict):
    """The engine's ranked plans for this case (briefing.py; cached there by case)."""
    if not sim.get("tested"):
        return None, sim.get("reason")
    import briefing

    try:
        rep = briefing.report_for(briefing.BriefingIn(region=e["state"], lat=e["lat"], lon=e["lon"], mw=float(min(e["mw"], MW_MAX))))
        return rep, None
    except HTTPException as ex:
        return None, str(ex.detail)
    except Exception:  # noqa: BLE001
        log.exception("vote: ranked plans failed for %s", e["id"])
        return None, "The engine could not work out plans for this case."


# ------------------------------------------------------------------------------------ the page's facts
def _entry(e: dict) -> dict:
    keep = ("id", "name", "company", "city", "county", "state", "state_name", "lat", "lon", "mw", "mw_basis", "status", "year", "confidence", "location_basis", "ai")
    out = {k: e.get(k) for k in keep}
    out["status_text"] = STATUS_TEXT.get(e["status"], (e["status"] or "unknown").capitalize())
    out["county_text"] = _county(e["county"])
    srcs, seen = [], {}
    for x in e.get("sources") or []:
        o = _outlet(x["url"])
        seen[o] = seen.get(o, 0) + 1
        srcs.append({**x, "outlet": o, "label": o if seen[o] == 1 else f"{o} ({seen[o]})"})
    out["sources"] = srcs
    return out


def _civic_block(e: dict, cv: dict) -> dict:
    p = cv["proposals"].get(e["id"])
    if p is None:
        return {"researched": False, "county": None, "municipality": None, "decision_body": None, "utility": None, "cases": [], "status_note": None, "sources": [], "checked": None}
    return {"researched": True, **p}


def _state_block(e: dict, cv: dict) -> dict:
    code = e["state"]
    s = cv["states"].get(code)
    base = {"code": code, "name": e["state_name"]}
    if s is None:
        return {**base, "researched": False, "commission": None, "url": None, "comment_url": None, "policy": []}
    return {**base, "researched": True, **s}


_parts_lock = threading.Lock()
_parts_cache: "OrderedDict[tuple, dict]" = OrderedDict()
CACHE_SIZE = 64


def _parts(e: dict, cat_mtime: float) -> dict:
    """Everything about a proposal that doesn't change between requests (the expensive part), cached per file versions."""
    cv = civic()
    key = (cat_mtime, cv.get("_mtime"), e["id"])
    with _parts_lock:
        hit = _parts_cache.get(key)
        if hit is not None:
            _parts_cache.move_to_end(key)
            return hit
    t0 = time.perf_counter()
    sim = _simulation(e)
    parts = {
        "entry": _entry(e),
        "civic": _civic_block(e, cv),
        "state": _state_block(e, cv),
        "simulation": sim,
        "cost": _cost(e, sim),
    }
    parts["ms"] = round((time.perf_counter() - t0) * 1000)
    with _parts_lock:
        _parts_cache[key] = parts
        while len(_parts_cache) > CACHE_SIZE:
            _parts_cache.popitem(last=False)
    return parts


# ------------------------------------------------------------------------------------ the brief
def _where_line(entry: dict) -> str:
    return ", ".join(x for x in (entry["city"], entry["county_text"], entry["state_name"]) if x)


def _speak_items(entry: dict, civ: dict, st: dict) -> list[dict]:
    items = []
    b = civ["decision_body"]
    if b:
        items.append({"text": f"Local decision body: {b['name']}", "url": b["url"]})
        if b["comment_url"]:
            items.append({"text": "Public comment page for that body", "url": b["comment_url"]})
        elif b.get("how_to_comment"):
            items.append({"text": f"How to comment: {b['how_to_comment']}"})
    else:
        who = entry["county_text"] or "the county"
        items.append({"text": f"Local decision body: not researched yet. Ask the {who} clerk (or the city clerk in {entry['city'] or 'the city'}) which board hears this proposal, and for the date and agenda."})
    if civ["utility"]:
        items.append({"text": f"Utility named in the sources: {civ['utility']['name']}", "url": civ["utility"]["source"]})
    for c in civ["cases"]:
        label = " ".join(x for x in (c["commission"], c["number"], c["title"]) if x)
        items.append({"text": f"Case: {label}", "url": c["url"], "detail": c["why"]})
    if st.get("commission"):
        items.append({"text": f"State utility regulator: {st['commission']}", "url": st["url"]})
        if st.get("comment_url"):
            items.append({"text": "How to comment or search its dockets", "url": st["comment_url"]})
        if st.get("note"):
            items.append({"text": st["note"]})
    else:
        items.append({"text": f"State utility regulator ({entry['state_name']}): not researched yet. Search the state's public service commission docket list for the utility's large-load rules."})
    return items


def _brief(parts: dict, safe: dict, qs: list[dict]) -> dict:
    entry, civ, st, sim, cost = parts["entry"], parts["civic"], parts["state"], parts["simulation"], parts["cost"]
    where = _where_line(entry)
    sections: list[dict] = []

    facts = [{"text": f"Reported size: {_n(entry['mw'])} MW" if entry["mw"] else "Reported size: not found"}]
    facts.append({"text": f"Status, as reported: {entry['status_text']}"})
    if entry["year"]:
        facts.append({"text": f"Timing, as reported: {entry['year']}"})
    facts.append({"text": f"Developer or applicant, as reported: {entry['company']}" if entry["company"] else "Developer: not found in the sources"})
    facts.append({"text": f"Place: {where} (location approximate)"})
    facts.append({"text": f"Confidence in these facts: {(entry['confidence'] or 'unknown').capitalize()}. Check them against the sources listed below."})
    sections.append({"heading": "The proposal, as reported", "items": facts})

    sections.append(
        {
            "heading": "Where it stands",
            "paragraphs": [
                civ["status_note"]
                or (
                    f"Status as reported by the sources listed below: {entry['status_text'].lower()}"
                    + (f"; timing as reported: {entry['year']}" if entry["year"] else "")
                    + ". The local decision has not been researched for this proposal yet."
                )
            ],
        }
    )

    if sim.get("tested"):
        paras = [sim["headline"], "Flexible service: " + sim["flexible"]["text"], "Firm service: " + sim["firm"]["text"]]
    else:
        paras = [f"Not tested on the model: {sim.get('reason')}"]
    sections.append({"heading": "What a campus this size could do to a grid (simulated)", "paragraphs": paras, "note": FRAME})

    if cost:
        items = []
        b, up, bill, who = cost.get("blackout"), cost.get("upgrades"), cost.get("power_bill"), cost.get("who_pays")
        if b:
            items.append({"text": f"A blackout, if it happened: {b['range']} ({b['outage_label']} without power, estimate)" if b["high"] > 0 else "A blackout: none on the model at this size"})
        if up:
            items.append({"text": f"Upgrades to keep every line within its limit: {up['range']}" if up["high"] > 0 else "Upgrades: none needed on the model at this size"})
        if bill:
            items.append({"text": f"The campus's own power bill: {bill['range']} a year"})
        if who:
            items.append(
                {
                    "text": f"If the upgrades were spread over all {_hh(who['households'])} {cost['region_name']} households: {who['range']} a month each (illustrative; regulators decide who really pays)"
                    if who["high"] > 0
                    else "Per household: nothing to pass on at this size",
                }
            )
        sections.append({"heading": "What it could cost and who pays (estimates, high end shown first)", "items": items})

    if safe["status"] in ("solutions", "not_needed"):
        items = []
        for s in safe["solutions"][:3]:
            line = s["title"] + (f" ({_n(s['kept_mw'])} MW)" if s["kept_mw"] and not s["full_size"] else "")
            if s["cost"]:
                line += f": {s['cost']['range']}"
            items.append({"text": line, "detail": s["steps"][0] if s["steps"] else None})
        sections.append({"heading": "What would have to be built for this to be safe (simulated)", "paragraphs": [safe["headline"]] if not items else None, "items": items or None})

    sections.append({"heading": "Questions to ask before approving", "items": [{"text": q["question"]} for q in qs]})
    sections.append({"heading": "Where to speak", "items": _speak_items(entry, civ, st)})

    seen: set[str] = set()
    sources: list[dict] = []

    def add(title, url):
        if url and url not in seen:
            seen.add(url)
            sources.append({"title": title or url, "url": url})

    for s in entry["sources"]:
        add(s["label"], s["url"])  # the outlet, not its headline: the brief states facts, the link shows where they were reported
    for s in civ["sources"]:
        add(s["title"], s["url"])
    if cost:
        for k in ("blackout", "power_bill"):
            for s in (cost.get(k) or {}).get("sources", [])[:2]:
                add(s["title"], s["url"])
    return {
        "title": f"Before the vote: {entry['name']}",
        "subtitle": " · ".join(x for x in (entry["company"], where) if x),
        "generated": date.today().isoformat(),
        "sections": [{k: v for k, v in sec.items() if v} for sec in sections if sec.get("items") or sec.get("paragraphs")],
        "sources": sources[:30],
        "credit": GRID_CREDIT,
        "disclaimer": DISCLAIMER,
    }


# ------------------------------------------------------------------------------------ the proposal
def _assemble(parts: dict, rep: dict | None, reason: str | None) -> dict:
    safe = _safe_block(rep, parts["simulation"], reason)
    qs = questions()
    return {
        "entry": parts["entry"],
        "civic": parts["civic"],
        "state": parts["state"],
        "simulation": parts["simulation"],
        "cost": parts["cost"],
        "safe": safe,
        "questions": qs,
        "brief": _brief(parts, safe, qs),
        "frame": FRAME,
        "credit": GRID_CREDIT,
        "ms": parts["ms"],
    }


def _load(eid: str) -> tuple[dict, dict, dict | None, str | None]:
    """(entry, parts, ranked-plans report, why not). Sync: run in the thread pool."""
    cat = catalog_mod.catalog()
    e = _canonical(cat, eid)
    if e is None:
        raise HTTPException(status_code=404, detail="No proposal with that id in the catalog")
    parts = _parts(e, cat["mtime"])
    rep, reason = _report(e, parts["simulation"])
    return e, parts, rep, reason


@router.get("/api/vote/proposal/{proposal_id}")
@limiter.limit("60/minute")
async def vote_proposal(request: Request, proposal_id: str = PathParam(..., pattern=ID_PATTERN)):
    """One proposal for the page and its printable brief. The plans' AI step (if a key is set) runs in the
    background: `safe.agentic.status` is 'running' until it is done, and asking again returns the newer list."""
    e, parts, rep, reason = await run_in_threadpool(_load, proposal_id)
    if rep is not None:
        try:
            import solutions

            solutions.kick(rep["key"])  # Gemini proposes plans, the engine verifies them, in the background
            rep = _fresh(rep)
        except Exception as ex:  # noqa: BLE001 - the AI step is a bonus
            log.warning("vote: could not start the AI proposer: %s", ex)
    data = await run_in_threadpool(_assemble, parts, rep, reason)
    data["alias_of"] = proposal_id if proposal_id != e["id"] else None
    return data


def _fresh(rep: dict) -> dict:
    """The cached report as it is now (the proposer edits it in place when it finishes)."""
    import briefing

    return briefing.report_by_key(rep["key"]) or rep
