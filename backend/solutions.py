"""Solutions: how the fixes are ranked and described, and the AI proposer whose plans the engine checks.

The rule (user, Sat 05:58): building the data center HERE at its full amount is the highest priority. A small
lowering is fine, but people want to see more power. So a fix that keeps at least FULL_KEEP_PCT of the campus
ranks first (strengthen the grid, or build it at the same size elsewhere), and shrinking far below the plan is
the last resort. There is always more than one solution when more than one holds.

    enrich(c, g, fixes, total)  adds to every fix: kept_mw, kept_pct, cost {low, high} | None, by, must {en, es}
    ranked(fixes)               indices of the verified fixes, best first
    best_fix(fixes)             the index the report calls best (a verified fix, else the partial one that helps most)
    kick(key)                   start the AI proposer in the background for a cached report (once)
    propose(key)                the agentic loop: Gemini proposes plans, the engine re-runs each one, a failing plan is
                                sent back once with what the engine found, only verified plans are added

Every plan the AI proposes is verified by the same cascade engine as every other fix: the AI never decides what
holds. Everything is an estimate on a SYNTHETIC grid model, never a real utility's network.
"""

import asyncio
import logging
import math

import numpy as np

log = logging.getLogger("uvicorn.error")

FULL_KEEP_PCT = 90.0  # a fix that keeps at least this share of the campus is a "full size" fix
# building it HERE comes first (strengthen the grid, an AI plan, a small lowering plus upgrades), then somewhere else at the full size
FAMILY_PRIORITY = {"upgrade": 0, "agentic": 1, "combo": 2, "move": 3, "shrink": 4, "onsite": 5, "flexible": 6, "time_of_day": 7, "remove": 9}
MAX_LISTED = 4  # solutions shown, best first
MUST_LINES = 6  # upgrade lines spelled out in a "you have to do this" list
PLANS_ASKED = 3
MAX_PLAN_LINES = 12
MAX_RERATE = 5.0  # a re-rating tops out at 5x (the Fix it search's own cap)
AI_TIMEOUT_S = 10
MAX_ROUNDS = 3  # one proposal and up to two revisions after the engine's feedback

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


def _cost_of(c, g, upgrades: dict) -> dict | None:
    """What the re-ratings cost (costs.py's published per-mile and per-MVA figures), low and high; None when none."""
    if not upgrades:
        return None
    try:
        import costs

        new = {int(k): float(v) for k, v in upgrades.items()}
        rate = g.rates_with(new)
        idx = sorted(g.br_index[k] for k in new if k in g.br_index)
        applied = {g.br_index[int(b)] for b in c.upgrades if int(b) in g.br_index}
        items = costs._upgrade_items(g, g.rate, rate, idx, applied)
        if not items:
            return None
        return {"low": int(sum(it["low"] for it in items)), "high": int(sum(it["high"] for it in items)), "lines": len(items)}
    except Exception as e:  # noqa: BLE001 — a cost that can't be computed is left out, never guessed
        log.warning("solutions: cost failed: %s", e)
        return None


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
        en.append(f"Cut the campus to {kept:,.0f} MW at the peak hour")
        es.append(f"Reducir el campus a {kept:,.0f} MW en la hora pico")
    elif fam == "onsite" and d.get("onsite_mw"):
        en.append(f"Add {d['onsite_mw']:,.0f} MW of on-site generation (the grid supplies {d.get('net_mw', 0):,.0f} MW)")
        es.append(f"Añadir {d['onsite_mw']:,.0f} MW de generación propia (la red aporta {d.get('net_mw', 0):,.0f} MW)")
    return {"en": en, "es": es}


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
        fx["must"] = _must(c, g, fx, total) if fx.get("verdict") in ("holds", "partly") else {"en": [], "es": []}
    order = ranked(fixes)
    for fx in fixes:
        fx["rank"] = None
    for pos, i in enumerate(order, 1):
        fixes[i]["rank"] = pos


# ------------------------------------------------------------------------------------------ ranking
def _key(fx: dict) -> tuple:
    kept = fx.get("kept_pct")
    tier = 0 if kept is not None and kept >= FULL_KEEP_PCT else 1
    return (tier, FAMILY_PRIORITY.get(fx.get("family"), 8), -(kept or 0.0))


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


# ------------------------------------------------------------------------------------------ the AI proposer
def kick(key: str) -> None:
    """Start the proposer for a cached report, once, in the background. Safe to call on every request."""
    from llm import configured

    rep = _b().report_by_key(key)
    if rep is None or key in _running or rep.get("agentic") is not None:
        return
    if not configured() or rep.get("verdict") not in ("preventable", "partly") or not rep.get("fixes"):
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
        out.append({"id": bid, "label": _label(g, bid, "en"), "kv": float(g.br_kv[i]), "mva": round(float(g.rate[i])), "tripped_at": tripped_at.get(bid), "loaded_pct": loaded.get(bid)})
    return out


def _prompt(rep: dict, c, cands: list[dict], have: list[str], feedback: str = "") -> str:
    total = float(sum(s.mw for s in c.sites))
    where = c.header.get("sub_area") or "the site"
    lines = "\n".join(
        f"- {x['id']}: {x['label']}, {x['kv']:.0f} kV, {x['mva']} MVA now"
        + (f", already at {x['loaded_pct']:.0f}% of its rating with the data center on" if x.get("loaded_pct") else "")
        + (f", tripped in step {x['tripped_at']}" if x["tripped_at"] else "")
        for x in cands
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
        + (f"\n\n{feedback}" if feedback else "")
        + '\n\nAnswer only as JSON: {"plans": [{"name": "at most 6 words", "why": "one plain sentence on why it holds", '
        '"keep_pct": 100, "upgrades": [{"line_id": 123, "to_mva": 900}]}]}'
    )


