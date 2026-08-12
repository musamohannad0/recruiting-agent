from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..settings import settings
from ..workspace import (
    FOLLOW_UP_QUESTIONS,
    MAX_FOLLOW_UP_QUESTIONS,
    CandidateWorkspace,
    OnboardingStage,
    workspace,
)
from .runtime import AgentRuntime, TaskSpec


INTERVIEW_SYSTEM_PROMPT = """\
You conduct the final clarification step of a job-search onboarding. The candidate
already completed a structured search brief. Treat that explicit brief as the source
of truth about intent; the resume is evidence of capability, not evidence of what the
candidate wants next.

Ask at most one question, and only if its answer could materially change ranking or
search scope. Never revisit a covered topic, never infer interest from a former
employer or industry, and never ask a resume-history question merely because it is
interesting. Return complete=true when the brief is actionable. Questions must be
short, concrete, and explain the decision they calibrate."""

QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "topic": {
            "type": "string",
            "enum": ["target_work", "strengths", "stretch", "location", "companies", "exclusions", "tradeoffs", "cadence"],
        },
        "complete": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["question", "topic", "complete", "reason"],
}


@dataclass
class OnboardingInterviewer:
    candidate_workspace: CandidateWorkspace = field(default_factory=lambda: workspace)
    runtime: AgentRuntime | None = None

    async def next_question(self) -> dict[str, str] | None:
        state = self.candidate_workspace.load_state()
        if state.get("stage") != OnboardingStage.interview.value:
            return None
        if state.get("current_question"):
            current = state["current_question"]
            return current if isinstance(current, dict) else {"question": str(current), "topic": "clarification", "reason": "This detail can change role ranking."}
        if int(state.get("question_index", 0)) >= MAX_FOLLOW_UP_QUESTIONS:
            self.candidate_workspace.finish_interview(state)
            return None

        resume = self.candidate_workspace.resume_path()
        resume_ref = resume.relative_to(self.candidate_workspace.root) if resume else "missing"
        prompt = (
            f"Resume path relative to the workspace: {resume_ref}\n\n"
            f"Approved structured search brief:\n{json.dumps(state.get('search_draft', {}), indent=2)}\n\n"
            f"Topics already covered: {json.dumps(state.get('covered_topics', []))}\n"
            f"Topics already asked: {json.dumps(state.get('asked_topics', []))}\n\n"
            f"Prior clarification answers:\n{json.dumps(state.get('answers', []), indent=2)}"
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
            topic = str(data["topic"])
            if data.get("complete") or topic in set(state.get("covered_topics", [])) | set(state.get("asked_topics", [])):
                self.candidate_workspace.finish_interview(state)
                return None
            question = str(data["question"]).strip()
            reason = str(data.get("reason", "This answer will calibrate the search.")).strip()
        except Exception:
            # Onboarding remains usable when the local Claude CLI is temporarily unavailable.
            question = self.candidate_workspace.next_fallback_question(state)
            topic = next((key for key, value in FOLLOW_UP_QUESTIONS.items() if value == question), "clarification")
            reason = "This is the last unresolved preference that could change ranking."
        if not question:
            self.candidate_workspace.finish_interview(state)
            return None
        state["current_question"] = {"question": question, "topic": topic, "reason": reason}
        state["generation"] = {"status": "complete", "message": "Clarification ready."}
        self.candidate_workspace.save_state(state)
        return state["current_question"]
