"""Celina Self-Model (`self_model.py`) — P1 truth layer.

Tracks tool outcome statistics with:
- Persistence to memory.db (SQLite WAL) — survives restart (audit item 5)
- EWMA decay — old failures don't poison repaired tools (audit item 5)
- Context keys (tool x offline/online x gear) — different populations (audit item 5)
- Wilson score lower bound — correct small-sample behavior (audit item 7)
- Signed bias — mean(predicted) - observed_success_rate, not Brier (audit item 6)
- Calibration ledger — prediction at dispatch, outcome from independent verification (audit item 8)

stdlib-only: sqlite3, datetime, math, json, threading
Zero $HOME filesystem mutations at import time.
"""

from __future__ import annotations

import math
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config

# EWMA decay half-life in days.  After this many days, a record's weight is halved.
EWMA_HALF_LIFE_DAYS = 30.0

# Minimum attempts before a tool is considered "proven" (not a blind spot).
MIN_ATTEMPTS_FOR_PROVEN = 5

# Wilson score confidence z-score (95% = 1.96).
WILSON_Z = 1.96

# Default confidence threshold below which human confirmation is required.
LOW_CONFIDENCE_THRESHOLD = 0.55

# Brier error threshold above which a tool is considered poorly calibrated.
POOR_CALIBRATION_BRIER_THRESHOLD = 0.25


@dataclass(frozen=True)
class ToolCalibration:
    """Empirical calibration for a single tool.

    Laplace-smoothed reliability so 0-attempt tools start neutral (0.75),
    not overconfident (1.0).
    """

    tool_name: str
    attempts: int
    verified_successes: int
    mean_latency_ms: float
    brier_error: float  # lower is better (0.0 = perfectly calibrated, 1.0 = maximally wrong)
    last_error: str = ""

    @property
    def empirical_reliability(self) -> float:
        """Laplace-smoothed reliability estimate."""
        return round((self.verified_successes + 1.5) / (self.attempts + 2.0), 3)


@dataclass(frozen=True)
class EpistemicPrecheck:
    """Result of a reflexive check before dispatching a tool."""

    can_attempt: bool
    confidence: float
    require_human_confirmation: bool
    blind_spots: tuple[str, ...]
    recommended_fallback: str | None = None
    reason: str = ""


def _context_key(tool: str, *, online: bool, gear: int | str) -> str:
    """Build a context key: ``tool:offline:gear2`` or ``tool:online:gear3``."""
    mode = "online" if online else "offline"
    return f"{tool}:{mode}:gear{gear}"


def _ewma_weight(ts: float, now: float | None = None) -> float:
    """Exponential decay weight for a record at timestamp `ts`.

    Weight = 2^(-age_days / half_life_days).  Recent records weigh ~1.0;
    records older than the half-life weigh less.
    """
    if now is None:
        now = datetime.now(UTC).timestamp()
    age_days = max(0.0, (now - ts) / 86400.0)
    return math.exp(-math.log(2) * age_days / EWMA_HALF_LIFE_DAYS)


