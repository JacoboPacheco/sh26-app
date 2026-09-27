"""Watch the story: every scenario as a narrated map documentary (CLAUDE.md -> Decisions -> SHOW, GEMINI MAX, MUTED,
LAZY, NO DEFAMATION, LOOK, IMPACT, AI SURFACES).

  GET  /api/show/episodes                      -> [{id, title, blurb, region, est_minutes}]
  POST /api/show/{episode} {region?, lang}     -> {id, status: pending|done|error, estimate_s}
  GET  /api/show/jobs/{id}                     -> {id, status, progress {step, of, text}, show?, error?}

A show is a list of scenes; each scene says where the camera goes (lat/lon bounds), what the map draws (layers: the
synthetic grid, lines drawing themselves, campuses dropping, blackouts spreading, a storm travelling, counters, a
meter, bars, a split-screen compare, chapter titles, lower thirds, a live agent trace, sourced quotes, a timeline) and
what the two voices say (presenter, analyst), each line tied to the facts its numbers come from.

1  STORYBOARDS (deterministic, no AI): one per episode, built from the engine's own results -- briefing.report_for
   (the cascade, the people hit, the areas, hospitals on backup power, the verified fixes, costs, the restoration
   plan), planner.run_plan (the siting agent), unlock's Florida study (capacity meter, upgrades, Gemini's challenge,
   the sensitivity cases, N-1; time to power when leadtimes.py is there), gridlock + negotiate + agreement (the two
   public filings, the pipeline's funnel, the overlaps, the same-station pair, the two agents' verified turns, the
   drafted terms). Every scene carries TEMPLATE lines built from the same facts: the show without Gemini.
   Timing: a layer with timing "span" places its items by `at` (0..1) over the scene's own length (the cascade's steps,
   the storm's progress along its track with sync "storm", the rebuild's waves), and a counter's `leaps` are the
   engine's own figures at those moments, so the map and the numbers move from a scene's first second to its last.
2  THE DIRECTOR (Gemini, native function calling, surface "show"): reads the storyboard through read-only tools
   (get_scene, get_fact, towns_hit, line_detail, cost_breakdown, agent_turns), drafts the narration (two lines a scene,
   presenter then analyst) and tests its drafts with check_draft, the checker itself, before it answers. Every call and
   every check is recorded in ai.trace, and each scene carries its review (self-checks, sent back, rewritten). THE
   CHECKER (Python, not Gemini): every number must be one of the scene's facts (the deck's rounding forms), spelled
   numbers too, and estimates are said as estimates; people hit are never "people without power"; no names but the
   ones the facts carry; no real utility or company outside the facts, no claim about the real grid or the real future;
   the first scene names the synthetic model (Build together: the public filings, where no line speaks for a utility
   and each utility's filed year stays its own); no "will" (the conditional instead), no blame, no alarm. The scenes
   that fail go back once with the findings; a scene that still fails keeps its template lines (labeled). A finished
   Gemini narration is saved (demo/show/, keyed by BOARD_VERSION and the code and data fingerprint) and re-checked when
   it is replayed: the same words and voice keys after a restart, so ElevenLabs renders each line once.
3  VOICE: every line is registered with voice.py (role presenter/analyst, the neighbours' text for intonation); the
   player asks /api/voice/segment {key} for audio + word timings (ElevenLabs when configured, else the browser voice,
   or captions alone while sound is off: MUTED is the default).
4  JOBS: a POST starts a background job (the unlock pattern); results are cached per (episode, region, lang), so a
   repeat POST answers "done" at once. LAZY: Florida's episodes (and Build together's filings) are the demo path; other
   states compute only when someone asks for one (collapse, boom and hurricane where a storm preset exists; strengthen
   reads a finished study only).

Everything about the grid describes the SYNTHETIC model (Breakthrough Energy / Texas A&M), never a real utility's
network; the Build together episode quotes two utilities' public filings as filed and says only what they could
coordinate. People and money are labeled estimates (the high end of their ranges, as the rest of the app).
"""

from __future__ import annotations

import asyncio
import copy
import difflib
import json
import logging
import math
import os
import re
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict

import llm
import voice
from bulletin import CHARS_PER_S, FORBIDDEN_LOCAL, TRANSLATED_NAMES, hours_say, num, people_round, usd_say, words
from grid import DEFAULT_REGION, POPULATION, REGIONS, grid_at, region_code
from limiter import limiter
from narrate import num_forms, number_words, ordinal

router = APIRouter(tags=["show"])
log = logging.getLogger("uvicorn.error")

VERSION = 1
SURFACE = "show"
LANGS = ("en", "es")
SHOW_MODEL = os.getenv("GEMINI_SHOW_MODEL", "").strip() or llm.AGENT_MODEL
NOTE = "Synthetic grid model (Breakthrough Energy / Texas A&M) — estimates, not a prediction about any real utility or project."
NOTE_FILINGS = ("Two utilities' public filings, as filed — the agents read those filings and are not the utilities; savings are rough estimates, "
                "not a prediction about any real project.")
CREDIT_URL = "https://github.com/Breakthrough-Energy/PreREISE"
PACE = 1.6  # every scene's minimum time, scaled: room for the camera flight, the draw-in and a beat of silence
CAPTION_CPS = 15.0  # the player's caption pace with sound off (characters per second)
LINE_MAX = {"en": 260, "es": 300}
CALL_TIMEOUT_S = 35  # one Gemini tool turn (socket read): the turn that drafts every scene for check_draft is a writing turn
WRITE_TIMEOUT_S = 40  # the turn that writes every scene's lines
DIRECTOR_DEADLINE_S = 180.0  # the whole director run (a background job, saved once written), then the templates stand
MODEL_TRIES = 3  # a busy (503), out-of-quota or slow model is swapped for the next in the chain, up to this many
TOOL_ROUNDS = 5
SHOWS_MAX = 40
JOBS_MAX = 64
JOB_TTL_S = 3600.0
RETRY_S = 90.0  # a show whose director failed transiently is asked again after this long
PENDING_MAX = 6  # shows being made at once (each is an engine run and a Gemini director): past this a POST answers 429
BOARD_VERSION = "6"  # bump when a storyboard changes (its facts, scenes, templates): the saved Gemini narrations are then rewritten
SAVED_DIR = os.path.join(os.path.dirname(__file__), "demo", "show")

FL_BOUNDS = [[24.4, -83.2], [31.0, -79.8]]  # the peninsula the Florida model covers

EPISODES = [
    {"id": "collapse", "title": {"en": "Five campuses, one afternoon", "es": "Cinco campus, una tarde"},
     "blurb": {"en": "Five 1 GW AI data centers switch on at once at the summer peak, and the synthetic grid collapses town by town.",
               "es": "Cinco centros de datos de IA de 1 GW se encienden a la vez en el pico del verano y la red sintética se derrumba pueblo a pueblo."},
     "region": "FL", "est_minutes": 3.2},
    {"id": "hurricane", "title": {"en": "The storm", "es": "La tormenta"},
     "blurb": {"en": "A hypothetical major hurricane crosses Florida: the lines in its path go down, the cascade follows, and the grid is rebuilt wave by wave.",
               "es": "Un huracán mayor hipotético cruza Florida: caen las líneas a su paso, sigue la cascada y la red se reconstruye por oleadas."},
     "region": "FL", "est_minutes": 3.0},
    {"id": "boom", "title": {"en": "The AI boom", "es": "El auge de la IA"},
     "blurb": {"en": "A siting agent places 5 GW of AI campuses: where they fit, where they break, and always-on against flexible campuses.",
               "es": "Un agente de ubicación coloca 5 GW de campus de IA: dónde caben, dónde fallan y campus siempre encendidos frente a flexibles."},
     "region": "FL", "est_minutes": 2.8},
    {"id": "strengthen", "title": {"en": "How many can the grid take?", "es": "¿Cuántos aguanta la red?"},
     "blurb": {"en": "The capacity meter: how many 1 GW campuses the grid carries at once today, the upgrades that add more, what they cost and how sure the answer is.",
               "es": "El medidor de capacidad: cuántos campus de 1 GW aguanta hoy la red a la vez, las mejoras que suman más, su costo y qué tan seguro es."},
     "region": "FL", "est_minutes": 3.4},
    {"id": "together", "title": {"en": "Build together", "es": "Construir juntos"},
     "blurb": {"en": "Two utilities' public construction plans across the Georgia–South Carolina border: the overlaps, the same station, two AI agents each reading one company's published plan, the drafted agreement.",
               "es": "Los planes públicos de obra de dos empresas en la frontera entre Georgia y Carolina del Sur: los solapes, la misma subestación, dos agentes de IA que leen, cada uno, el plan publicado de una empresa, y el borrador de acuerdo."},
     "region": "GA-SC", "est_minutes": 3.0},
]
EPISODE_IDS = tuple(e["id"] for e in EPISODES)
ESTIMATE_S = {"collapse": 45, "hurricane": 45, "boom": 50, "strengthen": 40, "together": 70}

GENERIC_NAMES = {"Breakthrough Energy", "Texas A&M", "A&M", "Gemini", "Gulf of Mexico", "Gulf", "Atlantic", "OpenStreetMap", "Census",
                 "Golfo de México", "Golfo", "Atlántico"}
LAYER_TYPES = ("grid", "lines", "points", "zones", "storm", "counter", "meter", "bars", "compare", "title", "lower_third", "agent", "quote", "timeline")

# the collapse episode's five load centers: hypothetical campuses, NOT any real proposal
COLLAPSE_SITES = [(26.12, -80.14), (27.95, -82.46), (28.54, -81.38), (30.33, -81.66), (26.71, -80.05)]
COLLAPSE_MW = 1000.0
STORM_PRESET = {"FL": "fl-gulf-fort-myers", "TX": "tx-gulf-landfall", "NY": "ny-noreaster-hudson"}
STORM_RADIUS_KM = 40.0  # a major storm's damage corridor, each side of the track (hurricane.py's default)
BOOM_MW = 5000.0
BOOM_SITES = 5
STUDY_MW = 1000.0


# ------------------------------------------------------------------------------------------------ small helpers
def T(lang: str, en: str, es: str) -> str:
    return en if lang == "en" else es


def half_up(x: float) -> int:
    return int(math.floor(float(x) + 0.5))


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def pct_text(p: float, lang: str) -> str:
    return f"{half_up(p)} " + T(lang, "percent", "por ciento")


def people_text(n: int, lang: str) -> str:
    r = people_round(int(n), lang)
    if lang == "en":
        return f"{r} people"
    return f"{r} de personas" if int(n) >= 999_500 else f"{r} personas"


def usd_text(v: float, lang: str) -> str:
    return usd_say(float(v), lang)[0]


def mw_text(mw: float, lang: str) -> str:
    v = half_up(mw)
    if v >= 1000 and v % 1000 == 0:
        g = v // 1000
        return f"{g} " + T(lang, "gigawatt" if g == 1 else "gigawatts", "gigavatio" if g == 1 else "gigavatios")
    return f"{num(v, lang)} " + T(lang, "megawatts", "megavatios")


def count_text(n: int, lang: str, fem: bool = False) -> str:
    """0..99 in words (a count the voice reads naturally, before its noun: 'setenta y un pares'), larger in digits."""
    if not 0 <= int(n) <= 99:
        return num(int(n), lang)
    w = words(int(n), lang, fem=fem, before_noun=True)
    return re.sub(r" ún$", " un", w)


def join_words(items: list[str], lang: str) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + T(lang, " and ", " y ") + items[-1]


