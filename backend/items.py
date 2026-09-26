"""
EXAMPLE FEATURE — template scaffolding, not project code. It exists so the first
real feature can copy a working shape:

    model in models.py  ->  this router  ->  smoke checks in smoke_test.py
    ->  demo rows in seed.py  ->  the panel in frontend/src/ItemsPanel.jsx

It shows: auth on every route, a per-visitor rate limit on the POST, an AI call
that falls back instead of failing (and tells the UI it did), owner-only access,
and input validation. Remove it before milestone 1 — all of: `Item` in models.py,
this file, its `include_router` line in main.py, its checks in smoke_test.py, its
rows in seed_project_data, ItemsPanel.jsx and its use in App.jsx.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from limiter import limiter
from llm import complete_json
from models import Item, User

router = APIRouter(tags=["items"])

NO_TAGS: dict = {"tags": []}

# strip_whitespace runs before min_length, so "   " is rejected, not saved as "".
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class ItemIn(BaseModel):
    title: Title
    notes: Notes = ""


def serialize(item: Item) -> dict:
    # Columns added mid-event arrive as NULL on old rows — always read them with a default.
    return {
        "id": item.id,
        "title": item.title,
        "notes": item.notes or "",
        "tags": list(item.tags or []),
        "fallback": bool(item.ai_fallback),
    }


async def suggest_tags(title: str, notes: str) -> tuple[list[str], bool]:
    """Up to 3 short tags from the AI. ([], True) when it's unconfigured, out of
    quota, unreachable, slow, or answered with the wrong shape — the item still saves."""
    data, offline = await complete_json(
        f"Suggest up to 3 short lowercase tags for this item.\nTitle: {title}\nNotes: {notes}\n"
        'Answer as {"tags": ["..."]}.',
        fallback=NO_TAGS,
        timeout=10,  # a demo-path call waits seconds, not the 60 s default
    )
    raw = data.get("tags") if isinstance(data, dict) else None
    if offline or not isinstance(raw, list):
        return [], True
    tags = [str(t).strip().lower()[:24] for t in raw if str(t).strip()]
    return list(dict.fromkeys(tags))[:3], False  # de-duplicated, capped


@router.get("/api/items")
def list_items(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    items = db.query(Item).filter(Item.user_id == user.id).order_by(Item.id.desc()).all()
    return [serialize(it) for it in items]


@router.post("/api/items")
@limiter.limit("30/minute")
async def create_item(
    request: Request, body: ItemIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    user_id = user.id
    # Release the pooled database connection before the slow AI call. Holding it
    # would let ~15 waiting requests exhaust the pool and take the whole app down
    # (including /api/health). Keep this line in every route that awaits the AI.
    db.rollback()
    tags, fallback = await suggest_tags(body.title, body.notes)
    item = Item(title=body.title, notes=body.notes, tags=tags, ai_fallback=fallback, user_id=user_id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return serialize(item)


@router.delete("/api/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Filtering by owner turns "someone else's item" into a plain 404 — no information leak.
    item = db.query(Item).filter(Item.id == item_id, Item.user_id == user.id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(item)
    db.commit()
    return {"ok": True}
