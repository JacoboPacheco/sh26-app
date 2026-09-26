"""Smoke checks for the fixit feature (owned by its track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
Create nothing another user would see; clean up what you create. (These endpoints store nothing.)"""


def register(ctx):
    hero = ctx.expected["hero"]
    case = {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["mw"]}

    def fix_clears_hero():
        before = ctx.request("POST", "/api/grid/whatif", case)
        assert before["overloaded"], "the hero case should start with overloads"
        fix = ctx.request("POST", "/api/fix", case)
        assert fix["calm"] is True and fix["remaining_count"] == 0, f"fix did not clear the hero case: {fix['remaining']}"
        assert len(fix["upgrades"]) >= 1, "expected at least one upgrade"
        assert fix["added_mva"] > 0
        assert fix["over_before"] == len(before["overloaded"])
        for u in fix["upgrades"]:
            assert u["new_mva"] > u["old_mva"], u
            assert u["pct_after"] <= 90, f"an upgraded branch is still hot: {u}"
        after = ctx.request("POST", "/api/grid/whatif", {**case, "upgrades": fix["apply"]})
        assert after["overloaded"] == [], f"whatif with the fix still overloads: {after['overloaded'][:3]}"
        cascade = ctx.request("POST", "/api/grid/cascade", {**case, "upgrades": fix["apply"]})
        assert cascade["total_steps"] == 0 and cascade["homes"] == 0, "the fixed case should not cascade"

    def fix_calm_case_is_empty():
        fix = ctx.request("POST", "/api/fix", {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["calm_mw"]})
        assert fix["calm"] is True and fix["upgrades"] == [] and fix["rounds"] == 0, fix

    def fix_too_big_says_why():
        # four 2 GW campuses: more overloads than the search's 60 rounds can clear
        big = {
            "lat": 28.54,
            "lon": -81.38,
            "mw": 2000,
            "sites": [{"lat": 25.77, "lon": -80.19, "mw": 2000}, {"lat": 27.95, "lon": -82.46, "mw": 2000}, {"lat": 30.33, "lon": -81.66, "mw": 2000}],
        }
        fix = ctx.request("POST", "/api/fix", big)
        assert fix["calm"] is False and fix["remaining_count"] > 0, fix["remaining_count"]
        assert fix["stopped_at_limit"] is True and len(fix["upgrades"]) <= fix["max_rounds"], len(fix["upgrades"])
        assert len(fix["apply"]) <= 200, "apply must stay postable (grid.MAX_UPGRADES)"
        ctx.request("POST", "/api/grid/whatif", {**big, "upgrades": fix["apply"]})  # accepted as-is

    def fix_rejects_bad_case():
        ctx.request("POST", "/api/fix", {**case, "mw": 0}, expect=422)
        ctx.request("POST", "/api/fix", {**case, "upgrades": {"-1": 100}}, expect=422)

    def best_sites_500():
        r = ctx.request("GET", "/api/best-sites?mw=500&load_factor=1.0&limit=10")
        sites = r["sites"]
        assert 1 <= len(sites) <= 10, f"expected 1-10 sites, got {len(sites)}"
        towns = [s["town"] for s in sites]
        assert len(set(towns)) == len(towns), f"one site per town: {towns}"
        for s in sites:
            assert s["headroom_mw"] >= 500, s
            for k in ("sub", "name", "lat", "lon", "kv", "rank"):
                assert k in s, f"missing {k}: {s}"
        # the top site really takes 500 MW: a what-if there shows no line over limit
        top = sites[0]
        w = ctx.request("POST", "/api/grid/whatif", {"lat": top["lat"], "lon": top["lon"], "mw": 500})
        assert w["sub"] == top["sub"], "placing at a best site should connect to that substation"
        assert w["overloaded"] == [], f"best site {top['name']} overloads at 500 MW"

    def best_sites_too_big():
        r = ctx.request("GET", "/api/best-sites?mw=5000&limit=5")
        assert r["sites"] == [] and 1 <= len(r["closest"]) <= 5, r
        assert all("fix_mva" in s for s in r["closest"])

    def best_sites_agree_with_a_solve():
        # the hero size: either a listed site really takes it, or every "closest" town really needs upgrades
        mw = hero["mw"]
        r = ctx.request("GET", f"/api/best-sites?mw={mw}&limit=5")
        if r["sites"]:
            top = r["sites"][0]
            assert top["headroom_mw"] >= mw, top
            w = ctx.request("POST", "/api/grid/whatif", {"lat": top["lat"], "lon": top["lon"], "mw": mw})
            assert w["overloaded"] == [], f"best site {top['name']} overloads at {mw} MW"
        else:
            assert r["closest"], "nothing fits, so the closest towns should be listed"
            for s in r["closest"]:
                assert s["fix_mva"] > 0 or not s["fix_calm"], f"{s['name']} needs no upgrade, so it fits: {s}"

    def best_sites_scored():
        # the screening table: every row's facts come from one solve, the score follows its stated formula, ranked by it
        r = ctx.request("GET", "/api/best-sites?mw=1000&load_factor=1.0&limit=10")
        assert "Score" in r["score_formula"] and r["hot_pct"] == 90, r.get("score_formula")
        alone = r["strain_alone"]
        assert 0 < alone["peak_pct"] and alone["hot"] >= 0 and alone["over"] >= 0, alone
        assert r["screened"] >= len(r["sites"]) >= 1, (r["screened"], len(r["sites"]))
        sites = r["sites"]
        for s in sites:
            room = min(1.0, max(0.0, (s["headroom_mw"] - 1000) / 1000))
            cool = min(1.0, max(0.0, (100 - s["busiest_pct"]) / 40))
            assert 0 <= s["score"] <= 100 and abs(s["score"] - (50 * room + 50 * cool)) <= 1, s
            assert s["busiest_pct"] <= 100 and s["strain_after"]["over"] == 0, s
            assert s["strain_after"]["peak_pct"] >= s["busiest_pct"] - 0.05, s
        keys = [(-s["score"], -round(s["headroom_mw"]), s["busiest_pct"]) for s in sites]
        assert keys == sorted(keys), f"not ranked by score: {[s['score'] for s in sites]}"
        assert [s["rank"] for s in sites] == list(range(1, len(sites) + 1))
        # the strain and the limiting element agree with the what-if at the top two sites (the same solve:
        # one campus on the intact grid); loading_pct is aligned with /api/grid's branches
        index = {b["id"]: k for k, b in enumerate(ctx.request("GET", "/api/grid")["branches"])}
        for s in sites[:2]:
            w = ctx.request("POST", "/api/grid/whatif", {"lat": s["lat"], "lon": s["lon"], "mw": 1000})
            pct = w["loading_pct"]
            assert abs(max(pct) - s["strain_after"]["peak_pct"]) <= 0.15, (s["town"], max(pct), s["strain_after"])
            hot = sum(1 for p in pct if p >= 90)
            assert abs(hot - s["strain_after"]["hot"]) <= 1, (s["town"], hot, s["strain_after"])
            lim = s["limiting"]
            assert lim and lim["label"].startswith("the ") and lim["id"] in index, (s["town"], lim)
            assert abs(pct[index[lim["id"]]] - s["busiest_pct"]) <= 0.15, (s["town"], lim, pct[index[lim["id"]]], s["busiest_pct"])
        sh = r["shared_limit"]
        if sh:
            assert 2 <= sh["count"] <= sh["of"] == len(sites) and sh["id"] == sites[0]["limiting"]["id"], sh

    def best_sites_already_over():
        # South Carolina's model already runs a line over its limit at 4 PM with no campus: the listed sites add no
        # NEW overload (every line over with the campus was over without it), which is what the panel says there
        r = ctx.request("GET", "/api/best-sites?mw=500&region=SC&limit=10")
        alone = r["strain_alone"]
        assert alone["over"] >= 1, alone
        assert r["sites"], "no SC site listed at 500 MW"
        for s in r["sites"]:
            assert s["busiest_pct"] <= 100 and s["strain_after"]["over"] <= alone["over"], (s["town"], s["busiest_pct"], s["strain_after"], alone)

    def best_sites_closest_unscored():
        r = ctx.request("GET", "/api/best-sites?mw=5000&limit=5")
        for s in r["closest"]:
            assert s["score"] is None and s["busiest_pct"] > 100 and s["strain_after"]["over"] >= 1, s

    def best_sites_rejects():
        ctx.request("GET", "/api/best-sites?mw=0", expect=422)
        ctx.request("GET", "/api/best-sites?mw=500&limit=0", expect=422)
        ctx.request("GET", "/api/best-sites?mw=500&load_factor=9", expect=422)
        ctx.request("GET", "/api/best-sites?mw=500&region=ZZ", expect=422)

    ctx.check("fix: the hero case is cleared and the what-if agrees", fix_clears_hero)
    ctx.check("fix: a calm case needs no upgrades", fix_calm_case_is_empty)
    ctx.check("fix: a case too big for re-rating says why, and its partial fix still posts", fix_too_big_says_why)
    ctx.check("fix: bad cases are rejected (422)", fix_rejects_bad_case)
    ctx.check("best sites: 500 MW returns up to 10 towns that take it", best_sites_500)
    ctx.check("best sites: nowhere fits 5,000 MW, closest listed", best_sites_too_big)
    ctx.check("best sites: 1,500 MW agrees with a full solve", best_sites_agree_with_a_solve)
    ctx.check("best sites: a ranked screening table, scored by its stated formula, strain agrees with the what-if", best_sites_scored)
    ctx.check("best sites: a state already over with no campus lists sites that add no new overload", best_sites_already_over)
    ctx.check("best sites: towns that need upgrades carry no score", best_sites_closest_unscored)
    ctx.check("best sites: bad size, limit, level rejected (422)", best_sites_rejects)
