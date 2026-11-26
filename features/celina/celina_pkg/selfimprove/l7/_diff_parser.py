# Copyright 2026 Celina contributors.
"""Parse a conservative, unambiguous subset of Git textual unified diffs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NoReturn


class PolicyViolationError(ValueError):
    """A proposal violates policy or cannot be parsed unambiguously."""


@dataclass(frozen=True, slots=True)
class DiffStats:
    """Canonical paths and total content changes."""

    paths: tuple[str, ...]
    changed_lines: int


_HEADER_PARTS = 4
_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
_HUNK = re.compile(r"@@ -(\d{1,9})(?:,(\d{1,9}))? \+(\d{1,9})(?:,(\d{1,9}))? @@(?: .*)?\Z")
_INDEX = re.compile(r"index [0-9a-f]+\.\.[0-9a-f]+(?: 100(?:644|755))?\Z")
_MODE = re.compile(r"(?:old mode|new mode|new file mode|deleted file mode) 100(?:644|755)\Z")
_SIMILARITY = re.compile(r"(?:dis)?similarity index (?:100|[0-9]{1,2})%\Z")


def _reject(reason: str) -> NoReturn:
    raise PolicyViolationError(reason)


def validate_path(raw: str, prefix: str = "") -> str:
    if prefix:
        if not raw.startswith(prefix):
            _reject("wrong diff path prefix")
        raw = raw[len(prefix) :]
    if not _PATH.fullmatch(raw) or any(
        part in {".", ".."} or part.casefold() == ".git" for part in raw.split("/")
    ):
        _reject("unsafe or ambiguous path")
    return raw


def _hunks(lines: list[str], offset: int) -> int:
    changed = 0
    while offset < len(lines):
        match = _HUNK.fullmatch(lines[offset])
        if match is None:
            _reject("invalid hunk header")
        old, new = int(match.group(2) or "1"), int(match.group(4) or "1")
        offset += 1
        while old or new:
            if offset >= len(lines) or not lines[offset]:
                _reject("truncated hunk")
            line = lines[offset]
            if line.startswith(" "):
                old, new = old - 1, new - 1
            elif line.startswith("-"):
                old, changed = old - 1, changed + 1
            elif line.startswith("+"):
                new, changed = new - 1, changed + 1
            else:
                _reject("invalid hunk line")
            if old < 0 or new < 0:
                _reject("hunk line count mismatch")
            offset += 1
            if offset < len(lines) and lines[offset] == r"\ No newline at end of file":
                offset += 1
    return changed


@dataclass(slots=True)
class _Metadata:
    old: str
    new: str
    old_marker: str | None = None
    new_marker: str | None = None
    movement: set[str] = field(default_factory=set)
    mode_change: bool = False

    def consume(self, line: str) -> None:
        if line.startswith("--- "):
            if self.old_marker is not None:
                _reject("duplicate file header")
            self.old_marker = line[4:]
        elif line.startswith("+++ "):
            if self.new_marker is not None:
                _reject("duplicate file header")
            self.new_marker = line[4:]
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            self._movement(line)
        elif _MODE.fullmatch(line):
            self.mode_change = True
        elif not (_INDEX.fullmatch(line) or _SIMILARITY.fullmatch(line)):
            _reject("unsupported diff metadata")

    def _movement(self, line: str) -> None:
        name, raw = line.rsplit(" ", 1)
        if name in self.movement or validate_path(raw) != (
            self.old if name.endswith("from") else self.new
        ):
            _reject("duplicate or mismatched rename/copy")
        self.movement.add(name)

    def finish(self, *, has_hunk: bool) -> bool:
        rename = {"rename from", "rename to"}
        copy = {"copy from", "copy to"}
        if self.movement not in (set(), rename, copy):
            _reject("inconsistent rename or copy")
        if self.old != self.new and not self.movement:
            _reject("path change without rename or copy")
        if (self.old_marker is None) != (self.new_marker is None):
            _reject("missing file header")
        self._check_markers()
        if has_hunk and self.old_marker is None:
            _reject("hunk without file headers")
        return self.mode_change or bool(self.movement)

    def _check_markers(self) -> None:
        old, new = self.old_marker, self.new_marker
        if old is None or new is None:
            return
        if (
            (old != "/dev/null" and validate_path(old, "a/") != self.old)
            or (new != "/dev/null" and validate_path(new, "b/") != self.new)
            or old == new == "/dev/null"
        ):
            _reject("file header path mismatch")


def _metadata(lines: list[str], old: str, new: str) -> tuple[int, bool]:
    state = _Metadata(old, new)
    offset = 1
    while offset < len(lines) and not lines[offset].startswith("@@ "):
        state.consume(lines[offset])
        offset += 1
    return offset, state.finish(has_hunk=offset < len(lines))


def _section(lines: list[str]) -> tuple[set[str], int]:
    parts = lines[0].split(" ")
    if len(parts) != _HEADER_PARTS or parts[:2] != ["diff", "--git"]:
        _reject("invalid diff header")
    old, new = validate_path(parts[2], "a/"), validate_path(parts[3], "b/")
    offset, metadata_change = _metadata(lines, old, new)
    count = _hunks(lines, offset)
    if not count and not metadata_change:
        _reject("empty patch section")
    return {old, new}, count


def parse_diff(text: str) -> DiffStats:
    """Parse all Git sections, reject ambiguity, and count added/removed lines."""
    if "\x00" in text or "\r" in text:
        _reject("NUL or CR in diff")
    lines = text.splitlines()
    sections: list[list[str]] = []
    for line in lines:
        if line.startswith("diff --git "):
            sections.append([line])
        elif not sections:
            _reject("diff lacks Git header")
        else:
            sections[-1].append(line)
    if not sections:
        _reject("diff has no target")
    paths: set[str] = set()
    changed = 0
    for section in sections:
        section_paths, count = _section(section)
        paths.update(section_paths)
        changed += count
    return DiffStats(tuple(sorted(paths)), changed)
