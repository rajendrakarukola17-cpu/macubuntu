"""Strict approval-card wire helpers; NOT an authenticated transport by themselves.

Trusted host code must establish and protect the private socket channel. These
helpers are not connected to production and do not authenticate a peer, render
GTK, or establish process isolation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .approval_broker import ApprovalBroker, ApprovalCard, ApprovalProtocolError, Grant, _snapshot, canonical_payload

_MAX_FRAME = 65536
_HEX = frozenset("0123456789abcdef")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ApprovalProtocolError("duplicate JSON key")
        result[key] = value
    return result


def _reject_number(_value: str) -> None:
    raise ApprovalProtocolError("unsupported JSON numeric representation")


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("ascii"), object_pairs_hook=_pairs,
                          parse_float=_reject_number, parse_constant=_reject_number)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ApprovalProtocolError("invalid JSON") from exc


def _hex64(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(c in _HEX for c in value)


def validate_card_for_display(card: ApprovalCard) -> str:
    """Return full escaped canonical payload and hash, or reject invalid card.

    This tests display text only; it is not proof that a GUI renders that text.
    """
    if type(card) is not ApprovalCard or not _hex64(card.request_id) or not _hex64(card.payload_hash):
        raise ApprovalProtocolError("invalid request identity or hash")
    raw = card.canonical_bytes
    if type(raw) is not bytes or len(raw) > 16384:
        raise ApprovalProtocolError("invalid payload size")
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), card.payload_hash):
        raise ApprovalProtocolError("payload hash mismatch")
    data = _decode(raw)
    if type(data) is not dict or set(data) != {"version", "tool", "arguments", "target", "risk", "scope"}:
        raise ApprovalProtocolError("invalid payload fields")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ApprovalProtocolError("unknown approval protocol version")
    expected = canonical_payload(data["tool"], data["arguments"], data["target"], data["risk"], data["scope"])
    if not hmac.compare_digest(raw, expected):
        raise ApprovalProtocolError("payload is not canonical")
    return f"Full request SHA-256: {card.payload_hash}\nComplete canonical request:\n{raw.decode('ascii')}"


def _require_timeout(sock: socket.socket) -> None:
    timeout = sock.gettimeout()
    if timeout is None or timeout <= 0 or timeout > 300:
        raise ApprovalProtocolError("socket requires a bounded blocking timeout")


def _read_exact(sock: socket.socket, size: int, deadline: float) -> bytes:
    result = bytearray()
    try:
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ApprovalProtocolError("approval channel deadline exceeded")
            sock.settimeout(remaining)
            data = sock.recv(size - len(result))
            if not data:
                raise ApprovalProtocolError("truncated frame")
            result.extend(data)
    except (OSError, TimeoutError) as exc:
        raise ApprovalProtocolError("approval channel unavailable") from exc
    return bytes(result)


def read_message(sock: socket.socket) -> dict[str, Any]:
    _require_timeout(sock)
    timeout = sock.gettimeout()
    deadline = time.monotonic() + timeout
    try:
        size = int.from_bytes(_read_exact(sock, 4, deadline), "big")
        if not 0 < size <= _MAX_FRAME:
            raise ApprovalProtocolError("invalid frame size")
        result = _decode(_read_exact(sock, size, deadline))
        if type(result) is not dict:
            raise ApprovalProtocolError("message must be an object")
        return _snapshot(result)
    finally:
        sock.settimeout(timeout)


def send_message(sock: socket.socket, message: dict[str, Any]) -> None:
    _require_timeout(sock)
    if type(message) is not dict:
        raise ApprovalProtocolError("message must be an object")
    raw = json.dumps(_snapshot(message), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if not 0 < len(raw) <= _MAX_FRAME:
        raise ApprovalProtocolError("invalid frame size")
    try:
        sock.sendall(len(raw).to_bytes(4, "big") + raw)
    except (OSError, TimeoutError) as exc:
        raise ApprovalProtocolError("approval channel unavailable") from exc


def card_from_message(message: dict[str, Any]) -> ApprovalCard:
    """Decode and validate the entire security-relevant UI payload."""
    if type(message) is not dict or set(message) != {"kind", "request_id", "payload_hash", "payload"}:
        raise ApprovalProtocolError("unexpected card message fields")
    if message["kind"] != "approval_request" or type(message["payload"]) is not str:
        raise ApprovalProtocolError("invalid card message")
    try:
        raw = message["payload"].encode("ascii")
    except UnicodeError as exc:
        raise ApprovalProtocolError("payload is not canonical ASCII") from exc
    card = ApprovalCard(message["request_id"], raw, message["payload_hash"])
    validate_card_for_display(card)
    return card


class BrokerUIChannel:
    """Broker side of one private inherited UI socket.

    It validates protocol state but does not create or authenticate a peer.
    A trusted launcher must guarantee descriptor provenance and confinement;
    passing a caller-controlled socket here is insecure.
    """

    def __init__(self, sock: socket.socket) -> None:
        _require_timeout(sock)
        if sock.family != socket.AF_UNIX or sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
            raise ApprovalProtocolError("approval channel must be a connected Unix stream")
        try:
            sock.getpeername()
        except OSError as exc:
            raise ApprovalProtocolError("approval channel is not connected") from exc
        self._socket = sock
        self._lock = threading.Lock()

    def request(self, broker: ApprovalBroker, card: ApprovalCard) -> Grant | None:
        """Send one card, validate its response and deny on errors or concurrency."""
        if not self._lock.acquire(blocking=False):
            broker.accept_ui_response(card.request_id, card.payload_hash, False)
            return None
        completed = False
        try:
            validate_card_for_display(card)
            send_message(self._socket, {"kind": "approval_request", "request_id": card.request_id,
                                        "payload_hash": card.payload_hash,
                                        "payload": card.canonical_bytes.decode("ascii")})
            response = read_message(self._socket)
            if (set(response) != {"request_id", "payload_hash", "decision"}
                    or type(response["decision"]) is not bool
                    or response["request_id"] != card.request_id
                    or response["payload_hash"] != card.payload_hash):
                raise ApprovalProtocolError("approval response does not match request")
            grant = broker.accept_ui_response(card.request_id, card.payload_hash, response["decision"])
            completed = True
            return grant
        except (ApprovalProtocolError, OSError, UnicodeError):
            # Do not reuse a channel that could contain a late/stale response.
            self._socket.close()
            return None
        finally:
            if not completed:
                broker.accept_ui_response(card.request_id, card.payload_hash, False)
            self._lock.release()


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """Exact validated payload a future trusted UI must display without truncation."""
    card: ApprovalCard
    display_text: str


class UIApprovalChannel:
    """UI-side single-flight protocol helper, not an authenticated UI or channel.

    A future trusted launcher must supply an authenticated, private socket and
    ensure this object is inaccessible to untrusted workers. `receive` and
    `respond` do not render a GUI or prove that a human saw or approved anything.
    Callers must cancel/close on display errors, timeout or UI destruction.
    """

    def __init__(self, sock: socket.socket, *, clock: Callable[[], float] = time.monotonic) -> None:
        _require_timeout(sock)
        try:
            if sock.family != socket.AF_UNIX or sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
                raise ApprovalProtocolError("approval channel must be a connected Unix stream")
            sock.getpeername()
        except OSError as exc:
            raise ApprovalProtocolError("approval channel is not connected") from exc
        self._socket = sock
        self._review_timeout = sock.gettimeout()
        self._clock = clock
        self._lock = threading.Lock()
        self._reading = False
        self._closed = False
        self._pending: tuple[ApprovalCard, float] | None = None

    def _close_locked(self) -> None:
        self._pending = None
        if not self._closed:
            self._closed = True
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()

    def close(self) -> None:
        """Invalidate pending review and interrupt any blocked receive."""
        with self._lock:
            self._close_locked()

    def receive(self) -> ReviewRequest:
        """Read one bounded request; refuse another while review is pending."""
        with self._lock:
            if self._closed or self._reading or self._pending is not None:
                raise ApprovalProtocolError("approval channel unavailable or review already pending")
            self._reading = True
        try:
            card = card_from_message(read_message(self._socket))
            request = ReviewRequest(card, validate_card_for_display(card))
            with self._lock:
                if self._closed:
                    raise ApprovalProtocolError("approval channel closed")
                self._pending = (card, self._clock() + self._review_timeout)
            return request
        except (ApprovalProtocolError, OSError, UnicodeError, ValueError) as exc:
            self.close()
            if isinstance(exc, ApprovalProtocolError):
                raise
            raise ApprovalProtocolError("approval channel unavailable") from exc
        finally:
            with self._lock:
                self._reading = False

    def respond(self, request_id: str, payload_hash: str, decision: bool) -> bool:
        """Send one matching decision, or return False without approving.

        A stale/mismatched callback cannot resolve or cancel the current card.
        Timeout, channel failure or explicit close burns the pending review.
        """
        if not _hex64(request_id) or not _hex64(payload_hash) or type(decision) is not bool:
            return False
        with self._lock:
            if self._closed or self._pending is None:
                return False
            card, deadline = self._pending
            if not hmac.compare_digest(request_id, card.request_id) or not hmac.compare_digest(payload_hash, card.payload_hash):
                return False
            if not self._clock() < deadline:
                self._close_locked()
                return False
            self._pending = None
            try:
                send_message(self._socket, {"request_id": card.request_id,
                                            "payload_hash": card.payload_hash,
                                            "decision": decision})
                return True
            except (ApprovalProtocolError, OSError):
                self._close_locked()
                return False
