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
    brief = "brief"
    interview = "interview"
    company_calibration = "company_calibration"
    role_calibration = "role_calibration"
    review = "review"
    ready = "ready"


ONBOARDING_SCHEMA_VERSION = 2
MAX_FOLLOW_UP_QUESTIONS = 3

FOLLOW_UP_QUESTIONS = {
    "strengths": "Which one or two accomplishments should the agent treat as your strongest evidence when it argues that you are a fit?",
    "tradeoffs": "When a role is unusually exciting but not a clean fit on paper, how aggressively should the agent surface it?",
    "cadence": "How many genuinely strong opportunities do you want in a normal weekly review?",
}

# A broad starting universe, not a claim that every company is right for every candidate.
# The candidate can remove any item before the search is activated.
DEFAULT_COMPANY_CATALOG = [
    ("Anthropic", "Foundation models", "Private", "Frontier model research with deeply technical customer-facing roles."),
    ("OpenAI", "Foundation models", "Private", "Frontier AI products spanning research, deployment, and platform work."),
    ("Thinking Machines", "Foundation models", "Private", "Early frontier lab with unusually broad technical ownership."),
    ("DeepMind", "Foundation models", "Public", "Research-led AI organization with large-scale applied programs."),
    ("World Labs", "Foundation models", "Private", "Spatial intelligence lab building a new model category."),
    ("Periodic Labs", "Foundation models", "Private", "AI-for-science lab pairing models with real-world experimentation."),
    ("Lila Sciences", "Foundation models", "Private", "Scientific superintelligence company with applied deployment problems."),
    ("Physical Intelligence", "Foundation models", "Private", "General-purpose robotics models grounded in physical systems."),
    ("Sierra", "Applied AI", "Private", "Agent platform where product, engineering, and customer outcomes are tightly linked."),
    ("Abridge", "Applied AI", "Private", "Clinical AI product with high-stakes workflows and rapid customer feedback."),
    ("OpenEvidence", "Applied AI", "Private", "Fast-growing medical AI product with visible end-user impact."),
    ("Harvey", "Applied AI", "Private", "Vertical AI platform for demanding professional workflows."),
    ("Decagon", "Applied AI", "Private", "Customer-facing AI agents with a strong deployment loop."),
    ("Glean", "Applied AI", "Private", "Enterprise search and agents deployed across complex organizations."),
    ("Hebbia", "Applied AI", "Private", "AI knowledge work product with technical enterprise deployments."),
    ("Cursor", "Applied AI", "Private", "AI-native developer product with fast iteration and strong user pull."),
    ("Cognition", "Applied AI", "Private", "Agentic software engineering products at an early, ambitious company."),
    ("Runway", "Applied AI", "Private", "Generative media models translated into creative production tools."),
    ("Suno", "Applied AI", "Private", "Consumer generative audio product with unusually rapid product feedback."),
    ("ElevenLabs", "Applied AI", "Private", "Voice AI platform spanning product, infrastructure, and enterprise use."),
    ("Clay", "Applied AI", "Private", "Flexible GTM product with a strong builder and customer-iteration culture."),
    ("Scale AI", "Applied AI", "Private", "AI infrastructure and deployments across enterprise and government."),
    ("NVIDIA", "AI infrastructure", "Public", "Core compute platform with broad applied AI and ecosystem roles."),
    ("Databricks", "AI infrastructure", "Private", "Data and AI platform serving deeply technical customers."),
    ("Snowflake", "AI infrastructure", "Public", "Large data platform adding substantial AI product surface."),
    ("Cloudflare", "AI infrastructure", "Public", "Developer infrastructure at global scale with a pragmatic product culture."),
    ("Vercel", "AI infrastructure", "Private", "Developer platform with AI-native products and a strong user feedback loop."),
    ("Baseten", "AI infrastructure", "Private", "Model inference infrastructure built alongside AI application teams."),
    ("Modal", "AI infrastructure", "Private", "Developer-first compute platform for fast-moving AI teams."),
    ("Together AI", "AI infrastructure", "Private", "Model training and inference platform close to frontier workloads."),
    ("Fireworks AI", "AI infrastructure", "Private", "Production inference platform with hands-on customer engineering."),
    ("CoreWeave", "AI infrastructure", "Public", "Large-scale AI cloud with demanding infrastructure and customer work."),
    ("Palantir", "Defense & industrial", "Public", "Deployment-led software work in complex operational environments."),
    ("Anduril", "Defense & industrial", "Private", "Autonomous systems company combining software, hardware, and field use."),
    ("Shield AI", "Defense & industrial", "Private", "Autonomy software deployed into high-consequence environments."),
    ("Saronic", "Defense & industrial", "Private", "Autonomous maritime systems at a rapidly scaling company."),
    ("Hadrian", "Defense & industrial", "Private", "Software-defined manufacturing with direct operational feedback."),
    ("Waymo", "Robotics & autonomy", "Private", "Mature autonomous driving platform operating in the real world."),
    ("Wayve", "Robotics & autonomy", "Private", "Embodied AI company taking an end-to-end approach to autonomy."),
    ("Neuralink", "Robotics & autonomy", "Private", "High-ambition neurotechnology spanning software, hardware, and operations."),
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
            return self._default_state()
        state = json.loads(self.state_path.read_text())
        if int(state.get("schema_version", 1)) < ONBOARDING_SCHEMA_VERSION:
            state = self._migrate_legacy_state(state)
            self.save_state(state)
        if (
            state.get("stage") == OnboardingStage.company_calibration.value
            and int(state.get("company_catalog_version", 1)) < 3
        ):
            state["company_candidates"] = self._build_company_candidates(
                state.get("search_draft", {})
            )
            state["company_catalog_version"] = 3
            self.save_state(state)
        return state

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
                "schema_version": ONBOARDING_SCHEMA_VERSION,
                "stage": OnboardingStage.resume.value,
                "answers": [],
                "question_index": 0,
                "current_question": None,
                "asked_topics": [],
                "covered_topics": [],
                "generation": {"status": "idle", "message": None},
            }

    def _migrate_legacy_state(self, legacy: dict[str, Any]) -> dict[str, Any]:
        """Preserve useful answers while retiring the old repetitive questionnaire."""
        backup_root = self.root / "backups" / "onboarding-v1"
        if not backup_root.exists():
            backup_root.mkdir(parents=True, exist_ok=True)
            for relative in (
                "onboarding.json",
                "manifest.yaml",
                "context/career-story.md",
                "policy/search-constitution.md",
                "policy/decision-rubric.md",
                "policy/company-thesis.md",
                "policy/companies.yaml",
            ):
                source = self.root / relative
                if source.exists():
                    target = backup_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)

        unique_answers: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in legacy.get("answers", []):
            question = str(item.get("question", "")).strip()
            answer = str(item.get("answer", "")).strip()
            key = question.casefold()
            if question and answer and key not in seen:
                seen.add(key)
                unique_answers.append({"question": question, "answer": answer})

        answer_text = "\n".join(item["answer"] for item in unique_answers)
        first = unique_answers[0]["answer"] if unique_answers else ""
        second = unique_answers[1]["answer"] if len(unique_answers) > 1 else ""
        lowered = answer_text.casefold()
        locations = []
        if "nyc" in lowered or "new york" in lowered:
            locations.append("New York City")
        if "sf" in lowered or "san francisco" in lowered or "bay area" in lowered:
            locations.append("San Francisco Bay Area")
        stretch = "reach" if any(word in lowered for word in ("reach", "stretch", "prove myself")) else "balanced"
        stage_preference = second or "Open to a range of company stages."

        state = self._default_state()
        state.update(
            {
                "resume_path": legacy.get("resume_path"),
                "stage": OnboardingStage.brief.value if legacy.get("resume_path") else OnboardingStage.resume.value,
                "answers": unique_answers,
                "legacy_answers_preserved": True,
                "search_draft": {
                    "role_thesis": first,
                    "work_style": "Prioritize work with direct customer feedback and rapid iteration." if "feedback" in lowered else "",
                    "locations": locations,
                    "remote_policy": "onsite_or_hybrid",
                    "company_stages": [stage_preference],
                    "company_preferences": second,
                    "verticals": ["Broadly open"] if "anything" in lowered or "everything" in lowered else [],
                    "excluded_verticals": [],
                    "stretch": stretch,
                    "hard_exclusions": "",
                    "weekly_cadence": "10",
                    "company_scope": legacy.get("company_calibration", {}).get("scope", "exploratory"),
                },
                "migration_notice": "We kept your resume and useful answers, removed repeated questions, and moved them into the new search brief for review.",
            }
        )
        if self.manifest_path.exists():
            manifest = yaml.safe_load(self.manifest_path.read_text()) or {}
            manifest["status"] = "onboarding"
            manifest["migrated_from_version"] = manifest.get("version", 1)
            self._atomic_write(self.manifest_path, yaml.safe_dump(manifest, sort_keys=False))
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        self.ensure()
        self._atomic_write(self.state_path, json.dumps(state, indent=2) + "\n")

    @property
    def is_ready(self) -> bool:
        # Loading first performs the one-time v1 questionnaire migration.
        had_state = self.state_path.exists()
        state = self.load_state()
        if had_state and state.get("stage") != OnboardingStage.ready.value:
            return False
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
        if suffix not in {".pdf", ".txt", ".md"}:
            raise ValueError("Resume must be PDF, TXT, or Markdown")
        uploads = self.root / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / f"resume{suffix}"
        for existing in uploads.glob("resume.*"):
            if existing != target:
                existing.unlink()
        target.write_bytes(content)
        self._install_generic_skills()
        state = self.load_state()
        state["resume_path"] = target.relative_to(self.root).as_posix()
        state["stage"] = OnboardingStage.brief.value
        state["current_question"] = None
        state["generation"] = {"status": "idle", "message": None}
        self.save_state(state)
        return target

    def resume_path(self) -> Path | None:
        rel = self.load_state().get("resume_path")
        path = self.root / rel if rel else None
        return path if path and path.exists() else None

    def next_fallback_question(self, state: dict[str, Any] | None = None) -> str | None:
        state = state or self.load_state()
        asked = set(state.get("asked_topics", [])) | set(state.get("covered_topics", []))
        for topic, question in FOLLOW_UP_QUESTIONS.items():
            if topic not in asked:
                return question
        return None

    def save_search_brief(self, draft: dict[str, Any]) -> dict[str, Any]:
        role_thesis = str(draft.get("role_thesis", "")).strip()
        if not role_thesis:
            raise ValueError("Describe the work you want the agent to target")
        locations = self._clean_list(list(draft.get("locations", [])))
        if not locations:
            raise ValueError("Add at least one acceptable location")
        state = self.load_state()
        state["search_draft"] = {
            "role_thesis": role_thesis,
            "work_style": str(draft.get("work_style", "")).strip(),
            "locations": locations,
            "remote_policy": str(draft.get("remote_policy", "onsite_or_hybrid")),
            "company_stages": self._clean_list(list(draft.get("company_stages", []))),
            "company_preferences": str(draft.get("company_preferences", "")).strip(),
            "verticals": self._clean_list(list(draft.get("verticals", []))),
            "excluded_verticals": self._clean_list(list(draft.get("excluded_verticals", []))),
            "stretch": str(draft.get("stretch", "balanced")),
            "hard_exclusions": str(draft.get("hard_exclusions", "")).strip(),
            "weekly_cadence": str(draft.get("weekly_cadence", "10")).strip() or "10",
            "company_scope": str(draft.get("company_scope", "exploratory")),
        }
        state["stage"] = OnboardingStage.interview.value
        state["question_index"] = 0
        state["current_question"] = None
        state["asked_topics"] = []
        state["covered_topics"] = [
            "target_work",
            "location",
            "companies",
            "exclusions",
            "stretch",
            "cadence",
        ]
        state["generation"] = {"status": "pending", "message": "Finding the one or two details that would change your search…"}
        state.pop("migration_notice", None)
        self.save_state(state)
        return state

    def record_answer(self, question: str, answer: str, topic: str | None = None) -> dict[str, Any]:
        if not answer.strip():
            raise ValueError("Answer cannot be empty")
        state = self.load_state()
        current = state.get("current_question") or {}
        if isinstance(current, dict):
            topic = topic or current.get("topic")
            question = current.get("question") or question
        normalized = question.strip().casefold()
        if not any(str(item.get("question", "")).strip().casefold() == normalized for item in state.get("answers", [])):
            state.setdefault("answers", []).append(
                {"question": question.strip(), "answer": answer.strip(), "topic": topic or "clarification"}
            )
        state["question_index"] = int(state.get("question_index", 0)) + 1
        state["current_question"] = None
        if topic:
            state.setdefault("asked_topics", []).append(topic)
        if state["question_index"] >= MAX_FOLLOW_UP_QUESTIONS:
            return self.finish_interview(state)
        state["generation"] = {"status": "pending", "message": "Checking whether one more clarification would materially change the search…"}
        self.save_state(state)
        return state

    def finish_interview(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = state or self.load_state()
        if not state.get("company_candidates"):
            state["company_candidates"] = self._build_company_candidates(state.get("search_draft", {}))
        state["company_catalog_version"] = 3
        state["stage"] = OnboardingStage.company_calibration.value
        state["current_question"] = None
        state["generation"] = {"status": "complete", "message": "Your initial company universe is ready."}
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
        state["company_candidates"] = [
            {
                "name": name,
                "category": "Candidate supplied",
                "stage": "Unknown",
                "rationale": "Included by the candidate.",
                "included": True,
                "priority": name in self._clean_list(excited),
            }
            for name in self._clean_list(excited + acceptable)
        ]
        state["role_cards"] = self._build_role_cards(state.get("search_draft", {}))
        state["stage"] = OnboardingStage.role_calibration.value
        self.save_state(state)

    def save_company_selection(
        self,
        *,
        scope: str,
        included: list[str],
        priority: list[str],
        additions: list[str] | None = None,
    ) -> None:
        if scope not in {"strict", "exploratory"}:
            raise ValueError("Company scope must be strict or exploratory")
        state = self.load_state()
        candidates = list(state.get("company_candidates", []))
        by_name = {str(item["name"]).casefold(): item for item in candidates}
        for name in self._clean_list(additions or []):
            if name.casefold() not in by_name:
                item = {
                    "name": name,
                    "category": "Added by you",
                    "stage": "Unknown",
                    "rationale": "Manually added to the starting universe.",
                    "included": True,
                    "priority": False,
                }
                candidates.append(item)
                by_name[name.casefold()] = item

        included_names = self._clean_list(included + (additions or []))
        priority_names = self._clean_list(priority)
        included_keys = {name.casefold() for name in included_names}
        priority_keys = {name.casefold() for name in priority_names}
        if not included_keys:
            raise ValueError("Keep at least one company in the starting universe")
        for item in candidates:
            key = str(item["name"]).casefold()
            item["included"] = key in included_keys
            item["priority"] = key in priority_keys and item["included"]

        ordered_included = [item["name"] for item in candidates if item["included"]]
        excited = [item["name"] for item in candidates if item["priority"]]
        acceptable = [name for name in ordered_included if name not in excited]
        excluded = [item["name"] for item in candidates if not item["included"]]
        state["company_candidates"] = candidates
        state["company_calibration"] = {
            "scope": scope,
            "excited": excited,
            "acceptable": acceptable,
            "excluded": excluded,
        }
        state["role_cards"] = self._build_role_cards(state.get("search_draft", {}))
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

    def save_role_ratings(self, ratings: dict[str, str], notes: dict[str, str] | None = None) -> None:
        allowed = {"excited", "consider", "pass"}
        state = self.load_state()
        cards = state.get("role_cards", [])
        if not cards:
            raise ValueError("Role calibration cards are missing")
        clean: dict[str, str] = {}
        for card in cards:
            card_id = str(card["id"])
            label = ratings.get(card_id, str(card.get("prediction", "consider")))
            if label not in allowed:
                raise ValueError("Role ratings must be excited, consider, or pass")
            clean[card_id] = label
        state["role_calibration"] = {
            "ratings": clean,
            "notes": {key: value.strip() for key, value in (notes or {}).items() if value.strip()},
        }
        state["stage"] = OnboardingStage.review.value
        self.save_state(state)

    @staticmethod
    def _build_company_candidates(draft: dict[str, Any]) -> list[dict[str, Any]]:
        preferences = " ".join(
            [
                str(draft.get("role_thesis", "")),
                str(draft.get("company_preferences", "")),
                " ".join(draft.get("verticals", [])),
            ]
        ).casefold()
        category_signals = {
            "Foundation models": ("lab", "llm", "model", "research"),
            "Applied AI": ("applied", "customer", "product", "agent", "fde"),
            "AI infrastructure": ("infra", "platform", "developer", "compute"),
            "Defense & industrial": ("defense", "industrial", "government"),
            "Robotics & autonomy": ("robot", "autonomy", "physical"),
        }

        def score(category: str, stage: str) -> int:
            value = sum(3 for signal in category_signals.get(category, ()) if signal in preferences)
            if ("private" in preferences or "non public" in preferences) and stage == "Private":
                value += 2
            if "public" in preferences and "non public" not in preferences and stage == "Public":
                value += 1
            return value

        rows = [
            {
                "name": name,
                "category": category,
                "stage": stage,
                "rationale": rationale,
                "included": True,
                "priority": False,
                "fit_score": score(category, stage),
            }
            for name, category, stage, rationale in DEFAULT_COMPANY_CATALOG
        ]
        rows.sort(key=lambda item: (-item["fit_score"], item["category"], item["name"]))
        categories: dict[str, list[dict[str, Any]]] = {}
        for item in rows:
            categories.setdefault(item["category"], []).append(item)
        ranked_categories = sorted(
            categories.values(),
            key=lambda group: (
                -max(item["fit_score"] for item in group),
                group[0]["category"],
            ),
        )
        for group, quota in zip(ranked_categories, (4, 3, 1)):
            for item in group[:quota]:
                item["priority"] = True
        return rows

    @staticmethod
    def _build_role_cards(draft: dict[str, Any]) -> list[dict[str, str]]:
        thesis = str(draft.get("role_thesis", "the target role thesis")).strip()
        locations = ", ".join(draft.get("locations", [])) or "an approved location"
        return [
            {
                "id": "core",
                "title": "Core target",
                "subtitle": "A role directly aligned with your stated thesis",
                "description": thesis,
                "context": f"Strong scope match · {locations}",
                "prediction": "excited",
            },
            {
                "id": "reach",
                "title": "Reach opportunity",
                "subtitle": "The right work, one level above the obvious fit",
                "description": "High ownership and unusually strong learning upside, with a meaningful experience gap to overcome.",
                "context": f"Stretch scope · {locations}",
                "prediction": "excited" if draft.get("stretch") == "reach" else "consider",
            },
            {
                "id": "technical_product",
                "title": "Technical product lead",
                "subtitle": "Product ownership while staying close to implementation",
                "description": "A smaller team where customer discovery, prototyping, and shipping remain part of the same role.",
                "context": "Small or scaling company · hands-on",
                "prediction": "consider",
            },
            {
                "id": "solutions",
                "title": "Solutions engineering",
                "subtitle": "Customer-facing, but more implementation than ownership",
                "description": "Deep technical work with customers, though the product roadmap and iteration loop sit elsewhere.",
                "context": "Customer-facing · moderate product ownership",
                "prediction": "consider",
            },
            {
                "id": "pure_engineering",
                "title": "Pure software engineering",
                "subtitle": "Strong technical fit without the customer loop",
                "description": "A respected engineering role focused on a mature internal platform with limited direct user contact.",
                "context": "Deep build work · indirect feedback",
                "prediction": "pass" if "pure swe" in thesis.casefold() or "customer" in thesis.casefold() else "consider",
            },
            {
                "id": "location_tradeoff",
                "title": "Location tradeoff",
                "subtitle": "Excellent work outside your approved geography",
                "description": "The role fits the thesis and level, but requires regular presence in a location you did not approve.",
                "context": "Strong role fit · location mismatch",
                "prediction": "pass",
            },
        ]

    def activate(self) -> str:
        state = self.load_state()
        if state.get("stage") != OnboardingStage.review.value:
            raise ValueError("Onboarding is not ready for activation")
        if not self.resume_path():
            raise ValueError("A resume must be uploaded before activation")

        answers = state.get("answers", [])
        draft = state.get("search_draft", {})
        companies = state.get("company_calibration", {})
        roles = state.get("role_calibration", {})

        self._write("context/career-story.md", self._career_story(draft, answers))
        self._write(
            "policy/search-constitution.md",
            self._search_constitution(draft, companies, roles, answers),
        )
        self._write("policy/decision-rubric.md", self._decision_rubric(roles, state.get("role_cards", [])))
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
        self._write("memory/calibration-anchors.md", self._calibration_anchors(companies, roles, state.get("role_cards", [])))
        self._write("memory/feedback-summary.md", "# Feedback Summary\n\nNo explicit corrections yet.\n")
        self._write("memory/current-search-state.md", "# Current Search State\n\nSearch activated. The initial source scan and matching cycle are queued.\n")
        self._write("memory/search-journal.md", "# Search Journal\n")
        self._write("CLAUDE.md", self._candidate_instructions())
        self._write_candidate_skill()
        self._install_generic_skills()

        harness_hash = self.compute_hash()
        manifest = {
            "status": OnboardingStage.ready.value,
            "version": 2,
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

    def apply_approved_revision(self, section: str, content: str) -> str:
        if not (section.startswith("memory/") or section.startswith("policy/")):
            raise ValueError("Approved revisions must target memory/ or policy/")
        if not section.endswith(".md"):
            raise ValueError("Approved revisions must target Markdown files")
        self._write(section, content)
        manifest = yaml.safe_load(self.manifest_path.read_text()) or {}
        manifest["version"] = int(manifest.get("version", 0)) + 1
        manifest["harness_hash"] = self.compute_hash()
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write("manifest.yaml", yaml.safe_dump(manifest, sort_keys=False))
        return manifest["harness_hash"]

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
        ignored = {"manifest.yaml", "onboarding.json", "search-journal.md", "current-search-state.md"}
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
    def _career_story(draft: dict, answers: list[dict[str, str]]) -> str:
        evidence = [item["answer"] for item in answers if item.get("topic") == "strengths"]
        return "\n".join(
            [
                "# Career Story",
                "",
                "## Direction",
                str(draft.get("role_thesis") or "Not yet specified."),
                "",
                "## Preferred working pattern",
                str(draft.get("work_style") or "No additional working-pattern preference supplied."),
                "",
                "## Strongest evidence",
                "\n".join(f"- {item}" for item in evidence) or "- Use the resume as the primary evidence source.",
                "",
                "The resume is evidence about capability. Explicit onboarding choices are the source of truth about intent.",
            ]
        )

    @staticmethod
    def _search_constitution(
        draft: dict,
        companies: dict,
        roles: dict,
        answers: list[dict[str, str]] | None = None,
    ) -> str:
        lines = [
            "# Search Constitution",
            "",
            "This is user-approved policy. Agent inferences may not silently override it.",
            "",
            "## Role thesis",
            str(draft.get("role_thesis") or "Not specified."),
            "",
            "## Working pattern",
            str(draft.get("work_style") or "No additional preference supplied."),
            "",
            "## Geography",
            f"Approved locations: {', '.join(draft.get('locations', [])) or 'not specified'}",
            f"Remote policy: {str(draft.get('remote_policy', 'not specified')).replace('_', ' ')}",
            "",
            "## Company thesis",
            f"Company scope: **{companies.get('scope', 'exploratory')}**",
            f"Stage preference: {', '.join(draft.get('company_stages', [])) or 'open'}",
            f"Preferred verticals: {', '.join(draft.get('verticals', [])) or 'broadly open'}",
            f"Excluded verticals: {', '.join(draft.get('excluded_verticals', [])) or 'none'}",
            str(draft.get("company_preferences") or "No additional company preference supplied."),
            "",
            "## Ranking policy",
            f"Stretch appetite: {draft.get('stretch', 'balanced')}",
            f"Weekly review target: {draft.get('weekly_cadence', '10')} strong opportunities",
            "",
            "## Hard exclusions",
            str(draft.get("hard_exclusions") or "None supplied."),
            "",
            "## Calibration",
            "Role-card labels in `memory/calibration-anchors.md` are approved examples. They refine this policy but cannot override hard constraints.",
        ]
        clarifications = [
            item for item in (answers or []) if item.get("topic") and item.get("answer")
        ]
        if clarifications:
            lines.extend(["", "## Approved clarifications"])
            for item in clarifications:
                topic = str(item["topic"]).replace("_", " ").title()
                lines.append(f"- **{topic}:** {item['answer']}")
        return "\n".join(lines)

    @staticmethod
    def _decision_rubric(roles: dict, cards: list[dict] | None = None) -> str:
        cards = cards or []
        ratings = roles.get("ratings", {})
        excited = [card["title"] for card in cards if ratings.get(card["id"]) == "excited"]
        passed = [card["title"] for card in cards if ratings.get(card["id"]) == "pass"]
        return "\n".join(
            [
                "# Decision Rubric",
                "",
                "Use the candidate's explicit evidence and calibration anchors; judge substance, not title keywords.",
                "",
                "## Excited anchors",
                ", ".join(excited) or roles.get("excited_examples") or "No examples supplied.",
                "",
                "## Pass anchors",
                ", ".join(passed) or roles.get("pass_examples") or "No examples supplied.",
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
    def _calibration_anchors(companies: dict, roles: dict, cards: list[dict] | None = None) -> str:
        lines = [
                "# Calibration Anchors",
                "",
                "## Companies",
                f"Excited: {', '.join(companies.get('excited', [])) or 'none'}",
                f"Acceptable: {', '.join(companies.get('acceptable', [])) or 'none'}",
                f"Exclude: {', '.join(companies.get('excluded', [])) or 'none'}",
                "",
                "## Roles that should excite",
        ]
        ratings = roles.get("ratings", {})
        if cards and ratings:
            for card in cards:
                lines.append(f"- **{ratings.get(card['id'], 'consider').title()} — {card['title']}:** {card['description']}")
        else:
            lines.extend(
                [
                    roles.get("excited_examples") or "No examples supplied.",
                    "",
                    "## Roles that should pass",
                    roles.get("pass_examples") or "No examples supplied.",
                ]
            )
        return "\n".join(lines)

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
