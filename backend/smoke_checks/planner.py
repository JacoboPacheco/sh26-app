"""Smoke checks for the siting planner (owned by the planner track). Loaded by smoke_test.py.

Read-only: the planner stores nothing but an in-memory job, so these are safe against the deployed
backend. Most checks pass use_ai=false (the deterministic planner: no AI quota, same answer every
run); one runs the default path, which uses Gemini when it is configured and falls back when not.
Every plan is re-checked through the public /api/grid endpoints with the case the planner returns,
so the numbers the panel shows are the numbers the workspace will get."""

import time

ALLOWED_AI = {"used", "off", "not_configured", "unavailable", "rate_limited", "slow", "rejected", "out_of_calls", "supply"}
TOOLS = {"headroom_top", "split", "whatif", "move", "shrink", "fix", "cascade", "finish", "check", "note"}


def trace_ok(steps, label):
    """The fields the AI boom mode's trace shows: the call as it ran, the engine's answer, its time,
    and a reason (Gemini's thought or the built-in planner's rule) on every tool step."""
    assert steps, f"{label}: no steps"
    assert [s["n"] for s in steps] == list(range(1, len(steps) + 1)), f"{label}: steps out of order"
    for s in steps:
        assert s["by"] in ("gemini", "planner") and s["tool"] in TOOLS, (label, s["by"], s["tool"])
        if s["tool"] == "note":
            assert s["call"] is None and s["ms"] is None, (label, s)
            continue
        assert isinstance(s["call"], dict) and s["call"]["tool"] and isinstance(s["call"]["args"], dict), (label, s["call"])
        assert isinstance(s["result"], str) and s["result"], (label, s["tool"])
        assert isinstance(s["ms"], int) and 0 <= s["ms"] < 60000, (label, s["tool"], s["ms"])
        if s["by"] == "gemini":
            assert isinstance(s["call_n"], int) and 1 <= s["call_n"] <= 6, (label, s.get("call_n"))
            assert isinstance(s.get("ai_cached"), bool), (label, "a Gemini step says whether its decision came from the cache")
        else:
            assert s["tool"] == "check" or isinstance(s["why"], str) and s["why"], (label, s["tool"], s.get("why"))
        for sub in s["call"]["args"].get("sites", []):
            assert isinstance(sub["sub"], int) and isinstance(sub["mw"], int), (label, sub)
        for u in s.get("upgrade_lines", []):
            assert isinstance(u["from"], int) and isinstance(u["to"], int) and u["added_mva"] > 0, (label, u)
    last = steps[-1]
    assert last["tool"] == "check" and last["result"].startswith(("passes", "does not pass")), (label, last["result"])


