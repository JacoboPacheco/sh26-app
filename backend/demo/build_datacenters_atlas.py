#!/usr/bin/env python
"""Build the two committed data-center source files from the open datasets in raw/datacenters/.

    backend/venv/Scripts/python backend/demo/build_datacenters_atlas.py [--report]

Reads (gitignored downloads; the user approved both):
  raw/datacenters/compute_atlas_v1.34.0_facilities.json   Compute Atlas (Kubiak, E.), CC BY 4.0,
        https://www.compute-atlas.com, doi 10.5281/zenodo.22284476
  raw/datacenters/epoch_data_centers.csv                  Epoch AI, AI data centers (epoch.ai), CC BY
and the curated catalog datacenters_us.json (the curated entry always wins a duplicate).

Writes:
  datacenters_atlas.json   Compute Atlas facilities, slimmed: id, name, operator as reported, the
                           normalized company, status, state, city, county, lat, lon, MW with its basis,
                           confidence, the first two sources. Left out: power-generation sites (not a
                           load), rumored entries, and every facility that is the same campus as a
                           curated entry (listed under `merged`).
  datacenters_epoch.json   Epoch AI's U.S. rows, slimmed the same way. Epoch's Users column is NOT read
                           (Decisions -> NO DEFAMATION: never name undisclosed tenants). A row that is the
                           same campus as a curated or Compute Atlas entry stays in the file marked
                           `duplicate_of`, so the views can show both sources without counting twice.
                           Epoch has no coordinates: a row takes its matched campus's point, else the
                           mean point of the Compute Atlas sites in its city, else it is unplaced.

Both files also carry `curated_companies` (the curated entries' company names normalized the same way),
so backend/views.py and backend/catalog.py never need this logic at run time. Nothing here is fetched
from the network, and nothing runs at request time.

Company names: an alias map folds "Amazon Web Services", "AWS", "Amazon Data Services" into Amazon,
"Meta Platforms" into Meta, and so on; a joint operator ("Google / Crusoe") keeps every party in
`parties`. `operator` stays as reported, except that anything starting "Undisclosed" is cut to
"Undisclosed" (its parenthetical names landowners and applicants: private parties we don't repeat).
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
RAW = HERE / "raw" / "datacenters"
ATLAS_RAW = RAW / "compute_atlas_v1.34.0_facilities.json"
EPOCH_RAW = RAW / "epoch_data_centers.csv"
CURATED = HERE / "datacenters_us.json"
ATLAS_OUT = HERE / "datacenters_atlas.json"
EPOCH_OUT = HERE / "datacenters_epoch.json"

ATLAS_CREDIT = (
    "Compute Atlas (Kubiak, E.), CC BY 4.0, https://www.compute-atlas.com, doi 10.5281/zenodo.22284476. "
    "Slimmed and de-duplicated by Overload; every figure is as reported by the sources it cites."
)
EPOCH_CREDIT = "Epoch AI, AI data centers (epoch.ai), CC BY. Slimmed by Overload; the Users column is not used."

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
STATE_CODES = set(STATE_NAMES.values())

# ------------------------------------------------------------------------------------ companies
# (pattern on the cleaned party name, canonical name), first match wins. Anything else keeps its own
# cleaned name (legal suffixes and parentheticals removed).
ALIASES: list[tuple[str, str]] = [
    (r"^(amazon|aws\b)", "Amazon"),
    (r"^google", "Google"),
    (r"^microsoft", "Microsoft"),
    (r"^(meta\b|facebook)", "Meta"),
    (r"^oracle", "Oracle"),
    (r"^openai", "OpenAI"),
    (r"^softbank", "SoftBank"),
    (r"^(x\.?ai\b|space ?x ?ai)", "xAI"),
    (r"^apple$", "Apple"),
    (r"^qts\b", "QTS"),
    (r"^digital realty", "Digital Realty"),
    (r"^ntt\b", "NTT"),
    (r"^lumen", "Lumen Technologies"),
    (r"^iron mountain", "Iron Mountain"),
    (r"^cyrusone", "CyrusOne"),
    (r"^edgeconnex", "EdgeConneX"),
    (r"^nextera", "NextEra Energy"),
    (r"^alliant", "Alliant Energy"),
    (r"^dominion", "Dominion Energy"),
    (r"^duke energy", "Duke Energy"),
    (r"^entergy", "Entergy"),
    (r"^rwe", "RWE"),
    (r"^centersquare", "Centersquare"),
    (r"^cipher", "Cipher Digital"),
    (r"^crusoe", "Crusoe"),
    (r"^stream (u\.?s\.? )?data", "Stream Data Centers"),
    (r"^us signal|^u\.s\. signal", "US Signal"),
    (r"^prime data", "Prime Data Centers"),
    (r"^edged", "Edged"),
    (r"^novva", "Novva Data Centers"),
    (r"^powerhouse", "PowerHouse Data Centers"),
    (r"^applied digital", "Applied Digital"),
    (r"^american tower", "American Tower"),
    (r"^bitfarms", "Bitfarms"),
    (r"^hut 8", "Hut 8"),
    (r"^iren\b", "IREN"),
    (r"^lightedge", "LightEdge"),
    (r"^serverfarm", "Serverfarm"),
    (r"^takanock", "Takanock"),
    (r"^soluna", "Soluna"),
    (r"^oppidan", "Oppidan"),
    (r"^cologix", "Cologix"),
    (r"^t5 data", "T5 Data Centers"),
    (r"^stonebridge", "Stonebridge"),
    (r"^greenidge", "Greenidge"),
    (r"^desri", "DESRI"),
    (r"^rowan", "Rowan Digital Infrastructure"),
    (r"^raeden", "Raeden"),
    (r"^firstlight", "FirstLight"),
    (r"^brookfield", "Brookfield"),
    (r"^aes\b", "AES"),
    (r"^nydig", "NYDIG"),
    (r"^cielo", "Cielo Digital Infrastructure"),
    (r"^netrality", "Netrality"),
    (r"^natelli", "Natelli"),
    (r"^beacon data", "Beacon Data Centers"),
    (r"^aligned", "Aligned Data Centers"),
    (r"^poolside", "Poolside"),
    (r"^atlas compute", "Atlas Compute"),
    (r"^galaxy", "Galaxy"),
    (r"^riot", "Riot Platforms"),
    (r"^terawulf", "TeraWulf"),
    (r"^core ?scientific", "Core Scientific"),
    (r"^coreweave", "CoreWeave"),
    (r"^vantage", "Vantage Data Centers"),
    (r"^stack\b", "STACK Infrastructure"),
    (r"^tract", "Tract"),
    (r"^ntt", "NTT"),
]
LEGAL = re.compile(r"[,\s]+(?:inc|llc|l\.l\.c|corp|corporation|holdings|holding|ltd|lp|l\.p|co|company|plc)\b\.?\s*$", re.I)
UNDISCLOSED = re.compile(r"^\s*(undisclosed|unknown|not disclosed|not confirmed|developer not confirmed)", re.I)
SPECULATION = re.compile(r"specul|rumou?r|allegedly|reportedly|unconfirmed|not confirmed", re.I)


def strip_parens(s: str) -> str:
    prev = None
    while prev != s:
        prev, s = s, re.sub(r"\([^()]*\)", " ", s)
    return s


def clean_party(p: str) -> str:
    p = re.sub(r"\s+", " ", strip_parens(p)).strip(" ;,-")
    for _ in range(3):
        q = LEGAL.sub("", p).strip(" ;,-")
        if q == p:
            break
        p = q
    for pat, canon in ALIASES:
        if re.match(pat, p, re.I):
            return canon
    return p


def parties_of(raw: str | None) -> list[str]:
    """The canonical company names in an operator string; ['Undisclosed'] when it says so."""
    s = str(raw or "").strip()
    if not s or UNDISCLOSED.match(s) or SPECULATION.search(strip_parens(s)):
        return ["Undisclosed"]
    s = re.sub(r"\s+[—–]\s+.*$", "", strip_parens(s))  # "Prime Storage (...) — end user undisclosed"
    out: list[str] = []
    for part in re.split(r"\s+/\s+|;", s):
        c = clean_party(part)
        if c and c.lower() not in {x.lower() for x in out}:
            out.append(c)
    return out or ["Undisclosed"]


def operator_as_reported(raw: str | None) -> str:
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not s or UNDISCLOSED.match(s):
        return "Undisclosed"
    return s[:160]


# ------------------------------------------------------------------------------------ helpers
def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1.0, a)))


STOP = {"data", "center", "centers", "campus", "the", "of", "and", "ai", "project", "park", "datacenter", "datacenters",
        "technology", "technologies", "llc", "inc", "corp", "site", "facility", "phase", "north", "south", "east", "west",
        "hyperscale", "digital", "infrastructure", "county", "city"}


def toks(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", str(name).lower()) if t not in STOP and len(t) > 1}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:90] or "site"


def clip(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def mw_int(v) -> int | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < 1:
        return None
    return int(round(f))


def num(v, nd=5):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if math.isfinite(f) else None


STATUS = {
    "operational": "operating",
    "under_construction": "under construction",
    "proposed": "announced",
    "permitted": "announced",
    "cancelled": "paused/canceled",
    "canceled": "paused/canceled",
}


# ------------------------------------------------------------------------------------ curated + atlas
def load_curated() -> list[dict]:
    doc = json.loads(CURATED.read_text(encoding="utf-8"))
    out = []
    for e in doc.get("entries", []):
        if e.get("lat") is None or e.get("lon") is None:
            continue
        out.append(
            {
                "id": e["id"], "name": e.get("name") or "", "state": (e.get("state") or "").upper(),
                "lat": float(e["lat"]), "lon": float(e["lon"]), "mw": e.get("mw"),
                "parties": parties_of(e.get("company")), "city": e.get("city") or "", "county": e.get("county") or "",
            }
        )
    return out


def curated_companies() -> dict[str, dict]:
    doc = json.loads(CURATED.read_text(encoding="utf-8"))
    res = {}
    for e in doc.get("entries", []):
        ps = parties_of(e.get("company"))
        res[e["id"]] = {"company": ps[0], "parties": ps}
    return res


def atlas_mw(f: dict) -> tuple[int | None, str, int | None, int | None]:
    cap = f.get("capacityMw") or {}
    op, pl = mw_int(cap.get("operational")), mw_int(cap.get("planned"))
    st = f["status"]
    if st == "operational" and op:
        mw = op
        basis = f"{op:,} MW operating capacity as reported (Compute Atlas)"
        if pl and pl > op:
            basis += f"; {pl:,} MW planned at full build"
    elif pl:
        mw = max(pl, op or 0)
        basis = f"{mw:,} MW planned capacity as reported (Compute Atlas)"
    elif op:
        mw = op
        basis = f"{op:,} MW capacity as reported (Compute Atlas)"
    else:
        return None, "No capacity reported (Compute Atlas)", op, pl
    return mw, basis, op, pl


GENERIC = STOP | {"realty", "new", "mega", "industrial", "expansion", "energy", "power", "gigasite", "building", "tech", "cloud",
                  "factory", "hpc", "colocation", "colo", "corridor", "gateway", "campuses", "valley", "hyperscale", "ai"}


PROGRAM: set[str] = set()  # words in 4+ campus names ("stargate", "project"): a program, not one campus
CITY_POOL: list[tuple[str, str, float, float]] = []  # every Compute Atlas site, kept or merged: a city gazetteer for Epoch rows
PLACES: set[str] = set()  # words that are place names (a city used by 3+ records, a state): filled by main()


def numbers(name: str) -> set[int]:
    return {int(t) for t in re.findall(r"(?<![A-Za-z0-9])\d{1,2}(?![A-Za-z0-9])", name)}


def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", strip_parens(name).lower())


def match(a: dict, b: dict) -> tuple[float, str] | None:
    """Why two records look like one campus: (score, reason), or None. Conservative on purpose: Compute Atlas
    points are often a city's centre, so distance alone proves nothing (two campuses can share a point), a
    company's own name in both records proves nothing, and a metro's name or a program's name ("Stargate")
    does not tell two companies' campuses apart. `a` may lack a point (Epoch rows)."""
    if a.get("state") and b.get("state") and a["state"] != b["state"]:
        return None
    d = None
    if a.get("lat") is not None and b.get("lat") is not None:
        d = km(a["lat"], a["lon"], b["lat"], b["lon"])
        if d > 40:
            return None
    na, nb = numbers(a["name"]), numbers(b["name"])
    if na and nb and na != nb:
        return None  # "Colossus 1" is not "Colossus 2"
    where = f"{d:.0f} km apart" if d is not None else "no point yet"
    pa = {p.lower() for p in a["parties"]} - {"undisclosed"}
    pb = {p.lower() for p in b["parties"]} - {"undisclosed"}
    same_co = bool(pa & pb)
    ka, kb = name_key(a["name"]), name_key(b["name"])
    if ka == kb and len(ka) >= 6:
        return 3.0, f"same name, {where}"
    company_words = set()
    for p in list(a["parties"]) + list(b["parties"]):
        company_words |= set(re.findall(r"[a-z0-9]+", p.lower()))
    wa = {t for t in toks(a["name"]) if t not in GENERIC and t not in company_words}
    wb = {t for t in toks(b["name"]) if t not in GENERIC and t not in company_words}
    near = d is None or d <= 20
    if same_co:
        strong = sorted(t for t in wa & wb if len(t) >= 4 and t not in PROGRAM)
        if strong and near:
            jac = len(wa & wb) / max(len(wa | wb), 1)
            return 1.5 + jac / 10, f"same company, shared name word(s) {strong}, {where}"
        same_city = bool(a.get("city")) and a["city"].lower() == (b.get("city") or "").lower().split(",")[0].strip()
        if same_city and (d is None or d <= 10):
            return 1.0, f"same company and city ({a['city']}), {where}"
        if d is not None and d <= 3:
            ma, mb = a.get("mw"), b.get("mw")
            if not ma or not mb or max(ma, mb) <= 3 * min(ma, mb):
                return 0.5, f"same company, {where}"
        return None
    # two companies: only a project name both records carry proves anything
    own_places = set(re.findall(r"[a-z0-9]+", (str(a.get("city") or "") + " " + str(b.get("city") or "")).lower()))
    xa, xb = wa - PLACES - PROGRAM - own_places, wb - PLACES - PROGRAM - own_places
    xs = sorted(t for t in xa & xb if len(t) >= 4)
    xj = len(xa & xb) / max(len(xa | xb), 1)
    if xs and near and xj >= 0.66:
        return 2.0 + xj, f"names {xj:.0%} alike on {xs}, {where}"
    return None


