# Copyright 2026 Celina contributors.
"""Test evaluation execution, budget controls and safe CLI defaults."""

import json
from pathlib import Path

import pytest

from celina_pkg.selfimprove.budget import BudgetExceeded, BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, EvalReport, TaskSpec, VerifierResult
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l2.gate import VerificationGate
from celina_pkg.selfimprove.l3.cli import main
from celina_pkg.selfimprove.l3.runner import EvalRunner
from celina_pkg.selfimprove.l3.store import EvalStore
from celina_pkg.selfimprove.l3.suite import EvalTask, load_benchmark_suite

BUDGET_TASKS = 2
REPAIR_TASKS = 4
SUITE_SIZE = 24


def guard(iterations: int = 30, *, writes: int = 10) -> BudgetGuard:
    """Build a deterministic test budget."""
    return BudgetGuard(BudgetLimits(iterations, 100, 100, writes, 0), clock=lambda: 0)


def test_disabled_does_not_call_evaluator(tmp_path: Path) -> None:
    """Neither disabled switch allows evaluation or storage."""
    calls = []

    def evaluator(task: EvalTask) -> EvalReport:
        calls.append(task)
        return EvalReport(task.spec.task_id, "pass", 1.0, 1)

    for flags in (SelfImproveFlags(), SelfImproveFlags(enabled=True)):
        report = EvalRunner(flags, guard(), evaluator).run("run", load_benchmark_suite())
        assert not report.complete
        assert report.blocked_reason == "eval_harness_disabled"
    assert not calls
    assert not list(tmp_path.iterdir())


def test_budget_stops_suite() -> None:
    """One iteration per task; over-budget tasks are not evaluated."""
    calls = []

    def evaluator(task: EvalTask) -> EvalReport:
        calls.append(task.spec.task_id)
        return EvalReport(task.spec.task_id, "pass", 1.0, 1)

    report = EvalRunner(SelfImproveFlags(enabled=True, eval_harness=True), guard(2), evaluator).run(
        "limited",
        load_benchmark_suite(),
    )
    assert not report.complete
    assert report.blocked_reason == "budget_exceeded:iterations"
    assert len(calls) == BUDGET_TASKS
    assert len(report.outcomes) == BUDGET_TASKS


def test_success_security_scores_persist_and_l2_gate(tmp_path: Path) -> None:
    """Code repair successes need a real configured gate; reports persist."""
    calls = []
    flags = SelfImproveFlags(enabled=True, eval_harness=True, verifiers=True)
    budget = guard()

    class Verifier:
        def verify(self, task: TaskSpec) -> VerifierResult:
            calls.append(task.task_id)
            return VerifierResult("trusted", "pass", 1.0)

    def evaluator(task: EvalTask) -> EvalReport:
        return EvalReport(task.spec.task_id, "pass", 1.0, 1)

    gate = VerificationGate(flags, budget, [Verifier()])
    store = EvalStore(tmp_path / "store")
    report = EvalRunner(flags, budget, evaluator, store=store, gate=gate).run(
        "run",
        load_benchmark_suite(),
    )
    assert report.complete
    assert report.pass_rate == 1
    assert report.security_pass_rate == 1
    assert len(calls) == REPAIR_TASKS
    assert store.verify_chain()[0].run == report


def test_no_gate_means_code_repairs_do_not_pass() -> None:
    """Claimed repair success without host verification is denied."""

    def evaluator(task: EvalTask) -> EvalReport:
        return EvalReport(task.spec.task_id, "pass", 1.0, 1)

    flags = SelfImproveFlags(enabled=True, eval_harness=True)
    tasks = tuple(task for task in load_benchmark_suite() if task.category == "code_repair")
    report = EvalRunner(flags, guard(), evaluator).run("run", tasks)
    assert not any(outcome.report.status == "pass" for outcome in report.outcomes)


