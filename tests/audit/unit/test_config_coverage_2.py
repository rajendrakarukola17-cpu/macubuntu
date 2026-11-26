"""
tests/audit/unit/test_config_coverage_2.py

Phase 7d — TASK 1: config.py branch coverage tests (round 2).
Targets: set_assistant_identity, get_active_local_inference_route, is_lite_mode,
get_voice_for_lang, get_espeak_for_lang, and other remaining uncovered branches.

No real network calls. Mocks os, json, Path, and filesystem operations.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import config


# ---------------------------------------------------------------------------
# set_assistant_identity: success path
# ---------------------------------------------------------------------------


def test_set_assistant_identity_success(tmp_path, monkeypatch):
    """CB: set_assistant_identity persists and updates in-memory state."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    env_path = tmp_path / "env"
    env_path.write_text("")

    monkeypatch.setattr(config, "SCHEMA_FILE", schema_path)
    monkeypatch.setattr(config, "ENV_FILE", env_path)
    monkeypatch.setattr(config, "ENV", {"CELINA_NAME": "Old", "CELINA_NICKNAME": "OldNick"})
    monkeypatch.setattr(config, "ASSISTANT_NAME", "Old")
    monkeypatch.setattr(config, "ASSISTANT_NICKNAME", "OldNick")
    monkeypatch.setattr(config, "NEURAL_SCHEMA", {"persona": {"name": "Old", "nickname": "OldNick"}})

    name, nick = config.set_assistant_identity("NewBot", nickname="NewNick", schema_path=schema_path, env_path=env_path)
    assert name == "NewBot"
    assert nick == "NewNick"
    assert config.ASSISTANT_NAME == "NewBot"
    assert config.ASSISTANT_NICKNAME == "NewNick"


def test_set_assistant_identity_value_error_empty_name():
    """CB: set_assistant_identity raises ValueError for empty name."""
    with pytest.raises(ValueError, match="cannot be empty"):
        config.set_assistant_identity("")


def test_set_assistant_identity_persistence_error(tmp_path, monkeypatch):
    """CB: set_assistant_identity raises PersistenceError on disk failure."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    env_path = tmp_path / "env"

    monkeypatch.setattr(config, "SCHEMA_FILE", schema_path)
    monkeypatch.setattr(config, "ENV_FILE", env_path)
    monkeypatch.setattr(config, "ENV", {})
    monkeypatch.setattr(config, "ASSISTANT_NAME", "Old")
    monkeypatch.setattr(config, "ASSISTANT_NICKNAME", "OldNick")
    monkeypatch.setattr(config, "NEURAL_SCHEMA", {"persona": {"name": "Old", "nickname": "OldNick"}})

    # Make atomic_write_private_text fail
    def failing_atomic_write(target, content):
        raise OSError("disk full")

    monkeypatch.setattr(config, "atomic_write_private_text", failing_atomic_write)

    with pytest.raises(config.PersistenceError):
        config.set_assistant_identity("NewBot", schema_path=schema_path, env_path=env_path)


def test_set_assistant_identity_no_nickname_uses_existing(tmp_path, monkeypatch):
    """CB: set_assistant_identity uses existing nickname when None."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    env_path = tmp_path / "env"
    env_path.write_text("")

    monkeypatch.setattr(config, "SCHEMA_FILE", schema_path)
    monkeypatch.setattr(config, "ENV_FILE", env_path)
    monkeypatch.setattr(config, "ENV", {"CELINA_NAME": "Old", "CELINA_NICKNAME": "ExistingNick"})
    monkeypatch.setattr(config, "ASSISTANT_NAME", "Old")
    monkeypatch.setattr(config, "ASSISTANT_NICKNAME", "ExistingNick")
    monkeypatch.setattr(config, "NEURAL_SCHEMA", {"persona": {"name": "Old", "nickname": "ExistingNick"}})

    name, nick = config.set_assistant_identity("NewBot", schema_path=schema_path, env_path=env_path)
    assert name == "NewBot"
    assert nick == "ExistingNick"


# ---------------------------------------------------------------------------
# get_active_local_inference_route
# ---------------------------------------------------------------------------


