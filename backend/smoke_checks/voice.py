"""Smoke checks for the briefing voice (backend/voice.py). Loaded by smoke_test.py. Public; creates
nothing another user sees.

Without ELEVENLABS_API_KEY (scripts/check.sh) every segment is a clear 503 and the page reads the text
with the browser's voice. With a key (the deployed app) one short segment is rendered, then served
from the cache; the full-briefing MP3 is not requested here, so a smoke run spends ~30 characters."""

import re

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def register(ctx):
    hero = ctx.expected["hero"]
    case = {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["mw"], "ai": False, "length": "short"}
    state = {}

    def test_status():
        st = ctx.request("GET", "/api/voice/status")
        assert isinstance(st["configured"], bool) and st["provider"] in ("elevenlabs", "browser"), st
        assert st["languages"] == ["en", "es"] and st["attribution"] == "Voice: ElevenLabs", st
        assert (st["provider"] == "elevenlabs") == st["configured"], st
        state["configured"] = st["configured"]

    def test_segment_errors():
        ctx.request("POST", "/api/voice/segment", {"key": "0" * 32}, expect=409)  # never registered: never spoken
        ctx.request("POST", "/api/voice/segment", {"key": "../../etc/passwd"}, expect=422)
        ctx.request("POST", "/api/voice/segment", {}, expect=422)
        ctx.request("GET", "/api/voice/audio/..%2F..%2Fx.mp3", expect=404)
        ctx.request("GET", "/api/voice/audio/" + "0" * 32 + ".mp3", expect=404)
        ctx.request("GET", "/api/voice/file/" + "0" * 32 + ".mp3", expect=404)
        ctx.request("POST", "/api/voice/download", {"deck_key": "f" * 40, "lang": "en"}, expect=409)
        ctx.request("POST", "/api/voice/download", {"deck_key": "x", "lang": "en"}, expect=422)

    def test_segment():
        deck = ctx.request("POST", "/api/briefing/deck", case)
        state["deck"] = deck
        segs = [seg for s in deck["slides"] for seg in s["narration"]["en"]]
        assert segs and all(HEX32.match(s["key"]) for s in segs), "segments without voice keys"
        shortest = min(segs, key=lambda s: s["chars"])
        if not state["configured"]:
            r = ctx.request("POST", "/api/voice/segment", {"key": shortest["key"]}, expect=503)
            assert r["detail"] == "Voice not configured", r
            return
        a = ctx.request("POST", "/api/voice/segment", {"key": shortest["key"]})
        assert a["duration_s"] > 0 and a["words"] and a["audio_url"].endswith(".mp3") and a["provider"] == "elevenlabs", a
        b = ctx.request("POST", "/api/voice/segment", {"key": shortest["key"]})
        assert b["duration_s"] == a["duration_s"], "the second request was not served from the cache"

    def test_download_without_voice():
        if state.get("configured"):
            return  # the joined MP3 would render the whole deck: not spent on a smoke run
        d = ctx.request("POST", "/api/voice/download", {"deck_key": state["deck"]["deck_key"], "lang": "es"})
        assert d["mp3_url"] is None and d["missing"] and d["vtt_url"] and d["txt_url"], d
        assert d["filename"].startswith("overload-briefing-fl-") and d["filename"].endswith("-es"), d["filename"]

    ctx.check("voice status: configured flag, provider, languages, attribution", test_status)
    ctx.check("voice: unknown key 409, malformed 422, path tricks 404, unknown deck 409", test_segment_errors)
    ctx.check("voice segment: 503 'Voice not configured' without a key; with one, audio + words, then cached", test_segment)
    ctx.check("voice download without a key: captions + transcript, no MP3 (named by state, place, language)", test_download_without_voice)
