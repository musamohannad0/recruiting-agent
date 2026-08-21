from pathlib import Path

import pytest
import yaml

from recruiting_agent.workspace import FOLLOW_UP_QUESTIONS, CandidateWorkspace, OnboardingStage


def complete_interview(ws: CandidateWorkspace) -> None:
    state = ws.load_state()
    if state["stage"] == OnboardingStage.brief.value:
        state = ws.save_search_brief(
            {
                "role_thesis": "Product-focused platform engineering",
                "locations": ["New York City"],
                "company_scope": "exploratory",
            }
        )
    while state["stage"] == OnboardingStage.interview.value:
        question = ws.next_fallback_question(state)
        if not question:
            state = ws.finish_interview(state)
            break
        topic = next(key for key, value in FOLLOW_UP_QUESTIONS.items() if value == question)
        state = ws.record_answer(question, f"Answer {state['question_index'] + 1}", topic)


def test_resume_upload_is_stored_without_parsing(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    resume = ws.store_resume("candidate.pdf", b"%PDF opaque resume bytes")

    assert resume.read_bytes() == b"%PDF opaque resume bytes"
    assert ws.load_state()["stage"] == OnboardingStage.brief.value


def test_resume_upload_rejects_formats_the_agent_cannot_read(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)

    with pytest.raises(ValueError, match="PDF, TXT, or Markdown"):
        ws.store_resume("candidate.docx", b"opaque archive")


def test_onboarding_resumes_and_compiles_versioned_harness(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("candidate.pdf", b"%PDF opaque resume bytes")
    ws.save_search_brief(
        {
            "role_thesis": "I want product-focused platform work.",
            "locations": ["New York City"],
        }
    )
    question = ws.next_fallback_question()
    ws.record_answer(question, "My launch work is the strongest evidence.", "strengths")

    resumed = CandidateWorkspace(tmp_path)
    assert resumed.load_state()["question_index"] == 1
    complete_interview(resumed)
    resumed.save_company_calibration(
        scope="strict",
        excited=["Google", "Microsoft", "Google"],
        acceptable=["Apple"],
        excluded=["Early startup"],
    )
    resumed.save_role_calibration(
        target_roles=["Software Engineer", "ML Engineer"],
        excited_examples="Platform engineering with meaningful ownership.",
        pass_examples="Sales engineering and IT support.",
    )
    harness_hash = resumed.activate()

    assert resumed.is_ready
    assert resumed.harness_hash == harness_hash
    assert "Company scope: **strict**" in resumed.read_section("policy/search-constitution.md")
    companies = yaml.safe_load(resumed.read_section("policy/companies.yaml"))
    assert companies["excited"] == ["Google", "Microsoft"]
    assert (tmp_path / ".claude/skills/candidate-search-policy/SKILL.md").exists()


def test_default_company_universe_starts_with_40_included_companies(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("resume.pdf", b"resume")
    ws.save_search_brief(
        {
            "role_thesis": "Applied AI engineer with a customer feedback loop",
            "locations": ["New York City", "San Francisco"],
            "company_preferences": "Private LLM labs and applied AI companies",
        }
    )
    state = ws.finish_interview()

    assert len(state["company_candidates"]) == 40
    assert all(company["included"] for company in state["company_candidates"])
    priorities = [company for company in state["company_candidates"] if company["priority"]]
    assert len(priorities) == 8
    assert {company["category"] for company in priorities} >= {"Applied AI", "Foundation models"}


def test_fallback_interview_does_not_repeat_structured_brief_topics(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("resume.pdf", b"resume")
    state = ws.save_search_brief(
        {
            "role_thesis": "Applied AI deployment work",
            "locations": ["New York City"],
            "weekly_cadence": "8",
        }
    )

    questions = []
    while state["stage"] == OnboardingStage.interview.value:
        question = ws.next_fallback_question(state)
        if not question:
            state = ws.finish_interview(state)
            break
        questions.append(question)
        topic = next(key for key, value in FOLLOW_UP_QUESTIONS.items() if value == question)
        state = ws.record_answer(question, "Candidate answer", topic)

    assert len(questions) == 2
    assert all("location" not in question.casefold() for question in questions)
    assert all("weekly" not in question.casefold() for question in questions)


def test_legacy_questionnaire_migration_deduplicates_and_preserves_intent(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    ws.ensure()
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads/resume.pdf").write_bytes(b"resume")
    ws.state_path.write_text(
        '{"stage":"ready","resume_path":"uploads/resume.pdf","answers":['
        '{"question":"Target?","answer":"FDE and applied AI, not pure SWE"},'
        '{"question":"Location?","answer":"NYC/SF only"},'
        '{"question":"Location?","answer":"NYC/SF only"}]}'
    )
    ws.manifest_path.write_text("status: ready\nversion: 1\n")

    state = ws.load_state()

    assert state["stage"] == OnboardingStage.brief.value
    assert state["search_draft"]["locations"] == ["New York City", "San Francisco Bay Area"]
    assert len(state["answers"]) == 2
    assert (tmp_path / "backups/onboarding-v1/onboarding.json").exists()


def test_harness_hash_changes_when_approved_policy_changes(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("resume.txt", b"resume")
    complete_interview(ws)
    ws.save_company_calibration(scope="exploratory", excited=["A"], acceptable=[], excluded=[])
    ws.save_role_calibration(target_roles=["Engineer"], excited_examples="Build", pass_examples="Sell")
    original = ws.activate()

    policy = tmp_path / "policy/search-constitution.md"
    policy.write_text(policy.read_text() + "\nNew approved constraint.\n")

    assert ws.compute_hash() != original


def test_strict_big_tech_calibration_never_adds_unapproved_companies(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("resume.pdf", b"resume")
    complete_interview(ws)
    ws.save_company_calibration(
        scope="strict",
        excited=["Google", "Microsoft"],
        acceptable=[],
        excluded=["Frontier AI startups"],
    )
    ws.save_role_calibration(
        target_roles=["Software Engineer"],
        excited_examples="Infrastructure engineering",
        pass_examples="Business operations",
    )
    ws.activate()

    config = yaml.safe_load(ws.read_section("policy/companies.yaml"))
    assert config["scope"] == "strict"
    assert config["excited"] == ["Google", "Microsoft"]
    assert "Anthropic" not in config["excited"]
