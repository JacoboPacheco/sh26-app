"""Smoke checks for Watch the story (backend/show.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200).

Read-only: a show is computed from the engine and cached per (episode, region, lang); nothing is stored for anyone.
Checks: the episode list; every episode's show (English) validates against the contract the player plays -- every
layer type known and shaped, lat/lon in range, every line's speaker known and its fact ids real, every digit in every
line one of the facts (names that hold digits masked, the deck's rounding forms), the synthetic note, the first
scene naming the synthetic model (Build together: the public filings), the voice keys registered (a segment answers
200 or 503, never 409 unknown), 8-14 scenes and 2-5 minutes; without Gemini (the check server blanks
GEMINI_API_KEY) the labeled template path runs; a repeat POST answers "done" at once; Spanish works; an unknown
episode is 404, a bad body 422, an unknown job 404."""

import math
import re
import time

EPISODES = ("collapse", "hurricane", "boom", "strengthen", "together")
DONE_S = 300
POLL_S = 1.0
LAYERS = {"grid", "lines", "points", "zones", "storm", "counter", "meter", "bars", "compare", "title", "lower_third", "agent", "quote", "timeline"}
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def page_round(x: float) -> int:
    return int(math.floor(float(x) + 0.5))


def forms(v: float) -> set[str]:
    """Every way a fact's value may be printed (the deck's rule; the same as smoke_checks/narrate.py)."""
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
    for s in (1.0, 1e3, 1e6, 1e9):
        x = a / s
        if x >= 1:
            k = 2 - int(math.floor(math.log10(x)))
            out.add(dec(round(x, k), max(k, 0)))
    return out


def latlon(lat, lon, where):
    assert isinstance(lat, (int, float)) and isinstance(lon, (int, float)), f"{where}: lat/lon not numbers"
    assert 15 <= lat <= 50 and -125 <= lon <= -65, f"{where}: ({lat}, {lon}) is not a U.S. lat/lon"


