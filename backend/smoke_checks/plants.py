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

    def test_restore_after_trip():
        """'Run it again with the plant back': the same case with every plant running, replayable, and
        the comparison against the trip (cached by the trip, so the press itself runs no cascade)."""
        top = fl_ranking()["ranking"][0]
        body = {"region": "FL", "outages": top["plant"]["ids"]}
        out = ctx.request("POST", "/api/plants/trip", body)
        back = ctx.request("POST", "/api/plants/restore", body)
        assert back["plants_back"] is True and back["region"] == "FL", back.get("plants_back")
        assert {p["id"] for p in back["restored"]} == set(top["plant"]["ids"]), "restored list differs from the request"
        assert abs(back["restored_mw"] - top["plant"]["pmax"]) <= 1, (back["restored_mw"], top["plant"]["pmax"])
        assert back["restored_output_mw"] > 0, "the plant made nothing in the case it was taken out of"
        # the replay is a cascade payload: the same case at today's load holds with every plant running
        assert back["people"] == 0 and back["total_steps"] == 0 and len(back["final_loading_pct"]) > 1000, (back["people"], back["total_steps"])
        cmp_ = back["compare"]
        assert cmp_["out"]["people"] == out["people"] > 0, (cmp_["out"], out["people"])
        assert cmp_["back"]["people"] == back["people"] and cmp_["held_people"] == out["people"], cmp_
        assert cmp_["held_mw"] > 0 and cmp_["held_areas"], "no area named as held up by the plant"
        a0 = cmp_["held_areas"][0]
        assert a0["area"] and a0["people"] > 0 and a0["mw"] > 0 and 24 <= a0["lat"] <= 31.1, a0
        held_mw = sum(cmp_["held_subs"].values())
        assert abs(held_mw - cmp_["held_mw"]) <= max(2.0, 0.01 * cmp_["held_mw"]), (held_mw, cmp_["held_mw"])
        assert set(cmp_["cached"]) == {"back", "out"}, cmp_["cached"]
        assert back["plant"]["label"].startswith("the ") and back["plant"]["count"] == len(top["plant"]["ids"]), back["plant"]
        # the trip echoes its plant fields, so a caller can rebuild the case (the briefing takes them)
        assert out["outages"] == sorted(top["plant"]["ids"]) and out["retire_fuels"] == [], (out["outages"], out["retire_fuels"])

    def test_restore_cold_with_campus():
        """No trip before it: the restore runs both cases itself, and each matches its own endpoint."""
        top = fl_ranking()["ranking"][0]
        case = {"region": "FL", "lat": 28.54, "lon": -81.38, "mw": 300, "load_factor": 0.82}  # a case no other check trips
        body = {**case, "outages": top["plant"]["ids"]}
        back = ctx.request("POST", "/api/plants/restore", body)
        plain = ctx.request("POST", "/api/grid/cascade", case)
        assert back["people"] == plain["people"] and back["total_steps"] == plain["total_steps"], "the plants-back replay differs from the plain cascade"
        assert back["compare"]["back"]["people"] == plain["people"], back["compare"]["back"]
        out = ctx.request("POST", "/api/plants/trip", body)
        assert back["compare"]["out"]["people"] == out["people"], (back["compare"]["out"], out["people"])
        assert back["compare"]["held_people"] == max(0, out["people"] - plain["people"]), back["compare"]

    def test_restore_validation():
        ctx.request("POST", "/api/plants/restore", {"region": "FL"}, expect=422)  # nothing is out
        ctx.request("POST", "/api/plants/restore", {"region": "FL", "outages": [987654321]}, expect=422)
        ctx.request("POST", "/api/plants/restore", {"region": "FL", "retire_fuels": ["unobtanium"]}, expect=422)

    def test_briefing_names_the_plant():
        """The incident briefing of a plant outage says a power plant went offline, by its name in the
        model, with its MW and that the rest of the grid had to pick up its output — not a storm, not a
        data center."""
        top = fl_ranking()["ranking"][0]
        ids = top["plant"]["ids"]
        by_id = {p["id"]: p for p in fl_plants()["plants"]}
        big = max((by_id[i] for i in ids), key=lambda p: p["pmax"])
        name = " ".join(w.capitalize() for w in big["sub_name"].split()) + " " + big["fuel"]  # "Homestead 20 nuclear"
        r = ctx.request("POST", "/api/briefing", {"region": "FL", "outages": ids})
        trip = ctx.request("POST", "/api/plants/trip", {"region": "FL", "outages": ids})
        rc = r["root_cause"]
        assert rc["cause"] == "plant" and r["kind"] == "plant", (rc["cause"], r["kind"])
        s = rc["sentence"]
        assert "went offline" in s and name in s and " MW" in s and "pick up" in s, s
        low = s.lower()
        assert "storm" not in low and "data center" not in low and "heat" not in low, s
        assert "went offline" in r["headline"]["text"] and name in r["headline"]["text"], r["headline"]["text"]
        assert r["event"]["people"] == trip["people"] > 0, (r["event"]["people"], trip["people"])
        po = r["plant_outage"]
        assert po["is_cause"] and po["count"] == len(ids) and abs(po["capacity_mw"] - top["plant"]["pmax"]) <= 1, po
        assert po["with_plants"]["people"] == 0 and po["people_due_to_plant"] == r["event"]["people"], po
        assert (rc["plant"]["pct_with_plant"] or 999) <= 100 < rc["pct_with"], (rc["plant"]["pct_with_plant"], rc["pct_with"])
        facts = {f["key"]: f for f in r["facts"]}
        for k in ("plant.offline", "plant.names", "plant.capacity_mw", "plant.output_mw", "plant.picked_up", "plant.people_due_to_plant", "cause.pct_with_plant"):
            assert k in facts, f"fact {k} missing"
        assert name in facts["plant.names"]["text"] and facts["cause.kind"]["value"] == "plant", facts["plant.names"]
        assert r["case"]["body"]["outages"] == sorted(ids), r["case"]["body"]  # the plan re-runs keep the plant out
        assert r["replay"]["outages"] == sorted(ids), "the replay doesn't say which plants were out"

    def test_briefing_harmless_plant_and_plain_case():
        calm = next(e for e in fl_ranking()["ranking"] if e["people"] == 0 and e["steps"] == 0)  # nothing even trips
        r = ctx.request("POST", "/api/briefing", {"region": "FL", "outages": calm["plant"]["ids"]})
        assert r["kind"] == "calm" and r["verdict"] == "nothing_happened", (r["kind"], r["verdict"])
        assert r["root_cause"]["cause"] == "none" and "went offline" in r["root_cause"]["sentence"], r["root_cause"]
        assert "offline" in r["headline"]["text"] and "holds" in r["headline"]["text"], r["headline"]["text"]
        ctx.request("POST", "/api/briefing", {"region": "FL", "outages": [987654321]}, expect=422)
        plain = ctx.request("POST", "/api/briefing", {"region": "FL", "lat": 28.54, "lon": -81.38, "mw": 300, "load_factor": 0.82})
        assert plain["plant_outage"] is None and "outages" not in plain["case"]["body"], "a plain case grew plant fields"

    def test_briefing_campus_not_the_plant():
        """The hero campus (1,500 MW at Fort Myers) with Bartow 6 gas out: the same people lose power with
        every plant running, so the briefing must not blame the plant (cause and kind), and its sentence
        says the outcome is unchanged. Bartow takes a different line over first: that's first_failed."""
        bartow = next((p for p in fl_plants()["plants"] if p["sub_name"].upper() == "BARTOW 6" and p["fuel"] == "gas"), None)
        assert bartow, "Bartow 6 gas missing from the Florida plant list"
        r = ctx.request("POST", "/api/briefing", {"region": "FL", "lat": 26.64, "lon": -81.87, "mw": 1500, "outages": [bartow["id"]]})
        rc, po = r["root_cause"], r["plant_outage"]
        assert r["event"]["people"] > 0, "the hero campus no longer cascades"
        assert po["people_due_to_plant"] == max(r["event"]["people"] - po["with_plants"]["people"], 0), po
        if po["people_due_to_plant"] == 0:
            assert rc["cause"] != "plant" and r["kind"] != "plant" and not po["is_cause"], (rc["cause"], r["kind"], po["is_cause"])
            s = rc["sentence"]
            assert "the same" in s and "lose power" in s, s
            assert "couldn't pick up" not in r["headline"]["text"] and "adds no one" in r["headline"]["text"], r["headline"]["text"]
            ff = rc.get("first_failed")
            if ff:  # the line the lost plant took over first holds with every plant running; the reported line doesn't
                assert ff["pct_with"] > 100 and (ff["pct_with_plant"] or 0) <= 100, ff
                assert rc["line"]["id"] != ff["id"] and (rc["plant"]["pct_with_plant"] or 0) > 100, (rc["line"], rc["plant"])
                assert ff["label"].lower() in s.lower() and rc["line"]["label"].lower() in s.lower(), s
            facts = {f["key"]: f for f in r["facts"]}
            assert facts["cause.kind"]["value"] != "plant" and facts["plant.is_cause"]["value"].startswith("no"), (facts["cause.kind"], facts["plant.is_cause"])

    ctx.check("plants: Florida has plants with fuels, capacity and output that balance the load; Texas too", test_list)
    ctx.check("plants: ranking computes in the background, sorted by people, harmless plants listed with 0", test_ranking)
    ctx.check("plants: tripping the top-ranked Florida plant cascades with people > 0 (matches its rank)", test_trip_top)
    ctx.check("plants: tripping a harmless plant leaves 0 people without power", test_trip_harmless)
    ctx.check("plants: retiring all coal removes exactly the coal plants", test_retire_coal)
    ctx.check("plants: unknown plant, fuel, region, load level or half a site is a 422", test_validation)
    ctx.check("plants: tracing delivers the plant's whole output, shares in 0..1, per-bus shares sum to 1", test_trace)
    ctx.check("plants: the plant back (restore) after a trip replays the calm case and names the areas it held up", test_restore_after_trip)
    ctx.check("plants: a cold restore with a campus matches the plain cascade and the trip", test_restore_cold_with_campus)
    ctx.check("plants: restoring nothing, an unknown plant or fuel is a 422", test_restore_validation)
    ctx.check("plants: the briefing of a plant outage names the plant, its MW and the pick-up as the cause", test_briefing_names_the_plant)
    ctx.check("plants: a harmless plant's briefing says the grid holds; a plain case has no plant fields", test_briefing_harmless_plant_and_plain_case)
    ctx.check("plants: hero campus + Bartow 6 gas out: the plant adds no one, so the briefing doesn't blame it", test_briefing_campus_not_the_plant)
