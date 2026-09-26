"""What changed since the last filing: DESC's 2026-2030 project list read, checked, located and compared with the
2024-2028 list the app is built on. One command, offline (no network at all):

    backend/venv/Scripts/python backend/demo/gridlock/diff_filings.py [--today YYYY-MM-DD]

Stages (each prints its counts):
  1 extract   the 2026-2030 PDF, one project per page (extract_desc.filing_doc), every field as printed with its
              page; a second PDF engine (pdfium) must find each record's id, title, date and total on the same page,
              and the pages read by eye (MANUAL_SPOT_CHECK below) must still match
  2 check     normalize (build.normalize_desc) and locate (build.locate_all over the CACHED OpenStreetMap extracts and
              the cached Nominatim answers only: zero network calls, asserted), then checks.ALL_RULES: the build's 16
              plus the three this filing needed (an impossible calendar date; one id printed for two projects; a title
              whose voltage the description contradicts). The same three rules are run over the 2024-2028 + Georgia
              records to show they fail and warn on nothing there.
  3 diff      link the two DESC lists (link_filings): the same project id ('6853 B-F' = '6853BF', leading zeros dropped)
              for the same project (similar title, or the same description when it was retitled); else, under a
              different but related id (a shared work order or base number), the same title or the same description;
              then classify each project: carried over (same date and cost, a new in-service date, a new cost
              estimate), dropped (only in 2024-2028) or new (only in 2026-2030). Every row cites both PDF pages.
  4 preview   in memory only: the cross-state pairs the 2026-2030 list would make with the Georgia filings, computed by
              backend/gridlock.py's own overlap code (imported, unchanged), next to the same numbers for the 2024-2028
              list the app ranks now. "Still ahead / open now / ended" depends on the date: today = the real date,
              or --today to reproduce an earlier run.

Writes (each only when its content changed): a rerun on the same inputs leaves desc_2026_2030.json and
desc_changes.json byte for byte; desc_2026_preview.json also records the date and backend/gridlock.py's SHA-256, so a
run on another day (without --today) or after an engine change rewrites that one file.
  data/desc_2026_2030.json   the source (URL, SHA-256), every extracted page, the checked + located records
  data/desc_changes.json     the two lists linked and classified, with counts
  data/desc_2026_preview.json  the pair counts and the top pairs with reasons
It never writes data/projects.json: the live ranking still uses the 2024-2028 list until the integration step.

Ids: a 2026-2030 record gets the build's id ('DESC-' + its printed id), so a carried-over project keeps its 2024-2028 id
and both records printed as 6809 M are 'DESC-6809M' (both set aside for it). Give one edition a prefix before the two
are ever served side by side.

Wording rule (CLAUDE.md, NO DEFAMATION): a change is stated as filed ("the 2026-2030 list gives ...", later / earlier,
higher / lower). The lists don't say why a date or an estimate changed, and neither does this file.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402
import checks  # noqa: E402
import extract_desc  # noqa: E402
import extract_ga  # noqa: E402
import locate  # noqa: E402
import normalize as N  # noqa: E402
import osm  # noqa: E402
from pdf_text import page_texts, page_words  # noqa: E402

DATA = HERE / "data"
OUT_FILING = DATA / "desc_2026_2030.json"
OUT_CHANGES = DATA / "desc_changes.json"
OUT_PREVIEW = DATA / "desc_2026_preview.json"
COMMAND = "backend/venv/Scripts/python backend/demo/gridlock/diff_filings.py"
OLD, NEW = "desc", "desc_2026"
RETITLE_DESCRIPTION = 0.9  # same id + this similar a description = the same project under a new title
RENUMBER_TITLE = 0.85  # a different but related id + this similar a title (each the other's best match) = the same project
NO_MATCH_WORDS = "by id (with a similar title or description), by title, or by description"

# Pages of the 2026-2030 PDF read from the rendered page image (not from either text engine), 2026-09-26 (p53 added at the
# review). Each rerun checks the extraction still says the same; kv is what the title prints.
MANUAL_SPOT_CHECK = [
    (1, "06810 H", "Summerville 115kV Loop: Rebuild", [115], "04/31/26", 15_775_885),
    (5, "6809 N", "Batesburg - Saluda County 115kV: Rebuild", [115], "06/31/2026", 13_040_765),
    (12, "06367 D - G", "Jasper – Okatie 230 kV #2: Construct", [230], "12/01/2026", 19_280_474),
    (13, "6367 D", "Riverport 115kV Tap: Construct Tap", [115], "12/01/26", 41_389_047),
    (18, "6238 H", "Fairfax-Yemassee 115kV: Upgrade for DESCSQ #1151 Interconnection", [115], "9/7/2027", 20_350_000),
    (19, "6809 M", "St George - Sumter 230kV Tie: Rebuild Line from Santee Substation - Duke/Progress Energy Tie", [230],
     "12/31/2027", 4_569_331),
    (30, "6873", "Winnsboro West 230-115 kV Sub and Fold-in: Construct", [230, 115], "1/31/2028", 23_272_000),
    (39, "1060A, I, L", "Williams St Sub: Replace Sw House & Relays, AM Williams Sub: Replace Sw House, and McMeekin Sub: Add Sw House",
     [], "12/31/28", 11_182_924),
    (44, "06076 A", "Canadys-Ritter 115KV: Rebuild SPDC 230/115KV 1272 (Approx 18 Miles)", [230, 115], "6/1/2029", 38_121_795),
    (46, "6877 A", "Church Creek – Dawson 230 kV: Rebuild from Long Savannah to Dawson", [230], "12/31/2029", 42_375_000),
    (48, "6809 M", "Modoc – McCormick 115/46 kV Rebuild", [115, 46], "12/31/29", 19_800_000),
    (51, "0167 C-D", "Union Pier 115-13.8 kV Sub: Tap", [115], "12/31/30", 22_400_000),
    (53, "0147 A-I", "Clements Ferry Rd Sub: 115kV Tap from Cainhoy", [115], "12/31/2031", 9_750_000),
    (54, "06810 F", "VCS2-Ward 230kV: Rebuild Line", [230], "12/31/32", 18_875_000),
]
MANUAL_NOTES = {
    12: "Previous is printed '$14,303648'; read as $14,303,648, the value the Total implies",
    18: "no yearly table: 'Estimated cost of $20,350,000 is to be financed by the interconnection customer'",
    19: "the 2028 amount is printed '0' (no dollar sign)",
    30: "no yearly table: 'Estimated cost of $23,272,000 is to be financed by the interconnection customer'",
    46: "the 2026 amount is printed '0' (no dollar sign)",
    54: "Previous is printed '25,000' (no dollar sign)",
    1: "April has 30 days", 5: "June has 30 days",
    13: "the title says 115kV; the description says a 230 kV tap (as printed; the title's voltage is used)",
    48: "the same id as p19, a different project",
    44: "the title says 230/115KV; the description says 'No 230kV work associated with this project.' (as printed; the title's voltage is used)",
    53: "the 2028 amount is printed '$00' (read as $0; the columns then add to the Total). The description is word for word p37's "
        "of the 2024-2028 list ('0147 C, K', Cainhoy 115 kV Tap)",
}


def _flat(s) -> str:
    return " ".join(str(s or "").split())


def _money(v) -> str:
    return "no cost filed" if v is None else f"${v:,}"


def _sim_text(a, b) -> float:
    import difflib

    return difflib.SequenceMatcher(None, _flat(a).upper(), _flat(b).upper()).ratio()


# =========================================================================== 1 extract + manual spot check


def spot_check(recs: list[dict]) -> list[dict]:
    by_page = {r["page"]: r for r in recs}
    rows = []
    for page, pid, title, kv, ins, cost in MANUAL_SPOT_CHECK:
        r = by_page.get(page) or {}
        got = {"project_id": r.get("project_id"), "title": r.get("name"), "kv": N.kv_of(r.get("name") or "")[0],
               "in_service": r.get("in_service_raw"), "cost_total": r.get("cost_total")}
        want = {"project_id": pid, "title": title, "kv": kv, "in_service": ins, "cost_total": cost}
        rows.append({"page": page, "read_on_page": want, "extracted": got, "same": got == want, "note": MANUAL_NOTES.get(page)})
    return rows


# =========================================================================== 2 normalize, locate, check


def check_new_filing(fdoc: dict, recs: list[dict]) -> dict:
    src = {"id": NEW}
    projects = build.normalize_desc(recs, src)
    for p, r in zip(projects, recs):
        p["_cost_note"] = r.get("cost_note")
    index = locate.Index()  # raw/osm cache (build.py --refresh-osm is the only thing that downloads it)
    routes = build.LineRoutes()
    geocoder = osm.Nominatim(allow_network=False)  # cached answers only
    info = build.locate_all(projects, index, geocoder)
    assert geocoder.calls == 0, "the 2026-2030 stage must not call Nominatim"
    for p in projects:
        build.geometry_of(p, routes)
    kept, quar, summary = checks.run(projects, info["zone_centers"], rules=checks.ALL_RULES)

    def pub(p, fn):
        out = fn(p)
        out["filing"] = NEW
        out["cost_note"] = p.get("_cost_note")
        out["provenance"] = dict(out["provenance"], file=fdoc["source"]["file"])
        return out

    return {"projects": projects, "kept": kept, "quarantine": quar, "summary": summary, "locate": info,
            "public": [pub(p, build.public) for p in kept], "set_aside": [pub(p, build.quarantined) for p in quar]}


def locate_report(res: dict, old_id_of_page: dict) -> dict:
    """Which endpoint names are new to the DESC list, and whether the names the 2024-2028 build already placed land on
    the same point now (the locate stage is a function of the name and its project; nothing is copied). A set-aside
    record whose 2024-2028 version the build placed says how it was placed then."""
    old = json.loads((DATA / "projects.json").read_text(encoding="utf-8"))
    committed = {p["id"]: (p, True) for p in old["projects"]} | {q["id"]: (q, False) for q in old["quarantine"]}

    def then(page: int) -> dict | None:
        hit = committed.get(old_id_of_page.get(page) or "")
        if not hit:
            return None
        p, kept = hit
        return {"id": p["id"], "kept": kept, "confidence": p.get("confidence"),
                "endpoints": [{"name": e.get("name"), "confidence": e.get("confidence"), "match": e.get("match")}
                              for e in p.get("endpoints") or [] if e.get("lat") is not None]}

    placed = {}
    for p in old["projects"] + old["quarantine"]:
        if p["utility"] != "DESC":
            continue
        for e in p.get("endpoints") or []:
            if e.get("lat") is not None:
                placed.setdefault(N.endpoint_key(e.get("raw") or e.get("name") or ""), set()).add((round(e["lat"], 4), round(e["lon"], 4)))
    known_names = {N.endpoint_key(e.get("raw") or e.get("name") or "") for p in old["projects"] + old["quarantine"] if p["utility"] == "DESC"
                   for e in p.get("endpoints") or []}
    names, same, moved = {}, 0, []
    for p in res["projects"]:
        for e in p["endpoints"]:
            names.setdefault(e["key"], e)
    new_names = sorted(k for k in names if k not in known_names)
    for k, e in names.items():
        if k in placed and e.get("lat") is not None:
            if (round(e["lat"], 4), round(e["lon"], 4)) in placed[k]:
                same += 1
            else:
                moved.append({"name": e["name"], "now": [e["lat"], e["lon"]], "in_2024_build": sorted(placed[k])})
    unplaced = [{"id": q["id"], "page": q["provenance"]["page"], "name": q["name"],
                 "reason": next((r for r in q["_reasons"] if r.startswith(("At least one endpoint", "Title names"))), q["_reasons"][0]),
                 "in_the_2024_2028_build": then(q["provenance"]["page"])}
                for q in res["quarantine"] if any(r.startswith(("At least one endpoint", "Title names")) for r in q["_reasons"])]
    info = res["locate"]
    return {
        "sources": "cached OpenStreetMap extracts (raw/osm: substations_SC, substations_GA, lines_SC_GA) and the cached Nominatim "
                   "answers; no network call (asserted)",
        "network_calls": info["nominatim_calls"],
        "endpoint_names": len(names),
        "names_new_to_the_desc_list": new_names,
        "names_the_2024_build_placed": sum(1 for k in names if k in placed),
        "placed_at_the_same_point": same,
        "placed_elsewhere": moved,
        "from_cached_nominatim": info["nominatim_found"],
        "from_the_description": info["from_description"],
        "set_aside_unplaced": unplaced,
    }


def recheck_existing() -> dict:
    """The new rules (checks.FILING_RULES) over the 2024-2028 DESC records and the Georgia rows (the build's own extract +
    normalize; the rules read only ids, titles, descriptions and dates, so no locating is needed): nothing may fail."""
    desc = build.normalize_desc(extract_desc.extract(page_texts(HERE / "sources" / "desc_2024_2028_projects.pdf")), {"id": "desc"})
    ga_pdf = HERE / "sources" / "ga_2025_irp_vol3_public.pdf"
    texts = page_texts(ga_pdf)
    pages = extract_ga.find_table_pages(texts)
    ga = build.normalize_ga(extract_ga.extract(texts, page_words(ga_pdf, pages)), {"id": "ga_irp"})
    recs = desc + ga
    ctx = checks.context(recs, {})
    out = {}
    for rid, _label, _blocking, fn, _lowers in checks.FILING_RULES:
        res = [(p, fn(p, ctx)) for p in recs]
        out[rid] = {"records": len(recs), "passed": sum(1 for _, r in res if r[0] == "pass"),
                    "warned": [{"id": p["id"], "detail": r[1]} for p, r in res if r[0] == "warn"],
                    "failed": [{"id": p["id"], "detail": r[1]} for p, r in res if r[0] == "fail"]}
    return out


# =========================================================================== 3 diff


def _date_change(o: dict, n: dict) -> dict:
    oi, _ = N.parse_date(o.get("in_service_raw"))
    ni, _ = N.parse_date(n.get("in_service_raw"))
    row = {"old_raw": o.get("in_service_raw"), "new_raw": n.get("in_service_raw"), "old": oi, "new": ni}
    bad_new = N.impossible_dates(n.get("in_service_raw"))
    if oi and ni:
        if oi == ni:
            return {**row, "changed": False}
        months = round((date.fromisoformat(ni) - date.fromisoformat(oi)).days / 30.4375)
    else:  # an impossible date still names its month: compare months ('04/31/26' = April 2026)
        mo, mn = N.month_of(o.get("in_service_raw")), N.month_of(n.get("in_service_raw"))
        if not mo or not mn:
            return {**row, "changed": o.get("in_service_raw") != n.get("in_service_raw"), "months": None}
        months = (mn[0] - mo[0]) * 12 + (mn[1] - mo[1])
        if months == 0 and o.get("in_service_raw") == n.get("in_service_raw"):
            return {**row, "changed": False}
    row.update(changed=True, months=months, direction="later" if months > 0 else "earlier" if months < 0 else "same month")
    if bad_new:
        row["note"] = "the 2026-2030 date isn't on the calendar (" + "; ".join(bad_new) + "); compared by month"
    return row


def _span_words(months: int) -> str:
    m = abs(months)
    y, r = divmod(m, 12)
    parts = ([f"{y} year{'s' if y != 1 else ''}"] if y else []) + ([f"{r} month{'s' if r != 1 else ''}"] if r else [])
    return " ".join(parts) or "less than a month"


def _id_base(raw) -> str:
    """The number an id starts with, leading zeros dropped ('06367 A - C, H' -> '6367', '0147 A-I' -> '147')."""
    m = re.match(r"^\s*0*(\d+)", str(raw or ""))
    return m.group(1) if m else ""


def _related_ids(o: dict, n: dict) -> str | None:
    """How two DIFFERENT ids are related, as printed: a work order in common, or the same base number. None otherwise."""
    common = N.project_id_parts(o.get("project_id")) & N.project_id_parts(n.get("project_id"))
    if common:
        return "work order " + ", ".join(N.work_order_label(w, n.get("project_id")) for w in sorted(common)) + " in both ids"
    if _id_base(o.get("project_id")) and _id_base(o.get("project_id")) == _id_base(n.get("project_id")):
        return f"the same base number {_id_base(n.get('project_id'))}"
    return None


def _shared_place(o: dict, n: dict) -> str | None:
    """A place both titles name (endpoint keys, as normalize reads them), or None."""
    ko = {e["key"]: e["name"] for e in N.endpoints_of(o["name"])["endpoints"]}
    kn = {e["key"]: e["name"] for e in N.endpoints_of(n["name"])["endpoints"]}
    common = sorted(set(ko) & set(kn))
    return kn[common[0]] if common else None


def _mutual_best(rest_o: list[dict], rest_n: list[dict], score, ok) -> list[tuple[dict, dict]]:
    """Pairs (o, n) that pass ok(o, n) and are each the other's best-scoring candidate among the ones that pass."""
    out = []
    for n in list(rest_n):
        cands = [o for o in rest_o if ok(o, n)]
        if not cands:
            continue
        o = max(cands, key=lambda o: score(o, n))
        back = max((x for x in rest_n if ok(o, x)), key=lambda x: score(o, x))
        if back is n:
            out.append((o, n))
            rest_o.remove(o)
            rest_n.remove(n)
    return out


