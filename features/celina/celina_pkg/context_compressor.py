"""Celina Tier-Adaptive Context Compressor & Per-Tool Token Ledger
===============================================================
Integrates Headroom (Apache 2.0) + Tokview-style per-tool token accounting
with hardware-adaptive scaling across 8 GB / 16 GB / 32 GB tiers:

  - 8 GB / Turbo Mode / High PSI:
      Deterministic-only compression (SmartCrusher + CodeCompressor + CacheAligner),
      0 MB extra ML RAM, low threshold (>= 800 chars), 2 MB CCR RAM ring buffer.
  - 16 GB (Balanced):
      Preserves normal outputs untouched (local threshold >= 2,400 chars;
      cloud threshold >= 1,200 chars), 16 MB reversible CCR RAM cache, optional
      ML prose compression (Kompress) gated by free RAM >= 4 GB & PSI < 10%.
  - 32 GB (Workstation):
      Gentle local threshold (>= 6,000 chars; cloud >= 1,800 chars), 64 MB
      reversible CCR RAM cache, full CacheAligner + optional ML Kompress.

Security & Privacy Invariants (all tiers):
  - Zero telemetry & strictly offline (HEADROOM_OFFLINE=1, HEADROOM_BEACON=off, DO_NOT_TRACK=1).
  - Never mutates `system` or `user` messages.
  - Ephemeral RAM-only CCR cache (never writes raw tool outputs to disk/SQLite; cleared on /clear & Turbo).
  - Metadata-only per-tool token accounting (never stores raw prompts or responses).
  - Pure-stdlib fallback when `headroom-ai` is not installed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

# Lock down telemetry and offline mode BEFORE importing headroom
os.environ.setdefault("HEADROOM_OFFLINE", "1")
os.environ.setdefault("HEADROOM_BEACON", "off")
os.environ.setdefault("DO_NOT_TRACK", "1")

try:
    from headroom import compress as _headroom_compress  # type: ignore

    HEADROOM_AVAILABLE = True
except ImportError:
    _headroom_compress = None
    HEADROOM_AVAILABLE = False

_HW_PROFILE_PATH = Path("/etc/macubuntu/hardware.json")
_PSI_MEM_PATH = Path("/proc/pressure/memory")
_MEMINFO_PATH = Path("/proc/meminfo")


# ---------------------------------------------------------------------------
# Tier Policies (8gb / 16gb / 32gb)
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class CompressionTierPolicy:
    tier: str                      # "8gb" | "16gb" | "32gb"
    local_min_chars: int           # skip compressing local tool outputs below this size
    cloud_min_chars: int           # skip compressing cloud tool outputs below this size
    per_msg_min_chars: int         # minimum individual tool message size to compress
    allow_ml_kompress: bool        # allow neural prose compression (Kompress) when RAM/PSI permit
    ml_min_free_mb: int            # minimum MemAvailable MiB required before running ML Kompress
    ml_max_psi_avg10: float        # maximum PSI some avg10 before stepping down to deterministic
    psi_stepdown_threshold: float  # PSI threshold that forces 8gb deterministic mode
    ccr_max_bytes: int             # RAM cap for reversible CCR cache
    ccr_max_items: int             # max stored tool outputs in CCR cache
    enable_cache_aligner: bool = True


TIER_POLICIES: dict[str, CompressionTierPolicy] = {
    "8gb": CompressionTierPolicy(
        tier="8gb",
        local_min_chars=800,
        cloud_min_chars=800,
        per_msg_min_chars=500,
        allow_ml_kompress=False,
        ml_min_free_mb=0,
        ml_max_psi_avg10=0.0,
        psi_stepdown_threshold=15.0,
        ccr_max_bytes=2 * 1024 * 1024,
        ccr_max_items=16,
    ),
    "16gb": CompressionTierPolicy(
        tier="16gb",
        local_min_chars=2400,
        cloud_min_chars=1200,
        per_msg_min_chars=1000,
        allow_ml_kompress=True,
        ml_min_free_mb=4096,
        ml_max_psi_avg10=10.0,
        psi_stepdown_threshold=15.0,
        ccr_max_bytes=16 * 1024 * 1024,
        ccr_max_items=64,
    ),
    "32gb": CompressionTierPolicy(
        tier="32gb",
        local_min_chars=6000,
        cloud_min_chars=1800,
        per_msg_min_chars=1600,
        allow_ml_kompress=True,
        ml_min_free_mb=6144,
        ml_max_psi_avg10=15.0,
        psi_stepdown_threshold=25.0,
        ccr_max_bytes=64 * 1024 * 1024,
        ccr_max_items=128,
    ),
}


def _read_meminfo_mb() -> tuple[int, int]:
    """Return (total_mb, available_mb) from /proc/meminfo."""
    total_kb = 0
    avail_kb = 0
    try:
        for line in _MEMINFO_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail_kb = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return 8192, 2048
    return max(1, total_kb // 1024), max(0, avail_kb // 1024)


def _read_psi_some_avg10() -> float:
    """Return Linux memory PSI `some avg10` percentage (0.0 if unavailable)."""
    try:
        for line in _PSI_MEM_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("some "):
                for tok in line.split():
                    if tok.startswith("avg10="):
                        return float(tok.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return 0.0


def detect_effective_tier(
    *,
    tier_override: str | None = None,
    hw_profile_path: Path = _HW_PROFILE_PATH,
) -> tuple[CompressionTierPolicy, str]:
    """Resolve active CompressionTierPolicy using:
      1. Explicit override / CELINA_COMPRESSION_TIER env var
      2. Turbo / Lite Mode (CELINA_LITE_MODE=1 -> forces 8gb)
      3. /etc/macubuntu/hardware.json (`ram_tier`)
      4. Live /proc/meminfo total RAM
      5. Live PSI step-down to 8gb if host is currently thrashing
    Returns (policy, reason).
    """
    forced = (tier_override or os.environ.get("CELINA_COMPRESSION_TIER") or "").strip().lower()
    if forced in TIER_POLICIES:
        base_tier = forced
        reason = f"override:{forced}"
    elif os.environ.get("CELINA_LITE_MODE", "0").strip().lower() in {"1", "true", "yes"}:
        return TIER_POLICIES["8gb"], "turbo_lite_mode"
    else:
        base_tier = ""
        reason = ""
        if hw_profile_path.is_file():
            try:
                hw = json.loads(hw_profile_path.read_text(encoding="utf-8"))
                rt = str(hw.get("ram_tier") or "").strip().lower()
                if rt in TIER_POLICIES:
                    base_tier = rt
                    reason = f"hardware.json:{rt}"
            except (OSError, json.JSONDecodeError):
                pass
        if not base_tier:
            total_mb, _ = _read_meminfo_mb()
            if total_mb < 12000:
                base_tier = "8gb"
            elif total_mb < 24000:
                base_tier = "16gb"
            else:
                base_tier = "32gb"
            reason = f"meminfo:{total_mb}MB->{base_tier}"

    policy = TIER_POLICIES[base_tier]
    psi = _read_psi_some_avg10()
    if base_tier != "8gb" and psi >= policy.psi_stepdown_threshold:
        return TIER_POLICIES["8gb"], f"psi_stepdown:{base_tier}->8gb(psi={psi:.1f}%)"
    return policy, reason


# ---------------------------------------------------------------------------
# Ephemeral RAM-Only CCR (Reversible Retrieval Cache)
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class CCREntry:
    ccr_id: str
    tool_name: str
    original_text: str
    compressed_chars: int
    original_chars: int
    created_at: float


class EphemeralCCRStore:
    """Bounded in-memory LRU cache for compressed tool outputs.
    Zero disk writes — wiped automatically on process exit, `/clear`, or Turbo Mode.
    """

    def __init__(self, *, max_bytes: int = 16 * 1024 * 1024, max_items: int = 64) -> None:
        self._lock = threading.RLock()
        self._items: OrderedDict[str, CCREntry] = OrderedDict()
        self._used_bytes = 0
        self._max_bytes = max(256 * 1024, int(max_bytes))
        self._max_items = max(4, int(max_items))

    def reconfigure_limits(self, *, max_bytes: int, max_items: int) -> None:
        with self._lock:
            self._max_bytes = max(256 * 1024, int(max_bytes))
            self._max_items = max(4, int(max_items))
            self._evict_locked()

    def _evict_locked(self) -> None:
        while self._items and (len(self._items) > self._max_items or self._used_bytes > self._max_bytes):
            _k, evicted = self._items.popitem(last=False)
            self._used_bytes = max(0, self._used_bytes - len(evicted.original_text.encode("utf-8", errors="ignore")))

    def put(self, *, tool_name: str, original_text: str, compressed_chars: int) -> str:
        raw_bytes = len(original_text.encode("utf-8", errors="ignore"))
        ccr_id = hashlib.sha256(f"{tool_name}:{original_text}".encode("utf-8", errors="replace")).hexdigest()[:10]
        with self._lock:
            if raw_bytes > self._max_bytes:
                return ""
            if ccr_id in self._items:
                self._items.move_to_end(ccr_id)
                return ccr_id
            entry = CCREntry(
                ccr_id=ccr_id,
                tool_name=tool_name or "tool",
                original_text=original_text,
                compressed_chars=int(compressed_chars),
                original_chars=len(original_text),
                created_at=time.monotonic(),
            )
            self._items[ccr_id] = entry
            self._used_bytes += raw_bytes
            self._evict_locked()
            return ccr_id

    def get(self, ccr_id: str, *, query: str = "", max_chars: int = 6000) -> str | None:
        key = (ccr_id or "").strip()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            self._items.move_to_end(key)
            text = entry.original_text
        if query.strip():
            q_low = query.strip().lower()
            matched = [ln for ln in text.splitlines() if q_low in ln.lower()]
            if matched:
                return "\n".join(matched)[:max_chars]
        return text[:max_chars] + (f"\n...[truncated at {max_chars} chars]" if len(text) > max_chars else "")

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            self._used_bytes = 0
            return count

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "items": len(self._items),
                "used_kb": round(self._used_bytes / 1024.0, 1),
                "max_mb": round(self._max_bytes / (1024 * 1024), 1),
                "ids": list(self._items.keys())[-10:],
            }


# ---------------------------------------------------------------------------
# Tokview-Inspired Metadata-Only Per-Tool Token Tracker
# ---------------------------------------------------------------------------
class TokenMetricsTracker:
    """Tracks per-tool token volume and compression savings in memory (metadata-only:
    zero prompt or response text ever retained). Surfaces via `/tokens` & `/why`.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_tool: dict[str, dict[str, int]] = {}
        self._turns = 0
        self._total_before = 0
        self._total_after = 0

    def record_turn(
        self,
        *,
        tokens_before: int,
        tokens_after: int,
        per_tool_tokens: dict[str, tuple[int, int]],
    ) -> None:
        with self._lock:
            self._turns += 1
            self._total_before += max(0, int(tokens_before))
            self._total_after += max(0, int(tokens_after))
            for tool_name, (tb, ta) in per_tool_tokens.items():
                rec = self._by_tool.setdefault(tool_name, {"calls": 0, "tokens_before": 0, "tokens_after": 0})
                rec["calls"] += 1
                rec["tokens_before"] += max(0, int(tb))
                rec["tokens_after"] += max(0, int(ta))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            saved = max(0, self._total_before - self._total_after)
            pct = round((saved * 100.0 / self._total_before), 1) if self._total_before else 0.0
            ranked = sorted(
                (
                    {
                        "tool": k,
                        "calls": v["calls"],
                        "tokens_before": v["tokens_before"],
                        "tokens_after": v["tokens_after"],
                        "saved_tokens": max(0, v["tokens_before"] - v["tokens_after"]),
                    }
                    for k, v in self._by_tool.items()
                ),
                key=lambda item: (-item["tokens_before"], item["tool"]),
            )
            return {
                "turns": self._turns,
                "tokens_before": self._total_before,
                "tokens_after": self._total_after,
                "saved_tokens": saved,
                "saved_pct": pct,
                "by_tool": ranked[:20],
            }

    def format_report(self) -> str:
        snap = self.snapshot()
        lines = [
            f"📊 Celina Token & Context Compression Ledger ({snap['turns']} turn(s)):",
            f"  • Total input tokens: {snap['tokens_after']} (was {snap['tokens_before']}, saved {snap['saved_tokens']} / {snap['saved_pct']}%)",
        ]
        if snap["by_tool"]:
            lines.append("  • Top tools by context footprint:")
            for row in snap["by_tool"][:8]:
                lines.append(
                    f"    - {row['tool']}: {row['tokens_after']} tok (was {row['tokens_before']}, {row['calls']} call(s))",
                )
        return "\n".join(lines)

    def reset(self) -> None:
        with self._lock:
            self._by_tool.clear()
            self._turns = 0
            self._total_before = 0
            self._total_after = 0


