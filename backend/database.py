import logging
import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def normalize(url: str) -> str:
    # Render/Supabase/Heroku hand out "postgres://" or "postgresql://"; SQLAlchemy 2
    # needs the driver spelled out to use psycopg 3.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


# The local file lives next to this module, whatever directory the server was started from.
DATABASE_URL = normalize(os.getenv("DATABASE_URL", f"sqlite:///{(Path(__file__).parent / 'app.db').as_posix()}"))
IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    # hosted Postgres drops idle connections; pre_ping reconnects instead of erroring
    pool_pre_ping=not IS_SQLITE,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def add_missing_columns() -> None:
    """`create_all` creates missing tables but never alters existing ones. When a
    model gains a column mid-event, add it (nullable) so the deployed Postgres —
    and a dev server with an old app.db — keep working after a redeploy/restart.
    Existing rows get NULL in the new column. Renames and type changes are not
    handled: those need a fresh database."""
    log = logging.getLogger("uvicorn.error")
    with engine.begin() as conn:
        insp = inspect(conn)
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
                conn.execute(text(ddl))
                log.warning("schema: added column %s.%s (existing rows have NULL there)", table.name, col.name)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