def best_match(rec: dict, pool: list[dict]) -> tuple[dict, str] | None:
    best = None
    for k in pool:
        m = match(rec, k)
        if m and (best is None or m[0] > best[0]):
            best = (m[0], k, m[1])
    return (best[1], best[2]) if best else None


def source_of(url: str, publisher: str | None, via: str | None = None) -> dict:
    """A source link labelled by who published it ("as reported by ..."), never by the source's own headline:
    a headline can name a site's tenant, and this app names only what the operator field reports."""
    from urllib.parse import urlparse

    host = re.sub(r"^www[.]", "", urlparse(url).netloc.lower())
    name = clip(publisher, 60) or host
    return {"title": name if not via else f"{host} (via {via})", "publisher": name, "url": url}


def build_atlas(curated: list[dict], report: bool) -> tuple[list[dict], list[dict], Counter]:
    raw = json.loads(ATLAS_RAW.read_text(encoding="utf-8"))
    skipped: Counter = Counter()
    entries: list[dict] = []
    merged: list[dict] = []
    seen_ids: set[str] = {c["id"] for c in curated}
    for f in raw:
        kind = f.get("facilityType")
        if kind == "power_generation":
            skipped["power generation (not a load)"] += 1
            continue
        if f.get("confidence") == "rumored":
            skipped["rumored"] += 1
            continue
        L = f.get("location") or {}
        lat, lon = num(L.get("lat")), num(L.get("lon"))
        state = str(L.get("state") or "").upper()
        if lat is None or lon is None or not state:
            skipped["no location"] += 1
            continue
        mw, basis, op, pl = atlas_mw(f)
        if L.get("city"):
            CITY_POOL.append((state, str(L["city"]).lower(), lat, lon))
        ps = parties_of(f.get("operator"))
        rec = {
            "id": slug(f["id"]),
            "name": clip(f.get("name"), 120),
            "operator": operator_as_reported(f.get("operator")),
            "company": ps[0],
            "parties": ps,
            "kind": "crypto_mining" if kind == "crypto_mining" else "data_center",
            "status": STATUS.get(f.get("status"), "unknown"),
            "status_detail": f.get("status"),
            "state": state,
            "city": clip(L.get("city"), 60),
            "county": clip(L.get("county"), 60),
            "lat": lat,
            "lon": lon,
            "precision": L.get("precision") or "approximate",
            "mw": mw,
            "mw_basis": basis,
            "mw_operational": op,
            "mw_planned": pl,
            "confidence": f.get("confidence") or "reported",
            "announced": clip(f.get("announcedDate"), 20) or None,
            "updated": f.get("lastUpdated"),
            "sources": [source_of(x["url"], x.get("publisher")) for x in (f.get("sources") or []) if re.match(r"^https?://", str(x.get("url") or ""), re.I)][:2],
            "origin": "compute-atlas",
        }
        n = 2
        base = rec["id"]
        while rec["id"] in seen_ids:
            rec["id"], n = f"{base}-{n}", n + 1
        hit = best_match(rec, curated)
        if hit:
            merged.append({"atlas_id": rec["id"], "atlas_name": rec["name"], "curated_id": hit[0]["id"], "curated_name": hit[0]["name"], "why": hit[1]})
            skipped["same campus as a curated entry"] += 1
            continue
        seen_ids.add(rec["id"])
        entries.append(rec)
    return entries, merged, skipped


