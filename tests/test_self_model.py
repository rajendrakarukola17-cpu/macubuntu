"""
Regression tests for features/celina/celina_pkg/self_model.py (P1 truth layer).

Covers:
  - Persistence to SQLite WAL (survives restart) — audit item 5
  - EWMA decay weighting — audit item 5
  - Context keys (tool x online/offline x gear) — audit item 5
  - Signed bias vs Brier — audit item 6
  - Wilson score lower bound — audit item 7
  - Blind spot for unproven tools (attempts < 5) — audit item 7
  - Calibration ledger: prediction + independent verification — audit item 8
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from celina_pkg.self_model import (
    MIN_ATTEMPTS_FOR_PROVEN,
    SelfModel,
    _context_key,
    _ewma_weight,
    _wilson_lower_bound,
    get_self_model,
)


# ------------------------------------------------------------------
# Fixture: isolated SelfModel with temp DB
# ------------------------------------------------------------------

@pytest.fixture
def model():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "memory.db"
        m = SelfModel(db_path=db_path)
        yield m


# ------------------------------------------------------------------
# Context keys
# ------------------------------------------------------------------

def test_context_key_format():
    assert _context_key("write_file", online=False, gear=2) == "write_file:offline:gear2"
    assert _context_key("web_search", online=True, gear=3) == "web_search:online:gear3"


# ------------------------------------------------------------------
# Record + stats (persistence, audit item 5)
# ------------------------------------------------------------------

def test_record_and_get_stats(model):
    model.record_outcome_full(
        "act-1", "write_file", 0.8, True, online=False, gear=2
    )
    model.record_outcome_full(
        "act-2", "write_file", 0.9, False, online=False, gear=2
    )
    model.record_outcome_full(
        "act-3", "write_file", 0.7, True, online=False, gear=2
    )

    stats = model.get_stats("write_file", "write_file:offline:gear2")
    assert stats["attempts"] == 3
    assert stats["success_rate"] == pytest.approx(2 / 3, abs=0.0001)
    assert stats["is_unproven"] is True  # 3 < 5


def test_stats_persist_across_instances(model):
    model.record_outcome_full("act-1", "edit_file", 0.5, True, online=True, gear=1)
    # New instance pointing to same DB should see the data
    m2 = SelfModel(db_path=model.db_path)
    stats = m2.get_stats("edit_file", "edit_file:online:gear1")
    assert stats["attempts"] == 1
    assert stats["success_rate"] == 1.0


def test_context_isolation(model):
    model.record_outcome_full("a1", "web_search", 0.9, True, online=False, gear=2)
    model.record_outcome_full("a2", "web_search", 0.5, False, online=True, gear=2)

    off = model.get_stats("web_search", "web_search:offline:gear2")
    on = model.get_stats("web_search", "web_search:online:gear2")
    assert off["attempts"] == 1 and off["success_rate"] == 1.0
    assert on["attempts"] == 1 and on["success_rate"] == 0.0


# ------------------------------------------------------------------
# EWMA decay (audit item 5)
# ------------------------------------------------------------------

def test_ewma_weight_decays_over_time():
    now = 1000000.0
    recent = _ewma_weight(now - 86400, now=now)  # 1 day old
    old = _ewma_weight(now - 86400 * 60, now=now)  # 60 days old
    assert recent > old
    assert recent <= 1.0
    assert old > 0.0


def test_ewma_affects_effective_attempts(model):
    now = 1000000.0
    # Record 10 outcomes, all old (weight ~0)
    for i in range(10):
        model.record_outcome_full(f"a{i}", "tool_x", 0.5, True, online=False, gear=1)
    # Manually set old timestamps
    conn = sqlite3.connect(str(model.db_path))
    conn.execute("UPDATE tool_outcomes SET ts = ?", (now - 86400 * 400,))  # 400 days old
    conn.commit()
    conn.close()

    stats = model.get_stats("tool_x", "tool_x:offline:gear1", now=now)
    assert stats["attempts"] == 10
    # Effective attempts should be much lower due to decay
    assert stats["effective_attempts"] < stats["attempts"]


# ------------------------------------------------------------------
# Signed bias (audit item 6)
# ------------------------------------------------------------------

def test_signed_bias_overconfident(model):
    # Predict 0.9 but only succeed 0.5 of the time
    for i in range(10):
        model.record_outcome_full(f"a{i}", "tool_y", 0.9, i % 2 == 0, online=False, gear=1)
    stats = model.get_stats("tool_y", "tool_y:offline:gear1")
    assert stats["signed_bias"] > 0  # overconfident
    assert stats["predicted_mean"] == 0.9
    assert stats["success_rate"] == 0.5


def test_signed_bias_underconfident(model):
    # Predict 0.3 but succeed 0.8 of the time
    for i in range(10):
        model.record_outcome_full(f"a{i}", "tool_z", 0.3, i % 5 != 0, online=False, gear=1)
    stats = model.get_stats("tool_z", "tool_z:offline:gear1")
    assert stats["signed_bias"] < 0  # underconfident


# ------------------------------------------------------------------
# Wilson score (audit item 7)
# ------------------------------------------------------------------

def test_wilson_zero_attempts():
    assert _wilson_lower_bound(0, 0) == 0.0


def test_wilson_perfect_small_sample():
    # 5/5 success should NOT be 1.0 — honest uncertainty
    wl = _wilson_lower_bound(5, 5)
    assert 0.0 < wl < 1.0
    assert wl < 0.95  # Not enough data for earned autonomy


def test_wilson_large_sample_perfect():
    # 100/100 should be close to 1.0
    wl = _wilson_lower_bound(100, 100)
    assert wl > 0.95


def test_is_unproven_under_5(model):
    for i in range(4):
        model.record_outcome_full(f"a{i}", "tool_w", 0.5, True, online=False, gear=1)
    assert model.is_unproven("tool_w", online=False, gear=1) is True


def test_is_proven_at_5(model):
    for i in range(5):
        model.record_outcome_full(f"a{i}", "tool_w", 0.5, True, online=False, gear=1)
    assert model.is_unproven("tool_w", online=False, gear=1) is False


# ------------------------------------------------------------------
# Calibration ledger (audit item 8)
# ------------------------------------------------------------------

def test_two_phase_prediction_then_outcome(model):
    # Phase 1: record prediction at dispatch
    model.record_prediction("act-cal", "write_file", 0.85, online=False, gear=2)
    # Phase 2: record verified outcome from independent verification
    model.record_outcome("act-cal", True)

    stats = model.get_stats("write_file", "write_file:offline:gear2")
    assert stats["attempts"] == 1
    assert stats["predicted_mean"] == 0.85
    assert stats["success_rate"] == 1.0


def test_outcome_without_prediction_is_ignored(model):
    # record_outcome for non-existent action_id should be a no-op
    model.record_outcome("nonexistent", True)
    stats = model.get_stats("any_tool", "any_tool:offline:gear1")
    assert stats["attempts"] == 0


def test_brier_secondary_not_primary(model):
    # Brier penalizes honest 50/50; signed_bias doesn't
    for i in range(10):
        model.record_outcome_full(f"a{i}", "tool_b", 0.5, i % 2 == 0, online=False, gear=1)
    stats = model.get_stats("tool_b", "tool_b:offline:gear1")
    assert abs(stats["signed_bias"]) < 0.01  # perfectly calibrated
    # Brier for 0.5 predictions on 50/50 outcomes = 0.25 (not a failure)
    assert stats["brier"] == pytest.approx(0.25, abs=0.01)


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

def test_get_self_model_singleton():
    m1 = get_self_model()
    m2 = get_self_model()
    assert m1 is m2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
