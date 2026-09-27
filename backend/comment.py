"""Write my public comment (Proposed data centers → a proposal → "Write your comment").

A resident on a proposal page picks what matters to them, their stance, a length and a language; Gemini writes the
comment they would read at the meeting or send to the decision body, in the first person, from a fact sheet built
out of the SAME data the page shows (vote.py: the sourced facts as reported, the synthetic-model test, the cost
estimates, the questions with their sources, the state's rules, the decision body). A checker then reads every word:

  - every number must be one of the fact sheet's numbers IN THE SAME SENSE (a person's rounding only: "about 11,700"
    for 11,694): "4 hours" is the model's outage, never a report's "denial 4-2"; "2 hospitals" is not the model's "2
    transformers"; 0 and 1 never match on their value alone. Each number comes back with the fact and the source it was
    matched to, so the page can show it on hover or focus (a model result is never credited to a news outlet)
  - nothing may say what the real project, its developer or its utility will do or did ("will cause", "caused",
    "led to", "plans to raise", "will make us pay" ...), and no insults or accusations ("misled", "profiteering" ...)
  - no company, tenant, utility or person the page's sourced facts don't carry (no invented officials, neighbors or
    streets), and no company or utility name at all in a sentence about the model
  - every simulation result is framed as on an open, synthetic grid model, with the model word in its own sentence;
    a sentence about lines over their limits, exceeding or capacity is about the model; never "our grid"
  - where it stands, as reported: a proposal that was withdrawn, denied, never filed, paused, or is already built is
    not before the body, so the comment says so and writes every request for if it (or a campus like it) comes back,
    before an application is heard, or about any expansion; it never asks for a vote on it now
  - clean text in the chosen language (no escapes, no English words or English number formats in a Spanish comment)
  - it is addressed to the decision body the page names, it says the resident's stance, it covers their concerns,
    and it fits the length they chose (about 130 words a minute spoken)

A draft that fails goes back to Gemini once with the findings; if the rewrite fails too (or Gemini is unavailable,
unconfigured or slow), the plain template comment, built from the same facts and passing the same checks, is
returned and labeled. A busy stronger model (503, a slow reply) hands the same prompt to the fast model first. The findings quote the draft, so they stay in the rewrite prompt and the log: the page gets only
the ids of the checks a draft failed. Nothing is stored; the answer is cached per case and choices.

  POST /api/vote/proposal/{id}/comment?ai=true   {concerns: [...0-5], stance, minutes: 1-3, lang: en|es}
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

import catalog as catalog_mod
import llm
import vote
from limiter import limiter

router = APIRouter(tags=["vote"])
log = logging.getLogger("uvicorn.error")

CONCERNS = ("bill", "blackouts", "water", "backup_air", "jobs_taxes")
STANCES = ("questions", "support_conditions", "oppose")
WPM = 130  # a calm public-comment pace, words a minute
AI_TIMEOUT_S = 10
# the writer: one call, not an agent loop, so the stronger model at minimal thinking (~3 s measured Sat) is worth it; its Spanish
# and its questions read far better than Flash-Lite's. The llm.py 429 chain still applies. GEMINI_COMMENT_MODEL= overrides.
COMMENT_MODEL = os.getenv("GEMINI_COMMENT_MODEL", "gemini-3.5-flash").strip() or None
# ... but that model answers 503 "high demand" or takes past its timeout for hours at a time (Sat night: 11 of 11 Render calls fell
# back to the plain template, "comment ok 0"), and llm.py's own chain moves on only for a 429. So the same prompt goes to the fast
# model next (Flash-Lite, ~2-3 s, passed the checker on 25 of 30 first drafts), and a model that just failed is skipped for 45 s.
# GEMINI_COMMENT_FALLBACK_MODEL= overrides; the same as the first model = no second try. (llm.py's chain now also moves on for a 503, a 5xx or a
# timeout and hands over to this same fast model first, so this second step runs only when that whole chain has failed.)
COMMENT_FAST_MODEL = os.getenv("GEMINI_COMMENT_FALLBACK_MODEL", "").strip() or None
FIRST_TRY_S = 8.0  # the stronger model's HTTP timeout (its wait is capped a second past it)
FAST_TRY_S = 12.0
COOLDOWN_S = 45.0
COMMENT_THINKING = "minimal"
DEADLINE_S = 22.0  # the whole AI step (a draft and one rewrite), then the template
CACHE_MAX = 256
TTL_OK_S = 6 * 3600
TTL_RETRY_S = 120  # a template answer while a key is set (Gemini failed): asked again soon
LEN_LO, LEN_HI = 0.55, 1.3  # the words a comment may have, as a share of the target
TEMPLATE_FILL = 1.02  # the template adds optional sentences up to this share of the target

FRAME_EN = "open, synthetic grid model"
FRAME_ES = "modelo abierto y sintético de la red"
# the framing: a sentence that says the model is open and synthetic, in the words a person would use ("an open,
# synthetic grid model", "an open and synthetic model of the grid", "un modelo de red sintético y abierto")
FRAME_WORDS = {
    "en": (re.compile(r"\bsynthetic\b", re.I), re.compile(r"\bopen\b", re.I), re.compile(r"\bmodel|\bsimulat|\bsynthetic\s+(?:power\s+|electric\s+)?grid\b", re.I)),
    "es": (re.compile(r"\bsint[eé]tic[oa]s?\b", re.I), re.compile(r"\babiert[oa]s?\b", re.I), re.compile(r"\bmodelos?\b|\bsimulaci[oó]n|\bred\b", re.I)),
}
FRAME_LEAD = {"en": "On an open, synthetic grid model,", "es": "En un modelo abierto y sintético de la red,"}


def _framed(sentence: str, lang: str) -> bool:
    return all(rx.search(sentence) for rx in FRAME_WORDS[lang])
# a model's broken escaping of accented letters: "l&iacute;nea", "%bfQui%e9n", "sint%tico", "oposici3n"
_GARBLED = re.compile(r"&#?\w+;|%[0-9a-fA-F]{2}|(?<!\d)%(?![\s,.;:)])|(?<=[^\W\d_])\d(?=[^\W\d_])")
# characters no comment needs (a model's stray superscript "¹" for "¿", other scripts, emoji)
_ODD = re.compile(r"[¹²³⁰-⁹₀-₉]|[^\x09\x0a\x0d\x20-\x7e -ſ‐-‧‰-⁞]")
MODEL_WORD = {"en": re.compile(r"\bmodel|\bsimulat", re.I), "es": re.compile(r"\bmodelo|\bsimula", re.I)}
NAME_SLOT = {"en": "[your name]", "es": "[su nombre]"}
PLACE_SLOT = {"en": "[your street or neighborhood]", "es": "[su calle o barrio]"}
# the greeting must keep the placeholder in its own words ("my name is [your name]"): a draft that invents a name, or mixes languages, fails
NAME_SLOT_RX = {"en": re.compile(r"\bmy name is \[your name\]", re.I), "es": re.compile(r"\bme llamo \[(?:su |tu )?nombre\]", re.I)}

CONCERN_TEXT = {
    "en": {"bill": "my electric bill", "blackouts": "blackouts and heat waves", "water": "water", "backup_air": "backup generators and the air", "jobs_taxes": "jobs and local taxes"},
    "es": {"bill": "mi factura de luz", "blackouts": "los apagones y las olas de calor", "water": "el agua", "backup_air": "los generadores de respaldo y el aire", "jobs_taxes": "los empleos y los impuestos locales"},
}
# the questions (backend/demo/questions.json ids) each concern leads with, most important first
CONCERN_QUESTIONS = {
    "bill": ("who-pays", "if-it-leaves"),
    "blackouts": ("firm-or-flexible", "heat-and-storms", "public-study"),
    "water": ("water",),
    "backup_air": ("backup-power",),
    "jobs_taxes": ("incentives",),
}
DEFAULT_CONCERNS = ("blackouts", "bill")  # what a comment with no concern ticked still covers
# what the checker looks for to see that a concern was covered (a loose, honest test: the topic's own words)
CONCERN_RX = {
    "bill": re.compile(r"\bbill|\brate|\bpay|\bcost|\bfactura|\bpag|\btarifa|\bcosto", re.I),
    "blackouts": re.compile(r"blackout|outage|without power|heat|firm|flexib|apag|sin luz|calor|firme", re.I),
    "water": re.compile(r"\bwater|\bagua", re.I),
    "backup_air": re.compile(r"generator|backup|\bair\b|generador|respaldo|\baire\b", re.I),
    "jobs_taxes": re.compile(r"\bjobs?\b|\btax|revenue|incentive|empleo|impuesto|ingreso|incentivo", re.I),
}
STANCE_RX = {
    "questions": re.compile(r"\?"),
    "support_conditions": re.compile(r"\bsupport|\bin favor\b|\bapoy|\ba favor\b", re.I),
    "oppose": re.compile(r"\boppos|\bvote (?:no|against)\b|\bme opongo|\bme opondr|\boposici[oó]n|\bvot(?:ar|en|e|ar[aá]n?) (?:en contra|no)\b", re.I),
}

# Where the proposal stands, as reported (the catalog's status and the page's status note). Only a "pending" proposal is
# before a body now; for the rest the comment never asks the body to vote on it: it is written for if it, or a campus like
# it, comes back (withdrawn, denied, paused), before an application is heard (not filed), or about how it is served and any
# expansion (operating or under construction).
SIT_CLAUSE = {
    "en": {"not_filed": "As reported, no application has been filed for it yet", "withdrawn": "As reported, the application was withdrawn",
           "denied": "As reported, the request was denied", "paused": "As reported, it is not moving ahead as proposed for now",
           "operating": "As reported, it is already operating", "under construction": "As reported, it is already under construction"},
    "es": {"not_filed": "Según lo reportado, todavía no se ha presentado ninguna solicitud para este proyecto", "withdrawn": "Según lo reportado, la solicitud fue retirada",
           "denied": "Según lo reportado, la solicitud fue denegada", "paused": "Según lo reportado, por ahora no avanza tal como se propuso",
           "operating": "Según lo reportado, ya está en operación", "under construction": "Según lo reportado, ya está en construcción"},
}
SIT_CLAUSE_SHORT = {"en": {"not_filed": "As reported, no application has been filed yet", "paused": "As reported, it is on hold for now"},
                    "es": {"not_filed": "Según lo reportado, aún no se ha presentado ninguna solicitud", "paused": "Según lo reportado, no avanza por ahora"}}
# a one-minute comment's stance and ask when no vote is pending: [lang][group][stance], group = not_filed | back | built
SHORT_STANCE = {
    "en": {"not_filed": {"questions": "; I am asking before one is.", "support_conditions": "; I could support a campus like it with my conditions in writing.",
                         "oppose": "; if one is filed as announced, I would oppose it."},
           "back": {"questions": "; I am asking in case it comes back.", "support_conditions": "; if it comes back, I could support it with my conditions in writing.",
                    "oppose": "; if it comes back as proposed, I would oppose it."},
           "built": {"questions": "; I am asking about any expansion.", "support_conditions": "; I can support an expansion with my conditions in writing.",
                     "oppose": "; I would oppose any expansion."}},
    "es": {"not_filed": {"questions": "; pregunto antes de que se presente.", "support_conditions": "; podría apoyar un campus como este con mis condiciones por escrito.",
                         "oppose": "; si se presenta tal como se anunció, me opondría."},
           "back": {"questions": "; pregunto por si vuelve.", "support_conditions": "; si vuelve, podría apoyarlo con mis condiciones por escrito.",
                    "oppose": "; si vuelve igual, me opondría."},
           "built": {"questions": "; pregunto por cualquier ampliación.", "support_conditions": "; puedo apoyar una ampliación con mis condiciones por escrito.",
                     "oppose": "; me opondría a cualquier ampliación."}},
}
SHORT_ASK = {
    "en": {"not_filed": {"questions": "If an application is filed, please get these answers in public before any vote.",
                         "oppose": "If one is filed as announced, please vote no until these answers are public.",
                         "support_conditions": "If one is filed, please write these conditions into any approval: {conds}."},
           "back": {"questions": "If it comes back, please get these answers in public before any vote.",
                    "oppose": "If it comes back as proposed, please vote no until these answers are public.",
                    "support_conditions": "If it comes back, please write these conditions into any approval: {conds}."},
           "built": {"questions": "Please get these answers in public before any expansion is approved.",
                     "oppose": "Please vote no on any expansion until these answers are public.",
                     "support_conditions": "Please write these conditions into any approval for an expansion: {conds}."}},
    "es": {"not_filed": {"questions": "Si se presenta una solicitud, les pido estas respuestas en público antes de votar.",
                         "oppose": "Si se presenta, les pido que voten no hasta que estas respuestas sean públicas.",
                         "support_conditions": "Si se presenta, les pido que incluyan estas condiciones en cualquier aprobación: {conds}."},
           "back": {"questions": "Si vuelve, les pido estas respuestas en público antes de votar.",
                    "oppose": "Si vuelve, les pido que voten no hasta que estas respuestas sean públicas.",
                    "support_conditions": "Si vuelve, les pido que incluyan estas condiciones en cualquier aprobación: {conds}."},
           "built": {"questions": "Les pido estas respuestas en público antes de aprobar cualquier ampliación.",
                     "oppose": "Les pido que voten no a cualquier ampliación hasta que estas respuestas sean públicas.",
                     "support_conditions": "Les pido que incluyan estas condiciones en cualquier aprobación de una ampliación: {conds}."}},
}
SIT_NOTE = {  # the line the page shows above the letter (the page is English)
    "not_filed": "As reported, no application has been filed yet, so the comment asks its questions before one is heard.",
    "withdrawn": "As reported, the application was withdrawn, so the comment is written for if it, or a campus like it, comes back.",
    "denied": "As reported, the request was denied, so the comment is written for if it, or a campus like it, comes back.",
    "paused": "As reported, it is not moving ahead as proposed for now, so the comment is written for if it, or a campus like it, comes back.",
    "built": "As reported, it is already {status}, so the comment is about how it is served and about any expansion or new campus like it.",
}
# the words that show a comment said where it stands (checked for every situation but "pending")
SIT_SAID = {
    "not_filed": re.compile(r"\bno application|\bnot (?:yet )?(?:been )?filed|\bnever filed|\bno se ha presentado|\bninguna solicitud|\bsin (?:una )?solicitud|\bno hay (?:una )?solicitud", re.I),
    "withdrawn": re.compile(r"withdr|\bretir", re.I),
    "denied": re.compile(r"\bden(?:ied|ial|y)\b|\breject|\bdeneg|\brechaz", re.I),
    "paused": re.compile(r"\bpaus|\bcancel|\bnot moving ahead|\bnot going ahead|\bon hold|\bstopped|\bno avanza|\bsuspend|\bdetenid|\ben pausa", re.I),
    "built": re.compile(r"\boperat|\bunder construction|\bbeing built|\ben operaci|\ben construcci|\bfuncionando|\bconstruy", re.I),
}
# a request to the body, or "as it stands": when no vote is pending, it must be conditional (if it comes back, any expansion ...)
_REQUEST = {
    "en": re.compile(r"\b(?:please|I\s+(?:ask|urge|request|call\s+on)|we\s+(?:ask|urge)|(?:you|the\s+\w+)\s+should|vote\s+(?:no|yes|against))\b|\bas\s+it\s+stands\b|\bcurrently\s+(?:proposed|presented)", re.I),
    "es": re.compile(r"\b(?:les\s+(?:pido|ruego|insto|solicito)|pido|solicito|voten|deben|deber[ií]an?)\b|\btal\s+como\s+est[aá]\b|\bactualmente\s+(?:propuest|present)", re.I),
}
_VOTEWORD = re.compile(r"\bvot\w*|\bapprov\w*|\bden(?:y|ial)\b|\breject\b|\baprob\w*|\baprueb\w*|\bdeneg\w*|\brechac\w*|\bas\s+it\s+stands|\bcurrently\s+(?:proposed|presented)|\btal\s+como\s+est|\bactualmente\s+(?:propuest|present)", re.I)
_IFBACK = re.compile(
    r"\bif\b|\bcomes?\s+back|\breturns?\b|\bresubmit\w*|\bagain\b|\bfuture\b|\bnew\b|\bexpan\w*|\bany\b|\blike\s+it\b|\bonce\s+(?:an|one)\b"
    r"|\bsi\b|\bvuelv\w*|\bnuev\w*|\bfutur\w*|\bampliaci\w*|\bcualquier\b|\bcomo\s+él\b|\botra\s+vez|\bde\s+nuevo", re.I)

# Spanish wording of the questions (questions.json is English) for the template; Gemini translates on its own
Q_ES = {
    "firm-or-flexible": ("¿Puede la empresa eléctrica pedir al campus que reduzca su consumo durante una ola de calor o una emergencia, o recibe un servicio firme que se mantiene encendido primero?",
                         "Me gustaría escuchar un sí o un no claro sobre esa reducción, y verlo por escrito en el contrato de servicio."),
    "who-pays": ("¿Quién paga las nuevas subestaciones, líneas y generación que necesita este campus: el propio campus o todos los clientes de la empresa eléctrica?",
                 "Me gustaría ver la tarifa o el contrato presentado ante el regulador."),
    "if-it-leaves": ("¿Qué pasa con el costo de esos equipos si el campus se retrasa, se reduce o se va antes de tiempo?",
                     "Me gustaría escuchar sobre cargos de salida, un plazo mínimo de contrato y garantías."),
    "backup-power": ("¿Usará el campus generadores de respaldo o una planta propia, con qué combustible y con qué permisos de aire?",
                     "Me gustaría saber cuántos generadores habrá, con qué combustible y cuántas horas al año podrán funcionar."),
    "water": ("¿Cuánta agua usará, de dónde vendrá y qué pasa durante una sequía?",
              "Me gustaría saber cuántos galones por día usará a plena capacidad, de qué fuente y cuál es su plan para una sequía."),
    "incentives": ("¿Qué exenciones de impuestos u otros incentivos recibe el proyecto, y qué empleos e ingresos locales se prometen por escrito?",
                   "Me gustaría ver el valor de cada incentivo y los compromisos de empleo por escrito."),
    "public-study": ("¿Ha publicado la empresa eléctrica o el operador de la red un estudio de cómo se conecta esta carga y qué necesita, y dónde puede leerlo el público?",
                     "Me gustaría saber el nombre del estudio, su fecha y si existe una versión pública."),
    "heat-and-storms": ("¿Cuál es el plan para una ola de calor o un huracán: el campus reduce su consumo o usa su propia energía, y a quién se llama primero?",
                        "Me gustaría ver un plan de operación por escrito y probado."),
}
CONDITION = {  # (full, short): the short one for a one-minute comment
    "en": {
        "bill": ("the campus pays for the grid upgrades built for it, so they are not added to household bills", "the campus pays for its own grid upgrades"),
        "blackouts": ("the campus agrees to cut back first in a heat wave or an emergency, and the utility's study of how it connects is made public", "it cuts back first in a heat wave"),
        "water": ("a public water plan, with the source and what happens in a drought", "a public water plan"),
        "backup_air": ("the air permits for any generators, with their fuel and yearly run-hour limits, are made public", "public air permits for its generators"),
        "jobs_taxes": ("the jobs, local revenue and incentives are put in writing, with what happens if a promise is missed", "its jobs and tax promises in writing"),
    },
    "es": {
        "bill": ("que el campus pague las mejoras de la red construidas para él, para que no se sumen a las facturas de los hogares", "que el campus pague sus propias mejoras de la red"),
        "blackouts": ("que el campus acepte reducir su consumo primero en una ola de calor o una emergencia, y que el estudio de conexión de la empresa eléctrica sea público", "que reduzca su consumo primero en una ola de calor"),
        "water": ("un plan de agua público, con la fuente y lo que pasa en una sequía", "un plan de agua público"),
        "backup_air": ("que los permisos de aire de cualquier generador, con su combustible y sus límites de horas al año, sean públicos", "permisos de aire públicos para sus generadores"),
        "jobs_taxes": ("que los empleos, los ingresos locales y los incentivos queden por escrito, con lo que pasa si no se cumple una promesa", "sus promesas de empleo e impuestos por escrito"),
    },
}


# ------------------------------------------------------------------------------------ the request
class CommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concerns: list[Literal["bill", "blackouts", "water", "backup_air", "jobs_taxes"]] = Field(default_factory=list, max_length=5)
    stance: Literal["questions", "support_conditions", "oppose"] = "questions"
    minutes: int = Field(2, ge=1, le=3, strict=True)
    lang: Literal["en", "es"] = "en"

    @field_validator("concerns")
    @classmethod
    def _once(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("list each concern once")
        return v


def _ordered(concerns) -> list:
    """The resident's concerns in the page's order (so the same choices always make the same comment)."""
    return [c for c in CONCERNS if c in (concerns or [])]


# ------------------------------------------------------------------------------------ the fact sheet
@dataclass
class Fact:
    key: str
    kind: str  # reported | model | estimate | question | rule | civic
    text: str  # the fact as Gemini reads it (English, the page's own words)
    values: list = field(default_factory=list)  # its numbers (as computed); the numbers in `text` count too
    money: list = field(default_factory=list)  # its dollar amounts
    source: dict = field(default_factory=dict)  # {label, url}
    section: str | None = None  # the page section it is shown in (a jump target on the page)
    # filled by _index: every printed form of every number -> the senses it is used in (the unit or word after it: "hour",
    # "line", "people" ...; None when nothing follows) and the words right before it; the dollar amounts with their sense;
    # identifiers such as docket numbers ("2025-00058")
    senses: dict = field(default_factory=dict)
    prevs: dict = field(default_factory=dict)
    cash: list = field(default_factory=list)
    idents: set = field(default_factory=set)


@dataclass
class Sheet:
    pid: str
    entry: dict
    civic: dict
    state: dict
    sim: dict
    cost: dict | None
    questions: dict
    facts: list
    addressee: str  # the decision body's name, or the county when it was not researched
    researched: bool
    send: dict
    names_ok: str  # every name the page's sourced facts carry (lowercase blob)
    real_words: list  # [(word, case_sensitive)]: the developer's / utility's distinctive words: never in a sentence about the model
    situation: dict  # {kind: pending|not_filed|withdrawn|denied|paused|built, status, moratorium}
    protected: list  # proper names a Spanish comment keeps in English (the body, the project, the utility, rule and case titles)


def _src_for(entry: dict, pat: str) -> dict:
    srcs = entry.get("sources") or []
    for s in srcs:
        if re.search(pat, s.get("supports") or "", re.I):
            return {"label": f"As reported by {s['outlet']}", "url": s["url"]}
    if srcs:
        return {"label": f"As reported by {srcs[0]['outlet']}", "url": srcs[0]["url"]}
    return {"label": "The catalog entry on this page", "url": None}


MODEL_SRC = {"label": "Overload's test on an open, synthetic grid model (Breakthrough Energy / Texas A&M, CC-BY 4.0), on this page", "url": None}


def _cost_src(*lines: dict) -> dict:
    """'Estimate on this page, from Black & Veatch for WECC, U.S. Census Bureau and EIA': every source behind the line (a
    per-household dollar figure is the upgrade estimate divided over the Census households, so it names both)."""
    names, url = [], None
    for line in lines:
        for s in (line or {}).get("sources") or []:
            short = re.split(r",\s", s.get("title") or "", maxsplit=1)[0].strip()
            if short and short not in names:
                names.append(short)
            url = url or s.get("url")
    names = names[:3]
    if not names:
        return {"label": "Estimate on this page", "url": url}
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return {"label": f"Estimate on this page, from {joined}", "url": url}


def _situation(entry: dict, civ: dict) -> dict:
    """Where it stands, as reported: pending (before a body), not_filed, withdrawn, denied, paused, or built."""
    status = (entry.get("status") or "").lower()
    blob = f"{civ.get('status_note') or ''} {entry.get('year') or ''}".lower()
    if status in ("operating", "under construction"):
        kind = "built"
    elif re.search(r"\bno application\b|\bnot (?:yet )?(?:been )?filed\b|\bnever filed\b|\bno later filing\b", blob):
        kind = "not_filed"
    elif status == "paused/canceled":
        if re.search(r"\bden(?:y|ied|ial)\b|\breject", blob):
            kind = "denied"
        elif re.search(r"\b(?:applicants?|developers?|company|owners?)\s+(?:\w+\s+){0,2}?withdr|\bwithdrawn\b|\bapplication was withdrawn", blob):
            kind = "withdrawn"
        else:
            kind = "paused"
    else:
        kind = "pending"
    return {"kind": kind, "status": status, "moratorium": kind not in ("pending", "built") and "moratori" in blob}


def _size_words(e: dict, es: bool) -> str:
    """The size as the sources give it: "1,200 MW", or, for a project whose MW is not a reported campus load
    (catalog.SHOWN_AS), its own wording ("200 MW (the company's microgrid figure)")."""
    if es:
        return e.get("size_text_es") or f"{_n_es(e['mw'])} MW"
    return e.get("size_text") or f"{vote._n(e['mw'])} MW"


def _facts(entry, civ, st, sim, cost, qs) -> list[Fact]:
    F: list[Fact] = []
    company = entry.get("company") or ""
    confirmed = bool(company) and not re.match(r"developer not confirmed", company, re.I)
    dev = f"developer or applicant, as reported: {company}" if confirmed else "developer: not confirmed in the sources"
    F.append(Fact("project", "reported", f"Project, as reported: {entry['name']}; {dev}; place: {entry['place_text']} (location approximate); status, as reported: {entry['status_text']}.",
                  source=_src_for(entry, r"developer|status"), section="top"))
    if entry.get("mw"):
        F.append(Fact("size", "reported", f"Reported size: {_size_words(entry, False)}.", values=[entry["mw"]], source=_src_for(entry, r"\bmw\b|\bgw\b|size"), section="top"))
    if entry.get("year"):
        F.append(Fact("timing", "reported", f"Timing, as reported: {entry['year']}.", source=_src_for(entry, r"timeline|timing|operation|year"), section="top"))
    if entry.get("mw_basis"):
        F.append(Fact("basis", "reported", f"About the project, compiled from the reported sources: {entry['mw_basis']}", source=_src_for(entry, r"\$|timeline|\bmw\b"), section="top"))
    if civ.get("status_note"):  # the page's "Where it stands" note, researched and sourced
        cs = civ.get("sources") or []
        F.append(Fact("where", "reported", f"Where it stands, as reported: {civ['status_note']}",
                      source={"label": f"As reported by {vote._outlet(cs[0]['url'])}", "url": cs[0]["url"]} if cs else _src_for(entry, r"status"), section="top"))

    if sim.get("tested"):
        mw = vote._n(sim["tested_mw"])
        F.append(Fact("room", "model", f"On an open, synthetic grid model, the site has room for {vote._n(sim['room_mw'])} MW before any line goes over its limit.", values=[sim["room_mw"]], source=MODEL_SRC, section="grid"))
        if sim["overloaded"]:
            both = re.search(r"\bline", sim["over_text"]) and re.search(r"\btransformer", sim["over_text"])
            total = f" ({sim['overloaded']} lines and transformers in all)" if both else ""
            F.append(Fact("over", "model", f"On the model, at {mw} MW, {sim['over_text']} go over their limits{total}.", values=[sim["tested_mw"], sim["overloaded"]], source=MODEL_SRC, section="grid"))
        else:
            F.append(Fact("over", "model", f"On the model, at {mw} MW, nothing goes over its limit.", values=[sim["tested_mw"]], source=MODEL_SRC, section="grid"))
        f, m = sim["flexible"], sim["firm"]
        F.append(Fact("flexible", "model", f"Flexible service (the campus can be cut off when its own lines trip), on the model: {f['text']}", values=[f["people"]], source=MODEL_SRC, section="grid"))
        for a in (f.get("areas") or [])[:3]:
            F.append(Fact(f"area:{a['area']}", "model", f"On the model, {a['area']} loses power: {a['people_text']} people (estimate).", values=[a["people"]], source=MODEL_SRC, section="grid"))
        F.append(Fact("firm", "model", f"Firm service (the grid operator tries to keep the campus on by cutting other customers instead), on the model: {m['text']}", values=[m["people"]], source=MODEL_SRC, section="grid"))
    if cost:
        b, up, bill, who = cost.get("blackout"), cost.get("upgrades"), cost.get("power_bill"), cost.get("who_pays")
        if b and b["high"] > 0:
            F.append(Fact("outage", "model", f"On the model, the time without power in that blackout: {b['outage_label']} (a rule of thumb from the incident's size).", values=[b["hours"]], source=_cost_src(b), section="money"))
            F.append(Fact("blackout_cost", "model", f"On the model, the cost of that blackout to the people and businesses without power: {b['range']} (estimate).", money=[b["low"], b["high"]], source=_cost_src(b), section="money"))
        if up and up["high"] > 0:
            F.append(Fact("upgrades", "model", f"On the model, {up['label'].lower()}: {up['range']} (estimate). {up['meaning']}", values=[up.get("count") or 0], money=[up["low"], up["high"]], source=_cost_src(up), section="money"))
        if bill and bill["high"] > 0:
            F.append(Fact("power_bill", "estimate", f"The campus's own power bill: {bill['range']} a year (estimate at the state's average industrial price).", money=[bill["low"], bill["high"]], source=_cost_src(bill), section="money"))
        if who and who["high"] > 0:
            F.append(Fact("who_pays", "model", f"If the model's upgrades were spread over all {who['households_text']} {cost['region_name']} households: {who['range']} a month each (illustrative; regulators decide who really pays).",
                          values=[who["households"]], money=[who["low"], who["high"]], source=_cost_src(up, who), section="money"))
        for sp in (cost.get("spread") or [])[:1]:
            F.append(Fact("spread", "model", f"If the model's upgrades were paid by {vote._n(sp['households'])} households instead: {sp['text']} a month each (illustrative).",
                          values=[sp["households"]], money=[sp["per_month"]], source=_cost_src(up, who), section="money"))

    for q in qs.values():
        src = (q.get("sources") or [{}])[0]
        notes = "; ".join(f"{s['title']}: {s['supports']}" if s.get("supports") else s["title"] for s in q.get("sources") or [])
        F.append(Fact(f"q:{q['id']}", "question", f"Question to ask ({(q.get('topic') or '').lower()}): {q['question']} Why it matters: {q['why']} Listen for: {q['listen_for']} Sources: {notes}",
                      source={"label": src.get("title") or "Source listed with the question", "url": src.get("url")}, section="ask"))
    for p in (st.get("policy") or [])[:3]:
        F.append(Fact(f"rule:{p['title'][:40]}", "rule", f"{st['name']} has on the books: {p['title']}" + (f": {p['summary']}" if p.get("summary") else ""),
                      source={"label": p["title"], "url": p.get("url")}, section="ask"))

    body = civ.get("decision_body")
    if body:
        F.append(Fact("body", "civic", f"The local decision body: {body['name']}.", source={"label": body["name"], "url": body.get("url")}, section="speak"))
    if civ.get("utility"):
        u = civ["utility"]
        F.append(Fact("utility", "civic", f"Utility named in the sources: {u['name']} (as reported).",
                      source={"label": f"As reported by {vote._outlet(u['source'])}" if u.get("source") else "The page's sources", "url": u.get("source")}, section="speak"))
    for c in civ.get("cases") or []:
        label = " ".join(x for x in (c.get("commission"), c.get("number"), c.get("title")) if x)
        F.append(Fact(f"case:{c.get('number') or label[:30]}", "civic", f"A case at the state regulator: {label}" + (f". {c['why']}" if c.get("why") else ""),
                      source={"label": label, "url": c.get("url")}, section="speak"))
    if st.get("commission"):
        F.append(Fact("regulator", "civic", f"The state utility regulator: {st['commission']}.", source={"label": st["commission"], "url": st.get("url")}, section="speak"))
    return F


GENERIC = set(
    "data center centers campus park project technology tech holdings compute computing solutions energy developer applicant partner infrastructure "
    "tenant undisclosed disclosed user places international airport county near town outside unincorporated the and with end not llc inc corp "
    "company group ventures capital partners properties development realty power electric utility utilities city "
    "site sites owner owners landowner landowners builder builders operator operators reports reported reportedly per halls hall "
    "joint venture affiliate affiliates subsidiary unnamed proposed global new national american united".split()
)


ACRONYM_NOT_NAME = {"MW", "GW", "MWH", "MVA", "KV", "AI", "TPU", "TPUS", "GPU", "GPUS", "US", "USA", "LLC", "INC", "LP", "CEO", "IT", "HQ", "ID", "EV", "PV", "AC", "DC",
                    "II", "III", "IV", "NA", "TBD", "USD", "EU", "UK", "HPC", "CO", "PPA", "NYSE", "IPO"}


def _sheet(pid: str, concerns: list) -> Sheet:
    """The fact sheet for proposal `pid` (404 when the catalog has no such id), from the page's own blocks."""
    cat = catalog_mod.catalog()
    e = vote._canonical(cat, pid)
    if e is None:
        raise HTTPException(status_code=404, detail="No proposal with that id in the catalog")
    parts = vote._parts(e, cat["mtime"])
    entry, civ, st, sim, cost = parts["entry"], parts["civic"], parts["state"], parts["simulation"], parts["cost"]
    by_id = {q["id"]: q for q in vote.questions()}
    qs = {k: by_id[k] for k in _qids(concerns) if k in by_id}
    facts = _facts(entry, civ, st, sim, cost, qs)
    for f in facts:
        _index(f)

    body = civ.get("decision_body")
    if body:
        addressee, researched = body["name"], True
        url = body.get("comment_url") or body.get("url")
        send = {"name": body["name"], "url": url, "label": "Public comment page" if body.get("comment_url") else "The decision body's page", "how": body.get("how_to_comment")}
    else:
        who = entry.get("county_text") or "the county"
        addressee, researched = who, False
        send = {"name": None, "url": None, "label": None,
                "how": f"Not researched yet for this proposal: ask the {who} clerk which board hears it, and for the meeting date, the agenda and how to sign up to speak."}

    places = " ".join(str(entry.get(k) or "") for k in ("city", "county", "state_name")).lower()
    # words the comment itself uses about the model ("the site has room ...") are never a name, whatever a company is called
    model_vocab = set(re.findall(r"[a-z]+", " ".join(f.text for f in facts if f.kind == "model").lower() + " " + FRAME_EN + " " + FRAME_ES.lower()))
    real: set = set()

    def words_of(name: str) -> set:
        """The capitalized words of a company or utility name, matched as written (the catalog's company field also
        carries lowercase notes such as 'which exited in Dec 2024' or 'separate 50 MW lease': those are not names)."""
        out = set()
        for w in re.findall(r"[A-Za-z][A-Za-z0-9&'-]{1,}", name):
            w = w.strip("'-")
            lw = w.lower()
            if not w[:1].isupper() or lw in GENERIC or lw in places or w.upper() in ACRONYM_NOT_NAME:
                continue
            if 2 <= len(w) <= 5 and w.isupper():  # PBA, QTS, NTT, TECO
                out.add((w, True))
            elif len(w) >= 4 and lw not in model_vocab:
                out.add((w, True))
        return out

    company = entry.get("company") or ""
    if company and not re.match(r"developer not confirmed", company, re.I):
        real |= words_of(company)
    if civ.get("utility"):
        real |= words_of(civ["utility"]["name"])
    protected = [addressee, entry.get("name") or "", company, (civ.get("utility") or {}).get("name") or "", st.get("commission") or ""]
    protected += [p.get("title") or "" for p in st.get("policy") or []]
    protected += [" ".join(x for x in (c.get("commission"), c.get("number"), c.get("title")) if x) for c in civ.get("cases") or []]
    return Sheet(pid=entry["id"], entry=entry, civic=civ, state=st, sim=sim, cost=cost, questions=qs, facts=facts, addressee=addressee, researched=researched,
                 send=send, names_ok=" ".join(f.text for f in facts).lower(), real_words=sorted(real), situation=_situation(entry, civ),
                 protected=sorted({p for p in protected if p and len(p) >= 3}, key=len, reverse=True))


