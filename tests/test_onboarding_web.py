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


def test_company_and_role_calibration_render_without_reasking_target_roles(
    tmp_path: Path, monkeypatch
):
    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("resume.pdf", b"opaque")
    ws.save_search_brief(
        {
            "role_thesis": "FDE and applied AI engineering with a customer loop",
            "locations": ["New York City", "San Francisco"],
            "company_preferences": "Private LLM labs and applied AI companies",
        }
    )
    state = ws.finish_interview()
    monkeypatch.setattr(web_module, "workspace", ws)
    client = TestClient(web_module.app)

    company_page = client.get("/onboarding")
    assert company_page.status_code == 200
    assert company_page.text.count('name="included"') == 40
    assert "Start broad. Cut what’s wrong." in company_page.text

    names = [company["name"] for company in state["company_candidates"]]
    ws.save_company_selection(scope="exploratory", included=names, priority=names[:8])
    role_page = client.get("/onboarding")

    assert role_page.text.count('class="role-card"') == 6
    assert "Your target work is already captured" in role_page.text
    assert 'name="target_roles"' not in role_page.text
