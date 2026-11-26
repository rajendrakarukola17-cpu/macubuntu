"""
Celina v5 Autonomy — Self-Healing Execution Layer (`self_healing.py`)

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
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from .. import landlock_policy
from .checkpoint import FILE_MUTATION_TOOLS, PreExecutionCheckpoint


@dataclasses.dataclass
class ExecResult:
    success: bool
    exit_code: int
    summary: str
    backup_path: Optional[str] = None
    action_id: Optional[str] = None
    error: Optional[str] = None
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
        tool_handlers: Optional[dict[str, Callable[..., Any]]] = None,
        legato_enabled: bool = False,
        legato_client: Optional[Any] = None,
    ):
        self.data_dir = Path(data_dir)
        self.checkpoints = PreExecutionCheckpoint(self.data_dir / "undo")
        self.tool_handlers = dict(tool_handlers) if tool_handlers is not None else {}
        self._legato: Optional[Any] = legato_client

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
            target = arguments.get("path") or arguments.get("src") or arguments.get("file_path")
            snap = self.checkpoints.capture(task_id=task_id, tool_name=tool, target_path=target)

        backup_path = snap.backup_dir if snap else None
        action_id = snap.action_id if snap else None

        if domain in self.GUI_DOMAINS and self._legato is not None:
            return self._execute_gui(decision, backup_path, action_id)
        return self._execute_native(tool, arguments, backup_path, action_id)

    def _execute_gui(
        self,
        decision: dict[str, Any],
        backup_path: Optional[str],
        action_id: Optional[str],
    ) -> ExecResult:
        instruction = str(
            (decision.get("arguments") or {}).get("instruction")
            or (decision.get("arguments") or {}).get("goal")
            or decision.get("tool_name")
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
        workdir: Optional[Path] = None,
        timeout: float = 15.0,
    ) -> ExecResult:
        """
        Executes a subprocess tool command under kernel-enforced process policy
        (SHarness -> ai-jail/safeshell -> bwrap + RO .git + Landlock LSM).
        """
        prof = landlock_policy.profile_for_tool(tool_name, workdir=workdir)
        wrapped_cmd, driver, p_hash = landlock_policy.build_kernel_sandbox_cmd(
            cmd,
            prof,
            policy_dir=self.data_dir / "policies",
            workdir=workdir,
            protect_dot_git=True,
        )
        res = landlock_policy.run_under_landlock(
            wrapped_cmd,
            prof,
            strict=False,
            timeout=timeout,
            cwd=workdir,
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
        backup_path: Optional[str],
        action_id: Optional[str],
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
            return ExecResult(
                success=True,
                exit_code=0,
                summary=str(out)[:500],
                backup_path=backup_path,
                action_id=action_id,
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
