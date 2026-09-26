"""Smoke checks for build plans / GridLock (owned by its track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).

Everything here is read-only: the GridLock endpoints are public GETs over committed data
(backend/demo/gridlock/data/projects.json, or Sperry's worked example until it exists)."""

import csv
import io
import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

GEORGIA = {"GPC", "GTC", "MEAG", "DU"}
# Sperry Tech's Projects_Overlaps.xlsx (their worked example), read from their file: the export must start with exactly these
SPERRY_PROJECT_COLS = [
    "project_id", "utility", "state", "project_name", "name_a", "lat_a", "lon_a", "name_b", "lat_b", "lon_b",
    "lat_center", "lon_center", "in_service_date", "overlap_count", "overlap_1", "overlap_2", "overlap_3",
]
SPERRY_OVERLAP_COLS = [
    "overlap_id", "distance_mi", "time_gap (day)", "utility_a", "project_id_a", "project_name_a",
    "utility_b", "project_id_b", "project_name_b",
]
FAULT_KINDS = {
    "excel_serial", "impossible_date", "malformed_amount", "duplicate_id", "customer_only", "same_name_far",
    "other_state", "two_digit_year", "kv_letter_o", "swapped_latlon",
}
FAULT_FILE = Path(__file__).resolve().parent.parent / "demo" / "gridlock" / "data" / "fault_report.json"
XNS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _base() -> str:
    """The server under test: smoke_test.py keeps it in BASE, scratch/run_one_smoke.py in base."""
    import __main__

    for name in ("BASE", "base"):
        v = getattr(__main__, name, None)
        if isinstance(v, str) and v.startswith("http"):
            return v.rstrip("/")
    return os.getenv("SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")


def _raw(path: str) -> tuple[bytes, dict]:
    """A GET whose body isn't JSON (the downloads): the bytes and the headers."""
    with urllib.request.urlopen(urllib.request.Request(_base() + path), timeout=60) as r:
        assert r.status == 200, f"{path}: {r.status}"
        return r.read(), {k.lower(): v for k, v in r.headers.items()}


