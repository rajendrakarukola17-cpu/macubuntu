# `macubuntu` v4.4 Hardware, Protocol & OS Compatibility Matrix

Target baseline: **Ubuntu 26.04 LTS (Wayland / GNOME)**, x86_64, 8 GB+ RAM.  
For hard Apple cryptographic/hardware ceilings and Tier B risk boundaries, read **[`LIMITATIONS.md`](LIMITATIONS.md)** first.

---

## 1. GPU, Video Decode (VA-API) & Local AI Acceleration

| GPU Stack | Wayland Compositor & Safari Firefox | Hardware Video Decode (`vainfo`) | Ollama Local AI Acceleration | Notes & Automatic Fallbacks |
|---|---|---|---|---|
| **AMD Ryzen iGPU / Radeon dGPU** (`amdgpu` / Mesa RADV) | Full native Wayland + VRR + fractional scaling | **Supported** (`mesa-va-drivers`: H.264, VP9, HEVC, AV1 on RDNA2+) | **Auto-enabled Vulkan Offload** (`OLLAMA_VULKAN=1`, `OLLAMA_IGPU_ENABLE=1` via `ollama-amd-vulkan.conf`) | Best overall out-of-the-box experience on 8–32 GB laptops. |
| **Intel Core iGPU (Gen 9–14 / Arc / Xe)** (`i915` / `xe`) | Full native Wayland + fractional scaling | **Supported** via `intel-media-va-driver` (H.264/VP9/AV1). Older kernels on early Xe can lack VLD profiles. | CPU (`q8_0` KV-cache, Flash Attention) | Stage 50 runs `vainfo` and automatically invokes `apply-safari-theme.py --disable-vaapi` if H.264/VP9 `VAEntrypointVLD` is absent. |
| **NVIDIA (Proprietary 555+ explicit sync / open kernel module)** | Wayland works with explicit sync; occasional Firefox VA-API stalls | **Not supported natively** without external `nvidia-vaapi-driver` | CUDA supported automatically by Ollama | Stage 50 detects missing H.264/VP9 VA-API via `vainfo` and **automatically disables Firefox VA-API prefs** (`--disable-vaapi`) to prevent silent decode stalls / CPU spikes. |

---

## 2. Laptop 80% Optimized Battery Charging (`90-battery-charge-limit.rules`)

Stage 50 checks `/sys/class/power_supply/BAT*/charge_control_end_threshold` before activating `90-battery-charge-limit.rules`. If unsupported by firmware, Stage 50 skips cleanly with zero errors.

| Laptop Family | Kernel Driver | Sysfs Path | Status |
|---|---|---|---|
| **ThinkPad (Lenovo)** | `thinkpad_acpi` | `/sys/class/power_supply/BAT0/charge_control_end_threshold` | **Native (Auto-enabled)** |
| **Framework Laptop (13/16)** | `cros_charge-control` / BIOS | `/sys/class/power_supply/BAT1/charge_control_end_threshold` | **Native (Auto-enabled on supported EC firmware)** |
| **ASUS ZenBook / ROG / VivoBook** | `asus-nb-wmi` | `/sys/class/power_supply/BAT0/charge_control_end_threshold` | **Native (Auto-enabled)** |
| **MSI Modern / Prestige / Stealth** | `msi-ec` | `/sys/class/power_supply/BAT1/charge_control_end_threshold` | **Supported when `msi-ec` loaded** |
| **System76 / Tuxedo / LG Gram** | `system76_acpi` / `lg-laptop` | `charge_control_end_threshold` | **Native** |
| **Dell / HP Consumer Laptops** | Vendor BIOS / `dell-smm` | Often BIOS-only (no standard sysfs node) | **Safe no-op** — configure 80% cap in BIOS/UEFI (*Primary Battery Charge Configuration* / *Battery Care Function*). |

---

## 3. Memory (8 GB ZRAM), `systemd-oomd` & Safe Sleep (Hibernate)

- **8 GB ZRAM + `vm.swappiness=150` + OOM Scores:**
  - `celina-eventd.service`: `OOMScoreAdjust=-300`, `MemoryHigh=96M`, `MemoryMax=384M` — keeps Dynamic Island privacy dots, Focus modes, USB Guard, and Systemd Crash Doctor journal analysis responsive under memory pressure.
  - `ollama.service` (`ollama-8gb-ram.conf`): `OOMScoreAdjust=300`, `ManagedOOMMemoryPressure=kill`, `OLLAMA_KEEP_ALIVE=45s`, `OLLAMA_KV_CACHE_TYPE=q8_0`.
  - **Expected 8 GB Behavior:** Default `qwen3:1.7b` (~1.4 GB) runs comfortably without OOM kills. Manually forcing a **4B+ model** (`qwen3:4b` or larger) on an 8 GB host with heavy browser tabs **will cause ZRAM compression churn**; because `ollama.service` has `+300` OOM score, `systemd-oomd` cleanly reaps Ollama (and systemd restarts it) rather than freezing or killing GNOME Shell.
  - *Recommended Host Benchmark:* Run `systemd-cgtop` and watch `/sys/fs/cgroup/system.slice/ollama.service/memory.pressure` (`some avg10`) during `ollama run qwen3:4b` before setting 4B as a default on 8 GB hardware.
