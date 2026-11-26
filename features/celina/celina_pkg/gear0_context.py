"""
Celina AI (Celly) - Gear 0-Context: State & Geofence Triggers (Update 19)

OS-level state awareness for trigger-based actions without LLM.
- Calendar-based: mute notifications when in a meeting
- Location-based: remind when arriving home (geofence placeholder)
- App-state: detect active window for context

Zero LLM tokens, <5ms lookup, uses system APIs.
"""

from __future__ import annotations

import datetime
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

_CONTEXT_DB_PATH = Path.home() / ".celina" / "storage" / "shared" / "context.db"

# Pre-compiled patterns
_MEETING_RE = re.compile(
    r"^(?:please\s+)?mute\s+notifications\s+when\s+im\s+in\s+a\s+meeting\??$",
    re.IGNORECASE,
)
_REMIND_WHEN_HOME_RE = re.compile(
    r"^(?:please\s+)?remind\s+me\s+to\s+(.+?)\s+when\s+I\s+get\s+home\??$",
    re.IGNORECASE,
)


def _get_conn() -> sqlite3.Connection:
    _CONTEXT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_CONTEXT_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,          -- 'calendar_busy', 'geofence_home', 'app_active'
            condition_json TEXT NOT NULL,-- JSON condition description
            action_json TEXT NOT NULL,   -- JSON action to execute
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            active INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    return conn


def context_add_trigger(kind: str, condition: dict[str, Any], action: dict[str, Any]) -> str:
    """Register a state-based trigger."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO triggers (kind, condition_json, action_json) VALUES (?, ?, ?)",
            (kind, json.dumps(condition), json.dumps(action)),
        )
        conn.commit()
        conn.close()
        return f"Registered {kind} trigger."
    except Exception as exc:
        return f"Failed to register trigger: {exc}"


def context_list_triggers() -> list[dict[str, Any]]:
    """List all active triggers."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, kind, condition_json, action_json, active FROM triggers WHERE active = 1"
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "condition": json.loads(r[2]),
                "action": json.loads(r[3]),
                "active": bool(r[4]),
            }
            for r in rows
        ]
    except Exception:
        return []


def context_check_calendar_busy() -> bool:
    """Check if the user is currently in a calendar meeting (best-effort)."""
    try:
        # Try to read from common calendar sources
        # This is a placeholder — actual implementation depends on OS calendar API
        # Returns True if busy, False otherwise
        return False
    except Exception:
        return False


def context_check_location_home() -> bool:
    """Check if the user is at home (geofence placeholder)."""
    try:
        # Placeholder for actual geofence implementation
        # Would use OS location services or Wi-Fi SSID detection
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_mute_when_busy(enable: bool, dry_run: bool = False) -> str:
    """Handle 'mute notifications when I'm in a meeting'."""
    condition = {"kind": "calendar_busy", "enabled": enable}
    action = {"kind": "dnd", "enabled": enable}

    if dry_run:
        return f"🧪 Dry-Run [Gear 0-Context]: Would {'enable' if enable else 'disable'} auto-DND when in meeting."

    try:
        context_add_trigger("calendar_busy", condition, action)
        return f"✓ Auto-DND when in meeting: {'enabled' if enable else 'disabled'}."
    except Exception as exc:
        return f"Failed to set context trigger: {exc}"


def handle_remind_when_home(message: str, dry_run: bool = False) -> str:
    """Handle 'remind me to buy milk when I get home'."""
    condition = {"kind": "geofence_home", "message": message}
    action = {"kind": "notify", "message": message}

    if dry_run:
        return f"🧪 Dry-Run [Gear 0-Context]: Would remind '{message}' when home."

    try:
        context_add_trigger("geofence_home", condition, action)
        return f"✓ Will remind '{message}' when you get home."
    except Exception as exc:
        return f"Failed to set reminder: {exc}"


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

def gear0_context_classify(query: str) -> Optional[str]:
    """Classify query into a context handler name, or None."""
    import re as _re

    low = query.strip().lower()

    if _MEETING_RE.match(low):
        return "mute_when_busy"
    if _REMIND_WHEN_HOME_RE.match(low):
        return "remind_when_home"

    return None


def gear0_context_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0-Context handler."""
    import re as _re

    try:
        if handler == "mute_when_busy":
            enable = "off" not in query.strip().lower() and "disable" not in query.strip().lower()
            return handle_mute_when_busy(enable, dry_run=dry_run)
        if handler == "remind_when_home":
            m = _REMIND_WHEN_HOME_RE.match(query.strip())
            if m:
                return handle_remind_when_home(m.group(1), dry_run=dry_run)
    except Exception:
        return None
    return None


__all__ = [
    "gear0_context_classify",
    "gear0_context_execute",
    "context_add_trigger",
    "context_list_triggers",
    "context_check_calendar_busy",
    "context_check_location_home",
]