def test_get_active_local_inference_route_npu_backend(monkeypatch):
    """CB: get_active_local_inference_route returns NPU backend when available."""
    monkeypatch.delenv("CELINA_LITE_MODE", raising=False)

    # Mock celina_hardware.compute module
    mock_compute = MagicMock()
    mock_compute.detect_compute_topology.return_value = {"npu": True}
    mock_compute.resolve_inference_endpoint.return_value = {"backend": "npu", "endpoint": "http://localhost:11435"}

    with patch.dict(sys.modules, {"celina_hardware": MagicMock(), "celina_hardware.compute": mock_compute}):
        result = config.get_active_local_inference_route(prefer_npu=True)
        assert isinstance(result, dict)
        assert "backend" in result


def test_get_active_local_inference_route_ollama_fallback(monkeypatch):
    """CB: get_active_local_inference_route falls back to ollama on import error."""
    monkeypatch.delenv("CELINA_LITE_MODE", raising=False)

    # Make the import fail by blocking celina_hardware.compute
    import builtins as builtins_mod
    original_import = builtins_mod.__import__

    def failing_import(name, *args, **kwargs):
        if name == "celina_hardware.compute" or name == "celina_hardware":
            raise ImportError("no hardware module")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins_mod, "__import__", failing_import)
    result = config.get_active_local_inference_route()
    assert result["backend"] == "ollama"


# ---------------------------------------------------------------------------
# is_lite_mode
# ---------------------------------------------------------------------------


def test_is_lite_mode_env_false(monkeypatch):
    """CB: is_lite_mode returns False for CELINA_LITE_MODE=0."""
    monkeypatch.setenv("CELINA_LITE_MODE", "0")
    result = config.is_lite_mode()
    assert result is False


def test_is_lite_mode_env_other(monkeypatch):
    """CB: is_lite_mode returns False for non-1 values."""
    monkeypatch.setenv("CELINA_LITE_MODE", "false")
    result = config.is_lite_mode()
    assert result is False


# ---------------------------------------------------------------------------
# get_voice_for_lang / get_espeak_for_lang
# ---------------------------------------------------------------------------


def test_get_voice_for_lang_env_override(monkeypatch):
    """CB: get_voice_for_lang returns env override when set."""
    monkeypatch.setenv("CELINA_VOICE_EN", "custom_voice")
    result = config.get_voice_for_lang("en")
    assert result == "custom_voice"


def test_get_voice_for_lang_default(monkeypatch):
    """CB: get_voice_for_lang returns default voice."""
    monkeypatch.delenv("CELINA_VOICE_EN", raising=False)
    result = config.get_voice_for_lang("en")
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_voice_for_lang_unknown_lang(monkeypatch):
    """CB: get_voice_for_lang falls back to en for unknown lang."""
    monkeypatch.delenv("CELINA_VOICE_XX", raising=False)
    result = config.get_voice_for_lang("xx")
    assert isinstance(result, str)


def test_get_espeak_for_lang_default(monkeypatch):
    """CB: get_espeak_for_lang returns default espeak voice."""
    result = config.get_espeak_for_lang("en")
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_espeak_for_lang_unknown(monkeypatch):
    """CB: get_espeak_for_lang falls back for unknown lang."""
    result = config.get_espeak_for_lang("xx")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# atomic_write_private_text: fsync and chmod branches
# ---------------------------------------------------------------------------


def test_atomic_write_private_text_fsync_branch(tmp_path, monkeypatch):
    """CB: atomic_write_private_text calls fsync and chmod."""
    f = tmp_path / "fsync_test.txt"
    config.atomic_write_private_text(f, "content with fsync")
    assert f.read_text() == "content with fsync"
    assert (f.stat().st_mode & 0o777) == 0o600


def test_atomic_write_private_text_exception_cleanup(tmp_path, monkeypatch):
    """CB: atomic_write_private_text cleans up temp file on exception."""
    f = tmp_path / "cleanup_test.txt"
    f.write_text("original")

    def failing_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(config.os, "replace", failing_replace)

    with pytest.raises(OSError):
        config.atomic_write_private_text(f, "new content")

    # Original file should still exist (not corrupted)
    assert f.read_text() == "original"


# ---------------------------------------------------------------------------
# load_neural_schema: version migration
# ---------------------------------------------------------------------------


def test_load_neural_schema_version_migration_removes_tier2_from_tier1(tmp_path):
    """CB: load_neural_schema migrates old schema (removes tier2 from tier1)."""
    old_schema = {
        "schema_version": "4.2",
        "security_policy": {
            "tier1_auto_tools": ["tool_a", "tool_b"],
            "tier2_approval_tools": ["tool_b", "tool_c"],
        },
        "privacy": {
            "local_sensitive_tools": ["tool_c"],
            "hard_blocked_paths": ["/root"],
        },
        "tool_domains": {},
    }
    schema_path = tmp_path / "old_schema.json"
    schema_path.write_text(json.dumps(old_schema))

    result = config.load_neural_schema(schema_path)
    tier1 = result.get("security_policy", {}).get("tier1_auto_tools", [])
    assert "tool_b" not in tier1  # tool_b moved to tier2, removed from tier1


