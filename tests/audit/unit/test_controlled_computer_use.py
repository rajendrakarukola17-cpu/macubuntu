"""
tests/audit/unit/test_controlled_computer_use.py

Phase 3 unit tests for celina_pkg.controlled_computer_use — controlled
7-step computer-use harness. Tests scope definition, input validation,
interface drift detection, staging, preflight verification, and action logging.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg.controlled_computer_use import (
    ControlledAction,
    ControlledObservation,
    ControlledPlan,
    ControlledScope,
    detect_interface_drift,
    stage_action,
    validate_inputs,
)


# ---------------------------------------------------------------------------
# ControlledScope dataclass
# ---------------------------------------------------------------------------


def test_controlled_scope_defaults():
    scope = ControlledScope()
    assert scope.allowed_apps == []
    assert scope.allowed_domains == []
    assert scope.allowed_actions == ["click", "type", "select"]
    assert scope.blocked_actions == ["submit", "drag", "hotkey"]
    assert scope.max_steps == 7
    assert scope.require_submit_approval is True


def test_controlled_scope_custom():
    scope = ControlledScope(
        allowed_apps=["Firefox"],
        allowed_domains=["example.com"],
        allowed_actions=["click", "type"],
        blocked_actions=["submit"],
        max_steps=5,
        require_submit_approval=False,
    )
    assert scope.allowed_apps == ["Firefox"]
    assert scope.allowed_domains == ["example.com"]
    assert scope.max_steps == 5
    assert scope.require_submit_approval is False


# ---------------------------------------------------------------------------
# ControlledObservation dataclass
# ---------------------------------------------------------------------------


def test_controlled_observation_defaults():
    obs = ControlledObservation()
    assert obs.active_app == ""
    assert obs.active_title == ""
    assert obs.layout_hash == ""
    assert obs.atspi_elements == []
    assert obs.timestamp_utc == ""


# ---------------------------------------------------------------------------
# ControlledAction dataclass
# ---------------------------------------------------------------------------


def test_controlled_action_defaults():
    action = ControlledAction(action="click", target="button#submit")
    assert action.action == "click"
    assert action.target == "button#submit"
    assert action.args == {}
    assert action.stage_only is True


# ---------------------------------------------------------------------------
# ControlledPlan dataclass
# ---------------------------------------------------------------------------


def test_controlled_plan_defaults():
    plan = ControlledPlan(scope=ControlledScope())
    assert plan.step == 0
    assert plan.scope is not None
    assert plan.preflight_ok is False
    assert plan.drift_detected is False
    assert plan.drift_reason == ""
    assert plan.halt is False


# ---------------------------------------------------------------------------
# detect_interface_drift
# ---------------------------------------------------------------------------


def test_detect_interface_drift_app_mismatch():
    scope = ControlledScope(allowed_apps=["Firefox"])
    obs = ControlledObservation(active_app="Thunderbird", active_title="Inbox")
    drifted, reason = detect_interface_drift(current=obs, expected_scope=scope)
    assert drifted is True
    assert "Thunderbird" in reason


def test_detect_interface_drift_app_match():
    scope = ControlledScope(allowed_apps=["Firefox"])
    obs = ControlledObservation(active_app="Firefox", active_title="Browser")
    drifted, reason = detect_interface_drift(current=obs, expected_scope=scope)
    assert drifted is False


def test_detect_interface_drift_no_apps_allowed():
    scope = ControlledScope()  # empty allowed_apps
    obs = ControlledObservation(active_app="AnyApp")
    drifted, reason = detect_interface_drift(current=obs, expected_scope=scope)
    assert drifted is False  # no app restrictions means no drift on app


# ---------------------------------------------------------------------------
# validate_inputs
# ---------------------------------------------------------------------------


def test_validate_inputs_valid():
    ok, err = validate_inputs(goal="fill form", inputs={"name": "Alice"}, expected_fields=["name"])
    assert ok is True
    assert err == "inputs valid"


def test_validate_inputs_blocked_pattern():
    ok, err = validate_inputs(
        goal="run command",
        inputs={"cmd": "rm -rf /"},
        forbidden_patterns=[r"rm\b"],
    )
    assert ok is False
    assert "forbidden" in err or "rm" in err


def test_validate_inputs_empty_goal():
    ok, err = validate_inputs(goal="", inputs={})
    assert ok is False
    assert "empty goal" in err


# ---------------------------------------------------------------------------
# stage_action
# ---------------------------------------------------------------------------


def test_stage_action_creates_action():
    scope = ControlledScope()
    action = stage_action(action="click", target="button#ok", args={}, scope=scope)
    assert action.action == "click"
    assert action.target == "button#ok"
    assert action.stage_only is True


def test_stage_action_blocks_blocked_action():
    scope = ControlledScope(blocked_actions=["submit"])
    action = stage_action(action="submit", target="form#login", args={}, scope=scope)
    assert action.action == "blocked"
    assert "blocked" in action.expected_result or "submit" in action.expected_result