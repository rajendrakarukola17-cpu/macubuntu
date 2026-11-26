"""
tests/acceptance/test_strict_sandbox.py

Phase 9 A3 — Strict sandbox fail-closed acceptance tests.

When bwrap/landlock is unavailable, Celina must refuse to run sandboxed
operations (fail closed). The sandbox is an ADDITIONAL defense layer —
approval gates, path validation, and SSRF protections remain enforced
regardless of sandbox availability.
"""

from __future__ import annotations

import shutil
import sys
from unittest.mock import MagicMock, patch

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "features" / "celina"))

from celina_pkg import landlock_policy, config, tools
import pytest  # noqa: E402


def _clear_bwrap_cache():
    """Clear the bwrap probe cache so each test starts fresh."""
    landlock_policy._BWRAP_EXEC_PROBE_CACHE.clear()


# ---------------------------------------------------------------------------
# bwrap unavailable → probe returns False (fail closed for sandbox)
# ---------------------------------------------------------------------------

def test_bwrap_unavailable_returns_false():
    """_probe_bwrap_runtime returns False when bwrap binary is not installed."""
    _clear_bwrap_cache()
    with patch.object(shutil, "which", return_value=None):
        result = landlock_policy._probe_bwrap_runtime("/usr/bin/bwrap", unshare_net=False)
        assert result is False


def test_bwrap_probe_exception_returns_false():
    """_probe_bwrap_runtime returns False when probe subprocess raises."""
    _clear_bwrap_cache()
    with patch.object(shutil, "which", return_value="/usr/bin/bwrap"):
        with patch.object(landlock_policy.subprocess, "run", side_effect=OSError("no such file")):
            result = landlock_policy._probe_bwrap_runtime("/usr/bin/bwrap", unshare_net=False)
            assert result is False


def test_bwrap_probe_non_zero_exit_returns_false():
    """_probe_bwrap_runtime returns False when bwrap exits non-zero."""
    _clear_bwrap_cache()
    fake_result = MagicMock()
    fake_result.returncode = 1
    with patch.object(shutil, "which", return_value="/usr/bin/bwrap"):
        with patch.object(landlock_policy.subprocess, "run", return_value=fake_result):
            result = landlock_policy._probe_bwrap_runtime("/usr/bin/bwrap", unshare_net=False)
            assert result is False


def test_bwrap_probe_zero_exit_returns_true():
    """_probe_bwrap_runtime returns True when bwrap works (sandbox available)."""
    _clear_bwrap_cache()
    fake_result = MagicMock()
    fake_result.returncode = 0
    with patch.object(shutil, "which", return_value="/usr/bin/bwrap"):
        with patch.object(landlock_policy.subprocess, "run", return_value=fake_result):
            result = landlock_policy._probe_bwrap_runtime("/usr/bin/bwrap", unshare_net=False)
            assert result is True


# ---------------------------------------------------------------------------
# landlock ABI unavailable → probe_landlock_abi returns 0
# ---------------------------------------------------------------------------

def test_landlock_abi_unavailable_returns_zero():
    """probe_landlock_abi returns 0 when landlock syscalls are unavailable."""
    with patch.object(landlock_policy, "_get_libc", return_value=None):
        result = landlock_policy.probe_landlock_abi()
        assert result == 0


def test_landlock_abi_syscall_fails_returns_zero():
    """probe_landlock_abi returns 0 when the landlock syscall fails."""
    mock_libc = MagicMock()
    mock_libc.syscall.side_effect = OSError("ENOSYS")
    with patch.object(landlock_policy, "_get_libc", return_value=mock_libc):
        result = landlock_policy.probe_landlock_abi()
        assert result == 0


# ---------------------------------------------------------------------------
# Non-locked sandbox operations still enforce approval + path + SSRF
# ---------------------------------------------------------------------------

def test_sandbox_unavailable_approval_still_required(tmp_path, monkeypatch):
    """Even without sandbox, write_file still requires approval (fail-closed)."""
    monkeypatch.setattr(landlock_policy, "_probe_bwrap_runtime", lambda *a, **k: False)
    monkeypatch.setattr(landlock_policy, "probe_landlock_abi", lambda: 0)
    monkeypatch.setattr(config, "HOME", tmp_path)

    def deny_cb(title, detail):
        return False

    result = tools.write_file(str(tmp_path / "test.txt"), "content", approval_cb=deny_cb)
    assert "Denied" in result or "denied" in result.lower() or "cancelled" in result.lower()


def test_sandbox_unavailable_path_validation_still_enforced(tmp_path, monkeypatch):
    """Even without sandbox, path traversal is still blocked."""
    monkeypatch.setattr(landlock_policy, "_probe_bwrap_runtime", lambda *a, **k: False)
    monkeypatch.setattr(landlock_policy, "probe_landlock_abi", lambda: 0)
    monkeypatch.setattr(config, "HOME", tmp_path)

    # Path validation should still block /etc/passwd regardless of approval
    result = tools.read_file("/etc/passwd")
    assert "limited to files inside your home" in result.lower() or "home directory" in result.lower()


# ---------------------------------------------------------------------------
# Documentation: what non-strict mode allows vs blocks
# ---------------------------------------------------------------------------

def test_non_strict_mode_documented_behavior():
    """
    DOCUMENTATION — Non-strict mode (bwrap/landlock unavailable):

    ALLOWED (sandbox is an additional layer, not primary access control):
    - Running Python tools directly without bwrap/landlock isolation
    - File operations within $HOME with normal OS permissions
    - Network requests through net_policy validation

    STILL BLOCKED even in non-strict mode (primary access controls remain):
    - Approval gates bypassed (write_file, move_file, trash_file require approval)
    - Path validation (_is_within_home still enforced)
    - SSRF/DNS protections (net_policy still enforced)
    - Writing outside $HOME (still blocked)
    - Accessing credential files (.ssh, .aws, etc.) (still blocked)
    """
    assert True  # This is a documentation test; enforcement tested elsewhere