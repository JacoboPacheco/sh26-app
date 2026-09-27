"""The chain slide as an arc of four parts (bulletin.py's s_chain calls this).

THE CHAIN IN FOUR PARTS (user, Sat 23:37-23:48): the play-by-play is one arc over the whole cascade, in order, each part
a single flowing sentence, never labeled or numbered (no "Play 1", no "First,"):

  origin    It begins at <the first element to fail>, which extends into <the next elements>, causing damage that hits
            <people> (estimate).
  spread    The chains extend, causing damage at <place>, <place> and <place>.
  outward   As the damage makes its way toward <the farthest place reached>, it hits <people> (estimate).
  end       It finally reaches <the last element> before the grid <settles | splits apart>, resulting in <the result>.

The cascade's ordered steps are cut into four consecutive phases (fewer parts when there are fewer than four steps: three,
two or one, never an empty part); the last part always holds the last step and the result. A storm's own damage (step 0)
opens the first part. Every number comes from the engine's report (the same figures the panel and the toll slide print);
the places are the towns the engine's own people-hit accounting names (`replay.steps[].hits`), nearest to each failure
first. The sentences are templates here; Gemini may rewrite each one on its own (bulletin.ai_slots), and each is checked
against this part's numbers only (`allowed_numbers`), so a figure from another part can never slip in.

Pure functions over the Writer (bulletin.Writer); the bulletin helpers are imported late (bulletin imports this module).
"""

from __future__ import annotations

import math
import re

KINDS = {4: ("origin", "spread", "outward", "end"), 3: ("origin", "spread", "end"), 2: ("origin", "end"), 1: ("only",)}
MORE_TOWNS = re.compile(r"^\d+ more towns?$", re.IGNORECASE)  # the engine's tail group when a step hits very many towns
MAX_IDS = 60  # tripped ids kept per part (the map highlights them)

# the shape of each part's sentence, as Gemini is told it (slots in angle brackets); never printed on screen
SHAPE = {
    "origin": "It begins at <first_element>, which extends into <extends_into>, causing damage that hits <people_hit_in_this_part> (estimate).",
    "origin_storm": "It begins with the storm, which cuts <storm_lines_cut> lines, then extends into <extends_into>, causing damage that hits <people_hit_in_this_part> (estimate).",
    "spread": "The chains extend into <extends_into>, causing damage at <places>.",
    "spread_places": "The chains extend, causing damage at <places>.",
    "spread_elements": "The chains extend, tripping <elements_that_trip>.",
    "outward": "As the damage makes its way toward <farthest_place>, it hits <people_hit_in_this_part> (estimate).",
    "end": "It finally reaches <last_element> <how_it_ends>, resulting in <result>.",
    "only": "It begins at <first_element>, causing damage that hits <people_hit_in_this_part> (estimate), <how_it_ends>, resulting in <result>.",
}


def _km(a, b) -> float:
    """Kilometers between two (lat, lon) points (flat-earth, fine at a grid's scale)."""
    return 111.19 * math.hypot(a[0] - b[0], (a[1] - b[1]) * math.cos(math.radians((a[0] + b[0]) / 2)))


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _steps(w) -> tuple[dict | None, list[dict]]:
    """(the storm's own step or None, the failure steps in order) with what each did:
    {n, action, ids (tripped or held), first (the id that names it), hits [(area, people added, km, sub ids)],
     cum (people hit so far)}. Built from the replay's steps and the timeline."""
    rp = w.r.get("replay") if isinstance(w.r.get("replay"), dict) else {}
    raw = {int(st.get("n") or 0): st for st in (rp.get("steps") or []) if isinstance(st, dict)}
    tl = {int(r.get("n") or 0): r for r in w.timeline if isinstance(r, dict)}
    out, prev = {}, 0
    for n in sorted(set(raw) | set(tl)):
        st, row = raw.get(n) or {}, tl.get(n) or {}
        ids = [int(b) for b in (st.get("tripped") or [])] or [int(x["id"]) for x in (row.get("lines") or []) if isinstance(x, dict) and x.get("id") is not None]
        held = st.get("held_line")
        if not ids and held is not None:
            ids = [int(held)]
        cum = max(int(st["people_hit"] if st.get("people_hit") is not None else (row.get("people_cum") or prev)), prev)
        hits, run = [], prev
        for h in st.get("hits") or []:
            top = int(h.get("people_hit") or run)
            hits.append((str(h.get("area") or ""), max(top - run, 0), float(h.get("km") or 0.0), [int(s) for s in h.get("subs") or []]))
            run = max(run, top)
        out[n] = {"n": n, "action": st.get("action") or row.get("action"), "ids": ids, "first": ids[0] if ids else None, "hits": hits, "cum": cum}
        prev = cum
    return out.get(0), [out[n] for n in sorted(out) if n >= 1]


