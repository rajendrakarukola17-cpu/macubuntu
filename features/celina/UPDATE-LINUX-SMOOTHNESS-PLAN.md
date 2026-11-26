# Linux Desktop Smoothness Plan — for Future Celina Update

**Source:** User-provided guide (macOS-style smoothness on Linux)
**Status:** Saved for future implementation — not yet applied
**Goal:** Eliminate variance and protect the foreground; replicate macOS smoothness mechanisms on Linux

---

## Core Philosophy

> macOS doesn't feel smooth because it's faster. It feels smooth because **frame times never vary** and **foreground work always wins**.

The software goal: **eliminate variance and protect the foreground.**

---

## Step 0 — Measure Variance (not averages)

```bash
cat /proc/pressure/cpu /proc/pressure/io /proc/pressure/memory
sudo apt install mesa-utils
glxinfo -B | grep -E 'OpenGL renderer|Device'
echo "$XDG_SESSION_TYPE"; systemd-detect-virt
cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null
```

- `some avg10` above ~10 on memory or IO pressure = stalling (stutter)
- `llvmpipe` renderer = CPU rendering — fix driver first, nothing else helps

---

## Rank 1 — Compositor & Display Path

| Action | Command / Setting |
|--------|------------------|
| Wayland session | Verify `echo $XDG_SESSION_TYPE` → `wayland` |
| Max refresh rate | Settings → Displays → highest Hz |
| VRR | `gsettings set org.gnome.mutter experimental-features "['variable-refresh-rate']"` |
| Avoid fractional scaling | 100% or 200% + `text-scaling-factor` |
| Keep animations ON | Consistency > speed |
| Skip blur effects | Adds GPU cost per frame |
| Force native Wayland | `MOZ_ENABLE_WAYLAND=1`, `ELECTRON_OZONE_PLATFORM_HINT=wayland` |

Create `~/.config/environment.d/90-wayland.conf` with those two env vars, then log out/in.

Verify: Firefox `about:support` → Window Protocol = wayland; Chromium `chrome://gpu`.

---

## Rank 2 — CPU Ramp (Instant P-core feeling)

```bash
powerprofilesctl set balanced  # or performance on AC
cat /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference
```

- `balance_power` or `power` = laggy ramp
- On AC, `balance_performance` = near-instant ramp

⚠️ **Do not install tuned or TLP** — conflicts with `power-profiles-daemon`.

Advanced (kernel 6.12+): `sched_ext` with `scx_lavd` for interactive latency:
```bash
uname -r; command -v scx_lavd && echo available
```

---

## Rank 3 — Foreground Always Wins (App Nap equivalent)

Deprioritize all system background services:
```bash
sudo mkdir -p /etc/systemd/system/system.slice.d
sudo tee /etc/systemd/system/system.slice.d/background.conf >/dev/null <<'EOF'
[Slice]
CPUWeight=50
IOWeight=50
EOF
```

Boost user/app slice:
```bash
sudo mkdir -p /etc/systemd/system/user.slice.d
sudo tee /etc/systemd/system/user.slice.d/foreground.conf >/dev/null <<'EOF'
[Slice]
CPUWeight=200
IOWeight=200
EOF
sudo systemctl daemon-reload
```

Timer coalescing for background services (tracker-miner-fs, packagekit, fwupd, snapd, unattended-upgrades):
```bash
sudo tee /etc/systemd/system/tracker-miner-fs-3.service.d/nap.conf >/dev/null <<EOF
[Service]
Nice=19
IOSchedulingClass=idle
CPUWeight=1
IOWeight=1
TimerSlackNSec=50ms
MemoryHigh=64M
EOF
sudo systemctl daemon-reload
```

Verify with `systemd-cgtop`.

---

## Rank 4 — Never Stall on Memory

```bash
sudo apt install zram-tools
sudo tee /etc/default/zramswap >/dev/null <<'EOF'
ALGO=lz4
PERCENT=50
PRIORITY=100
EOF
sudo systemctl restart zramswap && zramctl
```

Sysctl settings:
```bash
sudo tee /etc/sysctl.d/99-smooth.conf >/dev/null <<'EOF'
vm.swappiness = 150
vm.page-cluster = 0
vm.vfs_cache_pressure = 100
vm.watermark_scale_factor = 150
EOF
sudo sysctl --system
```

- `watermark_scale_factor=150` starts background reclaim earlier → avoids direct reclaim blocking foreground

MGLRU check:
```bash
cat /sys/kernel/mm/lru_gen/enabled   # if 0: echo y | sudo tee /sys/kernel/mm/lru_gen/enabled
```

Cap RAM hogs:
```bash
sudo tee /etc/systemd/system/user@.service.d/browser-cap.conf >/dev/null <<'EOF'
[Service]
MemoryHigh=2G
EOF
```

