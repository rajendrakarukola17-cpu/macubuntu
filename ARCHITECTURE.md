# macubuntu Architecture — Tier A (Daily-Driver OS) vs. Tier B (Experimental Apple Protocol Sandbox)

`macubuntu` enforces a strict architectural firewall between **Tier A** (production, Apple-ID-free, daily-driver macOS parity on Ubuntu 26.04 Wayland) and **Tier B** (opt-in, isolated Apple protocol research).

This separation guarantees that running `./install.sh` **never** risks Apple ID lockouts, installs unmaintained kernel drivers, opens public network ports, or degrades daily-driver stability. For plain-language boundaries on what Linux software cannot emulate (ECID/SEP, native iMessage, True Tone sensors, AWDL Wi-Fi chipsets), see **[`LIMITATIONS.md`](LIMITATIONS.md)**.

---

## Tier A — Production Daily-Driver Stack (`install.sh`, `stages/`, `features/celina/`)

Installed and verified by `./install.sh` and `./install.sh --verify`. Zero proprietary Apple credentials, zero binary blobs, 100% open-source and supply-chain pinned.

| OS Layer | macOS Mechanism | macubuntu Tier A Native Linux Implementation |
|---|---|---|
| **Kernel & Memory** | Compressed Memory, Jetsam, App Nap, APFS Snapshots | `zram-generator` (`zstd`, `swappiness=150`), `systemd-oomd` PSI guard, cgroup v2 `AutoAppNapCoordinator`, NVMe/SSD/HDD udev I/O schedulers, Btrfs `grub-btrfs` snapshot detection, smart Safe Sleep (`HibernateDelaySec=3h` or pure-ZRAM instant suspend) |
| **Display & Input** | Metal/Quartz Compositor, Retina HiDPI, Trackpad, `⌘` Key | Native Wayland (`ELECTRON_OZONE_PLATFORM_HINT=auto`, `MOZ_ENABLE_WAYLAND=1`), Mutter fractional scaling + native XWayland HiDPI + VRR, MacBook `fingers` trackpad physics, opt-in Wayland-native `keyd` terminal-aware `Cmd` remapping, opt-in `--silent-boot` + active AMD/Intel P-States |
| **Audio & Discovery** | CoreAudio, AirPods AAC, Bonjour mDNS | WirePlumber 0.5+ Apple AAC / LDAC / aptX-HD / SBC-XQ / mSBC + HW volume, PipeWire 44.1/48/96 kHz low-jitter clock, `systemd-resolved` `MulticastDNS=no` drop-in eliminating UDP 5353 races with `avahi-daemon` |
| **Continuity (Zero Apple ID)** | AirDrop, AirPlay 2, Continuity Camera, Sidecar, USB Sync, Handoff | `LocalSend`, `Shairport-Sync`, `UxPlay` (pauses mirror automatically before starting camera), persistent `/dev/video10` `v4l2loopback` Continuity Camera, `Weylus` iPad Sidecar + Apple Pencil, `libimobiledevice` + `ifuse` (`~/iPhone`), BLE ANCS (`ios_ancs.py`), `GSConnect` + `scrcpy`, `Syncthing`, `Deskflow` |
| **Intelligence & Privacy** | Apple Intelligence, CoreSpotlight, Siri, TCC, Keychain, Touch ID | Celina v4.4 (**99 tools / 8 domains**: `92` offline-capable, `7` opt-in web-gated), zero-idle-RAM Hybrid Semantic & Embedding Engine (`embeddings.py`: Ollama `embeddinggemma` / `Model2Vec` + `float16` BLOBs + RRF + language-partitioned semantic cache), Ebbinghaus memory decay + KG contradiction resolution, PipeWire loopback offline meeting STT, Bubblewrap (`bwrap`) Python/shell sandboxes, `Flatseal`, `KeePassXC`, capability-gated `fprintd`/FIDO2 PAM |

---

## 6 Native OS & Hardware Integration Layers (First-Class Ubuntu Citizen)

Rather than behaving as a collection of ad-hoc scripts, `macubuntu` integrates Celina directly into Ubuntu's core system primitives with strict privilege separation:

