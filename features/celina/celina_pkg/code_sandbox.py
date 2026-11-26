"""Celina AI (Celly) - Sandboxed Code Execution Tool (v4.5, Update 17)

Open Interpreter inspired: runs Python snippets and Bash commands in isolated
subprocesses with Bubblewrap sandboxing, timeout caps, output limits, and
approval gating. All execution is local — no cloud calls, no secrets exposed.

Tools exposed:
  - code_sandbox(code, language, timeout, approval_cb)  -> run Python or Bash snippets

Safety design:
  - Python: Bubblewrap with --ro-bind /, --tmpfs /home, --unshare-net, --clearenv
  - Bash:   Bubblewrap with read-only /home, no network, 20s cap
  - Output: capped at 3000 chars, stderr captured separately
  - Catastrophic commands: blocked by shared denylist (checked BEFORE bwrap)
  - Network: always disabled in sandbox
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import approval, command_runner, config

try:
    from .runtime.tier_policy import CelinaTierPolicy
except Exception:  # pragma: no cover - runtime package import fallback
    CelinaTierPolicy = None  # type: ignore


def _systemd_run_available() -> bool:
    """Return True if we are on Linux with an active user systemd bus and
    `systemd-run` exists. Used to wrap bwrap under celina-sandbox.slice.
    """
    if sys.platform != "linux":
        return False
    if shutil.which("systemd-run") is None:
        return False
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    # Quick probe: does systemctl --user report running?
    try:
        res = command_runner.run_command(
            ["systemctl", "--user", "is-system-running"],
            timeout=2.0,
        )
        return res.ok and ("running" in res.stdout or "degraded" in res.stdout)
    except Exception:
        return False


def _build_systemd_run_bwrap_prefix(slice_name: str = "celina-sandbox.slice") -> list[str]:
    """Build a systemd-run prefix that places bwrap under the given user slice."""
    return [
        "systemd-run",
        "--user",
        "--quiet",
        "--scope",
        f"--slice={slice_name}",
        "--",
    ]


def _libkrun_available() -> bool:
    """Return True if a libkrun VM sandbox backend is installed and usable."""
    if sys.platform != "linux":
        return False
    return shutil.which("libkrun") is not None


def _build_libkrun_cmd(
    code: str,
    language: str,
    timeout: int,
    mem_mb: int = 512,
    vcpu: int = 1,
    systemd_prefix: list[str] | None = None,
) -> list[str]:
    """Build a libkrun VM sandbox command for the given snippet.

    Fail-closed: if libkrun is unavailable this function still returns a
    well-formed command, but the caller checks _libkrun_available() first.
    The VM runs with no network, a small memory ceiling, and a timeout.
    """
    prefix = systemd_prefix or []
    inline = f"exec {language} -c {code!r}" if language == "python" else f"exec bash -c {code!r}"
    cmd = list(prefix) + [
        "libkrun",
        "run",
        "--vm",
        f"--memory={mem_mb}",
        f"--vcpu={vcpu}",
        "--",
        "sh",
        "-c",
        inline,
    ]
    return cmd


# Output cap to prevent runaway responses
OUTPUT_CAP = 3000

# Timeout limits
PYTHON_MAX_TIMEOUT = 15
BASH_MAX_TIMEOUT = 20

# Catastrophic safety denylist — checked BEFORE any sandbox availability check.
# These patterns are blocked immediately on ALL systems, with or without bwrap.
CATASTROPHIC_DENYLIST = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    ":(){ :|:& };:",
    "dd if=/dev/zero",
    "> /dev/sda",
    "chmod -R 777 /",
    "chown -R root /",
    "mv / /dev/null",
    "format c:",
]


def _is_catastrophic(code: str, language: str) -> bool:
    """Return True if code matches any catastrophic denylist pattern."""
    code_lower = code.lower()
    for pattern in CATASTROPHIC_DENYLIST:
        if pattern.lower() in code_lower:
            return True
    return False

# If set, allows unsandboxed fallback (dangerous — local dev only).
# Bubblewrap is required for production sandboxed execution.
_ALLOW_UNSANDBOXED = os.environ.get("CELINA_ALLOW_UNSANDBOXED_CODE", "0") == "1"


def _cap_output(text: str) -> str:
    if len(text) <= OUTPUT_CAP:
        return text
    return text[:OUTPUT_CAP] + f"\n...[truncated at {OUTPUT_CAP} chars]"


# Minimal read-only runtime paths for bwrap (never --ro-bind / / which leaks /etc, /var, /opt)
_BWRAP_RO_RUNTIME_DIRS = ["/usr", "/bin", "/lib", "/lib64", "/etc/alternatives"]


def _bwrap_runtime_bind_args() -> list[str]:
    """Build --ro-bind args for minimal runtime directories only."""
    args: list[str] = []
    for sys_dir in _BWRAP_RO_RUNTIME_DIRS:
        if os.path.exists(sys_dir):
            args.extend(["--ro-bind", sys_dir, sys_dir])
    # Add sys.prefix if outside /usr (NixOS, etc.)
    import sys as _sys_mod

    if _sys_mod.prefix != "/usr" and os.path.exists(_sys_mod.prefix):
        args.extend(["--ro-bind", _sys_mod.prefix, _sys_mod.prefix])
    return args


def _build_bwrap_python_cmd(
    script_path: Path, timeout: int, systemd_prefix: list[str] | None = None, work_dir: Path | None = None,
) -> list[str]:
    """Build bwrap command for Python sandbox.

    `work_dir` is the caller-created TemporaryDirectory already containing the
    script file. We bind it at /tmp/work and chdir there — no host paths leak in.
    """
    sandbox_py = shutil.which("python3", path="/usr/bin:/bin") or "/usr/bin/python3"
    home_resolved = config.HOME.resolve() if hasattr(config, "HOME") else Path.home()
    work = work_dir if work_dir is not None else Path(tempfile.mkdtemp(prefix="celina_codesandbox_work_"))
    base_cmd = [
        "bwrap",
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-net",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/work",  # nosec B108 — bwrap sandbox env var, not temp file creation
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "LANG", "C.UTF-8",
        "--tmpfs", str(home_resolved),
        "--tmpfs", "/tmp",  # nosec B108 — bwrap sandbox mount point, not temp file creation
        "--tmpfs", "/run",
        "--dev", "/dev",
        "--proc", "/proc",
        "--bind", str(work.resolve()), "/tmp/work",  # nosec B108 — bwrap sandbox bind mount, not temp file creation
        "--chdir", "/tmp/work",  # nosec B108 — bwrap sandbox chdir target, not temp file creation
    ]
    base_cmd.extend(_bwrap_runtime_bind_args())
    base_cmd.extend(["--", sandbox_py, "-I", f"/tmp/work/{script_path.name}"])  # nosec B108 — bwrap sandbox script path, not temp file creation
    if systemd_prefix:
        return systemd_prefix + base_cmd
    return base_cmd


def _build_bwrap_bash_cmd(
    command: str, systemd_prefix: list[str] | None = None, work_dir: Path | None = None,
) -> list[str]:
    """Build bwrap command for Bash sandbox.

    `work_dir` is the caller-created working directory (mounted at /tmp/work).
    """
    sandbox_sh = shutil.which("bash", path="/usr/bin:/bin") or "/bin/bash"
    home_resolved = config.HOME.resolve() if hasattr(config, "HOME") else Path.home()
    work = work_dir if work_dir is not None else Path(tempfile.mkdtemp(prefix="celina_codesandbox_work_"))
    base_cmd = [
        "bwrap",
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-net",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/work",  # nosec B108 — bwrap sandbox env var, not temp file creation
        "--setenv", "TMPDIR", "/tmp/work",  # nosec B108 — bwrap sandbox env var, not temp file creation
        "--setenv", "LANG", "C.UTF-8",
        "--tmpfs", str(home_resolved),
        "--tmpfs", "/tmp",  # nosec B108 — bwrap sandbox mount point, not temp file creation
        "--tmpfs", "/run",
        "--dev", "/dev",
        "--proc", "/proc",
        "--bind", str(work.resolve()), "/tmp/work",  # nosec B108 — bwrap sandbox bind mount, not temp file creation
        "--chdir", "/tmp/work",  # nosec B108 — bwrap sandbox chdir target, not temp file creation
    ]
    base_cmd.extend(_bwrap_runtime_bind_args())
    base_cmd.extend(["--", sandbox_sh, "-c", command])
    if systemd_prefix:
        return systemd_prefix + base_cmd
    return base_cmd


def code_sandbox(
    code: str,
    *,
    language: str = "python",
    timeout: int = 5,
    approval_cb: Any | None = None,
    tier_policy: Any = None,
) -> str:
    """Run a Python or Bash snippet in an isolated sandbox (Open Interpreter style).

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
    tier_policy : CelinaTierPolicy, optional
        Hardware-adaptive policy. When enable_libkrun_sandbox is true and a
        libkrun backend is available, execution uses a libkrun VM sandbox
        instead of bwrap. Defaults to fail-closed (None).

    Returns
    -------
    str
        Stdout/stderr combined, capped at 3000 chars, with an exit status tag.

    Safety
    ------
    - Catastrophic commands blocked by denylist (checked BEFORE bwrap / libkrun)
    - Bubblewrap sandbox with read-only host, no network, stripped env
    - Timeout enforced by subprocess layer
    - Output capped to prevent runaway responses

    """
    enable_libkrun = bool(getattr(tier_policy, "enable_libkrun_sandbox", False)) if tier_policy is not None else False
    code = (code or "").strip()
    if not code:
        return "Error: Empty code snippet."
    if len(code) > 16384:
        return "Error: Code snippet exceeds 16 KB sandbox limit."

    # Catastrophic safety denylist — checked BEFORE bwrap availability.
    # Blocks rm -rf /, mkfs, shutdown, etc. on ALL systems.
    if _is_catastrophic(code, language):
        return "Blocked: Code matched catastrophic safety denylist."

    if approval_cb is None:
        return "Denied: Sandboxed code execution requires interactive user approval."

    lang = (language or "python").strip().lower()
    if lang not in ("python", "bash"):
        return f"Error: Unsupported language '{language}'. Use 'python' or 'bash'."

    # systemd-run prefix: place bwrap under celina-sandbox.slice when an active
    # user systemd bus is available; fall back to plain bwrap otherwise.
    systemd_prefix: list[str] | None = None
    if _systemd_run_available():
        systemd_prefix = _build_systemd_run_bwrap_prefix()

    if lang == "python":
        sec = max(1, min(PYTHON_MAX_TIMEOUT, int(timeout)))
        bwrap_ok = shutil.which("bwrap") is not None
        libkrun_ok = enable_libkrun and _libkrun_available()
        mode = (
            "libkrun-python-vm"
            if libkrun_ok
            else ("bwrap-python-sandbox" if bwrap_ok else "python-isolated")
        )

        args = {"code": code, "language": lang, "timeout": sec, "mode": mode}

        token, deny_err = approval.request_bound_approval(
            "code_sandbox",
            args,
            approval_cb,
            title=(
                f"Approve Sandboxed Python Execution ({sec}s, no-net)?"
                if not libkrun_ok
                else f"Approve libkrun VM Sandboxed Python Execution ({sec}s, no-net)?"
            ),
            target=(
                "libkrun(vm, python3 -I, no-net, no-home)"
                if libkrun_ok
                else ("bwrap(python3 -I, no-net, no-home)" if bwrap_ok else "python3 -I (isolated)")
            ),
            extra_warning=(
                "libkrun VM sandbox: isolated kernel, no network, memory-capped."
                if libkrun_ok
                else (
                    ""
                    if bwrap_ok
                    else "Bubblewrap userns unavailable; script runs with `python3 -I` and stripped environment."
                )
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

            if libkrun_ok:
                res = command_runner.run_command(
                    _build_libkrun_cmd(code, lang, sec, systemd_prefix=systemd_prefix),
                    timeout=float(sec),
                )
            elif bwrap_ok:
                cmd = _build_bwrap_python_cmd(script_file, sec, systemd_prefix=systemd_prefix, work_dir=work_path)
                res = command_runner.run_command(cmd, timeout=float(sec))
            elif _ALLOW_UNSANDBOXED:
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
            else:
                res = command_runner.CommandResult(
                    returncode=1,
                    stdout="",
                    stderr=(
                        "Error: Bubblewrap (bwrap) is not installed. "
                        "Sandboxed code execution is disabled for security. "
                        "Install bwrap (Module 01) or set CELINA_ALLOW_UNSANDBOXED_CODE=1 "
                        "for local dev only."
                    ),
                    timed_out=False,
                )

        tag = (
            "libkrun-python-vm"
            if libkrun_ok
            else ("bwrap-python" if bwrap_ok else ("python-isolated-unsandboxed" if _ALLOW_UNSANDBOXED else "bwrap-unavailable"))
        )
    else:  # bash
        sec = max(1, min(BASH_MAX_TIMEOUT, int(timeout)))
        bwrap_ok = shutil.which("bwrap") is not None
        libkrun_ok = enable_libkrun and _libkrun_available()
        mode = (
            "libkrun-bash-vm"
            if libkrun_ok
            else ("bwrap-bash-sandbox" if bwrap_ok else "bash-isolated")
        )

        args = {"code": code, "language": lang, "timeout": sec, "mode": mode}

        token, deny_err = approval.request_bound_approval(
            "code_sandbox",
            args,
            approval_cb,
            title=(
                f"Approve Sandboxed Bash Command ({sec}s, no-net)?"
                if not libkrun_ok
                else f"Approve libkrun VM Sandboxed Bash Command ({sec}s, no-net)?"
            ),
            target=(
                "libkrun(vm, bash -c, no-net, no-home)"
                if libkrun_ok
                else ("bwrap(bash -c, no-net, no-home)" if bwrap_ok else "bash -c (isolated)")
            ),
            extra_warning=(
                "libkrun VM sandbox: isolated kernel, no network, memory-capped."
                if libkrun_ok
                else (
                    ""
                    if bwrap_ok
                    else "Bubblewrap userns unavailable; command runs with `bash -c` and stripped environment."
                )
            ),
        )
        if deny_err:
            return deny_err
        ok, err = approval.verify_and_consume(token, "code_sandbox", args)
        if not ok:
            return err

        if libkrun_ok:
            res = command_runner.run_command(
                _build_libkrun_cmd(code, lang, sec, systemd_prefix=systemd_prefix),
                timeout=float(sec),
            )
        elif bwrap_ok:
            with tempfile.TemporaryDirectory(prefix="celina_codesandbox_bash_") as bash_work:
                cmd = _build_bwrap_bash_cmd(code, systemd_prefix=systemd_prefix, work_dir=Path(bash_work))
            res = command_runner.run_command(cmd, timeout=float(sec))
        elif _ALLOW_UNSANDBOXED:
            clean_env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp",  # nosec B108 — env var for unsandboxed bash, not temp file creation
                "TMPDIR": "/tmp",  # nosec B108 — env var for unsandboxed bash, not temp file creation
                "LANG": "C.UTF-8",
            }
            res = command_runner.run_command(
                ["bash", "-c", code],
                timeout=float(sec),
                cwd="/tmp",  # nosec B108 — working dir for unsandboxed bash, not temp file creation
                env=clean_env,
            )
        else:
            res = command_runner.CommandResult(
                returncode=1,
                stdout="",
                stderr=(
                    "Error: Bubblewrap (bwrap) is not installed. "
                    "Sandboxed code execution is disabled for security. "
                    "Install bwrap (Module 01) or set CELINA_ALLOW_UNSANDBOXED_CODE=1 "
                    "for local dev only."
                ),
                timed_out=False,
            )

        tag = (
            "libkrun-bash-vm"
            if libkrun_ok
            else ("bwrap-bash" if bwrap_ok else ("bash-isolated-unsandboxed" if _ALLOW_UNSANDBOXED else "bwrap-unavailable"))
        )

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
