# Copyright 2026 Celina contributors.
"""L2 repair benchmark observations with explicit simulated command outcomes."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.wiring._eval_repairs import repair_observations

if TYPE_CHECKING:
    from pathlib import Path

_REPAIR_COUNT = 4


def _failing_executor(
    argv: list[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    assert cwd.is_dir()
    assert timeout > 0
    if argv[0] == "bandit":
        return subprocess.CompletedProcess(argv, 0, '{"results":[{"issue_severity":"MEDIUM"}]}', "")
    return subprocess.CompletedProcess(
        argv, 1 if argv[0] in {"pytest", "mypy"} else 0, "", "failure",
    )


def _clean_executor(
    argv: list[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    assert cwd.is_dir()
    assert timeout > 0
    output = '{"results":[]}' if argv[0] == "bandit" else "passed"
    return subprocess.CompletedProcess(argv, 0, output, "")


def test_real_l2_rejects_specific_failures(tmp_path: Path) -> None:
    """The real L2 gates must reject pytest, Bandit and mypy failures and empty suites."""
    # Task 22 requires a passing pytest result followed by a failing Bandit result.
    def executor(argv: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        if argv[0] == "pytest" and "bandit-case" in str(cwd):
            return _clean_executor(argv, cwd=cwd, timeout=timeout)
        return _failing_executor(argv, cwd=cwd, timeout=timeout)

    out = repair_observations(tmp_path, executor)
    assert len(out) == _REPAIR_COUNT
    assert all(item.passed for item in out.values())
    assert all("simulated" in out[name].reason for name in out if name != "code_repair-24")


def test_degraded_verifier_outcomes_are_not_coerced_to_pass(tmp_path: Path) -> None:
    """Clean command outputs are not falsely reported as detecting repair failures."""
    out = repair_observations(tmp_path, _clean_executor)
    assert not out["code_repair-21"].passed
    assert not out["code_repair-22"].passed
    assert not out["code_repair-23"].passed
    assert out["code_repair-24"].passed