def _mid(w, bid) -> tuple[float, float] | None:
    """A branch's midpoint as (lat, lon), None when the grid doesn't know it."""
    pts = w.line_pts(bid) if bid is not None and int(bid) in w.g.br_index else []
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)) if pts else None


def _sub_pt(w, sid: int) -> tuple[float, float] | None:
    i = w.g.sub_index.get(int(sid))
    return None if i is None else (float(w.g.sub_lat[i]), float(w.g.sub_lon[i]))


def plan(w) -> list[dict]:
    """The arc's parts, language-independent: [{k, kind, steps, line_ids, areas, places, people_delta, people_total, far,
    first, last (+ origin, extends, storm_lines on the first)}]; [] when nothing tripped and there was no storm."""
    storm, fails = _steps(w)
    if not fails and storm is None:
        return []
    m = len(fails)
    count = min(4, max(1, m))
    base, rem = divmod(m, count)
    sizes = [base + (1 if i >= count - rem else 0) for i in range(count)] if m else [0]  # the last parts take the remainder
    kinds = KINDS[count]
    origin = next((_mid(w, st["first"]) for st in fails if _mid(w, st["first"])), None)
    if origin is None and w.site_pt():
        origin = (w.site_pt()[1], w.site_pt()[0])
    parts, at, prev_cum = [], 0, 0
    for size in sizes:
        chunk = fails[at:at + size]
        at += size
        steps = ([storm] if (not parts and storm) else []) + chunk
        if not steps:
            continue
        cum = steps[-1]["cum"]
        # places: the towns this part's steps hit (nearest to each failure first), the three that hit the most people
        added: dict[str, int] = {}
        for st in steps:
            for area, add, _k, _subs in st["hits"]:
                if area and not MORE_TOWNS.match(area):
                    added[area] = added.get(area, 0) + add
        areas = [a for a in added if added[a] > 0]  # nearest first
        top = set(sorted(areas, key=lambda a: -added[a])[:3])
        w.names.update(areas)
        # the farthest place this part reaches from where the cascade began: a hit town (by its farthest station), else a failed element
        far, best = None, -1.0
        if origin:
            for st in steps:
                for area, add, _k, subs in st["hits"]:
                    if area and not MORE_TOWNS.match(area) and add > 0:
                        d = max((_km(origin, p) for p in (_sub_pt(w, s) for s in subs) if p), default=0.0)
                        if d > best:
                            best, far = d, ("area", area)
            if far is None:
                for st in chunk:
                    for b in st["ids"]:
                        p = _mid(w, b)
                        if p and _km(origin, p) > best:
                            best, far = _km(origin, p), ("line", int(b))
        parts.append({
            "k": len(parts) + 1, "kind": kinds[len(parts)], "steps": [st["n"] for st in steps],
            "line_ids": _dedupe([b for st in chunk for b in st["ids"]])[:MAX_IDS], "areas": sorted(areas, key=lambda a: -added[a])[:8],
            "places": [a for a in areas if a in top],
            "people_delta": max(cum - prev_cum, 0), "people_total": cum, "far": far,
            "first": chunk[0]["first"] if chunk else None, "last": (chunk or steps)[-1]["first"],
        })
        prev_cum = cum
    # the first part: where it begins (the first element to fail; a storm's own damage when there is one) and what it extends into
    head = parts[0]
    order = _dedupe([b for st in fails for b in st["ids"]])
    own = _dedupe([b for st in fails[:sizes[0]] for b in st["ids"]])
    head["origin"] = None if storm else (order[0] if order else None)
    head["storm_lines"] = int(w.storm or 0) if storm else 0
    head["extends"] = ([b for b in own if b != head["origin"]] or [b for b in order if b != head["origin"]])[:2]
    named = {head["origin"], *head["extends"]}  # already named in the first sentence: the spread part names only what is new
    for p in parts:
        p["fresh"] = [b for b in p["line_ids"] if b not in named]
    if fails:
        parts[-1]["last"] = fails[-1]["first"]
    return parts


