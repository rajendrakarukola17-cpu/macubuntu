# macubuntu — Hardened Ubuntu 26.04 → macOS OS Ecosystem (with Built-In Celina v4.4)

**One single-source-of-truth, security-hardened project** that transforms **Ubuntu 26.04 LTS (Wayland, 8GB+ RAM)** into a cohesive, deep macOS-grade desktop & cross-platform ecosystem — with **Celina (nickname: Celly)** architected natively inside it as its built-in **Siri / Apple Intelligence** subsystem (`features/celina/`), backed by a structured OS hardware & capability-gated biometric layer (`celina_hardware/`).

---

## macOS Parity & All-System Continuity Architecture

Beyond visual polish, `macubuntu` closes the core **system-level** and **cross-platform continuity** gaps between Ubuntu 26.04 and macOS across **Android, iPhone/iPad/Mac, Windows, and Linux**, using strictly vetted, pinned, open-source projects:

| macOS Architectural Layer | macubuntu Implementation | Vetted Open-Source Stack (Pinned) |
|---|---|---|
| **AirDrop (Universal)** | Cross-OS LAN file & text drop (`iOS/Mac/Android/Win/Linux`) | `LocalSend` (`v1.18.2`, Apache-2.0) |
| **iPhone / iPad Continuity (Full Open-Source Stack)** | AirDrop (`LocalSend`), AirPlay 2 Audio+Video (`Shairport-Sync` + `UxPlay`), BLE ANCS Notifications & Call Accept/Dismiss (`ios_ancs.py`), USB/Wi-Fi Sync & Mount (`libimobiledevice` + `ifuse` → `~/iPhone`), Continuity Camera Webcam (`UxPlay` → `v4l2loopback` `/dev/video10`), KDE Connect iOS ↔ `GSConnect`, & 60 FPS iPad Sidecar + Apple Pencil (`Weylus` / `gnome-remote-desktop`) | `LocalSend`, `Shairport-Sync`, `UxPlay` (`v1.73.7`), `libimobiledevice` + `ifuse`, `v4l2loopback`, `GSConnect` (`v74`), `Weylus` (MIT), `celina_pkg/{continuity,ios_ancs}.py` |
| **Deep Ubuntu 26.04 Wayland OS Hooks & Celina v4.4 Cognition** | **99 tools across 8 domains (`92` offline-capable / `7` web-gated)**: Hybrid Semantic & Embedding Engine (`embeddings.py`: Ollama `embeddinggemma` / `Model2Vec` + SQLite `float16` BLOBs + RRF + Semantic Tool Router + Semantic Cache + Hybrid CoreSpotlight), Ebbinghaus temporal memory decay & KG contradiction resolution, 0-token Gear 0 AST math & compound `prepare_meeting_context`, offline PipeWire loopback meeting transcription (`transcribe_system_audio`), self-verifying `bwrap` Python sandbox (`run_python_sandbox`), zero-polling Systemd Crash Doctor + UDisks2 USB Guard + Freedesktop Secret Service vault lock check + MIME default app manager | `celina_pkg/{embeddings,memory,cache,spotlight_index,app_intents,voice,tools,os_events,island_ui}.py` |
| **iPhone Mirroring & SMS/Notifs** | 60fps USB/Wi-Fi Android screen control + bidirectional notifications, clipboard & SMS | `Genymobile/scrcpy` (`v5.0.1`, Apache-2.0, commit `a60891ae...`) + `GSConnect` (`v74`, GPL-2.0) |
| **Universal Control** | Share mouse, keyboard & clipboard seamlessly across Ubuntu, Windows, macOS & Linux | `Deskflow` (`v1.27.0`, GPL-2.0, commit `081f6478...`) + opt-in `lan-mouse` (`v0.11.0`, GPL-3.0, commit `0d2190e7...`) |
| **iCloud Drive (P2P)** | End-to-end encrypted, zero-cloud cross-OS folder sync | `Syncthing` (`v2.1.7`, MPL-2.0) |
| **Sidecar & Wireless AirScan** | Native Wayland RDP remote display + driverless eSCL/WSD network scanning | `gnome-remote-desktop` + `sane-airscan` |
| **Focus Modes, Auto App Nap & Dynamic Appearance** | Event-driven daemon (`celina-eventd.service`), Focus Modes, `systemd-oomd`, opt-in cgroup v2 `AutoAppNapCoordinator`, & solar Light↔Dark `DynamicThemeScheduler` | `celina_pkg/os_events.py`, `system/systemd/{celina-eventd.service,oomd-macubuntu.conf}` |
| **Live Inline CoreSpotlight & Clipboard History** | Live Rofi script-mode AST math, unit converter, GNOME Settings deep-links, private `0600` FTS5 files (auto-refreshed by `celina-eventd`), & clipboard history | `celina_pkg/spotlight_index.py`, `clipboard-indicator` |
| **Apple Intelligence Writing Tools & Live Text OCR** | Local Ollama proofread/rewrite/tone/summarize (`Super+Shift+W`), interactive region OCR → clipboard (`Super+Shift+T`), & lightweight active-window context | `celina_pkg/writing_tools.py` |
| **Siri Voice Streaming & Native App Intents** | Sentence-streamed Piper TTS (`SentenceStreamSpeaker`), 5s hands-free follow-up mic window, native GNOME Calendar `.ics`, GNOME Contacts vCard/Evolution, & Firefox/Chromium bookmark lookup | `celina_pkg/voice.py`, `celina_pkg/calendar_bridge.py`, `celina_pkg/app_intents.py` |
| **Dynamic Island Live Privacy Dots & Control Center** | Event-driven `Gio.FileMonitor` live 🟠 mic / 🟢 cam / 🟣 screen dots, Focus badge, iOS ANCS alerts, & 1-click Focus/Write/LiveText/80%-Charge/Volume/Brightness/Audio-Sink drawer controls | `celina_pkg/island_ui.py`, `celina_hardware/adapters.py` |
| **Finder Quick Actions & Help Menu** | Right-click Nautilus Quick Actions (*Summarize with Celina*, *Convert to PDF*, *Share via LocalSend*) + live AT-SPI2 active-window menu search | `system/nautilus/celina_quick_actions.py`, `celina_pkg/continuity.py` |
| **Control Center Audio & Privacy Dots** | Per-app PipeWire sink routing + live PipeWire mic 🟠 / camera 🟢 / screen-share 🟣 indicator detection | `celina_hardware/adapters.py` (`PipeWireRoutingAdapter`, `PrivacyIndicatorAdapter`) |
| **TCC Privacy & Keychain** | GUI Flatpak permission inspector, CLI TCC privacy/autostart auditor, & local KeePassXC vault | `Flatseal` (GPL-3.0), `KeePassXC` (`2.8.0`), `audit_app_permissions` |
| **Optimized Battery Charging (80%), Safe Sleep & Silent Boot** | Hardware 80% battery charge-threshold udev rule (`90-battery-charge-limit.rules`), smart macOS Safe Sleep (`HibernateDelaySec=3h` when swap exists / instant-wake suspend guard on pure ZRAM), Intel/AMD VA-API decode, & opt-in flicker-free GRUB boot + active CPU P-State (`--silent-boot`) | `stages/50-power.sh`, `system/systemd/sleep-*.conf`, `system/grub/99-macubuntu-boot.cfg`, `celina_hardware/adapters.py` |
| **CoreAudio / AirPods AAC & Zero-Conflict mDNS** | WirePlumber 0.5+ AirPods AAC/LDAC/aptX-HD/SBC-XQ codec & battery config, PipeWire low-jitter 44.1/48/96 kHz clock, persistent `/dev/video10` `v4l2loopback` Continuity Camera, & `systemd-resolved` `MulticastDNS=no` drop-in eliminating UDP 5353 races with Avahi | `system/wireplumber/`, `system/pipewire/`, `system/modprobe/`, `system/systemd/resolved-mdns.conf`, `stages/40-continuity-audio.sh` |
| **MacBook Trackpad, Btrfs Snapshots & OS Health Telemetry** | `fingers` click-method, two-finger scroll, centered windows, sheet modal dialogs, APFS-style `grub-btrfs` snapshot detection on Btrfs roots, & zero-overhead `os_events.get_os_parity_health()` live telemetry | `stages/30-input-typography.sh`, `stages/45-apps-backups-search.sh`, `celina_pkg/os_events.py` |
| **Touch ID / Face ID & Secure Enclave** | Read-only TPM 2.0/LUKS2 advisor, capability-gated `fprintd`/FIDO2 PAM with mandatory `pam_unix.so` check, & 60 FPS Cairo Apple Face ID / Touch ID Dynamic Island animations driven by live `fprintd` + `ScreenSaver` D-Bus signals | `stages/55-security-biometrics.sh`, `celina_hardware/biometrics.py`, `celina_pkg/biometric_anim.py` |
| **Safari Browser Chrome & Compass Logo** | Authentic macOS Safari UI for Firefox (`userChrome.css` / `userContent.css`: traffic lights, centered pill URL bar, rounded floating tabs, Light/Dark adaptive) + crisp macOS squircle Safari compass SVG icon & Snap/DEB/Flatpak `.desktop` overrides | `system/firefox/{safari-compass.svg,userChrome.css,userContent.css,apply-safari-theme.py}` |
| **Native Wayland Input** | Tier-1 `org.freedesktop.portal.RemoteDesktop` D-Bus client (zero `/dev/uinput`, zero `input` group) + AT-SPI2 + `InputBroker` | `celina_pkg/portal_input.py`, `celina_pkg/automation.py` |

