# Copyright 2026 Celina contributors.
"""Reject SQLite journaling modes and sidecars unsafe for read-only memory."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_HEADER_SIZE = 20


class MemoryStoreError(RuntimeError):
    """Disabled, invalid or unavailable memory store."""


def check_journal(path: Path, *, allow_empty: bool = False) -> None:
    """Reject sidecars and WAL/invalid headers before SQLite opens the file."""
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            message = "unexpected SQLite journal sidecar"
            raise MemoryStoreError(message)
    with path.open("rb") as stream:
        header = stream.read(_HEADER_SIZE)
    if not header and allow_empty:
        return
    if (
        len(header) < _HEADER_SIZE
        or header[:16] != b"SQLite format 3\x00"
        or header[18:20] != b"\x01\x01"
    ):
        message = "invalid or unsupported SQLite journal mode"
        raise MemoryStoreError(message)
