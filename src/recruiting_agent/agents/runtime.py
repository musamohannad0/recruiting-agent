from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..settings import settings
from ..workspace import CandidateWorkspace, workspace
from .llm import LLMResult, llm_json

BUILTIN_TOOLS = {"Read", "WebSearch", "WebFetch"}


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
        builtins = [name for name in task.tools if name in BUILTIN_TOOLS]
        custom = [name for name in task.tools if name not in BUILTIN_TOOLS]
        mcp_servers = None
        allowed_tools = list(builtins)
        if custom:
            from .tools import build_candidate_tools_server

            mcp_servers = {"candidate": build_candidate_tools_server(self.candidate_workspace, custom)}
            allowed_tools.extend(f"mcp__candidate__{name}" for name in custom)
        return await llm_json(
            name=task.name,
            model=task.model,
            system_prompt=task.system_prompt,
            prompt=full_prompt,
            schema=task.schema,
            effort=task.effort,
            metadata={"harness_hash": self.candidate_workspace.harness_hash, **(metadata or {})},
            tools=builtins,
            allowed_tools=allowed_tools,
            skills=list(task.skills) if task.skills and self.candidate_workspace.is_ready else None,
            cwd=self.candidate_workspace.root,
            max_turns=task.max_turns,
            mcp_servers=mcp_servers,
        )


def default_runtime() -> AgentRuntime:
    return AgentRuntime(CandidateWorkspace(settings.workspace_dir))
