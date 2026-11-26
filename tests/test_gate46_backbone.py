"""Offline edge cases for core policy, harness, capability and guardrail boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from celina_pkg import brain_harness, capabilities, guardrails, policy

if TYPE_CHECKING:
    from pathlib import Path


def test_policy_fails_closed_for_missing_band_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a broken risk-band configuration cannot authorize auto-execution."""
    monkeypatch.setattr(policy, "RISK_BANDS", ())
    assert policy.score_to_band(42) == "critical"
    assert policy.band_allows_autoexec("unknown") is False


def test_brain_normalization_handles_bad_native_and_empty_arguments() -> None:
    """Malformed, disallowed and non-object tool calls do not become invocations."""
    assert brain_harness._parse_args_dict("") == {}
    assert brain_harness._parse_args_dict("[]") == {}
    assert brain_harness._parse_args_dict(None) == {}
    bad = {"tool_calls": [None, {}, {"name": "not_allowed"}], "content": ""}
    assert brain_harness.normalize_tool_calls(bad, allowed_tool_names={"allowed"}) == []
    callbacks = brain_harness.BrainCallbacks()
    assert callbacks.emit("on_route", "offline", "test") is None


def test_brain_compaction_preserves_recent_when_no_summary() -> None:
    """Blank and system-only older messages never become invented summaries."""
    recent = [{"role": "user", "content": "x" * 800} for _ in range(6)]
    old = [{"role": "system", "content": "x" * 500}, {"role": "user", "content": "  "}]
    out, compacted = brain_harness.compact_conversation_history(old + recent, ram_tier="8gb")
    assert out == recent
    assert compacted is False


@pytest.mark.parametrize(("text", "expected"), [
    ("", "empty_error"), ("Timed out", "timeout"), ("EACCES", "permission_denied"),
    ("No such file", "not_found"), ("network offline", "network_error"),
    ("HTTP 429", "rate_limit"), ("missing required", "schema_error"),
    ("blocked by policy", "policy_denied"), ("HTTP 418", "http_418"),
    ("unexpected", "runtime_error"),
])
def test_guardrail_error_classification(text: str, expected: str) -> None:
    assert guardrails.extract_error_code(text) == expected


def test_circuit_breaker_cooldown_and_session_reset() -> None:
    """Mixed failures cool down; repeated failures disable only that tool."""
    breaker = guardrails.SessionCircuitBreaker(same_error_threshold=3, mixed_threshold=2, cooldown_sec=5)
    assert breaker.check_open("a", now=10) is None
    assert not breaker.record_failure("a", "timeout", now=10)
    assert breaker.record_failure("a", "schema_error", now=10)
    assert breaker.check_open("a", now=12) == 3
    assert breaker.check_open("a", now=16) is None
    assert not breaker.record_failure("b", "timeout", now=20)
    assert breaker.record_failure("b", "timeout", now=20)  # mixed cooldown threshold
    assert breaker.record_failure("b", "timeout", now=20)  # same-code session threshold
    assert breaker.check_open("b", now=21) == float("inf")
    assert breaker.snapshot()["b"]["session_disabled"]
    breaker.reset("b")
    assert breaker.check_open("b", now=21) is None
    breaker.reset()
    assert breaker.snapshot() == {}


def test_alternating_loop_and_guardrail_step_budget() -> None:
    """Detect action cycles and stop at the configured budget."""
    detector = guardrails.ActionLoopDetector()
    for tool in ("a", "b", "a"):
        assert not detector.record_and_check(tool, {})[0]
    assert detector.record_and_check("b", {})[0]
    detector.reset()
    stack = guardrails.ForgeGuardrailStack(max_steps=3)
    budget = stack.compute_hardware_budget(avail_ram_mb=900)
    assert stack.enforce_step(3, "a", {}, budget=budget)[0] is False
    for _ in range(2):
        assert stack.enforce_step(0, "a", {}, budget=budget)[0]
    assert stack.enforce_step(0, "a", {}, budget=budget)[0] is False
    assert "network_error" in stack.build_retry_nudge("a", "network offline", 1)
    assert stack.compute_hardware_budget(avail_ram_mb=2000).max_steps == 3


def test_guardrail_compaction_respects_boundaries() -> None:
    stack = guardrails.ForgeGuardrailStack()
    budget = guardrails.ForgeBudget(5, 10, 8000)
    messages = [
        {"role": "system", "content": "original system"}, {"role": "user", "content": "goal"},
        {"role": "tool", "content": "   "}, {"role": "tool", "content": "important result"},
        {"role": "assistant", "content": "recent"}, {"role": "user", "content": "latest"},
    ]
    out, changed = stack.compact_messages(messages, budget=budget)
    assert changed and budget.compaction_triggered
    assert out[:2] == messages[:2] and out[-2:] == messages[-2:]
    assert "important result" in out[2]["content"]
    assert stack.compact_messages(messages[:4], budget=budget)[1] is False


