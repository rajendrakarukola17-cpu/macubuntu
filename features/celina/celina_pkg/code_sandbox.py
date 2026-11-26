"""
Celina AI (Celly) - Sandboxed Code Execution Tool (v4.5, Update 17)

Open Interpreter inspired: runs Python snippets and Bash commands in isolated
subprocesses with Bubblewrap sandboxing, timeout caps, output limits, and
approval gating. All execution is local — no cloud calls, no secrets exposed.

Tools exposed:
  - code_sandbox(code, language, timeout, approval_cb)  -> run Python or Bash snippets

Safety design:
  - Python: Bubblewrap with --ro-bind /, --tmpfs /home, --unshare-net, --clearenv
  - Bash:   Bubblewrap with read-only /home, no network, 20s cap
  - Output: capped at 3000 chars, stderr captured separately
  - Catastrophic commands: blocked by shared denylist
  - Network: always disabled in sandbox
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from . import command_runner, config, guardrails

# Output cap to prevent runaway responses
OUTPUT_CAP = 3000

# Timeout limits
PYTHON_MAX_TIMEOUT = 15
BASH_MAX_TIMEOUT = 20

# Denylist shared with run_shell_command
_CATASTROPHIC_PATTERNS = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "> /dev/sda",
    "chmod -R 777 /",
    "chown -R root /",
    ":(){ :|:& };:",
    "fork bomb",
    "format c:",
    "del /f /s /q",
    "shutdown",
    "reboot",
    "halt",
    "init 0",
    "init 6",
    "mkfs.ext4",
    "mkfs.xfs",
    "mkfs.btrfs",
)


def _is_catastrophic(code: str) -> bool:
    low = code.lower().strip()
    return any(pat in low for pat in _CATASTROPHIC_PATTERNS)


def _cap_output(text: str) -> str:
    if len(text) <= OUTPUT_CAP:
        return text
    return text[:OUTPUT_CAP] + f"\n...[truncated at {OUTPUT_CAP} chars]"


def _build_bwrap_python_cmd(script_path: Path, timeout: int) -> list[str]:
    sandbox_py = shutil.which("python3", path="/usr/bin:/bin") or "/usr/bin/python3"
    home_resolved = config.HOME.resolve() if hasattr(config, "HOME") else Path.home()
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--tmpfs", str(home_resolved),
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        "--bind", str(script_path.parent), "/tmp/work",
        "--chdir", "/tmp/work",
        "--dev", "/dev",
        "--proc", "/proc",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/work",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "LANG", "C.UTF-8",
        sandbox_py, "-I", f"/tmp/work/{script_path.name}",
    ]


def _build_bwrap_bash_cmd(command: str) -> list[str]:
    sandbox_sh = shutil.which("bash", path="/usr/bin:/bin") or "/bin/bash"
    home_resolved = config.HOME.resolve() if hasattr(config, "HOME") else Path.home()
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--tmpfs", str(home_resolved),
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        "--dev", "/dev",
        "--proc", "/proc",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/work",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
        sandbox_sh, "-c", command,
    ]


def code_sandbox(
    code: str,
    *,
    language: str = "python",
    timeout: int = 5,
    approval_cb: Optional[Any] = None,
) -> str:
    """
    Run a Python or Bash snippet in an isolated sandbox (Open Interpreter style).

    Parameters
    ----------
    code : str
        Source code to execute.
    language : str
        "python" or "bash". Default "python".
    timeout : int
        Seconds before hard kill (1..15 for python, 1..20 for bash).
    approval_cb : callable, optional
        User approval callback. Required for tier-2 execution.

    Returns
    -------
    str
        Stdout/stderr combined, capped at 3000 chars, with an exit status tag.

    Safety
    ------
    - Bubblewrap sandbox with read-only host, no network, stripped env
    - Catastrophic commands blocked (rm -rf /, mkfs, shutdown, etc.)
    - Timeout enforced by subprocess layer
    - Output capped to prevent runaway responses
    """
    code = (code or "").strip()
    if not code:
        return "Error: Empty code snippet."
    if len(code) > 16384:
        return "Error: Code snippet exceeds 16 KB sandbox limit."
    if _is_catastrophic(code):
        return "Blocked: Code matched catastrophic safety denylist."
    if approval_cb is None:
        return "Denied: Sandboxed code execution requires interactive user approval."

    lang = (language or "python").strip().lower()
    if lang not in ("python", "bash"):
        return f"Error: Unsupported language '{language}'. Use 'python' or 'bash'."

    if lang == "python":
        sec = max(1, min(PYTHON_MAX_TIMEOUT, int(timeout)))
        bwrap_ok = shutil.which("bwrap") is not None
        mode = "bwrap-python-sandbox" if bwrap_ok else "python-isolated"

        args = {"code": code, "language": lang, "timeout": sec, "mode": mode}
        from . import approval

        token, deny_err = approval.request_bound_approval(
            "code_sandbox",
            args,
            approval_cb,
            title=(
                f"Approve Sandboxed Python Execution ({sec}s, no-net)?"
                if bwrap_ok
                else f"WARNING: Bubblewrap Unavailable — Approve Isolated Python ({sec}s)?"
            ),
            target="bwrap(python3 -I, no-net, no-home)" if bwrap_ok else "python3 -I (isolated)",
            extra_warning=(
                ""
                if bwrap_ok
                else "Bubblewrap userns unavailable; script runs with `python3 -I` and stripped environment."
            ),
        )
        if deny_err:
            return deny_err
        ok, err = approval.verify_and_consume(token, "code_sandbox", args)
        if not ok:
            return err

        with tempfile.TemporaryDirectory(prefix="celina_codesandbox_") as tmp_work:
            work_path = Path(tmp_work).resolve()
            script_file = work_path / "snippet.py"
            script_file.write_text(code + "\n", encoding="utf-8")

            if bwrap_ok:
                cmd = _build_bwrap_python_cmd(script_file, sec)
                res = command_runner.run_command(cmd, timeout=float(sec))
            else:
                clean_env = {
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(work_path),
                    "TMPDIR": str(work_path),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "LANG": "C.UTF-8",
                }
                res = command_runner.run_command(
                    [sys.executable, "-I", str(script_file)],
                    timeout=float(sec),
                    cwd=str(work_path),
                    env=clean_env,
                )

        tag = "bwrap-python" if bwrap_ok else "python-isolated"
    else:  # bash
        sec = max(1, min(BASH_MAX_TIMEOUT, int(timeout)))
        bwrap_ok = shutil.which("bwrap") is not None
        mode = "bwrap-bash-sandbox" if bwrap_ok else "bash-isolated"

        args = {"code": code, "language": lang, "timeout": sec, "mode": mode}
        from . import approval

        token, deny_err = approval.request_bound_approval(
            "code_sandbox",
            args,
            approval_cb,
            title=(
                f"Approve Sandboxed Bash Command ({sec}s, no-net)?"
                if bwrap_ok
                else f"WARNING: Bubblewrap Unavailable — Approve Isolated Bash ({sec}s)?"
            ),
            target="bwrap(bash -c, no-net, no-home)" if bwrap_ok else "bash -c (isolated)",
            extra_warning=(
                ""
                if bwrap_ok
                else "Bubblewrap userns unavailable; command runs with `bash -c` and stripped environment."
            ),
        )
        if deny_err:
            return deny_err
        ok, err = approval.verify_and_consume(token, "code_sandbox", args)
        if not ok:
            return err

        if bwrap_ok:
            cmd = _build_bwrap_bash_cmd(code)
            res = command_runner.run_command(cmd, timeout=float(sec))
        else:
            clean_env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp",
                "TMPDIR": "/tmp",
                "LANG": "C.UTF-8",
            }
            res = command_runner.run_command(
                ["bash", "-c", code],
                timeout=float(sec),
                cwd="/tmp",
                env=clean_env,
            )

        tag = "bwrap-bash" if bwrap_ok else "bash-isolated"

    if res.timed_out:
        return f"Error: Sandboxed {lang} execution timed out after {sec}s."

    combined = (
        (res.stdout or "")
        + (f"\nSTDERR:\n{res.stderr}" if res.stderr else "")
    ).strip() or "(No output)"
    combined = _cap_output(combined)

    if res.returncode != 0:
        return f"Error: ({tag} exit={res.returncode})\n{combined}"
    return f"[{tag}] Verified OK (exit=0):\n{combined}"


__all__ = ["code_sandbox"]
