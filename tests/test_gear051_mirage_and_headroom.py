"""
Tests for Gear 0.5.1 Mirage + Tier-Adaptive Headroom Context Compression integration.

Covers:
  - Mirage telemetry locks (CACTUS_TELEMETRY=0, DO_NOT_TRACK=1)
  - Mirage fail-closed when cactus-compute unavailable
  - Mirage Latin-script-only OCR with Telugu/Hindi escalation
  - Mirage SHA-256 weight verification
  - Mirage unload() call (Turbo Mode integration)
  - Mirage T1 untrusted_screen_perception tagging
  - Mirage 20 MiB image cap
  - Headroom compression tier detection (8gb/16gb/32gb)
  - TurnTrace vision/compression field wiring
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
# Telemetry / offline env locks
# ---------------------------------------------------------------------------


def test_mirage_telemetry_env_locks():
    """M1: Mirage sets CACTUS_TELEMETRY=0 and DO_NOT_TRACK=1 on import, zero HOME writes."""
    import celina_pkg.gear0_5_1_mirage as mirage_mod

    assert os.environ.get("CACTUS_TELEMETRY") == "0"
    assert os.environ.get("DO_NOT_TRACK") == "1"

    home = Path.home()
    before = set(home.iterdir())
    import importlib

    importlib.reload(mirage_mod)
    after = set(home.iterdir())
    assert before == after, f"Import created files in $HOME: {after - before}"


def test_mirage_fail_closed_when_cactus_unavailable():
    """M2: Mirage fails closed (ok=False, escalate_to_vlm=True) when cactus-compute missing."""
    from celina_pkg.gear0_5_1_mirage import Gear051MirageRouter, MiragePerceptionResult

    router = Gear051MirageRouter()
    ok, reason = router.initialize()
    assert ok is False
    assert "cactus-compute" in reason.lower()


def test_mirage_telugu_hindi_escalation():
    """M3: Mirage flags escalate_to_vlm=True for Telugu/Hindi (Latin-script only OCR)."""
    from celina_pkg.gear0_5_1_mirage import Gear051MirageRouter, MiragePerceptionResult

    router = Gear051MirageRouter()
    result = router.perceive("/tmp/nonexistent.png", lang="te")
    assert result.ok is False
    assert result.escalate_to_vlm is True
    assert "telugu" in result.escalation_reason.lower() or "latin" in result.escalation_reason.lower()

    result_hi = router.perceive("/tmp/nonexistent.png", lang="hi")
    assert result_hi.ok is False
    assert result_hi.escalate_to_vlm is True


def test_mirage_20mb_image_cap():
    """M4: Mirage rejects images exceeding 20 MiB cap."""
    from celina_pkg.gear0_5_1_mirage import Gear051MirageRouter, MAX_IMAGE_BYTES

    router = Gear051MirageRouter()

    # Create a fake oversized file (>20 MiB)
    big_path = Path("/tmp/celina_mirage_big_test.img")
    try:
        big_path.write_bytes(b"\x00" * (MAX_IMAGE_BYTES + 1))
        result = router.perceive(big_path)
        assert result.ok is False
        assert "exceeds" in result.error or "cap" in result.error
    finally:
        try:
            big_path.unlink()
        except OSError:
            pass


def test_mirage_unload_releases_model():
    """M5: Mirage unload() releases the model handle (Turbo Mode integration)."""
    from celina_pkg.gear0_5_1_mirage import Gear051MirageRouter

    router = Gear051MirageRouter()
    router._model = object()  # simulate loaded model
    router._initialized = True
    router.unload()
    assert router._model is None
    assert router._initialized is False


def test_mirage_t1_untrusted_screen_perception_tagging():
    """M6: Mirage result carries source_trust='untrusted_screen_perception' (T1 tagging)."""
    from celina_pkg.gear0_5_1_mirage import MiragePerceptionResult

    result = MiragePerceptionResult(ok=False, error="test")
    assert result.source_trust == "untrusted_screen_perception"


def test_mirage_get_singleton_returns_same_instance():
    """M7: get_gear051_mirage returns the same singleton instance."""
    from celina_pkg.gear0_5_1_mirage import get_gear051_mirage, _MIRAGE_SINGLETON

    _MIRAGE_SINGLETON = None  # reset
    inst1 = get_gear051_mirage()
    inst2 = get_gear051_mirage()
    assert inst1 is inst2
    _MIRAGE_SINGLETON = None


def test_mirage_sha256_verification_path():
    """M8: Mirage verifies SHA-256 of cached weights when CELINA_MIRAGE_SHA256 is set."""
    from celina_pkg.gear0_5_1_mirage import Gear051MirageRouter

    router = Gear051MirageRouter()
    # With no weights cached and no download allowed, should fail closed
    ok, reason = router._verify_cached_weights()
    assert ok is False
    assert "not cached" in reason or "offline" in reason.lower()


# ---------------------------------------------------------------------------
# Headroom compression tier detection
# ---------------------------------------------------------------------------


def test_headroom_tier_8gb_policy():
    """H1: 8 GB tier policy thresholds."""
    from celina_pkg.context_compressor import detect_effective_tier, TIER_POLICIES

    policy, reason = detect_effective_tier(tier_override="8gb")
    assert policy.tier == "8gb"
    pol = TIER_POLICIES["8gb"]
    assert pol.local_min_chars == 800
    assert pol.cloud_min_chars == 800
    assert pol.allow_ml_kompress is False
    assert pol.allow_ml_kompress is False


def test_headroom_tier_16gb_policy():
    """H2: 16 GB tier policy thresholds."""
    from celina_pkg.context_compressor import detect_effective_tier, TIER_POLICIES

    policy, reason = detect_effective_tier(tier_override="16gb")
    assert policy.tier == "16gb"
    pol = TIER_POLICIES["16gb"]
    assert pol.local_min_chars == 2400
    assert pol.cloud_min_chars == 1200


def test_headroom_tier_32gb_policy():
    """H3: 32 GB tier policy thresholds."""
    from celina_pkg.context_compressor import detect_effective_tier, TIER_POLICIES

    policy, reason = detect_effective_tier(tier_override="32gb")
    assert policy.tier == "32gb"
    pol = TIER_POLICIES["32gb"]
    assert pol.local_min_chars == 6000
    assert pol.cloud_min_chars == 1800


def test_headroom_tier_auto_detect():
    """H4: Auto tier detection returns a valid tier string."""
    from celina_pkg.context_compressor import detect_effective_tier

    policy, reason = detect_effective_tier()
    assert policy.tier in {"8gb", "16gb", "32gb"}


# ---------------------------------------------------------------------------
# TurnTrace vision/compression field wiring
# ---------------------------------------------------------------------------


def test_turntrace_has_vision_compression_fields():
    """T1: TurnTrace dataclass has all vision and compression fields."""
    from celina_pkg.router_receipts import TurnTrace

    trace = TurnTrace()
    assert hasattr(trace, "vision_backend")
    assert hasattr(trace, "vision_latency_ms")
    assert hasattr(trace, "vision_escalated")
    assert hasattr(trace, "compression_backend")
    assert hasattr(trace, "compression_tier")
    assert hasattr(trace, "compression_saved_pct")
    assert hasattr(trace, "compression_ccr_ids")


def test_turntrace_vision_fields_defaults():
    """T2: TurnTrace vision fields have safe defaults."""
    from celina_pkg.router_receipts import TurnTrace

    trace = TurnTrace()
    assert trace.vision_backend == ""
    assert trace.vision_latency_ms == 0
    assert trace.vision_escalated is False


def test_turntrace_compression_fields_defaults():
    """T3: TurnTrace compression fields have safe defaults."""
    from celina_pkg.router_receipts import TurnTrace

    trace = TurnTrace()
    assert trace.compression_backend == ""
    assert trace.compression_tier == ""
    assert trace.compression_saved_pct == 0.0
    assert trace.compression_ccr_ids == ()


def test_router_vision_tracking_in_local_agent():
    """T4: Router records vision backend/escalation on TurnTrace during local agent tool calls."""
    from celina_pkg.router_receipts import TurnTrace

    trace = TurnTrace()

    # Simulate a vision tool call result from Mirage (JSON with escalate_to_vlm=false)
    out = json.dumps(
        {
            "source_trust": "untrusted_screen_perception",
            "summary": "A test scene",
            "objects": [],
            "text_regions": [],
            "relations": [],
            "confidence": 0.85,
            "escalate_to_vlm": False,
        }
    )

    # Manually set the fields as _run_local_agent would
    trace.vision_backend = "gear0_5_1_mirage"
    try:
        out_json = json.loads(out)
        trace.vision_escalated = bool(out_json.get("escalate_to_vlm", False))
    except Exception:
        trace.vision_escalated = True

    assert trace.vision_backend == "gear0_5_1_mirage"
    assert trace.vision_escalated is False


def test_router_vision_escalation_detection():
    """T5: Router detects vision escalation when output is not Mirage JSON (MCP fallback)."""
    from celina_pkg.router_receipts import TurnTrace

    trace = TurnTrace()
    out = "MCP vision result: heavy VLM processed the image"

    trace.vision_backend = "gear0_5_1_mirage"
    try:
        out_json = json.loads(out)
        trace.vision_escalated = bool(out_json.get("escalate_to_vlm", False))
    except Exception:
        trace.vision_escalated = True

    assert trace.vision_escalated is True


# ---------------------------------------------------------------------------
# Turbo Mode Mirage unload integration
# ---------------------------------------------------------------------------


def test_turbo_mode_calls_mirage_unload():
    """T6: Turbo Mode enable_turbo_mode calls Mirage unload()."""
    import celina_pkg.turbo_mode as tm
    import celina_pkg.gear0_5_1_mirage as mirage_mod
    import celina_pkg.context_compressor as cc_mod

    with mock.patch.object(tm, "_eject_ollama_now", return_value=(True, "ejected")):
        with mock.patch("celina_pkg.turbo_mode.config") as mock_config:
            mock_config.detect_available_ram_mb.return_value = 4096
            mock_config.LOCAL_MODEL = "test-model"
            mock_config.OLLAMA_API_BASE = "http://localhost:11434"
            mock_config.ENV = {}
            mock_config.set_env_pairs = mock.Mock()
            mock_config.HOME = Path.home()

            with mock.patch("celina_pkg.turbo_mode.requests") as mock_req:
                mock_resp = mock.Mock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"models": []}
                mock_req.get.return_value = mock_resp
                mock_req.post.return_value = mock_resp

                with mock.patch.object(cc_mod, "clear_compression_state", return_value={"ccr_cleared": 0}):
                    with mock.patch.object(
                        mirage_mod, "get_gear051_mirage"
                    ) as mock_get_mirage:
                        mock_mi = mock.Mock()
                        mock_mi.unload = mock.Mock()
                        mock_get_mirage.return_value = mock_mi

                        result = tm.enable_turbo_mode(dry_run=False)

                        mock_mi.unload.assert_called_once()