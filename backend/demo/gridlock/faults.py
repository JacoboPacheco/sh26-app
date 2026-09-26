"""Fault injection: do the checks keep bad records out?

Takes real validated records from data/projects.json and injects nine kinds of bad data (five this pipeline has met
in the two filings and in Sperry's worked example, four common data-entry errors), plus one FORMAT TEST: the two-digit
year, a format the filings really use, which must be read correctly and is not counted as bad data. Every copy goes back
through the SAME code the build runs (extract_desc.parse_page for a DESC page, build._common / normalize_desc for the
fields, locate for a title whose places changed, checks.run for the 16 named checks: imported, never re-implemented).
The report says per kind how many were injected, how many were caught, by which check, what the pipeline did with them,
and every one that slipped through.

    backend/venv/Scripts/python backend/demo/gridlock/faults.py [--per-kind 10] [--seed 2026]

Writes data/fault_report.json (committed; served at GET /api/gridlock/fault-test). Like build.py it needs the local
Census outlines (backend/demo/raw/census) and the cached OSM extracts (raw/osm); it never touches the network.

Where each fault goes in:
  DESC records   the PDF page text itself (provenance.text): the in-service line, the title, the Project ID, the
                 cost row. The page is re-parsed by extract_desc.parse_page and re-normalized by build.normalize_desc.
  Georgia rows   the parsed Table 2 row (title, need date, TEAMS number), re-normalized by build._common (the two
                 table parsers and the detail-page join need the 668-page PDF, so those two checks say n/a here).
  locations      the located point (a wrong match): moved 200 km inside the filer's state, moved into another
                 state, or lat/lon swapped. The geometry and center are rebuilt by build.geometry_of.

Outcomes, compared with the SAME record re-run with no fault (the control; every control record must be kept):
  set_aside  a blocking check failed: the record went to quarantine with the reason (caught)
  flagged    kept, but a check newly warns about it (caught)
  noted      format test only: kept and read as the right value (NOT a catch: nothing was wrong; `noted_by` names the
             check whose detail records the conversion). A format-test record that is set aside or misread is a failure.
  missed     nothing noticed, or the value came out wrong without a warning
The headline counts the nine bad-data kinds only: caught = set aside + flagged.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402
import checks  # noqa: E402
import extract_desc  # noqa: E402
import geo  # noqa: E402
import locate  # noqa: E402
import normalize as N  # noqa: E402
import osm  # noqa: E402

DATA = HERE / "data"
PROJECTS = DATA / "projects.json"
OUT = DATA / "fault_report.json"
COMMAND = "backend/venv/Scripts/python backend/demo/gridlock/faults.py"

RANK = {"pass": 0, "warn": 1, "fail": 2}
RULE = {rid: {"label": label, "blocking": blocking} for rid, label, blocking, _fn, _lowers in checks.RULES}
LABELS = [lab for _k, lab in extract_desc.SECTIONS]

# fictional code names: a customer-only title names no place (and no real company)
CODE_NAMES = ["KESTREL", "HALCYON", "NORTHSTAR", "ORION", "VANTAGE", "KEYSTONE", "MERIDIAN", "BEACON", "LODESTAR", "SPARROW",
              "TALON", "CIRRUS"]
# real towns just across SC/GA's borders, several sharing a name with a substation in the filings (Jasper)
OTHER_STATE = [
    ("Jasper, Tennessee", 35.0742, -85.6261), ("Jasper, Alabama", 33.8312, -87.2775), ("Jasper, Florida", 30.5185, -82.9482),
    ("Charlotte, North Carolina", 35.2271, -80.8431), ("Asheville, North Carolina", 35.5951, -82.5515),
    ("Murphy, North Carolina", 35.0876, -84.0213), ("Chattanooga, Tennessee", 35.0456, -85.3097),
    ("Phenix City, Alabama", 32.4710, -85.0008), ("Dothan, Alabama", 31.2232, -85.3905),
    ("Tallahassee, Florida", 30.4383, -84.2807), ("Jacksonville, Florida", 30.3322, -81.6557),
    ("Wilmington, North Carolina", 34.2257, -77.9447),
]
FAR_KM = 200.0


# =========================================================================== rebuilding a record


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").split("\n") if ln.strip()]


def set_section(text: str, label: str, value: str) -> str:
    """The DESC page with one labelled section's body replaced (parse_page reads sections by their label line)."""
    lines = _lines(text)
    i = lines.index(label)
    end = next((j for j in range(i + 1, len(lines)) if lines[j] in LABELS), len(lines))
    return "\n".join(lines[: i + 1] + [value] + lines[end:])


def set_title(text: str, title: str) -> str:
    lines = _lines(text)
    first = min(i for i, ln in enumerate(lines) if ln in LABELS)
    keep = [ln for ln in lines[1:first] if ln in extract_desc.BOILERPLATE]
    return "\n".join([lines[0]] + keep + [title] + lines[first:])


