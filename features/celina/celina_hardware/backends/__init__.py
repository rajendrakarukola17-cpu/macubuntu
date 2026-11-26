"""Hardware inference backend adapters (Vulkan, ROCm, OpenVINO NPU)."""

from .openvino_npu import OpenVINONPUBackend
from .rocm import ROCmBackend
from .vulkan import VulkanBackend

__all__ = ["OpenVINONPUBackend", "ROCmBackend", "VulkanBackend"]
