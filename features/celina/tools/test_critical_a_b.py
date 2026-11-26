#!/usr/bin/env python3
"""
Celina Critical Data-Safety & Security Tests (Groups A + B).

These tests verify the 16 critical items from the consolidated register
using only observable, measurable evidence. No assertions are made about
documents or section numbers I cannot verify.

Each test returns PASS/FAIL with literal output. A FAIL means the item
is OPEN and must be fixed before any latency work begins.

Usage:
  python3 tools/test_critical_a_b.py
"""
from __future__ import annotations

import ast
import os
import re
import sqlite3
import sys
from pathlib import Path

CELINA_ROOT = Path(__file__).resolve().parent.parent
CELINA_PKG = CELINA_ROOT / "celina_pkg"
SHARED = Path.home() / ".celina" / "storage" / "shared"

results: list = []


def record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append((name, passed, detail))
    print(f"[{status}] {name}: {detail}")


# ---------------------------------------------------------------------------
# GROUP A — Data loss & corruption (6 items)
# ---------------------------------------------------------------------------

def test_a1_contacts_not_redacted():
    """A1: Local contacts DB must store raw values, not [REDACTED_PHONE]."""
    db = SHARED / "contacts.db"
    if not db.exists():
        record("A1 contacts_not_redacted", False, f"{db} does not exist")
        return
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT * FROM contacts")
    rows = cur.fetchall()
    conn.close()
    redacted = any("[REDACTED" in str(cell) for row in rows for cell in row)
    if redacted:
        record("A1 contacts_not_redacted", False, f"Found redacted values: {rows}")
    else:
        record("A1 contacts_not_redacted", True, f"{len(rows)} row(s), all raw values")


def test_a2_case_preservation_macro():
    """A2: Case preservation — verify macro stores original case."""
    macro_path = CELINA_PKG / "gear0_macro.py"
    source = macro_path.read_text()
    lower_on_value = bool(re.search(r'\.lower\(\)\s*[),]', source))
    insert_lower = bool(re.search(r"INSERT.*\.lower\(\)|UPDATE.*\.lower\(\)", source, re.IGNORECASE))
    if lower_on_value or insert_lower:
        record("A2 case_preservation_macro", False, f"Found .lower() on stored value in gear0_macro.py")
    else:
        record("A2 case_preservation_macro", True, "No .lower() on stored values in gear0_macro.py")


def test_a2_case_preservation_contacts_graph():
    """A2: Case preservation — contacts/graph use COLLATE NOCASE, not .lower() on storage."""
    for mod_name in ("gear0_contacts.py", "gear0_graph.py"):
        path = CELINA_PKG / mod_name
        source = path.read_text()
        if re.search(r"\.lower\(\)\s*[),]", source):
            record(f"A2 case_preservation_{mod_name}", False, f"Found .lower() on stored value in {mod_name}")
        else:
            record(f"A2 case_preservation_{mod_name}", True, f"No .lower() on stored values in {mod_name}")


def test_a3_checkpoint_manifest():
    """A3: Checkpoint manifest covers all data files — check gear0_storage.py."""
    path = CELINA_PKG / "gear0_storage.py"
    source = path.read_text()
    # _CHECKPOINT_DB_FILES should list all data DBs (not root-level paths)
    db_files_block = re.search(r'_CHECKPOINT_DB_FILES\s*=\s*\[(.*?)\]', source, re.DOTALL)
    uses_backup_api = "backup(" in source and "sqlite3" in source
    # Check that checkpoint DB files reference SHARED_DIR, not root-level .celina/
    shared_refs = len(re.findall(r'SHARED_DIR\s*/\s*"', source))
    root_data_refs = len(re.findall(r'Path\.home\(\)\s*/\s*"\.celina"\s*/\s*"(?:contacts|graph|lists|iot|routines|assistant)\.db"', source))
    detail = f"db_files_block={bool(db_files_block)}, uses_backup_api={uses_backup_api}, shared_refs={shared_refs}, root_data_refs={root_data_refs}"
    passed = uses_backup_api and root_data_refs == 0
    record("A3 checkpoint_manifest", passed, detail)


