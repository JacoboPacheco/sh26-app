"""Smoke checks for the Library (backend/scenarios.py): scenarios that keep a full case, their
server-computed results, versions, share links, and the built-in examples. Loaded by smoke_test.py.

Everything here acts as this run's own throwaway user (plus a second throwaway user for the
owner-only checks): the rows it makes, including its own copy of the examples, live only in those
accounts, so it is safe against the deployed backend. It deletes what it can at the end.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json)."""

import uuid

SEED_MARK = "(demo scenario)"


def register(ctx):
    made = []  # ids to delete at the end
    s = {}

    def hero_case(mw=None, **more):
        h = ctx.expected["hero"]
        return {"lat": h["lat"], "lon": h["lon"], "mw": mw or h["mw"], **more}

    def post(path, data, expect=200, headers=None):
        return ctx.request("POST", path, data, headers=headers or ctx.auth(), expect=expect)

    def test_create_with_case():
        sc = post("/api/scenarios", {"name": "Smoke library hero", "case": hero_case()})
        made.append(sc["id"])
        s["hero"] = sc
        r = sc["result"]
        assert r and r["v"] >= 1, f"no result: {sc}"
        for k in ("verdict", "people", "people_peak", "steps", "overloaded", "areas_top", "sentence", "region"):
            assert k in r, f"result lacks {k}"
        # the stored numbers are the engine's: the same case through /api/grid/cascade
        c = ctx.request("POST", "/api/grid/cascade", hero_case())
        assert r["people"] == c["people"] and r["steps"] == c["total_steps"], (
            f"stored {r['people']:,} people / {r['steps']} steps != cascade {c['people']:,} / {c['total_steps']}"
        )
        assert r["verdict"] == "blackout" and r["people"] > 0, f"hero verdict {r['verdict']}"
        assert sum(a["people"] for a in r["areas_top"]) <= r["people"], "areas add up to more than the total"
        assert sc["case"]["region"] == "FL" and sc["region"] == "FL" and sc["version"] == 1 and sc["parent_id"] is None
        assert sc["example"] is False and sc["shared"] is False
        assert sc["summary"]["overloaded"] == r["overloaded"], "legacy summary out of step with the result"

    def test_create_other_region():
        body = {"name": "Smoke library Texas", "case": {"region": "tx", "lat": 32.45, "lon": -99.73, "mw": 1200, "firm": True}}
        sc = post("/api/scenarios", body)
        made.append(sc["id"])
        assert sc["region"] == "TX" and sc["case"]["firm"] is True, sc["case"]
        assert sc["result"]["region"] == "TX" and sc["result"]["firm"] is True, sc["result"]

    def test_validation():
        h = ctx.auth()
        bad = [
            ({"name": "x", "case": hero_case(region="ZZ")}, "unknown region"),
            ({"name": "x", "case": {"region": "FL", "lat": 40.7, "lon": -74.0, "mw": 500}}, "point outside the state"),
            ({"name": "x", "case": hero_case(trip=[10**12])}, "unknown line id"),
            ({"name": "x", "case": hero_case(load_factor=3)}, "load level out of range"),
            ({"name": "x", "case": hero_case(mw=10**400)}, "huge number"),
            ({"name": "x", "case": {"lat": 26.64, "lon": -81.87}}, "a site without mw"),
            ({"name": "x", "case": {}}, "an empty case"),
            ({"name": "x"}, "neither a case nor a site"),
            ({"name": "x", "note": f"sneaky {SEED_MARK}", "case": hero_case()}, "the examples' mark in a note"),
            ({"name": "x", "case": hero_case(catalog_id="no-such-campus")}, "unknown catalog entry"),
            ({"name": "x", "case": {"sites": [{"lat": 26.6, "lon": -81.9, "mw": 100}] * 13}}, "too many campuses"),
        ]
        for body, why in bad:
            ctx.request("POST", "/api/scenarios", body, headers=h, expect=422)
        ctx.request("GET", f"/api/scenarios/{10**30}", headers=h, expect=422)

    def test_storm_label():
        # a storm keeps a name only when it is one of the server's hypothetical presets, and then the
        # server's own name — a client can't label a scenario with a real storm's name
        presets = ctx.request("GET", "/api/hurricane/presets")["presets"]
        p = presets[0]
        hits = ctx.request("POST", "/api/hurricane/track", {"points": p["points"], "radius_km": p["radius_km"]})
        sc = post("/api/scenarios", {"name": "Smoke library storm", "case": {"trip": hits["trip"], "storm": {"preset": p["id"], "name": "Hurricane Real"}}})
        made.append(sc["id"])
        assert sc["case"]["storm"] == {"preset": p["id"], "name": p["name"]}, sc["case"].get("storm")
        assert "Hurricane Real" not in sc["result"]["sentence"] and sc["lat"] is None and sc["mw"] is None, sc
        assert sc["result"]["storm_lines"] == len(set(hits["trip"])), sc["result"]
        odd = post("/api/scenarios", {"name": "Smoke library storm 2", "case": {"trip": hits["trip"][:3], "storm": {"preset": "not-a-preset", "name": "X"}}})
        made.append(odd["id"])
        assert "storm" not in odd["case"], odd["case"]

    def test_get_and_update():
        sid = s["hero"]["id"]
        got = ctx.request("GET", f"/api/scenarios/{sid}", headers=ctx.auth())
        assert got["result"]["people"] == s["hero"]["result"]["people"], "fresh numbers differ from the stored ones"
        assert [f["id"] for f in got["family"]] == [sid], got["family"]
        calm = ctx.expected["hero"]["calm_mw"]
        up = ctx.request("PUT", f"/api/scenarios/{sid}", {"name": "Smoke library calm", "case": hero_case(calm)}, headers=ctx.auth())
        assert up["name"] == "Smoke library calm" and up["result"]["verdict"] == "holds", up["result"]
        assert up["mw"] == calm, up["mw"]
        ctx.request("PUT", f"/api/scenarios/{sid}", {}, headers=ctx.auth(), expect=422)
        ctx.request("PUT", f"/api/scenarios/{sid}", {"note": SEED_MARK}, headers=ctx.auth(), expect=422)

    def test_versions():
        sid = s["hero"]["id"]
        v2 = post(f"/api/scenarios/{sid}/version", {"case": hero_case(1500, load_factor=1.04)})
        made.append(v2["id"])
        assert v2["parent_id"] == sid and v2["version"] == 2, v2
        assert v2["result"]["load_factor"] == 1.04, v2["result"]
        v3 = post(f"/api/scenarios/{v2['id']}/version", {})  # a copy of v2, still under the original
        made.append(v3["id"])
        assert v3["parent_id"] == sid and v3["version"] == 3 and v3["case"] == v2["case"], v3
        fam = ctx.request("GET", f"/api/scenarios/{v3['id']}", headers=ctx.auth())["family"]
        assert [f["version"] for f in fam] == [1, 2, 3], [f["version"] for f in fam]
        listed = {x["id"]: x for x in ctx.request("GET", "/api/scenarios", headers=ctx.auth())}
        assert listed[v3["id"]]["version"] == 3 and listed[v2["id"]]["parent_id"] == sid
        s["v2"], s["v3"] = v2, v3

    def test_share():
        sid = s["hero"]["id"]
        a = post(f"/api/scenarios/{sid}/share", None)
        b = post(f"/api/scenarios/{sid}/share", None)
        assert a["slug"] == b["slug"] and len(a["slug"]) >= 8, (a, b)
        pub = ctx.request("GET", f"/api/share/{a['slug']}")  # no auth: public
        for k in ("id", "user_id", "owner", "email", "share_slug"):
            assert k not in pub, f"share leaks {k}"
        assert pub["name"] == "Smoke library calm" and pub["case"]["region"] == "FL" and pub["result"]["verdict"] == "holds"
        ctx.request("GET", "/api/share/not-a-real-slug", expect=404)
        ctx.request("GET", "/api/share/x", expect=422)
        ctx.request("DELETE", f"/api/scenarios/{sid}/share", headers=ctx.auth())
        ctx.request("GET", f"/api/share/{a['slug']}", expect=404)

    def test_owner_only():
        email = f"smoke-lib-{uuid.uuid4().hex[:10]}@example.com"
        tok = ctx.request("POST", "/api/auth/signup", {"email": email, "password": "smoketest123"})["access_token"]
        other = {"Authorization": f"Bearer {tok}"}
        sid = s["hero"]["id"]
        ctx.request("GET", f"/api/scenarios/{sid}", headers=other, expect=404)
        ctx.request("PUT", f"/api/scenarios/{sid}", {"name": "mine now"}, headers=other, expect=404)
        ctx.request("DELETE", f"/api/scenarios/{sid}", headers=other, expect=404)
        ctx.request("POST", f"/api/scenarios/{sid}/version", {}, headers=other, expect=404)
        ctx.request("POST", f"/api/scenarios/{sid}/share", None, headers=other, expect=404)
        assert not any(x["id"] == sid for x in ctx.request("GET", "/api/scenarios", headers=other)), "leaked"
        ctx.request("GET", "/api/scenarios", expect=401)
        ctx.request("POST", "/api/scenarios/examples", None, expect=401)

    def test_examples():
        ex = post("/api/scenarios/examples", None)
        names = [e["name"] for e in ex["examples"]]
        assert "Fort Myers · 1,500 MW" in names and len(names) >= 5, names
        again = post("/api/scenarios/examples", None)
        assert not again["created"] and not again["removed"], f"not idempotent: {again}"
        by = {e["name"]: e for e in again["examples"]}
        hero = by["Fort Myers · 1,500 MW"]
        assert hero["example"] and hero["result"]["verdict"] == "blackout", hero["result"]
        assert by["Fort Myers · 500 MW"]["result"]["verdict"] == "holds", by["Fort Myers · 500 MW"]["result"]
        assert any(e["region"] != "FL" for e in again["examples"]), "no example outside Florida"
        h = ctx.auth()
        ctx.request("PUT", f"/api/scenarios/{hero['id']}", {"name": "edited"}, headers=h, expect=403)
        ctx.request("DELETE", f"/api/scenarios/{hero['id']}", headers=h, expect=403)
        v = post(f"/api/scenarios/{hero['id']}/version", {"case": hero_case(500)})
        made.append(v["id"])
        assert not v["example"] and v["parent_id"] == hero["id"] and SEED_MARK not in v["note"], v
        sh = post(f"/api/scenarios/{hero['id']}/share", None)  # examples can be shared
        ctx.request("DELETE", f"/api/scenarios/{hero['id']}/share", headers=h, expect=403)  # but not unshared
        assert ctx.request("GET", f"/api/share/{sh['slug']}")["example"] is True

    def test_delete_keeps_versions():
        sid = s["hero"]["id"]
        ctx.request("DELETE", f"/api/scenarios/{sid}", headers=ctx.auth())
        made.remove(sid)
        ctx.request("GET", f"/api/scenarios/{sid}", headers=ctx.auth(), expect=404)
        v2 = ctx.request("GET", f"/api/scenarios/{s['v2']['id']}", headers=ctx.auth())
        assert v2["parent_id"] is None and v2["version"] == 1, "the oldest version should become the original"
        assert [f["id"] for f in v2["family"]] == [s["v2"]["id"], s["v3"]["id"]], v2["family"]

    def cleanup():
        for sid in list(made):
            ctx.request("DELETE", f"/api/scenarios/{sid}", headers=ctx.auth())
            made.remove(sid)

    ctx.check("library: save a full case, numbers match the cascade", test_create_with_case)
    ctx.check("library: a case in another state, firm", test_create_other_region)
    ctx.check("library: bad cases are 422s", test_validation)
    ctx.check("library: a storm is named only from the server's hypothetical presets", test_storm_label)
    ctx.check("library: open refreshes numbers, update re-solves", test_get_and_update)
    ctx.check("library: versions nest under the original", test_versions)
    ctx.check("library: share links are public, read-only, revocable", test_share)
    ctx.check("library: owner-only (404 for another user)", test_owner_only)
    ctx.check("library: built-in examples are idempotent and locked (403)", test_examples)
    ctx.check("library: deleting an original keeps its versions", test_delete_keeps_versions)
    ctx.check("library: cleanup", cleanup)