def set_amounts(text: str, line: str) -> str:
    lines = _lines(text)
    i = lines.index("Estimated Project Cost")
    j = next(k for k in range(i + 1, len(lines)) if lines[k].startswith("$"))
    return "\n".join(lines[:j] + [line] + lines[j + 1 :])


def section(text: str, label: str) -> str | None:
    lines = _lines(text)
    if label not in lines:
        return None
    i = lines.index(label)
    end = next((j for j in range(i + 1, len(lines)) if lines[j] in LABELS), len(lines))
    return " ".join(lines[i + 1 : end]) or None


def keys_of(pub: dict) -> list[str]:
    return [N.endpoint_key(e.get("raw") or e.get("name") or "") for e in pub.get("endpoints") or []]


class Relocator:
    """The build's locate stage for a title whose place names changed (offline: cached OSM + cached Nominatim)."""

    def __init__(self):
        self._index = None
        self.available = all((osm.RAW / f"{n}.json").exists() for n in ("substations_SC", "substations_GA"))
        self.geocoder = osm.Nominatim(allow_network=False)

    def index(self):
        if self._index is None and self.available:
            self._index = locate.Index()
        return self._index

    def endpoints(self, p: dict, zc: dict) -> list[dict]:
        idx = self.index()
        if idx is None:  # no cached OSM: the changed names stay unlocated (the build would need the network)
            return [dict(e, lat=None, lon=None, osm=None, confidence=None, match=None, state=None) for e in p["endpoints_parsed"]]
        zone = zc.get(p.get("zone") or "")
        eps = locate.locate_project(idx, p, zone)
        for i, e in enumerate(eps):
            if e.get("lat") is None:
                other = eps[1 - i] if len(eps) == 2 else None
                cands = locate.nominatim_candidates(idx, self.geocoder, e, locate.HOME[p["utility"]], max(p["kv"] or [0]) or None)
                hit = next((c for c in cands if locate.fits(c, p, other, zone)), None)
                if hit:
                    eps[i] = dict(hit, name=e["name"], raw=e["raw"], key=e["key"])
        return eps


def attach(p: dict, pub: dict, zc: dict, reloc: Relocator) -> dict:
    """Located endpoints for a re-normalized record: the pipeline's own answer for every place name it already
    located (locate is a function of the name and its context), the locate stage re-run for names that changed."""
    known = {}
    for k, e in zip(keys_of(pub), pub.get("endpoints") or []):
        known[k] = e
    parsed = p["endpoints_parsed"]
    if all(e["key"] in known for e in parsed):
        p["endpoints"] = [dict(copy.deepcopy(known[e["key"]]), key=e["key"], name=e["name"], raw=e["raw"]) for e in parsed]
        p["_relocated"] = False
    else:
        fresh = reloc.endpoints(p, zc)
        p["endpoints"] = [dict(copy.deepcopy(known[e["key"]]), key=e["key"], name=e["name"], raw=e["raw"]) if e["key"] in known else f
                          for e, f in zip(parsed, fresh)]
        p["_relocated"] = True
    build.geometry_of(p, None)
    return p


def rebuild_desc(pub: dict, page_text: str, zc: dict, reloc: Relocator) -> dict:
    rec = extract_desc.parse_page(page_text, pub["provenance"]["page"])
    p = build.normalize_desc([rec], {"id": pub["provenance"]["source"]})[0]
    return attach(p, pub, zc, reloc)


def rebuild_ga(pub: dict, zc: dict, reloc: Relocator, *, name: str | None = None, need_raw: str | None = None,
               teams: str | None = None) -> dict:
    name = pub["name"] if name is None else name
    teams = pub["teams_no"] if teams is None else teams
    p = {
        "id": f"GA-{teams}", "utility": pub["utility"], "utility_name": pub["utility_name"], "state": "GA",
        "name": name, "name_for_places": name, "project_id_raw": teams, "description": pub.get("description"),
        "need": None, "status": None, "in_service_raw": pub["in_service_raw"] if need_raw is None else need_raw,
        "cost_usd": None, "cost_by_year": None, "zone": pub.get("zone"), "teams_no": teams, "sponsor_raw": pub.get("sponsor_raw"),
        "plan_year": pub.get("plan_year"), "provenance": pub["provenance"], "_parse_errors": [], "_anomalies": [],
        "_ga": None,  # the two table parsers and the detail-page join need the PDF: those two checks say n/a
    }
    build._common(p)
    return attach(p, pub, zc, reloc)


def rebuild(pub: dict, zc: dict, reloc: Relocator) -> dict:
    if pub["utility"] == "DESC":
        return rebuild_desc(pub, pub["provenance"]["text"], zc, reloc)
    return rebuild_ga(pub, zc, reloc)


