"""Smoke checks for "Let a Gemini operator fight it" (backend/grid_operator.py): the same cascade with no operator, the
engine's own operator and a Gemini operator that acts through engine tools. Loaded by smoke_test.py. Public and
read-only: a run is an in-memory job (cached six hours), nothing is stored for another user, so it is safe against the
deployed backend. With no Gemini key (or quota) the run must fall back to the engine operator, labeled.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json)."""

import time

RUN = "/api/operator/run"


def register(ctx):
    memo = {}

    def hero_body():
        h = (ctx.expected or {}).get("hero") or {"lat": 26.64, "lon": -81.87, "mw": 1500}
        return {"lat": h["lat"], "lon": h["lon"], "mw": h["mw"]}

    def result():
        """The hero's finished run (started once, polled to the end: the engine's runs first, Gemini's after)."""
        if "r" in memo:
            return memo["r"]
        j = ctx.request("POST", RUN, hero_body())
        assert j["status"] in ("running", "done"), f"status {j['status']}"
        t0 = time.time()
        while j["status"] == "running":
            assert time.time() - t0 < 170, "the operator run did not finish in 170 s"
            time.sleep(1.5)
            j = ctx.request("GET", f"/api/operator/jobs/{j['job']}")
            assert j["progress"].get("phase") in ("engine", "gemini", "done"), f"phase {j['progress']}"
        assert j["status"] == "done", f"status {j['status']}: {j.get('error')}"
        memo["r"] = j["result"]
        return memo["r"]

    def test_shape():
        r = result()
        assert "synthetic" in r["frame"].lower(), "not labeled synthetic"
        assert r["rules"] and r["limits"]["actions_per_step"] == 3, "rules or limits missing"
        for k in ("none", "engine"):
            run = r["runs"][k]
            assert run and run["steps"], f"{k}: no steps"
            for f in ("people_hit", "people_out", "lost_mw", "shed_mw", "tripped", "steps", "cost_high", "cost_low"):
                assert f in run["toll"], f"{k}: toll lacks {f}"
            assert run["toll"]["cost_low"] <= run["toll"]["cost_high"], f"{k}: cost low > high"
        assert r["verdict"]["winner"] in ("none", "engine", "gemini", "tie") and r["verdict"]["text"], "no verdict"
        assert isinstance(r["trace"], list) and r["trace"], "no trace"
        assert r["shown"] in ("engine", "gemini"), f"shown {r['shown']}"

    def test_none_is_the_cascade():
        r = result()
        c = ctx.request("POST", "/api/grid/cascade", hero_body())
        assert r["matches_cascade"] is True, f"the no-operator run differs from the cascade route: {r.get('mismatch')}"
        n = r["runs"]["none"]["toll"]
        assert n["people_hit"] == c["people_hit"], f"people hit {n['people_hit']:,} vs the cascade's {c['people_hit']:,}"
        assert n["tripped"] == sum(len(s["tripped"]) for s in c["steps"] if s["action"] == "trip"), "trips differ from the cascade route"
        assert abs(n["lost_mw"] - c["lost_mw"]) < 0.5, "MW lost differs from the cascade route"

    def test_shed_load_is_counted_and_balance_holds():
        r = result()
        for k, run in r["runs"].items():
            if not run:
                continue
            assert abs(run["balance_mw"]) < 0.01, f"{k}: redispatch does not balance ({run['balance_mw']} MW)"
            hit = run["toll"]["people_hit"]
            assert hit >= run["toll"]["people_out"] - 1, f"{k}: people hit {hit:,} < people still dark {run['toll']['people_out']:,}"
            for s in run["steps"]:
                for a in (s["operator"] or {}).get("applied") or []:
                    if a["type"] == "shed":
                        # the load an operator cuts is a deliberate blackout: everyone it serves is in the toll
                        assert a["people"] <= hit + 1, f"{k}: a cut serving {a['people']:,} people is not in the toll of {hit:,}"
                        assert a["mw"] >= 1, f"{k}: a cut of {a['mw']} MW"
            if run["actions"]["shed"]:
                assert run["toll"]["shed_mw"] > 0, f"{k}: cut load but shed_mw is 0"
            else:
                assert run["toll"]["shed_mw"] == 0, f"{k}: shed_mw {run['toll']['shed_mw']} with no cut"

    def test_engine_resolves_each_action():
        r = result()
        for k in ("engine", "gemini"):
            run = r["runs"].get(k)
            if not run:
                continue
            for s in run["steps"]:
                op = s["operator"]
                if op and op["applied"]:
                    assert isinstance(op["over_after"], list), f"{k}: step {s['n']} has moves but no re-solved state"
                    assert op["alarm"], f"{k}: step {s['n']} has moves but no alarm"
            assert run["turns"] <= r["limits"]["turns"], f"{k}: {run['turns']} turns"
            assert sum(1 for s in run["steps"] if (s["operator"] or {}).get("applied") and len(s["operator"]["applied"]) > r["limits"]["actions_per_step"]) == 0, f"{k}: more than 3 actions in a step"
        # the engine's operator plays every plan forward, doing nothing included: it never does worse than no operator
        assert r["runs"]["engine"]["toll"]["people_hit"] <= r["runs"]["none"]["toll"]["people_hit"], "the engine operator did worse than no operator"

    def test_gemini_or_labeled_fallback():
        r = result()
        if r["by"] == "gemini":
            assert r["runs"]["gemini"] and r["shown"] == "gemini", "by gemini but no Gemini run"
            assert r["function_calling"] is True and r["calls"] >= 1, "Gemini run without any function call"
            assert any(t["actor"] == "gemini" for t in r["trace"]), "a Gemini run whose trace has no Gemini row"
            assert any(t["actor"] == "engine" and t["kind"] in ("verify", "result") for t in r["trace"]), "no engine verdict in the trace"
        else:  # no key, no quota, no network: the engine's own operator, labeled
            assert r["by"] == "fallback" and r["shown"] == "engine" and r["runs"]["gemini"] is None, f"by {r['by']}, shown {r['shown']}"
            assert isinstance(r["why"], str) and r["why"], "a fallback with no reason"

    def test_cached_second_run():
        first = result()
        again = ctx.request("POST", RUN, hero_body())
        assert again["status"] == "done" and again["result"]["cached"] is True, "a finished case was not served from the cache"
        assert again["result"]["runs"]["none"]["toll"] == first["runs"]["none"]["toll"], "the cached run differs"

    def test_bad_bodies():
        ctx.request("POST", RUN, {"load_factor": 1.0}, expect=422)  # nothing to fight
        ctx.request("POST", RUN, {**hero_body(), "firm": True}, expect=422)  # the operator plays the flexible case
        ctx.request("POST", RUN, {"lat": 26.64, "lon": -81.87, "mw": 0}, expect=422)
        ctx.request("POST", RUN, {"lat": 26.64, "lon": -81.87, "mw": "lots"}, expect=422)
        ctx.request("POST", RUN, {**hero_body(), "region": "ZZ"}, expect=422)
        ctx.request("GET", "/api/operator/jobs/doesnotexist1", expect=404)

    ctx.check("operator: three runs, verdict, trace, synthetic label", test_shape)
    ctx.check("operator: no operator = the cascade route", test_none_is_the_cascade)
    ctx.check("operator: shed load counts as people; redispatch balances", test_shed_load_is_counted_and_balance_holds)
    ctx.check("operator: every action re-solved by the engine, 3 a step, never worse than nothing", test_engine_resolves_each_action)
    ctx.check("operator: Gemini through function calls, or a labeled engine fallback", test_gemini_or_labeled_fallback)
    ctx.check("operator: a finished case comes back from the cache", test_cached_second_run)
    ctx.check("operator: 422 on a bad body, 404 on an unknown job", test_bad_bodies)
