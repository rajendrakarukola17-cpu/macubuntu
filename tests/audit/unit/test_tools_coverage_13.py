"""
tests/audit/unit/test_tools_coverage_13.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 13).
Targets: handle_fast_install_intent (25 missing branches — high impact).

Covers: regex no-match, stop-word rejection, curated match, dynamic flathub
search (status_cb, apt fallback, dry-run), flatpak install path (dry-run,
flatpak missing, approval None, approval denied, approval consumed, command
success/failure), apt install path (elevator pkexec/sudo, approval None,
denied, consumed, success/failure), flathub fallback URL.

No real network calls. Mocks CURATED_INSTALLABLE_APPS, _search_flathub_app,
_check_apt_package, shutil.which, approval, command_runner, open_url_safe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools



# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error=""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error


# ---------------------------------------------------------------------------
# handle_fast_install_intent: regex no-match
# ---------------------------------------------------------------------------


def test_install_intent_no_match():
    """CB: regex doesn't match → return None."""
    result = tools.handle_fast_install_intent("hello world")
    assert result is None


def test_install_intent_just_install_no_app():
    """CB: 'install' with no app name → return None."""
    result = tools.handle_fast_install_intent("install")
    assert result is None


def test_install_intent_download_no_app():
    """CB: 'download' with no app name → return None."""
    result = tools.handle_fast_install_intent("download")
    assert result is None


def test_install_intent_please_download():
    """CB: 'please download spotify' matches regex."""
    with patch.object(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")}):
        with patch.object(tools, "_search_flathub_app", return_value=None):
            with patch.object(tools, "_check_apt_package", return_value=None):
                result = tools.handle_fast_install_intent("please download spotify")
                assert result is not None


# ---------------------------------------------------------------------------
# handle_fast_install_intent: stop-word rejection
# ---------------------------------------------------------------------------


def test_install_intent_stop_word_and():
    """CB: 'and' in app name → return None."""
    result = tools.handle_fast_install_intent("install spotify and chrome")
    assert result is None


def test_install_intent_stop_word_pip():
    """CB: 'pip' in app name → return None."""
    result = tools.handle_fast_install_intent("install pip package")
    assert result is None


def test_install_intent_stop_word_youtube():
    """CB: 'youtube' in app name → return None."""
    result = tools.handle_fast_install_intent("install youtube video")
    assert result is None


# ---------------------------------------------------------------------------
# handle_fast_install_intent: curated match (dry_run=False)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_curated_flathub_approval_none(monkeypatch):
    """CB: curated match + approval_cb is None → requires confirmation."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak" if cmd == "flatpak" else None)
    result = tools.handle_fast_install_intent("install spotify")
    assert "requires interactive confirmation" in result


def test_install_intent_curated_flathub_approval_denied(monkeypatch):
    """CB: curated match + approval denied → return deny_err."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak" if cmd == "flatpak" else None)

    def deny_cb(title, detail):
        return False

    result = tools.handle_fast_install_intent("install spotify", approval_cb=deny_cb)
    assert "denied" in result.lower() or "cancelled" in result.lower() or "Error" in result


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_curated_flathub_success(monkeypatch):
    """CB: curated match + approval consumed + command success → installed."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak" if cmd == "flatpak" else None)
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install spotify", approval_cb=approve_cb)
    assert "Installed" in result or "✅" in result


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_curated_flathub_with_status_cb(monkeypatch):
    """CB: flathub path with status_cb → status_cb called (covers if status_cb branch)."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak" if cmd == "flatpak" else None)
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))

    status_calls = []
    def status_cb(msg):
        status_calls.append(msg)

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install spotify", status_cb=status_cb, approval_cb=approve_cb)
    assert "Installed" in result or "\u2713" in result
    assert len(status_calls) > 0


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_apt_with_status_cb(monkeypatch):
    """CB: apt path with status_cb → status_cb called."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda name: "ffmpeg")
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))

    status_calls = []
    def status_cb(msg):
        status_calls.append(msg)

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install ffmpeg", status_cb=status_cb, approval_cb=approve_cb)
    assert "Installed" in result or "\u2713" in result
    assert len(status_calls) > 0


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_curated_flathub_command_failure(monkeypatch):
    """CB: curated match + command failure → error message."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak" if cmd == "flatpak" else None)
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=False, stderr="network error"))

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install spotify", approval_cb=approve_cb)
    assert "Failed to install" in result


# ---------------------------------------------------------------------------
# handle_fast_install_intent: dry-run
# ---------------------------------------------------------------------------


def test_install_intent_dry_run_curated(monkeypatch):
    """CB: dry_run=True + curated match → dry-run simulation message."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")})
    result = tools.handle_fast_install_intent("install spotify", dry_run=True)
    assert "Dry-Run Simulation" in result


def test_install_intent_dry_run_no_match_dynamic(monkeypatch):
    """CB: dry_run=True + no curated match → dry-run simulation for dynamic search."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    result = tools.handle_fast_install_intent("install unknownapp", dry_run=True)
    assert "Dry-Run Simulation" in result


