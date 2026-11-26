"""Approval broker state machine; NOT a deployable process isolation boundary.

Only trusted broker/UI code may hold this object. Channel authentication,
worker confinement and actuator isolation must be provided by a trusted host;
this module is not wired to production dispatch and cannot establish them.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


class ApprovalProtocolError(ValueError):
    """A request cannot be represented safely or exceeds broker limits."""


@dataclass(frozen=True, slots=True)
class ApprovalCard:
    request_id: str
    canonical_bytes: bytes
    payload_hash: str


@dataclass(frozen=True, slots=True)
class Grant:
    """An opaque random handle; a constructed object has no authority by itself."""
    handle: str


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Trusted host registration; not supplied by the requesting planner."""
    validate: Callable[[dict], dict]
    target: Callable[[dict], str]
    may_display: Callable[[dict], bool]
    execute: Callable[[dict], Any]
    risk: str
    scope: str


def _snapshot(value: Any, depth: int = 0) -> Any:
    """Copy while validating, so later mutations do not alter the serialized payload."""
    if depth > 20:
        raise ApprovalProtocolError("payload nesting limit exceeded")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > 2**53 - 1:
            raise ApprovalProtocolError("integer outside interoperable range")
        return value
    if type(value) is str:
        try:
            if len(value.encode("utf-8", errors="strict")) > 16384:
                raise ApprovalProtocolError("string exceeds payload limit")
        except UnicodeError as exc:
            raise ApprovalProtocolError("invalid Unicode") from exc
        return value
    if type(value) is list:
        if len(value) > 1024:
            raise ApprovalProtocolError("too many items")
        return [_snapshot(item, depth + 1) for item in value]
    if type(value) is dict:
        if len(value) > 1024:
            raise ApprovalProtocolError("too many items")
        try:
            pairs = list(value.items())
        except RuntimeError as exc:
            raise ApprovalProtocolError("payload changed while copying") from exc
        if any(type(key) is not str for key, _ in pairs):
            raise ApprovalProtocolError("invalid object keys")
        return {_snapshot(key, depth + 1): _snapshot(item, depth + 1) for key, item in pairs}
    raise ApprovalProtocolError("unsupported payload type")


def canonical_payload(tool: str, arguments: dict, target: str, risk: str, scope: str) -> bytes:
    """Serialize a strictly bounded, lossless JSON payload using escaped ASCII."""
    if any(type(value) is not str or not value for value in (tool, target, risk, scope)):
        raise ApprovalProtocolError("tool, target, risk and scope must be nonempty strings")
    if type(arguments) is not dict:
        raise ApprovalProtocolError("arguments must be an object")
    payload = {"version": 1, "tool": tool, "arguments": arguments,
               "target": target, "risk": risk, "scope": scope}
    snapshot = _snapshot(payload)
    raw = json.dumps(snapshot, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                     allow_nan=False).encode("ascii")
    if len(raw) > 16384:
        raise ApprovalProtocolError("payload too large to review completely")
    return raw


class ApprovalBroker:
    """Trusted-process state machine, with trusted UI and executor adapters.

    Never expose accept_ui_response to a planner or attacker. This class alone
    cannot authenticate a UI or enforce an OS security boundary.
    """

    def __init__(self, actions: Mapping[str, ActionSpec], *,
                 clock: Callable[[], float] = time.monotonic, ttl: float = 60.0) -> None:
        if not math.isfinite(ttl) or not 0 < ttl <= 300:
            raise ValueError("invalid broker timeout")
        if any(type(name) is not str or not name or type(spec) is not ActionSpec
               or not all(callable(fn) for fn in (spec.validate, spec.target, spec.may_display, spec.execute))
               or type(spec.risk) is not str or not spec.risk
               or type(spec.scope) is not str or not spec.scope for name, spec in actions.items()):
            raise ValueError("invalid trusted action registration")
        self._actions = dict(actions)
        self._clock = clock
        self._ttl = ttl
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[ApprovalCard, float, str]] = {}
        self._grants: dict[str, tuple[bytes, float, str]] = {}

    def _prune(self, now: float) -> None:
        self._pending = {k: v for k, v in self._pending.items() if now < v[1]}
        self._grants = {k: v for k, v in self._grants.items() if now < v[1]}

    def create_request(self, tool: str, arguments: dict, *, principal: str) -> ApprovalCard:
        # The trusted transport must derive principal from authenticated peer identity.
        if type(principal) is not str or not principal or len(principal) > 256:
            raise ApprovalProtocolError("invalid authenticated principal")
        if type(tool) is not str or tool not in self._actions:
            raise ApprovalProtocolError("unknown or disabled tool")
        if type(arguments) is not dict:
            raise ApprovalProtocolError("arguments must be an object")
        spec = self._actions[tool]
        try:
            validated = _snapshot(spec.validate(_snapshot(arguments)))
            if type(validated) is not dict or spec.may_display(_snapshot(validated)) is not True:
                raise ApprovalProtocolError("unsafe or undisplayable request")
            target = spec.target(_snapshot(validated))
            raw = canonical_payload(tool, validated, target, spec.risk, spec.scope)
        except ApprovalProtocolError:
            raise
        except Exception as exc:
            raise ApprovalProtocolError("trusted action validation failed") from exc
        card = ApprovalCard(secrets.token_hex(32), raw, hashlib.sha256(raw).hexdigest())
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._pending) + len(self._grants) >= 128:
                raise ApprovalProtocolError("approval queue full")
            self._pending[card.request_id] = (card, now + self._ttl, principal)
        return card

    def accept_ui_response(self, request_id: str, payload_hash: str, decision: bool) -> Grant | None:
        """Trusted UI channel handler only; callers must authenticate channel first."""
        if type(request_id) is not str or type(payload_hash) is not str or type(decision) is not bool:
            return None
        with self._lock:
            now = self._clock()
            self._prune(now)
            pending = self._pending.pop(request_id, None)
            if pending is None:
                return None
            card, deadline, principal = pending
            if (not decision or len(payload_hash) != 64
                    or any(c not in "0123456789abcdef" for c in payload_hash)
                    or not hmac.compare_digest(card.payload_hash, payload_hash)):
                return None
            handle = secrets.token_hex(32)
            self._grants[handle] = (card.canonical_bytes, deadline, principal)
            return Grant(handle)

    def execute(self, grant: Grant | None, canonical_bytes: bytes, *, principal: str) -> Any:
        """Consume once using a transport-authenticated principal and stored data."""
        if (type(grant) is not Grant or type(grant.handle) is not str
                or type(canonical_bytes) is not bytes or type(principal) is not str):
            return None
        with self._lock:
            now = self._clock()
            self._prune(now)
            entry = self._grants.pop(grant.handle, None)
            if entry is None:
                return None
            raw, deadline, approved_principal = entry
            if principal != approved_principal or not hmac.compare_digest(raw, canonical_bytes):
                return None
        payload = json.loads(raw)
        action = self._actions[payload["tool"]]
        try:
            current_target = action.target(_snapshot(payload["arguments"]))
            if type(current_target) is not str or current_target != payload["target"]:
                return None
            if not self._clock() < deadline:
                return None
        except Exception:
            return None
        # This check is defense in depth. The target can still change between
        # resolution and actuation; production needs an atomic host mechanism.
        return action.execute(payload["arguments"])
