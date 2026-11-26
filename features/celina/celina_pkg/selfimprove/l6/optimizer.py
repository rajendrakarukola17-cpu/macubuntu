# Copyright 2026 Celina contributors.
"""Offline L3-backed candidate evaluation with shared L1 accounting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from celina_pkg.selfimprove.budget import BudgetExceeded
from celina_pkg.selfimprove.l3.runner import EvalRunner
from celina_pkg.selfimprove.l3.stats import compare_eval_runs

from ._gate_adapter import GateBudgetAdapter
from .types import (
    CandidateEvaluation,
    GeneratedCandidate,
    OptimizationConfig,
    OptimizationResult,
    PromotionProposal,
    PromptArtifact,
    content_digest,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from celina_pkg.selfimprove.budget import BudgetGuard
    from celina_pkg.selfimprove.contracts import EvalReport
    from celina_pkg.selfimprove.flags import SelfImproveFlags
    from celina_pkg.selfimprove.l3.stats import EvalRun
    from celina_pkg.selfimprove.l3.suite import EvalTask
    from celina_pkg.selfimprove.ledger_bridge import LedgerBridge

    from .registry import ArtifactRegistry
    from .types import (
        ArtifactEvaluatorFactoryProtocol,
        ArtifactEvaluatorProtocol,
        CandidateGeneratorProtocol,
    )


class _AccountedEvaluator:
    """Adapt a host evaluator's token declaration to L3's L0 report protocol."""

    def __init__(self, evaluator: ArtifactEvaluatorProtocol, budget: BudgetGuard) -> None:
        self.evaluator, self.budget = evaluator, budget

    def __call__(self, task: EvalTask) -> EvalReport:
        result, tokens = self.evaluator.evaluate(task)
        self.budget.consume_tokens(tokens)
        return result


class OfflineOptimizer:
    """Statistical selection only; it never registers or activates a candidate.

    Generator, evaluator, L3 benchmark classifications and an optional gate are
    host-trusted. Reported token counts are not independently attested; callbacks
    and bootstrap computations cannot be preempted mid-call.
    """

    def __init__(  # noqa: PLR0913  # Explicit host-controlled collaborators.
        self,
        flags: SelfImproveFlags,
        budget: BudgetGuard,
        registry: ArtifactRegistry,
        generator: CandidateGeneratorProtocol,
        factory: ArtifactEvaluatorFactoryProtocol,
        *,
        config: OptimizationConfig | None = None,
        bridge: LedgerBridge | None = None,
        runner_template: EvalRunner | None = None,
    ) -> None:
        """Bind explicit trusted collaborators and one shared L1 guard."""
        if registry.flags != flags:
            message = "registry and optimizer must use the same flags"
            raise ValueError(message)
        if runner_template is not None and (
            runner_template.flags != flags or runner_template.budget is not budget
        ):
            message = "runner must use shared flags and budget"
            raise ValueError(message)
        if runner_template is not None and runner_template.gate is not None:
            gate = runner_template.gate
            if gate.budget is not budget or gate.flags != flags:
                message = "verification gate must use shared flags and budget"
                raise ValueError(message)
        self.flags, self.budget, self.registry = flags, budget, registry
        self.generator, self.factory = generator, factory
        self.config, self.bridge, self.template = (
            config or OptimizationConfig(),
            bridge,
            runner_template,
        )

    def optimize(self, artifact_id: str, tasks: Sequence[EvalTask]) -> OptimizationResult:
        """Evaluate a baseline and unique candidates; never change the active pointer."""
        if not self.flags.enabled or not self.flags.optimize or not self.flags.eval_harness:
            return self._outcome("blocked_by_flag")
        evaluations: list[CandidateEvaluation] = []
        try:
            self.budget.check_wall_clock()
            baseline = self.registry.active(artifact_id)
            if baseline is None:
                return self._failed("failed_error", evaluations)
            baseline_run = self._run(baseline, "baseline", tasks)
            seen = {baseline.content_hash}
            for index in range(self.config.max_candidates):
                self.budget.check_wall_clock()
                candidate = self.generator.generate(baseline, index)
                self.budget.check_wall_clock()
                self._validate_candidate(candidate)
                self.budget.consume_tokens(candidate.tokens)
                digest = content_digest(candidate.content)
                if digest in seen:
                    continue
                seen.add(digest)
                evaluations.append(self._candidate(baseline, baseline_run, candidate, tasks, index))
            return self._select(baseline, evaluations)
        except BudgetExceeded as exc:
            return self._failed("failed_budget", evaluations, dimension=exc.dimension)
        except Exception:
            return self._failed("failed_error", evaluations)

    @staticmethod
    def _validate_candidate(candidate: GeneratedCandidate) -> None:
        if not isinstance(candidate, GeneratedCandidate):
            message = "invalid generated candidate"
            raise TypeError(message)

    def _run(self, artifact: PromptArtifact, run_id: str, tasks: Sequence[EvalTask]) -> EvalRun:
        self.budget.check_wall_clock()
        evaluator = self.factory.create(artifact)
        self.budget.check_wall_clock()
        gate = self.template.gate if self.template else None
        # L3 uses only verify(); the typed structural adapter preserves budget errors.
        adapted_gate = cast("Any", GateBudgetAdapter(gate)) if gate is not None else None
        runner = EvalRunner(
            self.flags,
            self.budget,
            _AccountedEvaluator(evaluator, self.budget),
            gate=adapted_gate,
        )
        result = runner.run(run_id, tasks)
        if not result.complete:
            if result.blocked_reason.startswith("budget_exceeded:"):
                raise BudgetExceeded(result.blocked_reason.removeprefix("budget_exceeded:"), 0, 1)
            message = "incomplete evaluation"
            raise ValueError(message)
        if any(x.report.status not in ("pass", "fail") for x in result.outcomes):
            message = "evaluation returned error or skipped task"
            raise ValueError(message)
        return result

    def _candidate(
        self,
        baseline: PromptArtifact,
        baseline_run: EvalRun,
        candidate: GeneratedCandidate,
        tasks: Sequence[EvalTask],
        index: int,
    ) -> CandidateEvaluation:
        artifact = PromptArtifact(
            baseline.artifact_id,
            baseline.kind,
            baseline.version + 1,
            candidate.content,
        )
        run = self._run(artifact, f"candidate-{index}", tasks)
        self.budget.check_wall_clock()
        comparison = compare_eval_runs(
            baseline_run,
            run,
            seed=self.config.bootstrap_seed,
            resamples=self.config.bootstrap_resamples,
            alpha=self.config.alpha,
        )
        self.budget.check_wall_clock()
        evaluation = CandidateEvaluation(candidate, run, comparison)
        self._event(
            "selfimprove.optimize.candidate",
            {
                "hash": content_digest(candidate.content),
                "delta": comparison.delta,
                "significant": comparison.statistically_significant,
                "security_regression": comparison.security_regression,
            },
        )
        return evaluation

    def _select(
        self,
        baseline: PromptArtifact,
        evaluations: list[CandidateEvaluation],
    ) -> OptimizationResult:
        eligible = [
            x
            for x in evaluations
            if x.comparison.approved_for_promotion and not x.comparison.security_regression
        ]
        winner = max(eligible, key=lambda x: x.comparison.delta) if eligible else None
        status = "improved" if winner is not None else "no_improvement"
        proposal = (
            PromotionProposal(baseline, winner.candidate, winner.comparison) if winner else None
        )
        self._event(
            "selfimprove.optimize.complete",
            {"status": status, "candidates": len(evaluations)},
        )
        return OptimizationResult(
            status,
            winner,
            proposal,
            tuple(evaluations),
            self.budget.snapshot(),
        )

    def _event(self, name: str, payload: object) -> None:
        if self.bridge is not None:
            self.budget.consume_file_write()
            self.bridge.record(name, payload, flags=self.flags)
            self.budget.check_wall_clock()

    def _failed(
        self,
        status: str,
        evaluations: list[CandidateEvaluation],
        *,
        dimension: str = "",
    ) -> OptimizationResult:
        try:
            self._event(
                "selfimprove.optimize.complete", {"status": status, "candidates": len(evaluations)},
            )
        except Exception:
            return self._outcome(status, evaluations, dimension=dimension)
        return self._outcome(status, evaluations, dimension=dimension)

    def _outcome(
        self,
        status: str,
        evaluations: list[CandidateEvaluation] | None = None,
        *,
        dimension: str = "",
    ) -> OptimizationResult:
        return OptimizationResult(
            status,
            None,
            None,
            tuple(evaluations or ()),
            self.budget.snapshot(),
            dimension,
        )