# ---------------------------------------------------------------------------
# handle_fast_install_intent: flatpak missing
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_flatpak_not_installed(monkeypatch):
    """CB: flatpak not installed + curated match → open URL fallback."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)  # flatpak not found
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"opened:{url}")

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install spotify", approval_cb=approve_cb)
    assert "Flatpak is not installed" in result


# ---------------------------------------------------------------------------
# handle_fast_install_intent: dynamic flathub search + apt fallback
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_dynamic_flathub_search_status_cb(monkeypatch):
    """CB: no curated match + not dry_run → status_cb called, flathub search."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak" if cmd == "flatpak" else None)
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: ("VLC", "org.videolan.VLC"))
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))

    status_calls = []
    def status_cb(msg):
        status_calls.append(msg)

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install vlc", status_cb=status_cb, approval_cb=approve_cb)
    assert len(status_calls) > 0
    assert "Searching Flathub" in status_calls[0]


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_dynamic_apt_fallback(monkeypatch):
    """CB: no curated match + flathub search None → apt package check."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak" if cmd == "flatpak" else None)
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda name: "ffmpeg")
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install ffmpeg", approval_cb=approve_cb)
    assert "Installed" in result or "✅" in result


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_apt_approval_none(monkeypatch):
    """CB: apt path + approval_cb is None → requires confirmation."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)  # no flatpak
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda name: "ffmpeg")
    result = tools.handle_fast_install_intent("install ffmpeg")
    assert "requires confirmation" in result


def test_install_intent_apt_approval_denied(monkeypatch):
    """CB: apt path + approval denied → return deny_err."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda name: "ffmpeg")

    def deny_cb(title, detail):
        return False

    result = tools.handle_fast_install_intent("install ffmpeg", approval_cb=deny_cb)
    assert "denied" in result.lower() or "cancelled" in result.lower() or "Error" in result


# ---------------------------------------------------------------------------
# handle_fast_install_intent: apt elevator pkexec vs sudo
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_apt_elevator_pkexec(monkeypatch):
    """CB: apt path with DISPLAY set → pkexec elevator."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/pkexec" if cmd == "pkexec" else None)
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda name: "ffmpeg")
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))
    monkeypatch.setattr(os, "environ", {"DISPLAY": ":0"})

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install ffmpeg", approval_cb=approve_cb)
    assert "Installed" in result or "✅" in result


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_apt_elevator_sudo(monkeypatch):
    """CB: apt path without DISPLAY → sudo elevator."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda name: "ffmpeg")
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))
    monkeypatch.setattr(os, "environ", {})

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install ffmpeg", approval_cb=approve_cb)
    assert "Installed" in result or "✅" in result


# ---------------------------------------------------------------------------
# handle_fast_install_intent: apt command failure
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_apt_command_failure(monkeypatch):
    """CB: apt path + command failure → error message."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda name: "ffmpeg")
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=False, stderr="package not found"))

    def approve_cb(title, detail):
        return ("token", None)

    result = tools.handle_fast_install_intent("install ffmpeg", approval_cb=approve_cb)
    assert "Failed to install" in result


# ---------------------------------------------------------------------------
# handle_fast_install_intent: flathub fallback URL
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_flathub_fallback(monkeypatch):
    """CB: no match anywhere → open Flathub search URL fallback."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda name: None)
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"opened:{url}")

    result = tools.handle_fast_install_intent("install unknownappxyz")
    assert "No exact Flathub or APT package matched" in result
    assert "flathub.org/apps/search" in result


# ---------------------------------------------------------------------------
# handle_fast_install_intent: punctuation stripping
# ---------------------------------------------------------------------------


def test_install_intent_trailing_punctuation():
    """CB: trailing .!? stripped from text before regex match."""
    with patch.object(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")}):
        with patch.object(tools, "_search_flathub_app", return_value=None):
            with patch.object(tools, "_check_apt_package", return_value=None):
                with patch.object(tools.shutil, "which", lambda cmd: None):
                    result = tools.handle_fast_install_intent("install spotify!")
                    assert result is not None



@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_flathub_verify_fails(monkeypatch):
    """CB: flathub path + verify_and_consume returns False → return err."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {"spotify": ("Spotify", "com.spotify.Client")})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak" if cmd == "flatpak" else None)
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))

    def approve_cb(title, detail):
        return True

    with patch.object(tools.approval, "verify_and_consume", return_value=(False, "Denied: token mismatch")):
        result = tools.handle_fast_install_intent("install spotify", approval_cb=approve_cb)
        assert result == "Denied: token mismatch"


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live install requires trusted approval broker")
def test_install_intent_apt_verify_fails(monkeypatch):
    """CB: apt path + verify_and_consume returns False → return err."""
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS", {})
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_search_flathub_app", lambda name: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda name: "ffmpeg")
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: _FakeResult(ok=True))

    def approve_cb(title, detail):
        return True

    with patch.object(tools.approval, "verify_and_consume", return_value=(False, "Denied: token expired")):
        result = tools.handle_fast_install_intent("install ffmpeg", approval_cb=approve_cb)
        assert result == "Denied: token expired"
