# Copyright 2026 Celina contributors.
"""Evaluator state, malformed worker output and budget failure boundaries."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from typing import TYPE_CHECKING

import pytest

from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l3.runner import EvalRunner
from celina_pkg.selfimprove.l3.suite import load_benchmark_suite
from celina_pkg.selfimprove.wiring import eval_adapter
from celina_pkg.selfimprove.wiring._eval_probes import Diagnostic
from celina_pkg.selfimprove.wiring.eval_adapter import CelinaOfflineEvaluator

if TYPE_CHECKING:
    from pathlib import Path


_FLAGS = SelfImproveFlags(enabled=True, eval_harness=True)


def _guard() -> BudgetGuard:
    return BudgetGuard(BudgetLimits(24, 1, 30, 100, 10))


def test_eval_harness_disabled_is_side_effect_free(tmp_path: Path) -> None:
    """A disabled subflag avoids state creation and budget use even if master is on."""
    guard = _guard()
    evaluator = CelinaOfflineEvaluator(tmp_path / "state", SelfImproveFlags(enabled=True), guard)
    assert evaluator(load_benchmark_suite()[0]).status == "skipped"
    assert not (tmp_path / "state").exists()
    assert guard.snapshot().subprocesses == 0


def test_unsafe_state_directories_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absolute, non-symlinked private paths outside HOME are required."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    task = load_benchmark_suite()[0]
    with pytest.raises(ValueError, match="inside HOME"):
        CelinaOfflineEvaluator(home / "state", _FLAGS, _guard())(task)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    symlink = tmp_path / "link"
    symlink.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        CelinaOfflineEvaluator(symlink / "state", _FLAGS, _guard())(task)
    target.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        CelinaOfflineEvaluator(target, _FLAGS, _guard())(task)


def test_missing_home_fails_closed_without_touching_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing HOME cannot silently bypass the explicit isolation check."""
    monkeypatch.delenv("HOME")
    state = tmp_path / "state"
    with pytest.raises(ValueError, match="HOME"):
        CelinaOfflineEvaluator(state, _FLAGS, _guard())(load_benchmark_suite()[0])
    assert not state.exists()


def _valid_data() -> dict[str, object]:
    return {
        item.spec.task_id: asdict(Diagnostic(passed=True, reason="fixture", observed="x"))
        for item in load_benchmark_suite()
    }


@pytest.mark.parametrize("mode", ["failed", "oversized", "missing", "malformed"])
def test_worker_failures_and_bad_output_are_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    """Worker failure and malformed observations do not turn into benchmark passes."""
    data = _valid_data()
    if mode == "malformed":
        data[next(iter(data))] = {"passed": "yes", "reason": "x", "observed": "x"}
    raw = {"failed": "", "oversized": "x" * 100_001, "missing": "{}"}.get(mode, json.dumps(data))

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["worker"], 1 if mode == "failed" else 0, raw, "failure")

    monkeypatch.setattr(eval_adapter.subprocess, "run", run)
    budget = _guard()
    evaluator = CelinaOfflineEvaluator(tmp_path / "state", _FLAGS, budget)
    run_result = EvalRunner(_FLAGS, budget, evaluator).run("failed", load_benchmark_suite()[:1])
    assert run_result.outcomes[0].report.status == "error"


def test_worker_environment_excludes_inherited_pythonpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker imports cannot be redirected through arbitrary inherited paths."""
    monkeypatch.setenv("PYTHONPATH", "/unexpected/user/path")
    evaluator = CelinaOfflineEvaluator(tmp_path / "state", _FLAGS, _guard())
    env = evaluator._environment(tmp_path)  # noqa: SLF001  # Inspect isolated worker environment.
    assert "/unexpected/user/path" not in env["PYTHONPATH"]