def run_checks(records: list[dict], zc: dict) -> set[str]:
    """checks.run over a small batch (the record, plus its twin for the duplicate-id fault); returns the ids of the
    records it set aside. id_unique counts ids within the batch, so every other fault runs alone."""
    _kept, quarantine, _summary = checks.run(records, zc)
    return {id(q) for q in quarantine}


# =========================================================================== faults


def excel_serial(iso: str) -> str:
    return str((date.fromisoformat(iso) - N.EXCEL_EPOCH).days)


def destination(lat: float, lon: float, bearing_deg: float, km: float) -> tuple[float, float]:
    d = km / geo.R_KM
    th = math.radians(bearing_deg)
    la1, lo1 = math.radians(lat), math.radians(lon)
    la2 = math.asin(math.sin(la1) * math.cos(d) + math.cos(la1) * math.sin(d) * math.cos(th))
    lo2 = lo1 + math.atan2(math.sin(th) * math.sin(d) * math.cos(la1), math.cos(d) - math.sin(la1) * math.sin(la2))
    return round(math.degrees(la2), 6), round(math.degrees(lo2), 6)


def _located(p: dict) -> list[int]:
    return [i for i, e in enumerate(p["endpoints"]) if e.get("lat") is not None]


ORIGINS = {
    "filings": "met in the two filings or Sperry's sheet",
    "common": "a common data-entry error (not seen in these filings)",
    "format": "a format the filings really use: must be read correctly, not counted as bad data",
}


class Kind:
    """origin: 'filings' (met_in says where), 'common' (a common data-entry error; met_in is None) or 'format' (not an
    error: a real format of the filings, the format test). about: what the fault is, in a few words."""

    def __init__(self, kid, label, origin, met_in, about, expect, where, applies, inject, value=None):
        assert origin in ORIGINS and (met_in is None) == (origin == "common"), kid
        self.id, self.label, self.origin, self.met_in, self.about = kid, label, origin, met_in, about
        self.expect, self.where, self.applies, self.inject, self.value = expect, where, applies, inject, value

    @property
    def is_format(self) -> bool:
        return self.origin == "format"


# Each inject(pub, clean, rng, ctx) -> (faulted record, companions, field, before, after) or None when it doesn't apply.


def inj_excel(pub, clean, rng, ctx):
    serial = excel_serial(clean["in_service"])
    before = clean["in_service_raw"]
    if pub["utility"] == "DESC":
        f = rebuild_desc(pub, set_section(pub["provenance"]["text"], "Planned In-Service Date", serial), ctx.zc, ctx.reloc)
    else:
        f = rebuild_ga(pub, ctx.zc, ctx.reloc, need_raw=serial)
    return f, [], "in-service date", before, serial


def inj_impossible(pub, clean, rng, ctx):
    y = date.fromisoformat(clean["in_service"]).year
    m, d = rng.choice([(4, 31), (6, 31), (9, 31), (11, 31), (2, 30), (2, 31)])
    bad = f"{m:02d}/{d}/{y % 100:02d}"
    if pub["utility"] == "DESC":
        f = rebuild_desc(pub, set_section(pub["provenance"]["text"], "Planned In-Service Date", bad), ctx.zc, ctx.reloc)
    else:
        f = rebuild_ga(pub, ctx.zc, ctx.reloc, need_raw=bad)
    return f, [], "in-service date", clean["in_service_raw"], bad


def _amounts_line(text: str) -> str | None:
    lines = _lines(text)
    if "Estimated Project Cost" not in lines:
        return None
    i = lines.index("Estimated Project Cost")
    return next((lines[k] for k in range(i + 1, len(lines)) if lines[k].startswith("$")), None)


def applies_amount(pub, clean):
    if pub["utility"] != "DESC" or clean.get("cost_usd") is None:
        return False
    line = _amounts_line(pub["provenance"]["text"])
    raws = extract_desc.MONEY.findall(line or "")
    return any("," in r for r in raws[:-1])


def inj_amount(pub, clean, rng, ctx):
    line = _amounts_line(pub["provenance"]["text"])
    raws = extract_desc.MONEY.findall(line)
    choices = [k for k, r in enumerate(raws[:-1]) if "," in r]  # never the Total: the repair reads from it
    k = rng.choice(choices)
    groups = raws[k].split(",")
    g = rng.randrange(1, len(groups))  # a digit goes missing from a thousands group ('$19,000,181' -> '$19,00,181')
    pos = rng.randrange(len(groups[g]))
    groups[g] = groups[g][:pos] + groups[g][pos + 1 :]
    bad = ",".join(groups)
    parts = re.split(r"(\$\s?[\d,]+)", line)
    n = -1
    for i, part in enumerate(parts):
        if extract_desc.MONEY.fullmatch(part):
            n += 1
            if n == k:
                parts[i] = "$" + bad
    f = rebuild_desc(pub, set_amounts(pub["provenance"]["text"], "".join(parts)), ctx.zc, ctx.reloc)
    return f, [], "cost row", f"${raws[k]}", f"${bad}"


