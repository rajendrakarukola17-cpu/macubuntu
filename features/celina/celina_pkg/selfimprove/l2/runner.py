# Copyright 2026 Celina contributors.
"""Bounded-output runner for a small allowlist of external verifier commands."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

ALLOWED = frozenset({"pytest", "ruff", "mypy", "bandit"})
OUTPUT_LIMIT = 4096
MARKER = "[TRUNCATED]"


@dataclass(frozen=True, slots=True)
class RawCommandOutput:
    """An immutable, bounded command result."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class CommandExecutor(Protocol):
    """Injectable process boundary."""

    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a fixed argument vector."""


def _execute(argv: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # Executable is verified against the allowlist.
        argv,
        cwd=cwd,
        timeout=timeout,
        shell=False,
        check=False,
        text=True,
        capture_output=True,
    )


def _bounded(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = value or ""
    if len(value) > OUTPUT_LIMIT:
        return value[: OUTPUT_LIMIT - len(MARKER)] + MARKER
    return value


def _validated_cwd(cwd: Path, workspace_root: Path | None) -> Path:
    try:
        resolved = cwd.resolve(strict=True)
        root = workspace_root.resolve(strict=True) if workspace_root is not None else None
    except OSError as exc:
        message = "cwd or workspace root does not exist"
        raise ValueError(message) from exc
    if not resolved.is_dir() or (root is not None and not root.is_dir()):
        message = "cwd and workspace root must be directories"
        raise ValueError(message)
    if root is not None and not resolved.is_relative_to(root):
        message = "cwd escapes workspace root"
        raise ValueError(message)
    return resolved


def run_allowlisted_command(  # noqa: PLR0913  # Explicit process and safety boundaries.
    executable: str,
    args: tuple[str, ...],
    *,
    cwd: Path,
    timeout: float,
    workspace_root: Path | None = None,
    executor: CommandExecutor | None = None,
) -> RawCommandOutput:
    """Run one allowed verifier without a shell and bound returned output."""
    if executable not in ALLOWED:
        message = "executable is not an allowed verifier"
        raise ValueError(message)
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        message = "timeout must be finite and positive"
        raise ValueError(message)
    directory = _validated_cwd(cwd, workspace_root)
    call = _execute if executor is None else executor
    try:
        result = call([executable, *args], cwd=directory, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return RawCommandOutput(-1, _bounded(exc.stdout), _bounded(exc.stderr), timed_out=True)
    return RawCommandOutput(result.returncode, _bounded(result.stdout), _bounded(result.stderr))
