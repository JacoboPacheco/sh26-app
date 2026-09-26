"""Smoke checks for the plan race (backend/plan_agents.py): three Gemini planners race the engine on Strengthen's
finished study. Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200), auth(), expected.
Read-only: a race stores nothing but an in-memory cache and jobs.

Florida at 1,000 MW and today's load is the warm (or baked) study; the checks join it the way the unlock checks do
(peek, then the job) and never start a second one. The knee is recomputed here from the study's own plan; every plan
the referee judged is re-checked on the public routes (the what-if with every campus and upgrade: no line over its
rating that wasn't over with no campus; the cascade: nothing trips, nobody loses power)."""

import time

FL = {"region": "FL", "mw": 1000, "load_factor": 1.0}
JUMP = 2.5
POLL_S = 240
STUDY_S = 200
NOT_STUDIED = {"region": "VT", "mw": 4850, "load_factor": 0.87}  # a study nobody has run (LAZY: the race must not start one)
TOL = 2.0  # dollars: the referee's price against the plan's own running total


def _knee(plan):
    """The knee rule, written again from its docstring (plan_agents.knee_budget), on the study's own steps."""
    steps = plan["steps"]
    s = n = 0.0
    last = None
    for st in steps:
        c = float(st["cost"]["high"])
        if st["free"] or c <= 0.5:
            last = st
            continue
        if n and c > JUMP * (s / n):
            break
        s += c
        n += 1
        last = st
    return (int(last["n"]), float(last["cum_cost"]["high"])) if last else (int(plan.get("today") or 0), 0.0)