def cap1(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def bounds_of(points: list[tuple[float, float]], pad: float = 0.25, min_span: float = 0.6) -> list[list[float]]:
    """[[south, west], [north, east]] around (lat, lon) points, padded, never tighter than `min_span` degrees."""
    pts = [(float(a), float(b)) for a, b in points if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    if not pts:
        return copy.deepcopy(FL_BOUNDS)
    s, n = min(p[0] for p in pts), max(p[0] for p in pts)
    w, e = min(p[1] for p in pts), max(p[1] for p in pts)
    cy, cx = (s + n) / 2, (w + e) / 2
    hy = max((n - s) / 2 + pad, min_span / 2)
    hx = max((e - w) / 2 + pad, min_span / 2)
    return [[round(cy - hy, 4), round(cx - hx, 4)], [round(cy + hy, 4), round(cx + hx, 4)]]


def region_bounds(code: str) -> list[list[float]]:
    if code == "FL":
        return copy.deepcopy(FL_BOUNDS)
    x0, y0, x1, y1 = REGIONS[code]["bbox"]
    return [[round(y0, 4), round(x0, 4)], [round(y1, 4), round(x1, 4)]]


def km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


class Geo:
    """Positions on one state model: substations by id, branches by id (at substation level)."""

    def __init__(self, g):
        self.g = g

    def sub(self, sid: int) -> tuple[float, float] | None:
        i = self.g.sub_index.get(int(sid))
        if i is None:
            return None
        return round(float(self.g.sub_lat[i]), 4), round(float(self.g.sub_lon[i]), 4)

    def branch(self, bid: int) -> dict | None:
        k = self.g.br_index.get(int(bid))
        if k is None:
            return None
        a, b = int(self.g.bus_sub_idx[self.g.f[k]]), int(self.g.bus_sub_idx[self.g.t[k]])
        pa = (round(float(self.g.sub_lat[a]), 4), round(float(self.g.sub_lon[a]), 4))
        pb = (round(float(self.g.sub_lat[b]), 4), round(float(self.g.sub_lon[b]), 4))
        return {"id": int(bid), "a": pa, "b": pb, "transformer": a == b, "kv": float(self.g.br_kv[k]),
                "from": self.g.sub_name[a], "to": self.g.sub_name[b]}

    def line_items(self, bids, labels: dict | None = None, values: dict | None = None, limit: int = 80) -> list[dict]:
        out = []
        for bid in bids:
            br = self.branch(bid)
            if br is None or br["transformer"]:
                continue
            it = {"path": [list(br["a"]), list(br["b"])]}
            if labels and bid in labels:
                it["label"] = labels[bid]
            if values and bid in values:
                it["value"] = values[bid]
            out.append(it)
            if len(out) >= limit:
                break
        return out

    def transformer_items(self, bids, labels: dict | None = None, limit: int = 20) -> list[dict]:
        out = []
        for bid in bids:
            br = self.branch(bid)
            if br is None or not br["transformer"]:
                continue
            it = {"lat": br["a"][0], "lon": br["a"][1]}
            if labels and bid in labels:
                it["label"] = labels[bid]
            out.append(it)
            if len(out) >= limit:
                break
        return out

    def zones_of(self, sub_ids, weights: bool = True) -> list[dict]:
        """The substations grouped by the town they're named after: one zone each, sized to cover them. `weight` is the
        population the zone's substations serve: the player spreads a counter's leaps by it, and never prints it (it is
        not the people without power there; the briefing's areas are)."""
        from powerflow import area_of

        groups: dict[str, list[int]] = {}
        for s in sub_ids:
            i = self.g.sub_index.get(int(s))
            if i is None:
                continue
            groups.setdefault(area_of(self.g.sub_name[i]), []).append(int(s))
        out = []
        for area, subs in groups.items():
            pts = [self.sub(s) for s in subs]
            lat = sum(p[0] for p in pts) / len(pts)
            lon = sum(p[1] for p in pts) / len(pts)
            r = max([km(lat, lon, p[0], p[1]) for p in pts] + [0.0]) + 6.0
            z = {"lat": round(lat, 4), "lon": round(lon, 4), "radius_km": round(min(r, 45.0), 1), "label": area}
            if weights:
                z["weight"] = int(self.g.zone(subs)["people_zone"])
            out.append(z)
        return out


# ------------------------------------------------------------------------------------------------ the storyboard
class Board:
    """One episode's storyboard: facts, scenes (layers + template lines), the names the lines may use, sources, and
    the payloads the director's tools read."""

    def __init__(self, episode: str, region: str, lang: str, title: str):
        self.episode, self.region, self.lang, self.title = episode, region, lang, title
        self.facts: dict[str, dict] = {}
        self.scenes: list[dict] = []
        self.names: set[str] = set()
        self.sources: list[dict] = []
        self.towns: dict[str, list[dict]] = {}  # scene id -> [{town, people}]
        self.lines_info: list[dict] = []  # line_detail's table
        self.costs: list[str] = []  # fact ids of the money facts
        self.agent_steps: list[dict] = []  # agent_turns: every agent step shown in the show
        self.extra: dict = {}
        self.first_rule = "synthetic"  # what the first scene must name ("synthetic" | "filings")
        self.names |= GENERIC_NAMES
        # regexes no line of the episode may match; the grid episodes have no filings (a director mixing episodes up)
        self.forbid: list[str] = [] if episode == "together" else [r"\bfilings?\b", r"\bdocumentos? p[uú]blicos?\b"]

    def f(self, fid: str, value, unit: str, text: str, source: str = "engine", kind: str | None = None, hedge: bool | None = None) -> str:
        """kind: 'hit' (people hit: never 'lose power' / 'without power') or 'out' (people without power); hedge: a line
        that says it must hedge it ('about', 'up to', 'estimate'): people counts by default, and the incident costs."""
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        self.facts[fid] = {"value": value, "unit": unit, "text": text, "source": source}
        if kind:
            self.facts[fid]["kind"] = kind
        if hedge if hedge is not None else unit == "people":
            self.facts[fid]["hedge"] = True
        return fid

    def name(self, *items) -> None:
        for s in items:
            if isinstance(s, str) and s.strip():
                self.names.add(s.strip())

    def src(self, name: str, url: str | None) -> None:
        if name and not any(s["name"] == name for s in self.sources):
            self.sources.append({"name": name, "url": url})

    def scene(self, sid: str, chapter: str, bounds, layers: list[dict], lines: list[tuple], min_ms: int = 7000,
              brief: str = "", points: list[str] | None = None, facts: list[str] | None = None, forbid: list[str] | None = None,
              min_km: float | None = None) -> dict:
        cam = {"bounds": bounds}
        if min_km:
            cam["min_km"] = float(min_km)  # a focal scene may close in tighter than the usual ~180 km across
        sc = {
            "id": f"{self.episode}-{len(self.scenes) + 1:02d}-{sid}",
            "chapter": chapter,
            "min_ms": int(round(min_ms * PACE / 100.0) * 100),
            "camera": cam,
            "layers": layers,
            "template": [{"speaker": sp, "text": tx, "facts": [x for x in fx if x in self.facts]} for sp, tx, fx in lines],
            "brief": brief,
            "points": points or [],
            "extra_facts": [f for f in facts or [] if f in self.facts],  # facts a line may also say (not in the template)
            "forbid": list(forbid or []),  # regexes a line of this scene must not match (e.g. 'upgrade' when upgrades failed)
        }
        self.scenes.append(sc)
        return sc


def grid_layer(mode: str) -> dict:
    return {"type": "grid", "mode": mode}


def title_layer(text: str, sub: str | None = None) -> dict:
    d = {"type": "title", "text": text}
    if sub:
        d["sub"] = sub
    return d


def lower(text: str, sub: str | None = None) -> dict:
    d = {"type": "lower_third", "text": text}
    if sub:
        d["sub"] = sub
    return d


def counter(label: str, frm, to, fmt: str, tone: str) -> dict:
    return {"type": "counter", "label": label, "from": frm, "to": to, "format": fmt, "tone": tone}


def compare(tone: str, left: tuple, right: tuple) -> dict:
    def side(t):
        d = {"label": t[0], "value": t[1]}
        if len(t) > 2 and t[2]:
            d["sub"] = t[2]
        return d
    return {"type": "compare", "tone": tone, "left": side(left), "right": side(right)}


# ------------------------------------------------------------------------------------------------ shared: incidents
def _report(body: dict) -> dict:
    import briefing

    return briefing.report_for(briefing.BriefingIn(**body))


def _dark_by_step(rep: dict) -> list[dict]:
    """Per cascade step: the branches that tripped, the substations that newly went dark (in the order the darkness
    reached them), people hit so far."""
    out = []
    for s in rep["replay"]["steps"]:
        dark = []
        for w in s.get("waves") or []:
            dark += [int(x) for x in w.get("subs") or []]
        for sid, _mw in s.get("newly_affected") or []:
            if int(sid) not in dark:
                dark.append(int(sid))
        out.append({"n": int(s["n"]), "action": s.get("action"), "tripped": [int(b) for b in s.get("tripped") or []],
                    "held": s.get("held_line"), "dark": dark, "people_hit": int(s.get("people_hit") or 0),
                    "hits": s.get("hits") or []})
    return out


def _hit_zones(geo: Geo, steps: list[dict], timed: bool = False) -> list[dict]:
    """Zones for every town that lost power over these steps, in the order they went dark. timed: each zone carries
    `at` (0..1), the moment in the scene its town went dark: the cascade step that first reached it (span_at)."""
    from powerflow import area_of

    subs, seen, first = [], set(), {}
    for si, s in enumerate(steps):
        for sid in list(s["dark"]) + [int(x) for h in s["hits"] for x in (h.get("subs") or [])]:
            if sid not in seen:
                seen.add(sid)
                subs.append(sid)
                first[sid] = si
    zones = geo.zones_of(subs)
    order, step_of = {}, {}
    for i, sid in enumerate(subs):
        j = geo.g.sub_index.get(sid)
        if j is not None:
            a = area_of(geo.g.sub_name[j])
            order.setdefault(a, i)
            step_of.setdefault(a, first[sid])
    zones.sort(key=lambda z: order.get(z["label"], 1e9))
    if timed:
        for z in zones:
            z["at"] = span_at(step_of.get(z["label"], 0), len(steps))
    return zones


def span_at(i: int, n: int) -> float:
    """Where item i of n sits in a scene's timeline, 0..1 (the player maps it onto the scene's own length)."""
    return round(i / (n - 1), 4) if n > 1 else 0.0


def leaps_by_weight(items: list[dict], frm: float, to: float, groups: int = 10) -> list[dict]:
    """A counter's leaps that follow timed items (each with `at` and a `weight`): the items in up to `groups` bunches,
    each bunch's share of the total weight added at once, the last leap landing exactly on `to`."""
    items = sorted([it for it in items if it.get("at") is not None], key=lambda it: it["at"])
    if not items:
        return [{"at": 0.0, "value": to}]
    total = sum(max(0.0, float(it.get("weight") or 0)) for it in items)
    n = len(items)
    k = max(1, min(groups, n))
    out, acc = [], 0.0
    for gi in range(k):
        lo, hi = (gi * n) // k, ((gi + 1) * n) // k
        for it in items[lo:hi]:
            acc += (max(0.0, float(it.get("weight") or 0)) / total) if total > 0 else 1.0 / n
        v = to if gi == k - 1 else frm + (to - frm) * min(1.0, acc)
        out.append({"at": items[hi - 1]["at"], "value": round(v)})
    return out


def _hospital_points(code: str, rep: dict) -> list[dict]:
    """Hospitals on backup power in this incident: positions only, never names (the briefing's rule)."""
    try:
        import hospitals

        affected = {int(k): float(v) for k, v in (rep["replay"].get("affected") or {}).items()}
        res = hospitals.status_for(code, grid_at(rep["case"]["load_factor"], code), affected)
        pts = []
        for row in res.get("backup", [])[:40]:
            lat, lon = row.get("lat"), row.get("lon")
            if lat is None or lon is None:
                continue
            pts.append({"lat": round(float(lat), 4), "lon": round(float(lon), 4)})
        return pts
    except Exception as e:  # noqa: BLE001 — the scene says the count; the dots are an extra
        log.warning("show: hospital points failed: %s", e)
        return []


def _cost(rep: dict) -> dict:
    """(hours out, blackout cost high end) for the report: the briefing's figures, else costs.py's formulas."""
    import costs

    c = rep.get("cost") or {}
    people = int(rep["event"]["people"] or 0)
    hours = float(c.get("duration_h_assumed") or costs.outage_hours(people) or 0)
    high = c.get("blackout_high_usd")
    if not high:
        lost = float(rep["event"]["lost_mw"] or 0)
        if lost > 0.5 and hours > 0:
            high = lost * hours * costs.voll_per_mwh(hours)[1]
    return {"hours": hours, "blackout_high": float(high or 0), "upgrade": c.get("upgrade_usd"), "sources": c.get("sources") or []}


# ------------------------------------------------------------------------------------------------ episode: collapse
def _load_centers(code: str, n: int) -> list[tuple[float, float]]:
    """The state's n biggest towns by model load, at least 60 km apart (another state's collapse)."""
    g = grid_at(1.0, code)
    from powerflow import area_of

    load = np.bincount(g.bus_sub_idx, weights=g.pd, minlength=len(g.sub_ids))
    by_area: dict[str, list[int]] = {}
    for i in range(len(g.sub_ids)):
        by_area.setdefault(area_of(g.sub_name[i]), []).append(i)
    ranked = sorted(by_area.items(), key=lambda kv: -float(load[kv[1]].sum()))
    out: list[tuple[float, float]] = []
    for _area, idx in ranked:
        best = max(idx, key=lambda i: float(load[i]))
        p = (float(g.sub_lat[best]), float(g.sub_lon[best]))
        if all(km(p[0], p[1], q[0], q[1]) >= 60 for q in out):
            out.append(p)
        if len(out) >= n:
            break
    return out


def build_collapse(code: str, lang: str) -> Board:
    from powerflow import area_of

    sites = COLLAPSE_SITES if code == "FL" else _load_centers(code, 5)
    body = {"region": code, "lat": sites[0][0], "lon": sites[0][1], "mw": COLLAPSE_MW,
            "sites": [{"lat": a, "lon": b, "mw": COLLAPSE_MW} for a, b in sites[1:]]}
    rep = _report(body)
    g = grid_at(rep["case"]["load_factor"], code)
    geo = Geo(g)
    state = REGIONS[code]["name"]
    B = Board("collapse", code, lang, EPISODES[0]["title"][lang])
    B.src("Breakthrough Energy / Texas A&M synthetic grid (CC-BY 4.0)", CREDIT_URL)
    B.name(state, "Gemini")
    camp = rep["case"]["sites"]
    towns = [s["sub_area"] for s in camp]
    B.name(*towns)
    n_c = len(camp)
    total_mw = sum(float(s["mw"]) for s in camp)
    B.f("campuses", n_c, "campuses", count_text(n_c, lang))
    B.f("campus_mw", COLLAPSE_MW, "MW", mw_text(COLLAPSE_MW, lang))
    B.f("total_mw", total_mw, "MW", mw_text(total_mw, lang))
    B.f("population", POPULATION.get(code), "people", people_text(POPULATION.get(code) or 0, lang), "Census Vintage 2024", hedge=False)
    B.f("peak_hour", 4, "PM", T(lang, "four in the afternoon", "las cuatro de la tarde"), "the heat-wave clock's summer peak (load level 1.0)")

    # the strain before anything trips: one steady-state solve with every campus on
    import grid as gridmod

    case_in = gridmod.CaseIn(**body)
    _g, sites_in, _t, _u = gridmod.check_case(case_in)
    extra, _hdr = gridmod._case_header(g, sites_in, [], {})
    st = g.solve(np.ones(g.m, dtype=bool), extra)
    pct = np.asarray(st.loading_pct)
    over_idx = np.flatnonzero(pct > 100.0 + 1e-6)
    over_idx = over_idx[np.argsort(-pct[over_idx])]
    hot_idx = np.flatnonzero((pct > 90.0) & (pct <= 100.0 + 1e-6))
    strain = rep.get("strain") or {}
    alone = (strain.get("grid_alone") or {}).get("peak_pct") or float(np.max(g.base.loading_pct))
    peak = float(pct.max())
    B.f("peak_alone", round(alone, 1), "%", pct_text(alone, lang))
    B.f("peak_with", round(peak, 1), "%", pct_text(peak, lang))
    B.f("lines_over", int(len(over_idx)), "lines", count_text(len(over_idx), lang, fem=True))
    B.f("lines_hot", int(len(hot_idx)), "lines", count_text(len(hot_idx), lang, fem=True))

    root = rep["root_cause"]
    rl = root["line"]
    B.name(rl["from_area"], rl["to_area"])
    B.f("root_with", root["pct_with"], "%", pct_text(root["pct_with"], lang))
    B.f("root_without", root["pct_without"], "%", pct_text(root["pct_without"], lang))
    root_label = rl["label"] if lang == "en" else _label_es(rl["label"])
    B.lines_info.append({"id": rl["id"], "label": rl["label"], "kv": rl["kv"], "pct_with": root["pct_with"], "pct_without": root["pct_without"],
                         "first_to_fail": True, "areas": [rl["from_area"], rl["to_area"]]})
    B.name(*_label_names(rl["label"]))

    steps = _dark_by_step(rep)
    ev = rep["event"]
    final_hit = int(rep["replay"]["people_hit"])
    people_out = int(ev.get("people") or 0)
    B.f("steps", int(ev["steps"]), "steps", count_text(int(ev["steps"]), lang))
    B.f("people_hit", final_hit, "people", people_text(final_hit, lang), kind="hit")
    B.f("people_out", people_out, "people", people_text(people_out, lang), kind="out")
    half = max(1, len(steps) // 3)
    part1, part2 = steps[:half], steps[half:]
    hit1 = part1[-1]["people_hit"] if part1 else 0
    B.f("people_hit_early", hit1, "people", people_text(hit1, lang), kind="hit")
    cost = _cost(rep)
    hours = half_up(cost["hours"])
    B.f("hours", hours, "hours", hours_say(cost["hours"], lang), "costs.py rule of thumb (long side)")
    B.f("blackout_usd", round(cost["blackout_high"]), "USD", usd_text(cost["blackout_high"], lang), "LBNL value of lost load, high end", hedge=True)
    B.costs += ["blackout_usd", "hours"]
    hosp = rep.get("hospitals") or {}
    n_h = int(hosp.get("count") or 0)
    if n_h:
        B.f("hospitals", n_h, "hospitals", count_text(n_h, lang))
        B.src("Hospitals: © OpenStreetMap contributors (ODbL)", "https://www.openstreetmap.org/copyright")
    areas = rep["areas"][:6]
    for a in areas:
        B.name(a["area"])
        B.f(f"area_{_slug(a['area'])}", int(a["people"]), "people", people_text(int(a["people"]), lang), kind="out")

    # scene 1: the cold open
    fl_all = region_bounds(code)
    B.scene("open", T(lang, "Cold open", "Apertura"), fl_all, [
        grid_layer("base"),
        title_layer(T(lang, "When the next AI data center plugs in, whose lights go out?", "Cuando se conecte el próximo centro de datos de IA, ¿quién se queda a oscuras?"),
                    T(lang, f"A synthetic model of {state}'s grid", f"Un modelo sintético de la red de {_state_es(state)}")),
    ], [
        ("presenter", T(lang, f"This is {state}'s power grid. Not the real one: a synthetic model, built from public data by Breakthrough Energy and Texas A&M.",
                        f"Esta es la red eléctrica de {_state_es(state)}. No la real: un modelo sintético, construido con datos públicos por Breakthrough Energy y Texas A&M."), []),
        ("analyst", T(lang, "Every line you see is carrying power. The question: what happens on this model when AI data centers plug in?",
                      "Cada línea que ven lleva energía. La pregunta: ¿qué pasa en este modelo cuando se conectan centros de datos de IA?"), []),
    ], 8000, brief="The whole state, the synthetic grid glowing faintly; the hook as a title card.",
        points=["hook: whose lights go out", "say it is a synthetic model (Breakthrough Energy / Texas A&M)"])

    # scene 2: the campuses drop
    camp_pts = [{"lat": s["sub_lat"], "lon": s["sub_lon"], "label": s["sub_area"], "sub": mw_text(s["mw"], lang)} for s in camp]
    B.scene("drop", T(lang, "Switch on", "Encendido"), bounds_of([(p["lat"], p["lon"]) for p in camp_pts], 0.6), [
        grid_layer("base"),
        {"type": "points", "kind": "campus", "animate": "drop", "stagger_ms": 450, "items": camp_pts},
        counter(T(lang, "New load", "Carga nueva"), 0, total_mw, "mw", "neutral"),
        lower(T(lang, f"{n_c} hypothetical campuses · {mw_text(COLLAPSE_MW, lang)} each", f"{n_c} campus hipotéticos · {mw_text(COLLAPSE_MW, lang)} cada uno"),
              T(lang, "Not any real proposal", "Ninguna propuesta real")),
    ], [
        ("presenter", T(lang, f"Four in the afternoon, the summer peak. {cap1(count_text(n_c, lang))} hypothetical AI campuses switch on at once, one gigawatt each, in {join_words(towns, lang)}.",
                        f"Las cuatro de la tarde, el pico del verano. {cap1(count_text(n_c, lang))} campus de IA hipotéticos se encienden a la vez, un gigavatio cada uno, en {join_words(towns, lang)}."),
         ["peak_hour", "campuses", "campus_mw"]),
        ("analyst", T(lang, f"That's {mw_text(total_mw, lang)} of new demand, arriving in a single moment.",
                      f"Son {mw_text(total_mw, lang)} de demanda nueva, en un solo instante."), ["total_mw"]),
    ], 8500, brief="Five campuses drop at five load centers; the new-load counter climbs to the total.",
        points=["hypothetical campuses, not real proposals", "all at once at the summer peak", "where they are"])

    # scene 3: the strain
    labels_over = {int(g.br_ids[i]): f"{half_up(pct[i])}%" for i in over_idx[:6]}
    over_items = geo.line_items([int(g.br_ids[i]) for i in over_idx], labels=labels_over, limit=90)
    for j, it in enumerate(over_items):  # the worst first, the whole set flashing across the state within a few seconds
        it["at"] = round(min(1.0, j / max(1, len(over_items) - 1)) * 0.35, 4)
    strain_pts = [(p["lat"], p["lon"]) for p in camp_pts] + [tuple(pt) for it in over_items for pt in it["path"]]
    B.scene("strain", T(lang, "Strain", "Tensión"), bounds_of(strain_pts, 0.25), [
        grid_layer("dim"),
        {"type": "lines", "style": "stress", "animate": "fade", "items": geo.line_items([int(g.br_ids[i]) for i in hot_idx], limit=60)},
        {"type": "lines", "style": "over", "animate": "flash", "timing": "span", "emphasis": True, "items": over_items},
        {"type": "points", "kind": "campus", "animate": "none", "items": camp_pts},
        compare("loss", (T(lang, "Busiest line, grid alone", "Línea más cargada, red sola"), f"{half_up(alone)}%"),
                (T(lang, "With the campuses", "Con los campus"), f"{half_up(peak)}%")),
    ], [
        ("presenter", T(lang, f"Watch the lines. Before the campuses, the busiest line ran at {pct_text(alone, lang)} of its rating. Now it's at {pct_text(peak, lang)}.",
                        f"Miren las líneas. Antes de los campus, la línea más cargada iba al {pct_text(alone, lang)} de su capacidad. Ahora está al {pct_text(peak, lang)}."),
         ["peak_alone", "peak_with"]),
        ("analyst", T(lang, f"{cap1(count_text(len(over_idx), lang))} lines are over their limit before anything has even failed.",
                      f"{cap1(count_text(len(over_idx), lang, fem=True))} líneas pasan su límite antes de que nada falle."), ["lines_over"]),
    ], 8000, brief="Lines near their limit fade in; the overloaded ones flash red with their loading.",
        points=["busiest line before vs after", "how many lines are over their limit (all of them flash red on the map)"])

    # scene 4: the first to fail
    rb = geo.branch(rl["id"])
    rpts = [rb["a"], rb["b"]] if rb else [(camp[0]["sub_lat"], camp[0]["sub_lon"])]
    B.scene("root", T(lang, "First to fail", "La primera en caer"), bounds_of(rpts, 0.12, 0.35), [
        grid_layer("dim"),
        {"type": "lines", "style": "over", "animate": "flash", "emphasis": True, "items": geo.line_items([rl["id"]], labels={rl["id"]: f"{half_up(root['pct_with'])}%"})},
        compare("loss", (T(lang, "Without the campuses", "Sin los campus"), f"{half_up(root['pct_without'])}%"),
                (T(lang, "With them", "Con ellos"), f"{half_up(root['pct_with'])}%")),
        lower(cap1(root_label), T(lang, "the first line to fail", "la primera línea en fallar")),
    ], [
        ("presenter", T(lang, f"The first to go is {root_label}. On its own it would carry {pct_text(root['pct_without'], lang)} of its rating. With the campuses, {pct_text(root['pct_with'], lang)}.",
                        f"La primera en caer es {root_label}. Por sí sola llevaría el {pct_text(root['pct_without'], lang)} de su capacidad. Con los campus, el {pct_text(root['pct_with'], lang)}."),
         ["root_without", "root_with"]),
        ("analyst", T(lang, "Protection relays do what they must: the line trips, and its power has to go somewhere else.",
                      "Las protecciones hacen lo que deben: la línea se desconecta y su energía tiene que ir a otra parte."), []),
    ], 8000, brief="Close on the first line to fail; the compare shows its loading with and without the campuses.",
        points=["the first line to fail and why (the campuses pushed it over)", "the relay trips it; its flow moves to its neighbors"], min_km=45)

    # scenes 5-6: the cascade in two acts
    def cascade_scene(key: str, chapter: str, part: list[dict], frm: int, to: int, lines: list[tuple], brief: str):
        # every item carries the moment of its own cascade step (`at`): the lines trip step by step across the whole
        # scene, each town goes dark right after the step that reached it, and the counter leaps with each step's
        # engine figure (people hit so far), so nothing sits still while the voice explains
        k = len(part)
        trips = []
        for j, s in enumerate(part):
            for it in geo.line_items(s["tripped"], limit=12):
                it["at"] = span_at(j, k)
                trips.append(it)
        trips = trips[:80]
        zones = _hit_zones(geo, part, timed=True)
        leaps, last = [], frm
        for j, s in enumerate(part):
            if int(s["people_hit"]) > last:
                last = int(s["people_hit"])
                leaps.append({"at": span_at(j, k), "value": last})
        if not leaps or leaps[-1]["value"] != to:
            leaps.append({"at": 1.0, "value": to})
        pts = [(z["lat"], z["lon"]) for z in zones] + [tuple(p) for it in trips[:30] for p in it["path"]]
        B.towns[f"{B.episode}-{len(B.scenes) + 1:02d}-{key}"] = [{"town": z["label"], "order": i + 1} for i, z in enumerate(zones)]
        for z in zones:
            B.name(z["label"])
        cnt = counter(T(lang, "People hit (estimate)", "Personas afectadas (estimación)"), frm, to, "people", "loss")
        cnt.update({"timing": "span", "after_ms": 450, "leaps": leaps})
        B.scene(key, chapter, bounds_of(pts or [(p["lat"], p["lon"]) for p in camp_pts], 0.3), [
            grid_layer("dim"),
            {"type": "lines", "style": "trip", "animate": "flash", "timing": "span", "items": trips},
            {"type": "zones", "style": "blackout", "animate": "spread", "timing": "span", "after_ms": 450, "items": zones},
            {"type": "points", "kind": "town", "animate": "appear", "timing": "span", "after_ms": 900,
             "items": [{"lat": z["lat"], "lon": z["lon"], "label": z["label"], "at": z["at"]} for z in zones[:14]]},
            cnt,
            {"type": "timeline", "timing": "span", "items": [{"t": s["n"], "label": T(lang, f"Step {s['n']}", f"Paso {s['n']}"), "tone": "loss", "at": span_at(j, k)} for j, s in enumerate(part)][:40]},
        ], lines, max(12000, 1000 * k + 2000), brief=brief,
            points=["the cascade: each trip pushes flow onto neighbors", "towns going dark in order", "people hit (estimate) leaps with every step",
                    "'people hit' is not 'people without power': say 'hit' or 'affected' for this counter"])

    first_towns = [z["label"] for z in _hit_zones(geo, part1)][:3]
    cascade_scene("cascade1", T(lang, "The cascade", "La cascada"), part1, 0, hit1, [
        ("presenter", T(lang, "One line trips. Its flow jumps to the lines beside it, and they overload too. Then the next, and the next.",
                        "Cae una línea. Su flujo salta a las vecinas, que también se sobrecargan. Luego la siguiente, y la siguiente."), []),
        ("analyst", T(lang, f"The first towns go dark: {join_words(first_towns, lang)}. About {people_text(hit1, lang)} hit already." if first_towns else f"About {people_text(hit1, lang)} are hit already.",
                      f"Los primeros pueblos se apagan: {join_words(first_towns, lang)}. Ya hay unas {people_text(hit1, lang)} afectadas." if first_towns else f"Ya hay unas {people_text(hit1, lang)} afectadas."),
         ["people_hit_early"]),
    ], "The first third of the cascade: lines trip one after another, towns go dark, the counter leaps.")
    cascade_scene("cascade2", T(lang, "The cascade", "La cascada"), part2, hit1, final_hit, [
        ("presenter", T(lang, f"It doesn't stop. {cap1(count_text(int(ev['steps']), lang))} steps, each one a line giving way under a load it was never built for.",
                        f"No se detiene. {cap1(count_text(int(ev['steps']), lang))} pasos, cada uno una línea que cede bajo una carga para la que no fue construida."),
         ["steps"]),
        ("analyst", T(lang, f"By the end, the blackout has reached about {people_text(final_hit, lang)}. That's an estimate, on the long side.",
                      f"Al final, el apagón ha alcanzado a unas {people_text(final_hit, lang)}. Es una estimación, del lado alto."),
         ["people_hit"]),
    ], "The rest of the cascade across the state; the counter leaps to the final people hit.")

    # scene 7: hospitals
    if n_h:
        hp = _hospital_points(code, rep)
        B.scene("hospitals", T(lang, "Who's hit", "A quién afecta"), bounds_of([(p["lat"], p["lon"]) for p in hp] or [(p["lat"], p["lon"]) for p in camp_pts], 0.4), [
            grid_layer("dim"),
            {"type": "zones", "style": "blackout", "animate": "none", "items": _hit_zones(geo, steps)},
            {"type": "points", "kind": "hospital", "animate": "pulse", "stagger_ms": 250, "items": hp},
            counter(T(lang, "Hospitals on backup power", "Hospitales con energía de respaldo"), 0, n_h, "count", "loss"),
        ], [
            ("presenter", T(lang, f"{cap1(count_text(n_h, lang))} hospitals sit where the power went out. They switch to backup generators.",
                            f"{cap1(count_text(n_h, lang))} hospitales están donde se fue la luz. Pasan a sus generadores de respaldo."), ["hospitals"]),
            ("analyst", T(lang, "Backup power is built for hours, not days. Every hour without the grid is fuel, heat and risk.",
                          "La energía de respaldo está hecha para horas, no para días. Cada hora sin la red es combustible, calor y riesgo."), []),
        ], 8000, brief="Hospitals inside the blackout pulse (positions only, no names); the counter shows how many are on backup power.",
            points=["hospitals on backup power (count, never names)", "backup is for hours"])

    # scene 8: the toll
    bars = [{"label": a["area"], "value": int(a["people"]), "tone": "loss"} for a in areas]
    B.scene("toll", T(lang, "The toll", "El saldo"), region_bounds(code), [
        grid_layer("dim"),
        {"type": "zones", "style": "blackout", "animate": "none", "items": _hit_zones(geo, steps)},
        counter(T(lang, "People hit (estimate)", "Personas afectadas (estimación)"), final_hit, final_hit, "people", "loss"),
        counter(T(lang, "Cost of the blackout (high end)", "Costo del apagón (extremo alto)"), 0, round(cost["blackout_high"]), "usd", "loss"),
        {"type": "bars", "title": T(lang, "Hardest-hit areas (people without power, estimate)", "Zonas más afectadas (personas sin luz, estimación)"), "format": "people", "items": bars},
    ], [
        ("presenter", T(lang, f"The toll: about {people_text(final_hit, lang)} hit, and the lights stay out {hours_say(cost['hours'], lang)}.",
                        f"El saldo: unas {people_text(final_hit, lang)} afectadas, y la luz no vuelve en {hours_say(cost['hours'], lang)}."), ["people_hit", "hours"]),
        ("analyst", T(lang, f"The cost of the blackout, at the high end of the estimate: {usd_text(cost['blackout_high'], lang)}. The hardest hit: {join_words([a['area'] for a in areas[:3]], lang)}.",
                      f"El costo del apagón, en el extremo alto de la estimación: {usd_text(cost['blackout_high'], lang)}. Lo más golpeado: {join_words([a['area'] for a in areas[:3]], lang)}."),
         ["blackout_usd"]),
    ], 9000, brief="The whole state with the blackout zones; counters for people and cost; bars of the hardest-hit areas.",
        points=["people hit, hours out", "cost (high end of the estimate: say 'up to')",
                "hardest-hit areas: the most people without power there (never claim who waits longest or anything the bars don't show)"])

    # scene 9: the fix search (the engine tries every family)
    fixes = rep["fixes"]
    fam_words = {
        "shrink": T(lang, "Build them smaller", "Construirlos más pequeños"), "move": T(lang, "Build elsewhere", "Construir en otro sitio"),
        "flexible": T(lang, "Make them flexible", "Hacerlos flexibles"), "time_of_day": T(lang, "Another hour", "Otra hora"),
        "upgrade": T(lang, "Upgrade the lines", "Mejorar las líneas"), "onsite": T(lang, "Generate on site", "Generar en el sitio"),
        "combo": T(lang, "Smaller plus upgrades", "Más pequeños y mejoras"), "remove": T(lang, "Don't build here", "No construir aquí"),
    }
    agent_steps = []
    for fx in fixes:
        ok = fx["verdict"] == "holds"
        agent_steps.append({"actor": "engine", "kind": "propose", "text": f"{fam_words.get(fx['family'], fx['label'])}: {fx['action']}"})
        agent_steps.append({"actor": "engine", "kind": "accept" if ok else "reject",
                            "text": T(lang, "Re-ran the cascade: it holds, nobody loses power." if ok else "Re-ran the cascade: it still fails.",
                                      "Cascada repetida: aguanta, nadie pierde la luz." if ok else "Cascada repetida: sigue fallando.")})
    B.agent_steps += agent_steps
    holds = [fx for fx in fixes if fx["verdict"] == "holds" and fx["family"] != "remove"]
    up = next((fx for fx in fixes if fx["family"] == "upgrade"), None)
    B.f("fixes_tried", len(fixes), "fix families", count_text(len(fixes), lang))
    B.f("fixes_hold", len(holds), "fix families", count_text(len(holds), lang))
    if up and up["verdict"] != "holds":
        B.f("upgrade_lines", int(up["detail"].get("lines") or 0), "lines and transformers", count_text(int(up["detail"].get("lines") or 0), lang))
    B.scene("fixes", T(lang, "The fix", "La solución"), region_bounds(code), [
        grid_layer("dim"),
        {"type": "agent", "title": T(lang, "The engine tests every fix", "El motor prueba cada solución"), "steps": agent_steps},
    ], [
        ("presenter", T(lang, f"Now the engine hunts for a fix. It tries {count_text(len(fixes), lang)} kinds, and re-runs the whole cascade for each one.",
                        f"Ahora el motor busca una solución. Prueba {count_text(len(fixes), lang, fem=True)} clases, y repite toda la cascada con cada una."), ["fixes_tried"]),
        ("analyst", (T(lang, f"Even upgrading {count_text(int(up['detail'].get('lines') or 0), lang)} lines and transformers isn't enough at full size. {cap1(count_text(len(holds), lang))} kinds of fix do hold.",
                       f"Ni siquiera mejorar {count_text(int(up['detail'].get('lines') or 0), lang, fem=True)} líneas y transformadores basta a tamaño completo. {cap1(count_text(len(holds), lang, fem=True))} clases de solución sí aguantan.")
                     if up and up["verdict"] != "holds" else
                     T(lang, f"{cap1(count_text(len(holds), lang))} kinds of fix hold.", f"{cap1(count_text(len(holds), lang, fem=True))} clases de solución aguantan.")),
         ["upgrade_lines", "fixes_hold"]),
    ], 9500, brief="A live trace: each fix family proposed and re-run through the cascade; accepted or rejected.",
        points=["the engine verifies every fix by re-running the cascade", "which fixes hold, which don't"])

    # scene 10: the full-size fix
    pref = {"upgrade": 0, "onsite": 1, "combo": 2, "flexible": 3}  # SOLUTIONS: the full size first, then the least cut
    full = min((fx for fx in fixes if fx["verdict"] == "holds" and fx["family"] in pref), key=lambda fx: pref[fx["family"]], default=None)
    small = next((fx for fx in fixes if fx["verdict"] == "holds" and fx["family"] == "shrink"), None)
    fix = full or small
    if fix:
        after = (fix.get("strain") or {}).get("peak_pct")
        if after is not None:
            B.f("peak_fixed", round(after, 1), "%", pct_text(after, lang))
        fl_layers = [grid_layer("dim")]
        if fix["family"] == "onsite":
            onsite_mw = _first_mw(fix["action"]) or float(fix["detail"].get("onsite_mw") or 0)
            B.f("onsite_mw", onsite_mw, "MW", mw_text(onsite_mw, lang))
            fl_layers.append({"type": "points", "kind": "plant", "animate": "appear", "stagger_ms": 300,
                              "items": [{"lat": p["lat"], "lon": p["lon"], "label": T(lang, "on-site power", "generación propia")} for p in camp_pts]})
            say = T(lang, f"The fix that keeps every campus at full size: {mw_text(onsite_mw, lang)} of generation on the campuses themselves.",
                    f"La solución que mantiene cada campus a tamaño completo: {mw_text(onsite_mw, lang)} de generación en los propios campus.")
            say_f = ["onsite_mw"]
        elif fix["family"] in ("upgrade", "combo"):
            lst = fix["detail"].get("list") or []
            fl_layers.append({"type": "lines", "style": "upgrade", "animate": "draw", "stagger_ms": 200, "items": geo.line_items([x["id"] for x in lst])})
            n_fix = int(fix["detail"].get("lines") or len(lst))
            B.f("fix_lines", n_fix, "lines and transformers", count_text(n_fix, lang))
            say = T(lang, f"The fix: upgrade {count_text(n_fix, lang)} lines and transformers, drawn here in green.",
                    f"La solución: mejorar {count_text(n_fix, lang, fem=True)} líneas y transformadores, aquí en verde.")
            say_f = ["fix_lines"]
        else:
            kept = float(fix.get("kept_mw") or 0)
            B.f("kept_mw", kept, "MW", mw_text(kept, lang))
            say = (T(lang, f"The fix: the campuses step down to {mw_text(kept, lang)} whenever the grid is as busy as this, and run at full power the rest of the time.",
                     f"La solución: los campus bajan a {mw_text(kept, lang)} cuando la red está tan cargada como ahora, y funcionan a plena potencia el resto del tiempo.")
                   if fix["family"] == "flexible" else
                   T(lang, f"The fix: build them smaller, {mw_text(kept, lang)} in total.", f"La solución: construirlos más pequeños, {mw_text(kept, lang)} en total."))
            say_f = ["kept_mw"]
        fl_layers += [
            {"type": "points", "kind": "campus", "animate": "none", "items": camp_pts},
            {"type": "zones", "style": "restored", "animate": "spread", "stagger_ms": 150, "items": _hit_zones(geo, steps)},
            counter(T(lang, "People hit (estimate)", "Personas afectadas (estimación)"), final_hit, 0, "people", "gain"),
        ]
        if after is not None:
            fl_layers.append(compare("gain", (T(lang, "Busiest line, before", "Línea más cargada, antes"), f"{half_up(peak)}%"),
                                     (T(lang, "With the fix", "Con la solución"), f"{half_up(after)}%")))
        B.scene("fixed", T(lang, "The fix", "La solución"), bounds_of([(p["lat"], p["lon"]) for p in camp_pts], 0.6), fl_layers, [
            ("presenter", say, say_f),
            ("analyst", T(lang, f"Run it again with the fix: the busiest line drops from {pct_text(peak, lang)} to {pct_text(after, lang)}, and nobody loses power."
                          if after is not None else "Run it again with the fix: nobody loses power.",
                          f"Repitan con la solución: la línea más cargada baja del {pct_text(peak, lang)} al {pct_text(after, lang)}, y nadie pierde la luz."
                          if after is not None else "Repitan con la solución: nadie pierde la luz."),
             ["peak_with", "peak_fixed"]),
        ], 9000, brief="The verified fix on the map (green); the blackout zones turn back; the people counter falls to zero.",
            points=["the full-size fix first (if you want to build this here, you have to do this)", "busiest line before/after", "nobody loses power"]
            + ([] if fix["family"] in ("upgrade", "combo") else ["line upgrades alone did NOT hold at full size: this fix is not an upgrade"]),
            forbid=[] if fix["family"] in ("upgrade", "combo") else [r"\bupgrad\w*", r"\bmejor(?:a|as|ar|ando)\b", r"\breforz\w*"])

    # scene 11: the hand-off to Strengthen
    cap = _study_capacity(code)
    if cap:
        B.f("today", cap["today"], "campuses", count_text(cap["today"], lang))
        B.f("headline", cap["headline"], "campuses", count_text(cap["headline"], lang))
        B.f("budget_usd", cap["budget"], "USD", usd_text(cap["budget"], lang))
        B.scene("handoff", T(lang, "What it can take", "Lo que aguanta"), region_bounds(code), [
            grid_layer("base"),
            {"type": "meter", "cells": max(cap["headline"] + 3, 10), "today": cap["today"], "filled": cap["headline"], "reserve_at": cap.get("reserve_at"),
             "label": T(lang, "1 GW campuses at once", "Campus de 1 GW a la vez")},
            title_layer(T(lang, "So how many can the grid safely take?", "Entonces, ¿cuántos puede aguantar la red?"),
                        T(lang, "Next: Strengthen the grid", "Siguiente: reforzar la red")),
        ], [
            ("presenter", T(lang, f"So how many can this grid safely take? Placed where it has room: {count_text(cap['today'], lang)} today, all at once.",
                            f"Entonces, ¿cuántos puede aguantar esta red? Ubicados donde hay espacio: {count_text(cap['today'], lang)} hoy, todos a la vez."), ["today"]),
            ("analyst", T(lang, f"And with {usd_text(cap['budget'], lang)} of upgrades, {count_text(cap['headline'], lang)}. Location is everything.",
                          f"Y con {usd_text(cap['budget'], lang)} en mejoras, {count_text(cap['headline'], lang)}. La ubicación lo es todo."), ["budget_usd", "headline"]),
        ], 8000, brief="The capacity meter fills to the headline: the hand-off to Strengthen the grid.",
            points=["the answer: how many fit today, how many with upgrades", "location matters"])
    return B


def _title(name: str) -> str:
    """'JACKSONVILLE 64' -> 'Jacksonville 64' (the model's substation names are upper case; the map writes them as towns)."""
    return " ".join(w.capitalize() if w.isalpha() else w for w in str(name or "").split())


def _first_mw(text: str) -> float:
    m = re.search(r"([\d,]+(?:\.\d+)?) MW", text or "")
    return float(m.group(1).replace(",", "")) if m else 0.0


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(s)).strip("_") or "x"


_STATE_ES = {"Florida": "Florida", "Texas": "Texas", "New York": "Nueva York", "Georgia": "Georgia", "South Carolina": "Carolina del Sur"}


def _state_es(name: str) -> str:
    try:
        from bulletin import STATE_ES

        return STATE_ES.get(name, name)
    except Exception:  # noqa: BLE001
        return _STATE_ES.get(name, name)


def _label_es(label: str) -> str:
    m = re.match(r"^the (.+) transformer(?: \(unit (\d+)\))?$", label or "")
    if m:
        return f"el transformador de {m.group(1)}" + (f" (unidad {m.group(2)})" if m.group(2) else "")
    m = re.match(r"^the (.+) to (.+) line(?: \(circuit (\d+)\))?$", label or "")
    if m:
        return f"la línea de {m.group(1)} a {m.group(2)}" + (f" (circuito {m.group(3)})" if m.group(3) else "")
    return label


def _label_names(label: str) -> list[str]:
    """The substation names inside a line label ('Orlando 6', 'Orlando 40'): names that hold digits."""
    m = re.match(r"^the (.+) to (.+) line", label or "") or re.match(r"^the (.+) transformer", label or "")
    return list(m.groups()) if m else []


# ------------------------------------------------------------------------------------------------ the study (Strengthen)
def _study(code: str) -> dict | None:
    """The finished Strengthen study for (code, 1,000 MW, today's load): unlock's cache, else the baked file. Never
    starts one (LAZY)."""
    import unlock

    key = (code, STUDY_MW, 1.0)
    with unlock._cache_lock:
        hit = unlock._cache.get(key)
    if hit is not None:
        return hit
    p = os.path.join(os.path.dirname(__file__), "demo", "strengthen", f"{code}_{int(STUDY_MW)}.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)["result"]
        except (OSError, ValueError, KeyError) as e:
            log.warning("show: baked study unreadable: %s", e)
    return None


def _study_capacity(code: str) -> dict | None:
    import capacity

    r = _study(code)
    cap = (r or {}).get("capacity") or {}
    firm = cap.get("firm")
    if not firm or not firm.get("steps"):
        return None
    head = int(capacity.headline_count(firm))
    budget = next((float(s["cum_cost"]["high"]) for s in firm["steps"] if s["n"] == head), 0.0)
    plants = cap.get("plants") or {}
    return {"today": int(firm["today"]), "headline": head, "budget": budget, "reserve_at": plants.get("campuses_firm"), "cap": cap, "result": r}


# ------------------------------------------------------------------------------------------------ episode: hurricane
def build_hurricane(code: str, lang: str) -> Board:
    import briefing

    pid = STORM_PRESET.get(code)
    if not pid or pid not in briefing.PRESETS:
        raise HTTPException(status_code=422, detail="There is no storm track for this state yet: the hurricane episode runs for Florida, Texas and New York")
    preset = briefing.PRESETS[pid]
    track = preset["tracks"][0]["points"]  # [[lon, lat], ...]
    g = grid_at(1.0, code)
    trip = briefing.corridor(g, track, STORM_RADIUS_KM, code)
    import grid as gridmod

    trip = trip[: gridmod.MAX_TRIPS]
    rep = _report({"region": code, "trip": trip, "load_factor": 1.0})
    geo = Geo(g)
    state = REGIONS[code]["name"]
    B = Board("hurricane", code, lang, EPISODES[1]["title"][lang])
    B.src("Breakthrough Energy / Texas A&M synthetic grid (CC-BY 4.0)", CREDIT_URL)
    B.name(state, "Gemini", "Gulf", "Atlantic")
    tr = [[round(p[1], 4), round(p[0], 4)] for p in track]  # [lat, lon]
    ev = rep["event"]
    steps = _dark_by_step(rep)
    final_hit = int(rep["replay"]["people_hit"])
    storm_step = steps[0] if steps and steps[0]["action"] == "storm" else None
    after_storm = steps[1:] if storm_step else steps
    hit_storm = storm_step["people_hit"] if storm_step else 0
    B.f("lines_out", len(trip), "lines", count_text(len(trip), lang, fem=True))
    B.f("radius_km", STORM_RADIUS_KM, "km", f"{half_up(STORM_RADIUS_KM)} " + T(lang, "kilometers", "kilómetros"))
    people_out = int(ev.get("people") or 0)
    B.f("people_hit", final_hit, "people", people_text(final_hit, lang), kind="hit")
    B.f("people_storm", hit_storm, "people", people_text(hit_storm, lang), kind="hit")
    B.f("people_out", people_out, "people", people_text(people_out, lang), kind="out")
    B.f("steps", len(after_storm), "steps", count_text(len(after_storm), lang))
    B.f("population", POPULATION.get(code), "people", people_text(POPULATION.get(code) or 0, lang), "Census Vintage 2024", hedge=False)
    share = ev.get("people_share_pct")
    if share is not None:
        B.f("share_pct", share, "%", pct_text(share, lang))
    cost = _cost(rep)
    B.f("hours", half_up(cost["hours"]), "hours", hours_say(cost["hours"], lang), "costs.py rule of thumb (long side)")
    B.f("blackout_usd", round(cost["blackout_high"]), "USD", usd_text(cost["blackout_high"], lang), "LBNL value of lost load, high end", hedge=True)
    B.costs += ["blackout_usd", "hours"]
    hosp = rep.get("hospitals") or {}
    n_h = int(hosp.get("count") or 0)
    if n_h:
        B.f("hospitals", n_h, "hospitals", count_text(n_h, lang))
        B.src("Hospitals: © OpenStreetMap contributors (ODbL)", "https://www.openstreetmap.org/copyright")
    for a in rep["areas"][:6]:
        B.name(a["area"])

    # how far along the track the storm is when it reaches a place (km), and the whole track's length
    track_km = sum(km(a0, b0, a1, b1) for (a0, b0), (a1, b1) in zip(tr[:-1], tr[1:])) or 1.0

    def along_pt(lat: float, lon: float) -> float:
        best, acc, bi = 1e18, 0.0, 0.0
        for (a0, b0), (a1, b1) in zip(tr[:-1], tr[1:]):
            seg = km(a0, b0, a1, b1)
            for t in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
                la, lo = a0 + (a1 - a0) * t, b0 + (b1 - b0) * t
                d = km(la, lo, lat, lon)
                if d < best:
                    best, bi = d, acc + seg * t
            acc += seg
        return bi

    def along(bid: int) -> float:
        br = geo.branch(bid)
        if not br:
            return 1e9
        return along_pt((br["a"][0] + br["b"][0]) / 2, (br["a"][1] + br["b"][1]) / 2)

    def reach(d_km: float) -> float:
        """The storm's progress (0..1 of its track) when its winds reach a point this far along it."""
        return round(max(0.0, min(1.0, (d_km - 0.6 * STORM_RADIUS_KM) / track_km)), 4)

    trip_sorted = sorted(trip, key=along)
    fl_all = region_bounds(code)
    land = tr[0]
    B.scene("open", T(lang, "Cold open", "Apertura"), fl_all, [
        grid_layer("base"),
        title_layer(T(lang, "The storm", "La tormenta"), T(lang, f"A hypothetical major hurricane over a synthetic model of {state}'s grid",
                                                              f"Un huracán mayor hipotético sobre un modelo sintético de la red de {_state_es(state)}")),
    ], [
        ("presenter", T(lang, f"A hypothetical major hurricane, heading for {state}. The grid it meets is a synthetic model, built from public data by Breakthrough Energy and Texas A&M.",
                        f"Un huracán mayor hipotético, rumbo a {_state_es(state)}. La red que encuentra es un modelo sintético, construido con datos públicos por Breakthrough Energy y Texas A&M."), []),
        ("analyst", T(lang, "No real storm, no real utility. Just the physics of what wind does to wires.",
                      "Ninguna tormenta real, ninguna empresa real. Solo la física de lo que el viento le hace a los cables."), []),
    ], 8000, brief="The state at rest; a title card for a hypothetical storm.", points=["hypothetical storm, never a named one", "synthetic model"])

    # each line goes down the moment the storm's winds reach it: its `at` is the storm's progress there, and the
    # player plays the lines on the storm's own clock (sync), so nothing breaks ahead of the storm
    corridor_lines = []
    for bid in trip_sorted:
        for it in geo.line_items([bid]):
            it["at"] = reach(along(bid))
            it["weight"] = 1
            corridor_lines.append(it)
    corridor_lines = corridor_lines[:400]
    down = counter(T(lang, "Lines down", "Líneas caídas"), 0, len(trip), "count", "loss")
    down.update({"timing": "span", "sync": "storm", "leaps": leaps_by_weight(corridor_lines, 0, len(trip), 12)})
    B.scene("landfall", T(lang, "Landfall", "Toca tierra"), bounds_of([(p[0], p[1]) for p in tr], 0.8), [
        grid_layer("base"),
        {"type": "storm", "track": tr, "radius_km": STORM_RADIUS_KM, "animate": "travel", "timing": "span"},
        {"type": "lines", "style": "trip", "animate": "flash", "timing": "span", "sync": "storm", "items": corridor_lines},
        down,
        lower(T(lang, "Damage corridor", "Corredor de daños"), T(lang, f"{half_up(STORM_RADIUS_KM)} km each side of the track", f"{half_up(STORM_RADIUS_KM)} km a cada lado de la trayectoria")),
    ], [
        ("presenter", T(lang, "It comes ashore from the Gulf and crosses the peninsula. Everything within reach of its winds is in danger." if code == "FL" else "It comes ashore and moves inland. Everything within reach of its winds is in danger.",
                        "Toca tierra desde el Golfo y cruza la península. Todo lo que alcanzan sus vientos está en peligro." if code == "FL" else "Toca tierra y avanza tierra adentro. Todo lo que alcanzan sus vientos está en peligro."), []),
        ("analyst", T(lang, f"Within {half_up(STORM_RADIUS_KM)} kilometers of its path, {count_text(len(trip), lang)} lines come down.",
                      f"A menos de {half_up(STORM_RADIUS_KM)} kilómetros de su paso caen {count_text(len(trip), lang, fem=True)} líneas."), ["radius_km", "lines_out"]),
    ], 11000, brief="The storm travels along its track; the lines in its corridor flash and go down the moment it reaches them; the lines-down counter leaps with them.",
        points=["the storm's path", "how many lines go down in the corridor (within this distance of the track)", "no claims about how fast (no hours or minutes) or how important the lines are"],
        forbid=[r"\bcritical\b", r"\bcr[ií]tic\w*", r"\bmatter of\b", r"\b(hours?|minutes?|horas?|minutos?)\b", r"\binstant\w*", r"\bimmediate\w*", r"\bat once\b",
                r"\bde inmediato\b", r"\binstant[aá]ne\w*", r"\ba la vez\b"])

    z_storm = _hit_zones(geo, [storm_step]) if storm_step else []
    for z in z_storm:
        z["at"] = round(min(1.0, along_pt(z["lat"], z["lon"]) / track_km), 4)  # the towns go dark in the order the storm passed them
        B.name(z["label"])
    z_storm.sort(key=lambda z: z["at"])
    B.towns[f"hurricane-{len(B.scenes) + 1:02d}-dark"] = [{"town": z["label"], "order": i + 1} for i, z in enumerate(z_storm)]
    hit_c = counter(T(lang, "People hit (estimate)", "Personas afectadas (estimación)"), 0, hit_storm, "people", "loss")
    hit_c.update({"timing": "span", "after_ms": 450, "leaps": leaps_by_weight(z_storm, 0, hit_storm, 10)})
    B.scene("dark", T(lang, "Lights out", "Apagón"), bounds_of([(z["lat"], z["lon"]) for z in z_storm] or [(p[0], p[1]) for p in tr], 0.4), [
        grid_layer("dim"),
        {"type": "storm", "track": tr, "radius_km": STORM_RADIUS_KM, "animate": "none"},
        {"type": "zones", "style": "blackout", "animate": "spread", "timing": "span", "items": z_storm},
        hit_c,
    ], [
        ("presenter", T(lang, f"The towns in its path go dark first. About {people_text(hit_storm, lang)} are hit by the storm's damage alone.",
                        f"Los pueblos a su paso se apagan primero. Unas {people_text(hit_storm, lang)} resultan afectadas solo por los daños de la tormenta."), ["people_storm"]),
    ], 9000, brief="Blackout zones spread along the damage corridor in the order the storm passed; the people-hit counter leaps.",
        points=["people HIT by the storm's damage alone (an estimate): 'hit' or 'affected', never 'lose power' or 'cut off' for this number"])

    k_cas = len(after_storm)
    trips = []
    for j, s_ in enumerate(after_storm):
        for it in geo.line_items(s_["tripped"], limit=20):
            it["at"] = span_at(j, k_cas)
            trips.append(it)
    z_cas = _hit_zones(geo, after_storm, timed=True)
    for z in z_cas:
        B.name(z["label"])
    B.towns[f"hurricane-{len(B.scenes) + 1:02d}-cascade"] = [{"town": z["label"], "order": i + 1} for i, z in enumerate(z_cas)]
    leaps, last = [], hit_storm
    for j, s_ in enumerate(after_storm):
        if int(s_["people_hit"]) > last:
            last = int(s_["people_hit"])
            leaps.append({"at": span_at(j, k_cas), "value": last})
    if not leaps or leaps[-1]["value"] != final_hit:
        leaps.append({"at": 1.0, "value": final_hit})
    cas_c = counter(T(lang, "People hit (estimate)", "Personas afectadas (estimación)"), hit_storm, final_hit, "people", "loss")
    cas_c.update({"timing": "span", "after_ms": 450, "leaps": leaps})
    B.scene("cascade", T(lang, "The cascade", "La cascada"), region_bounds(code), [
        grid_layer("dim"),
        {"type": "lines", "style": "trip", "animate": "flash", "timing": "span", "items": trips[:120]},
        {"type": "zones", "style": "blackout", "animate": "spread", "timing": "span", "after_ms": 450, "items": z_cas},
        cas_c,
        {"type": "timeline", "timing": "span", "items": [{"t": s_["n"], "label": T(lang, f"Step {s_['n']}", f"Paso {s_['n']}"), "tone": "loss", "at": span_at(j, k_cas)} for j, s_ in enumerate(after_storm)]},
    ], [
        ("presenter", T(lang, f"Then the grid itself gives way. With so many lines gone, the survivors overload: {count_text(len(after_storm), lang)} more steps of cascade.",
                        f"Luego cede la propia red. Con tantas líneas caídas, las que quedan se sobrecargan: {count_text(len(after_storm), lang)} pasos más de cascada."), ["steps"]),
        ("analyst", T(lang, f"In the end, about {people_text(final_hit, lang)} are hit, and about {people_text(people_out, lang)} are left without power. Estimates, on the long side.",
                      f"Al final, unas {people_text(final_hit, lang)} resultan afectadas, y unas {people_text(people_out, lang)} quedan sin luz. Estimaciones, del lado alto."), ["people_hit", "people_out"]),
    ], max(9000, 1500 * max(k_cas, 1) + 2500), brief="The cascade after the storm: more lines trip step by step, more zones go dark, the counter leaps with every step.",
        points=["the survivors overload", "people HIT (estimate) is the counter; people WITHOUT POWER is a smaller number: never say the hit number 'lose power'"])

    if n_h:
        hp = _hospital_points(code, rep)
        B.scene("hospitals", T(lang, "Who's hit", "A quién afecta"), bounds_of([(p["lat"], p["lon"]) for p in hp] or [(p[0], p[1]) for p in tr], 0.3), [
            grid_layer("dim"),
            {"type": "zones", "style": "blackout", "animate": "none", "items": _hit_zones(geo, steps)},
            {"type": "points", "kind": "hospital", "animate": "pulse", "stagger_ms": 150, "items": hp},
            counter(T(lang, "Hospitals on backup power", "Hospitales con energía de respaldo"), 0, n_h, "count", "loss"),
        ], [
            ("presenter", T(lang, f"{cap1(count_text(n_h, lang))} hospitals are inside the blackout, running on backup generators.",
                            f"{cap1(count_text(n_h, lang))} hospitales quedan dentro del apagón, funcionando con generadores de respaldo."), ["hospitals"]),
        ], 7000, brief="Hospitals inside the blackout pulse (positions only).", points=["hospitals on backup power (count, never names)"])

    areas = rep["areas"][:6]
    for a in areas:
        B.f(f"area_{_slug(a['area'])}", int(a["people"]), "people", people_text(int(a["people"]), lang), kind="out")
    B.scene("toll", T(lang, "The toll", "El saldo"), region_bounds(code), [
        grid_layer("dim"),
        {"type": "zones", "style": "blackout", "animate": "none", "items": _hit_zones(geo, steps)},
        counter(T(lang, "People without power (estimate)", "Personas sin luz (estimación)"), 0, people_out, "people", "loss"),
        counter(T(lang, "Cost of the blackout (high end)", "Costo del apagón (extremo alto)"), 0, round(cost["blackout_high"]), "usd", "loss"),
        {"type": "bars", "title": T(lang, "Hardest-hit areas (people without power, estimate)", "Zonas más afectadas (personas sin luz, estimación)"), "format": "people",
         "items": [{"label": a["area"], "value": int(a["people"]), "tone": "loss"} for a in areas]},
    ], [
        ("presenter", T(lang, f"The lights stay out {hours_say(cost['hours'], lang)}, and the blackout costs up to {usd_text(cost['blackout_high'], lang)}.",
                        f"La luz no vuelve en {hours_say(cost['hours'], lang)}, y el apagón cuesta hasta {usd_text(cost['blackout_high'], lang)}."), ["hours", "blackout_usd"]),
        ("analyst", T(lang, f"About {people_text(people_out, lang)} are left without power. The hardest hit: {join_words([a['area'] for a in areas[:3]], lang)}.",
                      f"Unas {people_text(people_out, lang)} quedan sin luz. Lo más golpeado: {join_words([a['area'] for a in areas[:3]], lang)}."), ["people_out"]),
    ], 9000, brief="The toll: people without power, the cost and the hardest-hit areas (their bars: people without power there).",
        points=["hours out", "cost (high end: say 'up to')", "people WITHOUT POWER (this number, not the people hit)", "hardest-hit areas"])

    nf = rep.get("no_fix")
    bound = rep.get("bound") or {}
    if nf:
        B.f("nofix_people", int(bound.get("people") or 0), "people", people_text(int(bound.get("people") or 0), lang), kind="out")
        B.f("nofix_share", nf.get("share_pct"), "%", pct_text(nf.get("share_pct") or 0, lang))
        B.scene("nofix", T(lang, "No fix exists", "No hay solución"), region_bounds(code), [
            grid_layer("dim"),
            {"type": "zones", "style": "blackout", "animate": "none", "items": _hit_zones(geo, [storm_step] if storm_step else steps)},
            {"type": "quote", "text": " ".join(re.split(r"(?<=[.!?])\s+", nf["sentence"])[:2]) if lang == "en" else "Ninguna mejora de líneas lo evita: el daño corta a esta gente de las centrales que podrían abastecerla. Solo reconstruir las líneas caídas la trae de vuelta.",
             "source": T(lang, "The engine, with unlimited line capacity", "El motor, con capacidad de líneas ilimitada")},
        ], [
            ("presenter", T(lang, f"Could anything have stopped it? The engine checked. Even with unlimited line capacity, about {people_text(int(bound.get('people') or 0), lang)} stay cut off.",
                            f"¿Algo podría haberlo evitado? El motor lo comprobó. Aun con capacidad de líneas ilimitada, unas {people_text(int(bound.get('people') or 0), lang)} siguen aisladas."), ["nofix_people"]),
            ("analyst", T(lang, "No upgrade fixes a line that's lying on the ground. Only rebuilding brings those people back.",
                          "Ninguna mejora arregla una línea que está en el suelo. Solo reconstruir devuelve la luz a esa gente."), []),
        ], 8500, brief="An honest verdict: no fix exists for most of the outage; the storm's damage itself cuts people off.",
            points=["no fix exists (computed by the engine on the model, not assumed: 'the model shows', never 'proves')", "only rebuilding brings them back"])

    rec = rep.get("recovery")
    if rec and rec.get("waves"):
        waves = rec["waves"]
        nw = len(waves)
        items, tl, wave_of_area = [], [], {}
        for wi, w in enumerate(waves):
            tl.append({"t": w["n"], "label": T(lang, f"Wave {w['n']}: {w['lines_total']} lines", f"Oleada {w['n']}: {w['lines_total']} líneas"), "tone": "gain", "at": span_at(wi, nw)})
            for a in (w.get("areas_relit") or []):
                wave_of_area.setdefault(a, wi)
            for a in (w.get("areas_relit") or [])[:4]:
                B.name(a)
            for it in geo.line_items(w.get("lines") or [], limit=60):  # each wave's lines are redrawn when its turn comes
                it["at"] = span_at(wi, nw)
                items.append(it)
        items = items[:160]
        relit_zones = _hit_zones(geo, steps)
        for z in relit_zones:
            z["at"] = span_at(wave_of_area.get(z["label"], nw - 1), nw)
        first = waves[0]
        B.f("wave1_lines", int(first["lines_total"]), "lines", count_text(int(first["lines_total"]), lang, fem=True))
        B.f("wave1_back", int(first.get("people_back") or 0), "people", people_text(int(first.get("people_back") or 0), lang))
        B.f("damaged", int(rec.get("damaged_lines") or 0), "lines", count_text(int(rec.get("damaged_lines") or 0), lang, fem=True))
        base = rec.get("baseline") or {}
        if base.get("plan_better_by"):
            B.f("better_by", int(base["plan_better_by"]), "people", people_text(int(base["plan_better_by"]), lang))
        relit1 = (first.get("areas_relit") or [])[:3]
        back_c = counter(T(lang, "People back (first wave)", "Personas con luz (primera oleada)"), 0, int(first.get("people_back") or 0), "people", "gain")
        back_c.update({"timing": "span", "after_ms": 900, "leaps": [{"at": 0.0, "value": int(first.get("people_back") or 0)}]})
        B.scene("rebuild", T(lang, "The rebuild", "La reconstrucción"), region_bounds(code), [
            grid_layer("dim"),
            {"type": "lines", "style": "upgrade", "animate": "draw", "timing": "span", "items": items},
            {"type": "zones", "style": "restored", "animate": "spread", "timing": "span", "after_ms": 700, "items": relit_zones},
            back_c,
            {"type": "timeline", "timing": "span", "items": tl},
        ], [
            ("presenter", T(lang, f"Now the way back. The engine orders the repairs: {count_text(int(first['lines_total']), lang)} lines first, and about {people_text(int(first.get('people_back') or 0), lang)} get power back"
                            + (f", in {join_words(relit1, lang)}." if relit1 else "."),
                            f"Ahora, el regreso. El motor ordena las reparaciones: primero {count_text(int(first['lines_total']), lang, fem=True)} líneas, y unas {people_text(int(first.get('people_back') or 0), lang)} recuperan la luz"
                            + (f", en {join_words(relit1, lang)}." if relit1 else ".")), ["wave1_lines", "wave1_back"]),
            ("analyst", (T(lang, f"Done in this order, about {people_text(int(base['plan_better_by']), lang)} more are back early than fixing the biggest lines first.",
                           f"En este orden, unas {people_text(int(base['plan_better_by']), lang)} más vuelven antes que arreglando primero las líneas más grandes.")
                         if base.get("plan_better_by") else T(lang, "Every wave is checked: no line over its limit as the power comes back.", "Cada oleada se comprueba: ninguna línea pasa su límite al volver la energía.")),
             ["better_by"]),
        ], 10000, brief="The restoration plan: lines redrawn green wave by wave; zones light back up; a timeline of waves.",
            points=["the order of repairs, verified wave by wave", "people back in the first wave", "better than biggest-first"])

    hard = (rec or {}).get("hardening") or []
    if hard:
        h0 = hard[0]
        B.f("harden_k", int(h0["k"]), "lines", count_text(int(h0["k"]), lang, fem=True))
        B.f("harden_kept", int(h0["people_kept_on"]), "people", people_text(int(h0["people_kept_on"]), lang))
        B.scene("harden", T(lang, "Before the next one", "Antes de la próxima"), bounds_of([(p[0], p[1]) for p in tr], 0.6), [
            grid_layer("dim"),
            {"type": "lines", "style": "upgrade", "animate": "draw", "stagger_ms": 300, "items": geo.line_items(h0["lines"])},
            counter(T(lang, "People kept on", "Personas que mantienen la luz"), 0, int(h0["people_kept_on"]), "people", "gain"),
        ], [
            ("presenter", T(lang, f"And before the next storm: harden just {count_text(int(h0['k']), lang)} lines in its path, and about {people_text(int(h0['people_kept_on']), lang)} keep their power.",
                            f"Y antes de la próxima tormenta: reforzar solo {count_text(int(h0['k']), lang, fem=True)} líneas en su camino, y unas {people_text(int(h0['people_kept_on']), lang)} conservan la luz."),
             ["harden_k", "harden_kept"]),
            ("analyst", T(lang, "That's the lesson of the model: a few well-chosen lines matter more than many.",
                          "Esa es la lección del modelo: unas pocas líneas bien elegidas importan más que muchas."), []),
        ], 8500, brief="The few storm lines worth hardening, drawn green; the people they would keep on.", points=["harden the most valuable lines", "people kept on"])
    return B


# ------------------------------------------------------------------------------------------------ episode: boom
async def build_boom(code: str, lang: str, progress) -> Board:
    import planner

    req = planner.PlanIn(region=code, total_mw=BOOM_MW, max_sites=BOOM_SITES, use_ai=llm.configured())
    await run_in_threadpool(planner._validate, req)
    progress(T(lang, "The siting agent is placing the campuses", "El agente de ubicación coloca los campus"))
    res = await planner.run_plan(req, [], planner.AI_DEADLINE_S)
    return await run_in_threadpool(_boom_board, code, lang, res)


def _boom_board(code: str, lang: str, res: dict) -> Board:
    import grid as gridmod

    g = grid_at(1.0, code)
    geo = Geo(g)
    state = REGIONS[code]["name"]
    B = Board("boom", code, lang, EPISODES[2]["title"][lang])
    B.src("Breakthrough Energy / Texas A&M synthetic grid (CC-BY 4.0)", CREDIT_URL)
    B.name(state, "Gemini")
    plan = res["plan"]
    ver = res["verification"]
    by_ai = res.get("by") == "gemini"
    sites = plan["sites"]
    for s in sites:
        B.name(s.get("town"), s.get("name"))
    total = float(plan["total_mw"])
    B.f("total_mw", total, "MW", mw_text(total, lang))
    B.f("sites", len(sites), "sites", count_text(len(sites), lang))
    B.f("busiest_pct", ver.get("busiest_pct"), "%", pct_text(ver.get("busiest_pct") or 0, lang))
    n_up = len(plan.get("upgrade_lines") or [])
    B.f("upgrades", n_up, "upgrades", count_text(n_up, lang, fem=True))
    wo = res.get("without_upgrades") or {}
    wo_mw = float(wo.get("total_mw") or 0)
    if wo_mw:
        B.f("without_mw", wo_mw, "MW", mw_text(wo_mw, lang))
    steps = res.get("steps") or []
    agent = []
    for s in steps:
        actor = "gemini" if s.get("by") == "gemini" else "engine"
        kind = {"headroom_top": "tool", "whatif": "tool", "cascade": "tool", "fix": "tool", "finish": "propose"}.get(s.get("tool"), "tool")
        if s.get("ok") is False:
            kind = "reject"
        txt = (s.get("thought") + " → " if s.get("thought") else "") + str(s.get("result") or s.get("text") or "")
        agent.append({"actor": actor, "kind": kind, "text": txt[:240]})
    agent.append({"actor": "engine", "kind": "verify" if ver.get("ok") else "reject",
                  "text": T(lang, f"Checked the whole plan at once: {'no line over its limit' if ver.get('lines_over') == 0 else str(ver.get('lines_over')) + ' lines over'}, busiest line {half_up(ver.get('busiest_pct') or 0)}%, {ver.get('people', 0)} people without power.",
                            f"Plan completo comprobado a la vez: {'ninguna línea sobre su límite' if ver.get('lines_over') == 0 else str(ver.get('lines_over')) + ' líneas sobre su límite'}, línea más cargada al {half_up(ver.get('busiest_pct') or 0)}%, {ver.get('people', 0)} personas sin luz.")})
    if ver.get("ok"):
        agent.append({"actor": "engine", "kind": "accept", "text": T(lang, "Plan accepted.", "Plan aceptado.")})
    B.agent_steps += agent
    B.f("agent_steps", len(agent), "steps", count_text(len(agent), lang))  # the steps the panel shows
    fl_all = region_bounds(code)
    B.scene("open", T(lang, "Cold open", "Apertura"), fl_all, [
        grid_layer("base"),
        title_layer(T(lang, "The AI boom", "El auge de la IA"), T(lang, f"{mw_text(total, lang)} of AI campuses on a synthetic model of {state}'s grid",
                                                                    f"{mw_text(total, lang)} de campus de IA en un modelo sintético de la red de {_state_es(state)}")),
        counter(T(lang, "New AI load", "Nueva carga de IA"), 0, total, "mw", "neutral"),
    ], [
        ("presenter", T(lang, f"The AI boom wants power, and a lot of it: {mw_text(total, lang)} of new campuses. Where can a grid like this take them? This is a synthetic model of {state}'s grid.",
                        f"El auge de la IA quiere energía, y mucha: {mw_text(total, lang)} de campus nuevos. ¿Dónde puede recibirlos una red así? Este es un modelo sintético de la red de {_state_es(state)}."), ["total_mw"]),
        ("analyst", T(lang, "Instead of guessing, we hand the question to an agent, and let the physics grade its answer.",
                      "En vez de adivinar, le damos la pregunta a un agente, y dejamos que la física califique su respuesta."), []),
    ], 10000, brief="Title card; the new-load counter climbs to the total.", points=["how much new AI load", "an agent places it; the engine grades it", "synthetic model"])

    top = next((s for s in steps if s.get("tool") == "headroom_top" and s.get("sites")), None)
    cand = (top or {}).get("sites") or []
    for c in cand:
        B.name(c.get("town"), c.get("name"))
    if cand:
        B.f("room_top", int(cand[0]["room_mw"]), "MW", mw_text(int(cand[0]["room_mw"]), lang))
    tied = len(cand) > 1 and len({int(c["room_mw"]) for c in cand[:8]}) == 1  # equal bars read as a bug: say the tie instead
    room_layers = [
        grid_layer("base"),
        {"type": "points", "kind": "station", "animate": "appear", "stagger_ms": 380, "items": [{"lat": c["lat"], "lon": c["lon"], "label": c.get("town"), "sub": f"{num(c['room_mw'], lang)} MW"} for c in cand]},
    ]
    if cand and tied:
        room_layers.append(lower(T(lang, f"The top {len(cand)} sites tie: {num(int(cand[0]['room_mw']), lang)} MW each", f"Los {len(cand)} mejores sitios empatan: {num(int(cand[0]['room_mw']), 'es')} MW cada uno"),
                                 T(lang, "Room before the first overload, each site tested alone", "Espacio antes de la primera sobrecarga, cada sitio probado solo")))
    elif cand:
        room_layers.append({"type": "bars", "title": T(lang, "Room before the first overload (MW, each site alone)", "Espacio antes de la primera sobrecarga (MW, cada sitio solo)"), "format": "mw",
                            "items": [{"label": c.get("town"), "value": int(c["room_mw"]), "tone": "neutral"} for c in cand[:8]]})
    B.scene("room", T(lang, "Where there's room", "Dónde hay espacio"), bounds_of([(c["lat"], c["lon"]) for c in cand] or [(s["lat"], s["lon"]) for s in sites], 0.5), room_layers, [
        ("presenter", T(lang, "Step one: the agent asks the engine where the grid has room. Each substation is tested alone, until its first line would overload.",
                        "Primer paso: el agente le pregunta al motor dónde tiene espacio la red. Cada subestación se prueba sola, hasta que su primera línea se sobrecargaría."), []),
        ("analyst", T(lang, (f"The roomiest spots tie: about {mw_text(int(cand[0]['room_mw']), lang)} each, on their own." if tied else f"The roomiest spots take about {mw_text(int(cand[0]['room_mw']), lang)} each, on their own.") if cand else "Some spots have far more room than others.",
                      (f"Los sitios con más espacio empatan: unos {mw_text(int(cand[0]['room_mw']), lang)} cada uno, por sí solos." if tied else f"Los sitios con más espacio aceptan unos {mw_text(int(cand[0]['room_mw']), lang)} cada uno, por sí solos.") if cand else "Algunos sitios tienen mucho más espacio que otros."), ["room_top"]),
    ], 8000, brief="Candidate substations appear one by one with their room.", points=["the agent's first tool call: headroom by substation", "room is per site, tested alone (sites that share lines have less room together)"])

    B.scene("agent", T(lang, "The agent works", "El agente trabaja"), bounds_of([(s["lat"], s["lon"]) for s in sites], 0.6), [
        grid_layer("dim"),
        {"type": "agent", "title": T(lang, "Gemini siting agent" if by_ai else "Siting planner (engine)", "Agente de ubicación Gemini" if by_ai else "Planificador de ubicación (motor)"), "steps": agent},
        {"type": "points", "kind": "campus", "animate": "drop", "stagger_ms": 700, "items": [{"lat": s["lat"], "lon": s["lon"], "label": s.get("town"), "sub": mw_text(s["mw"], lang)} for s in sites]},
    ], [
        ("presenter", T(lang, f"Then it works through the problem, one tool call at a time. {cap1(count_text(len(agent), lang))} steps on screen, every one run on the engine." if by_ai else
                        f"Then the planner works through the problem, one tool call at a time. {cap1(count_text(len(agent), lang))} steps on screen, every one run on the engine.",
                        f"Luego trabaja el problema, una llamada a la vez. {cap1(count_text(len(agent), lang))} pasos en pantalla, todos ejecutados en el motor."), ["agent_steps"]),
        ("analyst", T(lang, "Gemini proposes. The power-flow engine decides. Nothing on this map is taken on trust." if by_ai else
                      "Without Gemini, the built-in planner makes the same kind of plan, and the engine checks it the same way.",
                      "Gemini propone. El motor de flujo de potencia decide. Nada en este mapa se acepta por confianza." if by_ai else
                      "Sin Gemini, el planificador integrado hace el mismo tipo de plan, y el motor lo comprueba igual."), []),
    ], 15000, brief="The agent's live trace: tool calls and the engine's answers; the campuses drop where it places them.",
        points=["the agent's steps (tool calls, engine results): the panel shows exactly as many steps as the agent_steps fact; never list a different number of step kinds",
                "Gemini proposes, the engine verifies" if by_ai else
                ("this run: Gemini took the first steps, then the built-in planner finished the plan (say so; never say Gemini made this plan)"
                 if any(s.get("by") == "gemini" for s in steps) else "this run: the built-in planner made the plan (never say Gemini made it)")])

    # where they break: the same sites without the upgrades
    case = res.get("case") or {}
    body = {k: case.get(k) for k in ("region", "lat", "lon", "mw", "sites", "load_factor", "firm")}
    body["upgrades"] = {}
    try:
        cin = gridmod.CaseIn(**body)
        _g2, s_in, _t, _u = gridmod.check_case(cin)
        extra, _h = gridmod._case_header(g, s_in, [], {})
        st = g.solve(np.ones(g.m, dtype=bool), extra)
        pct = np.asarray(st.loading_pct)
        over = np.flatnonzero(pct > 100.0 + 1e-6)
        over = over[np.argsort(-pct[over])]
        cas = g.cascade_case(extra)
        broke_people = int(cas.get("people_hit") or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("show: boom without-upgrades case failed: %s", e)
        over, pct, broke_people = np.array([], dtype=int), np.zeros(g.m), 0
    B.f("over_without", int(len(over)), "lines", count_text(int(len(over)), lang, fem=True))
    if len(over):
        B.f("peak_without", round(float(pct[over[0]]), 1), "%", pct_text(float(pct[over[0]]), lang))
    B.f("broke_people", broke_people, "people", people_text(broke_people, lang))
    over_ids = [int(g.br_ids[i]) for i in over]
    pct_of = {int(g.br_ids[i]): float(pct[i]) for i in over}
    over_lines = geo.line_items(over_ids, labels={b: f"{half_up(v)}%" for b, v in list(pct_of.items())[:30]})
    over_stations = []
    for b in over_ids:  # an overloaded transformer: its station, in the loss red, with how far past its rating it is
        br = geo.branch(b)
        if br and br["transformer"]:
            B.name(br["from"], _title(br["from"]))
            over_stations.append({"lat": br["a"][0], "lon": br["a"][1], "label": _title(br["from"]), "sub": T(lang, f"{half_up(pct_of[b])}% of its rating", f"{half_up(pct_of[b])} % de su capacidad")})
    B.scene("break", T(lang, "Where they break", "Dónde fallan"), bounds_of([(s["lat"], s["lon"]) for s in sites], 0.5), [
        grid_layer("dim"),
        {"type": "points", "kind": "campus", "animate": "none", "items": [{"lat": s["lat"], "lon": s["lon"], "label": s.get("town")} for s in sites]},
        *([{"type": "lines", "style": "over", "animate": "flash", "stagger_ms": 260, "emphasis": True, "items": over_lines}] if over_lines else []),
        *([{"type": "points", "kind": "station", "style": "over", "animate": "pulse", "stagger_ms": 500, "items": over_stations[:12]}] if over_stations else []),
        compare("loss", (T(lang, "Fits with no upgrades", "Cabe sin mejoras"), mw_text(wo_mw, lang) if wo_mw else "—"),
                (T(lang, "Asked for", "Pedido"), mw_text(total, lang))),
        counter(T(lang, "People hit if it ran anyway (estimate)", "Personas afectadas si funcionara igual (estimación)"), 0, broke_people, "people", "loss"),
    ], [
        ("presenter", T(lang, f"But here's the catch. With no upgrades, these sites take only {mw_text(wo_mw, lang)}. Push the full {mw_text(total, lang)} through, and {count_text(int(len(over)), lang)} lines or transformers go over their limit." if wo_mw else
                        f"Push the full {mw_text(total, lang)} through with no upgrades, and {count_text(int(len(over)), lang)} lines or transformers go over their limit.",
                        f"Pero hay una trampa. Sin mejoras, estos sitios solo aceptan {mw_text(wo_mw, lang)}. Con los {mw_text(total, lang)} completos, {count_text(int(len(over)), lang, fem=True)} líneas o transformadores pasan su límite." if wo_mw else
                        f"Con los {mw_text(total, lang)} completos y sin mejoras, {count_text(int(len(over)), lang, fem=True)} líneas o transformadores pasan su límite."),
         ["without_mw", "total_mw", "over_without"]),
        ("analyst", T(lang, f"Run it anyway and the cascade hits about {people_text(broke_people, lang)}." if broke_people else "The engine won't sign off on that.",
                      f"Si funcionara igual, la cascada afectaría a unas {people_text(broke_people, lang)}." if broke_people else "El motor no lo aprueba."), ["broke_people"]),
    ], 11000, brief="The same sites at full size with no upgrades: the overloaded lines and transformers flash red; the people it would hit.",
        points=["what fits with no upgrades vs asked", "the overloads", "people hit if it ran anyway"])

    ups = plan.get("upgrade_lines") or []
    up_ids = [u["id"] for u in ups]
    labels = {u["id"]: f"{half_up(u['old_mva'])}→{half_up(u['new_mva'])} MVA" for u in ups}
    up_stations = []
    for u in ups:
        lab = str(u.get("label") or "")
        B.name(re.sub(r"^the (transformer at )?", "", lab), *_label_names(lab))
        br = geo.branch(u["id"])
        if br and br["transformer"]:
            B.name(br["from"], _title(br["from"]))
            up_stations.append({"lat": br["a"][0], "lon": br["a"][1], "label": _title(br["from"]), "sub": labels[u["id"]]})
    added = float(plan.get("added_mva") or 0)
    B.f("added_mva", round(added), "MVA", f"{num(half_up(added), lang)} " + T(lang, "megavolt-amperes", "megavoltamperios"))
    B.scene("fix", T(lang, "Where they fit", "Dónde caben"), bounds_of([(s["lat"], s["lon"]) for s in sites], 0.5), [
        grid_layer("dim"),
        *([{"type": "lines", "style": "upgrade", "animate": "draw", "stagger_ms": 400, "items": geo.line_items(up_ids, labels=labels)}] if geo.line_items(up_ids) else []),
        *([{"type": "points", "kind": "station", "style": "upgrade", "animate": "pulse", "stagger_ms": 600, "items": up_stations}] if up_stations else []),
        {"type": "points", "kind": "campus", "animate": "none", "items": [{"lat": s["lat"], "lon": s["lon"], "label": s.get("town"), "sub": mw_text(s["mw"], lang)} for s in sites]},
        compare("gain", (T(lang, "Upgrades", "Mejoras"), str(n_up)), (T(lang, "Busiest line after", "Línea más cargada después"), f"{half_up(ver.get('busiest_pct') or 0)}%")),
    ], [
        ("presenter", T(lang, f"So the plan adds {count_text(n_up, lang)} upgrades, drawn here in green. Now all {mw_text(total, lang)} fit, at {count_text(len(sites), lang)} sites.",
                        f"Así que el plan suma {count_text(n_up, lang, fem=True)} mejoras, aquí en verde. Ahora caben los {mw_text(total, lang)}, en {count_text(len(sites), lang)} sitios."),
         ["upgrades", "total_mw", "sites"]),
        ("analyst", T(lang, f"The engine re-checks the whole plan at once: the busiest line sits at {pct_text(ver.get('busiest_pct') or 0, lang)}, and nobody loses power.",
                      f"El motor vuelve a comprobar el plan completo a la vez: la línea más cargada queda al {pct_text(ver.get('busiest_pct') or 0, lang)}, y nadie pierde la luz."), ["busiest_pct"]),
    ], 9000, brief="The plan's upgrades draw in green; every campus at full size; the verification.", points=["the upgrades", "verified: busiest line, nobody without power"])

    cap = _study_capacity(code)
    if cap:
        c = cap["cap"]
        firm, flex = c["firm"], c.get("flexible") or {}
        B.f("firm_today", int(firm["today"]), "campuses", count_text(int(firm["today"]), lang))
        if flex:
            B.f("flex_today", int(flex["today"]), "campuses", count_text(int(flex["today"]), lang))
            share = flex.get("share")
            if share is not None:
                B.f("flex_share", round(float(share) * 100), "%", pct_text(float(share) * 100, lang))
        for s in (c.get("sources") or []):
            if "Duke" in str(s.get("name")) or "Nicholas" in str(s.get("name")):
                B.src(s["name"], s.get("url"))
        B.scene("flexible", T(lang, "Always on, or flexible?", "¿Siempre encendidos o flexibles?"), region_bounds(code), [
            grid_layer("base"),
            compare("gain", (T(lang, "Always-on campuses that fit today", "Campus siempre encendidos que caben hoy"), str(firm["today"])),
                    (T(lang, "Flexible campuses that fit today", "Campus flexibles que caben hoy"), str(flex.get("today", "—")))),
            lower(T(lang, "Flexible: the campus eases off on the hottest afternoons", "Flexible: el campus reduce su consumo en las tardes más calurosas"),
                  T(lang, "1 GW campuses, no upgrades", "Campus de 1 GW, sin mejoras")),
        ], [
            ("presenter", T(lang, f"One more lever, and it costs nothing to build. On this model, {count_text(int(firm['today']), lang)} always-on one-gigawatt campuses fit today. Flexible ones, that ease off on the hottest afternoons: {count_text(int(flex.get('today', 0)), lang)}.",
                            f"Una palanca más, y no cuesta construirla. En este modelo, hoy caben {count_text(int(firm['today']), lang)} campus de un gigavatio siempre encendidos. Flexibles, que bajan su consumo en las tardes más calurosas: {count_text(int(flex.get('today', 0)), lang)}."),
             ["firm_today", "flex_today"]),
            ("analyst", T(lang, "Same wires, same plants. The difference is when the campus draws its power.",
                          "Los mismos cables, las mismas centrales. La diferencia es cuándo consume el campus."), []),
        ], 8500, brief="Always-on vs flexible campuses that fit today, side by side.", points=["flexible campuses as a free lever", "same grid, timing differs"])

        plants_c = c.get("plants") or {}
        if plants_c.get("campuses_firm"):
            B.f("reserve_firm", int(plants_c["campuses_firm"]), "campuses", count_text(int(plants_c["campuses_firm"]), lang))
        if flex and flex.get("steps") and firm.get("steps"):
            fmax, xmax = len(firm["steps"]), len(flex["steps"])
            B.f("firm_max", fmax, "campuses", count_text(fmax, lang))
            B.f("flex_max", xmax, "campuses", count_text(xmax, lang))
            fpts = [{"lat": st_["site"]["lat"], "lon": st_["site"]["lon"], "label": st_["site"]["area"]} for st_ in flex["steps"][:xmax]]
            for p_ in fpts:
                B.name(p_["label"])
            B.scene("flexmore", T(lang, "Always on, or flexible?", "¿Siempre encendidos o flexibles?"), region_bounds(code), [
                grid_layer("dim"),
                {"type": "points", "kind": "campus", "animate": "drop", "stagger_ms": 350, "items": fpts},
                compare("gain", (T(lang, "Always on, with every wire upgrade it takes", "Siempre encendidos, con todas las mejoras de cables necesarias"), str(fmax),
                             T(lang, "until the plants are at their limit", "hasta el límite de las centrales")),
                    (T(lang, "Flexible, with every wire upgrade it takes", "Flexibles, con todas las mejoras de cables necesarias"), str(xmax),
                     T(lang, "until the plants are at their limit", "hasta el límite de las centrales"))),
                *([lower(T(lang, f"Keeping the plants' {pct_text(plants_c.get('reserve_pct') or 15, 'en')} reserve: {plants_c.get('campuses_firm')} always-on, {plants_c.get('campuses_flexible')} flexible",
                            f"Con la reserva del {pct_text(plants_c.get('reserve_pct') or 15, 'es')} de las centrales: {plants_c.get('campuses_firm')} siempre encendidos, {plants_c.get('campuses_flexible')} flexibles"),
                           T(lang, "The limits above use up that reserve", "Los límites de arriba agotan esa reserva"))] if plants_c.get("campuses_firm") else []),
            ], [
                ("presenter", T(lang, f"Push further, with as many wire upgrades as it takes, until the power plants are at their limit: {count_text(fmax, lang)} always-on campuses, or {count_text(xmax, lang)} flexible ones.",
                                f"Más allá, con todas las mejoras de cables necesarias, hasta el límite de las propias centrales: {count_text(fmax, lang)} campus siempre encendidos, o {count_text(xmax, lang)} flexibles."),
                 ["firm_max", "flex_max"]),
                ("analyst", (T(lang, f"That uses up the plants' reserve. Keeping it, the answer is {count_text(int(plants_c['campuses_firm']), lang)} always-on. A campus that can wait out the peak is one the grid can say yes to sooner.",
                               f"Eso agota la reserva de las centrales. Si se mantiene, la respuesta es {count_text(int(plants_c['campuses_firm']), lang)} siempre encendidos. Un campus que puede esperar a que pase el pico es uno al que la red puede decir que sí antes.")
                             if plants_c.get("campuses_firm") else
                             T(lang, "A campus that can wait out the peak is a campus the grid can say yes to sooner.",
                               "Un campus que puede esperar a que pase el pico es un campus al que la red puede decir que sí antes.")), ["reserve_firm"]),
            ], 8500, brief="Every flexible campus the model's plants can supply drops on the map; always-on vs flexible counts side by side, with unlimited wire upgrades.",
                points=["these counts assume every wire upgrade it takes, up to the plants' limit (not the reserve)", "keeping the plants' reserve stops always-on campuses far sooner (reserve_firm)", "flexibility as the fastest yes"])

        B.f("today", cap["today"], "campuses", count_text(cap["today"], lang))
        B.f("headline", cap["headline"], "campuses", count_text(cap["headline"], lang))
        B.f("budget_usd", cap["budget"], "USD", usd_text(cap["budget"], lang))
        B.scene("close", T(lang, "What it can take", "Lo que aguanta"), region_bounds(code), [
            grid_layer("base"),
            {"type": "meter", "cells": max(cap["headline"] + 3, 10), "today": cap["today"], "filled": cap["headline"], "reserve_at": cap.get("reserve_at"),
             "label": T(lang, "1 GW campuses at once", "Campus de 1 GW a la vez")},
            title_layer(T(lang, "Where the next ones go matters more than how many", "Dónde van los próximos importa más que cuántos"),
                        T(lang, "Next: Strengthen the grid", "Siguiente: reforzar la red")),
        ], [
            ("presenter", T(lang, f"Put them where the grid has room, add {usd_text(cap['budget'], lang)} of upgrades, and {count_text(cap['headline'], lang)} fit at once.",
                            f"Pónganlos donde la red tiene espacio, sumen {usd_text(cap['budget'], lang)} en mejoras, y caben {count_text(cap['headline'], lang)} a la vez."), ["budget_usd", "headline"]),
        ], 7000, brief="The capacity meter: the hand-off to Strengthen.", points=["how many fit with upgrades"])
    return B


# ------------------------------------------------------------------------------------------------ episode: strengthen
def build_strengthen(code: str, lang: str) -> Board:
    cap = _study_capacity(code)
    if cap is None:
        raise HTTPException(status_code=409, detail="No finished Strengthen study for this state yet: open Strengthen the grid and run it first")
    c = cap["cap"]
    firm = c["firm"]
    steps = firm["steps"]
    head, today, budget = cap["headline"], cap["today"], cap["budget"]
    reserve = cap.get("reserve_at")
    state = REGIONS[code]["name"]
    g = grid_at(1.0, code)
    B = Board("strengthen", code, lang, EPISODES[3]["title"][lang])
    B.src("Breakthrough Energy / Texas A&M synthetic grid (CC-BY 4.0)", CREDIT_URL)
    for s in c.get("sources") or []:
        B.src(s.get("name"), s.get("url"))
    B.name(state, "Gemini")
    B.f("today", today, "campuses", count_text(today, lang))
    B.f("headline", head, "campuses", count_text(head, lang))
    B.f("budget_usd", budget, "USD", usd_text(budget, lang), "Black & Veatch unit costs, CPI-adjusted (high end)")
    B.costs.append("budget_usd")
    plants = c.get("plants") or {}
    if reserve:
        B.f("reserve_at", int(reserve), "campuses", count_text(int(reserve), lang))
        B.f("reserve_pct", plants.get("reserve_pct"), "%", pct_text(plants.get("reserve_pct") or 0, lang), "NERC LTRA reference margin")
    cells = max(head + 3, 10)
    fl_all = region_bounds(code)

    def site_pt(s):
        return {"lat": s["site"]["lat"], "lon": s["site"]["lon"], "label": s["site"]["area"], "sub": T(lang, f"campus {s['n']}", f"campus {s['n']}")}

    for s in steps[: head + 1]:
        B.name(s["site"]["area"])
    today_steps = [s for s in steps if s["n"] <= today]
    B.scene("open", T(lang, "Cold open", "Apertura"), fl_all, [
        grid_layer("base"),
        title_layer(T(lang, "How many can the grid take?", "¿Cuántos aguanta la red?"), T(lang, f"1 GW AI campuses, all at once, on a synthetic model of {state}'s grid",
                                                                                         f"Campus de IA de 1 GW, todos a la vez, en un modelo sintético de la red de {_state_es(state)}")),
        {"type": "meter", "cells": cells, "today": today, "filled": 0, "reserve_at": reserve, "label": T(lang, "1 GW campuses at once", "Campus de 1 GW a la vez")},
    ], [
        ("presenter", T(lang, f"Every AI data center asks the same question: is there room on the grid? Here's the answer for a synthetic model of {state}'s grid.",
                        f"Cada centro de datos de IA hace la misma pregunta: ¿hay espacio en la red? Esta es la respuesta para un modelo sintético de la red de {_state_es(state)}."), []),
        ("analyst", T(lang, "One-gigawatt campuses, connected all at once, with no line or transformer over its rating. We fill this meter one campus at a time.",
                      "Campus de un gigavatio, conectados todos a la vez, sin ninguna línea ni transformador sobre su capacidad. Llenamos este medidor campus a campus."), []),
    ], 8500, brief="The capacity meter, empty; title card.", points=["the question: how many 1 GW campuses at once", "synthetic model"])

    B.scene("today", T(lang, "Today", "Hoy"), bounds_of([(s["site"]["lat"], s["site"]["lon"]) for s in today_steps], 0.8), [
        grid_layer("base"),
        {"type": "points", "kind": "campus", "animate": "drop", "stagger_ms": 900, "items": [site_pt(s) for s in today_steps]},
        {"type": "meter", "cells": cells, "today": today, "filled": today, "reserve_at": reserve, "label": T(lang, "1 GW campuses at once", "Campus de 1 GW a la vez")},
    ], [
        ("presenter", T(lang, f"Today, with no upgrades at all, the grid carries {count_text(today, lang)}: at {join_words([s['site']['area'] for s in today_steps], lang)}.",
                        f"Hoy, sin ninguna mejora, la red aguanta {count_text(today, lang)}: en {join_words([s['site']['area'] for s in today_steps], lang)}."), ["today"]),
        ("analyst", T(lang, "Each one goes where it fits with every campus before it. That's the key: they're all on at once.",
                      "Cada uno va donde cabe junto con todos los anteriores. Esa es la clave: todos encendidos a la vez."), []),
    ], 8000, brief="Today's campuses drop; the meter fills to today's count.", points=["how many fit today, where", "all at once"])

    fb = firm.get("first_block") or {}
    if fb:
        B.name(fb.get("where"), fb.get("short"), *(_label_names(fb.get("label", ""))))
        B.f("block_sites", int(fb.get("blocks") or 0), "sites", count_text(int(fb.get("blocks") or 0), lang))
        B.f("block_of", int(fb.get("of") or 0), "sites", count_text(int(fb.get("of") or 0), lang))
        B.lines_info.append({"id": fb.get("branch_id"), "label": fb.get("label"), "kind": fb.get("kind"), "kv": fb.get("kv"),
                             "blocks": fb.get("blocks"), "of": fb.get("of"), "rating_estimated": bool(fb.get("rate_est"))})
        mid = fb.get("mid") or [fb.get("from", {}).get("lat"), fb.get("from", {}).get("lon")]
        blk = fb.get("label") if lang == "en" else _label_es(fb.get("label", ""))
        lay = [grid_layer("dim")]
        if fb.get("kind") == "transformer":
            lay.append({"type": "points", "kind": "station", "style": "stress", "animate": "pulse", "items": [{"lat": mid[0], "lon": mid[1], "label": fb.get("short"),
                                                                                                          "sub": T(lang, f"blocks {fb.get('blocks')} of {fb.get('of')} sites", f"bloquea {fb.get('blocks')} de {fb.get('of')} sitios")}]})
        else:
            lay.append({"type": "lines", "style": "stress", "animate": "flash", "emphasis": True, "items": [{"path": [[fb["from"]["lat"], fb["from"]["lon"]], [fb["to"]["lat"], fb["to"]["lon"]]], "label": fb.get("short")}]})
        lay.append(lower(cap1(blk), T(lang, f"stops the next campus at {fb.get('blocks')} of {fb.get('of')} sites tried", f"frena el siguiente campus en {fb.get('blocks')} de {fb.get('of')} sitios probados")))
        B.scene("block", T(lang, "What stops the next one", "Qué frena al siguiente"), bounds_of([(mid[0], mid[1])], 0.3, 0.8), lay, [
            ("presenter", T(lang, f"What stops the next one? {cap1(blk)}. It blocks {count_text(int(fb.get('blocks') or 0), lang)} of the {count_text(int(fb.get('of') or 0), lang)} sites tried.",
                            f"¿Qué frena al siguiente? {cap1(blk)}. Bloquea {count_text(int(fb.get('blocks') or 0), lang)} de los {count_text(int(fb.get('of') or 0), lang)} sitios probados."),
             ["block_sites", "block_of"]),
            ("analyst", T(lang, "One piece of equipment, far from the campus, holding back the whole state.",
                          "Un solo equipo, lejos del campus, frenando a todo el estado."), []),
        ], 8000, brief="Close on the weak point that stops the next campus.", points=["the weak point that blocks the next campus", "blocks N of N sites"], min_km=60)

    paid = [s for s in steps if today < s["n"] <= head]
    groups: list[list[dict]] = []
    for s in paid:
        if len(groups) < 3 or not groups:
            groups.append([s])
        else:
            groups[-1].append(s)
    shown_projects: list[dict] = []
    for gi, grp in enumerate(groups):
        projects = [p for s in grp for p in s.get("projects") or []]
        shown_projects += projects
        lines_items, stations = [], []
        for p in projects:
            coords = (p.get("geometry") or {}).get("coords") or []
            lab = f"{half_up(p['rating_before_mva'])}→{half_up(p['rating_after_mva'])} MVA"
            if p.get("kind") == "transformer" or len(coords) < 2:
                if coords:
                    stations.append({"lat": coords[0][1], "lon": coords[0][0], "label": p.get("short") or p.get("label"), "sub": lab})
            else:
                lines_items.append({"path": [[c_[1], c_[0]] for c_ in coords], "label": p.get("short") or p.get("label"), "value": lab})
            B.name(p.get("where"), p.get("short"), *(_label_names(p.get("label", ""))))
            B.lines_info.append({"id": p.get("branch_id"), "label": p.get("label"), "kind": p.get("kind"), "rating_before_mva": p.get("rating_before_mva"),
                                 "rating_after_mva": p.get("rating_after_mva"), "cost_high": (p.get("cost") or {}).get("high"), "by": p.get("by")})
        last = grp[-1]
        cum = float(last["cum_cost"]["high"])
        cost_grp = sum(float(s["cost"]["high"]) for s in grp)
        fid_c = B.f(f"cum_{last['n']}", cum, "USD", usd_text(cum, lang))
        fid_g = B.f(f"cost_{last['n']}", cost_grp, "USD", usd_text(cost_grp, lang))
        B.f(f"n_{last['n']}", last["n"], "campuses", count_text(last["n"], lang))
        B.costs += [fid_c, fid_g]
        areas = [s["site"]["area"] for s in grp]
        up_words = join_words(sorted({(p.get("label") if lang == "en" else _label_es(p.get("label", ""))) for p in projects})[:3], lang)
        pts = [(s["site"]["lat"], s["site"]["lon"]) for s in grp] + [(st_["lat"], st_["lon"]) for st_ in stations] + [tuple(li["path"][0]) for li in lines_items]
        first_n = grp[0]["n"]
        B.scene(f"build{gi + 1}", T(lang, "The build-up", "La construcción"), bounds_of(pts, 0.5), [
            grid_layer("dim"),
            {"type": "lines", "style": "upgrade", "animate": "draw", "stagger_ms": 500, "items": lines_items},
            {"type": "points", "kind": "station", "style": "upgrade", "animate": "pulse", "stagger_ms": 700, "items": stations},
            {"type": "points", "kind": "campus", "animate": "drop", "stagger_ms": 1100, "items": [site_pt(s) for s in grp]},
            {"type": "meter", "cells": cells, "today": today, "filled": last["n"], "reserve_at": reserve, "label": T(lang, "1 GW campuses at once", "Campus de 1 GW a la vez")},
            counter(T(lang, "Upgrades so far (high end)", "Mejoras hasta ahora (extremo alto)"), float(grp[0]["cum_cost"]["high"]) - float(grp[0]["cost"]["high"]), cum, "usd", "neutral"),
        ], [
            ("presenter", T(lang, (f"Campus {words(first_n, 'en')} goes in at {areas[0]}." if len(grp) == 1 else f"Campuses {words(first_n, 'en')} to {words(last['n'], 'en')} go in at {join_words(areas, 'en')}.")
                            + f" To let {'it' if len(grp) == 1 else 'them'} in: {up_words}, raised.",
                            (f"El campus {words(first_n, 'es')} entra en {areas[0]}." if len(grp) == 1 else f"Los campus {words(first_n, 'es')} a {words(last['n'], 'es')} entran en {join_words(areas, 'es')}.")
                            + f" Para dejarle{'' if len(grp) == 1 else 's'} entrar se refuerza {up_words}."),
             [f"n_{last['n']}"]),
            ("analyst", T(lang, f"That costs up to {usd_text(cost_grp, lang)}. Running total: {usd_text(cum, lang)}, and the meter reads {count_text(last['n'], lang)}.",
                          f"Eso cuesta hasta {usd_text(cost_grp, lang)}. Total acumulado: {usd_text(cum, lang)}, y el medidor marca {count_text(last['n'], lang)}."),
             [fid_g, fid_c, f"n_{last['n']}"]),
        ], 9000, brief="Upgrades draw in green; the next campuses drop; the meter fills; the running cost climbs.",
            points=["where each campus goes", "the upgrade that lets it in", "its cost and the running total"])

    B.scene("headline", T(lang, "The answer", "La respuesta"), fl_all, [
        grid_layer("base"),
        {"type": "points", "kind": "campus", "animate": "appear", "items": [site_pt(s) for s in steps if s["n"] <= head]},
        {"type": "lines", "style": "upgrade", "animate": "none", "items": [{"path": [[c_[1], c_[0]] for c_ in (p.get("geometry") or {}).get("coords") or []]} for p in shown_projects if len((p.get("geometry") or {}).get("coords") or []) >= 2]},
        {"type": "meter", "cells": cells, "today": today, "filled": head, "reserve_at": reserve, "label": T(lang, "1 GW campuses at once", "Campus de 1 GW a la vez")},
        title_layer(T(lang, f"{head} at once with {usd_text(budget, 'en')} of upgrades. Today: {today}.", f"{head} a la vez con {usd_text(budget, 'es')} en mejoras. Hoy: {today}.")),
    ], [
        ("presenter", T(lang, f"The answer: {state}'s grid model can carry {count_text(head, lang)} one-gigawatt data centers at once with {usd_text(budget, lang)} of upgrades. Today it carries {count_text(today, lang)}.",
                        f"La respuesta: el modelo de la red de {_state_es(state)} puede aguantar {count_text(head, lang)} centros de datos de un gigavatio a la vez con {usd_text(budget, lang)} en mejoras. Hoy aguanta {count_text(today, lang)}."),
         ["headline", "budget_usd", "today"]),
        ("analyst", (T(lang, f"One caveat, marked on the meter: past {count_text(int(reserve), lang)}, the power plants' {pct_text(plants.get('reserve_pct') or 0, lang)} reserve runs out. Beyond that, it also takes new generation or flexible campuses.",
                       f"Una advertencia, marcada en el medidor: pasado {count_text(int(reserve), lang)}, se agota la reserva del {pct_text(plants.get('reserve_pct') or 0, lang)} de las centrales. Más allá, también hace falta nueva generación o campus flexibles.")
                     if reserve else T(lang, "Every campus on the meter was checked all together, not one at a time.", "Cada campus del medidor se comprobó con todos a la vez, no uno por uno.")),
         ["reserve_at", "reserve_pct"]),
    ], 10000, brief="The whole state with every campus and upgrade; the meter at the headline with the reserve line marked.",
        points=["the headline answer", "the power plants' reserve line on the meter"])

    ai = c.get("ai") or {}
    if ai.get("trace"):
        steps_ai = []
        for t in ai["trace"]:
            kind = {"ask": "tool", "propose": "propose", "revise": "propose", "verify": "verify", "feedback": "tool", "verdict": "verify"}.get(t.get("kind"), "verify")
            if t.get("kind") == "verify" and t.get("holds") is False:
                kind = "reject"
            if t.get("kind") == "verify" and t.get("holds") is True:
                kind = "accept"
            steps_ai.append({"actor": t.get("actor") if t.get("actor") in ("gemini", "engine") else "engine", "kind": kind, "text": str(t.get("title") or "")[:200]})
        B.agent_steps += steps_ai
        status = ai.get("status")
        B.f("ai_rounds", int(ai.get("rounds") or len([t for t in ai["trace"] if t.get("actor") == "gemini"])), "rounds", count_text(int(ai.get("rounds") or 1), lang))
        verdict_en = {"beat": "Gemini beat the engine's plan, and the engine confirmed it.", "matched": "Gemini matched the engine's plan.",
                      "lost": "Gemini didn't beat the engine's plan. The engine's plan stands.", "offline": "Gemini's challenge didn't run; the engine's plan stands."}
        verdict_es = {"beat": "Gemini superó el plan del motor, y el motor lo confirmó.", "matched": "Gemini igualó el plan del motor.",
                      "lost": "Gemini no superó el plan del motor. El plan del motor se mantiene.", "offline": "El reto de Gemini no se ejecutó; el plan del motor se mantiene."}
        B.scene("challenge", T(lang, "Gemini's challenge", "El reto de Gemini"), fl_all, [
            grid_layer("dim"),
            {"type": "agent", "title": T(lang, "Gemini tries to beat the plan", "Gemini intenta superar el plan"), "steps": steps_ai},
        ], [
            ("presenter", T(lang, "Can an AI do better? We gave Gemini the engine's plan and asked for one more campus for the same money, or the same for less.",
                            "¿Puede una IA hacerlo mejor? Le dimos a Gemini el plan del motor y le pedimos un campus más por el mismo dinero, o los mismos por menos."), []),
            ("analyst", T(lang, verdict_en.get(status, verdict_en["offline"]) + " Its own numbers are never used: the engine re-solves and prices every proposal.",
                          verdict_es.get(status, verdict_es["offline"]) + " Sus propios números nunca se usan: el motor vuelve a resolver y a poner precio a cada propuesta."), []),
        ], 10000, brief="Gemini's challenge as a live trace: its proposals, the engine's verdicts, the revision, the final verdict (honest).",
            points=["Gemini proposes, the engine re-solves and prices", f"the verdict (say it exactly): {verdict_en.get(status, verdict_en['offline'])}", f"why, in the engine's words: {ai.get('sentence') or ''}"],
            forbid={"lost": [r"\bmatch\w*", r"(?<!n't )(?<!not )(?<!failed to )\bbeat\b", r"\bwon\b", r"(?<!no )\bigual\w*", r"(?<!no )\bsuper[oó]\b", r"\bgan[oó]\b"],
                    "matched": [r"\bbeat\b", r"\bwon\b", r"\bbetter\b", r"\bsuper[oó]\b", r"\bgan[oó]\b"],
                    "beat": [r"didn'?t beat", r"\bfailed\b", r"no super[oó]"]}.get(status, [r"\bbeat\b", r"\bmatch\w*", r"\bwon\b"]))

    sens = c.get("sensitivity") or {}
    rows = [r for r in sens.get("rows") or [] if (r.get("budget") or {}).get("campuses") is not None]
    if rows:
        vals = [int(r["budget"]["campuses"]) for r in rows]
        lo, hi = min(vals), max(vals)
        B.f("sens_lo", lo, "campuses", count_text(lo, lang))
        B.f("sens_hi", hi, "campuses", count_text(hi, lang))
        B.f("sens_cases", len(rows), "cases", count_text(len(rows), lang))
        label_es = {"shipped": "Como se entrega", "pro_rata": "Despacho proporcional", "limit_90": "Límite del 90 %", "est_x2": "Capacidades estimadas x2", "est_tight": "Capacidades estimadas más ajustadas"}
        B.scene("sure", T(lang, "How sure?", "¿Qué tan seguro?"), fl_all, [
            grid_layer("dim"),
            {"type": "bars", "title": T(lang, f"Campuses at once for the same budget, under other assumptions", "Campus a la vez por el mismo presupuesto, con otros supuestos"), "format": "count",
             "items": [{"label": r.get("label") if lang == "en" else label_es.get(r.get("id"), r.get("label")), "value": int(r["budget"]["campuses"]), "tone": "neutral"} for r in rows]},
        ], [
            ("presenter", T(lang, f"How sure is that number? We re-ran the search under {count_text(len(rows), lang)} sets of assumptions. For the same money, the answer ranges from {count_text(lo, lang)} to {count_text(hi, lang)}.",
                            f"¿Qué tan seguro es ese número? Repetimos la búsqueda con {count_text(len(rows), lang)} conjuntos de supuestos. Por el mismo dinero, la respuesta va de {count_text(lo, lang)} a {count_text(hi, lang)}."),
             ["sens_cases", "sens_lo", "sens_hi"]),
            ("analyst", T(lang, "A range, not a promise. The model's own made-up ratings are the biggest unknown.",
                          "Un rango, no una promesa. Las capacidades que el modelo tuvo que estimar son la mayor incógnita."), []),
        ], 8500, brief="Bars: the same search under other assumptions; the range of the answer.",
            points=["the range under other assumptions (the bars name them: how the plants share the load, a lower loading limit, the model's estimated ratings)",
                    "what drives the uncertainty: the model's estimated ratings (never invent another reason)"])

    n1 = c.get("n1") or {}
    if n1.get("status") == "done" and n1.get("new_overloads") is not None:
        B.f("n1_outages", int(n1.get("screened") or 0), "outages", num(int(n1.get("screened") or 0), lang))
        B.f("n1_new", int(n1["new_overloads"]), "overloads", count_text(int(n1["new_overloads"]), lang))
        emerg = n1.get("emergency_pct") or 115
        B.scene("n1", T(lang, "The fine print", "La letra pequeña"), fl_all, [
            grid_layer("dim"),
            {"type": "quote", "text": T(lang, f"{int(n1['new_overloads'])} of {num(int(n1.get('screened') or 0), 'en')} single outages push a line or transformer past {emerg:g}% of its rating that the model alone doesn't, with the plan in place.",
                                        f"{int(n1['new_overloads'])} de {num(int(n1.get('screened') or 0), 'es')} fallas simples llevan una línea o un transformador más allá del {emerg:g} % de su capacidad, con el plan instalado, donde el modelo solo no lo hace."),
             "source": T(lang, "The engine's single-outage (N-1) screen", "La revisión de fallas simples (N-1) del motor")},
        ], [
            ("presenter", T(lang, f"And the fine print. A real study also asks: what if one line fails? We tried {num(int(n1.get('screened') or 0), lang)} single outages with the plan in place.",
                            f"Y la letra pequeña. Un estudio real también pregunta: ¿y si falla una línea? Probamos {num(int(n1.get('screened') or 0), lang)} fallas simples con el plan instalado."), ["n1_outages"]),
            ("analyst", T(lang, f"{cap1(count_text(int(n1['new_overloads']), lang))} of them would overload something new. A real plan would size for those too.",
                          f"{cap1(count_text(int(n1['new_overloads']), lang, fem=True))} de ellas sobrecargarían algo nuevo. Un plan real también las tendría en cuenta."), ["n1_new"]),
        ], 8000, brief="The single-outage (N-1) screen, stated honestly.", points=["N-1: single outages with the plan", "honest caveat"])

    try:
        import leadtimes

        ttp = leadtimes.time_to_power(cap["result"])
        camps = [x for x in ((ttp.get("firm") or {}).get("campuses") or []) if isinstance(x, dict)]
        if camps:
            rows = []
            for x in camps[:head]:
                lo_, hi_ = x.get("lo"), x.get("hi")
                if not isinstance(hi_, (int, float)):
                    continue
                lo_s = f"{lo_:g}" if isinstance(lo_, (int, float)) else str(lo_)
                hi_s = f"{hi_:g}" + ("+" if x.get("plus") else "")
                rows.append((float(hi_), float(lo_) if isinstance(lo_, (int, float)) else float(hi_), x.get("n"), lo_s, hi_s))
            rows.sort(key=lambda r: (r[0], r[1], r[2]))  # soonest first: the campuses connect in the order they could
            hi_min = min((r[0] for r in rows), default=0.0)
            span_hi = (max((r[0] for r in rows), default=1.0) - hi_min) or 1.0
            items, t_pts = [], []
            by_n = {s_["n"]: s_ for s_ in steps}
            for hi_v, _lo_v, n_, lo_s, hi_s in rows:
                at = round(min(1.0, (hi_v - hi_min) / span_hi), 4)  # the soonest at the start, the longest wait at the end
                items.append({"t": n_, "label": T(lang, f"Campus {n_}: {lo_s}–{hi_s} years", f"Campus {n_}: {lo_s}–{hi_s} años"), "tone": "neutral", "at": at})
                st_ = by_n.get(n_)
                if st_:
                    t_pts.append({"lat": st_["site"]["lat"], "lon": st_["site"]["lon"], "label": st_["site"]["area"], "sub": T(lang, f"{lo_s}–{hi_s} years", f"{lo_s}–{hi_s} años"), "at": at})
            if items:
                B.src("Time to power: typical durations from published sources (leadtimes.py)", None)
                B.scene("time", T(lang, "Time to power", "Tiempo hasta la conexión"), bounds_of([(p_["lat"], p_["lon"]) for p_ in t_pts], 0.5) if t_pts else fl_all, [
                    grid_layer("dim"),
                    *([{"type": "points", "kind": "campus", "animate": "drop", "timing": "span", "items": t_pts}] if t_pts else []),
                    {"type": "timeline", "timing": "span", "items": items},
                ], [
                    ("presenter", T(lang, "Money is one clock. Time is the other: the campuses that need a new transformer wait for it to be built.",
                                    "El dinero es un reloj. El tiempo es el otro: los campus que necesitan un transformador nuevo esperan a que se fabrique."), []),
                ], 7000, brief="A timeline: roughly when each campus could connect (typical durations, not dates).", points=["time to power, typical ranges"])
    except Exception as e:  # noqa: BLE001 — time to power is an extra scene
        log.info("show: time to power skipped: %s", e)

    B.scene("close", T(lang, "Bottom line", "En resumen"), fl_all, [
        grid_layer("base"),
        {"type": "meter", "cells": cells, "today": today, "filled": head, "reserve_at": reserve, "label": T(lang, "1 GW campuses at once", "Campus de 1 GW a la vez")},
        title_layer(T(lang, "Put them where there's room. Fix the few weak points.", "Pónganlos donde hay espacio. Arreglen los pocos puntos débiles.")),
    ], [
        ("presenter", T(lang, f"From {count_text(today, lang)} to {count_text(head, lang)}, for {usd_text(budget, lang)}. Put the campuses where there's room, and fix the few weak points that hold everything back.",
                        f"De {count_text(today, lang)} a {count_text(head, lang)}, por {usd_text(budget, lang)}. Pongan los campus donde hay espacio, y arreglen los pocos puntos débiles que frenan todo."),
         ["today", "headline", "budget_usd"]),
    ], 7000, brief="The meter at the headline; the bottom line.", points=["from today to the headline, for the budget"])
    return B


# ------------------------------------------------------------------------------------------------ episode: together
async def build_together(lang: str, progress) -> Board:
    import gridlock as gl
    import negotiate

    async with _gate():
        opp = await run_in_threadpool(lambda: json.loads(gl.opportunities(limit=8, max_km=gl.MAX_KM_DEFAULT, window_months=gl.WINDOW_DEFAULT,
                                                                          method="closest", a="DESC", b="GPC").body))
        summ = await run_in_threadpool(gl.summary)
        station = await run_in_threadpool(_station_pair)
    ops = opp.get("opportunities") or []
    if not ops:
        raise HTTPException(status_code=503, detail="The Build together data isn't built yet")
    top = next((o for o in ops if o.get("shared_station")), ops[0])
    # the same-substation scene keeps its own pair: the ranking puts a pair whose filed time has passed last, so Sperry's
    # own example (Thurmond Dam) sits past the top 8 and the scene silently vanished (the episode fell under two minutes)
    station = top if top.get("shared_station") else station
    progress(T(lang, "Two AI agents are drafting a coordination plan from the filings", "Dos agentes de IA redactan un plan de coordinación a partir de los documentos"))
    neg = await negotiate.run_case(top["id"], gl.WINDOW_DEFAULT, lang, ai=llm.configured())
    async with _gate():
        est = await run_in_threadpool(gl.estimate, top["id"], gl.WINDOW_DEFAULT)
        return await run_in_threadpool(_together_board, lang, opp, summ, top, neg, est, station)


def _station_pair() -> dict | None:
    """The best-ranked flagged pair whose two projects meet at the same substation, searched over EVERY flagged pair
    (the top few the ranked scene shows may hold none: a pair whose filed time has passed ranks last), shaped like a row
    of /api/gridlock/opportunities."""
    import gridlock as gl

    st = gl._load()
    prm = gl._params(gl.MAX_KM_DEFAULT, gl.WINDOW_DEFAULT, "closest", "DESC", "GPC")
    o = next((o for o in gl._compute(st, prm)["overlaps"] if o.get("shared_station")), None)
    return {**o, "project_a": gl._slim(st["by_id"][o["a"]]), "project_b": gl._slim(st["by_id"][o["b"]])} if o else None


def _years_in(title: str | None, default: str) -> str:
    m = re.search(r"(20\d\d)\s*[-–]\s*(20\d\d)", title or "")
    return f"{m.group(1)}–{m.group(2)}" if m else default


def _together_board(lang: str, opp: dict, summ: dict, top: dict, neg: dict, est: dict, station: dict | None = None) -> Board:
    B = Board("together", "GA-SC", lang, EPISODES[4]["title"][lang])
    B.first_rule = "filings"
    B.names -= {"Breakthrough Energy", "Texas A&M", "A&M", "Census"}
    B.forbid += [r"synth\w*", r"sint[eé]tic\w*", r"Breakthrough", r"Texas A&M", r"\bmodel\w*", r"\bmodelo\w*"]  # real filings: never the synthetic model
    # never imply the utilities fail to coordinate, never put coordinates in the filings (the pipeline locates their
    # place names on OpenStreetMap), nothing "hidden"
    B.forbid += [r"\bonly\b[^.]{0,40}\bseparate\b", r"\bseparately\b", r"\bsilos?\b", r"\bin isolation\b", r"\b(don'?t|do not|never|fail\w*( to)?) (talk|coordinate|communicate|share)\w*",
                 r"\bhidden\b", r"\bburied\b", r"\bcoordinates?\b[^.]{0,30}\b(in|inside|from|within) (the|those|these|their|both)?\s*(public )?(filings?|documents?)",
                 r"\bpor separado\b", r"\bocult\w*", r"\bno se coordinan\b"]
    for s in summ.get("sources") or []:
        B.src(s.get("title") or s.get("id"), s.get("url"))
    B.src("Basemap and substation locations: © OpenStreetMap contributors (ODbL)", "https://www.openstreetmap.org/copyright")
    pa, pb = top["project_a"], top["project_b"]
    ua, ub = pa.get("utility_name") or "DESC", pb.get("utility_name") or "Georgia Power"
    B.name("DESC", "Dominion Energy South Carolina", "Georgia Power", "Georgia", "South Carolina", "Gemini", "Sperry", "OpenStreetMap",
           "Georgia Transmission", "MEAG", "Dalton Utilities", "Savannah", "Augusta", ua, ub)
    B.name(pa.get("name"), pb.get("name"))
    for w in re.findall(r"[A-Z][A-Za-z]+", f"{pa.get('name')} {pb.get('name')}"):
        B.name(w)
    fun = summ.get("funnel") or {}
    B.f("filings", int(fun.get("filings") or 2), "filings", count_text(int(fun.get("filings") or 2), lang))
    B.f("pages", int(fun.get("pages") or 0), "pages", num(int(fun.get("pages") or 0), lang))
    B.f("rows", int(fun.get("extracted") or 0), "records", num(int(fun.get("extracted") or 0), lang))
    B.f("passed", int(fun.get("passed") or 0), "records", num(int(fun.get("passed") or 0), lang))
    B.f("set_aside", int(fun.get("set_aside") or 0), "records", count_text(int(fun.get("set_aside") or 0), lang))
    B.f("checks", int(fun.get("checks") or 0), "checks", count_text(int(fun.get("checks") or 0), lang))
    B.f("flagged", int(opp.get("flagged") or 0), "pairs", count_text(int(opp.get("flagged") or 0), lang))
    B.f("max_km", float(opp["params"]["max_km"]), "km", f"{half_up(opp['params']['max_km'])} " + T(lang, "kilometers", "kilómetros"))
    for y in (2024, 2025, 2028, 2034):
        B.f(f"y{y}", y, "year", str(y), "the filings' titles")
    _srcs = summ.get("sources") or []
    src_desc = next((s for s in _srcs if s.get("id") == "desc_2026"), None) or next((s for s in _srcs if s.get("id") == "desc"), {})
    src_ga = next((s for s in summ.get("sources") or [] if s.get("id") == "ga_irp"), {})

    # all located projects of the two utilities in play: from the pair list (every project that appears in a flagged pair)
    def geo_items(p):
        coords = (p.get("geometry") or {}).get("coords") or []
        if len(coords) >= 2:
            return "line", {"path": [[c[1], c[0]] for c in coords], "label": p.get("name")}
        if coords:
            return "point", {"lat": coords[0][1], "lon": coords[0][0], "label": p.get("name")}
        return None, None

    la, lb, pa_pts, pb_pts, allpts = [], [], [], [], []
    seen = set()
    for o in opp.get("opportunities") or []:
        for key, lst_l, lst_p in (("project_a", la, pa_pts), ("project_b", lb, pb_pts)):
            p = o[key]
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            kind, it = geo_items(p)
            if kind == "line":
                lst_l.append(it)
                allpts += [tuple(x) for x in it["path"]]
            elif kind == "point":
                lst_p.append(it)
                allpts.append((it["lat"], it["lon"]))
    area = bounds_of(allpts, 0.4, 1.0)
    legend_a = T(lang, f"DESC {_years_in(src_desc.get('title'), '2024–2028')} filing", f"Documento de DESC {_years_in(src_desc.get('title'), '2024–2028')}")
    legend_b = T(lang, "Georgia Power 2025 IRP (GA ITS plan)", "Plan IRP 2025 de Georgia Power (plan GA ITS)")
    legend = [{"swatch": "a", "text": legend_a}, {"swatch": "b", "text": legend_b}]
    B.scene("open", T(lang, "Cold open", "Apertura"), area, [
        grid_layer("hidden"),
        title_layer(T(lang, "Build together", "Construir juntos"), T(lang, "Two utilities' public construction plans, one border", "Los planes públicos de obra de dos empresas, una frontera")),
        {"type": "quote", "text": src_desc.get("title") or "DESC 2024–2028", "source": src_desc.get("publisher") or "SCRTP"},
        {"type": "quote", "text": src_ga.get("title") or "Georgia Power 2025 IRP", "source": src_ga.get("publisher") or "Georgia PSC"},
    ], [
        ("presenter", T(lang, f"Two utilities, one border. Dominion Energy South Carolina and Georgia Power each publish their construction plans. We read both public filings, as filed.",
                        f"Dos empresas, una frontera. Dominion Energy South Carolina y Georgia Power publican sus planes de obra. Leímos ambos documentos públicos, tal como se presentaron."), []),
        ("analyst", T(lang, "The question: where do their projects meet, in place or in time, so crews could work once instead of twice?",
                      "La pregunta: ¿dónde se cruzan sus proyectos, en lugar o en tiempo, para que las cuadrillas trabajen una vez en vez de dos?"), []),
    ], 8500, brief="The two filings as sourced cards over the border region.",
        points=["two public filings, as filed", "where plans meet in place or time", "neutral: never imply the utilities fail to talk or coordinate; this is a tool that reads their public plans side by side"])

    B.scene("projects", T(lang, "The plans", "Los planes"), area, [
        grid_layer("hidden"),
        {"type": "lines", "style": "project_a", "animate": "draw", "stagger_ms": 150, "items": la},
        {"type": "points", "kind": "project", "style": "project_a", "animate": "appear", "stagger_ms": 300, "items": pa_pts},
        {"type": "lines", "style": "project_b", "animate": "draw", "stagger_ms": 300, "items": lb},
        {"type": "points", "kind": "project", "style": "project_b", "animate": "appear", "stagger_ms": 300, "items": pb_pts},
        {**lower(T(lang, "Planned projects, located from the filings' place names", "Proyectos planeados, ubicados por los lugares que nombran los documentos"),
                 T(lang, "Routes are straight lines between located ends: the real routes aren't public", "Las rutas son rectas entre extremos ubicados: las reales no son públicas")), "legend": legend},
    ], [
        ("presenter", T(lang, "Every project is placed on the map from the names in its filing, matched to substations on OpenStreetMap.",
                        "Cada proyecto se ubica en el mapa a partir de los nombres de su documento, emparejados con subestaciones de OpenStreetMap."), []),
        ("analyst", T(lang, "Where a route isn't public, we draw a straight line between its ends, and say so.",
                      "Donde la ruta no es pública, dibujamos una recta entre sus extremos, y lo decimos."), []),
    ], 8500, brief="The two utilities' projects draw themselves: blue for DESC's filing, lilac for Georgia Power's.",
        points=["the filings name places (substations, lines); the pipeline finds those names on OpenStreetMap: the filings carry NO coordinates", "honest geometry: straight lines where the route isn't public",
                "blue is DESC, lilac is Georgia Power"])

    B.scene("pipeline", T(lang, "The pipeline", "El proceso de datos"), area, [
        grid_layer("hidden"),
        {"type": "bars", "title": T(lang, "From filings to checked records", "De los documentos a registros comprobados"), "format": "count", "items": [
            {"label": T(lang, "Pages read", "Páginas leídas"), "value": int(fun.get("pages") or 0), "tone": "neutral"},
            {"label": T(lang, "Records extracted", "Registros extraídos"), "value": int(fun.get("extracted") or 0), "tone": "neutral"},
            {"label": T(lang, "Passed every check", "Pasaron cada control"), "value": int(fun.get("passed") or 0), "tone": "gain"},
            {"label": T(lang, "Set aside, with reasons", "Apartados, con motivos"), "value": int(fun.get("set_aside") or 0), "tone": "loss"},
        ]},
    ], [
        ("presenter", T(lang, f"Behind that map is a pipeline: {num(int(fun.get('pages') or 0), lang)} pages of filings, {num(int(fun.get('extracted') or 0), lang)} records pulled out, each with its page.",
                        f"Detrás de ese mapa hay un proceso: {num(int(fun.get('pages') or 0), lang)} páginas de documentos, {num(int(fun.get('extracted') or 0), lang)} registros extraídos, cada uno con su página."), ["pages", "rows"]),
        ("analyst", T(lang, f"{num(int(fun.get('passed') or 0), lang)} pass every check. {cap1(count_text(int(fun.get('set_aside') or 0), lang))} are set aside, each with its reason. Bad records never reach the map.",
                      f"{num(int(fun.get('passed') or 0), lang)} pasan cada control. {cap1(count_text(int(fun.get('set_aside') or 0), lang))} se apartan, cada uno con su motivo. Los registros malos nunca llegan al mapa."),
         ["passed", "set_aside"]),
    ], 8500, brief="The pipeline funnel as bars: pages, records, passed, set aside.", points=["provenance: every record keeps its page", "checks set bad records aside with reasons"])

    pair_lines, seen_cp = [], set()
    for o in opp.get("opportunities") or []:
        cp = o.get("closest_points") or []
        if len(cp) == 2:
            key = (tuple(cp[0]), tuple(cp[1]))
            # a pair that meets at one point (the same station) is drawn as a ring there; the same spot once
            pair_lines.append({"path": [list(cp[0]), list(cp[1])], "label": None if key in seen_cp else f"#{o.get('rank')}", "value": o.get("score")})
            seen_cp.add(key)
    fl_c = counter(T(lang, "Pairs flagged", "Pares señalados"), 0, int(opp.get("flagged") or 0), "count", "neutral")
    B.scene("overlaps", T(lang, "The overlaps", "Los solapes"), area, [
        grid_layer("hidden"),
        {"type": "lines", "style": "project_a", "animate": "none", "items": la},
        {"type": "points", "kind": "project", "style": "project_a", "animate": "none", "items": pa_pts},
        {"type": "lines", "style": "project_b", "animate": "none", "items": lb},
        {"type": "points", "kind": "project", "style": "project_b", "animate": "none", "items": pb_pts},
        {"type": "lines", "style": "corridor", "animate": "draw", "stagger_ms": 700, "items": pair_lines},
        fl_c,
        {**lower(T(lang, f"Pairs within {half_up(opp['params']['max_km'])} km of each other", f"Pares a menos de {half_up(opp['params']['max_km'])} km entre sí"),
                 T(lang, "Measured between the projects, not from the border", "Medido entre los proyectos, no desde la frontera")), "legend": legend},
    ], [
        ("presenter", T(lang, f"Then every project of one utility is measured against every project of the other. Within {half_up(opp['params']['max_km'])} kilometers, {count_text(int(opp.get('flagged') or 0), lang)} pairs are flagged.",
                        f"Luego cada proyecto de una empresa se mide contra cada proyecto de la otra. A menos de {half_up(opp['params']['max_km'])} kilómetros, se señalan {count_text(int(opp.get('flagged') or 0), lang)} pares."),
         ["max_km", "flagged"]),
        ("analyst", T(lang, "Each pair is ranked: how close, how close in time, and how sure we are of where they are.",
                      "Cada par se clasifica: qué tan cerca, qué tan cerca en el tiempo, y qué tan seguros estamos de dónde están."), []),
    ], 9000, brief="The closest-point connectors between flagged pairs draw themselves; the pairs counter climbs.", points=["pairs whose projects come within this distance OF EACH OTHER (not of the border)", "ranked by place, time, confidence"])

    ranked = (opp.get("opportunities") or [])[:6]
    if ranked:
        B.f("top_score", ranked[0].get("score"), "score", f"{ranked[0].get('score'):g}")
        tier_es = {"touching": "Deben coordinarse", "row": "Compartir el terreno", "site": "Compartir logística", "crews": "Compartir cuadrillas y equipos"}

        def pair_label(o: dict) -> str:
            ss_ = o.get("shared_station") or {}
            if ss_:
                circ = re.search(r"#(\d+)", str((o.get("project_b") or {}).get("name") or ""))
                return f"#{o.get('rank')} {ss_.get('name')}, " + T(lang, "same station", "misma subestación") + (f" (#{circ.group(1)})" if circ else "")
            tier = o.get("tier_label") if lang == "en" else tier_es.get(o.get("tier"), o.get("tier_label"))
            return f"#{o.get('rank')} {tier} · {half_up(o.get('distance_km') or 0)} km"

        rk_lines = []
        for o in ranked:
            if len(o.get("closest_points") or []) == 2:
                rk_lines.append({"path": [list(o["closest_points"][0]), list(o["closest_points"][1])], "label": f"#{o.get('rank')}"})
        B.scene("ranked", T(lang, "The ranking", "La clasificación"), bounds_of([tuple(p_) for it in rk_lines for p_ in it["path"]] or allpts, 0.25, 0.8), [
            grid_layer("hidden"),
            {"type": "lines", "style": "project_a", "animate": "none", "items": la},
            {"type": "lines", "style": "project_b", "animate": "none", "items": lb},
            {"type": "bars", "title": T(lang, "Top pairs in rank order (score 0-100)", "Mejores pares en orden (puntuación 0-100)"), "format": "count",
             "items": [{"label": pair_label(o), "value": o.get("score"), "tone": "gain" if o.get("shared_station") else "neutral"} for o in ranked]},
            {"type": "lines", "style": "corridor", "animate": "draw", "stagger_ms": 900, "items": rk_lines},
        ], [
            ("presenter", T(lang, "The ranking asks timing first: pairs building in the same months come first, then pairs building at different times, then pairs whose timing is unknown, and last the ones whose time has passed.",
                            "La clasificación mira primero el calendario: primero los pares que construyen en los mismos meses, luego los que construyen en momentos distintos, luego los de calendario desconocido y, al final, los que ya pasaron."), []),
            ("analyst", T(lang, "Inside each group, pairs at the very same substation go first, then the score: closer is worth more, and so is a build window that overlaps.",
                          "Dentro de cada grupo, primero van los pares en la misma subestación y luego la puntuación: más cerca vale más, y también una ventana de obra que coincide."), []),
        ], 8500, brief="The top pairs as bars in rank order (timing group first: same months, different times, unknown, passed; then same station, then score); each pair's ring or connector draws in rank order on the map.",
            points=["grouped by timing first: same months, different times, timing unknown, time passed", "within a group: same station first, then score", "score: distance, timeline, location confidence"])

    station = station if (station or {}).get("shared_station") else top
    ss = station.get("shared_station") or {}
    if ss:
        spa, spb, is_top = station["project_a"], station["project_b"], station is top
        B.name(spa.get("name"), spb.get("name"))
        for w in re.findall(r"[A-Z][A-Za-z]+", f"{spa.get('name')} {spb.get('name')}"):
            B.name(w)
        lead_en = "The top pair isn't just close." if is_top else "One pair isn't just close."
        lead_es = "El primer par no solo está cerca." if is_top else "Un par no solo está cerca."
        B.name(ss.get("name"), ss.get("osm_name"))
        yrs = []
        owner_name = {"a": ("DESC", "Dominion Energy South Carolina"), "b": ("Georgia Power", "Georgia Power")}
        for k in ("a_in_service", "b_in_service"):
            if ss.get(k):
                y = int(str(ss[k])[:4])
                yrs.append(y)
                short, full = owner_name[k[0]]
                # the year says whose it is: a line may never give one utility's filed date to the other (checked)
                B.f(f"svc_{k[0]}", y, "year", T(lang, f"{short}'s project, due in service in {y}", f"el proyecto de {short}, con entrada en servicio en {y}"),
                    "the filings, as filed")
                B.facts[f"svc_{k[0]}"]["owner"] = [short, full]
        B.f("months_apart", int(ss.get("months_apart") or 0), "months", num(int(ss.get("months_apart") or 0), lang))
        import datetime as _dt

        past = {k[0]: str(ss.get(k) or "9999")[:10] < _dt.date.today().isoformat() for k in ("a_in_service", "b_in_service")}
        who_past = "DESC" if past.get("a") else "Georgia Power" if past.get("b") else None
        pts = [(ss["lat"], ss["lon"])]
        for p in (spa, spb):
            kind, it = geo_items(p)
            if kind == "line":
                pts += [tuple(x) for x in it["path"]]
        B.scene("station", T(lang, "The same station", "La misma subestación"), bounds_of(pts, 0.08, 0.25), [
            grid_layer("hidden"),
            *([{"type": "lines", "style": "project_a", "animate": "draw", "items": [geo_items(spa)[1]]}] if geo_items(spa)[0] == "line" else [{"type": "points", "kind": "project", "style": "project_a", "animate": "appear", "items": [geo_items(spa)[1]]}]),
            *([{"type": "lines", "style": "project_b", "animate": "draw", "items": [geo_items(spb)[1]]}] if geo_items(spb)[0] == "line" else [{"type": "points", "kind": "project", "style": "project_b", "animate": "appear", "items": [geo_items(spb)[1]]}]),
            {"type": "points", "kind": "station", "animate": "pulse", "items": [{"lat": ss["lat"], "lon": ss["lon"], "label": ss.get("name"), "sub": T(lang, "both filings work here", "ambos documentos trabajan aquí")}]},
            {"type": "quote", "text": ss.get("reason") or "", "source": T(lang, "The two public filings, as filed", "Los dos documentos públicos, tal como se presentaron")},
        ], [
            ("presenter", T(lang, (f"{lead_en} Both filings work at the same substation: {ss.get('name')}. As filed, DESC's project there was due in service in {yrs[0]}, and Georgia Power's in {yrs[1]}."
                                   if len(yrs) == 2 else f"{lead_en} Both filings work at the same substation: {ss.get('name')}."),
                            (f"{lead_es} Ambos documentos trabajan en la misma subestación: {ss.get('name')}. Según lo presentado, el proyecto de DESC debía entrar en servicio en {yrs[0]}, y el de Georgia Power en {yrs[1]}."
                             if len(yrs) == 2 else f"{lead_es} Ambos documentos trabajan en la misma subestación: {ss.get('name')}.")),
             ["svc_a", "svc_b"]),
            ("analyst", (T(lang, f"{who_past}'s filed date has already passed, so whether any of its work is still ahead isn't in the filing. If it is, one outage plan and one design where they meet could serve both.",
                           f"La fecha presentada por {who_past} ya pasó, así que el documento no dice si le queda obra. Si le queda, un solo plan de cortes y un solo diseño donde se unen podrían servir a los dos.")
                         if who_past else
                         T(lang, "If both schedules can line up, one outage plan and one design where they meet could serve both.",
                           "Si los dos calendarios pueden coincidir, un solo plan de cortes y un solo diseño donde se unen podrían servir a los dos.")), []),
        ], 9000, brief="Zoom on the shared substation: both projects meet there; the reason as a sourced quote.",
            points=[("the top-ranked pair" if is_top else "NOT the top-ranked pair (its filed time has passed, so the ranking puts it last): say 'one pair', never 'the top pair'"), "same substation in both filings", "in-service years AS FILED: DESC's is the earlier one (svc_a), Georgia Power's the later (svc_b); never swap them",
                    (f"{who_past}'s filed in-service date has already passed: whether any of its work is still ahead is NOT known; say 'if', never 'both still have work ahead'"
                     if who_past else "say 'if' the schedules line up; never state that both have work ahead"),
                    "could coordinate (never 'are'/'will')"], min_km=18)

    turns = neg.get("turns") or []
    agent = []
    names = {"a": "DESC", "b": "Georgia Power"}
    for t in turns:
        who = names.get(t.get("agent"), "?")  # the agent that reads this utility's filing: never the utility itself
        actor = "gemini" if neg.get("by") == "gemini" else "engine"
        kind = {"propose": "propose", "counter": "propose", "accept": "accept", "revise": "propose"}.get(t.get("kind"), "propose")
        note = str((t.get("proposal") or {}).get("note") or "")
        agent.append({"actor": actor, "kind": kind, "text": f"{T(lang, 'Agent reading', 'Agente que lee')} {who}: {note}"[:220]})
        v = t.get("verdict")
        ok = v.get("ok") if isinstance(v, dict) else None
        agent.append({"actor": "engine", "kind": "verify" if ok is not False else "reject",
                      "text": T(lang, "Pipeline check against both filings: " + ("passed" if ok is not False else "rejected, sent back"), "Control contra ambos documentos: " + ("aprobado" if ok is not False else "rechazado, devuelto"))})
    B.agent_steps += agent
    oc = neg.get("outcome") or {}
    terms = oc.get("terms") or {}
    split = terms.get("split") or {}
    shares = split.get("shares") or []
    B.f("rounds", int(oc.get("round") or 0), "rounds", count_text(int(oc.get("round") or 0), lang))
    if len(shares) == 2:
        B.f("share_a", shares[0], "%", pct_text(shares[0], lang))
        B.f("share_b", shares[1], "%", pct_text(shares[1], lang))
    by_ai = neg.get("by") == "gemini"
    B.scene("negotiate", T(lang, "Two agents compare the plans", "Dos agentes comparan los planes"), area, [
        grid_layer("hidden"),
        {"type": "agent", "title": T(lang, "Two AI agents, each reading one filing" if by_ai else "Two rule-based agents (plain version)", "Dos agentes de IA, cada uno lee un documento" if by_ai else "Dos agentes con reglas (versión simple)"), "steps": agent},
    ], [
        ("presenter", T(lang, "Now two agents each read one utility's published plan. They exchange proposals: a joint window, the shared work, the cost split.",
                        "Ahora dos agentes leen, cada uno, el plan publicado de una empresa. Intercambian propuestas: una ventana conjunta, el trabajo compartido, el reparto de costos."), []),
        ("analyst", T(lang, f"They're not the utilities, and they can't speak for them. Every turn is checked against both filings before the other side sees it. They land on the same proposal in round {count_text(int(oc.get('round') or 0), lang)}.",
                      f"No son las empresas, y no pueden hablar por ellas. Cada turno se comprueba contra ambos documentos antes de que el otro lo vea. Llegan a la misma propuesta en la ronda {count_text(int(oc.get('round') or 0), lang)}."),
         ["rounds"]),
    ], 11000, brief="The agents' exchange as a live trace: proposals, counter-proposals, the pipeline's check after every turn, the acceptance.",
        points=["agents read filings, not the utilities: never say a utility agrees, accepts, pays or commits; say 'the agent reading DESC's filing'", "every turn verified", "the round they settle in"])

    scope = terms.get("scope") or []
    lo_sum = sum(float(s.get("low") or 0) for s in scope)
    hi_sum = sum(float(s.get("high") or 0) for s in scope)
    piece_en, parte_es = ("piece", "parte") if len(scope) == 1 else ("pieces", "partes")  # 'share one piece', not 'one pieces'
    if scope:
        B.f("save_low", lo_sum, "USD", usd_text(lo_sum, lang) if lo_sum >= 1000 else T(lang, "$0", "0 dólares"), "the rough estimate's sources", hedge=True)
        B.f("save_high", hi_sum, "USD", usd_text(hi_sum, lang), "the rough estimate's sources", hedge=True)
        B.costs += ["save_low", "save_high"]
    items_es = {"mobilization": "Movilizar cuadrillas y equipos una vez", "laydown_yard": "Un solo patio de acopio", "access_roads": "Un solo camino de acceso", "crossing": "Estructuras donde se unen, diseñadas una vez"}
    B.scene("agreement", T(lang, "The agreement", "El acuerdo"), area, [
        grid_layer("hidden"),
        {"type": "bars", "title": T(lang, "What they could share (high end of the rough estimate)", "Lo que podrían compartir (extremo alto de la estimación)"), "format": "usd",
         "items": [{"label": s.get("label") if lang == "en" else items_es.get(s.get("id"), s.get("label")), "value": round(float(s.get("high") or 0)), "tone": "gain"} for s in scope]},
        *([compare("neutral", ("DESC", f"{half_up(shares[0])}%", T(lang, "of shared costs", "de los costos compartidos")),
                   ("Georgia Power", f"{half_up(shares[1])}%", T(lang, "of shared costs", "de los costos compartidos")))] if len(shares) == 2 else []),
    ], [
        ("presenter", (T(lang, f"The drafted terms: share {count_text(len(scope), lang)} {piece_en} of work, split by each project's filed length: {pct_text(shares[0], lang)} on DESC's side, {pct_text(shares[1], lang)} on Georgia Power's.",
                         f"Los términos redactados: compartir {count_text(len(scope), lang, fem=True)} {parte_es} del trabajo, {'repartida' if len(scope) == 1 else 'repartidas'} según la longitud presentada de cada proyecto: {pct_text(shares[0], lang)} del lado de DESC, {pct_text(shares[1], lang)} del de Georgia Power.")
                       if len(shares) == 2 and split.get("rule") == "by_length" else
                       T(lang, f"The drafted terms: share {count_text(len(scope), lang)} {piece_en} of work, with the cost split both agents accepted.",
                         f"Los términos redactados: compartir {count_text(len(scope), lang, fem=True)} {parte_es} del trabajo, con el reparto que ambos agentes aceptaron.")),
         ["share_a", "share_b"]),
        ("analyst", T(lang, f"A rough estimate of what that could save: up to {usd_text(hi_sum, lang)}, if the schedules line up. A draft for people to discuss, not a decision.",
                      f"Una estimación aproximada de lo que podría ahorrar: hasta {usd_text(hi_sum, lang)}, si los calendarios coinciden. Un borrador para discutir, no una decisión."),
         ["save_high"]),
    ], 9500, brief="Bars of the shared items (high end of the rough estimate); the split as a split-screen.",
        points=["the drafted scope and split (a draft the agents wrote, never what a utility agreed or pays)", "savings as a rough range, high end (say 'up to')", "a draft, not a decision"])

    B.scene("close", T(lang, "Bottom line", "En resumen"), area, [
        grid_layer("hidden"),
        {"type": "lines", "style": "project_a", "animate": "none", "items": la},
        {"type": "points", "kind": "project", "style": "project_a", "animate": "none", "items": pa_pts},
        {"type": "lines", "style": "project_b", "animate": "none", "items": lb},
        {"type": "points", "kind": "project", "style": "project_b", "animate": "none", "items": pb_pts},
        title_layer(T(lang, "Plans that could meet, found before the crews roll", "Planes que podrían coincidir, encontrados antes de que salgan las cuadrillas"),
                    T(lang, "Public filings only · what two utilities could coordinate", "Solo documentos públicos · lo que dos empresas podrían coordinar")),
    ], [
        ("presenter", T(lang, "Two public filings, one pipeline, and a short list of places where two utilities could build once instead of twice.",
                        "Dos documentos públicos, un proceso de datos, y una lista corta de lugares donde dos empresas podrían construir una vez en vez de dos."), []),
    ], 7000, brief="Both plans on the map; the bottom line.", points=["public filings only", "could coordinate"])
    return B


# ------------------------------------------------------------------------------------------------ the checker
_WILL = re.compile(r"\b(will|won't|going to|is set to|are set to)\b", re.I)
_WILL_ES = re.compile(r"\b(causará|provocará|fallará|construirá|hará|harán|va a|van a|será|serán)\b", re.I)
_BLAME = re.compile(
    r"\b(fault|blame\w*|negligen\w*|reckless\w*|irresponsib\w*|greed\w*|cover-?up|ignor\w*|responsib\w*|should have|ought to have|to blame"
    r"|culpa\w*|irresponsab\w*|codici\w*|responsab\w*|debi[óe]ron haber|debi[óo] haber|deber[ií]an? haber)\b"
    r"|\b(put|puts|putting|places?|placing|placed|leaves?|leaving|left)\b[^.]{0,40}\bat risk\b|\bpon\w*\b[^.]{0,40}\ben riesgo\b", re.I)
# a model is not the world: no claims about the real grid, no "this is not a simulation", no dates in the real future
_REAL = re.compile(r"\b(real|actual)\s+(?:[A-Z][\w&-]*['’]s\s+)?(?:power\s+|electric(?:al)?\s+)?(grids?|networks?|lines?|utilit(?:y|ies)|systems?|transmission)\b"
                   r"|\b(red|redes|l[ií]neas?|empresas?|sistemas?)\s+(?:el[ée]ctricas?\s+)?reales?\b", re.I)
_NEG = re.compile(r"\b(not?|never|nor|isn['’]?t|aren['’]?t|wasn['’]?t|instead of|rather than|ni|nunca)\b|n['’]t\b", re.I)
_NOT_SIM = re.compile(r"\bnot (just )?an? (simulation|model|drill|test|hypothetical|exercise)\b|\bthis is real\b|\b(shows?|is) (the )?reality\b|\bin reality\b|\bla realidad\b|\b(first|within|in|few|these) (seconds|minutes)\b|\b(primeros|en) (segundos|minutos)\b|\b(next|this) (summer|year|week|month|winter)\b|\bright now\b"
                      r"|\bno es (una? )?(simulaci[oó]n|modelo|simulacro)\b|\b(el )?pr[oó]ximo (verano|a[ñn]o|invierno)\b|\beste (verano|a[ñn]o|invierno)\b|\bahora mismo\b|\ben este momento\b", re.I)
# a model shows, it never proves; sober, not alarm
_OVERCLAIM = re.compile(r"\bprov(e|es|ed|en|ing)\b|\bguarantee\w*|\bdemuestra\w*|\bgarantiz\w*|\blife[- ](and|or)[- ]death\b|\btrapped\b|\batrapad\w*|\bvida o muerte\b"
                        r"|\bcatastroph\w*|\bcatastr[oó]f\w*|\bapocalyp\w*|\bapocal[ií]p\w*|\bdoom\w*|\bterrif\w*|\baterra\w*", re.I)
# real utilities and power companies the facts don't carry (anywhere in a line, even as its first word)
_ORGS = re.compile(r"\b(Duke|Entergy|Xcel|Ameren|Exelon|Oncor|CenterPoint|NextEra|Vistra|Talen|Constellation|Dominion|Tampa Electric|Florida Power|Gulf Power|Southern Company|"
                   r"Georgia Power|Georgia Transmission|MEAG|Dalton Utilities|PG&E|Con Edison|ConEd|National Grid|Eversource|FirstEnergy|Evergy|PacifiCorp|Avangrid|Santee Cooper|OUC|Lakeland Electric)\b")
_HEDGE = re.compile(r"\b(about|around|roughly|nearly|almost|up to|as many as|as much as|some|an estimated|estimated?|estimates?|approximately|high end|at most|on the long side"
                    r"|unos|unas|hasta|alrededor|cerca de|casi|aproximadamente|estimad[oa]s?|estimaci[oó]n|estimaciones|extremo alto|del lado alto)\b", re.I)
# 'people hit' (everyone the failed lines' power flowed on to) is not 'people without power'
_OUT_WORDS = re.compile(r"\b(los(?:e|es|ing)|lost|without (?:power|electricity|the lights)|cut(?:s|ting)? off|in the dark|outages?|pierde\w*|perdi\w*|sin (?:luz|electricidad|energ[ií]a|servicio)"
                        r"|a oscuras|se quedan? sin)\b", re.I)
# Build together: a line never speaks for a utility (the agents read the filings, they are not the utilities)
_UTIL = r"(?:DESC|Dominion(?: Energy South Carolina)?|Georgia Power|Georgia Transmission|MEAG|Dalton Utilities)"
_UTIL_RE = re.compile(r"\b" + _UTIL + r"\b")
_SPEAKS_FOR = re.compile(
    r"\b" + _UTIL + r"(?:['’]s)?\s+(?!(?:agent|agents|filing|filings|plan|plans|document|documents|project|projects|record|records)\b)(?:[\w-]+\s+){0,2}?"
    r"(agree\w*|accept\w*|pay|pays|paying|paid|settle\w*|commit\w*|should|must|ignor\w*|fail\w*|refus\w*|wast\w*|neglect\w*|promis\w*|decid\w*|approv\w*|sign\w*"
    r"|acept\w*|acuerd\w*|acord\w*|paga|pagan|pagar\w*|pag[oó]|compromet\w*|debe|deben|deber[ií]a\w*|incumpl\w*|rechaz\w*|desperdici\w*|descuid\w*|promet\w*|decid\w*|aprueb\w*|firm\w*)\b")
_AHEAD = re.compile(r"\b(work|construction|building)\s+(still\s+)?(ahead|to come|remaining|left)\b|\bstill (to )?(build|come)\b|\bqueda\w*\s+(de\s+)?obra\b|\bobra pendiente\b", re.I)
_IF = re.compile(r"\b(if|whether|unless|si)\b", re.I)
_ABBR = re.compile(r"\b(MW|MVA|GW|kV|kW|kWh|MWh)\b|%")
_FORBIDDEN_TOGETHER = re.compile(
    r"\b(FPL|TECO|JEA|ERCOT|FERC|NERC|CAISO|PJM|MISO|NYISO|FEMA|Duke Energy|Santee Cooper|Southern Company|Oglethorpe)\b"
    r"|(?i:\bemergency alert\b|\bthis is not a test\b|evacua|alerta de emergencia)"
)
_CAPWORD = re.compile(r"(?<![\w'])([A-Z][a-zA-Z&'-]+(?:\s+[A-Z][a-zA-Z&'-]+)*)")
_COMMON_CAPS = set(
    "the a an and but so now then this that these those it its it's here there what when where why how who one two three four five six seven "
    "eight nine ten every each even just only first next last today tonight watch look see and or if not no yes then still meanwhile "
    "in on at by for from with without to of as after before by end finally also plus once instead not we our you your they their "
    "i ai gemini campus campuses step steps line lines wave waves grid power "
    "el la los las un una unos unas y o pero así ahora luego este esta estos estas eso esto aquí allí qué cuando donde dónde cómo quién "
    "cada incluso solo primero siguiente último hoy mira miren vean en con sin para de del al por tras antes después también "
    "nosotros ustedes ellos ia paso pasos línea líneas oleada red energía entonces y sí no".split()
)


def scene_fact_ids(B: Board, sc: dict) -> list[str]:
    """The facts a scene's lines may say: the ones its template uses plus its extra facts."""
    out = [f for t in sc["template"] for f in t["facts"]] + list(sc.get("extra_facts") or [])
    return [f for f in dict.fromkeys(out) if f in B.facts]


def _allowed_numbers(B: Board, fids: list[str] | None = None) -> set[str]:
    out: set[str] = set()
    for f in (B.facts.values() if fids is None else [B.facts[x] for x in fids]):
        v = f["value"]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            out |= num_forms(v)
    return out


def _known_number_words(B: Board, lang: str, fids: list[str] | None = None) -> set[str]:
    out: set[str] = set()
    for f in (B.facts.values() if fids is None else [B.facts[x] for x in fids]):
        v = f["value"]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            if 0 <= v <= 99 and float(v).is_integer():
                n = int(v)
                out |= {words(n, lang), words(n, lang, fem=True), words(n, lang, before_noun=True), ordinal(n, lang)}
                if lang == "es" and n == 3:
                    out |= {"tercer", "tercera"}
            if v >= 1000:
                out.add("thousand" if lang == "en" else "mil")
            if v >= 1e6:
                out |= {"million", "millions", "millón", "millones"}
            if v >= 1e9:
                out |= {"billion"}
        t = str(f.get("text") or "")
        out |= set(re.findall(r"[a-záéíóúñü]+", t.lower()))
    return {w for x in out for w in re.findall(r"[a-záéíóúñü]+", x.lower())}


def _mask(B: Board, text: str) -> str:
    for n in sorted(B.names, key=len, reverse=True):
        if n:
            text = re.sub(r"(?<![\w])" + re.escape(n) + r"(?![\w])", "Placename", text, flags=re.IGNORECASE)
    return re.sub(r"\b(circuit|unit|circuito|unidad|campus|step|paso|wave|oleada|round|ronda) \d+\b", r"\1 N", text, flags=re.IGNORECASE)


def _num_re() -> re.Pattern:
    return re.compile(r"\d[\d,]*(?:\.\d+)?")


def check_line(B: Board, text: str, lang: str, allowed: set[str], known_words: set[str], scene_template: str = "",
               fids: list[str] | None = None) -> tuple[bool, str | None, int, list[str]]:
    """(ok, why, numbers checked, fact ids the line's numbers match). The deterministic checker every Gemini line
    passes before it is shown."""
    if not isinstance(text, str) or len(text.strip()) < 8:
        return False, "empty line", 0, []
    text = text.strip()
    if len(text) > LINE_MAX[lang]:
        return False, f"too long ({len(text)} characters, the limit is {LINE_MAX[lang]})", 0, []
    low = f" {text.lower()} "
    es_marks = sum(t in low for t in (" el ", " la ", " los ", " las ", " que ", " del ", " una ", " se ", "ción", "ñ"))
    es_more = es_marks + sum(t in low for t in (" de ", " en ", " un ", " es ", " y ", " con ", " son ", " por ", "ó", "á", "í", "é"))
    if lang == "es" and es_more == 0:
        return False, "not Spanish", 0, []
    if lang == "en" and es_marks >= 2:
        return False, "not English", 0, []
    if lang == "es":
        m0 = re.search(r"\b(the|line|lines|and|with|of)\b", _mask(B, text))
        if m0:
            return False, f"an English word in a Spanish line: {m0.group(0)!r}", 0, []
    if _ABBR.search(text):
        return False, "an abbreviation the voice would read letter by letter (write megawatts, percent)", 0, []
    masked = _mask(B, text)
    forb = _FORBIDDEN_TOGETHER if B.episode == "together" else FORBIDDEN_LOCAL
    # a four-digit quantity ('2031 megavatios', Spanish writes no separator under 10,000) is not a year
    unyear = re.sub(r"(?<![0-9.,])[0-9]{4}(?=\s+(?:megavatios|megawatts|personas|people|páginas|pages|registros|records|líneas|lines|dólares|kilómetros|kilometers))", "N", masked)
    m = forb.search(unyear)
    if m:
        return False, f"forbidden: {m.group(0)!r} (a real organization, a named storm, a year or alert wording)", 0, []
    if llm.scrub_names(masked) != masked:
        return False, "names a real company, utility or project the facts don't carry", 0, []
    if (_WILL if lang == "en" else _WILL_ES).search(text):
        return False, "a prediction ('will'): use the conditional or the present for the model", 0, []
    m1 = re.search(r"(?<![-\w])(?<!each )(?<!every )\bone (rounds|steps|lines|campuses|sites|waves|pairs|upgrades|hospitals|towns|transformers|outages|records|pages|filings|years|months|hours|days|agents|utilities)\b", text, re.I) if lang == "en" else None
    if m1:
        return False, f"a count that isn't a fact: {m1.group(0)!r}", 0, []
    m2 = _BLAME.search(text)
    if m2:
        return False, f"blame or accusation wording: {m2.group(0)!r}", 0, []
    m2 = _OVERCLAIM.search(text)
    if m2:
        return False, f"overclaim or alarm wording: {m2.group(0)!r} (a model shows, it never proves; keep it sober)", 0, []
    m2 = _ORGS.search(masked)
    if m2:
        return False, f"names a real utility or company the facts don't carry: {m2.group(0)!r}", 0, []
    if B.episode != "together":
        m2 = _NOT_SIM.search(text)
        if m2:
            return False, f"claims about the real world or the real future: {m2.group(0)!r} (everything here is a synthetic model)", 0, []
        for m2 in _REAL.finditer(text):
            sent_start = max(text.rfind(c, 0, m2.start()) for c in ".!?:;")
            if not _NEG.search(text[sent_start + 1: m2.start()]):
                return False, f"a claim about the real grid: {m2.group(0)!r} (this is a synthetic model; only 'not the real grid' is allowed)", 0, []
    else:
        m2 = _SPEAKS_FOR.search(text)
        if m2:
            return False, f"speaks for a utility: {m2.group(0)!r} (the agents read the filings; a utility never agrees, pays, accepts or fails here)", 0, []
        for sent in re.split(r"(?<=[.!?])\s+", text):
            if _AHEAD.search(sent) and not _IF.search(sent):
                return False, "states that work is still ahead as a fact: say 'if' (the filings don't say it)", 0, []
        why = _owner_years(B, text)
        if why:
            return False, why, 0, []
    for name in TRANSLATED_NAMES.get(lang, ()):
        if name.lower() in low:
            return False, f"a translated place name: {name}", 0, []
    # proper nouns: every capitalized word not at a sentence start must be a known name or a common word
    for sent in re.split(r"(?<=[.!?:;—])\s+", masked):
        toks = sent.split()
        for i, tok in enumerate(toks):
            w = re.sub(r"[’']s$", "", re.sub(r"[^\wáéíóúñü&'’-]", "", tok)).strip("'’-")
            if i == 0 or not w or not w[:1].isupper() or w == "Placename" or w.lower() in _COMMON_CAPS:
                continue
            if w.isupper() and len(w) <= 4 and w.lower() in ("ai", "ia", "n"):
                continue
            return False, f"a name the facts don't carry: {w}", 0, []
    known = known_words | number_words(scene_template, lang)
    extra = number_words(masked, lang) - known
    if extra:
        return False, f"a spelled number not in the facts: {sorted(extra)}", 0, []
    toks = [t.replace(",", "").rstrip(".") for t in _num_re().findall(masked)]
    for t in toks:
        norm = (f"{float(t):.2f}".rstrip("0").rstrip(".")) if "." in t else t
        if t not in allowed and norm not in allowed:
            return False, f"a number that isn't one of this scene's facts: {t}", len(toks), []
    matched = attribute(B, text, toks, lang, fids)
    # estimates are said as estimates: people and the incident's money take 'about', 'up to', 'an estimated'...
    hedged = [f for f in matched if B.facts.get(f, {}).get("hedge")]
    if hedged and not _HEDGE.search(text):
        return False, f"says an estimate ({B.facts[hedged[0]]['text']}) as an exact figure: add 'about', 'up to' or 'an estimated'", len(toks), []
    # people HIT is not people WITHOUT POWER: a clause that says 'lose power' / 'cut off' / 'outage' may not carry a hit figure
    for clause in re.split(r"[,;:—–]|\b(?:and|while|whereas|but|y|mientras|pero)\b", _mask(B, text)):
        if not _OUT_WORDS.search(clause):
            continue
        ctoks = [t.replace(",", "").rstrip(".") for t in _num_re().findall(clause)]
        if not ctoks:
            continue
        cf = attribute(B, clause, ctoks, lang, fids)
        kinds = {B.facts[f].get("kind") for f in cf if f in B.facts}
        if "hit" in kinds and "out" not in kinds:
            return False, "says the people HIT 'lose power' (or are cut off, in an outage): that figure is people hit; the people without power are a separate, smaller fact", len(toks), []
    return True, None, len(toks), matched


def _owner_years(B: Board, text: str) -> str | None:
    """Build together: each utility's filed year stays with that utility. For every filed year in the line, the utility
    named nearest before it in its sentence (else the nearest after it) must be the year's owner."""
    owned = [(int(f["value"]), f["owner"]) for f in B.facts.values() if f.get("owner") and isinstance(f.get("value"), int)]
    if len(owned) < 2 or len({y for y, _ in owned}) < 2:
        return None
    for sent in re.split(r"(?<=[.!?])\s+", text):
        mentions = [(m.start(), m.group(0)) for m in _UTIL_RE.finditer(sent)]
        if not mentions:
            continue
        for y, owner in owned:
            for m in re.finditer(r"(?<!\d)" + str(y) + r"(?!\d)", sent):
                before = [x for x in mentions if x[0] < m.start()]
                after = [x for x in mentions if x[0] > m.start()]
                who = before[-1][1] if before else after[0][1] if after else None
                if who is None:
                    continue
                if not any(who == o or who.startswith(o) or o.startswith(who) for o in owner):
                    return f"gives {owner[0]}'s filed year ({y}) to {who}: as filed, {y} is {owner[0]}'s"
    return None


def attribute(B: Board, text: str, toks: list[str], lang: str, fids: list[str] | None = None) -> list[str]:
    """The facts a line's numbers say: for each number, the facts it prints exactly (whole, one decimal, or the digits
    of the fact's own text), else any fact whose rounding forms it matches; spelled counts ('seventy-three') match
    integer facts."""
    out: list[str] = []
    nums = [(f"{float(t):.2f}".rstrip("0").rstrip(".") if "." in t else t) for t in toks]
    numeric = [(fid, f["value"]) for fid, f in B.facts.items()
               if (fids is None or fid in fids) and isinstance(f["value"], (int, float)) and not isinstance(f["value"], bool) and math.isfinite(f["value"])]
    for t in nums:
        strict = [fid for fid, v in numeric
                  if t in {str(half_up(abs(v))), f"{abs(v):.1f}".rstrip("0").rstrip("."), *[x.replace(",", "") for x in _num_re().findall(str(B.facts[fid]["text"]))]}]
        loose = strict or [fid for fid, v in numeric if t in num_forms(v)]
        out += [f for f in loose if f not in out]
    low = fold(text)
    for fid, v in numeric:
        if 2 <= v <= 99 and float(v).is_integer() and fid not in out:
            ws = {fold(words(int(v), lang)), fold(words(int(v), lang, fem=True)), fold(count_text(int(v), lang))}
            if any(re.search(r"(?<![a-z-])" + re.escape(w) + r"(?![a-z-])", low) for w in ws if w):
                out.append(fid)
    return out


def check_scene_lines(B: Board, sc: dict, lines: list[dict], lang: str, first: bool, allowed=None, known=None) -> tuple[bool, str | None, int, list[dict]]:
    """A scene's Gemini lines: 1-3 of them, speakers presenter/analyst, every line checked; the first scene names the
    synthetic model (Build together: the public filings)."""
    if not isinstance(lines, list) or not (1 <= len(lines) <= 3):
        return False, "a scene needs 1 to 3 lines", 0, []
    tmpl = " ".join(t["text"] for t in sc["template"])
    fids = scene_fact_ids(B, sc)
    allowed, known = _allowed_numbers(B, fids), _known_number_words(B, lang, fids)  # numbers are scoped to the scene
    out, n = [], 0
    for ln in lines:
        if not isinstance(ln, dict):
            return False, "a line is not an object", n, []
        sp = ln.get("speaker")
        if sp not in ("presenter", "analyst"):
            return False, f"unknown speaker {sp!r}", n, []
        text = re.sub(r"\s+", " ", str(ln.get("text") or "")).strip().strip('"“”')
        ok, why, k, matched = check_line(B, text, lang, allowed, known, tmpl, fids)
        n += k
        if not ok:
            return False, why, n, []
        for rx in list(B.forbid) + list(sc.get("forbid") or []):
            m = re.search(rx, text, re.I)
            if m:
                return False, f"says {m.group(0)!r}, which this scene's facts contradict", n, []
        same = any(difflib.SequenceMatcher(None, fold(text), fold(t["text"])).ratio() >= 0.9 for t in sc["template"])
        out.append({"speaker": sp, "text": text, "facts": matched, "same_as_template": same})
    if first:
        whole = fold(" ".join(x["text"] for x in out))
        need = ("public filing", "filings", "documento", "publica", "público", "publico") if B.first_rule == "filings" else ("synthetic", "sintetic")
        if not any(fold(w) in whole for w in need):
            return False, ("the first scene must say these are the utilities' public filings" if B.first_rule == "filings" else "the first scene must say the grid is a synthetic model"), n, []
    return True, None, n, out


# ------------------------------------------------------------------------------------------------ the director (Gemini)
SYSTEM = """You are the director and head writer of a documentary map explainer, in the style of Vox, Bloomberg Originals or Johnny Harris map sequences. Two voices narrate: the PRESENTER (leads, sets the scene, tells the viewer what to watch on the map) and the ANALYST (adds the why and the stakes). The camera flies over a map while lines draw themselves, blackouts spread, counters leap.

Your job: go through every detail of the storyboard with your tools, then write the narration, scene by scene.

Rules the engine checks on every line (a failing scene is replaced by the plain script):
- Only numbers from THIS scene's facts (get_scene lists them), each keeping the meaning its "meaning" line gives it: never attach a number to another subject, and never say a fix worked when the facts say it failed. Write them the way the fact's "text" writes them ("1.6 million people", "$431 million", "378 percent", "5 gigawatts"). Spell units out: megawatts, gigawatts, percent, kilometers. Never MW, GW, MVA, kV or %.
- Small counts may be words ("five campuses") only when that count is a fact.
- No names except the places, lines and organizations your tools return. Never name a real utility, company or storm that the facts don't carry.
- Everything about the grid is a SYNTHETIC model (Breakthrough Energy / Texas A&M); the first scene must say so. In the Build together episode the plans are two utilities' PUBLIC FILINGS, as filed: the first scene must say "public filings"; say what they "could" coordinate, never what they "will" do.
- Never write "will" (use the present for the model's events, "could" or "would" for anything real). No blame, no accusations ("ignored", "responsible", "should have"), no alarm wording ("life and death", "trapped", "catastrophic", evacuation or emergency-alert language). A model SHOWS, it never "proves".
- Never claim anything about the real grid, a real utility or the real future ("the real grid", "not a simulation", "next summer", "right now"). Everything happens on the model.
- People and money are estimates: say "about", "up to" or "an estimated" in the same line, and keep the high end the facts give.
- "People hit" (everyone the failed lines' power flowed on to) is NOT "people without power": never say a people-hit figure "lose power", are "cut off" or are "in an outage". Only facts whose meaning is "without power" may be said that way.
- Build together: the agents READ the filings; they are not the utilities. Never write that a utility agrees, accepts, pays, commits, should, must or failed. Each utility's filed year belongs to that utility: never swap them.
- Each scene: two lines, the PRESENTER then the ANALYST (one line only for a title card; three at most), each under 200 characters (English) or 230 (Spanish). Short, punchy sentences. The first scene opens with a hook. Say what matters and what to watch on the map, not everything at once. The analyst adds the why, from the talking points: never invent a cause, a reason or a timing the facts don't give.
- Write it fresh, in your own words: a story with momentum from scene to scene (callbacks, a turn, a payoff), not a list of facts read out. The presenter is warm and urgent; the analyst is calm, precise, a little dry.
"""

TOOLS = [
    {"name": "get_scene", "description": "One scene of the storyboard: its chapter, what the map shows, the talking points to cover, the facts it uses (with the exact text to say them) and the names you may use.",
     "parameters": {"type": "object", "properties": {"scene_id": {"type": "string"}}, "required": ["scene_id"]}},
    {"name": "get_fact", "description": "One fact by id: its value, unit, how to say it, and its source.",
     "parameters": {"type": "object", "properties": {"fact_id": {"type": "string"}}, "required": ["fact_id"]}},
    {"name": "towns_hit", "description": "The towns that lose power in a scene, in the order they go dark, with the people in each (estimates).",
     "parameters": {"type": "object", "properties": {"scene_id": {"type": "string"}}, "required": ["scene_id"]}},
    {"name": "line_detail", "description": "Details of the lines and transformers the show names (the first to fail, the weak points, the upgrades): loading, ratings, costs. Optional filter by words in the label.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "cost_breakdown", "description": "Every money and time fact of the episode, with its source.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "agent_turns", "description": "Every step of the agents shown in the episode (the siting agent, the fix search, Gemini's challenge, the two negotiating agents) with who acted and what the engine decided.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "check_draft", "description": "Run the engine's own checker on your draft lines for ONE scene before you commit to them: every number against the scene's facts, the names, the wording rules. Answers ok, or exactly what to fix. Check every scene's draft (all in one turn, in parallel), fix what fails, then write the JSON.",
     "parameters": {"type": "object", "properties": {"scene_id": {"type": "string"},
                                                     "lines": {"type": "array", "items": {"type": "object", "properties": {"speaker": {"type": "string", "enum": ["presenter", "analyst"]}, "text": {"type": "string"}}, "required": ["speaker", "text"]}}},
                    "required": ["scene_id", "lines"]}},
]

SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"},
        "lines": {"type": "array", "items": {"type": "object", "properties": {"speaker": {"type": "string", "enum": ["presenter", "analyst"]}, "text": {"type": "string"}}, "required": ["speaker", "text"]}},
    }, "required": ["id", "lines"]}}},
    "required": ["scenes"],
}


