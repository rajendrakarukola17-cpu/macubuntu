"""Celina Hardware Governor — runtime hardware-adaptive subsystem.

Reads /etc/macubuntu/hardware.json at startup, watches live telemetry
(PSI memory pressure, AC power state, window focus), and exposes the
active Operating Mode (eco, balanced, performance, pressure_relief) to
every Celina subsystem.

Uses only stdlib: asyncio, json, subprocess, pathlib, dataclasses.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tier_policy import CelinaTierPolicy


@dataclass
class HardwareProfile:
    """Static hardware contract from /etc/macubuntu/hardware.json."""

    tier: str
    ram_mb: int
    cpu_cores: int
    avx2: bool
    has_battery: bool
    disk_transport: str
    gpu_vendor: str
    vram_mb: int


class HardwareGovernor:
    """Async governor that evaluates live system conditions and sets the
    operating mode. Subsystems (LLM router, indexer, vision, proactive)
    query the governor to adapt behavior.
    """

    def __init__(
        self,
        profile_path: str = "/etc/macubuntu/hardware.json",
        *,
        tier_policy: CelinaTierPolicy | None = None,
    ):
        self.profile_path = Path(profile_path)
        self.hw = self._load_profile()
        self.mode: str = "balanced"
        self.tier_policy = tier_policy or CelinaTierPolicy()
        self._heavy_model_lock = asyncio.Lock()
        self._mode_change_listeners: list[Any] = []

    def _load_profile(self) -> HardwareProfile:
        """Load hardware contract from JSON."""
        try:
            data = json.loads(self.profile_path.read_text(encoding="utf-8"))
            return HardwareProfile(
                tier=data.get("tier", "balanced"),
                ram_mb=int(data.get("ram_mb", 8192)),
                cpu_cores=int(data.get("cpu_cores", 4)),
                avx2=bool(data.get("avx2", False)),
                has_battery=bool(data.get("has_battery", False)),
                disk_transport=str(data.get("disk_transport", "unknown")),
                gpu_vendor=str(data.get("gpu_vendor", "cpu")),
                vram_mb=int(data.get("vram_mb", 0)),
            )
        except Exception:
            # Safe fallback if hardware.json is missing/unreadable
            return HardwareProfile(
                tier="balanced", ram_mb=8192, cpu_cores=4, avx2=False,
                has_battery=False, disk_transport="unknown",
                gpu_vendor="cpu", vram_mb=0,
            )

    # -- Live telemetry readers ------------------------------------------

    def read_mem_psi_avg10(self) -> float:
        """Read memory pressure some.avg10 from /proc/pressure/memory."""
        try:
            psi_file = Path("/proc/pressure/memory")
            if psi_file.is_file():
                for line in psi_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("some "):
                        for token in line.split():
                            if token.startswith("avg10="):
                                return float(token.split("=", 1)[1])
        except Exception:
            pass
        return 0.0

    def on_battery(self) -> bool:
        """Return True if system is running on battery power."""
        if not self.hw.has_battery:
            return False
        try:
            ac_files = list(Path("/sys/class/power_supply").glob("A*/online"))
            if not ac_files:
                return False
            return all(f.read_text(encoding="utf-8").strip() == "0" for f in ac_files)
        except Exception:
            return False

    def read_cpu_load_avg(self) -> float:
        """Read 1-minute CPU load average."""
        try:
            load = Path("/proc/loadavg").read_text(encoding="utf-8").split()
            return float(load[0])
        except Exception:
            return 0.0

    # -- Mode evaluation --------------------------------------------------

    def evaluate_mode(self) -> str:
        """Determine the current operating mode from live telemetry."""
        psi = self.read_mem_psi_avg10()
        battery = self.on_battery()

        if psi > self.tier_policy.ram_psi_evict_threshold:
            return "pressure_relief"
        if battery:
            return "eco"
        if self.hw.tier == "workstation" and self.read_cpu_load_avg() < 1.0:
            return "performance"
        return "balanced"

    async def evaluate_and_act(
        self,
        llm_manager: Any | None = None,
        indexer: Any | None = None,
        evidence_log: Any | None = None,
    ) -> str:
        """Evaluate conditions, update mode, and notify subsystems."""
        new_mode = self.evaluate_mode()

        # Execute mode-specific actions
        if new_mode == "pressure_relief":
            if llm_manager and hasattr(llm_manager, "evict_idle_models"):
                await llm_manager.evict_idle_models(force=True)
            if indexer and hasattr(indexer, "pause"):
                await indexer.pause()
            self._set_scheduler_mode("scx_bpfland", "powersave")
        elif new_mode == "eco":
            if indexer and hasattr(indexer, "throttle"):
                await indexer.throttle(batch_size=2, interval_s=30)
            self._set_scheduler_mode("scx_bpfland", "powersave")
        else:  # balanced or performance
            if indexer and hasattr(indexer, "resume"):
                await indexer.resume()
            sched = "scx_lavd" if self.hw.tier == "workstation" else "scx_bpfland"
            mode_name = "auto" if new_mode == "performance" else "powersave"
            self._set_scheduler_mode(sched, mode_name)

        if new_mode != self.mode:
            old_mode = self.mode
            self.mode = new_mode
            if evidence_log and hasattr(evidence_log, "record"):
                await evidence_log.record("hw_mode_change", {
                    "mode": new_mode,
                    "previous_mode": old_mode,
                    "psi": self.read_mem_psi_avg10(),
                    "battery": self.on_battery(),
                })
            # Notify listeners
            for listener in self._mode_change_listeners:
                try:
                    if asyncio.iscoroutinefunction(listener):
                        await listener(new_mode, old_mode)
                    else:
                        listener(new_mode, old_mode)
                except Exception:
                    pass

        return new_mode

    def _set_scheduler_mode(self, sched: str, mode_name: str) -> None:
        """Switch sched_ext scheduler via scxctl (best effort)."""
        try:
            subprocess.run(
                ["scxctl", "switch", "--sched", sched, "--mode", mode_name],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass  # scxctl may not be available

    # -- Public query API --------------------------------------------------

    @property
    def is_lite(self) -> bool:
        return self.hw.tier in ("lite", "constrained")

    @property
    def is_workstation(self) -> bool:
        return self.hw.tier == "workstation"

    @property
    def is_battery_powered(self) -> bool:
        return self.on_battery()

    @property
    def memory_pressure_high(self) -> bool:
        return self.read_mem_psi_avg10() > 20.0

    def recommended_llm_model(self) -> str:
        """Return hardware-matched Ollama model name."""
        if self.hw.tier == "lite" or self.hw.vram_mb < 8000:
            return "qwen2.5:1.5b-instruct-q4_0"
        if self.hw.tier == "balanced":
            return "qwen2.5:7b-instruct-q4_0"
        return "qwen2.5:14b-instruct-q4_0"

    def recommended_slice_limits(self) -> dict[str, str]:
        """Return celina-ai.slice MemoryHigh/MemoryMax for this tier.
        CPUWeight=40, IOWeight=40 per master prompt specification;
        MemoryHigh/MemoryMax are hardware-scaled.
        """
        if self.hw.tier == "lite":
            return {"MemoryHigh": "3G", "MemoryMax": "4G", "CPUWeight": "40", "IOWeight": "40"}
        if self.hw.tier == "balanced":
            return {"MemoryHigh": "7G", "MemoryMax": "9G", "CPUWeight": "40", "IOWeight": "40"}
        return {"MemoryHigh": "16G", "MemoryMax": "22G", "CPUWeight": "40", "IOWeight": "40"}

    def register_mode_listener(self, listener: Any) -> None:
        """Register a callback for mode changes."""
        self._mode_change_listeners.append(listener)

    def __repr__(self) -> str:
        return (f"HardwareGovernor(tier={self.hw.tier}, mode={self.mode}, "
                f"ram={self.hw.ram_mb}MB, gpu={self.hw.gpu_vendor}, "
                f"vram={self.hw.vram_mb}MB, battery={self.hw.has_battery})")