def link_filings(old: list[dict], new: list[dict]) -> list[tuple[dict, dict, str]]:
    """(old record, new record, how) for every project in both lists. Three steps, strictest first:
      1 the same id key, and the same project (a similar title, or the same description when it was retitled);
      2 a different id: a similar title (>= RENUMBER_TITLE) AND the ids share a work order or base number, or the titles
        share a place; each the other's best match ('06367 A - C, H' Riverport -> '6367 D' Riverport);
      3 a different id: the same description (>= RETITLE_DESCRIPTION) AND the ids share a work order or base number; each
        the other's best match ('0147 C, K' Cainhoy 115 kV Tap -> '0147 A-I' Clements Ferry Rd Sub: 115kV Tap from Cainhoy,
        word for word the same description)."""
    out, used_o, used_n = [], set(), set()
    for n in new:  # 1: the same id, and the same project
        cands = [o for o in old if id(o) not in used_o and N.project_id_key(o.get("project_id")) == N.project_id_key(n.get("project_id"))]
        same = [o for o in cands if N.title_similarity(o["name"], n["name"]) >= N.SAME_TITLE
                or _sim_text(o.get("description"), n.get("description")) >= RETITLE_DESCRIPTION]
        if same:
            o = max(same, key=lambda o: N.title_similarity(o["name"], n["name"]))
            retitled = N.title_similarity(o["name"], n["name"]) < N.SAME_TITLE
            out.append((o, n, "same id, same description, new title" if retitled else "same id"))
            used_o.add(id(o))
            used_n.add(id(n))
    rest_o = [o for o in old if id(o) not in used_o]
    rest_n = [n for n in new if id(n) not in used_n]

    def title_ok(o, n):  # 2: a title this similar is not enough alone ('X 115 kV Tap: Construct' titles look alike)
        return (N.title_similarity(o["name"], n["name"]) >= RENUMBER_TITLE
                and bool(_related_ids(o, n) or _shared_place(o, n)))

    for o, n in _mutual_best(rest_o, rest_n, lambda o, n: N.title_similarity(o["name"], n["name"]), title_ok):
        out.append((o, n, f"same title, new id ({_related_ids(o, n) or 'both titles name ' + _shared_place(o, n)})"))

    def desc_ok(o, n):  # 3: the same description under a new id and title
        return (bool(o.get("description")) and _sim_text(o.get("description"), n.get("description")) >= RETITLE_DESCRIPTION
                and bool(_related_ids(o, n)))

    for o, n in _mutual_best(rest_o, rest_n, lambda o, n: _sim_text(o.get("description"), n.get("description")), desc_ok):
        retitled = N.title_similarity(o["name"], n["name"]) < N.SAME_TITLE
        out.append((o, n, f"same description, new id{' and title' if retitled else ''} ({_related_ids(o, n)})"))
    return out


