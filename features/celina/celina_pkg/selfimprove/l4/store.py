# Copyright 2026 Celina contributors.
"""Explicit private SQLite memory: sanitize first, then perform parameterized I/O."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from dataclasses import astuple, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from celina_pkg.selfimprove.contracts import AttemptRecord, AttemptStatus

from ._sqlite_guard import MemoryStoreError, check_journal
from .redactor import redact_with_report

if TYPE_CHECKING:
    from celina_pkg.selfimprove.flags import SelfImproveFlags

_FIELDS = ("task_id", "category", "verifier_name", "error_signature", "failure", "fix")
_SCHEMA = """CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, category TEXT NOT NULL,
    verifier_name TEXT NOT NULL, error_signature TEXT NOT NULL, failure TEXT NOT NULL,
    fix TEXT NOT NULL, created_at INTEGER NOT NULL, content_hash TEXT NOT NULL UNIQUE
)"""
_ATTEMPTS = """CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL,
    tokens_used INTEGER NOT NULL
)"""
_INSERT = """INSERT OR IGNORE INTO episodes
    (episode_id, task_id, category, verifier_name, error_signature, failure,
     fix, created_at, content_hash)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_SELECT = """SELECT episode_id, task_id, category, verifier_name, error_signature, failure,
    fix, created_at, content_hash FROM episodes
    WHERE (? IS NULL OR instr(lower(error_signature || ' ' || failure || ' ' || fix), lower(?)) > 0)
      AND (? IS NULL OR category = ?) AND (? IS NULL OR verifier_name = ?)
    ORDER BY created_at DESC, episode_id ASC LIMIT ?"""
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_MAX_FIELD = 8192
_MAX_QUERY = 1000
_PRIVATE_DIR = 0o700
_PRIVATE_FILE = 0o600


class _Bridge(Protocol):
    def record(self, event_type: str, payload: object) -> object: ...


@dataclass(frozen=True, slots=True)
class MemoryEpisode:
    """Immutable, bounded failure/fix candidate or sanitized stored record."""

    episode_id: str
    task_id: str
    category: str
    verifier_name: str
    error_signature: str
    failure: str
    fix: str
    created_at: int
    content_hash: str = ""

    def __post_init__(self) -> None:
        """Validate lengths and identities before any database access."""
        for name in ("episode_id", *_FIELDS):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > _MAX_FIELD:
                message = "required field is empty or exceeds length limit"
                raise ValueError(message)
        for value in (self.episode_id, self.task_id):
            _identifier(value)
        if type(self.created_at) is not int or self.created_at < 0:
            message = "created_at must be a nonnegative integer"
            raise ValueError(message)
        if self.content_hash and not re.fullmatch(r"[0-9a-f]{64}", self.content_hash):
            message = "invalid content hash"
            raise ValueError(message)


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        message = "invalid identifier"
        raise ValueError(message)
    if redact_with_report(value).count:
        message = "identifier contains sensitive data"
        raise ValueError(message)
    return value


def _sanitize(episode: MemoryEpisode) -> tuple[MemoryEpisode, int]:
    values: dict[str, str] = {}
    count = 0
    for name in _FIELDS:
        report = redact_with_report(getattr(episode, name))
        values[name] = report.text
        count += report.count
    digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, ensure_ascii=True).encode(),
    ).hexdigest()
    saved = replace(
        episode,
        task_id=values["task_id"],
        category=values["category"],
        verifier_name=values["verifier_name"],
        error_signature=values["error_signature"],
        failure=values["failure"],
        fix=values["fix"],
        content_hash=digest,
    )
    return saved, count


def _safe_path(path: Path) -> None:
    if not isinstance(path, Path) or ".." in path.parts:
        message = "database path must be an explicit safe Path"
        raise ValueError(message)
    if any(part.is_symlink() for part in (path, *path.parents)):
        message = "symlink in database path"
        raise MemoryStoreError(message)


def _private(path: Path, *, write: bool) -> bool:
    _safe_path(path)
    if write:
        path.parent.mkdir(mode=_PRIVATE_DIR, parents=True, exist_ok=True)
        _safe_path(path)
        if stat.S_IMODE(path.parent.stat().st_mode) != _PRIVATE_DIR:
            message = "insecure directory mode"
            raise MemoryStoreError(message)
    if not path.exists():
        return False
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    info = path.stat()
    if parent_mode != _PRIVATE_DIR or stat.S_IMODE(info.st_mode) != _PRIVATE_FILE:
        message = "insecure database or directory mode"
        raise MemoryStoreError(message)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        message = "database must be a private regular file"
        raise MemoryStoreError(message)
    return True


