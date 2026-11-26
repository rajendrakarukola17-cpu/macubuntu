# Copyright 2026 Celina contributors.
"""Detached throwaway worktrees; this is not an OS security sandbox."""

from __future__ import annotations

import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self

from ._git_runner import GitCommandError, GitRunner
from .policy import validate_patch_proposal

if TYPE_CHECKING:
    from types import TracebackType

    from celina_pkg.selfimprove.budget import BudgetGuard

    from .types import GitCommandExecutor, PatchPolicyConfig, PatchProposal, PolicyCheckResult


_PRIVATE_DIR = 0o700


class WorktreeApplyError(GitCommandError):
    """The patch could not safely be applied."""


class WorktreeTargetError(WorktreeApplyError):
    """A repository or worktree target is not a safe regular file."""


class WorktreeCleanupError(GitCommandError):
    """Cleanup failed; host intervention may be necessary."""


def _safe_path(path: Path, *, directory: bool = False) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        message = "path must be explicit and absolute"
        raise GitCommandError(message)
    if any(item.is_symlink() for item in (path, *path.parents)):
        message = "symlink in path"
        raise GitCommandError(message)
    if directory and path.exists() and not path.is_dir():
        message = "expected directory"
        raise GitCommandError(message)


def _check_targets(root: Path, paths: tuple[str, ...]) -> None:
    """Reject symlinks and hardlinks at check time; races remain a residual risk."""
    for name in paths:
        target = root / name
        for item in (target, *target.parents):
            if item == root:
                break
            if item.is_symlink():
                message = "symlink in patch target"
                raise WorktreeTargetError(message)
        if target.exists():
            info = target.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                message = "patch target must be an unlinked regular file"
                raise WorktreeTargetError(message)


def _remove_directory(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError as exc:
        message = "worktree filesystem removal failed; host intervention required"
        raise WorktreeCleanupError(message) from exc


class WorktreeManager:
    """Bind a trusted local repository and an explicit private external parent."""

    def __init__(  # noqa: PLR0913  # Explicit repository, policy, budget and executor dependencies.
        self,
        repo: Path,
        worktree_parent: Path,
        manifest_path: Path,
        budget: BudgetGuard,
        config: PatchPolicyConfig,
        *,
        executor: GitCommandExecutor | None = None,
    ) -> None:
        """Bind configuration without filesystem access."""
        self.repo, self.worktree_parent, self.manifest_path = repo, worktree_parent, manifest_path
        self.budget, self.config, self.executor = budget, config, executor

    def session(self, proposal: PatchProposal) -> ThrowawayWorktreeSession:
        """Create a lazy session; validation precedes all Git invocations."""
        return ThrowawayWorktreeSession(self, proposal)


class ThrowawayWorktreeSession:
    """Context-managed detached tree with pre-reserved cleanup budget."""

    def __init__(self, manager: WorktreeManager, proposal: PatchProposal) -> None:
        """Prepare a session without file or Git I/O."""
        self.manager, self.proposal = manager, proposal
        self.runner = GitRunner(manager.budget, manager.config, executor=manager.executor)
        self.path = Path()
        self.base_commit = ""
        self.policy: PolicyCheckResult | None = None
        self._started = False

    def __enter__(self) -> Self:
        """Validate, create a detached tree, check and apply the patch."""
        m = self.manager
        self.policy = validate_patch_proposal(self.proposal, m.manifest_path, m.config)
        self._validate_paths()
        _check_targets(m.repo, self.policy.touched_files)
        self._check_repo()
        self.runner.reserve_cleanup()
        m.budget.consume_file_write()
        self._create_parent()
        self.path = Path(tempfile.mkdtemp(prefix="patch-", dir=m.worktree_parent))
        self._started = True
        try:
            self.runner.run(
                ("worktree", "add", "--detach", "--", str(self.path), self.base_commit),
                m.repo,
            )
            self._apply()
        except BaseException:
            self._cleanup()
            raise
        return self

    def _validate_paths(self) -> None:
        m = self.manager
        _safe_path(m.repo, directory=True)
        _safe_path(m.worktree_parent, directory=True)
        if m.worktree_parent == m.repo or m.worktree_parent.is_relative_to(m.repo):
            message = "worktree parent must be outside repository"
            raise GitCommandError(message)
        if (
            m.worktree_parent.exists()
            and stat.S_IMODE(m.worktree_parent.stat().st_mode) != _PRIVATE_DIR
        ):
            message = "worktree parent must be private"
            raise GitCommandError(message)

    def _create_parent(self) -> None:
        parent = self.manager.worktree_parent
        if not parent.exists():
            parent.mkdir(mode=_PRIVATE_DIR)
        _safe_path(parent, directory=True)
        if stat.S_IMODE(parent.stat().st_mode) != _PRIVATE_DIR:
            message = "worktree parent must be private"
            raise GitCommandError(message)

    def _check_repo(self) -> None:
        repo = self.manager.repo
        top = self.runner.run(("rev-parse", "--show-toplevel"), repo).decode().strip()
        if top != str(repo):
            message = "repository must be the explicit top level"
            raise GitCommandError(message)
        if self.runner.run(("status", "--porcelain=v1", "--untracked-files=all"), repo):
            message = "repository must be clean"
            raise GitCommandError(message)
        commit = self.runner.run(("rev-parse", "--verify", "HEAD"), repo).decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            message = "invalid base commit"
            raise GitCommandError(message)
        self.base_commit = commit

    def _apply(self) -> None:
        if self.policy is None:
            message = "policy must be checked before patch application"
            raise WorktreeApplyError(message)
        _check_targets(self.path, self.policy.touched_files)
        patch = self.proposal.unified_diff.encode("utf-8")
        try:
            self.runner.run(("apply", "--check", "--whitespace=nowarn", "-"), self.path, patch)
            self.manager.budget.consume_file_write(len(self.policy.touched_files))
            self.runner.run(("apply", "--whitespace=nowarn", "-"), self.path, patch)
        except GitCommandError as exc:
            message = "patch failed git apply"
            raise WorktreeApplyError(message) from exc
        _check_targets(self.path, self.policy.touched_files)

    def _cleanup(self) -> None:
        if not self._started:
            return
        self._started = False
        try:
            self.runner.cleanup(self.manager.repo, self.path)
        except GitCommandError as exc:
            if self.path.exists() and not self.path.is_symlink():
                _remove_directory(self.path)
            message = "worktree removal failed; inspect Git metadata"
            raise WorktreeCleanupError(message) from exc
        if self.path.is_symlink():
            message = "worktree path became a symlink"
            raise WorktreeCleanupError(message)
        if self.path.exists():
            _remove_directory(self.path)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Attempt cleanup even on errors and preserve the original exception."""
        self._cleanup()
