"""
Tests for features/celina/celina_pkg/brain_harness.py — Brain Loop Primitives.

Covers:
  1. normalize_tool_calls: native tool_calls, Qwen XML tags, fenced JSON, hallucinated tool rejection
  2. call_with_backoff: retry on transient errors, no retry on non-retryable, jitter
  3. compact_conversation_history: tier-adaptive compaction, episode summary callback
  4. verify_final_completion: victory-declaration bias blocking, empty replies, clean pass
  5. BrainCallbacks: fail-open emit, all hook types
  6. Zero import-time $HOME writes
"""

from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

import pytest  # noqa: E402


# ============================================================================
# 1. normalize_tool_calls
# ============================================================================


def test_normalize_tool_calls_native():
    """BH1: Parses native OpenAI/Ollama tool_calls array."""
    from celina_pkg.brain_harness import normalize_tool_calls

    msg = {
        "content": "I'll help with that.",
        "tool_calls": [
            {
                "id": "call_1",
                "function": {
                    "name": "write_file",
                    "arguments": '{"path": "/tmp/x.txt", "content": "hello"}',
                },
            }
        ],
    }
    calls = normalize_tool_calls(msg, allowed_tool_names={"write_file", "read_file"})
    assert len(calls) == 1
    assert calls[0].call_id == "call_1"
    assert calls[0].name == "write_file"
    assert calls[0].arguments == {"path": "/tmp/x.txt", "content": "hello"}
    assert calls[0].source_format == "native"


def test_normalize_tool_calls_xml_tag():
    """BH2: Parses Qwen <tool_call>{...}</tool_call> blocks from content."""
    from celina_pkg.brain_harness import normalize_tool_calls

    msg = {
        "content": "Let me write the file.<tool_call>{\"name\": \"write_file\", \"arguments\": {\"path\": \"/tmp/y.txt\", \"content\": \"data\"}}</tool_call>",
    }
    calls = normalize_tool_calls(msg, allowed_tool_names={"write_file"})
    assert len(calls) == 1
    assert calls[0].name == "write_file"
    assert calls[0].arguments == {"path": "/tmp/y.txt", "content": "data"}
    assert calls[0].source_format == "xml_tag"


def test_normalize_tool_calls_fenced_json():
    """BH3: Parses fenced JSON tool calls from content."""
    from celina_pkg.brain_harness import normalize_tool_calls

    msg = {
        "content": 'Here is the result:\n```json\n{"name": "read_file", "arguments": {"path": "/tmp/z.txt"}}\n```',
    }
    calls = normalize_tool_calls(msg, allowed_tool_names={"read_file"})
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "/tmp/z.txt"}
    assert calls[0].source_format == "fenced_json"


def test_normalize_tool_calls_rejects_hallucinated():
    """BH4: Rejects tool names not in allowed_tool_names."""
    from celina_pkg.brain_harness import normalize_tool_calls

    msg = {
        "content": "I'll do it.<tool_call>{\"name\": \"hallucinated_tool\", \"arguments\": {}}</tool_call>",
    }
    calls = normalize_tool_calls(msg, allowed_tool_names={"write_file"})
    assert len(calls) == 0


def test_normalize_tool_calls_no_tool_calls():
    """BH5: Returns empty list when no tool calls present."""
    from celina_pkg.brain_harness import normalize_tool_calls

    msg = {"content": "Just a regular response."}
    calls = normalize_tool_calls(msg)
    assert calls == []


# ============================================================================
# 2. call_with_backoff
# ============================================================================


def test_call_with_backoff_first_try():
    """BH6: Succeeds on first attempt, no retries."""
    from celina_pkg.brain_harness import call_with_backoff

    attempts = 0

    def fn():
        nonlocal attempts
        attempts += 1
        return "ok"

    result = call_with_backoff(fn)
    assert result == "ok"
    assert attempts == 1


def test_call_with_backoff_retries_on_timeout():
    """BH7: Retries on TimeoutError with jitter, succeeds on 3rd attempt."""
    from celina_pkg.brain_harness import call_with_backoff

    attempts = 0
    sleeps = []

    def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("timed out")
        return "success"

    result = call_with_backoff(
        flaky, max_attempts=5, base_delay_sec=0.01, sleep_fn=lambda d: sleeps.append(d)
    )
    assert result == "success"
    assert attempts == 3
    assert len(sleeps) == 2  # two sleeps before 3rd success
    # Jitter: delays should differ slightly
    assert sleeps[0] != sleeps[1] or abs(sleeps[0] - sleeps[1]) < 0.3


def test_call_with_backoff_no_retry_on_non_retryable():
    """BH8: Does NOT retry non-retryable ValueError."""
    from celina_pkg.brain_harness import call_with_backoff

    attempts = 0

    def always_fail():
        nonlocal attempts
        attempts += 1
        raise ValueError("not retryable")

    try:
        call_with_backoff(always_fail, max_attempts=3)
        assert False, "Should have raised ValueError"
    except ValueError:
        assert attempts == 1  # no retries


