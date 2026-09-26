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

    def status_has_validation_and_ledger():
        r = ctx.request("GET", "/api/ai/status")
        v = r["validation"]
        assert v and v["florida"], v
        fl = v["florida"]
        assert fl["corr"] >= 0.999 and fl["compared"] >= 1000 and fl["pass"] is True, fl  # Milestone 0 on Florida
        assert fl["compared"] <= fl["branches"] and fl["mean_abs_err_mw"] >= 0 and fl["max_abs_err_mw"] >= fl["mean_abs_err_mw"], fl
        assert v["summary"]["states"] == 48 and v["summary"]["passed"] == 48 and v["summary"]["corr_min"]["corr"] >= 0.9, v["summary"]
        assert "synthetic" in v["limits"] and "DC" in v["limits"], v["limits"]  # the card states its limits
        led = r["ledger"]
        assert set(led) >= {"since", "by_surface", "totals", "catches"}, led
        for s, row in led["by_surface"].items():
            assert row["proposed"] == row["verified"] + row["rejected"] and row["proposed"] >= 0, (s, row)
        assert led["totals"]["proposed"] == sum(x["proposed"] for x in led["by_surface"].values()), led["totals"]
        assert len(led["catches"]) <= 20, len(led["catches"])
        for c in led["catches"]:
            assert c["surface"] and c["at"] and c["reason"], c
            assert c["token"] is None or len(c["token"]) <= 40, c  # a catch holds the offending token only
        full = ctx.request("GET", "/api/ai/validation")
        assert len(full["states"]) == 48 and full["states"]["FL"]["corr"] == fl["corr"], list(full["states"])[:5]

    def ledger_keeps_only_the_token_and_no_names(llm):
        saved_file = llm.LEDGER_FILE
        llm.LEDGER_FILE = ""  # this process never writes the server's ledger file
        try:
            before = dict(llm.ledger()["by_surface"].get("smoke") or {"proposed": 0, "verified": 0, "rejected": 0})
            long_ai_text = "The campus needs 20,000 MW of new lines and " + "much more text " * 20
            llm.note_check("smoke", False, "a memo number that no tool returned", long_ai_text)
            c = llm.ledger()["catches"][0]
            assert c["surface"] == "smoke" and c["token"] and len(c["token"]) <= llm.TOKEN_MAX, c
            assert "much more text much more text much more text" not in c["token"], c  # never the whole AI text
            # a real utility and a real catalog company in the excerpt are stored filtered (analyst.py's name rule)
            llm.note_check("smoke", False, "a smoke check", "Georgia Power 20,000")
            c = llm.ledger()["catches"][0]
            assert "Georgia" not in c["token"] and "Power" not in c["token"] and "20,000" in c["token"] and "[name]" in c["token"], c
            words = sorted(llm._catalog_name_words())
            assert words, "the catalog's name words did not load"
            name = next((w for w in words if w in ("stonebridge", "fermi", "meta", "tallgrass")), words[0])
            llm.note_check("smoke", False, "a smoke check", f"{name.title()} 1,200")
            c = llm.ledger()["catches"][0]
            assert name not in c["token"].lower() and "1,200" in c["token"], c
            llm.note_check("smoke", True, "accepted")
            after = llm.ledger()["by_surface"]["smoke"]
            assert after["proposed"] == before["proposed"] + 4 and after["rejected"] == before["rejected"] + 3 and after["verified"] == before["verified"] + 1, (before, after)
            # the same catch again is one entry with a count, so a re-checked cached answer doesn't crowd out the others
            n_before = len(llm.ledger()["catches"])
            llm.note_check("smoke", False, "a smoke check", "Georgia Power 20,000")
            led = llm.ledger()
            assert len(led["catches"]) == n_before and led["catches"][0]["times"] == 2 and led["catches"][0]["token"] == "[name] 20,000", led["catches"][:2]
            llm.note_check(None, False, None, {"odd": object()})  # never raises, whatever it is given
            assert len(llm.ledger()["catches"][0]["token"]) <= llm.TOKEN_MAX
        finally:
            llm.LEDGER_FILE = saved_file

    def ledger_survives_a_restart(llm):
        import tempfile

        saved_file, saved_armed = llm.LEDGER_FILE, llm._ledger_armed
        saved = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in llm._ledger.items()}
        with tempfile.TemporaryDirectory() as d:
            llm.LEDGER_FILE = os.path.join(d, ".ai_ledger.json")
            try:
                llm._ledger_armed = False  # a process that never started the app (this one) writes nothing
                llm.note_check("smoke_restart", True, "accepted")
                assert not os.path.exists(llm.LEDGER_FILE), "an unarmed process wrote the ledger"
                llm._ledger["by_surface"].pop("smoke_restart", None)
                llm.arm_ledger()  # what the app's startup does
                llm.note_check("smoke_restart", False, "a smoke check", "42")
                llm.note_check("smoke_restart", True, "accepted")
                assert os.path.exists(llm.LEDGER_FILE)
                llm._ledger.update(since=None, updated=None, by_surface={}, catches=[])  # what a restart starts from
                llm._ledger_load()
                led = llm.ledger()
                assert led["by_surface"]["smoke_restart"] == {"proposed": 2, "verified": 1, "rejected": 1}, led["by_surface"]
                assert led["since"] and led["catches"][0]["token"] == "42", led
                assert led["persisted"] is True, led["persisted"]
                # a hand-edited or damaged file never stops the import: odd shapes load as empty, odd counts as 0
                for bad in ('{"by_surface": [1, 2], "catches": "x", "since": 5}',
                            '{"by_surface": {"s": {"proposed": "lots", "verified": null, "rejected": 1e400}}, "catches": [{"surface": "s", "times": "x"}, 7]}'):
                    with open(llm.LEDGER_FILE, "w", encoding="utf-8") as f:
                        f.write(bad)
                    llm._ledger.update(since=None, updated=None, by_surface={}, catches=[])
                    llm._ledger_load()
                    led = llm.ledger()
                    assert all(r["proposed"] >= 0 for r in led["by_surface"].values()) and all(c["times"] >= 1 for c in led["catches"]), led
                # a save that fails (the folder is gone) means the card stops saying "kept across restarts"
                llm.LEDGER_FILE = os.path.join(d, "missing-folder", ".ai_ledger.json")
                llm.note_check("smoke_restart", True, "accepted")
                assert llm.ledger()["persisted"] is False, llm.ledger()["persisted"]
            finally:
                llm.LEDGER_FILE, llm._ledger_armed = saved_file, saved_armed
                llm._ledger_saved = None
                llm._ledger.update(saved)

    def name_filter_leaves_code_and_words_alone(llm):
        s = llm.scrub_names
        # a tool or argument name is code, and a lowercase everyday word in a token is a word, not a company
        assert s("connect_site") == "connect_site", s("connect_site")
        assert s("core stream platform related 1,200") == "core stream platform related 1,200", s("core stream platform related 1,200")
        assert s("Stream 1,200") == "[name] 1,200", s("Stream 1,200")  # capitalized, it is the catalog's name
        # three-letter company names as the catalog writes them (never the chip or legal-form words)
        short = llm._short_names or set()
        assert {"QTS", "AWS", "xAI"} <= short and not short & {"GPU", "LLC", "TPU"}, sorted(short)
        assert s("QTS 300 MW") == "[name] 300 MW" and s("xai 1,000") == "[name] 1,000", (s("QTS 300 MW"), s("xai 1,000"))
        assert s("a GPU and the new MW plan", strict=False) == "a GPU and the new MW plan"
        assert s("the AWS campus", strict=False) == "the [name] campus" and s("the aws campus", strict=False) == "the aws campus"

    ctx.check("ai: status lists every surface with what Gemini does, who checks it and the fallback", status_lists_every_surface)
    ctx.check("ai: status carries the flow validation (Florida correlation near 1, all 48 states) and the verification ledger", status_has_validation_and_ledger)
    ctx.check("ai: a ledger catch keeps only the offending token, with real names filtered; the counts add up", in_process(ledger_keeps_only_the_token_and_no_names))
    ctx.check("ai: the ledger is written to disk and read back after a restart; a damaged file or a failed save is handled", in_process(ledger_survives_a_restart))
    ctx.check("ai: the ledger's name filter leaves code and everyday words alone and catches three-letter names", in_process(name_filter_leaves_code_and_words_alone))
    ctx.check("ai: no key means a labeled fallback (or a 503 when a call has none), counted per surface", in_process(no_key_means_a_labeled_fallback))
    ctx.check("ai: a cached answer is served without a key or a network call; a new prompt is a miss", in_process(cache_serves_without_a_key))
