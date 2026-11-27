"""Celina AI — OS Event Bus, Focus Modes & Automatic cgroup v2 App Nap Daemon (P2)
Brings macOS-level invisible OS cohesion to Ubuntu 26.04 Wayland:
  1. Focus Modes (default / work / coding / sleep / presentation):
     Atomically coordinates GNOME Do-Not-Disturb, Night Light, and power profile.
  2. Automatic cgroup v2 App Nap Policy:
     Safely evaluates whether a background app may be throttled/frozen:
       - NEVER touches critical OS processes, terminals, password managers, or Celina
       - NEVER touches the active foreground window
       - NEVER freezes an app currently producing/capturing PipeWire audio/mic streams
       - Only acts after configurable idle_seconds occlusion threshold
  3. Live Privacy & Battery Event Monitor (`--daemon` for celina-eventd.service):
     Tracks mic/camera/screen-share privacy dots and battery thresholds at <1% CPU.
"""

from __future__ import annotations

import argparse
import atexit
import datetime
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import (
    approval,
    automation,
    command_runner,
    config,
    ios_ancs,
    privacy,
    spotlight_index,
)

ApprovalCallback = Callable[[str, str], bool]

WATCHED_USER_SERVICES: tuple[str, ...] = (
    "uxplay-user.service",
    "syncthing.service",
    "shairport-sync-user.service",
    "celina-ancs.service",
    "celina-eventd.service",
    "weylus.service",
)

_MIME_ALIASES: dict[str, str] = {
    "web": "x-scheme-handler/https",
    "browser": "x-scheme-handler/https",
    "https": "x-scheme-handler/https",
    "http": "x-scheme-handler/http",
    "pdf": "application/pdf",
    "mail": "x-scheme-handler/mailto",
    "email": "x-scheme-handler/mailto",
    "files": "inode/directory",
    "finder": "inode/directory",
    "folder": "inode/directory",
    "text": "text/plain",
    "image": "image/png",
    "video": "video/mp4",
    "audio": "audio/mpeg",
}

_HW_PARENT = str(Path(__file__).resolve().parent.parent)
if _HW_PARENT not in sys.path:
    sys.path.insert(0, _HW_PARENT)
try:
    import celina_hardware as _celina_hardware
except Exception:
    _celina_hardware = None  # type: ignore[assignment]


FOCUS_STATE_FILE = config.BASE_DIR / "focus_mode.json"


@dataclass(frozen=True)
class FocusProfile:
    name: str
    show_banners: bool
    night_light: bool | None
    power_profile: str | None
    description: str


FOCUS_PROFILES: dict[str, FocusProfile] = {
    "default": FocusProfile(
        name="default",
        show_banners=True,
        night_light=None,
        power_profile="balanced",
        description="Standard macOS-like desktop mode (notifications enabled, balanced power).",
    ),
    "work": FocusProfile(
        name="work",
        show_banners=False,
        night_light=None,
        power_profile="balanced",
        description="Deep Work Focus: silences distraction banners while keeping balanced performance.",
    ),
    "coding": FocusProfile(
        name="coding",
        show_banners=False,
        night_light=False,
        power_profile="performance",
        description="Coding Focus: Do-Not-Disturb enabled + high-performance CPU profile.",
    ),
    "presentation": FocusProfile(
        name="presentation",
        show_banners=False,
        night_light=False,
        power_profile="performance",
        description="Presentation Focus: blocks all popup notifications and disables Night Light tint.",
    ),
    "sleep": FocusProfile(
        name="sleep",
        show_banners=False,
        night_light=True,
        power_profile="power-saver",
        description="Sleep / Wind-Down Focus: Do-Not-Disturb + warm Night Light + power-saver.",
    ),
}


RunnerFn = Callable[[list[str]], command_runner.CommandResult]


