"""
tests/audit/unit/test_tools_coverage_6.py

Phase 7d — TASK 1a: tools.py branch coverage tests (round 6).
Targets handle_fast_intent - the main fast-path intent handler.

No real network calls. Mocks set_volume, set_brightness, control_media,
control_connectivity, open_url_safe, open_application, spotlight_index,
os_events, continuity, calendar_bridge, shutil.which, command_runner,
launch_detached, and _open_media_play / _get_preferred_music_platform.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error="", message="", timed_out=False, returncode=0):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.message = message
        self.timed_out = timed_out
        self.returncode = returncode


def _patch_all(monkeypatch):
    """Mock all external dependencies of handle_fast_intent."""
    monkeypatch.setattr(tools, "set_volume", lambda **k: "vol result")
    monkeypatch.setattr(tools, "set_brightness", lambda level: "brightness result")
    monkeypatch.setattr(tools, "control_media", lambda act: f"media {act}")
    monkeypatch.setattr(tools, "control_connectivity", lambda t, a: f"conn {t} {a}")
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"opened {url}")
    monkeypatch.setattr(tools, "open_application", lambda name: f"opened {name}")
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))
    monkeypatch.setattr(tools, "_open_media_play", lambda plat, q, **k: f"media play {plat}")
    monkeypatch.setattr(tools, "_get_preferred_music_platform", lambda: "spotify")
    monkeypatch.setattr(tools, "_clean_media_query", lambda q: (q, None))
    monkeypatch.setattr(tools, "_FRUSTRATION_RE", MagicMock(search=lambda s: None))


# ---------------------------------------------------------------------------
# Time queries
# ---------------------------------------------------------------------------


def test_handle_fast_intent_time_english(monkeypatch):
    """CB: handle_fast_intent handles English time query."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("what time is it")
    assert "It is currently" in result


def test_handle_fast_intent_time_telugu(monkeypatch):
    """CB: handle_fast_intent handles Telugu time query."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("సమయం ఎంత")
    assert "ఇప్పుడు సమయం" in result


def test_handle_fast_intent_time_hindi(monkeypatch):
    """CB: handle_fast_intent handles Hindi time query."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("समय क्या हुआ")
    assert "अभी समय" in result


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


def test_handle_fast_intent_unmute(monkeypatch):
    """CB: handle_fast_intent handles unmute."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("unmute")
    assert "vol result" in result


def test_handle_fast_intent_mute(monkeypatch):
    """CB: handle_fast_intent handles mute."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("mute")
    assert "vol result" in result


def test_handle_fast_intent_volume_level(monkeypatch):
    """CB: handle_fast_intent handles volume level."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("set volume to 75%")
    assert "vol result" in result


def test_handle_fast_intent_volume_up(monkeypatch):
    """CB: handle_fast_intent handles volume up."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("volume up")
    assert "vol result" in result


def test_handle_fast_intent_volume_down(monkeypatch):
    """CB: handle_fast_intent handles volume down."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("volume down")
    assert "vol result" in result


# ---------------------------------------------------------------------------
# Brightness
# ---------------------------------------------------------------------------


def test_handle_fast_intent_brightness(monkeypatch):
    """CB: handle_fast_intent handles brightness."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("set brightness to 60%")
    assert "brightness result" in result


# ---------------------------------------------------------------------------
# Media controls
# ---------------------------------------------------------------------------


def test_handle_fast_intent_media_play_pause(monkeypatch):
    """CB: handle_fast_intent handles play-pause."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("play music")
    assert "media play-pause" in result


def test_handle_fast_intent_media_next(monkeypatch):
    """CB: handle_fast_intent handles next track."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("next track")
    assert "media next" in result


def test_handle_fast_intent_media_previous(monkeypatch):
    """CB: handle_fast_intent handles previous track."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("previous track")
    assert "media previous" in result


def test_handle_fast_intent_media_status(monkeypatch):
    """CB: handle_fast_intent handles media status."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("whats playing")
    assert "media status" in result


# ---------------------------------------------------------------------------
# Dark / Light mode
# ---------------------------------------------------------------------------


