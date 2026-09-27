"""Negotiate (Sperry GridLock, Build together): two Gemini agents negotiate the terms of a draft coordination
proposal for one overlap, and the PIPELINE verifies every turn.

  POST /api/negotiate/{overlap_id}?lang=en&window_months=24&ai=true

Agent A reads only DESC's public filing for its project (its window as filed, kV, kind, length, place) and agent B
only Georgia's; both see the shared rough estimate's items and the split rules allowed for the pair. They are AI
agents reading public filings, NOT the utilities, and never speak for them. Up to 3 rounds (A, then B) of
structured proposals: {joint_window {start, end}, scope [estimate item ids], split {rule, a_pct, b_pct},
concerns [short strings], note, accept}.

After EVERY turn deterministic Python (verify() below, not Gemini) checks it against both filings:
  - the window lies inside both filed build windows and not before today (as_of); when the filed windows share no
    months, or the months they share have passed, the only valid window is none, and the next step is checking
    each project's current status
  - every scope id is one of the estimate's items
  - the split rule is one allowed for this pair (by filed length needs both lengths, by kV class both voltages,
    50/50 always), its shares are that rule's shares and add up to 100
  - every number in the free text passes agreement.py's checker by kind (money, unit cost, split %, plain numbers,
    dates said as past when they are), and no sentence speaks for a utility
A rejected turn goes back to its agent with the findings for one revision: the agentic loop. The negotiation ends
when an agent accepts, unchanged, a verified proposal the other agent put on the table, or after 3 rounds, with at
most 8 Gemini calls. The agreed terms can then feed the drafted agreement
(GET /api/agreement/{id}?negotiated=en|es|plain, see agreement.py).

Without Gemini (no key, quota, an error, too slow) a deterministic rule-based negotiation runs instead, labeled
"Plain version": A opens with the overlap window and a split rule, B counters with another rule, and they settle
on the filed-length split (50/50 when a length isn't filed). It goes through the same verifier.
"""

import asyncio
import copy
import itertools
import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

import agreement as ag
import gridlock as gl
import llm
import voice
from limiter import limiter

router = APIRouter(tags=["negotiate"])
log = logging.getLogger("uvicorn.error")

MAX_ROUNDS = 3
MAX_CALLS = 8  # 6 turns + 2 revisions after a rejection
CALL_TIMEOUT_S = 10  # per Gemini request (complete_json may ask twice on bad JSON)
CALL_DEADLINE_S = 25  # one turn, both of those requests
DEADLINE_S = 110  # the whole Gemini negotiation, then the plain version answers
LANGS = ("en", "es")
WHICH = ("en", "es", "plain")  # which negotiation's terms a draft uses (agreement.py ?negotiated=)
MAX_CONCERNS = 3
MAX_CONCERN_CHARS = 220
MAX_NOTE_CHARS = 300
CACHE_MAX = 128
RULE_IDS = ("by_length", "by_kv", "equal")
MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DISCLAIMER = ag.DISCLAIMER
DISCLAIMER_ES = ag.DISCLAIMER_ES
NEG_TEXT = {
    "gemini": "Terms negotiated by two AI agents reading each utility's public filing, verified against the filings",
    "plain": "Terms from the plain rule-based negotiation between the two filings, verified against the filings",
}

_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()
_inflight: dict = {}
_live: dict = {}  # a running Gemini negotiation's progress, by cache key (GET .../live reads it)
_NONE = {"_none": True}

# a sentence that speaks FOR a utility (what it wants, plans, would accept) instead of reading its filing
_UTIL = (
    r"(?:DESC|Dominion(?:\s+Energy)?(?:\s+South\s+Carolina)?|Georgia\s+Power|GPC|GTC|MEAG(?:\s+Power)?|Dalton(?:\s+Utilities)?"
    r"|Georgia\s+Transmission(?:\s+Corp\.?)?)"
)
_SPEAKS_FOR = re.compile(
    r"(?i)\b(?:on\s+behalf\s+of|speak(?:s|ing)?\s+for|we\s+at|our\s+(?:company|utility|customers|ratepayers|crews)"
    r"|en\s+nombre\s+de|nuestra\s+empresa|nuestros\s+clientes)\b"
    rf"|\b{_UTIL}\s+(?:wants?|prefers?|insists?|demands?|intends?|expects?|would\s+(?:like|prefer|accept|agree|want)|is\s+willing"
    r"|plans?\s+to|will|quiere|prefiere|insiste|exige|aceptar[ií]a|planea)\b"
)
# "we" / "our" reads as the utility speaking: an agent writes about the filing in the third person
_FIRST_PERSON = re.compile(r"(?i)\b(?:we|our|ours|nosotros|nosotras|nuestr[oa]s?)\b")


def _L(lang: str, en: str, es: str) -> str:
    return es if lang == "es" else en