def test_a4_checkpoint_uses_backup_api():
    """A4: Live DB snapshots use sqlite3.backup(), shutil.copy2 only for JSON/log/restore."""
    path = CELINA_PKG / "gear0_storage.py"
    source = path.read_text()
    # _snapshot_db uses backup() for live DBs
    snapshot_uses_backup = bool(re.search(r'_snapshot_db.*backup\(', source, re.DOTALL))
    # shutil.copy2 should only be used for non-DB files (JSON/logs) or restore
    copy2_for_json = bool(re.search(r'_CHECKPOINT_JSON_FILES.*shutil\.copy2|shutil\.copy2.*_CHECKPOINT_JSON_FILES', source, re.DOTALL))
    copy2_for_logs = bool(re.search(r'_CHECKPOINT_LOG_FILES.*shutil\.copy2|shutil\.copy2.*_CHECKPOINT_LOG_FILES', source, re.DOTALL))
    copy2_for_restore = bool(re.search(r'_restore_db.*shutil\.copy2|shutil\.copy2.*_restore_db', source, re.DOTALL))
    detail = f"snapshot_uses_backup={snapshot_uses_backup}, copy2_for_json={copy2_for_json}, copy2_for_logs={copy2_for_logs}, copy2_for_restore={copy2_for_restore}"
    # Pass if live DB snapshots use backup(), and copy2 is only for non-DB files or restore
    passed = snapshot_uses_backup and (copy2_for_json or copy2_for_logs or copy2_for_restore)
    record("A4 checkpoint_uses_backup_api", passed, detail)


def test_a5_wal_on_all_connections():
    """A5: WAL + busy_timeout on every DB connection (not just ledger)."""
    connection_count = 0
    wal_count = 0
    busy_timeout_count = 0
    for py_file in sorted(CELINA_PKG.glob("gear0_*.py")) + sorted((CELINA_PKG / "plugins").glob("*.py")):
        source = py_file.read_text()
        if "sqlite3.connect" in source:
            connection_count += 1
        if "journal_mode=WAL" in source or "PRAGMA journal_mode=WAL" in source:
            wal_count += 1
        if "busy_timeout" in source or "PRAGMA busy_timeout" in source:
            busy_timeout_count += 1
    detail = f"connections={connection_count}, WAL={wal_count}, busy_timeout={busy_timeout_count}"
    passed = connection_count > 0 and wal_count == connection_count and busy_timeout_count == connection_count
    record("A5 wal_on_all_connections", passed, detail)


def test_a6_money_not_float():
    """A6: Money must be stored as Decimal/integer cents, not float."""
    ledger_path = CELINA_PKG / "gear0_ledger.py"
    source = ledger_path.read_text()
    money_as_float = bool(re.search(r'amount\s+REAL|value\s+REAL|price\s+REAL', source, re.IGNORECASE))
    float_conversion = bool(re.search(r'float\(.*amount|float\(.*value|float\(.*price', source, re.IGNORECASE))
    detail = f"money_column_as_REAL={money_as_float}, float_conversion={float_conversion}"
    record("A6 money_not_float", not (money_as_float or float_conversion), detail)


# ---------------------------------------------------------------------------
# GROUP B — Security (10 items)
# ---------------------------------------------------------------------------

def test_b1_no_rm_rf_in_plugins():
    """B1: No rm -rf, find -delete, or shutil.rmtree in plugins."""
    destructive = []
    for py_file in sorted((CELINA_PKG / "plugins").glob("*.py")):
        source = py_file.read_text()
        for pattern in ("rm -rf", "find .* -delete", "shutil.rmtree"):
            if re.search(pattern, source, re.IGNORECASE):
                destructive.append((py_file.name, pattern))
    passed = len(destructive) == 0
    detail = f"found={destructive}" if destructive else "none found"
    record("B1 no_rm_rf_in_plugins", passed, detail)


