# Copyright 2026 Celina contributors.
"""Test private, redaction-first and flag-gated failure/fix storage."""

import sqlite3
import stat
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from celina_pkg.selfimprove.contracts import AttemptRecord, MemoryStoreProtocol
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l4 import store as store_module
from celina_pkg.selfimprove.l4.store import FailureFixStore, MemoryEpisode, MemoryStoreError

ACTIVE = SelfImproveFlags(enabled=True, memory=True)
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
DIGEST_SIZE = 64
PAIR_COUNT = 2
PUBLIC_DIR = 0o755


def episode(**changes: object) -> MemoryEpisode:
    """Construct a deterministic candidate failure/fix episode."""
    values = {
        "episode_id": "episode-1",
        "task_id": "task-1",
        "category": "repair",
        "verifier_name": "pytest",
        "error_signature": "test failure",
        "failure": "failed with password=yellow-bird and user@example.com",
        "fix": "edited /home/alice/project/code.py",
        "created_at": 4,
    }
    values.update(changes)
    return MemoryEpisode(**values)


@pytest.mark.parametrize("flags", [SelfImproveFlags(), SelfImproveFlags(enabled=True)])
def test_disabled_does_not_touch_files(tmp_path: Path, flags: SelfImproveFlags) -> None:
    """Disabled writes fail and reads return empty without creating paths."""
    path = tmp_path / "missing" / "memory.sqlite3"
    store = FailureFixStore(path, flags)
    with pytest.raises(MemoryStoreError, match="disabled"):
        store.record(episode())
    with pytest.raises(MemoryStoreError, match="disabled"):
        store.store(AttemptRecord("a", "t", "failed", 0))
    assert store.query() == ()
    assert store.lookup("t") == ()
    assert not path.parent.exists()


def test_private_redacted_sqlite_and_compact_audit(tmp_path: Path) -> None:
    """Secrets never reach rows, raw database bytes, or audit payload."""

    class Bridge:
        def __init__(self) -> None:
            self.events: list[tuple[str, object]] = []

        def record(self, name: str, payload: object) -> None:
            self.events.append((name, payload))

    bridge = Bridge()
    path = tmp_path / "private" / "memory.sqlite3"
    store = FailureFixStore(path, ACTIVE, bridge=bridge)
    saved = store.record(episode())
    assert stat.S_IMODE(path.parent.stat().st_mode) == PRIVATE_DIR
    assert stat.S_IMODE(path.stat().st_mode) == PRIVATE_FILE
    assert len(saved.content_hash) == DIGEST_SIZE
    assert "yellow-bird" not in saved.failure
    assert "user@example.com" not in saved.failure
    assert "/home/alice" not in saved.fix
    assert store.query() == (saved,)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        row = connection.execute("SELECT failure, fix FROM episodes").fetchone()
    for secret in (b"yellow-bird", b"user@example.com", b"/home/alice"):
        assert secret not in path.read_bytes()
        assert secret.decode() not in repr(row)
        assert secret.decode() not in repr(bridge.events)
    assert bridge.events[0][0] == "selfimprove.memory.write"
    assert set(bridge.events[0][1]) == {"episode_id", "category", "content_hash", "redaction_count"}
    with pytest.raises(FrozenInstanceError):
        saved.fix = "modified"
    assert not list(path.parent.glob("*-wal"))
    assert not list(path.parent.glob("*-shm"))


def test_dedup_sort_and_literal_query(tmp_path: Path) -> None:
    """Repeated content deduplicates; query values cannot become SQL."""
    store = FailureFixStore(tmp_path / "private" / "memory.db", ACTIVE)
    first = store.record(episode())
    duplicate = store.record(episode(episode_id="other"))
    assert duplicate == first
    later = store.record(episode(episode_id="next", failure="a different failure", created_at=8))
    assert store.query() == (later, first)
    assert store.query(keyword="' OR 1=1 --") == ()
    assert store.query(category="' OR 1=1 --") == ()
    assert len(store.query()) == PAIR_COUNT


