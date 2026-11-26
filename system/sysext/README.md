# macubuntu systemd-sysext Layout — OS Immutability Layer

## Overview

Instead of mutating Ubuntu's `/usr` and `/etc` in place across 18 install
stages, macubuntu packages its custom binaries, scripts, and configs as a
read-only **systemd-sysext** OverlayFS extension image. The sysext is merged
at runtime via `systemd-sysext merge`, making the entire macubuntu layer
active without touching the host root filesystem. `systemd-sysext unmerge`
instantly reverts the OS to stock Ubuntu — no reboot, no filesystem rollback
needed.

This is the closest commodity-hardware equivalent to Apple's Sealed System
Volume.

## Directory Layout

```
system/sysext/
├── macubuntu.raw/              # Sysext image directory (merged at runtime)
│   ├── usr/
│   │   ├── bin/                # Custom binaries (celina triggers, wrappers)
│   │   ├── lib/macubuntu/      # Probe library, shared helpers
│   │   │   └── hw_detect.sh
│   │   └── share/macubuntu/    # Configs, themes, icons, data files
│   │       ├── themes/
│   │       ├── icons/
│   │       └── configs/
│   ├── etc/
│   │   ├── macubuntu/          # macubuntu config (hardware.json, manifest)
│   │   └── systemd/system/     # System service units
│   └── var/
│       └── lib/macubuntu/      # Persistent state (CELINA DB, skills, vault)
├── merge-sysext.sh             # Merge the sysext into the running OS
├── unmerge-sysext.sh           # Unmerge the sysext (revert to stock Ubuntu)
└── README.md                   # This file
```

## How It Works

### Stage 00 (Install/Merge)
1. Installs all macubuntu files into `system/sysext/macubuntu.raw/` instead of
   directly into `/usr` and `/etc`
2. Copies the sysext image to `/usr/lib/extensions/macubuntu.raw/`
3. Runs `systemd-sysext merge macubuntu` to activate the OverlayFS layer
4. All subsequent stages read from the merged overlay — nothing is written to
   `/usr` or `/etc` directly

### Stage 61 (Recovery/Unmerge)
1. Provides `systemd-sysext unmerge macubuntu` to instantly remove the layer
2. The OS reverts to stock Ubuntu state immediately
3. No reboot, no btrfs snapshot rollback needed

### Verification
```bash
systemd-sysext status          # Shows active/inactive sysexts
systemd-sysext list            # Lists all available sysext images
```

## Security Properties

- **Read-only overlay**: The sysext is mounted read-only; no runtime mutation
  of macubuntu system files is possible
- **Atomic unmerge**: `systemd-sysext unmerge` removes the entire layer in one
  operation — no partial state
- **No rootfs pollution**: Ubuntu's `/usr`, `/etc`, `/var` remain pristine;
  all macubuntu files live in the OverlayFS layer
- **Fail-closed**: If `systemd-sysext` is unavailable, stages fall back to
  direct installation (with a warning)

## Integration with Existing Stages

| Stage | sysext Integration |
|-------|-------------------|
| 00    | Populates sysext image, runs `systemd-sysext merge` |
| 61    | Provides `systemd-sysext unmerge` option |
| 99    | Verifies sysext is merged via `systemd-sysext status` |

## Requirements

- `systemd-sysext` package (part of `systemd` on Ubuntu 26.04)
- Kernel with OverlayFS support (standard on Ubuntu 26.04)
- Root privileges for merge/unmerge operations

## Limitations

- sysext images are read-only; runtime-writable state (Celina DB, skills,
  vault) still lives under `~/.local/share/celina/` and `/run/user/$UID/celina/`
- Not all system files are suitable for sysext (e.g., files that must be
  edited by other system tools). Those continue to use P5 atomic writes via
  `write_root_file()`/`copy_root_file()`.
