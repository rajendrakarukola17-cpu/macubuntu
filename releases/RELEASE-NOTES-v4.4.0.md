# `macubuntu` `v4.4.0` — Release Notes (Feature-Frozen RC)

**Target Platform:** Ubuntu 26.04 LTS (Wayland, GNOME 48–51, 8 GB+ RAM)  
**Built-In AI Control Plane:** Celina v4.4 (`99` tools across 9 domains — `92` offline-capable, `7` opt-in web-gated)

---

## Verified Release Artifacts & SHA-256 Checksums

Verify before installation with `sha256sum -c SHA256SUMS`:

| Artifact | Size | SHA-256 |
|---|---:|---|
| `macubuntu-v4.4.0.tar.gz` | 781 KB (`292` entries) | `adc2d0d323362735accedc9f0c2cf1d2831580db505aa711f9388da39a1a69d6` |
| `macubuntu_4.4.0_all.deb` | 472 KB | `0d66acc32cb4196a11234a6a801209e074da6a629af48b6c07a5e10ad5102a3f` |

---

## Highlights & QA Audit Closure (`0 P0`, `0 P1`, `223/223` Tests)

- **Kernel-Enforced Landlock LSM (`files_office_policy`):** Verified live on Linux kernel Landlock ABI 2 — allows ELF dynamic linker execution (`/bin`, `/usr/bin`, `/lib`, `/lib64`, `/usr/lib`, `/usr/lib64`) and writes inside `~/Documents` while kernel-denying writes outside the allowlist (`EACCES`).
- **Hardened `systemd` Services (verified live on Ubuntu 26.04 Wayland `--user` systemd + `ollama.service`):**
  - System-level `ollama.service + ollama-hardening.conf`: **`1.1 OK`** (`systemd-analyze security --offline=yes < 3.0`)
  - User-session daemons (`celina.service`, `celina-eventd.service`, `celina-memory.service`, `celina-voice.service`, `celina-mcp-n8n.service`, and all helper `--user` units): kernel-enforced unprivileged seccomp (`NoNewPrivileges`, `UMask=0077`, `RestrictSUIDSGID`, `RestrictRealtime`, `LockPersonality`, `KeyringMode=private`, `SystemCallArchitectures=native`, `SystemCallFilter=@system-service`, `RestrictAddressFamilies=`), with root-only `CapabilityBoundingSet=` / `AmbientCapabilities=` (`218/CAPABILITIES`) and mount/userns `PrivateTmp=` / `ProtectSystem=` (`226/NAMESPACE`) removed from defaults (mount-namespace isolation remains opt-in in `celina-strict-fs.conf`) and `StartLimit*` placed cleanly in `[Unit]`.
- **Root Drop-In Permission Integrity (`core/common.sh`, `features/celina/install.sh`):** `copy_root_file()` and `write_root_file()` explicitly enforce `0755` directories and `0644` files under `umask 077` (healing stale `0700` drop-in dirs so `systemd-resolved`, `systemd-sleep`, and non-root health checks always read `/etc/systemd/*.d/*.conf` cleanly), and `/etc/systemd/system/ollama.service` is explicitly `chmod 644`.
- **Fail-Closed Evidence Ledger Anchor (`celina_pkg/evidence.py`):** External `0600` anchor in `~/.config/celina/evidence_anchors/` verified on open and `verify_chain()`; refuses startup (`EvidenceIntegrityError`) on ledger deletion, truncation, or tip tampering.
- **Durable `TaskGraph` Crash Recovery (`celina_pkg/planner/task_graph.py`):** Synchronous WAL commit + `PRAGMA wal_checkpoint(PASSIVE)` on every `mark()`, plus automatic `recover_on_startup()` in `AutonomyEngine` resetting stuck `'running'` agent tasks back to `'pending'` after a crash.
- **Gear 0 Eval-Free AST Math (`celina_pkg/router.py`):** Evaluates `sqrt(144)`, `what is 12 * 12?`, `2^8`, trig/log expressions deterministically in `0` LLM tokens with zero `eval()` and exponent DoS guards.
- **macOS Muscle Memory & Ubuntu 26.04 Cross-Tool Contracts (`celina_pkg/macos_glue.py`):**
  - `keyd` macOS Cmd layer (`leftmeta`/`rightmeta`), Caps Lock tap-Shift / hold-CapsLock timeout macro, terminal `Ctrl+Insert` / `Shift+Insert` / `Shift+Delete` & `Ctrl+Shift+C/V` preserving physical `Ctrl+C == SIGINT`.
  - Fixed 4-workspace Spaces (`dynamic-workspaces false`), `WorkspaceMemoryManager`, `FocusModeController` (`show-banners=false` + per-app filtering), diff-gated 10-minute `celina-auto-theme.sh`, `celina-borg-backup.timer` (`RandomizedDelaySec=3h`) + `BackupHealthMonitor` (`> 48h` Dynamic Island badge), and Ubuntu 26.04 `gnome-sushi >= 50.0` version gate.
- **Documentation & Boundaries:** Ships with [`LIMITATIONS.md`](../LIMITATIONS.md) (Apple SEP/ECID ceiling & Secure Boot MOK scope), [`COMPATIBILITY.md`](../COMPATIBILITY.md), [`CHANGELOG.md`](../CHANGELOG.md), and [`docs/v4.5-roadmap.md`](../docs/v4.5-roadmap.md).

---

## Upgrading from `v4.3` → `v4.4`

```bash
# 1. Backup Celina state independently before upgrading
celina-export ~/celina-pre-upgrade.tar.gz

# 2. Preview exact schema, extension & drop-in diff (zero writes)
./install.sh --dry-run --upgrade

# 3. Apply idempotently in-place
./install.sh --upgrade

# 4. Verify health
./install.sh --doctor
./install.sh --verify
```

---

## Final Real-Hardware Gate Before Tagging `v4.4.0`

Run on a clean **8 GB Ubuntu 26.04** laptop or KVM VM:

```bash
# 1. Fresh-user clean-$HOME install (tarball)
sudo useradd -m -s /bin/bash testuser && sudo passwd -d testuser
su - testuser -c 'cd ~ && tar -xzf /path/to/macubuntu-v4.4.0.tar.gz && cd macubuntu-v4.4.0 && ./install.sh --dry-run && ./install.sh --fail-fast --profile default-8gb && ./tools/doctor.sh'

# 2. Clean VM .deb verification
sudo apt install ./macubuntu_4.4.0_all.deb
systemctl --user is-active celina-eventd.service

# 3. All 3 hardened 8 GB hardware stability gates (OOM isolation, 1h RSS-slope soak, 50W+50R concurrent DB under resident Ollama)
./tools/hardware-release-gate.sh --test all --minutes 60

# 4. Sign & tag after all 4 gates pass
gpg --armor --detach-sign releases/macubuntu-v4.4.0.tar.gz
gpg --armor --detach-sign releases/macubuntu_4.4.0_all.deb
git tag -s v4.4.0 -F releases/RELEASE-NOTES-v4.4.0.md
```
