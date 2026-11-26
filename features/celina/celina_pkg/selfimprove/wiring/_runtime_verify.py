# Copyright 2026 Celina contributors.
"""Independent L2 meta-verification for simulated repair benchmark fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from celina_pkg.selfimprove.contracts import VerifierResult

from ._eval_repairs import repair_observations
from ._eval_worker import _simulated_verifier

if TYPE_CHECKING:
    from pathlib import Path

    from celina_pkg.selfimprove.budget import BudgetGuard
    from celina_pkg.selfimprove.contracts import TaskSpec

    from ._eval_probes import Diagnostic


class RepairFixtureVerifier:
    """Repeat L2 fixture checks; this does not verify an actual submitted patch."""

    def __init__(self, state_dir: Path, budget: BudgetGuard) -> None:
        """Bind explicit fixture state and shared runtime budget."""
        self.state_dir, self.budget = state_dir, budget
        self.observations: dict[str, Diagnostic] = {}

    def verify(self, task: TaskSpec) -> VerifierResult:
        """Re-evaluate the L2 decision instead of trusting an evaluator self-report."""
        if not self.observations:
            self.budget.consume_file_write(3)
            self.budget.consume_subprocess(4)
            self.observations = repair_observations(self.state_dir, _simulated_verifier)
        result = self.observations.get(task.task_id)
        passed = result is not None and result.passed
        return VerifierResult(
            "repair_fixture", "pass" if passed else "fail", float(passed),
            "independent L2 recheck of simulated verifier outputs",
        )
