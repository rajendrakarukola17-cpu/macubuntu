# Copyright 2026 Celina contributors.
"""Detached worktrees use offline local Git and preserve the source checkout."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from celina_pkg.selfimprove.budget import BudgetExceeded, BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits
from celina_pkg.selfimprove.l7._git_runner import GitCommandError, GitRunner, _execute
from celina_pkg.selfimprove.l7.types import PatchPolicyConfig, PatchProposal
from celina_pkg.selfimprove.l7.worktree import (
    WorktreeApplyError,
    WorktreeCleanupError,
    WorktreeManager,
    _check_targets,
    _safe_path,
)

LIMITS = BudgetLimits(10, 100, 60, 50, 30)
PATCH = (
    "diff --git a/hello.txt b/hello.txt\n--- a/hello.txt\n"
    "+++ b/hello.txt\n@@ -1 +1 @@\n-old\n+new\n"
)


def git(repo: Path, *args: str) -> bytes:
    """Run a fixed local fixture command in an isolated repository."""
    env = {
        "PATH": os.defpath,
        "HOME": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    return subprocess.run(  # noqa: S603  # Fixed local Git commands and temporary fixture paths.
        ["/usr/bin/git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    ).stdout


@pytest.fixture
def local_repo(tmp_path: Path) -> Path:
    """Create a committed local-only fixture using no global configuration."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "hello.txt").write_text("old\n")
    git(repo, "add", "hello.txt")
    git(repo, "commit", "-qm", "initial")
    return repo


