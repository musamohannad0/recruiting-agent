from __future__ import annotations

import json
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from sqlmodel import Session

from ..models import Run, RunKind, RunStatus, utcnow


@dataclass
class RunHandle:
    run: Run
    stats: dict = field(default_factory=dict)

    @property
    def id(self) -> int:
        return self.run.id


@contextmanager
def track_run(session: Session, kind: RunKind) -> Iterator[RunHandle]:
    """Record a pipeline run row; caller fills handle.stats before exit."""
    run = Run(kind=kind)
    session.add(run)
    session.commit()
    session.refresh(run)
    handle = RunHandle(run=run)
    try:
        yield handle
        run.status = RunStatus.success
    except Exception as exc:
        run.status = RunStatus.failed
        run.error = f"{exc}\n{traceback.format_exc(limit=5)}"
        raise
    finally:
        run.stats_json = json.dumps(handle.stats)
        run.finished_at = utcnow()
        session.add(run)
        session.commit()
