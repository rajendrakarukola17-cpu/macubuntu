# Copyright 2026 Celina contributors.
"""Offline optimizer tests using L3 evaluation and L1 budgets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from celina_pkg.evidence import EvidenceLedger
from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, EvalReport, TaskSpec
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l3.suite import EvalTask
from celina_pkg.selfimprove.l6.optimizer import OfflineOptimizer
from celina_pkg.selfimprove.l6.registry import ArtifactRegistry
from celina_pkg.selfimprove.l6.types import GeneratedCandidate, OptimizationConfig
from celina_pkg.selfimprove.ledger_bridge import LedgerBridge

if TYPE_CHECKING:
    from pathlib import Path

    from celina_pkg.selfimprove.l6.types import PromptArtifact

FLAGS = SelfImproveFlags(enabled=True, optimize=True, eval_harness=True)
EXPECTED_GENERATIONS = 3
EXPECTED_FACTORY_CALLS = 2
EXPECTED_ITERATIONS = 12
EXPECTED_TOKENS = 18
LIMITS = BudgetLimits(100, 1000, 60, 100, 0)
TASKS = tuple(
    EvalTask(TaskSpec(f"task-{n}", "offline test"), "routing", "expected", n == 0) for n in range(6)
)


class Generator:
    """Bounded deterministic candidates, with call counts."""

    def __init__(self, values: tuple[str, ...]) -> None:
        """Record fixture data."""
        self.values, self.calls = values, 0

    def generate(self, _baseline: PromptArtifact, index: int) -> GeneratedCandidate:
        """Return one bounded proposal without effects."""
        self.calls += 1
        return GeneratedCandidate(self.values[index], tokens=2)


class Evaluator:
    """Return deterministic reports and declared token consumption."""

    def __init__(self, content: str, *, veto: bool = False, error: bool = False) -> None:
        """Bind artifact content and test behavior."""
        self.content, self.veto, self.error = content, veto, error

    def evaluate(self, task: EvalTask) -> tuple[EvalReport, int]:
        """Evaluate one synthetic task without network or filesystem I/O."""
        if self.error:
            message = "synthetic evaluator failure"
            raise RuntimeError(message)
        passed = self.content != "baseline"
        if self.veto and task.security_critical:
            passed = not passed
        status = "pass" if passed else "fail"
        return EvalReport(task.spec.task_id, status, float(passed), 1), 1


class Factory:
    """Bind evaluators to explicit artifact content."""

    def __init__(self, *, veto: bool = False, error: bool = False) -> None:
        """Set deterministic behaviors."""
        self.veto, self.error, self.calls = veto, error, 0

    def create(self, artifact: PromptArtifact) -> Evaluator:
        """Record the invocation and return an offline evaluator."""
        self.calls += 1
        return Evaluator(artifact.content, veto=self.veto, error=self.error)


def registry(tmp_path: Path, flags: SelfImproveFlags = FLAGS) -> ArtifactRegistry:
    """Create an active baseline in an explicit private directory."""
    result = ArtifactRegistry(tmp_path / "registry", flags)
    result.register("main", "prompt", "baseline")
    result.activate("main", 1)
    return result


def test_blocked_flags_zero_calls_and_no_files(tmp_path: Path) -> None:
    """Blocked optimization never invokes generator, registry, evaluator or ledger."""
    for flags in (
        SelfImproveFlags(),
        SelfImproveFlags(enabled=True, optimize=False, eval_harness=True),
        SelfImproveFlags(enabled=True, optimize=True, eval_harness=False),
    ):
        root = tmp_path / "absent"
        store, generator, factory = (
            ArtifactRegistry(root, flags),
            Generator(("candidate",)),
            Factory(),
        )
        ledger = EvidenceLedger(tmp_path / "events", anchor_path=tmp_path / "anchor")
        optimizer = OfflineOptimizer(
            flags,
            BudgetGuard(LIMITS),
            store,
            generator,
            factory,
            bridge=LedgerBridge(ledger),
        )
        result = optimizer.optimize("main", TASKS)
        assert result.status == "blocked_by_flag"
        assert result.winner is result.proposal is None
        assert generator.calls == factory.calls == 0
        assert not root.exists()
        assert not ledger.ledger_path.exists()


def test_deduplicates_and_builds_proposal_without_activation(tmp_path: Path) -> None:
    """Only unique changes are evaluated; statistical winner stays inactive."""
    store = registry(tmp_path)
    generator = Generator(("baseline", "candidate", "candidate"))
    factory = Factory()
    config = OptimizationConfig(max_candidates=3, bootstrap_resamples=50)
    result = OfflineOptimizer(
        FLAGS,
        BudgetGuard(LIMITS),
        store,
        generator,
        factory,
        config=config,
    ).optimize("main", TASKS)
    assert result.status == "improved"
    assert result.winner is not None
    assert result.proposal is not None
    assert result.winner.comparison.approved_for_promotion
    assert result.winner.comparison.delta == 1.0
    assert len(result.evaluations) == 1
    assert generator.calls == EXPECTED_GENERATIONS
    assert factory.calls == EXPECTED_FACTORY_CALLS
    assert store.active("main").version == 1
    assert store.read("main", 2) is None
    assert result.budget.iterations == EXPECTED_ITERATIONS
    assert result.budget.tokens == EXPECTED_TOKENS


def test_security_veto_and_no_significant_gain(tmp_path: Path) -> None:
    """No winning proposal follows a security regression or no change."""
    store = registry(tmp_path)
    config = OptimizationConfig(max_candidates=1, bootstrap_resamples=50)
    outcome = OfflineOptimizer(
        FLAGS,
        BudgetGuard(LIMITS),
        store,
        Generator(("candidate",)),
        Factory(veto=True),
        config=config,
    ).optimize("main", TASKS)
    assert outcome.status == "no_improvement"
    assert outcome.winner is outcome.proposal is None
    assert outcome.evaluations[0].comparison.security_regression
    outcome = OfflineOptimizer(
        FLAGS,
        BudgetGuard(LIMITS),
        store,
        Generator(("baseline",)),
        Factory(),
        config=config,
    ).optimize("main", TASKS)
    assert outcome.status == "no_improvement"
    assert outcome.winner is outcome.proposal is None


@pytest.mark.parametrize(
    ("limits", "dimension"),
    [
        (BudgetLimits(2, 100, 60, 0, 0), "iterations"),
        (BudgetLimits(20, 2, 60, 0, 0), "tokens"),
    ],
)
def test_budget_exhaustion_returns_dimension_and_snapshot(
    tmp_path: Path,
    limits: BudgetLimits,
    dimension: str,
) -> None:
    """L3 task iterations and evaluator tokens share the exact L1 guard."""
    store = registry(tmp_path)
    result = OfflineOptimizer(
        FLAGS,
        BudgetGuard(limits),
        store,
        Generator(("candidate",)),
        Factory(),
        config=OptimizationConfig(max_candidates=1),
    ).optimize("main", TASKS)
    assert result.status == "failed_budget"
    assert result.budget_dimension == dimension
    assert result.winner is result.proposal is None


def test_evaluator_and_generator_exceptions_fail_closed(tmp_path: Path) -> None:
    """Failure cannot be confused with a successful benchmark."""
    store = registry(tmp_path)
    config = OptimizationConfig(max_candidates=1)
    result = OfflineOptimizer(
        FLAGS,
        BudgetGuard(LIMITS),
        store,
        Generator(("candidate",)),
        Factory(error=True),
        config=config,
    ).optimize("main", TASKS)
    assert result.status == "failed_error"
    assert result.winner is result.proposal is None

    class BrokenGenerator(Generator):
        """Generator deliberately fails."""

        def generate(self, _baseline: PromptArtifact, _index: int) -> GeneratedCandidate:
            """Fail without producing a candidate."""
            message = "synthetic"
            raise RuntimeError(message)

    result = OfflineOptimizer(
        FLAGS,
        BudgetGuard(LIMITS),
        store,
        BrokenGenerator(("x",)),
        Factory(),
        config=config,
    ).optimize("main", TASKS)
    assert result.status == "failed_error"


def test_optional_ledger_records_no_raw_content(tmp_path: Path) -> None:
    """Evidence uses only compact derived metadata and a valid chain."""
    store = registry(tmp_path)
    ledger = EvidenceLedger(tmp_path / "events", anchor_path=tmp_path / "anchor")
    candidate = "password=secretValue"
    result = OfflineOptimizer(
        FLAGS,
        BudgetGuard(LIMITS),
        store,
        Generator((candidate,)),
        Factory(),
        config=OptimizationConfig(max_candidates=1),
        bridge=LedgerBridge(ledger),
    ).optimize("main", TASKS)
    assert result.status == "improved"
    assert ledger.verify()[0]
    assert [x["event_type"] for x in ledger.list_entries()] == [
        "selfimprove.optimize.candidate",
        "selfimprove.optimize.complete",
    ]
    assert "secretValue" not in ledger.ledger_path.read_text()


def test_wall_budget_and_insignificant_candidate(tmp_path: Path) -> None:
    """Wall expiry fails closed and equal observed outcomes are not a gain."""
    store = registry(tmp_path)
    ticks = iter(float(n) for n in range(1000))

    def clock() -> float:
        return next(ticks)

    guard = BudgetGuard(BudgetLimits(100, 1000, 2, 0, 0), clock=clock)
    result = OfflineOptimizer(FLAGS, guard, store, Generator(("candidate",)), Factory()).optimize(
        "main",
        TASKS,
    )
    assert result.status == "failed_budget"
    assert result.budget_dimension == "wall_seconds"
    assert result.winner is None

    class FlatEvaluator(Evaluator):
        """Both variants produce identical failing outcomes."""

        def evaluate(self, task: EvalTask) -> tuple[EvalReport, int]:
            """Return the same result for every artifact."""
            return EvalReport(task.spec.task_id, "fail", 0, 1), 1

    class FlatFactory(Factory):
        """Return flat evaluations for any artifact."""

        def create(self, artifact: PromptArtifact) -> Evaluator:
            """Bind a flat evaluator."""
            return FlatEvaluator(artifact.content)

    result = OfflineOptimizer(
        FLAGS,
        BudgetGuard(LIMITS),
        store,
        Generator(("candidate",)),
        FlatFactory(),
        config=OptimizationConfig(max_candidates=1),
    ).optimize("main", TASKS)
    assert result.status == "no_improvement"
    assert result.evaluations[0].comparison.statistically_significant is False
    assert result.proposal is None


def test_runner_template_cannot_supply_a_separate_verification_budget(tmp_path: Path) -> None:
    """A template gate with a different guard must be rejected before execution."""
    from celina_pkg.selfimprove.l2.gate import VerificationGate  # noqa: PLC0415  # Host setup.
    from celina_pkg.selfimprove.l3.runner import EvalRunner  # noqa: PLC0415  # Host setup.

    store = registry(tmp_path)
    guard = BudgetGuard(LIMITS)
    other = BudgetGuard(LIMITS)
    gate = VerificationGate(FLAGS, other, ())
    runner = EvalRunner(
        FLAGS,
        guard,
        lambda task: EvalReport(task.spec.task_id, "fail", 0, 1),
        gate=gate,
    )
    with pytest.raises(ValueError, match="budget"):
        OfflineOptimizer(FLAGS, guard, store, Generator(("x",)), Factory(), runner_template=runner)


def test_code_repair_gate_budget_keeps_exact_dimension(tmp_path: Path) -> None:
    """Preserve verifier-side L1 dimension even though L3 otherwise obscures it."""
    from celina_pkg.selfimprove.l2.gate import VerificationGate  # noqa: PLC0415  # Host setup.
    from celina_pkg.selfimprove.l3.runner import EvalRunner  # noqa: PLC0415  # Host setup.

    flags = SelfImproveFlags(enabled=True, optimize=True, eval_harness=True, verifiers=True)
    store = registry(tmp_path, flags)
    guard = BudgetGuard(BudgetLimits(10, 10, 60, 0, 0))

    class SubprocessVerifier:
        """Synthetic verifier that exceeds its shared subprocess budget."""

        def verify(self, _task: TaskSpec) -> object:
            """Consume the unavailable subprocess unit."""
            guard.consume_subprocess()
            message = "unreachable"
            raise AssertionError(message)

    class PassingEvaluator(Evaluator):
        """Return a pass so L3 invokes the configured gate."""

        def evaluate(self, task: EvalTask) -> tuple[EvalReport, int]:
            """Return a successful code-repair report."""
            return EvalReport(task.spec.task_id, "pass", 1, 1), 0

    class PassingFactory(Factory):
        """Produce passing evaluators."""

        def create(self, artifact: PromptArtifact) -> Evaluator:
            """Bind the passing evaluator."""
            return PassingEvaluator(artifact.content)

    gate = VerificationGate(flags, guard, (SubprocessVerifier(),))
    template = EvalRunner(
        flags,
        guard,
        lambda task: EvalReport(task.spec.task_id, "pass", 1, 1),
        gate=gate,
    )
    task = EvalTask(TaskSpec("code", "repair"), "code_repair", "correct", security_critical=False)
    result = OfflineOptimizer(
        flags,
        guard,
        store,
        Generator(("candidate",)),
        PassingFactory(),
        runner_template=template,
    ).optimize("main", (task,))
    assert result.status == "failed_budget"
    assert result.budget_dimension == "subprocesses"


def test_failed_evaluation_emits_compact_completion_when_budget_allows(tmp_path: Path) -> None:
    """An evaluator error emits failure metadata but never a promotion candidate."""
    store = registry(tmp_path)
    ledger = EvidenceLedger(tmp_path / "events", anchor_path=tmp_path / "anchor")
    result = OfflineOptimizer(
        FLAGS,
        BudgetGuard(LIMITS),
        store,
        Generator(("candidate",)),
        Factory(error=True),
        config=OptimizationConfig(max_candidates=1),
        bridge=LedgerBridge(ledger),
    ).optimize("main", TASKS)
    assert result.status == "failed_error"
    assert [entry["event_type"] for entry in ledger.list_entries()] == [
        "selfimprove.optimize.complete",
    ]
    assert ledger.verify()[0]