def register(ctx):
    state = {}

    def study():
        """Florida's finished study (warm or baked), joined, never started."""
        if "study" in state:
            return state["study"]
        t0 = time.time()
        while True:
            pk = ctx.request("GET", f"/api/unlock/peek?region=FL&mw=1000&load_factor=1.0")
            if pk["state"] == "done":
                break
            assert pk["state"] in ("queued", "running"), f"Florida's study isn't warm or baked here: {pk['state']}"
            assert time.time() - t0 < STUDY_S, "Florida's study didn't finish in time"
            time.sleep(2)
        job = ctx.request("GET", f"/api/unlock/jobs/{pk['id']}")
        assert job["status"] == "done", job["status"]
        state["study"] = job["result"]
        return state["study"]

    def knee_is_the_rule():
        r = study()
        plan = r["capacity"]["firm"]
        n, money = _knee(plan)
        k = ctx.request("GET", "/api/strengthen/knee?region=FL&mw=1000&load_factor=1.0&mode=firm")
        assert k["campuses"] == n and abs(k["budget"] - money) < 1, f"knee {k['campuses']} for {k['budget']}, the rule says {n} for {money}"
        assert k["campuses"] > plan["today"], "the knee is below the first upgrade"
        paid = [st for st in plan["steps"][:n] if not st["free"]]
        if k["next"]:
            avg = sum(st["cost"]["high"] for st in paid) / len(paid)
            nxt = plan["steps"][k["next"]["n"] - 1]
            assert nxt["cost"]["high"] > JUMP * avg, "the step after the knee isn't a jump"
        state["knee"] = k
        print(f"      knee (FL 1 GW, always on): {k['campuses']} campuses for ${k['budget'] / 1e6:.1f}M; {k['sentence']}")
        kf = ctx.request("GET", "/api/strengthen/knee?region=FL&mw=1000&load_factor=1.0&mode=flexible")
        nf, mf = _knee(r["capacity"]["flexible"])
        assert kf["campuses"] == nf and abs(kf["budget"] - mf) < 1, "the flexible knee isn't the rule's"

    def every_bar_can_be_beaten():
        """Every finished Florida study (the baked 0.5, 1, 2 and 5 GW): an agent's plan may go past the bar (Florida at
        500 MW, flexible: a bar of 14 over the what-if's 12), and the bar's sentence reads right ("1 campus at once";
        a jump printed as more than the rule's 2.5x)."""
        seen = 0
        for mw in (500, 1000, 2000, 5000):
            pk = ctx.request("GET", f"/api/unlock/peek?region=FL&mw={mw}&load_factor=1.0")
            if pk["state"] != "done":
                continue  # not baked or warm here: nothing is started (LAZY)
            res = ctx.request("GET", f"/api/unlock/jobs/{pk['id']}")["result"]
            if res.get("already_failing"):
                continue
            for mode in ("firm", "flexible"):
                plan = (res.get("capacity") or {}).get(mode) or {}
                if not any(not st["free"] for st in plan.get("steps") or []):
                    continue
                k = ctx.request("GET", f"/api/strengthen/knee?region=FL&mw={mw}&load_factor=1.0&mode={mode}")
                n = k["campuses"]
                assert (n, round(k["budget"])) == tuple(map(round, _knee(plan))), f"FL {mw} {mode}: the knee isn't the rule's"
                assert k["plan_max"] > n, f"FL {mw} MW {mode}: the bar is {n} campuses and an agent's plan stops at {k['plan_max']}: it can't be beaten"
                assert (f"{n} campus at once" if n == 1 else f"{n} campuses at once") in k["sentence"], k["sentence"]
                if k["next"]:
                    assert k["next"]["times"] > JUMP, f"FL {mw} {mode}: a jump printed as {k['next']['times']}x next to a rule of over {JUMP}x"
                seen += 1
        assert seen, "no finished Florida study to read"

    def lazy_and_validated():
        ctx.request("POST", "/api/strengthen/plan-race", {**NOT_STUDIED, "mode": "firm"}, expect=409)
        ctx.request("GET", "/api/strengthen/knee?region=VT&mw=4850&load_factor=0.87", expect=409)
        pk = ctx.request("GET", "/api/unlock/peek?region=VT&mw=4850&load_factor=0.87")
        assert pk["state"] == "none", f"the refused race started a study: {pk['state']}"
        ctx.request("POST", "/api/strengthen/plan-race", {**FL, "mode": "sideways"}, expect=422)
        ctx.request("POST", "/api/strengthen/plan-race", {"region": "US", "mw": 1000, "load_factor": 1.0}, expect=422)
        ctx.request("GET", "/api/strengthen/plan-race/not-a-race-id-00", expect=404)

    def finish(start):
        t0 = time.time()
        r = ctx.request("GET", f"/api/strengthen/plan-race/{start['id']}")
        while r["status"] == "running":
            assert time.time() - t0 < POLL_S, f"the race was still running after {POLL_S} s"
            assert len(r["lanes"]) == 4 and all(isinstance(ln["trace"], list) for ln in r["lanes"]), "a running view without its four lanes"
            time.sleep(2)
            r = ctx.request("GET", f"/api/strengthen/plan-race/{start['id']}")
        assert r["status"] == "done", f"the race ended {r['status']}: {r.get('error')}"
        return r["result"]

    def recheck(plan, verdict):
        """The referee's verdict against the public routes (every campus and upgrade at once)."""
        sites = [{"lat": p["lat"], "lon": p["lon"], "mw": FL["mw"]} for p in plan["placements"]]
        ups = {str(p["branch_id"]): p["rating_after_mva"] for p in plan["projects"]}
        w = ctx.request("POST", "/api/grid/whatif", {"region": "FL", "load_factor": 1.0, "sites": sites, "upgrades": ups})
        got = [s["sub"] for s in w["sites"]]
        assert got == [p["id"] for p in plan["placements"]], f"the what-if snapped the campuses elsewhere: {got}"
        new_over = [o for o in w["overloaded"] if o["id"] not in state["base_over"]]
        holds = not new_over and w["lost_mw"] <= 0.5
        assert holds == bool(verdict["holds"]), f"{plan['name']}: the referee says holds={verdict['holds']}, the what-if has {len(new_over)} lines over"
        assert abs(sum(p["cost"]["high"] for p in plan["projects"]) - verdict["cost"]["high"]) <= TOL * max(1, len(plan["projects"])), "the plan's upgrades don't add up to its price"
        if holds:
            c = ctx.request("POST", "/api/grid/cascade", {"region": "FL", "load_factor": 1.0, "sites": sites, "upgrades": ups})
            calm = c["total_steps"] == 0 and int(c.get("people_hit", c.get("people", 0)) or 0) == 0
            assert calm == bool(verdict["calm"]), f"{plan['name']}: the referee says calm={verdict['calm']}, the cascade has {c['total_steps']} steps"

    def race_with_four_competitors():
        k = state.get("knee") or ctx.request("GET", "/api/strengthen/knee?region=FL&mw=1000&load_factor=1.0&mode=firm")
        base = ctx.request("POST", "/api/grid/whatif", {"region": "FL", "load_factor": 1.0})
        state["base_over"] = {o["id"] for o in base["overloaded"]}
        start = ctx.request("POST", "/api/strengthen/plan-race", {**FL, "mode": "firm"})
        assert start["status"] in ("running", "done"), start
        res = finish(start)
        lanes = res["lanes"]
        assert [ln["id"] for ln in lanes] == ["engine", "cheapest", "corridors", "flexible"], [ln["id"] for ln in lanes]
        assert abs(res["knee"]["budget"] - k["budget"]) < 1, "the race's bar isn't the knee"
        eng = lanes[0]
        assert eng["verdict"]["status"] == "verified", f"the engine's own plan failed the referee: {eng['verdict'].get('reason')}"
        assert eng["plan"]["campuses"] == k["campuses"] and abs(eng["verdict"]["cost"]["high"] - k["budget"]) <= TOL, "the engine's plan isn't its plan cut at the knee"
        for ln in lanes:
            v = ln.get("verdict")
            if ln["status"] == "offline":
                assert v is None and ln["why"], f"{ln['name']}: offline without its reason"
                continue
            assert v and v["status"] in ("verified", "over_budget", "failed", "empty"), f"{ln['name']}: no verdict"
            assert ln["trace"] and all({"n", "actor", "kind", "title"} <= set(r) for r in ln["trace"]), f"{ln['name']}: trace rows without their shape"
            if v["status"] == "verified":
                assert v["cost"]["high"] <= res["knee"]["budget"] + TOL, f"{ln['name']}: verified over the budget"
            p = ln.get("plan")
            if p and p["campuses"] and p["campuses"] <= 12 and v["status"] != "empty":
                recheck(p, v)
        board = res["leaderboard"]
        assert len(board) == 4 and board[0]["lane"] == res["winner"], "the leaderboard doesn't lead with the winner"
        win = next(ln for ln in lanes if ln["id"] == res["winner"])
        assert win["verdict"]["status"] == "verified", "a winner the referee didn't verify"
        if win["by"] == "gemini":
            e = eng["verdict"]
            w = win["verdict"]
            assert w["campuses"] > e["campuses"] or w["cost"]["high"] < e["cost"]["high"], "a Gemini winner that doesn't beat the engine"
        for r in board:
            if r["verified"] and r["lane"] != res["winner"]:
                assert r["campuses"] <= board[0]["campuses"], "a verified plan with more campuses ranked below the winner"
            if r["outcome"] == "matched":
                e = next(x for x in board if x["by"] == "engine")
                assert r["place"] == e["place"] and r["tied"], f"{r['name']} matched the engine's plan but is ranked {r['place']}, the engine {e['place']}"
        places = [r["place"] for r in board if r["place"] is not None]
        assert places == sorted(places), f"the leaderboard's places aren't in order: {places}"
        for p in set(places):
            same = [r for r in board if r["place"] == p]
            assert all(r["tied"] for r in same) == (len(same) > 1), f"place {p}: shared by {len(same)} plans, tied flags {[r['tied'] for r in same]}"
        for p in places:
            assert p == places.index(p) + 1, f"places aren't competition-ranked (1, 1, 3): {places}"
        assert "synthetic" in res["note"].lower() and res["sentence"], "no honesty note or sentence"
        again = ctx.request("POST", "/api/strengthen/plan-race", {**FL, "mode": "firm"})
        assert again["status"] == "done" and again["cached"], "the same race again isn't the cached result"
        print(f"      race: {res['sentence']}")

    def fallback_without_gemini():
        start = ctx.request("POST", "/api/strengthen/plan-race", {**FL, "mode": "firm", "ai": False})
        res = finish(start)
        assert res["fallback"] and res["why"], "ai=false isn't labeled as the plain version"
        assert res["winner"] == "engine", res["winner"]
        for ln in res["lanes"][1:]:
            assert ln["status"] == "offline" and ln["verdict"] is None and ln["why"], f"{ln['name']} ran without Gemini"
        assert res["lanes"][0]["verdict"]["status"] == "verified"
        assert "engine" in res["sentence"].lower(), res["sentence"]

    ctx.check("plan race: the budget bar is the knee of the engine's cost curve", knee_is_the_rule)
    ctx.check("plan race: every finished Florida study's bar can be beaten, and its sentence reads right", every_bar_can_be_beaten)
    ctx.check("plan race: 409 without a finished study (starts none), 422/404 on bad input", lazy_and_validated)
    ctx.check("plan race: four competitors, every verdict matches an independent re-check", race_with_four_competitors)
    ctx.check("plan race: without Gemini, the engine's plan alone, labeled", fallback_without_gemini)
