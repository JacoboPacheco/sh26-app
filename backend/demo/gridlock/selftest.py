"""Stage 0, self-test: pinned cases for the parsers and matchers, taken from the real filings and from
Sperry's worked example. build.py stops before writing anything if one fails, so a change to a regex
can't quietly send bad records downstream.

    backend/venv/Scripts/python backend/demo/gridlock/selftest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_desc  # noqa: E402
import extract_ga  # noqa: E402
import locate  # noqa: E402
import normalize as N  # noqa: E402


def _keys(name: str) -> list[str]:
    return [e["key"] for e in N.endpoints_of(name)["endpoints"]]


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
