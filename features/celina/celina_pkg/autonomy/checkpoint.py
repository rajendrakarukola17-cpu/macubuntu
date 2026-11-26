"""
Celina v5 Autonomy — Pre-Execution State Checkpoints (`checkpoint.py`)

Creates tamper-evident SHA-256 pre-execution file snapshots (`0700`/`0600`)
before any `files_office` mutation tool executes inside the Autonomy Engine,
guaranteeing reversibility via `restore_snapshot()` or `celina undo`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ACTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

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
    }
)


@dataclasses.dataclass(frozen=True)
class SnapshotRecord:
    action_id: str
    task_id: str
    tool_name: str
    original_path: str
    backup_dir: str
    backup_file: Optional[str]
    sha256_before: Optional[str]
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


class PreExecutionCheckpoint:
    def __init__(self, undo_dir: Path):
        self.undo_dir = Path(undo_dir)
        self.undo_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.undo_dir, 0o700)
        except OSError:
            pass

    def capture(self, *, task_id: str, tool_name: str, target_path: Optional[str]) -> Optional[SnapshotRecord]:
        if tool_name not in FILE_MUTATION_TOOLS or not target_path:
            return None
        target = Path(target_path).expanduser().resolve()
        action_id = f"{task_id[:12]}-{uuid.uuid4().hex[:8]}"
        dest = self.undo_dir / action_id
        dest.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(dest, 0o700)
        except OSError:
            pass

        existed = target.is_file()
        sha_before: Optional[str] = None
        backup_file: Optional[str] = None
        if existed:
            sha_before = _sha256_file(target)
            bpath = dest / target.name
            shutil.copy2(target, bpath)
            try:
                os.chmod(bpath, 0o600)
            except OSError:
                pass
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
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        meta_path = dest / "meta.json"
        meta_path.write_text(json.dumps(rec.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(meta_path, 0o600)
        except OSError:
            pass
        return rec

    def restore(self, action_id: str) -> tuple[bool, str]:
        if not _ACTION_ID_RE.fullmatch(str(action_id or "")):
            return False, f"Invalid checkpoint action_id: {action_id!r}"
        meta_path = (self.undo_dir / action_id / "meta.json").resolve()
        if not str(meta_path).startswith(str(self.undo_dir.resolve()) + os.sep):
            return False, f"Refusing path traversal in action_id={action_id}"
        if not meta_path.is_file():
            return False, f"Checkpoint metadata not found for action_id={action_id}"
        rec = json.loads(meta_path.read_text(encoding="utf-8"))
        orig = Path(rec["original_path"])
        if rec["existed_before"]:
            bfile = Path(rec["backup_file"])
            if not bfile.is_file():
                return False, f"Backup payload missing: {bfile}"
            actual_sha = _sha256_file(bfile)
            if actual_sha != rec["sha256_before"]:
                return False, f"Backup SHA-256 mismatch (tampered snapshot for {action_id})"
            orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bfile, orig)
            return True, f"Restored {orig} (verified sha256={actual_sha[:12]})"
        # File did not exist before mutation -> remove created artifact safely
        if orig.is_file():
            orig.unlink()
        return True, f"Removed newly created file {orig}"
