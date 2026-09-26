"""Smoke checks for the narrated build-up on Strengthen the grid (backend/narrate.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200), auth(), expected.

Read-only: the narration reads a FINISHED unlock study and never starts one (LAZY), so this module starts the
Florida 1,000 MW study itself (like smoke_checks/unlock.py; the server warms it, so this usually joins that job or
finds it cached) and polls it. Then: the script has an intro, one slide per paid campus step the budget buys and a
closing, in order; every digit in every spoken line is one of the script's facts (names that hold digits masked);
the voice keys are registered (a segment answers 200 or 503 'not configured', never 409 'unknown'); Spanish works;
a size with no finished study answers 409 (and still starts nothing); bad bodies 422; without Gemini (the check
server blanks GEMINI_API_KEY, or ai=false) the templates run, labeled. At most one short segment is rendered."""

import math
import re
import time

MW = 1000
DONE_S = 150
POLL_S = 1.5
URL = "/api/strengthen/narration"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


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


def ratings_as_the_page_prints(script: dict, steps: list[dict], lang: str) -> int:
    """Every rating the voice says ("from 235 to 350 megavolt-amperes") is one the plan row beside it prints: the
    page rounds a half up (234.5 -> 235), so the voice must too (Python's round would say 234). Returns pairs seen."""
    by_n = {st["n"]: st for st in steps}
    unit = "megavolt-amperes" if lang == "en" else "megavoltamperios"
    pairs = 0
    for s in script["slides"]:
        if s["kind"] != "step":
            continue
        st = by_n[int(s["id"].split("-")[1])]
        printed = {page_round(pj[k]) for pj in st["projects"] for k in ("rating_before_mva", "rating_after_mva")}
        for f in script["facts"]:
            if f["unit"] == "MVA" and f["slide"] == s["id"]:
                assert f["value"] in printed, (s["id"], f, sorted(printed))
        text = s["narration"][lang][0]["text"]
        for a, b in re.findall(rf"(?:from|de) ([\d,]+) (?:to|a) ([\d,]+) {unit}", text):
            assert int(a.replace(",", "")) in printed and int(b.replace(",", "")) in printed, (s["id"], a, b, sorted(printed), text)
            pairs += 1
    return pairs


def today_areas(steps: list[dict], today: int) -> list[str]:
    """The places the intro must name: today's campuses' places, each once (all of them up to four)."""
    areas = list(dict.fromkeys(st["site"]["area"] for st in steps[:today]))
    return areas if len(areas) <= 4 else areas[:3]


