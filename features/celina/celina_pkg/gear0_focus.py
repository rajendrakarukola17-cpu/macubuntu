"""Celina AI (Celly) - Gear 0 Focus / Deep Work (Update 19)

Local focus mode — blocks distractions, starts timers, sets status.
Zero cloud, sub-5ms.

Examples:
  - "Start focus mode for 25 minutes"
  - "End focus mode"

"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

_STATE_PATH = Path.home() / ".local" / "share" / "celina" / "gear0_focus_state.json"
_FOCUS_ACTIVE = False
_FOCUS_TIMER: threading.Timer | None = None


def focus_start(minutes: int) -> str:
    """Start focus mode for N minutes."""
    global _FOCUS_ACTIVE, _FOCUS_TIMER
    _FOCUS_ACTIVE = True
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            '{"active": true, "minutes": ' + str(minutes) + ', "started": ' + str(time.time()) + "}",
        )
    except Exception:
        pass

    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "Celly Focus", "Focus mode started — " + str(minutes) + " minutes"],
            capture_output=True, text=True, timeout=3,
        )

    def _end_focus():
        global _FOCUS_ACTIVE
        _FOCUS_ACTIVE = False
        try:
            _STATE_PATH.write_text('{"active": false}')
        except Exception:
            pass
        if shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", "Celly Focus", "Focus mode ended — welcome back!"],
                capture_output=True, text=True, timeout=3,
            )

    _FOCUS_TIMER = threading.Timer(minutes * 60, _end_focus)
    _FOCUS_TIMER.daemon = True
    _FOCUS_TIMER.start()

    return "🎯 Focus mode started — " + str(minutes) + " minutes. Distractions blocked."


def focus_end() -> str:
    """End focus mode early."""
    global _FOCUS_ACTIVE, _FOCUS_TIMER
    if not _FOCUS_ACTIVE:
        return "Focus mode is not active."
    _FOCUS_ACTIVE = False
    if _FOCUS_TIMER:
        _FOCUS_TIMER.cancel()
        _FOCUS_TIMER = None
    try:
        _STATE_PATH.write_text('{"active": false}')
    except Exception:
        pass
    return "✓ Focus mode ended."


def focus_status() -> str:
    """Check focus mode status."""
    if _FOCUS_ACTIVE:
        return "🎯 Focus mode is ACTIVE."
    return "Focus mode is inactive."


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

import re

_START_FOCUS_RE = re.compile(
    r"^(?:start|begin|enter)\s+focus\s+mode\s+(?:for\s+)?(\d+)\s*(?:min|minute|minutes)\??$",
    re.IGNORECASE,
)
_END_FOCUS_RE = re.compile(
    r"^(?:end|stop|exit)\s+focus\s+mode\??$",
    re.IGNORECASE,
)
_FOCUS_STATUS_RE = re.compile(
    r"^(?:focus\s+)?(?:status|mode)\??$",
    re.IGNORECASE,
)


def gear0_focus_classify(query: str) -> str | None:
    """Classify query into a focus handler name, or None."""
    low = query.strip().lower()
    if _START_FOCUS_RE.match(low):
        return "start_focus"
    if _END_FOCUS_RE.match(low):
        return "end_focus"
    if _FOCUS_STATUS_RE.match(low):
        return "focus_status"
    return None


def gear0_focus_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Focus handler."""
    try:
        if handler == "start_focus":
            m = _START_FOCUS_RE.match(query.lower())
            if m:
                minutes = int(m.group(1))
                return focus_start(minutes)
        if handler == "end_focus":
            return focus_end()
        if handler == "focus_status":
            return focus_status()
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Micro-affect sensing (Phase B: behavioral friction detection)
# Detects undo/backspace bursts and window-switch churn as proxies for
# cognitive friction / frustration. Zero network; local deque only.
# ---------------------------------------------------------------------------


@dataclass
class InputEvent:
    ts: float = field(default_factory=time.time)
    event_type: str = ""
    detail: str = ""


class MicroAffectSensor:
    """Tracks micro-affect signals: undo/backspace bursts, window-switch churn."""

    def __init__(self, *, window_sec: int = 300) -> None:
        self.window_sec = max(60, int(window_sec))
        self._events: deque = deque()

    def record_input_event(self, *, event_type: str, detail: str = "") -> None:
        """Record a low-level input event (undo, backspace, keydown, etc.)."""
        self._events.append(InputEvent(ts=time.time(), event_type=event_type, detail=detail))
        self._prune()

    def record_window_switch(self, *, app: str, title: str = "") -> None:
        """Record a window-switch (app change) event."""
        self._events.append(InputEvent(
            ts=time.time(), event_type="window_switch", detail=(app + "|" + title[:80]),
        ))
        self._prune()

    def _prune(self) -> None:
        cutoff = time.time() - self.window_sec
        while self._events and self._events[0].ts < cutoff:
            self._events.popleft()

    def _count(self, event_type: str) -> int:
        return sum(1 for e in self._events if e.event_type == event_type)

    def compute_affect_stress(self) -> dict:
        """Compute a 0-100 affect-stress score from micro-affect signals."""
        total = len(self._events)
        if total == 0:
            return {"score": 0, "level": "calm", "undo_backspace": 0, "window_switches": 0,
                    "total_events": 0, "window_sec": self.window_sec}

        undo_backspace = self._count("undo") + self._count("backspace")
        window_switches = self._count("window_switch")

        ub_ratio = undo_backspace / total if total else 0
        frustration = min(50, int(ub_ratio * 100))

        mins = max(self.window_sec / 60.0, 1.0)
        switches_per_min = window_switches / mins
        distraction = min(50, int(switches_per_min * 10))

        score = min(100, frustration + distraction)
        if score >= 60:
            level = "stressed"
        elif score >= 30:
            level = "friction"
        else:
            level = "calm"

        return {
            "score": score,
            "level": level,
            "undo_backspace": undo_backspace,
            "window_switches": window_switches,
            "total_events": total,
            "window_sec": self.window_sec,
        }

    def suggest_focus_if_stressed(self, *, threshold: int = 40) -> str | None:
        """Suggest starting focus mode if affect stress exceeds threshold."""
        state = self.compute_affect_stress()
        if state["score"] >= max(10, min(90, int(threshold))):
            return (
                "Micro-affect stress detected (score=" + str(state["score"]) + "/100, level=" + state["level"] + "). "
                "Consider starting focus mode to reduce distraction churn."
            )
        return None


__all__ = [
    "InputEvent",
    "MicroAffectSensor",
    "focus_end",
    "focus_start",
    "focus_status",
    "gear0_focus_classify",
    "gear0_focus_execute",
]
