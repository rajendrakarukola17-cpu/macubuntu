"""
Regression tests for Update 18: Automatic Model Router.

Tests cover:
  - RAM detection
  - Model tier selection based on RAM
  - Edge0 vs Ollama availability
  - User preference override
  - Disk space requirements
  - Fallback to cloud
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macubuntu" / "features" / "celina"))

from celina_pkg import model_router  # noqa: E402


def test_detect_total_ram_gb():
    """detect_total_ram_gb should return a positive float."""
    ram = model_router.detect_total_ram_gb()
    assert isinstance(ram, float)
    assert ram > 0


def test_detect_free_disk_gb():
    """detect_free_disk_gb should return a non-negative float."""
    disk = model_router.detect_free_disk_gb("/")
    assert isinstance(disk, float)
    assert disk >= 0


def test_edge0_available_when_binary_present():
    """edge0_available should be True when binary exists."""
    with patch.object(model_router.shutil, "which", return_value="/usr/bin/edge0-infer"):
        assert model_router.edge0_available() is True


def test_edge0_available_when_binary_missing():
    """edge0_available should be False when binary is missing."""
    with patch.object(model_router.shutil, "which", return_value=None):
        assert model_router.edge0_available() is False


def test_ollama_available_when_binary_present():
    """ollama_available should be True when binary exists."""
    with patch.object(model_router.shutil, "which", return_value="/usr/bin/ollama"):
        assert model_router.ollama_available() is True


def test_model_tiers_structure():
    """MODEL_TIERS should have the expected structure."""
    assert "qwen3:1.7b_q4km" in model_router.MODEL_TIERS
    assert "edge0-8b" in model_router.MODEL_TIERS
    assert "edge0-35b-compressed" in model_router.MODEL_TIERS
    assert "edge0-35b-standard" in model_router.MODEL_TIERS

    for tier_id, tier in model_router.MODEL_TIERS.items():
        assert "name" in tier
        assert "provider" in tier
        assert "active_ram_gb" in tier
        assert "disk_gb" in tier
        assert "description" in tier
        assert "best_for" in tier


def test_selection_low_ram_with_edge0():
    """8 GB RAM with edge0 should select Edge0-8B."""
    with patch.object(model_router, "detect_total_ram_gb", return_value=8.0), \
         patch.object(model_router, "detect_free_disk_gb", return_value=50.0), \
         patch.object(model_router, "edge0_available", return_value=True), \
         patch.object(model_router, "ollama_available", return_value=True):
        sel = model_router.select_best_model()
        assert sel.model_id == "edge0-8b"
        assert sel.provider == "edge0"
        assert sel.active_ram_gb == 1.0


def test_selection_low_ram_without_edge0():
    """8 GB RAM without edge0 should select qwen3:1.7b via Ollama."""
    with patch.object(model_router, "detect_total_ram_gb", return_value=8.0), \
         patch.object(model_router, "detect_free_disk_gb", return_value=50.0), \
         patch.object(model_router, "edge0_available", return_value=False), \
         patch.object(model_router, "ollama_available", return_value=True):
        sel = model_router.select_best_model()
        assert sel.model_id == "qwen3:1.7b_q4km"
        assert sel.provider == "ollama"
        assert sel.active_ram_gb == 1.4


def test_selection_high_ram_with_edge0():
    """16 GB RAM with edge0 and disk should select Edge0-35B compressed."""
    with patch.object(model_router, "detect_total_ram_gb", return_value=16.0), \
         patch.object(model_router, "detect_free_disk_gb", return_value=50.0), \
         patch.object(model_router, "edge0_available", return_value=True), \
         patch.object(model_router, "ollama_available", return_value=True):
        sel = model_router.select_best_model()
        assert sel.model_id == "edge0-35b-compressed"
        assert sel.provider == "edge0"
        assert sel.active_ram_gb == 1.8


def test_selection_very_high_ram():
    """24 GB RAM should select Edge0-35B standard."""
    with patch.object(model_router, "detect_total_ram_gb", return_value=24.0), \
         patch.object(model_router, "detect_free_disk_gb", return_value=50.0), \
         patch.object(model_router, "edge0_available", return_value=True), \
         patch.object(model_router, "ollama_available", return_value=True):
        sel = model_router.select_best_model()
        assert sel.model_id == "edge0-35b-standard"
        assert sel.active_ram_gb == 2.9


def test_selection_no_local_runtime():
    """No Edge0 or Ollama should fall back to cloud."""
    with patch.object(model_router, "detect_total_ram_gb", return_value=8.0), \
         patch.object(model_router, "detect_free_disk_gb", return_value=50.0), \
         patch.object(model_router, "edge0_available", return_value=False), \
         patch.object(model_router, "ollama_available", return_value=False):
        sel = model_router.select_best_model()
        assert sel.provider == "cloud"
        assert sel.active_ram_gb == 0.0


def test_user_preference_override():
    """User-specified preferred model should be respected."""
    with patch.object(model_router, "detect_total_ram_gb", return_value=8.0), \
         patch.object(model_router, "detect_free_disk_gb", return_value=50.0), \
         patch.object(model_router, "edge0_available", return_value=True), \
         patch.object(model_router, "ollama_available", return_value=True):
        sel = model_router.select_best_model(preferred_model="qwen3:1.7b_q4km")
        assert sel.model_id == "qwen3:1.7b_q4km"
        assert sel.reason == "User-specified preferred model"


def test_get_model_selection_summary():
    """get_model_selection_summary should return a complete dict."""
    with patch.object(model_router, "detect_total_ram_gb", return_value=8.0), \
         patch.object(model_router, "detect_free_disk_gb", return_value=50.0), \
         patch.object(model_router, "edge0_available", return_value=True), \
         patch.object(model_router, "ollama_available", return_value=True):
        summary = model_router.get_model_selection_summary()
        assert "selected_model" in summary
        assert "provider" in summary
        assert "active_ram_gb" in summary
        assert "system_ram_gb" in summary
        assert "reason" in summary
        assert "best_for" in summary
        assert "all_tiers" in summary


def test_get_ram_tier_constrained():
    """get_ram_tier returns CONSTRAINED for <= 10 GB."""
    from celina_pkg import model_selector
    assert model_selector.get_ram_tier(8192) == "CONSTRAINED"   # 8 GB
    assert model_selector.get_ram_tier(10240) == "CONSTRAINED"  # 10 GB boundary


def test_get_ram_tier_balanced():
    """get_ram_tier returns BALANCED for 10-22 GB."""
    from celina_pkg import model_selector
    assert model_selector.get_ram_tier(10241) == "BALANCED"
    assert model_selector.get_ram_tier(16384) == "BALANCED"   # 16 GB
    assert model_selector.get_ram_tier(22528) == "BALANCED"   # 22 GB boundary


def test_get_ram_tier_headroom():
    """get_ram_tier returns HEADROOM for >= 23 GB."""
    from celina_pkg import model_selector
    assert model_selector.get_ram_tier(22529) == "HEADROOM"
    assert model_selector.get_ram_tier(32768) == "HEADROOM"   # 32 GB


def test_tier_num_ctx():
    """tier_num_ctx returns correct values per tier."""
    from celina_pkg import model_selector
    assert model_selector.tier_num_ctx("CONSTRAINED") == 2048
    assert model_selector.tier_num_ctx("BALANCED") == 4096
    assert model_selector.tier_num_ctx("HEADROOM") == 16384


def test_tier_whisper_idle_offload():
    """tier_whisper_idle_offload_sec returns correct values per tier."""
    from celina_pkg import model_selector
    assert model_selector.tier_whisper_idle_offload_sec("CONSTRAINED") == 90
    assert model_selector.tier_whisper_idle_offload_sec("BALANCED") == 180
    assert model_selector.tier_whisper_idle_offload_sec("HEADROOM") == 300


def test_tier_cache_hot_max_items():
    """tier_cache_hot_max_items returns correct values per tier."""
    from celina_pkg import model_selector
    assert model_selector.tier_cache_hot_max_items("CONSTRAINED") == 32
    assert model_selector.tier_cache_hot_max_items("BALANCED") == 128
    assert model_selector.tier_cache_hot_max_items("HEADROOM") == 512


def test_tier_max_concurrent_mcp():
    """tier_max_concurrent_mcp returns correct values per tier."""
    from celina_pkg import model_selector
    assert model_selector.tier_max_concurrent_mcp("CONSTRAINED") == 1
    assert model_selector.tier_max_concurrent_mcp("BALANCED") == 2
    assert model_selector.tier_max_concurrent_mcp("HEADROOM") == 4


def test_forbidden_ui_targets_refusal():
    """InputBroker.check_allowed refuses Celina's own UI and system auth dialogs (T2)."""
    from celina_pkg import automation

    broker = automation.InputBroker()

    # Celina's own Dynamic Island should be refused
    allowed, reason = broker.check_allowed(
        active_window_info={"app": "celina-search", "title": "Celina Dynamic Island"}
    )
    assert not allowed
    assert "self-approval" in reason.lower() or "protected" in reason.lower()

    # Polkit dialog should be refused
    allowed, reason = broker.check_allowed(
        active_window_info={"app": "polkit-gnome-authentication-agent-1", "title": "Authentication"}
    )
    assert not allowed
    assert "self-approval" in reason.lower() or "protected" in reason.lower()

    # xdg-desktop-portal should be refused
    allowed, reason = broker.check_allowed(
        active_window_info={"app": "xdg-desktop-portal-gnome", "title": "Portal"}
    )
    assert not allowed
    assert "self-approval" in reason.lower() or "protected" in reason.lower()

    # Normal app (Firefox) should NOT be refused by T2
    allowed, reason = broker.check_allowed(
        active_window_info={"app": "firefox", "title": "Example Website"}
    )
    # May still be denied for other reasons (stop requested, rate limit), but NOT T2
    if not allowed:
        assert "self-approval" not in reason.lower()
        assert "protected" not in reason.lower()


