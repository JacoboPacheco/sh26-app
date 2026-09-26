"""The narrated play-by-play for Strengthen the grid: when the viewer presses "Watch it get built", the page leaves its
dashboard for a full-screen map and a presenter voice walks through the build in about a minute, whatever the
budget: the grid's weak points first, then the upgrades in at most four PACKAGES (Gemini groups them, the engine
checks the grouping), then the whole plan's answer. CLAUDE.md -> Decisions -> STRENGTHEN PLAY-BY-PLAY, STRENGTHEN IS
THE HEART, PRESENT V2 (framing: the problem is in today's grid), GEMINI MAX, MUTED, AI SURFACES.

POST /api/strengthen/narration {region, mw, load_factor, mode: firm|flexible, budget (USD, high end), lang: en|es, ai}
  -> a script shaped for the narration engine the deck uses (frontend useNarration): slides[{id, kind: intro|package|
     close, step_from, step, steps, package, headline{lang}, narration{lang: [{role, text, chars, cues, key}]},
     est_s{lang}, written_by{lang}}] plus packages[] (who, where, what it costs, the campuses it lets in), problems[]
     (the weak points that stop the next campuses, with their loading on today's grid), grouping{by, checks, ...}
     (who grouped the upgrades and what was checked), the facts every number comes from and the names that hold digits.
  409 while no finished study exists for that state, size and load level (it never starts one: LAZY), 422 bad body.

THE GROUPING. The paid campus steps the budget buys (each with the free campuses that ride along after it) are cut
into at most 4 packages, each a CONTIGUOUS run of steps, so the campuses-at-once count and the running cost after a
package are the engine's own numbers (never re-added by a model). Gemini (one call, a JSON schema, surface
"strengthen_narration") proposes the cuts, a short place-based name and why they belong together (same corridor,
same limit); code checks it: contiguous, every paid step exactly once, the package count for the number of steps,
every place a name says is one its upgrades touch (and every name different), no digits outside the facts, no real
utility or company names, short text in both languages. What fails goes back once with the reasons (proposal ->
checks -> feedback -> revision); a grouping that still fails, no key or no answer falls back to the ENGINE'S grouping
(cut where consecutive steps' upgrades jump furthest across the state, named from the dominant area), labeled so.

THE SCRIPT: an INTRO (how many fit today; what stops the next one framed as a weakness already in today's grid, with
its loading today), one slide per PACKAGE (its name, what gets raised by kind, its cost at the high end, how many
campuses fit at once after it) and a CLOSING (the whole plan's answer, the power plants' reserve line when it binds,
the synthetic model named). About 45-75 s spoken. Gemini writes the lines from a fact sheet whose numbers are already
in their spoken form; every line is checked (each digit one of the slide's facts, names that hold digits masked;
spelled numbers the data has; the cost, count and place said; no abbreviations; no real names); failures go back
once, a line that still fails keeps its template. With no key, no quota, or ai=false, templates run (labeled).
Florida's default and largest budgets (both campus types) are written ahead once unlock's warm study is in
(UNLOCK_WARM; NARRATE_WARM=0 skips). Every segment is registered with voice.py, so the page asks ElevenLabs for it by
key; the browser voice or captions alone are the page's fallbacks.

Everything describes the SYNTHETIC grid model (Breakthrough Energy / Texas A&M), not any utility's network; costs
are the high end of labeled estimates.
"""

import asyncio
import difflib
import hashlib
import itertools
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import Counter, OrderedDict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import grid as gridmod
import llm
import unlock
import voice
from bulletin import CHARS_PER_S, FORBIDDEN_LOCAL, STATE_ES, TRANSLATED_NAMES, mw_say, num, usd_say, usd_show, words
from grid import DEFAULT_REGION, REGIONS
from limiter import limiter

router = APIRouter(tags=["narrate"])
log = logging.getLogger("uvicorn.error")

