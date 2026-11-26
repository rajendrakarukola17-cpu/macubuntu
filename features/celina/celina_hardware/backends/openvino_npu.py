"""
Intel/AMD NPU OpenVINO Decoupled Inference Backend (`openvino_npu.py`)

Architectural Safeguard:
  - Running NPU-mode Ollama inside `ollama.service` conflicts with systemd unit
    defaults and exits with `status=2`.
  - This adapter verifies `/dev/accel/accel*` exists first, binds strictly to
    loopback (`127.0.0.1:11435`, NEVER `0.0.0.0`), and wraps launch via either
    `systemd-run --user --scope` or the dedicated `celina-npu-inference.service`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class OpenVINONPUBackend:
    name = "openvino_npu"
    endpoint = "http://127.0.0.1:11435"
    unit_file = "system/systemd/celina-npu-inference.service"

    @classmethod
    def health_check(cls, *, dev_root: Path = Path("/dev")) -> tuple[bool, list[str]]:
        accel_dir = dev_root / "accel"
        if not accel_dir.is_dir():
            return False, []
        nodes = [str(p) for p in sorted(accel_dir.glob("accel*"))]
        return bool(nodes), nodes

    @classmethod
    def get_env(cls, *, host_port: str = "127.0.0.1:11435") -> dict[str, str]:
        # Security guardrail: refuse wildcard 0.0.0.0 binds on local LLM daemons
        if host_port.startswith("0.0.0.0") or host_port.startswith("[::]"):
            raise ValueError("Refusing insecure 0.0.0.0/:: bind for local NPU inference daemon; use 127.0.0.1.")
        return {
            "GGML_OPENVINO_DEVICE": "NPU",
            "OLLAMA_HOST": host_port,
        }

    @classmethod
    def build_scoped_launch_cmd(
        cls,
        binary: str = "ollama-intel-npu",
        *,
        dev_root: Path = Path("/dev"),
        host_port: str = "127.0.0.1:11435",
    ) -> list[str]:
        ok, _nodes = cls.health_check(dev_root=dev_root)
        if not ok:
            raise RuntimeError(f"No /dev/accel/* NPU node present under {dev_root}")
        env = cls.get_env(host_port=host_port)
        return [
            "systemd-run",
            "--user",
            "--scope",
            "--unit=celina-npu-scope",
            "--setenv",
            f"GGML_OPENVINO_DEVICE={env['GGML_OPENVINO_DEVICE']}",
            "--setenv",
            f"OLLAMA_HOST={env['OLLAMA_HOST']}",
            binary,
            "serve",
        ]

    @classmethod
    def describe(cls, *, dev_root: Path = Path("/dev")) -> dict[str, Any]:
        ok, nodes = cls.health_check(dev_root=dev_root)
        return {
            "backend": cls.name,
            "available": ok,
            "devices": nodes,
            "env": cls.get_env(),
            "endpoint": cls.endpoint,
            "unit_file": cls.unit_file,
        }
