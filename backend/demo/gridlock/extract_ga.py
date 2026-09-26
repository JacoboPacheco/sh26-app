"""Stage 1b, extract: Georgia Power's 2025 IRP, Technical Appendix Volume 3 (Transmission Plan), the
redacted PUBLIC DISCLOSURE version filed with the Georgia PSC.

Two parts of the document are read:

  * "Table 2 Georgia ITS 10 Year Plan Project List" (PDF pages ~177-190): one row per project with
    Zone, Year, TEAMS number, Project Name (wrapped over several lines), Need Date, Sponsor
    (GPC, GTC, MEAG, DU, SAV) and five cost columns that are all REDACTED in the public version.
  * The per-project detail pages that follow (one per project): title, "Teams #", Need Date,
    Start Date, Description (often with miles and conductor), change notes.

Joining the wrapped names is the fragile part, so the table is parsed twice, independently:

  A. from the page text, line by line: a row starts with "zone year teams ... date sponsor REDACTED";
     every following line until the next row start is a continuation of the name;
  B. from word positions: row anchors are the 3-digit zone numbers in the left column, and every word
     inside the Project Name column between one anchor and the next belongs to that row.

extract() returns the rows with both readings and the matching detail page, and build.py reports how
many rows the two parsers and the detail titles agree on. Only unredacted fields are read; redacted
values are never inferred.

    backend/venv/Scripts/python backend/demo/gridlock/extract_ga.py [pdf]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TABLE_TITLE = "Table 2 Georgia ITS 10 Year Plan Project List"
ROW = re.compile(r"^(\d{3}) (\d{4}) (\d{4,6}) (.*?) ?(\d{1,2}/\d{1,2}/\d{4}) ([A-Z]{2,5})((?: REDACTED)+)$")
FOOTER = re.compile(r"GA ITS Ten-Year Plan \(\d{4}-\d{4}\) Page \d+ of \d+")
HEADER_LAST = "Number 2024 Sponsor MEAG DU"
DATE = r"(\d{1,2}/\d{1,2}/\d{4})"


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def join_wrapped(lines: list[str]) -> str:
    """Join a name wrapped over lines. A line ending in a hyphen glued to a digit ('230-' + '115KV') joins
    without a space (a voltage pair); every other break becomes one space."""
    out = ""
    for ln in (x.strip() for x in lines):
        if not ln:
            continue
        if out and re.search(r"\d-$", out) and re.match(r"\d", ln):
            out += ln
        else:
            out = f"{out} {ln}" if out else ln
    return _ws(out)


def canon(name: str) -> str:
    """Comparison key: case, whitespace and spacing around hyphens don't count."""
    return re.sub(r"\s*-\s*", "-", _ws(name).upper())


# --------------------------------------------------------------------------- the table, parser A (lines)


def find_table_pages(texts: list[str]) -> list[int]:
    start = next((i + 1 for i, t in enumerate(texts) if TABLE_TITLE in t and any(ROW.match(ln) for ln in t.split("\n"))), None)
    if start is None:
        return []
    pages = []
    for n in range(start, len(texts) + 1):
        t = texts[n - 1]
        if not any(ROW.match(ln) for ln in t.split("\n")):
            break
        pages.append(n)
        if re.search(r"^Total( REDACTED)+$", t, re.M):
            break
    return pages


def parse_table_lines(texts: list[str], pages: list[int]) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    notes: list[str] = []
    for n in pages:
        lines = texts[n - 1].split("\n")
        try:
            body_start = next(i for i, ln in enumerate(lines) if ln.strip() == HEADER_LAST) + 1
        except StopIteration:
            notes.append(f"p{n}: column header not found; read from the top")
            body_start = 0
        first_on_page = True
        for ln in lines[body_start:]:
            ln = ln.strip()
            if not ln or FOOTER.search(ln):
                continue
            if re.match(r"^Total( REDACTED)+$", ln):
                break
            m = ROW.match(ln)
            if m:
                zone, year, teams, name_part, need, sponsor, redacted = m.groups()
                rows.append({
                    "zone": zone, "year": int(year), "teams_no": teams, "need_date_raw": need, "sponsor": sponsor,
                    "name_lines": [name_part] if name_part else [], "page": n, "row_lines": [ln],
                    "redacted_columns": redacted.split().count("REDACTED"),
                })
            elif rows and not first_on_page:
                rows[-1]["name_lines"].append(ln)
                rows[-1]["row_lines"].append(ln)
            elif rows:
                notes.append(f"p{n}: '{ln}' before the first row; attached to the last row of the previous page")
                rows[-1]["name_lines"].append(ln)
                rows[-1]["row_lines"].append(ln)
            else:
                notes.append(f"p{n}: ignored '{ln}' (before the first row)")
            if m:
                first_on_page = False
    for r in rows:
        r["name"] = join_wrapped(r["name_lines"])
    return rows, notes


