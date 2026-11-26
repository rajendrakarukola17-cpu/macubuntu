# Copyright 2026 Celina contributors.
"""Offline contract tests for bounded, verifier-led refinement."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from celina_pkg.evidence import EvidenceLedger
from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, TaskSpec, VerifierResult
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l2.gate import VerificationGate
from celina_pkg.selfimprove.l4.retriever import MemoryRetriever
from celina_pkg.selfimprove.l4.store import FailureFixStore, MemoryEpisode
from celina_pkg.selfimprove.l5.loop import (
    ActorContext,
    ActorProposal,
    ApprovalDecision,
    LoopConfig,
    RefinementLoop,
)
from celina_pkg.selfimprove.ledger_bridge import LedgerBridge

if TYPE_CHECKING:
    from pathlib import Path

TWO = 2

TASK = TaskSpec("task1", "repair assertion")
ENABLED = SelfImproveFlags(enabled=True, loop=True, verifiers=True)
LIMITS = BudgetLimits(5, 100, 60, 10, 10)
PASS = VerifierResult("pytest", "pass", 1, "ok")
FAIL = VerifierResult("pytest", "fail", 0, "exit 1 assertion")


class FakeVerifier:
    """Fixed host-configured sequence of verifier results."""

    def __init__(self, *results: VerifierResult) -> None:
        """Prepare a sequence."""
        self.results = list(results)
        self.calls = 0

    def verify(self, _task: TaskSpec) -> VerifierResult:
        """Return the next result."""
        self.calls += 1
        return self.results.pop(0)


class FakeActor:
    """Proposals do not mutate; apply is a separate trusted operation."""

    def __init__(self, proposal: ActorProposal | None = None) -> None:
        """Bind a fixed proposal."""
        self.proposal = proposal or ActorProposal(summary="fixed", tokens=2)
        self.contexts: list[ActorContext] = []
        self.applied = 0

    def propose(self, context: ActorContext) -> ActorProposal:
        """Capture the supplied context."""
        self.contexts.append(context)
        return self.proposal

    def apply(self, _proposal: ActorProposal) -> None:
        """Count authorized applications."""
        self.applied += 1


class FakeApproval:
    """Record approval requests and return a fixed decision."""

    def __init__(self, *, approved: bool) -> None:
        """Bind the predetermined response."""
        self.approved = approved
        self.calls = 0

    def approve(self, _task: TaskSpec, _proposal: ActorProposal) -> ApprovalDecision:
        """Count requests and return the decision."""
        self.calls += 1
        return ApprovalDecision(approved=self.approved, reason="host policy")


def make_loop(
    flags: SelfImproveFlags,
    actor: FakeActor,
    verifier: FakeVerifier,
    *,
    budget: BudgetGuard | None = None,
    approval: FakeApproval | None = None,
    **kwargs: object,
) -> RefinementLoop:
    """Create a gate and loop sharing precisely one guard."""
    guard = budget or BudgetGuard(LIMITS)
    gate = VerificationGate(flags, guard, (verifier,))
    return RefinementLoop(flags, guard, gate, actor, approval=approval, **kwargs)


def test_flags_off_and_verifier_disabled_do_not_call_dependencies(tmp_path: Path) -> None:
    """No callbacks or filesystem writes occur until all required flags permit work."""
    for flags, expected in (
        (SelfImproveFlags(), "blocked_by_flag"),
        (SelfImproveFlags(enabled=True, loop=True), "failed_verification"),
    ):
        actor, verifier = FakeActor(), FakeVerifier(PASS)
        ledger = EvidenceLedger(tmp_path / "events", anchor_path=tmp_path / "anchor")
        loop = make_loop(flags, actor, verifier, bridge=LedgerBridge(ledger))
        assert loop.run(TASK).status == expected
        assert actor.contexts == []
        assert verifier.calls == 0
        assert not (tmp_path / "events").exists()


def test_feedback_then_l2_success_stops_immediately() -> None:
    """Actor success claims cannot hide a verifier failure."""
    actor, verifier = FakeActor(), FakeVerifier(FAIL, PASS, FAIL)
    outcome = make_loop(ENABLED, actor, verifier).run(TASK)
    assert outcome.status == "completed"
    assert verifier.calls == TWO
    assert len(actor.contexts) == TWO
    assert "pytest" in actor.contexts[1].feedback
    assert outcome.iterations == TWO
    assert actor.applied == 0
    with pytest.raises(FrozenInstanceError):
        outcome.status = "changed"


def test_identical_failures_stop_refinement() -> None:
    """A repeated failure signature triggers the default stagnation breaker."""
    actor, verifier = FakeActor(), FakeVerifier(FAIL, FAIL, PASS)
    outcome = make_loop(ENABLED, actor, verifier).run(TASK)
    assert outcome.status == "failed_stagnant"
    assert verifier.calls == TWO


@pytest.mark.parametrize(
    "declared",
    [ActorProposal("fix", requires_mutation=True), ActorProposal("fix", target_paths=("file",))],
)
def test_mutation_requires_explicit_approval(declared: ActorProposal) -> None:
    """No mutation callback is invoked before a positive approval decision."""
    for approval in (None, FakeApproval(approved=False)):
        actor, verifier = FakeActor(declared), FakeVerifier(PASS)
        outcome = make_loop(ENABLED, actor, verifier, approval=approval).run(TASK)
        assert outcome.status == "failed_approval"
        assert actor.applied == 0
        assert verifier.calls == 0
    actor, verifier = FakeActor(declared), FakeVerifier(PASS)
    approval = FakeApproval(approved=True)
    assert make_loop(ENABLED, actor, verifier, approval=approval).run(TASK).status == "completed"
    assert approval.calls == actor.applied == verifier.calls == 1


@pytest.mark.parametrize(
    ("proposal", "limits", "dimension"),
    [
        (ActorProposal("x", tokens=3), BudgetLimits(1, 2, 60, 0, 0), "tokens"),
        (ActorProposal("x", file_writes=1), BudgetLimits(1, 2, 60, 0, 0), "file_writes"),
        (ActorProposal("x", subprocesses=1), BudgetLimits(1, 2, 60, 0, 0), "subprocesses"),
    ],
)
def test_budget_caps_prevent_followup_calls(
    proposal: ActorProposal,
    limits: BudgetLimits,
    dimension: str,
) -> None:
    """Declared actor resources share the verifier's guard and halt on caps."""
    actor, verifier = FakeActor(proposal), FakeVerifier(PASS)
    outcome = make_loop(ENABLED, actor, verifier, budget=BudgetGuard(limits)).run(TASK)
    assert outcome.status == "failed_budget"
    assert outcome.budget_dimension == dimension
    assert verifier.calls == 0


