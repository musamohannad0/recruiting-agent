from __future__ import annotations

import asyncio
import inspect
import json
import traceback
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ContextManager

import yaml
from sqlalchemy import or_, update
from sqlmodel import Session, func, select

from .agents.llm import UsageTotals, track_usage
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
from .reporting import humanize_key, summarize_action
from .workspace import CandidateWorkspace, workspace

SessionFactory = Callable[[], ContextManager[Session]]
Operation = Callable[[], Any]


@dataclass(frozen=True)
class JournalEntry:
    """One recorded event, detached from the session that loaded it."""

    id: int
    event_type: str
    payload: dict
    created_at: datetime


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
    lease_minutes: float = 90
    lease_heartbeat_seconds: float = 30.0
    now: Callable[[], datetime] = utcnow
    initialize: Callable[[], None] = init_db

    async def run_cycle(self, trigger: str = "scheduled") -> dict[str, Any]:
        self.initialize()
        if not self.candidate_workspace.is_ready:
            return {
                "status": CycleStatus.skipped.value,
                "reason": "candidate onboarding incomplete",
            }
        token = uuid.uuid4().hex
        if not self._acquire_lease(token):
            return {"status": CycleStatus.skipped.value, "reason": "coordinator lease held"}

        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat_lease(token, heartbeat_stop))
        cycle: AgentCycle | None = None
        stats: dict[str, Any] = {"actions": {}, "trigger": trigger}
        try:
            cycle = self._start_cycle(trigger)
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

            failed_actions: list[str] = []
            for kind, bucket, operation in action_specs:
                self._set_phase(cycle.id, kind)
                key = f"{self.candidate_workspace.harness_hash}:{kind}:{bucket}"
                outcome = await self._execute_action(cycle.id, key, kind, operation)
                stats["actions"][kind] = outcome
                if outcome["status"] == "failed":
                    # A failing probe must not cost the operator this cycle's matching.
                    failed_actions.append(kind)
                    self._record_event(
                        "action_failed", {"kind": kind, "error": outcome.get("error", "")}, cycle.id
                    )

            corrections = self._explicit_feedback_count()
            threshold = int(cadence["feedback_recalibration_threshold"])
            if corrections >= threshold:
                self._record_event(
                    "recalibration_due",
                    {"explicit_feedback": corrections, "threshold": threshold},
                    cycle.id,
                )
                stats["recalibration_due"] = True

            usage = self._cycle_usage(cycle.id)
            stats.update(usage.as_dict())
            status = CycleStatus.partial if failed_actions else CycleStatus.success
            summary = (
                f"{len(failed_actions)} of {len(action_specs)} actions failed: "
                + ", ".join(humanize_key(kind) for kind in failed_actions)
                if failed_actions
                else None
            )
            self._finish_cycle(cycle.id, status, stats=stats, error=summary, usage=usage)
            return {"status": status.value, "cycle_id": cycle.id, **stats}
        except Exception as exc:
            if cycle is not None:
                self._finish_cycle(
                    cycle.id,
                    CycleStatus.failed,
                    stats=stats,
                    error=f"{exc}\n{traceback.format_exc(limit=5)}",
                )
            raise
        finally:
            heartbeat_stop.set()
            heartbeat.cancel()
            try:
                with suppress(asyncio.CancelledError):
                    await heartbeat
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

    def _renew_lease(self, token: str) -> bool:
        now = self.now()
        with self.session_factory() as session:
            result = session.exec(
                update(CoordinatorLease)
                .where(CoordinatorLease.id == 1, CoordinatorLease.token == token)
                .values(
                    expires_at=now + timedelta(minutes=self.lease_minutes),
                    updated_at=now,
                )
            )
            session.commit()
            return bool(result.rowcount)

    async def _heartbeat_lease(self, token: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.lease_heartbeat_seconds)
            except TimeoutError:
                if not self._renew_lease(token):
                    raise RuntimeError("coordinator lease was lost during an active cycle")

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
        """Run one idempotent action, recording what it produced and what it cost.

        Returns rather than raises on failure: the caller decides whether a failed
        action ends the cycle, and it should not, so the remaining stages still run.
        """
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

        with track_usage() as usage:
            try:
                result = operation()
                if inspect.isawaitable(result):
                    result = await result
                normalized = result if isinstance(result, dict) else {"result": result}
            except Exception as exc:
                message = str(exc)[:1000]
                with self.session_factory() as session:
                    action = session.get(AgentAction, action_id)
                    action.status = ActionStatus.failed
                    action.error = message
                    action.finished_at = self.now()
                    self._apply_usage(action, usage)
                    session.add(action)
                    session.commit()
                return {"status": "failed", "error": message, "kind": kind}

        with self.session_factory() as session:
            action = session.get(AgentAction, action_id)
            action.status = ActionStatus.success
            action.result_json = json.dumps(normalized, default=str)
            action.finished_at = self.now()
            self._apply_usage(action, usage)
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

    @staticmethod
    def _apply_usage(action: AgentAction, usage: UsageTotals) -> None:
        action.cost_usd = round(usage.cost_usd, 6)
        action.llm_calls = usage.llm_calls
        action.input_tokens = usage.input_tokens
        action.output_tokens = usage.output_tokens
        action.cached_tokens = usage.cache_read_tokens

    def _cycle_usage(self, cycle_id: int) -> UsageTotals:
        """Roll this cycle's actions up into one set of totals."""
        totals = UsageTotals()
        with self.session_factory() as session:
            actions = session.exec(
                select(AgentAction).where(AgentAction.cycle_id == cycle_id)
            ).all()
            for action in actions:
                totals.llm_calls += action.llm_calls
                totals.input_tokens += action.input_tokens
                totals.output_tokens += action.output_tokens
                totals.cache_read_tokens += action.cached_tokens
                totals.cost_usd += action.cost_usd or 0.0
                totals.errors += 1 if action.status == ActionStatus.failed else 0
        return totals

    #: Events folded into one journal rewrite. The journal is a rolling window, not an
    #: append-only log, so it stays inside a prompt budget as the search runs for months.
    JOURNAL_EVENT_WINDOW = 200

    def _consolidate_memory(self) -> dict[str, Any]:
        """Rewrite the candidate's search journal as prose an agent can read back.

        This file is loaded as context on later runs, so it holds sentences rather
        than serialised payloads: a JSON dump costs tokens and tells the next agent
        nothing a summary would not.
        """
        with self.session_factory() as session:
            rows = session.exec(
                select(AgentEvent)
                .order_by(AgentEvent.id.desc())
                .limit(self.JOURNAL_EVENT_WINDOW)
            ).all()
            if not rows:
                return {"events": 0}
            # Read everything needed off the ORM instances now: commit expires them,
            # and the session closes before the file is written and the result returned.
            events = [
                JournalEntry(
                    id=event.id,
                    event_type=event.event_type,
                    payload=json.loads(event.payload_json or "{}") if event.payload_json else {},
                    created_at=event.created_at,
                )
                for event in reversed(rows)
            ]
            content = self._render_journal(events)
            session.add(
                MemoryRevision(
                    section="memory/search-journal.md",
                    content=content,
                    status="approved",
                    source_event_start=events[0].id,
                    source_event_end=events[-1].id,
                    decided_at=self.now(),
                )
            )
            session.commit()
        target = self.candidate_workspace.root / "memory" / "search-journal.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {"events": len(events), "last_event_id": events[-1].id}

    def _render_journal(self, events: list[JournalEntry]) -> str:
        """Group events by day and describe each one in a sentence."""
        lines = [
            "# Search journal",
            "",
            "What the agent has done recently, newest day last. Written by the coordinator "
            "after each memory-consolidation pass.",
            "",
        ]
        totals: dict[str, int] = {}
        by_day: dict[str, list[JournalEntry]] = {}
        # Group and order by timestamp rather than insertion order, so a backfilled or
        # out-of-order event still reads correctly.
        for event in sorted(events, key=lambda entry: entry.created_at):
            by_day.setdefault(event.created_at.strftime("%Y-%m-%d"), []).append(event)

        for day, day_events in by_day.items():
            lines.append(f"## {day}")
            lines.append("")
            for event in day_events:
                payload = event.payload if isinstance(event.payload, dict) else {"result": event.payload}
                kind = event.event_type.removesuffix("_completed")
                totals[kind] = totals.get(kind, 0) + 1
                if event.event_type.endswith("_completed"):
                    sentence = summarize_action(kind, "success", payload)
                elif event.event_type == "action_failed":
                    sentence = f"{humanize_key(payload.get('kind', 'An action'))} failed: {payload.get('error', 'no detail recorded')}"
                elif event.event_type == "recalibration_due":
                    sentence = (
                        f"{payload.get('explicit_feedback', 0)} explicit corrections have accumulated, "
                        f"at or past the threshold of {payload.get('threshold', 0)}; the rubric wants review."
                    )
                else:
                    sentence = summarize_action(kind, "success", payload)
                lines.append(f"- **{event.created_at.strftime('%H:%M')}** — {sentence}")
            lines.append("")

        if totals:
            lines.append("## Totals over this window")
            lines.append("")
            for kind, count in sorted(totals.items(), key=lambda item: -item[1]):
                lines.append(f"- {humanize_key(kind)}: {count} time{'s' if count != 1 else ''}")
            lines.append("")
        return "\n".join(lines)

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
        usage: UsageTotals | None = None,
    ) -> None:
        with self.session_factory() as session:
            cycle = session.get(AgentCycle, cycle_id)
            cycle.status = status
            cycle.phase = "finished"
            cycle.stats_json = json.dumps(stats, default=str)
            cycle.error = error
            cycle.finished_at = self.now()
            cycle.checkpoint_json = json.dumps({"last_event_id": self._latest_event_id(session)})
            rollup = usage if usage is not None else self._cycle_usage(cycle_id)
            cycle.cost_usd = round(rollup.cost_usd, 6)
            cycle.llm_calls = rollup.llm_calls
            cycle.input_tokens = rollup.input_tokens
            cycle.output_tokens = rollup.output_tokens
            cycle.cached_tokens = rollup.cache_read_tokens
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
