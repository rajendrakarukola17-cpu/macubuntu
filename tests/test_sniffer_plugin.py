"""
Headless unit tests for features/celina/celina_pkg/plugins/sniffer_plugin.py.

Tests the sniffer's security features:
- App denylist (password managers, incognito browsers, Tor)
- Window title denylist (banking, login, payment pages)
- RAM-only TTL expiration
- Cooldown between sniffs (anti-surveillance)
- No clipboard reading on macOS/Windows (privacy)
- ContextSnapshot is_allowed() and is_expired()
"""

import sys
import time
from pathlib import Path
from unittest import mock

import pytest

CELINA_ROOT = Path(__file__).resolve().parent.parent / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.plugins import sniffer_plugin as sp


# --------------------------------------------------------------------------- #
# ContextSnapshot
# --------------------------------------------------------------------------- #

def test_snapshot_is_expired():
    """Snapshot should expire after SNIFFER_TTL_SECONDS."""
    old_ttl = sp.SNIFFER_TTL_SECONDS
    sp.SNIFFER_TTL_SECONDS = 1  # very short TTL for testing
    try:
        snap = sp.ContextSnapshot(
            window_title="Test", app_name="TestApp",
            highlighted_text="hi", timestamp=time.time() - 2,
            platform="linux", source="ats",
        )
        assert snap.is_expired() is True

        snap2 = sp.ContextSnapshot(
            window_title="Test", app_name="TestApp",
            highlighted_text="hi", timestamp=time.time(),
            platform="linux", source="ats",
        )
        assert snap2.is_expired() is False
    finally:
        sp.SNIFFER_TTL_SECONDS = old_ttl


def test_snapshot_is_allowed_app_denylist():
    """is_allowed() should return False for denylisted apps."""
    for app in sp.SNIFFER_APP_DENYLIST:
        snap = sp.ContextSnapshot(
            window_title="", app_name=app,
            highlighted_text="", timestamp=time.time(),
            platform="linux", source="ats",
        )
        assert snap.is_allowed() is False, f"{app} should be denied"


def test_snapshot_is_allowed_window_denylist():
    """is_allowed() should return False for denylisted window titles."""
    for pattern in sp.SNIFFER_WINDOW_DENYLIST:
        snap = sp.ContextSnapshot(
            window_title=f"My {pattern} page", app_name="chrome",
            highlighted_text="", timestamp=time.time(),
            platform="linux", source="ats",
        )
        assert snap.is_allowed() is False, f"window '{pattern}' should be denied"


def test_snapshot_is_allowed_safe():
    """is_allowed() should return True for safe apps and windows."""
    snap = sp.ContextSnapshot(
        window_title="Document - LibreOffice", app_name="libreoffice",
        highlighted_text="", timestamp=time.time(),
        platform="linux", source="ats",
    )
    assert snap.is_allowed() is True


# --------------------------------------------------------------------------- #
# ContextSniffer.sniff() — cooldown enforcement
# --------------------------------------------------------------------------- #

def test_sniffer_cooldown_blocks_rapid_sniffs():
    """sniff() should enforce a minimum cooldown between calls."""
    sniffer = sp.ContextSniffer()
    sniffer._platform = "linux"
    sniffer._last_sniff_time = 0.0  # force first sniff to be allowed

    # First sniff should proceed (may return empty due to no AT-SPI2, but
    # should not be blocked by cooldown)
    snap1 = sniffer.sniff()
    assert snap1.source != "cooldown", "first sniff should not be cooldown-blocked"

    # Immediate second sniff should be cooldown-blocked
    snap2 = sniffer.sniff()
    assert snap2.source == "cooldown", "second sniff should be cooldown-blocked"
    assert snap2.window_title == ""
    assert snap2.app_name == ""
    assert snap2.highlighted_text == ""

    # After cooldown expires, sniff should proceed again
    sniffer._last_sniff_time = time.time() - sp.SNIFFER_MIN_COOLDOWN_SEC - 0.1
    snap3 = sniffer.sniff()
    assert snap3.source != "cooldown", "sniff after cooldown should proceed"


# --------------------------------------------------------------------------- #
# gear0_sniffer_execute — cooldown + denylist enforcement
# --------------------------------------------------------------------------- #

