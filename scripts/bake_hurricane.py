"""Bake the three hypothetical-storm presets' track_hits() results into backend/demo/hurricane_bake.json,
so a request for one of them (GET /api/hurricane/presets, then "Make landfall") serves an instant, already-computed
answer instead of solving the wind field live -- no per-request compute, no per-request memory, for the demo's
three named storms. A hand-drawn path always still computes live (cheap: see backend/hurricane.py's module docstring).

    backend/venv/Scripts/python scripts/bake_hurricane.py

Each entry carries a fingerprint (hurricane._fingerprint: this module's MODEL_VERSION, the preset's own inputs, and
the grid's branch ids/kv) so a stale bake -- a tuning change, a grid rebuild -- is detected at import and ignored,
falling back to a live solve for that preset only. Rerun this after any change to backend/hurricane.py's constants
or PRESETS list, or after `backend/demo/build_grid.py` regenerates the grid."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import hurricane as H  # noqa: E402

OUT = ROOT / "backend" / "demo" / "hurricane_bake.json"


def main() -> int:
    out = {}
    for p in H.PRESETS:
        radius = p.get("radius_km", H.CATEGORY_REACH_KM[p["category"]])
        hits = H.track_hits(p["points"], radius, p["category"], p["landfall_km"])
        out[p["id"]] = {"fingerprint": H._fingerprint(p), "hits": hits}
        print(f"{p['id']:20s} category {p['category']}  {hits['count']:4d} lines  capped={hits['capped']}")
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
