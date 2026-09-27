"""Smoke checks for the hurricane feature (owned by its track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
Create nothing another user would see; clean up what you create.

Everything here is read-only: the storm endpoints store nothing."""

# a short storm path straight across Fort Myers (the demo's hero site), from the Gulf inland
ACROSS_FORT_MYERS = [[-82.3, 26.45], [-81.87, 26.64], [-81.4, 26.9]]


def register(ctx):
    state = {}

    def track_across_fort_myers():
        r = ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "radius_km": 20})
        assert r["count"] == len(r["trip"]) == len(r["along_km"]) >= 1, f"{r['count']} lines, {len(r['along_km'])} distances"
        assert r["count"] <= 400, r["count"]
        assert r["along_km"] == sorted(r["along_km"]), "lines not ordered along the track"
        assert all(0 <= a <= r["total_km"] + 0.1 for a in r["along_km"]), "a distance falls off the track"
        branches = {b["id"]: b for b in ctx.request("GET", "/api/grid")["branches"]}
        missing = [bid for bid in r["trip"] if bid not in branches]
        assert not missing, f"unknown line ids {missing[:3]}"
        inside = [bid for bid in r["trip"] if branches[bid]["from_sub"] == branches[bid]["to_sub"]]
        assert not inside, f"transformers inside one substation came back: {inside[:3]}"
        state["trip"] = r["trip"]

    def track_validation():
        ctx.request("POST", "/api/hurricane/track", {"points": [[-81.87, 26.64]]}, expect=422)  # one point
        ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "radius_km": 0}, expect=422)
        ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "radius_km": 500}, expect=422)
        ctx.request("POST", "/api/hurricane/track", {"points": [[-74.0, 40.7], [-73.9, 40.8]]}, expect=422)  # New York
        ctx.request("POST", "/api/hurricane/track", {"points": [[-81.87, 26.64], [-81.87, 26.64]]}, expect=422)  # no length
        ctx.request("POST", "/api/hurricane/track", {"points": [[-81.87, 26.64, 3], [-81.4, 26.9]]}, expect=422)
        ctx.request("POST", "/api/hurricane/track", {"points": [[-81.0, 26.0]] * 41}, expect=422)  # too many points
        ctx.request("POST", "/api/hurricane/track", {"points": "across Florida"}, expect=422)

    def cascade_from_the_storm():
        trip = state["trip"]
        c = ctx.request("POST", "/api/grid/cascade", {"trip": trip})
        assert c["steps"], "no steps"
        s0 = c["steps"][0]
        assert s0["n"] == 0, f"step 0 has n={s0['n']}"
        assert sorted(s0["tripped"]) == sorted(trip), "step 0 doesn't list the storm's lines"
        assert c["outcome"] in ("settled", "islanded"), c["outcome"]
        homes = [s["homes"] for s in c["steps"]]
        assert homes == sorted(homes), f"homes not monotone: {homes}"

    def presets():
        p = ctx.request("GET", "/api/hurricane/presets")
        assert 2 <= len(p["presets"]) <= 3, len(p["presets"])
        assert 1 <= p["category_default"] <= 5
        assert set(p["categories"].keys()) == {"1", "2", "3", "4", "5"}, p["categories"].keys()
        for pre in p["presets"]:
            assert pre["name"] and pre["description"] and pre["points"], pre
            assert 1 <= pre["category"] <= 5, pre["category"]
            r = ctx.request("POST", "/api/hurricane/track", {"points": pre["points"], "category": pre["category"],
                                                               "radius_km": pre["radius_km"], "landfall_km": pre.get("landfall_km")})
            assert 1 <= r["count"] < 400, f"{pre['id']}: {r['count']} lines"
            assert r["category"] == pre["category"], (pre["id"], r["category"], pre["category"])
            # the preset's own bake, if the file is fresh, must agree with a live solve of the same inputs
            if "hits" in pre:
                assert pre["hits"]["trip"] == r["trip"], f"{pre['id']}: baked hits are stale (rerun scripts/bake_hurricane.py)"

    def category_control():
        r1 = ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "category": 1})
        r5 = ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "category": 5})
        assert r1["vmax_mph"] < r5["vmax_mph"], (r1["vmax_mph"], r5["vmax_mph"])
        assert r1["count"] <= r5["count"], "a weaker category took out more lines than a stronger one"
        ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "category": 0}, expect=422)
        ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "category": 6}, expect=422)

    def deterministic():
        r1 = ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "category": 3})
        r2 = ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "category": 3})
        assert r1["trip"] == r2["trip"], "the same storm gave different lines on two runs"

    ctx.check("hurricane: a track across Fort Myers knocks out known lines between substations, in order", track_across_fort_myers)
    ctx.check("hurricane: rejects one point, radius 0 or 500, New York, a zero-length or malformed path", track_validation)
    ctx.check("hurricane: the cascade from the storm's lines reports them as step 0", cascade_from_the_storm)
    ctx.check("hurricane: 2-3 presets, each knocking out lines under the cap, baked hits agree with a live solve", presets)
    ctx.check("hurricane: a stronger category takes out at least as many lines, with a higher peak wind", category_control)
    ctx.check("hurricane: the same storm always knocks out the same lines (deterministic, no per-request randomness)", deterministic)
