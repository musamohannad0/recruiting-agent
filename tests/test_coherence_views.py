from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from recruiting_agent.models import MemoryRevision
from recruiting_agent.web import app as web_module
from recruiting_agent.workspace import CandidateWorkspace


def test_coherence_dashboard_and_memory_approval(tmp_path: Path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session:
            yield session

    ws = CandidateWorkspace(tmp_path)
    ws._write("manifest.yaml", "status: ready\nversion: 1\nharness_hash: initial\n")
    ws._write("policy/search-constitution.md", "# Search Constitution\nStrict scope")
    ws._write("policy/company-thesis.md", "# Company Thesis\nBig Tech")
    ws._write("memory/current-search-state.md", "# Current Search State\nScanning")
    with sessions() as session:
        session.add(
            MemoryRevision(
                section="memory/feedback-summary.md",
                content="# Feedback Summary\nPrefer infrastructure roles.",
            )
        )
        session.commit()

    monkeypatch.setattr(web_module, "workspace", ws)
    monkeypatch.setattr(web_module, "get_session", sessions)
    client = TestClient(web_module.app)

    for path in ["/search-state", "/activity", "/memory", "/feedback", "/sources"]:
        assert client.get(path).status_code == 200

    response = client.post("/memory/1/decision", data={"decision": "approve"})

    assert response.status_code == 200
    assert "Prefer infrastructure roles" in ws.read_section("memory/feedback-summary.md")
    assert ws.harness_hash != "initial"


def test_rendered_markdown_never_emits_source_html():
    """Job descriptions come from third-party ATS HTML; embedded markup must not execute."""
    from recruiting_agent.reporting import render_markdown

    hostile = (
        "## Real heading\n\n"
        "<script>alert('xss')</script>\n\n"
        "<img src=x onerror=alert('xss')>\n\n"
        "[looks fine](javascript:alert('xss'))\n\n"
        "- a genuine bullet\n"
    )
    html = render_markdown(hostile)

    assert "<h2>Real heading</h2>" in html
    assert "<li>a genuine bullet</li>" in html
    # Every piece of source markup arrives escaped, never as a live tag.
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "onerror=" not in html or "&lt;img" in html
    assert 'href="javascript:' not in html


def test_rendered_markdown_handles_empty_input():
    from recruiting_agent.reporting import render_markdown

    assert render_markdown(None) == ""
    assert render_markdown("") == ""
