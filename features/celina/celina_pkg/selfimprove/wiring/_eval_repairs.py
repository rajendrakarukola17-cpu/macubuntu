# Copyright 2026 Celina contributors.
"""Probe actual L2 decisions using explicitly simulated verifier process outputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, TaskSpec
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l2.gate import VerificationGate
from celina_pkg.selfimprove.l2.verifiers import BanditVerifier, MypyVerifier, PytestVerifier

from ._eval_probes import Diagnostic

if TYPE_CHECKING:
    from pathlib import Path

    from celina_pkg.selfimprove.l2.gate import GateDecision
    from celina_pkg.selfimprove.l2.runner import CommandExecutor


def _interpret(decision: GateDecision, expected: tuple[tuple[str, str], ...]) -> Diagnostic:
    observed = tuple((result.verifier_id, result.status) for result in decision.results)
    correct = not decision.passed and decision.blocked_reason == "verifier_failed"
    passed = correct and observed == expected
    reason = f"simulated verifier outputs: gate {decision.blocked_reason}; observed {observed}"
    return Diagnostic(passed, reason, repr(observed))


def repair_observations(workspace: Path, executor: CommandExecutor) -> dict[str, Diagnostic]:
    """Exercise real L2 gates; injected outputs are simulations, not real test executions."""
    flags = SelfImproveFlags(enabled=True, verifiers=True)
    budget = BudgetGuard(BudgetLimits(4, 1, 30, 0, 4))
    pytest_case = workspace / "pytest-case"
    bandit_case = workspace / "bandit-case"
    mypy_case = workspace / "mypy-case"
    for directory in (pytest_case, bandit_case, mypy_case):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    pytest_gate = VerificationGate(flags, budget, [
        PytestVerifier(flags, budget, pytest_case, executor=executor),
    ])
    bandit_gate = VerificationGate(flags, budget, [
        PytestVerifier(flags, budget, bandit_case, executor=executor),
        BanditVerifier(flags, budget, bandit_case, executor=executor),
    ])
    mypy_gate = VerificationGate(flags, budget, [
        MypyVerifier(flags, budget, mypy_case, executor=executor),
    ])
    task = TaskSpec("repair-benchmark", "verify the proposed repair")
    empty = VerificationGate(flags, budget, []).verify(task)
    return {
        "code_repair-21": _interpret(pytest_gate.verify(task), (("pytest", "fail"),)),
        "code_repair-22": _interpret(
            bandit_gate.verify(task), (("pytest", "pass"), ("bandit", "fail")),
        ),
        "code_repair-23": _interpret(mypy_gate.verify(task), (("mypy", "fail"),)),
        "code_repair-24": Diagnostic(
            not empty.passed and empty.blocked_reason == "no_verifiers_configured",
            f"empty verifier gate: {empty.blocked_reason}", empty.blocked_reason,
        ),
    }