def validate(show: dict, episode: str, lang: str) -> dict:
    assert show["episode"] == episode and show["lang"] == lang, (show.get("episode"), show.get("lang"))
    # the grid episodes say the grid is synthetic; Build together (no grid model on screen) says its plans are the filings
    want_note = "Two utilities' public filings" if episode == "together" else "Synthetic grid model"
    assert show["synthetic"] is True and show["note"].startswith(want_note), f"the on-screen note should start {want_note!r}"
    assert isinstance(show["sources"], list) and show["sources"], "no sources"
    assert set(show["voices"]) >= {"presenter", "analyst"}, "voices"
    ai = show["ai"]
    assert ai["status"] in ("gemini", "fallback", "not_configured"), ai["status"]
    assert isinstance(ai["trace"], list), "ai.trace"
    for t in ai["trace"]:
        assert t["actor"] in ("gemini", "engine") and t["kind"] in ("tool", "draft", "check", "rewrite", "accept"), t
    facts = show["facts"]
    allowed = set()
    for f in facts.values():
        v = f["value"]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            allowed |= forms(v)
    scenes = show["scenes"]
    assert 8 <= len(scenes) <= 14, f"{len(scenes)} scenes (8-14 expected)"
    total = 0
    chapters = set()
    for i, sc in enumerate(scenes):
        where = sc["id"]
        chapters.add(sc["chapter"])
        assert isinstance(sc["min_ms"], int) and sc["min_ms"] >= 3000, f"{where}: min_ms"
        (s, w), (n, e) = sc["camera"]["bounds"]
        assert s < n and w < e, f"{where}: camera bounds"
        latlon(s, w, where)
        latlon(n, e, where)
        for ly in sc["layers"]:
            t = ly["type"]
            assert t in LAYERS, f"{where}: unknown layer {t}"
            if t == "lines":
                assert ly["style"] in ("stress", "over", "trip", "upgrade", "project_a", "project_b", "corridor"), ly["style"]
                for it in ly["items"]:
                    assert len(it["path"]) >= 2, f"{where}: a line path needs two points"
                    for p in it["path"]:
                        latlon(p[0], p[1], where)
            elif t == "points":
                assert ly["kind"] in ("campus", "town", "station", "plant", "project", "hospital"), ly["kind"]
                for it in ly["items"]:
                    latlon(it["lat"], it["lon"], where)
            elif t == "zones":
                assert ly["style"] in ("blackout", "restored", "storm"), ly["style"]
                for it in ly["items"]:
                    latlon(it["lat"], it["lon"], where)
                    assert it["radius_km"] > 0, f"{where}: zone radius"
            elif t == "storm":
                assert len(ly["track"]) >= 2 and ly["radius_km"] > 0, f"{where}: storm"
            elif t == "counter":
                assert ly["format"] in ("people", "usd", "mw", "gw", "count", "pct") and ly["tone"] in ("loss", "gain", "neutral"), ly
                assert isinstance(ly["from"], (int, float)) and isinstance(ly["to"], (int, float)), f"{where}: counter values"
            elif t == "meter":
                assert 0 <= ly["today"] <= ly["cells"] and 0 <= ly["filled"] <= ly["cells"], f"{where}: meter {ly}"
            elif t == "agent":
                for st in ly["steps"]:
                    assert st["actor"] in ("gemini", "engine") and st["kind"] in ("tool", "propose", "verify", "reject", "accept"), st
            elif t == "bars":
                assert ly["items"], f"{where}: empty bars"
            # timed layers: every item's moment is 0..1 of the scene; a counter's leaps land on its end value
            if ly.get("timing") == "span":
                for it in ly.get("items") or []:
                    if "at" in it:
                        assert 0 <= it["at"] <= 1, f"{where}: an item's at {it['at']} is outside the scene"
            if t == "counter" and ly.get("leaps"):
                for x in ly["leaps"]:
                    assert 0 <= x["at"] <= 1 and isinstance(x["value"], (int, float)), f"{where}: a counter leap {x}"
                assert ly["leaps"][-1]["value"] == ly["to"], f"{where}: the counter's last leap isn't its end value"
            if t == "zones":
                for it in ly["items"]:
                    assert "people" not in it, f"{where}: a zone carries a people figure the map would print (weight only)"
        rv = sc.get("review") or {}
        assert rv.get("by") in ("gemini", "template") and isinstance(rv.get("checked"), int), f"{where}: the scene's review"
        assert 1 <= len(sc["lines"]) <= 3, f"{where}: {len(sc['lines'])} lines"
        for ln in sc["lines"]:
            assert ln["speaker"] in ("presenter", "analyst"), ln["speaker"]
            assert ln["voice"]["id"] and re.match(r"^[0-9a-f]{32}$", ln["voice"]["id"]), f"{where}: voice key"
            for fid in ln["facts"]:
                assert fid in facts, f"{where}: unknown fact id {fid}"
            text = re.sub(r"\b(circuit|unit|circuito|unidad|campus|step|paso|wave|oleada|round|ronda) \d+\b", "x", ln["text"], flags=re.I)
            for name in sorted(show.get("names") or [], key=len, reverse=True):
                text = re.sub(re.escape(name), "Placename", text, flags=re.I)
            for tok in NUM.findall(text):
                t = tok.replace(",", "").rstrip(".")
                norm = f"{float(t):.2f}".rstrip("0").rstrip(".") if "." in t else t
                assert t in allowed or norm in allowed, f"{where}: {tok} is not a fact ({ln['text'][:120]})"
            if ai["status"] != "gemini":
                assert ln["by"] == "template", f"{where}: a line not by the template without Gemini"
        total += max(sc["min_ms"], sum(len(ln["text"]) / 15.0 * 1000 for ln in sc["lines"]))
    if episode == "together":  # each utility's filed year stays with that utility (the truth review's swapped years)
        owned = [(int(f["value"]), f["owner"]) for f in facts.values() if f.get("owner")]
        util = re.compile(r"\b(DESC|Dominion(?: Energy South Carolina)?|Georgia Power)\b")
        for sc in scenes:
            for ln in sc["lines"]:
                for sent in re.split(r"(?<=[.!?])\s+", ln["text"]):
                    ments = [(m.start(), m.group(0)) for m in util.finditer(sent)]
                    for y, owner in owned:
                        for m in re.finditer(rf"(?<!\d){y}(?!\d)", sent):
                            before = [x for x in ments if x[0] < m.start()]
                            after = [x for x in ments if x[0] > m.start()]
                            who = before[-1][1] if before else after[0][1] if after else None
                            assert who is None or any(who == o or o.startswith(who) for o in owner), f"{sc['id']}: {y} is {owner[0]}'s, the line gives it to {who}: {ln['text']}"
    first = " ".join(ln["text"].lower() for ln in scenes[0]["lines"])
    need = ("public filing", "documentos públicos", "documento público") if episode == "together" else ("synthetic", "sintétic")
    assert any(w in first for w in need), f"the first scene doesn't say {need[0]}: {first[:160]}"
    assert 120_000 <= total <= 300_000, f"{total / 60000:.1f} minutes (2-5 expected)"
    assert show["chapters"] == list(dict.fromkeys(sc["chapter"] for sc in scenes)), "chapters"
    return {"minutes": total / 60000, "scenes": len(scenes), "chapters": len(chapters)}