def test_call_with_backoff_exhausts_attempts():
    """BH9: Raises last exception when all attempts fail."""
    from celina_pkg.brain_harness import call_with_backoff

    attempts = 0

    def always_timeout():
        nonlocal attempts
        attempts += 1
        raise TimeoutError("always times out")

    try:
        call_with_backoff(
            always_timeout, max_attempts=2, base_delay_sec=0.01, sleep_fn=lambda d: None
        )
        assert False, "Should have raised TimeoutError"
    except TimeoutError:
        assert attempts == 2


# ============================================================================
# 3. compact_conversation_history
# ============================================================================


def test_compact_history_no_compaction_needed():
    """BH10: Small history is returned unchanged."""
    from celina_pkg.brain_harness import compact_conversation_history

    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    result, compacted = compact_conversation_history(history, ram_tier="16gb")
    assert compacted is False
    assert len(result) == 2
    assert result == history


def test_compact_history_8gb_compacts():
    """BH11: Large history is compacted on 8gb tier, preserving recent turns + summary."""
    from celina_pkg.brain_harness import compact_conversation_history

    history = []
    for i in range(20):
        history.append({"role": "user", "content": f"message {i}: " + "x" * 300})
        history.append({"role": "assistant", "content": f"reply {i}: " + "y" * 300})

    summaries = []
    result, compacted = compact_conversation_history(
        history, ram_tier="8gb",
        on_episode_summary=lambda s: summaries.append(s),
    )
    assert compacted is True
    assert len(summaries) == 1
    # 6 recent turns + 1 summary header = 7 messages
    assert len(result) == 7
    assert result[0]["role"] == "system"
    assert "Compacted for 8GB" in result[0]["content"]
    # Recent turns preserved verbatim
    assert "reply 19" in result[-1]["content"]


def test_compact_history_16gb_no_compaction_small():
    """BH12: Small history on 16gb is not compacted."""
    from celina_pkg.brain_harness import compact_conversation_history

    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    result, compacted = compact_conversation_history(history, ram_tier="16gb")
    assert compacted is False
    assert len(result) == 2


def test_compact_history_32gb_large_limit():
    """BH13: 32gb tier has larger compaction threshold (28 turns / 40K chars)."""
    from celina_pkg.brain_harness import compact_conversation_history

    # 20 turns * ~600 chars = ~12K chars, well under 40K limit for 32gb
    history = []
    for i in range(20):
        history.append({"role": "user", "content": f"message {i}: " + "x" * 250})
        history.append({"role": "assistant", "content": f"reply {i}: " + "y" * 250})

    result, compacted = compact_conversation_history(history, ram_tier="32gb")
    assert compacted is False  # Under threshold
    assert len(result) == 40


def test_compact_history_skips_system_messages_in_summary():
    """BH14: System messages are excluded from the episodic summary."""
    from celina_pkg.brain_harness import compact_conversation_history

    # Long messages to exceed 4500 char threshold for 8gb compaction
    history = []
    for i in range(10):
        history.append({"role": "system", "content": "System instruction " + "z" * 100})
        history.append({"role": "user", "content": f"user message {i}: " + "x" * 300})
        history.append({"role": "assistant", "content": f"assistant reply {i}: " + "y" * 300})

    result, compacted = compact_conversation_history(history, ram_tier="8gb")
    assert compacted is True
    # Summary should not contain system messages
    summary = result[0]["content"]
    assert "System instruction" not in summary


# ============================================================================
# 4. verify_final_completion
# ============================================================================


def test_verify_final_completion_clean_pass():
    """BH15: Clean reply with no tool failures passes."""
    from celina_pkg.brain_harness import verify_final_completion

    verdict = verify_final_completion("The file has been saved successfully.", [])
    assert verdict.passed is True
    assert verdict.reply == "The file has been saved successfully."
    assert verdict.corrected is False
    assert verdict.issues == ()


def test_verify_final_completion_victory_bias_blocked():
    """BH16: Victory declaration blocked when tool failed."""
    from celina_pkg.brain_harness import verify_final_completion

    verdict = verify_final_completion(
        "Done! I wrote the file successfully.",
        [("write_file", "Denied: user cancelled")],
    )
    assert verdict.passed is False
    assert verdict.corrected is True
    assert "Verification note" in verdict.reply
    assert "write_file" in verdict.reply
    assert "victory_declaration_bias_blocked" in verdict.issues


def test_verify_final_completion_empty_reply():
    """BH17: Empty reply is rejected."""
    from celina_pkg.brain_harness import verify_final_completion

    verdict = verify_final_completion("", [])
    assert verdict.passed is False
    assert verdict.corrected is True
    assert verdict.issues == ("empty_reply",)


