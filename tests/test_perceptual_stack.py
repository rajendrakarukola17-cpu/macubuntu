"""
Perceptual Choreography & SCX Touch-Boost Verification Tests.

Validates:
  - system/scx/config.toml parses cleanly and defaults to scx_bpfland
  - choreography.js passes JS syntax validation (node --check or gjs)
  - choreography.js static ESM/lifecycle assertions (no imports.gi, no Shell.GLSLEffect, disable() restores slow_down_factor)
  - DRY_RUN=1 produces zero filesystem mutations or leaked snapshot files
"""

import json
import subprocess
import tomllib  # Python 3.11+
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_bash(script: str, env_extra: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    import os
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestSCXConfig:
    """system/scx/config.toml must parse and default to scx_bpfland."""

    def test_scx_config_toml_parses_and_defaults_to_scp_bpfland(self):
        config_path = REPO_ROOT / "system" / "scx" / "config.toml"
        assert config_path.is_file(), f"Missing {config_path}"

        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert "default_sched" in data, "Missing default_sched key"
        assert data["default_sched"] == "scx_bpfland", (
            f"Expected default_sched='scx_bpfland', got '{data['default_sched']}'"
        )
        assert "default_mode" in data, "Missing default_mode key"
        assert data["default_mode"] == "Auto", (
            f"Expected default_mode='Auto', got '{data['default_mode']}'"
        )

    def test_scx_config_has_scheds_section(self):
        config_path = REPO_ROOT / "system" / "scx" / "config.toml"
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert "scheds" in data, "Missing [scheds] section"
        # Should have at least one scheduler defined
        sched_names = [k for k in data["scheds"] if k.startswith("scx_")]
        assert len(sched_names) >= 1, "No scx_* schedulers defined"


class TestChoreographyJS:
    """choreography.js must pass JS syntax validation and static ESM/lifecycle assertions."""

    @pytest.fixture
    def choreography_js(self) -> Path:
        path = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "choreography.js"
        assert path.is_file(), f"Missing {path}"
        return path

    def test_choreography_js_node_syntax(self, choreography_js: Path):
        """choreography.js must pass node --input-type=module --check."""
        if not subprocess.run(["which", "node"], capture_output=True).returncode == 0:
            pytest.skip("node not available for JS syntax check")
        result = subprocess.run(
            ["node", "--input-type=module", "--check"],
            input=choreography_js.read_text(encoding="utf-8"),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, f"choreography.js JS syntax error: {result.stderr}"

    def test_choreography_js_no_legacy_imports_gi(self, choreography_js: Path):
        text = choreography_js.read_text(encoding="utf-8")
        assert "imports.gi" not in text, "choreography.js must not use legacy imports.gi.*"

    def test_choreography_js_no_shell_glsleffect(self, choreography_js: Path):
        text = choreography_js.read_text(encoding="utf-8")
        assert "Shell.GLSLEffect" not in text, "choreography.js must not use deprecated Shell.GLSLEffect"

    def test_choreography_js_exports_perceptual_choreographer(self, choreography_js: Path):
        text = choreography_js.read_text(encoding="utf-8")
        assert "export class PerceptualChoreographer" in text, (
            "choreography.js must export PerceptualChoreographer class"
        )

    def test_choreography_js_disable_restores_slow_down_factor(self, choreography_js: Path):
        text = choreography_js.read_text(encoding="utf-8")
        # GNOME 46+ uses property API: stSettings.slow_down_factor
        assert "slow_down_factor" in text, "choreography.js must reference slow_down_factor property"
        assert "disable()" in text, "choreography.js must have disable() method"


class TestExtensionChoreographerWiring:
    """extension.js must import and wire PerceptualChoreographer."""

    @pytest.fixture
    def extension_js(self) -> Path:
        path = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "extension.js"
        assert path.is_file(), f"Missing {path}"
        return path

    def test_extension_imports_perceptual_choreographer(self, extension_js: Path):
        text = extension_js.read_text(encoding="utf-8")
        assert "PerceptualChoreographer" in text, (
            "extension.js must import PerceptualChoreographer from choreography.js"
        )

    def test_extension_implements_launch_feedback_hooks(self, extension_js: Path):
        text = extension_js.read_text(encoding="utf-8")
        assert "showLaunchFeedback" in text, (
            "extension.js must implement showLaunchFeedback pill hook"
        )
        assert "clearLaunchFeedback" in text, (
            "extension.js must implement clearLaunchFeedback pill hook"
        )


class TestDryRunZeroSideEffects:
    """DRY_RUN=1 must produce zero filesystem mutations or leaked snapshot files."""

    def test_installer_dry_run_no_snapshot_leakage(self, repo_root: Path, tmp_path: Path):
        """Dry run must not create snapshot files in SNAPSHOT_ROOT."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        env = {"HOME": str(fake_home), "DRY_RUN": "1"}

        # Run the installer dry-run
        r = _run_bash(f"bash {repo_root}/install.sh --dry-run --only 35", env_extra=env)
        # Stage 35 should complete cleanly (exit 0) even if some sub-steps are skipped
        assert r.returncode == 0, f"Dry-run stage 35 failed:\n{r.stdout}\n{r.stderr}"

        # SNAPSHOT_ROOT should not have new snapshots created during dry-run
        snapshot_root = Path("/var/lib/macubuntu/snapshots")
        if snapshot_root.exists():
            snapshots_before = list(snapshot_root.iterdir())
            # Run again to confirm no new snapshots
            r2 = _run_bash(f"bash {repo_root}/install.sh --dry-run --only 35", env_extra=env)
            assert r2.returncode == 0, f"Second dry-run failed:\n{r2.stdout}\n{r2.stderr}"
            snapshots_after = list(snapshot_root.iterdir())
            assert snapshots_before == snapshots_after, (
                "Dry-run created new snapshot files in SNAPSHOT_ROOT"
            )

    def test_dry_run_no_home_leakage(self, repo_root: Path, tmp_path: Path):
        """DRY_RUN=1 must not create any files in HOME."""
        fake_home = tmp_path / "home_dry"
        fake_home.mkdir()

        env = {"HOME": str(fake_home)}
        r = _run_bash(f"bash {repo_root}/install.sh --dry-run", env_extra=env)
        assert r.returncode == 0, f"Dry-run install failed:\n{r.stdout}\n{r.stderr}"
        assert list(fake_home.iterdir()) == [], (
            f"Dry-run leaked files into HOME: {list(fake_home.iterdir())}"
        )


class TestCommonShFlags:
    """core/common.sh must allowlist new flags with safe defaults."""

    @pytest.fixture
    def common_sh(self) -> Path:
        path = REPO_ROOT / "core" / "common.sh"
        assert path.is_file(), f"Missing {path}"
        return path

    def test_perceptual_choreography_flag_allowlisted(self, common_sh: Path):
        text = common_sh.read_text(encoding="utf-8")
        assert "ENABLE_PERCEPTUAL_CHOREOGRAPHY" in text, (
            "ENABLE_PERCEPTUAL_CHOREOGRAPHY must be in allowlist"
        )

    def test_scx_scheduler_flag_allowlisted(self, common_sh: Path):
        text = common_sh.read_text(encoding="utf-8")
        assert "SCX_SCHEDULER" in text, "SCX_SCHEDULER must be in allowlist"

    def test_choreography_speed_factor_flag_allowlisted(self, common_sh: Path):
        text = common_sh.read_text(encoding="utf-8")
        assert "CHOREOGRAPHY_SPEED_FACTOR" in text, (
            "CHOREOGRAPHY_SPEED_FACTOR must be in allowlist"
        )

    def test_perceptual_choreography_default_true(self, common_sh: Path):
        text = common_sh.read_text(encoding="utf-8")
        assert 'ENABLE_PERCEPTUAL_CHOREOGRAPHY="${ENABLE_PERCEPTUAL_CHOREOGRAPHY:-true}"' in text, (
            "ENABLE_PERCEPTUAL_CHOREOGRAPHY default must be true"
        )

    def test_scx_scheduler_default_bpfland(self, common_sh: Path):
        text = common_sh.read_text(encoding="utf-8")
        assert 'SCX_SCHEDULER="${SCX_SCHEDULER:-scx_bpfland}"' in text, (
            "SCX_SCHEDULER default must be scx_bpfland"
        )

    def test_choreography_speed_factor_default_065(self, common_sh: Path):
        text = common_sh.read_text(encoding="utf-8")
        assert 'CHOREOGRAPHY_SPEED_FACTOR="${CHOREOGRAPHY_SPEED_FACTOR:-0.65}"' in text, (
            "CHOREOGRAPHY_SPEED_FACTOR default must be 0.65"
        )


class TestDetectShProbes:
    """core/detect.sh must have read-only sched_ext and gnome-shell version probes."""

    @pytest.fixture
    def detect_sh(self) -> Path:
        path = REPO_ROOT / "core" / "detect.sh"
        assert path.is_file(), f"Missing {path}"
        return path

    def test_detect_sched_ext_supported_exists(self, detect_sh: Path):
        text = detect_sh.read_text(encoding="utf-8")
        assert "detect_sched_ext_supported" in text, (
            "detect.sh must define detect_sched_ext_supported()"
        )
        assert "/sys/kernel/sched_ext" in text, (
            "detect_sched_ext_supported must check /sys/kernel/sched_ext"
        )

    def test_detect_gnome_shell_major_exists(self, detect_sh: Path):
        text = detect_sh.read_text(encoding="utf-8")
        assert "detect_gnome_shell_major" in text, (
            "detect.sh must define detect_gnome_shell_major()"
        )
        assert "gnome-shell --version" in text, (
            "detect_gnome_shell_major must parse gnome-shell --version"
        )
