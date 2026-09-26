"""Solutions: how the fixes are ranked and described, and the AI proposer whose plans the engine checks.

The rule (user, Sat 05:58): building the data center HERE at its full amount is the highest priority. A small
lowering is fine, but people want to see more power. So a fix that keeps at least FULL_KEEP_PCT of the campus
ranks first (strengthen the grid, or build it at the same size elsewhere), and shrinking far below the plan is
the last resort. There is always more than one solution when more than one holds.

    enrich(c, g, fixes, total)  adds to every fix: kept_mw, kept_pct, cost {low, high} | None, by, must {en, es},
                                strain (the grid's line loading with the fix in place)
    strain_report(c)            the grid's strain with no campus, with the campus, and what the best fix leaves
    ranked(fixes)               indices of the verified fixes, best first
    best_fix(fixes)             the index the report calls best (a verified fix, else the partial one that helps most)
    kick(key)                   start the AI proposer in the background for a cached report (once)
    propose(key)                the agentic loop: Gemini proposes plans, the engine re-runs each one, a failing plan is
                                sent back once with what the engine found, only verified plans are added. Every round
                                is recorded on the report as agentic.trace ("Watch the AI work"): what Gemini proposed
                                (lines, MVA, keep %), what the engine found (holds, or which lines stay over and by how
                                much, people still out), the feedback sent back and the revision, plus agentic totals
                                (asked, verified, rounds, calls, ms). The trace is written live while the loop runs.
                                BEAT THE ENGINE: the prompt carries each line's high-end cost (costs.py, as the engine
                                prices a plan) and the engine's own full-size plan's cost as the bar; every plan is priced
                                by the engine (verify rows: cost_usd, beats_engine_by, vs); a plan that holds but costs
                                more goes back with its price ("find a cheaper full-size plan"); the result row and
                                agentic carry outcome ('beat' | 'matched' | 'lost' | 'failed' | 'none' | 'verified'),
                                engine_cost_usd, best_cost_usd, beat_by_usd (agentic also `cached`: the calls answered
                                from the AI cache, which the result row says). Only a 'beat' ranks ahead of the
                                engine's own plan (_key), so the fix applied always agrees with the result's title.

Every plan the AI proposes is verified by the same cascade engine as every other fix: the AI never decides what
holds. Everything is an estimate on a SYNTHETIC grid model, never a real utility's network.

GRID STRAIN (user, Sat 07:43: "this project is also about reducing the grid strain data centers put on the grid"):
one steady-state solve per case (no cascade) measures how hard the lines are working: the most loaded line's
loading, how many lines run at 90 % or more of their rating, how many are over it and by how many MW in all.
Measured three ways: the grid alone, with the campus, and with each verified fix in place, so a fix shows
the strain it removes, not only that it "holds".
"""

import asyncio
import logging
import math
import re
import time

import numpy as np

log = logging.getLogger("uvicorn.error")

FULL_KEEP_PCT = 90.0  # a fix that keeps at least this share of the campus is a "full size" fix
# building it HERE comes first (strengthen the grid: the engine's upgrade or a verified AI plan, cheapest first; a small lowering plus upgrades), then somewhere else at the full size
FAMILY_PRIORITY = {"upgrade": 0, "agentic": 0, "combo": 2, "move": 3, "shrink": 4, "onsite": 5, "flexible": 6, "time_of_day": 7, "remove": 9}
MAX_LISTED = 4  # solutions shown, best first
MUST_LINES = 6  # upgrade lines spelled out in a "you have to do this" list
PLANS_ASKED = 3
MAX_PLAN_LINES = 12
MAX_RERATE = 5.0  # a re-rating tops out at 5x (the Fix it search's own cap)
AI_TIMEOUT_S = 18  # the proposer runs in the background (no request waits on it): a slow answer still counts
HOT_PCT = 90.0  # a line at or above this share of its rating is under strain
STRAIN_FIXES = 8  # fixes measured per report (one solve each)
MAX_ROUNDS = 3  # one proposal and up to two revisions after the engine's feedback
TRACE_MAX = 40  # entries kept in agentic.trace (a round is about 7: the ask, three plans and their three checks)
TRACE_LINES = 4  # upgraded lines named per proposed plan in the trace
MATCH_FRAC = 0.01  # a verified AI plan within 1 % of the engine's own plan's cost matches it; cheaper than that beats it

_running: set[str] = set()


def _b():
    import briefing  # late: briefing imports this module

    return briefing


# ------------------------------------------------------------------------------------------ describing
def _kept_mw(fx: dict, total: float) -> float | None:
    fam, d = fx.get("family"), fx.get("detail") or {}
    if fam in ("upgrade", "move"):
        return float(total)
    if fam in ("shrink", "combo", "agentic", "flexible"):
        return float(d["mw"]) if d.get("mw") is not None else None
    if fam == "onsite":
        return float(d["net_mw"]) if d.get("net_mw") is not None else None
    return None


def _label(g, bid: int, lang: str) -> str:
    b = _b()
    i = g.br_index[int(bid)]
    fs, ts = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
    a, z = b._title(g.sub_name[fs]), b._title(g.sub_name[ts])
    if fs == ts:
        return f"the {a} transformer" if lang == "en" else f"el transformador de {a}"
    return f"the {a} to {z} line" if lang == "en" else f"la línea de {a} a {z}"


def _cost_of(c, g, upgrades: dict, items: bool = False) -> dict | None:
    """What the re-ratings cost (costs.py's published per-mile and per-MVA figures), low and high; None when none.
    items=True adds the priciest re-ratings ({id, to_mva, high}, at most three) for the proposer's feedback."""
    if not upgrades:
        return None
    items_wanted = items
    try:
        import costs

        new = {int(k): float(v) for k, v in upgrades.items()}
        rate = g.rates_with(new)
        idx = sorted(g.br_index[k] for k in new if k in g.br_index)
        applied = {g.br_index[int(b)] for b in c.upgrades if int(b) in g.br_index}
        items = costs._upgrade_items(g, g.rate, rate, idx, applied)
        if not items:
            return None
        out = {"low": int(sum(it["low"] for it in items)), "high": int(sum(it["high"] for it in items)), "lines": len(items)}
        if items_wanted:
            out["items"] = [{"id": int(it["id"]), "to_mva": round(float(it["new_mva"])), "high": int(it["high"])} for it in items]
        return out
    except Exception as e:  # noqa: BLE001 — a cost that can't be computed is left out, never guessed
        log.warning("solutions: cost failed: %s", e)
        return None


def _cost_items(c, g, upgrades: dict) -> list[dict]:
    """Every element a fix upgrades, priced one by one exactly as _cost_of prices the whole (costs.py): where it is,
    what the work is and its low/high cost. The presentation pins each one on the map with its own price."""
    try:
        import costs

        new = {int(k): float(v) for k, v in (upgrades or {}).items()}
        rate = g.rates_with(new)
        idx = sorted(g.br_index[k] for k in new if k in g.br_index)
        applied = {g.br_index[int(b)] for b in c.upgrades if int(b) in g.br_index}
        out = []
        for it in costs._upgrade_items(g, g.rate, rate, idx, applied):
            i = g.br_index[int(it["id"])]
            new_line = it["kind"] == "line" and float(it["new_mva"]) > float(costs.RECONDUCTOR_MAX_RATIO) * float(it["old_mva"])
            out.append({
                "id": int(it["id"]), "kind": it["kind"], "kv": round(float(g.br_kv[i])), "miles": it.get("miles"),
                "old_mva": round(float(it["old_mva"])), "new_mva": round(float(it["new_mva"])),
                "work": "transformer" if it["kind"] == "transformer" else "new_line" if new_line else "reconductor",
                "low": int(it["low"]), "high": int(it["high"]),
            })
        return out
    except Exception as e:  # noqa: BLE001 — a price that can't be split is left out, never guessed
        log.info("solutions: cost items skipped: %s", e)
        return []


def _usd(v: float, lang: str = "en") -> str:
    """A cost as the panel prints it (features/cost/money.js): '$64 million', '$8.41 million', '$1.08 billion'."""
    v = float(v)
    for div, en_u, es_u in ((1e9, "billion", "mil millones"), (1e6, "million", "millones")):
        if v >= div:
            x = v / div
            d = 0 if x >= 100 else 1 if x >= 10 else 2
            s = f"{round(x, d):.{d}f}".rstrip("0").rstrip(".") if d else f"{x:.0f}"
            return f"${s.replace('.', ',') if lang == 'es' else s} {en_u if lang == 'en' else es_u}"
    return f"${int(round(v, -3)):,}"


def _pct(v: float, lang: str = "en") -> str:
    """A line's loading as the trace prints it: whole numbers, but one decimal close to the limit (99.7 % is inside
    its rating, 100.3 % is over it: '100 %' would read as either)."""
    v = float(v)
    s = f"{v:.1f}" if 99.5 <= v < 100.5 else f"{v:.0f}"
    return f"{s.replace('.', ',')} %" if lang == "es" else f"{s}%"


