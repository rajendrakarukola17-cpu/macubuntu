"""
Celina AI (Celly) - Google Artemis Bridge (v4.5, Update 18)

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
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

log = logging.get_logger = logging.getLogger("celina.artemis")


class ArtemisBridge:
    """
    Bridge to Google ARTEMIS for Android device automation.

    ARTEMIS turns natural-language instructions into reliable Android automation
    on real phones or emulators via ADB. Operates entirely on-device via ADB,
    with no data leaving the local environment.
    """

    def __init__(self, artemis_path: Optional[str] = None):
        self.artemis_path = artemis_path or self._find_artemis()
        self._server_proc: Optional[subprocess.Popen] = None
        self._mcp_port = 8000
        self._started = False

    @staticmethod
    def _find_artemis() -> Optional[str]:
        """Find the Artemis installation on the system."""
        candidates = [
            "/home/jtcviza/Downloads/artemis",
            os.path.expanduser("~/artemis"),
            os.path.expanduser("~/.local/share/artemis"),
            "/opt/artemis",
        ]
        for path in candidates:
            if Path(path).exists() and Path(path, "start.sh").exists():
                return path
        # Check if artemis is on PATH
        if shutil.which("artemis"):
            return shutil.which("artemis")
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
        """
        Start the Artemis server.

        profile: "flash" (fast, single-model) or "pro" (planner/operator/checker)
        """
        if not self.is_available:
            return False, (
                "Artemis not found. Install from https://github.com/google/artemis "
                "or clone to ~/artemis"
            )

        if self.is_running:
            return True, "Artemis server already running."

        try:
            start_script = Path(self.artemis_path) / "start.sh"
            if start_script.exists():
                self._server_proc = subprocess.Popen(
                    ["bash", str(start_script)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=self.artemis_path,
                    env={**os.environ, "ARTEMIS_PROFILE": profile},
                )
            else:
                self._server_proc = subprocess.Popen(
                    ["artemis", "server", "--profile", profile],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=self.artemis_path,
                    env={**os.environ, "ARTEMIS_PROFILE": profile},
                )

            time.sleep(3.0)

            if self._server_proc.poll() is not None:
                stderr = self._server_proc.stderr.read() if self._server_proc.stderr else ""
                return False, f"Artemis server exited: {stderr}"

            self._started = True
            return True, f"Artemis server started (profile: {profile})"

        except Exception as exc:
            log.exception("Failed to start Artemis")
            return False, f"Failed to start Artemis: {exc}"

    def stop(self) -> None:
        """Stop the Artemis server."""
        if self._server_proc and self.is_running:
            self._server_proc.terminate()
            try:
                self._server_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._server_proc.kill()
        self._server_proc = None
        self._started = False

    def run_task(
        self,
        instruction: str,
        *,
        profile: str = "flash",
        device_serial: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Run a natural-language task on an Android device via Artemis.

        instruction: Natural language task (e.g., "open Chrome and search weather")
        profile: "flash" (fast) or "pro" (planner/operator/checker)
        device_serial: ADB device serial number (optional, uses default if not specified)
        """
        if not self.is_available:
            return {
                "success": False,
                "error": "Artemis not installed. Clone from https://github.com/google/artemis",
                "instruction": instruction,
            }

        # Try MCP server first, fall back to CLI
        result = self._run_via_mcp(instruction, profile=profile, device_serial=device_serial)
        if result.get("success") is not False:
            return result

        # Fall back to CLI
        return self._run_via_cli(instruction, profile=profile, device_serial=device_serial)

    def _run_via_mcp(
        self,
        instruction: str,
        *,
        profile: str = "flash",
        device_serial: Optional[str] = None,
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
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return {
                    "success": "error" not in result,
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
        device_serial: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run task via Artemis CLI."""
        try:
            cmd = ["uv", "run", "artemis", "run", instruction, "--profile", profile]
            if device_serial:
                cmd.extend(["--device", device_serial])

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
