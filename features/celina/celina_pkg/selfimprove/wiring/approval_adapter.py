# Copyright 2026 Celina contributors.
"""Bound, single-use Celina token adapters for L5 mutations and L6/L7 reviews."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.l5.types import ApprovalDecision
from celina_pkg.selfimprove.l6.promoter import compute_proposal_hash
from celina_pkg.selfimprove.l6.types import PromotionApproval
from celina_pkg.selfimprove.l7.types import PatchApproval

from ._token_store import TokenSpendStore

if TYPE_CHECKING:
    from celina_pkg.approval import ApprovalToken
    from celina_pkg.selfimprove.contracts import TaskSpec
    from celina_pkg.selfimprove.l5.types import ActorProposal
    from celina_pkg.selfimprove.l6.types import PromotionProposal
    from celina_pkg.selfimprove.l7.types import PatchProposal

__all__ = (
    "MutationApprovalAdapter",
    "PatchApprovalAdapter",
    "PromotionApprovalAdapter",
    "TokenSpendStore",
    "mutation_arguments",
    "patch_arguments",
    "promotion_arguments",
)


def _nonce(arguments: dict[str, object], nonce: str) -> dict[str, object]:
    if not isinstance(nonce, str) or (nonce and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", nonce)):
        message = "invalid approval nonce"
        raise ValueError(message)
    return {**arguments, "approval_nonce": nonce} if nonce else arguments


def mutation_arguments(
    task: TaskSpec,
    proposal: ActorProposal,
    *,
    nonce: str = "",
) -> dict[str, object]:
    """Bind the full task, exact target paths and all declared mutation details."""
    return _nonce({"task": asdict(task), "proposal": asdict(proposal)}, nonce)


def promotion_arguments(proposal: PromotionProposal, *, nonce: str = "") -> dict[str, object]:
    """Bind the exact L6 proposal hash returned by the existing promoter."""
    return _nonce({"proposal_hash": compute_proposal_hash(proposal)}, nonce)


def patch_arguments(
    proposal: PatchProposal,
    bundle_hash: str,
    *,
    nonce: str = "",
) -> dict[str, object]:
    """Bind the exact L7 bundle hash and proposal identity."""
    return _nonce({"proposal_id": proposal.proposal_id, "bundle_hash": bundle_hash}, nonce)


class MutationApprovalAdapter:
    """L5 gate; token issuance must be controlled by a trusted human-facing host."""

    def __init__(
        self, token: ApprovalToken | None, store: TokenSpendStore, *, nonce: str = "",
    ) -> None:
        """Bind a pre-authorized token and host-controlled persistent store."""
        self.token, self.store, self.nonce = token, store, nonce

    def approve(self, task: TaskSpec, proposal: ActorProposal) -> ApprovalDecision:
        """Atomically consume a token bound to the full task and proposal."""
        try:
            approved = self.store.consume(
                self.token,
                "selfimprove_l5_mutation",
                mutation_arguments(task, proposal, nonce=self.nonce),
            )
        except Exception:
            approved = False
        return ApprovalDecision(approved, "approved" if approved else "denied")


class PromotionApprovalAdapter:
    """L6 approval; replay protection is scoped to the configured private store."""

    def __init__(
        self, token: ApprovalToken | None, store: TokenSpendStore, *, nonce: str = "",
    ) -> None:
        """Bind a token without filesystem or callback access."""
        self.token, self.store, self.nonce = token, store, nonce

    def approve(self, proposal: PromotionProposal) -> PromotionApproval:
        """Consume the exact proposal-bound token once."""
        try:
            arguments = promotion_arguments(proposal, nonce=self.nonce)
            digest = str(arguments["proposal_hash"])
            approved = self.store.consume(self.token, "selfimprove_l6_promotion", arguments)
        except Exception:
            digest, approved = "0" * 64, False
        return PromotionApproval(approved, digest, "approved" if approved else "denied")


class PatchApprovalAdapter:
    """L7 approval; the token is bound to the human-reviewed bundle digest."""

    def __init__(
        self, token: ApprovalToken | None, store: TokenSpendStore, *, nonce: str = "",
    ) -> None:
        """Bind a token without filesystem or callback access."""
        self.token, self.store, self.nonce = token, store, nonce

    def approve(self, proposal: PatchProposal, bundle_hash: str) -> PatchApproval:
        """Atomically consume the exact proposal- and bundle-bound token once."""
        if not isinstance(bundle_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", bundle_hash):
            return PatchApproval(approved=False, bundle_hash="0" * 64, reason="invalid digest")
        try:
            approved = self.store.consume(
                self.token,
                "selfimprove_l7_patch",
                patch_arguments(proposal, bundle_hash, nonce=self.nonce),
            )
        except Exception:
            approved = False
        return PatchApproval(approved, bundle_hash, "approved" if approved else "denied")
