"""Build plans (GridLock, owned by the gridlock track): neighboring utilities' public construction plans,
located, validated, and compared for coordination opportunities."""

from fastapi import APIRouter

router = APIRouter(tags=["gridlock"])
