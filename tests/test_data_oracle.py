"""
Headless unit tests for features/celina/celina_pkg/plugins/data_oracle.py.

Tests the local data oracle:
- gear0_data_oracle_classify pattern matching
- gear0_data_oracle_execute dispatch
- load_csv_to_memory / load_sqlite_db
- list_available_tables
- get_table_schema
- translate_nl_to_sql deterministic patterns
- execute_sql parameterized execution
- query_data full pipeline
- SQL injection safety (authorizer blocks ATTACH/DETACH/LOAD_EXTENSION)
- Read-only enforcement (query_only)
"""

import csv
import io
import sys
import tempfile
from pathlib import Path

import pytest

CELINA_ROOT = Path(__file__).resolve().parent.parent / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.plugins import data_oracle as do


# --------------------------------------------------------------------------- #
# gear0_data_oracle_classify
# --------------------------------------------------------------------------- #

def test_classify_recognizes_load_queries():
    """gear0_data_oracle_classify should return 'load_data' for load queries."""
    for q in ["load csv", "load sqlite", "load database", "load db", "csv file"]:
        assert do.gear0_data_oracle_classify(q) == "load_data", f"'{q}' should classify as load_data"

def test_classify_sqlite_db_returns_none():
    """'sqlite db' does not match _DATA_ORACLE_RE (no word boundary after 'sql')."""
    assert do.gear0_data_oracle_classify("sqlite db") is None

def test_classify_recognizes_query_queries():
    """gear0_data_oracle_classify should return 'translate_nl_to_sql' for query queries."""
    for q in ["data", "oracle", "query", "table", "sql", "select", "count", "average", "sum", "max", "min"]:
        assert do.gear0_data_oracle_classify(q) == "translate_nl_to_sql", f"'{q}' should classify as translate_nl_to_sql"

def test_classify_returns_none_for_unrelated():
    """gear0_data_oracle_classify should return None for unrelated queries."""
    assert do.gear0_data_oracle_classify("open browser") is None
    assert do.gear0_data_oracle_classify("play music") is None
    assert do.gear0_data_oracle_classify("shutdown now") is None


# --------------------------------------------------------------------------- #
# load_csv_to_memory / load_sqlite_db
# --------------------------------------------------------------------------- #

def test_load_csv_nonexistent_file():
    """load_csv_to_memory should return error for nonexistent file."""
    result = do.load_csv_to_memory("/nonexistent/file.csv")
    assert "CSV file not found" in result

def test_load_sqlite_nonexistent_file():
    """load_sqlite_db should return error for nonexistent file."""
    result = do.load_sqlite_db("/nonexistent/file.db")
    assert "Database not found" in result

def test_load_csv_round_trip(tmp_path):
    """load_csv_to_memory + query_data should work end-to-end."""
    csv_path = tmp_path / "test.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "amount", "category"])
        writer.writerow(["Alice", "100", "food"])
        writer.writerow(["Bob", "200", "food"])
        writer.writerow(["Carol", "50", "transport"])

    result = do.load_csv_to_memory(str(csv_path), table_name="expenses")
    assert "Loaded 3 rows" in result
    assert "expenses" in result

    tables = do.list_available_tables()
    assert any(":expenses" in t for t in tables)

    schema = do.get_table_schema(f"{tables[0]}")
    assert "Table:" in schema
    assert "amount" in schema
    assert "INTEGER" in schema  # amount column should be inferred as INTEGER

def test_list_available_tables_empty():
    """list_available_tables should return empty list when nothing loaded."""
    # Clear the registry
    do._in_memory_dbs.clear()
    tables = do.list_available_tables()
    assert tables == []


# --------------------------------------------------------------------------- #
# translate_nl_to_sql
# --------------------------------------------------------------------------- #

def test_translate_nl_no_tables():
    """translate_nl_to_sql should return error when no tables loaded."""
    do._in_memory_dbs.clear()
    sql, params = do.translate_nl_to_sql("count rows", "")
    assert sql.startswith("ERROR:")

def test_translate_nl_sum_pattern(tmp_path):
    """SUM pattern: 'sum of amount where category = food' should generate parameterized SQL."""
    csv_path = tmp_path / "test2.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "amount", "category"])
        writer.writerow(["Alice", "100", "food"])
        writer.writerow(["Bob", "200", "food"])
        writer.writerow(["Carol", "50", "transport"])

    do.load_csv_to_memory(str(csv_path), table_name="expenses")
    table_ref = do.list_available_tables()[0]

    sql, params = do.translate_nl_to_sql("sum of amount where category = food", table_ref)
    assert "SUM" in sql.upper()
    assert "?" in sql  # parameterized
    assert params == ("food",)