def inj_duplicate(pub, clean, rng, ctx):
    """Another record of the same filing is given this record's id: both land in one batch."""
    twin_pub = rng.choice([q for q in ctx.pool if q["utility"] != "DESC"] if pub["utility"] != "DESC" else
                          [q for q in ctx.pool if q["utility"] == "DESC"])
    while twin_pub["id"] == pub["id"]:
        twin_pub = rng.choice([q for q in ctx.pool if (q["utility"] == "DESC") == (pub["utility"] == "DESC")])
    if pub["utility"] == "DESC":
        f = rebuild_desc(twin_pub, set_section(twin_pub["provenance"]["text"], "Project ID", pub["project_id_raw"]), ctx.zc, ctx.reloc)
    else:
        f = rebuild_ga(twin_pub, ctx.zc, ctx.reloc, teams=pub["teams_no"])
    original = rebuild(pub, ctx.zc, ctx.reloc)
    f["_control"] = ctx.clean[twin_pub["id"]]  # compare the renamed record with ITS own clean run
    f["_record"] = twin_pub["id"]
    return f, [original], "project id", twin_pub["id"], f"{f['id']} (another record's id)"


def inj_customer(pub, clean, rng, ctx):
    title = f"CC - PROJECT {rng.choice(CODE_NAMES)}"
    if pub["utility"] == "DESC":
        f = rebuild_desc(pub, set_title(pub["provenance"]["text"], title), ctx.zc, ctx.reloc)
    else:
        f = rebuild_ga(pub, ctx.zc, ctx.reloc, name=title)
    return f, [], "title", pub["name"], title


def inj_far(pub, clean, rng, ctx):
    f = copy.deepcopy(clean)
    idxs = _located(f)
    rng.shuffle(idxs)
    home = locate.HOME[f["utility"]]
    for i in idxs:
        e = f["endpoints"][i]
        start = rng.randrange(0, 360, 15)
        for step in range(0, 360, 15):
            lat, lon = destination(e["lat"], e["lon"], (start + step) % 360, FAR_KM)
            if geo.state_of(lat, lon, (home,)) == home:
                before = f"{e['name']} at {e['lat']:.4f}, {e['lon']:.4f}"
                e["lat"], e["lon"] = lat, lon
                _reset(f)
                return f, [], "located point", before, f"{e['name']} at {lat:.4f}, {lon:.4f} ({FAR_KM:.0f} km away, still in {home})"
    return None


def inj_other_state(pub, clean, rng, ctx):
    f = copy.deepcopy(clean)
    i = rng.choice(_located(f))
    e = f["endpoints"][i]
    town, lat, lon = rng.choice(ctx.other_state)
    before = f"{e['name']} at {e['lat']:.4f}, {e['lon']:.4f}"
    e["lat"], e["lon"] = lat, lon
    _reset(f)
    return f, [], "located point", before, f"{e['name']} at {lat:.4f}, {lon:.4f} ({town})"


def inj_two_digit(pub, clean, rng, ctx):
    raw = clean["in_service_raw"]
    short = re.sub(r"(\d{1,2}/\d{1,2}/)\d{2}(\d{2})\b", r"\1\2", raw, count=1)
    if pub["utility"] == "DESC":
        f = rebuild_desc(pub, set_section(pub["provenance"]["text"], "Planned In-Service Date", short), ctx.zc, ctx.reloc)
    else:
        f = rebuild_ga(pub, ctx.zc, ctx.reloc, need_raw=short)
    return f, [], "in-service date", raw, short


KV_ZERO = re.compile(r"(?<!\d)(\d{2})0(?!\d)")


def _kv_spans(name: str) -> list[tuple[int, int]]:
    """Positions of a voltage's final zero ('230' in '230kV', '230-115kV', '500/230KV'), in the title as filed."""
    out = []
    for m in N.KV.finditer(name):
        for z in KV_ZERO.finditer(m.group(1)):
            if z.group(0) in N.STANDARD_KV:
                out.append((m.start(1) + z.end() - 1, m.start(1) + z.end()))
    return out


def inj_kv_o(pub, clean, rng, ctx):
    spans = _kv_spans(pub["name"])
    a, b = rng.choice(spans)
    title = pub["name"][:a] + "O" + pub["name"][b:]
    if pub["utility"] == "DESC":
        text = pub["provenance"]["text"]
        if pub["name"] not in " ".join(_lines(text)):
            return None
        f = rebuild_desc(pub, set_title(text, title), ctx.zc, ctx.reloc)
    else:
        f = rebuild_ga(pub, ctx.zc, ctx.reloc, name=title)
    return f, [], "title", pub["name"], title


