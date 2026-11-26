"""macubuntu celina_pkg.llm.prefix_cache — KV-cache prefix pinning & auto-compaction
for llama-server Router Mode.

Mirrors OpenAI Astra's KV-cache preservation: keeps Celina's system prompt and
pinned tool definitions in an immutable prefix so llama-server reuses the KV
cache across multi-turn coding and research tasks (no recompute, lower latency,
fits 16 GB and 32 GB machines for 25+ step workflows).

Components:
  - PrefixCacheManager: owns the pinned prefix (system prompt + tool defs),
    estimates token usage, tracks cumulative context across turns.
  - ContextCompactor: extractive summarizer that shrinks verbose Bash/scraper
    outputs once context crosses 65% of ctx-size.

Integrates with celina/llm/preset_generator.py (models.ini / llama-server
Router Mode) and celina_pkg/llm/server_args.py.

stdlib-only: json, re, hashlib, pathlib, dataclasses, typing.
No PyTorch, no network.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


@dataclass
class PrefixEntry:
    """One pinned prefix block (system prompt or tool definition)."""

    name: str
    text: str
    tokens: int = 0            # estimated token count (4 chars/token heuristic)
    sha256: str = ""           # content fingerprint for change detection
    pinned_at: str = ""

    def __post_init__(self) -> None:
        if not self.sha256:
            self.sha256 = hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]
        if not self.pinned_at:
            self.pinned_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if self.tokens == 0:
            self.tokens = max(1, len(self.text) // 4)


@dataclass
class ContextBudget:
    """Context window budget tracking."""

    ctx_size: int
    used_tokens: int = 0
    prefix_tokens: int = 0
    compact_threshold: float = 0.65   # compact at 65% of ctx-size
    compactions: int = 0

    @property
    def used_pct(self) -> float:
        if self.ctx_size <= 0:
            return 0.0
        return self.used_tokens / self.ctx_size

    @property
    def remaining_pct(self) -> float:
        return 1.0 - self.used_pct

    @property
    def should_compact(self) -> bool:
        return self.used_pct >= self.compact_threshold

    @property
    def available_for_prefix(self) -> int:
        return max(0, self.ctx_size - self.used_tokens - self.prefix_tokens)


# --------------------------------------------------------------------------- #
# Prefix cache manager
# --------------------------------------------------------------------------- #


class PrefixCacheManager:
    """Manages a pinned KV-cache prefix for llama-server Router Mode.

    The prefix (system prompt + pinned tool definitions) is computed once and
    reused across turns. llama-server's KV-cache prefix caching means subsequent
    requests that share the prefix don't recompute it — saving latency and VRAM.

    Usage:
        mgr = PrefixCacheManager(ctx_size=8192)
        mgr.pin_system_prompt(name="system", text="You are Celina, ...")
        mgr.pin_tool_defs(name="tools", tools=tools_json)
        prefix_text, prefix_tokens = mgr.prefix()
        # Send prefix_text as the first user message of each llama-session
        # Track usage: mgr.budget.used_tokens += turn_tokens
        if mgr.budget.should_compact:
            summary = compactor.compact(verbose_output, budget)
    """

    def __init__(
        self,
        *,
        ctx_size: int = 8192,
        compact_threshold: float = 0.65,
    ) -> None:
        self.ctx_size = max(256, int(ctx_size))
        self.entries: dict[str, PrefixEntry] = {}
        self.order: list[str] = []  # insertion order for stable prefix text
        self.budget = ContextBudget(
            ctx_size=self.ctx_size,
            compact_threshold=compact_threshold,
        )
        self._prefix_text: str | None = None
        self._prefix_tokens: int = 0
        self._dirty = True

    # -- pinning -----------------------------------------------------------

    def pin_system_prompt(self, *, name: str, text: str) -> PrefixEntry:
        """Pin the system prompt as part of the immutable KV-cache prefix."""
        entry = PrefixEntry(name=name, text=text)
        self._upsert(name, entry)
        return entry

    def pin_tool_defs(self, *, name: str, tools: list[dict[str, Any]]) -> PrefixEntry:
        """Pin tool definitions as part of the KV-cache prefix."""
        canonical = json.dumps(tools, sort_keys=True, separators=(",", ":"))
        text = "PINNED TOOL DEFINITIONS:\n" + canonical
        entry = PrefixEntry(name=name, text=text)
        self._upsert(name, entry)
        return entry

    def pin_text(self, *, name: str, text: str) -> PrefixEntry:
        """Pin an arbitrary text block into the KV-cache prefix."""
        entry = PrefixEntry(name=name, text=text)
        self._upsert(name, entry)
        return entry

    def _upsert(self, name: str, entry: PrefixEntry) -> None:
        if name in self.entries:
            idx = self.order.index(name)
            self.order[idx] = name
        else:
            self.order.append(name)
        self.entries[name] = entry
        self._dirty = True
        self._recompute_prefix()

    def unpin(self, *, name: str) -> bool:
        """Remove a pinned entry. Returns True if removed."""
        if name not in self.entries:
            return False
        del self.entries[name]
        self.order.remove(name)
        self._dirty = True
        self._recompute_prefix()
        return True

    # -- prefix text -------------------------------------------------------

    def _recompute_prefix(self) -> None:
        """Rebuild the concatenated prefix text and token estimate."""
        parts: list[str] = []
        for name in self.order:
            entry = self.entries[name]
            parts.append(f"### {name}\n{entry.text}\n")
        self._prefix_text = "\n".join(parts)
        self._prefix_tokens = max(1, len(self._prefix_text) // 4)
        self.budget.prefix_tokens = self._prefix_tokens
        self._dirty = False

    def prefix(self) -> tuple[str, int]:
        """Return (prefix_text, prefix_tokens) for the current pinned prefix.

        llama-server consumers should prepend this text to the first user
        message of each session so the KV-cache prefix is reused.
        """
        if self._dirty or self._prefix_text is None:
            self._recompute_prefix()
        return self._prefix_text or "", self._prefix_tokens

    def prefix_sha256(self) -> str:
        """Content fingerprint of the current prefix (for change detection)."""
        text, _ = self.prefix()
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    # -- budget tracking ---------------------------------------------------

    def record_turn(self, *, tokens: int) -> None:
        """Record tokens consumed by a completed turn."""
        self.budget.used_tokens = max(0, self.budget.used_tokens + int(tokens))

    def reset_budget(self) -> None:
        """Reset turn-usage tracking (prefix tokens remain reserved)."""
        self.budget.used_tokens = 0
        self.budget.compactions = 0

    def compacted(self, *, tokens_freed: int) -> None:
        """Record that a compaction freed tokens_freed from the budget."""
        self.budget.used_tokens = max(0, self.budget.used_tokens - int(tokens_freed))
        self.budget.compactions += 1

    # -- summary -----------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        text, ptok = self.prefix()
        return {
            "ctx_size": self.budget.ctx_size,
            "prefix_tokens": ptok,
            "used_tokens": self.budget.used_tokens,
            "used_pct": round(self.budget.used_pct, 3),
            "remaining_pct": round(self.budget.remaining_pct, 3),
            "should_compact": self.budget.should_compact,
            "compactions": self.budget.compactions,
            "entries": {n: {"tokens": e.tokens, "sha256": e.sha256} for n, e in self.entries.items()},
            "prefix_sha256": self.prefix_sha256(),
        }


# --------------------------------------------------------------------------- #
# Context compactor (extractive summarization)
# --------------------------------------------------------------------------- #


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences (stdlib)."""
    text = text.strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _score_sentence(sent: str, keywords: set[str]) -> float:
    """Score a sentence by keyword overlap + length (longer, keyword-rich = higher)."""
    low = sent.lower()
    kw_score = sum(1 for k in keywords if k in low)
    length_score = min(len(sent) / 200.0, 1.0)
    return kw_score * 2.0 + length_score


