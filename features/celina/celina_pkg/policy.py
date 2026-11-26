"""Celina OS Policy Engine (`policy.py`) — audit item 4.

Single source of truth for all path-protection, risk-band, and
never-exposed-path policy consumed by:

  - BlastRadiusEstimator (planner/blast_radius.py)
  - LandlockPolicy (landlock_policy.py)
  - WorldModel / self-model (planned)

Prevents drift between three divergent policy engines by giving them
one canonical path corpus and one risk-classification function.

Zero third-party dependencies (pure Python 3.13 stdlib).
Zero $HOME filesystem mutations at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import config

# ------------------------------------------------------------------
# Canonical path corpus (never-exposed / protected paths)
#
# Consolidated from:
#   - landlock_policy.PROTECTED_SENSITIVE_PATHS
#   - tools._SENSITIVE_PARTS / _SENSITIVE_FILENAMES
#   - config.PROTECTED_PATHS / config.HARD_BLOCKED_PATHS
# ------------------------------------------------------------------

# Path components that, if present anywhere in a path, mark it as protected.
PROTECTED_PATH_COMPONENTS: frozenset[str] = frozenset({
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".netrc",
    ".git-credentials",
    "keyrings",
    ".ai-env",
})

# Exact filenames that are sensitive regardless of directory.
SENSITIVE_FILENAMES: frozenset[str] = frozenset({
    ".ai-env",
    "borg.env",
    "env",
    "id_rsa",
    "id_ed25519",
    ".netrc",
    ".git-credentials",
    "Login Data",
    "cookies.sqlite",
})

# Sensitive directory names (match on directory component).
SENSITIVE_DIR_COMPONENTS: frozenset[str] = frozenset({
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    "keyrings",
    ".config/celina",   # matched as a two-part prefix
})


def is_protected_path(path: str | Path) -> bool:
    """Return True if `path` touches any never-exposed protected path.

    Checks path components against PROTECTED_PATH_COMPONENTS and the
    final component against SENSITIVE_FILENAMES.  Also consults
    config.PROTECTED_PATHS / config.HARD_BLOCKED_PATHS for
    deployment-specific additions.
    """
    resolved = Path(path).expanduser()
    parts = set(resolved.parts)

    # Component match
    if parts & PROTECTED_PATH_COMPONENTS:
        return True

    # Filename match
    if resolved.name in SENSITIVE_FILENAMES:
        return True

    # Two-part prefix match (e.g. ~/.config/celina/env)
    try:
        rel = resolved.relative_to(Path.home())
    except ValueError:
        rel = resolved
    rel_parts = rel.parts
    for i in range(len(rel_parts) - 1):
        two_part = f"{rel_parts[i]}/{rel_parts[i + 1]}"
        if two_part in SENSITIVE_DIR_COMPONENTS:
            return True

    # Config-driven protected / hard-blocked paths
    for blocked in config.PROTECTED_PATHS:
        blocked_p = Path(blocked).expanduser()
        try:
            resolved.relative_to(blocked_p)
            return True
        except ValueError:
            pass
        try:
            blocked_p.relative_to(resolved)
            return True
        except ValueError:
            pass

    return False


def is_sensitive_filename(name: str) -> bool:
    """Return True if `name` is a sensitive filename (exact match)."""
    return name in SENSITIVE_FILENAMES


# ------------------------------------------------------------------
# Risk bands
# ------------------------------------------------------------------

@dataclass(frozen=True)
class RiskBand:
    name: str
    min_score: int
    max_score: int
    auto_exec_allowed: bool


RISK_BANDS: tuple[RiskBand, ...] = (
    RiskBand("low", 0, 25, True),
    RiskBand("medium", 26, 50, False),
    RiskBand("high", 51, 75, False),
    RiskBand("critical", 76, 100, False),
)


def score_to_band(score: int) -> str:
    """Convert a numeric risk score (0-100) to a band name."""
    score = max(0, min(100, int(score)))
    for band in RISK_BANDS:
        if band.min_score <= score <= band.max_score:
            return band.name
    return "critical"


def band_allows_autoexec(band: str) -> bool:
    """Return True if a tool in the given risk band may auto-execute."""
    for band_def in RISK_BANDS:
        if band_def.name == band:
            return band_def.auto_exec_allowed
    return False


# ------------------------------------------------------------------
# Canonical path corpus for consistency testing
# ------------------------------------------------------------------

CANONICAL_PROTECTED_PATHS: tuple[str, ...] = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.kube",
    "~/.netrc",
    "~/.git-credentials",
    "~/.local/share/keyrings",
    "~/.config/celina/env",
    "~/Library/Application Support/.../Login Data",   # macOS Chrome/Edge
    "~/.../Login Data",                                # generic Chrome/Edge
)

__all__ = [
    "CANONICAL_PROTECTED_PATHS",
    "PROTECTED_PATH_COMPONENTS",
    "RISK_BANDS",
    "SENSITIVE_DIR_COMPONENTS",
    "SENSITIVE_FILENAMES",
    "RiskBand",
    "band_allows_autoexec",
    "is_protected_path",
    "is_sensitive_filename",
    "score_to_band",
]
