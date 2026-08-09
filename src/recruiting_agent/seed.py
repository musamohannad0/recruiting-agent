from __future__ import annotations

import yaml
from sqlmodel import Session, select

from .models import Company
from .settings import settings


def load_company_config() -> list[dict]:
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


def seed_companies(session: Session) -> tuple[int, int]:
    """Insert companies from config that aren't in the DB yet. Returns (added, existing)."""
    added = existing = 0
    for entry in load_company_config():
        row = session.exec(select(Company).where(Company.name == entry["name"])).first()
        if row is None:
            session.add(Company(name=entry["name"], careers_url=entry.get("careers_url")))
            added += 1
        else:
            existing += 1
    session.commit()
    return added, existing
