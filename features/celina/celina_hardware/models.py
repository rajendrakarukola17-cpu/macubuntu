"""Structured state & capability models for celina_hardware."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class CapabilityStatus(str, Enum):
    HEALTHY = "healthy"
    NOT_SELECTED = "not_selected"
    NOT_INSTALLED = "not_installed"
    INSTALLED_INACTIVE = "installed_but_inactive"
    UNSUPPORTED = "unsupported"
    PERMISSION_DENIED = "permission_denied"
    FAILED = "failed"


@dataclass(frozen=True)
class HardwareActionResult:
    ok: bool
    verified: bool
    message: str
    requested: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=dict)
    returncode: int = 0


@dataclass(frozen=True)
class PowerState:
    status: CapabilityStatus
    on_battery: bool = False
    battery_percent: Optional[int] = None
    charging_state: str = "unknown"
    power_profile: str = "unknown"
    detail: str = ""


@dataclass(frozen=True)
class AudioState:
    status: CapabilityStatus
    backend: str = "none"
    volume_percent: Optional[int] = None
    muted: Optional[bool] = None
    detail: str = ""


@dataclass(frozen=True)
class NetworkState:
    status: CapabilityStatus
    wifi_enabled: Optional[bool] = None
    active_ssid: str = ""
    detail: str = ""


@dataclass(frozen=True)
class BluetoothState:
    status: CapabilityStatus
    powered: Optional[bool] = None
    controller: str = ""
    detail: str = ""


@dataclass(frozen=True)
class PortalState:
    status: CapabilityStatus
    remote_desktop_portal: bool = False
    screencast_portal: bool = False
    atspi_available: bool = False
    detail: str = ""


@dataclass(frozen=True)
class ServiceState:
    unit: str
    scope: str
    status: CapabilityStatus
    detail: str = ""


@dataclass(frozen=True)
class PrivacyIndicatorState:
    status: CapabilityStatus
    mic_active: bool = False
    camera_active: bool = False
    screen_share_active: bool = False
    active_apps: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class BatteryLimitState:
    status: CapabilityStatus
    battery: str = ""
    threshold_percent: Optional[int] = None
    detail: str = ""

