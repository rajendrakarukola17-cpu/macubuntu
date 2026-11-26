"""Celina AI — Native GNOME xdg-desktop-portal RemoteDesktop Client (Tier-1 Wayland Input)
Eliminates the need for `input` group membership or unrestricted `/dev/uinput` (`ydotool`)
on GNOME Wayland by speaking the official `org.freedesktop.portal.RemoteDesktop` D-Bus API.

Security Invariants:
  - Runs strictly inside the user's own Wayland D-Bus session (user-consented GNOME dialog).
  - Never touches /dev/uinput or requires privileged groups.
  - Session handle is explicitly closed on shutdown or emergency stop (`Esc`).
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import Any

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_RD_IFACE = "org.freedesktop.portal.RemoteDesktop"
PORTAL_SC_IFACE = "org.freedesktop.portal.ScreenCast"
PORTAL_REQ_IFACE = "org.freedesktop.portal.Request"
PORTAL_SESSION_IFACE = "org.freedesktop.portal.Session"

# Linux evdev button codes used by xdg-desktop-portal RemoteDesktop NotifyPointerButton
BTN_LEFT = 272
BTN_RIGHT = 273
BTN_MIDDLE = 274

_BUTTON_MAP = {
    "left": BTN_LEFT,
    "right": BTN_RIGHT,
    "middle": BTN_MIDDLE,
}


@dataclass(frozen=True)
class PortalActionResult:
    ok: bool
    message: str
    portal_active: bool = False


class RemoteDesktopPortalClient:
    """Lazy, user-consented GNOME RemoteDesktop Portal session manager.
    Only activates when an approved Wayland input action requests it and GNOME's
    portal service is available.
    """

    def __init__(self, bus: Any = None):
        self._bus = bus
        self._session_handle: str | None = None
        self._stream_node_id: int = 0
        self._lock = threading.Lock()

    @property
    def has_active_session(self) -> bool:
        return bool(self._session_handle)

    def _get_bus(self) -> Any:
        if self._bus is not None:
            return self._bus
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            return self._bus
        except Exception:
            return None

    def is_available(self) -> bool:
        bus = self._get_bus()
        if bus is None:
            return False
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            res = bus.call_sync(
                PORTAL_BUS,
                PORTAL_PATH,
                "org.freedesktop.DBus.Properties",
                "Get",
                gi.repository.GLib.Variant("(ss)", (PORTAL_RD_IFACE, "AvailableDeviceTypes")),
                None,
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
            return res is not None
        except Exception:
            return False

    def _call_portal_request(
        self,
        method: str,
        params_variant: Any,
        timeout_ms: int = 8000,
        iface: str = PORTAL_RD_IFACE,
    ) -> tuple[int, dict[str, Any]]:
        """Invokes a portal method returning an org.freedesktop.portal.Request path and awaits its Response signal."""
        bus = self._get_bus()
        if bus is None:
            return 2, {}
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        done = threading.Event()
        result_box: dict[str, Any] = {"code": 2, "results": {}}
        loop = GLib.MainLoop()

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            code, res_dict = params.unpack()
            result_box["code"] = int(code)
            result_box["results"] = dict(res_dict or {})
            done.set()
            if loop.is_running():
                loop.quit()

        ret = bus.call_sync(
            PORTAL_BUS,
            PORTAL_PATH,
            iface,
            method,
            params_variant,
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        )
        req_path = ret.unpack()[0]
        sub_id = bus.signal_subscribe(
            PORTAL_BUS,
            PORTAL_REQ_IFACE,
            "Response",
            req_path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )

        def on_timeout():
            if loop.is_running():
                loop.quit()
            return False

        timer_id = GLib.timeout_add(timeout_ms, on_timeout)
        try:
            loop.run()
        finally:
            GLib.source_remove(timer_id)
            bus.signal_unsubscribe(sub_id)
        return result_box["code"], result_box["results"]

    def ensure_session(self) -> tuple[bool, str]:
        """Negotiates a user-consented GNOME RemoteDesktop session (keyboard + pointer)."""
        with self._lock:
            if self._session_handle:
                return True, self._session_handle
            bus = self._get_bus()
            if bus is None:
                return False, "Session D-Bus unavailable."
            try:
                from gi.repository import GLib

                tok1 = f"celina_cs_{secrets.token_hex(6)}"
                ses_tok = f"celina_ses_{secrets.token_hex(6)}"
                code, res = self._call_portal_request(
                    "CreateSession",
                    GLib.Variant(
                        "(a{sv})",
                        ({
                            "handle_token": GLib.Variant("s", tok1),
                            "session_handle_token": GLib.Variant("s", ses_tok),
                        },),
                    ),
                )
                if code != 0 or "session_handle" not in res:
                    return False, f"Portal CreateSession rejected (code={code})."
                session_handle = str(res["session_handle"])

                # Request keyboard (1) + pointer (2) device types = bitmask 3
                tok2 = f"celina_sd_{secrets.token_hex(6)}"
                code2, _ = self._call_portal_request(
                    "SelectDevices",
                    GLib.Variant(
                        "(oa{sv})",
                        (
                            session_handle,
                            {
                                "handle_token": GLib.Variant("s", tok2),
                                "types": GLib.Variant("u", 3),
                                "persist_mode": GLib.Variant("u", 1),
                            },
                        ),
                    ),
                )
                if code2 != 0:
                    return False, f"Portal SelectDevices rejected (code={code2})."

                # Attach a ScreenCast monitor source BEFORE Start() so Mutter returns a valid
                # PipeWire stream node_id required by NotifyPointerMotionAbsolute (per XDG spec).
                try:
                    tok_sc = f"celina_sc_{secrets.token_hex(6)}"
                    self._call_portal_request(
                        "SelectSources",
                        GLib.Variant(
                            "(oa{sv})",
                            (
                                session_handle,
                                {
                                    "handle_token": GLib.Variant("s", tok_sc),
                                    "types": GLib.Variant("u", 1),  # MONITOR
                                    "multiple": GLib.Variant("b", False),
                                },
                            ),
                        ),
                        iface=PORTAL_SC_IFACE,
                    )
                except Exception:
                    pass

                # Start session (GNOME prompts user once for consent)
                tok3 = f"celina_st_{secrets.token_hex(6)}"
                code3, res3 = self._call_portal_request(
                    "Start",
                    GLib.Variant(
                        "(osa{sv})",
                        (session_handle, "", {"handle_token": GLib.Variant("s", tok3)}),
                    ),
                    timeout_ms=20000,
                )
                if code3 != 0:
                    return False, f"Portal Start cancelled or denied (code={code3})."

                streams = res3.get("streams") or []
                if streams and isinstance(streams[0], (tuple, list)):
                    self._stream_node_id = int(streams[0][0])
                self._session_handle = session_handle
                return True, session_handle
            except Exception as exc:
                return False, f"RemoteDesktop portal unavailable: {exc}"

    def click(self, x: int, y: int, button: str = "left", double_click: bool = False) -> PortalActionResult:
        btn_code = _BUTTON_MAP.get(button.lower().strip())
        if btn_code is None:
            return PortalActionResult(False, f"Invalid button '{button}'.")
        ok, handle_or_err = self.ensure_session()
        if not ok:
            return PortalActionResult(False, handle_or_err, portal_active=False)
        if self._stream_node_id <= 0:
            return PortalActionResult(False, "Portal session has no ScreenCast stream for absolute coordinates.", portal_active=True)

        bus = self._get_bus()
        try:
            from gi.repository import Gio, GLib

            bus.call_sync(
                PORTAL_BUS,
                PORTAL_PATH,
                PORTAL_RD_IFACE,
                "NotifyPointerMotionAbsolute",
                GLib.Variant("(oa{sv}udd)", (handle_or_err, {}, self._stream_node_id, float(x), float(y))),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            reps = 2 if double_click else 1
            for _ in range(reps):
                for state in (1, 0):
                    bus.call_sync(
                        PORTAL_BUS,
                        PORTAL_PATH,
                        PORTAL_RD_IFACE,
                        "NotifyPointerButton",
                        GLib.Variant("(oa{sv}iu)", (handle_or_err, {}, btn_code, state)),
                        None,
                        Gio.DBusCallFlags.NONE,
                        2000,
                        None,
                    )
            return PortalActionResult(
                True,
                f"Clicked {button} ({'double' if double_click else 'single'}) at ({x}, {y}) via GNOME RemoteDesktop Portal.",
                portal_active=True,
            )
        except Exception as exc:
            self.close()
            return PortalActionResult(False, f"Portal click error: {exc}", portal_active=False)

    def scroll(
        self,
        dx: float,
        dy: float,
        *,
        x: int | None = None,
        y: int | None = None,
    ) -> PortalActionResult:
        """Wayland/libinput axis convention: +dy scrolls DOWN, -dy scrolls UP; +dx scrolls RIGHT, -dx scrolls LEFT.
        Optionally moves the pointer to (x, y) first when a ScreenCast stream node is attached.
        """
        ok, handle_or_err = self.ensure_session()
        if not ok:
            return PortalActionResult(False, handle_or_err, portal_active=False)
        bus = self._get_bus()
        try:
            from gi.repository import Gio, GLib

            if x is not None and y is not None and self._stream_node_id > 0:
                bus.call_sync(
                    PORTAL_BUS,
                    PORTAL_PATH,
                    PORTAL_RD_IFACE,
                    "NotifyPointerMotionAbsolute",
                    GLib.Variant("(oa{sv}udd)", (handle_or_err, {}, self._stream_node_id, float(x), float(y))),
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                )
            bus.call_sync(
                PORTAL_BUS,
                PORTAL_PATH,
                PORTAL_RD_IFACE,
                "NotifyPointerAxis",
                GLib.Variant("(oa{sv}dd)", (handle_or_err, {}, float(dx), float(dy))),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            return PortalActionResult(True, f"Scrolled ({dx}, {dy}) via GNOME RemoteDesktop Portal.", portal_active=True)
        except Exception as exc:
            self.close()
            return PortalActionResult(False, f"Portal scroll error: {exc}", portal_active=False)

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> PortalActionResult:
        ok, handle_or_err = self.ensure_session()
        if not ok:
            return PortalActionResult(False, handle_or_err, portal_active=False)
        if self._stream_node_id <= 0:
            return PortalActionResult(False, "Portal session has no ScreenCast stream for absolute drag.", portal_active=True)
        bus = self._get_bus()
        try:
            import time as _time

            from gi.repository import Gio, GLib

            def _move(px: float, py: float) -> None:
                bus.call_sync(
                    PORTAL_BUS,
                    PORTAL_PATH,
                    PORTAL_RD_IFACE,
                    "NotifyPointerMotionAbsolute",
                    GLib.Variant("(oa{sv}udd)", (handle_or_err, {}, self._stream_node_id, px, py)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                )

            _move(float(x1), float(y1))
            bus.call_sync(
                PORTAL_BUS, PORTAL_PATH, PORTAL_RD_IFACE, "NotifyPointerButton",
                GLib.Variant("(oa{sv}iu)", (handle_or_err, {}, BTN_LEFT, 1)),
                None, Gio.DBusCallFlags.NONE, 2000, None,
            )
            for frac in (0.25, 0.5, 0.75, 1.0):
                _time.sleep(0.05)
                _move(x1 + (x2 - x1) * frac, y1 + (y2 - y1) * frac)
            bus.call_sync(
                PORTAL_BUS, PORTAL_PATH, PORTAL_RD_IFACE, "NotifyPointerButton",
                GLib.Variant("(oa{sv}iu)", (handle_or_err, {}, BTN_LEFT, 0)),
                None, Gio.DBusCallFlags.NONE, 2000, None,
            )
            return PortalActionResult(True, f"Dragged from ({x1}, {y1}) to ({x2}, {y2}) via GNOME RemoteDesktop Portal.", portal_active=True)
        except Exception as exc:
            self.close()
            return PortalActionResult(False, f"Portal drag error: {exc}", portal_active=False)

    def press_evdev_sequence(self, evdev_seq: list[str]) -> PortalActionResult:
        """Dispatches a sequence of '<code>:<0|1>' Linux evdev key events via NotifyKeyboardKeycode."""
        ok, handle_or_err = self.ensure_session()
        if not ok:
            return PortalActionResult(False, handle_or_err, portal_active=False)
        bus = self._get_bus()
        try:
            from gi.repository import Gio, GLib

            for item in evdev_seq:
                code_s, state_s = item.split(":", 1)
                bus.call_sync(
                    PORTAL_BUS,
                    PORTAL_PATH,
                    PORTAL_RD_IFACE,
                    "NotifyKeyboardKeycode",
                    GLib.Variant("(oa{sv}iu)", (handle_or_err, {}, int(code_s), int(state_s))),
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                )
            return PortalActionResult(True, "Dispatched keyboard sequence via GNOME RemoteDesktop Portal.", portal_active=True)
        except Exception as exc:
            self.close()
            return PortalActionResult(False, f"Portal keyboard error: {exc}", portal_active=False)

    def close(self) -> None:
        with self._lock:
            handle = self._session_handle
            self._session_handle = None
            if handle and self._bus is not None:
                try:
                    from gi.repository import Gio

                    self._bus.call_sync(
                        PORTAL_BUS,
                        handle,
                        PORTAL_SESSION_IFACE,
                        "Close",
                        None,
                        None,
                        Gio.DBusCallFlags.NONE,
                        1000,
                        None,
                    )
                except Exception:
                    pass


_DEFAULT_PORTAL: RemoteDesktopPortalClient | None = None


def get_portal_client() -> RemoteDesktopPortalClient:
    global _DEFAULT_PORTAL
    if _DEFAULT_PORTAL is None:
        _DEFAULT_PORTAL = RemoteDesktopPortalClient()
    return _DEFAULT_PORTAL
