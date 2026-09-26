"""Build plans data pipeline: public filings -> located, validated projects. One command rebuilds everything.

    backend/venv/Scripts/python backend/demo/gridlock/build.py [--import-dir DIR] [--refresh-osm] [--offline] [--no-cache]

Stages (each prints its counts and time; the same report is written into data/projects.json):
  1 extract    DESC: one project per PDF page. Georgia: Table 2 of the ITS 10-year plan, parsed twice
               (text lines and word positions) + the per-project detail pages.
  2 normalize  dates (2-digit years, Excel serials, phases), kV, kind, endpoint names, miles, build windows
  3 locate     endpoint names -> OpenStreetMap substations/plants (3 cached bulk Overpass queries), then
               Nominatim for leftovers (<= 60 calls, cached); confidence + reason on every endpoint
  4 checks     named rules; a failed blocking rule sends the record to quarantine WITH its reasons
  5 write      data/projects.json (projects, quarantine, report incl. what changed since the last run)
               and data/basemap.json (SC + GA outlines, OSM lines >= 115 kV)

--import-dir   where to copy missing source PDFs from (searched recursively by file name)
--refresh-osm  re-download the Overpass extracts (otherwise raw/osm/ is reused)
--offline      no network at all: cached OSM + cached Nominatim answers only
--no-cache     re-read the PDFs instead of using raw/pdf_cache/
Runtime: ~10 s with warm caches; the first run reads the 668-page Georgia PDF (~90 s) and downloads OSM.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import checks  # noqa: E402
import extract_desc  # noqa: E402
import extract_ga  # noqa: E402
import geo  # noqa: E402
import locate  # noqa: E402
import normalize as N  # noqa: E402
import osm  # noqa: E402
import selftest  # noqa: E402
from pdf_text import page_texts, page_words, sha256  # noqa: E402

SOURCES_DIR = HERE / "sources"
DATA = HERE / "data"
OUT = DATA / "projects.json"
OUT_BASEMAP = DATA / "basemap.json"
SPERRY = HERE / "sperry_example.json"

SOURCES = [
    {
        "id": "desc",
        "utility": "DESC",
        "title": "Dominion Energy South Carolina: Planned Transmission Projects $2M and above, 2024-2028 (project descriptions)",
        "file": "desc_2024_2028_projects.pdf",
        "import_name": "2024-2028-2million-and-above-project-descriptions.pdf",
        "url": "https://www.scrtp.com/assets/pdfs/home/2024-2028-2million-and-above-project-descriptions.pdf",
        "publisher": "South Carolina Regional Transmission Planning (SCRTP)",
    },
    {
        "id": "ga_irp",
        "utility": "GA",
        "title": "Georgia Power 2025 IRP, Technical Appendix Volume 3: Transmission Plan (2024 GA ITS Ten-Year Plan 2025-2034), PUBLIC DISCLOSURE version",
        "file": "ga_2025_irp_vol3_public.pdf",
        "import_name": "2025 IRP Volume 3 PUBLIC DISCLOSURE.pdf",
        "url": "https://psc.ga.gov/search/facts-docket/?docketId=56002",
        "publisher": "Georgia Public Service Commission, Docket 56002 (redacted public-disclosure filing; only unredacted fields are used)",
    },
]
CONTEXT_SOURCES = [
    {"title": "Georgia Power: Georgia Power files 2025 IRP (press release; ~8,200 MW of load growth expected over six years)",
     "url": "https://www.georgiapower.com/news-hub/press-releases/georgia-power-files-2025-irp-plan-to-meet-energy-needs.html"},
    {"title": "OpenStreetMap contributors (ODbL 1.0), via the Overpass API and Nominatim", "url": "https://www.openstreetmap.org/copyright"},
    {"title": "U.S. Census Bureau cartographic boundary file cb_2024_us_state_20m (public domain)",
     "url": "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_20m.zip"},
]
UTILITY_NAMES = {
    "DESC": "Dominion Energy South Carolina",
    "GPC": "Georgia Power",
    "GTC": "Georgia Transmission Corp.",
    "MEAG": "MEAG Power",
    "DU": "Dalton Utilities",
}
SPONSOR_TO_UTILITY = {"GPC": "GPC", "SAV": "GPC", "GTC": "GTC", "MEAG": "MEAG", "DU": "DU"}


# =========================================================================== helpers


class Stages:
    def __init__(self):
        self.rows: list[dict] = []

    def run(self, sid: str, label: str, fn, n_in):
        """fn() -> (result, n_out, note); n_in may be a function of the result (counted by the stage itself)."""
        t = time.perf_counter()
        result, n_out, note = fn()
        ms = round((time.perf_counter() - t) * 1000)
        if callable(n_in):
            n_in = n_in(result)
        self.rows.append({"id": sid, "label": label, "in": n_in, "out": n_out, "ms": ms, "note": note})
        print(f"  [{sid:12}] {label:50} in {n_in!s:>5}  out {n_out!s:>5}  {ms:>6} ms   {note}")
        return result


def ensure_sources(import_dir: Path | None) -> list[dict]:
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for s in SOURCES:
        dest = SOURCES_DIR / s["file"]
        if not dest.exists():
            found = None
            if import_dir and import_dir.exists():
                found = next(iter(import_dir.rglob(s["import_name"])), None)
            if not found:
                raise SystemExit(
                    f"missing source {dest}\n  download it from {s['url']} and save it there, or pass --import-dir <folder containing '{s['import_name']}'>"
                )
            shutil.copyfile(found, dest)
            print(f"  copied {found} -> {dest}")
        out.append({**s, "path": dest, "sha256": sha256(dest)})
    return out


def _dedupe(xs):
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# =========================================================================== stage 2: normalize


def _common(p: dict) -> dict:
    parsed = N.endpoints_of(p["name_for_places"])
    p["endpoints_parsed"] = parsed["endpoints"]
    p["via"] = parsed["via"]
    p["name_prefixes"] = parsed["prefixes"]
    p["_name_notes"] = parsed["notes"]
    p["customer_named"] = parsed["customer"]
    p["kv"], kv_notes = N.kv_of(p["name"], p.get("description"))
    p["_kv_notes"] = _dedupe(kv_notes)
    p["kind"], p["kind_basis"] = N.kind_of(p["name"], p.get("description"), len(parsed["endpoints"]))
    p["miles"], p["miles_text"] = N.miles_of(p.get("description"), p["name"])
    p["in_service"], p["_date_note"] = N.parse_date(p["in_service_raw"])
    return p


def normalize_desc(recs: list[dict], src: dict) -> list[dict]:
    out = []
    for r in recs:
        pid = "DESC-" + re.sub(r"[^0-9A-Za-z]", "", r.get("project_id") or f"P{r['page']}").upper()
        p = {
            "id": pid, "utility": "DESC", "utility_name": UTILITY_NAMES["DESC"], "state": "SC",
            "name": r["name"], "name_for_places": r["name"], "project_id_raw": r.get("project_id"),
            "description": r.get("description"), "need": r.get("need"), "status": r.get("status"),
            "in_service_raw": r.get("in_service_raw"), "cost_usd": r.get("cost_total"), "cost_by_year": r.get("cost_by_year"),
            "rate_base_note": r.get("rate_base_note"), "zone": None, "teams_no": None, "sponsor_raw": None,
            "provenance": {"source": src["id"], "page": r["page"], "text": r["text"].strip()},
            "_parse_errors": r["parse_errors"], "_anomalies": r["anomalies"],
        }
        _common(p)
        p["build_window"] = N.build_window_desc(p["in_service"], p["cost_by_year"])
        out.append(p)
    return out


def normalize_ga(ga: dict, src: dict) -> list[dict]:
    out = []
    for r in ga["rows"]:
        d = r.get("detail") or {}
        utility = SPONSOR_TO_UTILITY.get(r["sponsor"])
        # place names come from the detail-page title when it corrects the table (e.g. TALLBOT -> TALBOT)
        name_for_places = d.get("title") if d and not r["title_matches_detail"] and len(d.get("title", "")) >= len(r["name"]) * 0.6 else r["name"]
        errors = []
        if not utility:
            errors.append(f"unknown sponsor '{r['sponsor']}'")
        p = {
            "id": f"GA-{r['teams_no']}", "utility": utility or r["sponsor"], "utility_name": UTILITY_NAMES.get(utility or "", r["sponsor"]),
            "state": "GA", "name": r["name"], "name_for_places": name_for_places, "project_id_raw": r["teams_no"],
            "description": d.get("description"), "need": None, "status": None,
            "in_service_raw": r["need_date_raw"], "cost_usd": None, "cost_by_year": None,
            "zone": r["zone"], "teams_no": r["teams_no"], "sponsor_raw": r["sponsor"], "plan_year": r["year"],
            "change_ten_year": d.get("change_ten_year"), "change_irp": d.get("change_irp"),
            "provenance": {"source": src["id"], "page": r["page"], "text": "\n".join(r["row_lines"]), "detail_page": d.get("page")},
            "_parse_errors": errors, "_anomalies": [],
            "_ga": {
                "parsers_agree": r["parsers_agree"], "name_by_position": r["name_by_position"], "detail": d or None,
                "title_matches_detail": r["title_matches_detail"],
                "detail_need": N.parse_date(d.get("need_date_raw"))[0] if d.get("need_date_raw") else None,
            },
        }
        _common(p)
        p["build_window"] = N.build_window_ga(p["in_service"], d.get("start_date_raw"), d.get("page"))
        out.append(p)
    return out


# =========================================================================== stage 3: locate


def geometry_of(p: dict, routes: "LineRoutes | None") -> None:
    loc = [e for e in p["endpoints"] if e.get("lat") is not None]
    if not loc:
        p["geometry"], p["center"] = None, None
        return
    if len(loc) == 1:
        p["geometry"] = {"type": "point", "coords": [[loc[0]["lon"], loc[0]["lat"]]], "basis": "one located endpoint"}
        p["center"] = [loc[0]["lat"], loc[0]["lon"]]
        return
    a, b = loc
    mid = geo.midpoint((a["lat"], a["lon"]), (b["lat"], b["lon"]))
    p["center"] = [round(mid[0], 6), round(mid[1], 6)]
    route = routes.route(a, b, p.get("kv")) if routes else None
    if route:
        p["geometry"] = {"type": "segment", "coords": route["coords"], "basis": route["basis"]}
    else:
        p["geometry"] = {"type": "segment", "coords": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]],
                         "basis": "straight line between the two located endpoints (the real route is not public)"}


KV_CLASSES = (46, 69, 115, 138, 161, 230, 345, 500)


def _kv_class(v: float) -> int:
    return min(KV_CLASSES, key=lambda c: abs(c - v))


class LineRoutes:
    """Named OSM power lines ('McIntosh - Blandford 230kV'): when a project's two endpoints are the two ends of
    a named line AT THE PROJECT'S VOLTAGE, the mapped route replaces the straight segment (two substations are
    often joined by both a 115 kV and a 230 kV line on different routes)."""

    def __init__(self, refresh: bool = False):
        by_pair: dict[frozenset, list[dict]] = {}
        for e in osm.overpass("lines_SC_GA", refresh=refresh)["elements"]:
            tags = e.get("tags", {})
            name = tags.get("name")
            if not name or not e.get("geometry"):
                continue
            parsed = N.endpoints_of(name)["endpoints"]
            if len(parsed) != 2:
                continue
            pair = frozenset(locate.core(x["key"]) for x in parsed)
            kv = {_kv_class(v) for v in locate._kv_list(tags.get("voltage"))} or {_kv_class(v) for v in N.kv_of(name)[0]}
            if len(pair) == 2:
                by_pair.setdefault(pair, []).append({"id": e["id"], "name": name, "kv": kv, "pts": [(g["lat"], g["lon"]) for g in e["geometry"]]})
        self.by_pair = by_pair

    def route(self, a: dict, b: dict, kv: list[int] | None = None) -> dict | None:
        pair = frozenset((locate.core(a["key"]), locate.core(b["key"])))
        ways = self.by_pair.get(pair)
        if not ways:
            return None
        want = {_kv_class(v) for v in kv or []}
        if want:  # a 115 kV project never follows the 230 kV line between the same two stations
            ways = [w for w in ways if w["kv"] & want]
            if not ways:
                return None
        A, B = (a["lat"], a["lon"]), (b["lat"], b["lon"])
        span = geo.haversine_km(A, B)
        ways = [w for w in ways if min(geo.haversine_km(A, p) + geo.haversine_km(p, B) for p in (w["pts"][0], w["pts"][-1])) < span + 25]
        if not ways:
            return None
        # chain greedily from the way end nearest A
        remaining = list(ways)
        start = min(remaining, key=lambda w: min(geo.haversine_km(A, w["pts"][0]), geo.haversine_km(A, w["pts"][-1])))
        remaining.remove(start)
        pts = start["pts"] if geo.haversine_km(A, start["pts"][0]) <= geo.haversine_km(A, start["pts"][-1]) else start["pts"][::-1]
        used = [start["id"]]
        while remaining:
            tail = pts[-1]
            nxt = min(remaining, key=lambda w: min(geo.haversine_km(tail, w["pts"][0]), geo.haversine_km(tail, w["pts"][-1])))
            d0, d1 = geo.haversine_km(tail, nxt["pts"][0]), geo.haversine_km(tail, nxt["pts"][-1])
            if min(d0, d1) > 0.5:
                break
            pts = pts + (nxt["pts"][1:] if d0 <= d1 else nxt["pts"][::-1][1:])
            used.append(nxt["id"])
            remaining.remove(nxt)
        if geo.haversine_km(A, pts[0]) > 3 or geo.haversine_km(B, pts[-1]) > 3:
            return None
        simple = _dp([[round(lon, 5), round(lat, 5)] for lat, lon in pts], 0.0015)
        coords = [[a["lon"], a["lat"]]] + simple + [[b["lon"], b["lat"]]]
        return {"coords": coords, "basis": f"route of the OSM line '{ways[0]['name']}' (ways {', '.join(map(str, used[:6]))}{'…' if len(used) > 6 else ''})"}


def _dp(pts: list, tol: float) -> list:
    sys.path.insert(0, str(HERE.parent))
    import build_outline

    return build_outline._dp(pts, tol)


DESC_PAIR = re.compile(r"([A-Z][A-Za-z.'&]+(?: [A-Z#][A-Za-z0-9.'&]*)*)\s*[–-]\s*([A-Z][A-Za-z.'&]+(?: [A-Z][A-Za-z.'&]+)*)\s*\d{2,3}\s?kV")
LEADING_VERBS = re.compile(r"^(?:(?:Construct|Rebuild|Build|Install|Upgrade|Replace|Reconductor|Fold|Expand|Add|in|the|existing|new|a|an)\s+)+", re.I)


def locate_all(projects: list[dict], index: locate.Index, geocoder: osm.Nominatim) -> dict:
    mappable = [p for p in projects if p["utility"] in locate.HOME]
    for p in projects:
        if p["utility"] not in locate.HOME:  # unknown sponsor: nothing to match against; checks quarantine it
            p["endpoints"] = [dict(e, lat=None, lon=None, osm=None, confidence=None, match=None, state=None) for e in p["endpoints_parsed"]]
    # OSM names: pass 1 without zone knowledge, pass 2 with each Georgia zone's median location
    for p in mappable:
        p["endpoints"] = locate.locate_project(index, p, None)
    zc = locate.zone_centers(mappable)
    for p in mappable:
        if p.get("zone") in zc:
            p["endpoints"] = locate.locate_project(index, p, zc[p["zone"]])

    # leftovers: Nominatim, one answer per (state, name) shared by every project that names it
    need_kv: dict[tuple, int] = {}
    for p in mappable:
        for e in p["endpoints"]:
            k = (locate.HOME[p["utility"]], e["key"])
            need_kv[k] = max(need_kv.get(k, 0), max(p["kv"] or [0]))
    memo: dict[tuple, list[dict]] = {}

    def candidates(p, e):
        k = (locate.HOME[p["utility"]], e["key"])
        if k not in memo:
            memo[k] = locate.nominatim_candidates(index, geocoder, e, k[0], need_kv.get(k) or None)
        return memo[k]

    todo = []
    for p in mappable:
        none_located = all(x.get("lat") is None for x in p["endpoints"])
        for i, e in enumerate(p["endpoints"]):
            if e.get("lat") is None:  # DESC first (the headline pair), then projects with nothing located
                todo.append((0 if p["utility"] == "DESC" else 1 if p["utility"] == "GPC" else 2, 0 if none_located else 1, p["id"], i, p))
    todo.sort(key=lambda t: t[:4])
    tried = found = 0
    for *_, i, p in todo:
        other = p["endpoints"][1 - i] if len(p["endpoints"]) == 2 else None
        tried += 1
        e = p["endpoints"][i]
        hit = next((c for c in candidates(p, e) if locate.fits(c, p, other, zc.get(p.get("zone") or ""))), None)
        if hit:  # shared answer, this project's own wording
            p["endpoints"][i] = dict(hit, name=e["name"], raw=e["raw"], key=e["key"])
            found += 1

    # last resort for a project with nothing located: its description names the line it sits on
    from_desc = 0
    for p in mappable:
        if any(e.get("lat") is not None for e in p["endpoints"]) or not p.get("description"):
            continue
        title_keys = {e["key"] for e in p["endpoints"]}
        for m in DESC_PAIR.finditer(p["description"]):
            pair = N.endpoints_of(LEADING_VERBS.sub("", m.group(0)))["endpoints"]
            if len(pair) != 2 or not title_keys & {e["key"] for e in pair}:
                continue
            trial = dict(p, endpoints_parsed=pair)
            eps = locate.locate_project(index, trial, zc.get(p.get("zone") or ""))
            for j, e in enumerate(eps):
                if e.get("lat") is None:
                    hit = next((c for c in candidates(p, e) if locate.fits(c, p, eps[1 - j], zc.get(p.get("zone") or ""))), None)
                    if hit:
                        eps[j] = dict(hit, name=e["name"], raw=e["raw"], key=e["key"])
            if any(e.get("lat") is not None for e in eps):
                for e in eps:
                    if e.get("lat") is not None:
                        e["confidence"] = "low"
                        e["match"] = f"from the description ('{m.group(0).strip()}'): " + e["match"]
                p["endpoints"] = eps
                p["_name_notes"] = p.get("_name_notes", []) + [f"endpoints taken from the description: '{m.group(0).strip()}'"]
                from_desc += 1
                break
    return {"zone_centers": zc, "nominatim_tried": tried, "nominatim_found": found, "nominatim_calls": geocoder.calls,
            "nominatim_cached_queries": len(geocoder.cache), "from_description": from_desc}


# =========================================================================== reproduce Sperry's example


def _canon_title(s: str) -> str:
    s = s.upper().replace("–", "-")
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"(\d)\s*KV", r"\1KV", s)
    return re.sub(r"\s+", " ", s).strip()


def sperry_report(all_projects: list[dict]) -> dict:
    import difflib

    ex = json.loads(SPERRY.read_text(encoding="utf-8"))
    P = {p["project_id"]: p for p in ex["projects"]}
    dates = {}
    for p in ex["projects"]:
        iso, note = N.parse_date(p["in_service_date_raw"])
        dates[p["project_id"]] = {"raw": p["in_service_date_raw"], "iso": iso, "note": note}
    rows = []
    for o in ex["overlaps"]:
        a, b = P[o["project_id_a"]], P[o["project_id_b"]]
        mi = geo.haversine_mi((a["lat_center"], a["lon_center"]), (b["lat_center"], b["lon_center"]))
        days = N.days_between(dates[a["project_id"]]["iso"], dates[b["project_id"]]["iso"])
        ok = abs(round(mi, 2) - o["distance_mi"]) <= 0.01 and days == o["time_gap_days"]
        rows.append({"overlap_id": o["overlap_id"], "a": a["project_id"], "b": b["project_id"], "expected_mi": o["distance_mi"],
                     "ours_mi": round(mi, 2), "expected_days": o["time_gap_days"], "ours_days": days, "ok": ok})
    # every cross-utility pair under 25 mi, to show we flag exactly their six and nothing else
    flagged = set()
    for a in ex["projects"]:
        for b in ex["projects"]:
            if a["utility"] < b["utility"] and geo.haversine_mi((a["lat_center"], a["lon_center"]), (b["lat_center"], b["lon_center"])) < 25:
                flagged.add(frozenset((a["project_id"], b["project_id"])))
    expected = {frozenset((o["project_id_a"], o["project_id_b"])) for o in ex["overlaps"]}

    # their projects in our pipeline: same title (spacing-insensitive), then their endpoints vs ours
    matches, near, compared, date_rows = [], 0, 0, []
    for sp in ex["projects"]:
        fam = ("DESC",) if sp["project_id"].startswith("DESC") else ("GPC", "GTC", "MEAG", "DU")
        pool = [p for p in all_projects if p["utility"] in fam]
        best = max(pool, key=lambda p: difflib.SequenceMatcher(None, _canon_title(sp["project_name"]), _canon_title(p["name"])).ratio())
        sim = difflib.SequenceMatcher(None, _canon_title(sp["project_name"]), _canon_title(best["name"])).ratio()
        m = {"sperry_id": sp["project_id"], "our_id": best["id"] if sim >= 0.8 else None, "title_similarity": round(sim, 3), "endpoints": []}
        if m["our_id"]:
            date_rows.append({"sperry_id": sp["project_id"], "their_date": dates[sp["project_id"]]["iso"], "their_raw": sp["in_service_date_raw"],
                              "our_date": best["in_service"], "same": dates[sp["project_id"]]["iso"] == best["in_service"]})
            for side in ("a", "b"):
                if sp[f"lat_{side}"] is None:
                    continue
                key = N.endpoint_key(re.sub(r"\bSub(station)?\b", "", sp[f"name_{side}"], flags=re.I))
                ours = next((e for e in best["endpoints"] if locate.core(e["key"]) == locate.core(key)), None)
                row = {"name": sp[f"name_{side}"], "theirs": [sp[f"lat_{side}"], sp[f"lon_{side}"]], "ours": None, "km": None}
                compared += 1
                if ours and ours.get("lat") is not None:
                    km = geo.haversine_km((sp[f"lat_{side}"], sp[f"lon_{side}"]), (ours["lat"], ours["lon"]))
                    row.update(ours=[ours["lat"], ours["lon"]], km=round(km, 3), confidence=ours["confidence"])
                    near += km <= 1.0
                m["endpoints"].append(row)
        matches.append(m)
    notes = []
    mc = [(p["project_id"], p[f"lat_{s}"], p[f"lon_{s}"]) for p in ex["projects"] for s in "ab" if (p[f"name_{s}"] or "").upper() == "MCINTOSH" and p[f"lat_{s}"]]
    if len({(la, lo) for _, la, lo in mc}) > 1:
        d = geo.haversine_km(mc[0][1:], mc[-1][1:])
        notes.append(f"their sheet places McIntosh at two points {d:.2f} km apart ({mc[0][0]} vs {mc[-1][0]}); same substation")
    serials = [f"{k}: '{v['raw']}' is an Excel serial date = {v['iso']}" for k, v in dates.items() if v["note"] and "Excel" in v["note"]]
    notes += serials
    return {
        "method": ex["method"],
        "reproduced": all(r["ok"] for r in rows) and flagged == expected,
        "rows": rows,
        "pairs_under_25_mi": len(flagged),
        "extra_pairs": sorted("/".join(sorted(x)) for x in flagged - expected),
        "missing_pairs": sorted("/".join(sorted(x)) for x in expected - flagged),
        "projects_matched": sum(1 for m in matches if m["our_id"]),
        "projects": matches,
        "endpoints_within_1km": {"within": near, "compared": compared},
        "dates": date_rows,
        "data_notes": notes,
    }


# =========================================================================== extraction QA


def spot_check_ga(ga: dict, texts: list[str], n: int = 15) -> list[dict]:
    """Deterministic sample of Table 2 rows: every field and every name word must be on the cited page, in order."""
    rows = ga["rows"]
    step = max(1, len(rows) // n)
    out = []
    for r in rows[::step][:n]:
        page = texts[r["page"] - 1]
        first_ok = all(tok in page for tok in (r["zone"], r["teams_no"], r["need_date_raw"], r["sponsor"]))
        pos, order_ok = 0, True
        flat = re.sub(r"\s+", " ", page)
        for w in r["name"].replace("-", " - ").split():
            i = flat.find(w, pos)
            if i < 0:
                order_ok = False
                break
            pos = i + len(w)
        det = r.get("detail") or {}
        out.append({"teams_no": r["teams_no"], "page": r["page"], "name": r["name"], "fields_on_page": first_ok, "name_words_in_order": order_ok,
                    "detail_page": det.get("page"), "detail_title_matches": r["title_matches_detail"], "ok": first_ok and order_ok})
    return out


# =========================================================================== output


PUBLIC_ENDPOINT = ("name", "raw", "lat", "lon", "osm", "confidence", "match", "state")
PUBLIC = [
    "id", "utility", "utility_name", "state", "name", "kv", "kind", "endpoints", "geometry", "center", "in_service",
    "in_service_raw", "build_window", "status", "need", "description", "cost_usd", "cost_by_year", "zone", "teams_no",
    "miles", "tags", "confidence", "provenance", "checks",
    # extras (not in the minimum contract, useful for tracing)
    "kind_basis", "via", "sponsor_raw", "plan_year", "change_ten_year", "change_irp", "name_prefixes", "customer_named",
    "project_id_raw", "notes",
]


def tags_of(p: dict) -> list[str]:
    t = [p["kind"].replace("_", " ")]
    t += [f"{k} kV" for k in p["kv"][:2]]
    states = {e.get("state") for e in p["endpoints"] if e.get("lat") is not None}
    if {"SC", "GA"} <= states:
        t.append("crosses the state line")
    elif p["utility"] == "DESC" and "GA" in states or p["utility"] != "DESC" and "SC" in states:
        t.append("across the state line")
    if re.search(r"\bTIE\b", p["name"], re.I) or re.search(r"\btie lines?\b", p.get("description") or "", re.I):
        t.append("tie line")
    if p.get("sponsor_raw") == "SAV":
        t.append("Savannah area (SAV)")
    if p.get("customer_named"):
        t.append("names a customer")
    return t


def public(p: dict) -> dict:
    p = dict(p)
    p["tags"] = tags_of(p)
    notes = list(p.get("_name_notes") or []) + list(p.get("_kv_notes") or [])
    if p.get("_date_note"):
        notes.append(f"date: {p['_date_note']}")
    if p.get("sponsor_raw") == "SAV":
        notes.append("sponsor 'SAV' counted as Georgia Power, as Sperry's worked example does")
    notes += [a["detail"] for a in p.get("_anomalies", [])]
    p["notes"] = _dedupe(notes)
    p["endpoints"] = [{k: e.get(k) for k in PUBLIC_ENDPOINT} for e in p["endpoints"]]
    return {k: p.get(k) for k in PUBLIC}


def quarantined(p: dict) -> dict:
    q = public(p)
    q["reasons"] = p["_reasons"]
    keep = ["id", "utility", "utility_name", "state", "name", "reasons", "kv", "kind", "endpoints", "in_service", "in_service_raw",
            "build_window", "zone", "teams_no", "miles", "description", "cost_usd", "provenance", "checks", "notes"]
    return {k: q.get(k) for k in keep}


def fingerprint(p: dict) -> dict:
    return {
        "name": p.get("name"), "in_service": p.get("in_service"), "kind": p.get("kind"), "kv": p.get("kv"),
        "cost_usd": p.get("cost_usd"), "confidence": p.get("confidence"), "status": p.get("status"),
        "build_window": (p.get("build_window") or {}).get("start"),
        "endpoints": [[e.get("name"), e.get("lat") and round(e["lat"], 4), e.get("lon") and round(e["lon"], 4)] for e in p.get("endpoints") or []],
    }


def diff_previous(prev: dict | None, projects: list[dict], quarantine: list[dict], sources: list[dict]) -> dict:
    if not prev:
        return {"previous_built_at": None, "first_run": True, "sources_changed": [x["id"] for x in sources], "added": [], "removed": [], "changed": [], "moved_to_quarantine": [], "released_from_quarantine": []}
    old = {p["id"]: p for p in prev.get("projects", [])}
    oldq = {q["id"] for q in prev.get("quarantine", [])}
    new = {p["id"]: p for p in projects}
    old_src = {x["id"]: x.get("sha256") for x in prev.get("sources", [])}
    newq = {q["id"] for q in quarantine}
    changed = []
    for i in sorted(set(old) & set(new)):
        a, b = fingerprint(old[i]), fingerprint(new[i])
        fields = [k for k in a if a[k] != b[k]]
        if fields:
            changed.append({"id": i, "fields": fields})
    return {
        "previous_built_at": prev.get("built_at"), "first_run": False,
        "sources_changed": [x["id"] for x in sources if old_src.get(x["id"]) != x.get("sha256")],
        "added": sorted(set(new) - set(old) - oldq), "removed": sorted(set(old) - set(new) - newq),
        "changed": changed,
        "moved_to_quarantine": sorted(set(old) & newq), "released_from_quarantine": sorted(oldq & set(new)),
    }


def build_basemap() -> dict:
    states = geo.census_states()
    out_states = {}
    for code in ("SC", "GA"):
        rings = [_dp([[round(x, 3), round(y, 3)] for x, y in r], 0.004) for r in states[code]["rings"]]
        rings = sorted((r for r in rings if len(r) >= 4), key=len, reverse=True)
        out_states[code] = [r for i, r in enumerate(rings) if i == 0 or len(r) >= 8]
    lines = []
    for e in osm.overpass("lines_SC_GA")["elements"]:
        t = e.get("tags", {})
        kvs = locate._kv_list(t.get("voltage"))
        if not kvs or max(kvs) < 115 or not e.get("geometry"):
            continue
        pts = _dp([[round(g["lon"], 3), round(g["lat"], 3)] for g in e["geometry"]], 0.002)
        dedup = [pts[0]] + [p for i, p in enumerate(pts[1:], 1) if p != pts[i - 1]]
        if len(dedup) >= 2:
            lines.append({"kv": max(kvs), "operator": t.get("operator"), "coords": dedup})
    xs = [p[0] for rs in out_states.values() for r in rs for p in r]
    ys = [p[1] for rs in out_states.values() for r in rs for p in r]
    return {
        "_source": "States: U.S. Census Bureau cb_2024_us_state_20m (public domain), simplified. Lines: OpenStreetMap power=line "
                   ">= 115 kV in SC and GA, (c) OpenStreetMap contributors, ODbL 1.0, simplified (~200 m). Built by backend/demo/gridlock/build.py.",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "osm_fetched_at": osm.fetched_at("lines_SC_GA"),
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "states": out_states,
        "lines": lines,
    }


# =========================================================================== main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--import-dir", type=Path, default=None)
    ap.add_argument("--refresh-osm", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.perf_counter()
    print("Build plans pipeline")

    # 0 self-test ---------------------------------------------------------------
    def test():
        res = selftest.run()
        bad = [r for r in res if not r["ok"]]
        if bad:
            for r in bad:
                print(f"  SELF-TEST FAILED: {r['case']}: expected {r['expected']!r}, got {r['got']!r}")
            raise SystemExit("self-test failed: nothing was written")
        return res, len(res), "pinned parser and matcher cases from the filings and Sperry's example all pass"

    st0 = Stages()
    tests = st0.run("selftest", "parsers and matchers on pinned cases", test, len(selftest.CASES))
    srcs = ensure_sources(args.import_dir)
    by_id = {s["id"]: s for s in srcs}
    st = Stages()
    st.rows += st0.rows
    use_cache = not args.no_cache

    # 1 extract ---------------------------------------------------------------
    def ex_desc():
        texts = page_texts(by_id["desc"]["path"], use_cache)
        recs = extract_desc.extract(texts)
        clean = sum(1 for r in recs if not r["parse_errors"])
        return (recs, texts), len(recs), f"{clean} pages parsed cleanly, {sum(len(r['anomalies']) for r in recs)} data anomalies recorded"

    desc_recs, desc_texts = st.run("extract_desc", "DESC: one project per page", ex_desc, lambda r: len(r[1]))

    def ex_ga():
        texts = page_texts(by_id["ga_irp"]["path"], use_cache)
        pages = extract_ga.find_table_pages(texts)
        ga = extract_ga.extract(texts, page_words(by_id["ga_irp"]["path"], pages, use_cache))
        note = (f"Table 2 on pp{pages[0]}-{pages[-1]}; the two parsers agree on {ga['parsers_agree']}/{len(ga['rows'])} rows; "
                f"{sum(1 for r in ga['rows'] if r['detail'])} detail pages; {sum(1 for r in ga['rows'] if r['title_matches_detail'])} titles identical")
        return (ga, texts), len(ga["rows"]), note

    ga, ga_texts = st.run("extract_ga", "Georgia: Table 2 rows + detail pages", ex_ga, lambda r: r[0]["rows_by_position"])

    # 2 normalize -------------------------------------------------------------
    def norm():
        ps = normalize_desc(desc_recs, by_id["desc"]) + normalize_ga(ga, by_id["ga_irp"])
        dated = sum(1 for p in ps if p["in_service"])
        return ps, len(ps), f"{dated} dates read, {sum(1 for p in ps if p['endpoints_parsed'])} titles name a place, {sum(1 for p in ps if p['kv'])} with kV"

    projects = st.run("normalize", "dates, kV, kind, endpoint names, build windows", norm, len(desc_recs) + len(ga["rows"]))

    # 3 locate ----------------------------------------------------------------
    def loc():
        index = locate.Index(refresh=args.refresh_osm)
        routes = LineRoutes(refresh=args.refresh_osm)
        geocoder = osm.Nominatim(allow_network=not args.offline)
        info = locate_all(projects, index, geocoder)
        for p in projects:
            geometry_of(p, routes)
        n = sum(1 for p in projects if any(e.get("lat") is not None for e in p["endpoints"]))
        routed = sum(1 for p in projects if p.get("geometry") and p["geometry"]["basis"].startswith("route of"))
        info.update(osm_features=len(index.features), osm_named=len(index.named), routed=routed)
        note = (f"{len(index.named)} named OSM power features; Nominatim {info['nominatim_found']}/{info['nominatim_tried']} leftovers "
                f"({info['nominatim_calls']} network calls); {routed} lines follow their mapped OSM route")
        return info, n, note

    loc_info = st.run("locate", "OSM name match -> Nominatim for leftovers", loc, len(projects))

    # 4 checks ----------------------------------------------------------------
    def chk():
        kept, quar, summary = checks.run(projects, loc_info["zone_centers"])
        return (kept, quar, summary), len(kept), f"{len(quar)} quarantined with reasons, {sum(1 for p in kept if any(c['status'] == 'warn' for c in p['checks']))} kept with warnings"

    kept, quar, rule_summary = st.run("checks", f"{len(checks.RULES)} named rules", chk, len(projects))

    # 5 write -----------------------------------------------------------------
    def write():
        sperry = sperry_report(projects)
        if not sperry["reproduced"]:  # a gate like the self-test: the distance/date code must reproduce the answer key
            bad = [r for r in sperry["rows"] if not r["ok"]]
            raise SystemExit(f"Sperry's worked example is not reproduced ({bad or sperry['extra_pairs'] or sperry['missing_pairs']}); nothing was written")
        prev = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
        pub = [public(p) for p in kept]
        q = [quarantined(p) for p in quar]
        coverage = {}
        for u in ("DESC", "GPC", "GTC", "MEAG", "DU"):
            allu = [p for p in projects if p["utility"] == u]
            ku = [p for p in kept if p["utility"] == u]
            coverage[u] = {
                "extracted": len(allu), "located": len(ku), "quarantined": len(allu) - len(ku),
                "high": sum(1 for p in ku if p["confidence"] == "high"), "medium": sum(1 for p in ku if p["confidence"] == "medium"),
                "low": sum(1 for p in ku if p["confidence"] == "low"),
                "both_endpoints": sum(1 for p in ku if p["geometry"] and p["geometry"]["type"] == "segment"),
            }
        report = {
            "stages": st.rows,
            "checks": rule_summary,
            "extraction": {
                "desc_pages": len(desc_texts), "desc_projects": len(desc_recs),
                "ga_table_pages": ga["pages"], "ga_rows_by_lines": len(ga["rows"]), "ga_rows_by_position": ga["rows_by_position"],
                "ga_parsers_agree": ga["parsers_agree"], "ga_rows_per_page": ga["rows_per_page"],
                "ga_detail_pages": len(ga["details"]), "ga_titles_identical": sum(1 for r in ga["rows"] if r["title_matches_detail"]),
                "ga_title_differences": [{"teams_no": r["teams_no"], "table": r["name"], "detail": r["detail"]["title"], "detail_page": r["detail"]["page"]}
                                         for r in ga["rows"] if r["detail"] and not r["title_matches_detail"]],
                "ga_spot_check": spot_check_ga(ga, ga_texts),
                "notes": ga["notes"],
            },
            "locate": {k: v for k, v in loc_info.items() if k != "zone_centers"} | {"zone_centers": {z: [round(a, 4), round(b, 4)] for z, (a, b) in loc_info["zone_centers"].items()}},
            "sperry_example": sperry,
            "coverage": coverage,
            "changed_since_last_run": None,
            "context_sources": CONTEXT_SOURCES,
        }
        doc = {
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sources": [{"id": s["id"], "utility": s["utility"], "title": s["title"], "file": s["file"], "url": s["url"],
                         "publisher": s["publisher"], "pages": len(desc_texts) if s["id"] == "desc" else len(ga_texts), "sha256": s["sha256"]} for s in srcs],
            "projects": pub,
            "quarantine": q,
            "report": report,
        }
        report["changed_since_last_run"] = diff_previous(prev, pub, q, doc["sources"])
        report["selftest"] = {"passed": sum(r["ok"] for r in tests), "cases": len(tests)}
        DATA.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
        basemap = build_basemap()
        OUT_BASEMAP.write_text(json.dumps(basemap, separators=(",", ":")), encoding="utf-8")
        note = f"projects.json {OUT.stat().st_size / 1024:.0f} KB, basemap.json {OUT_BASEMAP.stat().st_size / 1024:.0f} KB ({len(basemap['lines'])} lines)"
        return doc, len(pub) + len(q), note

    doc = st.run("write", "data/projects.json + data/basemap.json", write, len(kept) + len(quar))
    rep = doc["report"]

    # summary -----------------------------------------------------------------
    print("\n  checks (pass / warn / fail):")
    for c in rule_summary:
        print(f"    {c['id']:22} {c['passed']:>4} {c['warned']:>4} {c['failed']:>4}   {'blocking' if c['blocking'] else ''}")
    print("\n  coverage:")
    for u, c in rep["coverage"].items():
        print(f"    {u:5} extracted {c['extracted']:>4}  located {c['located']:>4} (high {c['high']}, medium {c['medium']}, low {c['low']}; both ends {c['both_endpoints']})  quarantined {c['quarantined']}")
    sp = rep["sperry_example"]
    print(f"\n  Sperry's worked example: {'REPRODUCED' if sp['reproduced'] else 'NOT reproduced'} "
          f"({sum(r['ok'] for r in sp['rows'])}/{len(sp['rows'])} overlaps, {sp['pairs_under_25_mi']} pairs under 25 mi); "
          f"{sp['projects_matched']}/10 of their projects found in our extraction; "
          f"{sp['endpoints_within_1km']['within']}/{sp['endpoints_within_1km']['compared']} of their endpoint coordinates within 1 km of ours")
    ch = rep["changed_since_last_run"]
    if ch["first_run"]:
        print("  changed since last run: first run")
    else:
        print(f"  changed since last run ({ch['previous_built_at']}): sources changed {ch['sources_changed'] or 'none'}; +{len(ch['added'])} added, -{len(ch['removed'])} removed, "
              f"{len(ch['changed'])} changed, {len(ch['moved_to_quarantine'])} newly quarantined, {len(ch['released_from_quarantine'])} released")
        for c in ch["changed"][:10]:
            print(f"    {c['id']}: {', '.join(c['fields'])}")
    print(f"\n  done in {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