*(See **[`LIMITATIONS.md`](LIMITATIONS.md)** for the plain-language honesty contract on Apple Secure Enclave/ECID ceilings, iMessage Mac/VM requirements, AWDL Wi-Fi chipsets, AirPlay 2 fragility, and disposable Apple ID rules, and **[`ARCHITECTURE.md`](ARCHITECTURE.md)** for the full Tier A vs. Tier B boundary. Optional isolated research tooling lives strictly outside `install.sh` in `./tools/experimental-deep-stack.sh`.)*

---

## Security Hardening Invariants (Phases 1–8)

1. **Supply-Chain & Installer Safety (`core/common.sh`, `system/download-locks.sha256`):**
   - Eval-free, allowlisted profile parser validating keys against `^[A-Z_][A-Z0-9_]*$` and rejecting command substitution (`$(...)`, backticks, `;`, newlines).
   - Every Git repository (including `UxPlay`, `lan-mouse`, `deskflow`, `scrcpy`, themes, and utilities) is pinned to an exact 40-char commit SHA and every remote binary/voice archive (Ollama `v0.40.16` `ollama-linux-amd64.tar.zst`, Piper EN/TE/HI `.onnx` & `.onnx.json` at commit `6b9cfaac`) is verified by exact SHA-256 **before** extraction or installation — zero floating `"latest"` URLs and zero remote `install.sh` execution.
   - Verified zero-side-effect `--dry-run` across all 3 profiles (`default-8gb`, `macos-only`, `power-user`) and `uninstall.sh --dry-run`.
