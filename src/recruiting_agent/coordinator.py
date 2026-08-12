from __future__ import annotations

import inspect
import json
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, ContextManager

import yaml
from sqlalchemy import or_, update
from sqlmodel import Session, func, select

from .db import get_session, init_db
from .models import (
    ActionStatus,
    AgentAction,
    AgentCycle,
    AgentEvent,
    CoordinatorLease,
    CycleStatus,
    Feedback,
    MemoryRevision,
    utcnow,
)
from .workspace import CandidateWorkspace, workspace

SessionFactory = Callable[[], ContextManager[Session]]
Operation = Callable[[], Any]


async def _ingest() -> dict:
    from .pipeline.ingest import run_ingest

    return await run_ingest()


async def _probe() -> dict:
    from .connectors.probe import probe_companies

    await probe_companies()
    return {"status": "source probing complete"}


async def _match() -> dict:
    from .pipeline.match import run_match

    return await run_match()


async def _discover() -> dict:
    from .agents.discovery import run_discovery

    return await run_discovery()


async def _scout_unsupported() -> dict:
    from .agents.role_discovery import run_role_discovery

    return await run_role_discovery()


@dataclass
class CoordinatorOperations:
    probe: Operation = _probe
    ingest: Operation = _ingest
    match: Operation = _match
    discover: Operation = _discover
    scout: Operation = _scout_unsupported
    consolidate_memory: Operation | None = None


