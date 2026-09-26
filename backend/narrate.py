"""The narrated build-up for Strengthen the grid: when the viewer presses "Watch it get built", a presenter voice
says what the map draws, campus by campus ("Campus three goes in at Crystal River. The Jacksonville 64
transformer stopped it at all thirty sites tried; raising it from 235 to 350 megavolt-amperes costs up to $6.24
million."). CLAUDE.md -> Decisions -> STRENGTHEN IS THE HEART, GEMINI MAX, MUTED, AI SURFACES.

POST /api/strengthen/narration {region, mw, load_factor, mode: firm|flexible, budget (USD, high end), lang: en|es, ai}
  -> a script shaped for the narration engine the deck uses (frontend useNarration): slides[{id, kind, step_from,
     step, steps, headline{lang}, narration{lang: [{role, text, chars, cues, key}]}, est_s{lang}, written_by{lang}}]
     plus the facts every number comes from, the names that hold digits, and how Gemini did.
  409 while no finished study exists for that state, size and load level (it never starts one: LAZY), 422 bad body.

The script: an INTRO (today's campuses at once, what stops the next one; the synthetic model named once), one
slide per PAID campus step the budget buys (a free step after it rides along in the same slide), and a CLOSING
(what the budget buys at once, the power plants' reserve line, what the next campus would take). Each slide says
which campuses the map shows: `step_from` when it enters, `step` cues as the words reach them, `step` when it ends.

Gemini (one call, both languages, a JSON schema; surface "strengthen_narration", the agent model at minimal
thinking) writes the lines from a fact sheet whose numbers are already in their spoken form. Every line is checked:
each digit must be one of that slide's facts (names that hold digits masked), spelled numbers must be ones the
data has, the place and the step's cost must be said, the intro must name the synthetic model, no abbreviations
the voice would stumble on, no real utility or company names. The lines that fail go back to Gemini once with the
checks' reasons and are checked again (proposal -> checks -> feedback -> revision); a line that still fails keeps its
template. With no key, no quota, or ai=false, the templates run (labeled). Florida's default budgets (both campus
types) are written ahead once unlock's warm study is in (UNLOCK_WARM; NARRATE_WARM=0 skips), so the first click
doesn't wait on Gemini. Every segment is registered with voice.py, so the page asks
ElevenLabs for it by key; the browser voice or captions alone are the page's fallbacks.

Everything describes the SYNTHETIC grid model (Breakthrough Energy / Texas A&M), not any utility's network; costs
are the high end of labeled estimates.
"""

import asyncio
import difflib
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import llm
import unlock
import voice
from bulletin import CHARS_PER_S, FORBIDDEN_LOCAL, STATE_ES, TRANSLATED_NAMES, mw_say, num, usd_say, usd_show, words
from grid import DEFAULT_REGION, REGIONS
from limiter import limiter

router = APIRouter(tags=["narrate"])
log = logging.getLogger("uvicorn.error")

VERSION = 2
LANGS = ("en", "es")
SURFACE = "strengthen_narration"
AI_TIMEOUT_S = 10  # per socket read inside llm
AI_DEADLINE_S = 16  # the whole call
LINE_MAX = {"en": 300, "es": 360}  # a slide's spoken line, characters
HOLD_S = 0.8  # the narration engine's pause before the next slide
SCRIPTS_MAX = 64
AI_MAX = 32
REVISE_MIN_S = 5.0  # a revision round (the failed lines, with the checks' reasons) is asked only with this much time left
RETRY_S = 60.0  # a case where no Gemini line passed is asked again after this long, not on every request
RETRYABLE = ("Gemini unavailable", "every line failed its checks")
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

_scripts: "OrderedDict[tuple, dict]" = OrderedDict()
_ai: "OrderedDict[tuple, tuple[dict, dict, float]]" = OrderedDict()  # (lines, meta, when)
_ai_mutex = threading.Lock()  # the warm-up thread writes _ai too
_ai_locks: dict[tuple, asyncio.Lock] = {}


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


