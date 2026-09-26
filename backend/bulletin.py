"""The incident briefing writer: turns the engine's report (briefing.py → report_for) into a narrated
slide deck in English and Spanish, and keeps the old one-paragraph bulletin as a thin alias.

POST /api/briefing/deck   body = a briefing case (grid.CaseIn + preset) + {ai, length}
POST /api/bulletin        legacy: {text, fallback, facts} for the v1 bulletin card (until the
                          review stage replaces it)

What the deck is: up to nine slides in a fixed order (event → chain → areas → hospitals → cost →
cause → fix | no_fix → recovery → bottom_line), each with a headline, up to three short lines, a big
number, a camera move and a map mode for the stage, and a narration script per language: segments
spoken by a PRESENTER (framing prose) and an ANALYST (the step-by-step data), with cues at character
offsets ('step', 'area', 'line', 'fix', 'wave', 'slide_end') that the page turns into map actions as
the voice reaches them.

Honesty (CLAUDE.md → Decisions, contract v2): every number comes from the engine's facts. Headlines,
lines, big numbers and analyst segments are deterministic templates filled from the report; only the
presenter's prose may be written by Gemini (M2), and each language of each slide is validated against
the facts (briefing.check_text) and falls back to its template on any doubt. The narration opens with
"This is a simulation on a synthetic grid model…" and closes with "End of simulated briefing…";
those sentences are fixed, never generated. No real utility, agency or storm names, no alert phrasing.

Until the engine track lands report_for, a small local report (the cascade, its timeline and the areas
it darkened; no fixes) keeps the deck and the legacy bulletin working.
"""

import asyncio
import difflib
import hashlib
import json
import logging
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import Field

import voice
from grid import REGIONS, CaseIn, _case_header, check_case, grid_at, region_code
from limiter import limiter
from llm import complete_json
from llm import configured as ai_configured
from powerflow import area_of

router = APIRouter(tags=["bulletin"])
log = logging.getLogger("uvicorn.error")

VERSION = 1
LANGS = ("en", "es")
ORDER = ("toll", "event", "chain", "areas", "hospitals", "cost", "cause", "fix", "no_fix", "recovery", "bottom_line")
# the ≤ 60 s API variant (length=short): the toll first, then who is hit, why it failed, what to do
SHORT = ("toll", "areas", "cause", "fix", "no_fix", "bottom_line")
# the presentation (deck["short"], what "Present the damage" plays): the toll, the play-by-play, the crucial
# infrastructure (hospitals), why it failed, the verified solutions, the bottom line
PRESENT = ("toll", "chain", "areas", "hospitals", "cause", "fix", "no_fix", "bottom_line")
# narration characters; the short deck is the <= 60 s demo version (~14.5 spoken chars/s + pauses)
BUDGET = {"full": {"en": 3800, "es": 4400}, "short": {"en": 1300, "es": 1500}}
# per-segment caps; Spanish runs ~15-20 % longer than English for the same content
PRESENTER_MAX = {"en": 320, "es": 384}
ANALYST_MAX = {"en": 420, "es": 504}
LINE_MAX = 90
CHARS_PER_S = {"en": 14.5, "es": 15.5}  # a newsreader's pace; used for est_s and the no-audio timer
GAP_S, HOLD_S = 0.35, 0.8  # the stage's pause between segments, and before advancing a slide
DECK_CACHE = 64

OPEN = {
    "en": "This is a simulation on a synthetic grid model of {region}.",
    "es": "Esto es una simulación en un modelo sintético de la red eléctrica de {region}.",
}
CLOSE = {
    "en": "End of simulated briefing. All figures are estimates from a synthetic model.",
    "es": "Fin del simulacro informativo. Todas las cifras son estimaciones de un modelo sintético.",
}
TITLE = {"en": "Simulation briefing", "es": "Simulacro informativo"}
# the states whose Spanish name differs (a state is not a substation name: it is translated like any country)
STATE_ES = {
    "New York": "Nueva York", "New Jersey": "Nueva Jersey", "New Mexico": "Nuevo México", "New Hampshire": "Nuevo Hampshire",
    "North Carolina": "Carolina del Norte", "South Carolina": "Carolina del Sur", "North Dakota": "Dakota del Norte",
    "South Dakota": "Dakota del Sur", "Pennsylvania": "Pensilvania", "Louisiana": "Luisiana", "West Virginia": "Virginia Occidental",
}
CREDIT = {
    "en": "Grid: Breakthrough Energy / Texas A&M synthetic USA test system (CC-BY 4.0). People: Census population per model load.",
    "es": "Red: sistema de prueba sintético de EE. UU. de Breakthrough Energy / Texas A&M (CC-BY 4.0). Personas: población del Censo según la carga del modelo.",
}
DISCLAIMER = {
    "en": "Simulation only: a synthetic grid model, not any utility's network, and not a real alert. Every people and cost figure is an estimate.",
    "es": "Solo una simulación: un modelo sintético, no la red de ninguna empresa eléctrica, y no es una alerta real. Toda cifra de personas o costos es una estimación.",
}

# the heat-wave clock's levels (frontend/src/features/heat/presets.js), as the start of a sentence
LOAD_WHEN = {
    0.62: ("Overnight", "De madrugada"),
    0.82: ("In the morning", "Por la mañana"),
    1.0: ("On a summer afternoon", "En una tarde de verano"),
    1.04: ("During a heat wave", "Durante una ola de calor"),
    1.08: ("At the height of a heat wave", "En el pico de una ola de calor"),
}
LOAD_WORD = {0.62: ("overnight", "de madrugada"), 0.82: ("morning", "por la mañana"), 1.0: ("summer afternoon", "tarde de verano"),
             1.04: ("heat wave", "ola de calor"), 1.08: ("peak of a heat wave", "pico de una ola de calor")}

# catastrophes.json presets, spoken (numbers in words: a spelled number is never mistaken for a fact)
PRESET_SAY = {
    "fl-gulf-fort-myers": ("a storm comes ashore from the Gulf near Fort Myers while the data center is running",
                           "una tormenta toca tierra desde el Golfo cerca de Fort Myers mientras el centro de datos está en marcha"),
    "fl-cat5-statewide": ("a category five hurricane crosses all of Florida", "un huracán de categoría cinco cruza toda Florida"),
    "fl-season-20": ("twenty category five storms strike Florida in a single season", "veinte tormentas de categoría cinco golpean Florida en una sola temporada"),
    "fl-heatdome-gulf": ("a heat dome and a Gulf storm strike at the same time", "una cúpula de calor y una tormenta del Golfo llegan al mismo tiempo"),
    # the same storms catastrophes.json names ("A Category 5 comes ashore on the Texas coast", "A nor'easter up
    # the Hudson Valley"): the spoken scenario must not contradict the title on screen
    "tx-gulf-landfall": ("a category five storm comes ashore on the Texas Gulf coast", "una tormenta de categoría cinco toca tierra en la costa del Golfo de Texas"),
    "ny-noreaster-hudson": ("a nor'easter tracks north up the Hudson Valley", "una tormenta del noreste sube por el valle del Hudson"),
}
PRESET_NAME_ES = {
    "fl-gulf-fort-myers": "Tormenta desde el Golfo cerca de Fort Myers, con el centro de datos",
    "fl-cat5-statewide": "Un huracán de categoría 5 cruza toda Florida",
    "fl-season-20": "Veinte tormentas de categoría 5 en una temporada",
    "fl-heatdome-gulf": "Cúpula de calor y tormenta en el Golfo",
    "tx-gulf-landfall": "Una tormenta de categoría 5 toca tierra en la costa de Texas",
    "ny-noreaster-hudson": "Una tormenta del noreste sube por el valle del Hudson",
}

VERDICT_CHIP = {
    "holds": ("Holds", "Funciona"), "partly": ("Partly", "En parte"), "fails": ("Fails", "No funciona"),
    "not_needed": ("Not needed", "No hace falta"), "not_checked": ("Not checked", "Sin comprobar"),
}
FAMILY_ORDER = ("shrink", "move", "flexible", "time_of_day", "upgrade", "onsite", "combo", "remove")


# ---------------------------------------------------------------------------------------------- engine
def _engine():
    """The engine track's module (briefing.py), or None while it is still a stub."""
    try:
        import briefing  # noqa: PLC0415 — resolved per call: the engine may land while this server runs
    except Exception:  # noqa: BLE001 — a broken engine must not take the writer down
        log.exception("briefing engine failed to import; using the local report")
        return None
    return briefing if callable(getattr(briefing, "report_for", None)) else None


class DeckIn(CaseIn):
    preset: str | None = Field(default=None, max_length=64)  # a catastrophes.json id (the engine expands it)
    budget_ms: int = 1800
    ai: bool = True  # false: templates only, instantly (the stage opens on this, then swaps in the AI deck)
    length: Literal["full", "short"] = "full"


def _nothing_happened(body: CaseIn) -> bool:
    return (
        body.lat is None and not body.sites and not body.trip and not getattr(body, "preset", None)
        and round(float(body.load_factor), 2) == 1.0
    )


def report_for_case(body: CaseIn) -> dict:
    """The engine's report for this case (cached there), or the local stand-in. Sync: run it in a thread."""
    if _nothing_happened(body):
        raise HTTPException(status_code=422, detail="Nothing has happened yet — add a data center, a storm or a heat level first")
    eng = _engine()
    model = getattr(eng, "BriefingIn", None) if eng else None
    if eng is not None and model is not None:
        data = body.model_dump()
        case = model(**{k: v for k, v in data.items() if k in model.model_fields})
        try:
            return eng.report_for(case)
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 — an engine bug on a plain case falls back; a preset has no fallback
            log.exception("briefing engine failed on this case")
            if getattr(body, "preset", None):
                raise HTTPException(status_code=503, detail="The briefing engine failed on this scenario — try another")
    if getattr(body, "preset", None):
        raise HTTPException(status_code=503, detail="Catastrophe presets need the briefing engine, which is not loaded yet")
    return _local_report(body)


# ------------------------------------------------------------------------------------ local stand-in
def _title_name(name: str) -> str:
    """'NORTH FORT MYERS 6' -> 'North Fort Myers 6' (the capitalization area_of uses)."""
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), str(name).strip().lower())


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def _group_areas(g, pairs) -> list[dict]:
    by: dict[str, dict] = {}
    for sid, mw in pairs:
        s = g.sub_index.get(int(sid))
        if s is None:
            continue
        a = area_of(g.sub_name[s])
        d = by.setdefault(a, {"area": a, "mw": 0.0, "sub_ids": [], "_pts": []})
        d["mw"] += float(mw)
        d["sub_ids"].append(int(sid))
        d["_pts"].append((float(g.sub_lon[s]), float(g.sub_lat[s])))
    out = sorted(by.values(), key=lambda d: -d["mw"])
    for d in out:
        xs, ys = [p[0] for p in d["_pts"]], [p[1] for p in d["_pts"]]
        d["center"] = [round(sum(xs) / len(xs), 4), round(sum(ys) / len(ys), 4)]
        d["bbox"] = [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]
        d["mw"] = round(d["mw"], 1)
        d["people"] = g.people(d["mw"]) if hasattr(g, "people") else int(round(d["mw"] * 700))
        del d["_pts"]
    return out


def _fact(key, label, value, unit="", estimate=False, source="engine") -> dict:
    shown = f"{value:,}" if isinstance(value, int) else str(value)
    return {"key": key, "label": label, "value": value, "unit": unit, "estimate": estimate, "source": source,
            "text": f"[{key}] {label}: {shown}{(' ' + unit) if unit else ''}{' (estimate)' if estimate else ''}"}


def _local_report(body: CaseIn) -> dict:
    """The engine's report shape, reduced to what a cascade tells: event, timeline, areas. No fixes
    (so no fix slide), verdict 'unverified'. Only used while briefing.report_for is missing."""
    g, sites, trip, upgrades = check_case(body)
    code = region_code(body.region)
    extra, header = _case_header(g, sites, trip, upgrades)
    c = g.cascade_case(extra, trip, upgrades)
    rate = g.rates_with(upgrades)
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[bid]] = False
    state = g.solve(active, extra, rate)
    timeline = []
    for st in c["steps"]:
        lines = []
        if st["n"] > 0:
            for bid in st["tripped"]:
                i = g.br_index[bid]
                a, b = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
                lines.append({
                    "id": int(bid), "from_sub": g.sub_name[a], "to_sub": g.sub_name[b],
                    "from_area": area_of(g.sub_name[a]), "to_area": area_of(g.sub_name[b]),
                    "kv": float(g.br_kv[i]), "transformer": a == b,
                    "pct_before": round(float(state.loading_pct[i]), 1), "flow_mw": round(abs(float(state.flow[i])), 1),
                })
                active[i] = False
            state = g.solve(active, extra, rate)
        timeline.append({
            "n": st["n"], "action": st["action"], "lines": lines, "storm_lines": len(trip) if st["n"] == 0 else 0,
            "held_line": st.get("held_line"), "why": [], "newly_dark": _group_areas(g, st.get("newly_affected") or []),
            "people_cum": int(st.get("people", 0)), "lost_mw_cum": st["lost_mw"],
        })
    areas = _group_areas(g, list(c["affected"].items()))
    first = {}
    for st in timeline:
        for d in st["newly_dark"]:
            first.setdefault(d["area"], st["n"])
    for d in areas:
        d["first_step"] = first.get(d["area"])
    people = int(c.get("people", 0))
    pop = getattr(g, "population", None) or 0
    lf = round(g.load_factor, 2)
    ev = {
        "steps": c["total_steps"], "capped": c["capped"], "outcome": c["outcome"], "lost_mw": c["lost_mw"],
        "people": people, "peak_people": max([people] + [s["people_cum"] for s in timeline]),
        "people_share_pct": round(people / pop * 100, 1) if pop else None,
        "campus_lost_power": bool(c.get("site_cut_off")), "storm_lines_out": len(trip),
    }
    kind = "storm" if trip else ("calm" if sites and not c["total_steps"] else ("cascade" if sites else "heat"))
    facts = [
        _fact("meta.region_name", "Region", REGIONS[code]["name"]),
        _fact("event.steps", "Cascade steps", ev["steps"]),
        _fact("event.people_out", "People without power at the end", people, "people", True),
        _fact("event.peak_people", "People without power at the worst moment", ev["peak_people"], "people", True),
        _fact("event.lost_mw", "Existing load lost", ev["lost_mw"], "MW"),
        _fact("event.campus_mw", "Data center size", round(float(header["mw"]), 1), "MW"),
        _fact("event.load_factor", "Load level", lf, "x"),
        _fact("event.storm_lines_out", "Lines knocked out by the storm", len(trip)),
    ]
    if ev["people_share_pct"] is not None:
        facts.append(_fact("event.people_share_pct", "Share of the state's residents", ev["people_share_pct"], "%", True))
    for st in timeline:
        for ln in st["lines"][:1]:
            facts.append(_fact(f"step.{st['n']}.pct_before", f"Step {st['n']} line loading before it tripped", ln["pct_before"], "%"))
        facts.append(_fact(f"step.{st['n']}.people_cum", f"People without power after step {st['n']}", st["people_cum"], "people", True))
    for d in areas[:12]:
        facts.append(_fact(f"area.{_slug(d['area'])}.people", f"People without power in {d['area']}", d["people"], "people", True))
    if header.get("headroom_mw") is not None and sites:
        facts.append(_fact("event.room_mw", "Room at the site before the first overload", header["headroom_mw"], "MW"))
    key = hashlib.sha1(json.dumps(body.model_dump(), sort_keys=True, default=str).encode()).hexdigest()
    return {
        "version": 1, "key": f"local-{key}", "engine": "local", "region": code, "region_name": REGIONS[code]["name"],
        "banner": f"SIMULATION · synthetic grid model of {REGIONS[code]['name']} (Breakthrough Energy / Texas A&M, CC-BY 4.0) · not any utility's network · every people and cost number is an estimate.",
        "kind": kind, "verdict": "nothing_happened" if (kind == "calm" and not people) else "unverified",
        "case": {**header, "load_factor": lf, "load_word": LOAD_WORD.get(lf, ("", ""))[0], "preset": None},
        "headline": None, "event": ev, "timeline": timeline, "root_cause": None, "areas": areas[:8],
        "hospitals": None, "cost": None, "fixes": [], "best_fix": None, "bound": None, "split": None,
        "no_fix": None, "recovery": None, "facts": facts, "unchecked": list(FAMILY_ORDER),
    }


# ------------------------------------------------------------------------------------ number words
_EN = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
       "sixteen seventeen eighteen nineteen").split()
_EN_T = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_ES = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis "
       "diecisiete dieciocho diecinueve veinte veintiuno veintidós veintitrés veinticuatro veinticinco "
       "veintiséis veintisiete veintiocho veintinueve").split()
_ES_T = "_ _ _ treinta cuarenta cincuenta sesenta setenta ochenta noventa".split()


