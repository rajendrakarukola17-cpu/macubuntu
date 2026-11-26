# Copyright 2026 Celina contributors.
"""Manifest loading rejects duplicate object keys and ambiguous tool identifiers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from celina_pkg import capabilities

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("raw", [
    '{"tools":{"read":{},"read":{}}}',
    '{"tools":{"read":{},"re\\u0061d":{}}}',
    '{"tools":{"read":{"tier":"tier1","tier":"tier2"}}}',
    '{"tools":{"Read":{},"read":{}}}',
    '{"tools":{"\\uff26oo":{},"Foo":{}}}',
])
def test_duplicate_and_equivalent_tool_ids_fail_closed(tmp_path: Path, raw: str) -> None:
    """Duplicate decoded keys and normalized tool-identifier collisions are rejected."""
    path = tmp_path / "manifest.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        capabilities.load_manifest(path)


def test_valid_case_distinct_non_identifier_keys_are_preserved(tmp_path: Path) -> None:
    """Case-sensitive metadata keys are not automatically treated as tool IDs."""
    path = tmp_path / "manifest.json"
    path.write_text('{"metadata":{"Name":1,"name":2},"tools":{"read":{}}}')
    assert capabilities.load_manifest(path)["metadata"] == {"Name": 1, "name": 2}


def test_rejected_manifest_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed load must not publish a duplicate-bearing manifest into the cache."""
    path = tmp_path / "manifest.json"
    path.write_text('{"tools":{"read":{},"read":{}}}')
    monkeypatch.setattr(capabilities, "locate_manifest_path", lambda: path)
    monkeypatch.setattr(capabilities, "_MANIFEST_CACHE", None)
    with pytest.raises(ValueError, match="duplicate"):
        capabilities.load_manifest()
    assert capabilities._MANIFEST_CACHE is None  # noqa: SLF001  # Inspect failed cache publication.
