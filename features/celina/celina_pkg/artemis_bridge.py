"""Celina AI (Celly) - Google Artemis Bridge (v4.5, Update 18)

Integrates Google ARTEMIS (Apache 2.0) for Android device automation:
- Natural-language task execution on real Android phones or emulators via ADB
- Flash profile: single-model observe-and-act loop (~3-5 seconds per step)
- Pro profile: Planner, Operator, Checker agents with living task plan
- Element locating via accessibility hierarchy, OCR, and visual fallbacks
- MCP server for Claude Code, Codex, Antigravity, Cursor integration
- 99%+ AndroidWorld benchmark task completion rate

Source: https://github.com/google/artemis
License: Apache-2.0
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from . import approval, evidence

log = logging.getLogger("celina.artemis")


class ArtemisBridge:
    """Bridge to Google ARTEMIS for Android device automation.

    ARTEMIS turns natural-language instructions into reliable Android automation
    on real phones or emulators via ADB. Operates entirely on-device via ADB,
    with no data leaving the local environment.

    run_task() is gated behind Tier-2 approval via approval.request_bound_approval
    + approval.verify_and_consume (fail-closed). Without an approval callback,
    all mutating Android actions are denied.
    """

    def __init__(self, artemis_path: str | None = None):
        self.artemis_path = artemis_path or self._find_artemis()
        self._server_proc: subprocess.Popen | None = None
        self._mcp_port = 8000
        self._started = False

    @staticmethod
    def _find_artemis() -> str | None:
        """Find the Artemis installation on the system.

        Returns a directory path (containing start.sh), never a bare binary path,
        so downstream consumers can safely do Path(self.artemis_path) / "start.sh".
        """
        candidates = [
            os.path.expanduser("~/artemis"),
            os.path.expanduser("~/.local/share/artemis"),
            "/opt/artemis",
        ]
        for path in candidates:
            if Path(path).is_dir() and Path(path, "start.sh").exists():
                return path
        # Check if artemis is on PATH — return its parent directory as a fallback
        binary = shutil.which("artemis")
        if binary:
            return str(Path(binary).resolve().parent)
        return None

    @property
    def is_available(self) -> bool:
        """Check if Artemis is installed and available."""
        return self.artemis_path is not None

    @property
    def is_running(self) -> bool:
        """Check if the Artemis MCP server is running."""
        return self._server_proc is not None and self._server_proc.poll() is None

    def start(self, profile: str = "flash") -> tuple[bool, str]:
        """Start the Artemis server.

        profile: "flash" (fast, single-model) or "pro" (planner/operator/checker)
        """
        if not self.is_available:
            return False, (
                "Artemis not found. Install from https://github.com/google/artemis "
                "or clone to ~/artemis"
            )

        if self.is_running:
            return True, "Artemis server already running."

        # artemis_path must be a directory (contains start.sh)
        if not Path(self.artemis_path).is_dir():
            return False, (
                f"Artemis path is not a directory: {self.artemis_path}. "
                "Expected a directory containing start.sh."
            )

        try:
            start_script = Path(self.artemis_path) / "start.sh"
            if start_script.exists():
                self._server_proc = subprocess.Popen(
                    ["bash", str(start_script)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=self.artemis_path,
                    start_new_session=True,
                    env={**os.environ, "ARTEMIS_PROFILE": profile},
                )
            else:
                self._server_proc = subprocess.Popen(
                    ["artemis", "server", "--profile", profile],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=self.artemis_path,
                    start_new_session=True,
                    env={**os.environ, "ARTEMIS_PROFILE": profile},
                )

            time.sleep(3.0)

            if self._server_proc.poll() is not None:
                stderr = self._server_proc.stderr.read() if self._server_proc.stderr else ""
                return False, f"Artemis server exited: {stderr}"

            self._started = True
            return True, f"Artemis server started (profile: {profile})"

        except Exception as exc:
            if self._server_proc is not None:
                try:
                    self._server_proc.kill()
                    self._server_proc.wait(timeout=2.0)
                except Exception:
                    pass
                self._server_proc = None
            log.exception("Failed to start Artemis")
            return False, f"Failed to start Artemis: {exc}"

    def stop(self) -> None:
        """Stop the Artemis server and its entire process group.

        Uses start_new_session=True in Popen so the Artemis server runs in its
        own process group; killing the group ensures grandchild processes
        (e.g. the actual server spawned by bash start.sh) are also terminated,
        preventing port 8000 from staying bound.
        """
        if self._server_proc and self.is_running:
            try:
                os.killpg(os.getpgid(self._server_proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                # Fall back to terminate/kill on the direct child
                try:
                    self._server_proc.terminate()
                except Exception:
                    pass
            try:
                self._server_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self._server_proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except Exception:
                    try:
                        self._server_proc.kill()
                    except Exception:
                        pass
                try:
                    self._server_proc.wait(timeout=2.0)
                except Exception:
                    pass
        self._server_proc = None
        self._started = False

    def run_task(
        self,
        instruction: str,
        *,
        profile: str = "flash",
        device_serial: str | None = None,
        approval_cb: approval.ApprovalCallback | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Run a natural-language task on an Android device via Artemis.

        instruction: Natural language task (e.g., "open Chrome and search weather")
        profile: "flash" (fast) or "pro" (planner/operator/checker)
        device_serial: ADB device serial number (optional, uses default if not specified)
        approval_cb: Interactive Tier-2 approval callback (required for mutating actions)
        dry_run: If True, return success without executing

        All mutating Android actions (install APK, send SMS, place calls, delete data,
        change settings) require Tier-2 approval via approval.request_bound_approval +
        approval.verify_and_consume. Without an approval callback, the call is denied.
        """
        if not self.is_available:
            return {
                "success": False,
                "error": "Artemis not installed. Clone from https://github.com/google/artemis",
                "instruction": instruction,
            }

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "instruction": instruction,
                "profile": profile,
                "method": "dry_run",
            }

        if approval_cb is None:
            return {
                "success": False,
                "error": "Denied: Artemis tasks require interactive Tier-2 approval.",
                "instruction": instruction,
                "profile": profile,
                "method": "denied",
            }

        safe_args = {
            "instruction": instruction,
            "profile": profile,
            "device_serial": device_serial,
        }
        tok, deny_err = approval.request_bound_approval(
            "artemis_run_task",
            safe_args,
            approval_cb,
            title=f"Approve Android automation ({profile}): '{instruction[:80]}'?",
            target=f"adb://{device_serial or 'default'}",
        )
        if deny_err:
            evidence.EvidenceLedger().record_execution(
                tool_name="artemis_run_task",
                arguments=safe_args,
                result_text=deny_err[:240],
                policy_decision="denied",
            )
            return {
                "success": False,
                "error": deny_err,
                "instruction": instruction,
                "profile": profile,
                "method": "denied",
            }
        ok, err = approval.verify_and_consume(tok, "artemis_run_task", safe_args)
        if not ok:
            evidence.EvidenceLedger().record_execution(
                tool_name="artemis_run_task",
                arguments=safe_args,
                result_text="Denied: " + err[:240],
                policy_decision="denied",
            )
            return {
                "success": False,
                "error": err,
                "instruction": instruction,
                "profile": profile,
                "method": "denied",
            }

        # Try MCP server first, fall back to CLI
        result = self._run_via_mcp(instruction, profile=profile, device_serial=device_serial)
        if result.get("success") is not False:
            evidence.EvidenceLedger().record_execution(
                tool_name="artemis_run_task",
                arguments=safe_args,
                result_text=f"Artemis task executed via MCP (profile={profile})",
                policy_decision="ok",
            )
            return result

        # Fall back to CLI
        result = self._run_via_cli(instruction, profile=profile, device_serial=device_serial)
        if result.get("success"):
            evidence.EvidenceLedger().record_execution(
                tool_name="artemis_run_task",
                arguments=safe_args,
                result_text=f"Artemis task executed via CLI (profile={profile})",
                policy_decision="ok",
            )
        else:
            evidence.EvidenceLedger().record_execution(
                tool_name="artemis_run_task",
                arguments=safe_args,
                result_text=result.get("error", "")[:240],
                policy_decision="error",
            )
        return result

    def _run_via_mcp(
        self,
        instruction: str,
        *,
        profile: str = "flash",
        device_serial: str | None = None,
    ) -> dict[str, Any]:
        """Run task via Artemis MCP server."""
        try:
            import urllib.request

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "automation/run",
                "params": {
                    "instruction": instruction,
                    "profile": profile,
                    "device_serial": device_serial,
                },
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"http://localhost:{self._mcp_port}/mcp",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60.0) as resp:  # nosec B310 — localhost MCP endpoint, scheme fixed to http
                result = json.loads(resp.read().decode("utf-8"))
                return {
                    "success": result.get("error") is None,
                    "result": result.get("result"),
                    "error": result.get("error"),
                    "instruction": instruction,
                    "profile": profile,
                    "method": "mcp",
                }
        except Exception as exc:
            log.debug("MCP call failed, will try CLI: %s", exc)
            return {"success": False, "error": str(exc), "instruction": instruction}

    def _run_via_cli(
        self,
        instruction: str,
        *,
        profile: str = "flash",
        device_serial: str | None = None,
    ) -> dict[str, Any]:
        """Run task via Artemis CLI.

        Prefers `uv run artemis` if uv is available; falls back to the bare
        `artemis` binary on PATH. Never raises FileNotFoundError.
        """
        if shutil.which("uv"):
            cmd = ["uv", "run", "artemis", "run", instruction, "--profile", profile]
        elif shutil.which("artemis"):
            cmd = ["artemis", "run", instruction, "--profile", profile]
        else:
            return {
                "success": False,
                "error": "Neither 'uv' nor 'artemis' is on PATH. Cannot run Artemis CLI.",
                "instruction": instruction,
                "profile": profile,
                "method": "cli",
            }

        if device_serial:
            cmd.extend(["--device", device_serial])

        try:
            result = subprocess.run(
                cmd,
                cwd=self.artemis_path,
                capture_output=True,
                text=True,
                timeout=120.0,
                env={**os.environ, "ARTEMIS_PROFILE": profile},
            )

            return {
                "success": result.returncode == 0,
                "result": result.stdout.strip() if result.returncode == 0 else None,
                "error": result.stderr.strip() if result.returncode != 0 else None,
                "instruction": instruction,
                "profile": profile,
                "method": "cli",
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Artemis task timed out (120s)",
                "instruction": instruction,
                "profile": profile,
                "method": "cli",
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "instruction": instruction,
                "profile": profile,
                "method": "cli",
            }

    def get_status(self) -> dict[str, Any]:
        """Get Artemis server status."""
        return {
            "available": self.is_available,
            "running": self.is_running,
            "path": self.artemis_path,
            "mcp_port": self._mcp_port,
            "license": "Apache-2.0",
            "source": "https://github.com/google/artemis",
            "capabilities": [
                "Android device automation via ADB",
                "Flash profile: observe-and-act (~3-5s/step)",
                "Pro profile: Planner/Operator/Checker agents",
                "Accessibility hierarchy + OCR + visual element locating",
                "MCP server for IDE integration (Claude Code, Codex, Cursor)",
                "99%+ AndroidWorld benchmark completion",
            ],
        }


__all__ = ["ArtemisBridge"]