def inj_swap(pub, clean, rng, ctx):
    f = copy.deepcopy(clean)
    i = rng.choice(_located(f))
    e = f["endpoints"][i]
    before = f"{e['name']} at {e['lat']:.4f}, {e['lon']:.4f}"
    e["lat"], e["lon"] = e["lon"], e["lat"]
    _reset(f)
    return f, [], "located point", before, f"{e['name']} at {e['lat']:.4f}, {e['lon']:.4f} (swapped)"


def _reset(f: dict) -> None:
    for k in ("checks", "confidence", "_reasons"):
        f.pop(k, None)
    for e in f["endpoints"]:
        e.pop("state", None)
    build.geometry_of(f, None)


def _has_date(clean) -> bool:
    return bool(clean.get("in_service"))


KINDS = [
    Kind("excel_serial", "Excel serial date", "filings", "Sperry's worked example stores two in-service dates as 45809 and 45778",
         "a date stored as an Excel day number (45809)", "read as the right date and flagged", "in-service date",
         lambda pub, c: _has_date(c), inj_excel, value=lambda c: c["in_service"]),
    Kind("impossible_date", "Impossible date", "common", None, "a calendar date that doesn't exist, like 04/31/26",
         "set aside", "in-service date", lambda pub, c: _has_date(c), inj_impossible),
    Kind("malformed_amount", "Malformed amount", "filings", "DESC's filing prints $19,00,181 on PDF page 22",
         "a digit missing from a thousands group", "repaired from the Total and flagged", "cost row (DESC)",
         applies_amount, inj_amount, value=lambda c: c.get("cost_by_year")),
    Kind("duplicate_id", "Duplicate project id", "common", None, "two records claiming one project id or TEAMS number",
         "both set aside", "project id", lambda pub, c: True, inj_duplicate),
    Kind("customer_only", "Customer-only title", "filings",
         "Georgia titles like 'CC - PROJECT CHRONOS- SK/HYUNDAI' name a customer, not a place",
         "a title that names a customer code instead of a place", "set aside", "title", lambda pub, c: True, inj_customer),
    Kind("same_name_far", "Same-name substation 200 km away", "filings",
         "a name that exists twice in one state, e.g. GA-13166's 'First Avenue'",
         "a match to a same-name substation elsewhere in the state", "set aside or flagged", "located point",
         lambda pub, c: bool(_located(c)), inj_far),
    Kind("other_state", "Point in another state", "common", None,
         "a match to a same-name place across the border, e.g. Jasper, Tennessee", "set aside", "located point",
         lambda pub, c: bool(_located(c)), inj_other_state),
    Kind("two_digit_year", "Two-digit year", "format", "DESC files dates like 12/31/23; a regex once read 6/1/2033 as 2020",
         "a date written with a two-digit year (12/31/26)", "read as the right year", "in-service date",
         lambda pub, c: bool(re.search(r"\d{1,2}/\d{1,2}/\d{4}\b", c.get("in_service_raw") or "")), inj_two_digit,
         value=lambda c: c["in_service"]),
    Kind("kv_letter_o", "Letter O in a voltage", "filings", "Georgia's Table 2 prints 'THALMANN AND COLERAIN 23O KV'",
         "a letter O typed for a zero in a voltage", "read as the right voltage and flagged", "title",
         lambda pub, c: bool(_kv_spans(pub["name"])), inj_kv_o, value=lambda c: c["kv"]),
    Kind("swapped_latlon", "Swapped latitude and longitude", "common", None, "a coordinate pair entered the wrong way round",
         "set aside", "located point", lambda pub, c: bool(_located(c)), inj_swap),
]


# =========================================================================== running


class Ctx:
    pass


def compare(control: dict, faulted: dict, set_aside: bool, kind: Kind) -> tuple[str, list[dict], bool | None]:
    base = {c["id"]: c for c in control["checks"]}
    new = [c for c in faulted["checks"]
           if RANK[c["status"]] > RANK[base[c["id"]]["status"]] or (c["status"] != "pass" and c["detail"] != base[c["id"]]["detail"])]
    value_ok = None
    if kind.value:
        try:
            value_ok = kind.value(faulted) == kind.value(control)
        except (KeyError, TypeError):
            value_ok = False
    if set_aside:
        fails = [c for c in new if c["status"] == "fail"]
        return "set_aside", fails or new, value_ok
    if kind.is_format and value_ok:
        # read correctly: nothing was wrong, so no check "caught" it; these name the checks whose detail records the reading
        changed = sorted((c for c in faulted["checks"] if c["detail"] != base[c["id"]]["detail"]), key=lambda c: RULE[c["id"]]["blocking"])
        return "noted", changed, value_ok
    if new:
        return "flagged", new, value_ok
    return "missed", [], value_ok