def test_memory_is_untrusted_and_only_resolved_failures_are_stored(tmp_path: Path) -> None:
    """L4 frames reference data and redacts failure/fix episodes on persistence."""
    flags = SelfImproveFlags(enabled=True, loop=True, verifiers=True, memory=True)
    db = tmp_path / "private" / "memory.sqlite"
    store = FailureFixStore(db, flags)
    store.record(MemoryEpisode("initial", "other", "tests", "pytest", "assertion", "bad", "old", 0))
    actor, verifier = FakeActor(ActorProposal("token=secretValue fixed")), FakeVerifier(FAIL, PASS)
    outcome = make_loop(
        flags,
        actor,
        verifier,
        retriever=MemoryRetriever(db, flags),
        store=store,
    ).run(TASK)
    assert outcome.status == "completed"
    assert "UNTRUSTED DATA ONLY" in actor.contexts[0].memory_context
    episodes = store.query()
    assert len(episodes) == TWO
    assert "secretValue" not in db.read_bytes().decode("utf-8", errors="ignore")


def test_bridge_records_compact_nonsecret_chain(tmp_path: Path) -> None:
    """Only compact event metadata crosses the optional audit bridge."""
    ledger = EvidenceLedger(tmp_path / "events", anchor_path=tmp_path / "anchor")
    actor = FakeActor(ActorProposal("password=secretValue"))
    outcome = make_loop(ENABLED, actor, FakeVerifier(PASS), bridge=LedgerBridge(ledger)).run(TASK)
    assert outcome.status == "completed"
    assert ledger.verify()[0]
    assert [e["event_type"] for e in ledger.list_entries()] == [
        "selfimprove.loop.attempt",
        "selfimprove.loop.complete",
    ]
    assert "secretValue" not in (tmp_path / "events").read_text()


