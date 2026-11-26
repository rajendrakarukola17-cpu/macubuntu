"""
Celina AI (Celly) - Gear 0 Ledger (Update 19)

Local expense & habit ledger — SQLite-backed, zero cloud, sub-5ms.
Examples:
  - "I spent $12 on lunch"
  - "Log habit: gym"
  - "Show my expenses today"
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path.home() / ".celina" / "storage" / "shared" / "gear0_ledger.db"


def _ledger_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            note TEXT,
            ts TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit TEXT NOT NULL,
            ts TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def ledger_expense(amount: float, category: str, note: str = "") -> str:
    """Record an expense."""
    try:
        conn = _ledger_conn()
        conn.execute(
            "INSERT INTO expenses (amount, category, note, ts) VALUES (?, ?, ?, ?)",
            (amount, category, note, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        return f"✓ Logged expense: ${amount:.2f} — {category}" + (f" ({note})" if note else "")
    except Exception as exc:
        return f"Failed to log expense: {exc}"


def ledger_habit(habit: str) -> str:
    """Log a habit completion."""
    try:
        conn = _ledger_conn()
        conn.execute(
            "INSERT INTO habits (habit, ts) VALUES (?, ?)",
            (habit, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        return f"✓ Habit logged: {habit}"
    except Exception as exc:
        return f"Failed to log habit: {exc}"


def ledger_expenses_today() -> str:
    """Show today's expenses."""
    try:
        conn = _ledger_conn()
        today = date.today().isoformat()
        rows = conn.execute(
            "SELECT amount, category, note FROM expenses WHERE ts LIKE ? ORDER BY ts DESC",
            (f"{today}%",),
        ).fetchall()
        conn.close()
        if not rows:
            return "No expenses logged today."
        total = sum(r[0] for r in rows)
        lines = [f"• ${r[0]:.2f} — {r[1]}" + (f" ({r[2]})" if r[2] else "") for r in rows]
        lines.append(f"Total today: ${total:.2f}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Failed to read expenses: {exc}"


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

_EXPENSE_RE = __import__("re").compile(
    r"^(?:i\s+)?spent?\s+\$?([\d.]+)\s+(?:on\s+)?(\w+)(?:\s+(?:for|:)?\s*(.+))?\??$",
    __import__("re").IGNORECASE,
)
_HABIT_RE = __import__("re").compile(
    r"^(?:log|record|track)\s+(?:habit\s+)?(.+)\??$",
    __import__("re").IGNORECASE,
)
_SHOW_EXPENSES_RE = __import__("re").compile(
    r"^(?:show|list)\s+(?:my\s+)?expenses\s+(?:for\s+)?(?:today)\??$",
    __import__("re").IGNORECASE,
)


def gear0_ledger_classify(query: str) -> Optional[str]:
    """Classify query into a ledger handler name, or None."""
    low = query.strip().lower()
    if _EXPENSE_RE.match(low):
        return "expense"
    if _HABIT_RE.match(low):
        return "habit"
    if _SHOW_EXPENSES_RE.match(low):
        return "expenses_today"
    return None


def gear0_ledger_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0-Ledger handler."""
    try:
        import re
        if handler == "expense":
            m = _EXPENSE_RE.match(query.lower())
            if m:
                amount = float(m.group(1))
                category = m.group(2)
                note = m.group(3) or ""
                return ledger_expense(amount, category, note)
        if handler == "habit":
            m = _HABIT_RE.match(query.lower())
            if m:
                return ledger_habit(m.group(1).strip())
        if handler == "expenses_today":
            return ledger_expenses_today()
    except Exception:
        return None
    return None


__all__ = [
    "ledger_expense",
    "ledger_habit",
    "ledger_expenses_today",
    "gear0_ledger_classify",
    "gear0_ledger_execute",
]
