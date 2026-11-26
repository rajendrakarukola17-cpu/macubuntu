# Celina Acceptance Tests (Phase 9 A3)

These tests verify security properties end-to-end. They are designed to be
run by hand after any change to the protected core modules.

## How to run

```bash
# From the repo root
# Run from your checkout root
export PYTHONPATH=features/celina

# Run all acceptance tests
python3 -m pytest tests/acceptance/ -v --tb=short

# Run a specific category
python3 -m pytest tests/acceptance/test_prompt_injection_e2e.py -v
python3 -m pytest tests/acceptance/test_approval_binding.py -v
python3 -m pytest tests/acceptance/test_strict_sandbox.py -v
python3 -m pytest tests/acceptance/test_evidence_ledger_fail_closed.py -v
python3 -m pytest tests/acceptance/test_credential_modes.py -v
```

## Test files

| File | What it verifies |
|------|-----------------|
| `test_prompt_injection_e2e.py` | Hostile instructions in email/web/file/tool output cannot cause mutating tool calls without bound approval. Denied approval → no side effect. Approval shows real tool name + args. Argument hash binds to tool+args; cannot be forged. Single-use tokens. Fail-closed without approval callback. |
| `test_approval_binding.py` | Approval argument hash binding, single-use tokens, tool/args mismatch detection. |
| `test_strict_sandbox.py` | bwrap/landlock unavailable → probe returns False (fail closed for sandbox). Approval + path validation + SSRF remain enforced without sandbox. |
| `test_evidence_ledger_fail_closed.py` | Ledger deletion, truncation, and tampering each fail closed. Reset requires confirm=True. Corrupt anchor fails closed. |
| `test_credential_modes.py` | Credential files 0600, directories 0700. Failed atomic write leaves no partial file. |

## Injection corpus

The prompt injection tests use `tests/audit/data/injection_corpus.json` which contains 56 cases across 6 categories plus 5 safe controls. Each hostile case must be blocked; each safe control must not be falsely flagged.

## What a failure means

Any acceptance test failure is a **P0 finding**. Do not hide it — report it immediately.

- **Prompt injection not blocked** → hostile instructions can trigger tool execution → critical security failure
- **Approval not required** → mutating tools can run without user consent → critical security failure
- **Sandbox not fail-closed** → bwrap/landlock bypass → defense-in-depth gap
- **Evidence ledger tampering not detected** → audit trail can be silently modified → integrity failure
- **Credential files not 0600** → secrets readable by other users → confidentiality failure
