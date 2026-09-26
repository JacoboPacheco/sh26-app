"""Stage 1a, extract: Dominion Energy South Carolina's "Planned Transmission Projects $2M and above" PDF.

One project per page, always in the same order of labelled sections:

    Project N of M / <title, may wrap> / Project ID / Project Description / Project Need /
    Project Status / Planned In-Service Date / Estimated Project Cost (Previous, five years, Total*)

Two editions are read the same way: 2024-2028 (44 pages, the build's source) and 2026-2030 (54 pages, see
FILINGS). The yearly columns come from each page's own header row. The 2026-2030 edition adds three printed forms:
an amount without its '$' ('0', '25,000'), read as dollars with an anomaly; an amount with a leading zero ('$00'),
read by its value with an anomaly; and a cost stated as a sentence ("Estimated cost of $20,350,000 is to be financed
by the interconnection customer ..."), kept in `cost_note`.

Every record keeps its page number and the page's raw text, so any number in the app can be traced
back to the filing. Nothing is interpreted here (dates, kV, endpoints are normalize.py's job); fields
are copied as filed. A page that doesn't have a section comes back with that field missing and a
`parse_errors` entry, and checks.py decides what happens to it.

    backend/venv/Scripts/python backend/demo/gridlock/extract_desc.py [desc | desc_2026 | pdf]   (prints what it found)

data/desc_2026_2030.json (filing_doc + the checked records) is written by diff_filings.py, the one command.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILINGS = {
    # the build's source (build.SOURCES["desc"] carries the same fields)
    "desc": {
        "id": "desc", "utility": "DESC", "years": "2024-2028",
        "title": "Dominion Energy South Carolina: Planned Transmission Projects $2M and above, 2024-2028 (project descriptions)",
        "file": "desc_2024_2028_projects.pdf",
        "url": "https://www.scrtp.com/assets/pdfs/home/2024-2028-2million-and-above-project-descriptions.pdf",
        "publisher": "South Carolina Regional Transmission Planning (SCRTP)",
    },
    "desc_2026": {
        "id": "desc_2026", "utility": "DESC", "years": "2026-2030",
        "title": "Dominion Energy South Carolina: Planned Transmission Projects $2M and above, 2026-2030 (project descriptions)",
        "file": "desc_2026_2030_projects.pdf",
        "url": "https://www.scrtp.com/assets/pdfs/home/2026-2030-2million-and-above-project-descriptions.pdf",
        "publisher": "South Carolina Regional Transmission Planning (SCRTP)",
    },
}
OUT_2026 = HERE / "data" / "desc_2026_2030.json"

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
LEADING_ZERO = re.compile(r"^0\d")


def _join(lines: list[str]) -> str:
    """Join wrapped lines; a line that ends mid-word with '-' keeps the hyphen (names like 'Hooks-Thurmond')."""
    return re.sub(r"\s+", " ", " ".join(s.strip() for s in lines)).strip()


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


LOOSE_AMOUNT = re.compile(r"(\$\s?)?(\d[\d,]*)")
# the 2026-2030 filing gives some costs as a sentence instead of a table (interconnection upgrades a customer pays for)
COST_SENTENCE = re.compile(r"Estimated cost of \$\s?([\d,]+)", re.I)


def _loose_amounts(header: str | None, lines: list[str], labels: list[str]) -> tuple[list[str] | None, list[dict]]:
    """The amounts row when one or more amounts are printed without a '$' (the 2026-2030 filing prints '0' and
    '25,000'): the line right after the header whose every token is a number, one per label. Each bare amount is
    read as dollars and recorded as an anomaly. None when that line doesn't have one number per label."""
    if header not in lines:
        return None, []
    i = lines.index(header)
    if i + 1 >= len(lines):
        return None, []
    line = lines[i + 1]
    toks = LOOSE_AMOUNT.findall(line)
    if len(toks) != len(labels) or LOOSE_AMOUNT.sub("", line).strip():
        return None, []
    bare = [(lab, num) for lab, (dollar, num) in zip(labels, toks) if not dollar]
    anomalies = [{"id": "cost_amount_no_dollar_sign",
                  "detail": "printed without a dollar sign, read as dollars: " + ", ".join(f"{lab} '{num}'" for lab, num in bare)}]
    return [num for _dollar, num in toks], anomalies


