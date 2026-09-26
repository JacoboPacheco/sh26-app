"""Smoke checks for Plant Down (owned by the plants track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).

Read-only: every call is a public list, trip, trace or ranking; nothing is stored, so it is safe
against the deployed backend. The ranking is computed in the background — polled here."""

import time

FUELS = {"nuclear", "coal", "gas", "oil", "hydro", "geothermal", "wind", "offshore wind", "solar", "other"}


def register(ctx):
    memo = {}

    def fl_plants():
        if "fl" not in memo:
            memo["fl"] = ctx.request("GET", "/api/plants?region=FL")
        return memo["fl"]

    def fl_ranking():
        if "rank" not in memo:
            deadline = time.time() + 90
            while True:
                r = ctx.request("GET", "/api/plants/ranking?region=FL")
                if r["status"] == "ready":
                    break
                assert r["status"] == "computing", f"ranking status {r['status']!r}"
                assert 0 <= r["done"] <= r["total"], f"progress {r['done']}/{r['total']}"
                assert time.time() < deadline, f"ranking still computing after 90 s ({r['done']}/{r['total']})"
                time.sleep(1.0)
            memo["rank"] = r
        return memo["rank"]

    def test_list():
        p = fl_plants()
        plants = p["plants"]
        assert len(plants) >= 50, f"only {len(plants)} Florida plants"
        assert p["synthetic"] is True and p["source"], "plants not labeled synthetic / no source"
        for x in plants:
            for k in ("id", "name", "bus", "sub", "sub_name", "lat", "lon", "fuel", "pmax", "pg", "output"):
                assert k in x, f"plant lacks {k}: {x}"
            assert x["fuel"] in FUELS, f"unknown fuel {x['fuel']!r}"
            assert 24.3 <= x["lat"] <= 31.1 and -87.7 <= x["lon"] <= -79.4, f"{x['name']} outside Florida"
            assert 0 <= x["output"] <= x["pmax"] + 0.5, f"{x['name']} produces {x['output']} of {x['pmax']} MW"
        assert "parts" not in plants[0], "internal parts leaked into the list"
        assert len({x["id"] for x in plants}) == len(plants), "duplicate plant ids"
        cap = sum(f["pmax"] for f in p["by_fuel"].values())
        assert abs(cap - p["total_pmax"]) <= 1, f"by_fuel {cap} != total {p['total_pmax']}"
        assert p["total_pmax"] > p["total_load"] > 0, "Florida's model should have more capacity than load"
        assert abs(p["total_output"] - p["total_load"] + p["net_import_mw"]) <= 0.01 * p["total_load"], (
            f"output {p['total_output']} doesn't balance load {p['total_load']} and ties {p['net_import_mw']}"
        )
        tx = ctx.request("GET", "/api/plants?region=TX")
        assert tx["region"] == "TX" and len(tx["plants"]) >= 50, "Texas plants missing"

    def test_ranking():
        r = fl_ranking()
        rank = r["ranking"]
        assert rank and len(rank) + len(r["unsolved"]) == r["total"], f"{len(rank)} + {len(r['unsolved'])} entries for {r['total']}"
        assert not r["unsolved"], f"Florida plants the engine couldn't settle: {[u['name'] for u in r['unsolved']]}"
        ppl = [e["people"] for e in rank]
        assert ppl == sorted(ppl, reverse=True), "ranking not sorted by people"
        assert ppl[0] > 0, "no plant in Florida puts anyone in the dark — expected Homestead to"
        assert any(v == 0 for v in ppl), "no harmless plants listed"
        assert r["baseline"]["people"] == 0, f"today's load alone darkens {r['baseline']['people']} people"
        for e in rank:
            assert e["plant"]["ids"] and e["outcome"] in ("settled", "islanded"), e

    def test_trip_top():
        top = fl_ranking()["ranking"][0]
        c = ctx.request("POST", "/api/plants/trip", {"region": "FL", "outages": top["plant"]["ids"]})
        assert c["people"] > 0, f"tripping {top['plant']['name']} left people={c['people']}"
        assert c["people"] == top["people"], f"trip says {c['people']:,}, ranking said {top['people']:,}"
        assert c["outcome"] in ("settled", "islanded") and c["total_steps"] <= 30, c["outcome"]
        assert abs(c["removed_mw"] - top["plant"]["pmax"]) <= 1, f"removed {c['removed_mw']} vs {top['plant']['pmax']}"
        assert {x["id"] for x in c["removed"]} == set(top["plant"]["ids"]), "removed list differs from the request"
        assert c["initial"]["overloaded"] >= 1, "the plant's loss overloads nothing, yet it cascades"
        assert c["baseline"]["people"] == 0 and c["added_people"] == c["people"], "baseline should be calm"
        g = fl_plants()
        assert len(c["final_loading_pct"]) > 1000, "cascade shape: final_loading_pct missing"
        people = [s["people"] for s in c["steps"]]
        assert people == sorted(people), f"people not monotone over the steps: {people}"
        assert g, "plants list unavailable"

    def test_trip_harmless():
        calm = next(e for e in fl_ranking()["ranking"] if e["people"] == 0)
        c = ctx.request("POST", "/api/plants/trip", {"region": "FL", "outages": calm["plant"]["ids"]})
        assert c["people"] == 0, f"{calm['plant']['name']} was ranked harmless but trips {c['people']:,} people"
        assert c["removed_mw"] > 0, "nothing removed"

    def test_retire_coal():
        coal = fl_plants()["by_fuel"].get("coal")
        c = ctx.request("POST", "/api/plants/trip", {"region": "FL", "retire_fuels": ["coal"]})
        assert c["retired_fuels"] == ["coal"], c["retired_fuels"]
        if coal:
            assert abs(c["removed_mw"] - coal["pmax"]) <= 1, f"removed {c['removed_mw']} MW, coal is {coal['pmax']} MW"
            assert all(x["fuel"] == "coal" for x in c["removed"]), "retired something that isn't coal"
        assert c["outcome"] in ("settled", "islanded") and "supply" in c, c.get("outcome")

    def test_validation():
        ctx.request("POST", "/api/plants/trip", {"region": "FL", "outages": [987654321]}, expect=422)
        ctx.request("POST", "/api/plants/trip", {"region": "FL", "outages": ["abc"]}, expect=422)
        ctx.request("POST", "/api/plants/trip", {"region": "FL", "retire_fuels": ["unobtanium"]}, expect=422)
        ctx.request("POST", "/api/plants/trip", {"region": "ZZ", "outages": []}, expect=422)
        ctx.request("POST", "/api/plants/trip", {"region": "FL", "load_factor": 3.0, "outages": []}, expect=422)
        ctx.request("POST", "/api/plants/trip", {"region": "FL", "outages": list(range(501))}, expect=422)  # too many
        ctx.request("GET", f"/api/plants/FL/{fl_plants()['plants'][0]['id']}/trace?load_factor=nan", expect=422)
        ctx.request("GET", "/api/plants/FL/987654321/trace", expect=422)
        ctx.request("GET", "/api/plants/ZZ/1/trace", expect=422)
        ctx.request("GET", "/api/plants?region=ZZ", expect=422)
        ctx.request("GET", "/api/plants/ranking?region=FL&lat=27&lon=-81", expect=422)  # no mw
        ctx.request("GET", "/api/plants/ranking?region=FL&lat=40.7&lon=-74&mw=500", expect=422)  # New York

    def test_trace():
        biggest = max(fl_plants()["plants"], key=lambda x: x["output"])
        t = ctx.request("GET", f"/api/plants/FL/{biggest['id']}/trace")
        assert t["method"].startswith("proportional sharing"), t["method"]
        assert t["serves"], f"{biggest['name']} serves no area"
        assert all(0 <= s["share"] <= 1 and s["mw"] > 0 for s in t["serves"]), "a share outside 0..1"
        total = t["served_mw"] + t["exported_mw"]
        assert abs(total - t["output_mw"]) <= max(1.0, 0.01 * t["output_mw"]), f"delivered {total} MW of {t['output_mw']} MW"
        listed = sum(s["mw"] for s in t["serves"]) + t["rest"]["mw"]
        assert abs(listed - t["served_mw"]) <= max(1.0, 0.01 * t["served_mw"]), f"areas add to {listed}, served {t['served_mw']}"
        assert t["check"]["max_bus_error"] < 1e-6, f"per-bus shares don't sum to 1: {t['check']}"
        # the same plant on a case: a campus next to it
        case = {"lat": biggest["lat"], "lon": biggest["lon"], "mw": 300}
        tc = ctx.request("POST", f"/api/plants/FL/{biggest['id']}/trace", case)
        assert tc["state"] == "case" and tc["output_mw"] >= t["output_mw"] - 1, "a campus next door should not lower its output"
        out = ctx.request("POST", f"/api/plants/FL/{biggest['id']}/trace", {"outages": [biggest["id"]]})
        assert out["out"] is True and out["output_mw"] == 0, "a tripped plant still traces output"

    ctx.check("plants: Florida has plants with fuels, capacity and output that balance the load; Texas too", test_list)
    ctx.check("plants: ranking computes in the background, sorted by people, harmless plants listed with 0", test_ranking)
    ctx.check("plants: tripping the top-ranked Florida plant cascades with people > 0 (matches its rank)", test_trip_top)
    ctx.check("plants: tripping a harmless plant leaves 0 people without power", test_trip_harmless)
    ctx.check("plants: retiring all coal removes exactly the coal plants", test_retire_coal)
    ctx.check("plants: unknown plant, fuel, region, load level or half a site is a 422", test_validation)
    ctx.check("plants: tracing delivers the plant's whole output, shares in 0..1, per-bus shares sum to 1", test_trace)