VERSION = 4  # 4: a claims check on the grouping (kinds, a shared limit); template lines speak only the engine's reason
LANGS = ("en", "es")
SURFACE = "strengthen_narration"
AI_TIMEOUT_S = 10  # per socket read inside llm
AI_DEADLINE_S = 16  # the lines' whole call
GROUP_DEADLINE_S = 12  # the grouping's whole call (it runs first: the lines are written for its packages)
LINE_MAX = {"en": 300, "es": 360}  # a slide's spoken line, characters
TOTAL_MAX_S = 76.0  # the whole script, spoken (English pace); longer Gemini lines give way to their templates
HOLD_S = 0.8  # the narration engine's pause before the next slide
SCRIPTS_MAX = 64
AI_MAX = 32
GROUPS_MAX = 32
PKG_MAX = 4
NAME_MAX = {"en": 48, "es": 56}  # a package name, characters (the beat strip shows the English one; Spanish runs longer)
WHY_MAX = 140  # why its upgrades belong together, characters
SAME_PLACE_KM = 25.0  # the engine's grouping: consecutive steps whose upgrades sit closer than this are one place
UNEVEN_KM = 10.0  # ... and a cut must be worth this many km of jump per squared step of unevenness it costs
REVISE_MIN_S = 5.0  # a revision round (the failed lines, with the checks' reasons) is asked only with this much time left
GROUP_REVISE_MIN_S = 4.0
RETRY_S = 60.0  # a case where no Gemini line passed is asked again after this long, not on every request
RETRYABLE = ("Gemini unavailable", "every line failed its checks")
GROUP_RETRYABLE = ("Gemini unavailable",)  # a grouping that failed its checks isn't asked again (the answer is cached)
DEFAULT_BUDGET = 50e6  # the page opens at the largest budget stop at or under this (capacity.js DEFAULT_CAP_BUDGET)
WARM_WAIT_S = 900.0  # the warm-up waits this long for unlock's warm study, then gives up
WARM_POLL_S = 5.0
STOP = {  # why the search ended, as the end of a sentence
    "plants": ("the model's power plants can't supply another campus", "las centrales del modelo no pueden abastecer otro campus"),
    "no_fix": ("no line upgrade within five times its rating makes room for the next one", "ninguna mejora de línea dentro de cinco veces su capacidad hace sitio para el siguiente"),
    "cap": ("the search stops at its spending cap", "la búsqueda se detiene en su tope de gasto"),
    "max": ("the search stops at its campus limit", "la búsqueda se detiene en su límite de campus"),
    "time": ("the search ran out of time", "la búsqueda se quedó sin tiempo"),
}
ORD_EN = ["zeroth", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth"]
ORD_ES = ["", "primero", "segundo", "tercero", "cuarto", "quinto", "sexto", "séptimo", "octavo", "noveno", "décimo"]
LEAD = {"en": ("First up", "Next", "Last"), "es": ("Primero", "Después", "Por último")}
ONE_LEAD = {"en": "One package does it", "es": "Basta un paquete"}

_scripts: "OrderedDict[tuple, dict]" = OrderedDict()
_ai: "OrderedDict[tuple, tuple[dict, dict, float]]" = OrderedDict()  # (lines, meta, when)
_groups: "OrderedDict[tuple, tuple[list, dict, float, bool]]" = OrderedDict()  # (packages, trace, when, retry later?)
_ai_mutex = threading.Lock()  # the warm-up thread writes _ai and _groups too
_ai_locks: dict[tuple, asyncio.Lock] = {}
_group_locks: dict[tuple, asyncio.Lock] = {}


# ------------------------------------------------------------------------------------------ words
def ordinal(n: int, lang: str) -> str:
    """'third' / 'tercero'; past the table, '13th' / 'número 13'."""
    n = int(n)
    if lang == "en":
        if n < len(ORD_EN):
            return ORD_EN[n]
        return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    return ORD_ES[n] if 0 < n < len(ORD_ES) else f"campus {words(n, 'es')}"


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def join(items: list[str], lang: str) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (" and " if lang == "en" else " y ") + items[-1]


def half_up(x: float) -> int:
    """Whole units, a half rounded up: what the page prints (geo.js fmt = Math.round), so the voice and the plan row
    beside it say one figure (Python's round and f"{x:.0f}" send 234.5 to 234; the page shows 235)."""
    return int(math.floor(float(x) + 0.5))


def mva(x: float, lang: str = "en") -> str:
    """A rating as the page's plan prints it: '235' for 234.5 (half up, as Math.round); '1,000' / '1000'."""
    v = half_up(x)
    return f"{v:,}" if lang == "en" else num(v, "es")


def label_of(b: dict, lang: str) -> str:
    """'the Jacksonville 64 transformer' / 'el transformador de Jacksonville 64'; lines likewise, twins numbered."""
    label = str(b.get("label") or "")
    if lang == "en":
        return label or f"the {b.get('short') or 'line'}"
    m = re.match(r"^the (.+) transformer(?: \(unit (\d+)\))?$", label)
    if m:
        return f"el transformador de {m.group(1)}" + (f" (unidad {m.group(2)})" if m.group(2) else "")
    m = re.match(r"^the (.+) to (.+) line(?: \(circuit (\d+)\))?$", label)
    if m:
        return f"la línea de {m.group(1)} a {m.group(2)}" + (f" (circuito {m.group(3)})" if m.group(3) else "")
    return f"la instalación {b.get('short') or ''}".strip()


def kinds_phrase(projects: list[dict], lang: str) -> str:
    """'two lines and a transformer' / 'dos líneas y un transformador'."""
    lines = sum(1 for p in projects if p.get("kind") != "transformer")
    tr = len(projects) - lines
    parts = []
    if lang == "en":
        if lines:
            parts.append("a line" if lines == 1 else f"{words(lines, 'en')} lines")
        if tr:
            parts.append("a transformer" if tr == 1 else f"{words(tr, 'en')} transformers")
    else:
        if lines:
            parts.append("una línea" if lines == 1 else f"{words(lines, 'es', fem=True)} líneas")
        if tr:
            parts.append("un transformador" if tr == 1 else f"{words(tr, 'es', before_noun=True)} transformadores")
    return join(parts, lang)


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------------------------------------ facts
def num_forms(v: float) -> set[str]:
    """Every way a fact's value may be printed (the deck's rule: rounded, scaled to thousands/millions/billions)."""
    a = abs(float(v))
    dec = lambda x, d: (f"{x:.{d}f}".rstrip("0").rstrip(".") if d else f"{x:.0f}")  # noqa: E731
    out = {str(int(round(a))), str(half_up(a)), str(int(a)), dec(a, 1), f"{a:.0f}"}
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


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NUMWORDS = {
    "en": set("two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
              "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds thousand thousands "
              "twice double doubled triple tripled half halved quarter third thirds times dozen dozens "
              "second fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split()),
    "es": set("dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis diecisiete dieciocho "
              "diecinueve veinte treinta cuarenta cincuenta sesenta setenta ochenta noventa cien ciento cientos "
              "doble duplica triple triplica mitad tercio tercios veces docena docenas "
              "segundo segunda tercero tercera tercer cuarto cuarta quinto quinta sexto sexta séptimo séptima octavo octava "
              "noveno novena décimo décima".split()),
}


def number_words(text: str, lang: str) -> set[str]:
    toks = set(re.findall(r"[a-záéíóúñü]+", text.lower()))
    return {t for t in toks if t in _NUMWORDS[lang] or (lang == "es" and t.startswith("veinti"))}


class Facts:
    """The fact sheet: every number a line may say, and the names that hold digits ('Jacksonville 64')."""

    def __init__(self):
        self.rows: list[dict] = []
        self.names: set[str] = set()

    def add(self, key: str, label: str, value, unit: str = "", slide: str | None = None) -> None:
        if value is None:
            return
        self.rows.append({"key": key, "label": label, "value": value, "unit": unit, "slide": slide})

    def name(self, *items) -> None:
        for s in items:
            if s and isinstance(s, str):
                self.names.add(s.strip())

    def branch(self, b: dict | None) -> None:
        if not b:
            return
        self.name(b.get("short"), b.get("where"), unlock._title((b.get("from") or {}).get("name", "")),
                  unlock._title((b.get("to") or {}).get("name", "")), (b.get("from") or {}).get("area"), (b.get("to") or {}).get("area"))
        for lang in LANGS:
            lab = label_of(b, lang)
            self.name(re.sub(r"^(the|el|la) ", "", lab))
        m = re.match(r"^the (.+) to (.+) line", str(b.get("label") or "")) or re.match(r"^the (.+) transformer", str(b.get("label") or ""))
        if m:
            self.name(*m.groups())

    def allowed(self, slide: str | None) -> set[str]:
        out: set[str] = set()
        for f in self.rows:
            if f["slide"] in (None, slide) and isinstance(f["value"], (int, float)) and not isinstance(f["value"], bool) and math.isfinite(f["value"]):
                out |= num_forms(f["value"])
        return out

    def known_words(self, slide: str | None, lang: str) -> set[str]:
        out: set[str] = set()
        for f in self.rows:
            v = f["value"]
            if f["slide"] in (None, slide) and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 99 and float(v).is_integer():
                n = int(v)
                out |= {words(n, lang), words(n, lang, fem=True), words(n, lang, before_noun=True), ordinal(n, lang)}
                if lang == "es" and n == 3:
                    out |= {"tercer", "tercera"}
                if lang == "es" and 0 < n < len(ORD_ES):
                    out.add(ORD_ES[n][:-1] + "a")
            if f["slide"] in (None, slide) and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 1000:
                out.add("thousand" if lang == "en" else "mil")  # "one thousand megawatts" says a fact's own value
        return {w for x in out for w in re.findall(r"[a-záéíóúñü]+", x.lower())}

    def mask(self, text: str) -> str:
        for n in sorted(self.names, key=len, reverse=True):
            if n:
                text = re.sub(re.escape(n), "Placename", text, flags=re.IGNORECASE)
        return re.sub(r"\b(circuit|unit|circuito|unidad) \d+\b", "twin", text, flags=re.IGNORECASE)

    def public(self) -> list[dict]:
        return [{k: f[k] for k in ("key", "label", "value", "unit", "slide")} for f in self.rows]


def check_numbers(text: str, facts: Facts, slide: str | None) -> tuple[bool, str | None, int]:
    """Every digit in `text` is one of the facts (names masked): the deck's number check."""
    masked = facts.mask(text)
    m = FORBIDDEN_LOCAL.search(masked)
    if m:
        return False, f"forbidden: {m.group(0)!r}", 0
    allowed = facts.allowed(slide)
    toks = [t.replace(",", "").rstrip(".") for t in _NUM.findall(masked)]
    for t in toks:
        norm = (f"{float(t):.2f}".rstrip("0").rstrip(".")) if "." in t else t
        if t not in allowed and norm not in allowed:
            return False, f"number not in the facts: {t}", len(toks)
    return True, None, len(toks)


# ------------------------------------------------------------------------------------------ the plan
class Plan:
    """One study's capacity search, read for one mode and budget."""

    def __init__(self, key: tuple, result: dict, mode: str, budget: float):
        self.code, self.mw, self.lf = key
        self.key = key
        self.cap = result["capacity"]
        self.m = self.cap[mode]
        self.mode = mode
        self.flex = mode == "flexible"
        self.steps: list[dict] = self.m.get("steps") or []
        self.today = int(self.m.get("today") or 0)
        # what the budget buys: every step whose running total (high end) fits, free steps along (the page's capWithin)
        n = 0
        for st in self.steps:
            if st["cum_cost"]["high"] > budget + 0.5:
                break
            n = st["n"]
        self.bought = max(n, self.today)
        self.cost = self.steps[self.bought - 1]["cum_cost"] if self.bought > 0 else {"low": 0, "high": 0}
        pl = self.cap.get("plants") or {}
        self.plants_n = pl.get("campuses_flexible" if self.flex else "campuses_firm")
        self.reserve = pl.get("reserve_pct", 15.0)
        self.state = REGIONS.get(self.code, {}).get("name", self.code)
        self.state_es = STATE_ES.get(self.state, self.state)
        self.first_block = self.m.get("first_block")
        # the fingerprint of what is narrated (a re-run study with other numbers gets its own script)
        self.fp = hashlib.sha1(json.dumps([self.steps[: self.bought + 1], self.today, self.plants_n, self.m.get("stop")],
                                          sort_keys=True, default=str).encode()).hexdigest()[:16]
        self.gkey = (self.key, self.mode, self.bought, self.fp)

    def size(self, lang: str, adj: bool = True) -> str:
        return mw_say(self.mw, lang, adj=adj)

    def units(self) -> list[dict]:
        """The paid steps the budget buys, in the engine's order, each with the free campuses that fit after it."""
        out: list[dict] = []
        for st in self.steps[self.today: self.bought]:
            if not st["free"] or not out:
                out.append({"main": st, "steps": [st]})
            else:
                out[-1]["steps"].append(st)
        return out

    def binds(self) -> bool:
        """The power plants (with their reserve kept) supply fewer campuses than the budget connects."""
        return self.plants_n is not None and self.bought > 0 and self.plants_n < self.bought

    def next_block(self) -> dict | None:
        fb = self.first_block
        return fb if fb and fb.get("at_campus") == self.today + 1 else None


def majority(b: dict | None) -> dict | None:
    """A limit that stopped a campus at most of the sites tried (the page's blockOf): 'what stops it'."""
    return b if b and b.get("of") and int(b.get("blocks") or 0) * 2 >= int(b["of"]) else None


def pkg_range(u: int) -> tuple[int, int]:
    """How many packages a build of `u` paid steps may have: 1-2 -> 1, 3-5 -> 2-3, more -> 3-4."""
    if u <= 0:
        return 0, 0
    if u <= 2:
        return 1, 1
    if u <= 5:
        return 2, 3
    return 3, PKG_MAX


def engine_k(u: int) -> int:
    return 0 if u <= 0 else 1 if u <= 2 else 2 if u <= 4 else 3 if u <= 8 else PKG_MAX


def _mid(pj: dict) -> tuple[float, float]:
    m = pj.get("mid")
    if m:
        return float(m[0]), float(m[1])
    return (float(pj["from"]["lat"]) + float(pj["to"]["lat"])) / 2, (float(pj["from"]["lon"]) + float(pj["to"]["lon"])) / 2


def center_of(steps: list[dict]) -> tuple[float, float]:
    pts = [_mid(pj) for st in steps for pj in st["projects"]] or [(float(st["site"]["lat"]), float(st["site"]["lon"])) for st in steps]
    return sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts)


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def projects_of(steps: list[dict]) -> list[dict]:
    """Every line and transformer the steps raise, once: its rating before the first raise, after the last."""
    by: dict = {}
    for st in steps:
        for pj in st["projects"]:
            b = pj["branch_id"]
            if b in by:
                by[b] = {**by[b], "rating_after_mva": pj["rating_after_mva"]}
            else:
                by[b] = {**pj, "first_step": st["n"]}
    return list(by.values())


def areas_of(pj: dict) -> list[str]:
    return [a for a in dict.fromkeys([(pj.get("from") or {}).get("area"), (pj.get("to") or {}).get("area")]) if a] or ([pj["where"]] if pj.get("where") else [])


def area_weights(projects: list[dict]) -> list[tuple[str, float]]:
    """The places a package's upgrades touch, heaviest (by cost) first."""
    w: dict[str, float] = {}
    for pj in projects:
        areas = areas_of(pj)
        share = max(float(pj["cost"]["high"]), 1.0) / max(1, len(areas))
        for a in areas:
            w[a] = w.get(a, 0.0) + share
    return sorted(w.items(), key=lambda x: -x[1])


def make_package(p: Plan, units: list[dict], a: int, b: int, i: int) -> dict:
    """Package i: units a..b (0-based, inclusive). Its counts and running cost are the engine's own numbers."""
    us = units[a: b + 1]
    steps = [st for u in us for st in u["steps"]]
    first, last = steps[0]["n"], steps[-1]["n"]
    before = p.steps[first - 2]["cum_cost"] if first >= 2 else {"low": 0, "high": 0}
    after = steps[-1]["cum_cost"]
    projs = projects_of(steps)
    lines = sum(1 for x in projs if x.get("kind") != "transformer")
    pk = {
        "i": i, "id": f"pkg-{i}", "units": [a + 1, b + 1], "steps": [st["n"] for st in steps], "paid": [u["main"]["n"] for u in us],
        "first": first, "last": last,
        "cost_low": round(after["low"] - before["low"]), "cost_high": round(after["high"] - before["high"]), "cum_high": round(after["high"]),
        "upgrades": len(projs), "lines": lines, "transformers": len(projs) - lines,
        "areas": [x for x, _ in area_weights(projs)][:4],
        "_projects": projs, "_steps": steps,
    }
    # the engine's own reason, from the data: a template line speaks this one (never Gemini's, so a line labeled
    # "template" holds no model text); the card shows the grouper's reason, checked by _check_grouping
    pk["_why_engine"] = why_engine(pk)
    return pk


def public_pkg(pk: dict) -> dict:
    return {k: v for k, v in pk.items() if not k.startswith("_")}


# ------------------------------------------------------------------------------------------ place words
_TOK = re.compile(r"[A-Za-zÀ-ÿ]+")
GENERIC = {_fold(w) for w in (
    "the a an and of around near north south east west central northern southern eastern western northeast northwest "
    "southeast southwest upper lower inner outer coast coastal corridor corridors loop ring grid package packages "
    "line lines transformer transformers upgrade upgrades station stations substation substations both these its it "
    "same limit links link hub hubs belt again first next last grid network area areas zone zones region backbone "
    "reinforcement reinforcements expansion expansions upgrade tie ties interconnection capacity core cluster build "
    "el la los las de del y en torno cerca norte sur este oeste centro central corredor paquete linea lineas "
    "transformador transformadores mejora mejoras subestacion subestaciones otra vez ambos mismo limite red redes zona "
    "zonas region regiones refuerzo refuerzos ampliacion ampliaciones enlace enlaces nodo nodos interconexion "
    "capacidad conexion conexiones eje nucleo obra obras "
    # more generic words Gemini writes in a name (a Spanish first draft said "Actualizaciones en Orlando y Jacksonville")
    "actualizacion actualizaciones modernizacion modernizaciones transmision tramo tramos fase fases lote lotes "
    "cable cables tendido plan planes corredores "
    "transmission stretch stretches phase phases batch works cable cables span spans"
).split()}


def place_tokens(items) -> set[str]:
    out: set[str] = set()
    for s in items:
        if s:
            out |= {_fold(t) for t in _TOK.findall(str(s))}
    return out - GENERIC


def own_places(pk: dict) -> set[str]:
    """The place words a package's upgrades touch: their towns and substations (no line or transformer words)."""
    return place_tokens(x for pj in pk["_projects"] for x in (pj.get("where"), (pj.get("from") or {}).get("area"), (pj.get("to") or {}).get("area"),
                                                              unlock._title((pj.get("from") or {}).get("name", "")),
                                                              unlock._title((pj.get("to") or {}).get("name", ""))))


def limit_places(pk: dict) -> set[str]:
    """The place words of the limits that stopped its campuses (a reason may name them)."""
    bs = [st.get("blocked_by") for st in pk["_steps"] if st.get("blocked_by")]
    return place_tokens(x for b in bs for x in (b.get("where"), (b.get("from") or {}).get("area"), (b.get("to") or {}).get("area"),
                                                 unlock._title((b.get("from") or {}).get("name", "")), unlock._title((b.get("to") or {}).get("name", ""))))


def place_problem(text: str, own: set[str], extra: set[str], need_own: bool, prose: bool) -> str | None:
    """Every capitalized word that isn't a generic word must be one of the package's places (a reason's sentence
    openers are exempt); a name must say at least one of its own places."""
    toks: list[str] = []
    if prose:
        for sent in re.split(r"(?<=[.!?;:])\s+", text):
            toks += _TOK.findall(sent)[1:]
    else:
        toks = _TOK.findall(text)
    for t in toks:
        f = _fold(t)
        if t[:1].isupper() and f not in GENERIC and f not in own and f not in extra:
            return f"names a place its upgrades don't touch: {t}"
    if need_own and not any(_fold(t) in own for t in _TOK.findall(text)):
        return "names no place its upgrades touch"
    return None


def _real_names(text: str) -> str | None:
    m = FORBIDDEN_LOCAL.search(text)
    if m:
        return m.group(0)
    try:
        rx = llm._names_rule()[0]  # the analyst's list of real utilities and grid operators (read only)
        m = rx.search(text)
        if m:
            return m.group(0)
        m = llm._GRIDLOCK_NAMES.search(text)
        if m:
            return m.group(0)
    except Exception:  # noqa: BLE001 - the local list above still ran
        pass
    return None


# ------------------------------------------------------------------------------------------ the engine's grouping
def _noun(pk: dict) -> tuple[str, str]:
    if pk["transformers"] and not pk["lines"]:
        return "transformers", "Transformadores"
    if pk["lines"] and not pk["transformers"]:
        return "lines", "Líneas"
    return "upgrades", "Mejoras"


def why_engine(pk: dict) -> dict:
    blocks = [majority(st.get("blocked_by")) for st in pk["_steps"]]
    blocks = [b for b in blocks if b]
    areas = pk["areas"][:2] or [pk["_steps"][0]["site"]["area"]]
    if blocks:
        top, n = Counter(b["branch_id"] for b in blocks).most_common(1)[0]
        b = next(x for x in blocks if x["branch_id"] == top)
        if n >= 2 and n >= len(pk["paid"]):  # "these campuses": every campus it pays for (the claims check's rule)
            return {"en": f"The same limit stopped these campuses: {label_of(b, 'en')}.",
                    "es": f"El mismo límite frenó estos campus: {label_of(b, 'es')}."}
        if len(pk["paid"]) == 1:
            return {"en": f"{cap(label_of(b, 'en'))} stopped this campus; these upgrades make room for it.",
                    "es": f"{cap(label_of(b, 'es'))} frenó este campus; estas mejoras le hacen sitio."}
    pts = [_mid(pj) for pj in pk["_projects"]]
    c = center_of(pk["_steps"])
    if pts and max(km(c, q) for q in pts) < 60:
        return {"en": f"Its upgrades sit close together, around {join(areas, 'en')}.",
                "es": f"Sus mejoras están juntas, en torno a {join(areas, 'es')}."}
    return {"en": f"The next stretch of the build, around {join(areas, 'en')}.",
            "es": f"El siguiente tramo de la obra, en torno a {join(areas, 'es')}."}


def name_engine(pkgs: list[dict]) -> None:
    """Each package named from its dominant places (by cost), every name different."""
    used: set[str] = set()
    for pk in pkgs:
        ws = area_weights(pk["_projects"])
        areas = [a for a, _ in ws] or [pk["_steps"][0]["site"]["area"]]
        cands = [[areas[0]]]
        if len(areas) > 1:
            # a second place that carries real weight is named too ("Orlando and Jacksonville transformers")
            cands = [[areas[0], areas[1]], [areas[0]]] if ws[1][1] >= 0.4 * ws[0][1] else cands
            cands += [[areas[0], areas[1]], [areas[1]]]
        if len(areas) > 2:
            cands += [[areas[0], areas[2]], [areas[1], areas[2]], [areas[2]]]
        en_noun, es_noun = _noun(pk)
        pick = next((c for c in cands if f"{join(c, 'en')} {en_noun}".lower() not in used), None)
        again = pick is None
        pick = pick or cands[0]
        en = f"{join(pick, 'en')} {en_noun}" + (" again" if again else "")
        es = f"{es_noun} de {join(pick, 'es')}" + (", otra vez" if again else "")
        used.add(en.lower())
        pk["name"] = {"en": en, "es": es}
        pk["why"] = why_engine(pk)


def engine_grouping(p: Plan, units: list[dict]) -> list[dict]:
    """Cut the ordered steps into engine_k(U) runs where consecutive steps' upgrades jump furthest across the state
    (jumps under SAME_PLACE_KM count as none), without making the packages very uneven (UNEVEN_KM)."""
    u = len(units)
    if not u:
        return []
    k = engine_k(u)
    cuts: tuple[int, ...] = ()
    if k > 1:
        centers = [center_of(x["steps"]) for x in units]
        jump = [km(centers[i], centers[i + 1]) for i in range(u - 1)]
        jump = [d if d >= SAME_PLACE_KM else 0.0 for d in jump]
        even = u / k
        best = None
        for c in itertools.combinations(range(u - 1), k - 1):  # at most C(39, 3): a few thousand, instant
            edges = (0, *(x + 1 for x in c), u)
            sizes = [edges[j + 1] - edges[j] for j in range(k)]
            score = sum(jump[x] for x in c) - UNEVEN_KM * sum((z - even) ** 2 for z in sizes)
            if best is None or score > best[0] + 1e-9:
                best = (score, c)
        cuts = best[1]
    bounds, a = [], 0
    for c in cuts:
        bounds.append((a, c))
        a = c + 1
    bounds.append((a, u - 1))
    pkgs = [make_package(p, units, x, y, i + 1) for i, (x, y) in enumerate(bounds)]
    name_engine(pkgs)
    return pkgs


# ------------------------------------------------------------------------------------------ Gemini's grouping
CHECK_IDS = ("contiguous", "cover", "count", "places", "claims", "numbers", "names", "text")

# what a name or a reason claims about its upgrades, checked against the package: a kind ("Jacksonville lines" must
# raise a line); a limit it names (it stopped one of the package's campuses, or the package raises it); a shared limit
# ("the same transformer stopped them": one limit stopped two of its campuses); "both" (the package lets in two) and
# "all / every / each" (one limit stopped every paid campus in it)
_KIND_IN = {
    "line": re.compile(r"\b(?:lines?|l[ií]neas?)\b", re.I),
    "transformer": re.compile(r"\b(?:transformers?|transformador(?:es)?)\b", re.I),
}
_SAME_CLAIM = {
    "en": re.compile(r"\b(?:same|shared?|common|single)\b[^.;:]{0,30}?\b(limit|line|transformer|constraint|bottleneck|element)s?\b", re.I),
    "es": re.compile(r"\b(?:mism[oa]s?|compart\w*|com[uú]n|[uú]nic[oa]s?)\b[^.;:]{0,30}?\b(l[ií]mite|l[ií]nea|transformador|restricci[oó]n|cuello)\w*", re.I),
}
_CLAIM_KIND = {"line": "line", "linea": "line", "transformer": "transformer", "transformador": "transformer"}
_UNIT_WORD = re.compile(r"\b(?:units?|unidad(?:es)?)\b", re.I)  # the prompt numbers the steps as units; the viewer sees campuses
_BOTH = {"en": re.compile(r"\bboth\b", re.I), "es": re.compile(r"\bamb[oa]s\b", re.I)}
_EVERY = {"en": re.compile(r"\b(?:all|every|each)\b", re.I), "es": re.compile(r"\b(?:tod[oa]s|cada)\b", re.I)}
# "these campuses" as the claim's subject (not "one of these campuses"): the claim is about every campus it pays for
_THESE = {"en": re.compile(r"(?<!of )\b(?:these|those)\s+campuses\b", re.I), "es": re.compile(r"(?<!de )\b(?:estos|esos)\s+campus\b", re.I)}
_STOP_VERB = {
    "en": re.compile(r"\b(?:stop|stops|stopped|limit|limits|limited|held back|holds back|hold back|block|blocks|blocked|capped|constrained|hit|hits|encounter\w*|face[sd]?|met|meet)\b", re.I),
    "es": re.compile(r"\b(?:fren\w*|limitad\w*|limit[oó]|limitan|bloque\w*|detuv\w*|choca\w*|toparon|topan|encontrar\w*)\b", re.I),
}


def check_labels(u: int) -> dict:
    lo, hi = pkg_range(u)
    steps = f"{u} paid {'step' if u == 1 else 'steps'}"
    return {
        "contiguous": "Each package is a run of consecutive campus steps",
        "cover": "Every paid step is in exactly one package",
        "count": (f"One package for {steps}" if hi == 1 else f"{lo} to {hi} packages for {steps}"),
        "places": "Each name says only places its upgrades touch; every name different",
        "claims": "Names and reasons match the data: lines or transformers as raised, and every limit named stopped the campuses it says",
        "numbers": "No numbers outside the engine's facts",
        "names": "No real utility or company names",
        "text": "Short names and reasons, in English and in Spanish",
    }


def _mask_all(p: Plan) -> Facts:
    """Every name that holds digits in this plan (the grouping's number check masks them)."""
    f = Facts()
    for st in p.steps:
        f.name(st["site"]["area"])
        f.branch(st.get("blocked_by"))
        for pj in st["projects"]:
            f.branch(pj)
    f.name(p.state, p.state_es)
    return f


_EN_WORDS = re.compile(r"\b(the|and|of|lines|line|transformers|transformer|upgrades|with|same|stopped|around|near)\b", re.I)
_ES_MARKS = (" el ", " la ", " los ", " las ", " de ", " del ", " que ", " se ", "ción", "ñ", " y ", "transformador", "línea", "linea", "mejora")


def _kind(b: dict) -> str:
    return "transformer" if b.get("kind") == "transformer" else "line"


def _dash(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[‐‑‒–—-]", "-", _fold(s or ""))).strip()


def plan_limits(p: Plan) -> list[dict]:
    """Every limit that stopped a campus anywhere in the plan, once (a reason may only name one of these)."""
    out: dict = {}
    for st in p.steps:
        b = st.get("blocked_by")
        if b and b.get("branch_id") is not None:
            out.setdefault(b["branch_id"], b)
    return list(out.values())


def limit_forms(b: dict) -> set[str]:
    """The ways a reason may name a limit: its short name, its label in both languages, with or without its circuit."""
    raw = {b.get("short") or ""}
    for lang in LANGS:
        raw.add(re.sub(r"^(the|el|la) ", "", label_of(b, lang)))
    raw |= {re.sub(r"\s*\((circuit|circuito|unit|unidad) \d+\)$", "", x) for x in list(raw)}
    return {_dash(x) for x in raw if x and len(x) >= 6}


def shared_limits(pk: dict) -> dict:
    """{branch_id: 'line' | 'transformer'} of each limit that stopped at least two of the package's campuses (the
    same rule as why_engine's "the same limit stopped these campuses")."""
    blocks = [b for b in (majority(st.get("blocked_by")) for st in pk["_steps"]) if b]
    n = Counter(b["branch_id"] for b in blocks)
    return {b["branch_id"]: _kind(b) for b in blocks if n[b["branch_id"]] >= 2}


def claim_problem(pk: dict, limits: list[dict], name: str, why: str, lang: str) -> str | None:
    """What a name or a reason says that this package's data doesn't (None: nothing). A name's kind must be one it
    raises. A limit the reason names must have stopped one of its campuses or be raised in it. "The same limit /
    line / transformer" needs one limit (of that kind) that stopped two of its campuses; "both" needs a package of two
    campuses; "all / every / each" about a limit needs one that stopped every paid campus in it."""
    if name and _KIND_IN["line"].search(name) and not pk["lines"]:
        return f"the name {name!r} says lines, but the package raises only transformers"
    if name and _KIND_IN["transformer"].search(name) and not pk["transformers"]:
        return f"the name {name!r} says transformers, but the package raises only lines"
    if not why:
        return None
    text = _dash(why)
    named = [b for b in limits if any(f in text for f in limit_forms(b))]
    stopped = Counter(b["branch_id"] for b in (majority(st.get("blocked_by")) for st in pk["_steps"]) if b)
    touched = {pj["branch_id"] for pj in pk["_projects"]} | {st["blocked_by"]["branch_id"] for st in pk["_steps"] if st.get("blocked_by")}
    for b in named:
        if b["branch_id"] not in touched:
            return f"the reason names {b.get('short')}, which stopped none of its campuses and isn't raised in it"
    kinds = {b["branch_id"]: _kind(b) for b in limits}
    pool = [b["branch_id"] for b in named] or list(stopped)  # the limit(s) the claim is about
    paid = set(pk["paid"])
    paid_by = [(majority(st.get("blocked_by")) or {}).get("branch_id") for st in pk["_steps"] if st["n"] in paid]
    need = len(paid_by)
    same = _SAME_CLAIM[lang].search(why)
    both = _BOTH[lang].search(why)
    every = _EVERY[lang].search(why)
    these = _THESE[lang].search(why)
    whole = both or every or these  # the claim is about every campus it pays for
    if same:
        kind = _CLAIM_KIND.get(_fold(same.group(1)))
        hits = [x for x in pool if stopped[x] >= 2]
        if not hits:
            return f"the reason says {same.group(0)!r}, but no single limit stopped two of its campuses"
        if kind and not any(kinds.get(x) == kind for x in hits):
            return f"the reason says {same.group(0)!r}, but the limit its campuses share is a {'line' if kind == 'transformer' else 'transformer'}"
        if whole and not any(stopped[x] >= need for x in hits):
            return (f"the reason says {same.group(0)!r} of {whole.group(0)!r}, but no single limit stopped every campus it pays for "
                    "(say which limit stopped most of them, or give another reason)")
    if both and len(pk["steps"]) != 2 and need != 2:
        return f"the reason says {both.group(0)!r}, but the package lets in {len(pk['steps'])} campuses"
    if named and whole and _STOP_VERB[lang].search(why):
        ids = {b["branch_id"] for b in named}
        if not all(x in ids for x in paid_by):
            return (f"the reason says {whole.group(0)!r} met {', '.join(str(b.get('short')) for b in named)}, but not every campus it pays for "
                    "was stopped by it (say which one it stopped, or name every limit)")
    if whole and not named and _STOP_VERB[lang].search(why):
        # "limits in Orlando stopped these campuses": every campus it pays for was stopped by a limit in a place it says
        at = {st["n"]: majority(st.get("blocked_by")) for st in pk["_steps"] if st["n"] in paid}
        where = {n: place_tokens([b.get("where"), (b.get("from") or {}).get("area"), (b.get("to") or {}).get("area"),
                                   unlock._title((b.get("from") or {}).get("name", "")), unlock._title((b.get("to") or {}).get("name", ""))])
                 for n, b in at.items() if b}
        said = place_tokens([why]) & (own_places(pk) | set().union(*where.values()))
        off = [n for n, w in where.items() if said and not (w & said)]
        if off:
            b = at[off[0]]
            return (f"the reason puts the limits that stopped {whole.group(0)!r} in {', '.join(sorted(said))}, but campus {off[0]} was stopped by "
                    f"{b.get('short')} (name that place too, or say 'most of these campuses')")
    return None


def check_grouping(p: Plan, units: list[dict], data, note: bool = True) -> tuple[list[dict] | None, list[dict], list[str]]:
    """Gemini's grouping through the checks: (packages or None, [{id, label, ok, detail}], reasons for a revision).
    Gemini's grouping counts in the AI panel's checks (note); the engine's own run of the same code doesn't."""
    pkgs, checks, reasons = _check_grouping(p, units, data)
    if note:
        for c in checks:
            llm.note_check(SURFACE, c["ok"], f"the upgrade grouping: {c['detail']}")
    return pkgs, checks, reasons


def _check_grouping(p: Plan, units: list[dict], data) -> tuple[list[dict] | None, list[dict], list[str]]:
    u = len(units)
    lo, hi = pkg_range(u)
    res = {c: None for c in CHECK_IDS}  # None = passed; else the first reason
    reasons: list[str] = []

    def fail(cid: str, why: str) -> None:
        if res[cid] is None:
            res[cid] = why
        reasons.append(why)

    def checks() -> list[dict]:
        labels = check_labels(u)
        return [{"id": c, "label": labels[c], "ok": res[c] is None, "detail": res[c]} for c in CHECK_IDS]

    rows = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        fail("cover", "the answer has no packages")
        return None, checks(), reasons
    spans = []
    for r in rows:
        try:
            a, b = int(r.get("first_unit")), int(r.get("last_unit"))
        except (TypeError, ValueError, AttributeError):
            fail("contiguous", "a package without whole first_unit and last_unit numbers")
            continue
        spans.append((a, b, r))
    spans.sort(key=lambda x: (x[0], x[1]))
    expect = 1
    for a, b, _r in spans:
        if a > b:
            fail("contiguous", f"a package runs backwards (units {a} to {b})")
        elif a > expect:
            fail("cover", f"units {expect} to {a - 1} are in no package")
        elif a < expect:
            fail("cover", f"unit {a} is in two packages")
        if b > u or a < 1:
            fail("cover", f"a package names unit {max(a, b) if b > u else a}, but there are only {u}")
        expect = max(expect, b + 1)
    if expect <= u:
        fail("cover", f"units {expect} to {u} are in no package")
    if not lo <= len(spans) <= hi:
        fail("count", f"{len(spans)} packages: use between {lo} and {hi}")
    if res["contiguous"] or res["cover"] or res["count"]:
        return None, checks(), reasons

    pkgs = [make_package(p, units, a - 1, b - 1, i + 1) for i, (a, b, _r) in enumerate(spans)]
    mask = _mask_all(p)
    limits = plan_limits(p)
    state = place_tokens([p.state, p.state_es])
    seen: dict[str, set] = {lang: set() for lang in LANGS}
    for pk, (_a, _b, r) in zip(pkgs, spans):
        own, lim = own_places(pk), limit_places(pk)
        pk["name"], pk["why"] = {}, {}
        for lang in LANGS:
            name = _clean(r.get(f"name_{lang}"))
            why = _clean(r.get(f"why_{lang}"))
            tag = f"package {pk['i']} ({lang})"
            pk["name"][lang], pk["why"][lang] = name, why
            # each failure says exactly what to fix: the revision round sends these reasons back to Gemini
            if not name:
                fail("text", f"{tag}: the name is missing")
            elif len(name) > NAME_MAX[lang] or len(name.split()) > 7:
                fail("text", f"{tag}: the name {name!r} is too long ({len(name)} characters, {len(name.split())} words; at most {NAME_MAX[lang]} characters and 7 words: name at most two places)")
            if not why:
                fail("text", f"{tag}: the reason is missing")
            elif len(why) > WHY_MAX:
                fail("text", f"{tag}: the reason is too long ({len(why)} characters; at most {WHY_MAX})")
            if _UNIT_WORD.search(mask.mask(f"{name} {why}")):
                fail("text", f"{tag}: says 'unit', the prompt's word; the viewer sees campuses (say 'these campuses', never unit numbers)")
            low = f" {why.lower()} "
            if lang == "es" and why and not any(t in f" {why.lower()} " or t in f" {name.lower()} " for t in _ES_MARKS):
                fail("text", f"{tag}: not Spanish")
            if lang == "es" and _EN_WORDS.search(mask.mask(f"{name} {why}")):
                fail("text", f"{tag}: an English word in the Spanish text")
            if lang == "en" and sum(t in low for t in (" el ", " la ", " los ", " las ", " que ", " del ")) >= 2:
                fail("text", f"{tag}: not English")
            bad = place_problem(name, own, state, need_own=True, prose=False) if name else None
            if bad:
                fail("places", f"{tag} name {name!r}: {bad}")
            bad = place_problem(why, own | lim, state, need_own=False, prose=True) if why else None
            if bad:
                fail("places", f"{tag} reason: {bad}")
            bad = claim_problem(pk, limits, name, why, lang)
            if bad:
                fail("claims", f"{tag}: {bad}")
            key = _fold(name)
            if key and key in seen[lang]:
                fail("places", f"{tag}: the name {name!r} is used twice; every package needs its own")
            seen[lang].add(key)
            for text in (name, why):
                if _NUM.search(mask.mask(text)):
                    fail("numbers", f"{tag}: a number in {text!r} (say it without digits)")
                hit = _real_names(text)
                if hit:
                    fail("names", f"{tag}: a real name, {hit!r}")
            counts = (len(pk["steps"]), len(pk["paid"]), pk["upgrades"], pk["lines"], pk["transformers"], pk["first"], pk["last"])
            known = {w for x in counts for form in (words(x, lang), words(x, lang, fem=True), words(x, lang, before_noun=True), ordinal(x, lang))
                     for w in re.findall(r"[a-záéíóúñü]+", form.lower())}
            extra = number_words(mask.mask(f"{name} {why}"), lang) - known
            if extra:
                fail("numbers", f"{tag}: a spelled number the data doesn't have: {sorted(extra)}")
    return (pkgs if all(v is None for v in res.values()) else None), checks(), reasons


GROUP_SYSTEM = """You are a grid planner preparing a one-minute narrated play-by-play of a build-up on a map. Data-center
campuses connect one after another to a SYNTHETIC grid model of a U.S. state (Breakthrough Energy / Texas A&M), not any
real utility's network. Each paid step raises some lines and transformers so the next campus fits. You get the steps in
the engine's order, numbered as UNITS, with the places their upgrades touch. Group the units into packages for the
narration.

Rules:
- Packages are CONTIGUOUS runs of units in the given order: never reorder, skip or repeat a unit. Package one starts at
  unit 1, each next package starts right after the previous one ends, the last one ends at the last unit.
- Use a number of packages within the range the prompt gives.
- Put units together when their upgrades belong together: the same corridor or area, or the same limit (the same line or
  transformer stopped their campuses). Cut where the upgrades move to another part of the state.
- name_en / name_es: a short place-based name, two to five words and at most 40 characters (Spanish at most 50), no
  leading article, naming at most two places, built ONLY from the places in that package's units' upgrade_places, e.g. "Jacksonville transformers", "Lines north of Orlando", "Orlando and Saint
  Cloud lines"; Spanish "Transformadores de Jacksonville", "Líneas al norte de Orlando". Every package gets its own
  name. Keep place names exactly as written, never translated.
- A name says "lines" only when the package raises a line and "transformers" only when it raises a transformer (see each
  upgrade's kind); for a mix, say "upgrades" or name the corridor.
- why_en / why_es: one short, concrete sentence (under 110 characters) on why these upgrades belong together, from the
  data: the limit they share, named as what_stopped_it writes it, or the towns the corridor runs between. Name a limit
  only if it stopped one of the package's units or is one of its upgrades. Say "the same limit / line / transformer"
  ONLY when what_stopped_it names that same limit for at least two of the package's units; "both" only for a package
  of exactly two campuses; "these campuses" / "all" / "every" / "each" as what a limit stopped only when it stopped
  every unit in the package (otherwise "most of these campuses", or name each limit).
  Never a generic reason ("regional capacity needs"). Spanish written natively, not word for word.
- No digits and no counts in names or reasons (a limit's own name, exactly as what_stopped_it writes it, keeps its
  number); no real utility, company, agency or project names; no dates.
- The viewer never sees units: in names and reasons say "these campuses" / "estos campus", never "unit", "units" or a
  unit's number.

Answer only with JSON: {"packages": [{"first_unit": 1, "last_unit": 2, "name_en": "...", "name_es": "...", "why_en": "...", "why_es": "..."}]}."""

GROUP_SCHEMA = {
    "type": "object",
    "properties": {
        "packages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first_unit": {"type": "integer"}, "last_unit": {"type": "integer"},
                    "name_en": {"type": "string"}, "name_es": {"type": "string"},
                    "why_en": {"type": "string"}, "why_es": {"type": "string"},
                },
                "required": ["first_unit", "last_unit", "name_en", "name_es", "why_en", "why_es"],
            },
        }
    },
    "required": ["packages"],
}


