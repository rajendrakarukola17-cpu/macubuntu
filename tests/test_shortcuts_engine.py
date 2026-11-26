"""
Suite 23 — Gear 0 / Gear 0.5 Zero-LLM Shortcut Engine.

Verifies celina_pkg.shortcuts end-to-end:
  1. ShortcutSpec parsing + SHA-256 fingerprint determinism
  2. Magic Variable interpolation ({{input.*}}, {{steps.*.output}})
  3. Safe `when` condition evaluation (no eval)
  4. ShortcutEngine execution with Tier-2 approval gating
  5. Gear05Router: exact match (Gear 0) <1 ms, semantic (Gear 0.5),
     gear0.5_confirm path, LLM fallback
  6. TriggerDaemon condition matching + cooldown
  7. Crystallizer habit mining from evidence traces

Zero generative LLM, no PyTorch, no network. <25 ms per route.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.shortcuts.crystallizer import (  # noqa: E402
    crystallize,
    find_crystallizable_habits,
    signature_of_trace,
    traces_from_ledger,
)
from celina_pkg.shortcuts.engine import (  # noqa: E402
    ShortcutEngine,
    ShortcutInput,
    ShortcutSpec,
    ShortcutStep,
    eval_when,
    resolve_magic_vars,
    sha256_of_spec,
)
from celina_pkg.shortcuts.gear05_router import (  # noqa: E402
    Gear05Router,
    RouteDecision,
)
from celina_pkg.shortcuts.triggers import (  # noqa: E402
    TriggerBinding,
    TriggerDaemon,
    TriggerEvent,
)


# --------------------------------------------------------------------------- #
# ShortcutSpec + engine primitives
# --------------------------------------------------------------------------- #


def _make_spec() -> ShortcutSpec:
    return ShortcutSpec(
        id="study_mode",
        name="Deep Study Setup",
        description="Focus mode + timer",
        phrases=["start study mode", "time to study"],
        inputs=[
            ShortcutInput(name="duration_min", kind="number", required=False, default=45),
            ShortcutInput(name="app", kind="text", required=True),
        ],
        steps=[
            ShortcutStep(id="s1", action="focus.set_mode", args={"mode": "study"}, tier=1),
            ShortcutStep(
                id="s2",
                action="clock.start_timer",
                args={"minutes": "{{input.duration_min}}", "label": "Study"},
                tier=1,
            ),
            ShortcutStep(
                id="s3",
                action="notify.island",
                args={"body": "Timer {{input.duration_min}}m for {{steps.s1.output}}"},
                tier=2,
            ),
        ],
        allow_without_llm=True,
        preauthorized_tier2=False,
    )


def test_shortcut_spec_sha256_deterministic():
    spec = _make_spec()
    h1 = sha256_of_spec(spec)
    h2 = sha256_of_spec(spec)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex

    # Changing a step changes the hash
    spec2 = _make_spec()
    spec2.steps[0].args = {"mode": "focus"}
    assert sha256_of_spec(spec2) != h1


def test_resolve_magic_vars():
    ctx = {"input": {"duration_min": 30}, "steps": {"s1": {"output": "study"}}}
    assert resolve_magic_vars("{{input.duration_min}}m", ctx) == "30m"
    assert resolve_magic_vars("{{steps.s1.output}}", ctx) == "study"
    assert resolve_magic_vars("Timer {{input.duration_min}}m", ctx) == "Timer 30m"
    # Unresolved placeholder left as-is (fail-closed)
    assert resolve_magic_vars("{{input.missing}}", ctx) == "{{input.missing}}"
    # Non-string passthrough
    assert resolve_magic_vars(42, ctx) == 42
    assert resolve_magic_vars({"k": "{{input.duration_min}}"}, ctx) == {"k": 30}


def test_eval_when_safe_no_eval():
    ctx = {"input": {"duration_min": 30, "silent": True}}
    assert eval_when(None, ctx) is True
    assert eval_when("input.duration_min == 30", ctx) is True
    assert eval_when("input.duration_min != 30", ctx) is False
    assert eval_when("input.duration_min > 10", ctx) is True
    assert eval_when("input.duration_min < 5", ctx) is False
    assert eval_when("input.silent == true", ctx) is True
    assert eval_when("input.duration_min >= 30", ctx) is True
    assert eval_when("input.duration_min <= 29", ctx) is False
    # Bare path truthy
    assert eval_when("input.silent", ctx) is True
    # Fail-closed on bad comparison
    assert eval_when("input.duration_min > notanumber", ctx) is False


def test_shortcut_engine_tier2_gating():
    spec = _make_spec()
    approved = []
    async def approve(title: str, body: str) -> bool:
        approved.append((title, body))
        return True

    async def fake_handler(**kwargs):
        return {"ok": True}

    actions = {
        "focus.set_mode": fake_handler,
        "clock.start_timer": fake_handler,
        "notify.island": fake_handler,
    }
    engine = ShortcutEngine(actions, approve)

    async def run_approved():
        return await engine.run(spec, {"app": "Notes"}, require_approval=True)

    res = asyncio.run(run_approved())
    assert res["status"] == "ok"
    assert res["shortcut"] == "study_mode"
    assert len(approved) == 1  # one Tier-2 step (notify.island)
    assert "Study Setup" in approved[0][0]


def test_shortcut_engine_tier2_denied():
    spec = _make_spec()
    async def deny(title: str, body: str) -> bool:
        return False

    engine = ShortcutEngine({}, deny)
    res = asyncio.run(engine.run(spec, {"app": "Notes"}, require_approval=True))
    assert res["status"] == "cancelled"


def test_shortcut_engine_missing_required_input():
    spec = _make_spec()
    engine = ShortcutEngine({}, lambda t, b: asyncio.sleep(0) or True)
    res = asyncio.run(engine.run(spec, {}, require_approval=False))
    assert res["status"] == "error"
    assert "app" in res["error"]


def test_shortcut_engine_unregistered_action():
    spec = _make_spec()
    spec.steps = [ShortcutStep(id="s1", action="nonexistent.action", args={}, tier=1)]
    engine = ShortcutEngine({}, lambda t, b: asyncio.sleep(0) or True)
    res = asyncio.run(engine.run(spec, {"app": "Notes"}, require_approval=False))
    assert res["status"] == "error"
    assert "Unregistered action" in res["error"]


def test_shortcut_engine_successful_run():
    async def fake_handler(**kwargs):
        return {"ok": True, "echo": kwargs}

    engine = ShortcutEngine({"focus.set_mode": fake_handler}, lambda t, b: True)
    spec = ShortcutSpec(
        id="t", name="T", description="d",
        steps=[ShortcutStep(id="s1", action="focus.set_mode", args={"mode": "study"}, tier=1)],
    )
    res = asyncio.run(engine.run(spec, {}, require_approval=False))
    assert res["status"] == "ok"
    assert res["steps"]["s1"]["output"]["ok"] is True


# --------------------------------------------------------------------------- #
# Gear05Router
# --------------------------------------------------------------------------- #


def _router_specs() -> list[ShortcutSpec]:
    return [
        ShortcutSpec(
            id="study_mode",
            name="Study Mode",
            description="Deep study setup",
            phrases=["start study mode", "time to study"],
            inputs=[ShortcutInput(name="duration_min", kind="number", required=False, default=45)],
            steps=[],
        ),
        ShortcutSpec(
            id="set_volume",
            name="Set Volume",
            description="Adjust system volume",
            phrases=["set volume to", "volume"],
            inputs=[ShortcutInput(name="level", kind="number", required=True)],
            steps=[],
        ),
    ]


def test_gear0_exact_match():
    router = Gear05Router(_router_specs())
    t0 = time.perf_counter()
    dec = router.route("start study mode")
    dt_ms = (time.perf_counter() - t0) * 1000
    assert dec.gear == "gear0"
    assert dec.shortcut is not None
    assert dec.shortcut.id == "study_mode"
    assert dec.confidence == 1.0
    assert dt_ms < 25  # sub-LLM latency


def test_gear0_exact_with_slots():
    router = Gear05Router(_router_specs())
    dec = router.route("set volume to 70")
    assert dec.gear == "gear0"
    assert dec.shortcut.id == "set_volume"
    assert dec.extracted_inputs.get("level") == 70
    assert dec.missing_inputs == []


def test_gear0_missing_required_input_triggers_confirm():
    router = Gear05Router(_router_specs())
    dec = router.route("set volume")
    assert dec.gear == "gear0.5_confirm"
    assert dec.shortcut.id == "set_volume"
    assert "level" in dec.missing_inputs


def test_gear0_5_confirm_on_missing_optional():
    router = Gear05Router(_router_specs())
    # study_mode has optional duration_min with default -> gear0 even without it
    dec = router.route("time to study")
    assert dec.gear == "gear0"
    assert dec.shortcut.id == "study_mode"
    # Optional input not in query -> not extracted, but default fills it at run time
    assert dec.missing_inputs == []


def test_llm_fallback_when_no_match():
    router = Gear05Router(_router_specs())
    dec = router.route("explain quantum entanglement in detail")
    assert dec.gear == "llm"
    assert dec.shortcut is None
    assert 0.0 <= dec.confidence < 1.0


def test_route_under_25ms():
    router = Gear05Router(_router_specs() * 20)  # larger corpus
    for _ in range(50):
        t0 = time.perf_counter()
        router.route("start study mode")
        dt_ms = (time.perf_counter() - t0) * 1000
        assert dt_ms < 25


# --------------------------------------------------------------------------- #
# TriggerDaemon condition matching + cooldown
# --------------------------------------------------------------------------- #


def test_trigger_condition_matching():
    detail = {"battery_pct": 15, "on_ac": False}
    assert TriggerDaemon._matches("battery_pct < 20", detail) is True
    assert TriggerDaemon._matches("battery_pct == 15", detail) is True
    assert TriggerDaemon._matches("on_ac == false", detail) is True
    assert TriggerDaemon._matches("battery_pct > 20", detail) is False
    assert TriggerDaemon._matches("nonexistent == 1", detail) is False


def test_trigger_cooldown_enforced():
    engine = mock.MagicMock()
    engine.get_spec.return_value = ShortcutSpec(id="x", name="X", description="d", steps=[])
    engine.run = mock.AsyncMock(return_value={"status": "ok"})
    daemon = TriggerDaemon(engine)
    binding = TriggerBinding(
        event_type="power",
        condition="battery_pct < 20",
        shortcut_id="x",
        cooldown_sec=60.0,
    )
    daemon.add_binding(binding)

    async def run():
        await daemon._maybe_dispatch(TriggerEvent("power", {"battery_pct": 10}))
        assert engine.run.call_count == 1
        # Immediate second dispatch should be suppressed by cooldown
        await daemon._maybe_dispatch(TriggerEvent("power", {"battery_pct": 5}))
        assert engine.run.call_count == 1

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Crystallizer habit mining
# --------------------------------------------------------------------------- #


def test_signature_of_trace_deterministic():
    assert signature_of_trace(["a", "b"]) == signature_of_trace(["a", "b"])
    assert signature_of_trace(["a", "b"]) != signature_of_trace(["b", "a"])


def test_find_crystallizable_habits_repeats():
    traces = []
    for i in range(5):
        traces.append({
            "tool_calls": [
                {"action": "files.read", "args": {}, "tier": 1},
                {"action": "files.write_file", "args": {}, "tier": 2},
            ],
            "user_query": "copy file " + str(i),
            "status": "ok",
        })
    # One-off (different chain) should not qualify
    traces.append({
        "tool_calls": [{"action": "os.set_volume", "args": {}, "tier": 1}],
        "user_query": "volume up",
        "status": "ok",
    })
    candidates = find_crystallizable_habits(traces, min_repeats=3)
    assert len(candidates) == 1
    assert candidates[0]["id"].startswith("auto_")
    assert candidates[0]["steps"][0]["action"] == "files.read"


def test_find_crystallizable_habits_below_threshold():
    traces = [
        {
            "tool_calls": [
                {"action": "files.read", "args": {}, "tier": 1},
                {"action": "files.write_file", "args": {}, "tier": 2},
            ],
            "user_query": "copy file",
            "status": "ok",
        }
    ]
    candidates = find_crystallizable_habits(traces, min_repeats=3)
    assert candidates == []


def test_traces_from_ledger_groups_by_goal():
    class _FakeLedger:
        def list_entries(self, limit=1000):
            return [
                {"seq": 1, "root_goal_id": "g1", "tool": "files.read", "tier": 1, "outcome": "ok"},
                {"seq": 2, "root_goal_id": "g1", "tool": "files.write_file", "tier": 2, "outcome": "ok"},
                {"seq": 3, "root_goal_id": "g2", "tool": "os.set_volume", "tier": 1, "outcome": "ok"},
                {"seq": 4, "root_goal_id": "g1", "tool": "os.set_volume", "tier": 1, "outcome": "error"},  # skipped
            ]

    traces = traces_from_ledger(_FakeLedger())
    assert len(traces) == 1  # only g1 has >=2 ok calls
    assert traces[0]["user_query"] == "g1"
    assert traces[0]["tool_calls"][0]["action"] == "files.read"
    assert traces[0]["tool_calls"][1]["action"] == "files.write_file"


def test_crystallize_stages_to_quarantine(tmp_path: Path):
    class _FakeLedger:
        def list_entries(self, limit=1000):
            entries = []
            for i in range(4):  # 4 separate sessions/goals with same signature
                entries.extend([
                    {"seq": len(entries) + 1, "root_goal_id": "copy_" + str(i), "tool": "files.read", "tier": 1, "outcome": "ok"},
                    {"seq": len(entries) + 1, "root_goal_id": "copy_" + str(i), "tool": "files.write_file", "tier": 2, "outcome": "ok"},
                ])
            return entries

    quarantine = tmp_path / "shortcuts" / "quarantine"
    result = crystallize(_FakeLedger(), quarantine, min_repeats=3, skilspector_scan=lambda code, timeout=30: True)
    assert result["candidates_found"] == 1
    assert len(result["staged"]) == 1
    assert result["staged"][0]["id"].startswith("auto_")
    assert Path(result["staged"][0]["path"]).exists()
    assert result["skipped"] == []


def test_crystallize_skips_sandbox_failure(tmp_path: Path):
    class _FakeLedger:
        def list_entries(self, limit=1000):
            entries = []
            for i in range(4):
                entries.extend([
                    {"seq": len(entries) + 1, "root_goal_id": "copy_" + str(i), "tool": "files.read", "tier": 1, "outcome": "ok"},
                    {"seq": len(entries) + 1, "root_goal_id": "copy_" + str(i), "tool": "files.write_file", "tier": 2, "outcome": "ok"},
                ])
            return entries

    quarantine = tmp_path / "shortcuts" / "quarantine"
    result = crystallize(
        _FakeLedger(), quarantine, min_repeats=3,
        skilspector_scan=lambda code, timeout=30: False,
    )
    assert result["candidates_found"] == 1
    assert result["staged"] == []
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["reason"] == "sandbox_validation_failed"
