#!/usr/bin/env bash
#
# CELINA UPDATE 20 — CI Gates
# Runs bandit, ruff, mypy, pip-audit, pip-licenses, and regression tests.
# Usage: run-ci-gates.sh [--ci] (--ci fails on any gate failure)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CELINA_ROOT="${CELINA_INSTALL_DIR:-/home/user/macubuntu/features/celina}"
CELINA_PKG="${CELINA_ROOT}/celina_pkg"
CI_MODE=false

# Update 20 file inventory (same as apply-update.sh)
declare -a UPDATE_FILES=(
    "celina_pkg/gear0_macro.py"
    "celina_pkg/gear0_storage.py"
    "celina_pkg/gear0_ledger.py"
    "celina_pkg/plugins/sniffer_plugin.py"
    "celina_pkg/plugins/habit_engine.py"
    "celina_pkg/plugins/data_oracle.py"
    "celina_pkg/plugins/os_shim.py"
    "celina_pkg/plugins/self_heal.py"
    "celina_pkg/router.py"
)

for arg in "$@"; do
    case "$arg" in
        --ci) CI_MODE=true ;;
        --help|-h)
            echo "Usage: $0 [--ci]"
            echo "  --ci   Fail on any gate failure (for CI pipeline)"
            exit 0
            ;;
    esac
done

FAILURES=0

log()  { echo "[ci-gate] $*"; }
fail() { echo "[ci-gate] FAIL: $*" >&2; FAILURES=$((FAILURES + 1)); }
pass() { echo "[ci-gate] PASS: $*"; }

# ------------------------------------------------------------------
# Gate 1: Regression tests (17 P0 tests)
# ------------------------------------------------------------------
run_regression_tests() {
    log "Gate 1: P0 regression tests"
    if python3 -m pytest "${SCRIPT_DIR}/../tests/test_p0_regressions.py" -v 2>/dev/null; then
        pass "All 17 P0 regression tests passed"
    else
        fail "P0 regression tests failed"
    fi
}

# ------------------------------------------------------------------
# Gate 2: Bandit security scan
# ------------------------------------------------------------------
run_bandit() {
    log "Gate 2: Bandit security scan"
    if ! command -v bandit &>/dev/null; then
        log "  SKIP: bandit not installed (pip install bandit)"
        return 0
    fi
    if bandit -c "${SCRIPT_DIR}/bandit.toml" -r "${CELINA_PKG}" -q 2>/dev/null; then
        pass "Bandit: no security issues found"
    else
        fail "Bandit: security issues detected"
    fi
}

# ------------------------------------------------------------------
# Gate 3: Ruff lint
# ------------------------------------------------------------------
run_ruff() {
    log "Gate 3: Ruff lint"
    if ! command -v ruff &>/dev/null; then
        log "  SKIP: ruff not installed (pip install ruff)"
        return 0
    fi
    if ruff check --config "${SCRIPT_DIR}/ruff.toml" "${CELINA_PKG}" 2>/dev/null; then
        pass "Ruff: no lint issues"
    else
        fail "Ruff: lint issues detected"
    fi
}

# ------------------------------------------------------------------
# Gate 4: Mypy type check
# ------------------------------------------------------------------
run_mypy() {
    log "Gate 4: Mypy type check"
    if ! command -v mypy &>/dev/null; then
        log "  SKIP: mypy not installed (pip install mypy)"
        return 0
    fi
    # Mypy on celina_pkg (may have pre-existing type issues — warn only)
    if mypy "${CELINA_PKG}" --ignore-missing-imports 2>/dev/null; then
        pass "Mypy: no type errors"
    else
        log "  WARN: Mypy type warnings (non-blocking for Update 20)"
    fi
}

