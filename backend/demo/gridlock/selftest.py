"""Stage 0, self-test: pinned cases for the parsers and matchers, taken from the real filings and from
Sperry's worked example. build.py stops before writing anything if one fails, so a change to a regex
can't quietly send bad records downstream.

    backend/venv/Scripts/python backend/demo/gridlock/selftest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402
import extract_desc  # noqa: E402
import extract_ga  # noqa: E402
import locate  # noqa: E402
import normalize as N  # noqa: E402


def _keys(name: str) -> list[str]:
    return [e["key"] for e in N.endpoints_of(name)["endpoints"]]


def _ids(a: tuple, b: tuple) -> list[str]:
    """id_one_project over two records (id, title, filing): each record's status."""
    recs = [{"id": f"r{i}", "project_id_raw": pid, "name": title, "provenance": {"source": src, "page": 19 + 29 * i}}
            for i, (pid, title, src) in enumerate((a, b))]
    ctx = checks.context(recs, {})
    return [checks.id_one_project(r, ctx)[0] for r in recs]


def _phases_date_valid(raw: str) -> str:
    iso, note = N.parse_date(raw)
    return checks.date_valid({"in_service": iso, "in_service_raw": raw, "_date_note": note}, {})[0]


def _link(old: list[tuple], new: list[tuple]) -> list[tuple[str, str, str]]:
    """diff_filings.link_filings over (id, title, description) records: (old id, new id, how) per link."""
    import diff_filings  # lazy: it imports build, which imports this module

    def recs(rows):
        return [{"page": k + 1, "project_id": pid, "name": title, "description": desc} for k, (pid, title, desc) in enumerate(rows)]

    return [(o["project_id"], n["project_id"], how.split(" (")[0]) for o, n, how in diff_filings.link_filings(recs(old), recs(new))]


def _kv(name: str, description: str) -> str:
    return checks.kv_title_matches_description({"name": name, "description": description}, {})[0]


CAINHOY_DESC = "Construct a 115 kV tap from Cainhoy to Clements Ferry. Approximately 2.8 miles. Construct terminal at Cainhoy."
JACK_PRIMUS_DESC = "Construct a 115 kV tap from Jack Primus to Clements Ferry. Approximately 2.2 miles. Construct terminals at Jack Primus."


COST_SENTENCE_PAGE = "\n".join([
    "Project 18 of 54", "Fairfax-Yemassee 115kV: Upgrade for DESCSQ #1151 Interconnection", "Project ID", "6238 H",
    "Project Description", "Upgrade 336 ACSR portion of DESCSQ #1151 - Yemassee 115 kV line to 1272 ACSR.", "Project Need",
    "This project is needed to interconnect DESCSQ #1151.", "Project Status", "Planned", "Planned In-Service Date", "9/7/2027",
    "Estimated Project Cost", "Estimated cost of $20,350,000 is to be financed by the interconnection customer. DESC will reimburse",
    "the full cost of the assigned upgrade to the interconnection customer after commercial operation of", "DESCSQ #1151.",
])


