# Copyright 2026 Celina contributors.
"""Explicit approval-bound promotion and deterministic rollback."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from typing import TYPE_CHECKING

from ._registry_io import ArtifactRegistryError, RegistryStaleError
from .types import PromotionApproval, PromotionOutcome, PromotionProposal, content_digest

if TYPE_CHECKING:
    from celina_pkg.selfimprove.flags import SelfImproveFlags
    from celina_pkg.selfimprove.l3.stats import EvalComparison
    from celina_pkg.selfimprove.ledger_bridge import LedgerBridge

    from .registry import ArtifactRegistry
    from .types import PromotionApprovalProtocol


def compute_proposal_hash(proposal: PromotionProposal) -> str:
    """Bind the baseline, candidate digest and exact paired comparison."""
    payload = {
        "artifact_id": proposal.baseline.artifact_id,
        "kind": proposal.baseline.kind,
        "baseline_version": proposal.baseline.version,
        "baseline_hash": proposal.baseline.content_hash,
        "candidate_hash": content_digest(proposal.candidate.content),
        "candidate_tokens": proposal.candidate.tokens,
        "comparison": asdict(proposal.comparison),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _eligible(comparison: EvalComparison) -> bool:
    values = (comparison.delta, comparison.ci_low, comparison.ci_high)
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
        for x in values
    ):
        return False
    return (
        comparison.improved is True
        and comparison.statistically_significant is True
        and comparison.security_regression is False
        and comparison.approved_for_promotion is True
        and comparison.delta > 0
        and comparison.ci_low > 0
        and comparison.ci_high >= comparison.ci_low
    )


class ArtifactPromoter:
    """Approval gate must be backed by a trusted host human approval flow.

    Decisions and L3 evaluations are in-process objects, not cryptographic
    attestations. The host must protect the registry and approval implementation.
    """

    def __init__(
        self,
        flags: SelfImproveFlags,
        registry: ArtifactRegistry,
        *,
        approval_gate: PromotionApprovalProtocol | None = None,
        bridge: LedgerBridge | None = None,
    ) -> None:
        """Bind explicit host dependencies without performing filesystem I/O."""
        if registry.flags != flags:
            message = "registry and promoter flags must match"
            raise ValueError(message)
        self.flags, self.registry, self.approval_gate, self.bridge = (
            flags,
            registry,
            approval_gate,
            bridge,
        )

    def promote(self, proposal: PromotionProposal) -> PromotionOutcome:
        """Request approval for an eligible proposal, then conditionally switch."""
        if not self.flags.enabled or not self.flags.optimize:
            return PromotionOutcome("blocked_by_flag", promoted=False)
        if not _eligible(proposal.comparison):
            return PromotionOutcome("rejected_eval_gate", promoted=False)
        digest = compute_proposal_hash(proposal)
        rejection = self._approval_rejection(proposal, digest)
        if rejection:
            return PromotionOutcome(rejection, promoted=False)
        try:
            artifact = self.registry.promote(proposal.baseline, proposal.candidate)
        except RegistryStaleError:
            return PromotionOutcome("rejected_stale_baseline", promoted=False)
        self._event(
            "selfimprove.optimize.promoted",
            {"proposal_hash": digest, "version": artifact.version},
        )
        return PromotionOutcome("promoted", promoted=True, active_version=artifact.version)

    def _approval_rejection(self, proposal: PromotionProposal, digest: str) -> str:
        if self.approval_gate is None:
            return "rejected_no_approval_gate"
        try:
            approval = self.approval_gate.approve(proposal)
        except Exception:
            return "rejected_by_approver"
        if not isinstance(approval, PromotionApproval) or not approval.approved:
            return "rejected_by_approver"
        if approval.proposal_hash != digest:
            return "rejected_approval_mismatch"
        return ""

    def rollback(
        self,
        artifact_id: str,
        *,
        reason: str,
        target_version: int | None = None,
    ) -> PromotionOutcome:
        """Restore a verified version with explicit reason and optional audit."""
        if not self.flags.enabled or not self.flags.optimize:
            return PromotionOutcome("blocked_by_flag", promoted=False)
        if not isinstance(reason, str) or not reason.strip():
            message = "rollback reason must be nonempty"
            raise ValueError(message)
        try:
            artifact = self.registry.rollback(artifact_id, target_version=target_version)
        except ArtifactRegistryError:
            return PromotionOutcome("rejected_rollback", promoted=False)
        self._event(
            "selfimprove.optimize.rollback",
            {"version": artifact.version, "hash": artifact.content_hash},
        )
        return PromotionOutcome("rolled_back", promoted=False, active_version=artifact.version)

    def _event(self, event: str, payload: object) -> None:
        if self.bridge is not None:
            self.bridge.record(event, payload, flags=self.flags)
