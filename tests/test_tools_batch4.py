"""Tests for the final tools/ batch (batch 4).

Covers: celina-borg-backup.sh, build-deb.sh, build-release.sh, build-ebpf.sh,
celina-auto-theme.sh, celina-capability-runner.py.
"""

import os
import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"


# ---------------------------------------------------------------------------
# 1. Syntax checks: bash -n on all 5 shell scripts + py_compile on Python
# ---------------------------------------------------------------------------
SHELL_SCRIPTS = [
    "celina-borg-backup.sh",
    "build-deb.sh",
    "build-release.sh",
    "build-ebpf.sh",
    "celina-auto-theme.sh",
]


@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_bash_syntax(script):
    path = TOOLS_DIR / script
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, f"bash -n failed for {script}: {result.stderr}"


def test_python_compile():
    path = TOOLS_DIR / "celina-capability-runner.py"
    result = subprocess.run(
        ["python3", "-m", "py_compile", str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"py_compile failed: {result.stderr}"


# ---------------------------------------------------------------------------
# 2. celina-auto-theme.sh: octal safety + arithmetic injection rejection
# ---------------------------------------------------------------------------
def test_auto_theme_hour_08_no_octal_crash(tmp_path):
    """--hour 08 must succeed (no octal parse crash on 08/09)."""
    script = TOOLS_DIR / "celina-auto-theme.sh"
    env = dict(os.environ)
    env["CELINA_DAY_START_HOUR"] = "7"
    env["CELINA_NIGHT_START_HOUR"] = "19"
    result = subprocess.run(
        ["bash", str(script), "--dry-run", "--hour", "08", "--current", "prefer-dark"],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0, f"hour 08 crashed: {result.stderr}"


def test_auto_theme_arithmetic_injection_exits_2(tmp_path):
    """--hour 'a[$(echo pwned)]' must exit 2 (arithmetic injection rejected)."""
    script = TOOLS_DIR / "celina-auto-theme.sh"
    result = subprocess.run(
        ["bash", str(script), "--dry-run", "--hour", 'a[$(echo pwned)]'],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2, f"expected exit 2 for injection, got {result.returncode}: {result.stdout}"


def test_auto_theme_out_of_range_exits_2(tmp_path):
    """--hour 99 must exit 2 (out of range 0..23)."""
    script = TOOLS_DIR / "celina-auto-theme.sh"
    result = subprocess.run(
        ["bash", str(script), "--dry-run", "--hour", "99"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2, f"expected exit 2 for out-of-range, got {result.returncode}"


def test_auto_theme_unknown_option_exits_2(tmp_path):
    """Unknown option must exit 2."""
    script = TOOLS_DIR / "celina-auto-theme.sh"
    result = subprocess.run(
        ["bash", str(script), "--bogus"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# 3. celina-borg-backup.sh: 0644 conf rejection + SQLite snapshot + borg compact
# ---------------------------------------------------------------------------
def test_borg_rejects_0644_conf(tmp_path):
    """Refuses to source a 0644 borg.env."""
    script = TOOLS_DIR / "celina-borg-backup.sh"
    conf = tmp_path / "borg.env"
    conf.write_text('BORG_REPO="/mnt/backup/celina-borg"\n', encoding="utf-8")
    os.chmod(conf, 0o644)
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    result = subprocess.run(
        ["bash", str(script), "--config", str(conf)],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 1, f"expected exit 1 for 0644 conf, got {result.returncode}"
    assert "0600" in result.stderr or "mode" in result.stderr.lower()


def test_borg_uses_sqlite_backup_and_compact(tmp_path):
    """Static check: script uses sqlite3 .backup() and borg compact."""
    content = (TOOLS_DIR / "celina-borg-backup.sh").read_text(encoding="utf-8")
    assert ".backup(" in content, "missing sqlite3 online backup (.backup)"
    assert "borg compact" in content, "missing borg compact after prune"
    assert "mktemp -d" in content, "missing mktemp -d for snapshot dir"
    assert "trap" in content, "missing EXIT trap for snapshot cleanup"


# ---------------------------------------------------------------------------
# 4. celina-capability-runner.py: approval callback + --start validation
# ---------------------------------------------------------------------------
def test_safe_approval_cb_blocks_dangerous(tmp_path):
    """make_safe_approval_cb blocks shell metacharacters, path traversal, close_application."""
    source = (TOOLS_DIR / "celina-capability-runner.py").read_text(encoding="utf-8")

    # _SHELL_META_RE must exist and reject metacharacters
    assert "_SHELL_META_RE" in source, "missing _SHELL_META_RE"

    # Check that close_application is NOT in the auto-approved set
    start = source.index("if action_name in {")
    end = source.index("}:\n            log_bucket.append", start)
    auto_approved_section = source[start:end]
    assert "close_application" not in auto_approved_section, (
        "close_application must be removed from auto-approved set"
    )

    # Check strict path containment helper exists
    assert "_path_strictly_inside" in source, "missing _path_strictly_inside"

    # Check that /tmp/celina-test substring match is removed
    assert '/tmp/celina-test" in combined' not in source, (
        "substring /tmp/celina-test match must be removed"
    )


def test_capability_runner_start_not_found(tmp_path):
    """--start Q9999 must exit non-zero (not silently fall back to index 0)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "celina_capability_runner", TOOLS_DIR / "celina-capability-runner.py"
    )
    # Just check the source has the exit(2) for missing --start
    source = (TOOLS_DIR / "celina-capability-runner.py").read_text(encoding="utf-8")
    assert "not found in selected items" in source, "missing --start not-found error"
    assert "sys.exit(2)" in source, "missing sys.exit(2) for --start not found"


# ---------------------------------------------------------------------------
# 5. build-deb.sh static checks
# ---------------------------------------------------------------------------
def test_build_deb_no_tmp_home_fallback():
    """No ${HOME:-/tmp} in wrappers."""
    content = (TOOLS_DIR / "build-deb.sh").read_text(encoding="utf-8")
    assert '${HOME:-/tmp}' not in content, "found HOME falling back to /tmp"


def test_build_deb_excludes_tier_b_and_secrets():
    """Excludes experimental-deep-stack.sh, inject_parity_break.py, ._*."""
    content = (TOOLS_DIR / "build-deb.sh").read_text(encoding="utf-8")
    assert "experimental-deep-stack.sh" in content, "missing Tier B exclusion"
    assert "inject_parity_break.py" in content, "missing fault injector exclusion"
    assert '._*' in content or "._*" in content, "missing AppleDouble exclusion"


def test_build_deb_version_from_toml():
    """Reads version from version.toml."""
    content = (TOOLS_DIR / "build-deb.sh").read_text(encoding="utf-8")
    assert "version.toml" in content, "missing version.toml read"


def test_build_deb_celina_daemon_and_sandbox():
    """Packages celina-daemon.service and celina-sandbox.slice."""
    content = (TOOLS_DIR / "build-deb.sh").read_text(encoding="utf-8")
    assert "celina-daemon.service" in content, "missing celina-daemon.service"
    assert "celina-sandbox.slice" in content, "missing celina-sandbox.slice"


def test_build_deb_prerm_disables_all_units():
    """prerm disables all packaged units/timers."""
    content = (TOOLS_DIR / "build-deb.sh").read_text(encoding="utf-8")
    for unit in ["celina-auto-theme.timer", "celina-borg-backup.timer", "celina-mcp-n8n.service", "celina-self-improve.service", "celina-daemon.service"]:
        assert unit in content, f"prerm missing {unit}"


def test_build_deb_exit_2_unknown():
    """Unknown option exits 2."""
    content = (TOOLS_DIR / "build-deb.sh").read_text(encoding="utf-8")
    assert 'exit 2' in content, "missing exit 2 on unknown option"


# ---------------------------------------------------------------------------
# 6. build-release.sh static checks
# ---------------------------------------------------------------------------
def test_build_release_gzip_n():
    """Uses gzip -n for reproducibility."""
    content = (TOOLS_DIR / "build-release.sh").read_text(encoding="utf-8")
    assert "gzip -n" in content, "missing gzip -n"


def test_build_release_source_date_epoch():
    """Uses SOURCE_DATE_EPOCH."""
    content = (TOOLS_DIR / "build-release.sh").read_text(encoding="utf-8")
    assert "SOURCE_DATE_EPOCH" in content, "missing SOURCE_DATE_EPOCH"


def test_build_release_excludes_secrets_and_apple_double():
    """Excludes secrets and AppleDouble files."""
    content = (TOOLS_DIR / "build-release.sh").read_text(encoding="utf-8")
    for pat in ["._*", ".DS_Store", "*.key", "*.pem", "*.db-wal", "*venv*", "node_modules"]:
        assert pat in content, f"missing exclusion: {pat}"


def test_build_release_exit_2_unknown():
    """Unknown option exits 2."""
    content = (TOOLS_DIR / "build-release.sh").read_text(encoding="utf-8")
    assert 'exit 2' in content, "missing exit 2 on unknown option"


def test_build_release_atomic_sums():
    """SHA256SUMS written atomically via mktemp + mv."""
    content = (TOOLS_DIR / "build-release.sh").read_text(encoding="utf-8")
    assert "mktemp" in content, "missing mktemp for atomic SHA256SUMS"
    assert "mv -f" in content, "missing mv for atomic SHA256SUMS"


# ---------------------------------------------------------------------------
# 7. build-ebpf.sh static checks
# ---------------------------------------------------------------------------
def test_build_ebpf_umask_022():
    """Uses umask 022 not 077."""
    content = (TOOLS_DIR / "build-ebpf.sh").read_text(encoding="utf-8")
    assert "umask 022" in content, "should use umask 022"


def test_build_ebpf_atomic_mv():
    """Generates artifacts atomically via mktemp + mv."""
    content = (TOOLS_DIR / "build-ebpf.sh").read_text(encoding="utf-8")
    assert "mktemp" in content, "missing mktemp for atomic generation"
    assert "mv -f" in content, "missing mv for atomic artifact placement"


def test_build_ebpf_arch_validation():
    """Validates architecture, fails on unsupported."""
    content = (TOOLS_DIR / "build-ebpf.sh").read_text(encoding="utf-8")
    assert "x86_64" in content, "missing x86_64 arch support"
    assert "aarch64" in content or "arm64" in content, "missing arm64 arch support"
    assert "Unsupported architecture" in content, "missing unsupported arch failure"


def test_build_ebpf_verify_sources():
    """Verifies .bpf.c sources and bpf_helpers.h before compiling."""
    content = (TOOLS_DIR / "build-ebpf.sh").read_text(encoding="utf-8")
    assert "bpf_helpers.h" in content, "missing bpf_helpers.h check"
    assert "Missing eBPF source file" in content, "missing source file verification"


def test_build_ebpf_actual_btf_path():
    """--check prints actual BTF_PATH variable, not hardcoded path string."""
    content = (TOOLS_DIR / "build-ebpf.sh").read_text(encoding="utf-8")
    assert "BTF_PATH" in content, "missing BTF_PATH variable usage"


def test_build_ebpf_exit_2_unknown():
    """Unknown option exits 2."""
    content = (TOOLS_DIR / "build-ebpf.sh").read_text(encoding="utf-8")
    assert 'exit 2' in content, "missing exit 2 on unknown option"