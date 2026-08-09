from __future__ import annotations

import typer
from sqlmodel import Session, select

from ..connectors.base import JobPosting
from ..connectors.registry import CONNECTORS
from ..db import get_session, init_db
from ..models import AtsType, Company, CompanyStatus, Job, RunKind, utcnow


def upsert_jobs(session: Session, company: Company, postings: list[JobPosting]) -> dict:
    """Insert new postings, refresh seen ones, deactivate vanished ones."""
    existing = {
        j.external_id: j
        for j in session.exec(select(Job).where(Job.company_id == company.id)).all()
    }
    stats = {"new": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
    seen_ids = set()

    for p in postings:
        seen_ids.add(p.external_id)
        row = existing.get(p.external_id)
        if row is None:
            session.add(
                Job(
                    company_id=company.id,
                    external_id=p.external_id,
                    title=p.title,
                    location=p.location,
                    department=p.department,
                    url=p.url,
                    apply_url=p.apply_url,
                    description_md=p.description_md,
                    content_hash=p.content_hash,
                    posted_at=p.posted_at,
                )
            )
            stats["new"] += 1
        else:
            row.last_seen_at = utcnow()
            if not row.is_active:
                row.is_active = True
            if row.content_hash != p.content_hash:
                row.title = p.title
                row.location = p.location
                row.department = p.department
                row.description_md = p.description_md
                row.content_hash = p.content_hash
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
            session.add(row)

    for external_id, row in existing.items():
        if external_id not in seen_ids and row.is_active:
            row.is_active = False
            session.add(row)
            stats["deactivated"] += 1

    session.commit()
    return stats


async def run_ingest(company_name: str | None = None) -> dict:
    from .runs import track_run

    init_db()
    totals = {"companies": 0, "errors": 0, "new": 0, "updated": 0, "deactivated": 0}

    with get_session() as session:
        with track_run(session, RunKind.ingest) as handle:
            query = select(Company).where(
                Company.status == CompanyStatus.active,
                Company.ats_type != AtsType.unsupported,
            )
            if company_name:
                query = select(Company).where(Company.name == company_name)
            companies = session.exec(query).all()

            for company in companies:
                if company.ats_type == AtsType.unsupported or not company.board_token:
                    typer.echo(f"- {company.name}: unsupported ATS, skipped")
                    continue
                connector = CONNECTORS[company.ats_type]
                try:
                    postings = await connector.fetch(company.board_token)
                except Exception as exc:
                    company.last_ingest_error = str(exc)[:500]
                    session.add(company)
                    session.commit()
                    totals["errors"] += 1
                    typer.echo(f"✗ {company.name}: {exc}")
                    continue
                stats = upsert_jobs(session, company, postings)
                company.last_ingest_at = utcnow()
                company.last_ingest_error = None
                session.add(company)
                session.commit()
                totals["companies"] += 1
                for k in ("new", "updated", "deactivated"):
                    totals[k] += stats[k]
                typer.echo(
                    f"✓ {company.name}: {len(postings)} open, "
                    f"{stats['new']} new, {stats['updated']} updated, {stats['deactivated']} closed"
                )

            handle.stats = totals
    typer.echo(f"Ingest done: {totals}")
    return totals
