"""Smoke checks for the AI boom, year by year (owned by the timelapse track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200), auth(), expected.
Public GET routes only (plus the public what-if to cross-check one year): nothing is created."""

import math
import time
from urllib.parse import quote

TIMEOUT_S = 90


def _poll(ctx, path: str) -> dict:
    t0 = time.monotonic()
    while True:
        r = ctx.request("GET", path)
        if r["state"] == "done":
            return r
        assert r["state"] == "running", f"{path}: unexpected state {r['state']!r}: {r.get('detail')}"
        assert time.monotonic() - t0 < TIMEOUT_S, f"{path}: still running after {TIMEOUT_S} s"
        time.sleep(0.5)


def register(ctx):
    cache: dict = {}

    def fl() -> dict:
        if "fl" not in cache:
            cache["fl"] = _poll(ctx, "/api/timelapse?region=FL")
        return cache["fl"]

    def years_ascending():
        r = fl()
        assert r["synthetic"] is True and "synthetic" in r["note"].lower()
        assert r["years"] == list(range(2026, 2036)), r["years"]
        assert [f["year"] for f in r["frames"]] == [None] + r["years"], "the grid alone first, then one frame per year"
        mws = [f["mw"] for f in r["frames"]]
        assert mws == sorted(mws), f"cumulative MW must never fall: {mws}"
        assert r["frames"][0]["mw"] == 0 and r["frames"][0]["strain"]["over"] >= 0
        assert len(r["lines"]["pct"]) == len(r["frames"]) and all(len(p) == len(r["lines"]["ids"]) for p in r["lines"]["pct"])

    def mw_adds_up_to_the_catalog():
        r = fl()
        a = r["accounting"]
        places = ctx.request("GET", "/api/catalog/places?state=FL")
        curated = [e for e in places["entries"] if e.get("origin") == "curated"]
        catalog_mw = sum(float(e["mw"] or 0) for e in curated)
        assert math.isclose(a["catalog_mw"], catalog_mw, abs_tol=0.6), f"catalog MW {a['catalog_mw']} != the catalog's {catalog_mw}"
        assert math.isclose(sum(s["mw"] for s in r["status_options"]), catalog_mw, abs_tol=len(r["status_options"])), "statuses must cover the catalog"
        parts = a["placed_mw"] + a["later_mw"] + a["no_year_mw"] + a["off_model_mw"]
        assert math.isclose(a["selected_mw"], parts, abs_tol=0.6), a
        assert math.isclose(a["catalog_mw"], a["selected_mw"] + a["status_mw"], abs_tol=0.6), a
        assert math.isclose(r["frames"][-1]["mw"], a["placed_mw"], abs_tol=0.6), "the last year carries every placed MW"
        for c in r["campuses"]:
            got = sum(x["mw"] for x in c["arrivals"])
            assert math.isclose(got, c["placed_mw"], abs_tol=0.2), c["id"]
            assert got + c["no_year_mw"] <= c["mw"] + 0.5, f"{c['id']}: more MW placed than reported"
            assert all(2026 <= x["year"] <= 2035 for x in c["arrivals"]), c["id"]
            assert c["status"] in r["statuses"], c["id"]
        # the default statuses leave out only what is paused, canceled, withdrawn or denied, as reported
        assert set(r["statuses"]) == {"operating", "under construction", "announced"}, r["statuses"]

    def every_campus_sourced():
        r = fl()
        assert r["campuses"], "Florida has reported campuses with reported years"
        rows = r["campuses"] + [x for v in r["left_out"].values() for x in v]
        for c in rows:
            assert c["sources"] and all(s["url"].startswith("http") for s in c["sources"]), f"{c['id']} has no source link"
            assert c["name"] and c["status"], c["id"]
        assert "synthetic" in r["frame"].lower() and "not a prediction" in r["frame"].lower()
        text = " ".join(str(c.get("read", "")) for c in r["campuses"]).lower()
        assert " will " not in f" {text} ", "no 'will' about a real project"
        for c in r["campuses"]:  # a full size counted from a first phase's year is flagged, and says so
            assert c["higher_end"] in (True, False), c["id"]
            assert not c["higher_end"] or "higher end" in c["read"], c["id"]
        for c in r["left_out"]["off_model"]:  # the model's coverage, never the map-drop message
            assert "drop the data center" not in c["why"] and "synthetic" in c["why"], c

    def strain_matches_a_direct_whatif():
        r = fl()
        pick = None
        for f in r["frames"][1:]:
            on = [c for c in r["campuses"] if c["first_year"] <= f["year"]]
            if f["mw"] > 0 and len(on) <= 12:
                pick = (f, on)  # the last year the public what-if can take (12 sites at most)
        assert pick, "no year to cross-check"
        f, on = pick
        sites = [{"lat": c["lat"], "lon": c["lon"], "mw": sum(x["mw"] for x in c["arrivals"] if x["year"] <= f["year"])} for c in on]
        w = ctx.request("POST", "/api/grid/whatif", {"region": "FL", "sites": sites})
        assert math.isclose(w["mw"], f["mw"], abs_tol=0.6), (w["mw"], f["mw"])
        peak = max(w["loading_pct"])
        assert abs(peak - f["strain"]["peak_pct"]) <= 0.15, f"{f['year']}: peak {f['strain']['peak_pct']} vs what-if {peak}"
        assert len(w["overloaded"]) == f["strain"]["over"], f"{f['year']}: {f['strain']['over']} over vs what-if {len(w['overloaded'])}"
        assert f["strain"]["over"] == f["strain"]["over_lines"] + f["strain"]["over_transformers"]
        base = r["frames"][0]["strain"]
        alone = ctx.request("POST", "/api/grid/whatif", {"region": "FL", "sites": [{"lat": on[0]["lat"], "lon": on[0]["lon"], "mw": 1}]})
        assert base["peak_pct"] <= max(alone["loading_pct"]) + 0.5

    def growth_is_sourced():
        r = fl()
        g = r["growth"]
        assert g and g["source"]["url"].startswith("https://www.nerc.com/"), "Florida's load growth cites NERC's LTRA"
        lf = [f["load_factor"] for f in g["frames"][1:]]
        assert lf == sorted(lf) and lf[0] == 1.0 and abs(lf[-1] - 60415 / 54477) < 1e-3, lf
        assert len(r["lines"]["pct_growth"]) == len(g["frames"])

    def capacity_cited():
        c = fl()["capacity"]
        assert c["status"] == "ready", c
        assert 0 <= c["today"] <= c["with_upgrades"] and c["campus_mw"] == 1000
        assert "Strengthen" in c["source"] and c["caveat"]
        again = _poll(ctx, "/api/timelapse?region=FL")  # a cache hit reads the Strengthen answer afresh
        assert again["capacity"]["status"] == "ready" and again["capacity"]["today"] == c["today"], again["capacity"]

    def status_filter_and_validation():
        r = _poll(ctx, "/api/timelapse?region=FL&statuses=" + quote("operating,under construction"))
        assert r["statuses"] == ["operating", "under construction"]
        assert all(c["status"] in ("operating", "under construction") for c in r["campuses"])
        assert r["frames"][-1]["mw"] <= fl()["frames"][-1]["mw"]
        ctx.request("GET", "/api/timelapse?region=FL&statuses=rumored", expect=422)
        ctx.request("GET", "/api/timelapse?region=US", expect=422)
        ctx.request("GET", "/api/timelapse?region=ZZ", expect=422)

    ctx.check("timelapse: Florida's years ascend, the grid alone first", years_ascending)
    ctx.check("timelapse: cumulative MW adds up to the catalog", mw_adds_up_to_the_catalog)
    ctx.check("timelapse: every campus has its source", every_campus_sourced)
    ctx.check("timelapse: a year's strain matches a direct what-if", strain_matches_a_direct_whatif)
    ctx.check("timelapse: Florida's load growth is the published NERC forecast", growth_is_sourced)
    ctx.check("timelapse: the Strengthen answer is cited", capacity_cited)
    ctx.check("timelapse: status filter and bad input", status_filter_and_validation)
