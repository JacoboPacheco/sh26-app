"""AI emergency bulletin: three plain-English sentences about what a cascade just did.

POST /api/bulletin takes the same case body as /api/grid/cascade (grid.py → CaseIn), re-runs the
cascade here — the client never writes the facts — and asks Gemini (llm.complete) to turn those
facts into a short public bulletin. Every failure mode (no key, quota gone, Google down, slow,
an answer that breaks the honesty rules) returns a template built from the same facts, flagged
`fallback: true` so the UI can say so.

Public like the grid endpoints (no login, no database), with a per-visitor rate limit; the
whole-app daily AI cap lives inside llm.complete. Bulletins Gemini wrote are cached in memory by
their facts, so a replay or a second judge on the same case costs no quota.
"""

import asyncio
import json
import logging
import re
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from grid import CaseIn, _case_header, check_case
from limiter import limiter
from llm import complete
from powerflow import HOMES_PER_MW

router = APIRouter(tags=["bulletin"])
log = logging.getLogger("uvicorn.error")

# the heat-wave clock's presets (frontend/src/features/heat/presets.js) read as a time of day
LOAD_WORDS = {
    0.62: "overnight",
    0.82: "in the morning",
    1.0: "on a summer afternoon",
    1.04: "during a heat wave",
    1.08: "at the height of a heat wave",
}
MAX_TRIPS_NAMED = 3
MAX_TOWNS = 5
MAX_CHARS = 900
CACHE_SIZE = 128
_cache: OrderedDict[str, str] = OrderedDict()

SYSTEM = """You write short public emergency bulletins about a simulated power-grid failure.
Write exactly 3 sentences, at most 75 words in total:
1. What set it off: the new data center (its size and town), the storm, or the heat, and when.
2. How it spread: overloaded lines tripping one after another, over how many steps, starting where;
   or, if the facts say nothing tripped, that every line held.
3. Who lost power: the estimated number of homes and the hardest-hit towns; or, if the facts say no
   homes lost power, that homes kept their power.
Rules:
- Never describe a failure, a step count or an outage the facts do not list.
- Say once, briefly, that this happened in a synthetic model of Florida's grid.
- Never name a real utility or company, and never mention a real storm, date or event.
- Home counts are estimates: always write "an estimated" before the number of homes.
- Use only the facts given. Do not invent crews, repairs, restoration times, advice, clock times,
  dates or numbers.
- Write numbers as digits the way a newspaper does (1,500 MW, 9 steps, 38,000, 1.05 million).
- Plain text only: no markdown, lists, headings or emoji. Calm, clear and direct, like a public
  safety alert people read on their phones."""

# An answer that names a real utility or a named storm breaks the honesty rule: use the template.
_FORBIDDEN = re.compile(
    r"\b(FPL|TECO|JEA|OUC|FMPA|GRU|NextEra|FPUC|KUA|LCEC|ERCOT|FERC|NERC)\b"
    r"|(?i:\bflorida power\b|\bduke energy\b|\btampa electric\b|\bgulf power\b|\borlando utilities\b"
    r"|\bseminole electric\b|\blakeland electric\b|\bflorida public utilities\b|\bkissimmee utility\b"
    r"|\blee county electric\b|\bclay electric\b|\bwithlacoochee\b|\bpeace river electric\b)"
    r"|\b[Hh]urricane [A-Z][a-z]+"  # a named storm ("Hurricane Ian"); "hurricane season" is fine
    r"|\b(Andrew|Charley|Wilma|Irma|Michael|Ian|Idalia|Helene|Milton)\b"  # past Florida storms by name
)
AI_DEADLINE_S = 12  # llm's timeout is per socket read; this bounds the whole call


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _allowed_numbers(facts: dict) -> set[str]:
    """Every figure a bulletin may print, normalized (no commas): the facts, and the homes estimate
    at the roundings a newspaper would use."""
    homes = facts["homes_estimate"]
    vals = [
        facts["data_center_mw"], len(facts["data_centers"]), facts["steps"], facts["storm_lines_out"],
        facts["upgraded_lines"], facts["load_lost_mw"], round(facts["load_factor"] * 100),
        homes, round(homes, -2), round(homes, -3), round(homes, -4), round(homes, -5),
    ]
    vals += [d["mw"] for d in facts["data_centers"]] + [t["homes_estimate"] for t in facts["towns"]]
    out = {str(int(v)) for v in vals}
    for scale, digits in ((1e6, 2), (1e6, 1), (1e3, 0)):  # "1.05 million", "1.1 million", "38 thousand"
        out.add(f"{homes / scale:.{digits}f}".rstrip("0").rstrip(".") if digits else str(round(homes / scale)))
    out.add(_homes_words(homes).split()[0].replace(",", ""))
    return out


def _honest(text: str, facts: dict) -> bool:
    """The rules the page promises: synthetic model named, homes called an estimate, no real names,
    and no figure the server's facts don't contain (an invented step count or outage)."""
    low = text.lower()
    if len(text) < 40 or _FORBIDDEN.search(text) or "synthetic" not in low:
        return False
    if facts["homes_estimate"] > 0 and "estimat" not in low:
        return False
    allowed = _allowed_numbers(facts)
    return all(m.replace(",", "").rstrip(".,") in allowed for m in _NUMBER.findall(text))


