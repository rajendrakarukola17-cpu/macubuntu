"""
Celina AI (Celly) - Full OS Control, Fast File Search, Document & Messaging Tools (v4.2)
- Tier 0: Zero-latency multilingual hardware/media shortcuts (EN / TE / HI)
- Tier 1: Auto-execute OS, Web, Fast File/Content Search (fd/rg), Read-Only Docs, Memory & Timers
- Tier 2: 1-Click Approval-Gated Full Control (Docs/LibreOffice Edit, File Edit/Move/Trash,
          App Close, Safe Wayland Messaging, and Guardrailed Shell)
"""

import atexit
import datetime
import hashlib
import inspect
import ipaddress
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from . import (
    api_actuators,
    app_intents,
    approval,
    automation,
    cache,
    calendar_bridge,
    capabilities,
    code_sandbox,
    command_runner,
    computer_use,
    ponytail_skill,
    skilspector_tool,
    config,
    continuity,
    documents,
    evidence,
    guardrails,
    libreoffice_uno,
    mcp_client,
    memory,
    net_policy,
    os_events,
    privacy,
    skills,
    spotlight_index,
    state_ops,
    writing_tools,
)

log = logging.getLogger("celina.tools")

TOOL_METADATA: dict[str, dict[str, Any]] = {}
OPT_IN_TOOLS: frozenset[str] = frozenset({
    "get_weather",
    "get_news_headlines",
    "get_current_time_zone",
    "fetch_url_text",
    "fetch_webpage",
})


