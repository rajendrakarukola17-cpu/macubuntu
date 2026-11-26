# Copyright 2026 Celina contributors.
"""Autonomy routing must honor its selection and bind the actual dispatched tool."""

import pytest

from celina_pkg import approval, capabilities, router, tools


def test_explicit_selected_tool_precedes_generic_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named tool, if allowed, must not be replaced by the first generic tool."""
    choices = [
        {"function": {"name": "get_system_stats"}},
        {"function": {"name": "list_files"}},
    ]
    monkeypatch.setattr(
        tools, "get_contextual_tool_schemas", lambda *_args, **_kwargs: (choices, []),
    )
    monkeypatch.setattr(
        capabilities, "check_tool_execution_boundary", lambda *_args, **_kwargs: (True, ""),
    )
    decision = router.CelinaRouter().route_for_autonomy("please use list_files")
    assert decision["tool_name"] == "list_files"
    assert decision["arguments_hash"] == approval.compute_arguments_hash(
        decision["tool_name"], decision["arguments"],
    )


def test_denied_tool_fallback_hash_binds_actual_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capability fallback must never carry a hash for the denied selection."""
    choices = [
        {"function": {"name": "get_system_stats"}},
        {"function": {"name": "list_files"}},
    ]
    monkeypatch.setattr(
        tools, "get_contextual_tool_schemas", lambda *_args, **_kwargs: (choices, []),
    )
    monkeypatch.setattr(
        capabilities, "check_tool_execution_boundary",
        lambda name, **_kwargs: (name != "list_files", "denied"),
    )
    decision = router.CelinaRouter().route_for_autonomy("please use list_files")
    assert decision["tool_name"] == "get_system_stats"
    assert decision["arguments_hash"] == approval.compute_arguments_hash(
        decision["tool_name"], decision["arguments"],
    )


def test_capability_fallback_never_bypasses_offline_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A denied local choice cannot fall through to a live web tool when offline."""
    choices = [
        {"function": {"name": "get_system_stats"}},
        {"function": {"name": "get_weather"}},
    ]
    monkeypatch.setattr(
        tools, "get_contextual_tool_schemas", lambda *_args, **_kwargs: (choices, []),
    )
    monkeypatch.setattr(
        capabilities, "check_tool_execution_boundary",
        lambda name, **_kwargs: (name == "get_weather", "denied"),
    )
    decision = router.CelinaRouter().route_for_autonomy(
        "check system", context={"online_probe": lambda: False},
    )
    assert decision["tool_name"] != "get_weather"
    assert decision.get("capability_denied") or decision.get("offline_fallback")


def test_permitted_online_capability_fallback_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A web fallback stays available when the existing policy explicitly permits it."""
    choices = [
        {"function": {"name": "get_system_stats"}},
        {"function": {"name": "get_weather"}},
    ]
    monkeypatch.setattr(
        tools, "get_contextual_tool_schemas", lambda *_args, **_kwargs: (choices, []),
    )
    monkeypatch.setattr(
        capabilities, "check_tool_execution_boundary",
        lambda name, **_kwargs: (name == "get_weather", "denied"),
    )
    monkeypatch.setattr(router.config, "is_web_comms_enabled", lambda: True)
    decision = router.CelinaRouter().route_for_autonomy(
        "check system", context={"online_probe": lambda: True},
    )
    assert decision["tool_name"] == "get_weather"
    assert decision["arguments_hash"] == approval.compute_arguments_hash(
        decision["tool_name"], decision["arguments"],
    )
