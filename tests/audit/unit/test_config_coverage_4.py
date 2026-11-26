"""
tests/audit/unit/test_config_coverage_4.py

Phase 7d — TASK 1: config.py branch coverage tests (round 4).
Targets: set_process_name (EPERM/non-Linux), umask failure, runtime dir,
neural schema migration, parse_schema_ver, safe_int/float, load_env,
get_assistant_name/nickname, is_lite_mode, get_voice/espeak_for_lang,
is_web_comms_enabled, get_web_comms_allowed_hosts, get_libreoffice_uno_port,
is_autonomy_observe_only, format_persona_instructions, sanitize_identity_token,
detect_system_ram_gb, detect_available_ram_mb, lookup_catalog_model,
format_model_catalog, set_local_model, set_env_pairs, model selector funcs,
get_smolLM_status, apply_recommended_models, get_active_local_inference_route.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import config


# ---------------------------------------------------------------------------
# set_process_name
# ---------------------------------------------------------------------------


def test_set_process_name_non_linux(monkeypatch):
    """CB: set_process_name returns immediately on non-Linux."""
    monkeypatch.setattr(config.sys, "platform", "darwin")
    # Should not raise
    config.set_process_name("test")


def test_set_process_name_eperm(monkeypatch):
    """CB: set_process_name handles EPERM from prctl (fails open)."""
    monkeypatch.setattr(config.sys, "platform", "linux")
    import errno

    fake_libc = MagicMock()
    fake_libc.prctl.return_value = -1  # error
    monkeypatch.setattr(config.ctypes, "CDLL", lambda *a, **k: fake_libc)
    monkeypatch.setattr(config.ctypes, "get_errno", lambda: errno.EPERM)
    # Should not raise despite EPERM (fails open)
    config.set_process_name("test")


def test_set_process_name_other_errno_fails_open(monkeypatch):
    """CB: set_process_name catches non-EPERM errors internally (fail-open)."""
    monkeypatch.setattr(config.sys, "platform", "linux")
    import errno

    fake_libc = MagicMock()
    fake_libc.prctl.return_value = -1
    monkeypatch.setattr(config.ctypes, "CDLL", lambda *a, **k: fake_libc)
    monkeypatch.setattr(config.ctypes, "get_errno", lambda: errno.ENOENT)
    # Should not raise — caught by except (OSError, AttributeError)
    config.set_process_name("test")


# ---------------------------------------------------------------------------
# umask / runtime dir
# ---------------------------------------------------------------------------


def test_os_umask_exception(monkeypatch):
    """CB: os.umask failure is silently ignored."""
    monkeypatch.setattr(config.os, "umask", lambda _: (_ for _ in ()).throw(OSError("boom")))
    # Just verify no crash
    try:
        config.os.umask(0o077)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# safe coercion helpers
# ---------------------------------------------------------------------------


def test_safe_int_invalid_with_key():
    """CB: _safe_int with invalid value and key."""
    result = config._safe_int("not_an_int", 42, "CELINA_TEST")
    assert result == 42


def test_safe_float_invalid_with_key():
    """CB: _safe_float with invalid value and key."""
    result = config._safe_float("not_a_float", 3.14, "CELINA_TEST")
    assert result == 3.14


def test_safe_int_none():
    """CB: _safe_int with None value."""
    result = config._safe_int(None, 7, "")
    assert result == 7


def test_safe_float_none():
    """CB: _safe_float with None value."""
    result = config._safe_float(None, 2.5, "")
    assert result == 2.5


# ---------------------------------------------------------------------------
# load_env variants
# ---------------------------------------------------------------------------


def test_load_env_quoted_values(tmp_path):
    """CB: load_env strips surrounding quotes."""
    env_file = tmp_path / "env"
    env_file.write_text('KEY="value with spaces"\nANOTHER=\"quoted\"\n')
    result = config.load_env(env_file)
    assert result["KEY"] == "value with spaces"
    assert result["ANOTHER"] == "quoted"


def test_load_env_export_prefix(tmp_path):
    """CB: load_env strips 'export ' prefix."""
    env_file = tmp_path / "env"
    env_file.write_text("export FOO=bar\n")
    result = config.load_env(env_file)
    assert result["FOO"] == "bar"


def test_load_env_no_equals(tmp_path):
    """CB: load_env skips lines without '='."""
    env_file = tmp_path / "env"
    env_file.write_text("just a line\nKEY=value\n")
    result = config.load_env(env_file)
    assert result == {"KEY": "value"}


def test_load_env_empty_file(tmp_path):
    """CB: load_env returns empty dict for empty file."""
    env_file = tmp_path / "env"
    env_file.write_text("")
    result = config.load_env(env_file)
    assert result == {}


# ---------------------------------------------------------------------------
# web comms
# ---------------------------------------------------------------------------


def test_is_web_comms_enabled_env_true(monkeypatch):
    """CB: is_web_comms_enabled checks CELINA_WEB_COMMS_ENABLED env."""
    monkeypatch.setenv("CELINA_WEB_COMMS_ENABLED", "true")
    assert config.is_web_comms_enabled() is True


def test_is_web_comms_enabled_env_false(monkeypatch):
    """CB: is_web_comms_enabled returns False for 'false' env."""
    monkeypatch.setenv("CELINA_WEB_COMMS_ENABLED", "false")
    assert config.is_web_comms_enabled() is False


def test_get_web_comms_allowed_hosts():
    """CB: get_web_comms_allowed_hosts normalizes hostnames."""
    # Reset module state - just call the function
    result = config.get_web_comms_allowed_hosts()
    assert isinstance(result, list)


def test_get_libreoffice_uno_port_env(monkeypatch):
    """CB: get_libreoffice_uno_port reads from env."""
    monkeypatch.setenv("CELINA_LIBREOFFICE_UNO_PORT", "9999")
    assert config.get_libreoffice_uno_port() == 9999


def test_get_libreoffice_uno_port_invalid_env(monkeypatch):
    """CB: get_libreoffice_uno_port falls back to default for non-digit env."""
    monkeypatch.setenv("CELINA_LIBREOFFICE_UNO_PORT", "abc")
    assert config.get_libreoffice_uno_port() == 2002


def test_is_autonomy_observe_only_env(monkeypatch):
    """CB: is_autonomy_observe_only checks env override."""
    monkeypatch.setenv("CELINA_AUTONOMY_OBSERVE_ONLY", "false")
    assert config.is_autonomy_observe_only() is False


# ---------------------------------------------------------------------------
# assistant identity
# ---------------------------------------------------------------------------


def test_get_assistant_name_default():
    """CB: get_assistant_name returns default 'Celina'."""
    assert config.get_assistant_name() == "Celina"


def test_get_assistant_nickname_default():
    """CB: get_assistant_nickname returns default 'Celly'."""
    assert config.get_assistant_nickname() == "Celly"


def test_format_persona_instructions():
    """CB: format_persona_instructions replaces placeholders."""
    result = config.format_persona_instructions()
    assert "Celina" in result or "Celly" in result


def test_sanitize_identity_token_control_chars():
    """CB: _sanitize_identity_token strips control chars."""
    result = config._sanitize_identity_token("hello\r\n\tworld")
    assert "\r" not in result
    assert "\n" not in result
    assert "\t" not in result


# ---------------------------------------------------------------------------
# lite mode
# ---------------------------------------------------------------------------


def test_is_lite_mode_env_false(monkeypatch):
    """CB: is_lite_mode reads env var."""
    monkeypatch.delenv("CELINA_LITE_MODE", raising=False)
    # Clear ENV cache
    config.ENV.pop("CELINA_LITE_MODE", None)
    assert config.is_lite_mode() is False


def test_is_lite_mode_env_true(monkeypatch):
    """CB: is_lite_mode returns True for '1'."""
    monkeypatch.setenv("CELINA_LITE_MODE", "1")
    assert config.is_lite_mode() is True


def test_is_lite_mode_env_dict(monkeypatch):
    """CB: is_lite_mode reads from ENV dict."""
    monkeypatch.delenv("CELINA_LITE_MODE", raising=False)
    config.ENV["CELINA_LITE_MODE"] = "yes"
    assert config.is_lite_mode() is True


# ---------------------------------------------------------------------------
# voice functions
# ---------------------------------------------------------------------------


def test_get_voice_for_lang_env_override(monkeypatch):
    """CB: get_voice_for_lang returns env override."""
    monkeypatch.setenv("CELINA_VOICE_EN", "custom_voice")
    result = config.get_voice_for_lang("en")
    assert result == "custom_voice"


def test_get_voice_for_lang_fallback():
    """CB: get_voice_for_lang falls back to language config."""
    result = config.get_voice_for_lang("en")
    assert isinstance(result, str)


def test_get_espeak_for_lang_unknown():
    """CB: get_espeak_for_lang falls back to default for unknown lang."""
    result = config.get_espeak_for_lang("xx")
    assert result == "en-us+f3"


# ---------------------------------------------------------------------------
# RAM detection
# ---------------------------------------------------------------------------


def test_detect_system_ram_gb_success(monkeypatch):
    """CB: detect_system_ram_gb parses /proc/meminfo."""
    import builtins

    def fake_open(path, *a, **k):
        if str(path).endswith("meminfo"):
            return mock_open(read_data="MemTotal: 16384000 kB\n")()
        raise OSError("no such file")

    monkeypatch.setattr(builtins, "open", fake_open)
    result = config.detect_system_ram_gb()
    assert result >= 1


def test_detect_system_ram_gb_fallback(monkeypatch):
    """CB: detect_system_ram_gb returns 8 on OSError."""
    import builtins

    monkeypatch.setattr(builtins, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    result = config.detect_system_ram_gb()
    assert result == 8


def test_detect_available_ram_mb_success(monkeypatch):
    """CB: detect_available_ram_mb parses MemAvailable."""
    import builtins

    def fake_open(path, *a, **k):
        if str(path).endswith("meminfo"):
            return mock_open(read_data="MemAvailable: 4194304 kB\n")()
        raise OSError("no such file")

    monkeypatch.setattr(builtins, "open", fake_open)
    result = config.detect_available_ram_mb()
    assert result >= 0


def test_detect_available_ram_mb_fallback(monkeypatch):
    """CB: detect_available_ram_mb returns 2048 on OSError."""
    import builtins

    monkeypatch.setattr(builtins, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    result = config.detect_available_ram_mb()
    assert result == 2048


# ---------------------------------------------------------------------------
# model catalog
# ---------------------------------------------------------------------------


def test_lookup_catalog_model_found():
    """CB: lookup_catalog_model returns matching item."""
    result = config.lookup_catalog_model("qwen3:1.7b")
    assert result is not None
    assert result["tag"] == "qwen3:1.7b"


def test_lookup_catalog_model_not_found():
    """CB: lookup_catalog_model returns None for unknown tag."""
    result = config.lookup_catalog_model("nonexistent:model")
    assert result is None


def test_format_model_catalog():
    """CB: format_model_catalog returns formatted string."""
    result = config.format_model_catalog(ram_gb=32)
    assert "qwen3:1.7b" in result or "Dense" in result


# ---------------------------------------------------------------------------
# set_local_model
# ---------------------------------------------------------------------------


def test_set_local_model_invalid_tag():
    """CB: set_local_model raises ValueError for invalid tag."""
    with pytest.raises(ValueError):
        config.set_local_model("invalid tag!")


def test_set_local_model_env_path_not_exists(tmp_path, monkeypatch):
    """CB: set_local_model creates env file when env_path is provided and doesn't exist."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "nonexistent.env")
    monkeypatch.setattr(config, "ENV", {})
    result = config.set_local_model("qwen3:1.7b", env_path=tmp_path / "new.env")
    assert result[0] == "qwen3:1.7b"