def test_atspi_direct_action_forbidden_targets():
    """_try_atspi_direct_action refuses Celina's own UI and system auth dialogs (T2)."""
    from celina_pkg import automation

    # These should be refused at the FORBIDDEN_UI_TARGETS level, before AT-SPI
    result, msg = automation._try_atspi_direct_action("Celina Dynamic Island Approve")
    assert not result
    assert "self-approval" in msg.lower() or "protected" in msg.lower()

    result, msg = automation._try_atspi_direct_action("Polkit Authentication")
    assert not result
    assert "self-approval" in msg.lower() or "protected" in msg.lower()


if __name__ == "__main__":
    test_detect_total_ram_gb()
    test_detect_free_disk_gb()
    test_edge0_available_when_binary_present()
    test_edge0_available_when_binary_missing()
    test_ollama_available_when_binary_present()
    test_model_tiers_structure()
    test_selection_low_ram_with_edge0()
    test_selection_low_ram_without_edge0()
    test_selection_high_ram_with_edge0()
    test_selection_very_high_ram()
    test_selection_no_local_runtime()
    test_user_preference_override()
    test_get_model_selection_summary()
    test_get_ram_tier_constrained()
    test_get_ram_tier_balanced()
    test_get_ram_tier_headroom()
    test_tier_num_ctx()
    test_tier_whisper_idle_offload()
    test_tier_cache_hot_max_items()
    test_tier_max_concurrent_mcp()
    test_forbidden_ui_targets_refusal()
    test_atspi_direct_action_forbidden_targets()
    print("All model_router tests passed!")