@dataclass
class SearchCoordinator:
    candidate_workspace: CandidateWorkspace = field(default_factory=lambda: workspace)
    session_factory: SessionFactory = get_session
    operations: CoordinatorOperations = field(default_factory=CoordinatorOperations)
    lease_minutes: int = 90
    now: Callable[[], datetime] = utcnow
    initialize: Callable[[], None] = init_db

    async def run_cycle(self, trigger: str = "scheduled") -> dict[str, Any]:
        self.initialize()
        token = uuid.uuid4().hex
        if not self._acquire_lease(token):
            return {"status": CycleStatus.skipped.value, "reason": "coordinator lease held"}

        cycle = self._start_cycle(trigger)
        stats: dict[str, Any] = {"actions": {}, "trigger": trigger}
        try:
            cadence = self._load_cadence()
            scope = self._company_scope()
            action_specs: list[tuple[str, str, Operation]] = [
                ("probe", self._bucket(hours=int(cadence["career_site_discovery_hours"])), self.operations.probe),
                ("ingest", self._bucket(hours=int(cadence["ats_ingest_hours"])), self.operations.ingest),
                ("match", self._bucket(hours=int(cadence["ats_ingest_hours"])), self.operations.match),
                ("scout", self._bucket(hours=int(cadence["career_site_discovery_hours"])), self.operations.scout),
            ]
            if scope == "exploratory":
                action_specs.append(
                    (
                        "discover",
                        self._bucket(days=int(cadence["company_discovery_days"])),
                        self.operations.discover,
                    )
                )
            memory_operation = self.operations.consolidate_memory or self._consolidate_memory
            action_specs.append(
                (
                    "consolidate_memory",
                    self._bucket(days=int(cadence["memory_consolidation_days"])),
                    memory_operation,
                )
            )

            for kind, bucket, operation in action_specs:
                self._set_phase(cycle.id, kind)
                key = f"{self.candidate_workspace.harness_hash}:{kind}:{bucket}"
                stats["actions"][kind] = await self._execute_action(cycle.id, key, kind, operation)

            corrections = self._explicit_feedback_count()
            threshold = int(cadence["feedback_recalibration_threshold"])
            if corrections >= threshold:
                self._record_event(
                    "recalibration_due",
                    {"explicit_feedback": corrections, "threshold": threshold},
                    cycle.id,
                )
                stats["recalibration_due"] = True

            self._finish_cycle(cycle.id, CycleStatus.success, stats=stats)
            return {"status": CycleStatus.success.value, "cycle_id": cycle.id, **stats}
        except Exception as exc:
            self._finish_cycle(
                cycle.id,
                CycleStatus.failed,
                stats=stats,
                error=f"{exc}\n{traceback.format_exc(limit=5)}",
            )
            raise
        finally:
            self._release_lease(token)

    def _acquire_lease(self, token: str) -> bool:
        now = self.now()
        expires = now + timedelta(minutes=self.lease_minutes)
        with self.session_factory() as session:
            if session.get(CoordinatorLease, 1) is None:
                session.add(CoordinatorLease(id=1))
                session.commit()
            statement = (
                update(CoordinatorLease)
                .where(
                    CoordinatorLease.id == 1,
                    or_(CoordinatorLease.token.is_(None), CoordinatorLease.expires_at < now),
                )
                .values(token=token, expires_at=expires, updated_at=now)
            )
            result = session.exec(statement)
            session.commit()
            return bool(result.rowcount)

    def _release_lease(self, token: str) -> None:
        with self.session_factory() as session:
            session.exec(
                update(CoordinatorLease)
                .where(CoordinatorLease.id == 1, CoordinatorLease.token == token)
                .values(token=None, expires_at=None, updated_at=self.now())
            )
            session.commit()

    def _start_cycle(self, trigger: str) -> AgentCycle:
        with self.session_factory() as session:
            cycle = AgentCycle(
                trigger=trigger,
                harness_hash=self.candidate_workspace.harness_hash,
                checkpoint_json=json.dumps({"last_event_id": self._latest_event_id(session)}),
            )
            session.add(cycle)
            session.commit()
            session.refresh(cycle)
            return cycle

    async def _execute_action(
        self, cycle_id: int, key: str, kind: str, operation: Operation
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            action = session.exec(
                select(AgentAction).where(AgentAction.idempotency_key == key)
            ).first()
            if action and action.status == ActionStatus.success:
                return {"status": "cached", "result": json.loads(action.result_json or "{}")}
            if action is None:
                action = AgentAction(cycle_id=cycle_id, idempotency_key=key, kind=kind)
            else:
                action.cycle_id = cycle_id
            action.status = ActionStatus.running
            action.attempts += 1
            action.error = None
            session.add(action)
            session.commit()
            session.refresh(action)
            action_id = action.id

        try:
            result = operation()
            if inspect.isawaitable(result):
                result = await result
            normalized = result if isinstance(result, dict) else {"result": result}
        except Exception as exc:
            with self.session_factory() as session:
                action = session.get(AgentAction, action_id)
                action.status = ActionStatus.failed
                action.error = str(exc)[:1000]
                action.finished_at = self.now()
                session.add(action)
                session.commit()
            raise

        with self.session_factory() as session:
            action = session.get(AgentAction, action_id)
            action.status = ActionStatus.success
            action.result_json = json.dumps(normalized, default=str)
            action.finished_at = self.now()
            session.add(action)
            session.add(
                AgentEvent(
                    event_type=f"{kind}_completed",
                    payload_json=action.result_json,
                    cycle_id=cycle_id,
                    harness_hash=self.candidate_workspace.harness_hash,
                )
            )
            session.commit()
        return {"status": "success", "result": normalized}

    def _consolidate_memory(self) -> dict[str, Any]:
        with self.session_factory() as session:
            events = session.exec(select(AgentEvent).order_by(AgentEvent.id)).all()
            if not events:
                return {"events": 0}
            lines = ["# Search Journal", ""]
            for event in events[-100:]:
                lines.append(f"- Event {event.id}: `{event.event_type}` — {event.payload_json}")
            content = "\n".join(lines) + "\n"
            revision = MemoryRevision(
                section="memory/search-journal.md",
                content=content,
                status="approved",
                source_event_start=events[0].id,
                source_event_end=events[-1].id,
                decided_at=self.now(),
            )
            session.add(revision)
            session.commit()
        target = self.candidate_workspace.root / "memory" / "search-journal.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {"events": len(events), "last_event_id": events[-1].id}

    def _set_phase(self, cycle_id: int, phase: str) -> None:
        with self.session_factory() as session:
            cycle = session.get(AgentCycle, cycle_id)
            cycle.phase = phase
            session.add(cycle)
            session.commit()

    def _finish_cycle(
        self,
        cycle_id: int,
        status: CycleStatus,
        *,
        stats: dict[str, Any],
        error: str | None = None,
    ) -> None:
        with self.session_factory() as session:
            cycle = session.get(AgentCycle, cycle_id)
            cycle.status = status
            cycle.phase = "finished"
            cycle.stats_json = json.dumps(stats, default=str)
            cycle.error = error
            cycle.finished_at = self.now()
            cycle.checkpoint_json = json.dumps({"last_event_id": self._latest_event_id(session)})
            session.add(cycle)
            session.commit()

    def _record_event(self, event_type: str, payload: dict, cycle_id: int) -> None:
        with self.session_factory() as session:
            session.add(
                AgentEvent(
                    event_type=event_type,
                    payload_json=json.dumps(payload),
                    cycle_id=cycle_id,
                    harness_hash=self.candidate_workspace.harness_hash,
                )
            )
            session.commit()

    def _explicit_feedback_count(self) -> int:
        with self.session_factory() as session:
            return session.exec(
                select(func.count()).select_from(Feedback).where(Feedback.signal_strength == "explicit")
            ).one()

    @staticmethod
    def _latest_event_id(session: Session) -> int:
        return session.exec(select(func.max(AgentEvent.id))).one() or 0

    def _load_cadence(self) -> dict[str, int]:
        defaults = {
            "ats_ingest_hours": 2,
            "career_site_discovery_hours": 24,
            "company_discovery_days": 7,
            "memory_consolidation_days": 7,
            "feedback_recalibration_threshold": 5,
        }
        path = self.candidate_workspace.root / "policy" / "cadence.yaml"
        if path.exists():
            defaults.update(yaml.safe_load(path.read_text()) or {})
        return defaults

    def _company_scope(self) -> str:
        path = self.candidate_workspace.root / "policy" / "companies.yaml"
        if not path.exists():
            return "strict"
        return (yaml.safe_load(path.read_text()) or {}).get("scope", "strict")

    def _bucket(self, *, hours: int | None = None, days: int | None = None) -> str:
        seconds = (hours * 3600) if hours is not None else (days or 1) * 86400
        return str(int(self.now().timestamp()) // seconds)
