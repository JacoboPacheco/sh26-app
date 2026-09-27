"""Smoke checks for "Write my public comment" (backend/comment.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200), auth(), expected.
A public POST that stores nothing. With a Gemini key the comment is Gemini's (checked) or the labeled template;
without one (GEMINI_API_KEY blank) the labeled template runs. ?ai=false always asks for the template, so the
template's own checks run on every server."""

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

PID = "stonebridge-fort-meade"
PATH = f"/api/vote/proposal/{PID}/comment"
FORBIDDEN = re.compile(
    r"will cause|\bcaused\b|responsible for|is going to black|will black|will trigger|\bblame|illegal|fraud|scam|corrupt|guilty|negligen"
    r"|causar[áa]|caus[óo]|responsables? de|dejar[áa] sin luz",
    re.I,
)
FRAME_WORDS = {  # the framing test backend/comment.py applies: one sentence says the model is open and synthetic
    "en": (r"\bsynthetic\b", r"\bopen\b", r"\bmodel|\bsimulat|\bsynthetic\s+(?:power\s+|electric\s+)?grid\b"),
    "es": (r"\bsint[eé]tic[oa]s?\b", r"\babiert[oa]s?\b", r"\bmodelos?\b|\bsimulaci[oó]n|\bred\b"),
}


def _framed(text: str, lang: str) -> bool:
    return any(all(re.search(w, s, re.I) for w in FRAME_WORDS[lang]) for s in re.split(r"(?<=[.!?])\s+", text))
DIGITS = re.compile(r"(?<![\w])\d+(?:[.,]\d+)*")


def _page_numbers(x, acc):
    """Every number the proposal page carries (values and the numbers written in its texts)."""
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        if math.isfinite(float(x)):
            acc.append(abs(float(x)))
    elif isinstance(x, str):
        for t in DIGITS.findall(x):
            t = t.strip(",.")
            for v in {t.replace(",", ""), t.replace(".", "").replace(",", ".")}:
                try:
                    acc.append(float(v))
                except ValueError:
                    pass
    elif isinstance(x, dict):
        for v in x.values():
            _page_numbers(v, acc)
    elif isinstance(x, list):
        for v in x:
            _page_numbers(v, acc)


def _on_page(v, pool) -> bool:
    return any(abs(v - p) <= max(0.05 * p, 0.006) for p in pool)


# a figure followed by one of these counts something the model computed: never credited to a news report
MODEL_UNIT_AFTER = re.compile(r"^\s*(?:lines?|transformers?|people|households|hours?|líneas?|transformadores?|personas|hogares|horas?)\b", re.I)
# no vote is pending on these (as reported): the comment says so and never asks for a vote on it now
NOT_PENDING = {
    "sentinel-grove-fort-pierce": ("withdrawn", r"withdr|retirad"),
    "pba-holdings-loxahatchee": ("denied", r"denied|denegad"),
    "nextnrg-near-jacksonville-international-airport": ("not_filed", r"no application|ninguna solicitud"),
}
UNCONDITIONAL_VOTE = re.compile(r"\bvote no on this proposal\b|\bas it stands\b|\bbefore you vote\.|\bvoten no a esta propuesta\b|\btal como est[aá]\b", re.I)


