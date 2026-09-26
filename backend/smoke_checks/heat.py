"""Smoke checks for the heat-wave clock (frontend/src/features/heat). Loaded by smoke_test.py.

The clock only changes `load_factor` on the existing grid endpoints, so these checks pin the story
the demo tells with it: the hero site (Fort Myers) at 500 MW is calm at 4 PM, the heat wave on its
own holds, and the heat wave plus that campus cascades. Public read-only endpoints; creates nothing.

Keep PRESETS and HEAT_WAVE in sync with frontend/src/features/heat/presets.js.
"""

import math

PRESETS = (0.62, 0.82, 1.0, 1.04)  # 3 AM, 9 AM, 4 PM, heat wave
HEAT_WAVE = 1.04
CAMPUS_MW = 500


def register(ctx):
    hero = ctx.expected["hero"]
    site = {"lat": hero["lat"], "lon": hero["lon"]}
    state = {}

    def rejects_bad_levels():
        # outside 0.4-1.4 is a 422 with a sentence, on every endpoint that takes a level
        for bad in (0.3, 2.0):
            r = ctx.request("POST", "/api/grid/whatif", {**site, "mw": CAMPUS_MW, "load_factor": bad}, expect=422)
            assert isinstance(r.get("detail"), str) and "Load level" in r["detail"], r
            ctx.request("POST", "/api/grid/cascade", {"load_factor": bad}, expect=422)
            ctx.request("GET", f"/api/grid/headroom?load_factor={bad}", expect=422)
        ctx.request("GET", "/api/grid/headroom?load_factor=abc", expect=422)

    def calm_at_four_pm():
        w = ctx.request("POST", "/api/grid/whatif", {**site, "mw": CAMPUS_MW, "load_factor": 1.0})
        assert not w["overloaded"], f"{len(w['overloaded'])} over limit at 4 PM, expected none"
        assert w["load_factor"] == 1.0, w["load_factor"]
        state["total_1"] = w["total_load_mw"]

    def presets_scale_the_load():
        # the clock's caption ("Florida's load: 44.8 GW") is the what-if's total_load_mw
        base = state.get("total_1") or ctx.request("POST", "/api/grid/whatif", {"load_factor": 1.0})["total_load_mw"]
        for f in PRESETS:
            w = ctx.request("POST", "/api/grid/whatif", {"load_factor": f})
            assert abs(w["total_load_mw"] - base * f) <= max(2, base * f * 0.001), f"x{f}: {w['total_load_mw']} MW vs {base * f:.0f}"

    def heat_wave_alone_holds():
        # the heat wave with no data center: strained but nothing over limit, so the campus is what tips it
        w = ctx.request("POST", "/api/grid/whatif", {"load_factor": HEAT_WAVE})
        assert not w["overloaded"], f"{len(w['overloaded'])} over limit in the heat wave with no data center"
        assert sum(1 for p in w["loading_pct"] if p >= 80) > 0, "no line strained in the heat wave"
        c = ctx.request("POST", "/api/grid/cascade", {"load_factor": HEAT_WAVE})
        assert c["total_steps"] == 0 and c["homes"] == 0, f"heat wave alone cascaded {c['total_steps']} steps"

    def heat_wave_campus_cascades():
        w = ctx.request("POST", "/api/grid/whatif", {**site, "mw": CAMPUS_MW, "load_factor": HEAT_WAVE})
        assert w["overloaded"], "the 500 MW campus overloads nothing in the heat wave"
        c = ctx.request("POST", "/api/grid/cascade", {**site, "mw": CAMPUS_MW, "load_factor": HEAT_WAVE})
        assert c["load_factor"] == HEAT_WAVE, c["load_factor"]
        assert c["total_steps"] >= 10, f"only {c['total_steps']} steps in the heat wave"
        assert c["outcome"] == "islanded" and c["homes"] > 500_000, f"{c['outcome']}, {c['homes']} homes"
        # (not monotone here: a trip can reconnect a short island and restore some load mid-cascade)
        assert c["homes"] == c["steps"][-1]["homes"], f"final homes {c['homes']} != last step {c['steps'][-1]['homes']}"

    def headroom_per_level():
        grid = ctx.request("GET", "/api/grid")
        n = len(grid["subs"])
        sub = str(hero["sub"])
        by_level = {}
        for f in PRESETS:
            h = ctx.request("GET", f"/api/grid/headroom?load_factor={f}")
            assert h["load_factor"] == f, h["load_factor"]
            by_sub = h["by_sub"]
            assert len(by_sub) == n, f"x{f}: {len(by_sub)} values for {n} substations"
            assert all(math.isfinite(v) and v >= 0 for v in by_sub.values()), f"x{f}: a value is negative or not finite"
            by_level[f] = by_sub[sub]
        # Fort Myers can take less as the day heats up
        seq = [by_level[f] for f in PRESETS]
        assert seq == sorted(seq, reverse=True) and seq[0] > seq[-1], f"Fort Myers headroom by hour: {seq}"
        assert seq[PRESETS.index(1.0)] >= CAMPUS_MW > seq[PRESETS.index(HEAT_WAVE)], f"Fort Myers headroom by hour: {seq}"

    ctx.check("heat: a load level outside 0.4-1.4 is a 422 on what-if, cascade and headroom", rejects_bad_levels)
    ctx.check("heat: Fort Myers at 500 MW has nothing over limit at 4 PM", calm_at_four_pm)
    ctx.check("heat: every preset scales Florida's total load by its factor", presets_scale_the_load)
    ctx.check("heat: the heat wave alone strains lines but overloads none and cascades nothing", heat_wave_alone_holds)
    ctx.check("heat: in the heat wave Fort Myers at 500 MW cascades 10+ steps", heat_wave_campus_cascades)
    ctx.check("heat: headroom has one value per substation at every preset; Fort Myers' shrinks through the day", headroom_per_level)
