"""Smoke checks for the hospital beds agent (backend/hospital_agent.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200), auth(), expected (expected_whatif.json).
Read-only against the server: the agent stores only its own checked facts (an in-memory cache and a runtime file).

Live: the hero's cascade (Fort Myers, 1,500 MW) -> its `affected` -> the agent; the hospitals it researches are exactly
the ones /api/hospitals/backup puts on backup power; every reported figure carries its source link and, when it was read
on the page, the page's own words holding the number; OSM figures carry their OpenStreetMap link; a finished clean run
comes back at once from the cache. In-process (stubbed Gemini and pages, no network, no quota): the grounded check and
the propose -> check -> feedback -> revise loop, the no-key fallback (OSM only) and the set cache; the honesty rules (a
page that doesn't name the hospital, a sibling's figure on a listing page, a same-named hospital in another state, one
unit's beds, a support spanning other hospitals' lines); the second model asked only when the first didn't search; the
miss cache."""

import asyncio
import os
import sys
import time
from pathlib import Path

POLL_S = 75


def register(ctx):
    state = {}

    def hero_case():
        h = ctx.expected["hero"]
        return {"lat": h["lat"], "lon": h["lon"], "mw": h["mw"]}

    def finish(r):
        t0 = time.time()
        while r["status"] == "running":
            assert time.time() - t0 < POLL_S, f"still running after {POLL_S} s: {r.get('counts')}"
            time.sleep(0.8)
            r = ctx.request("GET", f"/api/hospitals/agent/{r['job']}")
            assert isinstance(r.get("trace"), list) and isinstance(r.get("hospitals"), list), "a job view without its trace or hospitals"
        assert r["status"] == "done", r.get("status")
        return r

    def validation():
        ctx.request("POST", "/api/hospitals/agent", {"region": "ZZ", "affected": {}}, expect=422)
        ctx.request("POST", "/api/hospitals/agent", {"region": "FL", "affected": {"14173": -5}}, expect=422)
        ctx.request("POST", "/api/hospitals/agent", {"region": "FL", "load_factor": 9, "affected": {}}, expect=422)
        ctx.request("POST", "/api/hospitals/agent", {"region": "FL", "affected": {"not-a-sub": 5}}, expect=422)
        ctx.request("GET", "/api/hospitals/agent/nosuchjob123", expect=404)
        ctx.request("GET", "/api/hospitals/agent/bad!", expect=422)

    def nothing_dark():
        r = ctx.request("POST", "/api/hospitals/agent", {"region": "FL", "affected": {}})
        assert r["status"] == "done" and r["job"] is None and r["hospitals"] == [] and r["counts"]["hospitals"] == 0, r
        assert r["trace"] and r["trace"][-1]["kind"] == "result", r["trace"]

    def hero_run():
        cas = ctx.request("POST", "/api/grid/cascade", hero_case())
        body = {"region": "FL", "load_factor": 1.0, "affected": cas["affected"]}
        t0 = time.time()
        first = ctx.request("POST", "/api/hospitals/agent", body)
        r = finish(first)
        took = time.time() - t0
        state.update(body=body, res=r, took=took, first=first)
        # the same hospitals the backup route puts on backup power (one rule: hospitals.status_for)
        bk = ctx.request("POST", "/api/hospitals/backup", hero_case())
        assert sorted(h["id"] for h in r["hospitals"]) == sorted(h["id"] for h in bk["backup"]), "the agent's hospitals differ from /api/hospitals/backup's"
        assert r["counts"]["hospitals"] == len(r["hospitals"]) == bk["counts"]["backup"] > 0
        if not first.get("cached"):
            assert r["elapsed_s"] <= r["cap_s"] + 5, f"the run took {r['elapsed_s']} s (cap {r['cap_s']} s)"
        assert r["by"] in ("gemini", "fallback"), r["by"]
        assert "OpenStreetMap" in r["osm"]["source"], r["osm"]
        assert "not a count of the people" in r["assumption"], r["assumption"]

    def hero_rows():
        r = state["res"]
        c = r["counts"]
        for h in r["hospitals"]:
            basis = h["beds_basis"]
            assert basis in ("reported", "osm", "not_found"), f"{h['name']}: basis {basis!r}"
            assert h["state"] == "done", f"{h['name']}: state {h['state']}"
            if basis == "reported":
                assert isinstance(h["beds"], int) and 1 <= h["beds"] <= 4000, f"{h['name']}: {h['beds']}"
                assert h["source"] and h["source"]["url"].startswith(("https://", "http://")), f"{h['name']}: {h['source']}"
                assert h["checked"] is bool(h["page_confirmed"]), f"{h['name']}: checked {h['checked']} but page_confirmed {h['page_confirmed']}"
                if h["page_confirmed"]:
                    forms = (str(h["beds"]), f"{h['beds']:,}")
                    assert h["quote"] and any(f in h["quote"] for f in forms), f"{h['name']}: the page's words {h['quote']!r} don't hold {h['beds']}"
                    assert "bed" in h["quote"].lower(), f"{h['name']}: the page's words don't say bed: {h['quote']!r}"
                else:
                    assert h["page_confirmed"] is None and (h["note"] or "").startswith("Grounded in"), f"{h['name']}: {h['note']!r}"
            elif basis == "osm":
                assert h["beds"] == h["osm_beds"] and h["source"]["url"].startswith("https://www.openstreetmap.org/"), f"{h['name']}: {h['source']}"
                assert h["checked"] is False and "OpenStreetMap" in h["note"], h["note"]
            else:
                assert h["beds"] is None and h["source"] is None and h["note"], f"{h['name']}: {h}"
        assert c["reported"] + c["osm"] + c["not_found"] == c["hospitals"], c
        assert c["beds_total"] == sum(h["beds"] or 0 for h in r["hospitals"]), c
        if r["by"] == "fallback":
            assert c["reported"] == 0, "a fallback run with reported figures"
        kinds = [row["kind"] for row in r["trace"]]
        assert kinds[0] == "info" and kinds[-1] == "result", kinds
        assert all(row["actor"] in ("gemini", "engine") and row["title"].get("en") and row["title"].get("es") for row in r["trace"]), "a trace row without its actor or titles"
        if r["by"] == "gemini" and not state["first"].get("cached"):
            assert "propose" in kinds and "verify" in kinds, kinds

    def hero_cached():
        r = state["res"]
        if r["by"] != "gemini" or r.get("timed_out"):
            return  # only a clean Gemini run is remembered as a set
        t = time.time()
        again = ctx.request("POST", "/api/hospitals/agent", state["body"])
        assert again["status"] == "done" and again["job"] is None and again["cached"] is True, f"the second run is not the cached answer: {again.get('status')}"
        assert time.time() - t < 3, f"the cached answer took {time.time() - t:.1f} s"
        assert [(h["id"], h["beds"], h["beds_basis"]) for h in again["hospitals"]] == [(h["id"], h["beds"], h["beds_basis"]) for h in r["hospitals"]], "the cached answer differs"

    ctx.check("hospital agent: validation (422) and unknown jobs (404)", validation)
    ctx.check("hospital agent: an incident with no dark hospital answers at once", nothing_dark)
    ctx.check("hospital agent: the hero's dark hospitals researched within the cap (the backup route's list)", hero_run)
    ctx.check("hospital agent: every figure has its basis and source (reported: page words hold the number; OSM: its link)", hero_rows)
    ctx.check("hospital agent: the hero's second run is the cached answer, at once", hero_cached)

    # ------------------------------------------------------------------ in-process, stubbed (no network, no quota)
    def in_process(fn):
        def run():
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            os.environ.setdefault("JWT_SECRET", "smoke-check-only-not-a-secret")  # llm.py imports auth.py; this process only
            try:
                import hospital_agent as ha
                import llm
            except ImportError as e:  # a deployed run without the backend folder on the path
                raise AssertionError(f"hospital_agent.py not importable here: {e}") from e
            names = ("_read", "CACHE_FILE", "_facts", "_sets", "_misses", "_chips", "MODELS", "BATCH", "ATTEMPTS", "_region_names", "HEDGE_S",
                     "AI_RESERVE", "NEW_JOBS_MAX", "_starts", "_jobs", "_by_set", "_by_client", "_judge", "_public_url", "_peer_ok", "_resolve",
                     "PDF_OK")
            saved = {"key": os.environ.get("GEMINI_API_KEY"), "post": llm._post_json, **{n: getattr(ha, n) for n in names}}
            ha.CACHE_FILE = ""  # nothing stubbed reaches the disk
            ha._facts, ha._sets, ha._misses, ha._chips = {}, {}, {}, {}
            ha._jobs, ha._by_set, ha._by_client, ha._starts = type(ha._jobs)(), {}, type(ha._by_client)(), type(ha._starts)()
            ha._region_names = {"FL": []}  # the stub hospitals compete among themselves (no state grid loaded here)
            ha.MODELS, ha.BATCH = ["stub-model"], 10
            ha.AI_RESERVE = 0  # this process's own llm budget (no .env here) is not the server's
            try:
                fn(ha, llm)
            finally:
                llm._post_json = saved["post"]
                for n in names:
                    setattr(ha, n, saved[n])
                if saved["key"] is None:
                    os.environ.pop("GEMINI_API_KEY", None)
                else:
                    os.environ["GEMINI_API_KEY"] = saved["key"]

        return run

    HOSP = [
        {"id": 9001, "name": "Alpha General Hospital", "lat": 26.5, "lon": -81.8, "area": "Stubton", "beds": None},
        {"id": 9002, "name": "Bravo Medical Center", "lat": 26.6, "lon": -81.8, "area": "Stubton", "beds": 222},
        {"id": 9003, "name": "Charlie Regional Hospital", "lat": 26.7, "lon": -81.8, "area": "Stubton", "beds": None},
        {"id": 9004, "name": "Delta Hospital", "lat": 26.8, "lon": -81.8, "area": "Stubton", "beds": None},
    ]
    PAD = " ".join(["The hospital serves the region with emergency care, surgery and maternity services."] * 20)
    R = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
    CHIP = "<div>stub suggestions</div>"
    PAGES = {
        R + "A": ("https://a.example/about", "Alpha General Hospital in Stubton, FL is a 123-bed acute care hospital. " + PAD),
        R + "C1": ("https://c.example/facts", "Charlie Regional Hospital in Stubton, FL is licensed for 650 beds. " + PAD),
        R + "C2": ("https://c.example/facts", "Charlie Regional Hospital in Stubton, FL is licensed for 650 beds. " + PAD),
        R + "D": ("https://d.example/report.pdf", None),
        # the honesty cases
        R + "F": ("https://f.example/", "Foxtrot Memorial Hospital in Stubton, FL has 415 licensed beds. " + PAD),
        R + "L": ("https://l.example/list", "Hospitals of Stubton, FL. Golf Medical Center: 349 beds; Hotel Hospital: 291 beds. " + PAD),
        R + "U": ("https://u.example/scan.pdf", None),
        R + "K": ("https://k.example/", "Kilo Springs Hospital, Springfield, Ohio: a 500-bed hospital. " + PAD),
        R + "L2": ("https://l2.example/", "Lima Ridge Hospital, Stubton, FL. Lima Ridge Hospital Skilled Nursing Unit: 18 beds. " + PAD),
        R + "M": ("https://m.example/", "Mike Harbor Hospital in Stubton, FL is a 250-bed hospital. " + PAD),
        R + "N": ("https://n.example/", "November Bay Hospital in Stubton, FL is a 144-bed hospital. " + PAD),
        R + "O": ("https://o.example/", "Welcome. " + PAD),  # readable, but names neither the hospital nor the number
    }

    def fake_read(uri, title, pages, lock):
        url, text = PAGES[uri]
        return {"url": url, "title": url.split("/")[2], "text": text, "why": "" if text else "a PDF the checker could not read"}

    def answer(lines, supports, queries=("stub search",)):
        """A generateContent reply with grounding: `supports` = [(the answer's substring to tie, redirect uri)]."""
        text = "\n".join(lines)
        uris = []
        for _s, u in supports:
            if u not in uris:
                uris.append(u)
        gs = []
        for sub, u in supports:
            a = text.index(sub)
            gs.append({"segment": {"startIndex": a, "endIndex": a + len(sub), "text": sub}, "groundingChunkIndices": [uris.index(u)]})
        gm = {"webSearchQueries": list(queries), "groundingChunks": [{"web": {"uri": u, "title": "stub"}} for u in uris], "groundingSupports": gs,
              "searchEntryPoint": {"renderedContent": CHIP}} if queries else {}
        return {"candidates": [{"content": {"parts": [{"text": text}]}, "groundingMetadata": gm}]}

    REPLIES = [
        # attempt 1: A grounded on a page that states it; B's number tied to no page; C's to a page that says 650; D's to nothing
        answer(["1. Alpha General Hospital | 123 | licensed | a.example", "2. Bravo Medical Center | 456 | beds | b.example",
                "3. Charlie Regional Hospital | 789 | staffed | c.example", "4. Delta Hospital | 50 | beds | d.example"],
               [("Alpha General Hospital | 123 | licensed", "https://vertexaisearch.cloud.google.com/grounding-api-redirect/A"),
                ("Charlie Regional Hospital | 789 | staffed", "https://vertexaisearch.cloud.google.com/grounding-api-redirect/C1")]),
        # attempt 2 (B, C, D with the reasons): B not found after a search; C revised to the page's 650; D grounded in an unreadable PDF
        answer(["1. Bravo Medical Center | NOT FOUND | beds | -", "2. Charlie Regional Hospital | 650 | licensed | c.example", "3. Delta Hospital | 60 | beds | d.example"],
               [("Charlie Regional Hospital | 650 | licensed", "https://vertexaisearch.cloud.google.com/grounding-api-redirect/C2"),
                ("Delta Hospital | 60 | beds", "https://vertexaisearch.cloud.google.com/grounding-api-redirect/D")]),
        # attempt 3 (B): a figure from memory, no search at all
        answer(["1. Bravo Medical Center | 300 | beds | -"], [], queries=()),
    ]

    def stubbed_loop(ha, llm):
        os.environ["GEMINI_API_KEY"] = "stub-not-a-key"  # never sent: _post_json is stubbed
        bodies = []

        def post(url, body, key, timeout):
            bodies.append(body)
            assert "google_search" in body["tools"][0], body["tools"]
            return REPLIES[len(bodies) - 1]

        llm._post_json = post
        ha._read = fake_read
        rows = [ha._row(h) for h in HOSP]
        job = ha._Job("FL", ha._set_key("FL", rows), rows)
        asyncio.run(ha._run(job))
        by = {r["name"]: r for r in job.rows}
        a, b, c, d = (by[h["name"]] for h in HOSP)
        assert len(bodies) == 3, f"{len(bodies)} Gemini calls"
        assert "Your earlier answer was not kept" in bodies[1]["contents"][0]["parts"][0]["text"], "the revision was not told why"
        assert "650 beds" in bodies[1]["contents"][0]["parts"][0]["text"], "the feedback did not say what the page states"
        assert a["beds_basis"] == "reported" and a["beds"] == 123 and a["page_confirmed"] is True and "123" in a["quote"] and a["source"]["url"] == "https://a.example/about", a
        assert b["beds_basis"] == "osm" and b["beds"] == 222 and b["source"]["url"].startswith("https://www.openstreetmap.org/"), b  # 456 ungrounded, then NOT FOUND, then from memory
        assert "from memory" in b["note"], b["note"]
        assert c["beds_basis"] == "reported" and c["beds"] == 650 and c["page_confirmed"] is True, c  # 789 rejected by the page, 650 kept
        assert d["beds_basis"] == "reported" and d["beds"] == 60 and d["page_confirmed"] is None and d["note"].startswith("Grounded in"), d
        assert a["checked"] is True and d["checked"] is False, "checked must match page_confirmed"
        kinds = [t["kind"] for t in job.trace]
        assert kinds[:3] == ["info", "propose", "verify"] and "feedback" in kinds and "revise" in kinds and kinds[-1] == "result", kinds
        assert job.suggestions == [CHIP] and job.by == "gemini" and not job.timed, (job.suggestions, job.by, job.timed)  # one chip, not three copies
        # a clean run is remembered: the set answers at once (with its Search Suggestions), the facts are reused by another incident
        hit = ha._sets.get(job.key)
        assert hit is not None, "a clean run was not remembered"
        view = ha._set_view("FL", hit, [ha._row(h) for h in HOSP])
        assert view["cached"] and [h["beds"] for h in view["hospitals"]] == [123, 222, 650, 60], view["hospitals"]
        assert view["suggestions"] == [CHIP], "a remembered grounded result served without its Search Suggestions"
        assert [h["page_confirmed"] for h in view["hospitals"]] == [True, None, True, None] and view["hospitals"][0]["quote"], view["hospitals"]
        assert {k.split(":")[1] for k in ha._facts} == {"9001", "9003", "9004"}, sorted(ha._facts)
        rows2 = [ha._row(h) for h in HOSP[:1]]
        job2 = ha._Job("FL", ha._set_key("FL", rows2), rows2)
        asyncio.run(ha._run(job2))
        assert len(bodies) == 3 and job2.rows[0]["beds"] == 123 and job2.rows[0]["beds_basis"] == "reported", "a found hospital was searched again"
        assert job2.suggestions == [CHIP], "a reused grounded figure shown without its Search Suggestions"

    def no_key(ha, llm):
        os.environ["GEMINI_API_KEY"] = ""

        def post(*a, **k):
            raise AssertionError("Gemini called without a key")

        llm._post_json = post
        rows = [ha._row(h) for h in HOSP]
        job = ha._Job("FL", ha._set_key("FL", rows), rows)
        asyncio.run(ha._run(job))
        assert job.by == "fallback" and job.why == "no Gemini key", (job.by, job.why)
        bases = {r["name"]: r["beds_basis"] for r in job.rows}
        assert bases == {"Alpha General Hospital": "not_found", "Bravo Medical Center": "osm", "Charlie Regional Hospital": "not_found", "Delta Hospital": "not_found"}, bases
        assert job.trace[-1]["kind"] == "result" and job.trace[-1]["tone"] == "muted", job.trace[-1]
        assert job.key not in ha._sets, "a fallback run was remembered"

    def parse_and_check(ha, llm):
        items = [{"name": "Xavier Memorial Hospital"}, {"name": "Yankee Clinic"}, {"name": "Zulu Regional Medical Center"}]
        p = ha._parse("1. Xavier Memorial Hospital | 1,019 | licensed | x.org\n2. Yankee Clinic | NOT FOUND | beds | -\n3. Zulu Regional | **42** beds | staffed | z.org | extra", items)
        assert p[0]["n"] == 1019 and p[0]["kind"] == "licensed" and p[1]["n"] is None and p[2]["n"] == 42 and p[2]["kind"] == "staffed", p
        # a line is placed by the hospital's name, not its number (the model may reorder), and a stranger's line is dropped
        p = ha._parse("1. Zulu Regional Medical Center | 300 | beds | z.org\n2. Xavier Memorial Hospital | 120 | beds | x.org\n3. Somewhere Else | 9 | beds | -", items)
        assert p[2]["n"] == 300 and p[0]["n"] == 120 and 1 not in p, p
        text = "Zulu Regional Hospital is a 42-bed facility. " + PAD
        assert ha._number_near_bed(text, 42, ["zulu", "regional"]) is not None and ha._number_near_bed(text, 4, ["zulu", "regional"]) is None
        assert ha._number_near_bed("The 1,019 licensed beds of Big Sky Hospital. " + PAD, 1019, ["big", "sky"]), "a figure with a thousands comma"
        listing = "Gulf Coast Medical Center 349 beds; Cape Coral Hospital 291 beds. " + PAD
        assert ha._number_near_bed(listing, 291, ["gulf", "coast"], [ha._phrase("Cape Coral Hospital")]) is None, "a sibling's figure kept"
        assert ha._number_near_bed(listing, 291, ["gulf", "coast"]) is None, "another bed count between the name and the number"
        assert ha._number_near_bed(listing, 291, ["cape", "coral"], [ha._phrase("Gulf Coast Medical Center")]), "the right sibling's figure"
        assert ha._number_near_bed("The Rehabilitation Hospital of Cape Coral: 40-bed facility. " + PAD, 40, ["cape", "coral"]) is None, "a place inside another name"
        assert ha._number_near_bed("Lee Memorial Hospital Skilled Nursing Unit: 18 beds. " + PAD, 18, ["lee", "memorial"]) is None, "one unit's beds"
        assert ha._number_near_bed("Key Facts Number of Operating Rooms : 22 Number of Skilled Nursing Unit Beds in Building : 75 " + PAD, 75, ["key"]) is None
        assert ha._phrase("AdventHealth") is None and ha._phrase("The Centers") is None and ha._phrase("Doctors Hospital").search("the doctors hospital of x")
        assert not ha._public_url("http://127.0.0.1/x") and not ha._public_url("http://localhost:8000/") and not ha._public_url("file:///etc/passwd")
        assert not ha._public_url("http://10.0.0.1/") and not ha._public_url("https://example.com:8443/")

    HONEST = [
        {"id": 9101, "name": "Echo Valley Hospital", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None},
        {"id": 9102, "name": "Golf Medical Center", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None},
        {"id": 9103, "name": "Hotel Hospital", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None},
        {"id": 9104, "name": "India Lakes Hospital", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None},
        {"id": 9105, "name": "Juliet Point Hospital", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None},
        {"id": 9106, "name": "Kilo Springs Hospital", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None},
        {"id": 9107, "name": "Lima Ridge Hospital", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None},
        {"id": 9110, "name": "Oscar Creek Hospital", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None},
    ]
    HONEST_LINES = ["1. Echo Valley Hospital | 415 | licensed | f.example", "2. Golf Medical Center | 291 | beds | l.example",
                    "3. Hotel Hospital | 291 | beds | l.example", "4. India Lakes Hospital | 77 | beds | u.example",
                    "5. Juliet Point Hospital | NOT FOUND | beds | -", "6. Kilo Springs Hospital | 500 | beds | k.example",
                    "7. Lima Ridge Hospital | 18 | beds | l2.example", "8. Oscar Creek Hospital | 333 | beds | o.example"]
    HONEST_REPLY = answer(HONEST_LINES, [
        ("Echo Valley Hospital | 415 | licensed", R + "F"),
        ("Golf Medical Center | 291 | beds", R + "L"),
        ("Hotel Hospital | 291 | beds", R + "L"),
        ("India Lakes Hospital | 77 | beds | u.example\n5. Juliet Point Hospital", R + "U"),  # spans two hospitals' lines
        ("Kilo Springs Hospital | 500 | beds", R + "K"),
        ("Lima Ridge Hospital | 18 | beds", R + "L2"),
        ("Oscar Creek Hospital | 333 | beds", R + "O"),
    ])
    MEMORY_REPLY = answer([f"{k}. {h['name']} | NOT FOUND | beds | -" for k, h in enumerate(HONEST, 1)], [], queries=())

    def stubbed_honesty(ha, llm):
        os.environ["GEMINI_API_KEY"] = "stub-not-a-key"  # never sent: _post_json is stubbed
        ha.MODELS, ha.ATTEMPTS = ["first-model", "second-model"], 1
        calls = []
        replies = {}

        def post(url, body, key, timeout):
            model = url.rsplit("/", 1)[-1].split(":")[0]
            calls.append(model)
            return replies[model]

        llm._post_json = post
        ha._read = fake_read
        # the first model answers from memory (no search): the second one is asked, and its figures are checked
        replies.update({"first-model": MEMORY_REPLY, "second-model": HONEST_REPLY})
        rows = [ha._row(h) for h in HONEST]
        job = ha._Job("FL", ha._set_key("FL", rows), rows)
        asyncio.run(ha._run(job))
        assert calls == ["first-model", "second-model"], calls
        by = {r["name"]: r for r in job.rows}
        e, g, h, i, j, k, lima, oscar = (by[x["name"]] for x in HONEST)
        assert h["beds_basis"] == "reported" and h["beds"] == 291 and h["page_confirmed"] is True and "Hotel Hospital: 291 beds" in h["quote"], h
        for r in (e, g, i, j, k, lima, oscar):
            assert r["beds_basis"] == "not_found" and r["beds"] is None and r["source"] is None, r
        assert "does not name this hospital" in e["note"], e["note"]  # the page states 415 for another hospital
        assert "gives 291 beds for something else" in g["note"], g["note"]  # a sibling's figure on a listing page
        assert "own line" in i["note"], i["note"]  # only a support spanning two hospitals' lines backs 77
        assert "you answered NOT FOUND" in j["note"], j["note"]
        assert "but not in Florida" in k["note"], k["note"]  # a same-named hospital in Ohio
        assert "gives 18 beds for something else" in lima["note"], lima["note"]  # one unit's beds
        # a page the checker CAN read, tied to the hospital's own line, that shows neither its name nor 333: not kept
        assert "could be read, but shows neither" in oscar["note"], oscar["note"]
        verify = next(t for t in job.trace if t["kind"] == "verify")
        assert "not kept" in verify["detail"]["en"] and "descartado" in verify["detail"]["es"], verify
        assert "no nombra este hospital" in verify["detail"]["es"] and "pero no en Florida" in verify["detail"]["es"], verify["detail"]["es"]
        assert "no muestra ni el nombre" in verify["detail"]["es"], verify["detail"]["es"]
        # the misses are remembered: another incident that reaches Echo Valley doesn't search it again
        assert {int(x.split(":")[1]) for x in ha._misses} == {9101, 9102, 9104, 9105, 9106, 9107, 9110}, sorted(ha._misses)
        rows2 = [ha._row(x) for x in HONEST[:1]]
        job2 = ha._Job("FL", ha._set_key("FL", rows2), rows2)
        asyncio.run(ha._run(job2))
        assert len(calls) == 2 and job2.rows[0]["beds_basis"] == "not_found" and "earlier run" in job2.rows[0]["note"], (calls, job2.rows[0])
        # the first model searched: the second one is never asked
        replies["first-model"] = answer(["1. Mike Harbor Hospital | 250 | beds | m.example"], [("Mike Harbor Hospital | 250 | beds", R + "M")])
        rows3 = [ha._row({"id": 9108, "name": "Mike Harbor Hospital", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None})]
        job3 = ha._Job("FL", ha._set_key("FL", rows3), rows3)
        asyncio.run(ha._run(job3))
        assert calls[2:] == ["first-model"], calls
        assert job3.rows[0]["beds"] == 250 and job3.rows[0]["page_confirmed"] is True, job3.rows[0]
        assert not job.pages and not job3.pages, "the pages' text was kept after the job ended"
        # the first model is slow: after HEDGE_S the second one is asked too, and the first is cancelled once all is kept
        import time as _time
        ha.HEDGE_S = 0.2
        nov = answer(["1. November Bay Hospital | 144 | beds | n.example"], [("November Bay Hospital | 144 | beds", R + "N")])

        def slow_post(url, body, key, timeout):
            model = url.rsplit("/", 1)[-1].split(":")[0]
            calls.append(model)
            if model == "first-model":
                _time.sleep(1.5)
            return nov

        llm._post_json = slow_post
        rows4 = [ha._row({"id": 9109, "name": "November Bay Hospital", "lat": 26.5, "lon": -81.7, "area": "Stubton", "beds": None})]
        job4 = ha._Job("FL", ha._set_key("FL", rows4), rows4)
        asyncio.run(ha._run(job4))
        assert calls[3:] == ["first-model", "second-model"], calls
        assert job4.rows[0]["beds"] == 144 and job4.elapsed < 1.4, (job4.rows[0]["beds"], job4.elapsed)

    def local_server(replies):
        """A throwaway HTTP server on 127.0.0.1 that answers each connection with the next raw reply; `got` records
        what each connection sent (b"" when the client closed without sending a request)."""
        import socket
        import threading

        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        srv.settimeout(5)
        got = []

        def loop():
            try:
                for raw in replies:
                    c, _a = srv.accept()
                    c.settimeout(3)
                    try:
                        data = b""
                        while b"\r\n\r\n" not in data:
                            chunk = c.recv(65536)
                            if not chunk:
                                break
                            data += chunk
                        got.append(data)
                        if data:
                            c.sendall(raw)
                    except OSError:
                        got.append(b"")
                    finally:
                        c.close()
            except OSError:
                pass
            finally:
                srv.close()

        threading.Thread(target=loop, daemon=True).start()
        return srv.getsockname()[1], got

    def bad_pages(ha, llm):
        """One bad page is just 'not readable': a truncated body (IncompleteRead), a broken status line, a charset
        Python can't decode with 'replace' (idna) or doesn't know; a page read that raises; and the DNS-rebinding guard
        (a name that passed the public-address check but connects to a private address) refuses before any request."""
        html = ("<html><body><p>" + "Alpha General Hospital in Stubton, FL has 123 beds. " * 40 + "café</p></body></html>").encode("latin-1")
        head = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset={cs}\r\nContent-Length: {n}\r\nConnection: close\r\n\r\n"
        replies = [
            # a chunk cut short: IncompleteRead (a Content-Length body cut short just reads short)
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\nffff\r\n" + html[:2000],
            b"NOT HTTP AT ALL\r\n\r\n",  # BadStatusLine
            head.format(cs="idna", n=len(html)).encode() + html,
            head.format(cs="undefined", n=len(html)).encode() + html,
        ]
        port, got = local_server(replies)
        real_peer_ok = ha._peer_ok
        ha._public_url = lambda url: True  # the name checks pass (as they would for a rebinding name) ...
        ha._peer_ok = lambda ip: True  # ... and, for these four, the connected address too
        base = f"http://127.0.0.1:{port}/"
        text, why = ha._page_text(base + "cut")
        assert text is None and why == "could not be read", (text and text[:60], why)
        text, why = ha._page_text(base + "garbage")
        assert text is None and why == "could not be read", (text, why)
        for cs in ("idna", "undefined"):
            text, why = ha._page_text(base + cs)
            assert text and "123 beds" in text and why == "", (cs, why, (text or "")[:80])
        # without the PDF reader (Render), a PDF link is never fetched (port 9 would refuse if it were)
        ha.PDF_OK = False
        assert ha._page_text("http://127.0.0.1:9/beds-report.pdf") == (None, ha.NO_PDF)
        # _read never raises, whatever fails inside it
        real_resolve = ha._resolve

        def boom(uri):
            raise RuntimeError("boom")

        ha._resolve = boom
        out = ha._read("https://x.example/p", "x.example", {}, __import__("threading").Lock())
        assert out["text"] is None and out["why"] == "could not be read", out
        ha._resolve = real_resolve
        # DNS rebinding: the real peer check, a name that "resolved" public but connects to 127.0.0.1
        ha._peer_ok = real_peer_ok
        port2, got2 = local_server([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"])
        text, why = ha._page_text(f"http://127.0.0.1:{port2}/")
        assert text is None and why.startswith("could not be reached"), (text, why)
        __import__("time").sleep(0.3)
        assert got2 in ([], [b""]), f"a request reached a private address: {got2!r}"

    def batch_fails(ha, llm):
        """A batch that fails (here: its check raises) ends only its own hospitals; the others finish, the job ends
        done, and the failure is not remembered as a miss."""
        os.environ["GEMINI_API_KEY"] = "stub-not-a-key"
        ha.BATCH = 1
        ok = answer(["1. Alpha General Hospital | 123 | licensed | a.example"], [("Alpha General Hospital | 123 | licensed", R + "A")])
        nf = answer(["1. Charlie Regional Hospital | NOT FOUND | beds | -"], [], queries=("charlie beds",))

        def post(url, body, key, timeout):
            return ok if "Alpha General" in body["contents"][0]["parts"][0]["text"] else nf

        llm._post_json = post
        ha._read = fake_read
        real_judge = ha._judge

        async def judge(job, items, *a, **k):
            if any(r["id"] == 9003 for r in items):
                raise RuntimeError("a checker bug")
            return await real_judge(job, items, *a, **k)

        ha._judge = judge
        rows = [ha._row(h) for h in (HOSP[0], HOSP[2])]
        job = ha._Job("FL", ha._set_key("FL", rows), rows)
        asyncio.run(ha._run(job))
        by = {r["id"]: r for r in job.rows}
        assert job.status == "done" and job.by == "gemini", (job.status, job.by, job.why)
        assert by[9001]["beds_basis"] == "reported" and by[9001]["beds"] == 123, by[9001]
        assert by[9003]["beds_basis"] == "not_found" and "stopped on an error" in by[9003]["note"], by[9003]
        assert not any(k.split(":")[1] == "9003" for k in ha._misses), "a failure was remembered as a miss"
        assert job.key not in ha._sets, "a run with a failed batch was remembered as a clean set"
        assert not job.batches and not job.pages, "the job's tasks or pages outlived it"

    def budget_guard(ha, llm):
        """The budget guard: under AI_RESERVE calls left in the app's day, OpenStreetMap only (no Gemini call); past
        NEW_JOBS_MAX new searches per window, the same at once from the route's logic; a visitor who opens another case
        stops its previous search (another visitor behind the same address keeps theirs)."""
        os.environ["GEMINI_API_KEY"] = "stub-not-a-key"

        def no_post(*a, **k):
            raise AssertionError("Gemini called past the budget guard")

        llm._post_json = no_post
        ha.AI_RESERVE = 10**9
        rows = [ha._row(h) for h in HOSP]
        job = ha._Job("FL", ha._set_key("FL", rows), rows)
        asyncio.run(ha._run(job))
        assert job.by == "fallback" and job.why == "today's AI budget is low", (job.by, job.why)
        assert [r["beds_basis"] for r in job.rows] == ["not_found", "osm", "not_found", "not_found"], [r["beds_basis"] for r in job.rows]
        assert all("AI budget" in r["note"] for r in job.rows), [r["note"] for r in job.rows]
        ha.AI_RESERVE = 0
        ha.NEW_JOBS_MAX = 0
        v = asyncio.run(ha._begin("FL", [ha._row(h) for h in HOSP], "smoke|tab0"))
        assert v["job"] is None and v["status"] == "done" and v["by"] == "fallback" and "limit" in v["why"], (v["job"], v["status"], v["by"], v["why"])
        assert v["counts"]["osm"] == 1 and "new incidents" in v["hospitals"][0]["note"], v["hospitals"][0]
        ha.NEW_JOBS_MAX = 30
        import time as _time

        def slow(url, body, key, timeout):
            _time.sleep(0.8)
            return answer(["1. Stub | NOT FOUND | beds | -"], [], queries=("stub",))

        llm._post_json = slow

        async def scenario():
            v1 = await ha._begin("FL", [ha._row(HOSP[0])], "smoke|tab1")
            assert v1["status"] == "running" and v1["job"], v1["status"]
            j1 = ha._jobs[v1["job"]]
            await asyncio.sleep(0.05)
            v3 = await ha._begin("FL", [ha._row(HOSP[3])], "smoke|tab2")  # another visitor behind the same address
            j3 = ha._jobs[v3["job"]]
            v2 = await ha._begin("FL", [ha._row(HOSP[2])], "smoke|tab1")  # the first visitor opens another case
            j2 = ha._jobs[v2["job"]]
            await asyncio.sleep(0.2)
            assert j1.status == "done" and j1.stopping and "newer case" in j1.rows[0]["note"], (j1.status, j1.rows[0]["note"])
            assert j2.status == "running" and j3.status == "running" and not j3.stopping, (j2.status, j3.status)
            # the same visitor asking again for its running case joins it (nothing stopped)
            again = await ha._begin("FL", [ha._row(HOSP[2])], "smoke|tab1")
            assert again["job"] == j2.id and j2.status == "running", again["job"]
            for j in (j2, j3):
                j.task.cancel()
            await asyncio.gather(j2.task, j3.task, return_exceptions=True)
            assert j2.status == j3.status == "done", (j2.status, j3.status)

        asyncio.run(scenario())

    ctx.check("hospital agent (stubbed): grounded check, page check, feedback and revision, then the caches", in_process(stubbed_loop))
    ctx.check("hospital agent (stubbed): the honesty rules, the second model only when needed, the miss cache", in_process(stubbed_honesty))
    ctx.check("hospital agent (stubbed): without a key, OpenStreetMap's figures only, labeled, not remembered", in_process(no_key))
    ctx.check("hospital agent: the answer parser, the page words check and the public-address guard", in_process(parse_and_check))
    ctx.check("hospital agent: a bad page is just not readable (truncated, garbage, odd charsets, a read that raises); DNS rebinding refused", in_process(bad_pages))
    ctx.check("hospital agent (stubbed): one failing batch ends only its own hospitals", in_process(batch_fails))
    ctx.check("hospital agent (stubbed): the budget guard (AI reserve, new-search window) and one running search per visitor", in_process(budget_guard))
