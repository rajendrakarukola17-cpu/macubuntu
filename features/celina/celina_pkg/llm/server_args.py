"""Hardware-adaptive llama-server argument builder.

Builds llama-server command-line arguments scaled to the detected hardware
profile (tier, RAM, GPU vendor, VRAM) from /etc/macubuntu/hardware.json.

Uses only stdlib: json, pathlib, subprocess, shlex.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any


def _read_hardware_json() -> dict[str, Any]:
    """Read hardware contract JSON, returning empty dict on failure."""
    hw_path = Path("/etc/macubuntu/hardware.json")
    if not hw_path.is_file():
        return {}
    try:
        return json.loads(hw_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def detect_gpu_via_lspci() -> str:
    """Best-effort GPU vendor detection via lspci."""
    try:
        result = subprocess.run(
            ["lspci", "-nn"], capture_output=True, text=True, timeout=5,
        )
        pci = result.stdout
        if "NVIDIA" in pci.upper():
            return "nvidia"
        if "AMD" in pci.upper() or "ATI" in pci.upper():
            return "amd"
        if "Intel" in pci:
            return "intel"
    except Exception:
        pass
    return "cpu"


def build_llama_server_args(
    model_path: str,
    hw: dict[str, Any] | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build hardware-adaptive llama-server arguments.

    Parameters
    ----------
    model_path : str
        Path to the GGUF model file.
    hw : dict | None
        Hardware contract dict (from /etc/macubuntu/hardware.json). If None,
        reads from disk.
    extra_args : list[str] | None
        Additional user-supplied args appended at the end.

    Returns
    -------
    list[str]
        Command-line arguments for llama-server (not including the binary).

    """
    if hw is None:
        hw = _read_hardware_json()

    tier = str(hw.get("tier", "balanced")).lower()
    ram_mb = int(hw.get("ram_mb", 8192))
    gpu_vendor = str(hw.get("gpu_vendor", "cpu")).lower()
    vram_mb = int(hw.get("vram_mb", 0))
    cpu_cores = int(hw.get("cpu_cores", 4))

    args: list[str] = [
        "-m", str(model_path),
        "-c", "4096",          # context size
        "-b", "512",           # batch size
        "--host", "127.0.0.1",
        "--port", "8080",
    ]

    # --- GPU offload layer count (n-gpu-layers) ---
    # Lite/low-VRAM: 0 (CPU only). Balanced: partial offload. Workstation: full.
    if gpu_vendor == "nvidia" and vram_mb >= 8000:
        ngl = "33"  # full offload for high-VRAM NVIDIA
    elif gpu_vendor in ("amd", "intel") and vram_mb >= 4000:
        ngl = "20"  # partial offload for mid-range
    elif tier == "workstation" and vram_mb > 0:
        ngl = "33"
    elif tier == "balanced" and vram_mb > 0:
        ngl = "20"
    else:
        ngl = "0"  # CPU-only on lite/constrained or no GPU
    args.extend(["-ngl", ngl])

    # --- Thread count scaled to CPU cores ---
    threads = max(1, cpu_cores)
    args.extend(["-t", str(threads)])

    # --- Memory / context scaling by tier ---
    if tier == "lite":
        args.extend(["-c", "2048", "-b", "256"])
    elif tier == "workstation":
        args.extend(["-c", "8192", "-b", "1024"])
    else:  # balanced
        args.extend(["-c", "4096", "-b", "512"])

    # --- Flash attention for AMD/NVIDIA ---
    if gpu_vendor in ("amd", "nvidia") and vram_mb >= 4000:
        args.append("--flash-attn")

    # --- RBT (RoPE) scaling for large context on workstations ---
    if tier == "workstation":
        args.extend(["--rope-freq-base", "10000", "--rope-freq-scale", "0.5"])

    # Append any extra user-supplied arguments last
    if extra_args:
        args.extend(extra_args)

    return args


def llama_server_command(
    model_path: str,
    hw: dict[str, Any] | None = None,
    extra_args: list[str] | None = None,
    binary: str = "llama-server",
) -> str:
    """Return a shell-ready llama-server command string.

    Parameters
    ----------
    model_path : str
        Path to the GGUF model file.
    hw : dict | None
        Hardware contract dict.
    extra_args : list[str] | None
        Additional user-supplied args.
    binary : str
        llama-server binary name/path.

    Returns
    -------
    str
        Shell-escaped command string.

    """
    args = build_llama_server_args(model_path, hw=hw, extra_args=extra_args)
    return " ".join(shlex.quote(a) for a in [binary] + args)


if __name__ == "__main__":  # pragma: no cover - CLI smoke test
    import sys

    model = sys.argv[1] if len(sys.argv) > 1 else "/models/qwen2.5-7b-instruct-q4_0.gguf"
    print(llama_server_command(model))
