"""Smoke checks for hospitals on backup power (owned by its track). Loaded by smoke_test.py.

Read-only: a public list and a public cascade, nothing is created, so it is safe against the
deployed backend.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json)."""

import time


def register(ctx):
    def test_list():
        r = ctx.request("GET", "/api/hospitals?region=FL")
        assert r["region"] == "FL", f"region {r['region']!r}"
        assert "OpenStreetMap" in r["source"] and "ODbL" in r["source"], f"source not credited: {r['source']!r}"
        hs = r["hospitals"]
        assert r["count"] == len(hs) >= 100, f"only {len(hs)} Florida hospitals"
        inside = [h for h in hs if h["in_model"]]
        assert r["in_model"] == len(inside) >= 0.7 * len(hs), f"only {len(inside)} of {len(hs)} Florida hospitals in the model"
        for h in inside:
            assert h["name"] and h["sub"] is not None and h["area"], f"hospital without a substation/area: {h}"
            assert 0 <= h["km"] <= r["max_km"], f"{h['name']}: {h['km']} km from its substation"
        assert all(h["sub"] is None for h in hs if not h["in_model"]), "an out-of-model hospital has a substation"

    def test_other_state():
        r = ctx.request("GET", "/api/hospitals?region=TX")
        assert r["region"] == "TX" and r["count"] > 100, f"Texas: {r.get('count')} hospitals"
        assert all(h["state"] == "TX" for h in r["hospitals"]), "a non-Texas hospital in the Texas list"
        ctx.request("GET", "/api/hospitals?region=ZZ", expect=422)

    def test_backup_hero():
        h = ctx.expected["hero"]
        t = time.time()
        r = ctx.request("POST", "/api/hospitals/backup", {"lat": h["lat"], "lon": h["lon"], "mw": h["mw"]})
        took = time.time() - t
        c = r["counts"]
        assert c["backup"] == len(r["backup"]) > 0, f"hero cascade: {c['backup']} hospitals on backup"
        assert c["strained"] == len(r["strained"]), "strained count != list"
        assert c["backup"] + c["strained"] + c["ok"] == c["in_model"], f"counts don't add up: {c}"
        assert c["in_model"] + c["outside_model"] == c["total"], f"counts don't add up: {c}"
        assert all(x["share"] >= r["backup_share"] - 1e-3 for x in r["backup"]), "a backup hospital under the share"
        assert all(x["share"] < r["backup_share"] + 1e-3 for x in r["strained"]), "a strained hospital over the share"
        assert r["people"] > 0 and r["outcome"] == "islanded", f"hero: people {r['people']}, outcome {r['outcome']}"
        # the same case through the grid endpoint: every backup hospital's substation lost that much
        cas = ctx.request("POST", "/api/grid/cascade", {"lat": h["lat"], "lon": h["lon"], "mw": h["mw"]})
        for x in r["backup"]:
            got = cas["affected"].get(str(x["sub"]))
            assert got is not None and abs(got - x["lost_mw"]) <= 0.2, f"{x['name']}: backup says {x['lost_mw']} MW, cascade {got}"
        assert took < 5, f"backup took {took:.1f} s"

    def test_backup_calm():
        h = ctx.expected["hero"]
        r = ctx.request("POST", "/api/hospitals/backup", {"lat": h["lat"], "lon": h["lon"], "mw": h["calm_mw"]})
        assert r["counts"]["backup"] == 0 and r["counts"]["strained"] == 0, f"calm case: {r['counts']}"

    def test_backup_validation():
        ctx.request("POST", "/api/hospitals/backup", {"lat": 26.64, "lon": -81.87}, expect=422)
        ctx.request("POST", "/api/hospitals/backup", {"region": "ZZ"}, expect=422)

    ctx.check("hospitals: /api/hospitals lists Florida's OSM hospitals matched to substations", test_list)
    ctx.check("hospitals: another state (TX) and an unknown region (422)", test_other_state)
    ctx.check("hospitals: hero cascade puts hospitals on backup, counts add up, matches the cascade", test_backup_hero)
    ctx.check("hospitals: calm case (500 MW) has no hospital on backup", test_backup_calm)
    ctx.check("hospitals: backup validates the case (422)", test_backup_validation)
