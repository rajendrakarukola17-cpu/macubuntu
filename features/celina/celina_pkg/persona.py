"""Celina Dual-Channel Persona Engine (`persona.py`).

Builds system prompts for the LLM with per-style and per-channel variation.
Supports three styles: "celina" (default), "jarvis", "minimal".
Unknown styles fall back to "celina".

Voice channel adds strict constraints: 1-3 spoken sentences, zero markdown,
zero emojis, zero bullet points, zero raw URLs/paths.

Always includes action-honesty (never claim completion before verified tool
output) and zero-silent-failure rules (never skip/report errors silently).

Zero import-time side effects: no $HOME writes, no subprocess spawning.
"""

from __future__ import annotations

_STYLES = ("celina", "jarvis", "minimal")
_CHANNELS = ("text", "voice")


def _resolve_style(style: Optional[str]) -> str:
    """Normalize style name; unknown falls back to 'celina'."""
    s = (style or "celina").strip().lower()
    return s if s in _STYLES else "celina"


def _resolve_channel(channel: Optional[str]) -> str:
    """Normalize channel name; unknown falls back to 'text'."""
    c = (channel or "text").strip().lower()
    return c if c in _CHANNELS else "text"


def _style_prefix(style: str, name: str, nickname: str, honorific: str) -> str:
    """Style-specific persona prefix."""
    if style == "jarvis":
        h = f", {honorific}" if honorific else ""
        return (
            f"You are JARVIS{h}, a formal, precise, protocol-driven AI assistant. "
            f"Address the user as '{honorific or name}'. "
            f"Speak with measured, British-inflected formality. Prioritize accuracy, "
            f"brevity, and operational clarity. Never use slang or casual contractions "
            f"unless the user does first."
        )
    if style == "minimal":
        return "You are a concise, direct AI assistant. Answer in as few words as possible."
    # celina (default)
    n = f" ({nickname})" if nickname else ""
    return (
        f"You are Celina{n}, a helpful, privacy-first AI assistant running locally on "
        f"the user's machine. You are warm, clear, and honest. You never pretend to "
        f"capabilities you don't have, and you always explain what you're doing and why."
    )


def _voice_constraints() -> str:
    """Strict voice-channel output constraints."""
    return (
        "\n\nVOICE CHANNEL CONSTRAINTS (mandatory):\n"
        "1. Output exactly 1-3 spoken sentences maximum.\n"
        "2. Zero markdown (no bold, italics, code blocks, headers).\n"
        "3. Zero emojis or emoticons.\n"
        "4. Zero bullet points or numbered lists.\n"
        "5. Zero raw URLs or file paths.\n"
        "6. Speak naturally — as if talking to someone in the room.\n"
    )


def _action_honesty() -> str:
    """Action-honesty rule: never claim completion before verified tool output."""
    return (
        "\n\nACTION HONESTY (mandatory):\n"
        "1. Never claim a task is complete until you have observed verified tool output.\n"
        "2. If a tool call fails or returns an error, report it honestly — do not pretend success.\n"
        "3. If you are unsure about something, say so rather than guessing.\n"
        "4. Always distinguish between what you have done and what you plan to do.\n"
    )


def _zero_silent_failure() -> str:
    """Zero-silent-failure rule: always report failures honestly."""
    return (
        "\n\nZERO SILENT FAILURE (mandatory):\n"
        "1. Never silently skip a failed tool, connection, or operation.\n"
        "2. Always surface errors to the user with a clear, plain-English explanation.\n"
        "3. Never say 'I did X' when X was not verified as completed.\n"
        "4. If a prerequisite is missing, state what is missing and how to obtain it.\n"
    )


def build_system_prompt(
    name: str,
    *,
    nickname: Optional[str] = None,
    style: str = "celina",
    channel: str = "text",
    honorific: Optional[str] = None,
    language_rule: Optional[str] = None,
) -> str:
    """Build a dual-channel system prompt for the LLM.

    Args:
        name: Assistant name (e.g. "Celina").
        nickname: Optional nickname (e.g. "Celly").
        style: Persona style — "celina", "jarvis", or "minimal" (falls back to "celina").
        channel: Output channel — "text" or "voice" (voice adds strict constraints).
        honorific: Optional honorific for jarvis style (e.g. "Sir", "Ma'am").
        language_rule: Optional language instruction (e.g. "Reply in Telugu.").

    Returns:
        A complete system prompt string.

    """
    resolved_style = _resolve_style(style)
    resolved_channel = _resolve_channel(channel)

    parts = [_style_prefix(resolved_style, name, nickname or "", honorific or "")]

    if language_rule:
        parts.append(f"\n\nLANGUAGE: {language_rule.strip()}")

    parts.append(_action_honesty())
    parts.append(_zero_silent_failure())

    if resolved_channel == "voice":
        parts.append(_voice_constraints())

    return "".join(parts)
