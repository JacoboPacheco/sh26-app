"""Smoke checks for "Who goes dark first?" (backend/service_rules.py): the same case under three service rules.
Loaded by smoke_test.py. Read-only: every call is a public what-if, cascade or cost, nothing is created, so it is
safe against the deployed backend.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json)."""

import math

PATH = "/api/grid/service-rules"


def register(ctx):
    memo = {}

    def hero_body():
        h = (ctx.expected or {}).get("hero") or {"lat": 26.64, "lon": -81.87, "mw": 1500}
        return {"lat": h["lat"], "lon": h["lon"], "mw": h["mw"]}

    def get(key, method, path, body):
        if key not in memo:
            memo[key] = ctx.request(method, path, body)
        return memo[key]

    def rules():
        r = get("rules", "POST", PATH, hero_body())
        return r, {x["key"]: x for x in r["rules"]}

    def trips(casc):
        return sum(len(s["tripped"]) for s in casc["steps"] if s["action"] == "trip")

    def test_shape():
        r, by = rules()
        assert r["synthetic"] is True and r["estimate"] is True, "not labeled synthetic estimates"
        assert [x["key"] for x in r["rules"]] == ["flexible", "firm", "step_down"], f"rules {[x['key'] for x in r['rules']]}"
        for x in r["rules"]:
            for k in ("label", "people_dark", "people_hit", "people_out", "campus_served_mw", "lines_tripped", "outage_hours", "cost_high", "end"):
                assert k in x, f"{x['key']} lacks {k}"
            assert x["cost_low"] <= x["cost_high"], f"{x['key']}: cost low {x['cost_low']} > high {x['cost_high']}"
        assert r["sources"] and all(s.get("url", "").startswith("https://") for s in r["sources"]), "sources without links"
        assert r["lens"]["people"] == max(0, by["firm"]["people_dark"] - by["step_down"]["people_dark"]), "lens != firm - step down"
        assert r["storm"] is False, "the hero has no storm"
        for x in r["rules"]:  # "cut to keep it on" is a part of the people dark, and only the firm rule cuts anyone
            assert 0 <= x["people_cut"] <= x["people_dark"], f"{x['key']}: cut {x['people_cut']:,} > dark {x['people_dark']:,}"
            if x["key"] != "firm":
                assert x["people_cut"] == 0, f"{x['key']} cuts {x['people_cut']:,} people on purpose"

    def test_nobody_planned_is_the_cascade():
        _, by = rules()
        c = get("flex", "POST", "/api/grid/cascade", hero_body())
        f = by["flexible"]
        assert f["people_hit"] == c["people_hit"], f"people hit {f['people_hit']:,} vs the cascade's toll {c['people_hit']:,}"
        assert f["people_dark"] == (c["people_zone"] if c["lost_mw"] > 0.5 else 0), f"people dark {f['people_dark']:,} vs {c['people_zone']:,}"
        assert f["lines_tripped"] == trips(c), f"{f['lines_tripped']} trips vs the cascade's {trips(c)}"
        assert f["campus_cut_off"] == c["site_cut_off"], "campus cut off disagrees with the cascade"
        assert f["people_dark"] > 0, "the hero should black out under 'nobody planned'"

    def test_keep_on_is_the_firm_cascade():
        _, by = rules()
        c = get("firm", "POST", "/api/grid/cascade", {**hero_body(), "firm": True})
        f = by["firm"]
        assert c["firm"] is True, "firm cascade not reported as firm"
        assert f["people_hit"] == c["people_hit"], f"people hit {f['people_hit']:,} vs firm cascade {c['people_hit']:,}"
        assert f["people_dark"] == (c["people_zone"] if c["lost_mw"] > 0.5 else 0), f"people dark {f['people_dark']:,} vs {c['people_zone']:,}"
        assert f["firm_held"] == c["firm_held"] and abs(f["shed_mw"] - c["shed_mw"]) < 0.05, "firm held / shed disagree"
        if c["firm_held"]:
            assert f["campus_served_mw"] == hero_body()["mw"], f"kept on but serves {f['campus_served_mw']} MW"
        # the hero: everything lost was shed on purpose, so everyone dark was cut to keep the campus on
        if c["lost_mw"] > 0.5 and abs(c["lost_mw"] - c["shed_mw"]) <= 0.5:
            assert f["people_cut"] == f["people_dark"], f"all shed, but cut {f['people_cut']:,} != dark {f['people_dark']:,}"

    def test_storm_victims_are_not_cut():
        # a storm's line knocked out first: its victims lose power under every rule, never "cut to keep it on"
        body = {**hero_body(), "trip": [36001]}
        r = ctx.request("POST", PATH, body)
        by = {x["key"]: x for x in r["rules"]}
        assert r["storm"] is True, "a case with lines knocked out first is not reported as a storm"
        f = by["firm"]
        assert 0 <= f["people_cut"] <= f["people_dark"], f"cut {f['people_cut']:,} > dark {f['people_dark']:,}"
        if f["lost_mw"] > f["shed_mw"] + 0.5:  # something besides the operator's cuts went dark
            assert f["people_cut"] < f["people_dark"], "the storm's victims counted as cut to keep the campus on"
        s = r["step_down"]
        if s["over_without"]:  # past its limits with the campus off: stepping down means switching off, said so
            assert s["level_mw"] == 0 and by["step_down"]["label"] == "The campus switches off", f"level {s['level_mw']}, {by['step_down']['label']!r}"
        else:
            assert by["step_down"]["label"] == "The campus steps down first"

    def test_step_down_is_the_room():
        r, by = rules()
        s = r["step_down"]
        w = get("whatif", "POST", "/api/grid/whatif", hero_body())
        room = float(w["headroom_mw"])  # the verdict's number (rounded to 0.1 MW)
        assert abs(s["level_mw"] - min(hero_body()["mw"], math.floor(room))) <= 1, f"level {s['level_mw']} MW vs site headroom {room} MW"
        assert 0 < s["share_pct"] < 100, f"share {s['share_pct']} %"
        low = {**hero_body(), "mw": s["level_mw"]}
        w2 = ctx.request("POST", "/api/grid/whatif", low)
        assert not w2["overloaded"], f"{len(w2['overloaded'])} line(s) over at {s['level_mw']} MW"
        c = ctx.request("POST", "/api/grid/cascade", low)
        assert trips(c) == 0 and c["people_hit"] == 0, f"the cascade at {s['level_mw']} MW trips {trips(c)} line(s), hits {c['people_hit']:,}"
        st = by["step_down"]
        assert st["people_dark"] == 0 and st["lines_tripped"] == 0 and st["campus_served_mw"] == s["level_mw"], "step down not calm"
        assert s["verified"] == {"overloaded": 0, "lines_tripped": 0, "people_dark": 0}, f"verified {s['verified']}"

    def test_cost_matches_the_app():
        _, by = rules()
        cost = get("cost", "POST", "/api/cost", hero_body())
        h = cost["headline"]
        f = by["flexible"]
        assert h["kind"] == "blackout", f"hero priced as {h['kind']}"
        assert abs(f["outage_hours"] - h["outage_hours"]) < 0.05, f"outage {f['outage_hours']} h vs cost panel {h['outage_hours']} h"
        assert abs(f["cost_high"] - h["cost_high"]) <= max(1, 1e-6 * h["cost_high"]), f"cost {f['cost_high']:,} vs cost panel {h['cost_high']:,}"

    def test_several_campuses():
        body = {**hero_body(), "sites": [{"lat": 28.5384, "lon": -81.3789, "mw": 1000}]}
        r = ctx.request("POST", PATH, body)
        assert len(r["sites"]) == 2, f"{len(r['sites'])} sites"
        lv = [x["step_down_mw"] for x in r["sites"]]
        assert all(0 <= a <= x["mw"] for a, x in zip(lv, r["sites"])), f"levels {lv}"
        assert abs(sum(lv) - r["step_down"]["level_mw"]) < 0.5, "level != sum of the sites' levels"
        low = {"lat": body["lat"], "lon": body["lon"], "mw": lv[0], "sites": [{**body["sites"][0], "mw": lv[1]}]}
        if lv[0] >= 1 and lv[1] >= 1:
            w = ctx.request("POST", "/api/grid/whatif", low)
            assert not w["overloaded"], f"{len(w['overloaded'])} line(s) over with both campuses stepped down"

    def test_bad_bodies():
        ctx.request("POST", PATH, {"lat": 26.64, "lon": -81.87, "mw": "lots"}, expect=422)
        ctx.request("POST", PATH, {"load_factor": 1.0}, expect=422)  # no campus: nothing to rule on
        ctx.request("POST", PATH, {"lat": 26.64, "lon": -81.87, "mw": 0}, expect=422)
        ctx.request("POST", PATH, {"lat": 26.64, "lon": -81.87, "mw": 500, "region": "ZZ"}, expect=422)

    ctx.check("service rules: three rules, sources, the lens", test_shape)
    ctx.check("service rules: nobody planned = the cascade route's toll", test_nobody_planned_is_the_cascade)
    ctx.check("service rules: keep the campus on = the firm cascade", test_keep_on_is_the_firm_cascade)
    ctx.check("service rules: step down = the site's room, nothing trips there", test_step_down_is_the_room)
    ctx.check("service rules: a storm's victims are not 'cut to keep it on'", test_storm_victims_are_not_cut)
    ctx.check("service rules: outage and cost priced like the cost panel", test_cost_matches_the_app)
    ctx.check("service rules: several campuses each step down", test_several_campuses)
    ctx.check("service rules: 422 on a bad body", test_bad_bodies)
