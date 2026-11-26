"""Celina AI (Celly) - Gear 0 Clipboard History (Update 19)

Local clipboard history — stores copied text locally, zero cloud.

Examples:
  - "Show clipboard history"
  - "Paste last item"

"""

from __future__ import annotations

from collections import deque
from pathlib import Path

_HISTORY_PATH = Path.home() / ".local" / "share" / "celina" / "gear0_clipboard_history.txt"
_MAX_HISTORY = 50
_CLIPBOARD: deque[str] = deque(maxlen=_MAX_HISTORY)

# Password-manager app names — clipboard content from these should not be stored
PASSWORD_MANAGER_APPS = frozenset({
    "keepassxc", "keepass", "1password", "bitwarden", "lastpass",
    "dashlane", "robomform", "enpass", "pass", "gpg",
})

# MIME types that indicate sensitive/password-manager content
SENSITIVE_MIME_TYPES = frozenset({
    "application/x-keepass", "application/x-kdbx", "application/password-manager",
})


def _init_clipboard() -> None:
    """Load existing clipboard history from disk."""
    try:
        if _HISTORY_PATH.exists():
            for line in _HISTORY_PATH.read_text(errors="ignore").splitlines():
                if line:
                    _CLIPBOARD.append(line)
    except Exception:
        pass


_init_clipboard()


def _is_sensitive_content(text: str) -> bool:
    """Check if clipboard content appears to be from a password manager."""
    low = text.lower()
    for app in PASSWORD_MANAGER_APPS:
        if app in low:
            return True
    # Check for MIME type hints in content
    for mime in SENSITIVE_MIME_TYPES:
        if mime in low:
            return True
    return False


def clipboard_add(text: str) -> None:
    """Add text to clipboard history."""
    if not text or text in _CLIPBOARD:
        return
    if _is_sensitive_content(text):
        return  # Do not store password-manager content
    _CLIPBOARD.appendleft(text)
    try:
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_PATH.write_text("\n".join(_CLIPBOARD))
    except Exception:
        pass


def clipboard_history() -> str:
    """Show recent clipboard history."""
    if not _CLIPBOARD:
        return "Clipboard history is empty."
    lines = [f"{i+1}. {item[:80]}" for i, item in enumerate(list(_CLIPBOARD)[:10])]
    return "\n".join(lines)


def clipboard_paste_last() -> str | None:
    """Get the most recent clipboard item."""
    if _CLIPBOARD:
        return _CLIPBOARD[0]
    return None


# ---------------------------------------------------------------------------
# Master dispatch
# -------------------------------------------------------------------------__

_SHOW_CLIPBOARD_RE = __import__("re").compile(
    r"^(?:show|list)\s+(?:my\s+)?clipboard\s+(?:history)\??$",
    __import__("re").IGNORECASE,
)


def gear0_clipboard_classify(query: str) -> str | None:
    """Classify query into a clipboard handler name, or None."""
    low = query.strip().lower()
    if _SHOW_CLIPBOARD_RE.match(low):
        return "history"
    return None


def gear0_clipboard_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Clipboard handler."""
    try:
        if handler == "history":
            return clipboard_history()
    except Exception:
        return None
    return None


__all__ = [
    "clipboard_add",
    "clipboard_history",
    "clipboard_paste_last",
    "gear0_clipboard_classify",
    "gear0_clipboard_execute",
]
