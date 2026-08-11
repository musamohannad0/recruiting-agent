from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from ..settings import settings

# Langfuse reads its config from env vars; export them from .env-backed settings
# before the first client lookup.
if settings.langfuse_public_key and settings.langfuse_secret_key:
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
    _langfuse_enabled = True
else:
    _langfuse_enabled = False


def langfuse_client():
    if not _langfuse_enabled:
        return None
    from langfuse import get_client

    return get_client()


@dataclass
class LLMResult:
    data: Any
    trace_id: str | None
    cost_usd: float | None


async def llm_json(
    *,
    name: str,
    model: str,
    system_prompt: str,
    prompt: str,
    schema: dict,
    effort: str | None = None,
    metadata: dict | None = None,
    tools: list[str] | None = None,
    allowed_tools: list[str] | None = None,
    skills: list[str] | None = None,
    cwd: Path | None = None,
    max_turns: int = 3,
    mcp_servers: dict | None = None,
) -> LLMResult:
    """Structured Agent SDK call with explicit capabilities and tracing."""
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        tools=tools or [],
        allowed_tools=allowed_tools or [],
        skills=skills,
        cwd=cwd,
        setting_sources=["project"] if cwd else [],
        mcp_servers=mcp_servers or {},
        # Structured output consumes an internal tool turn.
        max_turns=max_turns,
        effort=effort,
        output_format={"type": "json_schema", "schema": schema},
    )

    lf = langfuse_client()
    trace_id = None
    if lf is not None:
        with lf.start_as_current_generation(
            name=name,
            model=model,
            input={"system": system_prompt, "prompt": prompt},
            metadata=metadata or {},
        ) as gen:
            trace_id = lf.get_current_trace_id()
            result = await _run_query(prompt, options)
            usage = result.usage or {}
            gen.update(
                output=result.structured_output,
                usage_details={
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                },
            )
    else:
        result = await _run_query(prompt, options)

    return LLMResult(
        data=result.structured_output,
        trace_id=trace_id,
        cost_usd=result.total_cost_usd,
    )


async def _run_query(prompt: str, options: ClaudeAgentOptions) -> ResultMessage:
    final: ResultMessage | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            final = message
    if final is None:
        raise RuntimeError("LLM call produced no result message")
    if final.is_error:
        raise RuntimeError(f"LLM call failed: {final.subtype} {final.errors or final.result}")
    if final.structured_output is None:
        raise RuntimeError(f"LLM call returned no structured output: {final.result!r}")
    return final
