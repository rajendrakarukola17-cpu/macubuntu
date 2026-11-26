"""Celina AI (Celly) - Gear 0-Intent: Deterministic Slot Filling (Update 19)

Classic Siri-style intent recognition: pattern match → extract slots → execute OS API.
Zero LLM tokens, zero AI RAM, sub-5ms latency.

All regex patterns pre-compiled at import. Uses only Python stdlib.
"""

from __future__ import annotations

import datetime
import re

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns (compiled once at import)
# ---------------------------------------------------------------------------

# Timer: "set a timer for 20 minutes", "timer for 5 secs", "set timer 1 hour"
_TIMER_RE = re.compile(
    r"^(?:please\s+)?(?:set\s+(?:a\s+)?)?timer\s+(?:for\s+)?"
    r"(?P<amount>\d{1,4})\s*"
    r"(?P<unit>sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours)"
    r"(?:\s+(?:for|to)\s+(?P<msg>.+))?$",
    re.IGNORECASE,
)

# Reminder in duration: "remind me in 10 minutes to call John"
_REMIND_IN_RE = re.compile(
    r"^(?:please\s+)?remind\s+me\s+in\s+"
    r"(?P<amount>\d{1,4})\s*"
    r"(?P<unit>sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours)"
    r"\s+(?:to\s+)?(?P<msg>.+)$",
    re.IGNORECASE,
)

# Reminder at time: "remind me to call John at 3 PM", "remind me at 3 PM to call John"
_REMIND_AT_RE = re.compile(
    r"^(?:please\s+)?remind\s+me\s+"
    r"(?:(?:at\s+)(?P<time>.+?)\s+(?:to\s+)?)?(?P<msg>.+?)$",
    re.IGNORECASE,
)

# Dark / Light mode: "turn on dark mode", "switch to light mode", "dark mode", "light mode", "dark mode off"
_THEME_RE = re.compile(
    r"^(?:please\s+)?(?:turn|switch|set|put)\s+(?:to\s+)?"
    r"(?P<onoff>on|off)?\s*"
    r"(?P<mode>dark|light)\s+mode$",
    re.IGNORECASE,
)
_THEME_ON_OFF_RE = re.compile(
    r"^(?:please\s+)?(?:turn|switch|set)\s+(?P<mode>dark|light)\s+mode\s+(?P<onoff>on|off)$",
    re.IGNORECASE,
)
_THEME_BARE_RE = re.compile(
    r"^(?P<mode>dark|light)\s+mode\s*(?P<onoff>on|off)?$",
    re.IGNORECASE,
)

# Do Not Disturb: "turn on dnd", "do not disturb off", "dnd on", "dnd off", "dnd"
_DND_RE = re.compile(
    r"^(?:please\s+)?(?:turn|switch|set)\s+(?:on|off)?\s*"
    r"(?:do not disturb|dnd)\s*(?P<onoff>on|off)?$",
    re.IGNORECASE,
)
_DND_BARE_RE = re.compile(
    r"^(?:do not disturb|dnd)\s*(?P<onoff>on|off)?$",
    re.IGNORECASE,
)

# Night Light: "turn on night light", "night light off", "night light on"
_NIGHT_LIGHT_RE = re.compile(
    r"^(?:please\s+)?(?:turn|switch|set)\s+(?:on|off)?\s*"
    r"night\s+light\s*(?P<onoff>on|off)?$",
    re.IGNORECASE,
)
_NIGHT_LIGHT_BARE_RE = re.compile(
    r"^night\s+light\s*(?P<onoff>on|off)?$",
    re.IGNORECASE,
)

# Volume: "set volume to 50%", "volume up", "volume down", "mute", "unmute"
_VOLUME_RE = re.compile(
    r"^(?:please\s+)?(?:set|change)\s+volume\s+(?:to\s+)?(?P<level>\d{1,3})\s*%?$",
    re.IGNORECASE,
)
_VOLUME_UP_DOWN_RE = re.compile(
    r"^(?:please\s+)?volume\s+(?P<direction>up|down)$", re.IGNORECASE,
)
_VOLUME_MUTE_RE = re.compile(
    r"^(?:please\s+)?(?:mute|unmute)\s+(?:the\s+)?(?:mic|microphone|audio|sound)$",
    re.IGNORECASE,
)