- **Hibernate + ZRAM Swap Invariant (`detect_has_hibernate_resume` in `core/detect.sh`):**
  - During `suspend-then-hibernate`, the Linux kernel must decompress ZRAM pages into real on-disk swap alongside active RAM.
  - Stage 50 verifies **`real_swap_kb >= ram_kb + zram_kb`** before deploying `HibernateDelaySec=3h` (`sleep-safesleep.conf`). On pure-ZRAM or undersized-swap hosts, Stage 50 automatically deploys `sleep-instant-suspend.conf` (`AllowHibernation=no`) so lid close never fails to hibernate.

---

## 4. iOS BLE ANCS Matrix (`celina_pkg/ios_ancs.py`)

| iOS Version | BLE Notification Mirroring | Incoming Call Accept / Dismiss (ANCS Positive/Negative Action) | AirPods Battery Status | Known Caveats |
|---|---|---|---|---|
| **iOS 16.x** | Supported (requires Bluetooth pairing + *"Share System Notifications"* toggled ON in iOS Bluetooth settings) | Partial — Positive/Negative action UIDs can drop if iPhone screen is locked | Basic / best-effort via BlueZ battery GATT | Re-toggle Bluetooth on iPhone if ANCS GATT service disappears after reboot. |
| **iOS 17.x** | Supported | Mixed — iOS 17 tightened background ANCS action handling on locked handsets | Partial — Apple changed some BLE advertisement formats for AirPods Pro 2 / USB-C | Expect occasional reconnect after long idle sleep. |
| **iOS 18.0 – 18.1** | Supported | Basic call notifications; reliable call rejection varies by handset | Best-effort | Ensure BlueZ `Experimental = true` if inspecting GATT battery characteristics. |
| **iOS 18.2+** | Supported | **Best support** for ANCS Positive/Negative call actions while paired | Best-effort (native AirPods AAC/HW volume works via WirePlumber regardless of BLE battery reporting) | For guaranteed messaging/calls, pair with Tier A `GSConnect` / `LocalSend` or [`docs/imessage-safe-path.md`](docs/imessage-safe-path.md). |

---

## 5. AirPlay 2 (`UxPlay` & `Shairport-Sync`) Known Caveats

- **Supply-Chain Pinning:** `UxPlay` is pinned in `system/download-locks.sha256` to `FDH2/UxPlay` `v1.73.7` (commit `df67c212...`). `Shairport-Sync` is installed from Ubuntu 26.04 signed APT repos (for automatic security patches).
- **Apple Protocol Regressions:** Both projects rely on community-maintained AirPlay 2 / FairPlay / HAP implementations. Major iOS/macOS releases (e.g., iOS 18 / macOS 15 audio encryption or pair-verify changes) have historically caused temporary AirPlay 2 handshake breaks until upstream updates.
- **Runtime Diagnostics:** `celina_pkg/continuity.py` (`get_continuity_status` / `check_airplay_handshake_health`) inspects the last hour of `uxplay-user.service` and `shairport-sync-user.service` logs and surfaces an explicit `airplay_handshake_warning` if `pair-verify`, `fairplay`, or `handshake fail` events appear, rather than failing silently.
- **mDNS Discovery:** Stage 40 deploys `/etc/systemd/resolved.conf.d/99-macubuntu-mdns.conf` (`MulticastDNS=no`) so `avahi-daemon` owns UDP 5353 cleanly with zero packet drops.

---

## 6. Wayland Clipboard Persistence & Race Conditions

- **Known Wayland Behavior:** On Wayland, the clipboard is owned by the source application; if a short-lived app copies text and exits before you paste, the selection disappears unless a clipboard manager holds it.
- **How `macubuntu` Mitigates It:**
  1. GNOME's **`clipboard-indicator`** extension is enabled by default (`ENABLE_CLIPBOARD_HISTORY=true`) to persist clipboard history across app exits.
  2. Celina's clipboard tools prefer **`org.freedesktop.portal.Clipboard`** when available, falling back to `wl-paste` / `wl-copy` (and `wl-paste --watch` only for non-portal clients).
  3. Celina's cache firewall strictly refuses to store clipboard contents in semantic/response caches.