def _tool(B: Board, name: str, args: dict) -> dict:
    by_id = {s["id"]: s for s in B.scenes}
    if name == "check_draft":
        sc = by_id.get(str(args.get("scene_id") or ""))
        if sc is None:
            return {"error": "unknown scene id", "scene_ids": list(by_id)}
        lines = args.get("lines")
        ok, why, n, _out = check_scene_lines(B, sc, lines if isinstance(lines, list) else [], B.lang, sc is B.scenes[0], None, None)
        return {"scene_id": sc["id"], "ok": ok, "numbers_checked": n, **({} if ok else {"fix": why})}
    if name == "get_scene":
        sc = by_id.get(str(args.get("scene_id") or ""))
        if sc is None:
            return {"error": "unknown scene id", "scene_ids": list(by_id)}
        fids = scene_fact_ids(B, sc)
        return {"id": sc["id"], "chapter": sc["chapter"], "on_the_map": sc["brief"], "talking_points": sc["points"],
                "layers": [ly["type"] + (f":{ly.get('style') or ly.get('kind') or ''}" if ly.get("style") or ly.get("kind") else "") for ly in sc["layers"]],
                "facts": [{"id": f, **B.facts[f], "meaning": next((t["text"] for t in sc["template"] if f in t["facts"]), None)} for f in fids],
                "counts_you_may_spell_out": sorted({count_text(int(B.facts[f]["value"]), B.lang) for f in fids
                                                    if isinstance(B.facts[f]["value"], (int, float)) and 0 <= B.facts[f]["value"] <= 99 and float(B.facts[f]["value"]).is_integer()}),
                "names_you_may_use": sorted(n for n in B.names if n and len(n) < 60)[:80]}
    if name == "get_fact":
        f = B.facts.get(str(args.get("fact_id") or ""))
        return {"id": args.get("fact_id"), **f} if f else {"error": "unknown fact id", "fact_ids": list(B.facts)[:80]}
    if name == "towns_hit":
        return {"scene_id": args.get("scene_id"), "towns": B.towns.get(str(args.get("scene_id") or ""), [])}
    if name == "line_detail":
        q = fold(str(args.get("query") or ""))
        rows = [r for r in B.lines_info if not q or q in fold(str(r.get("label") or ""))]
        return {"lines": rows[:20]}
    if name == "cost_breakdown":
        return {"costs": [{"id": f, **B.facts[f]} for f in B.costs if f in B.facts], "note": "Estimates; the high end of each range."}
    if name == "agent_turns":
        return {"steps": B.agent_steps[:60]}
    return {"error": f"unknown tool {name}"}


