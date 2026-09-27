"""Smoke checks for build plans / GridLock (owned by its track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).

Everything here is read-only: the GridLock endpoints are public GETs over committed data
(backend/demo/gridlock/data/projects.json, or Sperry's worked example until it exists)."""

import csv
import io
import json
import math
import os
import re
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

GEORGIA = {"GPC", "GTC", "MEAG", "DU"}
SPERRY_KM = 25 * 1.609344  # their cutoff, 25 mi exactly
# the list's groups, in order (backend/gridlock.py GROUPS): what can still be built together first
GROUP_ORDER = {"together": 0, "apart": 1, "unknown": 2, "passed": 3}
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


def _ics_events(body: bytes) -> tuple[dict, list[dict]]:
    """A strict reading of an iCalendar file (RFC 5545): UTF-8, CRLF line ends, no content line over 75 octets, folded lines
    unfolded, every BEGIN closed by its END in order. Returns (the VCALENDAR's properties, its VEVENTs), each as
    {NAME: [(params, value)]} with TEXT values unescaped."""
    text = body.decode("utf-8")
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", ""), "not CRLF line ends throughout"
    raw = text.split("\r\n")[:-1]
    for ln in raw:
        assert len(ln.encode("utf-8")) <= 75, f"content line over 75 octets: {ln[:40]!r}"
    lines = []
    for ln in raw:
        if ln.startswith((" ", "\t")):
            assert lines, "a folded line with nothing before it"
            lines[-1] += ln[1:]
        else:
            lines.append(ln)

    def unescape(v):
        return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)

    stack, cal, events, cur = [], None, [], None
    for ln in lines:
        name_params, sep, value = ln.partition(":")
        assert sep, f"no ':' in {ln[:40]!r}"
        name, *params = name_params.split(";")
        name = name.upper()
        if name == "BEGIN":
            stack.append(value)
            cur = {}
            if value == "VCALENDAR":
                assert cal is None and len(stack) == 1, "VCALENDAR not the outermost component"
                cal = cur
            elif value == "VEVENT":
                assert stack == ["VCALENDAR", "VEVENT"], f"VEVENT inside {stack}"
                events.append(cur)
            continue
        if name == "END":
            assert stack and stack[-1] == value, f"END:{value} closes {stack[-1] if stack else 'nothing'}"
            stack.pop()
            cur = cal if stack == ["VCALENDAR"] else None
            continue
        assert cur is not None, f"{name} outside any component"
        cur.setdefault(name, []).append((params, unescape(value)))
    assert not stack and cal is not None, f"unclosed {stack}"
    return cal, events


def _ics_date(prop) -> str:
    """The date of a DTSTART / DTEND;VALUE=DATE property as ISO text."""
    (params, v), = prop
    assert "VALUE=DATE" in params and re.fullmatch(r"\d{8}", v), (params, v)
    return f"{v[:4]}-{v[4:6]}-{v[6:]}"


