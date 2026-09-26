from sqlalchemy import JSON, Float, ForeignKey, Integer, LargeBinary, String
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
