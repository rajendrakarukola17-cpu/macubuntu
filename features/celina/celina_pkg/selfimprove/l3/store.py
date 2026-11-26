# Copyright 2026 Celina contributors.
"""Private append-only JSONL chain with a separately checkpointed head.

The checkpoint detects ordinary tail deletion but is not an independent trust
anchor: coordinated rollback of both files, or deletion of the entire directory,
cannot be detected. A crash between log and checkpoint writes fails closed.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from celina_pkg.selfimprove.contracts import EvalReport

from .stats import EvalRun, TaskOutcome

if TYPE_CHECKING:
    from collections.abc import Iterator

_GENESIS = "0" * 64
_PRIVATE_MODE = 0o600


class EvalStoreIntegrityError(RuntimeError):
    """Store content or its head checkpoint failed integrity checks."""


class _Bridge(Protocol):
    def record(self, event_type: str, payload: object) -> object: ...


@dataclass(frozen=True, slots=True)
class StoredEvalEntry:
    """Immutable chain entry containing one immutable evaluation run."""

    run: EvalRun
    prev_hash: str
    entry_hash: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _entry(run: EvalRun, previous: str) -> StoredEvalEntry:
    digest = hashlib.sha256(_canonical({"prev_hash": previous, "run": asdict(run)})).hexdigest()
    return StoredEvalEntry(run, previous, digest)


def _run_data(data: object) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {"run", "prev_hash", "entry_hash"}:
        msg = "invalid store entry"
        raise EvalStoreIntegrityError(msg)
    raw = data["run"]
    fields = {"run_id", "outcomes", "complete", "blocked_reason"}
    if not isinstance(raw, dict) or set(raw) != fields:
        msg = "invalid run schema"
        raise EvalStoreIntegrityError(msg)
    return raw


def _decode(data: object, previous: str) -> StoredEvalEntry:
    raw = _run_data(data)
    try:
        outcomes = tuple(
            TaskOutcome(EvalReport(**item["report"]), item["security_critical"])
            for item in raw["outcomes"]
        )
        run = EvalRun(raw["run_id"], outcomes, raw["complete"], raw["blocked_reason"])
        entry = _entry(run, previous)
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        msg = "invalid store entry"
        raise EvalStoreIntegrityError(msg) from exc
    record = cast("dict[str, Any]", data)
    if record["prev_hash"] != previous or record["entry_hash"] != entry.entry_hash:
        msg = "hash mismatch"
        raise EvalStoreIntegrityError(msg)
    return entry


class EvalStore:
    """Store records at a caller-specified private directory; no HOME defaults."""

    def __init__(self, directory: Path, *, bridge: _Bridge | None = None) -> None:
        """Create a new store only in an absent or empty explicit directory."""
        if directory.is_symlink():
            msg = "store directory is a symlink"
            raise EvalStoreIntegrityError(msg)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.directory = directory
        self.path = directory / "evaluations.jsonl"
        self.anchor_path = directory / "head.sha256"
        self._lock_path = directory / ".lock"
        self._bridge = bridge
        with self._locked():
            if self.path.is_symlink() or self.anchor_path.is_symlink():
                msg = "store paths must not be symlinks"
                raise EvalStoreIntegrityError(msg)
            if not self.path.exists() and not self.anchor_path.exists():
                if any(item.name != ".lock" for item in directory.iterdir()):
                    msg = "incomplete store"
                    raise EvalStoreIntegrityError(msg)
                self._create()
            self._verify()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if self._lock_path.is_symlink():
            msg = "lock is a symlink"
            raise EvalStoreIntegrityError(msg)
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _create(self) -> None:
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self._checkpoint(_GENESIS)

    def _checkpoint(self, digest: str) -> None:
        fd, name = tempfile.mkstemp(prefix=".head-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(digest + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            Path(name).replace(self.anchor_path)
        finally:
            if Path(name).exists():
                Path(name).unlink()

    def _verify(self) -> tuple[StoredEvalEntry, ...]:
        try:
            if self.path.is_symlink() or self.anchor_path.is_symlink():
                msg = "store paths must not be symlinks"
                raise EvalStoreIntegrityError(msg)
            for path in (self.path, self.anchor_path):
                if stat.S_IMODE(path.stat().st_mode) != _PRIVATE_MODE:
                    msg = "insecure store file mode"
                    raise EvalStoreIntegrityError(msg)
            content = self.path.read_bytes()
            head = self.anchor_path.read_text(encoding="ascii").strip()
            if content and not content.endswith(b"\n"):
                msg = "truncated store"
                raise EvalStoreIntegrityError(msg)
            entries: list[StoredEvalEntry] = []
            previous = _GENESIS
            for line in content.splitlines():
                entry = _decode(json.loads(line), previous)
                if line != _canonical(asdict(entry)):
                    msg = "non-canonical or modified store entry"
                    raise EvalStoreIntegrityError(msg)
                entries.append(entry)
                previous = entry.entry_hash
            if head != previous:
                msg = "head checkpoint mismatch"
                raise EvalStoreIntegrityError(msg)
            return tuple(entries)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            msg = "cannot read store or checkpoint"
            raise EvalStoreIntegrityError(msg) from exc

    def verify_chain(self) -> tuple[StoredEvalEntry, ...]:
        """Read and verify every entry and the checkpoint under an advisory lock."""
        with self._locked():
            return self._verify()

    def append(self, run: EvalRun) -> StoredEvalEntry:
        """Verify, append and checkpoint a run, then optionally audit its summary."""
        if not isinstance(run, EvalRun):
            msg = "run must be an EvalRun"
            raise TypeError(msg)
        with self._locked():
            entries = self._verify()
            previous = entries[-1].entry_hash if entries else _GENESIS
            entry = _entry(run, previous)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            with os.fdopen(fd, "ab") as stream:
                stream.write(_canonical(asdict(entry)) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._checkpoint(entry.entry_hash)
        if self._bridge is not None:
            self._bridge.record(
                "selfimprove.eval.run",
                {
                    "run_id": run.run_id,
                    "pass_rate": run.pass_rate,
                    "security_pass_rate": run.security_pass_rate,
                    "entry_hash": entry.entry_hash,
                },
            )
        return entry
