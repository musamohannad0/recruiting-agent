from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..settings import settings
from ..workspace import CandidateWorkspace, workspace
from .llm import LLMResult, llm_json


@dataclass(frozen=True)
class TaskSpec:
    name: str
    system_prompt: str
    schema: dict[str, Any]
    model: str
    context_sections: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    effort: str | None = None
    max_turns: int = 3


@dataclass
class AgentRuntime:
    candidate_workspace: CandidateWorkspace = field(default_factory=lambda: workspace)

    async def run(
        self,
        task: TaskSpec,
        prompt: str,
        *,
        metadata: dict[str, Any] | None = None,
        fallback_context: str = "",
    ) -> LLMResult:
        context = self.candidate_workspace.load_context(task.context_sections)
        if not context:
            context = fallback_context
        full_prompt = prompt
        if context:
            full_prompt = f"## Candidate harness\n\n{context}\n\n## Task\n\n{prompt}"
        return await llm_json(
            name=task.name,
            model=task.model,
            system_prompt=task.system_prompt,
            prompt=full_prompt,
            schema=task.schema,
            effort=task.effort,
            metadata={"harness_hash": self.candidate_workspace.harness_hash, **(metadata or {})},
            tools=list(task.tools),
            allowed_tools=list(task.tools),
            skills=list(task.skills) if task.skills and self.candidate_workspace.is_ready else None,
            cwd=self.candidate_workspace.root,
            max_turns=task.max_turns,
        )


def default_runtime() -> AgentRuntime:
    return AgentRuntime(CandidateWorkspace(settings.workspace_dir))
