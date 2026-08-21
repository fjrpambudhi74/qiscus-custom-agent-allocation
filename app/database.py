from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all() only creates tables that don't exist yet - it never
    alters an existing table's columns. A deployed SQLite DB predates the
    `attempts` column added to QueueEntry, so add it by hand if missing."""
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(queueentry)")).fetchall()}
        if cols and "attempts" not in cols:
            conn.execute(text("ALTER TABLE queueentry ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"))
            conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
