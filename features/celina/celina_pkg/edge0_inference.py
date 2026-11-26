"""Celina AI (Celly) - Edge0 Streaming MoE Inference (v4.5, Update 18)

Edge0-35B-A3B MoE model runner with aggressive memory optimization for 8 GB RAM systems.
Runs a 35B-parameter Mixture-of-Experts model inside 1.5-2.0 GiB active RAM using:

  1. Quantize resident layers (Q4_K / IQ4_XS) - embeddings, attention, shared experts
  2. Disable expert LRU cache (cache_size = 0) - single ping-pong DMA scratch buffer
  3. Use 3-bit (Q3_K_S / IQ3_XXS) for streamed experts - shrink SSD read volume ~25%
  4. 4-bit KV cache + strict 4K context cap - prevent memory spikes

License: Apache-2.0 (Edge0 framework)
Source: https://github.com/Mimo-Inverse/Edge0
Models: https://huggingface.co/Edge0/Edge0-35B-A3B-preview
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any

log = logging.getLogger("celina.edge0")

# Memory targets (in bytes)
TARGET_ACTIVE_RAM_MIN = 1.5 * 1024**3  # 1.5 GiB
TARGET_ACTIVE_RAM_MAX = 2.0 * 1024**3  # 2.0 GiB
STANDARD_EDGE0_35B_RAM = 2.9 * 1024**3  # 2.9 GiB standard

# Quantization levels
Q4_K = "Q4_K"
Q3_K_S = "Q3_K_S"
IQ4_XS = "IQ4_XS"
IQ3_XXS = "IQ3_XXS"

# Context cap
MAX_CONTEXT_TOKENS = 4096

# DMA scratch buffer size (bytes) - single reusable buffer for expert streaming
DMA_SCRATCH_BUFFER_MB = 200


class Edge0Config:
    """Configuration for Edge0-35B-A3B streaming MoE inference."""

    def __init__(
        self,
        model_path: str = "Edge0-35B-A3B-preview",
        active_ram_target_gb: float = 1.8,
        quantization: str = Q4_K,
        expert_quantization: str = Q3_K_S,
        kv_cache_quantization: str = "Q4_0",
        context_cap: int = MAX_CONTEXT_TOKENS,
        disable_lru_cache: bool = True,
        dma_scratch_mb: int = DMA_SCRATCH_BUFFER_MB,
        prerouter_enabled: bool = True,
        recover_lora_enabled: bool = True,
        speculative_decoding: bool = True,
        draft_model: str = "qwen3:0.5b",
    ):
        self.model_path = model_path
        self.active_ram_target_gb = active_ram_target_gb
        self.quantization = quantization
        self.expert_quantization = expert_quantization
        self.kv_cache_quantization = kv_cache_quantization
        self.context_cap = context_cap
        self.disable_lru_cache = disable_lru_cache
        self.dma_scratch_mb = dma_scratch_mb
        self.prerouter_enabled = prerouter_enabled
        self.recover_lora_enabled = recover_lora_enabled
        self.speculative_decoding = speculative_decoding
        self.draft_model = draft_model

    def to_edge0_args(self) -> list[str]:
        """Convert to edge0-infer CLI arguments."""
        args = [
            "edge0-infer",
            "serve",
            "--model",
            self.model_path,
            "--quantization",
            self.quantization,
            "--expert-quantization",
            self.expert_quantization,
            "--kv-cache-quant",
            self.kv_cache_quantization,
            "--max-context",
            str(self.context_cap),
            "--dma-scratch-mb",
            str(self.dma_scratch_mb),
        ]
        if self.disable_lru_cache:
            args.extend(["--cache-size", "0"])
        if self.prerouter_enabled:
            args.append("--prerouter")
        if self.recover_lora_enabled:
            args.append("--recover-lora")
        if self.speculative_decoding:
            args.extend(["--speculative", "--draft-model", self.draft_model])
        args.extend(["--active-ram-target", f"{self.active_ram_target_gb}GB"])
        return args


class Edge0Inference:
    """Edge0 streaming MoE inference engine for Celina.

    Runs Edge0-35B-A3B (35B MoE, Qwen3.6-35B-A3B base) inside 1.5-2.0 GiB active RAM
    by streaming experts from SSD on demand, quantizing resident layers to 4-bit,
    disabling the expert LRU cache, and using a single ping-pong DMA scratch buffer.
    """

    def __init__(self, config: Edge0Config | None = None):
        self.config = config or Edge0Config()
        self._proc: subprocess.Popen | None = None
        self._base_url = "http://localhost:8000/v1"
        self._started = False
        self._memory_measured_gb: float | None = None

    @property
    def is_available(self) -> bool:
        """Check if edge0-infer binary is available on the system."""
        return shutil.which("edge0-infer") is not None

    @property
    def is_running(self) -> bool:
        """Check if the Edge0 inference server is running."""
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> tuple[bool, str]:
        """Start the Edge0 inference server with memory-optimized configuration.

        Returns (success, message).
        """
        if not self.is_available:
            return False, "edge0-infer binary not found. Install from https://github.com/Mimo-Inverse/Edge0"

        if self.is_running:
            return True, "Edge0 inference server already running."

        args = self.config.to_edge0_args()
        log.info("Starting Edge0 inference: %s", " ".join(args))

        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "EDGE0_ACTIVE_RAM_GB": str(self.config.active_ram_target_gb)},
            )
            # Give the server a moment to start
            time.sleep(2.0)

            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read() if self._proc.stderr else ""
                return False, f"Edge0 server exited immediately: {stderr}"

            self._started = True
            self._memory_measured_gb = self._measure_active_memory()
            return True, f"Edge0 server started (target: {self.config.active_ram_target_gb}GB active RAM)"

        except Exception as exc:
            log.exception("Failed to start Edge0 server")
            return False, f"Failed to start Edge0: {exc}"

    def stop(self) -> None:
        """Stop the Edge0 inference server."""
        if self._proc and self.is_running:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._started = False

    def _measure_active_memory(self) -> float | None:
        """Attempt to measure active memory usage of the Edge0 process (GiB)."""
        if not self._proc:
            return None
        try:
            import psutil

            proc = psutil.Process(self._proc.pid)
            mem_bytes = proc.memory_info().rss
            return round(mem_bytes / (1024**3), 2)
        except Exception:
            return None

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 512,
        top_p: float = 0.9,
    ) -> dict[str, Any]:
        """Send a chat completion request to the Edge0 server.

        Falls back to a mock response if the server is not running (for testing).
        """
        if not self.is_running:
            return {
                "id": "edge0-offline",
                "model": self.config.model_path,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "[Edge0 server not running] This is a placeholder response. Start edge0-infer to use the 35B MoE model.",
                        },
                        "finish_reason": "server_offline",
                    },
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        try:
            import urllib.request

            payload = {
                "model": self.config.model_path,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "top_p": top_p,
                "stream": False,
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self._base_url}/chat/completions",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30.0) as resp:  # nosec B310 — configured Edge0 endpoint, scheme fixed to http/https
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            log.exception("Edge0 chat completion failed")
            return {
                "id": "edge0-error",
                "model": self.config.model_path,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"[Edge0 error] {exc}",
                        },
                        "finish_reason": "error",
                    },
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

    def get_memory_stats(self) -> dict[str, Any]:
        """Return memory statistics for the Edge0 inference process."""
        return {
            "target_gb": self.config.active_ram_target_gb,
            "measured_gb": self._memory_measured_gb,
            "within_target": (
                self._memory_measured_gb is not None
                and TARGET_ACTIVE_RAM_MIN <= self._memory_measured_gb * 1024**3 <= TARGET_ACTIVE_RAM_MAX
            ),
            "standard_35b_gb": STANDARD_EDGE0_35B_RAM / (1024**3),
            "optimization_techniques": [
                "Q4_K / IQ4_XS resident layer quantization",
                "Q3_K_S expert quantization",
                "Expert LRU cache disabled (cache_size=0)",
                "Ping-pong DMA scratch buffer",
                "Q4_0 KV cache + 4K context cap",
                "Prerouter routing prediction",
                "Recover-LoRA quality recovery",
            ],
        }


__all__ = ["Edge0Config", "Edge0Inference"]
