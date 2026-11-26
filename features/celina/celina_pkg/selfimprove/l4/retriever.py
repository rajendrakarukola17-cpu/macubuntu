# Copyright 2026 Celina contributors.
"""Read-only, bounded retrieval; returned context is untrusted reference data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .redactor import frame_untrusted_context
from .store import FailureFixStore, MemoryEpisode

if TYPE_CHECKING:
    from pathlib import Path

    from celina_pkg.selfimprove.flags import SelfImproveFlags

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_ITEMS = 5
_CANDIDATES = 1000


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    """An immutable stored episode and deterministic relevance score."""

    episode: MemoryEpisode
    score: int


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Selected episodes and a separately bounded untrusted reference frame."""

    items: tuple[RetrievedMemory, ...]
    context: str


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(text.casefold()))


def _score(
    episode: MemoryEpisode, query: frozenset[str], category: str | None, verifier: str | None,
) -> int:
    return (
        4 * int(category is not None and episode.category.casefold() == category.casefold())
        + 3 * int(verifier is not None and episode.verifier_name.casefold() == verifier.casefold())
        + 2 * len(query & _tokens(episode.error_signature))
    )


def _reference(item: RetrievedMemory) -> str:
    episode = item.episode
    return (
        f"ID: {episode.episode_id}\nCategory: {episode.category}\n"
        f"Verifier: {episode.verifier_name}\n"
        f"Error: {episode.error_signature}\nFailure: {episode.failure}\nFix: {episode.fix}"
    )


class MemoryRetriever:
    """Rank at most 1000 newest candidates; never open the database for writing."""

    def __init__(self, db_path: Path, flags: SelfImproveFlags) -> None:
        """Bind the explicit path and flags without filesystem access."""
        self.flags = flags
        self._store = FailureFixStore(db_path, flags)

    def retrieve(
        self,
        query: str = "",
        *,
        category: str | None = None,
        verifier_name: str | None = None,
        limit: int = 5,
        max_chars: int = 2000,
    ) -> RetrievalResult:
        """Select up to five references by relevance, recency, then identity."""
        if not self.flags.enabled or not self.flags.memory:
            return RetrievalResult((), "")
        if type(limit) is not int or not 1 <= limit <= _MAX_ITEMS:
            message = "limit must be between one and five"
            raise ValueError(message)
        if not isinstance(query, str) or any(
            value is not None and not isinstance(value, str) for value in (category, verifier_name)
        ):
            message = "query, category and verifier must be strings"
            raise ValueError(message)
        frame_untrusted_context((), max_chars=max_chars)
        tokens = _tokens(query)
        candidates = self._store.query(limit=_CANDIDATES)
        ranked = [
            RetrievedMemory(ep, _score(ep, tokens, category, verifier_name)) for ep in candidates
        ]
        if tokens or category or verifier_name:
            ranked = [item for item in ranked if item.score > 0]
        ranked.sort(
            key=lambda item: (-item.score, -item.episode.created_at, item.episode.episode_id),
        )
        selected = tuple(ranked[:limit])
        return RetrievalResult(
            selected,
            frame_untrusted_context(
                tuple(_reference(item) for item in selected),
                max_chars=max_chars,
            ),
        )
