# Copyright 2026 Celina contributors.
"""Uncovered L3 entrypoint and L7 cleanup security boundaries."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from celina_pkg.selfimprove.l3 import cli as l3_cli
from celina_pkg.selfimprove.l7._git_runner import GitCommandError
from celina_pkg.selfimprove.l7.types import PatchProposal
from celina_pkg.selfimprove.l7.worktree import WorktreeCleanupError
from celina_pkg.selfimprove.wiring._eval_probes import injection_detection

from .test_l7_worktree import PATCH, manager

DISABLED_EXIT = 2


def test_l3_entrypoint_disabled_without_home_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual module entrypoint preserves the default-off exit code."""
    assert callable(l3_cli.main)
    monkeypatch.setenv("SELFIMPROVE_ENABLED", "0")
    monkeypatch.setattr("sys.argv", ["selfimprove-l3"])
    with pytest.warns(RuntimeWarning), pytest.raises(SystemExit) as raised:
        runpy.run_module("celina_pkg.selfimprove.l3.cli", run_name="__main__")
    assert raised.value.code == DISABLED_EXIT


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_cleanup_never_follows_replaced_worktree_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, cleanup_fails: bool,
) -> None:
    """Whether Git cleanup reports success or failure, a symlink is not followed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    session = manager(repo, tmp_path).session(PatchProposal("p", PATCH, "change"))
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "sentinel"
    marker.write_text("unchanged")
    link = tmp_path / "unexpected-link"
    link.symlink_to(outside, target_is_directory=True)
    session.path = link
    session._started = True  # noqa: SLF001  # Exercise cleanup after a simulated path replacement.

    def cleanup(_repo: Path, _path: Path) -> None:
        if cleanup_fails:
            msg = "fixture Git failure"
            raise GitCommandError(msg)

    monkeypatch.setattr(session.runner, "cleanup", cleanup)
    with pytest.raises(WorktreeCleanupError):
        session._cleanup()  # noqa: SLF001  # Directly exercise cleanup fault injection.
    assert link.is_symlink()
    assert marker.read_text() == "unchanged"


def test_security_probe_helpers_in_process(tmp_path: Path) -> None:
    """Exercise component probes in-process, without claiming downstream refusal."""
    from celina_pkg import evidence, router  # noqa: PLC0415  # Explicit active test.
    from celina_pkg.selfimprove.wiring import _eval_security as security  # noqa: PLC0415

    work = tmp_path / "credential"
    work.mkdir()
    credential = security._credential(work, lambda _path: "Error: blocked")  # noqa: SLF001
    assert credential.passed
    injection = security._injection(  # noqa: SLF001  # Exercise actual detector and sanitizer.
        evidence.detect_prompt_injection,
        router._sanitize_tool_prompt_content,  # noqa: SLF001  # Exercise actual sanitizer.
        credential,
    )
    assert injection.passed is injection_detection(evidence.detect_prompt_injection).passed
    assert "downstream model refusal was not evaluated" in injection.reason
    assert security._redirect().passed  # noqa: SLF001  # Uses a simulated redirect session.
    assert security._approval().passed  # noqa: SLF001  # Uses a real in-process token.
    assert security._provenance().passed  # noqa: SLF001


@pytest.mark.parametrize("escape", [False, True])
def test_traversal_probe_records_actual_writer_result(tmp_path: Path, *, escape: bool) -> None:
    """The probe reports a hypothetical writer's real filesystem effect, not a stubbed score."""
    from celina_pkg.selfimprove.wiring import _eval_security as security  # noqa: PLC0415

    def writer(path: str, content: str, _callback: object) -> str:
        if escape:
            Path(path).write_text(content)
            return "Wrote fixture"
        return "Error: blocked"

    result = security._traversal(tmp_path, writer)  # noqa: SLF001
    assert result.passed is not escape
    assert (tmp_path / "outside.txt").exists() is escape
