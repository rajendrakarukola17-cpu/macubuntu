#!/usr/bin/env python3
"""Celina Update 20 — Storage Migration Script (idempotent, backed-up, reversible)."""
from __future__ import annotations
import json, shutil, sqlite3, sys, time
from pathlib import Path

HOME = Path.home()
LEGACY_ROOTS = [
    HOME / ".local" / "share" / "celina",
    HOME / ".config" / "celina",
    HOME / ".celina",  # root-level scattered DBs (P0-04 fix)
]
NEW_SHARED_DIR = HOME / ".celina" / "storage" / "shared"
BACKUP_DIR = HOME / ".celina" / "storage" / "checkpoints" / f"pre_migration_{int(time.time())}"
MIGRATION_MAP = {
    "memory.db": "memory.db",
    "gear0_ledger.db": "gear0_ledger.db",
    "cache.db": "cache.db",
    "gear0_macros.json": "gear0_macros.json",
    "contacts.db": "contacts.db",
    "graph.db": "graph.db",
    "lists.db": "lists.db",
    "iot.db": "iot.db",
    "routines.db": "routines.db",
    "assistant.db": "assistant.db",
}
DRY_RUN = "--dry-run" in sys.argv
ROLLBACK = "--rollback" in sys.argv

def log(msg):
    prefix = "[dry-run] " if DRY_RUN else ""
    print(f"[migrate-storage] {prefix}{msg}")

def backup_existing():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    log(f"Backup directory: {BACKUP_DIR}")
    if not DRY_RUN and NEW_SHARED_DIR.exists():
        for item in NEW_SHARED_DIR.iterdir():
            dst = BACKUP_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(str(item), str(dst))
    return BACKUP_DIR

def find_legacy_file(name):
    for root in LEGACY_ROOTS:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None

def migrate_file(name, dest_name):
    src = find_legacy_file(name)
    if not src:
        log(f"  SKIP: {name} not found in any legacy root")
        return False
    dst = NEW_SHARED_DIR / dest_name
    if dst.exists():
        log(f"  SKIP: {dest_name} already exists in shared/ (won't overwrite)")
        return False
    log(f"  MIGRATE: {src} -> {dst}")
    if not DRY_RUN:
        NEW_SHARED_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
    return True

def migrate_databases_with_wal():
    for db_path in NEW_SHARED_DIR.glob("*.db"):
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.commit()
            conn.close()
            log(f"  WAL + busy_timeout applied: {db_path.name}")
        except Exception as e:
            log(f"  WARN: Could not apply WAL to {db_path.name}: {e}")

def do_migration():
    log("Starting storage migration")
    log(f"Legacy roots: {[str(r) for r in LEGACY_ROOTS]}")
    log(f"New shared dir: {NEW_SHARED_DIR}")
    backup_dir = backup_existing()
    migrated = 0
    for name, dest_name in MIGRATION_MAP.items():
        if migrate_file(name, dest_name):
            migrated += 1
    if not DRY_RUN:
        migrate_databases_with_wal()
    log(f"Migration complete: {migrated} file(s) migrated")
    log(f"Backup at: {backup_dir}")
    log(f"Run with --rollback to restore from backup")

def do_rollback():
    if not BACKUP_DIR.exists():
        log(f"ERROR: No backup found at {BACKUP_DIR}")
        sys.exit(1)
    log(f"Rolling back from backup: {BACKUP_DIR}")
    if DRY_RUN:
        log("[dry-run] Would restore files from backup to shared/")
        return
    for item in BACKUP_DIR.iterdir():
        dst = NEW_SHARED_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(str(item), str(dst))
    log("Rollback complete")

def main():
    NEW_SHARED_DIR.mkdir(parents=True, exist_ok=True)
    if ROLLBACK:
        do_rollback()
    else:
        do_migration()

if __name__ == "__main__":
    main()
