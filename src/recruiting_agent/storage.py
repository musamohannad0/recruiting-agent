from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from sqlmodel import Session

from .db import engine
from .workspace import CandidateWorkspace


class WorkspaceStore(Protocol):
    def read(self, relative_path: str) -> str: ...

    def write_approved(self, relative_path: str, content: str) -> str: ...


class StateStore(Protocol):
    def session(self) -> Iterator[Session]: ...


class Scheduler(Protocol):
    def start(self) -> None: ...

    def shutdown(self, wait: bool = False) -> None: ...


class LocalWorkspaceStore:
    def __init__(self, root: Path):
        self.workspace = CandidateWorkspace(root)

    def read(self, relative_path: str) -> str:
        return self.workspace.read_section(relative_path)

    def write_approved(self, relative_path: str, content: str) -> str:
        return self.workspace.apply_approved_revision(relative_path, content)


class SQLStateStore:
    def __init__(self, database_engine=engine):
        self.engine = database_engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            yield session


class ModalVolumeWorkspaceStore(LocalWorkspaceStore):
    """Filesystem adapter for a mounted Modal Volume with explicit writer ownership."""

    def __init__(self, root: Path, *, writer: bool = False):
        super().__init__(root)
        self.writer = writer

    def write_approved(self, relative_path: str, content: str) -> str:
        if not self.writer:
            raise PermissionError("This Modal container has a read-only candidate workspace role")
        return super().write_approved(relative_path, content)