class FocusModeManager:
    """Applies and persists macOS-style Focus Modes cleanly across GNOME & power-profiles-daemon."""

    def __init__(
        self,
        state_path: Path = FOCUS_STATE_FILE,
        runner: RunnerFn | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ):
        self.state_path = Path(state_path)
        self._run = runner or (lambda cmd: command_runner.run_command(cmd, timeout=4.0))
        self._which = which

    def get_current(self) -> str:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("mode") in FOCUS_PROFILES:
                    return str(data["mode"])
            except Exception:
                pass
        return "default"

    def set_mode(self, mode: str) -> str:
        key = (mode or "").strip().lower()
        if key not in FOCUS_PROFILES:
            return f"Error: Unknown focus mode '{mode}'. Choose from: {', '.join(sorted(FOCUS_PROFILES))}."

        prof = FOCUS_PROFILES[key]
        applied: list[str] = []
        warnings: list[str] = []

        if self._which("gsettings"):
            r1 = self._run([
                "gsettings", "set", "org.gnome.desktop.notifications",
                "show-banners", "true" if prof.show_banners else "false",
            ])
            if r1.ok:
                applied.append(f"DND={'OFF' if prof.show_banners else 'ON'}")
            else:
                warnings.append(f"notifications: {r1.stderr}")

            if prof.night_light is not None:
                r2 = self._run([
                    "gsettings", "set", "org.gnome.settings-daemon.plugins.color",
                    "night-light-enabled", "true" if prof.night_light else "false",
                ])
                if r2.ok:
                    applied.append(f"NightLight={'ON' if prof.night_light else 'OFF'}")
                else:
                    warnings.append(f"night-light: {r2.stderr}")

        if prof.power_profile and self._which("powerprofilesctl"):
            r3 = self._run(["powerprofilesctl", "set", prof.power_profile])
            if r3.ok:
                applied.append(f"Power={prof.power_profile}")
            else:
                warnings.append(f"power: {r3.stderr}")

        try:
            config.atomic_write_private_text(
                self.state_path,
                json.dumps({"mode": prof.name, "updated_at": time.time()}, indent=2) + "\n",
            )
        except OSError as exc:
            return f"Error: Could not persist focus mode '{prof.name}': {exc}"

        summary = f"Focus Mode set to '{prof.name}': {prof.description}"
        if applied:
            summary += f"\nApplied: {', '.join(applied)}"
        if warnings:
            summary += f"\nNotes: {'; '.join(warnings)}"
        return summary


# --- Automatic cgroup v2 App Nap Policy (macOS App Nap equivalent) ---
_AUTO_NAP_NEVER_TOUCH: frozenset[str] = frozenset({
    "systemd", "gnome-shell", "gnome-session", "mutter", "xwayland", "dbus-daemon", "dbus-broker",
    "pipewire", "wireplumber", "polkitd", "celina", "celly", "python3", "bash", "zsh", "sh",
    "gnome-terminal", "gnome-terminal-server", "ptyxis", "kgx", "kitty", "alacritty", "konsole",
    "keepassxc", "bitwarden", "1password", "seahorse", "gpg-agent", "ssh-agent",
    "syncthing", "localsend", "shairport-sync", "uxplay", "deskflow", "lan-mouse",
})


@dataclass(frozen=True)
class AppNapDecision:
    should_nap: bool
    reason: str


class AutoAppNapPolicy:
    """Pure decision engine mirroring macOS App Nap assertions:
    Never freezes protected/critical apps, the focused app, audio-active apps,
    or apps occluded for less than idle_threshold_sec.
    """

    def __init__(self, idle_threshold_sec: float = 180.0):
        self.idle_threshold_sec = max(30.0, float(idle_threshold_sec))

    def evaluate(
        self,
        *,
        app_name: str,
        active_app: str,
        occluded_seconds: float,
        active_audio_apps: set[str],
        has_active_assertion: bool = False,
    ) -> AppNapDecision:
        low = (app_name or "").strip().lower()
        if not low:
            return AppNapDecision(False, "Empty application name.")
        if low in _AUTO_NAP_NEVER_TOUCH or any(tok in low for tok in ("celina", "gnome-shell", "terminal", "keepass", "bitwarden", "polkit")):
            return AppNapDecision(False, f"Protected process '{app_name}' is never auto-napped.")
        if low == (active_app or "").strip().lower():
            return AppNapDecision(False, f"'{app_name}' is the focused foreground app.")
        if any(low in a.lower() or a.lower() in low for a in active_audio_apps if a):
            return AppNapDecision(False, f"'{app_name}' holds an active PipeWire audio/media assertion.")
        if has_active_assertion:
            return AppNapDecision(False, f"'{app_name}' holds an active background task assertion.")
        if occluded_seconds < self.idle_threshold_sec:
            return AppNapDecision(
                False,
                f"Occluded {int(occluded_seconds)}s < threshold {int(self.idle_threshold_sec)}s.",
            )
        return AppNapDecision(True, f"Eligible for safe cgroup v2 App Nap (idle {int(occluded_seconds)}s).")


class CgroupFreezer:
    """Freezes/thaws a user systemd app cgroup v2 cleanly via cgroup.freeze (no blind pkill!)."""

    def __init__(self, cgroup_root: Path = Path("/sys/fs/cgroup")):
        self._root = cgroup_root

    def find_app_cgroup(self, pid: int) -> Path | None:
        cg_file = Path(f"/proc/{pid}/cgroup")
        if not cg_file.exists():
            return None
        try:
            for ln in cg_file.read_text(encoding="utf-8").splitlines():
                if ln.startswith("0::"):
                    rel = ln[3:].lstrip("/")
                    full = self._root / rel
                    if (full / "cgroup.freeze").exists():
                        return full
        except OSError:
            pass
        return None

    def set_frozen(self, cgroup_dir: Path, frozen: bool) -> bool:
        freeze_file = cgroup_dir / "cgroup.freeze"
        if not freeze_file.exists():
            return False
        try:
            freeze_file.write_text("1\n" if frozen else "0\n", encoding="utf-8")
            return freeze_file.read_text(encoding="utf-8").strip() == ("1" if frozen else "0")
        except OSError:
            return False


