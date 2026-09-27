"""Collaboration plans (Build together, Sperry GridLock): ways two utilities' PUBLISHED construction plans could be built
together, each with what every company gains and gives up, proposed by agents and checked by the pipeline.

  POST /api/gridlock/plans   {"pair": "<overlap id>", "lang": "en"|"es", "ai": true}

Three Gemini agents. Two each represent ONE company's published plan (its public filing: its project, its dates, its
share of the shared estimate) and are given a GOAL from that filing only (negotiate.goals: the most its side should pay,
the window its filing allows). Each proposes the plan kinds that serve its filing best, from a MENU the pipeline built
from the pair's own data. A neutral coordinator merges the proposals into 2-3 DISTINCT plans, words them for a
newcomer and recommends one. The agents choose and argue; they never set a figure:

  - the MENU (deterministic, below) holds every plan kind the data supports, with its window, its shift in months and
    its priced items: gridlock.py's rough estimate (MISO / USDA / SCE sources), split between the two companies by the
    draft's own rule (filed length when both filings give one, else 50/50)
  - every figure in a plan (money, months, outages avoided, the window) is the menu's, with its source; every number
    an agent WRITES in its text must be one of the facts' or the menu's (agreement.check_text), and no sentence may speak
    for a utility; what fails is dropped (listed in `dropped`) and the template's sentence is used instead
  - one revision: the coordinator gets the pipeline's findings back once when something was dropped

Without Gemini (no key, quota, an error, too slow), the same menu gives template plans labeled
"Template plan - AI unavailable". Honesty (CLAUDE.md -> NO DEFAMATION, GRIDLOCK): real public filings, "as filed", never
"will"; an agent represents a company's published plan and never speaks for the company; the Georgia file is the
public-disclosure version (unredacted fields only, costs redacted and never inferred).
"""

import asyncio
import copy
import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

import agreement as ag
import gridlock as gl
import llm
import negotiate as ng
import voice
from limiter import limiter

router = APIRouter(tags=["collab_plans"])
log = logging.getLogger("uvicorn.error")

SURFACE = "collab_plans"
CALL_TIMEOUT_S = 11  # per Gemini request
DEADLINE_S = 24  # the whole AI path, then the template answers (cold)
REVISE_IF_UNDER_S = 13  # the coordinator's one revision only while the call is young
LANGS = ("en", "es")
CACHE_MAX = 96
MAX_TITLE_WORDS = 8
MAX_STEPS = 4
MAX_TEXT = 320
FALLBACK_LABEL = "Template plan - AI unavailable"
FALLBACK_LABEL_ES = "Plan de plantilla - IA no disponible"
KIND_ORDER = ("one_outage", "shift", "stagger", "share_prep", "status_check")

_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()
_inflight: dict = {}
_NONE = {"_none": True}


def _L(lang: str, en: str, es: str) -> str:
    return es if lang == "es" else en


# ----------------------------------------------------------------------------- months (as (year, month))


