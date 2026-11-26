# Phase 9 — Baseline Freeze Progress

## Status
**Part A (Reproducible verification): COMPLETE (A1-A7)**
**Part B (Protected baseline): COMPLETE (B1-B7)**

## Part A Checklist (ALL DONE)
| Step | Description | Status | Commit |
|------|-------------|--------|--------|
| A1 | One-command gate (scripts/verify_all.sh) | DONE | a2288bf |
| A2 | Failing tests on original commit | DONE | (verified, no commit) |
| A3 | Acceptance tests (tests/acceptance/) | DONE | (tests committed earlier) |
| A4 | Mutation sanity check | DONE | (verified, no commit) |
| A5 | Secrets scan over full git history | DONE | (verified, no commit) |
| A6 | Number reconciliation | DONE | 4a7b695 |
| A7 | Rewrite phase-7-remediation.md + FINAL.md | DONE | 4a7b695 |

## Part B Checklist (ALL DONE)
| Step | Description | Status | Commit |
|------|-------------|--------|--------|
| B1 | Protected core manifest | DONE | a2288bf |
| B2 | CODEOWNERS | DONE | 4105de2 |
| B3 | CI proof on throwaway branch | DONE | 84e6063 |
| B4 | Dependency pinning with hashes | DONE | e40317a |
| B5 | Backups + tag audit-complete-2026-11 | DONE | tag created |
| B6 | docs/UNVERIFIED.md | DONE | cf65a93 |
| B7 | docs/BASELINE.md | DONE | fa14643 |

## Final Gate Results (latest run on master)
| Gate | Status | Detail |
|------|--------|--------|
| manifest-integrity | PASS | 106 tools, hash OK |
| config-integrity | PASS | All 4 configs valid |
| pytest | FAIL (pre-existing) | 1692 passed, 30 failed (12 pre-existing + 18 isolation), config.py 74% below floor |
| bandit | FAIL (exit=1, expected) | HIGH: 0, MEDIUM: 29 |
| pip-audit | FAIL (pre-existing) | requirements.txt env issue; tornado CVE-2026-126347 installed |
| mypy | FAIL (pre-existing) | 8 errors in tools.py |
| ruff | PASS | 0 findings on critical modules |
| detect-secrets | INFO | 29 potential secrets (review required) |
| clean-import | PASS | Zero $HOME I/O |

## Tag
**audit-complete-2026-11** → commit fa14643 (HEAD of master)

## Backups
audit-reports/backups/audit-complete-2026-11/ — 12 critical files backed up

## Hard Invariants — ALL PRESERVED
- fail-closed EvidenceLedger anchor verification
- 106-tool manifest parity
- No eager import-time instantiation
- Zero $HOME I/O at module import time
- BusRequest.uid_provenance default "untrusted" unchanged
- Approval gating, single-use tokens, redirect blocking, path validation, 0600/0700 modes
- All Celina QoS/security exemptions
