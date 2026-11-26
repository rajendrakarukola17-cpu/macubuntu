"""Celina AI (Celly) - Gear 0 Lists (Update 19)

Alexa-style instant shopping/task lists — local SQLite, zero LLM tokens.

Examples:
  - "Add almond milk and eggs to the shopping list"
  - "Add call the bank to my todo list"
  - "What's on my shopping list?"

"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_LISTS_DB_PATH = Path.home() / ".celina" / "storage" / "shared" / "lists.db"
_LISTS_CONN: sqlite3.Connection | None = None
_LISTS_INITIALIZED = False


def _get_conn() -> sqlite3.Connection:
    global _LISTS_CONN, _LISTS_INITIALIZED
    if _LISTS_CONN is not None:
        return _LISTS_CONN
    _LISTS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_LISTS_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_name TEXT NOT NULL,
            item TEXT NOT NULL,
            checked INTEGER DEFAULT 0,
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            checked_at TEXT DEFAULT NULL
        )
    """)
    if not _LISTS_INITIALIZED:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lists_name ON lists(list_name)")
        conn.commit()
        _LISTS_INITIALIZED = True
    _LISTS_CONN = conn
    return conn


_LIST_RE = re.compile(
    r"^(?:please\s+)?add\s+(?P<items>.+?)\s+to\s+(?:my\s+)?(?P<list_name>\w+)\s+list\??$",
    re.IGNORECASE,
)
_LIST_CHECK_RE = re.compile(
    r"^(?:please\s+)?(?:check|uncheck|mark|complete)\s+(?P<item>.+?)\s+(?:on\s+)?(?P<list_name>\w+)\s+list\??$",
    re.IGNORECASE,
)
_LIST_SHOW_RE = re.compile(
    r"^(?:please\s+)?(?:what'?s|what is|show)\s+(?:on\s+)?(?:my\s+)?(?P<list_name>\w+)\s+list\??$",
    re.IGNORECASE,
)


def list_add_items(list_name: str, items: list[str]) -> str:
    """Add items to a list."""
    try:
        conn = _get_conn()
        conn.executemany(
            "INSERT INTO lists (list_name, item, checked) VALUES (?, ?, 0)",
            [(list_name.lower().strip(), item.strip()) for item in items],
        )
        conn.commit()
        return f"Added {len(items)} item(s) to {list_name} list."
    except Exception as exc:
        return f"Failed to add items: {exc}"


def list_toggle_item(list_name: str, item_pattern: str, check: bool = True) -> str:
    """Check or uncheck an item on a list."""
    try:
        conn = _get_conn()
        if check:
            conn.execute(
                "UPDATE lists SET checked = 1, checked_at = datetime('now') WHERE list_name = ? AND item LIKE ?",
                (list_name.lower().strip(), f"%{item_pattern}%"),
            )
        else:
            conn.execute(
                "UPDATE lists SET checked = 0, checked_at = NULL WHERE list_name = ? AND item LIKE ?",
                (list_name.lower().strip(), f"%{item_pattern}%"),
            )
        changes = conn.total_changes
        conn.commit()
        if changes > 0:
            return f"{'Checked' if check else 'Unchecked'} '{item_pattern}' on {list_name} list."
        return f"Item '{item_pattern}' not found on {list_name} list."
    except Exception as exc:
        return f"Failed to update item: {exc}"


def list_show(list_name: str) -> str:
    """Show all items on a list."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT item, checked FROM lists WHERE list_name = ? ORDER BY added_at",
            (list_name.lower().strip(),),
        ).fetchall()
        if not rows:
            return f"{list_name} list is empty."
        lines = [f"{list_name.title()} List:"]
        for item, checked in rows:
            marker = "✓" if checked else "○"
            lines.append(f"  {marker} {item}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Failed to read list: {exc}"


def list_clear(list_name: str) -> str:
    """Clear all items from a list."""
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM lists WHERE list_name = ?", (list_name.lower().strip(),))
        conn.commit()
        return f"Cleared {list_name} list."
    except Exception as exc:
        return f"Failed to clear list: {exc}"


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

def gear0_lists_classify(query: str) -> str | None:
    """Classify query into a list handler name, or None."""
    low = query.strip().lower()

    if _LIST_RE.match(low):
        return "list_add"
    if _LIST_CHECK_RE.match(low):
        return "list_toggle"
    if _LIST_SHOW_RE.match(low):
        return "list_show"

    return None


def gear0_lists_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Lists handler."""
    try:
        if handler == "list_add":
            m = _LIST_RE.match(query.strip())
            if m:
                items_raw = m.group("items")
                list_name = m.group("list_name")
                # Split items by "and" or commas
                items = [i.strip() for i in re.split(r"\s+and\s+|,\s*", items_raw) if i.strip()]
                if dry_run:
                    return f"🧪 Dry-Run [Gear 0-Lists]: Would add {items} to {list_name} list."
                return list_add_items(list_name, items)
        if handler == "list_toggle":
            m = _LIST_CHECK_RE.match(query.strip())
            if m:
                item = m.group("item")
                list_name = m.group("list_name")
                check = "uncheck" not in query.strip().lower()
                if dry_run:
                    return f"🧪 Dry-Run [Gear 0-Lists]: Would {'check' if check else 'uncheck'} '{item}' on {list_name} list."
                return list_toggle_item(list_name, item, check=check)
        if handler == "list_show":
            m = _LIST_SHOW_RE.match(query.strip())
            if m:
                return list_show(m.group("list_name"))
    except Exception:
        return None
    return None


__all__ = [
    "gear0_lists_classify",
    "gear0_lists_execute",
    "list_add_items",
    "list_clear",
    "list_show",
    "list_toggle_item",
]
