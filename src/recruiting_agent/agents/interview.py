from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..settings import settings
from ..workspace import CandidateWorkspace, OnboardingStage, workspace
from .runtime import AgentRuntime, TaskSpec


INTERVIEW_SYSTEM_PROMPT = """\
You conduct a concise job-search onboarding interview. Inspect the uploaded resume
when available and review prior answers. Ask exactly one high-value unresolved
question that will materially affect roles, companies, constraints, or ranking.
Do not ask for facts already evident in the resume. Prefer a concrete tradeoff.
Return complete=true only when the required topics are sufficiently covered."""

QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "topic": {
            "type": "string",
            "enum": ["target_work", "strengths", "stretch", "location", "companies", "exclusions", "tradeoffs", "cadence"],
        },
        "complete": {"type": "boolean"},
    },
    "required": ["question", "topic", "complete"],
}


@dataclass
class OnboardingInterviewer:
    candidate_workspace: CandidateWorkspace = field(default_factory=lambda: workspace)
    runtime: AgentRuntime | None = None

    async def next_question(self) -> str | None:
        state = self.candidate_workspace.load_state()
        if state.get("stage") != OnboardingStage.interview.value:
            return None
        if state.get("current_question"):
            return str(state["current_question"])
        if int(state.get("question_index", 0)) >= 8:
            state["stage"] = OnboardingStage.company_calibration.value
            self.candidate_workspace.save_state(state)
            return None

        resume = self.candidate_workspace.resume_path()
        resume_ref = resume.relative_to(self.candidate_workspace.root) if resume else "missing"
        prompt = (
            f"Resume path relative to the workspace: {resume_ref}\n\n"
            "Required topics: target work, strongest evidence, stretch appetite, location, company thesis, "
            "hard exclusions, fit-versus-excitement tradeoff, and review cadence.\n\n"
            f"Prior answers:\n{json.dumps(state.get('answers', []), indent=2)}"
        )
        runtime = self.runtime or AgentRuntime(self.candidate_workspace)
        task = TaskSpec(
            name="onboarding_interview",
            system_prompt=INTERVIEW_SYSTEM_PROMPT,
            schema=QUESTION_SCHEMA,
            model=settings.scoring_model,
            tools=("Read",),
            effort="low",
            max_turns=5,
        )
        try:
            result = await runtime.run(task, prompt, metadata={"question_index": state.get("question_index", 0)})
            data = result.data
            if data.get("complete") and int(state.get("question_index", 0)) >= 6:
                state["stage"] = OnboardingStage.company_calibration.value
                self.candidate_workspace.save_state(state)
                return None
            question = str(data["question"]).strip()
        except Exception:
            # Onboarding remains usable when the local Claude CLI is temporarily unavailable.
            question = self.candidate_workspace.next_fallback_question(state)
        if not question:
            state["stage"] = OnboardingStage.company_calibration.value
            self.candidate_workspace.save_state(state)
            return None
        state["current_question"] = question
        self.candidate_workspace.save_state(state)
        return question
