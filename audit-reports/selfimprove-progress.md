# Self-improvement progress

## Gate 2: FROZEN — Ready for L0 & L1

Baseline artifacts: `audit-reports/selfimprove-context-bundle.md`, `selfimprove/protected_hashes.json`, `CODEOWNERS`, `tests/selfimprove/test_protected_core.py`, `tests/selfimprove/test_layering.py`, and `docs/selfimprove/`.

This status is contingent on the recorded full pytest, configuration and manifest verification and the annotated freeze tag. No L0 or L1 implementation is created by this gate.

### Guardrail scope and limitations

- The hash test pins exactly six protected files and rejects missing files, symlinks, altered bytes, and changed manifest membership. Its tamper test changes one byte.
- The AST guard checks explicit static selfimprove imports, cycles, size limits and common dynamic-import/execution calls. It is a development guard, not a Python security sandbox; reflection or other dynamic mechanisms are not proven impossible.
- To avoid guessing future module names, the classifier recognizes `contracts.py` and `flags.py` as L0, `budget.py` as L1, `ledger_bridge.py` as bridge, and modules or packages named `l0` through `l7` as their named layer. Other names fail closed until explicitly classified. The bridge may import L0 only; package `__init__.py` may not import selfimprove layers. This is a conservative convention pending approval of additional module names.
- `CODEOWNERS` extends the existing `@security-core` owner. **UNVERIFIED:** whether that GitHub account/team exists, has access, and is enforced by branch protection; repository configuration alone does not enforce reviews. Protecting CODEOWNERS and tests in CODEOWNERS does not replace branch protection.
- The isolation proof checks loaded module origins at pytest session end; it does not observe modules imported and removed earlier during a run.
- The installed third-party package licenses for the six requested distributions are UNVERIFIED when not installed; no dependency incorporation is authorized.

## Gate 3 Wave 1 — L0 and L1 COMPLETE

- L0 contracts and flags: commit `ee6fec3` (`feat(selfimprove): add L0 contracts and default-off kill-switch flags`).
- L1 budget guard: commit `d2a1ad2` (`feat(selfimprove): add L1 BudgetGuard with deterministic caps and injectable clock`).
- L1 wall-clock edge case: commit `44cac0b`; three regression cases demonstrated and fixed expiry precedence for invalid consumption.
- Guardrail-test lint-only cleanup: commit `2ac5ef3`; checks and negative tests retained. Frozen Protected Core files and the hash manifest were not edited.
- Test-first: the L0 test initially failed on `ModuleNotFoundError: celina_pkg.selfimprove`; the L1 test initially failed on `ModuleNotFoundError: celina_pkg.selfimprove.budget`. Each subsequently passed.
- Self-improvement suite: `79 passed` including the protected-core and layering guardrails. Full suite: `2554 passed, 5 skipped`, zero failures. The five skips require bwrap, which was not installed.
- Branch coverage: `contracts.py` **16/16 = 100%**, `flags.py` **12/12 = 100%**, `budget.py` **14/14 = 100%**; total **42/42 = 100%**. These are coverage.py's measured branches, not proof of all behavior.
- Ruff, mypy `--strict`, Bandit `-ll`, config validation, and manifest integrity verification exited 0. Ruff emitted two pre-existing incompatible-rule warnings; manifest verification noted pre-existing manifest/version drift.
- Size audit using the layering guard's counting method: longest package file `contracts.py` **218 lines**; longest function `BudgetGuard.snapshot` **20 lines**. `budget.py` is 124 lines and `flags.py` is 61 lines.
- `BudgetGuard` starts its clock at the first check or consumption, not at construction or an unstarted snapshot. An exhausted snapshot means at least one limit is reached, including a zero write/subprocess cap; that informational flag does not itself deny other dimensions.
- Exact captured command output: `audit-reports/selfimprove-wave1-verification.md`. L2 and later remain pending explicit approval.

## Authorized post-freeze EvidenceLedger anchor correction

The user authorized a narrowly scoped missing-anchor correction after the frozen `pre-selfimprove-freeze` tag. Frozen-tag evidence.py SHA-256 remains `b1fe25dd173f0789db358e012a19785739554bf1fde8ba4ec922a1a7a8baefab`; the updated protected manifest pins `11e9747977349e31cbad219eadad10a558de147c05b273eaa4c297d847900a66`. The tag is unchanged and continues to identify the historical baseline. `verify_chain()` retains its tuple contract; `verify()` raises on failures; open and append reject missing anchors for non-empty ledgers. Confirmed reset does not implicitly re-trust non-empty unanchored bytes.


## Gate 3 Wave 2A — bridge and L2 COMPLETE; stop before L3

### Commits and scope

- Acyclic L2 sibling-import guard and cycle negative test: `8f0a2d40f828e7ede0f5675fa7fa0ee8a1733a8a`.
- Authorized fail-closed EvidenceLedger anchor fix: `b0212fb1180591912915b80881d621040f4c355d`.
- Ledger bridge: `0c4359b8f83bc7184ca9351182409b82dc1f4eea`.
- L2 verifier suite and gate: `ea44efc5f6792c41b0dfb5348df873f1574422cc`.
- L3 and any sandbox/L7 implementation are **not started**; separate user approval is required.

### Trust boundary and known limits

The host must configure trustworthy `VerifierProtocol` objects and executors; the gate executes these configured objects and does not accept agent-provided result values as a substitute. An empty suite is blocked. This trust boundary prevents agent self-reporting only when the host retains control of configuration; a dishonest host-configured verifier or injected executor can report a pass. This is **not in-process attestation or an OS sandbox**. OS/worktree isolation belongs at the sandbox/L7 boundary, which is neither implemented nor verified here. Allowlisting and cwd validation are not a substitute for OS isolation. Output returned by the runner is truncated to 4096 characters per stream; the default subprocess uses `capture_output`, so this does **not** bound peak subprocess-output memory usage.

L2 feature flags block execution; the built-in verifiers account for subprocess and wall-clock budgets; timeouts, exceptions, missing executables, nonzero exits and invalid Bandit results fail closed. Bandit medium/high results and any nonzero exit block approval. The gate stops on budget exhaustion regardless of fail-fast mode. See `tests/selfimprove/test_l2_verifiers.py` for fake-executor and real temporary-directory subprocess tests, and `tests/selfimprove/test_ledger_bridge.py` for clean import, structured events, sanitization and tamper cases. `tests/selfimprove/test_layering.py` and `tests/selfimprove/test_protected_core.py` are development guardrails, not security sandboxes.

### Results and limits

- Evidence prerequisite test-first: six initially failing cases, followed by 22 focused protected/evidence cases passing; a deliberate missing-anchor-guard mutation was caught. `verify_chain()` retains its tuple contract, `verify()` raises, and missing anchors fail closed on open/append. The earlier pre/post evidence strict-mypy comparison found the same one pre-existing `RedactionVault` no-untyped-call error, and Ruff the same 83 baseline findings. Those earlier ephemeral logs are unavailable in this resumed environment; no claim is made that whole-file evidence lint/type gates are clean. Selfimprove strict mypy has zero errors.
- Final path-form branch coverage: 131 selfimprove tests passed. Actual measured branches: bridge **16/16**, L2 gate **10/10**, L2 runner **12/12**, L2 verifiers **12/12**, each **100%**; entire selfimprove package **92/92**, 100%. Coverage is not proof of security or all behavior.
- Final full suite: 2611 passed, 5 skipped because bwrap is unavailable; sandbox execution remains unverified. Protected-core plus layering guard tests: 26 passed. Ruff and strict mypy passed. Bandit `-ll` passed with zero medium/high, two low findings, and zero `#nosec` lines. Configuration and manifest validators passed; manifest/version drift remains a review warning.
- Size audit, counting decorator-to-end source lines: longest package file `contracts.py` **218 lines**, longest function `VerificationGate.verify` **35 lines**; bridge **92 lines**, gate **86**, runner **101**, verifiers **119**. L2 initializer is a one-line docstring. All within the stated limits.
- Tests used a temporary HOME, with `TARGET_HOME` and `SUDO_USER` unset, system Python and the temporary tool environment's site-packages plus `features/celina` in `PYTHONPATH`. No real network access was required by these tests.

### Final gate output

The following blocks are the captured stdout/stderr from the final commands. Tool variables `V` and `H` refer respectively to the temporary tool virtualenv and temporary HOME. Tool command exit statuses were all 0.

#### Path-form pytest branch coverage

Command: `python3 -m pytest tests/selfimprove/ --cov=features/celina/celina_pkg/selfimprove --cov-branch --cov-report=term-missing -ra --tb=short -o addopts='' -q` (exit 0)

```text
........................................................................ [ 54%]
...........................................................              [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.13.18-final-0 _______________

Name                                                      Stmts   Miss Branch BrPart  Cover   Missing
-----------------------------------------------------------------------------------------------------
features/celina/celina_pkg/selfimprove/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/budget.py             69      0     14      0   100%
features/celina/celina_pkg/selfimprove/contracts.py         125      0     16      0   100%
features/celina/celina_pkg/selfimprove/flags.py              36      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/__init__.py         0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l2/gate.py            44      0     10      0   100%
features/celina/celina_pkg/selfimprove/l2/runner.py          53      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/verifiers.py       63      0     12      0   100%
features/celina/celina_pkg/selfimprove/ledger_bridge.py      45      0     16      0   100%
-----------------------------------------------------------------------------------------------------
TOTAL                                                       435      0     92      0   100%
131 passed in 2.55s
```

#### Full pytest suite

Command: `python3 -m pytest tests/ tests/audit/unit/ tests/selfimprove/ -ra --tb=short -o addopts='' -q` (exit 0)

```text
........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 11%]
........................................................................ [ 13%]
........................................................................ [ 16%]
........................................................................ [ 19%]
........................................................................ [ 22%]
...........................................................
  evidence.detect_prompt_injection: 56/56 = 100.0%
.
  router._sanitize_tool_prompt_content: 56/56 = 100.0%
............ [ 24%]
........................................................................ [ 27%]
........................................................................ [ 30%]
........................................................................ [ 33%]
........................................................................ [ 35%]
........................................................................ [ 38%]
........................................................................ [ 41%]
........................................................................ [ 44%]
........................................................................ [ 46%]
........................................................................ [ 49%]
........................................................................ [ 52%]
........................................................................ [ 55%]
........................................................................ [ 57%]
........................................................................ [ 60%]
........................................................................ [ 63%]
........................................................................ [ 66%]
.........................sssss.......................................... [ 68%]
........................................................................ [ 71%]
........................................................................ [ 74%]
........................................................................ [ 77%]
........................................................................ [ 79%]
........................................................................ [ 82%]
........................................................................ [ 85%]
........................................................................ [ 88%]
........................................................................ [ 90%]
........................................................................ [ 93%]
........................................................................ [ 96%]
........................................................................ [ 99%]
........................                                                 [100%]
=========================== short test summary info ============================
SKIPPED [1] tests/test_code_sandbox.py:76: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:88: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:100: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:111: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:123: bwrap not installed — sandboxed execution tests require bwrap
2611 passed, 5 skipped in 39.24s
```

#### Protected-core and layering guards

Command: `python3 -m pytest tests/selfimprove/test_layering.py tests/selfimprove/test_protected_core.py -ra --tb=short -o addopts='' -q` (exit 0)

```text
..........................                                               [100%]
26 passed in 0.09s
```

#### Ruff

Command: `"$V/bin/ruff" check features/celina/celina_pkg/selfimprove tests/selfimprove` (exit 0)

```text
warning: `incorrect-blank-line-before-class` (D203) and `blank-line-before-class` (D211) are incompatible. Ignoring `incorrect-blank-line-before-class`.
warning: `multi-line-summary-first-line` (D212) and `multi-line-summary-second-line` (D213) are incompatible. Ignoring `multi-line-summary-second-line`.
All checks passed!
```

#### Strict mypy

Command: `"$V/bin/mypy" --strict features/celina/celina_pkg/selfimprove` (exit 0)

```text
Success: no issues found in 9 source files
```

#### Bandit medium/high gate

Command: `"$V/bin/bandit" -r features/celina/celina_pkg/selfimprove -ll` (exit 0)

```text
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.13.18
Run started:2026-11-25 05:53:38.355780+00:00

Test results:
	No issues identified.

Code scanned:
	Total lines of code: 633
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 2
		Medium: 0
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 2
Files skipped (0):
```

#### Config validator

Command: `python3 tools/validate_configs.py` (exit 0)

```text
All 4 config file(s) passed integrity validation.
```

#### Manifest validator

Command: `python3 tools/manifest_integrity.py --verify` (exit 0)

```text
Manifest A: canonical hash OK (5ebf8fb45183b9a1...)
Manifest B: canonical hash OK (5ebf8fb45183b9a1...)
Manifest copies byte-identical (raw sha256=907e3bfea9bddddb...)
Manifest A: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)
Manifest B: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)

Manifest integrity: OK
```

`grep -R -n -E '#[[:space:]]*nosec' features/celina/celina_pkg/selfimprove --include='*.py' | wc -l` output: `0`.


