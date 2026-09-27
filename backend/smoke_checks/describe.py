"""Smoke checks for the audio description of the presentation (backend/describe.py). Loaded by smoke_test.py. Public;
creates nothing another user sees.

Every beat of the hero deck carries an "On the map" line in English and Spanish (12 to 35 words, at most two sentences,
never a prediction, every number one of the deck's own facts, "estimated"/"the model" wherever a real-world claim could
arise), registered with the voice service like the deck's own lines (a request for its audio is 200 or a clear 503,
never 409), and the downloadable transcript carries the lines only when the viewer asked for them. Without a voice key
nothing is rendered; with one, at most the shortest line is (cached on disk by its text, so a repeat run spends nothing)."""

import json
import os
import re
import urllib.error
import urllib.request

HEX32 = re.compile(r"^[0-9a-f]{32}$")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
FORBIDDEN = re.compile(r"\b(FPL|TECO|JEA|OUC|NextEra|Duke Energy|Dominion|FEMA|ERCOT|will)\b|\b(19|20)\d\d\b|hurricane [A-Z]", re.IGNORECASE)
HEDGE = re.compile(r"estimat|assumed|the model|según la estimación|se supone|el modelo|estimad", re.IGNORECASE)
# a beat that names people or money must say it is an estimate (or a model result)
CLAIMS = re.compile(r"\$|people|personas|dólares", re.IGNORECASE)
PRESENTATION = ("toll", "chain", "areas", "hospitals", "cause", "fix", "bottom_line")


def _base() -> str:
    """The server under test: smoke_test.py keeps it in BASE, scratch/run_one_smoke.py in base."""
    import __main__

    for name in ("BASE", "base"):
        v = getattr(__main__, name, None)
        if isinstance(v, str) and v.startswith("http"):
            return v.rstrip("/")
    return os.getenv("SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")


