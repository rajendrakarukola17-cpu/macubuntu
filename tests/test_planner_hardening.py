"""
Regression tests for features/celina/celina_pkg/planner P0/P1 audit fixes.

Exactly 7 regression tests, one per audit P0/P1 area:

  1. control_flow._safe_int: never raises, clamps malformed LLM int config
  2. control_flow: sticky branch selection (chosen_child_id) + loop until_key
     pre-check + max_iter guard
  3. task_graph: parent dir 0o700 + umask 0o077 for sqlite connect (mode 0600)
  4. tree_planner: try/except around llm.complete + <<<USER_GOAL>>> fencing
  5. blast_radius: word-boundary regexes + fail-closed scoring (irreversible>=55,
     unknown>=30, empty=high)
  6. scaffolding: word-boundary _classify_objective + planner/__init__ exports
     (LogicalScaffold, LogicalScaffolder, BlastRadiusAssessment,
     BlastRadiusEstimator)
  7. task_graph: recursive _reset_subtree + next_runnable() up to 8 passes
     resolving nested control nodes to a single runnable leaf
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any

import pytest

from celina_pkg.planner import (
    BlastRadiusAssessment,
    BlastRadiusEstimator,
    ControlFlowNode,
    LogicalScaffold,
    LogicalScaffolder,
    TaskGraph,
    TreePlanner,
    VALID_CONTROL_KINDS,
)

from celina_pkg.planner.tree_planner import PlannerConfig


# ---------------------------------------------------------------------------
# 1. control_flow._safe_int: never raises, clamps malformed LLM int config
# ---------------------------------------------------------------------------
def test_control_flow_safe_int_never_raises_and_clamps():
    from celina_pkg.planner.control_flow import _safe_int

    # valid values pass through
    assert _safe_int(5, default=1) == 5
    assert _safe_int("5", default=1) == 5
    # None / non-numeric -> default
    assert _safe_int(None, default=1) == 1
    assert _safe_int("garbage", default=7) == 7
    assert _safe_int([], default=7) == 7
    # bool treated as int
    assert _safe_int(True, default=1) == 1
    assert _safe_int(False, default=1) == 0
    # clamped to [lo, hi]
    assert _safe_int(9999, default=3, lo=1, hi=20) == 20
    assert _safe_int(-5, default=3, lo=1, hi=20) == 1
    # from_raw: out-of-range numeric clamps to [lo, hi]
    cf = ControlFlowNode.from_raw("loop", {"max_iterations": 9999})
    assert cf.config["max_iterations"] == 20  # hi clamp on valid int
    # non-numeric falls back to default (clamped), not hi
    cf3 = ControlFlowNode.from_raw("loop", {"max_iterations": "infinite"})
    assert cf3.config["max_iterations"] == 3  # default=3, clamped to [1,20]
    cf2 = ControlFlowNode.from_raw("retry", {"max_attempts": None})
    assert cf2.config["max_attempts"] == 3  # default on None


# ---------------------------------------------------------------------------
# 2. Sticky branch selection + loop pre-check / max_iter guard
# ---------------------------------------------------------------------------
def test_control_flow_sticky_branch_and_loop_guards():
    # Sticky branch: once chosen_child_id is set, mid-run state changes never flip.
    cfg_with_choice = {"when_key": "env", "when_value": "prod", "chosen_child_id": "c1"}
    cf = ControlFlowNode.from_raw("branch", cfg_with_choice)
    decision = cf.evaluate_children(
        [
            {"id": "c1", "status": "pending", "control_config": {"when_key": "env", "when_value": "prod"}},
            {"id": "c2", "status": "pending", "control_config": {"when_key": "env", "when_value": "dev"}},
        ],
        state={"env": "dev"},
    )
    assert decision["runnable_child_ids"] == ["c1"], "sticky branch must not flip on state change"

    # First-time selection picks matching condition
    cf2 = ControlFlowNode.from_raw("branch", {"when_key": "env", "when_value": "prod"})
    d2 = cf2.evaluate_children(
        [
            {"id": "c1", "status": "pending", "control_config": {"when_key": "env", "when_value": "prod"}},
            {"id": "c2", "status": "pending", "control_config": {"when_key": "env", "when_value": "dev"}},
        ],
        state={"env": "prod"},
    )
    assert d2["runnable_child_ids"] == ["c1"]
    assert d2["updated_config"]["chosen_child_id"] == "c1"

    # Loop until_key pre-check: already met => done without running any child
    cf3 = ControlFlowNode.from_raw(
        "loop",
        {"max_iterations": 5, "until_key": "done", "until_value": True, "check_before_run": 1},
    )
    d3 = cf3.evaluate_children(
        [{"id": "c1", "status": "pending", "control_config": {}}],
        state={"done": True},
    )
    assert d3["control_status"] == "done"
    assert d3["runnable_child_ids"] == []

    # Loop max_iter guard: at/over max_iter => done, no dispatch
    cf4 = ControlFlowNode.from_raw("loop", {"max_iterations": 2, "iteration": 2})
    d4 = cf4.evaluate_children(
        [{"id": "c1", "status": "pending", "control_config": {}}],
        state={},
    )
    assert d4["control_status"] == "done"

    # Loop within budget: dispatch child as runnable
    cf5 = ControlFlowNode.from_raw("loop", {"max_iterations": 3, "iteration": 0})
    d5 = cf5.evaluate_children(
        [{"id": "c1", "status": "pending", "control_config": {}}],
        state={},
    )
    assert d5["control_status"] == "running"
    assert d5["runnable_child_ids"] == ["c1"]


# ---------------------------------------------------------------------------
# 3. task_graph: parent dir 0o700 + umask 0o077 for sqlite connect (mode 0600)
# ---------------------------------------------------------------------------
def test_task_graph_sqlite_permissions(tmp_path: Path):
    db_path = tmp_path / "subdir" / "tasks.db"
    graph = TaskGraph(db_path)
    dir_mode = stat.S_IMODE(os.stat(db_path.parent).st_mode)
    file_mode = stat.S_IMODE(os.stat(db_path).st_mode)
    assert dir_mode == 0o700, f"parent dir mode {oct(dir_mode)} != 0o700"
    assert file_mode == 0o600, f"db file mode {oct(file_mode)} != 0o600"
    graph.close()


# ---------------------------------------------------------------------------
# 4. tree_planner: try/except around llm.complete + <<<USER_GOAL>>> fencing
# ---------------------------------------------------------------------------
class _FailingLLM:
    def complete(self, *, system, user, temperature=0.2):
        raise RuntimeError("LLM downstream unavailable")


class _RecordingLLM:
    def __init__(self):
        self.last_user = None

    def complete(self, *, system, user, temperature=0.2):
        self.last_user = user
        return json.dumps({"node_type": "agent", "goal": "mock leaf", "children": []})


def test_tree_planner_llm_failure_and_prompt_fencing(tmp_path: Path):
    db_path = tmp_path / "tf" / "tasks.db"

    # (a) LLM raises -> falls back to a single leaf, no orphan empty control node
    graph = TaskGraph(db_path)
    planner = TreePlanner(llm_client=_FailingLLM(), graph=graph, config=PlannerConfig(max_total_nodes=10))
    root_id = planner.plan("build something", ["tool_a"], priority=5)
    children = graph.get_children(root_id)
    assert len(children) == 1
    assert children[0]["node_type"] == "agent"
    graph.close()

    # (b) <<<USER_GOAL>>> / <<<END_USER_GOAL>>> fence present in the user prompt
    graph2 = TaskGraph(tmp_path / "tf2" / "tasks.db")
    recorder = _RecordingLLM()
    planner2 = TreePlanner(llm_client=recorder, graph=graph2)
    planner2.plan("inject; rm -rf /", ["tool_a"], priority=5)
    assert recorder.last_user is not None
    assert "<<<USER_GOAL>>>" in recorder.last_user
    assert "<<<END_USER_GOAL>>>" in recorder.last_user
    assert "inject; rm -rf /" in recorder.last_user
    graph2.close()


# ---------------------------------------------------------------------------
# 5. blast_radius: word-boundary regexes + fail-closed scoring
# ---------------------------------------------------------------------------
def test_blast_radius_word_boundaries_and_fail_closed():
    est = BlastRadiusEstimator()
    # Word-boundary: substrings must NOT trigger false irreversible matches
    for action in ("confirm deployment", "alarm check", "install updates",
                   "undelete file", "wall message", "reformat text"):
        a = est.assess(action=action, target="/tmp/x")
        assert a.reversibility != "irreversible" or a.score < 55, \
            f"false positive irreversible for '{action}': {a.reasons}"

    # Empty action -> fail-closed: high risk, score >= 60
    a = est.assess(action="", target="/tmp/x")
    assert a.band == "high"
    assert a.score >= 60
    assert a.reversibility == "irreversible"

    # Unknown mutating verb -> fail-closed: score >= 30
    a2 = est.assess(action="frobnicate", target="/tmp/x")
    assert a2.score >= 30, f"unknown verb not fail-closed: {a2.score} {a2.reasons}"

    # Irreversible -> score >= 55, never low
    a3 = est.assess(action="rm -rf /tmp/x", target="/tmp/x")
    assert a3.score >= 55
    assert a3.band in ("high", "critical")
    assert a3.reversibility == "irreversible"

    # Read-only safe action -> low
    a4 = est.assess(action="list files", target="/tmp/x")
    assert a4.band == "low"
    assert a4.reversibility == "reversible"

    # assessed_at uses datetime.now(timezone.utc) (ends with Z, no +00:00)
    assert a4.assessed_at.endswith("Z")
    assert "+00:00" not in a4.assessed_at


# ---------------------------------------------------------------------------
# 6. scaffolding: word-boundary _classify_objective + planner/__init__ exports
# ---------------------------------------------------------------------------
def test_scaffolding_classify_and_planner_exports():
    import celina_pkg.planner as pkg

    # Exports exist
    for name in ("LogicalScaffold", "LogicalScaffolder",
                 "BlastRadiusAssessment", "BlastRadiusEstimator"):
        assert hasattr(pkg, name), f"planner.__init__ missing export: {name}"
    assert pkg.LogicalScaffold is LogicalScaffold
    assert pkg.LogicalScaffolder is LogicalScaffolder
    assert pkg.BlastRadiusAssessment is BlastRadiusAssessment
    assert pkg.BlastRadiusEstimator is BlastRadiusEstimator

    # Word-boundary classify: "undelete" must NOT trigger file_mutation
    scaffolder = LogicalScaffolder()
    s = scaffolder.build_scaffold("please undelete my file")
    assert s.objective_type == "general_autonomy", s.objective_type

    # office_document via .docx keyword
    s2 = scaffolder.build_scaffold("create a .docx report")
    assert s2.objective_type == "office_document", s2.objective_type

    # world_state accepted and reflected in preconditions
    s3 = scaffolder.build_scaffold("check cpu", world_state={"ram": "8gb", "cpu": "4core"})
    assert "World state snapshot captured" in " ".join(s3.preconditions)
    assert s3.kv_anchor_id == hashlib.sha256(s3.anchor_prefix.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 7. task_graph: recursive _reset_subtree + next_runnable() 8-pass fixed point
# ---------------------------------------------------------------------------
def test_task_graph_reset_subtree_and_next_runnable(tmp_path: Path):
    db_path = tmp_path / "reset" / "tasks.db"
    graph = TaskGraph(db_path)

    root_id = str(uuid.uuid4())
    graph.insert_task(
        task_id=root_id, parent_id=None, goal="root", node_type="control",
        control_kind="sequence", control_config={},
    )
    ctrl_id = str(uuid.uuid4())
    graph.insert_task(
        task_id=ctrl_id, parent_id=root_id, goal="inner", node_type="control",
        control_kind="sequence", control_config={},
    )
    leaf_id = str(uuid.uuid4())
    graph.insert_task(
        task_id=leaf_id, parent_id=ctrl_id, goal="leaf", node_type="agent",
        control_config=None,
    )

    # next_runnable walks nested controls (sequence -> sequence -> leaf) in one
    # call (8-pass fixed point covers depth-4 nesting without hanging).
    runnable = graph.next_runnable(state={})
    assert runnable is not None
    assert str(runnable["id"]) == leaf_id

    # Mark leaf done; advance_control_nodes propagates sequence completion upward
    graph.mark(leaf_id, "done")
    runnables = graph.advance_control_nodes(state={})
    # The inner sequence now has all children done -> done; root sequence done too
    assert graph.get_task(ctrl_id)["status"] == "done"
    assert graph.get_task(root_id)["status"] == "done"

    # _reset_subtree resets nested control + descendants to pending for re-run
    graph._reset_subtree(root_id, "2026-11-14T00:00:00Z")
    assert graph.get_task(root_id)["status"] == "pending"
    assert graph.get_task(ctrl_id)["status"] == "pending"
    assert graph.get_task(leaf_id)["status"] == "pending"
    graph.close()
