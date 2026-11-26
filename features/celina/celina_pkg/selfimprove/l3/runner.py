# Copyright 2026 Celina contributors.
"""Flag- and budget-gated execution of host-configured offline evaluators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from celina_pkg.selfimprove.budget import BudgetExceeded
from celina_pkg.selfimprove.contracts import EvalReport

from .stats import EvalRun, TaskOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence

    from celina_pkg.selfimprove.budget import BudgetGuard
    from celina_pkg.selfimprove.flags import SelfImproveFlags
    from celina_pkg.selfimprove.l2.gate import VerificationGate

    from .store import EvalStore
    from .suite import EvalTask


class TaskEvaluator(Protocol):
    """Host-supplied evaluator; not an attestation or security sandbox."""

    def __call__(self, task: EvalTask) -> EvalReport:
        """Evaluate one task and return its L0 report."""


class EvalRunner:
    """Run tasks with explicit trusted configuration and bounded iterations."""

    def __init__(
        self,
        flags: SelfImproveFlags,
        budget: BudgetGuard,
        evaluator: TaskEvaluator,
        *,
        store: EvalStore | None = None,
        gate: VerificationGate | None = None,
    ) -> None:
        """Bind the execution, accounting, storage and verifier dependencies."""
        self.flags = flags
        self.budget = budget
        self.evaluator = evaluator
        self.store = store
        self.gate = gate

    def _evaluate(self, task: EvalTask) -> EvalReport:
        try:
            result = self.evaluator(task)
            self.budget.check_wall_clock()
        except BudgetExceeded:
            raise
        except Exception:
            self.budget.check_wall_clock()
            return EvalReport(task.spec.task_id, "error", 0.0, 1)
        if not isinstance(result, EvalReport) or result.task_id != task.spec.task_id:
            return EvalReport(task.spec.task_id, "error", 0.0, 1)
        if task.category == "code_repair" and result.status == "pass":
            if self.gate is None:
                return EvalReport(task.spec.task_id, "error", 0.0, 1)
            decision = self.gate.verify(task.spec)
            if decision.blocked_reason == "budget_exceeded":
                message = "gate budget exceeded"
                raise BudgetExceeded(message, 0, 1)
            if not decision.passed:
                return EvalReport(task.spec.task_id, "fail", 0.0, 1)
        return result

    def run(self, run_id: str, tasks: Sequence[EvalTask]) -> EvalRun:
        """Evaluate in given order, stopping on budget exhaustion; optionally persist."""
        if not self.flags.enabled or not self.flags.eval_harness:
            return EvalRun(run_id, (), complete=False, blocked_reason="eval_harness_disabled")
        if not tasks or len({task.spec.task_id for task in tasks}) != len(tasks):
            message = "tasks must be nonempty and have unique IDs"
            raise ValueError(message)
        outcomes: list[TaskOutcome] = []
        try:
            for task in tasks:
                self.budget.consume_iteration()
                result = self._evaluate(task)
                self.budget.check_wall_clock()
                outcomes.append(TaskOutcome(result, task.security_critical))
            report = EvalRun(run_id, tuple(outcomes))
            if self.store is not None:
                self.budget.consume_file_write(2)
                self.store.append(report)
        except BudgetExceeded as exc:
            return EvalRun(
                run_id,
                tuple(outcomes),
                complete=False,
                blocked_reason=f"budget_exceeded:{exc.dimension}",
            )
        else:
            return report