def words(n: int, lang: str, fem: bool = False, before_noun: bool = False) -> str:
    """0..99 spelled out (a spelled count is never mistaken for a fact by the number check); above
    that, digits."""
    n = int(n)
    if not 0 <= n <= 99:
        return num(n, lang)
    if lang == "en":
        return _EN[n] if n < 20 else _EN_T[n // 10] + (f"-{_EN[n % 10]}" if n % 10 else "")
    w = _ES[n] if n < 30 else _ES_T[n // 10] + (f" y {_ES[n % 10]}" if n % 10 else "")
    if w.endswith("uno"):
        w = w[:-3] + ("una" if fem else ("ún" if before_noun and n > 1 else ("un" if before_noun else "uno")))
    return w


def num(v: float, lang: str = "en") -> str:
    """Digits: '1,500' in English; Spanish without a separator under 10,000 ('1500', RAE), else '12,500'."""
    v = int(round(float(v)))
    if lang == "es" and abs(v) < 10000:
        return str(v)
    return f"{v:,}"


def _dec(x: float, d: int) -> str:
    return f"{x:.{d}f}".rstrip("0").rstrip(".") if d else f"{x:.0f}"


def people_round(n: int, lang: str) -> str:
    """How a newspaper prints an estimate: 783,883 -> '784,000' / '784 mil'; 1,043,234 -> '1.04 million'."""
    n = int(n)
    if n >= 999_500:
        v = _dec(n / 1e6, 1 if n >= 9_950_000 else 2)
        return f"{v} million" if lang == "en" else f"{v} {'millón' if v == '1' else 'millones'}"
    if n >= 10_000:
        r = int(round(n, -3))
        return f"{r:,}" if lang == "en" else f"{r // 1000} mil"
    if n >= 1_000:
        return num(round(n, -2), lang)
    return num(n, lang)


def people_say(n: int, lang: str, noun: bool = True) -> str:
    """'about 784,000 people' / 'unas 784 mil personas' / 'unos 1.04 millones de personas'."""
    n = int(n)
    r = people_round(n, lang)
    if lang == "en":
        return f"about {r} people" if noun else f"about {r}"
    if n >= 999_500:
        return f"unos {r} de personas" if noun else f"unos {r}"
    return f"unas {r} personas" if noun else f"unas {r}"


def people_noun(n: int, lang: str) -> str:
    """'784,000 people' / '784 mil personas' / '2.1 millones de personas' (display, no 'about')."""
    r = people_round(n, lang)
    if lang == "en":
        return f"{r} people"
    return f"{r} de personas" if int(n) >= 999_500 else f"{r} personas"


def mw_say(mw: float, lang: str, adj: bool = False) -> str:
    v = float(mw)
    v = int(round(v)) if (v >= 10 or abs(v - round(v)) < 0.05) else round(v, 1)  # spoken: whole megawatts
    if lang == "en":
        return f"{v:,} megawatt" if adj else f"{v:,} megawatts"
    return (f"{_dec(v / 1000, 1)} mil" if v >= 10000 else str(v)) + " megavatios"


def mw_show(mw: float) -> str:
    v = float(mw)
    return f"{int(round(v)):,} MW" if abs(v - round(v)) < 0.05 else f"{v:,.1f} MW"


def pct_say(p: float, lang: str) -> str:
    return f"{int(round(float(p)))} " + ("percent" if lang == "en" else "por ciento")


def share_say(p: float | None, lang: str) -> str | None:
    """A share of the state's residents: '3.5 percent', 'less than one percent'."""
    if p is None:
        return None
    p = float(p)
    if p < 1:
        return "less than one percent" if lang == "en" else "menos del uno por ciento"
    return f"{_dec(p, 1) if p < 10 else int(round(p))} " + ("percent" if lang == "en" else "por ciento")


def usd_say(v: float, lang: str) -> tuple[str, float | None]:
    """('$1.2 billion' | '1.2 mil millones de dólares', the scaled value it prints, for the fact check)."""
    v = float(v)
    if v >= 1e9:
        if lang == "es":  # "2300 millones": a scale word must follow a figure ("mil millones" would not)
            x = round(v / 1e6)
            return f"{num(x, 'es')} millones de dólares", x
        x = round(v / 1e9, 1)
        return f"${_dec(x, 1)} billion", x
    if v >= 1e6:
        x = round(v / 1e6) if v >= 1e8 else round(v / 1e6, 1)
        return (f"${_dec(x, 1)} million" if lang == "en" else f"{_dec(x, 1)} millones de dólares"), x
    return (f"${num(round(v, -3), 'en')}" if lang == "en" else f"{num(round(v, -3), 'es')} dólares"), None


def usd_show(v: float) -> str:
    v = float(v)
    if v >= 1e9:
        return f"${_dec(v / 1e9, 1)}B"
    if v >= 1e6:
        return f"${_dec(v / 1e6, 1)}M"
    return f"${int(round(v, -3)):,}"


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def a_n(n: int) -> str:
    """The English article before a number read aloud: 'an 18-step cascade', 'a 9-step cascade'."""
    s = str(int(n))
    return "an" if s.startswith("8") or s in ("11", "18") or (len(s) == 5 and s[:2] in ("11", "18")) else "a"


def fix_article(text: str) -> str:
    """'a 18-step' -> 'an 18-step' in a headline the engine wrote."""
    return re.sub(r"\b([Aa]) (\d[\d,]*)(?=[- ])", lambda m: (("An" if m.group(1) == "A" else "an") if a_n(int(m.group(2).replace(",", ""))) == "an" else m.group(1)) + " " + m.group(2), text)


def speak_units(s: str, lang: str) -> str:
    """An engine string made speakable: 'MW' -> 'megawatts', '%' -> ' percent', '~' -> 'about '."""
    en = lang == "en"
    s = re.sub(r"~\s*", "about " if en else "unos ", s)
    s = re.sub(r"(\d)\s*%", r"\1 " + ("percent" if en else "por ciento"), s)
    for unit, e, sp in (("MW", "megawatts", "megavatios"), ("MVA", "megavolt-amperes", "megavoltamperios"),
                        ("kV", "kilovolts", "kilovoltios"), ("km", "kilometers", "kilómetros")):
        s = re.sub(rf"\b{unit}\b", e if en else sp, s)
    return re.sub(r"\s*[→·]\s*", ", ", s)


def join(items: list[str], lang: str) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (" and " if lang == "en" else " y ") + items[-1]


def cue(name: str, value) -> str:
    return f"{{cue:{name}={value}}}"


_CUE = re.compile(r"\{cue:(\w+)=([^}]*)\}")


def strip_cues(marked: str) -> tuple[str, list[dict]]:
    out, cues, pos, last = [], [], 0, 0
    for m in _CUE.finditer(marked):
        chunk = marked[last:m.start()]
        out.append(chunk)
        pos += len(chunk)
        v = m.group(2)
        cues.append({"char": pos, "name": m.group(1), "value": int(v) if v.lstrip("-").isdigit() else v})
        last = m.end()
    out.append(marked[last:])
    return "".join(out), cues


def spread(sentence: str, name: str, values: list) -> str:
    """Cue markers for `values` spread over the words of one sentence (the first at its start), so a
    grouped 'steps five to nine' still moves the map one step at a time while it is spoken."""
    if not values:
        return sentence
    words_ = sentence.split(" ")
    at = [round(i * (len(words_) - 1) / len(values)) for i in range(len(values))]
    return " ".join("".join(cue(name, v) for v, j in zip(values, at) if j == wi) + w for wi, w in enumerate(words_))


def plain_len(marked: str) -> int:
    return len(_CUE.sub("", marked))


# ------------------------------------------------------------------------------------------ writer
@dataclass(frozen=True)
class Level:
    """How much a deck says. Trimmed in the contract's order when it runs over its character budget:
    grouped steps first, then the third area, then the hospital clause; never the SIMULATION opening or
    closing, and never the fix verdict."""

    k: int = 5  # chain steps read one by one (the rest are grouped)
    areas: int = 4  # areas read by name on the areas slide
    hosp: bool = True  # the per-area hospital clause
    opt: int = 2  # optional sentences kept: 2 all, 1 the useful ones, 0 none
    checks: int = 4  # other fixes read out after the best one
    short: bool = False  # the demo version: one idea per slide


LEVELS = (
    Level(5, 4, True, 2, 4), Level(4, 4, True, 2, 4), Level(3, 4, True, 2, 4), Level(3, 3, True, 2, 4),
    Level(2, 3, True, 2, 4), Level(2, 3, False, 2, 4), Level(2, 2, False, 2, 3), Level(2, 2, False, 1, 3),
    Level(2, 2, False, 1, 2), Level(2, 2, False, 1, 1), Level(2, 1, False, 1, 1), Level(1, 1, False, 1, 1),
    Level(2, 2, False, 0, 2), Level(2, 2, False, 0, 1), Level(2, 1, False, 0, 0),
    Level(1, 1, False, 0, 0), Level(0, 1, False, 0, 0),
)
SHORT_LEVELS = tuple(Level(lv.k, min(lv.areas, 2), False, 0, 0, True) for lv in LEVELS[4:])


def sentences(parts: list[tuple[str, bool | int]], lv: Level, limit: int) -> str:
    """Join (sentence, optional) parts: required ones (False) always; optional ones (True = useful,
    2 = nice to have) while the level keeps them and they fit."""
    out: list[str] = []
    for text, optional in parts:
        if not text:
            continue
        if optional and (lv.opt < int(optional) or plain_len(" ".join(out + [text])) > limit):
            continue
        out.append(text)
    return " ".join(out)


class Writer:
    """Everything the slide templates need from one report, resolved once."""

    def __init__(self, report: dict):
        self.r = report
        self.code = report.get("region") or "FL"
        self.g = grid_at(1.0, self.code)  # base model: coordinates and names only
        self.region_name = report.get("region_name") or REGIONS.get(self.code, {}).get("name", self.code)
        self.ev = report.get("event") or {}
        self.case = report.get("case") or {}
        self.kind = report.get("kind") or "cascade"
        self.verdict = report.get("verdict") or "unverified"
        self.facts = {f["key"]: f for f in (report.get("facts") or []) if isinstance(f, dict) and "key" in f}
        self.extra: list[dict] = []  # numbers the templates print that the fact sheet lacks (derived from the report)
        self.region_es = STATE_ES.get(self.region_name, self.region_name)
        self.names: set[str] = {self.region_name, self.region_es}
        self.steps = int(self.ev.get("steps") or 0)
        self.people = int(self.ev.get("people") or 0)
        self.storm = int(self.ev.get("storm_lines_out") or 0)
        self.sites = self.case.get("sites") or []
        self.mw = float(self.case.get("mw") or 0) if self.sites else 0.0
        self.place = self.case.get("sub_area") if self.sites else None
        self.multi = len(self.sites) > 1  # the AI-boom case: several campuses at once
        self.site_places: list[str] = []
        for s in self.sites:
            a = s.get("sub_area") if isinstance(s, dict) else None
            if a and a not in self.site_places:
                self.site_places.append(a)
                self.names.add(a)
        preset = self.case.get("preset") or None
        self.preset = preset if isinstance(preset, dict) and preset.get("id") else None
        self.lead = "event"  # the id of the deck's first slide (compose sets it): it carries the SIMULATION opening
        self.lf = round(float(self.case.get("load_factor") or 1.0), 2)
        self.many_storms = bool(self.preset) and self.preset.get("id") in ("fl-season-20",)
        self.areas = [a for a in (report.get("areas") or []) if a.get("people", 0) > 0]
        for a in self.areas:
            self.names.add(a["area"])
        if self.place:
            self.names.add(self.place)
        if self.case.get("sub_name"):
            self.names.add(_title_name(self.case["sub_name"]))
        self.timeline = report.get("timeline") or []
        fixes = report.get("fixes") or []
        bi = report.get("best_fix")
        self.best = fixes[bi] if isinstance(bi, int) and 0 <= bi < len(fixes) else None
        self.fixes = fixes
        room = self.case.get("headroom_mw")
        self.room = float(room) if room is not None and self.sites else None
        if self.room is not None and "event.room_mw" not in self.facts:
            self.add("deck.room_mw", "Room at the site before the first overload", round(self.room, 1), "MW")
        if self.lf != 1.0:
            self.add("deck.load_pct", "Load level as a percent of the model's snapshot", int(round(self.lf * 100)), "%")

    # -- facts
    def add(self, key: str, label: str, value, unit: str = "", estimate: bool = False) -> None:
        if key not in self.facts and all(f["key"] != key for f in self.extra):
            self.extra.append(_fact(key, label, value, unit, estimate, source="engine (derived by the writer)"))

    def keys(self, *cands: str) -> list[str]:
        return [k for k in cands if k in self.facts or any(f["key"] == k for f in self.extra)]

    def area_key(self, area: str) -> list[str]:
        k = f"area.{_slug(area)}.people"
        if k in self.facts:
            return [k]
        alt = [key for key, f in self.facts.items() if key.startswith("area.") and key.endswith(".people") and area.lower() in (f.get("label", "") + f.get("text", "")).lower()]
        return alt[:1]

    # -- names and places
    def line_label(self, bid, lang: str, fallback: str | None = None) -> str:
        i = self.g.br_index.get(int(bid)) if bid is not None else None
        if i is None:
            return fallback or ("a line" if lang == "en" else "una línea")
        a, b = int(self.g.bus_sub_idx[self.g.f[i]]), int(self.g.bus_sub_idx[self.g.t[i]])
        na, nb = _title_name(self.g.sub_name[a]), _title_name(self.g.sub_name[b])
        self.names.update((na, nb))
        if a == b:
            return f"the {na} transformer" if lang == "en" else f"el transformador de {na}"
        return f"the {na} to {nb} line" if lang == "en" else f"la línea de {na} a {nb}"

    def line_pts(self, bid) -> list[list[float]]:
        i = self.g.br_index.get(int(bid)) if bid is not None else None
        if i is None:
            return []
        out = []
        for bus in (self.g.f[i], self.g.t[i]):
            s = int(self.g.bus_sub_idx[bus])
            out.append([round(float(self.g.sub_lon[s]), 4), round(float(self.g.sub_lat[s]), 4)])
        return out

    def area_near(self, lat: float, lon: float) -> str:
        a = area_of(self.g.sub_name[self.g.nearest_sub(lat, lon)])
        self.names.add(a)
        return a

    def when(self, lang: str) -> str:
        w = LOAD_WHEN.get(self.lf)
        if w:
            return w[0] if lang == "en" else w[1]
        p = int(round(self.lf * 100))
        return (f"With demand at {p} percent of a normal summer afternoon" if lang == "en"
                else f"Con la demanda al {p} por ciento de una tarde de verano normal")

    def load_show(self, lang: str) -> str:
        w = LOAD_WORD.get(self.lf)
        if w:
            return w[0] if lang == "en" else w[1]
        return f"{int(round(self.lf * 100))}% of summer peak" if lang == "en" else f"{int(round(self.lf * 100))}% del pico de verano"

    def preset_say(self, lang: str) -> str:
        pid = self.preset["id"]
        if pid in PRESET_SAY:
            return PRESET_SAY[pid][0 if lang == "en" else 1]
        name = re.sub(r"\d+", lambda m: words(int(m.group(0)), "en"), str(self.preset.get("name") or "a storm"))
        return name[:1].lower() + name[1:] if lang == "en" else "un escenario hipotético de tormenta"

    def preset_name(self, lang: str) -> str:
        pid = self.preset["id"]
        return str(self.preset.get("name") or pid) if lang == "en" else PRESET_NAME_ES.get(pid, "Escenario hipotético")

    def dc(self, lang: str, the: bool = True) -> str:
        """'the data center' / 'the data centers' (several campuses) / 'el centro de datos' / 'los centros de datos'."""
        if lang == "en":
            return ("the " if the else "") + ("data centers" if self.multi else "data center")
        return ("los " if the else "") + "centros de datos" if self.multi else ("el " if the else "") + "centro de datos"

    def where(self, lang: str) -> str:
        """The campus towns, joined: 'Fort Myers, Orlando and Jacksonville'."""
        return join(self.site_places[:4], lang) if self.site_places else (self.place or "")

    def site_pt(self) -> list[float] | None:
        if self.case.get("sub_lon") is None or not self.sites:
            return None
        return [round(float(self.case["sub_lon"]), 4), round(float(self.case["sub_lat"]), 4)]

    def top_area(self) -> str | None:
        return self.areas[0]["area"] if self.areas else None

    def mask(self, text: str) -> str:
        """Names hold digits ('Fort Myers 12') that are not quantities: hide them from the number check."""
        for n in sorted(self.names, key=len, reverse=True):
            if n:
                text = re.sub(re.escape(n), "Placename", text, flags=re.IGNORECASE)
        return text


# ------------------------------------------------------------------------------------------ cameras
def cam(type_: str, points: list, center=None, sub_ids=None, line_ids=None) -> dict:
    pts = [p for p in points if p][:80]
    if center is None and pts:
        center = [round(sum(p[0] for p in pts) / len(pts), 4), round(sum(p[1] for p in pts) / len(pts), 4)]
    return {"type": type_ if pts or type_ == "none" else "none", "points": pts, "center": center,
            "sub_ids": list(sub_ids or [])[:200], "line_ids": list(line_ids or [])[:200]}


def region_cam(w: Writer) -> dict:
    r = REGIONS.get(w.code, {})
    x0, y0, x1, y1 = r.get("bbox") or [0, 0, 0, 0]
    return cam("region", [[x0, y0], [x1, y1]], center=r.get("center"))


def mapspec(mode: str, step_from=None, step_to=None, highlight=None, apply=None, wave=None) -> dict:
    return {"mode": mode, "step_from": step_from, "step_to": step_to, "highlight_lines": list(highlight or []),
            "apply": apply, "wave": wave}


# ------------------------------------------------------------------------------------------- slides
# Each builder returns the slide's language-independent parts plus, per language, the headline, the
# lines and the narration as (role, prefix, body, suffix) with cue markers. `body` is the part Gemini
# may rewrite (presenter only); prefix/suffix are fixed.
def _seg(role: str, body: str, prefix: str = "", suffix: str = "") -> dict:
    return {"role": role, "prefix": prefix, "body": body, "suffix": suffix}


def hours_say(h: float, lang: str) -> str:
    """The outage length as it is spoken: 'about a day', 'about eight hours', 'about three days' (numbers in words)."""
    en = lang == "en"
    if h < 1.5:
        return "about an hour" if en else "aproximadamente una hora"
    if h < 22:
        n = int(round(h))
        return f"about {words(n, lang)} hours" if en else f"unas {words(n, lang, fem=True)} horas"
    if h < 36:
        return "about a day" if en else "aproximadamente un día"
    d = int(round(h / 24))
    return f"about {words(d, lang)} days" if en else f"unos {words(d, lang)} días"


def hours_show(h: float, lang: str) -> str:
    """The outage length as a figure for the big number: '1 day', '8 hours', '3 days'."""
    en = lang == "en"
    if h < 1.5:
        return "1 hour" if en else "1 hora"
    if h < 22:
        n = int(round(h))
        return f"{n} hours" if en else f"{n} horas"
    if h < 36:
        return "1 day" if en else "1 día"
    d = int(round(h / 24))
    return f"{d} days" if en else f"{d} días"


def s_toll(w: Writer, lv: Level) -> dict:
    """The lead: what it is expected to cost and how long people are without power. The cost is the high end
    of the range (we guess on the higher side); the outage length is estimated from the incident's size."""
    c = w.r.get("cost") or {}
    high = float(c.get("blackout_high_usd") or c.get("blackout_usd") or 0)
    low = float((c.get("ranges") or {}).get("blackout_usd", [0, 0])[0] or 0) if c.get("ranges", {}).get("blackout_usd") else high
    hours = float(c.get("duration_h_assumed") or 0)
    out = {"kind": "toll", "headline": {}, "lines": {}, "narr": {}}
    for lang in LANGS:
        _, scaled = usd_say(high, lang)
        if scaled is not None:
            w.add("deck.toll.cost_high.scaled", "Expected blackout cost, high end, scaled", scaled, "USD (scaled)", True)
    if hours:
        w.add("deck.toll.outage_hours", "Estimated hours without power (from the incident's size)", round(hours, 1), "hours", True)
    for lang in LANGS:
        en = lang == "en"
        rn = w.region_name if en else w.region_es
        usd, _ = usd_say(high, lang)
        parts: list[tuple[str, bool | int]] = [
            (f"The expected cost is about {usd}." if en else f"El costo esperado es de unos {usd}.", False),
            (f"People would be without power for {hours_say(hours, lang)}." if en else f"La gente estaría sin luz {hours_say(hours, lang)}.", False),
            ((f"That is {people_say(w.people, lang)} without power (estimate)." if en
              else f"Son {people_say(w.people, lang)} sin luz (estimación)."), 1),
        ]
        out["narr"][lang] = [_seg("presenter", sentences(parts, lv, PRESENTER_MAX[lang] - len(OPEN[lang].format(region=rn)) - 1),
                                  prefix=OPEN[lang].format(region=rn))]
        lab = c.get("outage_label", {}).get(lang) or hours_say(hours, lang)
        out["headline"][lang] = (f"Expected cost {usd_show(high)}, {lab} without power" if en
                                 else f"Costo esperado {usd_show(high)}, {lab} sin luz")
        lines = []
        if low and low < high:
            lines.append(f"Range: {usd_show(low)} to {usd_show(high)} (estimate)" if en else f"Rango: de {usd_show(low)} a {usd_show(high)} (estimación)")
        if w.people:
            lines.append(f"People without power: {w.people:,} (estimate)" if en else f"Personas sin luz: {w.people:,} (estimación)")
        lines.append("Outage time is estimated from the incident's size, not forecast" if en
                     else "El tiempo sin luz se estima según el tamaño del incidente; no es un pronóstico")
        out["lines"][lang] = [ln[:LINE_MAX] for ln in lines[:3]]
    out["big"] = {"value": high, "display": {"en": usd_show(high), "es": usd_show(high)},
                  "label": {"en": "expected cost, high end of typical estimates", "es": "costo esperado, extremo alto de las estimaciones típicas"},
                  "fact_key": "deck.toll.cost_high.scaled", "tone": "alert"}
    out["big2"] = {"value": hours, "display": {"en": hours_show(hours, "en"), "es": hours_show(hours, "es")},
                   "label": {"en": "average time without power (estimate)", "es": "tiempo medio sin luz (estimación)"},
                   "fact_key": "deck.toll.outage_hours", "tone": "alert"}
    out["camera"] = region_cam(w) if not w.areas else cam("areas", [a.get("center") for a in w.areas[:4]])
    out["map"] = mapspec("final", w.steps, w.steps)
    out["facts_used"] = w.keys("event.people_out", "deck.toll.cost_high.scaled", "deck.toll.outage_hours")
    return out


def s_event(w: Writer, lv: Level) -> dict:
    out = {"kind": "event", "headline": {}, "lines": {}, "narr": {}}
    pct_lf = int(round(w.lf * 100))
    for lang in LANGS:
        en = lang == "en"
        when = w.when(lang)
        who = people_say(w.people, lang)
        parts: list[tuple[str, bool]] = []
        if w.preset:
            parts.append(((f"Hypothetical scenario: {w.preset_say(lang)}." if en else f"Escenario hipotético: {w.preset_say(lang)}."), False))
            if w.storm:
                many = w.many_storms
                parts.append(((f"The {'storms knock' if many else 'storm knocks'} out {num(w.storm)} lines." if en
                               else f"{'Las tormentas derriban' if many else 'La tormenta derriba'} {num(w.storm, lang)} líneas."), False))
        elif w.storm:
            where = w.top_area()
            s = (f"A hypothetical storm knocks out {num(w.storm)} lines" + (f" around {where}" if where else "")
                 if en else f"Una tormenta hipotética derriba {num(w.storm, lang)} líneas" + (f" cerca de {where}" if where else ""))
            if w.multi:
                s += (f", while {words(len(w.sites), 'en')} new data centers draw {mw_say(w.mw, lang)} in all" if en
                      else f", mientras {words(len(w.sites), 'es', before_noun=True)} nuevos centros de datos consumen {mw_say(w.mw, lang)} en total")
            elif w.sites:
                s += (f", while a new {mw_say(w.mw, lang, adj=True)} data center draws power at {w.place}" if en
                      else f", mientras un nuevo centro de datos de {mw_say(w.mw, lang)} consume energía en {w.place}")
            parts.append((s + ".", False))
        elif w.multi:
            n_ = len(w.sites)
            parts.append(((f"{when}, {words(n_, 'en')} new data centers, {mw_say(w.mw, lang)} in all, connect to the grid at {w.where(lang)}." if en
                           else f"{when}, {words(n_, 'es', before_noun=True)} nuevos centros de datos, con {mw_say(w.mw, lang)} en total, se conectan a la red en {w.where(lang)}."), False))
        elif w.sites:
            parts.append(((f"{when}, a new {mw_say(w.mw, lang, adj=True)} data center connects to the grid at {w.place}." if en
                           else f"{when}, un nuevo centro de datos de {mw_say(w.mw, lang)} se conecta a la red en {w.place}."), False))
            if w.room is not None and w.steps and w.room < w.mw:
                parts.append(((f"The grid there can take about {mw_say(w.room, lang)} before a line overloads." if en
                               else f"La red allí admite unos {mw_say(w.room, lang)} antes de que una línea se sobrecargue."), True))
        else:
            if pct_lf > 100:
                parts.append(((f"{when}, demand rises to {pct_lf} percent of a normal summer afternoon." if en
                               else f"{when}, la demanda sube al {pct_lf} por ciento de una tarde de verano normal."), False))
            else:  # overnight or morning: demand is below the summer afternoon, it does not "rise"
                parts.append(((f"{when}, demand is at {pct_lf} percent of a normal summer afternoon." if en
                               else f"{when}, la demanda está al {pct_lf} por ciento de una tarde de verano normal."), False))
        # what it did
        if w.steps:
            steps_w = words(w.steps, lang, before_noun=True)
            if w.storm:  # the chain slide reads the steps: the short deck drops this sentence first
                s = (f"Then overloaded lines trip in {steps_w} more {'step' if w.steps == 1 else 'steps'}." if en
                     else f"Después, las líneas sobrecargadas se disparan en {steps_w} {'paso' if w.steps == 1 else 'pasos'} más.")
                parts.append((s, True))
            else:
                s = (f"Over {steps_w} {'step' if w.steps == 1 else 'steps'}, overloaded lines trip one after another" if en
                     else f"En {steps_w} {'paso' if w.steps == 1 else 'pasos'}, las líneas sobrecargadas se disparan una tras otra")
                s += ((f", and {who} lose power." if en else f", y {who} se quedan sin luz.") if w.people
                      else (", but nobody loses power." if en else ", pero nadie se queda sin luz."))
                parts.append((s, False))
        elif not w.storm:
            parts.append((("Every line stays within its limit: nothing trips, and nobody loses power." if en
                           else "Todas las líneas se mantienen dentro de su límite: nada se dispara y nadie se queda sin luz."), False))
        elif not w.people:
            parts.append((("The rest of the grid carries the load: nothing else trips, and nobody loses power." if en
                           else "El resto de la red lleva la carga: nada más se dispara y nadie se queda sin luz."), False))
        if w.people and (w.storm or not w.steps):
            s = f"{cap(who)} lose power" if en else f"{cap(who)} se quedan sin luz"
            share = share_say(w.ev.get("people_share_pct"), lang) if (w.preset or w.people > 1_000_000) else None
            if share:
                s += f", {share} of the state's residents" if en else f", el {share} de los habitantes del estado"
            parts.append((s + ".", False))
        if w.ev.get("capped"):
            parts.append((("It is still spreading when the model stops." if en else "Sigue propagándose cuando el modelo se detiene."), True))
        rn = w.region_name if en else w.region_es
        if w.lead == "event":
            body = sentences(parts, lv, PRESENTER_MAX[lang] - len(OPEN[lang].format(region=rn)) - 1)
            out["narr"][lang] = [_seg("presenter", body, prefix=OPEN[lang].format(region=rn))]
        else:  # the toll slide opened the deck (and said it is a simulation)
            out["narr"][lang] = [_seg("presenter", sentences(parts, lv, PRESENTER_MAX[lang]))]

        # headline + lines (visual)
        pr = people_round(w.people, lang)
        if w.preset:
            h = f"{w.preset_name(lang)}: {people_noun(w.people, lang)} " + ("without power (estimate)" if en else "sin luz (estimación)")
        elif w.storm and not w.people:
            h = (f"A storm knocked out {w.storm:,} {'line' if w.storm == 1 else 'lines'}; nobody lost power" if en
                 else f"Una tormenta derribó {w.storm:,} {'línea' if w.storm == 1 else 'líneas'}; nadie se quedó sin luz")
        elif w.storm:
            h = (f"A storm knocked out {w.storm:,} lines; {people_noun(w.people, lang)} lost power (estimate)" if en
                 else f"Una tormenta derribó {w.storm:,} líneas; {people_noun(w.people, lang)} sin luz (estimación)")
        elif w.sites and w.steps:
            if w.multi:
                h = (f"{len(w.sites)} data centers ({w.mw:,.0f} MW in all) set off {a_n(w.steps)} {w.steps}-step cascade" if en
                     else f"{len(w.sites)} centros de datos ({w.mw:,.0f} MW en total) desataron una cascada de {w.steps} pasos")
            else:
                h = (f"A {w.mw:,.0f} MW data center at {w.place} set off {a_n(w.steps)} {w.steps}-step cascade" if en
                     else f"Un centro de datos de {w.mw:,.0f} MW en {w.place} desató una cascada de {w.steps} pasos")
            if w.people:
                h += (f"; about {people_noun(w.people, lang)} lost power (estimate)." if en
                      else f"; {'unos' if w.people >= 999_500 else 'unas'} {people_noun(w.people, lang)} sin luz (estimación).")
            else:
                h += "."
            eng_h = (w.r.get("headline") or {}).get("text") if isinstance(w.r.get("headline"), dict) else None
            h = fix_article(eng_h) if (en and eng_h) else h
        elif w.sites:
            h = ((f"{len(w.sites)} data centers ({w.mw:,.0f} MW in all): every line holds." if en
                  else f"{len(w.sites)} centros de datos ({w.mw:,.0f} MW en total): todas las líneas aguantan.") if w.multi else
                 (f"A {w.mw:,.0f} MW data center at {w.place}: every line holds." if en
                  else f"Un centro de datos de {w.mw:,.0f} MW en {w.place}: todas las líneas aguantan."))
        elif w.steps:
            h = (f"Demand at {pct_lf}% set off {a_n(w.steps)} {w.steps}-step cascade" if en
                 else f"La demanda al {pct_lf}% desató una cascada de {w.steps} pasos")
            if w.people:
                h += (f"; about {people_noun(w.people, lang)} lost power (estimate)" if en
                      else f"; {'unos' if w.people >= 999_500 else 'unas'} {people_noun(w.people, lang)} sin luz (estimación)")
        else:
            h = f"Demand at {pct_lf}%: every line holds" if en else f"La demanda al {pct_lf}%: todas las líneas aguantan"
        out["headline"][lang] = h
        lines = []
        if w.multi:
            lines.append((f"Where: {w.where(lang)}" if en else f"Dónde: {w.where(lang)}")[:LINE_MAX])
            if w.storm:
                lines.append(f"Storm: {w.storm:,} lines knocked out" if en else f"Tormenta: {w.storm:,} líneas derribadas")
            lines.append((f"Size: {mw_show(w.mw)} across {len(w.sites)} sites" if en else f"Tamaño: {mw_show(w.mw)} en {len(w.sites)} sitios"))
            lines.append((f"When: {w.load_show(lang)}" if en else f"Cuándo: {w.load_show(lang)}"))
        else:
            if w.sites:
                sub = _title_name(w.case.get("sub_name") or w.place or "")
                kv = w.case.get("kv")
                if w.storm:
                    lines.append((f"Data center: {sub} substation · {mw_show(w.mw)}" if en else f"Centro de datos: subestación {sub} · {mw_show(w.mw)}"))
                else:
                    lines.append((f"Where: {sub} substation" if en else f"Dónde: subestación {sub}") + (f" ({kv:.0f} kV)" if kv else ""))
            if w.storm:
                lines.append(f"Storm: {w.storm:,} lines knocked out" if en else f"Tormenta: {w.storm:,} líneas derribadas")
            if w.preset or w.storm:  # a storm has its own season: this line is the demand the model runs at
                std = w.lf in LOAD_WORD
                lines.append((f"Demand: {w.load_show(lang)}{' level' if std else ''}" if en
                              else f"Demanda: {'nivel de ' if std else ''}{w.load_show(lang)}"))
            else:
                lines.append((f"When: {w.load_show(lang)}" if en else f"Cuándo: {w.load_show(lang)}"))
        if w.sites and not w.multi and len(lines) < 3:
            size = mw_show(w.mw) + (f" · room there: {mw_show(w.room)}" if en else f" · margen allí: {mw_show(w.room)}") if w.room is not None else mw_show(w.mw)
            lines.append((f"Size: {size}" if en else f"Tamaño: {size}"))
        out["lines"][lang] = lines[:3]

    tone = "alert" if w.people else "good"
    out["big"] = {"value": w.people, "display": {"en": f"{w.people:,}", "es": f"{w.people:,}"},
                  "label": {"en": "people without power (estimate)", "es": "personas sin luz (estimación)"},
                  "fact_key": "event.people_out", "tone": tone}
    site = w.site_pt()
    if site:
        out["camera"] = cam("site", [site], center=site, sub_ids=[w.case.get("sub")] if w.case.get("sub") else [])
    elif w.areas:
        out["camera"] = cam("areas", [a.get("center") for a in w.areas[:4]])
    else:
        out["camera"] = region_cam(w)
    out["map"] = mapspec("calm", 0, 0)
    out["facts_used"] = w.keys("event.people_out", "event.steps", "event.campus_mw", "event.load_factor",
                               "event.storm_lines_out", "event.people_share_pct", "event.room_mw", "deck.room_mw", "deck.load_pct")
    return out


def _step_sentence(w: Writer, st: dict, lang: str, seen: set | None = None) -> str:
    """One step, read aloud. `seen`: areas already said to lose power in this chain (never said twice)."""
    en = lang == "en"
    n = int(st["n"])
    seen = set() if seen is None else seen
    dark = [d["area"] for d in (st.get("newly_dark") or []) if d.get("people", 0) >= 1000 and d["area"] not in seen][:1]
    seen.update(dark)
    tail = ""
    if dark:
        tail = f", and {dark[0]} loses power" if en else f", y {dark[0]} se queda sin luz"
    if n == 0:
        k = st.get("storm_lines") or w.storm  # the engine: {count, listed}; a list or a count also works
        k = k.get("count") or w.storm if isinstance(k, dict) else (len(k) if isinstance(k, list) else int(k or 0))
        if w.many_storms:
            return cue("step", 0) + (f"First, the storms knock out {num(k)} lines{tail}." if en
                                     else f"Primero, las tormentas derriban {num(k, lang)} líneas{tail}.")
        return cue("step", 0) + (f"First, the storm knocks out {num(k)} lines{tail}." if en
                                 else f"Primero, la tormenta derriba {num(k, lang)} líneas{tail}.")
    head = f"Play {words(n, 'en')}: " if en else f"Jugada {words(n, 'es')}: "
    if st.get("action") == "shed":
        held = w.line_label(st.get("held_line"), lang)
        area = dark[0] if dark else None
        s = (f"to hold {held}, operators cut power to customers" + (f" near {area}" if area else "") if en
             else f"para sostener {held}, los operadores cortan la luz a clientes" + (f" cerca de {area}" if area else ""))
        return cue("step", n) + head + s + "."
    lines = st.get("lines") or []
    if not lines:
        return cue("step", n) + head + ("a line trips." if en else "se dispara una línea.")
    ln = lines[0]
    label = w.line_label(ln.get("id"), lang, fallback=ln.get("label"))
    more = len(lines) - 1
    if more:
        label += (f" and {words(more, 'en')} more {'line' if more == 1 else 'lines'}" if en
                  else f" y {words(more, 'es', fem=True)} {'línea' if more == 1 else 'líneas'} más")
    pct = ln.get("pct_before")
    at = ""
    if pct is not None:
        at = f" at {pct_say(pct, lang)}" if en else f" al {pct_say(pct, lang)}"
    verb = ("trip" if more else "trips") if en else ("se disparan" if more else "se dispara")
    return cue("step", n) + head + f"{label} {verb}{at}{tail}."


def _group_sentence(w: Writer, group: list[dict], lang: str, after: bool = True, seen: set | None = None) -> str:
    """'Steps five to nine: five more lines trip...' (`after`: steps were read one by one before it)."""
    en = lang == "en"
    a, b = int(group[0]["n"]), int(group[-1]["n"])
    trips = sum(max(len(s.get("lines") or []), 1) for s in group if s.get("action") != "shed")
    seen = set() if seen is None else seen
    dark: list[str] = []
    for s in group:
        for d in s.get("newly_dark") or []:
            if d.get("people", 0) >= 1000 and d["area"] not in dark and d["area"] not in seen:
                dark.append(d["area"])
    dark = dark[:2]
    seen.update(dark)
    if en:
        s = f"Steps {words(a, 'en')} to {words(b, 'en')}: {words(trips, 'en')}{' more' if after else ''} {'line trips' if trips == 1 else 'lines trip'}"
        s += f", and {join(dark, 'en')} {'loses' if len(dark) == 1 else 'lose'} power." if dark else "."
    else:
        s = f"Pasos {words(a, 'es')} a {words(b, 'es')}: se {'dispara' if trips == 1 else 'disparan'} {words(trips, 'es', fem=True)} {'línea' if trips == 1 else 'líneas'}{' más' if after else ''}"
        s += f", y {join(dark, 'es')} {'se queda' if len(dark) == 1 else 'se quedan'} sin luz." if dark else "."
    return spread(s, "step", list(range(a, b + 1)))


def _plays(w: Writer) -> list[dict]:
    """The cascade as plays for the play-by-play: one per failure, with what it is, how hard it was pushed, and who it hit."""
    steps = (w.r.get("replay") or {}).get("steps") or []
    rows = {int(r.get("n", 0)): r for r in w.timeline if isinstance(r, dict)}
    hosp = {a["area"]: int(a["count"]) for a in ((w.r.get("hospitals") or {}).get("areas") or [])}
    plays, prev = [], 0
    for st in steps:
        n = int(st.get("n") or 0)
        if n <= 0:
            continue
        tr = [int(b) for b in (st.get("tripped") or [])]
        bid = tr[0] if tr else st.get("held_line")
        if bid is None:
            continue
        i = w.g.br_index.get(int(bid))
        kind = "transformer" if i is not None and int(w.g.bus_sub_idx[w.g.f[i]]) == int(w.g.bus_sub_idx[w.g.t[i]]) else "line"
        ln = ((rows.get(n) or {}).get("lines") or [{}])[0]
        hit = int(st.get("people_hit") or 0)
        areas = [h["area"] for h in (st.get("hits") or []) if h.get("area")][:3]
        plays.append({
            "n": n, "action": st.get("action"), "kind": kind, "id": int(bid),
            "label": {"en": w.line_label(bid, "en"), "es": w.line_label(bid, "es")},
            "loading_pct": ln.get("pct_before"), "people_hit": hit, "people_delta": max(hit - prev, 0), "people_total": hit,
            "areas": areas, "hospitals": sum(hosp.get(a, 0) for a in areas), "dark": len(st.get("dark_subs") or []),
        })
        prev = hit
    return plays[:30]


def s_chain(w: Writer, lv: Level) -> dict:
    out = {"kind": "chain", "headline": {}, "lines": {}, "narr": {}}
    tl = [s for s in w.timeline if isinstance(s, dict)]
    run = [s for s in tl if int(s.get("n", 0)) > 0]
    trips = sum(len(s.get("lines") or []) for s in run if s.get("action") != "shed")
    islanded = (w.ev.get("outcome") == "islanded")
    # the mechanism, from the engine's "why" of the first trip: the line that picked up its power
    first = run[0] if run else None
    why = None
    if first and first.get("action") != "shed" and first.get("lines"):
        a_en = w.line_label(first["lines"][0].get("id"), "en", fallback=first["lines"][0].get("label"))
        cands = [x for x in (first.get("why") or []) if isinstance(x, dict) and x.get("pct_after") is not None
                 and x.get("id") != first["lines"][0].get("id") and w.line_label(x.get("id"), "en", fallback=x.get("label")) != a_en]
        nxt = (run[1].get("lines") or [{}])[0].get("id") if len(run) > 1 else None
        why = next((x for x in cands if x.get("id") == nxt), None)  # the line that trips next, if it picked up the flow
        if why is None:  # else the one pushed hardest, if it came close to its limit
            why = max((x for x in cands if x["pct_after"] >= 90), key=lambda x: x["pct_after"], default=None)
        if why is not None:
            w.add(f"deck.why.{first['n']}.pct_after", f"Step {first['n']}: loading of the line that picked up its power", float(why["pct_after"]), "%")
    for lang in LANGS:
        en = lang == "en"
        if w.storm:
            parts = [(("Here is how the damage spread." if en else "Así se propagó el daño."), False),
                     (("The storm cuts lines first, and the power they carried shifts onto the lines still standing." if en
                       else "La tormenta corta líneas primero, y la energía que llevaban pasa a las que siguen en pie."), True)]
        else:
            parts = [(("Here is how the failure spread." if en else "Así se propagó la falla."), False)]
            if why is not None:
                a = w.line_label(first["lines"][0].get("id"), lang, fallback=first["lines"][0].get("label"))
                b = w.line_label(why.get("id"), lang, fallback=why.get("label"))
                parts.append(((f"When {a} trips, its power shifts onto {b}, which climbs to {pct_say(why['pct_after'], lang)}." if en
                               else f"Cuando {a} se dispara, su energía pasa a {b}, que sube al {pct_say(why['pct_after'], lang)}."), True))
            if why is not None:
                parts.append((("Each trip does the same to the next line, until the grid gives way." if en
                                else "Cada disparo hace lo mismo con la siguiente línea, hasta que la red cede."), 2))
            else:
                parts.append((("Each line that trips hands its power to its neighbors, and the next one goes over its limit." if en
                                else "Cada línea que se dispara pasa su energía a las vecinas, y la siguiente supera su límite."), True))
        intro = sentences(parts, lv, PRESENTER_MAX[lang])
        closing = ""
        if w.ev.get("capped"):
            closing = ("Protection is still tripping lines when the model stops." if en
                       else "Las protecciones siguen disparando líneas cuando el modelo se detiene.")
        elif islanded and w.people:
            closing = "Then the grid settles, split into pieces." if en else "Luego la red se estabiliza, partida en pedazos."
        analyst = ""
        for k in range(min(lv.k, len(run)), -1, -1):
            k_eff = len(run) if len(run) <= k + 1 else k  # never group a single step
            seen: set = set()
            parts = [_step_sentence(w, s, lang, seen) for s in tl if int(s.get("n", 0)) == 0]
            parts += [_step_sentence(w, s, lang, seen) for s in run[:k_eff]]
            if len(run) > k_eff:
                parts.append(_group_sentence(w, run[k_eff:], lang, after=k_eff > 0 or bool(parts), seen=seen))
            text = " ".join(parts)
            if closing and lv.opt and plain_len(text + " " + closing) <= ANALYST_MAX[lang]:
                text += " " + closing
            analyst = text
            if plain_len(text) <= ANALYST_MAX[lang]:
                break
        out["narr"][lang] = [_seg("presenter", intro), _seg("analyst", analyst)]
        if run:
            h = (f"{w.steps} {'step' if w.steps == 1 else 'steps'}: {trips} {'line' if trips == 1 else 'lines'} tripped" if en
                 else f"{w.steps} {'paso' if w.steps == 1 else 'pasos'}: se {'disparó' if trips == 1 else 'dispararon'} {trips} {'línea' if trips == 1 else 'líneas'}")
            if w.ev.get("capped"):
                h += ", still spreading when the model stopped" if en else ", y seguía cuando el modelo se detuvo"
            elif islanded and w.people:
                h += ", and the grid split apart" if en else ", y la red se partió"
        else:
            h = (f"The storm knocked out {w.storm:,} lines; nothing else tripped" if en
                 else f"La tormenta derribó {w.storm:,} líneas; nada más se disparó")
        out["headline"][lang] = h
        lines = []
        if w.storm:
            lines.append(f"Storm · {w.storm:,} lines out" if en else f"Tormenta · {w.storm:,} líneas fuera")
        for s in run[: 3 - len(lines) - (1 if len(run) > 2 else 0)]:
            ln = (s.get("lines") or [{}])[0]
            lab = cap(w.line_label(ln.get("id"), lang, fallback=ln.get("label"))) if s.get("action") != "shed" else (
                "Load cut to hold a line" if en else "Carga cortada para sostener una línea")
            pct = ln.get("pct_before")
            lines.append((f"Step {s['n']} · " if en else f"Paso {s['n']} · ") + lab + (f" · {round(pct)}%" if pct is not None and s.get("action") != "shed" else ""))
        shown = len([x for x in lines if not x.startswith(("Storm", "Tormenta"))])
        if len(run) > shown:
            rest = len(run) - shown
            lines.append(f"… {rest} more {'step' if rest == 1 else 'steps'}" if en else f"… {rest} {'paso' if rest == 1 else 'pasos'} más")
        out["lines"][lang] = [x[:LINE_MAX] for x in lines[:3]]
    ids = [ln.get("id") for s in run for ln in (s.get("lines") or []) if ln.get("id") is not None]
    storm_ids = []
    for s in tl:
        sl = s.get("storm_lines")
        listed = sl.get("listed") if isinstance(sl, dict) else (sl if isinstance(sl, list) else None)
        if int(s.get("n", 0)) == 0 and listed:
            storm_ids = [x.get("id") if isinstance(x, dict) else x for x in listed][:12]
    pts = [p for bid in ids + storm_ids for p in w.line_pts(bid)]
    if w.steps == 0 and w.storm:  # nothing cascaded: the storm's own damage is the number
        out["big"] = {"value": w.storm, "display": {"en": f"{w.storm:,}", "es": f"{w.storm:,}"},
                      "label": {"en": "lines knocked out by the storm", "es": "líneas derribadas por la tormenta"},
                      "fact_key": "event.storm_lines_out", "tone": "alert"}
    else:
        out["big"] = {"value": w.steps, "display": {"en": f"{w.steps}", "es": f"{w.steps}"},
                      "label": {"en": "steps" if w.steps != 1 else "step", "es": "pasos" if w.steps != 1 else "paso"},
                      "fact_key": "event.steps", "tone": "alert"}
    out["camera"] = cam("bbox", pts, line_ids=ids) if pts else (cam("areas", [a.get("center") for a in w.areas[:4]]) if w.areas else region_cam(w))
    out["map"] = mapspec("replay", 0, w.steps, highlight=ids[:1])
    out["plays"] = _plays(w)
    out["facts_used"] = w.keys("event.steps", "event.storm_lines_out", *[f"step.{s['n']}.line" for s in tl], *[f"step.{s['n']}.pct_before" for s in tl])
    return out


def s_areas(w: Writer, lv: Level) -> dict:
    out = {"kind": "areas", "headline": {}, "lines": {}, "narr": {}}
    top = w.areas[0]
    for lang in LANGS:
        en = lang == "en"
        who = people_say(w.people, lang)
        share = share_say(w.ev.get("people_share_pct"), lang)
        s1 = (f"In all, {who} lose power" + (f", {share} of {w.region_name}'s residents" if share else "") + "." if en
              else f"En total, {who} se quedan sin luz" + (f", el {share} de los habitantes de {w.region_es}" if share else "") + ".")
        s2 = ("These are estimates: the load the model loses, counted as the residents it serves." if en
              else "Son estimaciones: la carga que pierde el modelo, contada como los habitantes a los que abastece.")
        body = sentences([(("Where the lights went out." if en else "Dónde se fue la luz."), False), (s1, False), (s2, True)], lv, PRESENTER_MAX[lang])
        items = []
        for i, a in enumerate(w.areas[: max(lv.areas, 1)]):
            p = people_say(a["people"], lang, noun=(i == 0))
            items.append(cue("area", a["area"]) + f"{a['area']}: {p}.")
        out["narr"][lang] = ([] if lv.short else [_seg("presenter", body)]) + [_seg("analyst", " ".join(items))]
        out["headline"][lang] = (f"Where the lights went out: {top['area']} was hit hardest" if en
                                 else f"Dónde se fue la luz: {top['area']}, la zona más afectada")
        out["lines"][lang] = [(f"{a['area']} · {a['people']:,} " + ("people (estimate)" if en else "personas (estimación)"))[:LINE_MAX] for a in w.areas[:3]]
    out["big"] = {"value": top["people"], "display": {"en": f"{top['people']:,}", "es": f"{top['people']:,}"},
                  "label": {"en": f"people in {top['area']} (estimate)", "es": f"personas en {top['area']} (estimación)"},
                  "fact_key": (w.area_key(top["area"]) or [None])[0], "tone": "alert"}
    shown = w.areas[: max(lv.areas, 1)]
    out["camera"] = cam("areas", [a.get("center") for a in shown], center=top.get("center"),
                        sub_ids=[sid for a in shown for sid in (a.get("sub_ids") or [])])
    out["camera"]["bboxes"] = {a["area"]: a.get("bbox") for a in shown if a.get("bbox")}
    out["map"] = mapspec("final", w.steps, w.steps)
    out["facts_used"] = w.keys("event.people_out", "event.people_share_pct") + [k for a in shown for k in w.area_key(a["area"])]
    return out


def s_hospitals(w: Writer, lv: Level) -> dict:
    h = w.r.get("hospitals") or {}
    count = int(h.get("count") or 0)
    per = [a for a in (h.get("areas") or []) if a.get("count")][:3]
    w.add("deck.hospitals.count", "Hospitals in the areas without power", count)
    out = {"kind": "hospitals", "headline": {}, "lines": {}, "narr": {}}
    for lang in LANGS:
        en = lang == "en"
        # never open a spoken sentence on a numeral ("146 hospitals sit..." reads badly and sounds worse)
        cw = words(count, lang, before_noun=True) if count <= 99 else num(count, lang)
        s1 = (f"The areas without power include {cw} {'hospital' if count == 1 else 'hospitals'}" if en
              else f"En las zonas sin luz hay {cw} {'hospital' if count == 1 else 'hospitales'}")
        if lv.hosp and per:
            parts_ = [f"{words(a['count'], lang, before_noun=True)} in {a['area']}" if en else f"{words(a['count'], lang, before_noun=True)} en {a['area']}" for a in per]
            s1 += ": " + join(parts_, lang)
        if count == 1:
            s2 = "It would have to run on backup power." if en else "Tendría que funcionar con energía de respaldo."
        else:
            s2 = "Each would have to run on backup power." if en else "Cada uno tendría que funcionar con energía de respaldo."
        out["narr"][lang] = [_seg("presenter", sentences([(s1 + ".", False), (s2, False)], lv, PRESENTER_MAX[lang]))]
        out["headline"][lang] = (f"{count} {'hospital' if count == 1 else 'hospitals'} in the dark areas" if en
                                 else f"{count} {'hospital' if count == 1 else 'hospitales'} en las zonas sin luz")
        lines = [(f"{a['area']} · {a['count']} " + (("hospital" if a["count"] == 1 else "hospitals") if en else ("hospital" if a["count"] == 1 else "hospitales"))) for a in per[:2]]
        src = h.get("source")
        lines.append(((f"Counts only · source: {src}" if en else f"Solo conteos · fuente: {src}") if src else ("Counts only, no names" if en else "Solo conteos, sin nombres"))[:LINE_MAX])
        out["lines"][lang] = lines[:3]
    out["big"] = {"value": count, "display": {"en": f"{count}", "es": f"{count}"},
                  "label": {"en": "hospitals in the dark areas", "es": "hospitales en las zonas sin luz"}, "fact_key": "deck.hospitals.count", "tone": "alert"}
    centers = [a.get("center") for a in w.areas if a["area"] in {p["area"] for p in per}]
    out["camera"] = cam("areas", centers) if centers else region_cam(w)
    out["map"] = mapspec("final", w.steps, w.steps)
    out["facts_used"] = w.keys("deck.hospitals.count")
    return out


def s_cost(w: Writer, lv: Level) -> dict:
    c = w.r.get("cost") or {}
    hours = c.get("duration_h_assumed")
    out = {"kind": "cost", "headline": {}, "lines": {}, "narr": {}}
    for key, label in (("blackout_usd", "Blackout cost"), ("upgrade_usd", "Upgrades that prevent it"), ("campus_bill_usd_per_year", "Campus power bill per year")):
        v = c.get(key)
        if v:
            for lang in LANGS:
                _, scaled = usd_say(v, lang)
                if scaled is not None:
                    w.add(f"deck.cost.{key}.scaled", f"{label}, scaled", scaled, "USD (scaled)", True)
    # costs.py prices "the upgrades that stop the cascade": after a storm they spare only the cascade's
    # share, never the people the damage itself cut off, so the copy must not say they "prevent" it
    upgrade_fx = next((f for f in w.fixes if f.get("family") == "upgrade"), None)
    prevents = w.verdict == "preventable" and upgrade_fx is not None and upgrade_fx.get("verdict") == "holds"
    h_show = _dec(float(hours), 1) if hours else None
    for lang in LANGS:
        en = lang == "en"
        parts: list[tuple[str, bool]] = [(("What it costs." if en else "Lo que cuesta."), False)]
        if c.get("blackout_usd"):
            usd, _ = usd_say(c["blackout_usd"], lang)
            if hours:
                s = (f"If the outage lasted {h_show} hours, it would cost the people and businesses without power about {usd}." if en
                     else f"Si el apagón durara {h_show} horas, costaría unos {usd} a quienes se quedan sin luz.")
            else:
                s = (f"The outage would cost the people and businesses without power about {usd}." if en
                     else f"El apagón costaría unos {usd} a quienes se quedan sin luz.")
            parts.append((s, False))
        if c.get("upgrade_usd"):
            usd, _ = usd_say(c["upgrade_usd"], lang)
            if prevents:
                s = (f"The line upgrades that would prevent it cost about {usd}." if en
                     else f"Los refuerzos de líneas que lo evitarían cuestan unos {usd}.")
            elif w.storm or w.verdict == "no_fix":
                s = (f"Upgrades that stop the cascade cost about {usd}, but they cannot reconnect the people the damage cut off." if en
                     else f"Los refuerzos que detienen la cascada cuestan unos {usd}, pero no reconectan a quienes el daño dejó aislados.")
            else:
                s = (f"The line upgrades that stop the cascade cost about {usd}." if en
                     else f"Los refuerzos de líneas que detienen la cascada cuestan unos {usd}.")
            parts.append((s, False))
        if c.get("campus_bill_usd_per_year"):
            usd, _ = usd_say(c["campus_bill_usd_per_year"], lang)
            parts.append(((f"The data center's own power bill would be about {usd} a year." if en
                           else f"La factura eléctrica del propio centro de datos sería de unos {usd} al año."), 2))
        out["narr"][lang] = [_seg("presenter", sentences(parts, lv, PRESENTER_MAX[lang]))]
        if c.get("blackout_usd"):
            h_ = (f"Estimated cost of the blackout: {usd_show(c['blackout_usd'])}" if en else f"Costo estimado del apagón: {usd_show(c['blackout_usd'])}")
            if hours:
                h_ += f" (assumes {h_show} hours out)" if en else f" (supone {h_show} horas sin luz)"
        else:
            h_ = "What it costs (estimates)" if en else "Lo que cuesta (estimaciones)"
        out["headline"][lang] = h_
        lines = []
        if c.get("upgrade_usd"):
            what = ("Upgrades that prevent it" if prevents else "Upgrades that stop the cascade") if en else (
                "Refuerzos que lo evitan" if prevents else "Refuerzos que detienen la cascada")
            lines.append(f"{what}: {usd_show(c['upgrade_usd'])} " + ("(estimate)" if en else "(estimación)"))
        if c.get("campus_bill_usd_per_year"):
            lines.append((f"Campus power bill: {usd_show(c['campus_bill_usd_per_year'])} a year (estimate)" if en
                          else f"Factura del campus: {usd_show(c['campus_bill_usd_per_year'])} al año (estimación)"))
        if c.get("who_pays") and en:
            # costs.py's range, read as a reader would say it ("$0.00 to $0.04" -> "up to $0.04")
            who = re.sub(r"^\$0\.00 to (\$\d[\d.,]*)", r"up to \1", str(c["who_pays"]))
            lines.append(f"Who pays (estimate): {who}"[:LINE_MAX])
        out["lines"][lang] = lines[:3]
    v = c.get("blackout_usd") or c.get("upgrade_usd") or 0
    out["big"] = {"value": v, "display": {"en": usd_show(v), "es": usd_show(v)},
                  "label": {"en": "blackout cost (estimate" + (f", assumes {h_show} hours out)" if hours else ")"),
                            "es": "costo del apagón (estimación" + (f", supone {h_show} horas sin luz)" if hours else ")")},
                  "fact_key": "cost.blackout_usd", "tone": "alert"}
    out["camera"] = region_cam(w) if not w.areas else cam("areas", [a.get("center") for a in w.areas[:4]])
    out["map"] = mapspec("final", w.steps, w.steps)
    out["facts_used"] = [k for k in w.facts if k.startswith("cost.")]
    return out


def s_cause(w: Writer, lv: Level) -> dict:
    rc = w.r.get("root_cause") or {}
    line = rc.get("line") or {}
    bid = line.get("id")
    cause = rc.get("cause") or "none"
    pw, po = rc.get("pct_with"), rc.get("pct_without")
    share = rc.get("campus_share_pct")
    out = {"kind": "cause", "headline": {}, "lines": {}, "narr": {}}
    for lang in LANGS:
        en = lang == "en"
        label = w.line_label(bid, lang, fallback=line.get("label"))
        parts: list[tuple[str, bool]] = [(("Why it happened." if en else "Por qué pasó."), False)]
        dc_en, dc_es = w.dc("en"), w.dc("es")
        if cause == "storm":
            parts.append((("The storm cut the lines first; the rest of the failure follows from that damage." if en
                           else "La tormenta cortó las líneas primero; el resto de la falla viene de ese daño."), False))
            if w.sites:
                parts.append(((f"Without {dc_en}, the same people lose power." if en
                               else f"Sin {dc_es}, las mismas personas se quedan sin luz."), False))
            elif pw is not None:
                parts.append(((f"The first line to overload after it, {label}, ran at {pct_say(pw, lang)} of its rating." if en
                               else f"La primera línea en sobrecargarse después, {label}, llegó al {pct_say(pw, lang)} de su capacidad."), True))
        else:
            parts.append(((f"The first line to fail was {label}." if en else f"La primera línea en fallar fue {label}."), False))
            if cause == "campus" and pw is not None and po is not None:
                parts.append(((f"With {dc_en} it ran at {pct_say(pw, lang)} of its rating; without, at {pct_say(po, lang)}." if en
                               else f"Con {dc_es} llegó al {pct_say(pw, lang)} de su capacidad; sin {'ellos' if w.multi else 'él'}, al {pct_say(po, lang)}."), False))
                s = "The new load alone pushed it over its limit" if en else "La nueva carga, por sí sola, la llevó por encima de su límite"
                if rc.get("people_without_campus") == 0:
                    s += ", and without the new load nobody loses power" if en else ", y sin esa carga nadie se queda sin luz"
                parts.append((s + ".", False))
            elif cause == "last_straw" and po is not None:
                parts.append(((f"It was already at {pct_say(po, lang)} before {dc_en}; the new load pushed it over its limit." if en
                               else f"Ya estaba al {pct_say(po, lang)} antes de{'l' if not w.multi else ''} {dc_es[3:] if not w.multi else dc_es}; la nueva carga la llevó por encima de su límite."), False))
            elif cause == "heat" and pw is not None:
                s = (f"It ran at {pct_say(pw, lang)} of its rating" if en else f"Llegó al {pct_say(pw, lang)} de su capacidad")
                if po is not None and w.sites:
                    s += (f", and even without {dc_en} it would be at {pct_say(po, lang)}" if en
                          else f", y aun sin {dc_es} estaría al {pct_say(po, lang)}")
                parts.append((s + ".", False))
                parts.append(((f"The heat, not {dc_en}, is the cause." if en else f"La causa es el calor, no {dc_es}.") if w.sites
                              else ("Demand alone is the cause." if en else "La causa es solo la demanda."), False))
            elif pw is not None:
                parts.append(((f"It ran at {pct_say(pw, lang)} of its rating." if en else f"Llegó al {pct_say(pw, lang)} de su capacidad."), False))
        out["narr"][lang] = [_seg("presenter", sentences(parts, lv, PRESENTER_MAX[lang]))]
        lines = []
        if cause == "storm":
            h = ((f"The cause: storm damage, not {dc_en}" if en else f"La causa: el daño de la tormenta, no {dc_es}") if w.sites
                 else ("The cause: storm damage" if en else "La causa: el daño de la tormenta"))
            if w.storm:
                lines.append(f"Storm: {w.storm:,} lines cut" if en else f"Tormenta: {w.storm:,} líneas cortadas")
            if pw is not None:
                lines.append((f"First overload after it: {round(pw)}% of its rating" if en else f"Primera sobrecarga después: {round(pw)}% de su capacidad"))
            if w.sites:
                lines.append((f"Without {dc_en}: the same people lose power" if en else f"Sin {dc_es}: las mismas personas sin luz"))
        else:
            h = (f"The first line to fail: {label}" if en else f"La primera línea en fallar: {label}")
            if pw is not None:
                h += f" at {round(pw)}%" if en else f" al {round(pw)}%"
            if po is not None and w.sites and cause in ("campus", "last_straw"):
                h += f" — {round(po)}% without {dc_en}" if en else f" — {round(po)}% sin {dc_es}"
            if pw is not None:
                lines.append(((f"With {dc_en}: " if w.sites else "Loading when it tripped: ") if en
                              else (f"Con {dc_es}: " if w.sites else "Carga al dispararse: ")) + f"{round(pw)}%" + (" of its rating" if en else " de su capacidad"))
            if po is not None and w.sites:
                lines.append((f"Without {dc_en}: {round(po)}%" if en else f"Sin {dc_es}: {round(po)}%"))
            if share is not None and w.sites:
                lines.append((f"Share of its flow from {dc_en}: {round(share)}%" if en else f"Parte de su flujo que viene de{'l' if not w.multi else ''} {dc_es[3:] if not w.multi else dc_es}: {round(share)}%"))
        out["headline"][lang] = cap(h)
        out["lines"][lang] = [x[:LINE_MAX] for x in lines[:3]]
    if cause == "storm" and w.storm:
        out["big"] = {"value": w.storm, "display": {"en": f"{w.storm:,}", "es": f"{w.storm:,}"},
                      "label": {"en": "lines cut by the storm", "es": "líneas cortadas por la tormenta"},
                      "fact_key": "event.storm_lines_out", "tone": "alert"}
    elif share is not None and w.sites and cause != "storm":
        out["big"] = {"value": share, "display": {"en": f"{round(share)}%", "es": f"{round(share)}%"},
                      "label": {"en": f"of its flow came from {w.dc('en')}", "es": f"de su flujo vino de{'l' if not w.multi else ''} {w.dc('es')[3:] if not w.multi else w.dc('es')}"},
                      "fact_key": "cause.campus_share_pct", "tone": "alert" if cause == "campus" else "neutral"}
    elif pw is not None:
        out["big"] = {"value": pw, "display": {"en": f"{round(pw)}%", "es": f"{round(pw)}%"},
                      "label": {"en": "loading of the first line to fail", "es": "carga de la primera línea en fallar"},
                      "fact_key": "cause.pct_with", "tone": "alert"}
    else:
        out["big"] = None
    pts = w.line_pts(bid)
    out["camera"] = cam("line", pts, line_ids=[bid] if bid is not None else []) if pts else region_cam(w)
    out["map"] = mapspec("cause", 0, 0, highlight=[bid] if bid is not None else [])
    out["facts_used"] = w.keys("cause.line", "cause.pct_with", "cause.pct_without", "cause.campus_share_pct", "cause.people_due_to_campus")
    return out


def _upgrade_what(fx: dict, lang: str) -> str:
    """'one line and one transformer' / 'una línea y un transformador', from the fix's upgrade list."""
    d = fx.get("detail") or {}
    lst = d.get("list") or []
    if lst:
        nt = sum(1 for x in lst if x.get("transformer"))
        nl = len(lst) - nt
    else:
        nt, nl = 0, int(d.get("lines") or len((fx.get("apply") or {}).get("upgrades") or {}))
    parts = []
    if lang == "en":
        if nl:
            parts.append(f"{words(nl, 'en')} {'line' if nl == 1 else 'lines'}")
        if nt:
            parts.append(f"{words(nt, 'en')} {'transformer' if nt == 1 else 'transformers'}")
    else:
        if nl:
            parts.append(f"{words(nl, 'es', fem=True)} {'línea' if nl == 1 else 'líneas'}")
        if nt:
            parts.append(f"{words(nt, 'es', before_noun=True)} {'transformador' if nt == 1 else 'transformadores'}")
    return join(parts, lang) or ("the lines" if lang == "en" else "las líneas")


def _fix_phrase(w: Writer, fx: dict, lang: str) -> str:
    """A fix as a spoken action (a verb phrase), built from the engine's verified numbers (`detail`,
    `apply`) so both languages say the same thing."""
    en = lang == "en"
    fam, ap, d = fx.get("family"), fx.get("apply") or {}, fx.get("detail") or {}
    size = d.get("mw", ap.get("mw"))
    if fam == "shrink" and size is not None:
        if w.multi:
            return (f"build {mw_say(size, lang)} in all instead of {num(w.mw)}" if en
                    else f"construir {mw_say(size, lang)} en total en lugar de {num(w.mw, lang)}")
        return (f"build {mw_say(size, lang)} instead of {num(w.mw)}" if en
                else f"construir {mw_say(size, lang)} en lugar de {num(w.mw, lang)}")
    if fam == "move":
        area = w.area_near(ap["lat"], ap["lon"]) if ap.get("lat") is not None and ap.get("lon") is not None else (
            (d.get("sites") or [{}])[0].get("town") or d.get("town"))
        if area:
            w.names.add(area)
            if w.multi:
                return f"build them near {area} instead" if en else f"construirlos cerca de {area}"
            return f"build it at a {area} substation instead" if en else f"construirlo en una subestación de {area}"
        return ("build them somewhere else" if w.multi else "build it somewhere else") if en else (
            "construirlos en otro lugar" if w.multi else "construirlo en otro lugar")
    if fam == "flexible":
        it_en, it_es = ("them", "hacerlos flexibles") if w.multi else ("it", "hacerlo flexible")
        if size is not None:
            return (f"make {it_en} flexible, curtailing to {mw_say(size, lang)}{' in all' if w.multi else ''} at the peak" if en
                    else f"{it_es}, recortando a {mw_say(size, lang)}{' en total' if w.multi else ''} en el pico")
        return f"make {it_en} flexible, curtailing at the peak" if en else f"{it_es}, recortando en el pico"
    if fam == "time_of_day":
        if not w.sites:  # a heat-only case: there is no campus to reschedule, only the hour itself
            return "wait for a cooler hour" if en else "esperar a una hora más fresca"
        return "run it only at quieter hours" if en else "operarlo solo en las horas de menos demanda"
    if fam == "upgrade":
        return f"upgrade {_upgrade_what(fx, 'en')}" if en else f"reforzar {_upgrade_what(fx, 'es')}"
    if fam == "combo":
        if size is not None:
            return (f"build {mw_say(size, lang)} and upgrade {_upgrade_what(fx, 'en')}" if en
                    else f"construir {mw_say(size, lang)} y reforzar {_upgrade_what(fx, 'es')}")
        return "build it smaller and upgrade lines" if en else "construirlo más pequeño y reforzar líneas"
    if fam == "onsite":
        mw = d.get("onsite_mw")
        if mw:
            return (f"add {mw_say(mw, lang)} of on-site generation" if en else f"añadir {mw_say(mw, lang)} de generación propia")
        return "generate part of its power on site" if en else "generar parte de su energía en el propio sitio"
    if fam == "remove":
        return "not build it here" if en else "no construirlo aquí"
    act = fx.get("action") or fx.get("label") or ""
    return speak_units(act[:1].lower() + act[1:], lang) if en and act else ("another fix" if en else "otra solución")


def _fix_verdict(w: Writer, fx: dict, lang: str, short: bool = False) -> str:
    en = lang == "en"
    v = fx.get("verdict")
    o = fx.get("outcome") or {}
    if v == "holds":
        if int(o.get("steps") or 0) == 0 and not short:
            return "holds, no line trips" if en else "funciona, ninguna línea se dispara"
        return "holds" if en else "funciona"
    if v == "partly":
        p = o.get("people")
        if p and p >= 1000:
            return (f"partly; {people_say(p, 'en')} still lose power" if en else f"en parte; {people_say(p, 'es')} siguen sin luz")
        return "partly" if en else "en parte"
    return {"fails": ("fails", "no funciona"), "not_needed": ("not needed", "no hace falta"),
            "not_checked": ("not checked in time", "no se comprobó a tiempo")}.get(v, ("not checked", "sin comprobar"))[0 if en else 1]


def _fix_big(w: Writer, fx: dict, lang: str) -> str:
    fam, ap = fx.get("family"), fx.get("apply") or {}
    en = lang == "en"
    d = fx.get("detail") or {}
    if fam in ("shrink", "flexible") and (d.get("mw") or ap.get("mw")) is not None:
        v = float(d.get("mw") or ap["mw"])
        return f"{round(v):,} MW" if v >= 100 else mw_show(v)  # as the narration says it
    if fam == "onsite" and d.get("onsite_mw"):
        return "+" + mw_show(d["onsite_mw"]) + (" on site" if en else " propios")
    if fam in ("upgrade", "combo") and ap.get("upgrades"):
        n = len(ap["upgrades"])
        return f"{n} {'upgrade' if n == 1 else 'upgrades'}" if en else f"{n} {'refuerzo' if n == 1 else 'refuerzos'}"
    if fam == "move" and ap.get("lat") is not None:
        return w.area_near(ap["lat"], ap["lon"])
    return {"flexible": ("Flexible", "Flexible"), "combo": ("Smaller + upgrades", "Menor + refuerzos"),
            "time_of_day": ("Another hour", "Otra hora"), "remove": ("No campus", "Sin campus")}.get(fam, ("Fix", "Solución"))[0 if en else 1]


def s_fix(w: Writer, lv: Level) -> dict:
    out = {"kind": "fix", "headline": {}, "lines": {}, "narr": {}}
    best = w.best
    if w.verdict == "nothing_happened" or best is None:  # a calm case: the room left at the site
        room = w.room
        for lang in LANGS:
            en = lang == "en"
            if room is not None:
                s = (f"This site has room. The grid here can take about {mw_say(room, lang)} before the first line overloads." if en
                     else f"Este sitio tiene margen. La red aquí admite unos {mw_say(room, lang)} antes de que la primera línea se sobrecargue.")
            else:
                s = "Nothing needs fixing: every line stays within its limit." if en else "No hay nada que arreglar: todas las líneas aguantan."
            out["narr"][lang] = [_seg("presenter", s)]
            out["headline"][lang] = ((f"Room left at this site: {mw_show(room)}" if en else f"Margen en este sitio: {mw_show(room)}") if room is not None
                                     else ("Nothing to fix" if en else "Nada que arreglar"))
            out["lines"][lang] = [(f"Before the first line overloads · {w.load_show(lang)}" if en else f"Antes de que se sobrecargue la primera línea · {w.load_show(lang)}")]
        out["big"] = ({"value": room, "display": {"en": mw_show(room), "es": mw_show(room)},
                       "label": {"en": "room at this site", "es": "margen en este sitio"}, "fact_key": "event.room_mw", "tone": "good"}
                      if room is not None else None)
        site = w.site_pt()
        out["camera"] = cam("site", [site], center=site) if site else region_cam(w)
        out["map"] = mapspec("calm", 0, 0)
        out["facts_used"] = w.keys("event.room_mw", "deck.room_mw")
        return out
    # the verified solutions, best first: building it HERE at (nearly) the full amount before anything smaller
    sol = [w.fixes[i] for i in (w.r.get("solutions") or []) if isinstance(i, int) and 0 <= i < len(w.fixes)] or [best]
    if best not in sol:
        sol.insert(0, best)
    ordered = sorted(w.fixes, key=lambda f: (f not in sol, sol.index(f) if f in sol else 0, FAMILY_ORDER.index(f["family"]) if f.get("family") in FAMILY_ORDER else 99))
    listed = [f for f in ordered if f.get("verdict") in ("holds", "partly", "fails") and f.get("family") != "remove"][:5]
    n_opts = len(sol) if len(sol) <= 2 else max(2, min(len(sol), 1 + lv.checks))  # always more than one when more than one holds
    opts = sol[:n_opts]
    for lang in LANGS:
        en = lang == "en"
        where = w.where(lang) or w.place or ""
        size = mw_say(w.mw, lang)
        if w.multi:
            intro = (f"How to fix it. If you want to build {words(len(w.sites), 'en')} campuses, {size} in all, this is what you have to do." if en
                     else f"Cómo evitarlo. Si quieres construir {words(len(w.sites), 'es', before_noun=True)} campus, {size} en total, esto es lo que tienes que hacer.")
        else:
            intro = (f"How to fix it. If you want to build {size} here" + (f" at {where}" if where else "") + ", this is what you have to do." if en
                     else f"Cómo evitarlo. Si quieres construir {size} aquí" + (f", en {where}" if where else "") + ", esto es lo que tienes que hacer.")
        segs = [_seg("presenter", sentences([(intro, False), (("There is more than one way, and the engine re-ran every one." if en else "Hay más de una manera, y el motor probó cada una."), 2)], lv, PRESENTER_MAX[lang]))]
        for k, fx in enumerate(opts):
            o = fx.get("outcome") or {}
            ph = _fix_phrase(w, fx, lang)
            kept, pct, cost = fx.get("kept_mw"), fx.get("kept_pct"), fx.get("cost")
            if pct is not None and pct >= 99.5:
                keeps = (f"It keeps all {size}." if en else f"Conserva los {size} completos.")
            elif kept is not None:
                keeps = (f"It keeps {mw_say(kept, lang)} of the {num(w.mw, lang)}." if en else f"Conserva {mw_say(kept, lang)} de los {num(w.mw, lang)}.")
            else:
                keeps = ""
            money = ""
            if cost and cost.get("high"):
                usd, _ = usd_say(cost["high"], lang)
                money = (f"It costs an estimated {usd}." if en else f"Cuesta unos {usd}, según la estimación.")
            works = ("With it, no line trips." if en else "Con ella, ninguna línea se dispara.") if int(o.get("steps") or 0) == 0 else (
                f"With it, {people_say(o.get('people') or 0, 'en')} still lose power." if en else f"Con ella, {people_say(o.get('people') or 0, 'es')} siguen sin luz.")
            by = (" The AI proposed this one, and the engine checked it." if en else " La IA propuso esta, y el motor la comprobó.") if fx.get("by") == "gemini" else ""
            text = (f"Option {words(k + 1, 'en')}: {ph}. {keeps} {money} {works}{by}" if en else f"Opción {words(k + 1, 'es')}: {ph}. {keeps} {money} {works}{by}")
            text = " ".join(text.split())
            if plain_len(text) > ANALYST_MAX[lang]:  # too long: drop the money and the provenance
                text = " ".join((f"Option {words(k + 1, 'en')}: {ph}. {keeps} {works}" if en else f"Opción {words(k + 1, 'es')}: {ph}. {keeps} {works}").split())
            segs.append(_seg("analyst", cue("option", k) + text))
        out["narr"][lang] = segs
        pre = ("Preventable" if en else "Evitable") if w.verdict == "preventable" else ("Partly preventable" if en else "Evitable en parte")
        n_hold = sum(1 for f in sol if f.get("verdict") == "holds")
        target = (mw_show(w.mw) + (f" at {where}" if where and not w.multi else "")) if en else (mw_show(w.mw) + (f" en {where}" if where and not w.multi else ""))
        if w.verdict == "preventable" and n_hold:
            if en:
                out["headline"][lang] = f"To build {target}: {n_hold} verified {'way' if n_hold == 1 else 'ways'}"
            else:
                out["headline"][lang] = f"Para construir {target}: {n_hold} {'manera verificada' if n_hold == 1 else 'maneras verificadas'}"
        else:
            out["headline"][lang] = f"{pre}: {(best.get('action') or _fix_phrase(w, best, lang))[:80]}"
        out["lines"][lang] = [f"{(f.get('action') if en else None) or cap(_fix_phrase(w, f, lang))} · {VERDICT_CHIP.get(f.get('verdict'), ('', ''))[0 if en else 1]}"[:LINE_MAX]
                              for f in listed[:3]]
    ap = best.get("apply") or {}
    up_ids = [int(k) for k in (ap.get("upgrades") or {})]
    all_ids = sorted({int(k) for f in opts for k in ((f.get("apply") or {}).get("upgrades") or {})})
    kept_best = best.get("kept_mw")
    out["big"] = {"value": kept_best if kept_best is not None else _fix_big(w, best, "en"),
                  "display": {"en": mw_show(kept_best) if kept_best is not None else _fix_big(w, best, "en"), "es": mw_show(kept_best) if kept_best is not None else _fix_big(w, best, "es")},
                  "label": {"en": "kept by the best verified fix", "es": "que conserva la mejor solución verificada"}, "fact_key": f"fix.{best.get('family')}.action", "tone": "good"}
    out["chips"] = [{"family": f.get("family"), "verdict": f.get("verdict"),
                     "label": {"en": cap(_fix_phrase(w, f, "en")), "es": cap(_fix_phrase(w, f, "es"))},
                     "people": (f.get("outcome") or {}).get("people")} for f in ordered]
    out["options"] = [{
        "fix": w.fixes.index(f), "family": f.get("family"),
        "name": {"en": cap(_fix_phrase(w, f, "en")), "es": cap(_fix_phrase(w, f, "es"))},
        "kept_mw": f.get("kept_mw"), "kept_pct": f.get("kept_pct"), "must": f.get("must") or {"en": [], "es": []},
        "cost": f.get("cost"), "by": f.get("by") or "engine", "verdict": f.get("verdict"), "outcome": f.get("outcome"),
        "lines": [{"id": x["id"], "label": x.get("label"), "old_mva": x.get("old_mva"), "new_mva": x.get("new_mva")} for x in ((f.get("detail") or {}).get("list") or [])[:20]],
        "apply": f.get("apply"),
    } for f in opts]
    if all_ids:
        pts = [p for bid in all_ids for p in w.line_pts(bid)]
        out["camera"] = cam("bbox", pts, line_ids=all_ids)
    elif ap.get("lat") is not None:
        pt = [round(float(ap["lon"]), 4), round(float(ap["lat"]), 4)]
        site = w.site_pt()
        out["camera"] = cam("bbox", [pt] + ([site] if site else []), center=pt)
    else:
        site = w.site_pt()
        out["camera"] = cam("site", [site], center=site) if site else region_cam(w)
    out["map"] = mapspec("fix", 0, 0, highlight=up_ids, apply=ap or None)
    out["facts_used"] = [k for k in w.facts if k.startswith("fix.")][:20]
    return out


FAMILY_SAY = {
    "shrink": ("a smaller campus", "un campus más pequeño"), "move": ("another site", "otro sitio"),
    "flexible": ("flexible load", "carga flexible"), "time_of_day": ("another hour", "otra hora"),
    "upgrade": ("line upgrades", "refuerzos de líneas"), "onsite": ("on-site generation", "generación propia"),
    "combo": ("a smaller campus with upgrades", "un campus menor con refuerzos"), "remove": ("no campus at all", "ningún campus"),
}


def s_no_fix(w: Writer, lv: Level) -> dict:
    nf = w.r.get("no_fix") or {}
    split = w.r.get("split") or {}
    bound = w.r.get("bound") or {}
    n = int(nf.get("people") or bound.get("people") or 0)
    proof = [p for p in (nf.get("proof") or []) if isinstance(p, dict) and p.get("family") in FAMILY_SAY]
    # the best fix the engine actually ran (not the no-campus floor, not a family it skipped as not needed)
    ran = [p for p in proof if p.get("verdict") in ("holds", "partly", "fails") and p.get("family") != "remove" and p.get("people") is not None]
    best_p = min(ran, key=lambda p: p["people"]) if ran else None
    best_fx = next((f for f in w.fixes if best_p and f.get("family") == best_p.get("family")), None)
    stops = bool(best_fx) and w.steps > 0 and int((best_fx.get("outcome") or {}).get("steps") or 0) == 0
    out = {"kind": "no_fix", "headline": {}, "lines": {}, "narr": {}}
    for lang in LANGS:
        en = lang == "en"
        cause_ = "the storm cut the lines that serve them" if en else "la tormenta cortó las líneas que las abastecen"
        if not w.storm:
            cause_ = "demand is more than the lines left can carry" if en else "la demanda supera lo que pueden llevar las líneas que quedan"
        best_s = ""
        if best_p is not None:
            fam_ = FAMILY_SAY[best_p["family"]][0 if en else 1]
            if stops:
                best_s = (f"The best fix we ran, {fam_}, stops the cascade that follows, and nothing more." if en
                          else f"La mejor solución que probamos, {fam_}, detiene la cascada posterior, y nada más.")
            else:
                best_s = (f"The best fix we ran, {fam_}, saves only part of it." if en
                          else f"La mejor solución que probamos, {fam_}, salva solo una parte.")
        body = sentences([
            ((f"No fix exists for {people_say(n, lang)}." if en else f"No hay solución para {people_say(n, lang)}."), False),
            ((f"Even with unlimited line ratings and no data center, they stay dark: {cause_}." if en
              else f"Aun con líneas de capacidad ilimitada y sin centro de datos, siguen sin luz: {cause_}."), False),
            (best_s, True),
            (("Only rebuilding brings them back." if en else "Solo reconstruir les devuelve la luz.") if w.storm and not lv.short else "", False),
        ], lv, PRESENTER_MAX[lang])
        segs = [_seg("presenter", body)]
        phys, casc, camp = split.get("physical"), split.get("cascade"), split.get("campus")
        if phys and not lv.short and not casc and not camp and phys >= 0.995 * max(w.people, 1):
            segs.append(_seg("analyst", "Every one of them is cut off by the damage itself; nothing cascades after it." if en
                             else "Todas quedan aisladas por el propio daño; nada se propaga después."))
        elif phys and not lv.short:
            s = (f"Of {people_say(w.people, 'en', noun=False)} without power, {people_say(phys, 'en', noun=False)} are cut off by the damage itself" if en
                 else f"De {people_say(w.people, 'es', noun=False)} sin luz, {people_say(phys, 'es', noun=False)} quedan aisladas por el propio daño")
            if casc:
                s += (f", {people_say(casc, 'en', noun=False)} by the cascade that follows" if en else f", {people_say(casc, 'es', noun=False)} por la cascada posterior")
            if w.sites:
                s += ((f", and {people_say(camp, 'en', noun=False)} by the data center" if en else f", y {people_say(camp, 'es', noun=False)} por el centro de datos")
                      if camp else (", and none by the data center" if en else ", y ninguna por el centro de datos"))
            if lang == "es" and phys >= 999_500:
                s = s.replace("quedan aisladas", "quedan aislados")
            segs.append(_seg("analyst", s + "."))
        out["narr"][lang] = segs
        out["headline"][lang] = (f"No fix exists for about {people_round(n, 'en')} people (estimate)" if en
                                 else f"No hay solución para unas {people_round(n, 'es')} personas (estimación)" if n < 999_500
                                 else f"No hay solución para unos {people_round(n, 'es')} de personas (estimación)")
        if best_p is not None:
            fam_ = FAMILY_SAY[best_p["family"]][0 if en else 1]
            if stops:
                lines = [(f"Best fix run, {fam_}: stops the cascade only" if en
                          else f"Mejor solución probada, {fam_}: solo detiene la cascada")[:LINE_MAX]]
            else:
                lines = [(f"Best fix run, {fam_}: {int(best_p['people']):,} still out (estimate)" if en
                          else f"Mejor solución probada, {fam_}: {int(best_p['people']):,} siguen sin luz (estimación)")[:LINE_MAX]]
        else:
            lines = ["Every fix family checked against the model" if en else "Cada familia de soluciones, comprobada en el modelo"]
        if phys:
            lines.append((f"Cut off by the damage: {phys:,} (estimate)" if en else f"Aislados por el daño: {phys:,} (estimación)"))
        if casc is not None and phys:
            lines.append((f"Cascade: {casc:,} · data center: {camp or 0:,} (estimates)" if en
                          else f"Cascada: {casc:,} · centro de datos: {camp or 0:,} (estimaciones)"))
        out["lines"][lang] = lines[:3]
    out["big"] = {"value": n, "display": {"en": f"~{people_round(n, 'en')}", "es": f"~{people_round(n, 'es')}"},
                  "label": {"en": "people no fix can reach (estimate)", "es": "personas que ninguna solución alcanza (estimación)"},
                  "fact_key": "bound.people", "tone": "alert"}
    out["table"] = {"proof": [{"family": p.get("family"), "verdict": p.get("verdict"), "people": p.get("people"),
                               "label": {"en": cap(FAMILY_SAY.get(p.get("family"), (p.get("family"), p.get("family")))[0] or ""),
                                         "es": cap(FAMILY_SAY.get(p.get("family"), (p.get("family"), p.get("family")))[1] or "")}}
                              for p in (nf.get("proof") or [])],
                    "split": {k: split.get(k) for k in ("physical", "cascade", "campus")}}
    out["camera"] = region_cam(w)
    out["map"] = mapspec("final", w.steps, w.steps)
    out["facts_used"] = w.keys("bound.people", "bound.share_pct", "split.physical", "split.cascade", "split.campus")
    return out


def s_recovery(w: Writer, lv: Level) -> dict:
    rec = w.r.get("recovery") or {}
    waves = [x for x in (rec.get("waves") or []) if isinstance(x, dict)]
    hard = [x for x in (rec.get("hardening") or []) if isinstance(x, dict)]
    lp = rec.get("method") == "lp"
    for wv in waves:  # cumulative per wave (the engine's lines_total / km_total)
        cnt = wv.get("lines_total")
        if cnt is None:
            cnt = len(wv.get("lines") or []) if isinstance(wv.get("lines"), list) else int(wv.get("lines") or 0)
        wv["_count"] = int(cnt)
        wv["_km"] = wv.get("km_total", wv.get("km"))
        w.add(f"deck.wave.{wv.get('n')}.lines", f"Lines rebuilt by wave {wv.get('n')} (cumulative)", int(cnt))
    for hd in hard:
        w.add(f"deck.harden.{hd.get('k')}.k", "Lines hardened ahead of time", int(hd.get("k") or 0))
    out = {"kind": "recovery", "headline": {}, "lines": {}, "narr": {}}
    spoken = waves[: (3 if lv.opt else 2)]
    for lang in LANGS:
        en = lang == "en"
        parts: list[tuple[str, bool]] = [
            (("How to recover." if en else "Cómo recuperarse."), False),
            (("Repair order matters: the right lines first bring the most people back per repair." if en
              else "El orden importa: reparar primero las líneas correctas devuelve la luz a más personas por reparación."), False),
        ]
        if hard:
            hd = hard[0]
            parts.append(((f"Hardening the {num(hd['k'])} most important lines ahead of time would keep {people_say(hd.get('people_kept_on') or 0, 'en')} connected." if en
                           else f"Reforzar de antemano las {num(hd['k'], 'es')} líneas más importantes mantendría con luz a {people_say(hd.get('people_kept_on') or 0, 'es')}."), True))
        if not lp:
            parts.append((("This order checks connections only; line limits were not applied." if en
                           else "Este orden solo revisa conexiones; no aplica los límites de las líneas."), False))
        items = []
        for i, wv in enumerate(spoken):
            nth = ("First wave", "Second wave", "Third wave")[i] if en else ("Primera ola", "Segunda ola", "Tercera ola")[i]
            km = wv.get("_km")
            cnt = wv["_count"]
            if en:
                s = f"{nth}: rebuild {num(cnt)} {'line' if cnt == 1 else 'lines'}" + (" in all" if i else "")
                s += f", {num(km)} kilometers" if km else ""
                s += f"; {people_say(wv.get('people_back') or 0, 'en')} " + ("have power back." if i else "get power back.")
            else:
                s = f"{nth}: reconstruir {num(cnt, 'es')} {'línea' if cnt == 1 else 'líneas'}" + (" en total" if i else "")
                s += f", {num(km, 'es')} kilómetros" if km else ""
                s += f"; {people_say(wv.get('people_back') or 0, 'es')} " + ("ya tienen luz." if i else "recuperan la luz.")
            items.append(cue("wave", wv.get("n", i + 1)) + s)
        out["narr"][lang] = [_seg("presenter", sentences(parts, lv, PRESENTER_MAX[lang]))] + ([_seg("analyst", " ".join(items))] if items else [])
        first = waves[0] if waves else {}
        out["headline"][lang] = ((f"The first {first.get('_count', 0)} repairs bring back {people_noun(first.get('people_back') or 0, 'en')} (estimate)" if en
                                  else f"Las primeras {first.get('_count', 0)} reparaciones devuelven la luz a {people_noun(first.get('people_back') or 0, 'es')} (estimación)")
                                 if first else ("Rebuild order" if en else "Orden de reconstrucción"))
        lines = []
        for wv in waves[:3]:
            km = f" · {wv['_km']:,.0f} km" if wv.get("_km") else ""
            lines.append((f"Wave {wv.get('n')}: {wv['_count']} lines{km} · {int(wv.get('people_back') or 0):,} back (estimate)" if en
                          else f"Ola {wv.get('n')}: {wv['_count']} líneas{km} · {int(wv.get('people_back') or 0):,} con luz (estimación)")[:LINE_MAX])
        if not lp and lines:
            lines[-1] = (lines[-1] + (" (line limits not applied)" if en else " (sin límites de línea)"))[:LINE_MAX]
        out["lines"][lang] = lines
    third = spoken[-1] if spoken else {}
    out["big"] = ({"value": third.get("people_back"), "display": {"en": f"{int(third.get('people_back') or 0):,}", "es": f"{int(third.get('people_back') or 0):,}"},
                   "label": {"en": f"people back after wave {third.get('n')} (estimate)", "es": f"personas con luz tras la ola {third.get('n')} (estimación)"},
                   "fact_key": f"recovery.wave.{third.get('n')}.people_back", "tone": "good"} if third else None)
    out["waves"] = [{"n": wv.get("n"), "lines": wv["_count"], "km": wv.get("_km"), "people_back": wv.get("people_back"),
                     "people_out": wv.get("people_out"), "areas_relit": wv.get("areas_relit")} for wv in waves]
    out["method"] = rec.get("method")
    out["camera"] = region_cam(w)
    out["map"] = mapspec("restore", w.steps, w.steps, wave=1)
    out["facts_used"] = [k for k in w.facts if k.startswith(("recovery.", "harden."))][:20]
    return out


def s_bottom(w: Writer, lv: Level) -> dict:
    out = {"kind": "bottom_line", "headline": {}, "lines": {}, "narr": {}}
    best = w.best
    ap = (best or {}).get("apply") if w.verdict in ("preventable", "partly") else None
    for lang in LANGS:
        en = lang == "en"
        if w.verdict == "preventable" and best:
            s = sentences([(("Bottom line: this blackout is preventable." if en else "En resumen: este apagón se puede evitar."), False),
                           ((f"The verified fix: {_fix_phrase(w, best, lang)}, and every line stays within its limit." if en
                             else f"La solución verificada: {_fix_phrase(w, best, lang)}, y todas las líneas aguantan."), True)], lv, PRESENTER_MAX[lang])
            h = "Preventable" if en else "Se puede evitar"
        elif w.verdict == "partly" and best:
            o = best.get("outcome") or {}
            s = (f"Bottom line: part of this is preventable. The best verified fix, to {_fix_phrase(w, best, lang)}, cuts the outage to {people_say(o.get('people') or 0, 'en')}." if en
                 else f"En resumen: una parte se puede evitar. La mejor solución verificada, {_fix_phrase(w, best, lang)}, reduce el apagón a {people_say(o.get('people') or 0, 'es')}.")
            h = "Partly preventable" if en else "Evitable en parte"
        elif w.verdict == "no_fix" and not w.storm:  # demand alone, beyond what the lines left can carry
            s = ("Bottom line: demand here is more than the grid can carry; no fix we ran brings everyone back." if en
                 else "En resumen: la demanda supera lo que la red puede llevar; ninguna solución probada devuelve la luz a todos.")
            h = "Beyond what the grid can carry" if en else "Más de lo que la red puede llevar"
        elif w.verdict == "no_fix":
            if lv.short or not w.r.get("recovery"):
                s = ("Bottom line: this is physical damage no fix prevents; it takes rebuilding." if en
                     else "En resumen: es daño físico que ninguna solución evita; hay que reconstruir.")
            else:
                s = ("Bottom line: most of this outage is physical damage no fix can prevent. Rebuild in the order shown, and harden the lines that matter most." if en
                     else "En resumen: la mayor parte de este apagón es daño físico que ninguna solución evita. Hay que reconstruir en el orden indicado y reforzar las líneas clave.")
            h = "Rebuild first, then harden" if en else "Primero reconstruir, después reforzar"
        elif w.verdict == "nothing_happened" and w.sites:
            s = ("Bottom line: this site can take the load; every line stays within its limit." if en
                 else "En resumen: este sitio puede con la carga; todas las líneas aguantan.")
            h = "This site can take the load" if en else "Este sitio puede con la carga"
        elif w.verdict == "nothing_happened":
            s = ("Bottom line: the grid rides this out; every line stays within its limit." if en
                 else "En resumen: la red lo aguanta; todas las líneas se mantienen dentro de su límite.")
            h = "The grid holds" if en else "La red aguanta"
        else:
            s = (f"Bottom line: in this scenario, {people_say(w.people, 'en')} lose power." if en
                 else f"En resumen: en este escenario, {people_say(w.people, 'es')} se quedan sin luz.") if w.people else (
                "Bottom line: every line holds in this scenario." if en else "En resumen: todas las líneas aguantan en este escenario.")
            h = "Bottom line" if en else "En resumen"
        out["narr"][lang] = [_seg("presenter", s, suffix=CLOSE[lang])]
        out["headline"][lang] = h
        out["lines"][lang] = ([("Apply the best fix and watch the map stay calm" if en else "Aplica la mejor solución y mira el mapa en calma")] if ap else [])
    out["big"] = None
    out["cta"] = {"label": {"en": "Apply the best fix", "es": "Aplicar la mejor solución"}, "apply": ap} if ap else None
    site = w.site_pt()
    out["camera"] = cam("site", [site], center=site) if site and w.verdict != "no_fix" else region_cam(w)
    out["map"] = mapspec("fix" if ap else "final", w.steps, w.steps, apply=ap)
    out["facts_used"] = w.keys("event.people_out") + ([f"fix.{best.get('family')}.verdict"] if best and f"fix.{best.get('family')}.verdict" in w.facts else [])
    return out


BUILDERS = {"toll": s_toll, "event": s_event, "chain": s_chain, "areas": s_areas, "hospitals": s_hospitals, "cost": s_cost,
            "cause": s_cause, "fix": s_fix, "no_fix": s_no_fix, "recovery": s_recovery, "bottom_line": s_bottom}


def slide_ids(w: Writer, length: str) -> list[str]:
    r = w.r
    rc = r.get("root_cause") or {}
    cost = r.get("cost") or {}
    has = {
        "toll": bool(cost.get("blackout_usd")) and w.people > 0,  # only when something is lost: else the event slide says it holds
        "event": True,
        "chain": w.steps > 0 or w.storm > 0,
        "areas": w.people > 0 and bool(w.areas),
        "hospitals": int((r.get("hospitals") or {}).get("count") or 0) > 0,
        "cost": bool(r.get("cost")) and bool((r.get("cost") or {}).get("blackout_usd") or (r.get("cost") or {}).get("upgrade_usd")),
        "cause": bool((rc.get("line") or {}).get("id") is not None or rc.get("cause") == "storm") and rc.get("cause") != "none",
        "fix": (w.verdict in ("preventable", "partly") and w.best is not None) or w.verdict == "nothing_happened",
        "no_fix": w.verdict == "no_fix" and bool(r.get("no_fix") or r.get("bound")),
        "recovery": bool((r.get("recovery") or {}).get("waves")) and w.people > 0,  # nothing to bring back otherwise
        "bottom_line": True,
    }
    ids = [i for i in ORDER if has[i]]
    return [i for i in ids if i in SHORT] if length == "short" else ids


# ---------------------------------------------------------------------------------------- assembly
def _marked(seg: dict, body: str | None = None) -> str:
    b = seg["body"] if body is None else body
    out = ""
    for x in (seg.get("prefix", ""), b, seg.get("suffix", "")):
        if x:
            out += x if (not out or not plain_len(out)) else " " + x  # a bare cue marker takes no space
    return out


def _totals(slides: list[dict], bodies: dict) -> dict:
    tot = {lang: 0 for lang in LANGS}
    for s in slides:
        for lang in LANGS:
            for j, seg in enumerate(s["narr"][lang]):
                tot[lang] += plain_len(_marked(seg, bodies.get((s["id"], lang)) if j == 0 and seg["role"] == "presenter" else None))
    return tot


def compose(report: dict, length: str = "full", ai: dict | None = None) -> dict:
    """The deck (without voice keys) for a report. `ai` = {(slide id, lang): validated presenter body}
    replaces template bodies; any that break the budget go back to their template, longest first."""
    w = Writer(report)
    ids = slide_ids(w, length)
    w.lead = ids[0] if ids else "event"
    budget = BUDGET[length]
    ai = {k: v for k, v in (ai or {}).items() if k[0] not in AI_KEEP_TEMPLATE}  # e.g. an older pinned cache
    slides: list[dict] = []
    for lv in (SHORT_LEVELS if length == "short" else LEVELS):
        slides = []
        for sid in ids:
            s = BUILDERS[sid](w, lv)
            s["id"] = sid
            slides.append(s)
        if all(v <= budget[lang] for lang, v in _totals(slides, ai).items()):
            break
    while ai and any(v > budget[lang] for lang, v in _totals(slides, ai).items()):
        over = max(ai, key=lambda k: len(ai[k]))
        del ai[over]
    return {"writer": w, "slides": slides, "ai": ai, "ids": ids}


def finish(report: dict, composed: dict, length: str, ai_meta: dict) -> dict:
    """Voice keys, cue offsets, timings and the response shape."""
    w: Writer = composed["writer"]
    ai = composed["ai"]
    slides_out = []
    totals = {lang: 0 for lang in LANGS}
    est_total = {lang: 0.0 for lang in LANGS}
    flat: dict[str, list[tuple[dict, str, str]]] = {lang: [] for lang in LANGS}
    for s in composed["slides"]:
        narration, written, est = {}, {}, {}
        for lang in LANGS:
            segs = []
            for j, seg in enumerate(s["narr"][lang]):
                use_ai = j == 0 and seg["role"] == "presenter" and (s["id"], lang) in ai
                text, cues = strip_cues(_marked(seg, ai[(s["id"], lang)] if use_ai else None))
                if use_ai:
                    cues += _find_cues(w, text, s)
                segs.append({"role": seg["role"], "text": text, "chars": len(text), "cues": sorted(cues, key=lambda c: c["char"])})
            if segs:
                segs[-1]["cues"].append({"char": segs[-1]["chars"], "name": "slide_end", "value": s["id"]})
            narration[lang] = segs
            written[lang] = "gemini" if (s["id"], lang) in ai else "template"
            chars = sum(x["chars"] for x in segs)
            est[lang] = round(chars / CHARS_PER_S[lang] + GAP_S * max(len(segs) - 1, 0) + HOLD_S, 1)
            totals[lang] += chars
            est_total[lang] += est[lang]
            for x in segs:
                flat[lang].append((x, s["id"], lang))
        slide = {k: v for k, v in s.items() if k not in ("narr",)}
        slide.update({"narration": narration, "written_by": written, "est_s": est})
        slides_out.append(slide)
    for lang in LANGS:  # voice keys, with the neighbors' text for smoother intonation across segments
        seq = flat[lang]
        for i, (seg, _, _) in enumerate(seq):
            prev_t = seq[i - 1][0]["text"] if i else None
            next_t = seq[i + 1][0]["text"] if i + 1 < len(seq) else None
            seg["key"] = voice.register(seg["text"], lang, seg["role"], prev_text=prev_t, next_text=next_t, cues=seg["cues"])
    if w.preset:
        place, place_es = w.preset_name("en"), w.preset_name("es")
    elif w.multi:
        place, place_es = f"{len(w.sites)} data centers", f"{len(w.sites)} centros de datos"
    elif w.place:
        place = place_es = w.place
    elif w.storm and w.top_area():  # a hand-drawn storm: named by where it hit hardest
        place, place_es = f"Storm near {w.top_area()}", f"Tormenta cerca de {w.top_area()}"
    elif w.storm:
        place, place_es = "Storm", "Tormenta"
    else:  # demand alone: the title names the heat, not the town that happened to lose the most
        place, place_es = cap(w.load_show("en")), cap(w.load_show("es"))
    title = {
        "en": f"{TITLE['en']} · {place}" + (f", {w.region_name}" if place != w.region_name and not w.preset else ""),
        "es": f"{TITLE['es']} · {place_es}" + (f", {w.region_es}" if place_es != w.region_name and not w.preset else ""),
    }
    by_list = [x for s in slides_out for x in s["written_by"].values()]
    by = "template" if all(b == "template" for b in by_list) else ("gemini" if all(b == "gemini" for b in _ai_slots(slides_out)) else "mixed")
    deck = {
        "version": VERSION,
        "report_key": report.get("key"),
        "engine": "local" if report.get("engine") == "local" else "briefing",
        "region": w.code,
        "region_name": w.region_name,
        "kind": w.kind,
        "verdict": w.verdict,
        "length": length,
        "title": title,
        "banner": report.get("banner") or "",
        "credit": CREDIT["en"],
        "disclaimer": DISCLAIMER["en"],
        "local": {"es": {"credit": CREDIT["es"], "disclaimer": DISCLAIMER["es"],
                         "banner": f"SIMULACIÓN · modelo sintético de la red de {w.region_es} (Breakthrough Energy / Texas A&M, CC-BY 4.0) · no es la red de ninguna empresa eléctrica · toda cifra de personas o costos es una estimación."}},
        "languages": list(LANGS),
        "slides": slides_out,
        "short": [s["id"] for s in slides_out if s["id"] in PRESENT],
        "total_chars": totals,
        "budget": BUDGET[length],
        "est_s": {lang: round(v, 1) for lang, v in est_total.items()},
        "ai": {"by": by, **ai_meta},
        "extra_facts": w.extra,
        "agentic": report.get("agentic"),  # the AI proposer: {status: running|done|off|error, asked, verified, added}; a deck fetched while it runs lacks its plans
    }
    deck["deck_key"] = hashlib.sha1(json.dumps([report.get("key"), length, VERSION, [(s["id"], s["narration"]) for s in slides_out]],
                                               sort_keys=True, default=str).encode()).hexdigest()
    return deck


def _ai_slots(slides: list[dict]) -> list[str]:
    """written_by of the presenter slots Gemini may write (every slide's first presenter segment)."""
    return [s["written_by"][lang] for s in slides for lang in LANGS
            if s["id"] not in AI_KEEP_TEMPLATE and s["narration"][lang] and s["narration"][lang][0]["role"] == "presenter"]


def _find_cues(w: Writer, text: str, s: dict) -> list[dict]:
    """Cues for prose Gemini wrote: area names and the fix, found by searching the text; a name that
    is not found cues at the start of the segment."""
    cues = []
    low = text.lower()
    if s["id"] == "areas":
        for a in w.areas[:4]:
            i = low.find(a["area"].lower())
            cues.append({"char": max(i, 0), "name": "area", "value": a["area"]})
    if s["id"] in ("fix", "bottom_line") and s.get("map", {}).get("apply"):
        cues.append({"char": 0, "name": "fix", "value": "show"})
    return cues


# ------------------------------------------------------------------------------------------- checks
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
FORBIDDEN_LOCAL = re.compile(
    r"\b(FPL|TECO|JEA|OUC|FMPA|GRU|NextEra|FPUC|KUA|LCEC|ERCOT|FERC|NERC|CAISO|PJM|MISO|NYISO|ConEd|PG&E|Entergy|Oncor|CenterPoint|Dominion|FEMA|EAS)\b"
    r"|(?i:\bflorida power\b|\bduke energy\b|\btampa electric\b|\bgulf power\b|\borlando utilities\b|\bseminole electric\b"
    r"|\blakeland electric\b|\bflorida public utilities\b|\bkissimmee utility\b|\blee county electric\b|\bclay electric\b"
    r"|\bwithlacoochee\b|\bpeace river electric\b|\bemergency alert\b|\bthis is not a test\b|\bnational weather service\b"
    r"|evacua|shelter in place|\bshelter\b|\brefugio\b|alerta de emergencia|esto no es un simulacro|esto no es una prueba)"
    r"|\b[Hh]urricane [A-Z][a-z]+|\b[Hh]urac[aá]n [A-Z][a-z]+"
    r"|\b(Andrew|Charley|Wilma|Irma|Michael|Ian|Idalia|Helene|Milton|Katrina|Harvey|Sandy|Maria|Beryl|Debby)\b"
    r"|\b(19|20)\d\d\b"
)
_DURATION = re.compile(r"\b(\d[\d,.]*)\s*(hours?|days?|weeks?|horas?|d[ií]as?|semanas?)\b", re.IGNORECASE)


def _num_forms(v: float) -> set[str]:
    a = abs(float(v))
    out = {str(int(round(a))), str(int(a)), _dec(a, 1)}
    for k in range(1, 7):
        r = round(a, -k)
        if r:
            out.add(str(int(r)))
    for s in (1e3, 1e6, 1e9):
        for d in (0, 1, 2):
            x = _dec(a / s, d)
            if x not in ("0", ""):
                out.add(x)
    if 0 < a <= 5:
        out.add(str(int(round(a * 100))))  # a load factor as a percent
    return out


def _local_allowed(report: dict, extra: list[dict]) -> set[str]:
    out: set[str] = set()
    for f in list(report.get("facts") or []) + list(extra or []):
        v = f.get("value") if isinstance(f, dict) else None
        vals = []
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)) and math.isfinite(v):
            vals.append(float(v))
        elif isinstance(v, str):
            vals += [float(x.replace(",", "")) for x in _NUM.findall(v)]
        for x in vals:
            out |= _num_forms(x)
    return out


