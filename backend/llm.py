"""
LLM helper (Gemini, free tier). Usage from any router:

    from llm import complete
    text = await complete("Summarize this: ...", system="You are terse.")
    data = await complete(prompt, json_mode=True)   # returns a JSON string
    text = await complete(prompt, fallback="(AI offline) Here's an example answer…")  # never raises
    text = await complete("List the questions on this whiteboard", image=(png_bytes, "image/png"))
    data, offline = await complete_json(prompt, fallback=NO_ANSWER, timeout=10)  # parsed JSON

Needs GEMINI_API_KEY in backend/.env (free key: https://aistudio.google.com/apikey).
Without it a call raises a clear 503 — or returns its `fallback` if one was given —
so the feature can be built and demoed as "not configured" instead of crashing.
The whole-app daily cap (AI_DAILY_LIMIT) is enforced in here too, so a route with a
fallback keeps working when the day's quota is gone. Swap providers by rewriting
`_complete` only — nothing else in the app knows which model is behind it.
"""

import asyncio
import base64
import copy
import hashlib
import http.client
import json
import logging
import os
import time
from collections import OrderedDict
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from limiter import limiter
from models import User

API_BASE = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
# Whole-app cap on AI calls per day, across all users. The demo account is public,
# so a per-IP limit alone can't protect the free quota from one person with a loop.
# Enforced inside complete(): a call past the cap raises 429, or returns its
# fallback if it has one. In-memory — a backend restart resets the count.
AI_DAILY_LIMIT = os.getenv("AI_DAILY_LIMIT", "400/day")
try:
    AI_DAILY_CAP = int(AI_DAILY_LIMIT.split("/")[0])
except ValueError:
    raise RuntimeError(f"AI_DAILY_LIMIT must look like '400/day', got {AI_DAILY_LIMIT!r}") from None
AI_QUOTA_MESSAGE = "AI quota for today is used up — try again tomorrow"
# Google's free quota resets at midnight Pacific, so the day boundary is Pacific too
# (Render's clock is UTC, which would give two quota-days per Google day).
QUOTA_TZ = ZoneInfo("America/Los_Angeles")
_daily = {"day": "", "used": 0}


def _take_daily_slot() -> bool:
    today = datetime.now(QUOTA_TZ).date().isoformat()
    if _daily["day"] != today:
        _daily["day"], _daily["used"] = today, 0
    if _daily["used"] >= AI_DAILY_CAP:
        return False
    _daily["used"] += 1
    return True
# What the AI panel reports (in memory; a backend restart resets it): calls that reached Google, answers
# served from the cache, calls that fell back, and the same per surface (a short label a call site passes).
_stats: dict = {"ok": 0, "fallback": 0, "cached": 0, "by_surface": {}, "last_error": ""}
_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
CACHE_MAX = 256
CACHE_TTL_S = 6 * 3600


def _note(surface: str | None, outcome: str) -> None:
    if outcome in _stats:
        _stats[outcome] += 1
    if surface:
        row = _stats["by_surface"].setdefault(surface, {"ok": 0, "fallback": 0, "cached": 0})
        row[outcome] = row.get(outcome, 0) + 1