def _ym_add(ym: tuple, n: int) -> tuple:
    k = ym[0] * 12 + (ym[1] - 1) + n
    return (k // 12, k % 12 + 1)


def _ym_diff(a: tuple, b: tuple) -> int:
    """Months from a to b (b later: positive)."""
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def _mt(ym) -> str:
    return f"{ag.MONTHS[ym[1] - 1]} {ym[0]}"


def _span(w, lang: str = "en") -> str:
    if lang == "es":
        return ng._mt(w[0], "es") if w[0] == w[1] else ng._span(w, "es")
    return _mt(w[0]) if w[0] == w[1] else f"{_mt(w[0])} to {_mt(w[1])}"


def _months_in(w) -> int:
    return _ym_diff(w[0], w[1]) + 1


def _usd(lo, hi) -> str:
    return ag._usd_range(lo, hi)


# the estimate's items in Spanish (gridlock.py words them in English); mobilization by what the two jobs can share
ITEM_ES = {
    "mobilization": {"line": "Cuadrillas de línea y maquinaria pesada movilizadas una sola vez",
                     "line_other_class": "Maquinaria pesada movilizada una sola vez",
                     "station": "Cuadrillas y equipos de subestación movilizados una sola vez",
                     "mixed": "Maquinaria pesada y entregas movilizadas una sola vez"},
    "laydown_yard": "Un solo patio de acopio en lugar de dos",
    "crossing": "Estructuras del punto de encuentro diseñadas una sola vez",
    "permits": "Permisos y revisión ambiental del tramo compartido, una sola vez",
    "access_roads": "Caminos de acceso construidos una sola vez",
    "row_cost": "Servidumbre comprada, negociada y tramitada una sola vez",
}


def _item_label(it: dict, case: dict, lang: str) -> str:
    if lang != "es":
        return it["label"]
    es = ITEM_ES.get(it["id"])
    if isinstance(es, dict):
        return es.get(case["fit"]["fit"]) or it["label"]
    return es or it["label"]


# ----------------------------------------------------------------------------- the case and the menu


def _case(pair: str, months: int, as_of: date | None = None) -> dict:
    """negotiate's case (the same overlap, filings, estimate, windows, split rules and goals the negotiation and the draft
    use) plus the overlap record, the estimate priced as if one schedule moved, the two sides' short names and the split
    every plan uses (settled between the two goals)."""
    case = ng._case(pair, months, as_of)
    st = gl._load()
    i, j = st["index"][case["pa"]["id"]], st["index"][case["pb"]["id"]]
    case["rec"] = gl._overlap_record(st, i, j, months, "closest")
    case["est_aligned"] = gl._estimate(st, i, j, months, assume_aligned=True)
    case["A"], case["B"] = ng._short(case, "a"), ng._short(case, "b")
    case["fit"] = gl.crew_fit(case["pa"], case["pb"])
    case["split"] = _split_rule(case)
    return case


_RULE_PREF = {"by_length": 0, "by_kv": 1, "equal": 2}


def _split_rule(case: dict) -> dict:
    """How the ONE shared cost of a plan is paid, settled between the two goals (J2): each agent's goal is the most its
    own filing supports paying (negotiate.goals); the allowed rule (filed length, kV class, 50/50) that asks the least of
    the side it asks most of is used, so when the goals conflict each side pays a stated number of points more than its
    goal. No rule is invented: the draft uses the same rule and wording."""
    caps = [case["goals"]["a"]["cap_pct"], case["goals"]["b"]["cap_pct"]]

    def over(r):
        return [max(0.0, round(r["shares"][k] - caps[k], 1)) for k in (0, 1)]

    r = min(case["rules"], key=lambda r: (max(over(r)), sum(over(r)), _RULE_PREF.get(r["id"], 9)))
    return {"rule": r["id"], "label": r["label"], "label_es": r["label_es"], "shares": list(r["shares"]),
            "basis": r["basis"], "basis_es": r["basis_es"], "caps": caps, "over": over(r),
            "goal_labels": [case["goals"]["a"]["rule_label"], case["goals"]["b"]["rule_label"]],
            "goal_rules": [case["goals"]["a"]["rule"], case["goals"]["b"]["rule"]]}


def _rule_word(label: str) -> str:
    return label if label[:1].isdigit() else label[:1].lower() + label[1:]


def _settle_text(case: dict, lang: str) -> str:
    """What the two goals mean for every plan's split, in plain words (replaces 'the agents have to trade': in these plans
    the pipeline settles the split between the goals; the agents choose and word the plans)."""
    sp, A, B = case["split"], case["A"], case["B"]
    ca, cb = sp["caps"]
    sa, sb = sp["shares"]
    oa, ob = sp["over"]
    rules_es = {r["id"]: r["label_es"] for r in case["rules"]}
    ga_es, gb_es = (rules_es.get(x, x) for x in sp["goal_rules"])
    if case["conflict"]["all_rules_equal"]:
        return _L(lang, f"Every allowed split gives the same shares ({A} {sa:g} %, {B} {sb:g} %), so there is nothing to settle on cost.",
                  f"Todas las reglas de reparto dan las mismas partes ({A} {sa:g} %, {B} {sb:g} %): no hay nada que decidir sobre el costo.")
    if not (oa or ob):
        return _L(lang,
                  f"The goals fit together: splitting the shared costs {_rule_word(sp['label'])} ({A} {sa:g} %, {B} {sb:g} %) asks neither side "
                  "to pay more than its smallest fair share.",
                  f"Los objetivos encajan: repartir los costos compartidos {_rule_word(sp['label_es'])} ({A} {sa:g} %, {B} {sb:g} %) no pide a "
                  "ninguna de las dos pagar más de su parte justa más baja.")
    who = [(A, oa, sa, ca), (B, ob, sb, cb)]
    over_en = " and ".join(f"{w} pays {s:g} %, {o:g} points above its {c:g} % goal" for w, o, s, c in who if o)
    over_es = " y ".join(f"{w} paga el {s:g} %, {o:g} puntos por encima de su objetivo del {c:g} %" for w, o, s, c in who if o)
    # said plainly: what each goal is and where it comes from, why the two can't both be met, what the plans do about it
    tot = ca + cb
    short_en = (f"Those limits add up to {tot:g} %, less than the 100 % the shared costs need, so one side has to pay above its goal."
                if tot < 100 else f"Those limits add up to {tot:g} %, but none of the allowed splits fits both, so one side has to pay above its goal.")
    short_es = (f"Esos límites suman el {tot:g} %, menos del 100 % que necesitan los costos compartidos, así que una de las dos paga más que su objetivo."
                if tot < 100 else f"Esos límites suman el {tot:g} %, pero ningún reparto permitido cumple los dos, así que una de las dos paga más que su objetivo.")
    return _L(lang,
              f"Neither filing states a cost split, so each agent's goal is a fair-share rule worked out from the filed figures: {A} pays at most "
              f"{ca:g} % ({_rule_word(sp['goal_labels'][0])}) and {B} at most {cb:g} % ({_rule_word(sp['goal_labels'][1])}). {short_en} "
              f"Every plan uses the allowed split closest to both goals, {_rule_word(sp['label'])}: {over_en}.",
              f"Ningún documento fija un reparto de costos, así que el objetivo de cada agente es una regla de reparto justo calculada con las cifras publicadas: {A} paga como máximo "
              f"el {ca:g} % ({_rule_word(ga_es)}) y {B} el {cb:g} % ({_rule_word(gb_es)}). {short_es} "
              f"Cada plan usa el reparto permitido más cercano a los dos objetivos, {_rule_word(sp['label_es'])}: {over_es}.")


def _items(est: dict, ids: tuple) -> list[dict]:
    return [it for it in est.get("items") or [] if it["id"] in ids and it["unit"] == "USD"]


def _gain_rows(case: dict, items: list[dict], k: int, split: dict, lang: str) -> list[dict]:
    """What company k saves on each shared item: it pays its share of ONE shared cost instead of a whole one of its
    own, so it saves the rest (the other company's share of the item). Paying more means saving less."""
    s = split["shares"][k]
    keep = (100 - s) / 100
    out = []
    for it in items:
        lo, hi = round(it["low"] * keep, -3), round(it["high"] * keep, -3)
        label = _item_label(it, case, lang)
        out.append({"what": _L(lang, f"{label} (pays {s:g} % of one shared cost instead of its own)",
                               f"{label} (paga el {s:g} % de un costo compartido en lugar del suyo)"),
                    "usd": [lo, hi], "months": None, "outages_avoided": None, "source": it.get("source"),
                    "_item": it["id"], "_basis": it.get("basis"), "_keep_pct": 100 - s})
    return out


def _over_row(case: dict, k: int, split: dict, lo: float, hi: float, lang: str) -> dict | None:
    """The split asks company k to pay more than its own filing supports: what that costs it against its goal."""
    over = split["over"][k]
    if not over or not hi:
        return None
    s, cap = split["shares"][k], split["caps"][k]
    rule = split["goal_labels"][k]
    rule_es = next((r["label_es"] for r in case["rules"] if r["id"] == split["goal_rules"][k]), rule)
    return {"what": _L(lang, f"Pays {s:g} % of the shared costs, not the {cap:g} % of its smallest fair share ({_rule_word(rule)})",
                       f"Paga el {s:g} % de los costos compartidos, no el {cap:g} % de su parte justa más baja ({_rule_word(rule_es)})"),
            "usd": [round(lo * over / 100, -3), round(hi * over / 100, -3)], "months": None,
            "source": "Its smallest fair share (negotiate.goals: a rule computed from the filed figures, not stated in any filing) against the split closest to both goals"}


def _menu(case: dict, lang: str = "en") -> list[dict]:
    """Every plan kind this pair's data supports, fully priced and dated by the pipeline (the agents choose among these)."""
    A, B = case["A"], case["B"]
    pa, pb, rec = case["pa"], case["pb"], case["rec"]
    wa, wb = case["bounds"]["a"], case["bounds"]["b"]
    now = case["now"]
    feasible = case["feasible"]
    ahead = [bool(w and w[1] >= now) for w in (wa, wb)]
    both_ahead = all(ahead)
    near = rec["tier"] in ("touching", "row") or bool(rec.get("shared_station"))
    split = case["split"]
    est = case["est"]
    fit = case["fit"]
    src_a, src_b = case["sources"]["a"].get("label"), case["sources"]["b"].get("label")
    es = lang == "es"
    menu = []

    def plan(kind, title, summary, steps, window, items, gives_up, extra=None, outages=None):
        usd = [it for it in items if it["unit"] == "USD"]
        lo, hi = sum(it["low"] for it in usd), sum(it["high"] for it in usd)
        inc = []
        for k, (side, who) in enumerate((("a", A), ("b", B))):
            gains = _gain_rows(case, usd, k, split, lang)
            if outages:
                gains.append({"what": outages, "usd": None, "months": None, "outages_avoided": 1,
                              "source": "Overload comparison of the two filings (backend/gridlock.py)"})
            gv = list(gives_up.get(side) or [])
            o = _over_row(case, k, split, lo, hi, lang) if usd else None
            if o:
                gv.append(o)
            g_lo, g_hi = sum(g["usd"][0] for g in gains if g["usd"]), sum(g["usd"][1] for g in gains if g["usd"])
            cost_bits = [g["what"][:1].lower() + g["what"][1:] for g in gv]
            if es:
                net = f"{who} ahorra unos {_usd(g_lo, g_hi)}" if g_hi else f"{who} no tiene un ahorro cuantificado"
                net += (f" y cede: {'; '.join(cost_bits)}." if cost_bits else " y no cede nada de su calendario publicado.")
            else:
                net = f"{who} saves about {_usd(g_lo, g_hi)}" if g_hi else f"{who} has no priced saving"
                net += (f" and gives up: {'; '.join(cost_bits)}." if cost_bits else " and gives up nothing on its filed schedule.")
            inc.append({"utility": pa["utility"] if side == "a" else pb["utility"], "gains": gains, "gives_up": gv, "net": net})
        sp = None
        if usd:  # no priced saving -> nothing to split (the page shows no split for it)
            sp = {"rule": split["rule"], "label": split["label_es" if es else "label"], "shares": list(split["shares"]),
                  "basis": split["basis_es" if es else "basis"]}
        menu.append({
            "id": kind, "kind": kind, "title": title, "summary": summary, "steps": steps,
            "window": {"start": ng._iso(window[0]), "end": ng._iso(window[1])} if window else None,
            "shift": (extra or {}).get("shift"),
            "items": [it["id"] for it in usd],
            "savings": {"low": lo, "high": hi, "unit": "USD"} if usd else None,
            "split": sp,
            "incentives": inc,
            "_window": window,
        })

    # 1. one outage: the projects meet, cross or share a station, and both filed windows share months still ahead
    if feasible and near:
        items = _items(est, ("crossing", "mobilization"))
        n = _months_in(feasible)
        where = rec["shared_station"]["name"] if rec.get("shared_station") else (
            _L(lang, "the crossing", "el cruce") if rec.get("crosses") else _L(lang, "where they meet", "donde se encuentran"))
        gives = {s: [{"what": _L(lang, f"Its outage fits inside {_span(feasible)} ({n} months) instead of anywhere in its filed window ({_span(w)})",
                                 f"Su corte debe caber en {_span(feasible, 'es')} ({n} meses) en lugar de en cualquier momento de su ventana publicada ({_span(w, 'es')})"),
                      "usd": None, "months": n, "source": src}]
                 for s, w, src in (("a", wa, src_a), ("b", wb, src_b))}
        plan("one_outage", _L(lang, "One shared outage window", "Un solo corte compartido"),
             _L(lang, f"Both projects take the line out of service once, together, at {where}, in {_span(feasible)}: one outage plan and one "
                      "structure design instead of two.",
                f"Los dos proyectos dejan la línea fuera de servicio una sola vez, juntos, en {where}, en {_span(feasible, 'es')}: un plan de "
                "corte y un diseño de estructuras en lugar de dos."),
             [_L(lang, f"Compare both outage schedules for {_span(feasible)}.", f"Comparar los dos calendarios de cortes para {_span(feasible, 'es')}."),
              _L(lang, f"Pick one outage window at {where} that fits both filings.", f"Elegir una ventana de corte en {where} que encaje con los dos documentos."),
              _L(lang, "Design the structures where the projects meet once.", "Diseñar una sola vez las estructuras donde se encuentran los proyectos."),
              _L(lang, "Set crews up once for the shared work.", "Instalar las cuadrillas una sola vez para la obra compartida.")],
             feasible, items, gives, outages=_L(lang, "One outage where the projects meet instead of two", "Un solo corte donde se encuentran los proyectos, en lugar de dos"))

    # 2. shift: both still ahead, the filed windows share no months, and they are at most 3 years apart
    if both_ahead and wa and wb and not feasible:
        first, second = ("a", "b") if wa[1] < wb[0] else ("b", "a") if wb[1] < wa[0] else (None, None)
        if first:
            w1, w2 = case["bounds"][first], case["bounds"][second]
            gap = _ym_diff(w1[1], w2[0])  # months from the first window's end to the second's start
            share = min(6, _months_in(w1), _months_in(w2))
            n = gap + share - 1 if gap >= 1 else share
            if 1 <= n <= 36:
                moved = (_ym_add(w1[0], n), _ym_add(w1[1], n))
                joint = (max(moved[0], w2[0]), min(moved[1], w2[1]))
                if joint[0] <= joint[1] and joint[0] >= now:
                    items = _items(case["est_aligned"], ("mobilization", "laydown_yard"))
                    who1 = A if first == "a" else B
                    p1 = pa if first == "a" else pb
                    ins = gl._date(p1.get("in_service"))
                    new_ins = _ym_add((ins.year, ins.month), n) if ins else None
                    gives = {first: [{"what": _L(lang,
                                                 f"Moves its filed window {n} months later ({_span(w1)} to {_span(moved)})"
                                                 + (f", so its in-service date moves from {_mt((ins.year, ins.month))} to {_mt(new_ins)}" if ins else ""),
                                                 f"Retrasa {n} meses su ventana publicada ({_span(w1, 'es')} pasa a {_span(moved, 'es')})"
                                                 + (f", así que su puesta en servicio pasa de {ng._mt((ins.year, ins.month), 'es')} a {ng._mt(new_ins, 'es')}" if ins else "")),
                                      "usd": None, "months": n, "source": src_a if first == "a" else src_b}],
                             second: []}
                    plan("shift", _L(lang, f"Move {who1}'s work {n} months later", f"Retrasar {n} meses la obra de {who1}"),
                         _L(lang, f"{who1}'s project starts {n} months later so both projects are being built in {_span(joint)}, and the crews and "
                                  "equipment are set up once for both.",
                            f"El proyecto de {who1} empieza {n} meses más tarde para que los dos se construyan en {_span(joint, 'es')}, y las "
                            "cuadrillas y los equipos se instalan una sola vez para ambos."),
                         [_L(lang, f"Check whether {who1}'s project can start {n} months later than filed.",
                             f"Comprobar si el proyecto de {who1} puede empezar {n} meses más tarde de lo publicado."),
                          _L(lang, f"Plan both projects' work for {_span(joint)}.", f"Planificar la obra de los dos proyectos para {_span(joint, 'es')}."),
                          _L(lang, "Set crews and equipment up once for both jobs.", "Instalar cuadrillas y equipos una sola vez para las dos obras."),
                          _L(lang, "Share one laydown yard if the sites allow it.", "Compartir un patio de acopio si los sitios lo permiten.")],
                         joint, items, gives, extra={"shift": {"project": p1["id"], "side": first, "utility": p1["utility"],
                                                              "months": n, "direction": "later", "window": {"start": ng._iso(moved[0]), "end": ng._iso(moved[1])}}})

    # 3. stagger: keep both filed dates; one crew (or only the heavy equipment, when the two jobs need different crews) goes
    # from one job to the other; a laydown yard is shared too when the estimate prices one
    if both_ahead and wa and wb:
        gap = 0 if feasible else max(_ym_diff(wa[1], wb[0]), _ym_diff(wb[1], wa[0]))
        if feasible or gap <= 12:
            est_use = est if feasible else case["est_aligned"]
            items = _items(est_use, ("mobilization", "laydown_yard"))
            first = "a" if (wa[0] <= wb[0]) else "b"
            f_who, s_who = (A, B) if first == "a" else (B, A)
            crews = fit["fit"] in ("line", "station")
            what_en = fit["crews"].split(" (")[0]
            what_es = {"line": "cuadrillas de línea y la maquinaria pesada", "station": "cuadrillas y equipos de subestación",
                       "line_other_class": "maquinaria pesada", "mixed": "maquinaria pesada y las entregas"}.get(fit["fit"], "maquinaria pesada")
            art_es = "las" if crews else "la"
            # "la maquinaria pesada que termina… pasa… se instala y se retira" (a lone piece of equipment is singular)
            one_es = not crews and fit["fit"] != "mixed"
            v_end, v_move, v_set = ("termina", "pasa", "se instala y se retira") if one_es else ("terminan", "pasan", "se instalan y se retiran")
            gives = {first: [], ("b" if first == "a" else "a"): [
                {"what": _L(lang, f"Its start waits on {f_who}'s {'crew' if crews else 'equipment'} finishing (its filed dates stay)",
                            f"Su inicio espera a que {'la cuadrilla' if crews else 'la maquinaria'} de {f_who} termine (sus fechas publicadas se mantienen)"),
                 "usd": None, "months": None, "source": src_b if first == "a" else src_a}]}
            yard = any(it["id"] == "laydown_yard" for it in items)
            plan("stagger",
                 _L(lang, "Keep the dates, share the crews" if crews else "Keep the dates, share the equipment",
                    "Mismas fechas, cuadrillas compartidas" if crews else "Mismas fechas, maquinaria compartida"),
                 _L(lang, f"Both keep their filed dates; the {what_en} that finish {f_who}'s job move straight to {s_who}'s, so they are set "
                          "up and taken down once instead of twice." + (" One laydown yard serves both jobs." if yard else ""),
                    f"Las dos mantienen sus fechas publicadas; {art_es} {what_es} que {v_end} la obra de {f_who} {v_move} directamente a "
                    f"la de {s_who}, así que {v_set} una vez en lugar de dos." + (" Un solo patio de acopio sirve a las dos obras." if yard else "")),
                 [_L(lang, f"Line up {f_who}'s finish with {s_who}'s start.", f"Hacer coincidir el final de {f_who} con el inicio de {s_who}."),
                  _L(lang, f"Hand the {what_en} from one job to the other.", f"Pasar {art_es} {what_es} de una obra a la otra."),
                  (_L(lang, "Stage both jobs from one laydown yard.", "Preparar las dos obras desde un solo patio de acopio.") if yard else
                   _L(lang, "Share one equipment and delivery plan.", "Compartir un plan de equipos y entregas."))],
                 feasible if feasible else None, items, gives)

    # 4. share preparation: survey, right-of-way, permits and access roads (the land they could share; no same months needed;
    # with nothing ahead, the surveys and outage records still serve the next work in the area)
    items = _items(est, ("permits", "access_roads", "row_cost"))
    shared_land = next((it for it in est.get("items") or [] if it["id"] == "shared_land"), None)
    gives = {s: [{"what": _L(lang, "Shares its survey, route and schedule data with the other company",
                             "Comparte con la otra empresa sus datos de estudio de campo, trazado y calendario"),
                  "usd": None, "months": None, "source": src}]
             for s, src in (("a", src_a), ("b", src_b))}
    priced = bool(items or shared_land)
    plan("share_prep",
         _L(lang, "Share the survey and the paperwork" if priced else "Share the survey and schedule data",
            "Compartir el estudio de campo y los trámites" if priced else "Compartir estudios de campo y calendarios"),
         _L(lang, "The two companies share " + ("the survey, permits and access roads along the stretch they share" if priced
                                                  else "survey data, schedules and outage plans for the area") + ", so the groundwork is done once."
                  + (f" About {shared_land['low']:g}-{shared_land['high']:g} acres of corridor could be shared." if shared_land else "")
                  + ("" if priced else " At this distance no saving is priced: it avoids surprises, not costs."),
            "Las dos empresas comparten " + ("el estudio de campo, los permisos y los caminos de acceso del tramo en común" if priced
                                              else "datos de estudio de campo, calendarios y planes de corte de la zona") + ", así que el trabajo previo se hace una sola vez."
            + (f" Podrían compartirse unos {shared_land['low']:g}-{shared_land['high']:g} acres de corredor." if shared_land else "")
            + ("" if priced else " A esta distancia no hay un ahorro cuantificado: evita sorpresas, no costos.")),
         ([_L(lang, "Exchange survey and route data for the shared stretch.", "Intercambiar los datos de estudio y trazado del tramo compartido."),
           _L(lang, "File permits for the shared stretch together.", "Tramitar juntos los permisos del tramo compartido."),
           _L(lang, "Build the access roads once and let both crews use them.", "Construir los caminos de acceso una vez y que los usen las dos cuadrillas.")]
          if priced else
          [_L(lang, "Exchange survey and route data for the area.", "Intercambiar los datos de estudio y trazado de la zona."),
           _L(lang, "Share schedules and outage plans so neither surprises the other.", "Compartir calendarios y planes de corte para que ninguna sorprenda a la otra.")]),
         None, items, gives)

    # nothing ahead on either side (as filed): the only honest plan is to check what is left
    if not any(ahead):
        plan("status_check", _L(lang, "Check what is left to build", "Comprobar qué queda por construir"),
             _L(lang, "As filed, both build windows have ended; the filings don't say what work remains, so the first step is to compare "
                      "each project's current status.",
                "Según lo publicado, las dos ventanas de obra ya terminaron; los documentos no dicen qué obra queda, así que el primer paso es "
                "comparar el estado actual de cada proyecto."),
             [_L(lang, "Ask each company for its project's current status.", "Pedir a cada empresa el estado actual de su proyecto."),
              _L(lang, "If work remains, compare schedules for it.", "Si queda obra, comparar sus calendarios.")],
             None, [], {"a": [], "b": []})
    return menu


def _why_not(case: dict, menu: list[dict], lang: str) -> list[dict]:
    """The plan kinds this pair's data does NOT support, each with the plain reason (so two plans read as the data's
    answer, not a thin one)."""
    have = {p["kind"] for p in menu}
    rec = case["rec"]
    mi = rec["distance_mi"]
    wa, wb = case["bounds"]["a"], case["bounds"]["b"]
    now = case["now"]
    both_ahead = all(w and w[1] >= now for w in (wa, wb))
    out = []
    if "one_outage" not in have:
        if not (rec["tier"] in ("touching", "row") or rec.get("shared_station")):
            why = _L(lang, f"One shared outage needs the projects to meet, cross or run within 1 mi of each other; these are {mi:.1f} mi apart.",
                     f"Un solo corte compartido requiere que los proyectos se toquen, se crucen o corran a menos de 1 mi; están a {mi:.1f} mi.")
        else:
            why = _L(lang, "One shared outage needs build months both filings still have ahead.",
                     "Un solo corte compartido requiere meses de obra que los dos documentos aún tengan por delante.")
        out.append({"kind": "one_outage", "why": why})
    if "shift" not in have:
        if case["feasible"]:
            why = _L(lang, "No schedule has to move: the filed windows already share months.",
                     "Ningún calendario tiene que moverse: las ventanas publicadas ya comparten meses.")
        elif not both_ahead:
            why = _L(lang, "Moving a schedule needs both projects still ahead, as filed.",
                     "Mover un calendario requiere que los dos proyectos sigan por delante, según lo publicado.")
        else:
            why = _L(lang, "The filed windows are more than 3 years apart: moving one that far isn't offered.",
                     "Las ventanas publicadas están a más de 3 años: no se propone mover un calendario tanto.")
        out.append({"kind": "shift", "why": why})
    if "stagger" not in have:
        out.append({"kind": "stagger", "why": _L(lang, "Handing crews from one job to the other needs both still ahead and at most a year apart.",
                                                 "Pasar cuadrillas de una obra a otra requiere que las dos sigan por delante y a un año como mucho.")})
    return out


def _select(menu: list[dict]) -> list[dict]:
    """2-3 DISTINCT kinds from the menu: the priced ones first, the kind order breaking ties."""
    if len(menu) <= 3:
        return menu
    ranked = sorted(menu, key=lambda p: (-((p.get("savings") or {}).get("high") or 0), KIND_ORDER.index(p["kind"])))
    keep = {p["id"] for p in ranked[:3]}
    return [p for p in menu if p["id"] in keep]


# ----------------------------------------------------------------------------- checks


def _plan_facts(case: dict, menu: list[dict]) -> tuple[list[dict], "ag.Allowed"]:
    """The fact sheet a plan's text is checked against: the draft's facts (both filings, the estimate) plus every figure in
    the menu (savings, each side's share, months, windows)."""
    facts = list(case["facts"])
    for p in menu:
        if p.get("savings"):
            facts.append({"key": f"plan.{p['id']}.savings", "text": f"{p['title']}: {_usd(p['savings']['low'], p['savings']['high'])}",
                          "value": [p["savings"]["low"], p["savings"]["high"]], "unit": "USD"})
        for inc in p["incentives"]:
            for g in inc["gains"] + inc["gives_up"]:
                if g.get("usd"):
                    facts.append({"key": f"plan.{p['id']}.{inc['utility']}.usd", "text": g["what"], "value": list(g["usd"]), "unit": "USD"})
                if g.get("months") is not None:
                    facts.append({"key": f"plan.{p['id']}.{inc['utility']}.months", "text": g["what"], "value": g["months"], "unit": "months"})
                else:
                    facts.append({"key": f"plan.{p['id']}.{inc['utility']}.what", "text": g["what"], "value": None, "unit": None})
        if p.get("_window"):
            w = p["_window"]
            facts.append({"key": f"plan.{p['id']}.window", "text": f"{_span(w)} ({_months_in(w)} months)",
                          "value": [ng._iso(w[0]), ng._iso(w[1])], "unit": "dates"})
        if p.get("shift"):
            facts.append({"key": f"plan.{p['id']}.shift", "text": f"{p['shift']['months']} months later", "value": p["shift"]["months"], "unit": "months"})
        for s in p["steps"] + [p["summary"]]:
            facts.append({"key": f"plan.{p['id']}.text", "text": s, "value": None, "unit": None})
    allowed = ag.allowed_numbers(facts)
    sp = case["split"]
    allowed.pct += [float(x) for x in sp["shares"]] + [100.0 - float(x) for x in sp["shares"]]
    allowed.pct += [float(x) for x in sp["caps"]] + [float(x) for x in sp["over"] if x]
    allowed.pct += [float(r["shares"][k]) for r in case["rules"] for k in (0, 1)]
    return facts, allowed


def _text_ok(text: str, facts: list, allowed, max_chars: int = MAX_TEXT) -> tuple[bool, str | None]:
    ok, why, _ = ag.check_text(text, facts, copy.deepcopy(allowed), max_chars)
    if not ok:
        return False, why
    m = ng._SPEAKS_FOR.search(text)
    if m:
        return False, f"speaks for a utility ({m.group(0)!r}); an agent represents a published plan, never the company"
    m = ng._FIRST_PERSON.search(text)
    if m:
        return False, f"writes as {m.group(0)!r}, which reads as the company speaking"
    if re.search(r"(?i)\bwill\b", text):
        return False, "says 'will' about what a company does; plans are proposals"
    # a Spanish line that carries "gives up" half-translated ("pero daup paga", "pero da up que su inicio espera")
    if re.search(r"(?i)\bda\s?up\b|\bdaup\b|\bgives?\s+up\b", text) and re.search(r"(?i)\b(pero|paga|su|el|la|los|las|de)\b", text):
        return False, "an English phrase ('gives up') left half-translated in a Spanish line"
    return True, None


def _figure_checks(case: dict, p: dict) -> list[dict]:
    """Every figure a plan shows, traced to where it comes from (these are the pipeline's own numbers)."""
    out = []
    w = p.get("_window")
    wa, wb = case["bounds"]["a"], case["bounds"]["b"]
    if w:
        inside_a = wa and wa[0] <= w[0] and w[1] <= wa[1]
        inside_b = wb and wb[0] <= w[0] and w[1] <= wb[1]
        shifted = p.get("shift")
        if shifted:
            sw = ng._parse_ym(shifted["window"]["start"]), ng._parse_ym(shifted["window"]["end"])
            other = wb if shifted["side"] == "a" else wa
            ok = sw[0] <= w[0] and w[1] <= sw[1] and other[0] <= w[0] and w[1] <= other[1] and w[0] >= case["now"]
            why = (f"inside {case['A'] if shifted['side'] == 'a' else case['B']}'s window moved {shifted['months']} months later "
                   f"({_span(sw)}) and the other filed window ({_span(other)}), and after today")
        else:
            ok = bool(inside_a and inside_b) and w[0] >= case["now"]
            why = f"inside both filed windows ({case['A']} {_span(wa)}, {case['B']} {_span(wb)}) and not before today"
        out.append({"figure": f"window {_span(w)}", "ok": bool(ok), "reason": why if ok else "outside a filed window or in the past",
                    "source": f"{case['sources']['a'].get('label')}; {case['sources']['b'].get('label')}"})
    if p.get("shift"):
        s = p["shift"]
        out.append({"figure": f"{s['months']} months later", "ok": True,
                    "reason": "the gap between the two filed windows, plus the months they would then share",
                    "source": case["sources"][s["side"]].get("label")})
    for inc in p["incentives"]:
        for g in inc["gains"]:
            if g.get("usd"):
                out.append({"figure": f"{gl.UTILITY_SHORT.get(inc['utility'], inc['utility'])}: {_usd(*g['usd'])}", "ok": True,
                            "reason": f"{g.get('_keep_pct', 0):g} % of the estimate's item (it pays "
                                      f"{100 - g.get('_keep_pct', 0):g} % of one shared cost, {_rule_word(case['split']['label'])}): {g.get('_basis') or g['what']}",
                            "source": g.get("source")})
            if g.get("outages_avoided"):
                out.append({"figure": "1 outage avoided", "ok": True,
                            "reason": "the projects meet, cross or share a station, so their outages at that point can be one",
                            "source": g.get("source")})
    return out


def _public(p: dict, case: dict) -> dict:
    """A plan as the API returns it (internal fields dropped, each figure's check attached)."""
    out = {k: v for k, v in p.items() if not k.startswith("_")}
    out["incentives"] = [{**inc, "gains": [{k: v for k, v in g.items() if not k.startswith("_")} for g in inc["gains"]],
                          "gives_up": [{k: v for k, v in g.items() if not k.startswith("_")} for g in inc["gives_up"]]}
                         for inc in p["incentives"]]
    out["checks"] = _figure_checks(case, p)
    out.setdefault("dropped", [])
    return out


def _template_recommend(case: dict, plans: list[dict], lang: str = "en") -> dict:
    """The plan that saves the most while asking neither side to move its filed dates; else the most saved."""
    def moves(p):
        return bool(p.get("shift"))

    best = sorted(plans, key=lambda p: (moves(p), -((p.get("savings") or {}).get("high") or 0), KIND_ORDER.index(p["kind"])))[0]
    sv = best.get("savings")
    if lang == "es":
        why = (f"Ahorra unos {_usd(sv['low'], sv['high'])} en total" if sv else "Es el paso que los dos documentos respaldan ahora")
        why += " sin mover ninguna fecha publicada." if not moves(best) else f", con un cambio de calendario de {best['shift']['months']} meses."
    else:
        why = (f"It saves about {_usd(sv['low'], sv['high'])} in all" if sv else "It is the step both filings support now")
        why += " without moving either filed date." if not moves(best) else f", for a {best['shift']['months']}-month schedule change."
    return {"plan": best["id"], "why": why}


_ID_WORDS = re.compile(r"\b(one_outage|share_prep|status_check)\b")
_ID_LEAD = re.compile(r"(?i)^\s*(?:the\s+)?[\"'‘“]?(stagger|shift)[\"'’”]?(?:\s+(?:plan|option))?(?=\s+(?:is|was|saves|keeps|gives|fits|works|lets)\b)")
_ID_PLAN = re.compile(r"(?i)\b(?:the\s+)?[\"'‘“]?(stagger|shift)[\"'’”]?\s+(?:plan|option)\b")


def _no_ids(text: str, titles: dict) -> str:
    """A menu id the model wrote in its text ('Stagger is recommended', 'the share_prep plan') said as the plan's title;
    'stagger' and 'shift' as ordinary verbs ('stagger the work') stay."""
    if not text:
        return text
    t = _ID_WORDS.sub(lambda m: f"'{titles.get(m.group(1), m.group(1).replace('_', ' '))}'", text)
    t = _ID_LEAD.sub(lambda m: f"'{titles.get(m.group(1).lower(), m.group(1))}'", t, count=1)
    t = _ID_PLAN.sub(lambda m: f"the '{titles.get(m.group(1).lower(), m.group(1))}' plan", t)
    # a goal is what the agent was given from the filing, never what the company prefers
    t = re.sub(r"\b(its|their) (preferred|desired|wished-for)\b", r"\1 goal of", t)
    return t[:1].upper() + t[1:] if t[:1] == "'" else t


# ----------------------------------------------------------------------------- the agents (Gemini)

AGENT_SYSTEM = (
    "You are an AI agent representing ONE electric utility's PUBLISHED construction plan for one project: its public filing, "
    "nothing else. You are not the utility and never speak for it: never say what the utility wants, plans, intends or would "
    "accept; write in the third person ('the filing shows', 'this plan gives'); never 'we' or 'our'; never 'will'. With the other "
    "utility's agent and a neutral coordinator, you look for ways the two projects could be built together. Choose only plan kinds "
    "from the MENU (the pipeline built it from the two filings; every figure in it is checked); use only its figures and the facts "
    "(every number you write must be copied from them). Argue from your goal: what your filing gains, what it would give up. "
    "Plain words for a newcomer: say 'crews set up once' not 'mobilization', 'the date it starts working' not 'in-service'. "
    "A date before today is said as past. Short sentences, no markdown."
)
AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "proposals": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string"}, "why": {"type": "string"},
            "gains": {"type": "array", "items": {"type": "string"}}, "gives_up": {"type": "array", "items": {"type": "string"}},
        }, "required": ["kind", "why"]}},
    },
    "required": ["proposals"],
}
COORD_SYSTEM = (
    "You are a NEUTRAL coordinator between two AI agents, each representing one utility's published construction plan. You never "
    "speak for either utility and never say 'will', 'agreed', 'committed' or 'guarantee'. Merge the two agents' proposals into 2 or "
    "3 DISTINCT plans (different kinds from the MENU, never two of one kind), written for someone new to utilities: a title of at "
    "most 8 plain words, a summary of at most 2 short sentences, at most 4 short steps, and for each company one plain sentence on "
    "what it gains and what it gives up. Use only the MENU's figures and the facts: every number you write must be copied from them. "
    "Then recommend one plan with one plain sentence on why, weighing both companies fairly. No jargon ('crews set up once', not "
    "'mobilization'); a date before today is said as past; no markdown. Never write a menu id (like stagger or share_prep) in "
    "the text: name a plan by what it does."
)
COORD_SCHEMA = {
    "type": "object",
    "properties": {
        "plans": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string"}, "title": {"type": "string"}, "summary": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}}, "proposed_by": {"type": "string"},
            "net_a": {"type": "string"}, "net_b": {"type": "string"},
        }, "required": ["kind", "title", "summary", "steps", "proposed_by", "net_a", "net_b"]}},
        "recommended": {"type": "object", "properties": {"kind": {"type": "string"}, "why": {"type": "string"}}, "required": ["kind", "why"]},
    },
    "required": ["plans", "recommended"],
}


