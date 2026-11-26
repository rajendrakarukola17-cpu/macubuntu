# Copyright 2026 Celina contributors.
"""Private registry filesystem primitives; host controls the parent directory."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


_PRIVATE_DIR = 0o700
_PRIVATE_FILE = 0o600


class ArtifactRegistryError(RuntimeError):
    """Base class for registry failures."""


class ArtifactIntegrityError(ArtifactRegistryError):
    """Stored data or filesystem metadata did not pass integrity checks."""


class RegistryStaleError(ArtifactRegistryError):
    """Active baseline no longer matches an approved proposal."""


class RegistryBlockedError(ArtifactRegistryError):
    """The optimizer flags do not authorize storage changes."""


def check_root(path: Path, *, create: bool = False) -> bool:
    """Reject relative/traversal/symlinked paths and insecure existing roots."""
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        message = "registry path must be explicit, absolute and safe"
        raise ArtifactIntegrityError(message)
    if any(part.is_symlink() for part in (path, *path.parents)):
        message = "symlink in registry path"
        raise ArtifactIntegrityError(message)
    if not path.exists() and create:
        path.mkdir(mode=0o700)
    if not path.exists():
        return False
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != _PRIVATE_DIR:
        message = "registry directory must be private"
        raise ArtifactIntegrityError(message)
    return True


def _verify_file(path: Path) -> bool:
    if path.is_symlink():
        message = "symlinked registry file"
        raise ArtifactIntegrityError(message)
    if not path.exists():
        return False
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != _PRIVATE_FILE
        or info.st_nlink != 1
    ):
        message = "registry file must be private and unlinked"
        raise ArtifactIntegrityError(message)
    return True


def read_json(path: Path) -> dict[str, object] | None:
    """Read a bounded private JSON object without following a final symlink."""
    if not _verify_file(path):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as stream:
            text = stream.read(1_000_000)
        data = json.loads(text)
    except (OSError, UnicodeError, ValueError) as exc:
        message = "invalid registry JSON"
        raise ArtifactIntegrityError(message) from exc
    if not isinstance(data, dict):
        message = "JSON object required"
        raise ArtifactIntegrityError(message)
    return data


def write_json(path: Path, data: dict[str, object], *, replace: bool) -> None:
    """Fsync a private temporary file, atomically publish, and never overwrite versions."""
    if replace:
        _verify_file(path)
    temp = path.parent / (".tmp-" + secrets.token_hex(16))
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            temp.replace(path)
        else:
            os.link(temp, path, follow_symlinks=False)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def locked(root: Path) -> Iterator[None]:
    """Serialize registry writers using an explicitly private lock file."""
    check_root(root, create=True)
    path = root / ".registry.lock"
    _verify_file(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
