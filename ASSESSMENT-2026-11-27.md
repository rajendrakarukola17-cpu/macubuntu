# macubuntu Public-Repo Assessment — Does It Deliver What the Docs Describe?

**Date:** 2026-11-27 · **Branch:** `arena/267fe244-macubuntu` · **Commit assessed:** `6d4d9ae9908d5b1bc059a59e3e5254d6124ef653` (= `origin/master` HEAD, fetched via `git ls-remote`)

**Method:** code inspection + committed evidence first; only safe isolated tests run (disposable `$HOME`, no network, no services, no installs, no protected-file changes). Nothing committed or pushed. This document is a new untracked file.

---

## 1. Repo identity & working-tree state

| Fact | Evidence | Observed |
|---|---|---|
| Public commit | `git ls-remote origin` + `gh api .../git/ref/heads/master` | `6d4d9ae…` both sides, dated 2026-11-27 |
| Local HEAD | `git rev-parse HEAD` | `6d4d9ae…` — branch points at the public commit (no local commits) |
| Working tree | `git status --porcelain` | **13 modified + 3 untracked** — the batch-1/2/3 fixes (code_sandbox.py, os_events.py, web-watch timer/service, test files, README/feature-doc wording edits) and this assessment file. All are **uncommitted local fixes**, not on GitHub. |
| Stash | `git stash list` | empty |
| CI for this exact commit | `gh api .../actions/runs?head_sha=6d4d9ae…` → run `45991393178`; check-runs | **success** — single job "full security, reliability, dry-run, rollback & hardware test suite": lint, mypy --strict, bandit HIGH, pip-audit, detect-secrets, manifest SHA-256 recompute, 7-way tool parity, bash -n + ShellCheck, py_compile, JSON schemas, zero-unpinned-remotes, pin-age ≤180 d, pytest + coverage floors, zero-side-effect dry-run, GNOME-extension/eBPF/.deb checks. This is **this repo's** CI result (the 282-failure figure belongs to the other workspace and is not cited here). |

**Distinguish:** committed GitHub code = `6d4d9ae` (CI-green). Uncommitted local = batch-1 functional fixes (sandbox tempdir, App Nap thaw, web-watch `ConditionACPower` move, new tests) + batch-2/3 doc/comment wording + 2 new test files + untracked `batch1-2-full.diff` (scratch) + these assessment files.

---

## 2. Honest feature table

Legend — **Status:** ✅ implemented & CI-verified · 🟡 implemented, unverified here · ⚠️ approximation/claim-only · ❌ disabled/incomplete · 🔌 external-service dependent.
**Evidence** = what was actually inspected/observed; **Remaining work** = what it would take to close the gap.

### A. Installation & desktop

