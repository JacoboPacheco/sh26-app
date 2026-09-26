"""Stage 4, checks: named rules over every normalized, located project.

Each rule returns pass / warn / fail with a plain-words detail, and every result is stored on the record
(`checks`), so the UI and the API can show why a record looks the way it does. A record that fails a
BLOCKING rule is not deleted: it moves to `quarantine` with its reasons and whatever was parsed, so a
person can fix the source or the matcher and rerun. Warnings keep the record; the ones about location
lower its confidence one step.
"""

from __future__ import annotations

import re
from collections import Counter

import geo
from normalize import fix_voltage_typos

DATE_MIN, DATE_MAX = "2020-01-01", "2040-12-31"
# a letter O for a zero inside a voltage that the typo repair can't read ('23O-115kV', '50O/230KV'; the repair only
# reads '23O KV'): flagged, never guessed. Found by faults.py, which slipped '23O-115kV' past every check.
LETTER_O_KV = re.compile(r"\b\d{2}[Oo](?=(?:\s*[-/]\s*\d{2,3}(?:\.\d+)?)*\s*-?\s*KV\b)", re.I)
MAX_SPAN_KM = 150
CONF = ["low", "medium", "high"]


def _r(status: str, detail: str) -> tuple[str, str]:
    return status, detail


def extract_complete(p, ctx):
    errs = p["_parse_errors"]
    if errs:
        return _r("fail", "the page did not parse: " + "; ".join(errs))
    return _r("pass", f"every field read from PDF page {p['provenance']['page']}")


def id_unique(p, ctx):
    n = ctx["id_counts"][p["id"]]
    return _r("fail", f"{n} records share the id {p['id']}") if n > 1 else _r("pass", "id is unique")


def date_valid(p, ctx):
    if not p["in_service"]:
        return _r("fail", f"in-service date '{p['in_service_raw']}' could not be read ({p['_date_note']})")
    if not DATE_MIN <= p["in_service"] <= DATE_MAX:
        return _r("fail", f"in-service date {p['in_service']} is outside {DATE_MIN[:4]}-{DATE_MAX[:4]}")
    return _r("pass", f"{p['in_service_raw']} -> {p['in_service']}")


def date_normalized(p, ctx):
    note = p.get("_date_note")
    if note and ("Excel" in note or "dates in the field" in note):
        return _r("warn", note)
    return _r("pass", note or "already in m/d/yyyy form")


def place_named(p, ctx):
    if p["endpoints"]:
        return _r("pass", "named places: " + ", ".join(e["name"] for e in p["endpoints"]))
    why = "; ".join(p["_name_notes"]) or "no substation or place name found in the title"
    return _r("fail", f"the title names no place to map ({why})")


def located(p, ctx):
    n = sum(1 for e in p["endpoints"] if e.get("lat") is not None)
    if not p["endpoints"]:
        return _r("fail", "nothing to locate")
    if n == 0:
        text = f"{p['name']} {p.get('description') or ''}"
        hint = ("; the filing describes new construction, and a substation that isn't built yet is usually not in OpenStreetMap"
                if re.search(r"new|will create|to be built", text, re.I) else "")
        return _r("fail", "no endpoint matched an OpenStreetMap feature or place: " + ", ".join(e["name"] for e in p["endpoints"]) + hint)
    return _r("pass", f"{n} of {len(p['endpoints'])} endpoints located")


def fully_located(p, ctx):
    eps = p["endpoints"]
    missing = [e["name"] for e in eps if e.get("lat") is None]
    if len(eps) == 2 and len(missing) == 1:
        return _r("warn", f"'{missing[0]}' not found; the project is placed at its other endpoint (Sperry's rule)")
    return _r("pass", "all named endpoints located" if eps else "n/a")