def group_prompt(p: Plan, units: list[dict]) -> str:
    lo, hi = pkg_range(len(units))
    lines = [f"STATE: {p.state}. CAMPUS: {mw_say(p.mw, 'en', adj=True)}, {'flexible' if p.flex else 'always on'}.",
             f"UNITS: {len(units)}. Use {'exactly one package' if hi == 1 else f'between {lo} and {hi} packages'}.", "", "UNITS (the engine's order):"]
    prev = None
    for i, x in enumerate(units, 1):
        st = x["main"]
        projs = projects_of(x["steps"])
        c = center_of(x["steps"])
        b = majority(st.get("blocked_by"))
        row = {
            "unit": i,
            "campuses": [s["n"] for s in x["steps"]],
            "campus_connects_at (not an upgrade place)": st["site"]["area"],
            "what_stopped_it": (f"{label_of(b, 'en')} ({b.get('where') or (b.get('from') or {}).get('area')})" if b else "no single limit"),
            "upgrades": [{"name": pj.get("short"), "kind": pj.get("kind"), "places": areas_of(pj)} for pj in projs][:8],
            "upgrade_places": [a for a, _w in area_weights(projs)],
            "upgrades_center_lat_lon": [round(c[0], 2), round(c[1], 2)],
            "km_from_previous_unit": (round(km(prev, c)) if prev else 0),
        }
        prev = c
        lines.append(json.dumps(row, ensure_ascii=False))
    lines += ["", 'Group them now, as JSON {"packages": [{"first_unit": 1, "last_unit": 2, "name_en": "...", "name_es": "...", "why_en": "...", "why_es": "..."}]}.']
    return "\n".join(lines)


