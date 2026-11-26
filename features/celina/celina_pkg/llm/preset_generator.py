"""macubuntu celina_pkg.llm.preset_generator — llama-server Router Mode preset generator.

Generates ~/.config/celina/models.ini for llama-server Router Mode
(--models-preset, --models-max, --sleep-idle-seconds, -np parallel slots,
q8_0 vs f16 KV cache) bound to /run/user/$UID/celina/llama.sock.

Hardware-adaptive: model count, KV cache precision, context size, and model
lineup scale with ai_tier from /etc/macubuntu/hardware.json:

- 8gb: 1 model max, q8_0 KV, 1 parallel slot, BitNet b1.58 / LFM2 CPU-efficient
       models for battery/8GB Guard mode
- 16gb: 2 model max, q8_0 KV, 2 parallel slots, LFM2 Fast + Qwen/Gemma models
- 32gb: 3 model max, f16 KV, 4 parallel slots, Fast/Deep/Reasoning large models

NPU-aware: when npu_available is true, voice ASR / VAD / OCR workloads are
routed to the NPU (Intel OpenVINO / AMD XDNA) at 2-3W instead of 15-25W on
iGPU. Conversational LLM KV-cache stays on Vulkan/CUDA/CPU (NPU is fixed-shape
graph only, unsuitable for per-token generation).

Uses only stdlib: json, pathlib, os. No PyTorch, no network.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Maps hardware ai_tier -> llama-server Router Mode configuration
#
# CPU-efficient neural architectures for low-RAM / battery profiles:
#   - BitNet b1.58: ternary {-1,0,+1} weights, 2.37x-6.17x faster CPU inference,
#     71-82% lower energy. Ideal for Gear 1 Fast/Tactical on 8 GB.
#   - LFM2 (Liquid Foundation Models): 2x faster CPU prefill/decode than
#     equivalent Transformers, fits easily in 8 GB / 16 GB.
TIER_LLM_CONFIGS: dict[str, dict[str, Any]] = {
    "8gb": {
        "models_max": 1,
        "sleep_idle_seconds": 120,
        "parallel_slots": 1,
        "kv_cache_type": "q8_0",
        "ctx_default": 8192,
        "cpu_efficient": True,
        "prefer_npu_for": ["asr", "vad", "ocr", "embeddings"],
        "models": {
            "fast": {
                "file": "BitNet-b1.58-3B-ternary-Q4_K_M.gguf",
                "ctx_size": 8192,
                "architecture": "bitnet",
                "device": "cpu",
            },
            "tactical": {
                "file": "LFM2-1.2B-Q4_K_M.gguf",
                "ctx_size": 8192,
                "architecture": "lfm2",
                "device": "cpu",
            },
        },
    },
    "16gb": {
        "models_max": 2,
        "sleep_idle_seconds": 300,
        "parallel_slots": 2,
        "kv_cache_type": "q8_0",
        "ctx_default": 16384,
        "cpu_efficient": False,
        "prefer_npu_for": ["asr", "vad", "ocr", "embeddings"],
        "models": {
            "fast": {
                "file": "LFM2-3B-Q4_K_M.gguf",
                "ctx_size": 16384,
                "architecture": "lfm2",
                "device": "cpu",
            },
            "multimodal": {
                "file": "gemma-4-E4B-it-Q4_0.gguf",
                "ctx_size": 24576,
                "architecture": "transformer",
                "device": "auto",
            },
            "deep": {
                "file": "Qwen3-14B-Q4_K_M.gguf",
                "ctx_size": 16384,
                "architecture": "transformer",
                "device": "auto",
            },
        },
    },
    "32gb": {
        "models_max": 3,
        "sleep_idle_seconds": 900,
        "parallel_slots": 4,
        "kv_cache_type": "f16",
        "ctx_default": 32768,
        "cpu_efficient": False,
        "prefer_npu_for": ["asr", "vad", "ocr", "embeddings"],
        "models": {
            "fast": {
                "file": "gemma-4-E4B-it-Q4_0.gguf",
                "ctx_size": 32768,
                "architecture": "transformer",
                "device": "auto",
            },
            "deep": {
                "file": "Qwen3.6-35B-A3B-Q4_K_M.gguf",
                "ctx_size": 65536,
                "architecture": "transformer",
                "device": "auto",
            },
            "reasoning": {
                "file": "Qwen3.8-27B-Q4_K_M.gguf",
                "ctx_size": 49152,
                "architecture": "transformer",
                "device": "auto",
            },
        },
    },
}

# Default fallback when ai_tier is unknown or unreadable
DEFAULT_AI_TIER = "8gb"


def _read_hardware_json(hw_path: Path = Path("/etc/macubuntu/hardware.json")) -> dict[str, Any]:
    """Read hardware contract JSON, returning empty dict on failure."""
    if not hw_path.is_file():
        return {}
    try:
        return json.loads(hw_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def generate_router_preset(
    *,
    hw_path: Path = Path("/etc/macubuntu/hardware.json"),
    models_dir: Path | None = None,
    out_ini: Path | None = None,
    socket_dir: Path | None = None,
) -> tuple[Path, list[str]]:
    """Generate models.ini and the llama-server router CLI command for the
    active hardware tier.

    Parameters
    ----------
    hw_path : Path
        Path to /etc/macubuntu/hardware.json.
    models_dir : Path | None
        Directory containing GGUF model files. Defaults to
        ~/.cache/celina/models.
    out_ini : Path | None
        Output path for models.ini. Defaults to
        ~/.config/celina/models.ini.
    socket_dir : Path | None
        Directory for the llama-server Unix socket. Defaults to
        /run/user/$UID/celina.

    Returns
    -------
    (ini_path, cmd)
        ini_path : Path to the generated models.ini file.
        cmd : list[str] — llama-server router command (binary + args),
              not including the binary name itself (caller prepends
              'llama-server').

    """
    hw_path = Path(hw_path)
    hw = _read_hardware_json(hw_path)
    ai_tier = str(hw.get("ai_tier", DEFAULT_AI_TIER))
    ram_mb = int(hw.get("ram_mb", 8192))
    cpu_cores = int(hw.get("cpu_cores", 4))
    npu_available = bool(hw.get("npu_available", False))
    npu_vendor = str(hw.get("npu_vendor", "none"))
    npu_backend = str(hw.get("npu_backend", "none"))

    cfg = TIER_LLM_CONFIGS.get(ai_tier, TIER_LLM_CONFIGS[DEFAULT_AI_TIER])

    m_dir = models_dir or (Path.home() / ".cache" / "celina" / "models")
    m_dir = Path(m_dir)
    ini_path = Path(out_ini or (Path.home() / ".config" / "celina" / "models.ini"))
    ini_path.parent.mkdir(parents=True, exist_ok=True)

    threads = max(2, cpu_cores - 2)
    lines: list[str] = [
        "version = 1",
        "",
        "[*]",
        f"ctx-size = {cfg['ctx_default']}",
        "n-gpu-layers = auto",
        "flash-attn = on",
        "fit = on",
        f"threads = {threads}",
        # FIX (Patch 4): parallel=1 so each model alias gets the FULL ctx-size.
        # In llama-server, setting -np N divides -c evenly across slots—e.g.
        # ctx-size=32768 with parallel=4 silently shrinks every request to 8K.
        # Omitting manual parallel (or forcing 1) uses the unified shared KV
        # buffer bounded by --kv-unified-per-slot, giving each alias its
        # declared ctx-size. Long-context aliases (deep/reasoning) keep
        # parallel=1; multi-slot fast aliases may override parallel in their
        # own [alias] section if needed.
        "parallel = 1",
        f"cache-type-k = {cfg['kv_cache_type']}",
        f"cache-type-v = {cfg['kv_cache_type']}",
        f"sleep-idle-seconds = {cfg['sleep_idle_seconds']}",
        "jinja = on",
        f"# cpu_efficient = {str(cfg.get('cpu_efficient', False)).lower()}",
        f"# prefer_npu_for = {', '.join(cfg.get('prefer_npu_for', []))}",
        f"# npu_available = {str(npu_available).lower()}",
        f"# npu_vendor = {npu_vendor}",
        f"# npu_backend = {npu_backend}",
        "",
    ]

    for alias, m in cfg["models"].items():
        model_path = m_dir / m["file"]
        lines += [
            f"[{alias}]",
            f"model = {model_path}",
            f"ctx-size = {m['ctx_size']}",
            f"# architecture = {m.get('architecture', 'transformer')}",
            f"# device = {m.get('device', 'auto')}",
            "",
        ]

    ini_path.write_text("\n".join(lines), encoding="utf-8")

    uid = os.getuid()
    sock_dir = Path(socket_dir or (Path(f"/run/user/{uid}") / "celina"))
    sock = str(sock_dir / "llama.sock")

    cmd = [
        "--host", sock,
        "--models-preset", str(ini_path),
        "--models-max", str(cfg["models_max"]),
    ]
    return ini_path, cmd


def router_command(
    *,
    hw_path: Path = Path("/etc/macubuntu/hardware.json"),
    models_dir: Path | None = None,
    out_ini: Path | None = None,
    socket_dir: Path | None = None,
    binary: str = "llama-server",
) -> str:
    """Return a shell-ready llama-server router command string.

    Convenience wrapper around generate_router_preset() that prepends the
    binary name and shell-quotes all arguments.
    """
    import shlex
    _ini_path, cmd = generate_router_preset(
        hw_path=hw_path,
        models_dir=models_dir,
        out_ini=out_ini,
        socket_dir=socket_dir,
    )
    return " ".join(shlex.quote(a) for a in [binary] + cmd)


def npu_workload_routing(
    *,
    hw_path: Path = Path("/etc/macubuntu/hardware.json"),
) -> dict[str, Any]:
    """Return NPU workload routing recommendations based on detected hardware.

    Returns a dict mapping workload type -> recommended device:
      - "asr": Whisper speech-to-text -> NPU when available, else CPU
      - "vad": Voice activity detection -> NPU when available, else CPU
      - "ocr": Optical character recognition -> NPU when available, else CPU
      - "embeddings": Spotlight/semantic embeddings -> NPU when available, else CPU
      - "llm_chat": Conversational LLM -> never NPU (fixed-shape graph), CPU/Vulkan/CUDA

    Fail-closed: when NPU is unavailable, all workloads route to CPU.
    """
    hw_path = Path(hw_path)
    hw = _read_hardware_json(hw_path)
    npu_available = bool(hw.get("npu_available", False))
    npu_vendor = str(hw.get("npu_vendor", "none"))
    npu_backend = str(hw.get("npu_backend", "none"))

    npu_device = "npu" if (npu_available and npu_backend != "none") else "cpu"

    return {
        "npu_available": npu_available,
        "npu_vendor": npu_vendor,
        "npu_backend": npu_backend,
        "routing": {
            "asr": npu_device,          # Whisper ASR -> NPU (always-on, low power)
            "vad": npu_device,          # Voice activity detection -> NPU
            "ocr": npu_device,          # OCR / vision -> NPU
            "embeddings": npu_device,   # Spotlight embeddings -> NPU
            "llm_chat": "cpu",          # Conversational LLM -> CPU/Vulkan/CUDA (NOT NPU)
        },
        "note": (
            "NPU is fixed-shape graph only — unsuitable for per-token LLM KV-cache."
            " Conversational LLMs stay on CPU/Vulkan/CUDA."
        ),
    }


if __name__ == "__main__":  # pragma: no cover - CLI smoke test
    ini_path, cmd = generate_router_preset()
    print("INI: " + str(ini_path))
    print("CMD: llama-server " + " ".join(cmd))
    print()
    print("NPU routing:")
    import json as _json
    print(_json.dumps(npu_workload_routing(), indent=2))
