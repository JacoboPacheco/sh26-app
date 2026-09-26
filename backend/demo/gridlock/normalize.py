"""Stage 2, normalize: turn filed text into typed fields, keeping the raw value next to every result and a
note whenever something had to be interpreted.

    parse_date("12/31/23")          -> ("2023-12-31", None)
    parse_date("45809")             -> ("2025-06-01", "Excel serial date 45809")
    kv_of("THALMANN AND COLERAIN 23O KV LINE RELAY PANEL UPGRADES") -> ([230], ["voltage typo '23O KV' read as 230 kV"])
    endpoints_of("SAV: GOSHEN (SAV) - MCINTOSH 115KV LINE REBUILD")  -> two endpoints, GOSHEN and MCINTOSH
    kind_of(name, description, n_endpoints)  -> new_line | line_rebuild | reconductor | substation | tap | other
    build_window(...)               -> {start, end, basis, assumed[, latest_start]}
"""

from __future__ import annotations

import re
from datetime import date, timedelta

# --------------------------------------------------------------------------- dates

EXCEL_EPOCH = date(1899, 12, 30)  # Excel's day 0 (it counts the 1900 leap-year bug, so day 60 = 1900-02-29)
MDY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")  # 4-digit years first: '6/1/2033' is not '6/1/20'


def parse_date(raw) -> tuple[str | None, str | None]:
    """(ISO date or None, note or None). Handles m/d/yy, m/d/yyyy, Excel serial numbers, and several dates
    in one field ('10/1/2025 (phase 1) and 10/1/2026 (phase 2)' -> the last one, with a note)."""
    if raw is None:
        return None, "no date"
    s = str(raw).strip()
    if re.fullmatch(r"\d{5}(\.0+)?", s):
        n = int(float(s))
        if 30000 <= n <= 60000:  # 1982..2064
            return (EXCEL_EPOCH + timedelta(days=n)).isoformat(), f"Excel serial date {n}"
        return None, f"number {s} is not a plausible date"
    found = MDY.findall(s)
    if not found:
        return None, f"no date in '{s}'"
    note = None
    if len(found) > 1:
        note = f"{len(found)} dates in the field ('{s}'); used the last (final phase)"
    m, d, y = found[-1]
    yy = int(y)
    if len(y) == 2:
        yy += 2000
        note = note or f"two-digit year '{y}' read as {yy}"
    try:
        return date(yy, int(m), int(d)).isoformat(), note
    except ValueError:
        return None, f"'{s}' is not a valid calendar date"


def days_between(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def add_months(iso: str, months: int) -> str:
    d = date.fromisoformat(iso)
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28)).isoformat()


# --------------------------------------------------------------------------- voltage

KV = re.compile(r"((?:\d{2,3}(?:\.\d+)?\s*[-/]\s*)*\d{2,3}(?:\.\d+)?)\s*-?\s*KV\b", re.I)


def fix_voltage_typos(text: str) -> tuple[str, list[str]]:
    """'23O KV' (letter O for zero) -> '230 KV'."""
    notes = []

    def sub(m):
        notes.append(f"voltage typo '{m.group(0).strip()} KV' read as {m.group(1)}0 kV")
        return f"{m.group(1)}0 "  # the lookahead leaves the KV in place

    return re.sub(r"\b(\d{2})[Oo]\s*(?=KV\b)", sub, text, flags=re.I), notes


def kv_of(*texts: str | None) -> tuple[list[int], list[str]]:
    """Distinct transmission voltages (>= 34 kV) named in the texts, highest first."""
    out: set[int] = set()
    notes: list[str] = []
    for t in texts:
        if not t:
            continue
        t, n = fix_voltage_typos(t)
        notes += n
        for m in KV.finditer(t):
            for part in re.split(r"\s*[-/]\s*", m.group(1)):
                v = float(part)
                if v >= 34:
                    out.add(int(round(v)))
        if out:  # the name is authoritative; the description only fills in when the name has none
            break
    return sorted(out, reverse=True), notes


# --------------------------------------------------------------------------- endpoints

