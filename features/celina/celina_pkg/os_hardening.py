"""Celina AI — OS-Level Kernel & Systems Hardening Runtime (`os_hardening.py`)

Unifies inspection, verification, and safe command construction across all 7
Linux OS-level hardening layers for Ubuntu 26.04+ / kernel 6.12+:
  1. sched_ext (`scx_lavd` low-latency <-> `scx_rusty` throughput dual-scheduler switch)
  2. cgroup v2 (`local-inference.slice` MemoryHigh/MemoryMax/memory.events inspection)
  3. PSI + KV Cache Trimming (`/proc/pressure/memory` `full avg10` -> 25% `num_ctx` trim)
  4. Landlock LSM / SHarness (kernel-enforced per-tool policy verification)
  5. PREEMPT_RT (optional sub-10us voice VAD jitter verification & FIFO-90 pinning)
  6. TPM 2.0 Measured Boot + PCR-signed sealing (`--tpm2-public-key=` surviving kernel updates
     + `systemd-creds encrypt --with-key=tpm2` for Tier B credentials)
  7. io_uring readiness (`/sys/kernel/io_uring_disabled` == 0) + SQLite WAL / Turso v5 path
"""

from __future__ import annotations

import dataclasses
import platform
import re
import shutil
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Layer 1: sched_ext + scx_lavd / scx_rusty Dual-Scheduler Switch
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class SchedExtStatus:
    supported: bool
    enabled: bool
    active_ops: str
    scxctl_available: bool


def detect_sched_ext(
    *,
    sys_root: Path = Path("/sys"),
    scxctl_bin: str | None = None,
) -> SchedExtStatus:
    state_p = Path(sys_root) / "kernel" / "sched_ext" / "state"
    ops_p = Path(sys_root) / "kernel" / "sched_ext" / "root" / "ops"
    supported = state_p.exists()
    enabled = False
    active_ops = ""
    if supported:
        try:
            enabled = state_p.read_text(encoding="utf-8").strip().lower() == "enabled"
        except OSError:
            enabled = False
    if ops_p.exists():
        try:
            active_ops = ops_p.read_text(encoding="utf-8").strip()
        except OSError:
            active_ops = ""
    has_scxctl = bool(scxctl_bin if scxctl_bin is not None else shutil.which("scxctl"))
    return SchedExtStatus(
        supported=supported,
        enabled=enabled,
        active_ops=active_ops,
        scxctl_available=has_scxctl,
    )


def build_scx_switch_cmd(
    scheduler: str,
    *,
    mode: str | None = None,
    scxctl_bin: str = "/usr/bin/scxctl",
) -> list[str]:
    """Builds `scxctl switch -s <lavd|rusty> [--mode <lowlatency|auto|...>]` safely (no shell)."""
    sched = (scheduler or "").strip().lower()
    if sched not in {"lavd", "rusty", "scx_lavd", "scx_rusty"}:
        raise ValueError(f"Unsupported sched_ext scheduler '{scheduler}'. Allowed: lavd, rusty.")
    short_sched = sched.removeprefix("scx_")
    cmd = [scxctl_bin, "switch", "-s", short_sched]
    if mode:
        m = mode.strip().lower()
        if m not in {"auto", "gaming", "lowlatency", "powersave"}:
            raise ValueError(f"Unsupported scx_loader mode '{mode}'.")
        cmd.extend(["--mode", m])
    return cmd


def build_scx_stop_cmd(*, scxctl_bin: str = "/usr/bin/scxctl") -> list[str]:
    """Builds `scxctl stop` to revert cleanly to the kernel's native EEVDF scheduler."""
    return [scxctl_bin, "stop"]


