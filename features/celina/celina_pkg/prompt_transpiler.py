"""Celina Modular Prompt Transpiler (`prompt_transpiler.py`)

Compiles system prompts from discrete, ordered layers in strict
STATIC-PREFIX-FIRST order to maximize local Ollama / llama-server KV-cache hits:
  1. Base Persona & Channel Layer (static per session)
  2. Security & Capability Policy Layer (static per manifest SHA)
  3. Domain / FORST Logical Anchor Layer (static per objective type)
  4. Active Tool Summary Layer (semi-static)
  5. Volatile Runtime Context Layer (recalled memory, sensory frames, failure hints — ALWAYS LAST)

Zero third-party dependencies (pure Python 3.13 stdlib).
Zero $HOME filesystem mutations at import time.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Iterable


@dataclasses.dataclass(frozen=True)
class PromptLayer:
    name: str
    content: str
    volatile: bool = False  # volatile layers are ALWAYS sorted to the tail


@dataclasses.dataclass(frozen=True)
class CompiledPrompt:
    text: str
    static_prefix_hash: str
    full_hash: str
    layer_names: tuple[str, ...]


def transpile_prompt(layers: Iterable[PromptLayer], *, max_chars: int | None = None) -> CompiledPrompt:
    """Deterministically compile static layers first, volatile layers last.

    All non-volatile layers are placed before all volatile layers, preserving
    their relative order within each group. This guarantees that the static
    prefix (persona, safety policy, FORST anchors) never shifts when volatile
    state (memory, sensory frames, timestamps) changes — maximizing KV-cache
    prefix hits on local inference engines.

    Args:
        layers: Iterable of PromptLayer instances.
        max_chars: If provided and the full blob exceeds this, truncate from
            the volatile tail while preserving the static prefix.

    Returns:
        A CompiledPrompt with `static_prefix_hash` (hash of static layers only)
        and `full_hash` (hash of all layers).

    """
    layer_list = list(layers)
    static_layers = [l for l in layer_list if not l.volatile and (l.content or "").strip()]
    volatile_layers = [l for l in layer_list if l.volatile and (l.content or "").strip()]
    ordered = static_layers + volatile_layers

    static_blob = "\n\n".join(l.content.strip() for l in static_layers)
    full_blob = "\n\n".join(l.content.strip() for l in ordered)

    if max_chars is not None and len(full_blob) > max_chars:
        # Preserve static safety/persona prefix; trim only from the volatile tail
        keep = max(len(static_blob), int(max_chars))
        full_blob = full_blob[:keep]

    return CompiledPrompt(
        text=full_blob,
        static_prefix_hash=hashlib.sha256(static_blob.encode("utf-8")).hexdigest()[:16],
        full_hash=hashlib.sha256(full_blob.encode("utf-8")).hexdigest()[:16],
        layer_names=tuple(l.name for l in ordered),
    )


__all__ = [
    "CompiledPrompt",
    "PromptLayer",
    "transpile_prompt",
]