class DynamicThemeScheduler:
    """macOS Dynamic Desktop (Auto Light <-> Dark Appearance at Sunrise/Sunset).
    Zero network calls: uses configurable local sunrise/sunset hours (default 06:30 -> 18:30)
    and only invokes gsettings when the target GNOME color-scheme actually changes.
    """

    def __init__(
        self,
        *,
        sunrise_hour: float = 6.5,
        sunset_hour: float = 18.5,
        runner: Callable[[list[str]], command_runner.CommandResult] = command_runner.run_command,
        which: Callable[[str], str | None] = shutil.which,
    ):
        self.sunrise_hour = float(sunrise_hour)
        self.sunset_hour = float(sunset_hour)
        self._run = runner
        self._which = which
        self._last_scheme: str | None = None

    def desired_scheme(self, now: datetime.datetime | None = None) -> str:
        t = now or datetime.datetime.now()
        h = t.hour + (t.minute / 60.0)
        return "default" if self.sunrise_hour <= h < self.sunset_hour else "prefer-dark"

    def apply_if_needed(self, now: datetime.datetime | None = None) -> str:
        target = self.desired_scheme(now=now)
        if target == self._last_scheme:
            return target
        if self._which("gsettings"):
            res = self._run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", f"'{target}'"])
            if res.ok:
                self._last_scheme = target
        else:
            self._last_scheme = target
        return target


class AutoAppNapCoordinator:
    """Live occlusion tracker connecting AutoAppNapPolicy + CgroupFreezer safely:
    - Tracks how long each non-active user GUI app has been out of focus
    - Auto-thaws immediately when an app regains foreground focus OR starts PipeWire audio
    - Attempts thaw_all() on shutdown / SIGTERM; failed thaws remain tracked for retry
      while this coordinator is alive. A persistent failure or process exit can still
      leave a cgroup frozen, so this is not a guarantee of recovery.
    """

    def __init__(
        self,
        policy: AutoAppNapPolicy | None = None,
        freezer: CgroupFreezer | None = None,
    ):
        self.policy = policy or AutoAppNapPolicy()
        self.freezer = freezer or CgroupFreezer()
        self._occluded_since: dict[int, tuple[str, float]] = {}
        self._frozen_cgroups: dict[int, tuple[str, Path]] = {}

    @property
    def napped_apps(self) -> list[str]:
        return sorted({name for name, _ in self._frozen_cgroups.values()})

    def thaw_all(self) -> int:
        thawed = 0
        for pid, (_name, cg) in list(self._frozen_cgroups.items()):
            if self.freezer.set_frozen(cg, False):
                thawed += 1
                self._frozen_cgroups.pop(pid, None)
                self._occluded_since.pop(pid, None)
        return thawed

    def step(
        self,
        *,
        active_app: str,
        active_audio_apps: set[str],
        candidate_pids: dict[int, str] | None = None,
        now: float | None = None,
    ) -> list[str]:
        ts = now if now is not None else time.time()
        active_low = (active_app or "").strip().lower()

        # 1. Immediately thaw any frozen app that regained focus or started audio
        for pid, (name, cg) in list(self._frozen_cgroups.items()):
            nlow = name.lower()
            has_audio = any(nlow in a.lower() or a.lower() in nlow for a in active_audio_apps if a)
            if (active_low and nlow == active_low) or has_audio:
                if self.freezer.set_frozen(cg, False):
                    self._frozen_cgroups.pop(pid, None)
                    self._occluded_since.pop(pid, None)

        # 2. Evaluate background candidates
        for pid, name in (candidate_pids or {}).items():
            if pid in self._frozen_cgroups:
                continue
            nlow = name.lower()
            if active_low and nlow == active_low:
                self._occluded_since.pop(pid, None)
                continue
            first_seen = self._occluded_since.get(pid)
            if first_seen is None:
                self._occluded_since[pid] = (name, ts)
                continue
            dec = self.policy.evaluate(
                app_name=name,
                active_app=active_app,
                occluded_seconds=max(0.0, ts - first_seen[1]),
                active_audio_apps=active_audio_apps,
            )
            if dec.should_nap:
                cg = self.freezer.find_app_cgroup(pid)
                if cg is not None and self.freezer.set_frozen(cg, True):
                    self._frozen_cgroups[pid] = (name, cg)
        return self.napped_apps


