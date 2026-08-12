from pathlib import Path

import pytest

from recruiting_agent.agents.interview import OnboardingInterviewer
from recruiting_agent.agents.llm import LLMResult
from recruiting_agent.agents.runtime import AgentRuntime, TaskSpec
from recruiting_agent.workspace import CandidateWorkspace


class FakeRuntime:
    def __init__(self):
        self.prompts = []

    async def run(self, task, prompt, **kwargs):
        self.prompts.append((task, prompt, kwargs))
        return LLMResult(
            data={
                "question": "Would you trade company prestige for substantially more technical ownership?",
                "topic": "tradeoffs",
                "complete": False,
                "reason": "This determines how the agent ranks ownership against company brand.",
            },
            trace_id=None,
            cost_usd=0,
        )


@pytest.mark.asyncio
async def test_interviewer_asks_agent_question_from_uploaded_resume(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("resume.pdf", b"opaque-pdf")
    ws.save_search_brief(
        {
            "role_thesis": "Technical product and deployment work",
            "locations": ["New York City"],
        }
    )
    runtime = FakeRuntime()

    question = await OnboardingInterviewer(ws, runtime).next_question()

    assert "technical ownership" in question["question"]
    assert "uploads/resume.pdf" in runtime.prompts[0][1]
    assert ws.load_state()["current_question"] == question


@pytest.mark.asyncio
async def test_runtime_injects_only_requested_candidate_context(tmp_path: Path, monkeypatch):
    ws = CandidateWorkspace(tmp_path)
    (tmp_path / "policy").mkdir(parents=True)
    (tmp_path / "policy/search-constitution.md").write_text("Only approved companies")
    captured = {}

    async def fake_llm_json(**kwargs):
        captured.update(kwargs)
        return LLMResult(data={"ok": True}, trace_id=None, cost_usd=0)

    monkeypatch.setattr("recruiting_agent.agents.runtime.llm_json", fake_llm_json)
    task = TaskSpec(
        name="test",
        system_prompt="generic",
        schema={"type": "object"},
        model="fake",
        context_sections=("policy/search-constitution.md",),
    )

    result = await AgentRuntime(ws).run(task, "Do the work")

    assert result.data == {"ok": True}
    assert "Only approved companies" in captured["prompt"]
    assert "Do the work" in captured["prompt"]
