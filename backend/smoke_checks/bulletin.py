"""Smoke checks for the AI emergency bulletin (backend/bulletin.py). Loaded by smoke_test.py.

Public and read-only: nothing is created. With GEMINI_API_KEY blank (scripts/check.sh) the
bulletin must come back as the template (fallback true); with a key (the deployed app) it may be
either, but it is never empty and always carries the server's own cascade facts."""


def register(ctx):
    hero = ctx.expected["hero"]
    case = {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["mw"]}

    def test_hero_bulletin():
        configured = ctx.request("GET", "/api/ai/status")["configured"]
        b = ctx.request("POST", "/api/bulletin", case)
        assert isinstance(b["text"], str) and len(b["text"].strip()) > 40, f"bulletin text too short: {b['text']!r}"
        assert isinstance(b["fallback"], bool), b["fallback"]
        if not configured:
            assert b["fallback"] is True, "no AI key, yet the bulletin says it was AI-written"
        if b["fallback"]:
            assert "synthetic" in b["text"].lower(), "the template must say the grid is synthetic"
        f = b["facts"]
        assert f["steps"] == hero["cascade_steps"] and f["outcome"] == hero["cascade_outcome"], (
            f"facts say {f['steps']} steps -> {f['outcome']}, expected {hero['cascade_steps']} -> {hero['cascade_outcome']}"
        )
        assert f["homes_estimate"] == hero["homes"], f"homes {f['homes_estimate']} vs expected {hero['homes']}"
        assert 1 <= len(f["towns"]) <= 5 and len(f["first_trips"]) <= 3, f
        assert f["data_centers"][0]["mw"] == hero["mw"], f["data_centers"]

    def test_bulletin_validation():
        ctx.request("POST", "/api/bulletin", {**case, "lat": 40}, expect=422)  # north of Florida
        ctx.request("POST", "/api/bulletin", {**case, "mw": 0}, expect=422)
        ctx.request("POST", "/api/bulletin", {}, expect=422)  # nothing happened: no AI call spent on it
        ctx.request("POST", "/api/bulletin", {**case, "trip": [-1]}, expect=422)

    ctx.check("bulletin for the hero cascade: non-empty, server-side facts, template when AI is off", test_hero_bulletin)
    ctx.check("bulletin rejects a point outside Florida, a size of 0, an empty case, an unknown line", test_bulletin_validation)