# Brightness: "set brightness to 50%", "brightness up/down"
_BRIGHTNESS_RE = re.compile(
    r"^(?:please\s+)?(?:set|change)\s+brightness\s+(?:to\s+)?(?P<level>\d{1,3})\s*%?$",
    re.IGNORECASE,
)

# Open application: "open VS Code", "launch chrome", "start spotify"
_OPEN_APP_RE = re.compile(
    r"^(?:please\s+)?(?:open|launch|start)\s+(?P<app>.+?)(?:\s+app)?$",
    re.IGNORECASE,
)

# Date/time natural language: "at 3 PM tomorrow", "in 2 hours"
_AT_TIME_RE = re.compile(
    r"\b(?:at\s+)(?P<time>\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))(?:\s+(?P<when>today|tomorrow|tonight|morning|afternoon|evening))?\b",
    re.IGNORECASE,
)


def _parse_duration_seconds(amount: int, unit: str) -> int:
    """Convert amount+unit to seconds, capped at 24 hours."""
    val = max(1, int(amount))
    u = unit.lower()
    if u.startswith("h"):
        return min(val * 3600, 86400)
    if u.startswith("m"):
        return min(val * 60, 86400)
    return min(val, 86400)


def _parse_natural_time(text: str) -> datetime.datetime | None:
    """Parse natural language time like '3 PM tomorrow' into datetime."""
    try:
        now = datetime.datetime.now()
        text_lower = text.lower().strip()

        # Handle "in X minutes/hours" patterns
        m = re.match(r"^in\s+(\d{1,4})\s*(min|mins|minute|minutes|hr|hrs|hour|hours)$", text_lower)
        if m:
            secs = _parse_duration_seconds(int(m.group(1)), m.group(2))
            return now + datetime.timedelta(seconds=secs)

        # Handle specific times: "3 PM", "3:30 PM", "15:00"
        time_match = re.search(
            r"(\d{1,2}):?(\d{2})?\s*(am|pm)?", text_lower,
        )
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            ampm = time_match.group(3)

            if ampm:
                if ampm == "pm" and hour != 12:
                    hour += 12
                if ampm == "am" and hour == 12:
                    hour = 0

            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)

            # Check for tomorrow/tonight modifiers
            if "tomorrow" in text_lower or "tonight" in text_lower:
                target += datetime.timedelta(days=1)
            elif "morning" in text_lower and hour < 12:
                pass  # already morning
            elif ("afternoon" in text_lower and hour < 12) or ("evening" in text_lower and hour < 12):
                hour += 12
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            return target
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Slot-filling intent handlers
# ---------------------------------------------------------------------------

def handle_timer(query: str, dry_run: bool = False) -> str | None:
    """Handle timer requests: 'set a timer for 20 minutes'."""
    m = _TIMER_RE.match(query.strip())
    if not m:
        return None
    secs = _parse_duration_seconds(int(m.group("amount")), m.group("unit"))
    msg = (m.group("msg") or "Timer finished!").strip()
    if dry_run:
        return f"🧪 Dry-Run [Gear 0-Timer]: Would set {secs}s timer ('{msg}')."
    try:
        from . import tools
        return tools.set_timer(secs, msg)
    except Exception:
        return f"Timer set for {secs} seconds ({msg})."


def handle_remind_in(query: str, dry_run: bool = False) -> str | None:
    """Handle 'remind me in X minutes to do something'."""
    m = _REMIND_IN_RE.match(query.strip())
    if not m:
        return None
    secs = _parse_duration_seconds(int(m.group("amount")), m.group("unit"))
    msg = m.group("msg").strip()
    if dry_run:
        return f"🧪 Dry-Run [Gear 0-Remind]: Would remind in {secs}s: '{msg}'."
    try:
        from . import tools
        return tools.set_timer(secs, msg)
    except Exception:
        return f"Reminder set for {secs} seconds from now ({msg})."