# ------------------------------------------------------------------------------------------ the script
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

    def size(self, lang: str, adj: bool = True) -> str:
        return mw_say(self.mw, lang, adj=adj)

    def slides(self) -> list[dict]:
        """The slide list (no text yet): intro, one per paid step bought (free ones after it ride along), closing."""
        out = [{"id": "intro", "kind": "intro", "step_from": 0, "step": self.today, "steps": list(range(1, self.today + 1))}]
        cur = None
        for st in self.steps[self.today: self.bought]:
            if not st["free"] or cur is None:
                cur = {"id": f"step-{st['n']}", "kind": "step", "step_from": st["n"], "step": st["n"], "steps": [st["n"]], "main": st, "free": []}
                out.append(cur)
            else:
                cur["free"].append(st)
                cur["steps"].append(st["n"])
                cur["step"] = st["n"]
        out.append({"id": "close", "kind": "close", "step_from": self.bought, "step": self.bought, "steps": []})
        return out


def build_facts(p: Plan, slides: list[dict]) -> Facts:
    f = Facts()
    f.add("campus.mw", "Campus size", p.mw, "MW")
    f.add("today", "Campuses the grid model carries at once today, with no upgrades", p.today, "campuses")
    f.add("bought.n", "Campuses at once with the upgrades the budget buys", p.bought, "campuses")
    f.add("bought.cost_high", "What those upgrades cost, high end", round(p.cost["high"]), "USD")
    if p.plants_n is not None:
        f.add("plants.campuses", "Campuses the power plants cover with the planning reserve kept", p.plants_n, "campuses")
        f.add("plants.reserve_pct", "Planning reserve kept on the state's own load", p.reserve, "%")
    for st in p.steps[p.today: p.bought]:
        f.name(st["site"]["area"])
    for st in p.steps[: p.today]:
        f.name(st["site"]["area"])
    if p.first_block:
        f.branch(p.first_block)
        f.add("first_block.campus", "The campus the first blocker stops", p.first_block.get("at_campus"), "", "intro")
    for s in slides:
        if s["kind"] != "step":
            continue
        st, sid = s["main"], s["id"]
        f.add(f"{sid}.n", "Campus number", st["n"], "", sid)
        f.add(f"{sid}.cost_high", "What this campus's upgrades cost, high end", round(st["cost"]["high"]), "USD", sid)
        f.add(f"{sid}.cum_high", "Running total, high end", round(st["cum_cost"]["high"]), "USD", sid)
        f.add(f"{sid}.upgrades", "Lines and transformers raised for it", len(st["projects"]), "", sid)
        b = st.get("blocked_by")
        if b:
            f.branch(b)
            f.add(f"{sid}.blocks", "Sites tried where that line reached its rating first", b.get("blocks"), "sites", sid)
            f.add(f"{sid}.of", "Sites tried for this campus", b.get("of"), "sites", sid)
        lead = lead_project(st)
        for j, pj in enumerate([lead] + [x for x in st["projects"] if x is not lead][:2]):
            f.branch(pj)
            f.add(f"{sid}.p{j}.before", f"{pj.get('short')}: rating before", half_up(pj["rating_before_mva"]), "MVA", sid)
            f.add(f"{sid}.p{j}.after", f"{pj.get('short')}: rating after", half_up(pj["rating_after_mva"]), "MVA", sid)
        for fr in s["free"]:
            f.add(f"{sid}.free{fr['n']}", "A campus that then fits with no new upgrade", fr["n"], "", sid)
            f.name(fr["site"]["area"])
    nxt = p.steps[p.bought] if p.bought < len(p.steps) else None
    if nxt:
        f.add("next.n", "The next campus", nxt["n"], "", "close")
        f.add("next.cum_high", "What the next campus would take in all, high end", round(nxt["cum_cost"]["high"]), "USD", "close")
    f.name(p.state, p.state_es)
    return f


def lead_project(st: dict) -> dict:
    b = st.get("blocked_by") or {}
    return next((pj for pj in st["projects"] if pj.get("branch_id") == b.get("branch_id")), None) or max(st["projects"], key=lambda pj: pj["cost"]["high"])


def _usd(v: float, lang: str) -> str:
    return usd_say(v, lang)[0]


PLACES_ALL = 4  # the intro names every place up to four; past that, three and "N more places"


def today_places(p: Plan) -> tuple[list[str], int]:
    """(the places the intro names, how many more it counts): today's campuses' places, each once, in order."""
    areas = list(dict.fromkeys(st["site"]["area"] for st in p.steps[: p.today]))
    if len(areas) <= PLACES_ALL:
        return areas, 0
    return areas[:3], len(areas) - 3


def places_phrase(p: Plan, lang: str) -> str:
    """'Intercession City, Spring Hill, Gainesville and Crystal River' / '..., and two more places'."""
    named, more = today_places(p)
    extra = [] if more <= 0 else [f"{words(more, 'en')} more {'place' if more == 1 else 'places'}" if lang == "en"
                                  else f"{words(more, 'es', before_noun=True)} {'lugar' if more == 1 else 'lugares'} más"]
    return join(named + extra, lang)


