"""Non-actuating tests for the unbrokered everyday-agent routing bypass."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import everyday_agents


@pytest.mark.parametrize("text", [
    "good morning", "add todo buy milk", "take a note fixture", "brightness up",
    "play now", "turn off wifi", "sleep pc", "create folder fixture on desktop",
    "open my downloads", "log me into spotify", "enable local mcp",
])
def test_everyday_dispatch_has_no_live_side_effects_without_broker(monkeypatch, tmp_path, text):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return "fake action"

    for name in ("run_daily_routine", "add_todo", "add_note", "enable_local_zero_login_mcp"):
        monkeypatch.setattr(everyday_agents, name, fake)
    for name in ("set_brightness", "control_media", "control_connectivity", "launch_detached"):
        monkeypatch.setattr(everyday_agents.tools, name, fake)
    monkeypatch.setattr(everyday_agents.keychain_login, "fill_saved_login", fake)
    monkeypatch.setattr(everyday_agents.command_runner, "run_command", fake)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    callback = lambda *_args: calls.append("approval") or True
    result = everyday_agents.dispatch_everyday_task(text, approval_cb=callback, dry_run=False)
    assert result is None
    assert not calls
    assert list(tmp_path.iterdir()) == []


def test_selected_dry_run_example_and_conceptual_question(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("dry run invoked input actuator")
    monkeypatch.setattr(everyday_agents.tools, "set_brightness", unexpected)
    result = everyday_agents.dispatch_everyday_task("brightness up", dry_run=True)
    assert result and "Dry-Run" in result[1]
    assert everyday_agents.dispatch_everyday_task("how do i turn on dark mode?", dry_run=True) is None


def test_direct_mcp_enabling_denies_without_touching_config(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    original = '{"servers":{"playwright":{"enabled":false}}}'
    path.write_text(original)
    calls = []
    monkeypatch.setattr(everyday_agents.shutil, "which", lambda name: calls.append(name) or "/fixture")
    monkeypatch.setattr(everyday_agents.config, "atomic_write_private_text",
                        lambda *args: calls.append("write"))
    result = everyday_agents.enable_local_zero_login_mcp(mcp_path=path)
    assert result.startswith("Denied:") and "broker" in result.lower()
    assert path.read_text() == original
    assert not calls
