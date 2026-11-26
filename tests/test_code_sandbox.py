"""
Regression tests for Update 17: Sandboxed Code Execution Tool (code_sandbox).

Tests cover:
  - Empty code rejection
  - Catastrophic command blocking
  - Missing approval callback denial
  - Python sandbox execution (mocked)
  - Bash sandbox execution (mocked)
  - Timeout handling
  - Output cap enforcement
  - Language validation
  - Approval gating flow
  - systemd-run celina-sandbox.slice command builder
  - systemd-available headless fallback
"""

import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure celina_pkg is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macubuntu" / "features" / "celina"))

from celina_pkg import code_sandbox  # noqa: E402

# Skip bwrap-dependent execution tests when bwrap is not installed
_skip_no_bwrap = pytest.mark.skipif(
    shutil.which("bwrap") is None,
    reason="bwrap not installed — sandboxed execution tests require bwrap",
)


def test_empty_code_rejected():
    """Empty code should return an error, not execute."""
    result = code_sandbox.code_sandbox("", language="python", approval_cb=lambda *a, **k: True)
    assert "Error: Empty code snippet." in result


def test_empty_code_bash_rejected():
    """Empty bash code should return an error."""
    result = code_sandbox.code_sandbox("", language="bash", approval_cb=lambda *a, **k: True)
    assert "Error: Empty code snippet." in result


def test_catastrophic_python_blocked():
    """rm -rf / in Python should be blocked."""
    code = "import os; os.system('rm -rf /')"
    result = code_sandbox.code_sandbox(code, language="python", approval_cb=lambda *a, **k: True)
    assert "Blocked: Code matched catastrophic safety denylist." in result


def test_catastrophic_bash_blocked():
    """rm -rf /* in bash should be blocked."""
    code = "rm -rf /*"
    result = code_sandbox.code_sandbox(code, language="bash", approval_cb=lambda *a, **k: True)
    assert "Blocked: Code matched catastrophic safety denylist." in result


def test_missing_approval_denied():
    """Without approval_cb, execution should be denied."""
    result = code_sandbox.code_sandbox("print(1+1)", language="python", approval_cb=None)
    assert "Denied: Sandboxed code execution requires interactive user approval." in result


def test_unsupported_language():
    """Unsupported language should return an error."""
    result = code_sandbox.code_sandbox("print(1)", language="javascript", approval_cb=lambda *a, **k: True)
    assert "Error: Unsupported language" in result


@_skip_no_bwrap
def test_python_execution_success():
    """Successful Python execution should return output with exit=0 tag."""
    code = "print(42)"
    mock_cb = MagicMock(return_value=True)
    with patch.object(code_sandbox.shutil, "which", return_value="/usr/bin/bwrap"), patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(stdout="42\n", stderr="", returncode=0, timed_out=False)
        result = code_sandbox.code_sandbox(code, language="python", timeout=5, approval_cb=mock_cb)
        assert "Verified OK (exit=0)" in result
        assert "42" in result


@_skip_no_bwrap
def test_python_execution_error():
    """Python execution with non-zero exit should return error tag."""
    code = "raise ValueError('test error')"
    mock_cb = MagicMock(return_value=True)
    with patch.object(code_sandbox.shutil, "which", return_value="/usr/bin/bwrap"), patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="Traceback (most recent call last):\n  ...\nValueError: test error", returncode=1, timed_out=False)
        result = code_sandbox.code_sandbox(code, language="python", timeout=5, approval_cb=mock_cb)
        assert "Error:" in result
        assert "ValueError" in result


@_skip_no_bwrap
def test_timeout_handling():
    """Timeout should return timeout error."""
    code = "import time; time.sleep(100)"
    mock_cb = MagicMock(return_value=True)
    with patch.object(code_sandbox.shutil, "which", return_value="/usr/bin/bwrap"), patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=124, timed_out=True)
        result = code_sandbox.code_sandbox(code, language="python", timeout=5, approval_cb=mock_cb)
        assert "timed out" in result.lower()


@_skip_no_bwrap
def test_output_cap():
    """Output should be capped at 3000 chars."""
    code = "print('x' * 5000)"
    mock_cb = MagicMock(return_value=True)
    with patch.object(code_sandbox.shutil, "which", return_value="/usr/bin/bwrap"), patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(stdout="x" * 5000, stderr="", returncode=0, timed_out=False)
        result = code_sandbox.code_sandbox(code, language="python", timeout=5, approval_cb=mock_cb)
        assert "[truncated" in result
        assert len(result) < 5000


@_skip_no_bwrap
def test_bash_execution_success():
    """Successful bash execution should return output."""
    code = "echo hello"
    mock_cb = MagicMock(return_value=True)
    with patch.object(code_sandbox.shutil, "which", return_value="/usr/bin/bwrap"), patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0, timed_out=False)
        result = code_sandbox.code_sandbox(code, language="bash", timeout=5, approval_cb=mock_cb)
        assert "Verified OK (exit=0)" in result
        assert "hello" in result


