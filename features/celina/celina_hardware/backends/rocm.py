"""AMD ROCm GPU backend profile (mandatory for Strix Halo gfx1151 where Vulkan hangs Qwen3.5)."""

from __future__ import annotations

from typing import Any


class ROCmBackend:
    name = "rocm"
    override_conf = "system/ollama/rocm-gfx1151.override.conf"

    @classmethod
    def get_env(cls, *, gfx_version: str = "11.5.1") -> dict[str, str]:
        # Explicitly omit OLLAMA_VULKAN so gfx1151 never falls into the Qwen3.5 Vulkan hang
        return {
            "OLLAMA_FLASH_ATTENTION": "1",
            "HSA_OVERRIDE_GFX_VERSION": gfx_version,
            "OLLAMA_KV_CACHE_TYPE": "q8_0",
        }

    @classmethod
    def describe(cls, gpu_model: str = "", *, gfx_version: str = "11.5.1") -> dict[str, Any]:
        return {
            "backend": cls.name,
            "gpu_model": gpu_model,
            "env": cls.get_env(gfx_version=gfx_version),
            "override_conf": cls.override_conf,
            "endpoint": "http://127.0.0.1:11434",
        }