class OSEventMonitor:
    """Unified event daemon core (`celina-eventd.service`):
      - Hardware privacy dots (mic/cam/screen-share) & battery state
      - Active Focus Mode & optional Dynamic Light/Dark Appearance
      - Live iOS BLE ANCS status
      - Optional opt-in cgroup v2 Auto App Nap (`CELINA_AUTO_APP_NAP=1`)
      - Periodic background CoreSpotlight FTS5 index refresh (every 15m)
    Writes a compact 0600 JSON snapshot read event-driven by Dynamic Island UI.
    """

    def __init__(
        self,
        state_file: Path = config.BASE_DIR / "live_os_events.json",
        theme_scheduler: DynamicThemeScheduler | None = None,
        app_nap: AutoAppNapCoordinator | None = None,
        enable_dynamic_appearance: bool | None = None,
        enable_auto_app_nap: bool | None = None,
    ):
        self.state_file = Path(state_file)
        self.theme_scheduler = theme_scheduler or DynamicThemeScheduler()
        self.app_nap = app_nap or AutoAppNapCoordinator()
        self.enable_dynamic_appearance = (
            enable_dynamic_appearance
            if enable_dynamic_appearance is not None
            else (os.environ.get("CELINA_DYNAMIC_APPEARANCE", config.ENV.get("CELINA_DYNAMIC_APPEARANCE", "0")) in {"1", "true", "yes"})
        )
        self.enable_auto_app_nap = (
            enable_auto_app_nap
            if enable_auto_app_nap is not None
            else (os.environ.get("CELINA_AUTO_APP_NAP", config.ENV.get("CELINA_AUTO_APP_NAP", "0")) in {"1", "true", "yes"})
        )
        self._last_spotlight_refresh: float = 0.0
        self._lock = threading.Lock()
        self._latest_usb_alert: dict[str, Any] | None = None
        self._dbus_failed_units: set[str] = set()
        self._slow_poll_ttl_sec: float = 60.0
        self._last_slow_poll_ts: float = 0.0
        self._cached_failed_svcs: list[str] = []
        self._cached_vault_info: dict[str, Any] = {"available": False, "collections": [], "any_locked": None}

    def record_usb_alert(self, label: str, size_bytes: int, mount_point: str = "") -> dict[str, Any]:
        alert = format_usb_drive_alert(label=label, size_bytes=size_bytes, mount_point=mount_point)
        with self._lock:
            self._latest_usb_alert = alert
        return alert

    def record_failed_unit(self, unit_name: str, failed: bool = True) -> None:
        clean = (unit_name or "").strip()
        if not clean:
            return
        with self._lock:
            if failed:
                self._dbus_failed_units.add(clean)
            else:
                self._dbus_failed_units.discard(clean)

    def maybe_refresh_spotlight(self, *, interval_sec: float = 900.0, now: float | None = None) -> int:
        ts = now if now is not None else time.time()
        if ts - self._last_spotlight_refresh < interval_sec:
            return 0
        self._last_spotlight_refresh = ts
        idx = spotlight_index.SpotlightIndex()
        try:
            count = idx.refresh()
            # Incrementally refresh semantic document chunks ONLY on AC power with >=2 GB free RAM
            on_battery = False
            if _celina_hardware is not None:
                try:
                    on_battery = bool(_celina_hardware.get_hardware().power.get_state().on_battery)
                except Exception:
                    on_battery = False
            if not on_battery and config.detect_available_ram_mb() >= 2000:
                n_chunks = idx.index_document_chunks(max_files=60)
                if n_chunks > 0:
                    try:
                        from . import embeddings

                        embeddings.get_engine().offload()
                    except Exception:
                        pass
            return count
        except Exception:
            return 0
        finally:
            idx.close()

    def poll_once(self, *, now: datetime.datetime | None = None) -> dict[str, Any]:
        appearance = (
            self.theme_scheduler.apply_if_needed(now=now)
            if self.enable_dynamic_appearance
            else "manual"
        )
        with self._lock:
            dbus_failed = set(self._dbus_failed_units)
            usb_alert = dict(self._latest_usb_alert) if self._latest_usb_alert else None
            # Auto-expire transient USB banner after 120s so pill stays clean
            if usb_alert and (time.time() - float(usb_alert.get("timestamp", 0.0)) > 120.0):
                self._latest_usb_alert = None
                usb_alert = None
        now_mono = time.monotonic()
        if (self._last_slow_poll_ts == 0.0) or ((now_mono - self._last_slow_poll_ts) >= self._slow_poll_ttl_sec):
            self._cached_failed_svcs = get_failed_user_services()
            self._cached_vault_info = inspect_secret_service_lock_state()
            self._last_slow_poll_ts = now_mono
        failed_svcs = sorted(set(self._cached_failed_svcs) | dbus_failed)
        vault_info = self._cached_vault_info

        payload: dict[str, Any] = {
            "timestamp": time.time(),
            "focus_mode": FocusModeManager().get_current(),
            "appearance_mode": appearance,
            "ancs_device": None,
            "napped_apps": self.app_nap.napped_apps,
            "failed_services": failed_svcs,
            "usb_alert": usb_alert,
            "secret_vault_locked": vault_info.get("any_locked"),
            "privacy": {"mic_active": False, "camera_active": False, "screen_share_active": False, "active_apps": []},
            "battery": {"on_battery": False, "percent": None},
        }
        active_audio_apps: set[str] = set()
        if _celina_hardware is not None:
            hw = _celina_hardware.get_hardware()
            priv = hw.privacy.get_state()
            pwr = hw.power.get_state()
            payload["privacy"] = {
                "mic_active": priv.mic_active,
                "camera_active": priv.camera_active,
                "screen_share_active": priv.screen_share_active,
                "active_apps": list(priv.active_apps),
                "detail": priv.detail,
            }
            payload["battery"] = {
                "on_battery": pwr.on_battery,
                "percent": pwr.battery_percent,
                "profile": pwr.power_profile,
            }
            active_audio_apps = {a.split(":", 1)[-1] for a in priv.active_apps}

        if self.enable_auto_app_nap and not config.is_lite_mode():
            active_info = automation.get_active_window_info()
            active_app = str(active_info.get("app") or "")
            payload["napped_apps"] = self.app_nap.step(
                active_app=active_app,
                active_audio_apps=active_audio_apps,
            )

        try:
            config.atomic_write_private_text(self.state_file, json.dumps(payload, indent=2) + "\n")
        except OSError:
            pass
        return payload