def _connect_read(path: Path) -> sqlite3.Connection | None:
    if not _private(path, write=False):
        return None
    check_journal(path)
    return sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)


def _connect_write(path: Path) -> sqlite3.Connection:
    if not _private(path, write=True):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _PRIVATE_FILE)
        except FileExistsError:
            pass
        else:
            try:
                os.fchmod(fd, _PRIVATE_FILE)
            finally:
                os.close(fd)
        if not _private(path, write=False):
            message = "could not create database"
            raise MemoryStoreError(message)
    check_journal(path, allow_empty=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(_SCHEMA)
        connection.execute(_ATTEMPTS)
        connection.commit()
    except Exception:
        connection.close()
        raise
    return connection


class FailureFixStore:
    """MemoryStoreProtocol adapter plus redacted failure/fix episode operations."""

    def __init__(
        self, db_path: Path, flags: SelfImproveFlags, *, bridge: _Bridge | None = None,
    ) -> None:
        """Bind explicit configuration without touching the filesystem."""
        self.db_path = db_path
        self.flags = flags
        self._bridge = bridge

    def _enabled(self) -> bool:
        return self.flags.enabled and self.flags.memory

    def record(self, episode: MemoryEpisode) -> MemoryEpisode:
        """Redact all stored fields before opening SQLite; deduplicate by content."""
        if not self._enabled():
            message = "memory disabled"
            raise MemoryStoreError(message)
        if not isinstance(episode, MemoryEpisode):
            message = "episode must be MemoryEpisode"
            raise TypeError(message)
        saved, count = _sanitize(episode)
        with closing(_connect_write(self.db_path)) as connection, connection:
            cursor = connection.execute(_INSERT, astuple(saved))
            row = connection.execute(
                "SELECT episode_id, task_id, category, verifier_name, error_signature, failure, "
                "fix, created_at, content_hash FROM episodes WHERE content_hash = ?",
                (saved.content_hash,),
            ).fetchone()
            if row is None:
                message = "episode identifier already exists with different content"
                raise MemoryStoreError(message)
            saved = MemoryEpisode(*row)
            inserted = cursor.rowcount == 1
        if inserted and self._bridge is not None:
            self._bridge.record(
                "selfimprove.memory.write",
                {
                    "episode_id": saved.episode_id,
                    "category": saved.category,
                    "content_hash": saved.content_hash,
                    "redaction_count": count,
                },
            )
        return saved

    def query(
        self,
        *,
        keyword: str | None = None,
        category: str | None = None,
        verifier_name: str | None = None,
        limit: int = 100,
    ) -> tuple[MemoryEpisode, ...]:
        """Read parameterized literal matches, newest first, without creating a DB."""
        if not self._enabled():
            return ()
        if type(limit) is not int or not 1 <= limit <= _MAX_QUERY:
            message = "invalid query limit"
            raise ValueError(message)
        connection = _connect_read(self.db_path)
        if connection is None:
            return ()
        with closing(connection):
            rows = connection.execute(
                _SELECT,
                (keyword, keyword, category, category, verifier_name, verifier_name, limit),
            ).fetchall()
        return tuple(MemoryEpisode(*row) for row in rows)

    def store(self, record: AttemptRecord) -> None:
        """Store a narrow L0 attempt, rejecting sensitive identifiers."""
        if not self._enabled():
            message = "memory disabled"
            raise MemoryStoreError(message)
        _identifier(record.attempt_id)
        _identifier(record.task_id)
        with closing(_connect_write(self.db_path)) as connection, connection:
            connection.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?)",
                (record.attempt_id, record.task_id, record.status, record.tokens_used),
            )

    def lookup(self, task_id: str) -> tuple[AttemptRecord, ...]:
        """Return L0 attempts for a literal, validated task ID without writes."""
        if not self._enabled():
            return ()
        _identifier(task_id)
        connection = _connect_read(self.db_path)
        if connection is None:
            return ()
        with closing(connection):
            rows = connection.execute(
                "SELECT attempt_id, task_id, status, tokens_used FROM attempts "
                "WHERE task_id = ? ORDER BY rowid DESC",
                (task_id,),
            ).fetchall()
        return tuple(
            AttemptRecord(row[0], row[1], cast("AttemptStatus", row[2]), row[3]) for row in rows
        )