Keep systemd-oomd; don't add earlyoom.

---

## Rank 5 — Protect Foreground I/O

```bash
lsblk -o NAME,MAJ:MIN,MOUNTPOINT
# if io.latency exists:
echo "259:0 target=25" | sudo tee /sys/fs/cgroup/user.slice/io.latency
```

`io.latency` guarantees I/O within 25ms even while background hammers disk. Fallback: `IOWeight` + `IOSchedulingClass=idle`.

---

## Rank 6 — Kill Background Storms

```bash
systemctl list-timers --all
```

Reschedule (don't disable security updates):
```bash
sudo systemctl edit apt-daily.timer    # [Timer] OnCalendar=weekly RandomizedDelaySec=1h
snap set system refresh.timer=fri,23:00~01:00
```

Tame Tracker3 indexer:
```bash
gsettings set org.freedesktop.Tracker3.Miner.Files ignored-directories \
 "['po','CVS','core-dumps','lost+found','node_modules','target','.venv','__pycache__','.git','.cache','build','dist']"
gsettings set org.freedesktop.Tracker3.Miner.Files index-on-battery false
gsettings set org.freedesktop.Tracker3.Miner.Files throttle 15
```

---

## Rank 7 — Offload Video to GPU

```bash
sudo apt install vainfo
vainfo | head -20
```
- Intel Gen8+: `intel-media-driver`
- AMD: `mesa-va-drivers`

Firefox `about:support` → HARDWARE_VIDEO_DECODING. Enable Memory Saver / tab unloading.

---

## Rank 8 — App Launch

| Do | Why |
|----|-----|
| Native `.deb` over Snap/Flatpak for daily apps | squashfs + runtime init adds 1–3s cold |
| Don't drop caches | Warm page cache = dyld-shared-cache equivalent |
| Skip `preload` | Consumes RAM, ~zero gain on NVMe |
| Fewer browser extensions, fewer Electron apps | Electron = 300MB–1GB per app |

Measure: `hyperfine --warmup 1 'firefox --new-window about:blank'`

---

## What Software Cannot Fix

| Limit | Reality |
|-------|---------|
| 60 Hz panel | Can't display 120 fps |
| VM without GPU accel | Compositor smoothness capped by virtual GPU; need virgl/virtio-gpu or passthrough |
| llvmpipe / no driver | CPU rendering — fix driver first |
| Thermal throttling | Software can't add cooling |
| Single-channel RAM | iGPU shares bandwidth |
| Electron app weight | Architectural |
| NVIDIA proprietary + Wayland | Still edge cases |

---

## Tie-in to Celina — Gear 0 QoS

Three things to carry over to Celina:

### 1. Protect Gear 0's resident pages

```bash
sudo tee /etc/systemd/system/celina.service.d/resident.conf >/dev/null <<'EOF'
[Service]
MemoryLow=256M
MemoryMin=128M
LimitMEMLOCK=infinity
EOF
```

Without this, sub-20ms Gear 0 reflexes pay a decompression fault when pages get reclaimed.

### 2. MemorySwapMax=0 on model unit

LLM weights in zram is catastrophic for latency.

### 3. Exclude noise from indexer

Exclude `__pycache__`, `target/`, `.venv` from Tracker3 — otherwise every `apply-update.sh --cleanup` and every build triggers a background storm.

---

## Order to Apply

**0 → 1 → 2 → 3** gives ~80% of the result. Re-run PSI check after each:

```bash
cat /proc/pressure/memory /proc/pressure/io
```

If `some avg10` doesn't drop, that change wasn't the bottleneck — revert it and move on.

---

## Diagnostic to Run First

Send these six lines to determine which ranks apply:

```bash
lsb_release -d; free -h; echo $XDG_SESSION_TYPE; systemd-detect-virt
glxinfo -B | grep 'OpenGL renderer'; cat /proc/pressure/memory /proc/pressure/io
```

---

## Summary for Celina Integration

| Rank | Applies to Celina? | Action |
|------|-------------------|--------|
| 1 | Indirect | Desktop compositing — host-level |
| 2 | Indirect | CPU governor — host-level |
| 3 | **Yes** | `celina.service` CPUWeight/IOWeight boost |
| 4 | **Yes** | `MemoryLow`/`MemoryMin` for celina service |
| 5 | Indirect | I/O latency — host-level |
| 6 | Indirect | Background timers — host-level |
| 7 | Indirect | GPU video decode — host-level |
| 8 | Indirect | App launch — host-level |

**Immediate Celina actions:** systemd service overrides for MemoryLow/MemoryMin/CPUWeight/IOWeight, plus Tracker3 exclusions for `__pycache__`, `target/`, `.venv`.
