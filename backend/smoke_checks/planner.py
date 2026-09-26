"""Smoke checks for the siting planner (owned by the planner track). Loaded by smoke_test.py.

Read-only: the planner stores nothing but an in-memory job, so these are safe against the deployed
backend. Most checks pass use_ai=false (the deterministic planner: no AI quota, same answer every
run); one runs the default path, which uses Gemini when it is configured and falls back when not.
Every plan is re-checked through the public /api/grid endpoints with the case the planner returns,
so the numbers the panel shows are the numbers the workspace will get."""

import time

ALLOWED_AI = {"used", "off", "not_configured", "unavailable", "slow", "rejected", "out_of_calls", "supply"}


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

    def default_path_answers():
        # Gemini when configured (cached after the first run), the deterministic planner otherwise
        r = plan({"region": "FL", "total_mw": 1000, "max_sites": 2, "goal": "1,000 MW in Florida without blacking anyone out"})
        assert r["ai"]["status"] in ALLOWED_AI, r["ai"]
        assert isinstance(r["fallback"], bool) and r["by"] in ("gemini", "planner")
        assert r["fallback"] == (r["by"] != "gemini"), (r["fallback"], r["by"])
        assert r["verification"]["ok"] is True and r["plan"]["total_mw"] == 1000, r["verification"]
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
