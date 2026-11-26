"""Autonomy world-model fail-closed tests; only fake executors and temporary DBs."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import approval, world_model
from celina_pkg.autonomy.engine import AutonomyEngine
from celina_pkg.autonomy.self_healing import ExecResult
from celina_pkg.planner.task_graph import TaskGraph


class Router:
    def route_for_autonomy(self, _goal, context):
        return {"tool_name": "write_file", "arguments": {"path": "/fixture", "content": "fixture"},
                "tier": 2, "why_receipt_hash": "why-fixture"}


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, decision, task_id):
        self.calls.append((decision, task_id))
        return ExecResult(success=True, exit_code=0, summary="fake only")


class Model:
    def __init__(self, value):
        self.value = value

    def predict_transition(self, *_args, **_kwargs):
        if isinstance(self.value, Exception):
            raise self.value
        return SimpleNamespace(confidence=self.value, epistemic_note="fixture reason")


@pytest.mark.parametrize("confidence", [RuntimeError("fixture"), 0.0, -0.1,
                                        float("nan"), float("inf"), 1.1, True, "bad"])
@pytest.mark.parametrize("observe_only", [False, True])
def test_unavailable_blocked_or_invalid_world_prediction_never_dispatches(tmp_path, confidence, observe_only):
    graph = TaskGraph(tmp_path / "tasks.db")
    executor = FakeExecutor()
    approvals = []
    gate = approval.ApprovalEngine(lambda title, detail: approvals.append((title, detail)) or True)
    try:
        graph.insert_task("task", None, "update notes", "agent")
        engine = AutonomyEngine(graph, executor, Router(), gate, observe_only=observe_only,
                                os_world_model=Model(confidence), speak_errors=False)
        assert engine.step() == "task"
        task = graph.get_task("task")
        assert task["status"] == "blocked"
        assert "world_model_blocked" in task["last_error"]
        assert task["evidence_hash"]
        assert not executor.calls and not approvals
    finally:
        graph.close()


def test_simulation_policy_block_cannot_be_converted_to_positive_confidence(monkeypatch):
    sim = world_model.SimResult(tool="write_file", recommended_guard="block", digest="fixture",
                                confidence=1.0, effects={}, warnings=("policy denied",))
    monkeypatch.setattr(world_model, "simulate_intervention", lambda *_a, **_k: sim)
    sm = SimpleNamespace(precheck_action=lambda **_kwargs: SimpleNamespace(
        confidence=1.0, require_human_confirmation=False, can_attempt=True, reason="ok"),
        get_calibration=lambda _tool: SimpleNamespace(attempts=0, brier_error=0.0))
    model = world_model.OSWorldModel(hardware_profile=world_model.HardwareProfile(tier="8gb", has_battery=False),
                                     self_model_instance=sm)
    prediction = model.predict_transition("write_file", {"path": "/fixture"})
    assert prediction.confidence == 0.0
    assert prediction.requires_verification is True


def test_precheck_cannot_attempt_cannot_be_converted_to_positive_confidence(monkeypatch):
    sim = world_model.SimResult(tool="cache_status", recommended_guard="allow", digest="fixture",
                                confidence=0.9, effects={})
    monkeypatch.setattr(world_model, "simulate_intervention", lambda *_a, **_k: sim)
    sm = SimpleNamespace(precheck_action=lambda **_kwargs: SimpleNamespace(
        confidence=0.8, require_human_confirmation=False, can_attempt=False, reason="blocked"),
        get_calibration=lambda _tool: SimpleNamespace(attempts=0, brier_error=0.0))
    model = world_model.OSWorldModel(hardware_profile=world_model.HardwareProfile(tier="8gb", has_battery=False),
                                     self_model_instance=sm)
    prediction = model.predict_transition("cache_status", {})
    assert prediction.confidence == 0.0


@pytest.mark.parametrize("block_source", ["simulation", "precheck"])
def test_real_world_model_block_reaches_engine_without_actuation(tmp_path, monkeypatch, block_source):
    sim = world_model.SimResult(tool="write_file", recommended_guard="block" if block_source == "simulation" else "allow",
                                digest="fixture", confidence=1.0, effects={}, warnings=("fixture",))
    monkeypatch.setattr(world_model, "simulate_intervention", lambda *_a, **_k: sim)
    self_model = SimpleNamespace(precheck_action=lambda **_kwargs: SimpleNamespace(
        confidence=1.0, require_human_confirmation=False, can_attempt=block_source != "precheck", reason="fixture"),
        get_calibration=lambda _tool: SimpleNamespace(attempts=0, brier_error=0.0))
    model = world_model.OSWorldModel(hardware_profile=world_model.HardwareProfile(tier="8gb", has_battery=False),
                                     self_model_instance=self_model)
    graph = TaskGraph(tmp_path / "tasks.db")
    executor = FakeExecutor()
    approvals = []
    try:
        graph.insert_task("task", None, "update notes", "agent")
        engine = AutonomyEngine(graph, executor, Router(),
                                approval.ApprovalEngine(lambda t, d: approvals.append((t, d)) or True),
                                observe_only=False, os_world_model=model, speak_errors=False)
        engine.step()
        task = graph.get_task("task")
        assert task["status"] == "blocked"
        assert "world_model_blocked" in task["last_error"]
        assert task["evidence_hash"]
        assert not approvals and not executor.calls
    finally:
        graph.close()
