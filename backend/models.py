from sqlalchemy import JSON, Boolean, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)


class Upload(Base):
    # Files live in the database, not on disk, so they survive redeploys on hosts
    # with no persistent filesystem (Render free tier). 5MB max each — see uploads.py.
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    content_type: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class Item(Base):
    # EXAMPLE table for items.py (template scaffolding — remove with it).
    # The shape to copy: everything but the key fields is `X | None`, because a
    # column added mid-event is created on startup as nullable and old rows get
    # NULL there. Lists go in JSON (works on SQLite and Postgres); no Enum columns.
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(String(2000), default="")
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    ai_fallback: Mapped[bool | None] = mapped_column(Boolean, default=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