# ------------------------------------------------------------------------------------------- the words
def _to_es(label: str) -> str:
    """'a' + a Spanish label: 'al transformador de X' / 'a la línea de X a Y' (a town has no article: 'a Naples')."""
    return "al " + label[3:] if label.startswith("el ") else "a " + label


def _bare(label: str) -> str:
    return re.sub(r"^(the|el|la|los|las)\s+", "", label, flags=re.IGNORECASE)


def _list(items: list[str], lang: str) -> str:
    from bulletin import join  # noqa: PLC0415

    return join(items, lang)


def when_end(w, lang: str) -> str:
    """How it ends, as a phrase after the last element: 'before the grid settles' / 'as the model stops, still spreading'."""
    en = lang == "en"
    if w.ev.get("capped"):
        return "as the model stops, still spreading" if en else "cuando el modelo se detiene, aún propagándose"
    if w.ev.get("outcome") == "islanded" and w.people:
        return "before the grid splits apart" if en else "antes de que la red se parta"
    return "before the grid settles" if en else "antes de que la red se estabilice"


def _result(w, lang: str) -> tuple[str, list[str]]:
    """(the result clause, the strings in it to set in heavier weight): the panel's figures, each an estimate."""
    from bulletin import hours_say, mw_say, people_round, people_say, usd_say  # noqa: PLC0415

    en = lang == "en"
    c = w.r.get("cost") or {}
    hours = float(c.get("duration_h_assumed") or 0)
    high = float(c.get("blackout_high_usd") or c.get("blackout_usd") or 0)
    items, marks = [], []
    if w.people > 0:
        tail = (" still without power" if en else " que siguen sin luz") if w.hit > w.people else (" without power" if en else " sin luz")
        h = hours_say(hours, lang) if hours else ""
        items.append(people_say(w.people, lang) + tail + ((f" for {h}" if en else f" durante {h}") if h else ""))
        marks += [people_round(w.people, lang)] + ([h] if h else [])
    else:
        items.append("no one left without power" if en else "cero personas sin luz")
    lost = float(w.ev.get("lost_mw") or 0)
    if lost >= 0.5:
        s = mw_say(lost, lang)
        items.append(f"{s} of load lost" if en else f"{s} de carga perdida")
        marks.append(s)
    if high:
        usd = usd_say(high, lang)[0]
        items.append(f"an expected cost of about {usd}" if en else f"un costo esperado de unos {usd}")
        marks.append(usd)
    return _list(items, lang) + (" (estimates)" if en else " (estimaciones)"), marks


