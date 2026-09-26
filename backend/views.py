"""Views over the data (owned by the views track): where the data centers are (with a company filter), state
population, and energy (load and generation by fuel). Read-only, from committed data only: no engine run,
nothing computed per request beyond filtering a list in memory. See CLAUDE.md -> Decisions -> VIEWS.

  GET /api/views/datacenters          the sites (filters: state, company, status, min_mw, q, kind, origin), the totals
                                      for the selection (reported MW, and each state's share of its model's load) and
                                      the facets that drive the filter chips
  GET /api/views/datacenters/{id}     one site: the sourced facts, how big and how sure, who else lists it
  GET /api/views/population           each state's residents, its model's load and people per MW, reported data-center MW
  GET /api/views/energy               each state's model load and generation capacity by fuel; the national mix

Where the sites come from (each carries its `origin`, and the page credits it):
  curated       the Overload catalog (backend/demo/datacenters_us.json, researched and sourced; via catalog.catalog())
  compute-atlas Compute Atlas (Kubiak, E.), CC BY 4.0 (backend/demo/datacenters_atlas.json)
  epoch-ai      Epoch AI, AI data centers, CC BY (backend/demo/datacenters_epoch.json)
Both open files are built by backend/demo/build_datacenters_atlas.py, which also folds the same campus listed
twice into one row (the curated entry wins) and normalizes company names.

Framing (Decisions -> NO DEFAMATION): the grid models contain no real campus. A site's reported MW is compared to
its state's model load only as a scale ("a campus of this reported size would be N% of the model's load");
nothing here says a company or a site causes an outage, and every test is on a SYNTHETIC grid model.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam

from grid import POPULATION, POPULATION_SOURCE, REGION_SOURCE, REGIONS, people_per_mw
from limiter import limiter

router = APIRouter(tags=["views"])
log = logging.getLogger("uvicorn.error")

DEMO = Path(__file__).parent / "demo"
ATLAS_PATH = DEMO / "datacenters_atlas.json"
EPOCH_PATH = DEMO / "datacenters_epoch.json"
PLANTS_DIR = DEMO / "plants"

FRAME = (
    "Reported sizes and places, as reported by the sources linked on each site. The grid models are SYNTHETIC "
    "(Breakthrough Energy / Texas A&M, CC-BY 4.0) and contain no real data center: a site's reported MW is "
    "compared with its state's model load only as a scale, never as a cause. Testing a site means a campus of "
    "that reported size at that location on a synthetic model, not a prediction about the real project or utility."
)
CREDITS = [
    {"origin": "curated", "label": "Overload catalog", "text": "Researched and sourced by Overload from public news and company sources; every row links its sources."},
    {
        "origin": "compute-atlas",
        "label": "Compute Atlas",
        "text": "Compute Atlas (Kubiak, E.), CC BY 4.0, https://www.compute-atlas.com, doi 10.5281/zenodo.22284476. Slimmed and de-duplicated by Overload.",
        "url": "https://www.compute-atlas.com",
    },
    {
        "origin": "epoch-ai",
        "label": "Epoch AI",
        "text": "Epoch AI, AI data centers (epoch.ai), CC BY. Slimmed by Overload; its Users column is not used.",
        "url": "https://epoch.ai/data/ai-data-centers",
    },
]
ORIGIN_LABEL = {c["origin"]: c["label"] for c in CREDITS}
STATUS_ORDER = ["operating", "under construction", "announced", "paused/canceled", "unknown"]
IN_PLAY = ("operating", "under construction", "announced", "unknown")  # the default selection: canceled and paused sites are opt-in
KINDS = ("data_center", "crypto_mining")
EXTRA_NAMES = {
    "DC": "District of Columbia", "AK": "Alaska", "HI": "Hawaii", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "U.S. Virgin Islands", "MP": "Northern Mariana Islands",
}
FUEL_ORDER = ["nuclear", "coal", "gas", "oil", "hydro", "wind", "offshore wind", "solar", "geothermal", "other"]
MAX_SITES = 5000


def state_name(code: str | None) -> str:
    if not code:
        return ""
    return REGIONS[code]["name"] if code in REGIONS else EXTRA_NAMES.get(code, code)


# ------------------------------------------------------------------------------------ loading (cached)
_lock = threading.Lock()
_cache: dict = {"key": None, "data": None}
_energy: dict = {"data": None}


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("views: could not read %s: %s", path.name, e)
        return {}


_docs: dict = {}


def _doc(path: Path) -> dict:
    """A source file, parsed once and re-read only when it changes."""
    mt = _mtime(path)
    with _lock:
        hit = _docs.get(path.name)
        if hit and hit[0] == mt:
            return hit[1]
    doc = _read(path)
    with _lock:
        _docs[path.name] = (mt, doc)
    return doc


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _site_from_open(e: dict, origin: str) -> dict:
    """One row of datacenters_atlas.json / datacenters_epoch.json, in the views' shape."""
    st = (e.get("state") or "").upper() or None
    parties = list(e.get("parties") or [e.get("company") or "Undisclosed"])
    return {
        "id": e["id"],
        "name": e.get("name") or "",
        "operator": e.get("operator") or "",
        "company": e.get("company") or parties[0],
        "parties": parties,
        "kind": e.get("kind") or "data_center",
        "status": e.get("status") or "unknown",
        "status_detail": e.get("status_detail") or "",
        "state": st,
        "city": e.get("city") or "",
        "county": e.get("county") or "",
        "lat": _num(e.get("lat")),
        "lon": _num(e.get("lon")),
        "precision": e.get("precision"),
        "mw": _num(e.get("mw")),
        "mw_basis": e.get("mw_basis") or "",
        "mw_planned": _num(e.get("mw_planned")),
        "mw_operational": _num(e.get("mw_operational")),
        "confidence": e.get("confidence") or "",
        "announced": e.get("announced"),
        "updated": e.get("updated"),
        "address": e.get("address"),
        "sources": [s for s in (e.get("sources") or []) if str(s.get("url") or "").startswith(("http://", "https://"))],
        "origin": origin,
        "also": [],
    }


