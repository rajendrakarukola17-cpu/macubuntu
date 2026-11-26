# Copyright 2026 Celina contributors.
"""Fail-closed master switch propagation at the offline worker boundary."""

from __future__ import annotations

import os
import subprocess
import sys
from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest

from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.wiring import facade
from celina_pkg.selfimprove.wiring.eval_adapter import CelinaOfflineEvaluator

if TYPE_CHECKING:
    from pathlib import Path

FLAGS = SelfImproveFlags(enabled=True, eval_harness=True)
DISABLED_EXIT = 2


def test_disabled_parent_cannot_launch_facade_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even stale enabled flags cannot override an explicitly disabled parent."""
    monkeypatch.setenv("SELFIMPROVE_ENABLED", "0")
    monkeypatch.setenv("SELFIMPROVE_EVAL_HARNESS", "1")
    monkeypatch.setattr(facade.subprocess, "run", lambda *_a, **_k: pytest.fail("worker spawned"))
    state = tmp_path / "state"
    with pytest.raises(PermissionError):
        facade.SelfImproveRuntime(FLAGS, state).evaluate()
    assert not state.exists()


def test_facade_inherits_explicit_parent_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child receives the exact opt-in values, not forced enablement."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SELFIMPROVE_ENABLED", "yes")
    monkeypatch.setenv("SELFIMPROVE_EVAL_HARNESS", "on")
    captured: dict[str, object] = {}
    def fake_run(*_args: object, **kwargs: object) -> CompletedProcess[str]:
        captured.update(kwargs)
        return CompletedProcess([], 0, '{"complete": true}', "")
    monkeypatch.setattr(facade.subprocess, "run", fake_run)
    assert facade.SelfImproveRuntime(FLAGS, tmp_path / "state").evaluate() == {"complete": True}
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["SELFIMPROVE_ENABLED"] == "yes"
    assert env["SELFIMPROVE_EVAL_HARNESS"] == "on"


def test_evaluator_grandchild_does_not_reenable_disabled_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second worker boundary passes disabled master through unchanged."""
    monkeypatch.setenv("SELFIMPROVE_ENABLED", "0")
    monkeypatch.setenv("SELFIMPROVE_EVAL_HARNESS", "1")
    evaluator = CelinaOfflineEvaluator(tmp_path, FLAGS, BudgetGuard(BudgetLimits(1, 1, 10, 0, 0)))
    env = evaluator._environment(tmp_path)  # noqa: SLF001
    assert env["SELFIMPROVE_ENABLED"] == "0"
    assert env["SELFIMPROVE_EVAL_HARNESS"] == "1"


def test_missing_parent_master_is_not_synthesized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent master switch does not silently become enabled in a child."""
    monkeypatch.delenv("SELFIMPROVE_ENABLED", raising=False)
    monkeypatch.setenv("SELFIMPROVE_EVAL_HARNESS", "1")
    evaluator = CelinaOfflineEvaluator(tmp_path, FLAGS, BudgetGuard(BudgetLimits(1, 1, 10, 0, 0)))
    assert evaluator._environment(tmp_path)["SELFIMPROVE_ENABLED"] == "0"  # noqa: SLF001


@pytest.mark.parametrize("module", ["_runtime_eval", "_eval_worker"])
def test_real_child_rejects_disabled_parent(tmp_path: Path, module: str) -> None:
    """Fresh workers exit before touching target state when the master is zero."""
    env = {
        "HOME": str(tmp_path), "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
        "SELFIMPROVE_ENABLED": "0", "SELFIMPROVE_EVAL_HARNESS": "1",
        "TARGET_HOME": str(tmp_path / "target"), "SELFIMPROVE_STATE_DIR": str(tmp_path / "state"),
    }
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and parameterized module names.
        [sys.executable, "-m", f"celina_pkg.selfimprove.wiring.{module}"],
        env=env, capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == DISABLED_EXIT
    assert not (tmp_path / "target").exists()
    assert not (tmp_path / "state").exists()