def group_revise_prompt(prompt: str, data, reasons: list[str]) -> str:
    out = [prompt, "", "YOUR FIRST GROUPING FAILED THESE CHECKS (the engine checks every package against the data and the rules):"]
    out += [f"- {r}" for r in reasons[:10]]
    out += ["Your answer was: " + json.dumps(data, ensure_ascii=False)[:1800],
            'Answer again with the whole grouping, fixing exactly what is named, as JSON {"packages": [...]}.']
    return "\n".join(out)


async def _ask(prompt: str, budget_s: float, system: str | None = None, schema: dict | None = None, empty: dict | None = None) -> tuple[dict, bool]:
    """One Gemini call within `budget_s` seconds: (JSON, offline)."""
    none = empty if empty is not None else {"slides": []}
    try:
        return await asyncio.wait_for(
            llm.complete_json(prompt, system=system or SYSTEM, fallback=none, timeout=AI_TIMEOUT_S, schema=schema or SCHEMA, surface=SURFACE,
                              model=llm.AGENT_MODEL, thinking=llm.AGENT_THINKING),
            max(1.0, budget_s))
    except asyncio.TimeoutError:
        log.warning("strengthen narration: Gemini took over %.0fs", budget_s)
        return none, True


async def gemini_grouping(p: Plan, units: list[dict]) -> tuple[list[dict] | None, dict]:
    """Gemini proposes the packages; the checks run; a failure goes back once with the reasons. (packages or None,
    trace). None with trace['offline']: no answer."""
    t0 = time.monotonic()
    prompt = group_prompt(p, units)
    data, offline = await _ask(prompt, GROUP_DEADLINE_S, GROUP_SYSTEM, GROUP_SCHEMA, {"packages": []})
    if offline:
        return None, {"offline": True}
    pkgs, checks, reasons = check_grouping(p, units, data)
    first = list(reasons)
    rounds, revised = 1, False
    left = GROUP_DEADLINE_S - (time.monotonic() - t0)
    if pkgs is None and left >= GROUP_REVISE_MIN_S:
        rounds = 2
        data2, off2 = await _ask(group_revise_prompt(prompt, data, reasons), left, GROUP_SYSTEM, GROUP_SCHEMA, {"packages": []})
        if not off2:
            pkgs, checks, reasons = check_grouping(p, units, data2)
            revised = pkgs is not None
    if pkgs is None:
        log.warning("strengthen narration: Gemini's grouping rejected, the engine's used: %s", "; ".join(reasons)[:500])
    return pkgs, {"offline": False, "rounds": rounds, "revised": revised, "checks": checks,
                  "first_draft_rejections": first[:8], "rejections": reasons[:8]}


