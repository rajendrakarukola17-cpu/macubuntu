"""Celina v5 Autonomy — Pre-Execution State Checkpoints (`checkpoint.py`)

Creates tamper-evident SHA-256 pre-execution file snapshots (`0700`/`0600`)
before any `files_office` mutation tool executes inside the Autonomy Engine,
guaranteeing reversibility via `restore_snapshot()` or `celina undo`.

P0/P1 hardening:
  - Sanitized `action_id` (task_id chars outside [A-Za-z0-9_-] -> `_`)
  - Never checkpoints sensitive paths (~/.ssh, ~/.gnupg, ~/.aws, ~/.kube, ...)
  - Atomic restore via temp-file + os.replace (never follows symlinks at dst)
  - umask 0o077 around all file/meta creation (zero permission-race window)
  - Corrupt meta.json handled gracefully in restore()
  - Bounded retention via prune(max_backups=50)
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ACTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_PREFIX_RE = re.compile(r"[^A-Za-z0-9_-]+")

# Paths that must never be duplicated into the undo cache (P0-8)
_NEVER_CHECKPOINT_PARTS: frozenset[str] = frozenset(
    {".ssh", ".gnupg", ".aws", ".kube", ".ai-env"},
)
_NEVER_CHECKPOINT_NAMES: frozenset[str] = frozenset(
    {"id_rsa", "id_ed25519", "env", "config", "settings.json"},
)
_NEVER_CHECKPOINT_PREFIXES: tuple[str, ...] = (
    ".ssh/",
    ".gnupg/",
    ".aws/",
    ".kube/",
    ".ai-env/",
    "config/celina/env",
)


FILE_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "move_file",
        "trash_file",
        "create_document",
        "edit_document",
        "convert_document",
        "libreoffice_edit",
    },
)


@dataclasses.dataclass(frozen=True)
class SnapshotRecord:
    action_id: str
    task_id: str
    tool_name: str
    original_path: str
    backup_dir: str
    backup_file: str | None
    sha256_before: str | None
    existed_before: bool
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_action_id(task_id: str) -> str:
    """Sanitize task_id into a `_ACTION_ID_RE`-compatible action_id prefix.

    Replaces any char outside [A-Za-z0-9_-] with `_`, eliminating path
    traversal (../), dots, colons, slashes, etc. Guarantees 100% regex match.
    """
    prefix = _SAFE_PREFIX_RE.sub("_", str(task_id or "task"))[:12].strip("_") or "task"
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _is_forbidden_checkpoint_path(target: Path) -> bool:
    """Return True if `target` lives under a sensitive credential/config path."""
    parts = set(target.parts)
    if parts & _NEVER_CHECKPOINT_PARTS:
        return True
    if target.name in _NEVER_CHECKPOINT_NAMES:
        return True
    rel_posix = target.as_posix()
    for prefix in _NEVER_CHECKPOINT_PREFIXES:
        if rel_posix == prefix or rel_posix.startswith(prefix + "/"):
            return True
    return False


def _atomic_write_private_text(target: Path, content: str) -> None:
    """Atomically write text to target with fsync + mode 0600 under umask 0o077."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _atomic_copy_file(src: Path, dst: Path) -> None:
    """Atomically replace dst with src without following a symlink at dst.

    Writes to a temp file in dst.parent, then os.replace()s it over dst.
    Raises PermissionError if dst is a symlink (refusing to overwrite the target).
    """
    if dst.is_symlink():
        raise PermissionError(f"Refusing to restore through symlink: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".restore_", suffix=".tmp", dir=str(dst.parent))
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(src, tmp_path)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, dst)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class PreExecutionCheckpoint:
    def __init__(self, undo_dir: Path):
        self.undo_dir = Path(undo_dir)
        old_umask = os.umask(0o077)
        try:
            self.undo_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.undo_dir, 0o700)
        finally:
            os.umask(old_umask)

    def capture(
        self,
        *,
        task_id: str,
        tool_name: str,
        target_path: str | None,
    ) -> SnapshotRecord | None:
        if tool_name not in FILE_MUTATION_TOOLS or not target_path:
            return None

        target = Path(target_path).expanduser().resolve()

        # P0-8: Never checkpoint sensitive credential/config paths
        if _is_forbidden_checkpoint_path(target):
            return None

        # P0-7: Sanitized action_id — always matches _ACTION_ID_RE, no traversal
        action_id = _safe_action_id(task_id)
        if not _ACTION_ID_RE.fullmatch(action_id):
            return None  # fail-closed

        dest = self.undo_dir / action_id
        old_umask = os.umask(0o077)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            os.chmod(dest, 0o700)
        finally:
            os.umask(old_umask)

        existed = target.is_file() and not target.is_symlink()
        sha_before: str | None = None
        backup_file: str | None = None
        if existed:
            sha_before = _sha256_file(target)
            bpath = dest / target.name
            # Copy under umask 0o077, then chmod 0600 (no race window)
            shutil.copy2(target, bpath)
            os.chmod(bpath, 0o600)
            backup_file = str(bpath)

        rec = SnapshotRecord(
            action_id=action_id,
            task_id=task_id,
            tool_name=tool_name,
            original_path=str(target),
            backup_dir=str(dest),
            backup_file=backup_file,
            sha256_before=sha_before,
            existed_before=existed,
            timestamp=datetime.now(UTC).isoformat(),
        )
        meta_path = dest / "meta.json"
        _atomic_write_private_text(meta_path, json.dumps(rec.to_dict(), indent=2, sort_keys=True))
        return rec

    def restore(self, action_id: str) -> tuple[bool, str]:
        # Validate action_id strictly
        if not _ACTION_ID_RE.fullmatch(str(action_id or "")):
            return False, f"Invalid checkpoint action_id: {action_id!r}"

        meta_path = (self.undo_dir / action_id / "meta.json").resolve()
        undo_dir_resolved = self.undo_dir.resolve()

        # P0-8: Verify meta.json stays strictly inside undo_dir (traversal guard)
        if not str(meta_path).startswith(str(undo_dir_resolved) + os.sep):
            return False, f"Refusing path traversal in action_id={action_id}"
        if not meta_path.is_file():
            return False, f"Checkpoint metadata not found for action_id={action_id}"

        # P1: Gracefully handle corrupt/incomplete meta.json
        try:
            rec = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return False, f"Corrupt checkpoint metadata for {action_id}: {exc}"

        try:
            orig = Path(rec["original_path"])
        except (KeyError, TypeError) as exc:
            return False, f"Malformed checkpoint metadata for {action_id}: {exc}"

        # P0-8: Check symlink BEFORE copying or unlinking — never follow symlinks
        if orig.is_symlink():
            return False, f"Refusing to restore through symlink: {orig}"

        if rec.get("existed_before"):
            bfile = Path(rec.get("backup_file") or "")
            if not bfile.is_file():
                return False, f"Backup payload missing: {bfile}"
            # P0-8: Verify backup file is strictly inside undo_dir
            bfile_resolved = bfile.resolve()
            if not str(bfile_resolved).startswith(str(undo_dir_resolved) + os.sep):
                return False, f"Backup payload outside undo_dir: {bfile}"
            actual_sha = _sha256_file(bfile)
            if actual_sha != rec.get("sha256_before"):
                return False, f"Backup SHA-256 mismatch (tampered snapshot for {action_id})"
            orig.parent.mkdir(parents=True, exist_ok=True)
            # P0-8: Atomic restore — temp file + os.replace, never direct copy2
            try:
                _atomic_copy_file(bfile, orig)
            except PermissionError as exc:
                return False, str(exc)
            return True, f"Restored {orig} (verified sha256={actual_sha[:12]})"

        # File did not exist before mutation -> remove created artifact safely
        if orig.is_file() and not orig.is_symlink():
            orig.unlink()
        return True, f"Removed newly created file {orig}"

    def list_snapshots(self) -> list[SnapshotRecord]:
        """Return all valid snapshots in undo_dir, newest first."""
        out: list[SnapshotRecord] = []
        if not self.undo_dir.is_dir():
            return out
        for entry in sorted(self.undo_dir.iterdir(), key=lambda p: p.name, reverse=True):
            meta = entry / "meta.json"
            if not meta.is_file():
                continue
            try:
                rec = json.loads(meta.read_text(encoding="utf-8"))
                out.append(
                    SnapshotRecord(
                        action_id=str(rec["action_id"]),
                        task_id=str(rec["task_id"]),
                        tool_name=str(rec["tool_name"]),
                        original_path=str(rec["original_path"]),
                        backup_dir=str(rec["backup_dir"]),
                        backup_file=rec.get("backup_file"),
                        sha256_before=rec.get("sha256_before"),
                        existed_before=bool(rec.get("existed_before", False)),
                        timestamp=str(rec.get("timestamp", "")),
                    ),
                )
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue
        return out

    def prune(self, *, max_backups: int = 50) -> int:
        """Delete oldest snapshots beyond `max_backups`, returning count removed."""
        snaps = self.list_snapshots()
        removed = 0
        for snap in snaps[max_backups:]:
            try:
                shutil.rmtree(self.undo_dir / snap.action_id, ignore_errors=True)
                removed += 1
            except OSError:
                pass
        return removed
