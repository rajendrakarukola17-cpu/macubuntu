---
name: ubuntu-sysadmin
description: Ubuntu 26.04 LTS Wayland, GNOME, PipeWire, ZRAM, Flatpak, and systemd troubleshooting & tuning playbook.
triggers: [ubuntu, wayland, pipewire, zram, systemd, apt, flatpak, laptop slow, high cpu, battery drain, wifi issue]
---
# Ubuntu 26.04 Sysadmin Playbook (Open-Source Agent Skill)
1. **Measure Before Acting:** Call `get_system_stats`, `list_running_apps`, `diagnose_systemd_service`, `get_secret_vault_status`, or `manage_default_apps` first to identify actual RAM/ZRAM/CPU/battery, crashed service, or handler issues.
2. **8GB RAM Discipline:** Recommend ZRAM checks, closing memory-heavy Electron/browser processes, or disabling idle daemons rather than adding heavy monitoring tools.
3. **Wayland-Native Commands Only:** Use `wpctl` (PipeWire), `wl-copy`/`wl-paste`, `ydotool`, `nmcli`, `bluetoothctl`, `gio trash`, and `systemctl --user`. Never recommend broken X11-only tools on GNOME Wayland.
4. **Safe Guardrails:** Any system change via `run_shell_command` must be minimal, reversible, and explained in 1 line before asking approval.
