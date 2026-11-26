"""Main tool-dispatch guard tests with fake handlers; no actual actuation."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import tools


@pytest.fixture
def dispatch_fixture(monkeypatch):
    monkeypatch.setattr(tools.config, "TIER1_AUTO_TOOLS", {"cache_status", "open_application", "set_clipboard"})
    monkeypatch.setattr(tools.config, "TIER2_APPROVAL_TOOLS", {"write_file"})
    calls = []
    decisions = []
    checks = []

    def handler(**kwargs):
        calls.append(kwargs)
        return "fake handler ran"

    def approval_cb(title, detail):
        decisions.append((title, detail))
        return True

    def check_boundary(name, is_tier2):
        checks.append((name, is_tier2))
        return True, ""

    monkeypatch.setitem(tools._AUTO_TOOL_MAP, "cache_status", handler)
    monkeypatch.setitem(tools._AUTO_TOOL_MAP, "open_application", handler)
    monkeypatch.setitem(tools._AUTO_TOOL_MAP, "set_clipboard", handler)
    monkeypatch.setitem(tools._APPROVAL_TOOL_MAP, "write_file", handler)
    monkeypatch.setattr(tools.capabilities, "check_tool_execution_boundary", check_boundary)
    monkeypatch.setattr(tools.guardrails, "validate_tool_argument_names", lambda *_args: (True, ""))
    monkeypatch.setattr(tools.guardrails, "validate_tool_arguments", lambda *_args: (True, ""))
    monkeypatch.setattr(tools, "_coerce_and_filter_tool_args", lambda _name, _fn, args: args)
    monkeypatch.setattr(tools.skills.recorder, "record_step", lambda *_args: None)
    return calls, decisions, checks, approval_cb


@pytest.mark.parametrize("name,args", [
    ("write_file", {"path": "fixture", "content": "fixture"}),
    ("open_application", {"app_name": "fixture"}),
    ("set_clipboard", {"text": "fixture"}),
    ("unknown_tool", {}),
    ("cache_status", {"command": "sudoers"}),
    ("cache_status", {"value": object()}),
])
@pytest.mark.parametrize("has_callback", [True, False])
def test_sensitive_unknown_or_invalid_dispatch_does_not_call_handler(dispatch_fixture, name, args, has_callback):
    calls, decisions, checks, callback = dispatch_fixture
    result = tools.execute_tool(name, args, approval_cb=callback if has_callback else None)
    assert result.startswith(("Denied:", "Blocked:"))
    assert not calls and not decisions and not checks


def test_read_only_dispatch_still_uses_fake_handler(dispatch_fixture):
    calls, decisions, checks, _callback = dispatch_fixture
    result = tools.execute_tool("cache_status", {"fixture": "value"})
    assert result == "fake handler ran"
    assert calls == [{"fixture": "value"}]
    assert checks == [("cache_status", False)]
    assert not decisions


def test_arguments_are_snapshotted_before_external_caller_mutates(dispatch_fixture, monkeypatch):
    calls, _decisions, _checks, _callback = dispatch_fixture
    args = {"nested": {"value": "original"}}

    def check_boundary(_name, is_tier2):
        args["nested"]["value"] = "changed"
        return True, ""

    monkeypatch.setattr(tools.capabilities, "check_tool_execution_boundary", check_boundary)
    result = tools.execute_tool("cache_status", args)
    assert result == "fake handler ran"
    assert args["nested"]["value"] == "changed"
    assert calls == [{"nested": {"value": "original"}}]
