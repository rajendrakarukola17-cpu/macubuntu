#!/usr/bin/env python3
# ============================================================================
# tools/inject_parity_break.py — Prove the Stage 00 parity gate catches violations
#
# Temporarily corrupts the capability manifest, runs stages/00-preflight.sh,
# asserts it exits non-zero, then restores the manifest.
#
# Usage:
#   python3 tools/inject_parity_break.py --dry    # dry-run: just validate the script can run
#   python3 tools/inject_parity_break.py          # full: corrupt, run preflight, restore
# ============================================================================

from __future__ import annotations

import hashlib
import json
import shutil
import signal
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_A = REPO_ROOT / "system" / "capabilities" / "celina-capabilities.json"
MANIFEST_B = REPO_ROOT / "features" / "celina" / "config" / "celina-capabilities.json"
SCHEMA_PATH = REPO_ROOT / "features" / "celina" / "config" / "neural-schema.json"
PREFLIGHT = REPO_ROOT / "stages" / "00-preflight.sh"

# Global backups so signal handlers can restore on interrupt
_a_backup: bytes | None = None
_b_backup: bytes | None = None
_s_backup: bytes | None = None
_dirty = False


def _restore_from_backups() -> None:
    """Restore all three files from the global backups (idempotent, no-op if None)."""
    global _dirty
    if _a_backup is not None:
        MANIFEST_A.write_bytes(_a_backup)
    if _b_backup is not None:
        MANIFEST_B.write_bytes(_b_backup)
    if _s_backup is not None:
        SCHEMA_PATH.write_bytes(_s_backup)
    _dirty = False


def _signal_handler(signum, frame):
    """Restore corrupted files on SIGINT/SIGTERM so we never leave the repo dirty."""
    signame = signal.Signals(signum).name
    print(f"\n  ⚠️  Received {signame} — restoring corrupted files before exit...", file=sys.stderr)
    if _dirty:
        _restore_from_backups()
        print("  ✅ Files restored.", file=sys.stderr)
    sys.exit(130 + signum)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def canonical_hash(obj: dict) -> str:
    obj_copy = {k: v for k, v in obj.items() if k != "integrity"}
    canonical = json.dumps(obj_copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def backup_files():
    return (
        MANIFEST_A.read_bytes(),
        MANIFEST_B.read_bytes(),
        SCHEMA_PATH.read_bytes(),
    )


def restore_files(a_bytes, b_bytes, s_bytes):
    MANIFEST_A.write_bytes(a_bytes)
    MANIFEST_B.write_bytes(b_bytes)
    SCHEMA_PATH.write_bytes(s_bytes)


def corrupt_auth_operation():
    """Remove auth_operation from one tool — should fail auth_operation coverage check."""
    data = json.loads(MANIFEST_A.read_text(encoding="utf-8"))
    first_tool = next(iter(data["tools"]))
    tool_entry = data["tools"][first_tool]
    if "auth_operation" not in tool_entry:
        print(f"  (skip: '{first_tool}' has no auth_operation to remove)", file=sys.stderr)
        return
    del tool_entry["auth_operation"]
    # Recompute hash so integrity check passes (only auth_operation check should fail)
    data["integrity"]["sha256"] = canonical_hash(data)
    MANIFEST_A.write_text(json.dumps(data, indent=2), encoding="utf-8")
    shutil.copy2(MANIFEST_A, MANIFEST_B)
    print(f"  Injected: removed auth_operation from '{first_tool}'")


def corrupt_fs_scope():
    """Set fs_scope to 'proc_sys_ro' on one tool — should fail vocabulary check."""
    data = json.loads(MANIFEST_A.read_text(encoding="utf-8"))
    first_tool = next(iter(data["tools"]))
    data["tools"][first_tool]["fs_scope"] = "proc_sys_ro"
    data["integrity"]["sha256"] = canonical_hash(data)
    MANIFEST_A.write_text(json.dumps(data, indent=2), encoding="utf-8")
    shutil.copy2(MANIFEST_A, MANIFEST_B)
    print(f"  Injected: set fs_scope='proc_sys_ro' on '{first_tool}'")


def corrupt_tier_overlap():
    """Add a tool to both tier1 and tier2 — should fail tier string parity."""
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    first_tool = next(iter(data["security_policy"]["tier1_auto_tools"]))
    data["security_policy"]["tier2_approval_tools"].append(first_tool)
    SCHEMA_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  Injected: added '{first_tool}' to both tier1 and tier2")


def corrupt_trailing_space():
    """Add trailing space to a tier entry — should fail tier string parity."""
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    t1 = data["security_policy"]["tier1_auto_tools"]
    if t1:
        t1[0] = t1[0] + " "
    SCHEMA_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  Injected: trailing space on tier1 entry '{t1[0]}'")


def run_preflight():
    """Run stages/00-preflight.sh and return its exit code."""
    result = subprocess.run(
        ["bash", str(PREFLIGHT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        for line in result.stderr.splitlines():
            if "PARITY GATE FAILURE" in line:
                print(f"  Gate caught: {line.strip()}")
    return result.returncode


def run_injection_test(name, corrupt_fn):
    """Run a single corruption→preflight→restore cycle. Returns True if gate caught it."""
    global _dirty
    print(f"\n--- {name} ---")
    corrupt_fn()
    _dirty = True
    rc = run_preflight()
    _restore_from_backups()
    _dirty = False
    if rc == 0:
        print(f"  ❌ Preflight did NOT catch {name.lower()}!")
        return False
    print(f"  ✅ Preflight caught {name.lower()}")
    return True


def main():
    dry_run = "--dry" in sys.argv

    if dry_run:
        print("=== DRY-RUN: validating inject_parity_break.py can load manifests ===")
        try:
            json.loads(MANIFEST_A.read_text(encoding="utf-8"))
            json.loads(MANIFEST_B.read_text(encoding="utf-8"))
            json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            print("  All files load correctly")
            return 0
        except Exception as e:
            print(f"  ❌ File load failed: {e}")
            return 1

    print("=== Parity Gate Injection Test ===")
    print(f"  Manifest A: {MANIFEST_A}")
    print(f"  Manifest B: {MANIFEST_B}")
    print(f"  Schema:     {SCHEMA_PATH}")
    print(f"  Preflight:  {PREFLIGHT}")

    # First: verify preflight passes on clean state
    print("\n--- Phase 1: Clean preflight (should pass) ---")
    rc = run_preflight()
    if rc != 0:
        print("  ❌ Preflight FAILED on clean state — fix before injection test")
        return 1
    print("  ✅ Preflight passes on clean state")

    global _a_backup, _b_backup, _s_backup, _dirty
    _a_backup, _b_backup, _s_backup = backup_files()

    all_passed = True
    all_passed &= run_injection_test("Phase 2: Inject missing auth_operation", corrupt_auth_operation)
    all_passed &= run_injection_test("Phase 3: Inject proc_sys_ro fs_scope", corrupt_fs_scope)
    all_passed &= run_injection_test("Phase 4: Inject tier1/tier2 overlap", corrupt_tier_overlap)
    all_passed &= run_injection_test("Phase 5: Inject trailing whitespace", corrupt_trailing_space)

    # Final safety restore (backups already restored by run_injection_test, but be safe)
    _restore_from_backups()
    _dirty = False

    if all_passed:
        print("\n=== All injection tests passed — parity gate catches every violation ===")
        return 0
    print("\n=== SOME INJECTION TESTS FAILED ===", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())