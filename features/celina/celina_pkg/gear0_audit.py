"""Celina AI (Celly) - Gear 0 Audit Log (Update 19)

Local, append-only audit trail for all Gear 0 actions. Zero cloud.
Used by Gear 0 modules to log every handled request.
Also provides Gear 0-Audit classify/execute for status/health queries.
"""

from __future__ import annotations

import datetime
from pathlib import Path

_AUDIT_LOG = Path.home() / ".local" / "share" / "celina" / "gear0_audit.log"


def audit_log(action: str, detail: str = "", module: str = "gear0") -> None:
    """Append an entry to the local audit log."""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"[{ts}] [{module}] {action}" + (f" — {detail}" if detail else "")
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # Audit logging must never crash Gear 0


def audit_read(tail: int = 50) -> str:
    """Read the tail of the audit log."""
    try:
        if not _AUDIT_LOG.exists():
            return "No audit log found."
        lines = _AUDIT_LOG.read_text(errors="ignore").splitlines()
        if not lines:
            return "Audit log is empty."
        return "\n".join(lines[-tail:])
    except Exception as exc:
        return f"Failed to read audit log: {exc}"


# ---------------------------------------------------------------------------
# Gear 0-Audit classify/execute (for router dispatch)
# ---------------------------------------------------------------------------

_AUDIT_LOG_RE = __import__("re").compile(
    r"^(?:show|list|view)\s+(?:the\s+)?(?:audit\s+)?log\??$",
    __import__("re").IGNORECASE,
)
_BACKUP_STATUS_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:backup|back up)\s+(?:status|is my backup)\??$",
    __import__("re").IGNORECASE,
)
_STORAGE_STATUS_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:what'?s|what is)\s+(?:my\s+)?(?:disk|storage|drive)\s+(?:space|usage|free)\??$",
    __import__("re").IGNORECASE,
)


def audit_backup_status() -> str:
    """Check backup status (local, placeholder)."""
    return "No automated backup configured. Use Timeshift, rsync, or your preferred local backup tool."


def audit_storage_status() -> str:
    """Check local disk storage."""
    try:
        import shutil as _sh
        total, used, free = _sh.disk_usage("/")
        total_gb = total // (1024 ** 3)
        used_gb = used // (1024 ** 3)
        free_gb = free // (1024 ** 3)
        pct = int(used / total * 100)
        return f"💾 Disk: {used_gb}GB used / {total_gb}GB total ({pct}% used, {free_gb}GB free)"
    except Exception as exc:
        return f"Failed to read disk usage: {exc}"


def gear0_audit_classify(query: str) -> str | None:
    """Classify query into an audit handler name, or None."""
    low = query.strip().lower()
    if _AUDIT_LOG_RE.match(low):
        return "audit_log"
    if _BACKUP_STATUS_RE.match(low):
        return "backup_status"
    if _STORAGE_STATUS_RE.match(low):
        return "storage_status"
    return None


def gear0_audit_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Audit handler."""
    try:
        if handler == "audit_log":
            return audit_read()
        if handler == "backup_status":
            return audit_backup_status()
        if handler == "storage_status":
            return audit_storage_status()
    except Exception:
        return None
    return None


__all__ = ["audit_log", "audit_read", "gear0_audit_classify", "gear0_audit_execute"]