def tmpl_intro(p: Plan, lang: str) -> tuple[str, str]:
    """(text, its first sentence: the step cues spread over it)."""
    en = lang == "en"
    t = p.today
    where = places_phrase(p, lang)
    if en:
        kind = "flexible " if p.flex else ""
        if t:
            s1 = f"On the synthetic grid model, {p.state} carries {words(t, 'en')} more {kind}{p.size('en')} data {'center' if t == 1 else 'centers'} at once today, with no upgrades: at {where}."
        else:
            s1 = f"On the synthetic grid model, {p.state} can't carry even one more {kind}{p.size('en')} data center at once without upgrades."
    else:
        kind = " flexibles" if p.flex else ""
        if t:
            s1 = (f"En el modelo sintético de la red, {p.state_es} soporta hoy {words(t, 'es', before_noun=True)} {'centro' if t == 1 else 'centros'} de datos"
                  f"{kind if t > 1 else (' flexible' if p.flex else '')} más, de {p.size('es')}{' cada uno' if t > 1 else ''}, a la vez, sin mejoras: en {where}.")
        else:
            s1 = f"En el modelo sintético de la red, {p.state_es} no soporta ni un centro de datos{' flexible' if p.flex else ''} más de {p.size('es')} sin mejoras."
    parts = [s1]
    fb = p.first_block
    if fb and fb.get("at_campus") == t + 1:
        parts.append(f"{cap(label_of(fb, 'en'))} stops the {ordinal(t + 1, 'en')}." if en
                     else f"Al {ordinal(t + 1, 'es')} lo frena {label_of(fb, 'es')}.")
    if p.flex:
        parts.append("A flexible campus runs at full power except on peak afternoons, when it drops to half." if en
                     else "Un campus flexible funciona a plena potencia salvo en las tardes de máxima demanda, cuando baja a la mitad.")
    if p.bought > t:
        parts.append(f"Here is what {_usd(p.cost['high'], 'en')} of upgrades builds, campus by campus." if en
                     else f"Esto es lo que construyen {_usd(p.cost['high'], 'es')} en mejoras, campus por campus.")
    return " ".join(parts), s1


def tmpl_step(p: Plan, s: dict, lang: str) -> str:
    en = lang == "en"
    st = s["main"]
    n, area = st["n"], st["site"]["area"]
    parts = [f"Campus {words(n, 'en')} goes in at {area}." if en else f"El campus {words(n, 'es')} se conecta en {area}."]
    b = st.get("blocked_by")
    if b:
        blocks, of = int(b.get("blocks") or 0), int(b.get("of") or 0)
        if en:
            where = f"all {words(of, 'en')} sites tried" if blocks >= of else f"{words(blocks, 'en')} of the {words(of, 'en')} sites tried"
            parts.append(f"{cap(label_of(b, 'en'))} stopped it at {where}.")
        else:
            where = f"los {words(of, 'es')} sitios probados" if blocks >= of else f"{words(blocks, 'es')} de los {words(of, 'es')} sitios probados"
            parts.append(f"{cap(label_of(b, 'es'))} lo frenó en {where}.")
    pj = lead_project(st)
    lo, hi = mva(pj["rating_before_mva"], lang), mva(pj["rating_after_mva"], lang)
    usd = _usd(st["cost"]["high"], lang)
    one = len(st["projects"]) == 1
    same = bool(b) and pj.get("branch_id") == b.get("branch_id")  # the upgrade raises what stopped it
    tr = pj.get("kind") == "transformer"
    if en:
        if one and same:
            parts.append(f"Raising it from {lo} to {hi} megavolt-amperes costs up to {usd}.")
        elif one:
            parts.append(f"The fix raises {label_of(pj, 'en')} from {lo} to {hi} megavolt-amperes, for up to {usd}.")
        else:
            led = f"that {'transformer' if tr else 'line'}" if same else label_of(pj, "en")
            parts.append(f"The fix raises {kinds_phrase(st['projects'], 'en')}, led by {led} from {lo} to {hi} megavolt-amperes: up to {usd} in all.")
    else:
        if one and same:
            parts.append(f"Ampliarlo de {lo} a {hi} megavoltamperios cuesta hasta {usd}.")
        elif one:
            parts.append(f"La solución amplía {label_of(pj, 'es')} de {lo} a {hi} megavoltamperios, por hasta {usd}.")
        else:
            led = ("ese transformador" if tr else "esa línea") if same else label_of(pj, "es")
            parts.append(f"La solución amplía {kinds_phrase(st['projects'], 'es')}, empezando por {led}, de {lo} a {hi} megavoltamperios: hasta {usd} en total.")
    free = s["free"]
    if len(free) == 1:
        fr = free[0]
        parts.append(f"Campus {words(fr['n'], 'en')} then fits at {fr['site']['area']} with no new upgrade." if en
                     else f"Después, el campus {words(fr['n'], 'es')} cabe en {fr['site']['area']} sin otra mejora.")
    elif free:
        ns = join([words(fr["n"], lang) for fr in free], lang)
        places = join([fr["site"]["area"] for fr in free], lang)
        parts.append(f"Then campuses {ns} fit at {places}, with no new upgrade." if en
                     else f"Después, los campus {ns} caben en {places}, sin otra mejora.")
    return " ".join(parts)


