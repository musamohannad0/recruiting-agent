from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from .settings import PROJECT_ROOT, settings


class OnboardingStage(StrEnum):
    resume = "resume"
    interview = "interview"
    company_calibration = "company_calibration"
    role_calibration = "role_calibration"
    review = "review"
    ready = "ready"


DEFAULT_INTERVIEW_QUESTIONS = [
    "What kinds of work do you want to spend most of your time doing in your next role?",
    "Which accomplishments or strengths should the search agent treat as your strongest evidence?",
    "How much of a stretch in title, scope, or required experience are you comfortable with?",
    "Which locations and remote arrangements are acceptable, and which are hard exclusions?",
    "What makes a company exciting to you: scale, mission, technical depth, growth, brand, stage, or something else?",
    "Which role families, responsibilities, or industries should never be surfaced?",
    "When fit and excitement conflict, which should the agent prioritize?",
    "How broad should the search be, and about how many strong opportunities can you review each week?",
]


@dataclass(frozen=True)
class CandidateWorkspace:
    root: Path = settings.workspace_dir

    @property
    def state_path(self) -> Path:
        return self.root / "onboarding.json"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.yaml"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> dict[str, Any]:
        self.ensure()
        if not self.state_path.exists():
            return {
                "stage": OnboardingStage.resume.value,
                "answers": [],
                "question_index": 0,
                "current_question": None,
            }
        return json.loads(self.state_path.read_text())

    def save_state(self, state: dict[str, Any]) -> None:
        self.ensure()
        self._atomic_write(self.state_path, json.dumps(state, indent=2) + "\n")

    @property
    def is_ready(self) -> bool:
        if not self.manifest_path.exists():
            return False
        manifest = yaml.safe_load(self.manifest_path.read_text()) or {}
        return manifest.get("status") == OnboardingStage.ready.value

    @property
    def harness_hash(self) -> str:
        if self.manifest_path.exists():
            manifest = yaml.safe_load(self.manifest_path.read_text()) or {}
            if manifest.get("harness_hash"):
                return str(manifest["harness_hash"])
        return self.compute_hash()

    def store_resume(self, filename: str, content: bytes) -> Path:
        if not content:
            raise ValueError("Resume upload is empty")
        if len(content) > 10 * 1024 * 1024:
            raise ValueError("Resume must be 10 MB or smaller")
        suffix = Path(filename or "resume.pdf").suffix.lower()
        if suffix not in {".pdf", ".docx", ".txt", ".md"}:
            raise ValueError("Resume must be PDF, DOCX, TXT, or Markdown")
        uploads = self.root / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / f"resume{suffix}"
        for existing in uploads.glob("resume.*"):
            if existing != target:
                existing.unlink()
        target.write_bytes(content)
        state = self.load_state()
        state["resume_path"] = target.relative_to(self.root).as_posix()
        state["stage"] = OnboardingStage.interview.value
        state["current_question"] = None
        self.save_state(state)
        return target

    def resume_path(self) -> Path | None:
        rel = self.load_state().get("resume_path")
        path = self.root / rel if rel else None
        return path if path and path.exists() else None

    def next_fallback_question(self, state: dict[str, Any] | None = None) -> str | None:
        state = state or self.load_state()
        index = int(state.get("question_index", 0))
        return DEFAULT_INTERVIEW_QUESTIONS[index] if index < len(DEFAULT_INTERVIEW_QUESTIONS) else None

    def record_answer(self, question: str, answer: str) -> dict[str, Any]:
        if not answer.strip():
            raise ValueError("Answer cannot be empty")
        state = self.load_state()
        state.setdefault("answers", []).append({"question": question, "answer": answer.strip()})
        state["question_index"] = int(state.get("question_index", 0)) + 1
        state["current_question"] = None
        if state["question_index"] >= len(DEFAULT_INTERVIEW_QUESTIONS):
            state["stage"] = OnboardingStage.company_calibration.value
        self.save_state(state)
        return state

    def save_company_calibration(
        self,
        *,
        scope: str,
        excited: list[str],
        acceptable: list[str],
        excluded: list[str],
    ) -> None:
        if scope not in {"strict", "exploratory"}:
            raise ValueError("Company scope must be strict or exploratory")
        state = self.load_state()
        state["company_calibration"] = {
            "scope": scope,
            "excited": self._clean_list(excited),
            "acceptable": self._clean_list(acceptable),
            "excluded": self._clean_list(excluded),
        }
        state["stage"] = OnboardingStage.role_calibration.value
        self.save_state(state)

    def save_role_calibration(
        self, *, target_roles: list[str], excited_examples: str, pass_examples: str
    ) -> None:
        state = self.load_state()
        state["role_calibration"] = {
            "target_roles": self._clean_list(target_roles),
            "excited_examples": excited_examples.strip(),
            "pass_examples": pass_examples.strip(),
        }
        state["stage"] = OnboardingStage.review.value
        self.save_state(state)

    def activate(self) -> str:
        state = self.load_state()
        if state.get("stage") != OnboardingStage.review.value:
            raise ValueError("Onboarding is not ready for activation")
        if not self.resume_path():
            raise ValueError("A resume must be uploaded before activation")

        answers = state.get("answers", [])
        companies = state.get("company_calibration", {})
        roles = state.get("role_calibration", {})

        self._write("context/career-story.md", self._career_story(answers))
        self._write("policy/search-constitution.md", self._search_constitution(answers, companies, roles))
        self._write("policy/decision-rubric.md", self._decision_rubric(roles))
        self._write("policy/company-thesis.md", self._company_thesis(companies))
        self._write("policy/companies.yaml", yaml.safe_dump(companies, sort_keys=False))
        self._write(
            "policy/cadence.yaml",
            yaml.safe_dump(
                {
                    "ats_ingest_hours": 2,
                    "career_site_discovery_hours": 24,
                    "company_discovery_days": 7,
                    "memory_consolidation_days": 7,
                    "feedback_recalibration_threshold": 5,
                },
                sort_keys=False,
            ),
        )
        self._write(
            "policy/tools.yaml",
            yaml.safe_dump(
                {
                    "default": ["load_search_state", "query_job_history"],
                    "company_curator": ["suggest_company", "record_observation"],
                    "memory": ["propose_memory_patch"],
                },
                sort_keys=False,
            ),
        )
        self._write("memory/calibration-anchors.md", self._calibration_anchors(companies, roles))
        self._write("memory/feedback-summary.md", "# Feedback Summary\n\nNo explicit corrections yet.\n")
        self._write("memory/current-search-state.md", "# Current Search State\n\nSearch activated; initial scan pending.\n")
        self._write("memory/search-journal.md", "# Search Journal\n")
        self._write("CLAUDE.md", self._candidate_instructions())
        self._write_candidate_skill()
        self._install_generic_skills()

        harness_hash = self.compute_hash()
        manifest = {
            "status": OnboardingStage.ready.value,
            "version": 1,
            "harness_hash": harness_hash,
            "resume_path": state["resume_path"],
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write("manifest.yaml", yaml.safe_dump(manifest, sort_keys=False))
        state["stage"] = OnboardingStage.ready.value
        state["harness_hash"] = harness_hash
        self.save_state(state)
        return harness_hash

    def read_section(self, relative_path: str) -> str:
        path = (self.root / relative_path).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("Context path escapes candidate workspace")
        return path.read_text() if path.exists() else ""

    def load_context(self, sections: tuple[str, ...] | list[str]) -> str:
        chunks = []
        for section in sections:
            content = self.read_section(section)
            if content:
                chunks.append(f"## {section}\n\n{content}")
        return "\n\n".join(chunks)

    def compute_hash(self) -> str:
        digest = hashlib.sha256()
        if not self.root.exists():
            return digest.hexdigest()[:16]
        ignored = {"manifest.yaml", "onboarding.json"}
        for path in sorted(p for p in self.root.rglob("*") if p.is_file()):
            if path.name in ignored or "uploads" in path.parts:
                continue
            digest.update(path.relative_to(self.root).as_posix().encode())
            digest.update(path.read_bytes())
        resume = self.resume_path()
        if resume:
            digest.update(resume.read_bytes())
        return digest.hexdigest()[:16]

    def import_legacy(self) -> None:
        """Copy legacy tracked profile/config into a private, unfinished workspace."""
        self.ensure()
        legacy_resume = settings.profile_dir / "resume.md"
        if legacy_resume.exists() and not self.resume_path():
            self.store_resume(legacy_resume.name, legacy_resume.read_bytes())
        companies_file = settings.companies_yaml
        if companies_file.exists():
            data = yaml.safe_load(companies_file.read_text()) or {}
            names = [entry["name"] for entry in data.get("companies", [])]
            state = self.load_state()
            state["legacy_companies"] = names
            self.save_state(state)

    @staticmethod
    def _clean_list(values: list[str]) -> list[str]:
        seen: set[str] = set()
        out = []
        for value in values:
            value = value.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                out.append(value)
        return out

    def _write(self, relative_path: str, content: str) -> None:
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, content.rstrip() + "\n")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content)
        temporary.replace(path)

    @staticmethod
    def _career_story(answers: list[dict[str, str]]) -> str:
        lines = ["# Career Story", "", "Generated from the candidate's onboarding interview.", ""]
        for item in answers:
            lines.extend([f"## {item['question']}", "", item["answer"], ""])
        return "\n".join(lines)

    @staticmethod
    def _search_constitution(answers: list[dict[str, str]], companies: dict, roles: dict) -> str:
        lines = [
            "# Search Constitution",
            "",
            "This is user-approved policy. Agent inferences may not silently override it.",
            "",
            f"Company scope: **{companies.get('scope', 'exploratory')}**",
            "",
            "Target roles: " + ", ".join(roles.get("target_roles", [])),
            "",
            "## Interview Evidence",
            "",
        ]
        for item in answers:
            lines.append(f"- **{item['question']}** {item['answer']}")
        return "\n".join(lines)

    @staticmethod
    def _decision_rubric(roles: dict) -> str:
        return "\n".join(
            [
                "# Decision Rubric",
                "",
                "Use the candidate's explicit evidence and calibration anchors; judge substance, not title keywords.",
                "",
                "## Excited anchors",
                roles.get("excited_examples") or "No examples supplied.",
                "",
                "## Pass anchors",
                roles.get("pass_examples") or "No examples supplied.",
                "",
                "## Recommendation bands",
                "- 75–100: excited / apply",
                "- 60–74: consider / maybe",
                "- 0–59: pass / skip",
            ]
        )

    @staticmethod
    def _company_thesis(companies: dict) -> str:
        return "\n".join(
            [
                "# Company Thesis",
                "",
                f"Scope: **{companies.get('scope', 'exploratory')}**",
                "",
                "## Excited",
                *(f"- {name}" for name in companies.get("excited", [])),
                "",
                "## Acceptable",
                *(f"- {name}" for name in companies.get("acceptable", [])),
                "",
                "## Excluded",
                *(f"- {name}" for name in companies.get("excluded", [])),
            ]
        )

    @staticmethod
    def _calibration_anchors(companies: dict, roles: dict) -> str:
        return "\n".join(
            [
                "# Calibration Anchors",
                "",
                "## Companies",
                f"Excited: {', '.join(companies.get('excited', [])) or 'none'}",
                f"Acceptable: {', '.join(companies.get('acceptable', [])) or 'none'}",
                f"Exclude: {', '.join(companies.get('excluded', [])) or 'none'}",
                "",
                "## Roles that should excite",
                roles.get("excited_examples") or "No examples supplied.",
                "",
                "## Roles that should pass",
                roles.get("pass_examples") or "No examples supplied.",
            ]
        )

    @staticmethod
    def _candidate_instructions() -> str:
        return """# Candidate Search Harness

Read the relevant files under `context/`, `policy/`, and `memory/` before making a
candidate-specific judgment. Treat `policy/search-constitution.md` as confirmed
user intent. Never silently change hard constraints. Propose policy or memory
changes through the provided tools and include the evidence that motivated them.
"""

    def _write_candidate_skill(self) -> None:
        self._write(
            ".claude/skills/candidate-search-policy/SKILL.md",
            """---
name: candidate-search-policy
description: Apply this candidate's approved search constitution and calibration anchors.
---

1. Read `policy/search-constitution.md` and `policy/decision-rubric.md`.
2. Use `memory/calibration-anchors.md` as concrete judgment evidence.
3. Apply hard constraints before preferences.
4. If evidence conflicts, surface a clarification instead of inventing a rule.
""",
        )

    def _install_generic_skills(self) -> None:
        source = PROJECT_ROOT / "src" / "recruiting_agent" / "harness_skills"
        target = self.root / ".claude" / "skills"
        if not source.exists():
            return
        for skill_dir in source.iterdir():
            if skill_dir.is_dir():
                shutil.copytree(skill_dir, target / skill_dir.name, dirs_exist_ok=True)


workspace = CandidateWorkspace()
