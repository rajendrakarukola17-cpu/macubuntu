"""
Regression tests for features/celina/celina_pkg/policy.py (audit item 4).

Single source of truth for path-protection and risk-band policy consumed by
BlastRadiusEstimator, LandlockPolicy, and the planned WorldModel.  These
tests assert the engines agree on a canonical path corpus so they cannot
drift apart.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from celina_pkg import policy
from celina_pkg.landlock_policy import PROTECTED_SENSITIVE_PATHS as LL_PROTECTED
from celina_pkg.planner.blast_radius import BlastRadiusEstimator, _score_band


# ------------------------------------------------------------------
# Canonical path corpus consistency
# ------------------------------------------------------------------

CANONICAL_PROTECTED = [
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.kube",
    "~/.netrc",
    "~/.git-credentials",
    "~/.local/share/keyrings",
    "~/.config/celina/env",
    "~/.config/celina/backups",
    "~/Library/Application Support/Google/Chrome/Login Data",
]


@pytest.mark.parametrize("path_str", CANONICAL_PROTECTED)
def test_is_protected_path_catches_canonical(path_str):
    """Every canonical protected path must be flagged by policy.is_protected_path."""
    assert policy.is_protected_path(path_str) is True, f"not protected: {path_str}"


def test_is_protected_path_rejects_normal_paths():
    """Ordinary user paths must NOT be flagged as protected."""
    normal = [
        "~/Documents/notes.txt",
        "~/Desktop/project.py",
        "~/Downloads/archive.zip",
        "~/Pictures/vacation.jpg",
        "~/Videos/movie.mp4",
        "~/Projects/celina/main.py",
        "~/.local/share/celina/voices/voice-00",
    ]
    for path_str in normal:
        assert policy.is_protected_path(path_str) is False, f"false positive: {path_str}"


def test_sensitive_filename_detection():
    """Exact sensitive filenames must be detected regardless of directory."""
    for name in ("id_rsa", "id_ed25519", ".netrc", ".git-credentials", "borg.env", "Login Data", "cookies.sqlite"):
        assert policy.is_sensitive_filename(name) is True, f"not sensitive: {name}"
    assert policy.is_sensitive_filename("notes.txt") is False
    assert policy.is_sensitive_filename("report.md") is False


# ------------------------------------------------------------------
# Cross-engine consistency: landlock <-> policy
# ------------------------------------------------------------------

def test_landlock_protected_matches_policy():
    """landlock_policy.PROTECTED_SENSITIVE_PATHS must equal policy.PROTECTED_PATH_COMPONENTS."""
    ll_set = set(LL_PROTECTED)
    pol_set = set(policy.PROTECTED_PATH_COMPONENTS)
    assert ll_set == pol_set, f"drift: landlock={ll_set}, policy={pol_set}"


def test_landlock_is_sensitive_delegates_to_policy(tmp_path):
    """landlock._is_sensitive_path must agree with policy.is_protected_path."""
    from celina_pkg.landlock_policy import _is_sensitive_path

    sensitive = ["~/.ssh/id_rsa", "~/.netrc", "~/.aws/credentials"]
    normal = ["~/Documents/notes.txt", "~/Desktop/project.py"]

    for s in sensitive:
        assert _is_sensitive_path(s) is True, f"landlock missed: {s}"
        assert policy.is_protected_path(s) is True, f"policy missed: {s}"

    for n in normal:
        assert _is_sensitive_path(n) is False, f"landlock false positive: {n}"
        assert policy.is_protected_path(n) is False, f"policy false positive: {n}"


# ------------------------------------------------------------------
# Cross-engine consistency: blast_radius <-> policy risk bands
# ------------------------------------------------------------------

def test_blast_radius_score_band_matches_policy():
    """blast_radius._score_band must agree with policy.score_to_band."""
    est = BlastRadiusEstimator()
    for score in (0, 10, 25, 26, 40, 50, 51, 60, 75, 76, 90, 100):
        assert _score_band(score) == policy.score_to_band(score), f"drift at score {score}"


def test_blast_radius_band_allows_autoexec_matches_policy():
    """is_safe_to_autoexec must agree with policy.band_allows_autoexec."""
    est = BlastRadiusEstimator()

    # low band -> autoexec allowed
    low = est.assess(action="read", target="~/Documents/notes.txt")
    assert low.band == "low"
    assert est.is_safe_to_autoexec(assessment=low) is True
    assert policy.band_allows_autoexec("low") is True

    # high band -> autoexec not allowed
    high = est.assess(action="rm -rf /tmp/foo", target="/tmp/foo")
    assert high.band in ("high", "critical")
    assert est.is_safe_to_autoexec(assessment=high) is False
    assert policy.band_allows_autoexec(high.band) is False


# ------------------------------------------------------------------
# Policy band definitions
# ------------------------------------------------------------------

def test_risk_bands_are_monotonic_and_complete():
    """RISK_BANDS must cover 0-100 with no gaps and auto_exec decreasing."""
    bands = policy.RISK_BANDS
    assert bands[0].min_score == 0
    assert bands[-1].max_score == 100
    for i in range(len(bands) - 1):
        assert bands[i].max_score + 1 == bands[i + 1].min_score, "gap in risk bands"
        assert bands[i].auto_exec_allowed >= bands[i + 1].auto_exec_allowed, "autoexec must not increase with risk"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