def local_check(text: str, report: dict, lang: str = "en", extra_facts: list | None = None) -> tuple[bool, str | None, int]:
    """The contract's validator (numbers must be facts, forbidden names and phrases), used while the
    engine's briefing.check_text is missing."""
    m = FORBIDDEN_LOCAL.search(text)
    if m:
        return False, f"forbidden: {m.group(0)!r}", 0
    hours = (report.get("cost") or {}).get("duration_h_assumed")
    for d in _DURATION.finditer(text):
        if hours is None or float(d.group(1).replace(",", "")) != float(hours):
            return False, f"duration: {d.group(0)!r}", 0
    allowed = _local_allowed(report, extra_facts or [])
    toks = [t.replace(",", "").rstrip(".") for t in _NUM.findall(text)]
    for t in toks:
        norm = _dec(float(t), 2) if "." in t else t
        if t not in allowed and norm not in allowed:
            return False, f"number not in the facts: {t}", len(toks)
    return True, None, len(toks)


def check_segment(text: str, w: Writer, lang: str) -> tuple[bool, str | None, int]:
    """briefing.check_text on the text with names masked (digits in 'Fort Myers 12' are names, not
    quantities), plus the local forbidden list (it carries the Spanish phrases)."""
    masked = w.mask(text)
    m = FORBIDDEN_LOCAL.search(masked)
    if m:
        return False, f"forbidden: {m.group(0)!r}", 0
    eng = _engine()
    fn = getattr(eng, "check_text", None) if eng else None
    if fn is not None:
        try:
            return fn(masked, w.r, lang, extra_facts=w.extra)
        except TypeError:
            return fn(masked, w.r, lang)
        except Exception:  # noqa: BLE001
            log.exception("briefing.check_text failed; using the local check")
    return local_check(masked, w.r, lang, w.extra)