def _site_from_curated(e: dict, companies: dict) -> dict:
    """One cleaned entry of catalog.catalog() (backend/catalog.py), in the views' shape."""
    norm = companies.get(e["id"]) or {}
    parties = list(norm.get("parties") or [e.get("company") or "Undisclosed"])
    st = (e.get("state") or "").upper() or None
    return {
        "id": e["id"],
        "name": e["name"],
        "operator": e.get("company") or "",
        "company": norm.get("company") or parties[0],
        "parties": parties,
        "kind": "data_center",
        "status": e.get("status") or "unknown",
        "status_detail": e.get("year") or "",
        "state": st,
        "city": e.get("city") or "",
        "county": e.get("county") or "",
        "lat": _num(e.get("lat")),
        "lon": _num(e.get("lon")),
        "precision": "approximate" if e.get("location_basis") else None,
        "location_basis": e.get("location_basis") or "",
        "mw": _num(e.get("mw")),
        "mw_basis": e.get("mw_basis") or "",
        "mw_planned": None,
        "mw_operational": None,
        "confidence": e.get("confidence") or "",
        "announced": None,
        "updated": None,
        "address": None,
        "sources": [{"title": s.get("title") or s["url"], "publisher": "", "url": s["url"], "supports": s.get("supports") or ""} for s in (e.get("sources") or [])][:4],
        "origin": "curated",
        "year": e.get("year") or "",
        "also": [],
    }


def _haystack(s: dict) -> str:
    return " ".join([s["name"], s["operator"], " ".join(s["parties"]), s["city"], s["county"], s["state"] or "", state_name(s["state"])]).lower()


