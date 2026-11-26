"""Computer-use mission approvals must not auto-approve individual actions."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import computer_use


@pytest.mark.parametrize("action,args", [
    ("open_app", {"app_name": "Text Editor"}),
    ("click_text", {"target_text": "Save"}),
    ("type", {"text": "hello"}),
    ("hotkey", {"keys": "ctrl+s"}),
    ("scroll", {"direction": "down", "amount": 1}),
])
def test_each_child_receives_real_approval_callback(monkeypatch, tmp_path, action, args):
    monkeypatch.setattr(computer_use.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(computer_use.automation, "clear_stop", lambda: None)
    monkeypatch.setattr(computer_use.automation, "is_stop_requested", lambda: False)
    monkeypatch.setattr(computer_use.automation, "get_active_window_info", lambda: {"app": "Editor", "title": "Note"})
    monkeypatch.setattr(computer_use.automation, "perceive_desktop", lambda **_: ("Editor", "fp"))
    monkeypatch.setattr(computer_use, "_query_planner", lambda *_a, **_k: ({"action": action, "args": args}, None))
    monkeypatch.setattr(computer_use, "_save_checkpoint", lambda *a, **k: tmp_path / "unused")
    monkeypatch.setattr(computer_use.time, "sleep", lambda _: None)
    decisions = []
    calls = []

    def human(title, detail):
        decisions.append((title, detail))
        return len(decisions) == 1

    def executor(tool, arguments, callback):
        calls.append((tool, callback("Child action", "Review")))
        return "synthetic adapter only"

    computer_use.autonomous_gui_task("Edit note", executor, max_steps=1, approval_cb=human,
                                     checkpoints_dir=tmp_path)
    assert len(decisions) == 2
    assert len(calls) == 1 and calls[0][1] is False
