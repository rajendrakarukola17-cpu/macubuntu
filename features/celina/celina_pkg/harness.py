"""Celina Unified Agent Harness (`harness.py`)

Turns raw tool execution into verified, taint-aware, accountable actions:
  1. `ToolOutcome`: structured result distinguishing `executed` vs `verified`
     (kills "victory declaration bias" where `"Error: ..."` strings looked like success).
  2. `VerificationGate`: runs registered post-condition verifiers for mutating tools
     (e.g., file exists & hash updated, service active, target removed/moved).
  3. `ExperiencePromoter`: closes the Experience Learning Loop safely — promotes
     repeated verified workflows ONLY when `tainted=False` (T1 prompt-injection defense),
     always landing in Quarantine first.

Zero third-party dependencies (pure Python 3.13 stdlib).
Zero $HOME filesystem mutations at import time.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

VerifierFn = Callable[[dict[str, Any], str], tuple[bool, str]]

_ERROR_PREFIXES = (
    "error:",
    "failed",
    "blocked:",
    "denied:",
    "cancelled",
    "refused",
    "tool argument error",
    "tool execution error",
    "offline fallback",
)


@dataclasses.dataclass(frozen=True)
class ToolOutcome:
    tool_name: str
    ok: bool
    verified: bool
    output: str
    verification_detail: str = ""
    tainted_source: bool = False
    latency_ms: int = 0
    error: str = ""

    def allows_victory_claim(self) -> bool:
        """The LLM may declare completion ONLY if execution succeeded AND verification passed."""
        return bool(self.ok and self.verified)


class VerificationGate:
    """Post-execution verification harness preventing "victory declaration bias".
    If a tool declares `requires_verification=True`, execution is NOT considered
    complete until its post-condition verifier confirms the real OS state changed.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._verifiers: dict[str, VerifierFn] = {}
        self._register_builtin_verifiers()

    def register(self, tool_name: str, fn: VerifierFn) -> None:
        with self._lock:
            self._verifiers[tool_name] = fn

    def _register_builtin_verifiers(self) -> None:
        def _verify_file_present(args: dict[str, Any], _out: str) -> tuple[bool, str]:
            raw = args.get("path") or args.get("dst") or args.get("file_path") or ""
            if not raw:
                return False, "missing path argument to verify"
            p = Path(str(raw)).expanduser()
            if p.is_file():
                return True, f"verified file exists ({p.stat().st_size} bytes)"
            return False, f"target file missing after write: {p}"

        def _verify_file_moved(args: dict[str, Any], _out: str) -> tuple[bool, str]:
            dst = Path(str(args.get("dst") or "")).expanduser()
            src = Path(str(args.get("src") or "")).expanduser()
            if dst.exists() and not src.exists():
                return True, f"verified moved to {dst}"
            return False, f"post-move mismatch (src_exists={src.exists()}, dst_exists={dst.exists()})"

        def _verify_file_trashed(args: dict[str, Any], _out: str) -> tuple[bool, str]:
            p = Path(str(args.get("path") or "")).expanduser()
            if not p.exists():
                return True, f"verified removed from original path: {p}"
            return False, f"file still present after trash: {p}"

        self._verifiers["write_file"] = _verify_file_present
        self._verifiers["edit_file"] = _verify_file_present
        self._verifiers["create_document"] = _verify_file_present
        self._verifiers["move_file"] = _verify_file_moved
        self._verifiers["trash_file"] = _verify_file_trashed

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        raw_output: str,
        *,
        requires_verification: bool = False,
        tainted_source: bool = False,
        latency_ms: int = 0,
    ) -> ToolOutcome:
        text = str(raw_output or "").strip()
        low = text.lower()
        if not text or low.startswith(_ERROR_PREFIXES):
            return ToolOutcome(
                tool_name=tool_name,
                ok=False,
                verified=False,
                output=text,
                verification_detail="tool returned error/denial",
                tainted_source=tainted_source,
                latency_ms=latency_ms,
                error=text[:300] or "empty tool output",
            )

        with self._lock:
            verifier = self._verifiers.get(tool_name)

        if verifier is not None:
            try:
                v_ok, v_detail = verifier(arguments, text)
            except Exception as exc:
                v_ok, v_detail = False, f"verifier crashed: {exc}"
            return ToolOutcome(
                tool_name=tool_name,
                ok=v_ok,
                verified=v_ok,
                output=text,
                verification_detail=v_detail,
                tainted_source=tainted_source,
                latency_ms=latency_ms,
                error="" if v_ok else f"Post-condition verification failed: {v_detail}",
            )

        if requires_verification:
            # Fail-closed: tool requires verification but has no registered post-condition check
            return ToolOutcome(
                tool_name=tool_name,
                ok=True,
                verified=False,
                output=text,
                verification_detail="unverified (no post-condition verifier registered)",
                tainted_source=tainted_source,
                latency_ms=latency_ms,
            )

        return ToolOutcome(
            tool_name=tool_name,
            ok=True,
            verified=True,
            output=text,
            verification_detail="read-only / self-verified",
            tainted_source=tainted_source,
            latency_ms=latency_ms,
        )


class ExperiencePromoter:
    """Closes the Experience Learning Loop safely:
    - Tracks verified, untainted tool-chain signatures across turns.
    - NEVER promotes a trace if `turn_tainted=True` (closes T1 prompt-injection -> skill persistence).
    - Once a clean chain succeeds `min_successes` times (default 2), marks it ready
      for quarantined Skillbook drafting (requires explicit user approval before activation).
    """

    def __init__(self, *, min_successes: int = 2) -> None:
        self.min_successes = max(2, int(min_successes))
        self._lock = threading.RLock()
        self._pattern_counts: dict[str, int] = {}
        self._failed_signatures: dict[str, str] = {}

    @staticmethod
    def _chain_signature(steps: list[dict[str, Any]]) -> str:
        norm = [
            {"tool": s.get("tool", ""), "arg_keys": sorted((s.get("args") or {}).keys())}
            for s in steps
        ]
        return hashlib.sha256(json.dumps(norm, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def record_turn_trace(
        self,
        steps: list[dict[str, Any]],
        *,
        all_verified: bool,
        turn_tainted: bool,
        error_summary: str = "",
    ) -> dict[str, Any]:
        if not steps:
            return {"status": "ignored", "promote_candidate": False}
        sig = self._chain_signature(steps)
        with self._lock:
            if not all_verified:
                self._failed_signatures[sig] = (error_summary or "unverified_or_failed")[:200]
                return {"status": "recorded_antipattern", "signature": sig, "promote_candidate": False}
            if turn_tainted:
                return {"status": "skipped_tainted_turn", "signature": sig, "promote_candidate": False}
            cnt = self._pattern_counts.get(sig, 0) + 1
            self._pattern_counts[sig] = cnt
            self._failed_signatures.pop(sig, None)
            return {
                "status": "candidate_ready" if cnt >= self.min_successes else "tracked",
                "signature": sig,
                "count": cnt,
                "promote_candidate": cnt >= self.min_successes,
            }

    def known_failure_hint(self, steps: list[dict[str, Any]]) -> str | None:
        sig = self._chain_signature(steps)
        with self._lock:
            return self._failed_signatures.get(sig)


__all__ = [
    "ExperiencePromoter",
    "ToolOutcome",
    "VerificationGate",
]
