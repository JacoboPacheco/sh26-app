"""Smoke checks for the AI layer (llm.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200).
The status route is checked over HTTP (it also runs against Render). The fallback and cache checks run
llm.py in-process with no key and no network, so they never spend the day's Gemini quota."""

import asyncio
import os
import sys
from pathlib import Path


def register(ctx):
    def status_lists_every_surface():
        r = ctx.request("GET", "/api/ai/status")
        assert isinstance(r["configured"], bool) and r["model"], r
        assert r["cap"] >= 1 and 0 <= r["remaining"] <= r["cap"] and r["used_today"] >= 0, r
        ids = [s["id"] for s in r["surfaces"]]
        for want in ("deck", "solutions", "unlock", "cost", "ask"):
            assert want in ids, ids
        for s in r["surfaces"]:
            assert s["name"] and s["gemini"] and s["check"] and s["fallback"], s  # what Gemini does, who checks it, what runs without it
        assert set(r["served"]) == {"ok", "fallback", "cached"}, r["served"]

    def in_process(fn):
        def run():
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")  # llm.py imports auth.py, which insists on one; this process only
            try:
                import llm
            except ImportError as e:  # a deployed run without the backend folder on the path
                raise AssertionError(f"llm.py not importable here: {e}") from e
            saved = os.environ.get("GEMINI_API_KEY")
            os.environ["GEMINI_API_KEY"] = ""
            try:
                fn(llm)
            finally:
                if saved is None:
                    os.environ.pop("GEMINI_API_KEY", None)
                else:
                    os.environ["GEMINI_API_KEY"] = saved

        return run

    def no_key_means_a_labeled_fallback(llm):
        data, offline = asyncio.run(llm.complete_json("smoke: no key", fallback={"a": 1}, surface="smoke"))
        assert offline is True and data == {"a": 1}, (offline, data)
        text = asyncio.run(llm.complete("smoke: no key", fallback="FALLBACK", surface="smoke"))
        assert text == "FALLBACK", text
        assert llm._stats["by_surface"]["smoke"]["fallback"] >= 2, llm._stats["by_surface"]
        try:
            asyncio.run(llm.complete_json("smoke: no key and no fallback"))
        except Exception as e:  # HTTPException 503: a call without a fallback says why
            assert getattr(e, "status_code", None) == 503, e
        else:
            raise AssertionError("a call without a fallback should raise when the key is missing")

    def cache_serves_without_a_key(llm):
        key = llm._cache_key("smoke: cached prompt", None, True, None)
        llm._cache_put(key, '{"z": 2}')
        before = llm._stats["cached"]
        data, offline = asyncio.run(llm.complete_json("smoke: cached prompt", fallback={"z": 0}, cache=True, surface="smoke"))
        assert offline is False and data == {"z": 2}, (offline, data)
        assert llm._stats["cached"] == before + 1
        # a different prompt is a miss, so with no key it falls back
        data2, offline2 = asyncio.run(llm.complete_json("smoke: another prompt", fallback={"z": 0}, cache=True, surface="smoke"))
        assert offline2 is True and data2 == {"z": 0}, (offline2, data2)
        assert llm.usage()["remaining"] <= llm.usage()["cap"]

    ctx.check("ai: status lists every surface with what Gemini does, who checks it and the fallback", status_lists_every_surface)
    ctx.check("ai: no key means a labeled fallback (or a 503 when a call has none), counted per surface", in_process(no_key_means_a_labeled_fallback))
    ctx.check("ai: a cached answer is served without a key or a network call; a new prompt is a miss", in_process(cache_serves_without_a_key))
