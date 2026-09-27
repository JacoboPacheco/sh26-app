"""Smoke checks for the collaboration plans (backend/collab_plans.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200).

The HTTP checks ask for the template plans ("ai": false), so a smoke run never spends Gemini quota; every figure is
recomputed here from the estimate and project routes (not from collab_plans.py's code). The agents run in-process: a
scripted stand-in for Gemini whose coordinator invents a figure, repeats a kind and names a kind that isn't on the menu
(each must be dropped and listed, the findings must reach the coordinator's one revision), and the labeled template with
GEMINI_API_KEY blank."""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

TOP = "DESC-6809T~GA-20793"  # the #1 (Sat 23:30): two 115 kV line rebuilds, 10.7 mi to the part Georgia Power works on
SHIFT = "DESC-6888~GA-20065"  # windows 7 months apart, both still ahead: a "shift" plan is on its menu
KINDS = {"one_outage", "shift", "stagger", "share_prep", "status_check"}
STEPS = {"goal", "settles", "proposes", "merges", "revises", "checks", "recommends", "fallback"}


def _ym(iso: str) -> tuple[int, int]:
    y, m = iso.split("-")[:2]
    return int(y), int(m)


def register(ctx):
    state = {}

    def plans(pair, **kw):
        return ctx.request("POST", "/api/gridlock/plans", data={"pair": pair, "ai": False, **kw})

    def shape():
        d = plans(TOP)
        state["top"] = d
        assert d["pair"] == TOP and d["fallback"] is True and d["by"] == "template", (d["by"], d["fallback"])
        assert d["fallback_label"] == "Template plan - AI unavailable" and d["fallback_reason"], d["fallback_label"]
        assert d["disclaimer"].startswith("Draft for discussion") and "not an agreement between, or endorsed by, either utility" in d["disclaimer"]
        cos = d["companies"]
        assert [c["utility"] for c in cos] == ["DESC", "GPC"], cos
        for c in cos:
            assert c["goal"] and c["project"]["id"] and c["project"]["page"] and c["project"]["source"], c
            assert "%" in c["goal"] and "as filed" in c["goal"], c["goal"]
        ps = d["plans"]
        assert 2 <= len(ps) <= 3 and len({p["kind"] for p in ps}) == len(ps), [p["kind"] for p in ps]
        for p in ps:
            assert p["kind"] in KINDS and p["id"] == p["kind"] and p["kind"] in d["menu"], p["kind"]
            assert p["title"] and len(p["title"].split()) <= 8 and p["summary"] and 1 <= len(p["steps"]) <= 4, p["title"]
            assert p["proposed_by"] in ("Coordinator", "DESC's agent", "Georgia Power's agent"), p["proposed_by"]
            assert len(p["incentives"]) == 2 and [i["utility"] for i in p["incentives"]] == ["DESC", "GPC"], p["incentives"]
            for inc in p["incentives"]:
                assert inc["net"] and isinstance(inc["gains"], list) and isinstance(inc["gives_up"], list), inc
                for g in inc["gains"] + inc["gives_up"]:
                    assert g["what"] and g.get("source"), (p["kind"], g)  # every kept figure has a source
                    if g.get("usd"):
                        lo, hi = g["usd"]
                        assert 0 < lo <= hi, g
            assert all(c["ok"] and c["source"] and c["reason"] for c in p["checks"]), p["checks"]
            assert isinstance(p["dropped"], list), p
        assert d["recommended"]["plan"] in {p["id"] for p in ps} and d["recommended"]["why"], d["recommended"]
        steps = {t["step"] for t in d["trace"]}
        assert steps <= STEPS and "goal" in steps and "settles" in steps and "checks" in steps and "recommends" in steps, steps
        # the split every plan uses is settled between the two goals, said once, and never called a trade by the agents
        cf = d["conflict"]
        assert cf["text"] and "have to trade" not in cf["text"] and len(cf["shares"]) == 2 and len(cf["caps"]) == 2, cf
        if any(cf["over"]):  # said plainly: whose rule, what the limits add up to, who pays how many points over
            assert "Neither filing states a cost split" in cf["text"] and "points above its" in cf["text"] and "add up to" in cf["text"], cf["text"]
        for p in ps:
            if p["split"]:
                assert p["split"]["shares"] == cf["shares"] and p["split"]["rule"] == cf["rule"], (p["split"], cf)
            for k, inc in enumerate(p["incentives"]):
                over = cf["over"][k]
                if p["savings"] and over:  # the split misses this company's goal: it gives that up, with its cost
                    assert any("smallest fair share" in g["what"] and g.get("usd") for g in inc["gives_up"]), (p["kind"], inc["gives_up"])
        assert not any("not a pair of months" in t["text"] or "YYYY-MM" in t["text"] for t in d["trace"]), "format noise in the trace"

    def figures_trace():
        """Every money figure is the estimate's item split by the plan's rule (a company pays its share of ONE shared cost
        instead of a whole one of its own, so it saves the rest: paying more means saving less); every window sits inside
        both filed windows."""
        d = state.get("top") or plans(TOP)
        est = ctx.request("GET", f"/api/gridlock/estimate/{TOP}")
        items = {it["id"]: it for it in est["items"]}
        pa, pb = (ctx.request("GET", f"/api/gridlock/projects/{x}")["project"] for x in TOP.split("~"))
        for p in d["plans"]:
            if p["savings"]:
                got = [items[i] for i in p["items"]]
                assert abs(sum(i["low"] for i in got) - p["savings"]["low"]) < 1 and abs(sum(i["high"] for i in got) - p["savings"]["high"]) < 1, p
                sa, sb = p["split"]["shares"]
                assert abs(sa + sb - 100) < 0.5, p["split"]
                for inc, share in zip(p["incentives"], (sa, sb)):
                    usd = [g["usd"] for g in inc["gains"] if g.get("usd")]
                    lo, hi = sum(u[0] for u in usd), sum(u[1] for u in usd)
                    keep = (100 - share) / 100
                    assert abs(lo - p["savings"]["low"] * keep) <= 1000 * len(usd) and abs(hi - p["savings"]["high"] * keep) <= 1000 * len(usd), (p["kind"], inc)
                # the two companies' savings add up to what building together saves
                tot = [sum(g["usd"][k] for inc in p["incentives"] for g in inc["gains"] if g.get("usd")) for k in (0, 1)]
                assert abs(tot[0] - p["savings"]["low"]) <= 2000 * len(p["items"]) and abs(tot[1] - p["savings"]["high"]) <= 2000 * len(p["items"]), (tot, p["savings"])
            else:
                assert p["split"] is None, (p["kind"], p["split"])  # nothing priced: nothing to split
            if p["window"] and p["kind"] != "shift":
                s, e = _ym(p["window"]["start"]), _ym(p["window"]["end"])
                for x in (pa, pb):
                    bw = x["build_window"]
                    assert _ym(bw["start"]) <= s and e <= _ym(bw["end"]), (p["kind"], p["window"], x["id"], bw)
                assert s >= _ym(d["as_of"]), (p["window"], d["as_of"])

    def shift_plan():
        """Windows apart and both still ahead: the shift plan moves one project by the gap (plus the months they then share),
        its window lies inside the moved window and the other filed window, and the moved side gives up exactly those months."""
        d = plans(SHIFT)
        s = next((p for p in d["plans"] if p["kind"] == "shift"), None)
        if s is None:
            assert "shift" not in d["menu"] or len(d["plans"]) == 3, d["menu"]
            return
        sh = s["shift"]
        assert sh["months"] >= 1 and sh["direction"] == "later" and sh["side"] in ("a", "b"), sh
        moved = next(inc for inc in s["incentives"] if inc["utility"] == sh["utility"])
        assert any(g.get("months") == sh["months"] and "later" in g["what"] for g in moved["gives_up"]), moved["gives_up"]
        w, mw = s["window"], sh["window"]
        assert _ym(mw["start"]) <= _ym(w["start"]) and _ym(w["end"]) <= _ym(mw["end"]), (w, mw)
        assert any(c["figure"] == f"{sh['months']} months later" and c["ok"] for c in s["checks"]), s["checks"]

    def spanish():
        """lang=es: the goals, the settled split, what each company gains and gives up and the recommendation are Spanish,
        the figures the same as in English."""
        en, es = state.get("top") or plans(TOP), plans(TOP, lang="es")
        assert es["lang"] == "es" and es["conflict"]["shares"] == en["conflict"]["shares"], (es["lang"], es["conflict"])
        assert "según lo publicado" in es["companies"][0]["goal"] and "objetivos" in es["conflict"]["text"].lower(), es["conflict"]["text"]
        for p in es["plans"]:
            for inc in p["incentives"]:
                for g in inc["gains"] + inc["gives_up"]:
                    assert not any(w in g["what"] for w in (" pays ", "Pays ", " its ", "Its ", "Shares ")), g["what"]
        assert [p["savings"] for p in es["plans"]] == [p["savings"] for p in en["plans"]], "the figures differ between languages"
        assert es["recommended"]["why"] and " the " not in es["recommended"]["why"], es["recommended"]

    def validation():
        ctx.request("POST", "/api/gridlock/plans", data={"pair": "nope", "ai": False}, expect=422)
        ctx.request("POST", "/api/gridlock/plans", data={"pair": TOP, "lang": "fr", "ai": False}, expect=422)
        ctx.request("POST", "/api/gridlock/plans", data={"pair": "DESC-0000~GA-0000", "ai": False}, expect=404)
        a = TOP.split("~")[0]
        ctx.request("POST", "/api/gridlock/plans", data={"pair": f"{a}~{a}", "ai": False}, expect=404)
        again = plans(TOP)
        assert again["cached"] is True, "the same template plans again should come from the cache"

    def agreement_from_plan():
        d = state.get("top") or plans(TOP)
        pid = d["recommended"]["plan"]
        doc = ctx.request("GET", f"/api/agreement/{TOP}?plan={pid}&ai=false")
        assert doc["plan"] and doc["plan"]["applied"] and doc["plan"]["plan"] == pid, doc["plan"]
        assert doc["verified"] and not doc["rejected"], doc["rejected"]
        dr = doc["draft"]
        assert "collaboration plan" in dr["negotiated"]["text"] and dr["negotiated"]["scope"] == next(p for p in d["plans"] if p["id"] == pid)["items"], dr["negotiated"]
        assert "not an agreement between, or endorsed by, either utility" in doc["disclaimer"], doc["disclaimer"]
        post = ctx.request("POST", f"/api/agreement/{TOP}", data={"plan": {"id": pid, "title": "ignored", "savings": {"low": 1, "high": 9e9}}, "ai": False})
        assert post["plan"]["plan"] == pid and post["draft"]["savings"]["high"] == doc["draft"]["savings"]["high"], "a posted plan's own figures were used"
        ctx.request("GET", f"/api/agreement/{TOP}?plan=helicopters&ai=false", expect=404)
        ctx.request("GET", f"/api/agreement/{TOP}?plan={pid}&negotiated=plain", expect=422)

    # ------------------------------------------------------------------ in-process

    def in_process(fn, key=None):
        def run():
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")  # llm.py imports auth.py; this process only
            try:
                import collab_plans
            except ImportError as e:  # a deployed run without the backend folder on the path
                raise AssertionError(f"collab_plans.py not importable here: {e}") from e
            saved = os.environ.get("GEMINI_API_KEY")
            os.environ["GEMINI_API_KEY"] = key if key is not None else ""
            try:
                fn(collab_plans)
            finally:
                if saved is None:
                    os.environ.pop("GEMINI_API_KEY", None)
                else:
                    os.environ["GEMINI_API_KEY"] = saved

        return run

    def fallback_without_a_key(cp):
        out = asyncio.run(cp.run(TOP, 24, "en", True, as_of=date(2026, 1, 15)))  # a date no live run has: never a cache hit
        assert out["fallback"] and out["fallback_label"] == "Template plan - AI unavailable" and out["fallback_reason"] == "Gemini not configured", (
            out["fallback"], out["fallback_reason"])
        assert 2 <= len(out["plans"]) <= 3 and any(t["step"] == "fallback" for t in out["trace"]), out["trace"]
        again = asyncio.run(cp.run(TOP, 24, "en", True, as_of=date(2026, 1, 15)))
        assert again["cached"] is False, "a transient Gemini miss must not be cached"

    def half_translated(cp):
        """A Spanish line with "gives up" left half-translated ("pero daup paga", "pero da up que su inicio espera") is
        dropped like any line the checks refuse, and the same words in an English line stay allowed."""
        case = cp._case(TOP, 24)
        facts, allowed = cp._plan_facts(case, cp._menu(case))
        for bad in ("DESC gana cuadrillas compartidas, pero daup paga la parte mayor de los costos",
                    "Georgia Power gana cuadrillas compartidas, pero da up que su inicio espera a DESC"):
            ok, why = cp._text_ok(bad, facts, allowed)
            assert not ok and "half-translated" in why, (bad, ok, why)
        ok, why = cp._text_ok("DESC gana cuadrillas compartidas, pero cede su inicio hasta que termine la otra empresa", facts, allowed)
        assert ok, why
        ok, why = cp._text_ok("DESC gains shared crews and gives up starting first on its filed schedule", facts, allowed)
        assert ok, why

    def agents_checked(cp):
        """A stand-in for Gemini: both company agents propose; the coordinator's first answer invents a dollar figure,
        repeats a kind and names a kind that isn't on the menu. Each is dropped and listed; the findings reach the
        coordinator's one revision; the figures stay the pipeline's."""
        import llm

        prompts = []

        async def fake(prompt, system=None, fallback=None, timeout=None, schema=None, cache=True, surface=None, model=None, image=None, thinking=None):
            prompts.append((system, prompt))
            assert surface == "collab_plans" and schema, surface
            if system == cp.AGENT_SYSTEM:
                assert "YOUR GOAL (a fair-share figure" in prompt and "MENU" in prompt
                return {"proposals": [{"kind": "stagger", "why": "The filing shows both build windows share months, so one crew set-up serves both."},
                                      {"kind": "helicopters", "why": "Fly the crews in."}]}, False
            revise = "THE PIPELINE DROPPED" in prompt
            plan = {"kind": "stagger", "title": "Keep the dates, share the crews", "summary": "Both keep their filed dates and share one crew set-up.",
                    "steps": ["Line up the two jobs."], "proposed_by": "a", "net_a": "DESC gains its share and gives up nothing.",
                    "net_b": "Georgia Power gains its share and waits on the first crew."}
            other = {**plan, "kind": "share_prep", "title": "Share the survey data", "summary": "They share survey data and schedules.",
                     "proposed_by": "coordinator"}
            if revise:
                return {"plans": [plan, other], "recommended": {"kind": "stagger", "why": "It saves the most without moving a filed date."}}, False
            bad = {**plan, "summary": "Sharing would save $412K for both."}
            return {"plans": [bad, dict(bad), {**plan, "kind": "teleport"}, other],
                    "recommended": {"kind": "stagger", "why": "It saves the most."}}, False

        saved = llm.complete_json
        llm.complete_json = fake
        try:
            out = asyncio.run(cp.run(TOP, 24, "en", True, as_of=date(2026, 9, 26)))
        finally:
            llm.complete_json = saved
        assert out["by"] == "gemini" and not out["fallback"], (out["by"], out["fallback_reason"])
        kinds = [p["kind"] for p in out["plans"]]
        assert len(kinds) == len(set(kinds)) and 2 <= len(kinds) <= 3 and "teleport" not in kinds, kinds
        claims = " | ".join(x["claim"] + " -> " + x["reason"] for x in out["dropped"])
        assert "412" in claims and "helicopters" in claims, claims  # the invented figure and the off-menu kind, listed
        assert any(t["step"] == "revises" and t["verdict"] == "revised" for t in out["trace"]), [t["step"] for t in out["trace"]]
        assert any("THE PIPELINE DROPPED" in p for s, p in prompts if s == cp.COORD_SYSTEM), "the findings never reached the coordinator"
        st = next(p for p in out["plans"] if p["kind"] == "stagger")
        assert "412" not in st["summary"], st["summary"]
        # the agents never set a figure: the stagger plan's money is the template's
        tpl = asyncio.run(cp.run(TOP, 24, "en", False, as_of=date(2026, 9, 26)))
        t_st = next(p for p in tpl["plans"] if p["kind"] == "stagger")
        assert st["savings"] == t_st["savings"] and [i["gains"] for i in st["incentives"]] == [i["gains"] for i in t_st["incentives"]], "the AI changed a figure"

    def hear_the_agents():
        """Hear the negotiation: the trace's kept agent lines carry voice keys (company A presenter, B analyst, the coordinator
        presenter), the spoken text is the line word for word (a plan kind's id said in its plain words), the pipeline's own
        lines and anything dropped are not read aloud, and each key is registered with voice.py (200 or 503, never 409)."""
        import re as _re
        say = {"one_outage": "One shared outage", "shift": "Move one schedule", "share_prep": "Share the prep work",
               "stagger": "Hand over between jobs", "status_check": "Compare notes"}
        for lang in ("en", "es"):
            d = plans(TOP, lang=lang)
            assert d["voice"]["attribution"] == "Voice: ElevenLabs" and d["voice"]["roles"] == {"a": "presenter", "b": "analyst", "coord": "presenter"}, d["voice"]
            spoken = []
            for s_ in d["trace"]:
                v = s_["voice"]
                if s_["agent"] in ("Pipeline", "Sistema") or s_["step"] in ("settles", "checks", "fallback") or s_["verdict"] == "dropped":
                    assert v is None, ("not read aloud", s_)
                    continue
                if s_["step"] == "proposes" and s_["verdict"] != "kept":
                    assert v is None, s_
                    continue
                assert v and _re.fullmatch(r"[0-9a-f]{32}", v["key"]) and v["lang"] == lang and v["who"], s_
                assert v["side"] in ("a", "b", "coord") and v["role"] == ("analyst" if v["side"] == "b" else "presenter"), v
                if lang == "en":
                    shown = _re.sub(r"^([a-z_]+):\s*", lambda m: f"{say[m.group(1)]}: " if m.group(1) in say else m.group(0), s_["text"].strip())
                    assert v["text"] == shown, (v["text"], shown)
                assert _re.findall(r"\d+", v["text"]) == _re.findall(r"\d+", s_["text"]), (v["text"], s_["text"])
                spoken.append(v)
            sides = [v["side"] for v in spoken]
            assert {"a", "b", "coord"} <= set(sides), sides  # both agents and the coordinator can be heard
            if not ctx.request("GET", "/api/voice/status")["configured"]:  # a server with a key would render (spend credits) here
                for v in (spoken[0], spoken[-1]):
                    try:
                        ctx.request("POST", "/api/voice/segment", {"key": v["key"]})
                    except AssertionError as e:
                        assert "503" in str(e) and "Voice not configured" in str(e), f"a registered key must be 200 or 503: {e}"

    ctx.check("collab plans: 2-3 distinct plan kinds for the top pair, each company's goal from its filing, what each gains and "
              "gives up with a source for every kept figure, a recommended plan, no format noise in the trace", shape)
    ctx.check("collab plans: every money figure is the estimate's items split by the plan's rule; every window sits inside both "
              "filed windows and after today", figures_trace)
    ctx.check("collab plans: a shift plan moves one project by the months between the filed windows, and that company gives "
              "exactly those months up", shift_plan)
    ctx.check("collab plans: in Spanish the goals, the split, the gains and gives-ups and the recommendation are Spanish, "
              "with the same figures", spanish)
    ctx.check("collab plans: bad pair or lang 422, unknown or same-utility pair 404, the same plans again from the cache", validation)
    ctx.check("collab plans: the drafted agreement takes a chosen plan (GET ?plan= or POST {plan}), re-derives its figures, keeps "
              "the disclaimer; an unknown plan 404, plan + negotiated 422", agreement_from_plan)
    ctx.check("collab plans: with GEMINI_API_KEY blank the labeled template runs (and is not cached)", in_process(fallback_without_a_key))
    ctx.check("collab plans: an invented figure, a repeated kind and an off-menu kind are dropped and listed, the coordinator "
              "revises once with the findings, and the figures stay the pipeline's", in_process(agents_checked, key="smoke-fake-key-never-sent"))
    ctx.check("collab plans: a Spanish line with 'gives up' left half-translated is refused; the same words in English stay",
              in_process(half_translated))
    ctx.check("collab plans: Hear the negotiation: kept agent lines carry voice keys (A presenter, B analyst, coordinator presenter), spoken word "
              "for word, the pipeline's lines and dropped ones are not read, keys registered with voice.py (200 or 503, never 409)", hear_the_agents)
