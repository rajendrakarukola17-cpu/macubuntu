# Copyright 2026 Celina contributors.
"""Tests for immutable L0 contracts and default-off feature flags."""

import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from celina_pkg.selfimprove.contracts import (
    AttemptRecord,
    BudgetLimits,
    BudgetSnapshot,
    CritiqueResult,
    EvalReport,
    PatchProposal,
    TaskSpec,
    VerifierResult,
)
from celina_pkg.selfimprove.flags import SelfImproveFlags, is_layer_enabled, load_flags

SUBFLAGS = ("verifiers", "eval_harness", "memory", "loop", "optimize", "patcher")


def test_flags_default_off() -> None:
    """Defaults and empty configuration cannot enable any feature."""
    assert SelfImproveFlags() == load_flags(env={})
    assert not any(getattr(SelfImproveFlags(), field.name) for field in fields(SelfImproveFlags))


@pytest.mark.parametrize("value", ["0", "false", "off", "no", "", "maybe"])
def test_master_kill_switch_overrides_config(value: str) -> None:
    """Any non-opt-in master environment value disables everything."""
    config = {"enabled": True, **dict.fromkeys(SUBFLAGS, True)}
    result = load_flags(config, {"SELFIMPROVE_ENABLED": value})
    assert not any(getattr(result, field.name) for field in fields(result))


def test_explicit_opt_in_and_subflag_precedence() -> None:
    """Subflags require a true master and must themselves be explicitly enabled."""
    assert load_flags({"enabled": True, "memory": True}, {}).memory
    flags = load_flags({"enabled": True, "memory": True}, {"SELFIMPROVE_ENABLED": "1"})
    assert flags.enabled
    assert flags.memory
    assert not flags.patcher
    assert load_flags({"memory": True}, {"SELFIMPROVE_ENABLED": "yes"}).memory
    assert not load_flags({"enabled": False, "loop": True}, {}).loop
    assert not SelfImproveFlags(enabled=False, loop=True).loop
    assert not load_flags({"enabled": "yes", "loop": True}, {}).enabled
    assert load_flags({"enabled": True}, {"SELFIMPROVE_PATCHER": "on"}).patcher
    assert not load_flags(
        {"enabled": True, "patcher": True}, {"SELFIMPROVE_PATCHER": "maybe"},
    ).patcher
    assert is_layer_enabled(flags, "memory")
    assert not is_layer_enabled(flags, "patcher")
    assert not is_layer_enabled(flags, "enabled")
    assert not is_layer_enabled(flags, "unknown")


@pytest.mark.parametrize(
    "instance",
    [
        TaskSpec("task-1", "goal"),
        BudgetLimits(1, 10, 5.0, 0, 0),
        BudgetSnapshot(0, 0, 0.0, 0, 0, 1, 10, 5.0, 0, 0, exhausted=True),
        VerifierResult("verifier-1", "pass", 1.0, "ok"),
        CritiqueResult("task-1", 0.5, "feedback"),
        AttemptRecord("attempt-1", "task-1", "pending", 0),
        PatchProposal("proposal-1", "task-1", "diff"),
        EvalReport("task-1", "pass", 1.0, 1),
    ],
)
def test_contracts_are_frozen(instance: object) -> None:
    """Every contract rejects reassignment of its fields."""
    first = fields(instance)[0]
    with pytest.raises(FrozenInstanceError):
        setattr(instance, first.name, getattr(instance, first.name))


@pytest.mark.parametrize(
    ("constructor", "args"),
    [
        (TaskSpec, ("", "goal")),
        (TaskSpec, ("id", " ")),
        (BudgetLimits, (0, 1, 1.0, 0, 0)),
        (BudgetLimits, (1, 1, 1.0, -1, 0)),
        (BudgetSnapshot, (-1, 0, 0.0, 0, 0, 1, 1, 1.0, 0, 0, False)),
        (VerifierResult, ("", "pass", 1.0, "ok")),
        (VerifierResult, ("verifier", "bad", 0.5, "ok")),
        (VerifierResult, ("verifier", "pass", -0.1, "ok")),
        (CritiqueResult, ("task", float("nan"), "feedback")),
        (AttemptRecord, ("attempt", "task", "pending", -1)),
        (AttemptRecord, ("attempt", "task", "unknown", 0)),
        (PatchProposal, ("proposal", "", "diff")),
        (EvalReport, ("task", "pass", 1.1, 0)),
        (EvalReport, ("task", "unknown", 0.5, 0)),
    ],
)
def test_invalid_contracts_are_rejected(constructor: object, args: tuple[object, ...]) -> None:
    """Invalid identifiers, statuses, values and counts fail closed."""
    with pytest.raises((ValueError, TypeError)):
        constructor(*args)


def test_import_has_no_home_io_or_existing_config_router_impact(tmp_path: Path) -> None:
    """Imports do not touch HOME or mutate loaded configuration/router symbols."""
    root = Path(__file__).resolve().parents[2]
    env = {
        "HOME": str(tmp_path),
        "DRY_RUN": "1",
        "PYTHONPATH": str(root / "features" / "celina"),
        "PATH": os.defpath,
    }
    clean_code = """\
from celina_pkg.selfimprove import contracts, flags
assert flags.load_flags(env={}).enabled is False
"""
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and fixed test code.
        [sys.executable, "-c", clean_code], env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
    integration_code = """\
from pathlib import Path
from celina_pkg import config, router
home = Path.home()
before = sorted(str(p.relative_to(home)) for p in home.rglob('*'))
checks = (config._safe_int('7', 2), router.CelinaRouter.ask, router.route_query_with_meta)
from celina_pkg.selfimprove import contracts, flags
assert checks == (config._safe_int('7', 2), router.CelinaRouter.ask, router.route_query_with_meta)
assert sorted(str(p.relative_to(home)) for p in home.rglob('*')) == before
assert flags.load_flags(env={}).enabled is False
"""
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and fixed test code.
        [sys.executable, "-c", integration_code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_invalid_flag_and_snapshot_types_are_rejected() -> None:
    """Flags and exhaustion state require actual booleans."""
    with pytest.raises(ValueError, match="bool"):
        SelfImproveFlags(enabled=1)
    with pytest.raises(ValueError, match="bool"):
        BudgetSnapshot(0, 0, 0.0, 0, 0, 1, 1, 1.0, 0, 0, exhausted=1)
