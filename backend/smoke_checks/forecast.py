"""Smoke checks for forecast (owned by its track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
Read-only: every call is a public forecast, what-if or cascade; nothing is created, so it is safe
against the deployed backend."""

import math


def register(ctx):
    hero = ctx.expected["hero"]
    site = {"lat": hero["lat"], "lon": hero["lon"]}
    memo = {}

    def forecast(mw):
        if mw not in memo:
            memo[mw] = ctx.request("POST", "/api/forecast", {**site, "mw": mw})
        return memo[mw]

    def cascade(mw, firm):
        return ctx.request("POST", "/api/grid/cascade", {**site, "mw": mw, "firm": firm})

    def forecast_matches_the_cascade():
        f = forecast(hero["mw"])
        assert f["verdict"] == "cascades", f"hero verdict {f['verdict']!r}"
        for firm in (False, True):
            c = cascade(hero["mw"], firm)
            m = f["cascade"]["firm" if firm else "flexible"]
            assert m["people"] == c["people"], f"{'firm' if firm else 'flexible'}: forecast {m['people']:,} != cascade {c['people']:,}"
            assert m["steps"] == c["total_steps"], f"steps {m['steps']} != {c['total_steps']}"
            assert m["outcome"] == c["outcome"]
            assert sum(a["people"] for a in m["areas_top5"]) <= m["people"], "hardest-hit areas add up to more than the total"
        flex = f["cascade"]["flexible"]
        assert flex["campus_cut_off_at_step"] is not None, "the hero campus should be cut off (flexible)"
        assert "cut off at step" in f["sentence"] and "estimate" in f["sentence"], f["sentence"]
        assert f["first_to_overload"], "no first line to overload"

    def room_is_the_room():
        f = forecast(hero["mw"])
        room = f["room_mw"]
        assert f["room_capped"] is False and 0 < room < hero["mw"], f"room {room}"
        below = ctx.request("POST", "/api/grid/whatif", {**site, "mw": math.floor(room)})
        assert below["overloaded"] == [], f"{len(below['overloaded'])} over limit at the room ({math.floor(room)} MW)"
        above = ctx.request("POST", "/api/grid/whatif", {**site, "mw": room + 2})
        assert above["overloaded"], f"nothing over limit 2 MW above the room ({room + 2} MW)"
        assert abs(room - above["headroom_mw"]) <= max(5.0, 0.02 * room), f"room {room} vs what-if headroom {above['headroom_mw']}"

    def calm_size_holds():
        f = forecast(hero["calm_mw"])
        assert f["verdict"] == "holds" and f["over"] == 0, f"{hero['calm_mw']} MW: {f['verdict']}, {f['over']} over"
        assert f["cascade"]["flexible"]["people"] == 0 and f["cascade"]["firm"]["people"] == 0

    def sweep_finds_the_cliff():
        # the sweep answers progressively: ask again until every size is tested (a slow server needs more asks)
        for _ in range(40):
            s = ctx.request("POST", "/api/forecast/sweep", site)
            assert s["tested"] == len(s["sizes"]) <= s["total"] == 20, f"{s['tested']} tested, {len(s['sizes'])} sizes"
            if s["complete"]:
                break
        assert s["complete"], "the sweep did not finish after 40 asks"
        rows = s["sizes"]
        assert [r["mw"] for r in rows] == list(range(100, 2001, 100)), "sizes are not 100..2,000 MW in 100 MW steps"
        cliff = s["cliff_mw"]
        assert cliff == math.ceil(s["room_mw"] / 100) * 100, f"cliff {cliff} vs room {s['room_mw']}"
        assert all(r["over"] == 0 and r["people_flexible"] == 0 for r in rows if r["mw"] < cliff), "something happens below the cliff"
        assert s["blackout_mw"] is not None and s["blackout_mw"] >= cliff
        at = next(r for r in rows if r["mw"] == hero["mw"])
        f = forecast(hero["mw"])
        assert at["people_flexible"] == f["cascade"]["flexible"]["people"], "sweep and forecast disagree (flexible)"
        assert at["people_firm"] == f["cascade"]["firm"]["people"], "sweep and forecast disagree (firm)"
        assert isinstance(s["shape_sentence"], str) and s["shape_sentence"], "no shape sentence"

    def rejects_bad_cases():
        ctx.request("POST", "/api/forecast", {}, expect=422)
        ctx.request("POST", "/api/forecast", {**site, "mw": 0}, expect=422)
        ctx.request("POST", "/api/forecast", {**site, "mw": 500, "region": "ZZ"}, expect=422)
        ctx.request("POST", "/api/forecast/sweep", {"mw": 500}, expect=422)

    def other_states():
        f = ctx.request("POST", "/api/forecast", {"region": "TX", "lat": 32.45, "lon": -99.73, "mw": 1200})
        assert f["region"] == "TX" and f["verdict"] in ("holds", "over_limit", "cascades"), f
        assert f["room_mw"] is not None and f["room_mw"] >= 0

    ctx.check("forecast: the hero's people and steps match /api/grid/cascade (flexible and firm)", forecast_matches_the_cascade)
    ctx.check("forecast: the room is the room (nothing over at it, over 2 MW above)", room_is_the_room)
    ctx.check("forecast: the hero's calm size holds", calm_size_holds)
    ctx.check("forecast: the sweep finds the cliff and agrees with the forecast", sweep_finds_the_cliff)
    ctx.check("forecast: bad cases are rejected (422)", rejects_bad_cases)
    ctx.check("forecast: other states answer for their region (Texas)", other_states)
