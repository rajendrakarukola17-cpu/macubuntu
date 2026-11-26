# Copyright 2026 Celina contributors.
"""Fail-closed L6 edge cases and trust-boundary checks."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, EvalReport, TaskSpec, VerifierResult
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l2.gate import VerificationGate
from celina_pkg.selfimprove.l3.runner import EvalRunner
from celina_pkg.selfimprove.l3.stats import EvalComparison, EvalRun
from celina_pkg.selfimprove.l3.suite import EvalTask
from celina_pkg.selfimprove.l6._gate_adapter import GateBudgetAdapter
from celina_pkg.selfimprove.l6._registry_io import ArtifactIntegrityError, check_root
from celina_pkg.selfimprove.l6.optimizer import OfflineOptimizer
from celina_pkg.selfimprove.l6.promoter import ArtifactPromoter
from celina_pkg.selfimprove.l6.registry import ArtifactRegistry
from celina_pkg.selfimprove.l6.types import (
    CandidateEvaluation,
    GeneratedCandidate,
    OptimizationConfig,
    OptimizationResult,
    PromotionOutcome,
    PromotionProposal,
    PromptArtifact,
)

if TYPE_CHECKING:
    from pathlib import Path

FLAGS = SelfImproveFlags(enabled=True, optimize=True, eval_harness=True, verifiers=True)
LIMITS = BudgetLimits(100, 100, 60, 10, 0)
GOOD = EvalComparison(
    1,
    0.5,
    1,
    improved=True,
    statistically_significant=True,
    security_regression=False,
    approved_for_promotion=True,
)
TASK = EvalTask(TaskSpec("t1", "goal"), "routing", "correct", security_critical=False)


class V:
    """Successful host verifier."""

    def verify(self, _task: TaskSpec) -> VerifierResult:
        """Return independent success."""
        return VerifierResult("test", "pass", 1, "ok")


class E:
    """Minimal host evaluator."""

    def evaluate(self, task: EvalTask) -> tuple[EvalReport, int]:
        """Return deterministic fail and no tokens."""
        return EvalReport(task.spec.task_id, "fail", 0, 1), 0


class F:
    """Construct a deterministic evaluator."""

    def create(self, _artifact: PromptArtifact) -> E:
        """Return a fake evaluator."""
        return E()


class G:
    """Generator used in edge tests."""

    def generate(self, _baseline: PromptArtifact, _index: int) -> GeneratedCandidate:
        """Return one deterministic candidate."""
        return GeneratedCandidate("different")


def seeded(tmp_path: Path) -> ArtifactRegistry:
    """Make a single active baseline."""
    store = ArtifactRegistry(tmp_path / "registry", FLAGS)
    store.register("main", "prompt", "base")
    store.activate("main", 1)
    return store


def test_gate_adapter_preserves_normal_verifier_decisions() -> None:
    """A successful underlying host gate is passed through unchanged."""
    guard = BudgetGuard(LIMITS)
    gate = VerificationGate(FLAGS, guard, (V(),))
    assert GateBudgetAdapter(gate).verify(TASK.spec).passed


def test_bad_paths_files_and_metadata_fail_closed(tmp_path: Path) -> None:
    """Reject relative paths, nonprivate files and malformed saved objects."""
    with pytest.raises(ArtifactIntegrityError, match="absolute"):
        check_root(tmp_path.relative_to(tmp_path.parent))
    store = seeded(tmp_path)
    with pytest.raises(ValueError, match="version"):
        store.read("main", 0)
    path = store.root_dir / "main.v1.json"
    path.chmod(0o644)
    with pytest.raises(ArtifactIntegrityError):
        store.read("main", 1)
    path.chmod(0o600)
    path.write_text("[]")
    with pytest.raises(ArtifactIntegrityError):
        store.read("main", 1)


def test_bad_identity_pointer_head_and_missing_version(tmp_path: Path) -> None:
    """Validate stored identity, pointer shape and immutable sequence metadata."""
    store = seeded(tmp_path)
    version = store.root_dir / "main.v1.json"
    original = version.read_text()
    item = json.loads(original)
    item["artifact_id"] = "other"
    version.write_text(json.dumps(item))
    with pytest.raises(ArtifactIntegrityError, match="identity"):
        store.read("main", 1)
    version.write_text(original)
    pointer = store.root_dir / "main.active.json"
    pointer.write_text('{"active": 0, "previous": null}')
    with pytest.raises(ArtifactIntegrityError, match="pointer"):
        store.active("main")
    pointer.write_text('{"active": 1, "previous": null}')
    with pytest.raises(ArtifactIntegrityError, match="does not exist"):
        store.activate("main", 9)
    head = store.root_dir / "main.head.json"
    head.unlink()
    with pytest.raises(ArtifactIntegrityError, match="head"):
        store.register("main", "prompt", "new")
    head.write_text('{"latest": 0}')
    head.chmod(0o600)
    with pytest.raises(ArtifactIntegrityError, match="head"):
        store.register("main", "prompt", "new")


def test_optimizer_checks_configuration_and_missing_baseline(tmp_path: Path) -> None:
    """Optimizer rejects mismatched dependencies and missing active artifacts."""
    store = seeded(tmp_path)
    guard = BudgetGuard(LIMITS)
    different = SelfImproveFlags(enabled=True, optimize=True)
    with pytest.raises(ValueError, match="flags"):
        OfflineOptimizer(different, guard, store, G(), F())
    runner = EvalRunner(
        FLAGS, BudgetGuard(LIMITS), lambda t: EvalReport(t.spec.task_id, "fail", 0, 1),
    )
    with pytest.raises(ValueError, match="budget"):
        OfflineOptimizer(FLAGS, guard, store, G(), F(), runner_template=runner)
    result = OfflineOptimizer(FLAGS, guard, store, G(), F()).optimize("missing", (TASK,))
    assert result.status == "failed_error"
    assert result.winner is None


def test_invalid_generator_and_incomplete_runner_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed candidates and non-budget incomplete runs never win."""
    store = seeded(tmp_path)

    class BadGenerator(G):
        """A malformed host generator."""

        def generate(self, _baseline: PromptArtifact, _index: int) -> object:
            """Return a non-candidate."""
            return None

    result = OfflineOptimizer(FLAGS, BudgetGuard(LIMITS), store, BadGenerator(), F()).optimize(
        "main", (TASK,),
    )
    assert result.status == "failed_error"
    monkeypatch.setattr(
        EvalRunner,
        "run",
        lambda _self, run_id, _tasks: EvalRun(
            run_id, (), complete=False, blocked_reason="host_failure",
        ),
    )
    result = OfflineOptimizer(FLAGS, BudgetGuard(LIMITS), store, G(), F()).optimize("main", (TASK,))
    assert result.status == "failed_error"