def test_loop_rejects_different_budget_and_invalid_config() -> None:
    """A separate gate budget cannot evade the loop's cap."""
    guard = BudgetGuard(LIMITS)
    gate = VerificationGate(ENABLED, BudgetGuard(LIMITS), (FakeVerifier(PASS),))
    with pytest.raises(ValueError, match="budget"):
        RefinementLoop(ENABLED, guard, gate, FakeActor())
    with pytest.raises(ValueError, match="stagnation"):
        LoopConfig(stagnation_limit=0)


def test_empty_gate_and_mismatched_flags_are_rejected() -> None:
    """A loop cannot use different gate switches or a vacuous verifier suite."""
    guard = BudgetGuard(LIMITS)
    gate = VerificationGate(ENABLED, guard, ())
    actor = FakeActor()
    assert RefinementLoop(ENABLED, guard, gate, actor).run(TASK).status == "failed_verification"
    assert actor.contexts == []
    other = SelfImproveFlags(enabled=True, verifiers=True)
    with pytest.raises(ValueError, match="flags"):
        RefinementLoop(other, guard, gate, actor)


def test_iteration_exhaustion_and_verifier_budget_are_reported() -> None:
    """Both loop and verifier usage stop through the same shared guard."""
    limits = BudgetLimits(1, 100, 60, 1, 0)
    actor, verifier = FakeActor(), FakeVerifier(FAIL, FAIL)
    result = make_loop(ENABLED, actor, verifier, budget=BudgetGuard(limits)).run(TASK)
    assert result.status == "failed_budget"
    assert result.budget_dimension == "iterations"

    guard = BudgetGuard(limits)

    class BudgetVerifier:
        """Trusted verifier consumes through the same shared guard."""

        def verify(self, _task: TaskSpec) -> VerifierResult:
            """Reject a subprocess beyond the configured cap."""
            guard.consume_subprocess()
            return PASS

    gate = VerificationGate(ENABLED, guard, (BudgetVerifier(),))
    result = RefinementLoop(ENABLED, guard, gate, FakeActor()).run(TASK)
    assert result.status == "failed_budget"
    assert result.budget_dimension == "subprocesses"


def test_invalid_proposals_and_approval_are_rejected() -> None:
    """Malformed values cannot masquerade as a safe proposal or approval."""
    for kwargs in (
        {"summary": "x" * 8193},
        {"summary": "x", "tokens": -1},
        {"summary": "x", "requires_mutation": 1},
        {"summary": "x", "target_paths": ("",)},
    ):
        with pytest.raises(ValueError, match=r"summary|resource|mutation|target"):
            ActorProposal(**kwargs)
    with pytest.raises(ValueError, match="approval"):
        ApprovalDecision(approved=1)

    class BadActor(FakeActor):
        """An incorrectly implemented actor returns an invalid object."""

        def propose(self, _context: ActorContext) -> object:
            """Return a non-proposal without effects."""
            return object()

    with pytest.raises(TypeError, match="ActorProposal"):
        make_loop(ENABLED, BadActor(), FakeVerifier(PASS)).run(TASK)


def test_disabled_memory_never_touches_injected_memory(tmp_path: Path) -> None:
    """Even if injected, memory dependencies stay completely idle when disabled."""
    path = tmp_path / "not-created" / "memory.sqlite"
    active = SelfImproveFlags(enabled=True, memory=True)
    retriever = MemoryRetriever(path, active)
    store = FailureFixStore(path, active)
    result = make_loop(
        ENABLED,
        FakeActor(),
        FakeVerifier(FAIL, PASS),
        retriever=retriever,
        store=store,
    ).run(TASK)
    assert result.status == "completed"
    assert not path.parent.exists()


def test_public_actor_and_approval_protocols_are_available() -> None:
    """Loop's integration protocols are available alongside its public DTOs."""
    from celina_pkg.selfimprove.l5.loop import (  # noqa: PLC0415  # Verify public runtime exports.
        ActorProtocol,
        ApprovalGateProtocol,
    )

    assert ActorProtocol.__name__ == "ActorProtocol"
    assert ApprovalGateProtocol.__name__ == "ApprovalGateProtocol"
