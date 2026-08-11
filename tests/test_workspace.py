from pathlib import Path

import yaml

from recruiting_agent.workspace import CandidateWorkspace, OnboardingStage


def complete_interview(ws: CandidateWorkspace) -> None:
    state = ws.load_state()
    while state["stage"] == OnboardingStage.interview.value:
        question = ws.next_fallback_question(state)
        assert question
        state = ws.record_answer(question, f"Answer {state['question_index'] + 1}")


def test_resume_upload_is_stored_without_parsing(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    resume = ws.store_resume("candidate.pdf", b"%PDF opaque resume bytes")

    assert resume.read_bytes() == b"%PDF opaque resume bytes"
    assert ws.load_state()["stage"] == OnboardingStage.interview.value


def test_onboarding_resumes_and_compiles_versioned_harness(tmp_path: Path):
    ws = CandidateWorkspace(tmp_path)
    ws.store_resume("candidate.pdf", b"%PDF opaque resume bytes")
    question = ws.next_fallback_question()
    ws.record_answer(question, "I want product-focused platform work.")

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
