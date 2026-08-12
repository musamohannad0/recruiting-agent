from __future__ import annotations

import asyncio
import json

import typer
from sqlmodel import select

from ..db import get_session, init_db
from ..models import AtsType, Company, CompanyStatus, JobSource
from ..seed import load_company_config, slug_candidates
from .registry import CONNECTORS

# Probe Greenhouse before Lever/Ashby: it's the most common and its API is cheap to check.
PROBE_ORDER = [AtsType.greenhouse, AtsType.lever, AtsType.ashby]


async def probe_one(name: str, candidates: list[str]) -> tuple[AtsType, str] | None:
    """Try each (ATS, slug) pair; return the first that resolves to a live board."""
    for slug in candidates:
        checks = {ats: CONNECTORS[ats].board_exists(slug) for ats in PROBE_ORDER}
        results = await asyncio.gather(*checks.values())
        for ats, ok in zip(checks.keys(), results):
            if ok:
                return ats, slug
    return None


async def probe_companies(name: str | None = None, force: bool = False) -> None:
    init_db()
    config_by_name = {entry["name"]: entry for entry in load_company_config()}

    with get_session() as session:
        query = select(Company).where(Company.status == CompanyStatus.active)
        if name:
            query = query.where(Company.name == name)
        companies = session.exec(query).all()

        for company in companies:
            already_resolved = company.ats_type != AtsType.unsupported and company.board_token
            if already_resolved and not force:
                typer.echo(f"  {company.name}: already {company.ats_type}/{company.board_token}")
                continue
            entry = config_by_name.get(company.name, {"name": company.name})
            result = await probe_one(company.name, slug_candidates(entry))
            if result:
                company.ats_type, company.board_token = result
                typer.echo(f"✓ {company.name}: {result[0]}/{result[1]}")
            else:
                company.ats_type = AtsType.unsupported
                company.board_token = None
                typer.echo(f"✗ {company.name}: no supported ATS found (workday/custom?)")
            session.add(company)
            session.commit()
            source_type = company.ats_type.value if company.ats_type != AtsType.unsupported else "career_site"
            source = session.exec(
                select(JobSource).where(
                    JobSource.company_id == company.id,
                    JobSource.source_type == source_type,
                )
            ).first()
            if source is None:
                source = JobSource(company_id=company.id, source_type=source_type)
            source.config_json = json.dumps({"board_token": company.board_token}) if company.board_token else "{}"
            source.is_authoritative = company.ats_type != AtsType.unsupported
            source.status = "active" if company.board_token else "needs_configuration"
            session.add(source)
            session.commit()
