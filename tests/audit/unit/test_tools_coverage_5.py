"""
tests/audit/unit/test_tools_coverage_5.py

Phase 7d — TASK 1a: tools.py branch coverage tests (round 5).
Targets compose_message, handle_fast_install_intent, run_python_sandbox.

No real network calls. Mocks approval, shutil.which, command_runner, subprocess,
and launch_detached/open_application/set_clipboard.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error="", message="", timed_out=False, returncode=0):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.message = message
        self.timed_out = timed_out
        self.returncode = returncode


# ---------------------------------------------------------------------------
# compose_message
# ---------------------------------------------------------------------------


def test_compose_message_approval_denied():
    """CB: compose_message rejects when approval denied."""
    result = tools.compose_message("whatsapp", "+1234567890", "hello", approval_cb=lambda *a, **k: False)
    assert "Cancelled" in result or "denied" in result.lower() or "Error" in result


def test_compose_message_whatsapp(monkeypatch):
    """CB: compose_message whatsapp channel."""
    launched = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: launched.append(cmd) or True)
    monkeypatch.setattr(tools, "set_clipboard", lambda text: None)
    result = tools.compose_message(
        "whatsapp", "+1234567890", "hello there", approval_cb=_always_approve
    )
    assert "WhatsApp" in result
    assert launched == [["xdg-open", "https://wa.me/1234567890?text=hello%20there"]]


def test_compose_message_whatsapp_no_digits(monkeypatch):
    """CB: compose_message whatsapp with no digits in recipient."""
    launched = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: launched.append(cmd) or True)
    monkeypatch.setattr(tools, "set_clipboard", lambda text: None)
    result = tools.compose_message(
        "whatsapp", "notanumber", "hello", approval_cb=_always_approve
    )
    assert "WhatsApp" in result
    assert launched == [["xdg-open", "https://web.whatsapp.com/send?text=hello"]]


def test_compose_message_email(monkeypatch):
    """CB: compose_message email channel."""
    launched = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: launched.append(cmd) or True)
    monkeypatch.setattr(tools, "set_clipboard", lambda text: None)
    result = tools.compose_message(
        "email", "alice@example.com", "Hello", subject="Hi", approval_cb=_always_approve
    )
    assert "email composer" in result.lower()
    assert launched[0][0] == "xdg-open"
    assert "mailto:" in launched[0][1]
    assert "subject=Hi" in launched[0][1]
    assert "body=Hello" in launched[0][1]


def test_compose_message_telegram(monkeypatch):
    """CB: compose_message telegram channel."""
    launched = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: launched.append(cmd) or True)
    monkeypatch.setattr(tools, "set_clipboard", lambda text: None)
    result = tools.compose_message(
        "telegram", "@handle", "hello", approval_cb=_always_approve
    )
    assert "Telegram" in result
    assert launched == [["xdg-open", "https://t.me/handle"]]


def test_compose_message_telegram_no_handle(monkeypatch):
    """CB: compose_message telegram with no handle."""
    launched = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: launched.append(cmd) or True)
    monkeypatch.setattr(tools, "set_clipboard", lambda text: None)
    result = tools.compose_message(
        "telegram", "", "hello", approval_cb=_always_approve
    )
    assert "Telegram" in result


def test_compose_message_recipient_app(monkeypatch):
    """CB: compose_message with recipient opens application."""
    opened = []
    monkeypatch.setattr(tools, "open_application", lambda name: opened.append(name) or "opened")
    monkeypatch.setattr(tools, "set_clipboard", lambda text: None)
    result = tools.compose_message("other", "firefox", "hello", approval_cb=_always_approve)
    assert opened == ["firefox"]
    assert "clipboard" in result


def test_compose_message_no_recipient(monkeypatch):
    """CB: compose_message with no recipient just copies to clipboard."""
    monkeypatch.setattr(tools, "set_clipboard", lambda text: None)
    result = tools.compose_message("other", "", "hello", approval_cb=_always_approve)
    assert "clipboard" in result


def test_compose_message_no_wl_copy(monkeypatch):
    """CB: compose_message when wl-copy not available skips clipboard."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.compose_message("email", "a@b.com", "hi", subject="s", approval_cb=_always_approve)
    assert "email composer" in result.lower()