def _menu_text(case: dict, menu: list[dict]) -> str:
    lines = []
    for p in menu:
        head = f"- {p['id']}: {p['title']}. {p['summary']}"
        if p.get("window"):
            head += f" Window: {_span(p['_window'])}."
        if p.get("savings") and p.get("split"):
            head += (f" Priced saving: {_usd(p['savings']['low'], p['savings']['high'])}; the one shared cost is paid {_rule_word(case['split']['label'])} "
                     f"({case['A']} pays {p['split']['shares'][0]:g} %, {case['B']} {p['split']['shares'][1]:g} %), so each saves the rest.")
        for inc in p["incentives"]:
            who = gl.UTILITY_SHORT.get(inc["utility"], inc["utility"])
            gains = "; ".join(g["what"] + (f" {_usd(*g['usd'])}" if g.get("usd") else "") for g in inc["gains"]) or "no priced saving"
            gives = "; ".join(g["what"] for g in inc["gives_up"]) or "nothing on its filed schedule"
            head += f" {who} gains: {gains}. {who} gives up: {gives}."
        lines.append(head)
    return "\n".join(lines)


def _agent_prompt(case: dict, side: str, menu: list[dict], lang: str) -> str:
    p = case["pa"] if side == "a" else case["pb"]
    other = "b" if side == "a" else "a"
    mine = [f for f in case["facts"] if f["key"].startswith(side + ".")]
    theirs = [f for f in case["facts"] if f["key"].startswith(other + ".") and f["key"].split(".")[1] in ("project", "window", "in_service", "kv")]
    shared = [f for f in case["facts"] if f["key"].startswith("pair.") or f["key"] == "as_of"]
    who = case["A"] if side == "a" else case["B"]
    out = [
        f"You represent {p.get('utility_name')}'s published plan ({who}) for project {p['id']}: {ag._display_name(p.get('name') or p['id'])}.",
        f"YOUR GOAL (a fair-share figure the pipeline computed from your filing's figures): {case['goals'][side]['text_en']}",
        "", "YOUR FILING:", *[f"- {f['text']}" for f in mine],
        "", "THE OTHER COMPANY'S PUBLISHED PLAN (public, for context):", *[f"- {f['text']}" for f in theirs],
        "", "THE PAIR:", *[f"- {f['text']}" for f in shared],
        "", "MENU (plan kinds this pair's data supports; ids first):", _menu_text(case, menu),
        "",
        'Answer as JSON: {"proposals": [1 or 2 of {"kind": a menu id, "why": at most 2 sentences on why this plan serves your '
        'filing, "gains": [at most 2 short strings], "gives_up": [at most 2 short strings]}], best first}.',
    ]
    if lang == "es":
        out.append("Write why, gains and gives_up in Spanish (numbers exactly as in the facts). Translate every English word, including \"gives up\" (say \"cede\"); keep company names and project ids exactly as written.")
    return "\n".join(out)