---

## 7. Tier B Research Wi-Fi Chipsets (AWDL `owl` & `opendrop`)

Only relevant if you manually opt into `./tools/experimental-deep-stack.sh`:

| Chipset / Driver | Monitor Mode + Frame Injection (AWDL `owl`) | Recommendation |
|---|---|---|
| **Qualcomm Atheros AR9280 / AR9271** (`ath9k` / `ath9k_htc`) | **Supported** | Gold-standard dedicated USB/PCIe lab adapter for AWDL research. |
| **MediaTek MT7612U / MT7921U** (`mt76`) | **Supported (modern kernels)** | Good dual-band USB lab adapter; keep built-in laptop Wi-Fi on normal internet. |
| **Intel AX200 / AX210 / BE200** (`iwlwifi`) | **Not reliable for AWDL injection** | Keep on Tier A Wi-Fi (`LocalSend`); use a separate USB lab adapter for Tier B. |
| **Realtek / Broadcom built-in laptop cards** | **Generally unsupported** | Use Tier A `LocalSend` for daily AirDrop replacement. |

---

## 8. Platform Edge Cases, Multi-User, Accessibility & Diagnostics

- **X11 Sessions (NVIDIA / Legacy Xorg):** Stage 00 detects `XDG_SESSION_TYPE=x11` and warns that Wayland-only features (`wl-clipboard`, `xdg-desktop-portal` screencast, fractional scaling, `ydotoold`) are degraded; recommends enabling `nvidia-drm.modeset=1` and switching to **Ubuntu on Wayland** at GDM.
- **LUKS Encrypted Swap & Hibernate:** Stage 50 checks both `real_swap_kb >= ram_kb + zram_kb` and `detect_luks_swap_resume_ready` (verifying `/etc/crypttab` does not use ephemeral `/dev/urandom` keys and `resume=` + `initramfs-tools/conf.d/resume` are configured) before enabling `HibernateDelaySec=3h`.
- **Existing Disk Swap Preservation:** Stage 35 preserves existing `/etc/fstab` disk swap partitions/files by default (letting ZRAM run at higher priority `100`). Pass `--replace-swap` only if you intentionally want Stage 35 to comment out legacy disk swap.
- **Dual-Boot Windows (EFI):** Stage 50 detects `/boot/efi/EFI/Microsoft` (`detect_dualboot_windows`) and automatically enforces `GRUB_DISABLE_OS_PROBER=false` when `--silent-boot` is enabled so Windows Boot Manager entries are preserved in GRUB.
- **Multi-User Workstations:** Stage 00 warns when multiple human accounts (`UID >= 1000`) exist. Admins can run `./install.sh --system-only` once, and individual users can opt in via `./install.sh --user-only`.
- **Accessibility Stack (Opt-In):** Pass `./install.sh --accessibility` (`ENABLE_ACCESSIBILITY=true`) to install and verify `orca` screen reader, `kmag` screen magnifier, `at-spi2-core`, and `speech-dispatcher` in Stage 60 & Stage 99.
- **Read-Only Fast Diagnostics:** Run `./install.sh --doctor` (zero sudo, zero disk writes) at any time for a 16-point health check with copy-pasteable fixes, `./install.sh --locale` for an AT-SPI2 localization audit, or `./install.sh --verify-models` to validate Ollama model SHA-256 digests against `~/.config/celina/model-digests.sha256`.

---

## 9. Voice, TTS & Locale Support Matrix (Piper, STT & AT-SPI2)

| Locale / Language | Offline Neural TTS (Piper, SHA-256 Pinned) | Speech-to-Text (`faster-whisper` / STT) | Romanized Input Detection | AT-SPI2 (`wayland_gui` & Orca) Behavior |
|---|---|---|---|---|
| **English (`en_US`, `en_GB`, `C.UTF-8`)** | **Native Neural** (`en_US-lessac-medium.onnx`, pinned in `download-locks.sha256`) | Native multilingual | N/A | Full canonical role names & Orca announcements. |
| **Telugu (`te_IN`)** | **Native Neural** (`te_IN-maya-medium.onnx`, pinned in `download-locks.sha256`) | Native multilingual | **Native Tenglish** (`time entha`, `volume penchu`, etc.) | Ubuntu 24.04/26.04 omits `at-spi2-core.mo` → Orca role names fall back to English; Celina uses canonical AT-SPI2 role enums (`Atspi.role_get_name`). |
| **Hindi (`hi_IN`)** | **Native Neural** (`hi_IN-pratham-medium.onnx`, pinned in `download-locks.sha256`) | Native multilingual | **Native Hinglish** (`samay kya hua`, `brightness kam karo`, etc.) | Ubuntu 24.04/26.04 omits `at-spi2-core.mo` → Orca role names fall back to English; Celina uses canonical AT-SPI2 role enums. |
| **Other Locales (`es`, `fr`, `de`, `ja`, etc.)** | **Fallback** (`espeak-ng` / English Piper unless user installs extra Piper `.onnx` models) | Supported by Whisper multilingual models | Falls back to native script / English | Stage 00 (`./install.sh --locale`) checks `/usr/share/locale/<lang>/LC_MESSAGES/at-spi2-core.mo` and warns explicitly if missing. |

