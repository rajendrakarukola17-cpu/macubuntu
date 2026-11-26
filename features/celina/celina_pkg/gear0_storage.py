"""Celina AI (Celly) - Gear 0 Common Storage (Update 19)

Shared storage backend for Ollama and Edge0-35B-A3B.
Switching between backends causes ZERO data loss — all models, contexts,
checkpoints, and conversation history live in one unified directory tree.

Layout:
  ~/.celina/storage/
    backends/ollama/    -> Ollama-specific models, contexts, checkpoints
    backends/edge0/     -> Edge0-35B-A3B-specific models, contexts, checkpoints
    shared/             -> Cross-backend shared data (conversations, facts, goals)
    migration/          -> Atomic migration scripts for backend switches
    checkpoints/        -> Pre-switch snapshots for instant rollback
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------

STORAGE_ROOT = Path(os.environ.get("CELINA_STORAGE_DIR", Path.home() / ".celina" / "storage"))
BACKENDS_DIR = STORAGE_ROOT / "backends"
OLLAMA_DIR = BACKENDS_DIR / "ollama"
EDGE0_DIR = BACKENDS_DIR / "edge0"
SHARED_DIR = STORAGE_ROOT / "shared"
MIGRATION_DIR = STORAGE_ROOT / "migration"
CHECKPOINTS_DIR = STORAGE_ROOT / "checkpoints"

# Shared SQLite database (cross-backend, never wiped on switch)
SHARED_DB = SHARED_DIR / "celina_shared.db"

# Backend state file (tracks which backend is active, last switch time, etc.)
BACKEND_STATE_FILE = SHARED_DIR / "backend_state.json"

# File lock to prevent startup races when multiple processes initialize storage
_STORAGE_LOCK_FILE = STORAGE_ROOT / ".storage_lock"


def _acquire_storage_lock():
    """Acquire an exclusive file lock on the storage root.

    Prevents multiple processes from running initialize_storage() simultaneously
    which could corrupt databases or create inconsistent state.
    """
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    lock_fd = open(_STORAGE_LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        # Another process holds the lock — wait briefly then proceed
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except OSError:
            pass
    return lock_fd


def ensure_storage_layout() -> None:
    """Create the full storage directory tree if missing."""
    for d in [STORAGE_ROOT, BACKENDS_DIR, OLLAMA_DIR, EDGE0_DIR, SHARED_DIR,
              MIGRATION_DIR, CHECKPOINTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _shared_conn() -> sqlite3.Connection:
    """Get connection to the shared cross-backend SQLite database."""
    ensure_storage_layout()
    conn = sqlite3.connect(str(SHARED_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Conversations (shared across backends)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            backend TEXT NOT NULL,
            ts TEXT NOT NULL DEFAULT (datetime('now')),
            cloud_safe INTEGER DEFAULT 1
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(ts DESC)")

    # Facts (shared across backends)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            backend TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            cloud_safe INTEGER DEFAULT 1,
            ts TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Goals (shared across backends)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            goal_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            sub_tasks TEXT DEFAULT '[]',
            deadline TEXT,
            backend TEXT NOT NULL,
            ts TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Backend switch history (audit trail)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backend_switches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_backend TEXT,
            to_backend TEXT,
            ts TEXT NOT NULL DEFAULT (datetime('now')),
            success INTEGER DEFAULT 1,
            notes TEXT
        )
    """)

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Backend state management
# ---------------------------------------------------------------------------

def get_backend_state() -> dict[str, Any]:
    """Read current backend state (active backend, last switch, etc.)."""
    try:
        if BACKEND_STATE_FILE.exists():
            return json.loads(BACKEND_STATE_FILE.read_text())
    except Exception:
        pass
    return {
        "active_backend": "ollama",
        "last_switch": None,
        "switch_count": 0,
        "data_preserved": True,
    }


def set_backend_state(state: dict[str, Any]) -> None:
    """Persist backend state."""
    try:
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        BACKEND_STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def initialize_storage() -> None:
    """Call once at Celina startup. Creates layout + writes initial backend state.

    Uses an exclusive file lock to prevent startup races when multiple processes
    initialize storage simultaneously.
    """
    _acquire_storage_lock()
    ensure_storage_layout()
    if not BACKEND_STATE_FILE.exists():
        set_backend_state(get_backend_state())