## Gate 3 Wave 2B — L3 evaluation harness COMPLETE

Wave 2A's stop-before-L3 note was historical; the user subsequently approved this Wave 2B. Stop before L4; no L4 or later implementation is authorized by this work.

### Commits and test-first evidence

- Layering change: `afdbb0b0c39cc94feb655e4cd3923dd3cdc4692a` (`test(selfimprove): permit acyclic l3 siblings and ledger_bridge in layering guard`). Before implementation the new allowed-case test failed (1 failed, 3 passed); the guard also tests L3 sibling cycles and upward L4 imports.
- Curated suite: `28fa9bb7bea42255b511d59c8784619c0cf78867` (`feat(selfimprove): add L3 24-task benchmark suite and strict loader`). The new tests first failed on missing `l3` package.
- Store and statistics: `966548b95bbe7efbfa6debb4e7543563bd213bf0` (`feat(selfimprove): add L3 hash-chained EvalStore and paired bootstrap stats`). The new tests first failed on missing `l3.stats`; an added un-hashed extra-field mutation was demonstrated failing to be detected before the canonical-entry fix; permission and dangling-symlink cases were also red before their fixes.
- Runner and CLI: `54d2be1e7ccc2de2020ffe73e52e88e15c6abd8b` (`feat(selfimprove): add L3 EvalRunner and flag-gated CLI smoke runner`). The new tests first failed on missing `l3.cli`. A red test for skipped/error comparisons preceded the fix preventing promotion from synthetic or nonterminal outcomes.
- CLI persistence follow-up: `fdfb986e15337092a7f3d2ed988726a192d87857` (`fix(selfimprove): require explicit store for host-configured CLI persistence [audit-7d]`). A new persistence test failed before the explicit-path/host-evaluator interface existed.
- Full suite passed after each component commit's implementation; final suite **2651 passed, 5 skipped, 0 failed**. The five skipped bwrap tests leave sandboxed execution unverified. Protected-core and layering guards: **29 passed**. Ruff, selfimprove mypy strict, Bandit `-ll`, config and manifest validators returned 0. Bandit found 3 low, 0 medium, 0 high, and 0 `#nosec` lines. Ruff has its two pre-existing conflicting-rule warnings; manifest version drift remains a review warning.
- Final `tests/selfimprove/` path-form branch run: **171 passed**. Actual coverage.py branch counts, not combined statement/branch percentages: `cli.py` **15/16 = 93.75%**, `runner.py` **18/18 = 100%**, `stats.py` **22/22 = 100%**, `store.py` **35/36 = 97.22%**, `suite.py` **16/16 = 100%**. `l3/__init__.py` has no branches. Measured over the entire selfimprove package: **198/200 = 99%**. These measurements are not proof of security or completeness.
- Size audit with the layering guard's decorator-to-end line counting: maximum file `contracts.py` **218 lines**, maximum function `VerificationGate.verify` **35 lines**. L3 files: `__init__.py` 1, `cli.py` 94, `runner.py` 98, `stats.py` 137, `store.py` 208, `suite.py` 101 lines. No Protected Core files or protected hash manifest were changed in this wave.

### Boundaries and limitations

- Task prompts and expected criteria are curated offline descriptions; **no real Celina behavior or model performance has been benchmarked here**. A trusted host must provide an evaluator that actually implements the checks. The CLI's dry-run uses skipped synthetic outcomes and performs no persistence; skipped/error outcomes are rejected by the comparator. Non-dry CLI evaluation requires a host-injected evaluator. CLI persistence requires both `--persist` and an explicit `--store-dir`; standalone `python -m` has no built-in production evaluator. A host can wire L2 gating through `EvalRunner` for code-repair tasks; a claimed code-repair pass without a gate is denied. As in L2, host evaluator, verifier configuration and task classifications are trusted; this is neither execution attestation nor an OS sandbox.
- Store logs have a SHA-256 chain and a separate checkpoint in the same private directory. This detects single-file tail truncation/deletion or modification, but it is **not an independent trust anchor**: an attacker able to roll back or rewrite both log and checkpoint, or delete the entire directory and cause fresh initialization, can evade detection. For stronger rollback protection, retain an independently protected checkpoint outside this store. Advisory `fcntl` locks coordinate cooperating processes; they do not provide OS isolation against a hostile writer or prevent filesystem race attacks. The directory must be host-controlled. A crash between log append and checkpoint update fails closed; coherent rollback of both files remains undetectable. The bridge hook runs after the store commit: if audit recording fails, the append may already be durable and the error propagates.
- Evaluators are synchronous. The runner checks wall time before/after task evaluation but cannot preempt a hanging evaluator; use the separately isolated sandbox boundary and bounded L2 subprocess timeouts for hard interruption. It budgets iterations and two logical file writes for store append; CLI conservatively budgets three initial file writes. It does not independently meter bridge internals or filesystem metadata operations. A timeout during/after persistence cannot retroactively undo a committed entry; do not treat the store as a transactional scheduler. Comparison uses a seeded paired percentile bootstrap and a hard critical pass-to-fail veto; it does not correct for repeated comparisons or establish causal effects.

### Final gate outputs

The following blocks reproduce captured stdout/stderr verbatim. `V` is the temporary tool virtualenv; pytest used system `python3` with its site-packages and `features/celina` in `PYTHONPATH`, an explicit temporary HOME, and `TARGET_HOME`/`SUDO_USER` unset. Each command exited 0.

#### Path-form pytest branch coverage

Command: `python3 -m pytest tests/selfimprove/ --cov=features/celina/celina_pkg/selfimprove --cov-branch --cov-report=term-missing -ra -o addopts='' -q` (exit 0)

```text
........................................................................ [ 42%]
........................................................................ [ 84%]
...........................                                              [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.13.18-final-0 _______________

Name                                                      Stmts   Miss Branch BrPart  Cover   Missing
-----------------------------------------------------------------------------------------------------
features/celina/celina_pkg/selfimprove/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/budget.py             69      0     14      0   100%
features/celina/celina_pkg/selfimprove/contracts.py         125      0     16      0   100%
features/celina/celina_pkg/selfimprove/flags.py              36      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/__init__.py         0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l2/gate.py            44      0     10      0   100%
features/celina/celina_pkg/selfimprove/l2/runner.py          53      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/verifiers.py       63      0     12      0   100%
features/celina/celina_pkg/selfimprove/l3/__init__.py         0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l3/cli.py             59      1     16      1    97%   94
features/celina/celina_pkg/selfimprove/l3/runner.py          55      0     18      0   100%
features/celina/celina_pkg/selfimprove/l3/stats.py           84      0     22      0   100%
features/celina/celina_pkg/selfimprove/l3/store.py          149      4     36      1    97%   80-82, 145
features/celina/celina_pkg/selfimprove/l3/suite.py           56      0     16      0   100%
features/celina/celina_pkg/selfimprove/ledger_bridge.py      45      0     16      0   100%
-----------------------------------------------------------------------------------------------------
TOTAL                                                       838      5    200      2    99%
171 passed in 2.67s
```

#### Full pytest suite

Command: `python3 -m pytest tests/ tests/audit/unit/ tests/selfimprove/ -ra --tb=short -o addopts='' -q` (exit 0)

```text
........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 10%]
........................................................................ [ 13%]
........................................................................ [ 16%]
........................................................................ [ 18%]
........................................................................ [ 21%]
...........................................................
  evidence.detect_prompt_injection: 56/56 = 100.0%
.
  router._sanitize_tool_prompt_content: 56/56 = 100.0%
............ [ 24%]
........................................................................ [ 27%]
........................................................................ [ 29%]
........................................................................ [ 32%]
........................................................................ [ 35%]
........................................................................ [ 37%]
........................................................................ [ 40%]
........................................................................ [ 43%]
........................................................................ [ 46%]
........................................................................ [ 48%]
........................................................................ [ 51%]
........................................................................ [ 54%]
........................................................................ [ 56%]
........................................................................ [ 59%]
........................................................................ [ 62%]
........................................................................ [ 65%]
.................................................................sssss.. [ 67%]
........................................................................ [ 70%]
........................................................................ [ 73%]
........................................................................ [ 75%]
........................................................................ [ 78%]
........................................................................ [ 81%]
........................................................................ [ 84%]
........................................................................ [ 86%]
........................................................................ [ 89%]
........................................................................ [ 92%]
........................................................................ [ 94%]
........................................................................ [ 97%]
................................................................         [100%]
=========================== short test summary info ============================
SKIPPED [1] tests/test_code_sandbox.py:76: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:88: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:100: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:111: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:123: bwrap not installed — sandboxed execution tests require bwrap
2651 passed, 5 skipped in 39.85s
```

#### Protected-core and layering guards

Command: `python3 -m pytest tests/selfimprove/test_protected_core.py tests/selfimprove/test_layering.py -ra --tb=short -o addopts='' -q` (exit 0)

```text
.............................                                            [100%]
29 passed in 0.18s
```

#### Ruff

Command: `"$V/bin/ruff" check features/celina/celina_pkg/selfimprove tests/selfimprove` (exit 0)

```text
warning: `incorrect-blank-line-before-class` (D203) and `blank-line-before-class` (D211) are incompatible. Ignoring `incorrect-blank-line-before-class`.
warning: `multi-line-summary-first-line` (D212) and `multi-line-summary-second-line` (D213) are incompatible. Ignoring `multi-line-summary-second-line`.
All checks passed!
```

#### Strict mypy

Command: `"$V/bin/mypy" --strict features/celina/celina_pkg/selfimprove` (exit 0)

```text
Success: no issues found in 15 source files
```

#### Bandit

Command: `"$V/bin/bandit" -r features/celina/celina_pkg/selfimprove -ll` (exit 0)

```text
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.13.18
Run started:2026-11-25 06:25:46.654216+00:00

Test results:
	No issues identified.

Code scanned:
	Total lines of code: 1169
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 3
		Medium: 0
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 3
Files skipped (0):
```

#### Config validation

Command: `python3 tools/validate_configs.py` (exit 0)

```text
All 4 config file(s) passed integrity validation.
```

#### Manifest verification

Command: `python3 tools/manifest_integrity.py --verify` (exit 0)

```text
Manifest A: canonical hash OK (5ebf8fb45183b9a1...)
Manifest B: canonical hash OK (5ebf8fb45183b9a1...)
Manifest copies byte-identical (raw sha256=907e3bfea9bddddb...)
Manifest A: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)
Manifest B: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)

Manifest integrity: OK
```

#### Real module-entrypoint dry-run

Command: `SELFIMPROVE_ENABLED=1 SELFIMPROVE_EVAL_HARNESS=1 python3 -m celina_pkg.selfimprove.l3.cli --dry-run --suite features/celina/celina_pkg/selfimprove/l3/benchmark_tasks.json` (exit 0)

```text
{"dry_run": true, "task_count": 24, "pass_rate": 0.0, "complete": true}
```

The module-entrypoint dry-run left **0 entries** in its temporary HOME. A recursive `#nosec` search in selfimprove Python source returned **0** matches. The final post-commit `git status --short` was checked separately.


## Gate 3 Wave 3A — L4 failure/fix memory COMPLETE

The user approved Wave 2B and authorized Wave 3A, then authorized narrowly scoped test-only repairs after the first offline full-suite gate failed. Stop here: L5 and later were not started and require explicit approval.

### Commits and test-first evidence

- Test-isolation prerequisites, committed separately: `72ac9292bab7b870a03872692cdaf4054da3623a` (stub resolver in frontier/redirect tests); `1e7357fb2c5060448f778b10b545880579e8d82d` (avoid real Ponytail clone); `5354fffd38896a26fd611fcd4ab62438274edf28` (isolate stage exit-policy verification from privileged state writes). The isolated stage test runs a temporary copy and mocks only `write_state_json`; it does not test that privileged persistence itself succeeds. Additional SQLite test connection cleanup: `426637c907ccb920482f78ebff63914febab4255`.
- L4 layering: `57d0a202f162e49c9ccc248a10bcfa6983a8f283`. Red: 1 failed, 6 passed; then the sibling, cycle and L1/L2/L3/L5 negative checks passed.
- Redactor/framer: `fb95b4336612365419583df2e51d9e99e195291e`. Initial red tests failed because `l4` did not exist; passing tests include synthetic credentials, PEM, PII, Unicode, framing and 20,000-character adversarial inputs.
- Store: `0d938ddde42bf0908005e655e150f9446d814971`. Initial tests failed because `l4.store` did not exist. A red regression demonstrated an insecure existing directory could get an empty DB before rejection; the fix rejects before creation.
- SQLite safety follow-up: `a1fc5490563746904392ce0611b1ddf1695c591c`. A red test showed that an existing WAL database was accepted for retrieval; the fix rejects WAL headers and WAL/SHM/rollback sidecars before connecting. `_sqlite_guard.py` is an additional acyclic L4 sibling.
- Retriever: `3840b502d31729a3d1badc9faf54734b3a512a29`. Initial red tests failed because `l4.retriever` did not exist. Tests cover deterministic ranking, read-only DB properties, bounded results, and escaped untrusted framing.