| Feature | Evidence | Status | Dependencies | Remaining work |
|---|---|---|---|---|
| Installer stage pipeline (19 stages 00→99) | `install.sh:242` runs `ALL_STAGES=("…"/stages/*.sh)` glob; `:267` chmods; `99-verify` execs last; stages 00–99 present | ✅ wired | bash, sudo for stages that use it | none structurally; full run never performed here (forbidden) |
| Ubuntu-version gate | `stages/00-preflight.sh:297` — mismatch is **NOTICE-ONLY** ("continuing safely on $UBUNTU_VER"); no hard abort | 🟡 warn-only | Ubuntu ≥24.04 assumed by package names | decide: hard-fail vs documented soft-support; test on 26.04/24.04 |
| Profiles (default-8gb / macos-only / power-user) | `install.sh:99,:201-205` allowlist + validation; `profiles/*.env` present | ✅ wired | — | — |
| Hardware-gated stages | 32 (low-latency kernel: opt-in flag, MOK re-enroll `record_manual`), 49 (mitigations-off: opt-in, GRUB), 50 (battery threshold: needs `/sys/class/power_supply/BAT*/charge_control_end_threshold`, else skipped safely), 55 (TPM2+LUKS, fprintd/FIDO2 only on opt-in/hardware detect), 46/50 zram/hibernate (swap+LUKS guards) | ✅ fail-safe gating | specific hardware/firmware; Secure Boot DKMS | live validation on real laptops; Secure Boot MOK path |
| Sudo/privilege use | stage sudo counts: 32×12, 35×40, 40×9, 45×10, 50×9, 61×9 (grep) | ✅ documented pattern | root during install | privilege-dropping tests pass (8/8 observed here) |
| GNOME Center: 16 pages | `apps/macubuntu-center/main.py:414-429` registers overview/updates/install/finish/models/apikeys/access/reset/performance/appearance/continuity/recovery/knowledge/voice/agents/security | ✅ wired | GTK4, gnome-shell | real-GUI smoke (needs desktop session) |
| Center install/services control | `installer.py` (927 lines): `WIPE_SERVICES`, `systemctl --user` at :617/:751, `svc_app_nap` gate :605 | 🟡 | systemd --user session | live install/uninstall rehearsal in VM |
| **Apple continuity claims** (AirDrop/AirPlay/Sidecar/Universal Control/iPhone mirroring) | README.md:9-29 maps each to open-source substitute (LocalSend, Shairport-Sync+UxPlay, GSConnect+scrcpy, Deskflow, gnome-remote-desktop, Weylus…); stage 40 installs/configures them; LIMITATIONS.md:88 + ARCHITECTURE.md:38 already declare ceilings (Secure Enclave/ECID, iMessage needs Mac/VM, AWDL, AirPlay fragility) | ⚠️ **approximations by design** — substitutes, not Apple protocols | Android/iPhone/Windows peers on LAN; UxPlay may be source-built (APT-miss `record_manual`); Secure Boot for v4l2loopback; mDNS conflicts handled via resolved drop-in | end-to-end cross-device test on real phone/laptop; docs already honest — keep it that way |
| "macOS-grade / Siri-like" framing | README.md:3 uses "macOS-grade desktop", "Siri / Apple Intelligence subsystem" | ⚠️ marketing wording, not equivalence claim | — | keep LIMITATIONS cross-references; never state equivalence |
| Pre-flight / dry-run safety | CI runs zero-side-effect dry-run across default-8gb/macos-only/power-user; stage `DRY_RUN=1` branches observed in 40/49/50/55 | ✅ CI-verified | — | — |

### B. Celina & the 106 tools

