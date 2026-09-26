"""Build plans, Reader C: what Gemini read from the filings' PDF pages, compared with the pipeline's two parsers.

    GET /api/gridlock/reader   the committed report (backend/demo/gridlock/data/gemini_reader.json)

The report is made OFFLINE by backend/demo/gridlock/gemini_reader.py (Gemini reads each page as a PDF with a JSON Schema;
the per-field agreement with the parsers, every disagreement with its PDF page, and rescue proposals for set-aside
records re-run through the pipeline's own checks). Nothing here calls Gemini: this route only serves the file, so it
costs no quota and works on a deployed backend without a key. Gemini proposes; the pipeline's checks decide.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from limiter import limiter

router = APIRouter(tags=["gridlock"])

READER_FILE = Path(__file__).parent / "demo" / "gridlock" / "data" / "gemini_reader.json"
_cache: dict = {"key": None, "bytes": None}


def _key():
    try:
        st = READER_FILE.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


@router.get("/api/gridlock/reader")
@limiter.limit("60/minute")
def reader(request: Request):
    key = _key()
    if key is None:
        raise HTTPException(status_code=404, detail="No reader report yet: run backend/venv/Scripts/python backend/demo/gridlock/gemini_reader.py")
    if _cache["key"] != key:
        try:
            doc = json.loads(READER_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=500, detail=f"gemini_reader.json could not be read: {e}") from e
        _cache["bytes"] = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        _cache["key"] = key
    return Response(content=_cache["bytes"], media_type="application/json")
