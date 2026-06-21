from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ROOT_DIR / ".env")
load_dotenv()


def _default_database_url() -> str:
    """Pick the SQLite location.

    The packaged desktop app sets ``AETHER_DATA_DIR`` to a per-user writable
    directory; keep the database alongside that storage so a read-only install
    location (e.g. Program Files) never holds mutable state. In development we
    keep the historical repo-relative file.
    """
    data_dir = os.getenv("AETHER_DATA_DIR")
    if data_dir:
        db_path = (Path(data_dir).expanduser() / "aether_studio.db").resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.as_posix()}"
    return "sqlite:///./aether_studio.db"


DATABASE_URL = os.getenv("DATABASE_URL") or _default_database_url()

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