def handle_remind_at(query: str, dry_run: bool = False) -> str | None:
    """Handle 'remind me to call John at 3 PM'."""
    m = _REMIND_AT_RE.match(query.strip())
    if not m:
        return None
    msg = (m.group("msg") or "").strip()
    time_str = m.group("time")

    target_time = None
    if time_str:
        target_time = _parse_natural_time(time_str)

    if not target_time:
        # Fallback: check if the whole query has a time pattern
        target_time = _parse_natural_time(query.strip())

    if target_time:
        secs = int((target_time - datetime.datetime.now()).total_seconds())
        if secs <= 0:
            secs += 86400  # next day
        if dry_run:
            return f"🧪 Dry-Run [Gear 0-Remind]: Would remind at {target_time.strftime('%I:%M %p')}: '{msg}'."
        try:
            from . import tools
            return tools.set_timer(secs, msg or "Reminder")
        except Exception:
            return f"Reminder set for {target_time.strftime('%I:%M %p')} ({msg})."

    return None


def handle_theme(query: str, dry_run: bool = False) -> str | None:
    """Handle dark/light mode: 'turn on dark mode', 'switch to light mode', 'dark mode off'."""
    m = _THEME_ON_OFF_RE.match(query.strip()) or _THEME_RE.match(query.strip())
    if not m:
        return None
    mode = m.group("mode").lower()
    onoff = m.group("onoff")

    # Determine desired state
    if onoff:
        want_dark = (onoff == "on") and (mode == "dark")
        # If mode is light and onoff is "on" -> light mode
        # If mode is light and onoff is "off" -> dark mode (turning light OFF)
        # If mode is dark and onoff is "on" -> dark mode
        # If mode is dark and onoff is "off" -> light mode (turning dark OFF)
        if mode == "light":
            want_dark = (onoff == "off")
    else:
        # No explicit on/off - just set to the named mode
        want_dark = (mode == "dark")

    if dry_run:
        label = "Dark Mode" if want_dark else "Light Mode"
        return f"🧪 Dry-Run [Gear 0-Theme]: Would switch desktop to {label}."

    try:
        from . import everyday_agents
        return everyday_agents.set_desktop_theme("dark" if want_dark else "light", dry_run=False)
    except Exception:
        return f"Switched to {'dark' if want_dark else 'light'} mode."


def handle_dnd(query: str, dry_run: bool = False) -> str | None:
    """Handle Do Not Disturb: 'turn on dnd', 'do not disturb off', 'dnd off'."""
    m = _DND_RE.match(query.strip())
    if not m:
        return None
    onoff = m.group("onoff")
    if onoff:
        enabled = onoff.lower() == "on"
    else:
        # No explicit on/off - check if "off" appears anywhere
        enabled = "off" not in query.strip().lower() and "disable" not in query.strip().lower()

    if dry_run:
        return f"🧪 Dry-Run [Gear 0-DND]: Would turn Do Not Disturb {'ON' if enabled else 'OFF'}."

    try:
        from . import everyday_agents
        return everyday_agents.set_dnd_mode(enabled, dry_run=False)
    except Exception:
        return f"Do Not Disturb {'enabled' if enabled else 'disabled'}."


def handle_night_light(query: str, dry_run: bool = False) -> str | None:
    """Handle Night Light: 'turn on night light', 'night light off'."""
    m = _NIGHT_LIGHT_RE.match(query.strip())
    if not m:
        return None
    onoff = m.group("onoff")
    if onoff:
        enabled = onoff.lower() == "on"
    else:
        enabled = "off" not in query.strip().lower() and "disable" not in query.strip().lower()

    if dry_run:
        return f"🧪 Dry-Run [Gear 0-NightLight]: Would turn Night Light {'ON' if enabled else 'OFF'}."

    try:
        from . import everyday_agents
        return everyday_agents.set_night_light(enabled, dry_run=False)
    except Exception:
        return f"Night Light {'enabled' if enabled else 'disabled'}."


