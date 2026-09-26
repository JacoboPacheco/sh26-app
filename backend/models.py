from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, LargeBinary, String
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


class Scenario(Base):
    # A saved data-center drop (site + size) the demo can replay. Everything but the key
    # fields is `X | None`: a column added mid-event arrives as NULL on old rows.
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    mw: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(280), default="")
    summary: Mapped[dict | None] = mapped_column(JSON, default=None)  # last what-if: overloads, headroom
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # The Library (scenarios.py), added Sat 03:00 — all nullable: old rows have NULL here.
    # case_json: the full case (grid.CaseIn: region, campuses, load level, lines out, upgrades, firm);
    # NULL on an old row means {region: FL, lat, lon, mw}. Named case_json because CASE is SQL.
    case_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    result: Mapped[dict | None] = mapped_column(JSON, default=None)  # what the case does, computed server-side
    parent_id: Mapped[int | None] = mapped_column(Integer, default=None, index=True)  # a version: its original
    # A random, url-safe public link id. unique= only takes on a fresh database (ALTER TABLE ADD
    # COLUMN can't add the constraint), so scenarios.py also checks before it hands one out.
    share_slug: Mapped[str | None] = mapped_column(String(32), default=None, unique=True, index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)  # naive UTC
