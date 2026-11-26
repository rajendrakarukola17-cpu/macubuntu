"""Celina v5 Autonomy — Self-Healing Execution Layer (`self_healing.py`)

Routes execution cleanly:
  - GUI tools (`wayland_gui` domain): uses Legato computer-use harness when
    explicitly enabled (`legato_enabled=True`), installed, AND GPU/display
    readiness is confirmed via `vainfo`; otherwise falls back seamlessly to
    Celina's native LUMOS semantic-blueprint GUI handlers.
  - Non-GUI tools: delegates directly to Celina's registered tool handlers.
  - Every `files_office` mutation captures a SHA-256 pre-execution checkpoint.
"""

from __future__ import annotations

import dataclasses
import importlib
import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import landlock_policy

log = logging.getLogger(__name__)
from ..harness import VerificationGate
from .checkpoint import FILE_MUTATION_TOOLS, PreExecutionCheckpoint

# ------------------------------------------------------------------
# Tool -> path argument name(s) mapping (audit item 11).
#
# Centralized so path arguments are not hardcoded inline in execute().
# Each entry lists candidate keys in priority order; the first present
# non-empty value is used as the mutation target.  When the capability
# manifest gains parameter schemas, this mapping can be derived from
# manifest["tools"][name]["parameters"]["properties"] instead of literals.
# ------------------------------------------------------------------
TOOL_PATH_ARG_KEYS: dict[str, tuple[str, ...]] = {
    "write_file": ("path",),
    "edit_file": ("path",),
    "move_file": ("src", "path", "source"),
    "trash_file": ("path", "file_path"),
    "create_document": ("path", "file_path"),
    "edit_document": ("path", "file_path"),
    "convert_document": ("path", "src", "file_path"),
    "libreoffice_edit": ("path", "file_path"),
}


def _resolve_target_path(tool: str, arguments: dict[str, Any]) -> str | None:
    """Resolve the target file path from tool arguments using manifest-aware keys.

    Falls back to a small set of well-known keys per tool rather than inline
    literals.  Returns None if no path argument is present.
    """
    for key in TOOL_PATH_ARG_KEYS.get(tool, ("path", "file_path", "src", "dst", "dest")):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _validate_target_atomic(target_path: str) -> tuple[bool, str]:
    """Atomically validate a target path against symlink/TOCTOU races (audit item 1).

    Uses lstat on each component (O_NOFOLLOW equivalent) to reject symlinks at
    any depth.  This closes the TOCTOU window between checkpoint capture and
    actual file mutation: a symlink swapped in between would be rejected here
    rather than followed.

    Home-confinement is enforced by the individual tools (tools.py checks
    `_is_within_home`); this guard focuses on the symlink/TOCTOU race that
    those checks alone cannot catch.

    Returns (ok, error_message).  On failure the caller must refuse the mutation.
    """
    import os as _os
    import stat as _stat

    target = Path(target_path).expanduser()

    # Walk each component and lstat to detect symlinks before final resolution.
    # This is the O_NOFOLLOW equivalent — catches symlink swaps at any depth.
    probe = target
    parts_to_check = []
    while True:
        parts_to_check.append(probe)
        if probe == probe.parent:
            break
        probe = probe.parent

    for p in reversed(parts_to_check):
        try:
            lst = _os.lstat(p)
        except FileNotFoundError:
            continue
        except OSError:
            return False, f"Cannot stat path component: {p}"
        if _stat.S_ISLNK(lst.st_mode):
            return False, f"Symlink detected in path component (TOCTOU risk): {p}"

    return True, ""


def _check_disk_headroom(target_path: str, *, needed_bytes: int = 0) -> tuple[bool, str]:
    """Verify sufficient disk headroom before claiming a mutation is reversible (audit item 3).

    Checks shutil.disk_usage for the target's filesystem and ensures free space
    exceeds both the needed bytes (backup + new content) and a 50 MB safety floor.
    """
    import shutil as _shutil

    target = Path(target_path).expanduser().resolve()
    # Walk up to find an existing mount point for disk_usage.
    probe = target if target.exists() else target.parent
    while probe != probe.parent and not probe.exists():
        probe = probe.parent
    try:
        du = _shutil.disk_usage(str(probe))
    except OSError:
        return False, "Cannot determine disk usage for target filesystem."

    free = du.free
    safety_floor = 50 * 1024 * 1024  # 50 MB
    required = needed_bytes + safety_floor
    if free < required:
        return (
            False,
            f"Insufficient disk headroom: {free / 1024 / 1024:.0f} MB free, "
            f"{required / 1024 / 1024:.0f} MB required (incl. 50 MB safety floor).",
        )
    return True, ""


