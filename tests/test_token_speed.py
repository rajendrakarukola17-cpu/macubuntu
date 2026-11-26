"""
Regression tests for Update 18: Token Speed Acceleration.

Tests cover:
  - Token cache hit/miss
  - Prompt compression ratio
  - Speculative decoding hit/miss
  - Tool-first reflex bypass
  - Full pipeline processing
  - Statistics reporting
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macubuntu" / "features" / "celina"))

from celina_pkg import token_speed  # noqa: E402


def test_token_cache_miss():
    """Cache miss should return None."""
    cache = token_speed.TokenCache(cache_dir=Path("/tmp/test_token_cache_miss"))
    result = cache.get("what is 2+2", "qwen3:1.7b", 0.7)
    assert result is None


def test_token_cache_hit():
    """Cache hit should return the stored response."""
    import tempfile
    cache_dir = Path(tempfile.mkdtemp())
    cache = token_speed.TokenCache(cache_dir=cache_dir)
    cache.set("what is 2+2", "qwen3:1.7b", "4", 0.7)
    result = cache.get("what is 2+2", "qwen3:1.7b", 0.7)
    assert result == "4"


def test_token_cache_different_models():
    """Different models should have different cache keys."""
    import tempfile
    cache_dir = Path(tempfile.mkdtemp())
    cache = token_speed.TokenCache(cache_dir=cache_dir)
    cache.set("hello", "qwen3:1.7b", "response_a", 0.7)
    result_b = cache.get("hello", "edge0-35b", 0.7)
    assert result_b is None  # Different model, different key


def test_token_cache_stats():
    """Cache stats should track hits and misses."""
    import tempfile
    cache_dir = Path(tempfile.mkdtemp())
    cache = token_speed.TokenCache(cache_dir=cache_dir)
    cache.get("miss1", "m", 0.7)
    cache.get("miss2", "m", 0.7)
    cache.set("hit1", "m", "resp1", 0.7)
    cache.get("hit1", "m", 0.7)
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["hit_rate_pct"] == 33.3


def test_prompt_compression_removable_phrases():
    """Removable phrases should be stripped from prompts."""
    compressor = token_speed.PromptCompressor()
    compressed = compressor.compress("please can you tell me what is the weather today")
    assert "please " not in compressed.lower()
    assert "can you " not in compressed.lower()
    assert "tell me " not in compressed.lower()
    assert "what is the " not in compressed.lower()


def test_prompt_compression_shorten_map():
    """Shorten map should shorten common phrases."""
    compressor = token_speed.PromptCompressor()
    compressed = compressor.compress("i need to go to the store")
    assert "i need to" not in compressed  # Should be shortened to "need to"
    assert "need to" in compressed  # Replacement is present


def test_prompt_compression_ratio():
    """compression_ratio should return correct value."""
    compressor = token_speed.PromptCompressor()
    ratio = compressor.compression_ratio("hello world", "hello")
    assert abs(ratio - 0.5454) < 0.01  # 1 - 5/11 = 0.5454...


def test_prompt_compression_does_not_overcompress():
    """If compression removes too much, return original."""
    compressor = token_speed.PromptCompressor()
    short = "hi"
    result = compressor.compress(short)
    assert result == short  # Too short to compress meaningfully


def test_speculative_decoder_stats():
    """SpeculativeDecoder stats should track hits and misses."""
    decoder = token_speed.SpeculativeDecoder()
    stats = decoder.stats()
    assert "draft_model" in stats
    assert "verify_model" in stats
    assert "hit_rate_pct" in stats


def test_tool_first_reflex_bypass():
    """Gear 0 reflex should bypass LLM for common tasks."""
    accelerator = token_speed.TokenSpeedAccelerator(
        enable_cache=False, enable_compression=False, enable_speculative=False
    )
    should_use, response = accelerator.should_use_llm("turn on dark mode", ["dark_mode_toggle"])
    assert should_use is False
    assert "dark_mode_toggle" in response
    assert "0 tokens" in response


def test_tool_first_no_match():
    """Non-reflex prompts should require LLM."""
    accelerator = token_speed.TokenSpeedAccelerator(
        enable_cache=False, enable_compression=False, enable_speculative=False
    )
    should_use, response = accelerator.should_use_llm("write a poem about space", ["dark_mode_toggle"])
    assert should_use is True
    assert response is None


def test_full_pipeline_cache_hit():
    """Full pipeline should return cached response on cache hit."""
    import tempfile
    cache_dir = Path(tempfile.mkdtemp())
    accelerator = token_speed.TokenSpeedAccelerator(
        enable_cache=True, enable_compression=False, enable_speculative=False
    )
    accelerator.cache.cache_dir = cache_dir
    accelerator.cache.set("repeat this", "qwen3:1.7b", "cached response", 0.7)

    result = accelerator.process_prompt("repeat this", "qwen3:1.7b", 0.7)
    assert result["cache_hit"] is True
    assert result["response"] == "cached response"
    assert result["tokens_saved_pct"] == 100.0


def test_full_pipeline_compression():
    """Full pipeline should apply compression when enabled."""
    import tempfile
    cache_dir = Path(tempfile.mkdtemp())
    accelerator = token_speed.TokenSpeedAccelerator(
        enable_cache=False, enable_compression=True, enable_speculative=False
    )

    def mock_llm(prompt, model, temp, speculative=False):
        return f"response to: {prompt}"

    result = accelerator.process_prompt(
        "please can you tell me what is the weather today",
        "qwen3:1.7b",
        0.7,
        llm_call_fn=mock_llm,
    )
    assert result["compression_applied"] is True
    assert result["compressed_prompt_length"] < result["original_prompt_length"]
    assert "response to:" in result["response"]


def test_token_speed_accelerator_stats():
    """stats() should return combined statistics."""
    accelerator = token_speed.TokenSpeedAccelerator()
    stats = accelerator.stats()
    assert "cache_enabled" in stats
    assert "compression_enabled" in stats
    assert "speculative_enabled" in stats
    assert "cache" in stats
    assert "speculative" in stats


if __name__ == "__main__":
    test_token_cache_miss()
    test_token_cache_hit()
    test_token_cache_different_models()
    test_token_cache_stats()
    test_prompt_compression_removable_phrases()
    test_prompt_compression_shorten_map()
    test_prompt_compression_ratio()
    test_prompt_compression_does_not_overcompress()
    test_speculative_decoder_stats()
    test_tool_first_reflex_bypass()
    test_tool_first_no_match()
    test_full_pipeline_cache_hit()
    test_full_pipeline_compression()
    test_token_speed_accelerator_stats()
    print("All token_speed tests passed!")