# -------------------------------------------------------------------------------------------- routes
_decks: "OrderedDict[str, dict]" = OrderedDict()


def remember(deck: dict) -> None:
    _decks[deck["deck_key"]] = deck
    _decks.move_to_end(deck["deck_key"])
    while len(_decks) > DECK_CACHE:
        _decks.popitem(last=False)


def deck_by_key(key: str) -> dict | None:
    """A deck this server built recently (voice.py's download uses it)."""
    return _decks.get(key)


# ------------------------------------------------------------------------------------------- Gemini
AI_SYSTEM = """You are the presenter of a narrated after-action briefing about a SIMULATED power-grid failure on a
synthetic grid model (not any real utility's network). For each slide you get its PURPOSE and its DATA (values
computed by the grid engine, with the spoken form of each number in English and Spanish). Write the presenter's
short spoken paragraph for that slide, once in English and once in Spanish: a calm, clear analyst explaining what
happened and what would fix it, in natural spoken prose (not a list, not a headline).

Rules:
- Say only what the DATA says. Write numbers exactly in their given spoken form ("about 784,000", "1,500
  megawatts"; Spanish "unas 784 mil", "1500 megavatios"). Never compute a new number: no sums, differences, ratios,
  percentages, comparisons ("three times", "half") of your own.
- Write step numbers and small counts as words ("nine steps", "nueve pasos").
- Keep every place and line name exactly as the DATA writes it, in both languages: never translate a name
  (Naples stays Naples, not Nápoles; Key West stays Key West).
- Never write "MW" or "%": say "megawatts" / "megavatios" and "percent" / "por ciento".
- Plain words a listener understands at once: "the areas without power" / "las zonas sin luz" (never "dark
  areas" / "áreas oscuras"), "keep them connected" (never "keep them on"), "lines trip" / "las líneas se disparan".
- Never name a real utility, company, agency, or storm. No dates, years, clock times or durations unless the DATA
  gives one. No advice to the public. Never sound like an emergency alert.
- Stay within each slide's max_chars. Lead with what matters most, connect cause and effect, keep it tight.
- Use the present tense, like a narrator walking the audience through a replay.
- Do not write the fixed opening ("This is a simulation...") or the fixed closing ("End of simulated
  briefing..."): the server adds them.
- The Spanish is written natively for a US Spanish-speaking audience, not translated word for word.
- Plain text only: no markdown, lists, emoji or quotation marks around the paragraph.

Answer only with JSON: {"slides": {"<slide id>": {"en": "...", "es": "..."}}}, one entry per slide id given."""

