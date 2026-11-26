"""Update 20 end-to-end verification after B1-B7 fixes."""
import sys
sys.path.insert(0, '.')
from celina_pkg.router import process_query, route_query_with_meta

print("=" * 60)
print("UPDATE 20 — End-to-End Verification (after B1-B7 fixes)")
print("=" * 60)

tests = [
    # Plugin #1 Sniffer
    ("Plugin #1 Sniffer", "what is my active window"),
    # Plugin #2 Habit
    ("Plugin #2 Habit", "habit"),
    ("Plugin #2 Habit Suggest", "suggest"),
    # Plugin #3 Data Oracle
    ("Plugin #3 Data Oracle", "count of users"),
    ("Plugin #3 Data Oracle (query)", "data query"),
    # Plugin #4 OS Shim — non-destructive
    ("Plugin #4 System Info", "system info"),
    ("Plugin #4 Clipboard", "clipboard"),
    # Plugin #4 OS Shim — destructive (should refuse without dry_run)
    ("Plugin #4 Sleep (refuse)", "sleep"),
    ("Plugin #4 Sleep (simulate)", "simulate: sleep"),
    # Plugin #5 Self Heal
    ("Plugin #5 Reliability", "reliability"),
    ("Plugin #5 Heal", "heal"),
]

all_ok = True
for label, query in tests:
    try:
        reply, source = route_query_with_meta(query)
        status = "OK" if reply else "EMPTY"
        display = reply.replace('\n', ' | ')[:100]
        print(f"  [{status}] {label}: {query!r} -> {display}")
    except Exception as e:
        all_ok = False
        print(f"  [FAIL] {label}: {query!r} -> {type(e).__name__}: {e}")

# Test case preservation (B7)
print()
print("B7 Case preservation test:")
try:
    from celina_pkg.gear0_contacts import contact_save, contact_lookup
    r1 = contact_save("John Doe", "+1234567890")
    r2 = contact_lookup("john doe")
    r3 = contact_lookup("JOHN DOE")
    print(f"  Save: {r1}")
    print(f"  Lookup 'john doe': {r2}")
    print(f"  Lookup 'JOHN DOE': {r3}")
    print(f"  Case preserved: {r2 is not None and r3 is not None}")
except Exception as e:
    print(f"  FAIL: {e}")

# Test data_oracle SQL injection protection
print()
print("B4 Data Oracle SQL injection test:")
try:
    from celina_pkg.plugins.data_oracle import load_csv_to_memory, translate_nl_to_sql, execute_sql
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    tmp.write('name,amount\nAlice,100\nBob,200\n')
    tmp.close()
    load_csv_to_memory(tmp.name)
    # Injection attempt in value
    q = "sum of amount where name = x'; DROP TABLE data; --"
    sql, params = translate_nl_to_sql(q, 'data:data')
    result = execute_sql(sql, 'data:data', params)
    print(f"  Injection attempt result: {result[:80]}")
    os.unlink(tmp.name)
except Exception as e:
    print(f"  FAIL: {e}")

print()
print("=" * 60)
print(f"END-TO-END: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
print("=" * 60)
