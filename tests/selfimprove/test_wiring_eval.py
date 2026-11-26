# Copyright 2026 Celina contributors.
"""Gate 4 offline evaluator: real observations, repeatability and isolation."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, TaskSpec
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l2.gate import VerificationGate
from celina_pkg.selfimprove.l2.verifiers import PytestVerifier
from celina_pkg.selfimprove.l3.runner import EvalRunner
from celina_pkg.selfimprove.l3.suite import load_benchmark_suite
from celina_pkg.selfimprove.wiring._eval_probes import injection_detection
from celina_pkg.selfimprove.wiring.eval_adapter import CelinaOfflineEvaluator

_TASK_COUNT = 24


def _fake_executor(
    argv: list[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    assert argv == ["pytest", "-q"]
    assert cwd.is_dir()
    assert timeout > 0
    return subprocess.CompletedProcess(argv, 0, "1 passed", "")


def _budget() -> BudgetGuard:
    return BudgetGuard(BudgetLimits(48, 1, 90, 100, 30))


def test_disabled_evaluator_does_not_create_state(tmp_path: Path) -> None:
    """The master switch prevents the evaluator from starting or touching state."""
    state = tmp_path / "disabled"
    evaluator = CelinaOfflineEvaluator(state, SelfImproveFlags(), _budget())
    task = load_benchmark_suite()[0]
    assert evaluator(task).status == "skipped"
    assert not state.exists()


def test_real_suite_repeatability_and_routing_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All 24 tasks use real probes and the real L3 runner without falsifying results."""
    monkeypatch.setenv("SELFIMPROVE_ENABLED", "1")
    monkeypatch.setenv("SELFIMPROVE_EVAL_HARNESS", "1")
    state = tmp_path / "state"
    flags = SelfImproveFlags(enabled=True, eval_harness=True, verifiers=True)
    budget = _budget()
    evaluator = CelinaOfflineEvaluator(state, flags, budget)
    gate = VerificationGate(flags, budget, [PytestVerifier(flags, budget, tmp_path,
                                                          executor=_fake_executor)])
    tasks = load_benchmark_suite()
    runner = EvalRunner(flags, budget, evaluator, gate=gate)
    first = runner.run("first", tasks)
    other_budget = _budget()
    other = CelinaOfflineEvaluator(tmp_path / "other-state", flags, other_budget)
    other_gate = VerificationGate(
        flags, other_budget,
        [PytestVerifier(flags, other_budget, tmp_path, executor=_fake_executor)],
    )
    second = EvalRunner(flags, other_budget, other, gate=other_gate).run("second", tasks)
    assert first.complete
    assert second.complete
    assert len(first.outcomes) == len(second.outcomes) == _TASK_COUNT
    assert [x.report for x in first.outcomes] == [x.report for x in second.outcomes]
    assert {task.category for task in tasks} == {
        "routing", "security_refusal", "tool_args", "config_integrity", "code_repair",
    }
    assert set(evaluator.diagnostics) == {task.spec.task_id for task in tasks}
    for outcome in first.outcomes:
        diagnostic = evaluator.diagnostics[outcome.report.task_id]
        assert isinstance(diagnostic.reason, str)
        assert diagnostic.reason
        assert diagnostic.passed is (outcome.report.status == "pass")
    for task in tasks:
        if task.category == "routing":
            diagnostic = evaluator.diagnostics[task.spec.task_id]
            if diagnostic.observed == "get_system_stats":
                assert not diagnostic.passed


def test_task_identity_cannot_be_relabelled(tmp_path: Path) -> None:
    """A copied ID with altered criterion is not the task that was measured."""
    flags = SelfImproveFlags(enabled=True, eval_harness=True)
    evaluator = CelinaOfflineEvaluator(tmp_path / "state", flags, _budget())
    task = load_benchmark_suite()[0]
    forged = replace(task, spec=TaskSpec(task.spec.task_id, "different goal"))
    assert evaluator(forged).status == "error"


def test_security_degradation_is_detected() -> None:
    """A broken injection detector is not counted as a successful security probe."""
    assert not injection_detection(lambda _text: []).passed
    assert injection_detection(
        lambda text: (
            [] if text == "Please summarize the meeting notes." else ["ignore_instructions"]
        ),
    ).passed


def test_invalid_state_dir_is_rejected() -> None:
    """Active evaluation requires a safe absolute explicit directory."""
    flags = SelfImproveFlags(enabled=True, eval_harness=True)
    evaluator = CelinaOfflineEvaluator(Path("relative"), flags, _budget())
    with pytest.raises(ValueError, match="state"):
        evaluator(load_benchmark_suite()[0])


def test_fresh_process_keeps_home_empty_and_checks_real_schema(tmp_path: Path) -> None:
    """Run actual Celina schema validation beside the evaluator in a fresh process."""
    home, state = tmp_path / "home", tmp_path / "state"
    home.mkdir()
    state.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(home),
        "TARGET_HOME": str(state / "direct-target"),
        "CELINA_STORAGE_DIR": str(state / "direct-storage"),
        "SELFIMPROVE_ENABLED": "1",
        "SELFIMPROVE_EVAL_HARNESS": "1",
        "DRY_RUN": "1",
        "BENCH_STATE": str(state / "evaluator"),
    }
    script = (
        "import os\n"
        "from pathlib import Path\n"
        "from celina_pkg import guardrails\n"
        "from celina_pkg.selfimprove.budget import BudgetGuard\n"
        "from celina_pkg.selfimprove.contracts import BudgetLimits\n"
        "from celina_pkg.selfimprove.flags import SelfImproveFlags\n"
        "from celina_pkg.selfimprove.l3.suite import load_benchmark_suite\n"
        "from celina_pkg.selfimprove.wiring.eval_adapter import CelinaOfflineEvaluator\n"
        "schema = {'read': {'parameters': {'properties': {'path': {'type': 'string'}}, "
        "'required': ['path'], 'additionalProperties': False}}}\n"
        "accepted, _ = guardrails.validate_tool_arguments('read', "
        "{'path': 'safe.txt', 'unknown': 1}, schema)\n"
        "task = next(t for t in load_benchmark_suite() if t.spec.task_id == 'tool_args-15')\n"
        "budget = BudgetGuard(BudgetLimits(24, 1, 90, 100, 30))\n"
        "ev = CelinaOfflineEvaluator(Path(os.environ['BENCH_STATE']), "
        "SelfImproveFlags(enabled=True, eval_harness=True), budget)\n"
        "result = ev(task)\n"
        "assert result.status == ('fail' if accepted else 'pass')\n"
    )
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and trusted inline probe.
        [sys.executable, "-c", script], env=env, timeout=90, check=False,
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list(home.iterdir())


def test_repeated_fresh_workers_can_use_same_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent state roots can host distinct bounded fixture runs without collisions."""
    monkeypatch.setenv("SELFIMPROVE_ENABLED", "1")
    monkeypatch.setenv("SELFIMPROVE_EVAL_HARNESS", "1")
    flags = SelfImproveFlags(enabled=True, eval_harness=True)
    state = tmp_path / "state"
    task = next(item for item in load_benchmark_suite() if item.spec.task_id == "routing-01")
    first = CelinaOfflineEvaluator(state, flags, _budget())(task)
    second = CelinaOfflineEvaluator(state, flags, _budget())(task)
    assert first == second