def test_protocol_round_trip_and_limits(tmp_path: Path) -> None:
    """The L0 attempt-record protocol is implemented without widening signatures."""
    store: MemoryStoreProtocol = FailureFixStore(tmp_path / "private" / "memory.db", ACTIVE)
    attempt = AttemptRecord("attempt-1", "task-1", "failed", 7)
    store.store(attempt)
    assert store.lookup("task-1") == (attempt,)
    assert store.lookup("unknown") == ()
    with pytest.raises(ValueError, match="identifier"):
        store.store(AttemptRecord("sk-abcdefghijklmnopqrstuv", "task-1", "failed", 1))
    with pytest.raises(ValueError, match=r"required|length"):
        episode(failure="")
    with pytest.raises(ValueError, match=r"required|length"):
        episode(fix="x" * 20000)


def test_symlinks_and_insecure_existing_permissions_fail_closed(tmp_path: Path) -> None:
    """DB and parent symlinks are refused, including dangling links."""
    parent = tmp_path / "dir"
    parent.mkdir(mode=PRIVATE_DIR)
    (parent / "db").symlink_to(tmp_path / "missing")
    with pytest.raises((ValueError, MemoryStoreError), match="symlink"):
        FailureFixStore(parent / "db", ACTIVE).record(episode())
    link = tmp_path / "link"
    link.symlink_to(parent, target_is_directory=True)
    with pytest.raises((ValueError, MemoryStoreError), match="symlink"):
        FailureFixStore(link / "db", ACTIVE).record(episode())
    db = tmp_path / "safe" / "memory.db"
    store = FailureFixStore(db, ACTIVE)
    store.record(episode())
    db.chmod(0o644)
    with pytest.raises((PermissionError, MemoryStoreError), match="mode"):
        store.query()


def test_insecure_parent_is_rejected_before_database_creation(tmp_path: Path) -> None:
    """Never create even an empty DB in a publicly accessible existing directory."""
    parent = tmp_path / "public"
    parent.mkdir()
    parent.chmod(0o755)
    db = parent / "memory.db"
    with pytest.raises(MemoryStoreError, match="mode"):
        FailureFixStore(db, ACTIVE).record(episode())
    assert not db.exists()
    assert stat.S_IMODE(parent.stat().st_mode) == PUBLIC_DIR


def test_invalid_fields_and_query_bounds(tmp_path: Path) -> None:
    """Bad IDs, timestamps, hashes, paths and limits fail before writing."""
    for changes in ({"episode_id": "has spaces"}, {"created_at": -1}, {"content_hash": "bad"}):
        with pytest.raises(ValueError, match=r"identifier|created_at|hash"):
            episode(**changes)
    store = FailureFixStore(tmp_path / "missing" / "db", ACTIVE)
    with pytest.raises(ValueError, match="limit"):
        store.query(limit=0)
    assert store.lookup("absent") == ()
    with pytest.raises(TypeError, match="episode"):
        store.record("bad")
    with pytest.raises(ValueError, match="safe Path"):
        FailureFixStore(tmp_path / ".." / "invalid", ACTIVE).record(episode())
    assert not (tmp_path / "missing").exists()


def test_duplicate_identity_is_not_overwritten_and_hardlinks_are_rejected(tmp_path: Path) -> None:
    """Conflicting identity and hard-linked DB files cannot silently pass."""
    path = tmp_path / "safe" / "db"
    store = FailureFixStore(path, ACTIVE)
    first = store.record(episode())
    with pytest.raises(MemoryStoreError, match="identifier"):
        store.record(episode(failure="different content"))
    assert store.query() == (first,)
    (tmp_path / "hardlink").hardlink_to(path)
    with pytest.raises(MemoryStoreError, match="regular file"):
        store.query()


