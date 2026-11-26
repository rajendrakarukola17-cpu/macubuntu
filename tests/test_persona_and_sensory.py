"""
Tests for features/celina/celina_pkg/persona.py and sensory_buffer.py —
Dual-Channel Persona Engine + Transient Sensory Buffer.

Covers:
  - persona.build_system_prompt: celina/jarvis/minimal styles, text/voice channels
  - Voice channel constraints: no markdown, emojis, bullets, URLs; 1-3 sentences
  - Action honesty + zero silent failure rules always present
  - Unknown style falls back to celina
  - Sensory buffer: record, recent_context, clear, TTL expiry
  - Password field / password manager refusal
  - Zero import-time $HOME writes
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# persona.py
# ---------------------------------------------------------------------------


def test_persona_celina_style_text_channel():
    """P1: celina style, text channel — warm, includes action honesty."""
    from celina_pkg.persona import build_system_prompt

    prompt = build_system_prompt("Celina", nickname="Celly", style="celina", channel="text")
    assert "Celina" in prompt
    assert "Celly" in prompt
    assert "ACTION HONESTY" in prompt
    assert "ZERO SILENT FAILURE" in prompt
    assert "VOICE CHANNEL" not in prompt


def test_persona_jarvis_style():
    """P2: jarvis style — formal, honorific, precise."""
    from celina_pkg.persona import build_system_prompt

    prompt = build_system_prompt("Jarvis", style="jarvis", honorific="Sir")
    assert "JARVIS" in prompt
    assert "Sir" in prompt
    assert "ACTION HONESTY" in prompt


def test_persona_minimal_style():
    """P3: minimal style — concise, direct."""
    from celina_pkg.persona import build_system_prompt

    prompt = build_system_prompt("Assistant", style="minimal")
    assert "concise" in prompt.lower() or "few words" in prompt.lower()
    assert "ACTION HONESTY" in prompt


def test_persona_unknown_style_falls_back():
    """P4: Unknown style falls back to celina."""
    from celina_pkg.persona import build_system_prompt

    prompt = build_system_prompt("Test", style="nonexistent")
    assert "Celina" in prompt or "test" in prompt.lower()
    assert "ACTION HONESTY" in prompt


def test_persona_voice_channel_constraints():
    """P5: Voice channel forbids markdown, emojis, bullets, URLs."""
    from celina_pkg.persona import build_system_prompt

    prompt = build_system_prompt("Celina", channel="voice")
    assert "VOICE CHANNEL CONSTRAINTS" in prompt
    assert "Zero markdown" in prompt
    assert "Zero emojis" in prompt
    assert "Zero bullet points" in prompt
    assert "Zero raw URLs" in prompt
    assert "1-3 spoken sentences" in prompt


def test_persona_language_rule():
    """P6: language_rule is included when provided."""
    from celina_pkg.persona import build_system_prompt

    prompt = build_system_prompt("Celina", language_rule="Reply in Telugu.")
    assert "Reply in Telugu" in prompt


# ---------------------------------------------------------------------------
# sensory_buffer.py
# ---------------------------------------------------------------------------


def test_sensory_buffer_record_and_recent():
    """S1: Record frames and retrieve recent context."""
    from celina_pkg.sensory_buffer import TransientSensoryBuffer

    buf = TransientSensoryBuffer(max_frames=10, ttl_sec=300.0)
    assert buf.record(kind="window", source_app="firefox", summary="User opened a browser tab")
    assert buf.record(kind="ocr", source_app="evince", summary="PDF page 3: total revenue $5000")

    ctx = buf.recent_context(limit=5)
    assert "Transient Sensory Memory" in ctx
    assert "firefox" in ctx
    assert "evince" in ctx
    assert "$5000" in ctx


def test_sensory_buffer_password_field_refused():
    """S2: Frames with password_field_active=True are refused."""
    from celina_pkg.sensory_buffer import TransientSensoryBuffer

    buf = TransientSensoryBuffer()
    assert buf.record(kind="window", source_app="firefox", summary="normal page") is True
    assert (
        buf.record(
            kind="window", source_app="keepass", summary="entering master password",
            password_field_active=True,
        )
        is False
    )


def test_sensory_buffer_password_manager_app_refused():
    """S3: Password manager apps are refused even without password_field_active."""
    from celina_pkg.sensory_buffer import TransientSensoryBuffer

    buf = TransientSensoryBuffer()
    assert buf.record(kind="window", source_app="bitwarden", summary="filling credentials") is False
    assert buf.record(kind="window", source_app="1password", summary="unlocking vault") is False


def test_sensory_buffer_ttl_expiry():
    """S4: Stale frames expire after TTL."""
    from celina_pkg.sensory_buffer import TransientSensoryBuffer

    buf = TransientSensoryBuffer(max_frames=10, ttl_sec=300.0)
    buf.record(kind="window", source_app="firefox", summary="recent page")
    assert buf.recent_context(limit=5) != ""

    # Manually shorten TTL to test expiry (minimum is 30s in constructor)
    buf._ttl_sec = 0.01
    import time as _time
    _time.sleep(0.05)
    ctx = buf.recent_context(limit=5)
    assert ctx == "", "Stale frames should have expired"


def test_sensory_buffer_clear():
    """S5: clear() empties the buffer."""
    from celina_pkg.sensory_buffer import TransientSensoryBuffer

    buf = TransientSensoryBuffer()
    buf.record(kind="window", source_app="firefox", summary="test page")
    assert buf.recent_context(limit=5) != ""
    buf.clear()
    assert buf.recent_context(limit=5) == ""


def test_sensory_buffer_max_frames():
    """S6: Buffer respects max_frames limit (ring buffer)."""
    from celina_pkg.sensory_buffer import TransientSensoryBuffer

    buf = TransientSensoryBuffer(max_frames=1, ttl_sec=300.0)
    for i in range(7):
        buf.record(kind="window", source_app=f"app{i}", summary=f"summary {i}")

    ctx = buf.recent_context(limit=10)
    # maxlen is max(5, 1) = 5, so only last 5 frames: app2..app6
    assert "app2" in ctx
    assert "app6" in ctx
    assert "app0" not in ctx
    assert "app1" not in ctx


def test_sensory_buffer_empty_context():
    """S7: Empty buffer returns empty string."""
    from celina_pkg.sensory_buffer import TransientSensoryBuffer

    buf = TransientSensoryBuffer()
    assert buf.recent_context(limit=5) == ""


def test_sensory_buffer_source_trust_tag():
    """S8: All frames carry source_trust='untrusted_sensory'."""
    from celina_pkg.sensory_buffer import SensoryFrame

    frame = SensoryFrame(ts=time.time(), kind="window", source_app="firefox", summary="test")
    assert frame.source_trust == "untrusted_sensory"


def test_get_sensory_buffer_singleton():
    """S9: get_sensory_buffer returns the same singleton instance."""
    from celina_pkg.sensory_buffer import get_sensory_buffer

    b1 = get_sensory_buffer()
    b2 = get_sensory_buffer()
    assert b1 is b2


# ---------------------------------------------------------------------------
# Zero import-time side effects
# ---------------------------------------------------------------------------


def test_persona_zero_home_writes():
    """P7: Importing persona writes nothing to $HOME."""
    home = Path.home()
    before = set(home.iterdir())
    import importlib

    import celina_pkg.persona as p

    importlib.reload(p)
    after = set(home.iterdir())
    assert before == after, f"Import created files in $HOME: {after - before}"


def test_sensory_buffer_zero_home_writes():
    """S10: Importing sensory_buffer writes nothing to $HOME."""
    home = Path.home()
    before = set(home.iterdir())
    import importlib

    import celina_pkg.sensory_buffer as sb

    importlib.reload(sb)
    after = set(home.iterdir())
    assert before == after, f"Import created files in $HOME: {after - before}"