def _call_text(call: dict) -> str:
    args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in (call.get("args") or {}).items())
    return f"{call['name']}({args})"


def _parse(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else None
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                d = json.loads(m.group(0))
                return d if isinstance(d, dict) else None
            except ValueError:
                return None
    return None


def _outline(B: Board) -> str:
    lang_name = "English" if B.lang == "en" else "Spanish (neutral Latin American)"
    rows = [f"EPISODE: {B.title} ({B.episode}). LANGUAGE: {lang_name}. REGION: {B.region}.",
            "SCENES (read every one with get_scene before writing -- call them all in one turn, in parallel -- and use the other tools for detail):"]
    for s in B.scenes:
        rows.append(f"- {s['id']} [{s['chapter']}]: {s['brief']}")
    rows.append("Then draft every scene and test the drafts with check_draft (every scene, in one turn, in parallel); fix whatever it rejects.")
    rows.append('When every draft passes, answer ONLY with JSON {"scenes": [{"id": "...", "lines": [{"speaker": "presenter|analyst", "text": "..."}]}]} with one entry per scene id above, in order.')
    return "\n".join(rows)


def _append_user_text(contents: list[dict], text: str) -> None:
    if contents and contents[-1].get("role") == "user":
        contents[-1] = {**contents[-1], "parts": list(contents[-1]["parts"]) + [{"text": text}]}
    else:
        contents.append({"role": "user", "parts": [{"text": text}]})


async def direct(B: Board, progress) -> tuple[dict, dict]:
    """The director agent: ({scene id: checked lines}, ai meta with the trace). Templates stand where it fails."""
    trace: list[dict] = []
    meta = {"status": "not_configured", "model": SHOW_MODEL, "calls": 0, "cached_calls": 0, "tools_called": 0, "drafts_checked": 0, "drafts_rejected": 0,
            "lines_checked": 0, "lines_rejected": 0, "scenes_by_gemini": 0, "scenes_template": len(B.scenes), "rounds": 0, "rejections": [], "trace": trace,
            "reviews": {}}
    reviews = meta["reviews"]  # scene id -> {status, checked, self_checks, self_fixes, why}
    if not llm.configured():
        trace.append({"actor": "engine", "kind": "check", "text": "Gemini is not configured: the plain script from the storyboard is used."})
        return {}, meta
    t0 = time.monotonic()
    left = lambda: DIRECTOR_DEADLINE_S - (time.monotonic() - t0)  # noqa: E731
    contents: list[dict] = [{"role": "user", "parts": [{"text": _outline(B)}]}]
    data = None
    keys: list[str] = []
    use = {"model": SHOW_MODEL}

    async def ask(final: bool) -> dict | None:
        """One turn; a model that is busy (503), out of quota or too slow is swapped once for the next in the chain."""
        chain = list(dict.fromkeys([use["model"], SHOW_MODEL, *llm.FALLBACK_MODELS]))
        chain = [m for m in chain if not llm._model_out(m)] or chain  # skip the models out of quota today
        for i, m in enumerate(chain[:MODEL_TRIES]):
            if left() < 6:
                return None
            if i:
                contents[:] = llm._with_dummy_signatures(contents)  # the history's signatures came from another model
                trace.append({"actor": "engine", "kind": "check", "text": f"{chain[i - 1]} didn't answer: asking {m} instead."})
            try:
                reply, off = await asyncio.wait_for(
                    llm.complete_tools(contents, TOOLS, system=SYSTEM, fallback={"content": None, "calls": [], "text": ""}, timeout=WRITE_TIMEOUT_S if final else CALL_TIMEOUT_S,
                                       surface=SURFACE, model=m, thinking=llm.AGENT_THINKING, tool_mode="NONE" if final else "AUTO",
                                       schema=SCHEMA if final else None, cache=True),
                    max(5.0, left()))
            except asyncio.TimeoutError:
                continue
            if not off and reply.get("content"):
                use["model"] = reply.get("model") or m
                meta["model"] = use["model"]
                meta["cached_calls" if reply.get("cached") else "calls"] += 1  # a replayed answer is not a new call
                keys.append(reply.get("cache_key"))
                return reply
        return None

    for rnd in range(TOOL_ROUNDS + 1):
        if left() < 8:
            break
        final = rnd == TOOL_ROUNDS
        if final:
            _append_user_text(contents, "Write the narration now, as the JSON described.")
        progress(T(B.lang, "Gemini is reading the storyboard" if not final else "Gemini is writing the narration", "Gemini lee el guion" if not final else "Gemini escribe la narración"))
        reply = await ask(final)
        if reply is None:
            break
        contents.append(reply["content"])
        calls = reply.get("calls") or []
        if not calls:
            data = _parse(reply.get("text") or "")
            if data is not None or final:
                break
            _append_user_text(contents, "Answer ONLY with the JSON described.")
            continue
        parts = []
        for call in calls[:24]:
            res = _tool(B, call["name"], call.get("args") or {})
            meta["tools_called"] += 1
            if call["name"] == "check_draft" and "ok" in res:
                sid = res.get("scene_id")
                rv = reviews.setdefault(sid, {"self_checks": 0, "self_fixes": 0})
                rv["self_checks"] += 1
                meta["drafts_checked"] += 1
                trace.append({"actor": "gemini", "kind": "tool", "text": f"check_draft({sid})"})
                if res["ok"]:
                    trace.append({"actor": "engine", "kind": "accept", "text": f"{sid}: the draft passes ({res.get('numbers_checked', 0)} numbers checked)."})
                else:
                    rv["self_fixes"] += 1
                    rv.setdefault("draft_why", res.get("fix"))
                    meta["drafts_rejected"] += 1
                    trace.append({"actor": "engine", "kind": "check", "text": f"{sid}: the draft is sent back: {res.get('fix')}"[:220]})
            else:
                trace.append({"actor": "gemini", "kind": "tool", "text": _call_text(call)[:200]})
            parts.append(llm.function_response(call, res))
        contents.append({"role": "user", "parts": parts})
        meta["rounds"] = rnd + 1
    if not data:
        meta["status"] = "fallback"
        trace.append({"actor": "engine", "kind": "check", "text": "Gemini didn't finish the narration (key, quota, network or time): the plain script stands."})
        return {}, meta
    meta["status"] = "gemini"
    trace.append({"actor": "gemini", "kind": "draft", "text": f"Drafted the narration for {len(data.get('scenes') or [])} scenes."})
    allowed, known = _allowed_numbers(B), _known_number_words(B, B.lang)
    got = {str(s.get("id")): s.get("lines") for s in (data.get("scenes") or []) if isinstance(s, dict)}
    ok_lines: dict[str, list[dict]] = {}
    failed: list[tuple[dict, str]] = []
    for i, sc in enumerate(B.scenes):
        lines = got.get(sc["id"])
        ok, why, n, out = check_scene_lines(B, sc, lines, B.lang, i == 0, allowed, known)
        meta["lines_checked"] += len(lines) if isinstance(lines, list) else 0
        llm.note_check(SURFACE, ok, f"a show scene: {why}" if why else "a show scene", None)
        rv = reviews.setdefault(sc["id"], {"self_checks": 0, "self_fixes": 0})
        rv["checked"] = n
        if ok:
            ok_lines[sc["id"]] = out
            rv["status"] = "passed"
            trace.append({"actor": "engine", "kind": "accept", "text": f"{sc['id']}: every number is a fact ({n} checked)."})
        else:
            meta["lines_rejected"] += 1
            rv["status"], rv["why"] = "template", why or "failed"
            failed.append((sc, why or "failed"))
            trace.append({"actor": "engine", "kind": "check", "text": f"{sc['id']}: rejected: {why}"})
    if failed and left() >= 8:
        progress(T(B.lang, "Gemini is rewriting the scenes the checker rejected", "Gemini reescribe las escenas que el control rechazó"))
        fb = ["THE ENGINE'S CHECKER REJECTED THESE SCENES. Rewrite only these, fixing exactly what is named and keeping every rule:"]
        for sc, why in failed:
            fb.append(f"- {sc['id']}: {why}. Your lines were: {json.dumps(got.get(sc['id']), ensure_ascii=False)[:600]}")
        fb.append('Answer ONLY with JSON {"scenes": [{"id": "...", "lines": [...]}]} for these ids.')
        _append_user_text(contents, "\n".join(fb))
        reply = await ask(True)
        if reply is not None:
            trace.append({"actor": "gemini", "kind": "rewrite", "text": f"Rewrote {len(failed)} scene(s) with the checker's findings."})
            d2 = _parse(reply.get("text") or "") or {}
            got2 = {str(s.get("id")): s.get("lines") for s in (d2.get("scenes") or []) if isinstance(s, dict)}
            still = []
            for sc, _why in failed:
                lines = got2.get(sc["id"])
                ok, why, n, out = check_scene_lines(B, sc, lines, B.lang, sc is B.scenes[0], allowed, known)
                meta["lines_checked"] += len(lines) if isinstance(lines, list) else 0
                llm.note_check(SURFACE, ok, f"a rewritten show scene: {why}" if why else "a rewritten show scene", None)
                if ok:
                    ok_lines[sc["id"]] = out
                    reviews[sc["id"]].update({"status": "rewritten", "checked": n})
                    trace.append({"actor": "engine", "kind": "accept", "text": f"{sc['id']}: the rewrite passes ({n} numbers checked)."})
                else:
                    meta["lines_rejected"] += 1
                    still.append((sc, why))
                    trace.append({"actor": "engine", "kind": "check", "text": f"{sc['id']}: still rejected ({why}): its plain script is used."})
            failed = still
    meta["rejections"] = [f"{sc['id']}: {why}" for sc, why in failed][:12]
    meta["scenes_by_gemini"] = len(ok_lines)
    meta["scenes_template"] = len(B.scenes) - len(ok_lines)
    if not ok_lines:
        meta["status"] = "fallback"
        llm.cache_forget([k for k in keys if k])  # a run with nothing usable is asked afresh next time
    return ok_lines, meta


# ------------------------------------------------------------------------------------------------ assemble
def assemble(B: Board, gem: dict, ai: dict) -> dict:
    scenes = []
    flat = []
    for sc in B.scenes:
        use = gem.get(sc["id"])
        if use and all(x.get("same_as_template") for x in use):
            use = None  # Gemini gave the plain script back: it stays labeled the template
        if use:
            lines = [{"speaker": x["speaker"], "text": x["text"], "facts": x["facts"], "by": "gemini"} for x in use]
        else:
            lines = []
            for t in sc["template"]:
                toks = [x.replace(",", "").rstrip(".") for x in _num_re().findall(_mask(B, t["text"]))]
                fx = list(t["facts"]) + [f for f in attribute(B, t["text"], toks, B.lang, scene_fact_ids(B, sc)) if f not in t["facts"]]
                lines.append({"speaker": t["speaker"], "text": t["text"], "facts": fx, "by": "template"})
        for ln in lines:
            ln["est_ms"] = int(round(len(ln["text"]) / CAPTION_CPS * 1000))
            flat.append(ln)
        rv = (ai.get("reviews") or {}).get(sc["id"]) or {}
        review = {"by": "gemini" if use else "template", "checked": int(rv.get("checked") or sum(len(_num_re().findall(_mask(B, x["text"]))) for x in lines)),
                  "self_checks": int(rv.get("self_checks") or 0), "self_fixes": int(rv.get("self_fixes") or 0),
                  "rewritten": rv.get("status") == "rewritten", **({"why": str(rv.get("why") or rv.get("draft_why"))[:200]} if (rv.get("why") or rv.get("draft_why")) else {})}
        scenes.append({"id": sc["id"], "chapter": sc["chapter"], "min_ms": sc["min_ms"], "camera": sc["camera"], "layers": sc["layers"],
                       "lines": lines, "written_by": "gemini" if use else "template", "review": review})
    for i, ln in enumerate(flat):
        key = voice.register(ln["text"], B.lang, ln["speaker"], prev_text=flat[i - 1]["text"] if i else None,
                             next_text=flat[i + 1]["text"] if i + 1 < len(flat) else None)
        ln["voice"] = {"id": key, "key": key, "role": ln["speaker"], "lang": B.lang}
    names = voice.voice_names() if voice.configured() else {"presenter": None, "analyst": None}
    for sc in scenes:
        sc["est_ms"] = max(sc["min_ms"], sum(ln["est_ms"] for ln in sc["lines"]) + 600)
    ep = next(e for e in EPISODES if e["id"] == B.episode)
    # Build together shows no grid model at all: its note says what it does show (and never calls the filings synthetic)
    note = NOTE if B.episode != "together" else NOTE_FILINGS
    return {
        "version": VERSION,
        "episode": B.episode,
        "title": B.title,
        "blurb": ep["blurb"][B.lang],
        "region": B.region,
        "lang": B.lang,
        "synthetic": True,
        "note": note,
        "sources": B.sources,
        "voices": {"presenter": {"name": names.get("presenter") or T(B.lang, "Presenter", "Presentador")},
                   "analyst": {"name": names.get("analyst") or T(B.lang, "Analyst", "Analista")},
                   "provider": "elevenlabs" if voice.configured() else "browser"},
        "ai": {**{k: ai.get(k) for k in ("status", "model", "calls", "cached_calls", "tools_called", "drafts_checked", "drafts_rejected", "lines_checked", "lines_rejected",
                                         "scenes_by_gemini", "scenes_template", "rounds", "rejections")},
               "surface": SURFACE, "trace": ai.get("trace") or []},
        "facts": B.facts,
        "names": sorted(n for n in B.names if re.search(r"\d", n)),  # names that hold digits ('Orlando 40'): not figures
        "scenes": scenes,
        "chapters": list(dict.fromkeys(s["chapter"] for s in scenes)),
        "est_ms": sum(s["est_ms"] for s in scenes),
    }


# ------------------------------------------------------------------------------------------------ jobs
class ShowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region: str | None = None
    lang: Literal["en", "es"] = "en"


_shows: "OrderedDict[tuple, dict]" = OrderedDict()
_show_times: dict[tuple, float] = {}
_jobs: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()
_engine_gate: asyncio.Semaphore | None = None
_bg: set = set()


def _gate() -> asyncio.Semaphore:
    global _engine_gate
    if _engine_gate is None:
        _engine_gate = asyncio.Semaphore(1)  # one storyboard computes at a time (CPU-bound; Render has a sliver of one)
    return _engine_gate


def _gc() -> None:
    now = time.monotonic()
    for jid in [j for j, job in _jobs.items() if job["status"] != "pending" and now - job["created"] > JOB_TTL_S]:
        del _jobs[jid]
    while len(_jobs) > JOBS_MAX:
        old = next((j for j, job in _jobs.items() if job["status"] != "pending"), None)
        if old is None:
            break
        del _jobs[old]


def build_board_sync(episode: str, code: str, lang: str) -> Board:
    if episode == "collapse":
        return build_collapse(code, lang)
    if episode == "hurricane":
        return build_hurricane(code, lang)
    if episode == "strengthen":
        return build_strengthen(code, lang)
    raise ValueError(episode)


async def build_board(episode: str, code: str, lang: str, progress) -> Board:
    if episode == "boom":
        return await build_boom(code, lang, progress)
    if episode == "together":
        return await build_together(lang, progress)
    return await run_in_threadpool(build_board_sync, episode, code, lang)


async def make_show(episode: str, code: str, lang: str, progress=lambda text, step=None: None) -> dict:
    """The whole show: storyboard (engine), director (Gemini, checked), voice keys. A Gemini-written show is saved to
    disk (SAVED_DIR), so its words and voice keys survive a restart: ElevenLabs renders each line once, not once per
    restart."""
    progress(T(lang, "The engine is building the storyboard", "El motor arma el guion"), 1)
    if episode == "together":  # its pipeline takes the engine gate itself; the two agents negotiate outside it
        B = await build_board(episode, code, lang, lambda text: progress(text, 1))
    else:
        async with _gate():
            B = await build_board(episode, code, lang, lambda text: progress(text, 1))
    progress(T(lang, "Gemini is directing the narration", "Gemini dirige la narración"), 2)
    gem, ai = await direct(B, lambda text: progress(text, 2))
    progress(T(lang, "Registering the voices", "Registrando las voces"), 3)
    show = await run_in_threadpool(assemble, B, gem, ai)
    if show["ai"].get("status") == "gemini" and show["ai"].get("scenes_by_gemini"):
        await run_in_threadpool(_save, B, gem, ai)
    return show


# ------------------------------------------------------------------------------------------------ saved narrations
_fp_cache: dict = {}


def _fingerprint() -> str:
    """The code and data a storyboard is built from: the engine (baked.py's list), the modules the boards read, the
    baked studies and the Build together pipeline's data. A saved narration is used only while this matches."""
    if "fp" in _fp_cache:
        return _fp_cache["fp"]
    import hashlib

    import baked

    here = os.path.dirname(__file__)
    h = hashlib.sha256((BOARD_VERSION + "|" + baked.fingerprint()).encode())
    files = [os.path.join(here, n) for n in ("briefing.py", "planner.py", "gridlock.py", "negotiate.py", "hospitals.py", "leadtimes.py", "fixit.py")]
    for sub in (os.path.join(here, "demo", "strengthen"), os.path.join(here, "demo", "gridlock")):
        if os.path.isdir(sub):
            files += sorted(os.path.join(sub, n) for n in os.listdir(sub) if n.endswith(".json"))
    for fn in files:
        try:
            with open(fn, "rb") as fh:
                h.update(os.path.basename(fn).encode())
                h.update(fh.read().replace(b"\r\n", b"\n"))
        except OSError:
            continue
    _fp_cache["fp"] = h.hexdigest()[:16]
    return _fp_cache["fp"]


def _saved_path(episode: str, region: str, lang: str) -> str:
    return os.path.join(SAVED_DIR, f"{episode}_{region}_{lang}.json")


def _save(B: Board, gem: dict, ai: dict) -> None:
    """The storyboard, Gemini's checked lines and the director's record, as JSON (best effort)."""
    try:
        os.makedirs(SAVED_DIR, exist_ok=True)
        board = {"episode": B.episode, "region": B.region, "lang": B.lang, "title": B.title, "facts": B.facts, "names": sorted(B.names),
                 "forbid": B.forbid, "first_rule": B.first_rule, "sources": B.sources, "scenes": B.scenes}
        data = {"version": BOARD_VERSION, "fingerprint": _fingerprint(), "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": ai.get("model"),
                "board": board, "gem": {sid: [{"speaker": x["speaker"], "text": x["text"]} for x in lines] for sid, lines in gem.items()},
                "ai": {k: v for k, v in ai.items() if k != "reviews"} | {"reviews": ai.get("reviews") or {}}}
        tmp = _saved_path(B.episode, B.region, B.lang) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, _saved_path(B.episode, B.region, B.lang))
    except (OSError, TypeError, ValueError) as e:
        log.warning("show: could not save %s/%s/%s: %s", B.episode, B.region, B.lang, e)