def _price_hint(g, bid: int) -> dict | None:
    """What raising one line costs at the high end, priced exactly as the engine prices a plan (costs.py): a line
    raised up to RECONDUCTOR_MAX_RATIO x its rating has one flat price (reconductor or rebuild), past that it is a
    new double-circuit line; a transformer is priced on its whole new rating. Given to Gemini so it can aim for a
    cheaper plan; the plan itself is priced again by the engine (Gemini's own numbers are never used)."""
    try:
        import costs

        i = g.br_index[int(bid)]
        old = float(g.rate[i])

        def high(to: float) -> float | None:
            r = np.array(g.rate, dtype=float, copy=True)
            r[i] = to
            items = costs._upgrade_items(g, g.rate, r, [i], set())
            return float(items[0]["high"]) if items else None

        fs, ts = int(g.bus_sub_idx[g.f[i]]), int(g.bus_sub_idx[g.t[i]])
        if fs == ts:
            h = high(old * 1.5)
            return {"kind": "transformer", "per_mva": h / (old * 1.5)} if h else None
        ratio = float(costs.RECONDUCTOR_MAX_RATIO)
        within, beyond = high(old * min(1.5, ratio)), high(old * ratio * 1.01)
        return {"kind": "line", "upto_mva": round(old * ratio), "within": within, "beyond": beyond} if within and beyond else None
    except Exception as e:  # noqa: BLE001 — a hint that can't be priced is left out
        log.info("solutions: price hint skipped for %s: %s", bid, e)
        return None


def _say_price(p: dict | None) -> str:
    if not p:
        return ""
    if p["kind"] == "transformer":
        return f"; high-end cost ${p['per_mva'] / 1e3:,.1f}k per MVA of its NEW rating (a new transformer)"
    return f"; high-end cost ${p['within'] / 1e6:,.1f}M for any new rating up to {p['upto_mva']:,} MVA, ${p['beyond'] / 1e6:,.1f}M above that (a new line)"


def _engine_bar(rep: dict) -> dict | None:
    """The bar Gemini is asked to beat: the engine's own cheapest verified plan that strengthens the grid and keeps the
    whole campus (the 'upgrade' family), with its high-end cost and the margin it leaves (margin_pct: its busiest line,
    or the grid's own busiest line without the campus when that is higher; None when not measured). None when the
    engine has no such plan."""
    best = None
    for f in rep.get("fixes") or []:
        if f.get("by", "engine") != "engine" or f.get("family") != "upgrade" or f.get("verdict") != "holds":
            continue
        hi = (f.get("cost") or {}).get("high")
        if hi and (best is None or float(hi) < best["high"]):
            peak = (f.get("strain") or {}).get("peak_pct")
            alone = ((rep.get("strain") or {}).get("grid_alone") or {}).get("peak_pct")
            margin = max(float(peak), float(alone or 0)) if peak is not None else None
            best = {"high": float(hi), "low": float((f.get("cost") or {}).get("low") or 0), "action": str(f.get("action") or ""),
                    "margin_pct": round(margin, 1) if margin is not None else None}
    return best


MARGIN_SLACK = 0.5  # percentage points over the engine's plan's busiest line still counted as the same margin


def _vs(bar: dict | None, cost: float | None, keep_pct: float, peak: float | None = None) -> str | None:
    """A verified plan against the engine's own: 'beat' (cheaper, the whole campus, the same margin), 'match' (within
    MATCH_FRAC), 'pricier', 'smaller' (cheaper but for less than the whole campus) or 'thin' (cheaper but it leaves a
    line hotter than the engine's plan leaves any: less margin, not a like-for-like win). Only 'beat' is a win."""
    if not bar or not cost:
        return None
    d = bar["high"] - float(cost)
    if abs(d) <= MATCH_FRAC * bar["high"]:
        return "match"
    if d > 0:
        if keep_pct < 99.5:
            return "smaller"
        if peak is not None and bar.get("margin_pct") is not None and float(peak) > bar["margin_pct"] + MARGIN_SLACK:
            return "thin"
        return "beat"
    return "pricier"


def _must(c, g, fx: dict, total: float) -> dict:
    """The "you have to do this" list for a fix, English and Spanish, from the engine's verified numbers."""
    fam, d, ap = fx.get("family"), fx.get("detail") or {}, fx.get("apply") or {}
    en: list[str] = []
    es: list[str] = []
    kept = fx.get("kept_mw")
    pct = fx.get("kept_pct")
    lst = d.get("list") or []
    if fam in ("combo", "agentic") and kept is not None and pct is not None and pct < 99.5:
        en.append(f"Build {kept:,.0f} MW ({pct:.0f}% of the planned {total:,.0f} MW)")
        es.append(f"Construir {kept:,.0f} MW ({pct:.0f} % de los {total:,.0f} MW previstos)")
    if fam in ("upgrade", "combo", "agentic") and lst:
        top = sorted(lst, key=lambda x: -float(x.get("added_mva") or 0))[:MUST_LINES]
        for x in top:
            try:
                en.append(f"Raise {_label(g, x['id'], 'en')} from {x['old_mva']:,.0f} to {x['new_mva']:,.0f} MVA")
                es.append(f"Aumentar {_label(g, x['id'], 'es')} de {x['old_mva']:,.0f} a {x['new_mva']:,.0f} MVA")
            except (KeyError, TypeError):
                continue
        if len(lst) > len(top):
            rest = lst[len(top):]
            add = sum(float(x.get("added_mva") or 0) for x in rest)
            en.append(f"…and {len(rest)} more upgrades ({add:,.0f} MVA in all)")
            es.append(f"…y {len(rest)} refuerzos más ({add:,.0f} MVA en total)")
    elif fam == "shrink" and kept is not None:
        en.append(f"Build {kept:,.0f} MW instead of {total:,.0f} MW ({pct:.0f}%)")
        es.append(f"Construir {kept:,.0f} MW en lugar de {total:,.0f} MW ({pct:.0f} %)")
    elif fam == "move":
        town = ((d.get("sites") or [{}])[0].get("town")) or d.get("town")
        here = c.header.get("sub_area") or "this site"
        if town:
            en.append(f"Build it at a {town} substation instead of {here}")
            es.append(f"Construirlo en una subestación de {town} en lugar de {here}")
    elif fam == "flexible" and kept is not None:
        # the engine's size at each load level where it runs lower (a 9 AM case is not "the peak hour")
        low = sorted((x for x in (d.get("levels") or []) if isinstance(x, dict) and x.get("level") is not None and not x.get("full")),
                     key=lambda x: float(x["level"]))
        for x in low[:4]:
            lf_ = float(x["level"])
            at_en, at_es = next((v for k, v in _LEVEL_AT.items() if _near(k, lf_)),
                                (f"at {round(lf_ * 100)}% of the peak load", f"al {round(lf_ * 100)} % de la carga pico"))
            en.append(f"Step down to {float(x.get('runs_mw') or 0):,.0f} MW {at_en}")
            es.append(f"Bajar a {float(x.get('runs_mw') or 0):,.0f} MW {at_es}")
        if low and len(low) < len(d.get("levels") or []):
            en.append("Run at full size the rest of the time")
            es.append("Operar a tamaño completo el resto del tiempo")
        if not low:
            en.append(f"Cut the campus to {kept:,.0f} MW at this load level")
            es.append(f"Reducir el campus a {kept:,.0f} MW en este nivel de carga")
    elif fam == "onsite" and d.get("onsite_mw"):
        en.append(f"Add {d['onsite_mw']:,.0f} MW of on-site generation (the grid supplies {d.get('net_mw', 0):,.0f} MW)")
        es.append(f"Añadir {d['onsite_mw']:,.0f} MW de generación propia (la red aporta {d.get('net_mw', 0):,.0f} MW)")
    return {"en": en, "es": es}


def _strain_of(st) -> dict:
    """How hard the lines work in one solved state: peak loading, lines under strain, lines over, overload MW."""
    act = st.active
    pct = np.where(act, st.loading_pct, 0.0)
    over = act & (pct > 100.0 + 1e-6)
    rate = st.rate if st.rate is not None else None
    over_mw = float(np.sum(np.abs(st.flow[over]) - rate[over])) if rate is not None and over.any() else 0.0
    return {
        "peak_pct": round(float(pct.max()) if pct.size else 0.0, 1),
        "hot": int(np.count_nonzero(act & (pct >= HOT_PCT))),
        "over": int(np.count_nonzero(over)),
        "over_mw": round(max(over_mw, 0.0), 1),
    }


def _strain_for_body(body: dict) -> dict | None:
    """The strain of a case given as a normalized briefing body (the case with a fix's apply delta)."""
    b = _b()
    try:
        c2 = b.build_case(b.BriefingIn(**{k: v for k, v in body.items() if v is not None or k in ("lat", "lon", "mw")}))
        st = b._solve(c2, c2.g, c2.active, c2.extra, c2.rate)
    except Exception as e:  # noqa: BLE001 — strain is an addition; a case that can't be rebuilt is left out
        log.info("solutions: strain skipped: %s", e)
        return None
    return _strain_of(st)


def _with_apply(c, ap: dict | None) -> dict | None:
    if not ap:
        return None
    body = {**c.body, **ap}
    if "sites" in ap and ap.get("mw") is None and ap.get("lat") is None:
        return None  # "don't build here": measured as the grid alone
    return body


def strain_report(c) -> dict | None:
    """The grid's strain with no campus and with the campus (before any cascade)."""
    b = _b()
    try:
        g = c.g
        st_with = b._solve(c, g, c.active, c.extra, c.rate)
        st_none = b._solve(c, g, c.active, np.zeros(g.n), c.rate)
    except Exception as e:  # noqa: BLE001
        log.info("solutions: strain report skipped: %s", e)
        return None
    return {"grid_alone": _strain_of(st_none), "with_campus": _strain_of(st_with), "hot_pct": HOT_PCT,
            "method": "One DC power-flow solve per case, before any line trips: the most loaded line, lines at 90 % or more of their rating, lines over it. Synthetic grid model; an estimate."}


