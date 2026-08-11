from __future__ import annotations

import json

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool
from sqlmodel import select

from ..db import get_session
from ..models import AgentEvent, Company, CompanySource, CompanyStatus, Feedback, Job, Match, MemoryRevision
from ..workspace import CandidateWorkspace


def _result(value) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(value, default=str)}]}


def build_candidate_tools_server(workspace: CandidateWorkspace, enabled: list[str]):
    @tool(
        "load_search_state",
        "Load approved candidate context or memory sections. Use before a candidate-specific judgment. Paths must be relative to the candidate workspace.",
        {"sections": list[str]},
    )
    async def load_search_state(args):
        return _result({"context": workspace.load_context(args["sections"]), "harness_hash": workspace.harness_hash})

    @tool(
        "query_job_history",
        "Return recent scored jobs and explicit review state so the agent does not repeatedly surface rejected patterns.",
        {"limit": int},
    )
    async def query_job_history(args):
        limit = max(1, min(int(args.get("limit", 25)), 100))
        with get_session() as session:
            rows = session.exec(
                select(Match, Job).join(Job, Match.job_id == Job.id).order_by(Match.created_at.desc()).limit(limit)
            ).all()
        return _result(
            [
                {"job_id": job.id, "title": job.title, "score": match.score, "recommendation": match.recommendation}
                for match, job in rows
            ]
        )

    @tool(
        "record_observation",
        "Append an evidence-backed operational observation. This cannot modify candidate policy or memory files.",
        {"event_type": str, "payload": str, "source": str},
    )
    async def record_observation(args):
        with get_session() as session:
            event = AgentEvent(
                event_type=args["event_type"],
                payload_json=args["payload"],
                source=args.get("source", "agent"),
                harness_hash=workspace.harness_hash,
            )
            session.add(event)
            session.commit()
            session.refresh(event)
        return _result({"event_id": event.id})

    @tool(
        "suggest_company",
        "Create a pending company suggestion with rationale. Never activates or ingests the company automatically.",
        {"name": str, "careers_url": str, "rationale": str},
    )
    async def suggest_company(args):
        with get_session() as session:
            existing = session.exec(select(Company).where(Company.name == args["name"])).first()
            if existing:
                return _result({"company_id": existing.id, "status": existing.status, "existing": True})
            company = Company(
                name=args["name"],
                careers_url=args.get("careers_url") or None,
                discovery_rationale=args["rationale"],
                source=CompanySource.discovered,
                status=CompanyStatus.pending_approval,
            )
            session.add(company)
            session.commit()
            session.refresh(company)
        return _result({"company_id": company.id, "status": company.status, "existing": False})

    @tool(
        "record_feedback",
        "Record an explicit candidate correction about a job recommendation. This is evidence, not permission to change policy.",
        {"job_id": int, "label": str, "reason": str},
    )
    async def record_feedback(args):
        with get_session() as session:
            feedback = Feedback(job_id=args["job_id"], label=args["label"], reason=args["reason"])
            session.add(feedback)
            session.commit()
            session.refresh(feedback)
        return _result({"feedback_id": feedback.id})

    @tool(
        "propose_memory_patch",
        "Propose replacement Markdown for a policy or memory section. The proposal remains pending until the candidate approves it.",
        {"section": str, "content": str, "source_event_start": int, "source_event_end": int},
    )
    async def propose_memory_patch(args):
        section = args["section"]
        if not (section.startswith("memory/") or section.startswith("policy/")) or not section.endswith(".md"):
            return _result({"error": "section must be a Markdown file under memory/ or policy/"})
        with get_session() as session:
            revision = MemoryRevision(
                section=section,
                content=args["content"],
                source_event_start=args.get("source_event_start"),
                source_event_end=args.get("source_event_end"),
            )
            session.add(revision)
            session.commit()
            session.refresh(revision)
        return _result({"revision_id": revision.id, "status": revision.status})

    @tool(
        "ingest_job_source",
        "Run the configured ingestion connector for one approved company and return normalized counts.",
        {"company_name": str},
    )
    async def ingest_job_source(args):
        from ..pipeline.ingest import run_ingest

        return _result(await run_ingest(company_name=args["company_name"]))

    @tool(
        "verify_posting",
        "Verify that a canonical company job URL still resolves. This does not mutate job state.",
        {"url": str},
    )
    async def verify_posting(args):
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            response = await client.get(args["url"])
        return _result({"url": str(response.url), "status_code": response.status_code, "open": response.status_code < 400})

    available = {
        item.name: item
        for item in [
            load_search_state,
            query_job_history,
            record_observation,
            suggest_company,
            record_feedback,
            propose_memory_patch,
            ingest_job_source,
            verify_posting,
        ]
    }
    unknown = set(enabled) - available.keys()
    if unknown:
        raise ValueError(f"Unknown candidate tools: {sorted(unknown)}")
    return create_sdk_mcp_server("candidate", tools=[available[name] for name in enabled])
