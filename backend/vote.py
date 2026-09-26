"""Before the vote (owned by the vote track): a community looks up a real proposed data center and learns
what it could do to a grid (simulated, labeled synthetic), what it could cost and who pays, what to ask
before approving, and where to speak."""

from fastapi import APIRouter

router = APIRouter(tags=["vote"])
