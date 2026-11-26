"""
Celina AI (Celly) - Structured Subprocess Runner (Phase 2)
Eliminates false-success string parsing by returning a structured CommandResult
with returncode, stdout, stderr, and timeout status for every OS invocation.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    command: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout or self.stderr).strip()


def run_command(
    cmd: Sequence[str],
    *,
    timeout: float = 5.0,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
) -> CommandResult:
    """Safely executes a command list without shell=True and returns CommandResult."""
    cmd_list = [str(c) for c in cmd]
    if not cmd_list:
        return CommandResult(returncode=2, stdout="", stderr="Empty command.", command=())
    if not shutil.which(cmd_list[0]):
        return CommandResult(
            returncode=127,
            stdout="",
            stderr=f"Required utility '{cmd_list[0]}' not found.",
            command=tuple(cmd_list),
        )
    try:
        proc = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(env) if env is not None else None,
            cwd=cwd,
        )
        return CommandResult(
            returncode=int(proc.returncode),
            stdout=(proc.stdout or "").strip(),
            stderr=(proc.stderr or "").strip(),
            timed_out=False,
            command=tuple(cmd_list),
        )
    except subprocess.TimeoutExpired as exc:
        out_s = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err_s = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CommandResult(
            returncode=124,
            stdout=out_s.strip(),
            stderr=(err_s or f"Command timed out after {timeout}s.").strip(),
            timed_out=True,
            command=tuple(cmd_list),
        )
    except OSError as exc:
        return CommandResult(
            returncode=126,
            stdout="",
            stderr=str(exc),
            timed_out=False,
            command=tuple(cmd_list),
        )
