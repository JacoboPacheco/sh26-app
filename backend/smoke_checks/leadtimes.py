"""Smoke checks for time to power on the Strengthen meter (backend/leadtimes.py). Loaded by smoke_test.py.

Read-only. GET /api/unlock/time-to-power reads the CACHED study only (LAZY): a state/size/load nobody has studied is a
404 and stays unstudied (peek still says "none"); the national map and out-of-range values are 422. For Florida at
1,000 MW (baked or warmed; joined here like the unlock checks do, never a second run) every campus of the always-on
and flexible plans gets a range: lo <= hi, known items, sources with links, never sooner than the campus before it
(each campus waits for the upgrades before it), "study and build" only for a campus that needs no upgrade and has
power-plant room, new plants past the plants' reserve line, a transformer or line wait when its upgrades include one;
each raised line's kind follows how far it went (up to 2x its original rating: a line upgrade; past 2x: doubled on its
corridor, 3-5+ years; a new line, 5-15 years, ONLY past 4x: Florida's 1-mile lines doubled are no greenfield
transmission); ties on the range name the same item every time (the fixed rank); flexible is "sooner" only when its
own range is. The words say "typical" and "synthetic"."""

import time

MW = 1000
DONE_S = 150
POLL_S = 1.5
ITEMS = {"connect", "line", "transformer", "line_doubled", "new_line", "generation"}


def _line_kinds(raised: dict) -> set:
    """The kinds the raised lines so far should count as (backend/leadtimes.py _line_kind, restated)."""
    out = set()
    for orig, top, priced_new in raised.values():
        if orig and top > 4 * orig + 1e-6:
            out.add("new_line")
        elif priced_new or (orig and top > 2 * orig + 1e-6):
            out.add("line_doubled")
        else:
            out.add("line")
    return out