# ============================================================================
# Deeper Ubuntu 26.04 Wayland OS Integration (v4.4):
#   1. Systemd User Unit Crash & Journal Doctor (`systemctl --user` / `journalctl`)
#   2. Freedesktop Secret Service Status Bridge (`org.freedesktop.secrets` — NEVER reads secrets!)
#   3. UDisks2 Smart External Drive / USB Guard (`org.freedesktop.UDisks2`)
#   4. MIME & Default Handler Inspector (`gio mime` / `xdg-mime`)
# ============================================================================

def get_failed_user_services(
    runner: RunnerFn | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Returns currently failed systemd --user service unit names (0 extra dependencies)."""
    if not which("systemctl"):
        return []
    run_fn = runner or (lambda cmd: command_runner.run_command(cmd, timeout=3.0))
    res = run_fn(["systemctl", "--user", "--failed", "--plain", "--no-legend", "--no-pager"])
    if not res.ok or not res.stdout.strip():
        return []
    units: list[str] = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if parts and parts[0].endswith(".service"):
            units.append(parts[0])
    return units


def diagnose_systemd_service(
    unit_name: str,
    lines: int = 20,
    *,
    runner: RunnerFn | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Systemd Crash & Journal Doctor: inspects `systemctl --user status` and recent
    `journalctl --user -u <unit>` logs with strict unit validation and secret redaction.
    """
    clean = (unit_name or "").strip()
    if not clean:
        failed = get_failed_user_services(runner=runner, which=which)
        if not failed:
            return "✅ All systemd --user services are healthy (0 failed units)."
        return "⚠️ Failed systemd --user units:\n" + "\n".join(f"  • {u}" for u in failed)

    if not clean.endswith((".service", ".timer", ".socket")):
        clean = f"{clean}.service"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,120}", clean):
        return f"Error: Invalid systemd unit name '{unit_name}'."
    if not which("systemctl"):
        return "Error: systemctl is not available on this host."

    run_fn = runner or (lambda cmd: command_runner.run_command(cmd, timeout=4.0))
    n_lines = max(5, min(60, int(lines)))

    st = run_fn(["systemctl", "--user", "status", clean, "--no-pager", "-n", "0"])
    state_txt = (st.stdout or st.stderr or st.output or "unknown").strip()
    logs_txt = ""
    if which("journalctl"):
        jr = run_fn(["journalctl", "--user", "-u", clean, "-n", str(n_lines), "--no-pager"])
        logs_txt = (jr.stdout or jr.stderr or "").strip()

    # Redact any credentials/tokens that may have appeared in service logs
    vault = privacy.RedactionVault()
    redacted_state, _ = vault.redact(state_txt[:800])
    redacted_logs, _ = vault.redact(logs_txt[-1800:])

    # Quick heuristic root-cause classification from status + recent journal lines
    combined_low = f"{state_txt}\n{logs_txt}".lower()
    hint = ""
    if "segfault" in combined_low or "core dumped" in combined_low or "sigsegv" in combined_low:
        hint = "CRITICAL: Segmentation fault / crash detected."
    elif "out of memory" in combined_low or "oom-kill" in combined_low or ("killed" in combined_low and "signal=kill" in combined_low):
        hint = "MEMORY LIMIT: Service was terminated by OOM killer."
    elif "address already in use" in combined_low or "bind failed" in combined_low:
        hint = "PORT CONFLICT: Another process is already bound to this port/socket."
    elif "permission denied" in combined_low or "operation not permitted" in combined_low:
        hint = "PERMISSION ERROR: Access or capability restriction."
    elif "no such file or directory" in combined_low or "command not found" in combined_low:
        hint = "MISSING DEPENDENCY: Required binary or config path not found."

    out = [f"🔧 Systemd Doctor — `{clean}`:"]
    if hint:
        out.append(f"• Quick Diagnosis: {hint}")
    out.append(redacted_state)
    if redacted_logs:
        out.append(f"\nRecent journal logs (last {n_lines} lines):\n{redacted_logs}")
    out.append(f"\nSuggested recovery: `systemctl --user restart {clean}`")
    return "\n".join(out)


