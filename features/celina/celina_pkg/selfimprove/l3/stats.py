# Copyright 2026 Celina contributors.
"""Deterministic paired bootstrap over immutable per-task evaluation results."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from celina_pkg.selfimprove.contracts import EvalReport


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """L0 result with the benchmark's immutable security classification."""

    report: EvalReport
    security_critical: bool

    def __post_init__(self) -> None:
        """Reject malformed outcomes."""
        if not isinstance(self.report, EvalReport) or type(self.security_critical) is not bool:
            message = "invalid task outcome"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class EvalRun:
    """Immutable run; incomplete runs must never be compared for promotion."""

    run_id: str
    outcomes: tuple[TaskOutcome, ...]
    complete: bool = True
    blocked_reason: str = ""

    def __post_init__(self) -> None:
        """Validate identity and reject duplicate task outcomes."""
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            message = "invalid run ID"
            raise ValueError(message)
        if not isinstance(self.outcomes, tuple) or any(
            not isinstance(outcome, TaskOutcome) for outcome in self.outcomes
        ):
            message = "outcomes must be an immutable tuple"
            raise ValueError(message)
        ids = [outcome.report.task_id for outcome in self.outcomes]
        if len(ids) != len(set(ids)) or type(self.complete) is not bool:
            message = "duplicate task ID or invalid completion flag"
            raise ValueError(message)
        if not isinstance(self.blocked_reason, str) or (self.complete and self.blocked_reason):
            message = "invalid blocked reason"
            raise ValueError(message)

    @property
    def pass_rate(self) -> float:
        """Return fraction of outcomes that passed, or zero for empty runs."""
        return sum(item.report.status == "pass" for item in self.outcomes) / max(
            1, len(self.outcomes),
        )

    @property
    def security_pass_rate(self) -> float:
        """Return pass fraction of critical tasks, or zero if there are none."""
        security = [item for item in self.outcomes if item.security_critical]
        return sum(item.report.status == "pass" for item in security) / max(1, len(security))


@dataclass(frozen=True, slots=True)
class EvalComparison:
    """Paired score delta, percentile confidence interval and promotion decision."""

    delta: float
    ci_low: float
    ci_high: float
    improved: bool
    statistically_significant: bool
    security_regression: bool
    approved_for_promotion: bool


def _paired(baseline: EvalRun, candidate: EvalRun) -> tuple[list[int], bool]:
    if not baseline.complete or not candidate.complete:
        message = "incomplete runs cannot be compared"
        raise ValueError(message)
    old = {item.report.task_id: item for item in baseline.outcomes}
    new = {item.report.task_id: item for item in candidate.outcomes}
    if not old or old.keys() != new.keys():
        message = "task ID sets must be equal and nonempty"
        raise ValueError(message)
    if any(old[key].security_critical != new[key].security_critical for key in old):
        message = "security classification mismatch"
        raise ValueError(message)
    if any(item.report.status not in {"pass", "fail"} for item in (*old.values(), *new.values())):
        message = "nonterminal or error outcomes cannot be compared"
        raise ValueError(message)
    ids = sorted(old)
    changes = [
        int(new[key].report.status == "pass") - int(old[key].report.status == "pass") for key in ids
    ]
    veto = any(
        old[key].security_critical
        and old[key].report.status == "pass"
        and new[key].report.status != "pass"
        for key in ids
    )
    return changes, veto


def compare_eval_runs(
    baseline: EvalRun,
    candidate: EvalRun,
    *,
    seed: int = 42,
    resamples: int = 2000,
    alpha: float = 0.05,
) -> EvalComparison:
    """Use paired resampling and veto every critical pass-to-nonpass regression."""
    if type(resamples) is not int or resamples < 1:
        message = "resamples must be a positive integer"
        raise ValueError(message)
    if (
        isinstance(alpha, bool)
        or not isinstance(alpha, (float, int))
        or not math.isfinite(alpha)
        or not 0 < alpha < 1
    ):
        message = "alpha must be finite and between zero and one"
        raise ValueError(message)
    changes, veto = _paired(baseline, candidate)
    rng = random.Random(seed)  # noqa: S311  # Reproducible statistics, not cryptography.
    n = len(changes)
    samples = sorted(sum(changes[rng.randrange(n)] for _ in changes) / n for _ in range(resamples))
    low = samples[math.floor((resamples - 1) * alpha / 2)]
    high = samples[math.ceil((resamples - 1) * (1 - alpha / 2))]
    delta = sum(changes) / n
    significant = low > 0
    return EvalComparison(delta, low, high, delta > 0, significant, veto, significant and not veto)
