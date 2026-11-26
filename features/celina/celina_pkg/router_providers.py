"""Celina AI - Router Module: Provider & Inference Helpers.

Contains standalone functions (no self):
- HTTP session pooling (_build_pooled_session)
- Ollama thread/ctx/keep-alive tuning
- Provider availability check (_has_cloud_key)
- Tool arg parsing (_parse_tool_args)
- Tool error detection (_is_tool_error)
- Self-correction hint delegation (_build_self_correction_hint)
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from . import config, provider_router
from .router_receipts import _TOOL_ERROR_MARKERS
from .router_receipts import (
    _build_self_correction_hint as _receipts_build_self_correction_hint,
)

# ---------------------------------------------------------------------------
# HTTP session pooling
# ---------------------------------------------------------------------------

def _build_pooled_session() -> requests.Session:
    """Creates a persistent HTTP/1.1 keep-alive Session to eliminate repeat TCP/TLS handshakes."""
    sess = requests.Session()
    adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


# ---------------------------------------------------------------------------
# Ollama tuning
# ---------------------------------------------------------------------------

def _recommended_ollama_threads(cpu_count: int | None = None) -> int:
    """Computes physical-core-friendly inference thread count to avoid SMT/hyperthread contention."""
    if config.OLLAMA_NUM_THREADS > 0:
        return max(1, int(config.OLLAMA_NUM_THREADS))
    logical = cpu_count if cpu_count is not None else (os.cpu_count() or 4)
    if logical <= 4:
        return max(1, logical)
    return max(4, min(8, logical // 2))


def _ollama_ctx_for_gear(
    gear: str,
    base_ctx: int | None = None,
    total_ram_gb: int | None = None,
) -> int:
    """Adaptive context window to prevent KV-cache RAM spikes & disk thrashing."""
    ctx = int(base_ctx if base_ctx is not None else config.OLLAMA_NUM_CTX)
    ram = int(total_ram_gb if total_ram_gb is not None else config.detect_system_ram_gb())
    if ram <= 10:
        cap = 1536 if gear == "fast" else 2048
        return max(1024, min(ctx, cap))
    if gear == "fast":
        return max(1024, min(ctx, 2048))
    return max(1024, ctx)


def _effective_ollama_keep_alive(available_ram_mb: int | None = None) -> Any:
    """Dynamically tightens Ollama keep_alive under real RAM pressure."""
    if config.is_lite_mode():
        return 0
    avail = int(available_ram_mb if available_ram_mb is not None else config.detect_available_ram_mb())
    if avail < 1200:
        return 0
    if avail < 2200:
        return "15s"
    return config.OLLAMA_KEEP_ALIVE


# ---------------------------------------------------------------------------
# Provider availability
# ---------------------------------------------------------------------------

def _has_cloud_key() -> bool:
    return any(row["configured"] for row in provider_router.status())


# ---------------------------------------------------------------------------
# Tool arg parsing
# ---------------------------------------------------------------------------

def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


# ---------------------------------------------------------------------------
# Tool error detection (delegates to router_receipts constants)
# ---------------------------------------------------------------------------

def _is_tool_error(output: str) -> bool:
    low = (output or "").strip().lower()
    if not low:
        return True
    if low.startswith("cancelled by user"):
        return False
    return any(
        low.startswith(m) or f"\n{m}" in low for m in _TOOL_ERROR_MARKERS
    ) or ("not found" in low[:120] and len(low) < 240)


# ---------------------------------------------------------------------------
# Self-correction hint (delegates to router_receipts)
# ---------------------------------------------------------------------------

def _build_self_correction_hint(fn_name: str, output: str, dup_failures: int) -> str:
    return _receipts_build_self_correction_hint(fn_name, output, dup_failures)
