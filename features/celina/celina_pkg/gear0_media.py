"""Celina AI (Celly) - Gear 0 Media (Update 19)

Local media playback control via MPRIS (Spotify, VLC, etc.) and system volume.
Zero cloud, sub-5ms.

Examples:
  - "Play music"
  - "Pause playback"
  - "Next track"
  - "Volume up"

"""

from __future__ import annotations

import shutil
import subprocess

_MEDIA_PLAY_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:play|resume|start)\s+(?:music|playback|media)\??$",
    __import__("re").IGNORECASE,
)
_MEDIA_PAUSE_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:pause|stop)\s+(?:music|playback|media)\??$",
    __import__("re").IGNORECASE,
)
_MEDIA_NEXT_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:next|skip)\s+(?:track|song)\??$",
    __import__("re").IGNORECASE,
)
_MEDIA_PREV_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:previous|prev)\s+(?:track|song)\??$",
    __import__("re").IGNORECASE,
)
_VOLUME_UP_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:volume\s+up|turn\s+up\s+the\s+volume)\??$",
    __import__("re").IGNORECASE,
)
_VOLUME_DOWN_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:volume\s+down|turn\s+down\s+the\s+volume)\??$",
    __import__("re").IGNORECASE,
)


def _mpris_command(command: str) -> str:
    """Send an MPRIS command via playerctl if available."""
    if not shutil.which("playerctl"):
        return "Media control unavailable (playerctl not found)."
    try:
        result = subprocess.run(
            ["playerctl", command], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return f"✓ Media: {command}"
        return f"Media command '{command}' failed (no player active?)."
    except Exception as exc:
        return f"Media command failed: {exc}"


def media_play() -> str:
    """Play/resume media."""
    return _mpris_command("play")


def media_pause() -> str:
    """Pause media."""
    return _mpris_command("pause")


def media_next() -> str:
    """Next track."""
    return _mpris_command("next")


def media_previous() -> str:
    """Previous track."""
    return _mpris_command("previous")


def media_volume_up() -> str:
    """Increase system volume."""
    try:
        if shutil.which("pactl"):
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%"],
                capture_output=True, text=True, timeout=5,
            )
            return "🔊 Volume up."
        if shutil.which("amixer"):
            subprocess.run(
                ["amixer", "set", "Master", "5%+"],
                capture_output=True, text=True, timeout=5,
            )
            return "🔊 Volume up."
        return "Volume control unavailable (no audio tools found)."
    except Exception as exc:
        return f"Volume up failed: {exc}"


def media_volume_down() -> str:
    """Decrease system volume."""
    try:
        if shutil.which("pactl"):
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"],
                capture_output=True, text=True, timeout=5,
            )
            return "🔉 Volume down."
        if shutil.which("amixer"):
            subprocess.run(
                ["amixer", "set", "Master", "5%-"],
                capture_output=True, text=True, timeout=5,
            )
            return "🔉 Volume down."
        return "Volume control unavailable (no audio tools found)."
    except Exception as exc:
        return f"Volume down failed: {exc}"


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

def gear0_media_classify(query: str) -> str | None:
    """Classify query into a media handler name, or None."""
    low = query.strip().lower()
    if _MEDIA_PLAY_RE.match(low):
        return "play"
    if _MEDIA_PAUSE_RE.match(low):
        return "pause"
    if _MEDIA_NEXT_RE.match(low):
        return "next"
    if _MEDIA_PREV_RE.match(low):
        return "previous"
    if _VOLUME_UP_RE.match(low):
        return "volume_up"
    if _VOLUME_DOWN_RE.match(low):
        return "volume_down"
    return None


def gear0_media_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Media handler."""
    try:
        if handler == "play":
            return media_play()
        if handler == "pause":
            return media_pause()
        if handler == "next":
            return media_next()
        if handler == "previous":
            return media_previous()
        if handler == "volume_up":
            return media_volume_up()
        if handler == "volume_down":
            return media_volume_down()
    except Exception:
        return None
    return None


__all__ = [
    "gear0_media_classify",
    "gear0_media_execute",
    "media_next",
    "media_pause",
    "media_play",
    "media_previous",
    "media_volume_down",
    "media_volume_up",
]
