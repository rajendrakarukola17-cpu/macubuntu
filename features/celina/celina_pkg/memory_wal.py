"""Async WAL-mode SQLite manager with single-writer queue.

Migrates Celina's SQLite tables to async WAL mode using aiosqlite with
PRAGMA journal_mode=WAL, busy_timeout=5000, and a single-writer asyncio
queue to prevent 'database is locked' errors under concurrent access.

Uses only stdlib + aiosqlite + sqlite-vec (optional).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any

try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False

log = logging.getLogger("celina.memory_wal")


class AsyncWalStore:
    """Single-writer async SQLite store in WAL mode.

    All writes go through a single asyncio queue processed by one writer
    task, eliminating 'database is locked' errors. Reads can proceed
    concurrently via separate connections.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        max_queue_size: int = 1000,
    ) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self._queue: asyncio.Queue[tuple[str, tuple[Any, ...], asyncio.Future]] | None = None
        self._writer_task: asyncio.Task | None = None
        self._started = False

    async def start(self) -> None:
        """Initialize the database and start the single-writer task."""
        if not HAS_AIOSQLITE:
            raise RuntimeError(
                "aiosqlite is required for AsyncWalStore. Install with: pip install aiosqlite",
            )

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # One-time schema initialization on a fresh connection
        async with aiosqlite.connect(str(self.db_path)) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA foreign_keys=ON;")
            await conn.commit()

        self._queue = asyncio.Queue(maxsize=max_queue_size)
        self._started = True
        # Writer task is lazily started on first write to avoid needing a running loop at init

    async def _ensure_writer(self) -> None:
        """Start the writer task if not already running."""
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._writer_loop())

    async def _writer_loop(self) -> None:
        """Single-writer loop: dequeues write operations and executes them serially."""
        assert self._queue is not None
        while True:
            sql, params, future = await self._queue.get()
            try:
                async with aiosqlite.connect(str(self.db_path)) as conn:
                    await conn.execute("PRAGMA journal_mode=WAL;")
                    await conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
                    await conn.execute("PRAGMA synchronous=NORMAL;")
                    await conn.execute("PRAGMA foreign_keys=ON;")
                    await conn.execute(sql, params)
                    await conn.commit()
                if not future.done():
                    future.set_result(None)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def execute_write(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Enqueue a write operation to the single-writer queue."""
        if not self._started:
            await self.start()
        await self._ensure_writer()
        assert self._queue is not None
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        await self._queue.put((sql, params, future))
        await future

    async def execute_read(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Execute a read-only query on a separate connection."""
        if not self._started:
            await self.start()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms};")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA foreign_keys=ON;")
            conn.row_factory = sqlite3.Row
            async with conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def execute_read_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Execute a read query returning at most one row."""
        rows = await self.execute_read(sql, params)
        return rows[0] if rows else None

    async def migrate_tables(self, ddl_statements: list[str]) -> None:
        """Run CREATE TABLE IF NOT EXISTS DDL statements under the writer queue."""
        for ddl in ddl_statements:
            await self.execute_write(ddl, ())

    async def close(self) -> None:
        """Stop the writer task and drain the queue."""
        if self._writer_task and not self._writer_task.done():
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            self._writer_task = None

    def __del__(self) -> None:
        if self._writer_task and not self._writer_task.done():
            self._writer_task.cancel()


# Convenience: migrate memory.py's 15 tables to WAL async store
MEMORY_TABLE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episodes (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS facts (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'user',
        confidence REAL NOT NULL DEFAULT 1.0,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reminders (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        due_at INTEGER NOT NULL,
        done INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skills (
        name TEXT PRIMARY KEY,
        description TEXT NOT NULL DEFAULT '',
        file_path TEXT NOT NULL,
        checksum TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skill_runs (
        id TEXT PRIMARY KEY,
        skill_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        started_at INTEGER NOT NULL,
        finished_at INTEGER,
        result TEXT,
        error TEXT,
        FOREIGN KEY (skill_name) REFERENCES skills(name) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS habit_patterns (
        id TEXT PRIMARY KEY,
        pattern TEXT NOT NULL,
        trigger TEXT NOT NULL,
        action TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0.0,
        last_seen INTEGER NOT NULL,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kg_triples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,
        predicate TEXT NOT NULL,
        object TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 1.0,
        source TEXT NOT NULL DEFAULT 'user',
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS goals (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sensory_schemas (
        id TEXT PRIMARY KEY,
        modality TEXT NOT NULL,
        schema_json TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_vectors (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        embedding BLOB NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS compiled_knowledge (
        id TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        summary TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 1.0,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_log (
        id TEXT PRIMARY KEY,
        action TEXT NOT NULL,
        actor TEXT NOT NULL,
        result TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guardrail_events (
        id TEXT PRIMARY KEY,
        rule TEXT NOT NULL,
        severity TEXT NOT NULL,
        message TEXT NOT NULL,
        timestamp INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_audit (
        id TEXT PRIMARY KEY,
        tool_name TEXT NOT NULL,
        args_hash TEXT NOT NULL,
        result_hash TEXT,
        status TEXT NOT NULL,
        timestamp INTEGER NOT NULL
    )
    """,
]
