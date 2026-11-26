"""
Celina AI (Celly) - Gear 0 Dual-Backend Switch (Update 19)

Switches between Edge0-35B-A3B (local MoE) and Ollama qwen3:1.7b backends
based on detected system resources. Zero cloud for Gear 0 itself.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

_BACKEND_STATE = Path.home() / ".config" / "celina" / "gear0_dual_backend.json"
_MIN_RAM_GB = 4   # Edge0-35B-A3B needs ~2-3 GiB active, recommend 4+ GB free
_MIN_SSD_GB = 20  # Edge0 model weights need SSD space


def _detect_system_ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        return 0.0


def _detect_ssd_free_gb() -> float:
    try:
        import shutil as _sh
        total, used, free = _sh.disk_usage("/")
        return free / (1024 ** 3)
    except Exception:
        return 0.0


def select_backend() -> str:
    """Auto-select backend: Edge0-35B-A3B if resources allow, else Ollama qwen3:1.7b."""
    ram_gb = _detect_system_ram_gb()
    ssd_gb = _detect_ssd_free_gb()
    if ram_gb >= _MIN_RAM_GB and ssd_gb >= _MIN_SSD_GB:
        return "edge0"
    return "ollama"


def current_backend() -> str:
    """Get the currently configured backend."""
    try:
        import json
        if _BACKEND_STATE.exists():
            data = json.loads(_BACKEND_STATE.read_text())
            return data.get("backend", select_backend())
    except Exception:
        pass
    return select_backend()


def set_backend(backend: str) -> str:
    """Manually set the backend."""
    import json
    _BACKEND_STATE.parent.mkdir(parents=True, exist_ok=True)
    _BACKEND_STATE.write_text(json.dumps({"backend": backend}, indent=2))
    return f"✓ Backend set to: {backend}"


def ollama_available() -> bool:
    """Check if Ollama is installed and qwen3:1.7b is available."""
    if not shutil.which("ollama"):
        return False
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=5
        )
        return "qwen3:1.7b" in result.stdout
    except Exception:
        return False


def edge0_available() -> bool:
    """Check if Edge0 binary/weights are present."""
    edge0_paths = [
        Path.home() / ".local" / "share" / "celina" / "edge0",
        Path("/usr/local/bin/edge0"),
        Path("/opt/edge0/bin/edge0"),
    ]
    return any(p.exists() for p in edge0_paths)


# ---------------------------------------------------------------------------
# Master dispatch
# -------------------------------------------------------------------------__

_SWITCH_BACKEND_RE = __import__("re").compile(
    r"^(?:switch|set)\s+backend\s+to\s+(edge0|ollama)\??$",
    __import__("re").IGNORECASE,
)
_SHOW_BACKEND_RE = __import__("re").compile(
    r"^(?:which|current)\s+backend\??$",
    __import__("re").IGNORECASE,
)


def gear0_dual_classify(query: str) -> Optional[str]:
    """Classify query into a dual-backend handler name, or None."""
    low = query.strip().lower()
    if _SWITCH_BACKEND_RE.match(low):
        return "switch_backend"
    if _SHOW_BACKEND_RE.match(low):
        return "show_backend"
    return None


def gear0_dual_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0-Dual-Backend handler."""
    try:
        import re
        if handler == "switch_backend":
            m = _SWITCH_BACKEND_RE.match(query.lower())
            if m:
                backend = m.group(1)
                if backend == "edge0" and not edge0_available():
                    return "Edge0 backend not available on this system."
                if backend == "ollama" and not ollama_available():
                    return "Ollama backend not available (qwen3:1.7b not pulled)."
                return set_backend(backend)
        if handler == "show_backend":
            backend = current_backend()
            return f"🔧 Current backend: {backend}"
    except Exception:
        return None
    return None


__all__ = [
    "select_backend",
    "current_backend",
    "set_backend",
    "ollama_available",
    "edge0_available",
    "gear0_dual_classify",
    "gear0_dual_execute",
]
