# Known Limitations & Honest Project Boundaries (`macubuntu` v4.4)

`macubuntu` makes Ubuntu 26.04 LTS look, feel, sound, and operate as close to macOS + Apple Intelligence as open-source Linux engineering permits—without compromising your OS security or Apple ID.

Trustworthy software is explicit about what it **cannot** do. This document draws the exact boundary between what works reliably on real Linux hardware (Tier A), what is experimental/lab-only (Tier B), and what is impossible due to Apple hardware cryptography.

---

## 1. Hard Cryptographic & Hardware Ceilings (Cannot Be Emulated in Software)

| Capability | Why It Cannot Work Natively on Linux | Honest Workaround |
|---|---|---|
| **Apple Secure Enclave (SEP) & Hardware ECID Identity** | Apple ties iMessage registration, Apple Pay, iCloud Keychain, and Handoff cryptographic attestation to factory-fused Secure Enclave silicon + signed ECID certificates. **No Linux software stack can manufacture a genuine Secure Enclave identity.** | Use Tier A open protocols (`LocalSend`, `GSConnect`, `KDE Connect`, `Syncthing`, `UxPlay`, `Shairport-Sync`) with **zero Apple ID risk**. |
| **Native iMessage / FaceTime on Linux** | Registering non-Apple hardware directly with Apple Push Notification / IDS servers risks temporary or permanent Apple ID suspension. | Use **BlueBubbles** bridged to a real Mac you own (recommended primary path) or an isolated macOS VM. See [`docs/imessage-safe-path.md`](docs/imessage-safe-path.md). |
| **True Tone Ambient RGB Display Sensor** | macOS True Tone relies on an Apple ambient color-temperature photodiode inside MacBook/Studio Display bezels. Most Linux laptops lack an RGB ambient color sensor. | GNOME Night Light + ICC color profiles adjust warmth by time of day, not live room white-balance. |
| **Apple H1/H2 Instant Multi-Device AirPods Switching & Proprietary Spatial Audio** | Instant iCloud-synced audio handoff across iPhone/iPad/Mac and head-tracked Spatial Audio use Apple-proprietary BLE + AAC-ELD protocols. | Tier A WirePlumber 0.5+ delivers high-quality **AAC / LDAC / aptX-HD / SBC-XQ** audio + hardware volume control; BLE battery readout is best-effort. |
| **Universal Control / Sidecar Wire-Protocol Parity** | Apple Sidecar & Universal Control use private AWDL + HEVC/display-streaming handshakes authenticated over iCloud. | Tier A uses **Deskflow / Lan Mouse** (mouse/keyboard sharing) and **Weylus** (iPad second display / stylus) over standard LAN Wi-Fi/USB. |
| **macOS `.app` / Mach-O GUI Binaries** | Linux uses ELF binaries and Wayland/X11; macOS uses Mach-O + Cocoa/AppKit/Metal. | Native Linux equivalents, Flatpaks, and web apps. **Darling** (experimental) supports CLI Darwin tools only—not modern Cocoa/Metal GUI apps. |

---

## 2. Protocol & Platform Fragility (Real-World Operational Caveats)

### AirPlay 2 (`UxPlay` & `Shairport-Sync`)
- Both projects rely on reverse-engineered community implementations of AirPlay 2 / FairPlay / HAP.
- Major iOS/macOS point releases (such as iOS 18 / macOS 15 audio encryption updates) have historically broken AirPlay 2 pairing temporarily until upstream patches land.
- `macubuntu` pins `UxPlay` (`v1.73.7`), tracks `Shairport-Sync` via Ubuntu signed APT updates, and monitors systemd logs (`celina_pkg/continuity.py`) to surface explicit `airplay_handshake_warning` alerts rather than failing silently.

### iOS BLE ANCS Notifications & Calls
- Requires pairing your iPhone over Bluetooth and enabling **Share System Notifications** in iOS Bluetooth settings.
- Incoming call Positive/Negative actions work best on **iOS 18.2+**; on iOS 16–17 and locked screens, iOS may drop ANCS action UIDs.

