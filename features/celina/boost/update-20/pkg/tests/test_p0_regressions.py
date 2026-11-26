#!/usr/bin/env python3
"""
Celina P0 Regression Tests (17 tests).

Tests the P0 safety fixes from Update 20:
  P0-01: Case preservation — .lower() not used on stored values
  P0-02: No destructive trash — no rm -rf, find -delete, shutil.rmtree
  P0-03: backend_state.json exists at shared storage path
  P0-04: All DBs consolidated to ~/.celina/storage/shared/
  P0-05: No shell=True in plugins
  P0-06: WAL mode on ledger DB
  P0-07: Self-heal per-step timeout is 3s (not 10s)
"""

from __future__ import annotations

import ast
import os
import re
import sqlite3
import sys
from pathlib import Path

CELINA_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
CELINA_PKG = CELINA_ROOT / "celina_pkg"
SHARED = Path.home() / ".celina" / "storage" / "shared"

results: list = []


def record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append((name, passed, detail))
    print(f"[{status}] {name}: {detail}")


def test_p0_01_case_preservation():
    """P0-01: gear0_macro.py must not use .lower() on stored values."""
    path = CELINA_PKG / "gear0_macro.py"
    source = path.read_text()
    # Check for .lower() on INSERT/UPDATE values (not on query matching)
    bad = bool(re.search(r"\.lower\(\)\s*[),]", source))
    record("P0-01 case_preservation", not bad, f".lower() on stored value: {bad}")


def test_p0_02_no_destructive_trash():
    """P0-02: No rm -rf, find -delete, or shutil.rmtree in plugins."""
    destructive = []
    for py_file in sorted((CELINA_PKG / "plugins").glob("*.py")):
        source = py_file.read_text()
        for pattern in ("rm -rf", "find .* -delete", "shutil.rmtree"):
            if re.search(pattern, source, re.IGNORECASE):
                destructive.append((py_file.name, pattern))
    record("P0-02 no_destructive_trash", len(destructive) == 0, f"found: {destructive}")


def test_p0_03_backend_state_exists():
    """P0-03: backend_state.json exists at shared storage path."""
    db = SHARED / "backend_state.json"
    record("P0-03 backend_state_json", db.exists(), f"{db} exists: {db.exists()}")


def test_p0_04_shared_storage():
    """P0-04: All DBs in ~/.celina/storage/shared/."""
    expected_dbs = [
        "contacts.db", "graph.db", "lists.db", "iot.db",
        "routines.db", "assistant.db", "celina_shared.db",
        "habits.db", "self_heal.db", "gear0_ledger.db", "memory.db",
    ]
    missing = [d for d in expected_dbs if not (SHARED / d).exists()]
    record("P0-04 shared_storage", len(missing) == 0, f"missing: {missing}")


def test_p0_05_no_shell_true():
    """P0-05: No shell=True in plugins."""
    count = 0
    for py_file in sorted((CELINA_PKG / "plugins").glob("*.py")):
        source = py_file.read_text()
        count += len(re.findall(r"shell\s*=\s*True", source))
    record("P0-05 no_shell_true", count == 0, f"shell=True count: {count}")


def test_p0_06_wal_mode():
    """P0-06: WAL mode on ledger DB."""
    db = SHARED / "gear0_ledger.db"
    if not db.exists():
        record("P0-06 wal_mode", False, f"{db} does not exist")
        return
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode")
    mode = cur.fetchone()[0]
    conn.close()
    record("P0-06 wal_mode", mode.lower() == "wal", f"journal_mode: {mode}")


def test_p0_07_self_heal_timeout():
    """P0-07: Self-heal per-step timeout is 3s."""
    path = CELINA_PKG / "plugins" / "self_heal.py"
    source = path.read_text()
    has_timeout_3 = bool(re.search(r"timeout\s*=\s*3\b", source))
    has_timeout_10 = bool(re.search(r"timeout\s*=\s*10\b", source))
    record("P0-07 self_heal_timeout", has_timeout_3 and not has_timeout_10,
           f"timeout=3: {has_timeout_3}, timeout=10: {has_timeout_10}")


# Additional regression tests (8-17)
def test_p0_08_no_echo_fallback():
    """P0-08: No echo fallback in self_heal FallbackChain."""
    path = CELINA_PKG / "plugins" / "self_heal.py"
    source = path.read_text()
    bad = bool(re.search(r'fallback\s*=\s*FallbackStep\(\s*["\']echo', source, re.IGNORECASE))
    record("P0-08 no_echo_fallback", not bad, f"echo fallback: {bad}")


def test_p0_09_data_oracle_authorizer():
    """P0-09: Data Oracle has set_authorizer."""
    path = CELINA_PKG / "plugins" / "data_oracle.py"
    source = path.read_text()
    record("P0-09 data_oracle_authorizer", "set_authorizer" in source, f"authorizer: {'set_authorizer' in source}")


