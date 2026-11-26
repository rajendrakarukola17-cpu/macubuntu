# Copyright 2026 Celina contributors.
"""Preserve L2 budget dimensions across the existing L3 gate interface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from celina_pkg.selfimprove.budget import BudgetExceeded

if TYPE_CHECKING:
    from celina_pkg.selfimprove.contracts import TaskSpec, VerifierResult


class _Decision(Protocol):
    @property
    def blocked_reason(self) -> str: ...

    @property
    def results(self) -> tuple[VerifierResult, ...]: ...


class _Gate(Protocol):
    def verify(self, task: TaskSpec) -> _Decision: ...


class GateBudgetAdapter:
    """Pass gate decisions through, converting gate budget blocks to L1 exceptions.

    L3 otherwise replaces the true dimension with a generic string. This adapter
    is structural: L3 consumes only the gate's verify method, and the actual
    host-configured gate retains responsibility for verification.
    """

    def __init__(self, gate: _Gate) -> None:
        """Bind the existing host-configured verifier gate."""
        self.gate = gate

    def verify(self, task: TaskSpec) -> _Decision:
        """Preserve a verifier-side budget dimension or return the decision."""
        decision = self.gate.verify(task)
        if decision.blocked_reason == "budget_exceeded":
            dimension = decision.results[-1].detail if decision.results else "unknown"
            raise BudgetExceeded(dimension, 0, 1)
        return decision