# ------------------------------------------------------------------------------------ epoch
TAGS = re.compile(r"\s*#\w+")
# AI labs that Epoch's row names may carry as a tenant. Row names are shown without them unless the lab is the owner.
TENANT_WORDS = re.compile(r"\b(Anthropic|OpenAI|Mistral(?: AI)?|Cursor)\b[\s\-–]*", re.I)


STREET = re.compile(r"\b(?:road|rd|street|st|avenue|ave|boulevard|blvd|drive|dr|lane|ln|highway|hwy|parkway|pkwy|way|circle|cir|court|ct)\b\.?", re.I)


def city_only(text: str) -> str:
    """'County Line Road Ridgeland' -> 'Ridgeland' (the words after the last street word)."""
    t = text.strip()
    parts = STREET.split(t)
    return (parts[-1] if len(parts) > 1 and parts[-1].strip() else t).strip(" ,.")


def epoch_state_city(addr: str) -> tuple[str | None, str | None]:
    a = re.sub(r"\s+", " ", str(addr or "")).strip().strip(",")
    if not a:
        return None, None
    m = re.search(r",\s*([A-Za-z .'\-]+?),?\s+([A-Z]{2})\b(?:\s+\d{5}(?:-\d{4})?)?(?:,?\s*USA)?\s*(?:,.*)?$", a)
    if m and m.group(2) in STATE_CODES:
        return m.group(2), city_only(m.group(1))
    m = re.search(r"([A-Za-z .'\-]+?),?\s+(" + "|".join(sorted(STATE_NAMES, key=len, reverse=True)) + r")\b\.?\s*(?:\d{5})?\s*$", a, re.I)
    if m:
        return STATE_NAMES[m.group(2).lower()], city_only(m.group(1).strip().split(",")[-1])
    m = re.search(r"\b([A-Z]{2})\s+\d{5}\b", a)
    if m and m.group(1) in STATE_CODES:
        return m.group(1), None
    return None, None


