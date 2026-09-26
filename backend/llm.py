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
from pathlib import Path
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
CACHE_MAX = 1024
CACHE_TTL_S = 48 * 3600  # long enough for answers pre-run on Saturday to serve Sunday's judging
# The cache also lives on disk (gitignored), so a restart doesn't throw away answers already paid for in quota,
# and scripts/prewarm_ai.py can fill it for the demo's cases ahead of time. AI_CACHE_FILE= (empty) turns it off.
CACHE_FILE = os.getenv("AI_CACHE_FILE", str(Path(__file__).parent / ".ai_cache.json"))


def _note(surface: str | None, outcome: str) -> None:
    if outcome in _stats:
        _stats[outcome] += 1
    if surface:
        row = _stats["by_surface"].setdefault(surface, {"ok": 0, "fallback": 0, "cached": 0})
        row[outcome] = row.get(outcome, 0) + 1


def _cache_key(prompt: str, system: str | None, json_mode: bool, schema: dict | None, model: str | None = None) -> str:
    raw = json.dumps([model or MODEL, system or "", prompt, json_mode, schema], sort_keys=True, default=str)
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
    _save_disk()


def _save_disk() -> None:
    if not CACHE_FILE:
        return
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: [ts, text] for k, (ts, text) in _cache.items()}, f)
        os.replace(tmp, CACHE_FILE)
    except OSError as e:  # a read-only disk just means no persistence
        logging.getLogger("uvicorn.error").warning("AI cache not saved: %s", e)


def _load_disk() -> None:
    if not CACHE_FILE:
        return
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return
    now = time.time()
    for k, v in sorted(raw.items(), key=lambda kv: kv[1][0] if isinstance(kv[1], list) and kv[1] else 0):
        if isinstance(v, list) and len(v) == 2 and isinstance(v[1], str) and now - float(v[0]) <= CACHE_TTL_S:
            _cache[k] = (float(v[0]), v[1])
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


_load_disk()


def usage() -> dict:
    """Today's whole-app AI budget (the number the AI panel shows)."""
    today = datetime.now(QUOTA_TZ).date().isoformat()
    used = _daily["used"] if _daily["day"] == today else 0
    return {"day": today, "used_today": used, "cap": AI_DAILY_CAP, "remaining": max(0, AI_DAILY_CAP - used)}


# Flash-Lite: the free tier allows ~500 requests/day on Lite models vs ~20/day on
# full Flash (as of Sept 2026) — a demo needs the 500. Override with GEMINI_MODEL.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
# The model for agent steps (the analyst's tool choices, the proposer's plans): the same by default; set
# GEMINI_AGENT_MODEL to a stronger model when its quota allows. Passed per call as complete_json(..., model=AGENT_MODEL).
AGENT_MODEL = os.getenv("GEMINI_AGENT_MODEL", MODEL)
# Agent steps run with thinking at its minimum (complete_json(..., thinking=AGENT_THINKING)): measured Sat 10:05 on the
# proposer and analyst prompts, gemini-3.5-flash with its default thinking took 17-24 s (1,300-2,900 thought tokens),
# past the 10 s / 18 s budgets, so every demo case fell back; at "minimal" it answered in 2-6.5 s. "minimal" is the
# one setting all four models in the chain accept (thinkingBudget 0 is a 400 on gemini-3.5-flash-lite). Set
# GEMINI_AGENT_THINKING= (empty) to let the agent model think, when its latency allows.
AGENT_THINKING = os.getenv("GEMINI_AGENT_THINKING", "minimal").strip() or None
TIMEOUT_SECONDS = 60
# Models tried in order when a model's free quota is used up for the day (HTTP 429); each has its own quota.
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-3.5-flash,gemini-3.6-flash").split(",") if m.strip()]
_out_today: dict[str, str] = {}  # model -> the quota day its daily quota ran out
_out_until: dict[str, float] = {}  # model -> when a per-minute limit (a burst) clears
BURST_OUT_S = 60.0


def _model_out(m: str) -> bool:
    return _out_today.get(m) == datetime.now(QUOTA_TZ).date().isoformat() or _out_until.get(m, 0.0) > time.time()


