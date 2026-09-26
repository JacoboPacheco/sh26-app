"""Smoke checks for costs (owned by the cost track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
These endpoints store nothing. One AI call per run (the rest are the deterministic route)."""

import math

KEYS = ["blackout", "upgrades", "power_bill", "who_pays"]


def register(ctx):
    hero = ctx.expected["hero"]
    case = {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["mw"]}
    calm = {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["calm_mw"]}
    got = {}

    def lines(r):
        assert [ln["key"] for ln in r["lines"]] == KEYS, [ln["key"] for ln in r["lines"]]
        for ln in r["lines"]:
            assert math.isfinite(ln["low"]) and math.isfinite(ln["high"]) and 0 <= ln["low"] <= ln["high"], ln
            assert ln["formula"] and ln["assumption"] and ln["sources"], f"{ln['key']} is missing its formula, assumption or sources"
            assert all(s["name"] and s["url"].startswith("https://") for s in ln["sources"]), ln["sources"]
        return {ln["key"]: ln for ln in r["lines"]}

    def hero_matches_the_engine():
        r = ctx.request("POST", "/api/cost", {**case, "hours_out": 6})
        by = lines(r)
        got["hero"] = r
        assert r["synthetic"] is True and r["region"] == "FL", r["region"]
        # the blackout is priced on the same cascade the map plays
        c = ctx.request("POST", "/api/grid/cascade", case)
        assert abs(r["lost_mw"] - c["lost_mw"]) < 0.2 and r["people"] == c["people"], (r["lost_mw"], c["lost_mw"], r["people"], c["people"])
        b = by["blackout"]
        assert r["lost_mw"] > 0 and b["low"] > 0, "the hero case should black customers out"
        assert abs(b["mwh"] - r["lost_mw"] * 6) < 1, (b["mwh"], r["lost_mw"])
        assert abs(b["low"] - b["mwh"] * b["voll_low"]) <= 0.01 * b["low"] + 1000, (b["low"], b["mwh"], b["voll_low"])
        # the upgrades are the Fix it search's
        f = ctx.request("POST", "/api/fix", case)
        up = by["upgrades"]
        assert up["count"] == len(f["upgrades"]) and up["low"] > 0, (up["count"], len(f["upgrades"]))
        assert abs(up["added_mva"] - f["added_mva"]) < 1, (up["added_mva"], f["added_mva"])
        # the bill: MW x 8,760 h x 50 % x price
        bill = by["power_bill"]
        assert abs(bill["low"] - hero["mw"] * 8760 * 0.5 * bill["price_cents_kwh"] * 10) < 1, bill
        assert bill["high"] > bill["low"]
        tot = r["total_one_time"]
        assert tot["low"] == b["low"] + up["low"] and tot["high"] == b["high"] + up["high"], tot
        assert r["insights"], "expected at least one plain-language comparison"

    def calm_case_costs_nothing():
        by = lines(ctx.request("POST", "/api/cost", calm))
        assert by["blackout"]["high"] == 0 and by["upgrades"]["high"] == 0 and by["who_pays"]["high"] == 0, by
        assert by["power_bill"]["low"] > 0, "a campus still has a power bill"

    def hours_scale_the_blackout():
        r = ctx.request("POST", "/api/cost", {**case, "hours_out": 12})
        b6 = got["hero"]["lines"][0]
        b12 = r["lines"][0]
        assert abs(b12["mwh"] - 2 * b6["mwh"]) < 1, (b6["mwh"], b12["mwh"])
        assert b12["low"] > b6["low"]

    def region_and_firm_are_honored():
        tx = ctx.request("POST", "/api/cost", {"region": "TX", "lat": 32.45, "lon": -99.73, "mw": 800})
        assert tx["region"] == "TX" and tx["lines"][2]["price_cents_kwh"] == 6.12, (tx["region"], tx["lines"][2]["price_cents_kwh"])
        firm = ctx.request("POST", "/api/cost", {**case, "firm": True})
        c = ctx.request("POST", "/api/grid/cascade", {**case, "firm": True})
        assert firm["firm"] is True and abs(firm["lost_mw"] - c["lost_mw"]) < 0.2, (firm["lost_mw"], c["lost_mw"])

    def prevent_only_when_it_does():
        # the hero's upgrades end the blackout: the cascade re-run with them leaves nobody dark
        up = {ln["key"]: ln for ln in got["hero"]["lines"]}["upgrades"]
        assert up["prevents"] is True and up["lost_after_mw"] == 0, (up["prevents"], up["lost_after_mw"])
        fixed = {str(it["id"]): it["new_mva"] for it in up["items"]}
        c = ctx.request("POST", "/api/grid/cascade", {**case, "upgrades": fixed})
        assert c["lost_mw"] <= 0.5, c["lost_mw"]
        # a storm that cuts places off by itself: stronger lines can't reconnect them, so no "preventing it" claim
        storm = ctx.request("POST", "/api/cost", {"trip": [34335, 34340, 34334, 34341, 34336, 34129, 34350, 34339]})
        sup = {ln["key"]: ln for ln in storm["lines"]}["upgrades"]
        assert storm["lost_mw"] > 0.5 and sup["prevents"] is False and sup["lost_after_mw"] > 0.5, (storm["lost_mw"], sup["prevents"])
        assert not any(s.startswith("Preventing") for s in storm["insights"]), storm["insights"]

    def rejects_bad_input():
        ctx.request("POST", "/api/cost", {**case, "hours_out": 0}, expect=422)
        ctx.request("POST", "/api/cost", {**case, "hours_out": 1000}, expect=422)
        ctx.request("POST", "/api/cost", {}, expect=422)  # nothing to price
        ctx.request("POST", "/api/cost", {**case, "mw": 0}, expect=422)
        ctx.request("POST", "/api/cost", {**case, "region": "ZZ"}, expect=422)

    def ai_estimate_or_clear_fallback():
        r = ctx.request("POST", "/api/cost/ai", {**case, "hours_out": 6})
        assert isinstance(r["fallback"], bool), r.get("fallback")
        by = lines(r)
        for k in KEYS:
            a = r["ai"][k]
            assert math.isfinite(a["low"]) and math.isfinite(a["high"]) and 0 <= a["low"] <= a["high"], (k, a)
            assert isinstance(a["reasoning"], str) and a["reasoning"], (k, a)
            if r["fallback"]:
                assert (a["low"], a["high"]) == (by[k]["low"], by[k]["high"]) and a["fallback"] is True, (k, a)

    ctx.check("cost: the hero case prices the same cascade and Fix it the engine runs", hero_matches_the_engine)
    ctx.check("cost: a calm case has no blackout or upgrades, only a power bill", calm_case_costs_nothing)
    ctx.check("cost: twice the hours, twice the unserved energy", hours_scale_the_blackout)
    ctx.check("cost: region and firm service are honored", region_and_firm_are_honored)
    ctx.check("cost: upgrades are called 'prevent' only when the re-run cascade leaves nobody dark", prevent_only_when_it_does)
    ctx.check("cost: rejects bad hours, an empty case, a size of 0, an unknown region", rejects_bad_input)
    ctx.check("cost: the AI estimate answers, or falls back to the formula and says so", ai_estimate_or_clear_fallback)
