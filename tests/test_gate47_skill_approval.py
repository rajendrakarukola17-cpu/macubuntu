"""Offline skill replay checks using a synthetic store and non-actuating executor."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import skills


class FakeStore:
    def __init__(self, steps):
        self.skill = {"name": "demo", "title": "Demo", "steps": steps}
        self.runs = []

    def get_skill(self, name):
        return self.skill if name == "demo" else None

    def record_skill_run(self, *args):
        self.runs.append(args)


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(skills.automation, "clear_stop", lambda: None)
    monkeypatch.setattr(skills.automation, "is_stop_requested", lambda: False)
    monkeypatch.setattr(skills.config, "DEFAULT_STEP_DELAY", 0)
    monkeypatch.setattr(skills.time, "sleep", lambda _seconds: None)


@pytest.mark.parametrize("tool,args", [
    ("get_system_stats", {}),
    ("run_shell_command", {"command": "fixture only"}),
    ("type_text", {"text": "fixture only"}),
])
def test_all_child_requests_use_real_callback_not_automatic_approval(ready, tool, args):
    store = FakeStore([{"tool": tool, "args": args}])
    decisions = []
    dispatched = []

    def human(title, detail):
        decisions.append((title, detail))
        return len(decisions) == 1

    def executor(name, arguments, callback):
        assert callback is human
        if not callback("Child request", "Review"):
            return "Denied: child request declined"
        dispatched.append((name, arguments))
        return "ok"

    result = skills.run_skill("demo", executor, approval_cb=human, db=store)
    assert "paused" in result
    assert len(decisions) == 2
    assert not dispatched
    assert store.runs[-1][1] == "failed"


def test_retry_does_not_inherit_an_automatic_approval(ready):
    store = FakeStore([{"tool": "click_on_text", "args": {"text": "Save"}}])
    decisions = []
    attempts = []

    def human(title, detail):
        decisions.append((title, detail))
        return len(decisions) < 3

    def executor(_name, _args, callback):
        assert callback is human
        attempts.append(callback("Child click", "Review"))
        return "Could not find target" if attempts[-1] else "Denied: retry declined"

    result = skills.run_skill("demo", executor, approval_cb=human, db=store)
    assert attempts == [True, False]
    assert len(decisions) == 3
    assert "paused" in result


@pytest.mark.parametrize("decision", [None, False])
def test_missing_or_declined_workflow_approval_does_not_call_executor(ready, decision):
    store = FakeStore([{"tool": "type_text", "args": {"text": "fixture only"}}])
    calls = []
    cb = None if decision is None else lambda _t, _d: False
    result = skills.run_skill("demo", lambda *args: calls.append(args), approval_cb=cb, db=store)
    assert result.startswith(("Denied:", "Cancelled"))
    assert not calls


def test_stop_during_retry_delay_prevents_retry(ready, monkeypatch):
    store = FakeStore([{"tool": "click_on_text", "args": {"text": "Save"}}])
    stopped = [False]
    calls = []
    monkeypatch.setattr(skills.automation, "is_stop_requested", lambda: stopped[0])
    monkeypatch.setattr(skills.time, "sleep", lambda _seconds: stopped.__setitem__(0, True))

    def executor(name, args, callback):
        calls.append((name, args, callback))
        return "Could not find target"

    result = skills.run_skill("demo", executor, approval_cb=lambda _t, _d: True, db=store)
    assert len(calls) == 1
    assert "Stopped" in result
    assert store.runs[-1][1] == "cancelled"
