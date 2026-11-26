# Copyright 2026 Celina contributors.
"""Explicit-state, default-off integration facade for self-improvement."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits
from celina_pkg.selfimprove.flags import load_flags

from .eval_adapter import CelinaOfflineEvaluator

if TYPE_CHECKING:
    from collections.abc import Sequence

    from celina_pkg.selfimprove.contracts import TaskSpec
    from celina_pkg.selfimprove.flags import SelfImproveFlags
    from celina_pkg.selfimprove.l3.suite import EvalTask
    from celina_pkg.selfimprove.l6.optimizer import OfflineOptimizer
    from celina_pkg.selfimprove.l6.promoter import ArtifactPromoter
    from celina_pkg.selfimprove.l6.types import (
        OptimizationResult,
        PromotionOutcome,
        PromotionProposal,
    )
    from celina_pkg.selfimprove.l7.patcher import SafeSelfPatcher
    from celina_pkg.selfimprove.l7.types import PatchOutcome, PatchProposal


_MAX_OUTPUT = 100_000


class SelfImproveRuntime:
    """Bind trusted configuration; construction does not touch state or HOME."""

    def __init__(self, flags: SelfImproveFlags, state_dir: Path | None = None) -> None:
        """Bind flags and explicit state path without importing protected modules."""
        self.flags, self.state_dir = flags, state_dir

    def enabled_for(self, operation: str) -> bool:
        """Check master and the corresponding feature switch without side effects."""
        names = {"eval": "eval_harness", "optimize": "optimize", "patch": "patcher"}
        return (
            self.flags.enabled
            and operation in names
            and bool(getattr(self.flags, names[operation]))
        )

    def _state(self) -> Path:
        if self.state_dir is None:
            msg = "active commands require --state-dir"
            raise ValueError(msg)
        guard = BudgetGuard(BudgetLimits(1, 1, 1, 1, 1))
        evaluator = CelinaOfflineEvaluator(self.state_dir, self.flags, guard)
        return evaluator._directory()  # noqa: SLF001  # Share the verified state boundary.

    def evaluate(self) -> dict[str, object]:
        """Run and persist a 24-task offline evaluation in an isolated worker."""
        parent = load_flags()
        if not self.enabled_for("eval") or not parent.enabled or not parent.eval_harness:
            msg = "evaluation disabled"
            raise PermissionError(msg)
        state = self._state()
        env = {
            "HOME": str(state / "runtime-home"), "TARGET_HOME": str(state / "runtime-target"),
            "CELINA_STORAGE_DIR": str(state / "runtime-storage"),
            "SELFIMPROVE_STATE_DIR": str(state),
            "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "DRY_RUN": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
            "SELFIMPROVE_ENABLED": os.environ.get("SELFIMPROVE_ENABLED", "0"),
            "SELFIMPROVE_EVAL_HARNESS": os.environ.get("SELFIMPROVE_EVAL_HARNESS", "0"),
        }
        result = subprocess.run(  # Fixed interpreter and trusted runtime module.
            [sys.executable, "-m", "celina_pkg.selfimprove.wiring._runtime_eval"], env=env,
            cwd=Path(__file__).resolve().parents[5], timeout=120, check=False,
            text=True, capture_output=True,
        )
        if result.returncode != 0 or len(result.stdout) > _MAX_OUTPUT:
            msg = f"evaluation worker failed: {result.stderr[-500:]}"
            raise RuntimeError(msg)
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            msg = "invalid evaluation worker response"
            raise TypeError(msg)
        return cast("dict[str, object]", value)

    def _require(self, operation: str) -> Path:
        if not self.enabled_for(operation):
            msg = f"{operation} disabled"
            raise PermissionError(msg)
        return self._state()

    @staticmethod
    def _within(path: Path, state: Path) -> None:
        if (
            not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or path == state or not path.is_relative_to(state)
            or any(part.is_symlink() for part in (path, *path.parents))
        ):
            msg = "collaborator state must be inside the explicit state directory"
            raise ValueError(msg)

    def optimize(
        self, optimizer: OfflineOptimizer, artifact_id: str, tasks: Sequence[EvalTask],
    ) -> OptimizationResult:
        """Run the host-configured optimizer against state confined to this runtime."""
        state = self._require("optimize")
        from celina_pkg.selfimprove.l6.optimizer import OfflineOptimizer  # noqa: PLC0415

        if not isinstance(optimizer, OfflineOptimizer) or optimizer.flags != self.flags:
            msg = "optimizer flags or type mismatch"
            raise ValueError(msg)
        self._within(optimizer.registry.root_dir, state)
        return optimizer.optimize(artifact_id, tasks)

    def _promoter(self, promoter: ArtifactPromoter, state: Path) -> None:
        from celina_pkg.selfimprove.l6.promoter import ArtifactPromoter  # noqa: PLC0415

        from .approval_adapter import PromotionApprovalAdapter  # noqa: PLC0415

        if not isinstance(promoter, ArtifactPromoter) or promoter.flags != self.flags:
            msg = "promoter flags or type mismatch"
            raise ValueError(msg)
        self._within(promoter.registry.root_dir, state)
        gate = promoter.approval_gate
        if gate is not None and (
            not isinstance(gate, PromotionApprovalAdapter)
            or gate.store.state_dir != state / "approvals"
        ):
            msg = "promotion approval must use the runtime durable store"
            raise ValueError(msg)

    def promote(self, promoter: ArtifactPromoter, proposal: PromotionProposal) -> PromotionOutcome:
        """Promote only through the trusted host's durable approval adapter."""
        state = self._require("optimize")
        self._promoter(promoter, state)
        return promoter.promote(proposal)

    def rollback(
        self, promoter: ArtifactPromoter, artifact_id: str, *, reason: str,
        target_version: int | None = None,
    ) -> PromotionOutcome:
        """Roll back an active artifact; do not reissue or reset an approval."""
        state = self._require("optimize")
        self._promoter(promoter, state)
        return promoter.rollback(artifact_id, reason=reason, target_version=target_version)

    def patch(
        self, patcher: SafeSelfPatcher, proposal: PatchProposal, task: TaskSpec,
    ) -> PatchOutcome:
        """Apply L7 policy and L2 review, using a durable bundle-bound approval."""
        state = self._require("patch")
        from celina_pkg.selfimprove.l7.patcher import SafeSelfPatcher  # noqa: PLC0415

        from .approval_adapter import PatchApprovalAdapter  # noqa: PLC0415

        if not isinstance(patcher, SafeSelfPatcher) or patcher.flags != self.flags:
            msg = "patcher flags or type mismatch"
            raise ValueError(msg)
        self._within(patcher.manager.worktree_parent, state)
        self._within(patcher.bundle_dir, state)
        gate = patcher.approval_gate
        if gate is not None and (
            not isinstance(gate, PatchApprovalAdapter)
            or gate.store.state_dir != state / "approvals"
        ):
            msg = "patch approval must use the runtime durable store"
            raise ValueError(msg)
        return patcher.run(proposal, task)