# --------------------------------------------------------------------------- the table, parser B (positions)


def parse_table_words(words_by_page: dict[int, list[dict]]) -> list[dict]:
    rows: list[dict] = []
    for n in sorted(words_by_page):
        words = words_by_page[n]

        def first(text: str, **kw) -> dict | None:
            return next((w for w in words if w["text"] == text and all(f(w) for f in kw.values())), None)

        number_h = first("Number")
        need_h = first("Need")
        zone_h = first("Zone")
        if not (number_h and need_h and zone_h):
            continue
        header_bottom = max(w["bottom"] for w in words if abs(w["top"] - number_h["top"]) < 1)
        footer = [w["top"] for w in words if w["text"] == "Ten-Year" and w["top"] > header_bottom]
        total = [w["top"] for w in words if w["text"] == "Total" and w["x0"] < need_h["x0"] and w["top"] > header_bottom]
        body_end = min(footer + total) if (footer or total) else 1e9
        body = [w for w in words if header_bottom < w["top"] < body_end - 0.5]
        anchors = sorted(
            (w for w in body if w["x1"] < zone_h["x1"] + 5 and re.fullmatch(r"\d{3}", w["text"])), key=lambda w: w["top"]
        )
        name_left, name_right = number_h["x1"] + 2, need_h["x0"] - 1
        for i, a in enumerate(anchors):
            top = a["top"] - 1.5
            bottom = anchors[i + 1]["top"] - 1.5 if i + 1 < len(anchors) else body_end
            band = [w for w in body if top <= w["top"] < bottom]
            same_line = [w for w in band if abs(w["top"] - a["top"]) < 1.5]
            mid = lambda w: (w["x0"] + w["x1"]) / 2  # noqa: E731  (a wide date starts left of its header)
            name_words = [w for w in band if w["x0"] >= name_left and mid(w) < name_right and not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", w["text"])]
            lines: dict[float, list[dict]] = {}
            for w in sorted(name_words, key=lambda w: (round(w["top"]), w["x0"])):
                key = next((k for k in lines if abs(k - w["top"]) < 1.5), w["top"])
                lines.setdefault(key, []).append(w)
            name_lines = [" ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"])) for _, ws in sorted(lines.items())]
            left = sorted((w for w in same_line if w["x1"] < name_left), key=lambda w: w["x0"])
            right = sorted((w for w in same_line if w not in name_words and mid(w) > name_right), key=lambda w: w["x0"])
            rows.append({
                "page": n,
                "zone": left[0]["text"] if len(left) > 0 else None,
                "year": left[1]["text"] if len(left) > 1 else None,
                "teams_no": left[2]["text"] if len(left) > 2 else None,
                "need_date_raw": right[0]["text"] if len(right) > 0 else None,
                "sponsor": right[1]["text"] if len(right) > 1 else None,
                "name": join_wrapped(name_lines),
            })
    return rows


# --------------------------------------------------------------------------- the detail pages