def _coord_prompt(case: dict, menu: list[dict], props: dict, findings: list[str] | None, lang: str) -> str:
    shared = [f for f in case["facts"] if f["key"].startswith("pair.") or f["key"] == "as_of" or f["key"].endswith(".project")]
    out = [
        f"Two agents represent the published plans of {case['A']} (agent A) and {case['B']} (agent B) for two projects close in place or time.",
        "", "FACTS:", *[f"- {f['text']}" for f in shared],
        f"- Agent A's goal: {case['goals']['a']['text_en']}", f"- Agent B's goal: {case['goals']['b']['text_en']}",
        f"- The cost split every plan uses (set by the pipeline between the two goals; don't call it traded or agreed): {_settle_text(case, 'en')}",
        "", "MENU (ids first; the only kinds and figures you may use):", _menu_text(case, menu),
        "", "PROPOSALS:",
    ]
    for side in ("a", "b"):
        for x in props.get(side) or []:
            out.append(f"- Agent {side.upper()} ({case['A'] if side == 'a' else case['B']}) proposes {x['kind']}: {x['why']}")
    if findings:
        out += ["", "THE PIPELINE DROPPED THESE FROM YOUR LAST ANSWER (fix each):", *[f"- {x}" for x in findings]]
    out += ["",
            'Answer as JSON: {"plans": [2 or 3 of {"kind": a menu id, "title": at most 8 words, "summary": at most 2 sentences, '
            '"steps": [at most 4 short strings], "proposed_by": "a", "b" or "coordinator", "net_a": one sentence on what '
            f'{case["A"]} gains and gives up, "net_b": the same for {case["B"]}}}], "recommended": {{"kind": a plan kind above, '
            '"why": one sentence}}}.']
    if lang == "es":
        out.append("Write every text in Spanish (numbers exactly as in the facts and the menu). Translate every English word, including \"gives up\" (say \"cede\"); keep company names and project ids exactly as written.")
    return "\n".join(out)


