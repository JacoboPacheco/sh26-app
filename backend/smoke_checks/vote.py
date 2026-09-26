"""Smoke checks for before-the-vote (the vote track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200), auth(), expected.
These endpoints are public GETs that store nothing. The civic file (backend/demo/civic.json) may or may not
exist yet: every civic piece may be null, and the checks accept both."""

import math
import re

FIVE = [
    "stonebridge-fort-meade",
    "atlas-compute-fort-pierce",
    "sentinel-grove-fort-pierce",
    "pba-holdings-loxahatchee",
    "nextnrg-near-jacksonville-international-airport",
]
FORBIDDEN = re.compile(r"will cause|will black|will trigger|is to blame|blame|illegal|fraud|scam|corrupt|guilty|negligen", re.I)
HTTP = re.compile(r"^https?://", re.I)


def register(ctx):
    got = {}

    def search_finds_fort_meade():
        r = ctx.request("GET", "/api/vote/search?q=Fort%20Meade")
        assert any(p["id"] == "stonebridge-fort-meade" for p in r["proposals"]), [p["id"] for p in r["proposals"]]
        p = next(p for p in r["proposals"] if p["id"] == "stonebridge-fort-meade")
        for k in ("id", "name", "company", "city", "county", "county_text", "state", "mw", "status", "status_text"):
            assert k in p, k
        assert p["state"] == "FL" and p["mw"] == 1200 and p["county_text"] == "Polk County", p
        assert any(s["code"] == "FL" for s in r["states"]), r["states"]

    def search_by_county_and_state():
        r = ctx.request("GET", "/api/vote/search?q=Polk%20County")
        assert any(p["id"] == "stonebridge-fort-meade" for p in r["proposals"]), "county search"
        r = ctx.request("GET", "/api/vote/search?state=fl&limit=50")
        assert r["proposals"] and all(p["state"] == "FL" for p in r["proposals"]), "state filter"
        assert r["total"] >= 5, r["total"]
        r = ctx.request("GET", "/api/vote/search?q=zzzznothing")
        assert r["proposals"] == [] and r["total"] == 0, "no match is an empty list, not an error"

    def empty_query_leads_with_the_florida_five():
        r = ctx.request("GET", "/api/vote/search?limit=12")
        ids = [p["id"] for p in r["proposals"]]
        assert ids[:5] == FIVE, ids[:6]
        assert all(p["featured"] for p in r["proposals"][:5]) and not any(p["featured"] for p in r["proposals"][5:]), "featured flags"
        assert len(ids) == 12 and len(set(ids)) == 12, "limit and no repeats"
        assert r["states"] and r["states"][0]["count"] >= 1

    def rejects_bad_input():
        ctx.request("GET", "/api/vote/search?q=" + "x" * 81, expect=422)
        ctx.request("GET", "/api/vote/search?state=Florida", expect=422)
        ctx.request("GET", "/api/vote/search?limit=0", expect=422)
        ctx.request("GET", "/api/vote/search?limit=500", expect=422)
        ctx.request("GET", "/api/vote/proposal/not.an.id", expect=422)
        ctx.request("GET", "/api/vote/proposal/UPPER-case", expect=422)
        ctx.request("GET", "/api/vote/proposal/" + "a" * 121, expect=422)
        ctx.request("GET", "/api/vote/proposal/no-such-campus", expect=404)
        ctx.request("GET", "/api/vote/proposal/..%2F..%2Fetc%2Fpasswd", expect=404)  # a path can't reach a file

    def proposal_has_every_key():
        r = ctx.request("GET", "/api/vote/proposal/stonebridge-fort-meade")
        got["fm"] = r
        for k in ("entry", "civic", "state", "simulation", "cost", "safe", "questions", "brief", "frame", "credit", "ms"):
            assert k in r, f"missing {k}"
        e = r["entry"]
        for k in ("id", "name", "company", "city", "county", "state", "lat", "lon", "mw", "mw_basis", "status", "year", "confidence", "sources"):
            assert k in e, f"entry missing {k}"
        assert "verification" not in e, "the catalog's internal notes stay internal"
        assert e["id"] == "stonebridge-fort-meade" and e["mw"] == 1200 and e["state"] == "FL", e["id"]
        assert e["sources"] and all(HTTP.match(s["url"]) and s["outlet"] and s["label"] for s in e["sources"]), e["sources"]
        # civic pieces may be null: the page says how to find them
        c = r["civic"]
        for k in ("researched", "decision_body", "utility", "cases", "status_note", "sources", "checked"):
            assert k in c, f"civic missing {k}"
        assert isinstance(c["cases"], list) and isinstance(c["sources"], list)
        s = r["state"]
        assert s["code"] == "FL" and s["name"] == "Florida" and isinstance(s["policy"], list) and "commission" in s, s
        for link in (c["decision_body"] or {}).values():
            assert link is None or isinstance(link, str)
        assert "synthetic" in r["frame"].lower() and "not a prediction" in r["frame"].lower(), r["frame"]

    def simulation_is_labeled_and_sane():
        sim = got["fm"]["simulation"]
        assert sim["tested"] is True and sim["verdict"] in ("fits", "overloads", "outage"), sim["verdict"]
        assert "synthetic" in sim["note"].lower() and "not a prediction" in sim["note"].lower(), sim["note"]
        for k in ("flexible", "firm"):
            o = sim[k]
            assert o["people"] >= 0 and o["text"] and o["label"], o
            assert isinstance(o["people_text"], str)
        assert sim["room_mw"] > 0 and sim["tested_mw"] == 1200 and sim["overloaded"] >= 0
        # the same numbers the catalog reports for this campus
        t = ctx.request("GET", "/api/catalog/stonebridge-fort-meade")["test"]
        assert t["headroom_mw"] == sim["room_mw"] and t["people"] == sim["flexible"]["people"], (t["headroom_mw"], sim["room_mw"])
        assert t["firm"]["people"] == sim["firm"]["people"]

    def cost_ranges_and_sources():
        cost = got["fm"]["cost"]
        assert cost["estimate"] is True
        for k in ("blackout", "upgrades", "power_bill", "who_pays"):
            ln = cost[k]
            assert math.isfinite(ln["low"]) and math.isfinite(ln["high"]) and 0 <= ln["low"] <= ln["high"], (k, ln["low"], ln["high"])
            assert ln["formula"] and ln["assumption"] and ln["range"], k
            assert ln["sources"] and all(HTTP.match(s["url"]) and s["title"] for s in ln["sources"]), k
        assert cost["power_bill"]["big"].startswith("$") and cost["power_bill"]["low"] > 0
        assert cost["blackout"]["people_text"]
        # the same prices the cost route gives for this case
        e = got["fm"]["entry"]
        c = ctx.request("POST", "/api/cost", {"region": "FL", "lat": e["lat"], "lon": e["lon"], "mw": e["mw"]})
        by = {ln["key"]: ln for ln in c["lines"]}
        for k in ("blackout", "upgrades", "power_bill"):
            assert by[k]["low"] == cost[k]["low"] and by[k]["high"] == cost[k]["high"], (k, by[k]["high"], cost[k]["high"])
        for sp in cost["spread"]:
            assert sp["households"] > 0 and sp["per_month"] > 0

    def ranked_plans_are_engine_verified():
        safe = got["fm"]["safe"]
        assert safe["status"] in ("solutions", "not_needed", "none", "unavailable"), safe["status"]
        assert safe["note"] and "synthetic" in safe["note"].lower()
        if safe["status"] == "solutions":
            assert safe["solutions"], "a solutions status lists at least one plan"
            for s in safe["solutions"]:
                assert s["verified"] is True and s["title"] and s["steps"], s
                assert s["by"] in ("engine", "gemini")
                if s["cost"]:
                    assert 0 < s["cost"]["low"] <= s["cost"]["high"] and s["cost"]["range"], s["cost"]
            assert safe["solutions"][0]["rank"] == 1

    def questions_are_sourced():
        qs = got["fm"]["questions"]
        assert len(qs) >= 6, len(qs)
        ids = [q["id"] for q in qs]
        assert len(set(ids)) == len(ids), "unique ids"
        for q in qs:
            assert q["question"].endswith("?"), q["question"]
            assert q["why"] and q["listen_for"], q["id"]
            assert 1 <= len(q["sources"]) <= 4 and all(HTTP.match(s["url"]) and s["title"] for s in q["sources"]), q["id"]

    def brief_has_sections_and_disclaimer():
        b = got["fm"]["brief"]
        assert b["title"].startswith("Before the vote") and b["generated"] and b["credit"], b["title"]
        heads = [s["heading"] for s in b["sections"]]
        for want in ("The proposal, as reported", "Where it stands", "Questions to ask before approving", "Where to speak"):
            assert any(h.startswith(want) for h in heads), (want, heads)
        assert any("simulated" in h for h in heads), heads
        for s in b["sections"]:
            assert s.get("paragraphs") or s.get("items"), s["heading"]
        assert "synthetic" in b["disclaimer"].lower() and "not a prediction" in b["disclaimer"].lower(), b["disclaimer"]
        assert b["sources"] and all(HTTP.match(s["url"]) for s in b["sources"])

    def wording_never_accuses():
        r = got["fm"]
        texts = [r["simulation"]["headline"], r["simulation"]["flexible"]["text"], r["simulation"]["firm"]["text"], r["safe"]["headline"], r["safe"]["note"]]
        for s in r["brief"]["sections"]:
            texts += (s.get("paragraphs") or []) + [i["text"] for i in s.get("items") or []] + [s.get("note") or ""]
        for q in r["questions"]:
            texts += [q["question"], q["why"], q["listen_for"]]
        bad = [t for t in texts if FORBIDDEN.search(t or "")]
        assert not bad, bad[:2]

    def second_call_is_cached():
        a = got["fm"]
        b = ctx.request("GET", "/api/vote/proposal/stonebridge-fort-meade")
        assert b["ms"] == a["ms"] and b["simulation"] == a["simulation"] and b["cost"] == a["cost"], "the expensive part is computed once"

    def a_case_that_fits_and_a_paused_one():
        r = ctx.request("GET", "/api/vote/proposal/pba-holdings-loxahatchee")
        assert r["entry"]["status_text"] == "Paused or canceled", r["entry"]["status_text"]
        sim = r["simulation"]
        assert sim["tested"] and sim["verdict"] in ("fits", "overloads", "outage")
        if sim["verdict"] == "fits":
            assert sim["flexible"]["people"] == 0 and r["cost"]["blackout"]["high"] == 0
            assert r["safe"]["status"] == "not_needed", r["safe"]["status"]
        assert r["brief"]["sections"]

    ctx.check("vote: search finds Fort Meade", search_finds_fort_meade)
    ctx.check("vote: search by county and state, empty result is a list", search_by_county_and_state)
    ctx.check("vote: an empty search leads with the Florida five", empty_query_leads_with_the_florida_five)
    ctx.check("vote: bad q, state, limit and ids are refused; unknown id 404", rejects_bad_input)
    ctx.check("vote: proposal has every key (civic pieces may be null)", proposal_has_every_key)
    ctx.check("vote: simulation is labeled synthetic and matches the catalog", simulation_is_labeled_and_sane)
    ctx.check("vote: costs are low-high ranges with formula and sources and match /api/cost", cost_ranges_and_sources)
    ctx.check("vote: ranked plans are engine-verified", ranked_plans_are_engine_verified)
    ctx.check("vote: questions carry why, what to listen for and sources", questions_are_sourced)
    ctx.check("vote: brief has its sections, sources and disclaimer", brief_has_sections_and_disclaimer)
    ctx.check("vote: generated wording never accuses", wording_never_accuses)
    ctx.check("vote: the second call is cached", second_call_is_cached)
    ctx.check("vote: a paused proposal and a campus that fits", a_case_that_fits_and_a_paused_one)
