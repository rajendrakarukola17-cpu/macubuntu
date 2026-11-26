"""Celina AI — Capability #9: Unified Capability Bus (D-Bus / Unix Socket / MCP)

Single authoritative policy engine shared by ALL THREE transports:
  1. `dbus`        — Session D-Bus (`org.macubuntu.Celina` / `org.macubuntu.Celina.Autonomy`)
  2. `unix_socket` — Local AF_UNIX socket (`0600`) with kernel `SO_PEERCRED` peer-UID verification
  3. `mcp`         — Inbound MCP server (`mcp_server.py`) & external MCP callers

Tier-2 and non-read-only requests through this bus are disabled until broker-owned
execution is integrated. Only explicitly configured Tier-1 requests classified
read-only are passed onward. This classification has not been independently
verified; checks do not establish isolation from a malicious same-UID process.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import socket
import struct
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from . import approval, config, evidence, governance, net_policy, telemetry, tools
from .approval_broker import ApprovalProtocolError, canonical_payload

VALID_TRANSPORTS: frozenset[str] = frozenset({"dbus", "unix_socket", "mcp", "cli"})
ApprovalCallback = Callable[[str, str], bool]


@dataclasses.dataclass(frozen=True)
class BusRequest:
    transport: str
    tool_name: str
    arguments: dict[str, Any]
    caller_uid: int
    uid_provenance: str = "untrusted"   # "dbus" | "unix_socket" | "in_process" | "untrusted"
    root_goal_id: str = ""
    task_id: str = ""
    trace_id: str | None = None
    provenance: governance.ProvenanceTag | None = None


@dataclasses.dataclass(frozen=True)
class BusResponse:
    ok: bool
    transport: str
    tool_name: str
    result: str
    policy_decision: str
    grant_id: str = ""
    trace_id: str = ""
    span_id: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class UnifiedCapabilityBus:
    """Shared transport-agnostic policy enforcement bus for D-Bus, Unix Socket & MCP."""

    def __init__(
        self,
        *,
        expected_uid: int | None = None,
        substrate: governance.GovernanceSubstrate | None = None,
        tracer: telemetry.CelinaTracer | None = None,
        ledger: evidence.EvidenceLedger | None = None,
        executor: Callable[..., str] | None = None,
    ):
        self.expected_uid = int(os.getuid() if expected_uid is None else expected_uid)
        self.substrate = substrate or governance.GovernanceSubstrate(expected_uid=self.expected_uid)
        self.tracer = tracer or telemetry.get_tracer()
        self.ledger = ledger or evidence.get_evidence_ledger()
        self._executor = executor or tools.execute_tool

    def dispatch(
        self,
        req: BusRequest,
        *,
        approval_cb: ApprovalCallback | None = None,
        online_probe: Callable[[], bool] | None = None,
    ) -> BusResponse:
        transport = (req.transport or "").strip().lower()
        if transport not in VALID_TRANSPORTS:
            return BusResponse(
                ok=False,
                transport=transport,
                tool_name=req.tool_name,
                result="",
                policy_decision="deny:unknown_transport",
                error=f"Unsupported capability bus transport '{req.transport}'.",
            )

        with self.tracer.start_span(
            f"bus.{transport}.{req.tool_name}",
            kind="bus",
            trace_id=req.trace_id,
            root_goal_id=req.root_goal_id,
            inputs={"transport": transport, "tool": req.tool_name, "caller_uid": req.caller_uid},
            attributes={"bus.transport": transport, "bus.caller_uid": req.caller_uid},
        ) as span:
            # 1. Peer UID enforcement (identical across D-Bus, Unix Socket SO_PEERCRED, and MCP)
            if int(req.caller_uid) != self.expected_uid:
                err = f"Access denied on {transport}: caller UID {req.caller_uid} != owner UID {self.expected_uid}."
                span.status = "error:uid_denied"
                return BusResponse(
                    ok=False,
                    transport=transport,
                    tool_name=req.tool_name,
                    result="",
                    policy_decision="deny:uid_mismatch",
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    error=err,
                )

            # 1b. Fail-closed: MCP transport does not provide a trustworthy caller UID.
            #     Require explicit provenance from dbus / unix_socket / in_process.
            if req.uid_provenance not in {"dbus", "unix_socket", "in_process"}:
                err = f"Transport '{transport}' does not provide a trustworthy caller UID; refusing Tier-2 access."
                span.status = "error:untrusted_transport"
                return BusResponse(
                    ok=False,
                    transport=transport,
                    tool_name=req.tool_name,
                    result="",
                    policy_decision="deny:untrusted_transport",
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    error=err,
                )

            # A boolean callback or same-UID identity cannot establish the
            # approved broker boundary. Snapshot/validate before risk checks so
            # caller mutations cannot change which arguments are dispatched.
            try:
                frozen = canonical_payload(req.tool_name, req.arguments, "bus", "classification", "bus")
                args = json.loads(frozen)["arguments"]
                risk = approval.classify_risk(req.tool_name, args)
            except (ApprovalProtocolError, TypeError, ValueError, RecursionError):
                span.status = "error:invalid_arguments"
                return BusResponse(
                    ok=False, transport=transport, tool_name=req.tool_name, result="",
                    policy_decision="deny:invalid_arguments", trace_id=span.trace_id,
                    span_id=span.span_id, error="Denied: invalid or unsupported capability request.",
                )
            is_t2 = req.tool_name in config.TIER2_APPROVAL_TOOLS
            if (is_t2 or req.tool_name not in config.TIER1_AUTO_TOOLS
                    or risk != approval.RiskLevel.READ_ONLY):
                span.status = "error:approval_broker_unavailable"
                return BusResponse(
                    ok=False,
                    transport=transport,
                    tool_name=req.tool_name,
                    result="",
                    policy_decision="deny:approval_broker_unavailable",
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    error="Denied: this capability requires a trusted approval broker, which is not available.",
                )
            req = dataclasses.replace(req, arguments=args)

            # 2. Prompt-injection scan on serialized arguments -> security audit event
            #    Uses approval._jsonable for canonical serialisation (same as compute_arguments_hash)
            args_str = json.dumps(
                approval._jsonable(req.arguments or {}),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            inj_hits = evidence.detect_prompt_injection(args_str)
            if inj_hits:
                try:
                    self.ledger.record_security_event(
                        event_type="security_prompt_injection",
                        tool_name=req.tool_name,
                        detail=f"Prompt-injection patterns {inj_hits} detected on {transport}",
                        root_goal_id=req.root_goal_id,
                        task_id=req.task_id,
                        trace_id=span.trace_id,
                        actor_identity=f"uid:{req.caller_uid}@{transport}",
                    )
                except Exception as exc:
                    log.error("EVIDENCE LEDGER SECURITY EVENT WRITE FAILED for %s on %s: %s",
                              req.tool_name, transport, exc)
                    span.status = "error:evidence_write_failed"
                    return BusResponse(
                        ok=False,
                        transport=transport,
                        tool_name=req.tool_name,
                        result="",
                        policy_decision="deny:evidence_failure",
                        trace_id=span.trace_id,
                        span_id=span.span_id,
                        error="Denied: evidence ledger is unavailable; refusing to record security event.",
                    )
                span.status = "error:prompt_injection_blocked"
                return BusResponse(
                    ok=False,
                    transport=transport,
                    tool_name=req.tool_name,
                    result="",
                    policy_decision="deny:prompt_injection",
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    error=f"Blocked suspected prompt-injection payload ({', '.join(inj_hits)}).",
                )

            # 3. Centralized net_policy offline & opt-in guard for web_comms tools
            #    Inspect all common URL keys, not just "url".
            _URL_KEYS = ("url", "webhook_url", "endpoint", "target_url", "href")
            url_hint = None
            if isinstance(req.arguments, dict):
                for _k in _URL_KEYS:
                    _v = req.arguments.get(_k)
                    if isinstance(_v, str):
                        url_hint = _v
                        break
            try:
                net_policy.check_web_comms_allowed(
                    req.tool_name,
                    url=url_hint,
                    online_probe=online_probe,
                )
            except (net_policy.OfflineError, net_policy.URLPolicyError) as exc:
                span.status = "error:net_policy"
                return BusResponse(
                    ok=False,
                    transport=transport,
                    tool_name=req.tool_name,
                    result="",
                    policy_decision="deny:net_policy",
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    error=str(exc),
                )

            # 4. System-boundary capability + GovernanceSubstrate one-use HMAC grant
            try:
                grant = self.substrate.issue_grant(
                    tool_name=req.tool_name,
                    arguments=req.arguments or {},
                    actor_uid=req.caller_uid,
                    is_tier2=is_t2,
                    provenance=req.provenance,
                )
                self.substrate.verify_and_consume_grant(
                    grant,
                    tool_name=req.tool_name,
                    arguments=req.arguments or {},
                    actor_uid=req.caller_uid,
                )
            except (governance.GovernanceError, governance.GovernanceTamperError) as exc:
                span.status = "error:governance"
                return BusResponse(
                    ok=False,
                    transport=transport,
                    tool_name=req.tool_name,
                    result="",
                    policy_decision="deny:governance",
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    error=str(exc),
                )

            # 5. Execute only the non-Tier-2 route. This does not certify the
            # classification list or prevent other direct executor entry points.
            result_str = str(self._executor(req.tool_name, req.arguments or {}, approval_cb=approval_cb))
            span.set_io(inputs=req.arguments or {}, outputs={"result_preview": result_str[:240]})
            span.set_attribute("governance.grant_id", grant.grant_id)

            # Currently unreachable because all Tier-2 requests return above.
            # A broker migration must redesign evidence ordering: recording a
            # failure after an actuator runs cannot undo that operation.
            if is_t2:
                evidence_ok = True
                try:
                    self.ledger.record_execution(
                        tool_name=req.tool_name,
                        arguments=req.arguments or {},
                        result_text=result_str,
                        root_goal_id=req.root_goal_id,
                        task_id=req.task_id,
                        trace_id=span.trace_id,
                        span_id=span.span_id,
                        actor_identity=f"uid:{req.caller_uid}@{transport}",
                        policy_decision=f"allow:grant={grant.grant_id}",
                    )
                except Exception as exc:
                    evidence_ok = False
                    log.error("EVIDENCE LEDGER WRITE FAILED for %s on %s: %s",
                              req.tool_name, transport, exc)

                if not evidence_ok:
                    span.status = "error:evidence_write_failed"
                    return BusResponse(
                        ok=False,
                        transport=transport,
                        tool_name=req.tool_name,
                        result="",
                        policy_decision="deny:evidence_failure",
                        grant_id=grant.grant_id,
                        trace_id=span.trace_id,
                        span_id=span.span_id,
                        error="Denied: evidence ledger is unavailable; refusing to record successful Tier-2 action.",
                    )

            ok = not result_str.startswith(("Denied:", "Cancelled", "Blocked", "Error:"))
            if not ok:
                span.status = "error:tool_failed"
            return BusResponse(
                ok=ok,
                transport=transport,
                tool_name=req.tool_name,
                result=result_str,
                policy_decision=f"allow:grant={grant.grant_id}",
                grant_id=grant.grant_id,
                trace_id=span.trace_id,
                span_id=span.span_id,
                error="" if ok else result_str,
            )


def get_unix_socket_peer_uid(conn: socket.socket) -> int:
    """Reads kernel-verified peer UID from an AF_UNIX socket via SO_PEERCRED (Linux)."""
    so_peercred = getattr(socket, "SO_PEERCRED", 17)
    creds = conn.getsockopt(socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return int(uid)


class UnixSocketCapabilityServer:
    """Lightweight local AF_UNIX JSON-line server (`0600`) routing directly into `UnifiedCapabilityBus`
    with kernel-enforced `SO_PEERCRED` peer-UID authentication.
    """

    def __init__(
        self,
        bus: UnifiedCapabilityBus,
        socket_path: Path | None = None,
        *,
        approval_cb: ApprovalCallback | None = None,
    ):
        self.bus = bus
        self.socket_path = Path(socket_path) if socket_path is not None else (config.BASE_DIR / "run" / "celina-bus.sock")
        self.approval_cb = approval_cb
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> Path:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        srv.listen(8)
        srv.settimeout(0.2)
        self._sock = srv
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()
        return self.socket_path

    def _serve_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                try:
                    peer_uid = get_unix_socket_peer_uid(conn)
                    data = conn.recv(65536).decode("utf-8", errors="replace").strip()
                    payload = json.loads(data) if data else {}
                    req = BusRequest(
                        transport="unix_socket",
                        tool_name=str(payload.get("tool_name", "")),
                        arguments=payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {},
                        caller_uid=peer_uid,
                        uid_provenance="unix_socket",
                        root_goal_id=str(payload.get("root_goal_id", "")),
                        task_id=str(payload.get("task_id", "")),
                    )
                    resp = self.bus.dispatch(req, approval_cb=self.approval_cb)
                    conn.sendall((json.dumps(dataclasses.asdict(resp)) + "\n").encode("utf-8"))
                except Exception as exc:
                    conn.sendall((json.dumps({"ok": False, "error": str(exc)}) + "\n").encode("utf-8"))

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass


_DEFAULT_BUS: UnifiedCapabilityBus | None = None


def get_capability_bus() -> UnifiedCapabilityBus:
    global _DEFAULT_BUS
    if _DEFAULT_BUS is None:
        _DEFAULT_BUS = UnifiedCapabilityBus()
    return _DEFAULT_BUS
