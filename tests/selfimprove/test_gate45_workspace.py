# Copyright 2026 Celina contributors.
"""Approved writes are confined to an explicit trusted workspace when configured."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from celina_pkg import config, state_ops, tools

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a temporary Celina home and a side-effect-free undo journal."""
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.delenv("CELINA_WORKSPACE_DIR", raising=False)

    class Journal:
        def record_before_mutation(self, *_args: object) -> str:
            return "fixture"

        def finalize_mutation(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(state_ops, "get_undo_journal", Journal)
    return tmp_path


@pytest.mark.parametrize(
    "case", ["parent", "nested", "sibling", "parent_symlink", "target_symlink"],
)
def test_approved_write_rejects_workspace_escapes(
    home: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Traversal, prefix collisions and symlinks cannot escape an explicit workspace."""
    workspace = home / "workspace"
    workspace.mkdir()
    (workspace / "a").mkdir()
    sibling = home / "workspace-evil"
    sibling.mkdir()
    (workspace / "parent-link").symlink_to(sibling, target_is_directory=True)
    (workspace / "target-link").symlink_to(sibling / "outside.txt")
    targets = {
        "parent": workspace / ".." / "outside.txt",
        "nested": workspace / "a" / ".." / ".." / "outside.txt",
        "sibling": sibling / "outside.txt",
        "parent_symlink": workspace / "parent-link" / "outside.txt",
        "target_symlink": workspace / "target-link",
    }
    monkeypatch.setenv("CELINA_WORKSPACE_DIR", str(workspace))
    calls: list[str] = []
    result = tools.write_file(
        str(targets[case]), "danger", lambda *_a: calls.append("approved") or True,
    )
    assert result.startswith("Error:")
    assert not calls
    assert not (home / "outside.txt").exists()
    assert not (sibling / "outside.txt").exists()


def test_explicit_workspace_and_home_fallback_allow_safe_writes(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safe explicit workspace writes and existing HOME-only writes remain usable."""
    workspace = home / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("CELINA_WORKSPACE_DIR", str(workspace))
    result = tools.write_file(str(workspace / "ok.txt"), "inside", lambda *_a: True)
    assert result.startswith("Wrote")
    assert (workspace / "ok.txt").read_text() == "inside"
    monkeypatch.delenv("CELINA_WORKSPACE_DIR")
    result = tools.write_file(str(home / "home-file.txt"), "home", lambda *_a: True)
    assert result.startswith("Wrote")
    assert (home / "home-file.txt").read_text() == "home"


@pytest.mark.parametrize("kind", ["relative", "missing", "symlink", "outside", "empty"])
def test_invalid_explicit_workspace_fails_closed(
    home: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    """Malformed, symlinked, missing and outside-home roots do not relax containment."""
    outside = home.parent / f"{home.name}-outside"
    outside.mkdir()
    link = home / "link"
    link.symlink_to(outside, target_is_directory=True)
    roots = {"relative": "relative", "missing": str(home / "missing"), "symlink": str(link),
             "outside": str(outside), "empty": ""}
    monkeypatch.setenv("CELINA_WORKSPACE_DIR", roots[kind])
    result = tools.write_file(str(home / "test.txt"), "x", lambda *_a: True)
    assert result.startswith("Error:")
    assert not (home / "test.txt").exists()


def test_approval_callback_cannot_swap_workspace_parent_to_symlink(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recheck containment after approval and before mutation or undo recording."""
    workspace, outside = home / "workspace", home / "outside"
    workspace.mkdir()
    outside.mkdir()
    child = workspace / "child"
    child.mkdir()
    monkeypatch.setenv("CELINA_WORKSPACE_DIR", str(workspace))
    journal_calls: list[str] = []

    class Journal:
        def record_before_mutation(self, *_args: object) -> str:
            journal_calls.append("recorded")
            return "fixture"

        def finalize_mutation(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(state_ops, "get_undo_journal", Journal)

    def approve(*_args: object) -> bool:
        child.rename(workspace / "original")
        child.symlink_to(outside, target_is_directory=True)
        return True

    result = tools.write_file(str(child / "file.txt"), "x", approve)
    assert result.startswith("Error:")
    assert not (outside / "file.txt").exists()
    assert not journal_calls