| Layer | Implementation Artifact | Engineering & Security Boundary |
|---|---|---|
| **1. `systemd` Control Plane** | `system/systemd/{celina.service,celina-strict-fs.conf,celina-npu-inference.service,ollama-amd-rocm.conf}` | User-session daemons (`Restart=on-failure`, `NoNewPrivileges=true`, `UMask=0077`, seccomp `SystemCallFilter=@system-service`, `RestrictAddressFamilies=`). Root-only `CapabilityBoundingSet=` (`218/CAPABILITIES`) is forbidden, and mount-namespace directives (`ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp=yes`) are isolated in opt-in `celina-strict-fs.conf` to avoid Ubuntu 26.04 `apparmor_restrict_unprivileged_userns=1` (`226/NAMESPACE`) crashes. |
| **2. Session D-Bus Nervous System** | `system/dbus/org.macubuntu.Celina.{conf,service}`, `celina_pkg/dbus_service.py` | Session bus `org.macubuntu.Celina` verifies `GetConnectionUnixUser` peer UID against `os.getuid()`, enforces fail-closed Tier-2 approval on `AskPrompt`, and broadcasts `StatusChanged`/`PrivacyDotsChanged` signals. (`celina-eventd.service` remains strictly read-only/AF_UNIX.) |
| **3. Native GNOME Shell Extension** | `system/gnome/extensions/celina-island@macubuntu.org/` | Modern GNOME Shell 48/49/50 ESM extension registering in `Main.panel.addToStatusArea`, subscribing to compositor workspace/window signals (`global.workspace_manager`, `global.display`), and talking to Celina exclusively over session D-Bus with compiled GSettings (`glib-compile-schemas --strict`). |
| **4. CO-RE eBPF Kernel Sensor & LSM** | `system/ebpf/{celina_sensor.bpf.c,celina-ebpf-sensor.service}`, `tools/build-ebpf.sh`, `celina_pkg/ebpf_sensor.py` | `CAP_BPF`/`CAP_PERFMON` stay strictly isolated inside `celina-ebpf-sensor.service`; Celina Python stays 100% unprivileged. Ring-buffer tracepoints (`sched_process_exec`, `sys_enter_openat`) and opt-in fail-open `lsm/socket_connect` fall back cleanly to D-Bus/inotify `os_events.py` when BTF is absent. |
| **5. NPU & GPU Compute Offload** | `celina_hardware/compute.py`, `tools/detect-compute.sh`, `celina_pkg/{config.py,net_policy.py}` | Probes `/dev/accel/accel*` (Intel OpenVINO / AMD XDNA NPU) and `/dev/dri/renderD*` (CUDA / ROCm / Vulkan). Routes NPU inference to decoupled `celina-npu-inference.service` (`127.0.0.1:11435`) so vendor NPU env vars never crash `ollama.service` (`status=2`), with transparent Ollama (`11434`) fallback and SSRF blocking on both ports. |
| **6. Native Debian Package (`.deb`)** | `tools/build-deb.sh` → `releases/macubuntu_4.4.0_all.deb` | FHS `.deb` shipping systemd units, D-Bus policy/activation, pre-compiled GNOME Shell extension, `/usr/share/macubuntu/`, `/usr/bin/*` CLI wrappers, and container-safe `DEBIAN/{control,conffiles,postinst,prerm,postrm}`. |

---

## Tier B — Opt-In Experimental Apple Protocol Sandbox (`tools/experimental-deep-stack.sh`)

**Never invoked by `install.sh` or any file in `stages/`.** Must be launched manually by a user who wants to inspect or study Apple's proprietary wireless/BLE protocols in an isolated `0700` directory (`~/.local/share/celina/apple-research`).

### The 5 Protocol Layers & Honest Hard Boundaries

| Layer | Apple Proprietary Stack | Open-Source Research Tool (Pinned) | Practical Reality & Constraints |
|---|---|---|---|
| **Layer 1: Link / Wi-Fi** | **AWDL** (Apple Wireless Direct Link, peer-to-peer Wi-Fi without an AP) | `seemoo-lab/owl` (GPL-3.0, archived research repo) | Requires a dedicated Wi-Fi adapter supporting **monitor mode + frame injection** (e.g., Atheros AR9280 / MediaTek mt76). Most built-in laptop Wi-Fi cards cannot run AWDL alongside normal Wi-Fi. |
| **Layer 2: Local Transfer** | **Native AirDrop** (HTTPS + mDNS over `awdl0`) | `seemoo-lab/opendrop` (GPL-3.0) | Depends on a working `awdl0` interface from Layer 1 and only interoperates reliably with "Everyone" AirDrop discovery (not contacts-only hashed identity verification). For daily use, Tier A's **LocalSend** is faster and works on any Wi-Fi router. |
| **Layer 3: BLE Crowd-Sourcing** | **Find My Network** (rotating EC P-224 BLE advertisements) | `malmeloo/FindMy.py` (MIT, `v0.10.2`) | Can decrypt offline Find My reports using exported keys and research Anisette headers; subject to Apple rate limits and server-side protocol changes. |
| **Layer 4: Identity Provisioning** | **GrandSlam (GSA) / Anisette v3** | Localhost-bound Docker `127.0.0.1:6969:6969` (manual opt-in only) | **Account-Safety Rule:** NEVER bind Anisette to `0.0.0.0`, and **NEVER use your primary Apple ID** with experimental GSA/FindMy clients — Apple may lock or challenge unusual provisioning requests. Use a dedicated secondary test Apple ID only. |
| **Layer 5: Silicon & Hardware Trust** | **Secure Enclave, FairPlay DRM, Native iMessage/FaceTime, Unified Memory** | **Hard Non-Replicable Boundary** | Cryptographically tied to Apple hardware attestation and closed cloud services. `macubuntu` explicitly documents these as non-replicable rather than shipping brittle or unsafe workarounds. |

