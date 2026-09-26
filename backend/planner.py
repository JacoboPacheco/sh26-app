"""Planner: a Gemini agent that calls the simulator, with a deterministic fallback (owned by the planner track)."""

from fastapi import APIRouter

router = APIRouter(tags=["planner"])