2. **Private Permissions, Privacy & State Correctness (`config.py`, `memory.py`, `cache.py`, `command_runner.py`):**
   - `umask 077`, `0700` private directories, and `0600` private config/SQLite DB/Spotlight FTS5/Focus state/skill/log/allowlist files.
   - Cache firewall refuses clipboard text, biometric data, passwords, secrets/PII, and raw `data:image/` screenshots.
   - Atomic `fsync` + `0600` + `os.replace` identity persistence with full in-memory rollback on `OSError`.
3. **Centralized Network, URL & SSRF Policy (`celina_pkg/net_policy.py`):**
   - Single egress policy enforcing `https://` port `443`, rejecting embedded credentials, resolving DNS upfront to block private/loopback/link-local/cloud metadata (`169.254.169.254`)/IPv4-mapped IPv6/DNS rebinding, revalidating redirects, and requiring explicit host/port/method allowlisting in `~/.config/celina/iot-allowlist.json` (`0600`) for IoT webhooks.
4. **Risk-Tiered Argument-Bound Approvals & MCP Hardening (`celina_pkg/approval.py`, `mcp_client.py`):**
   - Exact SHA-256 `arguments_hash` binding (`tool`, `target`, `arguments_hash`, `risk`, `expires_at`, single-use consumption) across all **99 tools** (`92` offline-capable, `7` web-gated).
   - High-risk actions (shell, destructive files, messaging, webhooks, MCP, raw coordinates, terminal/password typing, skill promotion, Android/AirPlay mirroring) require fresh confirmation.
