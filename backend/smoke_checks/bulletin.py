"""Smoke checks for the briefing writer (backend/bulletin.py): the narrated slide deck and the legacy
one-paragraph bulletin. Loaded by smoke_test.py. Public and read-only: nothing is created.

With GEMINI_API_KEY blank (scripts/check.sh) the AI deck must come back complete and flagged
fallback; with a key it may be Gemini-written, but every number it speaks is checked server-side."""

import re

HEX32 = re.compile(r"^[0-9a-f]{32}$")
# alert phrasing and names the briefing must never produce (CLAUDE.md → Decisions, contract v2)
NEVER = re.compile(
    r"(?i:emergency alert|this is not a test|evacuat|shelter|\bFEMA\b|national weather service|alerta de emergencia|evacu[aá])"
    r"|\b[Hh]urricane [A-Z][a-z]+|\b(FPL|TECO|JEA|Duke Energy)\b"
)


def register(ctx):
    hero = ctx.expected["hero"]
    case = {"lat": hero["lat"], "lon": hero["lon"], "mw": hero["mw"]}
    state = {}

    def segments(deck, lang):
        return [seg for s in deck["slides"] for seg in s["narration"][lang]]

    def test_hero_deck():
        d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False})
        state["deck"] = d
        ids = [s["id"] for s in d["slides"]]
        assert ids[:4] == ["toll", "event", "chain", "areas"] and ids[-1] == "bottom_line", ids
        assert 5 <= len(ids) <= 10 and len(set(ids)) == len(ids), ids
        assert d["ai"]["by"] == "template" and d["region"] == "FL", d["ai"]
        for lang, open_words in (("en", ("simulat", "synthetic")), ("es", ("simula", "sintétic"))):
            first = d["slides"][0]["narration"][lang][0]["text"].lower()
            assert all(w in first for w in open_words), f"{lang} opening lacks {open_words}: {first[:90]!r}"
            segs = segments(d, lang)
            last = segs[-1]["text"]
            assert ("End of simulated briefing" if lang == "en" else "Fin del simulacro") in last, last
            assert d["total_chars"][lang] <= d["budget"][lang], (lang, d["total_chars"], d["budget"])
            assert sum(s["chars"] for s in segs) == d["total_chars"][lang]
            for s in segs:
                assert HEX32.match(s["key"]) and s["chars"] == len(s["text"]) and s["role"] in ("presenter", "analyst"), s
                assert all(0 <= c["char"] <= s["chars"] for c in s["cues"]), s["cues"]
        chain = next(s for s in d["slides"] if s["id"] == "chain")
        steps = {c["value"] for seg in chain["narration"]["en"] for c in seg["cues"] if c["name"] == "step"}
        want = set(range(1, hero["cascade_steps"] + 1))
        assert want <= steps, f"chain cues cover {sorted(steps)}, expected {sorted(want)}"
        assert chain["map"]["mode"] == "replay" and chain["camera"]["points"], chain["camera"]
        areas = next(s for s in d["slides"] if s["id"] == "areas")
        assert any(c["name"] == "area" for seg in areas["narration"]["en"] for c in seg["cues"]), "no area cues"
        assert "estimate" in d["slides"][0]["big"]["label"]["en"], d["slides"][0]["big"]
        # the lead: expected cost (the high end) and time without power, the same numbers the cost panel shows
        toll = d["slides"][0]
        assert toll["kind"] == "toll" and toll["big"]["display"]["en"].startswith("$") and toll["big2"]["display"]["en"], toll
        panel = ctx.request("POST", "/api/cost", case)["headline"]
        assert abs(toll["big"]["value"] - panel["cost_high"]) <= 0.02 * panel["cost_high"], (toll["big"]["value"], panel["cost_high"])
        assert abs(toll["big2"]["value"] - panel["outage_hours"]) < 0.5, (toll["big2"]["value"], panel["outage_hours"])
        assert toll["headline"]["en"].startswith("Expected cost") and "expected cost" in toll["narration"]["en"][0]["text"].lower(), toll["headline"]
        assert "costo esperado" in toll["narration"]["es"][0]["text"].lower(), toll["narration"]["es"][0]["text"]
        assert not any("simulation" in seg["text"].lower() for seg in d["slides"][1]["narration"]["en"]), "the SIMULATION opening is repeated after the toll"
        blob = str(d)
        m = NEVER.search(blob)
        assert not m, f"forbidden phrase in the deck: {m.group(0)!r}"
        m = re.search(r"\b(play|jugada)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\b", blob, re.IGNORECASE)
        assert not m, f"a numbered play label in the deck: {m.group(0)!r}"

    def test_solutions_and_play_by_play():
        d = state["deck"]
        fix = next(s for s in d["slides"] if s["id"] == "fix")
        opts = fix["options"]
        assert len(opts) >= 2, f"always more than one solution: {[o['family'] for o in opts]}"
        first = opts[0]
        # PROPORTIONATE (PRESENT V2): the lead keeps the campus at full size, the smallest verified upgrade of the weak
        # point, or an operating rule that only steps down at the peak; a smaller campus, another site or on-site
        # generation never lead or get a beat of their own while a full-size option holds ("More options" only)
        flex_lead = first["family"] == "flexible" and ((fix.get("present") or {}).get("flex") or {}).get("peak_only")
        assert first["role"] == "lead" and (flex_lead or (first["family"] in ("upgrade", "agentic") and (first["kept_pct"] or 0) >= 99.5)), \
            f"the lead should keep the campus at full size: {first['family']} {first['kept_pct']}"
        assert all(o["role"] == "more" for o in opts if o["family"] in ("shrink", "move", "combo", "remove")), [(o["family"], o["role"]) for o in opts]
        # DISTINCT OPTIONS (user, Sat 19:12): the options walked through are different kinds of fix (on-site power may be
        # one, never the lead while a full-size upgrade holds); plans raising the same elements for about the same price
        # are folded into the option they vary (role 'variant'), never walked as options of their own
        walked = [o for o in opts if o["role"] in ("lead", "alt")]
        assert 2 <= len(walked) <= 4 and len({o.get("kind") for o in walked}) == len(walked), [(o["family"], o.get("kind")) for o in walked]
        assert all(o["role"] != "lead" for o in opts if o["family"] == "onsite"), "on-site power never leads while a full-size upgrade holds"
        ups = lambda o: set((o.get("apply") or {}).get("upgrades") or {})  # noqa: E731
        by_fix = {o["fix"]: o for o in walked}
        for v in (o for o in opts if o["role"] == "variant"):
            head = by_fix.get(v.get("variant_of"))
            assert head and head["cost"] and v["cost"], ("a variant is folded into a walked, priced option", v.get("variant_of"))
            assert ups(head) <= ups(v) or ups(v) <= ups(head) or len(ups(head) & ups(v)) / len(ups(head) | ups(v)) >= 0.6, (ups(head), ups(v))
            assert abs(v["cost"]["high"] - head["cost"]["high"]) <= 0.25 * head["cost"]["high"] + 1, (v["cost"], head["cost"])
        # no two walked upgrade options raise the same elements at about the same price (with Gemini's plans in, the hero's
        # three are variants of one): the same elements twice only when the later one costs much less ('upgrade_cheaper')
        same_core = lambda a, b: bool(a and b) and (a <= b or b <= a or len(a & b) / len(a | b) >= 0.6)  # noqa: E731
        priced = [o for o in walked if o["family"] in ("upgrade", "agentic") and (o.get("cost") or {}).get("high")]
        for x, a in enumerate(priced):
            for b in priced[x + 1:]:
                if same_core(ups(a), ups(b)):
                    assert b["cost"]["high"] < 0.75 * a["cost"]["high"] and b.get("kind") == "upgrade_cheaper", \
                        ("two walked options raise the same elements", a["name"]["en"], a["cost"]["high"], b["name"]["en"], b["cost"]["high"], b.get("kind"))
        # the variants say exactly what they raise next to their option: "the same line and transformer, each with one
        # more line", never a bare "the same elements" (which invites "why does the same work cost more?")
        for h in walked:
            vs = [v for v in opts if v["role"] == "variant" and v.get("variant_of") == h["fix"]]
            if vs:
                how = (h.get("variants_how") or {}).get("en") or ""
                assert how and (h.get("variants_how") or {}).get("es"), ("the variants' line", h["name"]["en"])
                if all(ups(h) < ups(v) for v in vs):
                    assert "with" in how and " more " in how, how
        assert "raising the same elements" not in str(fix["narration"]) and "the same elements;" not in str(fix["narration"]), "say what the variants add"
        assert not any(o["family"] == "remove" for o in opts), "'don't build it' is never offered as a solution"
        # HERO PATH POLISH (Sat 23:40): no two options the page lists (walked or under "More options") share a title
        for lang in ("en", "es"):
            titles = [o["name"][lang] for o in opts if o["role"] != "variant"]
            assert len(titles) == len(set(titles)), (lang, "two options share a title", titles)
        pres = fix.get("present") or {}
        assert (pres.get("blackout") or {}).get("high", 0) > 0 and (pres.get("often") or {}).get("levels"), pres
        if first.get("cost"):  # an upgrade: each element pinned on the map with its own price, adding up to the total
            assert all(it["high"] >= it["low"] > 0 and it["work"] in ("transformer", "reconductor", "new_line") for it in first["cost"]["items"]), first["cost"]
            assert abs(sum(it["high"] for it in first["cost"]["items"]) - first["cost"]["high"]) <= 2, "the pinned per-element prices add up to the total"
        assert all(o["verdict"] == "holds" and o["must"]["en"] and o["must"]["es"] for o in opts), "every listed solution is verified and says what you have to do"
        assert flex_lead or (first["cost"] and first["cost"]["high"] >= first["cost"]["low"] > 0), first["cost"]
        assert [g["cues"] for g in fix["narration"]["en"][1:]] and all(any(c["name"] == "option" for c in g["cues"]) for g in fix["narration"]["en"][1:]), "each option is cued"
        assert "you have to do" in fix["narration"]["en"][0]["text"], fix["narration"]["en"][0]["text"]
        # the headline counts only the options walked through, never the folded "More options"
        m = re.search(r": (\d+) verified", fix["headline"]["en"])
        if m:
            assert int(m.group(1)) == sum(1 for o in opts if o["role"] in ("lead", "alt")), (fix["headline"]["en"], [o["role"] for o in opts])
        fl = pres.get("flex")
        if fl:  # Duke's figure as reported (about 85 hours a year), never "0.25 % of the year's hours"
            assert fl["hours_assumed"] == 85 and "85 hours" in fl["assumption"] and "of the year" not in fl["assumption"], fl
        assert "weak point is overloaded at every" not in str(fix["narration"]), "the per-level check is the grid's, not the weak point's"
        chain = next(s for s in d["slides"] if s["id"] == "chain")
        arc = chain["arc"]
        assert 1 <= len(arc) <= 4 and [p_["k"] for p_ in arc] == list(range(1, len(arc) + 1)) and arc[0]["people_delta"] > 0, arc[:1]
        assert all({"k", "kind", "steps", "line_ids", "areas", "people_delta", "people_total", "text", "marks"} <= set(p_) for p_ in arc), arc[0]
        assert d["short"][:2] == ["toll", "chain"] and "fix" in d["short"] and d["short"][-1] == "bottom_line", d["short"]
        rep = ctx.request("POST", "/api/briefing", case)
        assert rep["solutions"] and rep["best_fix"] == rep["solutions"][0], (rep["best_fix"], rep["solutions"])
        assert rep["fixes"][rep["best_fix"]]["kept_pct"] >= 90, "best_fix keeps at least 90% of the campus when such a fix holds"
        assert isinstance((d.get("agentic") or {}).get("status", "off"), str)

    def test_chain_four_parts_plain_options():
        """THE CHAIN IN FOUR PARTS (Sat 23:37-23:48): the chain is one arc over the whole cascade, in order: four parts (fewer
        when the cascade is short), each ONE flowing sentence in EN and ES that is never labeled or numbered, its steps
        cued so the map still moves step by step, the running totals adding up to the panel's People hit.
        SOLUTIONS, SIMPLE (Sat 20:31): every walked option carries five plain lines in EN and ES with a sourced time."""
        d = state["deck"]
        chain = next(s for s in d["slides"] if s["id"] == "chain")
        arc = chain["arc"]
        n_steps = hero["cascade_steps"]
        assert len(arc) == min(4, n_steps), (len(arc), n_steps)
        assert [p_["kind"] for p_ in arc] == {4: ["origin", "spread", "outward", "end"], 3: ["origin", "spread", "end"], 2: ["origin", "end"], 1: ["only"]}[len(arc)], [p_["kind"] for p_ in arc]
        steps = [n for p_ in arc for n in p_["steps"]]
        assert steps == list(range(1, n_steps + 1)), ("consecutive phases that cover every step once", steps)
        assert all(p_["steps"] for p_ in arc) and arc[-1]["steps"][-1] == n_steps, "no empty part; the last holds the last step"
        assert sum(p_["people_delta"] for p_ in arc) == arc[-1]["people_total"] == chain["people"]["hit"], ([p_["people_delta"] for p_ in arc], chain["people"])
        label = re.compile(r"\b(play|jugada|step|paso|part|parte)\s*(\d+|one|two|three|four|five|uno|dos|tres|cuatro|cinco)\b", re.IGNORECASE)
        for lang, begins, ends in (("en", "It begins at", "It finally reaches"), ("es", "Empieza en", "Por fin llega")):
            segs = chain["narration"][lang]
            assert len(segs) == len(arc) and all(g["role"] == "analyst" for g in segs), [g["text"][:40] for g in segs]
            for g, p_ in zip(segs, arc):
                assert g["text"] == p_["text"][lang], (lang, g["text"], p_["text"][lang])
                assert len(re.findall(r"[.!?](?=\s|$)", g["text"])) == 1, ("one flowing sentence", g["text"])
                assert {c["value"] for c in g["cues"] if c["name"] == "step"} == set(p_["steps"]), (g["cues"], p_["steps"])
                assert not label.search(g["text"]) and not re.match(r"\s*(first|second|third|fourth|primero|segundo)\b", g["text"], re.IGNORECASE), g["text"]
                assert all(m in g["text"] for m in p_["marks"][lang]), ("every name and figure set in weight is in the sentence", p_["marks"][lang], g["text"])
            assert segs[0]["text"].startswith(begins) and (ends in segs[-1]["text"]), (lang, segs[0]["text"][:60], segs[-1]["text"][:60])
            assert not re.search(r"\d\s*(%|percent|por ciento)", " ".join(g["text"] for g in segs)), "no loading percentage is spoken or printed in the chain"
            assert not label.search(" ".join(chain["lines"][lang]) + chain["headline"][lang]), (chain["lines"][lang], chain["headline"][lang])
            assert all(len(x) <= 90 and not x.endswith("…") for x in chain["lines"][lang]), chain["lines"][lang]
        assert "an estimated" in arc[0]["text"]["en"] and "(estimate" not in arc[0]["text"]["en"] and "(estimate" not in arc[-1]["text"]["en"], (arc[0]["text"]["en"], arc[-1]["text"]["en"])
        fix = next(s for s in d["slides"] if s["id"] == "fix")
        for o in (o for o in fix["options"] if o["role"] in ("lead", "alt")):
            for lang in ("en", "es"):
                pl = (o.get("plain") or {}).get(lang) or {}
                assert pl.get("what") and pl.get("cost") and pl.get("prevents") and pl.get("time"), (lang, o["family"], pl)
            t = o.get("time") or {}
            assert t.get("none") or (t["lo"] <= t["hi"] and t["sources"] and t["sources"][0]["url"].startswith("https://")), (o["family"], t)
            if (o.get("cost") or {}).get("high"):
                assert (o.get("cost_source") or {}).get("url", "").startswith("https://"), o.get("cost_source")
            # Spanish figures read the Spanish way ("1,27 millones", "48 mil"), never with English thousands commas
            assert not re.search(r"\d,\d{3}", o["plain"]["es"]["prevents"]), o["plain"]["es"]["prevents"]
            # an operating rule names each hour once (a case at its lowest level is both the first step and its own)
            for lang in ("en", "es"):
                w_ = o["plain"][lang]["what"]
                assert not re.search(r"(\b[\d,.]+ MW [^;]+?) (?:and|y) \1\b", w_), (lang, w_)
        hosp = next((s for s in d["slides"] if s["id"] == "hospitals"), None)
        if hosp:  # REVIEW-1 (e): an assumption, never stated as a fact
            text = " ".join(g["text"] for lang in ("en", "es") for g in hosp["narration"][lang])
            assert "assumed" in text.lower() and "se supone" in text.lower(), text[:300]
            # HERO PATH POLISH (Sat 23:40): the list beside the map names every area the map labels (up to three), then the source line
            per = [x for x in (ctx.request("POST", "/api/briefing", case).get("hospitals") or {}).get("areas") or [] if x.get("count")][:3]
            for lang in ("en", "es"):
                ls = hosp["lines"][lang]
                assert all(any(ln.startswith(f"{x['area']} · {x['count']} ") for ln in ls) for x in per), (lang, [x["area"] for x in per], ls)
                assert ls[-1].startswith("Counts only" if lang == "en" else "Solo conteos"), (lang, ls)

    def test_prefetch_waits_to_propose():
        """A prefetch outside Florida (propose: false) builds the deck without starting the AI proposer; the stage's own
        request for the same case starts it (with a key: running; without one: off)."""
        import time as _t

        fresh = {**case, "mw": hero["mw"] - 13 + round(_t.time() % 1, 3)}  # a case this server has not seen
        for _ in range(2):  # asked twice: the second answer would carry the proposer's status had the first started it
            d = ctx.request("POST", "/api/briefing/deck", {**fresh, "ai": False, "propose": False})
            assert d["slides"] and d.get("agentic") is None, d.get("agentic")
        ctx.request("POST", "/api/briefing/deck", {**fresh, "ai": False})  # starts it (a cached report shows it next time)
        d3 = ctx.request("POST", "/api/briefing/deck", {**fresh, "ai": False})
        assert isinstance(d3.get("agentic"), dict) and d3["agentic"].get("status") in ("running", "off", "done", "error"), d3.get("agentic")

    def test_storm_chain_arc():
        """THE CHAIN IN FOUR PARTS after a storm: the storm's own damage opens the first part (and is named there, once), at
        most four parts, none labeled, every step still cued, the running totals ending on the panel's People hit."""
        d = ctx.request("POST", "/api/briefing/deck", {"preset": "fl-cat5-statewide", "ai": False})
        chain = next(s for s in d["slides"] if s["id"] == "chain")
        arc = chain["arc"]
        assert 1 <= len(arc) <= 4 and 0 in arc[0]["steps"], [(p_["kind"], p_["steps"]) for p_ in arc]
        assert "storm" in arc[0]["text"]["en"].lower() and "tormenta" in arc[0]["text"]["es"].lower(), arc[0]["text"]
        assert sum(p_["people_delta"] for p_ in arc) == arc[-1]["people_total"], [p_["people_delta"] for p_ in arc]
        for lang in ("en", "es"):
            assert len(chain["narration"][lang]) == len(arc)
            assert not re.search(r"\b(play|jugada)\s*\d", str(chain["narration"][lang]) + str(chain["lines"][lang]), re.IGNORECASE)
            assert all(len(x) <= 90 and not x.endswith("…") for x in chain["lines"][lang]), chain["lines"][lang]

    def test_chain_arc_checks():
        """Gemini's version of a chain part is one plain sentence in its own language that keeps the part's names and figures:
        a labeled, two-sentence, wrong-figure, mixed-language or field-name-leaking rewrite is refused; a light rephrase passes."""
        import os
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")
        try:
            import bulletin as B
        except ImportError as e:  # a deployed run without the backend folder on the path
            raise AssertionError(f"bulletin.py not importable here: {e}") from e
        rep = ctx.request("POST", "/api/briefing", case)
        comp = B.compose(rep, "full", None)
        w = comp["writer"]
        arc = w._arc
        assert len(arc) == min(4, hero["cascade_steps"]), len(arc)
        good = {lang: B.chain_arc.sentence(w, arc[0], lang)[0] for lang in ("en", "es")}
        for lang in ("en", "es"):
            ok, why, _ = B.validate_ai(w, "chain#1", lang, good[lang], 600, good[lang])
            assert ok, (lang, why, good[lang])
        en = good["en"]
        rephrase = en.replace("It begins at", "It starts at").replace(", causing damage that hits", ", and the damage hits")
        ok, why, _ = B.validate_ai(w, "chain#1", "en", rephrase, 600, en)
        assert ok, ("a light rephrase passes", why, rephrase)
        bad = {
            "a label": "Play 1: " + en,
            "two sentences": en[:-1] + ". It spreads.",
            "an ordinal": "First, " + en[0].lower() + en[1:],
            "another part's figure": en.replace(B.people_round(arc[0]["people_delta"], "en"), B.people_round(arc[-1]["people_total"] + 1234567, "en")),
            "a leaked field name": en.replace("causing damage", "causing expectedcost damage"),
            "an underscore": en.replace("causing", "causing_damage"),
        }
        for what, text in bad.items():
            ok, why, _ = B.validate_ai(w, "chain#1", "en", text, 600, en)
            assert not ok, (what, text)
        ok, why, _ = B.validate_ai(w, "chain#1", "es", en, 600, good["es"])
        assert not ok, "English text for the Spanish slot"
        # the ai slots: one per part per language, each with the part's own template
        slots = [x for x in B.ai_slots(comp) if x["id"].startswith("chain#")]
        assert len(slots) == 2 * len(arc) and {x["id"] for x in slots} == {f"chain#{k}" for k in range(1, len(arc) + 1)}, [x["id"] for x in slots]

    def test_hero_fix_holds():
        d = state["deck"]
        fix = next((s for s in d["slides"] if s["id"] in ("fix", "no_fix")), None)
        assert fix is not None and fix["id"] == "fix" and d["verdict"] == "preventable", [s["id"] for s in d["slides"]]
        bottom = d["slides"][-1]
        apply = (bottom.get("cta") or {}).get("apply")
        assert apply, "the bottom line has no 'apply the best fix'"
        c = ctx.request("POST", "/api/grid/cascade", {**case, **apply})
        assert c["total_steps"] == 0, f"the deck's best fix still cascades: {c['total_steps']} steps"

    def test_one_set_of_numbers():
        # ONE SET OF NUMBERS (the results panel, the deck, its ticker and the brief say the same figures):
        # people hit = the cascade's people_hit (the panel's counter), still without power when it settles =
        # its people; the outage length as costs.outage_label writes it; the cost at the high end; no peak figure;
        # and the deck's "Apply the best fix" is the report's best fix (the panel's "Run it again with the fix")
        d = state["deck"]
        c = ctx.request("POST", "/api/grid/cascade", case)
        panel = ctx.request("POST", "/api/cost", case)["headline"]
        toll, event = d["slides"][0], next(s for s in d["slides"] if s["id"] == "event")
        hit, still = int(c.get("people_hit") or c["people"]), int(c["people"])
        assert toll["people"] == {"hit": hit, "still_out": still}, (toll.get("people"), hit, still)
        assert event["big"]["value"] == hit and "hit" in event["big"]["label"]["en"], event["big"]
        assert f"{hit:,} people hit" in event["headline"]["en"], event["headline"]["en"]
        assert f"{hit:,}" in " ".join(toll["lines"]["en"]) and f"still out when it settled: an estimated {still:,}" in " ".join(toll["lines"]["en"]), toll["lines"]["en"]
        assert toll["big2"]["display"]["en"] == panel["outage_label"] and re.fullmatch(r"about \d+ (hours|days)|about an hour", panel["outage_label"]), (toll["big2"]["display"], panel["outage_label"])
        assert toll["headline"]["en"].endswith(f"{panel['outage_label']} without power"), toll["headline"]["en"]
        blob = " ".join(" ".join([s["headline"]["en"], *s["lines"]["en"], *(g["text"] for g in s["narration"]["en"])]) for s in d["slides"])
        assert "worst step" not in blob and "Peak:" not in blob, "a peak figure is back in the deck"
        assert not re.search(r"\b\d+\.\d hours\b", blob), "an outage length with decimals (the panel says 'about N hours')"
        rep = ctx.request("POST", "/api/briefing", case)
        best = rep["fixes"][rep["best_fix"]]
        bottom = d["slides"][-1]
        assert (bottom.get("cta") or {}).get("apply") == best["apply"], (bottom.get("cta"), best["apply"])
        if best.get("cost"):  # the bottom line names the fix's cost at the high end, as the panel's flip does
            assert "$" in bottom["narration"]["en"][0]["text"], bottom["narration"]["en"][0]["text"][:160]

    def test_short_deck():
        d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False, "length": "short"})
        ids = [s["id"] for s in d["slides"]]
        assert ids == d["short"] and ids[0] == "toll" and ids[-1] == "bottom_line" and len(ids) <= 6, ids
        assert "cause" in ids and ("fix" in ids or "no_fix" in ids), f"the presentation must say why it failed and what to do: {ids}"
        assert d["est_s"]["en"] <= 110 and d["total_chars"]["en"] <= d["budget"]["en"], (d["est_s"], d["total_chars"])

    def test_ai_deck():
        configured = ctx.request("GET", "/api/ai/status")["configured"]
        d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": True, "length": "short"})
        assert isinstance(d["ai"]["fallback"], bool) and d["ai"]["by"] in ("gemini", "mixed", "template"), d["ai"]
        if not configured:
            assert d["ai"]["fallback"] is True and d["ai"]["by"] == "template", d["ai"]
        assert d["slides"][0]["narration"]["en"][0]["text"].startswith("This is a simulation"), "the fixed opening is missing"
        m = NEVER.search(str(d))
        assert not m, f"forbidden phrase in the AI deck: {m.group(0)!r}"

    def test_legacy_bulletin():
        configured = ctx.request("GET", "/api/ai/status")["configured"]
        b = ctx.request("POST", "/api/bulletin", case)
        assert isinstance(b["text"], str) and len(b["text"].strip()) > 40, f"bulletin text too short: {b['text']!r}"
        assert "synthetic" in b["text"].lower() and "simulation" in b["text"].lower(), b["text"][:120]
        assert isinstance(b["fallback"], bool) and (b["fallback"] or configured), b["fallback"]
        keys = {f["key"] for f in b["facts"]}
        assert "event.people_out" in keys and "event.steps" in keys, sorted(keys)[:10]

    def test_honest_storm_and_heat():
        # a storm the data center did not cause: "no fix" deck, and nothing says upgrades "prevent" it
        d = ctx.request("POST", "/api/briefing/deck", {**case, "preset": "fl-gulf-fort-myers", "region": "FL", "ai": False})
        ids = [s["id"] for s in d["slides"]]
        assert d["verdict"] == "no_fix" and "no_fix" in ids and "fix" not in ids, (d["verdict"], ids)
        for s in d["slides"]:
            if s["id"] in ("cost", "no_fix", "recovery", "bottom_line"):
                blob = " ".join([s["headline"]["en"], *s["lines"]["en"], *(g["text"] for g in s["narration"]["en"])]).lower()
                assert "prevent it" not in blob and "would prevent" not in blob, f"{s['id']} claims a fix prevents a storm outage: {blob[:200]}"
        # demand alone, no campus: no slide may speak of a data center that is not there
        h = ctx.request("POST", "/api/briefing/deck", {"load_factor": 1.08, "ai": False})
        for s in h["slides"]:
            blob = " ".join([s["headline"]["en"], *s["lines"]["en"], *(g["text"] for g in s["narration"]["en"])]).lower()
            assert "data center" not in blob and " it only at" not in blob, f"{s['id']} speaks of a data center with none placed: {blob[:200]}"

    def test_peak_means_the_peak():
        # "only at the peak" is relative to the 4 PM peak, whatever level the case is at: at 9 AM an overload that
        # starts at 9 AM is not a peak-only one, and in a heat wave an overload only there is not "the summer peak"
        for body, early in (({**case, "mw": 1000, "load_factor": 0.82}, True), ({**case, "mw": 500, "load_factor": 1.04}, False)):
            d = ctx.request("POST", "/api/briefing/deck", {**body, "ai": False, "length": "short"})
            fix = next((s for s in d["slides"] if s["id"] == "fix"), None)
            if fix is None:
                continue
            pres = fix.get("present") or {}
            often = pres.get("often") or {}
            over = [float(x["level"]) for x in often.get("levels") or [] if x.get("over")]
            blob = " ".join(g["text"] for g in fix["narration"]["en"])
            if any(lv < 0.995 for lv in over):
                assert not often.get("peak_only") and not often.get("heat_only") and not (pres.get("flex") or {}).get("peak_only"), (body, often)
                assert "only comes" not in blob and "except at the summer peak" not in blob, blob[:300]
                assert fix["options"][0]["family"] != "flexible", "an overload from the morning on is not led by a peak-hour rule"
            elif over and all(lv > 1.005 for lv in over):
                assert often.get("heat_only"), often
                assert "summer peak" not in blob, f"a heat-wave-only overload is not 'the summer peak': {blob[:300]}"
            fl = pres.get("flex")
            if fl:
                tot = float(pres.get("total_mw") or body["mw"])
                assert all(abs(st["step_mw"] - (tot - st["runs_mw"])) <= 0.2 for st in fl["steps"]), fl["steps"]
                assert fl["step_down_mw"] == max((st["step_mw"] for st in fl["steps"]), default=fl["step_down_mw"]), fl
            assert early or over, (body, often)

    def test_agent_trace_in_deck():
        # "Watch the AI work": the fix slide carries the AI proposer's run (solutions.py → report.agentic) with its trace.
        # Without a key the proposer is off and the trace is empty; with one, once it is done, every round is in it.
        import time

        configured = ctx.request("GET", "/api/ai/status")["configured"]
        d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False})
        deadline = time.time() + 45
        while configured and (d.get("agentic") or {}).get("status") in (None, "running") and time.time() < deadline:
            time.sleep(3)
            d = ctx.request("POST", "/api/briefing/deck", {**case, "ai": False})
        fix = next(s for s in d["slides"] if s["id"] == "fix")
        ag = fix.get("agentic")
        assert isinstance(ag, dict) and isinstance(ag.get("trace"), list) and ag.get("status") in ("off", "running", "done", "error"), ag
        if not configured:
            assert ag["status"] == "off" and ag["trace"] == [], ag
            return
        if ag["status"] != "done" or not ag.get("asked"):
            return  # Gemini unavailable or out of quota: the engine's own fixes stand, nothing to trace
        tr = ag["trace"]
        assert tr and len(tr) <= 40 and [t["n"] for t in tr] == list(range(1, len(tr) + 1)), [t.get("n") for t in tr]
        assert tr[0]["kind"] == "ask" and tr[-1]["kind"] == "result", (tr[0]["kind"], tr[-1]["kind"])
        assert all(t["actor"] in ("gemini", "engine") and t["title"]["en"] and t["title"]["es"] for t in tr), tr[0]
        proposed = [t for t in tr if t["kind"] in ("propose", "revise")]
        checked = [t for t in tr if t["kind"] == "verify"]
        assert len(proposed) == ag["asked"] and len(checked) == len(proposed), (len(proposed), len(checked), ag["asked"])
        assert all(("holds" in t) and t["tone"] in ("holds", "over") for t in checked), checked[:1]
        assert sum(1 for t in checked if t["holds"] and not t.get("duplicate")) == ag["verified"], ag
        assert d["agentic"]["trace"] == tr or len(d["agentic"]["trace"]) >= len(tr), "the deck's agentic and the fix slide's disagree"

    def test_distinct_option_titles():
        """Two options that would read the same (two different AI upgrade sets, or the same elements at other ratings) are
        told apart by what differs, in both languages, and the first keeps its title; a title already free is left alone."""
        import os
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")
        try:
            import bulletin as B
        except ImportError as e:  # a deployed run without the backend folder on the path
            raise AssertionError(f"bulletin.py not importable here: {e}") from e

        def el(i, label, new, tr=False):
            return {"id": i, "label": label, "old_mva": 100.0, "new_mva": new, "transformer": tr, "km": 5.0}

        def row(name_en, name_es, lines, high):
            return {"name": {"en": name_en, "es": name_es}, "lines": lines, "cost": {"high": high}}

        core = [el(1, "the North Fort Myers 6 transformer", 2400.0, True), el(2, "the North Fort Myers 6 to Fort Myers 3 line", 2578.0)]
        t_en, t_es = "Upgrade two lines and one transformer", "Reforzar dos líneas y un transformador"
        rows = [
            row(t_en, t_es, core + [el(3, "the Bonita Springs 4 to Bonita Springs 2 line", 415.0)], 57.5e6),
            row(t_en, t_es, core + [el(4, "the Fort Myers 5 to North Fort Myers 6 line", 491.0)], 80.8e6),  # a different third element
            row(t_en, t_es, core + [el(3, "the Bonita Springs 4 to Bonita Springs 2 line", 830.0)], 90e6),  # the first's elements, larger ratings
            row("Add 950 MW of on-site generation", "Añadir 950 MW de generación propia", [], None),
        ]
        B._distinct_names(rows)
        for lang in ("en", "es"):
            titles = [r["name"][lang] for r in rows]
            assert len(set(titles)) == len(titles), (lang, titles)
        assert rows[0]["name"]["en"] == t_en and rows[3]["name"]["en"] == "Add 950 MW of on-site generation", "the first and the free titles are kept"
        assert "Fort Myers 5 to North Fort Myers 6" in rows[1]["name"]["en"] and "instead" in rows[1]["name"]["en"], rows[1]["name"]
        assert "larger ratings" in rows[2]["name"]["en"] or "Fort Myers 5" in rows[2]["name"]["en"], rows[2]["name"]
        assert rows[1]["name"]["es"].startswith(t_es) and "línea de Fort Myers 5 a North Fort Myers 6" in rows[1]["name"]["es"], rows[1]["name"]["es"]
        # the same elements, cheaper and smaller: told apart by their ratings
        a = row(t_en, t_es, core, 60e6)
        b = row(t_en, t_es, [el(1, core[0]["label"], 2000.0, True), el(2, core[1]["label"], 2000.0)], 40e6)
        B._distinct_names([a, b])
        assert "smaller ratings" in b["name"]["en"] and "menores" in b["name"]["es"] and a["name"]["en"] == t_en, (a["name"], b["name"])

    def test_validation():
        ctx.request("POST", "/api/briefing/deck", {}, expect=422)  # nothing happened
        ctx.request("POST", "/api/briefing/deck", {**case, "lat": 40}, expect=422)  # north of Florida
        ctx.request("POST", "/api/briefing/deck", {**case, "mw": 0}, expect=422)
        ctx.request("POST", "/api/briefing/deck", {**case, "trip": [-1]}, expect=422)
        ctx.request("POST", "/api/briefing/deck", {**case, "length": "epic"}, expect=422)
        ctx.request("POST", "/api/bulletin", {}, expect=422)
        ctx.request("POST", "/api/bulletin", {**case, "mw": 0}, expect=422)

    ctx.check("briefing deck (hero, templates): slide order, SIMULATION open/close EN+ES, budgets, cues for every step", test_hero_deck)
    ctx.check("briefing deck: several verified solutions, full size first, each with what you have to do; the chain has its four parts", test_solutions_and_play_by_play)
    ctx.check("briefing deck: the chain is four unlabeled sentences over the whole cascade (fewer when short), every step cued; every option in five plain lines", test_chain_four_parts_plain_options)
    ctx.check("briefing deck: after a storm the chain's first part names the storm, never a numbered play; Gemini's chain sentences are checked part by part", test_storm_chain_arc)
    ctx.check("briefing deck: a chain part written by Gemini keeps its names and figures, one plain sentence, its own language", test_chain_arc_checks)
    ctx.check("briefing deck: a prefetch with propose=false leaves the AI proposer for the stage's own request", test_prefetch_waits_to_propose)
    ctx.check("briefing deck: the hero is preventable and the deck's best fix really stops the cascade", test_hero_fix_holds)
    ctx.check("briefing deck: one set of numbers (people hit / still without power, 'about N hours', high-end cost) and the best fix the panel flips to", test_one_set_of_numbers)
    ctx.check("briefing deck (short): the <= 60 s demo version", test_short_deck)
    ctx.check("briefing deck with AI: complete with or without a key, fixed opening kept", test_ai_deck)
    ctx.check("legacy /api/bulletin: a paragraph from the deck, with the engine's facts", test_legacy_bulletin)
    ctx.check("briefing deck: a storm reads 'no fix' (no upgrade 'prevents' it); a heat-only case never mentions a data center", test_honest_storm_and_heat)
    ctx.check("briefing deck: 'only at the peak' is the 4 PM peak whatever the case's hour (9 AM, heat wave), step-downs per level", test_peak_means_the_peak)
    ctx.check("briefing deck: the fix slide carries the AI proposer's trace (every plan, the engine's verdict on each, the revisions)", test_agent_trace_in_deck)
    ctx.check("briefing deck: no two options share a title (told apart by which elements or ratings differ), in English and Spanish", test_distinct_option_titles)
    ctx.check("briefing deck rejects an empty case, a point outside Florida, a size of 0, an unknown line, a bad length", test_validation)
