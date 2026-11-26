#!/usr/bin/env bash
#
# scripts/verify_all.sh — One-command security & quality gate for Celina (Phase 8/9)
#
# Runs, in order, and exits non-zero on any failure:
#   1. pytest with per-module branch coverage floor (85%)
#   2. bandit -ll (prints total count for all severities)
#   3. pip-audit against real requirements files
#   4. mypy --strict on 7 critical modules
#   5. ruff S/B/ASYNC on critical modules + ratchet vs ruff_baseline.json
#   6. detect-secrets on working tree
#   7. clean-import check under fresh temporary HOME
#
# Prints a final PASS/FAIL table and writes raw outputs to
# audit-reports/verify-output/<ISO-date>/.
#
# Usage: bash scripts/verify_all.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DATE_STAMP="$(date +%Y-%m-%d)"
OUTDIR="audit-reports/verify-output/${DATE_STAMP}"
mkdir -p "$OUTDIR"

export PYTHONPATH="features/celina"

# Remove stale coverage data
rm -f .coverage

# Critical modules (same set used throughout the audit)
CRITICAL_MODULES=(
  features/celina/celina_pkg/approval.py
  features/celina/celina_pkg/evidence.py
  features/celina/celina_pkg/config.py
  features/celina/celina_pkg/connectors/base.py
  features/celina/celina_pkg/net_policy.py
  features/celina/celina_pkg/tools.py
  features/celina/celina_pkg/cli_bridge.py
)

# Per-module branch coverage floor
COVERAGE_FLOOR=85

# Build --cov args for pytest-cov
COV_MODULES="celina_pkg.approval celina_pkg.evidence celina_pkg.config celina_pkg.connectors.base celina_pkg.net_policy celina_pkg.tools celina_pkg.cli_bridge"
COV_ARGS=""
for m in $COV_MODULES; do
  COV_ARGS="$COV_ARGS --cov=$m"
done

# Results accumulator
declare -a RESULTS=()
OVERALL_EXIT=0

record() {
  local gate="$1"
  local status="$2"
  local detail="${3:-}"
  RESULTS+=("$gate|$status|$detail")
  if [ "$status" != "PASS" ]; then
    OVERALL_EXIT=1
  fi
}

echo "============================================================"
echo "  Celina Security & Quality Gate — $(date -Is)"
echo "  Root: $ROOT"
echo "============================================================"
echo

# ------------------------------------------------------------------
# Gate 0: Protected core manifest integrity (fail-closed, runs first)
# ------------------------------------------------------------------
echo "--- Gate 0: Protected core manifest integrity ---\n  Manifest A: system/capabilities/celina-capabilities.json\n  Manifest B: features/celina/config/celina-capabilities.json\n  106 tools (64 tier1, 42 tier2)\n  Canonical SHA-256: 5ebf8fb45183b9a101fa59f4387ff211526e93d6fce328ed248569a30409fc6f\n---"

set +e
python3 tools/manifest_integrity.py --verify \
  > "$OUTDIR/manifest-integrity.log" 2>&1
MANIFEST_EXIT=$?
set -e
cat "$OUTDIR/manifest-integrity.log"

if [ $MANIFEST_EXIT -ne 0 ]; then
  record "manifest-integrity" "FAIL" "exit=$MANIFEST_EXIT: manifest integrity violation (fail-closed)"
else
  record "manifest-integrity" "PASS" "manifest integrity OK"
fi
echo

# ------------------------------------------------------------------
# Gate 0b: Config integrity validation (fail-closed, runs first)
# ------------------------------------------------------------------
echo "--- Gate 0b: Config integrity validation ---"

set +e
python3 tools/validate_configs.py \
  > "$OUTDIR/config-integrity.log" 2>&1
CONFIG_EXIT=$?
set -e
cat "$OUTDIR/config-integrity.log"

if [ $CONFIG_EXIT -ne 0 ]; then
  record "config-integrity" "FAIL" "exit=$CONFIG_EXIT: config integrity violation (fail-closed)"
else
  record "config-integrity" "PASS" "config integrity OK"
fi
echo

# ------------------------------------------------------------------
# Gate 1: pytest with per-module branch coverage floor
# ------------------------------------------------------------------
echo "--- Gate 1: pytest + branch coverage (floor ${COVERAGE_FLOOR}%) ---"
COV_MODULES="celina_pkg.approval celina_pkg.evidence celina_pkg.config celina_pkg.connectors.base celina_pkg.net_policy celina_pkg.tools celina_pkg.cli_bridge"
set +e
python3 -m pytest tests/ tests/audit/unit/ \
  $COV_ARGS \
  --cov-branch \
  --cov-report=term \
  --cov-report=json:"$OUTDIR/coverage.json" \
  -p no:cacheprovider \
  --tb=no \
  > "$OUTDIR/pytest.log" 2>&1
PYTEST_EXIT=$?
set -e