# ------------------------------------------------------------------------------------ numbers
# A figure in the comment must be one of the facts' figures, read in its context: a dollar amount ("$83–147 million",
# "entre 0,02 y 0,10 dólares") is compared with the facts' dollar amounts (within 5 %, a person's rounding); any other
# number with every number the facts carry, in the rounded forms a person would print (within 3 %).
_NUM = re.compile(r"(?<![\w])\d+(?:[.,]\d+)*")
ROUND_TOL = 0.03
MONEY_TOL = 0.05
_SCALE = re.compile(r"\s?(mil\s+millones|millones|mill[oó]n|millions?|billions?|thousands?|mil|bn|[KMB](?![A-Za-z]))\b", re.I)
SCALE_MULT = {"mil millones": 1e9, "millones": 1e6, "millón": 1e6, "millon": 1e6, "million": 1e6, "millions": 1e6, "billion": 1e9, "billions": 1e9,
              "thousand": 1e3, "thousands": 1e3, "mil": 1e3, "bn": 1e9, "k": 1e3, "m": 1e6, "b": 1e9}
_CURRENCY = re.compile(r"\s?(?:de\s+)?(?:USD|dollars?|d[oó]lares)\b", re.I)
_DOLLAR_BEFORE = re.compile(r"(?:US)?\$\s?$")
_PCT = re.compile(r"\s?(?:%|percent\b|por\s+ciento\b)", re.I)
_UNIT = re.compile(r"\s?(MW|GW|MVA|kV|km|MWh)\b")
_RANGE = re.compile(r"^\s*(?:–|—|-|to|a|hasta)\s*$", re.I)
_RANGE_AND = re.compile(r"^\s*(?:and|y)\s*$", re.I)
_BETWEEN = re.compile(r"(?:between|entre)\s+(?:US)?\$?\s*$", re.I)
# a scale or a vague multitude said without a figure from the facts ("millions of dollars", "hundreds of thousands", "cientos de miles")
_SCALE_WORDS = {
    "en": re.compile(r"\b(millions?|billions?|thousands?|hundreds|dozens|scores\s+of|mil\s+millones|millones|mill[oó]n|billones)\b", re.I),
    "es": re.compile(r"\b(mil\s+millones|millones|mill[oó]n|billones|miles|cientos|centenares|decenas|millions?|billions?|thousands?|hundreds)\b", re.I),
}
_WORDS_EN = {w: i + 2 for i, w in enumerate("two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS_EN = {w: (i + 2) * 10 for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_ONES_EN = {w: i + 1 for i, w in enumerate("one two three four five six seven eight nine".split())}
_WORDS_ES = {w: i + 2 for i, w in enumerate("dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis diecisiete dieciocho diecinueve veinte".split())}
_WORDS_ES.update({"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100})
_WORDNUM = {
    "en": re.compile(r"\b(?:(" + "|".join(_TENS_EN) + r")(?:-(one|two|three|four|five|six|seven|eight|nine))?|(" + "|".join(_WORDS_EN) + r"))\b", re.I),
    "es": re.compile(r"\b(" + "|".join(sorted(_WORDS_ES, key=len, reverse=True)) + r")\b", re.I),
}


def _canon(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.2f}".rstrip("0").rstrip(".")


def _forms(v: float) -> set:
    """A number as a sentence may print it, always in its own units: itself, and its roundings (to 0-2 decimals, whole,
    tens ... millions) that stay within ROUND_TOL of it. "about 11,700" passes for 11,694; "12" does not pass for 11.7."""
    a = abs(float(v))
    if not math.isfinite(a):
        return set()
    out = {_canon(a)}
    cands = [round(a, 2), round(a, 1), round(a), math.floor(a), math.ceil(a)]
    for k in range(1, 10):
        p = 10**k
        cands += [round(a / p) * p, math.floor(a / p) * p, math.ceil(a / p) * p]
    for r in cands:
        if r > 0 and abs(r - a) <= ROUND_TOL * a:
            out.add(_canon(r))
    return out


def _vals(tok: str, lang: str) -> list:
    """A printed number's possible values: 1,200 / 11,700 / 1.1 (English); in Spanish also 1.200 / 0,10 / 2,7."""
    t = tok.strip(",.")
    out = []
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", t):
        out.append(float(t.replace(",", "")))
    if lang == "es" and re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+", t):
        out.append(float(t.replace(".", "").replace(",", ".")))
    if not out:
        try:
            out.append(float(t.replace(",", "")))
        except ValueError:
            pass
    return out


@dataclass
class Tok:
    s: int  # the figure's own characters
    e: int
    vals: list
    lo: int  # the marked span: a "$" before; a scale, currency, unit or % after
    hi: int
    money: bool = False
    mult: float = 1.0
    unit: str | None = None
    pct: bool = False
    word: bool = False  # spelled out ("eleven")
    rng: "Tok | None" = None  # the other end of a range
    sense: str | None = None  # what it counts: a unit ("hour", "line", "people" ...) or the word after it
    prev: str | None = None  # the word right before it (not a stop word)
    ident: str | None = None  # an identifier such as a docket number ("2025-00058"): matched as written


# What a figure counts, read from the words around it, in either language, so "4 hours" is never credited to a report's
# "denial 4-2" and "2 hospitals" never passes for the model's "2 transformers".
_UNITS: dict = {}
for _sense_name, _ws in {
    "mw": "mw megawatt megawatts megavatio megavatios",
    "line": "line lines línea líneas linea lineas",
    "transformer": "transformer transformers transformador transformadores",
    "people": "people person persons resident residents neighbor neighbors neighbour neighbours personas persona residentes vecinos vecinas habitantes",
    "household": "household households home homes hogar hogares vivienda viviendas casa casas",
    "customer": "customer customers cliente clientes",
    "hour": "hour hours hrs hora horas",
    "day": "day days día días dia dias",
    "month": "month months mes meses",
    "year": "year years año años",
    "minute": "minute minutes minuto minutos",
    "hospital": "hospital hospitals hospitales",
    "school": "school schools escuela escuelas",
    "substation": "substation substations subestación subestaciones",
    "plant": "plant plants planta plantas",
    "generator": "generator generators generador generadores turbine turbines turbina turbinas",
    "town": "town towns city cities pueblo pueblos ciudad ciudades",
    "county": "county counties condado condados",
    "job": "job jobs empleo empleos puesto puestos",
    "gallon": "gallon gallons galón galones",
    "acre": "acre acres",
    "mile": "mile miles milla millas",
    "building": "building buildings edificio edificios",
    "lawsuit": "lawsuit lawsuits demanda demandas",
    "vote": "vote votes voted votación votaciones voto votos votó votaron",
    "date": "january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec "
            "enero febrero marzo abril mayo junio julio agosto septiembre setiembre octubre noviembre diciembre",
    "phase": "phase phases fase fases",
    "ref": "order section chapter resolution docket case number no nos orden sección capítulo resolución expediente caso número",
}.items():
    for _w in _ws.split():
        _UNITS[_w] = _sense_name
KNOWN_SENSES = set(_UNITS.values()) | {"mva", "kv", "km", "mwh", "pct", "yr"}
PREFIX_SENSES = {"date", "phase", "vote", "ref"}  # read from the word BEFORE the figure ("July 15", "phase 1", "voted 5-1")
_STOP = set(
    "the a an of to in on at for and or by from with about some around nearly almost approximately roughly over under up than more less just only "
    "our my their its this that these those each per another other all both roughly estimated est "
    "de del la el los las y o en al un una unos unas por para con sobre cerca casi aproximadamente alrededor más menos solo "
    "nuestros nuestras nuestro nuestra mis sus su este esta estos estas cada otro otra otros otras todos todas ambos".split()
)
_UNIT_OF = {"MW": "mw", "GW": "mw", "MVA": "mva", "kV": "kv", "km": "km", "MWh": "mwh"}
_BOUND = re.compile(r"[.;:!?()\[\],\"“”«»\d]")
_ABBR_PREV = {"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "no", "nos", "st"}
_NEXT_SKIP = {"may", "mar", "no", "nos", "dec"}  # after a figure these are a verb, the sea, a negation


def _lit(w: str) -> str:
    w = w.lower().replace("’", "'")
    w = re.sub(r"'s$", "", w)
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def _context(text: str, t: "Tok", end: int) -> None:
    """Fill t.sense and t.prev from the words around the figure (end: where the figure, or its range, ends)."""
    m = re.search(r"([^\W\d_][\w'’-]*)(\.?)\s*$", text[max(0, t.lo - 40): t.lo])
    p = m.group(1).lower() if m and (not m.group(2) or m.group(1).lower() in _ABBR_PREV) else None
    t.prev = _lit(p) if p and p not in _STOP else None
    if t.unit:
        t.sense = _UNIT_OF.get(t.unit)
        return
    if t.pct:
        t.sense = "pct"
        return
    if p and _UNITS.get(p) in PREFIX_SENSES:
        t.sense = _UNITS[p]
        return
    lit = text[t.s: t.e]
    if not t.money and re.fullmatch(r"(?:19|20)\d\d", lit):
        t.sense = "yr"
        return
    seg = text[end: end + 80]
    b = _BOUND.search(seg)
    words = [w.lower() for w in re.findall(r"[^\W\d_][\w'’]*", seg[: b.start()] if b else seg) if w.lower() not in _STOP][:3]
    for w in words:
        u = None if w in _NEXT_SKIP else (_UNITS.get(w) or _UNITS.get(_lit(w)))
        if u and u not in ("phase", "ref"):  # "15 de julio", "5-1 vote"; but "phase" and "number" only name what follows them
            t.sense = u
            return
    t.sense = _lit(words[0]) if words else None


def _ident_pair(text: str, a: "Tok", b: "Tok") -> bool:
    """'2025-00058', '32-00457A', 'Resolution 29-2026': a hyphenated identifier, not a range."""
    if text[a.e: b.s] != "-" or a.money or b.money:
        return False
    x, y = text[a.s: a.e], text[b.s: b.e]
    tail = text[b.e: b.e + 1]
    return y.startswith("0") or bool(re.match(r"[A-Za-z]", tail)) or (len(x) == 4 and x[:2] in ("19", "20") and len(y) >= 3 and not (len(y) == 4 and y[:2] in ("19", "20"))) \
        or (len(x) <= 3 and len(y) == 4 and y[:2] in ("19", "20"))


def _tokens(text: str, lang: str = "en") -> list[Tok]:
    """Every figure in text with its context, ranges ("$83–147 million", "entre 0,02 y 0,10 dólares") resolved so both
    ends carry the dollar sign and the scale; identifiers ("2025-00058") kept whole; each with what it counts."""
    toks = _raw_tokens(text, lang)
    out: list[Tok] = []
    skip = set()
    for i, t in enumerate(toks):
        if i in skip:
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if nxt is not None and not t.word and not nxt.word and _ident_pair(text, t, nxt):
            m = re.match(r"[\w-]*", text[nxt.s:])
            t.ident = text[t.s: nxt.s + m.end()]
            t.e = t.hi = nxt.s + m.end()
            t.rng, t.vals = None, []
            skip.add(i + 1)
            if nxt.rng is not None and nxt.rng is not t:
                nxt.rng.rng = None
        out.append(t)
    for t in out:
        if t.ident:
            continue
        end = max(t.hi, t.rng.hi) if t.rng is not None else t.hi
        _context(text, t, end)
        if t.rng is not None and t.rng.s < t.s:  # the far end of a range counts what the range counts
            t.sense = t.rng.sense if t.rng.sense is not None else t.sense
    return out


def _raw_tokens(text: str, lang: str = "en") -> list[Tok]:
    toks: list[Tok] = []
    for m in _NUM.finditer(text):
        s, e = m.start(), m.end()
        t = Tok(s=s, e=e, vals=_vals(text[s:e], lang), lo=s, hi=e)
        before = text[max(0, s - 4): s]
        db = _DOLLAR_BEFORE.search(before)
        if db and not re.search(r"[A-Za-z]-?$", before):
            t.money, t.lo = True, s - len(db.group(0))
        rest = text[e:]
        sm = _SCALE.match(rest)
        if sm:
            t.mult = SCALE_MULT.get(re.sub(r"\s+", " ", sm.group(1).lower()), 1.0)
            t.hi = e + sm.end()
            rest = text[t.hi:]
        cm = _CURRENCY.match(rest)
        if cm:
            t.money = True
            t.hi += cm.end()
            rest = text[t.hi:]
        pm = _PCT.match(rest)
        um = _UNIT.match(rest)
        if pm:
            t.pct, t.hi = True, t.hi + pm.end()
        elif um and not t.money:
            t.unit, t.hi = um.group(1), t.hi + um.end()
        toks.append(t)
    for a, b in zip(toks, toks[1:]):
        gap = text[a.hi: b.lo]
        if not (_RANGE.match(gap) or (_RANGE_AND.match(gap) and _BETWEEN.search(text[: a.lo]))):
            continue
        a.rng, b.rng = b, a
        if a.money or b.money:
            a.money = b.money = True
        if a.mult == 1.0 and b.mult != 1.0 and a.hi == a.e:
            a.mult = b.mult
        if b.unit and not a.unit:
            a.unit = b.unit
    for m in _WORDNUM[lang].finditer(text):
        if lang == "en":
            v = _TENS_EN[m.group(1).lower()] + (_ONES_EN[m.group(2).lower()] if m.group(2) else 0) if m.group(1) else _WORDS_EN[m.group(3).lower()]
        else:
            v = _WORDS_ES[m.group(1).lower()]
        toks.append(Tok(s=m.start(), e=m.end(), vals=[float(v)], lo=m.start(), hi=m.end(), word=True))
    toks.sort(key=lambda t: t.s)
    return toks


def _cands(t: Tok) -> list:
    """The values a figure may stand for: as printed, times its scale, and GW in MW."""
    out = []
    for v in t.vals:
        out.append(v * t.mult)
        if t.mult != 1.0 and not t.money:
            out.append(v)
        if t.unit == "GW":
            out.append(v * 1000)
    return out


def _index(f: Fact) -> None:
    """Every printed form of every number this fact carries (the numbers in its text, and its computed values), each with
    the senses it is used in and the word before it; its dollar amounts with their sense; its identifiers."""
    senses: dict = {}
    prevs: dict = {}
    cash: list = []
    idents: set = set()
    seen: list = []
    for t in _tokens(f.text, "en"):
        if t.ident:
            idents.add(t.ident.lower())
            continue
        for v in _cands(t):
            for form in _forms(v):
                senses.setdefault(form, set()).add(t.sense)
                prevs.setdefault(form, set()).add(t.prev)
            seen.append((v, t.sense))
        if t.money:
            cash += [(v * t.mult, t.sense) for v in t.vals]
    for v in f.values:  # a computed value takes the sense of the printed number it rounds to ("11,694" -> "about 11,700 people")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            s = next((ts for x, ts in seen if abs(x - float(v)) <= ROUND_TOL * max(abs(float(v)), 1e-9)), None)
            for form in _forms(float(v)):
                senses.setdefault(form, set()).add(s)
    for v in f.money:
        s = next((cs for x, cs in cash if abs(x - float(v)) <= MONEY_TOL * max(abs(float(v)), 1e-9)), None)
        cash.append((float(v), s))
    f.senses, f.prevs, f.cash, f.idents = senses, prevs, cash, idents


def _close(v: float, x: float) -> bool:
    return abs(v - x) <= max(MONEY_TOL * abs(x), 0.005)


def _has(f: Fact, t: Tok, cands: list, sense=None, prev=None, loose=False):
    """The value of t that fact f carries: in the same sense or after the same word (or, loose, in any sense)."""
    for v in cands:
        if t.money:
            for x, s in f.cash:
                if _close(v, x) and (loose or (sense is not None and s == sense)):
                    return v
        else:
            form = _canon(v)
            if form not in f.senses:
                continue
            if loose or (sense is not None and sense in f.senses[form]) or (prev is not None and prev in f.prevs.get(form, ())):
                return v
    return None


def _match(t: Tok, facts: list[Fact]) -> tuple[Fact | None, float | None]:
    """(the fact this figure is, the value it matched). Facts are ordered reported, model, estimates, questions, rules,
    civic. First the same number in the same sense ("4 hours" is the model's outage, never a report's "denial 4-2"; "1,200
    MW" is the reported size). A figure whose sense is not a known unit may then be any of the model's or the estimates'
    numbers, and only then anything else; 0 and 1 never match on their value alone."""
    if t.ident:
        k = t.ident.lower()
        return next(((f, None) for f in facts if k in f.idents), (None, None))
    cands = [v * t.mult for v in t.vals] if t.money else _cands(t)
    if t.sense is not None or t.prev is not None:
        for f in facts:
            v = _has(f, t, cands, sense=t.sense, prev=t.prev)
            if v is not None:
                return f, v
    if not t.money and all(abs(v) in (0.0, 1.0) for v in t.vals):
        return None, None
    unknown = t.sense not in KNOWN_SENSES
    if t.money or unknown:
        for f in facts:
            if f.kind in ("model", "estimate"):
                v = _has(f, t, cands, loose=True)
                if v is not None:
                    return f, v
    if unknown:
        for f in facts:
            v = _has(f, t, cands, loose=True)
            if v is not None:
                return f, v
    return None, None


# ------------------------------------------------------------------------------------ the checker
# Always refused (the phrases the rules name, questions included):
_FORBID_ALWAYS = {
    "en": re.compile(
        r"\bwill\s+cause\w*|\bcaused\b|\bcauses\b|\bresponsible\s+for\b|\bto\s+blame\b|\bblam(?:e|ed|es|ing)\b|\b(?:is|are)\s+going\s+to\s+(?:black|go\s+dark|cause|knock)\w*|\bwill\s+(?:black|go\s+dark)\w*"
        r"|\b(?:led|leads|leading)\s+to\s+(?:\w+\s+){0,2}?(?:blackouts?|outages?|failures?|higher\s+(?:bills?|rates?))|\bresult(?:ed|s)?\s+in\s+(?:\w+\s+){0,2}?(?:blackouts?|outages?)"
        r"|\b(?:plans?|planning|intends?|intending|wants?|wanting|aims?|trying|tries)\s+to\s+(?:raise|increase|hike|charge|pass\s+(?:on|along)|overload|black)"
        r"|\b(?:will|would|is\s+going\s+to|are\s+going\s+to)\s+make\s+(?:us|me|residents|customers|families|households|everyone|ratepayers|taxpayers)\s+pay"
        r"|\b(?:bills?|rates?)\s+(?:are|is|will\s+be)\s+(?:going\s+up|rising|increasing|climbing)\s+because",
        re.I,
    ),
    "es": re.compile(
        r"\bcausar[áa]n?\b|\bcaus[óo]\b|\bprovocar[áa]n?\b|\bprovoc[óo]\b|\bresponsables?\s+del?\b|\bculpa\w*|\bdejar[áa]n?\s+(?:a\s+)?(?:[\w.,]+\s+){0,3}?sin\s+(?:luz|electricidad|energ[ií]a|servicio)"
        r"|\bva(?:n)?\s+a\s+(?:causar|provocar|apagar|dejar\s+sin)\w*"
        r"|\b(?:causa|causan|provoca|provocan|genera|generan|produce|producen)\s+(?:\w+\s+){0,2}?(?:apagones|cortes|un\s+apag[oó]n)"
        r"|\b(?:llev[óo]|llevaron|result[óo]|resultaron)\s+(?:a|en)\s+(?:\w+\s+){0,2}?(?:apagones|cortes)"
        r"|\b(?:planea|planean|quiere|quieren|pretende|pretenden|busca|buscan|intenta|intentan)\s+(?:subir|aumentar|cobrar|sobrecargar)"
        r"|\bnos\s+(?:har[áa]n?|va\s+a\s+hacer|van\s+a\s+hacer)\s+pagar|\bhar[áa]n?\s+que\s+(?:se\s+vaya\s+la\s+luz|suban|aumenten|falle)",
        re.I,
    ),
}
# Refused outside a question: saying what the real project or utility will do ("Will it raise my bill?" is a question)
_FORBID_ASSERT = {
    "en": re.compile(
        r"\b(?:will|would|is going to|are going to)\s+(?:\w+\s+){0,2}?(?:cause|black|trigger|overload|raise|increase|leave|cut|fail|drain|harm|hurt|destroy|pollute|poison|strain|damage|endanger|threaten|burden|crash|knock)\w*"
        r"|\b(?:is|are|would\s+be|will\s+be)\s+(?:\w+\s+)?(?:dangerous|unsafe|harmful|toxic|reckless|a\s+(?:danger|threat|hazard|disaster|menace))\b", re.I),
    "es": re.compile(
        r"\bva(?:n)?\s+a\s+(?:subir|colapsar|da[ñn]ar|contaminar|sobrecargar|dejar|aumentar)\w*|\bsubir[áa]n?\b|\bapagar[áa]n?\b|\bcolapsar[áa]n?\b|\bsobrecargar[áa]n?\b|\bcontaminar[áa]n?\b|\bda[ñn]ar[áa]n?\b"
        r"|\b(?:provocar|causar|subir|apagar|colapsar|sobrecargar|contaminar|da[ñn]ar)[ií]a(?:n)?\b|\bdejar[ií]a(?:n)?\s+sin\b"
        r"|\b(?:es|son|ser[ií]a|ser[aá]n?)\s+(?:\w+\s+)?(?:peligros[oa]s?|insegur[oa]s?|da[ñn]in[oa]s?|t[oó]xic[oa]s?|una\s+amenaza|un\s+peligro|un\s+desastre)\b",
        re.I,
    ),
}
_INSULT = re.compile(
    r"\b(greed\w*|liars?|lying|lied|corrupt\w*|fraud\w*|scam\w*|illegal\w*|unlawful\w*|criminal\w*|crook\w*|guilty|negligen\w*|reckless\w*|shady|incompeten\w*|stupid\w*|idiot\w*"
    r"|brib\w*|kickback\w*|sham|rigged|cover-?up|backroom|disgrace\w*|shameful|evil|steal\w*|stole|theft|rip-?off|sell-?out|sold\s+(?:us\s+)?out"
    r"|mislead\w*|misled|deceiv\w*|decept\w*|deceit\w*|dishonest\w*|profiteer\w*|price[\s-]+goug\w*|goug\w*|swindl\w*|con\s+job|abus\w*|exploit\w*"
    r"|stands?\s+to\s+(?:profit|gain|cash\s+in)|line\s+(?:their|its|his|her)\s+pockets|cash(?:ing)?\s+in\s+on"
    r"|codici\w*|mentir\w*|mentiros\w*|minti[óo]|mintieron|mienten?|enga[ñn]\w*|corrup\w*|fraude\w*|estafa\w*|ilegal\w*|delincuen\w*|culpable\w*|irresponsable\w*|est[úu]pid\w*|idiota\w*"
    r"|soborn\w*|robar\w*|rob[óo]|vergonzos\w*|verg[üu]enza|abus\w*|explot\w*|lucrarse|se\s+lucra\w*|deshonest\w*)\b",
    re.I,
)
# People and addresses the comment may not invent: a title with a name ("Mayor John Smith", "el alcalde Juan Pérez"), a
# relative or neighbor with a name, a common first name with a surname, a street. Allowed only when the page's facts carry it.
_CAP = r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ'’-]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ'’-]+)?)"
_TITLE_WORDS = {"chair", "chairman", "chairwoman", "chairperson", "mayor", "president", "commissioner", "commissioners", "members", "member", "board", "commission",
                "council", "presidente", "presidenta", "comisionados", "miembros", "junta", "comisión", "concejo", "and", "y", "vice", "pro", "tem"}
_TITLE_NAME = re.compile(
    r"(?i:\b(?:mayor|vice[- ]mayor|chair(?:man|woman|person)?|commissioner|council\s*(?:man|woman|member)|council\s+member|supervisor|senator|representative|rep\.|sen\.|judge|governor|gov\.|"
    r"dr\.|mr\.|mrs\.|ms\.|miss|sheriff|clerk|director|secretary|alcalde(?:sa)?|comisionad[oa]|concejal(?:a)?|se[ñn]or(?:a|ita)?|sr\.|sra\.|srta\.|gobernador(?:a)?|senador(?:a)?|juez(?:a)?|doctor(?:a)?))"
    r"\s+" + _CAP)
_RELATION_NAME = re.compile(
    r"(?i:\b(?:neighbou?rs?|friends?|wife|husband|son|daughter|mother|father|mom|dad|grandmother|grandfather|grandma|grandpa|sister|brother|cousin|aunt|uncle|"
    r"vecin[oa]s?|amig[oa]s?|espos[oa]|hij[oa]s?|madre|padre|mam[aá]|pap[aá]|abuel[oa]s?|herman[oa]s?|prim[oa]s?|t[ií][oa]s?))\s*,?\s+" + _CAP)
_FIRST_NAMES = set(
    "james john robert michael william david richard joseph thomas charles christopher daniel matthew anthony mark donald steven paul andrew joshua kenneth kevin brian "
    "george timothy ronald edward jason jeffrey ryan jacob gary nicholas eric jonathan stephen larry justin scott brandon benjamin samuel gregory frank alexander raymond "
    "patrick jack dennis jerry tyler aaron jose adam henry nathan douglas zachary peter kyle walter ethan jeremy harold keith christian roger noah gerald carl terry sean "
    "mary patricia jennifer linda elizabeth barbara susan jessica sarah karen nancy lisa betty margaret sandra ashley kimberly emily donna michelle dorothy carol amanda "
    "melissa deborah stephanie rebecca sharon laura cynthia kathleen amy shirley angela helen anna brenda pamela nicole emma samantha katherine christine debra rachel "
    "catherine carolyn janet ruth maria heather diane virginia julie joyce victoria olivia kelly christina lauren joan evelyn judith megan cheryl andrea hannah martha "
    "jacqueline frances gloria ann jane joe bob bill jim tom mike dave steve sue kate beth "
    "juan josé jose luis carlos jorge pedro miguel antonio manuel francisco javier alejandro fernando ricardo roberto rafael sergio eduardo andrés andres diego raúl raul "
    "maría ana carmen rosa lucía lucia isabel elena laura marta teresa pilar dolores guadalupe gabriela mariana daniela sofía sofia valentina camila paula".split()
)
_FIRST_LAST = re.compile(r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ'’-]+)")
_STREET = re.compile(
    r"\b(?:[A-Z][a-z]+\s+){1,2}(?:Street|St\.|Avenue|Ave\.?|Road|Rd\.?|Drive|Lane|Ln\.?|Boulevard|Blvd\.?|Court|Ct\.?|Parkway|Pkwy\.?|Highway|Hwy\.?|Circle|Terrace|Trail)(?![\w])"
    r"|\b(?:[Cc]alle|[Aa]venida|[Cc]amino|[Cc]arretera)\s+[A-ZÁÉÍÓÚÑ0-9][\wáéíóúñ-]*")
# Claims about the real grid: what a campus does to "our grid" (never said, model or not), and any sentence about lines
# going over their limits, exceeding, overloading or capacity (said only of the model, with the model word in it)
_REAL_GRID = {
    "en": re.compile(
        r"\bour\s+(?:(?:local|own|regional|power|electric(?:al)?|energy|community's)\s+)*(?:grid|infrastructure|power\s+system|electric(?:al)?\s+system|power\s+lines|substations?)\b"
        r"|\b(?:the\s+)?(?:real|actual|local|existing|county's|city's|state's|area's|region's|florida's)\s+(?:power\s+|electric(?:al)?\s+)?(?:grid|power\s+system)\b", re.I),
    "es": re.compile(
        r"\bnuestr[ao]s?\s+(?:propi[ao]s?\s+)?(?:red|infraestructura|sistema\s+el[eé]ctrico|l[ií]neas|subestaci\w+)"
        r"|\bla\s+red\s+(?:el[eé]ctrica\s+)?(?:real|local|actual|existente)\b|\bla\s+red\s+(?:el[eé]ctrica\s+)?(?:de\s+(?:nuestr\w+|la\s+ciudad|el\s+condado|la\s+regi[oó]n|Florida))", re.I),
}
# A plain disclaimer is not a claim about the real grid: "on the model, not the real grid", "not a prediction about the local grid",
# "no en la red real". The words just before the mention must be a negation (and a few filler words), and the sentence must
# carry no claim or harm word of its own (checked where it is used).
_NEGATED_BEFORE = {
    "en": re.compile(
        r"(?:\bnot|\bnor|n['’]t|\brather\s+than|\binstead\s+of)\s+(?:(?:a|an|the|any|about|of|for|on|to)\s+){0,3}"
        r"(?:(?:prediction|forecast|claim|statement|picture|study|test|result)s?\s+(?:about|of|for|on)\s+)?$", re.I),
    "es": re.compile(
        r"(?:\bno\s+(?:(?:es|son|se\s+trata\s+de|de|una?|la|el)\s+){0,3}|\by\s+no\s+(?:de\s+)?|\ben\s+(?:lugar|vez)\s+de\s+)"
        r"(?:(?:predicci[oó]n|pron[oó]stico|afirmaci[oó]n|prueba)\s+(?:sobre|de|acerca\s+de)\s+)?$", re.I),
}
_GRID_CLAIM = {
    "en": re.compile(
        r"\bover\s+(?:its|their|the|a)\s+(?:\w+\s+)?limits?\b|\bexceed\w*|\boverload\w*|\bover\s?capacity|\bcan(?:not|'t|’t)?\s+(?:\w+\s+)?(?:handle|withstand|cope)"
        r"|\b(?:grid|line|lines|transmission|system|substation|transformer|electric(?:al)?|power)\s+(?:\w+\s+)?capacity\b"
        r"|\bcapacity\s+(?:of|on|in)\s+(?:the|our|this|its)\s+(?:\w+\s+)?(?:grid|lines?|system|substation|area|region)\b"
        r"|\b(?:no|not\s+enough|lack\s+of|without|beyond|above|past)\s+(?:the\s+)?(?:\w+\s+)?capacity\b"
        r"|\bcould(?:n't|n’t|\s+not)?\s+(?:\w+\s+)?(?:handle|withstand|cope)|\bmax(?:ed)?\s+out|\bpush(?:es|ed|ing)?\s+(?:\w+\s+){0,3}?past\b", re.I),
    "es": re.compile(
        r"\bsuper\w*\s+(?:su|sus|el|los)\s+l[ií]mites?|\bl[ií]mites?\s+de\s+(?:la|nuestra)\s+red|\bexced\w*|\bsobrecarg\w*"
        r"|\bcapacidad\s+(?:de\s+)?(?:la|nuestra)\s+red|\bcapacidad\s+(?:el[eé]ctrica|de\s+transmisi[oó]n|de\s+las\s+l[ií]neas)"
        r"|\b(?:sin|no\s+hay|falta\s+de|m[aá]s\s+all[aá]\s+de\s+(?:la|su))\s+(?:la\s+)?capacidad"
        r"|\b(?:no\s+)?(?:puede|podr[ií]a|pueden)\s+(?:\w+\s+)?(?:soportar|aguantar|manejar)", re.I),
}
_HARM = {  # with "our grid" in the same sentence: a claim about the real grid
    "en": re.compile(r"\bthreaten\w*|\bendanger\w*|\bdestabiliz\w*|\bstrain\w*|\bstress\w*|\bharm\w*|\bhurt\w*|\bdamag\w*|\bburden\w*|\boverwhelm\w*|\bhandle\b|\brisk\w*|\bdanger\w*", re.I),
    "es": re.compile(r"\bamenaz\w*|\bpone\s+en\s+riesgo|\briesgo\w*|\bdesestabiliz\w*|\bda[ñn]\w*|\bperjudic\w*|\bsatur\w*|\bestr[eé]s|\bsoport\w*|\baguant\w*|\bpeligr\w*", re.I),
}
# In a sentence with a number from the model: never the developer or the utility as the one who does it or needs it (the
# model is neither): "the developer could leave 11,700 in the dark", "the utility would need $83 million". "Paid by
# households instead of the developer" is fine.
_PARTY = {
    "en": re.compile(
        r"\b(?:developers?|applicants?|owners?|builders?|utility|utilities|power\s+company|electric\s+company)(?:'s)?\s+(?:\w+\s+){0,2}?"
        r"(?:could|would|will|may|might|can|leaves?|cuts?|blacks?|knocks?|causes?|puts?|push(?:es)?|darkens?|shuts?|takes?|overloads?|needs?|plans?|wants?|lines|grid|system)\b", re.I),
    "es": re.compile(
        r"\b(?:promotor(?:es|a)?|desarrollador(?:es|a)?|solicitante|due[ñn]o|propietari[oa]|empresa\s+el[eé]ctrica|compa[ñn][ií]a\s+el[eé]ctrica)\s+(?:\w+\s+){0,2}?"
        r"(?:podr[ií]an?|dejar\w*|deja|dejan|cortar\w*|corta|apagar\w*|causar\w*|provoca\w*|sobrecarg\w*|necesit\w*|tendr[ií]an?|planea\w*|quiere\w*)\b", re.I),
}
# a Spanish comment in English (names aside): "9 líneas y 2 transformers"
_ENGLISH_IN_ES = re.compile(r"\b(?:transformers?|lines|people|households|hours|blackouts?|outages?|upgrades?|grid|utility|the|and|with|which|would|should|model)\b")
_CONTACT = re.compile(r"https?://|www\.|[\w.+-]+@[\w-]+\.[\w.]+|\(\d{3}\)\s*\d{3}-\d{4}|\b\d{3}-\d{3}-\d{4}\b|P\.?\s?O\.?\s+Box", re.I)
# companies, tenants and utilities a comment might name; any not in the page's own sourced facts is refused
KNOWN_NAMES = [
    "Meta", "Facebook", "Google", "Alphabet", "Microsoft", "Amazon", "AWS", "OpenAI", "Oracle", "Nvidia", "NVIDIA", "xAI", "Apple", "CoreWeave", "Anthropic",
    "Stargate", "SoftBank", "Tesla", "IBM", "QTS", "Equinix", "Digital Realty", "Blackstone", "Crusoe", "CyrusOne", "Iron Mountain", "EdgeCore",
    "Nebius", "Fluidstack", "TeraWulf", "IREN", "Applied Digital", "Talen Energy", "Calpine", "AMD", "Poolside", "Tallgrass", "Riot Platforms",
    "FPL", "Florida Power & Light", "Florida Power and Light", "NextEra", "Duke Energy", "TECO", "Tampa Electric", "JEA", "Seminole Electric",
    "Lakeland Electric", "OUC", "Orlando Utilities", "FMPA", "Gulf Power", "Dominion", "Georgia Power", "Southern Company", "Entergy", "Xcel",
    "AEP", "PG&E", "ERCOT", "Oncor", "CenterPoint", "TVA", "PJM", "MISO", "CAISO", "Constellation", "Vistra",
]
COMMON_WORDS = {"Switch", "Tract", "Lambda", "Prime"}  # company names that are also everyday words: not matched alone
_CORP = re.compile(r"\b((?:[A-Z][\w&'.-]*\s+){1,4})(LLC|L\.L\.C\.|Inc\.?|Corp\.?|Corporation|Ltd\.?|LP|L\.P\.|Holdings)(?![\w])")
_known_lock = threading.Lock()
_known: dict = {"mtime": None, "rx": None}
_ABBR = re.compile(r"(?:\b(?:Aug|Sept?|Oct|Nov|Dec|Jan|Feb|Mar|Apr|Jun|Jul|No|St|Dr|Mr|Mrs|Ms|Sr|Sra|Jr|vs|approx|etc|Co|Corp|Inc)\.|\b(?:[A-Z]\.){1,3})$")


def _known_names() -> list:
    """KNOWN_NAMES plus the company each catalog proposal reports (its leading name), so one page never borrows another's."""
    cat = catalog_mod.catalog()
    with _known_lock:
        if _known["mtime"] == cat["mtime"]:
            return _known["rx"]
    names = set(KNOWN_NAMES)
    for e in cat["entries"].values():
        c = (e.get("company") or "").strip()
        if not c or re.match(r"developer not confirmed", c, re.I):
            continue
        for piece in c.split(";"):
            lead = re.split(r"\s*\(|\s+with\s+|\s+and\s+|\s+for\s+|,|/", piece.strip())[0].strip()
            if len(lead) >= 3 and lead[0].isupper() and lead not in COMMON_WORDS and lead.lower() not in GENERIC:
                names.add(lead)
    out = sorted(names, key=len, reverse=True)
    with _known_lock:
        _known.update(mtime=cat["mtime"], rx=out)
    return out


def _sentences(text: str) -> list[tuple[int, int, int]]:
    """(start, end, paragraph) of every sentence. A sentence ends at . ! or ? followed by a space or the end
    ("$0.10" and "1.2 GW" don't end one; "Aug." and "St." don't either)."""
    out = []
    pos = 0
    for pi, para in enumerate(text.split("\n\n")):
        pieces, last = [], 0
        for m in re.finditer(r"[.!?]+[\"”’)]*(?=\s|$)", para):
            pieces.append([last, m.end()])
            last = m.end()
        if last < len(para):
            pieces.append([last, len(para)])
        merged: list = []
        for p in pieces:
            if merged and _ABBR.search(para[merged[-1][0]: merged[-1][1]].rstrip()):
                merged[-1][1] = p[1]
            else:
                merged.append(p)
        for s, e in merged:
            seg = para[s:e]
            if seg.strip():
                lead = len(seg) - len(seg.lstrip())
                out.append((pos + s + lead, pos + e, pi))
        pos += len(para) + 2
    return out


def _words(text: str) -> int:
    return len(re.findall(r"[\w$\[¿¡«][\w'’.,$%–\-\]»?]*", text))


CASE_SENSITIVE_NAMES = ("Meta", "Oracle", "Apple", "Amazon", "Dominion", "Constellation")


def _name_rx(n: str, case_sensitive: bool = False) -> re.Pattern:
    return re.compile(r"(?<![\w&])" + re.escape(n) + r"(?![\w&])", 0 if case_sensitive else re.I)


def _real_names_in(seg: str, sh: Sheet) -> list:
    """Every company, developer or utility name in seg: the catalog's and the known ones, and this page's own."""
    hits = [n for n in _known_names() if _name_rx(n, n in CASE_SENSITIVE_NAMES).search(seg)]
    hits += [w for w, cs in sh.real_words if _name_rx(w, cs).search(seg)]
    return hits


def _sourced(phrase: str, sh: Sheet) -> bool:
    """Whether the page's facts carry this name as a whole phrase ("aws" is not carried by "laws")."""
    p = " ".join(phrase.lower().split())
    return bool(p) and re.search(r"(?<![\w&])" + re.escape(p) + r"(?![\w&])", sh.names_ok) is not None


def _people_invented(text: str, sh: Sheet) -> list:
    """Names of people and addresses the comment made up (a sourced one, in the page's facts, is fine)."""
    out = []
    for m in _TITLE_NAME.finditer(text):
        name = m.group(1)
        if name.split()[0].lower() in _TITLE_WORDS or _sourced(name, sh):
            continue
        out.append(m.group(0))
    for m in _RELATION_NAME.finditer(text):
        if m.group(1).split()[0].lower() in _TITLE_WORDS or _sourced(m.group(1), sh):
            continue
        out.append(m.group(0))
    for m in _FIRST_LAST.finditer(text):
        if m.group(1).lower() in _FIRST_NAMES and not _sourced(m.group(0), sh) and m.group(2).lower() not in _TITLE_WORDS:
            out.append(m.group(0))
    for m in _STREET.finditer(text):
        if not _sourced(m.group(0), sh):
            out.append(m.group(0))
    return out


def _without_names(text: str, sh: Sheet) -> str:
    """The comment with its quoted titles and the proper names it keeps as given set aside."""
    t = re.sub(r"«[^»]*»|\"[^\"]*\"|“[^”]*”", " ", text)
    for p in sh.protected:
        t = re.sub(re.escape(p), " ", t, flags=re.I)
    return t


def _english_in_spanish(text: str, sh: Sheet):
    """An English word in a Spanish comment, once the proper names it keeps in English are set aside."""
    return _ENGLISH_IN_ES.search(_without_names(text, sh))


# "2,031 MW" in a Spanish comment reads as about 2 MW: Spanish writes 2.031 MW and 9,19 dólares (as the plain version does)
_EN_THOUSANDS = re.compile(r"(?<![\w.,])\d{1,3}(?:,\d{3})+(?![\d,])")


def check(text: str, sh: Sheet, body: CommentIn) -> tuple[bool, list, list, list]:
    """(ok, findings, numbers, checks). findings: what failed, with the words that failed (for the rewrite prompt and the
    log; never sent to the page). numbers: [{text, at, value, kind, ok, fact, fact_text, fact_kind, source, section}].
    checks: [{id, ok}]."""
    lang = body.lang
    fails: list[tuple[str, str]] = []  # (check id, finding)
    if not text or not text.strip():
        return False, ["the comment is empty"], [], []
    sents = _sentences(text)
    seg_of = [text[s:e] for s, e, _ in sents]
    is_q = [sg.rstrip().endswith("?") for sg in seg_of]

    def sent_of(pos):
        for i, (s, e, _) in enumerate(sents):
            if s <= pos < e:
                return i
        return None

    def quote(i):
        return seg_of[i].strip()[:110]

    # 1. numbers: every figure is one of the facts' figures, in the same sense
    nums: list[dict] = []
    for t in _tokens(text, lang):
        if t.rng is not None and t.rng.s < t.s:
            continue  # the far end of a range: checked with its start
        group = [t] + ([t.rng] if t.rng is not None else [])
        matched = [_match(g, sh.facts) for g in group]
        ok = all(f is not None for f, _ in matched)
        lo, hi = min(g.lo for g in group), max(g.hi for g in group)
        shown = text[lo:hi]
        if not ok:
            g = group[[f is None for f, _ in matched].index(True)]
            what = "identifier" if g.ident else ("dollar amount" if g.money else "number")
            fails.append(("numbers", f"number not in the fact sheet: {shown!r} ({what} {text[g.s:g.e]}"
                                     + (f" counting '{g.sense}'" if g.sense and not g.money else "") + " is not one of the facts' numbers in that sense)"))
        f0 = next((f for f, _ in matched if f is not None), None)
        vals = [v if v is not None else (g.vals[0] * g.mult if g.vals else None) for g, (_, v) in zip(group, matched)]
        nums.append({
            "text": shown, "at": [lo, hi], "value": (t.ident if t.ident else vals[0]) if len(vals) == 1 else vals,
            "kind": "id" if t.ident else ("money" if t.money else ("percent" if t.pct else "number")), "ok": ok,
            "fact": f0.key if f0 else None, "fact_text": f0.text[:320] if f0 else None, "fact_kind": f0.kind if f0 else None,
            "source": f0.source if f0 else None, "section": f0.section if f0 else None,
        })
    scale_bad = next((sm.group(0) for sm in _SCALE_WORDS[lang].finditer(text) if not text[: sm.start()].rstrip()[-1:].isdigit()), None)
    if scale_bad:
        fails.append(("numbers", f"'{scale_bad}' is used without a figure from the facts"))

    # 2. what the real project or utility will do or did; accusations; contact details
    pred = _FORBID_ALWAYS[lang].search(text) or (_FORBID_ALWAYS["en"].search(text) if lang == "es" else None)
    if not pred:
        for i, sg in enumerate(seg_of):
            if is_q[i]:
                continue
            pred = _FORBID_ASSERT[lang].search(sg) or (_FORBID_ASSERT["en"].search(sg) if lang == "es" else None)
            if pred:
                break
    if pred:
        fails.append(("no_prediction", f"says what the real project or utility will do or did: {pred.group(0)!r} (ask it as a question, or give what the model showed)"))
    im = _INSULT.search(text)
    if im:
        fails.append(("respectful", f"insulting or accusing word: {im.group(0)!r}"))
    cm = _CONTACT.search(text)
    if cm:
        fails.append(("respectful", f"contains a link, email, phone number or address: {cm.group(0)!r}"))

    # 3. clean text in the chosen language
    om = _ODD.search(text)
    if om:
        fails.append(("language", f"contains a stray character {om.group(0)!r} (U+{ord(om.group(0)):04X})"))
    gm = _GARBLED.search(text)
    if gm:
        at = max(0, gm.start() - 12)
        fails.append(("language", f"garbled characters near {text[at: gm.end() + 12]!r}: write accented letters and ¿ ¡ as themselves, never escaped"))
    if lang == "es":
        em = _english_in_spanish(text, sh)
        if em:
            fails.append(("language", f"an English word in the Spanish comment: {em.group(0)!r} (write it in Spanish; only proper names stay as they are)"))
        nm = _EN_THOUSANDS.search(_without_names(text, sh))
        if nm:
            fails.append(("language", f"writes {nm.group(0)!r} the English way, which a Spanish reader takes as a decimal: write {nm.group(0).replace(',', '.')} "
                                      "(a period for thousands, a comma for decimals)"))

    # 4. names: only those the page's sourced facts carry; no invented people or addresses
    name_bad = []
    for n in _known_names():
        if _name_rx(n, n in CASE_SENSITIVE_NAMES).search(text) and not _sourced(n, sh):
            name_bad.append(n)
    for m in _CORP.finditer(text):
        n = (m.group(1) + m.group(2)).strip()
        if not _sourced(n, sh):
            name_bad.append(n)
    if name_bad:
        fails.append(("names", f"names {', '.join(repr(n) for n in sorted(set(name_bad))[:3])}, not in the page's sourced facts"))
    invented = _people_invented(text, sh)
    if invented:
        fails.append(("names", f"names a person or an address that is not in the fact sheet: {invented[0]!r} (name no one; keep the bracketed placeholders)"))

    # 5. the model's results: framed as the open, synthetic model, the model word in every sentence that gives one, no real
    #    name in a sentence about the model, and never the real grid ("our grid") or a real party as what the model showed
    model_num = sorted({i for n in nums if n["ok"] and n["fact_kind"] == "model" for i in [sent_of(n["at"][0])] if i is not None})
    frame_i = next((i for i, sg in enumerate(seg_of) if _framed(sg, lang)), None)
    frame_fails = []
    if model_num:
        first = model_num[0]
        if frame_i is None:
            frame_fails.append(f"never says the grid results are on an open, synthetic grid model (write '{FRAME_LEAD[lang]}')")
        elif frame_i > first:
            frame_fails.append(f"gives a model result before saying it is on an open, synthetic grid model: {quote(first)!r}")
    for i in model_num:
        if not MODEL_WORD[lang].search(seg_of[i]):
            frame_fails.append(f"a model result is not framed as on the model: {quote(i)!r} (say '{_SYS_WORDS[lang]['on']}' in that same sentence)")
            break
    for i in model_num:
        pm = _PARTY[lang].search(seg_of[i])
        if pm:
            frame_fails.append(f"makes {pm.group(0)!r} the subject of a model result: {quote(i)!r} (the model is not the real developer or utility: say what the model showed)")
            break
    for i, sg in enumerate(seg_of):
        if is_q[i]:
            continue
        claim = _GRID_CLAIM[lang].search(sg)
        if claim and not MODEL_WORD[lang].search(sg):
            frame_fails.append(f"a claim about the grid ({claim.group(0)!r}) that is not framed as the model: {quote(i)!r} (only the model's lines go over their limits: say '{_SYS_WORDS[lang]['on']}')")
            break
    for i, sg in enumerate(seg_of):
        rg = _REAL_GRID[lang].search(sg)
        if not rg or is_q[i]:
            continue
        claim_word = _GRID_CLAIM[lang].search(sg) or _HARM[lang].search(sg)
        if not claim_word and _NEGATED_BEFORE[lang].search(sg[: rg.start()]):
            continue  # a plain disclaimer ("on the model, not the real grid"), the very framing the comment is asked to give
        if claim_word or MODEL_WORD[lang].search(sg) or i in model_num:
            frame_fails.append(f"treats the model's result as the real grid ({rg.group(0)!r}): {quote(i)!r} (Overload tested a model, not the real grid: never write '{rg.group(0)}' in a statement; ask it as a question, or say the model is not the real grid)")
            break
    for i, sg in enumerate(seg_of):
        if i in model_num or MODEL_WORD[lang].search(sg) or (_GRID_CLAIM[lang].search(sg) and not is_q[i]):
            hits = _real_names_in(sg, sh)
            if hits:
                frame_fails.append(f"names {hits[0]!r} in a sentence about the model: {quote(i)!r} (the model is not the real project, developer or utility)")
                break
    fails += [("framed", x) for x in frame_fails]

    # 6. where it stands: said, and no vote asked for on a proposal that is not before the body
    kind = sh.situation["kind"]
    if kind != "pending":
        if not SIT_SAID[kind].search(text):
            clause = SIT_CLAUSE[lang][sh.situation["status"] if kind == "built" else kind]
            fails.append(("situation", f"does not say where it stands, as reported (write: '{clause}')"))
        for i, sg in enumerate(seg_of):
            if _REQUEST[lang].search(sg) and _VOTEWORD.search(sg) and not _IFBACK.search(sg):
                want = ("about any expansion or a new campus like it" if kind == "built" else
                        "for if an application is filed" if kind == "not_filed" else "for if it, or a campus like it, comes back")
                fails.append(("situation", f"asks the body to act on it as if a vote were pending: {quote(i)!r} (no vote is pending: write the request {want})"))
                break

    # 7. the resident's own comment: addressed, their stance, their concerns, their length
    addressed = re.sub(r"\s+", " ", sh.addressee).lower() in re.sub(r"\s+", " ", text).lower()
    if not addressed:
        fails.append(("addressed", f"is not addressed to the {sh.addressee} by that exact name (copy it letter for letter, in English, even in a Spanish comment: no 'de', no translation)"
                      if lang == "es" else f"is not addressed to the {sh.addressee} by that exact name"))
    if not NAME_SLOT_RX[lang].search(text):
        fails.append(("yours", f"does not say '{_SYS_WORDS[lang]['greet'].split(' and ')[0].split(' y ')[0]}' with the placeholder for the resident to fill in"))
    if not STANCE_RX[body.stance].search(text):
        fails.append(("yours", {"questions": "asks no question", "support_conditions": "never says the resident supports it (with conditions)",
                                "oppose": "never says the resident opposes it as proposed"}[body.stance]))
    missing = [c for c in _ordered(body.concerns) if not CONCERN_RX[c].search(text)]
    if missing:
        fails.append(("yours", f"leaves out the resident's concern: {', '.join(CONCERN_TEXT['en'][c] for c in missing)}"))
    words = _words(text)
    target = body.minutes * WPM
    if words > target * LEN_HI:
        fails.append(("length", f"too long for {body.minutes} minute{'s' if body.minutes > 1 else ''} spoken ({words} words; aim for about {target}, never over {int(target * LEN_HI)})"))
    elif words < target * LEN_LO:
        fails.append(("length", f"too short for {body.minutes} minute{'s' if body.minutes > 1 else ''} spoken ({words} words; aim for about {target}, never under {int(target * LEN_LO)})"))

    failed = {c for c, _ in fails}
    ids = [c for c in CHECK_LABELS if c != "situation" or kind != "pending"]
    checks = [{"id": c, "ok": c not in failed} for c in ids]
    return not fails, [f for _, f in fails], nums, checks


CHECK_LABELS = {
    "numbers": "Every number is one of this page's facts",
    "no_prediction": "Nothing says what the real project or utility will do",
    "respectful": "No accusations, links or contact details",
    "names": "No company, utility or person the sources don't name",
    "framed": "Grid results framed as an open, synthetic model",
    "situation": "Says where the proposal stands, as reported",
    "language": "Clean text in the language you chose",
    "addressed": "Addressed to the decision body",
    "yours": "Your stance and your concerns",
    "length": "Fits the time you chose",
}
CHECK_CAUGHT = {  # what the checker caught in a draft it sent back, as the page says it (no quotes of the draft)
    "numbers": "a number that is not one of this page's facts",
    "no_prediction": "a claim about what the real project or utility would do",
    "respectful": "wording that was not respectful",
    "names": "a name the sources don't carry",
    "framed": "a grid result not framed as the synthetic model",
    "situation": "not saying where the proposal stands",
    "language": "garbled or mixed-language text",
    "addressed": "the wrong addressee",
    "yours": "your stance or a concern left out",
    "length": "the wrong length",
}


def _ledger(ok: bool, checks: list) -> None:
    """One line in the AI ledger (llm.note_check) per Gemini draft checked: the check that caught it, never its words."""
    bad = [c["id"] for c in checks if not c["ok"]]
    llm.note_check("comment", ok, "a public comment whose every number, name and claim traced to the page" if ok
                   else ("a public comment sent back: " + CHECK_CAUGHT.get(bad[0], bad[0])) if bad else "a public comment sent back")


# ------------------------------------------------------------------------------------ the plain version
def _n_es(x: float) -> str:
    return f"{math.floor(float(x) + 0.5):,}".replace(",", ".")


def _approx_n(n: float) -> float:
    n = float(n)
    if n < 100:
        return round(n, -1) if n >= 10 else n
    q = 10 ** max(int(math.floor(math.log10(n))) + 1 - 3, 0)
    return round(n / q) * q


def _dec_es(v: float, d: int) -> str:
    return f"{v:.{d}f}".replace(".", ",")


def _money_es(x: float) -> tuple[str, str]:
    """(figure, unit words) in Spanish: ('147', 'millones de dólares'), ('2,7', 'millones de dólares'), ('0,10', 'dólares')."""
    x = float(x)
    if x >= 999.5e6:
        v = x / 1e9
        return (_n_es(v) if v >= 10 else _dec_es(v, 1).replace(",0", "")), "mil millones de dólares"
    if x >= 1e6:
        v = x / 1e6
        return (_n_es(v) if v >= 10 else _dec_es(v, 1).replace(",0", "")), "millones de dólares"
    if x >= 100:
        return _n_es(round(x, -1) if x < 10_000 else round(x, -3)), "dólares"
    return _dec_es(x, 2), "dólares"


def _range_es(lo: float, hi: float) -> str:
    a, ua = _money_es(lo)
    b, ub = _money_es(hi)
    if lo < 0.005:
        return f"hasta {b} {ub}"
    if ua == ub:
        return f"entre {a} y {b} {ub}"
    return f"entre {a} {ua} y {b} {ub}"


def _qids(concerns) -> tuple:
    out: list = []
    for c in _ordered(concerns) or list(DEFAULT_CONCERNS):
        out += [q for q in CONCERN_QUESTIONS[c] if q not in out]
    for c in CONCERNS:  # every question on the page stays in the fact sheet (Gemini may use them), chosen ones first
        out += [q for q in CONCERN_QUESTIONS[c] if q not in out]
    return tuple(out)


def _low(s: str) -> str:
    """'A clear yes' -> 'a clear yes'; an acronym ('NERC ...') stays as it is."""
    return s if not s or re.match(r"[A-Z]{2,}\b", s) else s[:1].lower() + s[1:]


def _fit(paras: list[list[tuple[int, str]]], target: int) -> str:
    """Every priority-0 sentence, then optional ones by priority while the comment stays near the target length."""
    keep = {(pi, si) for pi, p in enumerate(paras) for si, (pr, _) in enumerate(p) if pr == 0}
    words = sum(_words(s) for pi, p in enumerate(paras) for si, (_, s) in enumerate(p) if (pi, si) in keep)
    optional = sorted(((pr, pi, si) for pi, p in enumerate(paras) for si, (pr, _) in enumerate(p) if pr > 0))
    for pr, pi, si in optional:
        w = _words(paras[pi][si][1])
        if words + w <= target * TEMPLATE_FILL:
            keep.add((pi, si))
            words += w
    out = []
    for pi, p in enumerate(paras):
        line = " ".join(s for si, (_, s) in enumerate(p) if (pi, si) in keep)
        if line:
            out.append(line)
    return "\n\n".join(out)


def template(sh: Sheet, body: CommentIn) -> str:
    """The plain comment from the same facts, fitted to the chosen length (it passes the same checks). Priority 0 is
    always said; the rest fills the time the resident chose, most useful first."""
    es = body.lang == "es"
    L = "es" if es else "en"
    e, sim, cost, mins = sh.entry, sh.sim, sh.cost or {}, body.minutes
    concerns = _ordered(body.concerns) or list(DEFAULT_CONCERNS)
    support = body.stance == "support_conditions"
    P: list[list[tuple[int, str]]] = []

    # who I am and where I stand (the size is said again in the model's result, so a one-minute comment skips it here)
    kind = sh.situation["kind"]
    clause = SIT_CLAUSE[L][sh.situation["status"] if kind == "built" else kind] if kind != "pending" else ""
    thing = ("el campus" if es else "campus") if kind == "built" else ("la propuesta" if es else "proposal")
    short1 = mins == 1 and kind != "pending"  # a one-minute comment says where it stands in fewer words
    grp = kind if kind in ("not_filed", "built") else "back"
    if es:
        greet = f"Estimados miembros de la {sh.addressee}:" if sh.researched else f"Al organismo que decide esta propuesta en {sh.addressee}:"
        about = f"Vengo a hablar de {thing} {e['name']}" + (f", con un tamaño reportado de {_size_words(e, True)}." if e.get("mw") and mins > 1 else ".")
        stance = {
            "pending": {"questions": "Tengo preguntas que deben responderse en público antes de decidir.",
                        "support_conditions": "Puedo apoyar este proyecto si mis condiciones quedan por escrito en la aprobación.",
                        "oppose": "Me opongo a esta propuesta tal como está."},
            "not_filed": {"questions": f"{clause}, así que pregunto ahora: estas preguntas deben responderse en público antes de que se considere cualquier solicitud.",
                          "support_conditions": f"{clause}; podría apoyar un campus como este si mis condiciones quedan por escrito en cualquier aprobación.",
                          "oppose": f"{clause}; si se presenta una solicitud tal como se anunció, me opondría."},
            "built": {"questions": f"{clause}, así que mis preguntas son sobre cómo recibe el servicio eléctrico y sobre cualquier ampliación u otro campus como él.",
                      "support_conditions": f"{clause}; puedo apoyar una ampliación, u otro campus como él, si mis condiciones quedan por escrito en cualquier aprobación.",
                      "oppose": f"{clause}; me opondría a cualquier ampliación, u otro campus como él, hasta que estas respuestas sean públicas y por escrito."},
        }.get(kind, {"questions": f"{clause}, pero este proyecto u otro campus como él podría volver, y tengo preguntas que deben responderse en público si vuelve.",
                     "support_conditions": f"{clause}; si este proyecto u otro campus como él vuelve, podría apoyarlo si mis condiciones quedan por escrito en cualquier aprobación.",
                     "oppose": f"{clause}; si este proyecto u otro campus como él vuelve tal como se propuso, me opondría."})[body.stance]
        if short1:
            stance = SIT_CLAUSE_SHORT[L].get(kind, clause) + SHORT_STANCE[L][grp][body.stance]
        P.append([(0, f"{greet} me llamo {NAME_SLOT['es']} y vivo en {PLACE_SLOT['es']}."), (0, about), (0, stance)])
    else:
        greet = f"Members of the {sh.addressee}:" if sh.researched else f"To the board hearing this proposal in {sh.addressee}:"
        about = f"I am here about the {e['name']} {thing}" + (f", reported at {_size_words(e, False)}." if e.get("mw") and mins > 1 else ".")
        stance = {
            "pending": {"questions": "I have questions that should be answered in public before you decide.",
                        "support_conditions": "I can support this project if my conditions are written into the approval.",
                        "oppose": "I oppose this proposal as it stands."},
            "not_filed": {"questions": f"{clause}, so I am asking now: these questions should be answered in public before any application is heard.",
                          "support_conditions": f"{clause}; I could support a campus like it if my conditions are written into any approval.",
                          "oppose": f"{clause}; if one is filed as announced, I would oppose it."},
            "built": {"questions": f"{clause}, so my questions are about how it is served and about any expansion or new campus like it.",
                      "support_conditions": f"{clause}; I can support an expansion, or a new campus like it, if my conditions are written into any approval.",
                      "oppose": f"{clause}; I would oppose any expansion, or a new campus like it, until these answers are public and in writing."},
        }.get(kind, {"questions": f"{clause}, but it or a campus like it could come back, and I have questions that should be answered in public if it does.",
                     "support_conditions": f"{clause}; if it or a campus like it comes back, I could support it if my conditions are written into any approval.",
                     "oppose": f"{clause}; if it or a campus like it comes back as proposed, I would oppose it."})[body.stance]
        if short1:
            stance = SIT_CLAUSE_SHORT[L].get(kind, clause) + SHORT_STANCE[L][grp][body.stance]
        P.append([(0, f"{greet} my name is {NAME_SLOT['en']} and I live in {PLACE_SLOT['en']}."), (0, about), (0, stance)])

    lead_n = min(len(concerns), {1: 1, 2: 3, 3: 5}[mins])  # concerns that get a paragraph of their own

    # what the open, synthetic model showed
    g: list[tuple[int, str]] = []
    if sim.get("tested"):
        f = sim["flexible"]
        up, b = cost.get("upgrades") or {}, cost.get("blackout") or {}
        if es:
            g.append((0, f"La consulté en Overload, que prueba un campus {'hipotético de ese tamaño' if e.get('hypothetical') else 'del tamaño reportado'} en un {FRAME_ES}, no una predicción sobre este proyecto ni sobre nuestra empresa eléctrica."
                      if mins > 1 else f"La consulté en el {FRAME_ES} de Overload, que no es una predicción sobre este proyecto."))
            g.append((2, f"En ese modelo, el sitio tiene capacidad para {_n_es(sim['room_mw'])} MW antes de que una línea supere su límite."))
            if sim["overloaded"]:
                n, ot = sim["overloaded"], sim["over_text"]
                what = ("líneas y transformadores", "una línea o transformador") if re.search(r"\bline", ot) and re.search(r"\btransformer", ot) else \
                       ("transformadores", "un transformador") if re.search(r"\btransformer", ot) else ("líneas", "una línea")
                s = f"Con {_n_es(sim['tested_mw'])} MW, " + (f"{n} {what[0]} superan su límite en el modelo" if n > 1 else f"{what[1]} supera su límite en el modelo")
                s += f", y unas {_n_es(_approx_n(f['people']))} personas se quedan sin luz en su cascada (estimación)." if f["people"] else ", y su cascada se detiene sin dejar a nadie sin luz."
            else:
                s = f"Con {_n_es(sim['tested_mw'])} MW, nada supera su límite en el modelo."
            g.append((0, s))
            if up.get("high", 0) > 0 and "bill" not in concerns[:lead_n]:  # the bill paragraph says it with who pays
                g.append((3, f"En el mismo modelo, las mejoras para mantener cada línea dentro de su límite se estiman {_range_es(up['low'], up['high'])}."))
            if b.get("high", 0) > 0:
                g.append((7, f"Un apagón así se estima {_range_es(b['low'], b['high'])} en el modelo."))
        else:
            g.append((0, f"I checked it on Overload, which tests {'a hypothetical campus of that size' if e.get('hypothetical') else 'a campus of the reported size'} on an {FRAME_EN}, not a prediction about this project or our utility."
                      if mins > 1 else f"I checked it on Overload's {FRAME_EN}, which is not a prediction about this project."))
            g.append((2, f"On that model, the site has room for {vote._n(sim['room_mw'])} MW before a line goes over its limit."))
            if sim["overloaded"]:
                s = f"At {vote._n(sim['tested_mw'])} MW, {sim['over_text']} go over their limits on the model"
                s += f", and {f['people_text']} people lose power in its cascade (estimate)." if f["people"] else ", and its cascade settles without cutting anyone off."
            else:
                s = f"At {vote._n(sim['tested_mw'])} MW, nothing goes over its limit on the model."
            g.append((0, s))
            if up.get("high", 0) > 0 and "bill" not in concerns[:lead_n]:  # the bill paragraph says it with who pays
                g.append((3, f"On the same model, the upgrades to keep every line within its limit are estimated at {up['range']}."))
            if b.get("high", 0) > 0:
                g.append((7, f"A blackout like the model's is estimated at {b['range']} on the model, with {b['outage_label']} without power."))
    else:
        g.append((0, "Overload no pudo probar un campus de este tamaño en su modelo de la red, así que hago las preguntas que un modelo no puede responder." if es
                  else "Overload could not run a campus of this size on its grid model, so I am asking the questions a model cannot answer."))
    P.append(g)

    # one paragraph per concern, as many as the time allows: its question, then what to listen for and the facts
    asked: list[str] = []
    for idx, c in enumerate(concerns[:lead_n]):
        qids = [q for q in CONCERN_QUESTIONS[c] if q in sh.questions and (not es or q in Q_ES)]
        if not qids:
            continue
        asked.append(qids[0])
        if es:
            lead = f"Mi mayor preocupación: {CONCERN_TEXT['es'][c]}." if idx == 0 else f"Otra preocupación: {CONCERN_TEXT['es'][c]}."
            q0, listen = Q_ES[qids[0]]
        else:
            lead = f"I am most concerned about {CONCERN_TEXT['en'][c]}." if idx == 0 else f"I am also concerned about {CONCERN_TEXT['en'][c]}."
            q0, listen = sh.questions[qids[0]]["question"], f"I would like to hear: {_low(sh.questions[qids[0]]['listen_for'])}"
        # a supporter's conditions carry the point in a one-minute comment; the question then fills time if it can
        para = [(0, lead), (1 if (support and mins == 1) else 0, q0), (4 + idx, listen)]
        if c == "bill":
            who, up = cost.get("who_pays") or {}, cost.get("upgrades") or {}
            if who.get("high", 0) > 0 and up.get("high", 0) > 0:
                if es:
                    hh = who["households"]
                    hh_text = f"{_dec_es(hh / 1e6, 1).replace(',0', '')} millones de" if hh >= 1e6 else _n_es(_approx_n(hh))
                    para.append((1, f"En el modelo, las mejoras de la red que necesita ({_range_es(up['low'], up['high'])}), repartidas entre los {hh_text} hogares de {sh.state['name']}, "
                                    f"salen {_range_es(who['low'], who['high'])} al mes cada uno; es ilustrativo, y los reguladores deciden quién paga de verdad."))
                else:
                    para.append((1, f"On the model, the grid upgrades it needs ({up['range']}), spread over all {who['households_text']} {cost.get('region_name')} households, "
                                    f"come to {who['range']} a month each; that is illustrative, and regulators decide who really pays."))
            pol = (sh.state.get("policy") or [])[:1]
            if pol:
                para.append((6, f"{sh.state['name']} tiene una norma para grandes cargas, «{pol[0]['title']}», y me gustaría saber cómo se aplica aquí." if es
                             else f"{sh.state['name']} has a rule on the books for large loads, {pol[0]['title']}, and I would like to know how it applies here."))
        if c == "blackouts" and sim.get("tested") and sim["overloaded"]:
            m = sim["firm"]
            if es:
                if m.get("held"):
                    s = (f"En el modelo, con servicio firme, el operador mantiene el campus encendido cortando a otros clientes: unas {_n_es(_approx_n(m['people']))} personas se quedan sin luz (estimación)."
                         if m["people"] else "En el modelo, con servicio firme, el operador puede mantener el campus encendido sin dejar a otros clientes sin luz.")
                else:
                    s = "En el modelo, con servicio firme, el campus tampoco puede mantenerse encendido aquí" + (f", y unas {_n_es(_approx_n(m['people']))} personas se quedan sin luz (estimación)." if m["people"] else ".")
            else:
                if m.get("held"):
                    s = (f"On the model, with firm service the grid operator keeps the campus on by cutting other customers instead: {m['people_text']} people lose power (estimate)."
                         if m["people"] else "On the model, with firm service the grid operator can keep the campus on without cutting anyone else.")
                else:
                    s = "On the model, even firm service cannot keep the campus on here" + (f", and {m['people_text']} people are without power (estimate)." if m["people"] else ".")
            para.append((1, s))
        if len(qids) > 1:
            asked.append(qids[1])
            para.append((8 + idx, Q_ES[qids[1]][0] if es else sh.questions[qids[1]]["question"]))
        P.append(para)
    rest = concerns[lead_n:]
    if rest and not support:  # a supporter's conditions name every concern
        names = [CONCERN_TEXT[L][c] for c in rest]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + (" y " if es else " and ") + names[-1]
        P.append([(0, (f"Otras preocupaciones: {joined}." if len(names) > 1 else f"Otra preocupación: {joined}.") if es else f"I am also concerned about {joined}.")])
    # a few more of the page's questions, if the time allows
    extra = [q for q in ("public-study", "heat-and-storms", "if-it-leaves", "who-pays", "firm-or-flexible") if q in sh.questions and q not in asked and (not es or q in Q_ES)]
    if extra and mins >= 2:
        P.append([(10 + i, (f"Otra pregunta: {Q_ES[q][0]}" if es else f"One more question: {sh.questions[q]['question']}")) for i, q in enumerate(extra[: 3 if mins < 3 else 5])])

    # the ask: a vote only when one is pending; otherwise for if it (or a campus like it) comes back, before an application
    # is heard, or about any expansion
    conds = ""
    if support:
        short = mins == 1 or (mins == 2 and len(concerns) >= 4)
        conds = "; ".join(CONDITION[L][c][1 if short else 0] for c in concerns)
    mor = sh.situation["moratorium"]
    if es:
        back = "Si este proyecto u otro campus como él vuelve" + (", incluso después de la moratoria" if mor else "")
        ask = {
            "pending": {"support_conditions": f"Les pido que incluyan estas condiciones en la aprobación: {conds}.",
                        "oppose": "Les pido que voten no a esta propuesta tal como está, hasta que estas respuestas sean públicas y por escrito.",
                        "questions": "Les pido que obtengan estas respuestas por escrito, y en público, antes de votar."},
            "not_filed": {"support_conditions": f"Si se presenta una solicitud, les pido que incluyan estas condiciones en cualquier aprobación: {conds}.",
                          "oppose": "Si se presenta una solicitud tal como se anunció, les pido que voten no hasta que estas respuestas sean públicas y por escrito.",
                          "questions": "Si se presenta una solicitud" + (", incluso después de la moratoria" if mor else "") + ", les pido que obtengan estas respuestas por escrito, y en público, antes de votar."},
            "built": {"support_conditions": f"Les pido que incluyan estas condiciones en cualquier aprobación de una ampliación u otro campus como él: {conds}.",
                      "oppose": "Les pido que voten no a cualquier ampliación, u otro campus como él, hasta que estas respuestas sean públicas y por escrito.",
                      "questions": "Les pido que obtengan estas respuestas por escrito, y en público, antes de aprobar cualquier ampliación u otro campus como él."},
        }.get(kind, {"support_conditions": f"{back}, les pido que incluyan estas condiciones en cualquier aprobación: {conds}.",
                     "oppose": "Si este proyecto u otro campus como él vuelve tal como se propuso" + (", incluso después de la moratoria" if mor else "") + ", les pido que voten no hasta que estas respuestas sean públicas y por escrito.",
                     "questions": f"{back}, les pido que obtengan estas respuestas por escrito, y en público, antes de votar."})[body.stance]
    else:
        back = "If it, or a campus like it, comes back" + (", including after the moratorium" if mor else "")
        ask = {
            "pending": {"support_conditions": f"Please write these conditions into the approval: {conds}.",
                        "oppose": "I ask you to vote no on this proposal as it stands, until these answers are public and in writing.",
                        "questions": "Please get these answers in writing, and in public, before you vote."},
            "not_filed": {"support_conditions": f"If an application is filed, please write these conditions into any approval: {conds}.",
                          "oppose": "If an application is filed as announced, I ask you to vote no until these answers are public and in writing.",
                          "questions": "If an application is filed" + (", including after the moratorium" if mor else "") + ", please get these answers in writing, and in public, before any vote."},
            "built": {"support_conditions": f"Please write these conditions into any approval for an expansion or a new campus like it: {conds}.",
                      "oppose": "I ask you to vote no on any expansion, or a new campus like it, until these answers are public and in writing.",
                      "questions": "Please get these answers in writing, and in public, before any expansion or new campus like it is approved."},
        }.get(kind, {"support_conditions": f"{back}, please write these conditions into any approval: {conds}.",
                     "oppose": "If it, or a campus like it, comes back as proposed" + (", including after the moratorium" if mor else "") + ", I ask you to vote no until these answers are public and in writing.",
                     "questions": f"{back}, please get these answers in writing, and in public, before any vote."})[body.stance]
    if short1:
        ask = SHORT_ASK[L][grp][body.stance].format(conds=conds)
    P.append([(0, ask + (" Gracias." if es else " Thank you."))])
    return _fit(P, mins * WPM)


# ------------------------------------------------------------------------------------ Gemini
_SYS_WORDS = {
    # "no": the phrases the checker refuses anywhere, a question included (_FORBID_ALWAYS); "ask" / "now": wording it accepts instead;
    # "ours": what it refuses as a statement about the real grid (_REAL_GRID); "disc": the one mention of the real grid it accepts in a statement
    "en": {"frame": FRAME_EN, "on": "on the model",
           "no": "'will cause', 'would cause', 'caused', 'causes', 'responsible for', 'to blame', 'led to', 'leading to' or 'results in' before blackouts, outages or failures, 'is going to', "
                 "'will black out', 'will raise my bill', 'plans to raise' (or to increase, or to charge), 'will make us pay'",
           "ask": "'Who pays for the upgrades?', 'What would it take to keep the campus from leaving neighbors without power?'",
           "now": "'on the model, the campus is cut off first'",
           "disc": "'on the model, not the real grid'",
           "greet": f"my name is {NAME_SLOT['en']} and I live in {PLACE_SLOT['en']}", "lang": "English",
           "ours": "'our grid', 'our infrastructure', 'our power lines', 'our substations', 'the local grid', 'the real grid', 'the existing grid', 'the county's grid', 'Florida's grid'",
           "only": "Write in plain English."},
    "es": {"frame": FRAME_ES, "on": "en el modelo",
           "no": "'causará', 'causaría', 'provocaría', 'causó', 'causa apagones', 'llevó a apagones', 'va a', 'responsable de', 'culpa', 'dejará sin luz', 'subirá mi factura', 'nos hará pagar', 'planea subir'",
           "ask": "'¿Quién paga las mejoras?', '¿Qué haría falta para que el campus no deje a los vecinos sin luz?'",
           "now": "'en el modelo, el campus se desconecta primero'",
           "disc": "'en el modelo, no en la red real'",
           "greet": f"me llamo {NAME_SLOT['es']} y vivo en {PLACE_SLOT['es']}", "lang": "Spanish",
           "ours": "'nuestra red', 'nuestra infraestructura', 'nuestras líneas', 'nuestras subestaciones', 'la red local', 'la red real', 'la red actual', 'la red de la ciudad'",
           "only": "Write every word in Spanish ('9 líneas y 2 transformadores', never 'transformers'); only proper names stay as given. Never write vague multitudes such as 'miles' or 'cientos'. "
                   "Write numbers the Spanish way, with a period for thousands and a comma for decimals: 1.200 MW, 11.700 personas, 100.000 hogares, 9,19 dólares, entre 1,1 y 2,7 millones de dólares."},
}


def _system(lang: str) -> str:
    """The rules, with only the chosen language's required phrases (an English request never sees the Spanish ones)."""
    w = _SYS_WORDS[lang]
    return (
        f"You write a public comment, in {w['lang']}, for a resident to read aloud at a local government meeting (or send by mail) about a "
        "proposed data center. Write in the first person as that resident: their stance, their concerns, their voice: plain, calm, specific "
        "and respectful. Facts come ONLY from the fact sheet you are given (in English), which is what the resident's page showed them. Hard "
        "rules, checked word by word by a program before anyone sees your text:\n"
        "1. Every number must be copied from the fact sheet, written in digits (a person's rounding is fine: 11,694 -> about 11,700). No other "
        "numbers at all: no dates, years, counts, percentages or totals of your own. Do not number your points or questions, and do not say how "
        "many questions you have.\n"
        f"2. Results from the grid test are on an OPEN, SYNTHETIC grid model, not the real grid: before the first one, say '{w['frame']}', and "
        f"put '{w['on']}' in the SAME sentence as every number from the model. It is not a prediction about the real project, its developer or its "
        f"utility. Only the model's lines go over their limits: never write {w['ours']} in a statement (you may ask about the real grid only as a "
        f"question; the one mention a statement may carry is a plain disclaimer such as {w['disc']}), and never compare the reported size with what a "
        "real grid can take. A number keeps its meaning: '4 hours' only where the fact sheet says hours, '2 transformers' only where it says transformers.\n"
        f"3. Never say what the real project, its developer or its utility will do or did. The checker refuses these words anywhere, even inside a question: "
        f"{w['no']}. Ask a real question instead ({w['ask']}) or say what the model showed, in the present tense ({w['now']}): outside a question, "
        "'will' or 'would' before a verb of harm or change (cause, cut, leave, fail, raise, strain, overload, trigger, harm) is refused too, so keep "
        "'will' and 'would' inside questions. Never make the developer or the utility the subject of a model result.\n"
        "4. Name no company, tenant, utility or person except those in the fact sheet, and never name any company, developer or utility in a sentence "
        "about the model. Name no officials, neighbors or family members, and no streets.\n"
        "5. No insults, accusations or claims of wrongdoing (nothing like misled, lied, deceptive, greedy, profiteering, gouging); no links, emails, "
        "phone numbers or addresses. Write every letter as itself: accented letters and ¿ ¡ ñ directly, never as escapes or codes (no &eacute;, no %e9).\n"
        "6. Address the decision body by its exact name as given (a proper name: never translate it). Start with the greeting, then the words "
        f"'{w['greet']}' exactly, brackets included, then the stance. Keep both bracketed placeholders as written: never invent a name, street or neighborhood.\n"
        "7. Say where the proposal stands exactly as the instructions give it. When no vote is pending, never ask the body to vote on it now: write "
        "every request for if it, or a campus like it, comes back (or about any expansion).\n"
        f"8. {w['only']}\n"
        "9. Hit the word count you are given: it is what fits the resident's speaking time, and a comment far over or under it is rejected.\n"
        'Reply as JSON only: {"paragraphs": ["...", "..."]} (3 to 6 paragraphs).'
    )


SYSTEM = {lang: _system(lang) for lang in ("en", "es")}
SCHEMA = {"type": "object", "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}}}, "required": ["paragraphs"]}
# Structured output for English; Spanish asks for the same JSON in plain JSON mode. Measured Sat on the Fort Meade cases:
# gemini-3.5-flash at minimal thinking under responseJsonSchema garbled accented letters in 3 of 4 Spanish drafts
# ("oposici3n", "l&iacute;nea", "%bfQui%e9n"), and 0 of 3 without the schema (same prompt, ~3 s). The garbled-text check
# in check() still guards both.
SCHEMA_FOR = {"en": SCHEMA, "es": None}
OFF = {"__offline__": True}


def _prompt(sh: Sheet, body: CommentIn) -> str:
    lang = "Spanish" if body.lang == "es" else "English"
    concerns = _ordered(body.concerns)
    cs = ", ".join(CONCERN_TEXT["en"][c] for c in concerns) if concerns else "none ticked: cover the grid test, service in a heat wave and who pays"
    target = body.minutes * WPM
    paras = {1: 3, 2: 5, 3: 6}[body.minutes]
    qs = list(_qids(concerns))[: max(2, len(concerns) + 1)]

    def line(f: Fact) -> str:
        tag = f"question {f.key[2:]}" if f.key.startswith("q:") else f.kind
        return f"- [{tag}] {f.text}"

    facts = "\n".join(line(f) for f in sh.facts)
    es = body.lang == "es"
    if sh.researched:
        opening = (f"Estimados miembros de la {sh.addressee}: me llamo {NAME_SLOT['es']} y vivo en {PLACE_SLOT['es']}." if es
                   else f"Members of the {sh.addressee}: my name is {NAME_SLOT['en']} and I live in {PLACE_SLOT['en']}.")
        greet = sh.addressee
    else:
        opening = (f"Al organismo que decide esta propuesta en {sh.addressee}: me llamo {NAME_SLOT['es']} y vivo en {PLACE_SLOT['es']}." if es
                   else f"To the board hearing this proposal in {sh.addressee}: my name is {NAME_SLOT['en']} and I live in {PLACE_SLOT['en']}.")
        greet = f"the board hearing this proposal in {sh.addressee} (the page doesn't name it yet)"
    glossary = (
        "Spanish wording: utility = la empresa eléctrica (never 'la utilidad'); the grid = la red eléctrica; firm service = servicio firme; "
        "flexible service = servicio flexible; data center = centro de datos; say 'en el modelo' once in a sentence, never twice in a row.\n"
        if es else ""
    )
    kind = sh.situation["kind"]
    clause = SIT_CLAUSE[body.lang][sh.situation["status"] if kind == "built" else kind] if kind != "pending" else ""
    if kind == "pending":
        where = ("It is before the body (or will be), as the fact sheet's status and 'Where it stands' say: the resident may ask the body to act on it. "
                 "If the fact sheet says an approval was already given, ask for the answers before any further approval or permit.")
        back = ""
    elif kind == "built":
        where = (f"It is already {sh.situation['status']}, as reported: there is no vote on it to ask for. Say so in a neutral clause, in these words: "
                 f"\"{clause}\". Write about how it is served and about any expansion or new campus like it: every request to the body is about "
                 "'any expansion or new campus like it'.")
        back = " (about any expansion or new campus like it)"
    elif kind == "not_filed":
        where = (f"As reported, no application has been filed: there is no vote to ask for now. Say so in a neutral clause, in these words: \"{clause}\". "
                 "Write every request to the body as 'if an application is filed, ...'.")
        back = " (if an application is filed)"
    else:
        where = (f"There is no vote pending on it. Say where it stands in a neutral clause, in these words: \"{clause}\". Write the comment for if it, "
                 "or a campus like it, comes back: every request to the body starts with 'If it, or a campus like it, comes back'. Never ask the body to "
                 "vote on it now, and never say 'as it stands'.")
        back = " (if it, or a campus like it, comes back)"
    if es and kind != "pending":
        # the phrases above are English: gemini-3.5-flash-lite copied "If it, or a campus like it, comes back" word for word into
        # Spanish drafts (the English-word check then sent them back), so the Spanish wording is given too
        where += (" In this Spanish comment write that phrase in Spanish, never in English: '" + {
            "built": "sobre cualquier ampliación o campus nuevo como este",
            "not_filed": "si se presenta una solicitud",
        }.get(kind, "si vuelve, o si llega un campus como este") + "'.")
    if sh.situation["moratorium"]:
        where += " The status note mentions a moratorium: you may mention it neutrally, as reported."
    shape = {
        "questions": f"the resident has questions: ask the matching questions from the fact sheet, and close by asking the body to get the answers in writing and in public before any vote{back}",
        "support_conditions": f"the resident supports it WITH CONDITIONS{back}: say so plainly, then name the conditions that match their concerns (the campus pays for its own grid upgrades; it cuts back first in a heat wave; a public water plan; public air permits for generators; jobs and tax promises in writing), and ask the body to write them into any approval",
        "oppose": f"the resident OPPOSES it as proposed{back}: say so plainly and respectfully, give what the model showed and the open questions as the reasons, and ask the body to vote no{back} until the answers are public and in writing",
    }[body.stance]
    return (
        f"Write the comment in {lang}.\n"
        f"Address it to: {greet}. Begin with exactly this line: \"{opening}\"\n"
        f"{glossary}"
        f"The proposal: {sh.entry['name']} ({sh.entry['place_text']}).\n"
        f"Where it stands: {where}\n"
        f"Stance: {shape}.\n"
        f"The resident's concerns, most important first: {cs}.\n"
        f"Questions to lead with (from the fact sheet): {', '.join(qs)}; ask them in your own words or as written.\n"
        f"Length: about {target} words ({body.minutes} minute{'s' if body.minutes > 1 else ''} spoken at {WPM} words a minute): {paras} paragraphs of about {target // paras} words each. "
        f"Never more than {int(target * 1.15)} words or fewer than {int(target * 0.8)}.\n"
        "Suggested shape: the greeting and who I am; my stance; what the model showed for a campus of the reported size, in a paragraph that "
        f"starts with the words \"{FRAME_LEAD[body.lang]}\"; a paragraph for each concern with its question; the ask; thank you.\n\n"
        f"FACT SHEET (the only facts you may use):\n{facts}\n"
    )


def _join(raw) -> str | None:
    if not isinstance(raw, dict):
        return None
    paras = raw.get("paragraphs")
    if isinstance(paras, str):
        paras = [paras]
    if not isinstance(paras, list):
        return None
    out = [" ".join(str(p).split()) for p in paras if isinstance(p, str) and p.strip()]
    text = "\n\n".join(out)
    return text if 40 <= len(text) <= 6000 else None


# ------------------------------------------------------------------------------------ the route
_cache: "OrderedDict[tuple, tuple[float, float, dict]]" = OrderedDict()
_cache_lock = threading.Lock()


def _ckey(pid: str, body: CommentIn, ai: bool) -> tuple:
    cat = catalog_mod.catalog()
    cv = vote.civic()
    # ai and the key apart: without a key an ai=true answer says "Gemini not configured", never "plain version requested"
    return (pid, cat["mtime"], cv.get("_mtime"), tuple(_ordered(body.concerns)), body.stance, body.minutes, body.lang, bool(ai), llm.configured())


def _respond(sh: Sheet, body: CommentIn, text: str, by: str, why: str | None, draft_ids: list, t0: float) -> dict:
    """The answer the page shows. Only check ids and their labels leave the server: the checker's findings quote the
    offending words (a rejected sentence, an insult), so they stay in the rewrite prompt and the log."""
    ok, findings, nums, checks = check(text, sh, body)
    if not ok and by != "template":  # a guard: the caller only passes Gemini text that passed
        log.warning("comment: Gemini text reached _respond failing its checks: %s", findings[:2])
        return _respond(sh, body, template(sh, body), "template", "Gemini's comment failed the checks", draft_ids, t0)
    if not ok:
        log.error("comment: the template failed its own checks for %s: %s", sh.pid, findings)
    words = _words(text)
    es = body.lang == "es"
    kind = sh.situation["kind"]
    sit_note = None if kind == "pending" else SIT_NOTE[kind].format(status=sh.situation["status"])
    draft_checks = [{"id": c, "label": CHECK_LABELS[c], "caught": CHECK_CAUGHT[c]} for c in dict.fromkeys(draft_ids) if c in CHECK_LABELS]
    return {
        "id": sh.pid,
        "lang": body.lang,
        "stance": body.stance,
        "minutes": body.minutes,
        "concerns": _ordered(body.concerns),
        "subject": f"Comentario público sobre la propuesta {sh.entry['name']}" if es else f"Public comment on the {sh.entry['name']} proposal",
        "addressee": sh.addressee,
        "researched": sh.researched,
        "send": sh.send,
        "text": text,
        "numbers": nums,
        "checked": sum(1 for n in nums if n["ok"]),
        "total": len(nums),
        "checks": [{**c, "label": CHECK_LABELS[c["id"]]} for c in checks],
        "ok": ok,
        "findings": [CHECK_LABELS[c["id"]] for c in checks if not c["ok"]] if not ok else [],
        "situation": {"kind": kind, "status": sh.situation["status"], "note": sit_note},
        "by": by,
        "fallback": by == "template",
        "why": why,
        "draft_checks": draft_checks,
        "rewritten": bool(draft_checks) and by == "gemini",
        "words": words,
        "seconds": round(words / WPM * 60),
        "target_words": body.minutes * WPM,
        "frame": vote.FRAME,
        "credit": vote.GRID_CREDIT,
        "ms": round((time.perf_counter() - t0) * 1000),
    }


_busy_until: dict[str, float] = {}  # model -> when to try it again after a 503, a timeout or a dropped connection


def _model_order() -> list[str]:
    """The writer's models in the order they are tried: the stronger one, then the fast one (the same when only one is set)."""
    first = COMMENT_MODEL or llm.MODEL
    fast = COMMENT_FAST_MODEL or llm.MODEL
    return [first] if fast == first else [first, fast]


async def _gemini(sh: Sheet, body: CommentIn, t0: float) -> tuple[str | None, str | None, list]:
    """(checked Gemini text or None, why not, the ids of the checks the first draft failed when it was sent back)."""
    prompt = _prompt(sh, body)
    keys: list = []
    schema = SCHEMA_FOR[body.lang]

    async def ask(p: str, budget: float):
        """(reply, offline): the same prompt on each model in turn until one answers; only the last try is allowed to end as a
        fallback, so the AI panel's counters say what the visitor got (a busy first model isn't a fallback if the fast one answers)."""
        t_ask = time.perf_counter()
        order = _model_order()
        if not llm.configured():
            order = order[-1:]
        else:
            now = time.time()
            order = [m for m in order if _busy_until.get(m, 0.0) <= now] or order[-1:]  # every model cooling down: the fast one anyway
        for n, m in enumerate(order):
            last = n == len(order) - 1
            left = budget - (time.perf_counter() - t_ask)
            if left <= 1.0:
                raise asyncio.TimeoutError()
            try_s = min(FAST_TRY_S if last else FIRST_TRY_S, left)
            keys.append(llm._cache_key(p, SYSTEM[body.lang], True, schema, m))
            try:
                return await asyncio.wait_for(
                    llm.complete_json(p, system=SYSTEM[body.lang], fallback=OFF if last else None, timeout=try_s, schema=schema, surface="comment",
                                      cache=True, model=m, thinking=COMMENT_THINKING),
                    timeout=left if last else min(left, try_s + 1.0),
                )
            except asyncio.TimeoutError:
                if last:
                    raise
                why, busy = f"no answer in {try_s:.0f} s", True
            except HTTPException as e:  # a try that may not fall back raises: not configured, a quota, a 5xx, a dropped connection, bad JSON
                if last:
                    return OFF, True
                why = " ".join(str(e.detail)[:160].split())[:120]
                busy = "invalid JSON" not in why
            if busy:
                _busy_until[m] = time.time() + COOLDOWN_S
            log.warning("comment: %s failed (%s), trying %s", m, why, order[n + 1])
        raise asyncio.TimeoutError()  # unreachable: the last try returns or raises above

    try:
        raw, off = await ask(prompt, DEADLINE_S)
    except asyncio.TimeoutError:
        return None, "Gemini too slow", []
    if off or (isinstance(raw, dict) and raw.get("__offline__")):
        return None, ("Gemini unavailable" if llm.configured() else "Gemini not configured"), []
    text = _join(raw)
    if text:
        ok, findings, _, checks = check(text, sh, body)
        _ledger(ok, checks)
        if ok:
            return text, None, []
        ids = [c["id"] for c in checks if not c["ok"]]
        log.info("comment: the draft for %s (%s) was sent back: %s | %r", sh.pid, body.lang, findings[:3], text[:1500])
    else:
        findings, ids = ["the reply had no comment in it"], []
    left = DEADLINE_S - (time.perf_counter() - t0)
    if left < 4:
        llm.cache_forget(keys)
        return None, "Gemini's comment failed the checks", ids
    again = (
        prompt
        + "\n\nYOUR DRAFT:\n" + (text or "(no comment in the reply)")
        + "\n\nA CHECKER REJECTED IT FOR THESE REASONS:\n" + "\n".join(f"- {x}" for x in findings[:8])
        + "\nRewrite the whole comment, fixing every point and keeping everything else. Same JSON shape."
    )
    try:
        raw2, off2 = await ask(again, left)
    except asyncio.TimeoutError:
        llm.cache_forget(keys)
        return None, "Gemini too slow", ids
    text2 = _join(raw2) if not off2 else None
    if text2:
        ok2, findings2, _, checks2 = check(text2, sh, body)
        _ledger(ok2, checks2)
        if ok2:
            return text2, None, ids
        log.info("comment: the rewrite for %s (%s) failed too: %s | %r", sh.pid, body.lang, findings2[:3], text2[:1500])
    llm.cache_forget(keys)  # a later request asks afresh instead of replaying the same failing drafts
    return None, ("Gemini's comment failed the checks twice" if text2 else "Gemini unavailable"), ids


@router.post("/api/vote/proposal/{proposal_id}/comment")
@limiter.limit("30/minute")
async def write_comment(
    request: Request,
    body: CommentIn,
    proposal_id: str = PathParam(..., pattern=vote.ID_PATTERN),
    ai: bool = Query(True),
):
    """The resident's public comment for this proposal: Gemini writes it from the page's facts, the checker verifies every
    number, name and claim, and a labeled plain version runs when Gemini can't (or ai=false)."""
    t0 = time.perf_counter()
    key = await run_in_threadpool(_ckey, proposal_id, body, ai)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] <= hit[1]:
            _cache.move_to_end(key)
            return {**hit[2], "cached": True}
    sh = await run_in_threadpool(_sheet, proposal_id, body.concerns)
    if not ai:
        out = _respond(sh, body, template(sh, body), "template", "plain version requested", [], t0)
    else:
        text, why, drafts = await _gemini(sh, body, t0)
        out = _respond(sh, body, text, "gemini", None, drafts, t0) if text else _respond(sh, body, template(sh, body), "template", why, drafts, t0)
    ttl = TTL_RETRY_S if (ai and out["fallback"] and llm.configured()) else TTL_OK_S
    with _cache_lock:
        _cache[key] = (now, ttl, out)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return {**out, "cached": False}