def shared_work_orders(rec: dict, others: list[dict]) -> list[dict]:
    """Records of the other list whose DIFFERENT id covers some of the same work orders ('0147 C, K' and '0147 A-I' share
    0147 C): stated as printed, never read as the same project."""
    parts, key = N.project_id_parts(rec.get("project_id")), N.project_id_key(rec.get("project_id"))
    out = []
    for q in others:
        common = parts & N.project_id_parts(q.get("project_id"))
        if common and N.project_id_key(q.get("project_id")) != key:
            out.append({"page": q["page"], "project_id": q.get("project_id"), "name": q["name"],
                        "work_orders": [N.work_order_label(w, rec.get("project_id")) for w in sorted(common)]})
    return out


def _work_order_words(rel: list[dict], which: str) -> str:
    return "".join(f" Work order {', '.join(r['work_orders'])} of its id also appears in the {which} list under '{r['project_id']}' "
                   f"(p{r['page']}, '{r['name']}')." for r in rel)


def diff(old: list[dict], new: list[dict], new_status: dict) -> dict:
    links = link_filings(old, new)
    linked_o = {id(o) for o, _, _ in links}
    linked_n = {id(n) for _, n, _ in links}
    rows = []
    for o, n, how in sorted(links, key=lambda t: t[1]["page"]):
        dc = _date_change(o, n)
        oc, nc = o.get("cost_total"), n.get("cost_total")
        cost = {"old": oc, "new": nc, "changed": oc != nc}
        if cost["changed"] and oc and nc:
            cost.update(delta=nc - oc, pct=round(100 * (nc - oc) / oc, 1), direction="higher" if nc > oc else "lower")
        if n.get("cost_note"):
            cost["new_note"] = n["cost_note"]
        title_changed = N.title_canon(o["name"]) != N.title_canon(n["name"])
        id_changed = N.project_id_key(o.get("project_id")) != N.project_id_key(n.get("project_id"))
        status_changed = _flat(o.get("status")) != _flat(n.get("status"))
        desc_changed = _flat(o.get("description")) != _flat(n.get("description"))
        changes = [k for k, v in (("in_service", dc["changed"]), ("cost", cost["changed"]), ("title", title_changed),
                                  ("id", id_changed), ("status", status_changed), ("description", desc_changed)) if v]
        words = [f"Listed in both, linked by: {how}."]
        if dc["changed"]:
            words.append(f"In-service date: the 2024-2028 list gives {dc['old_raw']} (p{o['page']}); the 2026-2030 list gives "
                         f"{dc['new_raw']} (p{n['page']})" + (f", {_span_words(dc['months'])} {dc['direction']}" if dc.get("months") else "")
                         + (f" ({dc['note']})" if dc.get("note") else "") + ".")
        else:
            words.append(f"In-service date {dc['new_raw']} in both.")
        if cost["changed"]:
            words.append(f"Estimated cost: {_money(oc)} (p{o['page']}) in the 2024-2028 list, {_money(nc)} (p{n['page']}) in the 2026-2030 list"
                         + (f", {_money(abs(cost['delta']))} ({abs(cost['pct']):g} %) {cost['direction']}" if cost.get("delta") else "") + ".")
        else:
            words.append(f"Estimated cost {_money(nc)} in both.")
        if title_changed:
            words.append(f"Title: '{o['name']}' became '{n['name']}'.")
        if id_changed:
            words.append(f"Project id: '{o.get('project_id')}' became '{n.get('project_id')}'.")
        if status_changed:
            words.append(f"Status: {o.get('status')} -> {n.get('status')}.")
        rows.append({
            "change": "carried_over", "linked_by": how, "changes": changes,
            "old": {"page": o["page"], "project_id": o.get("project_id"), "name": o["name"], "status": o.get("status")},
            "new": {"page": n["page"], "project_id": n.get("project_id"), "name": n["name"], "status": n.get("status"),
                    "checks": new_status.get(n["page"])},
            "in_service": dc, "cost": cost,
            **({"description": {"old": o.get("description"), "new": n.get("description")}} if desc_changed else {}),
            "summary": " ".join(words),
        })
    first_new_year = min(int(k) for r in new for k in (r.get("cost_by_year") or {}) if k.isdigit())
    for o in (o for o in old if id(o) not in linked_o):
        iso, _ = N.parse_date(o.get("in_service_raw"))
        before = bool(iso) and iso < f"{first_new_year}-01-01"
        rel = shared_work_orders(o, new)
        rows.append({
            "change": "dropped",
            "old": {"page": o["page"], "project_id": o.get("project_id"), "name": o["name"], "status": o.get("status"),
                    "in_service_raw": o.get("in_service_raw"), "in_service": iso, "cost_total": o.get("cost_total")},
            "new": None,
            "in_service_before_the_new_list": before,
            "shared_work_orders": rel,
            "summary": (f"In the 2024-2028 list (p{o['page']}, in service {o.get('in_service_raw')}, {_money(o.get('cost_total'))}). "
                        f"No project in the 2026-2030 list matches it {NO_MATCH_WORDS}. "
                        + (f"Its 2024-2028 in-service date is before {first_new_year}, the first year the new list covers."
                           if before else "The 2026-2030 list doesn't say why it isn't listed.")
                        + _work_order_words(rel, "2026-2030")),
        })
    for n in (n for n in new if id(n) not in linked_n):
        reused = [o for o in old if N.project_id_key(o.get("project_id")) == N.project_id_key(n.get("project_id"))]
        twins = [x for x in new if x is not n and N.project_id_key(x.get("project_id")) == N.project_id_key(n.get("project_id"))]
        iso, _ = N.parse_date(n.get("in_service_raw"))
        rel = shared_work_orders(n, old)
        rows.append({
            "change": "new",
            "old": None,
            "new": {"page": n["page"], "project_id": n.get("project_id"), "name": n["name"], "status": n.get("status"),
                    "in_service_raw": n.get("in_service_raw"), "in_service": iso, "cost_total": n.get("cost_total"),
                    "cost_note": n.get("cost_note"), "checks": new_status.get(n["page"])},
            "shared_work_orders": rel,
            "summary": (f"The 2026-2030 list gives in service {n.get('in_service_raw')} (p{n['page']}), {_money(n.get('cost_total'))}. "
                        f"No project in the 2024-2028 list matches it {NO_MATCH_WORDS}." + (f" Its id {n.get('project_id')} is the id of '{reused[0]['name']}' in the "
                                                             f"2024-2028 list (p{reused[0]['page']})." if reused else "")
                        + "".join(f" The 2026-2030 list also prints the id {n.get('project_id')} on p{x['page']}, for '{x['name']}'." for x in twins)
                        + _work_order_words(rel, "2024-2028")),
        })
    carried = [r for r in rows if r["change"] == "carried_over"]
    moved = [r for r in carried if r["in_service"]["changed"]]
    costed = [r for r in carried if r["cost"]["changed"]]
    dropped = [r for r in rows if r["change"] == "dropped"]
    counts = {
        "old_projects": len(old), "new_projects": len(new),
        "carried_over": len(carried),
        "carried_over_same_date_and_cost": sum(1 for r in carried if not r["in_service"]["changed"] and not r["cost"]["changed"]),
        "new_in_service_date": len(moved),
        "new_in_service_date_later": sum(1 for r in moved if (r["in_service"].get("months") or 0) > 0),
        "new_in_service_date_earlier": sum(1 for r in moved if (r["in_service"].get("months") or 0) < 0),
        "new_cost": len(costed),
        "new_cost_higher": sum(1 for r in costed if r["cost"].get("direction") == "higher"),
        "new_cost_lower": sum(1 for r in costed if r["cost"].get("direction") == "lower"),
        "retitled": sum(1 for r in carried if "title" in r["changes"]),
        "new_id": sum(1 for r in carried if "id" in r["changes"]),
        "dropped": len(dropped),
        "dropped_due_before_the_new_list": sum(1 for r in dropped if r["in_service_before_the_new_list"]),
        "new": sum(1 for r in rows if r["change"] == "new"),
        "total_cost_old_list": sum(o.get("cost_total") or 0 for o in old),
        "total_cost_new_list": sum(n.get("cost_total") or 0 for n in new),
    }
    return {"counts": counts, "rows": rows}