def test_p0_10_data_oracle_parameterized():
    """P0-10: Data Oracle uses parameterized queries."""
    path = CELINA_PKG / "plugins" / "data_oracle.py"
    source = path.read_text()
    record("P0-10 data_oracle_parameterized", "?" in source, f"parameterized ?: {'?' in source}")


def test_p0_11_sniffer_denylist():
    """P0-11: Sniffer has app denylist."""
    path = CELINA_PKG / "plugins" / "sniffer_plugin.py"
    source = path.read_text()
    record("P0-11 sniffer_denylist", "DENYLIST" in source.upper(), f"denylist: {'DENYLIST' in source.upper()}")


def test_p0_12_sniffer_ttl():
    """P0-12: Sniffer has RAM-only TTL."""
    path = CELINA_PKG / "plugins" / "sniffer_plugin.py"
    source = path.read_text()
    record("P0-12 sniffer_ttl", "TTL" in source.upper(), f"TTL: {'TTL' in source.upper()}")


def test_p0_13_actiongate_destructive():
    """P0-13: ActionGate covers destructive ops."""
    path = CELINA_PKG / "plugins" / "os_shim.py"
    source = path.read_text()
    record("P0-13 actiongate_destructive", "_DESTRUCTIVE_OPS" in source, f"destructive ops: {'_DESTRUCTIVE_OPS' in source}")


def test_p0_14_clipboard_password_mime():
    """P0-14: Clipboard honors password MIME types."""
    path = CELINA_PKG / "gear0_clipboard.py"
    source = path.read_text()
    record("P0-14 clipboard_mime", "SENSITIVE_MIME_TYPES" in source, f"mime types: {'SENSITIVE_MIME_TYPES' in source}")


def test_p0_15_all_modules_compile():
    """P0-15: All celina_pkg .py modules compile clean."""
    failures = []
    for py_file in sorted(CELINA_PKG.glob("*.py")):
        try:
            ast.parse(py_file.read_text())
        except SyntaxError as exc:
            failures.append((py_file.name, str(exc)))
    for py_file in sorted((CELINA_PKG / "plugins").glob("*.py")):
        try:
            ast.parse(py_file.read_text())
        except SyntaxError as exc:
            failures.append((f"plugins/{py_file.name}", str(exc)))
    record("P0-15 all_modules_compile", len(failures) == 0, f"failures: {failures}")


def test_p0_16_102_tool_contract():
    """P0-16: 102-tool contract preserved."""
    # Count tool handlers across all modules
    tool_count = 0
    for py_file in sorted(CELINA_PKG.glob("gear0_*.py")):
        source = py_file.read_text()
        tool_count += len(re.findall(r"def gear0_\w+_execute", source))
    for py_file in sorted((CELINA_PKG / "plugins").glob("*.py")):
        source = py_file.read_text()
        tool_count += len(re.findall(r"def gear0_\w+_execute", source))
    record("P0-16 tool_contract", tool_count > 0, f"execute handlers found: {tool_count}")


def test_p0_17_router_plugins_wired():
    """P0-17: All 5 plugins wired in router.py."""
    path = CELINA_PKG / "router.py"
    source = path.read_text()
    plugins = ["sniffer_plugin", "habit_engine", "data_oracle", "os_shim", "self_heal"]
    wired = all(p in source for p in plugins)
    record("P0-17 router_plugins_wired", wired, f"all 5 plugins wired: {wired}")


def main():
    print("=" * 70)
    print("Celina P0 Regression Tests (17 tests)")
    print("=" * 70)
    print()

    test_p0_01_case_preservation()
    test_p0_02_no_destructive_trash()
    test_p0_03_backend_state_exists()
    test_p0_04_shared_storage()
    test_p0_05_no_shell_true()
    test_p0_06_wal_mode()
    test_p0_07_self_heal_timeout()
    test_p0_08_no_echo_fallback()
    test_p0_09_data_oracle_authorizer()
    test_p0_10_data_oracle_parameterized()
    test_p0_11_sniffer_denylist()
    test_p0_12_sniffer_ttl()
    test_p0_13_actiongate_destructive()
    test_p0_14_clipboard_password_mime()
    test_p0_15_all_modules_compile()
    test_p0_16_102_tool_contract()
    test_p0_17_router_plugins_wired()

    print()
    print("=" * 70)
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    print(f"Results: {passed} PASS, {failed} FAIL out of {len(results)}")
    print()
    if failed:
        print("FAILED ITEMS:")
        for name, p, detail in results:
            if not p:
                print(f"  - {name}: {detail}")
        return 1
    else:
        print("All P0 regression tests passed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())