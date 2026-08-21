from __future__ import annotations

from ..settings import settings
from .llm import LLMResult
from .profile import Profile
from .runtime import AgentRuntime, TaskSpec, default_runtime

PROMPT_VERSION = "v3-harness"

SYSTEM_PROMPT = """\
You are an exacting career advisor evaluating one posting for one candidate. Use
the uploaded resume, approved search constitution, decision rubric, and calibration
anchors. Apply confirmed hard constraints before preferences and judge the substance
of the work rather than title keywords. Score 0–100 using the candidate's approved
anchors. Recommend apply for 75+, maybe for 60–74, and skip below 60 unless the
candidate rubric explicitly demands a stricter outcome."""

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "recommendation": {"type": "string", "enum": ["apply", "maybe", "skip"]},
        "reasoning": {
            "type": "string",
            "description": "3-6 sentences: why this score; what makes it a fit or not",
        },
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "seniority_fit": {"type": "string", "description": "one line on level match"},
        "location_fit": {"type": "string", "description": "one line on location match"},
    },
    "required": ["score", "recommendation", "reasoning", "red_flags", "seniority_fit", "location_fit"],
}

MAX_DESCRIPTION_CHARS = 12_000


async def score_job(
    profile: Profile,
    *,
    company: str,
    title: str,
    location: str | None,
    department: str | None,
    description_md: str,
    metadata: dict | None = None,
    runtime: AgentRuntime | None = None,
) -> LLMResult:
    prompt = (
        "## Job posting\n\n"
        + f"Company: {company}\nTitle: {title}\n"
        + (f"Department: {department}\n" if department else "")
        + (f"Location: {location}\n" if location else "")
        + "\n"
        + description_md[:MAX_DESCRIPTION_CHARS]
    )
    task = TaskSpec(
        name="score_job",
        model=settings.scoring_model,
        system_prompt=SYSTEM_PROMPT,
        schema=SCHEMA,
        context_sections=(
            "context/career-story.md",
            "policy/search-constitution.md",
            "policy/decision-rubric.md",
            "memory/calibration-anchors.md",
            "memory/feedback-summary.md",
        ),
        skills=("candidate-search-policy", "evaluate-role"),
        tools=("Read",) if profile.resume_path else (),
        max_turns=5 if profile.resume_path else 3,
    )
    resume_instruction = profile.resume_md
    if profile.resume_path:
        try:
            relative = profile.resume_path.relative_to(settings.workspace_dir)
            resume_instruction = f"Read the uploaded resume at `{relative}` before scoring."
        except ValueError:
            pass
    return await (runtime or default_runtime()).run(
        task,
        resume_instruction + "\n\n" + prompt,
        metadata=metadata or {},
        fallback_context=profile.profile_yaml,
    )
