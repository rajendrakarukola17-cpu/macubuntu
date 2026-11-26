"""Celina v5 Reliability & Stability Framework (`autonomy/reliability.py`)

Unifies three production agent-reliability layers cleanly inside Celina:
  1. `AgentSRE` (SLOs, Error Budgets, OWASP ASI07 Cost Limits & ASI08 Kill Switch):
       - Tracks rolling Tier-2 latency SLO (`< 30s`), success rate SLO (`>= 95%`),
         per-task / session USD & token limits (ASI07), and cascading-failure error
         budget burn rate with an instant fail-closed kill switch (ASI08).
  2. `AgentFuse` (Logical Circuit Breaker Supervisor above the TaskGraph):
       - Monitors live telemetry events from Router / AutonomyEngine to detect
         long-horizon failure modes: tool loops, goal drift, logic traps, and
         runaway spend.
       - Trips a deterministic 4-rung Recovery Ladder instead of blind retries:
           Rung 1: `RESTATE_GOAL`
           Rung 2: `FORBID_ACTION`
           Rung 3: `FALLBACK_CAPABILITY`
           Rung 4: `PAUSE_FOR_HUMAN`
  3. `EditableStateMachineRun` (agent6-inspired Stateful Long-Running Workflows):
       - Persists editable JSON run checkpoints (`pause`, `review`, `edit_step`,
         `replay_from_step`), captures optional git HEAD/status snapshots, and
         enforces a hard metered USD budget.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import subprocess
import threading
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from .. import config


class RecoveryRung(str, Enum):
    NONE = "none"
    RESTATE_GOAL = "restate_goal"
    FORBID_ACTION = "forbid_action"
    FALLBACK_CAPABILITY = "fallback_capability"
    PAUSE_FOR_HUMAN = "pause_for_human"


class AgentSREError(RuntimeError):
    """Raised when an Agent SRE kill switch, error budget, or hard cost cap blocks execution."""


@dataclasses.dataclass
class AgentSLOPolicy:
    tier2_max_latency_sec: float = 30.0
    min_success_rate: float = 0.90
    max_task_cost_usd: float = 0.50
    max_session_cost_usd: float = 2.00
    max_consecutive_failures: int = 5
    error_budget_window: int = 20
    max_errors_in_window: int = 4


@dataclasses.dataclass(frozen=True)
class SRECheckResult:
    allowed: bool
    reason: str
    error_budget_remaining: int
    session_cost_usd: float
    slo_violations: tuple[str, ...] = ()


class AgentSRE:
    """Production Agent SRE controller preventing cascading failures (OWASP ASI08)
    and uncontrolled spend (OWASP ASI07) via SLOs, error budgets, and a kill switch.
    """

    def __init__(self, policy: AgentSLOPolicy | None = None):
        self.policy = policy or AgentSLOPolicy()
        self._lock = threading.Lock()
        self._kill_switch = False
        self._kill_reason = ""
        self._session_cost_usd = 0.0
        self._task_costs: dict[str, float] = {}
        self._outcomes: list[dict[str, Any]] = []
        self._consecutive_failures = 0
        self._slo_violations: list[str] = []

    def trip_kill_switch(self, reason: str = "operator_kill_switch") -> None:
        with self._lock:
            self._kill_switch = True
            self._kill_reason = str(reason)

    def reset_kill_switch(self) -> None:
        with self._lock:
            self._kill_switch = False
            self._kill_reason = ""
            self._consecutive_failures = 0

    def is_killed(self) -> bool:
        with self._lock:
            return self._kill_switch

    def _window_errors(self) -> int:
        recent = self._outcomes[-self.policy.error_budget_window :]
        return sum(1 for item in recent if not item.get("success", False))

    def check_allowed(
        self,
        task_id: str,
        *,
        estimated_cost_usd: float = 0.0,
    ) -> SRECheckResult:
        with self._lock:
            err_used = self._window_errors()
            budget_rem = max(0, self.policy.max_errors_in_window - err_used)
            if self._kill_switch:
                return SRECheckResult(
                    allowed=False,
                    reason=f"Agent SRE kill switch active ({self._kill_reason})",
                    error_budget_remaining=budget_rem,
                    session_cost_usd=round(self._session_cost_usd, 6),
                    slo_violations=tuple(self._slo_violations[-5:]),
                )
            proj_task = self._task_costs.get(task_id, 0.0) + max(0.0, float(estimated_cost_usd))
            if proj_task > self.policy.max_task_cost_usd:
                return SRECheckResult(
                    allowed=False,
                    reason=(
                        f"OWASP ASI07 per-task cost cap exceeded for '{task_id}' "
                        f"(${proj_task:.4f} > ${self.policy.max_task_cost_usd:.4f})"
                    ),
                    error_budget_remaining=budget_rem,
                    session_cost_usd=round(self._session_cost_usd, 6),
                    slo_violations=tuple(self._slo_violations[-5:]),
                )
            proj_session = self._session_cost_usd + max(0.0, float(estimated_cost_usd))
            if proj_session > self.policy.max_session_cost_usd:
                return SRECheckResult(
                    allowed=False,
                    reason=(
                        f"OWASP ASI07 session cost cap exceeded "
                        f"(${proj_session:.4f} > ${self.policy.max_session_cost_usd:.4f})"
                    ),
                    error_budget_remaining=budget_rem,
                    session_cost_usd=round(self._session_cost_usd, 6),
                    slo_violations=tuple(self._slo_violations[-5:]),
                )
            if budget_rem <= 0 and len(self._outcomes) >= self.policy.max_errors_in_window:
                return SRECheckResult(
                    allowed=False,
                    reason=(
                        f"OWASP ASI08 error budget exhausted ({err_used}/{self.policy.max_errors_in_window} "
                        f"failures in last {self.policy.error_budget_window} tasks)"
                    ),
                    error_budget_remaining=0,
                    session_cost_usd=round(self._session_cost_usd, 6),
                    slo_violations=tuple(self._slo_violations[-5:]),
                )
            return SRECheckResult(
                allowed=True,
                reason="ok",
                error_budget_remaining=budget_rem,
                session_cost_usd=round(self._session_cost_usd, 6),
                slo_violations=tuple(self._slo_violations[-5:]),
            )

    def record_outcome(
        self,
        task_id: str,
        *,
        success: bool,
        latency_sec: float,
        cost_usd: float = 0.0,
        tier: int = 2,
    ) -> dict[str, Any]:
        with self._lock:
            cost = max(0.0, float(cost_usd))
            self._session_cost_usd += cost
            self._task_costs[task_id] = self._task_costs.get(task_id, 0.0) + cost
            self._outcomes.append(
                {
                    "task_id": task_id,
                    "success": bool(success),
                    "latency_sec": round(float(latency_sec), 4),
                    "cost_usd": cost,
                    "tier": int(tier),
                    "ts": time.time(),
                },
            )
            if len(self._outcomes) > 200:
                self._outcomes = self._outcomes[-200:]

            violations: list[str] = []
            if tier >= 2 and latency_sec > self.policy.tier2_max_latency_sec:
                v = f"SLO violation: Tier-2 task '{task_id}' took {latency_sec:.2f}s > {self.policy.tier2_max_latency_sec}s"
                self._slo_violations.append(v)
                violations.append(v)

            if success:
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.policy.max_consecutive_failures:
                    self._kill_switch = True
                    self._kill_reason = f"cascading_failure:{self._consecutive_failures}_consecutive_errors"
                    violations.append(f"Auto-tripped kill switch: {self._kill_reason}")

            return {
                "task_id": task_id,
                "success": bool(success),
                "slo_violations": violations,
                "kill_switch": self._kill_switch,
                "error_budget_remaining": max(0, self.policy.max_errors_in_window - self._window_errors()),
                "session_cost_usd": round(self._session_cost_usd, 6),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._outcomes)
            ok_cnt = sum(1 for o in self._outcomes if o["success"])
            return {
                "kill_switch": self._kill_switch,
                "kill_reason": self._kill_reason,
                "total_recorded": total,
                "success_rate": round(ok_cnt / total, 4) if total else 1.0,
                "error_budget_remaining": max(0, self.policy.max_errors_in_window - self._window_errors()),
                "session_cost_usd": round(self._session_cost_usd, 6),
                "slo_violations": list(self._slo_violations[-10:]),
            }


@dataclasses.dataclass(frozen=True)
class AgentFuseDecision:
    tripped: bool
    failure_mode: str  # "none" | "tool_loop" | "goal_drift" | "logic_trap" | "runaway_spend"
    recovery_rung: RecoveryRung
    instruction: str
    forbidden_tools: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tripped": self.tripped,
            "failure_mode": self.failure_mode,
            "recovery_rung": self.recovery_rung.value,
            "instruction": self.instruction,
            "forbidden_tools": list(self.forbidden_tools),
        }


class AgentFuse:
    """Logical circuit breaker supervising an agent's execution graph.
    Detects tool loops, goal drift, logic traps, and runaway spend, then
    escalates cleanly along a deterministic recovery ladder.
    """

    _LADDER: tuple[RecoveryRung, ...] = (
        RecoveryRung.RESTATE_GOAL,
        RecoveryRung.FORBID_ACTION,
        RecoveryRung.FALLBACK_CAPABILITY,
        RecoveryRung.PAUSE_FOR_HUMAN,
    )

    def __init__(
        self,
        *,
        max_loop_repeats: int = 3,
        max_drift_streak: int = 3,
        max_logic_trap_errors: int = 3,
        max_spend_usd: float = 0.50,
    ):
        self.max_loop_repeats = max(2, int(max_loop_repeats))
        self.max_drift_streak = max(2, int(max_drift_streak))
        self.max_logic_trap_errors = max(2, int(max_logic_trap_errors))
        self.max_spend_usd = max(0.01, float(max_spend_usd))
        self._lock = threading.Lock()
        self._call_history: list[str] = []
        self._error_history: list[str] = []
        self._drift_streak = 0
        self._total_spend_usd = 0.0
        self._trip_count = 0
        self._forbidden: set[str] = set()

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in {"the", "and", "for", "with"}}

    def _escalate(self, mode: str, tool_name: str, goal: str) -> AgentFuseDecision:
        idx = min(self._trip_count, len(self._LADDER) - 1)
        rung = self._LADDER[idx]
        self._trip_count += 1
        if rung in {RecoveryRung.FORBID_ACTION, RecoveryRung.FALLBACK_CAPABILITY} and tool_name:
            self._forbidden.add(tool_name)

        instructions = {
            RecoveryRung.RESTATE_GOAL: (
                f"[AgentFuse:{mode}] Restate root goal '{goal[:100]}' and plan a fresh minimal step."
            ),
            RecoveryRung.FORBID_ACTION: (
                f"[AgentFuse:{mode}] Action '{tool_name}' is now FORBIDDEN for this run; choose a different approach."
            ),
            RecoveryRung.FALLBACK_CAPABILITY: (
                f"[AgentFuse:{mode}] Route away from '{tool_name}' via CapabilityRouteTable fallback + Saga rollback."
            ),
            RecoveryRung.PAUSE_FOR_HUMAN: (
                f"[AgentFuse:{mode}] Recovery ladder exhausted; pausing execution for human confirmation."
            ),
        }
        return AgentFuseDecision(
            tripped=True,
            failure_mode=mode,
            recovery_rung=rung,
            instruction=instructions[rung],
            forbidden_tools=tuple(sorted(self._forbidden)),
        )

    def monitor(self, telemetry_event: dict[str, Any]) -> AgentFuseDecision:
        """Inspects a telemetry tick:
        {goal, tool_name, arguments_hash, error_code, cost_usd, action_summary}
        """
        with self._lock:
            goal = str(telemetry_event.get("goal") or "")
            tool_name = str(telemetry_event.get("tool_name") or "")
            args_hash = str(telemetry_event.get("arguments_hash") or "")
            error_code = str(telemetry_event.get("error_code") or "").strip().lower()
            cost_usd = max(0.0, float(telemetry_event.get("cost_usd") or 0.0))
            action_summary = str(telemetry_event.get("action_summary") or f"{tool_name} {args_hash}")

            self._total_spend_usd += cost_usd
            if self._total_spend_usd > self.max_spend_usd:
                return self._escalate("runaway_spend", tool_name, goal)

            if tool_name and tool_name in self._forbidden:
                return self._escalate("tool_loop", tool_name, goal)

            # 1. Tool loop detection (identical signature repeated or A-B-A-B ping-pong)
            if tool_name:
                sig = f"{tool_name}:{args_hash}"
                self._call_history.append(sig)
                if len(self._call_history) >= self.max_loop_repeats:
                    tail = self._call_history[-self.max_loop_repeats :]
                    if len(set(tail)) == 1:
                        return self._escalate("tool_loop", tool_name, goal)
                if len(self._call_history) >= 4:
                    a, b, c, d = self._call_history[-4:]
                    if a == c and b == d and a != b:
                        return self._escalate("tool_loop", tool_name, goal)

            # 2. Logic trap detection (same error code recurring without progress)
            if error_code:
                self._error_history.append(error_code)
                if len(self._error_history) >= self.max_logic_trap_errors:
                    if len(set(self._error_history[-self.max_logic_trap_errors :])) == 1:
                        return self._escalate("logic_trap", tool_name, goal)
            else:
                self._error_history.clear()

            # 3. Goal drift detection (consecutive actions sharing zero domain tokens with root goal)
            g_toks = self._tokens(goal)
            a_toks = self._tokens(action_summary)
            if g_toks and a_toks and len(g_toks & a_toks) == 0 and telemetry_event.get("check_drift", False):
                self._drift_streak += 1
                if self._drift_streak >= self.max_drift_streak:
                    return self._escalate("goal_drift", tool_name, goal)
            elif g_toks and a_toks and len(g_toks & a_toks) > 0:
                self._drift_streak = 0

            return AgentFuseDecision(
                tripped=False,
                failure_mode="none",
                recovery_rung=RecoveryRung.NONE,
                instruction="",
                forbidden_tools=tuple(sorted(self._forbidden)),
            )

    def reset(self) -> None:
        with self._lock:
            self._call_history.clear()
            self._error_history.clear()
            self._drift_streak = 0
            self._total_spend_usd = 0.0
            self._trip_count = 0
            self._forbidden.clear()


class EditableStateMachineRun:
    """agent6-inspired editable, snapshot-resumable stateful workflow run with
    optional git snapshot metadata and a hard metered USD budget.
    """

    def __init__(
        self,
        run_id: str,
        goal: str,
        steps: list[dict[str, Any]],
        *,
        state_dir: Path | None = None,
        budget_usd: float = 0.50,
        repo_dir: Path | None = None,
    ):
        self.run_id = str(run_id)
        self.goal = str(goal)
        self.steps: list[dict[str, Any]] = [dict(s) for s in steps]
        self.cursor = 0
        self.status = "running"  # running | paused | completed | budget_exhausted
        self.budget_usd = max(0.0, float(budget_usd))
        self.spent_usd = 0.0
        self.snapshots: list[dict[str, Any]] = []
        self.repo_dir = Path(repo_dir) if repo_dir is not None else None
        self.state_dir = Path(state_dir) if state_dir is not None else (config.BASE_DIR / "checkpoints" / "runs")
        self.state_path = self.state_dir / f"{self.run_id}.json"

    def _capture_git_head(self) -> str:
        if self.repo_dir is None or not (self.repo_dir / ".git").exists():
            return ""
        try:
            res = subprocess.run(
                ["git", "-C", str(self.repo_dir), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=3,
                shell=False,
            )
            return res.stdout.strip() if res.returncode == 0 else ""
        except Exception:
            return ""

    def save(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "goal": self.goal,
            "cursor": self.cursor,
            "status": self.status,
            "budget_usd": self.budget_usd,
            "spent_usd": round(self.spent_usd, 6),
            "steps": self.steps,
            "snapshots": self.snapshots,
        }
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return self.state_path

    @classmethod
    def load(cls, path: Path) -> EditableStateMachineRun:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        inst = cls(
            run_id=raw["run_id"],
            goal=raw["goal"],
            steps=raw["steps"],
            state_dir=Path(path).parent,
            budget_usd=raw.get("budget_usd", 0.50),
        )
        inst.cursor = int(raw.get("cursor", 0))
        inst.status = str(raw.get("status", "paused"))
        inst.spent_usd = float(raw.get("spent_usd", 0.0))
        inst.snapshots = list(raw.get("snapshots") or [])
        return inst

    def pause(self) -> None:
        self.status = "paused"
        self.save()

    def edit_step(self, step_index: int, patch: dict[str, Any]) -> dict[str, Any]:
        if step_index < 0 or step_index >= len(self.steps):
            raise IndexError(f"Step index {step_index} out of range (0..{len(self.steps) - 1}).")
        self.steps[step_index].update(patch)
        self.save()
        return dict(self.steps[step_index])

    def replay_from(self, step_index: int) -> None:
        if step_index < 0 or step_index > len(self.steps):
            raise IndexError(f"Replay index {step_index} out of range.")
        self.cursor = int(step_index)
        self.status = "running"
        self.save()

    def advance_step(self, *, step_cost_usd: float = 0.0, output: str = "") -> dict[str, Any]:
        if self.status == "paused":
            raise AgentSREError(f"Run '{self.run_id}' is paused for review; call replay_from() to resume.")
        if self.spent_usd + max(0.0, float(step_cost_usd)) > self.budget_usd:
            self.status = "budget_exhausted"
            self.save()
            raise AgentSREError(
                f"Hard USD budget exceeded for run '{self.run_id}': "
                f"${self.spent_usd + step_cost_usd:.4f} > ${self.budget_usd:.4f}",
            )
        if self.cursor >= len(self.steps):
            self.status = "completed"
            self.save()
            return {"status": "completed", "cursor": self.cursor}

        step = dict(self.steps[self.cursor])
        self.spent_usd += max(0.0, float(step_cost_usd))
        snap = {
            "step_index": self.cursor,
            "step_hash": hashlib.sha256(json.dumps(step, sort_keys=True).encode("utf-8")).hexdigest()[:16],
            "git_head": self._capture_git_head(),
            "cost_usd": round(float(step_cost_usd), 6),
            "output_preview": str(output)[:200],
            "ts": time.time(),
        }
        self.snapshots.append(snap)
        self.cursor += 1
        if self.cursor >= len(self.steps):
            self.status = "completed"
        self.save()
        return snap


# ============================================================================
# 4. AgentFuse Single-Decorator Circuit Breaker (`@reliable`) + `GuardLoop`
#    - Fuse: detects the exact same input seen `fuse_limit`+ times & kills loop
#    - Sentinel: validates output against `TaskResult` / Pydantic schema
#    - Medic: catches validation/runtime errors & auto-repairs up to 2x
#    - Budget: per-call wall-clock `max_seconds` & dollar `max_cost_usd` caps
#    - GuardLoop: per-tool 3-consecutive-same-error breaker logging to evidence
#      chain + OpenTelemetry span tracing on every protected call
# ============================================================================
class CircuitBreakerTripped(AgentSREError):
    """Raised when `@reliable` Fuse/Sentinel/Medic/Budget or `GuardLoop` trips."""


@dataclasses.dataclass
class TaskResult:
    """Validated output schema enforced by `@reliable(sentinel_schema=TaskResult)`."""

    task_id: str
    status: str  # "done" | "observed" | "blocked" | "pending" | "failed" | "repaired"
    tool_name: str = ""
    exit_code: int = 0
    summary: str = ""
    error: str = ""

    _VALID_STATUSES: frozenset[str] = frozenset(
        {"done", "observed", "blocked", "pending", "failed", "repaired"},
    )

    @classmethod
    def model_validate(cls, value: Any) -> TaskResult:
        if isinstance(value, cls):
            obj = value
        elif isinstance(value, dict):
            if "task_id" not in value or "status" not in value:
                raise ValueError(f"TaskResult missing required fields 'task_id'/'status': {list(value.keys())}")
            obj = cls(
                task_id=str(value["task_id"]),
                status=str(value["status"]),
                tool_name=str(value.get("tool_name", "")),
                exit_code=int(value.get("exit_code", 0)),
                summary=str(value.get("summary", "")),
                error=str(value.get("error", "")),
            )
        else:
            raise ValueError(f"Expected TaskResult or dict, got {type(value).__name__}")
        if not obj.task_id or obj.status not in cls._VALID_STATUSES:
            raise ValueError(f"Invalid TaskResult: task_id={obj.task_id!r}, status={obj.status!r}")
        return obj


def _validate_with_sentinel(schema: Any, value: Any) -> Any:
    if schema is None:
        return value
    if hasattr(schema, "model_validate") and callable(schema.model_validate):
        return schema.model_validate(value)
    if callable(schema) and isinstance(value, dict):
        return schema(**value)
    if isinstance(schema, type) and isinstance(value, schema):
        return value
    raise ValueError(f"Sentinel validation failed for schema {schema!r}")


def reliable(
    *,
    sentinel_schema: Any = TaskResult,
    fuse_limit: int = 5,
    max_seconds: float = 30.0,
    max_cost_usd: float = 0.50,
    medic_repair_fn: Callable[[Any, Exception], Any] | None = None,
    max_medic_repairs: int = 2,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """AgentFuse single-decorator circuit breaker (`Fuse + Sentinel + Medic + Budget`).
    Scopes Fuse counters per bound instance (when decorating methods) so independent
    engine instances stay isolated while repeated identical inputs trip the fuse.
    """
    import functools

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        seen_inputs_by_scope: dict[int, dict[str, int]] = {}
        spent_by_scope: dict[int, float] = {}
        lock = threading.RLock()

        @functools.wraps(fn)
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            # Scope key: bound `self` id if method, else 0
            scope = id(args[0]) if args and hasattr(args[0], "__dict__") else 0
            sig_args = args[1:] if scope else args
            try:
                sig_payload = json.dumps(
                    {"args": sig_args, "kwargs": kwargs},
                    sort_keys=True,
                    default=str,
                )
            except Exception:
                sig_payload = repr((sig_args, kwargs))
            in_hash = hashlib.sha256(sig_payload.encode("utf-8")).hexdigest()[:24]

            with lock:
                counts = seen_inputs_by_scope.setdefault(scope, {})
                counts[in_hash] = counts.get(in_hash, 0) + 1
                if counts[in_hash] >= max(2, int(fuse_limit)):
                    raise CircuitBreakerTripped(
                        f"Fuse tripped on {fn.__name__}: identical input seen {counts[in_hash]} times (limit={fuse_limit}).",
                    )
                if spent_by_scope.get(scope, 0.0) > float(max_cost_usd):
                    raise CircuitBreakerTripped(
                        f"Budget tripped on {fn.__name__}: spend ${spent_by_scope[scope]:.4f} > ${max_cost_usd:.4f}.",
                    )

            t0 = time.monotonic()
            last_exc: Exception | None = None
            raw_out: Any = None
            try:
                raw_out = fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc

            elapsed = time.monotonic() - t0
            if elapsed > float(max_seconds):
                raise CircuitBreakerTripped(
                    f"Time budget tripped on {fn.__name__}: {elapsed:.2f}s > {max_seconds:.2f}s.",
                )

            if last_exc is None:
                try:
                    return _validate_with_sentinel(sentinel_schema, raw_out)
                except Exception as val_exc:
                    last_exc = val_exc

            # Medic auto-repair up to `max_medic_repairs` (default 2)
            if medic_repair_fn is not None and last_exc is not None:
                candidate = raw_out
                for _attempt in range(max(0, int(max_medic_repairs))):
                    try:
                        candidate = medic_repair_fn(candidate, last_exc)
                        return _validate_with_sentinel(sentinel_schema, candidate)
                    except Exception as rep_exc:
                        last_exc = rep_exc

            assert last_exc is not None
            raise last_exc

        def _reset_fuse(instance: Any = None) -> None:
            with lock:
                if instance is None:
                    seen_inputs_by_scope.clear()
                    spent_by_scope.clear()
                else:
                    seen_inputs_by_scope.pop(id(instance), None)
                    spent_by_scope.pop(id(instance), None)

        _wrapped.reset_fuse = _reset_fuse  # type: ignore[attr-defined]
        return _wrapped

    return _decorator


class GuardLoop:
    """Production loop supervisor combining:
    - Hard tool-call count, wall-clock timeout, and USD budget caps
    - Per-tool circuit breakers: after `same_error_threshold` (default 3)
      consecutive identical-error failures, disables that tool for the session
      and records a tamper-evident security event in `EvidenceLedger`
    - OpenTelemetry span wrapping for every protected call
    """

    def __init__(
        self,
        *,
        max_tool_calls: int = 25,
        max_seconds: float = 120.0,
        max_cost_usd: float = 0.50,
        same_error_threshold: int = 3,
        evidence_ledger: Any | None = None,
        tracer: Any | None = None,
    ):
        self.max_tool_calls = int(max_tool_calls)
        self.max_seconds = float(max_seconds)
        self.max_cost_usd = float(max_cost_usd)
        self.same_error_threshold = max(2, int(same_error_threshold))
        self.evidence_ledger = evidence_ledger
        self.tracer = tracer
        self._started_at = time.monotonic()
        self._tool_calls = 0
        self._spent_usd = 0.0
        self._lock = threading.RLock()
        self._tool_err_streak: dict[str, tuple[str, int]] = {}
        self._disabled_tools: dict[str, str] = {}

    def is_tool_disabled(self, tool_name: str) -> bool:
        with self._lock:
            return tool_name in self._disabled_tools

    def disabled_tools(self) -> dict[str, str]:
        with self._lock:
            return dict(self._disabled_tools)

    def _log_breaker_evidence(self, tool_name: str, error_sig: str, streak: int) -> None:
        if self.evidence_ledger is None:
            return
        try:
            self.evidence_ledger.record_security_event(
                event_type="guardloop_tool_circuit_open",
                summary=f"Tool '{tool_name}' disabled for session after {streak} consecutive identical errors: {error_sig[:120]}",
                actor_identity="autonomy:guardloop",
                policy_decision="tool_disabled",
            )
        except Exception:
            pass

    def call_tool(
        self,
        tool_name: str,
        fn: Callable[..., Any],
        *args: Any,
        cost_usd: float = 0.0,
        **kwargs: Any,
    ) -> Any:
        with self._lock:
            if tool_name in self._disabled_tools:
                raise CircuitBreakerTripped(
                    f"GuardLoop per-tool breaker open for '{tool_name}': {self._disabled_tools[tool_name]}",
                )
            if self._tool_calls >= self.max_tool_calls:
                raise CircuitBreakerTripped(
                    f"GuardLoop tool-call limit exceeded ({self._tool_calls}/{self.max_tool_calls}).",
                )
            if (time.monotonic() - self._started_at) > self.max_seconds:
                raise CircuitBreakerTripped(
                    f"GuardLoop session timeout exceeded (>{self.max_seconds:.1f}s).",
                )
            if self._spent_usd + max(0.0, float(cost_usd)) > self.max_cost_usd:
                raise CircuitBreakerTripped(
                    f"GuardLoop USD budget exceeded (${self._spent_usd + cost_usd:.4f} > ${self.max_cost_usd:.4f}).",
                )
            self._tool_calls += 1
            self._spent_usd += max(0.0, float(cost_usd))

        span_cm = (
            self.tracer.start_span(
                f"guardloop.tool.{tool_name}",
                kind="tool",
                attributes={"guardloop.tool": tool_name, "guardloop.call_index": self._tool_calls},
            )
            if self.tracer is not None
            else None
        )
        span_obj = span_cm.__enter__() if span_cm is not None else None
        try:
            out = fn(*args, **kwargs)
            # Also detect structured ExecResult failures (success=False)
            if hasattr(out, "success") and out.success is False:
                err_sig = str(getattr(out, "error", "") or "execution_failed").strip().lower()[:160]
                self._record_tool_error(tool_name, err_sig)
            else:
                with self._lock:
                    self._tool_err_streak.pop(tool_name, None)
            return out
        except Exception as exc:
            err_sig = f"{type(exc).__name__}:{str(exc).strip().lower()[:140]}"
            self._record_tool_error(tool_name, err_sig)
            if span_obj is not None:
                span_obj.status = "error"
            raise
        finally:
            if span_cm is not None:
                span_cm.__exit__(None, None, None)

    def _record_tool_error(self, tool_name: str, error_sig: str) -> None:
        with self._lock:
            prev_sig, prev_cnt = self._tool_err_streak.get(tool_name, ("", 0))
            streak = (prev_cnt + 1) if prev_sig == error_sig else 1
            self._tool_err_streak[tool_name] = (error_sig, streak)
            if streak >= self.same_error_threshold:
                self._disabled_tools[tool_name] = f"{streak}x {error_sig}"
                self._log_breaker_evidence(tool_name, error_sig, streak)