### Wayland Clipboard & X11 Fallbacks
- On Wayland, applications own their clipboard selection; if a short-lived app copies text and immediately exits, the clipboard empties unless a clipboard manager holds it (`clipboard-indicator` is enabled by default to mitigate this).
- On legacy **X11** sessions (e.g., older NVIDIA drivers without `nvidia-drm.modeset=1`), Wayland-only features (`wl-clipboard`, `xdg-desktop-portal` screencast, fractional scaling, `ydotoold`) are degraded. Stage 00 detects X11 and warns explicitly.

### Ubuntu 24.04 / 26.04 Accessibility Localization Gap (`at-spi2-core`)
- Upstream Ubuntu `at-spi2-core` packages omit `.mo` translation catalogs on 24.04/26.04/26.10, and some GTK 4 accelerator labels remain untranslated in non-English locales.
- **Impact:** Orca screen reader announcements for some UI role names fall back to English in non-English locales.
- **Mitigation:** Stage 00 (`./install.sh --locale`) explicitly audits your locale and warns when `at-spi2-core.mo` is missing. Celina's `wayland_gui` perception resolves canonical integer AT-SPI2 role enums (`Atspi.role_get_name`) rather than localized strings so GUI automation never degrades silently.

### Hardware Video Acceleration (VA-API), Secure Boot MOK & Suspend-Then-Hibernate
- Older GPUs or missing VA-API codecs can stall Firefox hardware decode; Stage 20 runs `vainfo` and automatically falls back to software decode (`apply-safari-theme.py --disable-vaapi`).
- **UEFI Secure Boot (Ubuntu 26.04 Default):** 12 of 13 stages load zero unsigned modules and never require disabling Secure Boot or TPM 2.0. Only Stage 40's optional `v4l2loopback-dkms` (Continuity Camera `/dev/video10`) requires enrolling Ubuntu's local DKMS MOK once via `./tools/enroll-mok.sh` (reboot → Enroll MOK → `./tools/enroll-mok.sh --verify`).
- **Single-Node Agent Scope (Cross-Device Actuation is v5):** In v4.4, Celina orchestrates actions on your local Ubuntu desktop; cross-device workflows use human-operated Tier A tools (`LocalSend`, `GSConnect`, `Deskflow`, `Syncthing`). Automated multi-device gRPC orchestration with mTLS attestation is scoped to v5 (see [`docs/v5-roadmap-cross-device-actuation.md`](docs/v5-roadmap-cross-device-actuation.md)).
- Offline cutoff: local model frozen at training date; live data requires opt-in web_comms.
- `suspend-then-hibernate` requires **on-disk swap >= RAM + ZRAM** AND (if LUKS-encrypted) a non-ephemeral `/etc/crypttab` + configured `initramfs` `resume=`. On pure-ZRAM or unconfigured LUKS hosts, Stage 50 intentionally disables hibernation (`sleep-instant-suspend.conf`) so closing the lid never fails to sleep.

---

## 3. Stability & Risk Matrix: Stable vs. Experimental vs. Disposable Apple ID

| Subsystem | Status | Requires Apple ID? | Key Caveat |
|---|---|---|---|
| **Core macOS UI, ZRAM, PipeWire, Safari Firefox, LocalSend, GSConnect, UxPlay, Shairport-Sync, Celina Core** | **Tier A — Production-Safe** | **No (Zero Apple ID risk)** | Pinned SHAs (`download-locks.sha256`, `extensions.lock`), `flock`-guarded, reversible via `uninstall.sh` & `celina undo`. |
| **Opt-in `keyd` macOS Cmd Keys (`--macos-cmd-keys`) & Silent Boot (`--silent-boot`)** | **Tier A — Opt-In** | **No** | Never enabled silently; dual-boot Windows GRUB entries are preserved via `GRUB_DISABLE_OS_PROBER=false`. |
| **Opt-in Accessibility (`--accessibility`: Orca, KMagnifier, AT-SPI2)** | **Tier A — Opt-In** | **No** | Subject to Ubuntu's upstream `at-spi2-core.mo` English role-name fallback in non-English locales. |
| **Celina Project Astra Autonomous GUI Mode (`autonomous_gui_task`)** | **Experimental (Approval-Gated)** | **No** | Upfront mission approval + per-action re-confirmation on sensitive targets + Esc emergency stop + durable step checkpoints (`celina checkpoints`). |
| **Legacy `/dev/uinput` (`--experimental-uinput` / `ydotoold`)** | **Experimental / Discouraged** | **No** | Grants synthetic input injection; disabled by default in favor of GNOME AT-SPI2 & Wayland Portal. |
| **Tier B AWDL (`owl`) & AirDrop (`opendrop`)** (`tools/experimental-deep-stack.sh`) | **Experimental / Lab-Only** | **No** | **Chipset-dependent:** Requires monitor-mode + frame-injection Wi-Fi hardware (Atheros `ath9k`/`ath9k_htc` or MediaTek `mt76`). **Does NOT work reliably on built-in Intel `iwlwifi`, Realtek, or Broadcom laptop chips.** |
| **Tier B `FindMy.py` & Digest-Pinned Anisette v3** (`tools/experimental-deep-stack.sh`) | **Experimental / Lab-Only** | **Disposable Apple ID ONLY** | **NEVER enter your primary Apple ID.** Default mode generates non-impersonating local lab keys (`--generate-test-keys`) and blocks runs if primary Apple ID tokens are detected in the user keyring. |
| **Darling macOS Compatibility Layer** | **Experimental / External** | **No** | Not installed by `macubuntu`; CLI tools only, no production Cocoa/Metal GUI support. |

