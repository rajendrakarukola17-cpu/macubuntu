# Phase 7b — Remediation Progress Tracker

## Current State
- Phase 7 claimed all fixes done but many claims unverified
- Significant progress made on TASKS 1-10, partial on TASK 7 (coverage)

## Task Status

| Task | Status | Notes |
|------|--------|-------|
| TASK 1 - DNS rebinding / SSRF | COMPLETE | Fixed web_scraper.py to use safe_get. Added connect-time rebinding, multi-A-record, decimal/octal/hex IP, userinfo, trailing dot, non-https tests. Commits: e39a2ec, c99d7f4 |
| TASK 2 - Filesystem TOCTOU | COMPLETE | Hardened _open_tmp_nosymlink with dir-fd + O_CREAT|O_EXCL. All 7 tests pass. Commit: 7d1e4b5 |
| TASK 3 - Dependencies/supply chain | COMPLETE | Pinned audit tools with SHA-256 hashes. Upgraded tornado/filelock. pip-audit clean. Dependabot config. CI fails on violations. Commits: 74bf3e5, 5cf1a1f |
| TASK 4 - Bandit triage | COMPLETE | 0 HIGH. B314 fixed with defusedxml. B108 noqa'd with reasons (all 21 findings resolved). Commit: d98b4f0 + B108 suppressions this session |
| TASK 5 - mypy types | COMPLETE | 0 mypy --strict errors in all 7 critical modules. Commit: d20c31e |
| TASK 6 - ruff lint | COMPLETE | Auto-fixed safe rules. All S/B/ASYNC resolved in critical modules. pyproject.toml added. Commit: 1f13d2e |
| TASK 7 - Coverage | PARTIAL | PASS: evidence 91%, cli_bridge 99%, approval 98%, net_policy 85%, connectors/base 87%, config 85%. NEEDS WORK: tools 56% (+29% to reach 85%). Commits: multiple this session |
| TASK 8 - Connectors | COMPLETE | test_connector_integration_stubs.py (28 tests) + test_connector_base_security.py (27 tests) + test_connector_base_coverage.py (25 tests). |
| TASK 9 - Concurrency | COMPLETE | test_approval_concurrency.py (3 tests) + test_evidence_concurrency.py (7 tests). |
| TASK 10 - UNVERIFIED areas | COMPLETE | test_island_ui_guarded.py (7 tests). macOS/hardware/network marked UNVERIFIED. |
| TASK 11 - Phase 1 leftovers | ADDRESSED | 20 import failures are pre-existing/expected (missing `gi`, `fastmcp`, fail-closed evidence ledger anchor verification). Zero regressions in test suite. |
| TASK 12 - Prompt injection | COMPLETE | Corpus tests added: _sanitize_identity_token strips control/shell/quote chars, truncates to 32 chars; format_persona_instructions with sanitized names has no injection. |
| TASK 13 - Sandbox | PARTIAL | test_landlock_concurrency.py (3 tests) covers SUSPECTED-1. bwrap/landlock B108 suppressions documented with inline reasons. |
| TASK 14 - Secrets/CI | PARTIAL | detect-secrets baseline exists. CI security checks fail on violations. |
| TASK 15 - Explain numbers | COMPLETE | Coverage numbers reconciled (baseline was audit-only subset, not full suite). |
| TASK 16 - Correct report | COMPLETE | phase-7-remediation.md rewritten with correct coverage numbers. Commit: e347ce6 |

## Coverage Before/After (critical modules — branch coverage)

| Module | Before | After | Target | Status |
|--------|--------|-------|--------|--------|
| connectors/base.py | 49.2% | 87% | 85% | PASS ✓ |
| approval.py | 57.8% | 98% | 85% | PASS ✓ |
| evidence.py | 19.9% | 91% | 85% | PASS ✓ |
| config.py | 29.1% | 85% | 85% | PASS ✓ |
| tools.py | 11.2% | 56% | 85% | NEEDS WORK (+29%) |
| net_policy.py | 58.8% | 85% | 85% | PASS ✓ |
| cli_bridge.py | 84.8% | 99% | 85% | PASS ✓ |