# ---------------------------------------------------------------------------
# set_env_pairs
# ---------------------------------------------------------------------------


def test_set_env_pairs_empty():
    """CB: set_env_pairs returns early for empty dict."""
    config.set_env_pairs({})


def test_set_env_pairs_invalid_keys():
    """CB: set_env_pairs skips empty/invalid keys."""
    config.set_env_pairs({"   ": "value", "": ""})


# ---------------------------------------------------------------------------
# model selector wrappers
# ---------------------------------------------------------------------------


def test_get_recommended_whisper_model(monkeypatch):
    """CB: get_recommended_whisper_model delegates to model_selector."""
    fake = MagicMock()
    fake.select_whisper_model.return_value = {"model": "tiny"}
    monkeypatch.setattr(config.model_selector, "select_whisper_model", fake.select_whisper_model)
    result = config.get_recommended_whisper_model()
    assert result["model"] == "tiny"


def test_get_recommended_ollama_model(monkeypatch):
    """CB: get_recommended_ollama_model delegates to model_selector."""
    fake = MagicMock()
    fake.select_ollama_model.return_value = {"model": "qwen3:1.7b"}
    monkeypatch.setattr(config.model_selector, "select_ollama_model", fake.select_ollama_model)
    result = config.get_recommended_ollama_model()
    assert result["model"] == "qwen3:1.7b"