def tmpl_close(p: Plan, lang: str) -> str:
    en = lang == "en"
    n, t = p.bought, p.today
    parts = []
    if n > t:
        if en:
            parts.append(f"With {_usd(p.cost['high'], 'en')} of upgrades, the grid model carries {words(n, 'en')} {'flexible ' if p.flex else ''}{p.size('en')} data centers at once, up from {words(t, 'en')} today.")
        else:
            parts.append(f"Con {_usd(p.cost['high'], 'es')} en mejoras, el modelo de la red soporta {words(n, 'es', before_noun=True)} centros de datos{' flexibles' if p.flex else ''} de {p.size('es')} a la vez, frente a {words(t, 'es', before_noun=True)} hoy.")
    elif n:
        parts.append(f"Without upgrades, the grid model stops at {words(n, 'en')}." if en else f"Sin mejoras, el modelo de la red se queda en {words(n, 'es', before_noun=True)}.")
    else:
        parts.append("Without upgrades, no campus of this size fits." if en else "Sin mejoras, no cabe ningún campus de este tamaño.")
    pn = p.plants_n
    if pn is not None and n > 0:
        r = num(p.reserve, lang)
        if pn == 0:
            parts.append(f"The power plants can't cover even one with a {r} percent reserve kept: every campus here also needs new generation or flexibility." if en
                         else f"Las centrales no cubren ni uno manteniendo una reserva del {r} por ciento: cada campus también necesita nueva generación o flexibilidad.")
        elif pn < n:
            parts.append(f"The power plants cover {words(pn, 'en')} of them with a {r} percent reserve kept; past that, campuses also need new generation or flexibility, not only wires." if en
                         else f"Las centrales cubren {words(pn, 'es', before_noun=True)} de ellos con una reserva del {r} por ciento; a partir de ahí, los campus también necesitan nueva generación o flexibilidad, no solo cables.")
        else:
            them = ("it" if n == 1 else "both" if n == 2 else f"all {words(n, 'en')}") if en else ("ese campus" if n == 1 else f"los {words(n, 'es', before_noun=True)}")
            parts.append(f"The power plants cover {them} with a {r} percent reserve kept; past {words(pn, 'en')}, campuses also need new generation or flexibility." if en
                         else f"Las centrales cubren {them} con una reserva del {r} por ciento; a partir de {words(pn, 'es', before_noun=True)}, los campus también necesitan nueva generación o flexibilidad.")
    nxt = p.steps[n] if n < len(p.steps) else None
    if nxt:
        parts.append(f"The {ordinal(nxt['n'], 'en')} would take {_usd(nxt['cum_cost']['high'], 'en')} in all." if en
                     else f"El {ordinal(nxt['n'], 'es')} costaría {_usd(nxt['cum_cost']['high'], 'es')} en total.")
    else:
        stop = STOP.get(p.m.get("stop"), STOP["max"])
        parts.append(f"Then {stop[0]}." if en else f"Después, {stop[1]}.")
    return " ".join(parts)


def templates(p: Plan, slides: list[dict]) -> dict:
    """{(slide id, lang): text} and the intro's first sentence per language."""
    out, first = {}, {}
    for lang in LANGS:
        for s in slides:
            if s["kind"] == "intro":
                out[(s["id"], lang)], first[lang] = tmpl_intro(p, lang)
            elif s["kind"] == "step":
                out[(s["id"], lang)] = tmpl_step(p, s, lang)
            else:
                out[(s["id"], lang)] = tmpl_close(p, lang)
    return {"text": out, "first": first}


