"""Celina Brain Loop Primitives (`brain_harness.py`)

Hardens Celina's Path-B Router-Controller loop across 8 GB / 16 GB / 32 GB tiers:

  1. `BrainCallbacks`        — Unified real-time hooks for CLI, SentenceStreamSpeaker,
                               and D-Bus / GNOME Dynamic Island state updates.
  2. `normalize_tool_calls()` — Unified parser for OpenAI/Ollama native `tool_calls`,
                                Qwen `<tool_call>{...}</tool_call>` XML blocks, and
                                fenced JSON tool calls in `content`.
  3. `call_with_backoff()`   — Bounded exponential backoff + jitter for transient
                                LLM/provider errors (429/502/503/504/timeout).
  4. `compact_conversation_history()` — Tier-adaptive sliding-window compactor that
                                preserves recent verbatim turns while rolling older
                                turns into a deterministic summary block.
  5. `verify_final_completion()` — 0-LLM-token completion gate blocking "victory
                                   declaration bias" when any tool in the turn failed,
                                   was denied, or was observed-only.

Zero third-party dependencies (pure Python 3.13 stdlib).
Zero $HOME filesystem mutations at import time.
"""

from __future__ import annotations

import dataclasses
import json
import random
import re
import time
from collections.abc import Callable
from typing import Any

_XML_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>", re.IGNORECASE)
_FENCED_TOOL_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\"name\"\s*:[\s\S]*?\})\s*```", re.IGNORECASE)
_VICTORY_CLAIM_RE = re.compile(
    r"\b(?:done|completed|finished|successfully\s+(?:saved|updated|deleted|moved|created|sent|executed|installed)|i\s+have\s+(?:saved|updated|deleted|moved|created|sent|run))\b",
    re.IGNORECASE,
)
_TOOL_FAIL_PREFIXES = (
    "error:",
    "failed",
    "blocked:",
    "denied:",
    "cancelled",
    "refused",
    "tool argument error",
    "tool execution error",
    "offline fallback",
)


# ---------------------------------------------------------------------------
# 1. Unified Event & Streaming Callback Contract
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class BrainCallbacks:
    """Unified real-time callback contract for CLI, voice speaker, and D-Bus."""

    on_route: Callable[[str, str], None] | None = None              # (gear, reason)
    on_tool_start: Callable[[str, dict[str, Any]], None] | None = None
    on_tool_end: Callable[[str, bool, str, int], None] | None = None  # (tool, ok, preview, ms)
    on_token_chunk: Callable[[str], None] | None = None             # cumulative/delta text -> SentenceStreamSpeaker
    on_turn_done: Callable[[dict[str, Any]], None] | None = None

    def emit(self, hook_name: str, *args: Any) -> None:
        """Fail-open emit: a raising callback never breaks the brain loop."""
        fn = getattr(self, hook_name, None)
        if callable(fn):
            try:
                fn(*args)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 2. Local + Cloud Tool-Call Normalizer (Ollama / llama-server / OpenAI)
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class NormalizedToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    source_format: str  # "native" | "xml_tag" | "fenced_json"


