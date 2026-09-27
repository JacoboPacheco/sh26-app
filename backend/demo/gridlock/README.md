# Build plans: the data pipeline

Two utilities' public construction plans, turned into located, validated records you can compare on a map.
Unlike the rest of Overload (a synthetic grid model), this module uses real public filings:

- **Dominion Energy South Carolina (DESC)**: *Planned Transmission Projects $2M and above, 2024–2028*, published on SCRTP.
  Its next edition, *2026–2030* (54 pages), is read, checked, located and compared with it by `diff_filings.py`
  ([What changed since the last filing](#what-changed-since-the-last-filing-desc-20262030)). The app ranks it as DESC's
  CURRENT list ([The current list in the app](#the-current-list-in-the-app)); the 2024–2028 projects it no longer carries stay
  in the comparison, marked, and Sperry's worked example (built from 2024–2028) is still reproduced 6 of 6.
- **Georgia Power and the other Georgia ITS sponsors (GTC, MEAG Power, Dalton Utilities)**: Table 2 and the project detail pages
  of the *2025 IRP Technical Appendix Volume 3 (Transmission Plan)*, the redacted **public-disclosure** version filed with the
  Georgia PSC (Docket 56002). Every page carries a CEII banner because it is the public copy of a CEII document. The pipeline reads
  only unredacted fields (zone, year, TEAMS number, title, need date, sponsor, start date, description). Redacted costs stay `null`
  and are never inferred.

Output (committed): `data/projects.json` (projects, quarantine, report) and `data/basemap.json` (SC + GA outlines, OSM lines ≥ 115 kV).
The API in `backend/gridlock.py` serves these files. Nothing is fetched at runtime. The 2026–2030 stage adds
`data/desc_2026_2030.json` and `data/desc_changes.json` (both served: the ranking reads the first, `GET /api/gridlock/changes`
the second) and `data/desc_2026_preview.json` (a report: each list ranked alone).

## Run it

```bash
backend/venv/Scripts/pip install -r backend/demo/gridlock/requirements-build.txt   # once (offline build only)
backend/venv/Scripts/python backend/demo/gridlock/build.py --import-dir "<folder with the two PDFs>"   # first run
backend/venv/Scripts/python backend/demo/gridlock/build.py            # every rerun (~10–15 s with warm caches)
backend/venv/Scripts/python backend/demo/gridlock/diff_filings.py     # the DESC 2026-2030 filing: extract, check, diff, preview (~4 s, offline; --today YYYY-MM-DD pins the preview's date)
```

| flag | effect |
|---|---|
| `--import-dir DIR` | copy missing source PDFs into `sources/` (searched by file name; `sources/` is gitignored) |
| `--refresh-osm` | re-download the three Overpass extracts (otherwise `raw/osm/` is reused) |
| `--offline` | no network at all: cached OSM and cached Nominatim answers only |
| `--no-cache` | re-read the PDFs instead of `raw/pdf_cache/` (keyed by the file's SHA-256, so a new filing is re-read automatically) |

Each run prints a stage-by-stage report and writes the same report into `projects.json → report`. That includes
`changed_since_last_run`: which sources changed (by SHA-256), which projects were added, removed or changed (and in which fields),
and which moved into or out of quarantine. A rerun on the same inputs reports zero changes.

Parts can also be run alone: `extract_desc.py` (`desc_2026` for the 2026–2030 edition), `extract_ga.py` (prints both parsers'
agreement), `selftest.py`.

## Stages

0. **selftest** (`selftest.py`): 55 pinned cases from the filings and from Sperry's example (dates, kV, endpoint names, the
   `$19,00,181` repair, matching keys, the street-name rule, build-window flags, and the 2026–2030 edition's impossible dates,
   reused id, id keys and work orders, amounts printed without `$` or as `$00`, cost stated as a sentence, a title whose
   voltage the description contradicts, a tap's other end, and how the two editions are linked, including two look-alikes
   that must not be). If any case fails, the build stops before writing anything.
1. **extract** (`extract_desc.py`, `extract_ga.py`, `pdf_text.py`). DESC: one project per page, parsed by its labelled sections,
   yearly cost columns read from the header row. Georgia: Table 2 (PDF pp177–190) is parsed **twice, independently**. Parser A reads
   text lines (a row starts with `zone year TEAMS … date sponsor REDACTED`; the following lines continue the wrapped name). Parser B
   reads word positions (anchors in the Zone column, words inside the Project Name column between anchors). The rows are then
   joined to their **detail pages** by TEAMS number. Every record keeps its PDF page and raw text.
2. **normalize** (`normalize.py`):
   - Dates: 2-digit years, Excel serial numbers, and fields holding two dates.
   - kV from the title, or from the description when the title has none. The `23O KV` typo is read as 230 kV.
   - Kind (new line, line rebuild, reconductor, substation, tap, other), with the word that decided it.
   - Endpoint names: prefixes such as `SAV:`, `GTC:`, `CC -`, `GRID -` are stripped; qualifiers `(USA)`, `#5` and customer names
     are separated out. A title naming one place and then the station a tap is built from ("Clements Ferry Rd Sub: 115kV Tap
     from Cainhoy", 2026–2030 p53) gets that station as its other end; no 2024–2028 or Georgia title has the form, so their
     endpoints are unchanged.
   - Miles from the description.
   - Build windows. DESC: the fewest consecutive years carrying 75 % of the filed spending (a token first-year amount doesn't
     open the window; money filed after the in-service date extends it and is flagged by `spend_after_in_service`). Georgia: the filed Start Date on the detail page to the need date, a filed PLANNING window (it can span years), labeled so. Otherwise 24 months
     before in-service, with the basis stated. Every window carries `assumed`: true when the filing doesn't pin the start (no
     spend profile, no filed start date, or spend in DESC's 'Previous' column, where `latest_start` records that construction began
     before 2024). The engine re-derives assumed windows from its build-window setting.
3. **locate** (`locate.py`, `osm.py`, `geo.py`):
   - OSM data comes from three bulk Overpass queries: substations + plants in SC, the same in GA, and power lines in SC + GA.
     They are cached in `raw/osm/`.
   - Names match `exact`, `variant` (generic words like PRIMARY / PLANT / DAM dropped, -BOROUGH = -BORO) or `fuzzy` (edit
     distance: none for names up to 5 letters, 1 up to 12).
   - Features of one name within 2 km are one site.
   - When a name matches several sites, the pair that makes the line shortest wins. After that come the operator and the Georgia
     planning zone's median location, learned from confident matches in a first pass.
   - Candidates more than 30 km outside the filer's state are never considered.
   - Leftovers go to Nominatim, at most 60 distinct queries ever, 1 per second, cached. A result must carry the endpoint's own name
     and be the right kind of thing (a road only for a road-named substation). A road with no other located endpoint to anchor it
     must not be a name every town has (First Avenue, Main Street), must sit beside a transmission substation, and in Georgia must
     be within 40 km of its planning zone's median. The nearest unnamed substation at the highest voltage
     the filings give that name stands in for the place. Every project naming that place gets the same answer.
   - Last resort: a project's description, e.g. "Construct Okatie – Riverport 230 kV".
   - Every endpoint records the OSM feature (with a link), a confidence (high / medium / low) and the reason in plain words.
4. **checks** (`checks.py`): 16 named rules (`RULES`), plus 3 added for the 2026–2030 edition (`FILING_RULES`; `ALL_RULES` is all
   19). A record failing a blocking rule moves to `quarantine` **with its reasons and every parsed field**; nothing is silently
   dropped. Warnings keep the record, and the location warnings lower its confidence.
5. **write** (`build.py`): `projects.json`, `basemap.json`, the report. Before writing, the build also checks that Sperry's
   worked example is reproduced (a hard gate).

### The checks

| id | blocking | catches |
|---|---|---|
| `extract_complete` | yes | a page or row whose sections didn't parse |
| `id_unique` | yes | two records with one project id / TEAMS number |
| `id_one_project` (2026–2030) | yes | one id printed for two DIFFERENT projects in one filing (`6809 M` on p19 and p48); a work order inside another id's range (`6367 D` / `06367 D - G`) is a warning |
| `date_valid` | yes | in-service date unreadable or outside 2020–2040 |
| `date_real` (2026–2030) | yes | a printed date that isn't on the calendar, with the reason (`04/31/26`: April has 30 days), including an earlier phase of a phased field, which `date_valid` doesn't read |
| `place_named` | yes | titles that name only a customer or program (e.g. "CC - PROJECT CHRONOS- SK/HYUNDAI") |
| `located` | yes | no endpoint found (adds a hint when the filing says the substation is new) |
| `in_region` | yes | a located point outside SC and GA (Census outlines) |
| `in_territory` | yes | a point more than 30 km outside the filer's state |
| `span_plausible` | yes | a line whose endpoints are more than 150 km apart (a wrong same-name match) |
| `two_parsers_agree` | no | the two Table 2 parsers read a row differently |
| `table_matches_detail` | no | table title / need date differ from the detail page (the filing has 4 title differences, e.g. `TALLBOT` vs `TALBOT`, and 3 need-date differences; the table's date is kept) |
| `costs_consistent` | no | DESC cost columns that don't add up, or a malformed amount (`$19,00,181`, repaired from the Total and recorded; `$00` with a leading zero, 2026–2030 p53) |
| `date_normalized` | no | Excel serials and multi-date fields (2-digit years pass with a note) |
| `voltage_found` | no | no kV, a repaired typo, or a letter O inside a voltage pair the repair can't read (`23O-115kV`, found by the fault test) |
| `kv_title_matches_description` (2026–2030) | no | the description names only other voltages than the title (p13: title 115kV, description "a 230 kV Tap"), or says there is no work at a voltage the title names (p44: "No 230kV work associated with this project"); the title's voltage is kept and the conflict is stated |
| `spend_after_in_service` (2026–2030) | no | a quarter or more of the filed spending in years after the in-service date (p26 Urquhart – Aiken PSA: $2.9M of $3.0M in 2028, in service 12/31/2027); the window follows the money |
| `fully_located` | no, lowers confidence | one of two endpoints missing (the project sits at the other one, as in Sperry's guide) |
| `length_consistent` | no | straight-line span longer than the filed miles (usually a section of a longer line) |
| `zone_consistent` | no, lowers confidence | more than 100 km from its Georgia planning zone's other projects |

## Fault test: we tried to break it

```bash
backend/venv/Scripts/python backend/demo/gridlock/faults.py            # every eligible record, once per fault (~1 s)
backend/venv/Scripts/python backend/demo/gridlock/faults.py --per-kind 10   # a seeded sample, half DESC, half Georgia
```

`faults.py` takes the validated records in `data/projects.json` and injects nine kinds of bad data (five the pipeline has met in
the two filings or Sperry's sheet, four common data-entry errors) plus one format test (the two-digit year, a real format of the
filings that must be read correctly and is never counted as a catch). Each copy goes back through the same code the build runs: a DESC fault goes into the PDF page text, which
`extract_desc.parse_page` re-reads; a Georgia fault goes into the parsed Table 2 row, which `build._common` re-normalizes; a
location fault moves the located point the way a wrong match would; a title whose place names change goes back through the
locate stage (cached OSM, no network). Then `checks.run`, the same record checks (16 per project). Nothing is re-implemented.

A **control** runs first: the same records with nothing changed. 191 of the 194 are re-run exactly from their filed text (3 take
their place names from a detail page or description and are left out), every one is kept, and every check result equals the
published one. So a catch is the fault's doing, not a false alarm.

| bad data | met in | caught (every eligible record) | first caught by |
|---|---|---|---|
| Excel serial date (`45809`) | Sperry's sheet | 191 of 191, flagged, right date | `date_normalized` |
| Impossible date (`04/31/26`) | common entry error | 191 of 191, set aside | `date_valid` |
| Malformed amount (`$19,00,181`) | DESC p22 | 41 of 41, flagged (2 values not recoverable: those pages' Totals don't equal their columns) | `costs_consistent` |
| Duplicate project id | common entry error | 191 of 191, set aside (with the record it copies) | `id_unique` |
| Customer-only title (`CC - PROJECT …`) | Georgia Table 2 | 191 of 191, set aside | `place_named` |
| Same-name substation 200 km away | GA-13166 | 160 of 191 | `zone_consistent`, `span_plausible` |
| Point in another state | common entry error | 191 of 191, set aside | `in_region` |
| Letter O in a voltage (`23O KV`) | Georgia Table 2 | 74 of 74 | `voltage_found` |
| Swapped latitude and longitude | common entry error | 191 of 191, set aside | `in_region` |

**1,421 of 1,452 bad records caught** (caught = set aside or flagged). **Format test, not counted:** 172 of 172 dates rewritten
with a two-digit year (`12/31/26`, as DESC files them) were read as the right date; nothing was wrong, so no check "caught" them
(the report lists them under `format_test`, with `noted_by`, never `caught_by`). The 31 bad records that slip through are all one kind, and the report lists each with the reason: a single-substation
project (no second endpoint) moved to a same-name site elsewhere in its own state. DESC files no planning zones and Georgia zone 201
has too few confident matches for a median, so nothing in the record says where it should be. One is a line whose moved end landed
111 km from its other end, under the 150 km limit. The fault test also found a real gap, now closed: a letter O inside a voltage
pair (`23O-115kV`, `50O/230KV`) wasn't repaired and slipped through silently; `voltage_found` now flags it (pinned in `selftest.py`).
The voltage itself still reads wrong until the repair in `normalize.fix_voltage_typos` learns the pair form.

Output: `data/fault_report.json` (committed), served at `GET /api/gridlock/fault-test`. Rerun it after any change to a check or a parser.

## What changed since the last filing (DESC 2026–2030)

```bash
backend/venv/Scripts/python backend/demo/gridlock/diff_filings.py                     # ~4 s, no network
backend/venv/Scripts/python backend/demo/gridlock/diff_filings.py --today 2026-09-26  # the preview measured on a given date
```

It rewrites a file only when its content changes. On the same inputs a rerun writes nothing. The preview also records its
date and the SHA-256 of `backend/gridlock.py`, so a run on a later day (without `--today`) or after an engine change rewrites
`desc_2026_preview.json` only. `desc_2026_2030.json` and `desc_changes.json` don't depend on either.

DESC's next edition, *Planned Transmission Projects $2M and above, 2026–2030* (SCRTP, 54 pages, SHA-256 `e98bfeb8…`), goes
through the same stages as the build and is then compared with the 2024–2028 edition. It never writes `data/projects.json`;
the app merges the two at load time (see [The current list in the app](#the-current-list-in-the-app)).

1. **extract** (`extract_desc.filing_doc`). The same page parser. The yearly columns come from each page's header row, so
   2026–2030 needs no new layout. Three printed forms are new:
   - amounts printed without a `$` (`0` on p19 and p46, `25,000` on p54), read as dollars and recorded as anomalies;
   - an amount with a leading zero (`$00`, 2028 on p53), read as $0 (the columns then add to the Total) and recorded;
   - costs stated as a sentence ("Estimated cost of $20,350,000 is to be financed by the interconnection customer…", p18 and
     p30), kept in `cost_note`.

   None of these forms occurs in the 2024–2028 edition, whose extraction is byte-identical. 54 of 54 pages parse cleanly, with
   14 anomalies recorded (9 totals that don't equal their columns, one broken grouping `$14,303648`, three bare amounts, `$00`).
   - **Two independent readers.** A second PDF engine (pdfium, via pypdfium2, pinned in `requirements-build.txt`) must find
     each record's id, title, in-service date and total on the same page; it finds 54 of 54.
   - **Read by eye.** 14 pages were read from the rendered page image (`MANUAL_SPOT_CHECK`), and every run re-checks the
     extraction against them. 14 of 14 match.
2. **check.** `build.normalize_desc`, then `build.locate_all` against the cached OSM extracts and the cached Nominatim answers
   only (zero network calls, asserted), then `checks.ALL_RULES`. **40 kept, 14 set aside**, each with its reasons:
   - two impossible dates: `04/31/26` (p1, April has 30 days) and `06/31/2026` (p5, June has 30 days)
   - one id printed for two projects: `6809 M` (p19 'St George - Sumter 230kV Tie', p48 'Modoc – McCormick 115/46 kV Rebuild')
   - 10 with no place found in OSM or the cached answers. Several are new stations (Scout, Winnsboro West, Atomic Road).
     Riverport (p13) was placed in the 2024–2028 build only through its description, 'Construct Okatie – Riverport 230 kV'. The
     new description reads 'Constructing a 230 kV Tap from Okatie to Riverport', a form the build's last-resort pattern
     (`build.DESC_PAIR`) doesn't read, so the record is set aside. Teaching it that form is part of the integration step.

   Kept with warnings:
   - `6367 D` (p13) and `06367 D - G` (p12) share work order 6367 D.
   - Two titles disagree with their descriptions on voltage (`kv_title_matches_description`). p13's title says 115kV and its
     description "a 230 kV Tap". p44's title says 230/115KV and its description "No 230kV work associated with this project".
   - Clements Ferry Rd (p53) is placed at Cainhoy, the station its title says the tap is built from (the other end isn't
     mapped), and carries its `$00` amount.

   Of the 72 endpoint names, 27 are new to the DESC list. All 36 names that the 2024–2028 build placed land on exactly the
   same point. The three new rules also run over the 2024–2028 and Georgia records (the build's own extract and normalize),
   and they fail or warn on none of the 252.
3. **diff** (`data/desc_changes.json`). The two lists are linked in three steps, strictest first:
   - **By id.** The same id key (`6853 B-F` = `6853BF`, leading zeros dropped) and the same project (title similarity ≥ 0.8,
     or description similarity ≥ 0.9 when it was retitled: `6809 G`).
   - **By title, under a new id.** Title similarity ≥ 0.85, and the ids share a work order or base number (or both titles
     name a place), each the other's best match. Riverport `06367 A - C, H` → `6367 D` and Church Creek – Faber Place
     `6847 A-B, D-H` → `6847` link this way. The id condition keeps look-alike titles apart ("X 115 kV Tap: Construct").
   - **By description, under a new id and title.** Description similarity ≥ 0.9, the ids share a work order or base
     number, and each is the other's best match. Cainhoy 115 kV Tap `0147 C, K` (2024–2028 p37) → Clements Ferry Rd Sub:
     115kV Tap from Cainhoy `0147 A-I` (2026–2030 p53) links this way: the two pages print the same description word for
     word, and both ids include work order 0147 C. Jack Primus `0147 B, J` shares work order 0147 B but has a different
     description (similarity 0.86), so it stays dropped, and its row names the shared work order.

   Results:
   - **32 carried over.** 24 have a new in-service date (all later), 30 a new cost estimate (21 higher, 9 lower), and 1 is
     the same in both. For example, Church Creek - Ritter's in-service date goes from 6/1/2024 to 12/31/2028, and Union Pier's
     estimate from $5.3M to $22.4M.
   - **12 dropped.** 11 of them have a 2024–2028 in-service date before 2026, the first year the new list covers.
   - **22 new.**

   Every row cites both PDF pages and states each list's value. A dropped or new row says that no project in the other
   list matches it by id, title or description, and names any work order it shares with a different id. The lists don't
   say why a date or an estimate changed, and neither does the file.
4. **preview** (`data/desc_2026_preview.json`, in memory). The cross-state pairs the 2026–2030 list would make with the four
   Georgia sponsors, computed by `backend/gridlock.py`'s own code (imported unchanged; 40 km, 24-month windows, closest points;
   today = the real date, or `--today`). The same numbers for the committed 2024–2028 data come from the same code.

   | measured 2026-09-26 | with 2024–2028 | with 2026–2030 |
   |---|---|---|
   | DESC projects on the map | 43 | 40 |
   | pairs flagged | 71 | 56 |
   | same station | 2 | 2 |
   | shared build window | 28 | 31 |
   | shared window still ahead | 1 | 8 |
   | shared window open now | 0 | 13 |
   | shared window ended | 27 | 10 |

   37 pairs are flagged with both lists, 19 only with the new one and 34 only with the old one. Both same-station pairs come
   from a new DESC project, 'Okatie – McIntosh 115kV Tie: Add Series Reactor' (p41), at McIntosh. Two Georgia Power projects
   from Sperry's worked example also work there: the Goshen – McIntosh 115 kV line rebuild and the McIntosh – Purrysburg
   230 kV reactors.

**Ids.** A 2026–2030 record gets the build's id (`DESC-` + its printed id). A carried-over project therefore keeps its
2024–2028 id, and both records printed as `6809 M` are `DESC-6809M` (both are set aside for it). The app never serves the two
editions of one project side by side (below), so no id is read twice.

### The current list in the app

`backend/gridlock.py` (`_merge_current`, at load time, whenever either file changes) ranks DESC's CURRENT plan:

- every 2026–2030 record, kept (40) or set aside (14) as its checks decided, each marked `edition: "2026-2030"` with a note
  (new in this list, or also in 2024–2028 with the earlier in-service date);
- from 2024–2028, only the 12 projects the new list no longer carries (`dropped` in `desc_changes.json`; 11 were due in service
  before 2026), marked `edition: "2024-2028"`. They keep finished work and Sperry's worked example comparable (four of their five
  DESC projects are among them); a project in both lists is read from the 2026–2030 one only;
- Georgia's records unchanged.

The report is re-counted over those 274 rows (3 filings, 203 passed, 71 set aside; the 19 checks, the 3 new ones also run over
the 2024–2028 and Georgia records by this script). `_load("as_filed")` still gives `projects.json` exactly as the build wrote it
(this script's preview uses it). The list is grouped so what can still be built together comes first: pairs whose build windows
share months still ahead or open now, then pairs still to be built at different times, then pairs whose time has passed as
filed; same station first within a group, then the score (unchanged). Measured 2026-09-26, DESC × Georgia Power within 25 mi
(40.2336 km): 100 pairs, 25 building in the same months (8 still ahead, 17 open now), against 75 and 3 with the 2024–2028
list alone. Pair 1 is DESC's Stevens Creek – Graniteville 115 kV rebuild with Georgia Power's Evans Primary – Thurmond Dam
115 kV rebuild (10.7 mi to Thurmond Dam; the filed windows share Jun–Dec 2029); Sperry's OVL_3 is pair 7, and OVL_1
(Thurmond Dam) sits with the pairs whose time has passed (pair 51).

**Integration step (the pipeline part, not done here).** Move `FILING_RULES` into `RULES`, teach `build.DESC_PAIR` the "Tap from X to Y" form
(Riverport), and rerun `build.py` and `faults.py`. The rules part was measured in a scratch copy with all 19 rules:
- The build keeps the same 194 records and sets aside the same 58. The 16 existing checks give the same counts, the three
  new ones pass all 252 records, confidences are unchanged, and Sperry's example is still reproduced (6 of 6).
- The fault test still catches 1,421 of 1,452, with a clean control. `date_real` and `id_one_project` are then listed as
  second catchers of the impossible-date and duplicate-id faults. `kv_title_matches_description` catches nothing new.

Until then `build.py` and `faults.py` reproduce their committed outputs exactly.

## Reader C: Gemini reads the pages

```bash
backend/venv/Scripts/python backend/demo/gridlock/gemini_reader.py                  # read, compare, propose rescues (report only)
backend/venv/Scripts/python backend/demo/gridlock/gemini_reader.py --apply          # projects.json = the build + every passing rescue
backend/venv/Scripts/python backend/demo/gridlock/gemini_reader.py --apply --title-only   # only rescues placed from their own title
```

An offline, advisory stage (never at request time; it needs `GEMINI_API_KEY`). `gemini_reader.py` cuts the two PDFs into
single pages with pypdfium2 and sends each page the parsers read to Gemini as inline `application/pdf`, with a JSON Schema of the
fields the parsers extract (structured output through `llm.complete_json`, `GEMINI_READER_MODEL`, default `gemini-3.5-flash`,
thinking at its minimum): the 44 DESC project pages one by one, the 14 pages of Georgia's Table 2 one by one, and the 208 Georgia
detail pages twelve to a request (each detail page names its own TEAMS #, so answers are joined back per page). The answers are
cached in `raw/gemini_reader/` (gitignored) by the source file's SHA-256, the pages and the prompt, so a rerun costs nothing.
The first run made 134 requests (76 reading, 58 rescue); the stage never reads or writes the app's own answer cache. The report
is rewritten only when its content changes, so a cached rerun leaves the committed file (and its timestamp) byte for byte.

1. **Compare.** Every Gemini value and every parser value goes through the pipeline's own normalizing first (`parse_date`,
   `kv_of`, `endpoints_of` / `endpoint_key` / `locate.core`, the Sperry title canon), then field by field. Georgia Table 2 has three
   readers (parser A, text lines; parser B, word positions; Gemini). DESC pages and Georgia detail pages have two (the page parser
   and Gemini). Every disagreement is listed with its PDF page, each reader's raw and normalized value, and what the pipeline
   itself recorded about that field (its repairs).
2. **Rescue proposals.** For each of the 58 set-aside records, Gemini is asked, from that record's page and with the checks'
   reasons, for the places the work connects or sits at, with the exact words it took them from. A name that isn't printed on
   the page is not tried. A printed one goes through `endpoints_of`, the locate stage exactly as `build.locate_all` runs it for
   one record (the OSM name passes; the cached Nominatim answers at the highest voltage any filing gives that name, the first
   that `locate.fits`; then the line its description names; no network here) and `checks.run` over the whole record list (so
   `id_unique` counts as in the build). A proposal passes only when every blocking check passes. A point placed at a station
   that only the page's description names (a line end, a connected station), not the title, is capped at low confidence and
   noted "in this area, not the work site", in the report and in the record `--apply` would publish; low confidence halves a
   pair's score in the overlap ranking.
3. **`--apply`** (never by default) sets `data/projects.json` to the build plus the rescues chosen now (an earlier application is
   replaced, not stacked; `--title-only` keeps only those placed from their own title), each with a note saying so, and
   recomputes the check summary, coverage and Sperry comparison by re-running `checks.run` over the resulting list. It writes only
   when (a) `projects.json` is what the filings rebuild to offline and (b) the rescues leave Sperry's worked example as it was:
   each of their ten projects matched to the same record, each endpoint that was within 1 km of ours still within 1 km. (The six
   overlap distances come from Sperry's own coordinates and dates, so no rescue can move them; they are reported, not gated
   on.) `build.py` rebuilds that file from the filings alone, so an applied rescue lasts until the next build; the reader's
   report says which rescues `projects.json` holds.

First run (the committed `data/gemini_reader.json`, served at `GET /api/gridlock/reader`):

- **3,625 of 3,680 values agree (98.5 %).** Ids, dates, zones, plan years, sponsors, DESC totals, start dates and miles agree
  100 %. Gemini returned all 208 Table 2 rows and left every redacted cost empty (208 of 208). The parsers never disagree with
  each other (Table 2 has two, the DESC and detail pages one), so each of the 55 disagreements is Gemini against the parsers.
- **What the disagreements show.** Gemini copied `23O KV` as `230 KV`, a silent repair where the pipeline keeps the filed text and
  records the repair. On four detail pages it misspelled `RECONDUCTOR` in the title (`RECONSTRUCTION`, `RECONVERTOR`,
  `RECONDUCUTOR`, one with a Cyrillic `Т`). It read DESC p22's `$19,00,181` as printed, where the pipeline repairs it from the
  Total. 25 kind-of-work labels differ (90.1 %), and 20 place lists (92.1 %). In some of those Gemini is right where the
  title parser stops at the first work word (`MORNING HORNET … & THUMBS UP`, `GARRETT ROAD SWITCHING STATION - TRAE LANE`). In
  others it names a place for a customer-only title (`SAVANNAH`, `FAYETTEVILLE`) that the pipeline deliberately never geocodes.
- **Rescues: 15 of 58 pass every blocking check. 14 of those are placed at a station that only the page's description names**
  (a line end or a connected station, e.g. a new Scout substation placed at VCS1, one end of the line it folds into; GA-21013
  through the line its description names, the build's own last resort). Those points are "in this area", not the work site:
  the report marks each one (`placed_from: page`, `capped`, a caution) and they would be published at low confidence. Only
  GA-20797 is placed from its own title (at the existing Villa Rica substation). The other 43 stay set aside: 22 because
  Gemini proposed the same names the pipeline already tried, and 21 because the new names aren't in OSM's power features or in
  the cached Nominatim answers.

## Exports in Sperry's table format

- `GET /api/gridlock/export.xlsx`: a workbook with Sperry's two sheets, `projects` and `overlaps`, with exactly the columns of their
  `Projects_Overlaps.xlsx` in their order (`lat_center` / `lon_center` keep their formula), filled from the whole pipeline; our extra
  columns sit to the right (closest-point distance, tier, score, confidence, PDF file and page, OSM links, checks passed). An
  `about` sheet says where it came from and defines every added column.
  - `shared_window` is filled only for pairs whose build windows overlap (still ahead / open now / ended as filed); a pair whose
    windows don't overlap says `no shared window` (`window_gap_days` gives the gap).
  - `build_window_start` / `_end` are the windows the overlaps were scored with at the chosen `window_months` (`window_filed`
    says whether the filing gave them).
  - `in_sperry_example` carries Sperry's own id for their six pairs as `Sperry OVL_n`: their numbering. Our sheet's `overlap_id` is `PAIR_<rank>` (it was `OVL_<rank>`, which collided with Sperry's own ids).
  - Built per request (about 20 ms), so the `Generated` time is the file's own.
- `GET /api/gridlock/export.csv?table=projects|overlaps|set_aside` and `GET /api/gridlock/export.geojson` (projects as features).
- All three take the overlap settings of `/api/gridlock/opportunities`: `max_km`, `window_months`, `method`, `a`, `b`.

## Tracing a number

Every project has `provenance` (source id, PDF page, raw text; Georgia rows also have `detail_page`), `checks`, `notes`, and per
endpoint the OSM feature, its URL and the match reason. Two examples:

- **DESC-06367ACH, 2024 spend $19,000,181.** PDF page 22 prints `$19,00,181`. The columns only add up to the filed Total of
  $34,877,427 if the value is $19,000,181, and those digits fit inside it, so the repaired value is used. The record's
  `costs_consistent` check says so in words.
- **Okatie.** No OSM substation is named Okatie. Nominatim finds "Okatie Village". The filings name Okatie in 230 kV projects, and
  the nearest 230 kV substation is an unnamed switching station (OSM way 1064022697). That is 10 m from the point in Sperry's own
  sheet. Confidence: low, with that sentence as the reason.

## Sperry's worked example

`sperry_example.json` is Sperry's answer key: 10 projects, 6 overlaps, values exactly as their sheet stores them.
`report.sperry_example` shows the pipeline:

- Reproduces all six distances within 0.01 mi and all six time gaps exactly, using their center method with a 6,371 km Earth
  radius. It finds exactly their six pairs under 25 mi and no others.
- Finds all 10 of their projects in our own extraction, with the same in-service dates. Their `45809` and `45778` are Excel serials
  for 2025-06-01 and 2025-05-01.
- Places all 16 of their located endpoints within 1 km of ours. The largest gap, 0.66 km, is their own McIntosh, which their sheet
  places at two points 0.66 km apart.

## Sources and licenses

- DESC project descriptions: <https://www.scrtp.com/assets/pdfs/home/2024-2028-2million-and-above-project-descriptions.pdf>
- DESC project descriptions, next edition: <https://www.scrtp.com/assets/pdfs/home/2026-2030-2million-and-above-project-descriptions.pdf>
  (555,527 bytes; the SHA-256 is in `data/desc_2026_2030.json → source`)
- Georgia Power 2025 IRP, Docket 56002: <https://psc.ga.gov/search/facts-docket/?docketId=56002>. This is the public-disclosure
  Volume 3, as supplied in Sperry's ShellHacks 2026 starter package.
- Context: Georgia Power's filing announcement cites roughly 8,200 MW of load growth expected over six years and says many new
  businesses "bring large electrical demands"
  (<https://www.georgiapower.com/news-hub/press-releases/georgia-power-files-2025-irp-plan-to-meet-energy-needs.html>). The
  Volume 3 text itself doesn't mention data centers, and neither does this pipeline. Some titles name customers; they are kept in
  `customer_named` and never geocoded.
- Map features: © OpenStreetMap contributors, ODbL 1.0 (<https://www.openstreetmap.org/copyright>), via the Overpass API and
  Nominatim. The basemap lines are OSM data; attribution must stay visible wherever they are drawn.
- State outlines: U.S. Census Bureau `cb_2024_us_state_20m`, public domain.

## Known limits

- **Geometry is a straight line between the two located endpoints**, except for the few lines that match a named OSM line at the
  project's own voltage class (then the mapped route is used; a 115 kV project never follows the 230 kV line between the same two
  stations). Real routes aren't in the filings.
- **OSM names differ from the filings**, and many substations in OSM have no name at all. Planned substations (e.g. "New Lacy",
  "Cavender Drive") aren't in OSM yet. Of the 252 projects, 194 are located and 58 are quarantined with reasons: 48 have no place
  found, 9 name only a customer or program, and 1 is a line whose two matches are 204 km apart. Coverage by utility is in
  `report.coverage`. (GA-13166 "First Avenue - North Columbus" is one of the 48: the only "First Avenue" found was near Atlanta,
  about 150 km from Columbus, so it is set aside rather than placed there.)
- **Low confidence means low confidence.** Low covers town centroids, the nearest unnamed substation, and fuzzy spellings. Treat
  those points as "in this area", not "at this fence line".
- The Nominatim budget (60 queries in total) went to DESC first, the headline pair, so some Georgia leftovers were never queried.
  Purrysburg (SC), the far end of Georgia Power's McIntosh tie, is one; Sperry's sheet leaves it blank too.
- Sponsor `SAV` rows are counted as Georgia Power, as Sperry's example does (noted on each record).
- A few names are read with documented assumptions: `VCS1` / `VCS2` = the V.C. Summer station site; "Jack McDonough" / "Atkinson" =
  OSM's "Plant McDonough-Atkinson". Each record that uses one says so in its match reason.
- The pipeline never says whether utilities are or aren't coordinating. Overlaps are places where they *could* coordinate.
