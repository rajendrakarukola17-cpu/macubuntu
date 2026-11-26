"""
Celina AI (Celly) - Gear 0 Calendar (Update 19)

Local calendar parsing — reads .ics files, calcurse, khal, or system calendar.
Zero LLM tokens, sub-5ms, 100% local.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

_MEETING_RE = re.compile(
    r"^(?:please\s+)?(?:what'?s|what is)\s+(?:my\s+)?(?:next\s+)?(?:meeting|appointment|event)\??$",
    re.IGNORECASE,
)
_CALENDAR_TODAY_RE = re.compile(
    r"^(?:please\s+)?(?:what'?s|what is)\s+(?:on\s+)?(?:my\s+)?(?:calendar|schedule)\s+(?:for\s+)?(today|tomorrow)\??$",
    re.IGNORECASE,
)


def calendar_next_event() -> str:
    """Get the next calendar event from local sources."""
    try:
        # Try calcurse
        if shutil.which("calcurse"):
            result = subprocess.run(
                ["calcurse", "-n"], capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                return result.stdout.strip()[:200]

        # Try khal
        if shutil.which("khal"):
            result = subprocess.run(
                ["khal", "list", "now", "today"], capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                return result.stdout.strip()[:200]

        # Try parsing .ics files
        ical_dirs = [
            Path.home() / ".local" / "share" / "evolution" / "calendar",
            Path.home() / ".cache" / "evolution" / "calendar",
            Path.home() / ".config" / "calendar",
        ]
        for d in ical_dirs:
            if d.exists():
                for f in d.glob("*.ics"):
                    content = f.read_text(errors="ignore")
                    events = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", content, re.DOTALL)
                    if events:
                        # Extract the first upcoming event
                        for evt in events[:3]:
                            summary_match = re.search(r"SUMMARY:(.+)", evt)
                            dtstart_match = re.search(r"DTSTART;.*?:(\d{8}T\d{6})", evt)
                            if summary_match:
                                time_str = ""
                                if dtstart_match:
                                    try:
                                        dt = datetime.strptime(dtstart_match.group(1), "%Y%m%dT%H%M%S")
                                        time_str = f" at {dt.strftime('%I:%M %p')}"
                                    except Exception:
                                        pass
                                return f"{summary_match.group(1)}{time_str}"
                        return f"Found {len(events)} calendar event(s)."

        return "No upcoming calendar events found."
    except Exception as exc:
        return f"Calendar unavailable: {exc}"


def calendar_today(day: str = "today") -> str:
    """Get today's or tomorrow's calendar schedule."""
    try:
        if shutil.which("khal"):
            target = "today" if day == "today" else "tomorrow"
            result = subprocess.run(
                ["khal", "list", "now", target], capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                return result.stdout.strip()[:300]

        if shutil.which("calcurse"):
            result = subprocess.run(
                ["calcurse", "-s", "3"], capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                return result.stdout.strip()[:300]

        return f"No {day} calendar events found."
    except Exception as exc:
        return f"Calendar unavailable: {exc}"


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

def gear0_calendar_classify(query: str) -> Optional[str]:
    """Classify query into a calendar handler name, or None."""
    low = query.strip().lower()

    if _MEETING_RE.match(low):
        return "next_event"
    if _CALENDAR_TODAY_RE.match(low):
        return "today_schedule"

    return None


def gear0_calendar_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0-Calendar handler."""
    try:
        if handler == "next_event":
            return calendar_next_event()
        if handler == "today_schedule":
            m = re.search(r"(today|tomorrow)", query.lower())
            day = m.group(1) if m else "today"
            return calendar_today(day)
    except Exception:
        return None
    return None


__all__ = [
    "calendar_next_event",
    "calendar_today",
    "gear0_calendar_classify",
    "gear0_calendar_execute",
]
