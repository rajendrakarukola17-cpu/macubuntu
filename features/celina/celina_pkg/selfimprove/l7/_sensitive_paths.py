# Copyright 2026 Celina contributors.
"""Fail-closed L7 inventory for security-owned files and directory prefixes."""

from __future__ import annotations

import json
from pathlib import Path

from ._diff_parser import PolicyViolationError, validate_path

_ROOT = Path(__file__).resolve().parents[5]
_INVENTORY = _ROOT / "selfimprove/l7_sensitive_paths.json"
# Reserved names are deliberately absent from the on-disk inventory.
_RESERVED = frozenset({
    ".github/CODEOWNERS",
    "features/celina/celina_pkg/selfimprove/protected_hashes.json",
})


def _entries(root: Path, raw: object, *, directories: bool) -> frozenset[str]:
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) for item in raw):
        message = "invalid sensitive path entries"
        raise PolicyViolationError(message)
    if len(set(raw)) != len(raw):
        message = "duplicate sensitive path"
        raise PolicyViolationError(message)
    for name in raw:
        validate_path(name)
        target = root / name
        if any(path.is_symlink() for path in (target, *target.parents) if path != root.parent):
            message = "symlink in sensitive path"
            raise PolicyViolationError(message)
        if not (target.is_dir() if directories else target.is_file()):
            message = "missing sensitive path"
            raise PolicyViolationError(message)
    return frozenset(raw)


def load_sensitive_paths() -> tuple[frozenset[str], frozenset[str]]:
    """Read the independent path manifest; retain reserved future names."""
    if _INVENTORY.is_symlink():
        message = "sensitive manifest is a symlink"
        raise PolicyViolationError(message)
    try:
        data = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        message = "unreadable sensitive path manifest"
        raise PolicyViolationError(message) from exc
    if not isinstance(data, dict) or set(data) != {"files", "directories"}:
        message = "invalid sensitive path manifest"
        raise PolicyViolationError(message)
    files = _entries(_ROOT, data["files"], directories=False)
    directories = _entries(_ROOT, data["directories"], directories=True)
    if "selfimprove/l7_sensitive_paths.json" not in files or files & directories:
        message = "invalid sensitive inventory membership"
        raise PolicyViolationError(message)
    return files | _RESERVED, directories


def is_sensitive(path: str, files: frozenset[str], directories: frozenset[str]) -> bool:
    """Reject protected exact paths and every descendant of a protected directory."""
    return path in files or any(
        path == prefix or path.startswith(prefix + "/") for prefix in directories
    )
