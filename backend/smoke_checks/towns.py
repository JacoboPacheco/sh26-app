"""Smoke checks for areas ("is my area at risk?", backend/towns.py). Loaded by smoke_test.py.
Read-only: every call is a public GET or a public what-if / cascade, nothing is created, so it is
safe against the deployed backend.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json)."""


def register(ctx):
    memo = {}

    def get(path):
        if path not in memo:
            memo[path] = ctx.request("GET", path)
        return memo[path]

    def test_list():
        areas = get("/api/areas?region=FL")
        assert isinstance(areas, list) and len(areas) > 100, f"{len(areas)} Florida areas"
        by_name = {a["name"]: a for a in areas}
        for name in ("Naples", "Fort Myers", "Miami"):
            assert name in by_name, f"no area {name!r}"
        a = by_name["Naples"]
        for k in ("slug", "name", "subs", "sub_ids", "people", "load_mw", "lat", "lon"):
            assert k in a, f"area lacks {k}"
        assert a["subs"] == len(a["sub_ids"]) > 0 and a["people"] > 0, f"Naples: {a['subs']} subs, {a['people']} people"
        slugs = [x["slug"] for x in areas]
        assert len(set(slugs)) == len(slugs), "duplicate area slugs"
        # every substation is in exactly one area, and the people add up to the state (estimates)
        g = get("/api/grid")
        ids = [i for x in areas for i in x["sub_ids"]]
        assert sorted(ids) == sorted(s["id"] for s in g["subs"]), "areas don't cover every substation exactly once"
        total, pop = sum(x["people"] for x in areas), g["meta"]["population"]
        assert abs(total - pop) <= max(0.002 * pop, len(areas)), f"areas' people {total:,} vs population {pop:,}"
        people = [x["people"] for x in areas]
        assert people == sorted(people, reverse=True), "areas not sorted by people"

    def test_profile_matches_engine():
        p = get("/api/areas/FL/naples")
        assert p["name"] == "Naples" and p["synthetic"] is True, "profile header"
        assert p["substations"] and p["subs"] >= len(p["substations"]), "no substations"
        e = p["exposure"]
        assert e["tested"] >= 10 and e["tested"] == len(e["cases"]), f"tested {e['tested']} / {len(e['cases'])} cases"
        assert e["hits"] == sum(1 for c in e["cases"] if c["hit"]), "hits != cases hit"
        assert any(c["kind"] == "catalog" for c in e["cases"]), "no catalog campus tested in Florida"
        assert e["heat_wave"]["load_factor"] == 1.04, "heat-wave case not at 1.04"
        for c in e["cases"]:
            assert 0 <= c["people"] <= c["total_people"], f"{c['id']}: area {c['people']} > total {c['total_people']}"
            assert c["hit"] == (c["people"] > 0), f"{c['id']}: hit {c['hit']} but {c['people']} people"
        # the 1 GW test in Naples replays exactly in the workspace's cascade
        b = p["beyond"]
        assert b is not None, "Naples has room under 1 GW, so the 1 GW test should be there"
        c = ctx.request("POST", "/api/grid/cascade", b["case"])
        assert c["people"] == b["total_people"], f"cascade {c['people']:,} != area test {b['total_people']:,}"
        subs = {s["id"] for s in get("/api/grid")["subs"] if s["area"] == "Naples"}
        lost = sum(mw for sid, mw in c["affected"].items() if int(sid) in subs)
        assert abs(lost - b["lost_mw"]) <= 0.5, f"Naples lost {lost:.1f} MW in the cascade vs {b['lost_mw']} in the profile"
        want = lost * c["people_per_mw"]
        assert abs(b["people"] - want) <= max(0.01 * want, 50), f"Naples people {b['people']:,} vs {want:,.0f}"

    def test_room_is_checked():
        r = get("/api/areas/FL/naples")["room"]
        assert r and r["mw"] >= 1, "no room at Naples"
        w = ctx.request("POST", "/api/grid/whatif", {"lat": r["lat"], "lon": r["lon"], "mw": r["mw"]})
        assert w["sub"] == r["sub"], f"the room's point snaps to {w['sub']}, not {r['sub']}"
        base = {o["id"] for o in ctx.request("POST", "/api/grid/whatif", {"lat": r["lat"], "lon": r["lon"], "mw": 1})["overloaded"]}
        new = [o for o in w["overloaded"] if o["id"] not in base]
        assert not new, f"{r['mw']} MW at {r['sub_name']} overloads {len(new)} line(s)"

    def test_exposure_summary():
        s = get("/api/areas/FL/exposure")
        assert s["tested"] == len(s["cases"]) >= 10, f"{s['tested']} tests"
        assert s["areas"] and all(a["hits"] >= 1 for a in s["areas"]), "exposure summary without hit areas"
        hits = [a["hits"] for a in s["areas"]]
        assert hits == sorted(hits, reverse=True), "exposure areas not ranked by hits"
        top = s["areas"][0]
        p = get(f"/api/areas/FL/{top['slug']}")
        assert p["exposure"]["hits"] == top["hits"], f"{top['name']}: summary {top['hits']} vs profile {p['exposure']['hits']}"

    def test_other_region_and_firm():
        areas = ctx.request("GET", "/api/areas?region=RI")
        assert areas and areas[0]["name"] == "Providence", f"Rhode Island's largest area: {areas[0]['name'] if areas else None}"
        p = ctx.request("GET", "/api/areas/RI/providence?firm=true&load_factor=1.04")
        assert p["region"] == "RI" and p["exposure"]["firm"] is True and p["exposure"]["load_factor"] == 1.04, "RI firm heat profile"
        assert all(c["case"]["firm"] is True for c in p["exposure"]["cases"]), "a firm profile's case isn't firm"

    def test_validation():
        ctx.request("GET", "/api/areas/FL/no-such-town", expect=404)
        ctx.request("GET", "/api/areas/FL/..%2F..", expect=404)
        ctx.request("GET", "/api/areas?region=ZZ", expect=422)
        ctx.request("GET", "/api/areas/ZZ/naples", expect=422)
        ctx.request("GET", "/api/areas/FL/naples?load_factor=1.3", expect=422)
        ctx.request("GET", "/api/areas/FL/exposure?load_factor=nan", expect=422)

    ctx.check("areas: /api/areas lists Florida's areas, covering every substation once, people add up", test_list)
    ctx.check("areas: Naples profile, its 1 GW test replays exactly in /api/grid/cascade", test_profile_matches_engine)
    ctx.check("areas: the room at Naples is checked (no new overload at that size)", test_room_is_checked)
    ctx.check("areas: the exposure summary ranks areas by tests, and agrees with the profile", test_exposure_summary)
    ctx.check("areas: another region (Rhode Island), firm, heat wave", test_other_region_and_firm)
    ctx.check("areas: unknown area 404, bad region / load level 422", test_validation)
