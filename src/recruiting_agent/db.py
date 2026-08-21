from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import event, inspect as sqlalchemy_inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from .models import CoordinatorLease, DEFAULT_SETTINGS, JobReview, Match, Setting
from .settings import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
_connect_args = {"check_same_thread": False} if settings.effective_database_url.startswith("sqlite") else {}
engine = create_engine(settings.effective_database_url, connect_args=_connect_args)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    if not settings.effective_database_url.startswith("sqlite"):
        return
    # WAL lets the dashboard read while a pipeline run writes.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _run_migrations()
    with Session(engine) as session:
        for key, value in DEFAULT_SETTINGS.items():
            if session.get(Setting, key) is None:
                session.add(Setting(key=key, value=value))
        if session.get(CoordinatorLease, 1) is None:
            session.add(CoordinatorLease(id=1))
        session.commit()


def _run_migrations() -> None:
    """Small versioned migrations for the self-hosted SQLite/Postgres database."""
    with Session(engine) as session:
        session.exec(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at VARCHAR NOT NULL)"
            )
        )
        applied = {
            row[0]
            for row in session.exec(text("SELECT version FROM schema_migrations")).all()
        }
        if 1 not in applied:
            # Preserve the newest historical triage state before Match.user_status is retired.
            seen_jobs: set[int] = set()
            matches = session.exec(
                select(Match)
                .where(Match.user_status.is_not(None))
                .order_by(Match.created_at.desc())
            ).all()
            for match in matches:
                if match.job_id in seen_jobs:
                    continue
                seen_jobs.add(match.job_id)
                existing_review = session.exec(
                    select(JobReview).where(JobReview.job_id == match.job_id)
                ).first()
                if existing_review is None:
                    session.add(JobReview(job_id=match.job_id, user_status=match.user_status))
            session.exec(
                text(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES (1, CURRENT_TIMESTAMP)"
                )
            )
        if 2 not in applied:
            match_columns = {
                column["name"]
                for column in sqlalchemy_inspect(session.connection()).get_columns("matches")
            }
            if "cost_usd" not in match_columns:
                session.exec(text("ALTER TABLE matches ADD COLUMN cost_usd REAL"))
            session.exec(
                text(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES (2, CURRENT_TIMESTAMP)"
                )
            )
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
