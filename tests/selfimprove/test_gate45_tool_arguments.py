# Copyright 2026 Celina contributors.
"""Gate 4.5: reject undeclared arguments before any tool handler runs."""

from __future__ import annotations

import pytest

from celina_pkg import guardrails, tools

SCHEMA = {
    "example": {
        "parameters": {
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}


def test_declared_arguments_remain_valid() -> None:
    """Declared optional and required fields retain normal validation."""
    assert guardrails.validate_tool_arguments("example", {"path": "x", "limit": 2}, SCHEMA)[0]
    assert guardrails.validate_tool_arguments("example", {"path": "x"}, SCHEMA)[0]


@pytest.mark.parametrize("extra", [0, None, "value", {"nested": 1}])
def test_extra_argument_is_rejected_even_when_null(extra: object) -> None:
    """The presence of an undeclared key is a validation error regardless of value."""
    valid, reason = guardrails.validate_tool_arguments(
        "example", {"path": "x", "unexpected": extra}, SCHEMA,
    )
    assert not valid
    assert "unexpected" in reason.lower()
    assert "undeclared" in reason.lower()


def test_unknown_tool_schema_fails_closed() -> None:
    """No schema means arguments cannot be validated and should not be accepted."""
    valid, reason = guardrails.validate_tool_arguments("missing", {"path": "x"}, SCHEMA)
    assert not valid
    assert "schema" in reason.lower()


def test_dispatch_does_not_drop_extra_key_and_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dispatcher must reject raw extra keys rather than silently filter them."""
    calls: list[object] = []

    def fake_handler() -> str:
        calls.append("executed")
        return "done"

    monkeypatch.setattr(
        tools.capabilities, "check_tool_execution_boundary", lambda *_a, **_k: (True, ""),
    )
    monkeypatch.setitem(tools._AUTO_TOOL_MAP, "get_system_stats", fake_handler)  # noqa: SLF001
    result = tools.execute_tool("get_system_stats", {"unexpected": "secret"})
    assert "argument" in result.lower()
    assert "unexpected" in result.lower()
    assert not calls
