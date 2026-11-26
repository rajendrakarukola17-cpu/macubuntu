"""The capability bus must not dispatch Tier-2 actions without a trusted broker.

Fake transport labels, callbacks, governance, tracing and actuators are only
unit-test fixtures and do not demonstrate peer authentication or confinement.
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import capability_bus


class Span:
    trace_id = "fixture-trace"
    span_id = "fixture-span"
    status = "ok"

    def set_io(self, **_kwargs):
        pass

    def set_attribute(self, *_args):
        pass


class Tracer:
    @contextmanager
    def start_span(self, *_args, **_kwargs):
        yield Span()


class Substrate:
    def __init__(self):
        self.calls = []

    def issue_grant(self, **kwargs):
        self.calls.append(("issue", kwargs))
        return type("Grant", (), {"grant_id": "fixture-grant"})()

    def verify_and_consume_grant(self, grant, **kwargs):
        self.calls.append(("consume", kwargs))


@pytest.fixture
def bus_fixture(monkeypatch):
    monkeypatch.setattr(capability_bus.config, "TIER2_APPROVAL_TOOLS", {"write_file"})
    monkeypatch.setattr(capability_bus.evidence, "detect_prompt_injection", lambda _text: [])
    monkeypatch.setattr(capability_bus.net_policy, "check_web_comms_allowed", lambda *_a, **_k: None)
    substrate = Substrate()
    calls = []
    decisions = []

    def executor(tool, args, approval_cb=None):
        calls.append((tool, args))
        return "fake executor ran"

    def callback(title, detail):
        decisions.append((title, detail))
        return True

    bus = capability_bus.UnifiedCapabilityBus(expected_uid=os.getuid(), substrate=substrate,
                                               tracer=Tracer(), ledger=object(), executor=executor)
    return bus, substrate, calls, decisions, callback


@pytest.mark.parametrize("transport,provenance", [
    ("dbus", "dbus"), ("unix_socket", "unix_socket"), ("mcp", "in_process"), ("cli", "in_process"),
])
@pytest.mark.parametrize("has_callback", [False, True])
def test_tier2_custom_executor_fails_closed_without_broker(bus_fixture, transport, provenance, has_callback):
    bus, substrate, calls, decisions, callback = bus_fixture
    request = capability_bus.BusRequest(transport=transport, tool_name="write_file",
                                       arguments={"path": "fixture", "content": "fixture"},
                                       caller_uid=os.getuid(), uid_provenance=provenance)
    response = bus.dispatch(request, approval_cb=callback if has_callback else None)
    assert not response.ok
    assert response.policy_decision == "deny:approval_broker_unavailable"
    assert not calls and not decisions and not substrate.calls


def test_tier2_default_executor_also_fails_closed(bus_fixture, monkeypatch):
    bus, substrate, calls, decisions, callback = bus_fixture
    def fake_default(tool, args, approval_cb=None):
        calls.append((tool, args))
        return "fake executor ran"
    monkeypatch.setattr(capability_bus.tools, "execute_tool", fake_default)
    bus._executor = fake_default
    request = capability_bus.BusRequest(transport="dbus", tool_name="write_file", arguments={},
                                       caller_uid=os.getuid(), uid_provenance="dbus")
    response = bus.dispatch(request, approval_cb=callback)
    assert response.policy_decision == "deny:approval_broker_unavailable"
    assert not calls and not decisions and not substrate.calls


def test_existing_tier1_bus_routing_still_works_in_fixture(bus_fixture):
    bus, substrate, calls, _decisions, _callback = bus_fixture
    request = capability_bus.BusRequest(transport="dbus", tool_name="cache_status", arguments={},
                                       caller_uid=os.getuid(), uid_provenance="dbus")
    response = bus.dispatch(request)
    assert response.ok and response.result == "fake executor ran"
    assert calls == [("cache_status", {})]
    assert [kind for kind, _ in substrate.calls] == ["issue", "consume"]


@pytest.mark.parametrize("name,args", [
    ("open_application", {"app_name": "fixture"}),
    ("set_clipboard", {"text": "fixture"}),
    ("artemis_run_task", {}),
    ("not_a_tool", {}),
    ("cache_status", {"command": "sudoers"}),
    ("cache_status", {"value": object()}),
])
def test_non_read_only_unknown_or_malformed_bus_requests_fail_closed(bus_fixture, name, args):
    bus, substrate, calls, decisions, callback = bus_fixture
    request = capability_bus.BusRequest(transport="dbus", tool_name=name, arguments=args,
                                       caller_uid=os.getuid(), uid_provenance="dbus")
    response = bus.dispatch(request, approval_cb=callback)
    assert not response.ok
    assert response.policy_decision in {"deny:approval_broker_unavailable", "deny:invalid_arguments"}
    assert not calls and not decisions and not substrate.calls


def test_bus_dispatch_uses_snapshot_if_caller_mutates_original_arguments(bus_fixture):
    bus, substrate, calls, _decisions, _callback = bus_fixture
    args = {"nested": {"value": "original"}}
    original_issue = substrate.issue_grant

    def mutate_caller_arguments(**kwargs):
        args["nested"]["value"] = "changed"
        return original_issue(**kwargs)

    substrate.issue_grant = mutate_caller_arguments
    request = capability_bus.BusRequest(transport="dbus", tool_name="cache_status", arguments=args,
                                       caller_uid=os.getuid(), uid_provenance="dbus")
    response = bus.dispatch(request)
    assert response.ok
    assert args["nested"]["value"] == "changed"
    assert calls == [("cache_status", {"nested": {"value": "original"}})]
