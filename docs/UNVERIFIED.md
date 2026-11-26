# Unverified Areas — Celina Security Audit (Phase 9 B6)

**Date:** 2026-11-14  
**Tag:** audit-complete-2026-11  
**Scope:** Areas that could not be fully verified during the audit and remain as residual risk.

---

## UNV-1: Network-Dependent SSRF/DNS Validation

**File:** `features/celina/celina_pkg/connectors/base.py:_http_request`  
**Risk Level:** MEDIUM  
**Reason:** Cannot make real network requests in the sandboxed test environment. Stub-based resolver validation is in place (`test_ssrf_dns.py`, 41 tests) but not production-verified against actual DNS rebinding, private IP access, or metadata service endpoints.  
**What Would Verify It:** Integration test with a staging server that attempts SSRF, DNS rebinding, and metadata service access, verifying that `net_policy.py` blocks all such attempts at the resolver and `_http_request` level.  
**Mitigation:** Stub resolver tests cover private/loopback/link-local/metadata/IPv4-mapped IPv6/rebinding cases. No `shell=True`, no `eval()`, no `pickle.loads()` in code paths.

---

## UNV-2: Evidence Ledger Concurrency Under Load

**File:** `features/celina/celina_pkg/evidence.py:EvidenceLedger`  
**Risk Level:** MEDIUM  
**Reason:** Multi-threaded testing not performed under production load. Concurrency tests exist (`test_evidence_concurrency.py`, 7 tests) but don't stress-test with 100+ concurrent writers or verify fail-closed behavior under sustained contention.  
**What Would Verify It:** Stress test with 100+ concurrent writers, verifying that `EvidenceIntegrityError` is raised on any tampering and that the ledger remains fail-closed under load.  
**Mitigation:** Evidence ledger is tamper-evident with anchor verification on open. `reset_anchor` requires `confirm=True`. Thread safety via locking where applicable.

---

## UNV-3: GTK4 GUI Code (`island_ui.py`)

**File:** `features/celina/celina_pkg/island_ui.py`  
**Risk Level:** LOW  
**Reason:** `gi` bindings (PyGObject) not installed in test environment. Import-guarded tests exist (`test_island_ui_guarded.py`, 7 tests) with mocked `gi` bindings, but real GTK4 behavior is not tested.  
**What Would Verify It:** Install `python3-gi` and GTK4 development libraries, then run GUI tests against real bindings.  
**Mitigation:** Import is guarded; module only loads when GTK4 is available. No security-critical code paths in island_ui.py.

---

## UNV-4: macOS-Specific Code

**Files:** `features/celina/celina_pkg/ios_ancs.py`, `features/celina/celina_pkg/macos_glue.py`, `features/celina/celina_pkg/continuity.py`  
**Risk Level:** LOW  
**Reason:** Not testable on Linux test environment. These files handle macOS-specific features (iOS ANCS notifications, macOS glue, Handoff/Continuity).  
**What Would Verify It:** Test on macOS with the appropriate frameworks available.  
**Mitigation:** These are platform-specific modules that only load on macOS. No security-critical cross-platform impact.

---

## UNV-5: Hardware/NPU Backends

**Files:** `features/celina/celina_hardware/` (NPU, OpenVINO, GPU detection, etc.)  
**Risk Level:** MEDIUM  
**Reason:** Requires specific hardware (NPU, OpenVINO, GPU) not available in test environment. Tests in `test_npu_worker_and_os_desktop_mcp.py` (6 tests) are hardware-dependent and skipped/failed in CI.  
**What Would Verify It:** Test on target hardware with NPU/OpenVINO/GPU available.  
**Mitigation:** Hardware-dependent code is isolated behind hardware detection guards. Fail-closed when hardware is unavailable.

---

## UNV-6: End-to-End Autonomy Flows

**Scope:** Full LLM-driven task execution across multiple tools  
**Risk Level:** MEDIUM  
**Reason:** Not tested without LLM API access. Autonomy flows involve LLM planning, tool execution, evidence recording, and approval gating — none of which can be tested without an LLM provider.  
**What Would Verify It:** E2E test with mock LLM that drives a multi-step task through the full autonomy stack, verifying approval gating, evidence recording, and fail-closed behavior at each step.  
**Mitigation:** Unit tests exist for individual components (approval, evidence, net_policy, config). Integration tests verify tool boundaries and approval gating.

---

## UNV-7: Optional Requirements Audit

**File:** `features/celina/requirements-optional.txt`  
**Risk Level:** LOW  
**Reason:** pip-audit blocked by fasttext pybind11 build failure on Python 3.13. Cannot audit optional dependencies for vulnerabilities.  
**What Would Verify It:** Run pip-audit against requirements-optional.txt on a Python version where fasttext builds successfully (Python 3.11 or earlier).  
**Mitigation:** Optional requirements are not installed by default. Core + tier-b requirements are fully audited.

---

## Residual Risk Summary

| ID | Area | Risk | Mitigation Status |
|----|------|------|-------------------|
| UNV-1 | Network SSRF/DNS | MEDIUM | Stub tests in place; production integration needed |
| UNV-2 | Evidence concurrency | MEDIUM | Unit tests in place; stress test needed |
| UNV-3 | GTK4 GUI | LOW | Import-guarded; real bindings needed |
| UNV-4 | macOS code | LOW | Platform-isolated; macOS testing needed |
| UNV-5 | Hardware/NPU | MEDIUM | Hardware guards in place; target hardware needed |
| UNV-6 | E2E autonomy | MEDIUM | Component tests in place; E2E with mock LLM needed |
| UNV-7 | Optional requirements | LOW | Not installed by default; core+tier-b audited |

**None of these unverified areas represent known vulnerabilities.** They represent areas where testing coverage is incomplete due to environmental constraints (no network, no specific hardware, no macOS, no LLM API).
