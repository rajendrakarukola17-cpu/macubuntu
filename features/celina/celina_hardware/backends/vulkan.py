"""Vulkan GPU inference backend profile (Intel Arc/Iris Xe & AMD RDNA3+ non-Strix-Halo)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..models import CapabilityStatus, HardwareActionResult


class VulkanBackend:
    name = "vulkan"
    override_conf = "system/ollama/vulkan-igpu.override.conf"
    conflicts_with = ("rocm",)

    _GFX_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

    @classmethod
    def health_check(
        cls,
        *,
        dev_root: str | Path = Path("/dev"),
    ) -> tuple[CapabilityStatus, list[str], list[str]]:
        """Verify Vulkan availability: ICD JSON files, renderD nodes, vulkaninfo.

        Returns (status, devices, reasons).
        """
        dev_root = Path(dev_root)
        nodes: list[str] = []
        reasons: list[str] = []

        dri = dev_root / "dri"
        render_nodes: list[str] = []
        if dri.is_dir():
            for p in sorted(dri.glob("renderD*")):
                if p.is_char_device() and os.access(p, os.R_OK | os.W_OK):
                    render_nodes.append(str(p))
                    nodes.append(str(p))
        if not render_nodes:
            reasons.append("/dev/dri/renderD* missing or not accessible")

        icd_dir = Path("/usr/share/vulkan/icd.d")
        icds: list[str] = []
        if icd_dir.is_dir():
            icds = [f.name for f in icd_dir.glob("*.json")]
        if not icds:
            reasons.append("No Vulkan ICD JSON files in /usr/share/vulkan/icd.d/")

        if shutil.which("vulkaninfo"):
            try:
                res = subprocess.run(
                    ["vulkaninfo", "--summary"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if res.returncode != 0:
                    reasons.append("vulkaninfo --summary failed")
            except (OSError, subprocess.TimeoutExpired) as exc:
                reasons.append(f"vulkaninfo probe failed: {exc}")
        else:
            reasons.append("vulkaninfo not installed")

        if reasons:
            status = CapabilityStatus.NOT_INSTALLED if not nodes else CapabilityStatus.FAILED
            return status, nodes, reasons
        return CapabilityStatus.HEALTHY, nodes, []

    @classmethod
    def _validate_gfx_version(cls, gfx_version: str) -> str:
        if gfx_version and not cls._GFX_VERSION_RE.match(gfx_version):
            raise ValueError(
                f"Invalid gfx_version {gfx_version!r}; expected format like '11.5.1'"
            )
        return gfx_version

    @classmethod
    def get_env(cls, *, gfx_version: str = "") -> dict[str, str]:
        """Return Vulkan environment variables.

        Refuses gfx1151 (Strix Halo) — known Qwen3.5 Vulkan hang; use ROCm instead.
        """
        cls._validate_gfx_version(gfx_version)
        if gfx_version.replace(".", "") == "1151":
            raise RuntimeError(
                "Vulkan refused on gfx1151 (Strix Halo): known Qwen3.5 Vulkan hang; use ROCm backend."
            )
        return {
            "OLLAMA_VULKAN": "1",
            "OLLAMA_FLASH_ATTENTION": "1",
            "OLLAMA_KV_CACHE_TYPE": "q8_0",
        }

    @classmethod
    def describe(
        cls,
        *,
        gpu_model: str = "",
        gfx_version: str = "",
    ) -> dict[str, Any]:
        cls._validate_gfx_version(gfx_version)
        return {
            "backend": cls.name,
            "gpu_model": gpu_model,
            "conflicts_with": cls.conflicts_with,
            "env": cls.get_env(gfx_version=gfx_version),
            "override_conf": cls.override_conf,
            "endpoint": "http://127.0.0.1:11434",
        }

    @classmethod
    def verify_applied(cls) -> HardwareActionResult:
        """Verify Vulkan environment was applied: OLLAMA_VULKAN=1.

        Reads effective systemd environment and asserts Vulkan is enabled and no
        conflicting ROCm variables remain.
        """
        requested = {
            "env": cls.get_env(),
            "override_conf": cls.override_conf,
        }
        observed: dict[str, Any] = {}

        vulkan_enabled = False
        hsa_present = False
        try:
            res = subprocess.run(
                ["systemctl", "show", "ollama", "-p", "Environment"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            env_text = res.stdout or ""
            observed["systemctl_environment"] = env_text.strip()
            vulkan_enabled = "OLLAMA_VULKAN=1" in env_text
            hsa_present = "HSA_OVERRIDE_GFX_VERSION" in env_text
        except (OSError, subprocess.TimeoutExpired) as exc:
            observed["systemctl_error"] = str(exc)

        problems: list[str] = []
        if not vulkan_enabled:
            problems.append("OLLAMA_VULKAN=1 not in ollama.service environment")
        if hsa_present:
            problems.append("HSA_OVERRIDE_GFX_VERSION still present (conflicts with Vulkan)")

        if problems:
            return HardwareActionResult(
                ok=False,
                verified=False,
                message="Vulkan environment not fully applied: " + "; ".join(problems),
                requested=requested,
                observed=observed,
                returncode=1,
            )

        return HardwareActionResult(
            ok=True,
            verified=True,
            message="Vulkan environment verified",
            requested=requested,
            observed=observed,
            returncode=0,
        )


__all__ = ["VulkanBackend"]
