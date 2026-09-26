"""Smoke checks for danger zones (owned by its track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).

Everything here is read-only: the danger endpoint stores nothing (an in-memory cache only).
A cold answer comes back within the server's time budget, possibly `partial` (the likeliest sites
first); the server finishes it in the background, so the first check polls until it's complete
(Florida at 1,500 MW is started at server startup)."""

import math
import time

HERO_MW = 1500
FIRST_ANSWER_S = 15  # the server's budget (8 s) + one background slice it may wait for + slack
COMPLETE_S = 180  # a slow host (Render's free CPU) finishes Florida in the background within this
POLL_S = 6  # 30 requests a minute per visitor on this route: polling stays well under it


def register(ctx):
    state = {}

    def ranked_florida():
        path = f"/api/grid/danger?mw={HERO_MW}&load_factor=1.0&region=FL"
        t0 = time.monotonic()
        d = ctx.request("GET", path)
        took = time.monotonic() - t0
        assert took < FIRST_ANSWER_S, f"first answer took {took:.1f} s"
        while d["partial"]:
            hit = [z["people_hit"] for z in d["zones"]]
            assert hit == sorted(hit, reverse=True), f"partial answer not sorted by people hit: {hit[:6]}"
            assert d["checked"] < d["likely"], d
            assert time.monotonic() - t0 < COMPLETE_S, f"still partial after {COMPLETE_S} s ({d['checked']} of {d['likely']} checked)"
            time.sleep(POLL_S)
            d = ctx.request("GET", path)
        assert d["checked"] == d["likely"], d
        assert d["region"] == "FL" and d["mw"] == HERO_MW and d["firm"] is False, {k: d[k] for k in ("region", "mw", "firm")}
        assert "Synthetic grid model" in d["note"], d["note"]
        zones = d["zones"]
        assert 1 <= len(zones) <= 25, f"{len(zones)} zones"
        hit = [z["people_hit"] for z in zones]
        assert hit == sorted(hit, reverse=True), f"not sorted by people hit: {hit[:6]}"
        assert all(p > 0 for p in hit), "a zone where no one is hit"
        assert all(z["people_zone"] is not None and z["homes_zone"] is not None and z["people"] >= 0 for z in zones), zones[0]
        subs = {s["id"]: s for s in ctx.request("GET", "/api/grid?region=FL")["subs"]}
        unknown = [z["id"] for z in zones if z["id"] not in subs]
        assert not unknown, f"unknown substation ids {unknown[:3]}"
        assert len({z["id"] for z in zones}) == len(zones), "a substation listed twice"
        for z in zones:
            assert z["steps"] >= 1 and z["outcome"] in ("settled", "islanded"), z
            assert z["headroom_mw"] < HERO_MW * 1.05, f"{z['area']}: {z['headroom_mw']} MW headroom yet listed"
            assert abs(subs[z["id"]]["lat"] - z["lat"]) < 1e-3 and abs(subs[z["id"]]["lon"] - z["lon"]) < 1e-3, z
        assert 0 <= d["checked"] and 0 <= d["calm"] and d["likely"] + d["calm"] <= d["candidates"], {k: d[k] for k in ("candidates", "likely", "calm")}
        assert isinstance(d["cached"], bool) and d["computed_ms"] >= 0
        state["d"] = d

    def fort_myers_matches_cascade():
        zones = state["d"]["zones"]
        top = zones[:10]
        fm = next((z for z in top if "Fort Myers" in z["area"]), None)
        assert fm, f"no Fort Myers-area zone in the top 10: {[z['area'] for z in top]}"
        c = ctx.request("POST", "/api/grid/cascade", {"lat": fm["lat"], "lon": fm["lon"], "mw": HERO_MW})
        assert c["sub"] == fm["id"], f"a drop at the zone snaps to {c['sub']}, not {fm['id']}"
        assert c["people_hit"] == fm["people_hit"], f"zone says {fm['people_hit']} people hit, the cascade says {c['people_hit']}"
        assert c["people"] == fm["people"] and c["people_zone"] == fm["people_zone"], (c["people"], fm["people"], c["people_zone"], fm["people_zone"])
        assert c["total_steps"] == fm["steps"], f"zone says {fm['steps']} steps, the cascade {c['total_steps']}"

    def small_campus():
        d = ctx.request("GET", "/api/grid/danger?mw=50&region=FL")
        assert len(d["zones"]) <= 5, f"{len(d['zones'])} zones at 50 MW"
        assert d["calm"] >= d["candidates"] - 10, f"only {d['calm']} of {d['candidates']} towns calm at 50 MW"

    def validation():
        for q in (
            "mw=10",
            "mw=6000",
            "mw=nan",
            "mw=abc",
            "load_factor=2",
            "load_factor=0.1",
            "region=US",
            "region=ZZ",
            "limit=0",
            "limit=51",
        ):
            ctx.request("GET", f"/api/grid/danger?{q}", expect=422)

    def cached_second_call():
        again = ctx.request("GET", f"/api/grid/danger?mw={HERO_MW}&load_factor=1&region=fl")
        assert again["cached"] is True and again["partial"] is False, "second call recomputed"
        first = state["d"]["zones"]
        assert [z["id"] for z in again["zones"]] == [z["id"] for z in first], "cached answer differs"
        # the size is computed at the nearest 50 MW: 1,510 is the 1,500 answer
        near = ctx.request("GET", "/api/grid/danger?mw=1510&region=FL&limit=5")
        assert near["cached"] is True and near["mw"] == HERO_MW and len(near["zones"]) == min(5, len(first)), near["mw"]
        assert math.isclose(near["zones"][0]["people_hit"], first[0]["people_hit"])

    ctx.check("danger: Florida at 1,500 MW answers within the budget, completes, ranks zones by people hit at known substations, labeled synthetic", ranked_florida)
    ctx.check("danger: a Fort Myers-area zone is in the top 10 and a cascade dropped there hits the same people", fort_myers_matches_cascade)
    ctx.check("danger: a 50 MW campus finds few or no zones", small_campus)
    ctx.check("danger: rejects sizes outside 50-5,000 MW, a bad load level, the U.S. or an unknown region, a bad limit", validation)
    ctx.check("danger: a second call (and a size within 25 MW) comes from the cache", cached_second_call)
