"""Celina v5 Planner — Control-Flow Nodes (`sequence`, `branch`, `loop`, `retry`)

Coordinates child execution inside the durable SQLite TaskGraph with strict
safety caps (`max_iterations` on loops, `max_attempts` on retries) so an LLM
plan can never spin into an unbounded execution loop.

Fail-closed on malformed `control_config`: `from_raw()` never raises on bad
input — it clamps to safe defaults.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Literal

ControlKind = Literal["sequence", "branch", "loop", "retry"]
VALID_CONTROL_KINDS: frozenset[str] = frozenset({"sequence", "branch", "loop", "retry"})
TERMINAL_OK_STATES: frozenset[str] = frozenset({"done", "observed"})
TERMINAL_FAIL_STATES: frozenset[str] = frozenset({"failed", "blocked", "cancelled"})


def _safe_int(val: Any, default: int, lo: int = 0, hi: int = 1000) -> int:
    """Parse an int from possibly-malleated LLM config, clamped to [lo, hi].

    Never raises — malformed strings, None, or non-numeric values fall back to
    `default` (clamped to [lo, hi]).
    """
    try:
        if isinstance(val, bool):
            # bool is a subclass of int; treat True as 1, False as 0
            iv = 1 if val else 0
        else:
            iv = int(val)
    except (TypeError, ValueError):
        iv = default
    return max(lo, min(hi, iv))


@dataclasses.dataclass(frozen=True)
class ControlFlowNode:
    kind: ControlKind
    config: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_raw(cls, kind: str | None, raw_config: str | dict[str, Any] | None) -> ControlFlowNode:
        clean_kind = (kind or "sequence").strip().lower()
        if clean_kind not in VALID_CONTROL_KINDS:
            clean_kind = "sequence"
        if isinstance(raw_config, dict):
            cfg = dict(raw_config)
        elif isinstance(raw_config, str) and raw_config.strip():
            try:
                parsed = json.loads(raw_config)
                cfg = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                cfg = {}
        else:
            cfg = {}

        # Hard safety caps on loop iterations & retry counts — safe int parsing
        if clean_kind == "loop":
            max_it = _safe_int(cfg.get("max_iterations", 3), default=3, lo=1, hi=20)
            cfg["max_iterations"] = max_it
            cfg["iteration"] = _safe_int(cfg.get("iteration", 0), default=0, lo=0, hi=max_it)
        elif clean_kind == "retry":
            max_att = _safe_int(cfg.get("max_attempts", 3), default=3, lo=1, hi=5)
            cfg["max_attempts"] = max_att
            cfg["attempt"] = _safe_int(cfg.get("attempt", 0), default=0, lo=0, hi=max_att)
        return cls(kind=clean_kind, config=cfg)  # type: ignore[arg-type]

    def evaluate_children(self, children: list[dict[str, Any]], *, state: dict[str, Any] | None = None) -> dict[str, Any]:
        """Compute the next control-flow transition given ordered children rows.

        Returns:
            {
              "control_status": "running" | "done" | "failed",
              "runnable_child_ids": [...],
              "reset_child_ids": [...],
              "updated_config": {...},
            }

        """
        if not children:
            return {
                "control_status": "done",
                "runnable_child_ids": [],
                "reset_child_ids": [],
                "updated_config": dict(self.config),
            }

        cfg = dict(self.config)
        state = state or {}

        if self.kind == "sequence":
            for ch in children:
                st = ch["status"]
                if st in TERMINAL_FAIL_STATES:
                    return {"control_status": "failed", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
                if st == "running":
                    return {"control_status": "running", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
                if st == "pending":
                    return {
                        "control_status": "running",
                        "runnable_child_ids": [ch["id"]],
                        "reset_child_ids": [],
                        "updated_config": cfg,
                    }
            return {"control_status": "done", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}

        if self.kind == "branch":
            # Sticky branch selection: once chosen_child_id is set, never re-evaluate.
            chosen = None
            chosen_idx = None
            stored_choice = cfg.get("chosen_child_id")
            if stored_choice is not None:
                for idx, ch in enumerate(children):
                    if ch["id"] == stored_choice:
                        chosen = ch
                        chosen_idx = idx
                        break

            if chosen is None:
                # First-time selection: find first child whose when_key matches state,
                # or a child with default:true, or no when_key at all.
                fallback = None  # child with default:true or no when_key
                for idx, ch in enumerate(children):
                    ch_cfg = ControlFlowNode.from_raw("sequence", ch.get("control_config")).config
                    mkey = ch_cfg.get("when_key")
                    if mkey is None:
                        # No condition — always a candidate; prefer default:true
                        if ch_cfg.get("default") is True:
                            chosen = ch
                            chosen_idx = idx
                            break
                        if fallback is None:
                            fallback = (ch, idx)
                    elif state.get(mkey) == ch_cfg.get("when_value"):
                        chosen = ch
                        chosen_idx = idx
                        break
                if chosen is None and fallback is not None:
                    chosen, chosen_idx = fallback

            # If nothing matched and no fallback exists, fail cleanly.
            if chosen is None:
                return {
                    "control_status": "failed",
                    "runnable_child_ids": [],
                    "reset_child_ids": [],
                    "updated_config": cfg,
                }

            # Persist sticky choice so mid-execution state changes never switch branches.
            cfg["chosen_child_id"] = chosen["id"]

            st = chosen["status"]
            if st in TERMINAL_OK_STATES:
                return {"control_status": "done", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
            if st in TERMINAL_FAIL_STATES:
                return {"control_status": "failed", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
            return {
                "control_status": "running",
                "runnable_child_ids": [chosen["id"]] if st == "pending" else [],
                "reset_child_ids": [],
                "updated_config": cfg,
            }

        if self.kind == "loop":
            max_iter = _safe_int(cfg.get("max_iterations", 3), default=3, lo=1, hi=20)
            cur_iter = _safe_int(cfg.get("iteration", 0), default=0, lo=0, hi=max_iter)

            # Pre-check: if until_key already satisfied, don't run even iteration 0.
            if _safe_int(cfg.get("check_before_run", 0), default=0, lo=0, hi=1) == 1:
                stop_key = cfg.get("until_key")
                stop_met = bool(stop_key and state.get(stop_key) == cfg.get("until_value", True))
                if stop_met:
                    return {"control_status": "done", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}

            for ch in children:
                st = ch["status"]
                if st in TERMINAL_FAIL_STATES:
                    return {"control_status": "failed", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
                if st == "running":
                    return {"control_status": "running", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
                if st == "pending":
                    # Guard: don't dispatch if we've already hit max_iter.
                    if cur_iter >= max_iter:
                        return {"control_status": "done", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
                    return {
                        "control_status": "running",
                        "runnable_child_ids": [ch["id"]],
                        "reset_child_ids": [],
                        "updated_config": cfg,
                    }

            # All children completed this iteration
            cur_iter += 1
            cfg["iteration"] = cur_iter
            stop_key = cfg.get("until_key")
            stop_met = bool(stop_key and state.get(stop_key) == cfg.get("until_value", True))
            if stop_met or cur_iter >= max_iter:
                return {"control_status": "done", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
            return {
                "control_status": "running",
                "runnable_child_ids": [],
                "reset_child_ids": [ch["id"] for ch in children],
                "updated_config": cfg,
            }

        # kind == "retry"
        max_att = _safe_int(cfg.get("max_attempts", 3), default=3, lo=1, hi=5)
        cur_att = _safe_int(cfg.get("attempt", 0), default=0, lo=0, hi=max_att)

        # Execute all children sequentially as a unit.
        all_terminal_ok = True
        any_running = False
        first_pending = None
        for ch in children:
            st = ch["status"]
            if st in TERMINAL_FAIL_STATES:
                all_terminal_ok = False
                break
            if st == "running":
                any_running = True
                break
            if st == "pending":
                if first_pending is None:
                    first_pending = ch
            # st == "done" or "observed" — keep checking next child

        if any_running:
            return {"control_status": "running", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
        if first_pending is not None:
            return {
                "control_status": "running",
                "runnable_child_ids": [first_pending["id"]],
                "reset_child_ids": [],
                "updated_config": cfg,
            }
        if all_terminal_ok:
            return {"control_status": "done", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}

        # Target failed/blocked — increment attempt, reset children if within max_att.
        if cur_att < max_att:
            cur_att += 1
            cfg["attempt"] = cur_att
            return {
                "control_status": "running",
                "runnable_child_ids": [],
                "reset_child_ids": [ch["id"] for ch in children],
                "updated_config": cfg,
            }
        return {"control_status": "failed", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