def register(ctx):
    def validation():
        for q, what in (
            ("region=US&mw=1000", "the national map"),
            ("region=FL&mw=50", "a size below the range"),
            ("region=FL&mw=60000", "a size above the range"),
            ("region=FL&mw=1000&load_factor=3", "a load level out of range"),
            ("region=ZZ&mw=1000", "an unknown state"),
        ):
            r = ctx.request("GET", f"/api/unlock/time-to-power?{q}", expect=422)
            assert r.get("detail"), f"no readable reason for {what}"

    def lazy():
        # a size and load level nobody studies in a smoke run: nothing cached, and asking doesn't start a study (Florida,
        # whose model is loaded anyway: peek's estimate loads the state's model, and the free tier's memory is small)
        q = "region=FL&mw=750&load_factor=0.61"
        r = ctx.request("GET", f"/api/unlock/time-to-power?{q}", expect=404)
        assert "run it" in r["detail"].lower(), r
        p = ctx.request("GET", f"/api/unlock/peek?{q}")
        assert p["state"] == "none" and p["id"] is None, f"time to power started a study: {p}"

    def florida():
        start = ctx.request("POST", "/api/unlock/start", {"region": "FL", "mw": MW, "load_factor": 1.0})
        t0 = time.monotonic()
        while True:
            s = ctx.request("GET", f"/api/unlock/jobs/{start['id']}")
            if s["status"] == "done":
                break
            assert s["status"] in ("queued", "running"), s.get("error") or s["status"]
            assert time.monotonic() - t0 < DONE_S, f"the study did not finish in {DONE_S} s"
            time.sleep(POLL_S)
        cap = s["result"].get("capacity")
        assert cap and cap["firm"]["steps"], "the Florida study has no capacity plan"
        t = ctx.request("GET", f"/api/unlock/time-to-power?region=FL&mw={MW}&load_factor=1.0")
        assert t["region"] == "FL" and t["mw"] == MW and t["synthetic"] is True, t.get("region")
        note = t["note"].lower()
        assert "typical" in note and "varies" in note and "synthetic" in note, t["note"]
        assert set(t["items"]) == ITEMS, t["items"].keys()
        for k, it in t["items"].items():
            assert 0 < it["lo"] <= it["hi"], (k, it)
            assert it["short"] and it["label"] and it["basis"], k
            assert it["sources"], f"{k} has no source"
            for sid in it["sources"]:
                src = t["sources"].get(sid)
                assert src and src["name"] and src["url"].startswith("https://"), f"{k}: source {sid} missing"
        for sid in t["flex_sources"]:
            assert t["sources"][sid]["url"].startswith("https://"), sid
        items = t["items"]
        ranks = [it["rank"] for it in items.values()]
        assert len(set(ranks)) == len(ranks), f"tie-break ranks aren't distinct: {ranks}"

        def order(k):
            return (items[k]["hi"], items[k]["lo"], items[k]["rank"])

        assert t["used"] == sorted(t["used"], key=order), t["used"]
        plants = cap.get("plants") or {}
        for plan, pn in (("firm", plants.get("campuses_firm")), ("flexible", plants.get("campuses_flexible"))):
            steps = cap[plan]["steps"]
            cs = t[plan]["campuses"]
            assert len(cs) == len(steps), f"{plan}: {len(cs)} campuses timed, {len(steps)} in the plan"
            prev = (0, 0)
            seen = set()
            raised = {}  # line -> [original rating, highest rating so far, priced as a new line]
            for c, st in zip(cs, steps):
                for p in st["projects"]:
                    if p.get("kind") == "transformer":
                        continue
                    r = raised.setdefault(p.get("branch_id", p.get("id")), [p.get("rating_original_mva") or p.get("rating_before_mva") or 0, 0.0, False])
                    r[1] = max(r[1], float(p.get("rating_after_mva") or 0))
                    r[2] = r[2] or "new line" in str((p.get("cost") or {}).get("method") or "")
                want = _line_kinds(raised)
                wf = set(c["waits_for"])
                assert want <= wf, f"{plan} {c['n']}: the raised lines count as {sorted(want)}, the campus waits for {sorted(wf)}"
                assert ("new_line" in wf) == ("new_line" in want), f"{plan} {c['n']}: a new line (5-15 years) though no line passed four times its rating"
                assert c["item"] == max({"connect", *wf}, key=order), f"{plan} {c['n']}: {c['item']} named, not the slowest by (end, start, rank)"
                assert c["waits_for"] == sorted(c["waits_for"], key=order), f"{plan} {c['n']}: waits_for out of order"
                assert c["n"] == st["n"], (plan, c["n"], st["n"])
                assert c["item"] in ITEMS and c["lo"] <= c["hi"], (plan, c)
                assert (c["lo"], c["hi"]) == (items[c["item"]]["lo"], items[c["item"]]["hi"]), f"{plan} {c['n']}: its range isn't its item's"
                assert c["from_year"] <= c["to_year"], (plan, c)
                if c["item"] != "connect":  # what sets it: an upgrade made for this campus or one before it, or the plants' line
                    lim = c["limit"]
                    assert lim and 1 <= lim["n"] <= c["n"], f"{plan} {c['n']}: nothing named for {c['item']}"
                    assert c["item"] == "generation" or lim["name"], f"{plan} {c['n']}: an unnamed upgrade"
                assert (c["hi"], c["lo"]) >= prev, f"{plan} campus {c['n']} is sooner than the campus before it"
                prev = (c["hi"], c["lo"])
                for p in st["projects"]:
                    seen.add(p["kind"])
                gen = pn is not None and c["n"] > pn
                if gen:
                    assert c["hi"] >= items["generation"]["hi"] and "generation" in c["waits_for"], f"{plan} {c['n']} past the plants' line without new plants"
                if "transformer" in seen:
                    assert c["hi"] >= items["transformer"]["hi"] and c["lo"] >= items["transformer"]["lo"], f"{plan} {c['n']}: a transformer raised but no transformer wait"
                if "line" in seen:
                    assert c["hi"] >= items["line"]["hi"], f"{plan} {c['n']}: a line raised but no line wait"
                if not seen and not gen:
                    assert c["item"] == "connect", f"{plan} {c['n']}: no upgrade and plant room, yet {c['item']}"
        for c in t["firm"]["campuses"]:
            f = c["flex"]
            if c["flex_sooner"]:
                assert f and (f["hi"], f["lo"]) < (c["hi"], c["lo"]), c
            elif f:
                assert (f["hi"], f["lo"]) >= (c["hi"], c["lo"]), c
        # the meter's first campus: fits today (the headline's "Today it carries 2") and waits only for study and build
        today = cap["firm"]["today"]
        if today and (plants.get("campuses_firm") or 0) >= 1:
            assert t["firm"]["campuses"][0]["item"] == "connect", t["firm"]["campuses"][0]

    ctx.check("time to power: bad requests say why", validation)
    ctx.check("time to power: reads the cached study only (LAZY)", lazy)
    ctx.check("time to power: Florida 1 GW, every campus a sourced range", florida)
