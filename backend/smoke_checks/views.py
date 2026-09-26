"""Smoke checks for the views (backend/views.py, owned by the views track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
Read-only: every call is a public GET over committed data, so it is safe against the deployed backend."""

import urllib.parse


def register(ctx):
    memo = {}

    def sites(query=""):
        return ctx.request("GET", "/api/views/datacenters" + ("?" + query if query else ""))

    def default_list():
        if "all" not in memo:
            memo["all"] = sites()
        return memo["all"]

    def test_list_shape():
        r = default_list()
        assert r["selected"] >= 1000 and r["total_sites"] >= r["selected"], (r["selected"], r["total_sites"])
        assert len(r["sites"]) == min(r["selected"], 3000), len(r["sites"])
        ids = [s["id"] for s in r["sites"]]
        assert len(set(ids)) == len(ids), "duplicate site ids"
        for s in r["sites"][:600]:
            for k in ("id", "name", "company", "status", "kind", "state", "lat", "lon", "mw", "origin"):
                assert k in s, f"{s['id']} lacks {k}"
            assert s["origin"] in ("curated", "compute-atlas", "epoch-ai"), s["origin"]
            assert s["lat"] is None or (24 <= s["lat"] <= 72 and -170 <= s["lon"] <= -60), (s["id"], s["lat"], s["lon"])
        sized = [s["mw"] for s in r["sites"] if s["mw"]]
        assert sized == sorted(sized, reverse=True), "sites are not biggest first"
        assert "SYNTHETIC" in r["frame"] and {c["origin"] for c in r["credits"]} == {"curated", "compute-atlas", "epoch-ai"}, r["credits"]
        assert any("CC BY" in c["text"] for c in r["credits"]), "the open sources must be credited"
        t = r["totals"]
        assert t["sites"] == r["selected"] and t["mw"] > 10000 and t["placed"] > 800, t
        assert sum(x["sites"] for x in t["by_state"]) == t["sites"], "state rows do not add up to the selection"
        assert abs(sum(x["mw"] for x in t["by_status"]) - t["mw"]) <= len(t["by_status"]), "status rows do not add up to the selection"
        assert all("canceled" not in x["status"] for x in t["by_status"]), "the default selection leaves out paused/canceled sites"
        assert "not an effect" in t["note"], "the share of model load must be framed as a scale, not a cause"

    def test_facets():
        f = default_list()["facets"]
        names = [c["company"] for c in f["companies"]]
        assert len(names) == len({n.lower() for n in names}), "duplicate company names in the facet"
        for want in ("Amazon", "Google", "Microsoft", "Meta"):
            assert want in names, f"{want} missing from the company facet"
        assert not [n for n in names if n.lower() in ("amazon web services", "aws", "meta platforms", "amazon data services")], "alias not folded"
        assert [c["mw"] for c in f["companies"]] == sorted((c["mw"] for c in f["companies"]), reverse=True)
        assert {s["status"] for s in f["statuses"]} <= {"operating", "under construction", "announced", "paused/canceled", "unknown"}
        assert any(s["state"] == "VA" for s in f["states"]) and any(k["kind"] == "crypto_mining" for k in f["kinds"]), f["kinds"]

    def test_filters():
        va = sites("state=VA")
        assert va["selected"] > 20 and all(s["state"] == "VA" for s in va["sites"]), va["selected"]
        two = sites("state=VA&state=TX")
        assert {s["state"] for s in two["sites"]} == {"VA", "TX"}, {s["state"] for s in two["sites"]}
        assert sites("state=TX,VA")["selected"] == two["selected"], "comma lists work like repeated parameters"
        g = sites("company=Google")
        assert 5 < g["selected"] < default_list()["selected"], g["selected"]
        for s in g["sites"][:3]:
            d = ctx.request("GET", "/api/views/datacenters/" + urllib.parse.quote(s["id"], safe=""))
            assert "Google" in d["parties"], (s["id"], d["parties"])
        # a chip stays on screen while it is selected: its own filter is ignored when the facets are counted
        assert any(c["company"] == "Microsoft" for c in g["facets"]["companies"]), "company facet dropped the other companies"
        big = sites("min_mw=1000")
        assert big["selected"] > 0 and all((s["mw"] or 0) >= 1000 for s in big["sites"]), big["selected"]
        q = sites("q=" + urllib.parse.quote("fort meade"))
        assert q["selected"] >= 1 and all("meade" in (s["name"] + s["city"]).lower() or s["state"] == "FL" for s in q["sites"]), q["selected"]
        every = sites("status=all")
        assert every["selected"] > default_list()["selected"], "status=all should add the paused/canceled sites"
        assert sites("status=" + urllib.parse.quote("paused/canceled"))["selected"] == every["selected"] - sites("status=operating,under%20construction,announced,unknown")["selected"]
        crypto = sites("kind=crypto_mining")
        assert crypto["selected"] > 20 and all(s["kind"] == "crypto_mining" for s in crypto["sites"])
        assert sites("state=ZZ")["selected"] == 0 and sites("q=zzzzqqqq")["selected"] == 0
        ctx.request("GET", "/api/views/datacenters?kind=bogus", expect=422)
        ctx.request("GET", "/api/views/datacenters?min_mw=-1", expect=422)

    def test_dedupe_and_tenants():
        # the same campus listed by two sources is one row: Stonebridge's Fort Meade campus (curated) is also in Compute Atlas
        fm = sites("state=FL&kind=all&status=all&q=" + urllib.parse.quote("fort meade"))
        assert fm["selected"] == 1, [s["name"] for s in fm["sites"]]
        d = ctx.request("GET", "/api/views/datacenters/" + fm["sites"][0]["id"])
        assert d["origin"] == "curated" and any(a["origin"] == "compute-atlas" for a in d["also"]), d["also"]
        # Epoch AI's rows never carry an AI lab's name as a tenant, and its guesses are never named
        ep = sites("origin=epoch-ai&kind=all&status=all&limit=200")
        text = " ".join(s["name"] + " " + s["company"] for s in ep["sites"]).lower()
        assert "anthropic" not in text and "openai" not in text, "an AI lab named in an Epoch AI row"
        goodnight = [s for s in ep["sites"] if s["name"] == "Goodnight"]
        assert not goodnight or goodnight[0]["company"] == "Undisclosed", goodnight

    def test_detail():
        r = default_list()
        pick = next(s for s in r["sites"] if s["origin"] == "compute-atlas" and s["mw"] and s["lat"] is not None)
        d = ctx.request("GET", "/api/views/datacenters/" + urllib.parse.quote(pick["id"], safe=""))
        for k in ("name", "operator", "status", "mw", "mw_basis", "sources", "origin_label", "state_name", "frame", "can_test"):
            assert k in d, f"detail lacks {k}"
        assert d["sources"] and all(s["url"].startswith(("http://", "https://")) for s in d["sources"]), d["sources"]
        assert "SYNTHETIC" in d["frame"], d["frame"]
        if d["has_model"]:
            assert d["can_test"] is True and d["model_load_mw"] > 0, d
            assert abs(d["share_of_model_load_pct"] - 100.0 * d["mw"] / d["model_load_mw"]) < 0.06, d
        ctx.request("GET", "/api/views/datacenters/no-such-site-anywhere", expect=404)

    def test_population():
        r = ctx.request("GET", "/api/views/population")
        rows = r["states"]
        assert len(rows) == 48 and len({x["code"] for x in rows}) == 48, len(rows)
        assert [x["population"] for x in rows] == sorted((x["population"] for x in rows), reverse=True), "not largest first"
        assert sum(x["population"] for x in rows) == r["total_population"]
        for x in rows:
            assert abs(x["population"] / x["model_load_mw"] - x["people_per_mw"]) <= 0.01 * x["people_per_mw"] + 0.1, x["code"]
        regions = {x["code"]: x for x in ctx.request("GET", "/api/regions")["regions"]}
        assert all(regions[x["code"]]["population"] == x["population"] and regions[x["code"]]["load_mw"] == x["model_load_mw"] for x in rows), "disagrees with /api/regions"
        assert "Census" in r["source"] and "ratio" in r["note"].lower(), (r["source"], r["note"])
        assert sum(x["dc_mw"] for x in rows) > 10000, "no data-center MW attached to the states"

    def test_energy():
        r = ctx.request("GET", "/api/views/energy")
        regions = {x["code"]: x for x in ctx.request("GET", "/api/regions")["regions"]}
        assert len(r["states"]) == 48, len(r["states"])
        for s in r["states"]:
            assert abs(sum(s["by_fuel"].values()) - s["capacity_mw"]) <= len(s["by_fuel"]), (s["code"], s["capacity_mw"], sum(s["by_fuel"].values()))
            assert abs(s["capacity_mw"] - regions[s["code"]]["gen_mw"]) <= 2, (s["code"], s["capacity_mw"], regions[s["code"]]["gen_mw"])
            assert s["load_mw"] == regions[s["code"]]["load_mw"], s["code"]
        n = r["national"]
        assert abs(sum(f["share_pct"] for f in n["by_fuel"]) - 100) < 0.6, "the national mix does not add to 100 %"
        assert abs(sum(f["capacity_mw"] for f in n["by_fuel"]) - n["capacity_mw"]) <= len(n["by_fuel"])
        fuels = {f["fuel"] for f in n["by_fuel"]}
        assert {"gas", "coal", "nuclear", "wind", "solar", "hydro"} <= fuels, fuels
        assert "SYNTHETIC" in r["note"], "the energy view must say the model is synthetic"

    ctx.check("views: /api/views/datacenters has sorted sites, credits, totals that add up, the scale framing", test_list_shape)
    ctx.check("views: company, status, state and kind facets (aliases folded, biggest first)", test_facets)
    ctx.check("views: state, company, size, text, status and kind filters narrow the list", test_filters)
    ctx.check("views: one campus listed by two sources is one row; no AI lab is named as a tenant", test_dedupe_and_tenants)
    ctx.check("views: a site's card carries sources, framing and its share of the model's load", test_detail)
    ctx.check("views: /api/views/population agrees with /api/regions", test_population)
    ctx.check("views: /api/views/energy's fuels add up to each model's generation", test_energy)