def _town(sub_name: str) -> str:
    """'FORT MYERS 12' -> 'Fort Myers' (matches townOf() in frontend/src/store.jsx)."""
    return re.sub(r"\s+\d+$", "", sub_name.strip()).title()


def _sub_label(sub_name: str) -> str:
    """'FORT MYERS 12' -> 'Fort Myers 12'."""
    return sub_name.strip().title()


def _load_words(f: float) -> str:
    f = round(float(f), 2)
    return LOAD_WORDS.get(f) or f"with demand at {round(f * 100)}% of the summer peak"


def _homes_words(n: int) -> str:
    """Round an estimate so it reads like one: 1,051,974 -> '1.05 million', 38,312 -> '38,000'."""
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}".rstrip("0").rstrip(".") + " million"
    if n >= 10_000:
        return f"{round(n, -3):,}"
    if n >= 1_000:
        return f"{round(n, -2):,}"
    return f"{n:,}"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def build_facts(body: CaseIn) -> dict:
    """Re-run the case's cascade and keep only what a bulletin needs (all plain JSON)."""
    g, sites, trip, upgrades = check_case(body)
    if not sites and not trip and round(g.load_factor, 2) == 1.0:
        raise HTTPException(status_code=422, detail="Nothing has happened yet — add a data center, a storm or a heat level first")
    extra, header = _case_header(g, sites, trip, upgrades)
    c = g.cascade_case(extra, trip, upgrades)

    def ends(bid: int) -> tuple[int, int]:
        i = g.br_index[bid]
        return int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])

    def line(bid: int) -> str:
        a, b = ends(bid)
        if a == b:
            return f"the {_sub_label(g.sub_name[a])} transformer"
        return f"the {_sub_label(g.sub_name[a])} to {_sub_label(g.sub_name[b])} line"

    first_ids = [bid for st in c["steps"] if st["n"] > 0 for bid in st["tripped"]][:MAX_TRIPS_NAMED]
    first_trips = [line(bid) for bid in first_ids]
    # where the spread started, in town names ("around North Fort Myers and Fort Myers")
    first_towns = list(dict.fromkeys(_town(g.sub_name[s]) for bid in first_ids for s in ends(bid)))[:MAX_TRIPS_NAMED]

    by_town: dict[str, float] = {}
    for sid, mw in c["affected"].items():
        name = _town(g.sub_name[g.sub_index[int(sid)]])
        by_town[name] = by_town.get(name, 0.0) + mw
    towns = sorted(by_town.items(), key=lambda kv: -kv[1])[:MAX_TOWNS]

    return {
        "data_centers": [{"substation": _sub_label(s["sub_name"]), "town": _town(s["sub_name"]), "mw": round(s["mw"])} for s in header["sites"]],
        "data_center_mw": round(header["mw"]),
        "load_level": _load_words(g.load_factor),
        "load_factor": round(g.load_factor, 2),
        "storm_lines_out": len(trip),
        "upgraded_lines": len(upgrades),
        "first_trips": first_trips,
        "first_trip_towns": first_towns,
        "steps": c["total_steps"],
        "outcome": c["outcome"],
        "still_spreading": c["capped"],
        "load_lost_mw": round(c["lost_mw"]),
        "homes_estimate": c["homes"],
        "data_center_lost_power": c["site_dark_mw"] > 0.5,
        "towns": [{"town": t, "homes_estimate": int(round(mw * HOMES_PER_MW))} for t, mw in towns],
    }


def _dc_phrase(facts: dict) -> str:
    dcs = facts["data_centers"]
    if not dcs:
        return ""
    if len(dcs) == 1:
        mw = dcs[0]["mw"]
        article = "an" if str(mw).startswith("8") or mw in (11, 18) else "a"  # "an 800 MW", "an 18 MW"
        return f"{article} {mw:,} MW data center near {dcs[0]['town']}"
    towns = list(dict.fromkeys(d["town"] for d in dcs))
    shown = towns[:3] + (["other towns"] if len(towns) > 3 else [])
    return f"{len(dcs)} new data centers totaling {facts['data_center_mw']:,} MW near {_join(shown)}"


