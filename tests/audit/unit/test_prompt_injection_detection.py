"""
tests/audit/unit/test_prompt_injection_detection.py

Task 7a-7b — Prompt injection detection rate over the injection corpus.

Runs evidence.detect_prompt_injection and router._sanitize_tool_prompt_content
over all 56 hostile cases + 5 safe controls. Reports detection rate and lists
every miss. Safe controls must produce zero false positives.

These tests FAIL (red) until detection patterns are expanded to cover the corpus.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import evidence, router  # noqa: E402

CORPUS_PATH = Path(__file__).resolve().parents[3] / "tests" / "audit" / "data" / "injection_corpus.json"


def _load_corpus() -> dict:
    with open(CORPUS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _hostile_cases():
    data = _load_corpus()
    for case in data["cases"]:
        if case["expected"] == "blocked":
            yield case["id"], case["category"], case["payload"]


def _safe_controls():
    data = _load_corpus()
    for case in data["safe_controls"]:
        yield case["id"], case["category"], case["payload"]


# ---------------------------------------------------------------------------
# evidence.detect_prompt_injection — must flag every hostile case
# ---------------------------------------------------------------------------


def test_evidence_detects_all_hostile_cases():
    """Every hostile injection case must be detected by evidence.detect_prompt_injection."""
    misses = []
    for case_id, category, payload in _hostile_cases():
        results = evidence.detect_prompt_injection(payload)
        if not results:
            misses.append((case_id, category, payload[:90]))
    assert not misses, (
        f"evidence.detect_prompt_injection MISSED {len(misses)}/56 hostile cases:\n"
        + "\n".join(f"  {cid} ({cat}): {p}" for cid, cat, p in misses)
    )


def test_evidence_zero_false_positives_on_safe_controls():
    """Safe controls must NOT be flagged by evidence.detect_prompt_injection."""
    fps = []
    for case_id, category, payload in _safe_controls():
        results = evidence.detect_prompt_injection(payload)
        if results:
            fps.append((case_id, category, results, payload[:90]))
    assert not fps, (
        f"evidence.detect_prompt_injection FALSE POSITIVES on {len(fps)} safe controls:\n"
        + "\n".join(f"  {cid} ({cat}): {labels} - {p}" for cid, cat, labels, p in fps)
    )


# ---------------------------------------------------------------------------
# router._sanitize_tool_prompt_content — must neutralize every hostile case
# ---------------------------------------------------------------------------


def test_router_sanitizes_all_hostile_cases():
    """Every hostile injection case must be neutralized by _sanitize_tool_prompt_content."""
    misses = []
    for case_id, category, payload in _hostile_cases():
        result = router._sanitize_tool_prompt_content(payload)
        if "[PROMPT_INJECTION_PATTERN_REDACTED]" not in result:
            misses.append((case_id, category, payload[:90]))
    assert not misses, (
        f"router._sanitize_tool_prompt_content MISSED {len(misses)}/56 hostile cases:\n"
        + "\n".join(f"  {cid} ({cat}): {p}" for cid, cat, p in misses)
    )


def test_router_zero_false_positives_on_safe_controls():
    """Safe controls must NOT be neutralized by _sanitize_tool_prompt_content."""
    fps = []
    for case_id, category, payload in _safe_controls():
        result = router._sanitize_tool_prompt_content(payload)
        if "[PROMPT_INJECTION_PATTERN_REDACTED]" in result:
            fps.append((case_id, category, payload[:90]))
    assert not fps, (
        f"router._sanitize_tool_prompt_content FALSE POSITIVES on {len(fps)} safe controls:\n"
        + "\n".join(f"  {cid} ({cat}): {p}" for cid, cat, p in fps)
    )


# ---------------------------------------------------------------------------
# Detection rate reporting (informational — captures current state)
# ---------------------------------------------------------------------------


def test_detection_rate_evidence(capsys):
    """Report evidence.detect_prompt_injection detection rate over the corpus."""
    data = _load_corpus()
    hostile = [c for c in data["cases"] if c["expected"] == "blocked"]
    detected = 0
    for case in hostile:
        if evidence.detect_prompt_injection(case["payload"]):
            detected += 1
    rate = detected / len(hostile) * 100
    with capsys.disabled():
        print(f"\n  evidence.detect_prompt_injection: {detected}/{len(hostile)} = {rate:.1f}%")
    # This test documents the current rate; it does not fail on low rate.
    assert detected >= 0


def test_detection_rate_router(capsys):
    """Report router._sanitize_tool_prompt_content neutralization rate over the corpus."""
    data = _load_corpus()
    hostile = [c for c in data["cases"] if c["expected"] == "blocked"]
    neutralized = 0
    for case in hostile:
        result = router._sanitize_tool_prompt_content(case["payload"])
        if "[PROMPT_INJECTION_PATTERN_REDACTED]" in result:
            neutralized += 1
    rate = neutralized / len(hostile) * 100
    with capsys.disabled():
        print(f"\n  router._sanitize_tool_prompt_content: {neutralized}/{len(hostile)} = {rate:.1f}%")
    assert neutralized >= 0
