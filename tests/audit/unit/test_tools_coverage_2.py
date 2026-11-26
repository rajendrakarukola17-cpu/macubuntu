"""
tests/audit/unit/test_tools_coverage_2.py

Phase 7d — TASK 1a: tools.py branch coverage tests (round 2).
Targets set_volume, set_brightness, control_media, get_media_title,
AppNapController.nap, and control_connectivity uncovered branches.

No real network calls. Uses tmp_path for HOME directory isolation.
Mocks command_runner, shutil.which, subprocess, and _celina_hardware.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


# ---------------------------------------------------------------------------
# Helper fakes
# ---------------------------------------------------------------------------

class _FakeResult:
    """Fake command_runner result with ok/stdout/stderr/error/message attributes."""

    def __init__(self, ok=True, stdout="", stderr="", error="", message=""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.message = message


class _FakeAudio:
    def __init__(self, state=None, set_msg="OK"):
        self._state = state
        self.set_msg = set_msg

    def get_state(self):
        return self._state

    def set_volume(self, level=None, delta=None, mute=None):
        return _FakeResult(ok=True, message=self.set_msg)


class _FakeState:
    def __init__(self, status, volume_percent=50, muted=False, backend="pipewire", detail=None):
        self.status = status
        self.volume_percent = volume_percent
        self.muted = muted
        self.backend = backend
        self.detail = detail


class _FakeCapStatus:
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"


class _FakeHardware:
    def __init__(self, audio=None):
        self.audio = audio or _FakeAudio()
        self.CapabilityStatus = _FakeCapStatus

    def get_hardware(self):
        return self


# ---------------------------------------------------------------------------
# set_volume
# ---------------------------------------------------------------------------


def test_set_volume_hardware_healthy_state(monkeypatch):
    """CB: set_volume hardware path returns state when healthy and no args."""
    hw = _FakeHardware(audio=_FakeAudio(state=_FakeState(_FakeCapStatus.HEALTHY, 70, False, "pipewire")))
    monkeypatch.setattr(tools, "_celina_hardware", hw)
    result = tools.set_volume()
    assert "Volume: 70%" in result


def test_set_volume_hardware_unhealthy(monkeypatch):
    """CB: set_volume hardware path returns error when unhealthy."""
    hw = _FakeHardware(audio=_FakeAudio(state=_FakeState(_FakeCapStatus.UNHEALTHY, 0, False, "none")))
    monkeypatch.setattr(tools, "_celina_hardware", hw)
    result = tools.set_volume()
    assert "Error" in result


def test_set_volume_hardware_set_level(monkeypatch):
    """CB: set_volume hardware path calls set_volume with level."""
    calls = []
    audio = _FakeAudio(set_msg="Volume set to 50%.")

    def fake_set_volume(level=None, delta=None, mute=None):
        calls.append((level, delta, mute))
        return _FakeResult(ok=True, message="Volume set to 50%.")

    audio.set_volume = fake_set_volume
    hw = _FakeHardware(audio=audio)
    monkeypatch.setattr(tools, "_celina_hardware", hw)
    result = tools.set_volume(level=50)
    assert calls == [(50, None, None)]
    assert "Volume set to 50%." in result


def test_set_volume_hardware_set_mute(monkeypatch):
    """CB: set_volume hardware path calls set_volume with mute."""
    calls = []
    audio = _FakeAudio(set_msg="Audio muted.")

    def fake_set_volume(level=None, delta=None, mute=None):
        calls.append((level, delta, mute))
        return _FakeResult(ok=True, message="Audio muted.")

    audio.set_volume = fake_set_volume
    hw = _FakeHardware(audio=audio)
    monkeypatch.setattr(tools, "_celina_hardware", hw)
    result = tools.set_volume(mute=True)
    assert calls == [(None, None, True)]
    assert "Audio muted." in result


def test_set_volume_no_hw_wpctl_mute(monkeypatch):
    """CB: set_volume no-hardware wpctl mute path."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda cmd, **k: called.append(cmd) or _FakeResult(ok=True),
    )
    result = tools.set_volume(mute=True)
    assert called == [["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"]]
    assert "Audio muted." in result