def finalize_grouping(p: Plan, units: list[dict], pkgs: list[dict] | None, tr: dict | None, reason: str | None) -> tuple[list[dict], dict]:
    """Gemini's checked packages, or the engine's grouping (checked by the same code) with why Gemini's isn't used."""
    u = len(units)
    lo, hi = pkg_range(u)
    base = {"surface": SURFACE, "units": u, "range": [lo, hi], "model": llm.AGENT_MODEL}
    if not units:
        return [], {**base, "by": "none", "verified": True, "reason": "no upgrades to group", "packages": 0, "checks": []}
    if pkgs is not None:
        return pkgs, {**base, "by": "gemini", "verified": True, "reason": None, "packages": len(pkgs), "checks": tr["checks"],
                      "rounds": tr.get("rounds", 1), "revised": tr.get("revised", False),
                      "first_draft_rejections": tr.get("first_draft_rejections", [])}
    eng = engine_grouping(p, units)
    rows = [{"first_unit": pk["units"][0], "last_unit": pk["units"][1], **{f"{k}_{lang}": pk[k][lang] for k in ("name", "why") for lang in LANGS}} for pk in eng]
    _ok, checks, bad = check_grouping(p, units, {"packages": rows}, note=False)
    if bad:
        log.warning("strengthen narration: the engine's own grouping failed a check: %s", "; ".join(bad)[:300])
    out = {**base, "by": "engine", "verified": not bad, "reason": reason, "packages": len(eng), "checks": checks}
    if tr and tr.get("checks"):
        out["gemini_checks"] = tr["checks"]
        out["rounds"] = tr.get("rounds", 1)
        out["first_draft_rejections"] = tr.get("first_draft_rejections", [])
        out["rejections"] = tr.get("rejections", [])
    return eng, out


def _group_get(key: tuple):
    with _ai_mutex:
        hit = _groups.get(key)
        if hit is None or (hit[3] and time.monotonic() - hit[2] > RETRY_S):
            return None
        _groups.move_to_end(key)
        return hit[0], dict(hit[1])


def _group_put(key: tuple, pkgs: list[dict], trace: dict) -> None:
    with _ai_mutex:
        _groups[key] = (pkgs, trace, time.monotonic(), trace.get("reason") in GROUP_RETRYABLE)
        while len(_groups) > GROUPS_MAX:
            _groups.popitem(last=False)


async def grouping_for(p: Plan, units: list[dict], ai_on: bool) -> tuple[list[dict], dict]:
    """The packages for this plan and budget, and who grouped them (cached per study, mode and budget)."""
    if not units:
        return finalize_grouping(p, units, None, None, None)
    if not ai_on:
        return finalize_grouping(p, units, None, None, "AI off for this request")
    if not llm.configured():
        return finalize_grouping(p, units, None, None, "Gemini not configured")
    hit = _group_get(p.gkey)
    if hit is not None:
        return hit
    lock = _group_locks.setdefault(p.gkey, asyncio.Lock())
    async with lock:
        hit = _group_get(p.gkey)
        if hit is not None:
            return hit
        pk, tr = await gemini_grouping(p, units)
        _group_locks.pop(p.gkey, None)
        res = finalize_grouping(p, units, pk, tr, None if pk is not None else ("Gemini unavailable" if tr.get("offline") else "Gemini's grouping failed its checks"))
        _group_put(p.gkey, *res)
        return res[0], dict(res[1])


# ------------------------------------------------------------------------------------------ the script's parts
def base_pct(p: Plan, b: dict) -> int | None:
    """The limit's loading on today's grid model at the study's load level, percent of its rating (whole)."""
    try:
        g = gridmod.grid_at(p.lf, p.code)
        i = g.br_index.get(b["branch_id"]) if hasattr(g.br_index, "get") else g.br_index[b["branch_id"]]
        return None if i is None else int(round(float(g.base.loading_pct[i])))
    except Exception:  # noqa: BLE001 - the intro just doesn't say it
        return None


def problems_of(p: Plan, pkgs: list[dict]) -> list[dict]:
    """The weak points in today's grid this build runs into: what stops the next campus, then each paid step's
    majority limit, each once; which package raises it (if any)."""
    cands = []
    nb = p.next_block()
    if nb:
        cands.append((nb, p.today + 1))
    for st in p.steps[p.today: p.bought]:
        b = majority(st.get("blocked_by"))
        if b:
            cands.append((b, st["n"]))
    out, seen = [], set()
    for b, n in cands:
        if b["branch_id"] in seen:
            continue
        seen.add(b["branch_id"])
        fixed = next((pk["i"] for pk in pkgs if any(pj["branch_id"] == b["branch_id"] for pj in pk["_projects"])), None)
        out.append({
            "branch_id": b["branch_id"], "kind": b.get("kind"), "short": b.get("short"), "label": b.get("label"), "where": b.get("where"),
            "from": {k: (b.get("from") or {}).get(k) for k in ("lat", "lon", "area")}, "to": {k: (b.get("to") or {}).get(k) for k in ("lat", "lon", "area")},
            "mid": b.get("mid"), "stops": n, "base_pct": base_pct(p, b), "fixed_in": fixed, "rate_est": bool(b.get("rate_est")),
        })
    return out[:6]


def slides_of(p: Plan, pkgs: list[dict]) -> list[dict]:
    """Intro, one slide per package (the map shows the campuses before it when it enters; its campuses drop in as
    the words run), closing."""
    out = [{"id": "intro", "kind": "intro", "step_from": 0, "step": p.today, "steps": list(range(1, p.today + 1))}]
    prev = p.today
    for pk in pkgs:
        out.append({"id": pk["id"], "kind": "package", "step_from": prev, "step": pk["last"], "steps": list(pk["steps"]), "pkg": pk})
        prev = pk["last"]
    out.append({"id": "close", "kind": "close", "step_from": p.bought, "step": p.bought, "steps": []})
    return out


def build_facts(p: Plan, slides: list[dict], problems: list[dict]) -> Facts:
    f = Facts()
    pkgs = [s["pkg"] for s in slides if s["kind"] == "package"]
    f.add("campus.mw", "Campus size", p.mw, "MW")
    f.add("today", "Campuses the grid model carries at once today, with no upgrades", p.today, "campuses")
    f.add("bought.n", "Campuses at once with the upgrades the budget buys", p.bought, "campuses")
    f.add("bought.cost_high", "What those upgrades cost, high end", round(p.cost["high"]), "USD")
    f.add("packages.n", "Packages the upgrades are grouped into", len(pkgs), "")
    if p.binds():
        f.add("plants.campuses", "Campuses the power plants cover with the planning reserve kept", p.plants_n, "campuses")
        f.add("plants.reserve_pct", "Planning reserve kept on the state's own load", p.reserve, "%")
    for st in p.steps[: p.bought]:
        f.name(st["site"]["area"])
    nb = p.next_block()
    if nb:
        f.branch(nb)
        f.add("intro.next", "The campus the first weak point stops", nb.get("at_campus"), "", "intro")
        pb = next((x for x in problems if x["branch_id"] == nb["branch_id"]), None)
        if pb and pb.get("base_pct") is not None:
            f.add("intro.base_pct", "Its loading on today's grid, percent of its rating", pb["base_pct"], "%", "intro")
    if problems:
        f.add("intro.weak_points", "Weak points in today's grid that hold back the campuses in this build", len(problems), "", "intro")
        if len(problems) > 1:
            f.add("intro.more_weak_points", "More weak points like the first one", len(problems) - 1, "", "intro")
    for pb in problems:
        f.branch(pb)
    for s in slides:
        if s["kind"] != "package":
            continue
        pk, sid = s["pkg"], s["id"]
        f.add(f"{sid}.i", "Package number", pk["i"], "", sid)
        f.add(f"{sid}.cost_high", "What this package's upgrades cost, high end", pk["cost_high"], "USD", sid)
        f.add(f"{sid}.cum_high", "Running total after it, high end", pk["cum_high"], "USD", sid)
        f.add(f"{sid}.first", "Its first campus", pk["first"], "", sid)
        f.add(f"{sid}.last", "Campuses at once after it (its last campus)", pk["last"], "", sid)
        f.add(f"{sid}.campuses", "Campuses it lets in", len(pk["steps"]), "", sid)
        f.add(f"{sid}.upgrades", "Lines and transformers it raises", pk["upgrades"], "", sid)
        f.add(f"{sid}.lines", "Lines it raises", pk["lines"], "", sid)
        f.add(f"{sid}.transformers", "Transformers it raises", pk["transformers"], "", sid)
        for pj in pk["_projects"]:
            f.branch(pj)
        for st in pk["_steps"]:
            f.branch(st.get("blocked_by"))
        f.name(*pk["name"].values())
    f.name(p.state, p.state_es)
    # the campus size as said: "2000 megavatios" is a fact, not a year (the forbidden-words check reads years)
    f.name(mw_say(p.mw, "es"), mw_say(p.mw, "en"), mw_say(p.mw, "en", adj=True))
    return f


def _usd(v: float, lang: str) -> str:
    return usd_say(v, lang)[0]


def lead_of(i: int, k: int, lang: str) -> str:
    if k == 1:
        return ONE_LEAD[lang]
    return LEAD[lang][0 if i == 1 else 2 if i == k else 1]


def tmpl_intro(p: Plan, pkgs: list[dict], problems: list[dict], lang: str, short: bool = False) -> tuple[str, str]:
    """(text, its first sentence: today's campuses appear over it). short: without "and N more weak points like it"
    (the card still shows it), for a script that would run past TOTAL_MAX_S."""
    en = lang == "en"
    t = p.today
    if en:
        kind = "flexible " if p.flex else ""
        s1 = (f"Today, {p.state}'s grid model carries {words(t, 'en')} more {kind}{p.size('en')} data {'center' if t == 1 else 'centers'} at once." if t
              else f"Today, {p.state}'s grid model can't carry even one more {kind}{p.size('en')} data center.")
    else:
        if t:
            kind = (" flexibles" if t > 1 else " flexible") if p.flex else ""
            s1 = f"Hoy, el modelo de la red de {p.state_es} soporta {words(t, 'es', before_noun=True)} {'centro' if t == 1 else 'centros'} de datos{kind} más de {p.size('es')} a la vez."
        else:
            s1 = f"Hoy, el modelo de la red de {p.state_es} no soporta ni un centro de datos{' flexible' if p.flex else ''} más de {p.size('es')}."
    parts = [s1]
    nb = p.next_block()
    if nb:
        pb = next((x for x in problems if x["branch_id"] == nb["branch_id"]), None)
        pct = pb.get("base_pct") if pb else None
        pct = pct if pct is not None and pct >= 50 else None
        others = 0 if short else max(0, len(problems) - 1)
        nxt = ordinal(t + 1, lang)
        if en:
            more = "" if not others else (", and one more weak point like it" if others == 1 else f", and {words(others, 'en')} more weak points like it")
            if pct is not None:
                parts.append(f"What stops the {nxt} isn't the campus: it's {label_of(nb, 'en')}, already at {pct} percent of its rating"
                             + (f"{more}." if more else " on today's grid."))
            else:
                parts.append(f"What stops the {nxt} isn't the campus: it's {label_of(nb, 'en')}, a weak point already in today's grid{more}.")
        else:
            more = "" if not others else (", y otro punto débil como ese" if others == 1 else f", y {words(others, 'es', before_noun=True)} puntos débiles más como ese")
            if pct is not None:
                parts.append(f"Lo que frena al {nxt} no es el campus: es {label_of(nb, 'es')}, que ya funciona al {pct} por ciento de su capacidad"
                             + (f"{more}." if more else " en la red de hoy."))
            else:
                parts.append(f"Lo que frena al {nxt} no es el campus: es {label_of(nb, 'es')}, un punto débil que ya existe en la red de hoy{more}.")
    if p.flex:
        parts.append("Flexible campuses drop to half power on peak afternoons." if en else "Los campus flexibles bajan a la mitad de potencia en las tardes de máxima demanda.")
    if pkgs:
        k = len(pkgs)
        parts.append(f"Here's the fix, in {words(k, 'en')} {'package' if k == 1 else 'packages'}." if en
                     else f"Así se arregla, en {words(k, 'es', before_noun=True)} {'paquete' if k == 1 else 'paquetes'}.")
    return " ".join(parts), s1


