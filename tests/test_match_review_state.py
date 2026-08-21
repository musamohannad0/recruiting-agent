from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

from recruiting_agent.models import Company, Job, JobReview, Match, Recommendation
from recruiting_agent.web import app as web_module
from recruiting_agent.workspace import CandidateWorkspace


def test_match_list_uses_latest_score_and_job_level_review_state(tmp_path, monkeypatch):
    monkeypatch.setattr(web_module, "workspace", CandidateWorkspace(tmp_path))
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
        stronger_job = Job(
            company_id=company.id,
            external_id="2",
            title="Applied AI Engineer",
            url="https://example.com/applied-ai",
            content_hash="content-2",
        )
        session.add(stronger_job)
        session.commit()
        session.refresh(stronger_job)
        session.add(
            Match(
                job_id=stronger_job.id,
                content_hash="content-2",
                profile_hash="profile",
                score=82,
                recommendation=Recommendation.apply,
                reasoning="Direct applied AI fit.",
                created_at=now - timedelta(hours=2),
            )
        )
        session.commit()

        rows = web_module._match_rows(session, status="dismissed")

        assert len(rows) == 1
        assert rows[0][0].score == 55
        assert rows[0][0].user_status == "dismissed"
        assert web_module._match_rows(session, min_score=60, status="dismissed") == []

        ranked = web_module._match_rows(session, min_score=0, status="all")
        assert [row[0].score for row in ranked] == [82, 55]
