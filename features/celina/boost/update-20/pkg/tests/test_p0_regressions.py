"""P0 Safety Regression Tests for Update 20 — "Truth & Safety"."""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import sqlite3  # noqa: E402

WORKSPACE_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
CELINA_PKG = os.path.join(WORKSPACE_ROOT, "celina_pkg")

from celina_pkg.gear0_contacts import contact_save, contact_lookup  # noqa: E402
from celina_pkg.gear0_graph import graph_set, graph_get  # noqa: E402
from celina_pkg.gear0_storage import create_checkpoint, restore_checkpoint  # noqa: E402
from celina_pkg.plugins.data_oracle import (  # noqa: E402
    load_csv_to_memory,
    translate_nl_to_sql,
    execute_sql,
    _authorizer,
)
from celina_pkg.plugins.os_shim import gear0_os_classify, gear0_os_execute  # noqa: E402
from celina_pkg.plugins.self_heal import SelfHealingFallback  # noqa: E402
from celina_pkg.plugins.sniffer_plugin import (  # noqa: E402
    ContextSnapshot,
    SNIFFER_APP_DENYLIST,
    SNIFFER_TTL_SECONDS,
)

DEFAULT_CHAINS = SelfHealingFallback.DEFAULT_CHAINS


def _read_source(rel_path: str) -> str:
    full = os.path.join(CELINA_PKG, rel_path)
    with open(full) as f:
        return f.read()


def test_contact_case_preserved():
    contact_save("John Doe", "+1234567890")
    assert contact_lookup("john doe") is not None
    assert contact_lookup("JOHN DOE") is not None


def test_graph_schema_has_collate_nocase():
    src = _read_source("gear0_graph.py")
    assert "COLLATE NOCASE" in src


def test_graph_alias_case_preserved(tmp_path):
    import celina_pkg.gear0_graph as graph_module
    tmp_db = tmp_path / "test_graph.db"
    graph_module._GRAPH_DB_PATH = tmp_db
    if tmp_db.exists():
        tmp_db.unlink()
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS aliases ("
        "alias TEXT PRIMARY KEY COLLATE NOCASE, "
        "kind TEXT NOT NULL, value TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()
    conn.close()
    graph_set("MyProject", "path", "/home/user/projects")
    assert graph_get("myproject") is not None
    assert graph_get("MYPROJECT") is not None


def test_no_rmtree_in_os_shim():
    src = _read_source("plugins/os_shim.py")
    assert "shutil.rmtree" not in src
    assert "item.unlink" not in src


def test_no_find_delete_in_self_heal():
    src = _read_source("plugins/self_heal.py")
    has_find_delete = "find" in src and "-delete" in src
    assert not has_find_delete


def test_checkpoint_covers_all_files():
    cp = create_checkpoint("regression_test")
    manifest_path = os.path.join(cp, "manifest.json")
    assert os.path.exists(manifest_path)
    import json
    manifest = json.loads(open(manifest_path).read())
    files = manifest.get("files", [])
    required = [
        "celina_shared.db", "habits.db", "self_heal.db",
        "gear0_ledger.db", "memory.db", "gear0_macros.json",
        "backend_state.json",
    ]
    for name in required:
        assert name in files, f"Checkpoint missing {name}"
    assert restore_checkpoint(cp) is True


def test_no_shell_true_in_os_shim():
    src = _read_source("plugins/os_shim.py")
    assert "shell=True" not in src


def test_wal_on_shared_db():
    from celina_pkg.gear0_storage import _shared_conn
    conn = _shared_conn()
    jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
    bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert str(jm).lower() == "wal"
    assert bt > 0


def test_self_heal_timeout_3s():
    src = _read_source("plugins/self_heal.py")
    assert "timeout=10" not in src
    assert "timeout=3" in src or "3.0" in src


def test_sql_injection_blocked(tmp_path):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
    tmp.write("name,amount\nAlice,100\nBob,200\n")
    tmp.close()
    load_csv_to_memory(tmp.name)
    sql, params = translate_nl_to_sql(
        "sum of amount where name = x'; DROP TABLE data; --", "data:data"
    )
    assert "DROP" not in sql.upper()
    result = execute_sql(sql, "data:data", params)
    assert result is not None
    os.unlink(tmp.name)


def test_authorizer_blocks_attach():
    import sqlite3
    SQLITE_DENY = 1
    assert _authorizer(sqlite3.SQLITE_ATTACH, None, None, None, None) == SQLITE_DENY
    assert _authorizer(sqlite3.SQLITE_DETACH, None, None, None, None) == SQLITE_DENY


def test_destructive_ops_refuse_without_dry_run():
    for op in ["sleep_computer", "shutdown", "restart", "empty_trash", "lock_screen"]:
        result = gear0_os_execute(op, op, dry_run=False)
        assert result is not None
        assert "ActionGate" in result or "approval" in result.lower()


def test_destructive_ops_allowed_with_dry_run():
    for op in ["sleep_computer", "shutdown", "restart", "lock_screen"]:
        result = gear0_os_execute(op, op, dry_run=True)
        assert "ActionGate" not in str(result)


def test_sniffer_denylist_blocks_sensitive_apps():
    assert "keepassxc" in SNIFFER_APP_DENYLIST
    assert "1password" in SNIFFER_APP_DENYLIST
    assert "bitwarden" in SNIFFER_APP_DENYLIST
    snap = ContextSnapshot(
        window_title="Vault", app_name="KeePassXC",
        highlighted_text="secret", timestamp=0,
        platform="linux", source="fallback",
    )
    assert snap.is_allowed() is False
    snap2 = ContextSnapshot(
        window_title="Editor", app_name="firefox",
        highlighted_text="text", timestamp=0,
        platform="linux", source="fallback",
    )
    assert snap2.is_allowed() is True


def test_sniffer_ttl_expiry():
    assert SNIFFER_TTL_SECONDS == 30
    old = ContextSnapshot(
        "t", "app", "h", time.time() - 40, "linux", "fallback"
    )
    assert old.is_expired() is True
    fresh = ContextSnapshot(
        "t", "app", "h", time.time(), "linux", "fallback"
    )
    assert fresh.is_expired() is False


def test_self_heal_no_echo_fallback():
    chain = DEFAULT_CHAINS.get("show_notification")
    assert chain is not None
    assert chain.fallback is None


def test_no_rmrf_in_self_heal():
    src = _read_source("plugins/self_heal.py")
    assert "rm -rf" not in src
    assert "rm -rf" not in src.lower()
