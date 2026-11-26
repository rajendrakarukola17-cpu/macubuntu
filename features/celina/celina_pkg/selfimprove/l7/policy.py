# Copyright 2026 Celina contributors.
"""Protected-core and strict textual diff policy, checked before Git operations."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from ._diff_parser import PolicyViolationError, parse_diff, validate_path
from ._sensitive_paths import is_sensitive, load_sensitive_paths
from .types import PatchPolicyConfig, PatchProposal, PolicyCheckResult

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ("PolicyViolationError", "validate_patch_proposal")

def _manifest(path: Path) -> frozenset[str]:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or any(p.is_symlink() for p in (path, *path.parents))
    ):
        message = "unsafe protected manifest path"
        raise PolicyViolationError(message)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        message = "unreadable protected manifest"
        raise PolicyViolationError(message) from exc
    if (
        not isinstance(data, dict)
        or not data
        or any(
            not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
            for k, v in data.items()
        )
    ):
        message = "invalid protected manifest"
        raise PolicyViolationError(message)
    for name in data:
        validate_path(name)
    return frozenset(data)


def validate_patch_proposal(
    proposal: PatchProposal,
    manifest_path: Path,
    config: PatchPolicyConfig,
) -> PolicyCheckResult:
    """Reject protected, malformed, unsupported and excessive patch proposals."""
    if not isinstance(proposal, PatchProposal) or not isinstance(config, PatchPolicyConfig):
        message = "invalid policy arguments"
        raise PolicyViolationError(message)
    if len(proposal.unified_diff.encode("utf-8")) > config.max_diff_bytes:
        message = "diff exceeds byte limit"
        raise PolicyViolationError(message)
    protected = _manifest(manifest_path)
    files, directories = load_sensitive_paths()
    stats = parse_diff(proposal.unified_diff)
    if len(stats.paths) > config.max_files or stats.changed_lines > config.max_changed_lines:
        message = "diff exceeds policy bounds"
        raise PolicyViolationError(message)
    if any(path in protected or is_sensitive(path, files, directories) for path in stats.paths):
        message = "protected path"
        raise PolicyViolationError(message)
    return PolicyCheckResult(
        allowed=True,
        touched_files=stats.paths,
        changed_lines=stats.changed_lines,
    )