### Offline regression and final results

- First full run under network-family socket denial: **15 failed, 2642 passed, 5 skipped**. Twelve failures were from `example.com` DNS lookups in existing tests, one Ponytail test attempted a clone, and two failures concerned stage verification/executable mode. The socket denial prevented Internet-family socket creation. Three representative DNS/clone cases also failed in a separate detached `c534e2b` baseline under the same restriction. The user's approval covers the surgical test-only repairs above. A tracked executable mode altered during the initial run was restored; no production or Protected Core files were changed by these repairs. The prior Wave 2B green result did not establish that every legacy test was offline.
- After repairs, the full suite passed; final detached-worktree run: **2685 passed, 5 skipped, 0 failed**. Five skips require unavailable bwrap, so sandboxed execution remains unverified. A session-finish origin probe found **0** loaded `celina_pkg` modules outside the detached worktree; it does not prove all subprocess/file accesses stayed there.
- Final selfimprove path-form branch coverage: **205 passed**. Actual coverage.py branch counts: `_sqlite_guard.py` **8/8**, `redactor.py` **14/14**, `retriever.py` **8/8**, `store.py` **56/56**, each **100%**. `l4/__init__.py` is a one-line docstring with zero branches. Entire package: **284/286 = 99.30%**. Coverage does not prove all security properties.
- Final guards: **35 passed**. Ruff, strict mypy (20 source files), Bandit `-ll`, config validation and manifest verification exited 0. Bandit reports 3 low, 0 medium, 0 high, and no `#nosec`; an additional search over selfimprove source and tests returned zero matches. Ruff's two pre-existing conflicting-rule warnings and the manifest/version drift warning remain.
- Maximum source file: `l4/store.py` **283 lines**. Maximum function: `MemoryRetriever.retrieve` **39 lines**, counting decorators through the end line. L4 file sizes: initializer 1, `_sqlite_guard.py` 35, `redactor.py` 96, `retriever.py` 108, `store.py` 283 lines. Protected Core and `selfimprove/protected_hashes.json` are unchanged from the Wave 2B approved baseline.

### Security boundaries and limitations

- Redaction is heuristic defense-in-depth, **not formal DLP**: unknown formats, obfuscation, uncommon PII or unlabelled secrets may escape detection, and phone detection may have false positives. Replacement counts describe regex matches, not all sensitive information. Tests check representative synthetic values in SQLite trace, rows and DB bytes, but cannot prove all secrets are removed. SQLite is not encrypted or tamper-evident.
- Sanitization occurs before SQLite connection on episode writes; secret-looking or nonportable identifiers are rejected rather than stored. The L0 `MemoryStoreProtocol` `store(AttemptRecord)`/`lookup(task_id)` signatures are implemented separately from richer `record`/`query` operations; identifiers are restricted to a safe ASCII subset and rejected if a redactor rule recognizes sensitive data.
- Explicit private directory/DB modes and pre-open symlink checks are not an OS sandbox. Paths must be in a host-controlled directory: path checks and subsequent SQLite opens are not atomic against a hostile concurrent filesystem actor. WAL and sidecars are rejected fail-closed, including a journal left by a crash or an active concurrent writer; no automatic recovery is attempted. The ledger hook is after the SQLite commit, so hook failure can propagate after persistence rather than rolling back the DB.
- Retrieval examines at most the **1000 newest** stored episodes before ranking and returns at most **5**. Older candidates outside that window can be omitted. Returned structured items and framed text are untrusted data. Escaped delimiters, control-character filtering and a data-only header reduce breakout risk but cannot force a model to ignore hostile text; they complement, not replace, primary approval and policy gating.
- Verification/dev tooling was installed only in `/tmp/selfimprove-tools-venv` with the user's explicit permission; no L4 runtime dependencies were added. All subsequent pytest commands shown here ran through a seccomp wrapper denying creation of Internet, packet, Bluetooth and vsock sockets, inherited by child processes; AF_UNIX was left available for local IPC. This is not a proof against every possible external proxy or kernel-level escape. The 171-test preflight before that wrapper was introduced passed but was not independently network-instrumented. Python regex adversarial timing is tested on this environment and is not a universal worst-case timing proof.

### Final gate outputs

The following are captured stdout/stderr verbatim; all commands exited 0. `V=/tmp/selfimprove-tools-venv`; system Python used the virtualenv's site-packages plus `features/celina` in `PYTHONPATH`, temporary HOME, and unset `TARGET_HOME`/`SUDO_USER`. The pytest commands were run through `/tmp/wave3a-offline.py`; the full run used a detached worktree and the session-finish origin probe.

#### Selfimprove path-form coverage

Command: `python3 /tmp/wave3a-offline.py python3 -m pytest tests/selfimprove/ --cov=features/celina/celina_pkg/selfimprove --cov-branch --cov-report=term-missing -ra -o addopts='' -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [ 35%]
........................................................................ [ 70%]
.............................................................            [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.13.18-final-0 _______________

Name                                                         Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------------------
features/celina/celina_pkg/selfimprove/__init__.py               0      0      0      0   100%
features/celina/celina_pkg/selfimprove/budget.py                69      0     14      0   100%
features/celina/celina_pkg/selfimprove/contracts.py            125      0     16      0   100%
features/celina/celina_pkg/selfimprove/flags.py                 36      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l2/gate.py               44      0     10      0   100%
features/celina/celina_pkg/selfimprove/l2/runner.py             53      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/verifiers.py          63      0     12      0   100%
features/celina/celina_pkg/selfimprove/l3/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l3/cli.py                59      1     16      1    97%   94
features/celina/celina_pkg/selfimprove/l3/runner.py             55      0     18      0   100%
features/celina/celina_pkg/selfimprove/l3/stats.py              84      0     22      0   100%
features/celina/celina_pkg/selfimprove/l3/store.py             149      4     36      1    97%   80-82, 145
features/celina/celina_pkg/selfimprove/l3/suite.py              56      0     16      0   100%
features/celina/celina_pkg/selfimprove/l4/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l4/_sqlite_guard.py      17      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/redactor.py           55      0     14      0   100%
features/celina/celina_pkg/selfimprove/l4/retriever.py          46      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/store.py             178      0     56      0   100%
features/celina/celina_pkg/selfimprove/ledger_bridge.py         45      0     16      0   100%
--------------------------------------------------------------------------------------------------------
TOTAL                                                         1134      5    286      2    99%
205 passed in 2.71s
```

#### Full pytest suite in detached worktree

Command: `python3 /tmp/wave3a-offline.py python3 -m pytest -p wave3a_probe tests/ tests/audit/unit/ tests/selfimprove/ -ra --tb=short -o addopts='' -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 10%]
........................................................................ [ 13%]
........................................................................ [ 16%]
........................................................................ [ 18%]
........................................................................ [ 21%]
...........................................................
  evidence.detect_prompt_injection: 56/56 = 100.0%
.
  router._sanitize_tool_prompt_content: 56/56 = 100.0%
............ [ 24%]
........................................................................ [ 26%]
........................................................................ [ 29%]
........................................................................ [ 32%]
........................................................................ [ 34%]
........................................................................ [ 37%]
........................................................................ [ 40%]
........................................................................ [ 42%]
........................................................................ [ 45%]
........................................................................ [ 48%]
........................................................................ [ 50%]
........................................................................ [ 53%]
........................................................................ [ 56%]
........................................................................ [ 58%]
........................................................................ [ 61%]
........................................................................ [ 64%]
........................................................................ [ 66%]
...........................sssss........................................ [ 69%]
........................................................................ [ 72%]
........................................................................ [ 74%]
........................................................................ [ 77%]
........................................................................ [ 80%]
........................................................................ [ 82%]
........................................................................ [ 85%]
........................................................................ [ 88%]
........................................................................ [ 91%]
........................................................................ [ 93%]
........................................................................ [ 96%]
........................................................................ [ 99%]
..........................                                               [100%]LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0

=========================== short test summary info ============================
SKIPPED [1] tests/test_code_sandbox.py:76: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:88: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:100: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:111: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:123: bwrap not installed — sandboxed execution tests require bwrap
2685 passed, 5 skipped in 41.28s
```

#### Protected-core and layering tests

Command: `python3 /tmp/wave3a-offline.py python3 -m pytest tests/selfimprove/test_protected_core.py tests/selfimprove/test_layering.py -ra --tb=short -o addopts='' -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
...................................                                      [100%]
35 passed in 0.20s
```

#### Ruff

Command: `python3 /tmp/wave3a-offline.py "$V/bin/ruff" check features/celina/celina_pkg/selfimprove tests/selfimprove` (exit 0)

```text
warning: `incorrect-blank-line-before-class` (D203) and `blank-line-before-class` (D211) are incompatible. Ignoring `incorrect-blank-line-before-class`.
warning: `multi-line-summary-first-line` (D212) and `multi-line-summary-second-line` (D213) are incompatible. Ignoring `multi-line-summary-second-line`.
All checks passed!
```

#### Strict mypy

Command: `python3 /tmp/wave3a-offline.py "$V/bin/mypy" --strict features/celina/celina_pkg/selfimprove` (exit 0)

```text
Success: no issues found in 20 source files
```

#### Bandit

Command: `python3 /tmp/wave3a-offline.py "$V/bin/bandit" -r features/celina/celina_pkg/selfimprove -ll` (exit 0)

```text
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.13.18
Run started:2026-11-25 09:10:17.458262+00:00

Test results:
	No issues identified.

Code scanned:
	Total lines of code: 1611
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 3
		Medium: 0
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 3
Files skipped (0):
```

#### Config validation

Command: `python3 /tmp/wave3a-offline.py python3 tools/validate_configs.py` (exit 0)

```text
All 4 config file(s) passed integrity validation.
```

#### Manifest integrity

Command: `python3 /tmp/wave3a-offline.py python3 tools/manifest_integrity.py --verify` (exit 0)

```text
Manifest A: canonical hash OK (5ebf8fb45183b9a1...)
Manifest B: canonical hash OK (5ebf8fb45183b9a1...)
Manifest copies byte-identical (raw sha256=907e3bfea9bddddb...)
Manifest A: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)
Manifest B: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)

Manifest integrity: OK
```

#### AST size audit

Command: `python3 AST size audit (see command recorded in session)` (exit 0)

```text
MAX FILE (283, 'features/celina/celina_pkg/selfimprove/l4/store.py')
MAX FUNCTION (39, 'features/celina/celina_pkg/selfimprove/l4/retriever.py', 'retrieve')
L4 FILE 1 features/celina/celina_pkg/selfimprove/l4/__init__.py
L4 FILE 35 features/celina/celina_pkg/selfimprove/l4/_sqlite_guard.py
L4 FILE 96 features/celina/celina_pkg/selfimprove/l4/redactor.py
L4 FILE 108 features/celina/celina_pkg/selfimprove/l4/retriever.py
L4 FILE 283 features/celina/celina_pkg/selfimprove/l4/store.py
```

A recursive `#nosec` search over selfimprove source and tests returned `0`. A post-commit `git status --short` was checked separately.


## Gate 3 — Wave 3B: L5 Separate Critic & Refinement Loop

Implementation is complete and stops at the L5 boundary; **L6 has not been started**. The approved Wave 3A baseline was `df41580702592e806a3f91fc63c5e12108f214b2` and the preflight selfimprove tests were 205/205. The worktree was clean at preflight.

### Test-first commits and verification

- L5 acyclic sibling guard: `16ba6b454da976717b1c204bad0c6787fe41e1e1`. The focused red run showed `1 failed, 5 passed, 30 deselected`: the L5 acyclic sibling was rejected before the guard was changed. Tests also reject cycles and L3/L6/L7 imports. The full offline suite after the guard was 2690 passed, 5 skipped.
- Separate `RubricCritic`: `13b819211b5c6f28f9c0bb4995328026f1664954`. The initial critic tests failed at collection because L5 did not exist. The L0 `CriticProtocol.critique(task, attempts)` contains no verifier input and therefore cannot certify success; the additional immutable assessment API accepts explicit `VerifierResult` and optional `GateDecision`. Failed, skipped, error, empty, or inconsistent verifier evidence is clamped to zero regardless of actor claims/scorer; normalized bounded failures yield deterministic signatures and hints. The intermediate full offline suite was 2703 passed, 5 skipped.
- `RefinementLoop`: `0356956b481f98df0f359efb8768a53e14369abd`. Initial tests failed at collection because `l5.loop` did not exist. A later test-first check also exposed missing runtime protocol exports and passed after fixing them. The loop and gate require the same `BudgetGuard` and flags. Only nonempty, all-passing L2 evidence permits completion. Mutation declarations and declared file writes require a separate positive approval before the trusted actor's apply callback. L4 memory is gated, framed as untrusted, and only resolved verifier failures are written through the redacting store. The intermediate offline suite before the protocol-export test was 2718 passed, 5 skipped.

### Final results and boundaries