# ---------------------------------------------------------------------------
# Compression Result & Engine
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class CompressionReport:
    messages: list[dict[str, Any]]
    backend: str               # "headroom" | "headroom+ml" | "stdlib" | "bypass"
    tier: str                  # "8gb" | "16gb" | "32gb"
    tier_reason: str
    chars_before: int
    chars_after: int
    tokens_before: int
    tokens_after: int
    ccr_ids: tuple[str, ...] = ()
    per_tool_tokens: dict[str, tuple[int, int]] = dataclasses.field(default_factory=dict)

    @property
    def saved_pct(self) -> float:
        if self.tokens_before <= 0:
            return 0.0
        return round(max(0.0, (1.0 - (self.tokens_after / self.tokens_before)) * 100.0), 1)


def _est_tokens_str(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _est_messages(messages: list[dict[str, Any]]) -> tuple[int, int]:
    chars = sum(len(str(m.get("content") or "")) for m in messages)
    return chars, (max(1, chars // 4) if chars else 0)


def _stdlib_compress_text(content: str, *, aggressive: bool = False) -> str:
    """Zero-dependency deterministic compressor:
    1. Compacts pretty-printed JSON (and deduplicates repetitive object lists if aggressive)
    2. Collapses consecutive duplicate log lines
    3. Compacts long blank-line runs while preserving error/traceback lines
    """
    stripped = content.strip()
    if stripped.startswith(("{", "[")) and stripped.endswith(("}", "]")):
        try:
            parsed = json.loads(stripped)
            if aggressive and isinstance(parsed, list) and len(parsed) > 25:
                head = parsed[:12]
                tail = parsed[-5:]
                compact_obj = {
                    "items_head": head,
                    "omitted_count": len(parsed) - len(head) - len(tail),
                    "items_tail": tail,
                }
                compact = json.dumps(compact_obj, separators=(",", ":"), ensure_ascii=False)
            else:
                compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
            if len(compact) < len(content):
                return compact
        except json.JSONDecodeError:
            pass

    lines = content.splitlines()
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        cnt = j - i
        if cnt == 1:
            out_lines.append(lines[i])
        else:
            out_lines.append(f"{lines[i]}  [repeated {cnt}x]")
        i = j

    compacted = "\n".join(out_lines)
    return compacted if len(compacted) < len(content) else content


def _should_use_ml_kompress(policy: CompressionTierPolicy) -> bool:
    if not policy.allow_ml_kompress:
        return False
    env_flag = os.environ.get("CELINA_HEADROOM_ML", "").strip().lower()
    if env_flag in {"0", "false", "no", "off"}:
        return False
    # On 16 GB, require explicit opt-in OR default-on only on 32 GB
    if policy.tier == "16gb" and env_flag not in {"1", "true", "yes", "on"}:
        return False
    _total_mb, avail_mb = _read_meminfo_mb()
    psi = _read_psi_some_avg10()
    return avail_mb >= policy.ml_min_free_mb and psi < policy.ml_max_psi_avg10


def _invoke_headroom(
    messages: list[dict[str, Any]],
    *,
    model: str,
    use_ml_kompress: bool,
    enable_cache_aligner: bool,
) -> tuple[list[dict[str, Any]], int, int] | None:
    """Version-resilient wrapper around headroom.compress()."""
    if not HEADROOM_AVAILABLE or _headroom_compress is None:
        return None
    try:
        sig = inspect.signature(_headroom_compress)
        accepted = set(sig.parameters.keys())
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    except (TypeError, ValueError):
        accepted = set()
        has_varkw = True

    candidate_kwargs: dict[str, Any] = {
        "model": model,
        "compress_user_messages": False,
        "skip_kompress": not use_ml_kompress,
        "cache_aligner": enable_cache_aligner,
    }
    call_kwargs = (
        candidate_kwargs
        if has_varkw
        else {k: v for k, v in candidate_kwargs.items() if k in accepted}
    )

    try:
        res = _headroom_compress(messages, **call_kwargs)
        compressed = list(getattr(res, "messages", messages))
        if len(compressed) != len(messages):
            # Preserve exact message roles/count safety
            return None
        # Hard invariant: never alter system or user message contents
        safe_msgs: list[dict[str, Any]] = []
        for orig, comp in zip(messages, compressed):
            if orig.get("role") in {"system", "user"}:
                safe_msgs.append(orig)
            else:
                safe_msgs.append(comp)
        _cb, tb_est = _est_messages(messages)
        _ca, ta_est = _est_messages(safe_msgs)
        tb = int(getattr(res, "tokens_before", tb_est) or tb_est)
        ta = int(getattr(res, "tokens_after", ta_est) or ta_est)
        return safe_msgs, tb, ta
    except Exception:
        return None


_CCR_STORE = EphemeralCCRStore()
_TOKEN_TRACKER = TokenMetricsTracker()


def get_ccr_store() -> EphemeralCCRStore:
    return _CCR_STORE


def get_token_tracker() -> TokenMetricsTracker:
    return _TOKEN_TRACKER


def retrieve_compressed_context(ccr_id: str, query: str = "", max_chars: int = 6000) -> str:
    """Reversibly retrieve full/filtered original tool output from the RAM-only CCR cache."""
    hit = _CCR_STORE.get(ccr_id, query=query, max_chars=max_chars)
    if hit is None:
        return f"CCR entry '{ccr_id}' not found (expired, cleared, or out of session)."
    return hit


def clear_compression_state() -> dict[str, int]:
    """Clears ephemeral CCR cache (call on `/clear` or Turbo Mode activation)."""
    cleared = _CCR_STORE.clear()
    return {"ccr_cleared": cleared}


def compress_messages_for_llm(
    messages: list[dict[str, Any]],
    *,
    model: str = "qwen3:1.7b",
    route: str = "local",
    tier_override: str | None = None,
    enabled: bool = True,
    record_metrics: bool = True,
) -> CompressionReport:
    """Tier-adaptive context compression before local Ollama/llama-server or cloud inference.

    - Preserves `system` and `user` messages verbatim.
    - Leaves small/medium tool outputs untouched on 16 GB / 32 GB so useful details stay intact.
    - Stores pre-compression originals in RAM-only CCR cache and appends a `[CCR:<id>]` handle
      whenever significant shrinkage (>= 20%) occurs so nothing is permanently lost.
    """
    policy, tier_reason = detect_effective_tier(tier_override=tier_override)
    _CCR_STORE.reconfigure_limits(max_bytes=policy.ccr_max_bytes, max_items=policy.ccr_max_items)

    chars_before, toks_before = _est_messages(messages)
    threshold = policy.cloud_min_chars if route == "cloud" else policy.local_min_chars

    if not enabled or chars_before < threshold:
        per_tool: dict[str, tuple[int, int]] = {}
        for m in messages:
            if m.get("role") == "tool":
                tn = str(m.get("name") or "tool")
                t = _est_tokens_str(str(m.get("content") or ""))
                prev_b, prev_a = per_tool.get(tn, (0, 0))
                per_tool[tn] = (prev_b + t, prev_a + t)
        if record_metrics:
            _TOKEN_TRACKER.record_turn(
                tokens_before=toks_before,
                tokens_after=toks_before,
                per_tool_tokens=per_tool,
            )
        return CompressionReport(
            messages=messages,
            backend="bypass",
            tier=policy.tier,
            tier_reason=tier_reason,
            chars_before=chars_before,
            chars_after=chars_before,
            tokens_before=toks_before,
            tokens_after=toks_before,
            ccr_ids=(),
            per_tool_tokens=per_tool,
        )

    use_ml = _should_use_ml_kompress(policy)
    backend = "stdlib"
    out_messages: list[dict[str, Any]]
    toks_b_out = toks_before
    toks_a_out = toks_before

    hr = _invoke_headroom(
        messages,
        model=model,
        use_ml_kompress=use_ml,
        enable_cache_aligner=policy.enable_cache_aligner,
    )
    if hr is not None:
        out_messages, toks_b_out, toks_a_out = hr
        backend = "headroom+ml" if use_ml else "headroom"
    else:
        aggressive = policy.tier == "8gb"
        out_messages = []
        for m in messages:
            if m.get("role") != "tool":
                out_messages.append(m)
                continue
            raw_content = str(m.get("content") or "")
            if len(raw_content) < policy.per_msg_min_chars:
                out_messages.append(m)
                continue
            comp = _stdlib_compress_text(raw_content, aggressive=aggressive)
            out_messages.append({**m, "content": comp} if len(comp) < len(raw_content) else m)

    # Attach reversible CCR handles to any significantly compressed tool message (>= 20% smaller)
    final_messages: list[dict[str, Any]] = []
    ccr_ids: list[str] = []
    per_tool_tokens: dict[str, tuple[int, int]] = {}

    for orig, comp in zip(messages, out_messages):
        if orig.get("role") != "tool":
            final_messages.append(comp)
            continue

        tname = str(orig.get("name") or "tool")
        orig_txt = str(orig.get("content") or "")
        comp_txt = str(comp.get("content") or "")
        tb = _est_tokens_str(orig_txt)

        if len(orig_txt) >= policy.per_msg_min_chars and len(comp_txt) <= int(len(orig_txt) * 0.80):
            cid = _CCR_STORE.put(tool_name=tname, original_text=orig_txt, compressed_chars=len(comp_txt))
            if cid:
                ccr_ids.append(cid)
                comp_txt = f"{comp_txt}\n[CCR:{cid} | original={len(orig_txt)} chars; use /expand {cid} for full raw output]"
                comp = {**comp, "content": comp_txt}

        ta = _est_tokens_str(comp_txt)
        prev_b, prev_a = per_tool_tokens.get(tname, (0, 0))
        per_tool_tokens[tname] = (prev_b + tb, prev_a + ta)
        final_messages.append(comp)

    chars_after, est_toks_after = _est_messages(final_messages)
    if backend == "stdlib":
        toks_a_out = est_toks_after

    if record_metrics:
        _TOKEN_TRACKER.record_turn(
            tokens_before=toks_b_out,
            tokens_after=toks_a_out,
            per_tool_tokens=per_tool_tokens,
        )

    return CompressionReport(
        messages=final_messages,
        backend=backend,
        tier=policy.tier,
        tier_reason=tier_reason,
        chars_before=chars_before,
        chars_after=chars_after,
        tokens_before=toks_b_out,
        tokens_after=toks_a_out,
        ccr_ids=tuple(ccr_ids),
        per_tool_tokens=per_tool_tokens,
    )
