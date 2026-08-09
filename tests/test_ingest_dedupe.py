from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from recruiting_agent.connectors.base import JobPosting
from recruiting_agent.models import Company
from recruiting_agent.pipeline.ingest import upsert_jobs


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def company(session):
    c = Company(name="TestCo", ats_type="greenhouse", board_token="testco")
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def posting(external_id="1", title="BizOps Lead", description="desc"):
    return JobPosting(
        external_id=external_id,
        title=title,
        url=f"https://example.com/{external_id}",
        description_md=description,
        posted_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def test_new_then_unchanged(session, company):
    stats = upsert_jobs(session, company, [posting()])
    assert stats["new"] == 1
    stats = upsert_jobs(session, company, [posting()])
    assert stats == {"new": 0, "updated": 0, "unchanged": 1, "deactivated": 0}


def test_content_change_triggers_update(session, company):
    upsert_jobs(session, company, [posting()])
    stats = upsert_jobs(session, company, [posting(description="changed")])
    assert stats["updated"] == 1 and stats["new"] == 0


def test_vanished_posting_deactivated_and_reactivated(session, company):
    upsert_jobs(session, company, [posting("1"), posting("2", title="Strategy Ops")])
    stats = upsert_jobs(session, company, [posting("1")])
    assert stats["deactivated"] == 1
    stats = upsert_jobs(session, company, [posting("1"), posting("2", title="Strategy Ops")])
    assert stats["deactivated"] == 0
    from sqlmodel import select

    from recruiting_agent.models import Job

    jobs = session.exec(select(Job)).all()
    assert all(j.is_active for j in jobs)
