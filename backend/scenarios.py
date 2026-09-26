"""Saved scenarios: a named data-center drop (site + size) the demo replays with one click.

Per-user (the demo account in practice), owner-only, validated like any user input. Each save
stores a small what-if summary so the list can say "7 over limit · 217 MW headroom" without
re-solving.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, StringConstraints, field_validator
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from grid import check_site, site_summary
from limiter import limiter
from models import Scenario, User

router = APIRouter(tags=["scenarios"])

# strip_whitespace runs before min_length, so "   " is rejected, not saved as "".
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)]


class ScenarioIn(BaseModel):
    name: Name
    lat: float
    lon: float
    mw: float  # float, not int: check_site turns NaN/huge values into a readable 422 (an int overflows it)
    note: Note = ""

    @field_validator("mw", "lat", "lon", mode="before")
    @classmethod
    def _no_huge_ints(cls, v):
        # a 400-digit JSON integer would overflow float() later; refuse it here as a normal 422
        if isinstance(v, int) and abs(v) > 10**9:
            raise ValueError("number out of range")
        return v


def serialize(sc: Scenario) -> dict:
    # Columns added mid-event arrive as NULL on old rows — always read them with a default.
    return {
        "id": sc.id,
        "name": sc.name,
        "lat": sc.lat,
        "lon": sc.lon,
        "mw": sc.mw,
        "note": sc.note or "",
        "summary": sc.summary or None,
    }


@router.get("/api/scenarios")
def list_scenarios(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Scenario).filter(Scenario.user_id == user.id).order_by(Scenario.id).all()
    return [serialize(sc) for sc in rows]


@router.post("/api/scenarios")
@limiter.limit("30/minute")
def create_scenario(
    request: Request, body: ScenarioIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    check_site(body.lat, body.lon, body.mw)
    res = site_summary(body.lat, body.lon, body.mw)
    summary = {"overloaded": len(res["overloaded"]), "headroom_mw": res["headroom_mw"]}
    sc = Scenario(
        name=body.name, lat=body.lat, lon=body.lon, mw=round(body.mw), note=body.note, summary=summary, user_id=user.id
    )
    db.add(sc)
    db.commit()
    db.refresh(sc)
    return serialize(sc)


@router.delete("/api/scenarios/{scenario_id}")
def delete_scenario(
    scenario_id: Annotated[int, Path(ge=1, le=2**31 - 1)],  # bounded: a huge id overflows the database driver
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Filtering by owner turns "someone else's scenario" into a plain 404 — no information leak.
    sc = db.query(Scenario).filter(Scenario.id == scenario_id, Scenario.user_id == user.id).first()
    if sc is None:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(sc)
    db.commit()
    return {"ok": True}
