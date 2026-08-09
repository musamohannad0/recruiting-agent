from __future__ import annotations

import typer
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from sqlmodel import select

from ..db import get_session, init_db
from ..models import AtsType, Company, CompanySource, CompanyStatus, RunKind
from ..settings import settings

SYSTEM_PROMPT = """\
You are scouting companies for a candidate's job search. The candidate tracks \
"frontier" companies: leading AI labs and high-growth, well-funded AI/deep-tech \
startups (the kind that attract top-tier talent and investors).

Use web search to find companies MISSING from their current list that fit that bar \
today: recently prominent AI labs, breakout AI application/infra startups with \
significant recent funding, and serious deep-tech companies (robotics, chips, bio+AI).

Rules:
- Never return a company already on the current list (including obvious aliases).
- Prefer companies actively hiring, with a real careers page — include its URL.
- 5 to 10 candidates, each with a one-line rationale citing concrete evidence
  (funding round, launch, notable traction) and the URL where you saw it."""

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


async def run_discovery() -> dict:
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
                + "\n\nFind frontier companies missing from this list."
            )
            options = ClaudeAgentOptions(
                model=settings.discovery_model,
                system_prompt=SYSTEM_PROMPT,
                allowed_tools=["WebSearch", "WebFetch"],
                max_turns=30,
                output_format={"type": "json_schema", "schema": SCHEMA},
            )
            final: ResultMessage | None = None
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    final = message
            if final is None or final.is_error or final.structured_output is None:
                raise RuntimeError(f"discovery failed: {final and (final.errors or final.result)}")

            candidates = final.structured_output.get("candidates", [])
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
