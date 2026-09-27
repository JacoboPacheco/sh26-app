"""Audio description of the presentation: for every beat of the deck, one short plain sentence or two about what the
MAP is showing while that beat plays ("On the map: ..." on screen), for a viewer who is blind or has low vision.

Deterministic templates, English and Spanish, one per beat kind. Every figure and place name comes from the deck's own
data (the report the deck was written from, the slide's own fields); nothing is invented, and no place is placed by a
compass guess: the map's places are the towns the deck itself names. Each description goes through the same fact check
as the deck's narration (bulletin.check_segment: numbers must be facts, no real utility, storm or agency names); one
that fails a check is dropped, and its beat simply has no description. Each description is 12 to 35 words in at most two
sentences, says "the model" or "estimated" wherever a real-world claim could arise, and never predicts ("will").

The description is registered with the voice service like the deck's own lines (voice.register, the analyst voice), so
the stage can speak it before the beat's narration when the viewer has sound on. The page adds the "On the map:"
prefix; it is not part of the text spoken or stored here.

attach(w, slides) puts `slide["describe"] = {lang: {"text", "key", "chars"}}` on every slide that has one.
"""

import logging
import re

import numpy as np

import voice

log = logging.getLogger("uvicorn.error")
LANGS = ("en", "es")
MIN_WORDS, MAX_WORDS, MAX_SENTENCES = 12, 35, 2


def _b():
    import bulletin  # noqa: PLC0415 — bulletin imports this module

    return bulletin


def _span(w) -> tuple[float, float]:
    """The state model's own extent in degrees (longitude, latitude), what a camera frame is measured against."""
    q = getattr(w, "_describe_span", None)
    if q is None:
        lat = np.asarray(w.g.sub_lat, dtype=float)
        lon = np.asarray(w.g.sub_lon, dtype=float)
        q = (float(lon.max() - lon.min()) or 1.0, float(lat.max() - lat.min()) or 1.0)
        w._describe_span = q
    return q


def _area_names(w, limit: int = 3) -> list[str]:
    """The hardest-hit areas, by people, as they are named on the map."""
    names = [str(a["area"]) for a in (w.r.get("areas") or []) if float(a.get("people") or 0) > 0][:limit]
    w.names.update(names)
    return names