def _parse_args_dict(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return dict(raw_args)
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def normalize_tool_calls(
    response_message: dict[str, Any],
    *,
    allowed_tool_names: set[str] | None = None,
) -> list[NormalizedToolCall]:
    """Extract tool calls reliably across OpenAI/Cloud, Ollama, and llama-server:

      1. Native `tool_calls` array (preferred)
      2. Qwen3 / local GGUF `<tool_call>{...}</tool_call>` blocks inside `content`
      3. Fenced ```json {"name": ..., "arguments": {...}}``` fallback

    Filters against `allowed_tool_names` when supplied to reject hallucinated tools early.
    """
    results: list[NormalizedToolCall] = []

    # 1. Native `tool_calls` array (OpenAI / Ollama / llama-server native format)
    native = response_message.get("tool_calls")
    if isinstance(native, list) and native:
        for idx, tc in enumerate(native):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
            name = str(fn.get("name") or "").strip()
            if not name or (allowed_tool_names is not None and name not in allowed_tool_names):
                continue
            args = _parse_args_dict(fn.get("arguments"))
            cid = str(tc.get("id") or f"call_{idx + 1}")
            results.append(NormalizedToolCall(cid, name, args, "native"))
        if results:
            return results

    # 2 & 3. Parse from `content` — XML tags or fenced JSON
    content = str(response_message.get("content") or "")
    if not content:
        return []

    for fmt, regex in (("xml_tag", _XML_TOOL_CALL_RE), ("fenced_json", _FENCED_TOOL_RE)):
        matches = regex.findall(content)
        if not matches:
            continue
        for idx, blob in enumerate(matches):
            try:
                obj = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            name = str(obj.get("name") or "").strip()
            if not name or (allowed_tool_names is not None and name not in allowed_tool_names):
                continue
            args = _parse_args_dict(obj.get("arguments") or obj.get("parameters") or {})
            results.append(NormalizedToolCall(f"{fmt}_{idx + 1}", name, args, fmt))
        if results:
            return results

    return []


# ---------------------------------------------------------------------------
# 3. Exponential Backoff + Jitter for Transient Provider Failures
# ---------------------------------------------------------------------------
def call_with_backoff(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 3,
    base_delay_sec: float = 0.5,
    max_delay_sec: float = 6.0,
    is_retryable: Callable[[Exception], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Any:
    """Retry transient network/429/503 LLM errors with bounded exponential backoff + jitter.

    Args:
        fn: No-arg callable to invoke.
        max_attempts: Total number of attempts (1 = no retries).
        base_delay_sec: Base delay for exponential backoff.
        max_delay_sec: Ceiling on the delay before jitter.
        is_retryable: Optional predicate to classify retryable exceptions.
        sleep_fn: Sleep callable (injectable for testing).

    """
    attempts = max(1, int(max_attempts))

    def _default_retryable(exc: Exception) -> bool:
        msg = f"{type(exc).__name__} {exc}".lower()
        return any(
            tok in msg
            for tok in ("timeout", "timed out", "429", "502", "503", "504",
                         "connection", "temporarily unavailable", "unreachable")
        )

    check = is_retryable or _default_retryable
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= attempts or not check(exc):
                raise
            delay = min(max_delay_sec, base_delay_sec * (2 ** attempt)) + random.uniform(0.0, 0.25)
            sleep_fn(delay)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# 4. Tier-Adaptive Sliding-Window History Compaction (8 GB / 16 GB / 32 GB)
# ---------------------------------------------------------------------------
_HISTORY_LIMITS_BY_TIER: dict[str, tuple[int, int]] = {
    # (keep_recent_messages, max_total_chars_before_compaction)
    "8gb": (6, 4_500),
    "16gb": (14, 16_000),
    "32gb": (28, 40_000),
}


def compact_conversation_history(
    history: list[dict[str, Any]],
    *,
    ram_tier: str = "16gb",
    on_episode_summary: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Tier-adaptive sliding-window conversation compaction.

    Preserves the latest N turns verbatim (scaled by RAM tier) and rolls older
    turns into a concise episodic summary block instead of losing context via
    raw FIFO drop. The summary can be persisted into `memory.db` `episodes`
    via `on_episode_summary`.

    Returns:
        (compacted_history, was_compacted)

    """
    keep_recent, max_chars = _HISTORY_LIMITS_BY_TIER.get(
        ram_tier, _HISTORY_LIMITS_BY_TIER["16gb"],
    )
    total_chars = sum(len(str(m.get("content") or "")) for m in history)
    if len(history) <= keep_recent or total_chars <= max_chars:
        return list(history), False

    older = history[:-keep_recent]
    recent = history[-keep_recent:]

    bullets: list[str] = []
    for msg in older:
        role = str(msg.get("role") or "unknown")
        if role == "system":
            continue
        snippet = " ".join(str(msg.get("content") or "").split())[:140]
        if snippet:
            bullets.append(f"- {role}: {snippet}")

    summary_text = "\n".join(bullets[-12:])
    if not summary_text:
        return list(recent), False

    if callable(on_episode_summary):
        try:
            on_episode_summary(summary_text)
        except Exception:
            pass

    compacted_header = {
        "role": "system",
        "content": f"[Earlier Session Summary — Compacted for {ram_tier.upper()} Context Budget]\n{summary_text}",
    }
    return [compacted_header, *recent], True


# ---------------------------------------------------------------------------
# 5. Deterministic Final-Answer Completion Verifier (Anti-Victory Bias)
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class CompletionVerdict:
    passed: bool
    reply: str
    corrected: bool = False
    issues: tuple[str, ...] = ()


def verify_final_completion(
    reply_text: str,
    tool_results: list[tuple[str, str]],
) -> CompletionVerdict:
    """0-LLM-token quality & honesty gate before ending the agent loop:

      - Rejects empty replies.
      - Detects "victory declaration bias": if any tool in this turn failed,
        was denied, or was blocked, NEVER allow the model to claim the action
        succeeded. Instead, return a guarded reply listing the failed tools.

    Args:
        reply_text: The LLM's final reply text.
        tool_results: List of (tool_name, tool_output) pairs from this turn.

    Returns:
        CompletionVerdict with `passed` indicating whether the reply is safe
        to return as-is. If `corrected=True`, use `verdict.reply` instead.

    """
    clean = (reply_text or "").strip()
    failed_tools = [
        (tname, str(tout).strip()[:160])
        for tname, tout in tool_results
        if str(tout or "").strip().lower().startswith(_TOOL_FAIL_PREFIXES)
    ]

    if not clean:
        if failed_tools:
            lines = [f"- `{t}`: {msg}" for t, msg in failed_tools]
            return CompletionVerdict(
                passed=False,
                reply="Action did not complete:\n" + "\n".join(lines),
                corrected=True,
                issues=("empty_reply_with_tool_errors",),
            )
        return CompletionVerdict(
            passed=False,
            reply="I could not generate a complete response for that request. "
            "Please retry or run /why.",
            corrected=True,
            issues=("empty_reply",),
        )

    if failed_tools and _VICTORY_CLAIM_RE.search(clean):
        notice = "\n".join(f"- `{t}` did NOT succeed: {msg}" for t, msg in failed_tools)
        guarded = (
            f"⚠️ Verification note — one or more tool calls did not succeed:\n"
            f"{notice}\n\n"
            f"Original draft:\n{clean}"
        )
        return CompletionVerdict(
            passed=False,
            reply=guarded,
            corrected=True,
            issues=("victory_declaration_bias_blocked",),
        )

    return CompletionVerdict(passed=True, reply=clean, corrected=False, issues=())


__all__ = [
    "BrainCallbacks",
    "CompletionVerdict",
    "NormalizedToolCall",
    "call_with_backoff",
    "compact_conversation_history",
    "normalize_tool_calls",
    "verify_final_completion",
]