# ------------------------------------------------------------------------------------------ Gemini
SYSTEM = """You are the presenter narrating a build-up on a map: data-center campuses connect one by one to a
SYNTHETIC grid model of a U.S. state (Breakthrough Energy / Texas A&M), not any real utility's network, and the
upgrades that make room for each one are drawn as you speak. For each slide you get its PURPOSE and its DATA (values
computed by the grid engine, with every number already in its spoken form in English and Spanish). Write what the
presenter says on that slide, once in English and once in Spanish.

Rules:
- Short spoken sentences in the present tense, calm and clear, one idea per sentence. Lead with the campus and the place.
- Say only what the DATA says. Write every number exactly in its given spoken form ("$6.24 million", "from 235 to 350
  megavolt-amperes"; Spanish "6.24 millones de dólares", "megavoltamperios"). Never compute a new number: no sums,
  differences, ratios, percentages or comparisons ("twice", "half") of your own.
- Campus numbers and small counts as words, as the DATA writes them ("campus three", "all thirty sites").
- Keep every place, line and transformer name exactly as the DATA writes it, in both languages; never translate a place.
- Never write "MW", "MVA", "GW" or "%": say megawatts / megavolt-amperes / percent (megavatios / megavoltamperios / por ciento).
- Costs are the high end of an estimate: say "up to" (Spanish "hasta").
- The intro names the synthetic grid model once ("on the synthetic grid model" / "en el modelo sintético de la red");
  no other slide repeats it.
- Never name a real utility, company, agency or project; no dates or years; no advice to the public.
- Stay within each slide's max_chars.
- The Spanish is written natively for a U.S. Spanish-speaking audience, not translated word for word. "Campus" stays
  "campus" (plural "los campus"), never "campamento". The size comes after the noun: "dos centros de datos de 1000
  megavatios" or "dos campus de 1000 megavatios", never "dos 1000 megavatios campus".
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
    "intro": "how many campuses fit at once today and where (say where_they_connect whole: every place in it, and its 'more places' when it has them); what stops the next one; that this is the synthetic grid model (say it once); what the budget builds next",
    "step": "the campus that goes in and where; what stopped it at the sites tried; the upgrade that lets it in and its cost; then EVERY campus listed in then_fits_with_no_new_upgrade, by number and place",
    "close": "how many campuses fit at once with the budget's upgrades, against today; the power plants' reserve line (past it campuses also need new generation or flexibility); what the next campus would take",
}


def _both(fn) -> dict:
    return {lang: fn(lang) for lang in LANGS}


def slide_data(p: Plan, s: dict) -> dict:
    """The slide's content as data, every number in its spoken form (no sentences to copy)."""
    d: dict = {"campus_size": _both(lambda lang: p.size(lang)), "campus_type": "flexible (full power except on peak afternoons, half power then)" if p.flex else "always on"}
    if s["kind"] == "intro":
        t = p.today
        d["state"] = {"en": p.state, "es": p.state_es}
        d["campuses_at_once_today_with_no_upgrades"] = _both(lambda lang: words(t, lang))
        d["where_they_connect"] = _both(lambda lang: places_phrase(p, lang))  # every place, or three and "N more places"
        fb = p.first_block
        if fb and fb.get("at_campus") == t + 1:
            d["what_stops_the_next_campus"] = _both(lambda lang: label_of(fb, lang))
            d["the_next_campus"] = _both(lambda lang: ordinal(t + 1, lang))
        if p.bought > t:
            d["the_budget_builds_next_high_end"] = _both(lambda lang: _usd(p.cost["high"], lang))
        d["must_say_once"] = {"en": "on the synthetic grid model", "es": "en el modelo sintético de la red"}
    elif s["kind"] == "step":
        st = s["main"]
        d["campus_number"] = _both(lambda lang: words(st["n"], lang))
        d["place"] = st["site"]["area"]
        b = st.get("blocked_by")
        if b:
            d["what_stopped_it"] = _both(lambda lang: label_of(b, lang))
            d["at_sites_tried"] = ({"en": f"all {words(b['of'], 'en')}", "es": f"los {words(b['of'], 'es')}"} if b["blocks"] >= b["of"]
                                   else {"en": f"{words(b['blocks'], 'en')} of the {words(b['of'], 'en')}", "es": f"{words(b['blocks'], 'es')} de los {words(b['of'], 'es')}"})
        pj = lead_project(st)
        d["upgrades"] = [{"name": _both(lambda lang, x=x: label_of(x, lang)), "rating_from": _both(lambda lang, x=x: mva(x["rating_before_mva"], lang)),
                          "rating_to": _both(lambda lang, x=x: mva(x["rating_after_mva"], lang)), "unit": {"en": "megavolt-amperes", "es": "megavoltamperios"}}
                         for x in [pj] + [x for x in st["projects"] if x is not pj][:2]]
        if len(st["projects"]) > 1:
            d["all_upgrades_for_this_campus"] = _both(lambda lang: kinds_phrase(st["projects"], lang))
        d["cost_high_end"] = _both(lambda lang: _usd(st["cost"]["high"], lang))
        if s["free"]:
            d["then_fits_with_no_new_upgrade"] = [{"campus_number": _both(lambda lang, fr=fr: words(fr["n"], lang)), "place": fr["site"]["area"]} for fr in s["free"]]
    else:
        n, t = p.bought, p.today
        d["campuses_at_once_with_the_budget"] = _both(lambda lang: words(n, lang))
        d["campuses_at_once_today"] = _both(lambda lang: words(t, lang))
        if n > t:
            d["upgrades_cost_high_end"] = _both(lambda lang: _usd(p.cost["high"], lang))
        if p.plants_n is not None and n > 0:
            d["power_plants_cover_with_the_reserve_kept"] = _both(lambda lang: words(p.plants_n, lang))
            d["planning_reserve"] = {"en": f"{num(p.reserve, 'en')} percent", "es": f"{num(p.reserve, 'es')} por ciento"}
            d["past_the_power_plants"] = "campuses also need new generation or flexibility, not only wires"
        nxt = p.steps[n] if n < len(p.steps) else None
        if nxt:
            d["the_next_campus"] = _both(lambda lang: ordinal(nxt["n"], lang))
            d["the_next_campus_would_take_in_all_high_end"] = _both(lambda lang: _usd(nxt["cum_cost"]["high"], lang))
        else:
            d["then"] = {"en": STOP.get(p.m.get("stop"), STOP["max"])[0], "es": STOP.get(p.m.get("stop"), STOP["max"])[1]}
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
    return min(max(LINE_MAX[lang], len(template) + 40), max(int(len(template) * 1.35) + 50, 140))