def test_database_creation_race_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vanished collision cannot be mistaken for a created database."""
    def pretend_collision(*_args: object, **_kwargs: object) -> int:
        raise FileExistsError

    monkeypatch.setattr(store_module.os, "open", pretend_collision)
    with pytest.raises(MemoryStoreError, match="create"):
        FailureFixStore(tmp_path / "safe" / "db", ACTIVE).record(episode())


def test_sqlite_setup_failure_closes_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A setup error closes the failed connection without recording text."""
    class BadConnection:
        closed = False

        def execute(self, _sql: str) -> None:
            message = "synthetic failure"
            raise sqlite3.OperationalError(message)

        def close(self) -> None:
            self.closed = True

    bad = BadConnection()
    monkeypatch.setattr(store_module.sqlite3, "connect", lambda *_args, **_kwargs: bad)
    with pytest.raises(sqlite3.OperationalError, match="synthetic"):
        FailureFixStore(tmp_path / "safe" / "db", ACTIVE).record(episode())
    assert bad.closed


def test_sqlite_statements_never_interpolate_python_values() -> None:
    """All execute calls use literal SQL or fixed constants plus bound parameters."""
    import ast  # noqa: PLC0415  # Static guard loads only stdlib parsing for this test.

    source = Path(store_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert calls
    for call in calls:
        statement = call.args[0]
        assert isinstance(statement, (ast.Constant, ast.Name))


def test_sensitive_fields_are_removed_before_sql_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite's trace sees sanitized values, never original secrets."""
    source = store_module.sqlite3.connect
    trace: list[str] = []

    def traced(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = source(*args, **kwargs)
        conn.set_trace_callback(trace.append)
        return conn

    monkeypatch.setattr(store_module.sqlite3, "connect", traced)
    saved = FailureFixStore(tmp_path / "private" / "db", ACTIVE).record(episode(
        category="contact@example.com", verifier_name="token=my-secret-token",
        error_signature="phone +1 (415) 555-2671",
    ))
    assert "contact@example.com" not in saved.category
    assert "my-secret-token" not in saved.verifier_name
    assert "415" not in saved.error_signature
    secrets = (
        "yellow-bird", "contact@example.com", "my-secret-token", "+1 (415) 555-2671", "/home/alice",
    )
    for secret in secrets:
        assert secret not in "\n".join(trace)


def test_wal_databases_fail_closed_without_creating_sidecars(tmp_path: Path) -> None:
    """Read-only access must not create WAL or shared-memory sidecars."""
    path = tmp_path / "private" / "db"
    store = FailureFixStore(path, ACTIVE)
    store.record(episode())
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    assert not (path.parent / "db-wal").exists()
    assert not (path.parent / "db-shm").exists()
    with pytest.raises(MemoryStoreError, match="journal"):
        store.query()
    assert not (path.parent / "db-wal").exists()
    assert not (path.parent / "db-shm").exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_journal_sidecars_are_rejected_without_touching_them(tmp_path: Path, suffix: str) -> None:
    """Unexpected sidecars are refused for both reads and writes."""
    path = tmp_path / "private" / "db"
    store = FailureFixStore(path, ACTIVE)
    store.record(episode())
    sidecar = path.with_name(path.name + suffix)
    sidecar.write_text("foreign")
    with pytest.raises(MemoryStoreError, match="journal"):
        store.query()
    with pytest.raises(MemoryStoreError, match="journal"):
        store.record(episode(episode_id="second"))
    assert sidecar.read_text() == "foreign"


def test_dangling_journal_symlink_and_invalid_database_fail_closed(tmp_path: Path) -> None:
    """A dangling sidecar or invalid SQLite header is rejected before SQLite runs."""
    path = tmp_path / "private" / "db"
    store = FailureFixStore(path, ACTIVE)
    store.record(episode())
    sidecar = path.with_name(path.name + "-journal")
    sidecar.symlink_to(tmp_path / "missing")
    with pytest.raises(MemoryStoreError, match="journal"):
        store.query()
    sidecar.unlink()
    path.write_bytes(b"not sqlite")
    with pytest.raises(MemoryStoreError, match="journal"):
        store.query()
    path.write_bytes(b"")
    with pytest.raises(MemoryStoreError, match="journal"):
        store.query()
