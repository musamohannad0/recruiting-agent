from sqlmodel import Session, SQLModel, create_engine, select

from recruiting_agent import db
from recruiting_agent.models import JobReview, Match, Recommendation


def test_versioned_migration_backfills_job_review(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Match(
                job_id=42,
                content_hash="content",
                profile_hash="profile",
                score=80,
                recommendation=Recommendation.apply,
                user_status="saved",
            )
        )
        session.commit()
    monkeypatch.setattr(db, "engine", engine)

    db._run_migrations()
    db._run_migrations()

    with Session(engine) as session:
        review = session.exec(select(JobReview)).one()
        assert review.job_id == 42
        assert review.user_status == "saved"