WHY_SPOKEN_MAX_K = 2  # with one or two packages the play-by-play also says why each one's upgrades belong together


def go_in(pk: dict, lang: str) -> str:
    """'campus three' / 'campuses three to six' (the data's form of which campuses the package lets in)."""
    a, b = pk["first"], pk["last"]
    if lang == "en":
        return f"campus {words(a, 'en')}" if a == b else f"campuses {words(a, 'en')} to {words(b, 'en')}"
    return f"el campus {words(a, 'es')}" if a == b else f"los campus {words(a, 'es')} a {words(b, 'es')}"


def at_once(n: int, lang: str) -> str:
    """'Now six fit at once.' / 'Ahora caben seis a la vez.'"""
    if lang == "en":
        return f"Now {words(n, 'en')} {'fits' if n == 1 else 'fit'} at once."
    return f"Ahora {'cabe' if n == 1 else 'caben'} {words(n, 'es', before_noun=n != 1)} a la vez."


def tmpl_package(p: Plan, s: dict, k: int, lang: str, short: bool = False) -> str:
    pk = s["pkg"]
    kinds = kinds_phrase(pk["_projects"], lang)
    usd = _usd(pk["cost_high"], lang)
    why = f" {pk['_why_engine'][lang]}" if not short and k <= WHY_SPOKEN_MAX_K and pk.get("_why_engine", {}).get(lang) else ""
    if lang == "en":
        return f"{lead_of(pk['i'], k, 'en')}: {pk['name']['en']}.{why} {cap(kinds)} raised, up to {usd}. {at_once(pk['last'], 'en')}"
    verb = "amplía" if pk["upgrades"] == 1 else "amplían"
    return f"{lead_of(pk['i'], k, 'es')}: {pk['name']['es']}.{why} Se {verb} {kinds}, por hasta {usd}. {at_once(pk['last'], 'es')}"


def tmpl_close(p: Plan, lang: str, short: bool = False) -> str:
    """short: the power plants' line without its reserve percent (the beat card keeps it), for a long build."""
    en = lang == "en"
    n, t = p.bought, p.today
    parts = []
    if n > t:
        parts.append(f"In all: {_usd(p.cost['high'], 'en')} of upgrades, and {words(n, 'en')} {'fits' if n == 1 else 'fit'} at once, up from {words(t, 'en')} today." if en
                     else f"En total: {_usd(p.cost['high'], 'es')} en mejoras, y {'cabe' if n == 1 else 'caben'} {words(n, 'es', before_noun=n != 1)} a la vez, frente a {words(t, 'es', before_noun=True)} hoy.")
    elif n:
        parts.append(f"Without upgrades, {p.state}'s grid model stops at {words(n, 'en')}." if en else f"Sin mejoras, el modelo de la red de {p.state_es} se queda en {words(n, 'es', before_noun=True)}.")
    else:
        parts.append("Without upgrades, no campus of this size fits." if en else "Sin mejoras, no cabe ningún campus de este tamaño.")
    if p.binds() and short:
        pn = p.plants_n
        if pn == 0:
            parts.append("The power plants can't supply even one: every campus also needs new generation or flexible hours." if en
                         else "Las centrales no abastecen ni uno: cada campus también necesita nueva generación u horario flexible.")
        else:
            parts.append(f"The power plants supply {words(pn, 'en')} of them; past that, campuses need new generation or flexible hours too." if en
                         else f"Las centrales abastecen {words(pn, 'es', before_noun=True)}; a partir de ahí, los campus también necesitan nueva generación u horario flexible.")
    elif p.binds():
        pn, r = p.plants_n, num(p.reserve, lang)
        if pn == 0:
            parts.append(f"The power plants can't supply even one with a {r} percent reserve kept: every campus also needs new generation or flexible hours." if en
                         else f"Las centrales no abastecen ni uno manteniendo una reserva del {r} por ciento: cada campus también necesita nueva generación u horario flexible.")
        else:
            parts.append(f"The power plants supply {words(pn, 'en')} of them with a {r} percent reserve kept; past that, campuses need new generation or flexible hours too." if en
                         else f"Las centrales abastecen {words(pn, 'es', before_noun=True)} con una reserva del {r} por ciento; a partir de ahí, los campus también necesitan nueva generación u horario flexible.")
    parts.append("All on a synthetic grid model." if en else "Todo sobre un modelo sintético de la red.")
    return " ".join(parts)


def templates(p: Plan, slides: list[dict], problems: list[dict]) -> dict:
    """{(slide id, lang): text} and the intro's first sentence per language."""
    pkgs = [s["pkg"] for s in slides if s["kind"] == "package"]
    out, short, first = {}, {}, {}
    for lang in LANGS:
        for s in slides:
            if s["kind"] == "intro":
                out[(s["id"], lang)], first[lang] = tmpl_intro(p, pkgs, problems, lang)
                short[(s["id"], lang)] = tmpl_intro(p, pkgs, problems, lang, short=True)[0]
            elif s["kind"] == "package":
                out[(s["id"], lang)] = tmpl_package(p, s, len(pkgs), lang)
                short[(s["id"], lang)] = tmpl_package(p, s, len(pkgs), lang, short=True)
            else:
                out[(s["id"], lang)] = tmpl_close(p, lang)
                short[(s["id"], lang)] = tmpl_close(p, lang, short=True)
    return {"text": out, "short": short, "first": first}


def prepare(p: Plan, pkgs: list[dict]) -> dict:
    """Everything the lines are written from: the slides, the weak points, the facts, the templates."""
    problems = problems_of(p, pkgs)
    slides = slides_of(p, pkgs)
    facts = build_facts(p, slides, problems)
    tm = templates(p, slides, problems)
    return {"pkgs": pkgs, "problems": problems, "slides": slides, "facts": facts, "tmpl": tm["text"], "tmpl_short": tm["short"], "first": tm["first"]}


def gsig(pkgs: list[dict]) -> tuple:
    return tuple((pk["first"], pk["last"], pk["name"]["en"], pk["name"]["es"]) for pk in pkgs)


# ------------------------------------------------------------------------------------------ Gemini's lines
SYSTEM = """You are the presenter of a short play-by-play on a map: data-center campuses connect one by one to a
SYNTHETIC grid model of a U.S. state (Breakthrough Energy / Texas A&M), not any real utility's network, and the upgrades
that make room for them go in as PACKAGES, drawn as you speak. For each slide you get its PURPOSE and its DATA (values
computed by the grid engine, with every number already in its spoken form in English and Spanish). Write what the
presenter says on that slide, once in English and once in Spanish. The whole narration runs about a minute.

Rules:
- Short spoken sentences in the present tense, calm and clear, one idea per sentence.
- FRAMING: the problem is a weakness already in today's grid (a line or transformer already near its rating), not the
  data center; a campus is only what exposes it. Never blame the campus or its developer.
- Say only what the DATA says. Write every number exactly in its given spoken form ("$24.1 million"; Spanish "24.1
  millones de dólares"). Never compute a new number: no sums, differences, ratios, percentages or comparisons ("twice",
  "half") of your own.
- Campus numbers and small counts as words, as the DATA writes them ("campuses three to six", "seven at once").
- Keep every package name, place, line and transformer name exactly as the DATA writes it, in both languages.
- Never write "MW", "MVA", "GW" or "%": say megawatts / percent (megavatios / por ciento).
- Costs are the high end of an estimate: say "up to" (Spanish "hasta").
- Only the closing names the synthetic grid model ("a synthetic grid model" / "un modelo sintético de la red").
- Never name a real utility, company, agency or project; no dates or years; no advice to the public.
- Stay within each slide's max_chars.
- The Spanish is written natively for a U.S. Spanish-speaking audience, not translated word for word. "Campus" stays
  "campus" (plural "los campus"), never "campamento". The size comes after the noun: "centros de datos de 1000
  megavatios", never "1000 megavatios campus".
- Plain text only: no markdown, lists, emoji or quotation marks around the line.

Answer only with JSON: {"slides": [{"id": "<slide id>", "en": "...", "es": "..."}]}, one entry per slide id, in order."""

SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "en": {"type": "string"}, "es": {"type": "string"}},
                "required": ["id", "en", "es"],
            },
        }
    },
    "required": ["slides"],
}

PURPOSE = {
    "intro": "how many campuses fit at once today (say campuses_at_once_today); what stops the next one, framed as a weakness ALREADY in today's grid, not the campus (say what_stops_the_next_campus, and its_loading_today and more_weak_points_like_it when given); then that the fix comes in this many packages",
    "package": "lead with the package (say lead and name exactly); what gets raised, by kind (say raised); its cost (say cost_high_end exactly, as 'up to'); how many campuses fit at once after it (say campuses_at_once_after); when say_why is true, also why its upgrades belong together, in a few words",
    "close": "the whole plan: what the upgrades cost and how many campuses fit at once, against today; the power plants' reserve line when given (past it, campuses also need new generation or flexible hours); that this is a synthetic grid model, not any utility's network (say it here, once)",
}


def _both(fn) -> dict:
    return {lang: fn(lang) for lang in LANGS}


def slide_data(p: Plan, s: dict, problems: list[dict], k: int) -> dict:
    """The slide's content as data, every number in its spoken form (no sentences to copy)."""
    d: dict = {"campus_size": _both(lambda lang: p.size(lang)), "campus_type": "flexible (full power except on peak afternoons, half power then)" if p.flex else "always on"}
    if s["kind"] == "intro":
        t = p.today
        d["state"] = {"en": p.state, "es": p.state_es}
        d["campuses_at_once_today_with_no_upgrades"] = _both(lambda lang: words(t, lang))
        nb = p.next_block()
        if nb:
            d["what_stops_the_next_campus"] = _both(lambda lang: label_of(nb, lang))
            d["the_next_campus"] = _both(lambda lang: ordinal(t + 1, lang))
            pb = next((x for x in problems if x["branch_id"] == nb["branch_id"]), None)
            if pb and pb.get("base_pct") is not None and pb["base_pct"] >= 50:
                d["its_loading_today"] = {"en": f"{pb['base_pct']} percent of its rating", "es": f"{pb['base_pct']} por ciento de su capacidad"}
            if len(problems) > 1:
                d["more_weak_points_like_it"] = _both(lambda lang: words(len(problems) - 1, lang))
        if k:
            d["packages"] = _both(lambda lang: words(k, lang))
    elif s["kind"] == "package":
        pk = s["pkg"]
        d["lead"] = _both(lambda lang: lead_of(pk["i"], k, lang))
        d["name"] = dict(pk["name"])
        d["why_they_belong_together"] = dict(pk["why"])
        d["say_why"] = k <= WHY_SPOKEN_MAX_K
        d["raised"] = _both(lambda lang: kinds_phrase(pk["_projects"], lang))
        d["cost_high_end"] = _both(lambda lang: _usd(pk["cost_high"], lang))
        d["campuses_that_go_in"] = _both(lambda lang: go_in(pk, lang))
        d["campuses_at_once_after"] = _both(lambda lang: words(pk["last"], lang))
    else:
        n, t = p.bought, p.today
        d["state"] = {"en": p.state, "es": p.state_es}
        d["campuses_at_once_with_the_budget"] = _both(lambda lang: words(n, lang))
        d["campuses_at_once_today"] = _both(lambda lang: words(t, lang))
        if n > t:
            d["upgrades_cost_high_end"] = _both(lambda lang: _usd(p.cost["high"], lang))
        if p.binds():
            d["power_plants_cover_with_the_reserve_kept"] = _both(lambda lang: words(p.plants_n, lang))
            d["planning_reserve"] = {"en": f"{num(p.reserve, 'en')} percent", "es": f"{num(p.reserve, 'es')} por ciento"}
            d["past_the_power_plants"] = "campuses also need new generation or flexible hours, not only wires"
        d["must_say_once"] = {"en": "a synthetic grid model, not any utility's network", "es": "un modelo sintético de la red"}
    return d