def policy_manifest(tmp_path: Path) -> Path:
    """Write a synthetic, explicit manifest."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"protected.py": "a" * 64}))
    return path


def state(repo: Path) -> tuple[bytes, bytes, str, str]:
    """Snapshot HEAD, status, file contents, and the exact index digest."""
    return (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
        (repo / "hello.txt").read_text(),
        hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest(),
    )


def manager(repo: Path, tmp_path: Path, budget: BudgetGuard | None = None) -> WorktreeManager:
    """Bind an explicit root and private external parent."""
    return WorktreeManager(
        repo,
        tmp_path / "worktrees",
        policy_manifest(tmp_path),
        budget or BudgetGuard(LIMITS),
        PatchPolicyConfig(),
    )


def test_real_worktree_applies_only_in_detached_tree_and_cleans(
    tmp_path: Path,
    local_repo: Path,
) -> None:
    """No patch changes main HEAD, index or worktree before/during/after."""
    original = state(local_repo)
    worktrees = manager(local_repo, tmp_path)
    with worktrees.session(PatchProposal("p1", PATCH, "change file")) as session:
        assert (session.path / "hello.txt").read_text() == "new\n"
        assert git(session.path, "rev-parse", "--abbrev-ref", "HEAD").strip() == b"HEAD"
        assert state(local_repo) == original
        path = session.path
    assert not path.exists()
    assert state(local_repo) == original
    assert str(path).encode() not in git(local_repo, "worktree", "list", "--porcelain")


def test_cleanup_on_exception_bad_apply_and_subprocess_budget(
    tmp_path: Path,
    local_repo: Path,
) -> None:
    """Cleanup reservation permits removal even after normal budget exhaustion."""
    before = state(local_repo)
    worktrees = manager(local_repo, tmp_path)
    session = worktrees.session(PatchProposal("p1", PATCH, "change"))

    def fail_in_session() -> None:
        with session:
            message = "body"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="body"):
        fail_in_session()
    path = session.path
    assert not path.exists()
    bad = PATCH.replace("-old", "-missing")
    with pytest.raises(WorktreeApplyError), worktrees.session(PatchProposal("bad", bad, "change")):
        pass
    limited = BudgetGuard(BudgetLimits(10, 100, 60, 50, 6))
    worktrees = manager(local_repo, tmp_path, limited)
    with (
        pytest.raises(BudgetExceeded, match="subprocess"),
        worktrees.session(
            PatchProposal("limited", PATCH, "change"),
        ),
    ):
        pass
    assert state(local_repo) == before
    listing = git(local_repo, "worktree", "list", "--porcelain")
    assert str(tmp_path / "worktrees").encode() not in listing
    assert not list((tmp_path / "worktrees").iterdir())


def test_rejects_symlink_and_in_repo_parent_before_git(tmp_path: Path, local_repo: Path) -> None:
    """A temp parent cannot pollute the source repo or traverse a symlink."""
    manifest = policy_manifest(tmp_path)
    link = tmp_path / "link"
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link.symlink_to(target, target_is_directory=True)
    for parent in (local_repo / "nested", link):
        worktrees = WorktreeManager(
            local_repo,
            parent,
            manifest,
            BudgetGuard(LIMITS),
            PatchPolicyConfig(),
        )
        with pytest.raises(GitCommandError), worktrees.session(PatchProposal("p", PATCH, "change")):
            pass
    assert not (local_repo / "nested").exists()


def test_git_runner_rejects_commands_outside_fixed_allowlist(tmp_path: Path) -> None:
    """No injected executor sees merge, push, commit, checkout or arbitrary args."""
    calls: list[tuple[str, ...]] = []

    def fake(
        argv: tuple[str, ...],
        _cwd: Path,
        _input_data: bytes | None,
        _timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    runner = GitRunner(BudgetGuard(LIMITS), PatchPolicyConfig(), executor=fake)
    for args in (
        ("merge", "HEAD"),
        ("push",),
        ("commit", "-m", "x"),
        ("checkout", "main"),
        ("apply", "--unsafe-paths"),
    ):
        with pytest.raises(GitCommandError):
            runner.run(args, tmp_path)
    assert calls == []


def test_write_budget_exhaustion_does_not_create_parent(tmp_path: Path, local_repo: Path) -> None:
    """A zero write budget must stop before creating the worktree parent."""
    limited = BudgetGuard(BudgetLimits(10, 100, 60, 0, 30))
    worktrees = manager(local_repo, tmp_path, limited)
    with (
        pytest.raises(BudgetExceeded, match="file_writes"),
        worktrees.session(
            PatchProposal("p", PATCH, "change"),
        ),
    ):
        pass
    assert not (tmp_path / "worktrees").exists()


def test_wall_expiry_still_uses_precharged_cleanup(tmp_path: Path, local_repo: Path) -> None:
    """A wall-clock overrun after creation must not prevent reserved cleanup."""
    current = [0.0]

    def clock() -> float:
        return current[0]

    budget = BudgetGuard(BudgetLimits(10, 100, 1, 50, 30), clock=clock)
    worktrees = manager(local_repo, tmp_path, budget)
    session = worktrees.session(PatchProposal("p", PATCH, "change"))

    def exceed_wall() -> None:
        with session:
            current[0] = 2.0
            budget.check_wall_clock()

    with pytest.raises(BudgetExceeded, match="wall_seconds"):
        exceed_wall()
    assert not session.path.exists()
    assert str(session.path).encode() not in git(local_repo, "worktree", "list", "--porcelain")


def test_symlink_target_is_refused_before_apply(tmp_path: Path, local_repo: Path) -> None:
    """A checked-out symlink target cannot be patched, even if it points outside."""
    outside = tmp_path / "outside.txt"
    outside.write_text("old\n")
    (local_repo / "hello.txt").unlink()
    (local_repo / "hello.txt").symlink_to(outside)
    git(local_repo, "add", "hello.txt")
    git(local_repo, "commit", "-qm", "symlink")
    worktrees = manager(local_repo, tmp_path)
    with (
        pytest.raises(WorktreeApplyError, match="symlink"),
        worktrees.session(
            PatchProposal("p", PATCH, "change"),
        ),
    ):
        pass
    assert outside.read_text() == "old\n"
    assert not (tmp_path / "worktrees").exists()


def test_runner_timeout_is_charged_and_fails_closed(tmp_path: Path) -> None:
    """Timeouts and nonzero exits are bounded and never treated as success."""
    budget = BudgetGuard(LIMITS)

    def timeout(
        argv: tuple[str, ...],
        _cwd: Path,
        _data: bytes | None,
        seconds: float,
    ) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, seconds)

    runner = GitRunner(budget, PatchPolicyConfig(), executor=timeout)
    with pytest.raises(GitCommandError, match="timed out"):
        runner.run(("rev-parse", "--show-toplevel"), tmp_path)
    assert budget.snapshot().subprocesses == 1


def test_failed_add_before_registration_cleans_and_preserves_original_error(
    tmp_path: Path,
    local_repo: Path,
) -> None:
    """A failed unregistered add leaves no files or metadata and reports the add failure."""

    def reject_add(
        argv: tuple[str, ...],
        cwd: Path,
        data: bytes | None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        if "add" in argv and "worktree" in argv:
            return subprocess.CompletedProcess(argv, 1, b"", b"failed add")
        return _execute(argv, cwd, data, timeout)

    budget = BudgetGuard(LIMITS)
    worktrees = WorktreeManager(
        local_repo,
        tmp_path / "worktrees",
        policy_manifest(tmp_path),
        budget,
        PatchPolicyConfig(),
        executor=reject_add,
    )
    with pytest.raises(GitCommandError) as raised:
        worktrees.session(PatchProposal("p", PATCH, "change")).__enter__()
    assert not isinstance(raised.value, WorktreeCleanupError)
    assert not list((tmp_path / "worktrees").iterdir())
    assert str(tmp_path / "worktrees").encode() not in git(
        local_repo,
        "worktree",
        "list",
        "--porcelain",
    )


def test_failed_add_after_registration_removes_worktree(tmp_path: Path, local_repo: Path) -> None:
    """An add reported as failed after registration still removes metadata and disk."""

    def late_failure(
        argv: tuple[str, ...],
        cwd: Path,
        data: bytes | None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        result = _execute(argv, cwd, data, timeout)
        if "add" in argv and "worktree" in argv:
            return subprocess.CompletedProcess(argv, 1, b"", b"late failure")
        return result

    worktrees = WorktreeManager(
        local_repo,
        tmp_path / "worktrees",
        policy_manifest(tmp_path),
        BudgetGuard(LIMITS),
        PatchPolicyConfig(),
        executor=late_failure,
    )
    with pytest.raises(GitCommandError) as raised:
        worktrees.session(PatchProposal("p", PATCH, "change")).__enter__()
    assert not isinstance(raised.value, WorktreeCleanupError)
    assert not list((tmp_path / "worktrees").iterdir())
    assert str(tmp_path / "worktrees").encode() not in git(
        local_repo,
        "worktree",
        "list",
        "--porcelain",
    )


def test_registered_cleanup_failure_reports_host_intervention(
    tmp_path: Path,
    local_repo: Path,
) -> None:
    """A Git removal failure cannot be misreported as successful cleanup."""

    def fail_remove(
        argv: tuple[str, ...],
        cwd: Path,
        data: bytes | None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        if "remove" in argv and "worktree" in argv:
            return subprocess.CompletedProcess(argv, 1, b"", b"failed remove")
        return _execute(argv, cwd, data, timeout)

    worktrees = WorktreeManager(
        local_repo,
        tmp_path / "worktrees",
        policy_manifest(tmp_path),
        BudgetGuard(LIMITS),
        PatchPolicyConfig(),
        executor=fail_remove,
    )
    session = worktrees.session(PatchProposal("p", PATCH, "change"))
    with pytest.raises(WorktreeCleanupError), session:
        pass
    assert str(session.path).encode() in git(local_repo, "worktree", "list", "--porcelain")
    git(local_repo, "worktree", "prune")


def test_failed_add_with_unregistered_partial_files_is_cleaned(
    tmp_path: Path,
    local_repo: Path,
) -> None:
    """A failed add may leave files even though Git never registered the path."""

    def partial_add(
        argv: tuple[str, ...],
        cwd: Path,
        data: bytes | None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        if "add" in argv and "worktree" in argv:
            path = Path(argv[-2])
            (path / "partial").write_text("unfinished")
            return subprocess.CompletedProcess(argv, 1, b"", b"failed add")
        return _execute(argv, cwd, data, timeout)

    worktrees = WorktreeManager(
        local_repo,
        tmp_path / "worktrees",
        policy_manifest(tmp_path),
        BudgetGuard(LIMITS),
        PatchPolicyConfig(),
        executor=partial_add,
    )
    with pytest.raises(GitCommandError) as raised:
        worktrees.session(PatchProposal("p", PATCH, "change")).__enter__()
    assert not isinstance(raised.value, WorktreeCleanupError)
    assert not list((tmp_path / "worktrees").iterdir())


def test_dirty_nested_and_nonprivate_repo_fail_closed(tmp_path: Path, local_repo: Path) -> None:
    """Unsafe roots and dirty checkouts are rejected before a worktree is created."""
    (local_repo / "untracked").write_text("dirty")
    trees = manager(local_repo, tmp_path)
    with pytest.raises(GitCommandError, match="clean"):
        trees.session(PatchProposal("p", PATCH, "change")).__enter__()
    (local_repo / "untracked").unlink()
    directory = tmp_path / "nonprivate"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    trees = WorktreeManager(
        local_repo,
        directory,
        policy_manifest(tmp_path),
        BudgetGuard(LIMITS),
        PatchPolicyConfig(),
    )
    with pytest.raises(GitCommandError, match="private"):
        trees.session(PatchProposal("p", PATCH, "change")).__enter__()
    subdir = local_repo / "subdir"
    subdir.mkdir()
    trees = WorktreeManager(
        subdir,
        tmp_path / "new",
        policy_manifest(tmp_path),
        BudgetGuard(LIMITS),
        PatchPolicyConfig(),
    )
    with pytest.raises(GitCommandError, match="top level"):
        trees.session(PatchProposal("p", PATCH, "change")).__enter__()


def test_path_and_target_validation_edges(tmp_path: Path) -> None:
    """Relative paths, non-directories and linked targets are unsafe."""
    with pytest.raises(GitCommandError, match="absolute"):
        _safe_path(Path("relative"))
    file_path = tmp_path / "file"
    file_path.write_text("data")
    with pytest.raises(GitCommandError, match="directory"):
        _safe_path(file_path, directory=True)
    hardlink = tmp_path / "hardlink"
    os.link(file_path, hardlink)
    with pytest.raises(WorktreeApplyError, match="regular file"):
        _check_targets(tmp_path, ("hardlink",))
    _check_targets(tmp_path, ("missing",))
    with pytest.raises(WorktreeApplyError, match="regular file"):
        _check_targets(tmp_path, ("directory",)) if (
            tmp_path / "directory"
        ).mkdir() is None else None


def test_runner_cleanup_reservations_and_executor_validation(tmp_path: Path) -> None:
    """Cleanup cannot run unreserved or be reserved twice, and argv is fixed."""
    runner = GitRunner(BudgetGuard(LIMITS), PatchPolicyConfig())
    with pytest.raises(GitCommandError, match="not reserved"):
        runner.cleanup(tmp_path, tmp_path / "tree")
    runner.reserve_cleanup()
    with pytest.raises(GitCommandError, match="already reserved"):
        runner.reserve_cleanup()
    with pytest.raises(GitCommandError, match="allowlisted"):
        _execute(("git", "push"), tmp_path, None, 1.0)


def test_apply_without_policy_is_rejected(tmp_path: Path, local_repo: Path) -> None:
    """The internal apply phase cannot be entered without validated policy."""
    session = manager(local_repo, tmp_path).session(PatchProposal("p", PATCH, "change"))
    with pytest.raises(WorktreeApplyError, match="policy"):
        session._apply()  # noqa: SLF001  # Exercise the internal fail-closed precondition.


def test_cleanup_filesystem_failure_is_always_reported_as_cleanup_error(
    tmp_path: Path,
    local_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both Git and filesystem teardown failures retain the cleanup error classification."""

    def fail_remove(
        argv: tuple[str, ...],
        cwd: Path,
        data: bytes | None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        if "remove" in argv and "worktree" in argv:
            return subprocess.CompletedProcess(argv, 1, b"", b"failed remove")
        return _execute(argv, cwd, data, timeout)

    worktrees = WorktreeManager(
        local_repo,
        tmp_path / "worktrees",
        policy_manifest(tmp_path),
        BudgetGuard(LIMITS),
        PatchPolicyConfig(),
        executor=fail_remove,
    )
    session = worktrees.session(PatchProposal("p", PATCH, "change"))
    session.__enter__()

    def fail_rmtree(_path: Path) -> None:
        message = "permission denied"
        raise OSError(message)

    monkeypatch.setattr("celina_pkg.selfimprove.l7.worktree.shutil.rmtree", fail_rmtree)
    with pytest.raises(WorktreeCleanupError):
        session.__exit__(None, None, None)
    monkeypatch.undo()
    git(local_repo, "worktree", "remove", "--force", str(session.path))


def test_internal_safety_checks_after_directory_race(tmp_path: Path, local_repo: Path) -> None:
    """The post-creation privacy recheck rejects a directory whose mode changed."""
    trees = manager(local_repo, tmp_path)
    trees.worktree_parent.mkdir(mode=0o755)
    trees.worktree_parent.chmod(0o755)
    session = trees.session(PatchProposal("p", PATCH, "change"))
    with pytest.raises(GitCommandError, match="private"):
        session._create_parent()  # noqa: SLF001  # Simulate a mode change between safety checks.
    session.__exit__(None, None, None)


def test_invalid_git_commit_from_executor_is_rejected(tmp_path: Path, local_repo: Path) -> None:
    """Malformed HEAD output cannot become a worktree-add argument."""

    def invalid_head(
        argv: tuple[str, ...],
        cwd: Path,
        data: bytes | None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        if "--verify" in argv:
            return subprocess.CompletedProcess(argv, 0, b"bad-head\n", b"")
        return _execute(argv, cwd, data, timeout)

    trees = WorktreeManager(
        local_repo,
        tmp_path / "worktrees",
        policy_manifest(tmp_path),
        BudgetGuard(LIMITS),
        PatchPolicyConfig(),
        executor=invalid_head,
    )
    with pytest.raises(GitCommandError, match="invalid base"):
        trees.session(PatchProposal("p", PATCH, "change")).__enter__()
    assert not (tmp_path / "worktrees").exists()


def test_tracked_symlink_is_rejected_before_worktree_creation(
    tmp_path: Path,
    local_repo: Path,
) -> None:
    """Pre-existing symlink targets are refused before even creating a temp parent."""
    outside = tmp_path / "outside.txt"
    outside.write_text("old\n")
    (local_repo / "hello.txt").unlink()
    (local_repo / "hello.txt").symlink_to(outside)
    git(local_repo, "add", "hello.txt")
    git(local_repo, "commit", "-qm", "tracked symlink")
    trees = manager(local_repo, tmp_path)
    with pytest.raises(WorktreeApplyError, match="symlink"):
        trees.session(PatchProposal("p", PATCH, "change")).__enter__()
    assert not (tmp_path / "worktrees").exists()
    assert outside.read_text() == "old\n"
