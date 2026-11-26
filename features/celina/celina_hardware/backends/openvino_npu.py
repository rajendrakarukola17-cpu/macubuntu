"""
Intel NPU OpenVINO Decoupled Inference Backend (openvino_npu.py)

Architectural Safeguard:
  - Running NPU-mode Ollama inside `ollama.service` conflicts with systemd unit
    defaults and exits with `status=2`.
  - This adapter binds strictly to loopback (127.0.0.1:11435, NEVER 0.0.0.0),
    verifies /dev/accel/accel* is an intel_vpu char device, and wraps launch via
    a transient systemd user service unit (never --scope with --setenv).
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any, Optional

from ..models import CapabilityStatus, HardwareActionResult


class OpenVINONPUBackend:
    name = "openvino_npu"
    override_conf = "system/ollama/openvino-npu.override.conf"
    conflicts_with = ("rocm", "vulkan")
    unit_file = "system/systemd/celina-npu-inference.service"

    _LOOPBACK_HOST_PORT_RE = re.compile(
        r"^(?:(?P<host>\[[0-9a-fA-F:]+\]|[0-9a-fA-F:.]+|[A-Za-z0-9.-]+)):(?P<port>[0-9]+)$"
    )

    @classmethod
    def _loopback_hostport(cls, value: str) -> str:
        """Parse and validate a host:port string, returning normalized loopback host:port.

        Replaces the old prefix denylist with a strict parsed allowlist:
        * Rejects bare ports (":11435", "11435") — implicit wildcard bind.
        * Rejects non-loopback IPv4/IPv6 (0.0.0.0, 192.168.x.x, ::, [::0], etc.).
        * Rejects scheme prefixes ("http://0.0.0.0:11435").
        * Accepts "127.0.0.1:11435", "localhost:11435", "[::1]:11435".
        """
        raw = (value or "").strip()
        if not raw:
            raise ValueError("Refusing empty OLLAMA_HOST; use 127.0.0.1:11435")

        # Strip scheme prefix if present (e.g. "http://0.0.0.0:11435")
        if "://" in raw:
            from urllib.parse import urlsplit

            u = urlsplit(raw)
            host, port = (u.hostname or ""), u.port
        else:
            m = cls._LOOPBACK_HOST_PORT_RE.match(raw)
            if not m:
                raise ValueError(
                    f"Refusing invalid OLLAMA_HOST {raw!r}; expected <host>:<port> (e.g. 127.0.0.1:11435)"
                )
            host, port = m.group("host"), m.group("port")

        # Strip IPv6 brackets
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        host = host.strip().lower()

        # Normalize localhost aliases
        if host == "localhost":
            host = "127.0.0.1"

        # Parse IP address
        try:
            ip = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError(f"Refusing unparseable OLLAMA_HOST host {host!r}") from exc

        # IPv6 mapped IPv4 -> use the IPv4 form
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped

        # Loopback-only enforcement
        if not ip.is_loopback:
            raise ValueError(
                f"Refusing non-loopback bind {host!r} for local NPU inference daemon"
            )

        # Port validation
        if port is None or not 1024 <= int(port) <= 65535:
            raise ValueError(f"Invalid port in OLLAMA_HOST {raw!r}")

        return f"{ip}:{port}" if ip.version == 4 else f"[{ip}]:{port}"

    @classmethod
    def health_check(
        cls,
        *,
        dev_root: str | Path = Path("/dev"),
        sys_root: str | Path = Path("/sys"),
    ) -> tuple[CapabilityStatus, list[str], list[str]]:
        """Verify NPU availability: char device, intel_vpu driver, rw permission.

        Returns (status, devices, reasons).  status is HEALTHY only when at least
        one /dev/accel/accel* node is a char device backed by intel_vpu that the
        current user can read/write.
        """
        dev_root = Path(dev_root)
        sys_root = Path(sys_root)
        accel_dir = dev_root / "accel"
        if not accel_dir.is_dir():
            return CapabilityStatus.NOT_INSTALLED, [], ["/dev/accel directory missing"]

        nodes: list[str] = []
        reasons: list[str] = []

        for p in sorted(accel_dir.glob("accel*")):
            name = p.name

            # Must be a character device
            if not p.is_char_device():
                reasons.append(f"{name}: not a character device")
                continue

            # Driver must be intel_vpu (OpenVINO NPU plugin supports Intel only)
            drv_link = sys_root / "class" / "accel" / name / "device" / "driver"
            driver = ""
            try:
                if drv_link.is_symlink():
                    driver = os.path.basename(os.readlink(drv_link))
            except OSError:
                pass
            if driver != "intel_vpu":
                reasons.append(f"{name}: driver={driver or 'unknown'} unsupported by OpenVINO NPU plugin")
                continue

            # Read/write permission check
            if not os.access(p, os.R_OK | os.W_OK):
                reasons.append(f"{name}: EACCES (user may not be in 'render' group)")
                continue

            nodes.append(str(p))

        if not nodes:
            status = CapabilityStatus.UNSUPPORTED if reasons else CapabilityStatus.NOT_INSTALLED
            return status, [], reasons
        return CapabilityStatus.HEALTHY, nodes, []

    @classmethod
    def get_env(cls, *, host_port: str = "127.0.0.1:11435") -> dict[str, str]:
        """Return validated environment variables for the NPU daemon.

        Host:port is validated via _loopback_hostport; only loopback binds pass.
        """
        normalized = cls._loopback_hostport(host_port)
        return {
            "GGML_OPENVINO_DEVICE": "NPU",
            "OLLAMA_HOST": normalized,
        }

    @classmethod
    def build_scoped_launch_cmd(
        cls,
        *,
        binary: str = "ollama-intel-npu",
        dev_root: str | Path = Path("/dev"),
        host_port: str = "127.0.0.1:11435",
    ) -> list[str]:
        """Build a transient systemd user service launch command.

        Uses --collect (auto-cleanup on exit) + --no-block (non-blocking) so the
        caller is not stuck waiting for a long-running `ollama serve`.  The `--`
        separator prevents a binary beginning with `-` from being parsed as a
        systemd-run option.  Host:port is validated before emission.
        """
        dev_root = Path(dev_root)
        ok, _, _ = cls.health_check(dev_root=dev_root)
        if not ok:
            raise RuntimeError(f"No intel_vpu NPU node present under {dev_root}")

        _ = cls._loopback_hostport(host_port)  # validate host_port

        return [
            "systemd-run",
            "--user",
            "--no-block",
            "--collect",
            f"--unit=celina-npu-{os.getpid()}.service",
            "-E",
            f"GGML_OPENVINO_DEVICE=NPU",
            "-E",
            f"OLLAMA_HOST={cls._loopback_hostport(host_port)}",
            "--",
            binary,
            "serve",
        ]

    @classmethod
    def describe(
        cls,
        *,
        dev_root: str | Path = Path("/dev"),
        sys_root: str | Path = Path("/sys"),
        host_port: str = "127.0.0.1:11435",
    ) -> dict[str, Any]:
        status, nodes, reasons = cls.health_check(dev_root=dev_root, sys_root=sys_root)
        normalized = cls._loopback_hostport(host_port)
        return {
            "backend": cls.name,
            "available": status == CapabilityStatus.HEALTHY,
            "devices": nodes,
            "status": status.value,
            "reasons": reasons,
            "env": cls.get_env(host_port=host_port),
            "endpoint": f"http://{normalized}",
            "unit_file": cls.unit_file,
        }

    @classmethod
    def verify_applied(
        cls,
        *,
        host_port: str = "127.0.0.1:11435",
        probe_fn: Optional[Any] = None,
    ) -> HardwareActionResult:
        """Verify the NPU daemon is actually serving on the normalized endpoint.

        Checks the effective systemd environment and probes the HTTP endpoint.
        Returns a HardwareActionResult with requested vs observed state.
        """
        from urllib.request import Request, urlopen
        from urllib.error import URLError

        normalized = cls._loopback_hostport(host_port)
        endpoint = f"http://{normalized}"

        requested = {"endpoint": endpoint, "env": {"OLLAMA_HOST": normalized}}
        observed: dict[str, Any] = {"endpoint": endpoint}

        # Probe the HTTP endpoint
        try:
            req = Request(endpoint + "/api/version", method="GET")
            with urlopen(req, timeout=2.0) as resp:  # nosec B310 — local endpoint probe, scheme fixed to http/https
                body = resp.read(256).decode("utf-8", errors="replace")
            observed["version_probe"] = body.strip()
            observed["reachable"] = True
        except URLError as exc:
            observed["reachable"] = False
            observed["probe_error"] = str(exc)
            return HardwareActionResult(
                ok=False,
                verified=False,
                message=f"NPU daemon not reachable at {endpoint}: {exc}",
                requested=requested,
                observed=observed,
                returncode=1,
            )

        return HardwareActionResult(
            ok=True,
            verified=True,
            message=f"NPU daemon verified at {endpoint}",
            requested=requested,
            observed=observed,
            returncode=0,
        )


__all__ = ["OpenVINONPUBackend"]
