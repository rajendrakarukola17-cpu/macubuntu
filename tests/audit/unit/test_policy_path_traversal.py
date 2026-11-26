"""
tests/audit/unit/test_policy_path_traversal.py

Phase 3 unit tests for path traversal defense.

Tests:
- celina_pkg.policy.is_protected_path — protected path detection
- celina_pkg.connectors.base._validate_path_segment — path segment validation
- celina_pkg.connectors.base._build_path — safe path joining

Malicious input matrix: ../, absolute paths, symlink-swap, ~, \x00, empty,
overlong strings.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg.connectors.base import BaseConnector, _validate_path_segment

_build_path = BaseConnector._build_path
from celina_pkg.policy import is_protected_path

# ---------------------------------------------------------------------------
# is_protected_path
# ---------------------------------------------------------------------------


def test_is_protected_path_dot_ssh():
    assert is_protected_path("~/.ssh/id_rsa") is True


def test_is_protected_path_dot_gnupg():
    assert is_protected_path("/home/user/.gnupg/secret.key") is True


def test_is_protected_path_dot_netrc():
    assert is_protected_path("~/.netrc") is True


def test_is_protected_path_keyrings():
    assert is_protected_path("/home/user/.local/share/keyrings/login.keyring") is True


def test_is_protected_path_normal_path_false():
    assert is_protected_path("/home/user/Documents/notes.txt") is False
    assert is_protected_path("relative/path/file.txt") is False


def test_is_protected_path_none_raises_typeerror():
    try:
        is_protected_path(None)
        assert False, "Expected TypeError for None"
    except TypeError:
        pass  # expected — None is not a valid path


def test_is_protected_path_empty_is_protected():
    # Empty path fails closed — treated as protected
    assert is_protected_path("") is True


# ---------------------------------------------------------------------------
# _validate_path_segment
# ---------------------------------------------------------------------------


def test_validate_path_segment_dot_raises():
    try:
        _validate_path_segment(".")
        assert False, "Expected ValueError for '.'"
    except ValueError:
        pass


def test_validate_path_segment_dotdot_raises():
    try:
        _validate_path_segment("..")
        assert False, "Expected ValueError for '..'"
    except ValueError:
        pass


def test_validate_path_segment_dotdot_slash_raises():
    try:
        _validate_path_segment("../user/keys")
        assert False, "Expected ValueError for '../user/keys'"
    except ValueError:
        pass


def test_validate_path_segment_null_byte_raises():
    try:
        _validate_path_segment("a\x00b")
        assert False, "Expected ValueError for null byte"
    except ValueError:
        pass


def test_validate_path_segment_empty_raises():
    try:
        _validate_path_segment("")
        assert False, "Expected ValueError for empty string"
    except ValueError:
        pass


def test_validate_path_segment_backslash_raises():
    try:
        _validate_path_segment("a\\b")
        assert False, "Expected ValueError for backslash"
    except ValueError:
        pass


def test_validate_path_segment_overlong_raises():
    try:
        _validate_path_segment("a" * 5000)
        assert False, "Expected ValueError for overlong string"
    except ValueError:
        pass


def test_validate_path_segment_normal_ok():
    assert _validate_path_segment("Documents") == "Documents"
    assert _validate_path_segment("my-file.txt") == "my-file.txt"
    assert _validate_path_segment("abc123") == "abc123"


# ---------------------------------------------------------------------------
# _build_path
# ---------------------------------------------------------------------------


def test_build_path_traversal_blocked():
    try:
        _build_path("api", "..", "etc", "shadow")
        assert False, "Expected ValueError for path traversal"
    except ValueError:
        pass


def test_build_path_normal_ok():
    result = _build_path("api", "v1", "messages", "123")
    assert result == "/api/v1/messages/123"


def test_build_path_absolute_segment_blocked():
    try:
        _build_path("api", "/etc/shadow")
        assert False, "Expected ValueError for absolute segment"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Malicious input matrix (applied to _validate_path_segment and _build_path)
# ---------------------------------------------------------------------------


MALICIOUS_INPUTS = [
    "..",
    "../user/keys",
    "a\x00b",
    "a/b",
    "a\\b",
    "",
    "a" * 10000,
    "/etc/shadow",
    "~/.ssh/id_rsa",
    "....//....//",
    ".ssh/authorized_keys",
]


def test_malicious_inputs_segment_all_blocked():
    """Every malicious input must be rejected by _validate_path_segment."""
    for bad in MALICIOUS_INPUTS:
        try:
            _validate_path_segment(bad)
            assert False, f"_validate_path_segment({bad!r}) did not raise"
        except ValueError:
            pass  # expected


def test_malicious_inputs_build_path_blocked():
    """Every malicious input must be rejected by _build_path."""
    for bad in MALICIOUS_INPUTS:
        try:
            _build_path("base", bad)
            assert False, f"_build_path('base', {bad!r}) did not raise"
        except ValueError:
            pass  # expected


# ---------------------------------------------------------------------------
# Zero import-time $HOME writes (standing invariant)
# ---------------------------------------------------------------------------


def test_no_home_write_on_import(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "nonexistent_home"))
    import celina_pkg.policy as p  # noqa: F401
    import celina_pkg.connectors.base as b  # noqa: F401
    assert not (tmp_path / "nonexistent_home").exists()
