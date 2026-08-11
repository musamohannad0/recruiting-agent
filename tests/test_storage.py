from pathlib import Path

import pytest

from recruiting_agent.storage import LocalWorkspaceStore, ModalVolumeWorkspaceStore


def ready_manifest(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.yaml").write_text("status: ready\nversion: 1\nharness_hash: old\n")


def test_local_workspace_store_versions_approved_write(tmp_path):
    ready_manifest(tmp_path)
    store = LocalWorkspaceStore(tmp_path)

    new_hash = store.write_approved("memory/feedback-summary.md", "# Learned\n")

    assert store.read("memory/feedback-summary.md") == "# Learned\n"
    assert new_hash != "old"


def test_modal_volume_store_enforces_writer_role(tmp_path):
    ready_manifest(tmp_path)
    reader = ModalVolumeWorkspaceStore(tmp_path, writer=False)

    with pytest.raises(PermissionError):
        reader.write_approved("memory/feedback-summary.md", "no")

    writer = ModalVolumeWorkspaceStore(tmp_path, writer=True)
    assert writer.write_approved("memory/feedback-summary.md", "approved")