def in_region(p, ctx):
    bad = []
    for e in p["endpoints"]:
        if e.get("lat") is None:
            continue
        st = geo.state_of(e["lat"], e["lon"], ("SC", "GA"))
        e["state"] = st
        if st is None:
            bad.append(f"{e['name']} ({e['lat']:.4f}, {e['lon']:.4f})")
    if bad:
        return _r("fail", "located outside South Carolina and Georgia: " + ", ".join(bad))
    return _r("pass", "every located endpoint is inside SC or GA (Census state outlines)")


def in_territory(p, ctx):
    import locate

    home = locate.HOME.get(p["utility"])
    if not home:
        return _r("fail", f"unknown filer '{p['utility']}'")
    bad = []
    for e in p["endpoints"]:
        if e.get("lat") is None:
            continue
        km = geo.km_to_state(e["lat"], e["lon"], home)
        if km > locate.HOME_REACH_KM:
            bad.append(f"{e['name']} is {km:.0f} km outside {home}")
    if bad:
        return _r("fail", "; ".join(bad) + f" (the filer's state; at most {locate.HOME_REACH_KM} km across the line is accepted)")
    return _r("pass", f"within {locate.HOME_REACH_KM} km of {home}, the filer's state")


def span_plausible(p, ctx):
    loc = [e for e in p["endpoints"] if e.get("lat") is not None]
    if len(loc) < 2:
        return _r("pass", "n/a")
    d = geo.haversine_km((loc[0]["lat"], loc[0]["lon"]), (loc[1]["lat"], loc[1]["lon"]))
    if d > MAX_SPAN_KM:
        return _r("fail", f"endpoints are {d:.0f} km apart (> {MAX_SPAN_KM} km): at least one match is probably the wrong place")
    return _r("pass", f"endpoints {d:.1f} km apart")


def length_consistent(p, ctx):
    loc = [e for e in p["endpoints"] if e.get("lat") is not None]
    if len(loc) < 2 or not p.get("miles"):
        return _r("pass", "n/a")
    d = geo.haversine_mi((loc[0]["lat"], loc[0]["lon"]), (loc[1]["lat"], loc[1]["lon"]))
    if d > p["miles"] * 1.25 + 1:
        return _r("warn", f"endpoints are {d:.1f} mi apart in a straight line but the filing describes {p['miles']:g} mi of work "
                          "(often a section of a longer line; otherwise a match is off)")
    return _r("pass", f"{d:.1f} mi straight line vs {p['miles']:g} mi filed")


def zone_consistent(p, ctx):
    zc = ctx["zone_centers"].get(p.get("zone") or "")
    if not zc or not p.get("center"):
        return _r("pass", "n/a")
    d = geo.haversine_km(zc, tuple(p["center"]))
    if d > 100:
        return _r("warn", f"{d:.0f} km from the median of planning zone {p['zone']}'s other projects: check the match (a same-name substation elsewhere is a common false match)")
    return _r("pass", f"{d:.0f} km from zone {p['zone']}'s median")


def two_parsers_agree(p, ctx):
    ga = p.get("_ga")
    if not ga:
        return _r("pass", "n/a")
    if not ga["parsers_agree"]:
        return _r("warn", f"text-line parser read '{p['name']}', position parser read '{ga['name_by_position']}'")
    return _r("pass", "text-line and word-position parsers read the same row")


def table_matches_detail(p, ctx):
    ga = p.get("_ga")
    if not ga:
        return _r("pass", "n/a")
    d = ga.get("detail")
    if not d:
        return _r("warn", "no detail page with this TEAMS number")
    issues = []
    if not ga["title_matches_detail"]:
        issues.append(f"title on the detail page (p{d['page']}) is '{d['title']}'")
    if ga.get("detail_need") and ga["detail_need"] != p["in_service"]:
        issues.append(f"need date on the detail page is {ga['detail_need']}")
    if issues:
        return _r("warn", "table and detail page differ: " + "; ".join(issues))
    return _r("pass", f"table row and detail page p{d['page']} agree")


