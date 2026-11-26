"""AMD ROCm GPU backend profile (mandatory for Strix Halo gfx1151 where Vulkan hangs Qwen3.5)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..models import CapabilityStatus, HardwareActionResult


class ROCmBackend:
    name = "rocm"
    override_conf = "system/ollama/rocm-gfx1151.override.conf"
    conflicts_with = ("vulkan",)

    _GFX_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

    @classmethod
    def health_check(
        cls,
        *,
        dev_root: str | Path = Path("/dev"),
    ) -> tuple[CapabilityStatus, list[str], list[str]]:
        """Verify ROCm availability: /dev/kfd, /dev/dri/render*, rocminfo.

        Returns (status, devices, reasons).
        """
        dev_root = Path(dev_root)
        nodes: list[str] = []
        reasons: list[str] = []

        kfd = dev_root / "kfd"
        if not kfd.exists():
            reasons.append("/dev/kfd missing (ROCm kernel driver not loaded?)")
        elif not os.access(kfd, os.R_OK | os.W_OK):
            reasons.append("/dev/kfd: EACCES (user not in 'render'/'video' group?)")
        else:
            nodes.append(str(kfd))

        dri = dev_root / "dri"
        render_nodes: list[str] = []
        if dri.is_dir():
            for p in sorted(dri.glob("renderD*")):
                if p.is_char_device() and os.access(p, os.R_OK | os.W_OK):
                    render_nodes.append(str(p))
                    nodes.append(str(p))
        if not render_nodes:
            reasons.append("/dev/dri/renderD* missing or not accessible")

        if shutil.which("rocminfo"):
            try:
                res = subprocess.run(
                    ["rocminfo"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if res.returncode != 0:
                    reasons.append("rocminfo failed (ROCm runtime may be broken)")
                elif "gfx" not in res.stdout:
                    reasons.append("rocminfo reports no GFX targets")
            except (OSError, subprocess.TimeoutExpired) as exc:
                reasons.append(f"rocminfo probe failed: {exc}")
        else:
            reasons.append("rocminfo not installed")

        if reasons:
            status = CapabilityStatus.NOT_INSTALLED if not nodes else CapabilityStatus.FAILED
            return status, nodes, reasons
        return CapabilityStatus.HEALTHY, nodes, []

    @classmethod
    def _validate_gfx_version(cls, gfx_version: str) -> str:
        if not cls._GFX_VERSION_RE.match(gfx_version):
            raise ValueError(
                f"Invalid gfx_version {gfx_version!r}; expected format like '11.5.1'"
            )
        return gfx_version

    @classmethod
    def get_env(cls, *, gfx_version: str = "11.5.1") -> dict[str, str]:
        """Return ROCm environment variables.

        OLLAMA_VULKAN is explicitly set to "0" to negate any lingering Vulkan
        profile from a previous backend switch (absence of a key is not negation).
        """
        cls._validate_gfx_version(gfx_version)
        return {
            "OLLAMA_VULKAN": "0",
            "OLLAMA_FLASH_ATTENTION": "1",
            "HSA_OVERRIDE_GFX_VERSION": gfx_version,
            "OLLAMA_KV_CACHE_TYPE": "q8_0",
        }

    @classmethod
    def describe(
        cls,
        *,
        gpu_model: str = "",
        gfx_version: str = "11.5.1",
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
    def verify_applied(
        cls,
        *,
        gfx_version: str = "11.5.1",
    ) -> HardwareActionResult:
        """Verify ROCm environment was applied: OLLAMA_VULKAN=0, HSA_OVERRIDE set.

        Reads effective systemd environment and asserts no conflicting Vulkan
        variables remain.
        """
        cls._validate_gfx_version(gfx_version)
        requested = {
            "env": cls.get_env(gfx_version=gfx_version),
            "override_conf": cls.override_conf,
        }
        observed: dict[str, Any] = {}

        vulkan_found = False
        hsa_found = False
        try:
            res = subprocess.run(
                ["systemctl", "show", "ollama", "-p", "Environment"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            env_text = res.stdout or ""
            observed["systemctl_environment"] = env_text.strip()
            vulkan_found = "OLLAMA_VULKAN=1" in env_text
            hsa_found = f"HSA_OVERRIDE_GFX_VERSION={gfx_version}" in env_text
        except (OSError, subprocess.TimeoutExpired) as exc:
            observed["systemctl_error"] = str(exc)

        problems: list[str] = []
        if vulkan_found:
            problems.append("OLLAMA_VULKAN=1 still active in ollama.service environment")
        if not hsa_found:
            problems.append(f"HSA_OVERRIDE_GFX_VERSION={gfx_version} not in effective environment")

        if problems:
            return HardwareActionResult(
                ok=False,
                verified=False,
                message="ROCm environment not fully applied: " + "; ".join(problems),
                requested=requested,
                observed=observed,
                returncode=1,
            )

        return HardwareActionResult(
            ok=True,
            verified=True,
            message="ROCm environment verified (Vulkan disabled, HSA override set)",
            requested=requested,
            observed=observed,
            returncode=0,
        )


__all__ = ["ROCmBackend"]