def _load() -> dict:
    """Every site from the three sources, merged into one list and indexed. Re-read when a source file changes."""
    from catalog import catalog  # lazy: catalog.py imports this module lazily too, so neither needs the other at import time

    try:
        cat = catalog()
        cur_key = cat["mtime"]
    except HTTPException:
        cat, cur_key = {"entries": {}}, None
    key = (cur_key, _mtime(ATLAS_PATH), _mtime(EPOCH_PATH))
    with _lock:
        if _cache["key"] == key and _cache["data"] is not None:
            return _cache["data"]
    atlas_doc, epoch_doc = _doc(ATLAS_PATH), _doc(EPOCH_PATH)
    companies = atlas_doc.get("curated_companies") or {}
    sites: list[dict] = [_site_from_curated(e, companies) for e in cat["entries"].values() if not e["duplicate_of"]]
    by_id = {s["id"]: s for s in sites}
    for e in atlas_doc.get("entries", []):
        if e.get("id") and e["id"] not in by_id:
            s = _site_from_open(e, "compute-atlas")
            sites.append(s)
            by_id[s["id"]] = s
    for m in atlas_doc.get("merged", []):  # a Compute Atlas listing folded into a curated campus
        tgt = by_id.get(m.get("curated_id"))
        if tgt is not None:
            tgt["also"].append({"origin": "compute-atlas", "name": m.get("atlas_name") or "", "mw": None, "why": m.get("why") or ""})
    for e in epoch_doc.get("entries", []):
        if not e.get("id"):
            continue
        s = _site_from_open(e, "epoch-ai")
        dup = e.get("duplicate_of")
        if dup and dup in by_id:  # the same campus another source lists: shown on that site's card, not counted twice
            by_id[dup]["also"].append({"origin": "epoch-ai", "name": s["name"], "mw": s["mw"], "why": e.get("duplicate_why") or "", "sources": s["sources"], "mw_basis": s["mw_basis"]})
        elif e["id"] not in by_id:
            sites.append(s)
            by_id[s["id"]] = s
    for s in sites:
        # a site with no reported size takes the biggest figure another source gives it, labelled as that source's
        if not s["mw"]:
            sized = [a for a in s["also"] if a.get("mw")]
            if sized:
                best = max(sized, key=lambda a: a["mw"])
                s["mw"], s["mw_basis"], s["mw_from"] = best["mw"], best.get("mw_basis") or "", best["origin"]
        s["_hay"] = _haystack(s)
    data = {
        "sites": sites,
        "by_id": by_id,
        "generated": {"atlas": atlas_doc.get("generated"), "epoch": epoch_doc.get("generated")},
        "skipped": {"atlas": atlas_doc.get("skipped") or {}, "epoch": epoch_doc.get("skipped") or {}},
    }
    with _lock:
        _cache["key"], _cache["data"] = key, data
    return data


# ------------------------------------------------------------------------------------ places (for catalog.py)
def places_from_atlas(want: str | None = None) -> list[dict]:
    """The Compute Atlas sites that can be tested as a campus (a data center with a size and a point), in the
    shape /api/catalog/places returns. Nothing is computed; canceled sites and sites whose point is only a
    portfolio's representative point are left out."""
    doc = _doc(ATLAS_PATH)
    rows = []
    for e in doc.get("entries", []):
        if e.get("kind") != "data_center" or not e.get("mw") or e.get("lat") is None or e.get("lon") is None:
            continue
        if e.get("status") == "paused/canceled" or e.get("precision") == "representative_multi_site":
            continue
        st = (e.get("state") or "").upper()
        if want and st != want:
            continue
        rows.append(
            {
                "id": e["id"], "name": e["name"], "company": e.get("operator") or e.get("company"), "city": e.get("city") or "",
                "county": e.get("county") or "", "state": st, "state_name": state_name(st), "lat": e["lat"], "lon": e["lon"],
                "mw": e["mw"], "mw_basis": e.get("mw_basis") or "", "status": e.get("status") or "unknown", "year": e.get("announced") or "",
                "sources": [{"title": s.get("title") or s["url"], "url": s["url"]} for s in (e.get("sources") or [])][:2],
                "origin": "compute-atlas",
            }
        )
    return rows


# ------------------------------------------------------------------------------------ filtering + totals
def _split(values: list[str] | None) -> list[str]:
    """Query values, each possibly a comma-separated list ("TX,VA")."""
    out: list[str] = []
    for v in values or []:
        out += [p.strip() for p in str(v).split(",")]
    return [v for v in out if v]


def _passes(s: dict, f: dict, skip: str | None = None) -> bool:
    if skip != "state" and f["states"] and s["state"] not in f["states"]:
        return False
    if skip != "company" and f["companies"] and not ({p.lower() for p in s["parties"]} & f["companies"]):
        return False
    if skip != "status" and s["status"] not in f["statuses"]:
        return False
    if skip != "kind" and f["kind"] != "all" and s["kind"] != f["kind"]:
        return False
    if skip != "origin" and f["origin"] and s["origin"] != f["origin"]:
        return False
    if f["min_mw"] and (s["mw"] or 0) < f["min_mw"]:
        return False
    if f["q"] and not all(w in s["_hay"] for w in f["q"]):
        return False
    return True


