# Gate 3 Wave 1 verification output

All commands ran at code commit `44cac0b` and exited 0. HOME was a new temporary directory; PYTHONPATH included `features/celina` and the temporary audit-tools site-packages. `pytest` was a wrapper for system Python (`python3 -m pytest`), avoiding the sandbox subprocess issue encountered with the virtualenv interpreter. The repository `pytest.ini` supplies `-q`.

Each block below is unmodified combined stdout/stderr from its command; an empty status block means clean. The later documentation-only commit does not change code or test inputs.

## `pytest tests/selfimprove/ --cov=features/celina/celina_pkg/selfimprove --cov-branch --cov-report=term-missing -ra`

```text
........................................................................ [ 91%]
.......                                                                  [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.13.18-final-0 _______________

Name                                                  Stmts   Miss Branch BrPart  Cover   Missing
-------------------------------------------------------------------------------------------------
features/celina/celina_pkg/selfimprove/__init__.py        0      0      0      0   100%
features/celina/celina_pkg/selfimprove/budget.py         69      0     14      0   100%
features/celina/celina_pkg/selfimprove/contracts.py     125      0     16      0   100%
features/celina/celina_pkg/selfimprove/flags.py          36      0     12      0   100%
-------------------------------------------------------------------------------------------------
TOTAL                                                   230      0     42      0   100%
79 passed in 2.13s
```

## `pytest tests/ tests/audit/unit/ tests/selfimprove/ -ra --tb=short`

```text
........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 11%]
........................................................................ [ 14%]
........................................................................ [ 16%]
........................................................................ [ 19%]
........................................................................ [ 22%]
......................................................
  evidence.detect_prompt_injection: 56/56 = 100.0%
.
  router._sanitize_tool_prompt_content: 56/56 = 100.0%
................. [ 25%]
........................................................................ [ 28%]
........................................................................ [ 30%]
........................................................................ [ 33%]
........................................................................ [ 36%]
........................................................................ [ 39%]
........................................................................ [ 42%]
........................................................................ [ 45%]
........................................................................ [ 47%]
........................................................................ [ 50%]
........................................................................ [ 53%]
........................................................................ [ 56%]
........................................................................ [ 59%]
........................................................................ [ 61%]
........................................................................ [ 64%]
........................................sssss........................... [ 67%]
........................................................................ [ 70%]
........................................................................ [ 73%]
........................................................................ [ 75%]
........................................................................ [ 78%]
........................................................................ [ 81%]
........................................................................ [ 84%]
........................................................................ [ 87%]
........................................................................ [ 90%]
........................................................................ [ 92%]
........................................................................ [ 95%]
........................................................................ [ 98%]
.......................................                                  [100%]
=========================== short test summary info ============================
SKIPPED [1] tests/test_code_sandbox.py:76: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:88: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:100: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:111: bwrap not installed — sandboxed execution tests require bwrap
SKIPPED [1] tests/test_code_sandbox.py:123: bwrap not installed — sandboxed execution tests require bwrap
2554 passed, 5 skipped in 46.28s
```

## `ruff check features/celina/celina_pkg/selfimprove tests/selfimprove`

```text
warning: `incorrect-blank-line-before-class` (D203) and `blank-line-before-class` (D211) are incompatible. Ignoring `incorrect-blank-line-before-class`.
warning: `multi-line-summary-first-line` (D212) and `multi-line-summary-second-line` (D213) are incompatible. Ignoring `multi-line-summary-second-line`.
All checks passed!
```

## `mypy --strict features/celina/celina_pkg/selfimprove`

```text
Success: no issues found in 4 source files
```

## `bandit -r features/celina/celina_pkg/selfimprove -ll`

```text
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.13.18
Run started:2026-11-25 00:47:59.576340+00:00

Test results:
	No issues identified.

Code scanned:
	Total lines of code: 310
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 0
Files skipped (0):
```

## `python3 tools/validate_configs.py`

```text
All 4 config file(s) passed integrity validation.
```

## `python3 tools/manifest_integrity.py --verify`

```text
Manifest A: canonical hash OK (5ebf8fb45183b9a1...)
Manifest B: canonical hash OK (5ebf8fb45183b9a1...)
Manifest copies byte-identical (raw sha256=907e3bfea9bddddb...)
Manifest A: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)
Manifest B: manifest_version=1.0 != version.toml version=4.4 (version drift — review, not an integrity violation)

Manifest integrity: OK
```

## `git status --short`

```text

```
