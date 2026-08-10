from __future__ import annotations

from ..settings import settings
from .llm import LLMResult, llm_json
from .profile import Profile

PROMPT_VERSION = "v2"

SYSTEM_PROMPT = """\
You are an exacting career advisor evaluating whether a specific job posting is a \
strong fit for a specific candidate. You will see the candidate's resume, their \
structured profile (target roles, seniority, preferences, hard filters), and the \
full job posting.

Score 0-100 for overall fit:
- 90-100: near-perfect — role family, seniority, and location all align; the \
candidate's pricing/bizops/consulting background is a direct asset.
- 75-89: strong fit — right role family and level, minor concerns (location \
stretch, slightly different emphasis).
- 60-74: plausible — worth a look, but a meaningful gap (seniority stretch, \
adjacent role family, heavy domain requirement the candidate lacks).
- 40-59: weak — significant mismatch in role substance or level.
- 0-39: poor — wrong role family or clearly unattainable requirements.

Judge on substance, not title keywords. A "Chief of Staff" or "Strategic Projects" \
role that is really business operations can score high; an "Operations" role that is \
really warehouse logistics or people ops should score low.

Seniority: do NOT anchor on years-of-experience. The candidate (~5 years: 3 \
consulting, 2 bizops) wants the coolest, highest-impact roles and is happy to \
stretch — a posting asking for 6-7 years or a bigger scope than she's held is NOT \
a meaningful penalty. Only penalize egregious gaps: hard requirements of 8+ years, \
VP/executive scope, or deep domain expertise she clearly lacks. Ambition in a role \
is a plus, not a red flag.

Location is a hard constraint: New York, San Francisco / Bay Area, or US-remote \
only. If the role is based outside the US (London, Singapore, EMEA, APAC, ...) \
with no NYC/SF/US-remote option, score it <=40 and recommend "skip" regardless of \
role fit.

recommendation: "apply" (>=75 and no disqualifying red flag), "maybe" (60-74 or \
strong fit with one real concern), else "skip"."""

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
) -> LLMResult:
    prompt = (
        "## Candidate resume\n\n"
        + profile.resume_md
        + "\n\n## Candidate profile & preferences\n\n"
        + profile.profile_yaml
        + "\n\n## Job posting\n\n"
        + f"Company: {company}\nTitle: {title}\n"
        + (f"Department: {department}\n" if department else "")
        + (f"Location: {location}\n" if location else "")
        + "\n"
        + description_md[:MAX_DESCRIPTION_CHARS]
    )
    return await llm_json(
        name="score_job",
        model=settings.scoring_model,
        system_prompt=SYSTEM_PROMPT,
        prompt=prompt,
        schema=SCHEMA,
        metadata=metadata or {},
    )
