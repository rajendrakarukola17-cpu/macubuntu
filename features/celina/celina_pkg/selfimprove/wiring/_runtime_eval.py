# Copyright 2026 Celina contributors.
"""Opt-in offline runtime worker for evaluation and evidence persistence."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits
from celina_pkg.selfimprove.flags import SelfImproveFlags, load_flags
from celina_pkg.selfimprove.l2.gate import VerificationGate
from celina_pkg.selfimprove.l3.runner import EvalRunner
from celina_pkg.selfimprove.l3.store import EvalStore
from celina_pkg.selfimprove.l3.suite import load_benchmark_suite
from celina_pkg.selfimprove.ledger_bridge import LedgerBridge

from ._eval_offline import install_network_filter
from ._eval_security import _target
from ._runtime_verify import RepairFixtureVerifier
from .eval_adapter import CelinaOfflineEvaluator


def _enabled_flags() -> SelfImproveFlags:
    flags = load_flags()
    if not flags.enabled or not flags.eval_harness:
        msg = "evaluation disabled"
        raise PermissionError(msg)
    return flags


def _run(state: Path) -> dict[str, object]:
    flags = _enabled_flags()
    target = Path(os.environ.get("TARGET_HOME", ""))
    _target(target / "runtime-check")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    from celina_pkg import config  # noqa: PLC0415  # Opt-in and isolated paths checked.

    if target != config.HOME:
        msg = "Celina config target mismatch"
        raise ValueError(msg)
    from celina_pkg.evidence import EvidenceLedger  # noqa: PLC0415  # Opt-in and target checked.

    budget = BudgetGuard(BudgetLimits(24, 1, 90, 100, 12))
    budget.consume_file_write(8)
    ledger = EvidenceLedger(
        state / "evidence" / "ledger.jsonl", anchor_path=state / "evidence" / "anchor",
    )
    store = EvalStore(state / "evaluations", bridge=LedgerBridge(ledger))
    evaluator = CelinaOfflineEvaluator(state / "benchmark", flags, budget)
    # This gate only rechecks simulated benchmark fixtures; it does not enable
    # the host's general-purpose verifier feature or execute a submitted patch.
    fixture_flags = SelfImproveFlags(enabled=True, verifiers=True)
    gate = VerificationGate(
        fixture_flags, budget, [RepairFixtureVerifier(state / "repair-check", budget)],
    )
    run = EvalRunner(flags, budget, evaluator, store=store, gate=gate).run(
        "offline-benchmark", load_benchmark_suite(),
    )
    entries = store.verify_chain()
    valid_ledger, _, count = ledger.verify()
    return {
        "complete": run.complete, "blocked_reason": run.blocked_reason,
        "task_count": len(run.outcomes), "pass_rate": run.pass_rate,
        "security_pass_rate": run.security_pass_rate,
        "eval_chain_verified": bool(entries and entries[-1].run == run),
        "ledger_chain_verified": valid_ledger and count > 0,
        "diagnostics": {key: asdict(value) for key, value in evaluator.diagnostics.items()},
    }


def main() -> int:
    """Refuse disabled or unconfigured execution before installing any filter."""
    flags = load_flags()
    if not flags.enabled or not flags.eval_harness:
        return 2
    install_network_filter()
    state = Path(os.environ.get("SELFIMPROVE_STATE_DIR", ""))
    if not state.is_absolute():
        return 2
    result = _run(state)
    sys.stdout.write(json.dumps(result) + "\n")
    valid = result["complete"] and result["eval_chain_verified"] and result["ledger_chain_verified"]
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
