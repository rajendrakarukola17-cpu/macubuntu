"""
Installer, Supply-Chain, Dry-Run, Verification & Rollback Test Suite:
  - Eval-free allowlisted profile parsing (rejects command substitution, unknown keys, bad syntax)
  - Side-effect-free DRY_RUN=1 execution across install.sh profiles & uninstall.sh
  - Supply-chain lockfile verification (exact 40-hex commit SHAs, 64-hex file SHA-256s,
    no floating 'latest' URLs, no remote install.sh execution)
  - clone_pinned_repo refuses to auto-'rm -rf' unexpected non-Git directories
  - stages/99-verify.sh exits nonzero and NEVER prints 'SETUP COMPLETE' on required failure
"""

import os
import re
import subprocess
from pathlib import Path

import pytest


def _run_bash(script: str, env_extra: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
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


class TestEvalFreeProfileParser:
    def test_rejects_command_substitution_backticks_and_unknown_keys(self, repo_root: Path, tmp_path: Path):
        common = repo_root / "core" / "common.sh"

        # 1. Command substitution injection
        bad_cmd = tmp_path / "cmd_inject.env"
        bad_cmd.write_text('CELINA_NAME="$(touch /tmp/pwned_macubuntu)"\n', encoding="utf-8")
        r1 = _run_bash(f'source "{common}"; init_defaults; load_profile "{bad_cmd}"')
        assert r1.returncode != 0
        assert not Path("/tmp/pwned_macubuntu").exists()

        # 2. Backtick injection
        bad_bt = tmp_path / "bt_inject.env"
        bad_bt.write_text("CELINA_NAME=`id`\n", encoding="utf-8")
        r2 = _run_bash(f'source "{common}"; init_defaults; load_profile "{bad_bt}"')
        assert r2.returncode != 0

        # 3. Unknown key not in allowlist
        bad_key = tmp_path / "unknown_key.env"
        bad_key.write_text("PATH=/tmp/evil\n", encoding="utf-8")
        r3 = _run_bash(f'source "{common}"; init_defaults; load_profile "{bad_key}"')
        assert r3.returncode != 0

        # 4. Invalid key syntax (lowercase / semicolon)
        bad_syn = tmp_path / "bad_syntax.env"
        bad_syn.write_text("bad_key=1; echo pwn\n", encoding="utf-8")
        r4 = _run_bash(f'source "{common}"; init_defaults; load_profile "{bad_syn}"')
        assert r4.returncode != 0

    def test_loads_valid_profiles_safely(self, repo_root: Path):
        common = repo_root / "core" / "common.sh"
        for prof in ("default-8gb.env", "macos-only.env", "power-user.env"):
            p = repo_root / "profiles" / prof
            r = _run_bash(f'source "{common}"; init_defaults; load_profile "{p}"; echo "OK:$ENABLE_CELINA:$DOCK_CHOICE"')
            assert r.returncode == 0, r.stderr
            assert "OK:" in r.stdout


class TestSupplyChainLockfileAndCloneGuard:
    def test_lockfile_pins_all_commits_and_sha256_without_latest_or_remote_install_sh(self, repo_root: Path):
        lockfile = repo_root / "system" / "download-locks.sha256"
        lines = [
            ln.strip()
            for ln in lockfile.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        assert len(lines) >= 15
        for ln in lines:
            parts = ln.split("|")
            assert len(parts) == 7, f"Malformed lockfile row: {ln}"
            _id, kind, url, ref, sha, date_v, _lic = parts
            assert url.startswith("https://"), f"Non-HTTPS lock URL: {url}"
            assert "/releases/latest" not in url, f"Floating latest URL forbidden: {url}"
            assert not url.endswith("/install.sh"), f"Remote install.sh execution forbidden: {url}"
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_v)
            if kind == "git":
                assert re.fullmatch(r"[0-9a-f]{40}", ref), f"Git ref must be 40-hex commit SHA: {ln}"
            elif kind in ("file", "docker"):
                assert re.fullmatch(r"[0-9a-f]{64}", sha), f"{kind} SHA-256 must be 64-hex: {ln}"
            else:
                pytest.fail(f"Unknown lockfile kind: {kind}")

    def test_clone_pinned_repo_refuses_to_delete_unexpected_non_git_dir(self, repo_root: Path, tmp_path: Path):
        common = repo_root / "core" / "common.sh"
        existing_dir = tmp_path / "important_user_dir"
        existing_dir.mkdir()
        precious = existing_dir / "precious.txt"
        precious.write_text("do not delete me", encoding="utf-8")

        r = _run_bash(
            f'export REPO_DIR="{repo_root}" DRY_RUN=0; source "{common}"; init_defaults; clone_pinned_repo "whitesur-gtk-theme" "{existing_dir}"'
        )
        assert r.returncode != 0
        assert "non-git" in (r.stdout + r.stderr).lower()
        assert precious.exists() and precious.read_text(encoding="utf-8") == "do not delete me"


class TestDryRunZeroSideEffects:
    def test_install_dry_run_produces_zero_side_effects_in_clean_home(self, repo_root: Path, tmp_path: Path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        env = {"HOME": str(fake_home), "XDG_CONFIG_HOME": str(fake_home / ".config")}

        for extra_flags in (
            "--dry-run",
            "--dry-run --no-celina",
            "--dry-run --profile power-user --thirdparty --rust-tools",
            "--dry-run --name Aria --nickname Ari",
        ):
            r = _run_bash(f'bash "{repo_root}/install.sh" {extra_flags}', env_extra=env)
            assert r.returncode == 0, f"Dry run failed ({extra_flags}):\n{r.stdout}\n{r.stderr}"
            # Verify clean HOME had ZERO directories or files created!
            assert list(fake_home.iterdir()) == [], f"Dry-run leaked files into HOME: {list(fake_home.iterdir())}"

    def test_uninstall_dry_run_is_side_effect_free(self, repo_root: Path, tmp_path: Path):
        fake_home = tmp_path / "home_u"
        fake_home.mkdir()
        marker_dir = fake_home / ".config" / "celina"
        marker_dir.mkdir(parents=True)
        marker_file = marker_dir / "env"
        marker_file.write_text('CELINA_NAME="Celina"\n', encoding="utf-8")

        r = _run_bash(
            f'bash "{repo_root}/uninstall.sh" --dry-run --purge-data',
            env_extra={"HOME": str(fake_home)},
        )
        assert r.returncode == 0, r.stderr
        assert marker_file.exists(), "uninstall.sh --dry-run must NOT delete user files!"


class TestVerificationExitCodesAndStaticSafety:
    def test_verify_stage_exits_nonzero_and_never_prints_setup_complete_on_required_failure(
        self, repo_root: Path, tmp_path: Path
    ):
        fake_home = tmp_path / "verify_home"
        fake_home.mkdir()
        r = _run_bash(
            f'REPO_DIR="{repo_root}" DRY_RUN=0 ENABLE_CELINA=true bash "{repo_root}/stages/99-verify.sh"',
            env_extra={"HOME": str(fake_home)},
        )
        assert r.returncode != 0, "99-verify.sh MUST exit nonzero when required checks fail!"
        assert "SETUP COMPLETE" not in r.stdout
        assert "VERIFICATION FAILED" in r.stdout

    def test_no_eval_or_broad_input_group_in_shell_scripts(self, repo_root: Path):
        for sh_file in list(repo_root.glob("*.sh")) + list(repo_root.glob("core/*.sh")) + list(
            repo_root.glob("stages/*.sh")
        ) + list(repo_root.glob("tools/*.sh")) + [repo_root / "features/celina/install.sh"]:
            txt = sh_file.read_text(encoding="utf-8")
            assert not re.search(r"(?m)^\s*eval\s+", txt), f"Forbidden eval found in {sh_file}"
            assert "usermod -aG input" not in txt, f"Forbidden broad input-group membership found in {sh_file}"
            assert "curl -fsSL https://ollama.com/install.sh | sh" not in txt, f"Remote pipe-to-sh found in {sh_file}"


class TestFaultTolerantPipelineAndModelSelection:
    def test_continue_on_error_runs_next_stage_after_failure_and_fail_fast_aborts(
        self, repo_root: Path, tmp_path: Path
    ):
        """End-to-end test using a sandboxed clone of install.sh with a deliberately failing stage."""
        mock_repo = tmp_path / "repo"
        mock_repo.mkdir()
        for sub in ("core", "system", "profiles", "features"):
            (mock_repo / sub).symlink_to(repo_root / sub)
        (mock_repo / "install.sh").write_text(
            (repo_root / "install.sh").read_text(encoding="utf-8"), encoding="utf-8"
        )
        stages_dir = mock_repo / "stages"
        stages_dir.mkdir()
        (stages_dir / "10-broken.sh").write_text(
            "#!/usr/bin/env bash\necho 'STAGE_10_RAN'\nexit 7\n", encoding="utf-8"
        )
        (stages_dir / "20-healthy.sh").write_text(
            "#!/usr/bin/env bash\necho 'STAGE_20_RAN_AFTER_FAILURE'\nexit 0\n", encoding="utf-8"
        )
        os.chmod(stages_dir / "10-broken.sh", 0o755)
        os.chmod(stages_dir / "20-healthy.sh", 0o755)

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        env = {"HOME": str(fake_home)}

        # 1. Default safeguarded continue-on-error MUST execute stage 20 even after stage 10 fails,
        #    print retry guidance, and still exit nonzero honestly!
        r_cont = _run_bash(f'bash "{mock_repo}/install.sh" --dry-run', env_extra=env)
        assert r_cont.returncode != 0
        assert "STAGE_10_RAN" in r_cont.stdout
        assert "STAGE_20_RAN_AFTER_FAILURE" in r_cont.stdout
        assert "10-broken.sh (exit 7)" in r_cont.stdout
        assert "./install.sh --only 10" in r_cont.stdout

        # 2. --fail-fast MUST stop immediately on stage 10 without running stage 20!
        r_ff = _run_bash(f'bash "{mock_repo}/install.sh" --dry-run --fail-fast', env_extra=env)
        assert r_ff.returncode == 7
        assert "STAGE_10_RAN" in r_ff.stdout
        assert "STAGE_20_RAN_AFTER_FAILURE" not in r_ff.stdout

    def test_ram_aware_dense_and_sparse_moe_model_selection_and_cli_override(
        self, repo_root: Path, tmp_path: Path
    ):
        detect = repo_root / "core" / "detect.sh"
        for ram_gb, expected in (
            (8, "qwen3:1.7b"),
            (12, "qwen3:4b"),
            (16, "qwen3.5:9b"),
            (24, "gemma4:26b"),
            (32, "qwen3.5:35b-a3b"),
        ):
            r = _run_bash(f'source "{detect}"; resolve_ollama_model auto {ram_gb}')
            assert r.returncode == 0, r.stderr
            assert r.stdout.strip() == expected

        # Explicit Sparse MoE model tag via --model works cleanly in --dry-run with zero side effects
        fake_home = tmp_path / "home_model"
        fake_home.mkdir()
        r_cli = _run_bash(
            f'bash "{repo_root}/install.sh" --dry-run --model gemma4:26b --only 70',
            env_extra={"HOME": str(fake_home)},
        )
        assert r_cli.returncode == 0, r_cli.stderr
        assert "ollama pull gemma4:26b" in r_cli.stdout
        assert list(fake_home.iterdir()) == []


class TestOSLevelMacOSParityStack:
    def test_os_level_dropin_configs_and_uninstall_rollback_parity(self, repo_root: Path):
        required_files = [
            "system/modprobe/v4l2loopback-load.conf",
            "system/modprobe/v4l2loopback.conf",
            "system/systemd/resolved-mdns.conf",
            "system/systemd/sleep-safesleep.conf",
            "system/systemd/sleep-instant-suspend.conf",
            "system/wireplumber/51-macubuntu-bluetooth.conf",
            "system/pipewire/99-macubuntu-audio.conf",
            "system/grub/99-macubuntu-boot.cfg",
        ]
        for rel in required_files:
            p = repo_root / rel
            assert p.is_file() and p.stat().st_size > 20, f"Missing or empty OS drop-in: {rel}"

        # Key invariants inside the new OS configs
        assert "exclusive_caps=1" in (repo_root / "system/modprobe/v4l2loopback.conf").read_text()
        assert "MulticastDNS=no" in (repo_root / "system/systemd/resolved-mdns.conf").read_text()
        assert "HibernateDelaySec=3h" in (repo_root / "system/systemd/sleep-safesleep.conf").read_text()
        assert "AllowHibernation=no" in (repo_root / "system/systemd/sleep-instant-suspend.conf").read_text()
        wp_txt = (repo_root / "system/wireplumber/51-macubuntu-bluetooth.conf").read_text()
        assert "aac" in wp_txt and "ldac" in wp_txt and "bluez5.enable-sbc-xq = true" in wp_txt
        pw_txt = (repo_root / "system/pipewire/99-macubuntu-audio.conf").read_text()
        assert "default.clock.allowed-rates" in pw_txt and "48000" in pw_txt

        # uninstall.sh MUST roll back every single new OS-level system & user drop-in
        uninst = (repo_root / "uninstall.sh").read_text(encoding="utf-8")
        for target in (
            "/etc/modules-load.d/99-macubuntu-v4l2loopback.conf",
            "/etc/modprobe.d/99-macubuntu-v4l2loopback.conf",
            "/etc/systemd/resolved.conf.d/99-macubuntu-mdns.conf",
            "/etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf",
            "/etc/default/grub.d/99-macubuntu-boot.cfg",
            "51-macubuntu-bluetooth.conf",
            "99-macubuntu-audio.conf",
        ):
            assert target in uninst, f"uninstall.sh missing rollback for {target}"

    def test_silent_boot_and_os_parity_dry_run_zero_side_effects(self, repo_root: Path, tmp_path: Path):
        fake_home = tmp_path / "os_parity_home"
        fake_home.mkdir()
        r = _run_bash(
            f'bash "{repo_root}/install.sh" --dry-run --profile default-8gb --silent-boot --only 30,40,45,50,99',
            env_extra={"HOME": str(fake_home)},
        )
        assert r.returncode == 0, r.stderr
        assert "99-macubuntu-v4l2loopback.conf" in r.stdout
        assert "99-macubuntu-mdns.conf" in r.stdout
        assert "51-macubuntu-bluetooth.conf" in r.stdout
        assert "99-macubuntu-audio.conf" in r.stdout
        assert "99-macubuntu-safesleep.conf" in r.stdout
        assert "99-macubuntu-boot.cfg" in r.stdout
        assert "click-method" in r.stdout and "center-new-windows" in r.stdout
        assert list(fake_home.iterdir()) == []

    def test_os_events_parity_health_inspector(self, tmp_path: Path):
        from celina_pkg import os_events

        etc = tmp_path / "etc"
        home = tmp_path / "home"
        dev = tmp_path / "dev"
        etc.mkdir(); home.mkdir(); dev.mkdir()

        # Empty host -> reports missing core layers cleanly
        empty = os_events.get_os_parity_health(etc_root=etc, home_root=home, dev_root=dev)
        assert empty["active_count"] == 0
        assert "mdns_zero_conflict" in empty["missing_core"]

        # Populate all OS parity artifacts -> 100% healthy
        for rel in (
            "systemd/zram-generator.conf",
            "systemd/oomd.conf.d/99-macubuntu.conf",
            "udev/rules.d/60-macubuntu-io-scheduler.rules",
            "systemd/resolved.conf.d/99-macubuntu-mdns.conf",
            "modprobe.d/99-macubuntu-v4l2loopback.conf",
            "modules-load.d/99-macubuntu-v4l2loopback.conf",
            "systemd/sleep.conf.d/99-macubuntu-safesleep.conf",
            "default/grub.d/99-macubuntu-boot.cfg",
        ):
            p = etc / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# ok\n", encoding="utf-8")
        for rel in (
            ".config/wireplumber/wireplumber.conf.d/51-macubuntu-bluetooth.conf",
            ".config/pipewire/pipewire.conf.d/99-macubuntu-audio.conf",
            ".config/environment.d/90-macubuntu-smooth.conf",
        ):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# ok\n", encoding="utf-8")
        (dev / "video10").write_text("", encoding="utf-8")

        full = os_events.get_os_parity_health(etc_root=etc, home_root=home, dev_root=dev)
        assert full["active_count"] == full["total_checks"]
        assert full["missing_core"] == []
        assert "all core OS layers active" in full["summary"]


class TestTierBExperimentalResearchSandbox:
    def test_tier_b_firewall_and_verified_supply_chain_pins(self, repo_root: Path):
        # 1. Architecture doc & executable Tier B tool exist
        arch = (repo_root / "ARCHITECTURE.md").read_text(encoding="utf-8")
        assert "Tier A" in arch and "Tier B" in arch and "127.0.0.1:6969:6969" in arch

        script_path = repo_root / "tools/experimental-deep-stack.sh"
        assert script_path.is_file()
        script = script_path.read_text(encoding="utf-8")
        assert script.startswith("#!/usr/bin/env bash")

        # 2. Strict architectural firewall: install.sh and stages/ NEVER invoke Tier B
        assert "experimental-deep-stack" not in (repo_root / "install.sh").read_text(encoding="utf-8")
        for st in (repo_root / "stages").glob("*.sh"):
            assert "experimental-deep-stack" not in st.read_text(encoding="utf-8")

        # 3. Safeguard invariants in Tier B script
        assert "127.0.0.1:6969:6969" in script
        assert not re.search(
            r"docker\s+run[^\n]*(?:\\\n[^\n]*)*-p\s+6969:6969", script
        ), "Anisette docker run MUST NOT publish -p 6969:6969 on 0.0.0.0!"
        assert "-cipher" not in script, "Invalid OpenSSL req -cipher flag forbidden!"
        assert "/O=Apple Inc." not in script, "Impersonating Apple Inc. in local CSR forbidden!"
        assert "clone_pinned_repo" in script
        assert "apple-research/venv" in (repo_root / "uninstall.sh").read_text(encoding="utf-8")

        # 4. Real verified 40-char git pins exist in system/download-locks.sha256
        locks_txt = (repo_root / "system/download-locks.sha256").read_text(encoding="utf-8")
        for repo_id, expected_url in (
            ("owl", "https://github.com/seemoo-lab/owl.git"),
            ("opendrop", "https://github.com/seemoo-lab/opendrop.git"),
            ("findmy-py", "https://github.com/malmeloo/FindMy.py.git"),
        ):
            matched = [
                line for line in locks_txt.splitlines()
                if line.startswith(f"{repo_id}|git|{expected_url}|")
            ]
            assert len(matched) == 1, f"Missing or duplicate lock entry for {repo_id}"
            fields = matched[0].split("|")
            assert len(fields) == 7
            assert re.fullmatch(r"[0-9a-f]{40}", fields[3]), f"Invalid commit SHA for {repo_id}: {fields[3]}"
            assert fields[4] == "-", f"git lock entry field 5 must be '-' for {repo_id}"

    def test_tier_b_dry_run_zero_side_effects(self, repo_root: Path, tmp_path: Path):
        fake_home = tmp_path / "tier_b_home"
        fake_home.mkdir()
        r = _run_bash(
            f'bash "{repo_root}/tools/experimental-deep-stack.sh" --dry-run --generate-test-keys',
            env_extra={"HOME": str(fake_home)},
        )
        assert r.returncode == 0, r.stderr
        assert "TIER B: EXPERIMENTAL APPLE PROTOCOL RESEARCH SANDBOX" in r.stdout
        assert "clone_pinned_repo owl @ 0ee3263f944e" in r.stdout
        assert "clone_pinned_repo opendrop @ 11fe7ba78610" in r.stdout
        assert "clone_pinned_repo findmy-py @ 22e4879426f9" in r.stdout
        assert "research_local.csr" in r.stdout
        assert list(fake_home.iterdir()) == [], "Tier B --dry-run MUST create ZERO files in HOME!"


class TestRepoWideIntegrityAndHygiene:
    def test_all_profile_keys_and_system_asset_references_are_valid(self, repo_root: Path):
        common_txt = (repo_root / "core/common.sh").read_text(encoding="utf-8")
        m_fn = re.search(r"_assign_profile_kv\(\).*?\n\}", common_txt, re.S)
        assert m_fn, "_assign_profile_kv() missing from core/common.sh"
        case_body = re.search(r"case\s+\"\$key\"\s+in(.*?)\)", m_fn.group(0), re.S).group(1)
        allowed = set(re.findall(r"[A-Z][A-Z0-9_]+", case_body))
        assert len(allowed) >= 40

        for prof in sorted((repo_root / "profiles").glob("*.env")):
            for line in prof.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    key = s.split("=", 1)[0].strip()
                    assert key in allowed, f"Profile {prof.name} uses disallowed key: {key}"

        all_sh = (
            list(repo_root.glob("*.sh"))
            + list(repo_root.glob("core/*.sh"))
            + list(repo_root.glob("stages/*.sh"))
            + list(repo_root.glob("tools/*.sh"))
            + [repo_root / "features/celina/install.sh"]
        )
        for sh in all_sh:
            txt = sh.read_text(encoding="utf-8")
            for m in re.finditer(r"\$REPO_DIR/(system/[A-Za-z0-9._/@\-]+)", txt):
                rel = m.group(1)
                assert (repo_root / rel).exists(), f"Broken $REPO_DIR reference '{rel}' in {sh}"

    def test_all_lockfile_entries_and_script_lock_ids_are_strictly_valid(self, repo_root: Path):
        locks: dict[str, list[str]] = {}
        for line in (repo_root / "system/download-locks.sha256").read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split("|")
            assert len(parts) == 7, f"Lock entry must have 7 fields: {line}"
            lid, kind, url, ref, sha, _date, _lic = parts
            assert lid not in locks, f"Duplicate lock ID: {lid}"
            assert url.startswith("https://"), f"Non-HTTPS URL in lockfile for {lid}: {url}"
            if kind == "git":
                assert re.fullmatch(r"[0-9a-f]{40}", ref), f"Bad git 40-hex commit SHA for {lid}: {ref}"
                assert sha == "-", f"git lock entry field 5 must be '-' for {lid}"
            elif kind in ("file", "docker"):
                assert re.fullmatch(r"[0-9a-f]{64}", sha), f"Bad {kind} 64-hex SHA-256 for {lid}: {sha}"
            else:
                raise AssertionError(f"Unknown lock kind '{kind}' for {lid}")
            locks[lid] = parts

        all_sh = (
            list(repo_root.glob("*.sh"))
            + list(repo_root.glob("core/*.sh"))
            + list(repo_root.glob("stages/*.sh"))
            + list(repo_root.glob("tools/*.sh"))
            + [repo_root / "features/celina/install.sh"]
        )
        for sh in all_sh:
            txt = sh.read_text(encoding="utf-8")
            for m in re.finditer(r'(?:clone_pinned_repo|download_locked_file|get_lock_entry)\s+"([^"$]+)"', txt):
                lid = m.group(1)
                assert lid in locks, f"Script {sh} references unknown lock ID '{lid}'"

    def test_python_codebase_static_security_hygiene(self, repo_root: Path):
        import ast

        py_files = sorted(
            list(repo_root.glob("features/celina/celina_pkg/*.py"))
            + list(repo_root.glob("features/celina/celina_pkg/autonomy/*.py"))
            + list(repo_root.glob("features/celina/celina_hardware/*.py"))
            + list(repo_root.glob("system/nautilus/*.py"))
            + list(repo_root.glob("system/firefox/*.py"))
        )
        security_critical = {
            "approval.py",
            "automation.py",
            "engine.py",
            "net_policy.py",
            "privacy.py",
            "config.py",
            "command_runner.py",
            "skills.py",
        }
        assert len(py_files) >= 34
        for p in py_files:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    assert node.type is not None, f"Bare except found in {p}:{node.lineno}"
                    # Never swallow BaseException or SystemExit
                    caught: list[str] = []
                    if isinstance(node.type, ast.Name):
                        caught.append(node.type.id)
                    elif isinstance(node.type, ast.Tuple):
                        caught.extend(e.id for e in node.type.elts if isinstance(e, ast.Name))
                    for forbidden in ("BaseException", "SystemExit"):
                        assert forbidden not in caught, f"Forbidden except {forbidden} in {p}:{node.lineno}"
                    # Security-critical modules must never have silent `except Exception: pass`
                    if (
                        p.name in security_critical
                        and "Exception" in caught
                        and len(node.body) == 1
                        and isinstance(node.body[0], ast.Pass)
                    ):
                        raise AssertionError(f"Silent 'except Exception: pass' in security module {p}:{node.lineno}")
                if isinstance(node, ast.Call):
                    fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    kw = {k.arg: k.value for k in node.keywords if k.arg}
                    if "shell" in kw and isinstance(kw["shell"], ast.Constant):
                        assert kw["shell"].value is not True, f"shell=True forbidden in {p}:{node.lineno}"
                    if fn in ("run", "check_output", "check_call") and isinstance(node.func, ast.Attribute):
                        if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                            assert "timeout" in kw, f"subprocess.{fn} missing timeout in {p}:{node.lineno}"
                            tval = kw["timeout"]
                            assert not (isinstance(tval, ast.Constant) and tval.value is None), (
                                f"subprocess.{fn}(..., timeout=None) forbidden in {p}:{node.lineno}"
                            )


class TestSeniorReviewHardeningAndRelease:
    def test_stage00_hard_parity_gate_aborts_unconditionally_on_failure(self, repo_root: Path, tmp_path: Path):
        mock_repo = tmp_path / "repo_gate"
        mock_repo.mkdir()
        for sub in ("core", "system", "profiles", "features"):
            (mock_repo / sub).symlink_to(repo_root / sub)
        (mock_repo / "install.sh").write_text(
            (repo_root / "install.sh").read_text(encoding="utf-8"), encoding="utf-8"
        )
        stages_dir = mock_repo / "stages"
        stages_dir.mkdir()
        (stages_dir / "00-preflight.sh").write_text(
            "#!/usr/bin/env bash\necho 'GATE_FAIL'\nexit 9\n", encoding="utf-8"
        )
        (stages_dir / "10-after.sh").write_text(
            "#!/usr/bin/env bash\necho 'SHOULD_NOT_RUN'\nexit 0\n", encoding="utf-8"
        )
        os.chmod(stages_dir / "00-preflight.sh", 0o755)
        os.chmod(stages_dir / "10-after.sh", 0o755)

        fake_home = tmp_path / "gate_home"
        fake_home.mkdir()
        # Even with default CONTINUE_ON_ERROR=true, Stage 00 MUST hard-abort before Stage 10!
        r = _run_bash(f'bash "{mock_repo}/install.sh" --dry-run', env_extra={"HOME": str(fake_home)})
        assert r.returncode == 9
        assert "GATE_FAIL" in r.stdout
        assert "SHOULD_NOT_RUN" not in r.stdout
        assert "Pre-flight security gate" in r.stderr

    def test_files_office_mutation_tools_tier2_and_pinned_pypi_requirements(self, repo_root: Path):
        import json

        schema = json.loads((repo_root / "features/celina/config/neural-schema.json").read_text(encoding="utf-8"))
        t2 = set(schema["security_policy"]["tier2_approval_tools"])
        for mut in (
            "write_file",
            "edit_file",
            "move_file",
            "trash_file",
            "create_document",
            "edit_document",
            "convert_document",
        ):
            assert mut in t2, f"files_office mutation tool '{mut}' MUST be Tier-2!"

        for rel in (
            "features/celina/requirements.txt",
            "features/celina/requirements-optional.txt",
            "tools/requirements-tier-b.txt",
        ):
            lines = [
                ln.strip()
                for ln in (repo_root / rel).read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            assert len(lines) >= 2
            for ln in lines:
                assert "==" in ln, f"Unpinned PyPI requirement '{ln}' in {rel}"

    def test_oom_scores_and_hibernate_zram_swap_capacity_invariant(self, repo_root: Path):
        ev = (repo_root / "system/systemd/celina-eventd.service").read_text(encoding="utf-8")
        assert "OOMScoreAdjust=-300" in ev
        assert "MemoryMax=384M" in ev

        ol = (repo_root / "system/systemd/ollama-8gb-ram.conf").read_text(encoding="utf-8")
        assert "OOMScoreAdjust=300" in ol
        assert "ManagedOOMMemoryPressure=kill" in ol

        detect = repo_root / "core/detect.sh"
        # 8GB RAM + 4GB ZRAM requires >= 12GB real swap
        r_ok = _run_bash(f'source "{detect}"; check_hibernate_swap_capacity_kb 8388608 4194304 12582912')
        assert r_ok.returncode == 0
        # Undersized 8GB real swap with 8GB RAM + 4GB ZRAM MUST reject hibernation!
        r_small = _run_bash(f'source "{detect}"; check_hibernate_swap_capacity_kb 8388608 4194304 8388608')
        assert r_small.returncode != 0
        # Zero real swap (pure ZRAM) MUST reject hibernation!
        r_zero = _run_bash(f'source "{detect}"; check_hibernate_swap_capacity_kb 8388608 4194304 0')
        assert r_zero.returncode != 0

    def test_tier_b_apple_id_guardrail_blocks_existing_session_by_default(self, repo_root: Path, tmp_path: Path):
        fake_home = tmp_path / "apple_session_home"
        (fake_home / ".config" / "FindMy").mkdir(parents=True)

        # Must abort by default when Apple/FindMy session dir exists
        r_block = _run_bash(
            f'bash "{repo_root}/tools/experimental-deep-stack.sh" --dry-run',
            env_extra={"HOME": str(fake_home)},
        )
        assert r_block.returncode != 0
        assert "Account-Safety Guardrail triggered" in r_block.stderr

        # Explicit burner override succeeds in dry-run with zero extra files created
        r_ok = _run_bash(
            f'bash "{repo_root}/tools/experimental-deep-stack.sh" --dry-run --allow-existing-apple-session',
            env_extra={"HOME": str(fake_home)},
        )
        assert r_ok.returncode == 0, r_ok.stderr

    def test_firefox_vaapi_fallback_and_airplay_handshake_health(self, repo_root: Path, tmp_path: Path, monkeypatch):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "apply_safari_theme", str(repo_root / "system/firefox/apply-safari-theme.py")
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)

        fake_home = tmp_path / "ff_home"
        prof = fake_home / ".mozilla" / "firefox" / "abc123.default-release"
        prof.mkdir(parents=True)
        mod.apply_safari_theme(home=fake_home, dry_run=False, vaapi_enabled=False)
        ujs = (prof / "user.js").read_text(encoding="utf-8")
        assert 'user_pref("media.ffmpeg.vaapi.enabled", false);' in ujs
        assert 'user_pref("media.hardware-video-decoding.force-enabled", false);' in ujs

        from celina_pkg import command_runner, continuity

        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/journalctl" if cmd == "journalctl" else None)
        monkeypatch.setattr(
            command_runner,
            "run_command",
            lambda *a, **kw: command_runner.CommandResult(0, "uxplay: FairPlay pair-verify handshake failed\n", ""),
        )
        warn = continuity.check_airplay_handshake_health()
        assert "AirPlay 2 handshake warning" in warn

    def test_docs_and_reproducible_release_packager(self, repo_root: Path, tmp_path: Path):
        for doc, needle in (
            ("COMPATIBILITY.md", "VAEntrypointVLD"),
            ("CHANGELOG.md", "4.4.0"),
            ("docs/imessage-safe-path.md", "BlueBubbles"),
            ("ARCHITECTURE.md", "ECID"),
        ):
            txt = (repo_root / doc).read_text(encoding="utf-8")
            assert needle in txt, f"Expected '{needle}' in {doc}"

        out_dir = tmp_path / "rel_out"
        r = _run_bash(
            f'bash "{repo_root}/tools/build-release.sh" --version 4.4.0 --output-dir "{out_dir}"'
        )
        assert r.returncode == 0, r.stderr
        assert (out_dir / "macubuntu-v4.4.0.tar.gz").is_file()
        assert (out_dir / "SHA256SUMS").is_file()
        assert "macubuntu-v4.4.0.tar.gz: OK" in r.stdout





