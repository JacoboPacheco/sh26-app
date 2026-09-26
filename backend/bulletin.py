"""AI emergency bulletin (owned by the bulletin track): Gemini with a templated fallback."""

from fastapi import APIRouter

router = APIRouter(tags=["bulletin"])