def _add_strain(c, fixes: list[dict]) -> None:
    done = 0
    for fx in fixes:
        if fx.get("strain") is not None or fx.get("verdict") not in ("holds", "partly"):
            continue
        if done >= STRAIN_FIXES:
            break
        body = _with_apply(c, fx.get("apply"))
        if body is None:
            continue
        fx["strain"] = _strain_for_body(body)
        done += 1


def enrich(c, g, fixes: list[dict], total: float) -> None:
    """Add kept share, cost, provenance and the must-do list to every fix (in place), then rank."""
    for fx in fixes:
        if fx.get("by") is None:
            fx["by"] = "engine"
        kept = _kept_mw(fx, total)
        fx["kept_mw"] = round(kept, 1) if kept is not None else None
        fx["kept_pct"] = round(100.0 * kept / total, 1) if kept is not None and total else None
        ups = (fx.get("apply") or {}).get("upgrades")
        fx["cost"] = _cost_of(c, g, ups) if ups and fx.get("verdict") in ("holds", "partly") else None
        if fx["cost"]:
            fx["cost"]["items"] = _cost_items(c, g, ups)  # each element with its own price (the presentation pins them)
        fx["must"] = _must(c, g, fx, total) if fx.get("verdict") in ("holds", "partly") else {"en": [], "es": []}
    _add_strain(c, fixes)
    order = ranked(fixes)
    for fx in fixes:
        fx["rank"] = None
    for pos, i in enumerate(order, 1):
        fixes[i]["rank"] = pos


# ------------------------------------------------------------------------------------------ ranking
def _key(fx: dict) -> tuple:
    kept = fx.get("kept_pct")
    tier = 0 if kept is not None and kept >= FULL_KEEP_PCT else 1
    cost = (fx.get("cost") or {}).get("high")
    d = fx.get("detail") or {}
    # an AI plan that does not beat the engine's own like for like never ranks ahead of it: cheaper only because it
    # leaves a line hotter than the engine's plan leaves any ("thin") comes after the plans that keep that margin;
    # cheaper only because it keeps less than the whole campus ("smaller") after those (a full-size plan with less
    # margin still comes before one that builds less of the campus); within 1 % of the engine's price ("match": the
    # result calls it an alternative) right after the engine's own plan, so the engine's plan stays the one applied
    smaller = (fx.get("family") == "agentic" and kept is not None and kept < 99.5) or d.get("vs") == "smaller"
    behind = 2 if smaller else 1 if d.get("thin") or d.get("vs") == "thin" else 0
    if cost is not None and d.get("vs") == "match" and d.get("bar_usd"):
        cost = max(float(cost), float(d["bar_usd"]) + 1.0)
    # strengthening the grid (the engine's upgrade or an AI plan the engine verified): the cheapest first
    return (tier, FAMILY_PRIORITY.get(fx.get("family"), 8), behind, cost if cost is not None else float("inf"), -(kept or 0.0))


def _holds(fixes: list[dict]) -> list[int]:
    idx = [i for i, f in enumerate(fixes) if f.get("verdict") == "holds" and f.get("family") not in ("remove", "time_of_day")]
    return sorted(idx, key=lambda i: _key(fixes[i]))


def ranked(fixes: list[dict]) -> list[int]:
    """The verified fixes, best first: full-size ones (at least FULL_KEEP_PCT of the campus) before smaller ones."""
    return _holds(fixes)[:MAX_LISTED]


def best_fix(fixes: list[dict]) -> int | None:
    h = _holds(fixes)
    if h:
        return h[0]
    part = [(f["outcome"]["people"], i) for i, f in enumerate(fixes) if f.get("verdict") == "partly" and f.get("family") != "remove" and f.get("outcome")]
    return min(part)[1] if part else None


# ------------------------------------------------------------------------------------------ the presentation's order
# PROPORTIONATE (user, Sat 17:16-17:19: "we aren't going to spend $100 million to prevent something that is never
# going to happen"; "lower the amount that it says NO DATA CENTER"). The presentation leads with the cheapest verified
# way to keep the campus at full size: an operating rule when the overload only happens at the peak (the campus steps
# down on the hottest afternoons, no new equipment), else the smallest verified upgrade of the weak point. Bigger or
# pricier full-size plans follow ("if you want no step-downs"). A smaller campus, another site or on-site generation are
# listed only under "More options", and lead only when no full-size option verifies; "don't build it" never shows.
# report.solutions / best_fix stay as they are (the results panel's "Run it again with the fix" applies best_fix).
PRESENT_MAIN = 3  # options the presentation walks through one by one
MORE_ORDER = ("combo", "flexible", "onsite", "move", "shrink")  # "More options", in this order
HOURS_YEAR = 8760.0
PEAK_LF = 1.0  # the 4 PM summer peak: "only at the peak" means full size at every level below it
# Hours a year a flexible load is curtailed, as reported for Duke University's 2025 national study (costs.SOURCES
# ['duke_flex']): loads curtailed for 0.25 % of their maximum uptime, in about 85 hours a year, mostly partial. The same
# figure "Who goes dark first?" cites (service_rules.py). A national estimate, not measured for any site here.
FLEX_HOURS = 85


def _near(a: float, b: float) -> bool:
    return abs(a - b) < 0.005


# the load levels the engine checks, at the end of a sentence (briefing.LEVELS), EN / ES
_LEVEL_AT = {0.62: ("at 3 AM", "a las 3 AM"), 0.82: ("at 9 AM", "a las 9 AM"), 1.0: ("at the 4 PM summer peak", "en el pico de verano de las 4 PM"),
             1.04: ("in a heat wave", "en una ola de calor"), 1.08: ("at the height of a heat wave", "en el pico de una ola de calor")}


def _flex_info(i: int, fx: dict, total: float, lf: float) -> dict:
    """The flexible fix as an operating rule: what the campus runs at each load level the engine checked (each level's
    size is the engine's own steady-state fit at that level), whether it only has to step down at the 4 PM peak and
    above (peak_only: full size at every level below the peak) or only in a heat wave (heat_only: full size at the peak
    too), its step-down at each level it runs lower, and the compute it could give up in a year under a sourced,
    labeled assumption (Duke's reported hours; an upper bound: the largest step for every one of those hours)."""
    d = fx.get("detail") or {}
    levels = [{"level": float(x["level"]), "name": x.get("name"), "runs_mw": float(x.get("runs_mw") or 0), "full": bool(x.get("full"))}
              for x in (d.get("levels") or []) if isinstance(x, dict) and x.get("level") is not None]
    levels.sort(key=lambda x: x["level"])
    for x in levels:
        x["step_mw"] = 0.0 if x["full"] else round(max(total - x["runs_mw"], 0.0), 1)
    over = [x for x in levels if not x["full"]]
    below_peak = [x for x in levels if x["level"] < PEAK_LF - 0.005]
    to_peak = [x for x in levels if x["level"] <= PEAK_LF + 0.005]
    peak_only = bool(over) and bool(below_peak) and all(x["full"] for x in below_peak)
    heat_only = bool(over) and bool(to_peak) and all(x["full"] for x in to_peak)

    def runs_at(level: float) -> float | None:
        x = next((x for x in levels if _near(x["level"], level)), None)
        return round(x["runs_mw"] if not x["full"] else total, 1) if x else None

    case_mw = float(d.get("mw") or fx.get("kept_mw") or 0)
    step = max((x["step_mw"] for x in over), default=round(max(total - case_mw, 0.0), 1))
    src = None
    try:
        import costs

        src = costs.SOURCES.get("duke_flex")
    except Exception:  # noqa: BLE001
        src = None
    mwh = step * FLEX_HOURS
    return {
        "fix": i, "levels": levels, "peak_only": peak_only, "heat_only": heat_only,
        "steps": [{"level": x["level"], "name": x["name"], "runs_mw": round(x["runs_mw"], 1), "step_mw": x["step_mw"]} for x in over],
        "peak_mw": runs_at(PEAK_LF) if runs_at(PEAK_LF) is not None else round(case_mw, 1),
        "heat_mw": runs_at(1.04), "case_mw": round(case_mw, 1), "step_down_mw": round(step, 1),
        "hours_assumed": FLEX_HOURS, "mwh_year": round(mwh), "mwh_year_is_upper_bound": True,
        "energy_share_pct": round(100.0 * mwh / (total * HOURS_YEAR), 2) if total else None,
        "assumption": ("about 85 hours of curtailment a year, mostly partial, as reported for Duke University's 2025 national study "
                       "(loads curtailed for 0.25 % of their maximum uptime); a national estimate, not measured for this site; the "
                       "energy figure is an upper bound: the largest step-down for every one of those hours"),
        "source": src,
    }


