# Copyright 2026 Celina contributors.
"""Compact, bounded LedgerBridge events without silent preview truncation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from celina_pkg.selfimprove.flags import SelfImproveFlags
    from celina_pkg.selfimprove.ledger_bridge import LedgerBridge

    from .types import PatchProposal, PolicyCheckResult

_CHUNK = 100
_HEX_CHUNK = 8


def audit_payloads(
    proposal: PatchProposal,
    base_commit: str,
    digest: str,
    policy: PolicyCheckResult,
) -> list[dict[str, object]]:
    """Split paths into recoverable fragments below LedgerBridge's preview bound."""
    events: list[dict[str, object]] = [
        {
            "base_commit_hex_chunks": [
                base_commit[i : i + _HEX_CHUNK] for i in range(0, len(base_commit), _HEX_CHUNK)
            ],
        },
        {
            "bundle_hash_hex_chunks": [
                digest[i : i + _HEX_CHUNK] for i in range(0, len(digest), _HEX_CHUNK)
            ],
        },
    ]
    for number, name in enumerate(policy.touched_files):
        chunks = [name[start : start + _CHUNK] for start in range(0, len(name), _CHUNK)]
        for index, chunk in enumerate(chunks):
            events.append({"file": number, "part": index, "parts": len(chunks), "path": chunk})
    events.append({"proposal_id": proposal.proposal_id, "status": "bundle_ready"})
    return events


def record_audit(
    bridge: LedgerBridge,
    flags: SelfImproveFlags,
    payloads: list[dict[str, object]],
) -> None:
    """Record identity, paths and finally the ready status as ordered chain events."""
    for payload in payloads:
        if bridge.record("selfimprove.patch_bundle", payload, flags=flags) is None:
            message = "audit event not recorded"
            raise ValueError(message)
