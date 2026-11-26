"""Celina AI (Celly) - Plugin #2: Local Habit & Frequency Engine (Update 19)

Deterministic statistical tracking of user commands, time of day, and day of week.
Stored in SQLite. Zero tokens, zero cloud, sub-5ms lookups.

Used for proactive suggestions: "Start Focus Mode?" at your usual time.
"""

from __future__ import annotations

import datetime
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

_DB_PATH = Path(os.environ.get("CELINA_HABIT_DB", Path.home() / ".celina" / "storage" / "shared" / "habits.db"))


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=3000")
    c.execute("PRAGMA synchronous=NORMAL")

    # Command frequency log
    c.execute("""
        CREATE TABLE IF NOT EXISTS command_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT NOT NULL,
            hour INTEGER NOT NULL,
            weekday INTEGER NOT NULL,  -- 0=Monday
            date TEXT NOT NULL,
            ts TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_cmd_log_cmd ON command_log(command)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cmd_log_hour ON command_log(hour)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cmd_log_weekday ON command_log(weekday)")

    # Learned habit patterns (high-frequency recurring commands)
    c.execute("""
        CREATE TABLE IF NOT EXISTS habit_patterns (
            command TEXT PRIMARY KEY,
            total_count INTEGER NOT NULL DEFAULT 0,
            preferred_hour INTEGER,
            preferred_weekday INTEGER,
            confidence REAL NOT NULL DEFAULT 0.0,
            last_seen TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    c.commit()
    return c


def log_command(command: str) -> None:
    """Log a command execution for habit learning."""
    try:
        now = datetime.datetime.now()
        c = _conn()
        c.execute(
            "INSERT INTO command_log (command, hour, weekday, date, ts) VALUES (?, ?, ?, ?, ?)",
            (command, now.hour, now.weekday(), now.strftime("%Y-%m-%d"), now.isoformat()),
        )
        c.commit()
        _update_pattern(c, command, now.hour, now.weekday())
        c.commit()
        c.close()
    except Exception:
        pass


def _update_pattern(c: sqlite3.Connection, command: str, hour: int, weekday: int) -> None:
    """Increment habit pattern for a command."""
    row = c.execute(
        "SELECT total_count, preferred_hour, preferred_weekday FROM habit_patterns WHERE command = ?",
        (command,),
    ).fetchone()

    if row:
        count = row[0] + 1
        # Track preferred hour (mode)
        hour_counts = _hour_mode(c, command)
        weekday_counts = _weekday_mode(c, command)
        pref_hour = hour_counts[0][0] if hour_counts else hour
        pref_weekday = weekday_counts[0][0] if weekday_counts else weekday
        confidence = min(1.0, count / 10.0)  # Full confidence at 10+ occurrences
        c.execute(
            """UPDATE habit_patterns SET total_count = ?, preferred_hour = ?,
               preferred_weekday = ?, confidence = ?, last_seen = datetime('now'),
               updated_at = datetime('now') WHERE command = ?""",
            (count, pref_hour, pref_weekday, confidence, command),
        )
    else:
        c.execute(
            """INSERT INTO habit_patterns (command, total_count, preferred_hour,
               preferred_weekday, confidence, last_seen)
               VALUES (?, 1, ?, ?, 0.1, datetime('now'))""",
            (command, hour, weekday),
        )


def _hour_mode(c: sqlite3.Connection, command: str) -> list[tuple[int, int]]:
    """Return most common hour for a command as [(hour, count), ...]."""
    rows = c.execute(
        "SELECT hour, COUNT(*) as cnt FROM command_log WHERE command = ? GROUP BY hour ORDER BY cnt DESC LIMIT 3",
        (command,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _weekday_mode(c: sqlite3.Connection, command: str) -> list[tuple[int, int]]:
    """Return most common weekday for a command as [(weekday, count), ...]."""
    rows = c.execute(
        "SELECT weekday, COUNT(*) as cnt FROM command_log WHERE command = ? GROUP BY weekday ORDER BY cnt DESC LIMIT 3",
        (command,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def get_habit_pattern(command: str) -> dict[str, Any] | None:
    """Get the learned habit pattern for a command."""
    try:
        c = _conn()
        row = c.execute(
            "SELECT command, total_count, preferred_hour, preferred_weekday, confidence, last_seen FROM habit_patterns WHERE command = ?",
            (command,),
        ).fetchone()
        c.close()
        if not row:
            return None
        return {
            "command": row[0],
            "total_count": row[1],
            "preferred_hour": row[2],
            "preferred_weekday": row[3],
            "confidence": row[4],
            "last_seen": row[5],
        }
    except Exception:
        return None


def get_proactive_suggestions() -> list[dict[str, Any]]:
    """Return commands that are likely to be needed right now based on time/weekday patterns.
    Only returns patterns with confidence >= 0.5 (5+ occurrences).
    """
    try:
        c = _conn()
        now = datetime.datetime.now()
        rows = c.execute(
            """SELECT command, total_count, preferred_hour, preferred_weekday, confidence
               FROM habit_patterns
               WHERE confidence >= 0.5
               ORDER BY confidence DESC, total_count DESC
               LIMIT 10""",
        ).fetchall()
        c.close()

        suggestions = []
        for row in rows:
            cmd, count, pref_hour, pref_weekday, conf = row
            # Match if current hour and weekday match the preferred ones
            hour_match = pref_hour is not None and pref_hour == now.hour
            weekday_match = pref_weekday is not None and pref_weekday == now.weekday()
            if hour_match or weekday_match:
                suggestions.append({
                    "command": cmd,
                    "confidence": conf,
                    "total_count": count,
                    "preferred_hour": pref_hour,
                    "preferred_weekday": pref_weekday,
                    "reason": "time_match" if hour_match else "weekday_match",
                })
        return suggestions
    except Exception:
        return []


def get_all_patterns() -> list[dict[str, Any]]:
    """Return all learned habit patterns."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT command, total_count, preferred_hour, preferred_weekday, confidence, last_seen FROM habit_patterns ORDER BY total_count DESC",
        ).fetchall()
        c.close()
        return [
            {
                "command": r[0],
                "total_count": r[1],
                "preferred_hour": r[2],
                "preferred_weekday": r[3],
                "confidence": r[4],
                "last_seen": r[5],
            }
            for r in rows
        ]
    except Exception:
        return []


__all__ = [
    "gear0_habit_classify",
    "gear0_habit_execute",
    "get_all_patterns",
    "get_habit_pattern",
    "get_proactive_suggestions",
    "log_command",
]


# ---------------------------------------------------------------------------
# Gear 0 Plugin #2 dispatch: Local Habit & Frequency Engine
# ---------------------------------------------------------------------------

_HABIT_QUERY_RE = re.compile(
    r"^(?:please\s+)?(?:habit|habits|routine\s+pattern|what\s+do\s+i\s+usually)\??$",
    re.IGNORECASE,
)
_HABIT_SUGGEST_RE = re.compile(
    r"^(?:please\s+)?(?:suggest|proactive|what\s+should\s+i\s+do|predict)\??$",
    re.IGNORECASE,
)


def gear0_habit_classify(query: str) -> str | None:
    """Classify query into a habit handler name, or None."""
    low = query.strip().lower()
    if _HABIT_QUERY_RE.match(low):
        return "get_all_patterns"
    if _HABIT_SUGGEST_RE.match(low):
        return "get_proactive_suggestions"
    return None


def gear0_habit_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0 Plugin #2 habit handler."""
    try:
        if handler == "get_all_patterns":
            patterns = get_all_patterns()
            if not patterns:
                return "No habit patterns learned yet. Keep using Celina and I'll learn your routines."
            lines = ["🧠 Learned Habit Patterns:"]
            for p in patterns[:10]:
                lines.append(f"  • {p['command']} — {p['total_count']}x (confidence: {p['confidence']:.0%})")
            return "\n".join(lines)

        if handler == "get_proactive_suggestions":
            suggestions = get_proactive_suggestions()
            if not suggestions:
                return "No proactive suggestions right now — no habits match this time/weekday."
            lines = ["🔮 Proactive Suggestions (based on your habits):"]
            for s in suggestions:
                lines.append(f"  • {s['command']} (confidence: {s['confidence']:.0%})")
            return "\n".join(lines)

    except Exception:
        return None
    return None

