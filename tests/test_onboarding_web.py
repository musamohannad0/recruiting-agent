from pathlib import Path

from fastapi.testclient import TestClient

from recruiting_agent.web import app as web_module
from recruiting_agent.workspace import CandidateWorkspace


def test_dashboard_redirects_to_first_run_onboarding(tmp_path: Path, monkeypatch):
    ws = CandidateWorkspace(tmp_path)
    monkeypatch.setattr(web_module, "workspace", ws)
    client = TestClient(web_module.app)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding"


def test_resume_upload_advances_to_search_brief(tmp_path: Path, monkeypatch):
    ws = CandidateWorkspace(tmp_path)
    monkeypatch.setattr(web_module, "workspace", ws)
    client = TestClient(web_module.app)

    response = client.post(
        "/onboarding/resume",
        files={"resume": ("resume.pdf", b"opaque resume", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert ws.resume_path().read_bytes() == b"opaque resume"
    assert ws.load_state()["stage"] == "brief"


def test_interview_page_resumes_current_agent_question(tmp_path: Path, monkeypatch):
    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("resume.pdf", b"opaque")
    state = ws.load_state()
    state["stage"] = "interview"
    state["current_question"] = "Which tradeoff matters most?"
    ws.save_state(state)
    monkeypatch.setattr(web_module, "workspace", ws)
    client = TestClient(web_module.app)

    response = client.get("/onboarding")

    assert response.status_code == 200
    assert "Which tradeoff matters most?" in response.text
