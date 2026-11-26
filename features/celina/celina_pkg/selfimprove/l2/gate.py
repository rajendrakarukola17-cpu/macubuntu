# Copyright 2026 Celina contributors.
"""Fail-closed aggregation of configured L0 verifier-protocol instances."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.budget import BudgetExceeded
from celina_pkg.selfimprove.contracts import VerifierResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from celina_pkg.selfimprove.budget import BudgetGuard
    from celina_pkg.selfimprove.contracts import TaskSpec, VerifierProtocol
    from celina_pkg.selfimprove.flags import SelfImproveFlags


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Immutable aggregate decision; no verifier means no approval."""

    passed: bool
    blocked_reason: str
    results: tuple[VerifierResult, ...]


class VerificationGate:
    """Aggregate host-configured verifiers, which must not be agent self-reports.

    Verifier objects and executor injection are trusted host configuration. This
    gate is neither an in-process execution attestation mechanism nor an OS
    sandbox. OS/worktree isolation belongs at the sandbox/L7 boundary; this
    module does not implement or verify that boundary.
    """

    def __init__(
        self,
        flags: SelfImproveFlags,
        budget: BudgetGuard,
        verifiers: Sequence[VerifierProtocol],
        *,
        fail_fast: bool = True,
    ) -> None:
        """Bind an explicit verifier suite and shared wall-clock budget."""
        self.flags = flags
        self.budget = budget
        self.verifiers = tuple(verifiers)
        self.fail_fast = fail_fast

    def verify(self, task: TaskSpec) -> GateDecision:
        """Pass if and only if every configured verifier completes successfully."""
        if not self.flags.enabled or not self.flags.verifiers:
            return GateDecision(passed=False, blocked_reason="verifiers_disabled", results=())
        if not self.verifiers:
            return GateDecision(passed=False, blocked_reason="no_verifiers_configured", results=())
        results: list[VerifierResult] = []
        for verifier in self.verifiers:
            try:
                self.budget.check_wall_clock()
                result = verifier.verify(task)
                self.budget.check_wall_clock()
                if not isinstance(result, VerifierResult):
                    result = VerifierResult("verifier", "error", 0.0, "invalid verifier result")
            except BudgetExceeded as exc:
                results.append(VerifierResult("budget", "error", 0.0, exc.dimension))
                return GateDecision(
                    passed=False, blocked_reason="budget_exceeded", results=tuple(results),
                )
            except Exception as exc:
                try:
                    self.budget.check_wall_clock()
                except BudgetExceeded as budget_error:
                    results.append(VerifierResult("budget", "error", 0.0, budget_error.dimension))
                    return GateDecision(
                        passed=False, blocked_reason="budget_exceeded", results=tuple(results),
                    )
                result = VerifierResult("verifier", "error", 0.0, type(exc).__name__)
            results.append(result)
            if self.fail_fast and result.status != "pass":
                return GateDecision(
                    passed=False, blocked_reason="verifier_failed", results=tuple(results),
                )
        passed = all(result.status == "pass" for result in results)
        return GateDecision(passed, "" if passed else "verifier_failed", tuple(results))
