from contextlib import contextmanager
import asyncio
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
        probe=operation("probe"),
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
    assert calls == ["probe", "ingest", "match", "scout", "memory"]
    with session_factory() as session:
        assert len(session.exec(select(AgentCycle)).all()) == 2
        assert all(action.attempts == 1 for action in session.exec(select(AgentAction)).all())


@pytest.mark.asyncio
async def test_cycle_waits_for_candidate_activation(tmp_path, session_factory):
    calls = []
    coordinator = SearchCoordinator(
        CandidateWorkspace(tmp_path),
        session_factory,
        fake_operations(calls),
        initialize=lambda: None,
    )

    result = await coordinator.run_cycle("scheduled")

    assert result == {"status": "skipped", "reason": "candidate onboarding incomplete"}
    assert calls == []


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
async def test_long_action_renews_lease_and_blocks_overlap(tmp_path, session_factory):
    gate = asyncio.Event()

    async def slow_probe():
        await gate.wait()
        return {"ok": True}

    first_operations = fake_operations([])
    first_operations.probe = slow_probe
    first = SearchCoordinator(
        ready_workspace(tmp_path),
        session_factory,
        first_operations,
        lease_minutes=0.001,
        lease_heartbeat_seconds=0.01,
        initialize=lambda: None,
    )
    second = SearchCoordinator(
        ready_workspace(tmp_path),
        session_factory,
        fake_operations([]),
        lease_minutes=0.001,
        lease_heartbeat_seconds=0.01,
        initialize=lambda: None,
    )

    running = asyncio.create_task(first.run_cycle("long-running"))
    await asyncio.sleep(0.09)  # Past the original 60 ms lease, while heartbeats continue.

    overlap = await second.run_cycle("overlap")

    assert overlap == {"status": "skipped", "reason": "coordinator lease held"}
    gate.set()
    assert (await running)["status"] == "success"


@pytest.mark.asyncio
async def test_failed_action_retries_from_checkpoint(tmp_path, session_factory):
    attempts = 0

    async def flaky_ingest():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return {"recovered": True}

    calls: list[str] = []
    operations = fake_operations(calls)
    operations.ingest = flaky_ingest
    coordinator = SearchCoordinator(
        ready_workspace(tmp_path),
        session_factory,
        operations,
        now=lambda: datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        initialize=lambda: None,
    )

    first = await coordinator.run_cycle("test")

    # One broken stage costs that stage, not the cycle: matching still runs.
    assert first["status"] == "partial"
    assert "match" in calls and "scout" in calls
    assert first["actions"]["ingest"]["status"] == "failed"
    assert first["actions"]["match"]["status"] == "success"

    result = await coordinator.run_cycle("retry")

    assert result["status"] == "success"
    with session_factory() as session:
        ingest = session.exec(select(AgentAction).where(AgentAction.kind == "ingest")).one()
        assert ingest.attempts == 2
        assert ingest.status == "success"
        cycles = session.exec(select(AgentCycle).order_by(AgentCycle.id)).all()
        assert [cycle.status for cycle in cycles] == ["partial", "success"]
        assert "Ingest" in (cycles[0].error or "")


@pytest.mark.asyncio
async def test_memory_consolidation_writes_prose_not_json(tmp_path, session_factory):
    """The journal is fed back to agents as context, so it must read as sentences."""
    operations = fake_operations([])
    operations.consolidate_memory = None  # exercise the coordinator's own consolidation
    coordinator = SearchCoordinator(
        ready_workspace(tmp_path),
        session_factory,
        operations,
        now=lambda: datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        initialize=lambda: None,
    )

    await coordinator.run_cycle("test")
    journal = (tmp_path / "memory" / "search-journal.md").read_text()

    assert "# Search journal" in journal
    # No serialised payloads: no braces, no quoted keys, no JSON punctuation.
    assert "{" not in journal and "}" not in journal
    assert '":' not in journal
    assert "Totals over this window" in journal


@pytest.mark.asyncio
async def test_cycle_records_what_it_spent(tmp_path, session_factory):
    from recruiting_agent.agents.llm import LLMResult, current_usage

    async def ingest_that_calls_a_model():
        usage = current_usage()
        assert usage is not None, "actions must run inside a usage-tracking scope"
        usage.add(LLMResult(data={}, trace_id=None, cost_usd=0.25, input_tokens=900,
                            output_tokens=100, cache_read_tokens=4_000))
        return {"new": 3}

    operations = fake_operations([])
    operations.ingest = ingest_that_calls_a_model
    coordinator = SearchCoordinator(
        ready_workspace(tmp_path),
        session_factory,
        operations,
        now=lambda: datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        initialize=lambda: None,
    )

    result = await coordinator.run_cycle("test")

    assert result["cost_usd"] == pytest.approx(0.25)
    assert result["llm_calls"] == 1
    assert result["cached_tokens"] == 4_000
    with session_factory() as session:
        ingest = session.exec(select(AgentAction).where(AgentAction.kind == "ingest")).one()
        assert ingest.cost_usd == pytest.approx(0.25)
        assert ingest.cached_tokens == 4_000
        cycle = session.exec(select(AgentCycle)).one()
        assert cycle.cost_usd == pytest.approx(0.25)
        assert cycle.llm_calls == 1
