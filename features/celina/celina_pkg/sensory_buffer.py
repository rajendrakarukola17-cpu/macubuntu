"""Celina Transient Sensory Buffer (`sensory_buffer.py`) — RAM-only multimodal
sensory ring buffer.

A bounded, ephemeral sliding ring buffer (TTL = 5 min, max 30 entries, zero disk
persistence) storing sanitized frames: (timestamp, kind, source_app, summary)
while refusing any frame captured while a password field is focused or the app
is a password manager.

All frames are tagged `source_trust="untrusted_sensory"` — never fed raw into
an LLM session with Tier-2 OS/filesystem mutation tools.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections import deque

_BLOCKED_APPS = frozenset(
    {"keepass", "bitwarden", "1password", "seahorse", "polkit", "kwallet", "gnome-keyring"},
)

_PASSWORD_FIELD_HINT = ("password", "passwd", "secret", "token", "api_key", "private_key")


@dataclasses.dataclass(frozen=True)
class SensoryFrame:
    ts: float
    kind: str  # "window" | "ocr" | "mirage" | "voice"
    source_app: str
    summary: str  # bounded, secret-stripped text (NEVER raw image bytes)
    source_trust: str = "untrusted_sensory"


class TransientSensoryBuffer:
    """Ephemeral RAM-only multimodal sensory memory (auto-expires after TTL; zero disk trail)."""

    def __init__(self, *, max_frames: int = 30, ttl_sec: float = 300.0) -> None:
        self._lock = threading.RLock()
        self._frames: deque[SensoryFrame] = deque(maxlen=max(5, int(max_frames)))
        self._ttl_sec = max(30.0, float(ttl_sec))

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._ttl_sec
        while self._frames and self._frames[0].ts < cutoff:
            self._frames.popleft()

    @staticmethod
    def _is_password_field_active(source_app: str, summary: str) -> bool:
        """Refuse frames from password fields or password-manager apps."""
        combined = f"{source_app} {summary}".lower()
        if any(b in combined for b in _BLOCKED_APPS):
            return True
        if any(h in combined for h in _PASSWORD_FIELD_HINT):
            return True
        return False

    def record(
        self,
        *,
        kind: str,
        source_app: str,
        summary: str,
        password_field_active: bool = False,
    ) -> bool:
        """Record a sensory frame. Returns True if recorded, False if refused."""
        if password_field_active:
            return False
        if self._is_password_field_active(source_app, summary):
            return False

        app_clean = (source_app or "").strip()
        text = " ".join((summary or "").split())[:400]
        if not text:
            return False

        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            self._frames.append(
                SensoryFrame(
                    ts=now,
                    kind=kind,
                    source_app=app_clean,
                    summary=text,
                ),
            )
            return True

    def recent_context(self, *, limit: int = 4) -> str:
        """Return up to `limit` recent frames as a formatted string, or empty string."""
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            items = list(self._frames)[-max(1, int(limit)):]
        if not items:
            return ""
        lines = ["[Transient Sensory Memory — Untrusted Data, RAM-Only]:"]
        for f in items:
            age = int(now - f.ts)
            lines.append(f"  - ({age}s ago | {f.kind} | {f.source_app or 'desktop'}): {f.summary}")
        return "\n".join(lines)

    def clear(self) -> None:
        """Clear all frames (called on /clear and Turbo Mode activation)."""
        with self._lock:
            self._frames.clear()


# Module-level singleton
_sensory_buffer: TransientSensoryBuffer | None = None
_sensory_lock = threading.Lock()


def get_sensory_buffer() -> TransientSensoryBuffer:
    """Return the module-level TransientSensoryBuffer singleton."""
    global _sensory_buffer
    with _sensory_lock:
        if _sensory_buffer is None:
            _sensory_buffer = TransientSensoryBuffer()
        return _sensory_buffer