AI_PURPOSE = {
    "event": "what happened, in one breath: the trigger, where and when, how it spread, how many people lost power",
    "chain": "introduce the step-by-step chain reaction (an analyst reads the steps right after you)",
    "areas": "where the lights went out, in total, and that these are estimates",
    "hospitals": "hospitals in the dark areas would need backup power (counts only, no names)",
    "cost": "what it would cost, each figure an estimate with its assumption",
    "cause": "why it happened: the first line to fail, with and without the data center",
    "fix": "the best verified fix and what it does (an analyst lists the other fixes right after you)",
    "no_fix": "why no fix exists for most of these people, and that only rebuilding brings them back",
    "recovery": "why repair order matters, and hardening before the next storm",
    "bottom_line": "the one-sentence conclusion (the fixed closing follows you)",
}
AI_TIMEOUT_S = 10  # per socket read inside llm
AI_DEADLINE_S = 15  # the whole call
AI_CACHE = 128
_ai_cache: "OrderedDict[str, dict]" = OrderedDict()
_ai_locks: dict[str, asyncio.Lock] = {}
# Gemini's prose for the pinned hero decks (backend/demo/prerender_voice.py): the same text after a
# restart, so the pre-rendered audio still matches it
PINNED_AI = voice.PINNED_DIR / "ai_bodies.json"


