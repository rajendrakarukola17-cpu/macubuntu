"""macubuntu celina_pkg.llm.neural_backends — Adaptive Neural Backend Launcher.

Selects automatically between:
  1. 1-bit Ternary BitNet (I2_S x86 CPU) — battery / 8gb / CPU-only mode
     (2.37x–6.17x faster CPU decode, 71–82% lower energy)
  2. Liquid LFM2.5-8B-A1B MoE + DSpark speculative sidecar — 16gb/32gb
     (exact greedy output, minimal RAM overhead, significant token/s speedup)
  3. Standard llama-server fallback

Reads hardware.json for ai_tier, gpu_vendor, cpu_cores, and battery state.
Fail-closed: falls back to standard llama-server when model files are absent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NeuralLaunchPlan:
    backend: str  # "bitnet_cpu" | "llama_dspark" | "llama_standard"
    command: list[str]
    socket_path: str
    reason: str


def _read_hardware_json(hw_path: Path) -> dict:
    if not hw_path.is_file():
        return {}
    try:
        return json.loads(hw_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_on_battery(hw: dict) -> bool:
    """Best-effort battery discharge check via /sys/class/power_supply."""
    try:
        import glob

        for bat in glob.glob("/sys/class/power_supply/BAT*"):
            status_path = Path(bat) / "status"
            if status_path.is_file():
                status = status_path.read_text(encoding="utf-8").strip()
                if status in {"Discharging", "Not charging"}:
                    return True
    except Exception:
        pass
    return False


def select_neural_backend(
    *,
    hw_path: Path = Path("/etc/macubuntu/hardware.json"),
    models_dir: Path | None = None,
    on_battery: bool | None = None,
) -> NeuralLaunchPlan:
    """Select the optimal neural backend for the current hardware profile.

    Returns a NeuralLaunchPlan with backend, command, socket_path, and reason.
    Fail-closed: falls back to standard llama-server when preferred model
    files are absent.
    """
    hw_path = Path(hw_path)
    hw = _read_hardware_json(hw_path)
    ai_tier = str(hw.get("ai_tier", "8gb"))
    gpu_vendor = str(hw.get("gpu_vendor", "cpu"))
    cores = max(2, int(hw.get("cpu_cores", 4)))

    if on_battery is None:
        on_battery = _is_on_battery(hw)

    m_dir = Path(models_dir) if models_dir else (Path.home() / ".cache" / "celina" / "models")
    uid = os.getuid()
    sock = f"/run/user/{uid}/celina/llama.sock"

    # ------------------------------------------------------------------
    # 1. Eco / 8GB CPU-Only -> 1-Bit Ternary BitNet (I2_S x86 kernels)
    #    2.37x–6.17x faster CPU decode, 71–82% lower energy vs FP16
    # ------------------------------------------------------------------
    bitnet_model = m_dir / "BitNet-b1.58-2B-4T-i2_s.gguf"
    if (ai_tier == "8gb" or (on_battery and gpu_vendor == "cpu")) and bitnet_model.exists():
        threads = max(2, cores // 2 if on_battery else cores - 1)
        cmd = [
            "bitnet-server",
            "-m", str(bitnet_model),
            "--host", sock,
            "-t", str(threads),
            "-c", "4096",
        ]
        return NeuralLaunchPlan(
            backend="bitnet_cpu",
            command=cmd,
            socket_path=sock,
            reason=(
                f"1-bit I2_S ternary CPU inference (ai_tier={ai_tier}, "
                f"battery={on_battery}, threads={threads})"
            ),
        )

    # ------------------------------------------------------------------
    # 2. 16GB / 32GB -> Liquid LFM2.5-8B-A1B MoE + DSpark speculative sidecar
    #    Exact greedy output, minimal RAM overhead, token/s speedup
    # ------------------------------------------------------------------
    lfm_target = m_dir / "LFM2.5-8B-A1B-Q4_K_M.gguf"
    lfm_draft = m_dir / "LFM2.5-8B-A1B-DSpark-Q4_K_M.gguf"
    if ai_tier in {"16gb", "32gb"} and lfm_target.exists() and lfm_draft.exists():
        kv_type = "f16" if ai_tier == "32gb" else "q8_0"
        ctx = "32768" if ai_tier == "32gb" else "16384"
        cmd = [
            "llama-server",
            "-m", str(lfm_target),
            "-md", str(lfm_draft),
            "--spec-type", "draft-dspark",
            "--spec-draft-n-max", "10",
            "--spec-draft-n-min", "0",
            "--host", sock,
            "-ngl", "auto",
            "-ngld", "auto",
            "-fa", "on",
            "-fit", "on",
            "-c", ctx,
            "--cache-type-k", kv_type,
            "--cache-type-v", kv_type,
            "-t", str(max(2, cores - 2)),
        ]
        return NeuralLaunchPlan(
            backend="llama_dspark",
            command=cmd,
            socket_path=sock,
            reason=(
                f"Liquid LFM2.5 + DSpark speculative sidecar "
                f"(ai_tier={ai_tier}, kv={kv_type}, ctx={ctx})"
            ),
        )

    # ------------------------------------------------------------------
    # 3. Default Fallback -> Standard llama-server with auto-fit
    # ------------------------------------------------------------------
    fallback_model = m_dir / (
        "Qwen3.5-4B-Q4_K_M.gguf" if ai_tier == "8gb" else "gemma-4-E4B-it-Q4_0.gguf"
    )
    cmd = [
        "llama-server",
        "-m", str(fallback_model),
        "--host", sock,
        "-ngl", "auto",
        "-fa", "on",
        "-fit", "on",
        "-c", "8192" if ai_tier == "8gb" else "16384",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "-t", str(max(2, cores - 2)),
    ]
    return NeuralLaunchPlan(
        backend="llama_standard",
        command=cmd,
        socket_path=sock,
        reason=f"Standard llama-server fallback (ai_tier={ai_tier})",
    )


def launch_plan_summary(plan: NeuralLaunchPlan) -> str:
    """Human-readable one-line summary of a NeuralLaunchPlan."""
    return (
        f"[{plan.backend}] {plan.reason} | socket={plan.socket_path} | "
        f"cmd={' '.join(plan.command)}"
    )
