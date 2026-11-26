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
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure celina_pkg is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macubuntu" / "features" / "celina"))

from celina_pkg import code_sandbox  # noqa: E402


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


def test_python_execution_success(mock_approval=True):
    """Successful Python execution should return output with exit=0 tag."""
    code = "print(42)"
    mock_cb = MagicMock(return_value=True)

    with patch.object(code_sandbox.shutil, "which", return_value="/usr/bin/bwrap"), \
         patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="42\n",
            stderr="",
            returncode=0,
            timed_out=False,
        )
        result = code_sandbox.code_sandbox(code, language="python", timeout=5, approval_cb=mock_cb)
        assert "Verified OK (exit=0)" in result
        assert "42" in result


def test_python_execution_error():
    """Python execution with non-zero exit should return error tag."""
    code = "raise ValueError('test error')"
    mock_cb = MagicMock(return_value=True)

    with patch.object(code_sandbox.shutil, "which", return_value=None), \
         patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="Traceback (most recent call last):\n  ...\nValueError: test error",
            returncode=1,
            timed_out=False,
        )
        result = code_sandbox.code_sandbox(code, language="python", timeout=5, approval_cb=mock_cb)
        assert "Error:" in result
        assert "ValueError" in result


def test_timeout_handling():
    """Timeout should return timeout error."""
    code = "import time; time.sleep(100)"
    mock_cb = MagicMock(return_value=True)

    with patch.object(code_sandbox.shutil, "which", return_value=None), \
         patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="",
            returncode=124,
            timed_out=True,
        )
        result = code_sandbox.code_sandbox(code, language="python", timeout=5, approval_cb=mock_cb)
        assert "timed out" in result.lower()


def test_output_cap():
    """Output should be capped at 3000 chars."""
    code = "print('x' * 5000)"
    mock_cb = MagicMock(return_value=True)

    with patch.object(code_sandbox.shutil, "which", return_value=None), \
         patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="x" * 5000,
            stderr="",
            returncode=0,
            timed_out=False,
        )
        result = code_sandbox.code_sandbox(code, language="python", timeout=5, approval_cb=mock_cb)
        assert "[truncated" in result
        assert len(result) < 5000


def test_bash_execution_success():
    """Successful bash execution should return output."""
    code = "echo hello"
    mock_cb = MagicMock(return_value=True)

    with patch.object(code_sandbox.shutil, "which", return_value="/usr/bin/bwrap"), \
         patch.object(code_sandbox.command_runner, "run_command") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="hello\n",
            stderr="",
            returncode=0,
            timed_out=False,
        )
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
    print("All code_sandbox tests passed!")