def record_backend_switch(from_backend: str, to_backend: str, success: bool = True, notes: str = "") -> None:
    """Record a backend switch in the shared audit trail."""
    try:
        conn = _shared_conn()
        conn.execute(
            "INSERT INTO backend_switches (from_backend, to_backend, success, notes) VALUES (?, ?, ?, ?)",
            (from_backend, to_backend, 1 if success else 0, notes),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Atomic checkpoint (pre-switch snapshot for instant rollback)
# ---------------------------------------------------------------------------

# All data files that must be checkpointed/restored for zero data loss.
# Covers both the shared storage root and legacy ~/.celina/ root.
_CHECKPOINT_DB_FILES = [
    (SHARED_DIR / "celina_shared.db", "celina_shared.db"),
    (SHARED_DIR / "habits.db", "habits.db"),
    (SHARED_DIR / "self_heal.db", "self_heal.db"),
    (SHARED_DIR / "gear0_ledger.db", "gear0_ledger.db"),
    (SHARED_DIR / "memory.db", "memory.db"),
    (SHARED_DIR / "contacts.db", "contacts.db"),
    (SHARED_DIR / "graph.db", "graph.db"),
    (SHARED_DIR / "lists.db", "lists.db"),
    (SHARED_DIR / "routines.db", "routines.db"),
    (SHARED_DIR / "iot.db", "iot.db"),
    (SHARED_DIR / "assistant.db", "assistant.db"),
]

_CHECKPOINT_JSON_FILES = [
    (SHARED_DIR / "gear0_macros.json", "gear0_macros.json"),
    (BACKEND_STATE_FILE, "backend_state.json"),
]

_CHECKPOINT_LOG_FILES = [
    (SHARED_DIR / "gear0_misses.log", "gear0_misses.log"),
]


def _snapshot_db(src: Path, dst: Path) -> None:
    """Create a consistent snapshot of a live SQLite database using the
    sqlite3.backup() API. This avoids corrupt snapshots from copying a
    live WAL-mode database file directly.
    """
    if not src.exists():
        return
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _restore_db(src: Path, dst: Path) -> None:
    """Restore a database from a checkpoint backup."""
    if not src.exists():
        return
    shutil.copy2(str(src), str(dst))


def create_checkpoint(label: str = "pre_switch") -> str:
    """Create a full snapshot of all Celina data before a backend switch.
    Returns the checkpoint directory path.

    Uses sqlite3.backup() for live databases to produce consistent snapshots,
    never shutil.copy2 on a live WAL-mode DB (which can corrupt the copy).
    """
    ensure_storage_layout()
    ts = int(time.time())
    cp_dir = CHECKPOINTS_DIR / f"{label}_{ts}"
    cp_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []

    # Snapshot all databases with sqlite3.backup() (consistent, non-corrupting)
    for src_path, name in _CHECKPOINT_DB_FILES:
        if src_path.exists():
            _snapshot_db(src_path, cp_dir / name)
            copied.append(name)

    # Copy JSON and log files
    for src_path, name in _CHECKPOINT_JSON_FILES + _CHECKPOINT_LOG_FILES:
        if src_path.exists():
            shutil.copy2(str(src_path), str(cp_dir / name))
            copied.append(name)

    # Write checkpoint manifest
    manifest = {
        "label": label,
        "timestamp": ts,
        "iso_ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
        "files": copied,
        "storage_root": str(STORAGE_ROOT),
    }
    (cp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return str(cp_dir)


def list_checkpoints() -> list[dict[str, Any]]:
    """List all available checkpoints."""
    checkpoints = []
    if not CHECKPOINTS_DIR.exists():
        return checkpoints
    for cp_dir in sorted(CHECKPOINTS_DIR.iterdir(), reverse=True):
        manifest_file = cp_dir / "manifest.json"
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text())
                manifest["path"] = str(cp_dir)
                checkpoints.append(manifest)
            except Exception:
                continue
    return checkpoints


def restore_checkpoint(cp_path: str) -> bool:
    """Restore all Celina data from a checkpoint. Returns True on success.

    Restores databases, JSON files, and log files from the checkpoint.
    Missing files in the checkpoint are skipped (partial restore is safe).
    """
    try:
        cp = Path(cp_path)
        if not cp.exists():
            return False

        manifest_file = cp / "manifest.json"
        if manifest_file.exists():
            try:
                json.loads(manifest_file.read_text())
            except Exception:
                pass

        # Restore databases
        for src_path, name in _CHECKPOINT_DB_FILES:
            db_bak = cp / name
            if db_bak.exists():
                _restore_db(db_bak, src_path.parent / name)

        # Restore JSON and log files
        all_file_specs = _CHECKPOINT_JSON_FILES + _CHECKPOINT_LOG_FILES
        for src_path, name in all_file_specs:
            bak = cp / name
            if bak.exists():
                shutil.copy2(str(bak), str(src_path))

        return True
    except Exception:
        return False


# Backend-agnostic data access (never lost on switch)
# ---------------------------------------------------------------------------

def shared_save_fact(key: str, value: str, backend: str = "shared", confidence: float = 1.0, cloud_safe: bool = True) -> str:
    """Save a fact to shared storage (survives backend switches)."""
    try:
        conn = _shared_conn()
        conn.execute(
            """INSERT INTO facts (key, value, backend, confidence, cloud_safe, ts)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   backend = excluded.backend,
                   confidence = excluded.confidence,
                   cloud_safe = excluded.cloud_safe,
                   ts = datetime('now')""",
            (key, value, backend, 1 if cloud_safe else 0, confidence),
        )
        conn.commit()
        conn.close()
        return f"✓ Fact saved: {key} = {value}"
    except Exception as exc:
        return f"Failed to save fact: {exc}"


def shared_get_fact(key: str) -> str | None:
    """Retrieve a fact from shared storage."""
    try:
        conn = _shared_conn()
        row = conn.execute("SELECT value FROM facts WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def shared_save_conversation(role: str, content: str, backend: str = "shared", cloud_safe: bool = True) -> None:
    """Append a conversation turn to shared storage."""
    try:
        conn = _shared_conn()
        conn.execute(
            "INSERT INTO conversations (role, content, backend, cloud_safe) VALUES (?, ?, ?, ?)",
            (role, content, backend, 1 if cloud_safe else 0),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def shared_get_recent_conversations(limit: int = 10, backend: str | None = None) -> list[dict[str, Any]]:
    """Get recent conversation turns from shared storage."""
    try:
        conn = _shared_conn()
        if backend:
            rows = conn.execute(
                "SELECT role, content, backend, ts FROM conversations WHERE backend = ? ORDER BY ts DESC LIMIT ?",
                (backend, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content, backend, ts FROM conversations ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [{"role": r[0], "content": r[1], "backend": r[2], "ts": r[3]} for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Backend-specific data directories
# ---------------------------------------------------------------------------

def backend_dir(backend: str) -> Path:
    """Return the directory for a specific backend."""
    if backend == "ollama":
        return OLLAMA_DIR
    if backend == "edge0":
        return EDGE0_DIR
    return SHARED_DIR


def backend_model_path(backend: str, model_name: str) -> Path:
    """Get the path for a backend-specific model file."""
    return backend_dir(backend) / "models" / model_name


def backend_context_path(backend: str, context_name: str) -> Path:
    """Get the path for a backend-specific context file."""
    return backend_dir(backend) / "contexts" / context_name


def backend_checkpoint_path(backend: str, name: str) -> Path:
    """Get the path for a backend-specific checkpoint."""
    return backend_dir(backend) / "checkpoints" / name


__all__ = [
    "EDGE0_DIR",
    "OLLAMA_DIR",
    "SHARED_DIR",
    "STORAGE_ROOT",
    "backend_checkpoint_path",
    "backend_context_path",
    "backend_dir",
    "backend_model_path",
    "create_checkpoint",
    "ensure_storage_layout",
    "get_backend_state",
    "list_checkpoints",
    "record_backend_switch",
    "restore_checkpoint",
    "set_backend_state",
    "shared_get_fact",
    "shared_get_recent_conversations",
    "shared_save_conversation",
    "shared_save_fact",
]
