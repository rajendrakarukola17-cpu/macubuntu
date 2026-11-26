"""Vulkan GPU inference backend profile (Intel Arc/Iris Xe & AMD RDNA3+ non-Strix-Halo)."""

from __future__ import annotations

from typing import Any


class VulkanBackend:
    name = "vulkan"
    override_conf = "system/ollama/vulkan-igpu.override.conf"

    @classmethod
    def get_env(cls) -> dict[str, str]:
        return {
            "OLLAMA_VULKAN": "1",
            "OLLAMA_FLASH_ATTENTION": "1",
            "OLLAMA_KV_CACHE_TYPE": "q8_0",
        }

    @classmethod
    def describe(cls, gpu_model: str = "") -> dict[str, Any]:
        return {
            "backend": cls.name,
            "gpu_model": gpu_model,
            "env": cls.get_env(),
            "override_conf": cls.override_conf,
            "endpoint": "http://127.0.0.1:11434",
        }