def test_promoter_rejects_invalid_stats_missing_approval_and_disabled_rollback(
    tmp_path: Path,
) -> None:
    """Approval exceptions and nonsensical numerical comparisons fail closed."""
    store = seeded(tmp_path)
    baseline = store.active("main")
    assert baseline is not None
    proposal = PromotionProposal(baseline, GeneratedCandidate("different"), GOOD)
    disabled = SelfImproveFlags(enabled=True)
    promoter = ArtifactPromoter(disabled, ArtifactRegistry(store.root_dir, disabled))
    assert promoter.rollback("main", reason="test").status == "blocked_by_flag"
    with pytest.raises(ValueError, match="flags"):
        ArtifactPromoter(disabled, store)
    invalid = replace(proposal, comparison=replace(GOOD, delta=math.nan))
    assert ArtifactPromoter(FLAGS, store).promote(invalid).status == "rejected_eval_gate"

    class BrokenApproval:
        """Failing approval callback."""

        def approve(self, _proposal: PromotionProposal) -> object:
            """Raise a synthetic failure."""
            message = "cannot ask human"
            raise RuntimeError(message)

    result = ArtifactPromoter(FLAGS, store, approval_gate=BrokenApproval()).promote(proposal)
    assert result.status == "rejected_by_approver"
    assert store.active("main") == baseline


def test_dto_edge_bounds_and_invariants() -> None:
    """Immutable DTOs reject bad seeds, winner types, dimensions and statuses."""
    with pytest.raises(ValueError, match="seed"):
        OptimizationConfig(bootstrap_seed=True)
    baseline = PromptArtifact("main", "prompt", 1, "same")
    with pytest.raises(ValueError, match="baseline"):
        PromotionProposal(baseline, GeneratedCandidate("same"), GOOD)
    snapshot = BudgetGuard(LIMITS).snapshot()
    with pytest.raises(ValueError, match="evaluations"):
        OptimizationResult("no_improvement", None, None, ("bad",), snapshot)
    with pytest.raises(ValueError, match="dimension"):
        OptimizationResult("failed_budget", None, None, (), snapshot)
    with pytest.raises(ValueError, match="winner"):
        OptimizationResult("improved", None, None, (), snapshot)
    with pytest.raises(ValueError, match="active version"):
        PromotionOutcome(status="promoted", promoted=True)
    with pytest.raises(TypeError, match="evaluation"):
        CandidateEvaluation(GeneratedCandidate("x"), "not a run", GOOD)
