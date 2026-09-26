"""Smoke checks for the narrated play-by-play on Strengthen the grid (backend/narrate.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200), auth(), expected.

Read-only: the narration reads a FINISHED unlock study and never starts one (LAZY), so this module starts the
Florida 1,000 MW study itself (like smoke_checks/unlock.py; the server warms it or loads it baked, so this usually
joins that job or finds it cached) and polls it. Then, for the page's default budget and its largest: the script is an
intro, one slide per PACKAGE and a closing (at most 6 slides, about a minute spoken); the packages are contiguous runs
of the paid campus steps the budget buys, cover every one exactly once, and carry the engine's own counts and running
costs; the map's steps only go up and each slide ends on its own picture; every digit in every spoken line is one of
the script's facts (names that hold digits masked); the intro frames what stops the next campus as a weak point in
today's grid and the closing names the synthetic model; the voice keys are registered (a segment answers 200 or 503
'not configured', never 409 'unknown'); Spanish works; a size with no finished study answers 409 (and still starts
nothing); bad bodies 422; without Gemini (the check server blanks GEMINI_API_KEY, or ai=false) the engine groups and
the templates speak, labeled; with Gemini its grouping passes every check or the engine's runs, labeled. At most one
short segment is rendered."""

import math
import re
import time

MW = 1000
DONE_S = 150
POLL_S = 1.5
URL = "/api/strengthen/narration"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
SLIDES_MAX = 6
SPOKEN_MAX_S = 80.0  # "about a minute" whatever the budget (the backend aims at <= 76 s)
CHECK_IDS = {"contiguous", "cover", "count", "places", "claims", "numbers", "names", "text"}


def page_round(x: float) -> int:
    """The page's whole-number rounding (geo.js fmt = Math.round): a half goes up, 234.5 -> 235."""
    return int(math.floor(float(x) + 0.5))


def forms(v: float) -> set[str]:
    a = abs(float(v))
    dec = lambda x, d: (f"{x:.{d}f}".rstrip("0").rstrip(".") if d else f"{x:.0f}")  # noqa: E731
    out = {str(int(round(a))), str(page_round(a)), str(int(a)), dec(a, 1), f"{a:.0f}"}
    for k in range(1, 7):
        r = round(a, -k)
        if r:
            out.add(str(int(r)))
    for s in (1e3, 1e6, 1e9):
        for d in (0, 1, 2):
            x = dec(a / s, d)
            if x not in ("0", ""):
                out.add(x)
    for s in (1.0, 1e3, 1e6, 1e9):  # three significant figures, as the deck and the panel print money ("1080 millones")
        x = a / s
        if x >= 1:
            k = 2 - int(math.floor(math.log10(x)))
            out.add(dec(round(x, k), max(k, 0)))
    return out


def digits_in_facts(script: dict, lang: str) -> int:
    """Every digit in every spoken line is one of the facts; returns how many numbers were checked."""
    allowed: set[str] = set()
    for f in script["facts"]:
        v = f["value"]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            allowed |= forms(v)
    names = sorted(script["names"], key=len, reverse=True)
    n = 0
    for s in script["slides"]:
        for seg in s["narration"][lang]:
            text = seg["text"]
            for nm in names:
                text = re.sub(re.escape(nm), "Placename", text, flags=re.IGNORECASE)
            text = re.sub(r"\b(circuit|unit|circuito|unidad) \d+\b", "twin", text, flags=re.IGNORECASE)
            for t in NUM.findall(text):
                t = t.replace(",", "").rstrip(".")
                norm = f"{float(t):.2f}".rstrip("0").rstrip(".") if "." in t else t
                assert t in allowed or norm in allowed, f"{s['id']}/{lang}: {t} is not a fact ({seg['text'][:120]})"
                n += 1
    return n


def within(steps: list[dict], budget: float) -> int:
    n = 0
    for st in steps:
        if st["cum_cost"]["high"] > budget + 0.5:
            break
        n = st["n"]
    return n


def pkg_range(u: int) -> tuple[int, int]:
    return (0, 0) if u <= 0 else (1, 1) if u <= 2 else (2, 3) if u <= 5 else (3, 4)


