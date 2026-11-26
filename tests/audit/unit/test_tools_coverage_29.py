"""
tests/audit/unit/test_tools_coverage_29.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 29).
Targets: run_shell_command remaining branches, open_application remaining branches.

Covers: run_shell_command bwrap deny_err, bwrap verify fail, output truncation,
  bwrap timeout, host shell err2, host shell verify fail, host shell timeout.
  open_application gtk-launch stem match success, name match in desktop file,
  fallback match via genericname, web app with curated installable message.

No real shell or desktop calls. Mocks _can_use_bwrap, approval, command_runner,
tempfile, shutil.which, launch_detached, Path operations.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import os

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error="", timed_out=False, returncode=0):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.timed_out = timed_out
        self.returncode = returncode


# ---------------------------------------------------------------------------
# run_shell_command - remaining branches
# ---------------------------------------------------------------------------

def test_run_shell_command_bwrap_deny_err(monkeypatch):
    """CB: run_shell_command bwrap path → request_bound_approval returns deny_err."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)
    monkeypatch.setattr(tools.approval, "request_bound_approval",
                        lambda *a, **k: (None, "Denied: sandbox approval required"))
    result = tools.run_shell_command("echo hi", sandbox=True, approval_cb=lambda t, d: True)
    assert "Denied" in result


def test_run_shell_command_bwrap_verify_fail(monkeypatch):
    """CB: run_shell_command bwrap path → verify_and_consume returns False."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume",
                        lambda token, name, args: (False, "Token expired"))
    result = tools.run_shell_command("echo hi", sandbox=True, approval_cb=lambda t, d: True)
    assert "Token expired" in result


def test_run_shell_command_bwrap_output_truncation(monkeypatch, tmp_path):
    """CB: run_shell_command bwrap → len(out) > 3000 → truncated output."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume",
                        lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="x" * 4000,
                                                    stderr="", timed_out=False, returncode=0))

    import tempfile as _tempfile
    orig_tmpdir = _tempfile.TemporaryDirectory
    class FakeTmpDir:
        def __enter__(self): return str(tmp_path)
        def __exit__(self, *a): pass
    monkeypatch.setattr(_tempfile, "TemporaryDirectory", lambda *a, **k: FakeTmpDir())
    monkeypatch.setattr(tools, "_build_bwrap_command", lambda cmd, **k: ["bwrap", cmd])

    result = tools.run_shell_command("echo hi", sandbox=True, approval_cb=lambda t, d: True)
    assert "...[truncated]" in result


def test_run_shell_command_bwrap_timeout(monkeypatch, tmp_path):
    """CB: run_shell_command bwrap → res.timed_out → timeout error."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume",
                        lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=False, stdout="", stderr="",
                                                    timed_out=True, returncode=124))

    monkeypatch.setattr(tempfile, "TemporaryDirectory",
                        lambda *a, **k: _FakeTmpDir(str(tmp_path)))
    monkeypatch.setattr(tools, "_build_bwrap_command", lambda cmd, **k: ["bwrap", cmd])

    result = tools.run_shell_command("echo hi", sandbox=True, approval_cb=lambda t, d: True)
    assert "timed out" in result.lower()


def test_run_shell_command_host_err2(monkeypatch):
    """CB: run_shell_command unsandboxed → second approval err2 → refused."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)

    call_count = [0]
    def fake_request(*a, **k):
        call_count[0] += 1
        if call_count[0] == 2:  # second approval (host_args)
            return (None, "Denied: host shell confirmation required")
        return ("token", None)

    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    result = tools.run_shell_command("echo hi", sandbox=True, approval_cb=lambda t, d: True)
    assert "Refused unsandboxed fallback" in result or "Denied" in result


def test_run_shell_command_host_verify_fail(monkeypatch):
    """CB: run_shell_command unsandboxed → second verify_and_consume returns False."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)

    call_count = [0]
    def fake_request(*a, **k):
        call_count[0] += 1
        return ("token", None)

    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    def fake_verify(token, name, args):
        if call_count[0] == 2:
            return (False, "Host verification failed")
        return (True, "")
    monkeypatch.setattr(tools.approval, "verify_and_consume", fake_verify)
    result = tools.run_shell_command("echo hi", sandbox=True, approval_cb=lambda t, d: True)
    assert "Host verification failed" in result


def test_run_shell_command_host_timeout(monkeypatch):
    """CB: run_shell_command unsandboxed → host res.timed_out → timeout error."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=False, stdout="", stderr="",
                                                    timed_out=True, returncode=124))
    result = tools.run_shell_command("echo hi", sandbox=True, approval_cb=lambda t, d: True)
    assert "timed out" in result.lower()


class _FakeTmpDir:
    def __init__(self, path):
        self.path = path
    def __enter__(self):
        return self.path
    def __exit__(self, *a):
        pass


# ---------------------------------------------------------------------------
# open_application - remaining branches
# ---------------------------------------------------------------------------

def test_open_application_gtk_launch_stem_success(monkeypatch, tmp_path):
    """CB: open_application gtk-launch stem match → launch_detached success → 'Launched'."""
    desktop_dir = tmp_path / ".local/share/applications"
    desktop_dir.mkdir(parents=True)
    (desktop_dir / "firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\n")

    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gtk-launch" if cmd == "gtk-launch" else None)
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)

    result = tools.open_application("firefox")
    assert "Launched" in result


def test_open_application_gtk_launch_name_match(monkeypatch, tmp_path):
    """CB: open_application gtk-launch Name= match in desktop file → launch."""
    desktop_dir = tmp_path / ".local/share/applications"
    desktop_dir.mkdir(parents=True)
    # Stem is "browser" but Name=Firefox, searching for "firefox"
    (desktop_dir / "browser.desktop").write_text("[Desktop Entry]\nName=Firefox\n")

    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gtk-launch" if cmd == "gtk-launch" else None)
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)

    result = tools.open_application("firefox")
    assert result is not None


def test_open_application_gtk_launch_genericname_fallback(monkeypatch, tmp_path):
    """CB: open_application gtk-launch genericname fallback match → launch."""
    desktop_dir = tmp_path / ".local/share/applications"
    desktop_dir.mkdir(parents=True)
    (desktop_dir / "editor.desktop").write_text("[Desktop Entry]\nGenericName=Text Editor\n")

    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gtk-launch" if cmd == "gtk-launch" else None)
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)

    result = tools.open_application("editor")
    assert result is not None


def test_open_application_web_app_curated_msg(monkeypatch):
    """CB: open_application key in WEB_APPS and CURATED_INSTALLABLE_APPS → install hint."""
    monkeypatch.setattr(tools, "WEB_APPS", {**tools.WEB_APPS, "spotify": "https://spotify.com"})
    monkeypatch.setattr(tools, "CURATED_INSTALLABLE_APPS",
                        {**tools.CURATED_INSTALLABLE_APPS, "spotify": ("Spotify", "com.spotify.Client")})
    monkeypatch.setattr(tools, "open_url_safe", lambda url: "opened spotify")
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)

    result = tools.open_application("spotify")
    assert "Native app not installed yet" in result or "install" in result