def format_usb_drive_alert(label: str, size_bytes: int, mount_point: str = "") -> dict[str, Any]:
    """Formats a UDisks2 removable drive attachment into a compact Dynamic Island alert payload."""
    size_b = max(0, int(size_bytes or 0))
    if size_b >= 1_000_000_000_000:
        size_str = f"{size_b / 1_000_000_000_000:.1f} TB"
    elif size_b >= 1_000_000_000:
        size_str = f"{size_b / 1_000_000_000:.0f} GB"
    elif size_b >= 1_000_000:
        size_str = f"{size_b / 1_000_000:.0f} MB"
    else:
        size_str = "External Drive"
    clean_label = (label or "USB Drive").strip()[:48]
    return {
        "label": clean_label,
        "size_bytes": size_b,
        "size_human": size_str,
        "mount_point": (mount_point or "").strip(),
        "banner": f"💾 USB Connected ({clean_label}, {size_str}) — Open in Finder or Backup",
        "timestamp": time.time(),
    }


def inspect_secret_service_lock_state(
    *,
    runner: RunnerFn | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Queries Freedesktop Secret Service (`org.freedesktop.secrets`, GNOME Keyring / KeePassXC)
    STRICTLY for collection labels and Locked booleans — NEVER reads any password or secret!
    """
    if not which("gdbus"):
        return {"available": False, "collections": [], "any_locked": None, "detail": "gdbus not available"}
    run_fn = runner or (lambda cmd: command_runner.run_command(cmd, timeout=2.5))
    res = run_fn([
        "gdbus", "call", "--session",
        "--dest", "org.freedesktop.secrets",
        "--object-path", "/org/freedesktop/secrets",
        "--method", "org.freedesktop.DBus.Properties.Get",
        "org.freedesktop.Secret.Service", "Collections",
    ])
    if not res.ok:
        return {
            "available": False,
            "collections": [],
            "any_locked": None,
            "detail": "Secret Service inactive (start GNOME Keyring or enable KeePassXC Secret Service integration)",
        }
    paths = re.findall(r"/org/freedesktop/secrets/collection/[A-Za-z0-9_]+", res.stdout or "")
    collections: list[dict[str, Any]] = []
    for cpath in paths[:8]:
        r_lock = run_fn([
            "gdbus", "call", "--session",
            "--dest", "org.freedesktop.secrets",
            "--object-path", cpath,
            "--method", "org.freedesktop.DBus.Properties.Get",
            "org.freedesktop.Secret.Collection", "Locked",
        ])
        r_lbl = run_fn([
            "gdbus", "call", "--session",
            "--dest", "org.freedesktop.secrets",
            "--object-path", cpath,
            "--method", "org.freedesktop.DBus.Properties.Get",
            "org.freedesktop.Secret.Collection", "Label",
        ])
        locked = "true" in (r_lock.stdout or "").lower()
        m_lbl = re.search(r"'([^']+)'", r_lbl.stdout or "")
        lbl = m_lbl.group(1) if m_lbl else cpath.rsplit("/", 1)[-1]
        collections.append({"path": cpath, "label": lbl, "locked": locked})
    any_locked = any(c["locked"] for c in collections) if collections else False
    return {"available": True, "collections": collections, "any_locked": any_locked, "detail": "ok"}


def get_secret_vault_status(
    unlock: bool = False,
    *,
    runner: RunnerFn | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Reports GNOME Keyring / KeePassXC Secret Service (`org.freedesktop.secrets`) vault lock status
    without ever reading any stored passwords into Python. If `unlock=True` and a collection is
    locked, invokes the native Freedesktop OS unlock prompt (`org.freedesktop.Secret.Service.Unlock`).
    """
    info = inspect_secret_service_lock_state(runner=runner, which=which)
    if not info["available"]:
        return f"🔐 Secret Vault: {info['detail']}"
    cols = info["collections"]
    if not cols:
        return "🔐 Secret Vault: Connected to org.freedesktop.secrets (no collections registered yet)."

    lines = ["🔐 Freedesktop Secret Vault Status (GNOME Keyring / KeePassXC — zero secrets read):"]
    locked_paths: list[str] = []
    for c in cols:
        icon = "🔒 LOCKED" if c["locked"] else "🔓 UNLOCKED"
        lines.append(f"  • {c['label']}: {icon}")
        if c["locked"]:
            locked_paths.append(c["path"])

    if unlock and locked_paths and which("gdbus"):
        run_fn = runner or (lambda cmd: command_runner.run_command(cmd, timeout=5.0))
        arr = "[" + ", ".join(f"objectpath '{p}'" for p in locked_paths) + "]"
        u_res = run_fn([
            "gdbus", "call", "--session",
            "--dest", "org.freedesktop.secrets",
            "--object-path", "/org/freedesktop/secrets",
            "--method", "org.freedesktop.Secret.Service.Unlock",
            arr,
        ])
        if u_res.ok:
            lines.append("Triggered native OS vault unlock prompt (approve with Touch ID / passphrase).")
        else:
            lines.append(f"Could not trigger unlock prompt: {u_res.stderr or u_res.output}")
    elif locked_paths:
        lines.append("Tip: Say 'unlock my secret vault' to trigger the native GNOME Keyring / KeePassXC unlock dialog.")
    return "\n".join(lines)


def resolve_mime_type(mime_or_alias: str) -> str:
    raw = (mime_or_alias or "web").strip().lower()
    return _MIME_ALIASES.get(raw, raw)


def manage_default_apps(
    action: str = "get",
    mime_type: str = "web",
    desktop_id: str = "",
    *,
    runner: RunnerFn | None = None,
    which: Callable[[str], str | None] = shutil.which,
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """Inspects or switches Ubuntu GNOME default MIME handlers via `gio mime` / `xdg-mime`.
    Supports friendly aliases: web/browser, pdf, mail, files/finder, text, image, video, audio.
    """
    act = (action or "get").strip().lower()
    if act not in {"get", "set"}:
        return "Error: action must be 'get' or 'set'."
    mime = resolve_mime_type(mime_type)
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime):
        return f"Error: Invalid MIME type '{mime_type}'. Examples: 'web', 'pdf', 'mail', 'application/pdf'."

    run_fn = runner or (lambda cmd: command_runner.run_command(cmd, timeout=4.0))

    if act == "get":
        if which("gio"):
            r = run_fn(["gio", "mime", mime])
            if r.ok and r.stdout.strip():
                return f"Default handler for `{mime}`:\n{r.stdout.strip()[:600]}"
        if which("xdg-mime"):
            r = run_fn(["xdg-mime", "query", "default", mime])
            if r.ok:
                return f"Default handler for `{mime}`: {r.stdout.strip() or 'none'}"
        return "Error: Neither `gio` nor `xdg-mime` is available."

    clean_desk = (desktop_id or "").strip()
    if clean_desk and not clean_desk.endswith(".desktop"):
        clean_desk = f"{clean_desk}.desktop"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}\.desktop", clean_desk):
        return "Error: Provide a valid `.desktop` handler ID (e.g. 'firefox.desktop', 'org.gnome.Evince.desktop')."

    args = {"action": "set", "mime_type": mime, "desktop_id": clean_desk}
    token, deny_err = approval.request_bound_approval(
        "manage_default_apps",
        args,
        approval_cb,
        title=f"Approve Setting Default App for {mime} -> {clean_desk}?",
        target=f"{mime} -> {clean_desk}",
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "manage_default_apps", args)
    if not ok:
        return err

    if which("gio"):
        r = run_fn(["gio", "mime", mime, clean_desk])
        if r.ok:
            return f"Verified: Set default handler for `{mime}` to `{clean_desk}`."
        return f"Error setting default handler via gio: {r.stderr or r.output}"
    if which("xdg-mime"):
        r = run_fn(["xdg-mime", "default", clean_desk, mime])
        if r.ok:
            return f"Verified: Set default handler for `{mime}` to `{clean_desk}`."
        return f"Error setting default handler via xdg-mime: {r.stderr or r.output}"
    return "Error: Neither `gio` nor `xdg-mime` is available."