---

## Security & Rollback Guarantees

1. **Supply-Chain Pinning:** Even Tier B research repositories (`owl`, `opendrop`, `findmy-py`) are pinned to exact, cryptographically verified 40-char commit SHAs and the optional Anisette v3 Docker image (`dadoum/anisette-v3-server@sha256:d551c75454bcbee68dc262d4247d5f87fa309dfc8f2125644d333c648596e3c7`) is digest-pinned in `system/download-locks.sha256`. GNOME Shell extensions are pinned by immutable `version_tag` in `system/gnome/extensions.lock`. Python dependencies are pinned in `features/celina/requirements.txt` and `tools/requirements-tier-b.txt`, with automatic `pip install --require-hashes -r *.lock` enforcement whenever a host ABI `requirements.lock` is generated. (Ubuntu APT packages intentionally remain un-held so `unattended-upgrades` continues delivering Ubuntu Security Notice CVE patches; Flathub apps are GPG-verified via OSTree.)
2. **Zero D-Bus Server Attack Surface (`celina-eventd.service`):** `celina-eventd` does **not** export any D-Bus methods or own a bus name — it acts strictly as a read-only D-Bus signal subscriber (`systemd1`, `UDisks2`) and exchanges status with Dynamic Island solely via `0600` private files inside `~/.local/share/celina` (`0700`).
3. **Pre-Flight Hard Security Gates:**
   - **Stage 00 (`stages/00-preflight.sh`):** Hard-aborts `install.sh` (even under default `--continue-on-error`) if the 7-way 99-tool parity check or the Tier-2 `files_office` mutation lock fails.
   - **Tier B Account-Safety Guardrail (`tools/experimental-deep-stack.sh`):** Probes GNOME Keyring (`secret-tool`, never printing secret values) and local profile caches for Apple/iCloud/GSA/Anisette session indicators and **aborts** if detected unless `--allow-existing-apple-session` is passed explicitly.
3. **Zero Pollution of Tier A:** `tools/experimental-deep-stack.sh` installs strictly into `~/.local/share/celina/apple-research/` (`chmod 700`) with its own Python `venv` — it never touches `~/.local/share/celina/venv`, `install.sh`, `stages/`, or system services.
4. **Zero-Side-Effect Dry-Run:** `./tools/experimental-deep-stack.sh --dry-run` verifies every command without touching disk, APT, pip, or git.
5. **Clean Rollback:** `./uninstall.sh` removes the research `venv`; `./uninstall.sh --purge-data` wipes `~/.local/share/celina/apple-research` completely.

---

## Known Hardware & ECID/SEP Ceilings

- **ECID-Bound Device Quota & Secure Enclave (SEP) Impossibility:** Tier B's Anisette/GSA research tooling is strictly a **guest on an ECID-bound device quota it does not own**. No open-source Linux software stack can manufacture a cryptographic Apple Secure Enclave Processor (SEP) identity, bypass hardware attestation, or guarantee immunity from Apple server-side rate limits or account challenges.
- **Recommended iMessage Bridge:** Do **not** use Tier B research scripts for daily iMessage. See [`docs/imessage-safe-path.md`](docs/imessage-safe-path.md) for the BlueBubbles bridge architecture.
- **Wayland Clipboard Persistence:** Native Wayland compositors drop clipboard selections when the owning app exits before paste. Celina prefers `org.freedesktop.portal.Clipboard` when available, pairs `wl-clipboard` with GNOME's `clipboard-indicator` persistence extension, and documents caveats in [`COMPATIBILITY.md`](COMPATIBILITY.md).
- **8 GB OOM & ZRAM Policy:** Under `vm.swappiness=150` + `systemd-oomd`, `celina-eventd.service` runs with `OOMScoreAdjust=-300` (`MemoryMax=384M`) to stay alive while `ollama.service` runs with `OOMScoreAdjust=300` + `ManagedOOMMemoryPressure=kill` so an oversized 4B+ model spike reaps Ollama cleanly rather than freezing or killing GNOME Shell.

