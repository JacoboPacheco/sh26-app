"""Smoke checks for llm.py's model chain: a busy model (503 "high demand", 500, 502, 504, a timeout, a dropped connection)
hands the request to the next model like a 429 does, on all four entry points. Loaded by smoke_test.py.

ctx: check(name, fn). Every check runs llm.py in-process with the HTTP layer (llm._post_json) stubbed: no key is used, no
network, no Gemini quota (the stub key is never sent), and every knob it moves is restored afterwards."""

import asyncio
import io
import json
import os
import socket
import sys
import time
import urllib.error
from pathlib import Path

CHAIN = ["chain-strong", "chain-b", "chain-c"]  # the models the stubbed chain walks: the call's own model, then two fallbacks
STRONG, B, C = CHAIN
DEFAULT = "chain-default"  # what llm.MODEL is set to for the one check of the default model's place in the chain
ALL = CHAIN + [DEFAULT]
OK_TEXT = {"candidates": [{"content": {"parts": [{"text": json.dumps({"a": 1})}]}}]}
OK_TOOLS = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "f", "args": {}, "id": "c1"}, "thoughtSignature": "REAL_SIG"}]}}]}
OK_GROUNDED = {"candidates": [{"content": {"parts": [{"text": "grounded answer"}]}, "groundingMetadata": {}}]}


