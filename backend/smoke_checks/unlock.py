"""Smoke checks for Strengthen the grid (owned by the unlock track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).

Read-only: a study stores nothing (an in-memory job and cache). One Florida study at 1,000 MW runs as a
background job (~10 s on a laptop, bounded at 60 s plus the Gemini step; the server warms it a few seconds
after startup, so this usually joins that job or finds it cached); the checks poll it, then re-run what it
claims through the public cascade route: a site the plan unlocks must overload before and hold with the
plan's upgrades. The Strengthen page's fields (plain names, per-step strain and "biggest blackouts gone",
the budget's running cost) and the peek route (shows a study without starting one) are checked too, and so is Gemini's
try at beating the capacity plan (capacity_ai.py): published as "pending" without holding the study back, then a
known status in the same job; a win only when the engine verified it, re-run here through the cascade route; savings
only for a verified plan; with no key, not_configured and copy that claims nothing."""

import math
import time

MW = 1000
DONE_S = 150  # the study's own bound (60 s on Florida) + Gemini + a slow host
PENDING_S = 150  # Gemini's challenge lands after the study is published: two rounds of Gemini + the engine's checks
POLL_S = 1.5
AI_STATUS = {"used", "not_configured", "offline", "none_verified", "skipped", "error"}
CAP_AI_STATUS = {"beat", "matched", "lost", "not_configured", "offline", "error", "skipped"}