def test_set_volume_no_hw_wpctl_unmute(monkeypatch):
    """CB: set_volume no-hardware wpctl unmute path."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda cmd, **k: called.append(cmd) or _FakeResult(ok=True),
    )
    result = tools.set_volume(mute=False)
    assert called == [["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"]]
    assert "Audio unmuted." in result


def test_set_volume_no_hw_wpctl_level(monkeypatch):
    """CB: set_volume no-hardware wpctl level path."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda cmd, **k: called.append(cmd) or _FakeResult(ok=True),
    )
    result = tools.set_volume(level=75)
    assert called == [["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "75%"]]
    assert "Volume set to 75%." in result


def test_set_volume_no_hw_wpctl_delta(monkeypatch):
    """CB: set_volume no-hardware wpctl delta path."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda cmd, **k: called.append(cmd) or _FakeResult(ok=True),
    )
    result = tools.set_volume(delta=10)
    assert called == [["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "10%+"]]
    assert "Volume adjusted by +10%." in result


def test_set_volume_no_hw_wpctl_command_error(monkeypatch):
    """CB: set_volume no-hardware wpctl returns error on command failure."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=False, error="mixer busy"),
    )
    result = tools.set_volume(level=50)
    assert "Error: mixer busy" in result


def test_set_volume_no_hw_pactl_mute(monkeypatch):
    """CB: set_volume no-hardware pactl mute path (wpctl not available)."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/pactl" if cmd == "pactl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda cmd, **k: called.append(cmd) or _FakeResult(ok=True),
    )
    result = tools.set_volume(mute=True)
    assert called == [["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"]]
    assert "Audio muted." in result


def test_set_volume_no_hw_pactl_level(monkeypatch):
    """CB: set_volume no-hardware pactl level path."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/pactl" if cmd == "pactl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda cmd, **k: called.append(cmd) or _FakeResult(ok=True),
    )
    result = tools.set_volume(level=30)
    assert called == [["pactl", "set-sink-volume", "@DEFAULT_SINK@", "30%"]]
    assert "Volume set to 30%." in result


def test_set_volume_no_hw_pactl_delta(monkeypatch):
    """CB: set_volume no-hardware pactl delta path."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/pactl" if cmd == "pactl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda cmd, **k: called.append(cmd) or _FakeResult(ok=True),
    )
    result = tools.set_volume(delta=-5)
    assert called == [["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"]]
    assert "Volume adjusted by -5%." in result


def test_set_volume_no_hw_no_mixer(monkeypatch):
    """CB: set_volume no-hardware no mixer returns error."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.set_volume(level=50)
    assert "No supported audio mixer found" in result


def test_set_volume_no_hw_no_args(monkeypatch):
    """CB: set_volume no-hardware no args returns spec message."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.set_volume()
    assert "Specify level" in result


def test_set_volume_level_clamped(monkeypatch):
    """CB: set_volume clamps level to 0-100."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda cmd, **k: called.append(cmd) or _FakeResult(ok=True),
    )
    tools.set_volume(level=150)
    assert "100%" in called[0][-1]
    tools.set_volume(level=-10)
    assert "0%" in called[1][-1]


def test_set_volume_delta_clamped(monkeypatch):
    """CB: set_volume clamps delta to -50..50."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda cmd, **k: called.append(cmd) or _FakeResult(ok=True),
    )
    tools.set_volume(delta=100)
    assert "50%+" in called[0][-1]
    tools.set_volume(delta=-100)
    assert "50%-" in called[1][-1]


# ---------------------------------------------------------------------------
# set_brightness
# ---------------------------------------------------------------------------


def test_set_brightness_hardware(monkeypatch):
    """CB: set_brightness hardware path."""
    fake_hw = MagicMock()
    fake_hw.set_brightness.return_value = _FakeResult(ok=True, message="Brightness set to 60%.")
    monkeypatch.setattr(tools, "_celina_hardware", _FakeHardware())
    monkeypatch.setattr(tools._celina_hardware, "get_hardware", lambda: fake_hw)
    result = tools.set_brightness(60)
    assert "Brightness set to 60%." in result


def test_set_brightness_no_hw_no_utility(monkeypatch):
    """CB: set_brightness no-hardware no brightnessctl returns error."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.set_brightness(50)
    assert "brightnessctl" in result.lower() or "Error" in result