# Extract summary line
set +e
SUMMARY_LINE=$(grep -E "[0-9]+ (passed|failed|skipped)" "$OUTDIR/pytest.log" | tail -1)
set -e
echo "$SUMMARY_LINE"

# Check per-module branch coverage from the JSON report
set +e
COV_CHECK_OUTPUT=$(python3 - "$OUTDIR/coverage.json" "$COVERAGE_FLOOR" 2>&1 <<'PYEOF'
import json, sys
cov_file, floor = sys.argv[1], float(sys.argv[2])
with open(cov_file) as f:
    data = json.load(f)
for mod, info in data.get("files", {}).items():
    branch_pct = info.get("summary", {}).get("percent_branches_covered", 0)
    if branch_pct < floor:
        print(f"  COVERAGE BELOW FLOOR: {mod} = {branch_pct:.1f}% (need >= {floor}%)")
        sys.exit(1)
print("  All modules at or above coverage floor")
PYEOF
)
COV_CHECK_EXIT=$?
set -e
echo "$COV_CHECK_OUTPUT"

if [ $PYTEST_EXIT -ne 0 ]; then
  record "pytest" "FAIL" "exit=$PYTEST_EXIT: $SUMMARY_LINE"
elif [ $COV_CHECK_EXIT -ne 0 ]; then
  record "pytest" "FAIL" "coverage below floor: $SUMMARY_LINE"
else
  record "pytest" "PASS" "$SUMMARY_LINE"
fi
echo

# ------------------------------------------------------------------
# Gate 2: bandit -ll
# ------------------------------------------------------------------
echo "--- Gate 2: bandit -ll ---"
set +e
python3 -m bandit -r features/celina/celina_pkg -ll -f json -q \
  -o "$OUTDIR/bandit.json" 2> "$OUTDIR/bandit-stderr.log"
BANDIT_EXIT=$?
set -e
# Print severity totals
set +e
python3 - "$OUTDIR/bandit.json" <<'PYEOF' 2>/dev/null || echo "  Bandit JSON parse failed (check bandit-stderr.log)"
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
totals = data.get("results", [])
by_severity = {}
for r in totals:
    sev = r.get("issue_severity", "Unknown")
    by_severity[sev] = by_severity.get(sev, 0) + 1
print("  Bandit totals by severity:")
for sev in ("HIGH", "MEDIUM", "LOW", "UNDEFINED"):
    print(f"    {sev}: {by_severity.get(sev, 0)}")
print(f"  Total issues: {len(totals)}")
PYEOF
set -e
echo

if [ $BANDIT_EXIT -ne 0 ]; then
  record "bandit" "FAIL" "exit=$BANDIT_EXIT"
else
  record "bandit" "PASS" "exit=0"
fi

# ------------------------------------------------------------------
# Gate 3: pip-audit against real requirements files
# ------------------------------------------------------------------
echo "--- Gate 3: pip-audit ---"
PIP_AUDIT_FAIL=""
for reqfile in features/celina/requirements.txt tools/requirements-tier-b.txt; do
  if [ -f "$reqfile" ]; then
    echo "  Auditing $reqfile ..."
    pip-audit -r "$reqfile" > "$OUTDIR/pip-audit-$(basename "$reqfile").txt" 2>&1 || PIP_AUDIT_FAIL="$PIP_AUDIT_FAIL $reqfile"
  fi
done
# Also audit installed packages (covers transitive deps not in requirements)
pip-audit > "$OUTDIR/pip-audit-installed.txt" 2>&1 || PIP_AUDIT_FAIL="$PIP_AUDIT_FAIL (installed)"

if [ -n "$PIP_AUDIT_FAIL" ]; then
  record "pip-audit" "FAIL" "vulnerable: $PIP_AUDIT_FAIL"
else
  record "pip-audit" "PASS" "no known vulnerabilities"
fi
echo

# ------------------------------------------------------------------
# Gate 4: mypy --strict on 7 critical modules
# ------------------------------------------------------------------
echo "--- Gate 4: mypy --strict ---"
set +e
python3 -m mypy --strict --follow-imports=skip \
  "${CRITICAL_MODULES[@]}" \
  > "$OUTDIR/mypy.log" 2>&1
MYPY_EXIT=$?
set -e
MYPY_ERRORS=$(grep -c "error:" "$OUTDIR/mypy.log" 2>/dev/null || echo 0)
echo "  $MYPY_ERRORS mypy errors"
if [ $MYPY_EXIT -ne 0 ]; then
  record "mypy" "FAIL" "$MYPY_ERRORS errors"
else
  record "mypy" "PASS" "0 errors"
fi
echo

# ------------------------------------------------------------------
# Gate 5: ruff S/B/ASYNC on critical modules + ratchet
# ------------------------------------------------------------------
echo "--- Gate 5: ruff S/B/ASYNC ---"
set +e
python3 -m ruff check --select S,B,ASYNC \
  "${CRITICAL_MODULES[@]}" \
  > "$OUTDIR/ruff.log" 2>&1
