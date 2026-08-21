"""The cost levers: what gets re-sent per role, what gets retried, what gets counted."""

from __future__ import annotations

import pytest

from recruiting_agent.agents import runtime as runtime_module
from recruiting_agent.agents.llm import LLMError, LLMResult, supports_effort, track_usage
from recruiting_agent.agents.profile import Profile
from recruiting_agent.agents.scoring import MAX_DESCRIPTION_CHARS, clip_description, score_job
from recruiting_agent.workspace import CandidateWorkspace


@pytest.fixture()
def ready_workspace(tmp_path, monkeypatch):
    ws = CandidateWorkspace(tmp_path)
    ws._write("manifest.yaml", "status: ready\nharness_hash: test-harness\n")
    ws._write("context/career-story.md", "Eight years on data platforms.")
    ws._write("policy/search-constitution.md", "Staff platform roles in New York.")
    ws._write("policy/decision-rubric.md", "85+ is a direct hit.")
    ws._write("memory/calibration-anchors.md", "Staff Data Platform scored 88.")
    ws._write("memory/feedback-summary.md", "Downgrades product-org reporting lines.")
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    (tmp_path / "uploads" / "resume.md").write_text("# Candidate\n\nStaff engineer, data platform.")
    monkeypatch.setattr(runtime_module.settings, "workspace_dir", tmp_path)
    runtime_module.reset_runtime_caches()
    return ws


def _capture(monkeypatch):
    calls = []

    async def fake_llm_json(**kwargs):
        calls.append(kwargs)
        return LLMResult(
            data={
                "score": 80,
                "recommendation": "apply",
                "reasoning": "x",
                "red_flags": [],
                "seniority_fit": "y",
                "location_fit": "z",
            },
            trace_id=None,
            cost_usd=0.01,
        )

    monkeypatch.setattr(runtime_module, "llm_json", fake_llm_json)
    return calls


async def test_text_resume_rides_the_cached_prefix_instead_of_a_read_per_role(
    ready_workspace, monkeypatch
):
    """A run scores N roles; the resume must be sent once as a stable prefix, not fetched N times."""
    calls = _capture(monkeypatch)
    profile = Profile(
        resume_md="unused",
        profile_yaml="",
        resume_path=ready_workspace.root / "uploads" / "resume.md",
        version_hash="test-harness",
    )

    for title in ("Staff Engineer", "Principal Engineer", "Platform Lead"):
        await score_job(
            profile,
            company="Example",
            title=title,
            location="New York, NY",
            department="Engineering",
            description_md="Own the ingestion path.",
        )

    # No Read tool, and no extra turn budget to pay for one.
    assert all(call["tools"] == [] for call in calls)
    assert all(call["max_turns"] == 3 for call in calls)

    systems = [call["system_prompt"] for call in calls]
    # The resume and the whole harness live in the prefix...
    assert all("Staff engineer, data platform." in system for system in systems)
    assert all("Staff platform roles in New York." in system for system in systems)
    # ...and that prefix is byte-identical across roles, which is what makes it cacheable.
    assert len(set(systems)) == 1
    # Only the role varies.
    assert [("Staff Engineer" in c["prompt"]) for c in calls][0]
    assert len({call["prompt"] for call in calls}) == 3


async def test_pdf_resume_still_uses_the_read_tool(ready_workspace, monkeypatch):
    """A PDF cannot be inlined as text, so it keeps the tool round-trip."""
    calls = _capture(monkeypatch)
    pdf = ready_workspace.root / "uploads" / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 opaque")
    profile = Profile(resume_md="", profile_yaml="", resume_path=pdf, version_hash="test-harness")

    await score_job(
        profile,
        company="Example",
        title="Staff Engineer",
        location=None,
        department=None,
        description_md="Own the ingestion path.",
    )

    assert calls[0]["tools"] == ["Read"]
    assert calls[0]["max_turns"] == 5
    assert "uploads/resume.pdf" in calls[0]["prompt"]


def test_clipped_descriptions_say_so():
    short = "a" * 100
    assert clip_description(short) == short

    clipped = clip_description("b" * (MAX_DESCRIPTION_CHARS + 500))
    assert clipped.startswith("b" * 100)
    assert "truncated" in clipped
    # The model is told what kind of content may be missing, so it does not read the
    # absence of a salary band as evidence there isn't one.
    assert "compensation" in clipped


def test_effort_is_only_sent_to_models_that_accept_it():
    # Haiku 4.5 and Sonnet 4.5 reject the parameter outright.
    assert not supports_effort("claude-haiku-4-5")
    assert not supports_effort("claude-sonnet-4-5")
    assert supports_effort("claude-sonnet-5")
    assert supports_effort("claude-opus-5")


def test_usage_totals_accumulate_across_concurrent_calls():
    """Scoring runs calls concurrently; each must land in the same totals object."""
    with track_usage() as totals:
        for _ in range(3):
            totals.add(
                LLMResult(
                    data=None,
                    trace_id=None,
                    cost_usd=0.02,
                    input_tokens=500,
                    output_tokens=100,
                    cache_read_tokens=4_000,
                    attempts=2,
                )
            )

    assert totals.llm_calls == 3
    assert totals.cost_usd == pytest.approx(0.06)
    assert totals.input_tokens == 1_500
    assert totals.as_dict()["cached_tokens"] == 12_000
    assert totals.as_dict()["llm_retries"] == 3


def test_cache_hit_ratio_reports_what_was_reused():
    result = LLMResult(
        data=None, trace_id=None, cost_usd=0, input_tokens=1_000, cache_read_tokens=9_000
    )
    assert result.cache_hit_ratio == pytest.approx(0.9)
    assert LLMResult(data=None, trace_id=None, cost_usd=0).cache_hit_ratio == 0.0


async def test_transient_failures_retry_and_permanent_ones_do_not(monkeypatch):
    from recruiting_agent.agents import llm as llm_module

    monkeypatch.setattr(llm_module.asyncio, "sleep", lambda _: _noop())
    attempts = {"n": 0}

    async def flaky(prompt, options):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise LLMError("overloaded", retryable=True, status=529)
        return _fake_message()

    monkeypatch.setattr(llm_module, "_run_query", flaky)
    message, used = await llm_module._run_with_retries("p", object(), 3, "test")
    assert used == 3

    attempts["n"] = 0

    async def refused(prompt, options):
        attempts["n"] += 1
        raise LLMError("bad request", retryable=False, status=400)

    monkeypatch.setattr(llm_module, "_run_query", refused)
    with pytest.raises(LLMError):
        await llm_module._run_with_retries("p", object(), 3, "test")
    assert attempts["n"] == 1, "a non-retryable failure must not be retried"


async def _noop():
    return None


def _fake_message():
    class _Message:
        usage = {"input_tokens": 1, "output_tokens": 1}
        model_usage = None
        total_cost_usd = 0.0
        duration_ms = 1
        num_turns = 1
        structured_output = {"ok": True}

    return _Message()