def test_set_brightness_no_hw_success(monkeypatch):
    """CB: set_brightness no-hardware brightnessctl success."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/brightnessctl" if cmd == "brightnessctl" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True),
    )
    result = tools.set_brightness(75)
    assert "Screen brightness set to 75%." in result


def test_set_brightness_no_hw_clamped(monkeypatch):
    """CB: set_brightness clamps to 5-100."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/brightnessctl" if cmd == "brightnessctl" else None)
    called = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: called.append(list(a[0])) or _FakeResult(ok=True),
    )
    tools.set_brightness(200)
    assert "100%" in called[0][-1]
    tools.set_brightness(1)
    assert "5%" in called[1][-1]


def test_set_brightness_no_hw_permission_denied(monkeypatch):
    """CB: set_brightness returns permission denied error."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/brightnessctl" if cmd == "brightnessctl" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=False, stderr="Permission denied"),
    )
    result = tools.set_brightness(50)
    assert "permission denied" in result.lower()


def test_set_brightness_no_hw_command_error(monkeypatch):
    """CB: set_brightness returns error on command failure."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/brightnessctl" if cmd == "brightnessctl" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=False, error="no such device"),
    )
    result = tools.set_brightness(50)
    assert "Failed to set brightness" in result


# ---------------------------------------------------------------------------
# control_media
# ---------------------------------------------------------------------------


def test_control_media_invalid_action():
    """CB: control_media rejects invalid action."""
    result = tools.control_media("bogus_action")
    assert "Invalid media action" in result


def test_control_media_empty_action():
    """CB: control_media rejects empty action."""
    result = tools.control_media("")
    assert "Invalid media action" in result


def test_control_media_mpris_success_play(monkeypatch):
    """CB: control_media returns MPRIS result when D-Bus succeeds."""
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (True, "Media command 'play' executed."))
    result = tools.control_media("play")
    assert "Media command 'play' executed." in result


def test_control_media_mpris_fallback_playerctl_status(monkeypatch):
    """CB: control_media falls back to playerctl status when MPRIS fails."""
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (False, "dbus_unavailable"))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/playerctl" if cmd == "playerctl" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True, stdout="playing: Artist - Song"),
    )
    result = tools.control_media("status")
    assert "playing: Artist - Song" in result


def test_control_media_playerctl_no_players(monkeypatch):
    """CB: control_media playerctl status returns no active player."""
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (False, "dbus_unavailable"))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/playerctl" if cmd == "playerctl" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True, stdout="No players found"),
    )
    result = tools.control_media("status")
    assert "No active media player" in result


def test_control_media_playerctl_command(monkeypatch):
    """CB: control_media playerctl executes non-status command."""
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (False, "dbus_unavailable"))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/playerctl" if cmd == "playerctl" else None)
    calls = []
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: calls.append(list(a[0])) or _FakeResult(ok=True, stdout=""),
    )
    result = tools.control_media("next")
    assert ["playerctl", "next"] in calls
    assert "Media command 'next' executed" in result


def test_control_media_playerctl_command_fails(monkeypatch):
    """CB: control_media playerctl command failure returns error."""
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (False, "dbus_unavailable"))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/playerctl" if cmd == "playerctl" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=False, error="no player"),
    )
    result = tools.control_media("next")
    # Playerctl not ok -> falls through to "No active media player found"
    assert "No active media player" in result or "Error" in result


def test_control_media_no_player_play(monkeypatch):
    """CB: control_media no player returns autoplay hint for play."""
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (False, "dbus_unavailable"))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.control_media("play")
    assert "Autoplay Blocker" in result or "No active MPRIS" in result


def test_control_media_no_player_status(monkeypatch):
    """CB: control_media no player returns no active player for status."""
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (False, "dbus_unavailable"))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.control_media("status")
    assert "No active media player" in result


