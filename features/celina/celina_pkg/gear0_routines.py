"""Celina AI (Celly) - Gear 0 Routines (Update 19)

Multi-step local routines — pre-compiled scripts executed atomically.

Examples:
  - "Good morning" → read calendar, weather, open news, set volume
  - "Start focus mode" → DND, block sites, start Pomodoro
  - "Good night" → DND, dim lights, set alarm

"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

_ROUTINES_DB_PATH = Path.home() / ".celina" / "storage" / "shared" / "routines.db"

# Pre-defined built-in routines
BUILTIN_ROUTINES: dict[str, list[dict[str, Any]]] = {
    "good_morning": [
        {"type": "hardware", "action": "dnd", "enabled": False},
        {"type": "query", "target": "calendar"},
        {"type": "query", "target": "weather"},
        {"type": "media", "action": "open_url", "url": "https://news.google.com"},
        {"type": "hardware", "action": "volume", "level": 40},
    ],
    "good_night": [
        {"type": "hardware", "action": "dnd", "enabled": True},
        {"type": "hardware", "action": "night_light", "enabled": True},
        {"type": "system", "action": "set_alarm", "time": "07:00"},
    ],
    "focus_mode": [
        {"type": "hardware", "action": "dnd", "enabled": True},
        {"type": "network", "action": "block_domains", "domains": ["twitter.com", "reddit.com", "youtube.com"]},
        {"type": "timer", "duration": 1500, "label": "Pomodoro"},
    ],
    "end_focus": [
        {"type": "hardware", "action": "dnd", "enabled": False},
        {"type": "network", "action": "unblock_domains"},
    ],
}

# Seed built-in routines once at module load — NOT on every connection
_SEEDED = False


def _ensure_seeded(conn: sqlite3.Connection) -> None:
    """Seed built-in routines into the DB — called once per connection, but
    only actually inserts if the row doesn't already exist (INSERT OR IGNORE).
    """
    global _SEEDED
    if not _SEEDED:
        for name, steps in BUILTIN_ROUTINES.items():
            conn.execute(
                "INSERT OR IGNORE INTO routines (name, steps) VALUES (?, ?)",
                (name, json.dumps(steps)),
            )
        conn.commit()
        _SEEDED = True


def _get_conn() -> sqlite3.Connection:
    _ROUTINES_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_ROUTINES_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS routines (
            name TEXT PRIMARY KEY,
            steps TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    _ensure_seeded(conn)
    return conn


def routine_run(name: str, dry_run: bool = False) -> str:
    """Execute a routine by name — runs all steps sequentially."""
    try:
        conn = _get_conn()
        row = conn.execute("SELECT steps FROM routines WHERE name = ?", (name.lower().strip(),)).fetchone()
        conn.close()
        if not row:
            return f"Routine '{name}' not found. Available: {', '.join(BUILTIN_ROUTINES.keys())}"

        steps = json.loads(row[0])
        results: list[str] = []

        if dry_run:
            lines = [f"🧪 Dry-Run [Gear 0-Routine]: Would run '{name}' routine:"]
            for i, step in enumerate(steps, 1):
                lines.append(f"   {i}. {step.get('type', '?')}: {step.get('action', step.get('target', '?'))}")
            return "\n".join(lines)

        for i, step in enumerate(steps, 1):
            step_type = step.get("type", "")
            action = step.get("action", step.get("target", ""))
            try:
                result = _execute_routine_step(step)
                results.append(f"  {i}. ✓ {step_type}: {result}")
            except Exception as exc:
                results.append(f"  {i}. ✗ {step_type}: {exc}")

        return f"🔄 Routine '{name}' completed:\n" + "\n".join(results)
    except Exception as exc:
        return f"Failed to run routine: {exc}"


def _execute_routine_step(step: dict[str, Any]) -> str:
    """Execute a single routine step."""
    step_type = step.get("type", "")
    action = step.get("action", step.get("target", ""))

    if step_type == "hardware":
        return _hardware_step(action, step)
    if step_type == "query":
        return _query_step(action)
    if step_type == "media":
        return _media_step(action, step)
    if step_type == "network":
        return _network_step(action, step)
    if step_type == "timer":
        return _timer_step(step)
    if step_type == "system":
        return _system_step(action, step)

    return f"unknown step type: {step_type}"


def _hardware_step(action: str, step: dict[str, Any]) -> str:
    """Execute hardware step."""
    from . import gear0_intent

    if action == "dnd":
        enabled = step.get("enabled", True)
        # Use gear0_intent's handle_dnd via synthetic query
        synthetic = f"turn {'on' if enabled else 'off'} dnd"
        return gear0_intent.handle_dnd(synthetic)
    if action == "volume":
        level = step.get("level", 50)
        synthetic = f"set volume to {level}%"
        return gear0_intent.handle_volume(synthetic)
    if action == "night_light":
        enabled = step.get("enabled", True)
        synthetic = f"turn {'on' if enabled else 'off'} night light"
        return gear0_intent.handle_night_light(synthetic)
    return f"hardware:{action}"


def _query_step(target: str) -> str:
    """Execute query step."""
    if target == "calendar":
        return _query_calendar()
    if target == "weather":
        return _query_weather()
    if target == "battery":
        from . import gear0_audit
        return gear0_audit.audit_battery()
    return f"query:{target}"


def _query_calendar() -> str:
    """Read next calendar event from local sources."""
    try:
        # Try calcurse
        if shutil.which("calcurse"):
            result = subprocess.run(
                ["calcurse", "-n"], capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                return result.stdout.strip()[:200]
        # Try khal
        if shutil.which("khal"):
            result = subprocess.run(
                ["khal", "list", "now", "today"], capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                return result.stdout.strip()[:200]
        # Try parsing .ics files
        ical_dirs = [
            Path.home() / ".local" / "share" / "evolution" / "calendar",
            Path.home() / ".cache" / "evolution" / "calendar",
        ]
        for d in ical_dirs:
            if d.exists():
                for f in d.glob("*.ics"):
                    content = f.read_text(errors="ignore")
                    # Simple VEVENT extraction
                    events = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", content, re.DOTALL)
                    if events:
                        return f"Found {len(events)} calendar event(s) in {f.name}"
        return "No calendar events found."
    except Exception:
        return "Calendar unavailable."


import re


def _query_weather() -> str:
    """Read local weather from cached file or system."""
    weather_files = [
        Path.home() / ".cache" / "celina" / "weather.json",
        Path.home() / ".weather",
    ]
    for f in weather_files:
        if f.exists():
            try:
                data = json.loads(f.read_text())
                return f"{data.get('temperature', '?')}°{data.get('unit', 'C')}, {data.get('condition', 'unknown')}"
            except Exception:
                continue
    return "No cached weather data available."


def _media_step(action: str, step: dict[str, Any]) -> str:
    """Execute media step."""
    from . import gear0_media

    if action == "open_url":
        url = step.get("url", "")
        synthetic = f"open {url}"
        return gear0_media.media_open_project(url) if url else "no URL specified"
    return f"media:{action}"


def _network_step(action: str, step: dict[str, Any]) -> str:
    """Execute network step."""
    if action == "block_domains":
        domains = step.get("domains", [])
        # Write to /etc/hosts (requires sudo)
        return f"Would block {len(domains)} domains (requires sudo for /etc/hosts)"
    if action == "unblock_domains":
        return "Would unblock domains"
    return f"network:{action}"


def _timer_step(step: dict[str, Any]) -> str:
    """Execute timer step."""
    from . import gear0_intent

    duration = step.get("duration", 60)
    label = step.get("label", "Timer")
    synthetic = f"set a timer for {duration} seconds for {label}"
    return gear0_intent.handle_timer(synthetic)


def _system_step(action: str, step: dict[str, Any]) -> str:
    """Execute system step."""
    if action == "set_alarm":
        time_str = step.get("time", "07:00")
        return f"Alarm set for {time_str} (system alarm not yet integrated)"
    return f"system:{action}"


def routine_list() -> list[str]:
    """List all available routines."""
    try:
        conn = _get_conn()
        rows = conn.execute("SELECT name FROM routines ORDER BY name").fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return list(BUILTIN_ROUTINES.keys())


def routine_add(name: str, steps: list[dict[str, Any]]) -> str:
    """Add a custom routine."""
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO routines (name, steps, updated_at)
            VALUES (?, ?, datetime('now'))
            """,
            (name.lower().strip(), json.dumps(steps)),
        )
        conn.commit()
        conn.close()
        return f"Routine '{name}' saved with {len(steps)} step(s)."
    except Exception as exc:
        return f"Failed to save routine: {exc}"


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

def gear0_routines_classify(query: str) -> str | None:
    """Classify query into a routine name, or None."""
    low = query.strip().lower()

    # Check built-in routines
    for name in BUILTIN_ROUTINES:
        if name in low or low in name:
            return name

    # Check custom routines
    for name in routine_list():
        if name in low:
            return name

    # Generic routine trigger
    if low.startswith("run routine ") or low.startswith("execute routine "):
        return "run_custom"

    return None


def gear0_routines_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Routines handler."""
    try:
        if handler == "run_custom":
            m = re.search(r"(?:run|execute)\s+routine\s+(.+)$", query.strip(), re.IGNORECASE)
            if m:
                name = m.group(1).strip()
                return routine_run(name, dry_run=dry_run)
        else:
            return routine_run(handler, dry_run=dry_run)
    except Exception:
        return None
    return None


__all__ = [
    "BUILTIN_ROUTINES",
    "gear0_routines_classify",
    "gear0_routines_execute",
    "routine_add",
    "routine_list",
    "routine_run",
]
