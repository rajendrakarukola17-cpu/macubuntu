"""
Celina AI (Celly) - Gear 0.5: smolLM Quantized (Update 26)

Sub-second local LLM tier between Gear 0 (0 tokens, 0 MB) and Gear 1 (Ollama qwen3:1.7b).
Uses smolLM (117M-350M params, 4-bit quantized) via llama.cpp Python bindings.

Design goals:
- Local-first, offline, zero external API calls
- ~100-200 MB live RAM (vs ~1.8GB for qwen3:1.7b)
- Sub-100ms typical latency (vs seconds for Gear 1)
- Bounded token usage (max 128 output tokens, small context)
- Graceful fallback to Gear 1 if smolLM unavailable

Hard refusals:
- Not part of Gear 0 dispatch chain (separate tier 0.5)
- No cloud connector, no webhook, no exponential backoff
- No silent error swallowing — failures are reported explicitly
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# smolLM model variants (quantized GGUF)
SMOLLM_MODELS: dict[str, dict[str, Any]] = {
    "smollm-117m": {
        "tag": "HuggingFaceTB/smolLM-117M",
        "size_gb": 0.05,
        "min_ram_gb": 1,
        "note": "Tiny 117M param, 4-bit quantized — ~50MB RAM",
    },
    "smollm-360m": {
        "tag": "HuggingFaceTB/smolLM-360M",
        "size_gb": 0.2,
        "min_ram_gb": 2,
        "note": "360M param, 4-bit quantized — ~150MB RAM",
    },
    "smollm-1.7b": {
        "tag": "HuggingFaceTB/smolLM-1.7B",
        "size_gb": 1.0,
        "min_ram_gb": 4,
        "note": "1.7B param, 4-bit quantized — ~700MB RAM",
    },
}

# Default model — smallest that's still useful
DEFAULT_SMOLLM_MODEL = "smollm-117m"

# Environment overrides
SMOLLM_MODEL = os.environ.get("CELINA_SMOLLM_MODEL", DEFAULT_SMOLLM_MODEL)
SMOLLM_BIN = os.environ.get(
    "CELINA_SMOLLM_BIN",
    str(Path.home() / ".celina" / "models" / "smolLM" / "llama.cpp" / "llama-cli"),
)
SMOLLM_MODEL_PATH = os.environ.get(
    "CELINA_SMOLLM_MODEL_PATH",
    str(Path.home() / ".celina" / "models" / "smolLM" / f"{SMOLLM_MODEL}.gguf"),
)
SMOLLM_NUM_THREADS = int(os.environ.get("CELINA_SMOLLM_NUM_THREADS", "2"))
SMOLLM_NUM_CTX = int(os.environ.get("CELINA_SMOLLM_NUM_CTX", "512"))
SMOLLM_MAX_TOKENS = int(os.environ.get("CELINA_SMOLLM_MAX_TOKENS", "128"))
SMOLLM_TEMPERATURE = float(os.environ.get("CELINA_SMOLLM_TEMPERATURE", "0.3"))


# ---------------------------------------------------------------------------
# smolLM runner (llama.cpp CLI, local subprocess)
# ---------------------------------------------------------------------------

class SmolLMRunner:
    """
    Runs smolLM quantized GGUF via llama.cpp CLI as a local subprocess.
    Each call is a short-lived process — no persistent model in RAM.
    """

    def __init__(self) -> None:
        self._bin_path: Optional[str] = None
        self._model_path: Optional[str] = None
        self._available: bool = False
        self._probe()

    def _probe(self) -> None:
        """Check if llama.cpp binary and model file exist."""
        bin_path = Path(SMOLLM_BIN)
        model_path = Path(SMOLLM_MODEL_PATH)
        if bin_path.exists() and os.access(bin_path, os.X_OK) and model_path.exists():
            self._bin_path = str(bin_path)
            self._model_path = str(model_path)
            self._available = True
        else:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def generate(
        self,
        prompt: str,
        max_tokens: int = SMOLLM_MAX_TOKENS,
        temperature: float = SMOLLM_TEMPERATURE,
        timeout_sec: float = 10.0,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Generate text with smolLM.
        Returns (reply, error) — one is always None.
        """
        if not self._available:
            return None, "smolLM not available (llama.cpp binary or GGUF model missing)"

        cmd = [
            self._bin_path,
            "-m", self._model_path,
            "-p", prompt,
            "-n", str(max_tokens),
            "--temp", str(temperature),
            "--ctx-size", str(SMOLLM_NUM_CTX),
            "--threads", str(SMOLLM_NUM_THREADS),
            "--verbose-prompt", "no",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return None, f"smolLM timeout after {timeout_sec}s"
        except Exception as exc:
            return None, f"smolLM execution error: {exc}"

        if result.returncode != 0:
            return None, f"smolLM exit code {result.returncode}: {result.stderr.strip()[:200]}"

        # Extract generated text after the prompt echo
        output = result.stdout
        # llama.cpp echoes the prompt; find the generated portion after it
        marker = prompt.strip()
        idx = output.find(marker)
        if idx >= 0:
            generated = output[idx + len(marker):].strip()
        else:
            generated = output.strip()

        # Clean up common artifacts
        generated = generated.replace("\n\n", "\n").strip()
        if not generated:
            return None, "smolLM returned empty output"

        return generated, None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_runner: Optional[SmolLMRunner] = None


def _get_runner() -> SmolLMRunner:
    global _runner
    if _runner is None:
        _runner = SmolLMRunner()
    return _runner


def gear0_5_smolLM_available() -> bool:
    """Return True if smolLM runner is available (binary + model present)."""
    return _get_runner().available


def gear0_5_smolLM_generate(
    prompt: str,
    max_tokens: int = SMOLLM_MAX_TOKENS,
    temperature: float = SMOLLM_TEMPERATURE,
    timeout_sec: float = 10.0,
) -> tuple[Optional[str], Optional[str]]:
    """
    Generate a response using smolLM quantized.
    Returns (reply, error) — one is always None.
    """
    runner = _get_runner()
    return runner.generate(prompt, max_tokens=max_tokens, temperature=temperature, timeout_sec=timeout_sec)


def gear0_5_smolLM_status() -> dict[str, Any]:
    """Return status info for the smolLM tier."""
    runner = _get_runner()
    model_info = SMOLLM_MODELS.get(SMOLLM_MODEL, {})
    return {
        "available": runner.available,
        "model": SMOLLM_MODEL,
        "model_tag": model_info.get("tag", "unknown"),
        "size_gb": model_info.get("size_gb", 0),
        "min_ram_gb": model_info.get("min_ram_gb", 0),
        "bin_path": runner._bin_path,
        "model_path": runner._model_path,
    }


__all__ = [
    "SMOLLM_MODEL",
    "SMOLLM_BIN",
    "SMOLLM_MODEL_PATH",
    "SMOLLM_NUM_THREADS",
    "SMOLLM_NUM_CTX",
    "SMOLLM_MAX_TOKENS",
    "SMOLLM_TEMPERATURE",
    "SMOLLM_MODELS",
    "gear0_5_smolLM_available",
    "gear0_5_smolLM_generate",
    "gear0_5_smolLM_status",
]