def _facets(sites: list[dict], f: dict) -> dict:
    def tally(skip: str, keys) -> dict:
        t: dict = defaultdict(lambda: {"sites": 0, "mw": 0})
        for s in sites:
            if not _passes(s, f, skip):
                continue
            for k in keys(s):
                t[k]["sites"] += 1
                t[k]["mw"] += s["mw"] or 0
        return t

    co = tally("company", lambda s: s["parties"])
    st = tally("status", lambda s: [s["status"]])
    sa = tally("state", lambda s: [s["state"]] if s["state"] else [])
    kd = tally("kind", lambda s: [s["kind"]])
    return {
        "companies": [{"company": k, "sites": v["sites"], "mw": round(v["mw"])} for k, v in sorted(co.items(), key=lambda kv: (-kv[1]["mw"], -kv[1]["sites"], kv[0]))],
        "statuses": [{"status": k, "sites": st[k]["sites"], "mw": round(st[k]["mw"])} for k in STATUS_ORDER if k in st],
        "states": [{"state": k, "name": state_name(k), "sites": v["sites"], "mw": round(v["mw"])} for k, v in sorted(sa.items(), key=lambda kv: (-kv[1]["mw"], kv[0]))],
        "kinds": [{"kind": k, "sites": kd[k]["sites"], "mw": round(kd[k]["mw"])} for k in KINDS if k in kd],
    }


def _totals(rows: list[dict]) -> dict:
    by_state: dict = defaultdict(lambda: {"sites": 0, "mw": 0, "sized": 0})
    by_status: dict = defaultdict(lambda: {"sites": 0, "mw": 0})
    for s in rows:
        code = s["state"] or "??"
        by_state[code]["sites"] += 1
        by_state[code]["mw"] += s["mw"] or 0
        by_state[code]["sized"] += 1 if s["mw"] else 0
        by_status[s["status"]]["sites"] += 1
        by_status[s["status"]]["mw"] += s["mw"] or 0
    states = []
    for code, v in by_state.items():
        load = float(REGIONS[code]["load_mw"]) if code in REGIONS else None
        states.append(
            {
                "state": code, "name": state_name(code), "sites": v["sites"], "sized": v["sized"], "mw": round(v["mw"]),
                "model_load_mw": round(load) if load else None,
                "share_pct": round(100.0 * v["mw"] / load, 1) if load else None,
            }
        )
    states.sort(key=lambda r: (-r["mw"], r["state"]))
    modeled = [r for r in states if r["model_load_mw"]]
    mw_modeled = sum(r["mw"] for r in modeled)
    load_modeled = sum(r["model_load_mw"] for r in modeled)
    return {
        "sites": len(rows),
        "sized": sum(1 for s in rows if s["mw"]),
        "mw": round(sum(s["mw"] or 0 for s in rows)),
        "placed": sum(1 for s in rows if s["lat"] is not None),
        "companies": len({p for s in rows for p in s["parties"]}),
        "states": len([r for r in states if r["state"] != "??"]),
        "by_status": [{"status": k, "sites": by_status[k]["sites"], "mw": round(by_status[k]["mw"])} for k in STATUS_ORDER if k in by_status],
        "by_state": states,
        "share_of_model_load_pct": round(100.0 * mw_modeled / load_modeled, 1) if load_modeled else None,
        "modeled_states": len(modeled),
        "note": "Reported MW as a share of the state models' base load: a scale comparison, not an effect. The models contain no real campus.",
    }


def _public_row(s: dict) -> dict:
    return {
        "id": s["id"], "name": s["name"], "company": s["company"], "status": s["status"], "kind": s["kind"],
        "state": s["state"], "city": s["city"], "lat": s["lat"], "lon": s["lon"], "mw": s["mw"], "origin": s["origin"],
    }


