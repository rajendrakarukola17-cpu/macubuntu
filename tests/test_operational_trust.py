"""
Suite 9: Operational Trust, Disaster Recovery, Undo, Astra Checkpoints, Upgrade Diff,
Locale Audit & Public Honesty Contract Tests.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

from celina_pkg import (
    approval,
    cache,
    computer_use,
    config,
    documents,
    memory,
    state_ops,
    tools,
)


def _run_bash(cmd: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=60, env=env)


def test_celina_export_import_wal_safe_and_tamper_evident(tmp_path: Path):
    src_base = tmp_path / "src_data"
    src_cfg = tmp_path / "src_cfg"
    src_base.mkdir()
    src_cfg.mkdir()

    # Populate WAL SQLite DBs, trusted/quarantined skills, schema, and a secret env file
    mem = memory.MemoryDB(db_path=src_base / "memory.db")
    mem.set_fact("favorite_editor", "neovim")
    mem.add_knowledge_triple("macubuntu", "version", "4.4")
    sc = cache.SmartCache(db_path=src_base / "cache.db")
    sc.set("test_ns", "ping", "pong")
    try:
        (src_base / "skills" / "trusted").mkdir(parents=True)
        (src_base / "skills" / "trusted" / "morning.md").write_text("# Morning Routine\n", encoding="utf-8")
        (src_base / "skills" / "quarantine").mkdir(parents=True)
        (src_base / "skills" / "quarantine" / "untrusted.md").write_text("# Untrusted\n", encoding="utf-8")
        (src_cfg / "neural-schema.json").write_text(
            json.dumps({"schema_version": "4.4", "persona": {"name": "Celina"}}), encoding="utf-8"
        )
        (src_cfg / "env").write_text("OPENROUTER_API_KEY=sk-or-v1-SUPERSECRET\n", encoding="utf-8")

        # 1. Export while SQLite WAL connections are still open (tests online backup API!)
        arc_path = tmp_path / "exports" / "celina-backup.tar.gz"
        exp = state_ops.export_state(
            arc_path, include_secrets=False, base_dir=src_base, config_dir=src_cfg
        )
        assert arc_path.is_file()
        assert Path(exp["sha256_file"]).is_file()
        assert stat.S_IMODE(arc_path.stat().st_mode) == 0o600
        assert exp["includes_secrets"] is False

        # Ensure raw secrets (`config/env`) were NOT exported by default
        with tarfile.open(arc_path, "r:gz") as tf:
            names = tf.getnames()
            assert "MANIFEST.json" in names
            assert "data/memory.db" in names
            assert "data/skills/trusted/morning.md" in names
            assert "config/neural-schema.json" in names
            assert "config/env" not in names
    finally:
        mem.close()
        sc.close()

    # 2. Dry-run import into empty destination writes ZERO bytes
    dst_base = tmp_path / "dst_data"
    dst_cfg = tmp_path / "dst_cfg"
    dry = state_ops.import_state(arc_path, dry_run=True, base_dir=dst_base, config_dir=dst_cfg)
    assert dry["verified"] is True and dry["dry_run"] is True
    assert not dst_base.exists() and not dst_cfg.exists()

    # 3. Live import restores WAL SQLite DB + KG + skills + schema byte-for-byte with 0600 perms
    res = state_ops.import_state(arc_path, dry_run=False, base_dir=dst_base, config_dir=dst_cfg)
    assert res["verified"] is True and res["file_count"] == exp["file_count"]
    assert (dst_base / "skills" / "trusted" / "morning.md").read_text(encoding="utf-8") == "# Morning Routine\n"
    assert stat.S_IMODE((dst_base / "memory.db").stat().st_mode) == 0o600

    restored_mem = memory.MemoryDB(db_path=dst_base / "memory.db")
    try:
        facts, _eps = restored_mem.recall_relevant("favorite_editor", max_facts=5)
        assert any("neovim" in v for _k, v in facts)
        triples = restored_mem.recall_knowledge_triples("macubuntu")
        assert any("4.4" in str(t) for t in triples)
    finally:
        restored_mem.close()

    # 4. Re-importing over existing state creates an automatic pre-import safety backup
    res2 = state_ops.import_state(arc_path, dry_run=False, base_dir=dst_base, config_dir=dst_cfg)
    assert res2["safety_backup"] and Path(res2["safety_backup"]).is_file()

    # 5. Tamper test: flip 1 byte in a payload file inside a forged archive -> MUST reject
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(arc_path, "r:gz") as zin, tarfile.open(tampered, "w:gz") as zout:
        for m in zin.getmembers():
            raw = zin.extractfile(m).read()
            if m.name == "data/skills/trusted/morning.md":
                raw = raw + b"TAMPERED"
            ti = tarfile.TarInfo(name=m.name)
            ti.size = len(raw)
            ti.mode = 0o600
            zout.addfile(ti, io.BytesIO(raw))

    with pytest.raises(ValueError, match="SHA-256 verification FAILED"):
        state_ops.import_state(tampered, dry_run=False, base_dir=tmp_path / "t_base", config_dir=tmp_path / "t_cfg")

    # 6. Tar-Slip defense: archive containing ../escape.txt MUST be rejected
    evil_arc = tmp_path / "evil.tar.gz"
    with tarfile.open(evil_arc, "w:gz") as zout:
        ti = tarfile.TarInfo(name="../escape.txt")
        ti.size = 4
        zout.addfile(ti, io.BytesIO(b"evil"))
    with pytest.raises(ValueError, match="Tar-Slip"):
        state_ops.import_state(evil_arc, dry_run=False, base_dir=tmp_path / "e_base", config_dir=tmp_path / "e_cfg")


def test_celina_undo_pre_execution_backups_and_atomic_rollback(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    undo_dir = fake_home / ".local" / "share" / "celina" / "undo"
    backups_dir = fake_home / ".local" / "share" / "celina" / "backups"
    journal = state_ops.UndoJournal(undo_dir=undo_dir, home_root=fake_home)

    monkeypatch.setattr(config, "HOME", fake_home)
    monkeypatch.setattr(config, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(state_ops, "_DEFAULT_UNDO_JOURNAL", journal)

    approve_all = lambda _title, _desc: True

    # 1. write_file creating a brand-new file -> undo removes it cleanly
    f_new = fake_home / "notes.txt"
    out_new = tools.write_file(str(f_new), "hello v1\n", approval_cb=approve_all)
    assert "[undo: act_" in out_new and f_new.read_text(encoding="utf-8") == "hello v1\n"
    ok, msg = journal.undo_action()
    assert ok and not f_new.exists(), msg

    # 2. edit_file modifying an existing file -> undo restores byte-for-byte original
    f_edit = fake_home / "report.md"
    f_edit.write_text("Quarter: Q3\nRevenue: 100\n", encoding="utf-8")
    out_ed = tools.edit_file(str(f_edit), "Revenue: 100", "Revenue: 999", approval_cb=approve_all)
    assert "[undo: act_" in out_ed and "Revenue: 999" in f_edit.read_text(encoding="utf-8")
    ok, msg = journal.undo_action()
    assert ok and f_edit.read_text(encoding="utf-8") == "Quarter: Q3\nRevenue: 100\n", msg

    # 3. move_file overwriting an existing destination -> undo moves back AND restores overwritten dst!
    f_src = fake_home / "draft.txt"
    f_dst = fake_home / "final.txt"
    f_src.write_text("draft content", encoding="utf-8")
    f_dst.write_text("original final", encoding="utf-8")
    out_mv = tools.move_file(str(f_src), str(f_dst), approval_cb=approve_all)
    assert "[undo: act_" in out_mv and not f_src.exists() and f_dst.read_text(encoding="utf-8") == "draft content"
    ok, msg = journal.undo_action()
    assert ok and f_src.read_text(encoding="utf-8") == "draft content" and f_dst.read_text(encoding="utf-8") == "original final", msg

    # 4. create_document & edit_document (plain/md) -> undo works identically
    f_doc = fake_home / "brief.md"
    out_cd = documents.create_document(str(f_doc), title="Brief", content="Line 1", approval_cb=approve_all)
    assert "[undo: act_" in out_cd and f_doc.is_file()
    out_edoc = documents.edit_document(str(f_doc), find_text="Line 1", replace_text="Line 2", approval_cb=approve_all)
    assert "[undo: act_" in out_edoc and "Line 2" in f_doc.read_text(encoding="utf-8")
    ok, _ = journal.undo_action()
    assert ok and "Line 1" in f_doc.read_text(encoding="utf-8")

    # 5. Tamper check on undo blob: corrupting snapshot aborts rollback without touching live file
    f_tamper = fake_home / "precious.txt"
    f_tamper.write_text("original-safe", encoding="utf-8")
    tools.edit_file(str(f_tamper), "original-safe", "edited-v2", approval_cb=approve_all)
    latest = journal.list_actions(limit=1)[0]
    blob_p = journal.blobs_dir / latest["blob_file"]
    blob_p.write_text("CORRUPTED_BYTES", encoding="utf-8")
    ok, err_msg = journal.undo_action(latest["action_id"])
    assert not ok and "integrity check FAILED" in err_msg
    assert f_tamper.read_text(encoding="utf-8") == "edited-v2"


def test_project_astra_durable_step_checkpointing_and_interruption_recovery(tmp_path: Path, monkeypatch):
    ckpt_dir = tmp_path / "checkpoints"
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(
        computer_use.automation,
        "perceive_desktop",
        lambda max_chars=3000: ("Window: Files | Button: Open @(100,100) | secret ghp_1234567890abcdef1234567890abcdef1234", "fp1"),
    )
    monkeypatch.setattr(
        computer_use.automation,
        "get_active_window_info",
        lambda: {"app": "nautilus", "title": "Files"},
    )
    monkeypatch.setattr(computer_use.time, "sleep", lambda _s: None)

    # Phase 1: Step 1 succeeds, Step 2 raises RuntimeError simulating crash/suspend
    call_n = {"n": 0}

    def planner_crash_on_2(_prompt, **_kw):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return {"action": "wait", "args": {"seconds": 0.2}, "thought": "wait with token ghp_1234567890abcdef1234567890abcdef1234"}, None
        raise RuntimeError("simulated suspend interruption")

    monkeypatch.setattr(computer_use, "_query_planner", planner_crash_on_2)

    with pytest.raises(RuntimeError, match="simulated suspend"):
        computer_use.autonomous_gui_task(
            goal="Organize invoices",
            executor=lambda _t, _a, _c: "ok",
            max_steps=5,
            approval_cb=lambda _t, _d: True,
            checkpoints_dir=ckpt_dir,
        )

    listed = computer_use.list_checkpoints(limit=10, checkpoints_dir=ckpt_dir)
    assert len(listed) == 1
    tid = listed[0]["task_id"]
    assert listed[0]["status"] == "interrupted"
    assert listed[0]["completed_steps"] == 1

    loaded = computer_use.load_checkpoint(tid, checkpoints_dir=ckpt_dir)
    assert loaded is not None
    assert "ghp_1234567890" not in json.dumps(loaded), "Secrets must be redacted in Astra checkpoints!"

    # Phase 2: Resume from checkpoint and complete cleanly
    monkeypatch.setattr(
        computer_use,
        "_query_planner",
        lambda _p, **_kw: ({"action": "done", "args": {"summary": "Invoices organized"}, "thought": "finished"}, None),
    )
    monkeypatch.setattr(config, "REFLECTION_POLICY", {"verify_astra_done_state": False})

    out = computer_use.autonomous_gui_task(
        goal="Organize invoices",
        executor=lambda _t, _a, _c: "ok",
        max_steps=5,
        approval_cb=lambda _t, _d: True,
        resume_checkpoint_id=tid,
        checkpoints_dir=ckpt_dir,
    )
    assert "✅ Astra completed mission" in out and tid in out
    final_ckpt = computer_use.load_checkpoint(tid, checkpoints_dir=ckpt_dir)
    assert final_ckpt is not None and final_ckpt["status"] == "completed"


def test_celina_migrate_diff_and_fail_closed_tier2_promotion(repo_root: Path, tmp_path: Path):
    schema_file = tmp_path / "cfg" / "neural-schema.json"
    schema_file.parent.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Simulated older v4.3 schema where write_file/edit_file were Tier-1 and persona was customized
    old_v43 = {
        "schema_version": "4.3",
        "persona": {"name": "Nova", "nickname": "Novi"},
        "security_policy": {
            "tier1_auto_tools": ["get_system_info", "write_file", "edit_file"],
            "tier2_approval_tools": ["run_shell"],
            "protected_paths": ["/etc"],
        },
        "tool_domains": {"core": {"tools": ["get_system_info"]}},
    }
    raw_before = json.dumps(old_v43, indent=2)
    schema_file.write_text(raw_before, encoding="utf-8")

    template = repo_root / "features" / "celina" / "config" / "neural-schema.json"

    # 1. Dry-run migrate detects schema upgrade, newly added tools & Tier 1 -> Tier 2 promotion with 0 writes
    dry_rep = state_ops.migrate_state(
        dry_run=True, schema_path=schema_file, template_schema_path=template, base_dir=data_dir
    )
    assert dry_rep["schema_version_before"] == "4.3"
    assert dry_rep["schema_version_after"] == "4.4"
    assert len(dry_rep["tools_added"]) > 50
    promoted = {tc["tool"]: (tc["from"], tc["to"]) for tc in dry_rep["tier_changes"]}
    assert promoted.get("write_file") == ("Tier 1 (auto)", "Tier 2 (approval)")
    assert promoted.get("edit_file") == ("Tier 1 (auto)", "Tier 2 (approval)")
    assert schema_file.read_text(encoding="utf-8") == raw_before, "--dry-run migrate MUST NOT modify disk!"

    # 2. Live migrate upgrades schema, preserves custom persona "Nova", enforces Tier-2 precedence, and migrates DBs
    live_rep = state_ops.migrate_state(
        dry_run=False, schema_path=schema_file, template_schema_path=template, base_dir=data_dir
    )
    assert live_rep["dry_run"] is False
    upgraded = json.loads(schema_file.read_text(encoding="utf-8"))
    assert upgraded["schema_version"] == "4.4"
    assert upgraded["persona"]["name"] == "Nova" and upgraded["persona"]["nickname"] == "Novi"
    assert "write_file" in upgraded["security_policy"]["tier2_approval_tools"]
    assert "write_file" not in upgraded["security_policy"]["tier1_auto_tools"]
    assert (data_dir / "memory.db").is_file() and (data_dir / "spotlight_index.db").is_file()


def test_locale_audit_and_upgrade_simulation_diff_zero_side_effects(repo_root: Path, tmp_path: Path):
    fake_home = tmp_path / "loc_home"
    fake_home.mkdir()
    empty_locale_dir = tmp_path / "empty_locale"
    empty_locale_dir.mkdir()

    # 1. Non-English locale with missing at-spi2-core.mo MUST surface explicit warning + fallback + 0 writes
    r_te = _run_bash(
        f'bash "{repo_root}/install.sh" --locale',
        env_extra={
            "HOME": str(fake_home),
            "LC_ALL": "te_IN.UTF-8",
            "MACUBUNTU_LOCALE_DIR": str(empty_locale_dir),
        },
    )
    assert r_te.returncode == 0, r_te.stderr
    assert "AT-SPI2 Localization Gap" in r_te.stdout
    assert "canonical AT-SPI2 Role enums" in r_te.stdout
    assert "Native offline Piper neural voice available for 'te'" in r_te.stdout
    assert list(fake_home.iterdir()) == []

    # 2. English locale passes cleanly with 0 writes
    r_en = _run_bash(
        f'bash "{repo_root}/install.sh" --locale',
        env_extra={"HOME": str(fake_home), "LC_ALL": "C.UTF-8"},
    )
    assert r_en.returncode == 0
    assert "English/POSIX locale" in r_en.stdout
    assert list(fake_home.iterdir()) == []

    # 3. --dry-run --upgrade prints apt --simulate style diff without mutating existing v4.3 config
    upg_home = tmp_path / "upg_home"
    cfg_dir = upg_home / ".config" / "celina"
    cfg_dir.mkdir(parents=True)
    old_schema = cfg_dir / "neural-schema.json"
    old_payload = json.dumps(
        {
            "schema_version": "4.3",
            "security_policy": {"tier1_auto_tools": ["write_file"], "tier2_approval_tools": []},
        }
    )
    old_schema.write_text(old_payload, encoding="utf-8")

    r_upg = _run_bash(
        f'bash "{repo_root}/install.sh" --dry-run --upgrade --only 00',
        env_extra={"HOME": str(upg_home)},
    )
    assert r_upg.returncode == 0, r_upg.stderr
    assert "Upgrade Simulation Diff" in r_upg.stdout
    assert "Celina Migration Report [DRY-RUN" in r_upg.stdout
    assert "write_file: Tier 1 (auto) -> Tier 2 (approval)" in r_upg.stdout
    assert "GNOME Extension Pin Diff" in r_upg.stdout
    assert "OS Config Drop-In Diff" in r_upg.stdout
    assert old_schema.read_text(encoding="utf-8") == old_payload, "--dry-run --upgrade MUST NOT mutate disk!"


def test_limitations_and_public_contract_honesty(repo_root: Path):
    lim = repo_root / "LIMITATIONS.md"
    assert lim.is_file(), "LIMITATIONS.md missing!"
    txt = lim.read_text(encoding="utf-8")
    for required_topic in (
        "Secure Enclave",
        "ECID",
        "BlueBubbles",
        "True Tone",
        "AirPlay 2",
        "iwlwifi",
        "ath9k",
        "Disposable Apple ID",
        "at-spi2-core",
        "celina-export",
        "celina undo",
    ):
        assert required_topic in txt, f"LIMITATIONS.md missing honest disclosure for: {required_topic}"

    for doc_name in ("README.md", "ARCHITECTURE.md", "COMPATIBILITY.md"):
        assert "LIMITATIONS.md" in (repo_root / doc_name).read_text(encoding="utf-8"), (
            f"{doc_name} must link LIMITATIONS.md"
        )