def interleave(eligible: list[dict], rng: random.Random) -> list[dict]:
    """A seeded order that alternates DESC and Georgia records, so a sample (--per-kind N) draws from both filings."""
    desc = [p for p in eligible if p["utility"] == "DESC"]
    ga = [p for p in eligible if p["utility"] != "DESC"]
    rng.shuffle(desc)
    rng.shuffle(ga)
    out = []
    for i in range(max(len(desc), len(ga))):
        out += [x[i] for x in (desc, ga) if i < len(x)]
    return out


def family(utility: str) -> str:
    return "DESC" if utility == "DESC" else "Georgia"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-kind", type=int, default=0, help="records per fault kind (default 0 = every eligible record)")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.perf_counter()

    doc = json.loads(PROJECTS.read_text(encoding="utf-8"))
    projects = doc["projects"]
    zc = {z: tuple(v) for z, v in ((doc.get("report") or {}).get("locate") or {}).get("zone_centers", {}).items()}
    ctx = Ctx()
    ctx.zc, ctx.reloc = zc, Relocator()
    ctx.other_state = [(t, la, lo) for t, la, lo in OTHER_STATE if geo.state_of(la, lo, ("NC", "TN", "AL", "FL")) is not None]

    # control: every validated record re-run through the same code with no fault ------------------------------------
    pool, clean, skipped, mismatched = [], {}, [], []
    for pub in projects:
        try:
            c = rebuild(pub, zc, ctx.reloc)
        except (ValueError, KeyError) as e:  # a page whose text doesn't carry every section (never expected)
            skipped.append({"id": pub["id"], "why": f"{type(e).__name__}: {e}"})
            continue
        why = []
        if c["id"] != pub["id"]:
            why.append(f"id {c['id']}")
        if c["name"] != pub["name"]:
            why.append("title")
        if c["in_service"] != pub["in_service"]:
            why.append(f"date {c['in_service']}")
        if [e["key"] for e in c["endpoints_parsed"]] != keys_of(pub):
            why.append("its place names came from the detail page or the description, not the title")
        if pub["utility"] == "DESC" and c.get("cost_by_year") != pub.get("cost_by_year"):
            why.append("cost row")
        if why:
            skipped.append({"id": pub["id"], "why": "the re-run differs: " + ", ".join(why)})
            continue
        set_aside = run_checks([c], zc)
        if set_aside:
            skipped.append({"id": pub["id"], "why": "the clean re-run was set aside: " + "; ".join(c.get("_reasons", []))})
            continue
        published = {x["id"]: x["status"] for x in pub.get("checks") or []}
        skip_ids = {"two_parsers_agree", "table_matches_detail"} if pub["utility"] != "DESC" else set()
        diff = [x["id"] for x in c["checks"] if x["id"] not in skip_ids and published.get(x["id"]) not in (None, x["status"])]
        if diff:
            mismatched.append({"id": pub["id"], "checks": diff})
        clean[pub["id"]] = c
        pool.append(pub)
    ctx.pool, ctx.clean = pool, clean
    print(f"control: {len(pool)} of {len(projects)} validated records re-run exactly from their filed text and are kept "
          f"({len(skipped)} left out of the pool; {len(mismatched)} with a check result different from the published one)")

    # inject --------------------------------------------------------------------------------------------------------
    kinds_out = []
    total = {"kinds": 0, "injected": 0, "caught": 0, "missed": 0, "set_aside": 0, "flagged": 0}  # bad data only
    fmt = {"kinds": 0, "injected": 0, "read_correctly": 0, "not_read_correctly": 0}  # the format test
    for kind in KINDS:
        rng = random.Random(f"{args.seed}:{kind.id}")
        eligible = [p for p in pool if kind.applies(p, clean[p["id"]])]
        cases = []
        for pub in interleave(eligible, rng):
            if args.per_kind and len(cases) >= args.per_kind:
                break
            got = kind.inject(pub, clean[pub["id"]], rng, ctx)
            if got is None:
                continue
            f, companions, field, before, after = got
            control = f.pop("_control", None) or clean[pub["id"]]
            record = f.pop("_record", pub["id"])
            aside = run_checks([f] + companions, zc)
            outcome, by, value_ok = compare(control, f, id(f) in aside, kind)
            noted = outcome == "noted"
            case = {
                "record": record, "utility": pub["utility"], "field": field, "before": before, "after": after,
                "outcome": outcome, "caught_by": [] if noted else [c["id"] for c in by],
                "noted_by": [c["id"] for c in by] if noted else [], "detail": by[0]["detail"] if by else None,
                "value_correct": value_ok,
            }
            if f.get("_relocated"):
                case["relocated"] = True
            if kind.id == "duplicate_id":
                case["original_set_aside"] = id(companions[0]) in aside
            if outcome == "missed":
                case["why_missed"] = why_missed(kind, f, control, value_ok, ctx)
            elif value_ok is False:
                case["note"] = value_note(kind, f, control)
            cases.append(case)
        k = summarize(kind, eligible, cases)
        kinds_out.append(k)
        if kind.is_format:
            fmt["kinds"] += 1
            for key in ("injected", "read_correctly", "not_read_correctly"):
                fmt[key] += k[key]
            print(f"  {kind.id:17} FORMAT TEST: {k['read_correctly']} of {k['injected']} read correctly "
                  f"({k['not_read_correctly']} not; set aside {k['outcomes']['set_aside']}, flagged {k['outcomes']['flagged']}, "
                  f"missed {k['outcomes']['missed']})")
        else:
            total["kinds"] += 1
            for key in ("set_aside", "flagged"):
                total[key] += k["outcomes"][key]
            for key in ("injected", "caught", "missed"):
                total[key] += k[key]
            firsts = ", ".join(f"{c['check']} {c['first']}" for c in k["caught_by"] if c["first"])
            print(f"  {kind.id:17} injected {k['injected']:>4}  caught {k['caught']:>4}  "
                  f"(set aside {k['outcomes']['set_aside']}, flagged {k['outcomes']['flagged']})  "
                  f"missed {k['missed']}   first caught by: {firsts}")
        for m in k["misses"]:
            print(f"      MISSED {m['record']}: {m['before']!r} -> {m['after']!r}: {m['why_missed']}")
        for m in k["value_wrong"]:
            print(f"      CAUGHT, VALUE WRONG {m['record']}: {m['before']!r} -> {m['after']!r}: {m['note']}")

    mode = f"a seeded sample of up to {args.per_kind} records per fault" if args.per_kind else "every eligible record, once per fault"
    same = "the same check results the pipeline published" if not mismatched else f"{len(mismatched)} differing from the published checks"
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": COMMAND + (f" --per-kind {args.per_kind}" if args.per_kind else "") + (f" --seed {args.seed}" if args.seed != 2026 else ""),
        "mode": mode,
        "seed": args.seed,
        "per_kind": args.per_kind or None,
        "source": {"file": "backend/demo/gridlock/data/projects.json", "built_at": doc.get("built_at"), "validated_records": len(projects)},
        "control": {
            "records": len(pool), "kept": len(pool),
            "left_out": skipped,
            "differs_from_published": mismatched,
            "note": (f"Before any fault, {len(pool)} of the {len(projects)} validated records are re-run through the same code with "
                     f"nothing changed: every one is kept, with {same}. So each catch below is the fault's doing, not a false alarm."),
        },
        "checks": [{"id": rid, "label": RULE[rid]["label"], "blocking": RULE[rid]["blocking"]} for rid in RULE],
        "summary": total,
        "headline": f"We tried to break it: {total['caught']:,} of {total['injected']:,} bad records caught",
        "origins": {o: {"label": ORIGINS[o], "kinds": [k.id for k in KINDS if k.origin == o]} for o in ORIGINS},
        "format_test": dict(fmt, line=(
            f"Format test, not counted above: {fmt['read_correctly']:,} of {fmt['injected']:,} dates rewritten with a two-digit "
            "year (12/31/26, the way DESC files them) were read as the right date")),
        "kinds": kinds_out,
        "outcomes": {
            "set_aside": "a blocking check failed: the record went to quarantine with the reason",
            "flagged": "kept, but a check now warns about it, with the reason",
            "noted": "format test only: kept and read as the right value (not a catch: nothing was wrong)",
            "missed": "nothing noticed, or the value came out wrong without a warning",
        },
        "method": [
            "DESC records: the fault goes into the PDF page text itself, which is re-parsed (extract_desc.parse_page) "
            "and re-normalized (build.normalize_desc).",
            "Georgia records: the fault goes into the parsed Table 2 row (title, need date, TEAMS number), re-normalized by "
            "build._common; the two-parser and detail-page checks need the PDF and say n/a.",
            "Location faults move the located point the way a wrong match would; build.geometry_of rebuilds the geometry.",
            "A title whose place names change goes back through the locate stage (cached OpenStreetMap, no network).",
            f"Every record then runs through checks.run, the same {len(checks.RULES)} checks the build uses; a duplicate id "
            "runs in a batch with the record it copies.",
        ],
        "seconds": round(time.perf_counter() - t0, 1),
    }
    OUT.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{report['headline']} (set aside {total['set_aside']}, flagged {total['flagged']}; missed {total['missed']})")
    print(report["format_test"]["line"])
    print(f"wrote {OUT.relative_to(HERE.parent.parent.parent)} ({OUT.stat().st_size / 1024:.0f} KB) in {report['seconds']} s")


