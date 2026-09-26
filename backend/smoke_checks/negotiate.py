"""Smoke checks for Negotiate (backend/negotiate.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200).

The HTTP checks ask for the plain (rule-based) negotiation with ai=false, so a smoke run never spends Gemini quota; every
turn's verdict is recomputed here from the agreement, estimate and project routes (not from negotiate.py's code). The
verifier and the agentic loop run in-process: forged invalid proposals, a scripted stand-in for Gemini whose first
proposal the verifier rejects and whose revision it accepts (the findings must reach the agent's next prompt), and the
labeled fallback with GEMINI_API_KEY blank."""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

DISCLAIMER = "Draft for discussion, generated from public filings; not an agreement between the utilities."
SECOND = "DESC-6810O~GA-21116"  # a pair whose filed windows share months after today (Jan-Dec 2027)


def _ym(iso: str) -> tuple[int, int]:
    y, m = iso.split("-")[:2]
    return int(y), int(m)


def register(ctx):
    state = {}

    def top_overlap():
        if "id" not in state:
            o = ctx.request("GET", "/api/gridlock/opportunities?limit=1")
            assert o["opportunities"], "no opportunity to negotiate"
            state["id"] = o["opportunities"][0]["id"]
        return state["id"]

    def plain(oid, extra=""):
        return ctx.request("POST", f"/api/negotiate/{oid}?ai=false{extra}")

    def shape():
        oid = top_overlap()
        r = plain(oid)
        state["top"] = r
        assert r["overlap_id"] == oid and r["by"] == "fallback" and r["fallback_reason"] == "Plain version requested", (r["by"], r["fallback_reason"])
        assert r["disclaimer"] == DISCLAIMER and r["calls"] == 0 and r["max_calls"] <= 8 and r["max_rounds"] == 3, r
        assert [a["side"] for a in r["agents"]] == ["a", "b"] and r["agents"][0]["utility"] != r["agents"][1]["utility"], r["agents"]
        for a in r["agents"]:
            assert "AI agent reading" in a["represents"] and a["utility_name"] in a["represents"], a["represents"]
            assert a["not_the_utility"].startswith("Not ") and a["filing"] and a["source"]["kind"] == "filing", a
        assert 2 <= len(r["turns"]) <= 8, len(r["turns"])
        for n, t in enumerate(r["turns"], 1):
            assert t["n"] == n and t["agent"] in ("a", "b") and 1 <= t["round"] <= 3 and t["kind"] in ("propose", "counter", "accept", "revise"), t
            assert isinstance(t["verdict"]["ok"], bool) and isinstance(t["verdict"]["findings"], list), t["verdict"]
            assert {d["k"] for d in t["plain"]} == {"window", "scope", "split"}, t["plain"]
        o = r["outcome"]
        assert o["agreed"] is True and o["verified"] is True and o["terms"], o
        assert r["turns"][-1]["kind"] == "accept" and r["turns"][-1]["verdict"]["ok"], r["turns"][-1]
        assert sum(o["terms"]["split"]["shares"]) == 100, o["terms"]["split"]
        if not o["terms"]["joint_window"]:
            assert r["bounds"]["feasible"] is None and o["next"] and "current status" in o["next"], (r["bounds"], o["next"])

    def verdicts_recomputed():
        """Each turn's window, scope and split checked again from the agreement, estimate and projects routes."""
        for oid in (top_overlap(), SECOND):
            r = state["top"] if oid == state.get("id") and "top" in state else plain(oid)
            d = ctx.request("GET", f"/api/agreement/{oid}?ai=false")
            est = ctx.request("GET", f"/api/gridlock/estimate/{oid}")
            items = {it["id"]: it for it in est["items"]}
            projs = {p["side"]: p for p in d["overlap"]["projects"]}
            now = _ym(d["draft"]["joint_window"]["as_of"])
            wins = {s: (_ym(p["window"]["start"]), _ym(p["window"]["end"])) if p.get("window") else None for s, p in projs.items()}
            lo = max([w[0] for w in wins.values() if w] + [now]) if all(wins.values()) else None
            hi = min(w[1] for w in wins.values() if w) if all(wins.values()) else None
            feasible = (lo, hi) if lo and hi and lo <= hi else None
            ma, mb = projs["a"].get("miles"), projs["b"].get("miles")
            shares = {"equal": [50, 50]}
            if ma and mb:
                pa_ = round(100 * ma / (ma + mb))
                shares["by_length"] = [pa_, 100 - pa_]
            if projs["a"]["kv"] and projs["b"]["kv"]:
                ka, kb = max(projs["a"]["kv"]), max(projs["b"]["kv"])
                pk = round(100 * ka / (ka + kb))
                shares["by_kv"] = [pk, 100 - pk]
            assert {x["id"]: x["shares"] for x in r["rules"]} == shares, (r["rules"], shares)
            for t in r["turns"]:
                p = t["proposal"]
                ok = True
                jw = p["joint_window"]
                if jw:
                    s, e = _ym(jw["start"]), _ym(jw["end"])
                    ok &= bool(feasible) and feasible[0] <= s <= e <= feasible[1]
                else:
                    ok &= feasible is None
                ok &= bool(p["scope"]) and all(i in items for i in p["scope"]) and not p.get("scope_unknown")
                sp = p["split"]
                ok &= sp["rule"] in shares and sum(sp["shares"]) == 100 and list(sp["shares"]) == shares[sp["rule"]]
                assert ok == t["verdict"]["ok"], (oid, t["n"], p, t["verdict"])
            terms = r["outcome"]["terms"]
            lo_sum = sum(items[i["id"]]["low"] for i in terms["scope"] if items[i["id"]]["unit"] == "USD")
            hi_sum = sum(items[i["id"]]["high"] for i in terms["scope"] if items[i["id"]]["unit"] == "USD")
            assert terms["savings"]["low"] == lo_sum and terms["savings"]["high"] == hi_sum, (terms["savings"], lo_sum, hi_sum)
            if oid == SECOND:
                assert feasible and terms["joint_window"], (feasible, terms["joint_window"])

    def unknown_is_404_bad_is_422():
        ctx.request("POST", "/api/negotiate/NOPE-1~NADA-2?ai=false", expect=404)
        ctx.request("POST", "/api/negotiate/no-tilde?ai=false", expect=404)
        p = ctx.request("GET", "/api/gridlock/projects")["projects"]
        desc = [x["id"] for x in p if x["utility"] == "DESC" and x.get("geometry")][:2]
        if len(desc) == 2:
            ctx.request("POST", f"/api/negotiate/{desc[0]}~{desc[1]}?ai=false", expect=404)
        oid = top_overlap()
        ctx.request("POST", f"/api/negotiate/{oid}?ai=false&lang=fr", expect=422)
        ctx.request("POST", f"/api/negotiate/{oid}?ai=false&window_months=999", expect=422)
        ctx.request("POST", f"/api/negotiate/{'x' * 250}~y?ai=false", expect=422)
        ctx.request("GET", f"/api/agreement/{oid}?ai=false&negotiated=maybe", expect=422)
        ctx.request("GET", "/api/agreement/NOPE-1~NADA-2?ai=false&negotiated=plain", expect=404)
        live = ctx.request("GET", f"/api/negotiate/{oid}/live")
        assert live == {"running": False}, live

    def cached_second_time():
        oid = top_overlap()
        first = plain(oid, "&window_months=36")
        again = plain(oid, "&window_months=36")
        assert again["cached"] is True and again["turns"] == first["turns"] and again["outcome"] == first["outcome"], "the cached negotiation differs"

    def terms_feed_the_draft():
        for oid in (top_overlap(), SECOND):
            r = plain(oid)
            t = r["outcome"]["terms"]
            doc = ctx.request("GET", f"/api/agreement/{oid}?ai=false&negotiated=plain")
            assert doc["negotiated"]["applied"] is True and doc["negotiated"]["by"] == "plain", doc["negotiated"]
            assert doc["verified"] is True, doc["rejected"]
            d = doc["draft"]
            assert d["negotiated"] and "verified against the filings" in d["negotiated"]["text"], d["negotiated"]
            assert [s["pct"] for s in d["cost_split"]["shares"]] == t["split"]["shares"], (d["cost_split"], t["split"])
            assert [i["id"] for i in d["savings"]["items"]] == [i["id"] for i in t["scope"]], d["savings"]["items"]
            assert any("verified against the filings" in c["text"] for c in d["conditions"]), d["conditions"]
            if t["joint_window"]:
                assert d["joint_window"]["negotiated"]["start"].startswith(t["joint_window"]["start"]), d["joint_window"]
                assert d["joint_window"]["text"].startswith("Negotiated joint window"), d["joint_window"]["text"]
            else:
                assert d["joint_window"]["negotiated"] is None, d["joint_window"]
            plain_doc = ctx.request("GET", f"/api/agreement/{oid}?ai=false")
            assert plain_doc["negotiated"] is None and plain_doc["draft"]["negotiated"] is None, plain_doc["negotiated"]

    # ------------------------------------------------------------------ in-process

    def in_process(fn, key=None):
        def run():
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")  # llm.py imports auth.py; this process only
            try:
                import negotiate
            except ImportError as e:  # a deployed run without the backend folder on the path
                raise AssertionError(f"negotiate.py not importable here: {e}") from e
            saved = os.environ.get("GEMINI_API_KEY")
            os.environ["GEMINI_API_KEY"] = key if key is not None else ""
            try:
                fn(negotiate)
            finally:
                if saved is None:
                    os.environ.pop("GEMINI_API_KEY", None)
                else:
                    os.environ["GEMINI_API_KEY"] = saved

        return run

    def forged_turns_rejected(ng):
        case = ng._case(SECOND, 24)
        r = case["rules_by_id"]["by_length"]["shares"]

        def forge(**kw):
            base = {"joint_window": {"start": "2027-01", "end": "2027-12"}, "scope": ["mobilization"],
                    "split": {"rule": "by_length", "a_pct": r[0], "b_pct": r[1]}, "concerns": [], "note": "", "accept": False}
            base.update(kw)
            return base

        v, _ = ng.verify(forge(), case)
        assert v["ok"], v
        bad = {
            "window outside the filings": (forge(joint_window={"start": "2026-10", "end": "2030-06"}), "outside DESC's filed build window"),
            "window in the past": (forge(joint_window={"start": "2025-06", "end": "2027-06"}), "before today"),
            "split not summing to 100": (forge(split={"rule": "by_length", "a_pct": 60, "b_pct": 50}), "not 100"),
            "shares not the rule's": (forge(split={"rule": "equal", "a_pct": 40, "b_pct": 60}), "not 40 % and 60 %"),
            "a rule that isn't allowed": (forge(split={"rule": "by_cost", "a_pct": 50, "b_pct": 50}), "not an allowed split rule"),
            "an unknown scope id": (forge(scope=["mobilization", "helicopters"]), "'helicopters' is not one of the estimate's items"),
            "an invented number": (forge(concerns=["Sharing would cut 37 truck trips."]), "number 37 is not in the facts"),
            "an invented dollar amount": (forge(note="Sharing crews could save $412K."), "money figure 412 is not one of the facts' dollar amounts"),
            "speaking for a utility": (forge(note="Georgia Power wants the crews first."), "speaks for a utility"),
            "writing as 'we'": (forge(note="We accept the window."), "third person"),
            "no window when the filings leave one": (forge(joint_window={"start": "", "end": ""}), "propose a window inside it"),
        }
        for name, (raw, want) in bad.items():
            v, _ = ng.verify(raw, case)
            assert not v["ok"] and any(want in f for f in v["findings"]), (name, v["findings"])
        # the top pair's shared months have passed: a window is rejected, none is right
        past = ng._case("DESC-6852~GA-21116", 24, as_of=date(2026, 9, 26))
        v, _ = ng.verify(forge(joint_window={"start": "2026-10", "end": "2027-03"}), past)
        assert not v["ok"] and any("ended before today" in f for f in v["findings"]), v
        v, _ = ng.verify(forge(joint_window={"start": "", "end": ""}, split={"rule": "equal", "a_pct": 50, "b_pct": 50}), past)
        assert v["ok"], v

    def agentic_loop(ng):
        """A stand-in for Gemini: agent A's first proposal breaks the filings, the verifier rejects it, the findings reach
        A's next prompt, A revises, B accepts. The loop, not Gemini, is what's under test."""
        import llm

        prompts = []
        case = ng._case(SECOND, 24)
        good_rule = case["rules_by_id"]["by_length"]["shares"]
        good = {"joint_window": {"start": "2027-02", "end": "2027-11"}, "scope": ["mobilization"],
                "split": {"rule": "by_length", "a_pct": good_rule[0], "b_pct": good_rule[1]},
                "concerns": ["As filed, the build window runs Jan 2027 to Dec 2027."], "note": "This agent proposes a window inside the filing.", "accept": False}

        async def fake(prompt, system=None, fallback=None, timeout=None, schema=None, cache=True, surface=None, model=None, image=None):
            prompts.append(prompt)
            assert surface == "negotiation" and schema and "never speak for it" in system
            if prompt.startswith("You are agent A") and "THE PIPELINE REJECTED YOUR LAST PROPOSAL" not in prompt:
                return {**good, "joint_window": {"start": "2029-01", "end": "2030-06"}}, False  # outside DESC's filed window
            if prompt.startswith("You are agent A"):
                return good, False
            return {**good, "concerns": [], "note": "This agent accepts the proposal on the table.", "accept": True}, False

        saved = llm.complete_json
        llm.complete_json = fake
        try:
            out = asyncio.run(ng.run_case(SECOND, 24, "en", True, as_of=date(2026, 9, 26)))
        finally:
            llm.complete_json = saved
        assert out["by"] == "gemini" and out["calls"] == 3 and len(out["turns"]) == 3, (out["by"], out["calls"], out["fallback_reason"])
        t1, t2, t3 = out["turns"]
        assert not t1["verdict"]["ok"] and any("outside DESC's filed build window" in f for f in t1["verdict"]["findings"]), t1["verdict"]
        assert t2["revision"] and t2["kind"] == "revise" and t2["verdict"]["ok"], t2
        assert "THE PIPELINE REJECTED YOUR LAST PROPOSAL" in prompts[1] and "outside DESC's filed build window" in prompts[1], prompts[1][-600:]
        assert t3["agent"] == "b" and t3["kind"] == "accept" and t3["verdict"]["ok"], t3
        o = out["outcome"]
        assert o["agreed"] and o["verified"] and o["terms"]["joint_window"] == {"start": "2027-02", "end": "2027-11"}, o
        # agent B's prompt carries only B's own filing (plus the shared facts), never A's filing facts
        b_prompt = prompts[2]
        mine = b_prompt.split("YOUR FILING")[1].split("SHARED FACTS")[0]
        assert "- b." in mine and "- a." not in mine, mine[:400]

    def fallback_without_a_key(ng):
        out = asyncio.run(ng.run_case(SECOND, 24, "es", True, as_of=date(2026, 1, 15)))  # a date no live run has: never an llm.py cache hit
        assert out["by"] == "fallback" and out["fallback_reason"] == "Gemini not configured", (out["by"], out["fallback_reason"])
        assert out["outcome"]["agreed"] and out["outcome"]["verified"] and all(t["verdict"]["ok"] for t in out["turns"]), out["outcome"]
        again = asyncio.run(ng.run_case(SECOND, 24, "es", True, as_of=date(2026, 1, 15)))
        assert again["cached"] is False, "a transient Gemini miss must not be cached"

    ctx.check("negotiate: the plain negotiation of the top opportunity has two filing-reading agents, verified turns and agreed terms", shape)
    ctx.check("negotiate: every turn's verdict recomputed from the agreement, estimate and project routes (window, scope, split)", verdicts_recomputed)
    ctx.check("negotiate: an unknown or same-utility pair is a 404; bad lang, window or id is a 422; no live run", unknown_is_404_bad_is_422)
    ctx.check("negotiate: the same negotiation again is served from the cache", cached_second_time)
    ctx.check("negotiate: the agreed terms feed the drafted agreement (split, scope, window) and the draft says they were negotiated", terms_feed_the_draft)
    ctx.check("negotiate: the verifier rejects a window outside the filings, a split off 100, an unknown scope id, an invented number, speaking for a utility",
              in_process(forged_turns_rejected))
    ctx.check("negotiate: a rejected turn's findings reach the agent's next prompt, its revision passes, the other agent accepts",
              in_process(agentic_loop, key="smoke-fake-key-never-sent"))
    ctx.check("negotiate: with GEMINI_API_KEY blank the labeled plain version runs (and is not cached)", in_process(fallback_without_a_key))
