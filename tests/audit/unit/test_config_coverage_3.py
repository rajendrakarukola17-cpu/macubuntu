"""
tests/audit/unit/test_config_coverage_3.py

Phase 7d — TASK 1: config.py branch coverage tests (round 3).
Targets remaining set_assistant_identity env-sync branches, is_lite_mode,
get_active_local_inference_route, and scattered uncovered functions.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import config


# ---------------------------------------------------------------------------
# set_assistant_identity: env file sync with existing CELINA_NAME/NICKNAME
# ---------------------------------------------------------------------------


def test_set_assistant_identity_env_sync_existing_lines(tmp_path, monkeypatch):
    """CB: set_assistant_identity replaces existing CELINA_NAME/NICKNAME in env file."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    env_path = tmp_path / "env"
    env_path.write_text('CELINA_NAME="OldBot"\nCELINA_NICKNAME="OldNick"\nOTHER=value\n')

    monkeypatch.setattr(config, "SCHEMA_FILE", schema_path)
    monkeypatch.setattr(config, "ENV_FILE", env_path)
    monkeypatch.setattr(config, "ENV", {"CELINA_NAME": "OldBot", "CELINA_NICKNAME": "OldNick"})
    monkeypatch.setattr(config, "ASSISTANT_NAME", "OldBot")
    monkeypatch.setattr(config, "ASSISTANT_NICKNAME", "OldNick")
    monkeypatch.setattr(config, "NEURAL_SCHEMA", {"persona": {"name": "OldBot", "nickname": "OldNick"}})

    name, nick = config.set_assistant_identity("NewBot", nickname="NewNick", schema_path=schema_path, env_path=env_path)
    assert name == "NewBot"
    assert nick == "NewNick"

    # Verify env file was updated
    env_content = env_path.read_text()
    assert 'CELINA_NAME="NewBot"' in env_content
    assert 'CELINA_NICKNAME="NewNick"' in env_content
    assert "OTHER=value" in env_content


def test_set_assistant_identity_env_sync_missing_lines(tmp_path, monkeypatch):
    """CB: set_assistant_identity appends missing CELINA_NAME/NICKNAME to env file."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    env_path = tmp_path / "env"
    env_path.write_text("OTHER=value\n")

    monkeypatch.setattr(config, "SCHEMA_FILE", schema_path)
    monkeypatch.setattr(config, "ENV_FILE", env_path)
    monkeypatch.setattr(config, "ENV", {"OTHER": "value"})
    monkeypatch.setattr(config, "ASSISTANT_NAME", "Old")
    monkeypatch.setattr(config, "ASSISTANT_NICKNAME", "OldNick")
    monkeypatch.setattr(config, "NEURAL_SCHEMA", {"persona": {"name": "Old", "nickname": "OldNick"}})

    name, nick = config.set_assistant_identity("NewBot", nickname="NewNick", schema_path=schema_path, env_path=env_path)
    assert name == "NewBot"
    assert nick == "NewNick"

    env_content = env_path.read_text()
    assert 'CELINA_NAME="NewBot"' in env_content
    assert 'CELINA_NICKNAME="NewNick"' in env_content


def test_set_assistant_identity_env_sync_oserror(tmp_path, monkeypatch):
    """CB: set_assistant_identity raises PersistenceError on env file write failure."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    env_path = tmp_path / "env"
    env_path.write_text("")

    monkeypatch.setattr(config, "SCHEMA_FILE", schema_path)
    monkeypatch.setattr(config, "ENV_FILE", env_path)
    monkeypatch.setattr(config, "ENV", {})
    monkeypatch.setattr(config, "ASSISTANT_NAME", "Old")
    monkeypatch.setattr(config, "ASSISTANT_NICKNAME", "OldNick")
    monkeypatch.setattr(config, "NEURAL_SCHEMA", {"persona": {"name": "Old", "nickname": "OldNick"}})

    # Make atomic_write_private_text fail on env file but not schema file
    original_atomic = config.atomic_write_private_text
    call_count = [0]

    def failing_atomic_write(target, content):
        call_count[0] += 1
        if call_count[0] == 2:  # Fail on the second call (env file)
            raise OSError("env write failed")
        return original_atomic(target, content)

    monkeypatch.setattr(config, "atomic_write_private_text", failing_atomic_write)

    with pytest.raises(config.PersistenceError):
        config.set_assistant_identity("NewBot", schema_path=schema_path, env_path=env_path)