def _load_pinned_ai() -> None:
    try:
        data = json.loads(PINNED_AI.read_text(encoding="utf-8")) if PINNED_AI.exists() else {}
    except (OSError, ValueError):
        data = {}
    for key, v in data.items():
        bodies = {tuple(k.split("/", 1)): text for k, text in (v.get("bodies") or {}).items()}
        _ai_cache[key] = {"bodies": bodies, "meta": v.get("meta") or {"numbers_checked": 0, "rejected": 0, "fallback": False}}


def pinned_ai_entry(report: dict, length: str) -> dict | None:
    """The cached Gemini result for a report, as prerender_voice.py writes it to ai_bodies.json."""
    hit = _ai_cache.get(f"{VERSION}|{report.get('key')}|{length}")
    if hit is None:
        return None
    return {"bodies": {f"{sid}/{lang}": text for (sid, lang), text in hit["bodies"].items()}, "meta": hit["meta"]}
_OPENING = re.compile(r"^\s*(this is a simulation|esto es una simulaci[oó]n)", re.IGNORECASE)
_CLOSING = re.compile(r"(end of (the )?simulated briefing|fin del simulacro)", re.IGNORECASE)
HOLDS_WORDS = {
    "en": ("no line", "nothing trips", "within its limit", "within their limits", "holds", "stays within", "stay within", "every line"),
    "es": ("ninguna línea", "nada se dispara", "dentro de su límite", "dentro de sus límites", "funciona", "aguanta", "se sostiene", "todas las líneas"),
}


