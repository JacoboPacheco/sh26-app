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
ENGINE_FILES = ("unlock.py", "capacity.py", "powerflow.py", "danger.py", "costs.py", "grid.py")


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
        with unlock._cache_lock:
            unlock._cache[key] = d["result"]
        getattr(unlock, "_PINNED", set()).add(key)  # never evicted, like the warmed study
        n += 1
    if n:
        log.info("loaded %d baked Strengthen studies", n)
    return n