def test_approval_denied():
    """When approval_cb returns False, execution should be denied/cancelled."""
    code = "print(1)"
    mock_cb = MagicMock(return_value=False)
    result = code_sandbox.code_sandbox(code, language="python", timeout=5, approval_cb=mock_cb)
    assert "Denied" in result or "denied" in result.lower() or "cancelled" in result.lower() or "refus" in result.lower()


def test_code_sandbox_is_callable():
    """code_sandbox should be a callable function."""
    assert callable(code_sandbox.code_sandbox)


def test_build_systemd_run_bwrap_prefix_uses_celina_sandbox_slice():
    """systemd-run prefix must wrap bwrap under celina-sandbox.slice."""
    prefix = code_sandbox._build_systemd_run_bwrap_prefix()
    assert prefix[:4] == ["systemd-run", "--user", "--quiet", "--scope"]
    assert "--slice=celina-sandbox.slice" in prefix
    assert "--" in prefix  # separator before bwrap


def test_build_systemd_run_bwrap_prefix_custom_slice():
    """Custom slice name is honored."""
    prefix = code_sandbox._build_systemd_run_bwrap_prefix(slice_name="custom.slice")
    assert "--slice=custom.slice" in prefix


def test_bwrap_python_cmd_prepends_systemd_prefix():
    """When systemd_prefix is provided, it is prepended to the bwrap command."""
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "snippet.py"
        script.write_text("print(1)\n", encoding="utf-8")
        prefix = ["systemd-run", "--user", "--quiet", "--scope", "--slice=celina-sandbox.slice", "--"]
        cmd = code_sandbox._build_bwrap_python_cmd(script, 5, systemd_prefix=prefix)
        assert cmd[:len(prefix)] == prefix
        assert "bwrap" in cmd[len(prefix)]


def test_bwrap_bash_cmd_prepends_systemd_prefix():
    """When systemd_prefix is provided, it is prepended to the bwrap command."""
    prefix = ["systemd-run", "--user", "--quiet", "--scope", "--slice=celina-sandbox.slice", "--"]
    cmd = code_sandbox._build_bwrap_bash_cmd("echo hi", systemd_prefix=prefix)
    assert cmd[:len(prefix)] == prefix
    assert "bwrap" in cmd[len(prefix)]


def test_bwrap_cmd_without_systemd_prefix():
    """Without systemd_prefix, the command starts directly with bwrap."""
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "snippet.py"
        script.write_text("print(1)\n", encoding="utf-8")
        cmd = code_sandbox._build_bwrap_python_cmd(script, 5)
        assert cmd[0] == "bwrap"
        assert "systemd-run" not in cmd


def test_systemd_run_available_fallback_headless(monkeypatch):
    """_systemd_run_available returns False when no DBUS_SESSION_BUS_ADDRESS."""
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    assert code_sandbox._systemd_run_available() is False


def test_bwrap_python_cmd_no_ro_bind_root():
    """bwrap Python cmd must NOT use --ro-bind / / (leaks /etc, /var, /opt)."""
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "snippet.py"
        script.write_text("print(1)\n", encoding="utf-8")
        work = Path(td) / "work"
        work.mkdir()
        cmd = code_sandbox._build_bwrap_python_cmd(script, 5, work_dir=work)
        assert "--ro-bind" in cmd, "bwrap cmd should have --ro-bind entries"
        # Find all --ro-bind sources
        ro_sources = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--ro-bind" and i + 1 < len(cmd)]
        assert "/" not in ro_sources, "--ro-bind / / must NOT be present (leaks host root)"
        # Should have minimal runtime dirs instead
        assert any(s in ("/usr", "/bin", "/lib") for s in ro_sources), \
            "Should bind minimal runtime dirs instead of /"


def test_bwrap_bash_cmd_mounts_workdir():
    """bwrap Bash cmd must bind the real workdir at /tmp/work (not HOME=/tmp/work without mount)."""
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "bash_work"
        work.mkdir()
        cmd = code_sandbox._build_bwrap_bash_cmd("echo hi", work_dir=work)
        assert "--bind" in cmd or "--ro-bind" in cmd, "bwrap cmd should bind workdir"
        bind_sources = [cmd[i + 1] for i, a in enumerate(cmd) if a in ("--bind", "--ro-bind") and i + 1 < len(cmd)]
        assert str(work.resolve()) in bind_sources, \
            f"Workdir {work.resolve()} must be bind-mounted into sandbox"


if __name__ == "__main__":
    test_empty_code_rejected()
    test_empty_code_bash_rejected()
    test_catastrophic_python_blocked()
    test_catastrophic_bash_blocked()
    test_missing_approval_denied()
    test_unsupported_language()
    test_python_execution_success()
    test_python_execution_error()
    test_timeout_handling()
    test_output_cap()
    test_bash_execution_success()
    test_approval_denied()
    test_code_sandbox_is_callable()
    test_bwrap_python_cmd_no_ro_bind_root()
    test_bwrap_bash_cmd_mounts_workdir()
    print("All code_sandbox tests passed!")
