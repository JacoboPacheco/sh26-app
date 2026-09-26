"""Reader C: Gemini reads the pages. An OFFLINE, ADVISORY build-time stage of the Build plans pipeline (never at request time).

    backend/venv/Scripts/python backend/demo/gridlock/gemini_reader.py             # read, compare, propose rescues (report only)
    backend/venv/Scripts/python backend/demo/gridlock/gemini_reader.py --apply     # ... and fold passing rescues into projects.json
    backend/venv/Scripts/python backend/demo/gridlock/gemini_reader.py --refresh   # ask Gemini again instead of the cached answers
    backend/venv/Scripts/python backend/demo/gridlock/gemini_reader.py --apply --title-only   # only rescues placed from their title

  1 split    the two public PDFs in sources/ into single pages (pypdfium2): the 44 DESC project pages, the 14 pages of the
             Georgia ITS Table 2, and the 208 Georgia detail pages (12 single pages to a request: each names its own TEAMS #)
  2 read     every page goes to Gemini as inline application/pdf with a JSON Schema of the fields the parsers extract
             (llm.complete_json: structured output, thinking at its minimum, a 30-45 s timeout, surface "reader"); the answers
             are cached in raw/gemini_reader/ (gitignored) by the page's bytes + the prompt, so a rerun costs nothing
  3 compare  Gemini's fields against the parsers' (DESC: the page parser; Georgia Table 2: the text-line parser A and the
             word-position parser B; Georgia detail pages: the detail parser), every value normalized by normalize.py's own
             functions first; per-field agreement and every disagreement with its PDF page
  4 rescue   for every record the checks set aside, Gemini is asked, from that record's page and with the checks' reasons, for
             the place names the pipeline couldn't use. A proposed name must be printed on the page. It then goes through the
             pipeline's OWN normalize (endpoints_of), locate (build.locate_all's steps for one record: the OSM name passes, the
             cached Nominatim answers at the highest voltage any filing gives that name, then the line its description names;
             no network here) and checks.run; a proposal "passes" only when every blocking check passes. A point placed at a
             station only the page's DESCRIPTION names (not the title) is capped at low confidence and noted "in this area"
  5 write    data/gemini_reader.json (committed), served at GET /api/gridlock/reader; rewritten only when its content changed,
             so a cached rerun keeps the file (and its timestamp) byte for byte

Gemini proposes; the pipeline's checks decide. The pipeline itself is rebuilt here offline from the same caches build.py uses,
and nothing changes data/projects.json unless --apply is given. --apply sets projects.json to the build plus the rescues
chosen now (an earlier application is replaced, not stacked), recomputes the check summary, coverage and Sperry comparison
from the resulting record list, and writes only when (a) projects.json is what the filings rebuild to offline and (b) the
rescues leave Sperry's worked example as it was: every one of their projects matched to the same record and every endpoint
that was within 1 km still within 1 km (the six overlap distances come from Sperry's own coordinates, so no rescue can move
them). A later build.py run rebuilds projects.json from the filings alone, so an applied rescue lasts until the next build.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(BACKEND))

# before llm is imported: this stage never reads or writes the app's answer cache (backend/.ai_cache.json belongs to the
# running server, and its 1,024 slots hold the demo's pre-warmed answers); it keeps its own cache in raw/gemini_reader/
os.environ["AI_CACHE_FILE"] = ""
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

import pypdfium2 as pdfium  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import build  # noqa: E402
import checks  # noqa: E402
import extract_desc  # noqa: E402
import extract_ga  # noqa: E402
import locate  # noqa: E402
import llm  # noqa: E402
import normalize as N  # noqa: E402
import osm  # noqa: E402
from pdf_text import page_texts, page_words  # noqa: E402

OUT = HERE / "data" / "gemini_reader.json"
PROJECTS = HERE / "data" / "projects.json"
CACHE_DIR = HERE / "raw" / "gemini_reader"
COMMAND = "backend/venv/Scripts/python backend/demo/gridlock/gemini_reader.py"
MODEL = os.getenv("GEMINI_READER_MODEL", "gemini-3.5-flash")
THINKING = os.getenv("GEMINI_READER_THINKING", "minimal").strip() or None
CONCURRENCY = 6
DETAIL_BATCH = 12
PROMPT_VERSION = "reader-v1"  # part of every cache key: a changed prompt asks again
MAX_TRIALS = 4

SYSTEM = ("You read pages of public utility filings and copy fields exactly as printed. Never guess or fill in: a field that is "
          "not printed on the page is null, and a value printed as REDACTED is null. Answer only with JSON that follows the schema.")

KV_RULE = ("kv: every transmission voltage of 34 kV or more named in the TITLE, as integers, highest first ('230/115KV' is "
           "[230, 115]; a letter O typed for a zero, as in '23O KV', is 230). Only when the title names no voltage, use the ones in "
           "the description. An empty list when neither names one.")
KIND_RULE = ("kind: what the work is. One of: reconductor (new conductor on an existing line); new_line (a new line is built); tap "
             "(a tap, fold-in or loop-in of a line into a station); line_rebuild (an existing line is rebuilt or upgraded); "
             "substation (work at a station: a new substation, banks, transformers, breakers, buses, capacitors, reactors, relays, "
             "switches, smart valves); other. Decide from the TITLE first, and when several apply the first in this order wins: "
             "reconductor, new_line, tap, line_rebuild, substation. Only when the title names no work, use the description's "
             "first work verb.")
PLACES_RULE = ("places: the substations, plants or places the TITLE names as the ends or the site of the work, in title order, at "
               "most two (for 'A - B - C' give A and C; for work at one station give that one). Leave out utility prefixes "
               "('GTC:', 'SAV:', 'MEAG:', 'CC -', 'GRID -'), voltages and work words, qualifiers in parentheses such as '(APC)', "
               "numbers such as '#3', and names of customers, companies, programs or code names ('PROJECT ...', 'HYUNDAI', "
               "'QTS'). Copy each name as printed. An empty list when the title names no place.")

_KV = {"type": "array", "items": {"type": "integer"}}
_KIND = {"type": "string", "enum": ["reconductor", "new_line", "tap", "line_rebuild", "substation", "other"]}
_PLACES = {"type": "array", "items": {"type": "string"}, "maxItems": 2}
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}

DESC_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": _SN, "title": _S, "in_service_date": _SN, "kv": _KV, "kind": _KIND, "places": _PLACES,
        "costs": {"type": "array", "items": {"type": "object", "properties": {"column": _S, "amount": _S}, "required": ["column", "amount"]}},
    },
    "required": ["project_id", "title", "in_service_date", "kv", "kind", "places", "costs"],
}
GA_TABLE_SCHEMA = {
    "type": "object",
    "properties": {"rows": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "zone": _S, "plan_year": _S, "teams_no": _S, "title": _S, "need_date": _SN, "sponsor": _S,
            "cost_usd": {"type": ["integer", "null"]}, "kv": _KV, "places": _PLACES,
        },
        "required": ["zone", "plan_year", "teams_no", "title", "need_date", "sponsor", "cost_usd", "kv", "places"],
    }}},
    "required": ["rows"],
}
GA_DETAIL_SCHEMA = {
    "type": "object",
    "properties": {"pages": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "teams_no": _S, "title": _S, "need_date": _SN, "start_date": _SN, "miles": {"type": ["number", "null"]},
            "kv": _KV, "kind": _KIND,
        },
        "required": ["teams_no", "title", "need_date", "start_date", "miles", "kv", "kind"],
    }}},
    "required": ["pages"],
}
RESCUE_SCHEMA = {
    "type": "object",
    "properties": {
        "endpoints": {"type": "array", "maxItems": 2, "items": {
            "type": "object",
            "properties": {"name": _S, "quote": _S, "alternates": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
            "required": ["name", "quote", "alternates"],
        }},
        "note": _S,
    },
    "required": ["endpoints", "note"],
}

READERS = {
    "parser_a": {"name": "Parser A", "what": "text lines (pdfplumber), the pipeline's own reading: what it keeps"},
    "parser_b": {"name": "Parser B", "what": "word positions inside the table's columns (Georgia Table 2 only)"},
    "gemini": {"name": "Gemini", "what": "the PDF page itself, read by Gemini with a JSON Schema (advisory)"},
}
FIELDS = {
    "id": "Project id / TEAMS #", "name": "Title", "in_service": "In-service / need date", "kv": "Voltage (kV)",
    "kind": "Kind of work", "places": "Places named", "cost_total": "Total cost (DESC)", "cost_by_year": "Cost by year (DESC)",
    "zone": "Planning zone", "plan_year": "Plan year", "sponsor": "Sponsor", "cost_redacted": "Redacted cost left empty",
    "start_date": "Start date", "miles": "Miles",
}


# =========================================================================== pages -> PDF bytes


class Pages:
    """Single pages (or a few, for the Georgia detail batches) cut out of a source PDF as their own small PDF."""

    def __init__(self, path: Path):
        self.doc = pdfium.PdfDocument(str(path))

    def pdf(self, pages: list[int]) -> bytes:
        new = pdfium.PdfDocument.new()
        new.import_pages(self.doc, [p - 1 for p in pages])
        buf = io.BytesIO()
        new.save(buf)
        return buf.getvalue()


# =========================================================================== Gemini, cached


class Reader:
    def __init__(self, refresh: bool):
        self.refresh = refresh
        self.sem = asyncio.Semaphore(CONCURRENCY)
        self.calls: list[dict] = []

    async def ask(self, stage: str, doc: str, pages: list[int], pdf: bytes, prompt: str, schema: dict, timeout: float) -> dict:
        # keyed by the source file's SHA-256 + the pages (a re-cut page's bytes differ run to run: pdfium stamps a new file id)
        raw = json.dumps([PROMPT_VERSION, MODEL, THINKING, SYSTEM, prompt, schema, doc, pages], sort_keys=True)
        key = hashlib.sha256(raw.encode()).hexdigest()
        path = CACHE_DIR / f"{key}.json"
        rec = {"stage": stage, "pages": pages, "bytes": len(pdf)}
        if path.exists() and not self.refresh:
            hit = json.loads(path.read_text(encoding="utf-8"))
            rec.update(cached=True, model=hit.get("model"), ms=hit.get("ms"), ok=True, at=hit.get("at"))
            self.calls.append(rec)
            return hit["data"]
        async with self.sem:
            data, err, used, t0 = None, None, None, time.perf_counter()
            for attempt in range(2):  # one retry (a longer timeout) for a slow or truncated answer; never a loop
                try:
                    data, _ = await llm.complete_json(prompt, system=SYSTEM, image=(pdf, "application/pdf"), schema=schema,
                                                      timeout=timeout * (1 + attempt), surface="reader", model=MODEL,
                                                      thinking=THINKING, cache=False)
                    used = llm._stats.get("model_used")  # set just before complete_json returned (no await in between)
                    err = None
                    break
                except HTTPException as e:
                    err = str(e.detail)[:300]
            ms = round((time.perf_counter() - t0) * 1000)
        rec.update(cached=False, model=used, ms=ms, ok=err is None, error=err, at=_now())
        self.calls.append(rec)
        if err is not None:
            print(f"  ! {stage} p{pages[0]}{'-' + str(pages[-1]) if len(pages) > 1 else ''}: {err}")
            return {"_error": err}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": used, "ms": ms, "at": _now(), "stage": stage, "pages": pages, "data": data}), encoding="utf-8")
        return data


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# =========================================================================== the pipeline, rebuilt offline


class Pipeline:
    """build.py's stages 1-4, in process, from the same caches (PDF text, OSM extracts, Nominatim answers; no network)."""

    def __init__(self):
        srcs = build.ensure_sources(None)
        self.sources = {s["id"]: s for s in srcs}
        self.desc_texts = page_texts(self.sources["desc"]["path"])
        self.desc_recs = extract_desc.extract(self.desc_texts)
        self.ga_texts = page_texts(self.sources["ga_irp"]["path"])
        self.table_pages = extract_ga.find_table_pages(self.ga_texts)
        words = page_words(self.sources["ga_irp"]["path"], self.table_pages)
        self.ga = extract_ga.extract(self.ga_texts, words)
        self.rows_b = extract_ga.parse_table_words(words)  # parser B's own rows (extract() keeps only its name)
        self.projects = build.normalize_desc(self.desc_recs, self.sources["desc"]) + build.normalize_ga(self.ga, self.sources["ga_irp"])
        self.index = locate.Index()
        self.routes = build.LineRoutes()
        self.geocoder = osm.Nominatim(allow_network=False)
        info = build.locate_all(self.projects, self.index, self.geocoder)
        self.zc = info["zone_centers"]
        for p in self.projects:
            build.geometry_of(p, self.routes)
        self.kept, self.quarantine, _ = checks.run(self.projects, self.zc)
        self.by_id = {p["id"]: p for p in self.projects}
        # build.locate_all asks Nominatim about a (state, name) once, at the highest voltage ANY filing gives that name
        # (locate_project returns one endpoint per parsed name, with its key, so the parsed names are the build's keys)
        self.name_kv: dict[tuple, list[tuple[int, int]]] = {}
        for p in self.projects:
            if p["utility"] in locate.HOME:
                for e in p["endpoints_parsed"]:
                    self.name_kv.setdefault((locate.HOME[p["utility"]], e["key"]), []).append((id(p), max(p["kv"] or [0])))

    def need_kv(self, home: str, key: str, replaced: dict, trial: dict) -> int | None:
        """The build's need_kv for this name if `trial` stood in the record list instead of `replaced` (the trial counts only
        for the names it gives itself, as in the build: a name only a description gives has no filed voltage of its own)."""
        kvs = [kv for pid, kv in self.name_kv.get((home, key), []) if pid != id(replaced)]
        if key in {e["key"] for e in trial["endpoints_parsed"]}:
            kvs.append(max(trial["kv"] or [0]))
        return max(kvs, default=0) or None

    def page_text(self, source: str, page: int) -> str:
        texts = self.desc_texts if source == "desc" else self.ga_texts
        return texts[page - 1] if 0 < page <= len(texts) else ""