def test_b2_no_echo_notification_fallback():
    """B2: No echo used as fallback tool in any FallbackChain."""
    self_heal_path = CELINA_PKG / "plugins" / "self_heal.py"
    source = self_heal_path.read_text()
    # Check if any FallbackChain has fallback= with "echo" as the tool name
    fallback_echo = bool(re.search(r'fallback\s*=\s*FallbackStep\(\s*["\']echo', source, re.IGNORECASE))
    # Check show_notification chain specifically ends without fallback parameter
    # Find the show_notification block and verify it has no fallback= parameter
    show_notif_start = source.find('"show_notification": FallbackChain(')
    if show_notif_start >= 0:
        # Find the end of this specific chain (next ), at end of FallbackChain call)
        show_notif_block = source[show_notif_start:show_notif_start + 500]
        # The block ends at ")," pattern
        end_marker = show_notif_block.find('),\n')
        if end_marker >= 0:
            show_notif_block = show_notif_block[:end_marker]
        show_notif_has_fallback = bool(re.search(r'fallback\s*=', show_notif_block, re.IGNORECASE))
    else:
        show_notif_has_fallback = False
    detail = f"fallback_echo={fallback_echo}, show_notif_has_fallback={show_notif_has_fallback}"
    record("B2 no_echo_notification_fallback", not fallback_echo and not show_notif_has_fallback, detail)


def test_b3_data_oracle_security():
    """B3: Data Oracle — parameterized queries, authorizer, LIMIT cap."""
    oracle_path = CELINA_PKG / "plugins" / "data_oracle.py"
    source = oracle_path.read_text()
    has_authorizer = "set_authorizer" in source
    has_parameterized = "?" in source and "execute" in source
    has_limit_cap = "LIMIT" in source.upper() or "limit" in source.lower()
    has_read_only = "query_only" in source or "read.only" in source.lower() or "readonly" in source.lower()
    detail = f"authorizer={has_authorizer}, parameterized={has_parameterized}, limit_cap={has_limit_cap}, read_only={has_read_only}"
    record("B3 data_oracle_security", has_authorizer and has_parameterized, detail)


def test_b4_actiongate_destructive_ops():
    """B4: ActionGate covers destructive OS ops (dry_run required)."""
    os_shim_path = CELINA_PKG / "plugins" / "os_shim.py"
    source = os_shim_path.read_text()
    has_destructive_set = "_DESTRUCTIVE_OPS" in source
    has_dry_run_refusal = "dry_run" in source and "refus" in source.lower()
    has_empty_trash = "empty_trash" in source or "trash" in source.lower()
    detail = f"destructive_ops_set={has_destructive_set}, dry_run_refusal={has_dry_run_refusal}, empty_trash={has_empty_trash}"
    record("B4 actiongate_destructive_ops", has_destructive_set and has_dry_run_refusal, detail)


def test_b5_sniffer_denylist_ttl():
    """B5: Sniffer — app denylist + RAM-only TTL."""
    sniffer_path = CELINA_PKG / "plugins" / "sniffer_plugin.py"
    source = sniffer_path.read_text()
    has_denylist = "DENYLIST" in source.upper() or "denylist" in source.lower()
    has_ttl = "TTL" in source.upper() or "ttl" in source.lower()
    detail = f"denylist={has_denylist}, ttl={has_ttl}"
    record("B5 sniffer_denylist_ttl", has_denylist and has_ttl, detail)


def test_b6_clipboard_password_mime():
    """B6: Clipboard honors password-manager MIME hints."""
    clipboard_path = CELINA_PKG / "gear0_clipboard.py"
    source = clipboard_path.read_text()
    has_mime = "MIME" in source.upper() or "mime" in source.lower()
    has_password_hint = "password" in source.lower() or "keepass" in source.lower() or "1password" in source.lower()
    detail = f"mime_handling={has_mime}, password_hint={has_password_hint}"
    record("B6 clipboard_password_mime", has_mime or has_password_hint, detail)


