# Copyright 2026 Celina contributors.
"""Offline routing fixtures and comparisons against a host-supplied real router."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._eval_probes import Diagnostic

if TYPE_CHECKING:
    from collections.abc import Callable

    Route = Callable[[str, dict[str, object]], dict[str, object]]

_FIXTURES = (
    ("routing-01", "what is the weather today", "respond_offline"),
    ("routing-02", "list local files", "list_files"),
    ("routing-03", "hello", "respond_directly"),
    ("routing-04", "search notes for keyword", "search_memory"),
    ("routing-05", "read or delete a file", "respond_directly"),
)


def _offline() -> bool:
    return False


def _decision(
    decision: dict[str, object], *, expected: str, clarify: bool, offline: bool = False,
) -> Diagnostic:
    tool = decision.get("tool_name")
    if not isinstance(tool, str) or not tool:
        msg = "router must return a named tool"
        raise ValueError(msg)
    args = decision.get("arguments")
    message = args.get("message", "") if isinstance(args, dict) else ""
    question = isinstance(message, str) and "?" in message
    passed = tool == expected and (question or not clarify)
    if offline:
        passed = (
            passed and decision.get("offline_fallback") is True
            and isinstance(message, str) and "offline" in message.lower()
        )
    reason = f"selected {tool}; expected {expected}"
    if offline:
        reason += "; explained offline fallback " + ("present" if passed else "absent")
    if clarify:
        reason += "; clarification question " + ("present" if question else "absent")
    return Diagnostic(passed, reason, tool)


def routing_observations(route: Route) -> dict[str, Diagnostic]:
    """Measure concrete routing decisions; never coerce generic fallback to success."""
    context: dict[str, object] = {"online_probe": _offline, "uno_probe": _offline}
    return {
        task_id: _decision(
            route(goal, context), expected=expected, clarify=task_id == "routing-05",
            offline=task_id == "routing-01",
        )
        for task_id, goal, expected in _FIXTURES
    }