- Final detached full suite: **2719 passed, 5 skipped, 0 failed**; all skips require unavailable bwrap, so bwrap sandbox execution remains unverified. The session-finish Python-origin probe reported `LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0`; it does not prove all subprocess or filesystem accesses are confined to that worktree.
- Final path-form selfimprove coverage: **239 passed**. Actual coverage.py L5 branch counts: critic **22/22 (100%)**, loop **32/32 (100%)**, types **12/12 (100%)**; initializer has no executable branches. Whole selfimprove package **350/352 branches (99.43%)**. Coverage is not a security proof.
- Protected-core/layering guards **40 passed**. Ruff, strict mypy, Bandit `-ll`, both validators passed. Bandit: 3 low, 0 medium, 0 high; recursive `#nosec` search over selfimprove source and tests found zero. Ruff's two conflicting-rule warnings and manifest 1.0 versus version.toml 4.4 drift warning are pre-existing.
- Maximum source file **283 lines** (`l4/store.py`); maximum function **39 lines** (`MemoryRetriever.retrieve`), counting decorators. L5 files: initializer 1, critic 166, loop 209, types 110 lines; maximum L5 function is `RubricCritic.assess`, 35 lines. Protected Core and `selfimprove/protected_hashes.json` have no diff from the approved Wave 3A baseline.
- This is trusted in-process orchestration, **not attestation or an OS sandbox**. The actor and configured verifiers/executors must be host-trusted. An actor that mutates during `propose`, misdeclares usage, uses unmanaged resources, or otherwise bypasses callbacks cannot be constrained by these DTOs; approval is enforced only before the loop's `apply` callback. Token/write/subprocess accounting relies on trusted declarations and shared L1-aware configured components. Bridge and memory charges are logical writes, not a count of every physical filesystem or ledger operation. Proposal-time work can occur before its declared usage is charged. Synchronous callbacks cannot be preempted; elapsed time is checked before/after boundaries.
- The critic's redaction and the L4 store redaction are heuristic, not formal DLP. Signatures include bounded normalized verifier failure details; material nondeterminism in those details may prevent detection of semantically identical failures. L4 framing labels stored context untrusted but cannot enforce model behavior. The bridge receives only compact event metadata here; a bridge/store exception can propagate after an earlier durable side effect, and cannot roll that side effect back. Failure outcomes caused by budget exhaustion need not emit a completion event when the budget cannot permit the ledger write.
- No live network or LLM was used by these tests. All commands below ran through `/tmp/wave3a-offline.py`, a seccomp wrapper denying creation of Internet, packet, Bluetooth and vsock sockets and inherited by child processes; AF_UNIX remained available. This does not prove absence of every possible external proxy, inherited descriptor, or kernel-level escape. Tests used system Python with temporary HOME, unset TARGET_HOME/SUDO_USER, and `PYTHONPATH=/tmp/selfimprove-tools-venv/lib/python3.13/site-packages:features/celina`; the detached full suite additionally prepended `/tmp` for the origin probe. The temporary verification tools add no runtime dependency.

### Source/test SHA-256 hashes

```text
fbd8eb00133bd6d4c1464e5d88e491b21e566e083bcc3b92d5ebfda6e54b72d1  features/celina/celina_pkg/selfimprove/l5/__init__.py
715cdadecb2b9b7e48adee9b092c73b7970fcf26d26f87cc853d9196520327a9  features/celina/celina_pkg/selfimprove/l5/critic.py
3140d76c2b57f1b3f01e5281c23b2312024c2af751a117c51c551c629c88106c  features/celina/celina_pkg/selfimprove/l5/loop.py
e5a3d334060466d17df8985d5a70f374db953f366340c197bc941245fde9e6ec  features/celina/celina_pkg/selfimprove/l5/types.py
63a12c1be4029666d7b37ef3f0aa48df4a783870e53946d9a35fb499adc143b8  tests/selfimprove/test_l5_critic.py
0183c8026a4f4439118fde515619bbdeda2d7734b3adc4748ed693937ca17201  tests/selfimprove/test_l5_loop.py
74105ddaac5f0bcaaf97949657ab5f5a24178c24f1d593abc795dc7bf01f8aeb  tests/selfimprove/test_layering.py
```

### Exact final gate outputs

Each following command exited 0. `V=/tmp/selfimprove-tools-venv`; commands and captured stdout/stderr are shown verbatim. Runtime and Bandit timestamps are from the sandbox clock.

#### Selfimprove path-form branch coverage

Command: `python3 /tmp/wave3a-offline.py python3 -m pytest tests/selfimprove/ --cov=features/celina/celina_pkg/selfimprove --cov-branch --cov-report=term-missing -ra -o addopts='' -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [ 30%]
........................................................................ [ 60%]
........................................................................ [ 90%]
.......................                                                  [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.13.18-final-0 _______________

Name                                                         Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------------------
features/celina/celina_pkg/selfimprove/__init__.py               0      0      0      0   100%
features/celina/celina_pkg/selfimprove/budget.py                69      0     14      0   100%
features/celina/celina_pkg/selfimprove/contracts.py            125      0     16      0   100%
features/celina/celina_pkg/selfimprove/flags.py                 36      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l2/gate.py               44      0     10      0   100%
features/celina/celina_pkg/selfimprove/l2/runner.py             53      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/verifiers.py          63      0     12      0   100%
features/celina/celina_pkg/selfimprove/l3/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l3/cli.py                59      1     16      1    97%   94
features/celina/celina_pkg/selfimprove/l3/runner.py             55      0     18      0   100%
features/celina/celina_pkg/selfimprove/l3/stats.py              84      0     22      0   100%
features/celina/celina_pkg/selfimprove/l3/store.py             149      4     36      1    97%   80-82, 145
features/celina/celina_pkg/selfimprove/l3/suite.py              56      0     16      0   100%
features/celina/celina_pkg/selfimprove/l4/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l4/_sqlite_guard.py      17      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/redactor.py           55      0     14      0   100%
features/celina/celina_pkg/selfimprove/l4/retriever.py          46      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/store.py             178      0     56      0   100%
features/celina/celina_pkg/selfimprove/l5/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l5/critic.py             72      0     22      0   100%
features/celina/celina_pkg/selfimprove/l5/loop.py              110      0     32      0   100%
features/celina/celina_pkg/selfimprove/l5/types.py              59      0     12      0   100%
features/celina/celina_pkg/selfimprove/ledger_bridge.py         45      0     16      0   100%
--------------------------------------------------------------------------------------------------------
TOTAL                                                         1375      5    352      2    99%
239 passed in 4.41s
```

#### Full pytest, detached worktree

Command: `python3 /tmp/wave3a-offline.py python3 -m pytest -p wave3a_probe tests/ tests/audit/unit/ tests/selfimprove/ -ra --tb=short -o addopts='' -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  7%]
........................................................................ [ 10%]
........................................................................ [ 13%]
........................................................................ [ 15%]
........................................................................ [ 18%]
........................................................................ [ 21%]
...........................................................
  evidence.detect_prompt_injection: 56/56 = 100.0%
.
  router._sanitize_tool_prompt_content: 56/56 = 100.0%
............ [ 23%]
........................................................................ [ 26%]
........................................................................ [ 29%]
........................................................................ [ 31%]
........................................................................ [ 34%]
........................................................................ [ 37%]
........................................................................ [ 39%]
........................................................................ [ 42%]
........................................................................ [ 44%]
........................................................................ [ 47%]
........................................................................ [ 50%]
........................................................................ [ 52%]
........................................................................ [ 55%]
........................................................................ [ 58%]
........................................................................ [ 60%]
........................................................................ [ 63%]
........................................................................ [ 66%]
.............................................................sssss...... [ 68%]
........................................................................ [ 71%]
........................................................................ [ 74%]
........................................................................ [ 76%]
........................................................................ [ 79%]
........................................................................ [ 81%]
........................................................................ [ 84%]
........................................................................ [ 87%]
........................................................................ [ 89%]
........................................................................ [ 92%]
........................................................................ [ 95%]
........................................................................ [ 97%]
............................................................             [100%]LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0

=========================== short test summary info ============================
SKIPPED [1] tests/test_code_sandbox.py:76: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:88: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:100: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:111: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:123: bwrap not installed — sandboxed execution tests require bwrap
2719 passed, 5 skipped in 44.02s
```

#### Protected and layering guards

Command: `python3 /tmp/wave3a-offline.py python3 -m pytest tests/selfimprove/test_protected_core.py tests/selfimprove/test_layering.py -ra --tb=short -o addopts='' -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................                                 [100%]
40 passed in 0.31s
```

#### Ruff

Command: `python3 /tmp/wave3a-offline.py "$V/bin/ruff" check features/celina/celina_pkg/selfimprove tests/selfimprove` (exit 0)

```text
warning: `incorrect-blank-line-before-class` (D203) and `blank-line-before-class` (D211) are incompatible. Ignoring `incorrect-blank-line-before-class`.
warning: `multi-line-summary-first-line` (D212) and `multi-line-summary-second-line` (D213) are incompatible. Ignoring `multi-line-summary-second-line`.
All checks passed!
```

#### Strict mypy

Command: `python3 /tmp/wave3a-offline.py "$V/bin/mypy" --strict features/celina/celina_pkg/selfimprove` (exit 0)

```text
Success: no issues found in 24 source files
```

#### Bandit

Command: `python3 /tmp/wave3a-offline.py "$V/bin/bandit" -r features/celina/celina_pkg/selfimprove -ll` (exit 0)

```text
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.13.18
Run started:2026-11-25 09:38:07.972036+00:00

Test results:
	No issues identified.

Code scanned:
	Total lines of code: 2019
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 3
		Medium: 0
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 3
Files skipped (0):
```

#### Config validation

Command: `python3 /tmp/wave3a-offline.py python3 tools/validate_configs.py` (exit 0)

```text
All 4 config file(s) passed integrity validation.
```

#### Manifest validation

Command: `python3 /tmp/wave3a-offline.py python3 tools/manifest_integrity.py --verify` (exit 0)

```text
Manifest A: canonical hash OK (5ebf8fb45183b9a1...)
Manifest B: canonical hash OK (5ebf8fb45183b9a1...)
Manifest copies byte-identical (raw sha256=907e3bfea9bddddb...)
Manifest A: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)
Manifest B: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)

Manifest integrity: OK
```

#### AST size audit

`python3` AST audit counts decorators through function end lines (exit 0):

```text
MAX FILE (283, 'features/celina/celina_pkg/selfimprove/l4/store.py')
MAX FUNCTION (39, 'features/celina/celina_pkg/selfimprove/l4/retriever.py', 'retrieve')
L5 FILE 1 features/celina/celina_pkg/selfimprove/l5/__init__.py
L5 FILE 166 features/celina/celina_pkg/selfimprove/l5/critic.py
L5 FILE 209 features/celina/celina_pkg/selfimprove/l5/loop.py
L5 FILE 110 features/celina/celina_pkg/selfimprove/l5/types.py
FUNCTION (39, 'features/celina/celina_pkg/selfimprove/l4/retriever.py', 'retrieve')
FUNCTION (35, 'features/celina/celina_pkg/selfimprove/l5/critic.py', 'assess')
FUNCTION (35, 'features/celina/celina_pkg/selfimprove/l2/gate.py', 'verify')
FUNCTION (34, 'features/celina/celina_pkg/selfimprove/l5/loop.py', 'run')
FUNCTION (32, 'features/celina/celina_pkg/selfimprove/l4/store.py', 'record')
```

The recursive `#nosec` search returned zero matches. `git diff --name-status` for all protected files against `df41580702592e806a3f91fc63c5e12108f214b2` returned empty. Status was clean before this documentation change.


## Gate 3 — Wave 4A: L6 Offline Prompt & Skill Optimizer — COMPLETE

The approved Wave 3B baseline was `3566746bbf1ff7bdfcb47f8284576f2e678bef2b`. Preflight status and mode diff were empty, and the offline selfimprove suite passed **239 tests**. This wave stops at L6: **L7 has not been started**.

### Test-first commits

- `58f401f648420661d14f11f51d0c7c042f45c69c` — L6 acyclic sibling layering. Focused initial red: **1 failed, 9 passed, 33 deselected** because acyclic L6 imports were forbidden. Added cycle, forbidden L2/L4/L5/L7 and initializer tests. Intermediate offline full suite: **2726 passed, 5 skipped**.
- `51d7acefc101f2aff9ae77a9a117f09744f216a1` — L6 DTOs and private hash-verified registry. Initial tests failed at collection because L6 did not exist. Additional red tests caught missing/empty stored hashes, valid large Unicode JSON truncation, and switching away from a missing active version. Intermediate full suite: **2749 passed, 5 skipped**.
- `7fdd9a66696debbeb250568af03a768f704c7f0e` — budgeted offline optimizer. Initial tests failed at collection because the optimizer did not exist. A further red test showed L3 obscures a verifier-side L1 dimension; an L6 structural gate adapter preserves the actual L1 dimension while delegating all verification to the host-configured gate. Intermediate full suite: **2759 passed, 5 skipped**.
- `db4cb6abf7e358dac8c99d097faaad90555c6607` — approval-bound promoter and rollback. Initial promoter tests failed at collection. Added locked active-baseline recheck after approval, reuse of existing candidate versions, and a simulated approval race. Intermediate full suite: **2771 passed, 5 skipped**.
- `ea31580932ed4a71c7f67b861d23db744eda1492` — a red regression exposed reuse of a deleted latest artifact number. A private version-head record now detects missing/inconsistent history before registration. A second red test exposed an excessively large head causing an overflow/allocation; validation now compares lengths without materializing the range. Intermediate full suite: **2773 passed, 5 skipped**.
- `d882142851c84d7466841479a71db4d979ed5e0d` — a red test showed failed evaluations emitted no completion audit event. Failed outcomes now attempt a compact completion event when permitted; budget or bridge failure can prevent it. Intermediate full suite: **2774 passed, 5 skipped**.
- `de0a94edf3668c5be14dc0c89213d8d1b7f4c126` — additional fail-closed and branch-coverage tests. Intermediate full suite: **2781 passed, 5 skipped**.