def _start_zero_poll_os_dbus_watchers(mon: OSEventMonitor) -> threading.Thread | None:
    """Zero-polling background GLib D-Bus watcher for:
      - System D-Bus `org.freedesktop.UDisks2` `InterfacesAdded` (USB drives / SD cards)
      - Session D-Bus `org.freedesktop.systemd1.Manager` `JobRemoved` (user unit failures)
    Degrades silently when PyGObject/Gio is absent (e.g. minimal headless environments).
    """
    try:
        from gi.repository import Gio, GLib  # type: ignore[import-not-found]
    except Exception:
        return None

    def _run() -> None:
        try:
            sys_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            sess_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            def _on_udisks_added(_c, _sender, _path, _iface, _sig, params):
                try:
                    _obj_path, ifaces = params.unpack()
                    blk = ifaces.get("org.freedesktop.UDisks2.Block")
                    fs = ifaces.get("org.freedesktop.UDisks2.Filesystem")
                    if blk and fs is not None:
                        label = str(blk.get("IdLabel") or blk.get("IdUUID") or "External Drive")
                        size_b = int(blk.get("Size") or 0)
                        if size_b > 0:
                            mon.record_usb_alert(label=label, size_bytes=size_b)
                            mon.poll_once()
                except Exception:
                    pass

            def _on_systemd_job(_c, _sender, _path, _iface, _sig, params):
                try:
                    _jid, _jpath, unit, result = params.unpack()
                    if str(result) == "failed" and str(unit).endswith(".service"):
                        mon.record_failed_unit(str(unit), failed=True)
                        mon.poll_once()
                    elif str(result) == "done" and str(unit).endswith(".service"):
                        mon.record_failed_unit(str(unit), failed=False)
                except Exception:
                    pass

            sys_bus.signal_subscribe(
                "org.freedesktop.UDisks2",
                "org.freedesktop.DBus.ObjectManager",
                "InterfacesAdded",
                "/org/freedesktop/UDisks2",
                None,
                Gio.DBusSignalFlags.NONE,
                _on_udisks_added,
            )
            sess_bus.signal_subscribe(
                "org.freedesktop.systemd1",
                "org.freedesktop.systemd1.Manager",
                "JobRemoved",
                "/org/freedesktop/systemd1",
                None,
                Gio.DBusSignalFlags.NONE,
                _on_systemd_job,
            )
            GLib.MainLoop().run()
        except Exception:
            pass

    t = threading.Thread(target=_run, name="celina-os-dbus-watchers", daemon=True)
    t.start()
    return t


