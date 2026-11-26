"""Celina AI (Celly) - Gear 0-Graph: Local Personal Knowledge Base (Update 19)

SQLite key-value / alias graph for Siri-like contact and alias resolution.

Examples:
  - "Call Mom" → SQLite lookup: Mom = +1234567890 → tel:+1234567890
  - "Open my work project" → SQLite lookup: work project = /home/user/dev/celina → open VS Code

Zero LLM tokens, sub-2ms lookup, <5MB RAM.

"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_GRAPH_DB_PATH = Path.home() / ".celina" / "storage" / "shared" / "graph.db"


def _get_conn() -> sqlite3.Connection:
    """Get or create the graph database connection."""
    _GRAPH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_GRAPH_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS aliases (
        alias TEXT PRIMARY KEY COLLATE NOCASE,
        kind TEXT NOT NULL,          -- 'contact', 'path', 'app', 'url', 'command'
        value TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_aliases_kind ON aliases(kind)
    """)
    conn.commit()
    return conn


def graph_set(alias: str, kind: str, value: str) -> str:
    """Store an alias → value mapping."""
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO aliases (alias, kind, value, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(alias) DO UPDATE SET
                kind = excluded.kind,
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (alias.strip(), kind, value),
        )
        conn.commit()
        conn.close()
        return f"Saved: '{alias}' → {kind} = {value}"
    except Exception as exc:
        return f"Failed to save alias: {exc}"


def graph_get(alias: str) -> dict[str, str] | None:
    """Look up an alias → value mapping."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT kind, value FROM aliases WHERE alias = ?",
            (alias.strip(),),
        ).fetchone()
        conn.close()
        if row:
            return {"kind": row[0], "value": row[1]}
        return None
    except Exception:
        return None


def graph_list(kind: str | None = None) -> list[dict[str, str]]:
    """List all aliases, optionally filtered by kind."""
    try:
        conn = _get_conn()
        if kind:
            rows = conn.execute(
                "SELECT alias, kind, value FROM aliases WHERE kind = ? ORDER BY alias",
                (kind,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT alias, kind, value FROM aliases ORDER BY alias",
            ).fetchall()
        conn.close()
        return [{"alias": r[0], "kind": r[1], "value": r[2]} for r in rows]
    except Exception:
        return []


def graph_delete(alias: str) -> str:
    """Delete an alias."""
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM aliases WHERE alias = ?", (alias.lower().strip(),))
        conn.commit()
        conn.close()
        return f"Deleted alias: '{alias}'"
    except Exception as exc:
        return f"Failed to delete alias: {exc}"


# ---------------------------------------------------------------------------
# Siri-style command handlers using the graph
# ---------------------------------------------------------------------------

_CALL_RE = re.compile(r"^(?:please\s+)?call\s+(?P<alias>.+)$", re.IGNORECASE)
_TEXT_RE = re.compile(r"^(?:please\s+)?(?:text|message)\s+(?P<alias>.+?)\s+(?:saying\s+)?(?P<msg>.+)$", re.IGNORECASE)
_OPEN_RE = re.compile(r"^(?:please\s+)?open\s+my\s+(?P<alias>.+)$", re.IGNORECASE)


def handle_call(alias: str) -> str:
    """Resolve 'Call Mom' via graph → phone number → tel: URL."""
    entry = graph_get(alias)
    if not entry:
        return f"I don't have a contact named '{alias}'. Say 'save contact {alias} as <number>' to add one."
    if entry["kind"] != "contact":
        return f"'{alias}' is a {entry['kind']}, not a contact."
    return f"📞 Calling {alias} at {entry['value']} (tel:{entry['value']})"


def handle_text(alias: str, message: str) -> str:
    """Resolve 'Text Mom saying hello' via graph → phone number."""
    entry = graph_get(alias)
    if not entry:
        return f"I don't have a contact named '{alias}'."
    if entry["kind"] != "contact":
        return f"'{alias}' is a {entry['kind']}, not a contact."
    return f"💬 Texting {alias} ({entry['value']}): {message}"


def handle_open_my(alias: str) -> str:
    """Resolve 'Open my work project' via graph → path → open in file manager/editor."""
    entry = graph_get(alias)
    if not entry:
        return f"I don't have anything saved as '{alias}'. Say 'save {alias} as <path>' to add one."
    if entry["kind"] == "path":
        return f"📂 Opening {alias} at {entry['value']}"
    if entry["kind"] == "app":
        return f"🚀 Launching {alias} ({entry['value']})"
    if entry["kind"] == "url":
        return f"🌐 Opening {alias} at {entry['value']}"
    return f"❓ '{alias}' is a {entry['kind']}: {entry['value']}"


# ---------------------------------------------------------------------------
# Master dispatch for graph commands
# ---------------------------------------------------------------------------

def gear0_graph_classify(query: str) -> str | None:
    """Classify query into a graph handler name, or None."""
    import re as _re

    low = query.strip().lower()

    if _CALL_RE.match(low):
        return "call"
    if _TEXT_RE.match(low):
        return "text"
    if _OPEN_RE.match(low):
        return "open_my"

    # Direct alias save: "save mom as +1234567890 contact"
    save_re = _re.compile(
        r"^(?:please\s+)?save\s+(?P<alias>.+?)\s+as\s+(?P<value>.+?)\s+(?P<kind>contact|path|app|url|command)$",
        _re.IGNORECASE,
    )
    if save_re.match(low):
        return "save"

    return None


def gear0_graph_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Graph handler."""
    import re as _re

    try:
        if handler == "call":
            m = _CALL_RE.match(query.strip())
            if m:
                return handle_call(m.group("alias"))
        elif handler == "text":
            m = _TEXT_RE.match(query.strip())
            if m:
                return handle_text(m.group("alias"), m.group("msg"))
        elif handler == "open_my":
            m = _OPEN_RE.match(query.strip())
            if m:
                return handle_open_my(m.group("alias"))
        elif handler == "save":
            save_re = _re.compile(
                r"^(?:please\s+)?save\s+(?P<alias>.+?)\s+as\s+(?P<value>.+?)\s+(?P<kind>contact|path|app|url|command)$",
                _re.IGNORECASE,
            )
            m = save_re.match(query.strip())
            if m:
                if dry_run:
                    return f"🧪 Dry-Run [Gear 0-Graph]: Would save '{m.group('alias')}' → {m.group('kind')} = {m.group('value')}."
                return graph_set(m.group("alias"), m.group("kind"), m.group("value"))
    except Exception:
        return None
    return None


__all__ = [
    "gear0_graph_classify",
    "gear0_graph_execute",
    "graph_delete",
    "graph_get",
    "graph_list",
    "graph_set",
]