# =========================================================================== normalizing a reading (normalize.py's functions)


def n_date(v) -> str | None:
    return N.parse_date(v)[0] if v not in (None, "") else None


def n_title(v) -> str | None:
    return build._canon_title(v).rstrip(" .") if v else None


def n_kv(v) -> list[int]:
    out = set()
    for x in v or []:
        try:
            k = int(round(float(x)))
        except (TypeError, ValueError):
            continue
        if k >= 34:
            out.add(k)
    return sorted(out, reverse=True)


def n_place(name: str) -> str:
    s = name or ""
    while True:
        m = N.PREFIX.match(s)
        if not m:
            break
        s = s[m.end():]
    return locate.core(N.endpoint_key(s))


def n_places(names) -> list[str]:
    return sorted({k for k in (n_place(x) for x in names or []) if k})


def places_of_title(title: str | None) -> list[str]:
    return sorted({locate.core(e["key"]) for e in N.endpoints_of(title or "")["endpoints"] if e["key"]})


def n_money(raw) -> int | None:
    digits = re.sub(r"[^0-9]", "", str(raw or ""))
    return int(digits) if digits else None


def n_desc_id(v) -> str | None:
    s = re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()
    return s or None


def n_digits(v) -> str | None:
    s = re.sub(r"\D", "", str(v or ""))
    return s or None