def present_plan(rep: dict) -> dict | None:
    """The presentation's order of the verified fixes and what to weigh them against. None when nothing holds.
        main   fix indices walked through one by one (<= PRESENT_MAIN), the first is the lead
        more   the other verified fixes, listed under "More options"
        flex   the operating rule (_flex_info), when the flexible fix holds
        often  at which load levels the full campus overloads the grid (the engine's per-level check), so a price is
               weighed against how often the condition occurs: every_level (even 3 AM), peak_only (only at the 4 PM
               peak and above), heat_only (only in a heat wave); relative to the peak, whatever level the case is at
        blackout  the blackout's estimated cost {low, high} (costs.py), what the fixes prevent"""
    fixes = rep.get("fixes") or []
    case = rep.get("case") or {}
    sites = case.get("sites") or []
    total = float(sum(float(s.get("mw") or 0) for s in sites) or case.get("mw") or 0)
    lf = float(case.get("load_factor") or 1.0)
    holds = [i for i, f in enumerate(fixes) if f.get("verdict") == "holds" and f.get("family") not in ("remove", "time_of_day")]
    if not holds:
        return None
    full = sorted((i for i in holds if fixes[i].get("family") in ("upgrade", "agentic") and (fixes[i].get("kept_pct") or 0) >= 99.5),
                  key=lambda i: _key(fixes[i]))
    flex_i = next((i for i in holds if fixes[i].get("family") == "flexible"), None)
    flex = _flex_info(flex_i, fixes[flex_i], total, lf) if flex_i is not None and total else None
    main: list[int] = []
    if flex and flex["peak_only"]:
        main.append(flex_i)  # no new equipment: it only steps down on the hottest afternoons
    main += full[: PRESENT_MAIN - len(main)]

    def rest_key(i: int) -> tuple:
        f = fixes[i]
        fam = f.get("family")
        return (0 if fam in ("upgrade", "agentic") else 1 + (MORE_ORDER.index(fam) if fam in MORE_ORDER else 9), _key(f))

    rest = sorted((i for i in holds if i not in main), key=rest_key)
    if not main and rest:
        main = rest[:1]  # nothing keeps the full campus: the closest to it leads (a small lowering first)
        rest = rest[1:]
    often = None
    tod = next((f for f in fixes if f.get("family") == "time_of_day"), None)
    lv = [x for x in ((tod or {}).get("detail") or {}).get("levels") or [] if isinstance(x, dict) and x.get("level") is not None]
    if lv:
        lv = sorted(({"level": float(x["level"]), "name": x.get("name"), "over": not x.get("holds")} for x in lv), key=lambda x: x["level"])
    elif flex:
        lv = [{"level": x["level"], "name": x["name"], "over": not x["full"]} for x in flex["levels"]]
    if lv:
        over = [x for x in lv if x["over"]]
        below_peak = [x for x in lv if x["level"] < PEAK_LF - 0.005]
        to_peak = [x for x in lv if x["level"] <= PEAK_LF + 0.005]
        often = {"levels": lv, "over_at": [x["name"] for x in over], "every_level": len(over) == len(lv),
                 "peak_only": bool(over) and bool(below_peak) and not any(x["over"] for x in below_peak),
                 "heat_only": bool(over) and bool(to_peak) and not any(x["over"] for x in to_peak),
                 "lowest": lv[0]["name"], "case_level": lf}
    cost = rep.get("cost") or {}
    rng = (cost.get("ranges") or {}).get("blackout_usd") or []
    blackout = None
    if cost.get("blackout_high_usd") or rng:
        blackout = {"low": float(rng[0]) if rng else None, "high": float(cost.get("blackout_high_usd") or (rng[1] if len(rng) > 1 else 0))}
    return {"main": main, "more": rest, "flex": flex, "often": often, "blackout": blackout, "total_mw": total}


# ------------------------------------------------------------------------------------------ the AI proposer
def kick(key: str) -> None:
    """Start the proposer for a cached report, once, in the background. Safe to call on every request."""
    from llm import configured

    rep = _b().report_by_key(key)
    if rep is None or rep.get("agentic") is not None:
        return
    if key in _running:  # a rebuilt report for a case whose proposer is already running: say so (the result lands in it too)
        rep["agentic"] = {"status": "running", "added": 0}
        return
    has_campus = bool((rep.get("case") or {}).get("sites") or (rep.get("case") or {}).get("mw"))
    if not configured() or not has_campus or rep.get("verdict") not in ("preventable", "partly") or not rep.get("fixes"):
        rep["agentic"] = {"status": "off", "added": 0}
        return
    rep["agentic"] = {"status": "running", "added": 0}
    _running.add(key)
    try:
        asyncio.get_running_loop().create_task(_run(key))
    except RuntimeError:  # no loop (called from a thread): nothing to schedule
        _running.discard(key)
        rep["agentic"] = {"status": "off", "added": 0}


async def _run(key: str) -> None:
    try:
        await propose(key)
    except Exception as e:  # noqa: BLE001 — the AI step is a bonus; the engine's fixes stand alone
        log.warning("solutions: proposer failed: %s", e, exc_info=True)
        rep = _b().report_by_key(key)
        if rep is not None:
            rep["agentic"] = {"status": "error", "added": 0}
    finally:
        _running.discard(key)


def _candidates(rep: dict, c, extra_ids: list[int] | None = None) -> list[dict]:
    """The lines the AI may re-rate: the ones that tripped in the incident (in order), then the hottest, with their rating."""
    b = _b()
    g = c.g
    ids: list[int] = []
    tripped_at: dict[int, int] = {}
    loaded: dict[int, float] = {}
    # the lines already over their limit with the campus on (the constraints that bind first), hottest first
    st0 = b._solve(c, g, c.active, c.extra, c.rate)
    over0 = b._over(st0)
    for i in over0[np.argsort(-st0.loading_pct[over0])][:12]:
        bid = int(g.br_ids[i])
        loaded[bid] = float(st0.loading_pct[i])
        ids.append(bid)
    for st in (rep.get("replay") or {}).get("steps", []):
        for bid in st.get("tripped") or []:
            if int(bid) in g.br_index and int(bid) not in tripped_at:
                tripped_at[int(bid)] = int(st.get("n") or 0)
                if int(bid) not in ids:
                    ids.append(int(bid))
    steps = (rep.get("replay") or {}).get("steps", [])
    for st in steps[:2]:
        for h in st.get("hot") or []:
            bid = int(h["id"])
            if bid in g.br_index and bid not in ids:
                ids.append(bid)
    for bid in extra_ids or []:
        if int(bid) in g.br_index and int(bid) not in ids:
            ids.append(int(bid))
    out = []
    for bid in ids[:22]:
        i = g.br_index[bid]
        out.append({"id": bid, "label": _label(g, bid, "en"), "kv": float(g.br_kv[i]), "mva": round(float(g.rate[i])), "tripped_at": tripped_at.get(bid), "loaded_pct": loaded.get(bid),
                    "price": _price_hint(g, bid)})
    return out


def _prompt(rep: dict, c, cands: list[dict], have: list[str], feedback: str = "", bar: dict | None = None) -> str:
    total = float(sum(s.mw for s in c.sites))
    where = c.header.get("sub_area") or "the site"
    lines = "\n".join(
        f"- {x['id']}: {x['label']}, {x['kv']:.0f} kV, {x['mva']} MVA now"
        + (f", already at {x['loaded_pct']:.0f}% of its rating with the data center on" if x.get("loaded_pct") else "")
        + (f", tripped in step {x['tripped_at']}" if x["tripped_at"] else "")
        + _say_price(x.get("price"))
        for x in cands
    )
    # "beat the engine" is the goal: the engine's own full-size plan and its high-end cost are the bar
    goal = (
        f"\n\nTHE BAR TO BEAT: the engine's own plan ({bar['action']}) holds at the full size for {_usd(bar['high'])} (high end). "
        "Your goal is a plan that also holds with keep_pct 100 and costs LESS: the fewest, cheapest changes that hold"
        + (f", with the same margin: no line above {bar['margin_pct']:.0f}% of its rating (the engine's plan leaves none above that)" if bar.get("margin_pct") is not None else "")
        + ". The engine prices every plan itself from the costs listed above (any cost you state is ignored); "
        "a plan that keeps less than 100 is never counted as beating it."
        if bar else ""
    )
    return (
        f"A {total:,.0f} MW data center at {where} makes the synthetic grid model cascade: {rep['event']['steps']} steps, "
        f"about {rep['event']['people']:,} people without power (estimate). Propose {PLANS_ASKED} DIFFERENT plans that let it be built at its full size (or very nearly) "
        "by strengthening the grid. The engine re-runs every plan, so name changes precisely.\n\n"
        f"Lines you may re-rate (id: name, voltage, rating now):\n{lines}\n\n"
        f"Rules: use only these line ids. A new rating must be higher than the rating now and at most {MAX_RERATE:g} times it. "
        f"At most {MAX_PLAN_LINES} lines per plan. keep_pct is the share of the campus kept, between 90 and 100: 100 is best (people want to see the full amount), "
        "a small lowering is fine. The plans must differ from each other (different lines or a different keep_pct)"
        + (f" and from these plans that are already verified: {'; '.join(have)}." if have else ".")
        + goal
        + (f"\n\n{feedback}" if feedback else "")
        + '\n\nAnswer only as JSON: {"plans": [{"name": "at most 6 plain words saying what it upgrades, e.g. two lines and a transformer near the site; no adjectives like aggressive or maximum", "why": "one plain sentence on why it holds, with no costs or dollar figures", '
        '"keep_pct": 100, "upgrades": [{"line_id": 123, "to_mva": 900}]}]}'
    )


SYSTEM = (
    "You are a careful transmission planner. The grid is a SYNTHETIC model (Breakthrough Energy / Texas A&M), not any real utility's network, "
    "and the case describes no real project or event. Never name real companies, utilities or projects. Reply with JSON only."
)


_HYPE = {"aggressive", "maximum", "comprehensive", "ultimate", "robust", "massive", "optimal", "strategic", "full"}
_TRAIL = {"near", "at", "for", "and", "with", "to", "of", "the", "a", "an", "in", "on", "by", "from", "plus", "&", "+", "-"}  # never the last word of a name