### Final results

- Detached offline full suite **2781 passed, 5 skipped, 0 failed**; session-finish probe: `LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0`. The five bwrap-dependent tests are skipped; sandboxed execution is therefore unverified. The probe reports loaded Python module origins, not all subprocess or filesystem access.
- Path-form selfimprove coverage **301 passed**. Actual L6 branch counts: `_gate_adapter.py` **2/2 (100%)**, `_registry_io.py` **24/24 (100%)**, `optimizer.py` **26/26 (100%)**, `promoter.py` **22/22 (100%)**, `registry.py` **43/44 (97.73%)**, `types.py` **44/44 (100%)**. The initializer is a one-line docstring and has zero executable branches. Total package **511/514 branches (99.42%)**. The uncovered registry branch handles an artifact disappearing during a concurrent scan. Coverage is not a proof of security.
- Protected-core/layering tests **47 passed**. Ruff, strict mypy (31 source files), Bandit `-ll`, config validation and manifest verification all exited zero. Bandit: **3 low, 0 medium, 0 high**, and **0 `#nosec`**; recursive source/test search found zero. Ruff's two conflicting-rule warnings and manifest/version drift warning remain pre-existing.
- Max selfimprove source file **283 lines** (`l4/store.py`); max function **39 lines** (`MemoryRetriever.retrieve`), including decorators. L6 file lengths: `__init__` 1, `_gate_adapter` 44, `_registry_io` 124, `optimizer` 252, `promoter` 141, `registry` 220, `types` 279. Max L6 function: `OfflineOptimizer._candidate`, 35 lines. Protected Core files and `selfimprove/protected_hashes.json` have no diff from the approved Wave 3B baseline.

### Trust boundaries and limitations

- Registry storage is **not an OS sandbox, encryption, tamper-evident ledger, or formal secret redactor**. Artifact content is stored verbatim in private 0600 files; do not put secrets in artifacts unless the host explicitly accepts that risk. The host must control the parent and 0700 registry directory. Symlink, private-mode and link-count checks are defense in depth; pathname checks and opens are not atomic against a hostile filesystem actor, and flock coordinates cooperating writers only.
- Each immutable version is published without overwrite and checked against its stored SHA-256; the version head detects ordinary missing or inconsistent history. Neither the stored hash nor head is externally anchored: an actor able to replace both content and hash, or coordinate head/history rollback or delete the whole registry, can evade those checks. A crash between publishing a version and updating its head fails subsequent registration closed; file fsync and atomic rename/link do not establish full directory-entry durability across power loss. An interrupted process may leave a private temporary file. Recovery is a trusted host operation.
- Direct registry `activate`, `rollback`, and `promote` are trusted low-level host primitives. The `ArtifactPromoter` performs the evaluation and explicit approval checks before calling them; an actor with direct host access to the primitives or registry files can bypass that orchestration. Approval is bound to a deterministic proposal digest in process, **not** to a cryptographically authenticated human identity. A production gate must use Celina's human approval flow and enforce single-use atomic tokens; the DTO/digest alone does not prevent replay of an identical approval after rollback. A stale active baseline is rechecked under the registry lock before registering or switching.
- The supplied benchmark suite, security classifications, generator, evaluator factory, evaluator reports, optional L3 gate, and declared token usage are trusted host configuration, not independently attested. L3's paired bootstrap uses task pass/fail outcomes and vetoes a security-critical pass-to-nonpass regression; an in-process caller that fabricates both evaluation data and comparison objects is outside this boundary. Candidate evaluation uses a provisional artifact version, and no candidate is registered or activated by the optimizer itself.
- L0 `EvalReport` contains no token count. The host evaluator adapter returns an L0 report plus a declared token count, charged to the shared L1 guard after the synchronous callback; generator token declarations are similarly charged. Such accounting cannot prevent unreported work, or undo work already done before accounting. L3 task iterations use the same guard; synchronous generator/evaluator callbacks and bootstrap computation cannot be preempted mid-call, so wall checks occur around them. For L3 `code_repair` passes, a host-configured shared-budget verifier gate must be supplied via the runner template; without one L3 fails closed.
- Optional ledger events carry hashes, scores, booleans, statuses and version numbers, not raw artifact contents or reasons. Hashes are not secrecy guarantees. An audit bridge exception can propagate after a registry write and cannot undo it. Failed optimization attempts try to log completion, but an exhausted budget or broken ledger can prevent that event; failure is not converted to success.
- No live network or LLM was used in the test runs. Commands below ran through `/tmp/wave3a-offline.py`, a seccomp launcher denying creation of Internet, packet, Bluetooth and vsock sockets and inherited by child processes; AF_UNIX remained available. This does not prove absence of every possible external proxy, inherited descriptor or kernel-level escape. Pytest used system Python with temporary HOME, unset TARGET_HOME/SUDO_USER and `PYTHONPATH=/tmp/selfimprove-tools-venv/lib/python3.13/site-packages:features/celina`; the detached full suite additionally prepended `/tmp` for the origin probe. Verification tools were pre-existing temporary dev tools; no L6 runtime dependency was added.

### SHA-256: L6 source and tests

```text
b9f71f5e910220d40dd48034314864c4e13908c9cfe9a254cd998d5edaaad0bb  features/celina/celina_pkg/selfimprove/l6/__init__.py
35445741e388371bfb2d80deffdf82feb16bf4546a150b0e16de7300c401734f  features/celina/celina_pkg/selfimprove/l6/_gate_adapter.py
6aa45d3fb14b233e46fdddfac2c7c620bf2feaead37ac47c1618ccd67b5c1945  features/celina/celina_pkg/selfimprove/l6/_registry_io.py
6d41d2b722255fd9a685c7bd33108a18a57c098b57665ffbcf103708cedaa6e9  features/celina/celina_pkg/selfimprove/l6/optimizer.py
423780f01f4bc7fbf1be916a11a1c1df0d13492789a3ee0556b7ba23b70ff96f  features/celina/celina_pkg/selfimprove/l6/promoter.py
b8ceaf2305bfced174acca2c3230bd44e5045061a2aa5aa3da5eb9543550acf9  features/celina/celina_pkg/selfimprove/l6/registry.py
a145e07263cef0784ca154d8c2b096d3c47942fe330d315f310247ef5f3c42e3  features/celina/celina_pkg/selfimprove/l6/types.py
a1feedefc4452b52c7512aa5eb030cb199f34c5259f1658742926fc9c3f9e632  tests/selfimprove/test_l6_edges.py
18bc005eea54fb16e343348ecd2d49caede261ddf08a1329ec39e617aa23b99a  tests/selfimprove/test_l6_optimizer.py
675c6f84dff6039a86eb5986ed1c6c3c6b2e7443462fef880260ca916922d0bd  tests/selfimprove/test_l6_promoter.py
7aaeae62c23a1a46f3000070860f8d905f7f8115ce2300037b1b911e27423c1a  tests/selfimprove/test_l6_registry.py
14b829167ce1cbcb3908ee2900d283c2954afb75793c1d4f597894c8016c37b0  tests/selfimprove/test_layering.py
```

### Exact final gate outputs

Each command exited 0. `V=/tmp/selfimprove-tools-venv`; output is captured stdout/stderr verbatim. Runtime and Bandit timestamps are from the sandbox clock.

#### Selfimprove path-form branch coverage

Command: `python3 /tmp/wave3a-offline.py python3 -m pytest tests/selfimprove/ --cov=features/celina/celina_pkg/selfimprove --cov-branch --cov-report=term-missing -ra -o addopts='' -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [ 23%]
........................................................................ [ 47%]
........................................................................ [ 71%]
........................................................................ [ 95%]
.............                                                            [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.13.18-final-0 _______________

Name                                                         Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------------------
features/celina/celina_pkg/selfimprove/__init__.py               0      0      0      0   100%
features/celina/celina_pkg/selfimprove/budget.py                69      0     14      0   100%
features/celina/celina_pkg/selfimprove/contracts.py            125      0     16      0   100%
features/celina/celina_pkg/selfimprove/flags.py                 36      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l2/gate.py               44      0     10      0   100%
features/celina/celina_pkg/selfimprove/l2/runner.py             53      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/verifiers.py          63      0     12      0   100%
features/celina/celina_pkg/selfimprove/l3/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l3/cli.py                59      1     16      1    97%   94
features/celina/celina_pkg/selfimprove/l3/runner.py             55      0     18      0   100%
features/celina/celina_pkg/selfimprove/l3/stats.py              84      0     22      0   100%
features/celina/celina_pkg/selfimprove/l3/store.py             149      4     36      1    97%   80-82, 145
features/celina/celina_pkg/selfimprove/l3/suite.py              56      0     16      0   100%
features/celina/celina_pkg/selfimprove/l4/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l4/_sqlite_guard.py      17      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/redactor.py           55      0     14      0   100%
features/celina/celina_pkg/selfimprove/l4/retriever.py          46      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/store.py             178      0     56      0   100%
features/celina/celina_pkg/selfimprove/l5/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l5/critic.py             72      0     22      0   100%
features/celina/celina_pkg/selfimprove/l5/loop.py              110      0     32      0   100%
features/celina/celina_pkg/selfimprove/l5/types.py              59      0     12      0   100%
features/celina/celina_pkg/selfimprove/l6/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l6/_gate_adapter.py      14      0      2      0   100%
features/celina/celina_pkg/selfimprove/l6/_registry_io.py       82      0     24      0   100%
features/celina/celina_pkg/selfimprove/l6/optimizer.py         108      2     26      0    99%   234-235
features/celina/celina_pkg/selfimprove/l6/promoter.py           65      0     22      0   100%
features/celina/celina_pkg/selfimprove/l6/registry.py          134      2     44      1    98%   133-134
features/celina/celina_pkg/selfimprove/l6/types.py             147      0     44      0   100%
features/celina/celina_pkg/selfimprove/ledger_bridge.py         45      0     16      0   100%
--------------------------------------------------------------------------------------------------------
TOTAL                                                         1925      9    514      3    99%
301 passed in 4.21s
```

#### Full pytest, detached worktree

Command: `python3 /tmp/wave3a-offline.py python3 -m pytest -p wave3a_probe tests/ tests/audit/unit/ tests/selfimprove/ -ra --tb=short -o addopts='' -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  7%]
........................................................................ [ 10%]
........................................................................ [ 12%]
........................................................................ [ 15%]
........................................................................ [ 18%]
........................................................................ [ 20%]
...........................................................
  evidence.detect_prompt_injection: 56/56 = 100.0%
.
  router._sanitize_tool_prompt_content: 56/56 = 100.0%
............ [ 23%]
........................................................................ [ 25%]
........................................................................ [ 28%]
........................................................................ [ 31%]
........................................................................ [ 33%]
........................................................................ [ 36%]
........................................................................ [ 38%]
........................................................................ [ 41%]
........................................................................ [ 43%]
........................................................................ [ 46%]
........................................................................ [ 49%]
........................................................................ [ 51%]
........................................................................ [ 54%]
........................................................................ [ 56%]
........................................................................ [ 59%]
........................................................................ [ 62%]
........................................................................ [ 64%]
........................................................................ [ 67%]
...................................................sssss................ [ 69%]
........................................................................ [ 72%]
........................................................................ [ 74%]
........................................................................ [ 77%]
........................................................................ [ 80%]
........................................................................ [ 82%]
........................................................................ [ 85%]
........................................................................ [ 87%]
........................................................................ [ 90%]
........................................................................ [ 93%]
........................................................................ [ 95%]
........................................................................ [ 98%]
..................................................                       [100%]LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0

=========================== short test summary info ============================
SKIPPED [1] tests/test_code_sandbox.py:76: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:88: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:100: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:111: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:123: bwrap not installed — sandboxed execution tests require bwrap
2781 passed, 5 skipped in 42.65s
```

#### Protected and layering guards

Command: `python3 /tmp/wave3a-offline.py python3 -m pytest tests/selfimprove/test_protected_core.py tests/selfimprove/test_layering.py -ra --tb=short -o addopts='' -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
...............................................                          [100%]
47 passed in 0.31s
```

#### Ruff