class SchedExtWatchdog:
    """Safety watchdog guarding against the documented `scx_lavd` kernel watchdog-ejection
    compositor freeze (33-45s Wayland stalls).
    Tracks unexpected ejections (state falling from `enabled` -> `disabled` while active
    or explicit ejection reports); once `max_ejections` is reached, trips a cooldown lock
    and forces reversion to the kernel's native EEVDF scheduler (`scxctl stop`).
    """

    def __init__(self, *, max_ejections: int = 2, cooldown_sec: float = 3600.0):
        self.max_ejections = max(1, int(max_ejections))
        self.cooldown_sec = float(cooldown_sec)
        self.ejection_count = 0
        self.locked_to_eevdf_until = 0.0

    def allow_scx_lavd(self, *, now: float | None = None) -> tuple[bool, str]:
        import time as _time

        t = float(now) if now is not None else _time.time()
        if t < self.locked_to_eevdf_until:
            rem = int(self.locked_to_eevdf_until - t)
            return False, f"scx_lavd cooldown active ({rem}s remaining); staying on native EEVDF"
        return True, "ok"

    def evaluate_health(
        self,
        *,
        expected_active: bool,
        sys_root: Path = Path("/sys"),
        now: float | None = None,
    ) -> dict[str, Any]:
        import time as _time

        t = float(now) if now is not None else _time.time()
        st = detect_sched_ext(sys_root=sys_root)
        if expected_active and not st.enabled:
            self.ejection_count += 1
            if self.ejection_count >= self.max_ejections:
                self.locked_to_eevdf_until = t + self.cooldown_sec
                return {
                    "healthy": False,
                    "action": "revert_to_eevdf",
                    "fallback_cmd": build_scx_stop_cmd(),
                    "ejection_count": self.ejection_count,
                    "locked_to_eevdf": True,
                }
            return {
                "healthy": False,
                "action": "warn_ejection",
                "fallback_cmd": build_scx_stop_cmd(),
                "ejection_count": self.ejection_count,
                "locked_to_eevdf": False,
            }
        return {
            "healthy": True,
            "action": "none",
            "active_ops": st.active_ops,
            "ejection_count": self.ejection_count,
            "locked_to_eevdf": t < self.locked_to_eevdf_until,
        }


# ---------------------------------------------------------------------------
# Layer 2: cgroup v2 Hard Memory Ceiling Inspection
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class CgroupMemorySnapshot:
    available: bool
    cgroup_path: str
    memory_current_bytes: int
    events: dict[str, int]
    pressure_full_avg10: float