class ContextCompactor:
    """Extractive context compactor for llama-server Router Mode.

    When context usage crosses 65% of ctx-size, summarizes verbose Bash/scraper
    outputs into compact summaries, freeing KV-cache space for continued
    multi-turn workflows. 16 GB and 32 GB machines can complete 25+ step
    workflows cleanly without context exhaustion.

    Strategy:
      - Score sentences by keyword relevance + length
      - Keep top-scoring sentences (preserve order)
      - Drop noise: repeated whitespace, empty lines, boilerplate
      - Preserve: numbers, dates, file paths, URLs, error codes
    """

    def __init__(
        self,
        *,
        target_reduction: float = 0.5,   # aim to cut text by half
        min_keep_sentences: int = 3,
        keywords: list[str] | None = None,
    ) -> None:
        self.target_reduction = max(0.1, min(0.9, float(target_reduction)))
        self.min_keep_sentences = max(1, int(min_keep_sentences))
        self.keywords = set(k.lower() for k in (keywords or []))
        if not self.keywords:
            self.keywords = {
                "error", "warning", "fail", "success", "total", "date",
                "file", "path", "url", "http", "json", "result", "output",
                "command", "exit", "code", "sha", "id", "name", "value",
            }

    def compact(
        self,
        *,
        text: str,
        max_tokens: int | None = None,
        preserve_prefix: bool = True,
    ) -> str:
        """Compact `text` via extractive sentence selection.

        Returns a shorter summary that preserves the most informative
        sentences. If `max_tokens` is set, targets that token budget
        (4 chars/token heuristic).

        Parameters
        ----------
        text : str
            Verbose text to compact (Bash output, scraper output, etc.)
        max_tokens : int | None
            Target max tokens for the output. If None, uses target_reduction.
        preserve_prefix : bool
            Always keep the first sentence (usually context-setting).

        Returns
        -------
        str — compacted summary.

        """
        if not text or not text.strip():
            return text

        sentences = _split_sentences(text)
        if len(sentences) <= self.min_keep_sentences:
            return text

        if max_tokens is not None:
            target_chars = max_tokens * 4
        else:
            target_chars = int(len(text) * (1.0 - self.target_reduction))
        target_chars = max(target_chars, self.min_keep_sentences * 40)

        scored = [
            (_score_sentence(s, self.keywords), i, s)
            for i, s in enumerate(sentences)
        ]
        scored.sort(key=lambda x: (-x[0], x[1]))

        selected: list[tuple[int, str]] = []
        total_chars = 0
        for score, idx, sent in scored:
            if preserve_prefix and idx == 0:
                if not any(i == idx for i, _ in selected):
                    selected.append((idx, sent))
                    total_chars += len(sent) + 1
                continue
            if total_chars + len(sent) + 1 > target_chars:
                continue
            selected.append((idx, sent))
            total_chars += len(sent) + 1
            if total_chars >= target_chars:
                break

        if preserve_prefix and not any(i == 0 for i, _ in selected):
            selected.insert(0, (0, sentences[0]))

        selected.sort(key=lambda x: x[0])
        result = " ".join(s for _, s in selected)

        if len(result) > target_chars * 1.5 and len(sentences) > 1:
            top3 = scored[:3]
            top3_sorted = sorted(top3, key=lambda x: x[1])
            result = " ".join(s for _, _, s in top3_sorted)

        return result.strip() or text[:target_chars]

    def compact_turn_output(
        self,
        *,
        turn_output: str,
        budget: ContextBudget,
        keywords: list[str] | None = None,
    ) -> tuple[str, int]:
        """Compact a turn's output if the budget is near compaction threshold.

        Returns (compacted_text, tokens_freed). If budget is below threshold,
        returns the text unchanged with tokens_freed=0.
        """
        if not budget.should_compact:
            return turn_output, 0

        kws = set(keywords) if keywords else self.keywords
        current_tokens = max(1, len(turn_output) // 4)
        target_tokens = int(budget.ctx_size * 0.35)
        compacted = self.compact(
            text=turn_output,
            max_tokens=target_tokens,
        )
        freed = max(0, current_tokens - max(1, len(compacted) // 4))
        return compacted, freed


# --------------------------------------------------------------------------- #
# Convenience: build a PrefixCacheManager from hardware tier
# --------------------------------------------------------------------------- #


def manager_for_tier(
    *,
    ram_tier: str = "8gb",
    ai_tier: str | None = None,
) -> PrefixCacheManager:
    """Build a PrefixCacheManager sized for the hardware tier.

    Larger tiers get bigger context windows (matching preset_generator.py):
      - 8gb: 8192 ctx
      - 16gb: 16384 ctx
      - 32gb: 32768 ctx
    """
    ctx_map = {"8gb": 8192, "16gb": 16384, "32gb": 32768}
    tier = (ai_tier or ram_tier or "8gb").lower()
    ctx_size = ctx_map.get(tier, 8192)
    return PrefixCacheManager(ctx_size=ctx_size)


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover

    mgr = PrefixCacheManager(ctx_size=8192)
    mgr.pin_system_prompt(
        name="system",
        text="You are Celina, a local desktop assistant. Be concise and accurate.",
    )
    mgr.pin_tool_defs(
        name="tools",
        tools=[{"name": "read_file", "description": "Read a file", "inputSchema": {}}],
    )
    text, tokens = mgr.prefix()
    print(f"prefix tokens={tokens} len={len(text)}")
    print(json.dumps(mgr.summary(), indent=2))

    verbose = (
        "The command ran successfully. Exit code 0. "
        "Output: file1.txt, file2.txt, file3.txt. "
        "Total size: 1024 bytes. "
        "Error: none. "
        "Warning: low disk space. "
        "Date: 2026-11-20. "
        "SHA256: abc123. "
        "This is filler. " * 20
    )
    budget = ContextBudget(ctx_size=8192, used_tokens=6000)
    compacted, freed = ContextCompactor().compact_turn_output(
        turn_output=verbose, budget=budget,
    )
    print(f"compacted: {len(verbose)} -> {len(compacted)} chars, freed={freed} tokens")
    print(f"should_compact: {budget.should_compact}")
    print(json.dumps(mgr.summary(), indent=2))
