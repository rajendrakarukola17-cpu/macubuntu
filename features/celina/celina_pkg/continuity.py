"""Celina AI — Cross-Platform Continuity, TCC Privacy Auditor & Universal Menu Palette (P1/P3/P5)
Bridges Ubuntu 26.04 cleanly to ALL device ecosystems:
  - Android : GSConnect + scrcpy (60fps USB/Wireless mirroring)
  - iOS/Mac : LocalSend (AirDrop), Shairport-Sync (AirPlay Audio), UxPlay (AirPlay 2 Video)
  - Win/Lin : Deskflow / lan-mouse (Wayland Universal Control), Syncthing (E2EE Sync), Sidecar RDP
  - Privacy : Unified TCC-style Per-App Permission Auditor (PipeWire mic/cam + Flatpak overrides + autostart)
  - Menus   : AT-SPI2 Universal Menu Command Palette (macOS Help-Menu-Search equivalent!)
  - Writing : Local privacy-safe Writing Tools helper on Wayland selection/clipboard
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import approval, automation, command_runner, config, ios_ancs

ApprovalCallback = Callable[[str, str], bool]

_HW_PARENT = str(Path(__file__).resolve().parent.parent)
if _HW_PARENT not in sys.path:
    sys.path.insert(0, _HW_PARENT)
try:
    import celina_hardware as _celina_hardware
except Exception:
    _celina_hardware = None  # type: ignore[assignment]


def get_continuity_status() -> str:
    """Returns a unified live status matrix across Android, iOS/Mac, Windows, Linux & Sidecar continuity."""
    checks: dict[str, str] = {}

    # 1. Android mirroring (scrcpy + adb connected devices)
    if shutil.which("scrcpy") and shutil.which("adb"):
        adb_r = command_runner.run_command(["adb", "devices"], timeout=3.0)
        devs = [
            ln.split()[0]
            for ln in adb_r.stdout.splitlines()[1:]
            if ln.strip() and "\tdevice" in ln
        ] if adb_r.ok else []
        checks["android_scrcpy"] = f"ready ({len(devs)} device(s): {', '.join(devs)})" if devs else "installed (connect Android via USB/wireless ADB)"
    else:
        checks["android_scrcpy"] = "not installed (sudo apt install scrcpy adb)"

    # 2. AirPlay Video (UxPlay) & Audio (Shairport-Sync)
    for key, unit, binary in (
        ("airplay_video_uxplay", "uxplay-user.service", "uxplay"),
        ("airplay_audio_shairport", "shairport-sync-user.service", "shairport-sync"),
        ("syncthing_e2ee_sync", "syncthing.service", "syncthing"),
    ):
        if not shutil.which(binary):
            checks[key] = f"not installed ({binary})"
        elif _celina_hardware is not None:
            st = _celina_hardware.get_hardware().systemd_user.get_unit_state(unit)
            checks[key] = f"{st.status.value} ({st.detail})"
        else:
            checks[key] = "installed"

    # 3. LocalSend & Deskflow Flatpaks
    if shutil.which("flatpak"):
        fp_r = command_runner.run_command(["flatpak", "list", "--app", "--columns=application"], timeout=4.0)
        installed_fp = set(fp_r.stdout.splitlines()) if fp_r.ok else set()
        checks["airdrop_localsend"] = "installed" if "org.localsend.localsend_app" in installed_fp else "not installed"
        checks["universal_control_deskflow"] = "installed" if "org.deskflow.deskflow" in installed_fp else "not installed"
        checks["privacy_flatseal"] = "installed" if "com.github.tchx84.Flatseal" in installed_fp else "not installed"

    # 4. Sidecar / GNOME Remote Desktop
    if shutil.which("grdctl"):
        grd = command_runner.run_command(["grdctl", "status"], timeout=3.0)
        checks["sidecar_gnome_rdp"] = (
            "configured/ready" if grd.ok else f"installed ({grd.stderr or grd.output or 'inactive'})"
        )
    else:
        checks["sidecar_gnome_rdp"] = "not installed (gnome-remote-desktop)"

    # 5. iOS BLE ANCS Notifications & AirPods / Bluetooth Battery (BlueZ GATT / Battery1)
    checks["ios_ancs_ble"] = ios_ancs.get_ios_ancs_status()
    checks["bluetooth_batteries"] = ios_ancs.get_bluetooth_battery_status()

    # 5b. USB iPhone Sync (libimobiledevice / ifuse), Continuity Camera (v4l2loopback) & iPad Sidecar (Weylus)
    checks["ios_usb_sync"] = get_ios_usb_status()
    checks["kdeconnect_ios_gsconnect"] = (
        "GSConnect installed (pair KDE Connect iOS app for 1-tap clipboard, remote trackpad & battery)"
        if (config.HOME / ".local" / "share" / "gnome-shell" / "extensions" / "gsconnect@andyholmes.github.io").exists()
        or Path("/usr/share/gnome-shell/extensions/gsconnect@andyholmes.github.io").exists()
        else "optional (install GSConnect + KDE Connect iOS app)"
    )

    v4l2_loaded = Path("/sys/module/v4l2loopback").exists()
    checks["continuity_camera_v4l2"] = (
        "v4l2loopback kernel module loaded (virtual webcam ready)"
        if v4l2_loaded
        else "not loaded (optional: sudo apt install v4l2loopback-dkms)"
    )
    checks["ipad_sidecar_weylus"] = (
        "installed (ready for iPad Apple Pencil & multi-touch via Safari)"
        if shutil.which("weylus")
        else "optional (install Weylus for 60fps iPad Sidecar + Apple Pencil)"
    )

    # 6. AirPlay 2 Handshake Health Check (surfaces iOS/macOS AirPlay key/TLS regressions loudly)
    handshake_warn = check_airplay_handshake_health()
    if handshake_warn:
        checks["airplay_handshake_warning"] = handshake_warn

    # 7. Mesh VPN (Tailscale / WireGuard)
    if shutil.which("tailscale"):
        ts = command_runner.run_command(["tailscale", "status", "--peers=false"], timeout=3.0)
        checks["mesh_vpn"] = "tailscale active" if ts.ok else "tailscale installed (logged out)"
    elif shutil.which("wg"):
        checks["mesh_vpn"] = "wireguard-tools installed"
    else:
        checks["mesh_vpn"] = "none installed (optional: tailscale or wireguard)"

    lines = ["🌐 Cross-Platform Continuity Hub Status:"]
    for k, v in checks.items():
        lines.append(f"• {k}: {v}")
    return "\n".join(lines)


def check_airplay_handshake_health() -> str:
    """Inspects recent user journal logs for uxplay-user.service / shairport-sync-user.service
    to warn loudly on AirPlay 2 FairPlay/SRP/TLS handshake failures (e.g., after iOS/macOS updates).
    """
    if not shutil.which("journalctl"):
        return ""
    res = command_runner.run_command(
        [
            "journalctl",
            "--user",
            "-u",
            "uxplay-user.service",
            "-u",
            "shairport-sync-user.service",
            "--since",
            "1 hour ago",
            "-n",
            "40",
            "--no-pager",
            "-o",
            "cat",
        ],
        timeout=2.0,
    )
    if not res.ok or not res.stdout:
        return ""
    sig = re.compile(
        r"(pair[- ]?verify|fairplay|handshake\s+fail|auth(?:entication)?\s+fail|decrypt.*fail|hap.*error)",
        re.IGNORECASE,
    )
    for line in reversed(res.stdout.splitlines()):
        if sig.search(line):
            return (
                f"Recent AirPlay 2 handshake warning detected ({line.strip()[:140]}). "
                "See COMPATIBILITY.md for iOS/macOS AirPlay 2 key/pin notes."
            )
    return ""


def launch_android_mirror(
    serial: str = "",
    turn_screen_off: bool = True,
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """Launches 60fps Android screen mirroring via Genymobile scrcpy (approval-gated)."""
    if not shutil.which("scrcpy"):
        return "Error: scrcpy is not installed (run Stage 40 or `sudo apt install scrcpy adb`)."
    clean_serial = re.sub(r"[^a-zA-Z0-9._:-]+", "", (serial or "").strip())[:64]
    args = {"serial": clean_serial, "turn_screen_off": bool(turn_screen_off)}
    token, deny_err = approval.request_bound_approval(
        "launch_android_mirror",
        args,
        approval_cb,
        title="Approve Launching Android Mirror (scrcpy)?",
        target=clean_serial or "default-adb-device",
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "launch_android_mirror", args)
    if not ok:
        return err

    cmd = ["scrcpy", "--stay-awake"]
    if turn_screen_off:
        cmd.append("--turn-screen-off")
    if clean_serial:
        cmd.extend(["-s", clean_serial])

    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Launched scrcpy Android mirror ({clean_serial or 'default connected device'})."
    except OSError as exc:
        return f"Error: Could not launch scrcpy: {exc}"


def control_airplay_mirror(action: str = "status", approval_cb: ApprovalCallback | None = None) -> str:
    """Starts, stops, or checks AirPlay 2 Video+Audio mirroring receiver (uxplay-user.service)."""
    act = (action or "status").strip().lower()
    if act not in {"status", "start", "stop"}:
        return "Error: action must be 'status', 'start', or 'stop'."
    if not shutil.which("uxplay"):
        return "Error: uxplay is not installed (run Stage 40 or `sudo apt install uxplay`)."
    if _celina_hardware is None:
        return "Error: hardware service controller unavailable."

    sd = _celina_hardware.get_hardware().systemd_user
    if act == "status":
        st = sd.get_unit_state("uxplay-user.service")
        return f"AirPlay 2 Video Mirror (UxPlay): {st.status.value} ({st.detail})"

    args = {"unit": "uxplay-user.service", "action": act}
    token, deny_err = approval.request_bound_approval(
        "control_airplay_mirror",
        args,
        approval_cb,
        title=f"Approve AirPlay 2 Video Mirror ({act.upper()})?",
        target="uxplay-user.service",
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "control_airplay_mirror", args)
    if not ok:
        return err

    res = sd.control_unit("uxplay-user.service", act)
    return res.message


def audit_app_permissions(
    autostart_dir: Path = config.HOME / ".config" / "autostart",
    flatpak_overrides_dir: Path = config.HOME / ".local" / "share" / "flatpak" / "overrides",
) -> str:
    """MacOS Privacy & Security (TCC) Auditor:
    1. Live active PipeWire mic / camera / screen-share streams
    2. Flatpak user permission overrides (host filesystem / device grants)
    3. User desktop autostart entries
    """
    report: list[str] = ["🛡️ macubuntu Per-App Privacy & Permissions Audit:"]

    # 1. Live PipeWire hardware indicators
    if _celina_hardware is not None:
        priv = _celina_hardware.get_hardware().privacy.get_state()
        report.append(f"• Live Hardware Sensors: {priv.detail}")
        if priv.active_apps:
            report.append(f"  Active streams: {', '.join(priv.active_apps)}")
    else:
        report.append("• Live Hardware Sensors: unavailable")

    # 2. Flatpak user overrides
    ov_items: list[str] = []
    if flatpak_overrides_dir.exists():
        for f in sorted(flatpak_overrides_dir.iterdir()):
            if f.is_file():
                try:
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                    risky = [ln.strip() for ln in txt.splitlines() if "filesystem=host" in ln or "device=all" in ln]
                    ov_items.append(f"{f.name}" + (f" [BROAD: {', '.join(risky)}]" if risky else " [custom override]"))
                except OSError:
                    continue
    report.append(
        "• Flatpak Overrides: " + (", ".join(ov_items[:10]) if ov_items else "none (clean sandbox defaults)"),
    )

    # 3. Autostart apps
    auto_items: list[str] = []
    if autostart_dir.exists():
        for f in sorted(autostart_dir.glob("*.desktop")):
            auto_items.append(f.stem)
    report.append(
        f"• Login Autostart Entries ({len(auto_items)}): " + (", ".join(auto_items[:12]) if auto_items else "none"),
    )
    report.append("Tip: Open Flatseal (`flatpak run com.github.tchx84.Flatseal`) for visual per-app sandbox controls.")
    return "\n".join(report)


def search_active_app_menus(query: str = "", max_results: int = 15) -> str:
    """MacOS Help-Menu-Search equivalent: walks the active application's AT-SPI2 menu bar
    & menu items (<40ms) so you can discover any app action without hunting through menus.
    """
    q = (query or "").strip().lower()
    win_info = automation.get_active_window_info()
    app_label = win_info.get("app") or "active window"
    elements = automation.get_atspi_elements(max_elements=120)
    menu_roles = {"menu", "menu item", "check menu item", "radio menu item", "push button", "page tab"}
    hits = [
        el for el in elements
        if el.get("role") in menu_roles and (not q or q in str(el.get("label", "")).lower())
    ][: max(1, min(30, int(max_results)))]

    if not hits:
        return f"No matching menu actions found in '{app_label}' for '{query or '*'}'."
    lines = [f"⌘ Command Palette ({app_label}):"]
    for i, el in enumerate(hits, 1):
        lines.append(f"{i}. [{el['role']}] {el['label']}  (at {el['x']},{el['y']})")
    return "\n".join(lines)


# ============================================================================
# Complete Open-Source iOS / iPadOS Continuity (v4.4):
#   - USB/Wi-Fi iPhone Sync & Mount (`libimobiledevice` + `ifuse`)
#   - Continuity Camera (`UxPlay` -> `v4l2loopback` virtual webcam)
#   - iPad Sidecar with Apple Pencil Pressure (`Weylus` over PipeWire)
#   - BLE ANCS Call/Notification Actions (`Accept` / `Dismiss`)
# ============================================================================

def get_ios_usb_status() -> str:
    """Checks for USB/Wi-Fi connected iPhones or iPads via libimobiledevice (idevice_id)."""
    if not shutil.which("idevice_id"):
        return "not installed (sudo apt install libimobiledevice-utils ifuse usbmuxd)"
    res = command_runner.run_command(["idevice_id", "-l"], timeout=3.0)
    if not res.ok or not res.stdout.strip():
        return "installed (no USB iOS device plugged in)"
    udids = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    names: list[str] = []
    if shutil.which("ideviceinfo"):
        for u in udids[:3]:
            info = command_runner.run_command(["ideviceinfo", "-u", u, "-k", "DeviceName"], timeout=2.5)
            names.append(info.stdout.strip() if (info.ok and info.stdout.strip()) else u[:8])
    return f"connected ({len(udids)} iOS device(s): {', '.join(names or udids)})"


def mount_ios_device(
    action: str = "mount",
    mount_dir: str = "~/iPhone",
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """Mounts or unmounts a USB-connected iPhone/iPad into ~/iPhone via `ifuse`
    so photos (DCIM) and files appear natively in Nautilus Finder.
    """
    act = (action or "mount").strip().lower()
    if act not in {"mount", "unmount", "status"}:
        return "Error: action must be 'mount', 'unmount', or 'status'."
    target = Path(mount_dir).expanduser().resolve()
    try:
        target.relative_to(config.HOME.resolve())
    except ValueError:
        return "Error: Mount directory must be inside your home directory."

    if act == "status":
        return get_ios_usb_status()

    if not shutil.which("ifuse"):
        return "Error: ifuse is not installed (run: sudo apt install ifuse libimobiledevice-utils usbmuxd)."

    args = {"action": act, "mount_dir": str(target)}
    token, deny_err = approval.request_bound_approval(
        "mount_ios_device",
        args,
        approval_cb,
        title=f"Approve iOS USB {act.capitalize()} ({target.name})?",
        target=str(target),
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "mount_ios_device", args)
    if not ok:
        return err

    if act == "mount":
        target.mkdir(parents=True, exist_ok=True)
        res = command_runner.run_command(["ifuse", str(target)], timeout=8.0)
        if not res.ok:
            return (
                f"Error mounting iOS device: {res.stderr or res.output}. "
                "Ensure your iPhone is unlocked and you tapped 'Trust This Computer'."
            )
        return f"Verified: Mounted iOS device at {target} (open in Nautilus Finder)."

    um_bin = "fusermount3" if shutil.which("fusermount3") else "fusermount"
    res = command_runner.run_command([um_bin, "-u", str(target)], timeout=5.0)
    if not res.ok:
        return f"Error unmounting {target}: {res.stderr or res.output}"
    return f"Verified: Unmounted iOS device from {target}."


def start_ios_continuity_camera(
    video_device: str = "/dev/video10",
    action: str = "start",
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """MacOS Continuity Camera equivalent: launches (or stops) UxPlay piping iPhone AirPlay video
    into a v4l2loopback virtual webcam device so Zoom/Meet/Firefox can use the iPhone camera.
    Automatically pauses conflicting `uxplay-user.service` screen-mirroring before starting.
    """
    act = (action or "start").strip().lower()
    if act not in {"start", "stop"}:
        return "Error: action must be 'start' or 'stop'."
    if act == "stop":
        args = {"action": "stop", "video_device": str(video_device or "/dev/video10").strip()}
        token, deny_err = approval.request_bound_approval(
            "start_ios_continuity_camera",
            args,
            approval_cb,
            title="Approve Stopping iPhone Continuity Camera?",
            target=args["video_device"],
        )
        if deny_err:
            return deny_err
        ok, err = approval.verify_and_consume(token, "start_ios_continuity_camera", args)
        if not ok:
            return err
        command_runner.run_command(["pkill", "-f", "uxplay.*v4l2sink"], timeout=3.0)
        return "Stopped iPhone Continuity Camera (UxPlay v4l2sink)."

    if not shutil.which("uxplay"):
        return "Error: uxplay is not installed."
    clean_dev = (video_device or "/dev/video10").strip()
    if not re.fullmatch(r"/dev/video\d{1,2}", clean_dev):
        return "Error: video_device must be a valid V4L2 node like /dev/video10."
    dev_path = Path(clean_dev)
    if not dev_path.exists():
        return (
            f"Error: Virtual webcam {dev_path} does not exist. "
            "Load v4l2loopback first: `sudo modprobe v4l2loopback video_nr=10 "
            'card_label="iPhone Continuity Camera" exclusive_caps=1`'
        )

    args = {"action": "start", "video_device": str(dev_path)}
    token, deny_err = approval.request_bound_approval(
        "start_ios_continuity_camera",
        args,
        approval_cb,
        title=f"Approve Starting iPhone Continuity Camera ({dev_path})?",
        target=str(dev_path),
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "start_ios_continuity_camera", args)
    if not ok:
        return err

    # Prevent AirPlay mDNS/UDP port conflict if uxplay-user.service screen mirror is already running
    paused_mirror = False
    if _celina_hardware is not None:
        try:
            sd = _celina_hardware.get_hardware().systemd_user
            if sd.get_unit_state("uxplay-user.service").status.value == "healthy":
                sd.control_unit("uxplay-user.service", "stop")
                paused_mirror = True
        except Exception:
            pass

    cam_name = f"{config.get_assistant_name()}-Cam"
    cmd = ["uxplay", "-n", cam_name, "-vs", f"v4l2sink device={dev_path}"]
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        pause_note = " (paused uxplay-user.service to avoid AirPlay port conflict)" if paused_mirror else ""
        return (
            f"Started iPhone Continuity Camera on {dev_path}{pause_note}. "
            f"Select '{cam_name}' in iPhone Screen Mirroring, then pick it as your webcam in Zoom/Meet/OBS."
        )
    except OSError as exc:
        return f"Error launching UxPlay Continuity Camera: {exc}"


def start_ipad_sidecar_weylus(
    action: str = "start",
    web_port: int = 1701,
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """Open-source iPad Sidecar + Apple Pencil pressure/tilt/multi-touch bridge via Weylus
    (MIT, PipeWire ScreenCast + Wayland libei — opens right inside iPad Safari, zero App Store app).
    """
    act = (action or "start").strip().lower()
    if act not in {"status", "start", "stop"}:
        return "Error: action must be 'status', 'start', or 'stop'."
    port = max(1024, min(65535, int(web_port)))

    if act == "status":
        if not shutil.which("weylus"):
            return (
                "Weylus not installed. Install from https://github.com/H-M-H/Weylus/releases "
                "(or use built-in GNOME Remote Desktop Sidecar via `grdctl`)."
            )
        pg = command_runner.run_command(["pgrep", "-x", "weylus"], timeout=2.0)
        return (
            f"Weylus iPad Sidecar is running (open http://<this-pc-ip>:{port} in iPad Safari)."
            if pg.ok and pg.stdout.strip()
            else "Weylus installed (currently stopped)."
        )

    if act == "start" and not shutil.which("weylus"):
        return "Error: weylus is not installed. Download the MIT Linux release from H-M-H/Weylus."

    args = {"action": act, "web_port": port}
    token, deny_err = approval.request_bound_approval(
        "start_ipad_sidecar_weylus",
        args,
        approval_cb,
        title=f"Approve Weylus iPad Sidecar ({act.upper()} on port {port})?",
        target=f"weylus:{port}",
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "start_ipad_sidecar_weylus", args)
    if not ok:
        return err

    if act == "stop":
        command_runner.run_command(["pkill", "-x", "weylus"], timeout=3.0)
        return "Stopped Weylus iPad Sidecar."

    cmd = ["weylus", "--no-gui", "--web-port", str(port), "--ws-port", str(port + 1)]
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return (
            f"Started Weylus iPad Sidecar on LAN port {port}. "
            f"Open http://<your-ubuntu-lan-ip>:{port} in iPad Safari for 60 FPS display + Apple Pencil."
        )
    except OSError as exc:
        return f"Error starting Weylus: {exc}"


def respond_ios_notification(
    action: str = "dismiss",
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """Triggers Apple BLE ANCS Control Point action on the latest iPhone notification/call:
    - action='accept' / 'answer' / 'positive' -> ANCS Positive Action (answer call / accept)
    - action='dismiss' / 'decline' / 'negative' -> ANCS Negative Action (decline call / dismiss)
    """
    act = (action or "dismiss").strip().lower()
    if act in {"accept", "answer", "positive", "yes"}:
        positive = True
        label = "Accept / Answer"
    elif act in {"dismiss", "decline", "negative", "reject", "no"}:
        positive = False
        label = "Dismiss / Decline"
    else:
        return "Error: action must be 'accept'/'answer' or 'dismiss'/'decline'."

    args = {"action": "positive" if positive else "negative"}
    token, deny_err = approval.request_bound_approval(
        "respond_ios_notification",
        args,
        approval_cb,
        title=f"Approve iOS ANCS Action: {label}?",
        target="ios-ancs-active-alert",
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "respond_ios_notification", args)
    if not ok:
        return err

    return ios_ancs.perform_ios_notification_action(positive=positive)