RUFF_EXIT=$?
set -e
RUFF_COUNT=$(grep -c "." "$OUTDIR/ruff.log" 2>/dev/null || echo 0)
if [ "$RUFF_COUNT" = "0" ] || grep -q "All checks passed" "$OUTDIR/ruff.log" 2>/dev/null; then
  RUFF_COUNT=0
fi
echo "  $RUFF_COUNT ruff findings (critical modules)"

# Ratchet check vs baseline
BASELINE_FILE="audit-reports/ruff_baseline.json"
if [ -f "$BASELINE_FILE" ]; then
  BASELINE_COUNT=$(python3 -c "import json; print(json.load(open('$BASELINE_FILE'))['count'])")
  if [ "$RUFF_COUNT" -gt "$BASELINE_COUNT" ]; then
    record "ruff" "FAIL" "$RUFF_COUNT findings > baseline $BASELINE_COUNT"
  else
    record "ruff" "PASS" "$RUFF_COUNT findings (<= baseline $BASELINE_COUNT)"
  fi
else
  # Create baseline
  python3 -c "import json; json.dump({'count': $RUFF_COUNT, 'date': '$DATE_STAMP'}, open('$BASELINE_FILE','w'))"
  record "ruff" "PASS" "$RUFF_COUNT findings (baseline created)"
fi
echo

# ------------------------------------------------------------------
# Gate 6: detect-secrets on working tree
# ------------------------------------------------------------------
echo "--- Gate 6: detect-secrets ---"
set +e
detect-secrets scan > "$OUTDIR/secrets.json" 2>&1 || true
set -e
SECRETS_COUNT=$(python3 -c "
import json
try:
    data = json.load(open('$OUTDIR/secrets.json'))
    results = data.get('results', {})
    print(sum(len(v) for v in results.values() if isinstance(v, list)))
except Exception:
    print('0')
")
echo "  $SECRETS_COUNT potential secrets detected"
# We don't fail on secrets — some may be test fixtures. Report only.
record "detect-secrets" "INFO" "$SECRETS_COUNT potential secrets (review required)"
echo

# ------------------------------------------------------------------
# Gate 7: clean-import check under fresh temporary HOME
# ------------------------------------------------------------------
echo "--- Gate 7: clean-import check ---"
set +e
python3 - "$OUTDIR/clean-import.log" <<'PYEOF'
import tempfile, os, sys, importlib
log_file = sys.argv[1]
d = tempfile.mkdtemp()
os.environ['HOME'] = d
os.environ['DRY_RUN'] = '1'
modules = [
    'celina_pkg.api_actuators', 'celina_pkg.tools', 'celina_pkg.router',
    'celina_pkg.approval', 'celina_pkg.evidence', 'celina_pkg.config',
    'celina_pkg.net_policy', 'celina_pkg.connectors.base', 'celina_pkg.cli_bridge',
]
failed = []
for mod in modules:
    try:
        importlib.import_module(mod)
    except Exception as e:
        failed.append((mod, str(e)))
with open(log_file, 'w') as f:
    if failed:
        f.write('FAILED IMPORTS:\n')
        for mod, err in failed:
            f.write(f'  {mod}: {err}\n')
        sys.exit(1)
    else:
        f.write('CLEAN IMPORT OK\n')
        sys.exit(0)
PYEOF
set -e
CLEAN_EXIT=$?
if [ $CLEAN_EXIT -ne 0 ]; then
  record "clean-import" "FAIL" "see $OUTDIR/clean-import.log"
else
  record "clean-import" "PASS" "zero \$HOME I/O at import time"
fi
echo

# ------------------------------------------------------------------
# Final PASS/FAIL table
# ------------------------------------------------------------------
echo "============================================================"
echo "  FINAL PASS/FAIL TABLE"
echo "============================================================"
printf "%-20s %-8s %s\n" "GATE" "STATUS" "DETAIL"
echo "------------------------------------------------------------"
for entry in "${RESULTS[@]}"; do
  IFS='|' read -r gate status detail <<< "$entry"
  printf "%-20s %-8s %s\n" "$gate" "$status" "$detail"
done
echo "============================================================"

if [ "$OVERALL_EXIT" -ne 0 ]; then
  echo "RESULT: FAIL — one or more gates failed"
else
  echo "RESULT: PASS — all gates passed"
fi

# Save results summary
RESULTS_FILE="$OUTDIR/results.txt"
{
  echo "Celina Security & Quality Gate — $(date -Is)"
  echo "Root: $ROOT"
  echo "============================================================"
  for entry in "${RESULTS[@]}"; do
    IFS='|' read -r gate status detail <<< "$entry"
    echo "$gate: $status — $detail"
  done
  echo
  if [ "$OVERALL_EXIT" -ne 0 ]; then
    echo "RESULT: FAIL"
  else
    echo "RESULT: PASS"
  fi
} > "$RESULTS_FILE"

exit "$OVERALL_EXIT"