def validate(p: Plan, s: dict, lang: str, text: str, template: str, facts: Facts) -> tuple[bool, str | None, int]:
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
    if s["kind"] == "intro" and ("synthetic" if lang == "en" else "sintetic") not in folded:
        return False, "the intro must name the synthetic model", 0
    if s["kind"] == "intro":
        named, more = today_places(p)
        for area in named:  # every campus that fits today is placed (no "four ... at three places")
            if _fold(area) not in folded:
                return False, f"does not say where: {area}", 0
        if more > 0 and not re.search(r"\b(more|other|others|mas|otros|otras)\b", folded):
            return False, "drops the other places today's campuses connect at", 0
    if lang == "es" and re.search(r"campament|megavatios\s+(?:campus|centros?)\b", folded):
        return False, "Spanish wording: 'campamento', or the size before the noun", 0
    if lang == "es" and re.search(r"\b(to|the|and|from|with|of)\b", facts.mask(text), re.IGNORECASE):
        return False, "an English word in the Spanish line", 0  # "de 137 to 200 megavoltamperios" (names masked first)
    if s["kind"] == "step":
        st = s["main"]
        for area in [st["site"]["area"]] + [fr["site"]["area"] for fr in s["free"]]:
            if _fold(area) not in folded:
                return False, f"does not say where: {area}", 0
        figure = _NUM.search(usd_say(st["cost"]["high"], lang)[0]).group(0)  # "6.2" of "$6.2 million"
        if figure not in text:
            return False, "does not say this campus's cost", 0
    if s["kind"] == "close" and p.bought > 0:
        n = p.bought
        forms = {words(n, lang), words(n, lang, before_noun=True), str(n)}
        if not any(re.search(rf"\b{re.escape(x)}\b", low) for x in forms):
            return False, "does not say how many fit at once", 0
        if p.plants_n is not None and ("generation" if lang == "en" else "generacion") not in folded:
            return False, "drops the power plants' line (past it, new generation or flexibility)", 0
    data_text = json.dumps(slide_data(p, s), ensure_ascii=False)
    known = number_words(template, lang) | number_words(data_text, lang) | facts.known_words(s["id"], lang)
    extra = number_words(facts.mask(text), lang) - known
    if extra:
        return False, f"a spelled number or ratio not in the data: {sorted(extra)}", 0
    return check_numbers(text, facts, s["id"])


