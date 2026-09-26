"""Smoke checks for the AI analyst (backend/analyst.py): "What it would take" for a proposal, worked out by a Gemini
agent that calls the engine as tools, or by the fixed plan without a key. Loaded by smoke_test.py.

Public and read-only: nothing is stored but the in-memory cache. Without a key (scripts/check.sh blanks it) every
analysis must come back complete and labeled fallback; with one it may be Gemini's, but every number in its memo must
still be one of the tool results the trace carries (checked here independently of the server's own check).

The agent runs on Gemini's native function calling: its tool rows carry via "function_call" (and the engine's result
via "function_response", the same call id). The conversation it sends (functionResponse after functionCall, the thought
signature echoed, the dummy signature for another model) is checked in-process against stubbed replies: no network."""

import asyncio
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

HERO = "stonebridge-fort-meade"  # 1,200 MW reported; the model's room at the site is ~244 MW (CLAUDE.md → FLORIDA FIVE)
NUM = re.compile(r"\d+(?:[,.]\d+)*")
# numbers spelled out ("eleven lines") count as numbers here too, independently of the server's own conversion
_W = {w: i + 2 for i, w in enumerate("two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_T = {w: (i + 2) * 10 for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_O = {w: i + 1 for i, w in enumerate("one two three four five six seven eight nine".split())}
WORDS = re.compile(r"\b(?:(" + "|".join(_T) + r")(?:-(" + "|".join(_O) + r"))?|(" + "|".join(_W) + r"))\b", re.I)


def _as_digits(text: str) -> str:
    return WORDS.sub(lambda m: str(_W[m.group(3).lower()]) if m.group(3) else str(_T[m.group(1).lower()] + (_O[m.group(2).lower()] if m.group(2) else 0)), text)
NEVER = re.compile(r"Stonebridge|Bohler|will cause|will black|\bblame|\b(FPL|Duke Energy|TECO|JEA|NextEra)\b", re.I)


def _canon(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.2f}".rstrip("0").rstrip(".")


def _forms(v: float) -> set:
    v = abs(float(v))
    out = {_canon(v), _canon(round(v, 1)), _canon(round(v)), _canon(math.floor(v)), _canon(math.ceil(v))}
    for k in range(1, 7):
        p = 10**k
        out |= {_canon(r) for r in (round(v / p) * p, math.floor(v / p) * p, math.ceil(v / p) * p) if r}
    for scale in (1e3, 1e6, 1e9):
        for d in (0, 1, 2):
            out |= {_canon(r) for r in (round(v / scale, d), math.floor(v / scale * 10**d) / 10**d, math.ceil(v / scale * 10**d) / 10**d) if r}
    return out


def _walk(x, acc: set) -> None:
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        acc |= _forms(x)
    elif isinstance(x, str):
        for tok in NUM.findall(x):
            acc |= _forms(float(tok.replace(",", "")))
    elif isinstance(x, dict):
        for v in x.values():
            _walk(v, acc)
    elif isinstance(x, (list, tuple)):
        if x:
            acc |= _forms(len(x))  # a count ("4 verified ways") is a result too
        for v in x:
            _walk(v, acc)


def _memo_text(r: dict) -> str:
    m = r["memo"]
    return " ".join([m["headline"]] + [s["text"] for s in m["sections"]])


def _shape(r: dict) -> None:
    assert r["by"] in ("gemini", "fallback"), r["by"]
    assert r["verified"] is True and isinstance(r["calls"], int) and r["tool_calls"] >= 1, (r["verified"], r["calls"], r["tool_calls"])
    m = r["memo"]
    assert m["headline"] and len(m["sections"]) >= 3 and "SYNTHETIC" in m["frame"], m
    keys = [s["key"] for s in m["sections"]]
    assert set(keys) <= {"fits_here", "full_size", "nearby", "strain"} and len(set(keys)) == len(keys), keys
    assert all(s["heading"] and len(s["text"]) > 20 for s in m["sections"]), m["sections"]
    tr = r["trace"]
    assert tr and [t["n"] for t in tr] == list(range(1, len(tr) + 1)), [t["n"] for t in tr]
    assert all(t["actor"] in ("gemini", "engine") and t["title"] for t in tr), tr[:2]
    results = [t for t in tr if t["kind"] == "result"]
    assert len(results) == r["tool_calls"] and all(t.get("result") is not None and t.get("result_summary") for t in results), len(results)
    assert any(t["kind"] == "check" for t in tr), "the memo's number check is not in the trace"
    if r["by"] == "fallback":
        assert isinstance(r["why"], str) and r["why"], r["why"]
    else:
        assert r["calls"] >= 1 and any(t["actor"] == "gemini" and t["kind"] == "call" for t in tr), "a Gemini run with no tool call of its own"


def _native(r: dict, configured: bool) -> int:
    """Gemini's tool rows are native function calls: each call row (via function_call, a call like whatif_size(mw=600))
    is followed by the engine's result row (via function_response, the same call id). Returns the number of them."""
    tr = r["trace"]
    fc = [i for i, t in enumerate(tr) if t["actor"] == "gemini" and t["kind"] == "call"]
    for i in fc:
        t, nxt = tr[i], tr[i + 1] if i + 1 < len(tr) else {}
        assert t.get("via") == "function_call" and re.fullmatch(r"[a-z_]+\(.*\)", t.get("call") or ""), (t.get("via"), t.get("call"))
        assert nxt.get("kind") == "result" and nxt.get("via") == "function_response" and nxt.get("call_id") == t.get("call_id"), (nxt.get("kind"), nxt.get("via"))
    for t in tr:  # the fixed plan's own runs are the engine's, never labeled as Gemini's
        if t["actor"] == "engine" and t["kind"] == "call":
            assert t.get("via") is None, t
    assert r["function_calls"] >= len(fc), (r["function_calls"], len(fc))
    if not configured:
        assert not fc and r["function_calls"] == 0 and r["function_calling"] is False, (len(fc), r["function_calls"])
    if r["by"] == "gemini":
        assert r["function_calling"] is True and fc, "a Gemini memo without a native function call"
    return len(fc)


def _numbers_traced(r: dict) -> int:
    allowed = {"0", "1"}
    for t in r["trace"]:
        if t["kind"] == "result":
            _walk(t["args"], allowed)
            _walk(t["result"], allowed)
    _walk(r["case"], allowed)
    toks = NUM.findall(_as_digits(_memo_text(r)))
    bad = [x for x in toks if _canon(float(x.replace(",", ""))) not in allowed]
    assert not bad, f"memo numbers that match no tool result: {bad}"
    return len(toks)


def register(ctx):
    state = {}

    def hero_analysis():
        configured = ctx.request("GET", "/api/ai/status")["configured"]
        r = ctx.request("POST", f"/api/analyst/{HERO}")
        state["r"] = r
        _shape(r)
        _native(r, configured)
        assert r["case"]["id"] == HERO and r["case"]["mw"] == 1200 and r["case"]["region"] == "FL", r["case"]
        if not configured:
            assert r["by"] == "fallback" and r["calls"] == 0 and "not configured" in r["why"], (r["by"], r["calls"], r["why"])
            assert [t["tool"] for t in r["trace"] if t["kind"] == "result"] == ["whatif_size", "site_report_nearby", "ways_to_build", "best_sites_nearby", "service"]
        # the hero: the full size doesn't fit at the site's substation (room ~244 MW), and the engine verified ways to build it
        w = next(t["result"] for t in r["trace"] if t["kind"] == "result" and t["tool"] == "whatif_size" and abs(t["args"]["mw"] - 1200) < 1) if any(
            t["kind"] == "result" and t["tool"] == "whatif_size" for t in r["trace"]) else None
        if w is not None:
            assert not w["fits"] and 150 <= w["room_mw"] <= 400 and w["lines_over_limit"] >= 1, (w["fits"], w["room_mw"], w["lines_over_limit"])
        m = NEVER.search(_memo_text(r) + " " + " ".join(t["title"] for t in r["trace"]))
        assert not m, f"the analyst names the real project/company or blames: {m.group(0)!r}"

    def memo_numbers_trace_to_tools():
        n = _numbers_traced(state["r"])
        assert n >= 3 and state["r"]["memo"].get("numbers_checked", n) >= 1, n

    def cached_and_live():
        t0 = time.time()
        again = ctx.request("POST", f"/api/analyst/{HERO}")
        assert again["cached"] is True and again["memo"] == state["r"]["memo"], "the second run is not the cached one"
        assert time.time() - t0 < 5, "a cached analysis should come back at once"
        live = ctx.request("POST", "/api/analyst", {"id": HERO, "live": True})
        assert live["status"] == "done" and live["result"]["memo"] == state["r"]["memo"] and live["job"] is None, live.get("status")
        # a case (not a catalog id), live: a job to poll until it is done
        job = ctx.request("POST", "/api/analyst", {"region": "FL", "lat": 26.64, "lon": -81.87, "mw": 300, "live": True})
        assert job["status"] in ("running", "done"), job
        res = job.get("result")
        deadline = time.time() + 60
        while res is None and time.time() < deadline:
            time.sleep(1.0)
            view = ctx.request("GET", f"/api/analyst/jobs/{job['job']}")
            assert view["status"] in ("running", "done"), view.get("error")
            assert [t["n"] for t in view["trace"]] == list(range(1, len(view["trace"]) + 1))
            res = view.get("result")
        assert res is not None, "the live analysis did not finish within 60 s"
        _shape(res)
        _native(res, ctx.request("GET", "/api/ai/status")["configured"])
        _numbers_traced(res)
        assert res["case"]["mw"] == 300 and "id" not in res["case"], res["case"]

    def rejects_bad_input():
        ctx.request("POST", "/api/analyst/no-such-campus", expect=404)
        ctx.request("POST", "/api/analyst/UPPER-case", expect=422)
        ctx.request("POST", "/api/analyst/" + "a" * 121, expect=422)
        ctx.request("POST", "/api/analyst", {}, expect=422)
        ctx.request("POST", "/api/analyst", {"lat": 40.0, "lon": -81.0, "mw": 300}, expect=422)  # north of Florida
        ctx.request("POST", "/api/analyst", {"region": "XX", "lat": 27.7, "lon": -81.8, "mw": 300}, expect=422)
        ctx.request("POST", "/api/analyst", {"lat": 27.75, "lon": -81.8, "mw": 0}, expect=422)
        ctx.request("POST", "/api/analyst", {"id": "../etc"}, expect=422)
        ctx.request("GET", "/api/analyst/jobs/nosuchjob123", expect=404)
        ctx.request("GET", "/api/analyst/jobs/bad!", expect=422)

    # ------------------------------------------------------------------ in-process, stubbed (no network, no quota)
    def in_process(fn):
        def run():
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")  # llm.py imports auth.py; this process only
            try:
                import analyst
                import llm
            except ImportError as e:  # a deployed run without the backend folder on the path
                raise AssertionError(f"analyst.py not importable here: {e}") from e
            saved = {"key": os.environ.get("GEMINI_API_KEY"), "post": llm._post_json, "file": llm.CACHE_FILE, "run_tool": analyst.run_tool, "cache": llm._cache}
            os.environ["GEMINI_API_KEY"] = "stub-not-a-key"  # never sent: _post_json is stubbed
            llm.CACHE_FILE = ""  # stubbed replies never reach the disk cache
            # an empty answer cache of its own: nothing loaded from disk can answer a stubbed turn, and the stubbed turns
            # are thrown away afterwards (so a later save of the real cache can never carry them to disk)
            llm._cache = type(saved["cache"])()
            try:
                fn(analyst, llm)
            finally:
                llm._post_json, llm.CACHE_FILE, analyst.run_tool, llm._cache = saved["post"], saved["file"], saved["run_tool"], saved["cache"]
                if saved["key"] is None:
                    os.environ.pop("GEMINI_API_KEY", None)
                else:
                    os.environ["GEMINI_API_KEY"] = saved["key"]

        return run

    WHATIF = {"mw": 1200.0, "substation": "Stub", "kv": 230.0, "room_mw": 244.0, "fits": False, "lines_over_limit": 11, "worst_lines": [],
              "strain_with_campus": {"busiest_line_pct": 141.0, "lines_at_90pct_or_more": 14, "lines_over_limit": 11},
              "strain_grid_alone": {"busiest_line_pct": 88.0, "lines_at_90pct_or_more": 0, "lines_over_limit": 0},
              "strain_threshold_pct": 90.0, "limit_pct": 100.0, "cascade": {"steps": 6, "people_without_power": 53000, "campus_cut_off": True}}
    SERVICE = {"mw": 1200.0, "flexible": {"meaning": "cut off", "people_without_power": 53000, "steps": 6, "campus_cut_off": True},
               "firm": {"meaning": "kept on", "people_without_power": 90000, "steps": 7, "campus_kept_on": True, "load_cut_mw": 300.0}}
    MEMO = {"headline": "On the model 1,200 MW is past what this site can take.",
            "fits_here": "The site has room for 244 MW; at 1,200 MW, 11 lines go over their limit.",
            "full_size": "On firm service about 90,000 people lose power instead of about 53,000.",
            "nearby": "No nearby site was checked in this run.",
            "strain": "The busiest line runs at 141% with the campus and 88% without; 14 lines run at 90% or more."}

    WHY_A = "First the full 1,200 MW: does it fit at this substation?"

    def stub_case(analyst):
        return SimpleNamespace(mw=1200.0, state_name="Florida", sub_name="Stub", kv=230.0, town="Stubton", entry=None, key="stub-case-" + str(time.time()),
                               describe=lambda: {"region": "FL", "mw": 1200.0, "substation": "Stub", "kv": 230.0})

    def conversation_is_native(analyst, llm):
        call_a = {"functionCall": {"name": "whatif_size", "args": {"mw": 1200, "why": WHY_A}, "id": "call_a"}, "thoughtSignature": "SIG_A"}
        call_b = {"functionCall": {"name": "service", "args": {"why": "Firm against flexible: who loses power?"}, "id": "call_b"}}  # a parallel call: no signature of its own
        replies = [{"candidates": [{"content": {"role": "model", "parts": [{"text": "First the full size, then firm against flexible service."}, call_a, call_b]}}]},
                   {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps({"thought": "It does not fit.", "memo": MEMO}), "thoughtSignature": "SIG_B"}]}}]}]
        bodies = []

        def post(url, body, key, timeout):
            bodies.append(json.loads(json.dumps(body)))
            return json.loads(json.dumps(replies[len(bodies) - 1]))

        llm._post_json = post
        analyst.run_tool = lambda c, tool, args: ({"whatif_size": WHATIF, "service": SERVICE}[tool], 1, False)
        run = analyst.Run(stub_case(analyst))
        memo, status = asyncio.run(analyst._agent(run))
        assert status == "used" and memo and memo["numbers_checked"] >= 5, (status, memo)
        assert len(bodies) == 2 and run.calls == 2 and run.tools == 2 and run.fcalls == 2, (len(bodies), run.calls, run.tools, run.fcalls)
        assert len(run.cache_keys) == 2 and all(k in llm._cache for k in run.cache_keys), "a run that passed keeps its turns cached (a replay is the same run)"
        b0, b1 = bodies
        decls = b0["tools"][0]["functionDeclarations"]
        assert [d["name"] for d in decls] == list(analyst.TOOLS), b0["tools"]
        mw = next(d for d in decls if d["name"] == "whatif_size")["parameters"]["properties"]["mw"]
        assert mw["minimum"] >= 1 and mw["maximum"] == 50000, mw  # the bounds _clean_args enforces
        # every declaration asks for Gemini's reason (the trace's line under each call)
        assert all(d["parameters"]["properties"]["why"]["type"] == "string" and "why" in d["parameters"]["required"] for d in decls), decls
        assert b0["toolConfig"]["functionCallingConfig"]["mode"] == "ANY" and "responseJsonSchema" not in b0.get("generationConfig", {}), b0.get("toolConfig")
        # the second request: the case, the model's reply VERBATIM (signature on the first call only), then one user
        # turn with a functionResponse per call, same ids and names, in order, after all the calls
        c1 = b1["contents"]
        assert c1[0] == b0["contents"][0] and c1[1] == replies[0]["candidates"][0]["content"], c1[1]
        assert c1[1]["parts"][1]["thoughtSignature"] == "SIG_A" and "thoughtSignature" not in c1[1]["parts"][2]
        fr = [p["functionResponse"] for p in c1[2]["parts"]]
        assert c1[2]["role"] == "user" and [(f["id"], f["name"]) for f in fr] == [("call_a", "whatif_size"), ("call_b", "service")], fr
        assert fr[0]["response"]["room_mw"] == 244.0 and fr[1]["response"]["firm"]["people_without_power"] == 90000, fr
        assert b1["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED" and b1["generationConfig"]["responseJsonSchema"] == analyst.MEMO_SCHEMA
        rows = [t for t in run.trace if t.get("via") in ("function_call", "function_response")]
        assert [(t["kind"], t["call_id"]) for t in rows] == [("call", "call_a"), ("result", "call_a"), ("call", "call_b"), ("result", "call_b")], rows
        assert rows[0]["call"] == "whatif_size(mw=1200)" and any(t["kind"] == "think" for t in run.trace), rows[0]["call"]
        # the reason is shown under its call (its 1,200 is the case's), and it never reaches the engine
        assert rows[0]["detail"] == WHY_A and rows[2]["detail"].startswith("Firm against flexible"), (rows[0].get("detail"), rows[2].get("detail"))
        assert all("why" not in r["args"] for r in run.results), run.results

    def spelled_numbers_are_checked(analyst, llm):
        results = [{"tool": "whatif_size", "args": {"mw": 1200.0}, "result": WHATIF}, {"tool": "service", "args": {}, "result": SERVICE}]
        good = {"headline": "On the model 1,200 MW is past what this site can take.", "sections": [
            {"key": "fits_here", "heading": "h", "text": "The site has room for 244 MW; at the full size eleven lines go over their limit."},
            {"key": "strain", "heading": "h", "text": "The busiest line runs at 141%, and fourteen lines run at 90% or more."}]}
        ok, bad, n, why = analyst.check_memo(good, stub_case(analyst), results)
        assert ok and n == 6 and not bad, (ok, bad, n, why)  # 1,200 · 244 · eleven (11) · 141 · fourteen (14) · 90: each spelled one counted
        wrong = copy_memo(good, "thirty-seven lines go over their limit")
        ok, bad, n, why = analyst.check_memo(wrong, stub_case(analyst), results)
        assert not ok and bad == ["37"], (ok, bad)
        assert analyst._digits("no one, eleven lines, twenty-four sites, ninety") == "no one, 11 lines, 24 sites, 90"

    def copy_memo(memo, text):
        m = json.loads(json.dumps(memo))
        m["sections"][0]["text"] = f"The site has room for 244 MW; at the full size {text}."
        return m

    def failed_run_leaves_the_cache(analyst, llm):
        """A repeated call is answered "already called" and not run; a memo that fails the number check twice ends
        the run "rejected", and that run's Gemini turns leave the answer cache (the next try is a fresh conversation)."""
        dup = {"functionCall": {"name": "whatif_size", "args": {"mw": 1200.0, "why": "Again."}, "id": "call_d"}}
        first = {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "whatif_size", "args": {"mw": 1200, "why": WHY_A}, "id": "call_a"}, "thoughtSignature": "SIG_A"}, dup]}}]}
        bad_memo = dict(MEMO, fits_here="The site has room for 244 MW; at 1,200 MW, thirty-seven lines go over their limit.")
        memo_turn = {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps({"memo": bad_memo}), "thoughtSignature": "SIG_M"}]}}]}
        replies = [first, memo_turn, memo_turn]
        bodies = []

        def post(url, body, key, timeout):
            bodies.append(json.loads(json.dumps(body)))
            return json.loads(json.dumps(replies[len(bodies) - 1]))

        llm._post_json = post
        ran = []
        analyst.run_tool = lambda c, tool, args: (ran.append(tool), ({"whatif_size": WHATIF, "service": SERVICE}[tool], 1, False))[1]
        run = analyst.Run(stub_case(analyst))
        memo, status = asyncio.run(analyst._agent(run))
        assert status == "rejected" and memo is None and len(bodies) == 3, (status, len(bodies))
        assert ran == ["whatif_size"] and run.tools == 1 and run.fcalls == 2, (ran, run.tools, run.fcalls)
        fr = [p["functionResponse"] for p in bodies[1]["contents"][2]["parts"]]
        assert [f["id"] for f in fr] == ["call_a", "call_d"] and "already called" in fr[1]["response"]["error"], fr
        assert any(t["kind"] == "refused" and t["title"].startswith("Not run again") for t in run.trace)
        checks = [t for t in run.trace if t["kind"] == "check"]
        assert len(checks) == 2 and "37" in checks[0]["bad"], checks  # ("90,000" too: service was never called)
        assert run.cache_keys == [] and len(llm._cache) == 0, "a rejected run's turns must leave the answer cache"

    def retries_before_falling_back(analyst, llm):
        """complete_tools: a model that refuses VALIDATED (400) is asked once more on AUTO without the generation config;
        a MALFORMED_FUNCTION_CALL reply is asked once more; malformed twice falls back."""
        seen = []
        good = {"candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [
            {"functionCall": {"name": "service", "args": {"why": "x"}, "id": "c9"}, "thoughtSignature": "S"}]}}]}

        def post(url, body, key, timeout):
            seen.append((url, json.loads(json.dumps(body))))
            if len(seen) == 1:
                raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(b'{"error": {"message": "Invalid value at tool_config.function_calling_config.mode (VALIDATED)"}}'))
            if len(seen) == 2:
                return {"candidates": [{"finishReason": "MALFORMED_FUNCTION_CALL"}]}
            return json.loads(json.dumps(good))

        llm._post_json = post
        contents = [{"role": "user", "parts": [{"text": "stub retries " + str(time.time())}]}]
        reply, offline = asyncio.run(llm.complete_tools(contents, analyst.FUNCTION_DECLARATIONS, model="stub-model-v", tool_mode="VALIDATED", schema=analyst.MEMO_SCHEMA,
                                                        thinking="minimal", fallback={"off": True}, cache=False))
        assert offline is False and len(seen) == 3 and reply["calls"][0]["id"] == "c9", (offline, len(seen))
        assert all("stub-model-v" in u for u, _ in seen), [u for u, _ in seen]
        b = [x for _, x in seen]
        assert b[0]["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED" and "generationConfig" in b[0]
        assert b[1]["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO" and "generationConfig" not in b[1], b[1].get("toolConfig")
        assert b[2] == b[1], "the malformed reply is asked once more with the same request"
        seen.clear()
        llm._post_json = lambda url, body, key, timeout: (seen.append(url), {"candidates": [{"finishReason": "MALFORMED_FUNCTION_CALL"}]})[1]
        reply, offline = asyncio.run(llm.complete_tools(contents, analyst.FUNCTION_DECLARATIONS, model="stub-model-w", fallback={"off": True}, cache=False))
        assert offline is True and reply == {"off": True} and len(seen) == 2, (offline, len(seen))

    def other_model_gets_dummy_signatures(analyst, llm):
        seen = []

        def post(url, body, key, timeout):
            seen.append((url, json.loads(json.dumps(body))))
            if len(seen) == 1:  # the conversation's model is out of quota: the chain moves on
                raise urllib.error.HTTPError(url, 429, "quota", {}, io.BytesIO(b'{"quotaId": "PerMinute"}'))
            return {"candidates": [{"content": {"role": "model", "parts": [{"text": "{}"}]}}]}

        llm._post_json = post
        contents = [{"role": "user", "parts": [{"text": "stub " + str(time.time())}]},
                    {"role": "model", "parts": [{"functionCall": {"name": "service", "args": {}, "id": "x1"}, "thoughtSignature": "REAL_SIG"}]},
                    {"role": "user", "parts": [llm.function_response({"name": "service", "id": "x1"}, {"ok": True})]}]
        reply, offline = asyncio.run(llm.complete_tools(contents, analyst.FUNCTION_DECLARATIONS, model="stub-model-a", fallback={"off": True}, cache=False))
        assert offline is False and len(seen) == 2 and "stub-model-a" in seen[0][0] and reply["model"] != "stub-model-a", (offline, [u for u, _ in seen])
        assert seen[0][1]["contents"][1]["parts"][0]["thoughtSignature"] == "REAL_SIG", "the conversation's own model gets its signature back"
        assert seen[1][1]["contents"][1]["parts"][0]["thoughtSignature"] == llm.DUMMY_SIGNATURE, "another model must get the documented dummy signature"
        assert contents[1]["parts"][0]["thoughtSignature"] == "REAL_SIG", "the caller's history is never modified"

    ctx.check("analyst (Fort Meade): a memo with sections, a numbered trace of tool calls and engine results, labeled Gemini or fallback", hero_analysis)
    ctx.check("analyst: every number in the memo is one of the tool results in its trace", memo_numbers_trace_to_tools)
    ctx.check("analyst: a second run is cached; live mode returns it at once, and a new case runs as a job to poll", cached_and_live)
    ctx.check("analyst rejects an unknown id (404), bad ids and cases (422), an unknown job (404)", rejects_bad_input)
    ctx.check("analyst: native function calling (stubbed): declarations with a required why, ANY then VALIDATED + memo schema, the reply echoed "
              "verbatim with its thought signature, one functionResponse per call with its id, Gemini's reason under each call", in_process(conversation_is_native))
    ctx.check("analyst: numbers spelled out in a memo ('eleven lines') are checked like digits", in_process(spelled_numbers_are_checked))
    ctx.check("analyst (stubbed): a repeated call is not run again; a memo rejected twice drops the run's turns from the answer cache",
              in_process(failed_run_leaves_the_cache))
    ctx.check("llm.complete_tools: a 429 moves the conversation to the next model with the dummy thought signature (stubbed)", in_process(other_model_gets_dummy_signatures))
    ctx.check("llm.complete_tools: VALIDATED refused (400) retries on AUTO; a malformed function call is asked once more, then falls back (stubbed)",
              in_process(retries_before_falling_back))