def n_text(v) -> str | None:
    s = re.sub(r"\s+", " ", str(v or "")).strip().upper()
    return s or None


def n_miles(v) -> float | None:
    try:
        return round(float(v), 2) if v is not None else None
    except (TypeError, ValueError):
        return None


# =========================================================================== comparing


class Compare:
    def __init__(self):
        self.rows: list[dict] = []  # one per (record, field)
        self.missed: list[dict] = []  # records the parsers read and Gemini didn't return
        self.extra: list[dict] = []  # rows Gemini returned that no parser read
        self.unread: list[dict] = []  # records whose page call failed

    def add(self, source: str, part: str, record: str, page: int, field: str, values: dict, raw: dict, p: dict | None = None):
        note = _pipeline_notes(p, field)
        present = {k: v for k, v in values.items()}
        vals = list(present.values())
        all_agree = all(v == vals[0] for v in vals)
        g, a = present.get("gemini"), present.get("parser_a")
        b = present.get("parser_b", a) if "parser_b" in present else None
        if all_agree:
            status = "agree"
        elif "parser_b" in present:
            if a == b:
                status = "gemini_differs"
            elif g == a:
                status = "parser_b_differs"
            elif g == b:
                status = "parser_a_differs"
            else:
                status = "all_differ"
        else:
            status = "gemini_differs"
        self.rows.append({"source": source, "part": part, "record": record, "page": page, "field": field, "status": status,
                          "values": values, "raw": raw, "note": note, "gemini_matches_pipeline": g == a})


def _pipeline_notes(p: dict | None, field: str) -> str | None:
    """What the pipeline itself recorded about this field of the record (its repairs and notes), if anything."""
    if not p:
        return None
    notes: list[str] = []
    if field in ("cost_total", "cost_by_year"):
        notes += [a["detail"] for a in p.get("_anomalies") or [] if a["id"].startswith("cost")]
    elif field in ("name", "kv"):
        notes += list(p.get("_kv_notes") or [])
    elif field == "in_service" and p.get("_date_note") and "two-digit" not in p["_date_note"]:
        notes.append(f"date: {p['_date_note']}")
    elif field == "places":
        notes += list(p.get("_name_notes") or [])
    return "; ".join(notes) or None


def compare_desc(pl: Pipeline, answers: dict[int, dict], cmp: Compare) -> None:
    for r in pl.desc_recs:
        page = r["page"]
        pid = "DESC-" + (n_desc_id(r.get("project_id")) or f"P{page}")
        p = pl.by_id.get(pid)
        g = answers.get(page) or {}
        if "_error" in g or not g:
            cmp.unread.append({"source": "desc", "record": pid, "page": page, "error": g.get("_error", "no answer")})
            continue
        costs = {re.sub(r"\*", "", str(c.get("column", ""))).strip(): c.get("amount") for c in g.get("costs") or []}
        g_total = n_money(costs.pop("Total", None))
        g_years = {k: n_money(v) for k, v in costs.items() if k}
        add = lambda field, a, gv, ra, rg: cmp.add("desc", "page", pid, page, field, {"parser_a": a, "gemini": gv}, {"parser_a": ra, "gemini": rg}, p)  # noqa: E731
        add("id", n_desc_id(r.get("project_id")), n_desc_id(g.get("project_id")), r.get("project_id"), g.get("project_id"))
        add("name", n_title(r.get("name")), n_title(g.get("title")), r.get("name"), g.get("title"))
        add("in_service", n_date(r.get("in_service_raw")), n_date(g.get("in_service_date")), r.get("in_service_raw"), g.get("in_service_date"))
        if p:
            add("kv", p["kv"], n_kv(g.get("kv")), p["kv"], g.get("kv"))
            add("kind", p["kind"], g.get("kind"), f"{p['kind']} ({p['kind_basis']})", g.get("kind"))
            add("places", sorted({locate.core(e["key"]) for e in p["endpoints_parsed"]}), n_places(g.get("places")),
                [e["raw"] for e in p["endpoints_parsed"]], g.get("places"))
        add("cost_total", r.get("cost_total"), g_total, r.get("cost_total"), g_total)
        py = {k: v for k, v in (r.get("cost_by_year") or {}).items()}
        add("cost_by_year", py or None, g_years or None, py or None, {c.get("column"): c.get("amount") for c in g.get("costs") or []} or None)


def compare_ga_table(pl: Pipeline, answers: dict[int, dict], cmp: Compare) -> None:
    rows_b = pl.rows_b
    by_page_g: dict[int, dict[str, dict]] = {}
    for page, ans in answers.items():
        if "_error" in ans:
            continue
        m = {}
        for row in ans.get("rows") or []:
            t = n_digits(row.get("teams_no"))
            if t:
                m.setdefault(t, row)
        by_page_g[page] = m
    seen: set[tuple[int, str]] = set()
    for i, a in enumerate(pl.ga["rows"]):
        page, teams = a["page"], a["teams_no"]
        rid = f"GA-{teams}"
        if "_error" in (answers.get(page) or {"_error": "no answer"}):
            cmp.unread.append({"source": "ga_irp", "record": rid, "page": page, "error": (answers.get(page) or {}).get("_error", "no answer")})
            continue
        b = rows_b[i] if i < len(rows_b) else {}
        g = by_page_g.get(page, {}).get(teams)
        if g is None:
            cmp.missed.append({"source": "ga_irp", "part": "table", "record": rid, "page": page, "name": a["name"]})
            continue
        seen.add((page, teams))
        p = pl.by_id.get(rid)

        def add(field, fa, fb, fg, ra, rb, rg):
            cmp.add("ga_irp", "table", rid, page, field, {"parser_a": fa, "parser_b": fb, "gemini": fg},
                    {"parser_a": ra, "parser_b": rb, "gemini": rg}, p)

        add("id", n_digits(a["teams_no"]), n_digits(b.get("teams_no")), n_digits(g.get("teams_no")), a["teams_no"], b.get("teams_no"), g.get("teams_no"))
        add("zone", n_text(a["zone"]), n_text(b.get("zone")), n_text(g.get("zone")), a["zone"], b.get("zone"), g.get("zone"))
        add("plan_year", n_digits(a["year"]), n_digits(b.get("year")), n_digits(g.get("plan_year")), a["year"], b.get("year"), g.get("plan_year"))
        add("name", n_title(a["name"]), n_title(b.get("name")), n_title(g.get("title")), a["name"], b.get("name"), g.get("title"))
        add("in_service", n_date(a["need_date_raw"]), n_date(b.get("need_date_raw")), n_date(g.get("need_date")),
            a["need_date_raw"], b.get("need_date_raw"), g.get("need_date"))
        add("sponsor", n_text(a["sponsor"]), n_text(b.get("sponsor")), n_text(g.get("sponsor")), a["sponsor"], b.get("sponsor"), g.get("sponsor"))
        ka, kb = N.kv_of(a["name"])[0], N.kv_of(b.get("name") or "")[0]
        add("kv", ka, kb, n_kv(g.get("kv")), ka, kb, g.get("kv"))
        add("places", places_of_title(a["name"]), places_of_title(b.get("name")), n_places(g.get("places")),
            [e["raw"] for e in N.endpoints_of(a["name"])["endpoints"]], [e["raw"] for e in N.endpoints_of(b.get("name") or "")["endpoints"]],
            g.get("places"))
        # the public version redacts every cost: both parsers leave it empty, and Gemini must too (never inferred)
        add("cost_redacted", True, True, g.get("cost_usd") is None, "REDACTED", "REDACTED", g.get("cost_usd"))
    for page, m in by_page_g.items():
        for t, row in m.items():
            if (page, t) not in seen and not any(r["teams_no"] == t for r in pl.ga["rows"] if r["page"] == page):
                cmp.extra.append({"source": "ga_irp", "part": "table", "record": f"GA-{t}", "page": page, "name": row.get("title")})