def _ym(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def _iso(ym) -> str:
    return f"{ym[0]:04d}-{ym[1]:02d}"


def _mt(ym, lang: str = "en") -> str:
    return f"{(MONTHS_ES if lang == 'es' else ag.MONTHS)[ym[1] - 1]} {ym[0]}"


def _span(w, lang: str = "en") -> str:
    return f"{_mt(w[0], lang)} {_L(lang, 'to', 'a')} {_mt(w[1], lang)}"


def _parse_ym(v):
    """'2027-01' or '2027-01-15' -> (2027, 1); '' or None -> None; anything else -> 'bad'."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if not m:
        return "bad"
    y, mo = int(m[1]), int(m[2])
    if not (1 <= mo <= 12 and 1990 <= y <= 2100):
        return "bad"
    return (y, mo)


def _usd_range(lo, hi) -> str:
    return ag._usd_range(lo, hi)


# ----------------------------------------------------------------------------- the case


def _rules(pa: dict, pb: dict) -> list[dict]:
    """The split rules allowed for this pair, each with the shares it gives (A's, B's) and the draft's wording."""
    A, B = ag.SHORT.get(pa["utility"], pa["utility"]), ag.SHORT.get(pb["utility"], pb["utility"])
    out = []
    ma, mb = pa.get("miles"), pb.get("miles")
    if isinstance(ma, (int, float)) and isinstance(mb, (int, float)) and ma > 0 and mb > 0:
        s = ag._split(pa, pb)  # the draft's own rule: the same shares and wording
        out.append({
            "id": "by_length", "label": "By filed length", "label_es": "Por longitud publicada", "shares": list(s["pct"]),
            "basis": f"each project's share of the combined filed length: {A} {ma:g} mi, {B} {mb:g} mi",
            "basis_es": f"la parte de cada proyecto en la longitud publicada total: {A} {ma:g} mi, {B} {mb:g} mi",
            "rule": s["rule"], "rationale": s["rationale"],
        })
    kva = [int(k) for k in (pa.get("kv") or []) if isinstance(k, (int, float)) and k > 0]
    kvb = [int(k) for k in (pb.get("kv") or []) if isinstance(k, (int, float)) and k > 0]
    if kva and kvb:
        ka, kb = max(kva), max(kvb)
        pct_a = round(100 * ka / (ka + kb))
        out.append({
            "id": "by_kv", "label": "By kV class", "label_es": "Por clase de kV", "shares": [pct_a, 100 - pct_a],
            "basis": f"each project's highest filed voltage: {A} {ka} kV, {B} {kb} kV",
            "basis_es": f"la tensión publicada más alta de cada proyecto: {A} {ka} kV, {B} {kb} kV",
            "rule": f"Shared costs split by each project's highest filed voltage: {A} {ka} kV ({pct_a} %), {B} {kb} kV ({100 - pct_a} %).",
            "rationale": "Negotiated: higher-voltage work needs larger crews and heavier equipment, so each project's share follows its highest filed voltage. Each utility keeps paying for its own project's scope.",
        })
    out.append({
        "id": "equal", "label": "50/50", "label_es": "50/50", "shares": [50, 50],
        "basis": "equal shares", "basis_es": "partes iguales",
        "rule": "Shared costs split equally (50 % each).",
        "rationale": "Negotiated: an equal split of the shared items, whatever each project's size. Each utility keeps paying for its own project's scope.",
    })
    return out


def _why_rule_missing(rule: str, pa: dict, pb: dict, lang: str) -> str:
    if rule == "by_length":
        miss = [p for p in (pa, pb) if not (isinstance(p.get("miles"), (int, float)) and p["miles"] > 0)]
        return _L(lang, f"it needs both filed lengths, and {', '.join(p['id'] for p in miss)} gives none",
                  f"necesita las dos longitudes publicadas, y {', '.join(p['id'] for p in miss)} no da ninguna")
    miss = [p for p in (pa, pb) if not p.get("kv")]
    return _L(lang, f"it needs both filed voltages, and {', '.join(p['id'] for p in miss)} gives none",
              f"necesita las dos tensiones publicadas, y {', '.join(p['id'] for p in miss)} no da ninguna")


# ----------------------------------------------------------------------------- each agent's goal (J2)
#
# Sperry's judge (Sat 21:34) read the negotiation as a rubber stamp: identical offers, a 50/50 'by kV class' split, an
# agent accepting 73 % in round 1. Each agent now gets a GOAL from its own filing only: the smallest share of the shared
# costs an allowed split rule gives its side (the rule its filing's figures support) and its filed window (joint work only
# inside it). When the two caps add up to less than 100 %, the goals conflict and real counter-offers are expected;
# when every allowed rule gives the same shares there is nothing to argue about, and the page says so. Nothing is
# scripted: the counter-offers are the agents' own; the verifier only holds an agent to its goal before the last round.


def goals(case: dict) -> tuple[dict, dict]:
    """({side: goal}, conflict). A goal: cap_pct (the most its side should pay before the last round), rule (the split
    rule its filing supports), window (its filed window, the joint work's bounds), text_en / text_es (plain sentences)."""
    out = {}
    pref = {"by_length": 0, "by_kv": 1, "equal": 2}  # a filed figure before a flat split
    for k, side in enumerate(("a", "b")):
        p = case["pa"] if side == "a" else case["pb"]
        who = _short(case, side)
        best = min(case["rules"], key=lambda r: (r["shares"][k], pref.get(r["id"], 9)))
        cap = best["shares"][k]
        w = case["bounds"][side]
        ins = gl._date(p.get("in_service"))
        kind = gl.window_kind(p)
        kind_en = {"spending": "its construction years, from the filed spending", "planning": "its filed planning window",
                   "derived": "a window derived from its in-service date"}[kind]
        kind_es = {"spending": "sus años de obra, según el gasto publicado", "planning": "su ventana de planificación publicada",
                   "derived": "una ventana derivada de su fecha de puesta en servicio"}[kind]
        when_en = f"in service {ag.MONTHS[ins.month - 1]} {ins.year} as filed" if ins else "no in-service date filed"
        when_es = f"en servicio en {MONTHS_ES[ins.month - 1]} {ins.year} según lo publicado" if ins else "sin fecha de puesta en servicio publicada"
        flag = next(iter(gl.window_flags(p)), None)
        if ins and flag:  # the filing's own dates disagree (money filed after the in-service date): said, not hidden
            pct = re.match(r"\s*(\d+)\s*%", str(flag.get("detail") or ""))
            when_en += f", though the filing puts {pct.group(1) + ' % of' if pct else 'part of'} its spending after that date"
            when_es += f", aunque el documento sitúa {'el ' + pct.group(1) + ' % de' if pct else 'parte de'} su gasto después de esa fecha"
        win_en = f"; joint work only inside {kind_en}, {_span(w)}" if w else ""
        win_es = f"; obra conjunta solo dentro de {kind_es}, {_span(w, 'es')}" if w else ""
        out[side] = {
            "side": side,
            "utility": p["utility"],
            "cap_pct": cap,
            "rule": best["id"],
            "rule_label": best["label"],
            "window": {"start": _iso(w[0]), "end": _iso(w[1]), "kind": kind} if w else None,
            "text_en": f"Keep {who}'s filed schedule ({when_en}{win_en}) and aim to pay no more than {cap:g} % of the shared costs by our fair-share rule ({best['label'][:1].lower() + best['label'][1:]}: {best['basis']}).",
            "text_es": f"Mantener el calendario publicado de {who} ({when_es}{win_es}) y aspirar a pagar como máximo el {cap:g} % de los costes compartidos con nuestra regla de reparto justo ({best['label_es'][:1].lower() + best['label_es'][1:]}: {best['basis_es']}).",
        }
    conflict = out["a"]["cap_pct"] + out["b"]["cap_pct"] < 99.5
    same = len({tuple(r["shares"]) for r in case["rules"]}) == 1
    return out, {
        "split": conflict,
        "all_rules_equal": same,
        "text_en": (
            f"The goals conflict: by our fair-share rules (computed from the filed figures, not stated in either filing) {_short(case, 'a')}'s smallest share is {out['a']['cap_pct']:g} % and {_short(case, 'b')}'s "
            f"{out['b']['cap_pct']:g} %, which don't add up to 100 %, so the agents have to trade."
            if conflict else
            ("Nothing to argue about on cost: every allowed split rule gives the same shares, so the agents can agree quickly."
             if same else "The goals fit together: one allowed split gives each side no more than its smallest fair share.")
        ),
        "text_es": (
            f"Los objetivos chocan: con nuestras reglas de reparto justo (calculadas con las cifras publicadas, no dichas en ningún documento) la parte más baja de {_short(case, 'a')} es el {out['a']['cap_pct']:g} % y la de "
            f"{_short(case, 'b')} el {out['b']['cap_pct']:g} %, que no suman 100 %, así que los agentes tienen que negociar."
            if conflict else
            ("Nada que discutir sobre el coste: todas las reglas de reparto permitidas dan las mismas partes."
             if same else "Los objetivos encajan: una regla de reparto permitida da a cada parte no más de su parte justa más baja.")
        ),
    }


# a rejected turn whose findings are ALL about shape or wording (not the terms): fixed in a retry and hidden from the trace
_FORMAT_FINDINGS = (
    "is not a pair of months", "needs both a start and an end", "must be {start, end}", "no son dos meses",
    "is not one of the estimate's items", "the scope must list", "not an allowed split rule", "needs both shares as numbers",
    "concerns", "writes as", "isn't said as past", "longer than", "empty text", "must follow a figure", "the answer is not a proposal",
    "escribe como", "objeciones", "no es una partida", "no es una regla", "necesita las dos partes", "la respuesta no es",
)


def format_only(turn: dict) -> bool:
    f = turn["verdict"]["findings"]
    return bool(f) and not turn["verdict"]["ok"] and all(any(x in s for x in _FORMAT_FINDINGS) for s in f)


def public_turns(turns: list) -> list:
    """The trace a person reads: a turn rejected only for its format and then revised by the same agent is left out (it is
    kept in raw_turns); every turn is renumbered, its raw number kept."""
    out = []
    for k, t in enumerate(turns):
        nxt = turns[k + 1] if k + 1 < len(turns) else None
        if format_only(t) and nxt and nxt["agent"] == t["agent"] and nxt["revision"]:
            continue
        t2 = dict(t)
        t2["raw_n"] = t["n"]
        if t2["revision"] and out and out[-1]["agent"] == t2["agent"] and out[-1]["verdict"]["ok"] is False:
            pass  # a revision after a substantive rejection stays a revision
        elif t2["revision"]:
            t2["revision"] = False  # its format-only first try is hidden: this is the agent's turn
            if t2["kind"] == "revise":
                t2["kind"] = "propose" if not out else "counter"
        t2["n"] = len(out) + 1
        out.append(t2)
    return out


def _case(overlap_id: str, months: int, as_of: date | None = None) -> dict:
    """Everything the agents and the verifier need, from agreement.py's case (the same overlap, filings, estimate,
    windows and as-of rule the drafted agreement uses; nothing re-derived)."""
    b = ag._base(overlap_id, months, as_of)
    pa, pb, tl, extra, est, facts = b["pa"], b["pb"], b["tl"], b["extra"], b["est"], b["facts"]
    as_of = b["as_of"]
    now = _ym(as_of)
    wa, wb = tl["windows"]
    bounds = {"a": (_ym(wa[0]), _ym(wa[1])) if wa else None, "b": (_ym(wb[0]), _ym(wb[1])) if wb else None}
    feasible = None
    if extra["joint"] and extra["status"] in ("open", "future"):
        s, e = extra["joint"]
        lo, hi = max(_ym(s), now), _ym(e)
        if lo <= hi:
            feasible = (lo, hi)
    rules = _rules(pa, pb)
    items = [
        {"id": it["id"], "label": it["label"], "low": it["low"], "high": it["high"], "unit": it["unit"], "needs": it.get("needs")}
        for it in est.get("items") or []
    ]
    rule_facts = [{"key": f"rule.{r['id']}", "text": r["rule"], "value": r["shares"], "unit": "%", "source": ag.PROPOSAL_SOURCE} for r in rules]
    allowed = ag.allowed_numbers(facts + rule_facts)
    allowed.pct += [float(x) for r in rules for x in r["shares"]]
    usd = [it for it in items if it["unit"] == "USD"]
    for n in range(2, len(usd) + 1):  # a subtotal of any set of the estimate's items
        for combo in itertools.combinations(usd, n):
            allowed.money += [sum(it["low"] for it in combo), sum(it["high"] for it in combo)]
    projects = b["overlap"]["projects"]
    case = {
        "st_key": b["st_key"], "overlap_id": b["overlap"]["id"], "overlap": b["overlap"], "as_of": as_of, "now": now,
        "pa": pa, "pb": pb, "tl": tl, "extra": extra, "est": est, "facts": facts + rule_facts, "allowed": allowed,
        "bounds": bounds, "feasible": feasible, "rules": rules, "rules_by_id": {r["id"]: r for r in rules},
        "items": items, "item_ids": [it["id"] for it in items], "sources": {"a": projects[0]["source"], "b": projects[1]["source"]},
        "window_basis": {"a": wa[2] if wa else None, "b": wb[2] if wb else None},
    }
    case["goals"], case["conflict"] = goals(case)
    return case


def _short(case: dict, side: str) -> str:
    p = case["pa"] if side == "a" else case["pb"]
    return ag.SHORT.get(p["utility"], p["utility"])


def _agents(case: dict, lang: str) -> list[dict]:
    out = []
    for side, p in (("a", case["pa"]), ("b", case["pb"])):
        who, short = p.get("utility_name") or p["utility"], _short(case, side)
        w = case["bounds"][side]
        src = case["sources"][side]
        out.append({
            "id": side,
            "side": side,
            "utility": p["utility"],
            "utility_name": who,
            "short": short,
            "project_id": p["id"],
            "project": ag._display_name(p.get("name") or p["id"]),
            "kv": p.get("kv") or [],
            "kind_label": ag.KIND_WORDS.get(p.get("kind"), "grid work"),
            "miles": p.get("miles"),
            "window": {"start": _iso(w[0]), "end": _iso(w[1]), "basis": case["window_basis"][side], "ended": w[1] < case["now"]} if w else None,
            "represents": _L(lang, f"An AI agent reading {who}'s public filing", f"Un agente de IA que lee el documento público de {who}"),
            "not_the_utility": _L(lang, f"Not {short}: it reads the filing and can't speak for the utility.",
                                  f"No es {short}: lee el documento y no puede hablar por la empresa."),
            "filing": src.get("label"),
            "source": src,
            "goal": case["goals"][side]["text_es" if lang == "es" else "text_en"],
            "goal_cap_pct": case["goals"][side]["cap_pct"],
            "goal_rule": case["goals"][side]["rule"],
        })
    return out


def _why_none(case: dict, lang: str) -> str | None:
    """Why no joint window is possible (None when one is)."""
    if case["feasible"]:
        return None
    ended = [s for s in ("a", "b") if case["bounds"][s] and case["bounds"][s][1] < case["now"]]
    joint = case["extra"]["joint"]
    if joint and case["extra"]["status"] == "past":
        return _L(lang, f"as filed, the months both build windows shared ({_span((_ym(joint[0]), _ym(joint[1])))}) have passed",
                  f"según lo publicado, los meses que compartían las dos ventanas de obra ({_span((_ym(joint[0]), _ym(joint[1])), 'es')}) ya pasaron")
    if ended:
        s = ended[0]
        return _L(lang, f"as filed, {_short(case, s)}'s build window ended {_mt(case['bounds'][s][1])}",
                  f"según lo publicado, la ventana de obra de {_short(case, s)} terminó en {_mt(case['bounds'][s][1], 'es')}")
    if not (case["bounds"]["a"] and case["bounds"]["b"]):
        return _L(lang, "at least one filing gives no build window", "al menos un documento no da ventana de obra")
    return _L(lang, "the filed build windows share no months", "las ventanas de obra publicadas no comparten meses")


# ----------------------------------------------------------------------------- the verifier (deterministic)


def _window_numbers(turns: list, *extra) -> set[str]:
    out = set()
    for jw in [t["proposal"].get("joint_window") for t in turns] + list(extra):
        if isinstance(jw, dict):
            for v in (jw.get("start"), jw.get("end")):
                ym = _parse_ym(v)
                if isinstance(ym, tuple):
                    out |= {str(ym[0]), str(ym[1])}
    return out


def verify(raw, case: dict, lang: str = "en", turns: list | None = None) -> tuple[dict, dict]:
    """Check one proposal against both filings. Returns (verdict {ok, findings, notes}, the proposal normalized:
    {joint_window {start, end} | None, scope, split {rule, shares}, concerns, note, accept}). Findings block the
    turn; notes don't."""
    findings: list[str] = []
    notes: list[str] = []
    turns = turns or []
    A, B = _short(case, "a"), _short(case, "b")
    if not isinstance(raw, dict) or raw.get("_none"):
        return {"ok": False, "findings": [_L(lang, "the answer is not a proposal", "la respuesta no es una propuesta")], "notes": []}, {
            "joint_window": None, "scope": [], "split": {"rule": "", "shares": [None, None]}, "concerns": [], "note": "", "accept": False}

    # the window
    jw = raw.get("joint_window")
    s = e = None
    window = None
    shown = None
    if isinstance(jw, dict):
        s, e = _parse_ym(jw.get("start")), _parse_ym(jw.get("end"))
        shown = {"start": str(jw.get("start") or "").strip(), "end": str(jw.get("end") or "").strip()}
    elif jw not in (None, "", [], {}):
        findings.append(_L(lang, "the window must be {start, end} in months (YYYY-MM), or both empty for none",
                           "la ventana debe ser {start, end} en meses (AAAA-MM), o las dos vacías para ninguna"))
    if s == "bad" or e == "bad":
        findings.append(_L(lang, f"the window {shown['start'] or '(empty)'} to {shown['end'] or '(empty)'} is not a pair of months (YYYY-MM)",
                           f"la ventana {shown['start'] or '(vacía)'} a {shown['end'] or '(vacía)'} no son dos meses (AAAA-MM)"))
    elif (s is None) != (e is None):
        findings.append(_L(lang, "the window needs both a start and an end, or neither", "la ventana necesita inicio y fin, o ninguno"))
    elif s is not None:
        window = (s, e)
    if window:
        wtxt = _span(window, lang)
        if window[0] > window[1]:
            findings.append(_L(lang, f"the window {wtxt} starts after it ends", f"la ventana {wtxt} empieza después de terminar"))
        for side, who in (("a", A), ("b", B)):
            w = case["bounds"][side]
            if w is None:
                findings.append(_L(lang, f"{who}'s filing gives no build window to check a joint window against",
                                   f"el documento de {who} no da ventana de obra contra la que comprobar"))
            elif window[0] < w[0] or window[1] > w[1]:
                ended = w[1] < case["now"]
                findings.append(_L(
                    lang,
                    f"{wtxt} lies outside {who}'s filed build window ({_span(w)}{', which ended before today' if ended else ''})",
                    f"{wtxt} queda fuera de la ventana de obra publicada de {who} ({_span(w, 'es')}{', que terminó antes de hoy' if ended else ''})",
                ))
        if window[0] < case["now"]:
            findings.append(_L(lang, f"{wtxt} starts before today ({ag._day(case['as_of'])}): a joint window can't be planned in the past",
                               f"{wtxt} empieza antes de hoy: no se puede planear una ventana conjunta en el pasado"))
    elif s is None and e is None and case["feasible"]:
        findings.append(_L(lang, f"both filings leave {_span(case['feasible'])} open to both projects: propose a window inside it",
                           f"los dos documentos dejan {_span(case['feasible'], 'es')} abierto a ambos proyectos: propón una ventana dentro"))

    # the scope
    ids = case["item_ids"]
    scope: list[str] = []
    sr = raw.get("scope")
    if not isinstance(sr, list) or not sr:
        findings.append(_L(lang, f"the scope must list at least one of the estimate's items ({', '.join(ids)})",
                           f"el alcance debe incluir al menos una partida de la estimación ({', '.join(ids)})"))
    else:
        for x in sr:
            sid = str(x).strip()
            if sid not in ids:
                findings.append(_L(lang, f"'{sid[:60]}' is not one of the estimate's items ({', '.join(ids)})",
                                   f"'{sid[:60]}' no es una partida de la estimación ({', '.join(ids)})"))
            elif sid not in scope:
                scope.append(sid)
    if not window:
        for it in case["items"]:
            if it["id"] in scope and it.get("needs") == "same build window":
                notes.append(_L(lang, f"{it['label']} needs both crews in the field at once: with no joint window it applies only if both projects still have work ahead.",
                                f"{it['label']} necesita las dos cuadrillas a la vez: sin ventana conjunta solo aplica si a ambos proyectos les queda obra."))

    # the split
    sp = raw.get("split") if isinstance(raw.get("split"), dict) else {}
    rule = str(sp.get("rule") or "").strip()
    a_pct = b_pct = None
    try:
        if "a_pct" in sp or "b_pct" in sp:
            a_pct, b_pct = float(sp.get("a_pct")), float(sp.get("b_pct"))
        elif isinstance(sp.get("shares"), list) and len(sp["shares"]) == 2:
            a_pct, b_pct = float(sp["shares"][0]), float(sp["shares"][1])
    except (TypeError, ValueError):
        a_pct = b_pct = None
    r = case["rules_by_id"].get(rule)
    if rule not in RULE_IDS:
        findings.append(_L(lang, f"'{rule[:40] or '(none)'}' is not an allowed split rule (by_length, by_kv, equal)",
                           f"'{rule[:40] or '(ninguna)'}' no es una regla de reparto permitida (by_length, by_kv, equal)"))
    elif r is None:
        findings.append(_L(lang, f"the rule {rule} can't be used here: ", f"la regla {rule} no se puede usar aquí: ") + _why_rule_missing(rule, case["pa"], case["pb"], lang))
    if a_pct is None or b_pct is None:
        findings.append(_L(lang, "the split needs both shares as numbers", "el reparto necesita las dos partes como números"))
    elif abs(a_pct + b_pct - 100) > 0.5:
        findings.append(_L(lang, f"the shares add up to {a_pct + b_pct:g} %, not 100 %", f"las partes suman {a_pct + b_pct:g} %, no 100 %"))
    elif r is not None and (abs(a_pct - r["shares"][0]) > 1 or abs(b_pct - r["shares"][1]) > 1):
        label = r["label"] if lang != "es" else r["label_es"]
        findings.append(_L(lang, f"{label} gives {A} {r['shares'][0]} % and {B} {r['shares'][1]} %, not {a_pct:g} % and {b_pct:g} %",
                           f"{label} da a {A} {r['shares'][0]} % y a {B} {r['shares'][1]} %, no {a_pct:g} % y {b_pct:g} %"))

    # the free text: agreement.py's checker, by kind, and nothing that speaks for a utility
    concerns_raw = raw.get("concerns") if isinstance(raw.get("concerns"), list) else []
    concerns = [str(c).strip() for c in concerns_raw if isinstance(c, (str, int, float)) and str(c).strip()]
    if len(concerns) > MAX_CONCERNS:
        findings.append(_L(lang, f"at most {MAX_CONCERNS} concerns", f"como máximo {MAX_CONCERNS} objeciones"))
        concerns = concerns[:MAX_CONCERNS]
    note = raw.get("note")
    note = note.strip() if isinstance(note, str) else ""
    allowed = copy.deepcopy(case["allowed"])
    allowed.plain |= _window_numbers(turns, jw)
    texts = [(_L(lang, f"concern {i + 1}", f"objeción {i + 1}"), c, MAX_CONCERN_CHARS) for i, c in enumerate(concerns)]
    if note:
        texts.append((_L(lang, "note", "nota"), note, MAX_NOTE_CHARS))
    for where, text, cap in texts:
        ok, reason, _ = ag.check_text(text, case["facts"], allowed, cap)
        if not ok:
            findings.append(f"{where}: {reason}")
            continue
        m = _SPEAKS_FOR.search(text)
        if m:
            findings.append(_L(lang, f"{where}: speaks for a utility ({m.group(0)!r}); an agent reads a filing, it can't speak for the utility",
                               f"{where}: habla por una empresa ({m.group(0)!r}); un agente lee un documento, no habla por la empresa"))
            continue
        m = _FIRST_PERSON.search(text)
        if m:
            findings.append(_L(lang, f"{where}: writes as {m.group(0)!r}, which reads as the utility speaking; write about the filing in the third person ('the filing shows', 'this agent proposes')",
                               f"{where}: escribe como {m.group(0)!r}, que suena a la empresa hablando; escribir en tercera persona ('el documento muestra', 'este agente propone')"))

    proposal = {
        "joint_window": {"start": _iso(window[0]), "end": _iso(window[1])} if window else None,
        "window_shown": shown if (shown and (shown["start"] or shown["end"]) and not window) else None,
        "scope": scope,
        "scope_unknown": [str(x).strip()[:60] for x in (sr if isinstance(sr, list) else []) if str(x).strip() not in ids],
        "split": {"rule": rule, "shares": [a_pct, b_pct]},
        "concerns": concerns,
        "note": note,
        "accept": raw.get("accept") is True,
    }
    return {"ok": not findings, "findings": findings, "notes": notes}, proposal


def _terms(p: dict) -> dict:
    return {"joint_window": p["joint_window"], "scope": list(p["scope"]), "split": {"rule": p["split"]["rule"], "shares": list(p["split"]["shares"])}}


def _same(t1: dict, t2: dict) -> bool:
    return t1["joint_window"] == t2["joint_window"] and set(t1["scope"]) == set(t2["scope"]) and t1["split"]["rule"] == t2["split"]["rule"]


def describe(p: dict, case: dict, lang: str = "en") -> list[dict]:
    """A proposal's terms in plain words (the cards and the history the agents read)."""
    A, B = _short(case, "a"), _short(case, "b")
    out = []
    if p.get("joint_window"):
        jw = p["joint_window"]
        out.append({"k": "window", "label": _L(lang, "Window", "Ventana"), "text": _span((_parse_ym(jw["start"]), _parse_ym(jw["end"])), lang)})
    elif p.get("window_shown"):
        ws = p["window_shown"]
        out.append({"k": "window", "label": _L(lang, "Window", "Ventana"), "text": f"{ws['start'] or '?'} {_L(lang, 'to', 'a')} {ws['end'] or '?'}"})
    else:
        out.append({"k": "window", "label": _L(lang, "Window", "Ventana"), "text": _L(lang, "None: check each project's current status", "Ninguna: revisar el estado actual de cada proyecto")})
    labels = {it["id"]: it for it in case["items"]}
    def rng(it):
        return _usd_range(it["low"], it["high"]) if it["unit"] == "USD" else f"{it['low']:g}-{it['high']:g} {it['unit']}"

    share = [f"{labels[i]['label']} ({rng(labels[i])})" for i in p.get("scope") or []]
    share += [_L(lang, f"'{x}' (not in the estimate)", f"'{x}' (no está en la estimación)") for x in p.get("scope_unknown") or []]
    out.append({"k": "scope", "label": _L(lang, "Share", "Compartir"), "text": "; ".join(share) or _L(lang, "nothing", "nada")})
    sp = p.get("split") or {}
    r = case["rules_by_id"].get(sp.get("rule"))
    name = (r["label"] if lang != "es" else r["label_es"]) if r else f"'{sp.get('rule') or '?'}'"
    sh = sp.get("shares") or [None, None]
    fmt = lambda v: "?" if v is None else f"{v:g}"  # noqa: E731
    out.append({"k": "split", "label": _L(lang, "Split", "Reparto"), "text": f"{name}: {A} {fmt(sh[0])} %, {B} {fmt(sh[1])} %"})
    return out


# ----------------------------------------------------------------------------- the agents (Gemini)

AGENT_SYSTEM = (
    "You are an AI agent reading ONE electric utility's public transmission construction filing for one planned project. "
    "You are not the utility and never speak for it: never say what the utility wants, prefers, plans, intends or would accept; "
    "say 'the filing shows' or 'as filed'. With another AI agent that reads the other utility's filing, you negotiate the terms of a "
    "DRAFT coordination proposal for discussion (never an agreement between the utilities): one joint build window, which of the "
    "rough estimate's shared items to include, and one cost-split rule. "
    "A pipeline checks every proposal against BOTH filings: the window must lie inside both filed build windows and not start before "
    "today; when your filed window ended before today, or the filed windows share no months, the right proposal is no joint window "
    "(start and end empty strings) and the next step is checking each project's current status. When the pipeline rejects a proposal, "
    "fix exactly what it names. Use only the facts given: every number you write (money, miles, km, kV, dates, percentages) must be "
    "copied from them. No people, contacts, prices, dates or outcomes that are not in the facts; never the words agreed, committed, "
    "binding, guarantee, refuse or failed. A date before today is said as past ('as filed, its window opened Jun 2025'; "
    "'as filed, the window ended Aug 2026'). "
    "Write in the third person about the filing ('the filing shows', 'this agent proposes'); never 'we' or 'our'. "
    "In concerns and the note, name a split rule in words ('by filed length', 'by kV class', '50/50'), not by its id. "
    "You hold your filing's side of the shared estimate and you are given a GOAL (the most your side should pay by a fair-share rule the pipeline computed from your filing's figures, "
    "which the filing itself doesn't state, and the window your filing allows): argue for the split rule your filing's own figures (voltage, length, kind of work) support for "
    "your side, with that reason, and answer the other agent's reasons; move toward a middle ground only when its reason holds or in "
    "the last round, since a verified middle ground beats leaving without terms. Don't accept terms above your goal before the last "
    "round. If the terms on the table already meet your goal, accept them; never invent a disagreement. "
    "Accept (accept: true) only the other agent's proposal on the table, copying its terms exactly. Short, plain sentences; no markdown."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "joint_window": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]},
        "scope": {"type": "array", "items": {"type": "string"}},
        "split": {"type": "object", "properties": {"rule": {"type": "string"}, "a_pct": {"type": "number"}, "b_pct": {"type": "number"}},
                  "required": ["rule", "a_pct", "b_pct"]},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
        "accept": {"type": "boolean"},
    },
    "required": ["joint_window", "scope", "split", "concerns", "note", "accept"],
}