def epoch_sources(text: str) -> list[dict]:
    """The source links only, labelled by their site: Epoch's own descriptions of them can name a site's users."""
    return [source_of(m.group(2), None, via="Epoch AI") for m in re.finditer(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text or "")][:2]


def infer_place(rec: dict, known: list[dict]) -> dict | None:
    """A row with no usable address ("Google Mesa"): the same company's records in a city or county the name mentions."""
    mine = {p.lower() for p in rec["parties"]} - {"undisclosed"}
    words = {t for t in re.findall(r"[a-z0-9]+", rec["name"].lower()) if len(t) >= 4 and t not in GENERIC}
    words -= {w for p in rec["parties"] for w in re.findall(r"[a-z0-9]+", p.lower())}
    hits = []
    for k in known:
        if not mine & {p.lower() for p in k["parties"]}:
            continue
        where = set(re.findall(r"[a-z0-9]+", ((k.get("city") or "") + " " + (k.get("county") or "")).lower()))
        if words & where:
            hits.append(k)
    if not hits:
        return None
    states = Counter(k["state"] for k in hits)
    st = states.most_common(1)[0][0]
    hits = [k for k in hits if k["state"] == st]
    return {"state": st, "city": hits[0].get("city") or "", "lat": round(sum(k["lat"] for k in hits) / len(hits), 5), "lon": round(sum(k["lon"] for k in hits) / len(hits), 5)}