# =========================================================================== 4 preview (in memory)


def _engine_sha() -> str:
    return hashlib.sha256((BACKEND / "gridlock.py").read_bytes()).hexdigest()


def preview(kept_2026: list[dict], links: dict, today: date) -> dict:
    """Cross-state pairs with backend/gridlock.py's own code: the 2026-2030 DESC list with the Georgia filings, and,
    for comparison, the committed projects.json (2024-2028 DESC list) through the same code. `today` decides which
    shared build windows are still ahead, open now or ended (the engine's _today, set for this call only)."""
    sys.path.insert(0, str(BACKEND))
    import gridlock as G

    real_today = G._today
    G._today = lambda: today
    try:
        return _preview(G, kept_2026, links, today)
    finally:
        G._today = real_today


def _preview(G, kept_2026: list[dict], links: dict, today: date) -> dict:

    committed = json.loads((DATA / "projects.json").read_text(encoding="utf-8"))
    ga = [p for p in committed["projects"] if p["utility"] != "DESC"]
    prm = G._params(G.MAX_KM_DEFAULT, G.WINDOW_DEFAULT, "closest", "SC", "GA")  # DESC with all four Georgia ITS sponsors
    prm_app = G._params(G.MAX_KM_DEFAULT, G.WINDOW_DEFAULT, "closest", "DESC", "GPC")  # the app's default view

    def run(path: Path) -> tuple[list[dict], dict, list[dict]]:
        G.PROJECTS_FILE = path
        st = G._load()
        return G._compute(st, prm)["overlaps"], st, G._compute(st, prm_app)["overlaps"]

    def counts(rows: list[dict], st: dict) -> dict:
        shared = [r for r in rows if r["same_window"]]
        return {
            "desc_projects_on_the_map": sum(1 for p in st["placed"] if p["utility"] == "DESC"),
            "georgia_projects_on_the_map": sum(1 for p in st["placed"] if p["utility"] != "DESC"),
            "flagged": len(rows),
            "same_station": sum(1 for r in rows if r["shared_station"]),
            "by_tier": {t[0]: sum(1 for r in rows if r["tier"] == t[0]) for t in G.TIERS},
            "shared_build_window": len(shared),
            "shared_window_still_ahead": sum(1 for r in shared if r["ahead"] == "future"),
            "shared_window_open_now": sum(1 for r in shared if r["ahead"] == "open"),
            "shared_window_ended": sum(1 for r in shared if r["ahead"] == "past"),
            "same_station_with_window_ahead_or_open": sum(1 for r in shared if r["shared_station"] and r["ahead"] in ("future", "open")),
        }

    old_rows, old_st, old_app = run(DATA / "projects.json")
    old_counts = dict(counts(old_rows, old_st), app_default_view_desc_vs_gpc=counts(old_app, old_st))
    old_rank = {(r["a"], r["b"]): r["rank"] for r in old_rows}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "projects.json"
        path.write_text(json.dumps({"built_at": None, "sources": [], "projects": kept_2026 + ga, "quarantine": [], "report": {}}),
                        encoding="utf-8")
        new_rows, new_st, new_app = run(path)
        new_counts = dict(counts(new_rows, new_st), app_default_view_desc_vs_gpc=counts(new_app, new_st))
    by_id = {p["id"]: p for p in kept_2026 + ga}

    def pair(r: dict) -> dict:
        a, b = by_id[r["a"]], by_id[r["b"]]
        was = links.get(r["a"])
        return {
            "rank": r["rank"], "class": r["class_label"], "tier": r["tier_label"], "score": r["score"],
            "desc": {"id": a["id"], "name": a["name"], "in_service": a.get("in_service"), "page_2026_2030": a["provenance"]["page"],
                     "change_since_2024_2028": was["change"] if was else None},
            "georgia": {"id": b["id"], "utility": b["utility_name"], "name": b["name"], "in_service": b.get("in_service"),
                        "page": b["provenance"].get("page")},
            "distance_km": r["distance_km"], "shared_station": (r["shared_station"] or {}).get("name"),
            "same_window": r["same_window"], "ahead": r["ahead"],
            "rank_with_2024_2028": old_rank.get((was["old_id"], r["b"])) if was and was.get("old_id") else None,
            "reasons": r["reasons"],
        }

    new_keys = {((links.get(r["a"]) or {}).get("old_id") or r["a"], r["b"]) for r in new_rows}
    return {
        "_about": ("A preview, computed in memory: the cross-state pairs the 2026-2030 DESC list would make with the Georgia filings, "
                   "with the app's own overlap code (backend/gridlock.py, imported unchanged). The app still ranks the 2024-2028 "
                   "list; nothing here is live. Pairs are places the utilities could coordinate, as filed, never a statement "
                   "that they are or aren't coordinating."),
        "today": today.isoformat(),
        "today_note": ("the date the shared build windows are measured against (still ahead / open now / ended); a run on another "
                       "day recomputes those counts, and --today YYYY-MM-DD reproduces this file"),
        "engine": {"module": "backend/gridlock.py", "sha256": _engine_sha(), "max_km": prm["max_km"],
                   "window_months": prm["window_months"], "method": prm["method"], "a": prm["a"], "b": prm["b"],
                   "app_default_view": {"a": prm_app["a"], "b": prm_app["b"]}},
        "georgia": "the Georgia projects in data/projects.json (the build's, unchanged)",
        "with_2024_2028": old_counts,
        "with_2026_2030": new_counts,
        "pairs_also_flagged_with_2024_2028": sum(1 for k in new_keys if k in old_rank),
        "pairs_only_with_2026_2030": sum(1 for k in new_keys if k not in old_rank),
        "pairs_only_with_2024_2028": sum(1 for k in old_rank if k not in new_keys),
        "top_pairs": [pair(r) for r in new_rows[:12]],
        "same_station_pairs": [pair(r) for r in new_rows if r["shared_station"]][:25],
        "still_ahead_pairs": [pair(r) for r in new_rows if r["same_window"] and r["ahead"] == "future"][:25],
    }