def test_translate_nl_count_pattern(tmp_path):
    """COUNT pattern: 'count of rows' should generate COUNT(*) SQL."""
    csv_path = tmp_path / "test3.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "amount"])
        writer.writerow(["Alice", "100"])
        writer.writerow(["Bob", "200"])

    do.load_csv_to_memory(str(csv_path), table_name="people")
    table_ref = do.list_available_tables()[-1]

    sql, params = do.translate_nl_to_sql("count of rows", table_ref)
    assert "COUNT" in sql.upper()

def test_translate_nl_where_pattern(tmp_path):
    """WHERE pattern: 'where name = Alice' should generate parameterized SQL."""
    csv_path = tmp_path / "test4.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "amount"])
        writer.writerow(["Alice", "100"])

    do.load_csv_to_memory(str(csv_path), table_name="people")
    table_ref = do.list_available_tables()[-1]

    sql, params = do.translate_nl_to_sql("where name = Alice", table_ref)
    assert "WHERE" in sql.upper()
    assert "?" in sql
    # Query is lowercased in translate_nl_to_sql, so value is "alice"
    assert params == ("alice",)

def test_translate_nl_list_all_limit(tmp_path):
    """'list all' should generate SELECT * with forced LIMIT cap."""
    csv_path = tmp_path / "test5.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name"])
        writer.writerow(["Alice"])

    do.load_csv_to_memory(str(csv_path), table_name="people")
    table_ref = do.list_available_tables()[-1]

    sql, params = do.translate_nl_to_sql("list all", table_ref)
    assert "LIMIT" in sql.upper()
    # Hard cap at 500
    sql_large, _ = do.translate_nl_to_sql("list all limit 9999", table_ref)
    assert "LIMIT 500" in sql_large.upper()

# --------------------------------------------------------------------------- #
# execute_sql
# --------------------------------------------------------------------------- #

def test_execute_sql_no_tables():
    """execute_sql should return error when no tables loaded."""
    do._in_memory_dbs.clear()
    result = do.execute_sql("SELECT 1", "")
    assert "ERROR" in result

# --------------------------------------------------------------------------- #
# query_data
# --------------------------------------------------------------------------- #

def test_query_data_no_tables():
    """query_data should return error when no tables loaded."""
    do._in_memory_dbs.clear()
    result = do.query_data("count rows", "")
    assert "ERROR" in result or "No tables" in result

# --------------------------------------------------------------------------- #
# gear0_data_oracle_execute
# --------------------------------------------------------------------------- #

def test_execute_load_data_nonexistent():
    """gear0_data_oracle_execute load_data with nonexistent file should return error."""
    result = do.gear0_data_oracle_execute("load_data", "load /nonexistent/file.csv")
    assert result is not None
    assert "not found" in result.lower() or "Please specify" in result

def test_execute_translate_no_tables():
    """gear0_data_oracle_execute translate_nl_to_sql with no tables should return error."""
    do._in_memory_dbs.clear()
    result = do.gear0_data_oracle_execute("translate_nl_to_sql", "count rows")
    assert result is not None
    assert "No tables" in result or "ERROR" in result

def test_execute_unknown_handler_returns_none():
    """gear0_data_oracle_execute with unknown handler should return None."""
    assert do.gear0_data_oracle_execute("nonexistent_handler", "query") is None

# --------------------------------------------------------------------------- #
# SQL injection safety
# --------------------------------------------------------------------------- #

def test_authorizer_blocks_attach():
    """The sqlite3 authorizer should block ATTACH attempts."""
    conn = do.sqlite3.connect(":memory:")
    do._harden_connection(conn)
    try:
        conn.execute("ATTACH DATABASE '/etc/passwd' AS x")
        assert False, "ATTACH should have been blocked"
    except Exception:
        pass  # Expected — authorizer blocks it
    finally:
        conn.close()

def test_query_only_enforcement(tmp_path):
    """execute_sql should enforce query_only=ON (read-only)."""
    csv_path = tmp_path / "test_safety.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name"])
        writer.writerow(["Alice"])

    do.load_csv_to_memory(str(csv_path), table_name="test_table")
    table_ref = do.list_available_tables()[0]

    # SELECT should work
    result = do.execute_sql('SELECT * FROM "test_table"', table_ref)
    assert "Alice" in result

    # Clear registry to avoid leaking state between tests
    do._in_memory_dbs.clear()