Command: `python3 /tmp/wave3a-offline.py "$V/bin/ruff" check features/celina/celina_pkg/selfimprove tests/selfimprove` (exit 0)

```text
warning: `incorrect-blank-line-before-class` (D203) and `blank-line-before-class` (D211) are incompatible. Ignoring `incorrect-blank-line-before-class`.
warning: `multi-line-summary-first-line` (D212) and `multi-line-summary-second-line` (D213) are incompatible. Ignoring `multi-line-summary-second-line`.
All checks passed!
```

#### Strict mypy

Command: `python3 /tmp/wave3a-offline.py "$V/bin/mypy" --strict features/celina/celina_pkg/selfimprove` (exit 0)

```text
Success: no issues found in 31 source files
```

#### Bandit

Command: `python3 /tmp/wave3a-offline.py "$V/bin/bandit" -r features/celina/celina_pkg/selfimprove -ll` (exit 0)

```text
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.13.18
Run started:2026-11-25 10:26:19.922495+00:00

Test results:
	No issues identified.

Code scanned:
	Total lines of code: 2920
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 3
		Medium: 0
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 3
Files skipped (0):
```

#### Config validation

Command: `python3 /tmp/wave3a-offline.py python3 tools/validate_configs.py` (exit 0)

```text
All 4 config file(s) passed integrity validation.
```

#### Manifest validation

Command: `python3 /tmp/wave3a-offline.py python3 tools/manifest_integrity.py --verify` (exit 0)

```text
Manifest A: canonical hash OK (5ebf8fb45183b9a1...)
Manifest B: canonical hash OK (5ebf8fb45183b9a1...)
Manifest copies byte-identical (raw sha256=907e3bfea9bddddb...)
Manifest A: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)
Manifest B: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)

Manifest integrity: OK
```

#### AST size audit

The Python AST audit counts decorators through function end lines (exit 0):

```text
MAX FILE (283, 'features/celina/celina_pkg/selfimprove/l4/store.py')
MAX FUNCTION (39, 'features/celina/celina_pkg/selfimprove/l4/retriever.py', 'retrieve')
L6 FILE 1 features/celina/celina_pkg/selfimprove/l6/__init__.py
L6 FILE 44 features/celina/celina_pkg/selfimprove/l6/_gate_adapter.py
L6 FILE 124 features/celina/celina_pkg/selfimprove/l6/_registry_io.py
L6 FILE 252 features/celina/celina_pkg/selfimprove/l6/optimizer.py
L6 FILE 141 features/celina/celina_pkg/selfimprove/l6/promoter.py
L6 FILE 220 features/celina/celina_pkg/selfimprove/l6/registry.py
L6 FILE 279 features/celina/celina_pkg/selfimprove/l6/types.py
FUNCTION (39, 'features/celina/celina_pkg/selfimprove/l4/retriever.py', 'retrieve')
FUNCTION (35, 'features/celina/celina_pkg/selfimprove/l6/optimizer.py', '_candidate')
FUNCTION (35, 'features/celina/celina_pkg/selfimprove/l5/critic.py', 'assess')
FUNCTION (35, 'features/celina/celina_pkg/selfimprove/l2/gate.py', 'verify')
FUNCTION (34, 'features/celina/celina_pkg/selfimprove/l5/loop.py', 'run')
FUNCTION (33, 'features/celina/celina_pkg/selfimprove/l6/optimizer.py', '__init__')
```

The recursive `#nosec` search returned zero matches. `git diff --name-status` for all protected files against `3566746bbf1ff7bdfcb47f8284576f2e678bef2b` returned empty. Worktree status was clean before this documentation change.


## Gate 3 — Wave 4B (`L7`): COMPLETE; all eight layers implemented and verified
This is an offline verification result, not approval for Gate 4 runtime wiring. The final code/test baseline was commit `0b831d7891c4f4409b0697b2267817bca19545e1`. The report commit necessarily follows that baseline.
### Implementation commits

```text
9205cf17b6dbde3d14e500eb0a91846213adee33 test(selfimprove): permit acyclic l7 siblings in layering guard
6cf1e098864982c5349ee879665522dcfbfc1bfc feat(selfimprove): add L7 types and fail-closed Protected Core diff policy
153c511de9f5cee4e9cdf6d9cbce89ca8c1bdb12 feat(selfimprove): add L7 ephemeral detached git worktree isolation manager
6c9f1a852ee7be7423ac93aa82827cc404c4d994 fix(selfimprove): distinguish failed unregistered worktree adds
b904aadee5669f2e2a6b7bdfa3918513832452df fix(selfimprove): clean partial files after failed worktree add
b9cbc30075f9bbac008c3578c393a359cee75d19 feat(selfimprove): add L7 SafeSelfPatcher with L2/L3 gating and approval-bound bundle export
8d9060fe5c4eaf93cbbbe54039e7bc67f8b41034 fix(selfimprove): surface worktree filesystem cleanup failures
4b06a03db7885904ae5bd48bfeb5d044abb5c546 test(selfimprove): exercise L7 failure and branch coverage edges
3deabf0eaad2c63018574783002c91aa39be04d0 fix(selfimprove): reject linked patch targets before worktree creation
0b831d7891c4f4409b0697b2267817bca19545e1 fix(selfimprove): preserve audit digests through privacy redaction
```

### Exact L7 branch coverage

Coverage counts below are **branches**, not the combined statement/branch percentages displayed by coverage.py. The one-line initializer has no executable branches.

| Module | Covered / total branches | Branch coverage |
|---|---:|---:|
| `__init__.py` | 0/0 | N/A (0 branches) |
| `_audit.py` | 8/8 | 100.00% |
| `_bundle_io.py` | 4/4 | 100.00% |
| `_diff_parser.py` | 67/70 | 95.71% |
| `_git_runner.py` | 16/16 | 100.00% |
| `patcher.py` | 25/26 | 96.15% |
| `policy.py` | 14/14 | 100.00% |
| `types.py` | 24/24 | 100.00% |
| `worktree.py` | 39/42 | 92.86% |

Overall selfimprove branch coverage: **708/718 (98.61%)**; combined coverage.py report 99%; 408 tests.

### Trust boundaries and operational caveats

- Git worktrees isolate normal Git-applied edits to a separate directory; they are **not an OS/container security sandbox** and do not make corruption structurally impossible against malicious callbacks, host configuration, concurrent filesystem changes, or a compromised Git executable. The host repository and index must be clean and trusted; private parents, Git local configuration, executors, gate/eval factories and the ledger are trusted. Git apply and its check enforce patch application, not process confinement. Symlink checks are not a defense against hostile concurrent TOCTOU races.
- The runner precharges two subprocess units for teardown: one removal and an optional registration query. If Git or filesystem teardown fails, `failed_cleanup`/`WorktreeCleanupError` reports that host intervention may be necessary; cleanup cannot be absolutely guaranteed after OS failure, crash, forced termination, or inaccessible metadata. Pre-registration failures, partial worktree-add failures and wall/subprocess exhaustion have explicit tests.
- Approval is an in-process callback and digest check, **not proof of a human identity** or a replay-resistant authorization system. Before production use, bind `bundle_hash` to a real human approval using single-use, atomically consumed tokens. The patcher never auto-merges or commits the main checkout.
- The `.patch` and `.json` files are each published atomically at 0600 in a trusted 0700 directory, without overwriting existing files. The pair and the ledger are not one atomic transaction; errors trigger best-effort removal, but a crash or filesystem failure may leave partial artifacts or audit events and require host reconciliation. The evidence bridge may redact sensitive identifiers or paths. To preserve opaque digest values through PII redaction, their audit events carry ordered eight-hex-character chunks that can be concatenated.
- Synchronous host callbacks cannot be preempted mid-call. Wall budgets are checked around callbacks and Git commands have explicit timeouts; elapsed work is detected when control returns, not enforced as a hard real-time deadline.
- L6's registry similarly assumes host-controlled private directories, not an OS sandbox. L6 proposal-hash binding is also an in-process check and requires real single-use atomic human-approval tokens in production.
- The actual locked manifest is `selfimprove/protected_hashes.json`; the requested package-local path does not exist but is also hard-protected by policy. The six locked files and actual manifest have no diff against the Wave 4A documentation commit. The manifest validator's pre-existing version-drift warning is not an integrity failure.
- A detached full-suite run initially exposed evidence privacy redaction of a numeric run in a digest (1 failed, 2886 passed, 5 skipped). A deterministic regression test reproduced the issue; commit `0b831d7` fixed the audit representation. The final detached run below is the subsequent green run, not a claim that the earlier run passed.

### Verbatim final gate outputs

All pytest commands used the `/tmp/wave3a-offline.py` network-denying launcher, an explicit temporary `HOME`, no `TARGET_HOME`/`SUDO_USER`, and the temporary verification-tool environment. The final full run used a clean detached worktree, an origin probe, and removed that worktree afterward; the temporary HOME contained no entries afterward. Offline does not itself sandbox arbitrary filesystem access.

#### Selfimprove branch coverage

Command: `pytest tests/selfimprove/ -ra --tb=short -o addopts="" --cov=features/celina/celina_pkg/selfimprove --cov-branch --cov-report=term-missing --cov-report=json:/tmp/wave4b-final-coverage.json -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [ 17%]
........................................................................ [ 35%]
........................................................................ [ 52%]
........................................................................ [ 70%]
........................................................................ [ 88%]
................................................                         [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.13.18-final-0 _______________

Name                                                         Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------------------
features/celina/celina_pkg/selfimprove/__init__.py               0      0      0      0   100%
features/celina/celina_pkg/selfimprove/budget.py                69      0     14      0   100%
features/celina/celina_pkg/selfimprove/contracts.py            125      0     16      0   100%
features/celina/celina_pkg/selfimprove/flags.py                 36      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l2/gate.py               44      0     10      0   100%
features/celina/celina_pkg/selfimprove/l2/runner.py             53      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/verifiers.py          63      0     12      0   100%
features/celina/celina_pkg/selfimprove/l3/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l3/cli.py                59      1     16      1    97%   94
features/celina/celina_pkg/selfimprove/l3/runner.py             55      0     18      0   100%
features/celina/celina_pkg/selfimprove/l3/stats.py              84      0     22      0   100%
features/celina/celina_pkg/selfimprove/l3/store.py             149      4     36      1    97%   80-82, 145
features/celina/celina_pkg/selfimprove/l3/suite.py              56      0     16      0   100%
features/celina/celina_pkg/selfimprove/l4/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l4/_sqlite_guard.py      17      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/redactor.py           55      0     14      0   100%
features/celina/celina_pkg/selfimprove/l4/retriever.py          46      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/store.py             178      0     56      0   100%
features/celina/celina_pkg/selfimprove/l5/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l5/critic.py             72      0     22      0   100%
features/celina/celina_pkg/selfimprove/l5/loop.py              110      0     32      0   100%
features/celina/celina_pkg/selfimprove/l5/types.py              59      0     12      0   100%
features/celina/celina_pkg/selfimprove/l6/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l6/_gate_adapter.py      14      0      2      0   100%
features/celina/celina_pkg/selfimprove/l6/_registry_io.py       82      0     24      0   100%
features/celina/celina_pkg/selfimprove/l6/optimizer.py         108      2     26      0    99%   234-235
features/celina/celina_pkg/selfimprove/l6/promoter.py           65      0     22      0   100%
features/celina/celina_pkg/selfimprove/l6/registry.py          134      2     44      1    98%   133-134
features/celina/celina_pkg/selfimprove/l6/types.py             147      0     44      0   100%
features/celina/celina_pkg/selfimprove/l7/__init__.py            0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l7/_audit.py             17      0      8      0   100%
features/celina/celina_pkg/selfimprove/l7/_bundle_io.py         49      3      4      0    94%   90-92
features/celina/celina_pkg/selfimprove/l7/_diff_parser.py      134      3     70      3    97%   52, 96, 169
features/celina/celina_pkg/selfimprove/l7/_git_runner.py        60      0     16      0   100%
features/celina/celina_pkg/selfimprove/l7/patcher.py           116      5     26      1    96%   135-136, 166-168
features/celina/celina_pkg/selfimprove/l7/policy.py             40      0     14      0   100%
features/celina/celina_pkg/selfimprove/l7/types.py              98      0     24      0   100%
features/celina/celina_pkg/selfimprove/l7/worktree.py          139      2     42      3    97%   54->60, 192->194, 197-198
features/celina/celina_pkg/selfimprove/ledger_bridge.py         45      0     16      0   100%
--------------------------------------------------------------------------------------------------------
TOTAL                                                         2578     22    718     10    99%
Coverage JSON written to file /tmp/wave4b-final-coverage.json
408 passed in 5.90s
```

#### Protected Core and layering

Command: `pytest tests/selfimprove/test_protected_core.py tests/selfimprove/test_layering.py -ra --tb=short -o addopts="" -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
....................................................                     [100%]
52 passed in 0.48s
```

#### Detached offline full suite with origin probe

Command: `pytest -p wave3a_probe tests/ tests/audit/unit/ tests/selfimprove/ -ra --tb=short -o addopts="" -q` (exit 0)

