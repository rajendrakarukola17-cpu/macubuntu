# Copyright 2026 Celina contributors.
"""Real Celina tokens bind L5, L6 and L7 and resist durable replay."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from celina_pkg.approval import ApprovalToken, request_bound_approval
from celina_pkg.selfimprove.contracts import TaskSpec
from celina_pkg.selfimprove.l5.types import ActorProposal
from celina_pkg.selfimprove.l6.promoter import compute_proposal_hash
from celina_pkg.selfimprove.l6.types import GeneratedCandidate, PromotionProposal
from celina_pkg.selfimprove.l7.types import PatchProposal
from celina_pkg.selfimprove.wiring.approval_adapter import (
    MutationApprovalAdapter,
    PatchApprovalAdapter,
    PromotionApprovalAdapter,
    TokenSpendStore,
    mutation_arguments,
    patch_arguments,
    promotion_arguments,
)

from .test_l6_promoter import GOOD, setup_registry

if TYPE_CHECKING:
    from pathlib import Path

TASK = TaskSpec("task", "fix defect")
MUTATION = ActorProposal("change", requires_mutation=True, target_paths=("src/a.py",))
PATCH = PatchProposal("patch1", "text diff", "summary")
DIGEST = "a" * 64


def issue(tool: str, args: dict[str, object], *, approved: bool = True) -> ApprovalToken | None:
    """Request a genuinely argument-bound token via the real Celina API."""
    token, _ = request_bound_approval(tool, args, lambda _title, _details: approved)
    return token


def clone(token: ApprovalToken) -> ApprovalToken:
    """Reconstruct a duplicate token with an independent lock and unused flag."""
    return ApprovalToken(
        tool=token.tool,
        target=token.target,
        arguments_hash=token.arguments_hash,
        risk=token.risk,
        expires_at=token.expires_at,
        expires_at_monotonic=token.expires_at_monotonic,
    )


def test_l5_real_token_binds_full_task_and_mutation(tmp_path: Path) -> None:
    """A changed mutation or task is refused; the exact approved action succeeds once."""
    args = mutation_arguments(TASK, MUTATION)
    token = issue("selfimprove_l5_mutation", args)
    adapter = MutationApprovalAdapter(token, TokenSpendStore(tmp_path / "state"))
    assert not adapter.approve(TASK, replace(MUTATION, target_paths=("src/b.py",))).approved
    assert not adapter.approve(TaskSpec("other", "fix defect"), MUTATION).approved
    assert adapter.approve(TASK, MUTATION).approved
    assert not adapter.approve(TASK, MUTATION).approved


def test_l6_real_token_bound_to_proposal_hash_and_replay_is_denied(tmp_path: Path) -> None:
    """Promotion approval is tied to the exact L6 digest and cannot be reused."""
    _, proposal = setup_registry(tmp_path)
    digest = compute_proposal_hash(proposal)
    token = issue("selfimprove_l6_promotion", promotion_arguments(proposal))
    adapter = PromotionApprovalAdapter(token, TokenSpendStore(tmp_path / "state"))
    changed = PromotionProposal(proposal.baseline, GeneratedCandidate("different"), GOOD)
    assert not adapter.approve(changed).approved
    decision = adapter.approve(proposal)
    assert decision.approved
    assert decision.proposal_hash == digest
    assert not adapter.approve(proposal).approved


def test_l7_token_digest_mismatch_expiry_and_missing_fail_closed(tmp_path: Path) -> None:
    """Missing, denied, expired and mismatched patch authorizations never succeed."""
    store = TokenSpendStore(tmp_path / "state")
    assert not PatchApprovalAdapter(None, store).approve(PATCH, DIGEST).approved
    denied = issue("selfimprove_l7_patch", patch_arguments(PATCH, DIGEST), approved=False)
    assert denied is None
    token = issue("selfimprove_l7_patch", patch_arguments(PATCH, DIGEST))
    assert token is not None
    adapter = PatchApprovalAdapter(token, store)
    assert not adapter.approve(PATCH, "b" * 64).approved
    assert adapter.approve(PATCH, DIGEST).approved
    assert not adapter.approve(PATCH, DIGEST).approved
    expired = issue("selfimprove_l7_patch", patch_arguments(PATCH, DIGEST))
    assert expired is not None
    expired.expires_at_monotonic = time.monotonic() - 1
    assert not PatchApprovalAdapter(expired, store).approve(PATCH, DIGEST).approved


def test_reconstructed_token_cannot_replay_across_adapter_instances(tmp_path: Path) -> None:
    """The persistent atomic spend marker defeats a fresh object with the same issuance fields."""
    store = TokenSpendStore(tmp_path / "state")
    token = issue("selfimprove_l7_patch", patch_arguments(PATCH, DIGEST))
    assert token is not None
    duplicate = clone(token)
    assert PatchApprovalAdapter(token, store).approve(PATCH, DIGEST).approved
    assert (
        not PatchApprovalAdapter(duplicate, TokenSpendStore(tmp_path / "state"))
        .approve(PATCH, DIGEST)
        .approved
    )


def test_concurrent_duplicate_tokens_have_exactly_one_winner(tmp_path: Path) -> None:
    """Independent token locks cannot race around a shared durable spend store."""
    token = issue("selfimprove_l7_patch", patch_arguments(PATCH, DIGEST))
    assert token is not None
    copies = [clone(token) for _ in range(8)]
    adapters = [PatchApprovalAdapter(item, TokenSpendStore(tmp_path / "state")) for item in copies]
    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(
            pool.map(lambda adapter: adapter.approve(PATCH, DIGEST).approved, adapters),
        )
    assert sum(decisions) == 1


def test_reusable_token_and_unsafe_state_are_refused(tmp_path: Path) -> None:
    """Reusable tokens and symlinked token stores cannot authorize a mutation."""
    token = issue("selfimprove_l7_patch", patch_arguments(PATCH, DIGEST))
    assert token is not None
    token.single_use = False
    assert (
        not PatchApprovalAdapter(token, TokenSpendStore(tmp_path / "state"))
        .approve(PATCH, DIGEST)
        .approved
    )
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    fresh = issue("selfimprove_l7_patch", patch_arguments(PATCH, DIGEST))
    assert not PatchApprovalAdapter(fresh, TokenSpendStore(link)).approve(PATCH, DIGEST).approved
    assert not list(target.iterdir())


def test_mutating_unbound_token_fields_cannot_bypass_replay(tmp_path: Path) -> None:
    """An unbound display target or timestamp is not an issuance identity."""
    token = issue("selfimprove_l7_patch", patch_arguments(PATCH, DIGEST))
    assert token is not None
    copy = clone(token)
    store = TokenSpendStore(tmp_path / "state")
    assert PatchApprovalAdapter(token, store).approve(PATCH, DIGEST).approved
    copy.target = "different display target"
    copy.expires_at += 60
    copy.expires_at_monotonic += 60
    assert not PatchApprovalAdapter(copy, store).approve(PATCH, DIGEST).approved


def test_fresh_nonce_allows_new_human_approval_for_same_bundle(tmp_path: Path) -> None:
    """Fresh human approval can authorize the same digest without replaying a prior token."""
    store = TokenSpendStore(tmp_path / "state")
    args1 = patch_arguments(PATCH, DIGEST, nonce="human-review-1")
    args2 = patch_arguments(PATCH, DIGEST, nonce="human-review-2")
    token1 = issue("selfimprove_l7_patch", args1)
    token2 = issue("selfimprove_l7_patch", args2)
    assert (
        PatchApprovalAdapter(token1, store, nonce="human-review-1").approve(PATCH, DIGEST).approved
    )
    assert (
        not PatchApprovalAdapter(token1, store, nonce="human-review-1")
        .approve(PATCH, DIGEST)
        .approved
    )
    assert (
        PatchApprovalAdapter(token2, store, nonce="human-review-2").approve(PATCH, DIGEST).approved
    )


def test_invalid_nonce_and_digest_fail_closed_without_store_access(tmp_path: Path) -> None:
    """Malformed nonce/digest values cannot trigger or bypass approval."""
    _, proposal = setup_registry(tmp_path)
    state = tmp_path / "approval-state"
    store = TokenSpendStore(state)
    with pytest.raises(ValueError, match="invalid approval nonce"):
        mutation_arguments(TASK, MUTATION, nonce="bad nonce")
    mutation = MutationApprovalAdapter(None, store, nonce="bad nonce")
    assert not mutation.approve(TASK, MUTATION).approved
    assert not PromotionApprovalAdapter(None, store, nonce="bad nonce").approve(proposal).approved
    patcher = PatchApprovalAdapter(None, store, nonce="bad nonce")
    assert not patcher.approve(PATCH, DIGEST).approved
    assert not patcher.approve(PATCH, "not-a-digest").approved
    assert not state.exists()
