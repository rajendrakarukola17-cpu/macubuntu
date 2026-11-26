"""Celina AI (Celly) - Gear 0 Hardware (Update 19)

Real-time local hardware state queries — Grok-style awareness, 100% local.

Examples:
  - "Is my VPN on?" → checks network interfaces
  * "How hot is my CPU?" → reads thermal zones
  - "Is my mic muted?" → queries PulseAudio/PipeWire

"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_VPN_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:is\s+my\s+)?vpn\s+(?:on|off|up|down|active|connected)\??$",
    __import__("re").IGNORECASE,
)
_CPU_TEMP_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:how\s+(?:hot|warm)|cpu\s+temperature|cpu\s+temp|what.*cpu\s+temp|temperature\s+check)\??$",
    __import__("re").IGNORECASE,
)
_MIC_MUTE_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:is\s+my\s+)?mic\s+(?:muted|unmuted|on|off)\??$",
    __import__("re").IGNORECASE,
)
_BATTERY_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:what'?s|what is)\s+(?:my\s+)?battery\s+(?:level|percentage|percent)\??$",
    __import__("re").IGNORECASE,
)
_LOCAL_IP_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:what'?s|what is)\s+(?:my\s+)?(?:local\s+)?ip\s+(?:address)?\??$",
    __import__("re").IGNORECASE,
)


def hardware_vpn_status() -> str:
    """Check if VPN is active."""
    try:
        result = subprocess.run(
            ["ip", "addr"], capture_output=True, text=True, timeout=5,
        )
        output = result.stdout
        vpn_interfaces = ["tun0", "tun1", "wg0", "wg1", "ppp0"]
        active = [iface for iface in vpn_interfaces if iface in output]
        if active:
            return f"✓ VPN is ON — active interface(s): {', '.join(active)}"
        return "✓ VPN is OFF — no tunnel interfaces detected."
    except Exception as exc:
        return f"Failed to check VPN: {exc}"


def hardware_cpu_temp() -> str:
    """Read CPU temperature from thermal zones."""
    try:
        thermal_dir = Path("/sys/class/thermal")
        if not thermal_dir.exists():
            return "CPU temperature not available on this system."
        best_temp = None
        best_zone = ""
        for zone in thermal_dir.iterdir():
            temp_file = zone / "temp"
            type_file = zone / "type"
            if temp_file.exists():
                try:
                    temp_millic = int(temp_file.read_text().strip())
                    temp_c = temp_millic / 1000.0
                    zone_type = type_file.read_text().strip() if type_file.exists() else zone.name
                    if best_temp is None or temp_c > best_temp:
                        best_temp = temp_c
                        best_zone = zone_type
                except (ValueError, PermissionError):
                    continue
        if best_temp is not None:
            status = "✓" if best_temp < 70 else "⚠️" if best_temp < 85 else "🚨"
            return f"{status} CPU temperature: {best_temp:.1f}°C ({best_zone})"
        return "No thermal zones found."
    except Exception as exc:
        return f"Failed to read CPU temperature: {exc}"


def hardware_mic_status() -> str:
    """Check if microphone is muted via PulseAudio/PipeWire."""
    try:
        if shutil.which("pactl"):
            result = subprocess.run(
                ["pactl", "get-source-mute", "@DEFAULT_SOURCE@"],
                capture_output=True, text=True, timeout=5,
            )
            output = result.stdout.strip()
            if "yes" in output:
                return "🔇 Mic is MUTED."
            return "🎤 Mic is UNMUTED."
        if shutil.which("arecord"):
            return "Mic status unavailable (pactl not found, arecord present)."
        return "Mic status unavailable (no audio tools found)."
    except Exception as exc:
        return f"Failed to check mic: {exc}"


def hardware_battery() -> str:
    """Read battery percentage."""
    try:
        import psutil
        batt = psutil.sensors_battery()
        if batt is None:
            return "Battery information not available (desktop system?)."
        pct = batt.percent
        plugged = batt.power_plugged
        if plugged:
            return f"🔋 Battery: {pct}% (charging)"
        secs_left = batt.secsleft
        if secs_left and secs_left > 0:
            hrs = secs_left // 3600
            mins = (secs_left % 3600) // 60
            return f"🔋 Battery: {pct}% ({hrs}h {mins}m remaining, not charging)"
        return f"🔋 Battery: {pct}% (not charging)"
    except Exception:
        return "Unable to read battery status."


def hardware_local_ip() -> str:
    """Get local IP address."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"📡 Local IP: {ip}"
    except Exception:
        try:
            return f"📡 Local IP: {__import__('socket').gethostbyname(__import__('socket').gethostname())}"
        except Exception:
            return "Unable to determine local IP."


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

def gear0_hardware_classify(query: str) -> str | None:
    """Classify query into a hardware handler name, or None."""
    low = query.strip().lower()

    if _VPN_RE.match(low):
        return "vpn"
    if _CPU_TEMP_RE.match(low):
        return "cpu_temp"
    if _MIC_MUTE_RE.match(low):
        return "mic"
    if _BATTERY_RE.match(low):
        return "battery"
    if _LOCAL_IP_RE.match(low):
        return "local_ip"

    return None


def gear0_hardware_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Hardware handler."""
    try:
        if handler == "vpn":
            return hardware_vpn_status()
        if handler == "cpu_temp":
            return hardware_cpu_temp()
        if handler == "mic":
            return hardware_mic_status()
        if handler == "battery":
            return hardware_battery()
        if handler == "local_ip":
            return hardware_local_ip()
    except Exception:
        return None
    return None


__all__ = [
    "gear0_hardware_classify",
    "gear0_hardware_execute",
    "hardware_battery",
    "hardware_cpu_temp",
    "hardware_local_ip",
    "hardware_mic_status",
    "hardware_vpn_status",
]
