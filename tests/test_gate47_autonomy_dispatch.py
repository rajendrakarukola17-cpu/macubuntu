"""Offline autonomy dispatch fail-closed tests; no real actuators or MCP servers."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import approval
from celina_pkg.autonomy.engine import AutonomyEngine
from celina_pkg.autonomy.self_healing import ExecResult
from celina_pkg.planner.task_graph import TaskGraph


class Router:
    def __init__(self, decision):
        self.decision = decision

    def route_for_autonomy(self, _goal, context):
        return self.decision


class Model:
    def predict_transition(self, *_args, **_kwargs):
        return SimpleNamespace(confidence=0.8, epistemic_note="fixture")


class Executor:
    def __init__(self):
        self.calls = []

    def execute(self, decision, task_id):
        self.calls.append((decision, task_id))
        return ExecResult(success=True, exit_code=0, summary="fake")


class Loader:
    def __init__(self):
        self.calls = []

    def execute_domain_tool(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(ok=True, output="fake", arguments_hash="", mcp_transport="fixture", error=None)


@pytest.mark.parametrize("name,tier,domain,args", [
    ("write_file", 2, "files_office", {"path": "/fixture", "content": "fixture"}),
    ("cache_status", 1, "os_hardware", {}),
    ("cache_status", 1, "os_hardware", {"sandboxed_argv": ["fixture-binary"]}),
    ("open_application", 1, "os_hardware", {"app_name": "fixture"}),
    ("vision_analyze", 2, "mcp_perception_actuation", {"prompt": "fixture"}),
    ("unknown_tool", 1, "unknown", {}),
])
@pytest.mark.parametrize("has_callback", [False, True])
def test_nonobservational_autonomy_dispatch_never_invokes_legacy_executor(tmp_path, name, tier, domain, args, has_callback):
    graph = TaskGraph(tmp_path / "tasks.db")
    executor = Executor()
    loader = Loader()
    callbacks = []
    cb = (lambda title, detail: callbacks.append((title, detail)) or True) if has_callback else None
    decision = {"tool_name": name, "tier": tier, "domain": domain, "arguments": args,
                "why_receipt_hash": "why-fixture"}
    try:
        graph.insert_task("task", None, "check a fixture", "agent")
        engine = AutonomyEngine(graph, executor, Router(decision), approval.ApprovalEngine(cb),
                                observe_only=False, os_world_model=Model(), mcp_loader=loader,
                                speak_errors=False)
        assert engine.step() == "task"
        row = graph.get_task("task")
        assert row["status"] == "blocked"
        assert row["last_error"] == "approval_broker_unavailable"
        assert row["evidence_hash"]
        assert not executor.calls and not loader.calls and not callbacks
    finally:
        graph.close()


def test_tier2_observe_only_still_records_without_dispatch(tmp_path):
    graph = TaskGraph(tmp_path / "tasks.db")
    executor = Executor()
    loader = Loader()
    calls = []
    try:
        graph.insert_task("task", None, "check a fixture", "agent")
        decision = {"tool_name": "write_file", "arguments": {"path": "/fixture"}, "tier": 2}
        engine = AutonomyEngine(graph, executor, Router(decision),
                                approval.ApprovalEngine(lambda t, d: calls.append((t, d)) or True),
                                observe_only=True, os_world_model=Model(), mcp_loader=loader,
                                speak_errors=False)
        engine.step()
        assert graph.get_task("task")["status"] == "observed"
        assert not executor.calls and not loader.calls and not calls
    finally:
        graph.close()