def test_gear0_execute_cooldown():
    """gear0_sniffer_execute should return cooldown message when cooldown active."""
    sniffer = sp.get_sniffer()
    sniffer._platform = "linux"
    sniffer._last_sniff_time = time.time()  # mark as just sniffed

    result = sp.gear0_sniffer_execute("sniff_context", "what is my active window")
    assert result is not None
    assert "cooldown" in result.lower()


def test_gear0_execute_denylisted_app():
    """gear0_sniffer_execute should block denylisted apps."""
    with mock.patch.object(sp, "sniff_context") as mock_sniff:
        mock_sniff.return_value = sp.ContextSnapshot(
            window_title="", app_name="keepassxc",
            highlighted_text="", timestamp=time.time(),
            platform="linux", source="ats",
        )
        result = sp.gear0_sniffer_execute("sniff_context", "what is my active window")
        assert result is not None
        assert "blocked" in result.lower()


# --------------------------------------------------------------------------- #
# No clipboard reading on macOS/Windows (privacy fix)
# --------------------------------------------------------------------------- #

@mock.patch("shutil.which")
@mock.patch("subprocess.run")
def test_macos_does_not_read_clipboard(mock_run, mock_which):
    """_sniff_macos should NOT call pbpaste (clipboard reading removed)."""
    mock_which.return_value = "/usr/bin/osascript"
    mock_run.return_value = mock.Mock(stdout="Safari|Test Page", stderr="", returncode=0)

    sniffer = sp.ContextSniffer()
    sniffer._platform = "macos"
    title, app, highlighted, source = sniffer._sniff_macos()

    assert title == "Test Page"
    assert app == "Safari"
    # Highlighted text should be empty (no clipboard reading)
    assert highlighted == ""
    # pbpaste should never have been called
    for call in mock_run.call_args_list:
        args = call[0][0] if call[0] else []
        assert "pbpaste" not in args, "pbpaste (clipboard) should not be called on macOS"


@mock.patch("shutil.which")
@mock.patch("subprocess.run")
def test_windows_does_not_read_clipboard(mock_run, mock_which):
    """_sniff_windows should NOT call Get-Clipboard (clipboard reading removed)."""
    mock_which.return_value = "/usr/bin/powershell"
    # First call: window title, second: app name
    mock_run.side_effect = [
        mock.Mock(stdout="Document1 - Word", stderr="", returncode=0),
        mock.Mock(stdout="WINWORD.EXE", stderr="", returncode=0),
    ]

    sniffer = sp.ContextSniffer()
    sniffer._platform = "windows"
    title, app, highlighted, source = sniffer._sniff_windows()

    assert title == "Document1 - Word"
    assert app == "WINWORD.EXE"
    # Highlighted text should be empty (no clipboard reading)
    assert highlighted == ""
    # Get-Clipboard should never have been called
    for call in mock_run.call_args_list:
        args = call[0][0] if call[0] else []
        assert "Get-Clipboard" not in str(args), "Get-Clipboard should not be called on Windows"


# --------------------------------------------------------------------------- #
# gear0_sniffer_classify
# --------------------------------------------------------------------------- #

def test_gear0_classify_recognizes_sniff_queries():
    """gear0_sniffer_classify should return 'sniff_context' for relevant queries."""
    for q in [
        "what is my active window",
        "what's my current app",
        "please sniff context",
        "capture screen",
        "grab window",
        "sniff text",
    ]:
        assert sp.gear0_sniffer_classify(q) == "sniff_context", f"'{q}' should classify"

    assert sp.gear0_sniffer_classify("open the browser") is None
    assert sp.gear0_sniffer_classify("play music") is None


# --------------------------------------------------------------------------- #
# sniff_context convenience function
# --------------------------------------------------------------------------- #

def test_sniff_context_returns_snapshot():
    """sniff_context() should return a ContextSnapshot."""
    result = sp.sniff_context()
    assert isinstance(result, sp.ContextSnapshot)
    assert hasattr(result, "window_title")
    assert hasattr(result, "app_name")
    assert hasattr(result, "highlighted_text")
    assert hasattr(result, "timestamp")
    assert hasattr(result, "platform")
    assert hasattr(result, "source")
    assert hasattr(result, "is_expired")
    assert hasattr(result, "is_allowed")
