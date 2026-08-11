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
    cost_usd: float | None = None
    user_status: str | None = Field(default=None, index=True)  # saved | dismissed | applied
    created_at: datetime = Field(default_factory=utcnow)


class JobReview(SQLModel, table=True):
    __tablename__ = "job_reviews"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", unique=True, index=True)
    user_status: str | None = Field(default=None, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class JobSource(SQLModel, table=True):
    __tablename__ = "job_sources"
    __table_args__ = (UniqueConstraint("company_id", "source_type"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    source_type: str
    config_json: str = "{}"
    is_authoritative: bool = True
    status: str = Field(default="active", index=True)
    last_checked_at: datetime | None = None
    last_error: str | None = None
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


class CycleStatus(StrEnum):
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class ActionStatus(StrEnum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"


class AgentCycle(SQLModel, table=True):
    __tablename__ = "agent_cycles"

    id: int | None = Field(default=None, primary_key=True)
    trigger: str
    status: CycleStatus = CycleStatus.running
    phase: str = "starting"
    checkpoint_json: str = "{}"
    stats_json: str = "{}"
    harness_hash: str = ""
    error: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class AgentAction(SQLModel, table=True):
    __tablename__ = "agent_actions"

    id: int | None = Field(default=None, primary_key=True)
    cycle_id: int = Field(foreign_key="agent_cycles.id", index=True)
    idempotency_key: str = Field(unique=True, index=True)
    kind: str = Field(index=True)
    target: str = ""
    status: ActionStatus = ActionStatus.pending
    attempts: int = 0
    result_json: str = "{}"
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class AgentEvent(SQLModel, table=True):
    __tablename__ = "agent_events"

    id: int | None = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)
    entity_type: str | None = None
    entity_id: str | None = None
    payload_json: str = "{}"
    source: str = "system"
    confidence: float | None = None
    cycle_id: int | None = Field(default=None, foreign_key="agent_cycles.id", index=True)
    harness_hash: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Feedback(SQLModel, table=True):
    __tablename__ = "feedback"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    label: str
    reason: str = ""
    signal_strength: str = "explicit"
    created_at: datetime = Field(default_factory=utcnow)


class MemoryRevision(SQLModel, table=True):
    __tablename__ = "memory_revisions"

    id: int | None = Field(default=None, primary_key=True)
    section: str
    content: str
    status: str = Field(default="proposed", index=True)
    source_event_start: int | None = None
    source_event_end: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None


class CoordinatorLease(SQLModel, table=True):
    __tablename__ = "coordinator_leases"

    id: int = Field(default=1, primary_key=True)
    token: str | None = None
    expires_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)


DEFAULT_SETTINGS: dict[str, str] = {
    "min_score_display": "60",
}
