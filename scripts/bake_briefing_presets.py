"""Bake every catastrophe preset backend/demo/catastrophes.json lists into backend/demo/briefing/<preset-id>.json, so
any host, a freshly restarted Render instance included, answers a preset ("A Category 5 crosses all of Florida", etc.)
at once instead of running its LP-verified rebuild plan live (backend/briefing.py loads these the way backend/baked.py
loads the Strengthen studies and backend/grid_operator.py loads its baked hero runs). Each file is a REAL report from
this code (briefing.build_case + briefing._build, in-process — no server and no Gemini key needed: report_for never
calls out to an AI itself, only solutions.kick() after the HTTP response does, and that is not part of the report):
nothing is edited, faked or re-labeled here.

    backend/venv/Scripts/python scripts/bake_briefing_presets.py

Each file carries the fingerprint of the code that produced it (briefing.fingerprint()): rebake after any change to
briefing.py, powerflow.py, grid.py, costs.py or solutions.py (a stale file is ignored and the preset computes live,
never served, exactly as before this cycle — see BUG 1 in CLAUDE.md's history)."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")  # the backend modules read their settings from the environment when imported
import briefing  # noqa: E402
from briefing import BriefingIn  # noqa: E402


def main() -> int:
    fp = briefing.fingerprint()
    briefing.BAKED_DIR.mkdir(parents=True, exist_ok=True)
    bad = 0
    for pid, preset in sorted(briefing.PRESETS.items()):
        t0 = time.time()
        body = BriefingIn(region=preset["region"], preset=pid)
        try:
            c = briefing.build_case(body)
            rep = briefing._build(c, briefing.PRESET_BUDGET_MS, time.perf_counter())
        except Exception as e:  # noqa: BLE001
            print(f"{pid} ({preset['region']}): FAILED to build: {e!r}")
            bad += 1
            continue
        out = {"key": c.key, "fingerprint": fp, "baked_from": "in-process (scripts/bake_briefing_presets.py)", "result": rep}
        path = briefing.BAKED_DIR / f"{pid}.json"
        path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        ev = rep.get("event") or {}
        print(
            f"{pid} ({preset['region']}): saved {path.name} ({path.stat().st_size / 1e3:.0f} kB) in {time.time() - t0:.1f} s: "
            f"verdict {rep.get('verdict')}, lines_out {ev.get('storm_lines_out')}, people {ev.get('people')}, key {c.key[:12]}"
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
