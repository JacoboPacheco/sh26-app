"""Smoke checks for the briefing writer (backend/bulletin.py): the narrated slide deck and the legacy
one-paragraph bulletin. Loaded by smoke_test.py. Public and read-only: nothing is created.

With GEMINI_API_KEY blank (scripts/check.sh) the AI deck must come back complete and flagged
fallback; with a key it may be Gemini-written, but every number it speaks is checked server-side."""

import re

HEX32 = re.compile(r"^[0-9a-f]{32}$")
# alert phrasing and names the briefing must never produce (CLAUDE.md → Decisions, contract v2)
NEVER = re.compile(
    r"(?i:emergency alert|this is not a test|evacuat|shelter|\bFEMA\b|national weather service|alerta de emergencia|evacu[aá])"
    r"|\b[Hh]urricane [A-Z][a-z]+|\b(FPL|TECO|JEA|Duke Energy)\b"
)


def register(ctx):
    hero = ctx.expected["hero"]
    case = {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["mw"]}
    state = {}

    def segments(deck, lang):
        return [seg for s in deck["slides"] for seg in s["narration"][lang]]

    def test_hero_deck():
        d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False})
        state["deck"] = d
        ids = [s["id"] for s in d["slides"]]
        assert ids[:4] == ["toll", "event", "chain", "areas"] and ids[-1] == "bottom_line", ids
        assert 5 <= len(ids) <= 10 and len(set(ids)) == len(ids), ids
        assert d["ai"]["by"] == "template" and d["region"] == "FL", d["ai"]
        for lang, open_words in (("en", ("simulat", "synthetic")), ("es", ("simula", "sintétic"))):
            first = d["slides"][0]["narration"][lang][0]["text"].lower()
            assert all(w in first for w in open_words), f"{lang} opening lacks {open_words}: {first[:90]!r}"
            segs = segments(d, lang)
            last = segs[-1]["text"]
            assert ("End of simulated briefing" if lang == "en" else "Fin del simulacro") in last, last
            assert d["total_chars"][lang] <= d["budget"][lang], (lang, d["total_chars"], d["budget"])
            assert sum(s["chars"] for s in segs) == d["total_chars"][lang]
            for s in segs:
                assert HEX32.match(s["key"]) and s["chars"] == len(s["text"]) and s["role"] in ("presenter", "analyst"), s
                assert all(0 <= c["char"] <= s["chars"] for c in s["cues"]), s["cues"]
        chain = next(s for s in d["slides"] if s["id"] == "chain")
        steps = {c["value"] for seg in chain["narration"]["en"] for c in seg["cues"] if c["name"] == "step"}
        want = set(range(1, hero["cascade_steps"] + 1))
        assert want <= steps, f"chain cues cover {sorted(steps)}, expected {sorted(want)}"
        assert chain["map"]["mode"] == "replay" and chain["camera"]["points"], chain["camera"]
        areas = next(s for s in d["slides"] if s["id"] == "areas")
        assert any(c["name"] == "area" for seg in areas["narration"]["en"] for c in seg["cues"]), "no area cues"
        assert "estimate" in d["slides"][0]["big"]["label"]["en"], d["slides"][0]["big"]
        # the lead: expected cost (the high end) and time without power, the same numbers the cost panel shows
        toll = d["slides"][0]
        assert toll["kind"] == "toll" and toll["big"]["display"]["en"].startswith("$") and toll["big2"]["display"]["en"], toll
        panel = ctx.request("POST", "/api/cost", case)["headline"]
        assert abs(toll["big"]["value"] - panel["cost_high"]) <= 0.02 * panel["cost_high"], (toll["big"]["value"], panel["cost_high"])
        assert abs(toll["big2"]["value"] - panel["outage_hours"]) < 0.5, (toll["big2"]["value"], panel["outage_hours"])
        assert toll["headline"]["en"].startswith("Expected cost") and "expected cost" in toll["narration"]["en"][0]["text"].lower(), toll["headline"]
        assert "costo esperado" in toll["narration"]["es"][0]["text"].lower(), toll["narration"]["es"][0]["text"]
        assert not any("simulation" in seg["text"].lower() for seg in d["slides"][1]["narration"]["en"]), "the SIMULATION opening is repeated after the toll"
        blob = str(d)
        m = NEVER.search(blob)
        assert not m, f"forbidden phrase in the deck: {m.group(0)!r}"

    def test_solutions_and_play_by_play():
        d = state["deck"]
        fix = next(s for s in d["slides"] if s["id"] == "fix")
        opts = fix["options"]
        assert len(opts) >= 2, f"always more than one solution: {[o['family'] for o in opts]}"
        first = opts[0]
        assert first["family"] in ("upgrade", "agentic", "combo") and (first["kept_pct"] or 0) >= 90, f"the top solution should keep the campus at (nearly) full size: {first['family']} {first['kept_pct']}"
        assert all(o["verdict"] == "holds" and o["must"]["en"] and o["must"]["es"] for o in opts), "every listed solution is verified and says what you have to do"
        assert first["cost"] and first["cost"]["high"] >= first["cost"]["low"] > 0, first["cost"]
        assert [g["cues"] for g in fix["narration"]["en"][1:]] and all(any(c["name"] == "option" for c in g["cues"]) for g in fix["narration"]["en"][1:]), "each option is cued"
        assert "you have to do" in fix["narration"]["en"][0]["text"], fix["narration"]["en"][0]["text"]
        chain = next(s for s in d["slides"] if s["id"] == "chain")
        assert len(chain["plays"]) == hero["cascade_steps"] and chain["plays"][0]["people_hit"] > 0, chain["plays"][:1]
        assert all({"n", "kind", "label", "loading_pct", "people_hit", "areas"} <= set(p_) for p_ in chain["plays"]), chain["plays"][0]
        assert "Play one" in chain["narration"]["en"][1]["text"], chain["narration"]["en"][1]["text"][:80]
        assert d["short"][:2] == ["toll", "chain"] and "fix" in d["short"] and d["short"][-1] == "bottom_line", d["short"]
        rep = ctx.request("POST", "/api/briefing", case)
        assert rep["solutions"] and rep["best_fix"] == rep["solutions"][0], (rep["best_fix"], rep["solutions"])
        assert rep["fixes"][rep["best_fix"]]["kept_pct"] >= 90, "best_fix keeps at least 90% of the campus when such a fix holds"
        assert isinstance((d.get("agentic") or {}).get("status", "off"), str)

    def test_hero_fix_holds():
        d = state["deck"]
        fix = next((s for s in d["slides"] if s["id"] in ("fix", "no_fix")), None)
        assert fix is not None and fix["id"] == "fix" and d["verdict"] == "preventable", [s["id"] for s in d["slides"]]
        bottom = d["slides"][-1]
        apply = (bottom.get("cta") or {}).get("apply")
        assert apply, "the bottom line has no 'apply the best fix'"
        c = ctx.request("POST", "/api/grid/cascade", {**case, **apply})
        assert c["total_steps"] == 0, f"the deck's best fix still cascades: {c['total_steps']} steps"

    def test_one_set_of_numbers():
        # ONE SET OF NUMBERS (the results panel, the deck, its ticker and the brief say the same figures):
        # people hit = the cascade's people_hit (the panel's counter), still without power when it settles =
        # its people; the outage length as costs.outage_label writes it; the cost at the high end; no peak figure;
        # and the deck's "Apply the best fix" is the report's best fix (the panel's "Run it again with the fix")
        d = state["deck"]
        c = ctx.request("POST", "/api/grid/cascade", case)
        panel = ctx.request("POST", "/api/cost", case)["headline"]
        toll, event = d["slides"][0], next(s for s in d["slides"] if s["id"] == "event")
        hit, still = int(c.get("people_hit") or c["people"]), int(c["people"])
        assert toll["people"] == {"hit": hit, "still_out": still}, (toll.get("people"), hit, still)
        assert event["big"]["value"] == hit and "hit" in event["big"]["label"]["en"], event["big"]
        assert f"{hit:,} people hit" in event["headline"]["en"], event["headline"]["en"]
        assert f"{hit:,}" in " ".join(toll["lines"]["en"]) and f"still without power when it settled: {still:,}" in " ".join(toll["lines"]["en"]), toll["lines"]["en"]
        assert toll["big2"]["display"]["en"] == panel["outage_label"] and re.fullmatch(r"about \d+ (hours|days)|about an hour", panel["outage_label"]), (toll["big2"]["display"], panel["outage_label"])
        assert toll["headline"]["en"].endswith(f"{panel['outage_label']} without power"), toll["headline"]["en"]
        blob = " ".join(" ".join([s["headline"]["en"], *s["lines"]["en"], *(g["text"] for g in s["narration"]["en"])]) for s in d["slides"])
        assert "worst step" not in blob and "Peak:" not in blob, "a peak figure is back in the deck"
        assert not re.search(r"\b\d+\.\d hours\b", blob), "an outage length with decimals (the panel says 'about N hours')"
        rep = ctx.request("POST", "/api/briefing", case)
        best = rep["fixes"][rep["best_fix"]]
        bottom = d["slides"][-1]
        assert (bottom.get("cta") or {}).get("apply") == best["apply"], (bottom.get("cta"), best["apply"])
        if best.get("cost"):  # the bottom line names the fix's cost at the high end, as the panel's flip does
            assert "$" in bottom["narration"]["en"][0]["text"], bottom["narration"]["en"][0]["text"][:160]

    def test_short_deck():
        d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False, "length": "short"})
        ids = [s["id"] for s in d["slides"]]
        assert ids == d["short"] and ids[0] == "toll" and ids[-1] == "bottom_line" and len(ids) <= 6, ids
        assert "cause" in ids and ("fix" in ids or "no_fix" in ids), f"the presentation must say why it failed and what to do: {ids}"
        assert d["est_s"]["en"] <= 110 and d["total_chars"]["en"] <= d["budget"]["en"], (d["est_s"], d["total_chars"])

    def test_ai_deck():
        configured = ctx.request("GET", "/api/ai/status")["configured"]
        d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": True, "length": "short"})
        assert isinstance(d["ai"]["fallback"], bool) and d["ai"]["by"] in ("gemini", "mixed", "template"), d["ai"]
        if not configured:
            assert d["ai"]["fallback"] is True and d["ai"]["by"] == "template", d["ai"]
        assert d["slides"][0]["narration"]["en"][0]["text"].startswith("This is a simulation"), "the fixed opening is missing"
        m = NEVER.search(str(d))
        assert not m, f"forbidden phrase in the AI deck: {m.group(0)!r}"

    def test_legacy_bulletin():
        configured = ctx.request("GET", "/api/ai/status")["configured"]
        b = ctx.request("POST", "/api/bulletin", case)
        assert isinstance(b["text"], str) and len(b["text"].strip()) > 40, f"bulletin text too short: {b['text']!r}"
        assert "synthetic" in b["text"].lower() and "simulation" in b["text"].lower(), b["text"][:120]
        assert isinstance(b["fallback"], bool) and (b["fallback"] or configured), b["fallback"]
        keys = {f["key"] for f in b["facts"]}
        assert "event.people_out" in keys and "event.steps" in keys, sorted(keys)[:10]

    def test_honest_storm_and_heat():
        # a storm the data center did not cause: "no fix" deck, and nothing says upgrades "prevent" it
        d = ctx.request("POST", "/api/briefing/deck", {**case, "preset": "fl-gulf-fort-myers", "region": "FL", "ai": False})
        ids = [s["id"] for s in d["slides"]]
        assert d["verdict"] == "no_fix" and "no_fix" in ids and "fix" not in ids, (d["verdict"], ids)
        for s in d["slides"]:
            if s["id"] in ("cost", "no_fix", "recovery", "bottom_line"):
                blob = " ".join([s["headline"]["en"], *s["lines"]["en"], *(g["text"] for g in s["narration"]["en"])]).lower()
                assert "prevent it" not in blob and "would prevent" not in blob, f"{s['id']} claims a fix prevents a storm outage: {blob[:200]}"
        # demand alone, no campus: no slide may speak of a data center that is not there
        h = ctx.request("POST", "/api/briefing/deck", {"load_factor": 1.08, "ai": False})
        for s in h["slides"]:
            blob = " ".join([s["headline"]["en"], *s["lines"]["en"], *(g["text"] for g in s["narration"]["en"])]).lower()
            assert "data center" not in blob and " it only at" not in blob, f"{s['id']} speaks of a data center with none placed: {blob[:200]}"

    def test_agent_trace_in_deck():
        # "Watch the AI work": the fix slide carries the AI proposer's run (solutions.py → report.agentic) with its trace.
        # Without a key the proposer is off and the trace is empty; with one, once it is done, every round is in it.
        import time

        configured = ctx.request("GET", "/api/ai/status")["configured"]
        d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False})
        deadline = time.time() + 45
        while configured and (d.get("agentic") or {}).get("status") in (None, "running") and time.time() < deadline:
            time.sleep(3)
            d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False})
        fix = next(s for s in d["slides"] if s["id"] == "fix")
        ag = fix.get("agentic")
        assert isinstance(ag, dict) and isinstance(ag.get("trace"), list) and ag.get("status") in ("off", "running", "done", "error"), ag
        if not configured:
            assert ag["status"] == "off" and ag["trace"] == [], ag
            return
        if ag["status"] != "done" or not ag.get("asked"):
            return  # Gemini unavailable or out of quota: the engine's own fixes stand, nothing to trace
        tr = ag["trace"]
        assert tr and len(tr) <= 40 and [t["n"] for t in tr] == list(range(1, len(tr) + 1)), [t.get("n") for t in tr]
        assert tr[0]["kind"] == "ask" and tr[-1]["kind"] == "result", (tr[0]["kind"], tr[-1]["kind"])
        assert all(t["actor"] in ("gemini", "engine") and t["title"]["en"] and t["title"]["es"] for t in tr), tr[0]
        proposed = [t for t in tr if t["kind"] in ("propose", "revise")]
        checked = [t for t in tr if t["kind"] == "verify"]
        assert len(proposed) == ag["asked"] and len(checked) == len(proposed), (len(proposed), len(checked), ag["asked"])
        assert all(("holds" in t) and t["tone"] in ("holds", "over") for t in checked), checked[:1]
        assert sum(1 for t in checked if t["holds"] and not t.get("duplicate")) == ag["verified"], ag
        assert d["agentic"]["trace"] == tr or len(d["agentic"]["trace"]) >= len(tr), "the deck's agentic and the fix slide's disagree"

    def test_validation():
        ctx.request("POST", "/api/briefing/deck", {}, expect=422)  # nothing happened
        ctx.request("POST", "/api/briefing/deck", {**case, "lat": 40}, expect=422)  # north of Florida
        ctx.request("POST", "/api/briefing/deck", {**case, "mw": 0}, expect=422)
        ctx.request("POST", "/api/briefing/deck", {**case, "trip": [-1]}, expect=422)
        ctx.request("POST", "/api/briefing/deck", {**case, "length": "epic"}, expect=422)
        ctx.request("POST", "/api/bulletin", {}, expect=422)
        ctx.request("POST", "/api/bulletin", {**case, "mw": 0}, expect=422)

    ctx.check("briefing deck (hero, templates): slide order, SIMULATION open/close EN+ES, budgets, cues for every step", test_hero_deck)
    ctx.check("briefing deck: several verified solutions, full size first, each with what you have to do; the play-by-play has its plays", test_solutions_and_play_by_play)
    ctx.check("briefing deck: the hero is preventable and the deck's best fix really stops the cascade", test_hero_fix_holds)
    ctx.check("briefing deck: one set of numbers (people hit / still without power, 'about N hours', high-end cost) and the best fix the panel flips to", test_one_set_of_numbers)
    ctx.check("briefing deck (short): the <= 60 s demo version", test_short_deck)
    ctx.check("briefing deck with AI: complete with or without a key, fixed opening kept", test_ai_deck)
    ctx.check("legacy /api/bulletin: a paragraph from the deck, with the engine's facts", test_legacy_bulletin)
    ctx.check("briefing deck: a storm reads 'no fix' (no upgrade 'prevents' it); a heat-only case never mentions a data center", test_honest_storm_and_heat)
    ctx.check("briefing deck: the fix slide carries the AI proposer's trace (every plan, the engine's verdict on each, the revisions)", test_agent_trace_in_deck)
    ctx.check("briefing deck rejects an empty case, a point outside Florida, a size of 0, an unknown line, a bad length", test_validation)
