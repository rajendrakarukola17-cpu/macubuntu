"""
celina_hardware.detect_gpu — Dynamic GPU/NPU Backend Selection & /why Attribution (v4.4)

Detects PCI vendor + GPU family (via sysfs + lspci) and selects the safest,
highest-efficiency local inference backend:
  - NVIDIA GPU                         -> cuda
  - AMD Strix Halo (gfx1151 / 8060S)   -> rocm (HSA_OVERRIDE_GFX_VERSION=11.5.1, NO OLLAMA_VULKAN=1)
  - AMD RDNA3+ (non-Strix-Halo)        -> vulkan (OLLAMA_VULKAN=1)
  - Intel Arc / Iris Xe                -> vulkan (OLLAMA_VULKAN=1)
  - No discrete/integrated GPU         -> cpu
  - Plus decoupled Intel/AMD NPU flag  -> openvino_npu sidecar (:11435) when /dev/accel/* exists
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .backends.openvino_npu import OpenVINONPUBackend
from .backends.rocm import ROCmBackend
from .backends.vulkan import VulkanBackend
from .compute import DEFAULT_NPU_ENDPOINT, detect_compute_topology

# Identifiers matching AMD Strix Halo (gfx1151 / PCI 0x1002:0x1586 / Radeon 8060S & 8050S)
# The PCI device ID 1586 is ANCHORED to AMD vendor ID 1002 — bare 1586 on Intel/NVIDIA
# subsystem IDs must NOT trigger Strix Halo / ROCm selection.
_STRIX_HALO_RE = re.compile(
    r"(strix\s*halo|gfx1151|radeon\s*80[56]0s|\b1002:1586\b)", re.IGNORECASE
)
_NVIDIA_RE = re.compile(r"\bnvidia\b", re.IGNORECASE)
_AMD_RE = re.compile(r"\b(amd|ati|radeon)\b", re.IGNORECASE)
# Match Intel GPUs by name OR by PCI vendor ID 8086 on VGA/3D/Display lines
_INTEL_GPU_RE = re.compile(r"\bintel\b.*(arc|iris|uhd|graphics|xe)|\b8086:", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class BackendDecision:
    backend: str  # "cuda" | "rocm" | "vulkan" | "cpu"
    gpu_vendor: str  # "nvidia" | "amd" | "intel" | "none"
    gpu_model: str
    reason: str
    env: dict[str, str]
    override_conf: str
    npu_available: bool
    npu_endpoint: str = DEFAULT_NPU_ENDPOINT

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_why_line(self) -> str:
        npu_note = f" + decoupled NPU ({self.npu_endpoint})" if self.npu_available else ""
        return f"compute_backend={self.backend} ({self.reason}){npu_note}"


def _first_matching_line(pci_text: str, pattern: re.Pattern[str], fallback: str) -> str:
    """Return the first PCI line matching `pattern`, else `fallback`."""
    for ln in pci_text.splitlines():
        s = ln.strip()
        if s and pattern.search(s):
            return s
    return fallback


def _probe_lspci_gpu_lines() -> str:
    if not shutil.which("lspci"):
        return ""
    try:
        res = subprocess.run(
            ["lspci", "-nn"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        if res.returncode != 0:
            return ""
        lines = [
            ln
            for ln in res.stdout.splitlines()
            if any(tag in ln for tag in ("VGA compatible controller", "3D controller", "Display controller"))
        ]
        return "\n".join(lines)
    except Exception:
        return ""


def select_compute_backend(
    *,
    lspci_text: Optional[str] = None,
    dev_root: Path = Path("/dev"),
    sys_root: Path = Path("/sys"),
) -> BackendDecision:
    """Select the exact GPU/CPU backend and NPU availability for Celina + /why provenance."""
    topo = detect_compute_topology(dev_root=dev_root, sys_root=sys_root)
    npu_status, _npu_nodes, _npu_reasons = OpenVINONPUBackend.health_check(dev_root=dev_root)
    npu_ok = npu_status.value == "healthy"
    pci_text = _probe_lspci_gpu_lines() if lspci_text is None else lspci_text

    # 1. NVIDIA -> CUDA
    if _NVIDIA_RE.search(pci_text) or any(a.vendor == "nvidia" for a in topo.accelerators if a.kind == "gpu"):
        return BackendDecision(
            backend="cuda",
            gpu_vendor="nvidia",
            gpu_model=_first_matching_line(pci_text, _NVIDIA_RE, "NVIDIA GPU"),
            reason="NVIDIA GPU detected -> native CUDA backend",
            env={"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"},
            override_conf="",
            npu_available=npu_ok,
        )

    # 2. AMD Strix Halo (gfx1151) -> ROCm with HSA_OVERRIDE_GFX_VERSION=11.5.1 (NO OLLAMA_VULKAN=1!)
    if _STRIX_HALO_RE.search(pci_text):
        return BackendDecision(
            backend="rocm",
            gpu_vendor="amd",
            gpu_model=_first_matching_line(pci_text, _STRIX_HALO_RE, "AMD Strix Halo (gfx1151)"),
            reason="AMD Strix Halo (gfx1151) detected -> ROCm HSA_OVERRIDE_GFX_VERSION=11.5.1 (Vulkan disabled to avoid Qwen3.5 hang)",
            env=ROCmBackend.get_env(gfx_version="11.5.1"),
            override_conf=ROCmBackend.override_conf,
            npu_available=npu_ok,
        )

    # 3. AMD RDNA3+ / Radeon iGPU/dGPU -> Vulkan
    if _AMD_RE.search(pci_text) or any(a.vendor == "amd" for a in topo.accelerators if a.kind == "gpu"):
        return BackendDecision(
            backend="vulkan",
            gpu_vendor="amd",
            gpu_model=_first_matching_line(pci_text, _AMD_RE, "AMD Radeon GPU"),
            reason="AMD GPU detected -> low-power Vulkan backend (OLLAMA_VULKAN=1)",
            env=VulkanBackend.get_env(),
            override_conf=VulkanBackend.override_conf,
            npu_available=npu_ok,
        )

    # 4. Intel Arc / Iris Xe -> Vulkan
    if _INTEL_GPU_RE.search(pci_text) or any(a.vendor == "intel" for a in topo.accelerators if a.kind == "gpu"):
        return BackendDecision(
            backend="vulkan",
            gpu_vendor="intel",
            gpu_model=_first_matching_line(pci_text, _INTEL_GPU_RE, "Intel Graphics"),
            reason="Intel GPU detected -> Vulkan backend (OLLAMA_VULKAN=1)",
            env=VulkanBackend.get_env(),
            override_conf=VulkanBackend.override_conf,
            npu_available=npu_ok,
        )

    # 5. CPU fallback — no GPU lines to match, so just use a generic model name
    return BackendDecision(
        backend="cpu",
        gpu_vendor="none",
        gpu_model="CPU-only",
        reason="No supported GPU detected -> CPU backend",
        env={},
        override_conf="",
        npu_available=npu_ok,
    )


def select_backend(
    *,
    lspci_text: Optional[str] = None,
    dev_root: Path = Path("/dev"),
    sys_root: Path = Path("/sys"),
    prefer_npu: bool = True,
) -> str:
    """
    Concise accelerator selector for Celina router & CLI diagnostics:
      - Any `/dev/accel/accel*` NPU (and `prefer_npu=True`):
          * AMD XDNA (`amd-xdna-npu`)   -> `"amd_xdna_npu"`
          * Intel/generic NPU           -> `"openvino_npu"`
      - Otherwise delegates to `select_compute_backend()` -> `"cuda" | "rocm" | "vulkan" | "cpu"`
    """
    if prefer_npu:
        topo = detect_compute_topology(dev_root=dev_root, sys_root=sys_root)
        npus = [a for a in topo.accelerators if a.kind == "npu"]
        if npus:
            # Verify at least one NPU node is readable before routing to it.
            # (Full driver/char-device validation happens in OpenVINONPUBackend.health_check
            # at daemon launch time; here we just need a readable node to avoid
            # advertising an NPU backend that can't be used.)
            npu_readable = any(os.access(n.device_path, os.R_OK) for n in npus)
            if npu_readable:
                if any(a.backend == "amd-xdna-npu" or a.vendor == "amd" for a in npus):
                    return "amd_xdna_npu"
                return "openvino_npu"
    return select_compute_backend(
        lspci_text=lspci_text,
        dev_root=dev_root,
        sys_root=sys_root,
    ).backend
