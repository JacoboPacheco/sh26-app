"""Precomputed Strengthen studies: backend/demo/strengthen/*.json (written by scripts/bake_strengthen.py from a local
run, Gemini bundles included and engine-verified at bake time) are loaded into unlock's study cache at startup, so
the deployed page opens with answers instead of computing a study on the free tier's small CPU.

A file is used only when its engine fingerprint (a hash of the engine's source files, line endings normalized)
matches this code: after an engine change the study is computed live, as before, until the files are baked again.
Nothing here changes a number: a baked result is exactly what the same code computed."""

import hashlib
import json
import logging
from pathlib import Path

HERE = Path(__file__).parent
DIR = HERE / "demo" / "strengthen"
ENGINE_FILES = ("unlock.py", "capacity.py", "capacity_ai.py", "powerflow.py", "danger.py", "costs.py", "grid.py")


def fingerprint() -> str:
    h = hashlib.sha256()
    for name in ENGINE_FILES:
        h.update(name.encode())
        h.update((HERE / name).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def load() -> int:
    """Put every matching baked study in unlock's cache; returns how many were loaded."""
    import unlock

    log = logging.getLogger("uvicorn.error")
    if not DIR.is_dir():
        return 0
    fp = fingerprint()
    n = 0
    for p in sorted(DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            key = (str(d["key"][0]), float(d["key"][1]), float(d["key"][2]))
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            log.warning("baked study %s unreadable: %s", p.name, e)
            continue
        if d.get("fingerprint") != fp:
            log.info("baked study %s is from other engine code (%s, now %s): computed live instead", p.name, d.get("fingerprint"), fp)
            continue
        cap = (d["result"] or {}).get("capacity") or {}
        if (cap.get("ai") or {}).get("status") == "pending":
            # baked before Gemini's challenge landed (scripts/bake_strengthen.py waits for it): nothing will finish it here
            import capacity_ai

            d["result"]["capacity"] = cap = {**cap, "ai": capacity_ai._empty("offline", "Gemini's challenge hadn't finished when this study was saved; the engine's plan stands.", bar=cap["ai"].get("bar"))}
        # the same for the single-outage screen (the page says it didn't run) and the sensitivity cases (the fold offers
        # to run them on request, which works without the study object): nothing here would finish a "pending" one
        if (cap.get("n1") or {}).get("status") == "pending":
            d["result"]["capacity"] = cap = {**cap, "n1": {**cap["n1"], "status": "error"}}
        if (cap.get("sensitivity") or {}).get("status") == "pending":
            d["result"]["capacity"] = cap = {**cap, "sensitivity": {**cap["sensitivity"], "status": "not_run"}}
        d["result"]["baked"] = True  # its Gemini parts were recorded with a key, whatever this server has
        with unlock._cache_lock:
            unlock._cache[key] = d["result"]
        getattr(unlock, "_PINNED", set()).add(key)  # never evicted, like the warmed study
        n += 1
    if n:
        log.info("loaded %d baked Strengthen studies", n)
    return n