def template(facts: dict) -> str:
    """The bulletin without AI: three sentences from the same facts, readable on their own."""
    dc, level, storm = _dc_phrase(facts), facts["load_level"], facts["storm_lines_out"]
    n, where = facts["steps"], facts["first_trip_towns"]
    storm_txt = f"a storm knocked out {storm} {'line' if storm == 1 else 'lines'}" if storm else ""
    lead = "In this synthetic model of Florida's grid, "
    if dc and storm_txt:
        s1 = f"{lead}{storm_txt} while {dc} drew power {level}."
    elif dc:
        s1 = f"{lead}{dc} came online {level}" + (" and pushed nearby lines past their limits." if n else ".")
    elif storm_txt:
        s1 = f"{lead}{storm_txt} {level}."
    else:
        demand = f"demand {level}" if facts["load_factor"] in LOAD_WORDS else f"demand at {round(facts['load_factor'] * 100)}% of the summer peak"
        pct = round(facts["load_factor"] * 100)
        s1 = f"{lead}{demand} pushed lines past their limits." if n else f"{lead}demand was set to {pct}% of the summer peak."

    if n == 0:
        s2 = "No other line went over its limit after that." if storm else "Every line stayed within its limit."
    else:
        s2 = f"Overloaded lines then tripped one after another over {n} {'step' if n == 1 else 'steps'}"
        s2 += f", starting around {_join(where)}" if where else ""
        s2 += ", and the failure was still spreading when the model stopped." if facts["still_spreading"] else "."

    homes = facts["homes_estimate"]
    if facts["outcome"] == "islanded" and homes > 0:
        towns = [t["town"] for t in facts["towns"]]
        s3 = f"The grid split apart, leaving an estimated {_homes_words(homes)} homes without power"
        s3 += f", most of them in {_join(towns)}" if towns else ""
        if facts["data_center_lost_power"] and facts["data_centers"]:
            s3 += ", and the data center lost power too." if len(facts["data_centers"]) == 1 else ", and some of the data centers lost power too."
        else:
            s3 += "."
    elif facts["outcome"] == "islanded":
        s3 = "Part of the grid split off, but homes kept their power in this model."
    else:
        s3 = "The grid settled without cutting power to homes."
    return " ".join([s1, s2, s3])


def prompt_for(facts: dict) -> str:
    lines = ["Facts about what just happened in the model:"]
    dc = _dc_phrase(facts)
    if dc:
        lines.append(f"- New load: {dc}.")
    if facts["storm_lines_out"]:
        lines.append(f"- A storm knocked out {facts['storm_lines_out']} lines first.")
    if facts["upgraded_lines"]:
        lines.append(f"- {facts['upgraded_lines']} lines had been upgraded beforehand.")
    lines.append(f"- When: {facts['load_level']}.")
    if facts["steps"]:
        lines.append(f"- Overloaded lines tripped one by one over {facts['steps']} steps.")
    elif facts["storm_lines_out"]:
        lines.append("- After the storm, no other line went over its limit: nothing else tripped.")
    else:
        lines.append("- Every line stayed within its limit: nothing tripped.")
    if facts["first_trip_towns"]:
        lines.append(f"- The first lines to trip were around {_join(facts['first_trip_towns'])}.")
    if facts["still_spreading"]:
        lines.append("- It was still spreading when the model stopped.")
    outcome = "the grid split into pieces and some areas went dark" if facts["outcome"] == "islanded" else "the grid settled"
    lines.append(f"- Outcome: {outcome}.")
    if facts["homes_estimate"] > 0:
        lines.append(f"- Homes without power: an estimated {_homes_words(facts['homes_estimate'])}.")
        lines.append(f"- Existing load lost: {facts['load_lost_mw']:,} MW.")
    else:
        lines.append("- No homes lost power.")
    if facts["towns"]:
        lines.append(f"- Hardest-hit towns: {_join([t['town'] for t in facts['towns']])}.")
    if facts["data_center_lost_power"] and facts["data_centers"]:
        lines.append("- The data center lost power too." if len(facts["data_centers"]) == 1 else "- Some of the data centers lost power too.")
    lines.append("\nWrite the 3-sentence bulletin now.")
    return "\n".join(lines)


def _clean(text: str) -> str:
    """Plain text only, one paragraph, bounded length."""
    text = re.sub(r"[*_#`>]+", "", text or "")
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip()
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        end = cut.rfind(". ")
        text = cut[: end + 1] if end > 0 else cut.rstrip() + "…"
    return text


def _key(facts: dict) -> str:
    return json.dumps(facts, sort_keys=True, separators=(",", ":"))


@router.post("/api/bulletin")
@limiter.limit("30/minute")
async def bulletin(request: Request, body: CaseIn):
    facts = await run_in_threadpool(build_facts, body)  # the cascade is CPU work: keep the event loop free
    fallback_text = template(facts)
    key = _key(facts)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return {"text": cached, "fallback": False, "facts": facts}

    try:
        raw = await asyncio.wait_for(
            complete(prompt_for(facts), system=SYSTEM, fallback=fallback_text, timeout=10), AI_DEADLINE_S
        )
    except asyncio.TimeoutError:
        log.warning("AI bulletin took over %ss, template used instead", AI_DEADLINE_S)
        raw = fallback_text
    if raw is fallback_text:  # llm.complete hands back the very fallback object when it gave up
        return {"text": fallback_text, "fallback": True, "facts": facts}
    text = _clean(raw)
    if not _honest(text, facts):
        log.warning("AI bulletin rejected, template used instead: %r", text[:160])
        return {"text": fallback_text, "fallback": True, "facts": facts}
    _cache[key] = text
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return {"text": text, "fallback": False, "facts": facts}
