# Copyright 2026 Celina contributors.
"""Natural offline intent routing, safety and usable tool arguments."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from celina_pkg import approval, capabilities, config, router, tools
from celina_pkg.autonomy import AutonomyEngine, SelfHealingExecutor
from celina_pkg.planner import TaskGraph

if TYPE_CHECKING:
    from pathlib import Path


def _route(goal: str) -> dict[str, object]:
    return router.CelinaRouter().route_for_autonomy(
        goal, {"online_probe": lambda: False, "uno_probe": lambda: False},
    )


@pytest.mark.parametrize("goal", [
    "What is the weather today?", "Could you check tomorrow's weather?",
    "Show the weather forecast for Visakhapatnam", "Look up the current weather online",
])
def test_natural_weather_requests_obey_offline_fallback(goal: str) -> None:
    """Natural live-data requests must select the existing safe offline fallback."""
    decision = _route(goal)
    assert decision["tool_name"] == "respond_offline"
    assert decision.get("offline_fallback") is True
    assert "offline" in str(decision["arguments"]).lower()


@pytest.mark.parametrize("goal", [
    "list local files", "Show my home directory", "List the files in my home folder",
])
def test_list_home_is_a_usable_bounded_tool_call(goal: str) -> None:
    """A home-listing decision includes a usable path and matching approval hash."""
    decision = _route(goal)
    assert decision["tool_name"] == "list_files"
    arguments = decision["arguments"]
    assert isinstance(arguments, dict)
    assert arguments.get("path") == str(config.HOME)
    assert decision["arguments_hash"] == approval.compute_arguments_hash("list_files", arguments)
    assert not tools.execute_tool("list_files", arguments).startswith("Error:")


@pytest.mark.parametrize("goal", ["hello", "Hi!", "Good morning", "hey there"])
def test_greetings_have_a_direct_response(goal: str) -> None:
    """Simple conversational greetings do not invoke unrelated system tools."""
    decision = _route(goal)
    assert decision["tool_name"] == "respond_directly"
    assert decision["arguments"]["message"]


@pytest.mark.parametrize("goal", [
    "search notes for keyword", "find my notes about project planning",
    "search my memory for dinner plans",
])
def test_memory_search_carries_search_terms(goal: str) -> None:
    """A memory route supplies a nonempty query for real downstream execution."""
    decision = _route(goal)
    assert decision["tool_name"] == "search_memory"
    assert decision["arguments"]["query"]
    assert decision["arguments_hash"] == approval.compute_arguments_hash(
        "search_memory", decision["arguments"],
    )


@pytest.mark.parametrize("goal", [
    "read or delete a file", "Should you list or delete my files?",
    "open or remove the document",
])
def test_conflicting_file_actions_request_clarification(goal: str) -> None:
    """Competing actions cannot fall through to either action or system stats."""
    decision = _route(goal)
    assert decision["tool_name"] == "respond_directly"
    assert "?" in decision["arguments"]["message"]


def test_disabled_or_failed_explicit_choice_is_never_revived() -> None:
    """An intent must not override a retry's explicitly failed tool."""
    decision = router.CelinaRouter().route_for_autonomy(
        "list local files", {"known_failed_attempts": ["list_files"]},
    )
    assert decision["tool_name"] == "respond_directly"
    assert decision.get("requires_clarification") is True


@pytest.mark.parametrize(("goal", "status", "fragment"), [
    ("Hi!", "done", "Hello"),
    ("read or delete a file", "blocked", "?"),
    ("what is the weather today", "blocked", "Offline"),
])
def test_engine_delivers_response_or_blocks_for_input(
    tmp_path: Path, goal: str, status: str, fragment: str,
) -> None:
    """Pseudo-tools deliver their message; unanswered work is never marked complete."""
    graph = TaskGraph(tmp_path / "tasks.db")
    try:
        graph.insert_task(task_id="t1", parent_id=None, goal=goal, node_type="agent")
        executor = SelfHealingExecutor(data_dir=tmp_path, tool_handlers={})
        engine = AutonomyEngine(graph, executor, router.CelinaRouter(), approval.ApprovalEngine())
        engine.step()
        row = graph.get_task("t1")
        assert row["status"] == status
        assert fragment in row["result_summary"]
        assert graph.verify_evidence_chain()[0]
    finally:
        graph.close()


def test_denied_recognized_intent_never_falls_into_unrelated_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A denied intended tool is not replaced with a generic or mutating candidate."""
    monkeypatch.setattr(
        capabilities, "check_tool_execution_boundary",
        lambda name, **_kwargs: (name != "list_files", "denied"),
    )
    decision = _route("list local files")
    assert decision["tool_name"] == "respond_directly"
    assert decision.get("capability_denied") is True


@pytest.mark.parametrize("goal", [
    "search the web for train schedules", "look up the internet for volcano news",
])
def test_web_search_always_applies_offline_policy(goal: str) -> None:
    """A natural web search must not dispatch a live tool while offline."""
    decision = _route(goal)
    assert decision["tool_name"] == "respond_offline"
    assert decision.get("offline_fallback") is True


def test_online_weather_without_city_asks_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """An enabled online weather request without a city must not dispatch invalid args."""
    monkeypatch.setattr(config, "is_web_comms_enabled", lambda: True)
    decision = router.CelinaRouter().route_for_autonomy(
        "Could you check the weather?", {"online_probe": lambda: True},
    )
    assert decision["tool_name"] == "respond_directly"
    assert decision.get("requires_clarification") is True
    assert "city" in decision["arguments"]["message"].lower()


def test_generic_science_question_is_not_live_weather_request() -> None:
    """Talking about the concept of weather does not imply a live lookup."""
    assert _route("Explain how weather works")["tool_name"] != "respond_offline"
