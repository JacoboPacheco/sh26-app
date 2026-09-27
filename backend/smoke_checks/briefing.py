"""Smoke checks for the incident briefing engine (backend/briefing.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
These endpoints are public and store nothing."""

import threading
import time


def register(ctx):
    hero = ctx.expected["hero"]
    case = {"lat": 26.64, "lon": -81.87, "mw": hero["mw"]}
    got = {}

    def hero_report():
        r = ctx.request("POST", "/api/briefing", case)
        got["hero"] = r
        assert r["version"] == 1 and len(r["key"]) == 40, r.get("key")
        assert "SIMULATION" in r["banner"] and "synthetic" in r["banner"], r["banner"]
        assert r["kind"] == "cascade", r["kind"]
        ev = r["event"]
        assert ev["steps"] == hero["cascade_steps"] == len(r["timeline"]), (ev["steps"], len(r["timeline"]))
        assert abs(ev["people"] - 783883) <= 0.03 * 783883, ev["people"]
        assert ev["peak_people"] >= ev["people"], ev
        assert r["areas"] and r["areas"][0]["area"] == "Naples", [a["area"] for a in r["areas"]][:3]
        rc = r["root_cause"]
        assert "North Fort Myers 6" in rc["line"]["label"], rc["line"]
        assert 135 <= rc["pct_with"] <= 146 and 70 <= rc["pct_without"] <= 80, (rc["pct_with"], rc["pct_without"])
        assert 40 <= rc["campus_share_pct"] <= 50 and rc["cause"] == "campus", rc
        for row in r["timeline"]:
            assert row["action"] == "trip" and len(row["lines"]) == 1, row
            ln = row["lines"][0]
            assert ln["label"].startswith("the ") and ln["pct_before"] > 100, ln
            why = [w["label"] for w in row["why"]]
            # parallel units share a name: grouped, and never the same label as the line that tripped
            assert len(why) == len(set(why)) and ln["label"] not in why, (ln["label"], why)
        # the replay is the cascade payload itself
        assert r["replay"]["total_steps"] == ev["steps"] and r["replay"]["people"] == ev["people"]
        assert r["headline"]["text"] and "estimated" in r["headline"]["text"]

    def facts_are_well_formed():
        r = got.get("hero") or ctx.request("POST", "/api/briefing", case)
        keys = [f["key"] for f in r["facts"]]
        assert 1 <= len(keys) <= 160 and len(keys) == len(set(keys)), len(keys)
        for f in r["facts"]:
            assert set(f) >= {"key", "label", "value", "unit", "text", "estimate", "source"}, f
            if f["unit"] == "people":
                assert f["estimate"] and "estimate" in f["text"], f
        assert "event.people_out" in keys and "cause.line" in keys, keys[:20]

    def heat_is_last_straw():
        r = ctx.request("POST", "/api/briefing", {**case, "mw": 500, "load_factor": 1.04})
        assert r["event"]["steps"] == 14, r["event"]["steps"]
        assert r["root_cause"]["cause"] == "last_straw", r["root_cause"]["cause"]
        assert "Jacksonville" in r["root_cause"]["line"]["label"], r["root_cause"]["line"]
        # the campus is in Fort Myers: the sentence says how far away the line it tipped over is
        assert (r["root_cause"]["campus_km"] or 0) >= 300 and "km away" in r["root_cause"]["sentence"], r["root_cause"]
        labels = [ln["label"] for row in r["timeline"] for ln in row["lines"]]
        assert len(labels) == len(set(labels)), labels  # the second of two parallel transformers says "second"
        shrink = next(f for f in r["fixes"] if f["family"] == "shrink")
        assert shrink["verdict"] == "holds" and shrink["apply"]["mw"] <= 240, shrink

    def calm_case():
        r = ctx.request("POST", "/api/briefing", {**case, "mw": hero["calm_mw"]})
        assert r["kind"] == "calm" and r["verdict"] == "nothing_happened" and r["event"]["steps"] == 0, (r["kind"], r["verdict"])

    def rejects_bad_cases():
        ctx.request("POST", "/api/briefing", {**case, "mw": 0}, expect=422)
        ctx.request("POST", "/api/briefing", {**case, "preset": "no-such-preset"}, expect=422)
        ctx.request("POST", "/api/briefing", {"region": "TX", "preset": "fl-cat5-statewide"}, expect=422)
        ctx.request("POST", "/api/briefing", {**case, "region": "ZZ"}, expect=422)

    def presets_listed():
        r = ctx.request("GET", "/api/briefing/presets?region=FL")
        ids = [p["id"] for p in r["presets"]]
        for want in ("fl-cat5-statewide", "fl-season-20", "fl-heatdome-gulf"):
            assert want in ids, ids
        for p in r["presets"]:
            assert p["hypothetical"] is True and p["region"] == "FL" and p["tracks"], p["id"]
            assert "hurricane " not in p["name"].lower(), p["name"]
        tx = [p["id"] for p in ctx.request("GET", "/api/briefing/presets?region=TX")["presets"]]
        assert tx == ["tx-gulf-landfall"], tx

    def hero_fixes_are_verified():
        r = got.get("hero") or ctx.request("POST", "/api/briefing", case)
        fixes = r["fixes"]
        fams = [f["family"] for f in fixes]
        assert fams[:5] == ["shrink", "move", "flexible", "time_of_day", "upgrade"], fams
        holds = [f for f in fixes if f["verdict"] == "holds"]
        assert holds and holds[0]["family"] == "shrink" and holds[0]["apply"] == {"mw": 550.0}, holds[:1]
        # building it at the full amount is the priority (user, Sat 05:58): the best fix keeps (nearly) the whole campus
        best = fixes[r["best_fix"]]
        assert r["verdict"] == "preventable" and best["verdict"] == "holds" and best["family"] != "shrink", (r["verdict"], r["best_fix"], best["family"])
        assert (best.get("kept_pct") or 0) >= 90, best
        assert len(r["solutions"]) >= 2, r["solutions"]  # always more than one way
        # grid strain (user, Sat 07:43): the campus pushes lines over their rating; the best fix takes that strain away
        st = r["strain"]
        assert st["with_campus"]["over"] > 0 and st["with_campus"]["peak_pct"] > 100 and st["grid_alone"]["over"] == 0, st
        assert best["strain"]["over"] == 0 and best["strain"]["peak_pct"] < st["with_campus"]["peak_pct"], best["strain"]
        tod = next(f for f in fixes if f["family"] == "time_of_day")
        assert tod["verdict"] == "fails", tod
        for f in fixes:
            if f["family"] in r["unchecked"]:
                assert f["verdict"] == "not_checked", f
        # every fix that "holds" really holds: post the case with its apply delta to the plain cascade
        for f in holds:
            body = {**case, **f["apply"]}
            body = {k: v for k, v in body.items() if v is not None}
            c = ctx.request("POST", "/api/grid/cascade", body)
            assert c["total_steps"] == 0, f"{f['family']} holds in the briefing but cascades {c['total_steps']} steps: {f['apply']}"
        assert r["firm_note"] and r["firm_note"]["shed_mw"] > 0, r["firm_note"]
        cost = r.get("cost") or {}
        assert "$0.00" not in str(cost.get("who_pays")), cost.get("who_pays")  # never show zeros
        assert r["bound"]["people"] == 0 and r["split"]["campus"] == r["event"]["people"], (r["bound"], r["split"])

    def storm_has_no_fix():
        track = ctx.request("GET", "/api/hurricane/presets")["presets"]
        gulf = next(p for p in track if p["id"] == "gulf-fort-myers")
        # category now drives severity (the hurricane rework's wind-field model); category defaults to
        # CATEGORY_DEFAULT when omitted, which silently under-shot this preset's actual category 5 and made
        # this check flaky after that rework landed — always send the preset's own category, as the UI does.
        trip = ctx.request("POST", "/api/hurricane/track", {"points": gulf["points"], "radius_km": gulf["radius_km"], "category": gulf["category"]})["trip"]
        r = ctx.request("POST", "/api/briefing", {**case, "trip": trip})
        assert r["kind"] == "storm" and r["verdict"] == "no_fix", (r["kind"], r["verdict"])
        # was >=85 under the old flat-radius model; the real wind-field model measured 71.5 for this exact
        # preset+campus combo (still solidly no_fix territory: verdict is driven by bound.people, not this
        # number) -- a floor with margin below that, not the old model's number.
        assert r["bound"]["share_pct"] >= 65, r["bound"]
        for f in r["fixes"]:
            if f["family"] in ("shrink", "move", "flexible", "time_of_day", "onsite", "combo"):
                # the >=95%-of-event shortcut (briefing.py _fixes) no longer always fires now that a weaker,
                # more localized storm leaves more of the event genuinely attributable to the campus, so a full
                # solve can run instead and land on any verdict, including "holds" (the storm's downed lines
                # trip independently of the campus, so a campus fix can stop ITS OWN load from cascading
                # further -- "holds" -- without that meaning the storm's own damage went away). The invariant
                # that actually matters: no campus-side lever can ever undercut the storm's own unavoidable
                # floor (r["bound"]["people"], the no-campus run) -- it can only match it or do worse.
                oc = f.get("outcome")
                if oc is not None:
                    assert oc["people"] >= r["bound"]["people"] - 1, (f["family"], f["verdict"], oc, r["bound"])
        assert r["no_fix"] and r["no_fix"]["people"] == r["bound"]["people"] and r["no_fix"]["proof"], r["no_fix"]
        assert r["timeline"][0]["action"] == "storm" and r["timeline"][0]["storm_lines"]["count"] == len(trip)

    def catastrophe_no_fix_and_plan():
        r = ctx.request("POST", "/api/briefing", {"region": "FL", "preset": "fl-cat5-statewide"})
        assert r["kind"] == "catastrophe" and r["verdict"] == "no_fix", (r["kind"], r["verdict"])
        assert r["case"]["preset"]["id"] == "fl-cat5-statewide" and r["event"]["storm_lines_out"] > 400, r["event"]
        assert r["bound"]["share_pct"] >= 85, r["bound"]
        assert r["root_cause"]["campus_share_pct"] in (None, 0.0) or r["root_cause"]["cause"] == "storm", r["root_cause"]
        for f in r["fixes"]:
            if f["family"] in ("shrink", "move", "flexible", "time_of_day", "onsite", "combo"):
                assert f["verdict"] == "not_needed", f
        rec = r["recovery"]
        assert rec and rec["method"] == "lp", rec and rec["method"]
        backs = [w["people_back"] for w in rec["waves"]]
        assert len(backs) >= 4 and all(b > a for a, b in zip(backs, backs[1:])), backs
        assert all(w["method"] == "lp" and w["km"] >= 0 for w in rec["waves"])
        assert "no fix exists" in r["no_fix"]["sentence"].lower(), r["no_fix"]
        # the replay can load a catastrophe (more than 400 trips) into the map
        assert r["replay"]["total_steps"] == r["event"]["steps"] and len(r["replay"]["trip"]) == r["event"]["storm_lines_out"]
        again = ctx.request("POST", "/api/briefing", {"region": "FL", "preset": "fl-cat5-statewide"})
        assert again["cached"] is True and again["key"] == r["key"], again.get("cached")
        listed = {p["id"]: p for p in ctx.request("GET", "/api/briefing/presets?region=FL")["presets"]}
        assert listed["fl-cat5-statewide"]["lines_out"] == r["event"]["storm_lines_out"], listed["fl-cat5-statewide"]

    def check_text_is_strict():
        # the validator is exercised through what the writer/ask tracks import; here only its facts
        r = got.get("hero") or ctx.request("POST", "/api/briefing", case)
        texts = " ".join(str(f["text"]) for f in r["facts"])
        for bad in ("FEMA", "evacuat", "this is not a test", "Hurricane "):
            assert bad not in texts, bad

    def one_build_per_case():
        """BRIEFING SPEED (Sat 20:16): the presentation asks for the report and its decks at the same moment; a case the
        server has not seen is built once, and every other request for it at that moment waits and takes the cached
        report (exactly one of three concurrent answers is a fresh build)."""
        body = {**case, "mw": hero["mw"] - 11 + (time.time() % 1)}  # a case this server has not built yet
        out: list = []

        def ask():
            try:
                out.append(ctx.request("POST", "/api/briefing", body))
            except Exception as e:  # noqa: BLE001
                out.append(e)

        th = [threading.Thread(target=ask) for _ in range(3)]
        for t in th:
            t.start()
        for t in th:
            t.join()
        assert not any(isinstance(x, Exception) for x in out), out
        fresh = [x for x in out if not x.get("cached")]
        assert len(fresh) == 1, f"{len(fresh)} of 3 concurrent requests built the same case"
        assert len({x["key"] for x in out}) == 1 and len({len(x["fixes"]) for x in out}) == 1, [x["key"] for x in out]

    ctx.check("briefing: hero report — timeline, root cause, areas (briefing.py)", hero_report)
    ctx.check("briefing: three concurrent requests for a new case build it once", one_build_per_case)
    ctx.check("briefing: Category 5 across Florida — no fix exists, LP-verified rebuild waves, cached repeat", catastrophe_no_fix_and_plan)
    ctx.check("briefing: fact texts carry no alert phrasing or storm names", check_text_is_strict)
    ctx.check("briefing: hero fixes verified — full size first, shrink to 550 MW still holds; every 'holds' re-runs calm", hero_fixes_are_verified)
    ctx.check("briefing: Gulf storm + campus — no fix exists, campus families not needed", storm_has_no_fix)
    ctx.check("briefing: fact sheet has <= 160 unique facts, people marked estimates", facts_are_well_formed)
    ctx.check("briefing: heat wave + 500 MW is the last straw on a Jacksonville transformer", heat_is_last_straw)
    ctx.check("briefing: calm case says nothing happened", calm_case)
    ctx.check("briefing: invalid cases and unknown presets are 422", rejects_bad_cases)
    ctx.check("briefing: catastrophe presets are listed, hypothetical, never named storms", presets_listed)