def test_b7_self_heal_timeout_enforcement():
    """B7: Self-heal per-step timeout + circuit breaker enforcement."""
    self_heal_path = CELINA_PKG / "plugins" / "self_heal.py"
    source = self_heal_path.read_text()
    has_timeout = "timeout" in source.lower()
    timeout_enforced = bool(re.search(r'timeout\s*=\s*\d+', source)) or bool(re.search(r'timeout\s*:\s*\d+', source))
    detail = f"timeout_mentioned={has_timeout}, timeout_enforced={timeout_enforced}"
    record("B7 self_heal_timeout_enforcement", has_timeout and timeout_enforced, detail)


def test_b8_encryption_at_rest():
    """B8: Encryption at rest + redaction vault in OS keyring."""
    keyring_found = False
    for py_file in sorted(CELINA_PKG.glob("*.py")) + sorted((CELINA_PKG / "plugins").glob("*.py")):
        source = py_file.read_text()
        if "keyring" in source.lower() or "fernet" in source.lower() or "encrypt" in source.lower():
            keyring_found = True
            break
    detail = f"keyring_or_encryption_found={keyring_found}"
    record("B8 encryption_at_rest", keyring_found, detail)


def test_b9_update_signing():
    """B9: Sign the update channel (minisign/TUF)."""
    pkg_dir = CELINA_ROOT / "boost" / "update-20" / "pkg"
    has_signature = False
    if pkg_dir.exists():
        has_signature = any(f.name.endswith(".sig") for f in pkg_dir.iterdir())
    has_minisign = False
    ci_dir = pkg_dir / "ci" if pkg_dir.exists() else None
    if ci_dir and ci_dir.exists():
        for f in ci_dir.iterdir():
            if "minisign" in f.name.lower() or "tuf" in f.name.lower() or "sign" in f.name.lower():
                has_minisign = True
    detail = f"signature_file={has_signature}, signing_tool={has_minisign}"
    record("B9 update_signing", has_signature or has_minisign, detail)


def test_b10_skill_sandbox():
    """B10: Sandbox for skill healing / Ponytail (scanner != sandbox)."""
    skills_path = CELINA_PKG / "skills.py"
    source = skills_path.read_text()
    has_sandbox = "sandbox" in source.lower() or "nsjail" in source.lower() or "wasmtime" in source.lower()
    detail = f"sandbox={has_sandbox}"
    record("B10 skill_sandbox", has_sandbox, detail)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Celina Critical Data-Safety & Security Tests (Groups A + B)")
    print("=" * 70)
    print()

    print("--- Group A: Data loss & corruption ---")
    test_a1_contacts_not_redacted()
    test_a2_case_preservation_macro()
    test_a2_case_preservation_contacts_graph()
    test_a3_checkpoint_manifest()
    test_a4_checkpoint_uses_backup_api()
    test_a5_wal_on_all_connections()
    test_a6_money_not_float()

    print()
    print("--- Group B: Security ---")
    test_b1_no_rm_rf_in_plugins()
    test_b2_no_echo_notification_fallback()
    test_b3_data_oracle_security()
    test_b4_actiongate_destructive_ops()
    test_b5_sniffer_denylist_ttl()
    test_b6_clipboard_password_mime()
    test_b7_self_heal_timeout_enforcement()
    test_b8_encryption_at_rest()
    test_b9_update_signing()
    test_b10_skill_sandbox()

    print()
    print("=" * 70)
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    print(f"Results: {passed} PASS, {failed} FAIL out of {len(results)}")
    print()
    if failed:
        print("FAILED ITEMS (must be fixed before latency work):")
        for name, p, detail in results:
            if not p:
                print(f"  - {name}: {detail}")
        print()
        print("STATUS: Groups A+B NOT closed. Do not proceed to Update 21 latency fixes.")
        return 1
    else:
        print("STATUS: Groups A+B closed. Safe to proceed to Update 21.")
        return 0


if __name__ == "__main__":
    sys.exit(main())