PREFIX = re.compile(r"^\s*(SAV|GTC|MEAG|GPC|DU|CC|GRID)\s*[:\-]\s*", re.I)
# words that start the "what is being done" part of a name; the place name is everything before
WORK_WORDS = {
    "KV", "SUB", "SUBS", "SUBSTATION", "SUBSTATIONS", "TAP", "TAPS", "LINE", "LINES", "REBUILD", "REBUILDS", "REBLD", "RECONDUCTOR",
    "CONSTRUCT", "CONSTRUCTION", "NEW", "ADD", "INSTALL", "INSTALLATION", "REPLACE", "REPLACEMENT", "UPGRADE", "UPGRADES",
    "UPRATE", "EXPANSION", "EXPAND", "BUS", "BUS-TIE", "BREAKER", "BREAKERS", "CAPACITOR", "CAP", "BANK", "BANKS",
    "AUTOBANK", "AUTOBANKS", "TRANSFORMER", "TRANSFORMERS", "REACTOR", "REACTORS", "RELAY", "RELAYS", "STATCOM", "SVC",
    "SWITCH", "SWITCHES", "SERIES", "SECOND", "THIRD", "2ND", "3RD", "PROJECT", "PROJECTS", "MODERNIZATION", "IMPROVEMENTS",
    "IMPROVEMENT", "IMPROVMNT", "JUMPER", "PARALLEL", "PHASE", "TRANSMISSION", "FOLD-IN", "LOOP", "TERMINAL",
    "TERMINALS", "PARTIAL", "CONVERSION", "RETIREMENT", "AKA.", "AKA", "SPDC", "RATING", "TIE", "EQUIPMENT", "NEEDS",
    "DISTRIBUTION", "REPLACE,", "SPARE", "AREA", "STRATEGIC", "CUSTOMER", "NETWORK", "STATION", "XFMR", "REMOVAL", "BUILD",
}
WORK_BIGRAMS = {("LOW", "SIDE"), ("HIGH", "SIDE"), ("DUAL", "STAGE"), ("SMART", "VALVE"), ("LIMITING", "ELEMENT"), ("AREA", "SOLUTION")}
STANDARD_KV = {"34", "44", "46", "69", "100", "115", "138", "161", "230", "345", "500"}
SPLIT = re.compile(r"\s+[-–—]\s*|\s*[–—]\s*|(?<=[A-Za-z0-9)])-(?=[A-Za-z])|(?<=[A-Za-z])-\s+|\s+AND\s+|\s*&\s*|\s+TO\s+|\s*,\s*", re.I)
QUALIFIER = re.compile(r"\s*\((?:[^)]*)\)|\s*\([^)]*$|\s*#\s*\d+\w*")
# names in the filings that are customers or programs, not places (kept in the record, never geocoded)
CUSTOMER = re.compile(r"^(PROJECT\b|SK\b|HYUNDAI\b|QCELLS\b|SMART VALVES?\b|MICROSOFT\b|QTS\b|TA REALTY\b|FLEXENTIAL\b|EMBLEM\b|NORTH GEORGIA DATA\b)", re.I)
ABBREV = {"V RICA": "VILLA RICA", "N DUBLIN": "NORTH DUBLIN"}


def _cut_at_work(clause: str) -> str:
    """The place part of a piece: everything before the first voltage or work word (never cut at word 0,
    so places like 'Banks Crossing', 'Line Creek', 'Switch Way', 'New Lacy' survive)."""
    toks = clause.split()
    for i, tok in enumerate(toks):
        if i == 0:
            continue
        up = tok.upper().rstrip(",:;")
        nxt = toks[i + 1].upper() if i + 1 < len(toks) else ""
        if re.fullmatch(r"\d{2,3}(\.\d+)?KV\S*", up) or re.match(r"^\d{2,3}(\.\d+)?[-/]\d", up):
            return " ".join(toks[:i])
        if re.fullmatch(r"\d+(\.\d+)?", up) and (nxt.startswith("KV") or up in STANDARD_KV):
            return " ".join(toks[:i])
        if up in WORK_WORDS or (up, nxt) in WORK_BIGRAMS:
            return " ".join(toks[:i])
    return clause


def endpoint_key(name: str) -> str:
    """Matching key for a place name: upper case, qualifiers '(USA)', '#5' removed, abbreviations expanded."""
    s = QUALIFIER.sub("", name).upper()
    s = re.sub(r"[.'’]", "", s)
    s = re.sub(r"\b(SUBSTATION|SUB|SWITCHING STATION|SWITCHYARD)\b", " ", s)
    s = re.sub(r"\bPRI\b", "PRIMARY", s)
    s = re.sub(r"\s+", " ", s).strip()  # before the end-anchored rules below ('JEFFERSON RD ' -> 'JEFFERSON ROAD')
    s = re.sub(r"^ST\b(?=\s+[A-Z])", "SAINT", s)
    s = re.sub(r"\bST$", "STREET", s)
    s = re.sub(r"\bRD$", "ROAD", s)
    s = re.sub(r"\bFT\b", "FORT", s)
    s = re.sub(r"\bMT\b", "MOUNT", s)
    s = re.sub(r"\bJCT\b", "JUNCTION", s)
    s = re.sub(r"^N\b", "NORTH", s)
    s = re.sub(r"^S\b", "SOUTH", s)
    s = re.sub(r"^E\b", "EAST", s)
    s = re.sub(r"^W\b", "WEST", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return ABBREV.get(s, s)


def display_name(key_src: str) -> str:
    s = QUALIFIER.sub("", key_src).strip(" -:,")
    s = re.sub(r"\s+", " ", s)
    if s.isupper():
        keep = {"SKC", "CIP", "VCS1", "VCS2", "PSA", "USA", "DEP", "APC", "LGE"}
        s = " ".join(w if w in keep else w.capitalize() for w in s.split())
        s = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), s)
    return s