class SelfModel:
    """Tracks per-tool outcome statistics with persistence, decay, and calibration."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path is not None else config.DB_PATH
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """Create the tool_outcomes table if it does not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_outcomes (
                    action_id     TEXT PRIMARY KEY,
                    tool          TEXT NOT NULL,
                    predicted     REAL NOT NULL,
                    verified      INTEGER,       -- NULL until outcome recorded
                    context_key   TEXT NOT NULL,
                    ts            REAL NOT NULL
                )
                """,
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_outcomes_tool ON tool_outcomes(tool)",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_outcomes_context "
                "ON tool_outcomes(context_key)",
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Calibration ledger (audit item 8)
    # ------------------------------------------------------------------

    def record_prediction(
        self,
        action_id: str,
        tool: str,
        predicted_success: float,
        *,
        online: bool,
        gear: int | str,
    ) -> None:
        """Write the prediction to the ledger at dispatch time.

        The outcome is filled in later by :meth:`record_outcome` using
        independent read-after-write verification — never the tool's own
        exit code (audit item 8).
        """
        if not 0.0 <= predicted_success <= 1.0:
            raise ValueError("predicted_success must be in [0, 1]")
        context_key = _context_key(tool, online=online, gear=gear)
        ts = datetime.now(UTC).timestamp()
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tool_outcomes
                    (action_id, tool, predicted, verified, context_key, ts)
                    VALUES (?, ?, ?, NULL, ?, ?)
                    """,
                    (action_id, tool, predicted_success, context_key, ts),
                )
                conn.commit()
            finally:
                conn.close()

    def record_outcome(
        self,
        action_id: str,
        verified_success: bool,
    ) -> None:
        """Record the verified outcome for a previously-predicted action.

        The verified result must come from independent read-after-write
        verification (re-stat/hash, systemctl is-active), never from the
        tool's own exit code (audit item 8).
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    UPDATE tool_outcomes
                    SET verified = ?
                    WHERE action_id = ? AND verified IS NULL
                    """,
                    (int(verified_success), action_id),
                )
                conn.commit()
            finally:
                conn.close()

    def record_outcome_full(
        self,
        action_id: str,
        tool: str,
        predicted_success: float,
        verified_success: bool,
        *,
        online: bool,
        gear: int | str,
    ) -> None:
        """Convenience: record prediction + outcome in one call (for tools
        that don't use the two-phase dispatch flow).
        """
        context_key = _context_key(tool, online=online, gear=gear)
        ts = datetime.now(UTC).timestamp()
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tool_outcomes
                    (action_id, tool, predicted, verified, context_key, ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (action_id, tool, predicted_success, int(verified_success),
                     context_key, ts),
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Statistics (audit items 5, 6, 7)
    # ------------------------------------------------------------------

    def get_stats(
        self,
        tool: str,
        context_key: str | None = None,
        *,
        apply_decay: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Return EWMA-weighted statistics for a tool.

        Returns a dict with attempts, success_rate, predicted_mean,
        signed_bias, brier, wilson_lower, and is_unproven.
        """
        if context_key is None:
            # Gather all context keys for this tool
            rows = self._fetch_rows(tool, None, now=now)
        else:
            rows = self._fetch_rows(tool, context_key, now=now)

        if not rows:
            return {
                "tool": tool,
                "context_key": context_key,
                "attempts": 0,
                "success_rate": None,
                "predicted_mean": None,
                "signed_bias": None,
                "brier": None,
                "wilson_lower": None,
                "is_unproven": True,
            }

        n = len(rows)
        successes = sum(1 for _, v, _ in rows if v)
        success_rate = successes / n
        predicted_mean = sum(p for p, _, _ in rows) / n
        signed_bias = predicted_mean - success_rate

        # Brier score (secondary metric — not used for overconfidence penalty)
        brier = sum((p - float(v)) ** 2 for p, v, _ in rows) / n

        # Wilson score lower bound (95% CI) — correct small-sample behavior
        wilson_lower = _wilson_lower_bound(successes, n)

        # EWMA-weighted attempt count (effective N after decay)
        if apply_decay:
            total_weight = sum(w for _, _, w in rows)
            effective_n = total_weight
        else:
            effective_n = float(n)

        effective_n_rounded = round(effective_n, 6)
        return {
            "tool": tool,
            "context_key": context_key,
            "attempts": n,
            "effective_attempts": round(effective_n, 2),
            "success_rate": round(success_rate, 4),
            "predicted_mean": round(predicted_mean, 4),
            "signed_bias": round(signed_bias, 4),
            "brier": round(brier, 4),
            "wilson_lower": round(wilson_lower, 4),
            "is_unproven": effective_n_rounded < MIN_ATTEMPTS_FOR_PROVEN,
        }

    def list_all_tools(self) -> list[str]:
        """Return every tool name that has at least one recorded outcome row."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                rows = conn.execute(
                    "SELECT DISTINCT tool FROM tool_outcomes ORDER BY tool",
                ).fetchall()
            finally:
                conn.close()
        return [r[0] for r in rows]

    def _fetch_rows(
        self,
        tool: str,
        context_key: str | None,
        now: float | None = None,
    ) -> list[tuple[float, bool, float]]:
        """Fetch (predicted, verified_bool, weight) rows for a tool/context.

        Only rows with verified != NULL are returned (outcome recorded).
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                if context_key is not None:
                    rows = conn.execute(
                        """
                        SELECT predicted, verified, ts
                        FROM tool_outcomes
                        WHERE tool = ? AND context_key = ? AND verified IS NOT NULL
                        """,
                        (tool, context_key),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT predicted, verified, ts
                        FROM tool_outcomes
                        WHERE tool = ? AND verified IS NOT NULL
                        """,
                        (tool,),
                    ).fetchall()
            finally:
                conn.close()

        if now is None:
            now = datetime.now(UTC).timestamp()

        result: list[tuple[float, bool, float]] = []
        for predicted, verified, ts in rows:
            weight = _ewma_weight(ts, now=now)
            result.append((float(predicted), bool(verified), weight))
        return result

    # ------------------------------------------------------------------
    # Convenience queries
    # ------------------------------------------------------------------

    def is_unproven(self, tool: str, *, online: bool, gear: int | str) -> bool:
        """True if the tool has fewer than MIN_ATTEMPTS_FOR_PROVEN
        effective attempts in this context (blind spot — audit item 7).
        """
        context_key = _context_key(tool, online=online, gear=gear)
        stats = self.get_stats(tool, context_key, apply_decay=True)
        return stats["is_unproven"]

    def wilson_lower(
        self,
        tool: str,
        *,
        online: bool,
        gear: int | str,
    ) -> float | None:
        """Wilson score lower bound for auto-execute decisions.

        Earned autonomy: auto guard permitted only when wilson_lower >= 0.95
        over >= 30 verified attempts (audit: earned autonomy ceiling).
        """
        context_key = _context_key(tool, online=online, gear=gear)
        stats = self.get_stats(tool, context_key, apply_decay=True)
        return stats["wilson_lower"]

    def signed_bias(
        self,
        tool: str,
        *,
        online: bool,
        gear: int | str,
    ) -> float | None:
        """Signed bias = mean(predicted) - observed_success_rate.

        Positive = overconfident; negative = underconfident.  Used for
        the overconfidence penalty instead of raw Brier (audit item 6).
        """
        context_key = _context_key(tool, online=online, gear=gear)
        stats = self.get_stats(tool, context_key, apply_decay=True)
        return stats["signed_bias"]

    # ------------------------------------------------------------------
    # Epistemic self-model methods (Concept 1: Self-Model / Meta-Cognition)
    # ------------------------------------------------------------------

    def get_calibration(self, tool_name: str) -> ToolCalibration:
        """Return a `ToolCalibration` for `tool_name`.

        Tools with no recorded history return a neutral calibration
        (0 attempts, 0 successes, brier_error=0.0).
        """
        stats = self.get_stats(tool_name)
        attempts = stats["attempts"]
        if attempts == 0:
            return ToolCalibration(
                tool_name=tool_name,
                attempts=0,
                verified_successes=0,
                mean_latency_ms=0.0,
                brier_error=0.0,
                last_error="",
            )
        successes = round(stats["success_rate"] * attempts)
        return ToolCalibration(
            tool_name=tool_name,
            attempts=attempts,
            verified_successes=int(successes),
            mean_latency_ms=0.0,  # not tracked in current stats schema
            brier_error=round(stats["brier"], 4),
            last_error="",
        )

    def rank_candidates(self, candidates: list[str]) -> list[str]:
        """Bounded adaptive routing: order fallback tools by empirical reliability (highest first).

        Tools with no recorded history start at Laplace-smoothed 0.75
        and sort alphabetically as a tiebreaker.
        """
        return sorted(
            candidates,
            key=lambda t: (-self.get_calibration(t).empirical_reliability, t),
        )

    def precheck_action(
        self,
        tool_name: str,
        *,
        is_tier2: bool,
        requires_network: bool = False,
        circuit_open: bool = False,
        fallback_candidates: list[str] | None = None,
    ) -> EpistemicPrecheck:
        """Reflexive check BEFORE dispatching a tool or declaring capability.

        Returns an `EpistemicPrecheck` with:
        - `can_attempt=False` if the tool is blocked (offline, circuit open, etc.)
        - `confidence` penalized by historical Brier calibration error
        - `require_human_confirmation=True` for Tier-2 tools or low-confidence tools
        - `recommended_fallback` from `rank_candidates()` when confirmation is needed
        """
        blind_spots: list[str] = []

        # Offline check — never attempt live-web actions when web_comms is disabled
        if requires_network and not config.is_web_comms_enabled():
            blind_spots.append(
                "offline_mode: web_comms.enabled=false (local model frozen at training cutoff)",
            )
            return EpistemicPrecheck(
                can_attempt=False,
                confidence=0.0,
                require_human_confirmation=True,
                blind_spots=tuple(blind_spots),
                reason="Refusing live-web action while offline mode is active.",
            )

        # Circuit breaker open — tool disabled after repeated failures
        if circuit_open:
            blind_spots.append(f"circuit_open: '{tool_name}' disabled after repeated failures")
            alts = self.rank_candidates(
                [c for c in (fallback_candidates or []) if c != tool_name],
            )
            return EpistemicPrecheck(
                can_attempt=False,
                confidence=0.0,
                require_human_confirmation=True,
                blind_spots=tuple(blind_spots),
                recommended_fallback=alts[0] if alts else None,
                reason=f"Tool '{tool_name}' circuit breaker is open.",
            )

        # Confidence penalized by poor Brier calibration
        cal = self.get_calibration(tool_name)
        penalty = min(0.30, cal.brier_error * 0.5) if cal.attempts >= 3 else 0.0
        confidence = round(
            max(0.05, min(0.98, cal.empirical_reliability - penalty)), 3,
        )

        if cal.attempts >= 3 and cal.brier_error > POOR_CALIBRATION_BRIER_THRESHOLD:
            blind_spots.append(
                f"poor_calibration: {tool_name} brier={cal.brier_error:.2f} "
                f"(last_err={cal.last_error[:60]})",
            )

        need_confirm = is_tier2 or confidence < LOW_CONFIDENCE_THRESHOLD
        alts = self.rank_candidates(
            [c for c in (fallback_candidates or []) if c != tool_name],
        )
        return EpistemicPrecheck(
            can_attempt=True,
            confidence=confidence,
            require_human_confirmation=need_confirm,
            blind_spots=tuple(blind_spots),
            recommended_fallback=alts[0] if (need_confirm and alts) else None,
            reason=(
                "ok"
                if not need_confirm
                else f"confirmation required (tier2={is_tier2}, conf={confidence})"
            ),
        )

    def summarize_for_prompt(self, *, ram_tier: str = "16gb") -> str:
        """Compact volatile self-model block for PromptTranspiler injection.

        Returns a human-readable summary of live calibration metrics,
        degraded tools, and network state — suitable for prepending to
        the LLM system prompt as a self-awareness block.
        """
        with self._lock:
            degraded: list[str] = []
            seen_tools = self.list_all_tools()
            for tool_name in seen_tools:
                stats = self.get_stats(tool_name)
                attempts = stats["attempts"]
                success_rate = stats["success_rate"]
                if attempts >= 2 and success_rate < 0.65:
                    degraded.append(
                        f"{tool_name} (rel={success_rate:.2f}, brier={stats['brier']:.3f})",
                    )

        net_state = (
            "online (web_comms enabled)"
            if config.is_web_comms_enabled()
            else "offline-first (no live web)"
        )
        lines = [
            f"[Self-Model | tier={ram_tier} | network={net_state}]",
            "EPISTEMIC_RULE: If a tool is degraded or offline, state the limitation "
            "explicitly rather than guessing.",
        ]
        if degraded:
            lines.append("DEGRADED_TOOLS: " + ", ".join(sorted(degraded)[:8]))
        return "\n".join(lines)

    @staticmethod
    def _to_calibration_from_rec(tool_name: str, rec: dict[str, Any]) -> ToolCalibration:
        """Build a ToolCalibration from an internal stats record (for summarize_for_prompt)."""
        attempts = int(rec["attempts"])
        ok = int(rec["ok"])
        if attempts == 0:
            return ToolCalibration(tool_name, 0, 0, 0.0, 0.0, "")
        return ToolCalibration(
            tool_name=tool_name,
            attempts=attempts,
            verified_successes=ok,
            mean_latency_ms=round(float(rec["lat_sum"]) / max(1, attempts), 1),
            brier_error=round(float(rec["brier_sum"]) / max(1, attempts), 4),
            last_error=str(rec.get("last_error") or ""),
        )

    def decay(self, now: float | None = None) -> int:
        """Apply EWMA decay conceptually (weighting is done on read).

        Returns the number of records in the table.  No rows are deleted —
        decay is applied at read time via _ewma_weight, so old records
        naturally fade without destructive mutations.
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                count = conn.execute(
                    "SELECT COUNT(*) FROM tool_outcomes",
                ).fetchone()[0]
            finally:
                conn.close()
        return count


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_self_model: SelfModel | None = None
_self_model_lock = threading.Lock()


def get_self_model() -> SelfModel:
    """Return the module-level SelfModel singleton."""
    global _self_model
    with _self_model_lock:
        if _self_model is None:
            _self_model = SelfModel()
        return _self_model


def _wilson_lower_bound(successes: int, n: int, z: float = WILSON_Z) -> float:
    """Wilson score lower bound of the confidence interval.

    Correct small-sample behavior: with n=0, returns 0.0 (unproven).
    With n=30 and 100% success, returns ~0.89 (not 1.0 — honest uncertainty).
    """
    if n == 0:
        return 0.0
    phat = successes / n
    denominator = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denominator
    margin = (z / denominator) * math.sqrt(
        phat * (1 - phat) / n + z * z / (4 * n * n),
    )
    return center - margin


__all__ = [
    "EWMA_HALF_LIFE_DAYS",
    "LOW_CONFIDENCE_THRESHOLD",
    "MIN_ATTEMPTS_FOR_PROVEN",
    "POOR_CALIBRATION_BRIER_THRESHOLD",
    "EpistemicPrecheck",
    "SelfModel",
    "ToolCalibration",
    "_context_key",
    "_wilson_lower_bound",
    "get_self_model",
]
