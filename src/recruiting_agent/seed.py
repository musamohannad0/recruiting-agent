from __future__ import annotations

import yaml
from sqlmodel import Session, select

from .models import Company, CompanyStatus
from .settings import settings
from .workspace import CandidateWorkspace, workspace


def load_company_config(candidate_workspace: CandidateWorkspace | None = None) -> list[dict]:
    candidate_workspace = candidate_workspace or workspace
    if candidate_workspace.is_ready:
        candidate_config = candidate_workspace.root / "policy" / "companies.yaml"
        data = yaml.safe_load(candidate_config.read_text()) or {}
        catalog: dict[str, dict] = {}
        if settings.companies_yaml.exists():
            base_data = yaml.safe_load(settings.companies_yaml.read_text()) or {}
            for entry in base_data.get("companies", []):
                catalog[entry["name"].casefold()] = entry
        presets_dir = settings.config_dir / "presets"
        if presets_dir.exists():
            for preset in presets_dir.glob("*.yaml"):
                preset_data = yaml.safe_load(preset.read_text()) or {}
                for entry in preset_data.get("companies", []):
                    catalog[entry["name"].casefold()] = entry
        return [
            catalog.get(name.casefold(), {"name": name})
            for name in data.get("excited", []) + data.get("acceptable", [])
        ]
    with open(settings.companies_yaml) as f:
        return yaml.safe_load(f)["companies"]


def slug_candidates(entry: dict) -> list[str]:
    """Board-token candidates for a company: explicit hints first, then name variants."""
    name = entry["name"]
    variants = [
        name.lower().replace(" ", ""),
        name.lower().replace(" ", "-"),
        name.lower().replace(" ", "_"),
    ]
    candidates = list(entry.get("slugs", [])) + variants
    seen: set[str] = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


def seed_companies(
    session: Session, candidate_workspace: CandidateWorkspace | None = None
) -> tuple[int, int]:
    """Insert companies from config that aren't in the DB yet. Returns (added, existing)."""
    added = existing = 0
    for entry in load_company_config(candidate_workspace):
        row = session.exec(select(Company).where(Company.name == entry["name"])).first()
        if row is None:
            session.add(Company(name=entry["name"], careers_url=entry.get("careers_url")))
            added += 1
        else:
            existing += 1
    session.commit()
    return added, existing


def sync_candidate_companies(
    session: Session, candidate_workspace: CandidateWorkspace | None = None
) -> tuple[int, int]:
    """Activate the approved universe and pause unrelated legacy rows without deleting history."""
    configured = load_company_config(candidate_workspace)
    approved = {entry["name"].casefold() for entry in configured}
    added, existing = seed_companies(session, candidate_workspace)
    for company in session.exec(select(Company)).all():
        company.status = (
            CompanyStatus.active if company.name.casefold() in approved else CompanyStatus.paused
        )
        session.add(company)
    session.commit()
    return added, existing
