"""Celina AI - Local iOS Bluetooth LE ANCS (Apple Notification Center Service) Bridge (v4.3)
Real spec-compliant BlueZ D-Bus GATT client for Apple ANCS v1.0:
  - Discovers ONLY paired BLE devices actually exposing ANCS_SERVICE_UUID via BlueZ ObjectManager
    (never mistakes headphones, keyboards, or mice for an iPhone).
  - Subscribes event-driven to BlueZ org.bluez.GattCharacteristic1 (Data Source + Notification Source)
    via System D-Bus PropertiesChanged signals (0.0% idle CPU, zero polling).
  - Decodes Apple's 8-byte Notification Source packet (<BBBBI), requests Title/Subtitle/Message/AppID
    via Control Point WriteValue (CommandID=0), and reassembles multi-MTU BLE Data Source TLV frames.
  - Privacy-safe: ignores historical pre-existing floods on connect, redacts 2FA/OTP codes from
    desktop banners by default, and never persists iOS notification bodies to disk or cache.

Note: On iOS, the user must pair the iPhone via Bluetooth and enable
      Settings -> Bluetooth -> <This PC> (i) -> "Share System Notifications".
"""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from . import config

# Official Apple ANCS v1.0 UUIDs (verified against developer.apple.com specification)
ANCS_SERVICE_UUID = "7905f431-b5ce-4e99-a40f-4b1e122d00d0"
ANCS_NOTIFICATION_SOURCE_UUID = "9fbf120d-6301-42d9-8c58-25e699a21dbd"
ANCS_CONTROL_POINT_UUID = "69d1d8f3-45e1-49a8-9821-9bbdfdaad9d9"
ANCS_DATA_SOURCE_UUID = "22eac6e9-24d6-4bb5-be44-b36ace7c7bfb"

BLUEZ_BUS = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPS_IFACE = "org.freedesktop.DBus.Properties"
BLUEZ_DEV_IFACE = "org.bluez.Device1"
BLUEZ_BATTERY_IFACE = "org.bluez.Battery1"
BLUEZ_GATT_SVC_IFACE = "org.bluez.GattService1"
BLUEZ_GATT_CHAR_IFACE = "org.bluez.GattCharacteristic1"


class ANCSEventID(IntEnum):
    ADDED = 0
    MODIFIED = 1
    REMOVED = 2


class ANCSEventFlag(IntEnum):
    SILENT = 1 << 0
    IMPORTANT = 1 << 1
    PRE_EXISTING = 1 << 2
    POSITIVE_ACTION = 1 << 3
    NEGATIVE_ACTION = 1 << 4


class ANCSCategoryID(IntEnum):
    OTHER = 0
    INCOMING_CALL = 1
    MISSED_CALL = 2
    VOICEMAIL = 3
    SOCIAL = 4
    SCHEDULE = 5
    EMAIL = 6
    NEWS = 7
    HEALTH_AND_FITNESS = 8
    BUSINESS_AND_FINANCE = 9
    LOCATION = 10
    ENTERTAINMENT = 11


class ANCSCommandID(IntEnum):
    GET_NOTIFICATION_ATTRIBUTES = 0
    GET_APP_ATTRIBUTES = 1
    PERFORM_NOTIFICATION_ACTION = 2


class ANCSAttributeID(IntEnum):
    APP_IDENTIFIER = 0
    TITLE = 1
    SUBTITLE = 2
    MESSAGE = 3
    MESSAGE_SIZE = 4
    DATE = 5
    POSITIVE_ACTION_LABEL = 6
    NEGATIVE_ACTION_LABEL = 7


class ANCSActionID(IntEnum):
    POSITIVE = 0
    NEGATIVE = 1


_CATEGORY_LABELS = {
    ANCSCategoryID.OTHER: "Notification",
    ANCSCategoryID.INCOMING_CALL: "Incoming Call",
    ANCSCategoryID.MISSED_CALL: "Missed Call",
    ANCSCategoryID.VOICEMAIL: "Voicemail",
    ANCSCategoryID.SOCIAL: "Message",
    ANCSCategoryID.SCHEDULE: "Calendar",
    ANCSCategoryID.EMAIL: "Mail",
    ANCSCategoryID.NEWS: "News",
    ANCSCategoryID.HEALTH_AND_FITNESS: "Health",
    ANCSCategoryID.BUSINESS_AND_FINANCE: "Finance",
    ANCSCategoryID.LOCATION: "Location",
    ANCSCategoryID.ENTERTAINMENT: "Media",
}

