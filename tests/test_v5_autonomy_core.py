"""
Suite 13 — Celina v5 Autonomous Core Tests

Covers every v5 autonomous subsystem end-to-end:
  1. Hierarchical ReAcTree Planner (`tree_planner.py`) & control-flow caps (`sequence`/`branch`/`loop`/`retry`)
  2. Durable SQLite WAL `TaskGraph` (`tasks.db`), recursive cancellation & tamper-evident hash-chained evidence
  3. `AutonomyEngine` observe-only safe burn-in, fail-closed Tier-2 `ApprovalEngine.request_sync()`,
     pre-execution SHA-256 checkpoints (`checkpoint.py`), and Legato/native `SelfHealingExecutor`
  4. Event-driven `ProactiveDaemon` (`proactive/daemon.py` + `rules.json`) allowlist & cooldown enforcement
  5. Guarded nightly `SelfImprover` PCEC loop (sandbox verification, pending human review, anti-downgrade guard)
  6. Integration hooks: `org.macubuntu.Celina.Autonomy` D-Bus UID check, `router.route_for_autonomy()`,
     `submit_goal` Tier-2 tool parity, systemd user units, and `.deb` packaging.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.approval import ApprovalEngine, compute_arguments_hash  # noqa: E402
from celina_pkg.autonomy import (  # noqa: E402
    AutonomyEngine,
    PreExecutionCheckpoint,
    SelfHealingExecutor,
)
from celina_pkg.dbus_service import (  # noqa: E402
    AUTONOMY_INTERFACE_NAME,
    INTROSPECTION_XML,
    CelinaDBusBackend,
)
from celina_pkg.learning import SelfImprover  # noqa: E402
from celina_pkg.planner import (  # noqa: E402
    ControlFlowNode,
    PlannerConfig,
    TaskGraph,
    TreePlanner,
)
from celina_pkg.proactive import DEFAULT_RULES_PATH, ProactiveDaemon  # noqa: E402


class _MockLLM:
    def __init__(self, reply: str):
        self.reply = reply

    def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        return self.reply


def test_tree_planner_bounds_control_flow_and_task_graph_lifecycle(tmp_path: Path):
    graph = TaskGraph(tmp_path / "tasks.db")
    try:
        # Verify WAL + busy_timeout + synchronous=NORMAL pragmas
        assert str(graph._conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower() == "wal"
        assert int(graph._conn.execute("PRAGMA busy_timeout;").fetchone()[0]) >= 5000
        assert int(graph._conn.execute("PRAGMA synchronous;").fetchone()[0]) == 1

        # LLM returns a control node with 10 children -> PlannerConfig caps breadth at 3
        plan_json = json.dumps(
            {
                "node_type": "control",
                "control_kind": "sequence",
                "goal": "Prepare release",
                "children": [{"node_type": "agent", "goal": f"step {i}"} for i in range(10)],
            }
        )
        planner = TreePlanner(_MockLLM(plan_json), graph, PlannerConfig(max_depth=3, max_breadth=3, max_total_nodes=10))
        root_id = planner.plan("Prepare release", available_tools=["get_system_stats", "write_file"])

        children = graph.get_children(root_id)
        assert len(children) == 3
        assert [c["goal"] for c in children] == ["step 0", "step 1", "step 2"]

        # Sequence control flow serves step 0 first, then step 1, then step 2, then marks root 'done'
        first = graph.next_runnable()
        assert first is not None and first["goal"] == "step 0"
        graph.mark(first["id"], "done", result="ok0")

        second = graph.next_runnable()
        assert second is not None and second["goal"] == "step 1"
        graph.mark(second["id"], "done", result="ok1")

        third = graph.next_runnable()
        assert third is not None and third["goal"] == "step 2"
        graph.mark(third["id"], "done", result="ok2")

        assert graph.next_runnable() is None
        assert graph.get_task(root_id)["status"] == "done"

        # Recursive cancellation on a fresh plan
        root2 = TreePlanner(_MockLLM("not valid json"), graph).plan("Fallback goal", available_tools=[])
        assert len(graph.get_children(root2)) == 1  # graceful leaf fallback
        cancelled = graph.cancel_task(root2)
        assert cancelled >= 2
        assert graph.get_task(root2)["status"] == "cancelled"

        # Loop node respects hard max_iterations cap & Branch node matches condition
        loop_cf = ControlFlowNode.from_raw("loop", {"max_iterations": 999})
        assert loop_cf.config["max_iterations"] == 20  # hard safety cap
        branch_cf = ControlFlowNode.from_raw("branch", {})
        branch_res = branch_cf.evaluate_children(
            [
                {"id": "c1", "status": "pending", "control_config": json.dumps({"when_key": "mode", "when_value": "a"})},
                {"id": "c2", "status": "pending", "control_config": json.dumps({"when_key": "mode", "when_value": "b"})},
            ],
            state={"mode": "b"},
        )
        assert branch_res["runnable_child_ids"] == ["c2"]
    finally:
        graph.close()


def test_autonomy_engine_observe_only_approval_checkpoint_and_evidence_tamper(tmp_path: Path):
    graph = TaskGraph(tmp_path / "tasks.db")
    target_file = tmp_path / "notes.txt"
    target_file.write_text("original content", encoding="utf-8")

    class _FakeRouter:
        def __init__(self, decision: dict[str, Any]):
            self.decision = decision

        def route_for_autonomy(self, goal: str, context: dict[str, Any]) -> dict[str, Any]:
            return dict(self.decision)

    def _write_handler(path: str, content: str) -> str:
        Path(path).write_text(content, encoding="utf-8")
        return f"wrote {len(content)} bytes"

    write_args = {"path": str(target_file), "content": "mutated by autonomy"}
    write_decision = {
        "tool_name": "write_file",
        "arguments": write_args,
        "arguments_hash": compute_arguments_hash("write_file", write_args),
        "tier": 2,
        "domain": "files_office",
        "why_receipt_hash": "why123",
        "gear": "standard",
    }

    try:
        executor = SelfHealingExecutor(data_dir=tmp_path, tool_handlers={"write_file": _write_handler})

        # 1. Default observe_only=True NEVER executes Tier-2 tool; file stays untouched
        root_obs = TreePlanner(None, graph).plan("update notes", available_tools=["write_file"])
        eng_obs = AutonomyEngine(graph, executor, _FakeRouter(write_decision), ApprovalEngine(), observe_only=True)
        assert eng_obs.run_until_idle() == 1
        assert target_file.read_text(encoding="utf-8") == "original content"
        obs_leaf = graph.get_children(root_obs)[0]
        assert obs_leaf["status"] == "observed"

        # 2. observe_only=False with headless ApprovalEngine() (no callback) -> fail-closed 'blocked'
        root_blk = TreePlanner(None, graph).plan("update notes blocked", available_tools=["write_file"])
        eng_blk = AutonomyEngine(graph, executor, _FakeRouter(write_decision), ApprovalEngine(None), observe_only=False)
        assert eng_blk.run_until_idle() == 1
        assert target_file.read_text(encoding="utf-8") == "original content"
        blk_leaf = graph.get_children(root_blk)[0]
        assert blk_leaf["status"] == "blocked"
        assert blk_leaf["last_error"] == "approval_denied"

        # 3. observe_only=False with interactive approval -> executes + captures SHA-256 undo checkpoint
        state_events: list[tuple[str, str]] = []
        root_ok = TreePlanner(None, graph).plan("update notes approved", available_tools=["write_file"])
        eng_ok = AutonomyEngine(
            graph,
            executor,
            _FakeRouter(write_decision),
            ApprovalEngine(lambda _t, _d: True),
            observe_only=False,
            state_cb=lambda tid, st: state_events.append((tid, st)),
        )
        assert eng_ok.run_until_idle() == 1
        assert target_file.read_text(encoding="utf-8") == "mutated by autonomy"
        assert any(st == "done" for _tid, st in state_events)

        # Verify PreExecutionCheckpoint restores original bytes cleanly
        undo_dirs = [p for p in (tmp_path / "undo").iterdir() if p.is_dir()]
        assert len(undo_dirs) == 1
        restored_ok, msg = PreExecutionCheckpoint(tmp_path / "undo").restore(undo_dirs[0].name)
        assert restored_ok is True, msg
        assert target_file.read_text(encoding="utf-8") == "original content"

        # 4. Evidence chain passes, then detects tampering immediately
        ok_chain, _cmsg, cnt = graph.verify_evidence_chain()
        assert ok_chain is True and cnt == 3
        graph._conn.execute("UPDATE evidence SET tool_name = 'tampered_tool' WHERE seq = 1")
        graph._conn.commit()
        bad_chain, bad_msg, _ = graph.verify_evidence_chain()
        assert bad_chain is False
        assert "Tampered" in bad_msg
    finally:
        graph.close()


def test_legato_gui_executor_and_retry_exhaustion(tmp_path: Path):
    graph = TaskGraph(tmp_path / "tasks.db")

    class _FakeLegatoResult:
        success = True

        def report(self) -> str:
            return "Clicked Save button after screen stabilized"

    class _FakeLegato:
        def run(self, instruction: str):
            assert "Save" in instruction
            return _FakeLegatoResult()

    try:
        exec_gui = SelfHealingExecutor(data_dir=tmp_path, legato_client=_FakeLegato())
        res = exec_gui.execute(
            {"tool_name": "click_on_text", "domain": "wayland_gui", "arguments": {"instruction": "Click Save"}},
            task_id="t-gui",
        )
        assert res.success is True
        assert res.executor_kind == "legato"

        # Retry exhaustion on unregistered tool handler (max_attempts=2)
        graph.insert_task("t-fail", None, "broken task", node_type="agent", max_attempts=2)

        class _BrokenRouter:
            def route_for_autonomy(self, goal: str, context: dict[str, Any]) -> dict[str, Any]:
                return {
                    "tool_name": "missing_handler",
                    "arguments": {},
                    "arguments_hash": "abc",
                    "tier": 1,
                    "domain": "os_hardware",
                    "why_receipt_hash": "w1",
                    "gear": "fast",
                }

        eng = AutonomyEngine(graph, exec_gui, _BrokenRouter(), ApprovalEngine(lambda _t, _d: True), observe_only=False)
        assert eng.step() == "t-fail"
        assert graph.get_task("t-fail")["status"] == "pending"  # 1/2 -> retried
        assert eng.step() == "t-fail"
        assert graph.get_task("t-fail")["status"] == "failed"   # 2/2 -> exhausted
    finally:
        graph.close()


def test_proactive_daemon_allowlist_cooldown_and_ebpf_consumption(tmp_path: Path):
    published: list[dict[str, Any]] = []
    fake_now = [1000.0]
    daemon = ProactiveDaemon(DEFAULT_RULES_PATH, state_publisher=published.append, clock=lambda: fake_now[0])

    # 1. Safe allowlist rule: calendar standup -> auto_action set_focus_mode
    out = daemon.ingest({"kind": "calendar", "title": "standup"})
    assert len(out) == 1
    assert out[0]["kind"] == "auto_action"
    assert out[0]["tool"] == "set_focus_mode"

    # 2. Safe allowlist rule: battery < 20% on battery -> auto_action set_brightness, cooldown prevents spam
    out_bat1 = daemon.ingest({"kind": "power", "battery_pct": 15, "on_ac": False})
    assert any(e["kind"] == "auto_action" and e["tool"] == "set_brightness" for e in out_bat1)
    out_bat2 = daemon.ingest({"kind": "power", "battery_pct": 14, "on_ac": False})
    assert not any(e.get("tool") == "set_brightness" for e in out_bat2)  # suppressed by cooldown

    # 3. Non-allowlisted tool (offload_model) is NEVER auto-executed -> downgraded to suggestion
    out_mem = daemon.ingest({"kind": "pressure", "cpu": 0.2, "memory": 0.92})
    assert len(out_mem) == 1
    assert out_mem[0]["kind"] == "suggestion"
    assert out_mem[0]["suggestion"]["tool"] == "offload_model"

    # 4. eBPF JSONL consumption updates active_app world state
    stream = tmp_path / "events.jsonl"
    stream.write_text(json.dumps({"uid": 1000, "comm": "code", "event_type": 1}) + "\n", encoding="utf-8")
    daemon.poll_ebpf_once(stream)
    assert daemon.state.active_app == "code"


def test_guarded_self_improver_pcec_sandbox_and_security_rejection(tmp_path: Path):
    graph = TaskGraph(tmp_path / "tasks.db")
    try:
        graph.insert_task("f1", None, "parse invoice pdf", node_type="agent", status="failed")
        graph._conn.execute("UPDATE tasks SET last_error='timeout', updated_at=datetime('now') WHERE id='f1'")
        graph._conn.commit()

        class _PassSandbox:
            def run_python(self, code: str, timeout: int = 30) -> bool:
                return "print(" in code

        # 1. Valid skill proposal tested in sandbox -> staged as 'pending' (never auto-approved)
        good_llm = _MockLLM(
            json.dumps(
                {
                    "type": "new_skill",
                    "payload": {"name": "invoice_helper", "code": "print('ok')"},
                }
            )
        )
        imp_ok = SelfImprover(graph, good_llm, _PassSandbox())
        res = imp_ok.run_nightly()
        assert res is not None
        assert res["status"] == "pending"
        assert res["test_result"] == "passed"
        assert len(imp_ok.list_proposals(status="pending")) == 1

        # 2. Malicious proposal trying to touch .ssh or weaken tier1_auto_tools -> rejected automatically
        evil_llm = _MockLLM(
            json.dumps(
                {
                    "type": "new_skill",
                    "payload": {"name": "evil", "code": "open('~/.ssh/id_rsa').read()"},
                }
            )
        )
        imp_bad = SelfImprover(graph, evil_llm, _PassSandbox())
        res_bad = imp_bad.run_nightly()
        assert res_bad is not None
        assert res_bad["status"] == "rejected"
        assert "rejected_safety" in res_bad["test_result"]
    finally:
        graph.close()


def test_autonomy_dbus_router_units_and_submit_goal_integration(tmp_path: Path, monkeypatch):
    from celina_pkg import config as celina_config
    from celina_pkg.router import CelinaRouter

    monkeypatch.setattr(celina_config, "BASE_DIR", tmp_path)

    # 1. D-Bus introspection includes org.macubuntu.Celina.Autonomy + policy XML validates
    intro = ET.fromstring(INTROSPECTION_XML)
    auto_iface = intro.find(f"./interface[@name='{AUTONOMY_INTERFACE_NAME}']")
    assert auto_iface is not None
    assert {m.attrib["name"] for m in auto_iface.findall("method")} == {"SubmitGoal", "GetTaskGraph", "CancelTask"}
    assert {s.attrib["name"] for s in auto_iface.findall("signal")} == {"TaskStateChanged", "MCPActionStarted"}

    policy_txt = (REPO_ROOT / "system" / "dbus" / "org.macubuntu.Celina.conf").read_text(encoding="utf-8")
    assert ET.fromstring(policy_txt).tag == "busconfig"
    assert AUTONOMY_INTERFACE_NAME in policy_txt

    # 2. Peer-UID enforcement on SubmitGoal / GetTaskGraph / CancelTask
    backend = CelinaDBusBackend(expected_uid=1000)
    assert json.loads(backend.submit_goal("plan trip", caller_uid=2000))["ok"] is False
    root_id = backend.submit_goal("plan trip", caller_uid=1000)
    assert len(root_id) == 36  # UUID
    tg = json.loads(backend.get_task_graph(caller_uid=1000))
    assert tg["ok"] is True and tg["evidence_ok"] is True
    canc = json.loads(backend.cancel_task(root_id, caller_uid=1000))
    assert canc["ok"] is True and canc["cancelled_count"] >= 1

    # 3. CelinaRouter.route_for_autonomy() produces structured decision dict
    rt = CelinaRouter()
    dec = rt.route_for_autonomy("check cpu and ram stats")
    assert {"tool_name", "arguments", "arguments_hash", "tier", "domain", "why_receipt_hash", "gear"} <= set(dec.keys())
    assert len(dec["arguments_hash"]) == 64

    # 4. Systemd v5 units exist, are userns-safe, and autonomy defaults to observe-only
    auto_unit = (REPO_ROOT / "system" / "systemd" / "celina-autonomy.service").read_text(encoding="utf-8")
    active_lines = [ln.strip() for ln in auto_unit.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert any("CELINA_AUTONOMY_OBSERVE_ONLY=1" in ln for ln in active_lines)
    assert "NoNewPrivileges=yes" in active_lines
    assert not any(ln.startswith("ProtectSystem=strict") for ln in active_lines)
    pro_unit = (REPO_ROOT / "system" / "systemd" / "celina-proactive.service").read_text(encoding="utf-8")
    assert "RestrictAddressFamilies=AF_UNIX" in pro_unit
    assert (REPO_ROOT / "system" / "systemd" / "celina-self-improve.service").is_file()
    assert "OnCalendar=daily" in (REPO_ROOT / "system" / "systemd" / "celina-self-improve.timer").read_text(encoding="utf-8")

    # 5. Super+Shift+A GNOME shortcut is wired in Stage 25
    dock_sh = (REPO_ROOT / "stages" / "25-desktop-dock.sh").read_text(encoding="utf-8")
    assert '"<Super><Shift>a"' in dock_sh
    assert "celina-autonomy --status-notify" in dock_sh


def test_tasks_db_wal_4thread_concurrency_and_state_ops_autonomy_cli(tmp_path: Path, monkeypatch, capsys):
    """4-thread SQLite WAL stress test on tasks.db + state_ops autonomy CLI + export_state."""
    import concurrent.futures
    import tarfile
    import uuid
    from celina_pkg import config as celina_config
    from celina_pkg import state_ops

    monkeypatch.setattr(celina_config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(celina_config, "CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "tasks.db"
    graph = TaskGraph(db_path)

    errors: list[str] = []

    def _worker(tid: int) -> int:
        done = 0
        try:
            for i in range(25):
                task_id = str(uuid.uuid4())
                graph.insert_task(task_id, None, f"thread-{tid}-step-{i}", "agent", priority=tid % 5)
                graph.append_evidence(task_id, "get_system_stats", f"h{tid}-{i}", 0, None, f"w{tid}-{i}")
                claimed = graph.next_runnable()
                if claimed is not None:
                    graph.mark(claimed["id"], "done", result=f"ok-{tid}-{i}")
                    done += 1
        except Exception as exc:
            errors.append(f"t{tid}: {type(exc).__name__}: {exc}")
        return done

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_worker, t) for t in range(4)]
        counts = [f.result() for f in futs]

    assert not errors, f"WAL concurrency errors on tasks.db: {errors}"
    assert sum(counts) >= 80
    ev_ok, ev_msg, ev_count = graph.verify_evidence_chain()
    assert ev_ok is True and ev_count == 100 and "Verified 100" in ev_msg
    graph.close()

    # Verify `celina autonomy --submit / --status / --cancel / --proposals` CLI in state_ops
    assert state_ops.run_cli(["autonomy", "--submit", "Audit weekly backups"]) == 0
    sub_out = json.loads(capsys.readouterr().out)
    assert sub_out["ok"] is True and len(sub_out["root_task_id"]) == 36

    assert state_ops.run_cli(["autonomy", "--status"]) == 0
    st_out = json.loads(capsys.readouterr().out)
    assert st_out["evidence_ok"] is True
    assert st_out["counts"].get("pending", 0) >= 1

    assert state_ops.run_cli(["autonomy", "--cancel", sub_out["root_task_id"]]) == 0
    can_out = json.loads(capsys.readouterr().out)
    assert can_out["ok"] is True and can_out["cancelled_count"] >= 1

    assert state_ops.run_cli(["autonomy", "--proposals"]) == 0
    prop_out = json.loads(capsys.readouterr().out)
    assert isinstance(prop_out, list)

    # Verify export_state includes tasks.db cleanly via WAL-safe sqlite3.backup
    archive = tmp_path / "export.tar.gz"
    res = state_ops.export_state(archive, base_dir=tmp_path, config_dir=tmp_path / "cfg")
    assert Path(res["archive_path"]).is_file()
    with tarfile.open(archive, "r:gz") as tf:
        assert "data/tasks.db" in tf.getnames()