def parse_detail_page(text: str, page: int) -> dict | None:
    lines = [ln.strip() for ln in text.split("\n")]
    t_idx = next((i for i, ln in enumerate(lines) if ln.startswith("Teams #")), None)
    if t_idx is None:
        return None
    banner_end = next((i for i, ln in enumerate(lines[:t_idx]) if ln.endswith("employees.")), 0)
    title = _ws(" ".join(lines[banner_end + 1 : t_idx]))
    m = re.match(r"Teams #\s*(\d+)", lines[t_idx])
    rec: dict = {"page": page, "title": title, "teams_no": m.group(1) if m else None, "text": text}
    dates = next((ln for ln in lines[t_idx:] if ln.startswith("Need Date")), "")
    dm = re.match(rf"Need Date\s+{DATE}?\s*(?:Start Date\s+{DATE})?", dates)
    rec["need_date_raw"] = dm.group(1) if dm else None
    rec["start_date_raw"] = dm.group(2) if dm else None

    def section(label: str, until: tuple[str, ...]) -> str | None:
        try:
            i = lines.index(label)
        except ValueError:
            return None
        body = []
        for ln in lines[i + 1 :]:
            if ln.startswith(until) or FOOTER.search(ln):
                break
            body.append(ln)
        return _ws(" ".join(body)) or None

    rec["description"] = section("Description", ("Supporting Statement",))
    rec["change_ten_year"] = section("Change From Previous Ten Year Plan", ("Change From Previous IRP",))
    rec["change_irp"] = section("Change From Previous IRP", ("Estimated Cost",))
    return rec


def parse_details(texts: list[str], after_page: int) -> tuple[dict[str, dict], list[str]]:
    details: dict[str, dict] = {}
    notes: list[str] = []
    for n in range(after_page + 1, len(texts) + 1):
        d = parse_detail_page(texts[n - 1], n)
        if not d:
            continue
        if not d["teams_no"]:
            notes.append(f"p{n}: detail page without a readable Teams #")
            continue
        if d["teams_no"] in details:
            notes.append(f"p{n}: Teams # {d['teams_no']} also has a detail page on p{details[d['teams_no']]['page']}; kept the first")
            continue
        details[d["teams_no"]] = d
    return details, notes


# --------------------------------------------------------------------------- together


def extract(texts: list[str], words_by_page: dict[int, list[dict]]) -> dict:
    pages = find_table_pages(texts)
    rows_a, notes_a = parse_table_lines(texts, pages)
    rows_b = parse_table_words(words_by_page)
    details, notes_d = parse_details(texts, pages[-1] if pages else 0)

    # pair the two readings row by row (same order on the page) and compare every field
    agree = 0
    disagreements = []
    for i, a in enumerate(rows_a):
        b = rows_b[i] if i < len(rows_b) else None
        fields = ("zone", "teams_no", "need_date_raw", "sponsor")
        same = b is not None and all(str(a[f]) == str(b[f]) for f in fields) and str(a["year"]) == str(b["year"]) and canon(a["name"]) == canon(b["name"])
        a["parsers_agree"] = same
        a["name_by_position"] = b["name"] if b else None
        if same:
            agree += 1
        else:
            disagreements.append({"teams_no": a["teams_no"], "page": a["page"], "by_lines": a["name"], "by_position": b["name"] if b else None})
        d = details.get(a["teams_no"])
        a["detail"] = d
        a["title_matches_detail"] = bool(d) and canon(d["title"]) == canon(a["name"])
    per_page = {}
    for r in rows_a:
        per_page[r["page"]] = per_page.get(r["page"], 0) + 1
    return {
        "pages": pages,
        "rows": rows_a,
        "rows_by_position": len(rows_b),
        "parsers_agree": agree,
        "disagreements": disagreements,
        "rows_per_page": per_page,
        "details": details,
        "notes": notes_a + notes_d,
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).parent))
    from pdf_text import page_texts, page_words

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "sources" / "ga_2025_irp_vol3_public.pdf"
    texts = page_texts(src)
    pages = find_table_pages(texts)
    out = extract(texts, page_words(src, pages))
    print(f"table pages {out['pages'][0]}-{out['pages'][-1]}, rows by lines {len(out['rows'])}, by position {out['rows_by_position']}, agree {out['parsers_agree']}")
    print("rows per page", out["rows_per_page"])
    print("detail pages", len(out["details"]), "| rows with a detail page", sum(1 for r in out["rows"] if r["detail"]),
          "| title == joined name", sum(1 for r in out["rows"] if r["title_matches_detail"]))
    for d in out["disagreements"]:
        print("  DISAGREE", d)
    for r in out["rows"]:
        if r["detail"] and not r["title_matches_detail"]:
            print(f"  TITLE p{r['page']} {r['teams_no']}: table '{r['name']}' vs detail p{r['detail']['page']} '{r['detail']['title']}'")
        if not r["detail"]:
            print(f"  NO DETAIL p{r['page']} {r['teams_no']} {r['name']}")
    for n in out["notes"]:
        print("  note", n)