def endpoints_of(name: str) -> dict:
    """Place names in a project name. Returns {endpoints: [{name, raw, key}], via: [...], prefixes: [...],
    notes: [...], customer: [...]}. At most two endpoints: the first and the last place of the first clause."""
    notes: list[str] = []
    s, typo_notes = fix_voltage_typos(name)
    notes += typo_notes
    s = s.replace("LG&E", "LGE")
    prefixes = []
    while True:
        m = PREFIX.match(s)
        if not m:
            break
        prefixes.append(m.group(1).upper())
        s = s[m.end():]
    # the first clause: before ':' (a verb follows), ',' / ' / ' (another scope item follows)
    first = re.split(r":|,|\s/\s|(?<=[A-Za-z0-9])/(?=[A-Za-z])|\s&\s#|\s+and\s+(?=[A-Z][a-z]+ [A-Z])", s, maxsplit=1)[0]
    if re.match(r"^\s*[A-Z ]+ TRANSMISSION\s*$", first, re.I) and ":" in s:
        notes.append(f"'{first.strip()}' names an area; used the places listed after it")
        first = s.split(":", 1)[1]
    first = QUALIFIER.sub(lambda m: m.group(0) if m.group(0).lstrip().startswith("#") else "", first)
    customers: list[str] = []
    places = []
    for p in (p for p in SPLIT.split(first) if p and p.strip()):
        piece = p.strip().strip(" -:,")
        if CUSTOMER.match(piece):
            customers.append(piece)
            continue
        cut = _cut_at_work(piece).strip(" -:,")
        if not cut:
            continue
        places.append((cut, cut))  # raw = the place part as filed (qualifiers like '#5' kept, work words dropped)
        if cut != piece:  # the work part started inside this piece: what follows describes the work
            break
    if customers:
        notes.append("names a customer or program, not a place: " + ", ".join(f"'{c}'" for c in customers))
    places = [(c, r) for c, r in places if endpoint_key(c)]
    eps = [{"name": display_name(c), "raw": r, "key": endpoint_key(c)} for c, r in places]
    via = []
    if len(eps) > 2:
        via = [e["name"] for e in eps[1:-1]]
        notes.append(f"{len(eps)} places named; used the first and last as endpoints, {', '.join(via)} as via")
        eps = [eps[0], eps[-1]]
    if len(eps) == 2 and eps[0]["key"] == eps[1]["key"]:
        eps = eps[:1]
    return {"endpoints": eps, "via": via, "prefixes": prefixes, "notes": notes, "customer": customers}


# --------------------------------------------------------------------------- kind

