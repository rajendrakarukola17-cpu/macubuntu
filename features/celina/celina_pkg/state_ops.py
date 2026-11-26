"""Celina Operational Trust & State Lifecycle Engine (v4.4):
1. UndoJournal (`celina undo`): Automatic SHA-256 pre-execution snapshots for
every `files_office` Tier-2 mutation tool with tamper-checked atomic rollback.
2. Tamper-Evident Export/Import (`celina-export` / `celina-import`): Versioned
`.tar.gz` archive of Celina's entire identity (WAL-safe SQLite online backups
of memory.db / cache.db / spotlight_index.db, KG, goals, trusted & quarantined
skills, Astra checkpoints, undo history, neural-schema.json) with SHA-256
MANIFEST.json verification, Tar-Slip defense, and pre-import safety backup.
3. Schema & Tool Migration (`celina migrate [--dry-run]`): Reports exact tools
added/removed/tier-changed between versions and executes idempotent SQLite +
neural-schema.json migrations while preserving personal settings.
4. Project Astra Checkpoints (`celina checkpoints`): Inspect or clean durable
step recovery logs from long-running or interrupted autonomous GUI missions.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config

EXPORT_FORMAT_VERSION = "1.0"
CELINA_STATE_VERSION = "4.4.0"
MAX_UNDO_ENTRIES = 100
MAX_UNDO_BLOB_BYTES = 50 * 1024 * 1024  # 50 MiB cap per single pre-execution snapshot


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_json(path: Path, data: Any) -> None:
    config.ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _is_within_root(target: Path, root: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_sqlite_online_backup(src_db: Path, dst_db: Path) -> bool:
    """Uses SQLite's native online backup API so WAL transactions are never torn."""
    if not src_db.is_file():
        return False
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    from urllib.parse import quote

    uri = f"file:{quote(str(src_db), safe='/')}?mode=ro"
    src_conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    dst_conn = sqlite3.connect(str(dst_db), timeout=5.0)
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()
    try:
        os.chmod(dst_db, 0o600)
    except OSError:
        pass
    return True


# ============================================================================
# 1. Pre-Execution SHA-256 Undo Journal (`celina undo`)
# ============================================================================

