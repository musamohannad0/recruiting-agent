from __future__ import annotations

from dataclasses import dataclass

from ..settings import settings
from .llm import llm_json
from .profile import Profile

SYSTEM_PROMPT = """\
You are a lenient first-pass screener for a job search. You will see a candidate \
profile and a numbered list of job postings (title, company, department, location \
only). Decide for each posting whether it could PLAUSIBLY be a fit worth a closer look.

Be generous: this is only a cheap gate before a smarter model reads the full job \
description. Titles are noisy — a "Chief of Staff", "Strategic Projects", "Revenue \
Strategy", "Monetization", or ambiguous operations title can absolutely be a fit for \
a business-operations candidate. When in doubt, mark it plausible.

Reject only postings that clearly cannot fit the profile's target roles and hard \
filters: e.g. IC software/ML engineering, research scientist, hardware engineering, \
quota-carrying sales, recruiting, legal, internships, facilities.

Return a verdict for EVERY index in the input list, in order."""

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


async def prefilter_batch(profile: Profile, jobs: list[PrefilterInput]) -> dict[int, tuple[str, str]]:
    """Returns {job_id: (verdict, reason)}. Missing verdicts default to plausible."""
    lines = [
        f"{i}. {j.title} — {j.company}"
        + (f" | {j.department}" if j.department else "")
        + (f" | {j.location}" if j.location else "")
        for i, j in enumerate(jobs)
    ]
    prompt = (
        "## Candidate profile\n\n"
        + profile.profile_yaml
        + "\n\n## Job postings\n\n"
        + "\n".join(lines)
    )
    result = await llm_json(
        name="prefilter",
        model=settings.prefilter_model,
        system_prompt=SYSTEM_PROMPT,
        prompt=prompt,
        schema=SCHEMA,
        effort="low",
        metadata={"batch_size": len(jobs)},
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