5. **Skill Quarantine, Capability Sandbox & Scoped Wayland Input (`skills.py`, `tools.py`, `portal_input.py`, `automation.py`, `computer_use.py`):**
   - Community Markdown skills download strictly into `skills/quarantine/` with URL/publisher/SHA-256 metadata, never auto-activate, and require verified promotion (`approve_quarantined_skill`) to `skills/trusted/`.
   - Capability-based Bubblewrap (`bwrap`) shell sandbox isolates `/work`, masks `$HOME`, unshares `net/pid/ipc/uts`, and clears env (`--clearenv`); if `bwrap` is missing, Celina demands an explicit second `[UNSANDBOXED HOST SHELL]` confirmation.
   - Normal users are **never** added to the broad `input` group; Wayland input prioritizes GNOME `RemoteDesktop` Portal → App APIs → AT-SPI2 → scoped `InputBroker` → opt-in `--experimental-uinput`.
6. **Verification, Rollback & Automated CI (`stages/99-verify.sh`, `uninstall.sh`, `tests/`, `.github/workflows/ci.yml`):**
   - 5-state verification classifier (`HEALTHY`, `INSTALLED (INACTIVE)`, `NOT INSTALLED`, `NOT SELECTED`, `FAILED`) that exits nonzero on any required failure.
   - Full rollback utility (`./uninstall.sh --dry-run` / `--purge-data`) cleaning all services, udev rules, `oomd` drop-ins, Nautilus extensions, and optional Tier B research venv/sandbox.
   - **215 automated `pytest` tests** across 24 test suites (including live Linux Landlock ABI 2 kernel enforcement, `systemd-analyze security < 5.0`, fail-closed evidence ledger anchors, `TaskGraph` crash recovery, Gear 0 AST math, Secure Boot MOK, LUMOS semantic blueprints, OS capability manifest & kernel bwrap isolation, operational-trust export/import/undo/migrate/checkpoints/locale, 50W+50R SQLite WAL concurrency & soak) + hard Stage 00 **7-way** 99-tool (`92` offline / `7` web-gated) schema/config/handler/domain/risk/capability parity gate.

---

## Repository Structure

