"""
Celina AI (Celly) - Gear 0 Macro / Text Expansion (Update 19)

Local macro & text expansion system. Zero cloud, sub-1ms.
Examples:
  - "expaddr" → expands to full address
  - "sig" → expands to email signature
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_CONFIG_PATH = Path.home() / ".celina" / "storage" / "shared" / "gear0_macros.json"


def _load_macros() -> Dict[str, str]:
    try:
        if _CONFIG_PATH.exists():
            return json.loads(_CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_macros(macros: Dict[str, str]) -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(macros, indent=2))
    except Exception:
        pass


def macro_expand(trigger: str) -> str:
    """Expand a macro trigger to its full text."""
    macros = _load_macros()
    if trigger in macros:
        return macros[trigger]
    return f"Unknown macro: {trigger}"


def macro_add(trigger: str, expansion: str) -> str:
    """Add or update a macro."""
    try:
        macros = _load_macros()
        macros[trigger] = expansion
        _save_macros(macros)
        return f"✓ Macro saved: '{trigger}' → '{expansion[:50]}'"
    except Exception as exc:
        return f"Failed to save macro: {exc}"


def macro_list() -> str:
    """List all macros."""
    macros = _load_macros()
    if not macros:
        return "No macros defined."
    lines = [f"• {k} → {v}" for k, v in macros.items()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Master dispatch
# -------------------------------------------------------------------------__

_EXPAND_RE = __import__("re").compile(
    r"^(?:expand|expand macro)\s+(\S+)\??$",
    __import__("re").IGNORECASE,
)
_ADD_MACRO_RE = __import__("re").compile(
    r"^(?:add macro|macro add)\s+(\S+)\s+as\s+(.+)\??$",
    __import__("re").IGNORECASE,
)
_LIST_MACROS_RE = __import__("re").compile(
    r"^(?:list|show)\s+(?:my\s+)?macros\??$",
    __import__("re").IGNORECASE,
)


def gear0_macro_classify(query: str) -> Optional[str]:
    """Classify query into a macro handler name, or None."""
    low = query.strip().lower()
    if _EXPAND_RE.match(low):
        return "expand"
    if _ADD_MACRO_RE.match(low):
        return "add"
    if _LIST_MACROS_RE.match(low):
        return "list"
    return None


def gear0_macro_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0-Macro handler."""
    try:
        import re
        if handler == "expand":
            m = _EXPAND_RE.match(query)
            if m:
                return macro_expand(m.group(1))
        if handler == "add":
            m = _ADD_MACRO_RE.match(query)
            if m:
                return macro_add(m.group(1), m.group(2).strip())
        if handler == "list":
            return macro_list()
    except Exception:
        return None
    return None


__all__ = [
    "macro_expand",
    "macro_add",
    "macro_list",
    "gear0_macro_classify",
    "gear0_macro_execute",
]