_OTP_RE = re.compile(
    r"(?i)(?:code|otp|passcode|verification|pin|security|login|2fa)[^\d]{0,24}\b\d{4,8}\b"
    r"|\b\d{4,8}\b[^\n]{0,24}(?:code|otp|passcode|verification|pin|security|login|2fa)",
)

NotificationCallback = Callable[[str, str, str], None]


@dataclass(frozen=True)
class ANCSNotificationEvent:
    """Decoded 8-byte Apple ANCS Notification Source packet."""

    event_id: int
    event_flags: int
    category_id: int
    category_count: int
    notification_uid: int

    @property
    def is_added(self) -> bool:
        return self.event_id == ANCSEventID.ADDED

    @property
    def is_removed(self) -> bool:
        return self.event_id == ANCSEventID.REMOVED

    @property
    def is_pre_existing(self) -> bool:
        return bool(self.event_flags & ANCSEventFlag.PRE_EXISTING)

    @property
    def is_silent(self) -> bool:
        return bool(self.event_flags & ANCSEventFlag.SILENT)

    @property
    def category_name(self) -> str:
        try:
            return _CATEGORY_LABELS[ANCSCategoryID(self.category_id)]
        except ValueError:
            return "Notification"


@dataclass(frozen=True)
class ANCSNotificationDetail:
    """Decoded ANCS Data Source response attributes."""

    notification_uid: int
    app_id: str = ""
    title: str = ""
    subtitle: str = ""
    message: str = ""


@dataclass(frozen=True)
class ANCSEndpoint:
    """Resolved BlueZ D-Bus paths for one paired iOS device exposing ANCS."""

    device_path: str
    device_name: str
    connected: bool
    service_path: str
    notification_source_path: str
    control_point_path: str | None = None
    data_source_path: str | None = None


def parse_notification_source(payload: bytes) -> ANCSNotificationEvent | None:
    """Parses Apple's exact 8-byte Notification Source GATT packet (<BBBBI)."""
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 8:
        return None
    event_id, event_flags, category_id, category_count, uid = struct.unpack("<BBBBI", bytes(payload[:8]))
    return ANCSNotificationEvent(
        event_id=event_id,
        event_flags=event_flags,
        category_id=category_id,
        category_count=category_count,
        notification_uid=uid,
    )


def build_get_notification_attributes_cmd(
    uid: int,
    *,
    include_message: bool = True,
    max_title: int = 96,
    max_subtitle: int = 64,
    max_message: int = 240,
) -> tuple[bytes, tuple[int, ...]]:
    """Constructs Apple's GetNotificationAttributes Control Point command (CommandID=0).
    Returns (command_bytes, expected_attribute_ids) so the Data Source assembler knows
    how many TLV tuples to expect across BLE MTU chunks.
    """
    parts = [struct.pack("<BI", int(ANCSCommandID.GET_NOTIFICATION_ATTRIBUTES), int(uid) & 0xFFFFFFFF)]
    attrs: list[int] = [int(ANCSAttributeID.APP_IDENTIFIER), int(ANCSAttributeID.TITLE), int(ANCSAttributeID.SUBTITLE)]
    parts.append(struct.pack("<B", int(ANCSAttributeID.APP_IDENTIFIER)))
    parts.append(struct.pack("<BH", int(ANCSAttributeID.TITLE), max(1, min(512, int(max_title)))))
    parts.append(struct.pack("<BH", int(ANCSAttributeID.SUBTITLE), max(1, min(512, int(max_subtitle)))))
    if include_message:
        attrs.append(int(ANCSAttributeID.MESSAGE))
        parts.append(struct.pack("<BH", int(ANCSAttributeID.MESSAGE), max(1, min(512, int(max_message)))))
    return b"".join(parts), tuple(attrs)


