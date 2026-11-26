# Copyright 2026 Celina contributors.
"""L7 path inventory and protected directory enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from celina_pkg.selfimprove.l7 import _sensitive_paths
from celina_pkg.selfimprove.l7.policy import PolicyViolationError, validate_patch_proposal
from celina_pkg.selfimprove.l7.types import PatchPolicyConfig, PatchProposal

ROOT = Path(__file__).resolve().parents[2]
PATH_MANIFEST = ROOT / "selfimprove/l7_sensitive_paths.json"


def _patch(path: str) -> PatchProposal:
    patch = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"
    return PatchProposal("track0", patch, "test protected path")


def test_path_inventory_covers_security_owners_and_existing_core() -> None:
    """Every protected entry exists; security-owned entries are included."""
    data = json.loads(PATH_MANIFEST.read_text(encoding="utf-8"))
    files, directories = set(data["files"]), set(data["directories"])
    assert all((ROOT / path).is_file() for path in files)
    assert all((ROOT / path).is_dir() for path in directories)
    assert "selfimprove/l7_sensitive_paths.json" in files
    hashes = json.loads((ROOT / "selfimprove/protected_hashes.json").read_text())
    assert set(hashes) <= files
    for line in (ROOT / "CODEOWNERS").read_text().splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#") or "@security-core" not in parts[1:]:
            continue
        path = parts[0].lstrip("/").rstrip("/")
        assert path in (directories if (ROOT / path).is_dir() else files), path


@pytest.mark.parametrize("path", [
    "CODEOWNERS", "selfimprove/l7_sensitive_paths.json", ".github/workflows/ci.yml",
    ".github/workflows/new-workflow.yml", ".github/CODEOWNERS",
    "features/celina/celina_pkg/selfimprove/protected_hashes.json",
])
def test_protected_files_directories_and_reserved_paths_fail_closed(
    tmp_path: Path, path: str,
) -> None:
    """Existing and new targets cannot bypass the inventory or reserved names."""
    manifest = tmp_path / "hashes.json"
    manifest.write_text(json.dumps({"custom/locked.py": "a" * 64}))
    with pytest.raises(PolicyViolationError, match="protected"):
        validate_patch_proposal(_patch(path), manifest, PatchPolicyConfig())


@pytest.mark.parametrize("target", [
    ".github/workflows/new.yml", ".github/CODEOWNERS",
    "features/celina/celina_pkg/selfimprove/protected_hashes.json",
    "selfimprove/l7_sensitive_paths.json",
])
def test_rename_onto_protected_or_reserved_path_is_rejected(tmp_path: Path, target: str) -> None:
    """Rename metadata cannot move a safe file onto protected paths."""
    manifest = tmp_path / "hashes.json"
    manifest.write_text(json.dumps({"custom/locked.py": "a" * 64}))
    patch = (
        f"diff --git a/safe.py b/{target}\nsimilarity index 100%\n"
        f"rename from safe.py\nrename to {target}\n"
    )
    with pytest.raises(PolicyViolationError, match="protected"):
        validate_patch_proposal(
            PatchProposal("rename", patch, "test"), manifest, PatchPolicyConfig(),
        )


def test_directory_prefix_does_not_overblock_siblings(tmp_path: Path) -> None:
    """A textual prefix outside the protected directory is not the directory."""
    manifest = tmp_path / "hashes.json"
    manifest.write_text(json.dumps({"custom/locked.py": "a" * 64}))
    assert validate_patch_proposal(
        _patch(".github/workflows-extra/file.yml"), manifest, PatchPolicyConfig(),
    ).allowed


@pytest.mark.parametrize("content", [
    "{", "[]", "{}", '{"files": [], "directories": []}',
    '{"files": [1], "directories": ["dir"]}',
    '{"files": ["file", "file"], "directories": ["dir"]}',
    '{"files": ["missing"], "directories": ["dir"]}',
])
def test_invalid_sensitive_inventory_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str,
) -> None:
    """Missing, malformed, duplicated and nonexistent entries cannot disable policy."""
    manifest = tmp_path / "inventory.json"
    manifest.write_text(content)
    monkeypatch.setattr(_sensitive_paths, "_INVENTORY", manifest)
    monkeypatch.setattr(_sensitive_paths, "_ROOT", tmp_path)
    with pytest.raises(PolicyViolationError):
        _sensitive_paths.load_sensitive_paths()


def test_inventory_cannot_omit_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A manifest that excludes its own path cannot authorize patches."""
    (tmp_path / "file").write_text("x")
    (tmp_path / "dir").mkdir()
    manifest = tmp_path / "inventory.json"
    manifest.write_text(json.dumps({"files": ["file"], "directories": ["dir"]}))
    monkeypatch.setattr(_sensitive_paths, "_INVENTORY", manifest)
    monkeypatch.setattr(_sensitive_paths, "_ROOT", tmp_path)
    with pytest.raises(PolicyViolationError, match="membership"):
        _sensitive_paths.load_sensitive_paths()


def test_symlink_inventory_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An inventory symlink is rejected before it is read."""
    path = tmp_path / "actual.json"
    path.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(path)
    monkeypatch.setattr(_sensitive_paths, "_INVENTORY", link)
    with pytest.raises(PolicyViolationError, match="symlink"):
        _sensitive_paths.load_sensitive_paths()


def test_symlink_sensitive_entry_fails_closed(tmp_path: Path) -> None:
    """Existing entries cannot follow symlinks."""
    target = tmp_path / "real"
    target.write_text("x")
    (tmp_path / "link").symlink_to(target)
    with pytest.raises(PolicyViolationError, match="symlink"):
        _sensitive_paths._entries(tmp_path, ["link"], directories=False)  # noqa: SLF001
