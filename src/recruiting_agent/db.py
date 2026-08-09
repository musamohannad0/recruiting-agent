from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from .models import DEFAULT_SETTINGS, Setting
from .settings import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    # WAL lets the dashboard read while a pipeline run writes.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for key, value in DEFAULT_SETTINGS.items():
            if session.get(Setting, key) is None:
                session.add(Setting(key=key, value=value))
        session.commit()


@contextmanager
def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def get_setting(session: Session, key: str) -> str:
    row = session.get(Setting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key, "")
    return row.value


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    session.commit()
