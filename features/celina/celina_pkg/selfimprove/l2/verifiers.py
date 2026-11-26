# Copyright 2026 Celina contributors.
"""External verifiers with explicit feature, subprocess, and time budgets."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.budget import BudgetExceeded
from celina_pkg.selfimprove.contracts import VerifierResult

from .runner import CommandExecutor, RawCommandOutput, run_allowlisted_command

if TYPE_CHECKING:
    from pathlib import Path

    from celina_pkg.selfimprove.budget import BudgetGuard
    from celina_pkg.selfimprove.contracts import TaskSpec
    from celina_pkg.selfimprove.flags import SelfImproveFlags


class _BaseVerifier:
    tool = ""
    arguments: tuple[str, ...] = ()

    def __init__(  # noqa: PLR0913  # Explicit verifier configuration and executor boundary.
        self,
        flags: SelfImproveFlags,
        budget: BudgetGuard,
        cwd: Path,
        *,
        workspace_root: Path | None = None,
        timeout: float = 30.0,
        executor: CommandExecutor | None = None,
    ) -> None:
        self.flags = flags
        self.budget = budget
        self.cwd = cwd
        self.workspace_root = workspace_root
        self.timeout = timeout
        self.executor = executor

    def verify(self, task: TaskSpec) -> VerifierResult:  # noqa: ARG002  # Required L0 protocol.
        """Execute the external verifier or return a fail-closed result."""
        if not self.flags.enabled or not self.flags.verifiers:
            return VerifierResult(self.tool, "error", 0.0, "verifiers disabled")
        self.budget.consume_subprocess()
        try:
            raw = run_allowlisted_command(
                self.tool,
                self.arguments,
                cwd=self.cwd,
                timeout=self.timeout,
                workspace_root=self.workspace_root,
                executor=self.executor,
            )
            self.budget.check_wall_clock()
        except BudgetExceeded:
            raise
        except FileNotFoundError:
            self.budget.check_wall_clock()
            return VerifierResult(self.tool, "error", 0.0, "missing executable")
        except Exception as exc:
            self.budget.check_wall_clock()
            return VerifierResult(self.tool, "error", 0.0, f"runner error: {type(exc).__name__}")
        if raw.timed_out:
            return VerifierResult(self.tool, "error", 0.0, "timeout")
        if raw.returncode != 0:
            return VerifierResult(self.tool, "fail", 0.0, f"exit {raw.returncode}: {raw.stderr}")
        return self._successful(raw)

    def _successful(self, raw: RawCommandOutput) -> VerifierResult:
        return VerifierResult(self.tool, "pass", 1.0, raw.stdout)


class PytestVerifier(_BaseVerifier):
    """Run pytest in the specified workspace."""

    tool = "pytest"
    arguments = ("-q",)


class RuffVerifier(_BaseVerifier):
    """Run Ruff's checker in the specified workspace."""

    tool = "ruff"
    arguments = ("check", ".")


class MypyVerifier(_BaseVerifier):
    """Run mypy in strict mode in the specified workspace."""

    tool = "mypy"
    arguments = ("--strict", ".")


class BanditVerifier(_BaseVerifier):
    """Require clean parsed Bandit results at medium and high severity."""

    tool = "bandit"
    arguments = ("-r", ".", "-ll", "-f", "json")

    def _successful(self, raw: RawCommandOutput) -> VerifierResult:
        try:
            data = json.loads(raw.stdout)
            results = data["results"]
            if not isinstance(results, list):
                return VerifierResult(self.tool, "error", 0.0, "invalid bandit JSON")
            if any(
                not isinstance(item, dict)
                or item.get("issue_severity") not in {"LOW", "MEDIUM", "HIGH"}
                for item in results
            ):
                return VerifierResult(self.tool, "error", 0.0, "invalid bandit JSON")
            if any(item["issue_severity"] in {"MEDIUM", "HIGH"} for item in results):
                return VerifierResult(self.tool, "fail", 0.0, "medium or high finding")
        except (ValueError, TypeError, KeyError):
            return VerifierResult(self.tool, "error", 0.0, "invalid bandit JSON")
        return VerifierResult(self.tool, "pass", 1.0, "no medium or high findings")
