"""Trusted C2 approval broker host adapter over a private UI socketpair.

Bridges ``approval_broker.ApprovalBroker`` and ``approval_transport.BrokerUIChannel``
so a future isolated launcher can wire sensitive actuators through one atomic
create -> display -> grant -> consume path without weakening Gate 4.7 defaults.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .approval_broker import ActionSpec, ApprovalBroker, ApprovalProtocolError
from .approval_transport import BrokerUIChannel, UIApprovalChannel


@dataclass(frozen=True, slots=True)
class BrokerDispatchResult:
    """Outcome of one brokered request_and_execute call."""

    approved: bool
    executed: bool
    request_id: str
    payload_hash: str
    output: Any = None
    error: str | None = None


def create_ui_socketpair(timeout: float = 60.0) -> tuple[BrokerUIChannel, UIApprovalChannel]:
    """Create a connected private Unix stream pair with bounded timeouts."""
    if not 0 < timeout <= 300:
        raise ValueError("timeout must be in (0, 300]")
    sock_type = socket.SOCK_STREAM | getattr(socket, "SOCK_CLOEXEC", 0)
    broker_sock, ui_sock = socket.socketpair(socket.AF_UNIX, sock_type)
    broker_sock.settimeout(timeout)
    ui_sock.settimeout(timeout)
    return BrokerUIChannel(broker_sock), UIApprovalChannel(ui_sock)


class BrokerHost:
    """Single-flight trusted host coordinator for ApprovalBroker + BrokerUIChannel."""

    def __init__(self, actions: Mapping[str, ActionSpec], ui_channel: BrokerUIChannel, *, ttl: float = 60.0) -> None:
        self._broker = ApprovalBroker(actions, ttl=ttl)
        self._ui = ui_channel

    def request_and_execute(self, tool: str, arguments: dict[str, Any], *, principal: str) -> BrokerDispatchResult:
        """Create a canonical request, obtain UI approval, and execute once atomically."""
        try:
            card = self._broker.create_request(tool, arguments, principal=principal)
        except ApprovalProtocolError as exc:
            return BrokerDispatchResult(
                approved=False,
                executed=False,
                request_id="",
                payload_hash="",
                error=str(exc),
            )

        grant = self._ui.request(self._broker, card)
        if grant is None:
            return BrokerDispatchResult(
                approved=False,
                executed=False,
                request_id=card.request_id,
                payload_hash=card.payload_hash,
                error="approval_denied_or_unavailable",
            )

        result = self._broker.execute(grant, card.canonical_bytes, principal=principal)
        if result is None:
            return BrokerDispatchResult(
                approved=True,
                executed=False,
                request_id=card.request_id,
                payload_hash=card.payload_hash,
                error="grant_consumption_rejected",
            )

        return BrokerDispatchResult(
            approved=True,
            executed=True,
            request_id=card.request_id,
            payload_hash=card.payload_hash,
            output=result,
        )


__all__ = [
    "BrokerDispatchResult",
    "BrokerHost",
    "create_ui_socketpair",
]
