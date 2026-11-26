"""
Tests for features/celina/celina_pkg/context_compressor.py
Tier-Adaptive Context Compression & Per-Tool Token Ledger (8 GB / 16 GB / 32 GB)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

import pytest  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_msg(tool_name: str, content: str, role: str = "tool") -> dict:
    return {"role": role, "name": tool_name, "content": content}


# ---------------------------------------------------------------------------
# Telemetry / offline env locks
# ---------------------------------------------------------------------------


def test_telemetry_offline_env_locks():
    """T1: Telemetry/offline env locks set on import with zero $HOME disk side effects."""
    import celina_pkg.context_compressor as cc

    assert os.environ.get("HEADROOM_OFFLINE") == "1"
    assert os.environ.get("HEADROOM_BEACON") == "off"
    assert os.environ.get("DO_NOT_TRACK") == "1"

    # Zero disk writes: importing must not create files in $HOME
    home = Path.home()
    before = set(home.iterdir())
    import importlib
    importlib.reload(cc)
    after = set(home.iterdir())
    assert before == after, f"Import created files in $HOME: {after - before}"


# ---------------------------------------------------------------------------
# Tier detection
# ---------------------------------------------------------------------------


def test_tier_8gb_explicit_override():
    """T2: tier_override=8gb returns 8gb policy."""
    from celina_pkg.context_compressor import detect_effective_tier, TIER_POLICIES

    policy, reason = detect_effective_tier(tier_override="8gb")
    assert policy.tier == "8gb"
    assert policy.local_min_chars == 800
    assert policy.cloud_min_chars == 800
    assert reason == "override:8gb"


def test_tier_16gb_explicit_override():
    """T3: tier_override=16gb returns 16gb policy with higher thresholds."""
    from celina_pkg.context_compressor import detect_effective_tier

    policy, _ = detect_effective_tier(tier_override="16gb")
    assert policy.tier == "16gb"
    assert policy.local_min_chars == 2400
    assert policy.cloud_min_chars == 1200


def test_tier_32gb_explicit_override():
    """T4: tier_override=32gb returns 32gb policy with gentle thresholds."""
    from celina_pkg.context_compressor import detect_effective_tier

    policy, _ = detect_effective_tier(tier_override="32gb")
    assert policy.tier == "32gb"
    assert policy.local_min_chars == 6000
    assert policy.cloud_min_chars == 1800


def test_turbo_lite_mode_forces_8gb():
    """T5: CELINA_LITE_MODE=1 forces 8gb tier regardless of hardware.json."""
    from celina_pkg.context_compressor import detect_effective_tier

    with mock.patch.dict(os.environ, {"CELINA_LITE_MODE": "1"}, clear=False):
        policy, reason = detect_effective_tier()
        assert policy.tier == "8gb"
        assert reason == "turbo_lite_mode"


def test_psi_stepdown_16gb_to_8gb():
    """T6: Live PSI avg10 >= threshold steps 16gb down to 8gb."""
    from celina_pkg.context_compressor import detect_effective_tier
    from celina_pkg import context_compressor as cc

    with mock.patch.object(cc, "_read_psi_some_avg10", return_value=18.5):
        policy, reason = detect_effective_tier(tier_override="16gb")
        assert policy.tier == "8gb"
        assert "psi_stepdown" in reason


def test_psi_below_threshold_stays_16gb():
    """T7: PSI below threshold keeps 16gb tier."""
    from celina_pkg.context_compressor import detect_effective_tier
    from celina_pkg import context_compressor as cc

    with mock.patch.object(cc, "_read_psi_some_avg10", return_value=5.0):
        policy, _ = detect_effective_tier(tier_override="16gb")
        assert policy.tier == "16gb"


# ---------------------------------------------------------------------------
# Compression bypass thresholds
# ---------------------------------------------------------------------------


def test_1500_char_output_compresses_on_8gb():
    """T8: 1,500-char local output compresses on 8gb (>= 800)."""
    from celina_pkg.context_compressor import compress_messages_for_llm

    big = "x" * 1500
    msgs = [_make_tool_msg("grep_code", big)]
    report = compress_messages_for_llm(msgs, tier_override="8gb", route="local")
    assert report.backend != "bypass", f"Expected compression on 8gb, got {report.backend}"


def test_1500_char_output_bypasses_on_16gb_local():
    """T9: 1,500-char local output bypasses on 16gb (< 2400)."""
    from celina_pkg.context_compressor import compress_messages_for_llm

    big = "x" * 1500
    msgs = [_make_tool_msg("grep_code", big)]
    report = compress_messages_for_llm(msgs, tier_override="16gb", route="local")
    assert report.backend == "bypass"
    assert report.chars_after == report.chars_before


def test_1500_char_output_bypasses_on_32gb_local():
    """T10: 1,500-char local output bypasses on 32gb (< 6000)."""
    from celina_pkg.context_compressor import compress_messages_for_llm

    big = "x" * 1500
    msgs = [_make_tool_msg("grep_code", big)]
    report = compress_messages_for_llm(msgs, tier_override="32gb", route="local")
    assert report.backend == "bypass"
    assert report.chars_after == report.chars_before


def test_1500_char_output_compresses_on_16gb_cloud():
    """T11: 1,500-char cloud output compresses on 16gb (>= 1200)."""
    from celina_pkg.context_compressor import compress_messages_for_llm

    big = "x" * 1500
    msgs = [_make_tool_msg("grep_code", big)]
    report = compress_messages_for_llm(msgs, tier_override="16gb", route="cloud")
    assert report.backend != "bypass", f"Expected compression on 16gb cloud, got {report.backend}"


def test_system_user_messages_never_compressed():
    """T12: system and user messages are NEVER modified."""
    from celina_pkg.context_compressor import compress_messages_for_llm

    big_tool = "y" * 5000
    msgs = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is 2+2?"},
        _make_tool_msg("read_file", big_tool),
    ]
    report = compress_messages_for_llm(msgs, tier_override="8gb", route="local")
    assert report.messages[0]["content"] == "You are a helpful assistant."
    assert report.messages[1]["content"] == "What is 2+2?"


# ---------------------------------------------------------------------------
# Reversible CCR round-trip
# ---------------------------------------------------------------------------


def test_ccr_round_trip_json():
    """T13: Compressing large pretty-printed JSON attaches [CCR:<id>] and retrieve returns exact original."""
    from celina_pkg.context_compressor import (
        compress_messages_for_llm,
        retrieve_compressed_context,
        clear_compression_state,
    )

    clear_compression_state()
    data = [{"id": i, "name": f"item_{i}", "value": i * 10, "notes": "a" * 50} for i in range(50)]
    pretty = json.dumps(data, indent=2)
    msgs = [_make_tool_msg("read_file", pretty)]
    report = compress_messages_for_llm(msgs, tier_override="8gb", route="local")

    ccr_ids = list(report.ccr_ids)
    assert len(ccr_ids) > 0, f"Expected CCR handle, got {report.backend}"

    # Retrieve full original (use large max_chars since content is ~6.5KB)
    original = retrieve_compressed_context(ccr_ids[0], max_chars=10000)
    assert original == pretty, "CCR retrieve did not return exact original"

    # Clear wipes it
    cleared = clear_compression_state()
    assert cleared["ccr_cleared"] > 0
    not_found = retrieve_compressed_context(ccr_ids[0])
    assert "not found" in not_found


def test_ccr_query_filter():
    """T14: CCR retrieve supports query= filtering."""
    from celina_pkg.context_compressor import (
        compress_messages_for_llm,
        retrieve_compressed_context,
        clear_compression_state,
    )

    clear_compression_state()
    # Build a log with consecutive duplicate lines large enough to cross the 800-char threshold
    # and compress via duplicate-line collapsing (stdlib path)
    log = "ERROR: connection refused\n" * 100  # 100 consecutive identical lines
    msgs = [_make_tool_msg("search_in_files", log)]
    report = compress_messages_for_llm(msgs, tier_override="8gb", route="local")

    ccr_ids = list(report.ccr_ids)
    assert len(ccr_ids) > 0, f"Expected CCR handle, got backend={report.backend}"

    filtered = retrieve_compressed_context(ccr_ids[0], query="ERROR")
    assert "ERROR" in filtered
    # All lines are identical ERROR lines, so no INFO present
    assert "INFO" not in filtered


def test_small_output_below_per_msg_threshold_not_ccr():
    """T15: Small tool output below per_msg_min_chars gets no CCR handle."""
    from celina_pkg.context_compressor import (
        compress_messages_for_llm,
        clear_compression_state,
    )

    clear_compression_state()
    small = "short output"
    msgs = [_make_tool_msg("read_file", small)]
    report = compress_messages_for_llm(msgs, tier_override="8gb", route="local")
    assert len(report.ccr_ids) == 0


# ---------------------------------------------------------------------------
# Token ledger
# ---------------------------------------------------------------------------


def test_token_tracker_records_without_secrets():
    """T16: TokenMetricsTracker records metadata without storing raw strings."""
    from celina_pkg.context_compressor import get_token_tracker

    tracker = get_token_tracker()
    tracker.reset()
    tracker.record_turn(
        tokens_before=1000,
        tokens_after=800,
        per_tool_tokens={"read_file": (600, 400), "grep_code": (400, 400)},
    )
    snap = tracker.snapshot()
    assert snap["turns"] == 1
    assert snap["tokens_before"] == 1000
    assert snap["tokens_after"] == 800
    assert snap["saved_tokens"] == 200
    assert snap["saved_pct"] == 20.0

    # No raw content stored
    report = tracker.format_report()
    assert "secret" not in report.lower()
    assert "password" not in report.lower()

    by_tool = {r["tool"]: r for r in snap["by_tool"]}
    assert by_tool["read_file"]["tokens_before"] == 600
    assert by_tool["read_file"]["tokens_after"] == 400
    assert by_tool["read_file"]["saved_tokens"] == 200


def test_token_tracker_reset():
    """T17: Token tracker reset clears all state."""
    from celina_pkg.context_compressor import get_token_tracker

    tracker = get_token_tracker()
    tracker.record_turn(tokens_before=500, tokens_after=400, per_tool_tokens={"x": (500, 400)})
    tracker.reset()
    snap = tracker.snapshot()
    assert snap["turns"] == 0
    assert snap["tokens_before"] == 0
    assert snap["tokens_after"] == 0
    assert snap["by_tool"] == []


# ---------------------------------------------------------------------------
# Headroom fallback
# ---------------------------------------------------------------------------


def test_headroom_unavailable_falls_back_to_stdlib():
    """T18: When headroom-ai not installed, stdlib fallback works cleanly."""
    from celina_pkg.context_compressor import compress_messages_for_llm, HEADROOM_AVAILABLE

    # Force headroom unavailable
    with mock.patch("celina_pkg.context_compressor.HEADROOM_AVAILABLE", False):
        big = "z" * 3000
        msgs = [_make_tool_msg("read_file", big)]
        report = compress_messages_for_llm(msgs, tier_override="8gb", route="local")
        assert report.backend in {"stdlib", "bypass"}


# ---------------------------------------------------------------------------
# Compression report fields
# ---------------------------------------------------------------------------


def test_compression_report_has_all_fields():
    """T19: CompressionReport exposes all expected fields."""
    from celina_pkg.context_compressor import compress_messages_for_llm

    big = "w" * 5000
    msgs = [_make_tool_msg("cat", big)]
    report = compress_messages_for_llm(msgs, tier_override="8gb", route="local")

    assert report.backend in {"headroom", "headroom+ml", "stdlib", "bypass"}
    assert report.tier in {"8gb", "16gb", "32gb"}
    assert isinstance(report.tier_reason, str)
    assert report.chars_before > 0
    assert report.tokens_before > 0
    assert report.tokens_after > 0
    assert 0.0 <= report.saved_pct <= 100.0


def test_disabled_compression_returns_bypass():
    """T20: enabled=False returns bypass backend."""
    from celina_pkg.context_compressor import compress_messages_for_llm

    big = "v" * 5000
    msgs = [_make_tool_msg("read_file", big)]
    report = compress_messages_for_llm(msgs, tier_override="8gb", route="local", enabled=False)
    assert report.backend == "bypass"
    assert report.chars_before == report.chars_after


def test_ccr_stats():
    """T21: CCR store stats returns sensible structure."""
    from celina_pkg.context_compressor import get_ccr_store, clear_compression_state

    clear_compression_state()
    store = get_ccr_store()
    store.put(tool_name="test_tool", original_text="hello world", compressed_chars=5)
    stats = store.stats()
    assert "items" in stats
    assert "used_kb" in stats
    assert "max_mb" in stats
    assert "ids" in stats
    assert stats["items"] >= 1


def test_ccr_reconfigure_limits():
    """T22: CCR reconfigure_limits updates caps."""
    from celina_pkg.context_compressor import get_ccr_store

    store = get_ccr_store()
    store.reconfigure_limits(max_bytes=1024, max_items=2)
    stats = store.stats()
    assert stats["max_mb"] < 1  # 1024 bytes = 0.001 MB
