"""
celina_hardware.compute — Hardware Compute Offload & NPU/GPU Topology Discovery (v4.4)

Detects Linux compute accelerators cleanly without hardcoding vendor assumptions:
  - Neural Processing Units (NPU): `/dev/accel/accel*` (Intel Core Ultra NPU / AMD XDNA)
  - Discrete & Integrated GPUs:    `/dev/dri/renderD*` (NVIDIA CUDA / AMD ROCm & Vulkan / Intel Vulkan)

Architectural Safeguard:
  Ollama (`ollama.service` on 127.0.0.1:11434) does NOT natively support Intel/AMD
  `/dev/accel` NPU backends and can fail (`status=2`) if vendor NPU variables are
  injected into its unit. Therefore:
    1. NPU workloads target the dedicated `celina-npu-inference.service` on
       `http://127.0.0.1:11435` (OpenVINO / llama.cpp-NPU OpenAI-compatible server).
    2. `resolve_inference_endpoint()` checks NPU daemon availability first and
       transparently falls back to `http://127.0.0.1:11434` (Ollama GPU/CPU) if
       the NPU service is absent or stopped.
"""

from __future__ import annotations

import dataclasses
import json
import os
import socket
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_NPU_ENDPOINT = "http://127.0.0.1:11435"

PCI_VENDOR_NAMES: dict[str, str] = {
    "0x8086": "intel",
    "0x1002": "amd",
    "0x1022": "amd",
    "0x10de": "nvidia",
    "0x17cb": "qualcomm",
}


@dataclasses.dataclass(frozen=True)
class ComputeAccelerator:
    kind: str  # "npu" | "gpu"
    device_path: str
    vendor: str  # "intel" | "amd" | "nvidia" | "qualcomm" | "unknown"
    vendor_id: str
    backend: str  # "openvino-npu" | "amd-xdna-npu" | "cuda" | "rocm-vulkan" | "vulkan" | "generic"
    driver: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class ComputeTopology:
    accelerators: tuple[ComputeAccelerator, ...]
    has_npu: bool
    has_gpu: bool
    recommended_backend: str  # "npu_decoupled" | "cuda" | "rocm_vulkan" | "vulkan" | "cpu"
    ollama_env_overrides: dict[str, str]
    npu_endpoint: str = DEFAULT_NPU_ENDPOINT
    ollama_endpoint: str = DEFAULT_OLLAMA_ENDPOINT

    def to_dict(self) -> dict[str, Any]:
        return {
            "accelerators": [a.to_dict() for a in self.accelerators],
            "has_npu": self.has_npu,
            "has_gpu": self.has_gpu,
            "recommended_backend": self.recommended_backend,
            "ollama_env_overrides": dict(self.ollama_env_overrides),
            "npu_endpoint": self.npu_endpoint,
            "ollama_endpoint": self.ollama_endpoint,
        }