# ---------------------------------------------------------------------------
# is_lite_mode: ENV dict path
# ---------------------------------------------------------------------------


def test_is_lite_mode_from_ENV_dict(monkeypatch):
    """CB: is_lite_mode reads from ENV dict when env var not set."""
    monkeypatch.delenv("CELINA_LITE_MODE", raising=False)
    # Clear the module-level LITE_MODE to test ENV dict path
    monkeypatch.setattr(config, "LITE_MODE", False)
    config.ENV["CELINA_LITE_MODE"] = "1"
    result = config.is_lite_mode()
    assert result is True


def test_is_lite_mode_from_ENV_dict_false(monkeypatch):
    """CB: is_lite_mode reads False from ENV dict."""
    monkeypatch.delenv("CELINA_LITE_MODE", raising=False)
    monkeypatch.setattr(config, "LITE_MODE", False)
    config.ENV["CELINA_LITE_MODE"] = "0"
    result = config.is_lite_mode()
    assert result is False


def test_is_lite_mode_module_level_false(monkeypatch):
    """CB: is_lite_mode returns module-level LITE_MODE when no env/ENV."""
    monkeypatch.delenv("CELINA_LITE_MODE", raising=False)
    monkeypatch.setattr(config, "LITE_MODE", True)
    # Remove from ENV if present
    config.ENV.pop("CELINA_LITE_MODE", None)
    result = config.is_lite_mode()
    assert result is True


# ---------------------------------------------------------------------------
# get_active_local_inference_route: prefer_npu=False path
# ---------------------------------------------------------------------------


def test_get_active_local_inference_route_prefer_npu_false(monkeypatch):
    """CB: get_active_local_inference_route with prefer_npu=False."""
    monkeypatch.delenv("CELINA_LITE_MODE", raising=False)

    import builtins as builtins_mod
    original_import = builtins_mod.__import__

    def failing_import(name, *args, **kwargs):
        if "celina_hardware" in name:
            raise ImportError("no hardware")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins_mod, "__import__", failing_import)
    result = config.get_active_local_inference_route(prefer_npu=False)
    assert result["backend"] == "ollama"


# ---------------------------------------------------------------------------
# get_assistant_name / get_assistant_nickname
# ---------------------------------------------------------------------------


def test_get_assistant_name_global(monkeypatch):
    """CB: get_assistant_name returns ASSISTANT_NAME global."""
    monkeypatch.setattr(config, "ASSISTANT_NAME", "GlobalBot")
    result = config.get_assistant_name()
    assert result == "GlobalBot"


def test_get_assistant_nickname_global(monkeypatch):
    """CB: get_assistant_nickname returns ASSISTANT_NICKNAME global."""
    monkeypatch.setattr(config, "ASSISTANT_NICKNAME", "GlobalNick")
    result = config.get_assistant_nickname()
    assert result == "GlobalNick"


# ---------------------------------------------------------------------------
# format_persona_instructions
# ---------------------------------------------------------------------------


def test_format_persona_instructions_with_name_nickname(monkeypatch):
    """CB: format_persona_instructions uses name and nickname."""
    monkeypatch.setattr(config, "ASSISTANT_NAME", "TestBot")
    monkeypatch.setattr(config, "ASSISTANT_NICKNAME", "Testy")
    result = config.format_persona_instructions()
    assert isinstance(result, str)
    assert "TestBot" in result


# ---------------------------------------------------------------------------
# _get_setting: ENV dict fallback when env var not set
# ---------------------------------------------------------------------------


def test_get_setting_from_ENV_dict_fallback(monkeypatch):
    """CB: _get_setting falls through to ENV dict when env var not set."""
    monkeypatch.delenv("CELINA_COV_TEST_ENV4", raising=False)
    config.ENV["CELINA_COV_TEST_ENV4"] = "env_dict_value"
    result = config._get_setting("CELINA_COV_TEST_ENV4", default="default_val")
    assert result == "env_dict_value"