def _parse_psi_text(text: str) -> tuple[float, float, float, float]:
    """Parses Linux PSI text (`some avg10=... avg60=...` / `full avg10=... avg60=...`)."""
    some10 = some60 = full10 = full60 = 0.0
    for line in (text or "").splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        kind = parts[0]
        kv: dict[str, float] = {}
        for tok in parts[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                try:
                    kv[k] = float(v)
                except ValueError:
                    continue
        if kind == "some":
            some10 = kv.get("avg10", 0.0)
            some60 = kv.get("avg60", 0.0)
        elif kind == "full":
            full10 = kv.get("avg10", 0.0)
            full60 = kv.get("avg60", 0.0)
    return some10, some60, full10, full60


def read_cgroup_memory_state(cgroup_dir: Path) -> CgroupMemorySnapshot:
    cg = Path(cgroup_dir)
    if not cg.exists():
        return CgroupMemorySnapshot(False, str(cg), 0, {}, 0.0)
    cur_bytes = 0
    events: dict[str, int] = {}
    full10 = 0.0
    try:
        cur_p = cg / "memory.current"
        if cur_p.exists():
            cur_bytes = int(cur_p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        cur_bytes = 0
    try:
        ev_p = cg / "memory.events"
        if ev_p.exists():
            for ln in ev_p.read_text(encoding="utf-8").splitlines():
                toks = ln.strip().split()
                if len(toks) == 2:
                    events[toks[0]] = int(toks[1])
    except (OSError, ValueError):
        pass
    try:
        pr_p = cg / "memory.pressure"
        if pr_p.exists():
            _, _, full10, _ = _parse_psi_text(pr_p.read_text(encoding="utf-8"))
    except OSError:
        full10 = 0.0
    return CgroupMemorySnapshot(True, str(cg), cur_bytes, events, full10)


# ---------------------------------------------------------------------------
# Layer 3: PSI (/proc/pressure/memory) + Dynamic KV-Cache Trimming (kv-psi)
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class MemoryPSI:
    available: bool
    some_avg10: float = 0.0
    some_avg60: float = 0.0
    full_avg10: float = 0.0
    full_avg60: float = 0.0


def read_memory_psi(psi_path: Path = Path("/proc/pressure/memory")) -> MemoryPSI:
    p = Path(psi_path)
    if not p.exists():
        return MemoryPSI(available=False)
    try:
        txt = p.read_text(encoding="utf-8")
    except OSError:
        return MemoryPSI(available=False)
    some10, some60, full10, full60 = _parse_psi_text(txt)
    return MemoryPSI(
        available=True,
        some_avg10=some10,
        some_avg60=some60,
        full_avg10=full10,
        full_avg60=full60,
    )


def compute_psi_trimmed_ctx(
    current_ctx: int,
    psi: MemoryPSI,
    *,
    full_threshold: float = 10.0,
    some_threshold: float = 30.0,
    trim_factor: float = 0.75,
    min_ctx: int = 512,
) -> tuple[int, bool, str]:
    """kv-psi adaptive KV-cache pruning:
    If `/proc/pressure/memory` `full avg10 > 10.0` (or severe `some avg10 > 30.0`),
    trims `num_ctx` by 25% (floor `min_ctx`) before submitting the Ollama request.
    """
    base = max(min_ctx, int(current_ctx))
    if not psi.available:
        return base, False, ""
    if psi.full_avg10 > full_threshold:
        trimmed = max(min_ctx, int(base * trim_factor))
        return (
            trimmed,
            trimmed < base,
            f"PSI full avg10={psi.full_avg10:.1f} > {full_threshold:.1f}: trimmed num_ctx {base} -> {trimmed}",
        )
    if psi.some_avg10 > some_threshold:
        trimmed = max(min_ctx, int(base * trim_factor))
        return (
            trimmed,
            trimmed < base,
            f"PSI some avg10={psi.some_avg10:.1f} > {some_threshold:.1f}: trimmed num_ctx {base} -> {trimmed}",
        )
    return base, False, ""


# ---------------------------------------------------------------------------
# Layer 5: PREEMPT_RT Voice Jitter Detection & FIFO-90 Pinning
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class PreemptRTStatus:
    is_realtime: bool
    uname_v: str
    sys_realtime_flag: int | None


def detect_preempt_rt(
    *,
    uname_v: str | None = None,
    realtime_path: Path = Path("/sys/kernel/realtime"),
) -> PreemptRTStatus:
    uv = uname_v if uname_v is not None else platform.version()
    rt_flag: int | None = None
    p = Path(realtime_path)
    if p.exists():
        try:
            rt_flag = int(p.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            rt_flag = None
    is_rt = ("PREEMPT_RT" in uv) or (rt_flag == 1)
    return PreemptRTStatus(is_realtime=is_rt, uname_v=uv, sys_realtime_flag=rt_flag)


def build_chrt_voice_cmd(pid: int, *, priority: int = 90) -> list[str]:
    """Builds `chrt -f -p <prio> <pid>` to pin Celina voice VAD to FIFO priority 90."""
    p = max(1, min(99, int(priority)))
    if int(pid) <= 0:
        raise ValueError("pid must be a positive integer")
    return ["chrt", "-f", "-p", str(p), str(int(pid))]


# ---------------------------------------------------------------------------
# Layer 6: TPM 2.0 Measured Boot + PCR-Signed Sealing (Tier B Secrets)
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class TPM2Status:
    present: bool
    device_path: str
    systemd_creds_available: bool
    systemd_cryptenroll_available: bool


def detect_tpm2(*, dev_root: Path = Path("/dev")) -> TPM2Status:
    root = Path(dev_root)
    dev_path = ""
    for cand in ("tpmrm0", "tpm0"):
        p = root / cand
        if p.exists():
            dev_path = str(p)
            break
    return TPM2Status(
        present=bool(dev_path),
        device_path=dev_path,
        systemd_creds_available=bool(shutil.which("systemd-creds")),
        systemd_cryptenroll_available=bool(shutil.which("systemd-cryptenroll")),
    )


def build_tpm2_pcr_sign_cmd(
    kernel_release: str,
    *,
    public_key_path: Path = Path("/etc/celina/tierb-pcr.pub"),
    phase: str = "enter-initrd",
) -> list[str]:
    """Builds `systemd-measure sign` for PCR 11 policy signing so TPM2 volume sealing
    survives Ubuntu kernel updates (unlike brittle literal `--tpm2-pcrs=7,11`).
    """
    krel = (kernel_release or "").strip()
    if not krel or not re.fullmatch(r"[A-Za-z0-9._+-]+", krel):
        raise ValueError(f"Invalid kernel release string '{kernel_release}'")
    return [
        "systemd-measure",
        "sign",
        f"--linux=/boot/vmlinuz-{krel}",
        f"--initrd=/boot/initrd.img-{krel}",
        f"--phase={phase}",
        f"--public-key={public_key_path}",
    ]


def build_tpm2_cryptenroll_cmd(
    block_device: str,
    *,
    public_key_path: Path = Path("/etc/celina/tierb-pcr.pub"),
) -> list[str]:
    """Builds `systemd-cryptenroll --tpm2-device=auto --tpm2-public-key=... <dev>`.
    Uses signed PCR public-key policy instead of raw PCR hashes so kernel upgrades
    do not lock out Tier B LUKS volumes.
    """
    dev = (block_device or "").strip()
    if not dev.startswith("/dev/"):
        raise ValueError(f"Block device '{block_device}' must start with /dev/")
    return [
        "systemd-cryptenroll",
        "--tpm2-device=auto",
        f"--tpm2-public-key={public_key_path}",
        dev,
    ]


def build_tpm2_seal_secret_cmd(
    src_credential: Path,
    dst_credential: Path = Path("/etc/celina/tierb-secret.cred"),
) -> list[str]:
    """Builds `systemd-creds encrypt --with-key=tpm2 <src> <dst>` for Tier B secrets."""
    return [
        "systemd-creds",
        "encrypt",
        "--with-key=tpm2",
        str(src_credential),
        str(dst_credential),
    ]


# ---------------------------------------------------------------------------
# Layer 7: io_uring Verification + SQLite WAL / Turso v5 Readiness
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class IoUringStatus:
    available: bool
    disabled_flag: int | None  # 0 = enabled, 1 = unprivileged disabled, 2 = fully disabled
    sqlite_mode: str = "sqlite_wal_busy_timeout"
    turso_v5_ready: bool = False


def detect_io_uring(
    *,
    sys_path: Path = Path("/sys/kernel/io_uring_disabled"),
) -> IoUringStatus:
    p = Path(sys_path)
    flag: int | None = None
    if p.exists():
        try:
            flag = int(p.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            flag = None
    enabled = flag == 0
    turso_ok = bool(shutil.which("turso") or shutil.which("libsql"))
    return IoUringStatus(
        available=enabled,
        disabled_flag=flag,
        sqlite_mode="sqlite_wal_busy_timeout",
        turso_v5_ready=enabled and turso_ok,
    )


def collect_os_hardening_report(
    *,
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
    psi_path: Path = Path("/proc/pressure/memory"),
    cgroup_dir: Path | None = None,
) -> dict[str, Any]:
    """Returns a single structured readiness report across all 7 OS hardening layers."""
    from . import landlock_policy

    scx = detect_sched_ext(sys_root=sys_root)
    psi = read_memory_psi(psi_path=psi_path)
    cg = read_cgroup_memory_state(cgroup_dir) if cgroup_dir is not None else CgroupMemorySnapshot(False, "", 0, {}, 0.0)
    rt = detect_preempt_rt(realtime_path=Path(sys_root) / "kernel" / "realtime")
    tpm = detect_tpm2(dev_root=dev_root)
    iouring = detect_io_uring(sys_path=Path(sys_root) / "kernel" / "io_uring_disabled")
    ll_abi = landlock_policy.detect_landlock_abi()
    return {
        "sched_ext": dataclasses.asdict(scx),
        "cgroup_v2": dataclasses.asdict(cg),
        "psi": dataclasses.asdict(psi),
        "landlock": {
            "abi_version": ll_abi,
            "supported": ll_abi >= 1,
            "sharness_installed": bool(shutil.which("sharness-ai")),
        },
        "preempt_rt": dataclasses.asdict(rt),
        "tpm2": dataclasses.asdict(tpm),
        "io_uring": dataclasses.asdict(iouring),
    }
