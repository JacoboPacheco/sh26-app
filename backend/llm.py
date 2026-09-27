"""
LLM helper (Gemini, free tier). Usage from any router:

    from llm import complete
    text = await complete("Summarize this: ...", system="You are terse.")
    data = await complete(prompt, json_mode=True)   # returns a JSON string
    text = await complete(prompt, fallback="(AI offline) Here's an example answer…")  # never raises
    text = await complete("List the questions on this whiteboard", image=(png_bytes, "image/png"))
    data, offline = await complete_json(prompt, fallback=NO_ANSWER, timeout=10)  # parsed JSON
    reply, offline = await complete_tools(contents, DECLS, fallback=OFF, timeout=10, tool_mode="AUTO")  # native
        # function calling: reply["calls"] = [{id, name, args}]; append reply["content"] verbatim, then a user Content
        # of function_response(call, result) parts, and call again (see complete_tools)

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
import re
import sys
import threading
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


# ------------------------------------------------------------------ the verification ledger
# Every verifier that decides on something Gemini proposed (a memo's numbers, a function call's arguments, an
# upgrade plan the engine re-ran, a negotiation turn, a draft sentence) calls note_check(surface, ok, reason,
# excerpt): per surface, how many proposals were checked, accepted and rejected, plus the last CATCHES_MAX
# rejections (the surface, when, the offending token only, never the AI's text, and the checker's reason).
# A cached answer served again is checked again, so the counts are checks run, not distinct answers.
# Persisted next to the answer cache (AI_CACHE_FILE's folder, gitignored; off when AI_CACHE_FILE is empty), so a
# restart doesn't show 0/0; "since" is when the first check was recorded. Only the serving process writes it: the
# file is armed when the app starts (this router's startup handler), so a smoke check or a script that imports a
# verifier in-process (stubbed runs) never overwrites the server's counts.
LEDGER_FILE = os.path.join(os.path.dirname(CACHE_FILE) or ".", ".ai_ledger.json") if CACHE_FILE else ""
CATCHES_MAX = 20
TOKEN_MAX = 40  # a catch keeps at most this much of the offending token
REASON_MAX = 160
_ledger_lock = threading.Lock()
_ledger: dict = {"since": None, "updated": None, "by_surface": {}, "catches": []}
_ledger_warned = False
_ledger_armed = False  # set at app startup (arm_ledger); until then note_check counts in memory only
_ledger_saved: bool | None = None  # the last save: True written, False failed (a missing folder, a read-only disk), None not tried yet

# The name filter on catches: the same rule analyst.py uses to keep names out of a memo — its list of real
# utilities and grid operators (analyst._REAL_NAMES; this copy is used only when analyst.py isn't loaded) and the
# distinctive words of the catalog's project and company names minus generic words (analyst._GENERIC) — plus the
# utilities named in the GridLock filings, since a negotiation finding can quote them.
_REAL_NAMES_COPY = re.compile(
    r"\b(FPL|Florida Power|Duke Energy|TECO|Tampa Electric|JEA|ERCOT|Oncor|CenterPoint|Dominion|Georgia Power|Southern Company|PG&E|Con ?Ed(?:ison)?|Xcel|Entergy|AEP|PJM|MISO|CAISO|NYISO|TVA|NextEra)\b",
    re.I,
)
_GRIDLOCK_NAMES = re.compile(r"\b(DESC|Dominion Energy South Carolina|Georgia Transmission(?: Corporation)?|GTC|MEAG(?: Power)?|Dalton Utilities|Oglethorpe(?: Power)?|Santee Cooper)\b", re.I)
_GENERIC_COPY = set(
    "data center centers campus park project technology tech holdings compute computing solutions energy developer applicant partner infrastructure "
    "tenant undisclosed disclosed user places international airport county near town outside unincorporated the and with end not llc inc corp "
    "company group ventures capital partners properties development realty".split()
)
# everyday words that sit in some catalog names and in the checkers' own reasons: never treated as a name
_EVERYDAY = set(
    "power grid line lines plan plans cost costs number numbers fact facts site sites phase first north south east west new global national "
    "american america united digital cloud electric electricity utility utilities solar nuclear wind natural storage battery system systems "
    "university state states city river valley lake creek mountain point hill hills ridge field fields farm gate gateway south-east "
    "memo draft window share shares split scope rule people month months year years build "
    "january february march april june july august september october november december "
    "alabama arizona arkansas california colorado connecticut delaware florida georgia idaho illinois indiana iowa kansas kentucky "
    "louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska nevada hampshire jersey mexico "
    "york carolina dakota ohio oklahoma oregon pennsylvania rhode island tennessee texas utah vermont virginia washington wisconsin wyoming".split()
)
# catalog name words that are also ordinary English: a name only when capitalized, even in strict mode (a token's
# lowercase "connect", "core" or "stream" is a word, not a company)
_COMMON_NAME_WORDS = set(
    "aligned americas applied assets blue chapel citadel clean companies compass connect constructors core cumulus datacenters diode "
    "estate expansion fleet forge frontier galaxy golden greenfield horizon hyperscale intersect investment iron jade justified keystone "
    "lambda leap lighthouse loophole machine management mariner matador mega mining nest parks penguin plains platform platforms polaris "
    "prime real redevelopment related sail scientific services stack stream supernova switch tract vantage".split()
)
# three-letter words in catalog names that aren't company names (a chip, a legal form, a roman numeral)
_SHORT_GENERIC = {"gpu", "tpu", "cpu", "hpc", "llc", "inc", "ltd", "loi", "usa", "vii", "iii", "ceo"}
_name_words: set[str] | None = None
_short_names: set[str] | None = None  # three-letter company names as the catalog writes them (QTS, AWS, PBA, xAI)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9&'-]{3,}")
_SHORT = re.compile(r"(?<![A-Za-z0-9&'-])[A-Za-z]{3}(?![A-Za-z0-9&'-])")


def _names_rule() -> tuple[re.Pattern, set[str]]:
    a = sys.modules.get("analyst")
    return getattr(a, "_REAL_NAMES", _REAL_NAMES_COPY), getattr(a, "_GENERIC", _GENERIC_COPY)


def _catalog_name_words() -> set[str]:
    """Distinctive words of every catalog project and company name (lowercase), loaded once; also fills
    _short_names (three-letter names with at least two capitals, as written)."""
    global _name_words, _short_names
    if _name_words is None:
        words: set[str] = set()
        places: set[str] = set()
        short: set[str] = set()
        for fname in ("datacenters_us.json", "datacenters_epoch.json"):
            try:
                with open(Path(__file__).parent / "demo" / fname, encoding="utf-8") as f:
                    entries = json.load(f).get("entries") or []
            except (OSError, ValueError, AttributeError):
                continue
            for e in entries:
                if not isinstance(e, dict):
                    continue
                for k in ("name", "company", "operator"):  # proper nouns only (a note like "(developer; tenant unannounced)" isn't a name)
                    words |= {w.lower() for w in _WORD.findall(str(e.get(k) or "")) if w[:1].isupper()}
                    short |= {w for w in _SHORT.findall(str(e.get(k) or "")) if sum(c.isupper() for c in w) >= 2 and w.lower() not in _SHORT_GENERIC}
                for k in ("city", "county", "state_name"):
                    places |= {w.lower() for w in _WORD.findall(str(e.get(k) or ""))}
        generic = _names_rule()[1]
        _short_names = short
        _name_words = {w for w in words if w not in generic and w not in places and w not in _EVERYDAY}
    return _name_words


def scrub_names(text: str, strict: bool = True) -> str:
    """`text` with real company, utility and project names replaced by "[name]". strict (for a token): a distinctive
    catalog name word whatever its case, an ordinary-English one (_COMMON_NAME_WORDS) only capitalized; not strict
    (for a checker's own sentence): only capitalized catalog name words. A word joined to an identifier by "_"
    (a tool or argument name like connect_site) is code, never a name. Three-letter names (QTS, AWS, xAI) as the
    catalog writes them; in strict mode whatever their case."""
    real, _ = _names_rule()
    out = _GRIDLOCK_NAMES.sub("[name]", real.sub("[name]", text or ""))
    words = _catalog_name_words()
    short = _short_names or set()
    short_lower = {s.lower() for s in short}

    def in_identifier(m: re.Match) -> bool:
        s = m.string
        return s[m.start() - 1 : m.start()] == "_" or s[m.end() : m.end() + 1] == "_"

    def swap(m: re.Match) -> str:
        w = m.group(0)
        lw = w.lower()
        if lw not in words or in_identifier(m):
            return w
        if not w[:1].isupper() and (not strict or lw in _COMMON_NAME_WORDS):
            return w
        return "[name]"

    def swap_short(m: re.Match) -> str:
        w = m.group(0)
        hit = (w.lower() in short_lower if strict else w in short) and not in_identifier(m)
        return "[name]" if hit else w

    return _SHORT.sub(swap_short, _WORD.sub(swap, out))


def _clip(text, n: int) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _ledger_save() -> None:
    """Write the ledger (atomically: a temp file, then a rename). Called with _ledger_lock held."""
    global _ledger_warned, _ledger_saved
    if not LEDGER_FILE or not _ledger_armed:
        return
    try:
        tmp = f"{LEDGER_FILE}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_ledger, f)
        os.replace(tmp, LEDGER_FILE)
        _ledger_saved = True
    except OSError as e:  # a read-only or busy disk just means no persistence
        _ledger_saved = False  # the card stops saying "kept across restarts"
        if not _ledger_warned:
            _ledger_warned = True
            logging.getLogger("uvicorn.error").warning("AI ledger not saved: %s", e)


def _ledger_load() -> None:
    if not LEDGER_FILE:
        return
    try:
        with open(LEDGER_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return

    def count(v) -> int:
        try:
            return max(0, int(v or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    by = raw.get("by_surface")
    rows = {}
    for s, r in (by.items() if isinstance(by, dict) else ()):
        if isinstance(s, str) and isinstance(r, dict):
            rows[s[:40]] = {k: count(r.get(k)) for k in ("proposed", "verified", "rejected")}
    listed = raw.get("catches")
    catches = []
    for c in (listed if isinstance(listed, list) else [])[-CATCHES_MAX:]:
        if isinstance(c, dict) and isinstance(c.get("surface"), str):
            catches.append({"surface": c["surface"][:40], "at": c.get("at") if isinstance(c.get("at"), str) else None,
                            "token": c.get("token") if isinstance(c.get("token"), str) else None,
                            "reason": c.get("reason") if isinstance(c.get("reason"), str) else "rejected by the checker", "times": max(1, count(c.get("times")))})
    _ledger.update(since=raw.get("since") if isinstance(raw.get("since"), str) else None,
                   updated=raw.get("updated") if isinstance(raw.get("updated"), str) else None, by_surface=rows, catches=catches)


_ledger_load()


def note_check(surface: str, ok: bool, reason: str, excerpt=None) -> None:
    """Record one verifier decision on something Gemini proposed: `surface` (a SURFACES id), `ok` (accepted or
    rejected), `reason` (the checker's own words, short: "a memo number no tool returned"), `excerpt` (the offending
    token only — a number, a word — never the AI's whole text; clipped to TOKEN_MAX, names filtered). Never raises:
    a ledger problem must not change what the verifier decided."""
    try:
        now = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        surface = _clip(surface or "unknown", 40)
        with _ledger_lock:
            row = _ledger["by_surface"].setdefault(surface, {"proposed": 0, "verified": 0, "rejected": 0})
            row["proposed"] += 1
            row["verified" if ok else "rejected"] += 1
            _ledger["since"] = _ledger["since"] or now
            _ledger["updated"] = now
            if not ok:
                token = scrub_names(_clip(excerpt, TOKEN_MAX)) if excerpt not in (None, "") else None
                why = scrub_names(_clip(reason or "rejected by the checker", REASON_MAX), strict=False)
                # the same catch again (a cached answer re-checked): one entry, moved to the end, with how many times
                same = [c for c in _ledger["catches"] if c.get("surface") == surface and c.get("reason") == why and c.get("token") == token]
                times = sum(int(c.get("times") or 1) for c in same) + 1
                _ledger["catches"] = [c for c in _ledger["catches"] if c not in same]
                _ledger["catches"].append({"surface": surface, "at": now, "token": token, "reason": why, "times": times})
                del _ledger["catches"][:-CATCHES_MAX]
            _ledger_save()
    except Exception as e:  # noqa: BLE001 — the verifier's decision stands whatever happens here
        logging.getLogger("uvicorn.error").warning("AI ledger: note_check failed: %s", e)


def ledger() -> dict:
    """The ledger as GET /api/ai/status shows it: per-surface counts, totals, and the catches newest first."""
    with _ledger_lock:
        rows = {s: dict(r) for s, r in _ledger["by_surface"].items()}
        catches = [dict(c) for c in reversed(_ledger["catches"])]
        since, updated = _ledger["since"], _ledger["updated"]
    totals = {k: sum(r[k] for r in rows.values()) for k in ("proposed", "verified", "rejected")}
    persisted = bool(LEDGER_FILE and _ledger_armed and _ledger_saved is not False)
    return {"since": since, "updated": updated, "persisted": persisted, "by_surface": rows, "totals": totals, "catches": catches}


def arm_ledger() -> None:
    """The serving process from here on writes the ledger to disk (and writes now what was counted before startup,
    e.g. by a warm-up). Called by this router's startup handler; tests arm it by hand."""
    global _ledger_armed
    with _ledger_lock:
        _ledger_armed = True
        if _ledger["since"]:
            _ledger_save()


# Milestone 0, every state: our DC power flow against the dataset's own solved flows (backend/demo/validate.py
# --all writes the committed file; nothing is computed at runtime).
VALIDATION_FILE = Path(__file__).parent / "demo" / "validation.json"
try:
    _validation = json.loads(VALIDATION_FILE.read_text(encoding="utf-8"))
except (OSError, ValueError):
    _validation = None


def validation_summary() -> dict | None:
    """What the card needs: Florida's row, the all-states summary, the method and the limits."""
    v = _validation
    if not isinstance(v, dict):
        return None
    fl = (v.get("states") or {}).get("FL")
    return {"generated": v.get("generated"), "method": v.get("method"), "reference": v.get("reference"), "limits": v.get("limits"),
            "threshold": v.get("threshold"), "summary": v.get("summary"), "florida": fl}


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
router.add_event_handler("startup", arm_ledger)  # app.include_router(llm.router) carries it to the app


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


# ------------------------------------------------------------------ native function calling (Gemini tools)
# Built against Google's generateContent docs (fetched Sat 2026-09-26): ai.google.dev/gemini-api/docs/generate-content/
# function-calling and .../thought-signatures. The rules this follows:
#   - tools = [{"functionDeclarations": [{name, description, parameters (OpenAPI-style schema, optional)}]}];
#     toolConfig.functionCallingConfig.mode is AUTO (call or answer), ANY (must call), NONE (must answer) or VALIDATED.
#   - the model's reply is a Content {role: "model", parts}: functionCall parts {id, name, args}, text parts; several
#     functionCall parts in one reply are parallel calls. Gemini 3 puts a thoughtSignature on the FIRST functionCall part
#     of each step (and may put one on the last text part).
#   - the next request echoes the model's Content verbatim (every part, every thoughtSignature, same order, never merged
#     or split), then ONE user Content with one functionResponse {id, name, response} per functionCall, all of them
#     after all the calls. A missing signature on a function call of the current turn is a 400.
#   - history sent to another model (the 429 fallback chain) carries the documented dummy signature instead of the
#     one the first model wrote: the docs' way to hand over history whose signatures another model can't vouch for
#     (measured Sat: a real 3.5-flash-lite signature was also accepted by 3.1-flash-lite, so this is the documented
#     safe choice rather than a proven necessity).
#   - VALIDATED (tools next to structured output) is retried once on AUTO and without the generation config when a
#     model refuses it (400); a reply that ends MALFORMED_FUNCTION_CALL or UNEXPECTED_TOOL_CALL is asked once more.
#   - Gemini 3.x: leave temperature at its default.
DUMMY_SIGNATURE = "skip_thought_signature_validator"
RETRY_FINISH = ("MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL")


def cache_forget(keys) -> int:
    """Drop these answer-cache entries (memory and disk): the turns of an agent run whose result was rejected, so a
    retry asks the model afresh instead of replaying the same failing conversation. Returns how many were dropped."""
    n = sum(1 for k in set(keys or []) if k and _cache.pop(k, None) is not None)
    if n:
        _save_disk()
    return n


def _tools_cache_key(contents, tools, system, tool_mode, allowed, schema, model, thinking) -> str:
    raw = json.dumps(["tools-v1", model or AGENT_MODEL, system or "", contents, tools, tool_mode, allowed, schema, thinking], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _with_dummy_signatures(contents: list[dict]) -> list[dict]:
    out = copy.deepcopy(contents)
    for c in out:
        for p in c.get("parts") or []:
            if isinstance(p, dict) and ("thoughtSignature" in p or "thought_signature" in p):
                p.pop("thought_signature", None)
                p["thoughtSignature"] = DUMMY_SIGNATURE
    return out


def _parse_tool_reply(content: dict) -> tuple[list[dict], str]:
    """(function calls [{id, name, args}], the reply's text without thought summaries)."""
    calls, text = [], []
    for p in content.get("parts") or []:
        if not isinstance(p, dict):
            continue
        fc = p.get("functionCall")
        if isinstance(fc, dict) and fc.get("name"):
            calls.append({"id": fc.get("id"), "name": str(fc["name"]), "args": fc.get("args") if isinstance(fc.get("args"), dict) else {}})
        elif isinstance(p.get("text"), str) and not p.get("thought"):
            text.append(p["text"])
    return calls, "".join(text).strip()


def function_response(call: dict, response: dict) -> dict:
    """The part that answers one function call: {"functionResponse": {"id", "name", "response"}} (the id Gemini 3 gave
    the call, so parallel results map back to their calls)."""
    fr = {"name": call["name"], "response": response if isinstance(response, dict) else {"output": response}}
    if call.get("id"):
        fr["id"] = call["id"]
    return {"functionResponse": fr}


async def complete_tools(
    contents: list[dict],
    tools: list[dict],
    system: str | None = None,
    fallback: dict | None = None,
    timeout: float | None = None,
    surface: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    tool_mode: str = "AUTO",
    allowed: list[str] | None = None,
    schema: dict | None = None,
    cache: bool = True,
) -> tuple[dict, bool]:
    """One turn of a Gemini function-calling conversation. Returns `(reply, used_fallback)`:

        reply = {"content": <the model's Content, verbatim: append it to `contents` as is>,
                 "calls": [{"id", "name", "args"}, ...],   # the function calls (parallel when several)
                 "text": "...",                             # its text parts joined (no thought summaries)
                 "model": "<the model that answered>",      # pass it as model= on the next turn
                 "cache_key": "<this turn's answer-cache key>" | None}  # cache_forget([...]) drops a failed run's turns

    `contents`: the whole conversation so far (user text, then model Contents echoed verbatim and user Contents of
    functionResponse parts; build those with `function_response(call, result)`). `tools`: the functionDeclarations
    ([{name, description, parameters?}]). `tool_mode`: "AUTO" | "ANY" | "NONE" | "VALIDATED"; `allowed`: the
    functions ANY may call. `schema`: a JSON Schema for a text answer (structured output; Gemini 3 accepts it next to
    function declarations): a 400 over the schema or the thinking level is retried once without both.
    `fallback`: returned (a copy, used_fallback=True) instead of raising when the key, the day's cap
    (AI_DAILY_LIMIT), the network or the timeout fails. `cache` (on): the same conversation, tools and settings
    replay the stored reply (memory and disk, 48 h), so a replayed agent run is deterministic call by call.
    `timeout`, `surface`, `model`, `thinking`: as in `complete`; a 429 moves to the next model in the chain, with the
    history's thought signatures swapped for the documented dummy one. A model that refuses VALIDATED (400) is asked
    once more on AUTO without the generation config; a MALFORMED_FUNCTION_CALL reply is asked once more."""
    log = logging.getLogger("uvicorn.error")
    mode = (tool_mode or "AUTO").upper()
    key = _tools_cache_key(contents, tools, system, mode, allowed, schema, model, thinking) if cache else None
    if key:
        hit = _cache_get(key)
        if hit is not None:
            try:
                stored = json.loads(hit)
                content = stored["content"]
                calls, text = _parse_tool_reply(content)
                _note(surface, "cached")
                return {"content": content, "calls": calls, "text": text, "model": stored.get("model") or model or AGENT_MODEL, "cached": True, "cache_key": key}, False
            except (ValueError, KeyError, TypeError):
                _cache.pop(key, None)
    try:
        content, used = await _generate_tools(contents, tools, system, timeout, mode, allowed, schema, model, thinking)
    except HTTPException as e:
        _stats["last_error"] = str(e.detail)[:200]
        if fallback is not None:
            _note(surface, "fallback")
            log.warning("AI fallback used (function calling): %s", e.detail)
            return copy.deepcopy(fallback), True
        raise
    calls, text = _parse_tool_reply(content)
    _note(surface, "ok")
    if surface and calls:
        row = _stats["by_surface"].setdefault(surface, {"ok": 0, "fallback": 0, "cached": 0})
        row["function_calls"] = row.get("function_calls", 0) + len(calls)
    if key:
        _cache_put(key, json.dumps({"content": content, "model": used}))
    return {"content": content, "calls": calls, "text": text, "model": used, "cached": False, "cache_key": key}, False


async def _generate_tools(contents, tools, system, timeout, mode, allowed, schema, model, thinking) -> tuple[dict, str]:
    """POST generateContent with the tools; (the model's Content, the model that answered). Raises HTTPException."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="AI is not configured: add GEMINI_API_KEY to backend/.env (free key at aistudio.google.com/apikey)")
    if not _take_daily_slot():
        raise HTTPException(status_code=429, detail=AI_QUOTA_MESSAGE)
    body: dict = {"contents": contents, "tools": [{"functionDeclarations": tools}]}
    fcc: dict = {"mode": mode}
    if allowed and mode in ("ANY", "VALIDATED"):
        fcc["allowedFunctionNames"] = list(allowed)
    body["toolConfig"] = {"functionCallingConfig": fcc}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    gen: dict = {}
    if schema is not None:
        gen["responseMimeType"] = "application/json"
        gen["responseJsonSchema"] = schema
    if thinking:
        gen["thinkingConfig"] = {"thinkingLevel": thinking}
    if gen:
        body["generationConfig"] = gen
    first = model or AGENT_MODEL
    chain = [first] + [m for m in FALLBACK_MODELS if m != first]
    log = logging.getLogger("uvicorn.error")
    for i, m in enumerate(chain):
        if _model_out(m) and i < len(chain) - 1:
            continue
        # the history's signatures were written by `first`: another model gets the dummy one
        send = body if m == first else {**body, "contents": _with_dummy_signatures(contents)}
        url = f"{API_BASE}/models/{m}:generateContent"
        data = None
        plainer = asked_again = False
        while True:
            try:
                got = await asyncio.to_thread(_post_json, url, send, key, timeout or TIMEOUT_SECONDS)
            except urllib.error.HTTPError as e:
                full = e.read().decode(errors="replace")
                is_validated = send["toolConfig"]["functionCallingConfig"].get("mode") == "VALIDATED"
                if e.code == 400 and not plainer and (send.get("generationConfig") or is_validated) and "thought_signature" not in full:
                    # a schema, thinking level or VALIDATED mode this model refuses next to tools: once more on AUTO without them
                    log.warning("AI (function calling): %s refused the generation config or mode, retrying plainer: %s", m, full[:160])
                    plainer = True
                    send = {k: v for k, v in send.items() if k != "generationConfig"}
                    if is_validated:
                        send["toolConfig"] = {"functionCallingConfig": {**send["toolConfig"]["functionCallingConfig"], "mode": "AUTO"}}
                    continue
                if e.code == 429 and i < len(chain) - 1:
                    _mark_out(m, full)
                    log.warning("AI: %s is out of quota, trying %s", m, chain[i + 1])
                    break
                raise HTTPException(status_code=502, detail=f"AI request failed ({e.code}): {full[:300]}")
            except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
                raise HTTPException(status_code=502, detail=f"AI request failed: {e}")
            finish = ((got.get("candidates") or [{}])[0] or {}).get("finishReason") if isinstance(got, dict) else None
            if finish in RETRY_FINISH and not asked_again:
                # a function call the model got wrong (bad JSON or a call outside the declarations): ask once more
                log.warning("AI (function calling): %s ended %s, asking once more", m, finish)
                asked_again = True
                continue
            data = got
            break
        if data is None:
            continue  # 429: the next model in the chain
        _stats["model_used"] = m
        try:
            cand = data["candidates"][0]
            content = cand.get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise HTTPException(status_code=502, detail="AI returned no content (empty or blocked response)")
        if content is None:
            raise HTTPException(status_code=502, detail=f"AI returned no content ({cand.get('finishReason') or 'empty or blocked response'})")
        if not isinstance(content, dict) or not content.get("parts"):
            raise HTTPException(status_code=502, detail="AI returned an empty reply")
        content.setdefault("role", "model")
        return content, m
    raise HTTPException(status_code=502, detail="AI request failed: every model in the chain is out of quota")


# ------------------------------------------------------------------ Grounding with Google Search
# Built against Google's docs (fetched Sat 2026-09-26: ai.google.dev/gemini-api/docs/google-search and the Gemini API
# terms, "Grounding with Google Search") and a measured call the same evening (gemini-3.5-flash-lite, ~2 s for three
# hospitals): the request carries tools = [{"google_search": {}}]; the candidate's groundingMetadata holds
# webSearchQueries, groundingChunks [{web: {uri: a vertexaisearch.cloud.google.com redirect to the page, title: its
# domain}}], groundingSupports [{segment: {startIndex, endIndex (UTF-8 bytes of the ANSWER's text; startIndex is left
# out when 0), text}, groundingChunkIndices}] and searchEntryPoint.renderedContent (the Search Suggestions chip, HTML).
# The terms: grounded results are shown together with their Search Suggestions and are not cached, so this helper
# never reads or writes the answer cache. Google bills each search query a Gemini 3 model runs.
GROUNDED_EMPTY = {"text": "", "queries": [], "sources": [], "supports": [], "suggestions_html": None, "model": None}


def _byte_to_char(text: str):
    """A function: a UTF-8 byte offset of `text` -> its character offset (the segments count bytes)."""
    raw = text.encode("utf-8")
    if len(raw) == len(text):
        return lambda b: max(0, min(int(b), len(text)))
    return lambda b: len(raw[: max(0, min(int(b), len(raw)))].decode("utf-8", "ignore"))


def _parse_grounded(data: dict, model: str) -> dict:
    try:
        cand = data["candidates"][0]
    except (KeyError, IndexError, TypeError):
        raise HTTPException(status_code=502, detail="AI returned no candidate (empty or blocked response)")
    parts = ((cand.get("content") or {}).get("parts") or []) if isinstance(cand, dict) else []
    text = "".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought"))
    if not text.strip():
        raise HTTPException(status_code=502, detail=f"AI returned no text ({cand.get('finishReason') or 'empty response'})")
    gm = cand.get("groundingMetadata") if isinstance(cand.get("groundingMetadata"), dict) else {}
    sources = []
    for ch in gm.get("groundingChunks") or []:
        web = ch.get("web") if isinstance(ch, dict) else None
        web = web if isinstance(web, dict) else {}
        sources.append({"title": str(web.get("title") or ""), "uri": str(web.get("uri") or "")})
    to_char = _byte_to_char(text)
    supports = []
    for s in gm.get("groundingSupports") or []:
        seg = s.get("segment") if isinstance(s, dict) else None
        if not isinstance(seg, dict):
            continue
        idx = [int(i) for i in (s.get("groundingChunkIndices") or []) if isinstance(i, int) and 0 <= i < len(sources)]
        start, end = to_char(seg.get("startIndex") or 0), to_char(seg.get("endIndex") or 0)
        seg_text = str(seg.get("text") or "")
        if seg_text and text[start:end] != seg_text:  # offsets that don't match the text: place it by its text instead
            at = text.find(seg_text)
            start, end = (at, at + len(seg_text)) if at >= 0 else (start, end)
        supports.append({"start": start, "end": end, "text": seg_text, "sources": idx})
    entry = gm.get("searchEntryPoint") if isinstance(gm.get("searchEntryPoint"), dict) else {}
    html = entry.get("renderedContent")
    return {"text": text, "queries": [str(q) for q in gm.get("webSearchQueries") or [] if isinstance(q, str)], "sources": sources,
            "supports": supports, "suggestions_html": html if isinstance(html, str) and html.strip() else None, "model": model}


async def complete_grounded(
    prompt: str,
    *,
    system: str | None = None,
    surface: str | None = None,
    timeout: float | None = None,
    fallback: dict | None = None,
    model: str | None = None,
    thinking: str | None = None,
) -> tuple[dict, bool]:
    """One Gemini call with Google Search grounding. Returns `(result, used_fallback)`:

        result = {"text": the answer,
                  "queries": the searches Gemini ran,
                  "sources": [{"title": the page's domain, "uri": Google's redirect to the page}, ...],
                  "supports": [{"start", "end" (characters of text), "text", "sources": [indices into sources]}, ...],
                  "suggestions_html": the Search Suggestions chip to show beside the result (Google's terms) | None,
                  "model": the model that answered}

    A support says which web pages Google tied to that stretch of the answer; the caller checks its own numbers
    against them. `fallback`: returned (a copy, used_fallback=True) instead of raising when the key, the day's cap
    (AI_DAILY_LIMIT), the network or the timeout fails. Never cached (the grounding terms). `system`, `surface`,
    `timeout`, `model` (default AGENT_MODEL) and `thinking` as in `complete`; a 429 moves to the next model in the
    chain, like every other call here."""
    log = logging.getLogger("uvicorn.error")
    try:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise HTTPException(status_code=503, detail="AI is not configured: add GEMINI_API_KEY to backend/.env (free key at aistudio.google.com/apikey)")
        if not _take_daily_slot():
            raise HTTPException(status_code=429, detail=AI_QUOTA_MESSAGE)
        body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if thinking:
            body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": thinking}}
        first = model or AGENT_MODEL
        chain = [first] + [m for m in FALLBACK_MODELS if m != first]
        result = None
        for i, m in enumerate(chain):
            if _model_out(m) and i < len(chain) - 1:
                continue
            url = f"{API_BASE}/models/{m}:generateContent"
            send = body
            while True:
                try:
                    data = await asyncio.to_thread(_post_json, url, send, key, timeout or TIMEOUT_SECONDS)
                except urllib.error.HTTPError as e:
                    full = e.read().decode(errors="replace")
                    if e.code == 400 and send.get("generationConfig"):
                        send = {k: v for k, v in send.items() if k != "generationConfig"}  # a thinking level this model refuses
                        continue
                    if e.code in (429, 503) and i < len(chain) - 1:
                        if e.code == 429:
                            _mark_out(m, full)
                        # 503: this model is overloaded right now (measured Sat evening on gemini-3.5-flash): the next one
                        log.warning("AI (grounded): %s answered %s, trying %s", m, e.code, chain[i + 1])
                        break
                    raise HTTPException(status_code=502, detail=f"AI request failed ({e.code}): {full[:300]}")
                except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
                    raise HTTPException(status_code=502, detail=f"AI request failed: {e}")
                result = _parse_grounded(data, m)
                break
            if result is not None:
                _stats["model_used"] = m
                break
        if result is None:
            raise HTTPException(status_code=502, detail="AI request failed: every model in the chain is out of quota")
    except HTTPException as e:
        _stats["last_error"] = str(e.detail)[:200]
        if fallback is not None:
            _note(surface, "fallback")
            log.warning("AI fallback used (grounded): %s", e.detail)
            return copy.deepcopy(fallback), True
        raise
    _note(surface, "ok")
    return result, False


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
    {"id": "unlock", "name": "Strengthen the grid",
     "gemini": "Tries to beat the engine's capacity plan (one more data center at once for the same budget, or the same number for less) from the plan, each site's room and the lines that bind it, and each line's price; also proposes bundles of upgrades from the weak points found by simulation.",
     "check": "The engine re-solves Gemini's whole plan at once (every campus and upgrade), prices it itself (Gemini's own numbers are ignored), runs the full cascade and compares it with its own plan; one revision with the engine's findings; a win is shown only when the engine confirms it. Bundles: every site is re-scanned with the bundle added; the unlocked MW is measured, not claimed.",
     "fallback": "The engine's own plan and cheapest-first ranking"},
    {"id": "cost", "name": "Cost estimate", "gemini": "Estimates each cost line from the case facts, with the assumption shown.",
     "check": "An answer far outside the formula's range is rejected, and that line repeats the formula.", "fallback": "Formula estimate"},
    {"id": "ask", "name": "Ask about this case", "gemini": "Chooses which computed facts answer a question and words the answer.",
     "check": "Only facts from the case's fact sheet may be cited; other numbers are rejected.", "fallback": "Rule-based answers"},
    {"id": "planner", "name": "Siting planner", "gemini": "Chooses each next step of a siting plan (a headroom lookup, a what-if, a fix).",
     "check": "Every step runs on the engine, and the finished plan is re-checked: no line over its limit, nobody without power.", "fallback": "Greedy planner"},
    {"id": "analyst", "name": "What it would take (AI analyst)",
     "gemini": "An agent built on Gemini function calling: the engine's six tools are declared to Gemini as functions (a size what-if, the nearby substations, nearby sites that take it, the verified ways to build it, firm against flexible service, the time of day). Gemini calls the ones it needs, several at once when useful, each with a one-sentence reason shown in the trace, reads each function response, then writes a short memo as structured output.",
     "check": "Every function call is validated and run on the power-flow engine (a repeated call is not run again), and its result goes back to Gemini as the function response; every number in the memo, digits or spelled out, must match a tool result, or the plain memo built from the same results is used.",
     "fallback": "Fixed tool plan and a template memo"},
    {"id": "agreement", "name": "Build together",
     "gemini": "Drafts a coordination proposal for two utilities' overlapping planned projects from their public filings.",
     "check": "Every number in the draft must match the filings and the overlap pipeline, or the template draft is used.",
     "fallback": "Template draft"},
    {"id": "negotiation", "name": "Build together: two agents negotiate",
     "gemini": "Two agents, each reading one utility's public filing only, trade proposals for a joint build window, the shared scope and the cost split until one accepts the other's terms.",
     "check": "Every turn is verified before the other agent sees it: the window must sit inside both filed build windows and after today, the scope must be the estimate's items, the split one of the allowed rules adding to 100 %, every number from the facts; a rejected turn is revised once.",
     "fallback": "A scripted negotiation over the same rules, labeled plain"},
    {"id": "collab_plans", "name": "Build together: three agents propose ways to build together",
     "gemini": "Two agents each represent one company's published plan (its public filing) with a goal from that filing (the most its side should pay, the window its filing allows) and propose the plan kinds that serve it from a menu the pipeline built from the pair's data; a neutral coordinator merges them into 2-3 distinct plans, says what each company gains and gives up, and recommends one.",
     "check": "Every figure (money, months, outages avoided, the window) is the pipeline's, from the two filings and the sourced estimate; every number the agents write must be one of those, and no sentence may speak for a company or say 'will'; what fails is dropped and listed, and the coordinator gets the findings back once.",
     "fallback": "Template plans from the same menu, labeled"},
    {"id": "reader", "name": "Build together: a third reader of the filings",
     "gemini": "Reads each page of the two public filings as a PDF (offline, when the data is built) and fills the same fields the two parsers extract, as structured output; for each record the checks set aside, it proposes place names from that record's page.",
     "check": "Every field is compared with the parsers' readings after the pipeline's own normalizing, and every disagreement is listed with its PDF page; a proposed place name must be printed on the page and then pass the pipeline's own locate step and blocking checks. The published records come from the parsers.",
     "fallback": "The two parsers alone (the reader is advisory)"},
    {"id": "comment", "name": "Write my public comment",
     "gemini": "Writes a resident's public comment on a proposed data center, in the first person, from the proposal page's facts: their concerns, their stance, the length they will speak for (1-3 minutes), in English or Spanish, addressed to the decision body the page names.",
     "check": "Every number must be one of the page's facts (each is shown with its source); nothing may say what the real project or utility will do, name a company the sources don't, or accuse anyone; every grid result must be framed as the open, synthetic model. A failing draft goes back once with the findings.",
     "fallback": "A template comment built from the same facts, passing the same checks"},
    {"id": "strengthen_narration", "name": "Strengthen the grid: the narrated build-up",
     "gemini": "Writes what the presenter voice says as each campus goes in on the map: where it connects, what stopped it, the upgrade that lets it in and what it costs, in English and Spanish.",
     "check": "Every number in a line must be one of that step's facts from the engine's study, said the way the plan prints it (and every place and the cost must be said); the lines that fail go back to Gemini once with the reasons and are checked again, and a line that still fails is replaced by its template line.",
     "fallback": "Template lines from the same study"},
    {"id": "harden", "name": "Harden before the storm",
     "gemini": "An agent on Gemini function calling plans which power lines to harden before a hypothetical hurricane, within a budget: it lists the lines in the storm's reach, looks up who each one serves and what it costs, tests sets of lines on the engine, then submits a plan; with the engine's verdict (people still out and where, what each of its lines keeps on, the lines that would add the most, all measured) it revises, up to 3 plans.",
     "check": "Every submitted plan is re-run by the engine through the storm and the full cascade: every id must be a line the storm knocks out, and the engine's own price (public cost figures, high end) must fit the budget; Gemini's numbers are never used. A note shows only when its numbers are ones the tools returned, and every dollar figure in a note on a set of lines is the engine's own price for that exact set (a total Gemini added up wrong hides the note).",
     "fallback": "The engine's own plan: every line tested alone, then the most people kept on per dollar, re-measured at each step"},
    {"id": "show", "name": "Watch the story (the narrated map documentaries)",
     "gemini": "A director agent on Gemini function calling reads each episode's storyboard through read-only tools (get_scene, get_fact, towns_hit, line_detail, cost_breakdown, agent_turns), drafts what the presenter and the analyst say over every scene (English or Spanish), and tests its own drafts with the engine's checker (check_draft) before it answers.",
     "check": "Every line is checked before it is shown: every number (digits or spelled out) must be one of the scene's facts from the engine, and estimates are said as estimates; people hit are never called people without power; no names but the ones the facts carry and no real company, utility or storm; no claim about the real grid or the real future; the first scene names the synthetic model (Build together: the public filings, where no line speaks for a utility and each utility's filed year stays its own); no 'will', no blame, no alarm. Scenes that fail go back to Gemini once with the findings; a scene that still fails keeps its template lines. A finished narration is saved and re-checked when it is replayed.",
     "fallback": "The storyboard's template lines, built from the same facts"},
    {"id": "strengthen_race", "name": "Strengthen the grid: three planners race the engine",
     "gemini": "Three planner agents on Gemini function calling decide what needs fixing. Each has its own strategy (cheapest first, corridors, flexible-aware) and builds a plan campus by campus through read-only engine tools (sites, weak_points, try_add, raise_cost, current_plan, undo_last), then submits it. The fourth competitor is the engine's own greedy plan, cut at the knee of its cost curve.",
     "check": "A referee written in code re-solves every submitted plan at once, with every campus and upgrade and at both load levels for flexible campuses. Raises are snapped up to 50 MVA steps and capped at 5x. The engine prices every plan itself; Gemini's figures are never used. It also runs the full cascade: nothing may trip and nobody may lose power. Ranking: most campuses within the knee budget, then cheapest, then fewest upgrades. Plans within 2 % of each other share a place. An AI plan has to beat the engine's plan to win. A plan that fails is shown with the engine's reason.",
     "fallback": "The engine's plan alone, labeled with the reason"},
    {"id": "hospital_beds", "name": "Hospitals in the dark areas: beds as reported",
     "gemini": "An agent on Gemini with Grounding with Google Search looks up how many beds each hospital in the areas that lost power has, as a public page states it (licensed, else staffed, else total beds): batches of three hospitals, the first model asked, a second only when the first answered from memory without searching or is slow, up to three rounds; each figure comes with the web pages Google tied to it.",
     "check": "Code keeps a figure only when a search ran and Google tied the number to a web page that the checker reads (HTML, or a PDF) and that gives it as THIS hospital's bed count: the number next to the word bed, the hospital's own name the closest hospital name to it (no sibling from OpenStreetMap's list of the state closer, no other bed count in between, not one unit's beds such as an ICU or a crisis unit), and the page in the right state. When the page can't be read, the figure is kept only if Google tied it to that hospital's own answer line, labeled not confirmed on the page. A page that names another hospital, a same-named hospital elsewhere, or gives a different figure is a rejection; rejected and not-found hospitals go back with the reason for up to two revisions. The whole run stops at 40 seconds. Beds are never presented as a head count, and backup power is labeled an assumption of the synthetic model.",
     "fallback": "OpenStreetMap's beds tags only, labeled as map data, not checked"},
]


@router.get("/status")
def status():
    return {"configured": configured(), "model": MODEL, "agent_model": AGENT_MODEL, "agent_thinking": AGENT_THINKING, "fallback_models": FALLBACK_MODELS, "models_out_today": sorted(m for m in _out_today if _model_out(m)),
            "model_last_used": _stats.get("model_used"), **usage(), "served": {k: _stats[k] for k in ("ok", "fallback", "cached")},
            "by_surface": _stats["by_surface"], "cached_answers": len(_cache), "surfaces": SURFACES,
            "ledger": ledger(), "validation": validation_summary()}


@router.get("/validation")
def validation():
    """Milestone 0 for every state model (backend/demo/validation.json): our DC power flow against the dataset's own
    solved flows. 404 when the file isn't built."""
    if not isinstance(_validation, dict):
        raise HTTPException(status_code=404, detail="validation.json is not built: run backend/demo/validate.py --all")
    return _validation


# Example route — copy this shape for real features: auth required and a per-visitor
# rate limit (the shared daily cap is inside complete()). This one has no fallback on
# purpose, so an unconfigured key is a visible 503; demo-path routes pass fallback=.
@router.post("/ask")
@limiter.limit("30/minute")
async def ask(request: Request, body: AskRequest, user: User = Depends(get_current_user)):
    return {"text": await complete(body.prompt)}
