"""
Celina AI — Capability #9: Unified Capability Bus (D-Bus / Unix Socket / MCP)

Single authoritative policy engine shared by ALL THREE transports:
  1. `dbus`        — Session D-Bus (`org.macubuntu.Celina` / `org.macubuntu.Celina.Autonomy`)
  2. `unix_socket` — Local AF_UNIX socket (`0600`) with kernel `SO_PEERCRED` peer-UID verification
  3. `mcp`         — Inbound MCP server (`mcp_server.py`) & external MCP callers

Every transport passes through the EXACT same pipeline:
  peer-UID check -> prompt-injection scan -> net_policy offline/opt-in guard ->
  OS capability manifest boundary -> GovernanceSubstrate one-use HMAC grant ->
  Tier-2 approval gate -> OpenTelemetry durable span -> intent-linked evidence audit.
"""

from __future__ import annotations

import dataclasses
import json
import os
import socket
import struct
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from . import approval, capabilities, config, evidence, governance, net_policy, telemetry, tools

VALID_TRANSPORTS: frozenset[str] = frozenset({"dbus", "unix_socket", "mcp", "cli"})
ApprovalCallback = Callable[[str, str], bool]


@dataclasses.dataclass(frozen=True)
class BusRequest:
    transport: str
    tool_name: str
    arguments: dict[str, Any]
    caller_uid: int
    root_goal_id: str = ""
    task_id: str = ""
    trace_id: Optional[str] = None
    provenance: Optional[governance.ProvenanceTag] = None


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
        expected_uid: Optional[int] = None,
        substrate: Optional[governance.GovernanceSubstrate] = None,
        tracer: Optional[telemetry.CelinaTracer] = None,
        ledger: Optional[evidence.EvidenceLedger] = None,
        executor: Optional[Callable[..., str]] = None,
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
        approval_cb: Optional[ApprovalCallback] = None,
        online_probe: Optional[Callable[[], bool]] = None,
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

            # 2. Prompt-injection scan on serialized arguments -> security audit event
            args_str = json.dumps(req.arguments or {}, sort_keys=True, default=str)
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
                except OSError:
                    pass
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
            url_hint = req.arguments.get("url") if isinstance(req.arguments, dict) else None
            try:
                net_policy.check_web_comms_allowed(
                    req.tool_name,
                    url=str(url_hint) if isinstance(url_hint, str) else None,
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
            is_t2 = req.tool_name in config.TIER2_APPROVAL_TOOLS
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

            # 5. Tier-2 fail-closed HITL gate enforced uniformly at the bus boundary
            effective_approval_cb = approval_cb
            if is_t2:
                if approval_cb is None:
                    err = f"Denied: Tier-2 tool '{req.tool_name}' on {transport} requires explicit interactive approval."
                    span.status = "error:approval_required"
                    return BusResponse(
                        ok=False,
                        transport=transport,
                        tool_name=req.tool_name,
                        result="",
                        policy_decision="deny:tier2_no_approval",
                        grant_id=grant.grant_id,
                        trace_id=span.trace_id,
                        span_id=span.span_id,
                        error=err,
                    )
                if self._executor is not tools.execute_tool:
                    # Enforce HITL directly at the bus when a custom/MCP executor is attached
                    try:
                        approved = bool(
                            approval_cb(
                                f"Approve {req.tool_name} ({transport})",
                                json.dumps(req.arguments or {}, sort_keys=True)[:300],
                            )
                        )
                    except TypeError:
                        approved = bool(approval_cb(req.tool_name, req.arguments or {}))
                    if not approved:
                        err = f"Denied: User rejected Tier-2 tool '{req.tool_name}' on {transport}."
                        span.status = "error:approval_denied"
                        return BusResponse(
                            ok=False,
                            transport=transport,
                            tool_name=req.tool_name,
                            result="",
                            policy_decision="deny:tier2_user_rejected",
                            grant_id=grant.grant_id,
                            trace_id=span.trace_id,
                            span_id=span.span_id,
                            error=err,
                        )

            # 6. Execute through single unified tool pipeline
            result_str = str(self._executor(req.tool_name, req.arguments or {}, approval_cb=effective_approval_cb))
            span.set_io(inputs=req.arguments or {}, outputs={"result_preview": result_str[:240]})
            span.set_attribute("governance.grant_id", grant.grant_id)

            if is_t2:
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
                except OSError:
                    pass

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
    """
    Lightweight local AF_UNIX JSON-line server (`0600`) routing directly into `UnifiedCapabilityBus`
    with kernel-enforced `SO_PEERCRED` peer-UID authentication.
    """

    def __init__(
        self,
        bus: UnifiedCapabilityBus,
        socket_path: Optional[Path] = None,
        *,
        approval_cb: Optional[ApprovalCallback] = None,
    ):
        self.bus = bus
        self.socket_path = Path(socket_path) if socket_path is not None else (config.BASE_DIR / "run" / "celina-bus.sock")
        self.approval_cb = approval_cb
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

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
            except socket.timeout:
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


_DEFAULT_BUS: Optional[UnifiedCapabilityBus] = None


def get_capability_bus() -> UnifiedCapabilityBus:
    global _DEFAULT_BUS
    if _DEFAULT_BUS is None:
        _DEFAULT_BUS = UnifiedCapabilityBus()
    return _DEFAULT_BUS