```text
/tmp/selfimprove-tools-venv/lib/python3.13/site-packages/pytest_asyncio/plugin.py:306: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to "function" scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session". Set it to "fixture-scope" to keep the current behavior.

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [  2%]
........................................................................ [  4%]
........................................................................ [  7%]
........................................................................ [  9%]
........................................................................ [ 12%]
........................................................................ [ 14%]
........................................................................ [ 17%]
........................................................................ [ 19%]
...........................................................
  evidence.detect_prompt_injection: 56/56 = 100.0%
.
  router._sanitize_tool_prompt_content: 56/56 = 100.0%
............ [ 22%]
........................................................................ [ 24%]
........................................................................ [ 27%]
........................................................................ [ 29%]
........................................................................ [ 32%]
........................................................................ [ 34%]
........................................................................ [ 37%]
........................................................................ [ 39%]
........................................................................ [ 42%]
........................................................................ [ 44%]
........................................................................ [ 47%]
........................................................................ [ 49%]
........................................................................ [ 52%]
........................................................................ [ 54%]
........................................................................ [ 57%]
........................................................................ [ 59%]
........................................................................ [ 62%]
........................................................................ [ 64%]
........................................................................ [ 67%]
........................................................................ [ 69%]
..............sssss..................................................... [ 72%]
........................................................................ [ 74%]
........................................................................ [ 77%]
........................................................................ [ 79%]
........................................................................ [ 82%]
........................................................................ [ 84%]
........................................................................ [ 87%]
........................................................................ [ 89%]
........................................................................ [ 92%]
........................................................................ [ 94%]
........................................................................ [ 97%]
........................................................................ [ 99%]
.............                                                            [100%]LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0

=========================== short test summary info ============================
SKIPPED [1] tests/test_code_sandbox.py:76: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:88: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:100: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:111: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:123: bwrap not installed — sandboxed execution tests require bwrap
2888 passed, 5 skipped in 43.85s
HOME entries after full suite:
```

#### Ruff

Command: `ruff check features/celina/celina_pkg/selfimprove tests/selfimprove` (exit 0)

```text
warning: `incorrect-blank-line-before-class` (D203) and `blank-line-before-class` (D211) are incompatible. Ignoring `incorrect-blank-line-before-class`.
warning: `multi-line-summary-first-line` (D212) and `multi-line-summary-second-line` (D213) are incompatible. Ignoring `multi-line-summary-second-line`.
All checks passed!
```

#### Strict mypy

Command: `PYTHONPATH=features/celina mypy --strict features/celina/celina_pkg/selfimprove` (exit 0)

```text
Success: no issues found in 40 source files
```

#### Bandit

Command: `bandit -r features/celina/celina_pkg/selfimprove -ll` (exit 0)

```text
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.13.18
Run started:2026-11-25 11:28:09.914948+00:00

Test results:
	No issues identified.

Code scanned:
	Total lines of code: 3901
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 6
		Medium: 0
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 6
Files skipped (0):
```

#### Configuration

Command: `python3 tools/validate_configs.py` (exit 0)

```text
All 4 config file(s) passed integrity validation.
```

#### Manifest

Command: `python3 tools/manifest_integrity.py --verify` (exit 0)

```text
Manifest A: canonical hash OK (5ebf8fb45183b9a1...)
Manifest B: canonical hash OK (5ebf8fb45183b9a1...)
Manifest copies byte-identical (raw sha256=907e3bfea9bddddb...)
Manifest A: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)
Manifest B: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)

Manifest integrity: OK
```

#### AST sizes and nosec scan

Command: Python AST scan (decorators included), followed by recursive #nosec search (exit 0)

```text
Max file: (283, 'features/celina/celina_pkg/selfimprove/l4/store.py')
Max function including decorators: (39, 'features/celina/celina_pkg/selfimprove/l4/retriever.py', 'retrieve', 70)
Total Python files: 40
Oversize files: []
Oversize functions: []
L7 file: 1 features/celina/celina_pkg/selfimprove/l7/__init__.py
L7 file: 54 features/celina/celina_pkg/selfimprove/l7/_audit.py
L7 file: 88 features/celina/celina_pkg/selfimprove/l7/policy.py
L7 file: 99 features/celina/celina_pkg/selfimprove/l7/_bundle_io.py
L7 file: 132 features/celina/celina_pkg/selfimprove/l7/_git_runner.py
L7 file: 176 features/celina/celina_pkg/selfimprove/l7/_diff_parser.py
L7 file: 190 features/celina/celina_pkg/selfimprove/l7/patcher.py
L7 file: 209 features/celina/celina_pkg/selfimprove/l7/worktree.py
L7 file: 220 features/celina/celina_pkg/selfimprove/l7/types.py
NOSEC occurrence count: 0
```

#### Final L7 and Protected Core SHA-256

Command: `sha256sum` on final L7 modules and locked files (exit 0)

```text
fd61fddaab8e8625d2aa338e7ed623584d4a84e49c5db40249047f55df91ac74  features/celina/celina_pkg/selfimprove/l7/__init__.py
7de9a920aef500da411a97775bd22c2a947b654d924d5a3d6c1e63192aeb39c9  features/celina/celina_pkg/selfimprove/l7/_audit.py
9cf45f2531ebea15db26750059da67427a1144fc6a1d541223a862267c711149  features/celina/celina_pkg/selfimprove/l7/_bundle_io.py
5e9f9f36c422d45c3c8ec2d5c69f673af59c3fc19156598e08af8cc3113ea1a3  features/celina/celina_pkg/selfimprove/l7/_diff_parser.py
7dfcafd1c7405c180e12a1679dd0b3fc50c3875df277ba32965ccb8479d785bd  features/celina/celina_pkg/selfimprove/l7/_git_runner.py
1bd4bfa9c447b71bcf9fee29b162aea1200c249c4b522a3c201244574a4a9541  features/celina/celina_pkg/selfimprove/l7/patcher.py
1bec1fcaef7f2f764a038b5a0acf4b32d6e8767f6054d57ba260392e3de5b9f6  features/celina/celina_pkg/selfimprove/l7/policy.py
9c03a5ef8c7c55286bb6adcbb44b714d739a650f4fd2fcc2e0a55ec8a5679ebe  features/celina/celina_pkg/selfimprove/l7/types.py
7deb9541a71da72b3a87a05cc120cb4b7ee483e09fafda51080d7c6600d1e895  features/celina/celina_pkg/selfimprove/l7/worktree.py
74fba8361ac53d1dfcf6ae5d0804027b9711cba3ef11fadefb42378c32da8459  selfimprove/protected_hashes.json
e029e14e7f7af3cb2fde6f23876400f0306c82ee42b686843420554e9a7224f2  features/celina/celina_pkg/approval.py
11e9747977349e31cbad219eadad10a558de147c05b273eaa4c297d847900a66  features/celina/celina_pkg/evidence.py
f22025c0993d399a543c5fc87440efa83c4cfd5237c6c300a9361e47ad50ab2f  features/celina/celina_pkg/net_policy.py
1ae8540a951dbff682b4352c757abc2e0ec091bd5fa2e80d787a87f0fff07b22  features/celina/celina_pkg/landlock_policy.py
bd5e327dc12a113292054919d4627bdb06d3592b1dd4558ac66e2ca777996727  features/celina/celina_pkg/config.py
ab669c1f41009e4d68b705ef63901d92cb141b32ccb2570e6654ad8f917cf19f  features/celina/celina_pkg/connectors/base.py
```

`git status --short` returned empty immediately before this report edit; protected-file `git diff --name-status` against `aa06800119e82f7e883d21f4c877eecd7b4d279d` was also empty. Gate 4 runtime wiring was not started.


## Gate 4 — Runtime Wiring COMPLETE

### Commits and scope

- Evaluator: `b69eb0f26f640d451137937ba6f93bf673e42d00`.
- Facade, CLI, and L6/L7 replay E2E: `607bb9a3328af050ab8caa36a6175ca6e674a4a6`.
- This report is committed separately under `docs(selfimprove): record Gate 4 runtime wiring completion in selfimprove-progress.md`. The report cannot contain its own Git commit hash without changing that hash.
- Protected Core content was unchanged from `b4a82bf`; the protected-path Git diff was empty. No release tag or baseline defect fix was created.
- `wiring/__init__.py` is docstring-only. Maximum selfimprove file: `l4/store.py`, **283 lines**. Maximum function, including decorators: `l4/retriever.py::retrieve`, **39 lines** (AST source ranges).

### Locked offline baseline

| Category | Passes | Finding |
|---|---:|---|
| Routing | 0/5 | All five sampled offline router calls selected `get_system_stats`. |
| Security refusal | 4/6 | Approved workspace traversal created an outside file; the detector missed “ignore the previous instructions”. |
| Tool arguments | 4/5 | An undeclared argument was accepted. |
| Config integrity | 3/4 | **`config_integrity-18` failed**: the manifest loader accepted a duplicate identifier. |
| L2 repair decisions | 4/4 | Real L2 gate behavior checked using **simulated verifier outputs**; these are not real repair or test executions. |
| **Total** | **15/24 (62.5%)** | Honest observations, not a synthetic all-pass baseline. |

Injection detection and sanitization are component observations; they do **not** establish that a downstream model refuses disclosure. The L3 store contains the per-task pass/fail reports; the CLI also returns the separate diagnostic observations. The synthetic repair recheck uses an explicitly enabled L2 fixture gate only for the benchmark simulation; it does not enable host verifiers or verify an actual patch.

### Integration and trust boundaries

- Fresh subprocesses with unset or zero master switch left both `HOME` and the supplied state path empty and did not import Celina evidence, router, tools, or approval. Active commands without explicit `--state-dir` fail closed. The active `eval` CLI works with master plus eval-harness flags and isolates both `TARGET_HOME` and `CELINA_STORAGE_DIR` from a separate host `HOME`; its 24-task run verified the EvalStore chain and EvidenceLedger chain.
- The L6 E2E runs a host-configured optimizer (using deterministic test evaluators, not a claim that the five defects were repaired), promotes v2 with a genuine argument-bound Celina token, rolls back to v1, and rejects a reconstructed token through a fresh durable approval adapter.
- The L7 E2E instruments Git execution: a protected diff was rejected with zero Git calls; an unprotected diff was applied only in a detached disposable worktree and checked by a real L2 gate with a host test verifier. A genuine bundle-hash-bound token authorized a 0600 patch and metadata in a 0700 directory; main HEAD/index/content remained unchanged, and reconstructed token replay was rejected.
- The standalone `optimize` and `patch` CLI commands deliberately fail closed because they cannot safely invent trusted generator, verifier, Git, or human-approval collaborators. Those full operations are available through the host-configured `SelfImproveRuntime` API and were exercised there. `status` and `eval` are directly usable from the CLI.
- Linux seccomp in the evaluator worker denies ordinary network creation/connection/transmission syscalls and io_uring setup; it is **not a general OS sandbox**. The external test launcher denies network-family socket creation and permits AF_UNIX; neither excludes all inherited-descriptor or local-proxy scenarios. L6/L7 host callbacks and Git/worktree configuration are trusted; worktrees are not OS sandboxes and arbitrary callbacks are not preempted.
- Durable token consumption relies on trusted human token issuance, an intact private shared SQLite spend directory, and local POSIX file/locking semantics. Mutable in-process Celina tokens are not cryptographically signed attestations. These guarantees must not be assumed on NFS or if an attacker can replace or roll back the store.
- EvalStore checkpoint and EvidenceLedger anchors are local trust anchors, not protection against coordinated replacement or rollback of all associated files. Bundle files are individually atomically published, not one transaction with the ledger.
- Detached origin probe inspected loaded Celina module files at collection, teardown, and session end, not all transient imports or child interpreters. It observed 207 origins with none outside the worktree. Five sandbox tests were skipped because bwrap is unavailable; sandboxed execution was not verified.
- The first detached run failed because the origin-probe implementation itself called `Path.resolve` while a legacy test monkeypatched it. That invalid run was not counted as green. The probe was corrected to avoid that interaction, and the successful detached run below is the rerun. Both temporary worktrees were removed.

### Wiring branch coverage

The table reports **branch** coverage, not combined statement/branch coverage. In particular `_eval_security.py` has 14/14 branches but 75% combined coverage in the parent process because child-executed statements were not attributed to it. No universal 95% statement-coverage claim is made.

| Module | Covered / total branches | Branch coverage |
|---|---:|---:|
| `__init__.py` | 0/0 | N/A (no branches) |
| `_eval_arguments.py` | 8/8 | 100.00% |
| `_eval_config.py` | 6/6 | 100.00% |
| `_eval_offline.py` | 12/12 | 100.00% |
| `_eval_probes.py` | 4/4 | 100.00% |
| `_eval_repairs.py` | 2/2 | 100.00% |
| `_eval_routing.py` | 4/4 | 100.00% |
| `_eval_security.py` | 14/14 | 100.00% |
| `_eval_worker.py` | 18/18 | 100.00% |
| `_runtime_eval.py` | 8/8 | 100.00% |
| `_runtime_verify.py` | 2/2 | 100.00% |
| `_token_store.py` | 14/14 | 100.00% |
| `approval_adapter.py` | 4/4 | 100.00% |
| `cli.py` | 12/12 | 100.00% |
| `eval_adapter.py` | 24/24 | 100.00% |
| `facade.py` | 22/22 | 100.00% |