def test_control_media_no_player_other(monkeypatch):
    """CB: control_media no player returns not found for other actions."""
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (False, "dbus_unavailable"))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.control_media("stop")
    assert "No active media player found" in result


# ---------------------------------------------------------------------------
# get_media_title
# ---------------------------------------------------------------------------


def test_get_media_title_no_gi(monkeypatch):
    """CB: get_media_title returns empty when gi not importable."""
    monkeypatch.setattr(tools, "shutil", tools.shutil)  # ensure shutil is tools.shutil
    # Force ImportError on gi
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gi":
            raise ImportError("No module named 'gi'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.get_media_title()
    assert result == ""


def test_get_media_title_gi_no_playerctl(monkeypatch):
    """CB: get_media_title returns empty when gi fails and no playerctl."""
    # Mock gi import to raise
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gi":
            raise ImportError("No module named 'gi'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.get_media_title()
    assert result == ""


# ---------------------------------------------------------------------------
# AppNapController.nap - various branches
# ---------------------------------------------------------------------------


def test_appnap_nap_empty_name():
    """CB: nap() blocks empty process name."""
    ctrl = tools.AppNapController()
    result = ctrl.nap("")
    assert "Blocked" in result


def test_appnap_nap_excluded_process(monkeypatch):
    """CB: nap() blocks excluded processes like systemd."""
    ctrl = tools.AppNapController()
    result = ctrl.nap("systemd")
    assert "Blocked" in result


def test_appnap_nap_protected_process(monkeypatch):
    """CB: nap() blocks protected processes like python3."""
    ctrl = tools.AppNapController()
    result = ctrl.nap("python3")
    assert "Blocked" in result


def test_appnap_nap_foreground_app(monkeypatch):
    """CB: nap() blocks when app is the foreground window."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(tools.automation, "get_active_window_info", lambda: {"app": "firefox"})
    result = ctrl.nap("firefox")
    assert "Blocked" in result and "foreground" in result


def test_appnap_nap_no_targets(monkeypatch):
    """CB: nap() returns 'No running user process' when no PIDs found."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(ctrl, "_is_protected", lambda name: False)
    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [])
    result = ctrl.nap("nonexistent_app")
    assert "No running user process" in result


def test_appnap_nap_success(monkeypatch):
    """CB: nap() successfully naps a non-protected process with PIDs."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(ctrl, "_is_protected", lambda name: False)
    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [(1234, "firefox", "1000")])
    monkeypatch.setattr(ctrl, "_load_state", lambda: {})
    saved = []
    monkeypatch.setattr(ctrl, "_save_state", lambda state: saved.append(state))
    monkeypatch.setattr(ctrl, "_registered_atexit", False)

    killed = []
    monkeypatch.setattr(tools.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    result = ctrl.nap("firefox")
    assert killed == [(1234, tools.signal.SIGSTOP)]
    assert "firefox" in result
    assert "App Nap" in result


# ---------------------------------------------------------------------------
# control_connectivity
# ---------------------------------------------------------------------------


def test_control_connectivity_valid_targets_actions():
    """CB: control_connectivity rejects invalid target/action combos."""
    assert "Target must be" in tools.control_connectivity("storage", "on")
    assert "Action must be" in tools.control_connectivity("wifi", "invalid")


def test_control_connectivity_wifi_no_nmcli(monkeypatch):
    """CB: control_connectivity wifi returns error when nmcli missing."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.control_connectivity("wifi", "on")
    assert "nmcli not installed" in result


def test_control_connectivity_bluetooth_no_btctl(monkeypatch):
    """CB: control_connectivity bluetooth returns error when bluetoothctl missing."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.control_connectivity("bluetooth", "on")
    assert "bluetoothctl not installed" in result


def test_control_connectivity_wifi_nmcli_success(monkeypatch):
    """CB: control_connectivity wifi succeeds with nmcli."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/nmcli" if cmd == "nmcli" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True),
    )
    result = tools.control_connectivity("wifi", "on")
    assert "Wi-Fi" in result or "enabled" in result.lower() or "connected" in result.lower()

