"""Smoke checks for "Harden before the storm" (backend/harden.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200), auth(), expected.
Read-only: the planner stores nothing (an in-memory cache and jobs only).

A short storm across Fort Myers (the hurricane checks' own path) on a case with a 500 MW campus there, so the case
fields reach the engine too: the no-hardening run must equal the hurricane route's toll (/api/hurricane/track, then
/api/grid/cascade with those lines as trip: people still out when it settles), and every plan shown must fit the budget and
re-run to the same number."""

import time

ACROSS_FORT_MYERS = [[-82.3, 26.45], [-81.87, 26.64], [-81.4, 26.9]]
RADIUS_KM = 20
CAMPUS = {"lat": 26.6406, "lon": -81.8723, "mw": 500}
BUDGET = 50e6
POLL_S = 170


def register(ctx):
    state = {}

    def body(**extra):
        return {"region": "FL", "points": ACROSS_FORT_MYERS, "radius_km": RADIUS_KM, "budget_usd": BUDGET, **CAMPUS, **extra}

    def finish(r):
        t0 = time.time()
        while r["status"] == "running":
            assert time.time() - t0 < POLL_S, f"the plan was still running after {POLL_S} s: {r.get('progress')}"
            time.sleep(1.0)
            r = ctx.request("GET", f"/api/harden/jobs/{r['job']}")
            assert isinstance(r.get("trace"), list) and r.get("progress"), "a job view without its trace or progress"
        assert r["status"] == "done", f"the plan ended {r['status']}: {r.get('error')}"
        return r["result"]

    def run_plan():
        r = ctx.request("POST", "/api/harden/run", body())
        res = finish(r)
        for k in ("storm", "none", "engine", "best", "rounds", "trace", "by", "sources", "cost_method", "note"):
            assert k in res, f"no {k} in the result"
        assert res["storm"]["lines"] == len(res["storm"]["trip"]) >= 1, res["storm"]["lines"]
        assert all(s["url"].startswith("https://") for s in res["sources"]), "a cost source without its link"
        assert "synthetic" in res["note"].lower(), res["note"]
        state["res"] = res
        # the same case again: the cached result at once
        again = ctx.request("POST", "/api/harden/run", body())
        assert again["status"] == "done" and again["result"]["best"] == res["best"], "the second run is not the cached result"

    def no_hardening_is_the_hurricane_toll():
        res = state["res"]
        hits = ctx.request("POST", "/api/hurricane/track", {"points": ACROSS_FORT_MYERS, "radius_km": RADIUS_KM})
        assert hits["trip"] == res["storm"]["trip"], "the planner's storm knocks out other lines than hurricane mode's"
        c = ctx.request("POST", "/api/grid/cascade", {"region": "FL", **CAMPUS, "trip": hits["trip"]})
        none = res["none"]
        assert none["people_out"] == c["people"], f"no hardening: {none['people_out']} people out, the hurricane route says {c['people']}"
        assert none["people_zone"] == c["people_zone"] and none["people_hit"] == c["people_hit"], (none["people_zone"], c["people_zone"], none["people_hit"], c["people_hit"])
        assert none["steps"] == c["total_steps"], f"steps {none['steps']} != {c['total_steps']}"
        assert sorted(none["out_subs"]) == sorted(int(k) for k in c["affected"]), "the towns still out aren't the cascade's"
        state["trip"] = hits["trip"]

    def plans_fit_the_budget_and_rerun():
        res = state["res"]
        trip = state["trip"]
        plans = [res["engine"]] + ([res["gemini"]] if res.get("gemini") else [])
        for p in plans:
            assert p["cost_usd"] <= res["budget_usd"] + 1, f"{p['by']}'s plan costs {p['cost_usd']} over the budget {res['budget_usd']}"
            assert set(p["lines"]) <= set(trip), f"{p['by']}'s plan hardens a line the storm doesn't knock out"
            assert abs(p["cost_usd"] - sum(d["cost_usd"] for d in p["lines_detail"])) < 2000, f"{p['by']}'s cost isn't the sum of its lines'"
            assert 0 < p["cost_low_usd"] <= p["cost_usd"], f"{p['by']}'s typical range is upside down: {p['cost_low_usd']} to {p['cost_usd']}"
            c = ctx.request("POST", "/api/grid/cascade", {"region": "FL", **CAMPUS, "trip": [b for b in trip if b not in set(p["lines"])]})
            assert p["people_out"] == c["people"], f"{p['by']}'s plan: {p['people_out']} out, a re-run says {c['people']}"
            assert p["people_kept"] == max(0, res["none"]["people_out"] - c["people"]), f"{p['by']}'s people kept doesn't match the re-run"
        for rnd in res["rounds"]:
            if rnd.get("verified"):
                assert rnd["cost_usd"] <= res["budget_usd"] + 1, f"round {rnd['round']} verified over the budget"
        best = res["best"]
        assert best["people_kept"] == max(p["people_kept"] for p in plans), "the best plan isn't the one that keeps the most people on"
        assert res["engine"]["people_kept"] >= 0 and res["engine"]["people_out"] <= res["none"]["people_out"]

    def towns_add_up():
        # the towns listed never add up to more than the figure beside them (people still out = the load still cut,
        # town by town, by the cascade's own rule; not everyone living there)
        res = state["res"]
        for p in [res["none"], res["engine"]] + ([res["gemini"]] if res.get("gemini") else []):
            listed = sum(t["people"] for t in p["still_out"])
            assert listed <= p["people_out"], f"the towns still out add up to {listed}, more than the {p['people_out']} still out"
        for p in [res["engine"]] + ([res["gemini"]] if res.get("gemini") else []):
            assert isinstance(p.get("kept_towns"), list) and isinstance(p.get("newly_out"), list), "a plan without its town lists"
            if not p["newly_out"]:  # nothing newly out: the towns kept on are all of it
                kept = sum(t["people"] for t in p["kept_towns"])
                assert kept <= p["people_kept"], f"the towns kept on add up to {kept}, more than the {p['people_kept']} kept on"

    def info():
        i = ctx.request("GET", "/api/harden/info")
        ai = ctx.request("GET", "/api/ai/status")
        assert i["configured"] == ai["configured"], "the control's label disagrees with the AI status"
        assert i["budgets"] and all(s["url"].startswith("https://") for s in i["sources"]), i

    def fallback_or_gemini():
        res = state["res"]
        ai = ctx.request("GET", "/api/ai/status")
        if not ai.get("configured"):
            assert res["by"] == "fallback" and res["gemini"] is None and res["best"]["by"] == "engine", (res["by"], res["best"]["by"])
            assert res["why"], "a fallback without its reason"
            assert not any(r["actor"] == "gemini" for r in res["trace"]), "a Gemini row in a run without Gemini"
            assert any(r["kind"] == "plain" for r in res["trace"]), "the fallback isn't labeled in the trace"
        elif res["by"] == "gemini":
            assert res["gemini"] and res["function_calls"] >= 1 and any(r.get("verified") for r in res["rounds"]), "Gemini used but no verified plan"
            assert any(r["kind"] in ("propose", "revise") for r in res["trace"]) and any(r["kind"] == "verify" for r in res["trace"])
        else:
            assert res["why"], "a fallback without its reason"

    def validation():
        ctx.request("POST", "/api/harden/run", {"region": "FL", "budget_usd": BUDGET}, expect=422)  # no storm
        ctx.request("POST", "/api/harden/run", {"region": "FL", "preset": "not-a-storm", "budget_usd": BUDGET}, expect=422)
        ctx.request("POST", "/api/harden/run", {"region": "TX", "preset": "tampa-bay", "budget_usd": BUDGET}, expect=422)
        ctx.request("POST", "/api/harden/run", {"region": "FL", "preset": "tampa-bay", "budget_usd": 0}, expect=422)
        ctx.request("POST", "/api/harden/run", {"region": "FL", "preset": "tampa-bay", "budget_usd": 1e12}, expect=422)
        ctx.request("POST", "/api/harden/run", {"region": "FL", "points": [[-74.0, 40.7], [-73.9, 40.8]], "budget_usd": BUDGET}, expect=422)
        ctx.request("POST", "/api/harden/run", {"region": "FL", "points": [[-81.87, 26.64]], "budget_usd": BUDGET}, expect=422)
        ctx.request("POST", "/api/harden/run", {"region": "FL", "preset": "tampa-bay", "budget_usd": "lots"}, expect=422)
        ctx.request("POST", "/api/harden/run", {"region": "FL", "preset": "tampa-bay", "budget_usd": BUDGET, "lat": 26.6}, expect=422)  # half a campus
        ctx.request("GET", "/api/harden/jobs/no-such-job-here", expect=404)

    ctx.check("harden: 422 for no storm, an unknown preset, Texas, a bad budget, New York, one point, half a campus; 404 for an unknown job", validation)
    ctx.check("harden: a plan for a storm across Fort Myers (with a campus) finishes, with sources, and the same case comes back cached", run_plan)
    ctx.check("harden: with no hardening the toll equals the hurricane route's (track, then cascade)", no_hardening_is_the_hurricane_toll)
    ctx.check("harden: every plan shown fits the budget and re-runs to its people-out and people-kept", plans_fit_the_budget_and_rerun)
    ctx.check("harden: without a key the engine's plan is the labeled fallback; with one, Gemini's plans are verified", fallback_or_gemini)
    ctx.check("harden: the towns still out and kept on never add up to more than the totals beside them", towns_add_up)
    ctx.check("harden: /api/harden/info says whether Gemini plans here (the same as /api/ai/status), with the budgets and sources", info)
