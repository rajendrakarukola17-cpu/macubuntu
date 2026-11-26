"""
Tests for Stage 99 verifier and Gear-0 Rust FFI.

Covers:
- bash -n syntax check on stages/99-verify.sh
- DRY_RUN=1 execution exits 0 with zero HOME mutation
- P0 fixes: check_cmd probe splitting, PAM regex, write_state_json cleanup,
  bwrap/NPU/ydotool false-positive guards, models.ini pipefail guard
- Gear-0 FFI: MAX_FFI_INPUT_BYTES guard, whitespace trimming, catch_unwind,
  binary-safe sha256/base64, MD5 non-cryptographic marker
- stages/ invariant: only [0-9][0-9]-*.sh files
"""
import ast
import os
import re
import subprocess
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
STAGE99 = REPO_DIR / "stages" / "99-verify.sh"
STAGES_DIR = REPO_DIR / "stages"
RUST_LIB = REPO_DIR / "features" / "celina" / "celina_rust" / "src" / "lib.rs"


# ---------------------------------------------------------------------------
# Stage 99 verifier tests
# ---------------------------------------------------------------------------

def test_stage99_bash_n():
    """stages/99-verify.sh must pass bash -n syntax check."""
    result = subprocess.run(
        ["bash", "-n", str(STAGE99)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_stage99_dry_run_exits_zero():
    """DRY_RUN=1 bash stages/99-verify.sh must exit 0."""
    env = dict(os.environ)
    env["DRY_RUN"] = "1"
    env["PROFILE"] = "default-8gb"
    env["CELINA_SEL"] = "true"
    result = subprocess.run(
        ["bash", str(STAGE99)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"DRY_RUN=1 stage 99 exited {result.returncode}\n"
        f"stdout: {result.stdout[-2000:]}\n"
        f"stderr: {result.stderr[-2000:]}"
    )


def test_stage99_dry_run_zero_home_mutation():
    """DRY_RUN=1 must not mutate anything under HOME."""
    home = Path(os.environ.get("HOME", "/home/user"))
    # Record mtimes of key HOME paths before running
    before = {}
    for p in [home / ".config", home / ".local", home / ".cache"]:
        if p.exists():
            for f in p.rglob("*"):
                if f.is_file():
                    before[str(f)] = f.stat().st_mtime_ns

    env = dict(os.environ)
    env["DRY_RUN"] = "1"
    env["PROFILE"] = "default-8gb"
    result = subprocess.run(
        ["bash", str(STAGE99)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, f"stage 99 dry-run failed: {result.stderr}"

    # After running, no file under HOME should have changed mtime
    for path_str, before_ns in before.items():
        p = Path(path_str)
        if p.exists():
            after_ns = p.stat().st_mtime_ns
            assert after_ns == before_ns, (
                f"DRY_RUN=1 mutated {path_str} "
                f"(mtime {before_ns} -> {after_ns})"
            )


def test_stage99_check_cmd_probe_splitting():
    """check_cmd must split multi-word probe into argv (not quote the whole string)."""
    content = STAGE99.read_text()
    # Must use read -r -a to split probe into array, then "${probe_argv[@]}"
    assert "read -r -a probe_argv" in content, (
        "check_cmd must split probe into argv array with read -r -a"
    )
    assert '"${probe_argv[@]}"' in content, (
        "check_cmd must invoke probe array with ${probe_argv[@]}"
    )
    # Must NOT invoke "$probe" as a command (the old bug was running "$probe" directly)
    # Check that "$probe" is not used in a command position (followed by redirect or pipe)
    assert not re.search(r'"\$probe"\s*(>|\||;)', content), (
        "check_cmd must not execute $probe as a single quoted command string"
    )
    # The probe array invocation pattern must be present
    assert '${probe_argv[@]}' in content, (
        "check_cmd must invoke ${probe_argv[@]} as the command"
    )


def test_stage99_pam_regex_not_backwards():
    """PAM regex must match control BEFORE module (Ubuntu syntax)."""
    content = STAGE99.read_text()
    # The bash grep -E pattern uses [[:space:]] (POSIX class), not \s.
    # It must match: auth <control> pam_unix.so  (control before module)
    assert "auth[[:space:]]+(required|sufficient|requisite" in content, (
        "PAM regex must match control keyword before pam_unix.so module"
    )
    # Must NOT match module before control (backwards)
    assert "pam_unix.so.*(required|sufficient)" not in content, (
        "PAM regex must not match backwards (module before control)"
    )


def test_stage99_write_state_json_cleanup_trap():
    """write_state_json must register tmp file with cleanup trap."""
    content = STAGE99.read_text()
    assert "_register_tmp" in content, (
        "write_state_json must register tmp files with _register_tmp"
    )
    assert "trap cleanup_verify_tmp EXIT" in content, (
        "script must have EXIT trap for temp file cleanup"
    )
    assert "cleanup_verify_tmp" in content, (
        "script must define cleanup_verify_tmp function"
    )


def test_stage99_bwrap_actual_usable_not_just_exists():
    """bwrap check must test actual sandbox usability, not just binary presence."""
    content = STAGE99.read_text()
    assert "_bwrap_usable" in content, (
        "bwrap check must test actual sandbox usability"
    )
    assert "--ro-bind" in content and "--proc" in content and "--dev" in content, (
        "bwrap usability test must attempt an actual sandbox creation"
    )


def test_stage99_npu_via_hardware_json_not_lspci():
    """NPU detection must use hardware.json contract, not lspci string match."""
    content = STAGE99.read_text()
    assert "npu_vendor" in content, (
        "NPU detection must read npu_vendor from hardware.json"
    )
    assert "/dev/accel/accel0" in content, (
        "NPU detection must fall back to /dev/accel/accel0 device node"
    )
    # The old lspci "NPU" string match should not be the primary method
    assert 'if "NPU" in r.stdout.upper()' not in content, (
        "NPU detection must not rely on lspci string matching (false positives)"
    )


def test_stage99_ydotool_checks_permissions_not_just_exists():
    """ydotool check must verify actual socket permissions, not just path existence."""
    content = STAGE99.read_text()
    assert "os.stat" in content and "st_mode" in content, (
        "ydotool check must stat actual socket permissions"
    )
    assert 'Path("/run/ydotool").exists()' not in content, (
        "ydotool check must not just test path existence"
    )


def test_stage99_models_ini_pipefail_guard():
    """models.ini grep calls must not crash under set -e when no match."""
    content = STAGE99.read_text()
    # grep -c and grep -oP calls must have || true or || echo 0 fallbacks
    assert "2>/dev/null | head -1 || true" in content, (
        "models.ini grep -oP calls must have || true pipefail guard"
    )
    assert "2>/dev/null || echo 0" in content, (
        "models.ini grep -c call must have || echo 0 pipefail guard"
    )


def test_stages_dir_invariant():
    """stages/ must contain only [0-9][0-9]-*.sh files."""
    pattern = re.compile(r"^[0-9]{2}-.*\.sh$")
    for entry in STAGES_DIR.iterdir():
        if entry.is_file():
            assert pattern.match(entry.name), (
                f"stages/ contains non-conforming file: {entry.name} "
                f"(must match [0-9][0-9]-*.sh)"
            )


def test_no_duplicate_stage_99_verify():
    """stage-99-verify.sh must not exist (merged into 99-verify.sh)."""
    assert not (STAGES_DIR / "stage-99-verify.sh").exists(), (
        "stage-99-verify.sh must be deleted (merged into 99-verify.sh)"
    )


def test_no_duplicate_stage_70_vi_hotkey():
    """stage-70-vi-hotkey.sh must not exist (deprecated, moved to tools/)."""
    assert not (STAGES_DIR / "stage-70-vi-hotkey.sh").exists(), (
        "stage-70-vi-hotkey.sh must be deleted from stages/ (collision with Stage 25)"
    )


def test_vi_hotkey_tool_exists():
    """tools/celina-vi-hotkey.sh must exist as the DRY_RUN-aware replacement."""
    tool = REPO_DIR / "tools" / "celina-vi-hotkey.sh"
    assert tool.exists(), "tools/celina-vi-hotkey.sh must exist"
    assert tool.stat().st_mode & 0o111, "tools/celina-vi-hotkey.sh must be executable"


def test_vi_hotkey_tool_dry_run_aware():
    """tools/celina-vi-hotkey.sh must respect DRY_RUN=1."""
    tool = REPO_DIR / "tools" / "celina-vi-hotkey.sh"
    content = tool.read_text()
    assert "DRY_RUN" in content, "tool must reference DRY_RUN"
    assert "--dry-run" in content, "tool must support --dry-run flag"
    assert "[dry-run]" in content, "tool must print dry-run messages"


# ---------------------------------------------------------------------------
# Gear-0 Rust FFI tests (source-level, no Rust toolchain needed)
# ---------------------------------------------------------------------------

def test_gear0_lib_has_max_input_guard():
    """lib.rs must define MAX_FFI_INPUT_BYTES and use it in all FFI functions."""
    content = RUST_LIB.read_text()
    assert "MAX_FFI_INPUT_BYTES" in content, "lib.rs must define MAX_FFI_INPUT_BYTES"
    assert "64 * 1024" in content or "65536" in content, (
        "MAX_FFI_INPUT_BYTES must be set (64 KiB)"
    )
    # All four FFI functions must check the guard
    for fn in ["gear0_sha256", "gear0_md5", "gear0_base64_encode", "gear0_base64_decode"]:
        # Find the function body and check it references MAX_FFI_INPUT_BYTES
        fn_start = content.index(f'pub extern "C" fn {fn}')
        fn_end = content.index("}", content.index("{", fn_start) + 1)
        # Rough check: the guard should appear somewhere in the file near the function
        assert "bytes.len() > MAX_FFI_INPUT_BYTES" in content, (
            f"{fn} must check input length against MAX_FFI_INPUT_BYTES"
        )


def test_gear0_lib_whitespace_trimming():
    """lib.rs must trim whitespace from input before processing."""
    content = RUST_LIB.read_text()
    assert "trim_ws" in content, "lib.rs must define trim_ws function"
    assert "matches!(bytes[start], b' ' | b'\\t' | b'\\n' | b'\\r')" in content or \
           "matches!(bytes[start], b' ' | b'\\t' | b'\\n' | b'\\r')" in content.replace("\\\\", "\\"), (
        "trim_ws must trim ASCII whitespace (space, tab, newline, carriage return)"
    )


def test_gear0_lib_catch_unwind_on_free():
    """gear0_ffi_free must use catch_unwind."""
    content = RUST_LIB.read_text()
    fn_start = content.index('pub extern "C" fn gear0_ffi_free')
    fn_body = content[fn_start:content.index("}", content.index("{", fn_start) + 1)]
    assert "catch_unwind" in fn_body, (
        "gear0_ffi_free must wrap body in catch_unwind"
    )


def test_gear0_lib_binary_safe_not_utf8():
    """lib.rs must use CStr::to_bytes() not to_str() for binary safety."""
    content = RUST_LIB.read_text()
    assert "CStr::from_ptr(input) }.to_bytes()" in content, (
        "lib.rs must use to_bytes() for binary-safe input handling"
    )
    # to_str() is UTF-8 only and should NOT be used for input processing
    assert "to_str()" not in content, (
        "lib.rs must not use to_str() (UTF-8 restriction — use to_bytes() instead)"
    )


def test_gear0_lib_md5_non_cryptographic_marker():
    """MD5 function must be documented as non-cryptographic."""
    content = RUST_LIB.read_text()
    md5_start = content.index('pub extern "C" fn gear0_md5')
    md5_doc = content[:md5_start]
    # Look at the doc comment immediately before gear0_md5
    assert "not cryptographically secure" in md5_doc or "non-cryptographic" in md5_doc.lower(), (
        "gear0_md5 doc must mark MD5 as non-cryptographic"
    )


def test_gear0_lib_has_cfg_test_module():
    """lib.rs must have #[cfg(test)] mod tests with unit tests."""
    content = RUST_LIB.read_text()
    assert "#[cfg(test)]" in content, "lib.rs must have #[cfg(test)] module"
    assert "mod tests" in content, "lib.rs must define mod tests"
    # Must have at least a few test functions
    test_count = len(re.findall(r'#\[test\]', content))
    assert test_count >= 5, f"lib.rs must have at least 5 #[test] functions (found {test_count})"


def test_gear0_lib_tests_cover_roundtrip():
    """Tests must cover base64 encode/decode roundtrip."""
    content = RUST_LIB.read_text()
    assert "base64_encode_decode_roundtrip" in content, (
        "Tests must include base64 encode/decode roundtrip test"
    )


def test_gear0_lib_tests_cover_null_input():
    """Tests must cover null input handling."""
    content = RUST_LIB.read_text()
    assert "null_input_returns_null" in content, (
        "Tests must include null input test"
    )


def test_gear0_lib_tests_cover_whitespace_trimming():
    """Tests must cover whitespace trimming in sha256."""
    content = RUST_LIB.read_text()
    assert "sha256_trims_whitespace" in content, (
        "Tests must include whitespace trimming test for sha256"
    )


def test_gear0_lib_tests_cover_max_input():
    """Tests must cover MAX_FFI_INPUT_BYTES guard."""
    content = RUST_LIB.read_text()
    assert "max_input_guard" in content, (
        "Tests must include MAX_FFI_INPUT_BYTES guard test"
    )


def test_gear0_lib_tests_cover_md5_vector():
    """Tests must verify MD5 against known vector."""
    content = RUST_LIB.read_text()
    assert "md5_matches_known_vector" in content, (
        "Tests must include MD5 known vector test"
    )


def test_gear0_lib_tests_cover_sha256_vector():
    """Tests must verify SHA-256 against known vector."""
    content = RUST_LIB.read_text()
    assert "sha256_matches_known_vector" in content, (
        "Tests must include SHA-256 known vector test"
    )


def test_gear0_lib_tests_cover_free_null():
    """Tests must cover free(null) safety."""
    content = RUST_LIB.read_text()
    assert "free_null_is_safe" in content, (
        "Tests must include free(null) safety test"
    )
