"""Celina AI — Capabilities #14 & #15 (Security & Governance Substrate):
14. System-Boundary Capability Enforcement (`GovernanceSubstrate`):
- Lives OUTSIDE the LLM; enforces scoped permissions even if the model is tricked
- Issues HMAC-SHA256 cryptographically signed ONE-USE execution grants
(`ExecutionGrant`) bound to (tool_name, arguments_hash, actor_uid, policy_hash, ttl)
- Enforces governed data provenance (`DataClassification`: PUBLIC < INTERNAL <
CONFIDENTIAL < SECRET) blocking sensitive data egress to lower-clearance sinks
- Enforces verified worker shutdown (`WorkerLease.shutdown_and_verify()`)
15. Fail-Closed Behaviour with Tamper Detection (`GovernedStateMachine`):
- Enforces legal state transitions with a SHA-256 tamper-evident hash chain
- Raises `GovernanceTamperError` on illegal transitions or modified state history
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
import time
from typing import Any

from . import approval, capabilities, landlock_policy


class GovernanceError(PermissionError):
    """Raised when an execution grant, provenance boundary, or policy check fails closed."""


class GovernanceTamperError(RuntimeError):
    """Raised when governed state transition integrity or hash chain verification fails."""


class DataClassification(int, enum.Enum):
    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    SECRET = 3


@dataclasses.dataclass(frozen=True)
class ProvenanceTag:
    source: str
    classification: DataClassification
    origin_tool: str = ""
    created_at: float = dataclasses.field(default_factory=time.time)


@dataclasses.dataclass(frozen=True)
class ExecutionGrant:
    grant_id: str
    nonce: str
    tool_name: str
    arguments_hash: str
    actor_uid: int
    policy_hash: str
    max_classification: int
    issued_at: float
    expires_at: float
    signature: str


class WorkerLease:
    """Tracks a spawned worker subprocess and verifies clean termination + grant revocation."""

    def __init__(self, proc: subprocess.Popen, grant_id: str, substrate: GovernanceSubstrate):
        self.proc = proc
        self.grant_id = grant_id
        self._substrate = substrate
        self.pid = int(proc.pid)

    def shutdown_and_verify(self, *, timeout: float = 2.0) -> dict[str, Any]:
        """Terminates worker (SIGTERM -> SIGKILL if needed), revokes grant, and verifies dead PID."""
        self._substrate.revoke_grant(self.grant_id)
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=timeout)
            except Exception:
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=1.0)
                except Exception:
                    pass
        rc = self.proc.poll()
        alive = rc is None
        return {
            "pid": self.pid,
            "grant_id": self.grant_id,
            "verified_terminated": not alive,
            "returncode": rc,
        }


class GovernanceSubstrate:
    """Out-of-model authorization & execution control boundary:
    - One-use HMAC-signed ExecutionGrants
    - Data provenance & classification egress guard
    - Capability manifest + Landlock policy hash binding
    """

    def __init__(self, *, signing_key: bytes | None = None, expected_uid: int | None = None):
        self._key = signing_key or secrets.token_bytes(32)
        self.expected_uid = int(os.getuid() if expected_uid is None else expected_uid)
        self._lock = threading.RLock()
        self._active_grants: dict[str, ExecutionGrant] = {}
        self._consumed_nonces: set[str] = set()

    def _sign_fields(
        self,
        grant_id: str,
        nonce: str,
        tool_name: str,
        arguments_hash: str,
        actor_uid: int,
        policy_hash: str,
        max_classification: int,
        issued_at: float,
        expires_at: float,
    ) -> str:
        payload = json.dumps(
            {
                "grant_id": grant_id,
                "nonce": nonce,
                "tool_name": tool_name,
                "arguments_hash": arguments_hash,
                "actor_uid": actor_uid,
                "policy_hash": policy_hash,
                "max_classification": max_classification,
                "issued_at": round(issued_at, 6),
                "expires_at": round(expires_at, 6),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def issue_grant(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        actor_uid: int | None = None,
        is_tier2: bool = False,
        ttl_seconds: float = 30.0,
        provenance: ProvenanceTag | None = None,
    ) -> ExecutionGrant:
        uid = self.expected_uid if actor_uid is None else int(actor_uid)
        if uid != self.expected_uid:
            raise GovernanceError(
                f"Governance boundary denied grant: actor UID {uid} != session owner {self.expected_uid}.",
            )

        cap_ok, cap_err = capabilities.check_tool_execution_boundary(tool_name, is_tier2=is_tier2)
        if not cap_ok:
            raise GovernanceError(f"Governance capability check failed: {cap_err}")

        # Enforce data provenance clearance for outbound network sinks
        cap = capabilities.get_tool_capability(tool_name) or {}
        net_egress = str(cap.get("network_egress") or "none")
        max_cls = DataClassification.INTERNAL if net_egress == "none" else DataClassification.PUBLIC
        if provenance is not None:
            self.enforce_egress_clearance(tool_name, provenance, net_egress=net_egress)

        ll_prof = landlock_policy.profile_for_tool(tool_name)
        args_hash = approval.compute_arguments_hash(tool_name, arguments or {})
        now = time.time()
        gid = f"grt_{secrets.token_hex(8)}"
        nonce = secrets.token_hex(16)
        exp = now + max(1.0, float(ttl_seconds))
        sig = self._sign_fields(gid, nonce, tool_name, args_hash, uid, ll_prof.policy_hash, int(max_cls), now, exp)
        grant = ExecutionGrant(
            grant_id=gid,
            nonce=nonce,
            tool_name=tool_name,
            arguments_hash=args_hash,
            actor_uid=uid,
            policy_hash=ll_prof.policy_hash,
            max_classification=int(max_cls),
            issued_at=now,
            expires_at=exp,
            signature=sig,
        )
        with self._lock:
            self._active_grants[gid] = grant
        return grant

    def enforce_egress_clearance(
        self,
        tool_name: str,
        provenance: ProvenanceTag,
        *,
        net_egress: str | None = None,
    ) -> None:
        """Fail-closed provenance check: blocks CONFIDENTIAL/SECRET data from external sinks."""
        egress = net_egress
        if egress is None:
            cap = capabilities.get_tool_capability(tool_name) or {}
            egress = str(cap.get("network_egress") or "none")
        if egress not in {"none", "loopback_ollama"} and provenance.classification >= DataClassification.CONFIDENTIAL:
            raise GovernanceError(
                f"Sensitive-data egress blocked: classification={provenance.classification.name} "
                f"(source='{provenance.source}') cannot flow to network tool '{tool_name}' (egress={egress}).",
            )

    def verify_and_consume_grant(
        self,
        grant: ExecutionGrant,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        actor_uid: int | None = None,
    ) -> bool:
        """Verifies HMAC signature, expiry, UID, argument hash, and consumes one-use nonce atomically."""
        uid = self.expected_uid if actor_uid is None else int(actor_uid)
        if grant.actor_uid != uid or uid != self.expected_uid:
            raise GovernanceError("Grant UID mismatch.")
        if grant.tool_name != tool_name:
            raise GovernanceError(f"Grant tool mismatch: expected '{grant.tool_name}', got '{tool_name}'.")
        if time.time() > grant.expires_at:
            self.revoke_grant(grant.grant_id)
            raise GovernanceError("Execution grant has expired.")

        args_hash = approval.compute_arguments_hash(tool_name, arguments or {})
        if not hmac.compare_digest(grant.arguments_hash, args_hash):
            raise GovernanceError("Execution grant arguments_hash mismatch (possible parameter tampering).")

        expected_sig = self._sign_fields(
            grant.grant_id,
            grant.nonce,
            grant.tool_name,
            grant.arguments_hash,
            grant.actor_uid,
            grant.policy_hash,
            grant.max_classification,
            grant.issued_at,
            grant.expires_at,
        )
        if not hmac.compare_digest(grant.signature, expected_sig):
            raise GovernanceTamperError("ExecutionGrant HMAC signature verification failed (tampered grant).")

        with self._lock:
            if grant.nonce in self._consumed_nonces or grant.grant_id not in self._active_grants:
                raise GovernanceError("Execution grant already consumed or revoked (one-use enforcement).")
            self._consumed_nonces.add(grant.nonce)
            self._active_grants.pop(grant.grant_id, None)
        return True

    def revoke_grant(self, grant_id: str) -> bool:
        with self._lock:
            g = self._active_grants.pop(grant_id, None)
            if g is not None:
                self._consumed_nonces.add(g.nonce)
                return True
            return False


# ============================================================================
# Capability #15: Governed State Machine with Fail-Closed Tamper Detection
# ============================================================================

class GovernedStateMachine:
    """Hash-chained state machine enforcing legal autonomous state transitions.
    Any illegal transition OR retroactive modification to transition history raises
    `GovernanceTamperError` (fail-closed).
    """

    ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
        "pending": frozenset({"running", "cancelled", "observed"}),
        "running": frozenset({"done", "failed", "observed", "compensating", "cancelled"}),
        "failed": frozenset({"pending", "compensating", "compensated", "cancelled"}),
        "compensating": frozenset({"compensated", "failed"}),
        "observed": frozenset({"pending", "cancelled"}),
        "done": frozenset({"compensating"}),
        "compensated": frozenset(),
        "cancelled": frozenset(),
    }

    def __init__(self, entity_id: str, initial_state: str = "pending"):
        if initial_state not in self.ALLOWED_TRANSITIONS:
            raise ValueError(f"Unknown initial state '{initial_state}'.")
        self.entity_id = entity_id
        self.state = initial_state
        self._chain: list[dict[str, Any]] = []
        self._append_link("INIT", initial_state, "initialized")

    @staticmethod
    def _hash_link(prev_hash: str, entity_id: str, frm: str, to: str, reason: str, seq: int) -> str:
        raw = f"{prev_hash}|{entity_id}|{seq}|{frm}->{to}|{reason}".encode()
        return hashlib.sha256(raw).hexdigest()

    def _append_link(self, frm: str, to: str, reason: str) -> dict[str, Any]:
        prev = self._chain[-1]["entry_hash"] if self._chain else ("0" * 64)
        seq = len(self._chain) + 1
        rec = {
            "seq": seq,
            "entity_id": self.entity_id,
            "from": frm,
            "to": to,
            "reason": reason,
            "prev_hash": prev,
            "entry_hash": self._hash_link(prev, self.entity_id, frm, to, reason, seq),
        }
        self._chain.append(rec)
        return rec

    def transition(self, next_state: str, *, reason: str = "") -> dict[str, Any]:
        self.verify_chain()
        allowed = self.ALLOWED_TRANSITIONS.get(self.state, frozenset())
        if next_state not in allowed:
            raise GovernanceTamperError(
                f"Illegal governed state transition for '{self.entity_id}': {self.state} -> {next_state}",
            )
        prev_state = self.state
        self.state = next_state
        return self._append_link(prev_state, next_state, reason)

    def verify_chain(self) -> bool:
        expected_prev = "0" * 64
        for idx, rec in enumerate(self._chain, 1):
            if rec.get("prev_hash") != expected_prev:
                raise GovernanceTamperError(f"Tampered state prev_hash at seq={idx}.")
            recomputed = self._hash_link(
                expected_prev,
                str(rec.get("entity_id", "")),
                str(rec.get("from", "")),
                str(rec.get("to", "")),
                str(rec.get("reason", "")),
                int(rec.get("seq", 0)),
            )
            if recomputed != rec.get("entry_hash"):
                raise GovernanceTamperError(f"Tampered state entry_hash at seq={idx}.")
            expected_prev = recomputed
        return True

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._chain)


_DEFAULT_SUBSTRATE: GovernanceSubstrate | None = None


def get_governance_substrate() -> GovernanceSubstrate:
    global _DEFAULT_SUBSTRATE
    if _DEFAULT_SUBSTRATE is None:
        _DEFAULT_SUBSTRATE = GovernanceSubstrate()
    return _DEFAULT_SUBSTRATE