def build_epoch(curated: list[dict], atlas: list[dict], report: bool) -> tuple[list[dict], Counter]:
    rows = list(csv.DictReader(EPOCH_RAW.open(encoding="utf-8-sig", newline="")))
    skipped: Counter = Counter()
    city_pts: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for st, cy, la, lo in CITY_POOL:
        city_pts[(st, cy)].append((la, lo))
    known = curated + [
        {"id": a["id"], "name": a["name"], "state": a["state"], "lat": a["lat"], "lon": a["lon"], "mw": a["mw"], "parties": a["parties"], "city": a["city"], "county": a["county"]}
        for a in atlas
    ]
    out: list[dict] = []
    used: set[str] = set()
    for r in rows:
        if (r.get("Country") or "").strip() != "United States":
            skipped["outside the United States"] += 1
            continue
        owner_raw = TAGS.sub("", r.get("Owner") or "").strip()
        owner_conf = (re.search(r"#(\w+)", r.get("Owner") or "") or [None, None])[1]
        state, city = epoch_state_city(r.get("Address") or "")
        name = re.sub(r"\s+", " ", r.get("Name") or "").strip()
        # never present an AI lab as a tenant: drop the lab's name from the row's name unless it owns the site
        stripped = TENANT_WORDS.sub("", name).strip(" -–")
        if stripped != name and not re.search(TENANT_WORDS, owner_raw):
            name = stripped or name
        if not state:
            for full, code in STATE_NAMES.items():
                if re.search(r"(?<![a-z])" + full + r"(?![a-z])", name.lower()):
                    state = code
                    break
        if owner_conf and owner_conf.lower() in ("speculative", "rumored", "unlikely"):
            owner_raw = ""  # Epoch itself marks this owner as a guess: never name it
        ps = parties_of(owner_raw) if owner_raw else ["Undisclosed"]
        hy = re.match(r"^([A-Za-z0-9]+)[-–]([A-Za-z0-9]+)\b\s*(.*)$", name)
        if hy and clean_party(hy.group(1)).lower() not in {p.lower() for p in ps} and clean_party(hy.group(2)).lower() in {p.lower() for p in ps}:
            name = f"{hy.group(2)} {hy.group(3)}".strip()  # "Microsoft-Nebius New Jersey" (owner Nebius): the row's other party is not named
        mw_cur = mw_int(r.get("Current power (MW)"))
        rec = {
            "id": "epoch-" + slug(name + "-" + (city or state or "")),
            "name": clip(name, 120),
            "operator": operator_as_reported(owner_raw) if owner_raw else "Undisclosed",
            "company": ps[0],
            "parties": ps,
            "kind": "data_center",
            "status": "operating" if mw_cur else "unknown",
            "status_detail": "current power reported" if mw_cur else "no current power reported",
            "state": state,
            "city": city or "",
            "county": "",
            "address": clip(r.get("Address"), 160) or None,
            "lat": None,
            "lon": None,
            "precision": None,
            "mw": mw_cur,
            "mw_basis": (f"{mw_cur:,} MW current power as estimated by Epoch AI" if mw_cur else "No current power reported (Epoch AI)"),
            "confidence": owner_conf or "reported",
            "sources": epoch_sources(r.get("Selected Sources")),
            "origin": "epoch-ai",
            "duplicate_of": None,
            "duplicate_why": None,
        }
        base, n = rec["id"], 2
        while rec["id"] in used:
            rec["id"], n = f"{base}-{n}", n + 1
        used.add(rec["id"])
        # the same campus as a curated or Compute Atlas entry?
        hit = best_match(rec, known)
        if hit:
            k, why = hit
            rec["duplicate_of"], rec["duplicate_why"] = k["id"], why
            rec["lat"], rec["lon"] = round(k["lat"], 5), round(k["lon"], 5)
            rec["precision"] = "point of the matched entry"
            rec["state"] = rec["state"] or k["state"]
            rec["city"] = rec["city"] or k.get("city") or ""
        elif not (state and city and city_pts.get((state, city.lower()))) and (found := infer_place(rec, known)) and (not state or found["state"] == state):
            rec["state"], rec["city"] = found["state"], rec["city"] or found["city"]
            rec["lat"], rec["lon"] = found["lat"], found["lon"]
            rec["precision"] = "mean point of the same company's Compute Atlas sites in the place the name mentions"
        elif state and city and city_pts.get((state, city.lower())):
            pts = city_pts[(state, city.lower())]
            rec["lat"] = round(sum(p[0] for p in pts) / len(pts), 5)
            rec["lon"] = round(sum(p[1] for p in pts) / len(pts), 5)
            rec["precision"] = "mean point of Compute Atlas sites in the same city"
        out.append(rec)
    return out, skipped