| Feature | Evidence | Status | Dependencies | Remaining work |
|---|---|---|---|---|
| 106-tool declaration | `neural-schema.json` tool_domains: 108 entries → **106 unique** (run_shell_command, run_python_sandbox dual-domain); T1=64, T2=42; 11 domains; risk map 107 (=106+`artemis_run_task`); capability manifest == schema (CI 7-way parity gate green) | ✅ counts verified | — | tighten CI subset-check to exact `{artemis_run_task}` delta (proposal) |
| Handler wiring | `_AUTO_TOOL_MAP` 64 + `_APPROVAL_TOOL_MAP` 40 + `{run_skill, autonomous_gui_task}` = 106 — zero diff vs schema | ✅ | — | — |
| **Actual dispatch gate (`execute_tool`)** | tools.py:5460-5481: denies T2, non-T1, `_APPROVAL_TOOL_MAP` members, run_skill/autonomous_gui_task, or `risk != READ_ONLY` → `"Denied: this tool requires a trusted approval broker, which is not available."` **Only 32/106 tools pass** (T1 ∩ READ_ONLY). Confirmed present in public commit (`git show HEAD`). | ❌ **fail-closed by design; broker never wired** | trusted approval broker (does not exist in production) | decide: wire the broker, or re-scope docs to "32 read-only tools active, rest gated" |
| Approval broker code + tests | `approval_broker.py`, `approval_transport.py`, `broker_host.py`; **observed:** broker_core 16 passed, approval_transport 7 passed, broker_host 2 passed (disposable HOME) | 🟡 unit-tested, **zero production callers** — sole `ApprovalBroker()` instantiation is broker_host.py:46; BrokerHost has no production caller; router/island/voice/mcp_server never construct it | — | wire or explicitly document as "reserved, not active" |
| Fast intents | `_trusted_fast_intent_dispatch_available()` tools.py:5615 **hard-returns False**; module docstring :1-5 admits fast-intent/direct paths "have not been fully migrated or isolated"; 65 tests `xfail(strict)` with "handle_fast_intent disables direct actuation without a broker" | ❌ disabled (intentional) | broker | same broker decision |
| Direct paths **around** the gate | (1) `router.py:3026` `"save last actions as <name>"` → `skills.save_last_actions_as_skill()` — no approval_cb, bypasses execute_tool; (2) `island_ui.py:1296/:1412/:789` call `tools.control_media` / `send_notification` / `live_text_ocr_region` directly; (3) `gear0_*` reflex chain (router:2276+) executes via `_execute_fn` with `dry_run=effective_dry_run`, but `gear0_media_execute` (gear0_media.py:142-159) **ignores its `dry_run` argument** and runs `pactl`/`amixer` subprocesses; (4) `skills.heal_skill_from_last_trace`, `db.set_fact` called directly from router shortcuts | ⚠️ **bypasses observed in code** — not an independent security boundary; island's `_request_approval` callback is passed into `router.ask` but the direct GUI calls above never reach it | — | enumerate every bypass; route through one policy chokepoint (proposal only — no security changes without approval) |
| Skill preservation | `learn_skill` :224 / `save_last_actions_as_skill` :262 / `update_skill` :324 take **no approval_cb**; classified T1 (LOCAL_MUTATION ≠ READ_ONLY → centrally they'd hit the gate); `run_skill` itself shows upfront plan approval + quarantine of untrusted `source_trust` (fail-closed) and child executors re-enter `execute_tool` | 🟡 logic present, unverified live; router heal/save shortcuts bypass | skill store SQLite | decide policy: which skill mutations require broker vs local consent |
| Routing / memory / skills / voice / model wiring | `island_ui._submit_prompt` → `router.ask(...)` (:1575) with approval_cb+consent_cb+stream_cb; router tiers 0-3 (Gear0 reflex → smolLM → local Ollama fast/heavy → opt-in cloud); `/model` inspects/switches `config.LOCAL_MODEL` (Ollama, `keep_alive` telemetry); memory = SQLite `db.set_fact`; skills = skills.py store+recorder; voice = Piper TTS + faster-whisper STT subprocesses (voice.py, ephemeral) | 🟡 wiring connected in code | Ollama, PipeWire, whisper/piper binaries | live conversation test on real host |
| MCP surface | mcp_server.py:38 `bus or capability_bus.get_capability_bus()`; bus default executor = `tools.execute_tool` (capability_bus.py:81) → **MCP inherits the same fail-closed gate**; UID peer check :110 | 🟡 gated + UID-checked | D-Bus/socket peers | live MCP client test |
| **Tool classification (106)** | full table: `assessment-tool-inventory-106.md` — **29** tested-working (CI mentions), **3** implemented-unverified, **63** disabled-fail-closed (denied tests asserted in CI), **5** disabled+never-mentioned, **6** disabled-via-gate but with **direct-path-exists** (control_media, send_notification, live_text_ocr, save_last_actions_as_skill, learn/update/forget_skill routes) | see table | — | **do not claim "106 tools work"** — 32 can dispatch, of which 29 are CI-mentioned |
| Untested-by-name tools (11) | find_text_on_screen, forget_skill, list_running_apps, list_skills, press_hotkey, reject_quarantined_skill, save_last_actions_as_skill, scroll_screen, start_recording, stop_recording, update_skill — never appear in tests/ | 🟡/❌ | — | add at least dispatch-level tests |
| Local/offline vs tested-offline | manifest `network_egress`: **85 none / 21 non-none** = *manifest classification, not a verified offline capability count* (wording applied in README.md:15/:125, features/celina/README.md:3/:39); `OPT_IN_TOOLS` = 5 web tools; offline-guard tests exist (OfflineError, router `respond_offline` fallback) but **cannot run here** (py3.11 vs tools.py PEP-701) and **no offline soak was performed on a real host** | 🟡 logic+tests present, offline **operation unverified** | network-isolated machine | run offline suite + manual disconnect test on real Ubuntu host |

### C. Security (approval boundary & trust)

| Feature | Evidence | Status | Dependencies | Remaining work |
|---|---|---|---|---|
| Production approval wiring | Broker instantiated **only** in broker_host.py:46 (unit-test-only host); no construction in router/automation/island/voice/mcp_server | ❌ **not wired** | — | biggest single gap between docs and code |
| `execute_tool` fail-closed gate | tools.py:5460-5481; public-commit confirmed; 63+ tests assert Denied/Blocked (`test_gate47_tool_dispatch_guard`, 91 xfails encode the supersession) | ✅ as designed (fail-closed) | broker for the allow-side | document "32/106 dispatchable today" prominently |
| In-process `request_bound_approval` booleans | callers: api_actuators, artemis_bridge ×2, automation ×9, calendar_bridge, cli_bridge; repo comments state callback "is not an authenticated broker or a security boundary" | ⚠️ convenience layer only | — | **never present as an independent security boundary** (standing rule) |
| Skill-preservation gaps | see §B rows above | ⚠️ | — | policy decision required |
| Connector public-URL validation | `connectors/base.py` `_http_request` SSRF/DNS guard — **not modified** (git status clean for it); UNVERIFIED.md UNV-1 declares no live-network verification; stub tests = 41 | ✅ preserved here | staging network test | optional live SSRF integration test (out of scope here) |
| Protected-file hashes | `git status` shows **none** of protected_hashes.json / connectors/base.py / approval.py / CODEOWNERS modified; config.py == manifest `bd5e327d` | ✅ untouched | — | keep it that way |
| bwrap sandbox | code_sandbox.py fixes (batch 1) + `test_bash_bwrap_workdir_exists_when_run_command_executes` **passes here** (5/5 known-issue regression observed); but real bwrap coverage needs a bwrap-capable host | 🟡 partial | kernel unprivileged userns | run full code_sandbox suite on real host |
| CI security scans | bandit HIGH, pip-audit, detect-secrets, ShellCheck, mypy --strict — all green on `6d4d9ae` | ✅ | — | — |

### D. Known issues (from prior batches) — current status

| Issue | Status now | Evidence | Remaining work |
|---|---|---|---|
| Sandbox temporary-directory (code_sandbox.py:444) | **fixed, uncommitted** | batch-1 diff; regression test passed (5/5 observed here) | commit after approval |
| App Nap thaw (os_events.py pop-on-success) | **fixed, uncommitted** | batch-1 diff; `test_os_event_thaw_recovery.py` 3 passed observed | commit after approval |
| web-watch timer `ConditionACPower` → service `[Unit]` | **fixed, uncommitted** | batch-1 diff; `systemd-analyze verify` exit 0; schedule **preserved** (`OnBootSec=5min`, `OnUnitActiveSec=2h`, `RandomizedDelaySec=5min`, `Persistent=true`) | commit after approval |
| web-watch separate opt-in | **NOT implemented** — still gated by `svc_app_nap` (installer.py:605, default True); `svc_web_watch` doesn't exist | grep | implement per approved proposal (needs explicit approval) |
| web-watch deployment context | **NOT fixed** — timer has **no `Unit=`** (grep count 0), template `@.service` never instantiated, `ExecStart=/usr/bin/python3 /home/%i/macubuntu/…` hardcoded, `WantedBy=multi-user.target`, `Requires=network-online.target` (system target), **zero references** in install.sh unit loop / uninstall.sh / stages | grep | implement per proposal: non-template pair, `After=graphical-session.target`, venv python, install-path substitution, teardown in WIPE_SERVICES+main.py+uninstall.sh, stop running watcher |
| web-watch installation path | **NOT fixed** (same as above) | grep | per proposal |
| Timer's schedule vs App Nap confusion | wording corrected in batch 3; `svc_app_nap` still unrelated-gated (documented mismatch) | README/feature-doc edits | separate opt-in (above) |
| CI status for exact public commit | **success** (run 45991393178) | gh api | — |

### E. Performance & readiness — measured vs claimed

| Claim | Type | Evidence | Status |
|---|---|---|---|
| "18 MB boot RAM", "0% idle CPU", "~45% token cut", "60 fps", "3 ms startup" (README/feature docs) | **claimed** | no measurement artifact found in-repo for these figures | ⚠️ unverified — do not repeat as fact |
| Capability testbook run | **measured, partial** | `docs/audit-reports/full-1000-report.md` (2026-11-18, another machine `/home/jtcviza/…`): 1000 processed, **282 PASS / 682 WARN / 0 FAIL / 36 manual**, avg latency **40 579 ms**, most WARNs = timeouts | ⚠️ real run, but 70.7% warn-rate and machine-specific — not a clean readiness signal |
| Unit/CI test suite | **measured** | CI green on `6d4d9ae` incl. coverage floors, soak tests (FD leak ≤+4, memory growth <4 MB assertions in `test_soak_and_concurrency.py`), 91 intentional xfails | ✅ regression baseline |
| Known-issue regression | **measured here** | 5 passed (sandbox tempdir + thaw + web-watch conditions) with disposable HOME | ✅ |
| Broker unit tests | **measured here** | 16+7+2 passed | ✅ unit-level only |
| Offline operation | **not measured** | — | ❌ needs network-isolated host run |
| Full install on real Ubuntu 26.04 | **not measured** | — | ❌ forbidden here; top host task |
| End-to-end Celina conversation with live tools | **not measured** | py3.11 blocks tools-importing tests in this sandbox | ❌ needs real host (Python ≥3.12) |

**Python-version constraint (observed):** `celina_pkg/tools.py` (and everyday_agents.py:203) use PEP-701 f-strings → collection errors on this sandbox's Python 3.11.2. Repo targets 3.12+; this blocks running any test that imports tools here. CI (3.12+) runs them green. Not a repo defect — an environment limit; recorded so no one misreads collection errors as product failures.

---

## 3. Most important tests still needed on a real Ubuntu host (priority order)

1. **Full `./install.sh` dry-run then real run** on clean Ubuntu 26.04 (and one 24.04 for the notice-only path), all 19 stages + `99-verify`, per profile.
2. **Celina end-to-end on Python 3.12+**: router.ask → tier dispatch → `execute_tool` read-only success + T2 deny message; verify the 32-tool pass set behaves as classified.
3. **Offline soak**: disconnect network, run Celina with local Ollama, assert offline-guard fallback + no non-manifest egress (tcpdump or `ss`), then re-enable.
4. **bwrap sandbox suite** on a host with unprivileged userns (batch-1 tests already pass statically; full suite needs real bwrap).
5. **Web-watch lifecycle** (after opt-in proposal is approved+implemented): install → timer fires 2 h ± 5 min random → service runs once → teardown stops both units; verify no live network requests during tests.
6. **Cross-device continuity smoke** (LocalSend pair, GSConnect clipboard, UxPlay/AirPlay where hardware allows) — validates the "approximation" table against reality.
7. **Broker decision validation**: whatever wiring is chosen, run the 63 fail-closed tests + xfails to confirm no silent allow-side regression.
8. **Performance re-measure**: boot RAM / idle CPU / token counts on the target machine if the README figures are to stay.

---

## 4. Prioritized plan (before any changes — awaiting your approval)

| # | Action | Why | Risk |
|---|---|---|---|
| P1 | **Commit batch-1+2+3** (known-issue fixes + wording) as 1-3 clean commits | they are tested (5/5 regression, systemd-analyze 0) and CI-green-pending | low — but needs your explicit "commit" |
| P2 | **Broker decision**: (a) wire `ApprovalBroker` into router/island/mcp chokepoint, or (b) re-scope docs to "32 read-only tools active; mutating tools intentionally gated" | this is the single biggest docs-vs-code gap; everything in §C hangs on it | (a) = security-relevant, needs design review first |
| P3 | **Enumerate & close direct bypasses** (router skill shortcuts, island direct calls, gear0_media ignoring `dry_run`) — proposal only, no code until approved | user-visible inconsistency: GUI/volume actuation bypasses the gate the LLM path honors | medium — touches live paths |
| P4 | **Implement web-watch opt-in + deployment per the approved 10-point proposal** (separate `svc_web_watch`, non-template units, `Unit=`, teardown, stop-running-watcher) | completes known-issue #3-6 | medium — systemd units, default-off |
| P5 | **Tighten CI parity subset → exact delta `{artemis_run_task}`** (gate/test-only) | stops silent drift past 107 tools | low |
| P6 | **Add dispatch tests for the 11 never-mentioned tools** + soften/qualify unverified perf figures in README | closes "declared but untested" gap; honesty of numbers | low (docs + tests only) |
| P7 | **Host test campaign** (§3 list) | turns 🟡 → ✅ with observed evidence | requires a real Ubuntu machine (your side) |

**Explicit non-claims for this assessment:** no tool was executed live in this environment (Python 3.11 + no-install rule); macOS equivalence, superintelligence, and "106 working tools" are **not** asserted; the broker is **not** a proven security boundary; 85/21 is a manifest classification only; the 282-failure figure belongs to the other workspace.

**Artifacts produced:** this file · `assessment-tool-inventory-106.md` (per-tool table) · `batch1-2-full.diff` (pre-existing scratch diff) — all untracked, nothing committed.
