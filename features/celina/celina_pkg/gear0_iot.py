"""
Celina AI (Celly) - Gear 0 IoT / Local Triggers (Update 19)

Local IoT/context trigger evaluation. Zero cloud, sub-5ms.
Examples:
  - "Turn on the living room lights" → local MQTT/HTTP command
  - "Set thermostat to 72 degrees" → local device control
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

_LIGHTS_RE = __import__("re").compile(
    r"^(?:turn\s+on|switch\s+on|activate)\s+(?:the\s+)?(\w+)\s+lights\??$",
    __import__("re").IGNORECASE,
)
_THERMOSTAT_RE = __import__("re").compile(
    r"^(?:set|adjust)\s+(?:the\s+)?thermostat\s+to\s+(\d+)\s*(?:degrees|deg)\??$",
    __import__("re").IGNORECASE,
)

# In-memory local device registry (loaded from gear0_iot_devices.json if present)
_DEVICES: Dict[str, Dict[str, Any]] = {}


def iot_load_devices(config_path: Path | str | None = None) -> None:
    """Load local IoT device registry from JSON config."""
    global _DEVICES
    if config_path is None:
        config_path = Path.home() / ".config" / "celina" / "gear0_iot_devices.json"
    try:
        p = Path(config_path)
        if p.exists():
            _DEVICES = json.loads(p.read_text())
    except Exception:
        _DEVICES = {}


def iot_device_list() -> List[Dict[str, Any]]:
    """Return list of registered local IoT devices."""
    return list(_DEVICES.values())


def iot_command(device: str, action: str, value: Any = None) -> str:
    """Send a command to a local IoT device via MQTT or HTTP."""
    dev = _DEVICES.get(device)
    if not dev:
        return f"Unknown IoT device: {device}"

    endpoint = dev.get("endpoint")
    protocol = dev.get("protocol", "mqtt")

    try:
        if protocol == "mqtt":
            if shutil.which("mosquitto_pub"):
                topic = dev.get("topic", f"celina/iot/{device}")
                payload = json.dumps({"action": action, "value": value})
                subprocess.run(
                    ["mosquitto_pub", "-t", topic, "-m", payload],
                    capture_output=True, text=True, timeout=5,
                )
                return f"✓ MQTT command sent to {device}: {action}"
            return f"MQTT unavailable (mosquitto_pub not found) for {device}."
        if protocol == "http":
            import urllib.request
            url = f"http://{endpoint}/{action}"
            if value is not None:
                url += f"?value={value}"
            urllib.request.urlopen(url, timeout=3)
            return f"✓ HTTP command sent to {device}: {action}"
        return f"Unsupported protocol: {protocol}"
    except Exception as exc:
        return f"Failed to send command to {device}: {exc}"


def iot_lights_on(room: str) -> str:
    """Turn on lights in a room."""
    device = f"{room}_lights"
    return iot_command(device, "on", True)


def iot_thermostat_set(temp: int) -> str:
    """Set thermostat temperature."""
    return iot_command("thermostat", "set_temperature", temp)


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

def gear0_iot_classify(query: str) -> Optional[str]:
    """Classify query into an IoT handler name, or None."""
    low = query.strip().lower()
    m = _LIGHTS_RE.match(low)
    if m:
        return f"lights_on:{m.group(1)}"
    m = _THERMOSTAT_RE.match(low)
    if m:
        return f"thermostat_set:{m.group(1)}"
    return None


def gear0_iot_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0-IoT handler."""
    try:
        if handler.startswith("lights_on:"):
            room = handler.split(":", 1)[1]
            return iot_lights_on(room)
        if handler.startswith("thermostat_set:"):
            temp = int(handler.split(":", 1)[1])
            return iot_thermostat_set(temp)
    except Exception:
        return None
    return None


__all__ = [
    "iot_load_devices",
    "iot_device_list",
    "iot_command",
    "iot_lights_on",
    "iot_thermostat_set",
    "gear0_iot_classify",
    "gear0_iot_execute",
]