def structure(sc: dict, steps: list[dict], today: int, budget: float, lang: str = "en") -> None:
    """The script's shape for one budget, whoever grouped and wrote it: intro, one slide per package, closing; the
    packages are contiguous runs of the paid steps the budget buys, each exactly once, with the engine's own numbers;
    the map's steps only go up; about a minute."""
    n = max(within(steps, budget), today)
    assert sc["synthetic"] is True and "Synthetic grid model" in sc["note"], sc["note"]
    assert sc["today"] == today and sc["bought"]["n"] == n, (sc["today"], sc["bought"], today, n)
    if n:
        assert abs(sc["bought"]["cost_high"] - steps[n - 1]["cum_cost"]["high"]) <= 1, sc["bought"]
    paid = [st["n"] for st in steps[today:n] if not st["free"]]
    pk = sc["packages"]
    lo, hi = pkg_range(len(paid))
    assert lo <= len(pk) <= hi, (len(pk), len(paid), lo, hi)
    sl = sc["slides"]
    assert len(sl) <= SLIDES_MAX, [s["id"] for s in sl]
    assert [s["kind"] for s in sl] == ["intro"] + ["package"] * len(pk) + ["close"], [s["id"] for s in sl]
    # every paid step exactly once, contiguous, in the engine's order (free campuses ride along)
    assert sorted(x for p in pk for x in p["paid"]) == paid, ([p["paid"] for p in pk], paid)
    assert [x for p in pk for x in p["steps"]] == list(range(today + 1, n + 1)), [p["steps"] for p in pk]
    prev = today
    for i, p in enumerate(pk, 1):
        assert p["i"] == i and p["first"] == prev + 1 and p["steps"][0] == p["first"] and p["steps"][-1] == p["last"], p
        cum = steps[p["last"] - 1]["cum_cost"]["high"]
        before = steps[p["first"] - 2]["cum_cost"]["high"] if p["first"] >= 2 else 0
        assert abs(p["cum_high"] - cum) <= 1 and abs(p["cost_high"] - (cum - before)) <= 2, (p["id"], p["cum_high"], cum, p["cost_high"], before)
        assert p["name"]["en"] and p["name"]["es"] and p["why"]["en"] and p["why"]["es"], p
        assert not re.search(r"\d", p["name"]["en"] + p["name"]["es"]), p["name"]
        assert p["upgrades"] >= 1 and p["upgrades"] == p["lines"] + p["transformers"], p
        # the name's kind is what the package raises ("Jacksonville lines" raises a line), in both languages
        for nm in p["name"].values():
            assert p["lines"] or not re.search(r"\b(lines?|l[ií]neas?)\b", nm, re.I), (nm, p["lines"], p["transformers"])
            assert p["transformers"] or not re.search(r"\b(transformers?|transformador(es)?)\b", nm, re.I), (nm, p["lines"], p["transformers"])
        assert not any(k.startswith("_") for k in p), sorted(p)
        prev = p["last"]
    assert len({p["name"]["en"].lower() for p in pk}) == len(pk), "two packages share a name"
    # the map follows the voice: campuses shown only go up; the intro builds today's; each package ends on its last
    assert sl[0]["step_from"] == 0 and sl[0]["step"] == today and sl[-1]["step"] == n, (sl[0], sl[-1]["step"])
    last = 0
    for s in sl:
        assert s["step_from"] >= last and s["step"] >= s["step_from"], (s["id"], s["step_from"], s["step"], last)
        last = s["step"]
        if s["kind"] == "package":
            p = pk[s["package"] - 1]
            assert s["id"] == p["id"] and s["step"] == p["last"] and s["steps"] == p["steps"], (s, p)
        segs = s["narration"][lang]
        assert segs and s["est_s"][lang] > 0, s["id"]
        for seg in segs:
            assert HEX32.match(seg["key"]) and seg["role"] == "presenter" and seg["chars"] == len(seg["text"]) >= 30, (s["id"], seg.get("key"))
            chars = [c["char"] for c in seg["cues"]]
            assert chars == sorted(chars) and all(0 <= c <= seg["chars"] for c in chars), (s["id"], seg["cues"])
            vals = [c["value"] for c in seg["cues"] if c["name"] == "step"]
            assert vals and vals == sorted(vals) and vals[-1] == s["step"], (s["id"], vals)
            assert all(s["step_from"] <= v <= s["step"] for v in vals) or s["kind"] == "intro", (s["id"], vals)
    spoken = sc["est_s"][lang]
    assert 20 <= spoken <= SPOKEN_MAX_S * (1.12 if lang == "es" else 1.0), f"{spoken} s spoken"
    # the weak points: what stops the next campus first, each with its loading on today's grid when the model has it
    for x in sc["problems"]:
        assert x["base_pct"] is None or 0 <= x["base_pct"] <= 200, x
        assert x["fixed_in"] is None or 1 <= x["fixed_in"] <= len(pk), x