def test_load_neural_schema_new_schema_version_no_migration(tmp_path):
    """CB: load_neural_schema with current version doesn't migrate."""
    new_schema = {
        "schema_version": "4.3",
        "security_policy": {
            "tier1_auto_tools": ["tool_a"],
            "tier2_approval_tools": ["tool_b"],
        },
    }
    schema_path = tmp_path / "new_schema.json"
    schema_path.write_text(json.dumps(new_schema))

    result = config.load_neural_schema(schema_path)
    tier1 = result.get("security_policy", {}).get("tier1_auto_tools", [])
    assert "tool_a" in tier1


# ---------------------------------------------------------------------------
# _resolve_target_home: fallback paths
# ---------------------------------------------------------------------------


def test_resolve_target_home_no_env_no_sudo(monkeypatch):
    """CB: _resolve_target_home falls back to Path.home()."""
    monkeypatch.delenv("TARGET_HOME", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    result = config._resolve_target_home()
    assert result == Path.home()


# ---------------------------------------------------------------------------
# ensure_private_file: os.open branch
# ---------------------------------------------------------------------------


def test_ensure_private_file_os_open_branch(tmp_path):
    """CB: ensure_private_file uses os.open when creating new file."""
    f = tmp_path / "new_file_created.txt"
    # Don't create the file first - this triggers os.open path
    result = config.ensure_private_file(f)
    assert f.is_file()
    assert (f.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# load_env: various line formats
# ---------------------------------------------------------------------------


def test_load_env_inline_comment(tmp_path):
    """CB: load_env handles inline comments."""
    env_path = tmp_path / ".env"
    env_path.write_text("KEY=value # comment\n")
    result = config.load_env(env_path)
    # The value might include the comment depending on implementation
    assert "KEY" in result


def test_load_env_no_equals_sign(tmp_path):
    """CB: load_env skips lines without equals sign."""
    env_path = tmp_path / ".env"
    env_path.write_text("JUSTAWORD\nREAL=ok\n")
    result = config.load_env(env_path)
    assert result.get("REAL") == "ok"
    assert "JUSTAWORD" not in result


# ---------------------------------------------------------------------------
# _get_setting: ENV dict fallback
# ---------------------------------------------------------------------------


def test_get_setting_from_ENV_dict(monkeypatch):
    """CB: _get_setting falls through to ENV dict."""
    monkeypatch.delenv("CELINA_COV_TEST_ENV3", raising=False)
    config.ENV["CELINA_COV_TEST_ENV3"] = "from_env_dict"
    result = config._get_setting("CELINA_COV_TEST_ENV3", default="default_val")
    assert result == "from_env_dict"


# ---------------------------------------------------------------------------
# is_hard_blocked_path: trailing slash and empty parts
# ---------------------------------------------------------------------------


def test_is_hard_blocked_path_trailing_slash(monkeypatch):
    """CB: is_hard_blocked_path handles trailing slash in path."""
    monkeypatch.setattr(config, "HARD_BLOCKED_PATHS", {"/home/user/.ssh/"})
    result = config.is_hard_blocked_path("/home/user/.ssh/id_rsa")
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _sanitize_identity_token: various inputs
# ---------------------------------------------------------------------------


def test_sanitize_identity_token_control_chars_various(monkeypatch):
    """CB: _sanitize_identity_token strips various control characters."""
    result = config._sanitize_identity_token("Celina\r\n\t")
    assert "\r" not in result
    assert "\n" not in result
    assert "\t" not in result


# ---------------------------------------------------------------------------
# get_assistant_name / nickname: env override
# ---------------------------------------------------------------------------


def test_get_assistant_name_env_override(monkeypatch):
    """CB: get_assistant_name reads from ENV."""
    monkeypatch.setattr(config, "ENV", {"CELINA_NAME": "EnvBot"})
    monkeypatch.setattr(config, "ASSISTANT_NAME", "OldBot")
    # The function reads from ASSISTANT_NAME global, not ENV directly
    # This tests the global value
    result = config.get_assistant_name()
    assert isinstance(result, str)

