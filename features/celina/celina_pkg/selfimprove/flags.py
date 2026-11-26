# Copyright 2026 Celina contributors.
"""Default-off self-improvement flags with a fail-closed master switch."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

SUBFLAGS = ("verifiers", "eval_harness", "memory", "loop", "optimize", "patcher")
_TRUE = frozenset({"1", "true", "on", "yes"})


@dataclass(frozen=True, slots=True)
class SelfImproveFlags:
    """Immutable switches; a disabled master always clears all subflags."""

    enabled: bool = False
    verifiers: bool = False
    eval_harness: bool = False
    memory: bool = False
    loop: bool = False
    optimize: bool = False
    patcher: bool = False

    def __post_init__(self) -> None:
        """Validate types and enforce the master switch."""
        for field in fields(self):
            if type(getattr(self, field.name)) is not bool:
                message = f"{field.name} must be a bool"
                raise ValueError(message)
        if not self.enabled:
            for name in SUBFLAGS:
                object.__setattr__(self, name, False)


def _switch(name: str, config: Mapping[str, object], env: Mapping[str, str]) -> bool:
    env_name = f"SELFIMPROVE_{name.upper()}"
    if env_name in env:
        return env[env_name].strip().lower() in _TRUE
    return config.get(name) is True


def load_flags(
    config: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
) -> SelfImproveFlags:
    """Load switches with environment precedence and explicit opt-in only."""
    config = {} if config is None else config
    env = os.environ if env is None else env
    if not _switch("enabled", config, env):
        return SelfImproveFlags()
    return SelfImproveFlags(enabled=True, **{name: _switch(name, config, env) for name in SUBFLAGS})


def is_layer_enabled(flags: SelfImproveFlags, layer: str) -> bool:
    """Return whether a known subflag and the master are both enabled."""
    return flags.enabled and layer in SUBFLAGS and bool(getattr(flags, layer))