def prompt_of(p: Plan, slides: list[dict], tmpl: dict, facts: Facts) -> str:
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
        lines.append("  DATA: " + json.dumps(slide_data(p, s), ensure_ascii=False))
    lines += ["", 'Write every slide now, as JSON {"slides": [{"id": "...", "en": "...", "es": "..."}]}.']
    return "\n".join(lines)


async def _ask(prompt: str, budget_s: float) -> tuple[dict, bool]:
    """One Gemini call within `budget_s` seconds: (JSON, offline)."""
    none = {"slides": []}
    try:
        return await asyncio.wait_for(
            llm.complete_json(prompt, system=SYSTEM, fallback=none, timeout=AI_TIMEOUT_S, schema=SCHEMA, surface=SURFACE,
                              model=llm.AGENT_MODEL, thinking=llm.AGENT_THINKING),
            max(1.0, budget_s))
    except asyncio.TimeoutError:
        log.warning("strengthen narration: Gemini took over %.0fs; templates used", budget_s)
        return none, True


def _rows(data) -> dict:
    got = {}
    for row in (data.get("slides") if isinstance(data, dict) else None) or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            got[row["id"]] = row
    return got


def _check(p: Plan, slides: list[dict], tmpl: dict, facts: Facts, got: dict, only: set | None = None):
    """Gemini's lines through validate(): ({(id, lang): text}, numbers checked, [(slide, lang, text, why)])."""
    ok_lines, checked, failed = {}, 0, []
    for s in slides:
        for lang in LANGS:
            if only is not None and (s["id"], lang) not in only:
                continue
            text = _clean((got.get(s["id"]) or {}).get(lang))
            template = tmpl[(s["id"], lang)]
            if text and _same(text, template):
                continue  # Gemini gave the template back: it stays labeled a template
            ok, why, n = validate(p, s, lang, text, template, facts)
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
    if s["kind"] == "step" and "cost" in why:
        out.append(f"say {usd_say(s['main']['cost']['high'], lang)[0]!r} exactly as the DATA writes it")
    if "where" in why:
        out.append("name every place in the DATA exactly as written")
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


async def gemini_lines(p: Plan, slides: list[dict], tmpl: dict, facts: Facts) -> tuple[dict, dict] | None:
    """Gemini writes every slide (one call, both languages); the checks run on every line; the lines that fail go back
    once with the reasons (proposal -> checks -> feedback -> revision) and are checked again. A line that still fails
    keeps its template. None: Gemini unavailable (no answer in time, no quota, an error)."""
    t0 = time.monotonic()
    prompt = prompt_of(p, slides, tmpl, facts)
    data, offline = await _ask(prompt, AI_DEADLINE_S)
    if offline:
        return None
    lines, checked, failed = _check(p, slides, tmpl, facts, _rows(data))
    first = [f"{s['id']}/{lang}: {why}" for s, lang, _t, why in failed]
    revised, rounds = 0, 1
    left = AI_DEADLINE_S - (time.monotonic() - t0)
    if failed and left >= REVISE_MIN_S:
        rounds = 2
        data2, offline2 = await _ask(revise_prompt(prompt, failed, tmpl), left)
        if not offline2:
            fixed, n2, failed = _check(p, slides, tmpl, facts, _rows(data2), only={(s["id"], lang) for s, lang, _t, _w in failed})
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


async def ai_lines(p: Plan, slides: list[dict], tmpl: dict, facts: Facts) -> tuple[dict, dict]:
    """({(slide id, lang): checked Gemini line}, meta), cached per study, mode and budget; a line that fails its checks
    (after one revision) keeps its template."""
    key = (p.key, p.mode, p.bought, p.fp)
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
        res = await gemini_lines(p, slides, tmpl, facts)
        _ai_locks.pop(key, None)
        if res is None:
            return {}, {"fallback": True, "reason": "Gemini unavailable", "numbers_checked": 0, "rejected": 0}
        _ai_put(key, *res)
        return dict(res[0]), dict(res[1])


