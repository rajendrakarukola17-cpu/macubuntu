"""
Celina v5 Hierarchical Task Planner (`tree_planner.py`, ReAcTree-inspired)

Decomposes a high-level user goal into a bounded tree of `agent` leaf nodes
and `control` nodes (`sequence`, `branch`, `loop`, `retry`) persisted into
SQLite `TaskGraph` (`tasks.db`). Strictly enforces `max_depth`, `max_breadth`,
and `max_total_nodes` to prevent runaway LLM decomposition.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from typing import Any, Optional

from .control_flow import VALID_CONTROL_KINDS, ControlFlowNode
from .task_graph import TaskGraph

PLANNER_SYSTEM_PROMPT = """You are Celina's hierarchical planner. Decompose the goal
into a tree of subgoals. Each node is either:

1. An AGENT node: a self-contained subgoal that one executor can achieve with
   Celina's existing tools (os_hardware, files_office, wayland_gui, etc.).
2. A CONTROL node: coordinates children with one of:
   - sequence: run children in order
   - branch: run the child whose condition matches current state
   - loop: repeat a child until a condition is met (max_iterations required)
   - retry: retry a child up to N times on failure

Return ONLY valid JSON:
{"node_type":"agent"|"control","goal":"...","control_kind":null|"sequence"|"branch"|"loop"|"retry",
 "control_config":{...},"children":[...]}