def _cache_key(prompt: str, system: str | None, json_mode: bool, schema: dict | None) -> str:
    raw = json.dumps([MODEL, system or "", prompt, json_mode, schema], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> str | None:
    hit = _cache.get(key)
    if not hit:
        return None
    if time.time() - hit[0] > CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return hit[1]


def _cache_put(key: str, text: str) -> None:
    _cache[key] = (time.time(), text)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def usage() -> dict:
    """Today's whole-app AI budget (the number the AI panel shows)."""
    today = datetime.now(QUOTA_TZ).date().isoformat()
    used = _daily["used"] if _daily["day"] == today else 0
    return {"day": today, "used_today": used, "cap": AI_DAILY_CAP, "remaining": max(0, AI_DAILY_CAP - used)}


# Flash-Lite: the free tier allows ~500 requests/day on Lite models vs ~20/day on
# full Flash (as of Sept 2026) — a demo needs the 500. Override with GEMINI_MODEL.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
TIMEOUT_SECONDS = 60

router = APIRouter(prefix="/api/ai", tags=["ai"])


def configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def _post_json(url: str, body: dict, key: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


async def complete(
    prompt: str,
    system: str | None = None,
    json_mode: bool = False,
    fallback: str | None = None,
    image: tuple[bytes, str] | None = None,
    timeout: float | None = None,
    cache: bool = False,
    surface: str | None = None,
) -> str:
    """`fallback`: a canned answer to return instead of raising if the AI is
    unconfigured, out of quota, unreachable, or slow — so a demo survives a dead API.
    Route pattern, so the UI can show an "offline" badge when it fired:
        text = await complete(prompt, fallback=FALLBACK, timeout=10)
        return {"text": text, "fallback": text == FALLBACK}
    The swallowed error is logged as a WARNING (see backend/server.log).
    `timeout`: seconds to wait for Gemini (default 60) — use ~10 on the demo path.
    `image`: (bytes, mime_type) to send alongside the prompt, e.g. the `contents`
    from an upload — Gemini reads photos, screenshots, whiteboards, receipts.
    `cache`: serve an identical earlier answer (same model, system and prompt) from memory for six
    hours instead of spending quota again; only for prompts built from a fact sheet, never for a
    photo. `surface`: a short label for the AI panel's per-feature counters."""
    key = _cache_key(prompt, system, json_mode, None) if cache and image is None else None
    if key:
        hit = _cache_get(key)
        if hit is not None:
            _note(surface, "cached")
            return hit
    try:
        text = await _complete(prompt, system, json_mode, image, timeout)
    except HTTPException as e:
        _stats["last_error"] = str(e.detail)[:200]
        if fallback is not None:
            _note(surface, "fallback")
            logging.getLogger("uvicorn.error").warning("AI fallback used: %s", e.detail)
            return fallback
        raise
    _note(surface, "ok")
    if key:
        _cache_put(key, text)
    return text


async def complete_json(
    prompt: str,
    system: str | None = None,
    fallback: dict | list | None = None,
    image: tuple[bytes, str] | None = None,
    timeout: float | None = None,
    schema: dict | None = None,
    cache: bool = False,
    surface: str | None = None,
) -> tuple[dict | list, bool]:
    """`complete` in JSON mode, parsed, returned as `(data, used_fallback)`. If the
    model returns invalid JSON it is asked once more; then `fallback` is returned
    (a copy — safe to modify) with `used_fallback=True`, or a 502 is raised when
    there is no fallback. Say what shape you want in the prompt
    ('Answer as {"tags": ["..."]}'). Route pattern:
        data, offline = await complete_json(prompt, fallback=NO_ANSWER, timeout=10)
        return {**data, "fallback": offline}
    `schema`: a JSON Schema the reply must follow (Gemini structured output); a call that Google
    rejects with the schema is retried once without it, so a schema quirk never costs the answer.
    `cache` and `surface`: as in `complete`."""
    log = logging.getLogger("uvicorn.error")
    key = _cache_key(prompt, system, True, schema) if cache and image is None else None
    if key:
        hit = _cache_get(key)
        if hit is not None:
            try:
                data = json.loads(hit)
                _note(surface, "cached")
                return data, False
            except ValueError:
                _cache.pop(key, None)
    text = ""
    use_schema = schema
    for attempt in range(2):
        nudge = "" if attempt == 0 else "\n\nYour previous answer was not valid JSON. Reply with ONLY valid JSON."
        err: HTTPException | None = None
        try:
            text = await _complete(prompt + nudge, system, True, image, timeout, use_schema)
        except HTTPException as e:
            err = e
        if err is not None and use_schema is not None and err.status_code == 502 and "(400)" in str(err.detail):
            use_schema = None  # Google refused the schema itself: ask again in plain JSON mode
            err = None
            try:
                text = await _complete(prompt + nudge, system, True, image, timeout, None)
            except HTTPException as e:
                err = e
        if err is not None:
            _stats["last_error"] = str(err.detail)[:200]
            if fallback is not None:
                _note(surface, "fallback")
                log.warning("AI fallback used: %s", err.detail)
                return copy.deepcopy(fallback), True
            raise err
        try:
            data = json.loads(text)
        except ValueError:
            continue
        _note(surface, "ok")
        if key:
            _cache_put(key, text)
        return data, False
    _stats["last_error"] = "invalid JSON twice"
    if fallback is not None:
        _note(surface, "fallback")
        log.warning("AI fallback used: invalid JSON twice: %r", text[:120])
        return copy.deepcopy(fallback), True
    raise HTTPException(status_code=502, detail="AI returned invalid JSON twice")


async def _complete(
    prompt: str,
    system: str | None,
    json_mode: bool,
    image: tuple[bytes, str] | None,
    timeout: float | None,
    schema: dict | None = None,
) -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise HTTPException(
            status_code=503,
            detail="AI is not configured: add GEMINI_API_KEY to backend/.env (free key at aistudio.google.com/apikey)",
        )
    if not _take_daily_slot():
        raise HTTPException(status_code=429, detail=AI_QUOTA_MESSAGE)

    parts: list[dict] = []
    if image is not None:
        data, mime_type = image
        parts.append({"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode()}})
    parts.append({"text": prompt})
    body: dict = {"contents": [{"parts": parts}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}
        if schema is not None:
            body["generationConfig"]["responseJsonSchema"] = schema

    url = f"{API_BASE}/models/{MODEL}:generateContent"
    try:
        data = await asyncio.to_thread(_post_json, url, body, key, timeout or TIMEOUT_SECONDS)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise HTTPException(status_code=502, detail=f"AI request failed ({e.code}): {detail}")
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        # URLError: unreachable; OSError: dropped connection; ValueError: non-JSON reply;
        # HTTPException: truncated or malformed reply
        raise HTTPException(status_code=502, detail=f"AI request failed: {e}")

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise HTTPException(status_code=502, detail="AI returned no text (empty or blocked response)")


class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


# Where the app uses Gemini, for the on-screen "How AI is used" panel (frontend/src/features/ai). Every
# surface follows one rule: Gemini writes or proposes, the engine or the fact sheet checks, and a labeled
# fallback runs when the key, the quota or the network is missing.
SURFACES = [
    {"id": "deck", "name": "Present the damage", "gemini": "Writes the presenter's words from the computed fact sheet.",
     "check": "Every number in the text must appear in the fact sheet, or the plain-template text is used.", "fallback": "Template writer"},
    {"id": "solutions", "name": "Ways to build it", "gemini": "Proposes grid upgrades that would let the full campus connect.",
     "check": "The power-flow engine re-runs the case with each proposal; only plans that hold are shown.", "fallback": "Engine-generated fixes only"},
    {"id": "unlock", "name": "Strengthen the grid", "gemini": "Proposes bundles of upgrades from the weak points found by simulation.",
     "check": "The engine re-scans every site with the bundle added; the unlocked MW is measured, not claimed.", "fallback": "Cheapest-first ranking"},
    {"id": "cost", "name": "Cost estimate", "gemini": "Estimates each cost line from the case facts, with the assumption shown.",
     "check": "An answer far outside the formula's range is rejected, and that line repeats the formula.", "fallback": "Formula estimate"},
    {"id": "ask", "name": "Ask about this case", "gemini": "Chooses which computed facts answer a question and words the answer.",
     "check": "Only facts from the case's fact sheet may be cited; other numbers are rejected.", "fallback": "Rule-based answers"},
    {"id": "planner", "name": "Siting planner", "gemini": "Chooses each next step of a siting plan (a headroom lookup, a what-if, a fix).",
     "check": "Every step runs on the engine, and the finished plan is re-checked: no line over its limit, nobody without power.", "fallback": "Greedy planner"},
]


@router.get("/status")
def status():
    return {"configured": configured(), "model": MODEL, **usage(), "served": {k: _stats[k] for k in ("ok", "fallback", "cached")},
            "by_surface": _stats["by_surface"], "cached_answers": len(_cache), "surfaces": SURFACES}


# Example route — copy this shape for real features: auth required and a per-visitor
# rate limit (the shared daily cap is inside complete()). This one has no fallback on
# purpose, so an unconfigured key is a visible 503; demo-path routes pass fallback=.
@router.post("/ask")
@limiter.limit("30/minute")
async def ask(request: Request, body: AskRequest, user: User = Depends(get_current_user)):
    return {"text": await complete(body.prompt)}