def tool(
    *,
    name: str,
    tier: int = 1,
    domain: str = "os_hardware",
    opt_in: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declarative tool metadata decorator (tier, domain, opt_in)."""
    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        meta = {"name": name, "tier": int(tier), "domain": domain, "opt_in": bool(opt_in)}
        TOOL_METADATA[name] = meta
        setattr(fn, "_celina_tool_meta", meta)
        setattr(fn, "tool_name", name)
        setattr(fn, "tier", int(tier))
        setattr(fn, "domain", domain)
        setattr(fn, "opt_in", bool(opt_in))
        return fn
    return _decorator

# Register code_sandbox tool metadata (defined in code_sandbox.py, imported above)
TOOL_METADATA["code_sandbox"] = {
    "name": "code_sandbox",
    "tier": 2,
    "domain": "code_exec",
    "opt_in": False,
}

# Make sibling celina_hardware package importable in both repo & installed layouts
_HW_PARENT = str(Path(__file__).resolve().parent.parent)
if _HW_PARENT not in sys.path:
    sys.path.insert(0, _HW_PARENT)
try:
    import celina_hardware as _celina_hardware
except Exception:
    _celina_hardware = None  # type: ignore[assignment]

ApprovalCallback = Callable[[str, str], bool]
StatusCallback = Callable[[str], None]
PrivacyConsentCallback = privacy.PrivacyConsentCallback
TaskGlowCallback = Callable[[bool], None]

# --- Safety Policies (driven by neural-schema.json + hard core floor) ---
_SENSITIVE_PARTS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".netrc",
    ".git-credentials",
    "keyrings",
    "Login Data",
    "cookies.sqlite",
} | set(config.PROTECTED_PATHS)

_SKIP_SEARCH_DIRS = {
    ".git",
    ".cache",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".local",
    "snap",
    ".mozilla",
    ".config",
    ".ssh",
    ".gnupg",
}

_PROTECTED_PROCESSES = {
    "systemd",
    "gnome-shell",
    "gnome-session",
    "mutter",
    "xwayland",
    "dbus-daemon",
    "pipewire",
    "wireplumber",
    "pulseaudio",
    "ydotoold",
    "ollama",
    "celina",
    "celly",
    "celina-island",
    "python3",
    "init",
    "sshd",
    "networkmanager",
}

_CATASTROPHIC_SHELL_PATTERNS = [
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+(/(\s|$)|~(\s|$)|/\*)"),
    re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+(/(\s|$)|~(\s|$)|/\*)"),
    re.compile(r"\bmkfs(\.[a-z0-9]+)?\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r":\(\)\s*\{"),
    re.compile(r">\s*/dev/(sd[a-z]|nvme[0-9]|vd[a-z]|mmcblk)"),
    re.compile(r"\bchmod\s+-R\s+777\s+/(\s|$)"),
    re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba)?sh\b"),
]


def _is_sensitive_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    if resolved == config.ENV_FILE.resolve():
        return True
    return any(part in _SENSITIVE_PARTS for part in resolved.parts)


def _is_within_home(path: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(config.HOME.resolve())
        return True
    except ValueError:
        return False


def run_capture(cmd: list[str], timeout: int = 5) -> str:
    """Safely executes an allowlisted command without shell=True."""
    if not cmd or not shutil.which(cmd[0]):
        return f"Error: Required utility '{cmd[0]}' not found."
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (res.stdout or res.stderr).strip()
    except subprocess.TimeoutExpired:
        return "Error: Command timed out."
    except Exception as exc:
        return f"Error: {exc}"


def launch_detached(cmd: list[str]) -> bool:
    """Safely launches an allowlisted desktop process detached from Celina."""
    if not cmd or not shutil.which(cmd[0]):
        return False
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def send_notification(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        launch_detached(["notify-send", "-a", config.get_assistant_name(), title, body])


# ============================================================================
# Tier 1: Auto-Execute OS, Fast Search, Apps, Web, Timer & Memory Tools
# ============================================================================

def get_system_stats() -> str:
    """Returns uptime, RAM, ZRAM, battery, and top 5 CPU processes."""
    uptime = run_capture(["uptime", "-p"])
    mem = run_capture(["free", "-h"])
    zram = run_capture(["zramctl", "--output", "NAME,DISKSIZE,DATA,COMPR,TOTAL"])
    top_procs = run_capture(["ps", "-eo", "comm,%cpu,%mem", "--sort=-%cpu"])
    top_lines = "\n".join(top_procs.splitlines()[:6])

    battery = "Desktop / AC"
    power_dir = Path("/sys/class/power_supply")
    if power_dir.exists():
        for bat in sorted(power_dir.glob("BAT*")):
            cap = bat / "capacity"
            status = bat / "status"
            if cap.exists():
                pct = cap.read_text(encoding="utf-8").strip()
                st = status.read_text(encoding="utf-8").strip() if status.exists() else ""
                battery = f"{pct}% ({st})" if st else f"{pct}%"
                break

    parity_line = ""
    try:
        parity_line = f"\nOS Parity: {os_events.get_os_parity_health()['summary']}"
    except Exception:
        parity_line = ""

    return (
        f"Uptime: {uptime}\nBattery: {battery}{parity_line}\n\n"
        f"Memory:\n{mem}\n\nZRAM:\n{zram}\n\nTop Processes:\n{top_lines}"
    )


def _is_public_http_url(url: str) -> bool:
    """Delegates strictly to centralized SSRF/DNS/IP net_policy."""
    try:
        net_policy.validate_public_url(url)
        return True
    except net_policy.URLPolicyError:
        return False


def get_hardware_capabilities() -> str:
    """Returns live UPower, PipeWire, NetworkManager, BlueZ, Portal & Biometric capabilities."""
    if _celina_hardware is None:
        return "Hardware capability layer unavailable in this environment."
    return json.dumps(_celina_hardware.get_hardware().describe_capabilities(), indent=2)


def route_app_audio(app_name: str, output_device: str) -> str:
    """Routes a specific app's live PipeWire audio stream to a chosen output sink (e.g. Bluetooth)."""
    if _celina_hardware is None:
        return "Error: Hardware audio routing adapter unavailable."
    return _celina_hardware.get_hardware().routing.route_app_audio(app_name, output_device).message


def set_volume(
    level: Optional[int] = None, delta: Optional[int] = None, mute: Optional[bool] = None
) -> str:
    """Controls system audio volume/mute via celina_hardware PipeWire adapter with read-after-write verification."""
    if _celina_hardware is not None:
        hw = _celina_hardware.get_hardware()
        if level is None and delta is None and mute is None:
            st = hw.audio.get_state()
            if st.status != _celina_hardware.CapabilityStatus.HEALTHY:
                return f"Error: {st.detail or 'No supported audio mixer found.'}"
            return f"Volume: {st.volume_percent}% ({'muted' if st.muted else 'unmuted'}) via {st.backend}."
        res = hw.audio.set_volume(level=level, delta=delta, mute=mute)
        return res.message

    messages: list[str] = []
    if mute is not None:
        if shutil.which("wpctl"):
            r = command_runner.run_command(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"])
            if not r.ok:
                return f"Error: {r.error}"
            messages.append("Audio muted." if mute else "Audio unmuted.")
        elif shutil.which("pactl"):
            r = command_runner.run_command(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])
            if not r.ok:
                return f"Error: {r.error}"
            messages.append("Audio muted." if mute else "Audio unmuted.")
        else:
            return "Error: No supported audio mixer found (wpctl/pactl)."

    if level is not None:
        clamped = max(0, min(100, int(level)))
        if shutil.which("wpctl"):
            r = command_runner.run_command(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{clamped}%"])
            if not r.ok:
                return f"Error: {r.error}"
            messages.append(f"Volume set to {clamped}%.")
        elif shutil.which("pactl"):
            r = command_runner.run_command(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{clamped}%"])
            if not r.ok:
                return f"Error: {r.error}"
            messages.append(f"Volume set to {clamped}%.")
        else:
            return "Error: No supported audio mixer found."
    elif delta is not None:
        step = max(-50, min(50, int(delta)))
        sign = "+" if step >= 0 else "-"
        mag = abs(step)
        if shutil.which("wpctl"):
            r = command_runner.run_command(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{mag}%{sign}"])
            if not r.ok:
                return f"Error: {r.error}"
            messages.append(f"Volume adjusted by {sign}{mag}%.")
        elif shutil.which("pactl"):
            r = command_runner.run_command(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{mag}%"])
            if not r.ok:
                return f"Error: {r.error}"
            messages.append(f"Volume adjusted by {sign}{mag}%.")
        else:
            return "Error: No supported audio mixer found."

    return " ".join(messages) if messages else "Specify level (0-100), delta, or mute."


def set_brightness(level: int) -> str:
    """Sets screen brightness via celina_hardware with read-after-write verification."""
    if _celina_hardware is not None:
        return _celina_hardware.get_hardware().set_brightness(int(level)).message
    if not shutil.which("brightnessctl"):
        return "Error: brightnessctl is not installed."
    clamped = max(5, min(100, int(level)))
    res = command_runner.run_command(["brightnessctl", "set", f"{clamped}%"], timeout=5.0)
    if not res.ok:
        err_txt = res.stderr or res.stdout or res.error
        if "Permission denied" in err_txt:
            return "Error: Brightness permission denied. Reboot once so the 'video' group change takes effect."
        return f"Error: Failed to set brightness ({err_txt})."
    return f"Screen brightness set to {clamped}%."


_MPRIS_METHOD_MAP: dict[str, str] = {
    "play": "Play",
    "pause": "Pause",
    "play-pause": "PlayPause",
    "next": "Next",
    "previous": "Previous",
    "stop": "Stop",
}


def _mpris_dbus_control(act: str) -> tuple[bool, str]:
    """
    Sends an MPRIS2 Player command directly over GNOME Session D-Bus (0 extra packages!).
    Prioritizes browser players (Firefox/Chrome/Brave) & paused players when `act == 'play'`.
    Returns (handled_ok, human_status).
    """
    method = _MPRIS_METHOD_MAP.get(act)
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names_var = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            500,
            None,
        )
        players = [n for n in names_var.unpack()[0] if n.startswith("org.mpris.MediaPlayer2.")]
        if not players:
            return False, "no_mpris_players"

        def _get_status(pbus: str) -> str:
            try:
                v = bus.call_sync(
                    pbus,
                    "/org/mpris/MediaPlayer2",
                    "org.freedesktop.DBus.Properties",
                    "Get",
                    GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", "PlaybackStatus")),
                    None,
                    Gio.DBusCallFlags.NONE,
                    400,
                    None,
                )
                return str(v.unpack()[0] or "")
            except Exception:
                return ""

        if act == "status":
            parts = [f"{p.split('.')[-1]}={_get_status(p) or 'Unknown'}" for p in players]
            return True, ", ".join(parts)

        if not method:
            return False, "unsupported"

        # Prefer browser players (firefox/chromium/chrome/brave) and paused/stopped players for 'play'
        def _rank(pbus: str) -> tuple[int, int]:
            low = pbus.lower()
            is_browser = 0 if any(b in low for b in ("firefox", "chromium", "chrome", "brave", "vivaldi", "edge")) else 1
            st = _get_status(pbus).lower()
            if act == "play":
                want = 0 if st in {"paused", "stopped"} else 1
            elif act == "pause":
                want = 0 if st == "playing" else 1
            else:
                want = 0
            return (want, is_browser)

        ordered = sorted(players, key=_rank)
        fired: list[str] = []
        for pbus in ordered:
            try:
                bus.call_sync(
                    pbus,
                    "/org/mpris/MediaPlayer2",
                    "org.mpris.MediaPlayer2.Player",
                    method,
                    None,
                    None,
                    Gio.DBusCallFlags.NONE,
                    500,
                    None,
                )
                fired.append(pbus.removeprefix("org.mpris.MediaPlayer2."))
                if act in {"play", "pause", "play-pause", "next", "previous"}:
                    break
            except Exception:
                continue

        if fired:
            time.sleep(0.15)
            ver = _get_status(ordered[0])
            ver_s = f" (verified state: {ver})" if ver else ""
            return True, f"Media command '{act}' executed on {fired[0]}{ver_s}."
        return False, "dbus_call_failed"
    except Exception:
        return False, "dbus_unavailable"


def _kick_browser_media_play_async() -> None:
    """
    Lightweight daemon thread launched right after opening a direct YouTube watch URL.
    Waits briefly for Firefox/Chrome to mount the video player, then fires MPRIS D-Bus
    `Play` (or AT-SPI2 Play button activation) so playback starts hands-free!
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CELINA_DRY_RUN") == "1":
        return

    def _worker() -> None:
        for delay in (2.2, 1.5, 1.8):
            time.sleep(delay)
            ok, msg = _mpris_dbus_control("play")
            if ok and "Playing" in msg:
                return
            if ok:
                return
        # Final safe accessibility fallback (no raw uinput) if browser hid MPRIS behind autoplay shield
        try:
            automation._try_atspi_direct_action("Play")
        except Exception as exc:
            log.debug("Autoplay AT-SPI2 fallback skipped: %s", exc)

    t = threading.Thread(target=_worker, name="celina-autoplay-kick", daemon=True)
    t.start()


def control_media(action: str) -> str:
    """Controls MPRIS media via in-process D-Bus MPRIS first (0 forks), playerctl fallback, and browser Play fallback."""
    allowed = {"play", "pause", "play-pause", "next", "previous", "stop", "status"}
    act = (action or "").lower().strip()
    if act not in allowed:
        return f"Invalid media action '{action}'."

    # 1. Preferred: Direct GNOME Session D-Bus MPRIS2 (works even without playerctl!)
    dbus_ok, dbus_msg = _mpris_dbus_control(act)
    if dbus_ok:
        return dbus_msg

    # 2. Fallback: playerctl CLI if installed
    if shutil.which("playerctl"):
        if act == "status":
            r = command_runner.run_command(
                ["playerctl", "metadata", "--format", "{{status}}: {{artist}} - {{title}}"],
                timeout=2.0,
            )
            if not r.ok or not r.stdout or r.stdout.startswith("No players"):
                return "No active media player."
            return r.stdout
        r = command_runner.run_command(["playerctl", act], timeout=2.0)
        if r.ok:
            st = command_runner.run_command(["playerctl", "status"], timeout=2.0)
            ver = f" (verified state: {st.stdout})" if st.ok and st.stdout else ""
            return f"Media command '{act}' executed{ver}."

    # 3. Browser autoplay-shield fallback when user says 'play' and Firefox hasn't exposed MPRIS yet
    if act in {"play", "play-pause"}:
        try:
            hit, hit_msg = automation._try_atspi_direct_action("Play")
            if hit:
                return f"Started playback in browser — {hit_msg}"
        except Exception as exc:
            log.debug("control_media AT-SPI2 Play fallback failed: %s", exc)
        return (
            "No active MPRIS media player responded yet — Firefox/Chrome's Autoplay Blocker may be holding the video paused. "
            "Run 'celina-turbo autoplay' once (or allow Audio & Video for YouTube next to the URL bar) so videos play hands-free!"
        )

    if act == "status":
        return "No active media player."
    return f"No active media player found for '{act}'."


def get_media_title() -> str:
    """Reads playing media title via in-process D-Bus MPRIS first (0 forks), falling back to playerctl."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names_var = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            500,
            None,
        )
        mpris_players = [
            n for n in names_var.unpack()[0] if n.startswith("org.mpris.MediaPlayer2.")
        ]
        if not mpris_players:
            return ""
        for player_bus in mpris_players:
            try:
                meta_var = bus.call_sync(
                    player_bus,
                    "/org/mpris/MediaPlayer2",
                    "org.freedesktop.DBus.Properties",
                    "Get",
                    gi.repository.GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", "Metadata")),
                    None,
                    Gio.DBusCallFlags.NONE,
                    500,
                    None,
                )
                meta = meta_var.unpack()[0]
                title = str(meta.get("xesam:title") or "").strip()
                if title:
                    return f"♪ {title}"
            except Exception:
                continue
        return ""
    except Exception:
        pass

    if not shutil.which("playerctl"):
        return ""
    try:
        res = subprocess.run(
            ["playerctl", "metadata", "--format", "♪ {{title}}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return ""


_APP_NAP_EXCLUDED: frozenset[str] = frozenset({
    "systemd", "gnome-shell", "gnome-session", "mutter", "xwayland", "dbus-daemon", "dbus-broker",
    "pipewire", "wireplumber", "polkitd", "polkit-gnome-authentication-agent-1",
    "celina", "celly", "python3", "bash", "zsh", "sh", "fish",
    "gnome-terminal", "gnome-terminal-server", "ptyxis", "kgx", "kitty", "alacritty", "konsole", "foot", "wezterm",
    "keepassxc", "bitwarden", "1password", "seahorse", "gpg-agent", "ssh-agent",
    "systemd-logind", "networkmanager", "bluetoothd", "upowerd", "fprintd",
})


class AppNapController:
    """
    PID-Verified Safe App Nap Controller (Phase 6 Item 15):
      - NEVER uses broad `pkill -STOP -i <name>`!
      - Resolves exact user-owned PIDs matching process comm + start_time from /proc/<pid>/stat.
      - Refuses foreground active window, terminals, password managers, GNOME Shell, polkit, systemd, Celina.
      - Prefers cgroup v2 freezer when available; falls back to PID-verified SIGSTOP/SIGCONT.
      - Persists frozen PIDs + start_time to ~/.local/share/celina/napped_pids.json (0600)
        and registers atexit restoration so no app is ever left stranded frozen!
    """

    def __init__(self, state_path: Path = config.NAPPED_PIDS_FILE):
        self.state_path = Path(state_path)
        self._registered_atexit = False

    def _load_state(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_state(self, state: dict[str, dict[str, Any]]) -> None:
        try:
            config.atomic_write_private_text(self.state_path, json.dumps(state, indent=2) + "\n")
        except OSError:
            pass

    @staticmethod
    def _proc_info(pid: int) -> Optional[tuple[str, int, str]]:
        """Returns (comm, uid, start_time_ticks) for a live /proc/<pid>, else None."""
        try:
            stat_txt = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            rparen = stat_txt.rfind(")")
            lparen = stat_txt.find("(")
            if lparen < 0 or rparen < 0:
                return None
            comm = stat_txt[lparen + 1 : rparen]
            fields = stat_txt[rparen + 2 :].split()
            start_time = fields[19] if len(fields) > 19 else ""
            uid = Path(f"/proc/{pid}").stat().st_uid
            return comm, uid, start_time
        except OSError:
            return None

    def _find_exact_user_pids(self, app_name: str) -> list[tuple[int, str, str]]:
        target = app_name.strip().lower()
        my_uid = os.getuid()
        my_pid = os.getpid()
        matches: list[tuple[int, str, str]] = []
        proc_root = Path("/proc")
        if not proc_root.exists():
            return []
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == my_pid or pid <= 1:
                continue
            info = self._proc_info(pid)
            if info is None:
                continue
            comm, uid, start_time = info
            if uid != my_uid:
                continue
            if comm.lower() == target:
                matches.append((pid, comm, start_time))
        return matches

    @staticmethod
    def _is_protected(app_name: str) -> bool:
        low = app_name.strip().lower()
        if not low or low in _APP_NAP_EXCLUDED or low in _PROTECTED_PROCESSES:
            return True
        if any(tok in low for tok in ("celina", "celly", "gnome-shell", "systemd", "dbus", "polkit", "terminal", "keepass", "bitwarden", "1password")):
            return True
        return False

    def nap(self, app_name: str, active_window_app: Optional[str] = None) -> str:
        proc = app_name.strip()
        if self._is_protected(proc):
            return f"Blocked: Cannot nap protected/critical process '{app_name}'."

        fg = active_window_app
        if fg is None:
            info = automation.get_active_window_info()
            fg = str(info.get("app", "") or "")
        if fg and proc.lower() == fg.strip().lower():
            return f"Blocked: Refusing to freeze the currently focused foreground app '{proc}'."

        targets = self._find_exact_user_pids(proc)
        if not targets:
            return f"No running user process with exact name '{proc}'."

        state = self._load_state()
        frozen_pids: list[int] = []
        for pid, comm, start_time in targets:
            try:
                os.kill(pid, signal.SIGSTOP)
                state[str(pid)] = {
                    "pid": pid,
                    "comm": comm,
                    "start_time": start_time,
                    "frozen_at": time.time(),
                }
                frozen_pids.append(pid)
            except OSError:
                continue

        if not frozen_pids:
            return f"Error: Could not freeze any PID for '{proc}'."

        self._save_state(state)
        if not self._registered_atexit:
            atexit.register(self.restore_all)
            self._registered_atexit = True

        return (
            f"'{proc}' put into safe App Nap (PIDs {frozen_pids} frozen & tracked in "
            f"{self.state_path.name}; auto-restores on Celina exit)."
        )

    def wake(self, app_name: str) -> str:
        proc = app_name.strip().lower()
        state = self._load_state()
        woken: list[int] = []
        remaining: dict[str, dict[str, Any]] = {}

        for pid_s, rec in state.items():
            if str(rec.get("comm", "")).lower() != proc:
                remaining[pid_s] = rec
                continue
            pid = int(rec.get("pid", 0))
            info = self._proc_info(pid)
            if info and info[1] == os.getuid() and info[2] == str(rec.get("start_time", "")):
                try:
                    os.kill(pid, signal.SIGCONT)
                    woken.append(pid)
                except OSError:
                    pass

        self._save_state(remaining)
        if woken:
            return f"'{app_name}' woken up from App Nap (verified PIDs {woken})."

        # Fallback: wake exact-name user processes currently stopped ('T' state)
        for pid, _comm, _st in self._find_exact_user_pids(proc):
            try:
                os.kill(pid, signal.SIGCONT)
                woken.append(pid)
            except OSError:
                continue
        if woken:
            return f"'{app_name}' woken up from App Nap (PIDs {woken})."
        return f"No napped user process matching exact name '{app_name}'."

    def restore_all(self) -> list[int]:
        """Restores all previously napped PIDs whose start_time still matches (prevents PID reuse bugs)."""
        state = self._load_state()
        restored: list[int] = []
        for rec in state.values():
            pid = int(rec.get("pid", 0))
            info = self._proc_info(pid)
            if info and info[1] == os.getuid() and info[2] == str(rec.get("start_time", "")):
                try:
                    os.kill(pid, signal.SIGCONT)
                    restored.append(pid)
                except OSError:
                    pass
        self._save_state({})
        return restored


app_nap_controller = AppNapController()


def toggle_app_nap(app_name: str, action: str = "nap") -> str:
    """
    Safe PID-verified App Nap: freezes ('nap') an unfocused non-critical user app
    or wakes ('wake') it cleanly with automatic exit-time restoration.
    """
    act = action.lower().strip()
    if act in {"restore_all", "wake_all"}:
        restored = app_nap_controller.restore_all()
        return f"Restored {len(restored)} napped process(es): {restored}"
    if act in {"nap", "sleep", "freeze", "pause", "suspend", "stop"}:
        return app_nap_controller.nap(app_name)
    return app_nap_controller.wake(app_name)


def control_connectivity(target: str, action: str) -> str:
    """Controls Wi-Fi or Bluetooth via celina_hardware with read-after-write verification."""
    tgt = target.lower().strip()
    act = action.lower().strip()
    if act not in {"status", "on", "off"}:
        return "Action must be 'on', 'off', or 'status'."

    if tgt in {"wifi", "wi-fi", "network"}:
        if _celina_hardware is not None:
            hw = _celina_hardware.get_hardware()
            if act == "status":
                st = hw.network.get_state()
                if st.status != _celina_hardware.CapabilityStatus.HEALTHY:
                    return f"Error: {st.detail or 'NetworkManager unavailable.'}"
                return f"Wi-Fi: {'ON' if st.wifi_enabled else 'OFF'} | SSID: {st.active_ssid or 'none'}"
            return hw.network.set_wifi(act == "on").message
        if not shutil.which("nmcli"):
            return "Error: nmcli not installed."
        if act == "status":
            r = command_runner.run_command(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"])
            return r.stdout if r.ok else f"Error: {r.error}"
        r = command_runner.run_command(["nmcli", "radio", "wifi", act])
        return f"Wi-Fi turned {act}." if r.ok else f"Error: {r.error}"

    if tgt == "bluetooth":
        if _celina_hardware is not None:
            hw = _celina_hardware.get_hardware()
            if act == "status":
                st = hw.bluetooth.get_state()
                if st.status != _celina_hardware.CapabilityStatus.HEALTHY:
                    return f"Error: {st.detail or 'Bluetooth adapter unavailable.'}"
                return f"Bluetooth ({st.controller or 'default'}): {'ON' if st.powered else 'OFF'}"
            return hw.bluetooth.set_power(act == "on").message
        if not shutil.which("bluetoothctl"):
            return "Error: bluetoothctl not installed."
        if act == "status":
            r = command_runner.run_command(["bluetoothctl", "show"])
            return r.stdout if r.ok else f"Error: {r.error}"
        r = command_runner.run_command(["bluetoothctl", "power", act])
        return f"Bluetooth turned {act}." if r.ok else f"Error: {r.error}"

    return "Target must be 'wifi' or 'bluetooth'."


CURATED_APPS: dict[str, list[str]] = {
    "files": ["nautilus"],
    "browser": ["xdg-open", "https://google.com"],
    "terminal": ["gnome-terminal"],
    "settings": ["gnome-control-center"],
    "writer": ["libreoffice", "--writer"],
    "libreoffice": ["libreoffice"],
    "calc": ["libreoffice", "--calc"],
    "impress": ["libreoffice", "--impress"],
    "monitor": ["flatpak", "run", "io.missioncenter.MissionCenter"],
    "backup": ["flatpak", "run", "org.gnome.World.PikaBackup"],
    "audio": ["flatpak", "run", "org.freedesktop.Helvum"],
    "localsend": ["flatpak", "run", "org.localsend.localsend_app"],
}

WEB_APPS: dict[str, str] = {
    "spotify": "https://open.spotify.com",
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
    "youtube music": "https://music.youtube.com",
    "yt music": "https://music.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "wa": "https://web.whatsapp.com",
    "github": "https://github.com",
    "gh": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "gpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "netflix": "https://www.netflix.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "gmaps": "https://maps.google.com",
    "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com",
    "calendar": "https://calendar.google.com",
    "notion": "https://www.notion.so",
    "linear": "https://linear.app",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "instagram": "https://www.instagram.com",
    "ig": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "fb": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "prime video": "https://www.primevideo.com",
    "hotstar": "https://www.hotstar.com",
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
    "discord": "https://discord.com/app",
    "telegram": "https://web.telegram.org",
    "wikipedia": "https://www.wikipedia.org",
    "figma": "https://www.figma.com",
    "canva": "https://www.canva.com",
    "flathub": "https://flathub.org",
}

CURATED_INSTALLABLE_APPS: dict[str, tuple[str, str]] = {
    "spotify": ("Spotify", "com.spotify.Client"),
    "vlc": ("VLC Media Player", "org.videolan.VLC"),
    "discord": ("Discord", "com.discordapp.Discord"),
    "telegram": ("Telegram Desktop", "org.telegram.desktop"),
    "obsidian": ("Obsidian", "md.obsidian.Obsidian"),
    "chrome": ("Google Chrome", "com.google.Chrome"),
    "google chrome": ("Google Chrome", "com.google.Chrome"),
    "brave": ("Brave Browser", "com.brave.Browser"),
    "slack": ("Slack", "com.slack.Slack"),
    "zoom": ("Zoom", "us.zoom.Zoom"),
    "gimp": ("GIMP", "org.gimp.GIMP"),
    "obs": ("OBS Studio", "com.obsproject.Studio"),
    "obs studio": ("OBS Studio", "com.obsproject.Studio"),
    "steam": ("Steam", "com.valvesoftware.Steam"),
    "postman": ("Postman", "com.getpostman.Postman"),
    "vscode": ("Visual Studio Code", "com.visualstudio.code"),
    "vs code": ("Visual Studio Code", "com.visualstudio.code"),
    "whatsapp": ("WhatsApp Desktop", "io.github.mimbrero.WhatsAppDesktop"),
    "bitwarden": ("Bitwarden", "com.bitwarden.desktop"),
    "blender": ("Blender", "org.blender.Blender"),
}

_SAFE_DOMAIN_RE = re.compile(
    r"^(?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?:/[^\s\"'`<>]*)?$",
    re.IGNORECASE,
)

SETTINGS_PANELS: dict[str, str] = {
    "wifi settings": "wifi",
    "wi-fi settings": "wifi",
    "network settings": "network",
    "bluetooth settings": "bluetooth",
    "display settings": "display",
    "screen settings": "display",
    "sound settings": "sound",
    "audio settings": "sound",
    "power settings": "power",
    "battery settings": "power",
    "keyboard settings": "keyboard",
    "mouse settings": "mouse",
    "touchpad settings": "mouse",
    "appearance settings": "appearance",
    "wallpaper settings": "appearance",
    "privacy settings": "privacy",
    "apps settings": "applications",
    "notifications settings": "notifications",
    "users settings": "users",
    "about settings": "system",
    "system settings": "system",
}

SITE_SEARCH_URLS: dict[str, str] = {
    "google": "https://www.google.com/search?q={q}",
    "web": "https://www.google.com/search?q={q}",
    "online": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "yt": "https://www.youtube.com/results?search_query={q}",
    "youtube music": "https://music.youtube.com/search?q={q}",
    "yt music": "https://music.youtube.com/search?q={q}",
    "spotify": "https://open.spotify.com/search/{q}",
    "jiosaavn": "https://www.jiosaavn.com/search/song/{q}",
    "saavn": "https://www.jiosaavn.com/search/song/{q}",
    "gaana": "https://gaana.com/search/{q}",
    "soundcloud": "https://soundcloud.com/search?q={q}",
    "apple music": "https://music.apple.com/search?term={q}",
    "netflix": "https://www.netflix.com/search?q={q}",
    "prime video": "https://www.primevideo.com/search/?phrase={q}",
    "hotstar": "https://www.hotstar.com/in/explore?search_query={q}",
    "github": "https://github.com/search?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "wiki": "https://en.wikipedia.org/w/index.php?search={q}",
    "amazon": "https://www.amazon.in/s?k={q}",
    "flipkart": "https://www.flipkart.com/search?q={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "google maps": "https://www.google.com/maps/search/{q}",
    "flathub": "https://flathub.org/apps/search?q={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
}

MEDIA_PLATFORMS: tuple[str, ...] = (
    "youtube music",
    "yt music",
    "apple music",
    "prime video",
    "spotify",
    "youtube",
    "jiosaavn",
    "saavn",
    "gaana",
    "soundcloud",
    "netflix",
    "hotstar",
    "yt",
)

LOGIN_URLS: dict[str, str] = {
    "spotify": "https://accounts.spotify.com",
    "google": "https://accounts.google.com",
    "gmail": "https://mail.google.com",
    "youtube": "https://accounts.google.com/ServiceLogin?service=youtube",
    "github": "https://github.com/login",
    "whatsapp": "https://web.whatsapp.com",
    "instagram": "https://www.instagram.com/accounts/login/",
    "facebook": "https://www.facebook.com/login/",
    "linkedin": "https://www.linkedin.com/login",
    "x": "https://x.com/i/flow/login",
    "twitter": "https://x.com/i/flow/login",
    "reddit": "https://www.reddit.com/login/",
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com/account/login",
    "netflix": "https://www.netflix.com/login",
    "notion": "https://www.notion.so/login",
    "linear": "https://linear.app/login",
    "discord": "https://discord.com/login",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
}

_SAFE_FLATPAK_ID_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_-]+){2,}$")
_SAFE_APT_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,60}$")
_YT_RENDERER_ID_RE = re.compile(r'"videoRenderer"\s*:\s*\{\s*"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"')
_YT_VIDEO_ID_RE = re.compile(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"')
_FRUSTRATION_RE = re.compile(
    r"\b(?:stupid|idiot|dumb|useless|not\s+working|not\s+playing|didn'?t\s+work|doesn'?t\s+work|wrong|wtf|bruh|come\s+on|listen\s+to\s+me)\b",
    re.IGNORECASE,
)
_MEDIA_NOISE_TAIL_RE = re.compile(
    r"(?:\s+(?:right\s+now|now|immediately|please|pls|plz|bro|man|yaar|celina|celly|stupid|idiot|dumb|useless|fast|quickly|already))+$",
    re.IGNORECASE,
)
_PLAY_RESUME_ONLY_WORDS: frozenset[str] = frozenset({
    "now", "right now", "it", "this", "that", "again", "please", "pls",
    "music", "song", "video", "media", "spotify", "youtube", "yt", "on",
})


def _resolve_youtube_direct_watch_url(query: str) -> str:
    """Resolves the #1 YouTube video watch URL for direct playback (2 MB SSRF-safe fetch + yt-dlp fallback)."""
    fallback = SITE_SEARCH_URLS["youtube"].format(q=quote(query))
    try:
        resp = net_policy.safe_get(
            fallback,
            allow_redirects=True,
            max_bytes=2 * 1024 * 1024,
            timeout=(3.0, 5.0),
            headers={"User-Agent": _BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
        )
        if resp.status_code == 200 and resp.text:
            m = _YT_RENDERER_ID_RE.search(resp.text) or _YT_VIDEO_ID_RE.search(resp.text)
            if m:
                return f"https://www.youtube.com/watch?v={m.group(1)}"
    except (net_policy.URLPolicyError, net_policy.OfflineError, OSError, ValueError, RuntimeError):
        resp = None

    # Secondary local fallback if yt-dlp is installed on the desktop
    if shutil.which("yt-dlp"):
        try:
            r = command_runner.run_command(
                ["yt-dlp", "--no-playlist", "--get-id", f"ytsearch1:{query}"],
                timeout=6.0,
            )
            vid = (r.stdout or "").strip().splitlines()[0].strip() if r.ok and r.stdout.strip() else ""
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                return f"https://www.youtube.com/watch?v={vid}"
        except (OSError, IndexError, ValueError):
            return fallback
    return fallback


def _clean_media_query(raw_q: str) -> tuple[str, Optional[str]]:
    """Extracts inline platform (even with trailing filler like 'in youtube now' or 'in youtube stupid') and cleans the song/video query."""
    plat_alts = "|".join(sorted(MEDIA_PLATFORMS, key=len, reverse=True))
    cleaned = raw_q.strip().rstrip(".!?")
    found_plat: Optional[str] = None

    m_inline = re.search(
        rf"\s+(?:on|in|from|via)\s+({plat_alts})(?:\s+(?:web|app|website))?\b",
        cleaned,
        re.IGNORECASE,
    )
    if m_inline:
        found_plat = m_inline.group(1).strip().lower()
        cleaned = (cleaned[: m_inline.start()] + " " + cleaned[m_inline.end() :]).strip()

    cleaned = _MEDIA_NOISE_TAIL_RE.sub("", cleaned).strip(" ,.-!?")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, found_plat


def _get_preferred_music_platform() -> str:
    """Reads the user's learned favorite music platform from memory.db (default: youtube)."""
    try:
        facts = memory.get_db().get_all_facts(cloud_only=False)
        pref = str(facts.get("favorite_music_app") or "").strip().lower()
        if pref in MEDIA_PLATFORMS:
            return pref
    except (OSError, ValueError, RuntimeError):
        return "youtube"
    return "youtube"


def _open_media_play(
    platform: str,
    query: str,
    *,
    login_requested: bool = False,
    frustrated: bool = False,
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    """Opens Spotify/YouTube/streaming playback (API-first for Spotify when configured, else browser + MPRIS)."""
    plat = platform.strip().lower()
    q = query.strip().rstrip(".!?")
    apology = "Sorry about that — fixing it right now!\n" if frustrated else ""

    if not q or q.lower() in _PLAY_RESUME_ONLY_WORDS:
        res = control_media("play")
        return f"{apology}▶️ {res} (If a browser tab still needs a click, say 'click play'.)"

    if plat in {"youtube", "yt"}:
        target_url = _resolve_youtube_direct_watch_url(q)
        opened = open_url_safe(target_url)
        if "watch?v=" in target_url:
            _kick_browser_media_play_async()
            return (
                f"{apology}▶️ Playing '{q}' directly on YouTube — {opened}\n"
                "(Auto-sending browser Play signal once loaded; if Firefox's Autoplay Blocker still pauses new tabs, run 'celina-turbo autoplay' once.)"
            )
        return f"{apology}▶️ Opened YouTube for '{q}' — {opened}"

    if plat == "spotify":
        if approval_cb is not None and not login_requested:
            api_handled, api_msg = api_actuators.spotify_api_play(q, approval_cb=approval_cb)
            if api_handled:
                return f"{apology}{api_msg}"
        url = SITE_SEARCH_URLS["spotify"].format(q=quote(q))
        opened = open_url_safe(url)
        if login_requested:
            return (
                f"{apology}🎵 {opened}\n"
                f"• If you are already logged in to Spotify in your browser, your '{q}' results are open — click Play (or say 'play now' / 'click play').\n"
                f"• If Spotify prompts you to sign in, enter your login directly (Celina never reads or stores passwords for security)."
            )
        return f"{apology}🎵 Opened Spotify for '{q}' — {opened} (say 'play now' or 'click play' to start playback)."

    tmpl = SITE_SEARCH_URLS.get(plat, SITE_SEARCH_URLS["youtube"])
    opened = open_url_safe(tmpl.format(q=quote(q)))
    if login_requested:
        return (
            f"{apology}▶️ {opened}\n"
            f"• Sign in directly if prompted (Celina never touches passwords); if already signed in, '{q}' is ready to play."
        )
    return f"{apology}▶️ Opened {plat.title()} for '{q}' — {opened}"


def open_url_safe(url: str) -> str:
    """Opens an http:// or https:// URL safely in the user's default browser."""
    raw = (url or "").strip()
    if not raw:
        return "Error: Empty URL."
    if any(ch in raw for ch in ("\n", "\r", "\x00")):
        return "Blocked: Control characters in URL."
    # Reject any explicit non-http(s) scheme BEFORE prepending https:// for bare domains
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw):
        if not raw.lower().startswith(("http://", "https://")):
            return f"Blocked: Unsafe URL scheme in '{url}'."
    else:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or "." not in parsed.netloc:
        return f"Blocked: Unsafe or invalid URL '{url}'."
    if launch_detached(["xdg-open", raw]):
        return f"Opened {raw} in your browser."
    for browser_bin in ("firefox", "google-chrome", "chromium", "brave-browser"):
        if shutil.which(browser_bin) and launch_detached([browser_bin, raw]):
            return f"Opened {raw} in {browser_bin}."
    return f"Failed to open {raw} (no desktop browser or xdg-open available)."


def open_application(app_name: str) -> str:
    """Launches any curated or installed XDG .desktop desktop app, web app, or HTTPS URL safely."""
    raw = (app_name or "").strip()
    if not raw:
        return "Error: Provide an application or website name."

    # 1. Direct HTTP(S) URL or bare domain (e.g. 'https://open.spotify.com' or 'youtube.com')
    if raw.lower().startswith(("http://", "https://")) or (
        " " not in raw and "." in raw and _SAFE_DOMAIN_RE.fullmatch(raw)
    ):
        return open_url_safe(raw)

    key = raw.lower().strip()
    # 2. Explicit web request (e.g. 'spotify in web', 'spotify web', 'youtube in browser')
    web_forced = False
    m_web = re.fullmatch(
        r"(.+?)\s+(?:in\s+(?:the\s+)?(?:web|browser|firefox|chrome)|on\s+(?:the\s+)?(?:web|browser)|web(?:\s+app)?|website|site)",
        key,
    )
    if m_web:
        key = m_web.group(1).strip()
        web_forced = True

    if web_forced:
        if key in WEB_APPS:
            return open_url_safe(WEB_APPS[key])
        if _SAFE_DOMAIN_RE.fullmatch(key):
            return open_url_safe(key)
        return open_url_safe(f"https://www.google.com/search?q={quote(key)}")

    # 3. GNOME Control Center settings panels ("wifi settings", "bluetooth settings", etc.)
    if key in SETTINGS_PANELS:
        panel = SETTINGS_PANELS[key]
        if launch_detached(["gnome-control-center", panel]):
            return f"Opened Settings ({panel})."

    # 4. Curated local desktop apps & short web aliases (so 'open yt' opens YouTube, NEVER 'python3.14'!)
    if key in CURATED_APPS:
        cmd = CURATED_APPS[key]
        if launch_detached(cmd):
            return f"Opened {key}."
        return f"Failed to open {key} ('{cmd[0]}' unavailable)."
    if len(key) <= 3 and key in WEB_APPS:
        return open_url_safe(WEB_APPS[key])

    # 5. Installed XDG .desktop apps (APT, Flatpak, Snap, user local) — strict word/exact matching (no 'yt' -> 'python')
    if shutil.which("gtk-launch"):
        desktop_dirs = [
            config.HOME / ".local/share/applications",
            config.HOME / ".local/share/flatpak/exports/share/applications",
            Path("/var/lib/flatpak/exports/share/applications"),
            Path("/var/lib/snapd/desktop/applications"),
            Path("/usr/share/applications"),
        ]
        fallback_match: Optional[Path] = None
        wb_re = re.compile(rf"(?:^|[._\-\s]){re.escape(key)}(?:$|[._\-\s])")
        for d in desktop_dirs:
            if not d.exists():
                continue
            for entry in d.glob("*.desktop"):
                stem = entry.stem.lower()
                if key == stem or stem.endswith(f".{key}") or wb_re.search(stem) or (len(key) >= 4 and key in stem):
                    if launch_detached(["gtk-launch", entry.name]):
                        return f"Launched {entry.stem}."
                else:
                    try:
                        txt = entry.read_text(encoding="utf-8", errors="ignore")[:1200].lower()
                        if f"\nname={key}\n" in f"\n{txt}\n" or (len(key) >= 4 and f"\nname={key}" in f"\n{txt}"):
                            if launch_detached(["gtk-launch", entry.name]):
                                return f"Launched {entry.stem}."
                        if fallback_match is None and len(key) >= 4 and (
                            f"\ngenericname={key}" in f"\n{txt}"
                            or f";{key};" in txt
                        ):
                            fallback_match = entry
                    except OSError:
                        continue
        if fallback_match is not None and launch_detached(["gtk-launch", fallback_match.name]):
            return f"Launched {fallback_match.stem}."

    # 6. Seamless web fallback for popular services (so 'open spotify' works even before desktop install)
    if key in WEB_APPS:
        msg = open_url_safe(WEB_APPS[key])
        if key in CURATED_INSTALLABLE_APPS:
            return f"{msg} (Native app not installed yet — say 'install {key}' to install it.)"
        return msg

    return f"Application '{app_name}' not found."


def list_running_apps() -> str:
    """Lists top user-owned processes by memory usage."""
    out = run_capture(["ps", "-u", str(os.getuid()), "-o", "pid,comm,%cpu,%mem", "--sort=-%mem"])
    lines = out.splitlines()[:16]
    return "\n".join(lines) if lines else "No user processes found."


def search_files(query: str, directory: str = "~", extension: str = "") -> str:
    """
    Ultra-fast file search inside $HOME (<50ms via fd-find/fd, with bounded os.walk fallback).
    """
    base = Path(directory).expanduser().resolve()
    if not _is_within_home(base) or not base.exists():
        return "Error: Search directory must exist inside your home folder."

    needle = query.strip()
    ext = extension.strip().lstrip(".")
    if not needle and not ext:
        return "Please provide a filename query or extension."

    # Prefer Rust fd-find (0 MB idle RAM, blazing fast)
    fd_bin = shutil.which("fdfind") or shutil.which("fd")
    if fd_bin:
        cmd = [fd_bin, "--type", "f", "--max-results", "30", "--ignore-case"]
        if ext:
            cmd.extend(["--extension", ext])
        cmd.extend([needle or ".", str(base)])
        res = run_capture(cmd, timeout=5)
        filtered = [ln for ln in res.splitlines() if ln and not _is_sensitive_path(Path(ln))]
        return "\n".join(filtered) if filtered else f"No files matching '{query}' found in {base}."

    # Portable Python fallback
    low_needle = needle.lower()
    low_ext = f".{ext.lower()}" if ext else ""
    matches: list[str] = []
    visited = 0
    for root, dirs, files in os.walk(base):
        visited += 1
        if visited > 3000:
            break
        dirs[:] = [d for d in dirs if d not in _SKIP_SEARCH_DIRS and not d.startswith(".")]
        for fname in files:
            low_f = fname.lower()
            if low_ext and not low_f.endswith(low_ext):
                continue
            if not low_needle or low_needle in low_f:
                full = Path(root) / fname
                if not _is_sensitive_path(full):
                    matches.append(str(full))
                    if len(matches) >= 30:
                        return "\n".join(matches) + "\n...(capped at 30 results)"
    return "\n".join(matches) if matches else f"No files matching '{query}' found in {base}."


def search_in_files(text: str, directory: str = "~") -> str:
    """Fast content grep across text/code files inside $HOME (uses ripgrep when installed)."""
    base = Path(directory).expanduser().resolve()
    if not _is_within_home(base) or not base.exists():
        return "Error: Search directory must exist inside your home folder."
    needle = text.strip()
    if not needle:
        return "Provide text to search for."

    if shutil.which("rg"):
        cmd = [
            "rg",
            "--no-heading",
            "--line-number",
            "--ignore-case",
            "--fixed-strings",
            "--max-count",
            "3",
            "--max-filesize",
            "2M",
            "--glob",
            "!.ssh/**",
            "--glob",
            "!.gnupg/**",
            "--glob",
            "!.config/celina/env",
            needle,
            str(base),
        ]
        out = run_capture(cmd, timeout=6)
        lines = [ln for ln in out.splitlines() if ln][:25]
        return "\n".join(lines) if lines else f"No matches for '{needle}' in {base}."

    # Bounded Python fallback
    hits: list[str] = []
    low_q = needle.lower()
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_SEARCH_DIRS and not d.startswith(".")]
        for fname in files:
            fpath = Path(root) / fname
            if _is_sensitive_path(fpath) or fpath.suffix.lower() in {".png", ".jpg", ".pdf", ".zip", ".onnx"}:
                continue
            try:
                if fpath.stat().st_size > 512_000:
                    continue
                for idx, line in enumerate(fpath.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if low_q in line.lower():
                        hits.append(f"{fpath}:{idx}: {line.strip()[:120]}")
                        if len(hits) >= 25:
                            return "\n".join(hits) + "\n...(capped at 25 matches)"
                        break
            except OSError:
                continue
    return "\n".join(hits) if hits else f"No matches for '{needle}' in {base}."


def read_file(path: str, max_chars: int = 3500) -> str:
    """Reads plain text or routes Office/PDF files through documents.read_document."""
    target = Path(path).expanduser().resolve()
    if target.suffix.lower() in {".docx", ".odt", ".xlsx", ".pptx", ".pdf"}:
        return documents.read_document(str(target), max_chars=max_chars)
    if not _is_within_home(target):
        return "Error: Read access is limited to files inside your home directory."
    if _is_sensitive_path(target):
        return "Error: Access to sensitive credential/key files is blocked."
    if not target.is_file():
        return f"Error: File not found: {target}"
    try:
        cap = max(200, min(8000, int(max_chars)))
        text = target.read_text(encoding="utf-8", errors="replace")
        return text[:cap] + (f"\n...[truncated at {cap} chars]" if len(text) > cap else "")
    except Exception as exc:
        return f"Error reading file: {exc}"


def get_clipboard() -> str:
    if not shutil.which("wl-paste"):
        return "Error: wl-clipboard (wl-paste) is not installed."
    content = run_capture(["wl-paste", "--no-newline"], timeout=3)
    if not content:
        return "Clipboard is empty."
    return content[:1500] + ("...[truncated]" if len(content) > 1500 else "")


def get_highlighted_text() -> str:
    """Reads currently highlighted text from Wayland primary selection without Ctrl+C."""
    if not shutil.which("wl-paste"):
        return "Error: wl-clipboard (wl-paste) is not installed."
    content = run_capture(["wl-paste", "--primary", "--no-newline"], timeout=3)
    if not content:
        # Graceful fallback: inform model if nothing is highlighted
        return "No text is currently highlighted (primary selection is empty)."
    return content[:2000] + ("...[truncated]" if len(content) > 2000 else "")


def set_clipboard(text: str) -> str:
    if not shutil.which("wl-copy"):
        return "Error: wl-clipboard (wl-copy) is not installed."
    try:
        subprocess.run(["wl-copy"], input=text, text=True, timeout=3, check=False)
        return "Copied text to clipboard."
    except Exception as exc:
        return f"Error copying to clipboard: {exc}"


def set_timer(seconds: int, message: str = "Timer finished!") -> str:
    sec = int(seconds)
    if sec < 1 or sec > 86400 * 7:
        return "Timer duration must be between 1 second and 7 days."
    trigger_at = time.time() + sec
    memory.get_db().add_reminder(message, trigger_at)
    due_str = datetime.datetime.fromtimestamp(trigger_at).strftime("%I:%M:%S %p")
    return f"Reminder set for {due_str}: {message}"


class _DDGParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_title = False
        self._in_snippet = False
        self._cur_title = ""
        self._cur_href = ""
        self._cur_snippet = ""

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        cls = attr_map.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._in_title = True
            self._cur_title = ""
            raw_href = attr_map.get("href", "")
            if "uddg=" in raw_href:
                qs = parse_qs(urlparse(raw_href).query)
                self._cur_href = unquote(qs.get("uddg", [raw_href])[0])
            else:
                self._cur_href = raw_href
        elif tag in {"a", "div", "span"} and "result__snippet" in cls:
            self._in_snippet = True
            self._cur_snippet = ""

    def handle_endtag(self, tag):
        if tag == "a" and self._in_title:
            self._in_title = False
            self.results.append(
                {"title": self._cur_title.strip(), "url": self._cur_href.strip(), "snippet": ""}
            )
        elif self._in_snippet and tag in {"a", "div", "span"}:
            self._in_snippet = False
            if self.results:
                self.results[-1]["snippet"] = self._cur_snippet.strip()

    def handle_data(self, data):
        if self._in_title:
            self._cur_title += data
        elif self._in_snippet:
            self._cur_snippet += data


class _PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)


_BROWSER_UA = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:136.0) Gecko/20100101 Firefox/136.0"


def _search_ddg_html(q: str) -> list[dict[str, str]]:
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for endpoint in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            url_with_q = f"{endpoint}?q={quote(q)}"
            resp = net_policy.safe_get(
                url_with_q,
                allow_redirects=False,
                max_bytes=500_000,
                timeout=(5.0, 8.0),
                allowed_content_types={"text/html", "application/xhtml+xml"},
                headers=headers,
            )
            body = resp.text or ""
            low = body.lower()
            if resp.status_code != 200 or "anomaly-modal" in low or "challenge-form" in low or "botnet" in low:
                continue
            parser = _DDGParser()
            parser.feed(body)
            top = [r for r in parser.results if r.get("title")][:5]
            if top:
                return top
        except Exception:
            continue
    return []


def _search_fallback_apis(q: str) -> list[dict[str, str]]:
    """Zero-key fallbacks (Wikipedia Opensearch + HN Algolia) via centralized SSRF-safe net_policy."""
    headers = {"User-Agent": _BROWSER_UA, "Accept": "application/json"}
    results: list[dict[str, str]] = []
    # 1. Wikipedia Opensearch
    try:
        wiki_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={quote(q)}&limit=3&namespace=0&format=json"
        r = net_policy.safe_get(
            wiki_url,
            allow_redirects=False,
            max_bytes=250_000,
            timeout=(4.0, 6.0),
            allowed_content_types={"application/json"},
            headers=headers,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) >= 4:
                titles, descs, urls = data[1], data[2], data[3]
                for title, desc, url in zip(titles, descs, urls):
                    results.append({"title": f"{title} (Wikipedia)", "url": str(url), "snippet": str(desc or "")})
    except Exception:
        pass

    # 2. HackerNews Algolia
    try:
        hn_url = f"https://hn.algolia.com/api/v1/search?query={quote(q)}&hitsPerPage=3&tags=story"
        r = net_policy.safe_get(
            hn_url,
            allow_redirects=False,
            max_bytes=250_000,
            timeout=(4.0, 6.0),
            allowed_content_types={"application/json"},
            headers=headers,
        )
        if r.status_code == 200:
            for hit in (r.json().get("hits") or [])[:3]:
                title = (hit.get("title") or "").strip()
                url = (hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}").strip()
                if title:
                    results.append(
                        {
                            "title": title,
                            "url": url,
                            "snippet": f"HN discussion ({hit.get('points', 0)} pts, {hit.get('num_comments', 0)} comments)",
                        }
                    )
    except Exception:
        pass
    return results[:5]


def web_search(query: str) -> str:
    q = query.strip()
    if not q:
        return "Provide a search query."
    sc = cache.get_cache()
    hit = sc.get("web_search", q)
    if hit is not None:
        return hit[0]
    try:
        top = _search_ddg_html(q)
        if not top:
            top = _search_fallback_apis(q)
        if not top:
            return f"No web results found for '{q}'."
        result = "\n".join(
            f"{idx}. {r['title']}\n   URL: {r['url']}\n   {r['snippet']}" for idx, r in enumerate(top, 1)
        )
        sc.set("web_search", q, result)
        return result
    except Exception as exc:
        return f"Web search failed: {exc}"


@tool(name="fetch_webpage", tier=1, domain="web_comms", opt_in=True)
def fetch_webpage(url: str, resolver: Optional[net_policy.ResolverFn] = None) -> str:
    """Fetches a public HTTPS webpage strictly via centralized net_policy (opt-in web_comms + anti-SSRF)."""
    net_policy.check_web_comms_allowed("fetch_webpage", url)
    try:
        net_policy.validate_public_url(url, resolver=resolver)
    except net_policy.URLPolicyError as exc:
        return f"Blocked by network policy: {exc}"
    sc = cache.get_cache()
    hit = sc.get("fetch_webpage", url)
    if hit is not None:
        return hit[0]
    try:
        resp = net_policy.safe_get(
            url,
            allow_redirects=True,
            max_redirects=3,
            max_bytes=512_000,
            timeout=(5.0, 10.0),
            allowed_content_types={"text/html", "text/plain", "application/xhtml+xml", "application/json", "text/markdown"},
            headers={"User-Agent": _BROWSER_UA},
            resolver=resolver,
        )
        if resp.status_code >= 400:
            return f"Failed to fetch webpage: HTTP {resp.status_code}"
        parser = _PlainTextParser()
        parser.feed(resp.text[:150000])
        text = " ".join(parser.chunks)
        result = text[:3500] + ("...[truncated]" if len(text) > 3500 else "")
        sc.set("fetch_webpage", url, result)
        return result
    except net_policy.URLPolicyError as exc:
        return f"Blocked by network policy: {exc}"
    except Exception as exc:
        return f"Failed to fetch webpage: {exc}"


@tool(name="fetch_url_text", tier=1, domain="web_comms", opt_in=True)
def fetch_url_text(url: str, resolver: Optional[net_policy.ResolverFn] = None) -> str:
    """Opt-in live-data tool: fetches cleaned plain text from a public HTTPS URL via net_policy."""
    net_policy.check_web_comms_allowed("fetch_url_text", url)
    return fetch_webpage(url, resolver=resolver)


@tool(name="get_weather", tier=1, domain="web_comms", opt_in=True)
def get_weather(city: str, resolver: Optional[net_policy.ResolverFn] = None) -> str:
    """Opt-in live-data tool: fetches live weather conditions for a city over HTTPS."""
    clean_city = (city or "").strip()
    if not clean_city:
        return "Provide a city name for weather lookup."
    url = f"https://wttr.in/{quote(clean_city)}?format=j1"
    net_policy.check_web_comms_allowed("get_weather", url)
    try:
        resp = net_policy.safe_get(
            url,
            allow_redirects=False,
            max_bytes=256_000,
            timeout=(5.0, 10.0),
            allowed_content_types={"application/json", "text/plain"},
            headers={"User-Agent": _BROWSER_UA},
            resolver=resolver,
        )
        if resp.status_code >= 400:
            return f"Weather lookup failed: HTTP {resp.status_code}"
        data = resp.json()
        cur = (data.get("current_condition") or [{}])[0]
        temp_c = cur.get("temp_C", "?")
        feels_c = cur.get("FeelsLikeC", temp_c)
        hum = cur.get("humidity", "?")
        wind = cur.get("windspeedKmph", "?")
        desc_list = cur.get("weatherDesc") or [{}]
        desc = desc_list[0].get("value", "Unknown") if isinstance(desc_list[0], dict) else str(desc_list[0])
        return f"Weather in {clean_city}: {desc}, {temp_c}°C (feels like {feels_c}°C), humidity {hum}%, wind {wind} km/h."
    except net_policy.URLPolicyError as exc:
        return f"Blocked by network policy: {exc}"
    except Exception as exc:
        return f"Weather lookup failed: {exc}"


@tool(name="get_news_headlines", tier=1, domain="web_comms", opt_in=True)
def get_news_headlines(topic: str = "world", resolver: Optional[net_policy.ResolverFn] = None) -> str:
    """Opt-in live-data tool: fetches top recent news headlines for a topic over HTTPS RSS."""
    clean_topic = (topic or "world").strip() or "world"
    url = f"https://news.google.com/rss/search?q={quote(clean_topic)}&hl=en-US&gl=US&ceid=US:en"
    net_policy.check_web_comms_allowed("get_news_headlines", url)
    try:
        resp = net_policy.safe_get(
            url,
            allow_redirects=True,
            max_redirects=2,
            max_bytes=512_000,
            timeout=(5.0, 10.0),
            allowed_content_types={"application/rss+xml", "application/xml", "text/xml", "text/html"},
            headers={"User-Agent": _BROWSER_UA},
            resolver=resolver,
        )
        if resp.status_code >= 400:
            return f"News lookup failed: HTTP {resp.status_code}"
        root = ET.fromstring(resp.text)
        items = root.findall(".//item")[:5]
        if not items:
            return f"No news headlines found for '{clean_topic}'."
        lines = [f"Top headlines for '{clean_topic}':"]
        for idx, item in enumerate(items, 1):
            title = (item.findtext("title") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            lines.append(f"{idx}. {title}" + (f" ({pub})" if pub else ""))
        return "\n".join(lines)
    except net_policy.URLPolicyError as exc:
        return f"Blocked by network policy: {exc}"
    except Exception as exc:
        return f"News lookup failed: {exc}"


_CITY_TIMEZONE_MAP: dict[str, str] = {
    "hyderabad": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata",
    "chennai": "Asia/Kolkata",
    "london": "Europe/London",
    "new york": "America/New_York",
    "san francisco": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "tokyo": "Asia/Tokyo",
    "singapore": "Asia/Singapore",
    "sydney": "Australia/Sydney",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "dubai": "Asia/Dubai",
    "utc": "UTC",
}


@tool(name="get_current_time_zone", tier=1, domain="web_comms", opt_in=True)
def get_current_time_zone(city: str, resolver: Optional[net_policy.ResolverFn] = None) -> str:
    """Opt-in live-data tool: fetches live current time & time zone details for a city/IANA zone."""
    clean_city = (city or "").strip()
    if not clean_city:
        return "Provide a city or IANA timezone name (e.g. 'Tokyo' or 'Asia/Kolkata')."
    tz_name = _CITY_TIMEZONE_MAP.get(clean_city.lower(), clean_city)
    url = f"https://timeapi.io/api/time/current/zone?timeZone={quote(tz_name)}"
    net_policy.check_web_comms_allowed("get_current_time_zone", url)
    try:
        resp = net_policy.safe_get(
            url,
            allow_redirects=False,
            max_bytes=64_000,
            timeout=(5.0, 10.0),
            allowed_content_types={"application/json", "text/plain"},
            headers={"User-Agent": _BROWSER_UA},
            resolver=resolver,
        )
        if resp.status_code < 400:
            data = resp.json()
            dt_str = data.get("dateTime") or f"{data.get('date', '')} {data.get('time', '')}".strip()
            resolved_tz = data.get("timeZone") or tz_name
            return f"Current time in {clean_city} ({resolved_tz}): {dt_str}"
    except net_policy.URLPolicyError as exc:
        return f"Blocked by network policy: {exc}"
    except Exception:
        pass
    try:
        now = datetime.datetime.now(ZoneInfo(tz_name))
        return f"Current time in {clean_city} ({tz_name}): {now.strftime('%Y-%m-%d %H:%M:%S %Z (UTC%z)')}"
    except Exception as exc:
        return f"Time zone lookup failed for '{clean_city}': {exc}"


def _cached_read_document(path: str, max_chars: int = 4000) -> str:
    fp = cache.file_fingerprint(path)
    key = f"{Path(path).expanduser().resolve()}:{fp}:{max_chars}"
    sc = cache.get_cache()
    if fp != "missing":
        hit = sc.get("document", key)
        if hit is not None:
            return hit[0]
    res = documents.read_document(path, max_chars=max_chars)
    if fp != "missing":
        sc.set("document", key, res)
    return res


def cache_status() -> str:
    return cache.get_cache().format_stats()


def clear_cache() -> str:
    cache.get_cache().clear()
    return "Smart cache cleared and compacted (0 KB)."


def mcp_list_servers() -> str:
    return mcp_client.get_mcp().list_servers()


def mcp_list_tools(server_name: str) -> str:
    return mcp_client.get_mcp().list_tools(server_name)


def mcp_call_tool(
    server_name: str,
    tool_name: str,
    arguments: Optional[dict[str, Any]] = None,
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    return mcp_client.get_mcp().call_tool(
        server_name=server_name,
        tool_name=tool_name,
        arguments=arguments,
        approval_cb=approval_cb,
    )


def remember_fact(key: str, value: str) -> str:
    memory.get_db().set_fact(key, value)
    return f"Saved to long-term memory: {key.strip()} = {value.strip()}"


def link_knowledge(
    subject: str = "", predicate: str = "", obj: str = "", query_entity: str = ""
) -> str:
    """Adds a Semantic Knowledge Graph triple (Subject -> Predicate -> Object) or queries 2-hop links."""
    db = memory.get_db()
    if query_entity.strip() and not (subject.strip() and predicate.strip() and obj.strip()):
        links = db.query_knowledge_graph(query_entity.strip(), hops=2, limit=12, cloud_only=False)
        if not links:
            return f"No Knowledge Graph links found for '{query_entity.strip()}'."
        lines = [f"- [hop {r['hop']}] {r['subject']} --[{r['predicate']}]--> {r['object']}" for r in links]
        return f"Knowledge Graph links for '{query_entity.strip()}':\n" + "\n".join(lines)

    if not (subject.strip() and predicate.strip() and obj.strip()):
        return "Error: Provide subject, predicate, and obj to add a link, or query_entity to inspect the graph."
    res = db.add_knowledge_triple(subject, predicate, obj)
    return f"Saved Knowledge Graph link: {res['subject']} --[{res['predicate']}]--> {res['object']}"


def manage_goal(
    action: str = "upsert",
    goal_id: str = "",
    title: str = "",
    status: str = "active",
    sub_tasks: Optional[list[str]] = None,
    deadline: str = "",
) -> str:
    """Creates, updates, lists, or completes persistent multi-step goals across sessions."""
    db = memory.get_db()
    act = (action or "upsert").strip().lower()
    if act == "list":
        goals = db.list_goals(status=status or "active", limit=10, cloud_only=False)
        if not goals:
            return f"No goals found (status={status or 'active'})."
        lines = []
        for g in goals:
            due = f" | due {g['deadline']}" if g["deadline"] else ""
            subs = f"\n  sub-tasks: {', '.join(g['sub_tasks'])}" if g["sub_tasks"] else ""
            lines.append(f"- [{g['status'].upper()}] {g['goal_id']}: {g['title']}{due}{subs}")
        return "Persistent Goals:\n" + "\n".join(lines)

    if act == "complete":
        gid = goal_id.strip() or title.strip()
        if not gid:
            return "Error: Provide goal_id or title to mark completed."
        g = db.upsert_goal(goal_id=gid, title=title or gid, status="completed")
        return f"✅ Marked goal '{g['goal_id']}' ({g['title']}) as COMPLETED."

    if not (title.strip() or goal_id.strip()):
        return "Error: Provide a goal title or goal_id."
    g = db.upsert_goal(
        goal_id=goal_id or title,
        title=title or goal_id,
        status=status or "active",
        sub_tasks=sub_tasks,
        deadline=deadline,
    )
    return (
        f"🎯 Goal saved: [{g['status'].upper()}] {g['goal_id']} — {g['title']}"
        + (f" (due {g['deadline']})" if g["deadline"] else "")
        + (f" [{len(g['sub_tasks'])} sub-task(s)]" if g["sub_tasks"] else "")
    )


def search_memory(query: str) -> str:
    db = memory.get_db()
    recalled_facts, recalled_eps = db.recall_relevant(query, max_facts=6, max_episodes=4, cloud_only=False)
    kg_hits = db.recall_knowledge_triples(query, limit=6, cloud_only=False)
    history = db.search_history(query, limit=6)
    parts: list[str] = []
    if recalled_facts:
        parts.append("Matching facts (FTS5 BM25):\n" + "\n".join(f"- {k}: {v}" for k, v in recalled_facts))
    if kg_hits:
        parts.append("Matching Knowledge Graph links:\n" + "\n".join(f"- {k}" for k in kg_hits))
    if recalled_eps:
        parts.append("Matching past episodes:\n" + "\n".join(f"- {ep}" for ep in recalled_eps))
    if history:
        parts.append(
            "Matching recent history:\n"
            + "\n".join(f"[{h['time']}] {h['role']}: {h['content'][:160]}" for h in history)
        )
    return "\n\n".join(parts) if parts else f"No memory entries found matching '{query}'."


# ============================================================================
# Tier 2: Approval-Gated Full Control (Files, Apps, Messaging, Sandboxed Shell, IoT)
# ============================================================================

def is_catastrophic_command(command: str) -> bool:
    return any(pat.search(command) for pat in _CATASTROPHIC_SHELL_PATTERNS)


def _build_bwrap_command(
    cmd: str,
    *,
    allow_network: bool = False,
    work_dir: Optional[Path] = None,
) -> list[str]:
    """
    Capability-Based Bubblewrap Sandbox (Phase 5 Item 11):
      - Read-only root filesystem (`--ro-bind / /`)
      - NO full `$HOME` read-write bind! Mounts ONLY an isolated writable `/work` directory
        (or explicitly scoped work_dir) while masking `$HOME` with empty tmpfs so browser
        profiles, keyrings, `.ssh`, `.gnupg`, `.aws`, `.kube`, `.config/celina`, `.netrc`,
        `.git-credentials`, and `.password-store` are completely unreachable!
      - Network disabled by default (`--unshare-net` unless explicitly allowed)
      - Isolated PID, IPC, UTS namespaces (`--unshare-pid --unshare-ipc --unshare-uts`)
      - Cleared environment (`--clearenv`) with NO API keys, NO host D-Bus, NO session sockets
      - Resource limits via ulimit (CPU 15s, file size 32MB, max processes 64)
    """
    scoped_work = (work_dir or Path(tempfile.gettempdir())).resolve()
    home_resolved = config.HOME.resolve()
    bwrap_args = [
        "bwrap",
        "--ro-bind", "/", "/",
        "--tmpfs", str(home_resolved),
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        "--bind", str(scoped_work), "/work",
        "--chdir", "/work",
        "--dev", "/dev",
        "--proc", "/proc",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/work",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "TERM", "xterm",
    ]
    if not allow_network:
        bwrap_args.append("--unshare-net")

    wrapped = f"ulimit -t 15 -f 65536 -u 64 2>/dev/null || true; {cmd}"
    bwrap_args.extend(["bash", "-c", wrapped])
    return bwrap_args


_BWRAP_AVAILABLE: Optional[bool] = None


def _can_use_bwrap() -> bool:
    """
    Runtime probe for Ubuntu 24.04/26.04 AppArmor unprivileged userns & symlinked mount restrictions.
    Probes the actual tmpfs/namespace configuration so runtime EPERM or mount errors are caught upfront.
    """
    global _BWRAP_AVAILABLE
    if _BWRAP_AVAILABLE is not None:
        return _BWRAP_AVAILABLE
    if not shutil.which("bwrap"):
        _BWRAP_AVAILABLE = False
        return False
    try:
        home_resolved = config.HOME.resolve()
        probe = subprocess.run(
            [
                "bwrap",
                "--ro-bind", "/", "/",
                "--tmpfs", str(home_resolved),
                "--tmpfs", "/tmp",
                "--dev", "/dev",
                "--proc", "/proc",
                "--unshare-pid",
                "--unshare-ipc",
                "--unshare-uts",
                "--unshare-net",
                "true",
            ],
            capture_output=True,
            timeout=3,
        )
        _BWRAP_AVAILABLE = probe.returncode == 0
    except Exception:
        _BWRAP_AVAILABLE = False
    return _BWRAP_AVAILABLE


def run_shell_command(
    command: str,
    sandbox: bool = True,
    allow_network: bool = False,
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    """
    Runs a shell command with exact argument-bound approval and capability-based Bubblewrap
    sandboxing. If bwrap is unavailable when sandbox=True, NEVER silently executes unsandboxed:
    displays a prominent UNSANDBOXED HOST SHELL warning and requires an explicit second
    confirmation bound to the exact unsandboxed host execution mode!
    """
    cmd = command.strip()
    if not cmd:
        return "Error: Empty command."
    if is_catastrophic_command(cmd):
        return "Blocked: Command matched catastrophic safety denylist."
    if approval_cb is None:
        return "Denied: Shell commands require interactive user approval."

    bwrap_ok = _can_use_bwrap() if sandbox else False
    use_bwrap = bool(sandbox and bwrap_ok)

    if use_bwrap:
        args = {"command": cmd, "mode": "bwrap_sandbox", "allow_network": bool(allow_network)}
        token, deny_err = approval.request_bound_approval(
            "run_shell_command",
            args,
            approval_cb,
            title="Approve Sandboxed Shell Command (Bubblewrap)?",
            target="bwrap(/work, no-home, no-dbus" + (", net-on" if allow_network else ", net-off") + ")",
        )
        if deny_err:
            return deny_err
        ok, err = approval.verify_and_consume(token, "run_shell_command", args)
        if not ok:
            return err

        with tempfile.TemporaryDirectory(prefix="celina_sandbox_") as tmp_work:
            os.chmod(tmp_work, 0o700)
            exec_cmd = _build_bwrap_command(cmd, allow_network=allow_network, work_dir=Path(tmp_work))
            res = command_runner.run_command(exec_cmd, timeout=20.0)
        out = ((res.stdout or "") + (f"\nSTDERR:\n{res.stderr}" if res.stderr else "")).strip() or "(No output)"
        if len(out) > 3000:
            out = out[:3000] + "\n...[truncated]"
        if res.timed_out:
            return "Error: Sandboxed shell command timed out after 20s."
        return f"[bwrap sandbox] Exit code: {res.returncode}\n{out}"

    # Unsandboxed host shell path: prominent warning + explicit confirmation (and second confirmation if sandbox was requested!)
    if sandbox and not bwrap_ok:
        first_args = {"command": cmd, "mode": "sandbox_unavailable_ack"}
        t1, err1 = approval.request_bound_approval(
            "run_shell_command",
            first_args,
            approval_cb,
            title="WARNING: Bubblewrap Sandbox Unavailable — Run UNSANDBOXED on Host?",
            target="HOST SHELL (UNSANDBOXED)",
            extra_warning=(
                "Bubblewrap (bwrap) is unavailable or blocked by OS AppArmor userns. "
                "Continuing will run DIRECTLY ON YOUR HOST with full user permissions!"
            ),
        )
        if err1:
            return f"Refused unsandboxed fallback: {err1}"
        ok1, verr1 = approval.verify_and_consume(t1, "run_shell_command", first_args)
        if not ok1:
            return verr1

    host_args = {"command": cmd, "mode": "unsandboxed_host_shell"}
    t2, err2 = approval.request_bound_approval(
        "run_shell_command",
        host_args,
        approval_cb,
        title="CONFIRM UNSANDBOXED HOST SHELL EXECUTION",
        target="HOST SHELL (UNSANDBOXED)",
        extra_warning="FINAL CONFIRMATION: This command runs UNSANDBOXED directly on your host OS.",
    )
    if err2:
        return err2
    ok2, verr2 = approval.verify_and_consume(t2, "run_shell_command", host_args)
    if not ok2:
        return verr2

    res = command_runner.run_command(["bash", "-lc", cmd], timeout=20.0)
    if res.timed_out:
        return "Error: Host shell command timed out after 20s."
    out = ((res.stdout or "") + (f"\nSTDERR:\n{res.stderr}" if res.stderr else "")).strip() or "(No output)"
    if len(out) > 3000:
        out = out[:3000] + "\n...[truncated]"
    return f"[UNSANDBOXED HOST SHELL] Exit code: {res.returncode}\n{out}"


def run_python_sandbox(
    code: str,
    timeout: int = 5,
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    """
    Self-Verifying Code Execution Sandbox for The Coder / Engineer swarm role (v4.4):
      - Runs Python snippets inside Bubblewrap (`bwrap --unshare-net --unshare-pid --ro-bind / /
        --tmpfs <HOME> --tmpfs /tmp --bind <work> /work --clearenv`) with 1..15s timeout.
      - Never exposes host files, API keys, or network.
      - On non-zero exit / Traceback, returns `Error (Python sandbox exit=N): ...` so
        router.py's Critic Verifier automatically feeds stderr back to Engineer for self-repair!
    """
    src = (code or "").strip()
    if not src:
        return "Error: Empty Python code snippet."
    if len(src) > 16384:
        return "Error: Python snippet exceeds 16 KB sandbox limit."
    if approval_cb is None:
        return "Denied: Sandboxed Python execution requires interactive user approval."

    sec = max(1, min(15, int(timeout)))
    bwrap_ok = _can_use_bwrap()
    mode = "bwrap_python_sandbox" if bwrap_ok else "isolated_python_no_bwrap"
    args = {"code": src, "timeout": sec, "mode": mode}
    token, deny_err = approval.request_bound_approval(
        "run_python_sandbox",
        args,
        approval_cb,
        title=(
            f"Approve Sandboxed Python Execution ({sec}s, no-net)?"
            if bwrap_ok
            else f"WARNING: Bubblewrap Unavailable — Approve Isolated Python ({sec}s)?"
        ),
        target="bwrap(python3 -I, no-net, no-home)" if bwrap_ok else "python3 -I (isolated env, no bwrap)",
        extra_warning=(
            ""
            if bwrap_ok
            else "Bubblewrap userns is unavailable on this host; script will run with `python3 -I` and stripped environment."
        ),
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "run_python_sandbox", args)
    if not ok:
        return err

    with tempfile.TemporaryDirectory(prefix="celina_pysandbox_") as tmp_work:
        work_path = Path(tmp_work).resolve()
        script_file = work_path / "snippet.py"
        script_file.write_text(src + "\n", encoding="utf-8")
        if bwrap_ok:
            home_resolved = config.HOME.resolve()
            # Never invoke a venv python inside $HOME when `--tmpfs $HOME` intentionally hides $HOME!
            sandbox_py = shutil.which("python3", path="/usr/bin:/bin") or "/usr/bin/python3"
            cmd = [
                "bwrap",
                "--ro-bind", "/", "/",
                "--tmpfs", str(home_resolved),
                "--tmpfs", "/tmp",
                "--tmpfs", "/run",
                "--bind", str(work_path), "/tmp/work",
                "--chdir", "/tmp/work",
                "--dev", "/dev",
                "--proc", "/proc",
                "--unshare-net",
                "--unshare-pid",
                "--unshare-ipc",
                "--unshare-uts",
                "--die-with-parent",
                "--clearenv",
                "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
                "--setenv", "HOME", "/tmp/work",
                "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
                "--setenv", "LANG", "C.UTF-8",
                sandbox_py, "-I", "/tmp/work/snippet.py",
            ]
            res = command_runner.run_command(cmd, timeout=float(sec))
        else:
            clean_env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(work_path),
                "TMPDIR": str(work_path),
                "PYTHONDONTWRITEBYTECODE": "1",
                "LANG": "C.UTF-8",
            }
            res = command_runner.run_command(
                [sys.executable, "-I", str(script_file)],
                timeout=float(sec),
                cwd=str(work_path),
                env=clean_env,
            )

    if res.timed_out:
        return f"Error: Python sandbox timed out after {sec}s."
    combined = ((res.stdout or "") + (f"\nSTDERR:\n{res.stderr}" if res.stderr else "")).strip() or "(No output)"
    if len(combined) > 3000:
        combined = combined[:3000] + "\n...[truncated]"
    tag = "bwrap-python" if bwrap_ok else "python-isolated"
    if res.returncode != 0:
        return f"Error: ({tag} exit={res.returncode})\n{combined}"
    return f"[{tag}] Verified OK (exit=0):\n{combined}"


def transcribe_system_audio_tool(
    seconds: int = 30,
    lang_hint: str = "auto",
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    """Approval-gated wrapper for offline PipeWire loopback meeting/video audio transcription."""
    dur = max(3, min(120, int(seconds)))
    hint = (lang_hint or "auto").strip().lower()
    args = {"seconds": dur, "lang_hint": hint}
    token, deny_err = approval.request_bound_approval(
        "transcribe_system_audio",
        args,
        approval_cb,
        title=f"Approve Offline System Audio Capture ({dur}s via PipeWire Monitor)?",
        target=f"pipewire-monitor ({dur}s, 100% offline faster-whisper)",
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "transcribe_system_audio", args)
    if not ok:
        return err

    from . import voice

    return voice.transcribe_system_audio(seconds=dur, lang_hint=hint)


def set_assistant_identity(name: str, nickname: Optional[str] = None) -> str:
    """
    Live-updates the assistant's primary name and optional nickname (default: Celina / Celly)
    and persists atomically across reboots. Honestly reports any disk persistence failure
    without falsely claiming 'Saved persistently'.
    """
    try:
        new_name, new_nick = config.set_assistant_identity(name=name, nickname=nickname)
    except config.PersistenceError as exc:
        return f"Error: Identity NOT saved (rolled back in memory): {exc}"
    except ValueError as exc:
        return f"Error: {exc}"
    nick_note = f" (nickname: {new_nick})" if new_nick else ""
    return f"Identity updated! I am now {new_name}{nick_note}. Saved persistently for future sessions."


def save_workspace_snapshot(
    name: str, triggers: str = "", apps: Optional[list[str]] = None
) -> str:
    """
    Captures currently visible desktop apps + open documents into a named executable skill
    so saying 'Launch my <name> workspace' replays them deterministically in 0 LLM tokens!
    """
    snap_name = (name or "").strip()
    if not snap_name:
        return "Error: Provide a workspace snapshot name (e.g. 'Exam Prep' or 'Coding')."

    captured = automation.list_visible_workspace_apps() if not apps else [
        {"app": str(a).strip(), "title": "", "doc_path": ""} for a in apps if str(a).strip()
    ]
    if not captured:
        return (
            "No visible desktop applications were detected via AT-SPI2. "
            "You can also pass explicit app names (e.g. apps=['firefox', 'code', 'gnome-terminal'])."
        )

    steps: list[dict[str, Any]] = []
    app_names: list[str] = []
    for item in captured:
        app = item.get("app", "").strip()
        doc = item.get("doc_path", "").strip()
        if not app:
            continue
        app_names.append(app)
        steps.append({"tool": "open_application", "args": {"app_name": app}})
        if doc:
            steps.append({"tool": "open_document", "args": {"path": doc}})

    if not steps:
        return "Error: Could not extract any launchable apps for the snapshot."

    trig_list = [t.strip() for t in triggers.split(",") if t.strip()]
    for default_t in (
        f"{snap_name.lower()} workspace",
        f"launch {snap_name.lower()}",
        f"restore {snap_name.lower()}",
        snap_name.lower(),
    ):
        if default_t not in trig_list:
            trig_list.append(default_t)

    saved_msg = skills.learn_skill(
        name=f"Workspace: {snap_name}",
        steps=steps,
        triggers=trig_list,
        description=f"Saved desktop workspace snapshot ({', '.join(app_names)})",
    )
    return f"Saved Workspace Snapshot '{snap_name}' ({len(app_names)} apps: {', '.join(app_names)}).\n{saved_msg}"


def trigger_iot_webhook(
    webhook_url: str,
    payload_json: str = "",
    method: str = "POST",
    approval_cb: Optional[ApprovalCallback] = None,
    allowlist_path: Optional[Path] = None,
    resolver: Optional[net_policy.ResolverFn] = None,
) -> str:
    """
    Triggers an explicitly allowlisted Home Assistant / Node-RED smart-home webhook (Tier 2).
    Protected by centralized net_policy IoT allowlist (host/port/method/scheme + cloud-metadata
    & Ollama blocks + no redirects) and exact argument-bound user approval.
    """
    raw_url = (webhook_url or "").strip()
    http_method = (method or "POST").strip().upper()

    body_data: Optional[dict[str, Any]] = None
    if payload_json.strip():
        try:
            loaded = json.loads(payload_json)
            if isinstance(loaded, dict):
                body_data = loaded
            else:
                return "Error: payload_json must be a JSON object."
        except json.JSONDecodeError as exc:
            return f"Error: Invalid payload_json: {exc}"

    allowlist = net_policy.load_iot_allowlist(allowlist_path or config.IOT_ALLOWLIST_FILE)
    try:
        net_policy.validate_iot_url(raw_url, http_method, allowlist=allowlist, resolver=resolver)
    except net_policy.URLPolicyError as exc:
        return f"Blocked by IoT network policy: {exc}"

    args = {"webhook_url": raw_url, "method": http_method, "payload": body_data}
    token, deny_err = approval.request_bound_approval(
        "trigger_iot_webhook",
        args,
        approval_cb,
        title="Approve Allowlisted IoT / Smart Home Webhook?",
        target=raw_url,
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "trigger_iot_webhook", args)
    if not ok:
        return err

    try:
        resp = net_policy.safe_request(
            http_method,
            raw_url,
            policy="iot",
            allow_redirects=False,
            max_bytes=256_000,
            timeout=(4.0, 8.0),
            headers={"User-Agent": "Celina-IoT-Bridge/4.3"},
            json_body=body_data if http_method != "GET" else None,
            iot_allowlist=allowlist,
            resolver=resolver,
        )
        msg = f"IoT webhook HTTP {resp.status_code}: {(resp.text or 'OK').strip()[:400]}"
        api_actuators._record_api_evidence(
            "trigger_iot_webhook", {"url": raw_url, "method": http_method}, "ok", msg
        )
        return msg
    except net_policy.URLPolicyError as exc:
        return f"Blocked by IoT network policy: {exc}"
    except Exception as exc:
        api_actuators._record_api_evidence(
            "trigger_iot_webhook", {"url": raw_url, "method": http_method}, "error", str(exc)
        )
        return f"IoT webhook error: {exc}"


def write_file(path: str, content: str, approval_cb: Optional[ApprovalCallback] = None) -> str:
    target = Path(path).expanduser().resolve()
    if not _is_within_home(target):
        return "Error: write_file is restricted to paths inside your home directory."
    if _is_sensitive_path(target) or target.name in {".bashrc", ".zshrc", ".profile"}:
        return "Error: Writing sensitive credential or shell startup files is blocked."

    args = {
        "path": str(target),
        "content_len": len(content),
        "sha256_prefix": approval.compute_arguments_hash("write_file", {"content": content})[:16],
    }
    token, deny_err = approval.request_bound_approval(
        "write_file", args, approval_cb, title="Approve File Write?", target=str(target)
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "write_file", args)
    if not ok:
        return err

    aid = state_ops.get_undo_journal().record_before_mutation("write_file", target)
    try:
        if target.exists():
            documents.backup_file(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.celina.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=True, final_path=target)
        return f"Wrote {len(content)} characters to {target}. [undo: {aid}]"
    except Exception as exc:
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
        return f"Failed to write file: {exc}"


def edit_file(
    path: str, old_text: str, new_text: str, approval_cb: Optional[ApprovalCallback] = None
) -> str:
    """Surgically replaces text in an existing file inside ~ with automatic backup + bound approval."""
    target = Path(path).expanduser().resolve()
    if not _is_within_home(target) or not target.is_file():
        return f"Error: File not found inside home directory: {target}"
    if _is_sensitive_path(target) or target.name in {".bashrc", ".zshrc", ".profile"}:
        return "Error: Editing sensitive credential or shell startup files is blocked."

    orig = target.read_text(encoding="utf-8", errors="replace")
    if old_text not in orig:
        return f"Error: Target text not found in {target.name}."

    args = {"path": str(target), "old_text": old_text, "new_text": new_text}
    token, deny_err = approval.request_bound_approval(
        "edit_file", args, approval_cb, title="Approve File Edit?", target=str(target)
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "edit_file", args)
    if not ok:
        return err

    aid = state_ops.get_undo_journal().record_before_mutation("edit_file", target)
    try:
        bak = documents.backup_file(target)
        updated = orig.replace(old_text, new_text, 1)
        tmp = target.with_name(f".{target.name}.celina.tmp")
        tmp.write_text(updated, encoding="utf-8")
        os.replace(tmp, target)
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=True, final_path=target)
        return f"Edited {target.name} (backup saved: {bak.name if bak else 'none'}). [undo: {aid}]"
    except Exception as exc:
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
        return f"Failed to edit file: {exc}"


@tool(name="libreoffice_edit", tier=2, domain="files_office")
def libreoffice_edit(
    path: str,
    action: str,
    params: Optional[dict[str, Any]] = None,
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    """
    Structured LibreOffice document editing via headless UNO bridge (localhost:2002),
    with automatic fallback to `wayland_gui` GUI automation when UNO is unavailable.
    Requires Tier-2 argument-bound approval and captures a SHA-256 UndoJournal snapshot.
    """
    target = Path(path).expanduser().resolve()
    if not _is_within_home(target) or not target.is_file():
        return f"Error: Document not found inside home directory: {target}"
    if _is_sensitive_path(target):
        return "Error: Editing sensitive credential or protected files is blocked."

    pdict = dict(params or {})
    args = {"path": str(target), "action": str(action), "params": pdict}
    token, deny_err = approval.request_bound_approval(
        "libreoffice_edit",
        args,
        approval_cb,
        title=f"Approve LibreOffice Edit ({action})?",
        target=str(target),
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "libreoffice_edit", args)
    if not ok:
        return err

    aid = state_ops.get_undo_journal().record_before_mutation("libreoffice_edit", target)
    try:
        out = libreoffice_uno.edit_document_via_uno(target, action, pdict)
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=True, final_path=target)
        return f"{out} [undo: {aid}]"
    except libreoffice_uno.UnoUnavailableError as exc:
        fb = libreoffice_uno.fallback_wayland_gui_edit(
            target,
            action,
            pdict,
            open_doc_fn=documents.open_document,
            reason=str(exc),
        )
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=True, final_path=target)
        return f"{fb} [undo: {aid}]"
    except Exception as exc:
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
        return f"Failed LibreOffice edit: {exc}"


def move_file(src: str, dst: str, approval_cb: Optional[ApprovalCallback] = None) -> str:
    """Moves or renames a file/folder inside $HOME after argument-bound approval."""
    src_p = Path(src).expanduser().resolve()
    dst_p = Path(dst).expanduser().resolve()
    if not (_is_within_home(src_p) and _is_within_home(dst_p)):
        return "Error: Move/rename is restricted to paths inside your home directory."
    if not src_p.exists():
        return f"Error: Source not found: {src_p}"
    if _is_sensitive_path(src_p) or _is_sensitive_path(dst_p):
        return "Error: Sensitive paths cannot be moved."

    args = {"src": str(src_p), "dst": str(dst_p)}
    token, deny_err = approval.request_bound_approval(
        "move_file", args, approval_cb, title="Approve Move / Rename?", target=f"{src_p} -> {dst_p}"
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "move_file", args)
    if not ok:
        return err

    aid = state_ops.get_undo_journal().record_before_mutation("move_file", src_p, dst_path=dst_p)
    try:
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_p), str(dst_p))
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=True, final_path=dst_p)
        return f"Moved '{src_p.name}' to '{dst_p}'. [undo: {aid}]"
    except Exception as exc:
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
        return f"Failed to move file: {exc}"


def trash_file(path: str, approval_cb: Optional[ApprovalCallback] = None) -> str:
    """Safely moves a file to Ubuntu Trash (recoverable) after argument-bound approval."""
    target = Path(path).expanduser().resolve()
    if not _is_within_home(target) or not target.exists():
        return f"Error: Path not found inside home directory: {target}"
    if target == config.HOME.resolve() or _is_sensitive_path(target):
        return "Error: Cannot trash home root or sensitive directories."

    args = {"path": str(target)}
    token, deny_err = approval.request_bound_approval(
        "trash_file", args, approval_cb, title="Move File to Ubuntu Trash?", target=str(target)
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "trash_file", args)
    if not ok:
        return err

    if shutil.which("gio"):
        aid = state_ops.get_undo_journal().record_before_mutation("trash_file", target)
        res = command_runner.run_command(["gio", "trash", str(target)], timeout=5.0)
        if res.ok:
            state_ops.get_undo_journal().finalize_mutation(aid, succeeded=True)
            return f"Moved '{target.name}' to Trash (recoverable). [undo: {aid}]"
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
        return f"Error: {res.error}"
    return "Error: 'gio' utility not available for safe Trash deletion."


def close_application(app_name: str, approval_cb: Optional[ApprovalCallback] = None) -> str:
    """Gracefully sends SIGTERM to a running user application after argument-bound approval."""
    proc = app_name.strip()
    if not proc or proc.lower() in _PROTECTED_PROCESSES or app_nap_controller._is_protected(proc):
        return f"Blocked: '{app_name}' is a protected system process."

    args = {"app_name": proc}
    token, deny_err = approval.request_bound_approval(
        "close_application", args, approval_cb, title="Approve Closing Application?", target=proc
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "close_application", args)
    if not ok:
        return err

    if not shutil.which("pkill"):
        return "Error: pkill not available."
    res = command_runner.run_command(["pkill", "-TERM", "-u", str(os.getuid()), "-i", "-x", proc], timeout=5.0)
    return f"Sent close signal to '{proc}'." if res.ok else f"No running user process matching exact name '{proc}'."


def compose_message(
    channel: str,
    recipient: str = "",
    text: str = "",
    subject: str = "",
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    """
    Wayland-safe messaging composer with exact argument-bound user approval.
    """
    ch = channel.lower().strip()
    args = {"channel": ch, "recipient": recipient, "subject": subject, "text": text}
    token, deny_err = approval.request_bound_approval(
        "compose_message", args, approval_cb, title="Approve Composing External Message?", target=f"{ch}:{recipient or 'clipboard'}"
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "compose_message", args)
    if not ok:
        return err

    if text and shutil.which("wl-copy"):
        set_clipboard(text)

    if ch == "whatsapp":
        digits = re.sub(r"\D", "", recipient)
        url = (
            f"https://wa.me/{digits}?text={quote(text)}"
            if digits
            else f"https://web.whatsapp.com/send?text={quote(text)}"
        )
        launch_detached(["xdg-open", url])
        return "Opened WhatsApp compose with your message ready (also copied to clipboard)."

    if ch == "email":
        url = f"mailto:{quote(recipient)}?subject={quote(subject)}&body={quote(text)}"
        launch_detached(["xdg-open", url])
        return f"Opened email composer to '{recipient}' with your draft ready."

    if ch == "telegram":
        handle = recipient.lstrip("@").strip()
        url = f"https://t.me/{quote(handle)}" if handle else "https://web.telegram.org/"
        launch_detached(["xdg-open", url])
        return "Opened Telegram and copied your message to clipboard (press Ctrl+V to paste)."

    if recipient:
        open_application(recipient)
    return "Message copied to Wayland clipboard (press Ctrl+V to paste and send)."


# ============================================================================
# OpenAI / Ollama Function-Calling Schemas & Dispatcher
# ============================================================================

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_system_stats",
            "description": "Check uptime, RAM, ZRAM compression, battery level, and top CPU processes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set audio volume percentage (0-100), adjust relative delta, or mute/unmute.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "Absolute volume percentage 0-100"},
                    "delta": {"type": "integer", "description": "Relative volume change (e.g. +10 or -10)"},
                    "mute": {"type": "boolean", "description": "True to mute audio, False to unmute"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_brightness",
            "description": "Set screen brightness percentage (5 to 100).",
            "parameters": {
                "type": "object",
                "properties": {"level": {"type": "integer", "description": "Target screen brightness percentage 5-100"}},
                "required": ["level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_media",
            "description": "Control media playback or query the active playing track.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play-pause", "next", "previous", "stop", "status"],
                        "description": "Playback command or 'status' for current track",
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_connectivity",
            "description": "Check status or turn Wi-Fi or Bluetooth on/off.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "enum": ["wifi", "bluetooth"], "description": "Radio interface"},
                    "action": {"type": "string", "enum": ["status", "on", "off"], "description": "Radio action"},
                },
                "required": ["target", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Launch ANY installed desktop app, GNOME Settings panel (e.g. 'wifi settings'), website, login page, or full URL/search URL (e.g. 'https://open.spotify.com/search/telugu%20songs', 'https://www.youtube.com/results?search_query=lofi', 'spotify web', 'youtube', 'gmail', 'firefox', 'terminal').",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string", "description": "Desktop app name, GNOME settings panel, website name, or full http(s) URL (including search/login/media URLs)"}},
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_app_nap",
            "description": "macOS-style App Nap for 8GB RAM: freeze ('nap') a background app like Firefox/Chrome/VS Code to 0% CPU via SIGSTOP so ZRAM compresses its memory, or 'wake' it instantly via SIGCONT with zero data loss.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "User application process name (e.g. 'firefox', 'chrome', 'code')."},
                    "action": {"type": "string", "enum": ["nap", "wake"], "description": "'nap' to pause/freeze or 'wake' to resume."},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_document",
            "description": "Open any document in LibreOffice Writer/Calc/Impress or the default desktop app.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_running_apps",
            "description": "List running user applications and their CPU/RAM consumption.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Fast search for files by name or extension inside the user's home folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "directory": {"type": "string"},
                    "extension": {"type": "string", "description": "Optional extension like docx, pdf, odt, xlsx."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_files",
            "description": "Fast grep search for text inside files under the user's home folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "directory": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read text/code from a file inside the user's home directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Extract text/tables/sheets/slides from .docx, .odt, .xlsx, .pptx, or .pdf documents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": "Create a formatted .docx, .odt, .xlsx, .pptx, or .md document (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_document",
            "description": "Surgically edit .docx, .odt, .xlsx, or .pptx (find/replace, append text, or set spreadsheet cell) with auto-backup and user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "find_text": {"type": "string"},
                    "replace_text": {"type": "string"},
                    "append_text": {"type": "string"},
                    "sheet": {"type": "string"},
                    "cell": {"type": "string"},
                    "cell_value": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_document",
            "description": "Convert a document via LibreOffice headless (e.g. docx -> pdf, odt -> docx) after user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "target_format": {"type": "string", "enum": ["pdf", "docx", "odt", "xlsx", "ods", "pptx", "txt"]},
                },
                "required": ["path", "target_format"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clipboard",
            "description": "Read current text stored in the Wayland clipboard (Ctrl+C).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_highlighted_text",
            "description": "Read text currently highlighted/selected with the mouse in any window (Wayland primary selection, no Ctrl+C needed). Use when the user asks to summarize, translate, explain, or debug 'this' or highlighted text.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_clipboard",
            "description": "Copy text to the Wayland clipboard.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Set a countdown timer or reminder that triggers a desktop notification.",
            "parameters": {
                "type": "object",
                "properties": {"seconds": {"type": "integer"}, "message": {"type": "string"}},
                "required": ["seconds", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live web for current news, weather, facts, or documentation.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "Read clean text content from a public webpage URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Save a persistent preference or fact about the user into long-term memory.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search saved user facts and past conversations.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text/code file inside ~ (requires user approval).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Surgically replace old_text with new_text in a file inside ~ (auto-backup + user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move or rename a file/folder inside ~ (requires user approval).",
            "parameters": {
                "type": "object",
                "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trash_file",
            "description": "Move a file to Ubuntu Trash (recoverable, requires user approval).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_application",
            "description": "Gracefully close a running user application by process name (requires user approval).",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string"}},
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compose_message",
            "description": "Draft and open a message in WhatsApp, Email, Telegram, or clipboard (asks user approval before opening).",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "enum": ["whatsapp", "email", "telegram", "clipboard"]},
                    "recipient": {"type": "string", "description": "Phone number, email, username, or app name."},
                    "text": {"type": "string"},
                    "subject": {"type": "string"},
                },
                "required": ["channel", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Run a bash command inside Ubuntu's zero-RAM Bubblewrap (bwrap) namespace sandbox by default (read-only root, hidden API/SSH secrets). Always requires user click-to-approve confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "sandbox": {"type": "boolean", "description": "Default true (runs inside bwrap sandbox). Set false only if system-wide host mutation is strictly required."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_iot_webhook",
            "description": "Trigger a Home Assistant, Node-RED, or smart-home LAN/HTTPS webhook (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "webhook_url": {"type": "string", "description": "Full http:// or https:// webhook URL."},
                    "payload_json": {"type": "string", "description": "Optional JSON object string to send."},
                    "method": {"type": "string", "enum": ["POST", "PUT", "GET"]},
                },
                "required": ["webhook_url"],
            },
        },
    },
    # --- Wayland Offline OCR & GUI Automation ---
    {
        "type": "function",
        "function": {
            "name": "read_screen",
            "description": "Capture screen (or just active window for faster OCR) and read visible text + coordinates using offline Tesseract OCR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_chars": {"type": "integer"},
                    "active_window_only": {"type": "boolean", "description": "Crop OCR to only the active focused window for faster CPU execution."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_text_on_screen",
            "description": "Locate exact screen (x, y) coordinates of a visible button, label, or phrase via offline OCR.",
            "parameters": {
                "type": "object",
                "properties": {"target_text": {"type": "string"}},
                "required": ["target_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_on_text",
            "description": "Find visible text/button on screen via offline OCR and click it natively on Wayland (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_text": {"type": "string"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "double_click": {"type": "boolean"},
                    "occurrence": {"type": "integer", "description": "1-based match index if text appears multiple times."},
                },
                "required": ["target_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_position",
            "description": "Move pointer and click exact screen (x, y) coordinates on Wayland (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "double_click": {"type": "boolean"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text (English, Telugu, Hindi, or code) into the active GUI window (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "press_enter": {"type": "boolean"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_hotkey",
            "description": "Send a keyboard shortcut like 'ctrl+s', 'ctrl+c', 'alt+tab', 'enter', or 'esc' (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "string"}},
                "required": ["keys"],
            },
        },
    },
    # --- Autonomous Skill Learning, Recording & Replay ---
    {
        "type": "function",
        "function": {
            "name": "learn_skill",
            "description": "Save a permanent named multi-step automation skill/routine to SQLite. Each step is {'tool': '<name or wait>', 'args': {...}} and may contain {{variable}} placeholders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool": {"type": "string"},
                                "args": {"type": "object"},
                            },
                            "required": ["tool"],
                        },
                    },
                    "triggers": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                },
                "required": ["name", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_last_actions_as_skill",
            "description": "Save the exact sequence of actions Celina just performed as a reusable learned skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "triggers": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_recording",
            "description": "Begin interactive Learning/Recording mode so subsequent actions are recorded into a named skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "triggers": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_recording",
            "description": "Stop active Learning/Recording mode and permanently save the recorded skill to SQLite.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List all learned automation skills, step previews, and run counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_skill",
            "description": "Modify or refine a learned skill (replace step at 1-based replace_index, append_step, or replace full steps).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "object"}},
                    "append_step": {"type": "object"},
                    "replace_index": {"type": "integer"},
                    "new_step": {"type": "object"},
                    "triggers": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_skill",
            "description": "Delete a saved skill from Celina's memory.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill",
            "description": "Replay a learned multi-step skill by name with optional {{variable}} params (shows full plan for 1-click approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "params": {"type": "object", "description": "Optional key/value replacements for {{placeholders}}."},
                },
                "required": ["name"],
            },
        },
    },
    # --- Open-Source MCP Connectors, Community SKILL.md & Smart Cache ---
    {
        "type": "function",
        "function": {
            "name": "mcp_list_servers",
            "description": "List configured open-source MCP connectors (git, sqlite, playwright, context7, github, linear, notion) and their status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_list_tools",
            "description": "Inspect available tools exposed by an enabled MCP server.",
            "parameters": {
                "type": "object",
                "properties": {"server_name": {"type": "string"}},
                "required": ["server_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_call_tool",
            "description": "Invoke a tool on an enabled MCP server (lazy-started, auto-offloaded when idle, approval-gated for writes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["server_name", "tool_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_community_skill",
            "description": "Download & install an open-source SKILL.md file (agentskills.io / GitHub) into ~/.local/share/celina/skills/ after user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cache_status",
            "description": "Show Smart Cache size, hit rate, RAM hot items, and offload status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_cache",
            "description": "Immediately wipe and compact the Smart Cache.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # --- Project Astra Live UI Tree, Human Scroll/Drag & Autonomous Computer-Use ---
    {
        "type": "function",
        "function": {
            "name": "inspect_ui_elements",
            "description": "Inspect live interactive buttons, text boxes, tabs, and visible text via GNOME AT-SPI2 + offline OCR.",
            "parameters": {
                "type": "object",
                "properties": {"max_chars": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_screen",
            "description": "Scroll the mouse wheel up, down, left, or right on Wayland (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "amount": {"type": "integer"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drag_and_drop",
            "description": "Click-drag smoothly from (x1, y1) to (x2, y2) on Wayland (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer"},
                    "y1": {"type": "integer"},
                    "x2": {"type": "integer"},
                    "y2": {"type": "integer"},
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "autonomous_gui_task",
            "description": "Project Astra autonomous computer-use loop: observe screen via AT-SPI2 + offline OCR (optional vision), decide next click/type/scroll/drag/hotkey step, verify screen change, and loop until any human computer goal is completed (1-click mission approval + Esc abort).",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "High-level human computer task to complete."},
                    "max_steps": {"type": "integer"},
                    "use_vision": {"type": "boolean", "description": "Set true only if visual non-text icons/images require cloud vision (privacy-gated)."},
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link_knowledge",
            "description": "Add a Semantic Knowledge Graph link (Subject -> Predicate -> Object) or traverse 2-hop connections for query_entity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "obj": {"type": "string"},
                    "query_entity": {"type": "string", "description": "Optional entity name to query 1-hop & 2-hop links."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_goal",
            "description": "Create, update, list, or complete persistent multi-step goals with sub-tasks and deadlines across sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["upsert", "list", "complete"]},
                    "goal_id": {"type": "string"},
                    "title": {"type": "string"},
                    "status": {"type": "string", "enum": ["active", "completed", "paused", "cancelled", "all"]},
                    "sub_tasks": {"type": "array", "items": {"type": "string"}},
                    "deadline": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_workspace_snapshot",
            "description": "Capture currently open visible desktop apps and documents as a named reusable workspace skill (e.g. 'Exam Prep', 'Coding'). Saying 'Launch my <name> workspace' later restores them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Workspace name (e.g. 'Exam Prep', 'Coding')"},
                    "triggers": {"type": "string", "description": "Optional extra comma-separated trigger phrases"},
                    "apps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional explicit list of app names to save instead of live AT-SPI2 capture",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_assistant_identity",
            "description": "Update the assistant's primary name and optional nickname (defaults: Celina / Celly) and save persistently across reboots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "New primary assistant name (e.g. 'Celina', 'Aria', 'Nova')"},
                    "nickname": {"type": "string", "description": "Optional nickname (e.g. 'Celly', 'Ari'). Pass empty string to clear."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hardware_capabilities",
            "description": "Inspect live UPower battery, PipeWire audio, NetworkManager Wi-Fi, BlueZ Bluetooth, GNOME portal, and capability-gated biometric hardware status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_quarantined_skills",
            "description": "List downloaded community SKILL.md files currently held in quarantine/ awaiting user review.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_quarantined_skill",
            "description": "Verify SHA-256 checksum and promote a reviewed community skill from quarantine/ to trusted/ after explicit user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Quarantined skill slug"},
                    "expected_sha256": {"type": "string", "description": "Optional expected SHA-256 hex digest to verify"},
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_quarantined_skill",
            "description": "Delete an untrusted skill from quarantine/ without activating it.",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_goal",
            "description": "Hand a multi-step long-horizon goal to the Celina v5 Autonomy Engine (persisted in SQLite TaskGraph). Returns the root task ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "High-level multi-step goal to decompose and queue."},
                    "priority": {"type": "integer", "description": "Task priority (1=highest, 10=lowest, default 5)."},
                },
                "required": ["goal"],
            },
        },
    },
    # --- MCP Perception & Actuation Stack (Tier-2 by definition) ---
    {
        "type": "function",
        "function": {
            "name": "computer_click",
            "description": "Click a Wayland UI element semantically or by coordinates via @agent-sh/computer-use-linux MCP server (Tier-2 approval required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Semantic UI selector or accessibility label to click."},
                    "x": {"type": "integer", "description": "Optional screen X coordinate."},
                    "y": {"type": "integer", "description": "Optional screen Y coordinate."},
                    "button": {"type": "string", "description": "Mouse button (left, right, middle; default left)."},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "computer_type",
            "description": "Type text into a Wayland UI target via @agent-sh/computer-use-linux MCP server (Tier-2 approval required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type."},
                    "selector": {"type": "string", "description": "Optional target selector to focus before typing."},
                    "submit": {"type": "boolean", "description": "Press Enter after typing (default false)."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_ocr",
            "description": "Extract structured screen text via local Shadow Vision MCP server backed by Ollama (no screenshot leaves host; Tier-2 approval required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "Optional screen region ('full', 'active_window', or 'x,y,w,h')."},
                    "language": {"type": "string", "description": "Optional language hint (e.g. 'en', 'te', 'hi')."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_analyze",
            "description": "Analyze the screen with a local Ollama VLM via Shadow Vision MCP server and return a structured UI map (Tier-2 approval required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Visual perception question or UI elements to locate."},
                    "region": {"type": "string", "description": "Optional screen region ('full' or 'active_window')."},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ghostdesk_click",
            "description": "Click an element inside the isolated GhostDesk Docker virtual desktop via Streamable HTTP MCP on localhost:3000 (Tier-2 approval required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Semantic selector or element description inside GhostDesk."},
                    "x": {"type": "integer", "description": "Optional X coordinate in the GhostDesk virtual display."},
                    "y": {"type": "integer", "description": "Optional Y coordinate in the GhostDesk virtual display."},
                },
                "required": ["selector"],
            },
        },
    },
    # --- macOS-Level OS Integration, Continuity Hub, Focus Modes & CoreSpotlight ---
    {
        "type": "function",
        "function": {
            "name": "set_focus_mode",
            "description": "Switch or inspect macOS-style Focus Modes (default, work, coding, presentation, sleep, status) coordinating Do-Not-Disturb, Night Light, and power profile.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["status", "default", "work", "coding", "presentation", "sleep"],
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spotlight_query",
            "description": "Instant CoreSpotlight resolver: safe AST math calculations, unit conversions, GNOME settings deep-links, and private FTS5 file search.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_app_audio",
            "description": "Route a running application's PipeWire audio output to a specific device/sink (e.g. Bluetooth headphones or HDMI).",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Application or stream name (e.g. 'firefox', 'spotify')"},
                    "output_device": {"type": "string", "description": "Target sink name substring or ID (e.g. 'bluez', 'hdmi', 'analog')"},
                },
                "required": ["app_name", "output_device"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_continuity_status",
            "description": "Check live cross-platform continuity status across Android (scrcpy/GSConnect), iPhone/Mac (LocalSend/AirPlay Audio+Video), Windows/Linux (Deskflow/Syncthing), Sidecar RDP, and Mesh VPN.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "audit_app_permissions",
            "description": "macOS TCC-style privacy auditor showing live active mic/camera/screen-share apps, broad Flatpak sandbox overrides, and login autostart entries.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_active_app_menus",
            "description": "macOS Help-Menu-Search equivalent: searches live AT-SPI2 menus and actions inside the currently active window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_android_mirror",
            "description": "Launch 60fps USB/Wireless Android screen mirroring via Genymobile scrcpy (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "Optional ADB device serial or IP:port"},
                    "turn_screen_off": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_airplay_mirror",
            "description": "Start, stop, or inspect AirPlay 2 Video+Audio screen mirroring receiver (UxPlay) for iPhone/iPad/Mac (asks user approval to start/stop).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "start", "stop"]},
                },
            },
        },
    },
    # --- Apple Intelligence Writing Tools, Live Text OCR & Native Calendar App Intents ---
    {
        "type": "function",
        "function": {
            "name": "writing_tool_transform",
            "description": "Apple Intelligence Writing Tools (100% local Ollama): proofread, rewrite, condense, adjust tone, summarize, extract key points, or tabulate provided text or the current Wayland selection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["proofread", "rewrite", "concise", "friendly", "professional", "summarize", "key_points", "table"],
                    },
                    "text": {"type": "string", "description": "Optional text; if omitted, reads current Wayland selection/clipboard."},
                    "copy_result": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "live_text_ocr",
            "description": "macOS Live Text equivalent: interactively snip a screen region, extract text via local Tesseract OCR, copy to Wayland clipboard, and delete the temporary screenshot immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "copy_to_clipboard": {"type": "boolean"},
                    "lang": {"type": "string", "description": "Tesseract language code (e.g. 'eng', 'eng+tel', 'eng+hin')"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_upcoming_events",
            "description": "Read upcoming events natively from GNOME Calendar / local RFC 5545 (.ics) calendar.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "Number of days ahead to inspect (default 7)"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a native calendar event in GNOME Calendar / local .ics (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start_time": {"type": "string", "description": "Start time in 'YYYY-MM-DD HH:MM' format"},
                    "duration_minutes": {"type": "integer"},
                    "location": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title", "start_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_contacts",
            "description": "Search GNOME Contacts (Evolution addressbook) and local vCard (.vcf) files by name, email, phone, or organization (read-only).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_browser_bookmarks",
            "description": "Search local Firefox, Chrome, Chromium, and Brave bookmarks read-only (zero access to passwords, cookies, or browsing history).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    # --- v4.4 Cognitive Intelligence, Deeper Ubuntu 26.04 OS Integration & Complete iOS Continuity ---
    {
        "type": "function",
        "function": {
            "name": "prepare_meeting_context",
            "description": "Zero-LLM-token compound App Intent brief (<35ms): reads next upcoming Calendar event, matches attendee Contacts, recalls relevant Ebbinghaus Memory/KG facts, and finds related local docs in one call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional meeting title, person name, or topic filter"},
                    "days": {"type": "integer", "description": "Days ahead to search calendar (default 7)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transcribe_system_audio",
            "description": "100% offline PipeWire loopback meeting/video transcriber: records @DEFAULT_AUDIO_SINK@.monitor for N seconds and transcribes via local faster-whisper (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "integer", "description": "Capture duration in seconds (3 to 120, default 30)"},
                    "lang_hint": {"type": "string", "description": "Optional language hint: 'auto', 'en', 'te', or 'hi'"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python_sandbox",
            "description": "Execute a Python snippet inside an offline Bubblewrap sandbox (no network, no $HOME secrets, 1-15s timeout) to verify math, data processing, or generated code before answering (asks user approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python 3 source code to run and verify"},
                    "timeout": {"type": "integer", "description": "Execution timeout in seconds (1 to 15, default 5)"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_systemd_service",
            "description": "Systemd Crash & Journal Doctor: list failed user services or inspect systemctl status & secret-redacted journalctl logs for a specific systemd --user unit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_name": {"type": "string", "description": "User service name (e.g. 'uxplay-user.service', 'syncthing.service'); leave empty to list failed units"},
                    "lines": {"type": "integer", "description": "Number of recent journal lines to inspect (5-60, default 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_secret_vault_status",
            "description": "Check GNOME Keyring / KeePassXC Freedesktop Secret Service lock state without ever reading any passwords. Optionally trigger the native OS unlock dialog.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unlock": {"type": "boolean", "description": "Set true to trigger the native OS vault unlock prompt if locked"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_default_apps",
            "description": "Inspect ('get') or switch ('set', asks approval) Ubuntu GNOME default MIME handlers via gio/xdg-mime (e.g. web, pdf, mail, files).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get", "set"], "description": "'get' to inspect current default, 'set' to change it"},
                    "mime_type": {"type": "string", "description": "MIME type or alias: 'web', 'pdf', 'mail', 'files', 'image', 'video', or 'application/pdf'"},
                    "desktop_id": {"type": "string", "description": "Target .desktop ID when action='set' (e.g. 'firefox.desktop', 'org.gnome.Evince.desktop')"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mount_ios_device",
            "description": "Check USB iPhone/iPad status or mount/unmount it into ~/iPhone via libimobiledevice + ifuse so DCIM photos and files appear natively in Nautilus Finder (asks approval to mount/unmount).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "mount", "unmount"], "description": "Action to perform"},
                    "mount_dir": {"type": "string", "description": "Target mount folder inside $HOME (default '~/iPhone')"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_ios_continuity_camera",
            "description": "macOS Continuity Camera equivalent: launch or stop UxPlay piping iPhone AirPlay video into a v4l2loopback virtual webcam (/dev/video10) for Zoom, Meet, OBS, and Firefox (asks approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "video_device": {"type": "string", "description": "V4L2 loopback webcam node (default '/dev/video10')"},
                    "action": {"type": "string", "enum": ["start", "stop"], "description": "'start' (default) or 'stop' Continuity Camera"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_ipad_sidecar_weylus",
            "description": "Inspect, start, or stop Weylus iPad Sidecar (60 FPS PipeWire second display + Apple Pencil pressure/multi-touch right in iPad Safari; asks approval to start/stop).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "start", "stop"], "description": "Control action"},
                    "web_port": {"type": "integer", "description": "LAN HTTP port for iPad Safari (default 1701)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "respond_ios_notification",
            "description": "Trigger Apple BLE ANCS action on the latest iPhone notification or incoming call: accept/answer or dismiss/decline (asks approval).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["accept", "dismiss"], "description": "'accept' to answer/accept or 'dismiss' to decline/dismiss"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Opt-in live web_comms tool: fetch current weather conditions for a city (requires web_comms.enabled=true and internet).",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name (e.g. 'Hyderabad', 'London')"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news_headlines",
            "description": "Opt-in live web_comms tool: fetch top live news headlines for a topic (requires web_comms.enabled=true and internet).",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic or query (default 'world')"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time_zone",
            "description": "Opt-in live web_comms tool: fetch live current time and timezone info for a city or IANA timezone (requires web_comms.enabled=true and internet).",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City or IANA timezone (e.g. 'Tokyo', 'Asia/Kolkata')"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url_text",
            "description": "Opt-in live web_comms tool: fetch cleaned plain text from a public HTTPS URL (requires web_comms.enabled=true and internet).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Public https:// URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "libreoffice_edit",
            "description": "Edit a LibreOffice document (.odt/.docx/.ods/.xlsx) structurally via the headless UNO bridge (localhost:2002) with automatic fallback to wayland_gui automation (Tier-2 approval + SHA-256 undo).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Document path inside $HOME"},
                    "action": {
                        "type": "string",
                        "enum": ["replace_text", "append_text", "set_cell", "export_pdf"],
                        "description": "Structured LibreOffice edit action",
                    },
                    "params": {"type": "object", "description": "Action parameters (e.g. find/replace, text, sheet/cell/value, output_path)"},
                },
                "required": ["path", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_sandbox",
            "description": "Run Python or Bash code snippets in an isolated sandbox (Bubblewrap when available). Supports data analysis, calculations, file format conversion, and automation scripts. All execution is local with no network access.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python or Bash source code to execute.",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["python", "bash"],
                        "description": "Language of the code snippet. Default 'python'.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds before hard kill (1-15 for Python, 1-20 for Bash). Default 5.",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ponytail_evaluate",
            "description": "Apply Ponytail decision ladder to evaluate whether code should be written. Asks six questions to minimize unnecessary code generation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "Description of the coding task to evaluate.",
                    },
                },
                "required": ["task_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skilspector_scan",
            "description": "Scan an AI agent skill or MCP server for security vulnerabilities using SkillSpector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_path": {
                        "type": "string",
                        "description": "Path to skill directory, SKILL.md file, or git repo URL.",
                    },
                    "llm_enabled": {
                        "type": "boolean",
                        "description": "Enable LLM-based semantic analysis.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Scan subdirectories recursively.",
                    },
                },
                "required": ["skill_path"],
            },
        },
    },
]

_AUTO_TOOL_MAP: dict[str, Callable[..., str]] = {
    "get_system_stats": get_system_stats,
    "get_hardware_capabilities": get_hardware_capabilities,
    "set_focus_mode": os_events.set_focus_mode,
    "diagnose_systemd_service": os_events.diagnose_systemd_service,
    "get_secret_vault_status": os_events.get_secret_vault_status,
    "spotlight_query": spotlight_index.spotlight_query,
    "route_app_audio": route_app_audio,
    "get_continuity_status": continuity.get_continuity_status,
    "audit_app_permissions": continuity.audit_app_permissions,
    "search_active_app_menus": continuity.search_active_app_menus,
    "writing_tool_transform": writing_tools.writing_tool_transform,
    "live_text_ocr": writing_tools.live_text_ocr_region,
    "get_upcoming_events": calendar_bridge.get_upcoming_events,
    "search_contacts": app_intents.search_contacts,
    "search_browser_bookmarks": app_intents.search_browser_bookmarks,
    "prepare_meeting_context": app_intents.prepare_meeting_context,
    "set_volume": set_volume,
    "set_brightness": set_brightness,
    "control_media": control_media,
    "control_connectivity": control_connectivity,
    "open_application": open_application,
    "toggle_app_nap": toggle_app_nap,
    "open_document": documents.open_document,
    "list_running_apps": list_running_apps,
    "search_files": search_files,
    "search_in_files": search_in_files,
    "read_file": read_file,
    "read_document": _cached_read_document,
    "get_clipboard": get_clipboard,
    "get_highlighted_text": get_highlighted_text,
    "set_clipboard": set_clipboard,
    "set_timer": set_timer,
    "web_search": web_search,
    "fetch_webpage": fetch_webpage,
    "fetch_url_text": fetch_url_text,
    "get_weather": get_weather,
    "get_news_headlines": get_news_headlines,
    "get_current_time_zone": get_current_time_zone,
    "remember_fact": remember_fact,
    "search_memory": search_memory,
    "link_knowledge": link_knowledge,
    "manage_goal": manage_goal,
    "save_workspace_snapshot": save_workspace_snapshot,
    "set_assistant_identity": set_assistant_identity,
    "read_screen": automation.read_screen,
    "find_text_on_screen": automation.find_text_on_screen,
    "inspect_ui_elements": automation.inspect_ui_elements,
    "learn_skill": skills.learn_skill,
    "save_last_actions_as_skill": skills.save_last_actions_as_skill,
    "start_recording": skills.start_recording,
    "stop_recording": skills.stop_recording,
    "list_skills": skills.list_skills,
    "list_quarantined_skills": skills.list_quarantined_skills,
    "reject_quarantined_skill": skills.reject_quarantined_skill,
    "update_skill": skills.update_skill,
    "forget_skill": skills.forget_skill,
    "mcp_list_servers": mcp_list_servers,
    "mcp_list_tools": mcp_list_tools,
    "cache_status": cache_status,
    "clear_cache": clear_cache,
}


def submit_goal(goal: str, priority: int = 5) -> str:
    """Hand a multi-step goal to the Celina v5 Autonomy Engine (`tasks.db`). Returns root task ID."""
    clean = (goal or "").strip()
    if not clean:
        return "Error: goal must not be empty."
    from .planner.task_graph import TaskGraph
    from .planner.tree_planner import TreePlanner

    graph = TaskGraph(config.BASE_DIR / "tasks.db")
    try:
        planner = TreePlanner(llm_client=None, graph=graph)
        available = sorted(config.TIER1_AUTO_TOOLS | config.TIER2_APPROVAL_TOOLS)
        root_id = planner.plan(clean, available_tools=available, priority=int(priority))
        return f"Submitted autonomous goal (root_task_id={root_id}): {clean}"
    finally:
        graph.close()


def _execute_mcp_perception_actuation(
    celina_tool_name: str,
    arguments: dict[str, Any],
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    """Routes the 5 Tier-2 `mcp_perception_actuation` tools through approval + MCPLoader."""
    from .autonomy import mcp_loader

    route = mcp_loader.MCP_TOOL_ROUTING[celina_tool_name]
    server = route["server"]
    transport = route["transport"]
    token, deny_err = approval.request_bound_approval(
        celina_tool_name,
        arguments,
        approval_cb,
        title=f"Approve MCP {celina_tool_name} ({server})?",
        target=f"mcp://{server}/{route['remote_tool']} ({transport})",
        extra_warning="External MCP server call — Tier-2 approval required by policy.",
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, celina_tool_name, arguments)
    if not ok:
        return err

    receipt = mcp_loader.get_mcp_loader().execute_domain_tool(celina_tool_name, arguments)
    if not receipt.ok:
        return f"Error: MCP {server}.{route['remote_tool']} failed ({receipt.mcp_transport}): {receipt.error}"
    return receipt.output


def computer_click(
    selector: str,
    x: Optional[int] = None,
    y: Optional[int] = None,
    button: str = "left",
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    args: dict[str, Any] = {"selector": str(selector), "button": str(button or "left")}
    if x is not None:
        args["x"] = int(x)
    if y is not None:
        args["y"] = int(y)
    return _execute_mcp_perception_actuation("computer_click", args, approval_cb=approval_cb)


def computer_type(
    text: str,
    selector: str = "",
    submit: bool = False,
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    args: dict[str, Any] = {"text": str(text), "submit": bool(submit)}
    if selector:
        args["selector"] = str(selector)
    return _execute_mcp_perception_actuation("computer_type", args, approval_cb=approval_cb)


def vision_ocr(
    region: str = "full",
    language: str = "en",
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    args = {"region": str(region or "full"), "language": str(language or "en")}
    return _execute_mcp_perception_actuation("vision_ocr", args, approval_cb=approval_cb)


def vision_analyze(
    prompt: str,
    region: str = "full",
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    args = {"prompt": str(prompt), "region": str(region or "full")}
    return _execute_mcp_perception_actuation("vision_analyze", args, approval_cb=approval_cb)


def ghostdesk_click(
    selector: str,
    x: Optional[int] = None,
    y: Optional[int] = None,
    approval_cb: Optional[ApprovalCallback] = None,
) -> str:
    args: dict[str, Any] = {"selector": str(selector)}
    if x is not None:
        args["x"] = int(x)
    if y is not None:
        args["y"] = int(y)
    return _execute_mcp_perception_actuation("ghostdesk_click", args, approval_cb=approval_cb)


_APPROVAL_TOOL_MAP: dict[str, Callable[..., str]] = {
    "submit_goal": submit_goal,
    "computer_click": computer_click,
    "computer_type": computer_type,
    "vision_ocr": vision_ocr,
    "vision_analyze": vision_analyze,
    "ghostdesk_click": ghostdesk_click,
    "write_file": write_file,
    "edit_file": edit_file,
    "edit_document": documents.edit_document,
    "create_document": documents.create_document,
    "convert_document": documents.convert_document,
    "libreoffice_edit": libreoffice_edit,
    "move_file": move_file,
    "trash_file": trash_file,
    "close_application": close_application,
    "compose_message": compose_message,
    "run_shell_command": run_shell_command,
    "run_python_sandbox": run_python_sandbox,
    "code_sandbox": code_sandbox.code_sandbox,
    "transcribe_system_audio": transcribe_system_audio_tool,
    "manage_default_apps": os_events.manage_default_apps,
    "click_on_text": automation.click_on_text,
    "click_position": automation.click_position,
    "scroll_screen": automation.scroll_screen,
    "drag_and_drop": automation.drag_and_drop,
    "type_text": automation.type_text,
    "press_hotkey": automation.press_hotkey,
    "mcp_call_tool": mcp_call_tool,
    "install_community_skill": skills.install_community_skill,
    "approve_quarantined_skill": skills.approve_quarantined_skill,
    "trigger_iot_webhook": trigger_iot_webhook,
    "launch_android_mirror": continuity.launch_android_mirror,
    "control_airplay_mirror": continuity.control_airplay_mirror,
    "mount_ios_device": continuity.mount_ios_device,
    "start_ios_continuity_camera": continuity.start_ios_continuity_camera,
    "start_ipad_sidecar_weylus": continuity.start_ipad_sidecar_weylus,
    "respond_ios_notification": continuity.respond_ios_notification,
    "create_calendar_event": calendar_bridge.create_calendar_event,
    "ponytail_evaluate": ponytail_skill.PonytailSkill.evaluate_code_need,
    "skilspector_scan": skilspector_tool.SkillSpectorTool.scan_skill,
}


def get_enabled_tool_schemas() -> list[dict[str, Any]]:
    allowed = config.TIER1_AUTO_TOOLS | config.TIER2_APPROVAL_TOOLS
    if not allowed:
        return TOOL_SCHEMAS
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in allowed]


_TOOL_SCHEMA_BY_NAME: dict[str, dict[str, Any]] = {
    t["function"]["name"]: t["function"] for t in TOOL_SCHEMAS
}
_LEX_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "your", "have", "what",
    "can", "you", "please", "tell", "show", "about", "into", "using", "give",
    "want", "need", "make", "just", "all", "are", "how", "now", "one", "set",
})


def _trigger_matches(trigger: str, text_low: str) -> bool:
    """Word-boundary match for ASCII single-word triggers (prevents 'read' matching 'proofread')."""
    tr = trigger.strip().lower()
    if not tr:
        return False
    if " " not in tr and tr.isascii() and tr.isalnum():
        return bool(re.search(rf"\b{re.escape(tr)}\b", text_low))
    return tr in text_low


def _lexical_rank_tools(prompt: str, schemas: list[dict[str, Any]], top_k: int = 5) -> list[str]:
    """Zero-dependency BM25-like lexical tool retriever over tool names, descriptions & params."""
    tokens = {
        tok for tok in re.findall(r"[a-z0-9]{3,}", (prompt or "").lower())
        if tok not in _LEX_STOPWORDS
    }
    if not tokens:
        return []
    scored: list[tuple[float, str]] = []
    for item in schemas:
        fn = item["function"]
        tname = fn["name"]
        name_toks = set(re.findall(r"[a-z0-9]{2,}", tname.lower().replace("_", " ")))
        desc_toks = set(re.findall(r"[a-z0-9]{3,}", str(fn.get("description", "")).lower()))
        props = (fn.get("parameters") or {}).get("properties", {})
        param_toks: set[str] = set()
        for pk, pv in props.items():
            param_toks.update(re.findall(r"[a-z0-9]{3,}", pk.lower().replace("_", " ")))
            if isinstance(pv, dict) and pv.get("description"):
                param_toks.update(re.findall(r"[a-z0-9]{3,}", str(pv["description"]).lower()))
        score = (
            3.0 * len(tokens & name_toks)
            + 1.5 * len(tokens & desc_toks)
            + 0.75 * len(tokens & param_toks)
        )
        if score >= 3.0:
            scored.append((score, tname))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [name for _s, name in scored[:top_k]]


class _SemanticToolIndex:
    """
    Lazy, disk-cached semantic index over all 88 TOOL_SCHEMAS (~45 KB JSON/base64).
    Automatically invalidates when TOOL_SCHEMAS or embedding model fingerprint changes.
    """

    def __init__(self, cache_path: Path = config.BASE_DIR / "tool_vectors.json"):
        self.cache_path = Path(cache_path)
        self._lock = threading.Lock()
        self._fingerprint: str = ""
        self._blobs: list[tuple[str, bytes]] = []

    def invalidate(self) -> None:
        with self._lock:
            self._fingerprint = ""
            self._blobs = []

    def _compute_fingerprint(self, schemas: list[dict[str, Any]], model_fp: str) -> str:
        raw = json.dumps(
            [(t["function"]["name"], t["function"].get("description", "")) for t in schemas],
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(f"{model_fp}::{raw}".encode("utf-8")).hexdigest()[:24]

    def _ensure_loaded(self, schemas: list[dict[str, Any]]) -> list[tuple[str, bytes]]:
        from . import embeddings
        import base64

        eng = embeddings.get_engine()
        fp = self._compute_fingerprint(schemas, eng.model_fingerprint)
        with self._lock:
            if self._fingerprint == fp and self._blobs:
                return self._blobs
            # Try disk cache first (only for non-custom persistent engines)
            if not eng.model_fingerprint.startswith("custom:") and self.cache_path.exists():
                try:
                    data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                    if data.get("fp") == fp and isinstance(data.get("items"), dict):
                        self._blobs = [
                            (str(k), base64.b64decode(v.encode("ascii")))
                            for k, v in data["items"].items()
                        ]
                        self._fingerprint = fp
                        return self._blobs
                except Exception:
                    pass

        # Embed all tool summaries in one batch outside lock
        names: list[str] = []
        texts: list[str] = []
        for item in schemas:
            fn = item["function"]
            tname = str(fn["name"])
            desc = str(fn.get("description", ""))
            props = (fn.get("parameters") or {}).get("properties", {})
            p_desc = " ".join(
                f"{pk} {pv.get('description', '')}"
                for pk, pv in props.items()
                if isinstance(pv, dict)
            )
            names.append(tname)
            texts.append(f"{tname.replace('_', ' ')}: {desc} {p_desc}".strip())

        vecs = eng.embed_texts(texts, timeout=15.0)
        if not vecs or len(vecs) != len(names):
            return []
        blobs = [(names[i], embeddings.pack_vector(vecs[i], target_dim=config.EMBED_DIM)) for i in range(len(names))]
        with self._lock:
            self._fingerprint = fp
            self._blobs = blobs
            if not eng.model_fingerprint.startswith("custom:"):
                try:
                    payload = {
                        "fp": fp,
                        "items": {k: base64.b64encode(b).decode("ascii") for k, b in blobs},
                    }
                    config.atomic_write_private_text(self.cache_path, json.dumps(payload) + "\n")
                except OSError:
                    pass
            return self._blobs

    def search(
        self,
        prompt: str,
        schemas: list[dict[str, Any]],
        *,
        top_k: int = 5,
        min_sim: Optional[float] = None,
    ) -> list[str]:
        from . import embeddings

        q_vec = embeddings.get_engine().embed_one(prompt, timeout=2.0)
        if not q_vec:
            return []
        blobs = self._ensure_loaded(schemas)
        if not blobs:
            return []
        threshold = config.TOOL_ROUTER_SEMANTIC_MIN_SIM if min_sim is None else float(min_sim)
        ranked = embeddings.rank_vector_blobs(q_vec, blobs, top_k=top_k, min_sim=threshold)
        return [name for name, _sim in ranked]


_SEMANTIC_TOOL_INDEX = _SemanticToolIndex()


def _semantic_rank_tools(prompt: str, schemas: list[dict[str, Any]], top_k: int = 5) -> list[str]:
    return _SEMANTIC_TOOL_INDEX.search(prompt, schemas, top_k=top_k)


def _coerce_and_filter_tool_args(
    name: str,
    fn: Callable[..., Any],
    raw_args: dict[str, Any],
) -> dict[str, Any]:
    """
    Small-model BFCL reliability guard:
      1. Coerces stringified ints/floats/bools ("60" -> 60, "true" -> True) per TOOL_SCHEMAS types.
      2. Strips hallucinated extra kwargs when the target handler doesn't accept **kwargs.
    """
    schema_fn = _TOOL_SCHEMA_BY_NAME.get(name) or {}
    props = (schema_fn.get("parameters") or {}).get("properties", {})

    coerced: dict[str, Any] = {}
    for k, v in raw_args.items():
        spec = props.get(k) if isinstance(props, dict) else None
        ptype = spec.get("type") if isinstance(spec, dict) else None
        if v is not None and ptype == "integer" and not isinstance(v, bool):
            if isinstance(v, (int, float)):
                v = int(v)
            elif isinstance(v, str):
                s = v.strip().rstrip("%")
                try:
                    v = int(float(s))
                except ValueError:
                    pass
        elif v is not None and ptype == "number" and not isinstance(v, bool):
            if isinstance(v, str):
                try:
                    v = float(v.strip().rstrip("%"))
                except ValueError:
                    pass
        elif v is not None and ptype == "boolean":
            if isinstance(v, str):
                sl = v.strip().lower()
                if sl in {"true", "1", "yes", "on"}:
                    v = True
                elif sl in {"false", "0", "no", "off"}:
                    v = False
            elif isinstance(v, (int, float)) and v in (0, 1):
                v = bool(v)
        coerced[k] = v

    try:
        sig = inspect.signature(fn)
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if not has_varkw:
            valid_params = set(sig.parameters.keys())
            coerced = {k: v for k, v in coerced.items() if k in valid_params}
    except (TypeError, ValueError):
        pass
    return coerced


def get_contextual_tool_schemas(
    prompt: str,
    gear: str = "tactical",
    active_window: str = "",
    is_recording: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Hybrid Domain + Lexical Tool Router (v4.3):
    1. Matches word-boundary domain triggers (EN / TE / HI) & active window hints across 8 focused domains.
    2. Runs a zero-dependency lexical fallback scorer over all 78 tool names/descriptions when phrasing
       is unexpected so NO valid tool is silently pruned out, while keeping prompt tokens lean for 1.7B–4B models.
    Returns (pruned_schemas, active_domain_names).
    """
    all_enabled = get_enabled_tool_schemas()
    domains = config.TOOL_DOMAINS
    if not domains:
        return all_enabled, ["all"]

    low = (prompt or "").lower()
    win_low = (active_window or "").lower()
    selected_tools: set[str] = set()
    active_domains: list[str] = []

    matched_non_core = False
    for dname, dcfg in domains.items():
        if dcfg.get("always_active"):
            continue
        triggers = [str(t) for t in dcfg.get("triggers", [])]
        win_hints = [str(w).lower() for w in dcfg.get("window_hints", [])]
        prompt_hit = any(_trigger_matches(tr, low) for tr in triggers)
        win_hit = bool(win_low and gear != "fast" and any(wh in win_low for wh in win_hints))
        if prompt_hit or win_hit:
            matched_non_core = True
            active_domains.append(dname)
            selected_tools.update(dcfg.get("tools", []))

    if is_recording:
        for dname in ("skills_memory", "wayland_gui"):
            if dname in domains and dname not in active_domains:
                active_domains.append(dname)
                selected_tools.update(domains[dname].get("tools", []))

    # Lexical top-K rescue for unseen phrasing / synonyms not listed verbatim in triggers
    lex_hits = _lexical_rank_tools(prompt, all_enabled, top_k=5)
    if lex_hits:
        new_lex = [n for n in lex_hits if n not in selected_tools]
        if new_lex:
            selected_tools.update(new_lex)
            active_domains.append("lexical_top")

    # Semantic top-K rescue via pre-cached tool embeddings (0 ms no-op if embed engine inactive)
    sem_hits = _semantic_rank_tools(prompt, all_enabled, top_k=5)
    if sem_hits:
        new_sem = [n for n in sem_hits if n not in selected_tools]
        if new_sem:
            selected_tools.update(new_sem)
            active_domains.append("semantic_top")

    # Always include core os_hardware (lean core_fast_tools on Gear 1 fast; full domain otherwise)
    os_cfg = domains.get("os_hardware") or {}
    if gear == "fast" and not matched_non_core and os_cfg.get("core_fast_tools"):
        selected_tools.update(os_cfg["core_fast_tools"])
        active_domains.insert(0, "os_hardware(fast)")
    else:
        selected_tools.update(os_cfg.get("tools", []))
        active_domains.insert(0, "os_hardware")

    # On deep queries with no specific domain keyword, include web_comms + files_office + skills_memory
    if gear == "deep" and not matched_non_core:
        for fallback_d in ("files_office", "web_comms", "skills_memory"):
            if fallback_d in domains and fallback_d not in active_domains:
                active_domains.append(fallback_d)
                selected_tools.update(domains[fallback_d].get("tools", []))

    pruned = [t for t in all_enabled if t["function"]["name"] in selected_tools]
    return (pruned if pruned else all_enabled), active_domains


def build_tiered_tool_presentation(
    prompt: str,
    *,
    gear: str = "tactical",
    active_window: str = "",
    is_recording: bool = False,
    detailed_k: int = 8,
) -> dict[str, Any]:
    """
    Tier-Based Hybrid Tool Presentation (+10pp accuracy on 1.5B-3B models,
    83-92% fewer prompt tokens vs sending all full JSON schemas):
      - `detailed_schemas`: Top `detailed_k` (default 8) most relevant tool schemas
        in full OpenAI/Ollama JSON schema detail.
      - `name_only_tools`: All remaining enabled tool names as a compact catalog
        (e.g., `read_calendar, set_brightness, ...`) so small models keep full OS
        awareness without drowning their context window.
    """
    all_enabled = get_enabled_tool_schemas()
    contextual, active_domains = get_contextual_tool_schemas(
        prompt,
        gear=gear,
        active_window=active_window,
        is_recording=is_recording,
    )
    k = max(1, int(detailed_k))

    lex_order = _lexical_rank_tools(prompt, all_enabled, top_k=k * 2)
    contextual_names = [t.get("function", {}).get("name", "") for t in contextual]
    ordered_names: list[str] = []
    for n in lex_order + contextual_names:
        if n and n in _TOOL_SCHEMA_BY_NAME and n not in ordered_names:
            ordered_names.append(n)
    for t in all_enabled:
        if len(ordered_names) >= k:
            break
        n = t.get("function", {}).get("name", "")
        if n and n not in ordered_names:
            ordered_names.append(n)

    detailed_names = ordered_names[:k]
    detailed_set = set(detailed_names)
    detailed_schemas = [_TOOL_SCHEMA_BY_NAME[n] for n in detailed_names if n in _TOOL_SCHEMA_BY_NAME]
    name_only_tools = [
        t.get("function", {}).get("name", "")
        for t in all_enabled
        if t.get("function", {}).get("name", "") not in detailed_set
    ]

    full_chars = len(json.dumps(all_enabled))
    compact_catalog = "Other available tools (name-only): " + ", ".join(name_only_tools)
    tiered_chars = len(json.dumps(detailed_schemas)) + len(compact_catalog)
    savings_pct = round(max(0.0, (1.0 - (tiered_chars / max(1, full_chars))) * 100.0), 1)

    return {
        "detailed_schemas": detailed_schemas,
        "detailed_names": detailed_names,
        "name_only_tools": name_only_tools,
        "name_only_prompt_block": compact_catalog,
        "active_domains": active_domains,
        "total_enabled_tools": len(all_enabled),
        "estimated_token_savings_pct": savings_pct,
    }


class ToolCircuitBreaker:
    """
    Thread-safe per-tool circuit breaker (Capability #4):
      - Prevents runaway retry loops when a subsystem is down.
      - Tracks consecutive SAME-ERROR-CODE failures: after `threshold` consecutive
        failures with the same error code, disables that tool for the session
        (`session_disabled`).
      - When called without an explicit error_code/error_text, opens for `cooldown_sec`.
      - User approval denials (`Denied:` / `Cancelled`) do NOT trip the breaker.
    """

    def __init__(self, threshold: int = 3, cooldown_sec: float = 60.0):
        self.threshold = max(1, int(threshold))
        self.cooldown_sec = max(1.0, float(cooldown_sec))
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._last_error_code: dict[str, str] = {}
        self._same_code_streak: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._session_disabled: dict[str, str] = {}

    def is_session_disabled(self, tool_name: str) -> Optional[str]:
        with self._lock:
            return self._session_disabled.get(tool_name)

    def check_open(self, tool_name: str, now: Optional[float] = None) -> Optional[float]:
        ts = time.monotonic() if now is None else now
        with self._lock:
            if tool_name in self._session_disabled:
                return float("inf")
            until = self._open_until.get(tool_name, 0.0)
            if until > ts:
                return until - ts
            if until > 0.0:
                self._open_until.pop(tool_name, None)
                self._failures.pop(tool_name, None)
            return None

    def record_success(self, tool_name: str) -> None:
        with self._lock:
            self._failures.pop(tool_name, None)
            self._last_error_code.pop(tool_name, None)
            self._same_code_streak.pop(tool_name, None)
            self._open_until.pop(tool_name, None)

    def record_failure(
        self,
        tool_name: str,
        now: Optional[float] = None,
        *,
        error_code: Optional[str] = None,
        error_text: str = "",
    ) -> bool:
        ts = time.monotonic() if now is None else now
        with self._lock:
            cnt = self._failures.get(tool_name, 0) + 1
            self._failures[tool_name] = cnt

            if error_code is not None or error_text:
                code = (error_code or guardrails.extract_error_code(error_text)).strip().lower()
                if self._last_error_code.get(tool_name) == code:
                    streak = self._same_code_streak.get(tool_name, 0) + 1
                else:
                    self._last_error_code[tool_name] = code
                    streak = 1
                self._same_code_streak[tool_name] = streak
                if streak >= self.threshold:
                    self._session_disabled[tool_name] = (
                        f"session-disabled after {streak} consecutive '{code}' failures"
                    )
                    return True

            if cnt >= self.threshold:
                self._open_until[tool_name] = ts + self.cooldown_sec
                return True
            return False

    def snapshot(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            keys = sorted(set(self._failures) | set(self._open_until) | set(self._session_disabled))
            return {
                k: {
                    "consecutive_failures": self._failures.get(k, 0),
                    "same_error_code": self._last_error_code.get(k, ""),
                    "same_error_streak": self._same_code_streak.get(k, 0),
                    "session_disabled": k in self._session_disabled,
                    "session_disabled_reason": self._session_disabled.get(k, ""),
                    "open_remaining_sec": (
                        None if k in self._session_disabled else round(max(0.0, self._open_until.get(k, 0.0) - now), 1)
                    ),
                }
                for k in keys
            }

    def reset(self, tool_name: Optional[str] = None) -> None:
        with self._lock:
            if tool_name is None:
                self._failures.clear()
                self._last_error_code.clear()
                self._same_code_streak.clear()
                self._open_until.clear()
                self._session_disabled.clear()
            else:
                self._failures.pop(tool_name, None)
                self._last_error_code.pop(tool_name, None)
                self._same_code_streak.pop(tool_name, None)
                self._open_until.pop(tool_name, None)
                self._session_disabled.pop(tool_name, None)


TOOL_CIRCUIT_BREAKER = ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
TOOL_BULKHEAD = guardrails.ToolBulkhead()


def _domain_for_tool(tool_name: str) -> str:
    for dname, dcfg in (config.TOOL_DOMAINS or {}).items():
        if tool_name in (dcfg.get("tools") or ()):
            return str(dname)
    return "os_hardware"


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    approval_cb: Optional[ApprovalCallback] = None,
    status_cb: Optional[StatusCallback] = None,
    consent_cb: Optional[PrivacyConsentCallback] = None,
    task_glow_cb: Optional[TaskGlowCallback] = None,
) -> str:
    allowed = config.TIER1_AUTO_TOOLS | config.TIER2_APPROVAL_TOOLS
    if allowed and name not in allowed:
        return f"Blocked by neural-schema.json policy: tool '{name}' is disabled."

    is_t2 = name in config.TIER2_APPROVAL_TOOLS
    cap_ok, cap_err = capabilities.check_tool_execution_boundary(name, is_tier2=is_t2)
    if not cap_ok:
        if is_t2:
            try:
                evidence.get_evidence_ledger().record_execution(
                    tool_name=name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                    result_text=cap_err,
                    why_reasoning=evidence.get_current_why_context(),
                )
            except OSError:
                pass
        return cap_err

    rem = TOOL_CIRCUIT_BREAKER.check_open(name)
    if rem is not None:
        disabled_reason = TOOL_CIRCUIT_BREAKER.is_session_disabled(name)
        if disabled_reason:
            return f"Error: Circuit breaker open for '{name}' ({disabled_reason})."
        return (
            f"Error: Circuit breaker open for '{name}' after {TOOL_CIRCUIT_BREAKER.threshold} "
            f"consecutive failures (cooling down for {int(rem) + 1}s)."
        )

    args = arguments if isinstance(arguments, dict) else {}

    def _record_t2(result_str: str) -> str:
        if is_t2:
            try:
                evidence.get_evidence_ledger().record_execution(
                    tool_name=name,
                    arguments=args,
                    result_text=result_str,
                    why_reasoning=evidence.get_current_why_context(),
                )
            except OSError:
                pass
        return result_str

    try:
        with TOOL_BULKHEAD.acquire(_domain_for_tool(name), timeout=2.0):
            if name == "run_skill":
                return _record_t2(
                    str(
                        skills.run_skill(
                            name=str(args.get("name", "")),
                            executor=lambda tn, ta, acb: execute_tool(
                                tn, ta, approval_cb=acb, status_cb=status_cb, consent_cb=consent_cb, task_glow_cb=task_glow_cb
                            ),
                            params=args.get("params") if isinstance(args.get("params"), dict) else None,
                            approval_cb=approval_cb,
                            status_cb=status_cb,
                        )
                    )
                )
            if name == "autonomous_gui_task":
                return _record_t2(
                    str(
                        computer_use.autonomous_gui_task(
                            goal=str(args.get("goal", "")),
                            executor=lambda tn, ta, acb: execute_tool(
                                tn, ta, approval_cb=acb, status_cb=status_cb, consent_cb=consent_cb
                            ),
                            max_steps=int(args.get("max_steps", config.AUTOMATION_CFG.get("astra_max_steps", 10))),
                            use_vision=bool(args.get("use_vision", False)),
                            approval_cb=approval_cb,
                            consent_cb=consent_cb,
                            status_cb=status_cb,
                            task_glow_cb=task_glow_cb,
                        )
                    )
                )
            if name in _APPROVAL_TOOL_MAP:
                target_fn = _APPROVAL_TOOL_MAP[name]
                clean_args = _coerce_and_filter_tool_args(name, target_fn, args)
                clean_args.pop("approval_cb", None)
                val_ok, val_err = guardrails.validate_tool_arguments(name, clean_args, _TOOL_SCHEMA_BY_NAME)
                if not val_ok:
                    return _record_t2(f"Tool argument error ({name}): {val_err}")
                out = str(target_fn(**clean_args, approval_cb=approval_cb))
            else:
                fn = _AUTO_TOOL_MAP.get(name)
                if not fn:
                    return f"Error: Unknown tool '{name}'."
                clean_args = _coerce_and_filter_tool_args(name, fn, args)
                val_ok, val_err = guardrails.validate_tool_arguments(name, clean_args, _TOOL_SCHEMA_BY_NAME)
                if not val_ok:
                    return _record_t2(f"Tool argument error ({name}): {val_err}")
                out = str(fn(**clean_args))

        if out.startswith(("Error:", "Tool execution error")):
            TOOL_CIRCUIT_BREAKER.record_failure(name, error_text=out)
        elif not out.startswith(("Blocked:", "Denied:", "Cancelled", "Tool ", "Automation stopped")):
            TOOL_CIRCUIT_BREAKER.record_success(name)
            # Automatically capture successful actions into the learning trace & active recorder!
            skills.recorder.record_step(name, args)
        return _record_t2(out)
    except guardrails.BulkheadRejectedError as exc:
        return _record_t2(f"Error: {exc}")
    except net_policy.OfflineError as exc:
        return _record_t2(f"Offline fallback ({name}): {exc}")
    except TypeError as exc:
        return _record_t2(f"Tool argument error ({name}): {exc}")
    except Exception as exc:
        TOOL_CIRCUIT_BREAKER.record_failure(name, error_text=str(exc))
        return _record_t2(f"Tool execution error ({name}): {exc}")


# ============================================================================
# Tier 0: Zero-Latency Multilingual Fast-Path Intents (EN / TE / HI)
# ============================================================================

def handle_fast_intent(text: str, approval_cb: Optional[ApprovalCallback] = None) -> Optional[str]:
    low = text.lower().strip()

    if low in {
        "time", "what time is it", "what time is it?", "current time",
        "time entha", "samayam entha", "టైమ్ ఎంత", "సమయం ఎంత",
        "samay kya hua", "time kya hua", "समय क्या हुआ", "टाइम क्या है",
    }:
        now_str = datetime.datetime.now().strftime("%I:%M %p")
        if any("\u0c00" <= c <= "\u0c7f" for c in text):
            return f"ఇప్పుడు సమయం {now_str}."
        if any("\u0900" <= c <= "\u097f" for c in text):
            return f"अभी समय {now_str} है।"
        return f"It is currently {now_str}."

    if low in {"unmute", "unmute audio", "unmute volume", "అన్‌మ్యూట్", "అన్మ్యూట్", "अनम्यूट", "आवाज़ चालू करो"}:
        return set_volume(mute=False)
    if low in {"mute", "mute audio", "mute volume", "మ్యూట్", "आवाज़ बंद करो", "आवाज़ बंद", "म्यूट"}:
        return set_volume(mute=True)

    m_vol = re.fullmatch(r"(?:set\s+)?(?:volume|వాల్యూమ్|आवाज़|वॉल्यूम)(?:\s+to)?\s+(\d{1,3})%?", low)
    if m_vol:
        return set_volume(level=int(m_vol.group(1)))
    if low in {"volume up", "louder", "increase volume", "volume penchu", "వాల్యూమ్ పెంచు", "awaz badhao", "आवाज़ बढ़ाओ"}:
        return set_volume(delta=10)
    if low in {"volume down", "quieter", "decrease volume", "volume tagginchu", "వాల్యూమ్ తగ్గించు", "awaz kam karo", "आवाज़ कम करो"}:
        return set_volume(delta=-10)

    m_bri = re.fullmatch(r"(?:set\s+)?(?:brightness|బ్రైట్‌నెస్|ब्राइटनेस)(?:\s+to)?\s+(\d{1,3})%?", low)
    if m_bri:
        return set_brightness(int(m_bri.group(1)))

    if low in {"pause music", "play music", "toggle media", "play pause", "play/pause", "పాట ఆపు", "పాట ప్లే చేయి", "गाना रोको", "गाना चलाओ"}:
        return control_media("play-pause")
    if low in {"next track", "skip song", "next song", "తదుపరి పాట", "अगला गाना"}:
        return control_media("next")
    if low in {"previous track", "prev track", "previous song", "మునుపటి పాట", "पिछला गाना"}:
        return control_media("previous")
    if low in {"what's playing", "whats playing", "media status"}:
        return control_media("status")

    # Instant macOS Focus Modes, Continuity/AirPods, Calendar, Privacy Audit & CoreSpotlight Math/Units
    m_focus = re.fullmatch(
        r"(?:enable|set|switch to|start|turn on)?\s*(work|coding|sleep|presentation|default)\s+(?:focus\s*)?mode",
        low,
    )
    if m_focus:
        return os_events.set_focus_mode(m_focus.group(1))
    if low in {"focus status", "what focus mode", "current focus mode", "focus mode status"}:
        return os_events.set_focus_mode("status")
    if low in {
        "continuity status", "iphone status", "airpods battery",
        "bluetooth battery", "check airpods battery", "check continuity",
    }:
        return continuity.get_continuity_status()
    if low in {
        "upcoming events", "my calendar", "today's schedule", "todays schedule",
        "calendar events", "check my calendar", "నా క్యాలెండర్", "मेरा कैलेंडर",
    }:
        return calendar_bridge.get_upcoming_events(days=7)
    if low in {"privacy audit", "audit app permissions", "check app permissions", "who is using my mic", "who is using my camera"}:
        return continuity.audit_app_permissions()
    if low in {"hardware status", "biometrics status", "touch id status", "face id status"}:
        return get_hardware_capabilities()

    # Instant Dark / Light Mode, Lock Screen & Screenshot controls
    if low in {"dark mode", "enable dark mode", "switch to dark mode", "turn on dark mode"}:
        if shutil.which("gsettings"):
            command_runner.run_command(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-dark"], timeout=4.0)
            return "Switched desktop to Dark Mode."
    if low in {"light mode", "enable light mode", "switch to light mode", "turn on light mode"}:
        if shutil.which("gsettings"):
            command_runner.run_command(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "default"], timeout=4.0)
            return "Switched desktop to Light Mode."
    if low in {"lock screen", "lock my screen", "lock pc", "lock my pc", "lock laptop"}:
        if shutil.which("loginctl"):
            command_runner.run_command(["loginctl", "lock-session"], timeout=4.0)
            return "Locked screen."
    if low in {"take screenshot", "take a screenshot", "screenshot"}:
        if shutil.which("gnome-screenshot") and launch_detached(["gnome-screenshot", "-i"]):
            return "Opened Screenshot tool."

    # Instant Wi-Fi & Bluetooth toggles
    m_radio = re.fullmatch(
        r"(?:turn\s+)?(on|off|enable|disable)\s+(wifi|wi-fi|bluetooth)|(?:turn\s+)?(wifi|wi-fi|bluetooth)\s+(on|off|status)",
        low,
    )
    if m_radio:
        act_raw = (m_radio.group(1) or m_radio.group(4) or "status").lower()
        tgt_raw = (m_radio.group(2) or m_radio.group(3) or "wifi").lower().replace("-", "")
        act = "on" if act_raw in {"on", "enable"} else ("off" if act_raw in {"off", "disable"} else "status")
        return control_connectivity(tgt_raw, act)

    # Instant Universal Web / Site Search ("search google/youtube/spotify/amazon/flipkart/github/wikipedia/maps/flathub/reddit for ...")
    site_alts = "|".join(sorted(SITE_SEARCH_URLS.keys(), key=len, reverse=True))
    m_search = re.fullmatch(
        rf"(?:(?:search|look up)\s+(?:on\s+|in\s+|the\s+)?({site_alts})\s+(?:for\s+)?|(google|youtube)\s+)(.+)",
        low,
    )
    if m_search:
        site = (m_search.group(1) or m_search.group(2) or "google").strip()
        q = m_search.group(3).strip()
        tmpl = SITE_SEARCH_URLS.get(site)
        if q and tmpl:
            return open_url_safe(tmpl.format(q=quote(q)))

    # Instant Multi-Clause "open <site> and search/find/look up <query>"
    m_open_search = re.fullmatch(
        rf"(?:please\s+)?(?:open|go\s+to|launch)\s+({site_alts})(?:\s+(?:in\s+web|web|website|app))?\s+(?:and|then|,)\s*(?:search|find|look\s+up)(?:\s+for)?\s+(.+)",
        low,
    )
    if m_open_search:
        site = m_open_search.group(1).strip()
        q = m_open_search.group(2).strip().rstrip(".!?")
        tmpl = SITE_SEARCH_URLS.get(site)
        if q and tmpl:
            return open_url_safe(tmpl.format(q=quote(q)))

    # Universal Natural Play / Watch / Listen (supports single-clause & multi-clause like
    # "play dude telugu songs", "play dude songs in youtube now", "play dude song in youtube stupid",
    # "do login in spotify if alreday login done play dude telugu songs")
    if (
        len(low) <= 180
        and not re.match(r"^(?:how|what|why|when|where|who|explain|tell\s+me|can\s+you\s+explain|define)\b", low)
        and not any(
            bad in low for bad in ("play a game", "play chess", "play with ", "play football", "play cricket", "watch out")
        )
    ):
        m_play_clause = re.search(r"\b(?:play|watch|listen\s+to)\s+(.+)$", low)
        if m_play_clause:
            raw_q, inline_plat = _clean_media_query(m_play_clause.group(1))
            chosen_plat: Optional[str] = inline_plat
            if not chosen_plat:
                for p in MEDIA_PLATFORMS:
                    if re.search(rf"\b{re.escape(p)}\b", low):
                        chosen_plat = p
                        break
            frustrated = bool(_FRUSTRATION_RE.search(low))
            if not raw_q or raw_q.lower() in _PLAY_RESUME_ONLY_WORDS:
                return _open_media_play(chosen_plat or _get_preferred_music_platform(), "", frustrated=frustrated)
            if not raw_q.startswith(("my file", "the file", "local ", "/")):
                login_req = bool(re.search(r"\b(?:log\s*in|sign\s*in|signin|login)\b", low))
                return _open_media_play(
                    chosen_plat or _get_preferred_music_platform(),
                    raw_q,
                    login_requested=login_req,
                    frustrated=frustrated,
                    approval_cb=approval_cb,
                )

    # Universal Natural Login ("login to/in <site>", "sign in to <site>", "do login in <site>")
    m_login = re.fullmatch(
        r"(?:please\s+)?(?:do\s+)?(?:log\s*in|sign\s*in|signin|login)(?:\s+(?:to|in|into|on))?\s+([a-z0-9][a-z0-9 ._+-]{1,40}?)(?:\s+(?:account|web|website|app))?",
        low,
    )
    if m_login:
        site = m_login.group(1).strip().rstrip(".!?")
        if site in LOGIN_URLS:
            opened = open_url_safe(LOGIN_URLS[site])
            return (
                f"🔐 {opened}\n"
                f"• Enter your credentials directly in your browser (Celina never reads or stores passwords for security)."
            )
        if site in WEB_APPS:
            opened = open_url_safe(WEB_APPS[site])
            return (
                f"🔐 {opened}\n"
                f"• Sign in directly in your browser (Celina never touches passwords for security)."
            )
        opened = open_application(f"{site} in web")
        return (
            f"🔐 {opened}\n"
            f"• Sign in directly in your browser (Celina never touches passwords for security)."
        )

    # Instant Universal App / Website Launch ("open <any app or website>", "open <anything> in web", "open https://...")
    m_open = re.fullmatch(r"(?:please\s+)?(?:open|launch|start|go\s+to)\s+(.+)", low)
    if m_open:
        target = m_open.group(1).strip().rstrip(".!?")
        # Avoid intercepting multi-step or document/file prompts that need full LLM reasoning
        if (
            target
            and len(target) <= 80
            and not any(tok in f" {target} " for tok in (" and ", " then ", " file ", " document ", " folder ", " pdf ", " docx ", " xlsx "))
            and not target.startswith(("my ", "the file ", "the document ", "a ", "/", "~/"))
        ):
            res = open_application(target)
            if not res.startswith("Application '"):
                return res

    conv = spotlight_index.convert_units(text)
    if conv:
        return f"🔄 {conv}"
    if any(ch.isdigit() for ch in text) and any(op in text for op in "+-*/^") and re.fullmatch(r"[0-9a-zA-Z\.\s\+\-\*\/\%\^\(\),]+", text.strip()):
        calc = spotlight_index.safe_calculate(text)
        if calc is not None:
            return f"🧮 {text.strip()} = {calc}"

    return None


def _search_flathub_app(query: str) -> Optional[tuple[str, str]]:
    """Dynamically searches Flathub for ANY desktop app via `flatpak search` and returns (Name, AppID)."""
    if not shutil.which("flatpak"):
        return None
    q = query.strip()
    if not q or len(q) > 50:
        return None
    res = command_runner.run_command(
        ["flatpak", "search", "--columns=application,name", q],
        timeout=12.0,
    )
    if not res.ok or not res.stdout.strip():
        return None
    q_low = q.lower()
    best: Optional[tuple[int, str, str]] = None
    for line in res.stdout.splitlines():
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if len(parts) < 2:
            continue
        app_id, app_name = parts[0], parts[1]
        if not _SAFE_FLATPAK_ID_RE.fullmatch(app_id):
            continue
        name_low = app_name.lower()
        tail_low = app_id.rsplit(".", 1)[-1].lower()
        score = 0
        if name_low == q_low or tail_low == q_low:
            score = 100
        elif name_low.startswith(q_low) or tail_low.startswith(q_low):
            score = 70
        elif q_low in name_low or q_low in app_id.lower():
            score = 40
        if score > 0 and (best is None or score > best[0]):
            best = (score, app_name, app_id)
    if best is None:
        return None
    return best[1], best[2]


def _check_apt_package(query: str) -> Optional[str]:
    """Checks if an exact Ubuntu APT package exists for CLI/system tools (e.g. ffmpeg, htop, mpv, neovim)."""
    if not shutil.which("apt-cache"):
        return None
    pkg = query.strip().lower().replace(" ", "-")
    if not _SAFE_APT_PKG_RE.fullmatch(pkg):
        return None
    res = command_runner.run_command(["apt-cache", "show", pkg], timeout=6.0)
    if res.ok and "Package:" in res.stdout:
        return pkg
    return None


def handle_fast_install_intent(
    text: str,
    *,
    approval_cb: Optional[ApprovalCallback] = None,
    status_cb: Optional[Callable[[str], None]] = None,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Universal, approval-gated Tier-2 app installer for 'download/install <ANY app>' requests:
      1. Instant match in curated Flathub catalog (Spotify, Chrome, VLC, Discord, VS Code, etc.)
      2. Dynamic live lookup across ALL 2,500+ Flathub apps (`flatpak search`)
      3. Exact Ubuntu APT package lookup (`apt-cache show`) for CLI/system utilities
      4. Automatic Flathub web search fallback if no direct package matches
    Always requires explicit argument-bound user approval; never installs silently.
    """
    low = text.lower().strip().rstrip(".!?")
    m = re.fullmatch(
        r"(?:please\s+)?(?:download|install)\s+(?:the\s+)?([a-z0-9][a-z0-9 ._+-]{0,40}?)(?:\s+(?:app|desktop|desktop app|package|via flatpak|from flathub|via apt))?",
        low,
    )
    if not m:
        return None
    raw_name = m.group(1).strip()
    if any(w in raw_name.split() for w in ("and", "then", "file", "video", "song", "mp3", "pdf", "youtube", "pip", "npm", "this", "it")):
        return None

    # 1. Curated instant Flathub match, else dynamic live Flathub search
    match = CURATED_INSTALLABLE_APPS.get(raw_name)
    source = "flathub"
    apt_pkg: Optional[str] = None
    if match is None and not dry_run:
        if status_cb:
            status_cb(f"Searching Flathub & APT for '{raw_name}'...")
        match = _search_flathub_app(raw_name)
        if match is None:
            apt_pkg = _check_apt_package(raw_name)
            if apt_pkg:
                source = "apt"
    elif match is None and dry_run:
        return (
            f"🧪 Dry-Run Simulation: Would dynamically search Flathub/APT for '{raw_name}', "
            f"ask approval, and install (or open https://flathub.org/apps/search?q={quote(raw_name)})."
        )

    # 2. Flathub installation path (covers curated + ALL 2,500+ Flathub apps!)
    if source == "flathub" and match is not None:
        display_name, app_id = match
        cmd_list = ["flatpak", "install", "-y", "--noninteractive", "flathub", app_id]
        cmd_str = " ".join(cmd_list)
        if dry_run:
            return f"🧪 Dry-Run Simulation: Would ask approval and run `{cmd_str}` to install {display_name}."
        if not shutil.which("flatpak"):
            url = f"https://flathub.org/apps/{app_id}"
            opened = open_url_safe(url)
            return f"Flatpak is not installed on this system. {opened}"
        if approval_cb is None:
            return f"Installing {display_name} requires interactive confirmation. Run in terminal:\n  {cmd_str}"

        args = {"command": cmd_str, "app_id": app_id, "mode": "flathub_install"}
        token, deny_err = approval.request_bound_approval(
            "run_shell_command",
            args,
            approval_cb,
            title=f"Install {display_name} from Flathub?",
            target=f"flathub:{app_id}",
        )
        if deny_err:
            return deny_err
        ok, err = approval.verify_and_consume(token, "run_shell_command", args)
        if not ok:
            return err

        if status_cb:
            status_cb(f"Installing {display_name} ({app_id}) from Flathub...")
        res = command_runner.run_command(cmd_list, timeout=300.0)
        if res.ok:
            return f"✅ Installed {display_name} ({app_id}). Say 'open {raw_name}' anytime to launch it."
        err_detail = (res.stderr or res.stdout or "unknown error").strip()[-400:]
        return f"Failed to install {display_name} ({app_id}): {err_detail}\nManual command: `{cmd_str}`"

    # 3. Exact APT package installation path (CLI/system apps like ffmpeg, htop, neovim, mpv, krita...)
    if source == "apt" and apt_pkg is not None:
        elevator = "pkexec" if (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and shutil.which("pkexec") else "sudo"
        cmd_list = [elevator, "apt-get", "install", "-y", apt_pkg]
        cmd_str = f"sudo apt-get install -y {apt_pkg}"
        if approval_cb is None:
            return f"Installing APT package '{apt_pkg}' requires confirmation. Run in terminal:\n  {cmd_str}"

        args = {"command": cmd_str, "package": apt_pkg, "mode": "apt_install"}
        token, deny_err = approval.request_bound_approval(
            "run_shell_command",
            args,
            approval_cb,
            title=f"Install Ubuntu package '{apt_pkg}' via APT?",
            target=f"apt:{apt_pkg}",
        )
        if deny_err:
            return deny_err
        ok, err = approval.verify_and_consume(token, "run_shell_command", args)
        if not ok:
            return err

        if status_cb:
            status_cb(f"Installing '{apt_pkg}' via APT...")
        res = command_runner.run_command(cmd_list, timeout=300.0)
        if res.ok:
            return f"✅ Installed '{apt_pkg}' via APT. Say 'open {raw_name}' or run `{apt_pkg}`."
        err_detail = (res.stderr or res.stdout or "unknown error").strip()[-400:]
        return f"Failed to install '{apt_pkg}': {err_detail}\nManual command: `{cmd_str}`"

    # 4. Fallback: open Flathub search page immediately
    url = f"https://flathub.org/apps/search?q={quote(raw_name)}"
    opened = open_url_safe(url)
    return (
        f"No exact Flathub or APT package matched '{raw_name}'. {opened}\n"
        f"Or search manually: `flatpak search {raw_name}`"
    )


handle_intent = handle_fast_intent