def _clean_ai(text) -> str:
    """Plain spoken text: no markdown, one paragraph, no wrapping quotes."""
    if not isinstance(text, str):
        return ""
    text = re.sub(r"[*_#`>|]+", "", text)
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip("“”").strip()
    return text


# Slides whose presenter text stays the template even with Gemini on. The opening slide carries the
# scenario's core facts right after the fixed SIMULATION sentence; in review, flash-lite rewrote it as
# "a 1,500 megawatt data center ... leaves room for only 557 megawatts", which passes the number check
# but inverts what the numbers mean. The first thing a listener hears stays deterministic.
AI_KEEP_TEMPLATE = ("event", "toll", "fix")  # the numbers and the plans are the engine's: templates only


def ai_slots(composed: dict) -> list[dict]:
    """The presenter paragraphs Gemini may rewrite: every slide's first presenter segment."""
    out = []
    for sl in composed["slides"]:
        if sl["id"] in AI_KEEP_TEMPLATE:
            continue
        for lang in LANGS:
            segs = sl["narr"][lang]
            if not segs or segs[0]["role"] != "presenter" or not segs[0]["body"]:
                continue
            seg = segs[0]
            fixed = plain_len(seg.get("prefix", "")) + plain_len(seg.get("suffix", ""))
            tmpl = _CUE.sub("", seg["body"])
            cap_ = PRESENTER_MAX[lang] - fixed - (1 if fixed else 0)
            out.append({"id": sl["id"], "lang": lang, "template": tmpl, "max": min(cap_, max(int(len(tmpl) * 1.4) + 60, 120))})
    return out