def register(ctx):
    state = {}

    def run(episode: str, lang: str = "en") -> dict:
        start = ctx.request("POST", f"/api/show/{episode}", {"region": "FL", "lang": lang})
        assert start["status"] in ("pending", "done") and start["id"], start
        assert isinstance(start["estimate_s"], (int, float)), start
        t0 = time.monotonic()
        while True:
            s = ctx.request("GET", f"/api/show/jobs/{start['id']}")
            if s["status"] == "done":
                return s["show"]
            assert s["status"] == "pending", s.get("error") or s["status"]
            assert set(s["progress"]) >= {"step", "of", "text"}, s["progress"]
            assert time.monotonic() - t0 < DONE_S, f"{episode} not done after {DONE_S} s"
            time.sleep(POLL_S)

    def episodes():
        eps = ctx.request("GET", "/api/show/episodes")
        assert [e["id"] for e in eps] == list(EPISODES), [e["id"] for e in eps]
        for e in eps:
            assert e["title"] and e["blurb"] and e["region"] and 2 <= e["est_minutes"] <= 5, e

    ctx.check("show: the episode list", episodes)

    for ep in EPISODES:
        def one(ep=ep):
            show = run(ep)
            state[ep] = show
            validate(show, ep, "en")
        ctx.check(f"show: {ep} validates against the contract", one)

    def repeat():
        again = ctx.request("POST", "/api/show/collapse", {"region": "FL", "lang": "en"})
        assert again["status"] == "done", f"a repeat POST should answer done at once, got {again['status']}"
        s = ctx.request("GET", f"/api/show/jobs/{again['id']}")
        assert s["status"] == "done" and s["show"]["scenes"], s["status"]

    ctx.check("show: a repeat POST answers done at once (cached)", repeat)

    def template_path():
        show = state.get("collapse") or run("collapse")
        ai = show["ai"]
        if ai["status"] != "gemini":
            assert all(sc["written_by"] == "template" for sc in show["scenes"]), "template path: a scene not by the template"
            assert ai["trace"] and ai["trace"][0]["actor"] == "engine", "template path: the trace says why"
        # a line's voice key is registered with voice.py: 200 (audio) or 503 (not configured), never 409 unknown
        key = min((ln for sc in show["scenes"] for ln in sc["lines"]), key=lambda ln: len(ln["text"]))["voice"]["id"]
        try:
            ctx.request("POST", "/api/voice/segment", {"key": key}, expect=200)
        except AssertionError as e:  # 503: ElevenLabs not configured (the browser voice reads it); 429: its daily cap
            msg = str(e)
            assert any(s in msg for s in ("503 != 200", "429 != 200", "got 503", "got 429")), f"voice segment: {e}"

    ctx.check("show: the template path is labeled and its voice keys are registered", template_path)

    def spanish():
        show = run("strengthen", "es")
        validate(show, "strengthen", "es")
        assert any(w in " ".join(ln["text"] for ln in show["scenes"][1]["lines"]) for w in (" la ", " el ", " de ")), "not Spanish"

    ctx.check("show: Spanish works", spanish)

    def errors():
        ctx.request("POST", "/api/show/nope", {"lang": "en"}, expect=404)
        ctx.request("POST", "/api/show/collapse", {"lang": "fr"}, expect=422)
        ctx.request("POST", "/api/show/collapse", {"lang": "en", "extra": 1}, expect=422)
        ctx.request("POST", "/api/show/collapse", {"region": "ZZ", "lang": "en"}, expect=422)
        ctx.request("POST", "/api/show/hurricane", {"region": "OH", "lang": "en"}, expect=422)
        ctx.request("GET", "/api/show/jobs/doesnotexist123", expect=404)

    ctx.check("show: unknown episode 404, bad body 422, unknown job 404", errors)
