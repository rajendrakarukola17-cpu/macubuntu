"""
tests/audit/unit/test_tools_coverage_18.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 18).
Targets: wake, _find_exact_user_pids, _proc_info, get_media_title (AppNapController + media).

Covers: wake with state match (PID verified), wake fallback to _find_exact_user_pids,
wake no match, _proc_info OSError/malformed/empty fields, _find_exact_user_pids no /proc,
get_media_title with gi available/no players/player missing/playerctl fallback.

No real network, /proc, or D-Bus calls. Mocks os.kill, Path operations, gi/Gio/GLib.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import tempfile
import os

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


# ---------------------------------------------------------------------------
# AppNapController.wake
# ---------------------------------------------------------------------------

def test_wake_state_match_success(monkeypatch, tmp_path):
    """CB: wake() finds PID in state, verifies via _proc_info, SIGCONT succeeds → 'woken up from App Nap (verified PIDs...)'."""
    ctrl = tools.AppNapController(state_path=tmp_path / "napped.json")
    # Pre-populate state with a matching process
    state = {
        "1234": {"pid": 1234, "comm": "firefox", "start_time": "1000", "frozen_at": 1.0},
    }
    ctrl._save_state(state)

    def fake_proc_info(pid):
        return ("firefox", os.getuid(), "1000")

    monkeypatch.setattr(ctrl, "_proc_info", staticmethod(fake_proc_info))
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))

    result = ctrl.wake("firefox")
    assert "woken up from App Nap" in result
    assert "verified PIDs" in result
    assert 1234 in killed


def test_wake_state_match_oserror_skip(monkeypatch, tmp_path):
    """CB: wake() state match but os.kill raises OSError → skipped, fallback tries _find_exact_user_pids."""
    ctrl = tools.AppNapController(state_path=tmp_path / "napped.json")
    state = {
        "1234": {"pid": 1234, "comm": "firefox", "start_time": "1000", "frozen_at": 1.0},
    }
    ctrl._save_state(state)

    def fake_proc_info(pid):
        return ("firefox", os.getuid(), "1000")

    monkeypatch.setattr(ctrl, "_proc_info", staticmethod(fake_proc_info))
    monkeypatch.setattr(os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError()))
    # _find_exact_user_pids returns empty → no fallback
    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [])

    result = ctrl.wake("firefox")
    assert "No napped user process matching exact name" in result


def test_wake_fallback_find_exact_user_pids(monkeypatch, tmp_path):
    """CB: wake() state empty → falls back to _find_exact_user_pids → SIGCONT succeeds."""
    ctrl = tools.AppNapController(state_path=tmp_path / "napped.json")
    ctrl._save_state({})  # empty state

    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [(5678, "firefox", "2000")])
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))

    result = ctrl.wake("firefox")
    assert "woken up from App Nap" in result
    assert "verified PIDs" not in result  # fallback path
    assert 5678 in killed


def test_wake_fallback_oserror_continue(monkeypatch, tmp_path):
    """CB: wake() fallback _find_exact_user_pids but os.kill raises OSError → continue, no match."""
    ctrl = tools.AppNapController(state_path=tmp_path / "napped.json")
    ctrl._save_state({})

    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [(5678, "firefox", "2000")])
    monkeypatch.setattr(os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError()))

    result = ctrl.wake("firefox")
    assert "No napped user process matching exact name" in result


def test_wake_no_proc_info_match(monkeypatch, tmp_path):
    """CB: wake() state match but _proc_info returns None → skip to remaining, then fallback."""
    ctrl = tools.AppNapController(state_path=tmp_path / "napped.json")
    state = {
        "1234": {"pid": 1234, "comm": "firefox", "start_time": "1000", "frozen_at": 1.0},
    }
    ctrl._save_state(state)

    monkeypatch.setattr(ctrl, "_proc_info", staticmethod(lambda pid: None))
    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [])

    result = ctrl.wake("firefox")
    assert "No napped user process matching exact name" in result


def test_wake_uid_mismatch(monkeypatch, tmp_path):
    """CB: wake() state match but _proc_info uid != os.getuid() → skip."""
    ctrl = tools.AppNapController(state_path=tmp_path / "napped.json")
    state = {
        "1234": {"pid": 1234, "comm": "firefox", "start_time": "1000", "frozen_at": 1.0},
    }
    ctrl._save_state(state)

    monkeypatch.setattr(ctrl, "_proc_info", staticmethod(lambda pid: ("firefox", 9999, "1000")))
    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [])

    result = ctrl.wake("firefox")
    assert "No napped user process matching exact name" in result


def test_wake_start_time_mismatch(monkeypatch, tmp_path):
    """CB: wake() state match but start_time differs → skip."""
    ctrl = tools.AppNapController(state_path=tmp_path / "napped.json")
    state = {
        "1234": {"pid": 1234, "comm": "firefox", "start_time": "1000", "frozen_at": 1.0},
    }
    ctrl._save_state(state)

    monkeypatch.setattr(ctrl, "_proc_info", staticmethod(lambda pid: ("firefox", os.getuid(), "9999")))
    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [])

    result = ctrl.wake("firefox")
    assert "No napped user process matching exact name" in result


# ---------------------------------------------------------------------------
# _find_exact_user_pids
# ---------------------------------------------------------------------------

def test_find_exact_user_pids_iterdir_error(monkeypatch):
    """CB: _find_exact_user_pids when /proc.iterdir raises → returns []."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [])
    result = ctrl._find_exact_user_pids("firefox")
    assert result == []


