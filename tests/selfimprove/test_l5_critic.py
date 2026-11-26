# Copyright 2026 Celina contributors.
"""Test a separate, verifier-truth-clamped L5 critic."""

from dataclasses import FrozenInstanceError

import pytest

from celina_pkg.selfimprove.contracts import (
    AttemptRecord,
    CriticProtocol,
    CritiqueResult,
    TaskSpec,
    VerifierResult,
)
from celina_pkg.selfimprove.l2.gate import GateDecision
from celina_pkg.selfimprove.l5.critic import RubricCritic, compute_failure_signature

TASK = TaskSpec("task-1", "repair a broken test")
FAILED = VerifierResult("pytest", "fail", 0.0, "exit 1: AssertionError expected 2")
PASSED = VerifierResult("pytest", "pass", 1.0, "all tests passed")
HIGH_SCORE = 0.8
MAX_FINDING_CHARS = 5000
ATTEMPTS = (AttemptRecord("try-1", "task-1", "failed", 3),)


def test_l0_protocol_never_infers_pass_from_actor_records() -> None:
    """The narrow protocol has no verifier input, so it fails closed."""
    critic: CriticProtocol = RubricCritic()
    result = critic.critique(TASK, ATTEMPTS)
    assert isinstance(result, CritiqueResult)
    assert result.score == 0
    assert "verifier" in result.feedback.lower()
    with pytest.raises(FrozenInstanceError):
        result.score = 1.0


def test_failed_and_empty_results_cannot_be_approved_by_custom_scorer() -> None:
    """Self-reported success and a scorer returning 1 cannot override failure."""
    critic = RubricCritic(scorer=lambda _task, _summary: 1.0)
    for results in ((), (FAILED,), (PASSED, FAILED)):
        assessment = critic.assess(
            TASK, ATTEMPTS, actor_summary="All tests passed", verifier_results=results,
        )
        assert not assessment.accepted
        assert assessment.result.score == 0
        assert assessment.findings
    decision = GateDecision(passed=False, blocked_reason="verifier_failed", results=(PASSED,))
    assessment = critic.assess(
        TASK, ATTEMPTS, actor_summary="passed", verifier_results=(PASSED,), gate=decision,
    )
    assert not assessment.accepted
    assert assessment.result.score == 0


def test_nonempty_passing_verifiers_produce_high_rubric_score() -> None:
    """Only nonempty all-passing verifier evidence can receive acceptance."""
    decision = GateDecision(passed=True, blocked_reason="", results=(PASSED,))
    assessment = RubricCritic().assess(
        TASK, ATTEMPTS, actor_summary="fixed", verifier_results=(PASSED,), gate=decision,
    )
    assert assessment.accepted
    assert assessment.result.score >= HIGH_SCORE
    assert assessment.findings == ()
    assert assessment.failure_signature == ""


def test_diagnosis_is_bounded_and_fingerprints_are_stable() -> None:
    """Failures identify tools and hints without leaking secrets or unbounded text."""
    failed = (
        FAILED,
        VerifierResult("ruff", "fail", 0, "exit 2: lint token=secretValue " + "x" * 10000),
        VerifierResult("mypy", "error", 0, "type mismatch"),
        VerifierResult("bandit", "fail", 0, "medium security finding"),
    )
    report = RubricCritic().assess(TASK, ATTEMPTS, actor_summary="  ", verifier_results=failed)
    text = " ".join(report.findings)
    for term in ("pytest", "ruff", "mypy", "bandit", "exit 1"):
        assert term in text.lower()
    assert "secretValue" not in text
    assert len(text) < MAX_FINDING_CHARS
    assert report.failure_signature == compute_failure_signature(tuple(reversed(failed)))
    assert report.failure_signature == compute_failure_signature(failed)


@pytest.mark.parametrize("weights", [(0.9, 0.9), (-1.0, 2.0), (float("nan"), 1.0)])
def test_invalid_weights_fail_closed(weights: tuple[float, float]) -> None:
    """Invalid rubric weights are rejected rather than silently normalized."""
    with pytest.raises(ValueError, match="weights"):
        RubricCritic(weights=weights)


def test_limits_other_statuses_and_unknown_verifiers() -> None:
    """Bound suite size and reject skipped or errored unknown verifiers."""
    for status in ("error", "skipped"):
        result = VerifierResult("custom", status, 0, "unexpected state")
        report = RubricCritic().assess(
            TASK, ATTEMPTS, actor_summary="", verifier_results=(result,),
        )
        assert not report.accepted
        assert "inspect" in report.findings[0].lower()
    with pytest.raises(ValueError, match="too many"):
        compute_failure_signature((FAILED,) * 65)


@pytest.mark.parametrize("quality", [-1.0, 1.1, float("nan"), True])
def test_invalid_custom_scores_are_rejected(quality: float) -> None:
    """Scorer bugs cannot synthesize an invalid normalized score."""
    with pytest.raises(ValueError, match="scorer"):
        RubricCritic(scorer=lambda _task, _summary: quality).assess(
            TASK, ATTEMPTS, actor_summary="fix", verifier_results=(PASSED,),
        )


def test_passing_gate_cannot_certify_a_different_suite() -> None:
    """Evidence must match the independently supplied gate decision."""
    decision = GateDecision(passed=True, blocked_reason="", results=(PASSED,))
    other = VerifierResult("other", "pass", 1, "pass")
    report = RubricCritic().assess(
        TASK, ATTEMPTS, actor_summary="", verifier_results=(other,), gate=decision,
    )
    assert not report.accepted
    assert report.result.score == 0
