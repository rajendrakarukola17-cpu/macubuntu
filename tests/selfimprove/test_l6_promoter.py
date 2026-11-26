# Copyright 2026 Celina contributors.
"""L6 promotion and rollback are explicit, bound and fail-closed."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from celina_pkg.evidence import EvidenceLedger
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l3.stats import EvalComparison
from celina_pkg.selfimprove.l6.promoter import ArtifactPromoter, compute_proposal_hash
from celina_pkg.selfimprove.l6.registry import ArtifactRegistry
from celina_pkg.selfimprove.l6.types import (
    GeneratedCandidate,
    PromotionApproval,
    PromotionProposal,
)
from celina_pkg.selfimprove.ledger_bridge import LedgerBridge

if TYPE_CHECKING:
    from pathlib import Path

SECOND_VERSION = 2
FLAGS = SelfImproveFlags(enabled=True, optimize=True)
GOOD = EvalComparison(
    1.0,
    0.5,
    1.0,
    improved=True,
    statistically_significant=True,
    security_regression=False,
    approved_for_promotion=True,
)


def setup_registry(tmp_path: Path) -> tuple[ArtifactRegistry, PromotionProposal]:
    """Create one active baseline and an unregistered proposal."""
    registry = ArtifactRegistry(tmp_path / "private", FLAGS)
    baseline = registry.register("main", "prompt", "baseline")
    registry.activate("main", baseline.version)
    proposal = PromotionProposal(baseline, GeneratedCandidate("secret candidate"), GOOD)
    return registry, proposal


class Gate:
    """Explicit host decision bound to a proposal hash."""

    def __init__(self, *, approved: bool = True, mismatch: bool = False) -> None:
        """Bind the fixed fixture decision."""
        self.approved, self.mismatch, self.calls = approved, mismatch, 0

    def approve(self, proposal: PromotionProposal) -> PromotionApproval:
        """Return a decision bound to this proposal, unless testing replay."""
        self.calls += 1
        digest = "0" * 64 if self.mismatch else compute_proposal_hash(proposal)
        return PromotionApproval(
            approved=self.approved,
            proposal_hash=digest,
            reason="human reviewed",
        )


def test_missing_gate_rejected_without_registering(tmp_path: Path) -> None:
    """There is no automatic promotion even for a statistically eligible proposal."""
    registry, proposal = setup_registry(tmp_path)
    result = ArtifactPromoter(FLAGS, registry).promote(proposal)
    assert result.status == "rejected_no_approval_gate"
    assert not result.promoted
    assert registry.active("main") == proposal.baseline
    assert registry.read("main", 2) is None


def test_flags_off_do_not_call_gate_or_touch_registry(tmp_path: Path) -> None:
    """Disabled optimizer stops before approval, reads, writes or audit callbacks."""
    registry, proposal = setup_registry(tmp_path)
    before = {p.name: p.read_bytes() for p in registry.root_dir.iterdir()}
    disabled = SelfImproveFlags(enabled=True)
    gate = Gate()
    result = ArtifactPromoter(
        disabled,
        ArtifactRegistry(registry.root_dir, disabled),
        approval_gate=gate,
    ).promote(proposal)
    assert result.status == "blocked_by_flag"
    assert not result.promoted
    assert gate.calls == 0
    assert {p.name: p.read_bytes() for p in registry.root_dir.iterdir()} == before


@pytest.mark.parametrize(
    "comparison",
    [
        replace(GOOD, approved_for_promotion=False),
        replace(GOOD, security_regression=True),
        replace(GOOD, statistically_significant=False),
        replace(GOOD, ci_low=0.0),
    ],
)
def test_eval_veto_precedes_human_approval(tmp_path: Path, comparison: EvalComparison) -> None:
    """An invalid, inconsistent, or security-regressing comparison is refused."""
    registry, proposal = setup_registry(tmp_path)
    gate = Gate()
    outcome = ArtifactPromoter(FLAGS, registry, approval_gate=gate).promote(
        replace(proposal, comparison=comparison),
    )
    assert outcome.status == "rejected_eval_gate"
    assert gate.calls == 0
    assert registry.read("main", 2) is None


def test_denied_and_mismatched_approval_never_write(tmp_path: Path) -> None:
    """A denial or a digest replay cannot mutate version or active pointer."""
    registry, proposal = setup_registry(tmp_path)
    for gate, status in (
        (Gate(approved=False), "rejected_by_approver"),
        (Gate(mismatch=True), "rejected_approval_mismatch"),
    ):
        result = ArtifactPromoter(FLAGS, registry, approval_gate=gate).promote(proposal)
        assert result.status == status
        assert gate.calls == 1
        assert registry.active("main") == proposal.baseline
        assert registry.read("main", 2) is None


def test_approved_promotion_rollback_and_evidence_chain(tmp_path: Path) -> None:
    """Only the approved candidate becomes active; rollback restores the baseline."""
    registry, proposal = setup_registry(tmp_path)
    ledger = EvidenceLedger(tmp_path / "events", anchor_path=tmp_path / "anchor")
    promoter = ArtifactPromoter(FLAGS, registry, approval_gate=Gate(), bridge=LedgerBridge(ledger))
    result = promoter.promote(proposal)
    assert result.status == "promoted"
    assert result.promoted
    assert result.active_version == SECOND_VERSION
    assert registry.active("main").content == proposal.candidate.content
    restored = promoter.rollback("main", reason="regression")
    assert restored.status == "rolled_back"
    assert restored.active_version == proposal.baseline.version
    assert registry.active("main") == proposal.baseline
    assert ledger.verify()[0]
    assert [x["event_type"] for x in ledger.list_entries()] == [
        "selfimprove.optimize.promoted",
        "selfimprove.optimize.rollback",
    ]
    assert proposal.candidate.content not in ledger.ledger_path.read_text()


def test_stale_baseline_and_rollback_errors_fail_closed(tmp_path: Path) -> None:
    """Approval for an old baseline cannot overwrite another active version."""
    registry, proposal = setup_registry(tmp_path)
    other = registry.register("main", "prompt", "independent")
    registry.activate("main", other.version)
    promoter = ArtifactPromoter(FLAGS, registry, approval_gate=Gate())
    outcome = promoter.promote(proposal)
    assert outcome.status == "rejected_stale_baseline"
    assert registry.active("main") == other
    assert registry.read("main", 3) is None
    result = promoter.rollback("missing", reason="test")
    assert result.status == "rejected_rollback"
    with pytest.raises(ValueError, match="reason"):
        promoter.rollback("main", reason=" ")


def test_digest_changes_with_content_and_comparison(tmp_path: Path) -> None:
    """The human approval digest commits to candidate, baseline and statistics."""
    _, proposal = setup_registry(tmp_path)
    assert compute_proposal_hash(proposal) == compute_proposal_hash(proposal)
    assert compute_proposal_hash(proposal) != compute_proposal_hash(
        replace(proposal, candidate=GeneratedCandidate("other")),
    )
    assert compute_proposal_hash(proposal) != compute_proposal_hash(
        replace(proposal, comparison=replace(GOOD, delta=0.8)),
    )


def test_approval_race_and_reuse_previously_registered_candidate(tmp_path: Path) -> None:
    """The active baseline is rechecked under the registry lock after approval."""
    registry, proposal = setup_registry(tmp_path)
    candidate = registry.register("main", "prompt", proposal.candidate.content)
    outcome = ArtifactPromoter(FLAGS, registry, approval_gate=Gate()).promote(proposal)
    assert outcome.promoted
    assert outcome.active_version == candidate.version
    assert registry.read("main", 3) is None

    registry.rollback("main", target_version=1)

    class RaceGate(Gate):
        """Switch active content after the proposal is approved."""

        def approve(self, proposal: PromotionProposal) -> PromotionApproval:
            """Simulate another trusted host writer racing with approval."""
            decision = super().approve(proposal)
            registry.activate("main", candidate.version)
            return decision

    result = ArtifactPromoter(FLAGS, registry, approval_gate=RaceGate()).promote(proposal)
    assert result.status == "rejected_stale_baseline"
    assert registry.active("main") == candidate
