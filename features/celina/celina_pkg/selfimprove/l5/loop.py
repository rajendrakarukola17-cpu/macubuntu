# Copyright 2026 Celina contributors.
"""Bounded plan, act, verify, critique, refine orchestration."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.budget import BudgetExceeded
from celina_pkg.selfimprove.contracts import AttemptRecord
from celina_pkg.selfimprove.l4.store import MemoryEpisode

from .critic import CriticAssessment, RubricCritic
from .types import (
    ActorContext,
    ActorProposal,
    ActorProtocol,
    ApprovalDecision,
    ApprovalGateProtocol,
    LoopConfig,
    LoopOutcome,
)

if TYPE_CHECKING:
    from celina_pkg.selfimprove.budget import BudgetGuard
    from celina_pkg.selfimprove.contracts import TaskSpec
    from celina_pkg.selfimprove.flags import SelfImproveFlags
    from celina_pkg.selfimprove.l2.gate import GateDecision, VerificationGate
    from celina_pkg.selfimprove.l4.retriever import MemoryRetriever
    from celina_pkg.selfimprove.l4.store import FailureFixStore
    from celina_pkg.selfimprove.ledger_bridge import LedgerBridge



__all__ = (
    "ActorContext", "ActorProposal", "ActorProtocol", "ApprovalDecision",
    "ApprovalGateProtocol", "LoopConfig", "LoopOutcome", "RefinementLoop",
)


class RefinementLoop:
    """Trusted in-process orchestration, not sandboxing or resource attestation.

    Actors and verifiers are trusted to report resources and not mutate while
    proposing. Synchronous callbacks cannot be preempted; wall time is checked
    around them. Isolation is the responsibility of the host/L7 boundary.
    """

    def __init__(  # noqa: PLR0913  # Explicit independently optional host integrations.
        self,
        flags: SelfImproveFlags,
        budget: BudgetGuard,
        gate: VerificationGate,
        actor: ActorProtocol,
        *,
        approval: ApprovalGateProtocol | None = None,
        retriever: MemoryRetriever | None = None,
        store: FailureFixStore | None = None,
        bridge: LedgerBridge | None = None,
        config: LoopConfig | None = None,
    ) -> None:
        """Bind explicit host-owned components with one shared budget and flags."""
        if gate.budget is not budget:
            message = "gate and loop must share one budget"
            raise ValueError(message)
        if gate.flags != flags:
            message = "gate and loop must share flags"
            raise ValueError(message)
        self.flags, self.budget, self.gate, self.actor = flags, budget, gate, actor
        self.approval, self.retriever, self.store = approval, retriever, store
        self.bridge, self.config, self.critic = bridge, config or LoopConfig(), RubricCritic()

    def run(self, task: TaskSpec) -> LoopOutcome:
        """Run until the independent gate passes or a hard bound stops work."""
        blocked = self._blocked()
        if blocked is not None:
            return LoopOutcome(blocked, 0)
        iterations, feedback, previous, streak = 0, "", "", 0
        failure: CriticAssessment | None = None
        try:
            memory = self._memory(task)
            while True:
                self.budget.consume_iteration()
                iterations += 1
                context = ActorContext(task, iterations, feedback, memory)
                proposal = self._propose(context)
                if not self._apply(task, proposal):
                    return self._finish("failed_approval", iterations, feedback)
                decision = self.gate.verify(task)
                if decision.blocked_reason == "budget_exceeded":
                    return self._gate_budget(decision, iterations, feedback)
                assessment = self._critique(task, proposal, decision, iterations)
                self._event(
                    "selfimprove.loop.attempt",
                    {"iteration": iterations, "passed": decision.passed},
                )
                if decision.passed and assessment.accepted:
                    self._remember(task, proposal, failure, iterations)
                    return self._finish("completed", iterations, assessment.result.feedback)
                feedback, failure = assessment.result.feedback, assessment
                streak = streak + 1 if assessment.failure_signature == previous else 1
                previous = assessment.failure_signature
                if streak >= self.config.stagnation_limit:
                    return self._finish("failed_stagnant", iterations, feedback)
        except BudgetExceeded as exc:
            return LoopOutcome("failed_budget", iterations, feedback, exc.dimension)

    def _blocked(self) -> str | None:
        if not self.flags.enabled or not self.flags.loop:
            return "blocked_by_flag"
        if not self.flags.verifiers or not self.gate.verifiers:
            return "failed_verification"
        return None

    def _memory(self, task: TaskSpec) -> str:
        if not self.flags.memory or self.retriever is None:
            return ""
        self.budget.check_wall_clock()
        result = self.retriever.retrieve(task.goal)
        self.budget.check_wall_clock()
        return result.context

    def _propose(self, context: ActorContext) -> ActorProposal:
        self.budget.check_wall_clock()
        proposal = self.actor.propose(context)
        self.budget.check_wall_clock()
        if not isinstance(proposal, ActorProposal):
            message = "actor must return an ActorProposal"
            raise TypeError(message)
        self.budget.consume_tokens(proposal.tokens)
        self.budget.consume_file_write(proposal.file_writes)
        self.budget.consume_subprocess(proposal.subprocesses)
        return proposal

    def _apply(self, task: TaskSpec, proposal: ActorProposal) -> bool:
        if not (proposal.requires_mutation or proposal.target_paths or proposal.file_writes):
            return True
        if self.approval is None:
            return False
        self.budget.check_wall_clock()
        decision = self.approval.approve(task, proposal)
        self.budget.check_wall_clock()
        if not isinstance(decision, ApprovalDecision) or not decision.approved:
            return False
        self.actor.apply(proposal)
        self.budget.check_wall_clock()
        return True

    def _critique(
        self,
        task: TaskSpec,
        proposal: ActorProposal,
        decision: GateDecision,
        iteration: int,
    ) -> CriticAssessment:
        attempt = AttemptRecord(
            f"attempt-{iteration}",
            task.task_id,
            "passed" if decision.passed else "failed",
            proposal.tokens,
        )
        return self.critic.assess(
            task,
            (attempt,),
            actor_summary=proposal.summary,
            verifier_results=decision.results,
            gate=decision,
        )

    def _gate_budget(self, decision: GateDecision, iterations: int, feedback: str) -> LoopOutcome:
        dimension = decision.results[-1].detail if decision.results else "unknown"
        return LoopOutcome("failed_budget", iterations, feedback, dimension)

    def _event(self, event: str, payload: object) -> None:
        if self.bridge is not None:
            self.budget.consume_file_write()
            self.bridge.record(event, payload, flags=self.flags)
            self.budget.check_wall_clock()

    def _finish(self, status: str, iterations: int, feedback: str) -> LoopOutcome:
        self._event("selfimprove.loop.complete", {"status": status, "iterations": iterations})
        return LoopOutcome(status, iterations, feedback)

    def _remember(
        self,
        task: TaskSpec,
        proposal: ActorProposal,
        failure: CriticAssessment | None,
        iteration: int,
    ) -> None:
        if not self.flags.memory or self.store is None or failure is None:
            return
        self.budget.consume_file_write()
        task_id = hashlib.sha256(task.task_id.encode()).hexdigest()
        episode_id = hashlib.sha256(
            (task_id + failure.failure_signature + proposal.summary).encode(),
        ).hexdigest()
        episode = MemoryEpisode(
            episode_id,
            task_id,
            "verification",
            "multiple",
            failure.failure_signature,
            failure.result.feedback[:8192],
            proposal.summary.strip()[:8192] or "No summary",
            iteration,
        )
        self.store.record(episode)
        self.budget.check_wall_clock()
