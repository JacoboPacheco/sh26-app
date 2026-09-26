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
        assert ids[:3] == ["event", "chain", "areas"] and ids[-1] == "bottom_line", ids
        assert 5 <= len(ids) <= 9 and len(set(ids)) == len(ids), ids
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
        blob = str(d)
        m = NEVER.search(blob)
        assert not m, f"forbidden phrase in the deck: {m.group(0)!r}"

    def test_hero_fix_holds():
        d = state["deck"]
        fix = next((s for s in d["slides"] if s["id"] in ("fix", "no_fix")), None)
        assert fix is not None and fix["id"] == "fix" and d["verdict"] == "preventable", [s["id"] for s in d["slides"]]
        bottom = d["slides"][-1]
        apply = (bottom.get("cta") or {}).get("apply")
        assert apply, "the bottom line has no 'apply the best fix'"
        c = ctx.request("POST", "/api/grid/cascade", {**case, **apply})
        assert c["total_steps"] == 0, f"the deck's best fix still cascades: {c['total_steps']} steps"

    def test_short_deck():
        d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False, "length": "short"})
        ids = [s["id"] for s in d["slides"]]
        assert ids == d["short"] and ids[0] == "event" and ids[-1] == "bottom_line" and len(ids) <= 5, ids
        assert d["est_s"]["en"] <= 65 and d["total_chars"]["en"] <= d["budget"]["en"], (d["est_s"], d["total_chars"])

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

    def test_validation():
        ctx.request("POST", "/api/briefing/deck", {}, expect=422)  # nothing happened
        ctx.request("POST", "/api/briefing/deck", {**case, "lat": 40}, expect=422)  # north of Florida
        ctx.request("POST", "/api/briefing/deck", {**case, "mw": 0}, expect=422)
        ctx.request("POST", "/api/briefing/deck", {**case, "trip": [-1]}, expect=422)
        ctx.request("POST", "/api/briefing/deck", {**case, "length": "epic"}, expect=422)
        ctx.request("POST", "/api/bulletin", {}, expect=422)
        ctx.request("POST", "/api/bulletin", {**case, "mw": 0}, expect=422)

    ctx.check("briefing deck (hero, templates): slide order, SIMULATION open/close EN+ES, budgets, cues for every step", test_hero_deck)
    ctx.check("briefing deck: the hero is preventable and the deck's best fix really stops the cascade", test_hero_fix_holds)
    ctx.check("briefing deck (short): the <= 60 s demo version", test_short_deck)
    ctx.check("briefing deck with AI: complete with or without a key, fixed opening kept", test_ai_deck)
    ctx.check("legacy /api/bulletin: a paragraph from the deck, with the engine's facts", test_legacy_bulletin)
    ctx.check("briefing deck: a storm reads 'no fix' (no upgrade 'prevents' it); a heat-only case never mentions a data center", test_honest_storm_and_heat)
    ctx.check("briefing deck rejects an empty case, a point outside Florida, a size of 0, an unknown line, a bad length", test_validation)