SHARED_KEYS = ("as_of", "pair.distance", "pair.tier", "forum.sertp")


def _history_lines(case: dict, turns: list) -> list[str]:
    out = []
    for t in turns:
        who = _short(case, t["agent"])
        terms = "; ".join(f"{d['label']}: {d['text']}" for d in t["plain"])
        head = f"Round {t['round']}, agent {t['agent'].upper()} (reads {who}'s filing){' revision' if t['revision'] else ''}"
        act = "accepts the proposal on the table" if t["kind"] == "accept" else "proposes"
        line = f"- {head} {act}: {terms}."
        if t["proposal"]["concerns"]:
            line += " Concerns: " + " | ".join(t["proposal"]["concerns"])
        if t["proposal"]["note"]:
            line += f" Note: {t['proposal']['note']}"
        v = t["verdict"]
        line += " PIPELINE: " + ("VERIFIED against both filings." if v["ok"] else "REJECTED: " + "; ".join(v["findings"]))
        out.append(line)
    return out


def _middle_lines(case: dict, side: str, rnd: int) -> list[str]:
    """From round 2, when the goals conflict: the allowed rules that sit between the two goals (a real option both filings
    support, never a scripted move), and in the last round what repeating an opening leads to."""
    if rnd < 2 or not case["conflict"]["split"]:
        return []
    ga, gb = case["goals"]["a"]["rule"], case["goals"]["b"]["rule"]
    A, B = _short(case, "a"), _short(case, "b")
    mids = [r for r in case["rules"] if r["id"] not in (ga, gb)]
    out = []
    if mids:
        out.append("MIDDLE GROUND the filings allow: " + "; ".join(f"{r['id']} ({A} {r['shares'][0]} %, {B} {r['shares'][1]} %)" for r in mids) + ".")
    if rnd == MAX_ROUNDS:
        out.append("This is the last round: accept the other agent's verified proposal on the table (accept: true, the same terms) or "
                   "propose the middle ground; repeating your own opening again ends the negotiation with no terms.")
    return out


