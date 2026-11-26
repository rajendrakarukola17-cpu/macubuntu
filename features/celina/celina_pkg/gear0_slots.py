"""
Celina AI (Celly) - Gear 0 Slots / Deterministic Slot Filling (Update 19)

Siri-style deterministic slot filling — extracts structured parameters from
natural language using regex patterns. Zero LLM, zero AI RAM, sub-1ms.
Examples:
  - "Set a timer for 10 minutes" → {timer: 10, unit: minutes}
  - "Remind me to call mom at 5pm" → {task: call mom, time: 5pm}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Pattern


class Slot:
    """A single named slot with a regex pattern and optional type converter."""

    def __init__(
        self,
        name: str,
        pattern: str,
        converter: Any = str,
        required: bool = True,
        flags: int = re.IGNORECASE,
    ):
        self.name = name
        self.pattern = re.compile(pattern, flags)
        self.converter = converter
        self.required = required


class SlotFiller:
    """Compiles a slot-filling schema and fills slots from user input."""

    def __init__(self, slots: List[Slot], overall_pattern: Optional[str] = None):
        self.slots = slots
        self.overall_pattern = re.compile(overall_pattern, re.IGNORECASE) if overall_pattern else None

    def fill(self, query: str) -> Optional[Dict[str, Any]]:
        """Extract slots from query. Returns dict of slot_name → value, or None if required slots missing."""
        if self.overall_pattern and not self.overall_pattern.search(query):
            return None

        result: Dict[str, Any] = {}
        for slot in self.slots:
            m = slot.pattern.search(query)
            if m:
                try:
                    result[slot.name] = slot.converter(m.group(1))
                except (IndexError, ValueError):
                    if slot.required:
                        return None
            elif slot.required:
                return None
        return result


# ---------------------------------------------------------------------------
# Pre-defined slot schemas for common Gear 0 patterns
# ---------------------------------------------------------------------------

_TIMER_SLOTS = [
    Slot("minutes", r"(\d+)\s*(?:min|minute|minutes)", converter=int),
]
_TIMER_OVERALL = r"(?:set|start|create)\s+(?:a\s+)?timer"

_REMINDER_SLOTS = [
    Slot("task", r"(?:remind\s+me\s+to\s+)(.+?)(?:\s+at\s+|$)", converter=str),
    Slot("time", r"\bat\s+(.+)", converter=str, required=False),
]
_REMINDER_OVERALL = r"remind\s+me\s+to"

_ALARM_SLOTS = [
    Slot("time", r"(?:set\s+an\s+alarm\s+for\s+|alarm\s+at\s+)(.+)", converter=str),
]
_ALARM_OVERALL = r"(?:set\s+an\s+alarm|alarm)"


def fill_timer(query: str) -> Optional[Dict[str, Any]]:
    """Extract timer slots: 'set a timer for 10 minutes' → {minutes: 10}."""
    return SlotFiller(_TIMER_SLOTS, _TIMER_OVERALL).fill(query)


def fill_reminder(query: str) -> Optional[Dict[str, Any]]:
    """Extract reminder slots: 'remind me to call mom at 5pm' → {task: 'call mom', time: '5pm'}."""
    return SlotFiller(_REMINDER_SLOTS, _REMINDER_OVERALL).fill(query)


def fill_alarm(query: str) -> Optional[Dict[str, Any]]:
    """Extract alarm slots: 'set an alarm for 7am' → {time: '7am'}."""
    return SlotFiller(_ALARM_SLOTS, _ALARM_OVERALL).fill(query)


__all__ = [
    "Slot",
    "SlotFiller",
    "fill_timer",
    "fill_reminder",
    "fill_alarm",
]