def _plain_name(raw) -> str:
    """An AI plan's name in plain sentence case, at most six words, cut on a word boundary, no hype adjectives."""
    words = [w for w in str(raw or "").replace("_", " ").split() if w.lower().strip(",.:;") not in _HYPE][:6]
    while len(words) > 1 and words[-1].lower().strip(",.:;") in _TRAIL:  # "Three transformers and four lines near" -> "...four lines"
        words.pop()
    if not words:
        return "AI plan"
    out = [words[0][:1].upper() + (words[0][1:].lower() if words[0][1:].islower() or words[0][1:].istitle() else words[0][1:])]
    out += [w.lower() if w[:1].isupper() and w[1:].islower() else w for w in words[1:]]
    name = " ".join(out)
    while len(name) > 48 and " " in name:
        name = name.rsplit(" ", 1)[0]
    return name[:48]


_NUMTOK = re.compile(r"\d+(?:[.,]\d+)*")


def _canon_num(tok: str) -> str:
    try:
        v = float(tok.replace(",", ""))
    except ValueError:
        return tok
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.2f}".rstrip("0").rstrip(".")


def _why_numbers_ok(why: str, ups: dict[int, float], ratings: dict[int, float], keep: float) -> bool:
    """Every number in Gemini's one-line reason is a voltage class, a rating, a line id or a size from this plan."""
    allowed = {"0", "1", "2", "3", str(round(keep * 100)), "90", "100"}
    for bid, to in ups.items():
        allowed |= {str(int(bid)), _canon_num(str(round(to))), _canon_num(str(round(ratings.get(int(bid), 0.0))))}
    allowed |= {"69", "115", "138", "161", "230", "345", "500", "765"}  # the model's voltage classes (kV)
    return all(_canon_num(t) in allowed for t in _NUMTOK.findall(why))


_places: dict[int, list] = {}


def _proper_places(name: str, g) -> str:
    """Sentence case lowered the town names ("Upgrade north fort myers transformer"): put them back ("North Fort Myers")."""
    from powerflow import area_of

    pats = _places.get(id(g))
    if pats is None:
        areas = sorted({area_of(n) for n in g.sub_name if area_of(n)}, key=len, reverse=True)
        pats = [(re.compile(r"\b" + re.escape(a) + r"\b", re.I), a) for a in areas if len(a) >= 4][:600]
        _places[id(g)] = pats
    low = name.lower()
    for rx, a in pats:
        if a.lower() in low:
            name = rx.sub(a, name)
    return name


def _clean_plan(plan, g, ratings: dict[int, float]) -> tuple[dict[int, float], float, str, str] | None:
    """(upgrades {id: MVA}, keep fraction, name, why) from one AI plan, or None when it isn't usable."""
    if not isinstance(plan, dict):
        return None
    ups: dict[int, float] = {}
    for u in plan.get("upgrades") or []:
        try:
            bid, to = int(u["line_id"]), float(u["to_mva"])
        except (KeyError, TypeError, ValueError):
            continue
        cur = ratings.get(bid)
        if cur is None or not math.isfinite(to) or to <= cur + 1e-6:
            continue
        ups[bid] = round(min(to, cur * MAX_RERATE), 1)
        if len(ups) >= MAX_PLAN_LINES:
            break
    try:
        keep = float(plan.get("keep_pct", 100))
    except (TypeError, ValueError):
        keep = 100.0
    keep = min(100.0, max(FULL_KEEP_PCT, keep)) / 100.0
    name = _proper_places(_plain_name(plan.get("name")), g)
    why = str(plan.get("why") or "").strip()[:200]
    if why and not _why_numbers_ok(why, ups, ratings, keep):
        why = ""  # a reason is shown only when every number in it is one the case gave Gemini (or the plan itself)
    if not ups and keep >= 0.999:
        return None
    return ups, keep, name, why


def _check(c, J, plan: tuple, base_ups: dict) -> tuple[dict | None, str, dict]:
    """Run one plan through the engine: (a fix dict if it holds, else None), the feedback sentence for Gemini, and
    what the engine saw (for the trace): {holds, verdict, steps, people, over: [{id, pct, mva}], peak_pct, how, ms}."""
    b = _b()
    g = c.g
    t0 = time.perf_counter()
    ups_new, keep, name, why = plan
    if not c.sites:  # nothing to keep at full size: the proposer is for a data center's case
        return None, f"Plan '{name}' skipped: this case has no data center.", {"holds": False, "skipped": True}
    upgrades = {**{int(k): float(v) for k, v in base_ups.items()}, **ups_new}
    mws = b._site_mws(c, keep)
    total = float(sum(s.mw for s in c.sites))
    extra = b._extra_for(g, c.buses, mws)
    oc, verdict, how = b._verify(c, J, g, extra, upgrades)
    rate = g.rates_with(upgrades)
    ok, st = b._fits(c, g, extra, rate)
    act = st.active
    peak = float(np.max(np.where(act, st.loading_pct, 0.0))) if st.loading_pct.size else 0.0
    seen = {"holds": verdict == "holds", "verdict": verdict, "steps": int(oc["steps"]), "people": int(oc["people"]), "over": [],
            "peak_pct": round(peak, 1), "how": how, "mw": round(float(sum(mws)), 1), "keep_pct": round(keep * 100, 1)}
    # the engine prices the plan itself (costs.py's published figures, the way every fix is priced), holding or not
    cost = _cost_of(c, g, upgrades, items=True)
    seen["cost_items"] = cost.pop("items", []) if cost else []
    seen["cost_usd"] = int(cost["high"]) if cost else None
    seen["cost_low_usd"] = int(cost["low"]) if cost else None
    if verdict != "holds":
        over = []
        if not ok:
            hot = b._over(st)
            for i in hot[np.argsort(-st.loading_pct[hot])][:5]:
                over.append(f"{int(g.br_ids[i])}: {b._line(g, int(i))['label']} at {st.loading_pct[i]:.0f}% of {rate[i]:.0f} MVA (it carries {st.loading_pct[i] * rate[i] / 100:,.0f} MVA)")
                seen["over"].append({"id": int(g.br_ids[i]), "pct": round(float(st.loading_pct[i]), 1), "mva": round(float(rate[i]))})
            seen["over_count"] = int(len(hot))
        fb = f"Plan '{name}' did NOT hold: after {oc['steps']} cascade steps {oc['people']:,} people (estimate) were still without power."
        if over:
            fb += " Lines still over their limit: " + "; ".join(over) + "."
        seen["ms"] = round((time.perf_counter() - t0) * 1000)
        return None, fb, seen
    # the plan holds: its most loaded lines (the margin it leaves, set against the engine's own plan's)
    ld = np.where(act, st.loading_pct, 0.0)
    seen["top"] = [{"id": int(g.br_ids[i]), "pct": round(float(ld[i]), 1), "mva": round(float(rate[i]))} for i in np.argsort(-ld)[:3] if ld[i] > 0]
    # how hard each upgrade it made works (an upgrade at 50 % is oversized: where a cheaper plan can save), priciest first
    priced = {it["id"]: it["high"] for it in seen.get("cost_items") or []}
    seen["upgraded"] = sorted(
        ({"id": int(k), "to_mva": round(float(v)), "pct": round(float(ld[g.br_index[int(k)]]), 1), "high": priced.get(int(k))} for k, v in ups_new.items() if int(k) in g.br_index),
        key=lambda u: -(u["high"] or 0),
    )[:6]
    chosen = sorted(g.br_index[k] for k in ups_new)
    lst, mva, km = b._upgrade_list(g, g.rate, rate, chosen)
    new_total = float(sum(mws))
    pct = round(100 * new_total / total) if total else 100
    what = b._what_upgraded(lst)
    action = f"{name} ({what}" + (f", +{mva:,.0f} MVA)" if lst else ")")
    trade = f"Proposed by AI, then re-run by the engine: it holds. {why}".strip()
    detail = {"mw": new_total, "kept_pct": pct, "lines": len(lst), "mva": mva, "km": km, "list": lst[:20], "checked_by": how, "why": why, "name": name}
    ap = {**b._size_apply(c, mws), "upgrades": {str(k): float(v) for k, v in upgrades.items()}}
    fx = b._fix("agentic", action, "holds", oc, trade, detail, ap, 0)
    fx["by"] = "gemini"
    fx["cost"] = cost  # (enrich prices it again, the same way)
    seen["ms"] = round((time.perf_counter() - t0) * 1000)
    return fx, "", seen


def _same(fx: dict, others: list[dict], cost: float | None = None) -> bool:
    """A plan already listed (same upgraded lines at the same size) is not a new solution, unless it raises them to
    other ratings for a different price (`cost`, high end): the same lines for less is a cheaper plan."""
    ids = set((fx.get("apply") or {}).get("upgrades") or {})
    for o in others:
        if set((o.get("apply") or {}).get("upgrades") or {}) == ids and abs(float(o.get("kept_pct") or 100) - float(fx["detail"]["kept_pct"])) < 1.0:
            oc = (o.get("cost") or {}).get("high")
            if cost is not None and oc and abs(float(oc) - float(cost)) > MATCH_FRAC * max(float(oc), float(cost)):
                continue
            return True
    return False


# ------------------------------------------------------------------------------------------ the trace
def _n(x) -> str:
    return f"{float(x):,.0f}"


def _people_say(n: int, lang: str) -> str:
    n = int(n)
    if n <= 0:
        return "nobody without power" if lang == "en" else "nadie sin luz"
    return f"about {n:,} people without power (estimate)" if lang == "en" else f"unas {n:,} personas sin luz (estimación)".replace(",", ".")


def _steps(n: int, lang: str) -> str:
    n = int(n)
    if lang == "en":
        return f"{n} cascade {'step' if n == 1 else 'steps'}"
    return f"{n} {'paso' if n == 1 else 'pasos'} de cascada"