def _prompt(case: dict, side: str, rnd: int, turns: list, table: dict | None, feedback: list | None, lang: str) -> str:
    other = "b" if side == "a" else "a"
    me_p, ot_p = (case["pa"], case["pb"]) if side == "a" else (case["pb"], case["pa"])
    A, B = _short(case, "a"), _short(case, "b")
    mine = [f for f in case["facts"] if f["key"].startswith(side + ".")]
    shared = [f for f in case["facts"] if f["key"] in SHARED_KEYS or f["key"].startswith("save.")]
    items = "\n".join(
        f"- {it['id']}: {it['label']}: " + (_usd_range(it["low"], it["high"]) if it["unit"] == "USD" else f"{it['low']:g}-{it['high']:g} {it['unit']}")
        + (f" (needs {it['needs']})" if it.get("needs") else "")
        for it in case["items"]
    )
    rules = "\n".join(f"- {r['id']}: {A} {r['shares'][0]} %, {B} {r['shares'][1]} % ({r['basis']})" for r in case["rules"])
    missing = [x for x in RULE_IDS if x not in case["rules_by_id"]]
    if missing:
        rules += "\n" + "\n".join(f"- {x}: NOT allowed here ({_why_rule_missing(x, case['pa'], case['pb'], 'en')})" for x in missing)
    lines = [
        f"You are agent {side.upper()}: an AI agent reading {me_p.get('utility_name')}'s public filing ({case['sources'][side].get('label')}) "
        f"for project {me_p['id']}. The other agent ({other.upper()}) reads {ot_p.get('utility_name')}'s filing for project {ot_p['id']}; "
        "you don't see that filing, only its agent's proposals and the pipeline's findings.",
        f"Today (as_of): {case['as_of'].isoformat()}. Round {rnd} of {MAX_ROUNDS}." + (" This is the last round: accept a verified proposal that fits your filing rather than leave without terms." if rnd == MAX_ROUNDS else ""),
        "",
        "YOUR FILING (key: text):",
        *[f"- {f['key']}: {f['text']}" for f in mine],
        "",
        f"YOUR GOAL (a fair-share figure the pipeline computed from your filing's figures): {case['goals'][side]['text_en']} Before round {MAX_ROUNDS}, don't accept a split that "
        f"gives your side more than {case['goals'][side]['cap_pct']:g} %; counter with your filing's reason instead. In round "
        f"{MAX_ROUNDS}, a verified middle ground beats leaving without terms.",
        *_middle_lines(case, side, rnd),
        "",
        "SHARED FACTS:",
        *[f"- {f['key']}: {f['text']}" for f in shared],
        "",
        "SCOPE ITEMS (the estimate's shared items; use these ids):",
        items,
        "",
        f"SPLIT RULES (use one id and exactly its shares; a_pct is {A}'s share, b_pct is {B}'s):",
        rules,
        "",
        "NEGOTIATION SO FAR:",
        *(_history_lines(case, turns) or ["- Nothing yet: you open."]),
    ]
    if table:
        t = table["terms"]
        lines += ["", f"ON THE TABLE (verified, from agent {table['agent'].upper()}): "
                  + "; ".join(f"{d['label']}: {d['text']}" for d in describe({**t, "concerns": [], "note": ""}, case))
                  + (". You may accept it (accept: true, the same terms) or counter." if table["agent"] != side else ". It is your own proposal: counter only if the pipeline or the other agent gave a reason.")]
    if feedback:
        lines += ["", "THE PIPELINE REJECTED YOUR LAST PROPOSAL:", *[f"- {x}" for x in feedback], "Send a corrected proposal that fixes each of these."]
    lines += [
        "",
        'Answer as JSON: {"joint_window": {"start": "YYYY-MM", "end": "YYYY-MM"} (both "" for no joint window), "scope": [item ids], '
        '"split": {"rule": rule id, "a_pct": number, "b_pct": number}, "concerns": [0-3 short strings about what your filing shows, each under 200 characters], '
        '"note": one sentence on why these terms, "accept": true only to accept the proposal on the table unchanged}.',
    ]
    if lang == "es":
        lines.append("Write concerns and note in Spanish (numbers in the same digits as the facts).")
    return "\n".join(lines)


