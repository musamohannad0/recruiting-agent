from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

from recruiting_agent.models import Company, Job, JobReview, Match, Recommendation
from recruiting_agent.web.app import _match_rows


def test_match_list_uses_latest_score_and_job_level_review_state():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        company = Company(name="Example")
        session.add(company)
        session.commit()
        session.refresh(company)
        job = Job(
            company_id=company.id,
            external_id="1",
            title="Software Engineer",
            url="https://example.com/job",
            content_hash="content",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        now = datetime.now(timezone.utc)
        session.add(
            Match(
                job_id=job.id,
                content_hash="content",
                profile_hash="profile",
                score=95,
                recommendation=Recommendation.apply,
                created_at=now - timedelta(days=1),
            )
        )
        session.add(
            Match(
                job_id=job.id,
                content_hash="content",
                profile_hash="profile",
                score=55,
                recommendation=Recommendation.skip,
                created_at=now,
            )
        )
        session.add(JobReview(job_id=job.id, user_status="dismissed"))
        session.commit()

        rows = _match_rows(session, status="dismissed")

        assert len(rows) == 1
        assert rows[0][0].score == 55
        assert rows[0][0].user_status == "dismissed"
        assert _match_rows(session, min_score=60, status="all") == []
