"""Celina AI (Celly) - Gear 0.5: smolLM Quantized (Update 26)

Sub-second local LLM tier between Gear 0 (0 tokens, 0 MB) and Gear 1 (Ollama qwen3:1.7b).
Uses SmolLM2 (135M parameters, 4-bit quantized) via llama.cpp Python bindings.

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
from pathlib import Path
from typing import Any

from . import config
from .model_integrity import ModelIntegrityError, verify_model_hash

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# smolLM model variants (quantized GGUF)
SMOLLM_MODELS: dict[str, dict[str, Any]] = {
    "SmolLM2-135M-Instruct": {
        "tag": "HuggingFaceTB/SmolLM2-135M-Instruct",
        "filename": "smollm-135m.Q4_K_M.gguf",
        "digest_env": "CELINA_SMOLLM_135M_SHA256",
        "lock_id": "smollm-135m-q4",
        "size_gb": 0.05,
        "min_ram_gb": 1,
        "note": "135M instruction model; GGUF artifact is UNVERIFIED",
    },
    "smollm-360m": {
        "filename": "smolLM/smollm-360m.gguf",
        "lock_id": "smollm-360m-q4",
        "tag": "HuggingFaceTB/smolLM-360M",
        "size_gb": 0.2,
        "min_ram_gb": 2,
        "note": "360M param, 4-bit quantized — ~150MB RAM",
    },
    "smollm-1.7b": {
        "filename": "smolLM/smollm-1.7b.gguf",
        "lock_id": "smollm-1.7b-q4",
        "tag": "HuggingFaceTB/smolLM-1.7B",
        "size_gb": 1.0,
        "min_ram_gb": 4,
        "note": "1.7B param, 4-bit quantized — ~700MB RAM",
    },
}

# Default model — smallest that's still useful
DEFAULT_SMOLLM_MODEL = "SmolLM2-135M-Instruct"

# Environment overrides (with safe parsing — crashes at import are a security risk)
def _env_int(key: str, default: str, min_val: int = 1, max_val: int = 1024) -> int:
    try:
        val = int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return int(default)
    return max(min_val, min(max_val, val))


def _env_float(key: str, default: str, min_val: float = 0.0, max_val: float = 2.0) -> float:
    try:
        val = float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)
    return max(min_val, min(max_val, val))


class UnknownSmolLMModelError(ValueError):
    """The configured model name has no catalog entry."""


SMOLLM_MODEL = config.SMOLLM_MODEL
if SMOLLM_MODEL not in SMOLLM_MODELS:
    raise UnknownSmolLMModelError(f"Uncatalogued SmolLM model: {SMOLLM_MODEL!r}")

SMOLLM_BIN = os.environ.get(
    "CELINA_SMOLLM_BIN",
    str(Path.home() / ".celina" / "models" / "smolLM" / "llama.cpp" / "llama-cli"),
)
SMOLLM_MODEL_PATH = config._get_setting(
    "CELINA_SMOLLM_MODEL_PATH",
    str(config.HOME / ".celina" / "models" / SMOLLM_MODELS[SMOLLM_MODEL]["filename"]),
)
SMOLLM_NUM_THREADS = _env_int("CELINA_SMOLLM_NUM_THREADS", "2", 1, 64)
SMOLLM_NUM_CTX = _env_int("CELINA_SMOLLM_NUM_CTX", "512", 64, 8192)
SMOLLM_MAX_TOKENS = _env_int("CELINA_SMOLLM_MAX_TOKENS", "128", 1, 4096)
SMOLLM_TEMPERATURE = _env_float("CELINA_SMOLLM_TEMPERATURE", "0.0", 0.0, 2.0)


# ---------------------------------------------------------------------------
# smolLM runner (llama.cpp CLI, local subprocess)
# ---------------------------------------------------------------------------

class SmolLMRunner:
    """Runs smolLM quantized GGUF via llama.cpp CLI as a local subprocess.
    Each call is a short-lived process — no persistent model in RAM.
    """

    def __init__(self) -> None:
        self._bin_path: str | None = None
        self._model_path: str | None = None
        self._available: bool = False
        self._probe()

    def _verify_model_hash(self, model_path: Path) -> bool:
        """Require a verified signed entry before the runner may load a GGUF."""
        spec = SMOLLM_MODELS.get(SMOLLM_MODEL, {})
        return verify_model_hash(model_path, lock_id=spec.get("lock_id"),
                                 env_var=spec.get("digest_env"))

    def _probe(self) -> None:
        """Check if llama.cpp binary and model file exist and model hash verifies."""
        bin_path = Path(SMOLLM_BIN)
        model_path = Path(SMOLLM_MODEL_PATH)
        if (
            bin_path.exists()
            and os.access(bin_path, os.X_OK)
            and model_path.exists()
            and self._verify_model_hash(model_path)
        ):
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
    ) -> tuple[str | None, str | None]:
        """Generate text with smolLM.
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
            "--simple-io",
            "--no-cnv",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return None, f"smolLM timeout after {timeout_sec}s"
        except Exception as exc:
            return None, f"smolLM execution error: {exc}"

        if result.returncode != 0:
            return None, f"smolLM exit code {result.returncode}: {result.stderr.strip()[:200]}"

        # Extract generated text after the prompt echo
        output = result.stdout
        # llama.cpp echoes the prompt; find the generated portion after it.
        # If the marker is not found, the model returned something we cannot
        # trust — treat as a hard error rather than parsing the echoed prompt.
        marker = prompt.strip()
        idx = output.find(marker)
        if idx >= 0:
            generated = output[idx + len(marker):].strip()
        else:
            # No prompt echo found — output may contain the prompt itself or
            # be entirely untrustworthy. Fail closed.
            return None, "smolLM prompt echo not found in output (possible prompt-echo attack)"

        # Clean up common artifacts
        generated = generated.replace("\n\n", "\n").strip()
        if not generated:
            return None, "smolLM returned empty output"

        return generated, None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_runner: SmolLMRunner | None = None


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
) -> tuple[str | None, str | None]:
    """Generate a response using smolLM quantized.
    Returns (reply, error) — one is always None.
    """
    runner = _get_runner()
    return runner.generate(prompt, max_tokens=max_tokens, temperature=temperature, timeout_sec=timeout_sec)


def gear0_5_smolLM_status() -> dict[str, Any]:
    """Return status info for the smolLM tier."""
    runner = _get_runner()
    model_info = SMOLLM_MODELS.get(SMOLLM_MODEL, {})
    hash_pinned = False
    hash_verified = False
    if runner._model_path:
        try:
            hash_verified = runner._verify_model_hash(Path(runner._model_path))
            hash_pinned = hash_verified
        except ModelIntegrityError:
            pass
    return {
        "available": runner.available,
        "model": SMOLLM_MODEL,
        "model_tag": model_info.get("tag", "unknown"),
        "size_gb": model_info.get("size_gb", 0),
        "min_ram_gb": model_info.get("min_ram_gb", 0),
        "bin_path": runner._bin_path,
        "model_path": runner._model_path,
        "hash_pinned": hash_pinned,
        "hash_verified": hash_verified,
    }


__all__ = [
    "SMOLLM_BIN",
    "SMOLLM_MAX_TOKENS",
    "SMOLLM_MODEL",
    "SMOLLM_MODELS",
    "SMOLLM_MODEL_PATH",
    "SMOLLM_NUM_CTX",
    "SMOLLM_NUM_THREADS",
    "SMOLLM_TEMPERATURE",
    "gear0_5_smolLM_available",
    "gear0_5_smolLM_generate",
    "gear0_5_smolLM_status",
]
