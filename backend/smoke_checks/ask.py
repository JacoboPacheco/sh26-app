"""Smoke checks for Ask Overload (backend/ask.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200), auth(), expected.
These endpoints store nothing. Most checks send ai=false (the fact-sheet answer) so a run against a
live Gemini key spends no quota; one check lets Gemini answer if it is configured and accepts either
source, since both must state engine numbers."""

import re

FORBIDDEN = re.compile(r"(?i)evacuat|shelter in place|this is not a test|emergency alert|\bFEMA\b|hurricane [A-Z][a-z]+|\b(19|20)\d\d\b(?!\s*(MW|people|personas))")


def _numbers(text: str) -> list[float]:
    """Every figure in a sentence, with 'million'/'thousand' applied (so 0.78 million = 780,000)."""
    out = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(million|millones|thousand|mil\b)?", text):
        v = float(m.group(1).replace(",", ""))
        scale = {"million": 1e6, "millones": 1e6, "thousand": 1e3, "mil": 1e3}.get((m.group(2) or "").lower(), 1)
        out.append(v * scale)
    return out


def register(ctx):
    hero = ctx.expected["hero"]
    case = {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["mw"]}

    def ask(question, lang="en", ai=False, **extra):
        return ctx.request("POST", "/api/ask", {"case": {**case, **extra}, "question": question, "lang": lang, "ai": ai})

    def suggestions_for_hero():
        r = ctx.request("POST", "/api/ask/suggestions", {"case": case})
        qs = r["questions"]
        assert len(qs) == 4 and all(isinstance(q, str) and len(q) > 5 for q in qs), qs
        es = ctx.request("POST", "/api/ask/suggestions", {"case": case, "lang": "es"})["questions"]
        assert len(es) == 4 and all(q.startswith("¿") for q in es), es

    def people_answer_is_the_engines_number():
        r = ask("How many people lost power?")
        keys = {c["key"] for c in r["cited"]}
        assert not r["declined"] and "event.people_out" in keys, r
        fact = next(c for c in r["cited"] if c["key"] == "event.people_out")
        people = float(re.search(r"\d[\d,]*", fact["text"]).group(0).replace(",", ""))
        assert people > 0, fact
        assert any(abs(v - people) <= 0.03 * people for v in _numbers(r["answer"])), f"{people:,.0f} not in: {r['answer']}"
        assert r["numbers_checked"] >= 1 and r["report_key"], r
        assert not FORBIDDEN.search(r["answer"]), r["answer"]

    def half_size_runs_the_engine():
        # ai left on: Gemini answers if it's configured, else the fact sheet; the engine run is the same
        r = ask("What if it were half the size?", ai=True)
        assert r["tool"] and r["tool"]["name"] == "whatif_size", r
        facts = {f["key"]: f for f in r["tool_facts"] or []}
        steps = next((f["value"] for k, f in facts.items() if k.endswith(".steps")), None)
        assert steps is not None, facts
        assert str(steps) in r["answer"], f"the answer should state {steps} steps: {r['answer']}"
        # the tool's number is the engine's: the cascade endpoint at the same size agrees
        mw = next((f["value"] for k, f in facts.items() if k.endswith(".mw")), hero["mw"] / 2)
        c = ctx.request("POST", "/api/grid/cascade", {**case, "mw": mw})
        assert c["total_steps"] == steps, f"what-if {steps} steps != cascade {c['total_steps']} at {mw} MW"
        assert r["source"] in ("gemini", "pattern") and isinstance(r["fallback"], bool)
        assert not FORBIDDEN.search(r["answer"]), r["answer"]

    def off_topic_is_declined():
        r = ask("What is the capital of France?", ai=True)
        assert r["declined"] is True and r["tool"] is None and r["cited"] == [], r

    def spanish_answer():
        r = ask("¿Cuántas personas se quedaron sin luz?", lang="es")
        assert r["lang"] == "es" and not r["declined"] and "personas" in r["answer"], r

    def why_names_the_first_line():
        r = ask("Why did it happen?")
        keys = {c["key"] for c in r["cited"]}
        assert "cause.line" in keys, r
        line = next(c for c in r["cited"] if c["key"] == "cause.line")["text"]
        first_word = re.sub(r"^the ", "", line.split(":")[-1].strip()).split(" ")[0]
        assert first_word in r["answer"], (line, r["answer"])

    def real_world_names_get_the_fixed_answer():
        r = ask("Tell me about Hurricane Ian and FPL", ai=True)
        assert r["declined"] is True and r["source"] == "pattern" and r["tool"] is None, r
        assert "synthetic" in r["answer"] and not FORBIDDEN.search(r["answer"]), r["answer"]

    def no_fix_states_the_bound():
        # a statewide catastrophe: the answer states the engine's no-fix bound (or the engine's 503
        # while its patch is missing; never a 500)
        body = {"case": {**case, "preset": "fl-cat5-statewide"}, "question": "Can this be fixed?", "ai": False}
        try:
            r = ctx.request("POST", "/api/ask", body)
        except AssertionError as e:
            assert "503" in str(e), e
            return
        bound = next((c for c in r["cited"] if c["key"] == "bound.people"), None)
        assert bound and "No fix exists" in r["answer"], r
        people = float(re.search(r"\d[\d,]*", bound["text"]).group(0).replace(",", ""))
        assert any(abs(v - people) <= 0.03 * people for v in _numbers(r["answer"])), (people, r["answer"])

    def ask_rejects_bad_input():
        ctx.request("POST", "/api/ask", {"case": case, "question": "hi"}, expect=422)
        ctx.request("POST", "/api/ask", {"case": case, "question": "x" * 301}, expect=422)
        ctx.request("POST", "/api/ask", {"case": case, "question": "How many people?", "lang": "fr"}, expect=422)
        ctx.request("POST", "/api/ask", {"question": "How many people?"}, expect=422)
        ctx.request("POST", "/api/ask", {"case": {**case, "mw": 0}, "question": "How many people?"}, expect=422)
        ctx.request("POST", "/api/ask", {"case": {**case, "region": "ZZ"}, "question": "How many people?"}, expect=422)

    ctx.check("ask: four suggestions for the hero, EN and ES (no AI)", suggestions_for_hero)
    ctx.check("ask: 'how many people' states the engine's number and cites it", people_answer_is_the_engines_number)
    ctx.check("ask: 'half the size' runs the engine and matches /api/grid/cascade", half_size_runs_the_engine)
    ctx.check("ask: off-topic questions are declined", off_topic_is_declined)
    ctx.check("ask: answers in Spanish", spanish_answer)
    ctx.check("ask: 'why' names the first line over its limit", why_names_the_first_line)
    ctx.check("ask: a real storm or utility named gets the fixed answer, no AI", real_world_names_get_the_fixed_answer)
    ctx.check("ask: a no-fix catastrophe states the engine's bound", no_fix_states_the_bound)
    ctx.check("ask: bad input is a 422", ask_rejects_bad_input)
