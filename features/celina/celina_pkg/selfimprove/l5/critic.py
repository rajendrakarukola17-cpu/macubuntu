# Copyright 2026 Celina contributors.
"""Deterministic critique constrained by independent verifier truth."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.contracts import CritiqueResult
from celina_pkg.selfimprove.l4.redactor import redact

if TYPE_CHECKING:
    from collections.abc import Callable

    from celina_pkg.selfimprove.contracts import AttemptRecord, TaskSpec, VerifierResult
    from celina_pkg.selfimprove.l2.gate import GateDecision

_WEIGHT_COUNT = 2
_MAX_RESULTS = 64
_MAX_DETAIL = 512


@dataclass(frozen=True, slots=True)
class CriticAssessment:
    """A critique plus explicit, immutable verifier-grounded findings."""

    result: CritiqueResult
    accepted: bool
    findings: tuple[str, ...]
    failure_signature: str


def _clean(value: str, limit: int) -> str:
    """Normalize bounded text and apply the L4 heuristic redactor."""
    return " ".join(redact(value[:limit]).split())


def _failures(results: tuple[VerifierResult, ...]) -> tuple[tuple[str, str, str], ...]:
    if len(results) > _MAX_RESULTS:
        message = "too many verifier results"
        raise ValueError(message)
    return tuple(
        sorted(
            (_clean(item.verifier_id, 128), item.status, _clean(item.detail, _MAX_DETAIL))
            for item in results
            if item.status != "pass"
        ),
    )


def compute_failure_signature(results: tuple[VerifierResult, ...]) -> str:
    """Hash sorted, normalized, bounded verifier failures; never hash actor claims."""
    failures = _failures(results)
    if not failures:
        return ""
    payload = json.dumps(failures, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hint(verifier: str) -> str:
    name = verifier.lower()
    if "pytest" in name or "test" in name:
        return "Inspect the failing test and correct the behavior."
    if "ruff" in name or "lint" in name:
        return "Fix the reported lint findings."
    if "mypy" in name or "type" in name:
        return "Resolve the reported type errors."
    if "bandit" in name or "security" in name:
        return "Review and remediate the security findings."
    return "Inspect this verifier's failure and address its cause."


class RubricCritic:
    """L0-compatible critic; verified assessments require independent results."""

    def __init__(
        self,
        *,
        weights: tuple[float, float] = (0.8, 0.2),
        scorer: Callable[[TaskSpec, str], float] | None = None,
    ) -> None:
        """Validate a two-component rubric and optional trusted host scorer."""
        if (
            len(weights) != _WEIGHT_COUNT
            or any(
                isinstance(w, bool)
                or not isinstance(w, (int, float))
                or not math.isfinite(w)
                or w < 0
                for w in weights
            )
            or not math.isclose(sum(weights), 1.0, abs_tol=1e-9)
        ):
            message = "weights must be finite, nonnegative and sum to one"
            raise ValueError(message)
        self.weights = weights
        self.scorer = scorer

    def critique(self, task: TaskSpec, attempts: tuple[AttemptRecord, ...]) -> CritiqueResult:
        """Reject certification because L0 carries no verifier evidence."""
        del attempts
        return CritiqueResult(task.task_id, 0.0, "Independent verifier evidence is required.")

    def assess(
        self,
        task: TaskSpec,
        attempts: tuple[AttemptRecord, ...],
        *,
        actor_summary: str,
        verifier_results: tuple[VerifierResult, ...],
        gate: GateDecision | None = None,
    ) -> CriticAssessment:
        """Score only nonempty passing evidence, clamping any failed gate to zero."""
        del attempts
        failures = _failures(verifier_results)
        if (
            not verifier_results
            or failures
            or (gate is not None and (not gate.passed or gate.results != verifier_results))
        ):
            return self._rejected(task, verifier_results, failures, gate)
        summary = _clean(actor_summary, 2048)
        quality = self.scorer(task, summary) if self.scorer is not None else 1.0
        if (
            isinstance(quality, bool)
            or not isinstance(quality, (int, float))
            or not math.isfinite(quality)
            or not 0 <= quality <= 1
        ):
            message = "scorer must return a finite score in [0, 1]"
            raise ValueError(message)
        score = self.weights[0] + self.weights[1] * quality
        return CriticAssessment(
            result=CritiqueResult(task.task_id, score, "Verifiers passed."),
            accepted=True,
            findings=(),
            failure_signature="",
        )

    def _rejected(
        self,
        task: TaskSpec,
        results: tuple[VerifierResult, ...],
        failures: tuple[tuple[str, str, str], ...],
        gate: GateDecision | None,
    ) -> CriticAssessment:
        findings = tuple(
            f"{name}: {status}: {detail}. {_hint(name)}" for name, status, detail in failures
        )
        if not findings:
            findings = (
                "Independent verifier evidence or gate approval is missing or inconsistent.",
            )
        signature = compute_failure_signature(results)
        if not signature:
            reason = _clean(gate.blocked_reason, 128) if gate is not None else "missing evidence"
            signature = hashlib.sha256(reason.encode("utf-8")).hexdigest()
        return CriticAssessment(
            result=CritiqueResult(task.task_id, 0.0, " ".join(findings)),
            accepted=False,
            findings=findings,
            failure_signature=signature,
        )