@router.get("/api/views/datacenters")
@limiter.limit("60/minute")
def views_datacenters(
    request: Request,
    state: list[str] | None = Query(None, description="state code(s), e.g. TX"),
    company: list[str] | None = Query(None, description="company name(s) as listed in facets.companies; a site matches any of its parties"),
    status: list[str] | None = Query(None, description="operating, under construction, announced, paused/canceled, unknown, or all (default: everything but paused/canceled)"),
    min_mw: float = Query(0, ge=0, le=100000),
    q: str = Query("", max_length=80, description="words that must all appear in the name, company, city, county or state"),
    kind: str = Query("data_center", pattern="^(data_center|crypto_mining|all)$"),
    origin: str | None = Query(None, pattern="^(curated|compute-atlas|epoch-ai)$"),
    limit: int = Query(3000, ge=1, le=MAX_SITES),
):
    """The sites that match, biggest first (a site without a reported size comes last), the totals for that
    selection and the facets. Facet counts ignore their own filter, so the chips stay visible while one is on."""
    data = _load()
    sts = _split(status)
    f = {
        "states": {v.upper() for v in _split(state)},
        "companies": {c.strip().lower() for c in (company or []) if c.strip()},  # names may contain commas: one per parameter
        "statuses": set(STATUS_ORDER) if "all" in [x.lower() for x in sts] else (set(x.lower() for x in sts) or set(IN_PLAY)),
        "min_mw": min_mw,
        "q": [w for w in re.findall(r"[a-z0-9]+", q.lower())],
        "kind": kind,
        "origin": origin,
    }
    sites = data["sites"]
    rows = [s for s in sites if _passes(s, f)]
    rows.sort(key=lambda s: (-(s["mw"] or 0), s["name"].lower()))
    return {
        "frame": FRAME,
        "credits": CREDITS,
        "generated": data["generated"],
        "total_sites": len(sites),
        "selected": len(rows),
        "sites": [_public_row(s) for s in rows[:limit]],
        "truncated": len(rows) > limit,
        "totals": _totals(rows),
        "facets": _facets(sites, f),
        "statuses_default": list(IN_PLAY),
    }


@router.get("/api/views/datacenters/{site_id}")
@limiter.limit("120/minute")
def views_datacenter(request: Request, site_id: str = PathParam(..., max_length=160)):
    """One site's card: the sourced facts as reported, how sure the source is, and who else lists it."""
    s = _load()["by_id"].get(site_id)
    if s is None:
        raise HTTPException(status_code=404, detail="No data center with that id")
    load = float(REGIONS[s["state"]]["load_mw"]) if s["state"] in REGIONS else None
    out = {k: v for k, v in s.items() if not k.startswith("_")}
    out.update(
        {
            "state_name": state_name(s["state"]),
            "origin_label": ORIGIN_LABEL.get(s["origin"], s["origin"]),
            "has_model": s["state"] in REGIONS,
            "model_load_mw": round(load) if load else None,
            "share_of_model_load_pct": round(100.0 * s["mw"] / load, 2) if load and s["mw"] else None,
            "can_test": bool(s["state"] in REGIONS and s["lat"] is not None and s["mw"]),
            "frame": FRAME,
        }
    )
    return out


# ------------------------------------------------------------------------------------ population
_datacenter_mw_cache: dict = {"key": None, "by_state": None}


def _dc_by_state() -> dict[str, dict]:
    data = _load()
    key = id(data)
    if _datacenter_mw_cache["key"] == key:
        return _datacenter_mw_cache["by_state"]
    t: dict = defaultdict(lambda: {"sites": 0, "mw": 0})
    for s in data["sites"]:
        if s["kind"] != "data_center" or s["status"] not in IN_PLAY or not s["state"]:
            continue
        t[s["state"]]["sites"] += 1
        t[s["state"]]["mw"] += s["mw"] or 0
    _datacenter_mw_cache["key"], _datacenter_mw_cache["by_state"] = key, dict(t)
    return _datacenter_mw_cache["by_state"]