```text
macubuntu/
├── install.sh                         # Fault-tolerant hardened orchestrator (flock guard, --dry-run, --doctor, --locale, --verify, --verify-models, --upgrade, --system-only, --user-only, --replace-swap, --accessibility, --macos-cmd-keys, --silent-boot, --profile, --model, --only, --skip, --from)
├── uninstall.sh                       # Rollback & uninstall utility (--dry-run, --purge-data)
├── pytest.ini                         # Pytest configuration
├── ARCHITECTURE.md                    # Tier A vs. Tier B firewall, ECID/SEP ceilings, 8GB OOM policy & Wayland clipboard notes
├── LIMITATIONS.md                     # Public plain-language honesty contract: hardware/SEP ceilings, Tier B fragility & disposable Apple ID rules
├── COMPATIBILITY.md                   # GPU/VA-API, Secure Boot stage table, Laptop 80% battery sysfs, ZRAM/Hibernate, iOS 16-18 ANCS, AirPlay 2, Voice/Locale & Tier B Wi-Fi matrix
├── CHANGELOG.md                       # Versioned release notes (v4.4.0) & v4.2/v4.3 -> v4.4 idempotent upgrade procedures
├── README.md                          # Master OS & security architecture guide
├── docs/
│   ├── imessage-safe-path.md          # Production-safe BlueBubbles iMessage bridge vs. Tier B research-only guidance
│   └── v5-roadmap-cross-device-actuation.md # Planned v5 Hub-and-Spoke mTLS gRPC cross-device actuation spec
├── .github/workflows/ci.yml           # CI: bash -n, shellcheck, py_compile, JSON check, 7-way 99-tool parity, pytest (24 suites) + soak/concurrency, zero-side-effect dry-run
│
├── tools/                             # Standalone diagnostics, Secure Boot MOK, release packager & opt-in Tier B sandbox
│   ├── doctor.sh                      # Read-only fast-path health check (`./install.sh --doctor`, top 17 failure modes + fixes)
│   ├── enroll-mok.sh                  # UEFI Secure Boot DKMS MOK enrollment & post-reboot verifier for Stage 40 v4l2loopback
│   ├── build-release.sh               # Reproducible macubuntu-v4.4.0.tar.gz + SHA256SUMS release packager
│   ├── experimental-deep-stack.sh     # Isolated 0700 AWDL (owl) / AirDrop (opendrop) / Find My (FindMy.py) sandbox (Apple ID keyring guardrail, digest-pinned Anisette, pinned SHAs)
│   └── requirements-tier-b.txt        # Pinned Tier B Python dependencies (auto-uses --require-hashes when .lock present)
│
├── profiles/                          # Declarative allowlisted machine presets
│   ├── default-8gb.env                # 8GB RAM default: macOS Shell + Celina + Continuity Hub + 80% Charge Limit + ZRAM + 5-pillar smoothness
│   ├── macos-only.env                 # Pure Ubuntu -> macOS + Continuity Hub (Celina disabled)
│   └── power-user.env                 # 16GB+ RAM: Dash-to-Dock + Celina + LocalSearch + Distrobox
│
├── core/                              # Eval-free shared installer engine
│   ├── common.sh                      # Allowlisted profile parser (42 keys), pinned GNOME extension installer, structured JSONL telemetry, umask 077, SHA-256 & pinned git helpers, DRY_RUN wrappers
│   └── detect.sh                      # Ubuntu version, RAM, CPU vendor, root FS (Btrfs/ext4), laptop/hibernate+LUKS+ZRAM swap guard, dual-boot Windows, VA-API vainfo, AMD GPU, Wayland/X11 & PipeWire detection
│
├── system/                            # Declarative OS configs, capability manifest & supply-chain lockfiles
│   ├── download-locks.sha256          # Exact 40-char commit SHAs, 64-char SHA-256 artifacts & Anisette Docker OCI digest (24 pinned entries; 0 floating URLs)
│   ├── capabilities/celina-capabilities.json # Declarative 99-tool OS capability manifest (FS, network, D-Bus, PipeWire, bwrap kernel boundary, 0 credential access)
│   ├── sysctl/99-macubuntu-vm.conf    # Authoritative ZRAM + bounded dirty-bytes + BBR/fq + inotify sysctl profile
│   ├── environment.d/90-macubuntu-smooth.conf # Native Wayland acceleration (Electron/Firefox/Qt/SDL)
│   ├── keyd/                          # Opt-in Wayland terminal-aware macOS Cmd+C/V/Q remapping (macubuntu.conf + app.conf)
│   ├── modprobe/                      # Persistent /dev/video10 v4l2loopback Continuity Camera kernel load/options
│   ├── pipewire/                      # CoreAudio-style low-jitter 44.1/48/96 kHz PipeWire clock config
│   ├── wireplumber/                   # WirePlumber 0.5+ AirPods AAC / LDAC / aptX-HD / SBC-XQ Bluetooth codec config
│   ├── grub/                          # Opt-in flicker-free GRUB boot + active CPU P-State + Windows dual-boot preservation (--silent-boot)
│   ├── zram/                          # zram-generator.conf + zramswap.default
│   ├── systemd/                       # shairport-sync-user, uxplay-user, celina-eventd (OOMScoreAdjust=-300, MemoryMax=384M), oomd-macubuntu, resolved-mdns, sleep-safesleep/instant-suspend, ydotoold (opt-in), ollama-8gb-ram (OOMScoreAdjust=300), ollama-amd-vulkan
│   ├── udev/                          # 60-macubuntu-io-scheduler.rules (NVMe/SSD/HDD) + 90-battery-charge-limit.rules (80% cap)
│   ├── nautilus/celina_quick_actions.py    # Finder-style right-click Quick Actions extension
│   ├── firefox/                       # Safari-style Firefox chrome (GPU userChrome/userContent), Fastfox/Smoothfox/VA-API user.js, compass SVG icon & apply/revert/--disable-vaapi helper
│   └── gnome/                         # extensions.lock (pinned GNOME 48/49/50 version_tags), panel-glass.css, rofi-spotlight.rasi
│
├── stages/                            # 13 focused, idempotent OS pipeline stages (00..99)
│   ├── 00-preflight.sh ... 35-memory-zram.sh  # Stage 00 hard 6-way parity gate + X11/multi-user/AT-SPI2 locale audit (--locale); Stage 35 ZRAM + disk-swap preservation
│   ├── 40-continuity-audio.sh         # All-System Continuity Hub (LocalSend, GSConnect, scrcpy, Shairport, UxPlay, persistent v4l2loopback, Avahi mDNS fix, WirePlumber AAC/LDAC, PipeWire clock, Syncthing, Deskflow, RDP, USB iPhone sync)
│   ├── 45-apps-backups-search.sh      # Launcher, Spacebar Quick Look (gnome-sushi + PDF/HEIC/WebP/video thumbnailers), Btrfs APFS snapshot detection, Flatseal, KeePassXC, opt-in Distrobox, Nautilus Quick Actions, Backups
│   ├── 50-power.sh                    # Power backend (ppd/tlp), 80% battery charge threshold, Safe Sleep + LUKS swap resume guard, opt-in --silent-boot (dual-boot Windows GRUB preserved) & VA-API GPU decode
│   ├── 55-security-biometrics.sh      # Read-only TPM2/LUKS2 advisor & capability-gated Touch ID/FIDO2 PAM
│   ├── 60-extras.sh                   # Opt-in Accessibility stack (--accessibility: Orca screen reader, KMagnifier, AT-SPI2, speech-dispatcher) & pinned extras (--macos-cmd-keys keyd, lan-mouse, Stage Manager, Rust tools)
│   ├── 70-feature-celina.sh           # Built-In Celina AI + celina-export/celina-import/celina undo/celina migrate + celina-eventd.service
│   └── 99-verify.sh                   # Strict 5-state audit (verifies 0700/0600, no input group, extensions.lock, --accessibility, --verify-models, all continuity & OS-level parity drop-ins)
│
├── features/celina/                   # Built-In Celina AI (Celly) Subsystem (99 tools: 92 offline / 7 web-gated)
│   ├── install.sh                     # Private 0700/0600 installer (pinned Ollama archive, model-digests.sha256, pinned requirements.txt / --require-hashes .lock, embeddinggemma pull, Piper voices, celina-dbus/export/import launchers, automatic celina migrate, systemd user units, D-Bus activation, GNOME Shell extension, quarantine dirs)
│   ├── requirements.txt               # Pinned core Python dependencies (+ requirements-optional.txt)
│   ├── config/                        # env.template, neural-schema.json (99 tools), celina-capabilities.json, mcp-servers.json, iot-allowlist.json
│   ├── skills/                        # 5 bundled trusted SKILL.md playbooks
│   ├── celina_pkg/                    # 34-module hardened Python agent (+ dbus_service, ebpf_sensor, capabilities, evidence ledger, semantic_blueprint, state_ops [export/import/undo/migrate], Astra checkpoints, embeddings, portal_input, os_events, spotlight_index, continuity, ios_ancs, writing_tools, calendar_bridge, app_intents, biometric_anim)
│   └── celina_hardware/               # 5-module hardware & biometric layer (+ compute.py NPU/GPU offload, BatteryChargeLimit, PrivacyIndicator, PipeWireRouting)
│
└── tests/                             # Automated security, native OS integration (systemd, D-Bus, GNOME Shell 48/49/50 ESM, CO-RE eBPF, NPU/GPU compute offload, .deb packaging), AI-native OS boundaries (Secure Boot MOK, LUMOS semantic blueprints, OS capability manifest + bwrap isolation, hash-chained evidence ledger), operational-trust (export/import/undo/migrate/checkpoints/locale), concurrency/soak, hardware, continuity, OS-level macOS parity, Tier B sandbox + Apple ID guardrail, Stage 00 7-way hard gate, OOM/Hibernate/LUKS/Dual-Boot invariant, VA-API fallback, circuit breaker, repo-wide integrity/hygiene, Hybrid Semantic/Embeddings/RRF, iOS/Weylus/v4l2loopback, Ebbinghaus memory, meeting brief, Python sandbox, Systemd/UDisks2/Vault/MIME, ActionGate, PipeWire ducking, Silero VAD, schema migration, MoE & 5-pillar smoothness tests
    ├── test_security_net_approval_mcp.py
    ├── test_skills_sandbox_gui_appnap.py
    ├── test_hardware_biometrics.py
    ├── test_installer_dryrun_rollback.py
    ├── test_mac_ecosystem_integration.py
    ├── test_bucket1_bucket2_parity.py
    ├── test_audit_fixes_and_parity.py
    ├── test_soak_and_concurrency.py
    ├── test_operational_trust.py
    ├── test_ai_os_boundaries.py
    └── test_native_os_integration.py
```