def compare_ga_detail(pl: Pipeline, answers: dict[int, dict], cmp: Compare) -> None:
    """answers: {detail page: that page's entry} (the batches are split back per page by TEAMS number)."""
    for a in pl.ga["rows"]:
        d = a.get("detail")
        if not d:
            continue
        page, rid = d["page"], f"GA-{a['teams_no']}"
        g = answers.get(page)
        if g is None:
            cmp.missed.append({"source": "ga_irp", "part": "detail", "record": rid, "page": page, "name": d["title"]})
            continue
        if "_error" in g:
            cmp.unread.append({"source": "ga_irp", "record": rid, "page": page, "error": g["_error"]})
            continue
        p = pl.by_id.get(rid)

        def add(field, fa, fg, ra, rg):
            cmp.add("ga_irp", "detail", rid, page, field, {"parser_a": fa, "gemini": fg}, {"parser_a": ra, "gemini": rg}, p)

        add("id", n_digits(d["teams_no"]), n_digits(g.get("teams_no")), d["teams_no"], g.get("teams_no"))
        add("name", n_title(d["title"]), n_title(g.get("title")), d["title"], g.get("title"))
        add("in_service", n_date(d.get("need_date_raw")), n_date(g.get("need_date")), d.get("need_date_raw"), g.get("need_date"))
        add("start_date", n_date(d.get("start_date_raw")), n_date(g.get("start_date")), d.get("start_date_raw"), g.get("start_date"))
        miles, snippet = N.miles_of(d.get("description"))
        add("miles", n_miles(miles), n_miles(g.get("miles")), snippet, g.get("miles"))
        kv = N.kv_of(d["title"], d.get("description"))[0]
        add("kv", kv, n_kv(g.get("kv")), kv, g.get("kv"))
        if p:
            add("kind", p["kind"], g.get("kind"), f"{p['kind']} ({p['kind_basis']})", g.get("kind"))


def summarize(cmp: Compare) -> dict:
    by_field: dict[str, dict] = {}
    for r in cmp.rows:
        f = by_field.setdefault(r["field"], {"field": r["field"], "label": FIELDS.get(r["field"], r["field"]), "compared": 0,
                                             "gemini_matches_pipeline": 0, "all_agree": 0, "three_way": 0, "three_way_all_agree": 0,
                                             "parsers_agree": 0, "parts": set()})
        f["compared"] += 1
        f["gemini_matches_pipeline"] += r["gemini_matches_pipeline"]
        f["all_agree"] += r["status"] == "agree"
        f["parts"].add(f"{r['source']}:{r['part']}")
        if "parser_b" in r["values"]:
            f["three_way"] += 1
            f["three_way_all_agree"] += r["status"] == "agree"
            f["parsers_agree"] += r["values"]["parser_a"] == r["values"]["parser_b"]
    order = list(FIELDS)
    out = []
    for k in sorted(by_field, key=lambda k: order.index(k) if k in order else 99):
        f = by_field[k]
        f["parts"] = sorted(f["parts"])
        f["rate"] = round(100 * f["gemini_matches_pipeline"] / f["compared"], 1) if f["compared"] else None
        f["all_agree_rate"] = round(100 * f["all_agree"] / f["compared"], 1) if f["compared"] else None
        f["three_way_rate"] = round(100 * f["three_way_all_agree"] / f["three_way"], 1) if f["three_way"] else None
        out.append(f)
    total = len(cmp.rows)
    g_ok = sum(r["gemini_matches_pipeline"] for r in cmp.rows)
    return {
        "fields": out,
        "overall": {"compared": total, "gemini_matches_pipeline": g_ok, "rate": round(100 * g_ok / total, 1) if total else None,
                    "all_agree": sum(r["status"] == "agree" for r in cmp.rows)},
        "by_status": {s: sum(r["status"] == s for r in cmp.rows) for s in ("agree", "gemini_differs", "parser_b_differs", "parser_a_differs", "all_differ")},
    }


# =========================================================================== prompts


def prompt_desc(page: int) -> str:
    return f"""This is page {page} of Dominion Energy South Carolina's public list "Planned Transmission Projects $2M and above, 2024-2028" (SCRTP). The page describes one project in labelled sections: Project ID, Project Description, Project Need, Project Status, Planned In-Service Date, Estimated Project Cost.

Return:
- project_id: the value under "Project ID", as printed.
- title: the project's title, printed above "Project ID" (not the header lines "Project N of 44", "Dominion Energy South Carolina", "Planned Transmission Projects $2M and above Total", "5 Year Budget"), wrapped lines joined with one space.
- in_service_date: the "Planned In-Service Date", as printed.
- {KV_RULE}
- {KIND_RULE}
- {PLACES_RULE}
- costs: every amount in the "Estimated Project Cost" table, each exactly as printed with its '$' and commas (even when the digit grouping looks wrong), labelled by its column header (Previous, 2024, 2025, 2026, 2027, 2028, Total)."""


def prompt_ga_table(page: int) -> str:
    return f"""This is PDF page {page} of Georgia Power's 2025 IRP, Technical Appendix Volume 3 (Transmission Plan), the redacted public-disclosure version filed with the Georgia PSC. It holds part of "Table 2 Georgia ITS 10 Year Plan Project List". Columns: Zone, Year 2024, TEAMS Number, Project Name, Need Date, Project Sponsor, then five Estimated Cost columns, all REDACTED.

Return every table row on this page, top to bottom. Ignore the banner at the top, the column headers, the page footer and a "Total" row.
- zone, plan_year, teams_no, need_date, sponsor: as printed.
- title: the Project Name, wrapped lines joined with one space (a line ending in a digit and '-', such as '230-', followed by '115KV' joins without a space: '230-115KV').
- cost_usd: null when the cost cells say REDACTED (in this version they all do); a number only if a dollar amount is printed.
- {KV_RULE} (The title here is the Project Name.)
- {PLACES_RULE}"""


def prompt_ga_detail(pages: list[int]) -> str:
    return f"""These {len(pages)} pages come from Georgia Power's 2025 IRP, Technical Appendix Volume 3 (Transmission Plan), the redacted public-disclosure version: PDF pages {', '.join(map(str, pages))}. Each page is the detail page of one project: a title, "Teams #", "Need Date" and "Start Date", a Description, and cost lines that are REDACTED.

Return one entry per page, in page order ({len(pages)} entries):
- teams_no: the number after "Teams #".
- title: the project title printed above "Teams #" (not the banner), wrapped lines joined with one space.
- need_date, start_date: as printed (null if not printed).
- miles: the largest line length in miles the Description states, as a number (null if it states none).
- {KV_RULE}
- {KIND_RULE}"""