def register(ctx):
    def http_error(url, code, body=b'{"error": {"status": "UNAVAILABLE"}}'):
        return urllib.error.HTTPError(url, code, "stub", {}, io.BytesIO(body))

    def sandbox(fn):
        """Runs fn(llm, stub) with llm._post_json replaced by `stub`; stub.play({model: action}) sets what each model does.
        An action: "ok" (the entry point's good reply), an HTTP status (int: raised as that error), "timeout" (a bare
        TimeoutError, as a read that ran out), "url-timeout" (a URLError wrapping one), "reset" (a dropped connection),
        "dns" (a URLError that is not a timeout), or a callable(url, body, timeout) -> reply."""

        def run():
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")  # llm.py imports auth.py; this process only
            try:
                import llm
            except ImportError as e:  # a deployed run without the backend folder on the path
                raise AssertionError(f"llm.py not importable here: {e}") from e

            class Stub:
                def __init__(self):
                    self.script: dict = {}
                    self.ok = OK_TEXT
                    self.calls: list = []  # (model, timeout, body)

                def play(self, script, ok=OK_TEXT):
                    self.script, self.ok = script, ok
                    self.calls.clear()

                def models(self):
                    return [m for m, _, _ in self.calls]

                def __call__(self, url, body, key, timeout):
                    model = url.split("/models/")[1].split(":")[0]
                    self.calls.append((model, timeout, json.loads(json.dumps(body))))
                    act = self.script.get(model, "ok")
                    if callable(act):
                        return act(url, body, timeout)
                    if act == "ok":
                        return json.loads(json.dumps(self.ok))
                    if isinstance(act, int):
                        raise http_error(url, act)
                    if act == "timeout":
                        raise TimeoutError("The read operation timed out")
                    if act == "url-timeout":
                        raise urllib.error.URLError(TimeoutError("timed out"))
                    if act == "reset":
                        raise ConnectionResetError("connection reset by peer")
                    if act == "dns":
                        raise urllib.error.URLError(socket.gaierror("name or service not known"))
                    raise AssertionError(f"unknown stub action {act!r}")

            stub = Stub()
            saved = {"key": os.environ.get("GEMINI_API_KEY"), "post": llm._post_json, "file": llm.CACHE_FILE, "cache": llm._cache, "fb": llm.FALLBACK_MODELS,
                     "out_until": dict(llm._out_until), "out_today": dict(llm._out_today), "daily": dict(llm._daily), "reserve": llm.CHAIN_RESERVE_S, "model": llm.MODEL,
                     "stats": {k: v for k, v in llm._stats.items() if not isinstance(v, dict)}, "by_surface": llm._stats["by_surface"]}
            os.environ["GEMINI_API_KEY"] = "stub-not-a-key"  # never sent: _post_json is stubbed
            llm.CACHE_FILE = ""  # stubbed replies never reach the disk cache
            llm._cache = type(saved["cache"])()
            llm._post_json = stub
            llm.FALLBACK_MODELS = [B, C]
            llm.MODEL = STRONG  # the default model: the chain is then [STRONG, B, C] for a call on STRONG
            llm._stats["by_surface"] = {}
            for m in ALL:
                llm._out_until.pop(m, None)
                llm._out_today.pop(m, None)
            try:
                fn(llm, stub)
            finally:
                llm._post_json, llm.CACHE_FILE, llm._cache, llm.FALLBACK_MODELS, llm.CHAIN_RESERVE_S = saved["post"], saved["file"], saved["cache"], saved["fb"], saved["reserve"]
                llm.MODEL = saved["model"]
                for m in ALL:
                    llm._out_until.pop(m, None)
                    llm._out_today.pop(m, None)
                llm._stats.update(saved["stats"])
                llm._stats["by_surface"] = saved["by_surface"]
                llm._daily.update(saved["daily"])
                if saved["key"] is None:
                    os.environ.pop("GEMINI_API_KEY", None)
                else:
                    os.environ["GEMINI_API_KEY"] = saved["key"]

        return run

    def row(llm, surface="chain"):
        return dict(llm._stats["by_surface"].get(surface, {"ok": 0, "fallback": 0, "cached": 0}))

    def cool_down_reset(llm):
        for m in ALL:
            llm._out_until.pop(m, None)

    # --------------------------------------------------------------------------------------------- complete_json
    def busy_first_model_answers_from_the_second(llm, stub):
        stub.play({STRONG: 503})
        served, daily = dict(llm._stats), llm._daily["used"]
        data, offline = asyncio.run(llm.complete_json("chain: 503 on the first", model=STRONG, fallback={"f": 1}, timeout=10, surface="chain"))
        assert data == {"a": 1} and offline is False, (data, offline)
        assert stub.models() == [STRONG, B], stub.models()
        # one call, one outcome: ok, not a fallback plus an ok
        r = row(llm)
        assert r["ok"] == 1 and r["fallback"] == 0, r
        assert llm._stats["ok"] == served["ok"] + 1 and llm._stats["fallback"] == served["fallback"], (llm._stats["ok"], llm._stats["fallback"])
        assert llm._daily["used"] == daily + 1, "one daily slot for the call, not one per model tried"
        assert llm._stats["model_used"] == B and llm._stats.get("failover", 0) == served.get("failover", 0) + 1, llm._stats
        assert llm._out_until[STRONG] > time.time() + llm.BUSY_S - 5, "the model that just failed is skipped for a while"
        # while it cools down the next call goes straight to the second model: no second wait on the sick one
        stub.play({STRONG: 503})
        asyncio.run(llm.complete_json("chain: cooling down", model=STRONG, fallback={"f": 1}, timeout=10, surface="chain", cache=False))
        assert stub.models() == [B], stub.models()
        st = llm.status()
        assert STRONG in st["models_busy"] and st["failovers"] >= 1 and STRONG in (st["last_failover"] or ""), st

    def every_5xx_and_a_dropped_connection_advance(llm, stub):
        for how in (500, 502, 503, 504, "timeout", "url-timeout", "reset"):
            cool_down_reset(llm)
            stub.play({STRONG: how})
            data, offline = asyncio.run(llm.complete_json(f"chain: {how}", model=STRONG, fallback={"f": 1}, timeout=10, surface="chain", cache=False))
            assert data == {"a": 1} and offline is False and stub.models() == [STRONG, B], (how, data, offline, stub.models())
        # the second model can be busy too: the third answers, still one pass over the chain
        cool_down_reset(llm)
        stub.play({STRONG: 503, B: "timeout"})
        data, offline = asyncio.run(llm.complete_json("chain: two busy", model=STRONG, fallback={"f": 1}, timeout=10, surface="chain", cache=False))
        assert data == {"a": 1} and offline is False and stub.models() == [STRONG, B, C], stub.models()

    def all_busy_ends_in_the_fallback_or_the_raise(llm, stub):
        cool_down_reset(llm)
        stub.play({STRONG: 503, B: 503, C: 503})
        before = row(llm)
        data, offline = asyncio.run(llm.complete_json("chain: all busy", model=STRONG, fallback={"f": 1}, timeout=10, surface="chain", cache=False))
        assert data == {"f": 1} and offline is True and stub.models() == [STRONG, B, C], (data, offline, stub.models())
        after = row(llm)
        assert after["fallback"] == before["fallback"] + 1 and after["ok"] == before["ok"], (before, after)  # one outcome: the fallback
        # without a fallback: the same clear error as ever, and the text names the last model's answer
        cool_down_reset(llm)
        stub.play({STRONG: 503, B: 503, C: 504})
        for call in (
            lambda: llm.complete_json("chain: all busy, raises", model=STRONG, timeout=10, cache=False),
            lambda: llm.complete("chain: all busy, raises (text)", model=STRONG, timeout=10, cache=False),
        ):
            cool_down_reset(llm)
            stub.calls.clear()
            try:
                asyncio.run(call())
            except Exception as e:  # noqa: BLE001 — HTTPException
                assert getattr(e, "status_code", None) == 502 and "(504)" in str(e.detail), (getattr(e, "status_code", None), e)
            else:
                raise AssertionError("a call without a fallback must raise when every model is busy")
            assert stub.models() == [STRONG, B, C], stub.models()
        # a chain of one model (nothing to hand over to) behaves as it always did
        llm.FALLBACK_MODELS = []
        cool_down_reset(llm)
        stub.play({STRONG: 503})
        data, offline = asyncio.run(llm.complete_json("chain: alone", model=STRONG, fallback={"f": 1}, timeout=10, surface="chain", cache=False))
        assert offline is True and stub.models() == [STRONG], stub.models()
        assert 9.9 <= stub.calls[0][1] <= 10, "with no later model the first attempt has the whole timeout"

    def a_400_does_not_advance(llm, stub):
        for code in (400, 401, 403, 404):
            cool_down_reset(llm)
            stub.play({STRONG: code})
            before = row(llm)
            data, offline = asyncio.run(llm.complete_json(f"chain: {code}", model=STRONG, fallback={"f": 1}, timeout=10, surface="chain", cache=False))
            assert data == {"f": 1} and offline is True and stub.models() == [STRONG], (code, stub.models())
            assert row(llm)["fallback"] == before["fallback"] + 1, row(llm)
            assert llm._out_until.get(STRONG, 0) <= time.time(), "a bad request is not a sick model"
            try:
                cool_down_reset(llm)
                asyncio.run(llm.complete(f"chain: {code} raises", model=STRONG, timeout=10, cache=False))
            except Exception as e:  # noqa: BLE001
                assert getattr(e, "status_code", None) == 502 and f"({code})" in str(e.detail), e
            else:
                raise AssertionError("a 4xx must raise")
        # a 400 over the thinking level is still retried on the same model at its default thinking, and a 429 still advances
        cool_down_reset(llm)
        stub.play({STRONG: lambda url, body, timeout: (_ for _ in ()).throw(http_error(url, 400)) if "thinkingConfig" in json.dumps(body) else OK_TEXT})
        data, offline = asyncio.run(llm.complete_json("chain: thinking", model=STRONG, thinking="minimal", fallback={"f": 1}, timeout=10, cache=False))
        assert data == {"a": 1} and offline is False and stub.models() == [STRONG, STRONG], stub.models()
        cool_down_reset(llm)
        stub.play({STRONG: lambda url, body, timeout: (_ for _ in ()).throw(http_error(url, 429, b'{"quotaId": "GenerateRequestsPerMinute"}'))})
        data, offline = asyncio.run(llm.complete_json("chain: 429", model=STRONG, fallback={"f": 1}, timeout=10, cache=False))
        assert data == {"a": 1} and stub.models() == [STRONG, B], stub.models()
        assert llm._out_until[STRONG] > time.time(), "a 429 still marks the model out"

    def a_network_that_is_down_does_not_loop(llm, stub):
        cool_down_reset(llm)
        stub.play({STRONG: "dns"})  # unreachable: every model would fail the same way, so no hand-over
        data, offline = asyncio.run(llm.complete_json("chain: dns", model=STRONG, fallback={"f": 1}, timeout=10, cache=False))
        assert data == {"f": 1} and offline is True and stub.models() == [STRONG], stub.models()

    def the_timeout_is_one_ceiling_for_the_whole_chain(llm, stub):
        # each attempt is what is left of the caller's timeout, less a reserve for the model behind it
        llm.CHAIN_RESERVE_S = 8.0
        cool_down_reset(llm)
        stub.play({STRONG: 503, B: 503})
        asyncio.run(llm.complete_json("chain: budget", model=STRONG, fallback={"f": 1}, timeout=10, cache=False))
        t = [x for _, x, _ in stub.calls]
        assert len(t) == 3 and t[0] <= 10 * llm.CHAIN_SHARE + 0.01 and 3.4 <= t[0], t  # 6.5 s of 10: the sick model can't use it all
        assert all(x <= 10 for x in t), t
        assert t[2] > 8, "the last model gets everything that is left (the busy answers came back at once)"
        # a long timeout: the reserve is 8 s, not a third of it
        cool_down_reset(llm)
        stub.play({STRONG: 503})
        asyncio.run(llm.complete_json("chain: long budget", model=STRONG, fallback={"f": 1}, timeout=60, cache=False))
        assert 51 <= stub.calls[0][1] <= 52.5, stub.calls[0][1]
        # GEMINI_CHAIN_RESERVE_S=0: the first model gets the whole timeout again
        llm.CHAIN_RESERVE_S = 0.0
        cool_down_reset(llm)
        stub.play({STRONG: 503})
        asyncio.run(llm.complete_json("chain: no reserve", model=STRONG, fallback={"f": 1}, timeout=10, cache=False))
        assert 9.9 <= stub.calls[0][1] <= 10.0, stub.calls[0][1]
        llm.CHAIN_RESERVE_S = 8.0
        # a model that hangs for most of the budget leaves too little for a second try: the error, never an overrun
        cool_down_reset(llm)

        def hang(url, body, timeout):
            time.sleep(0.6)
            raise TimeoutError("The read operation timed out")

        stub.play({STRONG: hang})
        t0 = time.monotonic()
        try:
            asyncio.run(llm.complete("chain: hang", model=STRONG, timeout=1.2, cache=False))
        except Exception as e:  # noqa: BLE001
            assert getattr(e, "status_code", None) == 502 and "timed out" in str(e.detail), e
        else:
            raise AssertionError("no time left for the next model: the call raises")
        assert stub.models() == [STRONG] and time.monotonic() - t0 < 2.0, (stub.models(), time.monotonic() - t0)

    def the_default_model_is_the_first_hand_over(llm, stub):
        llm.MODEL = DEFAULT
        assert llm._chain(STRONG) == [STRONG, DEFAULT, B, C], llm._chain(STRONG)
        assert llm._chain(DEFAULT) == [DEFAULT, B, C], llm._chain(DEFAULT)  # a call on the default model: the fallbacks behind it, as before
        assert llm._chain(B) == [B, DEFAULT, C], llm._chain(B)  # no model twice
        cool_down_reset(llm)
        stub.play({STRONG: 503})
        data, offline = asyncio.run(llm.complete_json("chain: strong to default", model=STRONG, fallback={"f": 1}, timeout=10, cache=False))
        assert data == {"a": 1} and offline is False and stub.models() == [STRONG, DEFAULT], stub.models()
        cool_down_reset(llm)
        stub.play({STRONG: 503, DEFAULT: 504})  # the default is busy too: on to the fallbacks
        data, offline = asyncio.run(llm.complete_json("chain: strong to default to b", model=STRONG, fallback={"f": 1}, timeout=10, cache=False))
        assert data == {"a": 1} and stub.models() == [STRONG, DEFAULT, B], stub.models()

    # --------------------------------------------------------------------------------------------- complete
    def plain_complete_advances(llm, stub):
        for how in (503, "timeout"):
            cool_down_reset(llm)
            stub.play({STRONG: how})
            text = asyncio.run(llm.complete("chain: text", model=STRONG, fallback="FALLBACK", timeout=10, surface="chain", cache=False))
            assert json.loads(text) == {"a": 1} and stub.models() == [STRONG, B], (how, text, stub.models())
        cool_down_reset(llm)
        stub.play({STRONG: 503, B: 503, C: 503})
        assert asyncio.run(llm.complete("chain: text, all busy", model=STRONG, fallback="FALLBACK", timeout=10, surface="chain", cache=False)) == "FALLBACK"

    # --------------------------------------------------------------------------------------------- complete_tools
    def tools_advance_with_dummy_signatures(llm, stub):
        contents = [{"role": "user", "parts": [{"text": "chain: tools"}]},
                    {"role": "model", "parts": [{"functionCall": {"name": "f", "args": {}, "id": "x1"}, "thoughtSignature": "REAL_SIG"}]},
                    {"role": "user", "parts": [llm.function_response({"name": "f", "id": "x1"}, {"ok": True})]}]
        decls = [{"name": "f", "description": "stub"}]
        cool_down_reset(llm)
        stub.play({STRONG: 503}, ok=OK_TOOLS)
        before = row(llm)
        reply, offline = asyncio.run(llm.complete_tools(contents, decls, model=STRONG, fallback={"off": True}, timeout=10, surface="chain", cache=False))
        assert offline is False and reply["model"] == B and reply["calls"][0]["name"] == "f", (offline, reply.get("model"))
        assert stub.models() == [STRONG, B], stub.models()
        assert stub.calls[0][2]["contents"][1]["parts"][0]["thoughtSignature"] == "REAL_SIG", "the conversation's own model gets its signature back"
        assert stub.calls[1][2]["contents"][1]["parts"][0]["thoughtSignature"] == llm.DUMMY_SIGNATURE, "the model it moved to gets the documented dummy one"
        assert contents[1]["parts"][0]["thoughtSignature"] == "REAL_SIG", "the caller's history is never modified"
        after = row(llm)
        assert after["ok"] == before["ok"] + 1 and after["fallback"] == before["fallback"], (before, after)
        # a timeout, every model busy (the fallback), and a 400 that must not advance
        cool_down_reset(llm)
        stub.play({STRONG: "url-timeout"}, ok=OK_TOOLS)
        reply, offline = asyncio.run(llm.complete_tools(contents, decls, model=STRONG, fallback={"off": True}, timeout=10, cache=False))
        assert offline is False and stub.models() == [STRONG, B], stub.models()
        cool_down_reset(llm)
        stub.play({STRONG: 503, B: 502, C: 504}, ok=OK_TOOLS)
        reply, offline = asyncio.run(llm.complete_tools(contents, decls, model=STRONG, fallback={"off": True}, timeout=10, surface="chain", cache=False))
        assert offline is True and reply == {"off": True} and stub.models() == [STRONG, B, C], (offline, stub.models())
        cool_down_reset(llm)
        stub.calls.clear()
        try:
            asyncio.run(llm.complete_tools(contents, decls, model=STRONG, timeout=10, cache=False))
        except Exception as e:  # noqa: BLE001
            assert getattr(e, "status_code", None) == 502 and "(504)" in str(e.detail), e
        else:
            raise AssertionError("no fallback and every model busy: the call raises")
        cool_down_reset(llm)
        stub.play({STRONG: 403}, ok=OK_TOOLS)
        reply, offline = asyncio.run(llm.complete_tools(contents, decls, model=STRONG, fallback={"off": True}, timeout=10, cache=False))
        assert offline is True and stub.models() == [STRONG], stub.models()

    # --------------------------------------------------------------------------------------------- complete_grounded
    def grounded_advances_on_every_busy_kind(llm, stub):
        for how in (503, 500, 504, "timeout", "reset"):
            cool_down_reset(llm)
            stub.play({STRONG: how}, ok=OK_GROUNDED)
            res, offline = asyncio.run(llm.complete_grounded("chain: grounded", model=STRONG, fallback={"off": True}, timeout=10, surface="chain"))
            assert offline is False and res["text"] == "grounded answer" and res["model"] == B and stub.models() == [STRONG, B], (how, offline, stub.models())
        cool_down_reset(llm)
        stub.play({STRONG: 503, B: 503, C: 503}, ok=OK_GROUNDED)
        before = row(llm)
        res, offline = asyncio.run(llm.complete_grounded("chain: grounded, all busy", model=STRONG, fallback={"off": True}, timeout=10, surface="chain"))
        assert offline is True and res == {"off": True} and stub.models() == [STRONG, B, C], (offline, stub.models())
        assert row(llm)["fallback"] == before["fallback"] + 1 and row(llm)["ok"] == before["ok"], row(llm)
        cool_down_reset(llm)
        stub.play({STRONG: 401}, ok=OK_GROUNDED)
        res, offline = asyncio.run(llm.complete_grounded("chain: grounded, 401", model=STRONG, fallback={"off": True}, timeout=10))
        assert offline is True and stub.models() == [STRONG], stub.models()

    ctx.check("llm chain: a busy first model (503) answers from the second, counted once as ok, and is skipped while it cools down", sandbox(busy_first_model_answers_from_the_second))
    ctx.check("llm chain: 500, 502, 503, 504, a timeout or a dropped connection each advance to the next model", sandbox(every_5xx_and_a_dropped_connection_advance))
    ctx.check("llm chain: every model busy ends in the fallback (one fallback counted) or the same 502 without one", sandbox(all_busy_ends_in_the_fallback_or_the_raise))
    ctx.check("llm chain: a 400 (or any 4xx but 429) does not advance; the thinking retry and the 429 chain still work", sandbox(a_400_does_not_advance))
    ctx.check("llm chain: an unreachable network is one failure, not a loop over the models", sandbox(a_network_that_is_down_does_not_loop))
    ctx.check("llm chain: the caller's timeout is one ceiling for the whole chain, with a reserve for the model behind", sandbox(the_timeout_is_one_ceiling_for_the_whole_chain))
    ctx.check("llm chain: a stronger model that is busy hands over to the app's default model first, then the fallbacks", sandbox(the_default_model_is_the_first_hand_over))
    ctx.check("llm chain: complete() (text) advances like complete_json", sandbox(plain_complete_advances))
    ctx.check("llm chain: complete_tools advances (dummy signatures for the second model), falls back or raises, never advances on a 4xx", sandbox(tools_advance_with_dummy_signatures))
    ctx.check("llm chain: complete_grounded advances on every busy kind and falls back after the last", sandbox(grounded_advances_on_every_busy_kind))