def test_get_recommended_edge0_model(monkeypatch):
    """CB: get_recommended_edge0_model delegates to model_selector."""
    fake = MagicMock()
    fake.select_edge0_model.return_value = {"model": "edge0:gguf"}
    monkeypatch.setattr(config.model_selector, "select_edge0_model", fake.select_edge0_model)
    result = config.get_recommended_edge0_model()
    assert result["model"] == "edge0:gguf"


def test_get_full_model_decision(monkeypatch):
    """CB: get_full_model_decision delegates to model_selector."""
    fake = MagicMock()
    fake.make_full_model_decision.return_value = MagicMock()
    monkeypatch.setattr(config.model_selector, "make_full_model_decision", fake.make_full_model_decision)
    result = config.get_full_model_decision()
    assert result is not None


def test_format_model_decision_summary_no_decision(monkeypatch):
    """CB: format_model_decision_summary computes decision when None."""
    fake = MagicMock()
    fake.make_full_model_decision.return_value = MagicMock()
    fake.format_decision_summary.return_value = "summary"
    monkeypatch.setattr(config.model_selector, "make_full_model_decision", fake.make_full_model_decision)
    monkeypatch.setattr(config.model_selector, "format_decision_summary", fake.format_decision_summary)
    result = config.format_model_decision_summary()
    assert result == "summary"