def _load_saved(episode: str, region: str, lang: str) -> dict | None:
    """A saved Gemini narration for this episode, re-checked line by line with today's checker (a scene that no longer
    passes goes back to its plain script), assembled as when it was written: same words, same voice keys."""
    p = _saved_path(episode, region, lang)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != BOARD_VERSION or data.get("fingerprint") != _fingerprint():
            log.info("show: the saved %s narration is from other code or data: it is written again", os.path.basename(p))
            return None
        bd = data["board"]
        B = Board(bd["episode"], bd["region"], bd["lang"], bd["title"])
        B.facts, B.names, B.forbid, B.first_rule, B.sources, B.scenes = bd["facts"], set(bd["names"]), bd["forbid"], bd["first_rule"], bd["sources"], bd["scenes"]
        ai = data.get("ai") or {}
        reviews = ai.get("reviews") or {}
        gem = {}
        for i, sc in enumerate(B.scenes):
            lines = (data.get("gem") or {}).get(sc["id"])
            if not lines:
                continue
            ok, why, n, out = check_scene_lines(B, sc, lines, B.lang, i == 0)
            if ok:
                gem[sc["id"]] = out
            else:
                reviews.setdefault(sc["id"], {}).update({"status": "template", "why": f"re-checked when loaded: {why}"})
                (ai.setdefault("trace", [])).append({"actor": "engine", "kind": "check", "text": f"{sc['id']}: the saved lines no longer pass ({why}): its plain script is used."})
        if not gem:
            return None
        ai.update({"reviews": reviews, "scenes_by_gemini": len(gem), "scenes_template": len(B.scenes) - len(gem), "status": "gemini"})
        show = assemble(B, gem, ai)
        show["ai"].update({"replayed": True, "saved_at": data.get("saved_at")})
        return show
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("show: the saved narration %s is unreadable: %s", os.path.basename(p), e)
        return None


