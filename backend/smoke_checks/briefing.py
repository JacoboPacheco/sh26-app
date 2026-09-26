"""Smoke checks for the incident briefing engine (backend/briefing.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
These endpoints are public and store nothing."""


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

    ctx.check("briefing: hero report — timeline, root cause, areas (briefing.py)", hero_report)
    ctx.check("briefing: fact sheet has <= 160 unique facts, people marked estimates", facts_are_well_formed)
    ctx.check("briefing: heat wave + 500 MW is the last straw on a Jacksonville transformer", heat_is_last_straw)
    ctx.check("briefing: calm case says nothing happened", calm_case)
    ctx.check("briefing: invalid cases and unknown presets are 422", rejects_bad_cases)
    ctx.check("briefing: catastrophe presets are listed, hypothetical, never named storms", presets_listed)