def register(ctx):
    got = {}

    def page(pid=PID):
        if pid not in got:
            p = ctx.request("GET", f"/api/vote/proposal/{pid}")
            acc = []
            _page_numbers(p, acc)
            got[pid] = (p, acc)
        return got[pid]

    def verify(r, lang, concerns, stance, minutes, pid=PID):
        p, pool = page(pid)
        text = r["text"]
        assert isinstance(text, str) and len(text) > 200, text[:80]
        assert r["lang"] == lang and r["stance"] == stance and r["minutes"] == minutes and r["concerns"] == concerns, (r["lang"], r["concerns"])
        # every figure in the text sits inside one of the checked numbers, and each is one of the page's numbers
        spans = [n["at"] for n in r["numbers"]]
        for m in DIGITS.finditer(text):
            assert any(a <= m.start() and m.end() <= b for a, b in spans), f"{m.group(0)!r} is not in the numbers list"
        for n in r["numbers"]:
            a, b = n["at"]
            assert text[a:b] == n["text"], (text[a:b], n["text"])
            assert n["ok"] and n["fact"] and n["source"] and n["source"]["label"], n
            if n["kind"] == "id":
                assert n["value"] in text and n["fact"], n
                continue
            vals = n["value"] if isinstance(n["value"], list) else [n["value"]]
            for v in vals:
                assert v is not None and (_on_page(v, pool) or _on_page(v / 1e6, pool) or _on_page(v / 1e9, pool)), f"{n['text']} ({v}) is not on the proposal page"
            if MODEL_UNIT_AFTER.match(text[b: b + 24]):
                assert n["fact_kind"] in ("model", "estimate"), f"{n['text']}{text[b:b + 16]!r} is credited to {n['source']['label']!r}"
        assert r["checked"] == r["total"] == len(r["numbers"]) and r["total"] >= 1, (r["checked"], r["total"])
        assert r["ok"] is True and all(c["ok"] for c in r["checks"]) and not r["findings"], r["checks"]
        # only check ids and labels leave the server, never a rejected draft's words
        assert "draft_findings" not in r and all(set(c) == {"id", "label", "caught"} for c in r["draft_checks"]), r.get("draft_checks")
        bad = FORBIDDEN.search(text)
        assert not bad, f"forbidden wording: {bad.group(0)!r}"
        assert _framed(text, lang), "the synthetic-model framing is missing"
        body = p["civic"]["decision_body"]
        assert body and r["addressee"] == body["name"] and body["name"].lower() in text.lower(), (r["addressee"], body)
        if pid in NOT_PENDING:
            kind, said = NOT_PENDING[pid]
            assert r["situation"]["kind"] == kind and r["situation"]["note"], r["situation"]
            assert re.search(said, text, re.I), f"does not say it was {kind}"
            bad_vote = UNCONDITIONAL_VOTE.search(text)
            assert not bad_vote, f"asks for a vote as if one were pending: {bad_vote.group(0)!r}"
        else:
            assert r["situation"]["kind"] == "pending" and r["situation"]["note"] is None, r["situation"]
        assert r["send"]["url"] in (body.get("comment_url"), body.get("url")), r["send"]
        assert ("[your name]" if lang == "en" else "[su nombre]") in text, "the resident's name placeholder"
        assert r["words"] > 0 and r["seconds"] == round(r["words"] / 130 * 60), (r["words"], r["seconds"])
        assert 0.55 * minutes * 130 <= r["words"] <= 1.3 * minutes * 130, (r["words"], minutes)
        assert r["by"] in ("gemini", "template") and r["fallback"] == (r["by"] == "template"), (r["by"], r["fallback"])
        if r["fallback"]:
            assert r["why"], "a template answer says why it ran"
        assert "synthetic" in r["frame"].lower() and "not a prediction" in r["frame"].lower()

    def gemini_or_labeled_en():
        r = ctx.request("POST", PATH, {"concerns": ["bill", "blackouts"], "stance": "questions", "minutes": 2, "lang": "en"})
        verify(r, "en", ["bill", "blackouts"], "questions", 2)
        assert "?" in r["text"]

    def gemini_or_labeled_es():
        r = ctx.request("POST", PATH, {"concerns": ["water", "bill"], "stance": "oppose", "minutes": 2, "lang": "es"})
        verify(r, "es", ["bill", "water"], "oppose", 2)  # returned in the page's order
        # the stance words backend/comment.py accepts ("mi firme oposición", "que vote no", "me opondría" ...)
        assert re.search(r"\bme opongo|\bme opondr|\boposici[oó]n|\bvot(?:ar|en|e|ar[aá]n?) (?:en contra|no)\b|\ben contra\b", r["text"], re.I), "the stance is said"

    def template_runs_labeled():
        for lang, stance, minutes, concerns in (("en", "support_conditions", 1, ["bill", "water", "jobs_taxes"]), ("es", "questions", 3, ["blackouts", "backup_air"]),
                                                ("en", "oppose", 3, []), ("es", "support_conditions", 2, ["bill", "blackouts", "water", "backup_air", "jobs_taxes"])):
            r = ctx.request("POST", PATH + "?ai=false", {"concerns": concerns, "stance": stance, "minutes": minutes, "lang": lang})
            assert r["by"] == "template" and r["fallback"] is True and r["why"], (r["by"], r["why"])
            verify(r, lang, concerns, stance, minutes)

    def not_pending_says_where_it_stands():
        # withdrawn, denied, never filed: the plain version (every server) and Gemini's (when a key is set) say so and
        # write every request for if it, or a campus like it, comes back
        for pid in NOT_PENDING:
            for lang, stance, minutes in (("en", "oppose", 2), ("es", "questions", 1)):
                r = ctx.request("POST", f"/api/vote/proposal/{pid}/comment?ai=false", {"concerns": ["blackouts", "water"], "stance": stance, "minutes": minutes, "lang": lang})
                verify(r, lang, ["blackouts", "water"], stance, minutes, pid)
        r = ctx.request("POST", "/api/vote/proposal/sentinel-grove-fort-pierce/comment", {"concerns": ["blackouts", "water"], "stance": "oppose", "minutes": 2, "lang": "en"})
        verify(r, "en", ["blackouts", "water"], "oppose", 2, "sentinel-grove-fort-pierce")

    def blank_key_is_the_labeled_template():
        st = ctx.request("GET", "/api/ai/status")
        r = ctx.request("POST", PATH, {"concerns": ["bill"], "stance": "questions", "minutes": 1, "lang": "en"})
        if not st["configured"]:
            assert r["by"] == "template" and r["fallback"] is True and r["why"] == "Gemini not configured", (r["by"], r["why"])
        verify(r, "en", ["bill"], "questions", 1)
        assert any(s["id"] == "comment" for s in st["surfaces"]), "listed in How AI is used"

    def refuses_bad_input():
        ok = {"concerns": ["bill"], "stance": "questions", "minutes": 2, "lang": "en"}
        for bad in (
            {**ok, "stance": "maybe"},
            {**ok, "minutes": 4},
            {**ok, "minutes": 0},
            {**ok, "minutes": "2"},
            {**ok, "minutes": 1.5},
            {**ok, "lang": "fr"},
            {**ok, "concerns": ["bill", "bill"]},
            {**ok, "concerns": ["noise"]},
            {**ok, "concerns": ["bill", "blackouts", "water", "backup_air", "jobs_taxes", "bill"]},
            {**ok, "concerns": "bill"},
            {**ok, "extra": 1},
        ):
            ctx.request("POST", PATH, bad, expect=422)
        ctx.request("POST", "/api/vote/proposal/UPPER-case/comment", ok, expect=422)
        ctx.request("POST", "/api/vote/proposal/no-such-campus/comment", ok, expect=404)

    def busy_model_falls_to_the_fast_one():
        """In process, no network: the stronger model answers 503 "high demand" (seen Sat night on gemini-3.5-flash: every Render
        comment fell back to the plain template), the same prompt goes to the fast model, and the checked comment is Gemini's."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")  # llm.py imports auth.py; this process only
        try:
            import comment
            import llm
        except ImportError as e:  # a deployed run without the backend folder on the path
            raise AssertionError(f"comment.py not importable here: {e}") from e
        order = comment._model_order()
        assert len(order) == 2 and order[0] != order[1], order
        primary, fast = order
        body = comment.CommentIn(concerns=["blackouts", "bill"], stance="questions", minutes=2, lang="en")
        sh = comment._sheet(PID, body.concerns)
        paras = comment.template(sh, body).split("\n\n")  # the plain version passes the same checks: a stand-in for a good Gemini draft
        calls: list = []

        def post(url, req, key, timeout):
            model = url.split("/models/")[1].split(":")[0]
            calls.append(model)
            if model == primary:
                raise urllib.error.HTTPError(url, 503, "UNAVAILABLE", {}, io.BytesIO(b'{"error": {"code": 503, "status": "UNAVAILABLE"}}'))
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({"paragraphs": paras})}]}}]}

        saved = {"key": os.environ.get("GEMINI_API_KEY"), "post": llm._post_json, "file": llm.CACHE_FILE, "cache": llm._cache}
        os.environ["GEMINI_API_KEY"] = "stub-not-a-key"  # never sent: _post_json is stubbed
        llm.CACHE_FILE = ""  # stubbed replies never reach the disk cache
        llm._cache = type(saved["cache"])()
        llm._post_json = post
        comment._busy_until.clear()
        try:
            text, why, _ = asyncio.run(comment._gemini(sh, body, time.perf_counter()))
            assert text and why is None, (why, text and text[:80])
            assert calls == [primary, fast], calls
            assert comment._busy_until.get(primary, 0) > time.time(), "the model that just failed is skipped for a while"
            calls.clear()
            llm._cache = type(saved["cache"])()
            asyncio.run(comment._gemini(sh, body, time.perf_counter()))
            assert calls == [fast], calls  # while it cools down, straight to the fast model
            # both busy: the labeled plain version (why says Gemini was unavailable), never an error
            comment._busy_until.clear()
            llm._cache = type(saved["cache"])()
            llm._post_json = lambda url, req, key, timeout: (_ for _ in ()).throw(urllib.error.HTTPError(url, 503, "UNAVAILABLE", {}, io.BytesIO(b"{}")))
            text, why, _ = asyncio.run(comment._gemini(sh, body, time.perf_counter()))
            assert text is None and why == "Gemini unavailable", (text, why)
        finally:
            llm._post_json, llm.CACHE_FILE, llm._cache = saved["post"], saved["file"], saved["cache"]
            comment._busy_until.clear()
            if saved["key"] is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = saved["key"]

    def disclaimer_is_not_a_claim():
        """The checker accepts the plain disclaimer the prompt asks for ("not the real grid") and still refuses a claim about "our" grid."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")
        try:
            import comment
        except ImportError as e:
            raise AssertionError(f"comment.py not importable here: {e}") from e
        body = comment.CommentIn(concerns=["blackouts", "bill"], stance="questions", minutes=2, lang="en")
        sh = comment._sheet(PID, body.concerns)
        base = comment.template(sh, body)
        assert comment.check(base, sh, body)[0], "the plain version passes its own checks"
        ok, findings, _, _ = comment.check(base + " These results are on an open, synthetic grid model, not the real grid.", sh, body)
        assert ok, findings
        for bad in ("On an open, synthetic grid model, the campus strains our infrastructure.", "The campus strains our grid."):
            ok, findings, _, checks = comment.check(base + " " + bad, sh, body)
            assert not ok and any(c["id"] == "framed" and not c["ok"] for c in checks), (bad, findings)

    ctx.check("comment: English, two concerns: every number checked, framed, addressed (Gemini or labeled)", gemini_or_labeled_en)
    ctx.check("comment: Spanish, oppose: every number checked, framed, addressed (Gemini or labeled)", gemini_or_labeled_es)
    ctx.check("comment: the labeled template passes the same checks (ai=false, 1-3 minutes, EN and ES)", template_runs_labeled)
    ctx.check("comment: withdrawn, denied, never filed: says where it stands, no vote asked for now (template and Gemini)", not_pending_says_where_it_stands)
    ctx.check("comment: without a key the labeled template runs; listed in How AI is used", blank_key_is_the_labeled_template)
    ctx.check("comment: bad bodies 422, bad id 422, unknown id 404", refuses_bad_input)
    ctx.check("comment: a busy first model falls to the fast one, and Gemini still answers (in process, stubbed)", busy_model_falls_to_the_fast_one)
    ctx.check("comment: a plain 'not the real grid' disclaimer passes, a claim about our grid is still refused (in process)", disclaimer_is_not_a_claim)