def costs_consistent(p, ctx):
    an = [a for a in p.get("_anomalies", []) if a["id"].startswith("cost")]
    if an:
        return _r("warn", "; ".join(a["detail"] for a in an))
    if p["utility"] != "DESC":
        return _r("pass", "n/a (costs are redacted in the public version)")
    return _r("pass", "yearly amounts add up to the Total")


def voltage_found(p, ctx):
    stray = [m.group(0) for m in LETTER_O_KV.finditer(fix_voltage_typos(p["name"])[0])]
    stray_note = (f"the title has {', '.join(repr(s) for s in stray)} inside a voltage (a letter O for a zero?); "
                  "it was not read as a voltage, check the title") if stray else None
    if not p["kv"]:
        return _r("warn", "no voltage in the title or description" + (f"; {stray_note}" if stray_note else ""))
    notes = [n for n in p.get("_kv_notes", []) if "typo" in n] + ([stray_note] if stray_note else [])
    if notes:
        return _r("warn", "; ".join(notes))
    return _r("pass", "/".join(map(str, p["kv"])) + " kV")


RULES = [
    # id, label, blocking, fn, lowers location confidence
    ("extract_complete", "Every field parsed from the PDF", True, extract_complete, False),
    ("id_unique", "Project id is unique", True, id_unique, False),
    ("date_valid", "In-service date readable and 2020-2040", True, date_valid, False),
    ("place_named", "Title names a place", True, place_named, False),
    ("located", "At least one endpoint located", True, located, False),
    ("in_region", "Located inside SC or GA", True, in_region, False),
    ("in_territory", "Within reach of the filer's state", True, in_territory, False),
    ("span_plausible", "Line endpoints under 150 km apart", True, span_plausible, False),
    ("two_parsers_agree", "Two table parsers agree (Georgia)", False, two_parsers_agree, False),
    ("table_matches_detail", "Table row matches its detail page (Georgia)", False, table_matches_detail, False),
    ("costs_consistent", "Cost columns add up (DESC)", False, costs_consistent, False),
    ("date_normalized", "Date needed no repair", False, date_normalized, False),
    ("voltage_found", "Voltage found, no typo", False, voltage_found, False),
    ("fully_located", "Both endpoints located", False, fully_located, True),
    ("length_consistent", "Located span fits the filed miles", False, length_consistent, False),
    ("zone_consistent", "Near its planning zone's other projects (Georgia)", False, zone_consistent, True),
]


def overall_confidence(p: dict, lowered: int) -> str | None:
    confs = [e["confidence"] for e in p["endpoints"] if e.get("lat") is not None and e.get("confidence")]
    if not confs:
        return None
    level = min(CONF.index(c) for c in confs) - lowered
    return CONF[max(0, level)]


def run(projects: list[dict], zone_centers: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (kept, quarantine, rule summary)."""
    ctx = {"id_counts": Counter(p["id"] for p in projects), "zone_centers": zone_centers}
    summary = {rid: {"id": rid, "label": label, "blocking": blocking, "passed": 0, "warned": 0, "failed": 0} for rid, label, blocking, _, _ in RULES}
    kept, quarantine = [], []
    for p in projects:
        results, reasons, lowered = [], [], 0
        for rid, label, blocking, fn, lowers in RULES:
            status, detail = fn(p, ctx)
            if status == "fail" and not blocking:
                status = "warn"
            results.append({"id": rid, "status": status, "detail": detail})
            summary[rid][{"pass": "passed", "warn": "warned", "fail": "failed"}[status]] += 1
            if status == "fail":
                reasons.append(f"{label}: {detail}")
            if status == "warn" and lowers:
                lowered += 1
        p["checks"] = results
        p["confidence"] = overall_confidence(p, lowered)
        if reasons:
            p["_reasons"] = reasons
            quarantine.append(p)
        else:
            kept.append(p)
    return kept, quarantine, list(summary.values())