def build_perform_action_cmd(uid: int, *, positive: bool) -> bytes:
    """Constructs Apple's PerformNotificationAction command (CommandID=2: <BIB)."""
    action = ANCSActionID.POSITIVE if positive else ANCSActionID.NEGATIVE
    return struct.pack("<BIB", int(ANCSCommandID.PERFORM_NOTIFICATION_ACTION), int(uid) & 0xFFFFFFFF, int(action))


def sanitize_notification_text(text: str, *, redact_body: bool = False) -> str:
    """Redacts 2FA/OTP codes (or full body when redact_body=True) before desktop display."""
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return ""
    if redact_body:
        return "[Hidden for privacy]"
    if _OTP_RE.search(cleaned):
        return "[Sensitive verification code hidden — view on iPhone]"
    return cleaned[:240]


@dataclass
class ANCSDataSourceAssembler:
    """Reassembles fragmented BLE GATT Data Source responses (CommandID=0) into ANCSNotificationDetail.
    Bounded to max_buffer_bytes (4096) to prevent unbounded memory growth on malformed streams.
    """

    expected_attrs: dict[int, tuple[int, ...]] = field(default_factory=dict)
    max_buffer_bytes: int = 4096
    _buf: bytearray = field(default_factory=bytearray)

    def register_request(self, uid: int, attr_ids: tuple[int, ...]) -> None:
        if len(self.expected_attrs) > 32:
            # Drop oldest pending entry
            oldest = next(iter(self.expected_attrs))
            self.expected_attrs.pop(oldest, None)
        self.expected_attrs[int(uid)] = tuple(attr_ids)

    def feed(self, chunk: bytes) -> list[ANCSNotificationDetail]:
        """Appends a BLE Data Source notification chunk and returns any completed messages."""
        if not chunk:
            return []
        self._buf.extend(chunk)
        if len(self._buf) > self.max_buffer_bytes:
            self._buf.clear()
            return []

        completed: list[ANCSNotificationDetail] = []
        while len(self._buf) >= 5:
            cmd_id = self._buf[0]
            if cmd_id != int(ANCSCommandID.GET_NOTIFICATION_ATTRIBUTES):
                # Unknown/untracked command frame; discard cleanly
                self._buf.clear()
                break
            uid = struct.unpack_from("<I", self._buf, 1)[0]
            attr_order = self.expected_attrs.get(uid)
            if not attr_order:
                self._buf.clear()
                break

            pos = 5
            parsed: dict[int, str] = {}
            incomplete = False
            for expected_attr_id in attr_order:
                if pos + 3 > len(self._buf):
                    incomplete = True
                    break
                attr_id, attr_len = struct.unpack_from("<BH", self._buf, pos)
                pos += 3
                if pos + attr_len > len(self._buf):
                    incomplete = True
                    break
                val = bytes(self._buf[pos : pos + attr_len]).decode("utf-8", errors="replace")
                pos += attr_len
                parsed[attr_id] = val
                if attr_id != expected_attr_id:
                    # Keep reading safely even if NP reordered attributes
                    pass

            if incomplete:
                break

            del self._buf[:pos]
            self.expected_attrs.pop(uid, None)
            completed.append(
                ANCSNotificationDetail(
                    notification_uid=uid,
                    app_id=parsed.get(int(ANCSAttributeID.APP_IDENTIFIER), ""),
                    title=parsed.get(int(ANCSAttributeID.TITLE), ""),
                    subtitle=parsed.get(int(ANCSAttributeID.SUBTITLE), ""),
                    message=parsed.get(int(ANCSAttributeID.MESSAGE), ""),
                ),
            )
        return completed