# ------------------------------------------------------------------------------------------ warm-up
def default_budget(m: dict) -> float:
    """The budget the page opens at (capacity.js defaultCapBudget): the largest stop at or under $50 million, else
    the first paid one; the stops are each paid step's running total (high end)."""
    stops = [0.0]
    for st in m.get("steps") or []:
        if not st["free"] and st["cum_cost"]["high"] > stops[-1] + 0.5:
            stops.append(st["cum_cost"]["high"])
    if len(stops) < 2:
        return 0.0
    within = [v for v in stops if v <= DEFAULT_BUDGET]
    return within[-1] if len(within) > 1 else stops[1]


def _warm() -> None:
    """Florida's narration written ahead, so the first "Watch it get built" doesn't wait on Gemini: once each of
    unlock's warm studies (UNLOCK_WARM: Florida only, "0" on Render) is in its cache, the page's default budget for
    both campus types. The answer lands in llm's disk cache too, so the next restart is instant. NARRATE_WARM=0 skips."""
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
                try:
                    p = Plan(key, hit, mode, default_budget(hit["capacity"].get(mode) or {}))
                    k = (p.key, p.mode, p.bought, p.fp)
                    if _ai_get(k) is not None or p.bought <= p.today:
                        continue
                    slides = p.slides()
                    res = asyncio.run(gemini_lines(p, slides, templates(p, slides)["text"], build_facts(p, slides)))
                    if res is not None and _ai_get(k) is None:
                        _ai_put(k, *res)
                    log.info("strengthen narration: warmed %s %s (%s)", key, mode, "unavailable" if res is None else ("gemini" if res[0] else "templates"))
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
    elif s["kind"] == "step":
        cues.append({"char": 0, "name": "step", "value": s["step_from"]})
        low = text.lower()
        prev = 0
        for fr in s["free"]:  # a campus that rides along lands as its place is said (its last mention: the first may be a line's name)
            i = low.rfind(fr["site"]["area"].lower())
            prev = len(text) if i < 0 else max(i, prev)
            cues.append({"char": prev, "name": "step", "value": fr["n"]})
    cues.append({"char": len(text), "name": "step", "value": s["step"]})  # the slide always ends on its own picture
    return sorted(cues, key=lambda c: c["char"])


def headline(p: Plan, s: dict, lang: str) -> str:
    en = lang == "en"
    if s["kind"] == "intro":
        return (f"{p.today} at once today" if en else f"{p.today} a la vez hoy")
    if s["kind"] == "step":
        return f"Campus {s['main']['n']} · {s['main']['site']['area']}"
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


def compose(p: Plan, lang: str, lines: dict, meta: dict, ai_on: bool) -> dict:
    slides = p.slides()
    facts = build_facts(p, slides)
    tm = templates(p, slides)
    out, flat = [], []
    for s in slides:
        use_ai = (s["id"], lang) in lines
        text = lines[(s["id"], lang)] if use_ai else tm["text"][(s["id"], lang)]
        cues = cues_for(s, text, None if use_ai else tm["first"].get(lang))
        seg = {"role": "presenter", "text": text, "chars": len(text), "cues": cues}
        flat.append(seg)
        out.append({
            "id": s["id"], "kind": s["kind"], "step_from": s["step_from"], "step": s["step"], "steps": s["steps"],
            "headline": {lang: headline(p, s, lang)},
            "narration": {lang: [seg]},
            "est_s": {lang: round(len(text) / CHARS_PER_S[lang] + HOLD_S, 1)},
            "written_by": {lang: "gemini" if use_ai else "template"},
            "site": (s["main"]["site"] if s["kind"] == "step" else None),
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
        "slides": out,
        "est_s": {lang: round(sum(s["est_s"][lang] for s in out), 1)},
        "ai": {"by": written, "fallback": written == "template", "requested": ai_on, "surface": SURFACE, **meta},
        "facts": facts.public(),
        "names": sorted(facts.names),
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
    if cached is not None and not (body.ai and cached["ai"]["fallback"] and cached["ai"].get("reason") in RETRYABLE):
        _scripts.move_to_end(ck)
        return cached
    lines, meta = {}, {"fallback": True, "reason": "AI off for this request", "numbers_checked": 0, "rejected": 0}
    if body.ai:
        slides = p.slides()
        facts = await run_in_threadpool(build_facts, p, slides)
        tm = templates(p, slides)
        lines, meta = await ai_lines(p, slides, tm["text"], facts)
    script = await run_in_threadpool(compose, p, body.lang, lines, meta, body.ai)
    _scripts[ck] = script
    while len(_scripts) > SCRIPTS_MAX:
        _scripts.popitem(last=False)
    return script