def within(steps: list[dict], budget: float) -> int:
    n = 0
    for st in steps:
        if st["cum_cost"]["high"] > budget + 0.5:
            break
        n = st["n"]
    return n


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
        state["steps"] = steps
        state["today"] = cap["firm"]["today"]
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
        n = within(steps, budget)
        assert sc["synthetic"] is True and "Synthetic grid model" in sc["note"], sc["note"]
        assert sc["region"] == "FL" and sc["mw"] == MW and sc["mode"] == "firm" and sc["lang"] == "en", sc
        assert sc["today"] == today and sc["bought"]["n"] == n and abs(sc["bought"]["cost_high"] - steps[n - 1]["cum_cost"]["high"]) <= 1, sc["bought"]
        sl = sc["slides"]
        paid = [st["n"] for st in steps[today:n] if not st["free"]]
        assert [s["kind"] for s in sl] == ["intro"] + ["step"] * len(paid) + ["close"], [s["id"] for s in sl]
        assert [s["id"] for s in sl[1:-1]] == [f"step-{k}" for k in paid], [s["id"] for s in sl]
        # the map follows the voice: campuses shown only go up, the intro builds today's, the closing ends on the budget's
        assert sl[0]["step_from"] == 0 and sl[0]["step"] == today and sl[-1]["step"] == n, (sl[0], sl[-1]["step"])
        last = 0
        for s in sl:
            assert s["step_from"] >= last and s["step"] >= s["step_from"], (s["id"], s["step_from"], s["step"], last)
            last = s["step"]
            if s["kind"] == "step":
                k = int(s["id"].split("-")[1])
                assert s["step_from"] == k and s["site"]["area"], s
            segs = s["narration"]["en"]
            assert segs and s["written_by"]["en"] == "template" and s["est_s"]["en"] > 0, s["id"]
            for seg in segs:
                assert HEX32.match(seg["key"]) and seg["role"] == "presenter" and seg["chars"] == len(seg["text"]) >= 40, (s["id"], seg.get("key"))
                chars = [c["char"] for c in seg["cues"]]
                assert chars == sorted(chars) and all(0 <= c <= seg["chars"] for c in chars), (s["id"], seg["cues"])
                vals = [c["value"] for c in seg["cues"] if c["name"] == "step"]
                assert vals and vals == sorted(vals) and vals[-1] == s["step"] and all(s["step_from"] <= v <= s["step"] or s["kind"] == "intro" for v in vals), (s["id"], vals)
            if s["kind"] == "step":
                assert s["site"]["area"].lower() in segs[0]["text"].lower(), (s["id"], segs[0]["text"])
        assert "synthetic grid model" in sl[0]["narration"]["en"][0]["text"], sl[0]["narration"]["en"][0]["text"]
        for area in today_areas(steps, today):  # every campus that fits today is placed
            assert area.lower() in sl[0]["narration"]["en"][0]["text"].lower(), (area, sl[0]["narration"]["en"][0]["text"])
        assert ratings_as_the_page_prints(sc, steps, "en") >= 1, "no step says its upgrade's ratings"
        assert sum(1 for s in sl for seg in s["narration"]["en"] if "synthetic" in seg["text"]) == 1, "the synthetic model is named once"
        assert digits_in_facts(sc, "en") >= len(paid) * 2, "too few numbers checked"
        # the template runs, labeled
        assert sc["ai"]["by"] == "template" and sc["ai"]["fallback"] is True and sc["ai"]["requested"] is False, sc["ai"]
        state["script"] = sc
        again = ctx.request("POST", URL, body())
        assert again["slides"] == sc["slides"], "the same request gave another script"
        assert time.monotonic() - t0 < 30, "the template script should come back at once"

    def spanish():
        sc = ctx.request("POST", URL, body(lang="es"))
        assert sc["lang"] == "es" and [s["id"] for s in sc["slides"]] == [s["id"] for s in state["script"]["slides"]], "ES has other slides"
        for s in sc["slides"]:
            text = s["narration"]["es"][0]["text"]
            assert re.search(r"\b(el|la|los|las|en|de)\b", text) and "en" not in s["narration"], (s["id"], text[:80])
        assert "modelo sintético" in sc["slides"][0]["narration"]["es"][0]["text"], sc["slides"][0]["narration"]["es"][0]["text"]
        digits_in_facts(sc, "es")
        ratings_as_the_page_prints(sc, state["steps"], "es")
        flex = ctx.request("POST", URL, body(mode="flexible", budget=0))
        assert flex["mode"] == "flexible" and [s["kind"] for s in flex["slides"]] == ["intro", "close"], [s["id"] for s in flex["slides"]]
        assert "flexible" in flex["slides"][0]["narration"]["en"][0]["text"], flex["slides"][0]["narration"]["en"][0]["text"]
        digits_in_facts(flex, "en")
        fm = state["flex"]
        for area in today_areas(fm["steps"], fm["today"]):  # the flexible intro places every campus too
            assert area.lower() in flex["slides"][0]["narration"]["en"][0]["text"].lower(), (area, flex["slides"][0]["narration"]["en"][0]["text"])

    def voice_keys():
        segs = [seg for s in state["script"]["slides"] for seg in s["narration"]["en"]]
        shortest = min(segs, key=lambda x: x["chars"])
        st = ctx.request("GET", "/api/voice/status")
        if not st["configured"]:
            r = ctx.request("POST", "/api/voice/segment", {"key": shortest["key"]}, expect=503)
            assert r["detail"] == "Voice not configured", r
            return
        a = ctx.request("POST", "/api/voice/segment", {"key": shortest["key"]})  # one short line (~170 characters)
        assert a["audio_url"].endswith(".mp3") and a["duration_s"] > 0 and a["words"], a

    def gemini_or_template():
        ai = ctx.request("GET", "/api/ai/status")
        sc = ctx.request("POST", URL, body(ai=True))
        a = sc["ai"]
        assert a["surface"] == "strengthen_narration" and a["requested"] is True, a
        assert any(s["id"] == "strengthen_narration" for s in ai["surfaces"]), "the AI panel does not list the narration"
        if not ai["configured"]:
            assert a["by"] == "template" and a["fallback"] is True and a["reason"] == "Gemini not configured", a
            assert all(s["written_by"]["en"] == "template" for s in sc["slides"]), "a line claims Gemini without a key"
        else:
            assert a["by"] in ("gemini", "mixed", "template"), a
            if a.get("reason") != "Gemini unavailable":  # Gemini answered: at most one revision round, and it says so
                assert a.get("rounds") in (1, 2) and 0 <= a.get("revised", 0) <= a.get("first_draft_rejected", 0), a
        # whoever wrote them, the lines keep the structure and every digit is a fact
        assert [s["id"] for s in sc["slides"]] == [s["id"] for s in state["script"]["slides"]]
        digits_in_facts(sc, "en")
        ratings_as_the_page_prints(sc, state["steps"], "en")
        for area in today_areas(state["steps"], state["today"]):
            assert area.lower() in sc["slides"][0]["narration"]["en"][0]["text"].lower(), (area, sc["slides"][0]["narration"]["en"][0]["text"])
        for s in sc["slides"]:
            if s["kind"] == "step":
                assert s["site"]["area"].lower() in s["narration"]["en"][0]["text"].lower(), s["narration"]["en"][0]["text"]

    ctx.check("narration: the Florida 1,000 MW study finishes (started or joined)", study)
    ctx.check("narration: bad bodies are refused with a reason (422)", validation)
    ctx.check("narration: a size with no finished study is 409 and starts nothing", not_finished)
    ctx.check("narration: intro, one slide per paid step the budget buys, closing; the map's steps follow; every number a fact", script)
    ctx.check("narration: Spanish and flexible scripts, numbers checked", spanish)
    ctx.check("narration: voice keys are registered (200 or 503 not configured, never 409)", voice_keys)
    ctx.check("narration: Gemini's lines pass the same checks, or the template runs labeled", gemini_or_template)