CASES = [
    # dates: 2-digit years, 4-digit years (a regex once read '6/1/2033' as 2020), Excel serials, phased dates
    ("date 12/31/23", lambda: N.parse_date("12/31/23")[0], "2023-12-31"),
    ("date 6/1/2033", lambda: N.parse_date("6/1/2033")[0], "2033-06-01"),
    ("date Excel 45809", lambda: N.parse_date("45809")[0], "2025-06-01"),
    ("date Excel 45778", lambda: N.parse_date("45778")[0], "2025-05-01"),
    ("date two phases", lambda: N.parse_date("10/1/2025 (phase 1) and 10/1/2026 (phase 2)")[0], "2026-10-01"),
    # voltages, including the filing's '23O KV' typo
    ("kV typo 23O", lambda: N.kv_of("THALMANN AND COLERAIN 23O KV LINE RELAY PANEL UPGRADES")[0], [230]),
    ("kV pair", lambda: N.kv_of("Okatie 230-115kV Substation, Jasper – Yemassee 230kV #1 Fold-in")[0], [230, 115]),
    ("kV drops distribution", lambda: N.kv_of("Union Pier 115-13.8 kV Sub: Tap")[0], [115]),
    # a letter O inside a voltage pair isn't repaired, so the check flags it (faults.py slipped '23O-115kV' past every check)
    ("kV typo inside a pair flagged", lambda: checks.voltage_found(
        {"name": "Summerville: Replace and Spare 23O-115kV 336MVA Auto Bank", "kv": [115], "_kv_notes": []}, {})[0], "warn"),
    ("kV typo 50O/230KV flagged", lambda: checks.voltage_found(
        {"name": "BOWEN #10 50O/230KV AUTOBANK REPLACEMENT", "kv": [230], "_kv_notes": []}, {})[0], "warn"),
    ("repaired kV typo not flagged twice", lambda: checks.voltage_found(
        {"name": "THALMANN AND COLERAIN 23O KV LINE RELAY PANEL UPGRADES", "kv": [230], "_kv_notes": []}, {}), ("pass", "230 kV")),
    # endpoint names, the five Sperry projects' titles among them
    ("ends GOSHEN (SAV)", lambda: _keys("SAV: GOSHEN (SAV) - MCINTOSH 115KV LINE REBUILD"), ["GOSHEN", "MCINTOSH"]),
    ("ends THURMOND DAM (USA) #5", lambda: _keys("EVANS PRIMARY - THURMOND DAM (USA) #5 115KV REBUILD"), ["EVANS PRIMARY", "THURMOND DAM"]),
    ("ends slash clause", lambda: _keys("Stevens Creek - Hooks 115kV/LR Plumb Branch 46kV Rebuilds"), ["STEVENS CREEK", "HOOKS"]),
    ("ends en dash + #2", lambda: _keys("Jasper – Okatie 230 kV #2: Construct"), ["JASPER", "OKATIE"]),
    ("ends & clause", lambda: _keys("Queensboro - Ft Johnson 115 kV & Queensboro-Bayfront 115kV (Queensboro-James Island Sect)"), ["QUEENSBORO", "FORT JOHNSON"]),
    ("ends place starting with a work word", lambda: _keys("GTC: BANKS CROSSING - POND FORK 115 KV"), ["BANKS CROSSING", "POND FORK"]),
    ("ends substation work", lambda: _keys("NORCROSS 230KV BUS 1-3 SERIES BUS TIE BREAKER INSTALLATION"), ["NORCROSS"]),
    ("ends customer only", lambda: _keys("CC - PROJECT CHRONOS- SK/HYUNDAI"), []),
    ("ends GRID prefix", lambda: _keys("GRID - ARKWRIGHT - LLOYD SHOALS 115KV"), ["ARKWRIGHT", "LLOYD SHOALS"]),
    # extraction details
    ("wrapped voltage pair", lambda: extract_ga.join_wrapped(["SAV: LITTLE OGEECHEE 230-", "115KV: RELAY MODERNIZATION"]),
     "SAV: LITTLE OGEECHEE 230-115KV: RELAY MODERNIZATION"),
    ("malformed $19,00,181 repaired from the Total", lambda: extract_desc._costs(
        ["Previous 2024 2025 2026 2027 2028 Total*", "$4,337,401 $19,00,181 $11,489,845 $50,000 $0 $0 $34,877,427"])[0]["2024"], 19000181),
    # matching keys
    ("core drops plant words", lambda: locate.core(N.endpoint_key("Stevens Creek Power Plant")), "STEVENS CREEK"),
    ("core folds -borough", lambda: locate.core(N.endpoint_key("Queensborough Substation")), locate.core(N.endpoint_key("Queensboro"))),
    ("key Rd -> Road", lambda: N.endpoint_key("Jefferson Rd Substation"), "JEFFERSON ROAD"),
    ("no fuzzy for short names", lambda: locate.max_edits("GRADY"), 0),
    ("1 typo for mid names", lambda: locate.max_edits("ADAMSVILLE"), 1),
    # Nominatim road hits with nothing to anchor them (GA-13166 once landed on a First Avenue near Atlanta)
    ("unanchored 'First Avenue' road rejected", lambda: locate.fits(
        {"key": "FIRST AVENUE", "lat": 33.8516, "lon": -84.2193, "_road": True, "_snapped": True}, {}, None, (32.49, -84.95)), False),
    ("road far from its zone rejected", lambda: locate.fits(
        {"key": "WYNNTON ROAD", "lat": 33.8516, "lon": -84.2193, "_road": True, "_snapped": True}, {}, None, (32.49, -84.95)), False),
    ("named road beside a substation kept", lambda: locate.fits(
        {"key": "WILLIAMS STREET", "lat": 33.988, "lon": -81.038, "_road": True, "_snapped": True}, {}, None, None), True),
    # build windows: a start the filing doesn't pin is flagged, so the engine's build-window setting applies
    ("DESC 'Previous' spend window is assumed", lambda: N.build_window_desc("2024-06-01", {"Previous": 5, "2024": 1})["assumed"], True),
    ("DESC spend-year window is filed", lambda: N.build_window_desc("2026-06-01", {"Previous": 0, "2025": 1})["assumed"], False),
    # the window is the years carrying most of the money: $50K of $5.38M in 2027 does not open it (2026-2030 p41)
    ("a token first-year amount doesn't open the window (p41)",
     lambda: N.build_window_desc("2028-12-31", {"Previous": 0, "2026": 0, "2027": 50000, "2028": 5326418})["start"], "2028-01-01"),
    # most of the money after the filed in-service date: the window follows it and the check flags it (p26)
    ("money after in-service: the window follows it (p26)",
     lambda: N.build_window_desc("2027-12-31", {"Previous": 0, "2027": 100000, "2028": 2900000})["end"], "2028-12-31"),
    ("money after in-service is flagged (p26)",
     lambda: checks.spend_after_in_service_rule({"utility": "DESC", "in_service": "2027-12-31", "in_service_raw": "12/31/2027",
                                                  "cost_by_year": {"2027": 100000, "2028": 2900000}}, {})[0], "warn"),
    # DESC's 2026-2030 filing (diff_filings.py): impossible calendar dates and one id printed for two projects
    ("impossible date 04/31/26 set aside (2026-2030 p1)", lambda: checks.date_real({"in_service_raw": "04/31/26"}, {}),
     ("fail", "'04/31/26' is not a real date: April 2026 has 30 days (as printed in the filing; which date was meant can't be told from it)")),
    ("impossible date 06/31/2026 set aside (2026-2030 p5)", lambda: checks.date_real({"in_service_raw": "06/31/2026"}, {})[0], "fail"),
    ("a real date passes date_real", lambda: checks.date_real({"in_service_raw": "12/31/2028"}, {})[0], "pass"),
    ("an impossible earlier phase: date_valid reads only the last date",
     lambda: _phases_date_valid("04/31/25 (phase 1) and 10/1/2026 (phase 2)"), "pass"),
    ("an impossible earlier phase: date_real catches it",
     lambda: checks.date_real({"in_service_raw": "04/31/25 (phase 1) and 10/1/2026 (phase 2)"}, {})[0], "fail"),
    ("one id for two projects (6809 M, 2026-2030 p19 / p48): both set aside",
     lambda: _ids(("6809 M", "St George - Sumter 230kV Tie: Rebuild Line from Santee Substation - Duke/Progress Energy Tie", "f"),
                  ("6809 M", "Modoc – McCormick 115/46 kV Rebuild", "f")), ["fail", "fail"]),
    ("one project listed twice is id_unique's case, not this one",
     lambda: _ids(("6809 M", "Modoc – McCormick 115/46 kV Rebuild", "f"), ("6809 M", "Modoc - McCormick 115/46kV Rebuild", "f")), ["pass", "pass"]),
    ("the same id in two filings is not a reuse",
     lambda: _ids(("6809 M", "St George - Sumter 230kV Tie", "desc"), ("6809 M", "Modoc – McCormick 115/46 kV Rebuild", "desc_2026")), ["pass", "pass"]),
    ("work order inside another id's range flagged (6367 D / 06367 D - G)",
     lambda: _ids(("6367 D", "Riverport 115kV Tap: Construct Tap", "f"), ("06367 D - G", "Jasper – Okatie 230 kV #2: Construct", "f")), ["warn", "warn"]),
    ("id key 6853 B-F = 6853BF, 06810 H = 6810 H", lambda: (N.project_id_key("6853 B-F"), N.project_id_key("06810 H") == N.project_id_key("6810 H")),
     ("6853BF", True)),
    ("id work orders 6847 A-B, D-H", lambda: sorted(N.project_id_parts("6847 A-B, D-H")), ["6847A", "6847B", "6847D", "6847E", "6847F", "6847G", "6847H"]),
    ("amount printed '0' without a $ (2026-2030 p19)", lambda: (lambda r: (r[0]["2028"], r[1], [a["id"] for a in r[4]]))(extract_desc._costs(
        ["Previous 2026 2027 2028 2029 2030 Total", "$219,331 $50,000 $4,300,000 0 $0 $0 $4,569,331"])), (0, 4569331, ["cost_amount_no_dollar_sign"])),
    ("amount printed '25,000' without a $ (2026-2030 p54)", lambda: extract_desc._costs(
        ["Previous 2026 2027 2028 2029 2030 Total", "25,000 $0 $0 $0 $850,000 $18,000,000 $18,875,000"])[0]["Previous"], 25000),
    ("cost stated as a sentence (2026-2030 p18)", lambda: (lambda r: (r["cost_total"], r["cost_by_year"], r["parse_errors"]))(
        extract_desc.parse_page(COST_SENTENCE_PAGE, 18)), (20350000, None, [])),
    ("amount '$00' read as 0 and recorded (2026-2030 p53)", lambda: (lambda r: (r[0]["2028"], r[1], [a["id"] for a in r[4]]))(extract_desc._costs(
        ["Previous 2026 2027 2028 2029 2030 Total", "$0 $0 $0 $00 $250,000 $9,500,000 $9,750,000"])), (0, 9750000, ["cost_amount_malformed"])),
    # linking the two DESC editions (diff_filings.link_filings)
    ("same description, new id and title: Cainhoy '0147 C, K' -> '0147 A-I' (2024-2028 p37 / 2026-2030 p53)",
     lambda: _link([("0147 C, K", "Cainhoy 115 kV Tap: Construct", CAINHOY_DESC), ("0147 B, J", "Jack Primus 115 kV Tap: Construct", JACK_PRIMUS_DESC)],
                   [("0147 A-I", "Clements Ferry Rd Sub: 115kV Tap from Cainhoy", CAINHOY_DESC)]),
     [("0147 C, K", "0147 A-I", "same description, new id and title")]),
    ("the same description under an unrelated id is not linked",
     lambda: _link([("0147 C, K", "Cainhoy 115 kV Tap: Construct", CAINHOY_DESC)], [("9999 A", "Clements Ferry Rd Sub: 115kV Tap from Cainhoy", CAINHOY_DESC)]), []),
    ("same title, new id: Riverport '06367 A - C, H' -> '6367 D' (same base number)",
     lambda: _link([("06367 A - C, H", "Riverport Tap: Construct Tap", "Construct Okatie – Riverport 230 kV to feed new Distribution substation.")],
                   [("6367 D", "Riverport 115kV Tap: Construct Tap", "Constructing a 230 kV Tap from Okatie to Riverport")]),
     [("06367 A - C, H", "6367 D", "same title, new id")]),
    ("look-alike titles, unrelated ids, no place in common: not linked ('Scout' / 'Stout')",
     lambda: _link([("6853 B-F", "Scout 230 kV Sub and Fold-in: Construct", "a")], [("7001", "Stout 230 kV Sub and Fold-in: Construct", "b")]), []),
    # the title's voltage against the description's (2026-2030 p13, p44)
    ("title 115kV, description a 230 kV tap: warned (2026-2030 p13)",
     lambda: _kv("Riverport 115kV Tap: Construct Tap", "Constructing a 230 kV Tap from Okatie to Riverport"), "warn"),
    ("title 230/115KV, description 'No 230kV work': warned (2026-2030 p44)",
     lambda: _kv("Canadys-Ritter 115KV: Rebuild SPDC 230/115KV 1272 (Approx 18 Miles)",
                 "Rebuild Canadys-Ritter 115 kV line as SPDC with 1272 ACSR. Project includes the rebuild of the Canadys - Ritter 115kV only. "
                 "No 230kV work associated with this project."), "warn"),
    ("title and description agree on voltage: pass", lambda: _kv("VCS2-Ward 230kV: Rebuild Line", "Rebuild the VCS2-Ward 230 kV line."), "pass"),
    ("a tap's other end from the title ('Tap from Cainhoy', 2026-2030 p53)",
     lambda: _keys("Clements Ferry Rd Sub: 115kV Tap from Cainhoy"), ["CLEMENTS FERRY ROAD", "CAINHOY"]),
]


def run() -> list[dict]:
    out = []
    for label, fn, expected in CASES:
        try:
            got = fn()
        except Exception as e:  # a crash is a failure with its message
            got = f"{type(e).__name__}: {e}"
        out.append({"case": label, "ok": got == expected, "expected": expected, "got": got})
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    res = run()
    for r in res:
        print(("ok  " if r["ok"] else "FAIL") + f"  {r['case']}" + ("" if r["ok"] else f"  expected {r['expected']!r} got {r['got']!r}"))
    print(f"{sum(r['ok'] for r in res)}/{len(res)} passed")
    sys.exit(0 if all(r["ok"] for r in res) else 1)