def test_cli_disabled_and_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Disabled CLI has no effects; enabled dry run prints labelled JSON."""
    suite = (
        Path(__file__).parents[2] / "features/celina/celina_pkg/selfimprove/l3/benchmark_tasks.json"
    )
    store_dir = tmp_path / "store"
    assert main(["--dry-run", "--suite", str(suite), "--store-dir", str(store_dir)], env={}) != 0
    assert not list(tmp_path.iterdir())
    capsys.readouterr()
    result = main(
        ["--dry-run", "--suite", str(suite)],
        env={
            "SELFIMPROVE_ENABLED": "1",
            "SELFIMPROVE_EVAL_HARNESS": "1",
        },
    )
    summary = json.loads(capsys.readouterr().out)
    assert result == 0
    assert summary["dry_run"] is True
    assert summary["task_count"] == SUITE_SIZE
    assert not list(tmp_path.iterdir())


def test_runner_error_and_mismatched_report_are_fail_closed() -> None:
    """Evaluator exceptions and wrong identities become error outcomes."""
    flags = SelfImproveFlags(enabled=True, eval_harness=True)
    tasks = load_benchmark_suite()

    def broken(task: EvalTask) -> EvalReport:
        message = task.spec.task_id
        raise RuntimeError(message)

    def wrong(task: EvalTask) -> EvalReport:
        return EvalReport("wrong-" + task.spec.task_id, "pass", 1.0, 1)

    for evaluator in (broken, wrong):
        report = EvalRunner(flags, guard(), evaluator).run("x", tasks[:1])
        assert report.outcomes[0].report.status == "error"
    with pytest.raises(ValueError, match="nonempty"):
        EvalRunner(flags, guard(), wrong).run("x", ())


def test_runner_gate_failure_and_budget_failure() -> None:
    """Gate rejection fails repairs and gate budget exhaustion stops the suite."""
    flags = SelfImproveFlags(enabled=True, eval_harness=True, verifiers=True)
    tasks = tuple(task for task in load_benchmark_suite() if task.category == "code_repair")

    def evaluator(task: EvalTask) -> EvalReport:
        return EvalReport(task.spec.task_id, "pass", 1, 1)

    empty = VerificationGate(flags, guard(), ())
    report = EvalRunner(flags, guard(), evaluator, gate=empty).run("x", tasks)
    assert all(outcome.report.status == "fail" for outcome in report.outcomes)

    class Exhausted:
        def verify(self, task: TaskSpec) -> VerifierResult:
            raise BudgetExceeded(task.task_id, 0, 1)

    gate = VerificationGate(flags, guard(), (Exhausted(),))
    report = EvalRunner(flags, guard(), evaluator, gate=gate).run("x", tasks)
    assert not report.complete
    assert report.blocked_reason.startswith("budget_exceeded:")
    assert not report.outcomes


def test_runner_wall_expiry_and_write_budget(tmp_path: Path) -> None:
    """Elapsed time is checked after evaluator; denied writes cannot persist."""
    flags = SelfImproveFlags(enabled=True, eval_harness=True)
    tasks = load_benchmark_suite()[:1]
    times = iter([0.0, 101.0])
    budget = BudgetGuard(BudgetLimits(2, 1, 100, 2, 0), clock=lambda: next(times))

    def evaluator(task: EvalTask) -> EvalReport:
        return EvalReport(task.spec.task_id, "pass", 1.0, 1)

    report = EvalRunner(flags, budget, evaluator).run("x", tasks)
    assert not report.complete
    assert report.blocked_reason == "budget_exceeded:wall_seconds"
    store = EvalStore(tmp_path / "store")
    report = EvalRunner(flags, guard(writes=0), evaluator, store=store).run("x", tasks)
    assert report.blocked_reason == "budget_exceeded:file_writes"
    assert not store.verify_chain()


def test_cli_rejects_bad_suite_and_non_dry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI only supports a read-only synthetic mode and rejects bad suites."""
    env = {"SELFIMPROVE_ENABLED": "1", "SELFIMPROVE_EVAL_HARNESS": "1"}
    assert main([], env=env) != 0
    assert "only --dry-run" in capsys.readouterr().err
    assert main(["--dry-run", "--suite", str(tmp_path / "missing")], env=env) != 0
    assert "cannot load" in capsys.readouterr().err
    assert main(["--dry-run", "--store-dir", str(tmp_path / "store")], env=env) != 0
    assert "cannot persist" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text("[")
    assert main(["--dry-run", "--suite", str(bad)], env=env) != 0
    assert "cannot load" in capsys.readouterr().err


def test_cli_persistence_requires_explicit_path_and_trusted_evaluator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicitly injected evaluator may persist only to an explicit directory."""
    env = {"SELFIMPROVE_ENABLED": "1", "SELFIMPROVE_EVAL_HARNESS": "1"}

    def evaluator(task: EvalTask) -> EvalReport:
        return EvalReport(task.spec.task_id, "fail", 0, 1)

    assert main(["--persist"], env=env, evaluator=evaluator) != 0
    assert not list(tmp_path.iterdir())
    capsys.readouterr()
    directory = tmp_path / "eval-store"
    assert main(["--persist", "--store-dir", str(directory)], env=env, evaluator=evaluator) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["dry_run"] is False
    assert len(EvalStore(directory).verify_chain()) == 1