# =========================================================================== main


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="DESC 2024-2028 -> 2026-2030: extract, check, diff, preview (offline)")
    ap.add_argument("--today", type=date.fromisoformat, default=None,
                    help="YYYY-MM-DD the preview measures build windows against (default: the real date)")
    args = ap.parse_args()
    today = args.today or date.today()
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.perf_counter()
    print("What changed since the last filing (DESC 2024-2028 -> 2026-2030)")

    fdoc = extract_desc.filing_doc(NEW)
    new = extract_desc.extract(page_texts(HERE / "sources" / extract_desc.FILINGS[NEW]["file"]))
    old = extract_desc.extract(page_texts(HERE / "sources" / extract_desc.FILINGS[OLD]["file"]))
    manual = spot_check(new)
    ex = fdoc["extraction"]
    print(f"  [extract ] {ex['pages']} pages, {ex['parsed_cleanly']} parsed cleanly, {len(ex['anomalies'])} anomalies; second reader "
          f"found {ex['second_reader']['all_found']}/{ex['second_reader']['pages_checked']}; pages read by eye match "
          f"{sum(r['same'] for r in manual)}/{len(manual)}")
    if not all(r["same"] for r in manual) or ex["second_reader"]["not_found"]:
        raise SystemExit("the extraction no longer matches the pages read by eye or the second reader: nothing was written")

    res = check_new_filing(fdoc, new)
    existing = recheck_existing()
    print(f"  [check   ] {len(res['kept'])} kept, {len(res['quarantine'])} set aside; located from the cache with "
          f"{res['locate']['nominatim_calls']} network calls; the {len(existing)} new rules on the 2024-2028 + Georgia records: "
          + ", ".join(f"{k} {len(v['failed'])} failed / {len(v['warned'])} warned of {v['records']}" for k, v in existing.items()))
    for q in res["quarantine"]:
        print(f"      set aside p{q['provenance']['page']:>2} {q['id']}: " + " | ".join(r[:110] for r in q["_reasons"]))

    status = {}
    for p in res["kept"]:
        status[p["provenance"]["page"]] = {"kept": True, "confidence": p.get("confidence"),
                                          "warnings": [c["id"] for c in p["checks"] if c["status"] == "warn"]}
    for q in res["quarantine"]:
        status[q["provenance"]["page"]] = {"kept": False, "reasons": q["_reasons"]}
    d = diff(old, new, status)
    old_ids = {r["page"]: "DESC-" + "".join(ch for ch in (r.get("project_id") or "") if ch.isalnum()).upper() for r in old}
    loc = locate_report(res, {r["new"]["page"]: old_ids[r["old"]["page"]] for r in d["rows"] if r["change"] == "carried_over"})
    c = d["counts"]
    print(f"  [diff    ] {c['carried_over']} carried over ({c['new_in_service_date']} with a new in-service date, {c['new_cost']} with a new "
          f"cost, {c['carried_over_same_date_and_cost']} unchanged in both), {c['dropped']} dropped, {c['new']} new")

    by_page_id = {p["provenance"]["page"]: p["id"] for p in res["projects"]}
    links = {}
    for r in d["rows"]:
        if r["new"]:
            links[by_page_id[r["new"]["page"]]] = {"change": r["change"] if r["change"] != "carried_over" else
                                                   ("carried over: " + (", ".join(r["changes"]) + " changed" if r["changes"] else "unchanged")),
                                                   "old_id": old_ids.get(r["old"]["page"]) if r["old"] else None}
    pv = preview(res["public"], links, today)
    w, n = pv["with_2024_2028"], pv["with_2026_2030"]
    print(f"  [preview ] today = {today}; with 2026-2030: {n['flagged']} pairs flagged, {n['same_station']} same-station, {n['shared_window_still_ahead']} with the "
          f"shared window still ahead ({n['shared_window_open_now']} open now); with 2024-2028: {w['flagged']} / {w['same_station']} / "
          f"{w['shared_window_still_ahead']} ({w['shared_window_open_now']} open now)")

    filing = {
        **fdoc,
        "command": COMMAND,
        "manual_spot_check": {"how": "read from the rendered page image, independently of both text engines (2026-09-26); rechecked on every run",
                              "pages": manual},
        "checks": {
            "rules": [{"id": rid, "label": label, "blocking": blocking, "new_for_this_filing": rid in {f[0] for f in checks.FILING_RULES}}
                      for rid, label, blocking, _f, _l in checks.ALL_RULES],
            "summary": res["summary"],
            "new_rules_on_2024_2028_and_georgia": existing,
        },
        "locate": loc,
        "projects": res["public"],
        "set_aside": res["set_aside"],
    }
    changes = {
        "_about": ("DESC's 2026-2030 list of transmission projects of $2M and above compared with its 2024-2028 list, both as "
                   "published on SCRTP. Each row cites both PDF pages. The lists don't say why a date or an estimate changed; "
                   "the rows state what each list gives."),
        "command": COMMAND,
        "sources": [{k: extract_desc.FILINGS[s][k] for k in ("id", "years", "title", "url", "file")} for s in (OLD, NEW)],
        "method": {
            "id_key": "an id with its spaces, dashes and commas removed and leading zeros dropped ('6853 B-F' = '6853BF', '06810 H' = '6810 H')",
            "link": (f"1) the same id key and the same project (title similarity >= {N.SAME_TITLE}, or description similarity >= "
                     f"{RETITLE_DESCRIPTION} when the title changed); 2) a different id: title similarity >= {RENUMBER_TITLE} and the "
                     "ids share a work order or base number (or the titles name a place in common), each the other's best match; "
                     f"3) a different id: description similarity >= {RETITLE_DESCRIPTION} and the ids share a work order or base "
                     "number, each the other's best match. A project no step links is dropped (2024-2028 only) or new (2026-2030 only)"),
            "dates": "compared as calendar dates; an impossible date is compared by its month",
            "costs": "the filed Total of each list",
        },
        **d,
    }
    wrote = [p.name for p, doc in ((OUT_FILING, filing), (OUT_CHANGES, changes), (OUT_PREVIEW, pv))
             if extract_desc.write_json_if_changed(p, doc)]
    print(f"  [write   ] {', '.join(wrote) or 'nothing changed'}   ({time.perf_counter() - t0:.1f} s)")


if __name__ == "__main__":
    main()
