"""Smoke checks for the core engine (regions, people without power, firm vs flexible, area names).
Loaded by smoke_test.py. Read-only: every call is a public what-if or cascade, nothing is created, so
it is safe against the deployed backend.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json)."""


def register(ctx):
    memo = {}

    def hero(mw=None, firm=None):
        h = ctx.expected["hero"]
        body = {"lat": h["lat"], "lon": h["lon"], "mw": mw or h["mw"]}
        if firm is not None:
            body["firm"] = firm
        key = (body["mw"], firm)
        if key not in memo:
            memo[key] = ctx.request("POST", "/api/grid/cascade", body)
        return memo[key]

    def test_regions():
        r = ctx.request("GET", "/api/regions")
        regs = r["regions"]
        assert len(regs) == 48, f"{len(regs)} regions, expected the lower 48"
        bad = [x["code"] for x in regs if not x.get("valid")]
        assert not bad, f"regions not valid: {bad}"
        nopop = [x["code"] for x in regs if not (x.get("population") or 0) > 0 or not (x.get("people_per_mw") or 0) > 0]
        assert not nopop, f"regions without population / people_per_mw: {nopop}"
        assert r.get("population_source"), "no population_source"

    def test_hero_people():
        c = hero()
        assert c["people"] > 0, f"hero cascade: people={c['people']!r}"
        want = c["lost_mw"] * c["people_per_mw"]
        assert abs(c["people"] - want) <= max(0.01 * want, 50), f"people {c['people']:,} != lost {c['lost_mw']} MW x {c['people_per_mw']}/MW"
        assert c["population"] and c["people"] <= c["population"], "people above the state's population"
        last = c["steps"][-1]
        assert last["people"] == c["people"], f"last step people {last['people']:,} != final {c['people']:,}"
        assert all("people" in s and "action" in s for s in c["steps"]), "a step without people/action"
        assert c["firm"] is False and c["firm_held"] is None, "a case without firm should be flexible"

    def test_whatif_people_fields():
        h = ctx.expected["hero"]
        w = ctx.request("POST", "/api/grid/whatif", {"lat": h["lat"], "lon": h["lon"], "mw": h["calm_mw"]})
        for k in ("people", "people_per_mw", "population", "sub_area"):
            assert k in w, f"what-if lacks {k}"
        assert w["people"] == 0, f"calm what-if shows {w['people']} people without power"

    def test_texas():
        w = ctx.request("POST", "/api/grid/whatif", {"region": "TX", "lat": 32.45, "lon": -99.73, "mw": 1200})
        assert w["region"] == "TX", f"region {w['region']!r}"
        assert len(w["loading_pct"]) > 1000, "Texas what-if has too few lines"
        g = ctx.request("GET", "/api/grid?region=TX")
        assert len(g["branches"]) == len(w["loading_pct"]), "Texas what-if not aligned with /api/grid?region=TX"
        assert g["meta"]["region"] == "TX" and g["meta"]["people_per_mw"] > 0, "Texas grid meta lacks region / people_per_mw"

    def test_firm_vs_flexible():
        flex = hero(2000, False)
        firm = hero(2000, True)
        assert firm["firm"] is True and isinstance(firm["firm_held"], bool), "firm case not reported as firm"
        assert flex["firm"] is False, "flexible case reported as firm"
        assert firm["people"] >= flex["people"], f"firm {firm['people']:,} < flexible {flex['people']:,} at 2,000 MW"
        # the fix for the cliff: firm service makes size matter (800 MW sheds fewer people than 1,500)
        small, big = hero(800, True), hero(1500, True)
        assert small["firm_held"] and big["firm_held"], "firm service not held at 800 / 1,500 MW"
        assert 0 < small["people"] < big["people"], f"firm: 800 MW -> {small['people']:,}, 1,500 MW -> {big['people']:,}"
        assert any(s["action"] == "shed" for s in small["steps"]), "firm 800 MW has no shed step"

    def test_area_names():
        g = ctx.request("GET", "/api/grid")
        subs = g["subs"]
        nameless = [s["id"] for s in subs if not str(s.get("area") or "").strip()]
        assert not nameless, f"{len(nameless)} substations without an area name"
        assert any(s["area"] == "Fort Myers" for s in subs), "no substation in the area 'Fort Myers'"
        assert all(not s["area"][-1].isdigit() for s in subs), "an area name still ends in a number"

    ctx.check("core: /api/regions lists 48 valid regions with population", test_regions)
    ctx.check("core: hero cascade people > 0 and = lost MW x people_per_mw (estimate)", test_hero_people)
    ctx.check("core: what-if carries people, people_per_mw, population, sub_area", test_whatif_people_fields)
    ctx.check("core: a Texas what-if at Abilene 1,200 MW answers for region TX", test_texas)
    ctx.check("core: firm vs flexible at 2,000 MW, and firm size matters (800 < 1,500 MW)", test_firm_vs_flexible)
    ctx.check("core: every substation in /api/grid has an area name", test_area_names)
