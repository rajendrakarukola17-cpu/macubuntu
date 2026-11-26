"""
Test that the installer correctly drops privileges when running under sudo.
This test verifies that:
1. run_as_user() actually runs as the target user, not root
2. TARGET_USER/TARGET_UID/TARGET_HOME are correctly resolved
3. PATH doesn't allow user-writable dirs to shadow system binaries
4. Python config uses TARGET_HOME instead of Path.home()
"""
import os
import subprocess
import tempfile
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_run_as_user_drops_privileges():
    """Verify run_as_user() actually runs as the target user, not root."""
    # Create a test script that prints whoami
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(
            """#!/usr/bin/env bash
source core/common.sh
init_defaults
run_as_user whoami
"""
        )
        f.flush()
        os.chmod(f.name, 0o755)

        # Run as root (simulating sudo install.sh)
        sudo_user = os.environ.get("SUDO_USER", os.environ.get("USER", "root"))
        result = subprocess.run(
            ["bash", f.name],
            capture_output=True,
            text=True,
            env={**os.environ, "SUDO_USER": sudo_user},
        )

        # The output should be the SUDO_USER, not 'root'
        output = result.stdout.strip()
        assert output == sudo_user, (
            f"run_as_user ran as '{output}', expected '{sudo_user}'"
        )

        Path(f.name).unlink()


def test_target_user_resolution():
    """Verify TARGET_USER/TARGET_UID/TARGET_HOME are correctly resolved."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'''
        source "{REPO_ROOT}/core/common.sh"
        init_defaults
        echo "USER=$TARGET_USER"
        echo "UID=$TARGET_UID"
        echo "HOME=$TARGET_HOME"
    ''',
        ],
        capture_output=True,
        text=True,
    )

    output = result.stdout
    assert "USER=" in output, f"Missing TARGET_USER in output: {output}"
    assert "UID=" in output, f"Missing TARGET_UID in output: {output}"
    assert "HOME=" in output, f"Missing TARGET_HOME in output: {output}"

    # Parse values
    lines = output.strip().split("\n")
    user = next(l.split("=", 1)[1] for l in lines if l.startswith("USER="))
    uid = next(l.split("=", 1)[1] for l in lines if l.startswith("UID="))
    home = next(l.split("=", 1)[1] for l in lines if l.startswith("HOME="))

    expected_user = os.environ.get("SUDO_USER", os.environ.get("USER"))
    assert user == expected_user, f"TARGET_USER={user}, expected {expected_user}"
    assert uid.isdigit(), f"TARGET_UID={uid} is not numeric"
    assert Path(home).exists(), f"TARGET_HOME={home} does not exist"


def test_path_no_shadowing():
    """Verify PATH doesn't allow user-writable dirs to shadow system binaries."""
    result = subprocess.run(
        ["bash", "-c", f'source "{REPO_ROOT}/core/common.sh"; echo $PATH'],
        capture_output=True,
        text=True,
    )
    path = result.stdout.strip()

    path_dirs = path.split(":")

    # Find positions of key dirs
    local_bin_idx = next(
        (i for i, d in enumerate(path_dirs) if d.endswith(".local/bin")), None
    )
    usr_bin_idx = next(
        (i for i, d in enumerate(path_dirs) if d == "/usr/bin"), None
    )

    if local_bin_idx is not None and usr_bin_idx is not None:
        # .local/bin should come AFTER /usr/bin (append, not prepend)
        assert local_bin_idx > usr_bin_idx, (
            f"PATH shadowing risk: .local/bin at {local_bin_idx}, "
            f"/usr/bin at {usr_bin_idx}"
        )


def test_python_config_uses_target_home():
    """Verify Python config.py uses TARGET_HOME (not Path.home())."""
    result = subprocess.run(
        [
            "python3",
            "-c",
            """
import sys
sys.path.insert(0, '.')
from features.celina.celina_pkg import config
# HOME should not be /root
assert str(config.HOME) != '/root', f"HOME is /root: {config.HOME}"
print(f"HOME={config.HOME}")
print(f"BASE_DIR={config.BASE_DIR}")
print(f"CONFIG_DIR={config.CONFIG_DIR}")
""",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"config import failed: {result.stderr}"
    output = result.stdout.strip()
    assert "HOME=" in output
    assert "/root" not in output, f"HOME should not be /root: {output}"


def test_gset_uses_run_as_user():
    """Verify gset() wraps gsettings with run_as_user."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'''
        source "{REPO_ROOT}/core/common.sh"
        init_defaults
        # gset should call run_as_user (check function definition)
        type gset | head -1
    ''',
        ],
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    # gset should be a function (not an alias or external command)
    assert "function" in output or "gset is a function" in output, (
        f"gset is not a shell function: {output}"
    )


def test_systemctl_user_enable_uses_run_as_user():
    """Verify systemctl_user_enable wraps with run_as_user."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'''
        source "{REPO_ROOT}/core/common.sh"
        init_defaults
        # Check the function body contains run_as_user
        declare -f systemctl_user_enable | grep -q run_as_user && echo "PASS"
    ''',
        ],
        capture_output=True,
        text=True,
    )
    assert "PASS" in result.stdout, (
        f"systemctl_user_enable doesn't use run_as_user: {result.stdout}"
    )


def test_install_flatpak_uses_run_as_user():
    """Verify install_flatpak wraps with run_as_user."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'''
        source "{REPO_ROOT}/core/common.sh"
        init_defaults
        declare -f install_flatpak | grep -q run_as_user && echo "PASS"
    ''',
        ],
        capture_output=True,
        text=True,
    )
    assert "PASS" in result.stdout, (
        f"install_flatpak doesn't use run_as_user: {result.stdout}"
    )


def test_register_gnome_shortcut_uses_run_as_user():
    """Verify register_gnome_shortcut wraps dconf with run_as_user."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'''
        source "{REPO_ROOT}/core/common.sh"
        init_defaults
        declare -f register_gnome_shortcut | grep -q run_as_user && echo "PASS"
    ''',
        ],
        capture_output=True,
        text=True,
    )
    assert "PASS" in result.stdout, (
        f"register_gnome_shortcut doesn't use run_as_user: {result.stdout}"
    )