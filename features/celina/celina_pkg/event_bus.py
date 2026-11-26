"""Celina Event Bus (`event_bus.py`)

Lightweight typed pub/sub bus routing OS, PSI, calendar, MCP, and task events
through one topic contract. Features:
  - Thread-safe subscribe/unsubscribe with token-based management
  - Wildcard topic support (`"os.*"` matches `"os.focus_changed"`, etc.)
  - Debounce support (suppress rapid duplicate publishes within `debounce_sec`)
  - Fail-open subscriber error isolation (one bad handler never breaks others)
  - Module-level singleton `get_event_bus()`

Zero third-party dependencies (pure Python 3.13 stdlib).
Zero $HOME filesystem mutations at import time.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class _Subscription:
    token: str
    topic: str
    handler: Callable[[str, Any], None]


class EventBus:
    """Thread-safe typed pub/sub event bus with wildcard topics and debounce."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: dict[str, list[_Subscription]] = defaultdict(list)
        self._tokens: dict[str, _Subscription] = {}
        self._debounce_state: dict[str, float] = {}  # topic -> last publish time
        self._counter = 0

    def subscribe(
        self,
        topic: str,
        handler: Callable[[str, Any], None],
    ) -> str:
        """Subscribe `handler` to `topic`. Wildcard topics (e.g. ``"os.*"``) supported.

        Returns a subscription token usable with `unsubscribe()`.
        """
        topic = topic.strip()
        if not topic:
            raise ValueError("topic must not be empty")
        if not callable(handler):
            raise ValueError("handler must be callable")

        with self._lock:
            self._counter += 1
            token = f"sub-{self._counter}"
            sub = _Subscription(token=token, topic=topic, handler=handler)
            self._subscriptions[topic].append(sub)
            self._tokens[token] = sub
            return token

    def unsubscribe(self, token: str) -> bool:
        """Remove a subscription by token. Returns True if removed."""
        with self._lock:
            sub = self._tokens.pop(token, None)
            if sub is None:
                return False
            topic_subs = self._subscriptions.get(sub.topic, [])
            self._subscriptions[sub.topic] = [s for s in topic_subs if s.token != token]
            if not self._subscriptions[sub.topic]:
                del self._subscriptions[sub.topic]
            return True

    def publish(self, topic: str, payload: Any, debounce_sec: float = 0.0) -> int:
        """Publish `payload` to `topic`.

        Args:
            topic: The topic to publish to.
            payload: The event payload.
            debounce_sec: If > 0, suppress publishes to the same topic that
                occur within `debounce_sec` of the previous publish.

        Returns:
            The number of handlers that were invoked.

        """
        topic = topic.strip()
        if not topic:
            raise ValueError("topic must not be empty")

        with self._lock:
            # Debounce check
            if debounce_sec > 0:
                now = time.monotonic()
                last = self._debounce_state.get(topic, 0.0)
                if now - last < debounce_sec:
                    return 0  # Suppressed by debounce
                self._debounce_state[topic] = now

            # Collect matching handlers (exact + wildcard)
            handlers: list[Callable[[str, Any], None]] = []
            # Exact match
            for sub in self._subscriptions.get(topic, []):
                handlers.append(sub.handler)
            # Wildcard match (e.g. "os.*" matches "os.focus_changed")
            for sub_topic, subs in self._subscriptions.items():
                if "*" in sub_topic or "?" in sub_topic or "[" in sub_topic:
                    if fnmatch.fnmatch(topic, sub_topic):
                        for sub in subs:
                            handlers.append(sub.handler)

        # Invoke handlers outside the lock (fail-open isolation)
        invoked = 0
        for handler in handlers:
            try:
                handler(topic, payload)
                invoked += 1
            except Exception:
                # Fail-open: one bad handler never breaks others
                pass
        return invoked

    def subscriber_count(self, topic: str | None = None) -> int:
        """Return the number of subscribers for `topic`, or total if None."""
        with self._lock:
            if topic is None:
                return sum(len(subs) for subs in self._subscriptions.values())
            return len(self._subscriptions.get(topic, []))


# Module-level singleton
_event_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Return the module-level EventBus singleton."""
    global _event_bus
    with _bus_lock:
        if _event_bus is None:
            _event_bus = EventBus()
        return _event_bus


# ------------------------------------------------------------------
# ReflexGuard — EventBus reflexes are statically allowlisted to
# reversible Gear-0 actions ONLY.  A reflex (PSI spike → unload model,
# thermal event → throttle, etc.) must NEVER be able to reach a
# Tier-2 tool.  This is the new invariant from the P0 audit.
# ------------------------------------------------------------------
_REFLEX_SAFE_ACTIONS: frozenset[str] = frozenset({
    # Gear-0 reflex actions — all reversible, low blast radius
    "unload_model",
    "throttle_cpu",
    "throttle_gpu",
    "clear_compression_state",
    "clear_sensory_buffer",
    "pause_autonomy",
    "resume_autonomy",
    "emit_psi_alert",
    "emit_thermal_alert",
})


class ReflexGuard:
    """Static guard ensuring EventBus reflexes can only trigger
    reversible Gear-0 actions — never Tier-2 tools.

    Reflex handlers call `is_reflex_safe(action_name, tool_tier)` before
    dispatching.  Any Tier-2 tool or non-allowlisted action is rejected
    with fail-closed semantics.
    """

    @staticmethod
    def is_reflex_safe(action_name: str, tool_tier: int = 1) -> bool:
        """Return True only if `action_name` is a Gear-0 reflex-safe action
        and `tool_tier` is not Tier-2 (>= 2).
        """
        if tool_tier >= 2:
            return False
        return action_name in _REFLEX_SAFE_ACTIONS

    @staticmethod
    def assert_reflex_safe(action_name: str, tool_tier: int = 1) -> None:
        """Raise ValueError if the reflex action is not Gear-0 safe."""
        if not ReflexGuard.is_reflex_safe(action_name, tool_tier):
            raise ValueError(
                f"ReflexGuard: '{action_name}' (tier={tool_tier}) is not a "
                "reflex-safe Gear-0 action. Reflexes may only trigger "
                "reversible Tier-0/1 actions.",
            )


__all__ = [
    "EventBus",
    "ReflexGuard",
    "get_event_bus",
]
