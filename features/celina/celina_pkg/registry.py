"""Celina Pluggable Component Registries (`registry.py`)

Defines strict ABC contracts and decorator-driven runtime registries for
InferenceEngine and MemoryBackend so swapping Ollama -> llama-server UDS ->
OpenVINO NPU -> vLLM requires zero edits to router or autonomy core code.

Zero third-party dependencies (pure Python 3.13 stdlib).
Zero $HOME filesystem mutations at import time.
"""

from __future__ import annotations

import abc
import dataclasses
import threading
from collections.abc import Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclasses.dataclass(frozen=True)
class InferenceRequest:
    messages: list[dict[str, Any]]
    model: str
    tools: tuple[dict[str, Any], ...] = ()
    temperature: float = 0.2
    max_tokens: int = 1024
    timeout_sec: float = 30.0


@dataclasses.dataclass(frozen=True)
class InferenceResponse:
    ok: bool
    content: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    engine: str = ""
    model: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""


class InferenceEngine(abc.ABC):
    """Abstract contract for any local or opt-in cloud LLM backend."""

    engine_name: str = "abstract"

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True if this engine's daemon/socket/endpoint is reachable."""

    @abc.abstractmethod
    def generate(self, req: InferenceRequest) -> InferenceResponse:
        """Execute one inference turn and return a structured InferenceResponse."""

    def unload_models(self) -> int:
        """Optional RAM/VRAM release hook (called by Turbo Mode / PSI governor).

        Returns the number of models unloaded.
        """
        return 0


class MemoryBackend(abc.ABC):
    """Abstract contract for hybrid semantic/relational memory stores."""

    backend_name: str = "abstract"

    @abc.abstractmethod
    def remember(self, text: str, *, importance: float = 0.5, meta: dict[str, Any] | None = None) -> str:
        """Persist a memory entry and return its ID."""

    @abc.abstractmethod
    def recall(self, query: str, *, k: int = 5) -> list[dict[str, Any]]:
        """Retrieve top-k relevant memories."""


class ComponentRegistry(Generic[T]):
    """Thread-safe decorator registry (`@EngineRegistry.register("ollama")`)."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._lock = threading.RLock()
        self._factories: dict[str, Callable[..., T]] = {}

    def register(self, name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        key = (name or "").strip().lower()
        if not key:
            raise ValueError(f"{self.kind} registry name must not be empty")

        def _decorator(cls_or_fn: Callable[..., T]) -> Callable[..., T]:
            with self._lock:
                self._factories[key] = cls_or_fn
            return cls_or_fn

        return _decorator

    def create(self, name: str, *args: Any, **kwargs: Any) -> T:
        key = (name or "").strip().lower()
        with self._lock:
            factory = self._factories.get(key)
        if factory is None:
            available = sorted(self._factories.keys())
            raise KeyError(f"Unknown {self.kind} '{name}'. Registered: {available}")
        return factory(*args, **kwargs)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())


EngineRegistry: ComponentRegistry[InferenceEngine] = ComponentRegistry("InferenceEngine")
MemoryRegistry: ComponentRegistry[MemoryBackend] = ComponentRegistry("MemoryBackend")

__all__ = [
    "ComponentRegistry",
    "EngineRegistry",
    "InferenceEngine",
    "InferenceRequest",
    "InferenceResponse",
    "MemoryBackend",
    "MemoryRegistry",
]