# ---------------------------------------------------------------------------
# run_python_sandbox
# ---------------------------------------------------------------------------


def test_run_python_sandbox_empty_code():
    """CB: run_python_sandbox rejects empty code."""
    result = tools.run_python_sandbox("")
    assert "Empty" in result or "error" in result.lower()


def test_run_python_sandbox_empty():
    """CB: run_python_sandbox rejects empty code."""
    result = tools.run_python_sandbox("")
    assert "Empty Python code" in result


def test_run_python_sandbox_too_long(monkeypatch):
    """CB: run_python_sandbox rejects code over 16 KB."""
    result = tools.run_python_sandbox("x" * 20000)
    assert "exceeds 16 KB" in result


def test_run_python_sandbox_no_approval():
    """CB: run_python_sandbox requires approval callback."""
    result = tools.run_python_sandbox("print(1)")
    assert "Denied" in result or "approval" in result.lower()


def test_run_python_sandbox_bwrap_success(monkeypatch):
    """CB: run_python_sandbox executes in bwrap sandbox."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools, "_build_bwrap_command", lambda cmd, **k: ["bwrap", "--", "python3", "-c", cmd])
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True, stdout="42", returncode=0),
    )
    def fake_request(tool, args, cb, title, target, extra_warning=None):
        return MagicMock(), None
    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *a, **k: (True, None))
    result = tools.run_python_sandbox("print(42)", approval_cb=_always_approve)
    assert "[bwrap" in result.lower() or "sandbox" in result.lower()


def test_run_python_sandbox_unsandboxed(monkeypatch):
    """CB: run_python_sandbox unsandboxed path when bwrap unavailable."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    def fake_request(tool, args, cb, title, target, extra_warning=None):
        return MagicMock(), None
    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *a, **k: (True, None))
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True, stdout="42", returncode=0),
    )
    result = tools.run_python_sandbox("print(42)", approval_cb=_always_approve)
    assert "42" in result


# ---------------------------------------------------------------------------
# handle_fast_install_intent
# ---------------------------------------------------------------------------


def test_handle_fast_install_intent_none():
    """CB: handle_fast_install_intent returns None for non-install text."""
    result = tools.handle_fast_install_intent("just a regular message")
    assert result is None


def test_handle_fast_install_intent_install_spotify(monkeypatch):
    """CB: handle_fast_install_intent handles 'install spotify'."""
    monkeypatch.setattr(tools, "_search_flathub_app", lambda q: ("Spotify", "com.spotify.Client"))
    monkeypatch.setattr(tools, "_check_apt_package", lambda q: None)
    launched = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: launched.append(cmd) or True)
    result = tools.handle_fast_install_intent("install spotify")
    assert result is not None


def test_handle_fast_install_intent_install_via_apt(monkeypatch):
    """CB: handle_fast_install_intent installs via apt when flathub fails."""
    monkeypatch.setattr(tools, "_search_flathub_app", lambda q: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda q: "ffmpeg")
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/apt-get" if cmd == "apt-get" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True),
    )
    def fake_request(tool, args, cb, title, target, extra_warning=None):
        return MagicMock(), None
    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *a, **k: (True, None))
    result = tools.handle_fast_install_intent("install ffmpeg")
    assert result is not None


@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: live fast-install fails closed without a broker")
def test_handle_fast_install_intent_not_found(monkeypatch):
    """CB: handle_fast_install_intent reports not found."""
    monkeypatch.setattr(tools, "_search_flathub_app", lambda q: None)
    monkeypatch.setattr(tools, "_check_apt_package", lambda q: None)
    result = tools.handle_fast_install_intent("install nonexistent_app_xyz")
    assert "No exact Flathub or APT package matched" in result


def _always_approve(action, params, cb=None, title="", target=""):
    class Token:
        pass
    return Token(), None