async def _gemini_turn(case, side, rnd, turns, table, feedback, lang, model: str | None = None) -> tuple[dict, bool, int, bool]:
    prompt = _prompt(case, side, rnd, turns, table, feedback, lang)
    before = llm._stats["by_surface"].get("negotiation", {}).get("cached", 0)
    t0 = time.perf_counter()
    try:
        raw, offline = await asyncio.wait_for(
            llm.complete_json(prompt, system=AGENT_SYSTEM, fallback=_NONE, timeout=CALL_TIMEOUT_S, schema=SCHEMA, cache=True,
                              surface="negotiation", model=model or llm.AGENT_MODEL),
            CALL_DEADLINE_S,
        )
    except asyncio.TimeoutError:
        raw, offline = _NONE, True
    ms = round((time.perf_counter() - t0) * 1000)
    cached = llm._stats["by_surface"].get("negotiation", {}).get("cached", 0) > before
    return raw, offline, ms, cached


# ----------------------------------------------------------------------------- one negotiation


class _Game:
    """The table, the turns and the verdicts: shared by the Gemini agents and the plain version."""

    def __init__(self, case: dict, lang: str):
        self.case, self.lang = case, lang
        self.turns: list[dict] = []
        self.table: dict | None = None  # the last verified proposal: {agent, terms, n}
        self.agreed: dict | None = None

    def take(self, side: str, rnd: int, raw, ms: int = 0, revision: bool = False, cached: bool = False, hold_to_goal: bool = False) -> dict:
        verdict, prop = verify(raw, self.case, self.lang, self.turns)
        wants = prop["accept"]
        matches = bool(self.table and self.table["agent"] != side and _same(_terms(prop), self.table["terms"]))
        # J2: an AI agent is held to its own filing's goal before the last round (the plain version is a labeled script)
        goal = (self.case.get("goals") or {}).get(side)
        share = (prop["split"].get("shares") or [None, None])[0 if side == "a" else 1]
        if (hold_to_goal and goal and wants and matches and verdict["ok"] and rnd < MAX_ROUNDS
                and isinstance(share, (int, float)) and share > goal["cap_pct"] + 0.5):
            verdict["ok"] = False
            verdict["findings"].append(_L(
                self.lang,
                f"accepting {share:g} % goes past this agent's goal (at most {goal['cap_pct']:g} %, {goal['rule_label'][:1].lower() + goal['rule_label'][1:]}) before "
                f"round {MAX_ROUNDS}: counter with the filing's reason instead",
                f"aceptar el {share:g} % supera el objetivo de este agente (como máximo {goal['cap_pct']:g} %) antes de la ronda "
                f"{MAX_ROUNDS}: contraproponer con la razón del documento",
            ))
        accept = bool(wants and matches and verdict["ok"])
        if wants and not matches:
            if not self.table or self.table["agent"] == side:
                verdict["notes"].append(_L(self.lang, "Nothing from the other agent is on the table yet, so this is read as a proposal.",
                                           "Aún no hay nada del otro agente sobre la mesa: se lee como una propuesta."))
            else:
                verdict["notes"].append(_L(self.lang, "It changes the terms on the table, so it is a counter-proposal, not an acceptance.",
                                           "Cambia los términos sobre la mesa: es una contrapropuesta, no una aceptación."))
            prop["accept"] = False
        # an accept the verifier rejected (its text failed) is still shown as an accept attempt
        kind = "accept" if (wants and matches) else "revise" if revision else "propose" if not self.turns else "counter"
        turn = {
            "n": len(self.turns) + 1,
            "round": rnd,
            "agent": side,
            "kind": kind,
            "revision": revision,
            "proposal": prop,
            "plain": describe(prop, self.case, self.lang),
            "verdict": verdict,
            "ms": ms,
            "cached": cached,
        }
        self.turns.append(turn)
        if verdict["ok"]:
            if accept:
                self.agreed = {"terms": _terms(prop), "round": rnd, "accepted_by": side, "proposed_by": self.table["agent"], "n": turn["n"]}
            else:
                self.table = {"agent": side, "terms": _terms(prop), "n": turn["n"]}
        return turn