async def _ask(prompt: str, system: str, schema: dict) -> tuple[dict, bool, int]:
    t0 = time.perf_counter()
    try:
        raw, offline = await asyncio.wait_for(
            llm.complete_json(prompt, system=system, fallback=_NONE, timeout=CALL_TIMEOUT_S, schema=schema, cache=True,
                              surface=SURFACE, model=llm.AGENT_MODEL),
            CALL_TIMEOUT_S + 3,
        )
    except asyncio.TimeoutError:
        raw, offline = _NONE, True
    return raw, offline, round((time.perf_counter() - t0) * 1000)


def _agent_names(case: dict, lang: str) -> dict:
    return {"a": _L(lang, f"{case['A']}'s agent", f"Agente de {case['A']}"), "b": _L(lang, f"{case['B']}'s agent", f"Agente de {case['B']}")}


def _clean_props(raw, menu_ids: set, facts, allowed, who: str, trace: list, dropped: list) -> list[dict]:
    out = []
    if not isinstance(raw, dict) or raw.get("_none"):
        return out
    for x in (raw.get("proposals") or [])[:2]:
        if not isinstance(x, dict):
            continue
        kind = str(x.get("kind") or "").strip()
        why = str(x.get("why") or "").strip()
        if kind not in menu_ids:
            dropped.append({"claim": f"{who} proposed '{kind[:40]}'", "reason": "not a plan kind this pair's data supports"})
            trace.append({"agent": who, "step": "proposes", "text": f"'{kind[:40]}' (not on the menu)", "verdict": "dropped"})
            continue
        ok, reason = _text_ok(why, facts, allowed)
        if not ok:
            dropped.append({"claim": why[:200], "reason": reason})
            trace.append({"agent": who, "step": "proposes", "text": f"{kind}: (its reason was dropped: {reason})", "verdict": "dropped"})
            out.append({"kind": kind, "why": ""})
            continue
        out.append({"kind": kind, "why": why})
        trace.append({"agent": who, "step": "proposes", "text": f"{kind}: {why}", "verdict": "kept"})
    return out