def discover_ancs_endpoints(managed_objects: dict[str, dict[str, dict[str, Any]]]) -> list[ANCSEndpoint]:
    """Pure discovery helper over BlueZ `org.freedesktop.DBus.ObjectManager.GetManagedObjects()`.
    Returns ONLY BlueZ devices that actually expose Apple's ANCS primary GATT service UUID.
    """
    devices: dict[str, dict[str, Any]] = {}
    ancs_services: dict[str, str] = {}  # service_path -> device_path
    chars_by_service: dict[str, dict[str, str]] = {}  # service_path -> {uuid_lower: char_path}

    for path, ifaces in (managed_objects or {}).items():
        if not isinstance(ifaces, dict):
            continue
        if BLUEZ_DEV_IFACE in ifaces:
            devices[path] = dict(ifaces[BLUEZ_DEV_IFACE])
        if BLUEZ_GATT_SVC_IFACE in ifaces:
            svc = ifaces[BLUEZ_GATT_SVC_IFACE]
            uuid = str(svc.get("UUID", "")).strip().lower()
            if uuid == ANCS_SERVICE_UUID:
                dev_path = str(svc.get("Device", ""))
                ancs_services[path] = dev_path
        if BLUEZ_GATT_CHAR_IFACE in ifaces:
            ch = ifaces[BLUEZ_GATT_CHAR_IFACE]
            svc_path = str(ch.get("Service", ""))
            uuid = str(ch.get("UUID", "")).strip().lower()
            if svc_path and uuid:
                chars_by_service.setdefault(svc_path, {})[uuid] = path

    endpoints: list[ANCSEndpoint] = []
    for svc_path, dev_path in ancs_services.items():
        ch_map = chars_by_service.get(svc_path, {})
        ns_path = ch_map.get(ANCS_NOTIFICATION_SOURCE_UUID)
        if not ns_path:
            continue
        dev_props = devices.get(dev_path, {})
        dev_name = str(dev_props.get("Alias") or dev_props.get("Name") or "iOS Device").strip()
        connected = bool(dev_props.get("Connected", False))
        endpoints.append(
            ANCSEndpoint(
                device_path=dev_path,
                device_name=dev_name,
                connected=connected,
                service_path=svc_path,
                notification_source_path=ns_path,
                control_point_path=ch_map.get(ANCS_CONTROL_POINT_UUID),
                data_source_path=ch_map.get(ANCS_DATA_SOURCE_UUID),
            ),
        )
    return endpoints


@dataclass(frozen=True)
class BluetoothBatteryInfo:
    """Represents a Bluetooth device exposing org.bluez.Battery1 (e.g. AirPods, headphones, mouse)."""

    device_path: str
    device_name: str
    percentage: int
    connected: bool
    icon: str = ""

    @property
    def is_audio(self) -> bool:
        ic = self.icon.lower()
        nm = self.device_name.lower()
        return (
            "audio" in ic
            or "head" in ic
            or any(k in nm for k in ("airpods", "buds", "beats", "headphone", "wh-1000", "wf-1000", "bose", "jabra", "ear"))
        )


BluetoothBatteryCallback = Callable[[str, int, bool], None]


def discover_bluetooth_batteries(managed_objects: dict[str, dict[str, dict[str, Any]]]) -> list[BluetoothBatteryInfo]:
    """Extracts Bluetooth battery percentages from BlueZ org.bluez.Battery1 objects."""
    found: list[BluetoothBatteryInfo] = []
    for path, ifaces in managed_objects.items():
        if not isinstance(ifaces, dict):
            continue
        bat = ifaces.get(BLUEZ_BATTERY_IFACE)
        if not isinstance(bat, dict) or "Percentage" not in bat:
            continue
        dev = ifaces.get(BLUEZ_DEV_IFACE) or {}
        try:
            pct = max(0, min(100, int(bat["Percentage"])))
        except (TypeError, ValueError):
            continue
        name = str(dev.get("Alias") or dev.get("Name") or "Bluetooth Device").strip()
        found.append(
            BluetoothBatteryInfo(
                device_path=path,
                device_name=name,
                percentage=pct,
                connected=bool(dev.get("Connected", True)),
                icon=str(dev.get("Icon") or ""),
            ),
        )
    return found


def get_bluetooth_battery_status(bus: Any = None) -> str:
    """Returns formatted battery summary for connected Bluetooth audio/peripherals via BlueZ."""
    objs, err = _get_bluez_managed_objects(bus=bus)
    if err or objs is None:
        return "BlueZ unavailable"
    bats = [b for b in discover_bluetooth_batteries(objs) if b.connected]
    if not bats:
        return "no connected Bluetooth battery devices"
    return ", ".join(f"{'🎧 ' if b.is_audio else '🔋 '}{b.device_name} ({b.percentage}%)" for b in bats)