def get_os_parity_health(
    *,
    etc_root: Path | str = "/etc",
    home_root: Path | str | None = None,
    dev_root: Path | str = "/dev",
) -> dict[str, Any]:
    """Zero-overhead (<1 ms) OS-level macOS parity readiness inspector for macubuntu.
    Checks deployed kernel/systemd/PipeWire/WirePlumber/udev/Wayland drop-ins.
    """
    etc = Path(etc_root)
    home = Path(home_root) if home_root is not None else config.HOME
    dev = Path(dev_root)

    checks: dict[str, bool] = {
        "zram_compressed_mem": (etc / "systemd" / "zram-generator.conf").is_file(),
        "oomd_jetsam_guard": (etc / "systemd" / "oomd.conf.d" / "99-macubuntu.conf").is_file(),
        "io_scheduler_udev": (etc / "udev" / "rules.d" / "60-macubuntu-io-scheduler.rules").is_file(),
        "mdns_zero_conflict": (etc / "systemd" / "resolved.conf.d" / "99-macubuntu-mdns.conf").is_file(),
        "persistent_continuity_cam": (
            (etc / "modprobe.d" / "99-macubuntu-v4l2loopback.conf").is_file()
            and (etc / "modules-load.d" / "99-macubuntu-v4l2loopback.conf").is_file()
        ),
        "continuity_cam_device_ready": (dev / "video10").exists(),
        "airpods_bluetooth_aac": (
            home / ".config" / "wireplumber" / "wireplumber.conf.d" / "51-macubuntu-bluetooth.conf"
        ).is_file(),
        "pipewire_low_jitter": (
            home / ".config" / "pipewire" / "pipewire.conf.d" / "99-macubuntu-audio.conf"
        ).is_file(),
        "safe_sleep_policy": (etc / "systemd" / "sleep.conf.d" / "99-macubuntu-safesleep.conf").is_file(),
        "native_wayland_env": (home / ".config" / "environment.d" / "90-macubuntu-smooth.conf").is_file(),
        "silent_boot_opt_in": (etc / "default" / "grub.d" / "99-macubuntu-boot.cfg").is_file(),
    }
    active = [k for k, v in checks.items() if v]
    missing_core = [
        k for k, v in checks.items()
        if not v and k not in {"silent_boot_opt_in", "continuity_cam_device_ready"}
    ]
    return {
        "checks": checks,
        "active_count": len(active),
        "total_checks": len(checks),
        "missing_core": missing_core,
        "summary": (
            f"macubuntu OS parity: {len(active)}/{len(checks)} layers active"
            + (f" (missing: {', '.join(missing_core[:4])})" if missing_core else " (all core OS layers active)")
        ),
    }


def set_focus_mode(mode: str = "status") -> str:
    mgr = FocusModeManager()
    if (mode or "").strip().lower() in {"", "status", "get"}:
        cur = mgr.get_current()
        prof = FOCUS_PROFILES[cur]
        return f"Current Focus Mode: '{cur}' — {prof.description}"
    return mgr.set_mode(mode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Celina OS Event Bus Daemon")
    parser.add_argument("--daemon", action="store_true", help="Run low-overhead event polling loop")
    parser.add_argument("--interval", type=float, default=15.0, help="Polling interval in seconds (default 15s for low CPU)")
    args = parser.parse_args()
    mon = OSEventMonitor()
    if not args.daemon:
        print(json.dumps(mon.poll_once(), indent=2))
        return

    stop_flag = {"stop": False}

    def _cleanup(*_a: Any) -> None:
        stop_flag["stop"] = True
        mon.app_nap.thaw_all()
        ios_ancs.stop_ios_bridge()

    atexit.register(_cleanup)
    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    # Start zero-polling D-Bus watchers (UDisks2 USB hotplug + Systemd user crash doctor)
    _start_zero_poll_os_dbus_watchers(mon)

    # Start event-driven iOS BLE ANCS bridge if an ANCS-enabled iPhone is paired & connected
    try:
        ios_ancs.start_ios_bridge()
    except Exception:
        pass

    interval = max(2.0, float(args.interval))
    while not stop_flag["stop"]:
        mon.poll_once()
        mon.maybe_refresh_spotlight()
        time.sleep(interval)


if __name__ == "__main__":
    main()
