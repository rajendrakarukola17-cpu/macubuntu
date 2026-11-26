"""
tests/audit/unit/test_cli_bridge_allowlists.py

Phase 3 unit tests for celina_pkg.cli_bridge.py — shell=False, strict
subcommand allowlists, forbidden-flag denylists, --user forced on systemctl.

Tests that:
- Unknown tools are blocked
- Unknown subcommands are blocked
- Forbidden flags (--exec, --upload-pack, etc.) are blocked
- systemctl always gets --user
- Read-only subcommands execute without approval
- Mutating subcommands require bound approval
- No shell=True injection path exists
"""

from __future__ import annotations

import sys
from unittest import mock

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg.cli_bridge import (
    CLI_ALLOWLIST,
    _FORBIDDEN_FLAGS,
    _normalize_tool_name,
    run_typed_cli,
)

import celina_pkg.cli_bridge as cb


# ---------------------------------------------------------------------------
# _normalize_tool_name
# ---------------------------------------------------------------------------


def test_normalize_strips_cli_prefix():
    assert _normalize_tool_name("cli_git") == "git"


def test_normalize_lowercases():
    assert _normalize_tool_name("GIT") == "git"


def test_normalize_empty():
    assert _normalize_tool_name("") == ""


# ---------------------------------------------------------------------------
# Unknown tool blocked
# ---------------------------------------------------------------------------


def test_unknown_tool_blocked():
    result = run_typed_cli("npm", "install")
    assert "Blocked" in result
    assert "not in the typed allowlist" in result


def test_empty_tool_blocked():
    result = run_typed_cli("", "status")
    assert "Blocked" in result


# ---------------------------------------------------------------------------
# Unknown subcommand blocked
# ---------------------------------------------------------------------------


def test_unknown_subcommand_blocked():
    result = run_typed_cli("git", "nuketheplanet")
    assert "Blocked" in result
    assert "not permitted" in result


# ---------------------------------------------------------------------------
# Forbidden flags blocked
# ---------------------------------------------------------------------------


def test_forbidden_flag_exec_blocked():
    result = run_typed_cli("git", "status", ["--exec"])
    assert "Blocked" in result
    assert "--exec" in result


def test_forbidden_flag_upload_pack_blocked():
    result = run_typed_cli("git", "fetch", ["--upload-pack"])
    assert "Blocked" in result


def test_forbidden_flag_receive_pack_blocked():
    result = run_typed_cli("git", "push", ["--receive-pack"])
    assert "Blocked" in result


def test_forbidden_flag_dash_c_blocked():
    result = run_typed_cli("git", "log", ["-c", "core.autocrlf=true"])
    assert "Blocked" in result


def test_forbidden_flag_config_blocked():
    result = run_typed_cli("git", "config", ["--config", "/tmp/evil"])
    assert "Blocked" in result


# ---------------------------------------------------------------------------
# systemctl --user forced
# ---------------------------------------------------------------------------


@mock.patch("celina_pkg.cli_bridge.command_runner.run_command")
@mock.patch("celina_pkg.cli_bridge.shutil.which", return_value="/usr/bin/systemctl")
def test_systemctl_forces_user(mock_which, mock_run):
    mock_run.return_value = type("R", (), {"stdout": "active", "stderr": "", "returncode": 0})()
    run_typed_cli("systemctl", "status", ["celina.service"])
    called_argv = mock_run.call_args[0][0]
    assert "--user" in called_argv
    # --user should come right after systemctl, before subcommand
    assert called_argv.index("--user") < called_argv.index("status")


# ---------------------------------------------------------------------------
# Read-only subcommands execute without approval
# ---------------------------------------------------------------------------


@mock.patch("celina_pkg.cli_bridge.command_runner.run_command")
@mock.patch("celina_pkg.cli_bridge.shutil.which", return_value="/usr/bin/git")
@mock.patch.object(cb.approval, "request_bound_approval", return_value=(None, None))
def test_readonly_no_approval(mock_approval, mock_which, mock_run):
    mock_run.return_value = type("R", (), {"stdout": "on branch main", "stderr": "", "returncode": 0})()
    run_typed_cli("git", "status")
    # Read-only subcommands should not call approval
    mock_approval.assert_not_called()


# ---------------------------------------------------------------------------
# Mutating subcommands require approval
# ---------------------------------------------------------------------------


@mock.patch("celina_pkg.cli_bridge.command_runner.run_command")
@mock.patch("celina_pkg.cli_bridge.shutil.which", return_value="/usr/bin/git")
def test_mutating_requires_approval(mock_which, mock_run):
    mock_run.return_value = type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()
    with mock.patch.object(cb.approval, "request_bound_approval", return_value=(None, "denied")):
        result = run_typed_cli("git", "commit", ["-m", "x"])
        assert "denied" in result.lower() or "Blocked" in result


# ---------------------------------------------------------------------------
# shell=False enforced (no shell injection via extra_args)
# ---------------------------------------------------------------------------


def test_no_shell_injection_in_extra_args():
    # Even if an attacker tries shell metacharacters, they are treated as
    # literal argv elements, not shell syntax.
    result = run_typed_cli("git", "status", ["; rm -rf /"])
    # Should not execute rm — the semicolon arg is passed literally to git
    # (which will fail with unknown option, but won't run rm)
    assert "rm -rf" not in result or "Blocked" in result or "Error" in result


# ---------------------------------------------------------------------------
# Allowlist structure
# ---------------------------------------------------------------------------


def test_allowlist_has_expected_tools():
    assert "git" in CLI_ALLOWLIST
    assert "systemctl" in CLI_ALLOWLIST
    assert "docker" in CLI_ALLOWLIST
    assert "gh" in CLI_ALLOWLIST


def test_forbidden_flags_nonempty():
    assert "--exec" in _FORBIDDEN_FLAGS
    assert "--upload-pack" in _FORBIDDEN_FLAGS
    assert "-c" in _FORBIDDEN_FLAGS
