"""Catalog: real U.S. AI data-center campuses at their publicly reported sizes, each tested on its
state's SYNTHETIC grid model (owned by the catalog track).

The entries come from backend/demo/datacenters_us.json (compiled from cited news and company
sources by a research pass; locations approximate). Nothing here is a claim about the real project
or the real utility: every test is "a campus of this reported size at this location, on a synthetic
grid model", and every people number is an estimate (grid.people_fields).

  GET /api/catalog          every entry (facts + sources), its test summary once the batch has run,
                            and the national totals. The batch (one what-if + a flexible and a firm
                            cascade per campus) runs once per process in a background thread, started
                            by the first request; entries carry `test: null` until it is done.
  GET /api/catalog/status   the batch's progress ("testing N of M").
  GET /api/catalog/{id}     one entry + its full test: the connecting substation, the site's room,
                            the lines over their limits, both cascades with their steps and the
                            areas hit most. Computed on demand (< 0.5 s) and cached.

Cleaning at read time (the file itself belongs to the research workflow): entries without an id get
a slug; likely duplicates (the research merged overlapping lists) are folded into the first entry
(`duplicate_of`, sources merged) and left out of the totals; source links other than http(s) are
dropped; speculation about undisclosed tenants is cut from the company line and from the size and
place notes (`_clean_basis`); an entry the file marks `duplicate_of` folds into that entry.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam

from grid import (
    MAX_SNAP_KM,
    MW_MAX,
    POPULATION_SOURCE,
    REGIONS,
    SiteIn,
    _case_header,
    _km,
    case_firm_buses,
    check_site,
    grid_at,
    people_fields,
)
from limiter import limiter
from powerflow import area_of

router = APIRouter(tags=["catalog"])
log = logging.getLogger("uvicorn.error")

CATALOG_PATH = Path(__file__).parent / "demo" / "datacenters_us.json"
IN_PLAY = ("operating", "under construction", "announced")  # counted in the totals; paused/canceled are listed only
DUP_KM = 40.0  # two entries this close, same size, same project name or developer = one campus listed twice
TOP_AREAS = 6
TOP_LINES = 8
FRAME = (
    "A campus of this reported size at this location, tested on a synthetic grid model: "
    "not a prediction about the real project or the real utility."
)
METHOD = (
    "Each campus is added alone to its state's synthetic model at the model's base load, connected at "
    "the nearest substation's highest-voltage bus. Flexible: the plain cascade (the campus's own lines "
    "may trip). Firm: the grid operator keeps the campus on and cuts other customers instead. People "
    "without power = lost load x the state's residents per MW of model load (an estimate)."
)
# What the app SHOWS for a project whose reported facts do not fit "N MW + a status bucket" (Decisions -> NO DEFAMATION:
# say what the sources say, no more). Words only: `mw` and `status` still feed the tests, the counts and the buckets.
# `size`/`status` are the full lines (a proposal page, a brief, a card); `status_short` is the one-line form for a list row.
SHOWN_AS = {
    # NextNRG (fact check, Sat 27 Sep 2026): the 200 MW is the company's microgrid figure, not a reported data-center load;
    # its CEO has said the project is solar, not a data center (First Coast News, Apr 20); the county's Apr 14 release
    # reports no active or pending applications; "paused" has no source.
    "nextnrg-near-jacksonville-international-airport": {
        "size": "200 MW (the company's microgrid figure)",
        "size_es": "200 MW (la cifra de microrred de la empresa)",
        "size_note": "the company's microgrid figure",
        "status": "The company's CEO has said the project is solar, not a data center (First Coast News, Apr 20); the county reports no active or pending applications (Apr 14)",
        "status_es": "El director de la empresa ha dicho que el proyecto es solar, no un centro de datos (First Coast News, 20 de abril); el condado informa que no hay solicitudes activas ni pendientes (14 de abril)",
        "status_short": "CEO says solar, not a data center",
        "hypothetical": True,  # tested as a hypothetical campus of that size, never as "the reported campus"
    },
}


def shown_as(entry_id: str) -> dict:
    """The display wording for one project ({} when the plain "N MW, bucket" wording is true)."""
    return SHOWN_AS.get(entry_id) or {}


# ------------------------------------------------------------------------------------ the entries
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:80] or "campus"


_SPECULATION = re.compile(r"specul|rumou?r|allegedly|reportedly", re.I)
_UNDISCLOSED = re.compile(r"undisclosed|not disclosed|not confirmed|unconfirmed", re.I)


def _clean_company(text: str) -> str:
    """Never name a tenant the sources don't confirm (Decisions → NO DEFAMATION). In a parenthetical
    that says the tenant is undisclosed, keep only that statement ("tenant undisclosed; reports
    speculated X" -> "tenant undisclosed"); elsewhere drop speculation. A company line that is itself
    "not confirmed …" becomes a plain "Developer not confirmed in the sources". Sourced facts stay."""
    raw = str(text or "")
    outside = re.sub(r"\([^()]*\)", "", raw)
    if _UNDISCLOSED.search(outside) or _SPECULATION.search(outside):
        return "Developer not confirmed in the sources"

    def fix(m: re.Match) -> str:
        parts = [p.strip() for p in re.split(r"[;,]", m.group(1)) if p.strip()]
        if any(_UNDISCLOSED.search(p) or _SPECULATION.search(p) for p in parts):
            # keep the "undisclosed" statement and plain roles ("developer"); drop anything naming someone
            parts = [p for p in parts if not _SPECULATION.search(p) and (_UNDISCLOSED.search(p) or not re.search(r"[A-Z]", p))]
        return f"({'; '.join(parts)})" if parts else ""

    out = re.sub(r"\(([^()]*)\)", fix, raw)
    return re.sub(r"\s{2,}", " ", out).strip(" ;,")


# The size and place notes get the same rule: a company named beside "not confirmed" (or "links … to", "speculated")
# is an unconfirmed tenant, so that clause is cut, and so is "…, where Google is building" on a load the sources don't
# tie to Google. A company that is the entry's own (named in its name or company line) stays.
_TENANTS = re.compile(
    r"\b(Anthropic|OpenAI|Stargate|Google|Alphabet|Microsoft|Meta|Facebook|Amazon|AWS|xAI|Oracle|Apple|Nvidia|NVIDIA|CoreWeave|Fluidstack|Nebius|Tesla|IBM)\b"
)
_DOUBT = re.compile(r"not (?:been )?confirmed|unconfirmed|neither was confirmed|not disclosed|undisclosed|specul|rumou?r|allegedly|do(?:es)? not tie|\blinks?\b.{0,80}\bto\b", re.I)
_WHERE_BUILDING = re.compile(r",?\s*where ([A-Z][\w&.'-]*(?: [A-Z][\w&.'-]*)*) (?:is|are) (?:building|developing|planning|expanding)\b[^.;]*")
_LOW_CONF = re.compile(r"^\s*LOW CONFIDENCE(?: ON ([A-Z]+))?\s*:\s*")


def _clean_basis(text: str, own: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    mine = set(_TENANTS.findall(own or ""))

    def other(t: str) -> bool:
        return any(n not in mine for n in _TENANTS.findall(t))

    m = _LOW_CONF.match(raw)
    if m:  # the entry's confidence field already says low; keep only what the flag was about
        rest = raw[m.end() :]
        raw = ("Attribution is uncertain. " if (m.group(1) or "").upper() == "ATTRIBUTION" else "") + rest[:1].upper() + rest[1:]
    raw = _WHERE_BUILDING.sub(lambda w: "" if other(w.group(1)) else w.group(0), raw)
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", raw):
        if not (_DOUBT.search(sentence) and other(sentence)):
            kept.append(sentence)
            continue
        parts = re.split(r"(, and |; )", sentence.rstrip("."))
        clauses = [p for p in parts[0::2]]
        good = [c for c in clauses if not (other(c) and (_DOUBT.search(c) or len(clauses) == 1))]
        if good and len(good) < len(clauses):
            s = "; ".join(g.strip() for g in good)
            kept.append(s[:1].upper() + s[1:] + ".")
    return re.sub(r"\s{2,}", " ", " ".join(kept)).strip()


def _sources(raw) -> list[dict]:
    out, seen = [], set()
    for s in raw or []:
        url = str((s or {}).get("url") or "").strip()
        if not re.match(r"^https?://", url, re.I) or url in seen:
            continue  # only real web links are rendered as links, each once
        seen.add(url)
        out.append({"title": str(s.get("title") or url)[:300], "url": url, "supports": str(s.get("supports") or "")[:400]})
    return out


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _tokens(name: str) -> set[str]:
    stop = {"data", "center", "centers", "campus", "the", "of", "and", "ai", "project", "park"}
    return {t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in stop and len(t) > 1}


def _first_word(company: str) -> str:
    m = re.match(r"\s*([A-Za-z0-9]+)", company or "")
    return m.group(1).lower() if m else ""


def _same_campus(a: dict, b: dict) -> bool:
    if a["state"] != b["state"] or a["lat"] is None or b["lat"] is None:
        return False
    if _km(a["lat"], a["lon"], b["lat"], b["lon"]) > DUP_KM:
        return False
    if a["name"].strip().lower() == b["name"].strip().lower():
        return True
    if a["mw"] != b["mw"]:
        return False
    ta, tb = _tokens(a["name"]), _tokens(b["name"])
    jac = len(ta & tb) / max(len(ta | tb), 1)
    return jac >= 0.5 or (_first_word(a["company"]) and _first_word(a["company"]) == _first_word(b["company"]))


_CONF = {"high": 3, "medium": 2, "low": 1}


def _normalize(doc: dict) -> dict:
    """The file's entries, cleaned (ids, numbers, links, duplicates). Keyed by id, in file order."""
    entries: "OrderedDict[str, dict]" = OrderedDict()
    for raw in doc.get("entries", []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        company = str(raw.get("company") or "").strip()
        city = str(raw.get("city") or "").strip()
        if not name and not company:
            continue
        base = str(raw.get("id") or "").strip() or _slug(f"{company or name}-{city}")
        eid, n = _slug(base), 2
        while eid in entries:
            eid, n = f"{_slug(base)}-{n}", n + 1
        state = str(raw.get("state") or "").strip().upper()
        mw = _num(raw.get("mw"))
        e = {
            "id": eid,
            "name": name or company,
            "company": _clean_company(company),
            "city": city,
            "county": str(raw.get("county") or "").strip(),
            "state": state,
            "state_name": REGIONS[state]["name"] if state in REGIONS else state,
            "lat": _num(raw.get("lat")),
            "lon": _num(raw.get("lon")),
            "location_basis": _clean_basis(raw.get("location_basis"), f"{name} {_clean_company(company)}"),
            "mw": round(mw) if mw and mw > 0 else None,
            "mw_basis": _clean_basis(raw.get("mw_basis"), f"{name} {_clean_company(company)}"),
            "status": str(raw.get("status") or "").strip().lower() or "unknown",
            "year": str(raw.get("year") or "").strip(),
            "ai": bool(raw.get("ai")),
            "confidence": str(raw.get("confidence") or "").strip().lower() or "unknown",
            "verification": str(raw.get("verification") or "").strip(),
            "sources": _sources(raw.get("sources")),
            "duplicate_of": None,
            "also_listed_as": [],
            "_listed_dup": _slug(str(raw.get("duplicate_of") or "").strip()) if raw.get("duplicate_of") else None,
        }
        if e["lat"] is not None and e["lon"] is not None and not (-90 <= e["lat"] <= 90 and -180 <= e["lon"] <= 180):
            e["lat"] = e["lon"] = None
        entries[eid] = e

    def fold(keep: dict, drop: dict) -> None:
        drop["duplicate_of"] = keep["id"]
        keep["also_listed_as"].append({"id": drop["id"], "name": drop["name"], "company": drop["company"]})
        seen = {s["url"] for s in keep["sources"]}
        keep["sources"] += [s for s in drop["sources"] if s["url"] not in seen]

    # a duplicate the file names itself (an older listing of a project that was re-reported at another size) folds first
    for e in entries.values():
        target = entries.get(e.pop("_listed_dup") or "")
        if target is not None and target is not e and not target["duplicate_of"] and not e["duplicate_of"]:
            fold(target, e)
    # then fold likely duplicates into one entry: the better-sourced one stays, the other points at it
    items = list(entries.values())
    for i, a in enumerate(items):
        if a["duplicate_of"]:
            continue
        for b in items[i + 1 :]:
            if b["duplicate_of"] or not _same_campus(a, b):
                continue
            keep, drop = a, b
            if (_CONF.get(b["confidence"], 0), len(b["sources"])) > (_CONF.get(a["confidence"], 0), len(a["sources"])):
                keep, drop = b, a
            fold(keep, drop)
            if keep is b:
                break  # a is now a duplicate itself
    for e in entries.values():
        e["counted"] = e["duplicate_of"] is None and e["status"] in IN_PLAY
    return {"generated": doc.get("generated"), "note": str(doc.get("note") or ""), "entries": entries}


_data_lock = threading.Lock()
_data: dict = {"mtime": None, "catalog": None}


def catalog() -> dict:
    """The cleaned catalog, re-read when the file changes (the research workflow may still be
    writing it). 503 while the file is missing or half-written."""
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except OSError:
        raise HTTPException(status_code=503, detail="The data-center catalog isn't available yet") from None
    with _data_lock:
        if _data["mtime"] == mtime and _data["catalog"] is not None:
            return _data["catalog"]
    try:
        doc = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        cat = _normalize(doc)
    except (OSError, ValueError, TypeError, KeyError) as e:
        log.warning("catalog: could not read %s: %s", CATALOG_PATH, e)
        with _data_lock:
            if _data["catalog"] is not None:
                return _data["catalog"]  # keep serving the last good copy
        raise HTTPException(status_code=503, detail="The data-center catalog couldn't be read — try again in a minute") from None
    cat["mtime"] = mtime
    with _data_lock:
        _data["mtime"], _data["catalog"] = mtime, cat
    return cat


# ------------------------------------------------------------------------------------ the test
def _fmt(n: float) -> str:
    """Whole number with commas, rounded half up like the frontend's fmt (Math.round)."""
    return f"{math.floor(float(n) + 0.5):,}"


def _over_text(over: list[dict]) -> str:
    lines = sum(1 for o in over if o["from"] != o["to"])
    xf = len(over) - lines
    parts = []
    if lines:
        parts.append(f"{lines} {'line' if lines == 1 else 'lines'}")
    if xf:
        parts.append(f"{xf} {'transformer' if xf == 1 else 'transformers'}")
    return " and ".join(parts)


def _untested(reason: str) -> dict:
    return {"tested": False, "reason": reason, "verdict": "untested"}


def _areas(g, affected: dict) -> list[dict]:
    """Existing load lost per area (the town each substation is named after), most first."""
    by: dict[str, float] = {}
    for sid, mw in affected.items():
        i = g.sub_index.get(int(sid))
        if i is None:
            continue
        a = area_of(g.sub_name[i])
        by[a] = by.get(a, 0.0) + float(mw)
    rows = sorted(by.items(), key=lambda kv: -kv[1])
    return [{"area": a, "lost_mw": round(mw, 1), "people": g.people(mw)} for a, mw in rows]


def _run(g, c: dict, full: bool, firm: bool) -> dict:
    out = {
        "people": int(c["people"]),
        "lost_mw": c["lost_mw"],
        "steps": c["total_steps"],
        "outcome": c["outcome"],
        "capped": bool(c.get("capped")),
        "site_cut_off": bool(c.get("site_cut_off")),
        "shed_mw": c.get("shed_mw", 0.0),
    }
    if firm:
        out["firm_held"] = c.get("firm_held")
    if full:
        areas = _areas(g, c.get("affected", {}))
        out["areas"] = areas[:TOP_AREAS]
        out["area_count"] = len(areas)
        out["timeline"] = [
            {"n": s["n"], "action": s.get("action"), "people": s.get("people", 0), "lost_mw": s["lost_mw"], "tripped": len(s["tripped"])}
            for s in c["steps"]
        ]
    return out


def _sentence(e: dict, t: dict) -> str:
    """The test in one plain paragraph (a model result, never a claim about the real project)."""
    where = f"On the synthetic {t['region_name']} model, a campus of {_fmt(e['mw'])} MW at this location connects at {t['sub_name']} ({_fmt(t['kv'])} kV)"
    room = f"the site has room for {_fmt(t['headroom_mw'])} MW before a line overloads"
    if t["overloaded"] == 0:
        s = f"{where}. Nothing goes over its limit: {room}."
    else:
        limits = "its limit" if t["overloaded"] == 1 else "their limits"
        s = f"{where} and pushes {t['over_text']} over {limits} ({room})."
        f = t["flexible"]
        if f["people"] > 0:
            s += f" In the model's cascade about {_fmt(f['people'])} people lose power (estimate)"
            s += ", and the campus itself is cut off." if f["site_cut_off"] else "."
        elif f["site_cut_off"]:
            s += " When they trip, the campus itself is cut off; no other customers lose power in the model."
        else:
            s += " The model's cascade settles without cutting anyone off."
    return s


def _why(t: dict) -> str | None:
    """Why a bigger campus here may not black out more people: the flexible cascade cuts the campus
    off (its own lines trip), so past that point size stops mattering. Firm service shows the other side."""
    if t["overloaded"] == 0:
        return None
    f, m = t["flexible"], t["firm"]
    parts = []
    big = t.get("bigger")  # the same campus at twice the size, flexible (measured, not assumed)
    if f["site_cut_off"] and f["people"] > 0 and big:
        lead = "In the flexible run the lines feeding the campus trip and cut it off along with its neighbors"
        if big["people"] == f["people"]:
            parts.append(
                f"{lead}, so a bigger campus here cuts off the same people: at twice the size ({_fmt(big['mw'])} MW) "
                "the model's count is the same."
            )
        elif big["people"] > f["people"]:
            parts.append(
                f"{lead}. At twice the size ({_fmt(big['mw'])} MW) the model gives about {_fmt(big['people'])} people "
                "(estimate): the cascade spreads further before it settles."
            )
        else:
            parts.append(
                f"{lead}. At twice the size ({_fmt(big['mw'])} MW) the model gives fewer, about {_fmt(big['people'])} "
                "people (estimate): the cascade takes a different path, so size alone doesn't set the count."
            )
    elif f["site_cut_off"]:
        parts.append("In the flexible run the lines feeding the campus trip and cut off only the campus.")
    if m.get("firm_held"):
        parts.append(
            "On firm service the grid operator keeps the campus on and cuts other customers instead: "
            + (f"about {_fmt(m['people'])} people (estimate)." if m["people"] > 0 else "here nobody else is cut.")
        )
    elif m.get("firm_held") is False:
        parts.append(
            "Firm service can't hold it here: no load cut nearby relieves the lines feeding the campus enough, "
            "so it is cut off on firm service too"
            + (f" (about {_fmt(m['people'])} people without power, estimate)." if m["people"] > 0 else ".")
        )
    return " ".join(parts) or None


def test_entry(e: dict, full: bool = False) -> dict:
    """One campus on its state's model: the what-if, then a flexible and a firm cascade."""
    code = e["state"]
    if code not in REGIONS:
        return _untested(f"No grid model for {e['state_name'] or 'this place'}: the models cover the lower 48 states.")
    if e["lat"] is None or e["lon"] is None or not e["mw"]:
        return _untested("The catalog entry has no usable location or size.")
    region = REGIONS[code]
    g = grid_at(1.0, code)
    s = g.nearest_sub(e["lat"], e["lon"])
    km = _km(e["lat"], e["lon"], float(g.sub_lat[s]), float(g.sub_lon[s]))
    if km > MAX_SNAP_KM:
        ic = str(region["interconnect"]).replace("Texas (ERCOT)", "ERCOT")
        return _untested(
            f"The {region['name']} model ({ic} interconnection) has no substation within {MAX_SNAP_KM:.0f} km of "
            f"this site (the nearest is {_fmt(km)} km away), so it can't be tested there."
        )
    try:
        check_site(e["lat"], e["lon"], min(e["mw"], MW_MAX), code)  # the same rule the workspace applies
    except HTTPException as ex:
        return _untested(str(ex.detail))
    sites = [SiteIn(lat=e["lat"], lon=e["lon"], mw=float(e["mw"]))]
    extra, header = _case_header(g, sites, [], {})
    state = g.solve(np.ones(g.m, dtype=bool), extra)
    over = g.overloaded(state)
    try:
        flex = g.cascade_case(extra, [], {})
        firm = g.cascade_case(extra, [], {}, firm_buses=case_firm_buses(g, sites, True))
        bigger = None
        if flex.get("site_cut_off") and flex["people"] > 0:
            # "why doesn't a bigger campus black out more people?" — measured at twice the size, never assumed
            extra2, _ = _case_header(g, [SiteIn(lat=e["lat"], lon=e["lon"], mw=2.0 * e["mw"])], [], {})
            bigger = {"mw": 2 * e["mw"], "people": int(g.cascade_case(extra2, [], {})["people"])}
    except (ArithmeticError, ValueError, RuntimeError, np.linalg.LinAlgError):
        log.exception("catalog: cascade failed for %s", e["id"])
        return _untested("The engine couldn't settle this case.")
    t = {
        "tested": True,
        "region": code,
        "region_name": region["name"],
        "interconnect": region["interconnect"],
        "sub_name": header["sub_name"],
        "sub_area": header["sub_area"],
        "sub_lat": header["sub_lat"],
        "sub_lon": header["sub_lon"],
        "kv": header["kv"],
        "km": round(km, 1),
        "headroom_mw": header["headroom_mw"],
        "tested_mw": e["mw"],
        "over_workspace_max": e["mw"] > MW_MAX,
        "overloaded": len(over),
        "over_text": _over_text(over),
        "whatif_people": people_fields(g, state.lost_existing_mw)["people"],
        "flexible": _run(g, flex, full, firm=False),
        "firm": _run(g, firm, full, firm=True),
        "people_per_mw": round(g.people_per_mw, 2),
        "population": g.population,
        "bigger": bigger,  # null unless the flexible run cuts the campus off with people: {mw, people} at 2x (estimate)
    }
    t["people"] = t["flexible"]["people"]  # the headline number (estimate): flexible, the default
    t["verdict"] = "outage" if t["people"] > 0 else "overloads" if t["overloaded"] else "fits"
    t["sentence"] = _sentence(e, t)
    t["why"] = _why(t)
    if full:
        t["lines"] = [
            {
                "from": g.sub_name[g.sub_index[o["from"]]],
                "to": g.sub_name[g.sub_index[o["to"]]],
                "transformer": o["from"] == o["to"],
                "kv": o["kv"],
                "pct": o["pct"],
            }
            for o in over[:TOP_LINES]
        ]
    return t


# ------------------------------------------------------------------------------------ the batch
class _Batch:
    def __init__(self, mtime: float, ids: list[str]):
        self.mtime = mtime
        self.ids = ids
        self.status = "computing"
        self.done = 0
        self.total = len(ids)
        self.results: dict[str, dict] = {}
        self.error: str | None = None
        self.started = time.time()
        self.elapsed: float | None = None


_batch: _Batch | None = None
_batch_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="catalog-batch")


def _order(cat: dict) -> list[str]:
    """Florida first (the demo opens there), then state by state so each model loads once."""
    es = [e for e in cat["entries"].values() if e["duplicate_of"] is None]
    es.sort(key=lambda e: (e["state"] != "FL", e["state"], -(e["mw"] or 0)))
    return [e["id"] for e in es]


def _run_batch(b: _Batch, cat: dict) -> None:
    t0 = time.perf_counter()
    try:
        for eid in b.ids:
            e = cat["entries"][eid]
            try:
                b.results[eid] = test_entry(e)
            except HTTPException as ex:
                b.results[eid] = _untested(str(ex.detail))
            except Exception:  # noqa: BLE001 — one bad entry must not stop the batch
                log.exception("catalog: test failed for %s", eid)
                b.results[eid] = _untested("The engine couldn't settle this case.")
            b.done += 1
            time.sleep(0.005)  # yield: live requests share this process
        b.status = "ready"
    except Exception as ex:  # noqa: BLE001 — surfaced through /api/catalog/status
        log.exception("catalog batch failed")
        b.error = str(ex) or type(ex).__name__
        b.status = "error"
    b.elapsed = round(time.perf_counter() - t0, 2)


def batch() -> tuple[_Batch, dict]:
    """The batch for the current file, started if new (or retried after an error)."""
    global _batch
    cat = catalog()
    with _batch_lock:
        b = _batch
        if b is None or b.mtime != cat["mtime"] or b.status == "error":
            b = _Batch(cat["mtime"], _order(cat))
            _batch = b
            _executor.submit(_run_batch, b, cat)
    return b, cat


def _test_of(b: _Batch, e: dict) -> dict | None:
    canon = e["duplicate_of"] or e["id"]
    return b.results.get(canon)


def _totals(cat: dict, b: _Batch) -> tuple[dict, list[dict]]:
    counted = [e for e in cat["entries"].values() if e["counted"]]
    tests = [(e, b.results.get(e["id"])) for e in counted]
    done = [(e, t) for e, t in tests if t is not None]
    tested = [(e, t) for e, t in done if t.get("tested")]
    by_status: dict[str, dict] = {}
    for e in cat["entries"].values():
        if e["duplicate_of"]:
            continue
        s = by_status.setdefault(e["status"], {"campuses": 0, "mw": 0})
        s["campuses"] += 1
        s["mw"] += e["mw"] or 0
    states: dict[str, dict] = {}
    for e in counted:
        st = states.setdefault(
            e["state"],
            {"state": e["state"], "name": e["state_name"], "campuses": 0, "mw": 0, "tested": 0, "overloads": 0, "outages": 0, "people": 0},
        )
        st["campuses"] += 1
        st["mw"] += e["mw"] or 0
        t = b.results.get(e["id"])
        if t and t.get("tested"):
            st["tested"] += 1
            st["overloads"] += int(t["overloaded"] > 0)
            st["outages"] += int(t["people"] > 0)
            st["people"] += t["people"]
    totals = {
        "campuses": len(counted),
        "mw": sum(e["mw"] or 0 for e in counted),
        "states": len(states),
        "listed": sum(1 for e in cat["entries"].values() if e["duplicate_of"] is None),
        "duplicates": sum(1 for e in cat["entries"].values() if e["duplicate_of"]),
        "not_counted": {"paused_or_canceled": sum(1 for e in cat["entries"].values() if not e["duplicate_of"] and e["status"] not in IN_PLAY)},
        "tested": len(tested),
        "untested": len(done) - len(tested),
        "fits": sum(1 for _, t in tested if t["verdict"] == "fits"),
        "overloads": sum(1 for _, t in tested if t["overloaded"] > 0),
        "outages": sum(1 for _, t in tested if t["people"] > 0),
        "site_cut_off": sum(1 for _, t in tested if t["flexible"]["site_cut_off"]),
        "people": sum(t["people"] for _, t in tested),
        "people_firm": sum(t["firm"]["people"] for _, t in tested),
        "complete": b.status == "ready",
        "by_status": by_status,
        "people_note": "Sum over every counted campus, each tested alone on its state's model (estimate).",
    }
    return totals, sorted(states.values(), key=lambda s: -s["mw"])


def _public(e: dict, full: bool = False) -> dict:
    keep = {k: v for k, v in e.items() if k != "verification" or full}
    return keep


@router.get("/api/catalog")
@limiter.limit("120/minute")
def list_catalog(request: Request):
    """Every campus with its facts and sources; `test` is its summary once the batch has run (null
    while it is computing — poll /api/catalog/status). Totals count unique campuses that are
    operating, under construction or announced."""
    b, cat = batch()
    entries = []
    for e in cat["entries"].values():
        row = _public(e)
        row["test"] = _test_of(b, e)
        entries.append(row)
    totals, by_state = _totals(cat, b)
    return {
        "status": b.status,
        "done": b.done,
        "total": b.total,
        "generated": cat["generated"],
        "note": cat["note"],
        "frame": FRAME,
        "method": METHOD,
        "population_source": POPULATION_SOURCE,
        "workspace_max_mw": MW_MAX,
        "entries": entries,
        "totals": totals,
        "by_state": by_state,
    }


@router.get("/api/catalog/status")
def catalog_status():
    """The batch's progress: {status: computing|ready|error, done, total}. Cheap; the page polls it."""
    b, _ = batch()
    return {"status": b.status, "done": b.done, "total": b.total, "elapsed_s": b.elapsed, "error": b.error}


@router.get("/api/catalog/places")
@limiter.limit("60/minute")
def catalog_places(request: Request, state: str | None = None):
    """The data centers as places, nothing computed: name, company, city, state, coordinates, reported MW,
    status and the first sources. The state picker's dropdown reads this, so choosing a state never starts
    the batch of engine tests behind /api/catalog. `state` filters to one state code.

    Two sources, merged: this catalog's researched entries, and the Compute Atlas facilities (CC BY 4.0;
    backend/demo/datacenters_atlas.json, built by backend/demo/build_datacenters_atlas.py) that have a reported
    size and a point, with every facility that is the same campus as a curated entry already folded into the
    curated one (the curated entry wins). Canceled Atlas sites are left out. Each row carries `origin`."""
    from views import places_from_atlas  # lazy: views.py imports this module lazily too, so neither needs the other at import time

    cat = catalog()
    want = (state or "").strip().upper() or None
    rows = []
    seen = set()
    for e in cat["entries"].values():
        if e["duplicate_of"] is not None or (want and e["state"] != want):
            continue
        row = {k: e.get(k) for k in ("id", "name", "company", "city", "county", "state", "state_name", "lat", "lon", "mw", "mw_basis", "status", "year")}
        row["sources"] = (e.get("sources") or [])[:2]
        row["origin"] = "curated"
        rows.append(row)
        seen.add(e["id"])
    rows += [r for r in places_from_atlas(want) if r["id"] not in seen]
    rows.sort(key=lambda r: -(r["mw"] or 0))
    return {"entries": rows, "note": cat["note"], "frame": FRAME}


_detail_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_detail_lock = threading.Lock()


@router.get("/api/catalog/{entry_id}")
@limiter.limit("120/minute")
def get_entry(request: Request, entry_id: str = PathParam(..., max_length=120)):
    """One campus and its full test on its state's model (flexible and firm)."""
    cat = catalog()
    e = cat["entries"].get(entry_id)
    if e is None:
        raise HTTPException(status_code=404, detail="No data center with that id in the catalog")
    canon = cat["entries"][e["duplicate_of"]] if e["duplicate_of"] else e
    key = (cat["mtime"], canon["id"])
    with _detail_lock:
        t = _detail_cache.get(key)
    if t is None:
        t0 = time.perf_counter()
        try:
            t = test_entry(canon, full=True)
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 — a readable answer, not a 500; not cached, so a retry recomputes
            log.exception("catalog: detail test failed for %s", canon["id"])
            return {**_public(e, full=True), "test": _untested("The engine couldn't settle this case."), "frame": FRAME,
                    "method": METHOD, "population_source": POPULATION_SOURCE, "workspace_max_mw": MW_MAX, "load_factor": 1.0}
        t["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        with _detail_lock:
            _detail_cache[key] = t
            while len(_detail_cache) > 64:
                _detail_cache.popitem(last=False)
    return {
        **_public(e, full=True),
        "test": t,
        "frame": FRAME,
        "method": METHOD,
        "population_source": POPULATION_SOURCE,
        "workspace_max_mw": MW_MAX,
        "load_factor": 1.0,
    }