---

## 10. Stage-by-Stage UEFI Secure Boot Compatibility (Ubuntu 26.04 Default)

`macubuntu` **never** requires disabling UEFI Secure Boot or TPM 2.0.

| Stages | Unsigned Out-of-Tree Kernel Modules? | Secure Boot Status on Fresh Ubuntu 26.04 ISO | Action Needed |
|---|---|---|---|
| **Stages 00, 10, 20, 25, 30, 35, 45, 50, 55, 60, 70, 99** (12 of 13 stages) | **None** (`zram`, `bbr`, `fq`, `pipewire`, `keyd` use Ubuntu-signed in-kernel modules or pure userspace) | **100% Secure-Boot-Safe** | None — runs cleanly out of the box. |
| **Stage 40 (`40-continuity-audio.sh`) — `v4l2loopback-dkms` only** | **1 DKMS module** (`v4l2loopback` for optional `/dev/video10` iPhone Continuity Camera) | Blocked with `Key was rejected by service` until Ubuntu's local DKMS MOK is enrolled | Run `./tools/enroll-mok.sh`, reboot, accept **Enroll MOK**, then verify with `./tools/enroll-mok.sh --verify`. (All other Stage 40 tools—LocalSend, GSConnect, UxPlay, Shairport-Sync, WirePlumber, Syncthing—are 100% Secure-Boot-safe!) |
| **Tier B Sandbox (`tools/experimental-deep-stack.sh`)** | Pure userspace (`owl`, `opendrop`, `FindMy.py`), optional lab Wi-Fi adapter | Secure-Boot-Safe (uses Ubuntu-signed `ath9k`/`mt76` in-kernel Wi-Fi drivers) | Never invoked by `install.sh`. |

---

## 11. Native OS & Hardware Integration Layers (NPU, eBPF, GNOME Shell, `.deb`)

| Subsystem | Supported Hardware / Kernel / Desktop | Safe Fallback Behavior |
|---|---|---|
| **systemd User Control Plane** | Ubuntu 24.04 / 26.04 `systemd --user` (`apparmor_restrict_unprivileged_userns=1` safe) | `celina.service` avoids mount-namespace directives by default; opt-in `celina-strict-fs.conf` only for kernels permitting unprivileged userns. |
| **Session D-Bus (`org.macubuntu.Celina`)** | Any desktop session running `dbus-daemon` or `dbus-broker` | `dbus_service.py` enforces peer-UID verification; falls back to CLI/local state if `gi.repository.Gio` is absent. |
| **GNOME Shell Extension (`celina-island@macubuntu.org`)** | GNOME Shell **48, 49, 50** (modern ESM `Extension` base class) | Falls back to standalone `celina-island` GTK window on non-GNOME desktops. |
| **CO-RE eBPF Sensor (`celina_sensor.bpf.c`)** | Ubuntu generic kernels with `CONFIG_DEBUG_INFO_BTF=y` (`/sys/kernel/btf/vmlinux`); optional LSM requires `lsm=...,bpf` | Automatically falls back to unprivileged D-Bus/inotify `os_events.py` when BTF or `CAP_BPF` is unavailable. |
| **NPU Offload (`/dev/accel/accel*`)** | Intel Core Ultra (`intel_vpu` / OpenVINO) & AMD Ryzen AI (`amdxdna`) via decoupled `celina-npu-inference.service` (`:11435`) | Transparently falls back to `ollama.service` (`:11434` GPU/CPU) without injecting NPU env vars into Ollama. |
| **GPU Offload (`/dev/dri/renderD*`)** | NVIDIA CUDA, AMD ROCm/Vulkan (`OLLAMA_VULKAN=1` + opt-in `ollama-amd-rocm.conf`), Intel Vulkan (`OLLAMA_VULKAN=1`) | Clean CPU fallback (`OLLAMA_NUM_THREADS`) on headless or software-rendered hosts. |
| **Debian Package (`macubuntu_4.4.0_all.deb`)** | Ubuntu 24.04 LTS, 25.10, 26.04 LTS (`Architecture: all`) | Maintainer scripts guard `systemctl`/`glib-compile-schemas` cleanly inside containers and chroots. |