_MONTH_WORDS = {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
                "December", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}


def _sentence_case(title: str, case: dict) -> str:
    """The LOOK's sentence case for an AI title ('Stagger Line Work and Share Heavy Equipment' -> 'Stagger line work and
    share heavy equipment'), keeping names: the utilities, the projects' places and words, month names, acronyms."""
    keep = set(_MONTH_WORDS) | {"DESC", "Georgia", "Power", "GTC", "MEAG", "Dalton", "Utilities", "Dominion", "Energy", "South", "Carolina"}
    for f in case["facts"]:
        if f["key"].split(".")[-1] in ("utility", "project", "places"):
            keep |= {w.strip(",.:;()'") for w in str(f.get("text") or "").split() if w[:1].isupper()}
    words = title.split(" ")
    out = []
    for k, w in enumerate(words):
        core = w.strip(",.:;()'")
        if k and core[:1].isupper() and core[1:].islower() and core not in keep:
            w = w.replace(core, core.lower(), 1)
        out.append(w)
    s = " ".join(out)
    return s[:1].upper() + s[1:]


def _apply_coord(raw, menu: list[dict], facts, allowed, case: dict, dropped: list, lang: str = "en") -> tuple[list[dict], dict | None, list[str]]:
    """The coordinator's plans laid over the menu's (figures always the menu's): (plans, recommended, findings)."""
    by_id = {p["id"]: p for p in menu}
    titles = {p["id"]: p["title"] for p in menu}
    findings: list[str] = []
    plans, seen = [], set()
    if not isinstance(raw, dict) or raw.get("_none"):
        return [], None, ["no answer"]
    for x in raw.get("plans") or []:
        if not isinstance(x, dict):
            continue
        kind = str(x.get("kind") or "").strip()
        if kind not in by_id:
            findings.append(f"'{kind[:40]}' is not a menu kind")
            dropped.append({"claim": f"a plan of kind '{kind[:40]}'", "reason": "not a plan kind this pair's data supports"})
            continue
        if kind in seen:
            findings.append(f"two plans of kind {kind}: each plan must be a different kind")
            dropped.append({"claim": f"a second '{kind}' plan", "reason": "the plans must be distinct kinds"})
            continue
        seen.add(kind)
        base = copy.deepcopy(by_id[kind])
        base["proposed_by"] = _agent_names(case, lang).get(str(x.get("proposed_by") or "").strip().lower(), _L(lang, "Coordinator", "Coordinador"))
        base["dropped"] = []

        def use(field, text, limit=MAX_TEXT, words=None):
            text = _no_ids(str(text or "").strip(), titles)
            if not text:
                return None
            if words and len(text.split()) > words:
                reason = f"longer than {words} words"
            else:
                ok, reason = _text_ok(text, facts, allowed, limit)
                if ok:
                    return text
            findings.append(f"{kind}.{field}: {reason}")
            base["dropped"].append({"claim": text[:200], "reason": reason})
            dropped.append({"claim": text[:200], "reason": reason})
            return None

        title = use("title", x.get("title"), 90, MAX_TITLE_WORDS)
        base["title"] = _sentence_case(title, case) if title else base["title"]
        summary = use("summary", x.get("summary"))
        if summary and len(re.findall(r"[.!?](?:\s|$)", summary)) > 2:
            findings.append(f"{kind}.summary: more than 2 sentences")
            base["dropped"].append({"claim": summary[:200], "reason": "more than 2 sentences"})
            summary = None
        base["summary"] = summary or base["summary"]
        steps = [s for s in (use("steps", s, 200) for s in (x.get("steps") or [])[:MAX_STEPS]) if s]
        base["steps"] = steps or base["steps"]
        for k, key in ((0, "net_a"), (1, "net_b")):
            net = use(key, x.get(key))
            if net:
                base["incentives"][k]["net"] = net
        plans.append(base)
    rec = raw.get("recommended") if isinstance(raw.get("recommended"), dict) else {}
    rk = str(rec.get("kind") or "").strip()
    why = _no_ids(str(rec.get("why") or "").strip(), {k: next((x["title"] for x in plans if x["kind"] == k), v) for k, v in titles.items()})
    recommended = None
    if rk in seen:
        ok, reason = _text_ok(why, facts, allowed)
        if ok:
            recommended = {"plan": rk, "why": why}
        else:
            findings.append(f"recommended.why: {reason}")
            dropped.append({"claim": why[:200], "reason": reason})
    elif rk:
        findings.append(f"recommended '{rk[:40]}' is not one of the plans")
    if len(plans) < 2:
        findings.append("fewer than 2 distinct plans")
    return plans, recommended, findings