def _key(episode: str, code: str, lang: str) -> tuple:
    return (episode, code, lang, llm.configured())


def _cached(key: tuple) -> dict | None:
    with _lock:
        hit = _shows.get(key)
        if hit is not None:
            if hit["ai"]["status"] == "fallback" and llm.configured() and time.monotonic() - _show_times.get(key, 0) > RETRY_S:
                return None  # a transient Gemini miss: ask again
            _shows.move_to_end(key)
            return hit
    if not key[3]:
        return None  # without Gemini the plain script runs (and is labeled so); saved narrations are Gemini's
    saved = _load_saved(key[0], key[1], key[2])
    if saved is None:
        return None
    with _lock:
        _shows[key] = saved
        _show_times[key] = time.monotonic()
        while len(_shows) > SHOWS_MAX:
            old, _ = _shows.popitem(last=False)
            _show_times.pop(old, None)
    return saved


async def _run(job: dict, episode: str, code: str, lang: str) -> None:
    def progress(text: str, step: int | None = None) -> None:
        job["progress"] = {"step": step or job["progress"]["step"], "of": 3, "text": text}

    try:
        show = await make_show(episode, code, lang, progress)
        with _lock:
            _shows[job["key"]] = show
            _show_times[job["key"]] = time.monotonic()
            while len(_shows) > SHOWS_MAX:
                old, _ = _shows.popitem(last=False)
                _show_times.pop(old, None)
        job["show"] = show
        job["status"] = "done"
        job["progress"] = {"step": 3, "of": 3, "text": T(lang, "Ready", "Listo")}
    except HTTPException as e:
        job["status"] = "error"
        job["error"] = e.detail if isinstance(e.detail, str) else "This show can't be built"
        job["code"] = e.status_code
    except Exception:  # noqa: BLE001 — a background job always ends in a state the player can show
        log.exception("show: %s/%s/%s failed", episode, code, lang)
        job["status"] = "error"
        job["error"] = T(lang, "The show couldn't be built. Try again.", "No se pudo armar el episodio. Inténtalo de nuevo.")


