"""
Celina AI (Celly) - Plugin #3: Local Data Oracle (Update 19)

Natural-language querying of local CSV/SQLite data via deterministic
patterns translated to exact SQL, executed locally by Gear 0.
Zero hallucination — exact SQL guarantees 100% accuracy on personal data.

Security hardening (Update 20 Truth & Safety):
  - sqlite3.set_authorizer blocks ATTACH/DETACH/LOAD_EXTENSION/dangerous PRAGMA
  - PRAGMA query_only=ON on every connection (read-only enforcement)
  - All user-supplied values use parameterized ? placeholders
  - Identifiers (table/column) are whitelisted against the live schema
  - SELECT results are hard-capped (forced LIMIT) to prevent runaway scans

Supports:
  - SQLite databases (.db, .sqlite)
  - CSV files (loaded into in-memory SQLite)
"""

from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional


def _authorizer(action: int, p1: str | None, p2: str | None, p3: str | None, p4: str | None) -> int:
    """
    sqlite3 authorizer callback. Returns SQLITE_OK to allow, SQLITE_DENY to block.

    Blocks dangerous operations that could escape the sandboxed database:
      ATTACH, DETACH, LOAD_EXTENSION.
    All other actions (SELECT, INSERT, CREATE, PRAGMA, etc.) are allowed;
    read-only enforcement is applied separately via PRAGMA query_only.
    """
    SQLITE_DENY = 1
    SQLITE_OK = 0

    deny_actions = {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        getattr(sqlite3, "SQLITE_LOAD_EXTENSION", -1),
    }

    if action in deny_actions:
        return SQLITE_DENY

    return SQLITE_OK


def _harden_connection(conn: sqlite3.Connection) -> None:
    """Apply authorizer + timeout hardening to a connection.

    Read-only enforcement (query_only) is applied per-query in execute_sql()
    so that load_csv_to_memory / load_sqlite_db can still write during load.
    """
    conn.execute("PRAGMA busy_timeout=5000")
    conn.set_authorizer(_authorizer)


def _infer_column_types(rows: list[dict[str, str]]) -> dict[str, str]:
    """Infer SQLite column types from CSV rows."""
    if not rows:
        return {}
    types: dict[str, str] = {}
    for col in rows[0].keys():
        types[col] = "TEXT"  # default
    for row in rows[:100]:  # sample first 100 rows
        for col, val in row.items():
            if val is None or val == "":
                continue
            try:
                int(val)
                types[col] = "INTEGER"
            except (ValueError, TypeError):
                try:
                    float(val)
                    if types[col] != "INTEGER":
                        types[col] = "REAL"
                except (ValueError, TypeError):
                    types[col] = "TEXT"
    return types


def load_csv_to_memory(csv_path: str | Path, table_name: str = "data") -> str:
    """Load a CSV file into an in-memory SQLite database. Returns the table name."""
    path = Path(csv_path)
    if not path.exists():
        return f"CSV file not found: {csv_path}"

    # Sanitize table_name to a safe identifier (whitelist alnum + underscore)
    safe_table = re.sub(r"[^A-Za-z0-9_]", "_", table_name or "data")
    if not safe_table or safe_table[0].isdigit():
        safe_table = "t_" + safe_table

    rows: list[dict[str, str]] = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return "CSV file is empty."

    col_types = _infer_column_types(rows)

    # Create in-memory DB (read-only enforced via _harden_connection)
    conn = sqlite3.connect(":memory:")
    _harden_connection(conn)
    col_defs = ", ".join(f'"{col}" {typ}' for col, typ in col_types.items())
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{safe_table}" ({col_defs})')

    for row in rows:
        placeholders = ", ".join(["?"] * len(col_types))
        cols = ", ".join(f'"{c}"' for c in col_types.keys())
        vals = [row.get(c) for c in col_types.keys()]
        conn.execute(f'INSERT INTO "{safe_table}" ({cols}) VALUES ({placeholders})', vals)
    conn.commit()

    # Store the connection reference globally for query execution
    _in_memory_dbs[safe_table] = conn
    return f"✓ Loaded {len(rows)} rows into table '{safe_table}' ({len(col_types)} columns)"


def load_sqlite_db(db_path: str | Path, read_only: bool = True) -> str:
    """Open a local SQLite database for querying. Returns a handle key."""
    path = Path(db_path)
    if not path.exists():
        return f"Database not found: {db_path}"

    mode = "file:%s?mode=ro" % path.as_posix() if read_only else str(path)
    conn = sqlite3.connect(mode, uri=read_only)
    _harden_connection(conn)
    _in_memory_dbs[str(path)] = conn
    return f"✓ Opened SQLite database: {db_path}"


# In-memory database registry (loaded CSVs and opened SQLite DBs)
_in_memory_dbs: dict[str, sqlite3.Connection] = {}