def register(ctx):
    state = {}

    def validation():
        for body, what in (
            ({"region": "FL", "mw": 50}, "a size below the range"),
            ({"region": "FL", "mw": 60000}, "a size above the range"),
            ({"region": "US", "mw": 1000}, "the national map"),
            ({"region": "ZZ", "mw": 1000}, "an unknown state"),
            ({"region": "FL", "mw": 1000, "load_factor": 3}, "a load level out of range"),
        ):
            r = ctx.request("POST", "/api/unlock/start", body, expect=422)
            assert r.get("detail"), f"no readable reason for {what}"
        ctx.request("GET", "/api/unlock/jobs/AAAAAAAAAAAAAAAA", expect=404)
        ctx.request("GET", "/api/unlock/jobs/x", expect=422)

    def study():
        start = ctx.request("POST", "/api/unlock/start", {"region": "FL", "mw": MW, "load_factor": 1.0})
        assert start["status"] in ("queued", "running", "done") and start["id"], start
        t0 = time.monotonic()
        while True:
            s = ctx.request("GET", f"/api/unlock/jobs/{start['id']}")
            assert s["region"] == "FL" and s["mw"] == MW, s
            assert s["progress"]["message"], s["progress"]
            if s["status"] == "done":
                break
            assert s["status"] in ("queued", "running"), s.get("error") or s["status"]
            if s["partial"]["sites"]:
                assert all(math.isfinite(x["lat"]) and math.isfinite(x["lon"]) for x in s["partial"]["sites"])
            assert time.monotonic() - t0 < DONE_S, f"not done after {DONE_S} s: {s['progress']}"
            time.sleep(POLL_S)
        r = s["result"]
        state["r"] = r
        state["id"] = start["id"]
        assert r["synthetic"] is True and "Synthetic grid model" in r["note"], r["note"]
        assert r["already_failing"] is False
        assert r["region"] == "FL" and r["mw"] == MW
        n = r["sites_total"]
        assert n >= 100 and len(r["sites"]) == n, (n, len(r["sites"]))
        # the structural points: ranked, placed, explained
        pts = r["points"]
        assert 5 <= len(pts) <= 20, len(pts)
        assert [p["rank"] for p in pts] == list(range(1, len(pts) + 1))
        imp = [p["importance"] for p in pts]
        assert imp == sorted(imp, reverse=True) and imp[0] == 1.0 and imp[-1] > 0, imp
        for p in pts:
            assert p["kind"] in ("line", "transformer") and p["reason"].endswith("."), p
            for end in (p["from"], p["to"]):
                assert math.isfinite(end["lat"]) and math.isfinite(end["lon"]) and end["name"], end
            assert p["sites_blocked"] + p["first_fail"] + p["trips"] > 0, p
        # the plan: cheapest first, every step verified by the engine, the numbers add up
        steps = r["steps"]
        assert steps, "no upgrade unlocks any site"
        ok = r["before"]["sites_ok"]
        cum = 0
        for st in steps:
            assert st["by"] == "engine" and st["projects"], st["n"]
            assert st["verified"] + st["unverified"] == st["newly_count"], st
            assert st["sites_ok"] == ok + st["newly_count"], (st["n"], st["sites_ok"], ok, st["newly_count"])
            ok = st["sites_ok"]
            cum += st["cost"]["high"]
            assert abs(st["cum_cost"]["high"] - cum) <= len(steps), (st["cum_cost"], cum)
            # a step's cost is the change in the plan's low and high totals (a second raise of one line can move
            # its low end more than its high end: re-conductored before, a new line now); the totals stay ordered
            assert st["cost"]["low"] >= 0 and st["cost"]["high"] >= 0 and st["cum_cost"]["low"] <= st["cum_cost"]["high"], st["cost"]
            for pj in st["projects"]:
                for k in ("id", "name", "kind", "from", "to", "rating_before_mva", "rating_after_mva", "cost", "owner", "window", "geometry"):
                    assert k in pj, f"project record without {k}"
                assert pj["window"] is None and pj["owner"] and pj["by"] == "engine", pj
                assert pj["rating_original_mva"] <= pj["rating_before_mva"] < pj["rating_after_mva"] <= 5 * pj["rating_original_mva"] + 0.5, pj  # (ratings are rounded to 0.1)
                assert pj["cost"]["low"] >= 0 and pj["cost"]["high"] >= 0, pj["cost"]
                assert pj["geometry"]["coords"] and all(len(c) == 2 for c in pj["geometry"]["coords"]), pj["geometry"]
        assert sum(s["verified"] for s in steps) >= 1, "no unlocked site was verified by the engine"
        h = r["headline"]
        more = sum(s["newly_count"] for s in steps)
        assert h["more_sites"] == more == r["after"]["sites_ok"] - r["before"]["sites_ok"], (h, r["before"], r["after"])
        assert abs(h["gw"] - more * MW / 1000) < 0.05 and h["upgrades"] == steps[-1]["cum_upgrades"], h
        assert abs(h["cost_high"] - steps[-1]["cum_cost"]["high"]) <= len(steps) + 1, (h["cost_high"], steps[-1]["cum_cost"])
        assert 0 < h["cost_low"] <= h["cost_high"], h
        assert r["after"]["worst"]["people_hit"] <= r["before"]["worst"]["people_hit"], (r["before"]["worst"], r["after"]["worst"])
        # the Strengthen page: plain names, the budget's running cost, per-step strain and the biggest blackouts gone
        for x in pts + [pj for st in steps for pj in st["projects"]]:
            assert x["short"] and not x["short"].startswith("the ") and x["short"].endswith(("line", "transformer", ")")), x["short"]
            assert x["where"], x
        highs = [st["cum_cost"]["high"] for st in steps]
        assert highs == sorted(highs), "the plan's running cost goes down somewhere (the budget slices it)"
        prev = r["headline"]["strain"]["line_overloads_before"]
        for st in steps:
            assert st["overloads_before"] == prev, (st["n"], st["overloads_before"], prev)
            prev = st["overloads_left"]
        gone = [st["biggest_gone"] for st in steps]
        assert gone == sorted(gone) and 0 <= gone[-1] <= r["before"]["blackout_sites"], gone
        ranked = sorted((x for x in r["sites"] if (x["hit0"] or 0) > 0), key=lambda x: -x["hit0"])
        lead = 0
        while lead < len(ranked) and ranked[lead]["unlocked_at"] is not None:
            lead += 1
        # (ties in people hit can order either way; the count is the same unless a tie straddles the cut)
        assert abs(gone[-1] - lead) <= 1, (gone[-1], lead)
        # the headline in siting language, and the strain from the study's own numbers
        assert h["mw_unlocked"] == more * MW and f"{more:,} more sites can host {MW:,} MW" in h["sentence"], h["sentence"]
        # the summed figure is site options, never capacity that connects together: the visible text says so
        assert "of site options" in h["sentence"] and "each site tested alone, not all at once" in h["sentence"], h["sentence"]
        assert " unlocked" not in h["sentence"], h["sentence"]
        assert "not all at once" in h["mw_unlocked_note"] and "together" in h["mw_unlocked_note"], h["mw_unlocked_note"]
        sn = h["strain"]
        assert sn["blackout_sites_before"] == r["before"]["blackout_sites"] and sn["blackout_sites_after"] == r["after"]["blackout_sites"], sn
        assert sn["worst_people_before"] == r["before"]["worst"]["people_hit"] and sn["worst_people_after"] == r["after"]["worst"]["people_hit"], sn
        assert 0 <= sn["line_overloads_after"] < sn["line_overloads_before"] and sn["line_overloads_after"] == steps[-1]["overloads_left"], sn
        left = [s["overloads_left"] for s in steps]
        assert left == sorted(left, reverse=True) and left[0] < sn["line_overloads_before"], "a step raised the overload count"
        assert all(s["mw_unlocked"] == (s["sites_ok"] - r["before"]["sites_ok"]) * MW for s in steps), "mw_unlocked per step"
        assert sn["sentence"].startswith("Strain") and f"{sn['blackout_sites_before']:,} to {sn['blackout_sites_after']:,}" in sn["sentence"], sn["sentence"]
        lr = r["learned"]
        assert lr["cascades"] >= 1 and lr["solves"] >= lr["cascades"] and lr["sites"] == n, lr
        assert all(s["name"] and s["url"].startswith("https://") for s in r["sources"]), r["sources"]
        # the AI: whatever it proposed, only engine-verified bundles are listed
        ai = r["ai"]
        assert ai["status"] in AI_STATUS, ai["status"]
        for b in ai["bundles"]:
            assert b["by"] == "gemini" and b["verified"] >= 1 and b["more_sites"] >= 1, b
            assert all(pj["by"] == "gemini" and pj["window"] is None for pj in b["projects"]), b["name"]

    def engine_agrees():
        r = state.get("r")
        assert r, "the study didn't run"
        st = r["steps"][0]
        site = st["newly"][0]
        ups = {}
        for s in r["steps"][: st["n"]]:
            for pj in s["projects"]:
                ups[str(pj["branch_id"])] = pj["rating_after_mva"]
        case = {"lat": site["lat"], "lon": site["lon"], "mw": MW}
        before = ctx.request("POST", "/api/grid/cascade", case)
        assert before["total_steps"] > 0, f"{site['area']}: expected the campus to overload a line before the upgrades"
        after = ctx.request("POST", "/api/grid/cascade", {**case, "upgrades": ups})
        assert after["total_steps"] == 0 and int(after.get("people_hit", after["people"]) or 0) == 0, (site["area"], after["total_steps"])
        assert after["lost_mw"] <= 0.5 and not after["site_cut_off"], (site["area"], after["lost_mw"])

    def cached_rerun():
        t0 = time.monotonic()
        again = ctx.request("POST", "/api/unlock/start", {"region": "FL", "mw": MW + 10, "load_factor": 1.0})  # rounds to the same 50 MW step
        assert again["cached"] is True and again["status"] == "done", again
        s = ctx.request("GET", f"/api/unlock/jobs/{again['id']}")
        assert s["status"] == "done" and s["result"]["headline"] == state["r"]["headline"], "the cached study differs"
        assert time.monotonic() - t0 < 10, "a cached study should come back at once"

    def peek():
        ctx.request("GET", "/api/unlock/peek?region=US&mw=1000", expect=422)
        ctx.request("GET", "/api/unlock/peek?region=FL&mw=50", expect=422)
        # the finished Florida study shows at once (the `study` check above cached it). `warm` says whether the
        # server warms it at startup: true locally, false on Render (render.yaml sets UNLOCK_WARM=0), so only its type is checked
        pk = ctx.request("GET", "/api/unlock/peek?region=FL&mw=1000&load_factor=1")
        assert pk["state"] == "done" and pk["id"] and isinstance(pk["warm"], bool), pk
        assert pk["sites"] == state["r"]["sites_total"] and pk["estimate_s"] > 0, pk
        s = ctx.request("GET", f"/api/unlock/jobs/{pk['id']}")
        assert s["status"] == "done" and s["result"]["headline"] == state["r"]["headline"], "the peeked study differs"
        # elsewhere a peek starts nothing (LAZY): the page offers a button with the time it takes
        q = "/api/unlock/peek?region=RI&mw=450&load_factor=1"
        a = ctx.request("GET", q)
        assert a["estimate_s"] > 0 and a["sites"] > 0 and a["warm"] is False, a
        if a["state"] == "none":
            time.sleep(1.0)
            b = ctx.request("GET", q)
            assert b["state"] == "none" and b["id"] is None, f"a peek started a study: {b}"

    def capacity():
        # campuses AT ONCE (backend/capacity.py): ordered steps, costs that add up, the last set verified calm, and
        # the public cascade route agrees: today's campuses plus the first upgraded one, all together, trip nothing
        c = state["r"]["capacity"]
        assert c and c["synthetic"] is True and c["mw"] == MW, c and c.get("mw")
        for mode in ("firm", "flexible"):
            m = c[mode]
            assert m["stop"] in ("plants", "no_fix", "cap", "max", "time"), m["stop"]
            assert m["steps"] and [st["n"] for st in m["steps"]] == list(range(1, len(m["steps"]) + 1)), mode
            hi = 0
            for st in m["steps"]:
                hi += st["cost"]["high"]
                assert abs(st["cum_cost"]["high"] - hi) <= 2 * st["n"], (mode, st["n"], st["cum_cost"], hi)
                assert st["free"] == (not st["projects"]), (mode, st["n"])
                assert st["free"] or (st["cost"]["high"] > 0 and st["blocked_by"]), (mode, st["n"])
                assert st["busiest_pct"] <= 100.0 + 1e-6, (mode, st["n"], st["busiest_pct"])
            assert m["verified"] and m["verified"]["calm"] is True and m["verified"]["campuses"] == len(m["steps"]), m["verified"]
        assert c["firm"]["today"] >= 1 and c["flexible"]["today"] >= c["firm"]["today"], (c["firm"]["today"], c["flexible"]["today"])
        pl = c["plants"]
        assert 0 <= pl["room_mw"] <= pl["spare_mw"] and pl["reserve_pct"] == 15.0, pl
        assert any("NERC" in x["name"] for x in c["sources"]) and any("Rethinking Load Growth" in x["name"] for x in c["sources"])
        steps = c["firm"]["steps"]
        k = next((i for i, st in enumerate(steps) if not st["free"]), None)
        if k is None:
            return
        chosen = steps[: k + 1]
        ups = {}
        for st in chosen:
            for pj in st["projects"]:
                ups[str(pj["branch_id"])] = max(ups.get(str(pj["branch_id"]), 0), pj["rating_after_mva"])
        main, rest = chosen[0]["site"], chosen[1:]
        case = {"region": "FL", "lat": main["lat"], "lon": main["lon"], "mw": MW, "load_factor": 1.0,
                "sites": [{"lat": st["site"]["lat"], "lon": st["site"]["lon"], "mw": MW} for st in rest]}
        before = ctx.request("POST", "/api/grid/cascade", case)
        assert before["total_steps"] > 0 or before["lost_mw"] > 0.5, "the upgraded campus should not fit before its upgrade"
        after = ctx.request("POST", "/api/grid/cascade", {**case, "upgrades": ups})
        assert after["total_steps"] == 0 and int(after.get("people_hit", after["people"]) or 0) == 0, (len(chosen), after["total_steps"])

    def capacity_ai():
        # Gemini tries to beat the capacity plan (backend/capacity_ai.py): a known status; a win only when the engine
        # verified it, and then the public cascade route agrees (every campus and raise at once: nothing trips) and it
        # really beats the engine's plan for the money; with no key the copy says so and claims nothing
        c = state["r"]["capacity"]
        ai = c.get("ai")
        if ai is not None and ai["status"] == "pending":
            # the study was published without waiting for Gemini: the bar and a plain "trying" line, nothing claimed;
            # the verdict lands in the same job
            assert ai["verified"] is False and not ai["trace"] and not ai["placements"] and ai["bar"]["campuses"] >= 1, ai
            assert "Gemini is trying to beat this plan" in ai["sentence"], ai["sentence"]
            t0 = time.monotonic()
            while ai["status"] == "pending":
                assert time.monotonic() - t0 < PENDING_S, f"Gemini's challenge still pending after {PENDING_S} s"
                time.sleep(POLL_S)
                s = ctx.request("GET", f"/api/unlock/jobs/{state['id']}")
                assert s["status"] == "done", s["status"]
                assert s["result"]["headline"] == state["r"]["headline"], "the study changed while Gemini's challenge landed"
                c = s["result"]["capacity"]
                ai = c.get("ai")
        assert ai is not None and ai["status"] in CAP_AI_STATUS, ai and ai.get("status")
        assert ai["synthetic"] is True and isinstance(ai["trace"], list) and isinstance(ai["attempts"], list), ai.keys()
        assert ai["verified"] == (ai["status"] in ("beat", "matched")), (ai["status"], ai["verified"])
        firm = c["firm"]
        configured = ctx.request("GET", "/api/ai/status")["configured"]
        if not configured:
            assert ai["status"] in ("not_configured", "skipped"), f"no key, but the challenge says {ai['status']}"
            assert ai["calls"] == 0 and not ai["trace"] and not ai["placements"], ai["calls"]
            assert not ai["sentence"] or ("isn't set up" in ai["sentence"] and "the engine's plan stands" in ai["sentence"]), ai["sentence"]
            return
        if ai["status"] == "skipped":
            return
        # the bar: the page's default budget (the largest paid step at or under $50M) and what it connects
        stops = [0]
        for st in firm["steps"]:
            if not st["free"] and st["cum_cost"]["high"] > stops[-1] + 0.5:
                stops.append(st["cum_cost"]["high"])
        within = [v for v in stops if v <= 50e6]
        budget = within[-1] if len(within) > 1 else stops[1]

        def eng_within(money):
            n = 0
            for st in firm["steps"]:
                if st["cum_cost"]["high"] > money + 0.5:
                    break
                n = st["n"]
            return n

        bar_n = eng_within(budget)
        assert ai["bar"]["campuses"] == bar_n and abs(ai["bar"]["cost"]["high"] - firm["steps"][bar_n - 1]["cum_cost"]["high"]) <= 1, (ai["bar"], bar_n)
        for i, row in enumerate(ai["trace"], 1):
            assert row["n"] == i and row["actor"] in ("gemini", "engine") and row["kind"] and row["title"], row
            # Gemini's own cost figures are shown only as its own words, never as a figure the page stands behind
            if row["actor"] == "gemini" and "$" in (row.get("detail") or ""):
                assert "not used" in row["detail"], row["detail"]
        if ai["status"] in ("lost", "offline", "error"):
            assert "didn't beat" in ai["sentence"] or "stands" in ai["sentence"], ai["sentence"]
            assert not any(a["outcome"] in ("beat", "matched") for a in ai["attempts"]), ai["attempts"]
            assert ai.get("savings_high") is None and ai.get("engine_same_money") is None, "savings reported for a plan the engine didn't verify"
            return
        # beat or matched: the plan the page shows, re-checked through the public routes
        n = ai["campuses"]
        cost = ai["cost"]["high"]
        assert n == len(ai["placements"]) >= bar_n and len({x["id"] for x in ai["placements"]}) == n, (n, ai["placements"])
        assert abs(cost - sum(pj["cost"]["high"] for pj in ai["projects"])) <= len(ai["projects"]) + 1, (cost, [pj["cost"] for pj in ai["projects"]])
        for pj in ai["projects"]:
            assert pj["by"] == "gemini" and pj["rating_original_mva"] < pj["rating_after_mva"] <= 5 * pj["rating_original_mva"] + 0.5, pj
            on_step = abs(pj["rating_after_mva"] / 50 - round(pj["rating_after_mva"] / 50)) < 1e-6
            assert on_step or pj["rating_after_mva"] >= 5 * pj["rating_original_mva"] - 0.5, f"{pj['short']}: {pj['rating_after_mva']} MVA is not a 50 MVA step"
        ec = firm["steps"][n - 1]["cum_cost"]["high"] if n <= len(firm["steps"]) else None
        if ai["status"] == "beat":
            # more campuses than the engine's plan connects with the same money, by more than the rounding margin
            assert eng_within(cost) < n, (n, cost, eng_within(cost))
            assert ec is None or cost < ec - max(0.02 * ec, 250_000) + 1, (cost, ec)
        else:
            assert ec is not None and abs(cost - ec) <= max(0.02 * ec, 250_000) + 1, (cost, ec)
        if n <= 12:
            main, rest = ai["placements"][0], ai["placements"][1:]
            case = {"region": "FL", "lat": main["lat"], "lon": main["lon"], "mw": MW, "load_factor": 1.0,
                    "sites": [{"lat": x["lat"], "lon": x["lon"], "mw": MW} for x in rest],
                    "upgrades": {str(pj["branch_id"]): pj["rating_after_mva"] for pj in ai["projects"]}}
            after = ctx.request("POST", "/api/grid/cascade", case)
            assert after["total_steps"] == 0 and int(after.get("people_hit", after["people"]) or 0) == 0, (n, after["total_steps"])
            assert after["lost_mw"] <= 0.5, after["lost_mw"]

    ctx.check("unlock: bad sizes, the national map, unknown states and jobs are refused", validation)
    ctx.check("unlock: Florida at 1,000 MW finds weak points and a verified plan", study)
    ctx.check("unlock: the cascade route agrees a site the plan unlocks now holds", engine_agrees)
    ctx.check("unlock: the same study again comes from the cache", cached_rerun)
    ctx.check("unlock: peek shows the finished Florida study at once and starts nothing elsewhere", peek)
    ctx.check("unlock: capacity — campuses at once, costs that add up, verified calm, and the cascade route agrees", capacity)
    ctx.check("unlock: Gemini's try at beating the capacity plan — a known status, a win only when the engine and the cascade route agree", capacity_ai)