def test_handle_fast_intent_dark_mode(monkeypatch):
    """CB: handle_fast_intent handles dark mode."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("dark mode")
    assert "Dark Mode" in result


def test_handle_fast_intent_light_mode(monkeypatch):
    """CB: handle_fast_intent handles light mode."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("light mode")
    assert "Light Mode" in result


# ---------------------------------------------------------------------------
# Lock screen
# ---------------------------------------------------------------------------


def test_handle_fast_intent_lock_screen(monkeypatch):
    """CB: handle_fast_intent handles lock screen."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("lock screen")
    assert "Locked screen" in result


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


def test_handle_fast_intent_screenshot(monkeypatch):
    """CB: handle_fast_intent handles screenshot."""
    _patch_all(monkeypatch)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None)
    result = tools.handle_fast_intent("take screenshot")
    assert "Screenshot tool" in result


# ---------------------------------------------------------------------------
# Wi-Fi / Bluetooth toggle
# ---------------------------------------------------------------------------


def test_handle_fast_intent_wifi_on(monkeypatch):
    """CB: handle_fast_intent handles wifi on toggle."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("turn on wifi")
    assert "conn wifi on" in result


def test_handle_fast_intent_bluetooth_off(monkeypatch):
    """CB: handle_fast_intent handles bluetooth off toggle."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("turn off bluetooth")
    assert "conn bluetooth off" in result


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------


def test_handle_fast_intent_search_google(monkeypatch):
    """CB: handle_fast_intent handles google search."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("search google for python")
    assert "opened" in result


def test_handle_fast_intent_search_youtube(monkeypatch):
    """CB: handle_fast_intent handles youtube search."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("search youtube for python tutorial")
    assert "opened" in result


# ---------------------------------------------------------------------------
# Open application
# ---------------------------------------------------------------------------


def test_handle_fast_intent_open_app(monkeypatch):
    """CB: handle_fast_intent handles open app."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("open firefox")
    assert "opened firefox" in result


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_handle_fast_intent_login_web_app(monkeypatch):
    """CB: handle_fast_intent handles login to web app."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("login to github")
    assert "opened" in result


def test_handle_fast_intent_login_custom(monkeypatch):
    """CB: handle_fast_intent handles login to custom site."""
    _patch_all(monkeypatch)
    monkeypatch.setattr(tools, "open_application", lambda name: f"Application '{name}' not found")
    result = tools.handle_fast_intent("login to mysite")
    assert "🔐" in result


# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------


def test_handle_fast_intent_convert_units(monkeypatch):
    """CB: handle_fast_intent handles unit conversion."""
    _patch_all(monkeypatch)
    monkeypatch.setattr(tools.spotlight_index, "convert_units", lambda text: "10 km = 6.2 miles")
    result = tools.handle_fast_intent("convert 10 km to miles")
    assert "10 km = 6.2 miles" in result


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------


def test_handle_fast_intent_calculate(monkeypatch):
    """CB: handle_fast_intent handles calculation."""
    _patch_all(monkeypatch)
    monkeypatch.setattr(tools.spotlight_index, "safe_calculate", lambda text: 15)
    result = tools.handle_fast_intent("7 + 8")
    assert "7 + 8 = 15" in result


# ---------------------------------------------------------------------------
# Returns None for unmatched
# ---------------------------------------------------------------------------


def test_handle_fast_intent_unmatched(monkeypatch):
    """CB: handle_fast_intent returns None for unmatched text."""
    _patch_all(monkeypatch)
    monkeypatch.setattr(tools.spotlight_index, "convert_units", lambda text: None)
    monkeypatch.setattr(tools.spotlight_index, "safe_calculate", lambda text: None)
    result = tools.handle_fast_intent("this is just random text that matches nothing")
    assert result is None


def test_handle_fast_intent_play_how_question(monkeypatch):
    """CB: handle_fast_intent skips play for 'how' questions."""
    _patch_all(monkeypatch)
    result = tools.handle_fast_intent("how to play guitar")
    assert result is None