def prompt_rescue(p: dict, page: int, source_title: str) -> str:
    tried = ", ".join(f"'{e['name']}'" for e in p["endpoints_parsed"]) or "none (the title names only a customer or program)"
    reasons = "\n".join(f"  - {r}" for r in p.get("_reasons") or [])
    return f"""This is PDF page {page} of {source_title}. A data pipeline could not place one project from this page on a map.
Project: {p['id']} "{p['name']}"
Why it was set aside:
{reasons}
Place names the pipeline tried: {tried}

From THIS PAGE ONLY (its title and its description), give the substations, switching stations, power plants or other named places the work connects or sits at, so the project can be found on a map: at most two (the two ends of a line, or the one station where the work is). For each:
- name: the name as printed on the page (without voltages, work words or utility prefixes such as 'GTC:');
- quote: the exact words from the page that name it;
- alternates: other spellings or fuller names of the same place that the page itself prints (an empty list if none).
Prefer existing stations the work touches over stations the page says are new. Never invent a place the page does not print; if the page names none, return an empty list. Leave out customers and companies unless the page names a station after them.
note: one short sentence on what you found."""


# =========================================================================== rescue: the pipeline's own stages on a proposal


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", (s or "").upper())).strip()


def grounded(name: str, quote: str, page_text: str) -> tuple[bool, str]:
    flat = f" {_flat(page_text)} "
    if not _flat(name):
        return False, "empty name"
    if f" {_flat(name)} " not in flat:
        return False, f"'{name}' is not printed on the page"
    if quote and f" {_flat(quote)} " not in flat:
        return True, "the name is on the page (the quote isn't word for word)"
    return True, "printed on the page"


def locate_trial(pl: Pipeline, p: dict, replaced: dict) -> list[dict]:
    """build.locate_all's steps for one record `p` standing in for `replaced` (no network; the build's zone centers):
    1. the OSM name passes: pass 1 (no zone) is overwritten by pass 2 whenever the zone has a center, so one call with the
       center (or None when there is none) gives the build's result;
    2. leftovers: the cached Nominatim answers for (state, name) at the highest voltage any filing gives that name, the first
       one that fits, the other endpoint as it stands at that moment (endpoint 0 before endpoint 1, as the build's queue);
    3. last resort, nothing located: the line its description names, sharing a name with its own endpoints, at low confidence."""
    home = locate.HOME[p["utility"]]
    zone = pl.zc.get(p.get("zone") or "")

    def candidates(e):
        return locate.nominatim_candidates(pl.index, pl.geocoder, e, home, pl.need_kv(home, e["key"], replaced, p))

    eps = locate.locate_project(pl.index, p, zone)
    for i in range(len(eps)):
        e = eps[i]
        if e.get("lat") is None:
            other = eps[1 - i] if len(eps) == 2 else None
            hit = next((c for c in candidates(e) if locate.fits(c, p, other, zone)), None)
            if hit:
                eps[i] = dict(hit, name=e["name"], raw=e["raw"], key=e["key"])
    if any(e.get("lat") is not None for e in eps) or not p.get("description"):
        return eps
    title_keys = {e["key"] for e in eps}
    for m in build.DESC_PAIR.finditer(p["description"]):
        pair = N.endpoints_of(build.LEADING_VERBS.sub("", m.group(0)))["endpoints"]
        if len(pair) != 2 or not title_keys & {e["key"] for e in pair}:
            continue
        found = locate.locate_project(pl.index, dict(p, endpoints_parsed=pair), zone)
        for j, e in enumerate(found):
            if e.get("lat") is None:
                hit = next((c for c in candidates(e) if locate.fits(c, p, found[1 - j], zone)), None)
                if hit:
                    found[j] = dict(hit, name=e["name"], raw=e["raw"], key=e["key"])
        if any(e.get("lat") is not None for e in found):
            for e in found:
                if e.get("lat") is not None:
                    e["confidence"] = "low"
                    e["match"] = f"from the description ('{m.group(0).strip()}'): " + e["match"]
            p["_name_notes"] = p.get("_name_notes", []) + [f"endpoints taken from the description: '{m.group(0).strip()}'"]
            return found
    return eps


RULE_INFO = {rid: {"label": label, "blocking": blocking} for rid, label, blocking, _fn, _lowers in checks.RULES}


def run_trial(pl: Pipeline, q: dict, names: list[str], page: int) -> dict:
    parsed = N.endpoints_of(" - ".join(names))
    t = copy.deepcopy({k: v for k, v in q.items() if k not in ("checks", "confidence", "_reasons")})
    t["endpoints_parsed"] = parsed["endpoints"]
    t["via"] = parsed["via"]
    t["_name_notes"] = parsed["notes"] + [f"place names proposed by Gemini from PDF p{page} ({', '.join(names)}); checked by the pipeline"]
    t["kind"], t["kind_basis"] = N.kind_of(t["name"], t.get("description"), len(parsed["endpoints"]))
    before_keys = {e["key"] for e in q["endpoints_parsed"]}
    new_names = {e["key"] for e in parsed["endpoints"]} - before_keys
    if pl.index is not None and t["utility"] in locate.HOME:
        t["endpoints"] = locate_trial(pl, t, q)
    else:
        t["endpoints"] = [dict(e, lat=None, lon=None, osm=None, confidence=None, match=None, state=None) for e in parsed["endpoints"]]
    build.geometry_of(t, pl.routes)
    others = [p for p in pl.projects if p is not q]  # the whole list, so id_unique counts ids as the build does
    checks.run(others + [t], pl.zc)
    verdicts = [{"id": c["id"], **RULE_INFO[c["id"]], "status": c["status"], "detail": c["detail"]} for c in t["checks"]]
    failed = [v["id"] for v in verdicts if v["blocking"] and v["status"] == "fail"]
    return {
        "names": names,
        "endpoints_parsed": [e["name"] for e in parsed["endpoints"]],
        "new_names": bool(new_names),
        "endpoints": [{k: e.get(k) for k in ("name", "lat", "lon", "osm", "confidence", "match")} for e in t["endpoints"]],
        "checks": verdicts,
        "blocking_failed": failed,
        "passes": not failed,
        "confidence": t.get("confidence"),
        "_record": t,
    }


