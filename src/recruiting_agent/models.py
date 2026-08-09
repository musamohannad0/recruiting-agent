from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from sqlmodel import Field, SQLModel, UniqueConstraint


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AtsType(StrEnum):
    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    unsupported = "unsupported"


class CompanyStatus(StrEnum):
    active = "active"
    pending_approval = "pending_approval"
    rejected = "rejected"
    paused = "paused"


class CompanySource(StrEnum):
    seed = "seed"
    discovered = "discovered"


class Company(SQLModel, table=True):
    __tablename__ = "companies"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    ats_type: AtsType = AtsType.unsupported
    board_token: str | None = None
    careers_url: str | None = None
    status: CompanyStatus = CompanyStatus.active
    source: CompanySource = CompanySource.seed
    discovery_rationale: str | None = None
    last_ingest_at: datetime | None = None
    last_ingest_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("company_id", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    external_id: str
    title: str
    location: str | None = None
    department: str | None = None
    url: str
    apply_url: str | None = None
    description_md: str = ""
    content_hash: str = Field(index=True)
    is_active: bool = Field(default=True, index=True)
    posted_at: datetime | None = None
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)


class PrefilterVerdict(StrEnum):
    plausible = "plausible"
    rejected = "rejected"


class Prefilter(SQLModel, table=True):
    """Cheap-model gate: could this role plausibly fit the profile?"""

    __tablename__ = "prefilters"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    content_hash: str
    profile_hash: str
    verdict: PrefilterVerdict
    reason: str = ""
    model: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Recommendation(StrEnum):
    apply = "apply"
    maybe = "maybe"
    skip = "skip"


class Match(SQLModel, table=True):
    __tablename__ = "matches"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    content_hash: str
    profile_hash: str
    score: int = Field(ge=0, le=100)
    recommendation: Recommendation
    reasoning: str = ""
    red_flags: str = ""  # JSON list
    seniority_fit: str = ""
    location_fit: str = ""
    model: str = ""
    prompt_version: str = ""
    langfuse_trace_id: str | None = None
    user_status: str | None = Field(default=None, index=True)  # saved | dismissed | applied
    created_at: datetime = Field(default_factory=utcnow)


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str


class RunKind(StrEnum):
    ingest = "ingest"
    match = "match"
    discover = "discover"


class RunStatus(StrEnum):
    running = "running"
    success = "success"
    failed = "failed"


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    kind: RunKind
    status: RunStatus = RunStatus.running
    stats_json: str = "{}"
    error: str | None = None
    langfuse_trace_id: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


DEFAULT_SETTINGS: dict[str, str] = {
    "min_score_display": "60",
}
