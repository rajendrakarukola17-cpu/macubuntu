# Copyright 2026 Celina contributors.
"""Persistent approval spending across process and failure boundaries."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING

import pytest

from celina_pkg.approval import request_bound_approval
from celina_pkg.selfimprove.wiring import _token_store
from celina_pkg.selfimprove.wiring._token_store import TokenSpendStore

if TYPE_CHECKING:
    from pathlib import Path

_TOOL = "selfimprove_l7_patch"
_ARGS: dict[str, object] = {"proposal_id": "patch1", "bundle_hash": "a" * 64}


def _independent_spend(path: Path) -> bool:
    token, _ = request_bound_approval(_TOOL, _ARGS, lambda _title, _details: True)
    return TokenSpendStore(path).consume(token, _TOOL, _ARGS)


def test_spend_race_and_restart_across_processes(tmp_path: Path) -> None:
    """Independent processes and a later process share one persistent winner."""
    state = tmp_path / "state"
    with ProcessPoolExecutor(max_workers=8) as pool:
        winners = list(pool.map(_independent_spend, [state] * 8))
    assert sum(winners) == 1
    with ProcessPoolExecutor(max_workers=1) as restarted:
        assert not restarted.submit(_independent_spend, state).result()


def test_failed_verification_rolls_back_marker_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verifier refusal must not persist an otherwise valid spend marker."""
    store = TokenSpendStore(tmp_path / "state")
    token, _ = request_bound_approval(_TOOL, _ARGS, lambda _title, _details: True)
    assert token is not None
    real_verify = _token_store.verify_and_consume
    monkeypatch.setattr(_token_store, "verify_and_consume", lambda *_args: (False, "denied"))
    assert not store.consume(token, _TOOL, _ARGS)
    monkeypatch.setattr(_token_store, "verify_and_consume", real_verify)
    assert store.consume(token, _TOOL, _ARGS)
    assert not store.consume(token, _TOOL, _ARGS)


@pytest.mark.parametrize(
    "unsafe", ["directory_mode", "file_mode", "symlink", "hardlink", "corrupt"],
)
def test_unsafe_store_failures_do_not_consume_token(tmp_path: Path, unsafe: str) -> None:
    """Unsafe paths and corrupt databases are denied before consuming the token."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    db = state / "approval-spends.sqlite3"
    if unsafe == "directory_mode":
        state.chmod(0o755)
    elif unsafe == "file_mode":
        db.touch(mode=0o644)
        db.chmod(0o644)
    elif unsafe == "symlink":
        db.symlink_to(tmp_path / "elsewhere")
    elif unsafe == "hardlink":
        original = tmp_path / "original"
        original.touch(mode=0o600)
        db.hardlink_to(original)
    else:
        db.write_bytes(b"not a database")
        db.chmod(0o600)
    token, _ = request_bound_approval(_TOOL, _ARGS, lambda _title, _details: True)
    assert token is not None
    assert not TokenSpendStore(state).consume(token, _TOOL, _ARGS)
    assert not token.consumed