def start_job(episode: str, code: str, lang: str) -> dict:
    key = _key(episode, code, lang)
    hit = _cached(key)
    with _lock:
        _gc()
        if hit is not None:
            job = {"id": secrets.token_urlsafe(12), "key": key, "status": "done", "progress": {"step": 3, "of": 3, "text": T(lang, "Ready", "Listo")},
                   "show": hit, "error": None, "created": time.monotonic(), "estimate_s": 0}
            _jobs[job["id"]] = job
            return job
        same = next((j for j in _jobs.values() if j["key"] == key and j["status"] == "pending"), None)
        if same is not None:
            return same
        if sum(1 for j in _jobs.values() if j["status"] == "pending") >= PENDING_MAX:
            raise HTTPException(status_code=429, detail=T(lang, "Several episodes are being made right now: try again in a minute.",
                                                          "Se están preparando varios episodios ahora mismo: inténtalo en un minuto."))
        job = {"id": secrets.token_urlsafe(12), "key": key, "status": "pending", "progress": {"step": 0, "of": 3, "text": T(lang, "Queued", "En cola")},
               "show": None, "error": None, "created": time.monotonic(), "estimate_s": ESTIMATE_S.get(episode, 45) if llm.configured() else 10}
        _jobs[job["id"]] = job
    task = asyncio.ensure_future(_run(job, episode, code, lang))
    _bg.add(task)
    task.add_done_callback(_bg.discard)
    return job