def summarize(kind: Kind, eligible: list[dict], cases: list[dict]) -> dict:
    """A bad-data kind: caught = set aside + flagged. The format test: read_correctly / not_read_correctly instead, and
    caught / missed / catch_rate are None (nothing was wrong, so there was nothing to catch)."""
    caught = [c for c in cases if c["outcome"] in ("set_aside", "flagged")]
    first: dict[str, int] = {}
    anyc: dict[str, int] = {}
    for c in caught:
        first[c["caught_by"][0]] = first.get(c["caught_by"][0], 0) + 1
        for cid in c["caught_by"]:
            anyc[cid] = anyc.get(cid, 0) + 1
    order = sorted(anyc, key=lambda k: (-first.get(k, 0), -anyc[k]))
    by_filing = {}
    for fam in ("DESC", "Georgia"):
        mine = [c for c in cases if family(c["utility"]) == fam]
        if mine:
            by_filing[fam] = {"injected": len(mine), "caught": sum(1 for c in mine if c["outcome"] != "missed")}
    vals = [c["value_correct"] for c in cases if c["value_correct"] is not None]
    shown = [c for c in cases if c["outcome"] == "noted"] if kind.is_format else caught
    examples, seen = [], set()
    for c in shown:  # one example from each filing
        fam = family(c["utility"])
        if fam not in seen:
            examples.append(c)
            seen.add(fam)
    keep = ("record", "utility", "field", "before", "after", "outcome", "caught_by", "noted_by", "detail")
    read_ok = sum(1 for c in cases if c["outcome"] == "noted")
    out = {
        "id": kind.id, "label": kind.label, "origin": kind.origin, "met_in": kind.met_in, "about": kind.about,
        "expect": kind.expect, "where": kind.where, "eligible": len(eligible), "injected": len(cases),
        "caught": None if kind.is_format else len(caught),
        "missed": None if kind.is_format else len(cases) - len(caught),
        "catch_rate": round(len(caught) / len(cases), 4) if cases and not kind.is_format else None,
        "outcomes": {k: sum(1 for c in cases if c["outcome"] == k) for k in ("set_aside", "flagged", "noted", "missed")},
        "caught_by": [{"check": k, "label": RULE[k]["label"], "blocking": RULE[k]["blocking"], "first": first.get(k, 0), "any": anyc[k]}
                      for k in order],
        "by_filing": by_filing,
        "value_correct": {"checked": len(vals), "correct": sum(vals)} if vals else None,
        "examples": [{k: c[k] for k in keep} for c in examples],
        "misses": [{k: c.get(k) for k in keep + ("why_missed",)} for c in cases if c["outcome"] == "missed"],
        "value_wrong": [{k: c.get(k) for k in keep + ("note",)} for c in cases if c["outcome"] != "missed" and c["value_correct"] is False],
    }
    if kind.is_format:
        out["read_correctly"] = read_ok
        out["not_read_correctly"] = len(cases) - read_ok  # set aside, flagged with a wrong value, or misread silently
    return out


