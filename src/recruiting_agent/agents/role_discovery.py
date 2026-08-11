from __future__ import annotations

import hashlib
from collections.abc import Callable
from urllib.parse import urlparse

from sqlmodel import select

from ..connectors.base import JobPosting
from ..db import get_session
from ..models import Company, CompanyStatus, JobSource, utcnow
from ..pipeline.ingest import upsert_jobs
from ..settings import settings
from .runtime import AgentRuntime, TaskSpec, default_runtime

SYSTEM_PROMPT = """\
You discover currently open jobs on an approved company's own careers domain. Use
web search and fetch to verify each posting. Follow the candidate's confirmed search
constitution. Return only canonical company-hosted postings that appear open now.
Coverage may be incomplete; never invent a role or claim the result is exhaustive."""

SCHEMA = {
    "type": "object",
    "properties": {
        "postings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "location": {"type": ["string", "null"]},
                    "department": {"type": ["string", "null"]},
                    "url": {"type": "string"},
                    "apply_url": {"type": ["string", "null"]},
                    "description_md": {"type": "string"},
                },
                "required": ["title", "url", "description_md"],
            },
        },
        "coverage_note": {"type": "string"},
    },
    "required": ["postings", "coverage_note"],
}


async def run_role_discovery(
    runtime: AgentRuntime | None = None,
    session_factory: Callable = get_session,
) -> dict:
    totals = {"sources_checked": 0, "new": 0, "updated": 0, "errors": 0}
    with session_factory() as session:
        rows = [
            (source.id, company.id)
            for source, company in session.exec(
                select(JobSource, Company)
                .join(Company, JobSource.company_id == Company.id)
                .where(
                    JobSource.source_type == "career_site",
                    JobSource.status != "paused",
                    Company.status == CompanyStatus.active,
                )
            ).all()
        ]
    for source_id, company_id in rows:
        with session_factory() as session:
            source = session.get(JobSource, source_id)
            company = session.get(Company, company_id)
            if not company.careers_url:
                source.status = "needs_configuration"
                source.last_error = "Missing careers URL"
                session.add(source)
                session.commit()
                totals["errors"] += 1
                continue
            company_name = company.name
            careers_url = company.careers_url

        task = TaskSpec(
            name="discover_roles",
            system_prompt=SYSTEM_PROMPT,
            schema=SCHEMA,
            model=settings.discovery_model,
            context_sections=("policy/search-constitution.md", "policy/decision-rubric.md"),
            skills=("candidate-search-policy", "discover-roles"),
            tools=("WebSearch", "WebFetch"),
            max_turns=20,
        )
        try:
            result = await (runtime or default_runtime()).run(
                task,
                f"Company: {company_name}\nCareers URL: {careers_url}",
                metadata={"company_id": company_id, "source_id": source_id},
            )
            postings = [
                _normalize_posting(item)
                for item in result.data.get("postings", [])
                if _same_career_domain(item.get("url", ""), careers_url)
            ]
            with session_factory() as session:
                source = session.get(JobSource, source_id)
                company = session.get(Company, company_id)
                stats = upsert_jobs(session, company, postings, deactivate_missing=False)
                totals["new"] += stats["new"]
                totals["updated"] += stats["updated"]
                totals["sources_checked"] += 1
                source.status = "active"
                source.last_checked_at = utcnow()
                source.last_error = None
                session.add(source)
                session.commit()
        except Exception as exc:
            with session_factory() as session:
                source = session.get(JobSource, source_id)
                source.last_checked_at = utcnow()
                source.last_error = str(exc)[:500]
                session.add(source)
                session.commit()
                totals["errors"] += 1
    return totals


def _normalize_posting(item: dict) -> JobPosting:
    url = item["url"]
    external_id = hashlib.sha256(url.encode()).hexdigest()[:24]
    return JobPosting(
        external_id=external_id,
        title=item["title"],
        location=item.get("location"),
        department=item.get("department"),
        url=url,
        apply_url=item.get("apply_url"),
        description_md=item.get("description_md", ""),
        posted_at=None,
    )


def _same_career_domain(url: str, careers_url: str) -> bool:
    candidate = (urlparse(url).hostname or "").lower()
    approved = (urlparse(careers_url).hostname or "").lower()
    if not candidate or not approved:
        return False
    approved_root = ".".join(approved.split(".")[-2:])
    candidate_root = ".".join(candidate.split(".")[-2:])
    return approved_root == candidate_root