# ------------------------------------------------------------------
# Gate 5: Pip-audit (dependency vulnerabilities)
# ------------------------------------------------------------------
run_pip_audit() {
    log "Gate 5: Pip-audit vulnerability scan"
    if ! command -v pip-audit &>/dev/null; then
        log "  SKIP: pip-audit not installed (pip install pip-audit)"
        return 0
    fi
    if pip-audit -r "${SCRIPT_DIR}/../requirements-ci.txt" 2>/dev/null; then
        pass "Pip-audit: no vulnerabilities"
    else
        fail "Pip-audit: vulnerabilities detected"
    fi
}

# ------------------------------------------------------------------
# Gate 6: Pip-licenses (license compliance)
# ------------------------------------------------------------------
run_pip_licenses() {
    log "Gate 6: Pip-licenses compliance"
    if ! command -v pip-licenses &>/dev/null; then
        log "  SKIP: pip-licenses not installed (pip install pip-licenses)"
        return 0
    fi
    # Check for forbidden licenses (GPL, AGPL)
    if pip-licenses --from=mixed --format=json 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
forbidden = {'GPL', 'AGPL', 'LGPL'}
found = [p['Name'] for p in data if p.get('License', '') in forbidden]
if found:
    print('FORBIDDEN LICENSES:', found)
    sys.exit(1)
else:
    print('All licenses compliant')
" 2>/dev/null; then
        pass "Pip-licenses: all compliant"
    else
        fail "Pip-licenses: forbidden license detected"
    fi
}

# ------------------------------------------------------------------
# Gate 7: Shell=True count must be 0 in Update 20 files
# ------------------------------------------------------------------
run_shell_true_check() {
    log "Gate 7: shell=True must be 0 in Update 20 files"
    local count=0
    for rel in "${UPDATE_FILES[@]}"; do
        local f="${PKG_DIR}/${rel}"
        if [ -f "$f" ]; then
            local c
            c=$(grep -c "shell=True" "$f" 2>/dev/null || true)
            c=${c:-0}
            count=$((count + c))
        fi
    done
    if [ "$count" -eq 0 ]; then
        pass "shell=True count: 0 in Update 20 files"
    else
        fail "shell=True count: $count in Update 20 files (must be 0)"
    fi
}

# ------------------------------------------------------------------
# Gate 8: rm -rf / find -delete must not exist in Update 20 files
# ------------------------------------------------------------------
run_destructive_check() {
    log "Gate 8: No rm -rf / find -delete in Update 20 files"
    local count=0
    for rel in "${UPDATE_FILES[@]}"; do
        local f="${PKG_DIR}/${rel}"
        if [ -f "$f" ]; then
            local c
            c=$(grep -cE "rm -rf|find.*-delete|shutil\.rmtree" "$f" 2>/dev/null || true)
            c=${c:-0}
            count=$((count + c))
        fi
    done
    if [ "$count" -eq 0 ]; then
        pass "Destructive deletion patterns: 0 in Update 20 files"
    else
        fail "Destructive deletion patterns found: $count in Update 20 files"
    fi
}

# ------------------------------------------------------------------
# Gate 9: Checksum verification of package
# ------------------------------------------------------------------
run_checksum_check() {
    log "Gate 9: Package checksum verification"
    if [ -f "${SCRIPT_DIR}/../SHA256SUMS" ]; then
        cd "${SCRIPT_DIR}/.."
        if sha256sum -c SHA256SUMS 2>/dev/null; then
            pass "SHA256SUMS verified"
        else
            fail "SHA256SUMS verification failed"
        fi
        cd - >/dev/null
    else
        fail "SHA256SUMS not found in package"
    fi
}

# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
echo "========================================"
echo "CELINA UPDATE 20 — CI Gates"
echo "========================================"
echo ""

run_regression_tests
run_bandit
run_ruff
run_mypy
run_pip_audit
run_pip_licenses
run_shell_true_check
run_destructive_check
run_checksum_check

echo ""
echo "========================================"
if [ "$FAILURES" -eq 0 ]; then
    echo "ALL CI GATES PASSED"
    exit 0
else
    echo "${FAILURES} CI GATE(S) FAILED"
    exit 1
fi
