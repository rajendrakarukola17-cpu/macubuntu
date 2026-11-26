# Copyright 2026 Celina contributors.
"""Persistent replay markers for trusted, human-issued Celina token objects."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path

from celina_pkg.approval import ApprovalToken, RiskLevel, verify_and_consume

_PRIVATE_DIR = 0o700
_PRIVATE_FILE = 0o600


def _fingerprint(token: ApprovalToken) -> str:
    # Celina verifies only tool and arguments_hash. Other fields are mutable and
    # must never distinguish a fresh authorization from a replay.
    values = [token.tool, token.arguments_hash]
    raw = json.dumps(values, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _db_file(directory: Path) -> Path:
    if (
        not isinstance(directory, Path)
        or not directory.is_absolute()
        or ".." in directory.parts
        or any(part.is_symlink() for part in (directory, *directory.parents))
    ):
        message = "unsafe approval state directory"
        raise ValueError(message)
    directory.mkdir(mode=_PRIVATE_DIR, exist_ok=True)
    info = directory.stat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != _PRIVATE_DIR:
        message = "approval state directory must be private"
        raise ValueError(message)
    path = directory / "approval-spends.sqlite3"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, _PRIVATE_FILE)
    except FileExistsError:
        pass
    else:
        os.fchmod(fd, _PRIVATE_FILE)
        os.close(fd)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != _PRIVATE_FILE
    ):
        message = "unsafe approval state file"
        raise ValueError(message)
    return path


class TokenSpendStore:
    """Use a private persistent SQLite uniqueness constraint for atomic spending.

    The directory and issued token are host-controlled. A Celina token is a mutable
    in-process object, not a cryptographically signed proof of human approval.
    """

    def __init__(self, state_dir: Path) -> None:
        """Bind an explicit directory without creating or reading it."""
        self.state_dir = state_dir

    def consume(self, token: ApprovalToken | None, tool: str, arguments: dict[str, object]) -> bool:
        """Check a genuine single-use token and persist its one-time consumption."""
        if type(token) is not ApprovalToken or token.single_use is not True:
            return False
        try:
            if token.risk is not RiskLevel.SECURITY_SENSITIVE or time.time() > token.expires_at:
                return False
            if not token.verify_for(tool, arguments)[0]:
                return False
            return self._commit(token, tool, arguments)
        except Exception:
            return False

    def _commit(self, token: ApprovalToken, tool: str, arguments: dict[str, object]) -> bool:
        path = _db_file(self.state_dir)
        fingerprint = _fingerprint(token)
        with closing(sqlite3.connect(path, timeout=10.0)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS spends (fingerprint TEXT PRIMARY KEY)")
            db.execute("INSERT INTO spends (fingerprint) VALUES (?)", (fingerprint,))
            if not verify_and_consume(token, tool, arguments)[0]:
                db.rollback()
                return False
            db.commit()
            return True
