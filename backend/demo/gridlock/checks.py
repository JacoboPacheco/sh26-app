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
from normalize import (SAME_TITLE, fix_voltage_typos, impossible_dates, kv_of, project_id_key, project_id_parts,
                       title_similarity, work_order_label)

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


def date_real(p, ctx):
    """Every date printed in the in-service field is on the calendar. date_valid already sets aside a field whose
    (last) date can't be read; this names the reason ('April has 30 days') and also catches an impossible date in an
    earlier phase of a phased field, which parse_date doesn't read. Met in DESC's 2026-2030 filing ('04/31/26' p1,
    '06/31/2026' p5)."""
    bad = impossible_dates(p.get("in_service_raw"))
    if bad:
        return _r("fail", "; ".join(bad) + " (as printed in the filing; which date was meant can't be told from it)")
    return _r("pass", "every printed date is on the calendar" if p.get("in_service_raw") else "n/a (no date printed)")


def _page(q: dict) -> str:
    pg = (q.get("provenance") or {}).get("page")
    return f"p{pg}" if pg else "another row"


def id_one_project(p, ctx):
    """One project id, one project. id_unique counts records per id; this says whether the records sharing an id
    are the same project listed twice or two DIFFERENT projects under one id (DESC's 2026-2030 filing prints 6809 M
    for 'St George - Sumter 230kV Tie' on p19 and for 'Modoc - McCormick 115/46 kV Rebuild' on p48): the id then
    can't link the record to anything, so both are set aside. An id whose work orders fall inside another project's
    id range (6367 D inside 06367 D - G) is flagged, not set aside: the ranges are the filer's own bookkeeping."""
    raw = p.get("project_id_raw")
    if not raw:
        return _r("pass", "n/a (no project id)")
    src = (p.get("provenance") or {}).get("source")
    others = [q for q in ctx["by_source"].get(src, []) if q is not p]
    key, parts = ctx["id_key"][id(p)], ctx["id_parts"][id(p)]
    reused = [q for q in others if ctx["id_key"][id(q)] == key and title_similarity(p["name"], q["name"]) < SAME_TITLE]
    if reused:
        return _r("fail", f"the id {raw} is also printed on " + "; ".join(f"{_page(q)} for '{q['name']}'" for q in reused)
                  + ": one id for different projects, so the id can't say which project a row belongs to")
    inside = [q for q in others if ctx["id_key"][id(q)] != key and parts & ctx["id_parts"][id(q)]
              and title_similarity(p["name"], q["name"]) < SAME_TITLE]
    if inside:
        return _r("warn", "; ".join(
            f"the id '{q.get('project_id_raw')}' ({_page(q)}, '{q['name']}') also covers work order "
            f"{', '.join(work_order_label(w, raw) for w in sorted(parts & ctx['id_parts'][id(q)]))}" for q in inside)
                  + " (as printed): check the id")
    return _r("pass", f"no other project in this filing uses the id {raw}")


NO_KV_WORK = re.compile(r"\bno\s+(\d{2,3})\s?kV\b[^.]*", re.I)


def kv_title_matches_description(p, ctx):
    """The title's voltage and the description's agree. The build takes kV from the title (kv_of), so a description
    that names only other voltages, or says there is no work at a voltage the title names, is flagged, not set aside:
    the record keeps the title's voltage and says so. Met in DESC's 2026-2030 filing only: p13 'Riverport 115kV Tap'
    whose description says 'a 230 kV Tap', p44 'Canadys-Ritter 115KV: Rebuild SPDC 230/115KV' whose description says
    'No 230kV work associated with this project'."""
    title_kv, _ = kv_of(p.get("name"))
    desc_kv, _ = kv_of(p.get("description"))
    said = [m.group(0).strip() for m in NO_KV_WORK.finditer(p.get("description") or "") if int(m.group(1)) in title_kv]
    if not title_kv or not (desc_kv or said):
        return _r("pass", "n/a (no voltage in the title or the description)")
    fmt = lambda kv: "/".join(map(str, kv)) + " kV"  # noqa: E731
    if said:
        return _r("warn", f"the title names {fmt(title_kv)}; the description says '{said[0]}' (as printed; the title's voltage is used)")
    if not set(title_kv) & set(desc_kv):
        return _r("warn", f"the title names {fmt(title_kv)}; the description names {fmt(desc_kv)} (as printed; the title's voltage is used)")
    return _r("pass", f"{fmt(title_kv)} in the title, {fmt(desc_kv)} in the description")


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

# Three checks added for DESC's 2026-2030 filing, which prints impossible dates, one id for two projects, and titles whose
# voltage the description contradicts. build.py (data/projects.json) and the committed fault test (faults.py) still run
# RULES alone, so both reproduce exactly what they did; diff_filings.py runs ALL_RULES on the new filing. None of the
# three fails or warns on any record of the 2024-2028 filing or the Georgia plan (diff_filings.py re-checks all 252 and
# reports it). Moving them into RULES is the integration step: then rerun build.py and faults.py (see README).
FILING_RULES = [
    ("id_one_project", "One project id, one project", True, id_one_project, False),
    ("date_real", "Every printed date is on the calendar", True, date_real, False),
    ("kv_title_matches_description", "Title and description agree on voltage", False, kv_title_matches_description, False),
]
_AFTER = {"id_one_project": "id_unique", "date_real": "date_valid", "kv_title_matches_description": "voltage_found"}  # next to its sibling
ALL_RULES = []
for _rule in RULES:
    ALL_RULES.append(_rule)
    ALL_RULES += [f for f in FILING_RULES if _AFTER[f[0]] == _rule[0]]


def overall_confidence(p: dict, lowered: int) -> str | None:
    confs = [e["confidence"] for e in p["endpoints"] if e.get("lat") is not None and e.get("confidence")]
    if not confs:
        return None
    level = min(CONF.index(c) for c in confs) - lowered
    return CONF[max(0, level)]


def context(projects: list[dict], zone_centers: dict) -> dict:
    """What the rules know about the whole batch: id counts, and per filing its records' id keys and work orders."""
    by_source: dict = {}
    for p in projects:
        by_source.setdefault((p.get("provenance") or {}).get("source"), []).append(p)
    return {
        "id_counts": Counter(p["id"] for p in projects), "zone_centers": zone_centers, "by_source": by_source,
        "id_key": {id(p): project_id_key(p.get("project_id_raw")) for p in projects},
        "id_parts": {id(p): project_id_parts(p.get("project_id_raw")) for p in projects},
    }


def run(projects: list[dict], zone_centers: dict, rules: list | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (kept, quarantine, rule summary). rules: RULES (the build's 16) unless given, e.g. ALL_RULES."""
    rules = RULES if rules is None else rules
    ctx = context(projects, zone_centers)
    summary = {rid: {"id": rid, "label": label, "blocking": blocking, "passed": 0, "warned": 0, "failed": 0} for rid, label, blocking, _, _ in rules}
    kept, quarantine = [], []
    for p in projects:
        results, reasons, lowered = [], [], 0
        for rid, label, blocking, fn, lowers in rules:
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
