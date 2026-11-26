"""
tests/audit/unit/test_tools_coverage_27.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 27).
Targets: _kick_browser_media_play_async / _worker, _cached_read_document.

Covers: _kick_browser_media_play_async with PYTEST_CURRENT_TEST removed → _worker
  → _mpris_dbus_control returns (True, "Playing...") → early return;
  → _mpris_dbus_control returns (True, "Paused") → early return on `if ok`;
  → loop exhausts → automation._try_atspi_direct_action success;
  → loop exhausts → automation._try_atspi_direct_action raises → log.debug.
_cached_read_document: fp != "missing" + cache hit → return hit[0];
  fp != "missing" + cache miss → read_document + cache set;
  fp == "missing" → skip cache get/set.

No real threads, D-Bus, or filesystem calls. Mocks os.environ, time.sleep,
_mpris_dbus_control, automation._try_atspi_direct_action, threading.Thread,
cache, documents.read_document.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


# ---------------------------------------------------------------------------
# _kick_browser_media_play_async / _worker
# ---------------------------------------------------------------------------

def test_kick_worker_mpris_playing(monkeypatch):
    """CB: _worker → _mpris_dbus_control returns (True, "Playing...") → early return."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CELINA_DRY_RUN", raising=False)

    calls = []
    monkeypatch.setattr(tools.time, "sleep", lambda d: calls.append(d))
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (True, "Playing spotify"))

    captured_targets = []
    def fake_thread(target, name=None, daemon=None):
        captured_targets.append(target)
        return MagicMock()
    monkeypatch.setattr(tools.threading, "Thread", fake_thread)

    tools._kick_browser_media_play_async()
    assert len(captured_targets) == 1
    # Invoke _worker directly
    captured_targets[0]()
    # _worker: _mpris_dbus_control returned Playing → early return after first delay
    assert calls == [2.2]


def test_kick_worker_mpris_ok_no_playing(monkeypatch):
    """CB: _worker → _mpris_dbus_control returns (True, "Paused") → `if ok` return."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CELINA_DRY_RUN", raising=False)

    calls = []
    monkeypatch.setattr(tools.time, "sleep", lambda d: calls.append(d))
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (True, "Paused"))

    captured_targets = []
    def fake_thread(target, name=None, daemon=None):
        captured_targets.append(target)
        return MagicMock()
    monkeypatch.setattr(tools.threading, "Thread", fake_thread)

    tools._kick_browser_media_play_async()
    captured_targets[0]()
    # _worker: first iteration ok=True but "Playing" not in msg → `if ok` return after 2.2s
    assert calls == [2.2]


def test_kick_worker_mpris_not_ok_then_atspi(monkeypatch):
    """CB: _worker → _mpris_dbus_control returns (False, ...) → loop continues → AT-SPI fallback."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CELINA_DRY_RUN", raising=False)

    calls = []
    monkeypatch.setattr(tools.time, "sleep", lambda d: calls.append(d))
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (False, "dbus_unavailable"))

    atspi_called = []
    monkeypatch.setattr(tools.automation, "_try_atspi_direct_action",
                        lambda action: atspi_called.append(action) or True)

    captured_targets = []
    def fake_thread(target, name=None, daemon=None):
        captured_targets.append(target)
        return MagicMock()
    monkeypatch.setattr(tools.threading, "Thread", fake_thread)

    tools._kick_browser_media_play_async()
    captured_targets[0]()
    # _worker: all 3 delays, _mpris always not ok → AT-SPI fallback called
    assert len(calls) == 3
    assert atspi_called == ["Play"]


def test_kick_worker_atspi_exception(monkeypatch):
    """CB: _worker → AT-SPI raises → log.debug → no crash."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CELINA_DRY_RUN", raising=False)

    calls = []
    monkeypatch.setattr(tools.time, "sleep", lambda d: calls.append(d))
    monkeypatch.setattr(tools, "_mpris_dbus_control", lambda act: (False, "dbus_unavailable"))

    monkeypatch.setattr(tools.automation, "_try_atspi_direct_action",
                        lambda action: (_ for _ in ()).throw(OSError("atspi down")))

    log_calls = []
    monkeypatch.setattr(tools.log, "debug", lambda msg, exc: log_calls.append((msg, exc)))

    captured_targets = []
    def fake_thread(target, name=None, daemon=None):
        captured_targets.append(target)
        return MagicMock()
    monkeypatch.setattr(tools.threading, "Thread", fake_thread)

    tools._kick_browser_media_play_async()
    captured_targets[0]()
    assert len(calls) == 3
    assert len(log_calls) == 1
    assert "Autoplay AT-SPI2 fallback skipped" in log_calls[0][0]


# ---------------------------------------------------------------------------
# _cached_read_document
# ---------------------------------------------------------------------------

def test_cached_read_document_cache_hit(monkeypatch):
    """CB: _cached_read_document fp != missing + cache hit → return hit[0]."""
    monkeypatch.setattr(tools.cache, "file_fingerprint", lambda p: "abc123")
    fake_sc = MagicMock()
    fake_sc.get = lambda section, key: ("cached content",)
    monkeypatch.setattr(tools.cache, "get_cache", lambda: fake_sc)

    result = tools._cached_read_document("/tmp/test.txt")
    assert result == "cached content"


def test_cached_read_document_cache_miss(monkeypatch, tmp_path):
    """CB: _cached_read_document fp != missing + cache miss → read_document + cache set."""
    monkeypatch.setattr(tools.cache, "file_fingerprint", lambda p: "abc123")
    fake_sc = MagicMock()
    fake_sc.get = MagicMock(return_value=None)
    monkeypatch.setattr(tools.cache, "get_cache", lambda: fake_sc)

    monkeypatch.setattr(tools.documents, "read_document", lambda path, max_chars=4000: "fresh content")

    set_calls = []
    fake_sc.set = lambda section, key, val: set_calls.append((section, key, val))

    result = tools._cached_read_document("/tmp/test.txt")
    assert result == "fresh content"
    assert len(set_calls) == 1
    assert set_calls[0][0] == "document"


def test_cached_read_document_fp_missing(monkeypatch):
    """CB: _cached_read_document fp == missing → skip cache get and set."""
    monkeypatch.setattr(tools.cache, "file_fingerprint", lambda p: "missing")
    get_count = [0]
    def counting_get(section, key):
        get_count[0] += 1
        return ("should not be called",)
    fake_sc = MagicMock()
    fake_sc.get = counting_get
    monkeypatch.setattr(tools.cache, "get_cache", lambda: fake_sc)

    monkeypatch.setattr(tools.documents, "read_document", lambda path, max_chars=4000: "no cache")

    set_calls = []
    fake_sc.set = lambda section, key, val: set_calls.append((section, key, val))

    result = tools._cached_read_document("/tmp/test.txt")
    assert result == "no cache"
    # cache.get should NOT have been called since fp == "missing"
    assert get_count[0] == 0
    # cache.set should NOT have been called since fp == "missing"
    assert len(set_calls) == 0
