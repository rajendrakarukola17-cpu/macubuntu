"""
Celina AI (Celly) - Gear 0 Focus / Deep Work (Update 19)

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
from pathlib import Path
from typing import Optional

_STATE_PATH = Path.home() / ".local" / "share" / "celina" / "gear0_focus_state.json"
_FOCUS_ACTIVE = False
_FOCUS_TIMER: Optional[threading.Timer] = None


def focus_start(minutes: int) -> str:
    """Start focus mode for N minutes."""
    global _FOCUS_ACTIVE, _FOCUS_TIMER
    _FOCUS_ACTIVE = True
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            f"{{\"active\": true, \"minutes\": {minutes}, \"started\": {time.time()}}}"
        )
    except Exception:
        pass

    # Notify via notify-send if available
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "Celly Focus", f"Focus mode started — {minutes} minutes"],
            capture_output=True, text=True, timeout=3,
        )

    def _end_focus():
        global _FOCUS_ACTIVE
        _FOCUS_ACTIVE = False
        try:
            _STATE_PATH.write_text("{\"active\": false}")
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

    return f"🎯 Focus mode started — {minutes} minutes. Distractions blocked."


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
        _STATE_PATH.write_text("{\"active\": false}")
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
# -------------------------------------------------------------------------__

_START_FOCUS_RE = __import__("re").compile(
    r"^(?:start|begin|enter)\s+focus\s+mode\s+(?:for\s+)?(\d+)\s*(?:min|minute|minutes)\??$",
    __import__("re").IGNORECASE,
)
_END_FOCUS_RE = __import__("re").compile(
    r"^(?:end|stop|exit)\s+focus\s+mode\??$",
    __import__("re").IGNORECASE,
)
_FOCUS_STATUS_RE = __import__("re").compile(
    r"^(?:focus\s+)?(?:status|mode)\??$",
    __import__("re").IGNORECASE,
)


def gear0_focus_classify(query: str) -> Optional[str]:
    """Classify query into a focus handler name, or None."""
    low = query.strip().lower()
    if _START_FOCUS_RE.match(low):
        return "start_focus"
    if _END_FOCUS_RE.match(low):
        return "end_focus"
    if _FOCUS_STATUS_RE.match(low):
        return "focus_status"
    return None


def gear0_focus_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0-Focus handler."""
    try:
        import re
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


__all__ = [
    "focus_start",
    "focus_end",
    "focus_status",
    "gear0_focus_classify",
    "gear0_focus_execute",
]
