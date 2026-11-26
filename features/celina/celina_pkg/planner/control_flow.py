"""
Celina v5 Planner — Control-Flow Nodes (`sequence`, `branch`, `loop`, `retry`)

Coordinates child execution inside the durable SQLite TaskGraph with strict
safety caps (`max_iterations` on loops, `max_attempts` on retries) so an LLM
plan can never spin into an unbounded execution loop.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Literal, Optional

ControlKind = Literal["sequence", "branch", "loop", "retry"]
VALID_CONTROL_KINDS: frozenset[str] = frozenset({"sequence", "branch", "loop", "retry"})
TERMINAL_OK_STATES: frozenset[str] = frozenset({"done", "observed"})
TERMINAL_FAIL_STATES: frozenset[str] = frozenset({"failed", "blocked", "cancelled"})


@dataclasses.dataclass(frozen=True)
class ControlFlowNode:
    kind: ControlKind
    config: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_raw(cls, kind: Optional[str], raw_config: Optional[str | dict[str, Any]]) -> "ControlFlowNode":
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

        # Hard safety caps on loop iterations & retry counts
        if clean_kind == "loop":
            max_it = int(cfg.get("max_iterations", 3))
            cfg["max_iterations"] = max(1, min(max_it, 20))
            cfg["iteration"] = max(0, int(cfg.get("iteration", 0)))
        elif clean_kind == "retry":
            max_att = int(cfg.get("max_attempts", 3))
            cfg["max_attempts"] = max(1, min(max_att, 5))
        return cls(kind=clean_kind, config=cfg)  # type: ignore[arg-type]

    def evaluate_children(self, children: list[dict[str, Any]], *, state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """
        Compute the next control-flow transition given ordered children rows.
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
            # Choose first child whose optional `match_key`/`match_value` fits `state`, or first child by default
            ctx = state or {}
            chosen = children[0]
            for ch in children:
                ch_cfg = ControlFlowNode.from_raw("sequence", ch.get("control_config")).config
                mkey = ch_cfg.get("when_key")
                if mkey and ctx.get(mkey) == ch_cfg.get("when_value"):
                    chosen = ch
                    break
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
            max_iter = int(cfg.get("max_iterations", 3))
            cur_iter = int(cfg.get("iteration", 0))
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
            # All children completed this iteration
            cur_iter += 1
            cfg["iteration"] = cur_iter
            stop_key = cfg.get("until_key")
            stop_met = bool(stop_key and (state or {}).get(stop_key) == cfg.get("until_value", True))
            if stop_met or cur_iter >= max_iter:
                return {"control_status": "done", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
            return {
                "control_status": "running",
                "runnable_child_ids": [],
                "reset_child_ids": [ch["id"] for ch in children],
                "updated_config": cfg,
            }

        # kind == "retry"
        max_att = int(cfg.get("max_attempts", 3))
        cur_att = int(cfg.get("attempt", 0))
        target = children[0]
        st = target["status"]
        if st in TERMINAL_OK_STATES:
            return {"control_status": "done", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
        if st == "running":
            return {"control_status": "running", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
        if st == "pending":
            return {"control_status": "running", "runnable_child_ids": [target["id"]], "reset_child_ids": [], "updated_config": cfg}
        # Target failed/blocked
        if st == "blocked" or st == "cancelled":
            return {"control_status": "failed", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
        cur_att += 1
        cfg["attempt"] = cur_att
        if cur_att < max_att:
            return {
                "control_status": "running",
                "runnable_child_ids": [],
                "reset_child_ids": [target["id"]],
                "updated_config": cfg,
            }
        return {"control_status": "failed", "runnable_child_ids": [], "reset_child_ids": [], "updated_config": cfg}