def _day_after(iso: str) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


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
        assert abs(prm["max_km"] - SPERRY_KM) < 1e-9 and prm["method"] == "closest", prm
        assert o["limit_text"] == "25 mi (40.2 km)", o["limit_text"]
        rows = o["overlaps"]
        assert rows, "no overlaps at the default parameters"
        assert o["flagged"] >= len(rows) and o["total_pairs"] >= o["flagged"], (o["total_pairs"], o["flagged"], len(rows))
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1)), "ranks are not 1..n"
        # near-duplicate pairs (other projects meeting at the same place) fold under an earlier pair of their group: both of
        # their closest points within similar_km of the lead's, the lead ranked first, and no rank changed by it
        assert o["similar_km"] > 0 and o["similar_rule"], (o.get("similar_km"), o.get("similar_rule"))
        by_id = {r["id"]: r for r in rows}
        for r in rows:
            assert "similar_to" in r and isinstance(r["similar"], list), r["id"]
            if r["similar_to"]:
                lead = by_id[r["similar_to"]]
                assert lead["rank"] < r["rank"] and lead["group"] == r["group"] and lead["similar_to"] is None, (r["id"], lead["id"])
                assert r["id"] in lead["similar"], (r["id"], lead["similar"])
                for k in (0, 1):
                    la, lo = r["closest_points"][k], lead["closest_points"][k]
                    a1, b1, a2, b2 = map(math.radians, (la[0], la[1], lo[0], lo[1]))
                    h = math.sin((a2 - a1) / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin((b2 - b1) / 2) ** 2
                    assert 2 * 6371.0088 * math.asin(math.sqrt(h)) <= o["similar_km"] + 0.05, (r["id"], lead["id"], k)
            else:
                assert all(by_id[i]["similar_to"] == r["id"] for i in r["similar"]), r["id"]
        assert sum(len(r["similar"]) for r in rows) == sum(1 for r in rows if r["similar_to"]), "folded pairs don't add up"
        # the group first (building in the same months, at different times, unknown, passed), then same station, then score
        order = [(GROUP_ORDER[r["group"]], 0 if r.get("shared_station") else 1, -r["score"]) for r in rows]
        assert order == sorted(order), "not ranked by group, then same station, then score"
        assert [x["id"] for x in o["groups"]] == list(GROUP_ORDER), o["groups"]
        assert sum(x["count"] for x in o["groups"]) == len(rows) and all(x["label"] and x["what"] for x in o["groups"]), o["groups"]
        for r in rows:  # a group is the pair's timeline, said once
            if r["group"] == "together":
                assert r["same_window"] and r["ahead"] in ("future", "open"), (r["id"], r["same_window"], r["ahead"])
            elif r["same_window"]:
                assert r["group"] == "passed" and r["ahead"] == "past", (r["id"], r["group"], r["ahead"])
            assert r["group_label"] and any(r["group_label"] in x for x in r["reasons"]), (r["id"], r["group_label"])
        # forward-looking: a pair whose shared build window is still ahead or open now leads
        if any(r["group"] == "together" for r in rows):
            assert rows[0]["group"] == "together", rows[0]["id"]
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
            want = ("same_station", "Same station") if r.get("shared_station") else (t, r["tier_label"])
            assert (r["class"], r["class_label"]) == want, f"{r['id']}: class {r['class']!r} / {r['class_label']!r}"
        state["top"] = rows[0]

    def same_station():
        # DESC's Hooks - Thurmond 115 kV tie rebuild and Georgia Power's Evans Primary - Thurmond Dam 115 kV rebuilds end at
        # the same substation (one OpenStreetMap feature in both filings): flagged as the same station, ranked first
        o = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")
        rows, projects = o["overlaps"], state["projects"]
        ss = [r for r in rows if r.get("shared_station")]
        assert ss, "no same-station pair at the defaults (DESC's Thurmond tie x Georgia Power's Evans Primary - Thurmond Dam)"
        assert o["same_station"]["count"] == len(ss) and o["same_station"]["label"] == "Same station", o["same_station"]
        for grp in GROUP_ORDER:  # within each group, same-station pairs before every distance-tier pair
            mine = [r["rank"] for r in ss if r["group"] == grp]
            others = [r["rank"] for r in rows if not r.get("shared_station") and r["group"] == grp]
            assert not mine or not others or max(mine) < min(others), f"{grp}: a same-station pair ranks below a distance-tier pair"
        thurmond = [r for r in ss if "THURMOND" in r["shared_station"]["name"].upper()]
        assert thurmond and any(projects[r["a"]]["utility"] == "DESC" and projects[r["b"]]["utility"] == "GPC" for r in thurmond), (
            f"Thurmond Dam isn't a DESC x Georgia Power same-station pair: {[r['id'] for r in ss]}"
        )
        for r in ss:
            s = r["shared_station"]
            pa, pb = projects[r["a"]], projects[r["b"]]
            ea, eb = pa["endpoints"][s["a_end"]["index"]], pb["endpoints"][s["b_end"]["index"]]
            if s["rule"] == "osm_feature":  # the same asset in both filings: one OpenStreetMap feature
                assert s["osm_url"] and s["osm_url"].startswith("https://www.openstreetmap.org/"), s
                assert (ea.get("osm") or {}).get("url") == (eb.get("osm") or {}).get("url") == s["osm_url"], (r["id"], ea.get("osm"), eb.get("osm"))
            else:
                assert s["rule"] == "name_within_1km" and not ((ea.get("osm") or {}).get("url") and (eb.get("osm") or {}).get("url")), s
            # the reason states both in-service years as filed, and sits in the pair's reasons
            reason = s["reason"]
            assert reason.startswith("As filed, both projects work at ") and s["name"] in reason, reason
            for p in (pa, pb):
                if p.get("in_service"):
                    assert p["in_service"][:4] in reason, f"{r['id']}: {p['id']}'s in-service year {p['in_service'][:4]} missing: {reason}"
            assert any(reason in x for x in r["reasons"]), r["reasons"]
            # a stand-in (no OSM substation carries the filed name) is said as one, so the link isn't read as that station
            proxied = [e for e in (ea, eb) if str(e.get("match") or "").startswith("no OSM substation is named")]
            assert s["stand_in"] is (s["rule"] == "osm_feature" and bool(proxied)), (r["id"], s["stand_in"], len(proxied))
            assert not s["stand_in"] or ("stands in" in s["how"] and ("both sides" in s["how"]) == (len(proxied) == 2)), s["how"]
        t = thurmond[0]["shared_station"]
        assert "2024" in t["reason"] and "2033" in t["reason"] and t["months_apart"] and t["months_apart"] > 96, t
        # Georgia Power x GTC: Adamsville and Echeconnee are placed by proxy on both sides (no OSM substation carries
        # either name), so they are stand-ins and say so
        g = ctx.request("GET", "/api/gridlock/overlaps?a=GPC&b=GTC&limit=2000")["overlaps"]
        stand = [r for r in g if (r.get("shared_station") or {}).get("stand_in")]
        assert stand, "no stand-in same-station pair for Georgia Power x GTC (Adamsville, Echeconnee)"
        for r in stand:
            s = r["shared_station"]
            n = sum(str(projects[pid]["endpoints"][s[f"{side}_end"]["index"]].get("match") or "").startswith("no OSM substation is named")
                    for pid, side in ((r["a"], "a"), (r["b"], "b")))
            assert n and "stands in" in s["how"] and ("both sides" in s["how"]) == (n == 2), (r["id"], n, s["how"])
        # the other way round: every flagged pair whose endpoints name one OSM feature directly is flagged
        for r in rows:
            pa, pb = projects[r["a"]], projects[r["b"]]

            def direct(p):
                return {(e.get("osm") or {}).get("url") for e in p.get("endpoints") or []
                        if e and e.get("lat") is not None and (e.get("osm") or {}).get("url")
                        and not str(e.get("match") or "").startswith("no OSM substation is named")}

            if direct(pa) & direct(pb):
                # flagged same station, or (J1) said why not: a description puts the work somewhere else
                assert r.get("shared_station") or r.get("station_not_shared"), f"{r['id']} shares an OSM substation, neither flagged nor explained"
                if not r.get("shared_station"):
                    assert any(r["station_not_shared"] == x for x in r["reasons"]), (r["id"], r["station_not_shared"])
        # J1 (Sperry judge): DESC's p41 works at a new Deerfield Switching Station on the Okatie - McIntosh tie and Georgia's
        # p314 rebuilds only the Goshen - Georgia Pacific (Rincon) section, so the two must never be called "same station"
        mc = next((r for r in rows if {r["a"], r["b"]} == {"DESC-6888", "GA-20065"}), None)
        if mc is not None:
            assert not mc.get("shared_station") and "not listed as the same station" in (mc.get("station_not_shared") or ""), mc.get("station_not_shared")
            assert any("works on only part of this line" in x for x in mc["reasons"]), "GA-20065's section (Goshen - Georgia Pacific) is not said"
        for r in ss:  # a same-station pair's two descriptions both name the station
            for pid in (r["a"], r["b"]):
                d = (projects[pid].get("description") or projects[pid].get("name") or "").upper()
                assert r["shared_station"]["name"].split()[0].upper() in d, f"{r['id']}: {pid}'s description doesn't name {r['shared_station']['name']}"

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
        # the page states "DESC projects no longer listed" (and the pairs they're in) apart from "time passed": they differ
        # (a pair can have passed on the Georgia side while its DESC project is still in the current list)
        n = f["pairs_with_earlier_only_desc"]
        assert 0 <= n <= 6 and 0 <= f["passed"] <= 6, f
        assert (f["desc_only_in_earlier_list"] > 0) == (n > 0), f

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
        # the test runs every check the pipeline page lists (it once said "the same 16" beside a page that listed 20)
        n_checks = ctx.request("GET", "/api/gridlock/summary")["funnel"]["checks"]
        assert len(r["checks"]) == n_checks and f"the same {n_checks} checks" in " ".join(r["method"]), (len(r["checks"]), n_checks, r["method"][-1])

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
        ov = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")
        flagged = ov["flagged"]
        assert len(o) - 1 == flagged, f"{len(o) - 1} overlap rows, {flagged} flagged at the defaults"
        filled = [r for r in o[1:] if o[0].index("same_station") < len(r) and r[o[0].index("same_station")]]
        assert len(filled) == ov["same_station"]["count"], f"{len(filled)} same_station cells, {ov['same_station']['count']} same-station pairs"
        h = p[0]
        for row in p[1:]:
            assert row[h.index("project_id")] in state["projects"], row[0]
            lat = row[h.index("lat_center")]
            assert isinstance(lat, float) and 29.5 <= lat <= 36, f"{row[0]}: lat_center {lat!r}"
            d = row[h.index("in_service_date")]
            assert d is None or isinstance(d, float), f"{row[0]}: in_service_date is not a date cell ({d!r})"
        ho = o[0]
        for row in o[1:]:
            assert isinstance(row[ho.index("distance_mi")], float) and row[ho.index("distance_mi")] <= 25 + 0.005, row[:3]
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

    def funnel_adds_up():
        # the page's one-line pipeline: every number counted from the data, and they add up
        s = ctx.request("GET", "/api/gridlock/summary")
        f = s["funnel"]
        p = ctx.request("GET", "/api/gridlock/projects")
        projects, quarantine = p["projects"], p["quarantine"]
        assert f["passed"] == len(projects), f"funnel passed {f['passed']} != {len(projects)} projects served"
        assert f["set_aside"] == len(quarantine), f"funnel set aside {f['set_aside']} != {len(quarantine)} quarantined"
        assert f["extracted"] == f["passed"] + f["set_aside"], f"rows {f['extracted']} != passed {f['passed']} + set aside {f['set_aside']}"
        assert sum(x["rows"] for x in f["by_source"]) == f["extracted"], f"rows by filing {f['by_source']} don't add up to {f['extracted']}"
        assert f["filings"] == len(s["sources"]) == len(f["by_source"]), (f["filings"], len(s["sources"]))
        assert f["pages"] == (sum(x.get("pages") or 0 for x in s["sources"]) or None), (f["pages"], s["sources"])
        if f["extracted_by_stages"] is not None:  # the extractors' own count agrees with the records kept
            assert f["extracted_by_stages"] == f["extracted"], (f["extracted_by_stages"], f["extracted"])
        by_counts = sum(c["extracted"] for c in s["counts"].values())
        assert by_counts == f["extracted"], f"per-utility counts give {by_counts} rows, the funnel {f['extracted']}"
        # set aside, by the check each record failed: every record is in at least one group, each group counted right
        groups = f["set_aside_by_check"]
        assert (not quarantine) or groups, "records set aside but no reasons grouped"
        members = set()
        for grp in groups:  # by record (two records can share an id: that is what the id checks catch)
            hit = {k for k, q in enumerate(quarantine) if any(str(r).startswith(f"{grp['check']}:") for r in q["reasons"])}
            assert len(hit) == grp["records"], f"{grp['id']}: {grp['records']} records, {len(hit)} reasons start with {grp['check']!r}"
            assert grp["label"], grp
            members |= hit
        assert len(members) == f["set_aside"], f"{f['set_aside'] - len(members)} set-aside records in no group"
        assert sum(grp["records"] for grp in groups) >= f["set_aside"], groups
        assert f["placed"] == s["compared_projects"] <= f["passed"], (f["placed"], s["compared_projects"], f["passed"])
        assert 0 < f["blocking_checks"] <= f["checks"], (f["blocking_checks"], f["checks"])
        # the pair nodes (from /overlaps at the page's defaults): compared = one side x the other; flagged <= compared
        o = ctx.request("GET", "/api/gridlock/overlaps")
        c = o["compared"]
        placed = [x for x in projects if x.get("geometry")]
        n_a = sum(1 for x in placed if x["utility"] in o["params"]["a"])
        n_b = sum(1 for x in placed if x["utility"] in o["params"]["b"])
        assert (c["a_projects"], c["b_projects"]) == (n_a, n_b), (c, n_a, n_b)
        assert c["disjoint"] and o["total_pairs"] == n_a * n_b, f"{o['total_pairs']} pairs compared != {n_a} x {n_b}"
        # from "passed" to "pairs": the two sides plus the utilities switched off plus any project not placed = passed
        off = {x["utility"]: x["projects"] for x in c["not_compared"]}
        in_play = set(o["params"]["a"]) | set(o["params"]["b"])
        assert all(u not in in_play and n > 0 for u, n in off.items()), (off, in_play)
        assert off == {u: sum(1 for x in placed if x["utility"] == u) for u in {x["utility"] for x in placed} - in_play}, off
        assert c["unplaced"] == len(projects) - len(placed), (c["unplaced"], len(projects), len(placed))
        assert n_a + n_b + sum(off.values()) + c["unplaced"] == f["passed"], (n_a, n_b, off, c["unplaced"], f["passed"])
        assert 0 < o["flagged"] <= o["total_pairs"], (o["flagged"], o["total_pairs"])
        assert o["overlaps"][0]["rank"] == 1, o["overlaps"][0]["rank"]

    def trace_route():
        o = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")
        rows = o["overlaps"]
        projects = state["projects"]
        sha = {x["id"]: x.get("sha256") for x in ctx.request("GET", "/api/gridlock/summary")["sources"]}
        # the top pair in full, then a sweep of the next ones: the trace is the ranking, taken apart
        for r in rows[:25]:
            t = ctx.request("GET", f"/api/gridlock/trace/{r['id']}")
            assert t["overlap_id"] == r["id"] and t["rank"] == r["rank"] and t["flagged"] == o["flagged"], (r["id"], t["rank"], r["rank"])
            sc = t["score"]
            assert sc["value"] == r["score"] and sc["reproduced"] is True, (r["id"], sc["value"], r["score"])
            assert [x["id"] for x in sc["terms"]] == ["distance", "timeline", "location", "same_kv"], sc["terms"]
            prod = 100.0
            for x in sc["terms"]:
                assert x["how"] and x["rule"] and isinstance(x["value"], (int, float)), x
                prod *= x["value"]
            # to the raw score's 4th decimal (the page shows the terms to as many decimals as that needs)
            assert abs(prod - sc["raw"]) < 5e-4 and round(sc["raw"], 1) == sc["value"], f"{r['id']}: terms give {prod}, raw {sc['raw']}, score {sc['value']}"
            parts = r["score_parts"]
            assert abs(sc["terms"][0]["value"] - parts["distance"]) < 1e-3 and sc["terms"][1]["value"] == parts["timeline"], (sc["terms"], parts)
            assert sc["terms"][2]["value"] == parts["location"] and sc["terms"][3]["value"] == parts["same_kv"], (sc["terms"], parts)
            d = t["distance"]
            assert d["km"] == r["distance_km"] and d["closest_points"] == r["closest_points"] and d["tier"] == r["tier"], (r["id"], d)
            assert d["center_mi"] == r["center_distance_mi"], (d["center_mi"], r["center_distance_mi"])
            assert bool(t["shared_station"]) == bool(r["shared_station"]) and t["class"] == r["class"], r["id"]
            assert [p["id"] for p in t["projects"]] == [r["a"], r["b"]], [p["id"] for p in t["projects"]]
            for p in t["projects"]:
                src = p["source"]
                served = projects[p["id"]]
                assert src["title"] and isinstance(p["page"], int) and p["page"] > 0, (p["id"], src, p["page"])
                assert src["sha256"] == sha.get(src["id"]), f"{p['id']}: sha256 {src['sha256']!r} isn't the one the pipeline recorded"
                assert src["sha256"] is None or re.fullmatch(r"[0-9a-f]{64}", src["sha256"]), src["sha256"]
                assert p["raw_text"] and p["raw_text"] == (served.get("provenance") or {}).get("text"), f"{p['id']}: raw text isn't the committed row"
                fields = {x["field"]: x["value"] for x in p["fields"]}
                assert fields.get("name") == served["name"], (fields.get("name"), served["name"])
                # money reads one way (5,000 beside 2,150,000); a year stays 2025
                for key in ("cost_usd", "cost_by_year"):
                    assert not re.search(r"(?<![\d,])\d{4,}", re.sub(r"\b20\d\d:", "", fields.get(key) or "")), (key, fields.get(key))
                if served.get("plan_year"):
                    assert fields.get("plan_year") == str(served["plan_year"]), fields.get("plan_year")
                assert fields.get("in_service_raw") == served.get("in_service_raw"), (fields.get("in_service_raw"), served.get("in_service_raw"))
                assert p["checks"] and p["checks_summary"]["failed"] == 0, f"{p['id']}: a project that failed a check was compared"
                located = [e for e in p["endpoints"] if e["located"]]
                assert located, f"{p['id']}: no located endpoint"
                for e in p["endpoints"]:
                    assert e["rule"]["id"] and e["rule"]["label"], e
                    assert (e["rule"]["id"] == "not_located") == (not e["located"]), (p["id"], e["name"], e["rule"])
                    if e["osm"]:
                        assert e["osm"]["url"].startswith("https://www.openstreetmap.org/"), e["osm"]
                    if e["match"]:  # the reason as recorded, split at its semicolons: nothing reworded
                        assert e["clauses"] and all(c in e["match"] for c in e["clauses"]), (e["clauses"], e["match"])
            assert t["note"] and "not in this repository" in t["note"], t["note"]
        # outside the list's settings a pair is still traced, with no rank
        far = ctx.request("GET", f"/api/gridlock/trace/{rows[-1]['id']}?max_km=1")
        assert far["rank"] is None or far["rank"] >= 1, far["rank"]
        # unknown ids, a same-utility pair and bad settings are refused
        ctx.request("GET", "/api/gridlock/trace/NOPE~NADA", expect=404)
        ctx.request("GET", "/api/gridlock/trace/not-an-overlap", expect=404)
        same = [x for x in projects.values() if x["utility"] == "DESC" and x.get("geometry")][:2]
        if len(same) == 2:
            ctx.request("GET", f"/api/gridlock/trace/{same[0]['id']}~{same[1]['id']}", expect=404)
        ctx.request("GET", f"/api/gridlock/trace/{rows[0]['id']}?max_km=0", expect=422)
        ctx.request("GET", f"/api/gridlock/trace/{rows[0]['id']}?method=nearest", expect=422)

    def sperry_start():
        # the page can start from Sperry's example alone: their ten projects matched to ours, their six pairs drawable
        s = ctx.request("GET", "/api/gridlock/sperry-check")
        assert s["tolerance"] == {"mi": 0.01, "days": 0}, s["tolerance"]
        assert len(s["projects"]) == s["projects_in_example"] == 10, len(s["projects"])
        matched = [p for p in s["projects"] if p["our_id"]]
        assert len(matched) == s["found_in_filings"]["projects_matched"] == 10, f"{len(matched)} of their projects matched"
        assert all(p["our_id"] in state["projects"] for p in matched), [p["our_id"] for p in matched]
        ids = {p["our_id"] for p in matched}
        for r in s["rows"]:
            o = r["ours"]["overlap"]
            assert o["id"] == r["ours"]["id"] and {o["a"], o["b"]} <= ids and o["closest_points"], r["overlap_id"]
            assert o["sperry"] == r["overlap_id"] and o["rank"] == r["ours"]["rank"], (o["sperry"], r["overlap_id"])

    def calendar_matches_overlaps():
        # the coordination calendar draws the ranking's own timeline: one shared window per flagged pair whose build windows
        # overlap, with exactly the overlap's shared months, inside both projects' windows (checked at two settings)
        for q in ("", "?a=DESC&b=GA&window_months=60&max_km=50"):
            cal = ctx.request("GET", f"/api/gridlock/calendar{q}")
            ov = ctx.request("GET", f"/api/gridlock/overlaps{q}{'&' if q else '?'}limit=2000")
            assert cal["params"] == ov["params"], (cal["params"], ov["params"])
            rows = {o["id"]: o for o in ov["overlaps"]}
            want = {o["id"] for o in ov["overlaps"] if o["same_window"]}
            got = {w["id"]: w for w in cal["shared"]}
            assert set(got) == want, f"{q or 'defaults'}: shared windows for {sorted(set(got) ^ want)[:5]} differ from the overlaps"
            assert len(got) == len(cal["shared"]) == cal["counts"]["shared"], "duplicate shared windows"
            assert [w["rank"] for w in cal["shared"]] == sorted(w["rank"] for w in cal["shared"]), "shared windows not in rank order"
            projects = {r["id"]: r for r in cal["projects"]}
            chosen = set(ov["params"]["a"]) | set(ov["params"]["b"])
            placed = [x for x in state["projects"].values() if x.get("geometry") and x["utility"] in chosen]
            assert set(projects) == {x["id"] for x in placed}, "the calendar's projects aren't the compared ones"
            for pid, r in projects.items():
                mine = [o["id"] for o in ov["overlaps"] if pid in (o["a"], o["b"])]
                assert r["pairs"] == mine, f"{pid}: pairs {r['pairs'][:3]} != the overlaps it is in {mine[:3]}"
                assert r["best_rank"] == (rows[mine[0]]["rank"] if mine else None), (pid, r["best_rank"])
                if r["start"]:
                    assert r["start"] <= r["end"] and r["start_as"] in ("filed", "derived") and r["basis"], (pid, r)
                    # the bar ends at in-service, unless the filing puts most of its money after it (J3: the window
                    # follows the money, and the spend_after_in_service check says so)
                    after = "spend_after_in_service" in str(r.get("basis") or "")
                    assert r["end"] == r["in_service"] or (after and r["end"] > r["in_service"]), f"{pid}: the bar ends {r['end']}, in service {r['in_service']} as filed"
            assert cal["counts"]["start_derived"] == ov["window_assumed"]["projects"], (cal["counts"], ov["window_assumed"])
            for w in cal["shared"]:
                o = rows[w["id"]]
                assert w["months"] == o["windows_overlap_months"] > 0, f"{w['id']}: {w['months']} months, the overlap says {o['windows_overlap_months']}"
                assert (w["rank"], w["a"], w["b"], w["ahead"]) == (o["rank"], o["a"], o["b"], o["ahead"]), w["id"]
                assert w["station"] == ((o.get("shared_station") or {}).get("name")), (w["id"], w["station"])
                pa, pb = projects[w["a"]], projects[w["b"]]
                assert w["start"] == max(pa["start"], pb["start"]) and w["end"] == min(pa["end"], pb["end"]) and w["start"] < w["end"], w
                assert w["starts_as"] == {"a": pa["start_as"], "b": pb["start_as"]}, w["id"]
            c = cal["counts"]
            assert c["shared_open"] + c["shared_future"] == sum(1 for w in cal["shared"] if w["ahead"] != "past"), c
            assert c["shared_start_derived"] == sum(1 for w in cal["shared"] if "derived" in w["starts_as"].values()), c
            # same-station pairs whose windows don't overlap are still named, with the gap between the two windows
            want_st = [o["id"] for o in ov["overlaps"] if o.get("shared_station") and not o["same_window"]]
            assert [s["id"] for s in cal["stations"]] == want_st and c["stations"] == len(want_st), (cal["stations"], want_st)
            for s in cal["stations"]:
                o = rows[s["id"]]
                assert (s["rank"], s["station"], s["gap_days"]) == (o["rank"], o["shared_station"]["name"], o["window_gap_days"]), s
                if s["gap_from"]:
                    first, second = projects[s["first"]], projects[s["b"] if s["first"] == s["a"] else s["a"]]
                    assert (s["gap_from"], s["gap_to"]) == (first["end"], second["start"]) and s["gap_from"] <= s["gap_to"], s
            if not q:  # Sperry's OVL_1 (Thurmond Dam) has no shared window at the defaults: the calendar still names it
                assert any(s["sperry"] == "OVL_1" and s["station"] == "Thurmond Dam" for s in cal["stations"]), cal["stations"]
        # a pair's windows are the ones its trace reports (the ranking's own timeline)
        cal = ctx.request("GET", "/api/gridlock/calendar")
        w = cal["shared"][0]
        t = ctx.request("GET", f"/api/gridlock/trace/{w['id']}")["timeline"]
        pr = {r["id"]: r for r in cal["projects"]}
        for side in ("a", "b"):
            win, r = t[f"{side}_window"], pr[w[side]]
            assert (win["start"], win["end"]) == (r["start"], r["end"]), (side, win, r["start"], r["end"])
        state["calendar"] = cal
        ctx.request("GET", "/api/gridlock/calendar?max_km=0", expect=422)
        ctx.request("GET", "/api/gridlock/calendar?a=DESC&b=DESC", expect=422)

    def calendar_ics():
        cal = state["calendar"]
        by = {r["id"]: r for r in cal["projects"]}
        # the whole calendar: a shared window per flagged pair that has one, then the build window of every project in a pair
        body, hdr = _raw("/api/gridlock/calendar.ics")
        assert hdr.get("content-type", "").startswith("text/calendar") and "attachment;" in hdr.get("content-disposition", ""), hdr
        vcal, events = _ics_events(body)
        assert vcal["VERSION"][0][1] == "2.0" and vcal["PRODID"][0][1], vcal.get("VERSION")
        in_pairs = [r for r in cal["projects"] if r["pairs"] and r["start"]]
        assert len(events) == len(cal["shared"]) + len(in_pairs), f"{len(events)} events, {len(cal['shared'])} + {len(in_pairs)} expected"
        uids = [e["UID"][0][1] for e in events]
        assert len(set(uids)) == len(uids), "duplicate UIDs"
        for e in events:
            for k in ("UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY", "DESCRIPTION"):
                assert len(e.get(k, [])) == 1, f"{uids[0]}: {k} x {len(e.get(k, []))}"
            assert re.fullmatch(r"\d{8}T\d{6}Z", e["DTSTAMP"][0][1]), e["DTSTAMP"]
            assert _ics_date(e["DTSTART"]) < _ics_date(e["DTEND"]), (e["UID"], e["DTSTART"], e["DTEND"])
            assert "as filed" in e["DESCRIPTION"][0][1].lower() and "not an official utility record" in e["DESCRIPTION"][0][1]
        for w, e in zip(cal["shared"], events):  # shared windows first, in rank order, on their dates (DTEND exclusive)
            assert (_ics_date(e["DTSTART"]), _ics_date(e["DTEND"])) == (w["start"], _day_after(w["end"])), (w["id"], e["DTSTART"], e["DTEND"])
            # "as filed" only when both starts are filed; a shared window resting on a derived start says whose
            summary, first_line = e["SUMMARY"][0][1], e["DESCRIPTION"][0][1].split("\n")[0]
            derived = [k for k in ("a", "b") if w["starts_as"][k] == "derived"]
            if derived:
                assert "(as filed)" not in summary and "derived)" in summary, (w["id"], summary)
                assert not first_line.startswith("As filed") and "derived, not filed" in first_line, (w["id"], first_line)
            else:
                assert summary.endswith("(as filed)") and first_line.startswith("As filed"), (w["id"], summary)

        def plain(s):  # the .ics tidies the filings' capitals (Georgia's table): compare names case- and space-blind
            return re.sub(r"\s+", " ", s or "").strip().lower()

        # one pair's file: its shared window, naming both projects in full with their sources
        w = cal["shared"][0]
        body, hdr = _raw(f"/api/gridlock/calendar.ics?pair={urllib.parse.quote(w['id'])}")
        _, events = _ics_events(body)
        assert len(events) == 1, f"{len(events)} events for one pair"
        e = events[0]
        assert (_ics_date(e["DTSTART"]), _ics_date(e["DTEND"])) == (w["start"], _day_after(w["end"])), e
        text = e["SUMMARY"][0][1] + "\n" + e["DESCRIPTION"][0][1]
        for pid in (w["a"], w["b"]):
            assert pid in text and plain(by[pid]["name"]) in plain(text), f"the pair's event doesn't name {pid} ({by[pid]['name']})"
            src = by[pid]["source"]
            assert src["title"] and src["title"] in text, f"{pid}: source missing"
        assert f"#{w['rank']}" in text and "could" in text and " will " not in text, text[:300]
        # a same-station pair without a shared window: its file holds the two build windows, each naming the station
        for s in cal["stations"][:1]:
            _, events = _ics_events(_raw(f"/api/gridlock/calendar.ics?pair={urllib.parse.quote(s['id'])}")[0])
            assert len(events) == 2 and all(s["station"] in x["DESCRIPTION"][0][1] for x in events), [x["SUMMARY"] for x in events]
        # still ahead only: nothing that ended before today, as filed
        from datetime import date as _d

        today = _d.today().isoformat()
        _, events = _ics_events(_raw("/api/gridlock/calendar.ics?upcoming=true")[0])
        n_up = sum(1 for w in cal["shared"] if w["ahead"] != "past") + sum(1 for r in in_pairs if r["now"] != "past")
        assert len(events) == n_up and all(_ics_date(x["DTEND"]) > today for x in events), (len(events), n_up)
        # every compared project, and the refusals
        _, events = _ics_events(_raw("/api/gridlock/calendar.ics?scope=all")[0])
        assert len(events) == len(cal["shared"]) + sum(1 for r in cal["projects"] if r["start"]), len(events)
        ctx.request("GET", "/api/gridlock/calendar.ics?scope=everything", expect=422)
        ctx.request("GET", "/api/gridlock/calendar.ics?pair=NOPE~NADA", expect=404)
        ctx.request("GET", "/api/gridlock/calendar.ics?max_km=0", expect=422)

    def calendar_sheet():
        # the Excel export gains a calendar sheet after Sperry's two (which stay first and unchanged); CSV serves it too
        cal = state["calendar"]
        sheets = _xlsx_sheets(_raw("/api/gridlock/export.xlsx")[0])
        assert list(sheets)[:2] == ["projects", "overlaps"] and "calendar" in sheets, list(sheets)
        c = sheets["calendar"]
        h = c[0]
        shared = [r for r in c[1:] if r[h.index("row_type")] == "shared window"]
        build = [r for r in c[1:] if r[h.index("row_type")] == "build window"]
        station = [r for r in c[1:] if r[h.index("row_type")] == "same station, no shared window"]
        assert [r[h.index("id")] for r in shared] == [w["id"] for w in cal["shared"]], "calendar sheet's shared windows"
        assert [r[h.index("id")] for r in station] == [s["id"] for s in cal["stations"]], "calendar sheet's same-station rows"
        assert all(r[h.index("same_station")] for r in station), "a same-station row without its station"
        pair_ranks = [r[h.index("rank")] for r in c[1:] if r[h.index("row_type")] != "build window"]
        assert pair_ranks == sorted(pair_ranks), "the sheet's pair rows aren't in rank order"
        for r, w in zip(shared, cal["shared"]):  # 'filed' only when both starts are
            assert (r[h.index("start_as")] == "filed") == ("derived" not in w["starts_as"].values()), (w["id"], r[h.index("start_as")])
        assert [r[h.index("months")] for r in shared] == [float(w["months"]) for w in cal["shared"]], "shared months differ"
        assert len(build) == sum(1 for r in cal["projects"] if r["start"]), len(build)
        assert all(isinstance(r[h.index("start")], float) and isinstance(r[h.index("end")], float) for r in shared + build), "not date cells"
        body, _ = _raw("/api/gridlock/export.csv?table=calendar")
        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        assert rows[0] == h and len(rows) == len(c), (rows[0][:4], len(rows), len(c))

    def desc_current_list():
        # DESC's 2026-2030 filing is its current list; the 2024-2028 projects it no longer carries stay, marked; a project
        # in both lists is read from the 2026-2030 one; Georgia's public-disclosure copy says what that means
        s = ctx.request("GET", "/api/gridlock/summary")
        ed = s["edition"]
        assert ed and ed["desc_current"] == "2026-2030" and ed["desc_earlier"] == "2024-2028", ed
        src = {x["id"]: x for x in s["sources"]}
        assert src["desc_2026"]["role"] == "current" and src["desc"]["role"] == "earlier", [(k, v.get("role")) for k, v in src.items()]
        assert "public" in src["ga_irp"]["public_note"].lower() and "CEII" in src["ga_irp"]["public_note"], src["ga_irp"].get("public_note")
        assert "never inferred" in src["ga_irp"]["public_note"], src["ga_irp"]["public_note"]
        assert s["limit"]["text"] == "25 mi (40.2 km)" and abs(s["limit"]["max_km"] - SPERRY_KM) < 1e-9, s["limit"]
        p = ctx.request("GET", "/api/gridlock/projects")
        desc = [x for x in p["projects"] + p["quarantine"] if x["utility"] == "DESC"]
        eds = {x.get("edition") for x in desc}
        assert eds == {"2026-2030", "2024-2028"}, eds
        new = [x for x in desc if x["edition"] == "2026-2030"]
        old = [x for x in desc if x["edition"] == "2024-2028"]
        assert len(new) == ed["new_list"]["read"] and len(old) == ed["earlier_kept"]["read"], (len(new), len(old), ed)
        assert all((x.get("provenance") or {}).get("source") == "desc_2026" for x in new), "a 2026-2030 record from another source"
        assert all((x.get("provenance") or {}).get("source") == "desc" and x.get("edition_note") for x in old), "a 2024-2028 record unmarked"
        placed = {x["id"] for x in p["projects"]}
        assert not ({x["id"] for x in old} & {x["id"] for x in new}), "a project read from both lists"
        assert "DESC-6810A" in placed and state["projects"]["DESC-6810A"]["edition"] == "2024-2028", "Sperry's Thurmond tie isn't kept, marked"
        # the list looks forward: pairs whose shared build window is still ahead
        o = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")
        ahead = [r for r in o["overlaps"] if r["same_window"] and r["ahead"] == "future"]
        assert len(ahead) >= 5, f"only {len(ahead)} pairs with a shared window still ahead"
        top = o["overlaps"][0]
        assert state["projects"][top["a"]]["edition"] == "2026-2030" and top["ahead"] in ("future", "open"), (top["id"], top["ahead"])
        thurmond = [r for r in o["overlaps"] if r.get("sperry") == "OVL_1"]
        assert thurmond and thurmond[0]["group"] == "passed" and thurmond[0]["rank"] > 1, [(r["rank"], r["group"]) for r in thurmond]

    def changes_route():
        # What changed since DESC's last filing: every project of both lists, both pages linked, and what it does to the list
        c = ctx.request("GET", "/api/gridlock/changes")
        n = c["counts"]
        assert n["carried_over"] + n["dropped"] == n["old_projects"] and n["carried_over"] + n["new"] == n["new_projects"], n
        rows = c["rows"]
        assert len(rows) == n["carried_over"] + n["dropped"] + n["new"], len(rows)
        kinds = {k: [r for r in rows if r["change"] == k] for k in ("carried_over", "dropped", "new")}
        assert len(kinds["carried_over"]) == n["carried_over"] and len(kinds["new"]) == n["new"], {k: len(v) for k, v in kinds.items()}
        assert sum(1 for r in kinds["carried_over"] if "later" in r["tags"]) == n["later"], n["later"]
        for r in rows:
            for side in ("old", "new"):
                if r[side]:
                    assert r[side]["url"].startswith("https://www.scrtp.com/") and f"#page={r[side]['page']}" in r[side]["url"], r[side]
            assert (r["old"] is None) == (r["change"] == "new") and (r["new"] is None) == (r["change"] == "dropped"), r["change"]
            if r["on_map"]:
                assert r["id"] in state["projects"], r["id"]
        cmp = c["comparison"]
        assert cmp["with_2026_2030"]["together"] > cmp["with_2024_2028"]["together"], cmp
        assert cmp["limit"] == "25 mi (40.2 km)", cmp["limit"]

    def one_savings_figure():
        # savings that need both crews in the field at once are counted only when the build windows share months: a pair
        # with no shared window never shows them (they are listed as left out, with why)
        o = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")["overlaps"]
        apart = [r for r in o if r["same_window"] is False][:6]
        together = [r for r in o if r["same_window"]][:3]
        assert apart and together, (len(apart), len(together))
        for r in apart:
            e = ctx.request("GET", f"/api/gridlock/estimate/{r['id']}")
            assert not [it for it in e["items"] if it.get("needs") == "same build window"], (r["id"], [it["id"] for it in e["items"]])
            assert any(x["id"] == "mobilization" for x in e["left_out"]), (r["id"], e["left_out"])
            assert e["total_low"] == sum(it["low"] for it in e["items"] if it["unit"] == "USD"), r["id"]
        for r in together:
            e = ctx.request("GET", f"/api/gridlock/estimate/{r['id']}")
            assert any(it["id"] == "mobilization" for it in e["items"]) and not e["left_out"], (r["id"], e["left_out"])

    def judge_fixes():
        """Sperry's cold test (Sat 21:34): J3 windows follow the money, J4 placed from the description / unplaced listed,
        J5 location accuracy against Sperry's hand-located endpoints, near-ends-only pairs, savings that fit the work,
        one local date, the 47 other Georgia rows explained, the all-four-Georgia view, the slip base rate."""
        s = ctx.request("GET", "/api/gridlock/summary")
        by_id = {x["id"]: x for x in ctx.request("GET", "/api/gridlock/projects")["projects"]}
        # J3: a token first-year amount doesn't open the window (p41: $50K of $5.38M in 2027)
        p41 = by_id.get("DESC-6888")
        if p41:
            assert p41["build_window"]["start"] == "2028-01-01", p41["build_window"]
        # J3: most of the money after the filed in-service date is flagged, and the window follows it (p26)
        p26 = by_id.get("DESC-6810O")
        if p26:
            chk = {c["id"]: c for c in p26["checks"]}
            assert chk["spend_after_in_service"]["status"] == "warn", chk.get("spend_after_in_service")
            assert p26["build_window"]["end"] == "2028-12-31" and p26["in_service"] == "2027-12-31", p26["build_window"]
        rules = {c["id"] for c in s["report"]["checks"]}
        assert "spend_after_in_service" in rules, sorted(rules)
        ov = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")
        kinds = ov["window_kinds"]
        assert set(kinds) == {"spending", "planning", "derived"}, kinds
        for r in ov["overlaps"]:
            for side, pid in (("a", r["a"]), ("b", r["b"])):
                if by_id[pid]["utility"] != "DESC" and not by_id[pid]["build_window"].get("assumed"):
                    assert r["window_kinds"][0 if side == "a" else 1] == "planning", (r["id"], r["window_kinds"])
            # close only at the nearest ends: within the limit at the closest points, not by the center method
            assert r["near_ends_only"] == (r["center_distance_km"] > ov["params"]["max_km"]), (r["id"], r["center_distance_km"])
            if r["near_ends_only"]:
                assert any(x.startswith("Close only at the nearest ends") for x in r["reasons"]), r["id"]
            assert r["windows_overlap_months"] is None or not r["same_window"] or r["windows_overlap_months"] >= 0.5, (r["id"], r["windows_overlap_months"])
        # J4: set aside only for want of a place and its description names a placed station -> placed there, low confidence
        f = s["funnel"]
        res = f["rescued"]
        assert res["count"] == len(res["records"]) and f["passed_checks"] + res["count"] == f["passed"], (f["passed"], f["passed_checks"], res["count"])
        assert f["extracted"] == f["passed"] + f["set_aside"], f
        for r in res["records"]:
            x = by_id[r["id"]]
            assert x["confidence"] == "low" and "placed_from_description" in x["tags"] and x.get("geometry"), r["id"]
            assert any(e.get("confidence") == "low" and "placed from the description" in (e.get("match") or "") for e in x["endpoints"]), r["id"]
        riv = next((r for r in res["records"] if r["id"] == "DESC-6367D"), None)
        assert riv and riv["at"] == ["Okatie"], ("Riverport Tap ('a 230 kV Tap from Okatie to Riverport') not placed at Okatie", riv)
        assert not any(r["id"] == "GA-20989" for r in res["records"]), "Rice Hope's transformer placed at McIntosh from a line's name"
        un = ov["unplaced"]
        assert un["label"] == "Possible overlaps, unplaced" and un["count"] >= len(un["projects"]), un["label"]
        placed_ids = set(by_id)
        for u in un["projects"]:
            assert u["id"] not in placed_ids and u["why"].startswith("no endpoint could be placed") and u["same_months_as"] >= 0, u
        # J5: our geocoded endpoints against Sperry's hand-located ones, in metres
        la = s["location_accuracy"]
        assert la["compared"] >= 10 and la["within"] <= la["compared"] <= la["of"] and la["within_m"] == 500, la
        assert la["median_m"] is not None and la["median_m"] <= 500 and all(r["error_m"] is None or r["error_m"] >= 0 for r in la["rows"]), la["text"]
        assert f"{la['within']} of {la['of']}" in la["text"], la["text"]
        # the 47 other Georgia rows explained in the funnel itself; "all four Georgia utilities" is one filter away
        dv = f["default_view"]
        assert dv["pairs"] == dv["a"]["placed"] * dv["b"]["placed"] == ov["total_pairs"], (dv, ov["total_pairs"])
        assert dv["not_compared_total"] == sum(x["placed"] for x in dv["not_compared"]) and "GTC" in dv["text"], dv["text"]
        four = ctx.request("GET", "/api/gridlock/overlaps?b=GA&limit=2000")
        assert four["params"]["b"] == ["GPC", "GTC", "MEAG", "DU"] and four["total_pairs"] == dv["all_four"]["pairs"], (four["params"], four["total_pairs"])
        # a base rate, never a per-project prediction
        sb = s["slip_base_rate"]
        assert sb and 0 < sb["later"] <= sb["carried_over"] and "not a prediction" in sb["text"], sb
        # savings that fit the kinds of work: 46 kV and 230 kV line work share no line crews
        e = ctx.request("GET", "/api/gridlock/estimate/DESC-6810O~GA-21116")
        assert e["crew_fit"]["fit"] != "line", e["crew_fit"]
        for it in e["items"]:
            assert "Line crews" not in it["label"], it["label"]
        # one date: the Eastern calendar date, never the server's UTC one
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        eastern = {(now - timedelta(hours=h)).date().isoformat() for h in (4, 5)}
        today = ctx.request("GET", "/api/gridlock/changes")["comparison"]["today"]
        assert today in eastern, (today, eastern)

    def cold_test_fixes():
        """Sperry cold test (Sat 22:40): a line worked on only part of its length is measured to that part's located end
        (Georgia Power's Euchee Creek - Thurmond Dam segment: to Thurmond Dam, not Evans Primary); a conductor spec is never
        read as a part of the line; a shared window resting on a flagged record (money filed after the in-service date)
        ranks lower and says why; every pair sharing months carries its joint window."""
        rows = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")["overlaps"]
        by = {r["id"]: r for r in rows}
        r1 = by.get("DESC-6809T~GA-20793")
        assert r1 and r1["partial"][1] and r1["partial"][1]["measured_to"] == ["Thurmond Dam"], r1 and r1["partial"]
        assert "measured to that part" in r1["partial"][1]["text"] and r1["distance_mi"] > 9, (r1["distance_mi"], r1["partial"])
        for r in rows:
            for part in r["partial"]:
                assert not part or not any(w in part["part"] for w in ("ACSR", "ACSS", "conductor")), part
            if r["same_window"]:
                jw = r["joint_window"]
                assert jw and jw["start"] <= jw["end"], (r["id"], jw)
            else:
                assert r["joint_window"] is None, (r["id"], r["joint_window"])
        r2 = by.get("DESC-6810O~GA-21116")
        assert r2 and r2["window_flags"][0] and r2["window_flags"][0][0]["id"] == "spend_after_in_service", r2 and r2["window_flags"]
        assert r2["score_parts"]["timeline"] == 0.6 and any("(x0.6)" in x for x in r2["reasons"]), (r2["score_parts"], r2["reasons"][:3])
        assert r2["rank"] > 3, r2["rank"]  # demoted below the pairs whose dates agree with themselves
        tr = ctx.request("GET", "/api/gridlock/trace/DESC-6810O~GA-21116")
        assert tr["score"]["reproduced"] and "because" in tr["score"]["terms"][1]["how"], tr["score"]["terms"][1]

    ctx.check("gridlock: Sperry judge fixes (windows follow the money, spend after in-service flagged, placed from the description, "
              "unplaced listed, location accuracy in metres, near-ends-only pairs, crews that fit the work, the other Georgia rows, "
              "all four Georgia utilities, slip base rate, Eastern date)", judge_fixes)
    ctx.check("gridlock: cold-test fixes (a partial line measured to its worked part, conductor specs not read as parts, a "
              "flagged window ranked lower with the reason, every shared window's months on the pair)", cold_test_fixes)
    ctx.check("gridlock: summary has sources, DESC + Georgia counts, and the pipeline report", summary_shape)
    ctx.check("gridlock: projects cover DESC and a Georgia utility, placed inside SC/GA, quarantine has reasons", projects_both_sides)
    ctx.check("gridlock: default overlaps (within 25 mi exactly) are ranked cross-utility pairs, grouped (building in the same "
              "months first), with tiers matching their distances", overlaps_default)
    ctx.check("gridlock: the Thurmond Dam pair (one OSM substation in both filings) is flagged same station, ranked above every "
              "distance-tier pair of its group, its reason giving both in-service dates as filed", same_station)
    ctx.check("gridlock: DESC's 2026-2030 list is ranked as its current plan (the 2024-2028 projects it no longer carries kept, "
              "marked; none read twice); Georgia's public-disclosure note; a still-ahead pair leads, Thurmond (OVL_1) lower down",
              desc_current_list)
    ctx.check("gridlock: what changed since DESC's last filing adds up (carried over / dropped / new, both pages linked) and "
              "the newer list has more pairs building in the same months", changes_route)
    ctx.check("gridlock: a pair with no shared build window never counts crews mobilized once or a shared yard (left out, "
              "with why); a pair with one does", one_savings_figure)
    ctx.check("gridlock: center method stays under 25 mi; a tighter threshold stays tighter", overlaps_center_method)
    ctx.check("gridlock: bad max_km / window / method / utilities / limits are 422s", param_validation)
    ctx.check("gridlock: opportunities inline both projects and say what they could share", opportunities)
    ctx.check("gridlock: estimate for the top overlap has low <= high items, sources and assumptions", estimate_top)
    ctx.check("gridlock: unknown or same-utility estimate ids are 404s", estimate_unknown)
    ctx.check("gridlock: Sperry's worked example is reproduced (6 of 6, no extra pairs) and found in the full filings", sperry_reproduced)
    ctx.check("gridlock: the funnel adds up (rows = passed + set aside = rows by filing; each set-aside group counted from the "
              "reasons; pairs compared = DESC x Georgia Power projects; passed = both sides + the utilities switched off + "
              "unplaced; flagged <= compared)", funnel_adds_up)
    ctx.check("gridlock: trace/{id} takes the top 25 pairs apart (rank, the four terms multiplying back to the score, distance, "
              "endpoints, the committed row, the recorded SHA-256); unknown / same-utility ids 404, bad settings 422", trace_route)
    ctx.check("gridlock: Sperry's example as the start: their ten projects all matched to ours, their six pairs drawable",
              sperry_start)
    ctx.check("gridlock: the build-window setting changes pairs whose filing gives no start date", window_setting_matters)
    ctx.check("gridlock: basemap has states, lines and a bbox", basemap)
    ctx.check("gridlock: the fault test covers nine bad-data kinds + the two-digit-year format test (never counted as catches); "
              "every catch rate adds up (served = committed file)", fault_report)
    ctx.check("gridlock: export.xlsx opens as a workbook with Sperry's exact columns, every project and every flagged overlap; "
              "shared_window only for pairs that share a window", export_xlsx)
    ctx.check("gridlock: export.csv keeps Sperry's columns; export.geojson has every project inside SC/GA; bad params are 422s",
              export_csv_geojson)
    ctx.check("gridlock: the coordination calendar's shared windows are exactly the overlaps' shared months, inside both build "
              "windows, for every flagged pair (two settings); each project's pairs match; same-station pairs without one (Sperry's "
              "OVL_1, Thurmond Dam) are named with the gap between their windows; bad settings 422", calendar_matches_overlaps)
    ctx.check("gridlock: calendar.ics parses strictly (CRLF, 75-octet folds, BEGIN/END pairs, one UID/DTSTAMP/DTSTART/DTEND each); "
              "'as filed' only when both starts are filed, else whose start is derived; a pair's file names both projects and "
              "their sources; a same-station pair's file its two windows; upcoming=true drops what ended; scope=all adds every "
              "project; bad scope 422, unknown pair 404", calendar_ics)
    ctx.check("gridlock: the Excel export has a calendar sheet after Sperry's two (same shared windows and months, derived starts "
              "marked, same-station rows, pair rows in rank order); CSV serves it", calendar_sheet)
