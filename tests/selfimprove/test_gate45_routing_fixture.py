# Copyright 2026 Celina contributors.
"""Offline weather scoring must honor the existing fail-closed router policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from celina_pkg.selfimprove.wiring._eval_routing import routing_observations

if TYPE_CHECKING:
    from collections.abc import Callable


def _router(weather: dict[str, object]) -> Callable[[str, dict[str, object]], dict[str, object]]:
    def route(goal: str, _context: dict[str, object]) -> dict[str, object]:
        if "weather" in goal:
            return weather
        return {"tool_name": "get_system_stats"}

    return route


def test_offline_weather_fallback_is_the_correct_safe_action() -> None:
    """An explicit, explained offline fallback is correct; no live tool is dispatched."""
    decision = {
        "tool_name": "respond_offline", "offline_fallback": True,
        "arguments": {"message": "Offline: live weather is unavailable."},
    }
    result = routing_observations(_router(decision))["routing-01"]
    assert result.passed
    assert result.observed == "respond_offline"


@pytest.mark.parametrize("decision", [
    {"tool_name": "respond_offline", "arguments": {"message": "Offline."}},
    {
        "tool_name": "respond_offline", "offline_fallback": False,
        "arguments": {"message": "Offline."},
    },
    {"tool_name": "respond_offline", "offline_fallback": True, "arguments": {"message": "Hello."}},
    {"tool_name": "get_weather", "offline_fallback": True, "arguments": {"message": "Offline."}},
])
def test_weather_result_requires_real_explained_offline_fallback(
    decision: dict[str, object],
) -> None:
    """A tool label alone is insufficient; unsafe dispatch or unexplained replies fail."""
    assert not routing_observations(_router(decision))["routing-01"].passed
