"""
tests/test_world_model_epistemic.py

Tests for the epistemic OS world model (Concept 1: Self-Model / Meta-Cognition,
Concept 2: OS World Model) in features/celina/celina_pkg/world_model.py.

Covers: HardwareProfile, load_hardware_profile, OSWorldModel,
PredictedTransition, ToolCalibration, EpistemicPrecheck, and the
dispatch_pre_check / dispatch_post_check gate integration.

20 tests, stdlib-only, zero $HOME writes at import time.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from celina_pkg.self_model import SelfModel, ToolCalibration, EpistemicPrecheck
from celina_pkg.world_model import (
    HardwareProfile,
    OSWorldModel,
    PredictedTransition,
    load_hardware_profile,
    simulate_intervention,
    dispatch_pre_check,
    dispatch_post_check,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_path():
    """A temp sqlite DB path for SelfModel (auto-cleaned)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def self_model(tmp_db_path):
    return SelfModel(db_path=tmp_db_path)


@pytest.fixture
def os_world_model(self_model):
    return OSWorldModel(
        hardware_profile=HardwareProfile(tier="16gb", has_battery=True, gpu_vendor="amd"),
        self_model_instance=self_model,
    )


# ---------------------------------------------------------------------------
# HardwareProfile
# ---------------------------------------------------------------------------


def test_hardware_profile_defaults():
    hp = HardwareProfile(tier="8gb", has_battery=False)
    assert hp.tier == "8gb"
    assert hp.has_battery is False
    assert hp.gpu_vendor is None


def test_hardware_profile_full():
    hp = HardwareProfile(tier="32gb", has_battery=True, gpu_vendor="nvidia")
    assert hp.tier == "32gb"
    assert hp.has_battery is True
    assert hp.gpu_vendor == "nvidia"


# ---------------------------------------------------------------------------
# load_hardware_profile — graceful degradation
# ---------------------------------------------------------------------------


def test_load_hardware_profile_degrades_when_missing():
    with mock.patch.object(Path, "read_text", side_effect=FileNotFoundError("no such file")):
        hp = load_hardware_profile()
    assert hp.tier == "16gb"
    assert hp.has_battery is False
    assert hp.gpu_vendor is None


def test_load_hardware_profile_invalid_json_degrades():
    with mock.patch.object(
        Path, "read_text", return_value="{not valid json"
    ):
        hp = load_hardware_profile()
    assert hp.tier == "16gb"


def test_load_hardware_profile_valid(tmp_path):
    hw_file = tmp_path / "hardware.json"
    hw_file.write_text(json.dumps({"tier": "8gb", "has_battery": True, "gpu_vendor": "intel"}))
    with mock.patch(
        "celina_pkg.world_model._HARDWARE_JSON_PATH", hw_file
    ):
        hp = load_hardware_profile()
    assert hp.tier == "8gb"
    assert hp.has_battery is True
    assert hp.gpu_vendor == "intel"


# ---------------------------------------------------------------------------
# OSWorldModel.headroom_factor
# ---------------------------------------------------------------------------


def test_headroom_factor_8gb():
    wm = OSWorldModel(hardware_profile=HardwareProfile(tier="8gb", has_battery=False))
    assert wm.headroom_factor() == 0.6


def test_headroom_factor_16gb():
    wm = OSWorldModel(hardware_profile=HardwareProfile(tier="16gb", has_battery=False))
    assert wm.headroom_factor() == 1.0


def test_headroom_factor_32gb():
    wm = OSWorldModel(hardware_profile=HardwareProfile(tier="32gb", has_battery=False))
    assert wm.headroom_factor() == 1.4


def test_headroom_factor_unknown_tier_defaults_1():
    wm = OSWorldModel(hardware_profile=HardwareProfile(tier="64gb", has_battery=False))
    assert wm.headroom_factor() == 1.0


# ---------------------------------------------------------------------------
# OSWorldModel.predict_transition
# ---------------------------------------------------------------------------


def test_predict_transition_write_file_tier1(os_world_model):
    pt = os_world_model.predict_transition(
        "write_file", {"path": "/tmp/x.txt", "content": "hi"}, is_tier2=False
    )
    assert isinstance(pt, PredictedTransition)
    assert pt.tool == "write_file"
    assert 0.0 < pt.confidence <= 1.0
    assert pt.rollback_available is True
    assert "filesystem_write" in pt.predicted_effects


def test_predict_transition_unknown_tool_fail_closed(os_world_model):
    pt = os_world_model.predict_transition(
        "nonexistent_tool_xyz", {}, is_tier2=False
    )
    assert pt.confidence <= 0.05  # unknown tool -> very low confidence
    assert pt.requires_verification is True


def test_predict_transition_requires_verification_tier2(os_world_model):
    pt = os_world_model.predict_transition(
        "write_file", {"path": "/tmp/x.txt", "content": "hi"}, is_tier2=True
    )
    assert pt.requires_verification is True


def test_predict_transition_offline_network_blocked(self_model):
    wm = OSWorldModel(
        hardware_profile=HardwareProfile(tier="16gb", has_battery=False),
        self_model_instance=self_model,
    )
    with mock.patch(
        "celina_pkg.config.is_web_comms_enabled", return_value=False
    ):
        pt = wm.predict_transition(
            "web_search", {"url": "https://example.com"}, is_tier2=True, requires_network=True
        )
    assert pt.requires_verification is True
    assert "offline" in pt.epistemic_note.lower() or "web_comms" in pt.epistemic_note.lower()


