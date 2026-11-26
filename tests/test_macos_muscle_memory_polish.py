"""
Suite 23: macOS Muscle Memory, System Polish & Celina Coordination Glue Tests.

Covers:
  1. keyd macOS remap (leftmeta/rightmeta/capslock/Cmd+Space/Mission Control) + TerminalKeyboardContext
  2. Window Management (fixed Spaces dynamic-workspaces=false, Mission Control Ctrl+Up,
     Tiling Assistant active-screen-edges + restore-window-size-on-unsnap, WorkspaceMemoryManager)
  3. File Management (FinderTagStore xattr + SQLite fallback, FileHabitAdvisor 3-open suggestion,
     gnome-sushi + FrostFinder + BorgBackup in Stage 45)
  4. Focus Modes & Per-App Notification Filtering (gsettings show-banners + calendar meeting trigger)
  5. Unified Blue Accent Color, Auto Dark Mode (celina-auto-theme.sh), and Nightly BorgBackup Automation
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_pkg import macos_glue  # noqa: E402


def test_keyd_macos_muscle_memory_and_terminal_keyboard_context():
    keyd_conf = (REPO_ROOT / "system/keyd/macubuntu.conf").read_text(encoding="utf-8")
    assert "leftmeta = layer(macos_cmd)" in keyd_conf
    assert "rightmeta = layer(macos_cmd)" in keyd_conf
    assert "capslock = timeout(macro(leftshift), 300, capslock)" in keyd_conf
    assert "space = M-space" in keyd_conf
    assert "up = C-up" in keyd_conf

    # Terminal context preserves physical Ctrl+C as SIGINT while supporting both C-insert/S-insert/S-delete and C-S-c
    term_ins = macos_glue.resolve_cmd_c_behavior("org.gnome.Ptyxis — bash", terminal_mode="insert")
    assert term_ins["is_terminal"] is True
    assert term_ins["cmd_c_sequence"] == "C-insert"
    assert term_ins["cmd_v_sequence"] == "S-insert"
    assert term_ins["cmd_x_sequence"] == "S-delete"
    assert term_ins["physical_ctrl_c"] == "SIGINT"

    term_cs = macos_glue.resolve_cmd_c_behavior("org.gnome.Ptyxis — bash", terminal_mode="ctrl_shift")
    assert term_cs["cmd_c_sequence"] == "C-S-c" and term_cs["physical_ctrl_c"] == "SIGINT"

    browser_ctx = macos_glue.resolve_cmd_c_behavior("Mozilla Firefox")
    assert browser_ctx["is_terminal"] is False
    assert browser_ctx["cmd_c_sequence"] == "C-c"


def test_window_spaces_tiling_workspace_memory_and_focus_controller(tmp_path: Path):
    stage25 = (REPO_ROOT / "stages/25-desktop-dock.sh").read_text(encoding="utf-8")
    assert "org.gnome.mutter dynamic-workspaces false" in stage25
    assert "org.gnome.desktop.wm.preferences num-workspaces 4" in stage25
    assert "<Control>Up" in stage25
    assert "active-screen-edges true" in stage25
    assert "restore-window-size-on-unsnap true" in stage25
    assert "accent-color \"'blue'\"" in stage25

    # Persistent Workspace Spatial Memory across reload
    ws_path = tmp_path / "ws.json"
    wm = macos_glue.WorkspaceMemoryManager(state_path=ws_path, num_workspaces=4)
    wm.assign_app("firefox", 1)
    wm.assign_app("code", 2)
    wm2 = macos_glue.WorkspaceMemoryManager(state_path=ws_path, num_workspaces=4)
    assert wm2.workspace_for_app("firefox") == 1
    plan = wm2.build_restore_plan(
        [
            {"id": "w1", "app_class": "firefox", "workspace": 0},
            {"id": "w2", "app_class": "code", "workspace": 2},
        ]
    )
    assert len(plan) == 1 and plan[0]["app_class"] == "firefox" and plan[0]["to_workspace"] == 1

    # FocusModeController toggles gsettings show-banners + enforces per-app allowlist
    recorded_cmds: list[list[str]] = []
    fc = macos_glue.FocusModeController(gsettings_runner=lambda cmd: recorded_cmds.append(cmd))
    res = fc.evaluate_calendar_focus(active_events=[{"title": "Daily Standup"}], apply_gsettings=True)
    assert res["mode"] == "meeting" and res["show_banners"] is False
    assert recorded_cmds[-1] == ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "false"]
    assert fc.allow_notification("gnome-calendar") is True
    assert fc.allow_notification("discord", urgency=1) is False
    assert fc.allow_notification("discord", urgency=2) is True  # critical alert passes through


def test_finder_tags_file_habits_auto_dark_mode_and_borg_backup(tmp_path: Path):
    # 1. FinderTagStore: xattr + SQLite fallback + 3-open habit suggestion
    sample_doc = tmp_path / "roadmap.docx"
    sample_doc.write_text("Q4 plan", encoding="utf-8")
    store = macos_glue.FinderTagStore(db_path=tmp_path / "tags.db", prefer_xattr=True)
    try:
        for i in range(2):
            r = store.record_file_open(sample_doc, suggest_threshold=3)
            assert r["suggestion"] is None
        r3 = store.record_file_open(sample_doc, suggest_threshold=3)
        assert r3["suggestion"] is not None
        assert r3["suggestion"]["suggested_tag"] == "Blue"
        assert r3["suggestion"]["suggested_quick_action"] == "Convert to PDF"

        tag_res = store.add_tag(sample_doc, "Blue")
        assert tag_res["storage_backend"] in {"xattr", "sqlite_fallback"}
        assert "Blue" in store.get_tags(sample_doc)
        assert str(sample_doc.resolve()) in store.find_by_tag("blue")
    finally:
        store.close()

    # 2. Diff-gated Auto Dark Mode script toggles cleanly AND skips thrashing when unchanged!
    day = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/celina-auto-theme.sh"), "--dry-run", "--hour", "11", "--current", "prefer-dark"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert day.returncode == 0 and "'prefer-light'" in day.stdout
    day_noop = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/celina-auto-theme.sh"), "--dry-run", "--hour", "11", "--current", "prefer-light"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert day_noop.returncode == 0 and "unchanged:" in day_noop.stdout
    night = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/celina-auto-theme.sh"), "--dry-run", "--hour", "22", "--current", "prefer-light"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert night.returncode == 0 and "'prefer-dark'" in night.stdout

    # 3. Nightly BorgBackup script dry-run + 3h RandomizedDelaySec + BackupHealthMonitor (>48h Dynamic Island badge)
    borg_timer = (REPO_ROOT / "system/systemd/celina-borg-backup.timer").read_text(encoding="utf-8")
    assert "RandomizedDelaySec=3h" in borg_timer and "Persistent=true" in borg_timer

    bmon = macos_glue.BackupHealthMonitor(stale_threshold_hours=48.0)
    now_ep = 1_760_000_000.0
    ok_health = bmon.evaluate_timers(
        [
            {"unit": "timeshift-daily.timer", "last_epoch": now_ep - 3600 * 6},
            {"unit": "celina-borg-backup.timer", "last_epoch": now_ep - 3600 * 12},
        ],
        now_epoch=now_ep,
    )
    assert ok_health["healthy"] is True and ok_health["island_badge"] is None

    stale_health = bmon.evaluate_timers(
        [
            {"unit": "timeshift-daily.timer", "last_epoch": now_ep - 3600 * 60},
            {"unit": "borgmatic.timer", "last_epoch": now_ep - 3600 * 12},
        ],
        now_epoch=now_ep,
    )
    assert stale_health["healthy"] is False
    assert stale_health["island_badge"]["badge"] == "backup_overdue"
    assert stale_health["overdue_timers"][0]["family"] == "timeshift"

    # 4. Ubuntu 26.04 GNOME Sushi >= 50.0 gate, Simple Taskbar Dock-mode rule & Extension ABI pinning
    assert macos_glue.check_gnome_sushi_version("50.0-1ubuntu1")["ok"] is True
    assert macos_glue.check_gnome_sushi_version("46.0-2")["ok"] is False
    assert macos_glue.validate_simple_taskbar_config(mode="dock", show_app_labels=False)["magnification_available"] is True
    assert macos_glue.validate_simple_taskbar_config(mode="panel", show_app_labels=False)["magnification_available"] is False
    assert macos_glue.validate_simple_taskbar_config(mode="dock", show_app_labels=True)["magnification_available"] is False
    assert macos_glue.check_extension_abi("simple-taskbar", 50)["compatible"] is True
    assert macos_glue.check_extension_abi("stage-manager-for-linux", 47)["compatible"] is False

    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    skip_run = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/celina-borg-backup.sh"), "--dry-run"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(empty_home)},
        timeout=10,
    )
    assert skip_run.returncode == 0 and "BorgBackup skipped" in skip_run.stdout

    (empty_home / "Documents").mkdir()
    borg_env = tmp_path / "borg.env"
    borg_env.write_text('BORG_REPO="/mnt/backup/celina-borg"\n', encoding="utf-8")
    active_run = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/celina-borg-backup.sh"), "--dry-run", "--config", str(borg_env)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(empty_home)},
        timeout=10,
    )
    assert active_run.returncode == 0
    assert "borg create" in active_run.stdout and "borg prune" in active_run.stdout

    # 4. User systemd units keep Ubuntu 26.04 userns safety (no ProtectSystem=strict)
    for unit_name in ("celina-auto-theme.service", "celina-borg-backup.service"):
        txt = (REPO_ROOT / "system/systemd" / unit_name).read_text(encoding="utf-8")
        assert "ProtectSystem=strict" not in txt