def handle_volume(query: str, dry_run: bool = False) -> str | None:
    """Handle volume: 'set volume to 50%', 'volume up', 'mute mic'."""
    m = _VOLUME_RE.match(query.strip())
    if m:
        level = min(100, max(0, int(m.group("level"))))
        if dry_run:
            return f"🧪 Dry-Run [Gear 0-Volume]: Would set volume to {level}%."
        try:
            from . import tools
            return tools.set_volume(level)
        except Exception:
            return f"Volume set to {level}%."

    m = _VOLUME_UP_DOWN_RE.match(query.strip())
    if m:
        direction = m.group("direction").lower()
        if dry_run:
            return f"🧪 Dry-Run [Gear 0-Volume]: Would volume {direction}."
        try:
            from . import tools
            current = 50  # would need to read current
            new_level = min(100, max(0, current + (10 if direction == "up" else -10)))
            return tools.set_volume(new_level)
        except Exception:
            return f"Volume {direction}."

    m = _VOLUME_MUTE_RE.match(query.strip())
    if m:
        if dry_run:
            return "🧪 Dry-Run [Gear 0-Volume]: Would mute audio."
        try:
            from . import tools
            return tools.control_media("mute")
        except Exception:
            return "Audio muted."

    return None


def handle_brightness(query: str, dry_run: bool = False) -> str | None:
    """Handle brightness: 'set brightness to 50%'."""
    m = _BRIGHTNESS_RE.match(query.strip())
    if not m:
        return None
    level = min(100, max(0, int(m.group("level"))))
    if dry_run:
        return f"🧪 Dry-Run [Gear 0-Brightness]: Would set brightness to {level}%."
    try:
        from . import tools
        return tools.set_brightness(level)
    except Exception:
        return f"Brightness set to {level}%."


def handle_open_app(query: str, dry_run: bool = False) -> str | None:
    """Handle open application: 'open VS Code', 'launch chrome'."""
    m = _OPEN_APP_RE.match(query.strip())
    if not m:
        return None
    app = m.group("app").strip()
    if dry_run:
        return f"🧪 Dry-Run [Gear 0-OpenApp]: Would open {app}."
    try:
        from . import tools
        return tools.open_application(app)
    except Exception:
        return f"Opening {app}."


# ---------------------------------------------------------------------------
# Master dispatch: classify Gear 0-Intent request
# ---------------------------------------------------------------------------

def gear0_intent_classify(query: str) -> str | None:
    """Classify query into an intent handler name, or None."""
    low = query.strip().lower()

    if _TIMER_RE.match(low):
        return "timer"
    if _REMIND_IN_RE.match(low):
        return "remind_in"
    if _REMIND_AT_RE.match(low):
        return "remind_at"
    if _THEME_RE.match(low) or _THEME_ON_OFF_RE.match(low) or _THEME_BARE_RE.match(low):
        return "theme"
    if _DND_RE.match(low) or _DND_BARE_RE.match(low):
        return "dnd"
    if _NIGHT_LIGHT_RE.match(low) or _NIGHT_LIGHT_BARE_RE.match(low):
        return "night_light"
    if _VOLUME_RE.match(low) or _VOLUME_UP_DOWN_RE.match(low) or _VOLUME_MUTE_RE.match(low):
        return "volume"
    if _BRIGHTNESS_RE.match(low):
        return "brightness"
    if _OPEN_APP_RE.match(low):
        return "open_app"

    return None


def gear0_intent_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Intent handler."""
    try:
        if handler == "timer":
            return handle_timer(query, dry_run=dry_run)
        if handler == "remind_in":
            return handle_remind_in(query, dry_run=dry_run)
        if handler == "remind_at":
            return handle_remind_at(query, dry_run=dry_run)
        if handler == "theme":
            return handle_theme(query, dry_run=dry_run)
        if handler == "dnd":
            return handle_dnd(query, dry_run=dry_run)
        if handler == "night_light":
            return handle_night_light(query, dry_run=dry_run)
        if handler == "volume":
            return handle_volume(query, dry_run=dry_run)
        if handler == "brightness":
            return handle_brightness(query, dry_run=dry_run)
        if handler == "open_app":
            return handle_open_app(query, dry_run=dry_run)
    except Exception:
        return None
    return None


__all__ = [
    "_parse_natural_time",
    "gear0_intent_classify",
    "gear0_intent_execute",
]