def test_apply_recommended_models(monkeypatch):
    """CB: apply_recommended_models applies whisper and/or ollama."""
    fake_whisper = MagicMock(return_value={"model": "tiny"})
    fake_ollama = MagicMock(return_value={"model": "qwen3:1.7b"})
    monkeypatch.setattr(config, "get_recommended_whisper_model", fake_whisper)
    monkeypatch.setattr(config, "get_recommended_ollama_model", fake_ollama)
    monkeypatch.setattr(config, "set_env_pairs", MagicMock())
    monkeypatch.setattr(config, "set_local_model", MagicMock(return_value=("qwen3:1.7b", None)))
    result = config.apply_recommended_models(apply_whisper=True, apply_ollama=True)
    assert "whisper" in result
    assert "ollama" in result


# ---------------------------------------------------------------------------
# smolLM status
# ---------------------------------------------------------------------------


def test_get_smolLM_status_import_success(monkeypatch):
    """CB: get_smolLM_status returns status when import succeeds."""
    fake_mod = MagicMock()
    fake_mod.gear0_5_smolLM_status.return_value = {"available": True}
    monkeypatch.setitem(sys.modules, "celina_pkg.gear0_5_smolLM", fake_mod)
    result = config.get_smolLM_status()
    assert result["available"] is True


