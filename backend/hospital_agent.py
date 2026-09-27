"""Hospital beds agent: how many beds the hospitals in the dark areas have, AS REPORTED, each with its source.

    POST /api/hospitals/agent       {region, load_factor, affected: {sub id: MW of existing load lost}} (the cascade's
                                    own `affected` and the case's load level, sent the moment the cascade lands)
                                    -> a finished answer at once when this set of hospitals was researched before
                                    ({job: null, status: done, cached: true, ...}), else a job ({job, status: running, ...})
    GET  /api/hospitals/agent/{id}  the job so far: {status, hospitals, trace, counts, by, suggestions, ...}

Which hospitals: the ones on backup power, by hospitals.status_for (the substation nearest the hospital in the
SYNTHETIC model lost at least 60 % of its load) — the same rule and the same list the briefing's hospitals beat counts.

What each hospital gets (never a head count, never a figure without its source):
  reported   beds (licensed, else staffed, else the total a page states) found by the agent that passed the checks
  osm        OpenStreetMap's `beds` tag (map data, not checked by the agent): used only when nothing passed, or without
             Gemini; the trace shows it next to the reported figure when both exist
  not_found  nothing passed and there is no OSM tag; the reason is in `note` and the trace

THE AGENT (Gemini with Grounding with Google Search, llm.complete_grounded, fallback= and a timeout on every call).
Batches of BATCH hospitals run at once; each batch loops, up to ATTEMPTS times while the clock allows:
  propose   "how many beds does <name> (<state>, the <area> area, at lat, lon) have, as a web page states it — else
            NOT FOUND", one line per hospital: number | licensed/staffed/beds | the source's site. The first model
            (MODELS) is asked; the second only when the first answered from memory without searching (measured Sat
            evening: it happens), answered nothing, or is still silent after HEDGE_S — every grounded call is billed.
  check     code, never the model. A figure is kept only when
              1. it is a whole number from 1 to MAX_BEDS, and a search ran, and
              2. a grounding support (a stretch of the answer Google tied to web pages) holds that number, and
              3. a page Google tied to it, read by the checker (HTML, or a PDF when pypdfium2 is installed), names the
                 hospital in the right state and gives the number as THIS hospital's bed count: next to the word
                 "bed", with this hospital's name the closest hospital name to it (no sibling from OpenStreetMap's list
                 of the state closer, no other bed count in between, not one unit's beds) — page_confirmed: true, with
                 the page's words. Or, when the page can't be read (a script-filled page, a PDF too large), the support
                 lies on this hospital's own line of this answer: kept, page_confirmed: null.
            Rejected: a readable page that names the hospital but gives no such figure (or gives the number for
            another hospital or a unit), names a same-named hospital outside the state, or states the number without
            naming the hospital; a number only a support spanning several hospitals' lines backs.
  feedback  what failed goes back with the reason ("you answered from memory: no search ran", "the page you cited
            (x) does not state 391 beds; near the hospital's name it says: '...420 licensed beds...'") and the next
            attempt is checked the same way.
  cap       CAP_S seconds wall clock for the whole run; what passed by then is returned, the rest is not found (time)
            or its OpenStreetMap figure.
Without the key, the day's AI budget or the network: OpenStreetMap's beds only, labeled (by: fallback).

Each check decision goes to the verification ledger (llm.note_check, surface "hospital_beds"). A hospital that passed
is remembered (memory + a file next to the answer cache, FACT_TTL_S; *.db, so git ignores it), so a later incident
that reaches it doesn't search again; one a finished search found nothing for is remembered for MISS_TTL_S; a whole set
that finished cleanly is remembered too (the hero's second run is instant, with the trace of the run that found it).
Entries from older checker rules (CHECK_V) are dropped. Google's grounding terms: a fresh run returns its Search
Suggestions (`suggestions`, shown with the results, never stored); what is stored is our own checked facts and their
source links. Cost: every cascade starts this, so it has its own daily call budget (HOSPITAL_AGENT_DAILY_CALLS).

Page reads are the checker's own: http(s) to public addresses only (every hop), a size cap and short timeouts, at most
PAGE_READS at once across all jobs, the text dropped when the job ends. Public (no login), per-visitor rate limits;
nothing about a visitor is stored. The grid model is synthetic (Breakthrough
Energy / Texas A&M); the hospitals (OpenStreetMap) and their beds (as reported) are real, public figures.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import html as htmllib
import io
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import weakref
from collections import OrderedDict
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import hospitals
import llm
from grid import REGIONS, grid_at, region_code
from limiter import limiter

router = APIRouter(tags=["hospitals"])
log = logging.getLogger("uvicorn.error")

SURFACE = "hospital_beds"
# the models take turns across attempts (each has its own quota; llm's 429/503 chain applies inside a call too)
MODELS = [m.strip() for m in os.getenv("HOSPITAL_AGENT_MODELS", "gemini-3.6-flash,gemini-3.5-flash-lite").split(",") if m.strip()] or [llm.AGENT_MODEL]
THINKING = llm.AGENT_THINKING
BATCH = 3  # hospitals per grounded call (a round's calls run at once)
ATTEMPTS = 3  # proposals per batch (the first, then up to two revisions with the checker's findings)
MAX_RESEARCH = 24  # at most this many hospitals looked up per incident (a statewide storm keeps OSM for the rest)
CAP_S = 40.0  # the whole run's wall clock
CALL_TIMEOUT_S = 12.0  # one grounded call
MIN_CALL_S = 3.0  # no new call with less time than this left
MAX_BEDS = 4000  # the largest U.S. hospitals have ~2,500-3,000 beds
PAGE_TIMEOUT_S = 6.0  # per network operation
READ_BUDGET_S = 6.0  # a whole page download
RESOLVE_TIMEOUT_S = 4.0
PAGE_MAX_BYTES = 2_500_000
PDF_MAX_BYTES = 8_000_000
PDF_MAX_PAGES = 80
READABLE_CHARS = 1200  # less visible text than this (a script-only shell, a block page): not readable
SOURCES_PER_HOSPITAL = 3  # candidate pages read per figure
PAGE_READS = max(1, int(os.getenv("HOSPITAL_AGENT_PAGE_READS", "4") or 4))  # page downloads at once, across all jobs
HEDGE_S = 6.0  # the first model still silent after this: the second one is asked too
MAX_AFFECTED = 20000
CHECK_V = 2  # the checker's rules; a remembered figure or set from older rules is dropped on load
FACT_TTL_S = 14 * 24 * 3600
SET_TTL_S = 3 * 24 * 3600
MISS_TTL_S = 6 * 3600  # a hospital searched without a figure that passed is not searched again for this long
JOBS_MAX = 64
JOB_TTL_S = 2 * 3600
RUNNING_MAX = 6
# grounded calls per quota day (Pacific, as llm's cap): Google bills each search a Gemini 3 model runs, and the route is
# public (every cascade starts it), so the agent has its own budget well under the app-wide AI_DAILY_LIMIT (1500 on
# Render): past it, OpenStreetMap's figures (labeled), and the rest of the app's AI keeps its quota
DAILY_CALLS = int(os.getenv("HOSPITAL_AGENT_DAILY_CALLS", "400") or 0)
_day = {"day": "", "used": 0}
_day_lock = threading.Lock()


def _take_call() -> bool:
    today = datetime.now(llm.QUOTA_TZ).date().isoformat()
    with _day_lock:
        if _day["day"] != today:
            _day["day"], _day["used"] = today, 0
        if _day["used"] >= DAILY_CALLS:
            return False
        _day["used"] += 1
        return True


def _calls_left() -> int:
    today = datetime.now(llm.QUOTA_TZ).date().isoformat()
    with _day_lock:
        return DAILY_CALLS - (_day["used"] if _day["day"] == today else 0)
REDIRECT_HOST = "vertexaisearch.cloud.google.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 OverloadBedsCheck/1.0"
NO_ANSWER = "Gemini did not answer."
# reasons that say nothing about the hospital (the model or the clock failed): never remembered as a miss
TRANSIENT = {NO_ANSWER, "the page check did not finish in time.", "the run's time ran out before the search.", "the run ran out of time."}

SYSTEM = (
    "You are a fact checker with Google Search. You do not know any hospital's bed count from memory: for every "
    "hospital you MUST run a Google Search (for example: \"<hospital name> <city> number of beds\") and answer only with "
    "a figure a search result states for that hospital. If the results don't state one, answer NOT FOUND."
)
ASSUMPTION = (
    "Beds as reported (licensed or staffed beds, as the source states them), not a count of the people inside. "
    "Which hospitals: the ones whose nearest substation in the synthetic model lost at least 60% of its load; real "
    "hospitals have their own feeders, generators and priority restoration."
)
OSM_NOTE = "OpenStreetMap's beds tag (map data, not checked by the agent)"

# the facts cache, next to the answer cache (runtime state: *.db is gitignored); HOSPITAL_BEDS_CACHE_FILE= (empty) = off
_default_file = os.path.join(os.path.dirname(llm.CACHE_FILE), ".hospital_beds_cache.db") if llm.CACHE_FILE else ""
CACHE_FILE = os.getenv("HOSPITAL_BEDS_CACHE_FILE", _default_file)
_facts: dict[str, dict] = {}  # region:id:name -> a checked 'reported' row (+ "at", "v")
_sets: dict[str, dict] = {}  # the set's key -> a finished run (+ "at", "v")
_misses: dict[str, dict] = {}  # region:id:name -> {"at", "v", "why"}: searched, nothing passed
_store_lock = threading.Lock()
FACT_FIELDS = ("beds", "beds_basis", "beds_kind", "source", "checked", "page_confirmed", "quote", "note")


def L(en: str, es: str) -> dict:
    return {"en": en, "es": es}


def _load_disk() -> None:
    if not CACHE_FILE:
        return
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return
    now = time.time()
    if isinstance(raw, dict):
        for k, v in (raw.get("facts") or {}).items():
            if isinstance(v, dict) and v.get("v") == CHECK_V and now - float(v.get("at") or 0) <= FACT_TTL_S and v.get("beds_basis") == "reported":
                _facts[k] = v
        for k, v in (raw.get("sets") or {}).items():
            if isinstance(v, dict) and v.get("v") == CHECK_V and now - float(v.get("at") or 0) <= SET_TTL_S and isinstance(v.get("hospitals"), list):
                _sets[k] = v
        for k, v in (raw.get("misses") or {}).items():
            if isinstance(v, dict) and v.get("v") == CHECK_V and now - float(v.get("at") or 0) <= MISS_TTL_S and isinstance(v.get("why"), str):
                _misses[k] = v


def _save_disk() -> None:
    """Atomically (a temp file, then a rename); called with _store_lock held."""
    if not CACHE_FILE:
        return
    try:
        now = time.time()
        for k in [k for k, v in _misses.items() if now - float(v.get("at") or 0) > MISS_TTL_S]:
            del _misses[k]
        tmp = f"{CACHE_FILE}.{os.getpid()}.tmp.db"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"facts": _facts, "sets": _sets, "misses": _misses}, f)
        os.replace(tmp, CACHE_FILE)
    except OSError as e:  # a read-only disk just means no persistence
        log.warning("hospital agent: cache not saved: %s", e)


_load_disk()


def _fact_key(code: str, h: dict) -> str:
    return f"{code}:{h['id']}:{h['name']}"


def _set_key(code: str, rows: list[dict]) -> str:
    raw = json.dumps([code, sorted((int(h["id"]), h["name"]) for h in rows)])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ------------------------------------------------------------------------------------ reading a page
_GENERIC = set("hospital hospitals medical center centre health healthcare the and for with care services service system systems campus inc llc".split())


def _name_tokens(name: str) -> list[str]:
    """A name's distinctive words, in order ("Physicians Regional Medical Center- Collier Boulevard" -> physicians,
    regional, collier, boulevard)."""
    out = []
    for w in re.findall(r"[a-z][a-z']{2,}", name.lower()):
        if w not in _GENERIC and w not in out:
            out.append(w)
    return out


def _phrase(name: str) -> re.Pattern | None:
    """How ANOTHER hospital's name is recognized on a page: its distinctive words in order with at most three other
    words between each ("Physicians Regional - Pine Ridge" for OpenStreetMap's "Physicians Regional Medical Center -
    Pine Ridge"); a name with one distinctive word only as its whole name, nearly word for word ("Doctors Hospital",
    "Florida Medical Center Hospital"); None when that is a single word ("AdventHealth", "The Centers"), which can't be
    told apart from ordinary text."""
    words, gap = _phrase_parts(name)
    if len(words) < 2:
        return None
    return re.compile(r"\b" + gap.join(re.escape(w) for w in words) + r"\b")


def _phrase_parts(name: str) -> tuple[list[str], str]:
    toks = _name_tokens(name)
    if len(toks) >= 2:
        return toks, r"(?:\W+[\w'-]+){0,3}?\W+"
    return [w for w in re.findall(r"[a-z0-9][a-z0-9']*", name.lower()) if w != "the"], r"(?:\W+[\w'-]+)?\W+"


def _public_url(url: str) -> bool:
    """http(s) on the default ports to a host whose every address is public (never the server's own network)."""
    try:
        p = urllib.parse.urlsplit(url)
        port = p.port
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or port not in (None, 80, 443):
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    try:
        return bool(infos) and all(ipaddress.ip_address(str(i[4][0]).split("%")[0]).is_global for i in infos)
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 4

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_url(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect to a non-public address refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_resolver = urllib.request.build_opener(_NoRedirect)
_fetcher = urllib.request.build_opener(_SafeRedirect)


def _resolve(uri: str) -> str | None:
    """Google's grounding redirect -> the page it points to (one request, not followed)."""
    try:
        host = urllib.parse.urlsplit(uri).hostname
    except ValueError:
        return None
    if host != REDIRECT_HOST:
        return uri if _public_url(uri) else None
    try:
        with _resolver.open(urllib.request.Request(uri, headers={"User-Agent": UA}), timeout=RESOLVE_TIMEOUT_S) as r:
            return r.geturl() if r.geturl() != uri else None
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") if e.headers else None
        return urllib.parse.urljoin(uri, loc) if loc and e.code in (301, 302, 303, 307, 308) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _pdf_text(raw: bytes) -> str | None:
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415 - optional: without it a PDF is "not read"
    except ImportError:
        return None
    try:
        doc = pdfium.PdfDocument(io.BytesIO(raw))
        out = []
        for i in range(min(len(doc), PDF_MAX_PAGES)):
            page = doc[i]
            out.append(page.get_textpage().get_text_range())
        doc.close()
        return " ".join(out)
    except Exception:  # noqa: BLE001 - a PDF the library can't read is just "not read"
        return None


def _read_body(r, cap: int, deadline: float) -> tuple[bytes, bool]:
    """Up to `cap` bytes before `deadline` (monotonic); (the bytes, cut short)."""
    chunks, n = [], 0
    while n < cap:
        if time.monotonic() > deadline:
            raise TimeoutError("the page was too slow to download")
        b = r.read(min(65536, cap - n))
        if not b:
            return b"".join(chunks), False
        chunks.append(b)
        n += len(b)
    return b"".join(chunks), True


def _page_text(url: str) -> tuple[str | None, str]:
    """(the page's visible text, or None; why not). At most READ_BUDGET_S for the whole download."""
    if not _public_url(url):
        return None, "not a public web address"
    deadline = time.monotonic() + READ_BUDGET_S
    try:
        with _fetcher.open(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/pdf;q=0.9,text/plain;q=0.8,*/*;q=0.5"}), timeout=PAGE_TIMEOUT_S) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            is_pdf = "pdf" in ctype or (not ctype and urllib.parse.urlsplit(r.geturl()).path.lower().endswith(".pdf"))
            if not is_pdf and ctype and not any(t in ctype for t in ("html", "text", "xml")):
                return None, f"not a web page ({ctype.split(';')[0]})"
            size = r.headers.get("Content-Length")
            if is_pdf and size and size.isdigit() and int(size) > PDF_MAX_BYTES:
                return None, f"a PDF too large for the checker ({int(size) / 1e6:.0f} MB)"
            raw, cut = _read_body(r, PDF_MAX_BYTES if is_pdf else PAGE_MAX_BYTES, deadline)
            if is_pdf and cut:
                return None, f"a PDF too large for the checker (over {PDF_MAX_BYTES / 1e6:.0f} MB)"
            m = re.search(r"charset=([\w-]+)", ctype)
            enc = m.group(1) if m else "utf-8"
    except urllib.error.HTTPError as e:
        return None, f"the site answered {e.code}"
    except TimeoutError as e:
        return None, str(e)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"could not be reached ({type(e).__name__})"
    if is_pdf:
        text = _pdf_text(raw)
        if text is None:
            return None, "a PDF the checker could not read"
        text = re.sub(r"\s+", " ", text).strip()
    else:
        try:
            doc = raw.decode(enc, "replace")
        except LookupError:
            doc = raw.decode("utf-8", "replace")
        doc = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1\s*>", " ", doc)
        doc = re.sub(r"(?s)<!--.*?-->", " ", doc)
        text = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", doc))).strip()
    if len(text) < READABLE_CHARS:
        return None, "too little text to read (a script-only page or a block)"
    return text, ""


def _clip_words(text: str, a: int, b: int) -> str:
    """text[a:b] without half words at the edges, with ellipses where it was cut."""
    q = text[a:b]
    if a > 0:
        q = q.split(" ", 1)[-1]
    if b < len(text):
        q = q.rsplit(" ", 1)[0]
    return ("…" if a > 0 else "") + q.strip() + ("…" if b < len(text) else "")


# Whose number is it? Pages list sibling hospitals close together ("Gulf Coast Medical Center: 699-bed hospital… Cape
# Coral Hospital: 303-bed hospital"; Lee Health's and Physicians Regional's own pages do), so a number next to "bed"
# counts for a hospital only when THAT hospital's name (all its distinctive words, together) is the closest hospital
# name to it: ending up to NAME_BEFORE characters before it, or starting up to NAME_AFTER after it ("the 336-bed Lee
# Memorial Hospital", counted double); no other hospital of the state (OpenStreetMap's list) or of the incident stands
# closer, no other bed count sits between the name and the number, and the words just before it don't make it a single
# unit's beds ("Number of ED Beds : 70", "Skilled Nursing Unit: 18 beds", "adding … 268 patient beds").
BED_NEAR = 50  # the word bed stands this close to the number
NAME_BEFORE = 260  # measured on the hero's pages: Physicians Regional - North's name ends ~225 characters before its 20
NAME_AFTER = 45
NAME_SPAN = 70  # a name's distinctive words stand together within this many characters
_COUNT_BETWEEN = re.compile(r"(?i)\d[\d,]*\s*(?:-\s*)?(?:[a-z/]+\s+){0,2}beds?\b|\bbeds?\s*:?\s*\d")
_UNIT = re.compile(r"(?i)\b(?:units?|icu|nicu|picu|ed|er|emergency|stroke|trauma|skilled|nursing\s+home|rehab\w*|bassinets?|observation|add(?:s|ed|ing)?|additional|expan\w*)\b")
_BED_WORD = re.compile(r"(?i)\bbed")


def _unit_words(text: str, name_end: int, ms: int, me: int) -> bool:
    """Words that make the count one unit's (or an expansion's) beds: in the phrase before the number (after the name,
    the last sentence break or the last other number, 90 characters back at most: "Operating Rooms : 22 Number of
    Skilled Nursing Unit Beds in Building Adjacent to Hospital : 75"), or between the number and the word bed right after
    it ("12 NICU beds")."""
    before = re.split(r"[.;]\s|\d", text[max(name_end, ms - 90) : ms])[-1]
    after = text[me : me + 30]
    b = _BED_WORD.search(after)
    return bool(_UNIT.search(before) or (b and _UNIT.search(after[: b.start()])))


def _num_pattern(n: int) -> re.Pattern:
    forms = sorted({str(n), f"{n:,}"}, key=len, reverse=True)
    return re.compile(r"(?<![\d,.$])(?:" + "|".join(re.escape(f) for f in forms) + r")(?![\d]|,\d|\.\d)")


def _bed_spots(text: str, n: int) -> list[tuple[int, int]]:
    """Where `n` stands next to the word bed (within BED_NEAR characters)."""
    out = []
    for m in _num_pattern(n).finditer(text):
        if _BED_WORD.search(text, max(0, m.start() - BED_NEAR), min(len(text), m.end() + BED_NEAR)):
            out.append((m.start(), m.end()))
    return out


def _hits(low: str, toks: list[str], lo: int, hi: int, need: int) -> list[tuple[int, int]]:
    """(start, end) where at least `need` of a name's distinctive words stand together (within NAME_SPAN) in low[lo:hi]."""
    if not toks or need <= 0:
        return []
    occ = sorted((m.start() + lo, m.end() + lo, t) for t in toks for m in re.finditer(r"\b" + re.escape(t) + r"\b", low[lo:hi]))
    out = []
    for i, (s, _e, _t) in enumerate(occ):
        seen, end = set(), s
        for s2, e2, t2 in occ[i:]:
            if s2 - s > NAME_SPAN:
                break
            if t2 not in seen:
                seen.add(t2)
                end = max(end, e2)
                if len(seen) == len(toks):
                    break
        if len(seen) >= need:
            out.append((s, end))
    return out


# our name used as a place inside another place's name: "The Rehabilitation Hospital of Cape Coral: 40-bed" is not Cape
# Coral Hospital's figure
_PLACE_OF = re.compile(r"(?i)\b(?:hospital|center|centre|clinic|institute|facility|campus|pavilion|unit)\s+(?:of|at|in)\s+(?:the\s+)?$")


def _distance(ms: int, me: int, s: int, e: int) -> int | None:
    if e <= ms and ms - e <= NAME_BEFORE:
        return ms - e
    if s >= me and s - me <= NAME_AFTER:
        return 2 * (s - me)
    return None


def _nearest(text: str, low: str, ms: int, me: int, name: list[str] | re.Pattern) -> tuple[int, int, int] | None:
    """The closest place a name stands to the number at [ms, me): (distance, start, end), or None. `name`: this
    hospital's distinctive words (all of them, together, not as a place inside another name) or another hospital's
    phrase (_phrase)."""
    lo, hi = max(0, ms - NAME_BEFORE - NAME_SPAN), min(len(low), me + NAME_AFTER + NAME_SPAN)
    if isinstance(name, re.Pattern):
        spans = [(m.start(), m.end()) for m in name.finditer(low, lo, hi)]
    else:
        spans = [(s, e) for s, e in _hits(low, name, lo, hi, len(name)) if not _PLACE_OF.search(text[max(0, s - 40) : s])]
    best = None
    for s, e in spans:
        d = _distance(ms, me, s, e)
        if d is not None and (best is None or d < best[0]):
            best = (d, s, e)
    return best


def _owned(text: str, low: str, ms: int, me: int, toks: list[str], others: list[re.Pattern]) -> bool:
    """Is the bed count at [ms, me) this hospital's (see above)?"""
    mine = _nearest(text, low, ms, me, toks)
    if mine is None:
        return False
    d, s, e = mine
    between = text[e:ms] if e <= ms else text[me:s]
    if _COUNT_BETWEEN.search(between) or _unit_words(text, e if e <= ms else 0, ms, me):
        return False
    return not any((o := _nearest(text, low, ms, me, pat)) is not None and o[0] < d for pat in others)


def _number_near_bed(text: str, n: int, tokens: list[str], others: list[re.Pattern] = (), low: str | None = None) -> str | None:
    """A short quote where `n` is this hospital's bed count by the rule above, or None. `tokens`: its distinctive name
    words; `others`: the other hospitals' names (_phrase), the state's and the incident's."""
    if not tokens:
        return None
    low = low if low is not None else _lower(text)
    for ms, me in _bed_spots(text, n):
        if _owned(text, low, ms, me, tokens, list(others)):
            return _clip_words(text, max(0, ms - 70), min(len(text), me + 45))
    return None


def _bed_mentions(text: str, tokens: list[str], others: list[re.Pattern] = (), limit: int = 2, low: str | None = None) -> list[str]:
    """The bed counts the page gives for THIS hospital by the same rule (for the feedback: what the page does say)."""
    if not tokens:
        return []
    low = low if low is not None else _lower(text)
    out = []
    for m in re.finditer(r"(?<![\d,.$])\d{1,3}(?:,\d{3})?(?![\d]|,\d|\.\d)", text):
        n = int(m.group(0).replace(",", ""))
        if not (1 <= n <= MAX_BEDS) or not _BED_WORD.search(text, max(0, m.start() - BED_NEAR), min(len(text), m.end() + BED_NEAR)):
            continue
        if not _owned(text, low, m.start(), m.end(), tokens, list(others)):
            continue
        q = _clip_words(text, max(0, m.start() - 50), min(len(text), m.end() + 30))
        if q not in out:
            out.append(q)
        if len(out) >= limit:
            break
    return out


def _has_place(text: str, low: str, area: str | None, state_name: str, code: str) -> bool:
    """The page puts it in the right place: its area's name, the state's name or the state's postal code (an address)."""
    if area and area.lower() in low:
        return True
    return state_name.lower() in low or re.search(r"\b" + re.escape(code) + r"\b", text) is not None


def _domain(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    return host[4:] if host.startswith("www.") else host


_page_sems: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _page_sem() -> asyncio.Semaphore:
    """PAGE_READS page downloads at once across every job (one semaphore per event loop)."""
    loop = asyncio.get_running_loop()
    sem = _page_sems.get(loop)
    if sem is None:
        sem = _page_sems[loop] = asyncio.Semaphore(PAGE_READS)
    return sem


def _read(uri: str, title: str, pages: dict, lock: threading.Lock) -> dict:
    """A candidate page, read once per job (by its link and by the page it leads to): {url, title, text | None, why}."""
    with lock:
        hit = pages.get("link:" + uri)
    if hit is not None:
        return hit
    url = _resolve(uri)
    if not url:
        out = {"url": uri, "title": title or _domain(uri), "text": None, "why": "the link could not be followed"}
    else:
        with lock:
            out = pages.get("page:" + url)
        if out is None:
            text, why = _page_text(url)
            out = {"url": url, "title": _domain(url) or title, "text": text, "why": why}
    with lock:
        pages["link:" + uri] = out
        pages["page:" + out["url"]] = out
    return out


# (id, distinctive words, phrase or None, the phrase's words): how each hospital's name is recognized on a page
Name = tuple[int, list[str], "re.Pattern | None", list[str]]
_region_names: dict[str, list[Name]] = {}


def _name_entry(i: int, name: str) -> Name:
    pat = _phrase(name)
    return (int(i), _name_tokens(name), pat, _phrase_parts(name)[0] if pat else [])


def _state_names(code: str) -> list[Name]:
    """Every hospital OpenStreetMap lists in the state (cached per state)."""
    hit = _region_names.get(code)
    if hit is None:
        try:
            rows = hospitals.region_index(code)["hospitals"]
        except Exception:  # noqa: BLE001 - without the list, only the incident's own hospitals compete
            rows = []
        hit = _region_names[code] = [_name_entry(h["id"], h["name"]) for h in rows]
    return hit


def _dedupe_names(names: list[Name]) -> list[Name]:
    seen, out = set(), []
    for n in names:
        if n[0] not in seen:
            seen.add(n[0])
            out.append(n)
    return out


def _others(row: dict, names: list[Name], words: set[str]) -> list[re.Pattern]:
    """The other hospitals' names that could compete on this page (their phrase's words are all on it), without the ones
    whose distinctive words are all in this hospital's name (the same hospital, or a shorter name for it)."""
    own = set(_name_tokens(row["name"]))
    out, seen = [], set()
    for i, toks, pat, pwords in names:
        if i == row["id"] or pat is None or pat.pattern in seen or (toks and set(toks) <= own) or not all(w in words for w in pwords):
            continue
        seen.add(pat.pattern)
        out.append(pat)
    return out


def _lower(text: str) -> str:
    """text.lower() with the same length (so positions line up), even for the rare letters that lowercase to two."""
    low = text.lower()
    return low if len(low) == len(text) else "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def _verdict(page: dict, n: int, row: dict, names: list[Name], state_name: str, code: str) -> dict:
    """What one page says about this figure for this hospital:
    {readable, has_name, place, states_n (n stands next to "bed" somewhere on it), quote (n is this hospital's, by the
    owner rule), mentions (what it says for this hospital instead)}."""
    text = page["text"]
    if text is None:
        return {"readable": False, "has_name": None, "place": None, "states_n": False, "quote": None, "mentions": []}
    low = page.get("low")
    if low is None:
        low = page["low"] = _lower(text)
    words = page.get("words")
    if words is None:
        words = page["words"] = set(re.findall(r"[a-z0-9][a-z0-9']*", low))
    toks = _name_tokens(row["name"])
    present = sum(1 for t in toks if t in words)
    has_name = bool(toks) and (present == len(toks) or (len(toks) >= 3 and present >= len(toks) - 1))
    place = _has_place(text, low, row.get("area"), state_name, code)
    states_n = bool(_bed_spots(text, n))
    quote, mentions = None, []
    if has_name and place:
        others = _others(row, names, words)
        quote = _number_near_bed(text, n, toks, others, low) if states_n else None
        if not quote:
            mentions = _bed_mentions(text, toks, others, low=low)
    return {"readable": True, "has_name": has_name, "place": place, "states_n": states_n, "quote": quote, "mentions": mentions}


# ------------------------------------------------------------------------------------ the answer
LINE = re.compile(r"^[ \t]*(\d{1,2})[.)][ \t]*(.+)$", re.M)


def _which(name: str, items: list[dict], guess: int) -> int | None:
    """The hospital an answer line is about: by its name (the model may reorder or skip lines), the line's number
    breaking ties. None when the name matches no hospital of the batch."""
    said = set(_name_tokens(name))

    def score(h: dict) -> float:
        toks = _name_tokens(h["name"])
        return sum(1 for t in toks if t in said) / len(toks) if toks else -1.0

    scores = [score(h) for h in items]
    if 0 <= guess < len(items) and scores[guess] < 0:
        return guess  # a name without a distinctive word: only the number can place it
    best = max(scores, default=0.0)
    if best <= 0:
        return None
    if 0 <= guess < len(items) and scores[guess] == best:
        return guess
    return scores.index(best) if best >= 0.5 else None


def _parse(text: str, items: list[dict]) -> dict[int, dict]:
    """{hospital's position in `items` (0-based): {n: int | None, kind, site, line: (start, end), num: (start, end) | None}}.
    A line is `<k>. name | number | kind | site` (extra fields are ignored); it is placed by the hospital's name."""
    out: dict[int, dict] = {}
    for m in LINE.finditer(text or ""):
        if "|" not in m.group(2):
            continue
        fields, spans, at = [], [], m.start(2)
        for f in m.group(2).split("|"):
            fields.append(f)
            spans.append(at)
            at += len(f) + 1
        i = _which(fields[0], items, int(m.group(1)) - 1)
        if i is None or i in out:
            continue
        field = fields[1] if len(fields) > 1 else ""
        kind_field = (fields[2] if len(fields) > 2 else "").lower()
        num = re.search(r"\d(?:[\d,]*\d)?", field)
        n, span = None, None
        if num and not re.search(r"(?i)not\s*found", field):
            try:
                n = int(num.group(0).replace(",", ""))
                span = (spans[1] + num.start(), spans[1] + num.end())
            except ValueError:
                n = None
        kind = next((k for k in ("licensed", "staffed") if k in kind_field or k in field.lower()), "beds")
        out[i] = {"n": n, "kind": kind, "site": (fields[3] if len(fields) > 3 else "").strip()[:80], "line": (m.start(), m.end()), "num": span}
    return out


def _candidates(res: dict, p: dict) -> list[tuple[int, bool]]:
    """Web sources Google tied to the stretch of the answer holding this hospital's number, as (source index, tight):
    tight = a support that lies on this hospital's line (first), else a wider one that spans other hospitals' lines too
    (those pages may be another hospital's: they can confirm a figure on the page, never back it alone)."""
    if not p.get("num"):
        return []
    a, b = p["num"]
    s0, s1 = p["line"]
    tight, wide = [], []
    for s in res.get("supports") or []:
        if s["start"] <= a and s["end"] >= b and s["sources"]:
            (tight if s["start"] >= s0 - 4 and s["end"] <= s1 + 4 else wide).extend(s["sources"])
    out: list[tuple[int, bool]] = []
    for i in tight:
        if all(i != j for j, _t in out):
            out.append((i, True))
    for i in wide:
        if all(i != j for j, _t in out):
            out.append((i, False))
    return out


def _prompt(state: str, items: list[dict], feedback: dict[int, str] | None = None) -> str:
    rows = []
    for k, h in enumerate(items, 1):
        at = f"at {float(h['lat']):.4f}, {float(h['lon']):.4f}"
        line = f"{k}. {h['name']} ({state}; the {h['area']} area; {at})" if h.get("area") else f"{k}. {h['name']} ({state}; {at})"
        if feedback and h["id"] in feedback:
            line += f"\n   Your earlier answer was not kept: {feedback[h['id']]}"
        rows.append(line)
    return (
        "You are checking public facts for a report about hospitals during a power outage. For each hospital below, search "
        "Google and find how many beds it has as a web page states it: the hospital or its health system, a state licensing "
        "agency, CMS, the American Hospital Directory, or a news report. Prefer licensed beds; else staffed beds; else the "
        "total beds the page states. Give a number only if a page you found states that number of beds for THIS hospital "
        "(the same name and place); otherwise write NOT FOUND. Never estimate and never add up.\n\n"
        "Answer with exactly one line per hospital, in this form and nothing else:\n"
        "<n>. <hospital name> | <number of beds, digits only, or NOT FOUND> | <licensed|staffed|beds> | <the source's website>\n\n"
        + "\n".join(rows)
    )


# ------------------------------------------------------------------------------------ the job
class _Job:
    def __init__(self, code: str, key: str, rows: list[dict]):
        self.id = secrets.token_urlsafe(12)
        self.code = code
        self.key = key
        self.rows = rows
        self.trace: list[dict] = []
        self.status = "running"
        self.by = "gemini"
        self.why: str | None = None
        self.suggestions: list[str] = []
        self.created = time.monotonic()
        self.t0 = time.monotonic()
        self.elapsed: float | None = None
        self.timed = False  # something was cut short by the wall clock (the set's answer is then not remembered)
        self.calls = 0
        self.searches = 0
        self.pages: dict = {}  # the pages read in this job (cleared when it ends)
        self.pages_lock = threading.Lock()
        self.grounded: dict[int, list[tuple[str, str]]] = {}  # hospital id -> (uri, title) of pages Google tied to its own line
        self.names: list | None = None  # every hospital that could own a figure on a page
        self.misses: dict[int, str] = {}  # hospital id -> why a finished search found nothing that passed
        self.task: asyncio.Task | None = None

    def add(self, actor: str, kind: str, tone: str, title: dict, detail: dict | None = None, call: str | None = None, ms: int | None = None) -> None:
        row = {"n": len(self.trace) + 1, "actor": actor, "kind": kind, "tone": tone, "title": title, "at_s": round(time.monotonic() - self.t0, 1)}
        if detail:
            row["detail"] = detail
        if call:
            row["call"] = call
        if ms is not None:
            row["ms"] = ms
        self.trace.append(row)

    def left(self) -> float:
        return CAP_S - (time.monotonic() - self.t0)


_jobs: "OrderedDict[str, _Job]" = OrderedDict()
_by_set: dict[str, str] = {}
_jobs_lock = threading.Lock()


def _gc() -> None:
    now = time.monotonic()
    for jid in [j for j, job in _jobs.items() if job.status != "running" and now - job.created > JOB_TTL_S]:
        del _jobs[jid]
    while len(_jobs) > JOBS_MAX:
        old = next((j for j, job in _jobs.items() if job.status != "running"), None)
        if old is None:
            break
        del _jobs[old]


def _row(h: dict) -> dict:
    osm = h.get("beds")
    osm = int(osm) if isinstance(osm, (int, float)) and math.isfinite(osm) and 0 < osm <= MAX_BEDS else None
    return {"id": int(h["id"]), "name": h["name"], "area": h.get("area"), "lat": h["lat"], "lon": h["lon"], "emergency": bool(h.get("emergency")),
            "osm_beds": osm, "beds": None, "beds_basis": None, "beds_kind": None, "source": None, "checked": False, "page_confirmed": None,
            "quote": None, "note": None, "state": "queued"}


def _osm_source(r: dict) -> dict:
    lat, lon = float(r["lat"]), float(r["lon"])
    return {"title": "OpenStreetMap (beds tag)", "url": f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=18/{lat:.5f}/{lon:.5f}"}


def _settle(r: dict, why: str) -> None:
    """Nothing passed for this hospital: its OpenStreetMap figure when it has one, else not found (with the reason)."""
    if r["osm_beds"]:
        r.update(beds=r["osm_beds"], beds_basis="osm", beds_kind="beds", source=_osm_source(r), checked=False, page_confirmed=None, quote=None, note=f"{why} Shown instead: {OSM_NOTE}.")
    else:
        r.update(beds=None, beds_basis="not_found", beds_kind=None, source=None, checked=False, page_confirmed=None, quote=None, note=why)
    r["state"] = "done"


def _counts(rows: list[dict]) -> dict:
    rep = [r for r in rows if r["beds_basis"] == "reported"]
    osm = [r for r in rows if r["beds_basis"] == "osm"]
    return {
        "hospitals": len(rows),
        "reported": len(rep),
        "osm": len(osm),
        "not_found": sum(1 for r in rows if r["beds_basis"] == "not_found"),
        "pending": sum(1 for r in rows if r["beds_basis"] is None),
        "page_confirmed": sum(1 for r in rep if r["page_confirmed"]),
        "beds_total": sum(int(r["beds"]) for r in rows if r["beds"]),
        "beds_reported": sum(int(r["beds"]) for r in rep),
        "beds_osm": sum(int(r["beds"]) for r in osm),
        "differs_from_osm": sum(1 for r in rep if r["osm_beds"] and r["osm_beds"] != r["beds"]),
    }


_KIND_ES = {"licensed": "con licencia", "staffed": "en servicio", "beds": "camas"}


def _brief(items: list[dict], parsed: dict[int, dict], es: bool = False) -> str:
    out = []
    for i, h in enumerate(items):
        p = parsed.get(i)
        if p and p["n"] is not None:
            out.append(f"{h['name']}: {p['n']:,} {_KIND_ES[p['kind']] if es else p['kind']}")
        else:
            out.append(f"{h['name']}: {'no encontrado' if es else 'not found'}")
    return " · ".join(out)


def _calls(queries: list[str]) -> str | None:
    if not queries:
        return None
    shown = "; ".join(f'google_search("{q[:70]}")' for q in queries[:2])
    return shown + (f" +{len(queries) - 2} more" if len(queries) > 2 else "")


def _es_reason(why: str) -> str:
    """The checker's reason in Spanish (the reasons are a fixed set of shapes)."""
    rules = [
        (r"^you answered from memory: no search ran\.$", "respondió de memoria: no hizo ninguna búsqueda."),
        (r"^you answered NOT FOUND\.$", "respondió NO ENCONTRADO."),
        (r"^there was no line for it in the answer\.$", "no había una línea para él en la respuesta."),
        (r"^([\d,]+) is not a plausible number of beds\.$", r"\1 no es un número de camas plausible."),
        (r"^no web page in Google's grounding backs the number ([\d,]+)\.$", r"ninguna página web de la búsqueda de Google respalda el número \1."),
        (r"^no web page Google tied to this hospital's own line backs the number ([\d,]+)\.$",
         r"ninguna página web que Google vinculó a la línea de este hospital respalda el número \1."),
        (r"^the page you cited \((.+?)\) does not name this hospital\.$", r"la página citada (\1) no nombra este hospital."),
        (r"^the page you cited \((.+?)\) names a hospital of that name, but not in (.+?)\.$", r"la página citada (\1) nombra un hospital con ese nombre, pero no en \2."),
        (r"^the page you cited \((.+?)\) gives ([\d,]+) beds for something else: not next to this hospital's name(.*)$",
         r"la página citada (\1) da \2 camas para otra cosa: no junto al nombre de este hospital\3"),
        (r"^the page you cited \((.+?)\) does not state ([\d,]+) beds(.*)$", r"la página citada (\1) no dice \2 camas\3"),
        (r"^the page check did not finish in time\.$", "la verificación de la página no terminó a tiempo."),
        (r"^the run's time ran out before the search\.$", "se acabó el tiempo antes de la búsqueda."),
    ]
    for pat, rep in rules:
        if re.match(pat, why):
            return (re.sub(pat, rep, why).replace("; near the hospital's name it says:", "; cerca del nombre del hospital dice:")
                    .replace("; for this hospital it says:", "; para este hospital dice:")
                    .replace("(another hospital's name, another bed count or a single unit is closer to it)",
                             "(otro nombre de hospital, otra cifra de camas o una sola unidad está más cerca)"))
    return why.replace(NO_ANSWER, "Gemini no respondió.")


async def _judge(job: _Job, items: list[dict], parsed: dict[int, dict], res: dict, searched: bool, only: set[int] | None = None) -> dict[int, str]:
    """Check every proposed figure (code). Rows that pass are filled in; returns {hospital id: why it was not kept}."""
    rejected: dict[int, str] = {}
    work = []  # (row, figure, [(uri, title, grounded now)])
    srcs = res.get("sources") or []
    for i, r in enumerate(items):
        if only is not None and r["id"] not in only:
            continue
        p = parsed.get(i)
        if p is None:
            rejected[r["id"]] = "there was no line for it in the answer."
            llm.note_check(SURFACE, False, "no answer line for a hospital")
            continue
        now = [(srcs[c]["uri"], srcs[c]["title"], tight) for c, tight in _candidates(res, p)] if p["n"] is not None else []
        if now:  # pages Google tied to this hospital's own line, remembered for the next attempts
            known = job.grounded.setdefault(r["id"], [])
            known.extend(x for x in ((u, t) for u, t, tight in now if tight) if x not in known)
        if p["n"] is None:
            rejected[r["id"]] = "you answered NOT FOUND." if searched else "you answered from memory: no search ran."
            continue
        if not searched:
            rejected[r["id"]] = "you answered from memory: no search ran."
            llm.note_check(SURFACE, False, "a bed count given without any search", p["n"])
            continue
        if not (1 <= p["n"] <= MAX_BEDS):
            rejected[r["id"]] = f"{p['n']:,} is not a plausible number of beds."
            llm.note_check(SURFACE, False, "a bed count outside 1-4,000", p["n"])
            continue
        earlier = [(u, t) for u, t in job.grounded.get(r["id"], []) if all(u != u2 for u2, _t, _g in now)]
        # (uri, title, grounded in THIS answer, tied to this hospital's own line)
        cands = [(u, t, True, tight) for u, t, tight in now[:SOURCES_PER_HOSPITAL]] + [
            (u, t, False, True) for u, t in earlier[: max(0, SOURCES_PER_HOSPITAL + 1 - len(now))]]
        if not cands:
            rejected[r["id"]] = f"no web page in Google's grounding backs the number {p['n']:,}."
            llm.note_check(SURFACE, False, "a bed count no grounded web source backs", p["n"])
            continue
        work.append((r, p, cands))

    state_name = REGIONS[job.code]["name"]
    names = job.names or _state_names(job.code)

    async def read(u: str, t: str) -> dict:
        async with _page_sem():  # a few page downloads at once, whatever the number of jobs (memory on a small server)
            return await asyncio.to_thread(_read, u, t, job.pages, job.pages_lock)

    async def one(r: dict, p: dict, cands: list) -> None:
        pages = await asyncio.gather(*[read(u, t) for u, t, _g, _tight in cands])
        checks = [(pg, _verdict(pg, p["n"], r, names, state_name, job.code), g, tight) for pg, (_u, _t, g, tight) in zip(pages, cands)]
        good = next(((pg, v) for pg, v, _g, _t in checks if v["quote"]), None)
        if good:
            pg, v = good
            r.update(beds=p["n"], beds_basis="reported", beds_kind=p["kind"], source={"title": pg["title"], "url": pg["url"]}, checked=True,
                     page_confirmed=True, quote=v["quote"], note=None, state="done")
            llm.note_check(SURFACE, True, "a bed count on its source page, next to the hospital's own name")
            return

        def reject(why: str, ledger: str) -> None:
            rejected[r["id"]] = why
            llm.note_check(SURFACE, False, ledger, p["n"])

        # a readable page that names the hospital (in the right state) but not with this figure contradicts it
        named = [(pg, v) for pg, v, _g, _t in checks if v["readable"] and v["has_name"] and v["place"]]
        if named:
            pg, v = named[0]
            said = f"; for this hospital it says: {' / '.join(repr(x) for x in v['mentions'])}." if v["mentions"] else "."
            if v["states_n"]:
                return reject(f"the page you cited ({pg['title']}) gives {p['n']:,} beds for something else: not next to this hospital's name "
                              f"(another hospital's name, another bed count or a single unit is closer to it){said}",
                              "the grounded page gives that bed count for another hospital or a unit")
            return reject(f"the page you cited ({pg['title']}) does not state {p['n']:,} beds{said}", "the grounded page does not state that bed count")
        elsewhere = [pg for pg, v, _g, _t in checks if v["readable"] and v["has_name"] and not v["place"]]
        if elsewhere:
            return reject(f"the page you cited ({elsewhere[0]['title']}) names a hospital of that name, but not in {state_name}.",
                          "the grounded page is about a same-named hospital elsewhere")
        # a readable page that states this number of beds without naming the hospital is someone else's figure
        foreign = [pg for pg, v, _g, _t in checks if v["readable"] and not v["has_name"] and v["states_n"]]
        if foreign:
            return reject(f"the page you cited ({foreign[0]['title']}) does not name this hospital.", "the grounded page states that bed count but not the hospital")
        # nothing to confirm it on: a page the checker can't read, or one whose text as read shows neither the name nor
        # the number (often filled in by a script). Kept, labeled not page-confirmed, only when Google tied the page to
        # THIS hospital's own line of THIS answer
        backing = [(pg, v) for pg, v, g, tight in checks if g and tight]
        if not backing:
            return reject(f"no web page Google tied to this hospital's own line backs the number {p['n']:,}.",
                          "a bed count backed only by a support spanning other hospitals' lines")
        pg, v = backing[0]
        why = pg["why"] if not v["readable"] else "its text as the checker reads it shows neither the hospital's name nor the number, often a page filled in by a script"
        r.update(beds=p["n"], beds_basis="reported", beds_kind=p["kind"], source={"title": pg["title"], "url": pg["url"]}, checked=True,
                 page_confirmed=None, quote=None, note=f"Grounded in {pg['title']}; not confirmed on the page ({why}).", state="done")
        llm.note_check(SURFACE, True, "a bed count in a grounded web source (not confirmed on the page)")

    if work:
        try:
            await asyncio.wait_for(asyncio.gather(*[one(r, p, c) for r, p, c in work]), timeout=max(0.5, job.left()))
        except asyncio.TimeoutError:
            job.timed = True
            for r, _p, _c in work:
                if r["beds_basis"] is None and r["id"] not in rejected:
                    rejected[r["id"]] = "the page check did not finish in time."
    return rejected


async def _ask(job: _Job, pending: list[dict], state: str, feedback: dict[int, str], model: str, sem: asyncio.Semaphore) -> tuple[str, dict, bool, int]:
    """One grounded proposal: (model, result, used_fallback, ms)."""
    if not _take_call():
        job.why = job.why or "today's hospital-search budget is used"
        return model, llm.GROUNDED_EMPTY, True, 0
    async with sem:
        left = job.left()
        t = time.monotonic()
        try:
            res, off = await asyncio.wait_for(
                llm.complete_grounded(_prompt(state, pending, feedback), system=SYSTEM, surface=SURFACE, timeout=min(CALL_TIMEOUT_S, left),
                                      fallback=llm.GROUNDED_EMPTY, model=model, thinking=THINKING),
                timeout=min(CALL_TIMEOUT_S, left) + 1,
            )
        except asyncio.TimeoutError:
            job.timed = True
            res, off = llm.GROUNDED_EMPTY, True
    job.calls += 1
    return (res.get("model") or model), res, off, int((time.monotonic() - t) * 1000)


def _searched(a: tuple) -> bool:
    return not a[2] and bool(a[1].get("queries") or a[1].get("sources"))


async def _answers(job: _Job, pending: list[dict], state: str, feedback: dict[int, str], pair: list[str], sem: asyncio.Semaphore):
    """The answers to check, in order. The first model is asked alone; the second only when the first answered without
    searching (or not at all), or as a hedge when the first is still silent after HEDGE_S (each grounded call is billed,
    and every cascade starts this agent). An answer that searched is yielded as soon as it arrives; one from memory is
    held and yielded last (the checker rejects its figures, and the trace shows it). Closing the generator cancels
    whatever is still running."""
    items = list(pending)
    ask = lambda m: asyncio.ensure_future(_ask(job, items, state, feedback, m, sem))  # noqa: E731
    running = {ask(pair[0])}
    spare = list(pair[1:2])
    held = []
    try:
        while running:
            done, running = await asyncio.wait(running, timeout=HEDGE_S if spare else None, return_when=asyncio.FIRST_COMPLETED)
            if not done:  # the first model is slow: ask the second one too, check whichever searched first
                if job.left() >= MIN_CALL_S:
                    running.add(ask(spare.pop()))
                else:
                    spare.clear()
                continue
            for t in done:
                a = t.result()
                if _searched(a):
                    yield a
                    continue
                held.append(a)
                if spare and job.left() >= MIN_CALL_S:
                    running.add(ask(spare.pop()))
        for a in sorted(held, key=lambda a: a[2]):  # an answer (from memory) before no answer at all
            yield a
    finally:
        for t in running:
            t.cancel()


async def _batch(job: _Job, items: list[dict], state: str, sem: asyncio.Semaphore) -> dict[int, str]:
    """Propose -> check -> feedback -> revise for these hospitals. Each attempt asks the first model; the second only when
    the first answered from memory without searching (measured Sat evening: flash-lite ~2/3 of the time, the flash model
    ~1/3) or not at all, or as a hedge when the first is slow. Returns {id: why it was not kept} for the hospitals still
    open."""
    pending = list(items)
    feedback: dict[int, str] = {}
    last: dict[int, str] = {}
    searched_nf: dict[int, int] = {}  # NOT FOUND after a real search, per hospital (asked at most twice)
    for attempt in range(ATTEMPTS):
        if not pending:
            break
        if job.left() < MIN_CALL_S:
            job.timed = True
            for r in pending:
                last.setdefault(r["id"], "the run's time ran out before the search.")
            break
        for r in pending:
            r["state"] = "searching"
        arrivals = _answers(job, pending, state, feedback, MODELS[:2], sem)  # the model that searches more often leads
        kind = "revise" if attempt else "propose"
        rejected: dict[int, str] = {}
        answers: list = []
        j = -1
        async for model, res, off, ms in arrivals:
            open_ids = {r["id"] for r in pending if r["beds_basis"] is None}
            if not open_ids:
                break
            answers.append((model, res, off, ms))
            j += 1
            k = len(open_ids)
            if off:
                job.add("gemini", kind, "muted", L(f"No answer for {k} hospital{'s' if k != 1 else ''}", f"Sin respuesta para {k} hospital{'es' if k != 1 else ''}"),
                        L(f"{model} did not answer in time or is unavailable.", f"{model} no respondió a tiempo o no está disponible."), ms=ms)
                for i in open_ids:
                    rejected.setdefault(i, NO_ANSWER)
                continue
            queries = res.get("queries") or []
            searched = bool(queries or res.get("sources"))
            job.searches += len(queries)
            if res.get("suggestions_html") and len(job.suggestions) < 5:
                job.suggestions.append(res["suggestions_html"])
            parsed = _parse(res["text"], pending)
            if searched:
                verb = ("Searched Google again for", "Buscó de nuevo en Google") if attempt else ("Searched Google for", "Buscó en Google")
            else:
                verb = ("Answered without searching for", "Respondió sin buscar para")
            shown = [r for r in pending if r["id"] in open_ids]
            sp = {shown.index(r): parsed[i] for i, r in enumerate(pending) if r["id"] in open_ids and i in parsed}
            if j and not searched and not any(x["n"] is not None for x in sp.values()):
                continue  # the second model neither searched nor proposed a figure: nothing to check, nothing to show
            also = (" (the second model's answer, for what was still open)", " (la respuesta del segundo modelo, para lo que seguía abierto)") if j else ("", "")
            proposal = ("gemini", kind, "info",
                        L(f"{verb[0]} {k} hospital{'s' if k != 1 else ''}", f"{verb[1]} {k} hospital{'es' if k != 1 else ''}"),
                        L(f"{model} proposed{also[0]}: {_brief(shown, sp)}", f"{model} propuso{also[1]}: {_brief(shown, sp, True)}"), _calls(queries), ms)
            t = time.monotonic()
            got = await _judge(job, pending, parsed, res, searched, only=open_ids)
            ms2 = int((time.monotonic() - t) * 1000)
            en, es = [], []
            for r in shown:
                if r["id"] in got:
                    en.append(f"{r['name']}: not kept — {got[r['id']]}")
                    es.append(f"{r['name']}: descartado — {_es_reason(got[r['id']])}")
                elif r["page_confirmed"]:
                    en.append(f"{r['name']}: {r['beds']:,} kept — Google tied it to {r['source']['title']}, and that page gives it as this hospital's bed count.")
                    es.append(f"{r['name']}: {r['beds']:,} aceptado — Google lo vinculó a {r['source']['title']} y esa página lo da como las camas de este hospital.")
                else:
                    en.append(f"{r['name']}: {r['beds']:,} kept — Google tied it to {r['source']['title']}; not confirmed on the page.")
                    es.append(f"{r['name']}: {r['beds']:,} aceptado — Google lo vinculó a {r['source']['title']}; sin confirmar en la página.")
            kept = k - len(got)
            job.add(*proposal)  # the proposal and its verdict land together: parallel batches never split a pair
            job.add("engine", "verify", "holds" if not got else ("over" if not kept else "info"),
                    L(f"Checked in code: {kept} of {k} kept", f"Verificado en código: {kept} de {k} aceptados"), L(" · ".join(en), " · ".join(es)), ms=ms2)
            for i, why in got.items():  # the better answer's reason stands
                if i not in rejected or rejected[i] == NO_ANSWER or rejected[i].startswith("you answered from memory"):
                    rejected[i] = why
            if all(r["beds_basis"] is not None for r in pending):
                break  # everything is kept: don't wait for a hedged model still answering
        await arrivals.aclose()  # cancels a hedged model's call that is no longer needed
        still = [r for r in pending if r["beds_basis"] is None]
        for r in pending:
            if r["beds_basis"] is not None:
                last.pop(r["id"], None)
        for r in still:
            last[r["id"]] = rejected.get(r["id"], "the run ran out of time.")
        # what goes back: everything not kept, except a hospital a real search found nothing for twice
        nxt = []
        for r in still:
            if last[r["id"]] == NO_ANSWER and all(a[2] for a in answers):
                continue  # no model answered: asking again won't help
            if last[r["id"]] == "you answered NOT FOUND.":
                searched_nf[r["id"]] = searched_nf.get(r["id"], 0) + 1
                if searched_nf[r["id"]] >= 2:
                    continue
            nxt.append(r)
        if nxt and attempt < ATTEMPTS - 1 and job.left() >= MIN_CALL_S + 1:
            feedback = {r["id"]: _hint(last[r["id"]]) for r in nxt}
            job.add("engine", "feedback", "info", L(f"Sent {len(nxt)} back with the reasons", f"Devolvió {len(nxt)} con los motivos"),
                    L(" · ".join(f"{r['name']}: {last[r['id']]}" for r in nxt), " · ".join(f"{r['name']}: {_es_reason(last[r['id']])}" for r in nxt)))
        elif nxt and attempt < ATTEMPTS - 1:
            job.timed = True
        pending = nxt
    for r in items:
        if r["beds_basis"] is None:
            r["state"] = "queued"
    return last


def _hint(why: str) -> str:
    """The reason as it goes back to Gemini, with what to do next."""
    if why.startswith("you answered from memory"):
        return why + " Run a Google Search for this hospital now and answer only from what a result states."
    if why == "you answered NOT FOUND.":
        return why + " Try once more: search its state's hospital licensing records, CMS or the American Hospital Directory."
    return why + " Search again and give the figure a page states for this hospital, or NOT FOUND."


async def _run(job: _Job) -> None:
    code = job.code
    state = REGIONS[code]["name"]
    rows = job.rows
    try:
        n = len(rows)
        with_osm = sum(1 for r in rows if r["osm_beds"])
        job.add("engine", "info", "info",
                L(f"{n} hospital{'s' if n != 1 else ''} in the areas that lost power", f"{n} hospital{'es' if n != 1 else ''} en las zonas sin luz"),
                L(f"Each one's nearest substation on the synthetic model lost 60 % or more of its load. OpenStreetMap lists beds for {with_osm} of them; the agent looks each one up for the figure the hospital or a public registry reports, and code checks every figure.",
                  f"La subestación más cercana de cada uno perdió el 60 % o más de su carga en el modelo sintético. OpenStreetMap da camas para {with_osm}; el agente busca la cifra que publica el hospital o un registro público, y el código verifica cada cifra."))
        reused = []
        with _store_lock:
            for r in rows:
                f = _facts.get(_fact_key(code, r))
                if f:
                    r.update({k: copy.deepcopy(f[k]) for k in FACT_FIELDS}, state="done")
                    reused.append(r)
        if reused:
            job.add("engine", "info", "holds", L(f"{len(reused)} found in an earlier run, reused", f"{len(reused)} encontrados en una ejecución anterior, reutilizados"),
                    L(" · ".join(f"{r['name']}: {r['beds']:,} ({r['source']['title']})" for r in reused), " · ".join(f"{r['name']}: {r['beds']:,} ({r['source']['title']})" for r in reused)))
        missed = []
        if llm.configured():
            now = time.time()
            with _store_lock:
                for r in rows:
                    m = _misses.get(_fact_key(code, r)) if r["beds_basis"] is None else None
                    if m and now - float(m.get("at") or 0) <= MISS_TTL_S:
                        _settle(r, f"Not found: {m['why']} (searched in an earlier run in the last {MISS_TTL_S // 3600} hours; not searched again until then)")
                        missed.append((r, m["why"]))
        if missed:
            job.add("engine", "info", "muted",
                    L(f"{len(missed)} searched in an earlier run without a figure that passed: not searched again", f"{len(missed)} buscados en una ejecución anterior sin una cifra aceptada: no se buscan de nuevo"),
                    L(" · ".join(f"{r['name']}: {why}" for r, why in missed), " · ".join(f"{r['name']}: {_es_reason(why)}" for r, why in missed)))
        job.names = _dedupe_names(_state_names(code) + [_name_entry(r["id"], r["name"]) for r in rows])
        todo = [r for r in rows if r["beds_basis"] is None]
        if todo and (not llm.configured() or llm.usage()["remaining"] <= 0 or _calls_left() <= 0):
            job.by = "fallback"
            job.why = "no Gemini key" if not llm.configured() else "today's AI budget is used" if llm.usage()["remaining"] <= 0 else "today's hospital-search budget is used"
            for r in todo:
                _settle(r, "Not searched: Gemini is unavailable.")
            k = sum(1 for r in rows if r["beds_basis"] == "osm")
            job.add("engine", "result", "muted", L("Gemini is unavailable: OpenStreetMap's figures only", "Gemini no está disponible: solo las cifras de OpenStreetMap"),
                    L(f"{k} of {n} hospitals have a beds tag on OpenStreetMap (map data, not checked).", f"{k} de {n} hospitales tienen camas en OpenStreetMap (datos del mapa, sin verificar)."))
            return
        # a statewide incident: the emergency hospitals first, then the ones OpenStreetMap has no figure for
        todo.sort(key=lambda r: (not r["emergency"], r["osm_beds"] is not None))
        search, beyond = todo[:MAX_RESEARCH], todo[MAX_RESEARCH:]
        for r in beyond:
            _settle(r, f"Not searched: this incident has more than {MAX_RESEARCH} hospitals.")
        sem = asyncio.Semaphore(8)
        why: dict[int, str] = {}
        if search:
            for got in await asyncio.gather(*[_batch(job, search[i : i + BATCH], state, sem) for i in range(0, len(search), BATCH)]):
                why.update(got)
        if search and all(v == NO_ANSWER for v in why.values()) and len(why) == len(search):
            job.by, job.why = "fallback", "Gemini did not answer"
        for r in rows:
            if r["beds_basis"] is None:
                if r["id"] not in why:
                    job.timed = True
                reason = why.get(r["id"]) or "the run ran out of time."
                if reason not in TRANSIENT:
                    job.misses[r["id"]] = reason
                _settle(r, f"Not found: {reason}")
    except Exception:  # noqa: BLE001 - a background job always ends in a state the page can show
        log.exception("hospital agent failed")
        for r in rows:
            if r["beds_basis"] is None:
                _settle(r, "Not found: the agent stopped on an error.")
        job.why = "the agent stopped on an error"
    finally:
        job.elapsed = round(time.monotonic() - job.t0, 1)
        c = _counts(rows)
        if job.by == "gemini":
            job.add("engine", "result", "holds" if c["reported"] else "muted",
                    L(f"Done in {job.elapsed:.1f} s: {c['reported']} of {c['hospitals']} found as reported", f"Listo en {job.elapsed:.1f} s: {c['reported']} de {c['hospitals']} encontrados según lo publicado"),
                    L(f"About {c['beds_total']:,} beds in all ({c['beds_reported']:,} as reported, {c['beds_osm']:,} from OpenStreetMap); {c['not_found']} not found. {c['page_confirmed']} of the reported figures were also read on the source page itself. {job.calls} Gemini calls, {job.searches} Google searches.",
                      f"Unas {c['beds_total']:,} camas en total ({c['beds_reported']:,} según lo publicado, {c['beds_osm']:,} de OpenStreetMap); {c['not_found']} sin encontrar. {c['page_confirmed']} de las cifras publicadas se leyeron además en la propia página. {job.calls} llamadas a Gemini, {job.searches} búsquedas en Google."))
        job.status = "done"
        _remember(job)
        with job.pages_lock:
            job.pages.clear()  # the pages' text (up to a few MB each) isn't kept with the finished job
        job.grounded.clear()
        with _jobs_lock:
            if _by_set.get(job.key) == job.id:
                del _by_set[job.key]


def _remember(job: _Job) -> None:
    """Keep what passed (the facts), the hospitals a finished search found nothing for (the misses, MISS_TTL_S, so the
    next incident that reaches them doesn't pay for the same searches) and, when the whole run finished cleanly with
    Gemini, the set's answer."""
    now = time.time()
    clean = job.by == "gemini" and job.why is None and not job.timed
    with _store_lock:
        for r in job.rows:
            if r["beds_basis"] == "reported":
                _facts[_fact_key(job.code, r)] = {**{k: copy.deepcopy(r[k]) for k in FACT_FIELDS}, "at": now, "v": CHECK_V}
        if job.by == "gemini":
            for r in job.rows:
                why = job.misses.get(r["id"])
                if why and r["beds_basis"] != "reported":
                    _misses[_fact_key(job.code, r)] = {"at": now, "v": CHECK_V, "why": why}
        if clean and job.rows:
            _sets[job.key] = {"at": now, "v": CHECK_V, "region": job.code, "hospitals": copy.deepcopy(job.rows), "trace": copy.deepcopy(job.trace),
                              "elapsed_s": job.elapsed}
        _save_disk()


def _meta(code: str) -> dict:
    return {"region": code, "region_name": REGIONS[code]["name"], "assumption": ASSUMPTION, "cap_s": CAP_S, "surface": SURFACE,
            "osm": {"source": hospitals.SOURCE, "license_url": hospitals.LICENSE_URL}}


def _view(job: _Job) -> dict:
    rows = copy.deepcopy(job.rows)
    return {**_meta(job.code), "job": job.id, "status": job.status, "hospitals": rows, "trace": list(job.trace), "counts": _counts(rows),
            "by": job.by, "fallback": job.by == "fallback", "why": job.why, "cached": False, "cached_at": None,
            "elapsed_s": job.elapsed if job.elapsed is not None else round(time.monotonic() - job.t0, 1), "timed_out": job.timed,
            "calls": job.calls, "searches": job.searches, "suggestions": list(job.suggestions)}


def _set_view(code: str, hit: dict) -> dict:
    rows = copy.deepcopy(hit["hospitals"])
    return {**_meta(code), "job": None, "status": "done", "hospitals": rows, "trace": copy.deepcopy(hit["trace"]), "counts": _counts(rows),
            "by": "gemini", "fallback": False, "why": None, "cached": True, "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(hit["at"])),
            "elapsed_s": hit.get("elapsed_s"), "timed_out": False, "calls": 0, "searches": 0, "suggestions": []}


# ------------------------------------------------------------------------------------ routes
class AgentIn(BaseModel):
    region: str = Field("FL", max_length=4)
    load_factor: float = 1.0
    affected: dict[int, float] = Field(default_factory=dict)  # the cascade's `affected`: sub id -> MW of existing load lost


def _dark_hospitals(body: AgentIn) -> tuple[str, list[dict]]:
    code = region_code(body.region)
    if not math.isfinite(body.load_factor):
        raise HTTPException(status_code=422, detail="Load level must be a number")
    if len(body.affected) > MAX_AFFECTED:
        raise HTTPException(status_code=422, detail=f"At most {MAX_AFFECTED:,} substations")
    if not all(math.isfinite(v) and 0 <= v <= 1e6 for v in body.affected.values()):
        raise HTTPException(status_code=422, detail="Each substation's lost load must be a number of MW from 0 to 1,000,000")
    g = grid_at(body.load_factor, code)
    res = hospitals.status_for(code, g, {int(k): float(v) for k, v in body.affected.items()})
    return code, res["backup"]


@router.post("/api/hospitals/agent")
@limiter.limit("30/minute")
async def hospital_agent_start(request: Request, body: AgentIn):
    """Start the beds agent for the hospitals the cascade put on backup power (or return the finished answer)."""
    code, dark = await run_in_threadpool(_dark_hospitals, body)
    rows = [_row(h) for h in dark]
    key = _set_key(code, rows)
    if not rows:
        job = _Job(code, key, [])
        job.add("engine", "result", "muted", L("No hospital is in an area that lost power", "Ningún hospital está en una zona sin luz"))
        job.status, job.by, job.elapsed = "done", "engine", 0.0
        return {**_view(job), "job": None}
    with _store_lock:
        hit = _sets.get(key)
    if hit is not None and time.time() - float(hit.get("at") or 0) <= SET_TTL_S:
        return _set_view(code, hit)
    with _jobs_lock:
        _gc()
        jid = _by_set.get(key)
        job = _jobs.get(jid) if jid else None
        if job is None or job.status != "running":
            if sum(1 for j in _jobs.values() if j.status == "running") >= RUNNING_MAX:
                raise HTTPException(status_code=429, detail="The hospital agent is busy with other incidents: try again in a moment")
            job = _Job(code, key, rows)
            _jobs[job.id] = job
            _by_set[key] = job.id
            job.task = asyncio.get_running_loop().create_task(_run(job))
    return _view(job)


@router.get("/api/hospitals/agent/{job_id}")
@limiter.limit("240/minute")  # the page polls every 0.8 s and the venue shares one IP
async def hospital_agent_job(request: Request, job_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_-]{8,40}$")):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="This lookup is no longer available: run the cascade again")
    return _view(job)
