"""Voice for the incident briefing: the server registers every line it wrote for narration and hands
the page a key; only registered text is ever spoken (ElevenLabs is added behind ELEVENLABS_API_KEY).

register(text, lang, role) -> key: key = sha256(VERSION|model|voice|lang|settings|text)[:32]. The
text is kept in an in-memory LRU and in backend/.voice_cache/<key>.json (gitignored), so a segment
request carries only a key and the server never speaks text a visitor typed.
"""

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["voice"])
log = logging.getLogger("uvicorn.error")

VERSION = "v1"
ROLES = ("presenter", "analyst")
LANGS = ("en", "es")
# stability 0.6 keeps a steady newsreader delivery; style 0 = no exaggeration (ElevenLabs docs:
# https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps → voice_settings)
VOICE_SETTINGS = {"stability": 0.6, "similarity_boost": 0.75, "style": 0.0}

HERE = Path(__file__).parent
CACHE_DIR = Path(os.getenv("VOICE_CACHE_DIR", str(HERE / ".voice_cache")))
PINNED_DIR = HERE / "demo" / "voice"  # pre-rendered hero audio (committed once the key exists)
MAX_TEXTS = 1024

_texts: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


def model_id() -> str:
    return os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5").strip() or "eleven_flash_v2_5"


def voice_ref(role: str) -> str:
    """The voice a role speaks with, as it enters the key: the configured voice id, or 'auto:<role>'
    (resolved from the account's default voices at render time, deterministically)."""
    env = os.getenv(f"ELEVENLABS_VOICE_{role.upper()}", "").strip()
    return env or f"auto:{role}"


def key_for(text: str, lang: str, role: str) -> str:
    settings = json.dumps(VOICE_SETTINGS, sort_keys=True, separators=(",", ":"))
    raw = "|".join([VERSION, model_id(), voice_ref(role), lang, settings, text])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def register(text: str, lang: str, role: str, prev_text: str | None = None, next_text: str | None = None) -> str:
    """Remember a line the server wrote for narration; returns the key the page asks audio for.
    prev_text / next_text (optional) only steer the voice's intonation across segments."""
    lang = lang if lang in LANGS else "en"
    role = role if role in ROLES else "presenter"
    key = key_for(text, lang, role)
    meta = {"key": key, "text": text, "lang": lang, "role": role, "prev_text": prev_text or "", "next_text": next_text or ""}
    with _lock:
        known = key in _texts
        _texts[key] = meta
        _texts.move_to_end(key)
        while len(_texts) > MAX_TEXTS:
            _texts.popitem(last=False)
    if not known:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path = CACHE_DIR / f"{key}.json"
            if not path.exists():
                path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except OSError as e:  # a read-only disk still narrates from memory
            log.warning("voice: could not store %s: %s", key, e)
    return key


def lookup(key: str) -> dict | None:
    """The registered text for a key: memory, then the cache folder, then the pinned folder."""
    with _lock:
        meta = _texts.get(key)
        if meta is not None:
            _texts.move_to_end(key)
            return meta
    for folder in (CACHE_DIR, PINNED_DIR):
        path = folder / f"{key}.json"
        if path.exists():
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            with _lock:
                _texts[key] = meta
            return meta
    return None
