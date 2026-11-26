"""Fast-intent fallback must not invoke direct actuators without a broker."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import tools


@pytest.mark.parametrize("prompt", [
    "mute", "volume 42", "brightness 50", "pause music", "dark mode", "lock screen",
    "turn off wifi", "search google for fixture", "open firefox", "play music",
])
def test_fast_intent_has_no_direct_actuation(monkeypatch, prompt):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return "fake action"

    monkeypatch.setattr(tools, "set_volume", fake)
    monkeypatch.setattr(tools, "set_brightness", fake)
    monkeypatch.setattr(tools, "control_media", fake)
    monkeypatch.setattr(tools, "control_connectivity", fake)
    monkeypatch.setattr(tools, "open_url_safe", fake)
    monkeypatch.setattr(tools, "open_application", fake)
    monkeypatch.setattr(tools, "_open_media_play", fake)
    monkeypatch.setattr(tools.shutil, "which", lambda _name: "/fixture")
    monkeypatch.setattr(tools.command_runner, "run_command", fake)
    assert tools.handle_fast_intent(prompt, approval_cb=lambda *_args: True) is None
    assert not calls


def test_fast_time_remains_a_non_actuating_response():
    assert tools.handle_fast_intent("what time is it").startswith("It is currently ")


def test_pure_fast_calculation_and_conversion_remain_available():
    assert tools.handle_fast_intent("6 * 7") == "🧮 6 * 7 = 42"
    assert tools.handle_fast_intent("10 km to miles").startswith("🔄 10 km =")
