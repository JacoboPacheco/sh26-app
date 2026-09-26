"""Pre-render the hero briefing's audio into backend/demo/voice/ (committed), so the demo never waits
on the voice service and survives a restart or a spent daily quota.

Run once ELEVENLABS_API_KEY is in backend/.env (from the repo root):
    backend/venv/Scripts/python backend/demo/prerender_voice.py              # short deck, EN + ES, templates + Gemini
    backend/venv/Scripts/python backend/demo/prerender_voice.py --full       # also the full deck
    backend/venv/Scripts/python backend/demo/prerender_voice.py --no-ai      # templates only (no Gemini call)

Cost: about 800 (EN) + 900 (ES) characters per short deck and variant; the full deck about 2,000 + 2,300.
It also pins Gemini's prose for these decks (voice/ai_bodies.json), so after a restart the AI deck has
the same text, hence the same voice keys, hence this audio. Re-run it after changing the templates.
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
os.environ.setdefault("VOICE_DAILY_CHARS", "20000")  # this run only: the server's daily cap is separate

import bulletin  # noqa: E402
import voice  # noqa: E402

HERO = {"lat": 26.64, "lon": -81.87, "mw": 1500}


async def pin(length: str, ai: bool) -> tuple[dict, list[str]]:
    deck, report = await bulletin.build_deck(bulletin.DeckIn(**HERO, ai=ai, length=length))
    keys = []
    for lang in voice.LANGS:
        for s in deck["slides"]:
            for seg in s["narration"][lang]:
                await voice.ensure_audio(seg["key"])  # renders into the cache (or finds it there)
                keys.append(seg["key"])
    voice.PINNED_DIR.mkdir(parents=True, exist_ok=True)
    for k in keys:
        for ext in (".mp3", ".align.json", ".json"):
            src = voice.CACHE_DIR / f"{k}{ext}"
            if src.exists():
                shutil.copyfile(src, voice.PINNED_DIR / f"{k}{ext}")
    entry = bulletin.pinned_ai_entry(report, length) if ai else None
    return {"length": length, "ai": deck["ai"], "deck_key": deck["deck_key"], "report_key": report.get("key"),
            "segments": len(keys), "chars": deck["total_chars"], "ai_entry": entry}, keys


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--full", action="store_true", help="also pin the full deck")
    ap.add_argument("--no-ai", action="store_true", help="templates only (no Gemini call)")
    args = ap.parse_args()
    if not voice.configured():
        sys.exit("ELEVENLABS_API_KEY is not set in backend/.env — nothing to render.")
    runs = [("short", False)] + ([] if args.no_ai else [("short", True)])
    if args.full:
        runs += [("full", False)] + ([] if args.no_ai else [("full", True)])
    ai_path = voice.PINNED_DIR / "ai_bodies.json"
    pinned_ai = json.loads(ai_path.read_text(encoding="utf-8")) if ai_path.exists() else {}
    manifest = []
    for length, ai in runs:
        info, keys = await pin(length, ai)
        if info.pop("ai_entry", None) is not None:
            pinned_ai[f"{bulletin.VERSION}|{info['report_key']}|{length}"] = bulletin.pinned_ai_entry(
                {"key": info["report_key"]}, length)
        manifest.append(info)
        print(f"pinned {length} {'AI' if ai else 'template'} deck: {info['segments']} segments, {info['chars']} chars, ai={info['ai']}")
    ai_path.write_text(json.dumps(pinned_ai, ensure_ascii=False, indent=1), encoding="utf-8")
    (voice.PINNED_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"characters left today (this run's counter): {voice.chars_left()}")


if __name__ == "__main__":
    asyncio.run(main())