def _plan_lines(g, ups_new: dict[int, float], ratings: dict[int, float]) -> tuple[list[dict], float]:
    """The re-rated lines of a plan (biggest raise first) and the MVA it adds in all."""
    rows = []
    for bid, to in ups_new.items():
        cur = float(ratings.get(int(bid), 0.0))
        rows.append({"id": int(bid), "label": _label(g, int(bid), "en"), "label_es": _label(g, int(bid), "es"), "from_mva": round(cur), "to_mva": round(float(to))})
    rows.sort(key=lambda r: -(r["to_mva"] - r["from_mva"]))
    return rows, round(sum(r["to_mva"] - r["from_mva"] for r in rows))


def _say_raise(rows: list[dict], lang: str) -> str:
    en = lang == "en"
    shown = [
        (f"{r['label']} from {_n(r['from_mva'])} to {_n(r['to_mva'])} MVA" if en else f"{r['label_es']} de {_n(r['from_mva'])} a {_n(r['to_mva'])} MVA")
        for r in rows[:2]
    ]
    rest = len(rows) - len(shown)
    tail = (f" and {rest} more" if en else f" y {rest} más") if rest > 0 else ""
    return ("Raise " if en else "Aumentar ") + "; ".join(shown) + tail if shown else ""


class _Trace:
    """agentic.trace: one entry per thing that happened, in order. Written live (the report's agentic dict holds
    the same list), capped at TRACE_MAX with the last slot kept for the result."""

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, *, final: bool = False, **row) -> None:
        if len(self.rows) >= TRACE_MAX - (0 if final else 1):
            if not final:
                return
            self.rows.pop()
        row["n"] = len(self.rows) + 1
        self.rows.append(row)

    def ask(self, rnd: int, rep: dict, c, cands: list[dict], bar: dict | None = None) -> None:
        total = float(sum(s.mw for s in c.sites))
        where = c.header.get("sub_area") or ""
        ev = rep.get("event") or {}
        over = sum(1 for x in cands if x.get("loaded_pct"))
        goal_en = f" The bar to beat: the engine's own plan, {_usd(bar['high'])} (high end)." if bar else ""
        goal_es = f" La meta: superar el plan del propio motor, {_usd(bar['high'], 'es')} (extremo alto)." if bar else ""
        self.add(round=rnd, actor="engine", kind="ask", tone="info",
                 title={"en": f"Sent Gemini the case: {_n(total)} MW" + (f" at {where}" if where else "") + f", {_steps(int(ev.get('steps') or 0), 'en')}",
                        "es": f"Le pasó a Gemini el caso: {_n(total)} MW" + (f" en {where}" if where else "") + f", {_steps(int(ev.get('steps') or 0), 'es')}"},
                 detail={"en": f"{_people_say(int(ev.get('people') or 0), 'en').capitalize()}. Asked for {PLANS_ASKED} plans that keep the full size, from {len(cands)} lines it may re-rate ({over} already over their limit).{goal_en}",
                         "es": f"{_people_say(int(ev.get('people') or 0), 'es').capitalize()}. Le pidió {PLANS_ASKED} planes que mantengan el tamaño completo, con {len(cands)} líneas que puede reforzar ({over} ya sobre su límite).{goal_es}"},
                 lines_offered=len(cands), engine_cost_usd=int(bar["high"]) if bar else None)

    def proposed(self, rnd: int, revised: bool, name: str, why: str, keep: float, rows: list[dict], mva: float) -> None:
        pct = round(keep * 100)
        self.add(round=rnd, actor="gemini", kind="revise" if revised else "propose", tone="info", plan=name, keep_pct=pct,
                 lines=[{k: r[k] for k in ("id", "label", "from_mva", "to_mva")} for r in rows[:TRACE_LINES]], lines_total=len(rows), mva_added=mva,
                 title={"en": ("Revised: " if revised else "Proposed: ") + name, "es": ("Revisó: " if revised else "Propuso: ") + name},
                 detail={"en": f"{_say_raise(rows, 'en')} (+{_n(mva)} MVA in all); keep {pct}% of the campus." + (f" {why}" if why else ""),
                         "es": f"{_say_raise(rows, 'es')} (+{_n(mva)} MVA en total); conservar el {pct} % del campus."})

    def unusable(self, rnd: int, raw_name: str) -> None:
        name = _plain_name(raw_name)
        self.add(round=rnd, actor="engine", kind="skip", tone="muted",
                 title={"en": f"Skipped: {name}", "es": f"Descartado: {name}"},
                 detail={"en": "It named no line on the list with a higher rating, so there was nothing to run.",
                         "es": "No nombró ninguna línea de la lista con una capacidad mayor, así que no había nada que probar."})

    def verified(self, rnd: int, g, seen: dict, duplicate: bool, bar: dict | None = None, vs: str | None = None) -> None:
        cost = seen.get("cost_usd")
        # the engine's own price for the plan, and (holding plans) how it compares with the engine's own plan
        priced = {"cost_usd": cost, "cost_low_usd": seen.get("cost_low_usd"), "keep_pct": seen.get("keep_pct"),
                  "engine_cost_usd": int(bar["high"]) if bar else None, "margin_pct": bar.get("margin_pct") if bar else None,
                  "beats_engine_by": int(round(bar["high"] - cost)) if bar and cost and seen.get("holds") else None, "vs": vs}
        if seen.get("holds") and seen.get("top"):  # the plan's most loaded lines (its margin), named
            priced["top"] = [{**t, "label": _label(g, t["id"], "en")} for t in seen["top"]]
        if seen.get("holds"):
            en = f"No line trips and {_people_say(seen['people'], 'en')}; the busiest line runs at {_pct(seen['peak_pct'])} of its rating."
            es = f"Ninguna línea se dispara y {_people_say(seen['people'], 'es')}; la línea más cargada va al {_pct(seen['peak_pct'], 'es')} de su capacidad."
            if cost:
                en += f" The engine prices it at {_usd(cost)} (high end)"
                es += f" El motor lo valora en {_usd(cost, 'es')} (extremo alto)"
                d = abs(bar["high"] - cost) if bar else 0
                if vs == "beat":
                    en += f": {_usd(d)} under its own plan ({_usd(bar['high'])})."
                    es += f": {_usd(d, 'es')} menos que su propio plan ({_usd(bar['high'], 'es')})."
                elif vs == "match":
                    en += f", the same as its own plan ({_usd(bar['high'])})."
                    es += f", lo mismo que su propio plan ({_usd(bar['high'], 'es')})."
                elif vs == "pricier":
                    en += f": {_usd(d)} more than its own plan ({_usd(bar['high'])})."
                    es += f": {_usd(d, 'es')} más que su propio plan ({_usd(bar['high'], 'es')})."
                elif vs == "smaller":
                    en += f", less than its own plan ({_usd(bar['high'])}) but for {seen.get('keep_pct', 100):.0f}% of the campus, so it does not beat it."
                    es += f", menos que su propio plan ({_usd(bar['high'], 'es')}) pero para el {seen.get('keep_pct', 100):.0f} % del campus, así que no lo supera."
                elif vs == "thin":
                    en += (f", less than its own plan ({_usd(bar['high'])}), but it leaves a line at {_pct(seen['peak_pct'])} of its rating where the engine's plan "
                           f"leaves none above {_pct(bar['margin_pct'])}: less margin, so it does not beat it.")
                    es += (f", menos que su propio plan ({_usd(bar['high'], 'es')}), pero deja una línea al {_pct(seen['peak_pct'], 'es')} de su capacidad donde el plan del motor "
                           f"no deja ninguna por encima del {_pct(bar['margin_pct'], 'es')}: menos margen, así que no lo supera.")
                else:
                    en += "."
                    es += "."
            if duplicate:
                en += " Same as a plan already listed, so it is not added twice."
                es += " Es igual a un plan ya listado, así que no se añade dos veces."
            self.add(round=rnd, actor="engine", kind="verify", tone="holds", holds=True, duplicate=duplicate, people=seen["people"], steps=seen["steps"],
                     peak_pct=seen["peak_pct"], ms=seen.get("ms"), **priced,
                     title={"en": "Engine re-ran the case: it holds", "es": "El motor repitió el caso: aguanta"}, detail={"en": en, "es": es})
            return
        over = [{**o, "label": _label(g, o["id"], "en"), "label_es": _label(g, o["id"], "es")} for o in seen.get("over") or []]
        en = f"{_steps(seen.get('steps', 0), 'en').capitalize()}, {_people_say(seen.get('people', 0), 'en')}."
        es = f"{_steps(seen.get('steps', 0), 'es').capitalize()}, {_people_say(seen.get('people', 0), 'es')}."
        if over:
            en += " Still over: " + "; ".join(f"{o['label']} at {_pct(o['pct'])} of {_n(o['mva'])} MVA" for o in over[:2]) + (f" (+{len(over) - 2} more)" if len(over) > 2 else "") + "."
            es += " Siguen sobre su límite: " + "; ".join(f"{o['label_es']} al {_pct(o['pct'], 'es')} de {_n(o['mva'])} MVA" for o in over[:2]) + (f" (y {len(over) - 2} más)" if len(over) > 2 else "") + "."
        self.add(round=rnd, actor="engine", kind="verify", tone="over", holds=False, people=seen.get("people", 0), steps=seen.get("steps", 0),
                 over=[{k: o[k] for k in ("id", "label", "pct", "mva")} for o in over[:5]], over_count=seen.get("over_count", len(over)), ms=seen.get("ms"), **priced,
                 title={"en": "Engine re-ran the case: it fails", "es": "El motor repitió el caso: falla"}, detail={"en": en, "es": es})

    def feedback(self, rnd: int, failed: int, extra_ids: list[int], pricier: int = 0, bar: dict | None = None) -> None:
        parts_en, parts_es = [], []
        if failed:
            parts_en.append(f"{failed} {'plan' if failed == 1 else 'plans'} failed")
            parts_es.append(f"{failed} {'plan falló' if failed == 1 else 'planes fallaron'}")
        if pricier:
            parts_en.append(f"{pricier} held but did not beat the engine's {_usd(bar['high'])}" if bar else f"{pricier} held")
            parts_es.append(f"{pricier} {'aguantó' if pricier == 1 else 'aguantaron'} sin superar los {_usd(bar['high'], 'es')} del motor" if bar else f"{pricier} aguantaron")
        det_en = "The lines still over their limit and by how much, with those lines added to the ones it may re-rate." if failed else ""
        det_es = "Las líneas que siguen sobre su límite y por cuánto, añadidas a las que puede reforzar." if failed else ""
        if pricier and bar:
            det_en = (det_en + " " if det_en else "") + f"For each plan that held, its price against the engine's own plan ({_usd(bar['high'])}): find a cheaper full-size plan."
            det_es = (det_es + " " if det_es else "") + f"Para cada plan que aguantó, su precio frente al plan del motor ({_usd(bar['high'], 'es')}): buscar un plan completo más barato."
        self.add(round=rnd, actor="engine", kind="feedback", tone="info", failed=failed, pricier=pricier, lines_added=len(extra_ids),
                 engine_cost_usd=int(bar["high"]) if bar else None,
                 title={"en": f"Sent the engine's findings back to Gemini ({', '.join(parts_en)})",
                        "es": f"Le devolvió a Gemini lo que encontró el motor ({', '.join(parts_es)})"},
                 detail={"en": f"{det_en} Asked it to revise.".strip(), "es": f"{det_es} Le pidió que revise.".strip()})

    def offline(self, rnd: int) -> None:
        self.add(round=rnd, actor="gemini", kind="offline", tone="muted",
                 title={"en": "Gemini did not answer" if rnd == 1 else "Gemini did not answer the revision",
                        "es": "Gemini no respondió" if rnd == 1 else "Gemini no respondió a la revisión"},
                 detail={"en": "Unavailable or too slow: the engine's own ways to build it stand.", "es": "No disponible o demasiado lento: quedan las soluciones del propio motor."})

    def result(self, asked: int, verified: int, rounds: int, calls: int, ms: int, bar: dict | None = None, best: dict | None = None, cached: int = 0) -> dict:
        """The last row. With the engine's own plan as the bar, its title is the honest end of the contest: Gemini's
        cheapest verified plan against the engine's. Returns the outcome fields (also kept on agentic). `cached`: how
        many of the calls were answered from the AI cache (an answer Gemini gave when this case first ran): said so,
        so a replayed run's timing never reads as Gemini answering in a fraction of a second."""
        counts_en = f"{verified} of {asked} AI {'plan' if asked == 1 else 'plans'} verified and added" if asked else "No AI plan to add"
        counts_es = f"{verified} de {asked} {'plan' if asked == 1 else 'planes'} de IA verificados y añadidos" if asked else "Ningún plan de IA que añadir"
        rounds_en = f"{rounds} {'round' if rounds == 1 else 'rounds'}"
        rounds_es = f"{rounds} {'ronda' if rounds == 1 else 'rondas'}"
        tail_en = "Only plans the engine re-ran and found holding are listed."
        tail_es = "Solo se listan los planes que el motor repitió y aguantan."
        if calls and cached >= calls:
            run_en = (f"{rounds_en}; {calls} Gemini {'answer' if calls == 1 else 'answers'} kept from when this case first ran, "
                      f"every plan re-run by the engine just now ({ms / 1000:.1f} s). {tail_en}")
            run_es = (f"{rounds_es}; {calls} {'respuesta' if calls == 1 else 'respuestas'} de Gemini guardadas de cuando se corrió este caso por primera vez, "
                      f"cada plan repetido por el motor ahora mismo ({ms / 1000:.1f} s). {tail_es}")
        else:
            got_en = f" ({cached} answered from the cache)" if cached else ""
            got_es = f" ({cached} respondidas desde la caché)" if cached else ""
            run_en = f"{rounds_en}, {calls} Gemini {'call' if calls == 1 else 'calls'}{got_en}, {ms / 1000:.1f} s. {tail_en}"
            run_es = f"{rounds_es}, {calls} {'llamada' if calls == 1 else 'llamadas'} a Gemini{got_es}, {ms / 1000:.1f} s. {tail_es}"
        out = {"outcome": "none" if not asked else None, "engine_cost_usd": int(bar["high"]) if bar else None,
               "best_cost_usd": int(best["cost"]) if best else None,
               "beat_by_usd": int(round(bar["high"] - best["cost"])) if bar and best and best.get("vs") == "beat" else None}
        title_en, title_es = counts_en, counts_es
        detail_en, detail_es = run_en, run_es
        if asked and bar:
            e_en, e_es = _usd(bar["high"]), _usd(bar["high"], "es")
            if not best:
                out["outcome"] = "failed"
                title_en = f"No Gemini plan held; the engine's own plan stands ({e_en})"
                title_es = f"Ningún plan de Gemini aguantó; queda el plan del propio motor ({e_es})"
            elif best.get("vs") == "beat":
                out["outcome"] = "beat"
                title_en = f"Gemini's plan: {_usd(best['cost'])}, {_usd(bar['high'] - best['cost'])} under the engine's own"
                title_es = f"El plan de Gemini: {_usd(best['cost'], 'es')}, {_usd(bar['high'] - best['cost'], 'es')} menos que el del propio motor"
            elif best.get("vs") == "match":
                out["outcome"] = "matched"
                title_en = f"Gemini matched the engine ({_usd(best['cost'])}); its plans are listed as alternatives"
                title_es = f"Gemini igualó al motor ({_usd(best['cost'], 'es')}); sus planes quedan como alternativas"
            elif best.get("vs") == "thin":
                out["outcome"] = "lost"
                title_en = (f"Gemini didn't beat the engine: its {_usd(best['cost'])} plan runs a line at {_pct(best['peak_pct'])} of its rating, "
                            f"the engine's {e_en} plan none above {_pct(bar['margin_pct'])}; its plans are listed as alternatives")
                title_es = (f"Gemini no superó al motor: su plan de {_usd(best['cost'], 'es')} deja una línea al {_pct(best['peak_pct'], 'es')} de su capacidad, "
                            f"el del motor ({e_es}) ninguna por encima del {_pct(bar['margin_pct'], 'es')}; sus planes quedan como alternativas")
            elif best.get("vs") == "smaller":
                out["outcome"] = "lost"
                kp = float(best.get("keep_pct") or 100)
                title_en = (f"Gemini didn't beat the engine: its cheapest plan, {_usd(best['cost'])}, builds only {kp:.0f}% of the campus; "
                            f"the engine's {e_en} plan builds all of it; its plans are listed as alternatives")
                title_es = (f"Gemini no superó al motor: su plan más barato, {_usd(best['cost'], 'es')}, construye solo el {kp:.0f} % del campus; "
                            f"el del motor ({e_es}) lo construye entero; sus planes quedan como alternativas")
            elif best.get("vs") == "pricier":
                out["outcome"] = "lost"
                more = best["cost"] - bar["high"]
                title_en = (f"Gemini didn't beat the engine: its cheapest plan that holds costs {_usd(best['cost'])}, "
                            f"{_usd(more)} more than the engine's {e_en}; its plans are listed as alternatives")
                title_es = (f"Gemini no superó al motor: su plan más barato que aguanta cuesta {_usd(best['cost'], 'es')}, "
                            f"{_usd(more, 'es')} más que los {e_es} del motor; sus planes quedan como alternativas")
            else:
                out["outcome"] = "lost"
                title_en = f"Gemini didn't beat the engine: its best, {_usd(best['cost'])}, against {e_en}; its plans are listed as alternatives"
                title_es = f"Gemini no superó al motor: su mejor plan, {_usd(best['cost'], 'es')}, frente a {e_es}; sus planes quedan como alternativas"
            detail_en, detail_es = f"{counts_en}. {run_en}", f"{counts_es}. {run_es}"
        elif asked:
            out["outcome"] = "verified" if verified else "failed"
        self.add(final=True, round=rounds, actor="engine", kind="result", tone="holds" if verified else "muted", **out,
                 title={"en": title_en, "es": title_es}, detail={"en": detail_en, "es": detail_es})
        return out