def register(ctx):
    memo = {}

    def plan(body):
        key = repr(sorted(body.items()))
        if key not in memo:
            memo[key] = ctx.request("POST", "/api/planner", body)
        return memo[key]

    def calm_in_workspace(case, label):
        w = ctx.request("POST", "/api/grid/whatif", case)
        assert w["overloaded"] == [], f"{label}: the workspace what-if shows {len(w['overloaded'])} lines over"
        c = ctx.request("POST", "/api/grid/cascade", case)
        assert c["people"] == 0 and not c["site_cut_off"], f"{label}: the workspace cascade leaves {c['people']:,} people without power"
        return w, c

    def texas_2000():
        r = plan({"region": "TX", "total_mw": 2000, "max_sites": 3, "use_ai": False, "goal": "place 2,000 MW of AI campuses in Texas without blacking anyone out"})
        assert r["region"] == "TX" and r["by"] == "planner" and r["fallback"] is False, (r["region"], r["by"], r["fallback"])
        assert r["ai"]["status"] == "off", r["ai"]
        v, p = r["verification"], r["plan"]
        assert v["ok"] is True and v["people"] == 0 and v["lines_over"] == 0, v
        assert p["total_mw"] == 2000 and 1 <= len(p["sites"]) <= 3, (p["total_mw"], len(p["sites"]))
        towns = [s["town"] for s in p["sites"]]
        assert len(set(towns)) == len(towns), f"one campus per town: {towns}"
        assert sum(s["mw"] for s in p["sites"]) == 2000
        assert r["steps"] and r["steps"][-1]["tool"] == "check" and r["steps"][-1]["ok"] is True, r["steps"][-1:]
        trace_ok(r["steps"], "Texas 2,000 MW")
        ran = sorted((x["sub"], x["mw"]) for x in r["steps"][-1]["call"]["args"]["sites"])
        assert ran == sorted((s["sub"], s["mw"]) for s in p["sites"]), f"the check step ran on {ran}, not the plan's sites"
        case = r["case"]
        assert case["region"] == "TX" and case["mw"] == p["sites"][0]["mw"], case
        w, _ = calm_in_workspace(case, "Texas 2,000 MW")
        assert w["mw"] == 2000, f"the workspace case adds {w['mw']} MW"
        assert abs(max(w["loading_pct"]) - v["busiest_pct"]) < 0.2, f"busiest {max(w['loading_pct'])} % in the workspace vs {v['busiest_pct']} % in the plan"

    def florida_needs_upgrades():
        r = plan({"region": "FL", "total_mw": 3000, "max_sites": 3, "use_ai": False})
        v, p = r["verification"], r["plan"]
        assert v["ok"] is True and p["total_mw"] == 3000, (v, p["total_mw"])
        assert p["upgrades"] and p["upgrade_lines"] and p["added_mva"] > 0, "3,000 MW in Florida should need upgrades"
        assert r["case"]["upgrades"], "the case carries the upgrades"
        trace_ok(r["steps"], "Florida 3,000 MW")
        # the fix step names the same branches the plan upgrades, with their ends for the map
        fix = [s for s in r["steps"] if s["tool"] == "fix"]
        assert fix and {str(u["id"]) for u in fix[-1]["upgrade_lines"]} == set(p["upgrades"]), (fix[-1:], p["upgrades"])
        calm_in_workspace(r["case"], "Florida 3,000 MW with upgrades")
        alt = r["without_upgrades"]
        assert alt and 0 < alt["total_mw"] < 3000 and alt["verification"]["ok"] is True, alt and alt["total_mw"]
        assert not alt["case"]["upgrades"], "the version without upgrades has none"
        calm_in_workspace(alt["case"], "Florida without upgrades")
        # one step more at the same sites is over a limit: the alternative really is the most they take
        more = dict(alt["case"], mw=alt["case"]["mw"] + 100)
        assert ctx.request("POST", "/api/grid/whatif", more)["overloaded"], "100 MW more should put a line over"

    def near_town():
        r = plan({"region": "GA", "total_mw": 1500, "max_sites": 3, "use_ai": False, "goal": "1,500 MW near Atlanta"})
        assert r["near"] and r["near"]["town"] == "Atlanta", r["near"]
        assert r["verification"]["ok"] is True
        assert all(s.get("km") is not None and s["km"] <= r["near"]["km"] for s in r["plan"]["sites"]), r["plan"]["sites"]
        # "in New York" is the state the plan is for, not NY's town New York (the AI boom goal sentence);
        # "near New York" still names the town
        ny = plan({"region": "NY", "total_mw": 1000, "max_sites": 2, "use_ai": False, "goal": "Place 1 GW of AI campuses in New York without blacking anyone out"})
        assert ny["near"] is None, f"'in New York' read as near {ny['near']}"
        ny2 = plan({"region": "NY", "total_mw": 1000, "max_sites": 2, "use_ai": False, "goal": "1 GW near New York"})
        assert ny2["near"] and ny2["near"]["town"] == "New York", ny2["near"]

    def small_state_supply():
        # Rhode Island's model can't generate 500 MW more: the plan places less, says why, and passes
        r = plan({"region": "RI", "total_mw": 500, "max_sites": 3, "use_ai": False})
        v, p = r["verification"], r["plan"]
        assert v["ok"] is True and v["people"] == 0 and not v["site_cut_off"], v
        assert p["partial"] is True and p["partial_reason"] == "supply" and 0 < p["total_mw"] < 500, (p["partial"], p.get("partial_reason"), p["total_mw"])
        assert sum(s["mw"] for s in p["sites"]) == p["total_mw"] == v["total_mw"], p["sites"]
        calm_in_workspace(r["case"], "Rhode Island (supply-limited)")

    def job_runs_to_done():
        body = {"region": "FL", "total_mw": 2000, "max_sites": 3, "use_ai": False}
        j = ctx.request("POST", "/api/planner/start", body)
        assert j["status"] == "running" and j["id"], j
        deadline = time.time() + 25
        while True:
            s = ctx.request("GET", f"/api/planner/jobs/{j['id']}")
            if s["status"] != "running" or time.time() > deadline:
                break
            time.sleep(0.4)
        assert s["status"] == "done", f"job ended as {s['status']}: {s.get('error')}"
        assert s["result"]["verification"]["ok"] is True
        assert [x["text"] for x in s["steps"]] == [x["text"] for x in s["result"]["steps"]], "the live steps differ from the result's"
        trace_ok(s["steps"], "background job")
        # fresh is accepted (it only skips the cache of an earlier Gemini run)
        j2 = ctx.request("POST", "/api/planner/start", {**body, "fresh": True})
        assert j2["id"] != j["id"], j2

    def default_path_answers():
        # Gemini when configured (cached after the first run), the deterministic planner otherwise
        r = plan({"region": "FL", "total_mw": 1000, "max_sites": 2, "goal": "1,000 MW in Florida without blacking anyone out"})
        assert r["ai"]["status"] in ALLOWED_AI, r["ai"]
        assert isinstance(r["fallback"], bool) and r["by"] in ("gemini", "planner")
        assert r["fallback"] == (r["by"] != "gemini"), (r["fallback"], r["by"])
        assert r["verification"]["ok"] is True and r["plan"]["total_mw"] == 1000, r["verification"]
        assert r["ai"]["calls"] <= 6, f"{r['ai']['calls']} Gemini calls for one plan (the cap is 6)"
        assert 0 <= r["ai"]["cached_calls"] <= r["ai"]["calls"], r["ai"]
        trace_ok(r["steps"], "Florida 1,000 MW (default path)")
        if r["by"] == "gemini":
            g = [s for s in r["steps"] if s["by"] == "gemini"]
            assert g[-1]["tool"] == "finish" and all(isinstance(s["ai_ms"], int) or s["ai_ms"] is None for s in g), g[-1:]
            assert any(s["thought"] for s in g), "Gemini's steps carry its one-sentence reasons"
        calm_in_workspace(r["case"], "Florida 1,000 MW (default path)")

    def rejects_bad_requests():
        base = {"region": "FL", "total_mw": 1000, "max_sites": 3, "use_ai": False}
        for bad, why in (
            ({"total_mw": 0}, "total 0"),
            ({"total_mw": 40000}, "total 40,000"),
            ({"max_sites": 7}, "7 sites"),
            ({"max_sites": 0}, "0 sites"),
            ({"region": "ZZ"}, "unknown region"),
            ({"region": "US"}, "the national map"),
            ({"total_mw": 12000, "max_sites": 2}, "6,000 MW per site"),
            ({"load_factor": 3}, "load 3.0"),
            ({"goal": "x" * 301}, "a 301-character goal"),
        ):
            r = ctx.request("POST", "/api/planner", {**base, **bad}, expect=422)
            assert isinstance(r["detail"], (str, list)), f"{why}: {r}"
        ctx.request("GET", "/api/planner/jobs/notarealjob123", expect=404)
        ctx.request("GET", "/api/planner/jobs/bad!id", expect=422)

    ctx.check("planner: 2,000 MW in Texas, verified, and the workspace agrees", texas_2000)
    ctx.check("planner: 3,000 MW in Florida needs upgrades; the most without them is exact", florida_needs_upgrades)
    ctx.check("planner: a goal naming a town keeps the sites near it", near_town)
    ctx.check("planner: a small state's generators cap the total; the plan says so and passes", small_state_supply)
    ctx.check("planner: a background job reports its steps and ends done", job_runs_to_done)
    ctx.check("planner: the default path (Gemini or its fallback) returns a verified plan", default_path_answers)
    ctx.check("planner: bad requests get a 422, unknown jobs a 404", rejects_bad_requests)
