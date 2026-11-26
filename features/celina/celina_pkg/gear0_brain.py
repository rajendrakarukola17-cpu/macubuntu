"""
Celina AI (Celly) - Gear 0-Brain: Unified Local SQLite Brain (Update 19)

Single local SQLite database backing all Gear 0 personal assistant features:
  - habits      (date, category, value, note)
  - expenses    (date, amount, category, note)
  - aliases     (name, kind, value)          -> contacts, paths, apps, urls
  - macros      (trigger, action_text, kind)  -> text expansion templates
  - clipboard   (timestamp, content_hash, content) -> last 20 entries
  - triggers    (id, kind, condition_json, action_json, active) -> state triggers

Zero LLM tokens, sub-5ms lookups, <5MB RAM, 100% local.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_BRAIN_DB_PATH = Path.home() / ".celina" / "storage" / "shared" / "assistant.db"


def _get_conn() -> sqlite3.Connection:
    _BRAIN_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_BRAIN_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("PRAGMA synchronous=NORMAL")

    # --- habits table ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            value REAL NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_habits_date ON habits(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_habits_category ON habits(category)")

    # --- expenses table ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category)")

    # --- aliases table (contacts, paths, apps, urls) ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aliases (
            name TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aliases_kind ON aliases(kind)")

    # --- macros table (text expansion) ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macros (
            trigger TEXT PRIMARY KEY,
            action_text TEXT NOT NULL,
            kind TEXT DEFAULT 'text',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # --- clipboard history (last 20 entries) ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clipboard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clipboard_ts ON clipboard(timestamp DESC)")

    # --- triggers table (state-based automation) ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            condition_json TEXT NOT NULL,
            action_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            active INTEGER DEFAULT 1
        )
    """)

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

def brain_log_habit(category: str, value: float = 1.0, note: str = "") -> str:
    """Log a habit entry: 'I did 30 pushups' → habits table."""
    try:
        conn = _get_conn()
        today = datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO habits (date, category, value, note) VALUES (?, ?, ?, ?)",
            (today, category, float(value), note),
        )
        conn.commit()
        conn.close()
        return f"Logged habit: {category} = {value}{' (' + note + ')' if note else ''}"
    except Exception as exc:
        return f"Failed to log habit: {exc}"


def brain_get_habits(category: Optional[str] = None, days: int = 7) -> list[dict[str, Any]]:
    """Get habit entries for the last N days."""
    try:
        conn = _get_conn()
        today = datetime.now()
        from datetime import timedelta
        start = (today - timedelta(days=days)).strftime("%Y-%m-%d")

        if category:
            rows = conn.execute(
                "SELECT date, category, value, note FROM habits WHERE date >= ? AND category = ? ORDER BY date DESC",
                (start, category),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT date, category, value, note FROM habits WHERE date >= ? ORDER BY date DESC",
                (start,),
            ).fetchall()
        conn.close()
        return [{"date": r[0], "category": r[1], "value": r[2], "note": r[3]} for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------

def brain_log_expense(amount: float, category: str, note: str = "") -> str:
    """Log an expense: 'Log 15 dollars for coffee' → expenses table."""
    try:
        conn = _get_conn()
        today = datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO expenses (date, amount, category, note) VALUES (?, ?, ?, ?)",
            (today, float(amount), category, note),
        )
        conn.commit()
        conn.close()
        return f"Logged expense: ${amount:.2f} for {category}{' (' + note + ')' if note else ''}"
    except Exception as exc:
        return f"Failed to log expense: {exc}"


def brain_get_expenses(category: Optional[str] = None, days: int = 30) -> list[dict[str, Any]]:
    """Get expense entries for the last N days."""
    try:
        conn = _get_conn()
        today = datetime.now()
        from datetime import timedelta
        start = (today - timedelta(days=days)).strftime("%Y-%m-%d")

        if category:
            rows = conn.execute(
                "SELECT date, amount, category, note FROM expenses WHERE date >= ? AND category = ? ORDER BY date DESC",
                (start, category),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT date, amount, category, note FROM expenses WHERE date >= ? ORDER BY date DESC",
                (start,),
            ).fetchall()
        conn.close()
        return [{"date": r[0], "amount": r[1], "category": r[2], "note": r[3]} for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Aliases (shared with gear0_graph but unified in brain)
# ---------------------------------------------------------------------------

def brain_set_alias(name: str, kind: str, value: str) -> str:
    """Store an alias: 'save mom as +1234567890 contact'."""
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO aliases (name, kind, value, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(name) DO UPDATE SET
                kind = excluded.kind,
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (name.lower().strip(), kind, value),
        )
        conn.commit()
        conn.close()
        return f"Saved alias: '{name}' → {kind} = {value}"
    except Exception as exc:
        return f"Failed to save alias: {exc}"


def brain_get_alias(name: str) -> Optional[dict[str, str]]:
    """Look up an alias."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT kind, value FROM aliases WHERE name = ?",
            (name.lower().strip(),),
        ).fetchone()
        conn.close()
        if row:
            return {"kind": row[0], "value": row[1]}
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Macros (text expansion templates)
# ---------------------------------------------------------------------------

def brain_set_macro(trigger: str, action_text: str, kind: str = "text") -> str:
    """Save a text expansion macro: 'macro ETA = Hi, running 15 min late'."""
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO macros (trigger, action_text, kind, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(trigger) DO UPDATE SET
                action_text = excluded.action_text,
                kind = excluded.kind,
                updated_at = datetime('now')
            """,
            (trigger.lower().strip(), action_text, kind),
        )
        conn.commit()
        conn.close()
        return f"Saved macro: '{trigger}' → '{action_text[:50]}...'"
    except Exception as exc:
        return f"Failed to save macro: {exc}"


