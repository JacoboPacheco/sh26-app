"""Smoke checks for the AI analyst (backend/analyst.py): "What it would take" for a proposal, worked out by a Gemini
agent that calls the engine as tools, or by the fixed plan without a key. Loaded by smoke_test.py.

Public and read-only: nothing is stored but the in-memory cache. Without a key (scripts/check.sh blanks it) every
analysis must come back complete and labeled fallback; with one it may be Gemini's, but every number in its memo must
still be one of the tool results the trace carries (checked here independently of the server's own check)."""

import math
import re
import time

HERO = "stonebridge-fort-meade"  # 1,200 MW reported; the model's room at the site is ~244 MW (CLAUDE.md → FLORIDA FIVE)
NUM = re.compile(r"\d+(?:[,.]\d+)*")
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


def _numbers_traced(r: dict) -> int:
    allowed = {"0", "1"}
    for t in r["trace"]:
        if t["kind"] == "result":
            _walk(t["args"], allowed)
            _walk(t["result"], allowed)
    _walk(r["case"], allowed)
    toks = NUM.findall(_memo_text(r))
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

    ctx.check("analyst (Fort Meade): a memo with sections, a numbered trace of tool calls and engine results, labeled Gemini or fallback", hero_analysis)
    ctx.check("analyst: every number in the memo is one of the tool results in its trace", memo_numbers_trace_to_tools)
    ctx.check("analyst: a second run is cached; live mode returns it at once, and a new case runs as a job to poll", cached_and_live)
    ctx.check("analyst rejects an unknown id (404), bad ids and cases (422), an unknown job (404)", rejects_bad_input)
