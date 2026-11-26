"""
Headless unit tests for features/celina/celina_pkg/plugins/os_shim.py.

Tests the universal OS shim:
- gear0_os_classify pattern matching
- gear0_os_execute dispatch + ActionGate dry_run gating
- UniversalOSShim instantiation
- OSPlatform detection
- ShimResult dataclass
- Command injection safety (Windows set_clipboard, macOS/Windows notifications)
"""

import sys
from pathlib import Path

import pytest

CELINA_ROOT = Path(__file__).resolve().parent.parent / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.plugins import os_shim as osh


# --------------------------------------------------------------------------- #
# gear0_os_classify
# --------------------------------------------------------------------------- #

def test_classify_recognizes_sleep():
    """gear0_os_classify should return 'sleep_computer' for sleep queries."""
    for q in ["sleep", "suspend computer", "please sleep"]:
        assert osh.gear0_os_classify(q) == "sleep_computer", f"'{q}' should classify"

def test_classify_recognizes_shutdown():
    """gear0_os_classify should return 'shutdown' for shutdown queries."""
    assert osh.gear0_os_classify("shutdown") == "shutdown"
    assert osh.gear0_os_classify("shutdown now") == "shutdown"

def test_classify_recognizes_restart():
    """gear0_os_classify should return 'restart' for restart queries."""
    assert osh.gear0_os_classify("restart") == "restart"

def test_classify_recognizes_lock():
    """gear0_os_classify should return 'lock_screen' for lock queries."""
    assert osh.gear0_os_classify("lock") == "lock_screen"
    assert osh.gear0_os_classify("lock screen") == "lock_screen"

def test_classify_recognizes_trash():
    """gear0_os_classify should return 'empty_trash' for trash queries."""
    assert osh.gear0_os_classify("empty trash") == "empty_trash"
    assert osh.gear0_os_classify("trash") == "empty_trash"

def test_classify_recognizes_clipboard():
    """gear0_os_classify should return 'get_clipboard' for clipboard queries."""
    assert osh.gear0_os_classify("clipboard") == "get_clipboard"

def test_classify_recognizes_notify():
    """gear0_os_classify should return 'show_notification' for notify queries."""
    assert osh.gear0_os_classify("notify") == "show_notification"
    assert osh.gear0_os_classify("notification") == "show_notification"

def test_classify_recognizes_screenshot():
    """gear0_os_classify should return 'take_screenshot' for screenshot queries."""
    assert osh.gear0_os_classify("screenshot") == "take_screenshot"

def test_classify_recognizes_system_info():
    """gear0_os_classify should return 'get_system_info' for sysinfo queries."""
    assert osh.gear0_os_classify("system info") == "get_system_info"
    assert osh.gear0_os_classify("sysinfo") == "get_system_info"

def test_classify_recognizes_open():
    """gear0_os_classify should return 'open_file' for open queries."""
    assert osh.gear0_os_classify("open file") == "open_file"
    assert osh.gear0_os_classify("open") == "open_file"

def test_classify_returns_none_for_unrelated():
    """gear0_os_classify should return None for unrelated queries."""
    assert osh.gear0_os_classify("play music") is None
    assert osh.gear0_os_classify("") is None
    assert osh.gear0_os_classify("what is the weather") is None


# --------------------------------------------------------------------------- #
# gear0_os_execute — ActionGate dry_run gating
# --------------------------------------------------------------------------- #

def test_destructive_op_refused_without_dry_run():
    """Destructive ops should be refused unless dry_run=True."""
    for handler in ["sleep_computer", "shutdown", "restart", "empty_trash", "lock_screen"]:
        result = osh.gear0_os_execute(handler, "query", dry_run=False)
        assert result is not None
        assert "tier-2" in result, f"{handler} should require tier-2 approval without dry_run"

def test_destructive_op_simulated_with_dry_run():
    """Destructive ops should return simulation message with dry_run=True."""
    for handler in ["sleep_computer", "shutdown", "restart", "empty_trash", "lock_screen"]:
        result = osh.gear0_os_execute(handler, "query", dry_run=True)
        assert result is not None
        assert "Dry run" in result, f"{handler} should simulate with dry_run"
        assert "tier-2" not in result, f"{handler} should not mention tier-2 with dry_run"

def test_non_destructive_op_not_blocked():
    """Non-destructive ops should not be blocked by dry_run check."""
    result = osh.gear0_os_execute("get_system_info", "sysinfo", dry_run=False)
    assert result is not None
    assert "tier-2" not in result

def test_execute_unknown_handler_returns_none():
    """gear0_os_execute with unknown handler should return None."""
    assert osh.gear0_os_execute("nonexistent_handler", "query") is None


# --------------------------------------------------------------------------- #
# UniversalOSShim
# --------------------------------------------------------------------------- #

def test_shim_instantiation():
    """UniversalOSShim should instantiate with detected platform."""
    shim = osh.UniversalOSShim()
    assert shim is not None
    assert shim.platform in ("linux", "macos", "windows", "unknown")

def test_get_shim_singleton():
    """get_shim should return the same instance (singleton)."""
    shim1 = osh.get_shim()
    shim2 = osh.get_shim()
    assert shim1 is shim2

def test_os_platform_detect():
    """OSPlatform.detect should return a valid platform string."""
    platform = osh.OSPlatform.detect()
    assert platform in ("linux", "macos", "windows", "unknown")

def test_shim_result_creation():
    """ShimResult should create with all fields."""
    result = osh.ShimResult(
        success=True,
        output="output text",
        command_used="test command",
        platform="linux",
        fallback_used=False,
    )
    assert result.success is True
    assert result.output == "output text"
    assert result.command_used == "test command"
    assert result.platform == "linux"
    assert result.fallback_used is False


# --------------------------------------------------------------------------- #
# Security: command injection safety
# --------------------------------------------------------------------------- #

def test_set_clipboard_with_quotes_does_not_crash():
    """set_clipboard with text containing single quotes should not crash (injection safety)."""
    shim = osh.UniversalOSShim()
    # On non-Windows, this won't hit the PowerShell path, but should not crash
    result = shim.set_clipboard("text with 'single quotes' and \"double quotes\"")
    assert result is not None
    assert isinstance(result, osh.ShimResult)

def test_show_notification_with_quotes_does_not_crash():
    """show_notification with quotes in title/body should not crash (injection safety)."""
    shim = osh.UniversalOSShim()
    result = shim.show_notification('Title with "quotes"', 'Body with "quotes"')
    assert result is not None
    assert isinstance(result, osh.ShimResult)

# --------------------------------------------------------------------------- #
# gear0_os_execute convenience
# --------------------------------------------------------------------------- #

def test_execute_get_system_info():
    """gear0_os_execute for get_system_info should return system info."""
    result = osh.gear0_os_execute("get_system_info", "system info")
    assert result is not None
    assert "platform" in result.lower() or "python" in result.lower()

def test_execute_open_file_no_path():
    """gear0_os_execute for open_file without path should return info message."""
    result = osh.gear0_os_execute("open_file", "open file")
    assert result is not None
    assert "Specify a file path" in result