async def _run_gemini(case: dict, lang: str, live: dict | None = None) -> tuple[_Game, int, str | None, str | None]:
    """(game, calls, failure reason or None, stop reason or None). live: a dict the progress route reads while this
    runs (the game so far, the calls made, which agent is working now)."""
    g = _Game(case, lang)
    calls = 0
    t0 = time.perf_counter()
    stop = None
    if live is not None:
        live.update(game=g, calls=0, t0=t0, working=None)
    for rnd in range(1, MAX_ROUNDS + 1):
        for side in ("a", "b"):
            if g.agreed:
                break
            feedback = None
            for attempt in range(2):
                if calls >= MAX_CALLS:
                    stop = "budget"
                    break
                if time.perf_counter() - t0 > DEADLINE_S:
                    return g, calls, "Gemini too slow", None
                if live is not None:
                    live.update(working={"agent": side, "round": rnd, "revision": attempt == 1}, calls=calls)
                model = llm.AGENT_MODEL
                raw, offline, ms, cached = await _gemini_turn(case, side, rnd, g.turns, g.table, feedback, lang, model)
                calls += 1
                # one slow or failed call: the same turn once more on the fast model (never for a missing key or quota)
                if offline and llm.configured() and calls < MAX_CALLS and model != llm.MODEL and not str(llm._stats.get("last_error", "")).startswith("AI quota"):
                    model = llm.MODEL
                    if live is not None:
                        live.update(calls=calls)
                    raw, offline, ms2, cached = await _gemini_turn(case, side, rnd, g.turns, g.table, feedback, lang, model)
                    calls += 1
                    ms += ms2
                if live is not None:
                    live.update(calls=calls, working=None)
                if offline:
                    why = "Gemini not configured" if not llm.configured() else ("Gemini too slow" if ms >= CALL_DEADLINE_S * 1000 - 50 else "Gemini unavailable")
                    return g, calls, why, None
                turn = g.take(side, rnd, raw, ms, revision=attempt == 1, cached=cached, hold_to_goal=True)
                found = turn["verdict"]["findings"]
                if found and lang == "es":  # the ledger reads in English: the same check, worded in English (verify is pure and fast)
                    found = verify(raw, case, "en", g.turns[:-1])[0]["findings"] or found
                llm.note_check("negotiation", turn["verdict"]["ok"], "a negotiation turn: " + ((found or ["rejected by the verifier"])[0]))
                turn["model"] = model
                turn["retried"] = model != llm.AGENT_MODEL
                if turn["verdict"]["ok"]:
                    break
                feedback = turn["verdict"]["findings"]
            if stop:
                break
        if g.agreed or stop:
            break
    return g, calls, None, stop


def _plain_game(case: dict, lang: str) -> _Game:
    """The rule-based negotiation (labeled 'Plain version'), driven by the two goals: A opens with the split its filing
    supports; B accepts when that already meets its own goal, else counters with the split ITS filing supports; then A
    proposes the middle ground (the draft's own filed-length rule, or 50/50) and B accepts it. No counter-offer is made
    when there is nothing to disagree about."""
    g = _Game(case, lang)
    by_id = case["rules_by_id"]
    goals_ = case["goals"]
    ra, rb = goals_["a"]["rule"], goals_["b"]["rule"]
    win = case["feasible"]
    jw = {"start": _iso(win[0]), "end": _iso(win[1])} if win else {"start": "", "end": ""}
    scope = list(case["item_ids"])
    A, B = _short(case, "a"), _short(case, "b")
    es = lang == "es"

    def label(rid):
        return by_id[rid]["label_es" if es else "label"]

    def concern(side):
        w = case["bounds"][side]
        who = A if side == "a" else B
        if not w:
            return []
        if w[1] < case["now"]:
            return [_L(lang, f"As filed, {who}'s build window ended {_mt(w[1])}; the project's current status is not in the filing.",
                       f"Según lo publicado, la ventana de obra de {who} terminó en {_mt(w[1], 'es')}; el estado actual del proyecto no está en el documento.")]
        return [_L(lang, f"As filed, {who}'s build window runs {_span(w)}.", f"Según lo publicado, la ventana de obra de {who} va de {_span(w, 'es')}.")]

    def raw(rid, concerns, note, accept=False):
        r = by_id[rid]
        return {"joint_window": jw, "scope": scope, "split": {"rule": rid, "a_pct": r["shares"][0], "b_pct": r["shares"][1]},
                "concerns": concerns, "note": note, "accept": accept}

    wtxt = _span(win, lang) if win else _L(lang, "no joint window", "sin ventana conjunta")
    accept_note = _L(lang, "Accepts the proposal on the table.", "Acepta la propuesta sobre la mesa.")
    open_note = _L(lang, f"Opening: {wtxt}, every shared item in the estimate, split {ag._lc(label(ra))}, the fair-share split that asks this side for the least.",
                   f"Apertura: {wtxt}, todas las partidas compartidas de la estimación, reparto {ag._lc(label(ra))}, el reparto justo que menos pide a esta parte.")
    g.take("a", 1, raw(ra, concern("a"), open_note))
    if by_id[ra]["shares"][1] <= goals_["b"]["cap_pct"] + 0.5:  # A's opening already meets B's goal: nothing to counter
        g.take("b", 1, raw(ra, concern("b"), accept_note, True))
        return g
    g.take("b", 1, raw(rb, concern("b"), _L(lang, f"Counter: split {ag._lc(label(rb))}, the fair-share split that asks this side for the least.",
                                              f"Contrapropuesta: reparto {ag._lc(label(rb))}, el reparto justo que menos pide a esta parte.")))
    settle = "by_length" if "by_length" in by_id else "equal"
    mid = next((x for x in (settle, "equal") if x in by_id and x not in (ra, rb)), None)
    if mid is None:  # no rule between the two: A takes B's counter
        g.take("a", 2, raw(rb, [], accept_note, True))
        return g
    middle = (
        _L(lang, "Middle ground: split by filed length, the draft's own rule, since both filings give a length.",
           "Punto medio: reparto por longitud publicada, la regla del propio borrador, porque los dos documentos dan una longitud.")
        if mid == "by_length"
        else _L(lang, "Middle ground: split equally until both scopes are sized.", "Punto medio: reparto a partes iguales hasta dimensionar los dos alcances.")
    )
    g.take("a", 2, raw(mid, [], middle))
    g.take("b", 2, raw(mid, [], accept_note, True))
    return g


def _terms_view(terms: dict, case: dict, lang: str) -> dict:
    items = {it["id"]: it for it in case["items"]}
    scope = [items[i] for i in terms["scope"] if i in items]
    usd = [it for it in scope if it["unit"] == "USD"]
    r = case["rules_by_id"][terms["split"]["rule"]]
    return {
        "joint_window": terms["joint_window"],
        "scope": scope,
        "split": {"rule": r["id"], "label": r["label_es" if lang == "es" else "label"], "shares": list(r["shares"]),
                  "basis": r["basis_es" if lang == "es" else "basis"], "rule_text": r["rule"], "rationale": r["rationale"]},
        "savings": {"low": sum(it["low"] for it in usd), "high": sum(it["high"] for it in usd), "unit": "USD"},
    }