def sentence(w, part: dict, lang: str) -> tuple[str, list[str]]:
    """(the part's sentence, the names / places / numbers in it to set in heavier weight)."""
    from bulletin import num, people_round, people_say  # noqa: PLC0415

    en = lang == "en"
    kind = part["kind"]
    el = lambda bid: w.line_label(bid, lang)  # noqa: E731
    marks: list[str] = []
    ppl = people_say(part["people_delta"], lang) if part["people_delta"] >= 1 else None
    hit_mark = people_round(part["people_delta"], lang) if ppl else None  # set in weight where the sentence prints it

    def causing() -> str:
        if ppl:
            marks.append(hit_mark)
            return f"causing damage that hits {ppl} (estimate)" if en else f"causando daños que afectan a {ppl} (estimación)"
        return "shifting its load onto the lines around it" if en else "pasando su carga a las líneas cercanas"

    if kind in ("origin", "only"):
        ext = [el(b) for b in part.get("extends") or []] if kind == "origin" else []
        into = (_list(ext, lang) if en else _list([_to_es(x) for x in ext], lang)) if ext else ""
        if part.get("storm_lines"):
            n = int(part["storm_lines"])
            marks.append(num(n, lang))
            head = f"It begins with the storm, which cuts {num(n, lang)} lines" if en else f"Empieza con la tormenta, que corta {num(n, lang)} líneas"
            if into:
                head += f", then extends into {into}" if en else f", y luego se extiende {into}"
        else:
            o = el(part["origin"]) if part.get("origin") is not None else (w.place or ("the grid" if en else "la red"))
            marks.append(_bare(o))
            head = f"It begins at {o}" if en else f"Empieza en {o}"
            if into:
                head += f", which extends into {into}" if en else f", que se extiende {into}"
        marks += [_bare(x) for x in ext]
        if kind == "only":
            res, rm = _result(w, lang)
            marks += rm
            tail = f", {when_end(w, lang)}, resulting in {res}" if en else f", {when_end(w, lang)}, con el resultado de {res}"
            return f"{head}, {causing()}{tail}.", marks
        return f"{head}, {causing()}.", marks
    if kind == "spread":
        els = [el(b) for b in part.get("fresh", part["line_ids"])[:2]]
        marks += [_bare(x) for x in els]
        if part["places"]:
            marks += part["places"]
            lst = _list(part["places"], lang)
            if els:
                into = _list(els, lang) if en else _list([_to_es(x) for x in els], lang)
                return (f"The chains extend into {into}, causing damage at {lst}." if en
                        else f"Las cadenas se extienden {into}, causando daños en {lst}."), marks
            return (f"The chains extend, causing damage at {lst}." if en else f"Las cadenas se extienden y causan daños en {lst}."), marks
        if els:
            lst = _list(els, lang)
            return (f"The chains extend, tripping {lst}." if en else f"Las cadenas se extienden y disparan {lst}."), marks
        return ("The chains keep extending across the grid." if en else "Las cadenas siguen extendiéndose por la red."), marks
    if kind == "outward":
        far = part.get("far")
        if far and far[0] == "area":
            place = far[1]
            marks.append(place)
        elif far:
            place = el(far[1])
            marks.append(_bare(place))
        else:
            place = "the edge of the affected area" if en else "el borde de la zona afectada"
        if ppl:
            marks.append(hit_mark)
            return (f"As the damage makes its way toward {place}, it hits {ppl} (estimate)." if en
                    else f"A medida que el daño avanza hacia {place}, afecta a {ppl} (estimación)."), marks
        return (f"As the damage makes its way toward {place}, it reaches no one new." if en
                else f"A medida que el daño avanza hacia {place}, no afecta a nadie más."), marks
    # end
    last = part.get("last")
    target = el(last) if last is not None else (w.place or ("the far edge of the grid" if en else "el otro extremo de la red"))
    marks.append(_bare(target))
    res, rm = _result(w, lang)
    marks += rm
    if en:
        return f"It finally reaches {target} {when_end(w, lang)}, resulting in {res}.", marks
    return f"Por fin llega {_to_es(target)} {when_end(w, lang)}, con el resultado de {res}.", marks


def texts(w, parts: list[dict]) -> list[dict]:
    """Each part's template sentence and marks in both languages: [{en: (text, marks), es: (text, marks)}]."""
    return [{lang: sentence(w, p, lang) for lang in ("en", "es")} for p in parts]