---

## Quick Start

### Option A — Native Debian Package (`.deb`)
```bash
# Build (or download) the native Ubuntu/Debian package
./tools/build-deb.sh
sudo apt install ./releases/macubuntu_4.4.0_all.deb

# Complete per-user desktop & Celina setup
macubuntu-install --user-only
systemctl --user enable --now celina.service celina-eventd.service
gnome-extensions enable celina-island@macubuntu.org

# Inspect NPU/GPU compute routing & optional CO-RE eBPF readiness
macubuntu-compute --json
./tools/build-ebpf.sh --check
```

### Option B — Direct Repository Installer
```bash
cd macubuntu
chmod +x install.sh uninstall.sh

# Preview every action safely (0 writes, 0 clones, 0 sudo)
./install.sh --dry-run

# Instant read-only diagnostics, AT-SPI2 locale audit & Secure Boot MOK check (0 sudo, 0 writes)
./install.sh --doctor
./install.sh --locale
./tools/enroll-mok.sh --dry-run

# Preview an apt --simulate style upgrade diff (schema, tool tiers, extensions, drop-ins; 0 writes)
./install.sh --dry-run --upgrade

# Single-command complete install (flock-guarded, continue-on-error by default, auto-selects RAM-matched Dense/MoE model + AMD Vulkan if present)
./install.sh --profile default-8gb

# Safe idempotent upgrade from v4.2/v4.3 -> v4.4 (preserves personal memories, secrets & skills)
./install.sh --upgrade

# Disaster recovery, undo, OS capability audit, tamper-evident evidence & migration
celina-export ~/celina-backup.tar.gz          # Tamper-evident WAL-safe SQLite + KG + skills + evidence + SHA-256 manifest
celina-import ~/celina-backup.tar.gz --dry-run
celina undo --list                            # Pre-execution SHA-256 backups for all 7 files_office mutation tools
celina undo --last                            # Atomically revert the last Tier-2 file edit/move/trash
celina capabilities --audit                   # Validate 99-tool OS capability manifest (0 Shadow-AI drift)
celina evidence verify                        # Verify SHA-256 hash chain across every Tier-2 execution
celina evidence export ~/celina-audit.jsonl   # Export self-verifying Tier-2 evidence bundle + .sha256
celina migrate --dry-run                      # Diff tools added/removed/tier-changed before migrating
celina checkpoints                            # Inspect Project Astra autonomous step recovery logs

# Optional customizations (top bar, snappy animations, explicit Dense or Sparse MoE model, Accessibility, multi-user split)
./install.sh --profile default-8gb --top-panel hidden --animation-speed fast --accessibility
./install.sh --profile default-8gb --model granite3.1-moe:1b    # Ultra-light 400M-active Sparse MoE for 8GB Ryzen 3
./install.sh --profile power-user --model gemma4:26b            # 25.2B Sparse MoE (3.8B active) for 24GB+ RAM
./install.sh --system-only                                      # System stages only (for multi-user hosts)
./install.sh --user-only                                        # User desktop/Celina stages only
./install.sh --fail-fast                                        # Abort on first stage failure instead of continuing

# Run strict 5-state post-install health, security & Ollama model-digest audit
./install.sh --verify
./install.sh --verify-models

# Preview or execute rollback / uninstall
./uninstall.sh --dry-run
./uninstall.sh                 # Keeps user memories/configs
./uninstall.sh --purge-data    # Also removes ~/.config/celina & ~/.local/share/celina

# Run full automated security, continuity & hardware test suite
pytest -v
```

