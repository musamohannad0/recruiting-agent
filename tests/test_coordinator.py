from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from recruiting_agent.coordinator import CoordinatorOperations, SearchCoordinator
from recruiting_agent.models import AgentAction, AgentCycle, CoordinatorLease
from recruiting_agent.workspace import CandidateWorkspace


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CoordinatorLease(id=1))
        session.commit()

    @contextmanager
    def factory():
        with Session(engine) as session:
            yield session

    return factory


def ready_workspace(tmp_path: Path, scope: str = "strict") -> CandidateWorkspace:
    ws = CandidateWorkspace(tmp_path)
    ws._write("manifest.yaml", "status: ready\nharness_hash: test-harness\n")
    ws._write("policy/companies.yaml", f"scope: {scope}\n")
    ws._write(
        "policy/cadence.yaml",
        "ats_ingest_hours: 2\ncareer_site_discovery_hours: 24\ncompany_discovery_days: 7\n"
        "memory_consolidation_days: 7\nfeedback_recalibration_threshold: 5\n",
    )
    return ws


def fake_operations(calls: list[str]) -> CoordinatorOperations:
    def operation(name):
        async def run():
            calls.append(name)
            return {"name": name}

        return run

    return CoordinatorOperations(
        ingest=operation("ingest"),
        match=operation("match"),
        discover=operation("discover"),
        scout=operation("scout"),
        consolidate_memory=operation("memory"),
    )


@pytest.mark.asyncio
async def test_cycle_is_idempotent_within_cadence_bucket(tmp_path, session_factory):
    calls = []
    coordinator = SearchCoordinator(
        ready_workspace(tmp_path),
        session_factory,
        fake_operations(calls),
        now=lambda: datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        initialize=lambda: None,
    )

    first = await coordinator.run_cycle("test")
    second = await coordinator.run_cycle("test")

    assert first["status"] == "success"
    assert second["status"] == "success"
    assert calls == ["ingest", "match", "scout", "memory"]
    with session_factory() as session:
        assert len(session.exec(select(AgentCycle)).all()) == 2
        assert all(action.attempts == 1 for action in session.exec(select(AgentAction)).all())


@pytest.mark.asyncio
async def test_held_lease_skips_overlapping_cycle(tmp_path, session_factory):
    coordinator = SearchCoordinator(
        ready_workspace(tmp_path),
        session_factory,
        fake_operations([]),
        now=lambda: datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        initialize=lambda: None,
    )
    assert coordinator._acquire_lease("already-running")

    result = await coordinator.run_cycle("overlap")

    assert result == {"status": "skipped", "reason": "coordinator lease held"}
    coordinator._release_lease("already-running")


@pytest.mark.asyncio
async def test_failed_action_retries_from_checkpoint(tmp_path, session_factory):
    attempts = 0

    async def flaky_ingest():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return {"recovered": True}

    operations = fake_operations([])
    operations.ingest = flaky_ingest
    coordinator = SearchCoordinator(
        ready_workspace(tmp_path),
        session_factory,
        operations,
        now=lambda: datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        initialize=lambda: None,
    )

    with pytest.raises(RuntimeError, match="temporary failure"):
        await coordinator.run_cycle("test")
    result = await coordinator.run_cycle("retry")

    assert result["status"] == "success"
    with session_factory() as session:
        ingest = session.exec(select(AgentAction).where(AgentAction.kind == "ingest")).one()
        assert ingest.attempts == 2
        assert ingest.status == "success"
