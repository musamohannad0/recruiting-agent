from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from recruiting_agent.models import Company, CompanyStatus
from recruiting_agent.web import app as web_module
from recruiting_agent.workspace import CandidateWorkspace


def test_activation_redirects_to_live_progress_and_syncs_approved_companies(
    tmp_path: Path, monkeypatch
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session:
            yield session

    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("resume.pdf", b"resume")
    ws.save_search_brief(
        {
            "role_thesis": "Applied AI deployment work",
            "locations": ["New York City"],
            "company_scope": "strict",
        }
    )
    state = ws.finish_interview()
    approved = ["Anthropic", "OpenAI"]
    ws.save_company_selection(
        scope="strict",
        included=approved,
        priority=["Anthropic"],
    )
    state = ws.load_state()
    ws.save_role_ratings(
        {card["id"]: card["prediction"] for card in state["role_cards"]}
    )

    cycle_started = []

    async def fake_initial_cycle():
        cycle_started.append(True)

    monkeypatch.setattr(web_module, "workspace", ws)
    monkeypatch.setattr(web_module, "get_session", sessions)
    monkeypatch.setattr(web_module, "_run_initial_cycle", fake_initial_cycle)
    client = TestClient(web_module.app)

    response = client.post("/onboarding/activate", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/getting-started"
    assert ws.is_ready
    assert cycle_started == [True]
    with sessions() as session:
        active = session.exec(
            select(Company).where(Company.status == CompanyStatus.active)
        ).all()
        assert {company.name for company in active} == set(approved)

    progress = client.get("/getting-started")
    assert progress.status_code == 200
    assert "Initial search in progress" in progress.text