def _outcome(g: _Game, case: dict, lang: str, stop: str | None) -> dict:
    why_none = _why_none(case, lang)
    if g.agreed:
        t = g.agreed["terms"]
        final, _ = verify({"joint_window": t["joint_window"] or {"start": "", "end": ""}, "scope": t["scope"],
                           "split": {"rule": t["split"]["rule"], "a_pct": t["split"]["shares"][0], "b_pct": t["split"]["shares"][1]},
                           "concerns": [], "note": "", "accept": True}, case, lang)
        view = _terms_view(t, case, lang)
        return {
            "agreed": True,
            "verified": final["ok"],
            "final_check": final,
            "round": g.agreed["round"],
            "accepted_by": g.agreed["accepted_by"],
            "proposed_by": g.agreed["proposed_by"],
            "terms": view,
            "plain": describe({**t, "concerns": [], "note": ""}, case, lang),
            "reason": _L(lang, f"Agent {g.agreed['accepted_by'].upper()} accepted agent {g.agreed['proposed_by'].upper()}'s verified proposal in round {g.agreed['round']}; the pipeline re-checked the final terms against both filings.",
                         f"El agente {g.agreed['accepted_by'].upper()} aceptó la propuesta verificada del agente {g.agreed['proposed_by'].upper()} en la ronda {g.agreed['round']}; el sistema volvió a comprobar los términos finales con los dos documentos."),
            "next": (_L(lang, f"No joint window: {why_none}. Check each project's current status first.", f"Sin ventana conjunta: {why_none}. Primero, revisar el estado actual de cada proyecto.")
                     if not t["joint_window"] and why_none else None),
        }
    last = g.table
    return {
        "agreed": False,
        "verified": False,
        "terms": None,
        "last_verified": _terms_view(last["terms"], case, lang) if last else None,
        "reason": (_L(lang, f"The Gemini call budget ({MAX_CALLS}) ran out before both agents accepted the same verified terms.",
                      f"Se agotó el presupuesto de llamadas a Gemini ({MAX_CALLS}) antes de que ambos agentes aceptaran los mismos términos verificados.")
                   if stop == "budget" else
                   _L(lang, f"No verified agreement in {MAX_ROUNDS} rounds: the draft keeps its own terms.",
                      f"Sin acuerdo verificado en {MAX_ROUNDS} rondas: el borrador mantiene sus propios términos.")),
        "next": None,
    }


def _nothing_outcome(case: dict, lang: str) -> dict:
    """A pair whose estimate has no item: the only things this distance could share (crews mobilized once, one laydown
    yard) need the two build windows to share months, and these don't, so there are no terms to negotiate."""
    left = [it["label"] for it in case["est"].get("left_out") or []]
    what = ", ".join(ag._lc(x) for x in left) or _L(lang, "what could be shared", "lo que podría compartirse")
    return {
        "agreed": False,
        "verified": False,
        "nothing": True,
        "terms": None,
        "last_verified": None,
        "reason": _L(lang, f"Nothing to negotiate: at this distance, {what} would need the two build windows to share months, and they don't.",
                     f"Nada que negociar: a esta distancia, {what} necesitaría que las dos ventanas de obra compartieran meses, y no los comparten."),
        "next": _L(lang, "Compare detailed schedules first: a shared window would make one mobilization possible.",
                   "Primero, comparar los calendarios detallados: una ventana compartida permitiría una sola movilización."),
    }


async def _negotiate(case: dict, lang: str, ai: bool, key: tuple | None = None) -> tuple[dict, bool]:
    """(response, cacheable)."""
    t0 = time.perf_counter()
    calls, failure, stop, discarded = 0, None, None, 0
    model, models = None, []
    if not case["items"]:  # nothing in the estimate applies to this pair: no agent is asked (see _nothing_outcome)
        g, by = _Game(case, lang), "none"
    elif ai:
        live: dict = {}
        if key is not None:
            _live[key] = live
        try:
            g, calls, failure, stop = await _run_gemini(case, lang, live)
        finally:
            if key is not None:
                _live.pop(key, None)
        model = llm.AGENT_MODEL
        models = sorted({t["model"] for t in g.turns if t.get("model")}, key=lambda m: (m != llm.AGENT_MODEL, m))  # the agent model first, then the fast one after a retry
        if failure:
            discarded = len(g.turns)
            g = _plain_game(case, lang)
            stop = None
        by = "fallback" if failure else "gemini"
    else:
        g, by, failure = _plain_game(case, lang), "fallback", "Plain version requested"
    bad_plain = [t for t in g.turns if not t["verdict"]["ok"]] if by == "fallback" else []
    if bad_plain:  # the plain version is built to pass: a failure here is a bug
        log.warning("negotiate: plain turn(s) failed the verifier for %s: %s", case["overlap_id"], [t["verdict"]["findings"] for t in bad_plain][:2])
    feas = case["feasible"]
    out = {
        "overlap_id": case["overlap_id"],
        "as_of": case["as_of"].isoformat(),
        "lang": lang,
        "window_months": None,
        "agents": _agents(case, lang),
        "rules": [{"id": r["id"], "label": r["label_es" if lang == "es" else "label"], "shares": r["shares"], "basis": r["basis_es" if lang == "es" else "basis"]} for r in case["rules"]],
        "rules_unavailable": [{"id": x, "why": _why_rule_missing(x, case["pa"], case["pb"], lang)} for x in RULE_IDS if x not in case["rules_by_id"]],
        "scope_items": case["items"],
        "bounds": {
            "a": {"start": _iso(case["bounds"]["a"][0]), "end": _iso(case["bounds"]["a"][1])} if case["bounds"]["a"] else None,
            "b": {"start": _iso(case["bounds"]["b"][0]), "end": _iso(case["bounds"]["b"][1])} if case["bounds"]["b"] else None,
            "feasible": {"start": _iso(feas[0]), "end": _iso(feas[1])} if feas else None,
            "status": case["extra"]["status"],
            "why_none": _why_none(case, lang),
        },
        # the trace a person reads: turns rejected only for their format (and revised by the same agent) are left out
        "turns": public_turns(g.turns),
        "raw_turns": g.turns,
        "format_retries_hidden": len(g.turns) - len(public_turns(g.turns)),
        "goals": {side: {k: v for k, v in case["goals"][side].items() if not k.startswith("text_")}
                  | {"text": case["goals"][side]["text_es" if lang == "es" else "text_en"]} for side in ("a", "b")},
        "conflict": {"split": case["conflict"]["split"], "all_rules_equal": case["conflict"]["all_rules_equal"],
                     "text": case["conflict"]["text_es" if lang == "es" else "text_en"]},
        "outcome": _nothing_outcome(case, lang) if by == "none" else _outcome(g, case, lang, stop),
        "by": by,
        "fallback_reason": failure,
        "gemini_turns_discarded": discarded,
        "calls": calls,
        "max_calls": MAX_CALLS,
        "max_rounds": MAX_ROUNDS,
        "model": model if by == "gemini" else None,
        "models": models if by == "gemini" else [],
        "disclaimer": DISCLAIMER_ES if lang == "es" else DISCLAIMER,
        "disclaimer_en": DISCLAIMER,
        "how": _L(
            lang,
            "Each agent reads only one utility's public filing for its project and the shared estimate. After every turn the pipeline "
            "(deterministic code, not Gemini) checks the proposal against both filings: the window, the scope ids, the split rule and "
            "its shares, and every number in the text. A rejected turn goes back to its agent with the findings.",
            "Cada agente lee solo el documento público de una empresa para su proyecto y la estimación compartida. Después de cada turno "
            "el sistema (código determinista, no Gemini) comprueba la propuesta con los dos documentos: la ventana, las partidas, la regla "
            "de reparto y sus partes, y cada número del texto. Un turno rechazado vuelve a su agente con los hallazgos.",
        ),
        "ms": round((time.perf_counter() - t0) * 1000),
    }
    # a transient Gemini miss is not cached (try again next time); an answer, a verdict or the plain version is
    cacheable = not (ai and failure)
    return out, cacheable


async def run_case(overlap_id: str, months: int, lang: str = "en", ai: bool = True, as_of: date | None = None) -> dict:
    """The negotiation for one overlap (cached per overlap, window setting, language, ai and date)."""
    t0 = time.perf_counter()
    case = await run_in_threadpool(_case, overlap_id, months, as_of)
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
        task = asyncio.ensure_future(_negotiate(case, lang, ai, key))
        _inflight[key] = task
        task.add_done_callback(lambda _t, k=key: _inflight.pop(k, None))
    res, cacheable = await asyncio.shield(task)
    out = copy.deepcopy(res)
    out["window_months"] = months
    out["cached"] = False
    if cacheable:
        with _cache_lock:
            _cache[key] = copy.deepcopy(out)
            while len(_cache) > CACHE_MAX:
                _cache.popitem(last=False)
    return out