def _read_sys_str(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip().lower()
    except OSError:
        return ""


def _read_driver_name(device_dir: Path) -> str:
    driver_link = device_dir / "driver"
    try:
        if driver_link.is_symlink():
            return os.path.basename(os.readlink(driver_link))
    except OSError:
        pass
    uevent = _read_sys_str(device_dir / "uevent")
    for line in uevent.splitlines():
        if line.startswith("driver="):
            return line.split("=", 1)[1].strip()
    return ""


def detect_compute_topology(
    *,
    dev_root: Path = Path("/dev"),
    sys_root: Path = Path("/sys"),
    npu_endpoint: str = DEFAULT_NPU_ENDPOINT,
    ollama_endpoint: str = DEFAULT_OLLAMA_ENDPOINT,
) -> ComputeTopology:
    """Discover `/dev/accel/*` NPUs and `/dev/dri/renderD*` GPUs safely (read-only)."""
    accelerators: list[ComputeAccelerator] = []

    accel_dir = dev_root / "accel"
    if accel_dir.is_dir():
        for node in sorted(accel_dir.glob("accel[0-9]*")):
            name = node.name
            dev_sys = sys_root / "class" / "accel" / name / "device"
            vid = _read_sys_str(dev_sys / "vendor")
            vendor = PCI_VENDOR_NAMES.get(vid, "unknown")
            driver = _read_driver_name(dev_sys)
            if vendor == "intel" or "ivpu" in driver:
                backend = "openvino-npu"
            elif vendor == "amd" or "amdxdna" in driver:
                backend = "amd-xdna-npu"
            else:
                backend = "generic-npu"
            accelerators.append(
                ComputeAccelerator(
                    kind="npu",
                    device_path=str(node),
                    vendor=vendor,
                    vendor_id=vid or "unknown",
                    backend=backend,
                    driver=driver,
                )
            )

    dri_dir = dev_root / "dri"
    if dri_dir.is_dir():
        for node in sorted(dri_dir.glob("renderD[0-9]*")):
            name = node.name
            dev_sys = sys_root / "class" / "drm" / name / "device"
            vid = _read_sys_str(dev_sys / "vendor")
            vendor = PCI_VENDOR_NAMES.get(vid, "unknown")
            driver = _read_driver_name(dev_sys)
            if vendor == "nvidia" or "nvidia" in driver:
                backend = "cuda"
            elif vendor == "amd" or "amdgpu" in driver:
                backend = "rocm-vulkan"
            elif vendor == "intel" or driver in {"i915", "xe"}:
                backend = "vulkan"
            else:
                backend = "generic-gpu"
            accelerators.append(
                ComputeAccelerator(
                    kind="gpu",
                    device_path=str(node),
                    vendor=vendor,
                    vendor_id=vid or "unknown",
                    backend=backend,
                    driver=driver,
                )
            )

    npus = [a for a in accelerators if a.kind == "npu"]
    gpus = [a for a in accelerators if a.kind == "gpu"]

    # Determine safe Ollama drop-in overrides (NEVER inject unsupported NPU vars into Ollama!)
    ollama_env: dict[str, str] = {}
    gpu_backends = {g.backend for g in gpus}

    # Detect AMD Strix Halo via sysfs PCI device ID (0x1002:0x1586) —
    # do NOT set OLLAMA_VULKAN=1 on Strix Halo (avoids Qwen3.5 hang).
    strix_halo = False
    for g in gpus:
        if g.vendor == "amd":
            dev_name = g.device_path.replace("/dev/dri/", "")
            dev_sys = Path("/sys/class/drm") / dev_name / "device"
            vid = _read_sys_str(dev_sys / "vendor")
            did = _read_sys_str(dev_sys / "device")
            if vid == "0x1002" and did == "0x1586":
                strix_halo = True
                break

    if "cuda" in gpu_backends:
        recommended = "npu_decoupled" if npus else "cuda"
    elif "rocm-vulkan" in gpu_backends:
        if strix_halo:
            recommended = "npu_decoupled" if npus else "rocm"
            # Strix Halo: NO OLLAMA_VULKAN=1 (avoid Qwen3.5 hang)
        else:
            recommended = "npu_decoupled" if npus else "rocm_vulkan"
            ollama_env["OLLAMA_VULKAN"] = "1"
    elif "vulkan" in gpu_backends:
        recommended = "npu_decoupled" if npus else "vulkan"
        ollama_env["OLLAMA_VULKAN"] = "1"
    elif npus:
        recommended = "npu_decoupled"
    else:
        recommended = "cpu"

    return ComputeTopology(
        accelerators=tuple(accelerators),
        has_npu=bool(npus),
        has_gpu=bool(gpus),
        recommended_backend=recommended,
        ollama_env_overrides=ollama_env,
        npu_endpoint=npu_endpoint,
        ollama_endpoint=ollama_endpoint,
    )


def _tcp_endpoint_reachable(url: str, timeout: float = 0.25) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _sanitize_local_endpoint(raw_url: str, default_url: str) -> str:
    """
    Enforces loopback-only local inference endpoints (anti-SSRF) and normalizes
    wildcard bind addresses (`0.0.0.0` / `::`) to `127.0.0.1`.
    Falls back to `default_url` if the URL is invalid or points to a non-loopback host.
    Preserves the default port when `raw_url` omits one (e.g. `localhost` -> `http://localhost:11434`).
    Strips arbitrary paths and rejects out-of-range ports.
    """
    candidate = (raw_url or "").strip()
    if not candidate:
        return default_url
    # Bare IPv6 wildcard or loopback without scheme -> normalize to default
    if candidate in {"::", "[::]", "0.0.0.0"}:
        return default_url
    if not candidate.startswith(("http://", "https://")):
        # Bracket bare IPv6 host like "::1" or "::1:11434" safely
        if candidate.count(":") > 1 and not candidate.startswith("["):
            candidate = f"http://[{candidate}]"
        else:
            candidate = f"http://{candidate}"
    try:
        parsed = urllib.parse.urlparse(candidate)
    except ValueError:
        return default_url
    if parsed.scheme not in {"http", "https"}:
        return default_url
    host = (parsed.hostname or "").strip().lower()
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if host not in _LOOPBACK_HOSTS:
        return default_url
    # Preserve default port when raw_url omits one; reject out-of-range ports
    default_port = urllib.parse.urlparse(default_url).port
    try:
        port = parsed.port
    except ValueError:
        return default_url
    if port is not None and not (1 <= port <= 65535):
        return default_url
    effective_port = port if port is not None else default_port
    netloc = f"[{host}]" if ":" in host else host
    if effective_port is not None:
        netloc = f"{netloc}:{effective_port}"
    # Strip arbitrary paths — local inference endpoints have no meaningful path
    return urllib.parse.urlunparse((parsed.scheme, netloc, "", "", "", "")).rstrip("/")


def resolve_inference_endpoint(
    *,
    prefer_npu: bool = True,
    topology: Optional[ComputeTopology] = None,
    probe_fn: Optional[Callable[[str], bool]] = None,
) -> dict[str, Any]:
    """
    Pick the active local inference endpoint cleanly:
      - If an NPU is present AND `prefer_npu=True` AND the decoupled NPU daemon
        (`127.0.0.1:11435`) is listening -> route to `celina-npu-inference.service`.
      - Otherwise fall back transparently to `ollama.service` (`127.0.0.1:11434`).
      - Both endpoints are strictly constrained to loopback hosts (anti-SSRF).
    """
    topo = topology if topology is not None else detect_compute_topology()
    checker = probe_fn if probe_fn is not None else _tcp_endpoint_reachable

    npu_url = _sanitize_local_endpoint(os.environ.get("CELINA_NPU_ENDPOINT", topo.npu_endpoint), DEFAULT_NPU_ENDPOINT)
    ollama_url = _sanitize_local_endpoint(os.environ.get("OLLAMA_HOST", topo.ollama_endpoint), DEFAULT_OLLAMA_ENDPOINT)

    if prefer_npu and topo.has_npu and checker(npu_url):
        return {
            "endpoint": npu_url,
            "backend": "npu_decoupled",
            "fallback_used": False,
            "service": "celina-npu-inference.service",
        }

    return {
        "endpoint": ollama_url,
        "backend": topo.recommended_backend if topo.recommended_backend != "npu_decoupled" else (
            "cuda" if any(a.backend == "cuda" for a in topo.accelerators)
            else "vulkan" if topo.has_gpu
            else "cpu"
        ),
        "fallback_used": bool(prefer_npu and topo.has_npu),
        "service": "ollama.service",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Probe NPU/GPU compute topology for Celina AI")
    parser.add_argument("--json", action="store_true", help="Emit JSON topology + active endpoint")
    args = parser.parse_args()

    topo = detect_compute_topology()
    active = resolve_inference_endpoint(topology=topo)
    payload = {"topology": topo.to_dict(), "active_route": active}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Recommended backend: {topo.recommended_backend}")
        print(f"NPU detected:        {topo.has_npu} ({topo.npu_endpoint})")
        print(f"GPU detected:        {topo.has_gpu} ({topo.ollama_endpoint})")
        print(f"Active route:        {active['endpoint']} ({active['service']}, fallback={active['fallback_used']})")
        for acc in topo.accelerators:
            print(f"  - [{acc.kind.upper()}] {acc.device_path} vendor={acc.vendor} ({acc.vendor_id}) backend={acc.backend} driver={acc.driver or 'n/a'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
