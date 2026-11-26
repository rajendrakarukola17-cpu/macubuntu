"""Celina AI — Capabilities #3 & #4 (The "Forge" Reliability Guardrail Layer):
3. Generic, Tool-Agnostic Guardrail Stack (`ForgeGuardrailStack`):
- Retry nudges on malformed/failed tool calls
- Hard step-count enforcement
- Error-class classification & recovery directives
- Deterministic context compaction preserving goal + latest observations
- Hardware-aware RAM/VRAM token budgeting
4. Per-Tool Circuit Breaker with Bulkheads, Loop Detection & Schema Validation:
- Same-error-code tracking: 3 consecutive identical error-code failures
disables that tool for the session (`session_disabled`)
- Domain Bulkheads (`ToolBulkhead`) bounding per-domain concurrency
- Action Loop Detector (`ActionLoopDetector`) catching A->A->A & A->B->A->B cycles
- Strict JSON-Schema / optional Pydantic v2 argument validation before execution
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

from . import config

try:
    import pydantic as _pydantic  # type: ignore[import-not-found]
except Exception:
    _pydantic = None


def extract_error_code(error_text: str) -> str:
    """Classifies raw tool error output into a normalized error code for circuit breaking."""
    low = (error_text or "").strip().lower()
    if not low:
        return "empty_error"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "permission denied" in low or "eacces" in low or "unauthorized" in low:
        return "permission_denied"
    if "not found" in low or "no such file" in low or "enoent" in low:
        return "not_found"
    if "offline" in low or "network" in low or "dns" in low or "connection refused" in low:
        return "network_error"
    if "rate limit" in low or "http 429" in low or "too many requests" in low:
        return "rate_limit"
    if "validation" in low or "argument error" in low or "invalid" in low or "missing required" in low:
        return "schema_error"
    if "blocked" in low or "denied" in low or "cancelled" in low:
        return "policy_denied"
    m = re.search(r"http\s+(\d{3})", low)
    if m:
        return f"http_{m.group(1)}"
    return "runtime_error"


class SessionCircuitBreaker:
    """Capability #4: Per-tool circuit breaker tracking consecutive SAME-ERROR-CODE failures.
    After `same_error_threshold` (default 3) consecutive failures with the same error code
    on a tool, disables that tool for the remainder of the session (`session_disabled`).
    Also maintains general cooldown protection for mixed errors.
    """

    def __init__(
        self,
        *,
        same_error_threshold: int = 3,
        mixed_threshold: int = 3,
        cooldown_sec: float = 60.0,
    ):
        self.same_error_threshold = max(1, int(same_error_threshold))
        self.threshold = max(1, int(mixed_threshold))
        self.cooldown_sec = max(5.0, float(cooldown_sec))
        self._lock = threading.RLock()
        self._failures: dict[str, int] = {}
        self._last_error_code: dict[str, str] = {}
        self._same_code_streak: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._session_disabled: dict[str, str] = {}  # tool_name -> reason

    def is_session_disabled(self, tool_name: str) -> str | None:
        with self._lock:
            return self._session_disabled.get(tool_name)

    def check_open(self, tool_name: str, now: float | None = None) -> float | None:
        """Returns remaining open seconds (float('inf') if session-disabled), or None if closed."""
        ts = time.monotonic() if now is None else now
        with self._lock:
            if tool_name in self._session_disabled:
                return float("inf")
            until = self._open_until.get(tool_name)
            if until is None:
                return None
            rem = until - ts
            if rem > 0:
                return rem
            self._open_until.pop(tool_name, None)
            self._failures.pop(tool_name, None)
            return None

    def record_success(self, tool_name: str) -> None:
        with self._lock:
            self._failures.pop(tool_name, None)
            self._same_code_streak.pop(tool_name, None)
            self._last_error_code.pop(tool_name, None)
            self._open_until.pop(tool_name, None)

    def record_failure(
        self,
        tool_name: str,
        error_code: str | None = None,
        *,
        error_text: str = "",
        now: float | None = None,
    ) -> bool:
        """Records a failure. Returns True if circuit opened OR tool was session-disabled.
        """
        ts = time.monotonic() if now is None else now
        code = (error_code or extract_error_code(error_text)).strip().lower()
        with self._lock:
            total_cnt = self._failures.get(tool_name, 0) + 1
            self._failures[tool_name] = total_cnt

            if self._last_error_code.get(tool_name) == code:
                streak = self._same_code_streak.get(tool_name, 0) + 1
            else:
                self._last_error_code[tool_name] = code
                streak = 1
            self._same_code_streak[tool_name] = streak

            if streak >= self.same_error_threshold:
                self._session_disabled[tool_name] = (
                    f"disabled for session after {streak} consecutive '{code}' failures"
                )
                return True

            if total_cnt >= self.threshold:
                self._open_until[tool_name] = ts + self.cooldown_sec
                return True
            return False

    def snapshot(self) -> dict[str, dict[str, Any]]:
        ts = time.monotonic()
        with self._lock:
            keys = sorted(set(self._failures) | set(self._open_until) | set(self._session_disabled))
            out: dict[str, dict[str, Any]] = {}
            for k in keys:
                disabled_reason = self._session_disabled.get(k)
                out[k] = {
                    "consecutive_failures": self._failures.get(k, 0),
                    "same_error_code": self._last_error_code.get(k, ""),
                    "same_error_streak": self._same_code_streak.get(k, 0),
                    "session_disabled": disabled_reason is not None,
                    "session_disabled_reason": disabled_reason or "",
                    "open_remaining_sec": (
                        None if disabled_reason else round(max(0.0, self._open_until.get(k, 0.0) - ts), 1)
                    ),
                }
            return out

    def reset(self, tool_name: str | None = None) -> None:
        with self._lock:
            if tool_name is None:
                self._failures.clear()
                self._last_error_code.clear()
                self._same_code_streak.clear()
                self._open_until.clear()
                self._session_disabled.clear()
            else:
                self._failures.pop(tool_name, None)
                self._last_error_code.pop(tool_name, None)
                self._same_code_streak.pop(tool_name, None)
                self._open_until.pop(tool_name, None)
                self._session_disabled.pop(tool_name, None)


class BulkheadRejectedError(RuntimeError):
    """Raised when a domain bulkhead concurrency slot is saturated."""


class ToolBulkhead:
    """Per-domain concurrency bulkhead preventing one saturated subsystem from starving others."""

    DEFAULT_LIMITS: dict[str, int] = {
        "os_hardware": 4,
        "files_office": 2,
        "web_comms": 2,
        "wayland_gui": 1,
        "apple_pim_writing": 2,
        "continuity_audio_privacy": 2,
        "skills_memory": 4,
        "mcp_system": 2,
    }

    def __init__(self, limits: dict[str, int] | None = None):
        merged = dict(self.DEFAULT_LIMITS)
        if limits:
            merged.update({k: max(1, int(v)) for k, v in limits.items()})
        self._limits = merged
        self._lock = threading.RLock()
        self._sems: dict[str, threading.BoundedSemaphore] = {
            dom: threading.BoundedSemaphore(lim) for dom, lim in merged.items()
        }
        self._in_flight: dict[str, int] = {}

    def _get_sem(self, domain: str) -> threading.BoundedSemaphore:
        with self._lock:
            sem = self._sems.get(domain)
            if sem is None:
                sem = threading.BoundedSemaphore(2)
                self._sems[domain] = sem
            return sem

    @contextlib.contextmanager
    def acquire(self, domain: str, *, timeout: float = 0.05) -> Iterator[None]:
        dom = (domain or "os_hardware").strip()
        sem = self._get_sem(dom)
        acquired = sem.acquire(timeout=max(0.0, float(timeout)))
        if not acquired:
            raise BulkheadRejectedError(
                f"Bulkhead saturated for domain '{dom}' (limit={self._limits.get(dom, 2)}).",
            )
        with self._lock:
            self._in_flight[dom] = self._in_flight.get(dom, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._in_flight[dom] = max(0, self._in_flight.get(dom, 1) - 1)
            sem.release()

    def stats(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                dom: {"limit": self._limits.get(dom, 2), "in_flight": self._in_flight.get(dom, 0)}
                for dom in sorted(set(self._limits) | set(self._in_flight))
            }


class ActionLoopDetector:
    """Detects direct (A->A->A) and alternating (A->B->A->B) tool-call loops."""

    def __init__(self, *, max_identical: int = 3, max_alternating_pairs: int = 2):
        self.max_identical = max(2, int(max_identical))
        self.max_alternating_pairs = max(2, int(max_alternating_pairs))
        self._history: list[str] = []

    @staticmethod
    def _sig(tool_name: str, arguments: dict[str, Any]) -> str:
        raw = json.dumps({"tool": tool_name, "args": arguments or {}}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def record_and_check(self, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        sig = self._sig(tool_name, arguments)
        self._history.append(sig)
        # Check direct repeat: last N identical
        if len(self._history) >= self.max_identical:
            tail = self._history[-self.max_identical :]
            if len(set(tail)) == 1:
                return True, f"Direct loop detected: '{tool_name}' called {self.max_identical}x with identical arguments."
        # Check alternating cycle: A, B, A, B
        span = self.max_alternating_pairs * 2
        if len(self._history) >= span:
            window = self._history[-span:]
            evens = set(window[0::2])
            odds = set(window[1::2])
            if len(evens) == 1 and len(odds) == 1 and evens != odds:
                return True, f"Alternating loop detected (A->B->A->B) involving '{tool_name}'."
        return False, ""

    def reset(self) -> None:
        self._history.clear()


_JSON_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def validate_tool_argument_names(
    tool_name: str,
    arguments: dict[str, Any],
    schema_by_name: dict[str, dict[str, Any]],
) -> tuple[bool, str]:
    """Reject unknown fields before coercion or tool dispatch can discard them."""
    if not isinstance(arguments, dict):
        return False, f"Schema validation error ({tool_name}): arguments must be a JSON object."
    schema = schema_by_name.get(tool_name)
    if not isinstance(schema, dict) or not schema:
        return False, f"Schema validation error ({tool_name}): tool schema is unavailable."
    params = schema.get("parameters") or {}
    if not isinstance(params, dict) or not isinstance(params.get("properties", {}), dict):
        return False, f"Schema validation error ({tool_name}): malformed properties."
    props = params.get("properties") or {}
    for key in arguments:
        if key not in props:
            return False, f"Schema validation error ({tool_name}): undeclared argument {key!r}."
    return True, ""


def validate_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    schema_by_name: dict[str, dict[str, Any]],
) -> tuple[bool, str]:
    """Validates tool arguments against the declared JSON Schema (using Pydantic v2
    TypeAdapter when available, with a strict zero-dependency schema validator always active).
    """
    names_ok, names_error = validate_tool_argument_names(tool_name, arguments, schema_by_name)
    if not names_ok:
        return False, names_error
    fn_schema = schema_by_name[tool_name]
    params = fn_schema.get("parameters") or {}
    props = params.get("properties") or {}
    required = list(params.get("required") or [])

    for req_key in required:
        if req_key not in arguments or arguments[req_key] is None or (
            isinstance(arguments[req_key], str) and not arguments[req_key].strip()
        ):
            return False, f"Schema validation error ({tool_name}): missing required field '{req_key}'."

    for key, val in arguments.items():
        if val is None:
            continue
        p_spec = props[key]
        expected_type = p_spec.get("type")
        if expected_type == "integer":
            if isinstance(val, bool) or not isinstance(val, int):
                return False, f"Schema validation error ({tool_name}.{key}): expected integer, got {type(val).__name__}."
        elif expected_type == "number":
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                return False, f"Schema validation error ({tool_name}.{key}): expected number, got {type(val).__name__}."
        elif expected_type in _JSON_TYPE_MAP:
            if not isinstance(val, _JSON_TYPE_MAP[expected_type]):
                return False, f"Schema validation error ({tool_name}.{key}): expected {expected_type}, got {type(val).__name__}."
        allowed_enum = p_spec.get("enum")
        if isinstance(allowed_enum, list) and val not in allowed_enum:
            return False, f"Schema validation error ({tool_name}.{key}): value {val!r} not in allowed enum {allowed_enum}."

    # Optional Pydantic v2 round-trip check when installed
    if _pydantic is not None and hasattr(_pydantic, "TypeAdapter"):
        try:
            _pydantic.TypeAdapter(dict[str, Any]).validate_python(arguments)
        except Exception as exc:
            return False, f"Pydantic validation error ({tool_name}): {exc}"

    return True, ""


# ============================================================================
# Capability #3: Tool-Agnostic "Forge" Guardrail Stack
# ============================================================================

@dataclasses.dataclass
class ForgeBudget:
    max_steps: int
    max_context_chars: int
    avail_ram_mb: int
    compaction_triggered: bool = False


class ForgeGuardrailStack:
    """Tool-agnostic reliability guardrail stack closing the self-hosted vs. frontier gap:
    1. Step enforcement (hard cap + loop check)
    2. Retry nudges & structured error-recovery directives
    3. Deterministic context compaction
    4. Hardware-aware RAM/VRAM token budgeting
    """

    def __init__(self, *, max_steps: int = 8, max_context_chars: int = 14000):
        self.base_max_steps = max(1, int(max_steps))
        self.base_max_context_chars = max(2000, int(max_context_chars))
        self.loop_detector = ActionLoopDetector()

    def compute_hardware_budget(self, *, avail_ram_mb: int | None = None) -> ForgeBudget:
        avail = int(avail_ram_mb) if avail_ram_mb is not None else int(config.detect_available_ram_mb())
        if avail < 1200:
            return ForgeBudget(
                max_steps=min(self.base_max_steps, 4),
                max_context_chars=min(self.base_max_context_chars, 6000),
                avail_ram_mb=avail,
            )
        if avail < 2400:
            return ForgeBudget(
                max_steps=min(self.base_max_steps, 6),
                max_context_chars=min(self.base_max_context_chars, 10000),
                avail_ram_mb=avail,
            )
        return ForgeBudget(
            max_steps=self.base_max_steps,
            max_context_chars=self.base_max_context_chars,
            avail_ram_mb=avail,
        )

    def enforce_step(
        self,
        step_index: int,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        budget: ForgeBudget | None = None,
    ) -> tuple[bool, str]:
        b = budget or self.compute_hardware_budget()
        if step_index >= b.max_steps:
            return False, (
                f"[FORGE STEP LIMIT]: Reached step cap ({b.max_steps}). "
                "Stop calling tools and synthesize the best grounded answer from collected observations."
            )
        looped, loop_msg = self.loop_detector.record_and_check(tool_name, arguments)
        if looped:
            return False, (
                f"[FORGE LOOP GUARD]: {loop_msg} Switch strategy or summarize the blocker for the user."
            )
        return True, ""

    def build_retry_nudge(self, tool_name: str, error_text: str, attempt: int) -> str:
        code = extract_error_code(error_text)
        guidance = {
            "schema_error": "Check required argument names and types in the tool schema before retrying.",
            "not_found": "Locate the exact path/resource with a read-only search tool before retrying.",
            "timeout": "Reduce scope/size or use a lighter read-only tool.",
            "network_error": "Network is unavailable; fall back to local offline tools or cached memory.",
            "permission_denied": "Respect the OS/approval boundary; do not retry privileged paths.",
            "policy_denied": "Do not bypass user approval or security policy; explain the restriction clearly.",
        }.get(code, "Choose a different tool or smaller step instead of repeating the failing call.")
        return f"[FORGE RETRY NUDGE attempt={attempt} code={code} tool={tool_name}]: {guidance}"

    def compact_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        budget: ForgeBudget | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Deterministically compacts stale middle tool messages when total prompt size
        exceeds the hardware-aware character budget while preserving system prompt,
        original user goal, and the 2 most recent turns verbatim.
        """
        b = budget or self.compute_hardware_budget()
        total = sum(len(str(m.get("content") or "")) for m in messages)
        if total <= b.max_context_chars or len(messages) <= 4:
            return list(messages), False

        head = list(messages[:2])
        tail = list(messages[-2:])
        middle = messages[2:-2]
        digest_lines: list[str] = []
        for m in middle:
            role = str(m.get("role") or "tool")
            snippet = " ".join(str(m.get("content") or "").split())[:140]
            if snippet:
                digest_lines.append(f"- [{role}] {snippet}")

        compacted_msg = {
            "role": "system",
            "content": "[FORGE COMPACTED CONTEXT]\n" + "\n".join(digest_lines[:10]),
        }
        b.compaction_triggered = True
        return head + [compacted_msg] + tail, True
