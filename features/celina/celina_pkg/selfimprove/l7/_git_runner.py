# Copyright 2026 Celina contributors.
"""Fixed offline Git invocations with explicit subprocess-budget accounting."""

from __future__ import annotations

import os
import re
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from celina_pkg.selfimprove.budget import BudgetGuard

    from .types import GitCommandExecutor, PatchPolicyConfig

_FIXED = frozenset(
    {
        ("rev-parse", "--show-toplevel"),
        ("worktree", "list", "--porcelain", "-z"),
        ("rev-parse", "--verify", "HEAD"),
        ("status", "--porcelain=v1", "--untracked-files=all"),
        ("apply", "--check", "--whitespace=nowarn", "-"),
        ("apply", "--whitespace=nowarn", "-"),
        ("diff", "--no-ext-diff", "--no-textconv", "--"),
    },
)
_ADD_ARGS = 6
_REMOVE_ARGS = 5
_PREFIX = ("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


class GitCommandError(RuntimeError):
    """An unsafe, failed or timed-out Git operation."""


def _allowed(args: tuple[str, ...]) -> bool:
    if args in _FIXED:
        return True
    if len(args) == _ADD_ARGS and args[:4] == ("worktree", "add", "--detach", "--"):
        return args[4].startswith("/") and bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", args[5]))
    return (
        len(args) == _REMOVE_ARGS
        and args[:4] == ("worktree", "remove", "--force", "--")
        and args[4].startswith("/")
    )


def _execute(
    argv: tuple[str, ...],
    cwd: Path,
    input_data: bytes | None,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    env = {
        "PATH": os.defpath,
        "HOME": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }
    if argv[: len(_PREFIX)] != _PREFIX or not _allowed(argv[len(_PREFIX) :]):
        message = "Git executable and arguments must be allowlisted"
        raise GitCommandError(message)
    return subprocess.run(  # noqa: S603  # Fixed Git argv validated immediately above.
        argv,
        cwd=cwd,
        input=input_data,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=env,
    )


class GitRunner:
    """Validate fixed Git command shapes and charge the shared guard."""

    def __init__(
        self,
        budget: BudgetGuard,
        config: PatchPolicyConfig,
        *,
        executor: GitCommandExecutor | None = None,
    ) -> None:
        """Bind an optional trusted test executor and explicit timeout."""
        self.budget, self.config = budget, config
        self.executor = executor or _execute
        self._reserved = False

    def reserve_cleanup(self) -> None:
        """Pre-charge removal and, on failure, a registration check."""
        if self._reserved:
            message = "cleanup already reserved"
            raise GitCommandError(message)
        self.budget.consume_subprocess(2)
        self._reserved = True

    def run(self, args: tuple[str, ...], cwd: Path, input_data: bytes | None = None) -> bytes:
        """Execute one allowlisted Git operation with no shell."""
        if not _allowed(args):
            message = "Git command is not allowed"
            raise GitCommandError(message)
        self.budget.consume_subprocess()
        return self._invoke(args, cwd, input_data)

    def cleanup(self, cwd: Path, worktree: Path) -> None:
        """Use the single pre-charged removal even if the wall budget expired."""
        if not self._reserved:
            message = "cleanup was not reserved"
            raise GitCommandError(message)
        self._reserved = False
        try:
            self._invoke(("worktree", "remove", "--force", "--", str(worktree)), cwd, None)
        except GitCommandError:
            listing = self._invoke(("worktree", "list", "--porcelain", "-z"), cwd, None)
            if b"worktree " + os.fsencode(worktree) in listing.split(b"\0"):
                raise

    def _invoke(self, args: tuple[str, ...], cwd: Path, input_data: bytes | None) -> bytes:
        try:
            result = self.executor((*_PREFIX, *args), cwd, input_data, self.config.git_timeout_sec)
        except (OSError, subprocess.TimeoutExpired) as exc:
            message = "Git operation failed or timed out"
            raise GitCommandError(message) from exc
        if result.returncode != 0:
            message = "Git operation returned a nonzero exit code"
            raise GitCommandError(message)
        return result.stdout