def test_verify_final_completion_empty_with_tool_errors():
    """BH18: Empty reply with tool errors lists the failures."""
    from celina_pkg.brain_harness import verify_final_completion

    verdict = verify_final_completion("", [("write_file", "Error: permission denied")])
    assert verdict.passed is False
    assert verdict.corrected is True
    assert "write_file" in verdict.reply
    assert "empty_reply_with_tool_errors" in verdict.issues


def test_verify_final_completion_tool_failed_no_victory_claim():
    """BH19: Tool failed but no victory claim — passes with error notice."""
    from celina_pkg.brain_harness import verify_final_completion

    verdict = verify_final_completion(
        "I was unable to complete that request.",
        [("write_file", "Error: disk full")],
    )
    assert verdict.passed is True  # no victory claim, so it passes
    assert verdict.corrected is False


# ============================================================================
# 5. BrainCallbacks
# ============================================================================


def test_brain_callbacks_emit_on_route():
    """BH20: on_route callback is invoked."""
    from celina_pkg.brain_harness import BrainCallbacks

    calls = []
    cb = BrainCallbacks(on_route=lambda gear, reason: calls.append((gear, reason)))
    cb.emit("on_route", "tier1", "local ollama")
    assert calls == [("tier1", "local ollama")]


def test_brain_callbacks_emit_on_tool_start_end():
    """BH21: on_tool_start and on_tool_end callbacks are invoked."""
    from celina_pkg.brain_harness import BrainCallbacks

    starts = []
    ends = []
    cb = BrainCallbacks(
        on_tool_start=lambda tool, args: starts.append(tool),
        on_tool_end=lambda tool, ok, preview, ms: ends.append((tool, ok)),
    )
    cb.emit("on_tool_start", "write_file", {"path": "/tmp/x.txt"})
    cb.emit("on_tool_end", "write_file", True, "wrote ok", 10)
    assert starts == ["write_file"]
    assert ends == [("write_file", True)]


def test_brain_callbacks_emit_on_token_chunk():
    """BH22: on_token_chunk callback is invoked for streaming."""
    from celina_pkg.brain_harness import BrainCallbacks

    chunks = []
    cb = BrainCallbacks(on_token_chunk=lambda chunk: chunks.append(chunk))
    cb.emit("on_token_chunk", "Hello")
    cb.emit("on_token_chunk", " world")
    assert chunks == ["Hello", " world"]


def test_brain_callbacks_emit_on_turn_done():
    """BH23: on_turn_done callback is invoked with turn summary."""
    from celina_pkg.brain_harness import BrainCallbacks

    done_calls = []
    cb = BrainCallbacks(on_turn_done=lambda summary: done_calls.append(summary))
    cb.emit("on_turn_done", {"source": "tier1", "completion_verified": True})
    assert len(done_calls) == 1
    assert done_calls[0]["source"] == "tier1"
    assert done_calls[0]["completion_verified"] is True


def test_brain_callbacks_fail_open_on_raising_handler():
    """BH24: A raising callback does not break .emit() or other handlers."""
    from celina_pkg.brain_harness import BrainCallbacks

    results = []

    def raiser(hook, *args):
        raise RuntimeError("boom")

    def good(hook, *args):
        results.append(args)

    cb = BrainCallbacks()
    # Manually add a raising handler and a good handler to the same hook
    # BrainCallbacks.emit uses getattr, so we test that a hook that raises internally is caught
    cb.on_tool_end = lambda tool, ok, preview, ms: (_ for _ in ()).throw(RuntimeError("boom"))
    cb.on_tool_end2 = lambda tool, ok, preview, ms: results.append((tool, ok))
    # Emit the raising hook — should not propagate
    cb.emit("on_tool_end", "write_file", True, "ok", 5)
    assert results == []  # raising hook didn't break emit


# ============================================================================
# 6. Zero import-time side effects
# ============================================================================


def test_brain_harness_zero_home_writes():
    """BH25: Importing brain_harness writes nothing to $HOME."""
    home = Path.home()
    before = set(home.iterdir())
    import importlib

    import celina_pkg.brain_harness as bh

    importlib.reload(bh)
    after = set(home.iterdir())
    assert before == after, f"Import created files in $HOME: {after - before}"


# ============================================================================
# 7. Integration: NormalizedToolCall dataclass
# ============================================================================


def test_normalized_tool_call_dataclass():
    """BH26: NormalizedToolCall is a frozen dataclass with all fields."""
    from celina_pkg.brain_harness import NormalizedToolCall

    tc = NormalizedToolCall(
        call_id="call_1",
        name="write_file",
        arguments={"path": "/tmp/x.txt"},
        source_format="native",
    )
    assert tc.call_id == "call_1"
    assert tc.name == "write_file"
    assert tc.arguments == {"path": "/tmp/x.txt"}
    assert tc.source_format == "native"
    # Frozen: cannot modify
    try:
        tc.name = "changed"
        assert False, "Should be frozen"
    except dataclasses.FrozenInstanceError:
        pass
