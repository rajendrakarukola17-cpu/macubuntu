# Copyright 2026 Celina contributors.
"""Flag-gated isolated offline adapter for the L3 Celina benchmark."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.contracts import EvalReport
from celina_pkg.selfimprove.l3.suite import load_benchmark_suite

from ._eval_probes import Diagnostic

if TYPE_CHECKING:
    from celina_pkg.selfimprove.budget import BudgetGuard
    from celina_pkg.selfimprove.flags import SelfImproveFlags
    from celina_pkg.selfimprove.l3.suite import EvalTask


_PRIVATE_DIR = 0o700
_MAX_OUTPUT = 100_000


class CelinaOfflineEvaluator:
    """Measure real offline Celina components in a fresh, isolated worker process.

    The caller must provide a trusted private state directory. A Linux seccomp
    filter blocks network syscalls in the worker but is not a general sandbox.
    Repair verifier process outputs are simulated, not real test executions.
    """

    def __init__(self, state_dir: Path, flags: SelfImproveFlags, budget: BudgetGuard) -> None:
        """Bind dependencies without reading the environment or touching files."""
        self.state_dir, self.flags, self.budget = state_dir, flags, budget
        self.diagnostics: dict[str, Diagnostic] = {}

    def _directory(self) -> Path:
        path = self.state_dir
        if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
            msg = "invalid state directory"
            raise ValueError(msg)
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            msg = "state directory cannot contain symlinks"
            raise ValueError(msg)
        home = os.environ.get("HOME", "")
        if not home or not Path(home).is_absolute():
            msg = "HOME must be an explicit absolute path"
            raise ValueError(msg)
        if path.resolve().is_relative_to(Path(home).resolve()):
            msg = "state directory cannot be inside HOME"
            raise ValueError(msg)
        path.mkdir(mode=_PRIVATE_DIR, parents=True, exist_ok=True)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != _PRIVATE_DIR:
            msg = "state directory must be private"
            raise ValueError(msg)
        return path

    def _environment(self, state: Path) -> dict[str, str]:
        package_dir = Path(__file__).resolve().parents[3]
        return {
            "HOME": str(state / "isolated-home"), "TARGET_HOME": str(state / "target"),
            "CELINA_STORAGE_DIR": str(state / "storage"), "DRY_RUN": "1",
            "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(package_dir),
            "SELFIMPROVE_ENABLED": os.environ.get("SELFIMPROVE_ENABLED", "0"),
            "SELFIMPROVE_EVAL_HARNESS": os.environ.get("SELFIMPROVE_EVAL_HARNESS", "0"),
        }

    def _run(self) -> None:
        self.budget.consume_subprocess(2)
        self.budget.consume_file_write(32)
        state = self._directory()
        with tempfile.TemporaryDirectory(prefix="probe-", dir=state) as temporary:
            env = self._environment(Path(temporary))
            timeout = min(60.0, self.budget.snapshot().remaining_wall_seconds)
            result = subprocess.run(  # Fixed interpreter and trusted worker module.
                [sys.executable, "-m", "celina_pkg.selfimprove.wiring._eval_worker"],
                env=env, cwd=Path(__file__).resolve().parents[5], timeout=timeout,
                capture_output=True, text=True, check=False,
            )
            self.budget.check_wall_clock()
            if result.returncode != 0 or len(result.stdout) > _MAX_OUTPUT:
                msg = f"offline evaluator worker failed: {result.stderr[-500:]}"
                raise RuntimeError(msg)
            self.diagnostics = self._decode(result.stdout)

    @staticmethod
    def _decode(raw: str) -> dict[str, Diagnostic]:
        data = json.loads(raw)
        expected = {task.spec.task_id for task in load_benchmark_suite()}
        if not isinstance(data, dict) or set(data) != expected:
            msg = "invalid worker task set"
            raise ValueError(msg)
        result: dict[str, Diagnostic] = {}
        for key, value in data.items():
            if (
                not isinstance(value, dict) or set(value) != {"passed", "reason", "observed"}
                or type(value["passed"]) is not bool or not isinstance(value["reason"], str)
                or not value["reason"] or not isinstance(value["observed"], str)
            ):
                msg = "invalid worker observation"
                raise ValueError(msg)
            result[key] = Diagnostic(**value)
        return result

    def __call__(self, task: EvalTask) -> EvalReport:
        """Evaluate an exact bundled task; disabled or modified tasks fail closed."""
        if not self.flags.enabled or not self.flags.eval_harness:
            return EvalReport(task.spec.task_id, "skipped", 0.0, 0)
        canonical = {item.spec.task_id: item for item in load_benchmark_suite()}
        if canonical.get(task.spec.task_id) != task:
            return EvalReport(task.spec.task_id, "error", 0.0, 1)
        if not self.diagnostics:
            self._run()
        outcome = self.diagnostics[task.spec.task_id]
        return EvalReport(
            task.spec.task_id, "pass" if outcome.passed else "fail", float(outcome.passed), 1,
        )
