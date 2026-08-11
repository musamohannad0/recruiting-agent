from __future__ import annotations

import typer
from sqlmodel import select

from ..db import get_session, init_db
from ..models import AtsType, Company, CompanySource, CompanyStatus, RunKind
from ..settings import settings
from .runtime import AgentRuntime, TaskSpec, default_runtime

SYSTEM_PROMPT = """\
You scout companies for a candidate's job search. Use the approved company thesis,
scope mode, inclusions, and exclusions. In strict mode, return no adjacent companies.
In exploratory mode, use web search to find evidence-backed candidates missing from
the current list.

Rules:
- Never return a company already on the current list (including obvious aliases).
- Prefer companies actively hiring, with a real careers page — include its URL.
- Return at most 10 candidates with a concise rationale and evidence URL.
- Suggestions require human approval and must never be described as already tracked."""

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "careers_url": {"type": "string"},
                    "rationale": {"type": "string"},
                    "evidence_url": {"type": "string"},
                },
                "required": ["name", "rationale"],
            },
        }
    },
    "required": ["candidates"],
}


async def run_discovery(runtime: AgentRuntime | None = None) -> dict:
    from ..connectors.probe import probe_one
    from ..pipeline.runs import track_run

    init_db()
    stats = {"candidates": 0, "added": 0, "skipped_existing": 0}

    with get_session() as session:
        with track_run(session, RunKind.discover) as handle:
            existing = session.exec(select(Company)).all()
            existing_names = {c.name.lower() for c in existing}
            current_list = "\n".join(f"- {c.name}" for c in existing)

            prompt = (
                "## Current company list (do NOT return any of these)\n\n"
                + current_list
                + "\n\nFollow the candidate company thesis and propose any permitted missing companies."
            )
            task = TaskSpec(
                name="discover_companies",
                model=settings.discovery_model,
                system_prompt=SYSTEM_PROMPT,
                schema=SCHEMA,
                context_sections=("policy/company-thesis.md", "policy/search-constitution.md"),
                skills=("candidate-search-policy", "curate-companies"),
                tools=("WebSearch", "WebFetch"),
                max_turns=30,
            )
            result = await (runtime or default_runtime()).run(task, prompt)
            candidates = result.data.get("candidates", [])
            stats["candidates"] = len(candidates)

            for cand in candidates:
                name = cand["name"].strip()
                if name.lower() in existing_names:
                    stats["skipped_existing"] += 1
                    continue
                resolved = await probe_one(name, _slug_variants(name))
                company = Company(
                    name=name,
                    careers_url=cand.get("careers_url"),
                    status=CompanyStatus.pending_approval,
                    source=CompanySource.discovered,
                    discovery_rationale=cand.get("rationale", ""),
                )
                if resolved:
                    company.ats_type, company.board_token = resolved
                else:
                    company.ats_type = AtsType.unsupported
                session.add(company)
                session.commit()
                stats["added"] += 1
                ats = f"{company.ats_type}/{company.board_token}" if company.board_token else "unresolved ATS"
                typer.echo(f"+ {name} ({ats}) — {company.discovery_rationale}")

            handle.stats = stats

    typer.echo(f"Discovery done: {stats}")
    return stats


def _slug_variants(name: str) -> list[str]:
    return [
        name.lower().replace(" ", ""),
        name.lower().replace(" ", "-"),
        name,
    ]
