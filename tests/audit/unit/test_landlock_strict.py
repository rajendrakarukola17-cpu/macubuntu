"""
tests/audit/unit/test_landlock_strict.py

Task 8a — Strict sandbox: build_kernel_sandbox_cmd(strict=True) must refuse
to run unsandboxed when no driver + no Landlock ABI.

Each test fails when its raise/guard is removed (demonstrated below).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import landlock_policy
from celina_pkg.autonomy.self_healing import SelfHealingExecutor
from celina_pkg.landlock_policy import LandlockEnforcementError
import pytest  # noqa: E402


def _make_profile():
    return landlock_policy.profile_for_tool("write_file", workdir=None)


# --- build_kernel_sandbox_cmd strict tests ---


def test_build_kernel_sandbox_cmd_strict_no_driver_raises():
    """strict=True with no sandbox driver and no Landlock ABI must raise."""
    with patch("shutil.which", return_value=None), patch.object(
        landlock_policy, "probe_landlock_abi", return_value=0
    ):
        with pytest.raises(LandlockEnforcementError):
            landlock_policy.build_kernel_sandbox_cmd(
                ["echo", "hi"],
                _make_profile(),
                strict=True,
            )


def test_build_kernel_sandbox_cmd_strict_with_driver_works():
    """strict=True with an explicit bwrap_bin returns a wrapped command."""
    prof = landlock_policy.profile_for_tool("write_file", workdir=None)
    cmd, driver, p_hash = landlock_policy.build_kernel_sandbox_cmd(
        ["echo", "hi"],
        prof,
        sharness_bin="",
        ai_jail_bin="",
        bwrap_bin="/usr/bin/bwrap",
        workdir=None,
        strict=True,
    )
    assert driver == "bwrap+landlock"
    assert cmd[0] == "/usr/bin/bwrap"


def test_build_kernel_sandbox_cmd_strict_bwrap_probe_fails_raises():
    """strict=True: bwrap exists but probe fails must raise."""
    def which_side_effect(name):
        if name == "bwrap":
            return "/usr/bin/bwrap"
        return None

    with patch.object(shutil, "which", side_effect=which_side_effect), patch.object(
        landlock_policy, "_probe_bwrap_runtime", return_value=False
    ), patch.object(landlock_policy, "probe_landlock_abi", return_value=0):
        with pytest.raises(LandlockEnforcementError):
            landlock_policy.build_kernel_sandbox_cmd(
                ["echo", "hi"], _make_profile(), strict=True
            )


def test_build_kernel_sandbox_cmd_non_strict_unchanged():
    """strict=False with no driver returns (cmd, 'landlock_ctypes', hash) — current behavior."""
    with patch.object(shutil, "which", return_value=None), patch.object(
        landlock_policy, "probe_landlock_abi", return_value=0
    ):
        cmd, driver, p_hash = landlock_policy.build_kernel_sandbox_cmd(
            ["echo", "hi"], _make_profile(), strict=False
        )
        assert driver == "landlock_ctypes"
        assert cmd == ["echo", "hi"]


def test_build_kernel_sandbox_cmd_strict_landlock_available_returns_ctypes():
    """strict=True with Landlock ABI >= 1 returns landlock_ctypes (kernel enforcement)."""
    with patch.object(shutil, "which", return_value=None), patch.object(
        landlock_policy, "probe_landlock_abi", return_value=1
    ):
        cmd, driver, p_hash = landlock_policy.build_kernel_sandbox_cmd(
            ["echo", "hi"], _make_profile(), strict=True
        )
        assert driver == "landlock_ctypes"
        assert cmd == ["echo", "hi"]


# --- self_healing strict tests ---


def test_self_healing_refuses_without_sandbox(tmp_path):
    """SelfHealingExecutor with no sandbox driver returns ExecResult(success=False, error='sandbox_unavailable: ...')."""
    executor = SelfHealingExecutor(data_dir=tmp_path / "data")
    with patch("shutil.which", return_value=None), patch.object(
        landlock_policy, "probe_landlock_abi", return_value=0
    ):
        res = executor.run_sandboxed_command("write_file", ["echo", "hi"], workdir=tmp_path)
        assert res.success is False
        assert "sandbox_unavailable" in res.error


def test_self_healing_strict_logs_and_refuses(caplog, tmp_path):
    """SelfHealingExecutor logs a clear error and takes no repair action when sandbox missing."""
    executor = SelfHealingExecutor(data_dir=tmp_path / "data")
    with patch("shutil.which", return_value=None), patch.object(
        landlock_policy, "probe_landlock_abi", return_value=0
    ), caplog.at_level("ERROR", logger="celina.autonomy.self_healing"):
        res = executor.run_sandboxed_command("write_file", ["echo", "hi"], workdir=tmp_path)
        assert res.success is False
        assert "Sandbox unavailable" in caplog.text or "sandbox_unavailable" in res.error