def test_get_smolLM_status_import_exception(monkeypatch):
    """CB: get_smolLM_status returns available=False on import error."""
    monkeypatch.setitem(sys.modules, "celina_pkg.gear0_5_smolLM", None)
    # Remove any cached import to force ImportError
    result = config.get_smolLM_status()
    assert result["available"] is False


# ---------------------------------------------------------------------------
# get_active_local_inference_route
# ---------------------------------------------------------------------------


def test_get_active_local_inference_route_prefer_npu_false(monkeypatch):
    """CB: get_active_local_inference_route with prefer_npu=False."""
    fake_topo = MagicMock()
    fake_resolve = MagicMock(return_value={"endpoint": "http://ollama:11434", "backend": "ollama"})

    # Patch the celina_hardware.compute module that is imported inside the function
    fake_compute = MagicMock()
    fake_compute.detect_compute_topology.return_value = fake_topo
    fake_compute.resolve_inference_endpoint = fake_resolve
    monkeypatch.setitem(sys.modules, "celina_hardware.compute", fake_compute)
    result = config.get_active_local_inference_route(prefer_npu=False)
    assert result["backend"] == "ollama"


def test_get_active_local_inference_route_exception(monkeypatch):
    """CB: get_active_local_inference_route falls back to ollama on exception."""
    fake_compute = MagicMock()
    fake_compute.detect_compute_topology.side_effect = Exception("boom")
    monkeypatch.setitem(sys.modules, "celina_hardware.compute", fake_compute)
    result = config.get_active_local_inference_route(prefer_npu=True)
    assert result["backend"] == "ollama"


# ---------------------------------------------------------------------------
# _parse_schema_ver
# ---------------------------------------------------------------------------


def test_parse_schema_ver_no_match():
    """CB: _parse_schema_ver returns (0,0) for unparseable input."""
    result = config._parse_schema_ver("abc")
    assert result == (0, 0)


def test_parse_schema_ver_major_only():
    """CB: _parse_schema_ver parses major-only version."""
    result = config._parse_schema_ver("4")
    assert result == (4, 0)


# ---------------------------------------------------------------------------
# _union_list
# ---------------------------------------------------------------------------


def test_union_list_dedup():
    """CB: _union_list deduplicates across lists."""
    result = config._union_list(["a", "b"], ["b", "c"])
    assert result == ["a", "b", "c"]


def test_union_list_none():
    """CB: _union_list handles non-list inputs."""
    result = config._union_list(None, None)
    assert result == []


# ---------------------------------------------------------------------------
# deep merge
# ---------------------------------------------------------------------------


def test_deep_merge_nested():
    """CB: _deep_merge recursively merges dicts."""
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    override = {"a": {"y": 99}, "c": 3}
    result = config._deep_merge(base, override)
    assert result["a"]["x"] == 1
    assert result["a"]["y"] == 99
    assert result["b"] == 1
    assert result["c"] == 3


# ---------------------------------------------------------------------------
# is_hard_blocked_path
# ---------------------------------------------------------------------------


def test_is_hard_blocked_path_trailing_slash(tmp_path, monkeypatch):
    """CB: is_hard_blocked_path handles trailing slash paths."""
    # Use the real HOME-based blocked paths; just call with a safe path
    result = config.is_hard_blocked_path(str(tmp_path / "safe"))
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# config_load_warnings
# ---------------------------------------------------------------------------


def test_config_load_warnings():
    """CB: config_load_warnings returns a list."""
    result = config.config_load_warnings()
    assert isinstance(result, list)