def rescue_one(pl: Pipeline, q: dict, page: int, answer: dict) -> dict:
    src = "desc" if q["utility"] == "DESC" else "ga_irp"
    text = pl.page_text(src, page)
    out = {
        "id": q["id"], "utility": q["utility"], "name": q["name"], "source": src, "page": page,
        "reasons_before": list(q.get("_reasons") or []),
        "tried_before": [e["name"] for e in q["endpoints_parsed"]],
        "proposal": None, "grounding": [], "trials": [], "passes": False, "verdict": "", "capped": [],
    }
    if "_error" in answer:
        out["verdict"] = f"Gemini gave no answer ({answer['_error'][:120]})"
        return out
    eps = answer.get("endpoints") or []
    out["proposal"] = {"endpoints": eps, "note": answer.get("note")}
    names, alts = [], []
    for e in eps[:2]:
        ok, why = grounded(e.get("name", ""), e.get("quote", ""), text)
        out["grounding"].append({"name": e.get("name"), "ok": ok, "why": why})
        if ok:
            names.append(e["name"].strip())
            alts.append([a.strip() for a in e.get("alternates") or [] if a and grounded(a, "", text)[0]])
        else:
            alts.append([])
    if not eps:
        out["verdict"] = "Gemini found no place named on the page: still set aside"
        return out
    if not names:
        out["verdict"] = "Gemini's names are not printed on the page: not tried, still set aside"
        return out
    combos = [names]
    for i, al in enumerate(alts[: len(names)]):
        for a in al:
            c = list(names)
            c[i] = a
            if c not in combos:
                combos.append(c)
    for c in combos[:MAX_TRIALS]:
        tr = run_trial(pl, q, c, page)
        out["trials"].append(tr)
        if tr["passes"]:
            break
    best = next((t for t in out["trials"] if t["passes"]), out["trials"][0])
    out["passes"] = best["passes"]
    out["chosen"] = out["trials"].index(best)
    title = f" {_flat(q['name'])} "
    for e in best["endpoints"]:
        e["in_title"] = f" {_flat(e['name'])} " in title
    located = [e for e in best["endpoints"] if e.get("lat") is not None]
    out["placed_from"] = None if not located else "title" if any(e["in_title"] for e in located) else "page"
    # a point at a station only the DESCRIPTION names (a line end, a connected station) is not the work site: it is capped at
    # low confidence with that note, in the report and in the record --apply would publish (low halves a pair's ranking score)
    rec = best.get("_record")
    capped = []
    for k, e in enumerate(best["endpoints"]):
        if e.get("lat") is None or e["in_title"]:
            continue
        capped.append(e["name"])
        for x in [e] + ([rec["endpoints"][k]] if rec is not None else []):
            x["confidence"] = "low"
            x["match"] = "in this area, not the work site (a name only the page's description gives, proposed by Gemini)" + (
                f": {x['match']}" if x.get("match") else "")
    out["capped"] = capped
    if capped:
        best["confidence_from_checks"] = best["confidence"]
        best["confidence"] = "low"
        what = "the point" if out["placed_from"] == "page" else "that end"
        out["caution"] = (f"{', '.join(capped)}: named only in the page's description (a line end or a connected station), not in "
                          f"its title. Read {what} as 'in this area', not the work site; it is published at low confidence.")
        if rec is not None:
            rec["confidence"] = "low"
            rec["_name_notes"] = rec.get("_name_notes", []) + [
                f"in this area, not the work site: {', '.join(capped)} {'is' if len(capped) == 1 else 'are'} named only in the "
                f"description on PDF p{page}, so the location is capped at low confidence"]
    if best["passes"] and capped:
        out["verdict"] = (f"accepted by the checks: placed at {', '.join(e['name'] for e in located)}, low confidence "
                          f"('in this area': {'a station' if len(capped) == 1 else 'stations'} only its description names)")
    elif best["passes"]:
        out["verdict"] = f"accepted by the checks: located as {', '.join(e['name'] for e in located)} ({best['confidence']} confidence)"
    elif not best["new_names"] and all(not t["new_names"] for t in out["trials"]):
        out["verdict"] = "Gemini proposed the same names the pipeline already tried: still set aside"
    else:
        out["verdict"] = "still set aside: " + ", ".join(RULE_INFO[f]["label"] for f in best["blocking_failed"])
    return out


# =========================================================================== --apply (never by default)


def swapped_list(pl: Pipeline, passing: list[dict]) -> list[dict]:
    """The rebuilt record list with each passing rescue standing in for the set-aside record it came from (by identity:
    a rescue never carries a duplicated id, id_unique is blocking)."""
    swap = {t["_record"]["id"]: t["_record"] for t in passing}
    held = {id(q) for q in pl.quarantine}
    return [swap[p["id"]] if id(p) in held and p["id"] in swap else p for p in pl.projects]


def sperry_gate(pl: Pipeline, passing: list[dict]) -> dict:
    """Do the rescues leave Sperry's worked example as it was? The guard is on OUR side of it: each of their ten projects
    matched to the same record, and each of their endpoints that was within 1 km of ours still within 1 km. (The six
    overlap distances and day gaps are computed from Sperry's own coordinates and dates, so a rescue can't move them;
    they are reported, not gated on.)"""
    before = build.sperry_report(pl.projects)
    after = build.sperry_report(swapped_list(pl, passing))

    def brief(s):
        return {"reproduced": s["reproduced"], "overlaps_ok": sum(r["ok"] for r in s["rows"]), "overlaps": len(s["rows"]),
                "projects_matched": s["projects_matched"], "endpoints_within_1km": s["endpoints_within_1km"]}

    def near(s):
        return {(m["sperry_id"], row["name"]): row["km"] is not None and row["km"] <= 1.0 for m in s["projects"] for row in m["endpoints"]}

    ours_b = {m["sperry_id"]: m["our_id"] for m in before["projects"]}
    ours_a = {m["sperry_id"]: m["our_id"] for m in after["projects"]}
    nb, na = near(before), near(after)
    rematched = sorted(k for k in ours_b if ours_b[k] and ours_a.get(k) != ours_b[k])
    moved_off = sorted(f"{k[0]} {k[1]}" for k, ok in nb.items() if ok and not na.get(k))
    b, a = brief(before), brief(after)
    ok = a["reproduced"] and not rematched and not moved_off
    return {"before": b, "after": a, "ok": ok, "rematched": rematched, "endpoints_moved_off": moved_off,
            "guards": "their projects matched to the same records; their endpoints within 1 km of ours stay within 1 km",
            "overlaps_note": "the six overlaps are computed from Sperry's own coordinates and dates: reported, not gated on"}


def committed_matches(pl: Pipeline, committed: dict | None) -> bool | None:
    """Is projects.json what the filings rebuild to offline (allowing for rescues an earlier --apply released)?"""
    if not committed:
        return None
    prev = set((committed.get("report", {}).get("gemini_reader_applied") or {}).get("released") or [])
    cq, cp = {q["id"] for q in committed["quarantine"]}, {p["id"] for p in committed["projects"]}
    rq, rk = {q["id"] for q in pl.quarantine}, {p["id"] for p in pl.kept}
    return prev <= rq and prev <= cp and not prev & cq and (cq | prev) == rq and (cp - prev) == rk


def apply_rescues(pl: Pipeline, chosen: list[dict], gate: dict, committed: dict) -> dict:
    """projects.json := the build + the rescues chosen now. An earlier application goes back to the set-aside list first, so
    '--apply --title-only' after a plain '--apply' leaves only the title-placed rescue. The check summary, coverage and the
    Sperry comparison are recomputed from the resulting record list (the same checks.run over the whole list the build uses);
    the file is written only when that recomputation keeps exactly the records it says it publishes."""
    doc = copy.deepcopy(committed)
    rep = doc["report"]
    prev = set((rep.get("gemini_reader_applied") or {}).get("released") or [])
    doc["projects"] = [p for p in doc["projects"] if p["id"] not in prev]
    held = {q["id"] for q in doc["quarantine"]}
    doc["quarantine"] += [build.quarantined(pl.by_id[i]) for i in sorted(prev - held)]
    passing = [r["trials"][r["chosen"]] for r in chosen]
    recs = {t["_record"]["id"]: t for t in passing}
    doc["quarantine"] = [q for q in doc["quarantine"] if q["id"] not in recs]
    for r in chosen:
        t = recs[r["id"]]
        pub = build.public(t["_record"])
        pub["notes"] = list(pub.get("notes") or []) + [
            "released from quarantine by gemini_reader.py --apply: Gemini proposed the place names from the filing's page "
            f"(PDF p{r['page']}), and every blocking check passed" + ("; low confidence: in this area, not the work site" if r["capped"] else "")]
        doc["projects"].append(pub)
    order = {p["id"]: i for i, p in enumerate(pl.projects)}
    doc["projects"].sort(key=lambda p: order.get(p["id"], len(order)))
    doc["quarantine"].sort(key=lambda p: order.get(p["id"], len(order)))

    records = swapped_list(pl, passing)
    kept, quar, summary = checks.run(copy.deepcopy(records), pl.zc)
    if {p["id"] for p in kept} != {p["id"] for p in doc["projects"]} or {q["id"] for q in quar} != {q["id"] for q in doc["quarantine"]}:
        raise SystemExit("  --apply refused: re-running the checks over the new record list doesn't keep exactly the records it would "
                         "publish; nothing written")
    rep["checks"] = summary
    rep["sperry_example"] = build.sperry_report(records)
    cov = rep.get("coverage") or {}
    for u in cov:
        ku = [p for p in doc["projects"] if p["utility"] == u]
        cov[u].update(located=len(ku), quarantined=cov[u]["extracted"] - len(ku),
                      high=sum(1 for p in ku if p["confidence"] == "high"), medium=sum(1 for p in ku if p["confidence"] == "medium"),
                      low=sum(1 for p in ku if p["confidence"] == "low"),
                      both_endpoints=sum(1 for p in ku if p.get("geometry") and p["geometry"]["type"] == "segment"))
    ids = sorted(recs)
    low = sorted(r["id"] for r in chosen if r["capped"])
    rep["stages"] = [s for s in rep.get("stages") or [] if s.get("id") != "gemini_reader"]
    if ids:
        rep["stages"].append({"id": "gemini_reader", "label": "Gemini's place names for set-aside records, re-checked", "in": len(pl.quarantine),
                              "out": len(ids), "ms": None,
                              "note": (f"gemini_reader.py --apply released {len(ids)} record{'s' if len(ids) != 1 else ''} that "
                                       f"pass{'es' if len(ids) == 1 else ''} every blocking check"
                                       + (f"; {len(low)} at low confidence, 'in this area'" if low else ""))})
        rep["gemini_reader_applied"] = {"at": _now(), "released": ids, "low_confidence": low, "sperry_example": gate,
                                        "note": "a build.py run rebuilds this file from the filings alone and drops these"}
    else:
        rep.pop("gemini_reader_applied", None)
    PROJECTS.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return rep.get("gemini_reader_applied")