def _http(method: str, path: str, data=None):
    """(status, parsed JSON or raw text) for any status: the voice routes answer 200, 503 or 409 on purpose."""
    req = urllib.request.Request(_base() + path, method=method, data=None if data is None else json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    try:
        return code, json.loads(raw)
    except ValueError:
        return code, raw.decode("utf-8", errors="replace")


def _forms(v: float) -> set[str]:
    """The spoken and written forms a fact can take (rounded to a thousand, in millions, ...): the deck's own rule."""
    a = abs(float(v))
    out = {str(int(round(a))), str(int(a))}
    for d in (1, 2):
        out.add(f"{a:.{d}f}".rstrip("0").rstrip("."))
    for k in range(1, 7):
        r = round(a, -k)
        if r:
            out.add(str(int(r)))
    for div in (1e3, 1e6, 1e9):
        for d in (0, 1, 2):
            x = f"{a / div:.{d}f}".rstrip("0").rstrip(".") if d else f"{a / div:.0f}"
            if x not in ("0", ""):
                out.add(x)
    return out


def _allowed(report: dict, deck: dict) -> set[str]:
    out: set[str] = set()
    for f in list(report.get("facts") or []) + list(deck.get("extra_facts") or []):
        v = f.get("value") if isinstance(f, dict) else None
        if isinstance(v, bool) or v is None:
            continue
        vals = [float(v)] if isinstance(v, (int, float)) else [float(x.replace(",", "")) for x in NUM.findall(str(v))]
        for x in vals:
            out |= _forms(x)
    return out


def register(ctx):
    hero = ctx.expected["hero"]
    case = {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["mw"], "propose": False}
    state = {}

    def deck():
        if "deck" not in state:
            state["deck"] = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False, "length": "full"})
            state["report"] = ctx.request("POST", "/api/briefing", case)
        return state["deck"], state["report"]

    def test_lines():
        d, report = deck()
        allowed = _allowed(report, d)
        got = {s["id"]: s["describe"] for s in d["slides"] if s.get("describe")}
        missing = [i for i in PRESENTATION if i in {s["id"] for s in d["slides"]} and i not in got]
        assert not missing, f"the hero's beats have no description: {missing}"
        for sid, desc in got.items():
            assert set(desc) == {"en", "es"}, f"{sid}: both languages or none, got {sorted(desc)}"
            for lang, item in desc.items():
                text = item["text"]
                where = f"{sid} ({lang})"
                assert HEX32.match(item["key"]), f"{where}: no voice key"
                assert item["chars"] == len(text), f"{where}: chars"
                n = len(text.split())
                assert 12 <= n <= 35, f"{where}: {n} words: {text}"
                assert len(SENTENCE_END.findall(text)) <= 2, f"{where}: more than two sentences: {text}"
                assert not FORBIDDEN.search(text), f"{where}: forbidden wording: {text}"
                assert not text.lower().startswith("on the map"), f"{where}: the page adds the prefix, the text must not"
                if CLAIMS.search(text):
                    assert HEDGE.search(text), f"{where}: names people or money without saying it is an estimate: {text}"
                for tok in NUM.findall(text):
                    t = tok.replace(",", "").rstrip(".")
                    assert t in allowed, f"{where}: the number {tok} is not one of the deck's facts: {text}"

    def test_toll_matches_deck():
        # the toll beat's figure is the deck's own: the description prints the same cost the slide's big number shows
        d, _ = deck()
        toll = next(s for s in d["slides"] if s["id"] == "toll")
        shown = toll["big"]["display"]["en"]  # "$1.08 billion"
        assert shown in toll["describe"]["en"]["text"], (shown, toll["describe"]["en"]["text"])
        chain = next(s for s in d["slides"] if s["id"] == "chain")
        headline_n = int(re.match(r"\d+ steps?: (\d+) lines? tripped", chain["headline"]["en"]).group(1))
        words = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
        assert words[headline_n].capitalize() in chain["describe"]["en"]["text"], "the chain beat's count of trips differs from the slide's headline"

    def test_voice_registered():
        d, _ = deck()
        item = min((s["describe"]["en"] for s in d["slides"] if s.get("describe")), key=lambda x: x["chars"])
        st = ctx.request("GET", "/api/voice/status")
        code, body = _http("POST", "/api/voice/segment", {"key": item["key"]})
        assert code != 409, "the description's voice key was never registered (409)"
        if st["configured"]:
            assert code == 200 and body["audio_url"].endswith(".mp3"), (code, body)
        else:
            assert code in (200, 503), (code, body)  # 200: the audio is already on disk; else a clear "not configured"
            if code == 503:
                assert body["detail"] == "Voice not configured", body
        state["configured"] = bool(st["configured"])

    def test_download():
        d, _ = deck()
        if state.get("configured") or ctx.request("GET", "/api/voice/status")["configured"]:
            return  # with a voice the download renders every line: not spent on a smoke run
        for lang, label in (("en", "ON THE MAP:"), ("es", "EN EL MAPA:")):
            code, plain = _http("POST", "/api/voice/download", {"deck_key": d["deck_key"], "lang": lang})
            assert code == 200 and plain["txt_url"], (code, plain)
            code, with_ = _http("POST", "/api/voice/download", {"deck_key": d["deck_key"], "lang": lang, "describe": True})
            assert code == 200 and with_["txt_url"] and with_["txt_url"] != plain["txt_url"], (code, with_)
            t_plain = urllib.request.urlopen(_base() + plain["txt_url"], timeout=60).read().decode("utf-8")
            t_with = urllib.request.urlopen(_base() + with_["txt_url"], timeout=60).read().decode("utf-8")
            n = sum(1 for s in d["slides"] if s.get("describe"))
            assert label not in t_plain, "the transcript carries descriptions the viewer did not ask for"
            assert t_with.count(label) == n, f"{lang}: {t_with.count(label)} described beats in the transcript, expected {n}"
            first = next(s for s in d["slides"] if s.get("describe"))
            assert first["describe"][lang]["text"] in t_with, "the description's own words are missing from the transcript"
            code, vtt = _http("GET", with_["vtt_url"])
            assert code == 200 and first["describe"][lang]["text"][:40] in vtt, "the captions file lacks the description"
            assert with_["duration_s"] > plain["duration_s"], "the described transcript is not longer than the plain one"

    ctx.check("describe: every beat of the hero deck has an On-the-map line in EN and ES (12-35 words, no prediction, every number a fact, hedged)", test_lines)
    ctx.check("describe: the toll's cost and the chain's count are the slides' own figures", test_toll_matches_deck)
    ctx.check("describe: the line's voice key is registered (audio 200 or a clear 503, never 409)", test_voice_registered)
    ctx.check("describe: the download carries the descriptions only when asked (transcript and captions, EN and ES)", test_download)