def _costs(lines: list[str]) -> tuple[dict | None, int | None, str | None, list[str], list[dict]]:
    """The cost block: a header row of labels (Previous, years, Total*) and a row of $ amounts.

    Returns (by_year, total, rate_base_note, parse_errors, anomalies). An amount with broken thousands
    grouping (the filing has '$19,00,181') is repaired from the Total only when exactly one amount is
    malformed and its digits fit inside the value the Total implies; the repair is recorded as an anomaly.
    Totals that don't equal the shown columns are kept as filed and reported, never "fixed".
    An amount printed without its '$' ('0', '25,000' in the 2026-2030 filing) is read as dollars, with an anomaly;
    this path runs only where the '$' row alone doesn't give one amount per label, so a page that parsed before
    parses exactly as before.
    """
    anomalies: list[dict] = []
    header = next((ln for ln in lines if ln.startswith("Previous")), None)
    amounts_line = next((ln for ln in lines if ln.startswith("$")), None)
    rate_base = next((ln for ln in lines if "Rate Base" in ln), None)
    labels = [lab.rstrip("*") for lab in header.split()] if header else []
    raws = MONEY.findall(amounts_line) if amounts_line else []
    if header and len(labels) != len(raws):
        loose, notes = _loose_amounts(header, lines, labels)
        if loose:
            raws, amounts_line = loose, "loose"
            anomalies += notes
    if not header or not amounts_line:
        return None, None, rate_base, ["cost table not found"], anomalies
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
    # a leading zero ('$00', 2026-2030 p53) is well grouped but not a way anyone writes an amount: read by its value, recorded
    lead0 = [(lab, raw) for lab, raw in zip(labels, raws) if LEADING_ZERO.match(raw.replace(",", ""))]
    if lead0:
        agrees = total is not None and shown == total
        anomalies.append({
            "id": "cost_amount_malformed",
            "detail": ", ".join(f"{lab} filed as '${raw}'" for lab, raw in lead0) + " (a leading zero); read as "
            + ", ".join(f"${int(raw.replace(',', '')):,}" for _lab, raw in lead0)
            + ("; with that, the columns add to the filed Total" if agrees else ""),
        })
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
            sentence = COST_SENTENCE.search(_join(body))
            if errs == ["cost table not found"] and sentence and not any(ln.startswith("Previous") for ln in body):
                # no yearly table: the filing states one estimate in words (who pays is part of the sentence)
                total, errs, rec["cost_note"] = int(sentence.group(1).replace(",", "")), [], _join(body)
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


# --------------------------------------------------------------------------- a second reader, and the written file


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("–", "-").replace("—", "-").replace(" ", " ")).strip()