def register(ctx):
    state = {}

    def body(**kw):
        return {"region": "FL", "mw": MW, "load_factor": 1.0, "mode": "firm", "budget": state.get("budget", 0), "lang": "en", "ai": False, **kw}

    def study():
        start = ctx.request("POST", "/api/unlock/start", {"region": "FL", "mw": MW, "load_factor": 1.0})
        t0 = time.monotonic()
        while True:
            s = ctx.request("GET", f"/api/unlock/jobs/{start['id']}")
            if s["status"] == "done":
                break
            assert s["status"] in ("queued", "running"), s.get("error") or s["status"]
            assert time.monotonic() - t0 < DONE_S, f"the study is not done after {DONE_S} s"
            time.sleep(POLL_S)
        cap = s["result"]["capacity"]
        assert cap and cap["firm"]["steps"], "the Florida study has no campuses-at-once plan"
        steps = cap["firm"]["steps"]
        paid = [st for st in steps if not st["free"]]
        assert paid, "no paid step to narrate"
        # the budget: the largest paid step's running total at or under $50 million (the page's default), else the first
        fits = [st["cum_cost"]["high"] for st in paid if st["cum_cost"]["high"] <= 50e6]
        state["budget"] = fits[-1] if fits else paid[0]["cum_cost"]["high"]
        state["max"] = paid[-1]["cum_cost"]["high"]  # the slider's last stop
        state["steps"] = steps
        state["today"] = cap["firm"]["today"]
        state["first_block"] = cap["firm"].get("first_block")
        state["flex"] = cap["flexible"]

    def validation():
        for bad, what in (
            ({"mode": "both"}, "an unknown mode"),
            ({"lang": "fr"}, "an unknown language"),
            ({"budget": -1}, "a negative budget"),
            ({"budget": "lots"}, "a budget that is not a number"),
            ({"region": "US"}, "the national map"),
            ({"region": "ZZ"}, "an unknown state"),
            ({"mw": 50}, "a size below the range"),
            ({"load_factor": 3}, "a load level out of range"),
            ({"region": "FLORIDA-STATE"}, "a region code that is too long"),
        ):
            r = ctx.request("POST", URL, body(**bad), expect=422)
            assert r.get("detail"), f"no readable reason for {what}"

    def not_finished():
        # a size nobody has studied: 409 with a readable reason, and the narration starts nothing (LAZY)
        for region, mw in (("VT", 4950), ("RI", 4900), ("DE", 4850), ("NH", 4800)):
            q = f"/api/unlock/peek?region={region}&mw={mw}&load_factor=1"
            if ctx.request("GET", q)["state"] != "none":
                continue
            r = ctx.request("POST", URL, {"region": region, "mw": mw, "load_factor": 1.0, "budget": 1e8, "ai": False}, expect=409)
            assert "study" in r["detail"].lower(), r["detail"]
            time.sleep(0.5)
            assert ctx.request("GET", q)["state"] == "none", "asking for a narration started a study"
            return
        raise AssertionError("every probe size already has a study: pick others")

    def script():
        steps, today, budget = state["steps"], state["today"], state["budget"]
        t0 = time.monotonic()
        sc = ctx.request("POST", URL, body())
        structure(sc, steps, today, budget)
        sl = sc["slides"]
        # without AI: the engine grouped (labeled, every check passed) and the templates speak
        g = sc["grouping"]
        assert g["by"] == "engine" and g["reason"] == "AI off for this request" and g["verified"] is True, g
        assert {c["id"] for c in g["checks"]} == CHECK_IDS and all(c["ok"] for c in g["checks"]), g["checks"]
        assert sc["ai"]["by"] == "template" and sc["ai"]["fallback"] is True and sc["ai"]["requested"] is False, sc["ai"]
        assert all(s["written_by"]["en"] == "template" for s in sl), "a line claims Gemini with AI off"
        # framing: the intro names what stops the next campus as a weak point in today's grid; the closing names the model
        intro = sl[0]["narration"]["en"][0]["text"]
        fb = state["first_block"]
        if fb and fb.get("at_campus") == today + 1:
            assert fb["where"].lower() in intro.lower() and "isn't the campus" in intro, intro
            assert sc["problems"] and sc["problems"][0]["branch_id"] == fb["branch_id"], sc["problems"][:1]
        close = sl[-1]["narration"]["en"][0]["text"]
        assert "synthetic grid model" in close, close
        for s in sl:
            if s["kind"] == "package":
                p = sc["packages"][s["package"] - 1]
                text = s["narration"]["en"][0]["text"]
                assert p["name"]["en"] in text and s["headline"]["en"].startswith(p["name"]["en"]), (p["name"], text)
        assert digits_in_facts(sc, "en") >= len(sc["packages"]) + 1, "too few numbers checked"
        state["script"] = sc
        again = ctx.request("POST", URL, body())
        assert again["slides"] == sc["slides"], "the same request gave another script"
        assert time.monotonic() - t0 < 30, "the template script should come back at once"

    def largest():
        # the slider's last stop: still at most four packages and about a minute
        sc = ctx.request("POST", URL, body(budget=state["max"]))
        structure(sc, state["steps"], state["today"], state["max"])
        digits_in_facts(sc, "en")
        state["max_script"] = sc

    def spanish():
        sc = ctx.request("POST", URL, body(lang="es"))
        assert sc["lang"] == "es" and [s["id"] for s in sc["slides"]] == [s["id"] for s in state["script"]["slides"]], "ES has other slides"
        structure(sc, state["steps"], state["today"], state["budget"], "es")
        for s in sc["slides"]:
            text = s["narration"]["es"][0]["text"]
            assert re.search(r"\b(el|la|los|las|en|de)\b", text) and "en" not in s["narration"], (s["id"], text[:80])
        assert "modelo sintético" in sc["slides"][-1]["narration"]["es"][0]["text"], sc["slides"][-1]["narration"]["es"][0]["text"]
        digits_in_facts(sc, "es")
        flex = ctx.request("POST", URL, body(mode="flexible", budget=0))
        assert flex["mode"] == "flexible" and [s["kind"] for s in flex["slides"]] == ["intro", "close"], [s["id"] for s in flex["slides"]]
        assert flex["packages"] == [] and flex["grouping"]["packages"] == 0, flex["grouping"]
        assert "flexible" in flex["slides"][0]["narration"]["en"][0]["text"], flex["slides"][0]["narration"]["en"][0]["text"]
        digits_in_facts(flex, "en")

    def voice_keys():
        segs = [seg for s in state["script"]["slides"] for seg in s["narration"]["en"]]
        shortest = min(segs, key=lambda x: x["chars"])
        st = ctx.request("GET", "/api/voice/status")
        if not st["configured"]:
            try:
                r = ctx.request("POST", "/api/voice/segment", {"key": shortest["key"]}, expect=503)
                assert r["detail"] == "Voice not configured", r
            except AssertionError as e:
                # a line rendered earlier with a key is served from the disk cache without one: registered, fine
                if ": 200 != 503" not in str(e):
                    raise
            return
        a = ctx.request("POST", "/api/voice/segment", {"key": shortest["key"]})  # one short line (~100 characters)
        assert a["audio_url"].endswith(".mp3") and a["duration_s"] > 0 and a["words"], a

    def gemini_or_template():
        ai = ctx.request("GET", "/api/ai/status")
        for budget in (state["budget"], state["max"]):
            sc = ctx.request("POST", URL, body(ai=True, budget=budget))
            a, g = sc["ai"], sc["grouping"]
            assert a["surface"] == "strengthen_narration" and a["requested"] is True, a
            assert any(s["id"] == "strengthen_narration" for s in ai["surfaces"]), "the AI panel does not list the narration"
            if not ai["configured"]:
                assert a["by"] == "template" and a["fallback"] is True and a["reason"] == "Gemini not configured", a
                assert g["by"] == "engine" and g["reason"] == "Gemini not configured", g
                assert all(s["written_by"]["en"] == "template" for s in sc["slides"]), "a line claims Gemini without a key"
            else:
                assert a["by"] in ("gemini", "mixed", "template"), a
                assert g["by"] in ("gemini", "engine"), g
                if g["by"] == "gemini":  # Gemini's grouping is used only when every check passed
                    assert g["verified"] is True and g["reason"] is None and all(c["ok"] for c in g["checks"]), g
                else:
                    assert g["reason"] in ("Gemini unavailable", "Gemini's grouping failed its checks"), g
                if a.get("reason") != "Gemini unavailable":  # Gemini answered: at most one revision round, and it says so
                    assert a.get("rounds") in (1, 2) and 0 <= a.get("revised", 0) <= a.get("first_draft_rejected", 0), a
            assert {c["id"] for c in g["checks"]} == CHECK_IDS, g["checks"]
            # whoever grouped and wrote them, the script keeps its shape and every digit is a fact
            structure(sc, state["steps"], state["today"], budget)
            digits_in_facts(sc, "en")
            assert "synthetic" in sc["slides"][-1]["narration"]["en"][0]["text"], sc["slides"][-1]["narration"]["en"][0]["text"]

    ctx.check("narration: the Florida 1,000 MW study finishes (started or joined)", study)
    ctx.check("narration: bad bodies are refused with a reason (422)", validation)
    ctx.check("narration: a size with no finished study is 409 and starts nothing", not_finished)
    ctx.check("narration: intro, one slide per package, closing; packages cover every paid step once; about a minute; every number a fact", script)
    ctx.check("narration: the largest budget is still at most four packages and about a minute", largest)
    ctx.check("narration: Spanish and flexible scripts, numbers checked", spanish)
    ctx.check("narration: voice keys are registered (200 or 503 not configured, never 409)", voice_keys)
    ctx.check("narration: Gemini's grouping and lines pass the same checks, or the engine and templates run labeled", gemini_or_template)
