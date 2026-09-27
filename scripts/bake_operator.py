"""Bake the two Fort Myers hero runs of "Let an AI operator try" (1,000 MW and 1,500 MW, 4 PM) into backend/demo/operator/, so
any host, a freshly restarted Render instance included, answers those two cases at once instead of running for minutes
(backend/grid_operator.py loads them the way backend/baked.py loads the Strengthen studies). Each is a REAL run through a
local backend's API, Gemini's function calls included (that backend needs GEMINI_API_KEY): nothing is edited, faked or
re-labeled here. A run is written only when Gemini answered every step it was asked (by "gemini", not stopped early) and the
no-operator run matched the cascade route; whatever the honest result is (at 1,500 MW Gemini may tie doing nothing) is what is saved.

    backend/venv/Scripts/python scripts/bake_operator.py http://127.0.0.1:9091

Each file carries the fingerprint of the code that produced it (grid_operator.fingerprint()): rebake after any change to
grid_operator.py, powerflow.py, grid.py, costs.py or plants.py (a stale file is ignored and the case runs live, never served).
To record a fresh Gemini sample of a case whose file is current, delete its file first (the server answers from the file)."""

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")  # the backend modules read their settings (JWT_SECRET...) from the environment when imported
import grid_operator  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9091").rstrip("/")
HERO = {"lat": 26.6406, "lon": -81.8723}  # Fort Myers, the hero link's own point
SIZES = [1000, 1500]


def call(method, path, body=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> int:
    fp = grid_operator.fingerprint()
    grid_operator.BAKED_DIR.mkdir(parents=True, exist_ok=True)
    bad = 0
    for mw in SIZES:
        t0 = time.time()
        j = call("POST", "/api/operator/run", {**HERO, "mw": mw})
        while j["status"] == "running":
            if time.time() - t0 > 900:
                break
            time.sleep(3)
            j = call("GET", f"/api/operator/jobs/{j['job']}")
            print(f"  {mw} MW: {time.time() - t0:.0f} s, {j['progress']}")
        if j["status"] != "done":
            print(f"{mw} MW: {j.get('status')} {j.get('error')}")
            bad += 1
            continue
        r = j["result"]
        if r.get("baked"):
            print(f"{mw} MW: the server answered from the committed file (current): delete backend/demo/operator/FL_{mw}.json to record a fresh run")
            continue
        g = (r["runs"] or {}).get("gemini")
        if r["by"] != "gemini" or not g or g.get("stopped_at") or not r.get("matches_cascade"):
            print(f"{mw} MW: not saved: by {r['by']} ({r.get('why')}), gemini stopped_at {(g or {}).get('stopped_at')}, matches_cascade {r.get('matches_cascade')}: rerun it")
            bad += 1
            continue
        out = {"key": grid_operator.key_of_header(r["case"]), "fingerprint": fp, "baked_from": BASE, "result": {**r, "cached": False}}
        out["result"].pop("baked", None)
        path = grid_operator.BAKED_DIR / f"FL_{mw}.json"
        path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        toll = lambda k: ((r["runs"].get(k) or {}).get("toll") or {}).get("people_hit")  # noqa: E731
        print(f"{mw} MW: saved {path.name} ({path.stat().st_size / 1e3:.0f} kB) in {time.time() - t0:.0f} s: none {toll('none'):,}, engine {toll('engine'):,}, gemini {toll('gemini'):,}; {r['calls']} calls, {r['tool_calls']} tool calls; {r['verdict']['text']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
