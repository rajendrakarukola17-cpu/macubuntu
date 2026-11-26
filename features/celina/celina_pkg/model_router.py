"""Celina AI (Celly) - Automatic Model Router (v4.5, Update 18)

Selects the best local model based on system RAM and capabilities:

  - 8 GB RAM or less: Edge0-8B (~1.0 GiB active) or qwen3:1.7b Q4_K_M (~1.4 GiB)
  - 16 GB RAM: Edge0-35B-A3B compressed (~1.5-2.0 GiB active) with memory optimization
  - 24+ GB RAM: Edge0-35B-A3B standard (~2.9 GiB active) or larger models

Automatically detects available RAM, disk space, and edge0-infer availability,
then selects the optimal model configuration.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass

log = logging.getLogger("celina.model_router")

# Model tiers with memory requirements
MODEL_TIERS = {
    "qwen3:1.7b_q4km": {
        "name": "qwen3:1.7b Q4_K_M",
        "provider": "ollama",
        "active_ram_gb": 1.4,
        "disk_gb": 1.0,
        "description": "Tiny dense model, fastest, simplest",
        "best_for": "8 GB RAM, basic tasks",
    },
    "edge0-8b": {
        "name": "Edge0-8B-A1B-preview",
        "provider": "edge0",
        "active_ram_gb": 1.0,
        "disk_gb": 4.2,
        "description": "8B MoE, streams from SSD, 24-25 tok/s",
        "best_for": "8 GB RAM, better reasoning than qwen3:1.7b",
    },
    "edge0-35b-compressed": {
        "name": "Edge0-35B-A3B-preview (compressed 1.5-2.0 GiB)",
        "provider": "edge0",
        "active_ram_gb": 1.8,
        "disk_gb": 23.0,
        "description": "35B MoE with Q4_K/Q3_K_S, no LRU cache, Q4 KV cache, 4K cap",
        "best_for": "16 GB RAM, complex reasoning, coding, analysis",
    },
    "edge0-35b-standard": {
        "name": "Edge0-35B-A3B-preview (standard 2.9 GiB)",
        "provider": "edge0",
        "active_ram_gb": 2.9,
        "disk_gb": 23.0,
        "description": "35B MoE standard configuration",
        "best_for": "24+ GB RAM, maximum capability",
    },
}


def detect_total_ram_gb() -> float:
    """Detect total system RAM in GiB."""
    try:
        import psutil

        return round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        # Fallback: read /proc/meminfo
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / (1024**2), 1)
        except Exception:
            pass
        return 8.0  # Safe default


def detect_free_disk_gb(path: str = "/") -> float:
    """Detect free disk space in GiB at the given path."""
    try:
        stat = shutil.disk_usage(path)
        return round(stat.free / (1024**3), 1)
    except Exception:
        return 0.0


def edge0_available() -> bool:
    """Check if edge0-infer binary is available."""
    return shutil.which("edge0-infer") is not None


def ollama_available() -> bool:
    """Check if ollama binary is available."""
    return shutil.which("ollama") is not None


@dataclass
class ModelSelection:
    """Result of automatic model selection."""

    model_id: str
    model_name: str
    provider: str
    active_ram_gb: float
    disk_gb: float
    description: str
    best_for: str
    reason: str
    config: dict


def select_best_model(
    preferred_model: str | None = None,
    min_free_disk_gb: float = 5.0,
) -> ModelSelection:
    """Automatically select the best model for the current system.

    Selection logic:
    1. If user specified a preferred model and it's available, use it.
    2. If RAM <= 8 GB: prefer Edge0-8B (if edge0 available) or qwen3:1.7b (ollama)
    3. If RAM 8-16 GB: prefer Edge0-35B compressed (if edge0 + disk space) or Edge0-8B
    4. If RAM >= 16 GB: prefer Edge0-35B standard (if edge0 + disk space)
    5. Fallback: qwen3:1.7b via Ollama
    """
    ram_gb = detect_total_ram_gb()
    disk_gb = detect_free_disk_gb()
    has_edge0 = edge0_available()
    has_ollama = ollama_available()

    log.info(
        "Model selection: RAM=%.1f GB, Free Disk=%.1f GB, Edge0=%s, Ollama=%s",
        ram_gb,
        disk_gb,
        has_edge0,
        has_ollama,
    )

    # User preference takes priority
    if preferred_model:
        for tier_id, tier in MODEL_TIERS.items():
            if tier_id == preferred_model or tier["name"] == preferred_model:
                # Verify availability
                if tier["provider"] == "edge0" and not has_edge0:
                    log.warning("Preferred model %s requires edge0-infer which is not available", preferred_model)
                    break
                if tier["provider"] == "ollama" and not has_ollama:
                    log.warning("Preferred model %s requires ollama which is not available", preferred_model)
                    break
                if disk_gb < tier["disk_gb"]:
                    log.warning(
                        "Preferred model %s needs %.1f GB disk, only %.1f GB free",
                        preferred_model,
                        tier["disk_gb"],
                        disk_gb,
                    )
                    break
                return ModelSelection(
                    model_id=tier_id,
                    model_name=tier["name"],
                    provider=tier["provider"],
                    active_ram_gb=tier["active_ram_gb"],
                    disk_gb=tier["disk_gb"],
                    description=tier["description"],
                    best_for=tier["best_for"],
                    reason="User-specified preferred model",
                    config=tier,
                )

    # Automatic selection based on RAM
    if ram_gb <= 8.0:
        # 8 GB or less: lightweight models
        if has_edge0 and disk_gb >= MODEL_TIERS["edge0-8b"]["disk_gb"]:
            tier = MODEL_TIERS["edge0-8b"]
            return ModelSelection(
                model_id="edge0-8b",
                model_name=tier["name"],
                provider=tier["provider"],
                active_ram_gb=tier["active_ram_gb"],
                disk_gb=tier["disk_gb"],
                description=tier["description"],
                best_for=tier["best_for"],
                reason=f"8 GB RAM detected, Edge0-8B fits in {tier['active_ram_gb']} GB active RAM",
                config=tier,
            )
        if has_ollama:
            tier = MODEL_TIERS["qwen3:1.7b_q4km"]
            return ModelSelection(
                model_id="qwen3:1.7b_q4km",
                model_name=tier["name"],
                provider=tier["provider"],
                active_ram_gb=tier["active_ram_gb"],
                disk_gb=tier["disk_gb"],
                description=tier["description"],
                best_for=tier["best_for"],
                reason=f"8 GB RAM detected, qwen3:1.7b Q4_K_M via Ollama ({tier['active_ram_gb']} GB)",
                config=tier,
            )
        # No local runtime available - must use cloud
        return ModelSelection(
            model_id="openrouter/free",
            model_name="OpenRouter/free",
            provider="cloud",
            active_ram_gb=0.0,
            disk_gb=0.0,
            description="Cloud fallback via OpenRouter/free",
            best_for="No local model runtime available",
            reason="No Edge0 or Ollama available, falling back to cloud",
            config={},
        )

    if ram_gb <= 16.0:
        # 8-16 GB: compressed 35B if possible, otherwise 8B
        if has_edge0 and disk_gb >= MODEL_TIERS["edge0-35b-compressed"]["disk_gb"]:
            tier = MODEL_TIERS["edge0-35b-compressed"]
            return ModelSelection(
                model_id="edge0-35b-compressed",
                model_name=tier["name"],
                provider=tier["provider"],
                active_ram_gb=tier["active_ram_gb"],
                disk_gb=tier["disk_gb"],
                description=tier["description"],
                best_for=tier["best_for"],
                reason=f"{ram_gb} GB RAM, Edge0-35B compressed to {tier['active_ram_gb']} GB active RAM",
                config=tier,
            )
        if has_edge0 and disk_gb >= MODEL_TIERS["edge0-8b"]["disk_gb"]:
            tier = MODEL_TIERS["edge0-8b"]
            return ModelSelection(
                model_id="edge0-8b",
                model_name=tier["name"],
                provider=tier["provider"],
                active_ram_gb=tier["active_ram_gb"],
                disk_gb=tier["disk_gb"],
                description=tier["description"],
                best_for=tier["best_for"],
                reason=f"{ram_gb} GB RAM, insufficient disk for 35B, using Edge0-8B ({tier['active_ram_gb']} GB)",
                config=tier,
            )
        if has_ollama:
            tier = MODEL_TIERS["qwen3:1.7b_q4km"]
            return ModelSelection(
                model_id="qwen3:1.7b_q4km",
                model_name=tier["name"],
                provider=tier["provider"],
                active_ram_gb=tier["active_ram_gb"],
                disk_gb=tier["disk_gb"],
                description=tier["description"],
                best_for=tier["best_for"],
                reason=f"{ram_gb} GB RAM, no Edge0 available, using qwen3:1.7b ({tier['active_ram_gb']} GB)",
                config=tier,
            )
        return ModelSelection(
            model_id="openrouter/free",
            model_name="OpenRouter/free",
            provider="cloud",
            active_ram_gb=0.0,
            disk_gb=0.0,
            description="Cloud fallback via OpenRouter/free",
            best_for="No local model runtime available",
            reason="No Edge0 or Ollama available, falling back to cloud",
            config={},
        )

    # 16+ GB: standard 35B if possible
    if has_edge0 and disk_gb >= MODEL_TIERS["edge0-35b-standard"]["disk_gb"]:
        tier = MODEL_TIERS["edge0-35b-standard"]
        return ModelSelection(
            model_id="edge0-35b-standard",
            model_name=tier["name"],
            provider=tier["provider"],
            active_ram_gb=tier["active_ram_gb"],
            disk_gb=tier["disk_gb"],
            description=tier["description"],
            best_for=tier["best_for"],
            reason=f"{ram_gb} GB RAM, Edge0-35B standard ({tier['active_ram_gb']} GB active)",
            config=tier,
        )
    if has_edge0 and disk_gb >= MODEL_TIERS["edge0-35b-compressed"]["disk_gb"]:
        tier = MODEL_TIERS["edge0-35b-compressed"]
        return ModelSelection(
            model_id="edge0-35b-compressed",
            model_name=tier["name"],
            provider=tier["provider"],
            active_ram_gb=tier["active_ram_gb"],
            disk_gb=tier["disk_gb"],
            description=tier["description"],
            best_for=tier["best_for"],
            reason=f"{ram_gb} GB RAM, Edge0-35B compressed ({tier['active_ram_gb']} GB active)",
            config=tier,
        )
    if has_edge0 and disk_gb >= MODEL_TIERS["edge0-8b"]["disk_gb"]:
        tier = MODEL_TIERS["edge0-8b"]
        return ModelSelection(
            model_id="edge0-8b",
            model_name=tier["name"],
            provider=tier["provider"],
            active_ram_gb=tier["active_ram_gb"],
            disk_gb=tier["disk_gb"],
            description=tier["description"],
            best_for=tier["best_for"],
            reason=f"{ram_gb} GB RAM, insufficient disk for 35B, using Edge0-8B ({tier['active_ram_gb']} GB)",
            config=tier,
        )
    if has_ollama:
        tier = MODEL_TIERS["qwen3:1.7b_q4km"]
        return ModelSelection(
            model_id="qwen3:1.7b_q4km",
            model_name=tier["name"],
            provider=tier["provider"],
            active_ram_gb=tier["active_ram_gb"],
            disk_gb=tier["disk_gb"],
            description=tier["description"],
            best_for=tier["best_for"],
            reason=f"{ram_gb} GB RAM, no Edge0 available, using qwen3:1.7b ({tier['active_ram_gb']} GB)",
            config=tier,
        )
    return ModelSelection(
        model_id="openrouter/free",
        model_name="OpenRouter/free",
        provider="cloud",
        active_ram_gb=0.0,
        disk_gb=0.0,
        description="Cloud fallback via OpenRouter/free",
        best_for="No local model runtime available",
        reason="No Edge0 or Ollama available, falling back to cloud",
        config={},
    )


def get_model_selection_summary() -> dict:
    """Return a human-readable summary of the current model selection."""
    selection = select_best_model()
    ram_gb = detect_total_ram_gb()
    disk_gb = detect_free_disk_gb()

    return {
        "selected_model": selection.model_name,
        "provider": selection.provider,
        "active_ram_gb": selection.active_ram_gb,
        "disk_required_gb": selection.disk_gb,
        "system_ram_gb": ram_gb,
        "system_free_disk_gb": disk_gb,
        "edge0_available": edge0_available(),
        "ollama_available": ollama_available(),
        "reason": selection.reason,
        "best_for": selection.best_for,
        "description": selection.description,
        "all_tiers": MODEL_TIERS,
    }


__all__ = [
    "MODEL_TIERS",
    "ModelSelection",
    "detect_free_disk_gb",
    "detect_total_ram_gb",
    "edge0_available",
    "get_model_selection_summary",
    "ollama_available",
    "select_best_model",
]
