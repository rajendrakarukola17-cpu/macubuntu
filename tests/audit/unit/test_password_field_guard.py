"""
tests/audit/unit/test_password_field_guard.py

Phase 7d — Mutation Property 8: Password-field guard.

Verifies that writing_tools.writing_tool_transform refuses synthetic text
injection when a password/secret field is active, and attempts injection
only when no password field is focused.

No real network calls; mocks at automation.is_focused_password_field
and shutil.which boundaries.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import writing_tools  # noqa: E402


def _fake_llm(system_prompt: str, user_text: str) -> tuple[bool, str]:
    return True, "transformed text"


def test_password_field_blocks_injection():
    """When a password field is focused, ydotool injection must be refused."""
    with patch(
        "celina_pkg.automation.is_focused_password_field", return_value=True
    ), patch("celina_pkg.writing_tools.shutil.which", return_value=None):
        result = writing_tools.writing_tool_transform(
            mode="proofread",
            text="hello world",
            inject=True,
            llm_fn=_fake_llm,
        )
    assert "🔒 Injection blocked" in result, (
        f"Expected password-field injection block, got: {result[:200]}"
    )
    assert "ydotool injection unavailable" not in result


def test_non_password_field_attempts_injection():
    """When no password field is focused, injection should be attempted."""
    with patch(
        "celina_pkg.automation.is_focused_password_field", return_value=False
    ), patch("celina_pkg.writing_tools.shutil.which", return_value=None):
        result = writing_tools.writing_tool_transform(
            mode="proofread",
            text="hello world",
            inject=True,
            llm_fn=_fake_llm,
        )
    assert "🔒 Injection blocked" not in result, (
        f"Should not block injection for non-password field, got: {result[:200]}"
    )
    assert "ydotool injection unavailable" in result


def test_inject_false_never_types():
    """With inject=False, no injection path is taken regardless of field type."""
    with patch(
        "celina_pkg.automation.is_focused_password_field", return_value=True
    ):
        result = writing_tools.writing_tool_transform(
            mode="proofread",
            text="hello world",
            inject=False,
            llm_fn=_fake_llm,
        )
    assert "🔒 Injection blocked" not in result
    assert "ydotool" not in result
    assert "transformed text" in result
