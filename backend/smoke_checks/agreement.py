"""Smoke checks for Build agreement (backend/agreement.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200).

Read-only: /api/agreement/{overlap_id} is a public GET over the committed GridLock data. The HTTP checks ask
for the plain (template) draft with ai=false, so a smoke run never spends the day's Gemini quota; the checker
that guards Gemini's drafts is unit-tested in-process with a fabricated Gemini-style answer."""

import os
import re
import sys
from datetime import timedelta
from pathlib import Path

DISCLAIMER = "Draft for discussion, generated from public filings; not an agreement between the utilities."
NUM = re.compile(r"\d+(?:[,.]\d+)*")


def _values(tok: str) -> list[float]:
    t = tok.strip(",.")
    out = []
    try:
        out.append(float(t.replace(",", "")))
    except ValueError:
        pass
    return out


def _fact_numbers(facts: list) -> list[float]:
    """Every number the fact sheet holds (values and the numbers written in its text): an independent
    reading, not agreement.py's own checker."""
    nums: list[float] = []
    for f in facts:
        v = f.get("value")
        for x in v if isinstance(v, list) else [v]:
            if isinstance(x, bool) or x is None:
                continue
            if isinstance(x, (int, float)):
                nums.append(float(x))
            elif isinstance(x, str):
                for tok in NUM.findall(x):
                    nums += _values(tok)
        for tok in NUM.findall(str(f.get("text") or "")):
            nums += _values(tok)
    return nums


def _traceable(v: float, nums: list[float]) -> bool:
    """v is a fact's number, or that number rounded (within 3 %) or written in thousands / millions."""
    if v <= 10 and float(v).is_integer():
        return True  # small counts ("two utilities", "one window")
    for n in nums:
        for scale in (1.0, 1e3, 1e6, 1e9):
            x = abs(n) / scale
            if x > 0 and abs(v - x) <= max(0.03 * x, 0.005):
                return True
    return False


def _texts(d: dict) -> list[tuple[str, str]]:
    out = [("summary", d["summary"]["text"]), ("joint_window", d["joint_window"]["text"]), ("cost_split", d["cost_split"]["rationale"])]
    for s in d["sections"]:
        out += [(s["id"], it["text"]) for it in s["items"]]
    for r in d["roles"]:
        out += [(f"roles.{r['side']}", it["text"]) for it in r["does"]]
    out += [("next_steps", it["text"]) for it in d["next_steps"]]
    out += [("conditions", it["text"]) for it in d["conditions"]]
    return out


