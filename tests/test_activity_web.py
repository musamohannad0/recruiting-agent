from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from recruiting_agent.models import ActionStatus, AgentAction, AgentCycle, AgentEvent, utcnow
from recruiting_agent.web import app as web_module
from recruiting_agent.workspace import CandidateWorkspace


def test_activity_turns_agent_records_into_live_progress(tmp_path: Path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session:
            yield session

    workspace = CandidateWorkspace(tmp_path)
    workspace._write("manifest.yaml", "status: ready\nversion: 1\nharness_hash: test-harness\n")
    with sessions() as session:
        cycle = AgentCycle(trigger="onboarding_activation", phase="match", harness_hash="test-harness")
        session.add(cycle)
        session.commit()
        session.refresh(cycle)
        session.add(
            AgentAction(
                cycle_id=cycle.id,
                idempotency_key="ingest-once",
                kind="ingest",
                status=ActionStatus.success,
                attempts=1,
                result_json='{"companies": 35, "errors": 0, "new": 6350}',
                finished_at=utcnow(),
            )
        )
        session.add(
            AgentAction(
                cycle_id=cycle.id,
                idempotency_key="match-once",
                kind="match",
                status=ActionStatus.running,
                attempts=1,
            )
        )
        session.add(
            AgentEvent(
                event_type="ingest_completed",
                cycle_id=cycle.id,
                payload_json='{"companies": 35, "new": 6350}',
            )
        )
        session.commit()

    monkeypatch.setattr(web_module, "workspace", workspace)
    monkeypatch.setattr(web_module, "get_session", sessions)
    client = TestClient(web_module.app)

    page = client.get("/activity")
    assert page.status_code == 200
    assert "The search, in motion." in page.text
    assert "Evaluating 6,350 newly collected roles" in page.text
    assert "Recorded result" in page.text

    status = client.get("/activity/status")
    assert status.status_code == 200
    assert status.json()["phase_label"] == "Evaluate roles"
    assert status.json()["status"] == "running"