def _clean(text) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r"[*_#`>|]+", "", text)
    return re.sub(r"\s+", " ", text).strip().strip('"').strip("“”").strip()


def _same(a: str, b: str) -> bool:
    norm = lambda x: re.sub(r"[^a-z0-9áéíóúñü]+", " ", x.lower()).strip()  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.9


def limit_of(template: str, lang: str) -> int:
    """A Gemini line may run a little longer than its template, never much: the whole script stays about a minute
    (and never shorter than the template itself plus a little: some intros must say a lot)."""
    return max(min(LINE_MAX[lang], max(int(len(template) * 1.25) + 20, 120)), len(template) + 20)


def says_count(n: int, low: str, lang: str) -> bool:
    forms = {words(n, lang), words(n, lang, before_noun=True), str(n)}
    return any(re.search(rf"\b{re.escape(x)}\b", low) for x in forms)


def validate(p: Plan, s: dict, lang: str, text: str, template: str, facts: Facts, problems: list[dict], k: int) -> tuple[bool, str | None, int]:
    """One Gemini line: length, language, no abbreviations, the must-says, spelled numbers, every digit a fact."""
    lim = limit_of(template, lang)
    if not text or len(text) < 20:
        return False, "empty", 0
    if len(text) > lim:
        return False, f"too long ({len(text)} > {lim})", 0
    low = f" {text.lower()} "
    es_marks = sum(t in low for t in (" el ", " la ", " los ", " las ", " que ", " del ", " una ", " se ", "ción", "ñ"))
    if lang == "es" and es_marks == 0:
        return False, "not Spanish", 0
    if lang == "en" and es_marks >= 2:
        return False, "not English", 0
    if re.search(r"\b(MW|MVA|GW|kV)\b|%", text):
        return False, "an abbreviation the voice would read letter by letter", 0
    for name in TRANSLATED_NAMES.get(lang, ()):
        if name.lower() in low:
            return False, f"a translated place name: {name}", 0
    folded = _fold(text)
    if lang == "es" and re.search(r"campament|megavatios\s+(?:campus|centros?)\b", folded):
        return False, "Spanish wording: 'campamento', or the size before the noun", 0
    if lang == "es" and re.search(r"\b(to|the|and|from|with|of)\b", facts.mask(text), re.IGNORECASE):
        return False, "an English word in the Spanish line", 0  # "de 137 to 200 megavoltamperios" (names masked first)
    if s["kind"] == "intro":
        if p.today > 0 and not says_count(p.today, low, lang):
            return False, "does not say how many fit today", 0
        nb = p.next_block()
        if nb:
            where = nb.get("where") or (nb.get("from") or {}).get("area") or ""
            if where and _fold(where) not in folded:
                return False, f"does not name what stops the next campus ({where})", 0
            # the framing: a weakness already in TODAY's grid, measured (its loading today), not the campus
            if not re.search(r"\b(today|hoy)\b", low):
                return False, "does not frame the weak point as today's grid (say 'today')", 0
            pb = next((x for x in problems if x["branch_id"] == nb["branch_id"]), None)
            if pb and pb.get("base_pct") is not None and pb["base_pct"] >= 50 and not re.search(rf"\b{pb['base_pct']}\b", text):
                return False, "does not say its loading on today's grid (its_loading_today)", 0
    elif s["kind"] == "package":
        pk = s["pkg"]
        figure = _NUM.search(usd_say(pk["cost_high"], lang)[0]).group(0)  # "24.1" of "$24.1 million"
        if figure not in text:
            return False, "does not say this package's cost", 0
        if not says_count(pk["last"], low, lang):
            return False, "does not say how many fit at once after it", 0
        own = own_places(pk)
        spots = [t for t in _TOK.findall(pk["name"][lang]) if _fold(t) in own]
        if spots and not any(_fold(t) in folded for t in spots):
            return False, "does not name the package (its place)", 0
    else:
        if p.bought > 0 and not says_count(p.bought, low, lang):
            return False, "does not say how many fit at once", 0
        if p.binds() and ("generation" if lang == "en" else "generacion") not in folded:
            return False, "drops the power plants' line (past it, new generation or flexible hours)", 0
        if ("synthetic" if lang == "en" else "sintetic") not in folded:
            return False, "the closing must name the synthetic grid model", 0
    data_text = json.dumps(slide_data(p, s, problems, k), ensure_ascii=False)
    known = number_words(template, lang) | number_words(data_text, lang) | facts.known_words(s["id"], lang)
    extra = number_words(facts.mask(text), lang) - known
    if extra:
        return False, f"a spelled number or ratio not in the data: {sorted(extra)}", 0
    return check_numbers(text, facts, s["id"])


def prompt_of(p: Plan, parts: dict) -> str:
    slides, tmpl, facts, problems = parts["slides"], parts["tmpl"], parts["facts"], parts["problems"]
    k = len(parts["pkgs"])
    lines = [f"STATE: {p.state} (Spanish: {p.state_es}). CAMPUS: {mw_say(p.mw, 'en', adj=True)}, {'flexible' if p.flex else 'always on'}.",
             "FACTS (from the grid engine; the only numbers you may use):"]
    for f in facts.rows:
        v = f["value"]
        lines.append(f"[{f['key']}] {f['label']}: {v:,}{(' ' + f['unit']) if f['unit'] else ''}" if isinstance(v, (int, float)) else f"[{f['key']}] {f['label']}: {v}")
    lines += ["", "SLIDES:"]
    for s in slides:
        lines.append(f"- id: {s['id']}")
        lines.append(f"  PURPOSE: {PURPOSE[s['kind']]}")
        lines.append("  max_chars: " + ", ".join(f"{lang} {int(limit_of(tmpl[(s['id'], lang)], lang) * 0.85)}" for lang in LANGS))
        lines.append("  DATA: " + json.dumps(slide_data(p, s, problems, k), ensure_ascii=False))
    lines += ["", 'Write every slide now, as JSON {"slides": [{"id": "...", "en": "...", "es": "..."}]}.']
    return "\n".join(lines)


def _rows(data) -> dict:
    got = {}
    for row in (data.get("slides") if isinstance(data, dict) else None) or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            got[row["id"]] = row
    return got


def _check(p: Plan, parts: dict, got: dict, only: set | None = None):
    """Gemini's lines through validate(): ({(id, lang): text}, numbers checked, [(slide, lang, text, why)])."""
    ok_lines, checked, failed = {}, 0, []
    k = len(parts["pkgs"])
    for s in parts["slides"]:
        for lang in LANGS:
            if only is not None and (s["id"], lang) not in only:
                continue
            text = _clean((got.get(s["id"]) or {}).get(lang))
            template = parts["tmpl"][(s["id"], lang)]
            if text and _same(text, template):
                continue  # Gemini gave the template back: it stays labeled a template
            ok, why, n = validate(p, s, lang, text, template, parts["facts"], parts["problems"], k)
            llm.note_check(SURFACE, ok, f"a narration line: {why}")
            if ok:
                ok_lines[(s["id"], lang)] = text
                checked += n
            else:
                failed.append((s, lang, text, why))
    return ok_lines, checked, failed


def _hint(s: dict, lang: str, why: str | None, tmpl: dict) -> str:
    """A failed check, said so the revision can act on it."""
    why = why or "empty"
    out = [why]
    if s["kind"] == "package" and "cost" in why:
        out.append(f"say {usd_say(s['pkg']['cost_high'], lang)[0]!r} exactly as the DATA writes it")
    if "package" in why and "place" in why:
        out.append("say the package's name exactly as the DATA writes it")
    if "synthetic" in why:
        out.append("say 'a synthetic grid model' (Spanish 'un modelo sintético de la red') in the closing")
    if "stops the next" in why:
        out.append("name what_stops_the_next_campus exactly as the DATA writes it")
    out.append(f"stay under {int(limit_of(tmpl[(s['id'], lang)], lang) * 0.85)} characters")
    return "; ".join(out)


def revise_prompt(prompt: str, failed: list, tmpl: dict) -> str:
    """The first prompt, the lines that failed and why: Gemini rewrites only those (the engine checks them again)."""
    out = [prompt, "", "YOUR FIRST DRAFT FAILED THESE CHECKS (the engine checks every line against the DATA and the rules). "
           "Rewrite only these slides, in both languages, fixing exactly what is named and keeping every rule:"]
    for s, lang, text, why in failed:
        out.append(f"- id {s['id']} ({lang}): {_hint(s, lang, why, tmpl)}. Your line was: {json.dumps(text or '', ensure_ascii=False)}")
    out.append('Answer only with JSON {"slides": [{"id": "...", "en": "...", "es": "..."}]}, one entry per id above.')
    return "\n".join(out)


async def gemini_lines(p: Plan, parts: dict) -> tuple[dict, dict] | None:
    """Gemini writes every slide (one call, both languages); the checks run on every line; the lines that fail go back
    once with the reasons (proposal -> checks -> feedback -> revision) and are checked again. A line that still fails
    keeps its template. None: Gemini unavailable (no answer in time, no quota, an error)."""
    t0 = time.monotonic()
    prompt = prompt_of(p, parts)
    data, offline = await _ask(prompt, AI_DEADLINE_S)
    if offline:
        return None
    lines, checked, failed = _check(p, parts, _rows(data))
    first = [f"{s['id']}/{lang}: {why}" for s, lang, _t, why in failed]
    revised, rounds = 0, 1
    left = AI_DEADLINE_S - (time.monotonic() - t0)
    if failed and left >= REVISE_MIN_S:
        rounds = 2
        data2, offline2 = await _ask(revise_prompt(prompt, failed, parts["tmpl"]), left)
        if not offline2:
            fixed, n2, failed = _check(p, parts, _rows(data2), only={(s["id"], lang) for s, lang, _t, _w in failed})
            lines.update(fixed)
            checked += n2
            revised = len(fixed)
    reasons = [f"{s['id']}/{lang}: {why}" for s, lang, _t, why in failed]
    if reasons:
        log.warning("strengthen narration: lines rejected, templates used: %s", "; ".join(reasons)[:600])
    meta = {"fallback": not lines, "reason": None if lines else "every line failed its checks", "numbers_checked": checked,
            "rejected": len(failed), "rejections": reasons[:8], "rounds": rounds, "revised": revised,
            "first_draft_rejected": len(first), "first_draft_rejections": first[:8], "model": llm.AGENT_MODEL}
    return lines, meta


def _ai_get(key: tuple):
    """A cached answer, unless it had no passing line and RETRY_S has gone by (then Gemini is asked again)."""
    with _ai_mutex:
        hit = _ai.get(key)
        if hit is None or (not hit[0] and time.monotonic() - hit[2] > RETRY_S):
            return None
        _ai.move_to_end(key)
        return dict(hit[0]), dict(hit[1])


def _ai_put(key: tuple, lines: dict, meta: dict) -> None:
    with _ai_mutex:
        _ai[key] = (lines, meta, time.monotonic())
        while len(_ai) > AI_MAX:
            _ai.popitem(last=False)


async def ai_lines(p: Plan, parts: dict) -> tuple[dict, dict]:
    """({(slide id, lang): checked Gemini line}, meta), cached per study, mode, budget and grouping; a line that fails
    its checks (after one revision) keeps its template."""
    key = (*p.gkey, gsig(parts["pkgs"]))
    hit = _ai_get(key)
    if hit is not None:
        return hit
    if not llm.configured():
        return {}, {"fallback": True, "reason": "Gemini not configured", "numbers_checked": 0, "rejected": 0}
    lock = _ai_locks.setdefault(key, asyncio.Lock())
    async with lock:  # EN and ES are asked for at once: one Gemini call writes both
        hit = _ai_get(key)
        if hit is not None:
            return hit
        res = await gemini_lines(p, parts)
        _ai_locks.pop(key, None)
        if res is None:
            return {}, {"fallback": True, "reason": "Gemini unavailable", "numbers_checked": 0, "rejected": 0}
        _ai_put(key, *res)
        return dict(res[0]), dict(res[1])


# ------------------------------------------------------------------------------------------ warm-up
def budget_stops(m: dict) -> list[float]:
    """The page's budget stops (capacity.js capStops): nothing, then each paid step's running total (high end)."""
    stops = [0.0]
    for st in m.get("steps") or []:
        if not st["free"] and st["cum_cost"]["high"] > stops[-1] + 0.5:
            stops.append(st["cum_cost"]["high"])
    return stops


def default_budget(m: dict) -> float:
    """The budget the page opens at (capacity.js defaultCapBudget): the largest stop at or under $50 million, else
    the first paid one."""
    stops = budget_stops(m)
    if len(stops) < 2:
        return 0.0
    within = [v for v in stops if v <= DEFAULT_BUDGET]
    return within[-1] if len(within) > 1 else stops[1]