def _in_ai_cache(prompt: str, model: str | None) -> bool:
    """Whether complete_json will answer this prompt from the AI cache (an answer Gemini gave earlier for the same
    prompt), checked the way llm.py keys and ages its cache. Read-only: never moves or drops an entry."""
    try:
        import llm

        hit = llm._cache.get(llm._cache_key(prompt, SYSTEM, True, None, model))
        return bool(hit) and time.time() - float(hit[0]) <= llm.CACHE_TTL_S
    except Exception:  # noqa: BLE001 — only the wording of the run's summary depends on it
        return False


def _live(rep: dict, key: str, **fields) -> None:
    """Update the running proposer's status on the report (and a rebuilt copy under the same key) so a page
    polling it sees the trace grow."""
    b = _b()
    for r in {id(rep): rep, id(b.report_by_key(key) or rep): b.report_by_key(key) or rep}.values():
        ag = r.get("agentic")
        if isinstance(ag, dict) and ag.get("status") == "running":
            ag.update(fields)


async def propose(key: str) -> None:
    """Gemini proposes, the engine verifies, a failing plan gets one revision with the engine's findings."""
    from llm import AGENT_MODEL, AGENT_THINKING, complete_json, note_check

    b = _b()
    rep = b.report_by_key(key)
    try:
        c = b._ctx_for(key)
    except KeyError:
        return
    if rep is None:
        return
    t_start = time.perf_counter()
    g = c.g
    J = b._Judge(int(rep["event"]["people"]), int(rep["event"]["steps"]), int((rep.get("bound") or {}).get("people") or 0), float((rep.get("bound") or {}).get("lost_mw") or 0.0))
    ratings = {int(g.br_ids[i]): float(g.rate[i]) for i in range(g.m)}
    have = [f["action"] for f in rep["fixes"] if f.get("verdict") == "holds" and f.get("family") in ("upgrade", "combo", "agentic")]
    # the goal (beat the engine): its own cheapest full-size plan, priced the way every plan is priced
    bar = _engine_bar(rep)
    asked = verified = calls = cached = 0
    added: list[dict] = []
    held: list[dict] = []  # every verified AI plan, with its price against the bar (the result names the cheapest)
    beat = False
    feedback = ""
    extra_ids: list[int] = []
    tr = _Trace()
    _live(rep, key, trace=tr.rows, asked=0, verified=0, calls=0)
    rnd = 0
    for rnd in range(MAX_ROUNDS):
        cands = _candidates(rep, c, extra_ids)
        if rnd == 0:
            tr.ask(1, rep, c, cands, bar)
        prompt = _prompt(rep, c, cands, have + [f["action"] for f in added], feedback, bar)
        was_cached = _in_ai_cache(prompt, AGENT_MODEL)
        raw, offline = await complete_json(prompt, system=SYSTEM, fallback={"plans": []},
                                           timeout=AI_TIMEOUT_S, surface="solutions", model=AGENT_MODEL, thinking=AGENT_THINKING)
        calls += 1
        cached += 1 if was_cached and not offline else 0
        if offline:
            tr.offline(rnd + 1)
            break
        listed = raw if isinstance(raw, list) else (raw.get("plans") if isinstance(raw, dict) else None)
        plans = [p for p in (listed or []) if isinstance(p, dict)][:PLANS_ASKED]
        failed: list[str] = []
        pricier: list[str] = []  # plans that held but did not beat the engine's own (sent back with their price)
        thin_ids: list[int] = []  # lines a cheaper plan left hotter than the engine's plan leaves any
        for p in plans:
            clean = _clean_plan(p, g, ratings)
            if clean is None:
                note_check("solutions", False, "an upgrade plan that named no line on the list with a higher rating")
                tr.unusable(rnd + 1, p.get("name"))
                continue
            asked += 1
            rows, mva = _plan_lines(g, clean[0], ratings)
            tr.proposed(rnd + 1, rnd > 0, clean[2], clean[3], clean[1], rows, mva)
            fx, fb, seen = await asyncio.get_running_loop().run_in_executor(None, _check, c, J, clean, c.upgrades)
            dup = fx is not None and _same(fx, rep["fixes"] + added, seen.get("cost_usd"))
            vs = _vs(bar, seen.get("cost_usd"), float(seen.get("keep_pct") or 100), seen.get("peak_pct")) if fx is not None else None
            if fx is not None and vs:  # how it did against the engine's own plan: only a 'beat' ranks ahead of it (_key)
                fx["detail"]["vs"] = vs
                fx["detail"]["bar_usd"] = int(bar["high"])
            if vs == "thin":  # cheaper only by leaving less margin: ranked after the plans that keep the engine's margin
                fx["detail"]["thin"] = True
                fx["detail"]["margin_pct"] = bar.get("margin_pct")
            if not seen.get("skipped"):
                note_check("solutions", fx is not None, "an upgrade plan the engine re-ran through the full cascade: " + ("a line still over its limit" if seen.get("over") else "people still without power"), None if fx is not None else (f"{seen['over'][0]['pct']:g}%" if seen.get("over") else f"{seen.get('people', 0):,} people"))
                tr.verified(rnd + 1, g, seen, dup, bar, vs)
            if fx is None:
                failed.append(fb)
            else:
                if not dup:
                    added.append(fx)
                    verified += 1
                if seen.get("cost_usd"):
                    held.append({"cost": float(seen["cost_usd"]), "vs": vs, "keep_pct": float(seen.get("keep_pct") or 100), "name": clean[2], "peak_pct": seen.get("peak_pct")})
                if vs == "beat":
                    beat = True
                elif bar and seen.get("cost_usd"):
                    cost_s, bar_s = _usd(seen["cost_usd"]), _usd(bar["high"])
                    hot = [t for t in seen.get("top") or [] if bar.get("margin_pct") is not None and t["pct"] > bar["margin_pct"] + MARGIN_SLACK]
                    pricier.append(
                        f"Plan '{clean[2]}' holds but keeps only {seen.get('keep_pct', 100):.0f}% of the campus; to beat the engine's {bar_s} a plan must keep 100%." if vs == "smaller"
                        else (f"Plan '{clean[2]}' holds for {cost_s} (high end) but leaves "
                              + "; ".join(f"{t['id']}: {_label(g, t['id'], 'en')} at {t['pct']:.0f}% of {t['mva']:,} MVA (it carries {t['pct'] * t['mva'] / 100:,.0f} MVA)" for t in hot[:3])
                              + f". The engine's plan ({bar_s}) leaves no line above {bar['margin_pct']:.0f}%: find a full-size plan under {bar_s} with that margin.") if vs == "thin"
                        else f"Plan '{clean[2]}' holds but costs {cost_s} (high end), the same as the engine's {bar_s}; find a cheaper full-size plan." if vs == "match"
                        else f"Plan '{clean[2]}' holds but costs {cost_s} (high end) vs the engine's {bar_s}; find a cheaper full-size plan."
                    )
                    if vs in ("pricier", "match") and seen.get("upgraded"):  # where its money goes and how hard each upgrade works
                        pricier[-1] += " Its upgrades, as the engine priced and loaded them: " + "; ".join(
                            f"{u['id']} raised to {u['to_mva']:,} MVA runs at {u['pct']:.0f}%" + (f", {_usd(u['high'])}" if u.get("high") else "") for u in seen["upgraded"]) + "."
                    thin_ids += [t["id"] for t in hot if t["id"] not in thin_ids]  # it may re-rate them next round
            _live(rep, key, asked=asked, verified=verified, calls=calls)
        # stop once a plan beats the engine's own, or when nothing is left to send back; without a bar (no engine plan
        # that keeps the whole campus) the old rule: two verified plans are enough
        if beat or (not failed and not pricier) or (bar is None and len(added) >= 2):
            break
        extra_ids = [int(x.split(":")[0]) for f in failed for x in f.split("Lines still over their limit: ")[-1].split("; ") if x.split(":")[0].strip().isdigit()][:10]
        extra_ids += [i for i in thin_ids if i not in extra_ids][: max(0, 12 - len(extra_ids))]
        if rnd + 1 < MAX_ROUNDS:
            tr.feedback(rnd + 2, len(failed), extra_ids, len(pricier), bar)
        asks = []
        if failed:
            asks.append("propose replacement plans that fix what is still over its limit")
        if pricier and bar:
            asks.append(f"find full-size plans (keep_pct 100) that cost less than the engine's {_usd(bar['high'])}: fewer or smaller upgrades "
                        "(a line's price is flat up to twice its rating; a transformer's follows its new rating), "
                        + (f"while every line stays at or under {bar['margin_pct']:.0f}% of its rating" if bar.get("margin_pct") is not None else "while every line stays within its rating"))
        feedback = "The engine checked your previous plans:\n" + "\n".join(failed[:3] + pricier[:3]) + "\nRevise: " + " and ".join(asks) + "."
    total = float(sum(s.mw for s in c.sites))
    for fx in added:
        fx["kept_mw"] = float(fx["detail"]["mw"])
        fx["kept_pct"] = round(100.0 * fx["kept_mw"] / total, 1) if total else None
    rounds = min(rnd + 1, MAX_ROUNDS)
    ms = round((time.perf_counter() - t_start) * 1000)
    # Gemini's best verified plan against the engine's own: the cheapest that keeps the whole campus (else the cheapest)
    full = [h for h in held if h["keep_pct"] >= 99.5]
    wins = [h for h in held if h["vs"] == "beat"]
    best = min(wins or full or held, key=lambda h: h["cost"]) if held else None
    outcome = tr.result(asked, verified, rounds, calls, ms, bar, best, cached)
    status = {"status": "done", "asked": asked, "verified": verified, "added": len(added), "rounds": rounds, "by": "gemini",
              "calls": calls, "cached": cached, "ms": ms, "model": AGENT_MODEL, "trace": list(tr.rows), **outcome}
    # The report may have been rebuilt while Gemini worked (a request with a bigger time budget replaces an
    # "unchecked" one under the same key): write the verified plans into every copy still reachable.
    targets = [rep]
    cur = b.report_by_key(key)
    if cur is not None and cur is not rep:
        targets.append(cur)
    for r in targets:
        if added:
            fixes = list(r["fixes"]) + [fx for fx in added if not _same(fx, r["fixes"], (fx.get("cost") or {}).get("high"))]
            enrich(c, g, fixes, total)
            r["fixes"] = fixes
            r["solutions"] = ranked(fixes)
            r["best_fix"] = best_fix(fixes)
        r["agentic"] = dict(status)
