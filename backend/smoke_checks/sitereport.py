"""Smoke checks for the site report (owned by the site-report track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
POST /api/site/report is public and stores nothing; every check here reads."""

import json
import math
import time

SUB_KEYS = {"rank", "id", "name", "area", "lat", "lon", "distance_km", "kv", "headroom_mw", "fits", "limiting", "right_size_mw", "upgrade"}


def register(ctx):
    hero = ctx.expected["hero"]
    at = {"lat": hero["lat"], "lon": hero["lon"]}
    big, mixed = {**at, "mw": hero["mw"]}, {**at, "mw": 240}
    got = {}

    def report_has_its_shape_and_is_sorted_by_distance():
        t0 = time.time()
        r = ctx.request("POST", "/api/site/report", big)
        got["cold_s"] = time.time() - t0
        got["big"] = r
        assert got["cold_s"] < 8, f"a cold report at {hero['mw']} MW took {got['cold_s']:.1f} s"
        s = r["site"]
        assert s["region"] == "FL" and s["mw"] == hero["mw"] and s["nearest_town"] and s["region_name"] == "Florida", s
        assert r["synthetic"] is True and r["estimate"] is True and "synthetic" in r["disclaimer"].lower(), r["disclaimer"]
        assert r["verdict"] and r["verdict_kind"] in ("fits", "fits_after_upgrades", "no_verified_fix"), r["verdict"]
        assert r["method"] and r["assumptions"] and all(x["name"] and x["url"].startswith("https://") for x in r["sources"]), r["sources"]
        subs = r["substations"]
        assert len(subs) == 6 and [x["rank"] for x in subs] == [1, 2, 3, 4, 5, 6], [x["rank"] for x in subs]
        dist = [x["distance_km"] for x in subs]
        assert dist == sorted(dist) and dist[0] < 10, dist  # the hero site is a couple of km from a substation
        for x in subs:
            assert SUB_KEYS <= set(x), SUB_KEYS - set(x)
            assert math.isfinite(x["headroom_mw"]) and x["headroom_mw"] >= 0 and 0 <= x["right_size_mw"] <= hero["mw"], x
            assert isinstance(x["fits"], bool) and (x["upgrade"] is None) == x["fits"], x
            if x["fits"]:
                assert x["right_size_mw"] == hero["mw"], x
        assert r["best_id"] in [x["id"] for x in subs], r["best_id"]
        # the hero campus doesn't fit at its nearest substation: an upgrade with money and a re-run, and the people if built anyway
        first = subs[0]
        assert first["fits"] is False and first["upgrade"]["count"] >= 1, first
        up = first["upgrade"]
        assert 0 < up["cost_low_usd"] <= up["cost_high_usd"] and up["mva_added"] > 0 and up["lines"], up
        assert all(ln["new_mva"] > ln["old_mva"] and ln["label"] for ln in up["lines"]), up["lines"]
        anyway = r["if_built_anyway"]
        assert anyway and anyway["estimate"] is True and anyway["summary"] and 1 <= len(anyway["sites"]) <= 3, anyway
        assert anyway["sites"][0]["people_hit"] > 0, anyway["sites"][0]

    def first_headroom_matches_the_heatmap():
        r = got["big"]
        by_sub = ctx.request("GET", "/api/grid/headroom")["by_sub"]
        for x in r["substations"]:
            assert abs(x["headroom_mw"] - by_sub[str(x["id"])]) <= 0.06, (x["name"], x["headroom_mw"], by_sub[str(x["id"])])
        # a load level other than 1.0 has its own headroom vector
        hot = ctx.request("POST", "/api/site/report", {**mixed, "load_factor": 1.04})
        hot_by = ctx.request("GET", "/api/grid/headroom?load_factor=1.04")["by_sub"]
        for x in hot["substations"]:
            assert abs(x["headroom_mw"] - hot_by[str(x["id"])]) <= 0.06, (x["name"], x["headroom_mw"], hot_by[str(x["id"])])
        assert hot["site"]["load_factor"] == 1.04, hot["site"]

    def fits_agrees_with_a_plain_whatif():
        r = ctx.request("POST", "/api/site/report", mixed)
        checked = fits = nofit = 0
        for x in r["substations"]:
            w = ctx.request("POST", "/api/grid/whatif", {"lat": x["lat"], "lon": x["lon"], "mw": 240})
            if w["sub"] != x["id"]:
                continue  # the point snapped to a substation stacked on this one: not a like-for-like drop
            checked += 1
            assert x["fits"] == (len(w["overloaded"]) == 0), (x["name"], x["fits"], len(w["overloaded"]))
            fits += x["fits"]
            nofit += not x["fits"]
            lim = x["limiting"]
            if lim and lim["limits_headroom"]:  # the limiting line: at the campus's size it is past its rating only where the site doesn't fit
                assert (lim["loading_pct_at_mw"] > 100) == (not x["fits"]), (x["name"], lim)
            if not x["fits"]:
                assert 0 <= x["right_size_mw"] < 240 and abs(x["right_size_mw"] - x["headroom_mw"]) <= 0.2, x
                # right-sized, the site takes it with no line over its limit
                if x["right_size_mw"] >= 1:
                    ok = ctx.request("POST", "/api/grid/whatif", {"lat": x["lat"], "lon": x["lon"], "mw": x["right_size_mw"]})
                    assert not ok["overloaded"], (x["name"], x["right_size_mw"], len(ok["overloaded"]))
        assert checked >= 4 and fits >= 1 and nofit >= 1, (checked, fits, nofit)  # both answers were tested

    def upgrades_are_verified_and_priced_like_fix_it():
        first = got["big"]["substations"][0]
        up = first["upgrade"]
        assert up["verified"] is True and up["still_over"] == 0 and up["reason"] is None, up
        case = {"lat": first["lat"], "lon": first["lon"], "mw": hero["mw"]}
        w = ctx.request("POST", "/api/grid/whatif", case)
        assert w["sub"] == first["id"] and len(w["overloaded"]) >= 1, (w["sub"], len(w["overloaded"]))
        fixed = ctx.request("POST", "/api/grid/whatif", {**case, "upgrades": up["apply"]})
        assert fixed["overloaded"] == [], fixed["overloaded"][:3]  # the case re-run with exactly these ratings: nothing over
        # the same search and unit costs as the cost panel
        c = {ln["key"]: ln for ln in ctx.request("POST", "/api/cost", case)["lines"]}["upgrades"]
        assert up["count"] == c["count"] and abs(up["cost_low_usd"] - c["low"]) <= 2 and abs(up["cost_high_usd"] - c["high"]) <= 2, (up, c["count"], c["low"], c["high"])
        assert abs(up["mva_added"] - c["added_mva"]) < 1, (up["mva_added"], c["added_mva"])

    def built_anyway_is_the_engines_cascade():
        anyway = got["big"]["if_built_anyway"]
        top = anyway["sites"][0]
        first = next(x for x in got["big"]["substations"] if x["id"] == top["substation"]["id"])
        c = ctx.request("POST", "/api/grid/cascade", {"lat": first["lat"], "lon": first["lon"], "mw": hero["mw"]})
        assert c["sub"] == first["id"], (c["sub"], first["id"])
        assert c["people_hit"] == top["people_hit"] and abs(c["lost_mw"] - top["lost_mw"]) < 0.2 and c["total_steps"] == top["steps"], (c["people_hit"], top)

    def n_minus_1_is_a_labeled_screen():
        n1 = got["big"]["n_minus_1"]
        assert n1 and 1 <= n1["checked"] <= 12 and n1["checked"] == len(n1["lines"]), n1
        assert n1["secure_count"] + n1["insecure_count"] == n1["checked"] and n1["summary"], n1
        for ln in n1["lines"]:
            assert ln["label"] and ln["worst_pct"] >= 0 and isinstance(ln["secure"], bool), ln
            assert ln["secure"] == (ln["overloaded_after"] == 0 and ln["lost_mw"] < 0.5), ln
        # a site that takes the campus untouched has an N-1 screen too, and the best row is the one it was run at
        fit = ctx.request("POST", "/api/site/report", mixed)
        assert fit["n_minus_1"]["substation"]["id"] == fit["best_id"], (fit["n_minus_1"]["substation"], fit["best_id"])
        assert fit["verdict_kind"] == "fits" and "240 MW" in fit["verdict"], fit["verdict"]

    def rejects_bad_input():
        for bad in (
            {**at, "mw": 0},
            {**at, "mw": 50001},
            {**at, "mw": -5},
            {**at, "mw": 100, "load_factor": 2},
            {**at, "mw": 100, "load_factor": 0.1},
            {**at, "mw": 100, "region": "ZZ"},
            {"lat": 40.0, "lon": -100.0, "mw": 100},  # Kansas, not in Florida
            {"lat": 91, "lon": -81.87, "mw": 100},
            {"lat": hero["lat"], "lon": hero["lon"]},  # no size
            {"lat": "x", "lon": -81.87, "mw": 100},
        ):
            ctx.request("POST", "/api/site/report", bad, expect=422)

    def another_region_answers():
        r = ctx.request("POST", "/api/site/report", {"region": "TX", "lat": 32.45, "lon": -99.73, "mw": 800})
        assert r["site"]["region"] == "TX" and r["site"]["region_name"] == "Texas" and len(r["substations"]) == 6, r["site"]
        assert r["synthetic"] is True and r["verdict"], r["verdict"]
        tx = ctx.request("GET", "/api/grid/headroom?region=TX")["by_sub"]
        for x in r["substations"]:
            assert abs(x["headroom_mw"] - tx[str(x["id"])]) <= 0.06, (x["name"], x["headroom_mw"], tx[str(x["id"])])

    def a_second_identical_call_is_cached():
        body = {**at, "mw": 733.3}  # a size no other check uses, so the first call is a real computation
        t0 = time.time()
        a = ctx.request("POST", "/api/site/report", body)
        t1 = time.time()
        b = ctx.request("POST", "/api/site/report", body)
        t2 = time.time()
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), "the cached answer differs"
        # a busy machine (other servers, a browser) can slow one call: judge the fastest of three repeats
        best = t2 - t1
        for _ in range(2):
            t3 = time.time()
            ctx.request("POST", "/api/site/report", body)
            best = min(best, time.time() - t3)
        assert best < 1.0, f"a repeated call took {best:.2f} s at best (first {t1 - t0:.2f} s)"

    ctx.check("site report: six substations sorted by distance, with limits, right-size, upgrades, N-1, if-built-anyway and the disclaimer", report_has_its_shape_and_is_sorted_by_distance)
    ctx.check("site report: every substation's headroom equals the heatmap's, at two load levels", first_headroom_matches_the_heatmap)
    ctx.check("site report: fits agrees with a plain what-if at that substation, and right-sized sizes leave nothing over", fits_agrees_with_a_plain_whatif)
    ctx.check("site report: an upgrade is verified by a re-run with its own ratings and priced like the cost panel", upgrades_are_verified_and_priced_like_fix_it)
    ctx.check("site report: 'if built anyway' is the engine's own cascade at that substation", built_anyway_is_the_engines_cascade)
    ctx.check("site report: the N-1 screen is bounded, consistent and run at the best substation", n_minus_1_is_a_labeled_screen)
    ctx.check("site report: rejects a bad size, load level, region, point off the model, missing size", rejects_bad_input)
    ctx.check("site report: another state answers with its own headroom", another_region_answers)
    ctx.check("site report: a second identical call is fast and identical", a_second_identical_call_is_cached)
