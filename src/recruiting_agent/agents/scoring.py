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

TRUNCATION_NOTE = (
    "\n\n[Description truncated at {limit:,} characters. Later sections — often "
    "compensation, benefits, and eligibility — may be missing. Do not treat their "
    "absence as evidence.]"
)

HARNESS_SECTIONS = (
    "context/career-story.md",
    "policy/search-constitution.md",
    "policy/decision-rubric.md",
    "memory/calibration-anchors.md",
    "memory/feedback-summary.md",
)

#: Resume formats we can inline as text. A PDF has to be opened with the Read tool.
INLINE_RESUME_SUFFIXES = {".md", ".txt"}


def clip_description(description_md: str, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """Trim a long posting, but tell the model that is what happened."""
    if len(description_md) <= limit:
        return description_md
    return description_md[:limit] + TRUNCATION_NOTE.format(limit=limit)


def _resume_plan(profile: Profile) -> tuple[tuple[str, ...], tuple[str, ...], int, str]:
    """Decide how the resume reaches the model.

    A text resume goes into the cached system prefix, so a run of N roles sends it
    once instead of making N separate Read round-trips for the same bytes. A PDF
    still needs the Read tool, which costs an extra turn per role.
    """
    path = profile.resume_path
    if path is None:
        return HARNESS_SECTIONS, (), 3, profile.resume_md
    if path.suffix.lower() in INLINE_RESUME_SUFFIXES:
        try:
            relative = path.relative_to(settings.workspace_dir).as_posix()
        except ValueError:
            relative = None
        if relative:
            return HARNESS_SECTIONS + (relative,), (), 3, ""
    try:
        relative = path.relative_to(settings.workspace_dir)
    except ValueError:
        relative = path
    return (
        HARNESS_SECTIONS,
        ("Read",),
        5,
        f"Read the uploaded resume at `{relative}` before scoring.",
    )


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
    sections, tools, max_turns, resume_instruction = _resume_plan(profile)
    prompt = (
        "## Job posting\n\n"
        + f"Company: {company}\nTitle: {title}\n"
        + (f"Department: {department}\n" if department else "")
        + (f"Location: {location}\n" if location else "")
        + "\n"
        + clip_description(description_md)
    )
    task = TaskSpec(
        name="score_job",
        model=settings.scoring_model,
        system_prompt=SYSTEM_PROMPT,
        schema=SCHEMA,
        context_sections=sections,
        skills=("candidate-search-policy", "evaluate-role"),
        tools=tools,
        max_turns=max_turns,
    )
    return await (runtime or default_runtime()).run(
        task,
        (resume_instruction + "\n\n" + prompt) if resume_instruction else prompt,
        metadata=metadata or {},
        fallback_context=profile.profile_yaml,
    )
