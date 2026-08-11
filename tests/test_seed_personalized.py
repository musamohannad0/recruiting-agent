from pathlib import Path

from recruiting_agent import seed
from recruiting_agent.workspace import CandidateWorkspace


def test_candidate_company_selection_is_enriched_from_presets(tmp_path: Path, monkeypatch):
    ws = CandidateWorkspace(tmp_path)
    ws._write("manifest.yaml", "status: ready\nharness_hash: test\n")
    ws._write(
        "policy/companies.yaml",
        "scope: strict\nexcited:\n  - Google\nacceptable: []\nexcluded:\n  - Anthropic\n",
    )
    monkeypatch.setattr(seed, "workspace", ws)

    entries = seed.load_company_config()

    assert [entry["name"] for entry in entries] == ["Google"]
    assert entries[0]["careers_url"].startswith("https://www.google.com/")
