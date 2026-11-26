"""
Celina AI — Unprivileged eBPF Sensor Consumer & Kernel Capability Probe (v4.4)

Architecture:
  - Kernel BPF loading & CAP_BPF stay strictly inside `celina-ebpf-sensor.service`.
  - Celina's user-space Python agent stays 100% unprivileged and consumes sanitized
    ring-buffer event snapshots from `/run/celina-ebpf/events.jsonl` (when enabled),
    falling back seamlessly to D-Bus/inotify (`os_events.py`) whenever BTF/eBPF is
    unavailable or not opted in.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Optional

DEFAULT_EBPF_STREAM = Path("/run/celina-ebpf/events.jsonl")
DEFAULT_SIGNED_POLICY = Path("/etc/celina/policy.signed")


@dataclasses.dataclass(frozen=True)
class EbpfCapabilities:
    btf_available: bool
    bpf_lsm_enabled: bool
    lockdown_mode: str
    sensor_stream_active: bool
    recommended_mode: str  # "ebpf_lsm" | "ebpf_tracepoints" | "userspace_dbus_fallback"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def probe_ebpf_support(
    *,
    sys_root: Path = Path("/sys"),
    stream_path: Path = DEFAULT_EBPF_STREAM,
) -> EbpfCapabilities:
    """Read-only probe of Linux BTF, BPF LSM, kernel lockdown, and active sensor stream."""
    btf = (sys_root / "kernel" / "btf" / "vmlinux").is_file()
    lsm_file = sys_root / "kernel" / "security" / "lsm"
    bpf_lsm = False
    if lsm_file.is_file():
        try:
            tokens = [t.strip() for t in lsm_file.read_text(encoding="utf-8", errors="ignore").split(",")]
            bpf_lsm = "bpf" in tokens
        except OSError:
            bpf_lsm = False

    lockdown_file = sys_root / "kernel" / "security" / "lockdown"
    lockdown_mode = "none"
    if lockdown_file.is_file():
        try:
            raw = lockdown_file.read_text(encoding="utf-8", errors="ignore").strip()
            # Format: "none [integrity] confidentiality"
            for tok in raw.split():
                if tok.startswith("[") and tok.endswith("]"):
                    lockdown_mode = tok[1:-1]
                    break
        except OSError:
            lockdown_mode = "unknown"

    stream_active = stream_path.is_file()
    if btf and bpf_lsm:
        rec = "ebpf_lsm"
    elif btf:
        rec = "ebpf_tracepoints"
    else:
        rec = "userspace_dbus_fallback"

    return EbpfCapabilities(
        btf_available=btf,
        bpf_lsm_enabled=bpf_lsm,
        lockdown_mode=lockdown_mode,
        sensor_stream_active=stream_active,
        recommended_mode=rec,
    )


class EbpfEventClient:
    """Reads recent kernel-level exec/open/lsm events emitted by celina-ebpf-sensor.service."""

    def __init__(self, stream_path: Path = DEFAULT_EBPF_STREAM):
        self.stream_path = Path(stream_path)

    def read_recent_events(self, limit: int = 50, *, uid_filter: Optional[int] = None) -> list[dict[str, Any]]:
        if not self.stream_path.is_file():
            return []
        events: list[dict[str, Any]] = []
        try:
            with self.stream_path.open("r", encoding="utf-8", errors="ignore") as fh:
                for raw in fh:
                    s = raw.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if uid_filter is not None and obj.get("uid") != uid_filter:
                        continue
                    events.append(obj)
        except OSError:
            return []
        return events[-max(1, int(limit)) :]


def verify_signed_policy(
    policy_path: Path,
    signature_path: Path,
    *,
    ed25519_public_key_bytes: Optional[bytes] = None,
    expected_sha256: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Fail-closed verifier for Focus Mode BPF LSM policy bundles (`/etc/celina/policy.signed`).
      - When `ed25519_public_key_bytes` (32-byte raw Ed25519 public key) is provided and
        `cryptography` is installed, performs real Ed25519 detached signature verification.
      - Otherwise verifies a pinned SHA-256 hex digest (`expected_sha256`) stored alongside
        the bundle with constant-time `hmac.compare_digest`.
    """
    if not policy_path.is_file():
        return False, f"Policy file missing: {policy_path}"
    payload = policy_path.read_bytes()
    actual_sha = hashlib.sha256(payload).hexdigest()

    if ed25519_public_key_bytes is not None:
        if not signature_path.is_file():
            return False, f"Detached signature missing: {signature_path}"
        sig_raw = signature_path.read_text(encoding="utf-8", errors="ignore").strip()
        try:
            sig_bytes = base64.b64decode(sig_raw, validate=True)
        except Exception:
            try:
                sig_bytes = bytes.fromhex(sig_raw)
            except ValueError:
                return False, "Malformed Ed25519 signature encoding."
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            pub = Ed25519PublicKey.from_public_bytes(ed25519_public_key_bytes)
            pub.verify(sig_bytes, payload)
            return True, f"Verified Ed25519 policy signature (sha256={actual_sha[:12]})"
        except ImportError:
            return False, "cryptography package unavailable for Ed25519 verification."
        except Exception as exc:
            return False, f"Ed25519 signature verification FAILED: {exc}"

    if expected_sha256 is not None:
        if hmac.compare_digest(actual_sha.lower(), expected_sha256.strip().lower()):
            return True, f"Verified policy SHA-256 ({actual_sha[:12]})"
        return False, f"Policy SHA-256 mismatch (actual={actual_sha[:12]})"

    if signature_path.is_file():
        sig_text = signature_path.read_text(encoding="utf-8", errors="ignore").strip()
        if sig_text.startswith("sha256:"):
            target = sig_text.split(":", 1)[1].strip().lower()
            if hmac.compare_digest(actual_sha, target):
                return True, f"Verified detached SHA-256 envelope ({actual_sha[:12]})"
            return False, "Detached SHA-256 envelope mismatch."

    return False, "No verifiable Ed25519 key or SHA-256 trust anchor provided (fail-closed)."


def format_prometheus_metrics(total_blocks: int = 0, net_blocks: int = 0) -> str:
    """Render Prometheus text exposition metrics matching focus.bpf.c per-CPU counters."""
    t_val = max(0, int(total_blocks))
    n_val = max(0, int(net_blocks))
    return (
        "# HELP celina_ebpf_blocks_total Total syscalls denied by Celina BPF LSM Focus Mode\n"
        "# TYPE celina_ebpf_blocks_total counter\n"
        f"celina_ebpf_blocks_total {t_val}\n"
        "# HELP celina_ebpf_net_blocks_total Network socket_connect/bind calls denied during Focus Mode\n"
        "# TYPE celina_ebpf_net_blocks_total counter\n"
        f"celina_ebpf_net_blocks_total {n_val}\n"
    )

