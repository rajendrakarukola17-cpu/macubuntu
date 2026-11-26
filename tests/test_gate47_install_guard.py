"""Direct installer fast path is disabled until broker-owned execution exists."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import tools


@pytest.mark.parametrize("prompt", ["install spotify", "install example-app", "download htop"])
@pytest.mark.parametrize("callback_present", [False, True])
def test_install_requests_fail_before_search_approval_or_execution(monkeypatch, prompt, callback_present):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    monkeypatch.setattr(tools, "_search_flathub_app", fake)
    monkeypatch.setattr(tools, "_check_apt_package", fake)
    monkeypatch.setattr(tools, "open_url_safe", fake)
    monkeypatch.setattr(tools.shutil, "which", fake)
    monkeypatch.setattr(tools.command_runner, "run_command", fake)
    cb = (lambda *_args: calls.append("approval") or True) if callback_present else None
    result = tools.handle_fast_install_intent(prompt, approval_cb=cb, status_cb=fake)
    assert result.startswith("Denied:") and "broker" in result.lower()
    assert not calls


def test_install_dry_run_and_unrelated_input_remain_non_actuating(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("dry-run triggered a command or search")

    monkeypatch.setattr(tools, "_search_flathub_app", unexpected)
    monkeypatch.setattr(tools, "_check_apt_package", unexpected)
    monkeypatch.setattr(tools, "open_url_safe", unexpected)
    monkeypatch.setattr(tools.command_runner, "run_command", unexpected)
    assert "Dry-Run" in tools.handle_fast_install_intent("install spotify", dry_run=True)
    assert "Dry-Run" in tools.handle_fast_install_intent("install example-app", dry_run=True)
    assert tools.handle_fast_install_intent("hello there") is None


def test_router_install_denial_does_not_claim_command_executed(monkeypatch):
    from celina_pkg import router

    monkeypatch.setattr(router, "try_gear0_math", lambda _text: None)
    instance = router.CelinaRouter.__new__(router.CelinaRouter)
    reply, source = instance._tier0_dispatch("install spotify", False, lambda *_args: True,
                                              None, None, None)
    assert reply.startswith("Denied:")
    assert source == "install"
    assert instance.last_trace.tools_called == []