async def terms_for_draft(overlap_id: str, months: int, which: str) -> tuple[dict | None, dict]:
    """The agreed terms of one negotiation, shaped for agreement._base (None when nothing was agreed), and what the
    draft says about them. which: 'en' / 'es' (the Gemini negotiation in that language) or 'plain'."""
    lang, ai = ("en", False) if which == "plain" else (which, True)
    res = await run_case(overlap_id, months, lang, ai)
    o = res["outcome"]
    by = "gemini" if res["by"] == "gemini" else "plain"
    meta = {"which": which, "by": by, "round": o.get("round"), "reason": o["reason"], "calls": res["calls"], "fallback_reason": res["fallback_reason"]}
    if not (o["agreed"] and o["verified"]):
        return None, {**meta, "applied": False}
    t = o["terms"]
    jw = t["joint_window"]
    terms = {
        "scope": [it["id"] for it in t["scope"]],
        "split": {"id": t["split"]["rule"], "basis": {"by_length": "length", "by_kv": "kv", "equal": "equal"}[t["split"]["rule"]],
                  "pct": list(t["split"]["shares"]), "rule": t["split"]["rule_text"], "rationale": t["split"]["rationale"]},
        "window": (_parse_ym(jw["start"]), _parse_ym(jw["end"])) if jw else None,
        "by": by,
        "round": o["round"],
        "text": NEG_TEXT[by],
    }
    return terms, {**meta, "applied": True, "text": NEG_TEXT[by]}


# ------------------------------------------------------------------------------- Hear the negotiation
# Each agent's turn can be heard (Build together, step 2): the words of a verified turn (its concerns, then its note, as the
# agent wrote them and the pipeline checked them) go to voice.py like the briefing's lines do. Agent A speaks with the
# presenter voice (George), agent B with the analyst voice (Matilda), the closing summary with the presenter voice.
# A turn the pipeline rejected is never read aloud (its words did not pass); nothing is added to what was checked: a turn
# with no words of its own (an acceptance) gets one fixed lead-in with no figure in it.
VOICE_ROLE = {"a": "presenter", "b": "analyst", "summary": "presenter"}
_LEAD = {
    "en": {"propose": "I open with these terms.", "counter": "I counter with these terms.", "revise": "I revise my proposal.",
           "accept": "I accept the proposal on the table."},
    "es": {"propose": "Abro con estos términos.", "counter": "Contrapropongo con estos términos.", "revise": "Corrijo mi propuesta.",
           "accept": "Acepto la propuesta sobre la mesa."},
}


def who_label(side: str, short: str, lang: str, ai: bool) -> str:
    """The caption's label for a voice: which published plan the agent reads (an AI agent, or the scripted plain version),
    the neutral coordinator, or the narrator's summary."""
    if side == "summary":
        return _L(lang, "Summary of the exchange (read by the narrator, not an agent)", "Resumen del intercambio (lo lee el narrador, no un agente)")
    if side == "coord":
        return (_L(lang, "The neutral coordinator, an AI agent that speaks for neither company", "El coordinador neutral, un agente de IA que no habla por ninguna empresa")
                if ai else _L(lang, "The coordinator (template, no AI)", "El coordinador (plantilla, sin IA)"))
    return (_L(lang, f"An AI agent for {short}'s published plan", f"Un agente de IA del plan publicado de {short}") if ai
            else _L(lang, f"A scripted agent for {short}'s published plan (plain version)", f"Un agente con guion del plan publicado de {short} (versión simple)"))


def _end(s: str) -> str:
    s = s.strip()
    return s if not s or s[-1] in ".!?…" else s + "."


def spoken_text(turn: dict, lang: str) -> str:
    """What a turn says aloud: its concerns, then its note, verbatim (a full stop added where one is missing)."""
    p = turn["proposal"]
    parts = [_end(str(c)) for c in (p.get("concerns") or [])] + ([_end(str(p["note"]))] if p.get("note") else [])
    parts = [x for x in parts if x]
    return " ".join(parts) if parts else _LEAD.get(lang, _LEAD["en"]).get(turn["kind"], _LEAD["en"]["propose"])


def closing_text(out: dict, lang: str) -> str:
    """The neutral summary, from the outcome: who accepted whose proposal and that the pipeline re-checked it, or why not."""
    o = out["outcome"]
    short = {a["side"]: a["short"] for a in out["agents"]}
    if o.get("agreed") and o.get("verified"):
        return _L(lang,
                  f"In round {o['round']}, {short[o['accepted_by']]}'s agent accepted {short[o['proposed_by']]}'s agent's verified proposal. "
                  "The pipeline then re-checked the final terms against both filings. These are agents reading public filings, not the utilities.",
                  f"En la ronda {o['round']}, el agente de {short[o['accepted_by']]} aceptó la propuesta verificada del agente de {short[o['proposed_by']]}. "
                  "Después, el sistema volvió a comprobar los términos finales con los dos documentos. Son agentes que leen documentos públicos, no las empresas.")
    return _end(o.get("reason") or "")


def add_voice(out: dict) -> dict:
    """Register what each verified turn (and the closing summary) says with voice.py and put the keys on the response (extra
    fields, nothing else changes): turn["voice"] = {key, role, side, text, lang}, out["voice"] = {roles, attribution,
    summary {…} | None}. The keys depend on the configured voices, so this runs per request, on the caller's copy."""
    lang = out["lang"] if out.get("lang") in ("en", "es") else "en"
    lines: list[tuple[dict, str, str]] = []  # (holder, side, text)
    for t in out["turns"]:
        t["voice"] = None
        if t["verdict"]["ok"]:
            t["voice"] = {}
            lines.append((t["voice"], t["agent"], spoken_text(t, lang)))
    summary = None
    if out["turns"] and out["outcome"].get("reason") and not out["outcome"].get("nothing"):
        summary = {}
        lines.append((summary, "summary", closing_text(out, lang)))
    texts = [x[2] for x in lines]
    short = {a["side"]: a["short"] for a in out["agents"]}
    ai = out.get("by") == "gemini"
    for i, (holder, side, text) in enumerate(lines):
        role = VOICE_ROLE[side]
        key = voice.register(text, lang, role, prev_text=texts[i - 1] if i else None, next_text=texts[i + 1] if i + 1 < len(texts) else None)
        holder.update({"key": key, "role": role, "side": side, "text": text, "lang": lang, "who": who_label(side, short.get(side, ""), lang, ai)})
    out["voice"] = {"roles": VOICE_ROLE, "attribution": voice.ATTRIBUTION, "summary": summary or None}
    return out


# ----------------------------------------------------------------------------- the route


@router.post("/api/negotiate/{overlap_id}")
@limiter.limit("30/minute")
async def negotiate(
    request: Request,
    overlap_id: str,
    lang: str = Query("en"),
    window_months: int = Query(gl.WINDOW_DEFAULT),
    ai: bool = Query(True),
):
    """Two AI agents, each reading one utility's public filing, negotiate the draft's terms; every turn verified."""
    if not (0 <= window_months <= gl.WINDOW_MAX):
        raise HTTPException(status_code=422, detail=f"window_months must be between 0 and {gl.WINDOW_MAX}")
    lang = (lang or "").strip().lower()
    if lang not in LANGS:
        raise HTTPException(status_code=422, detail="lang must be 'en' or 'es'")
    if len(overlap_id) > 200:
        raise HTTPException(status_code=422, detail="overlap id is too long")
    out = await run_case(overlap_id, window_months, lang, ai)  # a fresh copy each time (cache hits are deep-copied)
    return await run_in_threadpool(add_voice, out)


@router.get("/api/negotiate/{overlap_id}/live")
def negotiate_live(overlap_id: str, lang: str = Query("en"), window_months: int = Query(gl.WINDOW_DEFAULT)):
    """The turns of a Gemini negotiation still running (the page polls this while its POST waits), so each turn and
    its verdict appear as they happen. {running: false} when none is running for this case; the POST has the result."""
    lang = (lang or "").strip().lower()
    if lang not in LANGS or not (0 <= window_months <= gl.WINDOW_MAX) or len(overlap_id) > 200:
        raise HTTPException(status_code=422, detail="bad lang, window_months or overlap id")
    key = (gl._load()["key"], overlap_id, window_months, lang, True, gl.today_local().isoformat())
    live = _live.get(key)
    if not live or "game" not in live:
        return {"running": False}
    g = live["game"]
    return {
        "running": True,
        "turns": public_turns(copy.deepcopy(g.turns)),
        "calls": live.get("calls", 0),
        "max_calls": MAX_CALLS,
        "working": live.get("working"),
        "elapsed_ms": round((time.perf_counter() - live["t0"]) * 1000),
    }