## Verification Gate

| Gate | Status |
|------|--------|
| Full pytest | ✅ 1611 passed, 11 failed (all pre-existing), 5 skipped, 0 regressions |
| bandit -r -ll (HIGH) | ✅ 0 HIGH |
| pip-audit (core + tier-b) | ✅ Clean |
| mypy --strict (critical modules) | ✅ 0 errors (down from 625) |
| ruff S/B/ASYNC (critical modules) | ✅ 0 findings (was 31) |
| Secret scan | ⬜ Pending |
| Clean-import check | ✅ Clean import OK |

## Pre-existing test failures (NOT regressions)

| Test File | Count | Reason |
|-----------|-------|--------|
| test_phase3b_remediation.py | 3 | pytest-asyncio plugin / router functions |
| test_npu_worker_and_os_desktop_mcp.py | 6 | Hardware-dependent (NPU not available) |
| test_installer_dryrun_rollback.py | 1 | Broken `$REPO_DIR` reference |
| test_stage99_and_gear0_ffi.py | 1 | FFI-related assertion |

## New test files created this session

- tests/audit/unit/test_config_coverage.py — +102 tests total (config coverage + prompt injection corpus)
- tests/audit/unit/test_tools_coverage.py — +121 tests total (tools coverage)
- tests/audit/unit/test_tools_file_ops_coverage.py — 25 tests (file ops coverage)
- tests/audit/unit/test_evidence_coverage.py — 45 tests (evidence coverage)
- tests/audit/unit/test_approval_coverage.py — 15 tests (approval coverage)
- tests/audit/unit/test_net_policy_coverage.py — 21 tests (net_policy coverage)
- tests/audit/unit/test_connector_base_coverage.py — 25 tests (connector base coverage)
- tests/test_cli_bridge.py — +6 tests (cli_bridge coverage)

## Commits (this session)

- e39a2ec — Task 1 SSRF fix
- 7d1e4b5 — Task 2 TOCTOU fix
- 74bf3e5 + 5cf1a1f — Task 3 supply chain + CI hardening
- d98b4f0 — Task 4 bandit
- d20c31e — Task 5 mypy
- 1f13d2e — Task 6 ruff
- ccfd77a, bbb29b7 — earlier session commits
- 025b545 — evidence coverage tests
- c82d9f6 — cli_bridge coverage tests
- d88f246 — approval coverage tests
- 70bfb14 — net_policy coverage tests
- f260af9 — connector base coverage tests
- eaeaa72 — config coverage tests (+14 tests)
- 18dce73 — tools coverage tests (15 tests)
- 50188ae — multi-file commit (coverage + B108 + router fix)
- 73b2120 — phase-7-remediation.md update
- f8bafaf — phase-7b-progress.md update
- b65b255 — tools coverage tests (+13 tests)
- e207be2 — phase-7-remediation.md update
- 74244ca — fix(config): add coverage tests for chmod/JSON/env branches
- 0037ad9 — fix(tools): add coverage tests for run_capture/launch_detached/send_notification/app_nap/connectivity/open_app
- 90e40fe — fix(tools): add coverage tests for read_file/list_files/read_function_signatures/read_lines/grep_code/clipboard/trigger/domain/flathub/apt
- e347ce6 — docs(remediation): rewrite phase-7-remediation.md with current coverage numbers
- b7460e5 — fix(config): add prompt injection corpus tests

## Outstanding Work

| Item | Description | Priority |
|------|-------------|----------|
| `tools.py` branch coverage | 56% → 85% (~120 more branches across ~30 functions) | High |
| Phase 7c: mutation check | No mutation testing infrastructure available (mutmut not installed) | Medium |
| Tasks 13-14: Sandbox review / Secrets CI | bwrap/landlock review ongoing; detect-secrets baseline exists | Medium |
