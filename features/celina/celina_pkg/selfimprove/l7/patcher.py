# Copyright 2026 Celina contributors.
"""Fail-closed L2/L3 review and human-approved patch bundle export."""

from __future__ import annotations

from typing import TYPE_CHECKING

from celina_pkg.selfimprove.budget import BudgetExceeded
from celina_pkg.selfimprove.l2.gate import VerificationGate
from celina_pkg.selfimprove.l3.runner import EvalRunner
from celina_pkg.selfimprove.l3.stats import compare_eval_runs

from ._audit import audit_payloads, record_audit
from ._bundle_io import bundle_hash, bundle_metadata, discard_bundle, export_bundle
from .policy import PolicyViolationError, validate_patch_proposal
from .types import PatchApproval, PatchOutcome
from .worktree import WorktreeCleanupError, WorktreeTargetError

if TYPE_CHECKING:
    from pathlib import Path

    from celina_pkg.selfimprove.contracts import TaskSpec
    from celina_pkg.selfimprove.flags import SelfImproveFlags
    from celina_pkg.selfimprove.l3.stats import EvalRun
    from celina_pkg.selfimprove.l3.suite import EvalTask
    from celina_pkg.selfimprove.ledger_bridge import LedgerBridge

    from .types import (
        PatchApprovalProtocol,
        PatchBundle,
        PatchProposal,
        WorktreeEvalFactory,
        WorktreeGateFactory,
    )
    from .worktree import ThrowawayWorktreeSession, WorktreeManager


class _BlockedError(Exception):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(status)


class SafeSelfPatcher:
    """Gate a patch in a disposable worktree; never merge or commit it.

    Host-provided factories, Git configuration and private directories are trusted.
    A Git worktree is not an OS sandbox; synchronous callbacks cannot be preempted.
    """

    def __init__(  # noqa: PLR0913  # Explicit host-owned security dependencies.
        self,
        flags: SelfImproveFlags,
        manager: WorktreeManager,
        gate_factory: WorktreeGateFactory,
        *,
        bundle_dir: Path,
        bridge: LedgerBridge,
        approval_gate: PatchApprovalProtocol | None = None,
        baseline: EvalRun | None = None,
        eval_factory: WorktreeEvalFactory | None = None,
        eval_tasks: tuple[EvalTask, ...] = (),
        require_statistical_significance: bool = False,
    ) -> None:
        """Bind dependencies without touching files, Git, clocks, or callbacks."""
        self.flags, self.manager, self.gate_factory = flags, manager, gate_factory
        self.bundle_dir, self.bridge, self.approval_gate = bundle_dir, bridge, approval_gate
        self.baseline, self.eval_factory, self.eval_tasks = baseline, eval_factory, eval_tasks
        self.require_statistical_significance = require_statistical_significance

    def _result(
        self,
        status: str,
        bundle: PatchBundle | None = None,
        dimension: str = "",
    ) -> PatchOutcome:
        return PatchOutcome(
            status,
            bundle is not None,
            bundle,
            self.manager.budget.snapshot(),
            dimension,
        )

    def run(self, proposal: PatchProposal, task: TaskSpec) -> PatchOutcome:
        """Review and package a proposal, or return an explicit fail-closed outcome."""
        if not self.flags.enabled or not self.flags.patcher:
            return self._result("blocked_by_flag")
        try:
            return self._run(proposal, task)
        except WorktreeCleanupError:
            status = "failed_cleanup"
        except (PolicyViolationError, WorktreeTargetError):
            status = "rejected_policy"
        except BudgetExceeded as exc:
            return self._result("failed_budget", dimension=exc.dimension)
        except _BlockedError as exc:
            status = exc.status
        except Exception:
            status = "failed_error"
        return self._result(status)

    def _run(self, proposal: PatchProposal, task: TaskSpec) -> PatchOutcome:
        m, budget = self.manager, self.manager.budget
        policy = validate_patch_proposal(proposal, m.manifest_path, m.config)
        budget.consume_iteration()
        with m.session(proposal) as session:
            self._verify(session, task)
            self._evaluate(session, proposal)
            metadata = bundle_metadata(proposal, session.base_commit, policy)
        digest = bundle_hash(metadata)
        self._approve(proposal, digest)
        payloads = audit_payloads(proposal, session.base_commit, digest, policy)
        budget.consume_file_write(3 + 2 * len(payloads))
        bundle = export_bundle(self.bundle_dir, m.repo, proposal, metadata, digest)
        try:
            budget.check_wall_clock()
            record_audit(self.bridge, self.flags, payloads)
            budget.check_wall_clock()
        except BaseException:
            discard_bundle(bundle)
            raise
        return self._result("bundle_ready", bundle)

    def _verify(self, session: ThrowawayWorktreeSession, task: TaskSpec) -> None:
        budget = self.manager.budget
        budget.check_wall_clock()
        gate = self.gate_factory.create(session.path, budget)
        if not isinstance(gate, VerificationGate) or gate.budget is not budget:
            msg = "failed_verification"
            raise _BlockedError(msg)
        decision = gate.verify(task)
        budget.check_wall_clock()
        if decision.blocked_reason == "budget_exceeded":
            dimension = decision.results[-1].detail if decision.results else "wall_seconds"
            raise BudgetExceeded(dimension, 0, 1)
        if not decision.passed:
            msg = "failed_verification"
            raise _BlockedError(msg)

    def _evaluate(self, session: ThrowawayWorktreeSession, proposal: PatchProposal) -> None:
        if self.baseline is None and self.eval_factory is None and not self.eval_tasks:
            if self.require_statistical_significance:
                msg = "failed_eval_regression"
                raise _BlockedError(msg)
            return
        if self.baseline is None or self.eval_factory is None or not self.eval_tasks:
            msg = "failed_eval_regression"
            raise _BlockedError(msg)
        budget = self.manager.budget
        budget.check_wall_clock()
        runner = self.eval_factory.create(session.path, budget)
        if (
            not isinstance(runner, EvalRunner)
            or runner.budget is not budget
            or runner.store is not None
        ):
            msg = "failed_eval_regression"
            raise _BlockedError(msg)
        run = runner.run(proposal.proposal_id, self.eval_tasks)
        budget.check_wall_clock()
        if not run.complete and run.blocked_reason.startswith("budget_exceeded:"):
            raise BudgetExceeded(run.blocked_reason.partition(":")[2], 0, 1)
        try:
            comparison = compare_eval_runs(self.baseline, run)
        except ValueError as exc:
            msg = "failed_eval_regression"
            raise _BlockedError(msg) from exc
        if (
            comparison.security_regression
            or comparison.delta < 0
            or (self.require_statistical_significance and not comparison.statistically_significant)
        ):
            msg = "failed_eval_regression"
            raise _BlockedError(msg)

    def _approve(self, proposal: PatchProposal, digest: str) -> None:
        if self.approval_gate is None:
            msg = "rejected_no_approval_gate"
            raise _BlockedError(msg)
        budget = self.manager.budget
        budget.check_wall_clock()
        decision = self.approval_gate.approve(proposal, digest)
        budget.check_wall_clock()
        if not isinstance(decision, PatchApproval) or decision.bundle_hash != digest:
            msg = "rejected_approval_mismatch"
            raise _BlockedError(msg)
        if not decision.approved:
            msg = "rejected_by_approver"
            raise _BlockedError(msg)
