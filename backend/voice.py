"""Voice for the incident briefing: ElevenLabs reads the deck the writer built (bulletin.py), with
per-character timestamps for word captions and map cues. Without ELEVENLABS_API_KEY every segment
answers 503 and the page reads the same text with the browser's own voice.

Only text the server wrote is ever spoken: the writer calls register(text, lang, role) for each
narration segment and hands the page the returned key; a segment request carries only that key.
key = sha256(VERSION|model|voice|lang|settings|text)[:32]; the text is kept in memory (LRU 1,024) and
in backend/.voice_cache/<key>.json (gitignored).

Endpoints
  GET  /api/voice/status              configured?, provider, model, voices, characters left today
  POST /api/voice/segment {key}       render (or serve cached) one segment → audio URL, duration,
                                      words [[w, t0, t1]], cues [[name, value, t]]      (60/minute)
                                      409 unknown key · 503 not configured · 429 daily cap reached
  GET  /api/voice/audio/<key>.mp3     the audio (immutable)
  POST /api/voice/download {deck_key, lang}  the whole briefing: MP3 (when every segment has audio),
                                      captions .vtt, transcript .txt                     (10/minute)
  GET  /api/voice/file/<id>.<ext>     those files, as attachments

ElevenLabs API (docs fetched Sat 26 Sep 2026):
  POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_64
       body {text, model_id, language_code, voice_settings, previous_text, next_text}
       → {audio_base64, alignment{characters, character_start_times_seconds, character_end_times_seconds},
          normalized_alignment{...}}   https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
  GET  https://api.elevenlabs.io/v2/voices?voice_type=default → {voices[{voice_id, name, category, labels}]}
       https://elevenlabs.io/docs/api-reference/voices/search
  Models: eleven_flash_v2_5 (default here: low latency, half the credits, Spanish included)
       https://elevenlabs.io/docs/models
Cache lookup order: backend/demo/voice/ (pinned hero audio, committed) → backend/.voice_cache/ (LRU
100 MB) → render. Premade voices only; no cloning; no hardcoded voice ids.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from limiter import limiter

router = APIRouter(tags=["voice"])
log = logging.getLogger("uvicorn.error")

VERSION = "v1"
ROLES = ("presenter", "analyst")
LANGS = ("en", "es")
# stability 0.6 keeps a steady newsreader delivery; style 0 = no exaggeration (voice_settings in the docs above)
VOICE_SETTINGS = {"stability": 0.6, "similarity_boost": 0.75, "style": 0.0}
OUTPUT_FORMAT = "mp3_44100_64"
LANGUAGE_MODELS = ("eleven_flash_v2_5", "eleven_turbo_v2_5")  # models that take language_code
# premade voices, by first name, in order of preference (resolved once from the account's default voices)
PREFER = {
    "presenter": ("George", "Brian", "Daniel", "Adam", "Eric", "Chris", "Roger", "Will", "Liam", "Bill"),
    "analyst": ("Sarah", "Alice", "Matilda", "Laura", "Jessica", "Charlotte", "Lily", "Aria", "River"),
}
ATTRIBUTION = "Voice: ElevenLabs"

HERE = Path(__file__).parent
CACHE_DIR = Path(os.getenv("VOICE_CACHE_DIR", str(HERE / ".voice_cache")))
PINNED_DIR = HERE / "demo" / "voice"  # pre-rendered hero audio (committed once the key exists)
MAX_TEXTS = 1024
CACHE_MAX_BYTES = 100 * 1024 * 1024
QUOTA_TZ = ZoneInfo("America/Los_Angeles")  # ElevenLabs and Google both count days in Pacific time here
GAP_S, HOLD_S = 0.35, 0.8  # the stage's pause between segments and before the next slide (bulletin.py)
_KEY = re.compile(r"^[0-9a-f]{32}$")

_texts: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()
_daily = {"day": "", "used": 0}
_voices: dict = {"resolved": False, "presenter": None, "analyst": None, "names": {}}
_render_locks: dict[str, asyncio.Lock] = {}
_sem: asyncio.Semaphore | None = None


# ------------------------------------------------------------------------------------------ config
def api_key() -> str:
    return os.getenv("ELEVENLABS_API_KEY", "").strip()


def configured() -> bool:
    return bool(api_key())


def api_base() -> str:
    return os.getenv("ELEVENLABS_API_BASE", "https://api.elevenlabs.io").rstrip("/")


def model_id() -> str:
    return os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5").strip() or "eleven_flash_v2_5"


def daily_chars() -> int:
    try:
        return max(int(os.getenv("VOICE_DAILY_CHARS", "8000")), 0)
    except ValueError:
        return 8000


def timeout_s() -> float:
    try:
        return float(os.getenv("ELEVENLABS_TIMEOUT_S", "8"))
    except ValueError:
        return 8.0


def voice_ref(role: str) -> str:
    """The voice a role speaks with, as it enters the key: the configured voice id, or 'auto:<role>'
    (resolved from the account's default voices at render time, deterministically)."""
    env = os.getenv(f"ELEVENLABS_VOICE_{role.upper()}", "").strip()
    return env or f"auto:{role}"


def key_for(text: str, lang: str, role: str) -> str:
    settings = json.dumps(VOICE_SETTINGS, sort_keys=True, separators=(",", ":"))
    raw = "|".join([VERSION, model_id(), voice_ref(role), lang, settings, text])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# -------------------------------------------------------------------------------------- the script
def register(text: str, lang: str, role: str, prev_text: str | None = None, next_text: str | None = None,
             cues: list | None = None) -> str:
    """Remember a line the server wrote for narration; returns the key the page asks audio for.
    prev_text / next_text only steer intonation across segments; cues ({char, name, value}) come back
    with times once the audio exists."""
    lang = lang if lang in LANGS else "en"
    role = role if role in ROLES else "presenter"
    key = key_for(text, lang, role)
    meta = {"key": key, "text": text, "lang": lang, "role": role, "prev_text": prev_text or "", "next_text": next_text or "",
            "cues": [c for c in (cues or []) if isinstance(c, dict)]}
    with _lock:
        known = _texts.get(key) == meta
        _texts[key] = meta
        _texts.move_to_end(key)
        while len(_texts) > MAX_TEXTS:
            _texts.popitem(last=False)
    if not known:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (CACHE_DIR / f"{key}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except OSError as e:  # a read-only disk still narrates from memory
            log.warning("voice: could not store %s: %s", key, e)
        _writes["n"] += 1
        if _writes["n"] % 500 == 0:
            _trim_cache()
    return key


_writes = {"n": 0}


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


# --------------------------------------------------------------------------------------------- MP3
_BR = {3: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
       2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]}
_SR = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def mp3_frames(data: bytes) -> list[tuple[int, int, int, int]]:
    """(offset, length, sample rate, samples) of each MPEG Layer III audio frame, skipping an ID3v2
    tag and the Xing/Info frame (its frame count would be wrong once segments are joined)."""
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
        i = 10 + size
    out = []
    while i + 4 <= len(data):
        h0, h1, h2, h3 = data[i], data[i + 1], data[i + 2], data[i + 3]
        ver, layer, br_i, sr_i = (h1 >> 3) & 3, (h1 >> 1) & 3, h2 >> 4, (h2 >> 2) & 3
        if h0 != 0xFF or (h1 & 0xE0) != 0xE0 or layer != 1 or ver == 1 or br_i in (0, 15) or sr_i == 3:
            i += 1
            continue
        sr = _SR[ver][sr_i]
        br = _BR[3 if ver == 3 else 2][br_i] * 1000
        pad = (h2 >> 1) & 1
        length = (144 * br // sr if ver == 3 else 72 * br // sr) + pad
        if length < 8 or i + length > len(data):
            break
        mono = ((h3 >> 6) & 3) == 3
        side = (17 if mono else 32) if ver == 3 else (9 if mono else 17)
        at = i + 4 + (0 if h1 & 1 else 2) + side
        if data[at:at + 4] in (b"Xing", b"Info"):
            i += length
            continue
        out.append((i, length, sr, 1152 if ver == 3 else 576))
        i += length
    return out


def mp3_duration(data: bytes) -> float:
    fr = mp3_frames(data)
    return round(sum(s / r for _, _, r, s in fr), 3) if fr else 0.0


def mp3_silence(like: bytes, seconds: float) -> bytes:
    """Silent frames shaped like the first frame of `like` (a frame whose side info is all zero
    decodes to silence), for the pauses between segments in a downloaded briefing."""
    fr = mp3_frames(like)
    if not fr or seconds <= 0:
        return b""
    off, _, sr, samples = fr[0]
    h = bytearray(like[off:off + 4])
    h[1] |= 0x01  # no CRC
    h[2] &= ~0x02 & 0xFF  # no padding
    ver, br_i = (h[1] >> 3) & 3, h[2] >> 4
    br = _BR[3 if ver == 3 else 2][br_i] * 1000
    length = 144 * br // sr if ver == 3 else 72 * br // sr
    frame = bytes(h) + b"\x00" * (length - 4)
    return frame * max(int(round(seconds * sr / samples)), 1)


def mp3_audio(data: bytes) -> bytes:
    """Just the audio frames (no ID3, no Xing/Info), ready to be joined."""
    return b"".join(data[o:o + n] for o, n, _, _ in mp3_frames(data))


# ------------------------------------------------------------------------------------------- quota
def _day() -> str:
    return datetime.now(QUOTA_TZ).date().isoformat()


def chars_left() -> int:
    with _lock:
        if _daily["day"] != _day():
            _daily["day"], _daily["used"] = _day(), 0
        return max(daily_chars() - _daily["used"], 0)


def _take(n: int) -> bool:
    with _lock:
        if _daily["day"] != _day():
            _daily["day"], _daily["used"] = _day(), 0
        if _daily["used"] + n > daily_chars():
            return False
        _daily["used"] += n
        return True


def _give_back(n: int) -> None:
    with _lock:
        _daily["used"] = max(_daily["used"] - n, 0)


# ----------------------------------------------------------------------------------- ElevenLabs I/O
def _http(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"xi-api-key": api_key(), "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s()) as resp:
        return json.loads(resp.read())


def _resolve_voices() -> dict:
    """Voice ids per role: the env's, else premade default voices picked by name preference (once)."""
    if _voices["resolved"]:
        return _voices
    env = {r: os.getenv(f"ELEVENLABS_VOICE_{r.upper()}", "").strip() for r in ROLES}
    found: list[dict] = []
    if not all(env.values()):
        try:
            data = _http("GET", f"{api_base()}/v2/voices?voice_type=default&page_size=100")
            found = [v for v in data.get("voices", []) if isinstance(v, dict) and v.get("voice_id")]
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.warning("voice: could not list ElevenLabs voices: %s", e)
    premade = [v for v in found if v.get("category") in (None, "premade", "default")] or found
    used: set[str] = set()
    for role in ROLES:
        if env[role]:
            _voices[role], _voices["names"][role] = env[role], "custom"
            used.add(env[role])
            continue
        pick = None
        for name in PREFER[role]:
            pick = next((v for v in premade if str(v.get("name", "")).split(" ")[0].split("-")[0].strip().lower() == name.lower()
                         and v["voice_id"] not in used), None)
            if pick:
                break
        pick = pick or next((v for v in premade if v["voice_id"] not in used), None)
        if pick:
            _voices[role], _voices["names"][role] = pick["voice_id"], str(pick.get("name", "")).split(" - ")[0]
            used.add(pick["voice_id"])
    _voices["resolved"] = bool(_voices["presenter"] and _voices["analyst"])
    return _voices


def _render(meta: dict) -> tuple[bytes, dict]:
    """One ElevenLabs call: (mp3 bytes, alignment). Raises urllib errors / ValueError."""
    v = _resolve_voices()
    voice_id = v.get(meta["role"]) or v.get("presenter")
    if not voice_id:
        raise ValueError("no ElevenLabs voice available")
    body = {"text": meta["text"], "model_id": model_id(), "voice_settings": VOICE_SETTINGS}
    if model_id() in LANGUAGE_MODELS:
        body["language_code"] = meta["lang"]
    if meta.get("prev_text"):
        body["previous_text"] = meta["prev_text"][-400:]
    if meta.get("next_text"):
        body["next_text"] = meta["next_text"][:400]
    data = _http("POST", f"{api_base()}/v1/text-to-speech/{voice_id}/with-timestamps?output_format={OUTPUT_FORMAT}", body)
    audio = base64.b64decode(data.get("audio_base64") or "")
    if not audio:
        raise ValueError("ElevenLabs returned no audio")
    return audio, data.get("alignment") or data.get("normalized_alignment") or {}


def _timing(meta: dict, alignment: dict, duration: float) -> dict:
    """Words [[w, t0, t1]] and cues [[name, value, t]] from the character alignment (the original text's
    characters, so the writer's character offsets map straight onto it)."""
    chars = alignment.get("characters") or []
    t0s = alignment.get("character_start_times_seconds") or []
    t1s = alignment.get("character_end_times_seconds") or []
    text = meta["text"]
    n = min(len(chars), len(t0s), len(t1s))
    if n == 0:  # no alignment: spread evenly
        t0s = [duration * i / max(len(text), 1) for i in range(len(text))]
        t1s = [duration * (i + 1) / max(len(text), 1) for i in range(len(text))]
        n = len(text)
    scale = (n / len(text)) if text and n != len(text) else 1.0

    def at(c: int) -> float:
        i = min(int(c * scale), n - 1) if n else 0
        return round(float(t0s[i]), 3) if n else 0.0

    words, start = [], None
    for i, ch in enumerate(text):
        if not ch.isspace() and start is None:
            start = i
        if (ch.isspace() or i == len(text) - 1) and start is not None:
            end = i if ch.isspace() else i + 1
            j0, j1 = min(int(start * scale), n - 1), min(int((end - 1) * scale), n - 1)
            words.append([text[start:end], round(float(t0s[j0]), 3), round(float(t1s[j1]), 3)])
            start = None
    cues = [[c["name"], c["value"], at(int(c["char"])) if int(c["char"]) < len(text) else round(duration, 3)] for c in meta.get("cues", [])]
    return {"words": words, "cues": cues}


def _paths(key: str) -> tuple[Path, Path] | None:
    """The (mp3, timing) files for a key, pinned first."""
    for folder in (PINNED_DIR, CACHE_DIR):
        mp3, tj = folder / f"{key}.mp3", folder / f"{key}.align.json"
        if mp3.exists() and tj.exists():
            return mp3, tj
    return None


def _payload(key: str, meta: dict | None) -> dict | None:
    p = _paths(key)
    if p is None:
        return None
    try:
        t = json.loads(p[1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta is not None and meta.get("cues") and not t.get("cues"):
        t.update(_timing(meta, t.get("alignment") or {}, t["duration_s"]))
    return {"key": key, "audio_url": f"/api/voice/audio/{key}.mp3", "duration_s": t["duration_s"], "words": t["words"],
            "cues": t.get("cues", []), "provider": "elevenlabs", "role": t.get("role"), "lang": t.get("lang"),
            "pinned": p[0].parent == PINNED_DIR}


MAX_SCRIPTS = 4000  # registered-text files kept on disk (memory keeps the newest 1,024 anyway)
DOWNLOAD_TTL_S = 24 * 3600


def _trim_cache() -> None:
    """Audio: oldest out past 100 MB. Downloads: gone after a day. Script files: the newest 4,000."""
    try:
        files = sorted(CACHE_DIR.glob("*.mp3"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while files and total > CACHE_MAX_BYTES:
            f = files.pop(0)
            total -= f.stat().st_size
            f.unlink(missing_ok=True)
            (CACHE_DIR / f"{f.stem}.align.json").unlink(missing_ok=True)
        now = time.time()
        for f in CACHE_DIR.glob("dl-*"):
            if now - f.stat().st_mtime > DOWNLOAD_TTL_S:
                f.unlink(missing_ok=True)
        scripts = [f for f in CACHE_DIR.glob("*.json") if _KEY.match(f.stem)]
        if len(scripts) > MAX_SCRIPTS:
            for f in sorted(scripts, key=lambda f: f.stat().st_mtime)[: len(scripts) - MAX_SCRIPTS]:
                if not (CACHE_DIR / f"{f.stem}.mp3").exists():
                    f.unlink(missing_ok=True)
    except OSError as e:
        log.warning("voice: cache trim failed: %s", e)


def _save(key: str, meta: dict, audio: bytes, alignment: dict) -> dict:
    duration = mp3_duration(audio) or float((alignment.get("character_end_times_seconds") or [0])[-1])
    timing = {"duration_s": round(duration, 3), **_timing(meta, alignment, duration), "role": meta["role"], "lang": meta["lang"],
              "model": model_id(), "voice": _voices["names"].get(meta["role"]), "alignment": alignment, "created": int(time.time())}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.mp3").write_bytes(audio)
    (CACHE_DIR / f"{key}.align.json").write_text(json.dumps(timing, ensure_ascii=False), encoding="utf-8")
    _trim_cache()
    return timing


async def ensure_audio(key: str) -> dict:
    """The segment payload: cached/pinned audio, else one ElevenLabs render. HTTPException otherwise."""
    global _sem
    meta = lookup(key)
    if meta is None:
        cached = _payload(key, None)  # pinned audio outlives the in-memory script
        if cached:
            return cached
        raise HTTPException(status_code=409, detail="Script expired — fetch it again")
    cached = _payload(key, meta)
    if cached:
        return cached
    if not configured():
        raise HTTPException(status_code=503, detail="Voice not configured")
    lock = _render_locks.setdefault(key, asyncio.Lock())
    async with lock:  # the stage prefetches the next slide; a second request waits for the first render
        cached = _payload(key, meta)
        if cached:
            return cached
        n = len(meta["text"])
        if not _take(n):
            raise HTTPException(status_code=429, detail="Voice quota for today is used up")
        if _sem is None:
            _sem = asyncio.Semaphore(2)
        try:
            async with _sem:
                audio, alignment = await asyncio.to_thread(_render, meta)
        except urllib.error.HTTPError as e:
            _give_back(n)
            detail = e.read().decode(errors="replace")[:200]
            log.warning("voice: ElevenLabs %s: %s", e.code, detail)
            if e.code in (401, 403):
                raise HTTPException(status_code=503, detail="Voice not configured — the voice service refused the key")
            if e.code == 429:
                raise HTTPException(status_code=429, detail="The voice service is busy — try again in a moment")
            raise HTTPException(status_code=502, detail=f"The voice service failed ({e.code})")
        except (urllib.error.URLError, OSError, ValueError) as e:
            _give_back(n)
            log.warning("voice: ElevenLabs call failed: %s", e)
            raise HTTPException(status_code=502, detail="The voice service did not answer")
        finally:
            _render_locks.pop(key, None)
        await asyncio.to_thread(_save, key, meta, audio, alignment)
        log.info("voice: rendered %s (%s, %s chars)", key, meta["role"], n)
    return _payload(key, meta)


# ------------------------------------------------------------------------------------------ routes
@router.get("/api/voice/status")
def status():
    on = configured()
    names = _voices["names"] if _voices["resolved"] else None
    pinned = len(list(PINNED_DIR.glob("*.mp3"))) if PINNED_DIR.exists() else 0
    return {
        "configured": on,
        "provider": "elevenlabs" if on else "browser",
        "model": model_id() if on else None,
        "voices": ({r: (names or {}).get(r) or ("custom" if os.getenv(f"ELEVENLABS_VOICE_{r.upper()}") else "default") for r in ROLES} if on else None),
        "languages": list(LANGS),
        "chars_left_today": chars_left() if on else 0,
        "daily_chars": daily_chars(),
        "pinned": pinned,
        "attribution": ATTRIBUTION,
    }


class SegmentIn(BaseModel):
    key: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")


@router.post("/api/voice/segment")
@limiter.limit("60/minute")
async def segment(request: Request, body: SegmentIn):
    return await ensure_audio(body.key)


@router.get("/api/voice/audio/{name}")
def audio(name: str):
    m = re.fullmatch(r"([0-9a-f]{32})\.mp3", name)
    p = _paths(m.group(1)) if m else None
    if p is None:
        raise HTTPException(status_code=404, detail="No audio for that segment")
    return FileResponse(p[0], media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


class DownloadIn(BaseModel):
    deck_key: str = Field(min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")
    lang: Literal["en", "es"] = "en"


def _chunks(text: str, limit: int = 84) -> list[tuple[int, int]]:
    """Caption chunks (start, end char offsets): sentence pieces of at most `limit` characters."""
    out, start = [], 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):  # break at a sentence end if there is one, else a clause, else a word
            cut = next((c for c in (text.rfind(sep, start, end) for sep in (". ", "; ", ": ", ", ")) if c > start + 24), -1)
            end = cut + 1 if cut > 0 else (text.rfind(" ", start, end) if text.rfind(" ", start, end) > start else end)
        out.append((start, end))
        start = end
        while start < len(text) and text[start] == " ":
            start += 1
    return out


def _vtt_time(t: float) -> str:
    h, rem = divmod(max(t, 0.0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:40] or "briefing"


def _build_files(deck: dict, lang: str) -> dict:
    """Write the transcript, the captions and (when every segment has audio) the joined MP3."""
    from bulletin import CHARS_PER_S  # noqa: PLC0415 — bulletin imports voice

    segs = [(s, seg) for s in deck["slides"] for seg in s["narration"][lang]]
    audio: dict[str, bytes] = {}
    timings: dict[str, dict] = {}
    for _, seg in segs:
        p = _paths(seg["key"])
        if p:
            audio[seg["key"]] = p[0].read_bytes()
            try:
                timings[seg["key"]] = json.loads(p[1].read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    missing = [seg["key"] for _, seg in segs if seg["key"] not in audio]
    fid = hashlib.sha256(f"{deck['deck_key']}|{lang}|{len(missing) == 0}".encode()).hexdigest()[:32]
    place = deck.get("title", {}).get("en", "").split("·")[-1]
    name = f"overload-briefing-{_slug(deck.get('region', ''))}-{_slug(place)}-{lang}"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # transcript
    local = deck.get("local", {}).get(lang, {}) if lang != "en" else {}
    lines = [deck["title"][lang], "", local.get("banner") or deck.get("banner", ""), local.get("credit") or deck.get("credit", ""),
             local.get("disclaimer") or deck.get("disclaimer", ""), ""]
    who = {"presenter": ("PRESENTER", "PRESENTADOR"), "analyst": ("ANALYST", "ANALISTA")}
    for s in deck["slides"]:
        lines.append(f"## {s['headline'][lang]}")
        for seg in s["narration"][lang]:
            lines.append(f"{who[seg['role']][0 if lang == 'en' else 1]}: {seg['text']}")
        lines.append("")
    lines.append(ATTRIBUTION if not missing else ("Audio: not rendered (browser voice)" if lang == "en" else "Audio: sin generar (voz del navegador)"))
    (CACHE_DIR / f"dl-{fid}.txt").write_text("\n".join(lines), encoding="utf-8")

    # captions (and the MP3, joined with the same pauses so the offsets line up)
    vtt = ["WEBVTT", ""]
    parts: list[bytes] = []
    t = 0.0
    first_audio = next(iter(audio.values()), b"")
    for idx, (s, seg) in enumerate(segs):
        text = seg["text"]
        tm = timings.get(seg["key"])
        dur = float(tm["duration_s"]) if tm else len(text) / CHARS_PER_S.get(lang, 14.5)
        words = tm["words"] if tm else None
        for a, b in _chunks(text):
            if words:
                # the words whose text starts inside this chunk
                pos, t0, t1 = 0, None, None
                for w_ in words:
                    at = text.find(w_[0], pos)
                    if at < 0:
                        continue
                    pos = at + len(w_[0])
                    if a <= at < b:
                        t0 = w_[1] if t0 is None else t0
                        t1 = w_[2]
                t0 = t0 if t0 is not None else dur * a / max(len(text), 1)
                t1 = t1 if t1 is not None else dur * b / max(len(text), 1)
            else:
                t0, t1 = dur * a / max(len(text), 1), dur * b / max(len(text), 1)
            vtt += [f"{_vtt_time(t + t0)} --> {_vtt_time(t + t1)}", text[a:b].strip(), ""]
        last_of_slide = idx + 1 == len(segs) or segs[idx + 1][0] is not s
        pause = HOLD_S if last_of_slide else GAP_S
        if not missing:
            parts.append(mp3_audio(audio[seg["key"]]))
            if idx + 1 < len(segs):
                parts.append(mp3_silence(first_audio, pause))
            dur = mp3_duration(audio[seg["key"]])
        t += dur + (pause if idx + 1 < len(segs) else 0)
    (CACHE_DIR / f"dl-{fid}.vtt").write_text("\n".join(vtt), encoding="utf-8")
    if not missing:
        (CACHE_DIR / f"dl-{fid}.mp3").write_bytes(b"".join(parts))
    (CACHE_DIR / f"dl-{fid}.name").write_text(name, encoding="utf-8")
    return {"mp3_url": None if missing else f"/api/voice/file/{fid}.mp3", "vtt_url": f"/api/voice/file/{fid}.vtt",
            "txt_url": f"/api/voice/file/{fid}.txt", "missing": missing, "filename": name, "duration_s": round(t, 1)}


@router.post("/api/voice/download")
@limiter.limit("10/minute")
async def download(request: Request, body: DownloadIn):
    from bulletin import deck_by_key  # noqa: PLC0415 — bulletin imports voice

    deck = deck_by_key(body.deck_key)
    if deck is None:
        raise HTTPException(status_code=409, detail="Script expired — fetch it again")
    if configured():  # render what the stage has not played yet (each render counts against the daily cap)
        keys = [seg["key"] for s in deck["slides"] for seg in s["narration"][body.lang] if _paths(seg["key"]) is None]
        for k in keys:
            try:
                await ensure_audio(k)
            except HTTPException as e:
                log.warning("voice: download could not render %s: %s", k, e.detail)
                break
    return await asyncio.to_thread(_build_files, deck, body.lang)


@router.get("/api/voice/file/{name}")
def file(name: str):
    m = re.fullmatch(r"([0-9a-f]{32})\.(mp3|vtt|txt)", name)
    if not m:
        raise HTTPException(status_code=404, detail="No such file")
    path = CACHE_DIR / f"dl-{m.group(1)}.{m.group(2)}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="That download expired — ask for it again")
    try:
        base = (CACHE_DIR / f"dl-{m.group(1)}.name").read_text(encoding="utf-8").strip() or "overload-briefing"
    except OSError:
        base = "overload-briefing"
    media = {"mp3": "audio/mpeg", "vtt": "text/vtt; charset=utf-8", "txt": "text/plain; charset=utf-8"}[m.group(2)]
    return FileResponse(path, media_type=media, filename=f"{base}.{m.group(2)}")

