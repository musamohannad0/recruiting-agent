from __future__ import annotations

from dataclasses import dataclass

from ..settings import settings
from .profile import Profile
from .runtime import AgentRuntime, TaskSpec, default_runtime

SYSTEM_PROMPT = """\
You are a lenient first-pass screener for a job search. Apply the candidate's
confirmed search constitution and hard constraints. Titles are noisy, so judge the
likely substance of the work. Reject only when the limited posting metadata clearly
violates an explicit constraint; otherwise mark the role plausible. Return a verdict
for every input index in order."""

BATCH_SIZE = 25

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["plausible", "rejected"]},
                    "reason": {"type": "string", "description": "Short reason, <=12 words"},
                },
                "required": ["index", "verdict", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}


@dataclass
class PrefilterInput:
    job_id: int
    title: str
    company: str
    department: str | None
    location: str | None


async def prefilter_batch(
    profile: Profile,
    jobs: list[PrefilterInput],
    runtime: AgentRuntime | None = None,
) -> dict[int, tuple[str, str]]:
    """Returns {job_id: (verdict, reason)}. Missing verdicts default to plausible."""
    lines = [
        f"{i}. {j.title} — {j.company}"
        + (f" | {j.department}" if j.department else "")
        + (f" | {j.location}" if j.location else "")
        for i, j in enumerate(jobs)
    ]
    task = TaskSpec(
        name="prefilter",
        model=settings.prefilter_model,
        system_prompt=SYSTEM_PROMPT,
        schema=SCHEMA,
        context_sections=("policy/search-constitution.md", "policy/decision-rubric.md"),
        skills=("candidate-search-policy", "evaluate-role"),
        effort="low",
    )
    result = await (runtime or default_runtime()).run(
        task,
        "## Job postings\n\n" + "\n".join(lines),
        metadata={"batch_size": len(jobs)},
        fallback_context=profile.profile_yaml,
    )
    by_index = {v["index"]: v for v in result.data.get("verdicts", [])}
    out: dict[int, tuple[str, str]] = {}
    for i, j in enumerate(jobs):
        v = by_index.get(i)
        if v is None:
            # Model skipped this index; err on the side of a closer look.
            out[j.job_id] = ("plausible", "no verdict returned; defaulted to plausible")
        else:
            out[j.job_id] = (v["verdict"], v["reason"])
    return out
