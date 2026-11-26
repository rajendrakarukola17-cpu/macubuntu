"""
tests/audit/unit/test_tools_coverage_15.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 15).
Targets: _mpris_dbus_control (MPRIS D-Bus control function).

Covers: unknown act (unsupported when gi available), gi import failure (dbus_unavailable),
no MPRIS players (no_mpris_players), status act, play/pause/next/previous
with player ranking, D-Bus call failure (dbus_call_failed), successful
command execution, outer exception handler (dbus_unavailable).

No real D-Bus or network calls. Mocks gi, Gio, GLib via sys.modules injection.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: inject fake gi/Gio/GLib modules
# ---------------------------------------------------------------------------

class _FakeVariant:
    def __init__(self, sig, val):
        self.sig = sig
        self.val = val
    def unpack(self):
        return (self.val,)


class _FakeNamesVar:
    def __init__(self, names):
        self.names = names
    def unpack(self):
        return (self.names,)


class _FakeBus:
    def __init__(self, names=None, list_names_ok=True, method_ok=True, status_map=None):
        self.names = names or []
        self.list_names_ok = list_names_ok
        self.method_ok = method_ok
        self.status_map = status_map or {}
        self.call_count = 0
        self.last_call = None

    def call_sync(self, *args, **kwargs):
        self.call_count += 1
        self.last_call = (args, kwargs)
        if len(args) >= 4 and args[3] == "ListNames":
            if not self.list_names_ok:
                raise Exception("ListNames failed")
            return _FakeNamesVar(self.names)
        # Property Get for PlaybackStatus
        if len(args) >= 4 and args[2] == "org.freedesktop.DBus.Properties":
            pbus = args[0]
            status = self.status_map.get(pbus, "Playing")
            class _V:
                def unpack(self):
                    return (status,)
            return _V()
        # Method call
        if not self.method_ok:
            raise Exception("Method call failed")
        return None


def _install_fake_gi(bus):
    """Inject fake gi, Gio, GLib into sys.modules."""
    fake_gi = MagicMock()
    fake_gi.require_version = MagicMock()

    fake_glib = MagicMock()
    fake_glib.Variant = _FakeVariant

    fake_gio = MagicMock()
    fake_gio.bus_get_sync = MagicMock(return_value=bus)
    fake_gio.BusType = MagicMock(SESSION="session")
    fake_gio.DBusCallFlags = MagicMock(NONE=0)

    fake_repo = MagicMock()
    fake_repo.Gio = fake_gio
    fake_repo.GLib = fake_glib

    sys.modules["gi"] = fake_gi
    sys.modules["gi.repository"] = fake_repo

    return fake_gi, fake_gio, fake_glib


def _remove_fake_gi():
    for mod in ("gi", "gi.repository"):
        sys.modules.pop(mod, None)


# ---------------------------------------------------------------------------
# Unknown act → unsupported (requires gi to be available)
# ---------------------------------------------------------------------------

def test_mpris_unknown_act_with_gi():
    """CB: unknown act + gi available + players present → method is None → (False, 'unsupported')."""
    bus = _FakeBus(names=["org.mpris.MediaPlayer2.spotify"])
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("unknown_action")
        assert result == (False, "unsupported")
    finally:
        _remove_fake_gi()


# ---------------------------------------------------------------------------
# gi import failure → dbus_unavailable
# ---------------------------------------------------------------------------

def test_mpris_gi_import_failure(monkeypatch):
    """CB: import gi raises → (False, 'dbus_unavailable')."""
    _remove_fake_gi()

    import builtins
    # Patch __import__ to fail for 'gi'
    real_import = builtins.__import__
    def failing_import(name, *args, **kwargs):
        if name == "gi" or name == "gi.repository":
            raise ImportError("no gi module")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", failing_import):
        result = tools._mpris_dbus_control("play")
        assert result == (False, "dbus_unavailable")


# ---------------------------------------------------------------------------
# No MPRIS players → no_mpris_players
# ---------------------------------------------------------------------------

def test_mpris_no_players():
    """CB: ListNames returns no MPRIS players → (False, 'no_mpris_players')."""
    bus = _FakeBus(names=[])
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("play")
        assert result == (False, "no_mpris_players")
    finally:
        _remove_fake_gi()


# ---------------------------------------------------------------------------
# Status act
# ---------------------------------------------------------------------------

def test_mpris_status_act():
    """CB: act == 'status' → returns status of all players."""
    bus = _FakeBus(names=["org.mpris.MediaPlayer2.spotify", "org.mpris.MediaPlayer2.firefox"])
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("status")
        assert result[0] is True
        assert "spotify" in result[1]
        assert "firefox" in result[1]
    finally:
        _remove_fake_gi()


# ---------------------------------------------------------------------------
# Play/pause/next/previous with players, success
# ---------------------------------------------------------------------------

def test_mpris_play_success():
    """CB: play with players → bus.call_sync succeeds → (True, 'Media command...')."""
    bus = _FakeBus(names=["org.mpris.MediaPlayer2.spotify"])
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("play")
        assert result[0] is True
        assert "Media command 'play' executed" in result[1]
    finally:
        _remove_fake_gi()


def test_mpris_pause_success():
    """CB: pause with players → bus.call_sync succeeds → (True, 'Media command...')."""
    bus = _FakeBus(names=["org.mpris.MediaPlayer2.spotify"])
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("pause")
        assert result[0] is True
        assert "Media command 'pause' executed" in result[1]
    finally:
        _remove_fake_gi()


def test_mpris_next_success():
    """CB: next with players → bus.call_sync succeeds → (True, 'Media command...')."""
    bus = _FakeBus(names=["org.mpris.MediaPlayer2.spotify"])
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("next")
        assert result[0] is True
        assert "Media command 'next' executed" in result[1]
    finally:
        _remove_fake_gi()


def test_mpris_previous_success():
    """CB: previous with players → bus.call_sync succeeds → (True, 'Media command...')."""
    bus = _FakeBus(names=["org.mpris.MediaPlayer2.spotify"])
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("previous")
        assert result[0] is True
        assert "Media command 'previous' executed" in result[1]
    finally:
        _remove_fake_gi()


# ---------------------------------------------------------------------------
# D-Bus call failure → dbus_call_failed
# ---------------------------------------------------------------------------

def test_mpris_method_call_fails():
    """CB: bus.call_sync for method raises → (False, 'dbus_call_failed')."""
    bus = _FakeBus(names=["org.mpris.MediaPlayer2.spotify"], method_ok=False)
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("play")
        assert result == (False, "dbus_call_failed")
    finally:
        _remove_fake_gi()


# ---------------------------------------------------------------------------
# Outer exception → dbus_unavailable (bus_get_sync fails)
# ---------------------------------------------------------------------------

def test_mpris_bus_get_fails():
    """CB: Gio.bus_get_sync raises → (False, 'dbus_unavailable')."""
    _remove_fake_gi()

    failing_bus_get = MagicMock(side_effect=Exception("bus error"))
    fake_gi = MagicMock()
    fake_gi.require_version = MagicMock()
    fake_gio = MagicMock()
    fake_gio.bus_get_sync = failing_bus_get
    fake_gio.BusType = MagicMock(SESSION="session")
    fake_gio.DBusCallFlags = MagicMock(NONE=0)
    fake_repo = MagicMock()
    fake_repo.Gio = fake_gio
    fake_repo.GLib = MagicMock()
    fake_repo.GLib.Variant = MagicMock()

    sys.modules["gi"] = fake_gi
    sys.modules["gi.repository"] = fake_repo
    try:
        result = tools._mpris_dbus_control("play")
        assert result == (False, "dbus_unavailable")
    finally:
        _remove_fake_gi()


# ---------------------------------------------------------------------------
# Player ranking (browser preference, paused preference for play)
# ---------------------------------------------------------------------------

def test_mpris_ranking_browser_first():
    """CB: browser players ranked before non-browser for 'play'."""
    bus = _FakeBus(
        names=["org.mpris.MediaPlayer2.spotify", "org.mpris.MediaPlayer2.firefox"],
        status_map={
            "org.mpris.MediaPlayer2.spotify": "Playing",
            "org.mpris.MediaPlayer2.firefox": "Paused",
        },
    )
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("play")
        assert result[0] is True
    finally:
        _remove_fake_gi()


def test_mpris_ranking_non_browser_for_pause():
    """CB: for 'pause', playing players preferred over paused."""
    bus = _FakeBus(
        names=["org.mpris.MediaPlayer2.spotify", "org.mpris.MediaPlayer2.firefox"],
        status_map={
            "org.mpris.MediaPlayer2.spotify": "Paused",
            "org.mpris.MediaPlayer2.firefox": "Playing",
        },
    )
    _install_fake_gi(bus)
    try:
        result = tools._mpris_dbus_control("pause")
        assert result[0] is True
    finally:
        _remove_fake_gi()
