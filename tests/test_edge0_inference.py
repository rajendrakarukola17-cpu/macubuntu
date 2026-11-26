"""
Regression tests for Update 18: Edge0-35B streaming MoE inference with
aggressive memory optimization (1.5-2.0 GiB active RAM target).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macubuntu" / "features" / "celina"))

from celina_pkg import edge0_inference  # noqa: E402


def test_edge0_config_defaults():
    """Default Edge0Config should target 1.8 GB active RAM."""
    cfg = edge0_inference.Edge0Config()
    assert cfg.active_ram_target_gb == 1.8
    assert cfg.quantization == "Q4_K"
    assert cfg.expert_quantization == "Q3_K_S"
    assert cfg.kv_cache_quantization == "Q4_0"
    assert cfg.context_cap == 4096
    assert cfg.disable_lru_cache is True
    assert cfg.dma_scratch_mb == 200
    assert cfg.prerouter_enabled is True
    assert cfg.recover_lora_enabled is True
    assert cfg.speculative_decoding is True
    assert cfg.draft_model == "qwen3:0.5b"


def test_edge0_config_to_edge0_args():
    """to_edge0_args should produce correct CLI arguments."""
    cfg = edge0_inference.Edge0Config()
    args = cfg.to_edge0_args()
    assert "edge0-infer" in args
    assert "serve" in args
    assert "--model" in args
    assert "--quantization" in args
    assert "Q4_K" in args
    assert "--expert-quantization" in args
    assert "Q3_K_S" in args
    assert "--kv-cache-quant" in args
    assert "Q4_0" in args
    assert "--max-context" in args
    assert "4096" in args
    assert "--cache-size" in args
    assert "0" in args  # LRU cache disabled
    assert "--prerouter" in args
    assert "--recover-lora" in args
    assert "--speculative" in args
    assert "--draft-model" in args
    assert "qwen3:0.5b" in args


def test_edge0_config_custom_values():
    """Custom Edge0Config values should be reflected in args."""
    cfg = edge0_inference.Edge0Config(
        active_ram_target_gb=1.5,
        quantization="IQ4_XS",
        expert_quantization="IQ3_XXS",
        context_cap=2048,
        disable_lru_cache=False,
        prerouter_enabled=False,
    )
    args = cfg.to_edge0_args()
    assert "1.5GB" in args
    assert "IQ4_XS" in args
    assert "IQ3_XXS" in args
    assert "2048" in args
    # When disable_lru_cache=False, --cache-size 0 should NOT be present
    assert "--cache-size" not in args or args[args.index("--cache-size") + 1] != "0"
    assert "--prerouter" not in args


def test_edge0_inference_not_available_without_binary():
    """is_available should be False when edge0-infer binary is missing."""
    with patch.object(edge0_inference.shutil, "which", return_value=None):
        engine = edge0_inference.Edge0Inference()
        assert engine.is_available is False


def test_edge0_inference_available_with_binary():
    """is_available should be True when edge0-infer binary exists."""
    with patch.object(edge0_inference.shutil, "which", return_value="/usr/bin/edge0-infer"):
        engine = edge0_inference.Edge0Inference()
        assert engine.is_available is True


def test_edge0_chat_completion_offline():
    """chat_completion should return offline placeholder when server not running."""
    with patch.object(edge0_inference.shutil, "which", return_value="/usr/bin/edge0-infer"):
        engine = edge0_inference.Edge0Inference()
        engine._proc = None  # Ensure not running
        result = engine.chat_completion([{"role": "user", "content": "hello"}])
        assert "choices" in result
        assert "Edge0 server not running" in result["choices"][0]["message"]["content"]


def test_edge0_memory_stats():
    """get_memory_stats should return proper structure."""
    with patch.object(edge0_inference.shutil, "which", return_value="/usr/bin/edge0-infer"):
        engine = edge0_inference.Edge0Inference()
        stats = engine.get_memory_stats()
        assert "target_gb" in stats
        assert "measured_gb" in stats
        assert "within_target" in stats
        assert "standard_35b_gb" in stats
        assert "optimization_techniques" in stats
        assert stats["standard_35b_gb"] == 2.9
        assert len(stats["optimization_techniques"]) > 5


def test_memory_optimization_techniques_documented():
    """All 4 memory optimization techniques should be documented."""
    cfg = edge0_inference.Edge0Config()
    engine = edge0_inference.Edge0Inference(cfg)
    stats = engine.get_memory_stats()
    techniques = stats["optimization_techniques"]
    # Check for the 4 key techniques from the user's requirements
    technique_text = " ".join(techniques).lower()
    assert "quantization" in technique_text  # Q4_K / IQ4_XS
    assert "lru" in technique_text or "cache" in technique_text  # Disable LRU
    assert "dma" in technique_text or "scratch" in technique_text  # DMA buffer
    assert "kv" in technique_text  # Q4_0 KV cache


def test_target_ram_within_range():
    """Target RAM should be within 1.5-2.0 GiB range."""
    cfg = edge0_inference.Edge0Config(active_ram_target_gb=1.8)
    target_bytes = cfg.active_ram_target_gb * 1024**3
    assert edge0_inference.TARGET_ACTIVE_RAM_MIN <= target_bytes <= edge0_inference.TARGET_ACTIVE_RAM_MAX


def test_edge0_config_context_cap():
    """Context cap should be 4096 tokens by default."""
    cfg = edge0_inference.Edge0Config()
    assert cfg.context_cap == 4096


if __name__ == "__main__":
    test_edge0_config_defaults()
    test_edge0_config_to_edge0_args()
    test_edge0_config_custom_values()
    test_edge0_inference_not_available_without_binary()
    test_edge0_inference_available_with_binary()
    test_edge0_chat_completion_offline()
    test_edge0_memory_stats()
    test_memory_optimization_techniques_documented()
    test_target_ram_within_range()
    test_edge0_config_context_cap()
    print("All edge0_inference tests passed!")
