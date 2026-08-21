from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import event, inspect as sqlalchemy_inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from .models import CoordinatorLease, DEFAULT_SETTINGS, JobReview, Setting
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
            # Preserve the newest historical triage state before Match.user_status is
            # retired. Addressed with raw SQL, not the ORM: `user_status` is gone from
            # the model, and a migration must keep working against the schema as it was.
            match_columns = {
                column["name"]
                for column in sqlalchemy_inspect(session.connection()).get_columns("matches")
            }
            if "user_status" in match_columns:
                rows = session.exec(
                    text(
                        "SELECT job_id, user_status FROM matches "
                        "WHERE user_status IS NOT NULL ORDER BY created_at DESC"
                    )
                ).all()
                seen_jobs: set[int] = set()
                for job_id, user_status in rows:
                    if job_id in seen_jobs:
                        continue
                    seen_jobs.add(job_id)
                    existing = session.exec(
                        select(JobReview).where(JobReview.job_id == job_id)
                    ).first()
                    if existing is None:
                        session.add(JobReview(job_id=job_id, user_status=user_status))
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
        if 3 not in applied:
            # Per-action and per-cycle spend accounting, so a run's cost is inspectable
            # instead of only being printed once to a terminal that has since scrolled.
            accounting = {
                "cost_usd": "REAL NOT NULL DEFAULT 0",
                "llm_calls": "INTEGER NOT NULL DEFAULT 0",
                "input_tokens": "INTEGER NOT NULL DEFAULT 0",
                "output_tokens": "INTEGER NOT NULL DEFAULT 0",
                "cached_tokens": "INTEGER NOT NULL DEFAULT 0",
            }
            for table in ("agent_cycles", "agent_actions"):
                # Re-inspect per table: the previous iteration may have altered the schema.
                existing = {
                    column["name"]
                    for column in sqlalchemy_inspect(session.connection()).get_columns(table)
                }
                for name, ddl in accounting.items():
                    if name not in existing:
                        session.exec(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            session.exec(
                text(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES (3, CURRENT_TIMESTAMP)"
                )
            )
        if 4 not in applied:
            # profile_hash is filtered on every match query and every skip-set build;
            # user_status on matches is retired and its index only costs write time.
            for statement in (
                "CREATE INDEX IF NOT EXISTS ix_matches_profile_hash ON matches (profile_hash)",
                "CREATE INDEX IF NOT EXISTS ix_matches_created_at ON matches (created_at)",
                "CREATE INDEX IF NOT EXISTS ix_prefilters_profile_hash ON prefilters (profile_hash)",
                "CREATE INDEX IF NOT EXISTS ix_feedback_created_at ON feedback (created_at)",
                "DROP INDEX IF EXISTS ix_matches_user_status",
            ):
                session.exec(text(statement))
            session.exec(
                text(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES (4, CURRENT_TIMESTAMP)"
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
