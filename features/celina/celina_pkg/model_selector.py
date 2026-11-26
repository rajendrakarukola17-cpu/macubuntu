"""Celina AI (Celly) - Unified Model Selector & Router (v4.5, Update 27)

Single source of truth for ALL model decisions across Celina:

  • Whisper STT model (tiny/base/small/medium/large-v3) — by RAM, language, accuracy-vs-speed
  • Ollama LLM model (qwen3:1.7b … qwen3.5:35b-a3b) — by RAM, GPU/NPU, task class
  • Edge0 GGUF model (Edge0-8B / Edge0-35B compressed/standard) — by RAM, disk, binary availability
  • Gear 0.5 smolLM GGUF — by RAM, llama.cpp availability
  • Cloud provider routing (parallel ensemble vs single-provider failover) — by task complexity
  • Heavy-task decomposition — split work among providers, reassemble locally

Every decision is driven by detected system resources (RAM, disk, GPU/NPU, CPU cores)
and can be overridden via environment variables or /model /whisper-model commands.

No model is downloaded automatically; the selector reports what SHOULD be installed.
The installer / stages perform the actual `ollama pull` / `pip install` / edge0 download.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from typing import Any

log = logging.getLogger("celina.model_selector")

# ---------------------------------------------------------------------------
# Resource detection
# ---------------------------------------------------------------------------


def detect_total_ram_gb() -> float:
    """Detect total system RAM in GiB."""
    try:
        import psutil

        return round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024**2), 1)
    except Exception:
        pass
    return 8.0  # Safe default


def detect_available_ram_mb() -> int:
    """Detect currently available RAM in MiB."""
    try:
        import psutil

        return psutil.virtual_memory().available // (1024**2)
    except Exception:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:
        pass
    return 2048


def detect_cpu_cores() -> int:
    """Detect logical CPU core count."""
    try:
        import os as _os

        return _os.cpu_count() or 4
    except Exception:
        return 4


def detect_free_disk_gb(path: str = "/") -> float:
    """Detect free disk space in GiB."""
    try:
        stat = shutil.disk_usage(path)
        return round(stat.free / (1024**3), 1)
    except Exception:
        return 0.0


def has_gpu() -> bool:
    """Check if any GPU render node exists."""
    from pathlib import Path as _P

    return any(_P("/dev/dri").glob("renderD*")) if _P("/dev/dri").exists() else False


def has_npu() -> bool:
    """Check if any NPU device exists."""
    from pathlib import Path as _P

    return any(_P("/dev/accel").glob("accel*")) if _P("/dev/accel").exists() else False


def _detect_module_gpu() -> bool:
    """Module-level GPU detection (not shadowed by parameter names)."""
    return has_gpu()


def _detect_module_npu() -> bool:
    """Module-level NPU detection (not shadowed by parameter names)."""
    return has_npu()


def binary_available(name: str) -> bool:
    """Check if a binary exists on PATH."""
    return shutil.which(name) is not None


# ---------------------------------------------------------------------------
# Unified 3-Tier RAM classifier (matches core/detect.sh detect_hw_tier())
# ---------------------------------------------------------------------------

CONSTRAINED_RAM_MB = 10240   # <= 10 GB / 8 GB class
BALANCED_RAM_MB = 22528      # 10-22 GB / 16 GB class


def get_ram_tier(ram_mb: int | None = None) -> str:
    """Return the canonical 3-tier RAM classifier: CONSTRAINED | BALANCED | HEADROOM.

    Cutoffs match core/detect.sh detect_hw_tier() exactly:
      CONSTRAINED: <= 10240 MB (<= ~10 GB)
      BALANCED:    10241 - 22528 MB (~16 GB)
      HEADROOM:    >= 22529 MB (~32 GB+)
    """
    if ram_mb is None:
        ram_gb = detect_total_ram_gb()
        ram_mb = int(ram_gb * 1024)
    if ram_mb <= CONSTRAINED_RAM_MB:
        return "CONSTRAINED"
    if ram_mb <= BALANCED_RAM_MB:
        return "BALANCED"
    return "HEADROOM"


# ---------------------------------------------------------------------------
# Whisper STT model selection
# ---------------------------------------------------------------------------


class WhisperModel(str, Enum):
    TINY = "tiny"        # ~39 MB, fastest, lowest accuracy (~8-10x realtime on CPU)
    BASE = "base"        # ~74 MB, good balance (default)
    SMALL = "small"      # ~244 MB, better accuracy
    MEDIUM = "medium"    # ~769 MB, high accuracy
    LARGE_V3 = "large-v3"  # ~1550 MB, best accuracy, slowest


WHISPER_SPECS: dict[str, dict[str, Any]] = {
    "tiny": {
        "size_mb": 39,
        "ram_mb": 150,
        "disk_mb": 60,
        "relative_speed": 10.0,   # ~10x realtime on CPU
        "accuracy": "low",
        "languages": "en + major",
        "best_for": "8 GB RAM, wake-word + short commands, maximum speed",
    },
    "base": {
        "size_mb": 74,
        "ram_mb": 300,
        "disk_mb": 120,
        "relative_speed": 5.0,
        "accuracy": "medium",
        "languages": "en + common",
        "best_for": "8-16 GB RAM, general dictation (default)",
    },
    "small": {
        "size_mb": 244,
        "ram_mb": 800,
        "disk_mb": 350,
        "relative_speed": 2.5,
        "accuracy": "high",
        "languages": "en + TE + HI + many",
        "best_for": "16 GB RAM, multilingual (EN/TE/HI), meetings",
    },
    "medium": {
        "size_mb": 769,
        "ram_mb": 2000,
        "disk_mb": 900,
        "relative_speed": 1.5,
        "accuracy": "very high",
        "languages": "all",
        "best_for": "24+ GB RAM, high-stakes transcription",
    },
    "large-v3": {
        "size_mb": 1550,
        "ram_mb": 3500,
        "disk_mb": 1800,
        "relative_speed": 0.7,
        "accuracy": "best",
        "languages": "all + rare",
        "best_for": "32+ GB RAM, server-grade transcription",
    },
}


def select_whisper_model(
    ram_gb: float | None = None,
    prefer_language: str | None = None,
    prefer_accuracy: str | None = None,
    max_disk_mb: int | None = None,
) -> dict[str, Any]:
    """Select the best Whisper STT model for the current system.

    Decision factors:
      1. RAM — must fit model + VAD + Piper TTS + Celina core within available RAM
      2. Language — EN/TE/HI multilingual needs at least 'small'
      3. Accuracy preference — 'high' forces small+, 'best' forces medium+
      4. Disk — must have space for model file
      5. User override via CELINA_WHISPER_MODEL env var

    Returns dict with model name, specs, reason, and install command.
    """
    ram = ram_gb if ram_gb is not None else detect_total_ram_gb()
    disk = detect_free_disk_gb() * 1024  # MB
    env_override = os.environ.get("CELINA_WHISPER_MODEL", "").strip().lower()

    # User override takes priority (validated)
    if env_override and env_override in WHISPER_SPECS:
        spec = WHISPER_SPECS[env_override]
        return {
            "model": env_override,
            "spec": spec,
            "reason": "User-specified via CELINA_WHISPER_MODEL",
            "install_cmd": f"pip install faster-whisper  # model '{env_override}' auto-downloads on first use",
            "ram_mb": spec["ram_mb"],
            "disk_mb": spec["disk_mb"],
        }

    # Accuracy preference
    min_accuracy = (prefer_accuracy or "").lower()
    if min_accuracy in ("best", "maximum"):
        candidates = ["large-v3", "medium", "small"]
    elif min_accuracy in ("high", "better"):
        candidates = ["small", "medium", "large-v3"]
    else:
        candidates = ["tiny", "base", "small", "medium", "large-v3"]

    # Language need — EN/TE/HI multilingual benefits from 'small' minimum
    needs_multilingual = bool(prefer_language and prefer_language.lower() in {"te", "hi", "te+hi", "multilingual"})
    if needs_multilingual and "tiny" in candidates:
        candidates.remove("tiny")
    if needs_multilingual and "base" in candidates:
        candidates.remove("base")

    # RAM-aware filtering: reserve ~1.5 GB for Celina core + TTS + VAD
    reserve_ram_mb = 1500
    available_mb = ram * 1024 - reserve_ram_mb

    chosen = None
    for cand in candidates:
        spec = WHISPER_SPECS[cand]
        if spec["ram_mb"] <= available_mb and (max_disk_mb is None or spec["disk_mb"] <= max_disk_mb):
            chosen = cand
            break  # First fit in priority order (candidates already sorted by accuracy)

    # Fallback to tiny if nothing fits
    if chosen is None:
        chosen = "tiny"

    spec = WHISPER_SPECS[chosen]
    reason_parts = [f"RAM={ram:.1f} GB", f"available={available_mb} MB"]
    if needs_multilingual:
        reason_parts.append("multilingual EN/TE/HI")
    reason = f"Whisper {chosen} — " + ", ".join(reason_parts)

    return {
        "model": chosen,
        "spec": spec,
        "reason": reason,
        "install_cmd": f"pip install faster-whisper  # model '{chosen}' auto-downloads on first use (~{spec['size_mb']} MB)",
        "ram_mb": spec["ram_mb"],
        "disk_mb": spec["disk_mb"],
    }


# ---------------------------------------------------------------------------
# Ollama LLM model selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OllamaModelSpec:
    tag: str
    arch: str
    total_params: str
    active_params: str
    size_gb: float
    min_ram_gb: int
    recommended_ram_gb: int
    note: str
    best_for: str
    vision: bool = False


OLLAMA_CATALOG: dict[str, OllamaModelSpec] = {
    "qwen3:1.7b": OllamaModelSpec(
        tag="qwen3:1.7b", arch="Dense", total_params="1.7B", active_params="1.7B",
        size_gb=1.4, min_ram_gb=6, recommended_ram_gb=8,
        note="Zero-lag default for 8 GB RAM (~1.8 GB live RAM)",
        best_for="8 GB RAM, everyday tool-calling, fast reflex",
    ),
    "granite3.1-moe:1b": OllamaModelSpec(
        tag="granite3.1-moe:1b", arch="Sparse MoE", total_params="1B", active_params="0.4B",
        size_gb=1.4, min_ram_gb=6, recommended_ram_gb=8,
        note="Featherweight Sparse MoE (only 400M params fire/token)",
        best_for="6-8 GB RAM, lowest CPU & RAM, basic tasks",
    ),
    "granite3.1-moe:3b": OllamaModelSpec(
        tag="granite3.1-moe:3b", arch="Sparse MoE", total_params="3B", active_params="0.8B",
        size_gb=2.0, min_ram_gb=8, recommended_ram_gb=12,
        note="Fast Sparse MoE (800M params fire/token; ~2.4 GB live RAM)",
        best_for="8-12 GB RAM, reliable tool-calling",
    ),
    "qwen2.5:3b": OllamaModelSpec(
        tag="qwen2.5:3b", arch="Dense", total_params="3B", active_params="3B",
        size_gb=1.9, min_ram_gb=8, recommended_ram_gb=12,
        note="Smarter 3B dense (~2.2 GB live RAM; fast & reliable offline)",
        best_for="8-12 GB RAM, everyday reasoning",
    ),
    "qwen3:4b": OllamaModelSpec(
        tag="qwen3:4b", arch="Dense", total_params="4B", active_params="4B",
        size_gb=2.5, min_ram_gb=11, recommended_ram_gb=16,
        note="Stronger 4B dense (~3.4 GB live RAM)",
        best_for="12-16 GB RAM, stronger reasoning, coding",
    ),
    "qwen3.5:4b": OllamaModelSpec(
        tag="qwen3.5:4b", arch="Dense Vision", total_params="4B", active_params="4B",
        size_gb=3.5, min_ram_gb=8, recommended_ram_gb=12,
        note="Multimodal text + image vision on 8 GB RAM",
        best_for="8-16 GB RAM, vision tasks (images, screenshots)",
        vision=True,
    ),
    "qwen3.5:9b": OllamaModelSpec(
        tag="qwen3.5:9b", arch="Dense Vision", total_params="9B", active_params="9B",
        size_gb=6.6, min_ram_gb=14, recommended_ram_gb=20,
        note="Strong 16 GB RAM daily driver (text + vision)",
        best_for="16-24 GB RAM, complex reasoning + vision",
        vision=True,
    ),
    "qwen3:30b-a3b": OllamaModelSpec(
        tag="qwen3:30b-a3b", arch="Sparse MoE", total_params="30.5B", active_params="3.3B",
        size_gb=19.0, min_ram_gb=24, recommended_ram_gb=32,
        note="30B MoE at 3.3B CPU speed (NVMe mmap if less RAM)",
        best_for="24-32 GB RAM, heavy reasoning, coding, analysis",
    ),
    "gemma4:26b": OllamaModelSpec(
        tag="gemma4:26b", arch="Sparse MoE Vision", total_params="25.2B", active_params="3.8B",
        size_gb=17.0, min_ram_gb=22, recommended_ram_gb=32,
        note="8 of 128 experts fire/token (3.8B speed, 26B brains + vision)",
        best_for="24-32 GB RAM, vision + reasoning",
        vision=True,
    ),
    "qwen3.5:35b-a3b": OllamaModelSpec(
        tag="qwen3.5:35b-a3b", arch="Sparse MoE Vision", total_params="34.7B", active_params="3B",
        size_gb=22.0, min_ram_gb=28, recommended_ram_gb=40,
        note="35B multimodal MoE firing ~3B/token",
        best_for="32+ GB RAM, maximum local capability",
        vision=True,
    ),
}


def select_ollama_model(
    ram_gb: float | None = None,
    _has_gpu_param: bool | None = None,
    _has_npu_param: bool | None = None,
    prefer_vision: bool = False,
    prefer_heavy: bool = False,
    max_disk_gb: float | None = None,
) -> dict[str, Any]:
    """Select the best Ollama LLM model for the current system.

    Decision factors:
      1. RAM — model's min_ram_gb must be <= detected RAM (with reserve)
      2. GPU/NPU — GPU enables larger models via mmap; NPU routes to decoupled service
      3. Vision — if vision tasks expected, prefer vision-capable models
      4. Heavy — if heavy tasks expected, prefer larger MoE models
      5. User override via CELINA_LOCAL_MODEL env var

    Returns dict with model tag, specs, reason, install command.
    """
    ram = ram_gb if ram_gb is not None else detect_total_ram_gb()
    disk = detect_free_disk_gb()
    has_gpu = _has_gpu_param if _has_gpu_param is not None else _detect_module_gpu()
    has_npu = _has_npu_param if _has_npu_param is not None else _detect_module_npu()
    env_override = os.environ.get("CELINA_LOCAL_MODEL", "").strip()

    # User override takes priority
    if env_override and env_override in OLLAMA_CATALOG:
        spec = OLLAMA_CATALOG[env_override]
        return {
            "model": env_override,
            "spec": spec,
            "reason": "User-specified via CELINA_LOCAL_MODEL",
            "install_cmd": f"ollama pull {env_override}",
            "ram_gb": spec.min_ram_gb,
            "disk_gb": spec.size_gb,
        }

    # Filter by RAM (reserve ~2 GB for OS + Celina core)
    reserve_ram_gb = 2.0
    available_ram = ram - reserve_ram_gb
    tier = get_ram_tier(int(ram * 1024))

    # CONSTRAINED (<= 10 GB): never auto-select qwen3.5:4b vision (3.5 GB live RAM
    # risks OOM thrash with browser + GNOME). Cap default auto-selection at
    # qwen3:1.7b / granite3.1-moe:3b. Users can still override via CELINA_LOCAL_MODEL.
    if tier == "CONSTRAINED":
        excluded_tags = {"qwen3.5:4b", "qwen3.5:9b", "qwen3:4b"}
    else:
        excluded_tags = set()

    candidates = []
    for tag, spec in OLLAMA_CATALOG.items():
        if tag in excluded_tags:
            continue
        if spec.min_ram_gb <= available_ram:
            if max_disk_gb is not None and spec.size_gb > max_disk_gb:
                continue
            candidates.append((tag, spec))

    if not candidates:
        # Fallback to smallest
        chosen_tag = "granite3.1-moe:1b"
        spec = OLLAMA_CATALOG[chosen_tag]
        return {
            "model": chosen_tag,
            "spec": spec,
            "reason": f"RAM={ram:.1f} GB — no catalog model fits, using smallest (granite3.1-moe:1b)",
            "install_cmd": f"ollama pull {chosen_tag}",
            "ram_gb": spec.min_ram_gb,
            "disk_gb": spec.size_gb,
        }

    # Sort by capability (size_gb descending) but prefer vision if requested
    def sort_key(item):
        tag, spec = item
        vision_bonus = 1 if (prefer_vision and spec.vision) else 0
        heavy_bonus = 1 if prefer_heavy else 0
        return (spec.active_params != "1.7B", -vision_bonus, -heavy_bonus, -spec.size_gb)

    candidates.sort(key=sort_key)
    chosen_tag, spec = candidates[0]

    # GPU/NPU note
    hw_note = ""
    if has_npu:
        hw_note += " NPU decoupled (celina-npu-inference.service:11435)"
    elif has_gpu:
        hw_note += " GPU-accelerated Ollama"
    else:
        hw_note += " CPU-only"

    reason = f"RAM={ram:.1f} GB (avail={available_ram:.1f} GB){hw_note} → {chosen_tag}"

    return {
        "model": chosen_tag,
        "spec": spec,
        "reason": reason,
        "install_cmd": f"ollama pull {chosen_tag}",
        "ram_gb": spec.min_ram_gb,
        "disk_gb": spec.size_gb,
    }


# ---------------------------------------------------------------------------
# Edge0 model selection
# ---------------------------------------------------------------------------


class Edge0Model(str, Enum):
    EDGE0_8B = "edge0-8b"
    EDGE0_35B_COMPRESSED = "edge0-35b-compressed"
    EDGE0_35B_STANDARD = "edge0-35b-standard"


EDGE0_SPECS: dict[str, dict[str, Any]] = {
    "edge0-8b": {
        "name": "Edge0-8B-A1B-preview",
        "active_ram_gb": 1.0,
        "disk_gb": 4.2,
        "source": "https://github.com/edge0-ai/edge0-8b/releases",
        "format": "GGUF Q4_K_M",
        "best_for": "8 GB RAM, 24-25 tok/s streaming from SSD",
    },
    "edge0-35b-compressed": {
        "name": "Edge0-35B-A3B-preview (compressed)",
        "active_ram_gb": 1.8,
        "disk_gb": 23.0,
        "source": "https://github.com/edge0-ai/edge0-35b/releases",
        "format": "GGUF Q4_K/Q3_K_S, no LRU cache, Q4 KV cache, 4K cap",
        "best_for": "16 GB RAM, complex reasoning, coding",
    },
    "edge0-35b-standard": {
        "name": "Edge0-35B-A3B-preview (standard)",
        "active_ram_gb": 2.9,
        "disk_gb": 23.0,
        "source": "https://github.com/edge0-ai/edge0-35b/releases",
        "format": "GGUF standard",
        "best_for": "24+ GB RAM, maximum local capability",
    },
}


def select_edge0_model(
    ram_gb: float | None = None,
    disk_gb: float | None = None,
    edge0_binary_available: bool | None = None,
) -> dict[str, Any]:
    """Select the best Edge0 model for the current system.

    Edge0 models come from the Edge0 AI project GitHub releases as GGUF files.
    They are NOT Ollama models — they run via the `edge0-infer` binary which
    streams weights from disk (mmap), keeping active RAM low.

    Installation: download the GGUF from the Edge0 GitHub releases page to
    ~/.local/share/celina/models/edge0/ and ensure `edge0-infer` is on PATH.

    Returns dict with model id, specs, source URL, reason, install steps.
    """
    ram = ram_gb if ram_gb is not None else detect_total_ram_gb()
    disk = disk_gb if disk_gb is not None else detect_free_disk_gb()
    has_binary = edge0_binary_available if edge0_binary_available is not None else binary_available("edge0-infer")

    env_override = os.environ.get("CELINA_EDGE0_MODEL", "").strip().lower()
    if env_override and env_override in EDGE0_SPECS:
        spec = EDGE0_SPECS[env_override]
        return {
            "model": env_override,
            "spec": spec,
            "edge0_available": has_binary,
            "reason": "User-specified via CELINA_EDGE0_MODEL",
            "install_steps": [
                f"1. Download {spec['name']} GGUF from {spec['source']}",
                "2. Place at ~/.local/share/celina/models/edge0/<model>.gguf",
                "3. Install edge0-infer binary and ensure it's on PATH",
                "4. Set CELINA_EDGE0_MODEL=<model-id>",
            ],
        }

    # RAM-aware selection
    if ram <= 8.0:
        chosen = "edge0-8b"
    elif ram <= 16.0:
        chosen = "edge0-35b-compressed" if disk >= 23.0 else "edge0-8b"
    else:
        chosen = "edge0-35b-standard" if disk >= 23.0 else "edge0-35b-compressed"

    spec = EDGE0_SPECS[chosen]
    if disk < spec["disk_gb"]:
        # Step down
        for alt in ("edge0-8b", "edge0-35b-compressed", "edge0-35b-standard"):
            if disk >= EDGE0_SPECS[alt]["disk_gb"]:
                chosen = alt
                spec = EDGE0_SPECS[alt]
                break

    return {
        "model": chosen,
        "spec": spec,
        "edge0_available": has_binary,
        "reason": f"RAM={ram:.1f} GB, disk={disk:.1f} GB → {chosen}",
        "install_steps": [
            f"1. Download {spec['name']} GGUF from {spec['source']}",
            "2. Place at ~/.local/share/celina/models/edge0/<model>.gguf",
            "3. Install edge0-infer binary and ensure it's on PATH",
            "4. Set CELINA_EDGE0_MODEL=<model-id>",
        ],
    }


# ---------------------------------------------------------------------------
# Tier-based scaling helpers (CONSTRAINED / BALANCED / HEADROOM)
# ---------------------------------------------------------------------------

TIER_NUM_CTX = {"CONSTRAINED": 2048, "BALANCED": 4096, "HEADROOM": 16384}
TIER_WHISPER_IDLE_OFFLOAD_SEC = {"CONSTRAINED": 90, "BALANCED": 180, "HEADROOM": 300}
TIER_CACHE_HOT_MAX_ITEMS = {"CONSTRAINED": 32, "BALANCED": 128, "HEADROOM": 512}
TIER_MAX_CONCURRENT_MCP = {"CONSTRAINED": 1, "BALANCED": 2, "HEADROOM": 4}

TIER_KEEP_ALIVE = {"CONSTRAINED": "45s", "BALANCED": "5m", "HEADROOM": "15m"}


def tier_num_ctx(tier: str | None = None, ram_mb: int | None = None) -> int:
    """Return default Ollama num_ctx for the given RAM tier."""
    t = tier or (get_ram_tier(ram_mb) if ram_mb is not None else get_ram_tier())
    return TIER_NUM_CTX.get(t, TIER_NUM_CTX["CONSTRAINED"])


def tier_whisper_idle_offload_sec(tier: str | None = None, ram_mb: int | None = None) -> int:
    """Return Whisper idle offload seconds for the given RAM tier."""
    t = tier or (get_ram_tier(ram_mb) if ram_mb is not None else get_ram_tier())
    return TIER_WHISPER_IDLE_OFFLOAD_SEC.get(t, TIER_WHISPER_IDLE_OFFLOAD_SEC["CONSTRAINED"])


def tier_cache_hot_max_items(tier: str | None = None, ram_mb: int | None = None) -> int:
    """Return SmartCache ram_hot_max_items for the given RAM tier."""
    t = tier or (get_ram_tier(ram_mb) if ram_mb is not None else get_ram_tier())
    return TIER_CACHE_HOT_MAX_ITEMS.get(t, TIER_CACHE_HOT_MAX_ITEMS["CONSTRAINED"])


def tier_max_concurrent_mcp(tier: str | None = None, ram_mb: int | None = None) -> int:
    """Return max concurrent lazy MCP servers for the given RAM tier."""
    t = tier or (get_ram_tier(ram_mb) if ram_mb is not None else get_ram_tier())
    return TIER_MAX_CONCURRENT_MCP.get(t, TIER_MAX_CONCURRENT_MCP["CONSTRAINED"])


def tier_keep_alive(tier: str | None = None, ram_mb: int | None = None) -> str:
    """Return Ollama keep_alive for the given RAM tier (CONSTRAINED=45s, BALANCED=5m, HEADROOM=15m)."""
    t = tier or (get_ram_tier(ram_mb) if ram_mb is not None else get_ram_tier())
    return TIER_KEEP_ALIVE.get(t, TIER_KEEP_ALIVE["CONSTRAINED"])


# ---------------------------------------------------------------------------
# Cloud provider routing (parallel ensemble vs failover)
# ---------------------------------------------------------------------------


class RoutingStrategy(str, Enum):
    FAILOVER = "failover"      # Single provider, next on failure
    PARALLEL = "parallel"      # All providers run simultaneously, best result wins
    HYBRID = "hybrid"          # Primary + parallel backup for critical tasks


# Provider groups by task domain for parallel ensemble routing
PROVIDER_GROUPS: dict[str, list[str]] = {
    "code": ["deepseek", "cerebras", "groq", "mistral"],
    "math": ["xai", "nvidia", "groq", "gemini"],
    "creative": ["gemini", "groq", "mistral"],
    "search": ["perplexity", "openrouter", "gemini"],
    "multilingual": ["glm", "gemini", "groq"],
    "vision": ["gemini", "glm"],
    "general": ["groq", "cerebras", "gemini", "mistral", "openrouter"],
}


def classify_task_complexity(goal: str) -> dict[str, Any]:
    """Classify a task's complexity and domains to determine routing strategy.

    Returns:
      - complexity: 'simple' | 'moderate' | 'complex' | 'heavy'
      - domains: list of detected domains
      - strategy: RoutingStrategy
      - reason: human-readable explanation

    """
    text = (goal or "").strip()
    word_count = len(text.split())
    low = text.lower()

    # Domain detection
    domains = []
    domain_keywords = {
        "code": ["code", "program", "script", "python", "javascript", "debug", "function", "api", "git", "compile"],
        "math": ["calculate", "math", "equation", "solve", "derivative", "integral", "statistics", "probability"],
        "creative": ["write", "story", "poem", "creative", "essay", "compose", "lyrics", "script"],
        "search": ["search", "web", "browse", "lookup", "find", "research", "news", "price"],
        "multilingual": ["translate", "telugu", "hindi", "español", "français", "language"],
        "vision": ["image", "screenshot", "photo", "picture", "visual", "ocr", "read screen"],
    }
    for domain, keywords in domain_keywords.items():
        if any(kw in low for kw in keywords):
            domains.append(domain)

    # Complexity classification
    if word_count >= 60 or len(domains) >= 3 or any(kw in low for kw in ["plan", "architect", "design system", "migrate", "refactor"]):
        complexity = "heavy"
        strategy = RoutingStrategy.PARALLEL
    elif word_count >= 25 or len(domains) >= 2 or any(kw in low for kw in ["analyze", "compare", "review", "explain", "debug"]):
        complexity = "complex"
        strategy = RoutingStrategy.HYBRID
    elif word_count >= 10 or len(domains) >= 1:
        complexity = "moderate"
        strategy = RoutingStrategy.FAILOVER
    else:
        complexity = "simple"
        strategy = RoutingStrategy.FAILOVER

    return {
        "complexity": complexity,
        "domains": domains,
        "strategy": strategy,
        "word_count": word_count,
        "reason": f"{word_count} words, {len(domains)} domain(s) → {strategy.value}",
    }


def select_providers_for_task(
    domains: list[str],
    strategy: RoutingStrategy,
    configured_providers: list[str],
    max_parallel: int = 3,
) -> dict[str, Any]:
    """Select which cloud providers to use for a task based on domains and strategy.

    For PARALLEL/HYBRID: divides work among providers by domain specialization.
    For FAILOVER: picks ordered list of best-fit providers.

    Returns dict with:
      - primary: first provider to try
      - parallel: list of providers to run simultaneously (for parallel/hybrid)
      - fallback: ordered failover list
      - task_split: how to divide heavy tasks among providers
    """
    # Filter to configured providers only
    configured_set = set(configured_providers)

    # Build provider priority by domain
    domain_providers: dict[str, list[str]] = {}
    for domain in domains:
        group = PROVIDER_GROUPS.get(domain, [])
        domain_providers[domain] = [p for p in group if p in configured_set]

    # General fallback providers
    general = [p for p in PROVIDER_GROUPS["general"] if p in configured_set]

    # Determine primary provider (first available from general or first domain)
    primary = general[0] if general else (configured_providers[0] if configured_providers else None)

    # Build parallel list for complex/heavy tasks
    parallel: list[str] = []
    if strategy in (RoutingStrategy.PARALLEL, RoutingStrategy.HYBRID):
        seen = set()
        for domain in domains:
            for p in domain_providers.get(domain, []):
                if p not in seen and len(parallel) < max_parallel:
                    parallel.append(p)
                    seen.add(p)
        # Fill remaining slots with general providers
        for p in general:
            if p not in seen and len(parallel) < max_parallel:
                parallel.append(p)
                seen.add(p)

    # Build failover list (ordered)
    failover: list[str] = []
    seen = set()
    for p in parallel:
        if p not in seen:
            failover.append(p)
            seen.add(p)
    for p in general:
        if p not in seen:
            failover.append(p)
            seen.add(p)
    for p in configured_providers:
        if p not in seen:
            failover.append(p)
            seen.add(p)

    # Task split for heavy tasks
    task_split: dict[str, list[str]] = {}
    if strategy == RoutingStrategy.PARALLEL and len(domains) >= 2:
        for domain in domains:
            providers = domain_providers.get(domain, [])
            if providers:
                task_split[domain] = providers[:2]

    return {
        "primary": primary,
        "parallel": parallel,
        "fallback": failover,
        "strategy": strategy,
        "task_split": task_split,
        "max_parallel": max_parallel,
    }


def decide_cloud_routing(
    goal: str,
    configured_providers: list[str],
    ram_gb: float | None = None,
    prefer_parallel: bool = False,
) -> dict[str, Any]:
    """Complete cloud routing decision for a task.

    Combines complexity classification + provider selection into one decision.

    Returns dict with classification, provider selection, and execution plan.
    """
    ram = ram_gb if ram_gb is not None else detect_total_ram_gb()
    classification = classify_task_complexity(goal)

    # Override strategy if user prefers parallel
    strategy = classification["strategy"]
    if prefer_parallel and strategy == RoutingStrategy.FAILOVER:
        strategy = RoutingStrategy.HYBRID

    # RAM constraint: on low RAM, avoid parallel (more context to manage)
    if ram <= 8.0 and strategy == RoutingStrategy.PARALLEL:
        strategy = RoutingStrategy.HYBRID

    provider_selection = select_providers_for_task(
        classification["domains"], strategy, configured_providers,
    )

    return {
        "goal": goal,
        "ram_gb": ram,
        "classification": classification,
        "routing": provider_selection,
        "execution_plan": {
            "step1": f"Try primary: {provider_selection['primary']}",
            "step2": f"Parallel: {provider_selection['parallel']}" if provider_selection["parallel"] else "None",
            "step3": f"Failover: {provider_selection['fallback']}",
            "local_merge": "Reassemble results locally via Celina router",
        },
    }


# ---------------------------------------------------------------------------
# Unified model decision (entry point)
# ---------------------------------------------------------------------------


@dataclass
class FullModelDecision:
    """Complete model decision for all tiers."""

    whisper: dict[str, Any]
    ollama: dict[str, Any]
    edge0: dict[str, Any]
    smolLM: dict[str, Any]
    cloud_routing: dict[str, Any]
    system_resources: dict[str, Any]


def make_full_model_decision(
    goal: str | None = None,
    configured_providers: list[str] | None = None,
    prefer_language: str | None = None,
    prefer_vision: bool = False,
    prefer_heavy: bool = False,
    prefer_parallel: bool = False,
) -> FullModelDecision:
    """Make a complete model decision for Celina based on system resources.

    This is the single entry point for all model selection. It considers:
      - System RAM, disk, CPU, GPU, NPU
      - User environment overrides (CELINA_*_MODEL)
      - Task complexity (if goal provided)
      - Cloud provider availability

    Returns FullModelDecision with all sub-decisions.
    """
    ram = detect_total_ram_gb()
    disk = detect_free_disk_gb()
    cpu = detect_cpu_cores()
    gpu = has_gpu()
    npu = has_npu()

    whisper = select_whisper_model(ram_gb=ram, prefer_language=prefer_language)
    ollama = select_ollama_model(
        ram_gb=ram, _has_gpu_param=gpu, _has_npu_param=npu,
        prefer_vision=prefer_vision, prefer_heavy=prefer_heavy,
    )
    edge0 = select_edge0_model(ram_gb=ram, disk_gb=disk)

    # Lazy import avoids the config/model selector import cycle.
    from . import gear0_5_smolLM

    # Gear 0.5 smolLM decision
    smolLM_ram = "100-200 MB" if ram >= 8.0 else "50-100 MB"
    smolLM = {
        "model": gear0_5_smolLM.SMOLLM_MODEL,
        "active_ram_mb": 100 if ram >= 8.0 else 50,
        "binary": "llama-cli",
        "model_path": gear0_5_smolLM.SMOLLM_MODEL_PATH,
        "reason": f"Gear 0.5 smolLM — {smolLM_ram} active RAM, sub-100ms intent classification",
    }

    cloud = decide_cloud_routing(
        goal or "", configured_providers or [], ram_gb=ram, prefer_parallel=prefer_parallel,
    )

    resources = {
        "ram_gb": ram,
        "available_ram_mb": detect_available_ram_mb(),
        "cpu_cores": cpu,
        "free_disk_gb": disk,
        "gpu": gpu,
        "npu": npu,
    }

    return FullModelDecision(
        whisper=whisper,
        ollama=ollama,
        edge0=edge0,
        smolLM=smolLM,
        cloud_routing=cloud,
        system_resources=resources,
    )


def format_decision_summary(decision: FullModelDecision) -> str:
    """Human-readable summary of the full model decision."""
    lines = [
        "═══════════════════════════════════════════════════════════════════",
        "Celina Model Decision Summary",
        "═══════════════════════════════════════════════════════════════════",
        f"System: {decision.system_resources['ram_gb']} GB RAM, "
        f"{decision.system_resources['cpu_cores']} CPU cores, "
        f"{decision.system_resources['free_disk_gb']} GB free disk, "
        f"GPU={decision.system_resources['gpu']}, NPU={decision.system_resources['npu']}",
        "",
        "Whisper STT:",
        f"  Model: {decision.whisper['model']} (~{decision.whisper['spec']['size_mb']} MB)",
        f"  Reason: {decision.whisper['reason']}",
        f"  Install: {decision.whisper['install_cmd']}",
        "",
        "Ollama LLM:",
        f"  Model: {decision.ollama['model']} ({decision.ollama['spec'].arch}, {decision.ollama['spec'].total_params})",
        f"  Reason: {decision.ollama['reason']}",
        f"  Install: {decision.ollama['install_cmd']}",
        "",
        "Edge0:",
        f"  Model: {decision.edge0['model']} ({decision.edge0['spec']['name']})",
        f"  Edge0-infer available: {decision.edge0['edge0_available']}",
        f"  Reason: {decision.edge0['reason']}",
        "",
        "Gear 0.5 smolLM:",
        f"  Model: {decision.smolLM['model']}",
        f"  Reason: {decision.smolLM['reason']}",
        "",
        "Cloud Routing:",
        f"  Complexity: {decision.cloud_routing['classification']['complexity']}",
        f"  Strategy: {decision.cloud_routing['routing']['strategy']}",
        f"  Primary: {decision.cloud_routing['routing']['primary']}",
        f"  Parallel: {decision.cloud_routing['routing']['parallel']}",
        f"  Fallback: {decision.cloud_routing['routing']['fallback']}",
        "",
        "═══════════════════════════════════════════════════════════════════",
    ]
    return "\n".join(lines)


__all__ = [
    "BALANCED_RAM_MB",
    "CONSTRAINED_RAM_MB",
    "EDGE0_SPECS",
    "OLLAMA_CATALOG",
    "PROVIDER_GROUPS",
    "WHISPER_SPECS",
    "Edge0Model",
    "FullModelDecision",
    "OllamaModelSpec",
    "RoutingStrategy",
    "WhisperModel",
    "binary_available",
    "classify_task_complexity",
    "decide_cloud_routing",
    "detect_available_ram_mb",
    "detect_cpu_cores",
    "detect_free_disk_gb",
    "detect_total_ram_gb",
    "format_decision_summary",
    "get_ram_tier",
    "has_gpu",
    "has_npu",
    "make_full_model_decision",
    "select_edge0_model",
    "select_ollama_model",
    "select_providers_for_task",
    "select_whisper_model",
    "tier_cache_hot_max_items",
    "tier_keep_alive",
    "tier_max_concurrent_mcp",
    "tier_num_ctx",
    "tier_whisper_idle_offload_sec",
]