def brain_get_macro(trigger: str) -> Optional[dict[str, str]]:
    """Look up a macro by trigger."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT action_text, kind FROM macros WHERE trigger = ?",
            (trigger.lower().strip(),),
        ).fetchone()
        conn.close()
        if row:
            return {"action_text": row[0], "kind": row[1]}
        return None
    except Exception:
        return None


def brain_list_macros() -> list[dict[str, str]]:
    """List all saved macros."""
    try:
        conn = _get_conn()
        rows = conn.execute("SELECT trigger, action_text, kind FROM macros ORDER BY trigger").fetchall()
        conn.close()
        return [{"trigger": r[0], "action_text": r[1], "kind": r[2]} for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Clipboard history
# ---------------------------------------------------------------------------

def brain_clipboard_add(content: str) -> str:
    """Add a clipboard entry (deduplicated by hash)."""
    try:
        import hashlib
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

        conn = _get_conn()
        # Check if same content already at top
        top = conn.execute("SELECT content_hash FROM clipboard ORDER BY id DESC LIMIT 1").fetchone()
        if top and top[0] == content_hash:
            conn.close()
            return "Clipboard entry already at top (deduplicated)."

        conn.execute(
            "INSERT INTO clipboard (timestamp, content_hash, content) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), content_hash, content),
        )
        # Keep only last 20 entries
        conn.execute("DELETE FROM clipboard WHERE id NOT IN (SELECT id FROM clipboard ORDER BY id DESC LIMIT 20)")
        conn.commit()
        conn.close()
        return f"Clipboard entry saved ({len(content)} chars)."
    except Exception as exc:
        return f"Failed to save clipboard: {exc}"


def brain_clipboard_last(n: int = 1) -> list[dict[str, str]]:
    """Get the last N clipboard entries."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT timestamp, content FROM clipboard ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
        conn.close()
        return [{"timestamp": r[0], "content": r[1]} for r in rows]
    except Exception:
        return []


def brain_clipboard_clear() -> str:
    """Clear clipboard history."""
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM clipboard")
        conn.commit()
        conn.close()
        return "Clipboard history cleared."
    except Exception as exc:
        return f"Failed to clear clipboard: {exc}"


# ---------------------------------------------------------------------------
# Triggers (state-based automation)
# ---------------------------------------------------------------------------

def brain_add_trigger(kind: str, condition: dict[str, Any], action: dict[str, Any]) -> str:
    """Register a state-based trigger."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO triggers (kind, condition_json, action_json) VALUES (?, ?, ?)",
            (kind, json.dumps(condition), json.dumps(action)),
        )
        conn.commit()
        conn.close()
        return f"Registered {kind} trigger."
    except Exception as exc:
        return f"Failed to register trigger: {exc}"


def brain_list_triggers() -> list[dict[str, Any]]:
    """List all active triggers."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, kind, condition_json, action_json, active FROM triggers WHERE active = 1"
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "condition": json.loads(r[2]),
                "action": json.loads(r[3]),
                "active": bool(r[4]),
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def brain_summary() -> dict[str, Any]:
    """Return a summary of all brain data for quick status."""
    try:
        conn = _get_conn()
        habit_count = conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0]
        expense_count = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        alias_count = conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
        macro_count = conn.execute("SELECT COUNT(*) FROM macros").fetchone()[0]
        clipboard_count = conn.execute("SELECT COUNT(*) FROM clipboard").fetchone()[0]
        trigger_count = conn.execute("SELECT COUNT(*) FROM triggers WHERE active = 1").fetchone()[0]

        # Today's totals
        today = datetime.now().strftime("%Y-%m-%d")
        today_expense = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE date = ?", (today,)).fetchone()[0]

        conn.close()

        return {
            "habits": habit_count,
            "expenses": expense_count,
            "aliases": alias_count,
            "macros": macro_count,
            "clipboard_entries": clipboard_count,
            "active_triggers": trigger_count,
            "today_expense_total": today_expense,
            "db_path": str(_BRAIN_DB_PATH),
        }
    except Exception:
        return {}


__all__ = [
    "brain_log_habit",
    "brain_get_habits",
    "brain_log_expense",
    "brain_get_expenses",
    "brain_set_alias",
    "brain_get_alias",
    "brain_set_macro",
    "brain_get_macro",
    "brain_list_macros",
    "brain_clipboard_add",
    "brain_clipboard_last",
    "brain_clipboard_clear",
    "brain_add_trigger",
    "brain_list_triggers",
    "brain_summary",
]