SYSTEM = (
    "You are a careful transmission planner. The grid is a SYNTHETIC model (Breakthrough Energy / Texas A&M), not any real utility's network, "
    "and the case describes no real project or event. Never name real companies, utilities or projects. Reply with JSON only."
)


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
    name = str(plan.get("name") or "AI plan").strip()[:48] or "AI plan"
    why = str(plan.get("why") or "").strip()[:200]
    if not ups and keep >= 0.999:
        return None
    return ups, keep, name, why


def _check(c, J, plan: tuple, base_ups: dict) -> tuple[dict | None, str]:
    """Run one plan through the engine. (a fix dict if it holds, else None) and the feedback sentence."""
    b = _b()
    g = c.g
    ups_new, keep, name, why = plan
    upgrades = {**{int(k): float(v) for k, v in base_ups.items()}, **ups_new}
    mws = b._site_mws(c, keep)
    total = float(sum(s.mw for s in c.sites))
    extra = b._extra_for(g, c.buses, mws)
    oc, verdict, how = b._verify(c, J, g, extra, upgrades)
    rate = g.rates_with(upgrades)
    if verdict != "holds":
        ok, st = b._fits(c, g, extra, rate)
        over = []
        if not ok:
            for i in b._over(st)[np.argsort(-st.loading_pct[b._over(st)])][:5]:
                over.append(f"{int(g.br_ids[i])}: {b._line(g, int(i))['label']} at {st.loading_pct[i]:.0f}% of {rate[i]:.0f} MVA")
        fb = f"Plan '{name}' did NOT hold: after {oc['steps']} cascade steps {oc['people']:,} people (estimate) were still without power."
        if over:
            fb += " Lines still over their limit: " + "; ".join(over) + "."
        return None, fb
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
    return fx, ""


def _same(fx: dict, others: list[dict]) -> bool:
    """A plan already listed (same upgraded lines at the same size) is not a new solution."""
    ids = set((fx.get("apply") or {}).get("upgrades") or {})
    for o in others:
        if set((o.get("apply") or {}).get("upgrades") or {}) == ids and abs(float(o.get("kept_pct") or 100) - float(fx["detail"]["kept_pct"])) < 1.0:
            return True
    return False


async def propose(key: str) -> None:
    """Gemini proposes, the engine verifies, a failing plan gets one revision with the engine's findings."""
    from llm import complete_json

    b = _b()
    rep = b.report_by_key(key)
    try:
        c = b._ctx_for(key)
    except KeyError:
        return
    if rep is None:
        return
    g = c.g
    J = b._Judge(int(rep["event"]["people"]), int(rep["event"]["steps"]), int((rep.get("bound") or {}).get("people") or 0), float((rep.get("bound") or {}).get("lost_mw") or 0.0))
    ratings = {int(g.br_ids[i]): float(g.rate[i]) for i in range(g.m)}
    have = [f["action"] for f in rep["fixes"] if f.get("verdict") == "holds" and f.get("family") in ("upgrade", "combo", "agentic")]
    asked = verified = 0
    added: list[dict] = []
    feedback = ""
    extra_ids: list[int] = []
    for rnd in range(MAX_ROUNDS):
        cands = _candidates(rep, c, extra_ids)
        raw, offline = await complete_json(_prompt(rep, c, cands, have + [f["action"] for f in added], feedback), system=SYSTEM, fallback={"plans": []}, timeout=AI_TIMEOUT_S, surface="solutions")
        if offline:
            break
        listed = raw if isinstance(raw, list) else (raw.get("plans") if isinstance(raw, dict) else None)
        plans = [p for p in (listed or []) if isinstance(p, dict)][:PLANS_ASKED]
        failed: list[str] = []
        for p in plans:
            clean = _clean_plan(p, g, ratings)
            if clean is None:
                continue
            asked += 1
            fx, fb = await asyncio.get_running_loop().run_in_executor(None, _check, c, J, clean, c.upgrades)
            if fx is None:
                failed.append(fb)
            elif not _same(fx, rep["fixes"] + added):
                added.append(fx)
                verified += 1
        if len(added) >= 2 or not failed:
            break
        feedback = "The engine checked your previous plans:\n" + "\n".join(failed[:3]) + "\nRevise: propose replacement plans that fix what is still over its limit."
        extra_ids = [int(x.split(":")[0]) for f in failed for x in f.split("Lines still over their limit: ")[-1].split("; ") if x.split(":")[0].strip().isdigit()][:10]
    total = float(sum(s.mw for s in c.sites))
    if added:
        for fx in added:
            fx["kept_mw"] = float(fx["detail"]["mw"])
            fx["kept_pct"] = round(100.0 * fx["kept_mw"] / total, 1) if total else None
        fixes = list(rep["fixes"]) + added
        enrich(c, g, fixes, total)
        rep["fixes"] = fixes
        rep["solutions"] = ranked(fixes)
        rep["best_fix"] = best_fix(fixes)
    rep["agentic"] = {"status": "done", "asked": asked, "verified": verified, "added": len(added), "rounds": min(rnd + 1, MAX_ROUNDS), "by": "gemini"}
