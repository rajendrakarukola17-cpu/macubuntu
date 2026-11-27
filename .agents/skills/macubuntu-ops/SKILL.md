---
name: macubuntu-ops
description: >-
  Operations, testing, security verification gates, and release packaging for macubuntu and Celina.
  Use when the user asks to run verification gates, verify tests, build .deb packages, or check deployment readiness.
---

# macubuntu & Celina Operational Workflows

Runbooks for verifying, testing, and packaging the `macubuntu` system.

## 1. Security & Quality Verification (`verify_all.sh`)
Runs Gates 0 through 7 (manifest integrity, configs, coverage floor, bandit, pip-audit, mypy, ruff):
```bash
cd /home/jtcviza/macubuntu
PATH="$PWD/.venv/bin:$PATH" bash scripts/verify_all.sh
```

## 2. Fast Test Verification
Run core capability, tools, and Gate 4.7 test suites:
```bash
cd /home/jtcviza/macubuntu
.venv/bin/pytest -q tests/test_tools_batch4.py tests/test_native_os_integration.py tests/test_gate47_*.py tests/selfimprove/test_protected_core.py
```

## 3. Debian Package Build (`.deb`)
Build and inspect the verified Debian package:
```bash
cd /home/jtcviza/macubuntu
TMPDEB=$(mktemp -d)
bash tools/build-deb.sh --out "$TMPDEB"
dpkg-deb --info "$TMPDEB"/*.deb
rm -rf "$TMPDEB"
```

## 4. Release Tarball & Bundle Packaging
- Build release archive:
  ```bash
  tools/build-release.sh --version 4.4.0
  ```
- Build zipapp:
  ```bash
  bash build-zipapp.sh
  ```
- Pack `.mubundle`:
  ```bash
  bash scripts/pack-update.sh
  ```

## 5. Doctor & Hardware Checks
```bash
bash tools/doctor.sh
bash tools/verify_perf_stack.sh
bash tools/hardware-release-gate.sh --dry-run
```