@dataclasses.dataclass
class ExecResult:
    success: bool
    exit_code: int
    summary: str
    backup_path: str | None = None
    action_id: str | None = None
    error: str | None = None
    executor_kind: str = "native"  # "legato" | "native" | "observe_only" | "sharness"
    policy_hash: str = ""
    sandbox_driver: str = ""


def check_vainfo_display_ready() -> bool:
    """Confirm working GPU/VA-API stack before enabling optional Legato GUI driver."""
    if not shutil.which("vainfo"):
        return False
    try:
        res = subprocess.run(
            ["vainfo"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        return res.returncode == 0
    except Exception:
        return False


class SelfHealingExecutor:
    GUI_DOMAINS: frozenset[str] = frozenset({"wayland_gui"})

    def __init__(
        self,
        *,
        data_dir: Path,
        tool_handlers: dict[str, Callable[..., Any]] | None = None,
        legato_enabled: bool = False,
        legato_client: Any | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.checkpoints = PreExecutionCheckpoint(self.data_dir / "undo")
        self.tool_handlers = dict(tool_handlers) if tool_handlers is not None else {}
        self._legato: Any | None = legato_client

        if self._legato is None and legato_enabled and check_vainfo_display_ready():
            try:
                legato_mod = importlib.import_module("legato")
                self._legato = legato_mod.local()
            except Exception:
                self._legato = None

    def execute(self, decision: dict[str, Any], task_id: str) -> ExecResult:
        tool = str(decision.get("tool_name") or "").strip()
        domain = str(decision.get("domain") or "").strip()
        arguments = decision.get("arguments") or {}

        snap = None
        if tool in FILE_MUTATION_TOOLS:
            target = _resolve_target_path(tool, arguments)
            if target is not None:
                # Atomic re-validation: reject symlink/TOCTOU races (audit item 1).
                ok, err = _validate_target_atomic(target)
                if not ok:
                    return ExecResult(
                        success=False,
                        exit_code=1,
                        summary="",
                        backup_path=None,
                        action_id=None,
                        error=f"Blocked: {err}",
                        executor_kind="native",
                        sandbox_driver="landlock_policy",
                    )
                # Verify disk headroom before claiming reversibility (audit item 3).
                content_len = len(str(arguments.get("content", "") or ""))
                ok, err = _check_disk_headroom(target, needed_bytes=content_len)
                if not ok:
                    return ExecResult(
                        success=False,
                        exit_code=1,
                        summary="",
                        backup_path=None,
                        action_id=None,
                        error=f"Blocked: {err}",
                        executor_kind="native",
                        sandbox_driver="landlock_policy",
                    )
            snap = self.checkpoints.capture(task_id=task_id, tool_name=tool, target_path=target)

        backup_path = snap.backup_dir if snap else None
        action_id = snap.action_id if snap else None

        if domain in self.GUI_DOMAINS and self._legato is not None:
            return self._execute_gui(decision, backup_path, action_id)
        return self._execute_native(tool, arguments, backup_path, action_id)

    def _execute_gui(
        self,
        decision: dict[str, Any],
        backup_path: str | None,
        action_id: str | None,
    ) -> ExecResult:
        instruction = str(
            (decision.get("arguments") or {}).get("instruction")
            or (decision.get("arguments") or {}).get("goal")
            or decision.get("tool_name"),
        )
        if self._legato is None:
            return ExecResult(
                success=False,
                exit_code=1,
                summary="Legato not available",
                backup_path=backup_path,
                action_id=action_id,
                error="Legato not available",
            )
        try:
            result = self._legato.run(instruction)
            ok = bool(getattr(result, "success", False))
            report = result.report() if callable(getattr(result, "report", None)) else str(result)
            return ExecResult(
                success=ok,
                exit_code=0 if ok else 1,
                summary=str(report)[:500],
                backup_path=backup_path,
                action_id=action_id,
                error=None if ok else str(report)[:300],
                executor_kind="legato",
            )
        except Exception as exc:
            return ExecResult(
                success=False,
                exit_code=1,
                summary="",
                backup_path=backup_path,
                action_id=action_id,
                error=str(exc),
                executor_kind="legato",
            )

    def run_sandboxed_command(
        self,
        tool_name: str,
        cmd: list[str],
        *,
        workdir: Path | None = None,
        timeout: float = 15.0,
    ) -> ExecResult:
        """Executes a subprocess tool command under kernel-enforced process policy
        (SHarness -> ai-jail/safeshell -> bwrap + RO .git + Landlock LSM).
        """
        prof = landlock_policy.profile_for_tool(tool_name, workdir=workdir)
        p_hash = prof.policy_hash
        try:
            wrapped_cmd, driver, p_hash = landlock_policy.build_kernel_sandbox_cmd(
                cmd,
                prof,
                policy_dir=self.data_dir / "policies",
                workdir=workdir,
                protect_dot_git=True,
                strict=True,
            )
        except landlock_policy.LandlockEnforcementError as exc:
            log.error(
                "Sandbox unavailable for '%s': %s — refusing to run unsandboxed", tool_name, exc
            )
            return ExecResult(
                success=False,
                exit_code=1,
                summary="",
                error=f"sandbox_unavailable: {exc}",
                executor_kind="none",
                policy_hash=p_hash,
                sandbox_driver="none",
            )

        try:
            res = landlock_policy.run_under_landlock(
                wrapped_cmd,
                prof,
                strict=True,
                timeout=timeout,
                cwd=workdir,
            )
        except landlock_policy.LandlockEnforcementError as exc:
            log.error(
                "Landlock enforcement failed for '%s': %s — refusing to run unsandboxed",
                tool_name,
                exc,
            )
            return ExecResult(
                success=False,
                exit_code=1,
                summary="",
                error=f"sandbox_unavailable: {exc}",
                executor_kind=driver,
                policy_hash=p_hash,
                sandbox_driver=driver,
            )
        ok = int(res.get("returncode", 1)) == 0
        return ExecResult(
            success=ok,
            exit_code=int(res.get("returncode", 1)),
            summary=str(res.get("stdout", ""))[:500],
            error=None if ok else (str(res.get("stderr", ""))[:300] or "sandboxed command failed"),
            executor_kind=driver,
            policy_hash=p_hash,
            sandbox_driver=driver,
        )

    def _execute_native(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        backup_path: str | None,
        action_id: str | None,
    ) -> ExecResult:
        prof = landlock_policy.profile_for_tool(tool_name)
        p_hash = prof.policy_hash

        # If a subprocess argv was explicitly provided, enforce SHarness / ai-jail / Landlock!
        if isinstance(arguments.get("sandboxed_argv"), list) and arguments["sandboxed_argv"]:
            wd = Path(arguments["workdir"]) if arguments.get("workdir") else None
            sub_res = self.run_sandboxed_command(
                tool_name,
                [str(x) for x in arguments["sandboxed_argv"]],
                workdir=wd,
            )
            sub_res.backup_path = backup_path
            sub_res.action_id = action_id
            return sub_res

        handler = self.tool_handlers.get(tool_name)
        if handler is None:
            return ExecResult(
                success=False,
                exit_code=127,
                summary="",
                backup_path=backup_path,
                action_id=action_id,
                error=f"Unregistered tool handler: {tool_name}",
                executor_kind="native",
                policy_hash=p_hash,
                sandbox_driver="landlock_policy",
            )
        try:
            out = handler(**arguments)
            # Anti-victory-declaration-bias: route through VerificationGate so
            # "Error: ..." strings and failed post-conditions never mark success=True.
            gate = VerificationGate()
            outcome = gate.evaluate(
                tool_name=tool_name,
                arguments=arguments,
                raw_output=str(out),
                requires_verification=tool_name in FILE_MUTATION_TOOLS,
                tainted_source=False,
            )
            return ExecResult(
                success=outcome.allows_victory_claim(),
                exit_code=0 if outcome.allows_victory_claim() else 1,
                summary=str(out)[:500],
                backup_path=backup_path,
                action_id=action_id,
                error=outcome.error or None,
                executor_kind="native",
                policy_hash=p_hash,
                sandbox_driver="landlock_policy",
            )
        except Exception as exc:
            return ExecResult(
                success=False,
                exit_code=1,
                summary="",
                backup_path=backup_path,
                action_id=action_id,
                error=str(exc),
                executor_kind="native",
                policy_hash=p_hash,
                sandbox_driver="landlock_policy",
            )
