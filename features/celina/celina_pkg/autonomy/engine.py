"""Celina v5 Autonomy Engine (`engine.py`)

Durable worker consuming `tasks.db` (`TaskGraph`), routing leaf goals through
Celina's cognitive router and propagating task state. Direct tool dispatch is
disabled while a trusted approval broker is unavailable; Tier-2 observe-only
records are still supported. This does not establish OS process confinement.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import approval, config, evidence, telemetry
from ..planner.scaffolding import LogicalScaffolder
from ..planner.task_graph import TaskGraph
from ..world_model import OSWorldModel
from .mcp_loader import MCP_PERCEPTION_ACTUATION_TOOLS, MCP_TOOL_ROUTING, MCPLoader
from .reliability import (
    AgentFuse,
    AgentSRE,
    CircuitBreakerTripped,
    GuardLoop,
    RecoveryRung,
    TaskResult,
    reliable,
)
from .saga import CapabilityRouteTable, SagaCoordinator
from .self_healing import ExecResult, SelfHealingExecutor

log = logging.getLogger("celina.autonomy")

_GOAL_STOPWORDS = frozenset(
    {"the", "and", "for", "with", "from", "that", "this", "please", "run", "check", "open", "set"},
)
_SUGGESTED_FALLBACK_RE = re.compile(r"suggested_fallback=([A-Za-z0-9_]+)")


def _extract_goal_entity(goal: str, fallback: str = "") -> str:
    toks = [t for t in re.findall(r"[A-Za-z0-9_.-]{3,}", (goal or "").lower()) if t not in _GOAL_STOPWORDS]
    return toks[0] if toks else (fallback or (goal or "").strip().lower()[:32])


def _canonical_args_hash(tool_name: str, arguments: Any) -> str:
    """Compute canonical SHA-256 over the exact (tool-bound) arguments dict.

    Never trusts a router-supplied arguments_hash — always recompute from the
    actual arguments payload so the approval binding matches what executes.
    Uses the same canonical form as approval.compute_arguments_hash for
    consistency with the evidence ledger.
    """
    from .. import approval as _approval
    return _approval.compute_arguments_hash(tool_name, arguments)


def _authoritative_tier(tool_name: str, is_mcp_pa: bool, router_tier: Any) -> int:
    """Never let the router downgrade a Tier-2 or unknown tool to Tier-1 (fail-closed).

    - MCP perception/actuation tools are always Tier-2 (external process).
    - Tools in config.TIER2_APPROVAL_TOOLS are always Tier-2.
    - Tier-1 tools accept the router's tier (validated >= 1).
    - Unknown tools default to Tier-2 (fail-closed).
    """
    if is_mcp_pa:
        return 2
    if tool_name in config.TIER2_APPROVAL_TOOLS:
        return 2
    if tool_name in config.TIER1_AUTO_TOOLS:
        try:
            return max(1, int(router_tier))
        except (TypeError, ValueError):
            return 2
    # Unknown tool -> fail-closed Tier-2
    return 2


def _trusted_autonomy_dispatch_available() -> bool:
    """No broker-owned autonomy dispatch is wired yet.

    Do not enable legacy dispatch by changing this return value: it must be
    replaced with an authenticated, broker-owned execution path first.
    This in-process guard is not protection against compromised process code.
    """
    return False


class AutonomyEngine:
    def __init__(
        self,
        graph: TaskGraph,
        executor: SelfHealingExecutor,
        router: Any,
        approval_gate: Any,
        *,
        observe_only: bool = True,
        state_cb: Callable[[str, str], None] | None = None,
        mcp_action_cb: Callable[[str, str], None] | None = None,
        mcp_loader: MCPLoader | None = None,
        tracer: telemetry.CelinaTracer | None = None,
        route_table: CapabilityRouteTable | None = None,
        saga: SagaCoordinator | None = None,
        sre: AgentSRE | None = None,
        fuse: AgentFuse | None = None,
        memory: Any | None = None,
        guard_loop: GuardLoop | None = None,
        os_world_model: OSWorldModel | None = None,
        recover_stuck: bool = True,
        speak_errors: bool = True,
    ):
        self.graph = graph
        self.executor = executor
        self.router = router
        self.approval = approval_gate
        self.observe_only = bool(observe_only)
        self._state_cb = state_cb
        self._mcp_action_cb = mcp_action_cb
        self.mcp_loader = mcp_loader
        self._stop = False
        self.speak_errors = bool(speak_errors)
        self.last_struggle_reason: str = ""
        self.scaffolder = LogicalScaffolder()
        self.tracer = tracer
        self.route_table = route_table or CapabilityRouteTable()
        self.saga = saga or SagaCoordinator(
            saga_id="autonomy-engine",
            route_table=self.route_table,
            tracer=tracer,
        )
        self.sre = sre or AgentSRE()
        self.fuse = fuse or AgentFuse()
        self.os_world_model = os_world_model or OSWorldModel()
        self.memory = memory
        self.guard_loop = guard_loop or GuardLoop(tracer=tracer)
        self.recovered_task_ids: list[str] = []
        if recover_stuck and hasattr(self.graph, "recover_on_startup"):
            self.recovered_task_ids = list(self.graph.recover_on_startup())

    def request_stop(self) -> None:
        self._stop = True

    def _emit_state(self, task_id: str, status: str) -> None:
        if self._state_cb is not None:
            try:
                self._state_cb(task_id, status)
            except Exception as exc:
                log.debug("state_cb(%s, %s) failed: %s", task_id, status, exc)

    def _emit_mcp_action(self, server: str, tool: str) -> None:
        """Emits MCPActionStarted(server, tool) for the D-Bus / Dynamic Island progress pill."""
        if self._mcp_action_cb is not None:
            try:
                self._mcp_action_cb(server, tool)
            except Exception as exc:
                log.debug("mcp_action_cb(%s, %s) failed: %s", server, tool, exc)

    def _speak_struggle_explanation(self, goal: str, reason: str, suggestion: str = "") -> str:
        """Spoken Honesty ("Why I'm Struggling"):
        When AgentFuse trips or an autonomous task gets blocked repeatedly, records a plain-English
        explanation and speaks it aloud (when voice is available & enabled) instead of failing silently.
        """
        short_goal = (goal or "this task").strip()[:90]
        msg = f"I'm struggling to complete '{short_goal}'. {reason.strip()}"
        if suggestion.strip():
            msg = f"{msg} {suggestion.strip()}"
        self.last_struggle_reason = msg
        log.warning("Celina struggle explanation: %s", msg)

        if not self.speak_errors:
            return msg

        # Prefer an explicit router.voice.speak(...) hook when attached (works in tests & live UI)
        router_voice = getattr(self.router, "voice", None)
        if router_voice is not None and callable(getattr(router_voice, "speak", None)):
            try:
                router_voice.speak(msg)
                return msg
            except Exception as exc:
                log.debug("router.voice.speak failed: %s", exc)

        # Avoid triggering audio hardware during pytest unless router.voice was explicitly mocked
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return msg
        try:
            from .. import voice as _voice

            _voice.speak_text(msg, enabled=True)
        except Exception as exc:
            log.debug("voice.speak_text struggle alert skipped: %s", exc)
        return msg

    @telemetry.agent_span("autonomy_engine", self_attr="tracer", governance_tier="autonomy")
    def step(self, *, state: dict[str, Any] | None = None) -> str | None:
        """Consume and process at most one runnable task. Returns task_id or None."""
        task = self.graph.next_runnable(state=state)
        if task is None:
            return None
        try:
            self._execute_one(task, state=state)
        except CircuitBreakerTripped as exc:
            # P0-9: @reliable timeout (max_seconds) fires outside _execute_one's
            # try/except — transition the task out of "running" so it never sticks.
            task_id = str(task["id"])
            log.exception("Autonomous task %s tripped circuit breaker", task_id)
            self.graph.mark(task_id, "failed", error=str(exc))
            self._emit_state(task_id, "failed")
            return str(task["id"])
        self.graph.advance_control_nodes(state=state)
        return str(task["id"])

    def run_until_idle(self, *, max_steps: int = 100, state: dict[str, Any] | None = None) -> int:
        processed = 0
        while processed < max_steps and not self._stop:
            tid = self.step(state=state)
            if tid is None:
                break
            processed += 1
        return processed

    def run_forever(self, poll_interval: float = 2.0) -> None:
        while not self._stop:
            try:
                tid = self.step()
            except Exception as exc:
                # P1: transient SQLite/operational errors must not crash the daemon
                log.exception("Autonomy engine step error, backing off: %s", exc)
                time.sleep(max(0.5, float(poll_interval)))
                continue
            if tid is None:
                time.sleep(max(0.1, float(poll_interval)))

    @reliable(sentinel_schema=TaskResult, fuse_limit=5, max_seconds=30.0)
    def _execute_one(self, task: dict[str, Any], *, state: dict[str, Any] | None = None) -> TaskResult:
        task_id = str(task["id"])
        final_status = "pending"
        final_tool = ""
        final_exit = 0
        final_summary = ""
        final_error = ""
        root_goal_id = str(task.get("root_id") or task.get("parent_id") or task_id)
        parent_task_id = str(task.get("parent_id") or "")
        log.info("Executing autonomous task %s: %s", task_id, task["goal"])
        self.graph.mark(task_id, "running", increment_attempt=False)
        self._emit_state(task_id, "running")

        # Capability #5: FORST pre-execution logical scaffolding + KV-cache anchor
        scaffold = self.scaffolder.build_scaffold(
            str(task["goal"]),
            world_state=state or {},
        )
        # Capability #12/#16: Open durable OTel trace/span
        tracer = self.tracer
        trace_id = (
            tracer.store.start_trace(f"autonomy:{task_id}", root_goal_id=root_goal_id)
            if tracer is not None
            else ""
        )
        span_ctx = (
            tracer.start_span(
                "autonomy.execute_task",
                trace_id=trace_id,
                root_goal_id=root_goal_id,
                attributes={"task_id": task_id, "root_goal_id": root_goal_id, "kv_anchor_id": scaffold.kv_anchor_id},
            )
            if tracer is not None
            else None
        )
        span_obj = span_ctx.__enter__() if span_ctx is not None else None

        try:
            # Capability #13: Prompt-injection detection on incoming task goal (T1)
            inj = evidence.detect_prompt_injection(str(task["goal"]))
            if inj:
                try:
                    evidence.get_evidence_ledger().record_security_event(
                        event_type="prompt_injection_detected",
                        summary=f"Autonomy task {task_id} matched injection signatures: {inj}",
                        root_goal_id=root_goal_id,
                        task_id=task_id,
                        parent_task_id=parent_task_id,
                        trace_id=trace_id,
                        span_id=span_obj.span_id if span_obj else "",
                        actor_identity="autonomy:engine",
                        policy_decision="deny:prompt_injection_blocked",
                    )
                except OSError:
                    pass
                inj_err = f"prompt_injection_blocked: {','.join(inj)}"
                ev_hash = self.graph.append_evidence(
                    task_id=task_id,
                    tool_name="security",
                    args_hash=_canonical_args_hash("security", {"goal": str(task["goal"])}),
                    exit_code=1,
                    backup_path=None,
                    why_hash="",
                )
                if span_obj is not None:
                    span_obj.status = "error"
                    span_obj.error = inj_err
                self.graph.mark(task_id, "blocked", error=inj_err, evidence_hash=ev_hash)
                self._emit_state(task_id, "blocked")
                return TaskResult(
                    task_id=task_id,
                    status="blocked",
                    tool_name="security",
                    exit_code=1,
                    error=inj_err,
                )

            # v4.5 Hybrid Memory Graph Walk: surface prior goals, failed attempts & related skills
            # BEFORE routing/planning so the engine does not repeat known-failed approaches.
            graph_context: dict[str, Any] = {}
            if self.memory is not None and hasattr(self.memory, "graph_walk"):
                try:
                    goal_entity = str((state or {}).get("goal_entity") or "").strip()
                    if not goal_entity:
                        goal_entity = _extract_goal_entity(str(task["goal"]), fallback=task_id)
                    graph_context = self.memory.graph_walk(goal_entity, hops=2)
                except Exception as exc:
                    log.debug("memory.graph_walk failed for task %s: %s", task_id, exc)
                    graph_context = {}

            last_err = str(task.get("last_error") or task.get("error") or "")
            fb_match = _SUGGESTED_FALLBACK_RE.search(last_err)
            suggested_fallback = fb_match.group(1) if fb_match else ""

            decision = self.router.route_for_autonomy(
                task["goal"],
                context={
                    "autonomy": True,
                    "task_id": task_id,
                    "root_goal_id": root_goal_id,
                    "trace_id": trace_id,
                    "scaffold_anchor": scaffold.anchor_prefix,
                    "kv_anchor_id": scaffold.kv_anchor_id,
                    "world_state": state or {},
                    "prior_graph_context": graph_context,
                    "known_failed_attempts": graph_context.get("failed_attempts", []),
                    "last_error": last_err,
                    "attempts": int(task.get("attempts") or 0),
                    "suggested_fallback": suggested_fallback,
                },
            )
            tool_name = str(decision.get("tool_name") or "unknown")
            # Router pseudo-tools are messages, not registered tool handlers. A
            # clarification or offline fallback must not complete the task.
            if tool_name in {"respond_directly", "respond_offline"}:
                response_args = decision.get("arguments")
                response_args = response_args if isinstance(response_args, dict) else {}
                raw_message = response_args.get("message", response_args.get("text", ""))
                message = raw_message if isinstance(raw_message, str) else ""
                message = message.strip()[:5000]
                needs_input = decision.get("requires_clarification") is True
                offline = tool_name == "respond_offline"
                denied = decision.get("capability_denied") is True
                blocked = needs_input or offline or denied or not message
                reason = (
                    "clarification_required" if needs_input else "offline_fallback" if offline
                    else "capability_denied" if denied else "invalid_response"
                )
                status = "blocked" if blocked else "done"
                exit_code = 1 if blocked else 0
                ev_hash = self.graph.append_evidence(
                    task_id=task_id, tool_name=tool_name,
                    args_hash=_canonical_args_hash(tool_name, response_args),
                    exit_code=exit_code, backup_path=None,
                    why_hash=str(decision.get("why_receipt_hash") or ""),
                )
                self.graph.mark(task_id, status, result=message, error=reason if blocked else None, evidence_hash=ev_hash)
                self._emit_state(task_id, status)
                if blocked and span_obj is not None:
                    span_obj.status = "error"
                    span_obj.error = reason
                return TaskResult(
                    task_id=task_id, status=status, tool_name=tool_name, exit_code=exit_code,
                    summary=message, error=reason if blocked else "",
                )
            domain = str(decision.get("domain") or "")
            is_mcp_pa = domain == "mcp_perception_actuation" or tool_name in MCP_PERCEPTION_ACTUATION_TOOLS
            mcp_meta = MCP_TOOL_ROUTING.get(tool_name, {})
            mcp_server = str(decision.get("server") or mcp_meta.get("server") or "")
            mcp_transport = str(decision.get("mcp_transport") or mcp_meta.get("transport") or "")
            # Every external MCP call is Tier-2 by definition (P0-2: authoritative tier)
            tier = _authoritative_tier(tool_name, is_mcp_pa, decision.get("tier", 2))

            # Concept 2: OS World Model pre-dispatch prediction (fail-closed)
            wm_args = decision.get("arguments", {})
            if not isinstance(wm_args, dict):
                wm_args = {}
            why_hash = str(decision.get("why_receipt_hash") or "")
            wm_error = ""
            try:
                predicted = self.os_world_model.predict_transition(
                    tool_name,
                    wm_args,
                    is_tier2=tier >= 2,
                    requires_network=bool(decision.get("requires_network", False)),
                    circuit_open=False,
                    fallback_candidates=decision.get("fallback_candidates"),
                )
                confidence = predicted.confidence
                if (type(confidence) not in (int, float) or not math.isfinite(confidence)
                        or not 0.0 < confidence <= 1.0):
                    note = getattr(predicted, "epistemic_note", "")
                    wm_error = note if type(note) is str and note else "invalid or blocked prediction"
            except Exception as exc:
                log.debug("world model predict_transition failed for %s: %s", tool_name, exc)
                wm_error = "prediction unavailable"
            if wm_error:
                err = f"world_model_blocked: {wm_error[:500]}"
                ev_hash = self.graph.append_evidence(
                    task_id=task_id,
                    tool_name=tool_name,
                    args_hash=_canonical_args_hash(tool_name, wm_args),
                    exit_code=126,
                    backup_path=None,
                    why_hash=why_hash,
                )
                if span_obj is not None:
                    span_obj.status = "error"
                    span_obj.error = "world_model_blocked"
                self.graph.mark(task_id, "blocked", error=err, evidence_hash=ev_hash)
                self._emit_state(task_id, "blocked")
                return TaskResult(task_id=task_id, status="blocked", tool_name=tool_name,
                                  exit_code=126, error=err)

            # P0-2: canonical args_hash from actual arguments, never trust router-supplied hash
            router_args_hash = str(decision.get("arguments_hash") or "")
            args_hash = router_args_hash or _canonical_args_hash(tool_name, decision.get("arguments", {}))
            if not router_args_hash:
                # Router supplied no hash — recompute canonically from arguments
                args_hash = _canonical_args_hash(tool_name, decision.get("arguments", {}))
            elif decision.get("arguments") is not None:
                # Router supplied a hash — verify it matches canonical args
                canonical = _canonical_args_hash(tool_name, decision.get("arguments", {}))
                if canonical != router_args_hash:
                    args_hash = canonical  # fail-closed: trust canonical over router
            why_hash = str(decision.get("why_receipt_hash") or "")

            # Safety Burn-In Mode: NEVER execute Tier-2 tools while observe_only=True
            if self.observe_only and tier >= 2:
                ev_hash = self.graph.append_evidence(
                    task_id=task_id,
                    tool_name=tool_name,
                    args_hash=args_hash,
                    exit_code=0,
                    backup_path=None,
                    why_hash=why_hash,
                )
                self.graph.mark(
                    task_id,
                    "observed",
                    result=f"[observe-only] would execute Tier-{tier} tool '{tool_name}'",
                    evidence_hash=ev_hash,
                )
                self._emit_state(task_id, "observed")
                return TaskResult(task_id=task_id, status="observed", tool_name=tool_name, exit_code=0)

            # The legacy ApprovalEngine callback cannot authorize broker-owned
            # execution. Even nominal Tier-1 decisions can carry alternate
            # executor arguments such as sandboxed_argv, so block all tool
            # dispatch, not just tools labeled Tier-2. Future work must replace
            # this with a real broker path, not enable the legacy branches.
            if not _trusted_autonomy_dispatch_available():
                err = "approval_broker_unavailable"
                ev_hash = self.graph.append_evidence(
                    task_id=task_id, tool_name=tool_name, args_hash=args_hash,
                    exit_code=126, backup_path=None, why_hash=why_hash,
                )
                if span_obj is not None:
                    span_obj.status = "error"
                    span_obj.error = err
                self.graph.mark(task_id, "blocked", error=err, evidence_hash=ev_hash)
                self._emit_state(task_id, "blocked")
                return TaskResult(task_id=task_id, status="blocked", tool_name=tool_name,
                                  exit_code=126, error=err)

            # Legacy, unreachable until replaced with broker-owned execution.
            # Explicit MCP Perception/Actuation Tier-2 approval gate (fail-closed)
            if is_mcp_pa:
                mcp_args_preview = json.dumps(
                    decision.get("arguments") or {}, ensure_ascii=False, sort_keys=True,
                )[:200]
                approved = bool(
                    self.approval.request_sync(
                        tool=tool_name,
                        arguments_hash=args_hash,
                        context=(
                            f"MCP: {mcp_server or 'external'} | "
                            f"Args: {mcp_args_preview} | Goal: {str(task['goal'])[:200]}"
                        ),
                    ),
                )
                if not approved:
                    ev_hash = self.graph.append_evidence(
                        task_id=task_id,
                        tool_name=tool_name,
                        args_hash=args_hash,
                        exit_code=126,
                        backup_path=None,
                        why_hash=why_hash,
                    )
                    if span_obj is not None:
                        span_obj.status = "error"
                        span_obj.error = "mcp_approval_denied"
                    self.graph.mark(
                        task_id,
                        "blocked",
                        error="mcp_approval_denied",
                        evidence_hash=ev_hash,
                    )
                    self._emit_state(task_id, "blocked")
                    return TaskResult(
                        task_id=task_id,
                        status="blocked",
                        tool_name=tool_name,
                        exit_code=126,
                        error="mcp_approval_denied",
                    )
                self._emit_mcp_action(mcp_server or "mcp", tool_name)
                if span_obj is not None:
                    span_obj.set_attribute("mcp.server", mcp_server)
                    span_obj.set_attribute("mcp.tool", tool_name)
                    span_obj.set_attribute("mcp.transport", mcp_transport)
                    span_obj.set_attribute("mcp.arguments_hash", args_hash)
            elif tier >= 2:
                # Standard Tier-2 tools require explicit synchronous approval (fail-closed)
                tier2_args_preview = json.dumps(
                    decision.get("arguments") or {}, ensure_ascii=False, sort_keys=True,
                )[:200]
                approved = bool(
                    self.approval.request_sync(
                        tool=tool_name,
                        arguments_hash=args_hash,
                        context=(
                            f"Args: {tier2_args_preview} | Goal: {str(task['goal'])[:200]}"
                        ),
                    ),
                )
                if not approved:
                    ev_hash = self.graph.append_evidence(
                        task_id=task_id,
                        tool_name=tool_name,
                        args_hash=args_hash,
                        exit_code=126,
                        backup_path=None,
                        why_hash=why_hash,
                    )
                    if span_obj is not None:
                        span_obj.status = "error"
                        span_obj.error = "approval_denied"
                    self.graph.mark(
                        task_id,
                        "blocked",
                        error="approval_denied",
                        evidence_hash=ev_hash,
                    )
                    self._emit_state(task_id, "blocked")
                    return TaskResult(
                        task_id=task_id,
                        status="blocked",
                        tool_name=tool_name,
                        exit_code=126,
                        error="approval_denied",
                    )

            # Agent SRE pre-execution SLO / error-budget / OWASP ASI07+ASI08 kill-switch guard
            sre_check = self.sre.check_allowed(
                task_id,
                estimated_cost_usd=float(decision.get("estimated_cost_usd") or 0.0),
            )
            if not sre_check.allowed:
                sre_err = f"agent_sre_blocked: {sre_check.reason}"
                ev_hash = self.graph.append_evidence(
                    task_id=task_id,
                    tool_name=tool_name,
                    args_hash=args_hash,
                    exit_code=1,
                    backup_path=None,
                    why_hash=why_hash,
                )
                if span_obj is not None:
                    span_obj.status = "error"
                    span_obj.error = sre_err
                self.graph.mark(task_id, "blocked", error=sre_err, evidence_hash=ev_hash)
                self._emit_state(task_id, "blocked")
                return TaskResult(
                    task_id=task_id,
                    status="blocked",
                    tool_name=tool_name,
                    exit_code=1,
                    error=sre_err,
                )

            # AgentFuse logical circuit breaker supervisor (loops, goal drift, logic traps, spend)
            fuse_dec = self.fuse.monitor(
                {
                    "goal": str(task["goal"]),
                    "tool_name": tool_name,
                    "arguments_hash": args_hash,
                    "cost_usd": float(decision.get("estimated_cost_usd") or 0.0),
                    "action_summary": f"{tool_name} {task['goal']}",
                },
            )
            if fuse_dec.tripped:
                err_tag = f"agentfuse_tripped:{fuse_dec.recovery_rung.value} ({fuse_dec.instruction})"
                alt = None
                if fuse_dec.recovery_rung == RecoveryRung.FALLBACK_CAPABILITY:
                    alt = self.route_table.find_alternative(tool_name)
                    if alt:
                        err_tag = f"{err_tag} suggested_fallback={alt}"
                # P0-6: AgentFuse retry cap — don't spin forever on tripped fuse
                attempts_so_far = int(task.get("attempts", 0)) + 1
                max_att = int(task.get("max_attempts", 3))
                if fuse_dec.recovery_rung == RecoveryRung.PAUSE_FOR_HUMAN:
                    target_status = "blocked"
                elif attempts_so_far >= max_att:
                    target_status = "blocked"
                    err_tag = f"{err_tag} retries_exhausted({attempts_so_far}/{max_att})"
                else:
                    target_status = "pending"
                hint = (
                    f"Trying fallback '{alt}' next."
                    if alt
                    else "Can you step in or rephrase what you want me to click or type?"
                )
                self._speak_struggle_explanation(str(task["goal"]), str(fuse_dec.instruction), hint)
                ev_hash = self.graph.append_evidence(
                    task_id=task_id,
                    tool_name=tool_name,
                    args_hash=args_hash,
                    exit_code=1,
                    backup_path=None,
                    why_hash=why_hash,
                )
                if span_obj is not None:
                    span_obj.status = "error"
                    span_obj.error = err_tag
                self.graph.mark(task_id, target_status, error=err_tag, evidence_hash=ev_hash)
                self._emit_state(task_id, target_status)
                return TaskResult(
                    task_id=task_id,
                    status=target_status,
                    tool_name=tool_name,
                    exit_code=1,
                    error=err_tag,
                )

            t_exec0 = time.monotonic()
            if is_mcp_pa:
                # P1: fail-closed — MCP tools require mcp_loader; never fall through
                # to the native executor for an MCP perception/actuation tool.
                if self.mcp_loader is None:
                    mcp_err = "mcp_loader_unavailable"
                    ev_hash = self.graph.append_evidence(
                        task_id=task_id,
                        tool_name=tool_name,
                        args_hash=args_hash,
                        exit_code=126,
                        backup_path=None,
                        why_hash=why_hash,
                    )
                    if span_obj is not None:
                        span_obj.status = "error"
                        span_obj.error = mcp_err
                    self.graph.mark(
                        task_id,
                        "blocked",
                        error=mcp_err,
                        evidence_hash=ev_hash,
                    )
                    self._emit_state(task_id, "blocked")
                    return TaskResult(
                        task_id=task_id,
                        status="blocked",
                        tool_name=tool_name,
                        exit_code=126,
                        error=mcp_err,
                    )
                mcp_receipt = self.guard_loop.call_tool(
                    tool_name,
                    self.mcp_loader.execute_domain_tool,
                    tool_name,
                    decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {},
                    cost_usd=float(decision.get("estimated_cost_usd") or 0.0),
                )
                result = ExecResult(
                    success=mcp_receipt.ok,
                    exit_code=0 if mcp_receipt.ok else 1,
                    summary=mcp_receipt.output,
                    error=mcp_receipt.error or None,
                )
                args_hash = args_hash or mcp_receipt.arguments_hash
                mcp_transport = mcp_receipt.mcp_transport or mcp_transport
            else:
                result = self.guard_loop.call_tool(
                    tool_name,
                    self.executor.execute,
                    decision,
                    task_id=task_id,
                    cost_usd=float(decision.get("estimated_cost_usd") or 0.0),
                )

            exec_elapsed = time.monotonic() - t_exec0
            self.sre.record_outcome(
                task_id,
                success=result.success,
                latency_sec=exec_elapsed,
                cost_usd=float(decision.get("estimated_cost_usd") or 0.0),
                tier=tier,
            )

            if is_mcp_pa and tracer is not None and trace_id:
                try:
                    why_txt = (
                        f"MCP Action Receipt | server={mcp_server} | tool={tool_name} "
                        f"| arguments_hash={args_hash} | mcp_transport={mcp_transport}"
                    )
                    tracer.store.save_why_receipt(
                        trace_id=trace_id,
                        prompt_preview=str(task["goal"])[:80],
                        gear="autonomy",
                        source=f"mcp:{mcp_server}",
                        receipt_text=why_txt,
                        receipt_hash=why_hash or args_hash,
                    )
                except Exception as exc:
                    log.debug("save_why_receipt failed for trace %s: %s", trace_id, exc)
            ev_hash = self.graph.append_evidence(
                task_id=task_id,
                tool_name=tool_name,
                args_hash=args_hash,
                exit_code=result.exit_code,
                backup_path=result.backup_path,
                why_hash=why_hash,
            )
            if span_obj is not None:
                span_obj.set_attribute("tool_name", tool_name)
                span_obj.set_attribute("exit_code", result.exit_code)
                if not result.success:
                    span_obj.status = "error"
                    span_obj.error = result.error or "execution_failed"

            final_tool = tool_name
            final_exit = int(result.exit_code)
            final_summary = str(result.summary or "")
            final_error = str(result.error or "")
            if result.success:
                # P0-4: Register Saga compensating rollback step for mutation tools
                if result.backup_path or result.action_id:
                    def _make_compensate(bp: str | None, aid: str | None) -> Callable[[], Any]:
                        def _compensate() -> str:
                            if bp:
                                from .checkpoint import PreExecutionCheckpoint
                                undo_dir = Path(str(bp)).parent
                                chk = PreExecutionCheckpoint(undo_dir)
                                ok, msg = chk.restore(aid or "")
                                if not ok:
                                    raise RuntimeError(f"Saga compensation failed: {msg}")
                                return msg
                            return "no_backup_to_restore"
                        return _compensate

                    self.saga.register_completed_step(
                        step_id=f"{task_id}:{tool_name}",
                        tool_name=tool_name,
                        compensate=_make_compensate(result.backup_path, result.action_id),
                        description=f"Rollback {tool_name} via checkpoint {result.action_id or result.backup_path}",
                    )
                self.graph.mark(task_id, "done", result=result.summary, evidence_hash=ev_hash)
                self._emit_state(task_id, "done")
                final_status = "done"
            else:
                self._handle_failure(task, result=result, evidence_hash=ev_hash, failed_tool=tool_name)
                row_after = self.graph.get_task(task_id)
                final_status = str(row_after["status"]) if row_after else "failed"
        except Exception as exc:
            log.exception("Autonomous task %s crashed", task_id)
            if span_obj is not None:
                span_obj.status = "error"
                span_obj.error = str(exc)
            self._handle_failure(task, error=str(exc))
            row_after = self.graph.get_task(task_id)
            final_status = str(row_after["status"]) if row_after else "failed"
            final_exit = 1
            final_error = str(exc)
        finally:
            if span_ctx is not None:
                # P1: pass exception info through so the span status reflects errors
                span_ctx.__exit__(*sys.exc_info()) if span_obj is not None and span_obj.status != "ok" else span_ctx.__exit__(None, None, None)
            if tracer is not None and trace_id:
                tracer.store.finish_trace(
                    trace_id,
                    status="ok" if (span_obj is None or span_obj.status == "ok") else "error",
                )
        return TaskResult(
            task_id=task_id,
            status=final_status,
            tool_name=final_tool,
            exit_code=final_exit,
            summary=final_summary,
            error=final_error,
        )

    def _handle_failure(
        self,
        task: dict[str, Any],
        *,
        result: ExecResult | None = None,
        error: str | None = None,
        evidence_hash: str | None = None,
        failed_tool: str = "",
    ) -> None:
        task_id = str(task["id"])
        attempts = int(task.get("attempts", 0)) + 1
        max_attempts = int(task.get("max_attempts", 3))
        err_text = error or (result.error if result else "unknown_error")
        # Record failed approach in unified memory graph so future graph_walk() avoids repeating it
        if self.memory is not None and hasattr(self.memory, "associate") and failed_tool:
            try:
                g_ent = _extract_goal_entity(str(task.get("goal") or ""), fallback=task_id)
                self.memory.associate(g_ent, failed_tool, "failed_attempt")
            except Exception as exc:
                log.debug("memory.associate failed for task %s: %s", task_id, exc)
        # Capability #17: Suggest alternative capability from CapabilityRouteTable
        alt_tool = self.route_table.find_alternative(failed_tool) if failed_tool else None
        if alt_tool and f"suggested_fallback={alt_tool}" not in err_text:
            err_text = f"{err_text} (suggested_fallback={alt_tool})"
        if attempts < max_attempts:
            self.graph.mark(task_id, "pending", error=err_text, evidence_hash=evidence_hash)
            self._emit_state(task_id, "pending")
            log.warning("Task %s failed, retrying (%d/%d): %s", task_id, attempts, max_attempts, err_text)
        else:
            # P0-4: Trigger Saga LIFO compensation before marking terminal failure
            try:
                comp = self.saga.compensate_all(reason="task_exhausted_retries")
                if not comp["all_ok"]:
                    log.warning("Saga compensation partially failed for task %s: %s", task_id, comp)
            except Exception as exc:
                log.debug("Saga compensation raised for task %s: %s", task_id, exc)
            self.graph.mark(task_id, "failed", error=err_text, evidence_hash=evidence_hash)
            self._emit_state(task_id, "failed")
            log.error("Task %s exhausted retries (%d/%d): %s", task_id, attempts, max_attempts, err_text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Celina v5 Autonomy Engine Worker")
    parser.add_argument("--once", action="store_true", help="Process pending queue until idle and exit")
    parser.add_argument("--status", action="store_true", help="Print TaskGraph status JSON and exit")
    parser.add_argument(
        "--status-notify",
        action="store_true",
        help="Show a GNOME desktop notification (and stdout summary) of TaskGraph status (Super+Shift+A)",
    )
    parser.add_argument(
        "--execute-tier2",
        action="store_true",
        help="Disable default observe-only mode and allow approved Tier-2 execution",
    )
    args = parser.parse_args()

    config.set_process_name("celina-autonomy")

    observe_only = config.is_autonomy_observe_only() and (not args.execute_tier2)

    db_path = config.BASE_DIR / "tasks.db"
    graph = TaskGraph(db_path)
    if args.status or args.status_notify:
        snap = graph.export_graph()
        snap["observe_only"] = observe_only
        if args.status_notify:
            import shutil
            import subprocess

            counts = snap.get("counts", {})
            ev_ok = "OK" if snap.get("evidence_ok") else "TAMPERED"
            mode = "observe-only" if observe_only else "active"
            summary = (
                f"Mode: {mode} | Running: {counts.get('running', 0)} | "
                f"Pending: {counts.get('pending', 0)} | Done: {counts.get('done', 0)} | "
                f"Observed: {counts.get('observed', 0)} | Evidence: {ev_ok}"
            )
            print(summary)
            if shutil.which("notify-send"):
                try:
                    subprocess.run(
                        ["notify-send", "-a", "Celina Autonomy", "Celina Autonomy Status", summary],
                        check=False,
                        timeout=5,
                    )
                except Exception as exc:
                    log.debug("notify-send failed: %s", exc)
        else:
            print(json.dumps(snap, indent=2))
        graph.close()
        return 0

    from .. import router as celina_router
    from .. import tools as celina_tools

    legato_enabled = os.environ.get("CELINA_LEGATO_ENABLED", "0").strip().lower() in {"1", "true", "yes"}

    handlers = {**celina_tools._AUTO_TOOL_MAP, **celina_tools._APPROVAL_TOOL_MAP}
    executor = SelfHealingExecutor(data_dir=config.BASE_DIR, tool_handlers=handlers, legato_enabled=legato_enabled)
    rt = celina_router.CelinaRouter()
    gate = approval.ApprovalEngine()

    # Wire mcp_action_cb to the D-Bus backend so engine._emit_mcp_action()
    # emits MCPActionStarted over D-Bus when running in the same process
    # as celina.service (dbus_service.run_dbus_daemon). The backend's
    # _mcp_action_cb is wired to a D-Bus emitter in run_dbus_daemon(); here
    # we expose backend.emit_mcp_action_started as the engine's callback.
    # If they run as separate systemd services (the default), this is a
    # silent no-op because backend._mcp_action_cb is None outside the bus loop.
    mcp_action_cb = None
    try:
        from ..dbus_service import CelinaDBusBackend as _DbusBackend
        _dbus_backend = _DbusBackend()
        mcp_action_cb = _dbus_backend.emit_mcp_action_started
    except Exception as exc:  # log but continue — D-Bus may be unavailable headless/CI
        log.debug("mcp_action_cb D-Bus wiring unavailable: %s", exc)

    # P0-5: Wire production dependencies — memory, MCP loader, tracer, state_cb.
    # Degrade cleanly headless/CI when D-Bus or optional modules are unavailable.
    state_cb = None
    mcp_action_cb = None
    try:
        from ..dbus_service import CelinaDBusBackend as _DbusBackend
        _dbus_backend = _DbusBackend()
        mcp_action_cb = _dbus_backend.emit_mcp_action_started
    except Exception as exc:  # D-Bus may be unavailable headless/CI
        log.debug("mcp_action_cb D-Bus wiring unavailable: %s", exc)

    try:
        from ..memory import CelinaMemory
        memory = CelinaMemory(db_path=config.BASE_DIR / "memory.db")
    except Exception as exc:  # degrade cleanly if memory module unavailable
        log.debug("CelinaMemory wiring unavailable: %s", exc)
        memory = None

    try:
        mcp_loader = MCPLoader()
    except Exception as exc:
        log.debug("MCPLoader wiring unavailable: %s", exc)
        mcp_loader = None

    try:
        from ..telemetry import get_tracer
        tracer = get_tracer()
    except Exception as exc:
        log.debug("Telemetry tracer wiring unavailable: %s", exc)
        tracer = None

    engine = AutonomyEngine(
        graph, executor, rt, gate,
        observe_only=observe_only,
        state_cb=state_cb,
        mcp_action_cb=mcp_action_cb,
        mcp_loader=mcp_loader,
        tracer=tracer,
        memory=memory,
    )

    signal.signal(signal.SIGTERM, lambda *_a: engine.request_stop())
    signal.signal(signal.SIGINT, lambda *_a: engine.request_stop())

    try:
        if args.once:
            processed = engine.run_until_idle()
            print(json.dumps({"processed": processed, "observe_only": observe_only}))
        else:
            engine.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        graph.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