Leaf agent nodes must be achievable by a single tool call or a short tool chain.
Do NOT invent tools. Available tools: {tool_list}
"""


@dataclasses.dataclass(frozen=True)
class PlannerConfig:
    max_depth: int = 4
    max_breadth: int = 6
    max_total_nodes: int = 40


def _parse_json_fragment(raw: str) -> Optional[dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(ln for ln in lines[1:] if not ln.strip().startswith("```")).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


class TreePlanner:
    def __init__(self, llm_client: Any, graph: TaskGraph, config: Optional[PlannerConfig] = None):
        self.llm = llm_client
        self.graph = graph
        self.cfg = config or PlannerConfig()

    def plan(self, root_goal: str, available_tools: list[str], *, priority: int = 5) -> str:
        """Decompose `root_goal` and persist the bounded tree. Returns `root_id`."""
        clean_goal = (root_goal or "").strip()
        if not clean_goal:
            raise ValueError("Cannot plan an empty goal.")

        root_id = str(uuid.uuid4())
        self.graph.insert_task(
            task_id=root_id,
            parent_id=None,
            goal=clean_goal,
            node_type="control",
            control_kind="sequence",
            control_config={"description": "root sequence"},
            priority=priority,
            order_index=0,
        )
        budget = {"remaining": max(1, self.cfg.max_total_nodes - 1)}
        self._expand(root_id, clean_goal, available_tools, depth=0, budget=budget, priority=priority)
        return root_id

    def _expand(
        self,
        parent_id: str,
        goal: str,
        tools: list[str],
        *,
        depth: int,
        budget: dict[str, int],
        priority: int,
    ) -> None:
        if budget["remaining"] <= 0:
            return
        if depth >= self.cfg.max_depth:
            self._insert_leaf(parent_id, goal, order_index=0, budget=budget, priority=priority)
            return

        if self.llm is None:
            self._insert_leaf(parent_id, goal, order_index=0, budget=budget, priority=priority)
            return

        raw = self.llm.complete(
            system=PLANNER_SYSTEM_PROMPT.replace("{tool_list}", ", ".join(tools)),
            user=f"Goal: {goal}",
            temperature=0.2,
        )
        node = _parse_json_fragment(raw)
        if node is None:
            self._insert_leaf(parent_id, goal, order_index=0, budget=budget, priority=priority)
            return

        if node.get("node_type") == "control":
            children = node.get("children")
            if not isinstance(children, list) or not children:
                self._insert_leaf(parent_id, goal, order_index=0, budget=budget, priority=priority)
                return

            bounded_children = children[: self.cfg.max_breadth]
            for idx, child_item in enumerate(bounded_children):
                if budget["remaining"] <= 0:
                    break
                if isinstance(child_item, dict):
                    child_goal = str(child_item.get("goal") or goal).strip()
                    nested = child_item.get("children")
                    is_ctrl = bool(
                        child_item.get("node_type") == "control"
                        and isinstance(nested, list)
                        and nested
                        and depth + 1 < self.cfg.max_depth
                    )
                    if is_ctrl:
                        ckind = str(child_item.get("control_kind") or "sequence").lower()
                        if ckind not in VALID_CONTROL_KINDS:
                            ckind = "sequence"
                        cf = ControlFlowNode.from_raw(ckind, child_item.get("control_config"))
                        child_id = str(uuid.uuid4())
                        self.graph.insert_task(
                            task_id=child_id,
                            parent_id=parent_id,
                            goal=child_goal,
                            node_type="control",
                            control_kind=cf.kind,
                            control_config=cf.config,
                            priority=priority,
                            order_index=idx,
                        )
                        budget["remaining"] -= 1
                        self._persist_subtree(
                            child_id,
                            nested,
                            tools=tools,
                            depth=depth + 1,
                            budget=budget,
                            priority=priority,
                        )
                    else:
                        self._insert_leaf(
                            parent_id,
                            child_goal,
                            order_index=idx,
                            budget=budget,
                            priority=priority,
                            control_config=child_item.get("control_config"),
                        )
                else:
                    self._insert_leaf(parent_id, str(child_item).strip(), order_index=idx, budget=budget, priority=priority)
        else:
            leaf_goal = str(node.get("goal") or goal).strip()
            self._insert_leaf(parent_id, leaf_goal, order_index=0, budget=budget, priority=priority)

    def _persist_subtree(
        self,
        parent_id: str,
        children: list[Any],
        *,
        tools: list[str],
        depth: int,
        budget: dict[str, int],
        priority: int,
    ) -> None:
        for idx, item in enumerate(children[: self.cfg.max_breadth]):
            if budget["remaining"] <= 0:
                break
            if isinstance(item, dict):
                cgoal = str(item.get("goal") or "").strip()
                if not cgoal:
                    continue
                sub = item.get("children")
                if item.get("node_type") == "control" and isinstance(sub, list) and sub and depth + 1 < self.cfg.max_depth:
                    cf = ControlFlowNode.from_raw(item.get("control_kind"), item.get("control_config"))
                    cid = str(uuid.uuid4())
                    self.graph.insert_task(
                        task_id=cid,
                        parent_id=parent_id,
                        goal=cgoal,
                        node_type="control",
                        control_kind=cf.kind,
                        control_config=cf.config,
                        priority=priority,
                        order_index=idx,
                    )
                    budget["remaining"] -= 1
                    self._persist_subtree(cid, sub, tools=tools, depth=depth + 1, budget=budget, priority=priority)
                else:
                    self._insert_leaf(
                        parent_id,
                        cgoal,
                        order_index=idx,
                        budget=budget,
                        priority=priority,
                        control_config=item.get("control_config"),
                    )
            else:
                self._insert_leaf(parent_id, str(item).strip(), order_index=idx, budget=budget, priority=priority)

    def _insert_leaf(
        self,
        parent_id: str,
        goal: str,
        *,
        order_index: int,
        budget: dict[str, int],
        priority: int,
        control_config: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        if budget["remaining"] <= 0 or not goal:
            return None
        tid = str(uuid.uuid4())
        self.graph.insert_task(
            task_id=tid,
            parent_id=parent_id,
            goal=goal,
            node_type="agent",
            control_config=control_config if isinstance(control_config, dict) else None,
            priority=priority,
            order_index=order_index,
        )
        budget["remaining"] -= 1
        return tid
