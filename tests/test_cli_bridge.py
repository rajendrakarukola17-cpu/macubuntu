"""
Tests for features/celina/celina_pkg/cli_bridge.py — Typed CLI Execution Gateway.

Covers:
  - CLI_ALLOWLIST structure (git/docker/systemctl/gh, read vs mutate split)
  - run_typed_cli read-only subcommands succeed without approval
  - Mutating subcommands denied when approval_cb is None
  - Forbidden flags blocked (--exec, -c, --upload-pack, etc.)
  - Unlisted tools blocked (rm, etc.)
  - Unlisted subcommands blocked
  - shell=False enforcement via command_runner
  - Zero import-time $HOME writes
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Allowlist structure
# ---------------------------------------------------------------------------


def test_cli_allowlist_has_expected_tools():
    """CB1: CLI_ALLOWLIST contains git, docker, systemctl, gh."""
    from celina_pkg.cli_bridge import CLI_ALLOWLIST

    for tool in ("git", "docker", "systemctl", "gh"):
        assert tool in CLI_ALLOWLIST, f"Missing tool {tool} in CLI_ALLOWLIST"


def test_cli_allowlist_read_vs_mutate_split():
    """CB2: Each tool has (read_only, mutating) frozensets."""
    from celina_pkg.cli_bridge import CLI_ALLOWLIST

    for tool, (ro, rw) in CLI_ALLOWLIST.items():
        assert isinstance(ro, frozenset), f"{tool} read subs should be frozenset"
        assert isinstance(rw, frozenset), f"{tool} mutate subs should be frozenset"
        assert ro.isdisjoint(rw), f"{tool} read/mutate sets overlap"
        # git status is read, git commit is mutate
        if tool == "git":
            assert "status" in ro
            assert "commit" in rw
        if tool == "systemctl":
            assert "status" in ro
            assert "restart" in rw
        if tool == "gh":
            assert len(rw) == 0, "gh should be read-only only"


def test_forbidden_flags_denylist():
    """CB3: _FORBIDDEN_FLAGS contains dangerous flags."""
    from celina_pkg.cli_bridge import _FORBIDDEN_FLAGS

    for flag in ("--exec", "-c", "--upload-pack", "--receive-pack", "--config", "--privileged", "--host"):
        assert flag in _FORBIDDEN_FLAGS


# ---------------------------------------------------------------------------
# run_typed_cli behavior
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: typed CLI execution fails closed without a trusted approval broker")
def test_read_only_subcommand_succeeds_without_approval():
    """CB4: run_typed_cli git status works with approval_cb=None."""
    from celina_pkg.cli_bridge import run_typed_cli

    with mock.patch("shutil.which", return_value="/usr/bin/git"):
        with mock.patch("celina_pkg.cli_bridge.command_runner.run_command") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="On branch main", stderr="")
            result = run_typed_cli("git", "status", approval_cb=None)
            assert "exit=0" in result
            assert "On branch main" in result
            mock_run.assert_called_once()
            # Verify shell=False (argv list, not string)
            call_args = mock_run.call_args
            argv = call_args[0][0]
            assert isinstance(argv, list)
            assert argv[0] == "git"
            assert argv[1] == "status"


def test_mutating_subcommand_denied_without_approval():
    """CB5: run_typed_cli git commit denied when approval_cb is None."""
    from celina_pkg.cli_bridge import run_typed_cli

    with mock.patch("shutil.which", return_value="/usr/bin/git"):
        result = run_typed_cli("git", "commit", ["-m", "x"], approval_cb=None)
        assert "Denied" in result or "denied" in result.lower()


def test_mutating_subcommand_blocked_when_approval_cb_raises():
    """CB6: Mutating subcommand denied if approval_cb raises (fail-closed)."""
    from celina_pkg.cli_bridge import run_typed_cli

    def boom(title, detail):
        raise RuntimeError("callback exploded")

    with mock.patch("shutil.which", return_value="/usr/bin/git"):
        result = run_typed_cli("git", "commit", ["-m", "x"], approval_cb=boom)
        assert "Denied" in result or "denied" in result.lower()


def test_unlisted_tool_blocked():
    """CB7: run_typed_cli rm is blocked (not in allowlist)."""
    from celina_pkg.cli_bridge import run_typed_cli

    result = run_typed_cli("rm", "-rf", ["/"], approval_cb=None)
    assert "Blocked" in result


def test_cli_prefix_stripped():
    """CB8: cli_ prefix is stripped from tool name."""
    from celina_pkg.cli_bridge import run_typed_cli

    with mock.patch("celina_pkg.cli_bridge._trusted_cli_dispatch_available", return_value=True):
        with mock.patch("shutil.which", return_value="/usr/bin/git"):
            with mock.patch("celina_pkg.cli_bridge.command_runner.run_command") as mock_run:
                mock_run.return_value = mock.Mock(returncode=0, stdout="main", stderr="")
                run_typed_cli("cli_git", "branch", approval_cb=None)
                call_args = mock_run.call_args
                argv = call_args[0][0]
                assert argv[0] == "git"


def test_forbidden_flag_blocked():
    """CB9: -c flag is blocked in typed CLI."""
    from celina_pkg.cli_bridge import run_typed_cli

    result = run_typed_cli("git", "status", ["-c", "core.pager=id"], approval_cb=None)
    assert "Blocked" in result and "-c" in result


def test_unlisted_subcommand_blocked():
    """CB10: Unlisted subcommand is blocked."""
    from celina_pkg.cli_bridge import run_typed_cli

    result = run_typed_cli("git", "reflog", approval_cb=None)
    assert "Blocked" in result


def test_tool_not_installed():
    """CB11: Tool not on PATH returns error."""
    from celina_pkg.cli_bridge import run_typed_cli

    with mock.patch("celina_pkg.cli_bridge._trusted_cli_dispatch_available", return_value=True):
        with mock.patch("shutil.which", return_value=None):
            result = run_typed_cli("git", "status", approval_cb=None)
            assert "not installed" in result


def test_systemctl_forces_user_flag():
    """CB12: systemctl always appends --user."""
    from celina_pkg.cli_bridge import run_typed_cli

    with mock.patch("celina_pkg.cli_bridge._trusted_cli_dispatch_available", return_value=True):
        with mock.patch("shutil.which", return_value="/usr/bin/systemctl"):
            with mock.patch("celina_pkg.cli_bridge.command_runner.run_command") as mock_run:
                mock_run.return_value = mock.Mock(returncode=0, stdout="active", stderr="")
                run_typed_cli("systemctl", "status", ["celina"], approval_cb=None)
                call_args = mock_run.call_args
                argv = call_args[0][0]
                assert "--user" in argv


def test_output_truncation():
    """CB13: Long output is truncated at 4000 chars."""
    from celina_pkg.cli_bridge import run_typed_cli

    with mock.patch("celina_pkg.cli_bridge._trusted_cli_dispatch_available", return_value=True):
        with mock.patch("shutil.which", return_value="/usr/bin/git"):
            with mock.patch("celina_pkg.cli_bridge.command_runner.run_command") as mock_run:
                long_out = "x" * 5000
                mock_run.return_value = mock.Mock(returncode=0, stdout=long_out, stderr="")
                result = run_typed_cli("git", "log", approval_cb=None)
                assert "...[truncated]" in result
                assert len(result) < 5200


# ---------------------------------------------------------------------------
# Additional coverage: dash-dash stripping else branch + no-truncation branch
# ---------------------------------------------------------------------------


def test_strip_leading_dash_dash_else_branch():
    """CB15: _strip_leading_dash_dash returns args unchanged when first is not --."""
    from celina_pkg.cli_bridge import _strip_leading_dash_dash

    result = _strip_leading_dash_dash(["git", "log"])
    assert result == ["git", "log"]


def test_strip_leading_dash_dash_empty_list():
    """CB16: _strip_leading_dash_dash handles empty list."""
    from celina_pkg.cli_bridge import _strip_leading_dash_dash

    result = _strip_leading_dash_dash([])
    assert result == []


def test_output_not_truncated():
    """CB17: Output <= 4000 chars is not truncated (False branch)."""
    from celina_pkg.cli_bridge import run_typed_cli

    with mock.patch("celina_pkg.cli_bridge._trusted_cli_dispatch_available", return_value=True):
        with mock.patch("shutil.which", return_value="/usr/bin/git"):
            with mock.patch("celina_pkg.cli_bridge.command_runner.run_command") as mock_run:
                short_out = "x" * 100
                mock_run.return_value = mock.Mock(returncode=0, stdout=short_out, stderr="")
                result = run_typed_cli("git", "log", approval_cb=None)
                assert "...[truncated]" not in result
                assert short_out in result


def test_empty_tool_name_blocked():
    """CB18: Empty tool name is blocked (line 82-83)."""
    from celina_pkg.cli_bridge import run_typed_cli

    result = run_typed_cli("", "log", approval_cb=None)
    assert "Blocked" in result


def test_strip_leading_dash_dash_true_branch():
    """CB19: _strip_leading_dash_dash drops leading -- (True branch)."""
    from celina_pkg.cli_bridge import _strip_leading_dash_dash

    result = _strip_leading_dash_dash(["--", "git", "log"])
    assert result == ["git", "log"]


def test_run_typed_cli_with_dash_dash_extra_args():
    """CB20: run_typed_cli with -- in extra_args hits dash-dash path."""
    from celina_pkg.cli_bridge import run_typed_cli

    with mock.patch("shutil.which", return_value="/usr/bin/git"):
        with mock.patch("celina_pkg.cli_bridge.command_runner.run_command") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            run_typed_cli("git", "log", extra_args=["--", "oneline"], approval_cb=None)


def test_mutating_verify_and_consume_fails():
    """CB21: Mutating subcommand fails when verify_and_consume returns False (lines 121-123)."""
    from celina_pkg.cli_bridge import run_typed_cli

    def approve(title, detail):
        return True

    with mock.patch("shutil.which", return_value="/usr/bin/git"):
        with mock.patch(
            "celina_pkg.cli_bridge.approval.verify_and_consume",
            return_value=(False, "Verification failed: token mismatch"),
        ):
            result = run_typed_cli("git", "commit", ["-m", "x"], approval_cb=approve)
            assert "Verification failed" in result or "Denied" in result


# ---------------------------------------------------------------------------
# Zero import-time side effects
# ---------------------------------------------------------------------------


def test_zero_home_writes_on_import():
    """CB14: Importing cli_bridge writes nothing to $HOME."""
    home = Path.home()
    before = set(home.iterdir())
    import importlib

    import celina_pkg.cli_bridge as cb

    importlib.reload(cb)
    after = set(home.iterdir())
    assert before == after, f"Import created files in $HOME: {after - before}"
