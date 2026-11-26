# Copyright 2026 Celina contributors.
"""Routing benchmark compares selected actions, not expected canned answers."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from celina_pkg.selfimprove.wiring._eval_routing import routing_observations

if TYPE_CHECKING:
    from pathlib import Path

_ROUTING_COUNT = 5
_ACTIONS = (
    "respond_offline", "list_files", "respond_directly", "search_memory", "respond_directly",
)


def test_unrelated_fallbacks_and_nonclarifying_answers_fail() -> None:
    """A generic fallback is not counted as weather, files, greeting or notes routing."""
    def fallback(_goal: str, context: dict[str, object]) -> dict[str, object]:
        online_probe = context["online_probe"]
        uno_probe = context["uno_probe"]
        assert callable(online_probe)
        assert not online_probe()
        assert callable(uno_probe)
        assert not uno_probe()
        return {"tool_name": "get_system_stats"}

    observations = routing_observations(fallback)
    assert len(observations) == _ROUTING_COUNT
    assert all(not item.passed for item in observations.values())
    assert all(item.observed == "get_system_stats" for item in observations.values())


def test_appropriate_actions_and_ambiguity_are_checked() -> None:
    """A clarify tool choice must include a question; a generic reply is insufficient."""
    tools = iter(_ACTIONS)

    def route(_goal: str, _context: dict[str, object]) -> dict[str, object]:
        name = next(tools)
        message = (
            "Offline: live weather unavailable." if name == "respond_offline" else "Read or delete?"
        )
        return {
            "tool_name": name, "offline_fallback": name == "respond_offline",
            "arguments": {"message": message},
        }

    assert all(item.passed for item in routing_observations(route).values())
    tools2 = iter(_ACTIONS)

    def no_question(_goal: str, _context: dict[str, object]) -> dict[str, object]:
        name = next(tools2)
        message = "Offline: live weather unavailable." if name == "respond_offline" else "Okay."
        return {
            "tool_name": name, "offline_fallback": name == "respond_offline",
            "arguments": {"message": message},
        }

    assert not routing_observations(no_question)["routing-05"].passed


def test_malformed_router_result_is_not_success() -> None:
    """Missing selections are errors rather than silently successful routing."""
    with pytest.raises(ValueError, match="named tool"):
        routing_observations(lambda _goal, _context: {})


def test_real_router_results_in_fresh_process(tmp_path: Path) -> None:
    """Each diagnostic corresponds to a live offline Celina router selection."""
    home, state = tmp_path / "home", tmp_path / "state"
    home.mkdir()
    state.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1", "DRY_RUN": "1",
        "HOME": str(home), "TARGET_HOME": str(state),
        "CELINA_STORAGE_DIR": str(state / "storage"),
    }
    script = (
        "from celina_pkg.router import CelinaRouter\n"
        "from celina_pkg.selfimprove.wiring._eval_routing import routing_observations\n"
        "r = CelinaRouter()\n"
        "actual = []\n"
        "def route(goal, context):\n"
        "    result = r.route_for_autonomy(goal, context)\n"
        "    actual.append(result['tool_name'])\n"
        "    return result\n"
        "outcomes = routing_observations(route)\n"
        "assert [item.observed for item in outcomes.values()] == actual\n"
        "assert all(not item.passed for item in outcomes.values() "
        "if item.observed == 'get_system_stats')\n"
    )
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and trusted inline probe.
        [sys.executable, "-c", script], env=env, timeout=30, check=False,
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list(home.iterdir())