async def _run_ai(case: dict, menu: list[dict], lang: str, trace: list, dropped: list) -> tuple[list[dict] | None, dict | None, str | None]:
    """(plans, recommended, failure reason)."""
    t0 = time.perf_counter()
    facts, allowed = _plan_facts(case, menu)
    ids = {p["id"] for p in menu}
    names = _agent_names(case, lang)
    coord = _L(lang, "Coordinator", "Coordinador")
    for side in ("a", "b"):
        trace.append({"agent": names[side], "step": "goal", "text": case["goals"][side]["text_es" if lang == "es" else "text_en"], "verdict": None})
    (ra, off_a, _), (rb, off_b, _) = await asyncio.gather(
        _ask(_agent_prompt(case, "a", menu, lang), AGENT_SYSTEM, AGENT_SCHEMA),
        _ask(_agent_prompt(case, "b", menu, lang), AGENT_SYSTEM, AGENT_SCHEMA),
    )
    if off_a and off_b:
        return None, None, "Gemini not configured" if not llm.configured() else "Gemini unavailable"
    props = {"a": _clean_props(ra, ids, facts, allowed, names["a"], trace, dropped),
             "b": _clean_props(rb, ids, facts, allowed, names["b"], trace, dropped)}
    for side, off in (("a", off_a), ("b", off_b)):
        if off:
            trace.append({"agent": names[side], "step": "proposes", "text": _L(lang, "No answer in time; its goal still counts.",
                                                                                 "Sin respuesta a tiempo; su objetivo sigue contando."), "verdict": None})
    if time.perf_counter() - t0 > DEADLINE_S - CALL_TIMEOUT_S:
        return None, None, "Gemini too slow"
    raw, off, _ = await _ask(_coord_prompt(case, menu, props, None, lang), COORD_SYSTEM, COORD_SCHEMA)
    if off:
        return None, None, "Gemini too slow" if llm.configured() else "Gemini not configured"
    d0 = len(dropped)
    plans, recommended, findings = _apply_coord(raw, menu, facts, allowed, case, dropped, lang)
    trace.append({"agent": coord, "step": "merges",
                  "text": (_L(lang, f"Merged the proposals into {len(plans)} plans: ", f"Unió las propuestas en {len(plans)} planes: ")
                           + "; ".join(p["title"] for p in plans)) if plans else _L(lang, "No usable plans.", "Ningún plan utilizable."),
                  "verdict": "kept" if plans and not findings else ("dropped" if not plans else "revised")})
    if findings and time.perf_counter() - t0 < REVISE_IF_UNDER_S:
        raw2, off2, _ = await _ask(_coord_prompt(case, menu, props, findings, lang), COORD_SYSTEM, COORD_SCHEMA)
        if not off2:
            dropped2: list = []
            p2, r2, f2 = _apply_coord(raw2, menu, facts, allowed, case, dropped2, lang)
            if len(p2) >= 2 and len(f2) < len(findings):
                trace.append({"agent": coord, "step": "revises",
                              "text": _L(lang, f"Revised after the pipeline dropped {len(findings)} item(s): {findings[0]}",
                                         f"Revisó tras descartar el sistema {len(findings)} elemento(s): {findings[0]}"), "verdict": "revised"})
                plans, recommended, findings = p2, r2, f2
                dropped[d0:] = dropped[d0:] + dropped2
    if len(plans) < 2:  # fill from the menu: never fewer than two plans when the menu has them
        have = {p["kind"] for p in plans}
        for p in _select(menu):
            if len(plans) >= min(3, max(2, len(menu))):
                break
            if p["kind"] not in have:
                plans.append({**copy.deepcopy(p), "proposed_by": coord, "dropped": []})
        if len(plans) < min(2, len(menu)):
            return None, None, "Gemini's plans failed the checks"
    return plans[:3], recommended, None


# ----------------------------------------------------------------------------- one run


def _template_trace(case: dict, menu: list[dict], lang: str) -> list[dict]:
    """The same steps without Gemini: each goal, the menu kind each goal favors (the most priced saving for its side, then
    the least given up), the coordinator's pick. Labeled template."""
    goals, props = [], []
    for k, side in enumerate(("a", "b")):
        short = case["A"] if side == "a" else case["B"]
        who = f"Agente de {short} (plantilla)" if lang == "es" else f"{short}'s agent (template)"
        goals.append({"agent": who, "step": "goal", "text": case["goals"][side]["text_es" if lang == "es" else "text_en"], "verdict": None})
        best = sorted(menu, key=lambda p: (-sum((g.get("usd") or [0, 0])[1] for g in p["incentives"][k]["gains"]),
                                           len(p["incentives"][k]["gives_up"]), KIND_ORDER.index(p["kind"])))
        if best:
            props.append({"agent": who, "step": "proposes", "text": f"{best[0]['id']}: {best[0]['incentives'][k]['net']}", "verdict": "kept"})
    return goals + props  # the goals first, so the pipeline's settled split is said before the proposals