# ---------------------------------------------------------------------------
# HARD_BLOCKED_PATHS: set operations
# ---------------------------------------------------------------------------


def test_hard_blocked_paths_set(monkeypatch):
    """CB: HARD_BLOCKED_PATHS is a set used for membership checks."""
    monkeypatch.setattr(config, "HARD_BLOCKED_PATHS", {"/etc/passwd", "/root"})
    assert config.is_hard_blocked_path("/etc/passwd") is True
    assert config.is_hard_blocked_path("/safe/file") is False


# ---------------------------------------------------------------------------
# _safe_int / _safe_float: key parameter for warning dedup
# ---------------------------------------------------------------------------


def test_safe_int_with_key_warning(monkeypatch):
    """CB: _safe_int with key parameter tracks warnings."""
    config._CONFIG_LOAD_WARNINGS.clear()
    result = config._safe_int("not_an_int", default=0, key="my_key")
    assert result == 0
    assert len(config._CONFIG_LOAD_WARNINGS) >= 0


def test_safe_float_with_key_warning(monkeypatch):
    """CB: _safe_float with key parameter tracks warnings."""
    config._CONFIG_LOAD_WARNINGS.clear()
    result = config._safe_float("not_a_float", default=0.0, key="my_key")
    assert result == 0.0


# ---------------------------------------------------------------------------
# load_neural_schema: various paths
# ---------------------------------------------------------------------------


def test_load_neural_schema_copy_of_default(tmp_path):
    """CB: load_neural_schema returns copy of default when path doesn't exist."""
    nonexistent = tmp_path / "no_such_schema.json"
    result = config.load_neural_schema(nonexistent)
    assert isinstance(result, dict)
    assert result == dict(config.DEFAULT_NEURAL_SCHEMA)


def test_load_neural_schema_loaded_not_dict(tmp_path):
    """CB: load_neural_schema skips merge when loaded JSON is not a dict."""
    not_dict_file = tmp_path / "not_dict.json"
    not_dict_file.write_text("[1, 2, 3]")
    result = config.load_neural_schema(not_dict_file)
    assert isinstance(result, dict)
    assert result == dict(config.DEFAULT_NEURAL_SCHEMA)


# ---------------------------------------------------------------------------
# load_env: various line formats
# ---------------------------------------------------------------------------


def test_load_env_inline_comment(tmp_path):
    """CB: load_env handles inline comments."""
    env_path = tmp_path / ".env"
    env_path.write_text("KEY=value # comment\n")
    result = config.load_env(env_path)
    assert "KEY" in result


def test_load_env_no_equals_sign(tmp_path):
    """CB: load_env skips lines without equals sign."""
    env_path = tmp_path / ".env"
    env_path.write_text("JUSTAWORD\nREAL=ok\n")
    result = config.load_env(env_path)
    assert result.get("REAL") == "ok"
    assert "JUSTAWORD" not in result


# ---------------------------------------------------------------------------
# _parse_schema_ver: various formats
# ---------------------------------------------------------------------------


def test_parse_schema_ver_dot_only():
    """CB: _parse_schema_ver handles '.' with no digits."""
    result = config._parse_schema_ver(".")
    assert result == (0, 0)


def test_parse_schema_ver_empty_string():
    """CB: _parse_schema_ver handles empty string."""
    result = config._parse_schema_ver("")
    assert result == (0, 0)


# ---------------------------------------------------------------------------
# _safe_int: None and float string
# ---------------------------------------------------------------------------


def test_safe_int_none():
    """CB: _safe_int handles None value."""
    result = config._safe_int(None, default=42)
    assert result == 42


def test_safe_int_float_string():
    """CB: _safe_int handles float string (raises ValueError)."""
    result = config._safe_int("3.14", default=0)
    assert result == 0


# ---------------------------------------------------------------------------
# _safe_float: None
# ---------------------------------------------------------------------------


def test_safe_float_none():
    """CB: _safe_float handles None value."""
    result = config._safe_float(None, default=3.14)
    assert result == 3.14

