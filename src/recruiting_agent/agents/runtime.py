from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
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
    max_budget_usd: float | None = None


@dataclass
class AgentRuntime:
    candidate_workspace: CandidateWorkspace = field(default_factory=lambda: workspace)

    def build_system_prompt(self, task: TaskSpec, fallback_context: str = "") -> str:
        """Task instructions plus the candidate harness, as one stable prefix.

        Everything invariant across a batch of calls belongs here rather than in the
        per-item turn: an identical prefix is what lets the model cache it, so a
        scoring run pays for the harness once instead of once per role.
        """
        context = self._context(task.context_sections) or fallback_context
        if not context:
            return task.system_prompt
        return f"{task.system_prompt}\n\n# Candidate harness\n\n{context}"

    def _context(self, sections: tuple[str, ...]) -> str:
        """Read the harness sections once per (harness, section set), not once per call."""
        if not sections:
            return ""
        return _load_context_cached(
            self.candidate_workspace.root.as_posix(),
            self.candidate_workspace.harness_hash,
            tuple(sections),
        )

    async def run(
        self,
        task: TaskSpec,
        prompt: str,
        *,
        metadata: dict[str, Any] | None = None,
        fallback_context: str = "",
    ) -> LLMResult:
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
            system_prompt=self.build_system_prompt(task, fallback_context),
            prompt=prompt,
            schema=task.schema,
            effort=task.effort,
            metadata={"harness_hash": self.candidate_workspace.harness_hash, **(metadata or {})},
            tools=builtins,
            allowed_tools=allowed_tools,
            skills=list(task.skills) if task.skills and self.candidate_workspace.is_ready else None,
            cwd=self.candidate_workspace.root,
            max_turns=task.max_turns,
            mcp_servers=mcp_servers,
            max_budget_usd=task.max_budget_usd,
        )


@lru_cache(maxsize=64)
def _load_context_cached(root: str, harness_hash: str, sections: tuple[str, ...]) -> str:
    """Harness sections for one harness version. Keyed on the hash, so an approved
    revision bumps the hash and the next call reads from disk again."""
    from pathlib import Path

    return CandidateWorkspace(Path(root)).load_context(sections)


@lru_cache(maxsize=1)
def _shared_runtime(workspace_dir: str) -> AgentRuntime:
    from pathlib import Path

    return AgentRuntime(CandidateWorkspace(Path(workspace_dir)))


def default_runtime() -> AgentRuntime:
    """One shared runtime per workspace, so its caches survive across calls."""
    return _shared_runtime(settings.workspace_dir.as_posix())


def reset_runtime_caches() -> None:
    """Drop memoized harness context — for tests and after a workspace rewrite."""
    _load_context_cached.cache_clear()
    _shared_runtime.cache_clear()