def _xlsx_sheets(data: bytes) -> dict:
    """{sheet name: rows (lists of cell values)} read with zipfile + xml (inline or shared strings, numbers)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    bad = z.testzip()
    assert bad is None, f"corrupt zip member {bad}"
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(t.text or "" for t in si.iter(f"{{{XNS['m']}}}t"))
                  for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", XNS)]
    target = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = {}
    for sh in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", XNS):
        part = "xl/" + target[sh.get(f"{{{XNS['r']}}}id")].lstrip("/").removeprefix("xl/")
        rows = []
        for row in ET.fromstring(z.read(part)).find("m:sheetData", XNS):
            cells = {}
            for c in row:
                idx = 0
                for ch in re.match(r"[A-Z]+", c.get("r")).group(0):
                    idx = idx * 26 + ord(ch) - 64
                t, v = c.get("t"), c.find("m:v", XNS)
                if t == "s":
                    cells[idx - 1] = shared[int(v.text)]
                elif t == "inlineStr":
                    cells[idx - 1] = "".join(x.text or "" for x in c.iter(f"{{{XNS['m']}}}t"))
                else:
                    cells[idx - 1] = float(v.text) if v is not None else None
            rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
        out[sh.get("name")] = rows
    return out
TIER_EDGES = {"touching": 0.1, "row": 1.6, "site": 8.0}
TIER_LABELS = {
    "touching": "Must coordinate",
    "row": "Share the land",
    "site": "Share site logistics",
    "crews": "Share crews and equipment",
}


def register(ctx):
    state = {}

    def summary_shape():
        s = ctx.request("GET", "/api/gridlock/summary")
        assert "built_at" in s and isinstance(s["sources"], list) and s["sources"], "no sources"
        counts = s["counts"]
        assert "DESC" in counts, f"no DESC counts: {list(counts)}"
        assert GEORGIA & set(counts), f"no Georgia utility in counts: {list(counts)}"
        for code, c in counts.items():
            for k in ("extracted", "located", "quarantined"):
                assert isinstance(c[k], int) and c[k] >= 0, f"{code}.{k} = {c.get(k)!r}"
            assert c["located"] + c["quarantined"] <= c["extracted"], f"{code}: located + quarantined > extracted ({c})"
        assert isinstance(s["report"], dict) and "stages" in s["report"], "report missing its stages"

    def projects_both_sides():
        p = ctx.request("GET", "/api/gridlock/projects")
        projects = p["projects"]
        utilities = {x["utility"] for x in projects}
        assert "DESC" in utilities, f"no DESC projects: {utilities}"
        assert GEORGIA & utilities, f"no Georgia projects: {utilities}"
        ids = [x["id"] for x in projects]
        assert len(ids) == len(set(ids)), "duplicate project ids"
        for x in projects:
            assert x.get("name") and x.get("provenance"), f"{x['id']}: no name or provenance"
            g = x.get("geometry")
            if g:
                assert g["type"] in ("point", "segment") and g["coords"], f"{x['id']}: bad geometry {g}"
                for lon, lat in g["coords"]:
                    assert 29.5 <= lat <= 36 and -86.5 <= lon <= -78, f"{x['id']}: {lat},{lon} is outside SC/GA"
        for q in p["quarantine"]:
            assert q.get("reasons"), f"quarantined {q.get('id')} has no reasons"
        state["projects"] = {x["id"]: x for x in projects}

    def overlaps_default():
        o = ctx.request("GET", "/api/gridlock/overlaps")
        prm = o["params"]
        assert prm["max_km"] == 40 and prm["method"] == "closest", prm
        rows = o["overlaps"]
        assert rows, "no overlaps at the default parameters"
        assert o["flagged"] >= len(rows) and o["total_pairs"] >= o["flagged"], (o["total_pairs"], o["flagged"], len(rows))
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1)), "ranks are not 1..n"
        scores = [r["score"] for r in rows]
        assert scores == sorted(scores, reverse=True), "not ranked by score"
        projects = state["projects"]
        for r in rows:
            pa, pb = projects[r["a"]], projects[r["b"]]
            assert pa["utility"] != pb["utility"], f"{r['id']} pairs a utility with itself"
            assert pa["utility"] in prm["a"] and pb["utility"] in prm["b"], f"{r['id']} is outside a={prm['a']} b={prm['b']}"
            d = r["distance_km"]
            assert 0 <= d <= prm["max_km"] + 1e-6, f"{r['id']}: {d} km is over the threshold"
            assert abs(r["distance_mi"] * 1.609344 - d) < 0.01, f"{r['id']}: km and miles disagree"
            t = r["tier"]
            assert r["tier_label"] == TIER_LABELS[t], f"{r['id']}: label {r['tier_label']!r} for tier {t}"
            if t == "touching":
                assert d < 0.1 or r.get("crosses"), f"{r['id']}: touching at {d} km"
            elif t == "row":
                assert 0.1 <= d < 1.6, f"{r['id']}: row at {d} km"
            elif t == "site":
                assert 1.6 <= d < 8, f"{r['id']}: site at {d} km"
            else:
                assert d >= 8, f"{r['id']}: crews at {d} km"
            assert len(r["closest_points"]) == 2 and all(len(pt) == 2 for pt in r["closest_points"]), r["closest_points"]
            assert r["reasons"] and 0 < r["score"] <= 110, f"{r['id']}: score {r['score']} / reasons {r['reasons']}"
        state["top"] = rows[0]

    def overlaps_center_method():
        o = ctx.request("GET", "/api/gridlock/overlaps?method=center&max_km=40.2336")
        for r in o["overlaps"]:
            assert r["center_distance_mi"] < 25.005, f"{r['id']}: centers {r['center_distance_mi']} mi apart"
        wide = ctx.request("GET", "/api/gridlock/overlaps?max_km=8&a=DESC&b=GA")
        assert all(r["distance_km"] < 8 for r in wide["overlaps"]), "a pair over 8 km came back"

    def param_validation():
        for q in (
            "max_km=0", "max_km=-5", "max_km=1000", "max_km=nan", "max_km=abc",
            "window_months=-1", "window_months=1000", "window_months=x",
            "method=nearest", "a=XYZ", "a=DESC&b=DESC", "b=", "limit=0", "limit=100000",
        ):
            ctx.request("GET", f"/api/gridlock/overlaps?{q}", expect=422)
        ctx.request("GET", "/api/gridlock/opportunities?limit=0", expect=422)
        ctx.request("GET", "/api/gridlock/opportunities?limit=500", expect=422)

    def opportunities():
        o = ctx.request("GET", "/api/gridlock/opportunities?limit=5")
        opps = o["opportunities"]
        assert 1 <= len(opps) <= 5, len(opps)
        for x in opps:
            assert x["project_a"]["id"] == x["a"] and x["project_b"]["id"] == x["b"], x["id"]
            assert x["share"], f"{x['id']}: no 'what they could share' line"
        assert opps[0]["id"] == state["top"]["id"], "opportunity #1 differs from overlap #1"

    def estimate_top():
        e = ctx.request("GET", f"/api/gridlock/estimate/{state['top']['id']}")
        assert e["items"], "no estimate items"
        for it in e["items"]:
            assert it["low"] <= it["high"], f"{it['label']}: low {it['low']} > high {it['high']}"
            assert it["basis"] and it["source"] and it["unit"], it
        assert 0 <= e["total_low"] <= e["total_high"], (e["total_low"], e["total_high"])
        assert e["total_high"] > 0, "estimate totals nothing"
        assert e["sources"] and all(s["url"].startswith("https://") for s in e["sources"]), e["sources"]
        assert e["assumptions"], "no assumptions"

    def estimate_unknown():
        ctx.request("GET", "/api/gridlock/estimate/NOPE~NADA", expect=404)
        ctx.request("GET", "/api/gridlock/estimate/not-an-overlap", expect=404)
        same = [p for p in state["projects"].values() if p["utility"] == "DESC" and p.get("geometry")][:2]
        if len(same) == 2:
            ctx.request("GET", f"/api/gridlock/estimate/{same[0]['id']}~{same[1]['id']}", expect=404)

    def sperry_reproduced():
        s = ctx.request("GET", "/api/gridlock/sperry-check")
        assert len(s["rows"]) == 6, f"{len(s['rows'])} rows, Sperry's example has 6 overlaps"
        bad = [r for r in s["rows"] if not r["ok"]]
        assert not bad, f"not reproduced: {bad}"
        assert s["all_ok"] and not s["extra"] and not s["missing"], (s["extra"], s["missing"])
        # their six pairs are also found in the comparison of the full filings, and tagged there
        f = s["found_in_filings"]
        assert f["flagged"] == f["of"] == 6, f"only {f['flagged']} of their pairs flagged in the full comparison"
        top = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")
        tagged = {r["sperry"] for r in top["overlaps"] if r.get("sperry")}
        assert tagged == {r["overlap_id"] for r in s["rows"]}, f"tagged overlaps: {sorted(tagged)}"

    def window_setting_matters():
        # projects without a filed start date take their build window from window_months, so it must
        # change something (it once changed nothing: every window was treated as filed)
        q = "a=DESC&b=GA&max_km=50&limit=2000"
        short = ctx.request("GET", f"/api/gridlock/overlaps?{q}&window_months=6")
        long = ctx.request("GET", f"/api/gridlock/overlaps?{q}&window_months=60")
        assert short["window_assumed"]["projects"] > 0, short["window_assumed"]
        key = lambda r: (r["id"], r["windows_overlap_months"], r["score"])  # noqa: E731
        assert {key(r) for r in short["overlaps"]} != {key(r) for r in long["overlaps"]}, "window_months changes nothing"

    def basemap():
        b = ctx.request("GET", "/api/gridlock/basemap")
        assert isinstance(b["states"], dict) and isinstance(b["lines"], list), "basemap shape"
        x0, y0, x1, y1 = b["bbox"]
        assert x0 < x1 and y0 < y1, b["bbox"]

    def fault_report():
        r = ctx.request("GET", "/api/gridlock/fault-test")
        kinds = {k["id"]: k for k in r["kinds"]}
        assert set(kinds) == FAULT_KINDS, f"fault kinds: missing {FAULT_KINDS - set(kinds)}, extra {set(kinds) - FAULT_KINDS}"
        tot = {"injected": 0, "caught": 0, "missed": 0}
        fmt = {"injected": 0, "read_correctly": 0}
        for kid, k in kinds.items():
            assert k["injected"] > 0, f"{kid}: nothing injected"
            oc = k["outcomes"]
            assert sum(oc.values()) == k["injected"], f"{kid}: outcomes {oc} don't add up to {k['injected']}"
            assert k["origin"] in ("filings", "common", "format"), f"{kid}: origin {k.get('origin')}"
            assert (k["met_in"] is None) == (k["origin"] == "common"), f"{kid}: a 'met in' source only for faults met in the filings"
            assert all(not e["caught_by"] for e in k["examples"] if e["outcome"] == "noted"), f"{kid}: a correct read shown as a catch"
            if k["origin"] == "format":  # the two-digit year: a real format, read correctly, never counted as bad data caught
                assert k["caught"] is None and k["caught_by"] == [], f"{kid}: a format test counted as catches"
                assert k["read_correctly"] == oc["noted"] == k["injected"] - k["not_read_correctly"], f"{kid}: {k}"
                for key in fmt:
                    fmt[key] += k[key]
                continue
            assert oc["noted"] == 0, f"{kid}: 'read correctly' outcomes in a bad-data kind"
            assert k["caught"] == k["injected"] - oc["missed"] == oc["set_aside"] + oc["flagged"], f"{kid}: {k}"
            assert k["missed"] == oc["missed"] == len(k["misses"]), f"{kid}: {k['missed']} missed but {len(k['misses'])} listed"
            assert abs(k["catch_rate"] - k["caught"] / k["injected"]) < 1e-4, f"{kid}: catch rate {k['catch_rate']}"
            assert sum(c["first"] for c in k["caught_by"]) == k["caught"], f"{kid}: 'caught by' counts don't add up"
            assert all(m.get("why_missed") for m in k["misses"]), f"{kid}: a miss without its reason"
            for key in tot:
                tot[key] += k[key]
        s = r["summary"]
        assert {k: s[k] for k in tot} == tot, f"summary {s} != sum of the bad-data kinds {tot}"
        assert s["set_aside"] + s["flagged"] == s["caught"], f"summary: caught {s['caught']} != set aside + flagged"
        assert f"{s['caught']:,} of {s['injected']:,}" in r["headline"], r["headline"]
        ft = r["format_test"]
        assert {k: ft[k] for k in fmt} == fmt and f"{ft['read_correctly']:,} of {ft['injected']:,}" in ft["line"], ft
        c = r["control"]
        assert c["kept"] == c["records"] > 0, f"control: {c['kept']} of {c['records']} clean records kept"
        disk = json.loads(FAULT_FILE.read_text(encoding="utf-8"))
        assert disk["summary"] == s and disk["generated_at"] == r["generated_at"], "the served report isn't the committed file"

    def export_xlsx():
        body, hdr = _raw("/api/gridlock/export.xlsx")
        assert "spreadsheetml" in hdr.get("content-type", ""), hdr.get("content-type")
        assert hdr.get("content-disposition", "").startswith("attachment;") and ".xlsx" in hdr["content-disposition"], hdr
        sheets = _xlsx_sheets(body)
        assert list(sheets)[:2] == ["projects", "overlaps"], f"sheets {list(sheets)}"
        p, o = sheets["projects"], sheets["overlaps"]
        assert p[0][: len(SPERRY_PROJECT_COLS)] == SPERRY_PROJECT_COLS, f"projects header: {p[0][:17]}"
        assert o[0][: len(SPERRY_OVERLAP_COLS)] == SPERRY_OVERLAP_COLS, f"overlaps header: {o[0][:9]}"
        assert len(p) - 1 == len(state["projects"]), f"{len(p) - 1} project rows, {len(state['projects'])} projects"
        flagged = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")["flagged"]
        assert len(o) - 1 == flagged, f"{len(o) - 1} overlap rows, {flagged} flagged at the defaults"
        h = p[0]
        for row in p[1:]:
            assert row[h.index("project_id")] in state["projects"], row[0]
            lat = row[h.index("lat_center")]
            assert isinstance(lat, float) and 29.5 <= lat <= 36, f"{row[0]}: lat_center {lat!r}"
            d = row[h.index("in_service_date")]
            assert d is None or isinstance(d, float), f"{row[0]}: in_service_date is not a date cell ({d!r})"
        ho = o[0]
        for row in o[1:]:
            assert isinstance(row[ho.index("distance_mi")], float) and row[ho.index("distance_mi")] <= 40 / 1.609344 + 0.01, row[:3]
            # shared_window describes a SHARED build period only: a pair whose windows never overlap has none
            shared, months = row[ho.index("shared_window")], row[ho.index("windows_overlap_months")]
            if months is None:
                assert shared is None, f"{row[0]}: shared_window {shared!r} with no timeline"
            else:
                assert (shared in ("still ahead", "open now", "ended (as filed)")) == (months > 0), f"{row[0]}: {shared!r}, {months} months"
                assert months > 0 or shared == "no shared window", f"{row[0]}: {shared!r} for windows that don't overlap"
            sp = row[ho.index("in_sperry_example")]
            assert sp is None or re.fullmatch(r"Sperry OVL_\d+", sp), f"{row[0]}: in_sperry_example {sp!r} (their id, prefixed)"
        about = {r[0]: r[1] for r in sheets.get("about", []) if len(r) > 1}
        assert "not an official utility record" in about.get("Disclaimer", ""), "no disclaimer in the about sheet"

    def export_csv_geojson():
        body, hdr = _raw("/api/gridlock/export.csv?table=overlaps")
        assert hdr.get("content-type", "").startswith("text/csv") and "attachment;" in hdr.get("content-disposition", ""), hdr
        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        assert rows[0][: len(SPERRY_OVERLAP_COLS)] == SPERRY_OVERLAP_COLS, rows[0][:9]
        assert {len(r) for r in rows} == {len(rows[0])}, "ragged CSV rows"
        body, _ = _raw("/api/gridlock/export.csv?table=projects")
        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        assert rows[0][: len(SPERRY_PROJECT_COLS)] == SPERRY_PROJECT_COLS and len(rows) - 1 == len(state["projects"]), len(rows)
        ctx.request("GET", "/api/gridlock/export.csv?table=nope", expect=422)
        ctx.request("GET", "/api/gridlock/export.xlsx?max_km=0", expect=422)
        body, hdr = _raw("/api/gridlock/export.geojson")
        assert "geo+json" in hdr.get("content-type", ""), hdr.get("content-type")
        gj = json.loads(body)
        assert gj["type"] == "FeatureCollection" and gj.get("disclaimer"), "not a FeatureCollection with a disclaimer"
        assert len(gj["features"]) == len(state["projects"]), f"{len(gj['features'])} features"
        for f in gj["features"]:
            g = f["geometry"]
            pts = [g["coordinates"]] if g["type"] == "Point" else g["coordinates"]
            assert g["type"] in ("Point", "LineString") and pts, f["id"]
            assert all(-86.5 <= lon <= -78 and 29.5 <= lat <= 36 for lon, lat in pts), f"{f['id']}: outside SC/GA"
            assert f["properties"]["project_id"] == f["id"], f["id"]

    ctx.check("gridlock: summary has sources, DESC + Georgia counts, and the pipeline report", summary_shape)
    ctx.check("gridlock: projects cover DESC and a Georgia utility, placed inside SC/GA, quarantine has reasons", projects_both_sides)
    ctx.check("gridlock: default overlaps are ranked cross-utility pairs with tiers matching their distances", overlaps_default)
    ctx.check("gridlock: center method stays under 25 mi; a tighter threshold stays tighter", overlaps_center_method)
    ctx.check("gridlock: bad max_km / window / method / utilities / limits are 422s", param_validation)
    ctx.check("gridlock: opportunities inline both projects and say what they could share", opportunities)
    ctx.check("gridlock: estimate for the top overlap has low <= high items, sources and assumptions", estimate_top)
    ctx.check("gridlock: unknown or same-utility estimate ids are 404s", estimate_unknown)
    ctx.check("gridlock: Sperry's worked example is reproduced (6 of 6, no extra pairs) and found in the full filings", sperry_reproduced)
    ctx.check("gridlock: the build-window setting changes pairs whose filing gives no start date", window_setting_matters)
    ctx.check("gridlock: basemap has states, lines and a bbox", basemap)
    ctx.check("gridlock: the fault test covers nine bad-data kinds + the two-digit-year format test (never counted as catches); "
              "every catch rate adds up (served = committed file)", fault_report)
    ctx.check("gridlock: export.xlsx opens as a workbook with Sperry's exact columns, every project and every flagged overlap; "
              "shared_window only for pairs that share a window", export_xlsx)
    ctx.check("gridlock: export.csv keeps Sperry's columns; export.geojson has every project inside SC/GA; bad params are 422s",
              export_csv_geojson)
