"""Hardware inference backend adapters (Vulkan, ROCm, OpenVINO NPU)."""

from typing import Any, Protocol, runtime_checkable

from .openvino_npu import OpenVINONPUBackend
from .rocm import ROCmBackend
from .vulkan import VulkanBackend


@runtime_checkable
class InferenceBackend(Protocol):
    """Common contract every inference backend must satisfy.

    Provides: name, conflicts_with, health_check(), get_env(), describe(),
    verify_applied().  Backends that deviate from this contract fail CI.
    """

    name: str
    override_conf: str
    conflicts_with: tuple[str, ...]

    def health_check(self) -> tuple[Any, list[str], list[str]]: ...
    def get_env(self) -> dict[str, str]: ...
    def describe(self) -> dict[str, Any]: ...
    def verify_applied(self) -> Any: ...


__all__ = [
    "OpenVINONPUBackend",
    "ROCmBackend",
    "VulkanBackend",
    "InferenceBackend",
]