---

## Shortcuts & Built-In Celina Controls

- **`Super + Space`** — Live Inline CoreSpotlight (`celina-spotlight` — apps, AST math, unit conversion, GNOME Settings, private FTS5 files & Celina)
- **`Super + Shift + C`** — Toggle Celina Dynamic Island (live 🟠/🟢/🟣 privacy dots, Focus badge, Control Center quick strip, iOS ANCS alerts, 60 FPS Face ID / Touch ID animations)
- **`Super + Alt + C`** — Instant Siri-style voice capture (sentence-streamed Piper TTS + 5s hands-free follow-up window)
- **`Super + Shift + W`** — Apple Intelligence Writing Tools (`celina-writing-tools` — local Ollama proofread/rewrite on Wayland selection)
- **`Super + Shift + T`** — macOS Live Text Region OCR (`celina-live-text` — area snip → Tesseract → clipboard, zero disk trail)
- **`Esc`** — Silence speech mid-sentence (Barge-In) or Emergency Stop desktop automation (`InputBroker` + Portal session teardown)
- **Instant Tier-0 Commands:** `celly "coding mode"`, `celly "100 miles to km"`, `celly "sqrt(144) + 2^8"`, `celly "check continuity status"`, `celly "audit app permissions"`, `celly "route spotify audio to bluetooth"`
- **Live Controls (0-LLM Reflex):** `/model` (view/switch Dense & Sparse MoE Ollama models), `/ram` (live RAM & dynamic `keep_alive` telemetry), `/offload` (instant 0 MB RAM eviction), `/name <NewName> [Nickname]`, `/why`.
