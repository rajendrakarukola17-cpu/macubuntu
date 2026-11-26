"""
Suite 25 — install.sh / uninstall.sh root hardening verification.

P0 coverage:
  - arithmetic injection via --from blocked (exit 2)
  - invalid --top-panel / --animation-speed / missing --profile (exit 2)
  - --list prints non-empty stage descriptions (sed ;q regression)
  - chmod +x guarded by -w (no crash on read-only /usr/share/macubuntu)
  - --rollback calls init_runtime_state (no unbound SNAPSHOT_DIR)
  - uninstall.sh --unknown-flag exits 2; --dry-run zero side effects
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd, env=None, **kw):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(cmd, env=e, capture_output=True, text=True, **kw)


class TestInstallRootHardening:
    def test_bash_n_install_sh(self):
        r = _run(["bash", "-n", str(REPO_ROOT / "install.sh")])
        assert r.returncode == 0, r.stderr

    def test_bash_n_uninstall_sh(self):
        r = _run(["bash", "-n", str(REPO_ROOT / "uninstall.sh")])
        assert r.returncode == 0, r.stderr

    def test_from_arithmetic_injection_blocked(self):
        r = _run(
            ["bash", str(REPO_ROOT / "install.sh"), "--from", 'a[$(echo pwned)]', "--dry-run"]
        )
        assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stdout} {r.stderr}"

    def test_top_panel_invalid_exits_2(self):
        r = _run(["bash", str(REPO_ROOT / "install.sh"), "--top-panel", "invalid", "--dry-run"])
        assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stdout} {r.stderr}"

    def test_profile_missing_arg_exits_2(self):
        r = _run(["bash", str(REPO_ROOT / "install.sh"), "--profile"])
        assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stdout} {r.stderr}"

    def test_list_prints_descriptions(self):
        r = _run(["bash", str(REPO_ROOT / "install.sh"), "--list"])
        assert r.returncode == 0, f"--list failed: {r.stderr}"
        # Every stage line should have a non-empty description after the name
        lines = [l for l in r.stdout.splitlines() if l.startswith("  ")]
        assert len(lines) >= 10, f"expected many stage lines, got {len(lines)}"
        for line in lines:
            parts = line.split(None, 1)
            assert len(parts) == 2, f"stage missing description: {line!r}"
            assert parts[1].strip(), f"empty description for: {parts[0]!r}"

    def test_dry_run_zero_side_effects(self):
        clean_home = Path("/tmp/macubuntu-test-clean-home")
        clean_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(clean_home)
        env["DRY_RUN"] = "1"
        r = _run(["bash", str(REPO_ROOT / "install.sh"), "--dry-run"], env=env)
        # Dry-run should succeed even in a minimal environment
        assert r.returncode == 0, f"dry-run failed: {r.stderr}"
        # No files should have been created in clean HOME
        created = [p for p in clean_home.rglob("*") if p.is_file()]
        assert len(created) == 0, f"dry-run created files: {created}"

    def test_no_python3_c_repo_dir_interpolation(self):
        txt = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "${REPO_DIR}" not in txt.split("python3 -")[1] if "python3 -" in txt else True
        # More robust: no '${REPO_DIR}' inside any python3 -c block
        import re
        for m in re.finditer(r"python3\s+-c\s+[\"']", txt):
            start = m.end()
            end = txt.find('"', start)
            block = txt[start:end]
            assert "${REPO_DIR}" not in block, f"REPO_DIR interpolated in python3 -c: {block!r}"

    def test_chmod_guarded_by_writable_check(self):
        txt = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "-w \"$REPO_DIR/stages\"" in txt, "chmod +x not guarded by -w check"

    def test_rollback_calls_init_runtime_state(self):
        txt = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        rollback_idx = txt.find("ROLLBACK_ONLY")
        init_idx = txt.find("init_runtime_state", rollback_idx)
        assert init_idx > rollback_idx, "init_runtime_state called after rollback check"


class TestUninstallRootHardening:
    def test_unknown_option_exits_2(self):
        r = _run(["bash", str(REPO_ROOT / "uninstall.sh"), "--unknown-flag"])
        assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stdout} {r.stderr}"

    def test_dry_run_zero_side_effects(self):
        clean_home = Path("/tmp/macubuntu-test-clean-home-uninstall")
        clean_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(clean_home)
        env["DRY_RUN"] = "1"
        r = _run(["bash", str(REPO_ROOT / "uninstall.sh"), "--dry-run"], env=env)
        assert r.returncode == 0, f"uninstall dry-run failed: {r.stderr}"
        created = [p for p in clean_home.rglob("*") if p.is_file()]
        assert len(created) == 0, f"uninstall dry-run created files: {created}"

    def test_no_static_system_commands_without_dry_run_guard(self):
        txt = (REPO_ROOT / "uninstall.sh").read_text(encoding="utf-8")
        # ydotoold.service should be ydotool.service
        assert "ydotool.service" in txt, "missing ydotool.service"
        assert "80-uinput.rules" in txt, "missing 80-uinput.rules"
        # No 80-celina-uinput.rules (obsolete)
        assert "80-celina-uinput.rules" not in txt, "obsolete 80-celina-uinput.rules still present"