def _region_for(episode: str, region: str | None) -> str:
    if episode == "together":
        return "GA-SC"
    if (region or "").strip().upper() == "US":
        raise HTTPException(status_code=422, detail="Pick a state: each episode runs on one state's model")
    code = region_code(region or DEFAULT_REGION)
    if episode == "hurricane" and code not in STORM_PRESET:
        raise HTTPException(status_code=422, detail="There is no storm track for this state yet: the hurricane episode runs for Florida, Texas and New York")
    return code


# ------------------------------------------------------------------------------------------------ routes
@router.get("/api/show/episodes")
def episodes(lang: str = "en"):
    lg = lang if lang in LANGS else "en"
    return [{"id": e["id"], "title": e["title"][lg], "blurb": e["blurb"][lg], "region": e["region"], "est_minutes": e["est_minutes"]} for e in EPISODES]


@router.post("/api/show/{episode}")
@limiter.limit("30/minute")
async def start(request: Request, body: ShowIn, episode: str = PathParam(..., max_length=20)):
    if episode not in EPISODE_IDS:
        raise HTTPException(status_code=404, detail="No such episode")
    code = _region_for(episode, body.region)
    job = start_job(episode, code, body.lang)
    return {"id": job["id"], "status": job["status"], "estimate_s": job.get("estimate_s", 0)}


@router.get("/api/show/jobs/{job_id}")
@limiter.limit("240/minute")
def job_status(request: Request, job_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_-]{8,40}$")):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="This show job is no longer available: start it again")
    out = {"id": job["id"], "status": job["status"], "progress": job["progress"]}
    if job["status"] == "done":
        out["show"] = job["show"]
    elif job["status"] == "error":
        out["error"] = job["error"]
    return out