### Verbatim gate outputs

All commands exited 0. Pytest and the other gates were launched under the network-family socket filter, with an empty temporary `HOME`, `TARGET_HOME` and `SUDO_USER` unset, and the pre-existing temporary audit-tool environment. Detached execution used a fresh temporary Git worktree at commit `607bb9a`; its worktree and HOME were removed afterward. `HOME_ENTRIES=0` was reported for every gate.

#### Selfimprove suite with branch coverage

Command: `pytest -q --cov=features/celina/celina_pkg/selfimprove --cov-branch --cov-report=term-missing tests/selfimprove` (exit 0)

```text
........................................................................ [ 14%]
........................................................................ [ 28%]
........................................................................ [ 42%]
........................................................................ [ 57%]
........................................................................ [ 71%]
........................................................................ [ 85%]
........................................................................ [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.13.18-final-0 _______________

Name                                                                Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------------------------------------
features/celina/celina_pkg/selfimprove/__init__.py                      0      0      0      0   100%
features/celina/celina_pkg/selfimprove/budget.py                       69      0     14      0   100%
features/celina/celina_pkg/selfimprove/contracts.py                   125      0     16      0   100%
features/celina/celina_pkg/selfimprove/flags.py                        36      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/__init__.py                   0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l2/gate.py                      44      0     10      0   100%
features/celina/celina_pkg/selfimprove/l2/runner.py                    53      0     12      0   100%
features/celina/celina_pkg/selfimprove/l2/verifiers.py                 63      0     12      0   100%
features/celina/celina_pkg/selfimprove/l3/__init__.py                   0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l3/cli.py                       59      1     16      1    97%   94
features/celina/celina_pkg/selfimprove/l3/runner.py                    55      0     18      0   100%
features/celina/celina_pkg/selfimprove/l3/stats.py                     84      0     22      0   100%
features/celina/celina_pkg/selfimprove/l3/store.py                    149      4     36      1    97%   80-82, 145
features/celina/celina_pkg/selfimprove/l3/suite.py                     56      0     16      0   100%
features/celina/celina_pkg/selfimprove/l4/__init__.py                   0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l4/_sqlite_guard.py             17      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/redactor.py                  55      0     14      0   100%
features/celina/celina_pkg/selfimprove/l4/retriever.py                 46      0      8      0   100%
features/celina/celina_pkg/selfimprove/l4/store.py                    178      0     56      0   100%
features/celina/celina_pkg/selfimprove/l5/__init__.py                   0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l5/critic.py                    72      0     22      0   100%
features/celina/celina_pkg/selfimprove/l5/loop.py                     110      0     32      0   100%
features/celina/celina_pkg/selfimprove/l5/types.py                     59      0     12      0   100%
features/celina/celina_pkg/selfimprove/l6/__init__.py                   0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l6/_gate_adapter.py             14      0      2      0   100%
features/celina/celina_pkg/selfimprove/l6/_registry_io.py              82      0     24      0   100%
features/celina/celina_pkg/selfimprove/l6/optimizer.py                108      2     26      0    99%   234-235
features/celina/celina_pkg/selfimprove/l6/promoter.py                  65      0     22      0   100%
features/celina/celina_pkg/selfimprove/l6/registry.py                 134      2     44      1    98%   133-134
features/celina/celina_pkg/selfimprove/l6/types.py                    147      0     44      0   100%
features/celina/celina_pkg/selfimprove/l7/__init__.py                   0      0      0      0   100%
features/celina/celina_pkg/selfimprove/l7/_audit.py                    17      0      8      0   100%
features/celina/celina_pkg/selfimprove/l7/_bundle_io.py                49      3      4      0    94%   90-92
features/celina/celina_pkg/selfimprove/l7/_diff_parser.py             134      3     70      3    97%   52, 96, 169
features/celina/celina_pkg/selfimprove/l7/_git_runner.py               60      0     16      0   100%
features/celina/celina_pkg/selfimprove/l7/patcher.py                  116      5     26      1    96%   135-136, 166-168
features/celina/celina_pkg/selfimprove/l7/policy.py                    40      0     14      0   100%
features/celina/celina_pkg/selfimprove/l7/types.py                     98      0     24      0   100%
features/celina/celina_pkg/selfimprove/l7/worktree.py                 139      2     42      3    97%   54->60, 192->194, 197-198
features/celina/celina_pkg/selfimprove/ledger_bridge.py                45      0     16      0   100%
features/celina/celina_pkg/selfimprove/wiring/__init__.py               0      0      0      0   100%
features/celina/celina_pkg/selfimprove/wiring/_eval_arguments.py       37      0      8      0   100%
features/celina/celina_pkg/selfimprove/wiring/_eval_config.py          64      0      6      0   100%
features/celina/celina_pkg/selfimprove/wiring/_eval_offline.py         42      0     12      0   100%
features/celina/celina_pkg/selfimprove/wiring/_eval_probes.py          25      0      4      0   100%
features/celina/celina_pkg/selfimprove/wiring/_eval_repairs.py         28      0      2      0   100%
features/celina/celina_pkg/selfimprove/wiring/_eval_routing.py         22      0      4      0   100%
features/celina/celina_pkg/selfimprove/wiring/_eval_security.py       102     29     14      0    75%   29, 37, 41-42, 71-85, 89-97, 112-128
features/celina/celina_pkg/selfimprove/wiring/_eval_worker.py          69      0     18      0   100%
features/celina/celina_pkg/selfimprove/wiring/_runtime_eval.py         53      0      8      0   100%
features/celina/celina_pkg/selfimprove/wiring/_runtime_verify.py       17      0      2      0   100%
features/celina/celina_pkg/selfimprove/wiring/_token_store.py          63      0     14      0   100%
features/celina/celina_pkg/selfimprove/wiring/approval_adapter.py      52      0      4      0   100%
features/celina/celina_pkg/selfimprove/wiring/cli.py                   42      0     12      0   100%
features/celina/celina_pkg/selfimprove/wiring/eval_adapter.py          79      0     24      0   100%
features/celina/celina_pkg/selfimprove/wiring/facade.py                90      0     22      0   100%
---------------------------------------------------------------------------------------------------------------
TOTAL                                                                3363     51    872     10    99%
Coverage JSON written to file /tmp/gate4-final-coverage.json
504 passed in 23.47s
```

#### Protected-core and layering guards

Command: `pytest tests/selfimprove/test_protected_core.py tests/selfimprove/test_layering.py -ra` (exit 0)

```text
============================= test session starts ==============================
platform linux -- Python 3.13.18, pytest-9.0.3, pluggy-1.7.0
rootdir: /home/user/macubuntu
configfile: pytest.ini
plugins: cov-7.1.0, asyncio-1.5.0, platformdirs-4.14.5, anyio-4.16.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 58 items

tests/selfimprove/test_protected_core.py ....                            [  6%]
tests/selfimprove/test_layering.py ..................................... [ 70%]
.................                                                        [100%]

============================== 58 passed in 0.25s ==============================
```

#### Detached offline full suite and origin probe

Command: `pytest -p gate4_origin_probe tests/ tests/audit/unit/ tests/selfimprove/ -ra --tb=short -o addopts="" -q` (exit 0)

```text
........................................................................ [  2%]
........................................................................ [  4%]
........................................................................ [  7%]
........................................................................ [  9%]
........................................................................ [ 12%]
........................................................................ [ 14%]
........................................................................ [ 16%]
........................................................................ [ 19%]
...........................................................
  evidence.detect_prompt_injection: 56/56 = 100.0%
.
  router._sanitize_tool_prompt_content: 56/56 = 100.0%
............ [ 21%]
........................................................................ [ 24%]
........................................................................ [ 26%]
........................................................................ [ 28%]
........................................................................ [ 31%]
........................................................................ [ 33%]
........................................................................ [ 36%]
........................................................................ [ 38%]
........................................................................ [ 40%]
........................................................................ [ 43%]
........................................................................ [ 45%]
........................................................................ [ 48%]
........................................................................ [ 50%]
........................................................................ [ 52%]
........................................................................ [ 55%]
........................................................................ [ 57%]
........................................................................ [ 60%]
........................................................................ [ 62%]
........................................................................ [ 65%]
........................................................................ [ 67%]
........................................................................ [ 69%]
......................................sssss............................. [ 72%]
........................................................................ [ 74%]
........................................................................ [ 77%]
........................................................................ [ 79%]
........................................................................ [ 81%]
........................................................................ [ 84%]
........................................................................ [ 86%]
........................................................................ [ 89%]
........................................................................ [ 91%]
........................................................................ [ 93%]
........................................................................ [ 96%]
........................................................................ [ 98%]
.....................................                                    [100%]LOADED_CELINA_ORIGINS_INSPECTED=207
LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0

=========================== short test summary info ============================
SKIPPED [1] tests/test_code_sandbox.py:76: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:88: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:100: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:111: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:123: bwrap not installed — sandboxed execution tests require bwrap
2984 passed, 5 skipped in 68.48s (0:01:08)
```

#### Ruff

Command: `ruff check features/celina/celina_pkg/selfimprove tests/selfimprove` (exit 0)

```text
warning: `incorrect-blank-line-before-class` (D203) and `blank-line-before-class` (D211) are incompatible. Ignoring `incorrect-blank-line-before-class`.
warning: `multi-line-summary-first-line` (D212) and `multi-line-summary-second-line` (D213) are incompatible. Ignoring `multi-line-summary-second-line`.
All checks passed!
```

#### Strict mypy

Command: `PYTHONPATH=features/celina mypy --strict features/celina/celina_pkg/selfimprove` (exit 0)

```text
Success: no issues found in 56 source files
```

#### Bandit

Command: `bandit -r features/celina/celina_pkg/selfimprove -ll` (exit 0)

```text
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.13.18
Working... ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:00
Run started:2026-11-25 15:43:58.287625+00:00

Test results:
	No issues identified.

Code scanned:
	Total lines of code: 5052
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 13
		Medium: 0
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 13
Files skipped (0):
```

#### Config validation

Command: `python3 tools/validate_configs.py` (exit 0)

```text
All 4 config file(s) passed integrity validation.
```

#### Manifest verification

Command: `python3 tools/manifest_integrity.py --verify` (exit 0)

```text
Manifest A: canonical hash OK (5ebf8fb45183b9a1...)
Manifest B: canonical hash OK (5ebf8fb45183b9a1...)
Manifest copies byte-identical (raw sha256=907e3bfea9bddddb...)
Manifest A: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)
Manifest B: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)

Manifest integrity: OK
```

#### Clean checkout and protected diff before this report

```text
$ git status --short
$ git diff --name-only b4a82bf HEAD -- <locked Protected Core paths>
```

Both produced no output. The detached full run returned `PYTEST_EXIT=0`, `HOME_ENTRIES=0`, and `WORKTREE_REMOVE_EXIT=0`. Bandit found zero medium/high issues and zero `#nosec` directives (13 low-severity findings remain below the requested `-ll` threshold). The manifest verifier reported pre-existing `manifest_version`/`version.toml` drift as review-only.


## Gate 4.5 final checkpoint — acceptance targets not met

User-approved workspace and weather clarifications have been applied. Tested code `5aa4427cbbe51913e13aba4d700dbd65fb547b04`; see [`pre-model-readiness-audit.md`](pre-model-readiness-audit.md#gate-45-final-addendum--verified-not-promotion-ready) for detailed findings and [`gate45-final/`](gate45-final/) for actual EvalStore/EvidenceLedger artifacts, comparison, fixture snapshot, coverage and logs. Protected files and protected hash manifest unchanged since v1; no model integration.

Corrected-fixture matched v1 vs hardened **15/24 → 18/24**, delta 0.125, paired 95% CI [0.0, 0.2916666666666667], not statistically significant, no task/security regression and no approval for promotion. All five routing scenarios still fail; one protected detector variant remains missed. The 23/24 goal and broader high-risk coverage goal remain unmet. Original 15/24 baseline is kept separately; do not mix metrics. L2 outputs are simulated; detector recognition is not evidence of downstream refusal.

Offline detached full suite: **3027 passed, 5 skipped, 41 warnings**; 207 loaded origins inspected, 0 outside at sampling points, HOME empty; worktree removed. Selfimprove: **547 passed**, 871/878 branches, all modules ≥95%; protected/layering 58 passed. Strict mypy, selfimprove Ruff, compileall and core Bandit medium/high checks passed. Full core branch coverage remains 47.70%. Local POSIX atomicity and intact trusted state/issuance are assumptions, not NFS or cryptographic guarantees; paired ledger+anchor rollback is not prevented. `TARGET_HOME` and `CELINA_STORAGE_DIR` must both be isolated, and neither a Git worktree nor the Linux network filter is an OS sandbox. Further protected detector work requires explicit narrow unlock and rehash authorization.
