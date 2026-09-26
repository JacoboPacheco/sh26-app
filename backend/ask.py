"""Ask Overload (owned by the ask track): your own questions about a scenario, answered by Gemini from the computed facts."""

from fastapi import APIRouter

router = APIRouter(tags=["ask"])
