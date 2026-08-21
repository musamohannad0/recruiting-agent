from __future__ import annotations

import asyncio
import contextvars
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

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


#: Model families that accept an `effort` setting. Haiku 4.5 and Sonnet 4.5 reject it,
#: so sending one to the cheap screening model is an error, not a no-op.
EFFORT_CAPABLE = ("claude-opus-", "claude-sonnet-5", "claude-fable-", "claude-mythos-")


def supports_effort(model: str) -> bool:
    return any(model.startswith(prefix) for prefix in EFFORT_CAPABLE)


#: HTTP statuses worth another attempt: overload, rate limit, and gateway faults.
RETRYABLE_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
#: Result subtypes the CLI reports for conditions that usually clear on their own.
RETRYABLE_SUBTYPES = {"error_max_turns", "error_during_execution"}


class LLMError(RuntimeError):
    """An Agent SDK call that did not produce usable structured output."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class LLMResult:
    data: Any
    trace_id: str | None
    cost_usd: float | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    duration_ms: int = 0
    num_turns: int = 0
    model: str = ""
    attempts: int = 1

    @property
    def cache_hit_ratio(self) -> float:
        """Share of prompt tokens served from cache — the efficiency signal."""
        billed = self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens
        return (self.cache_read_tokens / billed) if billed else 0.0


@dataclass
class UsageTotals:
    """Running totals for every LLM call made inside a :func:`track_usage` block."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    errors: int = 0
    retries: int = 0

    def add(self, result: LLMResult) -> None:
        self.llm_calls += 1
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.cache_read_tokens += result.cache_read_tokens
        self.cache_creation_tokens += result.cache_creation_tokens
        self.cost_usd += result.cost_usd or 0.0
        self.retries += max(0, result.attempts - 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cache_read_tokens,
            "cost_usd": round(self.cost_usd, 6),
            **({"llm_errors": self.errors} if self.errors else {}),
            **({"llm_retries": self.retries} if self.retries else {}),
        }


_usage: contextvars.ContextVar[UsageTotals | None] = contextvars.ContextVar("llm_usage", default=None)


@contextmanager
def track_usage() -> Iterator[UsageTotals]:
    """Accumulate usage for every ``llm_json`` call made inside this block.

    Tasks started with ``asyncio.gather`` inherit a copy of the context, and a copy
    still points at this same totals object, so concurrent scoring calls all land here.
    """
    totals = UsageTotals()
    token = _usage.set(totals)
    try:
        yield totals
    finally:
        _usage.reset(token)


def current_usage() -> UsageTotals | None:
    return _usage.get()


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
    max_budget_usd: float | None = None,
    max_attempts: int | None = None,
) -> LLMResult:
    """Structured Agent SDK call with explicit capabilities, retries, and accounting.

    ``system_prompt`` should carry everything invariant across a batch of calls (the
    candidate harness, the rubric) and ``prompt`` only the per-item payload. The stable
    prefix is what makes prompt caching pay off across a scoring run; the returned
    ``cache_read_tokens`` is how you check that it did.
    """
    options = ClaudeAgentOptions(
        model=model,
        fallback_model=settings.fallback_model or None,
        system_prompt=system_prompt,
        tools=tools or [],
        allowed_tools=allowed_tools or [],
        skills=skills,
        cwd=cwd,
        setting_sources=["project"] if cwd else [],
        mcp_servers=mcp_servers or {},
        # Structured output consumes an internal tool turn.
        max_turns=max_turns,
        # Silently dropping an unsupported effort beats a 400 from the cheap screener.
        effort=effort if (effort and supports_effort(model)) else None,
        max_budget_usd=max_budget_usd if max_budget_usd is not None else settings.max_call_budget_usd,
        output_format={"type": "json_schema", "schema": schema},
    )

    attempts = max_attempts if max_attempts is not None else settings.llm_max_attempts
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
            message, used = await _run_with_retries(prompt, options, attempts, name)
            usage = message.usage or {}
            gen.update(
                output=message.structured_output,
                usage_details={
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                },
            )
    else:
        message, used = await _run_with_retries(prompt, options, attempts, name)

    result = _to_result(message, trace_id, model, used)
    totals = _usage.get()
    if totals is not None:
        totals.add(result)
    return result


def _to_result(message: ResultMessage, trace_id: str | None, model: str, attempts: int) -> LLMResult:
    usage = message.usage or {}
    # ResultMessage.usage is the raw API shape; model_usage is the per-model rollup.
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    if not (input_tokens or output_tokens) and message.model_usage:
        for entry in message.model_usage.values():
            input_tokens += int(entry.get("inputTokens", 0) or 0)
            output_tokens += int(entry.get("outputTokens", 0) or 0)
            cache_read += int(entry.get("cacheReadInputTokens", 0) or 0)
            cache_creation += int(entry.get("cacheCreationInputTokens", 0) or 0)
    return LLMResult(
        data=message.structured_output,
        trace_id=trace_id,
        cost_usd=message.total_cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        duration_ms=message.duration_ms,
        num_turns=message.num_turns,
        model=model,
        attempts=attempts,
    )


async def _run_with_retries(
    prompt: str, options: ClaudeAgentOptions, attempts: int, name: str
) -> tuple[ResultMessage, int]:
    """Retry transient failures with exponential backoff; surface real errors at once."""
    last: LLMError | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await _run_query(prompt, options), attempt
        except LLMError as exc:
            last = exc
            totals = _usage.get()
            if not exc.retryable or attempt >= attempts:
                if totals is not None:
                    totals.errors += 1
                raise
            await asyncio.sleep(min(2 ** (attempt - 1), 8))
    assert last is not None
    raise last


async def _run_query(prompt: str, options: ClaudeAgentOptions) -> ResultMessage:
    final: ResultMessage | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            final = message
    if final is None:
        raise LLMError("LLM call produced no result message", retryable=True)
    if final.is_error:
        status = final.api_error_status
        retryable = (status in RETRYABLE_STATUSES) or (final.subtype in RETRYABLE_SUBTYPES)
        raise LLMError(
            f"LLM call failed: {final.subtype} {final.errors or final.result}",
            retryable=retryable,
            status=status,
        )
    if final.structured_output is None:
        # The model answered but skipped the structured-output tool; another pass usually fixes it.
        raise LLMError(
            f"LLM call returned no structured output: {final.result!r}",
            retryable=True,
        )
    return final