@pytest.mark.parametrize(("field_type", "value"), [
    ("integer", True), ("integer", "1"), ("number", False), ("number", "1"),
    ("string", 1), ("boolean", "yes"), ("object", []), ("array", {}),
])
def test_guardrail_rejects_wrong_typed_values(field_type: str, value: object) -> None:
    schema = {"x": {"parameters": {"properties": {"v": {"type": field_type}}}}}
    assert guardrails.validate_tool_arguments("x", {"v": value}, schema)[0] is False


def test_guardrail_validation_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = {"x": {"parameters": {"properties": {
        "v": {"type": "string", "enum": ["yes", "no"]}, "optional": {"type": "integer"},
    }, "required": ["v"]}}}
    for value in (None, "", "  "):
        assert guardrails.validate_tool_arguments("x", {"v": value}, schema)[0] is False
    assert not guardrails.validate_tool_argument_names("x", [], schema)[0]
    assert not guardrails.validate_tool_argument_names("x", {}, {"x": {"parameters": "bad"}})[0]
    assert not guardrails.validate_tool_arguments("x", {"v": "maybe"}, schema)[0]
    monkeypatch.setattr(guardrails, "_pydantic", None)
    assert guardrails.validate_tool_arguments("x", {"v": "yes", "optional": None}, schema)[0]
    class BrokenAdapter:
        def __init__(self, _type: object) -> None:
            pass
        def validate_python(self, _arguments: object) -> None:
            raise ValueError("bad")
    monkeypatch.setattr(guardrails, "_pydantic", SimpleNamespace(TypeAdapter=BrokenAdapter))
    assert "Pydantic" in guardrails.validate_tool_arguments("x", {"v": "yes"}, schema)[1]


def test_manifest_missing_and_execution_denials(tmp_path: Path) -> None:
    """Empty, undeclared, credential-exposing and mutable tier-one tools fail closed."""
    assert capabilities.load_manifest(tmp_path / "missing")["tools"] == {}
    capabilities._check_tool_identifier_collisions({"tools": []})
    assert not capabilities.check_tool_execution_boundary("x", is_tier2=False, manifest={})[0]
    assert not capabilities.check_tool_execution_boundary("x", is_tier2=False, manifest={"tools": {"a": {}}})[0]
    for entry in ({"credential_access": "all"}, {"fs_scope": "home_rw_approval_undo"}):
        assert not capabilities.check_tool_execution_boundary("x", is_tier2=False, manifest={"tools": {"x": entry}})[0]
    assert capabilities.verify_manifest_integrity({})
    assert len(capabilities.verify_manifest_integrity({"integrity": {"default_policy": "allow"}})) == 2


def test_manifest_invariant_diagnostics_cover_malformed_inputs() -> None:
    assert "missing" in capabilities.validate_manifest_invariants({}, expected_tier1=set(), expected_tier2=set())[0].lower()
    malformed = {
        "unknown": True, "tools": {
            "a": {"tier": "tier2", "fs_scope": "invalid", "network_egress": "invalid",
                  "kernel_boundary": "invalid", "credential_access": "all",
                  "auth_operation": 42, "domain": "white space", "unknown": True},
            "b": "not an object",
            "c": {"tier": "tier1", "fs_scope": "home_rw_approval_undo", "auth_operation": "bogus"},
        },
    }
    errors = capabilities.validate_manifest_invariants(malformed, expected_tier1={"a", "b", "c"}, expected_tier2=set())
    for fragment in ("Unknown top-level", "entry must be a dict", "unknown capability key", "whitespace",
                     "tier mismatch", "invalid fs_scope", "invalid network_egress", "invalid kernel_boundary",
                     "credential_access", "auth_operation must be", "unknown auth_operation", "Shadow-AI"):
        assert any(fragment in error for error in errors), fragment
    extra = capabilities.validate_manifest_invariants({"tools": {"extra": {}}}, expected_tier1=set(), expected_tier2=set())
    assert any("extra tools" in error for error in extra)


def test_bwrap_builder_validates_capability_without_executing(tmp_path: Path) -> None:
    """Inspect generated sandbox command only; no hardware or bwrap is simulated."""
    with pytest.raises(PermissionError, match="not declared"):
        capabilities.build_bwrap_command("missing", ["true"], workdir=tmp_path, manifest={"tools": {}})
    with pytest.raises(PermissionError, match="credential"):
        capabilities.build_bwrap_command("x", [], workdir=tmp_path, manifest={"tools": {"x": {"credential_access": "all"}}})
    cap = {"credential_access": "none", "network_egress": "https_public_only", "fs_scope": "bwrap_workdir"}
    command = capabilities.build_bwrap_command("x", ["true"], workdir=tmp_path, allow_workdir_write=True,
                                                manifest={"tools": {"x": cap}})
    assert "--unshare-net" not in command
    assert "--bind" in command
    assert "/work" in command
    assert "x" not in capabilities.format_capabilities_summary({"tools": {}})
    summary = capabilities.format_capabilities_summary({"tools": {"x": cap}})
    assert "https_public_only" in summary
