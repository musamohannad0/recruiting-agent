from contextlib import contextmanager

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from recruiting_agent.agents.llm import LLMResult
from recruiting_agent.agents.role_discovery import run_role_discovery
from recruiting_agent.models import Company, Job, JobSource


class FakeRuntime:
    async def run(self, task, prompt, **kwargs):
        return LLMResult(
            data={
                "postings": [
                    {
                        "title": "Software Engineer, Infrastructure",
                        "location": "New York, NY",
                        "department": "Engineering",
                        "url": "https://careers.google.com/jobs/123",
                        "apply_url": None,
                        "description_md": "Build reliable infrastructure.",
                    },
                    {
                        "title": "Unverified aggregator copy",
                        "location": None,
                        "department": None,
                        "url": "https://jobs.example.net/copied",
                        "apply_url": None,
                        "description_md": "Must be rejected by domain validation.",
                    },
                ],
                "coverage_note": "Scoped search; may be incomplete.",
            },
            trace_id=None,
            cost_usd=0,
        )


@pytest.mark.asyncio
async def test_agentic_source_keeps_only_verified_company_domain_jobs():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        company = Company(name="Google", careers_url="https://careers.google.com")
        session.add(company)
        session.commit()
        session.refresh(company)
        session.add(
            JobSource(
                company_id=company.id,
                source_type="career_site",
                is_authoritative=False,
            )
        )
        session.commit()

    @contextmanager
    def sessions():
        with Session(engine) as session:
            yield session

    stats = await run_role_discovery(FakeRuntime(), sessions)

    assert stats == {"sources_checked": 1, "new": 1, "updated": 0, "errors": 0}
    with sessions() as session:
        jobs = session.exec(select(Job)).all()
        assert [job.title for job in jobs] == ["Software Engineer, Infrastructure"]