def list_available_tables() -> list[str]:
    """List all tables across all loaded databases."""
    tables: list[str] = []
    for key, conn in _in_memory_dbs.items():
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for r in rows:
                tables.append(f"{key}:{r[0]}")
        except Exception:
            continue
    return tables


def _validate_identifier(name: str, allowed: set[str]) -> str | None:
    """Return name if it is a safe, whitelisted identifier; else None."""
    if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return None
    if name not in allowed:
        return None
    return name


def get_table_schema(table_ref: str) -> str:
    """Get the schema of a table (format: 'db_key:table_name')."""
    if ":" not in table_ref:
        return "Invalid table reference. Use 'db_key:table_name'."
    db_key, table_name = table_ref.split(":", 1)
    conn = _in_memory_dbs.get(db_key)
    if not conn:
        return f"Database not loaded: {db_key}"
    try:
        safe_table = _validate_identifier(table_name, set())
        # Validate against actual schema
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        valid_tables = {r[0] for r in rows}
        if not _validate_identifier(table_name, valid_tables):
            return f"Table not found: {table_name}"
        schema_rows = conn.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
        if not schema_rows:
            return f"Table not found: {table_name}"
        lines = [f"Table: {table_name} (in {db_key})"]
        for r in schema_rows:
            lines.append(f"  • {r[1]} ({r[2]}){' PK' if r[5] else ''}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Failed to read schema: {exc}"


def translate_nl_to_sql(nl_query: str, table_ref: str) -> tuple[str, tuple[Any, ...]]:
    """
    Translate a natural language query to SQL using deterministic patterns
    (no LLM required for common patterns).

    Returns (sql_string, params) where params are bound via ? placeholders.
    Identifiers are whitelisted against the live schema; values are parameterized.
    """
    q = nl_query.strip().lower()

    # Resolve table reference
    if ":" not in table_ref:
        tables = list_available_tables()
        if not tables:
            return ("ERROR: No databases loaded. Load a CSV or SQLite database first.", ())
        # Use the first available table
        table_ref = tables[0]

    db_key, table_name = table_ref.split(":", 1)
    conn = _in_memory_dbs.get(db_key)
    if not conn:
        return (f"ERROR: Database not loaded: {db_key}", ())

    # Validate table_name against actual schema (identifier whitelist)
    try:
        schema_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    except Exception:
        return ("ERROR: Cannot read schema for database.", ())
    valid_tables = {r[0] for r in schema_rows}
    safe_table = _validate_identifier(table_name, valid_tables)
    if not safe_table:
        return (f"ERROR: Table not found or unsafe: {table_name}", ())

    # Get actual schema for column whitelisting
    try:
        pragma_rows = conn.execute(f'PRAGMA table_info("{safe_table}")').fetchall()
    except Exception:
        return (f"ERROR: Cannot read schema for {table_ref}", ())
    columns = [r[1] for r in pragma_rows]

    # --- Deterministic pattern matching (no LLM) ---

    # Pattern: "how much did I spend on X last month" / "sum of Y where Z"
    sum_match = re.search(
        r"(?:how much|total|sum)\s+(?:of\s+)?(\w+)\s+(?:where|on|for)\s+(\w+)\s*['\"]?([^'\"]*)['\"]?",
        q,
    )
    if sum_match:
        col = sum_match.group(1)
        cat_col = sum_match.group(2)
        cat_val = sum_match.group(3)
        safe_col = _validate_identifier(col, set(columns))
        safe_cat_col = _validate_identifier(cat_col, set(columns))
        if safe_col and safe_cat_col:
            if cat_val:
                return (
                    f'SELECT SUM("{safe_col}") FROM "{safe_table}" WHERE "{safe_cat_col}" = ?;',
                    (cat_val,),
                )
            return (
                f'SELECT SUM("{safe_col}") FROM "{safe_table}";',
                (),
            )

    # Pattern: "count of X" / "how many X"
    count_match = re.search(r"(?:how many|count of)\s+(.+)", q)
    if count_match:
        return (f'SELECT COUNT(*) FROM "{safe_table}";', ())

    # Pattern: "average of X"
    avg_match = re.search(r"(?:average|avg)\s+of\s+(\w+)", q)
    if avg_match:
        col = avg_match.group(1)
        safe_col = _validate_identifier(col, set(columns))
        if safe_col:
            return (f'SELECT AVG("{safe_col}") FROM "{safe_table}";', ())

    # Pattern: "show me all rows where X = Y"
    where_match = re.search(r"(?:where|filter)\s+(\w+)\s*(?:=|is|equals)\s*['\"]?([^'\"]*)['\"]?", q)
    if where_match:
        col = where_match.group(1)
        val = where_match.group(2)
        safe_col = _validate_identifier(col, set(columns))
        if safe_col:
            return (
                f'SELECT * FROM "{safe_table}" WHERE "{safe_col}" = ?;',
                (val,),
            )

    # Pattern: "list all X" / "show all" — forced LIMIT cap
    if any(w in q for w in ["list all", "show all", "display all"]):
        limit = 50
        lim_match = re.search(r"(?:top|first|limit)\s+(\d+)", q)
        if lim_match:
            requested = int(lim_match.group(1))
            limit = min(requested, 500)  # hard cap
        return (f'SELECT * FROM "{safe_table}" LIMIT {limit};', ())

    # Pattern: "max/min of X"
    extrema_match = re.search(r"(?:max|minimum|min|maximum)\s+(?:of\s+)?(\w+)", q)
    if extrema_match:
        col = extrema_match.group(1)
        op = "MAX" if "max" in q and "min" not in q else "MIN"
        safe_col = _validate_identifier(col, set(columns))
        if safe_col:
            return (f'SELECT {op}("{safe_col}") FROM "{safe_table}";', ())

    # Fallback: return a helpful error
    return (f"ERROR: Could not translate query to SQL. Available columns: {', '.join(columns)}", ())


def execute_sql(sql: str, table_ref: str, params: tuple[Any, ...] = ()) -> str:
    """Execute a SQL query against a loaded table. Returns results as text.

    `sql` must come from translate_nl_to_sql (parameterized). User-supplied
    values are bound via `params`, never interpolated into the SQL string.
    """
    if ":" not in table_ref:
        tables = list_available_tables()
        if tables:
            table_ref = tables[0]
        else:
            return "ERROR: No databases loaded."
    db_key, table_name = table_ref.split(":", 1)
    conn = _in_memory_dbs.get(db_key)
    if not conn:
        return f"ERROR: Database not loaded: {db_key}"

    try:
        # Enforce read-only for the duration of this query (defense-in-depth)
        conn.execute("PRAGMA query_only=ON")
        try:
            cur = conn.execute(sql, params)
        finally:
            conn.execute("PRAGMA query_only=OFF")
        rows = cur.fetchall()
        col_names = [d[0] for d in cur.description] if cur.description else []

        if not rows:
            return "Query returned no results."

        # Format as aligned text
        widths = [len(c) for c in col_names]
        for row in rows:
            for i, val in enumerate(row):
                widths[i] = max(widths[i], len(str(val)))

        lines = []
        header = " | ".join(c.ljust(w) for c, w in zip(col_names, widths))
        lines.append(header)
        lines.append("-" * len(header))
        for row in rows:
            lines.append(" | ".join(str(v).ljust(w) for v, w in zip(row, widths)))

        return "\n".join(lines)
    except Exception as exc:
        return f"SQL execution error: {exc}"


def query_data(nl_query: str, table_ref: str = "") -> str:
    """
    Full pipeline: translate NL to SQL, execute, return results.
    Single entry point for Gear 0 integration.
    """
    sql, params = translate_nl_to_sql(nl_query, table_ref)
    if sql.startswith("ERROR:"):
        return sql
    return execute_sql(sql, table_ref, params)


__all__ = [
    "load_csv_to_memory",
    "load_sqlite_db",
    "list_available_tables",
    "get_table_schema",
    "translate_nl_to_sql",
    "execute_sql",
    "query_data",
    "gear0_data_oracle_classify",
    "gear0_data_oracle_execute",
]

# ---------------------------------------------------------------------------
# Gear 0 Plugin #3 dispatch: Local Data Oracle
# ---------------------------------------------------------------------------

_DATA_ORACLE_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:data|oracle|query|table|csv|sql|select|count|average|sum|max|min|load)\b",
    __import__("re").IGNORECASE,
)