async def _plans(case: dict, lang: str, ai: bool) -> tuple[dict, bool]:
    t0 = time.perf_counter()
    menu = _menu(case, lang)
    coord = _L(lang, "Coordinator", "Coordinador")
    for p in menu:
        p["proposed_by"] = coord
        p["dropped"] = []
    trace: list = []
    dropped: list = []
    failure = None
    plans = recommended = None
    by = "template"
    if ai and len(menu) >= 2:
        try:
            plans, recommended, failure = await asyncio.wait_for(_run_ai(case, menu, lang, trace, dropped), DEADLINE_S + 4)
        except asyncio.TimeoutError:
            plans, failure = None, "Gemini too slow"
        if plans:
            by = "gemini"
    elif not ai:
        failure = "Plain version requested"
    else:
        failure = "only one kind of plan fits this pair"
    if not plans:
        trace = _template_trace(case, menu, lang) + ([{"agent": _L(lang, "Pipeline", "Sistema"), "step": "fallback",
                                                        "text": f"{_L(lang, FALLBACK_LABEL, FALLBACK_LABEL_ES)} ({failure}).", "verdict": None}] if failure else [])
        plans = _select(menu)
        dropped = [] if by == "template" else dropped
    if not recommended or recommended["plan"] not in {p["id"] for p in plans}:
        recommended = _template_recommend(case, plans, lang)
    # the split every plan uses, settled between the two goals by the pipeline: said right after the goals
    at = max((k for k, t in enumerate(trace) if t.get("step") == "goal"), default=-1) + 1
    trace.insert(at, {"agent": _L(lang, "Pipeline", "Sistema"), "step": "settles", "text": _settle_text(case, lang), "verdict": None})
    public = [_public(p, case) for p in plans]
    n_fig = sum(len(p["checks"]) for p in public)
    trace.append({"agent": _L(lang, "Pipeline", "Sistema"), "step": "checks",
                  "text": _L(lang,
                             f"Checked {n_fig} figures against the two filings and the cost sources: every money figure is the estimate's, "
                             f"every window sits inside the filed windows" + (f"; dropped {len(dropped)} claim(s) the data can't support" if dropped else ""),
                             f"Comprobó {n_fig} cifras con los dos documentos y las fuentes de costos: cada cifra de dinero es de la estimación y "
                             f"cada ventana cae dentro de las ventanas publicadas" + (f"; descartó {len(dropped)} afirmación(es) sin respaldo" if dropped else "")),
                  "verdict": "kept" if not dropped else "revised"})
    trace.append({"agent": coord if by == "gemini" else _L(lang, "Coordinator (template)", "Coordinador (plantilla)"), "step": "recommends",
                  "text": f"{next(p['title'] for p in plans if p['id'] == recommended['plan'])}: {recommended['why']}", "verdict": "kept"})
    companies = []
    for side, p in (("a", case["pa"]), ("b", case["pb"])):
        src = case["sources"][side]
        companies.append({
            "utility": p["utility"],
            "name": p.get("utility_name") or gl.UTILITIES.get(p["utility"], (p["utility"],))[0],
            "short": case["A"] if side == "a" else case["B"],
            "project": {"id": p["id"], "name": ag._display_name(p.get("name") or p["id"]), "page": src.get("page"),
                        "source_url": src.get("url"), "source": src.get("label")},
            "goal": case["goals"][side]["text_es" if lang == "es" else "text_en"],
            "goal_cap_pct": case["goals"][side]["cap_pct"],
            "window": case["goals"][side]["window"],
        })
    out = {
        "pair": case["overlap_id"],
        "as_of": case["as_of"].isoformat(),
        "lang": lang,
        "companies": companies,
        "plans": public,
        "recommended": recommended,
        "trace": trace,
        "dropped": dropped,
        # the two goals and the split every plan uses (settled between them by the pipeline, never by an agent)
        "conflict": {"split": case["conflict"]["split"], "text": _settle_text(case, lang), "rule": case["split"]["rule"],
                     "shares": case["split"]["shares"], "caps": case["split"]["caps"], "over": case["split"]["over"]},
        "fallback": by != "gemini",
        "fallback_label": _L(lang, FALLBACK_LABEL, FALLBACK_LABEL_ES) if by != "gemini" else None,
        "fallback_reason": failure if by != "gemini" else None,
        "model": llm.AGENT_MODEL if by == "gemini" else None,
        "by": by,
        "menu": [p["id"] for p in menu],
        # the kinds this pair's data does not support, each with its plain reason
        "not_offered": _why_not(case, menu, lang),
        "disclaimer": ag.DISCLAIMER_ES if lang == "es" else ag.DISCLAIMER,
        "how": _L(lang,
                  "Two agents each represent one company's published plan and propose the plan kinds that serve it; a neutral "
                  "coordinator merges them into distinct plans and recommends one. The pipeline (code, not Gemini) sets every figure "
                  "from the two filings and the sourced cost estimate, settles the cost split between the two goals, and drops any "
                  "claim the data can't support.",
                  "Dos agentes representan cada uno el plan publicado de una empresa y proponen los tipos de plan que le convienen; un "
                  "coordinador neutral los une en planes distintos y recomienda uno. El sistema (código, no Gemini) fija cada cifra a partir "
                  "de los dos documentos y la estimación de costos con fuentes, resuelve el reparto entre los dos objetivos y descarta lo que "
                  "los datos no respaldan."),
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    cacheable = not (ai and by != "gemini" and failure not in ("Plain version requested", "only one kind of plan fits this pair"))
    return out, cacheable


async def run(pair: str, months: int = gl.WINDOW_DEFAULT, lang: str = "en", ai: bool = True, as_of: date | None = None) -> dict:
    t0 = time.perf_counter()
    case = await run_in_threadpool(_case, pair, months, as_of)
    key = (case["st_key"], case["overlap_id"], months, lang, bool(ai), case["as_of"].isoformat())
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
    if hit is not None:
        out = copy.deepcopy(hit)
        out["cached"] = True
        out["ms"] = round((time.perf_counter() - t0) * 1000)
        return out
    task = _inflight.get(key)
    if task is None:
        task = asyncio.ensure_future(_plans(case, lang, ai))
        _inflight[key] = task
        task.add_done_callback(lambda _t, k=key: _inflight.pop(k, None))
    res, cacheable = await asyncio.shield(task)
    out = copy.deepcopy(res)
    out["cached"] = False
    if cacheable:
        with _cache_lock:
            _cache[key] = copy.deepcopy(out)
            while len(_cache) > CACHE_MAX:
                _cache.popitem(last=False)
    return out


async def plan_terms(pair: str, months: int, plan) -> tuple[dict | None, dict]:
    """A chosen plan as terms for the drafted agreement (agreement._base): its window, the estimate items it prices and the
    split its savings use. `plan`: a plan id or a plan object (only its id is read: every figure is re-derived here)."""
    pid = plan.get("id") if isinstance(plan, dict) else plan
    pid = str(pid or "").strip()
    # the plans the viewer saw (the agents' run, cached) when there is one; else the template's, which carry the SAME
    # window, items and split (the agents never set a figure), so choosing a plan never spends a Gemini call by itself
    case = await run_in_threadpool(_case, pair, months, None)
    res = None
    with _cache_lock:
        for lang in LANGS:
            hit = _cache.get((case["st_key"], case["overlap_id"], months, lang, True, case["as_of"].isoformat()))
            if hit is not None and hit.get("by") == "gemini":
                res = copy.deepcopy(hit)
                break
    if res is None:
        res = await run(pair, months, "en", False)
    p = next((x for x in res["plans"] if x["id"] == pid), None)
    if p is None:  # a menu kind the AI didn't pick is still a valid plan for the draft
        p = next((_public(x, case) for x in _menu(case) if x["id"] == pid), None)
    if p is None:
        return None, {"plan": pid, "applied": False, "reason": "not a plan this pair's data supports"}
    sp = p.get("split") or {k: case["split"][k] for k in ("rule", "label", "shares", "basis")}
    rule = next((r for r in case["rules"] if r["id"] == sp["rule"]), None)
    w = p.get("window")
    terms = {
        "scope": list(p.get("items") or []),
        "split": {"id": sp["rule"], "basis": {"by_length": "length", "by_kv": "kv", "equal": "equal"}[sp["rule"]], "pct": list(sp["shares"]),
                  "rule": rule["rule"] if rule else sp["label"], "rationale": rule["rationale"] if rule else sp["basis"]},
        "window": (ng._parse_ym(w["start"]), ng._parse_ym(w["end"])) if w else None,
        "by": "gemini" if res["by"] == "gemini" else "plain",
        "round": None,
        "text": f"Terms from the collaboration plan '{p['title']}' ({p.get('proposed_by') or 'Coordinator'}), every figure checked against both filings",
        "window_word": "Plan's joint window",
        "aligned": p["kind"] == "shift",
        "plan": {"id": p["id"], "kind": p["kind"], "title": p["title"]},
    }
    return terms, {"plan": p["id"], "title": p["title"], "applied": True, "by": terms["by"], "text": terms["text"]}


# ------------------------------------------------------------------------------- Hear the negotiation
# The trace of the agents' work can be heard (Build together, "Watch the AI agents work"): each company's agent speaks its own
# lines in its own voice (company A the presenter voice, George; company B the analyst voice, Matilda) and the neutral
# coordinator speaks with the presenter voice. Only lines the trace shows and the pipeline kept are read, word for word (a
# plan kind's id is said in its plain words, as the page shows it): each company's goal as the filings state it, each
# proposal that passed the checks, the coordinator's merge, revision and recommendation. The pipeline's own lines and
# anything it dropped are not read aloud. Nothing is added.
KIND_SAY = {
    "en": {"one_outage": "One shared outage", "shift": "Move one schedule", "share_prep": "Share the prep work",
           "stagger": "Hand over between jobs", "status_check": "Compare notes"},
    "es": {"one_outage": "Un solo corte", "shift": "Mover un calendario", "share_prep": "Compartir la preparación",
           "stagger": "Relevo entre obras", "status_check": "Comparar avances"},
}
_SPOKEN_STEPS = ("goal", "proposes", "merges", "revises", "recommends")


def _side_of(agent: str, shorts: dict) -> str | None:
    """'DESC's agent', 'Agente de DESC (plantilla)', 'Coordinator', ... -> 'a' | 'b' | 'coord' | None (the pipeline)."""
    a = str(agent or "")
    if a.startswith(("Coordinator", "Coordinador")):
        return "coord"
    for side, short in shorts.items():
        if short and (a.startswith(f"{short}'s agent") or a.startswith(f"Agente de {short}")):
            return side
    return None


def spoken_line(step: dict, lang: str) -> str:
    """The line as the page shows it: a leading plan-kind id ("stagger: ...") is said in its plain words."""
    kinds = KIND_SAY.get(lang, KIND_SAY["en"])
    return re.sub(r"^([a-z_]+):\s*", lambda m: f"{kinds[m.group(1)]}: " if m.group(1) in kinds else m.group(0), str(step["text"]).strip())


def add_voice(out: dict) -> dict:
    """Register the spoken trace lines with voice.py and put the keys on the response (extra fields; nothing else changes):
    trace[i]["voice"] = {key, role, side, text, lang, who} | None, out["voice"] = {roles, attribution}. The keys depend on the
    configured voices, so this runs per request, on the caller's copy."""
    lang = out["lang"] if out.get("lang") in LANGS else "en"
    shorts = {"a": (out.get("companies") or [{}, {}])[0].get("short"), "b": (out.get("companies") or [{}, {}])[1].get("short")}
    ai = out.get("by") == "gemini"
    lines: list[tuple[dict, str, str]] = []
    for step in out.get("trace") or []:
        step["voice"] = None
        side = _side_of(step.get("agent"), shorts)
        if not side or step.get("step") not in _SPOKEN_STEPS or not str(step.get("text") or "").strip():
            continue
        if step["step"] == "proposes" and step.get("verdict") != "kept":
            continue  # dropped by the pipeline, or "no answer in time": not read aloud
        if step.get("verdict") == "dropped":
            continue
        step["voice"] = {}
        lines.append((step["voice"], side, spoken_line(step, lang)))
    texts = [x[2] for x in lines]
    for i, (holder, side, text) in enumerate(lines):
        role = ng.VOICE_ROLE[side if side in ("a", "b") else "summary"]
        key = voice.register(text, lang, role, prev_text=texts[i - 1] if i else None, next_text=texts[i + 1] if i + 1 < len(texts) else None)
        holder.update({"key": key, "role": role, "side": side, "text": text, "lang": lang, "who": ng.who_label(side, shorts.get(side) or "", lang, ai)})
    out["voice"] = {"roles": {"a": "presenter", "b": "analyst", "coord": "presenter"}, "attribution": voice.ATTRIBUTION}
    return out


# ----------------------------------------------------------------------------- the route


class PlansIn(BaseModel):
    pair: str
    lang: str | None = "en"
    ai: bool | None = True
    window_months: int | None = None


@router.post("/api/gridlock/plans")
@limiter.limit("30/minute")
async def plans(request: Request, body: PlansIn):
    """2-3 distinct ways the pair's two published plans could be built together, each with what every company gains and
    gives up, proposed by three agents (one per company's published plan and a neutral coordinator), every figure the
    pipeline's; a labeled template without Gemini."""
    pair = (body.pair or "").strip()
    if not pair or len(pair) > 200 or "~" not in pair:
        raise HTTPException(status_code=422, detail="pair must be an overlap id like DESC-6809T~GA-20793")
    lang = (body.lang or "en").strip().lower()
    if lang not in LANGS:
        raise HTTPException(status_code=422, detail="lang must be 'en' or 'es'")
    months = gl.WINDOW_DEFAULT if body.window_months is None else body.window_months
    if not (0 <= months <= gl.WINDOW_MAX):
        raise HTTPException(status_code=422, detail=f"window_months must be between 0 and {gl.WINDOW_MAX}")
    out = await run(pair, months, lang, body.ai is not False)  # a fresh copy each time (cache hits are deep-copied)
    return await run_in_threadpool(add_voice, out)