def max_budget(m: dict) -> float:
    return budget_stops(m)[-1]


async def _warm_one(p: Plan) -> str:
    """One budget's grouping and lines written ahead (the raw calls, no locks: this runs on the warm-up's own loop)."""
    units = p.units()
    hit = _group_get(p.gkey)
    if hit is None:
        pk, tr = await gemini_grouping(p, units)
        res = finalize_grouping(p, units, pk, tr, None if pk is not None else ("Gemini unavailable" if tr.get("offline") else "Gemini's grouping failed its checks"))
        _group_put(p.gkey, *res)
        hit = res
    pkgs, trace = hit[0], hit[1]
    parts = prepare(p, pkgs)
    key = (*p.gkey, gsig(pkgs))
    if _ai_get(key) is None:
        res = await gemini_lines(p, parts)
        if res is not None and _ai_get(key) is None:
            _ai_put(key, *res)
        return f"grouped by {trace['by']}, lines {'unavailable' if res is None else ('gemini' if res[0] else 'templates')}"
    return f"grouped by {trace['by']}, lines cached"


def _warm() -> None:
    """Florida's play-by-play written ahead, so the first "Watch it get built" doesn't wait on Gemini: once each of
    unlock's warm studies (UNLOCK_WARM: Florida only, "0" on Render) is in its cache (a baked study is there at once),
    the page's default budget and its largest, for both campus types. The answers land in llm's disk cache too, so the
    next restart is instant. NARRATE_WARM=0 skips."""
    if os.getenv("NARRATE_WARM", "1") == "0" or not llm.configured():
        return
    todo = list(unlock._WARM)
    until = time.monotonic() + WARM_WAIT_S
    while todo and time.monotonic() < until:
        for key in list(todo):
            with unlock._cache_lock:
                hit = unlock._cache.get(key)
            if hit is None:
                continue
            todo.remove(key)
            if hit.get("already_failing") or not (hit.get("capacity") or {}).get("firm"):
                continue
            for mode in ("firm", "flexible"):
                mm = hit["capacity"].get(mode) or {}
                for budget in dict.fromkeys([default_budget(mm), max_budget(mm)]):
                    try:
                        p = Plan(key, hit, mode, budget)
                        if p.bought <= p.today:
                            continue
                        how = asyncio.run(_warm_one(p))
                        log.info("strengthen narration: warmed %s %s $%.0fM (%s)", key, mode, budget / 1e6, how)
                    except Exception:  # noqa: BLE001 - best effort: the page's own request writes it anyway
                        log.exception("strengthen narration: warm-up failed")
        if todo:
            time.sleep(WARM_POLL_S)


if unlock._WARM:
    _warmer = threading.Timer(unlock.WARM_DELAY_S + 2.0, _warm)
    _warmer.daemon = True  # never holds the process open at shutdown
    _warmer.start()


# ------------------------------------------------------------------------------------------ cues
def spread_cues(text: str, sentence: str, values: list[int]) -> list[dict]:
    """Step cues spread over the words of one sentence (the first at its start): today's campuses appear one by one
    while the intro names them."""
    if not values:
        return []
    at0 = text.find(sentence) if sentence and sentence in text else 0
    sent = sentence if sentence and sentence in text else text.split(". ")[0]
    starts = [at0 + m.start() for m in re.finditer(r"\S+", sent)] or [at0]
    k = len(starts)
    return [{"char": starts[round(i * (k - 1) / len(values))], "name": "step", "value": v} for i, v in enumerate(values)]


def first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return m.group(1) if m else text


def cues_for(s: dict, text: str, first: str | None) -> list[dict]:
    cues = []
    if s["kind"] == "intro":
        cues += spread_cues(text, first if first and first in text else first_sentence(text), s["steps"])
    elif s["kind"] == "package":
        # nothing new when the package enters (its upgrades draw first); its campuses drop in over the rest of the
        # words, the first about a third of the way in, the last near the end
        cues.append({"char": 0, "name": "step", "value": s["step_from"]})
        L = len(text)
        starts = [m.start() for m in re.finditer(r"\S+", text) if 0.3 * L <= m.start() <= 0.9 * L] or [int(0.3 * L)]
        ns = s["steps"]
        for i, n in enumerate(ns):
            j = round(i * (len(starts) - 1) / (len(ns) - 1)) if len(ns) > 1 else 0
            cues.append({"char": starts[j], "name": "step", "value": n})
    cues.append({"char": len(text), "name": "step", "value": s["step"]})  # the slide always ends on its own picture
    return sorted(cues, key=lambda c: (c["char"], c["value"]))


def headline(p: Plan, s: dict, lang: str) -> str:
    en = lang == "en"
    if s["kind"] == "intro":
        return (f"{p.today} at once today" if en else f"{p.today} a la vez hoy")
    if s["kind"] == "package":
        pk = s["pkg"]
        return f"{pk['name'][lang]} · {usd_show(pk['cost_high'])}"
    if p.bought > p.today:
        return f"{p.bought} at once for {usd_show(p.cost['high'])}" if en else f"{p.bought} a la vez por {usd_show(p.cost['high'])}"
    return (f"{p.bought} at once, no upgrades" if en else f"{p.bought} a la vez, sin mejoras")


# ------------------------------------------------------------------------------------------ the route
class NarrationIn(BaseModel):
    region: str = Field(DEFAULT_REGION, max_length=8)
    mw: float = Field(1000.0, allow_inf_nan=False)
    load_factor: float = Field(1.0, allow_inf_nan=False)
    mode: Literal["firm", "flexible"] = "firm"
    budget: float = Field(0.0, ge=0, le=1e12, allow_inf_nan=False)  # USD, high end (the page's budget stop)
    lang: Literal["en", "es"] = "en"
    ai: bool = True


def finished_study(body: NarrationIn) -> tuple[tuple, dict]:
    """The finished unlock study for this state, size and load level (never starts one), or a 409 that says why."""
    key = unlock._key_of(unlock.UnlockIn(region=body.region, mw=body.mw, load_factor=body.load_factor))
    with unlock._cache_lock:
        hit = unlock._cache.get(key)
    where = REGIONS.get(key[0], {}).get("name", key[0])
    if hit is None:
        with unlock._jobs_lock:
            running = any(j.key == key and j.status in ("queued", "running") for j in unlock._jobs.values())
        size = f"{key[1] / 1000:g} GW" if key[1] >= 1000 else f"{key[1]:,.0f} MW"
        detail = (f"The study for {size} campuses in {where} is still running: the narration is ready when it finishes." if running
                  else f"There is no finished study for {size} campuses in {where} yet: run it on Strengthen the grid first.")
        raise HTTPException(status_code=409, detail=detail)
    if hit.get("already_failing") or not (hit.get("capacity") or {}).get("firm"):
        raise HTTPException(status_code=409, detail="This study has no campuses-at-once plan to narrate (the grid fails at this load level with no data center).")
    return key, hit


def est_of(text: str, lang: str) -> float:
    return round(len(text) / CHARS_PER_S[lang] + HOLD_S, 1)


def compose(p: Plan, parts: dict, lang: str, lines: dict, meta: dict, ai_on: bool, grouping: dict) -> dict:
    slides = parts["slides"]
    tmpl = {s["id"]: parts["tmpl"][(s["id"], lang)] for s in slides}
    chosen = {s["id"]: (lines[(s["id"], lang)] if (s["id"], lang) in lines else None) for s in slides}
    # about a minute: past TOTAL_MAX_S (English pace) the Gemini lines that run longest past their templates give way
    total = sum(est_of(chosen[s["id"]] or tmpl[s["id"]], lang) for s in slides) * (CHARS_PER_S[lang] / CHARS_PER_S["en"])
    for s in sorted(slides, key=lambda x: -(len(chosen[x["id"]] or "") - len(tmpl[x["id"]]))):
        if total <= TOTAL_MAX_S:
            break
        if chosen[s["id"]] and len(chosen[s["id"]]) > len(tmpl[s["id"]]):
            total -= (len(chosen[s["id"]]) - len(tmpl[s["id"]])) / CHARS_PER_S["en"]
            chosen[s["id"]] = None
    # ... and past it still (a long build's plain script), the templates speak their short forms (the intro without
    # "and N more weak points like it", a package without its reason), biggest saving first; the cards keep both
    short = parts.get("tmpl_short") or {}
    for s in sorted(slides, key=lambda x: -(len(tmpl[x["id"]]) - len(short.get((x["id"], lang)) or tmpl[x["id"]]))):
        if total <= TOTAL_MAX_S:
            break
        sh = short.get((s["id"], lang))
        if chosen[s["id"]] is None and sh and len(sh) < len(tmpl[s["id"]]):
            total -= (len(tmpl[s["id"]]) - len(sh)) / CHARS_PER_S["en"]
            tmpl[s["id"]] = sh
    out, flat = [], []
    for s in slides:
        use_ai = chosen[s["id"]] is not None
        text = chosen[s["id"]] if use_ai else tmpl[s["id"]]
        cues = cues_for(s, text, None if use_ai else parts["first"].get(lang))
        seg = {"role": "presenter", "text": text, "chars": len(text), "cues": cues}
        flat.append(seg)
        out.append({
            "id": s["id"], "kind": s["kind"], "step_from": s["step_from"], "step": s["step"], "steps": s["steps"],
            "package": s["pkg"]["i"] if s["kind"] == "package" else None,
            "headline": {lang: headline(p, s, lang)},
            "narration": {lang: [seg]},
            "est_s": {lang: est_of(text, lang)},
            "written_by": {lang: "gemini" if use_ai else "template"},
        })
    for i, seg in enumerate(flat):  # voice keys, with the neighbors' text for smoother intonation across slides
        seg["key"] = voice.register(seg["text"], lang, "presenter", prev_text=flat[i - 1]["text"] if i else None,
                                    next_text=flat[i + 1]["text"] if i + 1 < len(flat) else None, cues=seg["cues"])
    by = [s["written_by"][lang] for s in out]
    written = "gemini" if all(b == "gemini" for b in by) else ("template" if all(b == "template" for b in by) else "mixed")
    size = f"{p.mw / 1000:g} GW" if p.mw >= 1000 else f"{p.mw:,.0f} MW"
    return {
        "version": VERSION,
        "region": p.code, "region_name": p.state, "mw": p.mw, "load_factor": p.lf, "mode": p.mode, "lang": lang,
        "title": {lang: (f"Watch it get built · {p.state} · {size} campuses" if lang == "en" else f"Así se construye · {p.state_es} · campus de {size}")},
        "today": p.today, "steps_total": len(p.steps), "stop": p.m.get("stop"),
        "bought": {"n": p.bought, "cost_low": round(p.cost["low"]), "cost_high": round(p.cost["high"])},
        "plants": {"campuses": p.plants_n, "reserve_pct": p.reserve, "binds": p.binds()},
        "slides": out,
        "packages": [public_pkg(pk) for pk in parts["pkgs"]],
        "problems": parts["problems"],
        "grouping": grouping,
        "est_s": {lang: round(sum(s["est_s"][lang] for s in out), 1)},
        "ai": {"by": written, "fallback": written == "template", "requested": ai_on, "surface": SURFACE, **meta},
        "facts": parts["facts"].public(),
        "names": sorted(parts["facts"].names),
        "synthetic": True,
        "note": "Synthetic grid model (Breakthrough Energy / Texas A&M), not any utility's network; costs are the high end of estimates.",
    }


@router.post("/api/strengthen/narration")
@limiter.limit("30/minute")
async def narration(request: Request, body: NarrationIn):
    key, hit = finished_study(body)
    p = Plan(key, hit, body.mode, body.budget)
    ck = (key, p.mode, p.bought, p.fp, body.lang, body.ai, llm.configured())
    cached = _scripts.get(ck)
    if cached is not None and not (body.ai and ((cached["ai"]["fallback"] and cached["ai"].get("reason") in RETRYABLE)
                                                or cached["grouping"].get("reason") in GROUP_RETRYABLE)):
        _scripts.move_to_end(ck)
        return cached
    pkgs, grouping = await grouping_for(p, p.units(), body.ai)
    parts = await run_in_threadpool(prepare, p, pkgs)
    lines, meta = {}, {"fallback": True, "reason": "AI off for this request", "numbers_checked": 0, "rejected": 0}
    if body.ai:
        lines, meta = await ai_lines(p, parts)
    script = await run_in_threadpool(compose, p, parts, body.lang, lines, meta, body.ai, grouping)
    _scripts[ck] = script
    while len(_scripts) > SCRIPTS_MAX:
        _scripts.popitem(last=False)
    return script