# =========================================================================== main


def page_url(pl: Pipeline, source: str, page: int) -> str:
    url = pl.sources[source]["url"]
    return f"{url}#page={page}" if url.lower().endswith(".pdf") else url


async def run(args) -> dict:
    t0 = time.perf_counter()
    print("Reader C: Gemini reads the pages")
    pl = Pipeline()
    print(f"  pipeline rebuilt offline: {len(pl.projects)} records, {len(pl.kept)} kept, {len(pl.quarantine)} set aside "
          f"({time.perf_counter() - t0:.1f} s)")
    committed = json.loads(PROJECTS.read_text(encoding="utf-8")) if PROJECTS.exists() else None
    same_as_committed = committed_matches(pl, committed)
    reader = Reader(args.refresh)
    desc_pdf = Pages(pl.sources["desc"]["path"])
    ga_pdf = Pages(pl.sources["ga_irp"]["path"])

    desc_pages = [r["page"] for r in pl.desc_recs]
    detail_pages = sorted({r["detail"]["page"] for r in pl.ga["rows"] if r.get("detail")})
    batches = [detail_pages[i:i + DETAIL_BATCH] for i in range(0, len(detail_pages), DETAIL_BATCH)]

    async def read_desc(page):
        return page, await reader.ask("desc_page", pl.sources["desc"]["sha256"], [page], desc_pdf.pdf([page]), prompt_desc(page), DESC_SCHEMA, 30)

    async def read_table(page):
        return page, await reader.ask("ga_table", pl.sources["ga_irp"]["sha256"], [page], ga_pdf.pdf([page]), prompt_ga_table(page), GA_TABLE_SCHEMA, 45)

    async def read_detail(pages):
        return pages, await reader.ask("ga_detail", pl.sources["ga_irp"]["sha256"], pages, ga_pdf.pdf(pages), prompt_ga_detail(pages), GA_DETAIL_SCHEMA, 45)

    res = await asyncio.gather(*[read_desc(p) for p in desc_pages], *[read_table(p) for p in pl.table_pages],
                               *[read_detail(b) for b in batches])
    desc_ans = {p: a for p, a in res[: len(desc_pages)]}
    table_ans = {p: a for p, a in res[len(desc_pages): len(desc_pages) + len(pl.table_pages)]}
    detail_ans: dict[int, dict] = {}
    teams_page = {r["detail"]["teams_no"]: r["detail"]["page"] for r in pl.ga["rows"] if r.get("detail")}
    for pages, a in res[len(desc_pages) + len(pl.table_pages):]:
        if "_error" in a:
            for p in pages:
                detail_ans[p] = a
            continue
        for i, entry in enumerate(a.get("pages") or []):
            t = n_digits(entry.get("teams_no"))
            page = teams_page.get(t) if t and teams_page.get(t) in pages else (pages[i] if i < len(pages) else None)
            if page is not None and page not in detail_ans:
                detail_ans[page] = entry
    print(f"  read: {len(desc_pages)} DESC pages, {len(pl.table_pages)} Table 2 pages, {len(detail_pages)} detail pages in "
          f"{len(batches)} requests ({sum(not c['cached'] for c in reader.calls)} asked, {sum(c['cached'] for c in reader.calls)} cached)")

    cmp = Compare()
    compare_desc(pl, desc_ans, cmp)
    compare_ga_table(pl, table_ans, cmp)
    compare_ga_detail(pl, detail_ans, cmp)
    agreement = summarize(cmp)

    # rescues: one call per set-aside record, from its own page (DESC: its page; Georgia: its detail page, else its table page)
    jobs = []
    for q in pl.quarantine:
        if q["utility"] == "DESC":
            src, page, pdf = "desc", q["provenance"]["page"], desc_pdf
        else:
            src, page, pdf = "ga_irp", q["provenance"].get("detail_page") or q["provenance"]["page"], ga_pdf
        jobs.append((q, src, page, pdf))

    async def ask_rescue(q, src, page, pdf):
        return await reader.ask("rescue", pl.sources[src]["sha256"], [page], pdf.pdf([page]), prompt_rescue(q, page, pl.sources[src]["title"]), RESCUE_SCHEMA, 30)

    answers = await asyncio.gather(*[ask_rescue(*j) for j in jobs])
    rescues = [rescue_one(pl, q, page, a) for (q, _src, page, _pdf), a in zip(jobs, answers)]
    chosen = [r for r in rescues if r["passes"] and (not args.title_only or r.get("placed_from") == "title")]
    passing = [r["trials"][r["chosen"]] for r in chosen]
    gate = sperry_gate(pl, passing)
    # what projects.json holds after this run: the application made now, else whatever an earlier --apply left there
    applied = (committed or {}).get("report", {}).get("gemini_reader_applied")
    if args.apply:
        prev = (applied or {}).get("released") or []
        if not same_as_committed:
            print(f"  --apply refused: {os.path.relpath(PROJECTS, BACKEND.parent)} is not what the filings rebuild to offline "
                  "(run build.py first); nothing written")
        elif not gate["ok"]:
            print(f"  --apply refused: Sperry's worked example would not hold (re-matched {gate['rematched']}, endpoints moved "
                  f"off {gate['endpoints_moved_off']}); nothing written")
        elif not chosen and not prev:
            print("  --apply: no rescue passed the checks; nothing to write")
        else:
            applied = apply_rescues(pl, chosen, gate, committed)
            ids = (applied or {}).get("released") or []
            print(f"  --apply: {os.path.relpath(PROJECTS, BACKEND.parent)} = the build + {len(ids)} rescued records {ids}"
                  + (f" (replacing an earlier application of {len(prev)})" if prev else ""))

    # ------------------------------------------------------------------ the report
    disagreements = []
    for r in cmp.rows:
        if r["status"] == "agree":
            continue
        disagreements.append({
            "record": r["record"], "source": r["source"], "part": r["part"], "page": r["page"], "page_url": page_url(pl, r["source"], r["page"]),
            "field": r["field"], "label": FIELDS.get(r["field"], r["field"]), "status": r["status"],
            "values": r["values"], "raw": r["raw"], "pipeline_note": r["note"],
        })
    status_rank = {"all_differ": 0, "parser_a_differs": 1, "parser_b_differs": 2, "gemini_differs": 3}
    disagreements.sort(key=lambda d: (status_rank.get(d["status"], 9), list(FIELDS).index(d["field"]) if d["field"] in FIELDS else 99, d["source"], d["page"]))
    for r in rescues:
        r["page_url"] = page_url(pl, r["source"], r["page"])
        for t in r["trials"]:
            t.pop("_record", None)
    calls = reader.calls
    models = {}
    for c in calls:
        if c.get("model"):
            models[c["model"]] = models.get(c["model"], 0) + 1
    stages = {}
    for c in calls:
        s = stages.setdefault(c["stage"], {"requests": 0, "pages": 0, "failed": 0})
        s["requests"] += 1
        s["pages"] += len(c["pages"])
        s["failed"] += not c["ok"]
    read_at = sorted(c["at"] for c in calls if c.get("at"))
    would = sorted(r["id"] for r in rescues if r["passes"])
    doc = {
        "generated_at": _now(),
        "command": COMMAND,
        "advisory": ("Gemini proposes; the pipeline's checks decide. This report never changes data/projects.json by itself; "
                     "gemini_reader.py --apply folds in only rescues that pass every blocking check (a point only the page's "
                     "description names goes in at low confidence, 'in this area'), and only when Sperry's worked example stays as "
                     "it was: their projects matched to the same records, their endpoints within 1 km of ours still within 1 km."),
        "model": MODEL, "thinking": THINKING, "models_used": models,
        "sources": [{"id": s["id"], "utility": s["utility"], "title": s["title"], "file": s["file"], "url": s["url"], "sha256": s["sha256"]}
                    for s in pl.sources.values()],
        "readers": READERS,
        "pipeline": {"records": len(pl.projects), "kept": len(pl.kept), "set_aside": len(pl.quarantine),
                     "same_as_committed": same_as_committed,
                     "note": "build.py's stages rebuilt in process from the same caches (PDF text, OSM extracts, cached Nominatim; no network)"},
        "pages_read": {"desc": len(desc_pages), "ga_table": len(pl.table_pages), "ga_detail": len(detail_pages),
                       "ga_detail_requests": len(batches), "ga_detail_batch": DETAIL_BATCH},
        # what the reading took (a cached rerun reports the same: this block, like the rest, changes only when the answers do)
        "calls": {"requests": len(calls), "failed": sum(not c["ok"] for c in calls), "by_stage": stages,
                  "read_at": {"first": read_at[0], "last": read_at[-1]} if read_at else None},
        "agreement": agreement,
        "rows": {"compared_records": len({(r["source"], r["part"], r["record"]) for r in cmp.rows}),
                 "gemini_missed": cmp.missed, "gemini_extra": cmp.extra, "unread": cmp.unread},
        "disagreements": disagreements,
        "rescues": {
            "set_aside": len(pl.quarantine),
            "asked": len(rescues),
            "proposed": sum(1 for r in rescues if r["trials"]),
            "not_on_page": sum(1 for r in rescues if r["proposal"] and r["proposal"]["endpoints"] and not r["trials"]),
            "no_place": sum(1 for r in rescues if r["proposal"] and not r["proposal"]["endpoints"]),
            "passing": len(would),
            "passing_from_title": sum(1 for r in rescues if r["passes"] and r.get("placed_from") == "title"),
            "passing_from_page": sum(1 for r in rescues if r["passes"] and r.get("placed_from") == "page"),
            "passing_low_confidence": sum(1 for r in rescues if r["passes"] and r["capped"]),
            "items": sorted(rescues, key=lambda r: (not r["passes"], not r["trials"], r["id"])),
            "note": ("Offline: a proposed name is located from the OpenStreetMap power features and the Nominatim answers the build "
                     "already cached; a name that needs a new Nominatim search stays unlocated here."),
        },
        "apply": {"would_release": would, "would_release_title_only": sorted(r["id"] for r in rescues if r["passes"] and r.get("placed_from") == "title"),
                  "sperry_gate": gate, "sperry_gate_for": "title-only rescues" if args.title_only else "every passing rescue",
                  # what data/projects.json holds now (its report.gemini_reader_applied), whether applied by this run or an earlier one
                  "applied": applied, "command": f"{COMMAND} --apply", "command_title_only": f"{COMMAND} --apply --title-only"},
    }
    # rewrite only on a change: a fully cached rerun keeps the committed file byte for byte (the deployed copy and the local
    # one stay equal, which the smoke check compares)
    text = json.dumps(doc, indent=1, ensure_ascii=False, default=list)
    old = None
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    fresh = json.loads(text)
    if old and {k: v for k, v in old.items() if k != "generated_at"} == {k: v for k, v in fresh.items() if k != "generated_at"}:
        doc["generated_at"] = old.get("generated_at")
        print(f"  {os.path.relpath(OUT, BACKEND.parent)} unchanged: kept as it was (timestamp {doc['generated_at']})")
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")

    print("\n  agreement (Gemini = the pipeline's value):")
    for f in agreement["fields"]:
        three = f"   all three {f['three_way_rate']}%" if f["three_way_rate"] is not None else ""
        print(f"    {f['label']:28} {f['gemini_matches_pipeline']:>4}/{f['compared']:<4} {f['rate']:>5}%{three}")
    o = agreement["overall"]
    print(f"    {'overall':28} {o['gemini_matches_pipeline']:>4}/{o['compared']:<4} {o['rate']:>5}%   disagreements {len(disagreements)}; "
          f"rows Gemini missed {len(cmp.missed)}, extra {len(cmp.extra)}, unread {len(cmp.unread)}")
    rs = doc["rescues"]
    print(f"  rescues: {rs['asked']} set-aside records asked; {rs['proposed']} proposals tried; {rs['passing']} pass every blocking check; "
          f"{rs['not_on_page']} not on the page; {rs['no_place']} no place")
    print(f"  --apply would release: {would or 'nothing'} ({rs['passing_low_confidence']} at low confidence); Sperry gate "
          f"{'OK' if gate['ok'] else 'FAILS'} (re-matched {gate['rematched'] or 'none'}, endpoints moved off {gate['endpoints_moved_off'] or 'none'})")
    print(f"  projects.json holds applied rescues: {(applied or {}).get('released') or 'none'}")
    asked = sum(not c.get("cached") for c in calls)
    print(f"  {os.path.relpath(OUT, BACKEND.parent)} ({OUT.stat().st_size / 1024:.0f} KB) in {time.perf_counter() - t0:.1f} s; "
          f"{asked} Gemini requests now, {len(calls) - asked} cached, {doc['calls']['failed']} failed")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="set data/projects.json to the build plus the rescues that pass every blocking check (replaces an earlier --apply)")
    ap.add_argument("--refresh", action="store_true", help="ask Gemini again instead of using raw/gemini_reader/")
    ap.add_argument("--title-only", action="store_true",
                    help="with --apply: fold in only rescues located at a place named in the title (not one only the description names)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not llm.configured():
        raise SystemExit("GEMINI_API_KEY is not set in backend/.env: this stage needs Gemini (the committed report stays as it is)")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
