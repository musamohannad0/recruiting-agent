from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..settings import settings
from ..workspace import workspace


@dataclass
class Profile:
    resume_md: str
    profile_yaml: str
    resume_path: Path | None = None
    version_hash: str | None = None

    @property
    def hash(self) -> str:
        if self.version_hash:
            return self.version_hash
        return hashlib.sha256((self.resume_md + self.profile_yaml).encode()).hexdigest()[:16]


def load_profile() -> Profile:
    if workspace.is_ready:
        resume_path = workspace.resume_path()
        context = workspace.load_context(
            [
                "context/career-story.md",
                "policy/search-constitution.md",
                "policy/decision-rubric.md",
                "memory/calibration-anchors.md",
                "memory/feedback-summary.md",
            ]
        )
        return Profile(
            resume_md=f"Uploaded resume is available at {resume_path}",
            profile_yaml=context,
            resume_path=resume_path,
            version_hash=workspace.harness_hash,
        )
    resume = (settings.profile_dir / "resume.md").read_text()
    profile = (settings.profile_dir / "profile.yaml").read_text()
    return Profile(resume_md=resume, profile_yaml=profile)