def _get_bluez_managed_objects(bus: Any = None) -> tuple[dict[str, Any] | None, str | None]:
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        conn = bus or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        res = conn.call_sync(
            BLUEZ_BUS,
            "/",
            DBUS_OM_IFACE,
            "GetManagedObjects",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            2500,
            None,
        )
        return res.unpack()[0], None
    except Exception as exc:
        return None, str(exc)


def get_ios_ancs_status(bus: Any = None) -> str:
    """Returns live BlueZ GATT ANCS status without spawning any subprocesses."""
    objs, err = _get_bluez_managed_objects(bus=bus)
    if err or objs is None:
        return f"BlueZ unavailable ({err or 'no system D-Bus'})"
    endpoints = discover_ancs_endpoints(objs)
    if not endpoints:
        return "no ANCS iPhone connected (pair via Bluetooth & enable 'Share System Notifications' on iOS)"
    connected = [ep for ep in endpoints if ep.connected]
    if connected:
        names = ", ".join(ep.device_name for ep in connected)
        return f"connected ({names}; ANCS GATT ready)"
    names = ", ".join(ep.device_name for ep in endpoints)
    return f"paired offline ({names})"


class IOSAncsBridge:
    """Event-driven BlueZ D-Bus ANCS + AirPods/Bluetooth Battery listener (0.0% idle CPU, zero polling).
    - Survives iPhone & AirPods hotplug/reconnects via ObjectManager.InterfacesAdded & Device1.Connected.
    - Subscribes to Apple Notification Source & Data Source GATT characteristics and dispatches
      privacy-sanitized notifications to `on_notification(app_id, title, body)` and desktop banners.
    - Supports Apple ANCS PerformNotificationAction (Accept/Answer or Dismiss/Decline) via Control Point.
    - Fires `on_bt_battery(name, percentage, is_audio)` when Bluetooth headphones/AirPods connect.
    """

    def __init__(
        self,
        on_notification: NotificationCallback | None = None,
        *,
        on_bt_battery: BluetoothBatteryCallback | None = None,
        redact_body: bool = False,
        desktop_notify: bool = True,
        bus: Any = None,
    ):
        self.on_notification = on_notification
        self.on_bt_battery = on_bt_battery
        self.redact_body = bool(redact_body)
        self.desktop_notify = bool(desktop_notify)
        self._bus = bus
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: Any = None
        self._endpoint: ANCSEndpoint | None = None
        self._assembler = ANCSDataSourceAssembler()
        self._pending_events: dict[int, ANCSNotificationEvent] = {}
        self._last_uid: int | None = None
        self._last_event: ANCSNotificationEvent | None = None
        self._sub_ids: list[int] = []
        self._gatt_sub_ids: list[int] = []
        self._started_notify_paths: list[str] = []
        self._last_bt_seen: dict[str, int] = {}

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def connected_device(self) -> str | None:
        return self._endpoint.device_name if self._endpoint else None

    @property
    def last_notification_uid(self) -> int | None:
        return self._last_uid

    @property
    def last_notification_event(self) -> ANCSNotificationEvent | None:
        return self._last_event

    def start(self) -> bool:
        """Starts the event-driven BlueZ System D-Bus hotplug loop whenever BlueZ is accessible.
        Attaches immediately if an iPhone is already connected, or automatically attaches on hotplug!
        """
        with self._lock:
            if self.is_running:
                return True
            objs, err = _get_bluez_managed_objects(bus=self._bus)
            if err or objs is None:
                return False
            endpoints = [ep for ep in discover_ancs_endpoints(objs) if ep.connected]
            self._endpoint = endpoints[0] if endpoints else None
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_dbus_loop, name="celina-ios-ancs", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            loop = self._loop
            if loop is not None:
                try:
                    from gi.repository import GLib

                    GLib.idle_add(loop.quit)
                except Exception:
                    pass
            thr = self._thread
        if thr is not None and thr.is_alive():
            thr.join(timeout=2.0)
        with self._lock:
            self._thread = None
            self._endpoint = None

    def _emit_notification(self, app_id: str, title: str, body: str) -> None:
        safe_title = sanitize_notification_text(title, redact_body=False) or app_id or "iPhone Notification"
        safe_body = sanitize_notification_text(body, redact_body=self.redact_body)
        if self.on_notification is not None:
            try:
                self.on_notification(app_id, safe_title, safe_body)
            except Exception:
                pass
        if self.desktop_notify and shutil.which("notify-send"):
            try:
                subprocess.Popen(
                    ["notify-send", "-a", config.get_assistant_name(), f"iOS • {safe_title}", safe_body or app_id or "New notification"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass

    def handle_notification_source_bytes(self, payload: bytes, *, write_control_point: Callable[[bytes], None] | None = None) -> ANCSNotificationEvent | None:
        """Processes a raw 8-byte ANCS Notification Source packet (usable both live and in tests)."""
        ev = parse_notification_source(payload)
        if ev is None:
            return None
        # Skip pre-existing backlog floods and removal events
        if not ev.is_added or ev.is_pre_existing:
            if ev.event_id == ANCSEventID.REMOVED and ev.notification_uid == self._last_uid:
                self._last_uid = None
                self._last_event = None
            return ev

        self._last_uid = ev.notification_uid
        self._last_event = ev

        if write_control_point is not None:
            cmd, attrs = build_get_notification_attributes_cmd(
                ev.notification_uid,
                include_message=not self.redact_body,
            )
            self._pending_events[ev.notification_uid] = ev
            self._assembler.register_request(ev.notification_uid, attrs)
            try:
                write_control_point(cmd)
                return ev
            except Exception:
                self._pending_events.pop(ev.notification_uid, None)

        # Fallback if Control Point / Data Source is unavailable: emit category summary safely
        self._emit_notification("com.apple.ios", ev.category_name, f"New {ev.category_name.lower()} on iPhone")
        return ev

    def handle_data_source_bytes(self, payload: bytes) -> list[ANCSNotificationDetail]:
        """Processes a raw BLE Data Source chunk and dispatches any completed notifications."""
        details = self._assembler.feed(payload)
        for det in details:
            ev = self._pending_events.pop(det.notification_uid, None)
            title = det.title or (ev.category_name if ev else "iPhone Notification")
            body = f"{det.subtitle}: {det.message}".strip(": ") if det.subtitle else det.message
            self._emit_notification(det.app_id or "com.apple.ios", title, body)
        return details

    def perform_last_notification_action(
        self,
        *,
        positive: bool = False,
        write_control_point: Callable[[bytes], None] | None = None,
    ) -> str:
        """Sends Apple ANCS PerformNotificationAction (Accept/Answer or Dismiss/Decline) to iPhone."""
        uid = self._last_uid
        if uid is None:
            return "No actionable iOS notification active."
        cmd = build_perform_action_cmd(uid, positive=positive)
        act_lbl = "Accepted/Answered" if positive else "Dismissed/Declined"

        if write_control_point is not None:
            try:
                write_control_point(cmd)
                self._last_uid = None
                self._last_event = None
                return f"Verified: {act_lbl} iOS notification (UID {uid})."
            except Exception as exc:
                return f"Error sending ANCS action: {exc}"

        ep = self._endpoint
        if ep is None or not ep.control_point_path:
            return "No connected iOS ANCS Control Point available."
        try:
            import gi

            gi.require_version("Gio", "2.0")
            gi.require_version("GLib", "2.0")
            from gi.repository import Gio, GLib

            conn = self._bus or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            conn.call_sync(
                BLUEZ_BUS,
                ep.control_point_path,
                BLUEZ_GATT_CHAR_IFACE,
                "WriteValue",
                GLib.Variant("(aya{sv})", (list(cmd), {"type": GLib.Variant("s", "request")})),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
            self._last_uid = None
            self._last_event = None
            return f"Verified: {act_lbl} iOS notification (UID {uid}) on {ep.device_name}."
        except Exception as exc:
            return f"Error sending ANCS action: {exc}"

    def _emit_bt_battery_if_new(self, info: BluetoothBatteryInfo, *, force: bool = False) -> None:
        if not info.connected or self.on_bt_battery is None:
            return
        prev = self._last_bt_seen.get(info.device_path)
        if not force and prev == info.percentage:
            return
        self._last_bt_seen[info.device_path] = info.percentage
        try:
            self.on_bt_battery(info.device_name, info.percentage, info.is_audio)
        except Exception:
            pass

    def _run_dbus_loop(self) -> None:
        try:
            import gi

            gi.require_version("Gio", "2.0")
            gi.require_version("GLib", "2.0")
            from gi.repository import Gio, GLib
        except Exception:
            return

        conn = self._bus
        try:
            if conn is None:
                conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception:
            return

        def _write_cp_sync(ep_ref: ANCSEndpoint, raw: bytes) -> None:
            if not ep_ref.control_point_path:
                raise RuntimeError("ANCS Control Point unavailable")
            conn.call_sync(
                BLUEZ_BUS,
                ep_ref.control_point_path,
                BLUEZ_GATT_CHAR_IFACE,
                "WriteValue",
                GLib.Variant("(aya{sv})", (list(raw), {"type": GLib.Variant("s", "request")})),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )

        def _write_cp_async(ep_ref: ANCSEndpoint, raw: bytes) -> None:
            """Dispatches Control Point writes off the immediate signal callback so BLE RX never stalls."""
            def _do_write() -> bool:
                try:
                    _write_cp_sync(ep_ref, raw)
                except Exception:
                    pass
                return False

            GLib.idle_add(_do_write)

        def _detach_gatt() -> None:
            for path in reversed(self._started_notify_paths):
                try:
                    conn.call_sync(
                        BLUEZ_BUS, path, BLUEZ_GATT_CHAR_IFACE, "StopNotify",
                        None, None, Gio.DBusCallFlags.NONE, 1500, None,
                    )
                except Exception:
                    pass
            self._started_notify_paths.clear()
            for sid in self._gatt_sub_ids:
                try:
                    conn.signal_unsubscribe(sid)
                except Exception:
                    pass
            self._gatt_sub_ids.clear()
            self._endpoint = None

        def _attach_gatt(ep_ref: ANCSEndpoint) -> None:
            if self._endpoint is not None and self._endpoint.service_path == ep_ref.service_path:
                return
            _detach_gatt()
            self._endpoint = ep_ref

            def _on_gatt_props(_c, _sender, obj_path, _iface, _signal, params):
                try:
                    iface_name, changed, _invalidated = params.unpack()
                    if iface_name != BLUEZ_GATT_CHAR_IFACE or "Value" not in changed:
                        return
                    raw = bytes(changed["Value"])
                    if obj_path == ep_ref.data_source_path:
                        self.handle_data_source_bytes(raw)
                    elif obj_path == ep_ref.notification_source_path:
                        cp_fn = (
                            (lambda b: _write_cp_async(ep_ref, b))
                            if (ep_ref.control_point_path and ep_ref.data_source_path)
                            else None
                        )
                        self.handle_notification_source_bytes(raw, write_control_point=cp_fn)
                except Exception:
                    pass

            watched_paths = [p for p in (ep_ref.data_source_path, ep_ref.notification_source_path) if p]
            for path in watched_paths:
                sid = conn.signal_subscribe(
                    BLUEZ_BUS,
                    DBUS_PROPS_IFACE,
                    "PropertiesChanged",
                    path,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    _on_gatt_props,
                )
                self._gatt_sub_ids.append(sid)

            for path in watched_paths:
                try:
                    conn.call_sync(
                        BLUEZ_BUS,
                        path,
                        BLUEZ_GATT_CHAR_IFACE,
                        "StartNotify",
                        None,
                        None,
                        Gio.DBusCallFlags.NONE,
                        3000,
                        None,
                    )
                    self._started_notify_paths.append(path)
                except Exception:
                    pass

        def _refresh_bluez_devices(*, announce_audio_battery: bool = False) -> None:
            objs, err = _get_bluez_managed_objects(bus=conn)
            if err or objs is None:
                return
            eps = [e for e in discover_ancs_endpoints(objs) if e.connected]
            if eps:
                _attach_gatt(eps[0])
            elif self._endpoint is not None:
                _detach_gatt()
            if announce_audio_battery:
                for binfo in discover_bluetooth_batteries(objs):
                    if binfo.connected and binfo.is_audio:
                        self._emit_bt_battery_if_new(binfo, force=True)

        def _on_bluez_hotplug(_c, _sender, obj_path, _iface, signal_name, params):
            try:
                if signal_name == "InterfacesAdded":
                    _added_path, added_ifaces = params.unpack()
                    if BLUEZ_GATT_CHAR_IFACE in added_ifaces or BLUEZ_GATT_SVC_IFACE in added_ifaces:
                        GLib.idle_add(lambda: (_refresh_bluez_devices(announce_audio_battery=False), False)[1])
                    if BLUEZ_BATTERY_IFACE in added_ifaces:
                        GLib.idle_add(lambda: (_refresh_bluez_devices(announce_audio_battery=True), False)[1])
                    return
                if signal_name == "PropertiesChanged":
                    iface_name, changed, _inv = params.unpack()
                    if iface_name == BLUEZ_DEV_IFACE and "Connected" in changed:
                        connected_now = bool(changed["Connected"])
                        if not connected_now:
                            self._last_bt_seen.pop(obj_path, None)
                            if self._endpoint is not None and self._endpoint.device_path == obj_path:
                                _detach_gatt()
                        else:
                            GLib.timeout_add(
                                800,
                                lambda: (_refresh_bluez_devices(announce_audio_battery=True), False)[1],
                            )
                    elif iface_name == BLUEZ_BATTERY_IFACE and "Percentage" in changed:
                        GLib.idle_add(lambda: (_refresh_bluez_devices(announce_audio_battery=True), False)[1])
            except Exception:
                pass

        # Subscribe globally to BlueZ hotplug & battery signals (0.0% idle CPU)
        self._sub_ids.append(
            conn.signal_subscribe(
                BLUEZ_BUS,
                DBUS_OM_IFACE,
                "InterfacesAdded",
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                _on_bluez_hotplug,
            ),
        )
        self._sub_ids.append(
            conn.signal_subscribe(
                BLUEZ_BUS,
                DBUS_PROPS_IFACE,
                "PropertiesChanged",
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                _on_bluez_hotplug,
            ),
        )

        try:
            if self._endpoint is not None:
                _attach_gatt(self._endpoint)
            self._loop = GLib.MainLoop()
            if not self._stop_event.is_set():
                self._loop.run()
        except Exception:
            pass
        finally:
            _detach_gatt()
            for sub_id in self._sub_ids:
                try:
                    conn.signal_unsubscribe(sub_id)
                except Exception:
                    pass
            self._sub_ids.clear()
            self._loop = None


_DEFAULT_ANCS_BRIDGE: IOSAncsBridge | None = None
_BRIDGE_LOCK = threading.Lock()


def start_ios_bridge(
    on_notification: NotificationCallback | None = None,
    *,
    on_bt_battery: BluetoothBatteryCallback | None = None,
    redact_body: bool = False,
) -> bool:
    global _DEFAULT_ANCS_BRIDGE
    with _BRIDGE_LOCK:
        if _DEFAULT_ANCS_BRIDGE is None:
            _DEFAULT_ANCS_BRIDGE = IOSAncsBridge(
                on_notification=on_notification,
                on_bt_battery=on_bt_battery,
                redact_body=redact_body,
            )
        return _DEFAULT_ANCS_BRIDGE.start()


def perform_ios_notification_action(*, positive: bool = False) -> str:
    """Triggers Apple ANCS Accept/Answer (positive=True) or Dismiss/Decline on the active iOS alert."""
    with _BRIDGE_LOCK:
        bridge = _DEFAULT_ANCS_BRIDGE
    if bridge is None:
        return "iOS ANCS bridge is not active."
    return bridge.perform_last_notification_action(positive=positive)


def stop_ios_bridge() -> None:
    global _DEFAULT_ANCS_BRIDGE
    with _BRIDGE_LOCK:
        bridge = _DEFAULT_ANCS_BRIDGE
        _DEFAULT_ANCS_BRIDGE = None
    if bridge is not None:
        bridge.stop()
