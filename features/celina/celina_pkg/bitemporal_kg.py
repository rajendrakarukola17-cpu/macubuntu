"""macubuntu celina_pkg.bitemporal_kg - Bi-temporal Knowledge Graph.

Tracks facts across TWO time dimensions:
  - Valid time (valid_from / valid_until): when the fact was true in the real world.
  - Transaction time (ingested_at / superseded_by): when it was recorded/retired.

Tables: kg_facts, stakeholder_profiles, open_commitments.
All writes are append-only inserts with time-window versioning.
stdlib-only: sqlite3, datetime, re, json. Fail-closed defaults.
"""

from __future__ import annotations

import datetime as _dt
import re
import sqlite3
from pathlib import Path

DEFAULT_SOURCE_TRUST = "untrusted_web"
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _now_iso() -> str:
    return _dt.datetime.utcnow().isoformat() + "Z"


def _validate_iso(value: str) -> str:
    if value and _ISO_RE.match(value):
        return value
    return _now_iso()


class BiTemporalKG:
    """Bi-temporal knowledge graph with stakeholder profiles and open commitments."""

    def __init__(self, *, db_path: str | None = None, source_trust: str = DEFAULT_SOURCE_TRUST) -> None:
        self.db_path = Path(db_path) if db_path else (Path.home() / ".local" / "share" / "celina" / "celina.db")
        self.source_trust = source_trust if source_trust in {"trusted_user", "trusted_local", "untrusted_web"} else DEFAULT_SOURCE_TRUST
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kg_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT NOT NULL DEFAULT '9999-12-31T23:59:59Z',
                    ingested_at TEXT NOT NULL,
                    superseded_by INTEGER,
                    source_trust TEXT NOT NULL DEFAULT 'untrusted_web'
                );
                CREATE TABLE IF NOT EXISTS stakeholder_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stakeholder_id TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT '',
                    trust_level TEXT NOT NULL DEFAULT 'untrusted_web',
                    valid_from TEXT NOT NULL,
                    valid_until TEXT NOT NULL DEFAULT '9999-12-31T23:59:59Z',
                    ingested_at TEXT NOT NULL,
                    superseded_by INTEGER
                );
                CREATE TABLE IF NOT EXISTS open_commitments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    commitment_id TEXT NOT NULL UNIQUE,
                    stakeholder_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    due_date TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    valid_from TEXT NOT NULL,
                    valid_until TEXT NOT NULL DEFAULT '9999-12-31T23:59:59Z',
                    ingested_at TEXT NOT NULL,
                    superseded_by INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_kg_facts_subj_pred ON kg_facts(subject, predicate);
                CREATE INDEX IF NOT EXISTS idx_kg_facts_valid ON kg_facts(valid_from, valid_until);
                CREATE INDEX IF NOT EXISTS idx_stakeholder_id ON stakeholder_profiles(stakeholder_id);
                CREATE INDEX IF NOT EXISTS idx_commitments_stakeholder ON open_commitments(stakeholder_id);
            """)
            self._migrate_column(conn, "kg_facts", "source_trust", "TEXT NOT NULL DEFAULT 'untrusted_web'")
            self._migrate_column(conn, "kg_facts", "superseded_by", "INTEGER")
            self._migrate_column(conn, "stakeholder_profiles", "trust_level", "TEXT NOT NULL DEFAULT 'untrusted_web'")
            self._migrate_column(conn, "open_commitments", "status", "TEXT NOT NULL DEFAULT 'open'")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _migrate_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def add_fact(self, *, subject: str, predicate: str, object_: str,
                 valid_from: str | None = None, valid_until: str | None = None,
                 ingested_at: str | None = None, source_trust: str | None = None) -> int:
        now = _now_iso()
        vf = _validate_iso(valid_from or now)
        vu = _validate_iso(valid_until or "9999-12-31T23:59:59Z")
        ia = _validate_iso(ingested_at or now)
        st = (source_trust or self.source_trust)
        if st not in {"trusted_user", "trusted_local", "untrusted_web"}:
            st = DEFAULT_SOURCE_TRUST
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO kg_facts (subject, predicate, object, valid_from, valid_until, ingested_at, superseded_by, source_trust) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (subject, predicate, object_, vf, vu, ia, st),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def close_fact(self, *, subject: str, predicate: str, object_: str,
                   valid_until: str | None = None, superseded_by: int | None = None) -> int:
        vu = _validate_iso(valid_until or _now_iso())
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE kg_facts SET valid_until = ?, superseded_by = COALESCE(?, superseded_by) "
                "WHERE subject = ? AND predicate = ? AND object = ? AND valid_until = '9999-12-31T23:59:59Z'",
                (vu, superseded_by, subject, predicate, object_),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def recall(self, *, subject: str | None = None, predicate: str | None = None,
               object_: str | None = None, as_of: str | None = None,
               source_trust_filter: str | None = None, limit: int = 100) -> list:
        now = _now_iso()
        at = _validate_iso(as_of or now)
        conditions: list = []
        params: list = []
        if subject is not None:
            conditions.append("subject = ?"); params.append(subject)
        if predicate is not None:
            conditions.append("predicate = ?"); params.append(predicate)
        if object_ is not None:
            conditions.append("object = ?"); params.append(object_)
        conditions.append("valid_from <= ? AND valid_until > ?"); params.extend([at, at])
        if source_trust_filter is not None:
            conditions.append("source_trust = ?"); params.append(source_trust_filter)
        sql = "SELECT subject, predicate, object, valid_from, valid_until, ingested_at, source_trust FROM kg_facts"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY ingested_at DESC LIMIT ?"
        params.append(limit)
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [{"subject": r[0], "predicate": r[1], "object": r[2], "valid_from": r[3],
                 "valid_until": r[4], "ingested_at": r[5], "source_trust": r[6]} for r in rows]

    def upsert_stakeholder(self, *, stakeholder_id: str, role: str = "",
                           trust_level: str = DEFAULT_SOURCE_TRUST,
                           valid_from: str | None = None, ingested_at: str | None = None) -> int:
        now = _now_iso()
        vf = _validate_iso(valid_from or now)
        ia = _validate_iso(ingested_at or now)
        tl = trust_level if trust_level in {"trusted_user", "trusted_local", "untrusted_web"} else DEFAULT_SOURCE_TRUST
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE stakeholder_profiles SET valid_until = ? WHERE stakeholder_id = ? AND valid_until = '9999-12-31T23:59:59Z'",
                (now, stakeholder_id),
            )
            cur = conn.execute(
                "INSERT INTO stakeholder_profiles (stakeholder_id, role, trust_level, valid_from, valid_until, ingested_at, superseded_by) "
                "VALUES (?, ?, ?, ?, '9999-12-31T23:59:59Z', ?, NULL)",
                (stakeholder_id, role, tl, vf, ia),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def get_stakeholder(self, *, stakeholder_id: str, as_of: str | None = None) -> dict | None:
        now = _now_iso()
        at = _validate_iso(as_of or now)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT role, trust_level, valid_from, valid_until FROM stakeholder_profiles "
                "WHERE stakeholder_id = ? AND valid_from <= ? AND valid_until > ? "
                "ORDER BY ingested_at DESC LIMIT 1",
                (stakeholder_id, at, at),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {"stakeholder_id": stakeholder_id, "role": row[0], "trust_level": row[1],
                "valid_from": row[2], "valid_until": row[3]}

    def add_commitment(self, *, commitment_id: str, stakeholder_id: str, description: str,
                       due_date: str | None = None, status: str = "open",
                       valid_from: str | None = None, ingested_at: str | None = None) -> int:
        now = _now_iso()
        vf = _validate_iso(valid_from or now)
        ia = _validate_iso(ingested_at or now)
        st = status if status in {"open", "done", "superseded", "cancelled"} else "open"
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE open_commitments SET valid_until = ? WHERE commitment_id = ? AND valid_until = '9999-12-31T23:59:59Z'",
                (now, commitment_id),
            )
            cur = conn.execute(
                "INSERT INTO open_commitments (commitment_id, stakeholder_id, description, due_date, status, "
                "valid_from, valid_until, ingested_at, superseded_by) "
                "VALUES (?, ?, ?, ?, ?, ?, '9999-12-31T23:59:59Z', ?, NULL)",
                (commitment_id, stakeholder_id, description, due_date, st, vf, ia),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def list_commitments(self, *, stakeholder_id: str | None = None,
                         status: str | None = None, as_of: str | None = None,
                         limit: int = 100) -> list:
        now = _now_iso()
        at = _validate_iso(as_of or now)
        conditions = ["valid_from <= ? AND valid_until > ?"]
        params = [at, at]
        if stakeholder_id is not None:
            conditions.append("stakeholder_id = ?"); params.append(stakeholder_id)
        if status is not None:
            conditions.append("status = ?"); params.append(status)
        sql = ("SELECT commitment_id, stakeholder_id, description, due_date, status, "  # nosec B608 — all values use parameterized ? placeholders; conditions use hardcoded column names
               "valid_from, valid_until FROM open_commitments WHERE " + " AND ".join(conditions)
               + " ORDER BY ingested_at DESC LIMIT ?")
        params.append(limit)
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [{"commitment_id": r[0], "stakeholder_id": r[1], "description": r[2],
                 "due_date": r[3], "status": r[4], "valid_from": r[5], "valid_until": r[6]} for r in rows]

    def close_commitment(self, *, commitment_id: str, status: str = "done") -> int:
        st = status if status in {"done", "cancelled"} else "done"
        now = _now_iso()
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE open_commitments SET valid_until = ?, status = ? "
                "WHERE commitment_id = ? AND valid_until = '9999-12-31T23:59:59Z'",
                (now, st, commitment_id),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