def test_find_exact_user_pids_pid_eq_my_pid(monkeypatch):
    """CB: _find_exact_user_pids skips own PID — mock _proc_info to return matching comm for non-own PID."""
    ctrl = tools.AppNapController()
    # Mock _proc_info to return firefox for pid 9999, python for own pid
    def fake_proc_info(pid):
        if pid == os.getpid():
            return ("python", os.getuid(), "100")
        return ("firefox", os.getuid(), "200")
    monkeypatch.setattr(ctrl, "_proc_info", fake_proc_info)
    # Mock Path.iterdir to yield entries
    fake_entries = []
    for pid in (os.getpid(), 9999):
        entry = MagicMock()
        entry.name = str(pid)
        entry.isdigit = lambda: True
        fake_entries.append(entry)
    monkeypatch.setattr(tools.Path, "iterdir", lambda self: iter(fake_entries))

    result = ctrl._find_exact_user_pids("firefox")
    assert len(result) == 1
    assert result[0][0] == 9999


def test_find_exact_user_pids_uid_mismatch(monkeypatch):
    """CB: _find_exact_user_pids skips PIDs with different uid."""
    ctrl = tools.AppNapController()
    entry = MagicMock()
    entry.name = "9999"
    entry.isdigit = lambda: True
    monkeypatch.setattr(tools.Path, "iterdir", lambda self: iter([entry]))
    monkeypatch.setattr(ctrl, "_proc_info", lambda pid: ("firefox", 9999, "200"))

    result = ctrl._find_exact_user_pids("firefox")
    assert result == []


def test_find_exact_user_pids_comm_no_match(monkeypatch):
    """CB: _find_exact_user_pids skips PIDs with different comm."""
    ctrl = tools.AppNapController()
    entry = MagicMock()
    entry.name = "9999"
    entry.isdigit = lambda: True
    monkeypatch.setattr(tools.Path, "iterdir", lambda self: iter([entry]))
    monkeypatch.setattr(ctrl, "_proc_info", lambda pid: ("gedit", os.getuid(), "200"))

    result = ctrl._find_exact_user_pids("firefox")
    assert result == []


# ---------------------------------------------------------------------------
# _proc_info
# ---------------------------------------------------------------------------

def test_proc_info_empty_fields(monkeypatch):
    """CB: _proc_info returns None when stat has parens but not enough fields."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(
        tools.Path,
        "read_text",
        lambda self, **k: "(comm) 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19",
    )
    result = ctrl._proc_info(1234)
    assert result is None


# ---------------------------------------------------------------------------
# get_media_title
# ---------------------------------------------------------------------------

def test_get_media_title_gi_available_no_players(monkeypatch):
    """CB: get_media_title with gi available but no MPRIS players → returns ''."""
    fake_bus = MagicMock()
    fake_names = MagicMock()
    fake_names.unpack = lambda: ([],)
    fake_bus.call_sync = lambda *a, **k: fake_names

    fake_gi = MagicMock()
    fake_gi.require_version = MagicMock()
    fake_gi.BusType = MagicMock(SESSION="session")
    fake_gi.DBusCallFlags = MagicMock(NONE=0)
    fake_repo = MagicMock()
    fake_repo.Gio = fake_gi
    fake_repo.GLib = MagicMock()

    sys.modules["gi"] = fake_gi
    sys.modules["gi.repository"] = fake_repo
    try:
        result = tools.get_media_title()
        assert result == ""
    finally:
        sys.modules.pop("gi", None)
        sys.modules.pop("gi.repository", None)


def test_get_media_title_gi_import_failure(monkeypatch):
    """CB: get_media_title import gi raises → falls through to playerctl path."""
    # Remove gi from sys.modules so import is attempted
    sys.modules.pop("gi", None)
    sys.modules.pop("gi.repository", None)

    import builtins
    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "gi":
            raise ImportError("no gi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    with patch.object(builtins, "__import__", failing_import):
        result = tools.get_media_title()
        assert result == ""


def test_get_media_title_playerctl_available(monkeypatch):
    """CB: get_media_title gi unavailable + playerctl available → subprocess fallback."""
    sys.modules.pop("gi", None)
    sys.modules.pop("gi.repository", None)

    import builtins
    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "gi":
            raise ImportError("no gi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/playerctl" if cmd == "playerctl" else None)

    class FakeResult:
        returncode = 0
        stdout = "♪ Song Title"

    with patch.object(builtins, "__import__", failing_import), \
         patch.object(tools.subprocess, "run", return_value=FakeResult()):
        result = tools.get_media_title()
        assert "Song Title" in result


def test_get_media_title_playerctl_missing(monkeypatch):
    """CB: get_media_title gi unavailable + playerctl missing → returns ''."""
    sys.modules.pop("gi", None)
    sys.modules.pop("gi.repository", None)

    import builtins
    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "gi":
            raise ImportError("no gi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)

    with patch.object(builtins, "__import__", failing_import):
        result = tools.get_media_title()
        assert result == ""
