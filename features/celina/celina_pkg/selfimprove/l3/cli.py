# Copyright 2026 Celina contributors.
"""Flag-gated CLI smoke runner and explicit host-injected evaluation entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.budget import BudgetExceeded, BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, EvalReport
from celina_pkg.selfimprove.flags import load_flags

from .runner import EvalRunner
from .store import EvalStore, EvalStoreIntegrityError
from .suite import EvalTask, load_benchmark_suite

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from celina_pkg.selfimprove.flags import SelfImproveFlags

    from .runner import TaskEvaluator
    from .stats import EvalRun


def _stub(task: EvalTask) -> EvalReport:
    """Mark synthetic smoke tasks skipped: no Celina behavior was evaluated."""
    return EvalReport(task.spec.task_id, "skipped", 0.0, 1)


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="L3 evaluation harness")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--suite", type=Path)
    parser.add_argument("--store-dir", type=Path)
    return parser.parse_args(argv)


def _execute(
    args: argparse.Namespace, flags: SelfImproveFlags, evaluator: TaskEvaluator | None,
) -> EvalRun:
    if args.dry_run and (args.persist or args.store_dir is not None):
        message = "dry-run cannot persist; do not pass --store-dir"
        raise ValueError(message)
    if args.persist != (args.store_dir is not None):
        message = "persistence requires both --persist and --store-dir"
        raise ValueError(message)
    if args.dry_run:
        configured: TaskEvaluator = _stub
    else:
        if evaluator is None:
            message = "only --dry-run is supported without a trusted host evaluator"
            raise ValueError(message)
        configured = evaluator
    if args.suite is not None and not args.suite.is_file():
        message = "cannot load benchmark suite"
        raise ValueError(message)
    tasks = load_benchmark_suite(args.suite)
    budget = BudgetGuard(BudgetLimits(len(tasks), 1, 30, 5 if args.persist else 0, 0))
    store = None
    if args.persist:
        budget.consume_file_write(3)
        store = EvalStore(args.store_dir)
    return EvalRunner(flags, budget, configured, store=store).run("cli-evaluation", tasks)


def main(
    argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None,
    evaluator: TaskEvaluator | None = None,
) -> int:
    """Run only with opt-in; non-dry execution requires a trusted host evaluator."""
    args = _arguments(argv)
    flags = load_flags(env=env)
    if not flags.enabled or not flags.eval_harness:
        sys.stderr.write("evaluation harness disabled\n")
        return 2
    try:
        report = _execute(args, flags, evaluator)
    except (ValueError, OSError, BudgetExceeded, EvalStoreIntegrityError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    sys.stdout.write(json.dumps({
        "dry_run": args.dry_run, "task_count": len(report.outcomes),
        "pass_rate": report.pass_rate, "complete": report.complete,
    }) + "\n")
    return 0 if report.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