def frame(w, pts, lang: str, areas: list[str] | None = None) -> str:
    """What the camera takes in, as a noun phrase after 'across' / 'por': the whole state, the areas around the named
    towns (the deck's own areas, never a compass guess), or one town's area."""
    pts = [p for p in (pts or []) if p and len(p) >= 2]
    en = lang == "en"
    whole = f"the whole of {w.region_name}" if en else f"todo el estado de {w.region_es}"
    if not pts:
        return whole
    dlon, dlat = _span(w)
    lons = [float(p[0]) for p in pts]
    lats = [float(p[1]) for p in pts]
    span = max((max(lons) - min(lons)) / dlon, (max(lats) - min(lats)) / dlat)
    if span >= 0.55:
        return whole
    if areas and span >= 0.04:
        return f"the areas around {_b().join(areas, 'en')}" if en else f"las zonas de {_b().join(areas, 'es')}"
    town = w.area_near((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
    return f"the {town} area" if en else f"la zona de {town}"


def _place_in(w, lon: float, lat: float, lang: str) -> str:
    """'Fort Myers, Florida': the town the camera centers on."""
    town = w.area_near(lat, lon)
    return f"{town}, {w.region_name if lang == 'en' else w.region_es}"


def _origin(w) -> str | None:
    """Where the first failure was: the first element to fail (its own town), else the campus's town."""
    rc = w.r.get("root_cause") or {}
    line = rc.get("line") or {}
    if line.get("from_area"):
        w.names.add(str(line["from_area"]))
        return str(line["from_area"])
    return w.place or None


def _fact(w, key: str, label: str, value, unit: str = "", estimate: bool = False) -> None:
    """A figure the description prints, from the deck's own data, put on the fact sheet so the number check sees it."""
    try:
        w.add(f"deck.describe.{key}", label, round(float(value), 2), unit, estimate)
    except (TypeError, ValueError):
        pass


def _plain_num(n: int, lang: str, fem: bool = False) -> str:
    return _b().words(int(n), lang, fem=fem)


def _fix_slide(w) -> dict:
    return next((x for x in getattr(w, "_describe_slides", []) if (x.get("kind") or x.get("id")) == "fix"), {})


def _walked(s) -> list[dict]:
    return [o for o in (s.get("options") or []) if o.get("role") in ("lead", "alt") and o.get("verdict") in ("holds", "partly")]


# ---------------------------------------------------------------------------------------------- the beats
def _toll(w, s, lang):
    B = _b()
    en = lang == "en"
    c = w.r.get("cost") or {}
    high = float(c.get("blackout_high_usd") or c.get("blackout_usd") or 0)
    if not high:
        return None
    _fact(w, "toll.cost", "Expected blackout cost, high end (as the toll beat prints it)", high, "USD", True)
    usd, _ = B.usd_say(high, lang)
    origin = _origin(w) or w.top_area()
    if en:
        start = f" from the first failure near {origin}" if origin else ""
        return f"A red front spreads outward{start}, darkening every area it reaches, while the estimated cost counter climbs to {usd}."
    start = f" desde la primera falla cerca de {origin}" if origin else ""
    return f"Un frente rojo se extiende{start}, oscureciendo cada zona que alcanza, mientras el contador del costo estimado sube a {usd}."


def _event(w, s, lang):
    B = _b()
    en = lang == "en"
    site = w.site_pt()
    mw = float(w.mw or (w.case.get("mw") or 0)) if w.sites else 0.0
    fr = frame(w, [], lang)
    if w.storm:  # the map pulls back to the whole state, the storm's cut lines already down
        n = B.num(int(w.storm), lang)
        if site and mw and not w.multi:
            town = w.area_near(site[1], site[0])
            return (f"The map shows {fr} with the {n} lines the storm cut already down, and a circle marks the new {B.mw_say(mw, 'en', adj=True)} data center at {town}." if en
                    else f"El mapa muestra {fr} con las {n} líneas que cortó la tormenta ya caídas, y un círculo marca el nuevo centro de datos de {B.mw_say(mw, 'es')} en {town}.")
        return (f"Dashed red lines across {fr} mark the {n} lines the storm cut before anything else fails." if en
                else f"Líneas rojas discontinuas por {fr} marcan las {n} líneas que cortó la tormenta antes de que falle nada más.")
    if site and w.sites:
        at = _place_in(w, site[0], site[1], lang)
        if w.multi:
            n = len(w.sites)
            return (f"The map centers on {at}, where a circle marks the first of {_plain_num(n, 'en')} new data centers tested together." if en
                    else f"El mapa se centra en {at}, donde un círculo marca el primero de {_plain_num(n, 'es')} centros de datos nuevos probados a la vez.")
        if not mw:
            return None
        return (f"The map centers on {at}, where a circle marks the new {B.mw_say(mw, 'en', adj=True)} data center." if en
                else f"El mapa se centra en {at}, donde un círculo marca el nuevo centro de datos de {B.mw_say(mw, 'es')}.")
    return (f"The map shows {fr} as the model has it before anything fails, with demand raised in every area." if en
            else f"El mapa muestra {fr} como lo tiene el modelo antes de que falle nada, con la demanda elevada en todas las zonas.")


def _chain(w, s, lang):
    B = _b()
    en = lang == "en"
    run = [x for x in w.timeline if isinstance(x, dict) and int(x.get("n", 0)) > 0]
    trips = sum(len(x.get("lines") or []) for x in run if x.get("action") != "shed")
    if not run or not trips:
        if w.storm:
            return (f"The map already shows the {B.num(int(w.storm))} lines the storm cut as dashed red lines; in the model nothing else trips afterward." if en
                    else f"El mapa ya muestra las {B.num(int(w.storm), 'es')} líneas que cortó la tormenta como líneas rojas discontinuas; en el modelo no se dispara nada más.")
        return None
    if en:
        tail = "One line or transformer trips in all." if trips == 1 else f"{_plain_num(trips, 'en').capitalize()} lines and transformers trip in all."
        return f"The cascade replays across the map: failed lines flash one after another, spreading outward, and each town they hit fills with red hatching. {tail}"
    tail = "En total, se dispara una línea o transformador." if trips == 1 else f"En total, se disparan {_plain_num(trips, 'es', fem=True)} líneas y transformadores."
    return f"La cascada se repite en el mapa: las líneas que fallan destellan una tras otra y cada pueblo afectado se llena de rayas rojas. {tail}"


def _areas(w, s, lang):
    B = _b()
    en = lang == "en"
    areas = [a for a in (w.r.get("areas") or []) if float(a.get("people") or 0) > 0]
    if not areas:
        return None
    k = min(5, len(areas))
    first = areas[0]
    w.names.update(str(a["area"]) for a in areas[:2])
    if k == 1:
        return (f"The camera moves to {first['area']}, outlined in red hatching, where an estimated {B.people_round(first['people'], 'en')} people are without power." if en
                else f"La cámara va a {first['area']}, con contorno de rayas rojas, donde {B.people_say(first['people'], 'es')} se quedan sin luz según la estimación.")
    second = areas[1]
    if en:
        return (f"The camera visits {_plain_num(k, 'en')} areas in turn, each outlined in red hatching: {first['area']} first, an estimated {B.people_round(first['people'], 'en')} people without power, "
                f"then {second['area']}, an estimated {B.people_round(second['people'], 'en')}.")
    return (f"La cámara visita {_plain_num(k, 'es')} zonas, cada una con contorno de rayas rojas: primero {first['area']}, con {B.people_say(first['people'], 'es', noun=False)} personas sin luz según la estimación, "
            f"luego {second['area']}, {B.people_say(second['people'], 'es', noun=False)}.")


def _hospitals(w, s, lang):
    B = _b()
    en = lang == "en"
    hos = w.r.get("hospitals") or {}
    rows = [h for h in (hos.get("areas") or []) if int(h.get("count") or 0) > 0][:3]
    if not rows:
        return None
    w.names.update(str(h["area"]) for h in rows)
    more = int(hos.get("count") or 0) > sum(int(h["count"]) for h in rows)
    if en:
        parts = [f"{_plain_num(h['count'], 'en')} in {h['area']}" for h in rows]
        listed = f"{', '.join(parts)}, and more elsewhere" if more else B.join(parts, 'en')
        return f"Tags beside the dark areas count their hospitals: {listed}. Each is assumed to switch to backup power."
    parts = [f"{_plain_num(h['count'], 'es')} en {h['area']}" for h in rows]
    listed = f"{', '.join(parts)} y más en otras zonas" if more else B.join(parts, 'es')
    return f"Etiquetas junto a las zonas sin luz cuentan hospitales: {listed}. Se supone que cada uno pasa a respaldo."


def _cost(w, s, lang):
    B = _b()
    en = lang == "en"
    c = w.r.get("cost") or {}
    hi = float(c.get("blackout_high_usd") or ((c.get("ranges") or {}).get("blackout_usd") or [0, 0])[-1] or 0)
    if not hi:
        return None
    best = w.best or {}
    fix_hi = float((best.get("cost") or {}).get("high") or 0)
    _fact(w, "cost.blackout", "Blackout cost, high end (as the cost beat's map tag prints it)", hi, "USD", True)
    usd, _ = B.usd_say(hi, lang)
    if fix_hi:
        _fact(w, "cost.fix", "Cost of the leading fix, high end (as the cost beat's green tag prints it)", fix_hi, "USD", True)
        fusd, _ = B.usd_say(fix_hi, lang)
        return (f"A red tag beside the hardest-hit area shows the estimated blackout cost, up to {usd}; green lines mark the upgrades that would prevent it, up to {fusd}." if en
                else f"Una etiqueta roja junto a la zona más afectada muestra el costo estimado del apagón, hasta {usd}; líneas verdes marcan los refuerzos que lo evitarían, hasta {fusd}.")
    return (f"A red tag beside the hardest-hit area shows the estimated cost of the blackout, up to {usd}, over the areas that lost power, dimmed and outlined." if en
            else f"Una etiqueta roja junto a la zona más afectada muestra el costo estimado del apagón, hasta {usd}, sobre las zonas sin luz, atenuadas y con contorno.")


def _cause(w, s, lang):
    B = _b()
    en = lang == "en"
    rc = w.r.get("root_cause") or {}
    line = rc.get("line") or {}
    bid = line.get("id")
    pw, po = rc.get("pct_with"), rc.get("pct_without")
    if rc.get("cause") == "storm":
        fr = frame(w, [], lang)
        return (f"Dashed red lines mark every line the storm cut across {fr}; the failure follows from that damage." if en
                else f"Líneas rojas discontinuas marcan cada línea que cortó la tormenta por {fr}; la falla sigue de ese daño.")
    if bid is None:
        return None
    label = w.line_label(bid, lang, fallback=line.get("label"))
    if s.get("weak_point"):
        if pw is None:
            return None
        over = pw > 100
        far = pw > B.PCT_FAR  # a re-solve artefact above this: the deck says "far past its limit" (REVIEW-1 c)
        if po is not None and far:
            a = int(round(float(po)))
            gauge = (f"A gauge beside it fills from {a} percent of its rating to far past its limit, as power flows toward the campus." if en
                     else f"Un medidor sube del {a} por ciento de su capacidad a muy por encima de su límite, mientras llega la nueva carga.")
        elif po is not None:
            a, z = int(round(float(po))), int(round(float(pw)))
            gauge = (f"A gauge beside it fills from {a} to {z} percent of its rating{', past the limit mark,' if over else ''} as power flows toward the campus." if en
                     else f"Un medidor sube del {a} al {z} por ciento de su capacidad{', pasando el límite' if over else ''}, mientras llega la nueva carga.")
        elif far:
            gauge = ("A gauge beside it shows its loading with the new load, far past its limit." if en
                     else "Un medidor a su lado muestra su carga con la nueva carga, muy por encima de su límite.")
        else:
            gauge = (f"A gauge beside it shows its loading with the new load, {B.pct_say(pw, 'en')} of its rating." if en
                     else f"Un medidor a su lado muestra su carga con la nueva carga, el {B.pct_say(pw, 'es')} de su capacidad.")
        return (f"The camera settles on {label}. " if en else f"La cámara se detiene en {label}. ") + gauge
    # a heat wave, demand or a last straw with no weak-point framing: the first line to overload, marked, with its gauges
    far = pw is not None and pw > B.PCT_FAR
    if pw is None:
        return (f"The camera centers on {label}, the first to overload, marked in white." if en
                else f"La cámara se centra en {label}, la primera en sobrecargarse, con una marca blanca.")
    if far:
        return (f"The camera centers on {label}, marked in white, with a gauge showing its loading when it tripped: far past its limit." if en
                else f"La cámara se centra en {label}, con una marca blanca y un medidor de su carga al dispararse: muy por encima de su límite.")
    if po is not None and w.sites:
        return (f"The camera centers on {label}, marked in white. Two gauges compare its loading with the new load, {B.pct_say(pw, 'en')}, and without it, {B.pct_say(po, 'en')}." if en
                else f"La cámara se centra en {label}, con una marca blanca. Dos medidores comparan su carga con la nueva carga, el {B.pct_say(pw, 'es')}, y sin ella, el {B.pct_say(po, 'es')}.")
    return (f"The camera centers on {label}, marked in white, with a gauge showing its loading when it tripped: {B.pct_say(pw, 'en')} of its rating." if en
            else f"La cámara se centra en {label}, con una marca blanca y un medidor de su carga al dispararse: el {B.pct_say(pw, 'es')} de su capacidad.")


def _fix(w, s, lang):
    B = _b()
    en = lang == "en"
    if w.verdict == "nothing_happened" or not (s.get("options") or []):
        room = w.room
        if room is None:
            return None
        return (f"The map stays calm: no line goes over its limit, and the campus circle marks the site, with room for {B.mw_say(room, 'en')} before the first overload in the model." if en
                else f"El mapa sigue en calma: ninguna línea pasa de su límite, y el círculo del campus marca el sitio, con margen de {B.mw_say(room, 'es')} antes de la primera sobrecarga en el modelo.")
    opts = _walked(s)
    if not opts:
        return None
    n = len(opts)
    lead = opts[0]
    fam = lead.get("family")
    cost = lead.get("cost") or {}
    hi = float(cost.get("high") or 0)
    k = len(cost.get("items") or lead.get("lines") or [])
    if hi:
        _fact(w, "fix.lead_cost", "Cost of the first option, high end (as its green tag prints it)", hi, "USD", True)
    usd = B.usd_say(hi, lang)[0] if hi else None
    if en:
        intro = (f"Overloaded lines show in red, then {_plain_num(n, 'en')} verified options appear one at a time. " if n > 1
                 else "Overloaded lines show in red, then the verified option appears. ")
        if fam in ("upgrade", "combo") and k:
            tail = f"The first draws {_plain_num(k, 'en')} upgrade{'s' if k != 1 else ''} in green with cost tags{f', up to an estimated {usd} in all' if usd else ''}."
        elif fam == "flexible":
            tail = "The first tags the campus with its size stepping down at peak hours, with no new equipment."
        elif fam == "onsite":
            tail = "The first marks an on-site power plant at the campus, and what the grid still supplies."
        elif fam == "move":
            tail = "The first shows the campus at another substation, away from the weak point."
        else:
            tail = "The first shows its change to the campus in green."
        return intro + tail
    intro = (f"Las líneas sobrecargadas van en rojo y luego aparecen {_plain_num(n, 'es', fem=True)} opciones verificadas, una por una. " if n > 1
             else "Las líneas sobrecargadas van en rojo y luego aparece la opción verificada. ")
    if fam in ("upgrade", "combo") and k:
        tail = f"La primera dibuja {_plain_num(k, 'es', fem=True)} mejora{'s' if k != 1 else ''} en verde con sus costos estimados{f', hasta {usd} en total' if usd else ''}."
    elif fam == "flexible":
        tail = "La primera etiqueta el campus con su tamaño bajando en horas pico, sin equipo nuevo."
    elif fam == "onsite":
        tail = "La primera marca una planta propia en el campus y lo que la red sigue aportando."
    elif fam == "move":
        tail = "La primera muestra el campus en otra subestación, lejos del punto débil."
    else:
        tail = "La primera muestra en verde su cambio al campus."
    return intro + tail


def _no_fix(w, s, lang):
    B = _b()
    en = lang == "en"
    if not _area_names(w):
        return None
    n = int(w.people or 0)
    fr = frame(w, [], lang)
    tail = ""
    if n:
        tail = f" {B.cap(B.people_say(n, 'en'))} stay without power in the estimate." if en else f" {B.cap(B.people_say(n, 'es'))} siguen sin luz según la estimación."
    return (f"The areas no fix can reach are outlined in red hatching across {fr}, and the first is labeled.{tail}" if en
            else f"Las zonas que ninguna solución alcanza tienen contorno de rayas rojas por {fr}, y la primera lleva etiqueta.{tail}")


def _recovery(w, s, lang):
    B = _b()
    en = lang == "en"
    waves = (w.r.get("recovery") or {}).get("waves") or []
    if not waves:
        return None
    back = int(waves[0].get("people_back") or 0)
    fr = frame(w, [], lang)
    if back:
        return (f"Rebuilt lines light up green, wave by wave, across {fr}, as a strip counts the people who get power back: an estimated {B.people_round(back, 'en')} after the first wave." if en
                else f"Las líneas reconstruidas se encienden en verde, ola por ola, por {fr}, y una franja cuenta a quienes recuperan la luz: según la estimación, {B.people_say(back, 'es', noun=False)} tras la primera.")
    return (f"Rebuilt lines light up green, wave by wave, across {fr}, as a strip counts the waves of the rebuilding plan." if en
            else f"Las líneas reconstruidas se encienden en verde, ola por ola, por {fr}, y una franja cuenta las olas del plan de reconstrucción.")


def _bottom(w, s, lang):
    en = lang == "en"
    if w.r.get("no_fix"):
        fr = frame(w, [], lang)
        return (f"Green lines keep lighting wave by wave across {fr}, over the areas that lost power, showing where rebuilding starts." if en
                else f"Líneas verdes siguen encendiéndose ola por ola por {fr}, sobre las zonas sin luz, y muestran dónde empieza la reconstrucción.")
    if w.verdict == "nothing_happened" or not _walked(_fix_slide(w)):
        return ("The map rests on the campus site, calm: in the model no line goes over its limit here." if en
                else "El mapa queda en el sitio del campus, en calma: en el modelo ninguna línea pasa de su límite aquí.")
    return ("The map frames the leading fix in green with its cost tags, drawn over faint red hatching where the blackout would have been." if en
            else "El mapa encuadra la mejor solución en verde con sus costos, sobre rayas rojas tenues donde habría estado el apagón.")


BEATS = {"toll": _toll, "event": _event, "chain": _chain, "areas": _areas, "hospitals": _hospitals, "cost": _cost, "cause": _cause,
         "fix": _fix, "no_fix": _no_fix, "recovery": _recovery, "bottom_line": _bottom}


# ---------------------------------------------------------------------------------------------- checks
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def problems(text: str) -> list[str]:
    """Why a description breaks the rules (12 to 35 words, at most two sentences, no prediction); empty when it is fine."""
    out = []
    n = len(text.split())
    if not MIN_WORDS <= n <= MAX_WORDS:
        out.append(f"{n} words")
    if len(_SENTENCE_END.findall(text)) > MAX_SENTENCES:
        out.append("more than two sentences")
    if re.search(r"\bwill\b", text, re.IGNORECASE):
        out.append("a prediction")
    return out


def text_for(w, slide: dict, lang: str) -> str | None:
    """The description of one slide in one language, checked; None when the beat has none or it fails a check."""
    fn = BEATS.get(slide.get("kind") or slide.get("id"))
    if fn is None:
        return None
    try:
        text = fn(w, slide, lang)
    except Exception:  # noqa: BLE001 — a description must never take the deck down
        log.exception("describe: %s (%s) failed", slide.get("id"), lang)
        return None
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    why = problems(text)
    if not why:
        ok, reason, _ = _b().check_segment(text, w, lang)
        if not ok:
            why = [reason or "the number check"]
    if why:
        log.warning("describe: %s (%s) dropped: %s | %s", slide.get("id"), lang, "; ".join(why), text)
        return None
    return text


def attach(w, slides: list[dict]) -> None:
    """Put `describe` {lang: {text, key, chars}} on every slide of the deck that has one, with voice keys."""
    w._describe_slides = slides  # the bottom line reads the fix slide's options
    for s in slides:
        got = {}
        for lang in LANGS:
            text = text_for(w, s, lang)
            if not text:
                continue
            nxt = next(iter((s.get("narration") or {}).get(lang) or []), None)
            key = voice.register(text, lang, "analyst", next_text=(nxt or {}).get("text"))
            got[lang] = {"text": text, "key": key, "chars": len(text)}
        if len(got) == len(LANGS):  # both languages or none: the toggle must not switch a beat on in one language only
            s["describe"] = got