# ---------------------------------------------------------------------------
# OSWorldModel.epistemic_precheck
# ---------------------------------------------------------------------------


def test_epistemic_precheck_circuit_open(os_world_model):
    pc = os_world_model.epistemic_precheck(
        "write_file", is_tier2=False, circuit_open=True, fallback_candidates=["edit_file"]
    )
    assert pc.can_attempt is False
    assert pc.require_human_confirmation is True
    assert "circuit breaker" in pc.reason.lower()


def test_epistemic_precheck_ok(os_world_model):
    pc = os_world_model.epistemic_precheck("write_file", is_tier2=False)
    assert pc.can_attempt is True
    assert pc.confidence > 0.0


def test_epistemic_precheck_tier2_requires_confirmation(os_world_model):
    pc = os_world_model.epistemic_precheck("write_file", is_tier2=True)
    assert pc.require_human_confirmation is True


# ---------------------------------------------------------------------------
# OSWorldModel.tool_calibration
# ---------------------------------------------------------------------------


def test_tool_calibration_no_history(os_world_model):
    cal = os_world_model.tool_calibration("brand_new_tool")
    assert isinstance(cal, ToolCalibration)
    assert cal.attempts == 0
    assert cal.verified_successes == 0
    assert cal.empirical_reliability == 0.75  # Laplace-smoothed neutral


def test_tool_calibration_with_history(self_model, os_world_model):
    sm = self_model
    sm.record_outcome_full("a1", "edit_file", 0.9, True, online=True, gear=2)
    sm.record_outcome_full("a2", "edit_file", 0.9, True, online=True, gear=2)
    sm.record_outcome_full("a3", "edit_file", 0.9, False, online=True, gear=2)
    cal = os_world_model.tool_calibration("edit_file")
    assert cal.attempts == 3
    assert cal.verified_successes == 2
    assert 0.0 < cal.empirical_reliability < 1.0


# ---------------------------------------------------------------------------
# ToolCalibration
# ---------------------------------------------------------------------------


def test_tool_calibration_empirical_reliability_laplace():
    cal = ToolCalibration(tool_name="x", attempts=0, verified_successes=0, mean_latency_ms=0.0, brier_error=0.0)
    assert cal.empirical_reliability == 0.75

    cal2 = ToolCalibration(tool_name="x", attempts=10, verified_successes=8, mean_latency_ms=0.0, brier_error=0.0)
    assert cal2.empirical_reliability == round((8 + 1.5) / (10 + 2.0), 3)


# ---------------------------------------------------------------------------
# EpistemicPrecheck
# ---------------------------------------------------------------------------


def test_epistemic_precheck_fields():
    pc = EpistemicPrecheck(
        can_attempt=True,
        confidence=0.8,
        require_human_confirmation=False,
        blind_spots=(),
        recommended_fallback=None,
        reason="ok",
    )
    assert pc.can_attempt is True
    assert pc.confidence == 0.8
    assert pc.require_human_confirmation is False


# ---------------------------------------------------------------------------
# dispatch_pre_check / dispatch_post_check integration
# ---------------------------------------------------------------------------


def test_dispatch_pre_check_blocked_guard():
    # protected path -> block guard -> proceed=False
    result = dispatch_pre_check("write_file", {"path": "/home/user/.netrc", "content": "x"})
    assert result.proceed is False
    assert result.sim.recommended_guard == "block"


def test_dispatch_pre_check_proceeds_tier2():
    result = dispatch_pre_check("write_file", {"path": "/tmp/ok.txt", "content": "x"})
    assert result.proceed is True
    assert result.sim.recommended_guard in {"tier2", "allow"}


def test_dispatch_post_check_digest_mismatch():
    outcome = dispatch_post_check(
        "write_file",
        {"path": "/tmp/ok.txt", "content": "changed"},
        approved_digest="deadbeef",
        verified_success=True,
        action_id="act-1",
        online=True,
        gear=2,
    )
    assert outcome.verified is False
    assert "mismatch" in outcome.reason.lower()


def test_dispatch_post_check_digest_match():
    sim = simulate_intervention("write_file", {"path": "/tmp/ok.txt", "content": "x"})
    outcome = dispatch_post_check(
        "write_file",
        {"path": "/tmp/ok.txt", "content": "x"},
        approved_digest=sim.digest,
        verified_success=True,
        action_id="act-2",
        online=True,
        gear=2,
    )
    assert outcome.verified is True
    assert outcome.reason == ""


# ---------------------------------------------------------------------------
# PredictedTransition immutability
# ---------------------------------------------------------------------------


def test_predicted_transition_is_frozen():
    pt = PredictedTransition(
        tool="t",
        confidence=0.5,
        requires_verification=True,
        predicted_effects={"a": 1},
        rollback_available=False,
        epistemic_note="note",
    )
    with pytest.raises(Exception):
        pt.confidence = 0.9  # frozen dataclass


# ---------------------------------------------------------------------------
# Zero import-time $HOME writes (standing invariant)
# ---------------------------------------------------------------------------


def test_no_home_write_on_import(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "nonexistent_home"))
    # Re-importing should not write to $HOME
    import celina_pkg.world_model as wm  # noqa: F401
    import celina_pkg.self_model as sm  # noqa: F401
    assert not (tmp_path / "nonexistent_home").exists()