def register(ctx):
    state = {}

    def top_overlap():
        if "id" not in state:
            o = ctx.request("GET", "/api/gridlock/opportunities?limit=1")
            assert o["opportunities"], "no opportunity to draft"
            state["id"] = o["opportunities"][0]["id"]
        return state["id"]

    def shape():
        oid = top_overlap()
        r = ctx.request("GET", f"/api/agreement/{oid}?ai=false")
        state["doc"] = r
        assert r["overlap_id"] == oid and r["by"] == "template" and r["verified"] is True, (r["by"], r["verified"], r.get("rejected"))
        assert r["disclaimer"] == DISCLAIMER, r["disclaimer"]
        ov = r["overlap"]
        assert len(ov["projects"]) == 2 and {p["side"] for p in ov["projects"]} == {"a", "b"}, ov["projects"]
        assert ov["projects"][0]["utility"] != ov["projects"][1]["utility"], "a pair within one utility"
        for p in ov["projects"]:
            assert p["name"] and p["utility_name"] and p["source"]["kind"] == "filing" and p["source"]["title"], p
            assert isinstance(p["places"], list), p
        assert ov["tier"] in ("touching", "row", "site", "crews") and ov["distance_km"] >= 0, ov
        keys = [f["key"] for f in r["facts"]]
        assert len(keys) == len(set(keys)), "duplicate fact keys"
        for f in r["facts"]:
            assert f["text"] and f["source"]["label"] and f["source"]["kind"] in ("filing", "engine", "estimate", "proposal", "brief"), f
        d = r["draft"]
        assert d["title"].startswith("Draft coordination proposal"), d["title"]
        assert len(d["parties"]) == 2 and d["summary"]["text"], d["parties"]
        assert {s["id"] for s in d["sections"]} >= {"scope", "why"} and all(s["items"] for s in d["sections"]), d["sections"]
        assert {r_["side"] for r_ in d["roles"]} == {"a", "b", "both"} and all(r_["does"] for r_ in d["roles"]), d["roles"]
        cs = d["cost_split"]
        assert cs["proposal"] is True and cs["rule"] and cs["rationale"] and sum(s["pct"] for s in cs["shares"]) == 100, cs
        sv = d["savings"]
        assert sv["low"] is not None and sv["high"] is not None and 0 <= sv["low"] <= sv["high"], sv
        assert sv["items"] and all(it["source"] for it in sv["items"]), sv["items"]
        assert len(d["next_steps"]) >= 3 and d["conditions"], d["next_steps"]
        jw = d["joint_window"]
        if jw["overlap"]:
            assert jw["start"] <= jw["end"] and jw["months"] > 0, jw
        # the as-of rule: a joint window that ended before the draft's date is reported as past, never proposed
        assert jw["as_of"] and jw["status"] in ("past", "open", "future", None), jw
        if jw["status"] == "past":
            assert jw["end"] < jw["as_of"] and not jw["proposed"], jw
            assert "passed" in jw["text"] and "Proposed joint window" not in jw["text"], jw["text"]
        if jw["status"] in ("open", "future"):
            assert jw["end"] >= jw["as_of"] and jw["proposed"], jw
        assert any(f["key"] == "as_of" for f in r["facts"]), "no as_of fact"
        cited = {k for _, it in [(0, d["summary"])] + [(0, i) for s in d["sections"] for i in s["items"]] for k in it["facts"]}
        assert cited and cited <= set(keys), cited - set(keys)
        assert r["sources"] and any(s["kind"] == "filing" for s in r["sources"]), r["sources"]

    def every_number_traceable():
        r = state.get("doc") or ctx.request("GET", f"/api/agreement/{top_overlap()}?ai=false")
        nums = _fact_numbers(r["facts"])
        checked = 0
        for where, text in _texts(r["draft"]):
            for tok in NUM.findall(text):
                vals = _values(tok)
                checked += 1
                assert vals and any(_traceable(v, nums) for v in vals), f"{where}: {tok} is not in the facts: {text[:160]}"
        assert checked >= 10, f"only {checked} numbers in the draft"
        # the estimate's figures in the draft are the module's own (no re-derived numbers)
        e = ctx.request("GET", f"/api/gridlock/estimate/{r['overlap_id']}")
        assert r["draft"]["savings"]["low"] == e["total_low"] and r["draft"]["savings"]["high"] == e["total_high"], (r["draft"]["savings"], e["total_low"], e["total_high"])

    def unknown_is_404():
        ctx.request("GET", "/api/agreement/NOPE-1~NADA-2?ai=false", expect=404)
        ctx.request("GET", "/api/agreement/no-tilde?ai=false", expect=404)
        # two projects of one utility are not an overlap
        p = ctx.request("GET", "/api/gridlock/projects")["projects"]
        desc = [x["id"] for x in p if x["utility"] == "DESC" and x.get("geometry")][:2]
        if len(desc) == 2:
            ctx.request("GET", f"/api/agreement/{desc[0]}~{desc[1]}?ai=false", expect=404)

    def bad_input_is_422():
        oid = top_overlap()
        ctx.request("GET", f"/api/agreement/{oid}?lang=fr&ai=false", expect=422)
        ctx.request("GET", f"/api/agreement/{oid}?window_months=999&ai=false", expect=422)
        ctx.request("GET", f"/api/agreement/{oid}?window_months=abc&ai=false", expect=422)
        ctx.request("GET", f"/api/agreement/{'x' * 250}~y?ai=false", expect=422)

    def cached_second_time():
        oid = top_overlap()
        first = ctx.request("GET", f"/api/agreement/{oid}?ai=false&window_months=36")
        again = ctx.request("GET", f"/api/agreement/{oid}?ai=false&window_months=36")
        assert again["cached"] is True, again["cached"]
        assert again["draft"] == first["draft"] and again["facts"] == first["facts"], "the cached draft differs"

    def in_process(fn):
        def run():
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")  # llm.py imports auth.py; this process only
            try:
                import agreement
            except ImportError as e:  # a deployed run without the backend folder on the path
                raise AssertionError(f"agreement.py not importable here: {e}") from e
            fn(agreement)

        return run

    def checker_rejects_invented_numbers(ag):
        oid = state.get("id") or "DESC-6852~GA-21116"
        b = ag._base(oid, 24)
        facts, tpl = b["facts"], b["tpl"]
        # the checker itself
        ok, why, _ = ag.check_text("The two projects could save $250K by sharing a yard.", facts)
        assert not ok and "250" in why, why
        ok, why, _ = ag.check_text("DESC has agreed to host the crews.", facts)
        assert not ok and "agreed" in why, why
        ok, why, _ = ag.check_text("Call Mr. Smith at 803-555-0100 to start.", facts)
        assert not ok, why
        ok, why, _ = ag.check_text("Savings of a million dollars are possible.", facts)
        assert not ok and "follow a figure" in why, why
        ok, _, n = ag.check_text(tpl["summary"]["text"], facts)
        assert ok and n >= 3, (ok, n)

        good = {"text": tpl["why"][0]["text"], "facts": ["pair.distance", "made.up.key"]}

        def answer(**over):
            raw = {
                "summary": tpl["summary"],
                "scope": [good, good],
                "why": [good, good],
                "roles_a": [good],
                "roles_b": [good],
                "roles_both": [good],
                "cost_split": tpl["cost_split_text"],
                "next_steps": [good, good, good],
            }
            raw.update(over)
            return raw

        parts, rejected, reason = ag._clean_ai(answer(), facts, tpl, b["pa"], b["pb"])
        assert parts and not rejected and reason is None, (rejected, reason)
        assert parts["why"][0]["facts"] == ["pair.distance"], parts["why"][0]["facts"]  # unknown fact keys are dropped
        # one invented figure in a list: that item is stripped and reported
        bad = {"text": "The shared yard would cut 37 truck trips and save $412K.", "facts": ["save.total"]}
        parts, rejected, reason = ag._clean_ai(answer(scope=[good, bad, good]), facts, tpl, b["pa"], b["pb"])
        assert parts and len(parts["scope"]) == 2 and len(rejected) == 1 and rejected[0]["where"] == "scope[1]", rejected
        assert "not in the facts" in rejected[0]["reason"], rejected
        # an invented figure in the summary: the whole draft falls back to the template
        parts, rejected, reason = ag._clean_ai(answer(summary={"text": "They would save $9.9M by 2031.", "facts": []}), facts, tpl, b["pa"], b["pb"])
        assert parts is None and reason and "summary" in reason and rejected, (reason, rejected)
        # no answer at all (Gemini offline): falls back
        parts, _, reason = ag._clean_ai(ag._NONE, facts, tpl, b["pa"], b["pb"])
        assert parts is None and reason, reason
        # the assembled page passes the final gate
        doc = ag._respond(b, ag._tpl_parts(b), "template", [], "smoke", "en", 24, 0.0)
        assert doc["verified"] is True, doc["rejected"]

    def checker_reads_context(ag):
        # a figure only matches facts of its own kind: made-up money and percentages built from other numbers
        # (12 and 15 from 12.3 mi and 14.81 km, 60 from 61,000, 0-10 as counts) are rejected
        oid = "DESC-6852~GA-21116"  # the reviewer's case: savings $61K-$122K, split 50/50, 12.3 / 12.5 mi, 14.81 km
        b = ag._base(oid, 24)
        facts = b["facts"]
        for s in (
            "Sharing crews could save $10M.",
            "Sharing could save $2M.",
            "Sharing could save 5 million dollars.",
            "Sharing crews could save $12K to $15K.",
            "DESC would pay 60 % of shared costs.",
            "DESC would pay 60 percent of shared costs.",
            "Sharing could save 12 thousand dollars.",
        ):
            ok, why, _ = ag.check_text(s, facts)
            assert not ok and why, (s, why)
        # the real figures, in their own contexts, still pass
        est, pct = b["est"], b["split"]["pct"]
        for s in (
            f"Sharing could save {ag._usd_range(est['total_low'], est['total_high'])}.",
            f"DESC would pay {pct[0]} % of shared costs.",
            "The two projects come within 14.81 km (9.20 mi) of each other; the Georgia line is 12.3 mi.",
            "It is ranked #1 of 71 flagged pairs; two utilities, one window.",
        ):
            ok, why, _ = ag.check_text(s, facts)
            assert ok, (s, why)

    def as_of_rule(ag):
        # one pair whose filed windows share months, drafted on three dates: before, during and after them
        st = ag.gl._load()
        rows = ag.gl._compute(st, ag.gl._params(ag.gl.MAX_KM_DEFAULT, 24, "closest", "all", "all"))["overlaps"]
        oid = next(f"{r['a']}~{r['b']}" for r in rows if r.get("same_window"))
        b = ag._base(oid, 24)
        assert b["extra"]["joint"], f"{oid}: no joint window"
        s, e = b["extra"]["joint"]
        cases = {"future": s - timedelta(days=40), "open": s + (e - s) / 2, "past": e + timedelta(days=40)}
        for want, day in cases.items():
            bb = ag._base(oid, 24, as_of=day)
            doc = ag._respond(bb, ag._tpl_parts(bb), "template", [], "smoke", "en", 24, 0.0)
            jw = doc["draft"]["joint_window"]
            assert jw["status"] == want and doc["verified"] is True, (want, jw["status"], doc["rejected"])
            step = doc["draft"]["next_steps"][2]["text"]
            if want == "past":
                assert "passed" in jw["text"] and "Proposed" not in jw["text"] and not jw["proposed"], jw["text"]
                assert step.startswith("Check each project's current status"), step
                assert "passed" in doc["draft"]["summary"]["text"], doc["draft"]["summary"]["text"]
                # Gemini planning inside a passed window is rejected by the checker
                start = f"{ag.MONTHS[s.month - 1]} {s.year}"
                ok, why, _ = ag.check_text(f"Compare their build windows starting from {start}.", bb["facts"])
                assert not ok and "before this draft's date" in why, why
                ok, why, _ = ag.check_text(f"As filed, both windows were open from {start}; that period has passed.", bb["facts"])
                assert ok, why
            elif want == "open":
                assert "already open" in jw["text"] and jw["months_left"] >= 1 and jw["proposed"], jw
                assert "months left" in step, step
            else:
                assert jw["text"].startswith("Proposed joint window") and jw["proposed"], jw["text"]
                assert step.startswith("Compare detailed construction schedules"), step

    ctx.check("agreement: a draft for the top opportunity has the overlap, sourced facts, the draft's parts and the disclaimer", shape)
    ctx.check("agreement: every number in the template draft is one of the facts' numbers; savings are gridlock's own", every_number_traceable)
    ctx.check("agreement: an unknown overlap or a same-utility pair is a 404", unknown_is_404)
    ctx.check("agreement: bad lang, window or id is a 422", bad_input_is_422)
    ctx.check("agreement: the same draft again is served from the cache", cached_second_time)
    ctx.check("agreement: the checker rejects an invented number or wording (a stripped item, or the template for a bad summary)", in_process(checker_rejects_invented_numbers))
    ctx.check("agreement: money only matches dollar facts and a percentage only the proposed split ($10M, $2M, 5 million, $12K-$15K, 60 % rejected)", in_process(checker_reads_context))
    ctx.check("agreement: a joint window before the draft's date is reported as passed (not proposed), an open one gives the months left", in_process(as_of_rule))