def _both(fn, *a) -> dict:
    return {lang: fn(*a, lang) for lang in LANGS}


def ai_data(w: Writer, sid: str) -> dict:
    """The slide's content as data (no sentences to copy): engine values, names, and the spoken form of
    every number in both languages. Gemini writes the paragraph from this."""
    ev, r = w.ev, w.r
    ppl = {"en": people_say(w.people, "en"), "es": people_say(w.people, "es")}
    d: dict = {}
    if sid == "event":
        if w.preset:
            d["scenario"] = {lang: w.preset_say(lang) for lang in LANGS}
            d["hypothetical"] = True
        if w.multi:
            d["data_centers"] = {"how_many": _both(lambda lang: words(len(w.sites), lang)), "total_size": _both(lambda lang: mw_say(w.mw, lang)),
                                 "places": w.site_places[:4]}
        elif w.sites:
            d["data_center"] = {"size": _both(lambda lang: mw_say(w.mw, lang)), "size_before_a_noun": _both(lambda lang: mw_say(w.mw, lang, adj=True)),
                                "place": w.place}
            if w.room is not None and w.steps and w.room < w.mw:
                d["room_at_site_before_a_line_overloads"] = _both(lambda lang: mw_say(w.room, lang))
        if w.storm:
            d["lines_knocked_out_by_the_storm"] = num(w.storm)
        d["when"] = {lang: w.when(lang).lower() for lang in LANGS}
        d["cascade_steps"] = _both(lambda lang: words(w.steps, lang))
        d["people_without_power"] = ppl
        if w.preset or w.people > 1_000_000:
            d["share_of_state_residents"] = _both(lambda lang: share_say(ev.get("people_share_pct"), lang))
        d["still_spreading_when_model_stopped"] = bool(ev.get("capped"))
    elif sid == "chain":
        run = [x for x in w.timeline if int(x.get("n", 0)) > 0]
        d["cascade_steps"] = _both(lambda lang: words(w.steps, lang))
        d["storm_first"] = bool(w.storm)
        if run and run[0].get("lines"):
            ln = run[0]["lines"][0]
            d["first_line_to_trip"] = {lang: w.line_label(ln.get("id"), lang, fallback=ln.get("label")) for lang in LANGS}
            if ln.get("pct_before") is not None:
                d["its_loading_when_it_tripped"] = _both(lambda lang: pct_say(ln["pct_before"], lang))
        why = next((f for f in w.extra if f["key"].startswith("deck.why.")), None)
        if why and run:
            first = run[0]
            cands = [x for x in (first.get("why") or []) if isinstance(x, dict) and x.get("pct_after") == why["value"]]
            if cands:
                d["line_that_picked_up_its_power"] = {lang: w.line_label(cands[0].get("id"), lang, fallback=cands[0].get("label")) for lang in LANGS}
                d["that_line_climbs_to"] = _both(lambda lang: pct_say(why["value"], lang))
        d["note"] = "an analyst reads each step right after you: introduce the chain reaction, do not list the steps"
    elif sid == "areas":
        d["people_without_power"] = ppl
        d["share_of_state_residents"] = _both(lambda lang: share_say(ev.get("people_share_pct"), lang))
        d["hardest_hit_areas_in_order"] = [a["area"] for a in w.areas[:3]]
        d["note"] = "people counts are estimates: the load the model loses, counted as the residents it serves"
    elif sid == "hospitals":
        h = r.get("hospitals") or {}
        d["hospitals_in_the_dark_areas"] = _both(lambda lang: words(int(h.get("count") or 0), lang))
        d["note"] = "counts only, no hospital names; they would need backup power"
    elif sid == "cost":
        c = r.get("cost") or {}
        if c.get("duration_h_assumed"):
            d["assumed_outage_hours"] = num(c["duration_h_assumed"])
        for k in ("blackout_usd", "upgrade_usd", "campus_bill_usd_per_year"):
            if c.get(k):
                d[k.replace("_usd", "")] = _both(lambda lang, v=c[k]: usd_say(v, lang)[0])
        up = next((f for f in w.fixes if f.get("family") == "upgrade"), None)
        if c.get("upgrade_usd") and not (w.verdict == "preventable" and up and up.get("verdict") == "holds"):
            d["upgrade_note"] = "these upgrades only stop the cascade; they do NOT prevent the outage or reconnect people the damage cut off"
        d["note"] = "every figure is an estimate"
    elif sid == "cause":
        rc = r.get("root_cause") or {}
        line = rc.get("line") or {}
        d["cause"] = rc.get("cause")
        if rc.get("cause") == "storm":
            d["conclusion"] = "the storm's damage caused it, not the data center" + (
                "; without the data center the same people lose power" if w.sites else "")
            return d
        if line.get("id") is not None:
            d["first_line_to_fail"] = {lang: w.line_label(line.get("id"), lang, fallback=line.get("label")) for lang in LANGS}
        if rc.get("pct_with") is not None:
            d["its_loading_with_the_data_center"] = _both(lambda lang: pct_say(rc["pct_with"], lang))
        if rc.get("pct_without") is not None and w.sites:
            d["its_loading_without_the_data_center"] = _both(lambda lang: pct_say(rc["pct_without"], lang))
        if w.sites:
            d["nobody_loses_power_without_the_data_center"] = rc.get("people_without_campus") == 0
    elif sid == "fix":
        if w.best is None:
            d["room_at_site"] = _both(lambda lang: mw_say(w.room or 0, lang))
        else:
            o = w.best.get("outcome") or {}
            d["tested_by_rerunning_the_model"] = True
            d["best_fix"] = _both(lambda lang: _fix_phrase(w, w.best, lang))
            d["verdict"] = w.best.get("verdict")
            d["with_it"] = ({"en": "no line trips", "es": "ninguna línea se dispara"} if int(o.get("steps") or 0) == 0
                            else _both(lambda lang: people_say(o.get("people") or 0, lang) + (" still lose power" if lang == "en" else " siguen sin luz")))
            d["note"] = "an analyst lists the other fixes right after you"
    elif sid == "no_fix":
        nf = r.get("no_fix") or {}
        n = int(nf.get("people") or (r.get("bound") or {}).get("people") or 0)
        d["people_no_fix_can_reach"] = _both(lambda lang: people_say(n, lang))
        ran = [p_ for p_ in (nf.get("proof") or []) if isinstance(p_, dict) and p_.get("family") in FAMILY_SAY and p_.get("family") != "remove"
               and p_.get("verdict") in ("holds", "partly", "fails")]
        d["fixes_run_in_the_model"] = _both(lambda lang: join([FAMILY_SAY[p_["family"]][0 if lang == "en" else 1] for p_ in ran], lang))
        if w.sites:
            d["data_center_fixes"] = "not needed: without the data center the same people lose power (verified)"
        d["proof"] = "even with unlimited line ratings and no data center they stay dark: " + ("the storm cut the lines that serve them" if w.storm else "demand is more than the remaining lines can carry")
        d["only_rebuilding_brings_them_back"] = bool(w.storm)
    elif sid == "recovery":
        rec = r.get("recovery") or {}
        d["repair_order_matters"] = "fixing the right lines first brings the most people back per repair"
        d["how_the_order_was_checked"] = ("with each line's limit applied (verified)" if rec.get("method") == "lp"
                                          else "connections only: line limits were NOT applied, say so")
        hard = [x for x in (rec.get("hardening") or []) if isinstance(x, dict)]
        if hard:
            d["hardening"] = {"lines": num(hard[0].get("k") or 0), "people_kept_on": _both(lambda lang: people_say(hard[0].get("people_kept_on") or 0, lang))}
        d["note"] = "an analyst reads the repair waves right after you"
    elif sid == "bottom_line":
        d["verdict"] = w.verdict
        if w.best is not None and w.verdict in ("preventable", "partly"):
            d["best_fix"] = _both(lambda lang: _fix_phrase(w, w.best, lang))
        d["note"] = "one or two sentences; the fixed closing follows you"
    return d


def _prompt(w: Writer, slots: list[dict]) -> str:
    eng = _engine()
    sheet = None
    if eng is not None and callable(getattr(eng, "fact_sheet", None)):
        try:
            sheet = eng.fact_sheet(w.r, extra_facts=w.extra)
        except Exception:  # noqa: BLE001
            sheet = None
    if sheet is None:
        sheet = "\n".join(f"[{f['key']}] {f['label']}: {f['text']}" for f in list(w.r.get("facts") or []) + w.extra)
    lines = ["FACTS (from the grid engine; the only numbers you may use):", sheet, "", "SLIDES:"]
    by_id: dict[str, dict] = {}
    for sl in slots:
        by_id.setdefault(sl["id"], {})[sl["lang"]] = sl
    for sid, langs in by_id.items():
        lines.append(f"- id: {sid}")
        lines.append(f"  PURPOSE: {AI_PURPOSE.get(sid, sid)}")
        # the model runs long: it is asked for ~80 % of the real limit, which validate_ai enforces
        lines.append("  max_chars: " + ", ".join(f"{lang} {int(langs[lang]['max'] * 0.8)}" for lang in LANGS if lang in langs))
        lines.append("  DATA: " + json.dumps(ai_data(w, sid), ensure_ascii=False))
    lines.append("")
    lines.append('Write every slide now, as JSON {"slides": {"<id>": {"en": "...", "es": "..."}}}.')
    return "\n".join(lines)


_NUMWORDS = {
    "en": set("two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
              "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds "
              "twice double doubled triple tripled half halved quarter third thirds times dozen dozens".split()),
    "es": set("dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis diecisiete dieciocho "
              "diecinueve veinte treinta cuarenta cincuenta sesenta setenta ochenta noventa cien ciento cientos "
              "doble duplica triple triplica mitad tercio tercios veces docena docenas".split()),
}


# Spanish exonyms of Florida (and other) places named in the synthetic models: the names must stay as written
TRANSLATED_NAMES = {"es": ("Nápoles", "Napoles", "Cayo Hueso", "San Agustín", "Fuerte Myers", "Fuerte Lauderdale",
                           "San Petersburgo", "Nueva Orleans", "Palma de ", "Ciudad de Panamá", "Ciudad de Panama"),
                    "en": ()}


def _number_words(text: str, lang: str) -> set[str]:
    """Spelled numbers and ratio words (the digit check cannot see 'three times' or 'half')."""
    toks = set(re.findall(r"[a-záéíóúñü]+", text.lower()))
    if lang == "es":
        toks |= {t for t in toks if t.startswith("veinti")}
    return {t for t in toks if t in _NUMWORDS[lang] or t.startswith("veinti")}


def _same(a: str, b: str) -> bool:
    """A near-copy of the template (it stays labeled a template: Gemini did not write it)."""
    norm = lambda x: re.sub(r"[^a-z0-9áéíóúñü]+", " ", x.lower()).strip()  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.9


def validate_ai(w: Writer, sid: str, lang: str, text: str, limit: int, template: str | None = None) -> tuple[bool, str | None, int]:
    """One Gemini paragraph against the report: length, no repeated opening/closing, the fix verdict
    kept, the right language, and every number a fact (briefing.check_text, names masked)."""
    if not text or len(text) < 20:
        return False, "empty", 0
    if len(text) > limit:
        return False, f"too long ({len(text)} > {limit})", 0
    if _OPENING.search(text) or _CLOSING.search(text):
        return False, "repeats the fixed opening or closing", 0
    low = f" {text.lower()} "
    es_marks = sum(t in low for t in (" el ", " la ", " los ", " las ", " que ", " del ", " una ", " se ", "ción", "ñ"))
    if lang == "es" and es_marks == 0:
        return False, "not Spanish", 0
    if lang == "en" and es_marks >= 2:
        return False, "not English", 0
    known = _number_words(template or "", lang) | _number_words(json.dumps(ai_data(w, sid), ensure_ascii=False), lang)
    known |= {words(int(x), lang) for x in re.findall(r"\b\d{1,2}\b", (template or "") + json.dumps(ai_data(w, sid)))}
    known |= {words(int(x), lang, fem=True) for x in re.findall(r"\b\d{1,2}\b", (template or "") + json.dumps(ai_data(w, sid)))}
    for name in TRANSLATED_NAMES.get(lang, ()):
        if name.lower() in low:
            return False, f"a translated place name: {name}", 0
    extra_words = _number_words(text, lang) - known
    if extra_words:
        return False, f"a spelled number or ratio not in the template: {sorted(extra_words)}", 0
    if sid == "cost" and "upgrade_note" in ai_data(w, sid) and re.search(r"prevent|avoid|evit", low):
        return False, "says the upgrades prevent an outage they cannot prevent", 0
    if sid == "fix" and w.best is not None and w.best.get("verdict") == "holds":
        if not any(x in low for x in HOLDS_WORDS[lang]):
            return False, "drops the fix verdict", 0
    return check_segment(text, w, lang)


async def ai_bodies(report: dict, composed: dict, length: str) -> tuple[dict, dict]:
    """({(slide id, lang): validated Gemini paragraph}, meta). One Gemini call per report and length,
    cached; any slide or language that fails validation keeps its template."""
    key = f"{VERSION}|{report.get('key')}|{length}"
    hit = _ai_cache.get(key)
    if hit is not None:
        _ai_cache.move_to_end(key)
        return dict(hit["bodies"]), dict(hit["meta"])
    if not ai_configured():
        return {}, {"numbers_checked": 0, "rejected": 0, "fallback": True, "reason": "not configured"}
    lock = _ai_locks.setdefault(key, asyncio.Lock())
    async with lock:  # the stage prefetches; a second request for the same deck waits for the first
        hit = _ai_cache.get(key)
        if hit is not None:
            return dict(hit["bodies"]), dict(hit["meta"])
        w: Writer = composed["writer"]
        slots = ai_slots(composed)
        if not slots:
            return {}, {"numbers_checked": 0, "rejected": 0, "fallback": True, "reason": "nothing to write"}
        prompt = await run_in_threadpool(_prompt, w, slots)
        none = {"slides": {}}
        try:
            data, offline = await asyncio.wait_for(
                complete_json(prompt, system=AI_SYSTEM, fallback=none, timeout=AI_TIMEOUT_S, surface="deck"), AI_DEADLINE_S)
        except asyncio.TimeoutError:
            log.warning("briefing deck: Gemini took over %ss; templates used", AI_DEADLINE_S)
            data, offline = none, True
        if offline:
            _ai_locks.pop(key, None)
            return {}, {"numbers_checked": 0, "rejected": 0, "fallback": True, "reason": "AI unavailable"}
        got = data.get("slides") if isinstance(data, dict) else None
        got = got if isinstance(got, dict) else {}
        bodies, checked, rejected, reasons = {}, 0, 0, []
        for sl in slots:
            text = _clean_ai((got.get(sl["id"]) or {}).get(sl["lang"]) if isinstance(got.get(sl["id"]), dict) else None)
            if text and _same(text, sl["template"]):
                continue  # Gemini returned the draft unchanged: it stays labeled a template
            ok, why, n = await run_in_threadpool(validate_ai, w, sl["id"], sl["lang"], text, sl["max"], sl["template"])
            if ok:
                bodies[(sl["id"], sl["lang"])] = text
                checked += n
            else:
                rejected += 1
                reasons.append(f"{sl['id']}/{sl['lang']}: {why}")
        if reasons:
            log.warning("briefing deck: Gemini paragraphs rejected, templates used: %s", "; ".join(reasons)[:600])
        meta = {"numbers_checked": checked, "rejected": rejected, "fallback": not bodies}
        _ai_cache[key] = {"bodies": bodies, "meta": meta}
        while len(_ai_cache) > AI_CACHE:
            _ai_cache.popitem(last=False)
        _ai_locks.pop(key, None)
        return dict(bodies), dict(meta)


async def build_deck(body: DeckIn) -> tuple[dict, dict]:
    """(deck, report). ai=false: templates only, instantly. ai=true: Gemini's presenter prose where it
    passes the checks (cached per report), templates everywhere else."""
    report = await run_in_threadpool(report_for_case, body)
    if report.get("key"):
        try:
            import solutions

            solutions.kick(report["key"])  # AI-proposed plans, verified by the engine, arrive in the background
        except Exception as e:  # noqa: BLE001
            log.warning("bulletin: could not start the AI proposer: %s", e)
    composed = await run_in_threadpool(compose, report, body.length, None)
    meta = {"numbers_checked": 0, "rejected": 0, "fallback": bool(body.ai)}
    if body.ai:
        bodies, meta = await ai_bodies(report, composed, body.length)
        if bodies:
            composed = await run_in_threadpool(compose, report, body.length, bodies)
    deck = await run_in_threadpool(finish, report, composed, body.length, meta)
    remember(deck)
    return deck, report


@router.post("/api/briefing/deck")
@limiter.limit("30/minute")
async def briefing_deck(request: Request, body: DeckIn):
    deck, _ = await build_deck(body)
    return deck


@router.post("/api/bulletin")
@limiter.limit("30/minute")
async def bulletin(request: Request, body: CaseIn):
    """Legacy (the v1 bulletin card): the deck's headline and opening narration as one paragraph."""
    deck, report = await build_deck(DeckIn(**body.model_dump(), ai=True, length="short"))
    ev = deck["slides"][0]
    text = ev["headline"]["en"].rstrip(".") + ". " + " ".join(s["text"] for s in ev["narration"]["en"])
    return {"text": text, "fallback": bool(deck["ai"].get("fallback", True)),
            "facts": list(report.get("facts") or []) + deck["extra_facts"], "deck_key": deck["deck_key"]}


_load_pinned_ai()
