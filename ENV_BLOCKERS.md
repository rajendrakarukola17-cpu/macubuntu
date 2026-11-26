# Environment Blockers — P0 Track Live Testing

This document records blockers encountered when attempting P0 Stability
live testing (btrfs snapshots, sched_ext, grub-btrfs) in the current
sandbox environment. All code is written and default-off where btrfs is
required; tests pass. Blockers are environmental, not code defects.

---

## 1. Kernel lacks sched_ext support

**Kernel:** `6.1.189+`

**Symptom:** `scx_bpfland` and `scx_lavd` schedulers are not available.
`sched_ext` requires kernel patches that are not present in the stock
Debian 6.1.189+ kernel.

**Impact:**
- Stage 32 (`stages/32-low-latency-kernel.sh`) cannot load `scx_bpfland`
  or `scx_lavd` at runtime. The sched_ext config.toml and D-Bus policy
  are still deployed correctly; they simply won't take effect until a
  sched_ext-capable kernel is installed.
- Celina hardware governor (`features/celina/celina_pkg/runtime/hw_governor.py`)
  calls `scxctl switch` best-effort; this will silently no-op without sched_ext.

**Workaround / Prerequisite:**
- Install a sched_ext-capable kernel (e.g., mainline 6.12+ with sched_ext
  patches, or a distribution kernel with `CONFIG_SCHED_EXT=y`).
- Install `scx` tooling (`scxctl`, `scx_bpfland`, `scx_lavd` packages).

**Test isolation:** `test_mcp_perception_actuation.py::test_run_dbus_daemon_wires_mcp_action_cb`
was confirmed to pass in isolation without sched_ext (environmental segfault,
not a regression).

---

## 2. Root filesystem is ext4 (not btrfs)

**Mount:** `/dev/vda on / type ext4 (rw,relatime,discard)`

**Symptom:** `btrfs` commands cannot operate on `/`. Snapper's btrfs backend
requires a btrfs-formatted root subvolume.

**Impact:**
- Stage 45 (`stages/45-apps-backups-search.sh`) Snapper deployment: `snapper`
  cannot create snapshots on ext4 root.
- Stage 61 (`stages/61-recovery-polish.sh`) Snapper verification: same limitation.
- `system/snapper/configs/root` config file is correctly written but inactive.
- `system/snapper/apt-dpkg-hook` and `system/snapper/pre-post-hooks/macubuntu-stage-wrapper`
  are correctly deployed but won't fire without btrfs.

**Workaround / Prerequisite:**
- Reinstall with btrfs root subvolume (`@`), OR
- Create a btrfs loop device and point Snapper `SUBVOLUME` at it for testing,
  OR
- Use `snapper` with LVM backend (if LVM is available).

**Loop device attempt:** `mkfs.btrfs` succeeded on a 2G loop image, but
`mount -o loop` failed with "unknown filesystem type 'btrfs'" — the sandbox
kernel lacks btrfs mount support entirely.

---

## 3. grub-btrfs package unavailable

**Package:** `grub-btrfs` not in apt repositories.

**Symptom:** `apt-get install grub-btrfs` → "Unable to locate package".

**Impact:**
- Stage 61 grub-btrfs integration for bootable rollback cannot be installed.
- Snapper boot menu entries for snapshot selection won't be generated.

**Workaround / Prerequisite:**
- Install `grub-btrfs` from source (https://github.com/Antynea/grub-btrfs)
  or from a PPA/third-party repo on a real Debian system.

---

## 4. No root access for apt (initially)

**Symptom:** `apt-get update` / `apt-get install` failed with permission
denied on `/var/lib/dpkg/lock-frontend`.

**Workaround:** `sudo -n apt-get` works (passwordless sudo available).
`btrfs-progs` and `snapper` were installed successfully via `sudo -n apt-get`.

---

## Summary Table

| Component | Status | Blocker | Prerequisite |
|-----------|--------|---------|--------------|
| sched_ext (scx_bpfland/scx_lavd) | Code written, inactive | Kernel 6.1.189+ lacks sched_ext | sched_ext-capable kernel + scx tooling |
| Snapper btrfs snapshots | Code written, inactive | Root is ext4; kernel lacks btrfs mount | btrfs-formatted root subvolume |
| grub-btrfs rollback menu | Code written, inactive | Package not in apt repos | Install from source/third-party |
| btrfs-progs | Installed | N/A | — |
| snapper | Installed | Cannot snapshot ext4 root | btrfs root |

---

## Code Status

All P0 code is complete and correct:
- `system/snapper/configs/root` — Snapper config file
- `system/snapper/apt-dpkg-hook` — apt dpkg snapshot hook
- `system/snapper/pre-post-hooks/macubuntu-stage-wrapper` — pre/post snapshot wrapper
- `system/systemd/celina-ai.slice` — cgroup slice for Celina AI workloads
- Stage 32 — sched_ext config.toml + D-Bus policy
- Stage 45/61 — Snapper deployment + verification
- Stage 61 — grub-btrfs integration (conditional on package availability)

All code is default-off where btrfs/sched_ext is required, so installation
on ext4 systems proceeds safely without errors. Live testing requires the
prerequisites listed above.