def _mark_out(m: str, detail: str = "") -> None:
    """A 429: out for the rest of the quota day only when Google says the daily quota is gone ("PerDay" in its
    quotaId); a per-minute burst limit clears in a minute."""
    if "PerDay" in detail:
        _out_today[m] = datetime.now(QUOTA_TZ).date().isoformat()
    else:
        _out_until[m] = time.time() + BURST_OUT_S

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
    cache: bool = True,
    surface: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
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
    `cache` (on by default): serve an identical earlier answer (same model, system and prompt) from memory
    and disk for 48 hours instead of spending quota again; every prompt here is built from a computed fact
    sheet, and a call with an image is never cached. Pass cache=False for a call that must be fresh. `surface`: a short label for the AI panel's per-feature counters. `model`: this call's model
    (default MODEL; agent steps pass AGENT_MODEL). `thinking`: the model's thinking level ("minimal", "low", ...;
    None = the model's default); agent steps pass AGENT_THINKING."""
    key = _cache_key(prompt, system, json_mode, None, model) if cache and image is None else None
    if key:
        hit = _cache_get(key)
        if hit is not None:
            _note(surface, "cached")
            return hit
    try:
        text = await _complete(prompt, system, json_mode, image, timeout, model=model, thinking=thinking)
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
    cache: bool = True,
    surface: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
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
    `cache`, `surface`, `model` and `thinking`: as in `complete`."""
    log = logging.getLogger("uvicorn.error")
    key = _cache_key(prompt, system, True, schema, model) if cache and image is None else None
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
            text = await _complete(prompt + nudge, system, True, image, timeout, use_schema, model=model, thinking=thinking)
        except HTTPException as e:
            err = e
        if err is not None and use_schema is not None and err.status_code == 502 and "(400)" in str(err.detail):
            use_schema = None  # Google refused the schema itself: ask again in plain JSON mode
            err = None
            try:
                text = await _complete(prompt + nudge, system, True, image, timeout, None, model=model, thinking=thinking)
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
    model: str | None = None,
    thinking: str | None = None,
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
    if thinking:
        body.setdefault("generationConfig", {})["thinkingConfig"] = {"thinkingLevel": thinking}

    # Each Gemini model has its own free-tier quota: when this call's model answers 429 (quota used up for
    # the day), the same request goes to the next model in FALLBACK_MODELS, skipping models already known
    # to be out today, so one exhausted model doesn't turn every AI feature into its plain version.
    chain = [model or MODEL] + [m for m in FALLBACK_MODELS if m != (model or MODEL)]
    data = None
    for i, m in enumerate(chain):
        if _model_out(m) and i < len(chain) - 1:
            continue
        url = f"{API_BASE}/models/{m}:generateContent"
        try:
            data = await asyncio.to_thread(_post_json, url, body, key, timeout or TIMEOUT_SECONDS)
            _stats["model_used"] = m
            break
        except urllib.error.HTTPError as e:
            full = e.read().decode(errors="replace")  # whole: a 429's quotaId ("...PerDay...") sits past the first lines
            detail = full[:300]
            if e.code == 400 and (body.get("generationConfig") or {}).pop("thinkingConfig", None) is not None:
                # a model that doesn't take this thinking level (400): the same model again at its default thinking
                logging.getLogger("uvicorn.error").warning("AI: %s refused thinkingLevel=%s, retrying at its default", m, thinking)
                try:
                    data = await asyncio.to_thread(_post_json, url, body, key, timeout or TIMEOUT_SECONDS)
                    _stats["model_used"] = m
                    break
                except urllib.error.HTTPError as e2:
                    full = e2.read().decode(errors="replace")
                    e, detail = e2, full[:300]
                except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e2:
                    raise HTTPException(status_code=502, detail=f"AI request failed: {e2}")
            if e.code == 429 and i < len(chain) - 1:
                _mark_out(m, full)
                logging.getLogger("uvicorn.error").warning("AI: %s is out of quota, trying %s", m, chain[i + 1])
                continue
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
    {"id": "analyst", "name": "What it would take (AI analyst)",
     "gemini": "An agent with tools: it chooses which engine runs to make for a proposal (a size what-if, the nearby substations, nearby sites that take it, the verified ways to build it, firm against flexible service, the time of day), reads each result, then writes a short memo.",
     "check": "Every tool call runs on the power-flow engine; every number in the memo must match a tool result, or the plain memo built from the same results is used.",
     "fallback": "Fixed tool plan and a template memo"},
    {"id": "agreement", "name": "Build together",
     "gemini": "Drafts a coordination proposal for two utilities' overlapping planned projects from their public filings.",
     "check": "Every number in the draft must match the filings and the overlap pipeline, or the template draft is used.",
     "fallback": "Template draft"},
    {"id": "negotiation", "name": "Build together: two agents negotiate",
     "gemini": "Two agents, each reading one utility's public filing only, trade proposals for a joint build window, the shared scope and the cost split until one accepts the other's terms.",
     "check": "Every turn is verified before the other agent sees it: the window must sit inside both filed build windows and after today, the scope must be the estimate's items, the split one of the allowed rules adding to 100 %, every number from the facts; a rejected turn is revised once.",
     "fallback": "A scripted negotiation over the same rules, labeled plain"},
]


@router.get("/status")
def status():
    return {"configured": configured(), "model": MODEL, "agent_model": AGENT_MODEL, "agent_thinking": AGENT_THINKING, "fallback_models": FALLBACK_MODELS, "models_out_today": sorted(m for m in _out_today if _model_out(m)),
            "model_last_used": _stats.get("model_used"), **usage(), "served": {k: _stats[k] for k in ("ok", "fallback", "cached")},
            "by_surface": _stats["by_surface"], "cached_answers": len(_cache), "surfaces": SURFACES}


# Example route — copy this shape for real features: auth required and a per-visitor
# rate limit (the shared daily cap is inside complete()). This one has no fallback on
# purpose, so an unconfigured key is a visible 503; demo-path routes pass fallback=.
@router.post("/ask")
@limiter.limit("30/minute")
async def ask(request: Request, body: AskRequest, user: User = Depends(get_current_user)):
    return {"text": await complete(body.prompt)}
