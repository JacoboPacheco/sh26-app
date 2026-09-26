"""Bake the Strengthen studies the demo opens with into backend/demo/strengthen/, so a deployed backend loads them at
startup (backend/baked.py) instead of computing them on a small CPU. Runs each study through a local backend's API
(Gemini bundles included when that backend has the key) and stamps it with the engine fingerprint.

    backend/venv/Scripts/python scripts/bake_strengthen.py http://127.0.0.1:8306 [FL:1000 FL:500 ...]

Rebake after any change to the engine files (baked.ENGINE_FILES): a stale file is ignored, never served."""

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import baked  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8306").rstrip("/")
CASES = sys.argv[2:] or ["FL:1000", "FL:500", "FL:2000", "FL:5000"]


def call(method, path, body=None, timeout=300):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    fp = baked.fingerprint()
    baked.DIR.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        region, mw = case.split(":")
        t0 = time.time()
        job = call("POST", "/api/unlock/start", {"region": region, "mw": float(mw), "load_factor": 1.0})
        while True:
            s = call("GET", f"/api/unlock/jobs/{job['id']}")
            if s["status"] in ("done", "error"):
                break
            time.sleep(2)
        if s["status"] != "done":
            print(f"{case}: {s.get('error')}")
            continue
        r = s["result"]
        key = [r["region"], float(r["mw"]), float(r["load_factor"])]
        out = {"key": key, "fingerprint": fp, "baked_from": BASE, "result": r}
        path = baked.DIR / f"{region}_{int(float(mw))}.json"
        path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        cap = (r.get("capacity") or {}).get("firm") or {}
        print(f"{case}: {time.time() - t0:.0f} s, {path.stat().st_size / 1e3:.0f} kB, AI {(r.get('ai') or {}).get('status')}, capacity today {cap.get('today')} / {len(cap.get('steps') or [])} steps")


if __name__ == "__main__":
    main()