# ------------------------------------------------------------------------------------ what Gemini gets
def ai_data(w, parts: list[dict], k: int) -> dict:
    """One part as data for Gemini (no sentence to copy): the names and the spoken forms of its numbers, per language."""
    from bulletin import hours_say, mw_say, num, people_say, usd_say  # noqa: PLC0415

    p = parts[k - 1]
    kind = p["kind"]
    both = lambda fn: {lang: fn(lang) for lang in ("en", "es")}  # noqa: E731
    d: dict = {"draft": both(lambda lang: sentence(w, p, lang)[0])}  # the template: polish it, keep every name and figure
    if p["people_delta"] >= 1 and kind in ("origin", "outward", "only"):
        d["people_hit_in_this_part"] = both(lambda lang: people_say(p["people_delta"], lang))
    if kind in ("origin", "only"):
        if p.get("storm_lines"):
            d["storm_lines_cut"] = num(int(p["storm_lines"]))
        elif p.get("origin") is not None:
            d["first_element"] = both(lambda lang: w.line_label(p["origin"], lang))
        if kind == "origin" and p.get("extends"):
            d["extends_into"] = [both(lambda lang, b=b: w.line_label(b, lang)) for b in p["extends"]]
    if kind == "spread":
        els = [both(lambda lang, b=b: w.line_label(b, lang)) for b in p.get("fresh", p["line_ids"])[:2]]
        if p["places"]:
            d["places"] = p["places"]
            if els:
                d["extends_into"] = els
        elif els:
            d["elements_that_trip"] = els
    if kind == "outward":
        far = p.get("far")
        if far and far[0] == "area":
            d["farthest_place"] = far[1]
        elif far:
            d["farthest_place"] = both(lambda lang: w.line_label(far[1], lang))
    if kind in ("end", "only"):
        if kind == "end" and p.get("last") is not None:
            d["last_element"] = both(lambda lang: w.line_label(p["last"], lang))
        d["how_it_ends"] = both(lambda lang: when_end(w, lang))
        c = w.r.get("cost") or {}
        hours = float(c.get("duration_h_assumed") or 0)
        high = float(c.get("blackout_high_usd") or c.get("blackout_usd") or 0)
        res: dict = {}
        if w.people > 0:
            res["people_without_power"] = both(lambda lang: people_say(w.people, lang))
            if hours:
                res["for_about"] = both(lambda lang: hours_say(hours, lang))
        else:
            res["people_without_power"] = "none"
        if float(w.ev.get("lost_mw") or 0) >= 0.5:
            res["load_lost"] = both(lambda lang: mw_say(float(w.ev["lost_mw"]), lang))
        if high:
            res["expected_cost"] = both(lambda lang: usd_say(high, lang)[0])
        d["result"] = res
    return d


def allowed_numbers(w, parts: list[dict], k: int) -> set[str]:
    """The number strings this part's sentence may print: its own figures only (a total from another part is rejected)."""
    from bulletin import _num_forms, usd_say  # noqa: PLC0415

    p = parts[k - 1]
    vals: list[float] = []
    if p["kind"] in ("origin", "outward", "only") and p["people_delta"] >= 1:
        vals.append(float(p["people_delta"]))
    if p.get("storm_lines"):
        vals.append(float(p["storm_lines"]))
    if p["kind"] in ("end", "only"):
        c = w.r.get("cost") or {}
        vals += [float(w.people), float(w.ev.get("lost_mw") or 0)]
        high = float(c.get("blackout_high_usd") or c.get("blackout_usd") or 0)
        for lang in ("en", "es"):
            v = usd_say(high, lang)[1] if high else None
            if v is not None:
                vals.append(float(v))
    out: set[str] = set()
    for v in vals:
        if v:
            out |= _num_forms(v)
    return out


def needed_names(w, parts: list[dict], k: int, lang: str) -> list[str]:
    """The names a written part must contain (any one of them): where it begins, the places, the farthest place, the last element."""
    p = parts[k - 1]
    kind = p["kind"]
    if kind in ("origin", "only") and not p.get("storm_lines") and p.get("origin") is not None:
        return [_bare(w.line_label(p["origin"], lang))]
    if kind == "spread" and p["places"]:
        return list(p["places"])
    if kind == "outward" and p.get("far"):
        far = p["far"]
        return [far[1]] if far[0] == "area" else [_bare(w.line_label(far[1], lang))]
    if kind == "end" and p.get("last") is not None:
        return [_bare(w.line_label(p["last"], lang))]
    return []


