"""Incident briefing engine (owned by the briefing-engine track): timeline, root cause, verified fixes, no-fix + restoration plan, catastrophe presets."""

from fastapi import APIRouter

router = APIRouter(tags=["briefing"])
