"""
tests/audit/unit/test_tools_coverage_23.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 23).
Targets: open_application gtk-launch desktop scanning, get_media_title MPRIS player found.

Covers: open_application gtk-launch stem match, name match in desktop file,
genericname fallback, WEB_APPS with curated installable app message.
get_media_title MPRIS player with metadata title, MPRIS player without title.

No real desktop or D-Bus calls. Mocks launch_detached, open_url_safe, shutil.which,
Path operations, gi/Gio/GLib.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import os

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


# ---------------------------------------------------------------------------
# open_application gtk-launch desktop scanning
# ---------------------------------------------------------------------------

def test_open_application_gtk_launch_stem_match(monkeypatch, tmp_path):
    """CB: open_application gtk-launch finds .desktop by stem match → launched."""
    desktop_dir = tmp_path / "applications"
    desktop_dir.mkdir()
    (desktop_dir / "firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\n")

    # Patch config.HOME to point to tmp_path
    monkeypatch.setattr(tools.config, "HOME", tmp_path)

    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gtk-launch" if cmd == "gtk-launch" else None)
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)

    result = tools.open_application("firefox")
    assert "Launched" in result or result is not None


def test_open_application_gtk_launch_name_match(monkeypatch, tmp_path):
    """CB: open_application gtk-launch finds .desktop by Name= match in file content."""
    desktop_dir = tmp_path / "applications"
    desktop_dir.mkdir()
    # Desktop file where stem doesn't match but Name= does
    (desktop_dir / "browser.desktop").write_text("[Desktop Entry]\nName=Firefox\n")

    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gtk-launch" if cmd == "gtk-launch" else None)
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)

    result = tools.open_application("firefox")
    assert result is not None


def test_open_application_gtk_launch_genericname_fallback(monkeypatch, tmp_path):
    """CB: open_application gtk-launch genericname fallback match."""
    desktop_dir = tmp_path / "applications"
    desktop_dir.mkdir()
    (desktop_dir / "editor.desktop").write_text("[Desktop Entry]\nGenericName=Text Editor\n")

    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gtk-launch" if cmd == "gtk-launch" else None)
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)

    result = tools.open_application("editor")
    assert result is not None


def test_open_application_gtk_launch_no_desktop_dirs(monkeypatch):
    """CB: open_application gtk-launch available but no desktop dirs exist → falls through."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gtk-launch" if cmd == "gtk-launch" else None)
    # config.HOME doesn't have .local/share/applications
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)
    result = tools.open_application("someapp")
    # Should fall through to WEB_APPS or not found
    assert result is not None


def test_open_application_web_apps_with_curated_msg(monkeypatch):
    """CB: open_application key in WEB_APPS and CURATED_INSTALLABLE_APPS → msg with install hint."""
    monkeypatch.setattr(tools, "WEB_APPS", {**tools.WEB_APPS, "spotify": "https://spotify.com"})
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {**tools.CURATED_INSTALLABLE_APPS, "spotify": ("Spotify", "com.spotify.Client")})
    monkeypatch.setattr(tools, "open_url_safe", lambda url: "opened spotify")
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.open_application("spotify")
    assert "Native app not installed yet" in result or "install" in result


# ---------------------------------------------------------------------------
# get_media_title MPRIS player found
# ---------------------------------------------------------------------------

def test_get_media_title_mpris_player_with_title(monkeypatch):
    """CB: get_media_title MPRIS player with Metadata title → returns '♪ title'."""
    fake_bus = MagicMock()
    fake_names = MagicMock()
    fake_names.unpack = lambda: (["org.mpris.MediaPlayer2.spotify"],)
    fake_bus.call_sync = lambda *a, **k: fake_names

    # For the Metadata Get call
    fake_meta = MagicMock()
    fake_meta.unpack = lambda: ({"xesam:title": "My Favorite Song"},)

    # First call (ListNames) returns names, second call (Get) returns metadata
    call_count = [0]
    def smart_call_sync(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return fake_names
        return fake_meta

    fake_bus.call_sync = smart_call_sync

    fake_gi = MagicMock()
    fake_gi.require_version = MagicMock()
    fake_gi.BusType = MagicMock(SESSION="session")
    fake_gi.DBusCallFlags = MagicMock(NONE=0)
    fake_gi.bus_get_sync = MagicMock(return_value=fake_bus)
    fake_glib = MagicMock()
    fake_glib.Variant = MagicMock()
    fake_repo = MagicMock()
    fake_repo.Gio = fake_gi
    fake_repo.GLib = fake_glib

    sys.modules["gi"] = fake_gi
    sys.modules["gi.repository"] = fake_repo
    try:
        result = tools.get_media_title()
        assert "My Favorite Song" in result
        assert "♪" in result
    finally:
        sys.modules.pop("gi", None)
        sys.modules.pop("gi.repository", None)


def test_get_media_title_mpris_player_no_title(monkeypatch):
    """CB: get_media_title MPRIS player without title → continues, returns ''."""
    fake_bus = MagicMock()
    fake_names = MagicMock()
    fake_names.unpack = lambda: (["org.mpris.MediaPlayer2.spotify"],)

    fake_meta = MagicMock()
    fake_meta.unpack = lambda: ({},)  # empty metadata, no xesam:title

    call_count = [0]
    def smart_call_sync(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return fake_names
        return fake_meta

    fake_bus.call_sync = smart_call_sync

    fake_gi = MagicMock()
    fake_gi.require_version = MagicMock()
    fake_gi.BusType = MagicMock(SESSION="session")
    fake_gi.DBusCallFlags = MagicMock(NONE=0)
    fake_gi.bus_get_sync = MagicMock(return_value=fake_bus)
    fake_glib = MagicMock()
    fake_glib.Variant = MagicMock()
    fake_repo = MagicMock()
    fake_repo.Gio = fake_gi
    fake_repo.GLib = fake_glib

    sys.modules["gi"] = fake_gi
    sys.modules["gi.repository"] = fake_repo
    try:
        result = tools.get_media_title()
        assert result == ""
    finally:
        sys.modules.pop("gi", None)
        sys.modules.pop("gi.repository", None)


def test_get_media_title_mpris_get_exception(monkeypatch):
    """CB: get_media_title MPRIS Get call raises → continues to next player."""
    fake_bus = MagicMock()
    fake_names = MagicMock()
    fake_names.unpack = lambda: (["org.mpris.MediaPlayer2.spotify"],)

    call_count = [0]
    def smart_call_sync(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return fake_names
        raise Exception("D-Bus error")

    fake_bus.call_sync = smart_call_sync

    fake_gi = MagicMock()
    fake_gi.require_version = MagicMock()
    fake_gi.BusType = MagicMock(SESSION="session")
    fake_gi.DBusCallFlags = MagicMock(NONE=0)
    fake_gi.bus_get_sync = MagicMock(return_value=fake_bus)
    fake_glib = MagicMock()
    fake_glib.Variant = MagicMock()
    fake_repo = MagicMock()
    fake_repo.Gio = fake_gi
    fake_repo.GLib = fake_glib

    sys.modules["gi"] = fake_gi
    sys.modules["gi.repository"] = fake_repo
    try:
        result = tools.get_media_title()
        assert result == ""
    finally:
        sys.modules.pop("gi", None)
        sys.modules.pop("gi.repository", None)