def kind_of(name: str, description: str | None, n_endpoints: int) -> tuple[str, str]:
    """(kind, the word that decided it)."""
    up = name.upper()
    desc = (description or "").upper()
    rules = [
        ("reconductor", r"RECONDUCTOR"),
        ("new_line", r"NEW (?:\d+ ?KV )?LINE|\(SECOND LINE\)|#2:? CONSTRUCT|ADD \d+ ?KV LINE|CONSTRUCT NEW"),
        ("tap", r"\bTAP\b|FOLD-IN|\bLOOP\b"),
        ("line_rebuild", r"REBUILD|REBLD|UPGRADE TO SPDC|LINE CROSSINGS"),
        ("substation", r"SUBSTATION|\bSUB\b|\bBANK\b|TRANSFORMER|AUTOBANK|BREAKER|\bBUS\b|CAPACITOR|STATCOM|REACTOR|RELAY|\bSWITCH\b|\bSVC\b|SMART VALVE|MODERNIZATION|XFMR"),
    ]
    for kind, pat in rules:
        m = re.search(pat, up)
        if m:
            if kind == "line_rebuild" and n_endpoints < 2 and "LINE" not in up:
                continue
            return kind, m.group(0)
    # the name has no verb: the description's first work verb decides
    desc_rules = [
        ("new_line", r"\bBUILD(?:S)? (?:A )?NEW\b|\bNEW [\d.]+ ?(?:MILE|MI)|\bBEING BUILT\b|\bCONSTRUCT"),
        ("reconductor", r"RECONDUCTOR"),
        ("line_rebuild", r"\bREBUILD"),
        ("tap", r"\bLOOP IN\b|\bTAP\b"),
        ("substation", r"TRANSFORMER|BREAKER|\bBANK\b|CAPACITOR|REACTOR|RELAY|\bBUS\b|SUBSTATION"),
    ]
    hits = [(m.start(), kind, m.group(0)) for kind, pat in desc_rules for m in [re.search(pat, desc)] if m]
    if hits and n_endpoints >= 1:
        _, kind, word = min(hits)
        if kind in ("new_line", "line_rebuild", "reconductor") and n_endpoints < 2 and "LINE" not in desc:
            kind = "substation"
        return kind, f"description: {word.lower()}"
    if n_endpoints == 2:
        return ("line_rebuild" if "KV" in up else "other"), "two places named"
    return "other", "no work verb found"


# --------------------------------------------------------------------------- miles

MILES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:miles?|mi\b)", re.I)


def miles_of(*texts: str | None) -> tuple[float | None, str | None]:
    best, snippet = None, None
    for t in texts:
        if not t:
            continue
        for m in MILES.finditer(t):
            v = float(m.group(1))
            if v > 0 and (best is None or v > best):
                best, snippet = v, t[max(0, m.start() - 40) : m.end() + 10].strip()
        if best is not None:
            break
    return best, snippet


# --------------------------------------------------------------------------- build windows

ASSUMED_LEAD_MONTHS = 24


def build_window_desc(in_service: str | None, cost_by_year: dict | None) -> dict | None:
    """DESC files money per year: construction runs from the first year with spend to the in-service date.

    `assumed` is True when the filing doesn't pin the start (the engine then re-derives it from its build-window
    setting); `latest_start` is the latest start the filing still allows ('Previous' spend = started before 2024)."""
    if not in_service:
        return None
    years = sorted(int(k) for k, v in (cost_by_year or {}).items() if k.isdigit() and v and v > 0)
    prev = (cost_by_year or {}).get("Previous") or 0
    latest = None
    if prev > 0:
        first_col = min((int(k) for k in (cost_by_year or {}) if k.isdigit()), default=2024)
        latest = f"{first_col - 1}-01-01"
        start = min(add_months(in_service, -ASSUMED_LEAD_MONTHS), latest)
        basis = (f"spending began before {first_col} (the 'Previous' column), so the exact start isn't filed; assumed the earlier "
                 f"of {latest} and {ASSUMED_LEAD_MONTHS} months before in-service")
        assumed = True
    elif years:
        start = f"{years[0]}-01-01"
        basis = f"first year with budgeted spend ({years[0]}) to the in-service date"
        assumed = False
    else:
        start = add_months(in_service, -ASSUMED_LEAD_MONTHS)
        basis = f"no spend profile; assumed {ASSUMED_LEAD_MONTHS} months before the in-service date"
        assumed = True
    if start > in_service:
        start = add_months(in_service, -12)
        basis += "; spend continues past in-service, start set 12 months before in-service"
        assumed, latest = False, None
    out = {"start": start, "end": in_service, "basis": basis, "assumed": assumed}
    if latest:
        out["latest_start"] = latest
    return out


def build_window_ga(need: str | None, start_raw: str | None, detail_page: int | None) -> dict | None:
    if not need:
        return None
    start, _ = parse_date(start_raw) if start_raw else (None, None)
    if start and start <= need:
        return {"start": start, "end": need, "basis": f"start date filed on the project's detail page (PDF p{detail_page})", "assumed": False}
    return {"start": add_months(need, -ASSUMED_LEAD_MONTHS), "end": need,
            "basis": f"no filed start date; assumed {ASSUMED_LEAD_MONTHS} months before the need date", "assumed": True}


def months_overlap(a: dict | None, b: dict | None) -> float:
    """Months two build windows share (0 when they don't touch)."""
    if not a or not b:
        return 0.0
    s = max(a["start"], b["start"])
    e = min(a["end"], b["end"])
    if s > e:
        return 0.0
    return round((date.fromisoformat(e) - date.fromisoformat(s)).days / 30.44, 1)