@router.get("/api/views/population")
@limiter.limit("60/minute")
def views_population(request: Request):
    """Each state model's residents, base load and people per MW (the ratio behind every "people without power"
    estimate), with the reported data-center MW in the state (operating, under construction or announced)."""
    dc = _dc_by_state()
    rows = []
    for code, r in REGIONS.items():
        pop = POPULATION.get(code)
        load = float(r["load_mw"])
        d = dc.get(code, {"sites": 0, "mw": 0})
        rows.append(
            {
                "code": code, "name": r["name"], "population": pop, "model_load_mw": round(load), "gen_mw": round(float(r["gen_mw"])),
                "people_per_mw": round(people_per_mw(code), 1),
                "dc_sites": d["sites"], "dc_mw": round(d["mw"]),
                "dc_mw_per_100k": round(100000.0 * d["mw"] / pop, 1) if pop else None,
                "dc_share_of_load_pct": round(100.0 * d["mw"] / load, 1) if load else None,
            }
        )
    rows.sort(key=lambda r: -(r["population"] or 0))
    tot_pop = sum(r["population"] or 0 for r in rows)
    tot_load = sum(r["model_load_mw"] for r in rows)
    return {
        "source": POPULATION_SOURCE,
        "model_source": REGION_SOURCE,
        "states": rows,
        "total_population": tot_pop,
        "total_model_load_mw": tot_load,
        "people_per_mw": round(tot_pop / tot_load, 1) if tot_load else None,
        "note": "People per MW is a state's residents divided by its model's base load: the ratio the app uses to turn lost MW into an estimate of people without power.",
        "frame": FRAME,
    }


# ------------------------------------------------------------------------------------ energy
def _load_energy() -> dict:
    with _lock:
        if _energy["data"] is not None:
            return _energy["data"]
    states = []
    nat_cap: dict = defaultdict(float)
    nat_out: dict = defaultdict(float)
    nat_n: dict = defaultdict(int)
    for code, r in REGIONS.items():
        doc = _read(PLANTS_DIR / f"{code}.json")  # read once: the whole result is cached
        cap: dict = defaultdict(float)
        out: dict = defaultdict(float)
        n = 0
        for p in doc.get("plants", []):
            fuel = str(p.get("fuel") or "other")
            cap[fuel] += float(p.get("pmax") or 0)
            out[fuel] += float(p.get("pg") or 0)
            nat_cap[fuel] += float(p.get("pmax") or 0)
            nat_out[fuel] += float(p.get("pg") or 0)
            nat_n[fuel] += 1
            n += 1
        load = float(r["load_mw"])
        total = sum(cap.values())
        states.append(
            {
                "code": code, "name": r["name"], "load_mw": round(load), "capacity_mw": round(total), "dispatch_mw": round(sum(out.values())),
                "plants": n, "capacity_over_load": round(total / load, 2) if load else None,
                "by_fuel": {k: round(cap[k]) for k in sorted(cap, key=lambda k: FUEL_ORDER.index(k) if k in FUEL_ORDER else 99)},
                "dispatch_by_fuel": {k: round(out[k]) for k in sorted(out, key=lambda k: FUEL_ORDER.index(k) if k in FUEL_ORDER else 99)},
            }
        )
    states.sort(key=lambda s: -s["load_mw"])
    total_cap = sum(nat_cap.values()) or 1.0
    fuels = sorted(nat_cap, key=lambda k: FUEL_ORDER.index(k) if k in FUEL_ORDER else 99)
    data = {
        "source": REGION_SOURCE,
        "fuels": fuels,
        "states": states,
        "national": {
            "load_mw": sum(s["load_mw"] for s in states),
            "capacity_mw": round(total_cap),
            "plants": sum(nat_n.values()),
            "by_fuel": [
                {"fuel": k, "capacity_mw": round(nat_cap[k]), "share_pct": round(100.0 * nat_cap[k] / total_cap, 1), "dispatch_mw": round(nat_out[k]), "plants": nat_n[k]}
                for k in fuels
            ],
        },
        "note": (
            "Installed generation capacity by fuel in each state's SYNTHETIC grid model (Breakthrough Energy / Texas A&M test system): "
            "plants sit on the model's synthetic buses and are named after its substations. Capacity is not output, and the mix is the "
            "dataset's snapshot, not today's real grid. Model load is the state model's base load."
        ),
        "frame": FRAME,
    }
    with _lock:
        _energy["data"] = data
    return data


@router.get("/api/views/energy")
@limiter.limit("60/minute")
def views_energy(request: Request):
    """Each state model's load and its generation capacity by fuel, and the national mix. Cached after the first read
    (48 small files, tens of milliseconds)."""
    return _load_energy()
