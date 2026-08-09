from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..settings import settings


@dataclass
class Profile:
    resume_md: str
    profile_yaml: str

    @property
    def hash(self) -> str:
        return hashlib.sha256((self.resume_md + self.profile_yaml).encode()).hexdigest()[:16]


def load_profile() -> Profile:
    resume = (settings.profile_dir / "resume.md").read_text()
    profile = (settings.profile_dir / "profile.yaml").read_text()
    return Profile(resume_md=resume, profile_yaml=profile)
