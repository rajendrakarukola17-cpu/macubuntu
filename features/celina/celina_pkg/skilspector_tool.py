"""
Celina AI (Celly) - SkillSpector Security Scanner (v4.5, Update 18)

SkillSpector — Security scanner for AI agent skills.
Detects vulnerabilities, malicious patterns, prompt injection, data exfiltration,
and supply-chain risks in Claude Code, Codex, and MCP skills before installation.

Source: https://github.com/NVIDIA/SkillSpector (and forks)
License: Varies by fork (Apache-2.0 / MIT)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("celina.skilspector")

# Risk severity levels
RISK_SEVERITY = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
RISK_RECOMMENDATION = ("SAFE", "CAUTION", "DO_NOT_INSTALL")


class SkillSpectorTool:
    """
    SkillSpector security scanner integration for Celina.

    Scans AI agent skills, MCP servers, and skill directories for
    vulnerabilities before they are installed or used. Acts as a
    runtime guardrail — skills must pass SkillSpector before Celina
    will load them.
    """

    def __init__(self, skilspector_path: Optional[str] = None):
        self.skilspector_path = skilspector_path or self._find_skilspector()
        self._installed = self.skilspector_path is not None

    @staticmethod
    def _find_skilspector() -> Optional[str]:
        """Find SkillSpector installation on the system."""
        candidates = [
            "/home/jtcviza/.local/share/skillspector",
            os.path.expanduser("~/.local/share/skillspector"),
            "/opt/skillspector",
            "/home/jtcviza/Downloads/skillspector",
        ]
        for path in candidates:
            if Path(path).exists():
                return path
        if shutil.which("skillspector"):
            return shutil.which("skillspector")
        return None

    @property
    def is_available(self) -> bool:
        """Check if SkillSpector is installed."""
        return self._installed

    def scan_skill(
        self,
        skill_path: str,
        *,
        llm_enabled: bool = False,
        format: str = "json",
        recursive: bool = False,
        max_skills: int = 32,
    ) -> dict[str, Any]:
        """
        Scan a skill directory or SKILL.md file for security risks.

        Parameters
        ----------
        skill_path : str
            Path to skill directory, SKILL.md file, or git repo URL.
        llm_enabled : bool
            Enable LLM-based semantic analysis (sends file contents to provider).
        format : str
            Output format: "json" or "sarif".
        recursive : bool
            Scan subdirectories recursively.
        max_skills : int
            Maximum skills to scan in recursive mode.

        Returns
        -------
        dict
            Scan results with risk_score, severity, recommendation, and issues.
        """
        if not self.is_available:
            return {
                "success": False,
                "error": "SkillSpector not installed. Clone from https://github.com/NVIDIA/SkillSpector",
                "skill_path": skill_path,
            }

        cmd = ["skillspector", "scan", skill_path, "--format", format]

        if recursive:
            cmd.extend(["--recursive", "--max-skills", str(max_skills)])

        if not llm_enabled:
            cmd.append("--no-llm")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "SKILLSPECTOR_NO_LLM": "1" if not llm_enabled else "0"},
            )

            if format == "json":
                try:
                    report = json.loads(result.stdout)
                except json.JSONDecodeError:
                    report = {"raw_output": result.stdout, "parse_error": True}

                return {
                    "success": result.returncode == 0,
                    "skill_path": skill_path,
                    "risk_score": report.get("risk_assessment", {}).get("score", 0),
                    "severity": report.get("risk_assessment", {}).get("severity", "UNKNOWN"),
                    "recommendation": report.get("risk_assessment", {}).get("recommendation", "UNKNOWN"),
                    "issues": report.get("issues", []),
                    "raw": report,
                    "exit_code": result.returncode,
                }
            else:
                return {
                    "success": result.returncode == 0,
                    "skill_path": skill_path,
                    "raw_output": result.stdout,
                    "exit_code": result.returncode,
                }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "SkillSpector scan timed out (120s)",
                "skill_path": skill_path,
            }
        except Exception as exc:
            log.exception("SkillSpector scan failed")
            return {
                "success": False,
                "error": str(exc),
                "skill_path": skill_path,
            }

    def scan_and_gate(
        self,
        skill_path: str,
        *,
        max_severity: str = "MEDIUM",
        fail_on_high: bool = True,
    ) -> dict[str, Any]:
        """
        Scan a skill and apply a security gate.

        If the skill's risk exceeds max_severity, installation is blocked.

        Parameters
        ----------
        skill_path : str
            Path to skill to scan.
        max_severity : str
            Maximum allowed severity ("LOW", "MEDIUM", "HIGH", "CRITICAL").
        fail_on_high : bool
            If True, block installation on HIGH or CRITICAL severity.

        Returns
        -------
        dict
            Gate result: {"allowed": bool, "reason": str, "scan": dict}
        """
        severity_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        max_level = severity_order.get(max_severity, 1)

        scan = self.scan_skill(skill_path, llm_enabled=False)
        if not scan.get("success"):
            return {
                "allowed": False,
                "reason": f"Scan failed: {scan.get('error', 'unknown error')}",
                "scan": scan,
            }

        severity = scan.get("severity", "UNKNOWN")
        skill_level = severity_order.get(severity, 0)
        recommendation = scan.get("recommendation", "UNKNOWN")

        allowed = True
        reason = "Skill passed security gate"

        if skill_level > max_level:
            allowed = False
            reason = f"Skill severity {severity} exceeds maximum allowed {max_severity}"

        if fail_on_high and severity in ("HIGH", "CRITICAL"):
            allowed = False
            reason = f"Skill blocked: {severity} severity — {recommendation}"

        if recommendation == "DO_NOT_INSTALL":
            allowed = False
            reason = f"Skill blocked: {recommendation}"

        return {
            "allowed": allowed,
            "reason": reason,
            "scan": scan,
        }

    def get_status(self) -> dict[str, Any]:
        """Return SkillSpector status information."""
        return {
            "available": self.is_available,
            "path": self.skilspector_path,
            "license": "Varies (Apache-2.0 / MIT forks)",
            "source": "https://github.com/NVIDIA/SkillSpector",
            "capabilities": [
                "Static security scanning of AI agent skills",
                "Vulnerability detection (prompt injection, data exfiltration)",
                "Supply-chain risk checking (OSV.dev integration)",
                "MCP server mode for IDE integration",
                "Risk scoring (LOW/MEDIUM/HIGH/CRITICAL)",
                "SARIF output for CI/CD pipelines",
                "Recursive batch scanning",
                "LLM-based semantic analysis (optional)",
            ],
            "risk_levels": list(RISK_SEVERITY),
            "recommendations": list(RISK_RECOMMENDATION),
        }


__all__ = ["SkillSpectorTool", "RISK_SEVERITY", "RISK_RECOMMENDATION"]