def second_reader(path: Path, recs: list[dict]) -> dict:
    """Every record's id, title, in-service date and total cost looked up in the page text of a DIFFERENT PDF engine
    (pdfium, via pypdfium2; the parser reads pdfplumber's text). A field counts as found when its printed form is on
    the same page (the title word by word, in order, since titles wrap)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = [_flat(pdf[i].get_textpage().get_text_range()) for i in range(len(pdf))]
    finally:
        pdf.close()
    rows = []
    for r in recs:
        flat = pages[r["page"] - 1] if r["page"] - 1 < len(pages) else ""
        pos, title_ok = 0, bool(r.get("name"))
        for w in _flat(r.get("name")).split():
            i = flat.find(w, pos)
            if i < 0:
                title_ok = False
                break
            pos = i + len(w)
        total = r.get("cost_total")
        found = {
            "id": bool(r.get("project_id")) and _flat(r["project_id"]) in flat,
            "title": title_ok,
            "in_service": bool(r.get("in_service_raw")) and _flat(r["in_service_raw"]) in flat,
            "cost_total": total is not None and f"{total:,}" in flat,
        }
        rows.append({"page": r["page"], "project_id": r.get("project_id"), **found, "ok": all(found.values())})
    return {
        "engine": f"pdfium {pdfium.PDFIUM_INFO} via pypdfium2 {pdfium.PYPDFIUM_INFO} (the parser reads pdfplumber's text)",
        "fields": ["id", "title", "in_service", "cost_total"],
        "pages_checked": len(rows), "all_found": sum(r["ok"] for r in rows),
        "not_found": [r for r in rows if not r["ok"]],
    }


RECORD_FIELDS = ("seq", "of", "project_id", "name", "description", "need", "status", "in_service_raw", "cost_by_year",
                 "cost_total", "cost_note", "rate_base_note")


def filing_doc(key: str = "desc_2026", use_cache: bool = True) -> dict:
    """One DESC edition as extracted: a header naming the source (file, URL, SHA-256), and one record per page with its
    fields as printed, its page, its page text, and what the parser noticed (parse_errors, anomalies)."""
    sys.path.insert(0, str(HERE))
    from pdf_text import page_texts, sha256

    meta = FILINGS[key]
    path = HERE / "sources" / meta["file"]
    if not path.exists():
        raise SystemExit(f"missing source {path}\n  download it from {meta['url']} and save it there")
    texts = page_texts(path, use_cache)
    recs = extract(texts)
    records = []
    for r in recs:
        rec = {"provenance": {"source": meta["id"], "file": meta["file"], "page": r["page"]}}
        rec.update({k: r.get(k) for k in RECORD_FIELDS})
        rec.update(parse_errors=r["parse_errors"], anomalies=r["anomalies"], text=r["text"].strip())
        records.append(rec)
    cross = second_reader(path, recs)
    return {
        "_about": (f"{meta['title']}: every page as extracted by backend/demo/gridlock/extract_desc.py, fields copied as printed "
                   "(nothing interpreted), each with its PDF page. A public filing, read as published."),
        "source": {**meta, "pages": len(texts), "bytes": path.stat().st_size, "sha256": sha256(path)},
        "extraction": {
            "pages": len(texts), "projects": len(recs),
            "parsed_cleanly": sum(1 for r in recs if not r["parse_errors"]),
            "parse_errors": [{"page": r["page"], "errors": r["parse_errors"]} for r in recs if r["parse_errors"]],
            "anomalies": [{"page": r["page"], "project_id": r.get("project_id"), **a} for r in recs for a in r["anomalies"]],
            "cost_as_sentence": [r["page"] for r in recs if r.get("cost_note")],
            "second_reader": cross,
        },
        "records": records,
    }


def write_json_if_changed(path: Path, doc: dict, stamp_key: str = "built_at") -> bool:
    """Write doc (with a fresh timestamp) only when its content differs from the file's, so a rerun on the same
    inputs leaves the committed file byte for byte. Returns True when it wrote."""
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            old.pop(stamp_key, None)
            if old == json.loads(json.dumps(doc)):
                return False
        except (OSError, ValueError):
            pass
    out = {stamp_key: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **doc}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:  # LF on every OS, as the repo stores it
        f.write(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).parent))
    from pdf_text import page_texts

    arg = sys.argv[1] if len(sys.argv) > 1 else "desc"
    src = HERE / "sources" / FILINGS[arg]["file"] if arg in FILINGS else Path(arg)
    recs = extract(page_texts(src))
    for r in recs:
        flag = "  !! " + "; ".join(r["parse_errors"] + [a["detail"] for a in r["anomalies"]]) if r["parse_errors"] or r["anomalies"] else ""
        print(f"p{r['page']:>2} {r.get('project_id')!s:>10} | {r['name'][:70]:70} | {r.get('in_service_raw')} | ${r.get('cost_total') or 0:,}{flag}")
    print(f"{len(recs)} pages, {sum(1 for r in recs if not r['parse_errors'])} clean")
