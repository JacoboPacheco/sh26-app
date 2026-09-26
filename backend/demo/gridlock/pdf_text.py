"""PDF text with a content-addressed cache (raw/pdf_cache/, gitignored).

Reading all 668 pages of the Georgia IRP volume takes ~90 s with pdfplumber; the cache is keyed by the
file's SHA-256, so a re-run with the same PDF is instant and a new version of the filing is re-read
automatically. pdfplumber is an OFFLINE build dependency (requirements-build.txt), never a runtime one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CACHE = Path(__file__).parent / "raw" / "pdf_cache"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_file(path: Path, kind: str) -> Path:
    return CACHE / f"{sha256(path)[:16]}_{kind}.json"


def page_texts(path: Path, use_cache: bool = True) -> list[str]:
    """Text of every page, in order (index 0 = page 1)."""
    cf = _cache_file(path, "text")
    if use_cache and cf.exists():
        return json.loads(cf.read_text(encoding="utf-8"))
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        out = [(p.extract_text() or "") for p in pdf.pages]
    CACHE.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(out), encoding="utf-8")
    return out


def page_words(path: Path, pages: list[int], use_cache: bool = True) -> dict[int, list[dict]]:
    """Positioned words ({text, x0, x1, top, bottom}) for the given 1-based pages."""
    key = hashlib.sha1(",".join(map(str, sorted(pages))).encode()).hexdigest()[:8]
    cf = _cache_file(path, f"words_{key}")
    if use_cache and cf.exists():
        return {int(k): v for k, v in json.loads(cf.read_text(encoding="utf-8")).items()}
    import pdfplumber

    out: dict[int, list[dict]] = {}
    with pdfplumber.open(path) as pdf:
        for n in pages:
            words = pdf.pages[n - 1].extract_words(keep_blank_chars=False, use_text_flow=False)
            out[n] = [
                {"text": w["text"], "x0": round(w["x0"], 2), "x1": round(w["x1"], 2), "top": round(w["top"], 2), "bottom": round(w["bottom"], 2)}
                for w in words
            ]
    CACHE.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(out), encoding="utf-8")
    return out
