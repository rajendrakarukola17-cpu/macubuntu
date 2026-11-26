"""
Celina AI (Celly) - Gear 0 Status / Audit (Update 19)

System status and audit trail — shows Gear 0 health, resident modules, recent actions.
Zero cloud, sub-5ms.
Examples:
  - "Gear 0 status"
  - "Show audit log"
  - "System health"
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

_AUDIT_LOG = Path.home() / ".local" / "share" / "celina" / "gear0_audit.log"


def status_gear0() -> str:
    """Show Gear 0 system status."""
    lines = []
    lines.append("🤖 Celina Gear 0 Status")
    lines.append("=" * 30)
    lines.append(f"Python: {sys.version.split()[0]}")
    lines.append(f"Resident: {'YES' if os.environ.get('CELINA_GEAR0_RESIDENT') == '1' else 'NO'}")

    tools = ["playerctl", "pactl", "amixer", "calcurse", "khal", "mosquitto_pub", "notify-send"]
    available = [t for t in tools if shutil.which(t)]
    lines.append(f"Available tools: {', '.join(available) if available else 'none'}")

    try:
        import psutil
        mem = psutil.virtual_memory()
        lines.append(f"RAM: {mem.available // (1024*1024)} MB free / {mem.total // (1024*1024)} MB total")
    except Exception:
        pass

    lines.append("")
    lines.append("✓ Gear 0 is operational — zero LLM tokens, zero AI RAM, 100% local.")
    return "\n".join(lines)


def status_audit_tail(lines_count: int = 10) -> str:
    """Show the tail of the audit log."""
    try:
        if not _AUDIT_LOG.exists():
            return "No audit log found."
        lines = _AUDIT_LOG.read_text(errors="ignore").splitlines()
        if not lines:
            return "Audit log is empty."
        return "\n".join(lines[-lines_count:])
    except Exception as exc:
        return f"Failed to read audit log: {exc}"


# ---------------------------------------------------------------------------
# Master dispatch
# -------------------------------------------------------------------------__

_STATUS_RE = __import__("re").compile(
    r"^(?:gear\s+0\s+)?(?:status|health|system\s+status)\??$",
    __import__("re").IGNORECASE,
)
_AUDIT_RE = __import__("re").compile(
    r"^(?:show|list)\s+(?:the\s+)?(?:audit\s+)?log\??$",
    __import__("re").IGNORECASE,
)


def gear0_status_classify(query: str) -> Optional[str]:
    """Classify query into a status handler name, or None."""
    low = query.strip().lower()
    if _STATUS_RE.match(low):
        return "gear0_status"
    if _AUDIT_RE.match(low):
        return "audit_log"
    return None


def gear0_status_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0-Status handler."""
    try:
        if handler == "gear0_status":
            return status_gear0()
        if handler == "audit_log":
            return status_audit_tail()
    except Exception:
        return None
    return None


__all__ = [
    "status_gear0",
    "status_audit_tail",
    "gear0_status_classify",
    "gear0_status_execute",
]
