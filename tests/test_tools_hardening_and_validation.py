#!/usr/bin/env python3
"""Tests for tools/ hardening & validation fixes (P0/P1 audit batch).

Covers:
  - validate_configs.py --file validates the user-supplied file (not repo copy)
  - os-hardening-setup.sh --dry-run creates zero files/dirs in $HOME
  - bash -n syntax checks on verify_perf_stack.sh and os-hardening-setup.sh
  - verify_perf_stack.sh checks wifi.powersave against 2 (not 1)
  - verify_perf_stack.sh references 45-apps-backups-search.sh for gnome-sushi
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. validate_configs.py --file bug fix
# ---------------------------------------------------------------------------
def test_validate_configs_file_validates_passed_file_not_repo_copy(tmp_path: Path):
    """--file <bad.json> must fail because it validates the passed file,
    not the hardcoded repo celina-capabilities.json."""
    # Create a temp capabilities file with a schema violation:
    # missing required "credential_access" and "pipewire" fields
    bad_cap = {
        "manifest_version": "1.0",
        "celina_version": "1.0",
        "enforcement_model": "deny",
        "never_exposed_paths": [],
        "tools": {
            "bad_tool": {
                "domain": "test",
                "tier": "tier1",
                "fs_scope": "none",
                "network_egress": "none",
                "auth_operation": "read",
                # MISSING: credential_access, pipewire (now required)
            }
        },
        "integrity": {
            "sha256": "0" * 64,
            "signed_by": "test",
            "schema_version": "1.0",
            "default_policy": "deny",
        },
    }
    bad_file = tmp_path / "celina-capabilities.json"
    bad_file.write_text(json.dumps(bad_cap, indent=2), encoding="utf-8")

    # Run validate_configs.py --file <bad_file> — must FAIL (exit 1)
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "validate_configs.py"),
         "--file", str(bad_file)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1, (
        f"Expected exit 1 for invalid --file, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "credential_access" in result.stderr or "pipewire" in result.stderr, (
        f"Schema should report missing required fields. stderr: {result.stderr}"
    )


def test_validate_configs_file_passes_for_valid_file(tmp_path: Path):
    """--file <valid.json> must pass (exit 0)."""
    good_cap = {
        "manifest_version": "1.0",
        "celina_version": "1.0",
        "enforcement_model": "deny",
        "never_exposed_paths": [],
        "tools": {
            "good_tool": {
                "domain": "test",
                "tier": "tier1",
                "fs_scope": "none",
                "network_egress": "none",
                "credential_access": "none",
                "pipewire": False,
                "auth_operation": "read",
            }
        },
        "integrity": {
            "sha256": "0" * 64,
            "signed_by": "test",
            "schema_version": "1.0",
            "default_policy": "deny",
        },
    }
    good_file = tmp_path / "celina-capabilities.json"
    good_file.write_text(json.dumps(good_cap, indent=2), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "validate_configs.py"),
         "--file", str(good_file)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"Expected exit 0 for valid --file, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# 2. os-hardening-setup.sh --dry-run zero side effects
# ---------------------------------------------------------------------------
def test_os_hardening_dry_run_zero_side_effects(tmp_path: Path):
    """DRY_RUN=1 must create zero files/dirs in $HOME."""
    # Use a clean temporary HOME
    clean_home = tmp_path / "home"
    clean_home.mkdir()

    env = {
        "HOME": str(clean_home),
        "XDG_CONFIG_HOME": str(clean_home / ".config"),
        "XDG_DATA_HOME": str(clean_home / ".local" / "share"),
        "DRY_RUN": "1",
        "PATH": "/usr/bin:/bin",
    }

    # Count files before
    before = set(clean_home.rglob("*"))

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "tools" / "os-hardening-setup.sh"), "--dry-run"],
        capture_output=True, text=True, env=env,
    )
    # The script may fail due to missing tools in sandbox, but must not write files
    after = set(clean_home.rglob("*"))
    new_files = after - before
    assert not new_files, (
        f"--dry-run created files/dirs in HOME: {new_files}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# 3. bash -n syntax checks
# ---------------------------------------------------------------------------
def test_bash_n_syntax_checks():
    """verify_perf_stack.sh and os-hardening-setup.sh must pass bash -n."""
    for script in ["verify_perf_stack.sh", "os-hardening-setup.sh"]:
        result = subprocess.run(
            ["bash", "-n", str(REPO_ROOT / "tools" / script)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"bash -n failed for {script}: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# 4. verify_perf_stack.sh static checks
# ---------------------------------------------------------------------------
def test_verify_perf_wifi_powersave_checks_2_not_1():
    """verify_perf_stack.sh must check wifi.powersave against 2 (NM disable),
    not 1 (NM ignore)."""
    content = (REPO_ROOT / "tools" / "verify_perf_stack.sh").read_text(encoding="utf-8")
    assert "wifi.powersave=2" in content, (
        "verify_perf_stack.sh should check for wifi.powersave=2 (disable)"
    )
    # Should NOT have the old inverted check for =1 as a PASS condition
    assert "wifi.powersave=1 (powersave disabled)" not in content, (
        "Old inverted wifi.powersave=1 check must be removed"
    )


def test_verify_perf_gnome_sushi_stage_reference():
    """verify_perf_stack.sh gnome-sushi warning must reference
    stage 45-apps-backups-search.sh (not 60-extras.sh)."""
    content = (REPO_ROOT / "tools" / "verify_perf_stack.sh").read_text(encoding="utf-8")
    assert "45-apps-backups-search.sh" in content, (
        "gnome-sushi warning should reference stage 45-apps-backups-search.sh"
    )
    assert "60-extras.sh" not in content or "gnome-sushi" not in content.split("60-extras.sh")[0], (
        "Old stage 60-extras.sh reference for gnome-sushi must be removed"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))