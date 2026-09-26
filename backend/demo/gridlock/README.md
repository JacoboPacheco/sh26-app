# Build plans: the data pipeline

Two utilities' public construction plans, turned into located, validated records you can compare on a map.
Unlike the rest of Overload (a synthetic grid model), this module uses real public filings:

- **Dominion Energy South Carolina (DESC)**: *Planned Transmission Projects $2M and above, 2024–2028*, published on SCRTP.
- **Georgia Power and the other Georgia ITS sponsors (GTC, MEAG Power, Dalton Utilities)**: Table 2 and the project detail pages
  of the *2025 IRP Technical Appendix Volume 3 (Transmission Plan)*, the redacted **public-disclosure** version filed with the
  Georgia PSC (Docket 56002). Every page carries a CEII banner because it is the public copy of a CEII document. The pipeline reads
  only unredacted fields (zone, year, TEAMS number, title, need date, sponsor, start date, description). Redacted costs stay `null`
  and are never inferred.

Output (committed): `data/projects.json` (projects, quarantine, report) and `data/basemap.json` (SC + GA outlines, OSM lines ≥ 115 kV).
The API in `backend/gridlock.py` serves these files. Nothing is fetched at runtime.

## Run it

```bash
backend/venv/Scripts/pip install -r backend/demo/gridlock/requirements-build.txt   # once (offline build only)
backend/venv/Scripts/python backend/demo/gridlock/build.py --import-dir "<folder with the two PDFs>"   # first run
backend/venv/Scripts/python backend/demo/gridlock/build.py            # every rerun (~10–15 s with warm caches)
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

Parts can also be run alone: `extract_desc.py`, `extract_ga.py` (prints both parsers' agreement), `selftest.py`.

## Stages

0. **selftest** (`selftest.py`): 29 pinned cases from the filings and from Sperry's example (dates, kV, endpoint names, the
   `$19,00,181` repair, matching keys, the street-name rule, build-window flags). If any case fails, the build stops before
   writing anything.
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
     are separated out.
   - Miles from the description.
   - Build windows. DESC: from the yearly spend profile. Georgia: from the filed Start Date on the detail page. Otherwise 24 months
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
4. **checks** (`checks.py`): 16 named rules. A record failing a blocking rule moves to `quarantine` **with its reasons and every
   parsed field**; nothing is silently dropped. Warnings keep the record, and the location warnings lower its confidence.
5. **write** (`build.py`): `projects.json`, `basemap.json`, the report. Before writing, the build also checks that Sperry's
   worked example is reproduced (a hard gate).

### The checks

| id | blocking | catches |
|---|---|---|
| `extract_complete` | yes | a page or row whose sections didn't parse |
| `id_unique` | yes | two records with one project id / TEAMS number |
| `date_valid` | yes | in-service date unreadable or outside 2020–2040 |
| `place_named` | yes | titles that name only a customer or program (e.g. "CC - PROJECT CHRONOS- SK/HYUNDAI") |
| `located` | yes | no endpoint found (adds a hint when the filing says the substation is new) |
| `in_region` | yes | a located point outside SC and GA (Census outlines) |
| `in_territory` | yes | a point more than 30 km outside the filer's state |
| `span_plausible` | yes | a line whose endpoints are more than 150 km apart (a wrong same-name match) |
| `two_parsers_agree` | no | the two Table 2 parsers read a row differently |
| `table_matches_detail` | no | table title / need date differ from the detail page (the filing has 4 title differences, e.g. `TALLBOT` vs `TALBOT`, and 3 need-date differences; the table's date is kept) |
| `costs_consistent` | no | DESC cost columns that don't add up, or a malformed amount (`$19,00,181`, repaired from the Total and recorded) |
| `date_normalized` | no | Excel serials and multi-date fields (2-digit years pass with a note) |
| `voltage_found` | no | no kV, or a repaired typo |
| `fully_located` | no, lowers confidence | one of two endpoints missing (the project sits at the other one, as in Sperry's guide) |
| `length_consistent` | no | straight-line span longer than the filed miles (usually a section of a longer line) |
| `zone_consistent` | no, lowers confidence | more than 100 km from its Georgia planning zone's other projects |

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