---

## 4. Operational Recovery Guarantees (What You Should Do Before Upgrading or Traveling)

1. **Back up Celina's identity independently of Timeshift:**
   ```bash
   celina-export ~/celina-backup.tar.gz          # WAL-safe SQLite + KG + skills + SHA-256 manifest
   celina-import ~/celina-backup.tar.gz --dry-run
   ```
2. **Reverse any Celina file edit/move/trash immediately:**
   ```bash
   celina undo --list
   celina undo --last                            # Verifies pre-execution SHA-256 before restoring
   ```
3. **Preview any macubuntu upgrade before touching disk:**
   ```bash
   ./install.sh --dry-run --upgrade              # Shows schema, tool-tier, extension & drop-in diffs
   celina migrate --dry-run                      # Shows exact Tier 1 / Tier 2 tool changes
   ```
4. **Diagnose live desktop issues, Secure Boot MOK, OS capabilities & tamper-evident Tier-2 evidence:**
   ```bash
   ./install.sh --doctor
   ./install.sh --locale
   ./tools/enroll-mok.sh --dry-run               # Check UEFI Secure Boot & DKMS MOK for v4l2loopback
   celina capabilities --audit                   # Validate 99-tool OS capability manifest (92 offline / 7 web-gated, 0 Shadow-AI drift)
   celina evidence verify                        # Verify SHA-256 hash chain across ~/.local/share/celina/evidence/ledger.jsonl (+ fail-closed anchor)
   celina evidence export ~/celina-audit.jsonl   # Export tamper-evident JSONL audit bundle + .sha256
   ```

---

## v4.4.0 Audit Storage & Verification Scope Notes

- **Evidence Ledger Storage Format:** Celina stores its tamper-evident Tier-2 audit trail as an **append-only SHA-256 hash-chained JSONL file** (`~/.local/share/celina/evidence/ledger.jsonl`, mode `0600`) paired with an external fail-closed anchor (`~/.config/celina/evidence_anchors/<sha>.anchor`, mode `0600`) — strictly separate from SQLite `memory.db` (not a SQLite `evidence.db` file). Deleting, truncating, or tampering with `ledger.jsonl` fails closed on startup (`EvidenceIntegrityError`).
- **Statically vs. Live-Verified Subsystems in `v4.4.0`:** Live Linux kernel Landlock ABI 2 enforcement, `systemd-analyze security`, Tier-2 gating, evidence hash-chain tamper & deletion detection, 3-leg hybrid memory, 50W+50R SQLite WAL concurrency, `TaskGraph` crash recovery, Gear 0 AST math, offline guards, and `macos_glue` cross-tool contracts are executed and verified in the automated 24-suite CI harness (`217/217` passing). Five desktop-hardware/session-dependent checks — **live microphone Whisper STT**, **live LibreOffice UNO/GUI rendering**, **live AT-SPI desktop button click**, **live `bwrap` container escape**, and **live multi-UID cross-session D-Bus calls** — are verified statically and via unit tests in `v4.4.0` and require a full desktop VM (scheduled for automated desktop-VM CI in `v4.5`).