def why_missed(kind: Kind, f: dict, control: dict, value_ok: bool | None, ctx) -> str:
    if kind.id == "same_name_far":
        loc = [f["endpoints"][i] for i in _located(f)]
        if len(loc) >= 2:
            span = geo.haversine_km((loc[0]["lat"], loc[0]["lon"]), (loc[1]["lat"], loc[1]["lon"]))
            zone = ctx.zc.get(f.get("zone") or "")
            where = (f"and its center is {geo.haversine_km(zone, tuple(f['center'])):.0f} km from planning zone {f['zone']}'s median "
                     "(the zone check allows 100)" if zone and f.get("center") else "and there is no planning-zone median to compare with")
            return f"the end moved toward the other one: they are {span:.0f} km apart, under the {checks.MAX_SPAN_KM} km limit, {where}"
        if not f.get("zone"):
            return ("a single-substation project: no second endpoint and no planning zone (DESC files none), so nothing in the "
                    "record says where in the state it should be")
        if f["zone"] not in ctx.zc:
            return (f"a single-point project in planning zone {f['zone']}, which has too few confidently located projects for a "
                    "median, so the zone check has nothing to compare against")
        return "a single-point project still within 100 km of its planning zone's median"
    if kind.id == "kv_letter_o":
        return f"the voltage came out as {f.get('kv')} instead of {control.get('kv')} with no warning"
    if value_ok is False:
        return "the value came out wrong and no check noticed"
    return "no check noticed"


def value_note(kind: Kind, f: dict, control: dict) -> str:
    if kind.id == "malformed_amount":
        wrong = {k: v for k, v in (f.get("cost_by_year") or {}).items() if (control.get("cost_by_year") or {}).get(k) != v}
        return ("flagged, but the amount came out as " + ", ".join(f"{k} ${v:,}" for k, v in wrong.items())
                + ": this page's Total doesn't equal its columns, so the Total can't give the missing digit back")
    if kind.id == "kv_letter_o":
        return f"flagged, but the voltage came out as {f.get('kv')} instead of {control.get('kv')}"
    return "flagged, but the value came out wrong"


if __name__ == "__main__":
    main()