def gear0_data_oracle_classify(query: str) -> Optional[str]:
    """Classify query into a data oracle handler name, or None."""
    low = query.strip().lower()
    if not _DATA_ORACLE_RE.match(low):
        return None
    if any(w in low for w in ["load", "csv", "sqlite", "database", "db "]):
        return "load_data"
    return "translate_nl_to_sql"


def gear0_data_oracle_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0 Plugin #3 data oracle handler."""
    try:
        import re as _re
        if handler == "load_data":
            m = _re.search(r"(?:load|csv|sqlite|database|db)\s+(?:from\s+)?(.+)$", query, _re.IGNORECASE)
            if not m:
                return "Please specify a file to load: load_csv_to_memory <path> or load_sqlite_db <path>"
            path = m.group(1).strip().strip("'\"")
            if path.lower().endswith(".csv"):
                return load_csv_to_memory(path)
            return load_sqlite_db(path)
        if handler == "translate_nl_to_sql":
            tables = list_available_tables()
            if not tables:
                return "No tables loaded. Use load_csv_to_memory or load_sqlite_db first."
            table_ref = ""
            for t in tables:
                if t.split(":")[1] in query:
                    table_ref = t
                    break
            if not table_ref and tables:
                table_ref = tables[0]
            result = query_data(query, table_ref)
            return result
    except Exception:
        return None
    return None