_EN_ONLY = re.compile(r"\b(the|and|which|causing|resulting|damage|extends?|begins|reaches|before|still|without|power|hits|people)\b", re.IGNORECASE)
_ES_ONLY = re.compile(r"\b(que|del|las|los|una|unas|unos|para|con|por|sin|luz|línea|líneas|transformador|personas|afectan|causando|empieza|antes)\b", re.IGNORECASE)
_LEAK = re.compile(r"_|\b(expectedcost|peoplehit|firstelement|lastelement|howitends|extendsinto|loadlost)\b", re.IGNORECASE)


def _norm(x: str) -> str:
    return re.sub(r"[^a-z0-9áéíóúñü]+", " ", x.lower()).strip()


def check_text(w, parts: list[dict], k: int, lang: str, text: str, masked: str) -> str | None:
    """Why a written part is rejected, or None: one plain sentence in its own language that keeps the template's names and
    figures and stays close to it (a rephrase, never a different sentence); nothing of the data's field names leaks in."""
    import difflib  # noqa: PLC0415

    if _LEAK.search(text):
        return "leaks a data field name"
    other = _ES_ONLY if lang == "en" else _EN_ONLY
    m = other.search(masked)
    if m:
        return f"mixes in the other language: {m.group(0)!r}"
    tmpl, marks = sentence(w, parts[k - 1], lang)
    low = text.lower()
    for mk in marks:
        if mk and mk.lower() not in low:
            return f"drops {mk!r}"
    if difflib.SequenceMatcher(None, _norm(text), _norm(tmpl)).ratio() < 0.5:
        return "strays from the sentence shape"
    return None


PURPOSE = {
    "origin": "the first part of the chain reaction: where it begins, what it extends into, and the damage that first chain does. Write ONE flowing sentence in this shape and no other: ",
    "spread": "the second part of the chain reaction: the chains extend, and where they cause damage. Write ONE flowing sentence in this shape and no other: ",
    "outward": "the third part of the chain reaction: the damage makes its way to the outer edge and hits people. Write ONE flowing sentence in this shape and no other: ",
    "end": "the last part of the chain reaction: where it finally reaches, how it ends, and the result. Write ONE flowing sentence in this shape and no other: ",
    "only": "the whole chain reaction in one sentence: where it begins, the damage, how it ends, and the result. Write ONE flowing sentence in this shape and no other: ",
}
LABEL = re.compile(r"\b(play|jugada|step|paso|part|parte|phase|fase)\s*(\d+|one|two|three|four|uno|dos|tres|cuatro)\b", re.IGNORECASE)
ORDINAL_LEAD = re.compile(r"^\s*(first|second|third|fourth|primero|segundo|tercero|cuarto)\b[,:]", re.IGNORECASE)


def purpose(parts: list[dict], k: int) -> str:
    p = parts[k - 1]
    kind = p["kind"]
    if kind == "spread":
        shape = SHAPE["spread" if (p["places"] and p.get("fresh", p["line_ids"])) else "spread_places" if p["places"] else "spread_elements"]
    else:
        shape = SHAPE["origin_storm" if (kind in ("origin", "only") and p.get("storm_lines")) else kind]
    return PURPOSE[kind] + shape


def check_shape(masked: str) -> str | None:
    """Why a written part is not one plain, unlabeled sentence (None when it is). `masked`: names hidden (a name with a
    period, "St. Petersburg", is not a sentence end)."""
    if LABEL.search(masked):
        return "labels the part with a number"
    if ORDINAL_LEAD.match(masked):
        return "opens with an ordinal"
    if len(re.findall(r"[.!?](?=\s|$)", masked.strip())) > 1:
        return "more than one sentence"
    return None
