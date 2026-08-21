from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from recruiting_agent.models import Company, Job, Match, Recommendation
from recruiting_agent.web import app as web_module
from recruiting_agent.workspace import CandidateWorkspace


def test_matches_exposes_rationale_and_evaluation_record(tmp_path: Path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session:
            yield session

    workspace = CandidateWorkspace(tmp_path)
    workspace._write("manifest.yaml", "status: ready\nversion: 1\nharness_hash: active-harness\n")
    with sessions() as session:
        company = Company(name="Example AI")
        session.add(company)
        session.commit()
        session.refresh(company)
        job = Job(
            company_id=company.id,
            external_id="applied-ai",
            title="Applied AI Engineer",
            url="https://example.com/job",
            content_hash="content",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        session.add(
            Match(
                job_id=job.id,
                content_hash="content",
                profile_hash="active-harness",
                score=93,
                recommendation=Recommendation.apply,
                reasoning="Direct match to customer-facing applied AI work.",
                seniority_fit="Strong fit",
                location_fit="New York is approved",
                model="test-model",
                prompt_version="test-prompt",
            )
        )
        session.commit()

    monkeypatch.setattr(web_module, "workspace", workspace)
    monkeypatch.setattr(web_module, "get_session", sessions)
    client = TestClient(web_module.app)

    page = client.get("/matches?status=all&min_score=0")

    assert page.status_code == 200
    assert "Score ↓" in page.text
    assert "Why this score" in page.text
    assert "Direct match to customer-facing applied AI work." in page.text
    assert "Harness active-h" in page.text
    assert "External trace not configured" in page.text