class UndoJournal:
    """Lightweight, Celina-owned pre-execution backup & atomic rollback store for
    `files_office` Tier-2 mutation tools. Stored under `~/.local/share/celina/undo/`
    with `0700` dir / `0600` blob permissions.
    """

    def __init__(
        self,
        undo_dir: Path | None = None,
        home_root: Path | None = None,
        max_entries: int = MAX_UNDO_ENTRIES,
    ):
        self.undo_dir = Path(undo_dir) if undo_dir is not None else config.UNDO_DIR
        self.blobs_dir = self.undo_dir / "blobs"
        self.journal_path = self.undo_dir / "journal.json"
        self.home_root = Path(home_root).resolve() if home_root is not None else config.HOME.resolve()
        self.max_entries = max(5, int(max_entries))
        self._lock = threading.RLock()

    def _ensure_dirs(self) -> None:
        config.ensure_private_dir(self.undo_dir)
        config.ensure_private_dir(self.blobs_dir)

    def _load_entries(self) -> list[dict[str, Any]]:
        if not self.journal_path.is_file():
            return []
        try:
            raw = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return raw
        except (OSError, json.JSONDecodeError):
            pass
        return []

    def _save_entries(self, entries: list[dict[str, Any]]) -> None:
        self._ensure_dirs()
        # Prune oldest entries and their orphaned blobs beyond max_entries
        if len(entries) > self.max_entries:
            dropped = entries[: len(entries) - self.max_entries]
            entries = entries[-self.max_entries :]
            for item in dropped:
                for bkey in ("blob_file", "dst_blob_file"):
                    bname = item.get(bkey)
                    if bname:
                        try:
                            (self.blobs_dir / bname).unlink(missing_ok=True)
                        except OSError:
                            pass
        _atomic_write_json(self.journal_path, entries)

    def _snapshot_file(self, src: Path, blob_name: str) -> tuple[str | None, str | None, int | None, int | None]:
        """Copies `src` bytes into `blobs_dir/blob_name` (0600) and returns (blob_name, sha256, size, mode)."""
        if not src.is_file():
            return None, None, None, None
        st = src.stat()
        if st.st_size > MAX_UNDO_BLOB_BYTES:
            return None, _sha256_file(src), st.st_size, st.st_mode & 0o777
        self._ensure_dirs()
        dst_blob = self.blobs_dir / blob_name
        shutil.copy2(src, dst_blob)
        os.chmod(dst_blob, 0o600)
        return blob_name, _sha256_file(dst_blob), st.st_size, st.st_mode & 0o777

    def record_before_mutation(
        self,
        tool_name: str,
        target_path: Path | str,
        *,
        dst_path: Path | str | None = None,
    ) -> str:
        """Captures a tamper-checked SHA-256 snapshot BEFORE a `files_office` tool touches disk.
        Returns a unique `action_id` (e.g. `act_20261115_174500_a1b2c3`).
        """
        target = Path(target_path).expanduser().resolve()
        dst = Path(dst_path).expanduser().resolve() if dst_path is not None else None
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        action_id = f"act_{stamp}_{secrets.token_hex(3)}"

        existed = target.exists()
        blob_file: str | None = None
        pre_sha: str | None = None
        size_bytes: int | None = None
        orig_mode: int | None = None
        is_dir = target.is_dir()

        if existed and target.is_file():
            blob_file, pre_sha, size_bytes, orig_mode = self._snapshot_file(target, f"{action_id}.bak")

        dst_existed = bool(dst and dst.exists())
        dst_blob: str | None = None
        dst_pre_sha: str | None = None
        dst_mode: int | None = None
        if dst and dst.is_file():
            dst_blob, dst_pre_sha, _dsz, dst_mode = self._snapshot_file(dst, f"{action_id}.dst.bak")

        entry: dict[str, Any] = {
            "action_id": action_id,
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "tool": tool_name,
            "target_path": str(target),
            "dst_path": str(dst) if dst else None,
            "existed_before": existed,
            "is_dir": is_dir,
            "blob_file": blob_file,
            "pre_sha256": pre_sha,
            "size_bytes": size_bytes,
            "orig_mode": orig_mode,
            "dst_existed_before": dst_existed,
            "dst_blob_file": dst_blob,
            "dst_pre_sha256": dst_pre_sha,
            "dst_orig_mode": dst_mode,
            "committed": False,
            "post_sha256": None,
            "undone": False,
            "undone_at_utc": None,
        }
        with self._lock:
            entries = self._load_entries()
            entries.append(entry)
            self._save_entries(entries)
        return action_id

    def finalize_mutation(self, action_id: str, *, succeeded: bool = True, final_path: Path | str | None = None) -> None:
        """Marks an undo entry committed (recording post-SHA256) or removes it if the mutation failed."""
        with self._lock:
            entries = self._load_entries()
            kept: list[dict[str, Any]] = []
            for item in entries:
                if item.get("action_id") != action_id:
                    kept.append(item)
                    continue
                if not succeeded:
                    for bkey in ("blob_file", "dst_blob_file"):
                        bname = item.get(bkey)
                        if bname:
                            try:
                                (self.blobs_dir / bname).unlink(missing_ok=True)
                            except OSError:
                                pass
                    continue
                item["committed"] = True
                check_p = Path(final_path).expanduser().resolve() if final_path else Path(
                    item.get("dst_path") or item["target_path"],
                )
                if check_p.is_file():
                    try:
                        item["post_sha256"] = _sha256_file(check_p)
                    except OSError:
                        pass
                kept.append(item)
            self._save_entries(kept)

    def list_actions(self, limit: int = 20, include_undone: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            entries = [e for e in self._load_entries() if e.get("committed")]
            if not include_undone:
                entries = [e for e in entries if not e.get("undone")]
            return list(reversed(entries[-max(1, int(limit)) :]))

    def _restore_blob_atomically(self, blob_name: str, expected_sha: str | None, dest: Path, mode: int | None) -> None:
        blob_path = self.blobs_dir / blob_name
        if not blob_path.is_file():
            raise FileNotFoundError(f"Undo snapshot blob missing: {blob_name}")
        actual_sha = _sha256_file(blob_path)
        if expected_sha and actual_sha != expected_sha:
            raise ValueError(
                f"Undo snapshot integrity check FAILED for {blob_name} "
                f"(expected {expected_sha[:12]}..., got {actual_sha[:12]}...)",
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.celina.undo.{secrets.token_hex(3)}.tmp")
        shutil.copy2(blob_path, tmp)
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        os.replace(tmp, dest)

    def undo_action(self, action_id: str | None = None) -> tuple[bool, str]:
        """Atomically rolls back a recorded Tier-2 file mutation.
        If `action_id` is None, rolls back the most recent committed non-undone action.
        """
        with self._lock:
            entries = self._load_entries()
            target_idx: int | None = None
            if action_id:
                for idx in range(len(entries) - 1, -1, -1):
                    if entries[idx].get("action_id") == action_id:
                        target_idx = idx
                        break
                if target_idx is None:
                    return False, f"Error: Undo action '{action_id}' not found."
            else:
                for idx in range(len(entries) - 1, -1, -1):
                    if entries[idx].get("committed") and not entries[idx].get("undone"):
                        target_idx = idx
                        break
                if target_idx is None:
                    return False, "No reversible Celina file mutations found in the undo journal."

            entry = entries[target_idx]
            aid = str(entry["action_id"])
            if entry.get("undone"):
                return False, f"Action '{aid}' was already undone at {entry.get('undone_at_utc')}."

            tool_name = str(entry.get("tool", ""))
            target = Path(entry["target_path"]).resolve()
            dst = Path(entry["dst_path"]).resolve() if entry.get("dst_path") else None

            if not _is_within_root(target, self.home_root) or config.is_hard_blocked_path(target):
                return False, f"Blocked: Refusing to undo outside home or on protected path ({target})."
            if dst and (not _is_within_root(dst, self.home_root) or config.is_hard_blocked_path(dst)):
                return False, f"Blocked: Refusing to undo move involving protected path ({dst})."

            try:
                if tool_name == "move_file" and dst is not None:
                    if not dst.exists():
                        return False, f"Error: Moved destination no longer exists at {dst}; cannot reverse move."
                    if target.exists():
                        return False, f"Error: Cannot undo move; target '{target}' exists. Move it aside first."
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(target))
                    if entry.get("dst_existed_before") and entry.get("dst_blob_file"):
                        self._restore_blob_atomically(
                            entry["dst_blob_file"],
                            entry.get("dst_pre_sha256"),
                            dst,
                            entry.get("dst_orig_mode"),
                        )
                    msg = f"✅ Undid {aid} (move_file): moved '{dst}' back to '{target}'."
                elif not entry.get("existed_before"):
                    # File was newly created (write_file / create_document / convert_document)
                    if target.is_file():
                        target.unlink()
                        msg = f"✅ Undid {aid} ({tool_name}): removed newly created file '{target}'."
                    else:
                        msg = f"✅ Undid {aid} ({tool_name}): '{target}' was already absent."
                else:
                    if not entry.get("blob_file"):
                        return False, (
                            f"Error: Action '{aid}' ({tool_name}) exceeded snapshot cap "
                            f"({MAX_UNDO_BLOB_BYTES // (1024 * 1024)} MiB) or was a directory; manual restore required."
                        )
                    self._restore_blob_atomically(
                        entry["blob_file"],
                        entry.get("pre_sha256"),
                        target,
                        entry.get("orig_mode"),
                    )
                    sha_short = (entry.get("pre_sha256") or "")[:12]
                    msg = f"✅ Undid {aid} ({tool_name}): restored '{target}' (verified SHA-256 {sha_short}...)."
            except Exception as exc:
                return False, f"Error: Undo failed for {aid}: {exc}"

            entry["undone"] = True
            entry["undone_at_utc"] = datetime.now(UTC).isoformat()
            entries[target_idx] = entry
            self._save_entries(entries)
            return True, msg


_DEFAULT_UNDO_JOURNAL: UndoJournal | None = None


def get_undo_journal() -> UndoJournal:
    global _DEFAULT_UNDO_JOURNAL
    if _DEFAULT_UNDO_JOURNAL is None:
        _DEFAULT_UNDO_JOURNAL = UndoJournal()
    return _DEFAULT_UNDO_JOURNAL


# ============================================================================
# 2. Tamper-Evident Export & Import (`celina-export` / `celina-import`)
# ============================================================================

def _collect_dir_files(root: Path, arc_prefix: str) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for fn in filenames:
            full = Path(dirpath) / fn
            if full.is_symlink() or not full.is_file():
                continue
            rel = full.relative_to(root).as_posix()
            out.append((full, f"{arc_prefix}/{rel}"))
    return out


def export_state(
    output_path: Path | str | None = None,
    *,
    include_secrets: bool = False,
    base_dir: Path | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    """Produces a tamper-evident, versioned `.tar.gz` snapshot of Celina's identity
    (WAL-safe SQLite backups, KG, goals, skills, checkpoints, undo journal, schema)
    with a SHA-256 `MANIFEST.json` and companion `.sha256` file.
    """
    src_base = Path(base_dir) if base_dir is not None else config.BASE_DIR
    src_cfg = Path(config_dir) if config_dir is not None else config.CONFIG_DIR

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_file = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else (src_base / "backups" / f"celina-state-v{CELINA_STATE_VERSION}-{stamp}.tar.gz").resolve()
    )
    out_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="celina-export-") as tmp_s:
        staging = Path(tmp_s)
        payload_entries: list[tuple[Path, str]] = []

        # 1. Online WAL-safe SQLite database backups
        for db_name in (
            "memory.db",
            "cache.db",
            "spotlight_index.db",
            "kg.db",
            "tasks.db",
            "traces.db",
            "working_memory.db",
            "ast_rag.db",
            "priority_kg.db",
        ):
            src_db = src_base / db_name
            if src_db.is_file():
                staged_db = staging / "data" / db_name
                if _safe_sqlite_online_backup(src_db, staged_db):
                    payload_entries.append((staged_db, f"data/{db_name}"))

        # 2. Private state subtrees (skills trusted + quarantine, checkpoints, undo, tamper-evident evidence)
        for sub in ("skills/trusted", "skills/quarantine", "checkpoints", "undo", "evidence"):
            payload_entries.extend(_collect_dir_files(src_base / sub, f"data/{sub}"))

        # 3. Legacy JSON state files if present
        for extra_data in ("semantic_memory.json",):
            p = src_base / extra_data
            if p.is_file() and not p.is_symlink():
                payload_entries.append((p, f"data/{extra_data}"))

        # 4. Configuration files (NEVER export raw API keys in `env` unless explicitly requested!)
        cfg_names = [
            "neural-schema.json",
            "celina-capabilities.json",
            "mcp-servers.json",
            "iot-allowlist.json",
            "model-digests.sha256",
        ]
        if include_secrets:
            cfg_names.append("env")
        for cname in cfg_names:
            cp = src_cfg / cname
            if cp.is_file() and not cp.is_symlink():
                payload_entries.append((cp, f"config/{cname}"))

        payload_entries.sort(key=lambda item: item[1])

        manifest_files: list[dict[str, Any]] = []
        for fpath, arcname in payload_entries:
            st = fpath.stat()
            manifest_files.append(
                {
                    "arcname": arcname,
                    "sha256": _sha256_file(fpath),
                    "size_bytes": st.st_size,
                    "mode": oct(st.st_mode & 0o777),
                },
            )

        canonical_table = json.dumps(manifest_files, sort_keys=True, separators=(",", ":")).encode("utf-8")
        manifest: dict[str, Any] = {
            "format_version": EXPORT_FORMAT_VERSION,
            "celina_version": CELINA_STATE_VERSION,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "hostname": socket.gethostname(),
            "includes_secrets": bool(include_secrets),
            "file_count": len(manifest_files),
            "manifest_sha256": _sha256_bytes(canonical_table),
            "files": manifest_files,
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

        tmp_archive = out_file.with_name(f".{out_file.name}.{secrets.token_hex(3)}.tmp")
        try:
            with tarfile.open(tmp_archive, "w:gz") as tf:
                ti = tarfile.TarInfo(name="MANIFEST.json")
                ti.size = len(manifest_bytes)
                ti.mode = 0o600
                ti.mtime = int(time.time())
                tf.addfile(ti, io.BytesIO(manifest_bytes))

                for fpath, arcname in payload_entries:
                    info = tf.gettarinfo(str(fpath), arcname=arcname)
                    info.mode = 0o600
                    with fpath.open("rb") as fh:
                        tf.addfile(info, fh)
            os.chmod(tmp_archive, 0o600)
            os.replace(tmp_archive, out_file)
        except Exception:
            tmp_archive.unlink(missing_ok=True)
            raise

    archive_sha = _sha256_file(out_file)
    sha_sidecar = Path(f"{out_file}.sha256")
    sha_sidecar.write_text(f"{archive_sha}  {out_file.name}\n", encoding="utf-8")
    try:
        os.chmod(sha_sidecar, 0o600)
    except OSError:
        pass

    return {
        "archive_path": str(out_file),
        "sha256_file": str(sha_sidecar),
        "archive_sha256": archive_sha,
        "manifest_sha256": manifest["manifest_sha256"],
        "file_count": len(manifest_files),
        "includes_secrets": bool(include_secrets),
    }


def _validate_arcname(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name:
        return False
    parts = Path(name).parts
    if any(p in ("", ".", "..") for p in parts):
        return False
    if name == "MANIFEST.json":
        return True
    if parts[0] in ("data", "config"):
        return len(parts) >= 2  # must be data/foo or config/foo, never bare "data"/"config"
    return False


def import_state(
    archive_path: Path | str,
    *,
    dry_run: bool = False,
    create_safety_backup: bool = True,
    base_dir: Path | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    """Verifies a `celina-export` archive (Tar-Slip protection + `MANIFEST.json` envelope
    + per-file SHA-256 verification) BEFORE touching disk, creates an automatic pre-import
    safety backup when existing state is present, and restores with 0700/0600 permissions.
    """
    arc = Path(archive_path).expanduser().resolve()
    if not arc.is_file():
        raise FileNotFoundError(f"Export archive not found: {arc}")

    dst_base = Path(base_dir) if base_dir is not None else config.BASE_DIR
    dst_cfg = Path(config_dir) if config_dir is not None else config.CONFIG_DIR

    # Optional companion .sha256 check if present
    sidecar = Path(f"{arc}.sha256")
    if sidecar.is_file():
        expected_arc_sha = sidecar.read_text(encoding="utf-8").strip().split()[0].lower()
        actual_arc_sha = _sha256_file(arc)
        if expected_arc_sha != actual_arc_sha:
            raise ValueError(
                f"Archive SHA-256 mismatch vs {sidecar.name}: "
                f"expected {expected_arc_sha[:16]}..., got {actual_arc_sha[:16]}...",
            )

    with tempfile.TemporaryDirectory(prefix="celina-import-") as tmp_s:
        staging = Path(tmp_s)
        with tarfile.open(arc, "r:gz") as tf:
            members = tf.getmembers()
            for m in members:
                if not m.isfile():
                    raise ValueError(f"Unsafe non-regular archive entry rejected: {m.name}")
                if not _validate_arcname(m.name):
                    raise ValueError(f"Unsafe archive path rejected (Tar-Slip defense): {m.name}")
                dest = (staging / m.name).resolve()
                if not _is_within_root(dest, staging):
                    raise ValueError(f"Escaping archive path rejected: {m.name}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                extracted = tf.extractfile(m)
                if extracted is None:
                    raise ValueError(f"Unreadable archive member: {m.name}")
                with dest.open("wb") as out_fh:
                    shutil.copyfileobj(extracted, out_fh)

        manifest_path = staging / "MANIFEST.json"
        if not manifest_path.is_file():
            raise ValueError("Archive missing MANIFEST.json — refusing to import unverified archive.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list):
            raise ValueError("Malformed MANIFEST.json: 'files' must be a list.")

        # 1. Verify manifest envelope digest
        canonical_table = json.dumps(manifest_files, sort_keys=True, separators=(",", ":")).encode("utf-8")
        calc_env = _sha256_bytes(canonical_table)
        if manifest.get("manifest_sha256") != calc_env:
            raise ValueError("MANIFEST.json envelope digest mismatch — archive appears tampered.")

        # 2. Verify EVERY file's SHA-256 + ensure no unlisted stray files were injected
        listed_names = {"MANIFEST.json"}
        planned: list[dict[str, Any]] = []
        for item in manifest_files:
            aname = str(item["arcname"])
            if not _validate_arcname(aname) or aname == "MANIFEST.json":
                raise ValueError(f"Invalid manifest entry: {aname}")
            listed_names.add(aname)
            staged_file = staging / aname
            if not staged_file.is_file():
                raise ValueError(f"Missing payload file declared in MANIFEST.json: {aname}")
            actual_sha = _sha256_file(staged_file)
            if actual_sha != str(item["sha256"]):
                raise ValueError(
                    f"SHA-256 verification FAILED for '{aname}' "
                    f"(expected {str(item['sha256'])[:12]}..., got {actual_sha[:12]}...)",
                )

            rel = Path(aname)
            if rel.parts[0] == "data":
                target = dst_base.joinpath(*rel.parts[1:])
            else:
                target = dst_cfg.joinpath(*rel.parts[1:])
            status = "replace" if target.exists() else "create"
            planned.append({"arcname": aname, "target": str(target), "action": status, "sha256": actual_sha})

        # Reject unlisted files inside the tarball
        actual_names = {
            p.relative_to(staging).as_posix()
            for p in staging.rglob("*")
            if p.is_file()
        }
        extra = actual_names - listed_names
        if extra:
            raise ValueError(f"Archive contains unlisted files not covered by MANIFEST.json: {sorted(extra)}")

        if dry_run:
            return {
                "dry_run": True,
                "verified": True,
                "celina_version": manifest.get("celina_version", "unknown"),
                "created_at_utc": manifest.get("created_at_utc", ""),
                "file_count": len(planned),
                "planned": planned,
            }

        # 3. Create automatic pre-import safety backup if existing state exists
        safety_backup_path: str | None = None
        if create_safety_backup and ((dst_base / "memory.db").exists() or (dst_cfg / "neural-schema.json").exists()):
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            sb = dst_base / "backups" / f"pre-import-safety-{stamp}.tar.gz"
            res = export_state(sb, include_secrets=False, base_dir=dst_base, config_dir=dst_cfg)
            safety_backup_path = res["archive_path"]

        # 4. Atomically write verified payload files with strict 0700 / 0600 permissions
        config.ensure_private_dir(dst_base)
        config.ensure_private_dir(dst_cfg)
        for pitem in planned:
            staged_file = staging / pitem["arcname"]
            target = Path(pitem["target"])
            config.ensure_private_dir(target.parent)
            # Remove stale WAL/SHM sidecars when replacing SQLite DBs cleanly
            if target.suffix == ".db":
                for suf in ("-wal", "-shm"):
                    Path(f"{target}{suf}").unlink(missing_ok=True)
            tmp = target.with_name(f".{target.name}.import.{secrets.token_hex(3)}.tmp")
            shutil.copy2(staged_file, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)

        return {
            "dry_run": False,
            "verified": True,
            "celina_version": manifest.get("celina_version", "unknown"),
            "file_count": len(planned),
            "safety_backup": safety_backup_path,
            "restored": planned,
        }


# ============================================================================
# 3. Schema & Tool Migration (`celina migrate [--dry-run]`)
# ============================================================================

def _extract_tier_map(schema: dict[str, Any]) -> dict[str, str]:
    sp = schema.get("security_policy") or {}
    tmap: dict[str, str] = {}
    for t in sp.get("tier1_auto_tools") or []:
        tmap[str(t)] = "Tier 1 (auto)"
    for t in sp.get("tier2_approval_tools") or []:
        tmap[str(t)] = "Tier 2 (approval)"
    return tmap


def migrate_state(
    *,
    dry_run: bool = False,
    schema_path: Path | str | None = None,
    template_schema_path: Path | str | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Compares on-disk `neural-schema.json` against the target schema (defaulting to
    `config.DEFAULT_NEURAL_SCHEMA` or an explicit template JSON), reports exact tool
    additions, removals, and tier transitions, and (unless `dry_run=True`) persists the
    migrated schema + runs non-destructive SQLite schema migrations.
    """
    target_schema_file = Path(schema_path) if schema_path is not None else config.SCHEMA_FILE
    data_dir = Path(base_dir) if base_dir is not None else config.SHARED_STORAGE_DIR

    if template_schema_path is not None:
        target_schema = json.loads(Path(template_schema_path).read_text(encoding="utf-8"))
    else:
        target_schema = json.loads(json.dumps(config.DEFAULT_NEURAL_SCHEMA))

    existed_on_disk = target_schema_file.is_file()
    if existed_on_disk:
        current_disk = json.loads(target_schema_file.read_text(encoding="utf-8"))
    else:
        current_disk = {}

    ver_before = str(current_disk.get("schema_version", "none"))
    ver_after = str(target_schema.get("schema_version", "4.4"))

    old_tiers = _extract_tier_map(current_disk) if existed_on_disk else {}
    new_tiers = _extract_tier_map(target_schema)

    tools_added = sorted(set(new_tiers) - set(old_tiers))
    tools_removed = sorted(set(old_tiers) - set(new_tiers))
    tier_changes = [
        {"tool": t, "from": old_tiers[t], "to": new_tiers[t]}
        for t in sorted(set(old_tiers) & set(new_tiers))
        if old_tiers[t] != new_tiers[t]
    ]

    old_domains = set((current_disk.get("tool_domains") or {}).keys())
    new_domains = set((target_schema.get("tool_domains") or {}).keys())
    domains_added = sorted(new_domains - old_domains)

    # Build migrated schema in memory (preserving custom persona/routing/user keys,
    # while enforcing fail-closed Tier-2 precedence for promoted tools!)
    migrated = config._deep_merge(target_schema, current_disk) if existed_on_disk else dict(target_schema)
    base_sec = target_schema.get("security_policy") or {}
    merged_sec = dict(migrated.get("security_policy") or {})
    for key in ("tier1_auto_tools", "tier2_approval_tools", "protected_paths"):
        merged_sec[key] = config._union_list(base_sec.get(key), merged_sec.get(key))
    t2_promoted = set(base_sec.get("tier2_approval_tools") or [])
    merged_sec["tier1_auto_tools"] = [t for t in merged_sec["tier1_auto_tools"] if t not in t2_promoted]
    migrated["security_policy"] = merged_sec

    base_priv = target_schema.get("privacy") or {}
    merged_priv = dict(migrated.get("privacy") or {})
    for key in ("local_sensitive_tools", "hard_blocked_paths"):
        merged_priv[key] = config._union_list(base_priv.get(key), merged_priv.get(key))
    migrated["privacy"] = merged_priv

    base_doms = target_schema.get("tool_domains") or {}
    upgraded_doms = {k: dict(v) for k, v in base_doms.items()}
    for k, v in (current_disk.get("tool_domains") or {}).items():
        if k not in upgraded_doms and isinstance(v, dict):
            upgraded_doms[k] = v
    migrated["tool_domains"] = upgraded_doms
    migrated["schema_version"] = ver_after

    db_status: dict[str, str] = {}
    for db_name in ("memory.db", "cache.db", "spotlight_index.db"):
        p = data_dir / db_name
        db_status[db_name] = "exists (would verify WAL + schema)" if p.exists() else "not initialized yet"

    if not dry_run:
        _atomic_write_json(target_schema_file, migrated)
        # Run non-destructive SQLite schema initialization/migrations on existing DBs
        from . import cache as _cache
        from . import memory as _memory
        from . import spotlight_index as _spot

        mem = _memory.MemoryDB(db_path=data_dir / "memory.db")
        try:
            db_status["memory.db"] = "migrated (WAL + busy_timeout=5000)"
        finally:
            mem.close()
        sc = _cache.SmartCache(db_path=data_dir / "cache.db")
        try:
            db_status["cache.db"] = "migrated (WAL + busy_timeout=5000)"
        finally:
            sc.close()
        sp_idx = _spot.SpotlightIndex(db_path=data_dir / "spotlight_index.db")
        try:
            db_status["spotlight_index.db"] = "migrated (WAL + busy_timeout=5000)"
        finally:
            sp_idx.close()

    return {
        "dry_run": dry_run,
        "schema_path": str(target_schema_file),
        "schema_existed": existed_on_disk,
        "schema_version_before": ver_before,
        "schema_version_after": ver_after,
        "tools_added": tools_added,
        "tools_removed": tools_removed,
        "tier_changes": tier_changes,
        "domains_added": domains_added,
        "databases": db_status,
    }


def format_migrate_report(rep: dict[str, Any]) -> str:
    mode = "DRY-RUN (0 bytes written)" if rep["dry_run"] else "APPLIED"
    lines = [
        f"🔄 Celina Migration Report [{mode}]",
        f"  Schema file:    {rep['schema_path']}",
        f"  Schema version: {rep['schema_version_before']} -> {rep['schema_version_after']}",
        f"  Tools added:    {len(rep['tools_added'])}"
        + (f" ({', '.join(rep['tools_added'][:10])}{'...' if len(rep['tools_added']) > 10 else ''})" if rep["tools_added"] else ""),
        f"  Tools removed:  {len(rep['tools_removed'])}"
        + (f" ({', '.join(rep['tools_removed'][:10])})" if rep["tools_removed"] else ""),
        f"  Tier changes:   {len(rep['tier_changes'])}",
    ]
    for tc in rep["tier_changes"]:
        lines.append(f"    • {tc['tool']}: {tc['from']} -> {tc['to']}")
    if rep["domains_added"]:
        lines.append(f"  Domains added:  {', '.join(rep['domains_added'])}")
    lines.append("  SQLite stores:")
    for db_name, status in rep["databases"].items():
        lines.append(f"    • {db_name}: {status}")
    return "\n".join(lines)


# ============================================================================
# 4. CLI Dispatch (`python3 -m celina_pkg.state_ops ...`)
# ============================================================================

def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="celina",
        description="Celina operational safety commands: export, import, undo, migrate, checkpoints",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_exp = sub.add_parser("export", help="Create a tamper-evident SHA-256 archive of Celina's state")
    p_exp.add_argument("output", nargs="?", default=None, help="Output .tar.gz path")
    p_exp.add_argument(
        "--include-secrets",
        action="store_true",
        help="Also include ~/.config/celina/env (API keys) — OFF by default",
    )

    p_imp = sub.add_parser("import", help="Verify SHA-256 manifest and restore a celina-export archive")
    p_imp.add_argument("archive", help="Path to celina-state-*.tar.gz archive")
    p_imp.add_argument("--profile", default=None, help="Restore into an isolated target profile directory")
    p_imp.add_argument("--dry-run", action="store_true", help="Verify archive & show restore plan without writing")
    p_imp.add_argument("--no-safety-backup", action="store_true", help="Skip automatic pre-import backup")

    p_undo = sub.add_parser("undo", help="Roll back a files_office Tier-2 mutation using pre-execution SHA-256 backups")
    p_undo.add_argument("action_id", nargs="?", default=None, help="Specific action ID (omit or use --last for most recent)")
    p_undo.add_argument("--list", action="store_true", help="List recent reversible mutations")
    p_undo.add_argument("--last", action="store_true", help="Undo the most recent reversible mutation")
    p_undo.add_argument("--limit", type=int, default=20, help="Max items for --list")

    p_mig = sub.add_parser("migrate", help="Migrate neural-schema.json & SQLite DBs and report tool/tier diffs")
    p_mig.add_argument("--dry-run", action="store_true", help="Show schema & tool diff without writing")
    p_mig.add_argument("--template", default=None, help="Optional path to target neural-schema.json template")
    p_mig.add_argument("--json", action="store_true", help="Output structured JSON")

    p_chk = sub.add_parser("checkpoints", help="Inspect Project Astra autonomous task checkpoints")
    p_chk.add_argument("--show", default=None, help="Show full step recovery log for a task ID")
    p_chk.add_argument("--limit", type=int, default=15, help="Max checkpoints to list")

    p_ev = sub.add_parser("evidence", help="Verify, inspect, or export Celina's hash-chained Tier-2 evidence ledger")
    p_ev.add_argument("action", choices=["verify", "export", "list"], help="Evidence ledger operation")
    p_ev.add_argument("output", nargs="?", default=None, help="Output .jsonl path for 'export'")
    p_ev.add_argument("--limit", type=int, default=20, help="Max records for 'list'")

    p_cap = sub.add_parser("capabilities", help="Audit Celina's OS-level capability manifest")
    p_cap.add_argument("--tool", default=None, help="Inspect capability profile for one tool")
    p_cap.add_argument("--audit", action="store_true", help="Run Shadow-AI invariant validation")

    p_auto = sub.add_parser("autonomy", help="Inspect or manage Celina v5 Autonomous TaskGraph (tasks.db)")
    p_auto.add_argument("--status", action="store_true", help="Show TaskGraph summary, counts & evidence chain")
    p_auto.add_argument("--submit", default=None, help="Submit a new multi-step goal into tasks.db")
    p_auto.add_argument("--cancel", default=None, help="Cancel a task ID and all descendants")
    p_auto.add_argument("--proposals", action="store_true", help="List overnight self-improvement proposals")

    p_cfg = sub.add_parser("config", help="Manage opt-in MCP servers (add-mcp / revoke-mcp / remove-mcp / list-mcp)")
    cfg_sub = p_cfg.add_subparsers(dest="cfg_action", required=True)

    p_add_mcp = cfg_sub.add_parser("add-mcp", help="Add an isolated, approved, revocable MCP server")
    p_add_mcp.add_argument("name", help="Unique MCP server name")
    p_add_mcp.add_argument("cmd", nargs="+", help="Server executable and arguments")
    p_add_mcp.add_argument("--description", default="", help="Optional description")
    p_add_mcp.add_argument("--allow-tool", action="append", default=[], help="Optional explicitly auto-approved tool name")
    p_add_mcp.add_argument("--yes", action="store_true", help="Explicitly confirm approval non-interactively")

    p_rev_mcp = cfg_sub.add_parser("revoke-mcp", help="Trigger per-server kill switch (revokes & stops MCP server)")
    p_rev_mcp.add_argument("name", help="MCP server name to revoke")

    p_rem_mcp = cfg_sub.add_parser("remove-mcp", help="Stop and remove an MCP server from mcp-servers.json")
    p_rem_mcp.add_argument("name", help="MCP server name to remove")

    cfg_sub.add_parser("list-mcp", help="List configured MCP servers and kill-switch status")
    cfg_sub.add_parser("mcp-catalog", help="Show the ordered 7-server MCP perception/actuation catalog & priorities")
    p_preset = cfg_sub.add_parser("setup-mcp", help="Enable a curated MCP catalog preset (e.g. computer-use-linux, shadow-vision, ghostdesk)")
    p_preset.add_argument("preset", help="Preset name from `celina config mcp-catalog`")
    p_preset.add_argument("--yes", action="store_true", help="Confirm opt-in approval non-interactively")

    p_tr = sub.add_parser("traces", help="Inspect durable OpenTelemetry traces & /why receipts (traces.db)")
    p_tr.add_argument("--limit", type=int, default=20, help="Number of recent traces/receipts to show")
    p_tr.add_argument("--why", action="store_true", help="List persisted /why decision receipts instead of traces")

    args = parser.parse_args(argv)

    if args.command == "export":
        res = export_state(args.output, include_secrets=bool(args.include_secrets))
        print(f"✅ Celina state exported ({res['file_count']} files)")
        print(f"   Archive:  {res['archive_path']}")
        print(f"   SHA-256:  {res['archive_sha256']}")
        print(f"   Manifest: {res['manifest_sha256']}")
        print(f"   Secrets:  {'INCLUDED' if res['includes_secrets'] else 'excluded (safe default)'}")
        return 0

    if args.command == "import":
        prof_base = (Path(args.profile).expanduser().resolve() / "data") if args.profile else None
        prof_cfg = (Path(args.profile).expanduser().resolve() / "config") if args.profile else None
        try:
            res = import_state(
                args.archive,
                dry_run=bool(args.dry_run),
                create_safety_backup=not bool(args.no_safety_backup),
                base_dir=prof_base,
                config_dir=prof_cfg,
            )
        except Exception as exc:
            print(f"❌ Import failed: {exc}", file=sys.stderr)
            return 1
        if res["dry_run"]:
            print(f"✅ Verified archive (Celina {res['celina_version']}, {res['file_count']} files) [DRY-RUN]:")
            for p in res["planned"]:
                print(f"   [{p['action']}] {p['arcname']} -> {p['target']} ({p['sha256'][:12]}...)")
        else:
            print(f"✅ Verified & imported {res['file_count']} files from Celina {res['celina_version']}")
            if res.get("safety_backup"):
                print(f"   Pre-import safety backup: {res['safety_backup']}")
        return 0

    if args.command == "undo":
        journal = get_undo_journal()
        if args.list or (args.action_id is None and not args.last):
            items = journal.list_actions(limit=args.limit)
            if not items:
                print("No reversible Celina file mutations recorded yet.")
                return 0
            print(f"Reversible Celina File Mutations ({len(items)} shown):")
            for it in items:
                st = "UNDONE" if it.get("undone") else "ACTIVE"
                sha = (it.get("pre_sha256") or "new-file")[:12]
                print(f"  {it['action_id']}  [{st:6s}]  {it['tool']:16s}  {it['target_path']}  (pre:{sha})")
            print("\nRun `celina undo <action_id>` or `celina undo --last` to restore.")
            return 0
        ok, msg = journal.undo_action(None if args.last else args.action_id)
        print(msg, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.command == "migrate":
        rep = migrate_state(dry_run=bool(args.dry_run), template_schema_path=args.template)
        if args.json:
            print(json.dumps(rep, indent=2, sort_keys=True))
        else:
            print(format_migrate_report(rep))
        return 0

    if args.command == "checkpoints":
        from . import computer_use

        if args.show:
            ckpt = computer_use.load_checkpoint(args.show)
            if not ckpt:
                print(f"Checkpoint '{args.show}' not found.", file=sys.stderr)
                return 1
            print(json.dumps(ckpt, indent=2))
            return 0
        items = computer_use.list_checkpoints(limit=args.limit)
        if not items:
            print("No Project Astra checkpoints recorded yet.")
            return 0
        print(f"Project Astra Task Checkpoints ({len(items)} shown):")
        for c in items:
            print(
                f"  {c['task_id']}  [{c['status']:11s}]  "
                f"steps={c['completed_steps']}/{c['max_steps']}  goal={c['goal'][:60]}",
            )
        return 0

    if args.command == "evidence":
        from . import evidence

        ledger = evidence.get_evidence_ledger()
        if args.action == "verify":
            ok, msg, _cnt = ledger.verify_chain()
            print(msg, file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
        if args.action == "export":
            try:
                res = ledger.export_evidence(args.output)
            except Exception as exc:
                print(f"❌ Evidence export failed: {exc}", file=sys.stderr)
                return 1
            print(f"✅ Evidence ledger exported ({res['record_count']} records)")
            print(f"   File:    {res['export_path']}")
            print(f"   SHA-256: {res['sha256']}")
            return 0
        items = ledger.list_entries(limit=args.limit)
        if not items:
            print("Evidence ledger is empty (0 Tier-2 executions recorded yet).")
            return 0
        print(f"Hash-Chained Tier-2 Agent Evidence Ledger ({len(items)} shown):")
        for it in items:
            uid = it.get("undo_action_id") or "-"
            print(
                f"  #{it['seq']:04d}  [{it['outcome']:7s}]  {it['tool']:22s}  "
                f"undo={uid:22s}  hash={str(it['entry_hash'])[:12]}",
            )
        return 0

    if args.command == "capabilities":
        from . import capabilities

        mf = capabilities.load_manifest(force_reload=True)
        if args.tool:
            cap = capabilities.get_tool_capability(args.tool, manifest=mf)
            if not cap:
                print(f"Tool '{args.tool}' not found in capability manifest.", file=sys.stderr)
                return 1
            print(json.dumps({args.tool: cap}, indent=2))
            return 0
        print(capabilities.format_capabilities_summary(mf))
        if args.audit:
            errs = capabilities.validate_manifest_invariants(
                mf,
                expected_tier1=set(config.TIER1_AUTO_TOOLS),
                expected_tier2=set(config.TIER2_APPROVAL_TOOLS),
            )
            if errs:
                for e in errs:
                    print(f"❌ {e}", file=sys.stderr)
                return 1
            print("✅ Shadow-AI & OS capability invariants PASSED (0 violations).")
        return 0

    if args.command == "autonomy":
        from .learning.self_improve import SelfImprover
        from .planner.task_graph import TaskGraph
        from .planner.tree_planner import TreePlanner

        graph = TaskGraph(config.BASE_DIR / "tasks.db")
        try:
            if args.submit:
                planner = TreePlanner(llm_client=None, graph=graph)
                avail = sorted(config.TIER1_AUTO_TOOLS | config.TIER2_APPROVAL_TOOLS)
                rid = planner.plan(args.submit, available_tools=avail)
                print(json.dumps({"ok": True, "root_task_id": rid, "goal": args.submit}, indent=2))
                return 0
            if args.cancel:
                cnt = graph.cancel_task(args.cancel)
                print(json.dumps({"ok": True, "task_id": args.cancel, "cancelled_count": cnt}, indent=2))
                return 0
            if args.proposals:
                imp = SelfImprover(graph, llm=None)
                print(json.dumps(imp.list_proposals(), indent=2))
                return 0
            print(json.dumps(graph.export_graph(), indent=2))
            return 0
        finally:
            graph.close()

    if args.command == "config":
        from . import mcp_client

        cfg_file = config.MCP_CONFIG_FILE
        if args.cfg_action == "add-mcp":
            cmd_val: str | list[str] = args.cmd[0] if len(args.cmd) == 1 else list(args.cmd)
            approved = bool(args.yes)
            if not approved and sys.stdin.isatty():
                try:
                    ans = input(f"Approve opt-in isolated MCP server '{args.name}' ({cmd_val})? [y/N]: ").strip().lower()
                    approved = ans in {"y", "yes"}
                except (EOFError, KeyboardInterrupt):
                    approved = False
            elif not approved:
                # Explicit CLI invocation `celina config add-mcp <name> <command>` with --yes required if non-TTY
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": f"Approval required: pass --yes or confirm interactively to add MCP server '{args.name}'.",
                        },
                        indent=2,
                    ),
                    file=sys.stderr,
                )
                return 1
            try:
                res = mcp_client.add_mcp_server(
                    args.name,
                    cmd_val,
                    cfg_path=cfg_file,
                    description=args.description,
                    approve=approved,
                    allowed_tools=list(args.allow_tool or []),
                )
                print(json.dumps(res, indent=2))
                return 0
            except (ValueError, PermissionError) as exc:
                print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
                return 1

        if args.cfg_action == "revoke-mcp":
            res = mcp_client.revoke_mcp_server(args.name, cfg_path=cfg_file)
            print(json.dumps(res, indent=2))
            return 0 if res.get("ok") else 1

        if args.cfg_action == "remove-mcp":
            res = mcp_client.remove_mcp_server(args.name, cfg_path=cfg_file)
            print(json.dumps(res, indent=2))
            return 0 if res.get("ok") else 1

        if args.cfg_action == "list-mcp":
            cfg = mcp_client.load_mcp_config(cfg_file)
            merged = dict(cfg.get("mcpServers") or {})
            merged.update(cfg.get("servers") or {})
            print(json.dumps(merged, indent=2))
            return 0

        if args.cfg_action == "mcp-catalog":
            from .autonomy import mcp_loader

            print(json.dumps(list(mcp_loader.MCP_SERVER_CATALOG), indent=2))
            return 0

        if args.cfg_action == "setup-mcp":
            from .autonomy import mcp_loader

            try:
                preset_cfg = mcp_loader.get_catalog_preset(args.preset)
                mcp_loader.validate_server_policy(args.preset, preset_cfg)
            except (ValueError, mcp_loader.MCPPolicyError) as exc:
                print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
                return 1
            if not args.yes:
                print(
                    json.dumps(
                        {"ok": False, "error": f"Approval required: pass --yes to enable MCP preset '{args.preset}'."},
                        indent=2,
                    ),
                    file=sys.stderr,
                )
                return 1
            cfg = mcp_client.load_mcp_config(cfg_file)
            mcp_srvs = dict(cfg.get("mcpServers") or {})
            preset_cfg["enabled"] = True
            mcp_srvs[args.preset.strip().lower()] = preset_cfg
            cfg["mcpServers"] = mcp_srvs
            mcp_client.save_mcp_config(cfg, cfg_file)
            print(json.dumps({"ok": True, "preset": args.preset.strip().lower(), "config": preset_cfg}, indent=2))
            return 0

    if args.command == "traces":
        from . import telemetry

        store = telemetry.DurableTraceStore()
        try:
            if args.why:
                print(json.dumps(store.list_why_receipts(limit=args.limit), indent=2))
            else:
                print(json.dumps(store.list_traces(limit=args.limit), indent=2))
            return 0
        finally:
            store.close()

    return 1


if __name__ == "__main__":
    raise SystemExit(run_cli())