# ------------------------------------------------------------------------------------ writing
def write(path: Path, credit: str, source: str, extra: dict, entries: list[dict]) -> None:
    head = {"generated": date.today().isoformat(), "credit": credit, "source": source, **extra}
    lines = [json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"entries":[']
    body = ",\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in entries)
    path.write_text("\n".join(lines) + "\n" + body + "\n]}\n", encoding="utf-8")


def fill_places(curated: list[dict]) -> None:
    raw = json.loads(ATLAS_RAW.read_text(encoding="utf-8"))
    count: Counter = Counter()
    cities = [(f.get("location") or {}).get("city") or "" for f in raw] + [c["city"] for c in curated]
    for city in cities:
        for t in set(re.findall(r"[a-z0-9]+", city.lower())):
            count[t] += 1
    PLACES.update(t for t, n in count.items() if n >= 3 and len(t) >= 4)
    names: dict[str, list[str]] = defaultdict(list)
    rows = [(f.get("name") or "", (f.get("location") or {}).get("state") or "") for f in raw] + [(c["name"], c["state"]) for c in curated]
    for nm, st in rows:
        for t in set(re.findall(r"[a-z0-9]+", nm.lower())):
            names[t].append(st)
    PROGRAM.update(t for t, sts in names.items() if len(sts) >= 4 and len(set(sts)) >= 3 and len(t) >= 4)
    for name in STATE_NAMES:
        PLACES.update(re.findall(r"[a-z]+", name))


def main() -> None:
    report = "--report" in sys.argv
    curated = load_curated()
    fill_places(curated)
    cc = curated_companies()
    atlas, merged, skipped_a = build_atlas(curated, report)
    epoch, skipped_e = build_epoch(curated, atlas, report)

    write(
        ATLAS_OUT, ATLAS_CREDIT, "https://www.compute-atlas.com",
        {"skipped": dict(skipped_a), "merged": merged, "curated_companies": cc}, atlas,
    )
    write(EPOCH_OUT, EPOCH_CREDIT, "https://epoch.ai/data/ai-data-centers", {"skipped": dict(skipped_e), "curated_companies": {}}, epoch)

    placed = sum(1 for e in epoch if e["lat"] is not None)
    dup = sum(1 for e in epoch if e["duplicate_of"])
    print(f"curated: {len(curated)} with a point")
    print(f"atlas:   {len(atlas)} kept, {len(merged)} merged into curated, skipped {dict(skipped_a)}")
    print(f"           by kind {dict(Counter(e['kind'] for e in atlas))}; by status {dict(Counter(e['status'] for e in atlas))}")
    print(f"           with MW {sum(1 for e in atlas if e['mw'])}; companies {len({e['company'] for e in atlas})}")
    print(f"epoch:   {len(epoch)} U.S. rows, {dup} duplicate of another source, {placed} placed, skipped {dict(skipped_e)}")
    print(f"wrote {ATLAS_OUT.name} ({ATLAS_OUT.stat().st_size // 1024} KB) and {EPOCH_OUT.name} ({EPOCH_OUT.stat().st_size // 1024} KB)")
    if report:
        print("\n-- merged into curated (atlas -> curated)")
        for m in merged:
            print(f"  {m['atlas_name']!r} -> {m['curated_name']!r}  [{m['why']}]")
        print("\n-- epoch rows")
        for e in epoch:
            print(f"  {e['name']!r:48} {e['state'] or '--'} {e['city']!r:18} mw={e['mw']} dup={e['duplicate_of']} ({e['duplicate_why']}) placed={e['lat'] is not None}")
        print("\n-- top companies (atlas)")
        for c, n in Counter(p for e in atlas for p in e["parties"]).most_common(40):
            print(f"  {n:4} {c}")


if __name__ == "__main__":
    main()
