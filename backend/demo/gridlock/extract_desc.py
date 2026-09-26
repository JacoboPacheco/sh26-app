"""Stage 1a, extract: Dominion Energy South Carolina's "Planned Transmission Projects $2M and above" PDF.

One project per page, always in the same order of labelled sections:

    Project N of 44 / <title, may wrap> / Project ID / Project Description / Project Need /
    Project Status / Planned In-Service Date / Estimated Project Cost (Previous, 2024 ... 2028, Total*)

Every record keeps its page number and the page's raw text, so any number in the app can be traced
back to the filing. Nothing is interpreted here (dates, kV, endpoints are normalize.py's job); fields
are copied as filed. A page that doesn't have a section comes back with that field missing and a
`parse_errors` entry, and checks.py decides what happens to it.

    backend/venv/Scripts/python backend/demo/gridlock/extract_desc.py [pdf]    (prints what it found)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SECTIONS = [
    ("project_id", "Project ID"),
    ("description", "Project Description"),
    ("need", "Project Need"),
    ("status", "Project Status"),
    ("in_service_raw", "Planned In-Service Date"),
    ("cost_block", "Estimated Project Cost"),
]
PAGE_HEADER = re.compile(r"^Project (\d+) of (\d+)$")
BOILERPLATE = {"Dominion Energy South Carolina", "Planned Transmission Projects $2M and above Total", "5 Year Budget"}
MONEY = re.compile(r"\$\s?([\d,]+)")
WELL_GROUPED = re.compile(r"^\d{1,3}(,\d{3})*$")


def _join(lines: list[str]) -> str:
    """Join wrapped lines; a line that ends mid-word with '-' keeps the hyphen (names like 'Hooks-Thurmond')."""
    return re.sub(r"\s+", " ", " ".join(s.strip() for s in lines)).strip()


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def _costs(lines: list[str]) -> tuple[dict | None, int | None, str | None, list[str], list[dict]]:
    """The cost block: a header row of labels (Previous, years, Total*) and a row of $ amounts.

    Returns (by_year, total, rate_base_note, parse_errors, anomalies). An amount with broken thousands
    grouping (the filing has '$19,00,181') is repaired from the Total only when exactly one amount is
    malformed and its digits fit inside the value the Total implies; the repair is recorded as an anomaly.
    Totals that don't equal the shown columns are kept as filed and reported, never "fixed".
    """
    anomalies: list[dict] = []
    header = next((ln for ln in lines if ln.startswith("Previous")), None)
    amounts_line = next((ln for ln in lines if ln.startswith("$")), None)
    rate_base = next((ln for ln in lines if "Rate Base" in ln), None)
    if not header or not amounts_line:
        return None, None, rate_base, ["cost table not found"], anomalies
    labels = [lab.rstrip("*") for lab in header.split()]
    raws = MONEY.findall(amounts_line)
    if len(labels) != len(raws):
        return None, None, rate_base, [f"cost table has {len(labels)} labels but {len(raws)} amounts"], anomalies
    amounts = [int(r.replace(",", "")) for r in raws]
    by = dict(zip(labels, amounts))
    total = by.pop("Total", None)
    bad = [(lab, raw) for lab, raw in zip(labels, raws) if not WELL_GROUPED.match(raw) and lab != "Total"]
    if total is not None and len(bad) == 1:
        lab, raw = bad[0]
        implied = total - sum(v for k, v in by.items() if k != lab)
        if implied > 0 and _is_subsequence(raw.replace(",", ""), str(implied)):
            by[lab] = implied
            anomalies.append({
                "id": "cost_amount_malformed",
                "detail": f"{lab} filed as '${raw}' (broken thousands grouping); read as ${implied:,}, the value the Total implies",
            })
        else:
            anomalies.append({"id": "cost_amount_malformed", "detail": f"{lab} filed as '${raw}'; could not be reconciled with the Total"})
    elif bad:
        anomalies.append({"id": "cost_amount_malformed", "detail": "malformed amounts: " + ", ".join(f"{lab} '${raw}'" for lab, raw in bad)})
    shown = sum(by.values())
    if total is not None and shown != total:
        anomalies.append({
            "id": "cost_total_mismatch",
            "detail": f"the columns shown add to ${shown:,} but the Total says ${total:,} "
            f"({'$' + format(total - shown, ',') + ' more' if total > shown else '$' + format(shown - total, ',') + ' less'}); kept the filed Total",
        })
    return by, total, rate_base, [], anomalies


def parse_page(text: str, page: int) -> dict:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    rec: dict = {"page": page, "text": text, "parse_errors": [], "anomalies": []}
    m = PAGE_HEADER.match(lines[0]) if lines else None
    if m:
        rec["seq"], rec["of"] = int(m.group(1)), int(m.group(2))
    else:
        rec["parse_errors"].append("page header 'Project N of M' not found")

    # positions of the labelled sections, in page order
    starts = []
    for key, label in SECTIONS:
        idx = next((i for i, ln in enumerate(lines) if ln == label), None)
        if idx is None:
            rec["parse_errors"].append(f"section '{label}' not found")
        else:
            starts.append((idx, key))
    starts.sort()

    first = starts[0][0] if starts else len(lines)
    rec["name"] = _join([ln for ln in lines[1:first] if ln not in BOILERPLATE])
    if not rec["name"]:
        rec["parse_errors"].append("title not found")
    for n, (idx, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        body = lines[idx + 1 : end]
        if key == "cost_block":
            by, total, rate_base, errs, anomalies = _costs(body)
            rec["cost_by_year"], rec["cost_total"], rec["rate_base_note"] = by, total, rate_base
            rec["parse_errors"] += errs
            rec["anomalies"] += anomalies
        else:
            rec[key] = _join(body) or None
            if rec[key] is None:
                rec["parse_errors"].append(f"section '{dict(SECTIONS)[key]}' is empty")
    return rec


def extract(pages_text: list[str]) -> list[dict]:
    """pages_text[i] is page i+1's text (pdf_text.py caches it)."""
    return [parse_page(t or "", i + 1) for i, t in enumerate(pages_text)]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).parent))
    from pdf_text import page_texts

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "sources" / "desc_2024_2028_projects.pdf"
    recs = extract(page_texts(src))
    for r in recs:
        flag = "  !! " + "; ".join(r["parse_errors"] + [a["detail"] for a in r["anomalies"]]) if r["parse_errors"] or r["anomalies"] else ""
        print(f"p{r['page']:>2} {r.get('project_id')!s:>10} | {r['name'][:70]:70} | {r.get('in_service_raw')} | ${r.get('cost_total') or 0:,}{flag}")
    print(f"{len(recs)} pages, {sum(1 for r in recs if not r['parse_errors'])} clean")
