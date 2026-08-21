from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from recruiting_agent import db
from recruiting_agent.models import JobReview


def _legacy_engine():
    """A database as it existed before triage state moved off Match.

    Built with raw SQL rather than the current models: the point of the migration is
    to upgrade a schema the code no longer describes, so the fixture must not be
    regenerated from today's model definitions.
    """
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.exec(text("ALTER TABLE matches ADD COLUMN user_status VARCHAR"))
        session.exec(text("CREATE INDEX ix_matches_user_status ON matches (user_status)"))
        for match_id, job_id, status, created in (
            (1, 42, "dismissed", "2026-08-01 10:00:00"),
            (2, 42, "saved", "2026-08-09 10:00:00"),  # newest wins
            (3, 43, None, "2026-08-09 10:00:00"),
        ):
            session.exec(
                text(
                    "INSERT INTO matches (id, job_id, content_hash, profile_hash, score,"
                    " recommendation, reasoning, red_flags, seniority_fit, location_fit,"
                    " model, prompt_version, user_status, created_at)"
                    " VALUES (:id, :job, 'content', 'profile', 80, 'apply', '', '[]', '',"
                    " '', '', '', :status, :created)"
                ).bindparams(id=match_id, job=job_id, status=status, created=created)
            )
        session.commit()
    return engine


def test_versioned_migration_backfills_job_review(monkeypatch):
    engine = _legacy_engine()
    monkeypatch.setattr(db, "engine", engine)

    db._run_migrations()
    db._run_migrations()  # must be idempotent

    with Session(engine) as session:
        reviews = session.exec(select(JobReview)).all()
        assert len(reviews) == 1
        assert reviews[0].job_id == 42
        # The most recent triage decision survives, not the oldest.
        assert reviews[0].user_status == "saved"


def test_migrations_add_accounting_columns_and_indexes(monkeypatch):
    engine = _legacy_engine()
    monkeypatch.setattr(db, "engine", engine)

    db._run_migrations()

    with Session(engine) as session:
        for table in ("agent_cycles", "agent_actions"):
            columns = {
                row[1] for row in session.exec(text(f"PRAGMA table_info({table})")).all()
            }
            assert {"cost_usd", "llm_calls", "input_tokens", "output_tokens", "cached_tokens"} <= columns
        indexes = {
            row[0]
            for row in session.exec(
                text("SELECT name FROM sqlite_master WHERE type='index'")
            ).all()
        }
        # Filtered on every match query, so it earns an index...
        assert "ix_matches_profile_hash" in indexes
        assert "ix_prefilters_profile_hash" in indexes
        # ...while the retired column's index is dropped.
        assert "ix_matches_user_status" not in indexes


def test_migrations_run_clean_on_a_fresh_database(monkeypatch):
    """A new install has no legacy columns; every migration must still be a no-op."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "engine", engine)

    db._run_migrations()
    db._run_migrations()

    with Session(engine) as session:
        applied = {
            row[0] for row in session.exec(text("SELECT version FROM schema_migrations")).all()
        }
        assert {1, 2, 3, 4} <= applied
        columns = {row[1] for row in session.exec(text("PRAGMA table_info(matches)")).all()}
        assert "user_status" not in columns
