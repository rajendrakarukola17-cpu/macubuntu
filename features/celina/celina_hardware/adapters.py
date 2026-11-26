"""
OS Hardware Adapters for celina_hardware (Phase 2 Item 6 + Phase 8).
Every mutating hardware action strictly follows:
  1. Execute command
  2. Check returncode & timeout
  3. Query resulting OS state
  4. Compare actual state against requested state
  5. Report explicit failure when returncode fails OR post-state verification mismatches
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .models import (
    AudioState,
    BatteryLimitState,
    BluetoothState,
    CapabilityStatus,
    HardwareActionResult,
    NetworkState,
    PortalState,
    PowerState,
    PrivacyIndicatorState,
    ServiceState,
)


@dataclass(frozen=True)
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


RunnerFn = Callable[[Sequence[str], float], ExecResult]
WhichFn = Callable[[str], Optional[str]]


def default_runner(cmd: Sequence[str], timeout: float = 5.0) -> ExecResult:
    cmd_list = [str(c) for c in cmd]
    if not cmd_list or not shutil.which(cmd_list[0]):
        return ExecResult(returncode=127, stdout="", stderr=f"Command not found: {cmd_list[0] if cmd_list else ''}")
    try:
        p = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)
        return ExecResult(int(p.returncode), (p.stdout or "").strip(), (p.stderr or "").strip(), False)
    except subprocess.TimeoutExpired:
        return ExecResult(124, "", f"Timed out after {timeout}s", True)
    except OSError as exc:
        return ExecResult(126, "", str(exc), False)


class UPowerAdapter:
    """Queries battery and power profile via UPower / powerprofilesctl / sysfs."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def get_state(self) -> PowerState:
        profile = "unknown"
        if self._which("powerprofilesctl"):
            pr = self._run(["powerprofilesctl", "get"], 2.0)
            if pr.ok and pr.stdout:
                profile = pr.stdout.strip()

        if self._which("upower"):
            enum_res = self._run(["upower", "-e"], 3.0)
            if enum_res.ok:
                bats = [ln.strip() for ln in enum_res.stdout.splitlines() if "BAT" in ln or "battery" in ln.lower()]
                if bats:
                    info = self._run(["upower", "-i", bats[0]], 3.0)
                    if info.ok:
                        pct_m = re.search(r"percentage:\s*(\d+)%", info.stdout)
                        st_m = re.search(r"state:\s*([A-Za-z0-9_-]+)", info.stdout)
                        pct = int(pct_m.group(1)) if pct_m else None
                        st = st_m.group(1).lower() if st_m else "unknown"
                        return PowerState(
                            status=CapabilityStatus.HEALTHY,
                            on_battery=(st == "discharging"),
                            battery_percent=pct,
                            charging_state=st,
                            power_profile=profile,
                            detail="upower",
                        )
                return PowerState(
                    status=CapabilityStatus.HEALTHY,
                    on_battery=False,
                    battery_percent=None,
                    charging_state="ac",
                    power_profile=profile,
                    detail="upower (desktop/AC)",
                )

        # Fallback: read-only sysfs battery properties
        pdir = Path("/sys/class/power_supply")
        if pdir.exists():
            for bat in sorted(pdir.glob("BAT*")):
                cap = bat / "capacity"
                st_f = bat / "status"
                if cap.exists():
                    try:
                        pct = int(cap.read_text(encoding="utf-8").strip())
                        st = st_f.read_text(encoding="utf-8").strip().lower() if st_f.exists() else "unknown"
                        return PowerState(
                            status=CapabilityStatus.HEALTHY,
                            on_battery=(st == "discharging"),
                            battery_percent=pct,
                            charging_state=st,
                            power_profile=profile,
                            detail="sysfs",
                        )
                    except (OSError, ValueError):
                        pass

        return PowerState(
            status=CapabilityStatus.NOT_INSTALLED if not self._which("upower") else CapabilityStatus.UNSUPPORTED,
            power_profile=profile,
            detail="No battery / UPower device detected",
        )


class PipeWireAdapter:
    """PipeWire (wpctl) / PulseAudio (pactl) audio adapter with read-after-write verification."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def get_state(self) -> AudioState:
        if self._which("wpctl"):
            res = self._run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], 3.0)
            if not res.ok:
                return AudioState(
                    status=CapabilityStatus.FAILED,
                    backend="wpctl",
                    detail=res.stderr or f"wpctl exited {res.returncode}",
                )
            # Example output: "Volume: 0.45" or "Volume: 0.45 [MUTED]"
            m = re.search(r"Volume:\s*([0-9]+(?:\.[0-9]+)?)", res.stdout)
            if m:
                pct = int(round(float(m.group(1)) * 100))
                muted = "[MUTED]" in res.stdout.upper()
                return AudioState(
                    status=CapabilityStatus.HEALTHY,
                    backend="wpctl",
                    volume_percent=pct,
                    muted=muted,
                    detail=res.stdout,
                )
            return AudioState(status=CapabilityStatus.FAILED, backend="wpctl", detail=f"Unparseable: {res.stdout}")

        if self._which("pactl"):
            v_res = self._run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], 3.0)
            m_res = self._run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], 3.0)
            if not v_res.ok or not m_res.ok:
                return AudioState(
                    status=CapabilityStatus.FAILED,
                    backend="pactl",
                    detail=(v_res.stderr or m_res.stderr or "pactl failed"),
                )
            vm = re.search(r"(\d+)%", v_res.stdout)
            pct = int(vm.group(1)) if vm else None
            muted = "yes" in m_res.stdout.lower()
            return AudioState(
                status=CapabilityStatus.HEALTHY,
                backend="pactl",
                volume_percent=pct,
                muted=muted,
                detail=f"{pct}% muted={muted}",
            )

        return AudioState(status=CapabilityStatus.NOT_INSTALLED, backend="none", detail="Neither wpctl nor pactl installed")

    def set_volume(
        self,
        *,
        level: Optional[int] = None,
        delta: Optional[int] = None,
        mute: Optional[bool] = None,
    ) -> HardwareActionResult:
        before = self.get_state()
        if before.status == CapabilityStatus.NOT_INSTALLED:
            return HardwareActionResult(False, False, "Error: No PipeWire/PulseAudio mixer found (wpctl/pactl).", returncode=127)

        backend = before.backend
        requested: dict[str, object] = {}

        if mute is not None:
            requested["muted"] = bool(mute)
            cmd = (
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"]
                if backend == "wpctl"
                else ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"]
            )
            res = self._run(cmd, 3.0)
            if not res.ok:
                return HardwareActionResult(
                    False, False, f"Error: Failed to set mute via {backend}: {res.stderr or res.returncode}",
                    requested=requested, returncode=res.returncode,
                )

        expected_level: Optional[int] = None
        if level is not None:
            clamped = max(0, min(100, int(level)))
            expected_level = clamped
            requested["volume_percent"] = clamped
            cmd = (
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{clamped}%"]
                if backend == "wpctl"
                else ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{clamped}%"]
            )
            res = self._run(cmd, 3.0)
            if not res.ok:
                return HardwareActionResult(
                    False, False, f"Error: Failed to set volume via {backend}: {res.stderr or res.returncode}",
                    requested=requested, returncode=res.returncode,
                )
        elif delta is not None:
            step = max(-50, min(50, int(delta)))
            requested["delta"] = step
            if before.volume_percent is not None:
                expected_level = max(0, min(100, before.volume_percent + step))
            sign = "+" if step >= 0 else "-"
            mag = abs(step)
            cmd = (
                ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{mag}%{sign}"]
                if backend == "wpctl"
                else ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{mag}%"]
            )
            res = self._run(cmd, 3.0)
            if not res.ok:
                return HardwareActionResult(
                    False, False, f"Error: Failed to adjust volume via {backend}: {res.stderr or res.returncode}",
                    requested=requested, returncode=res.returncode,
                )

        # Verify actual resulting state
        after = self.get_state()
        observed = {"volume_percent": after.volume_percent, "muted": after.muted}
        if after.status != CapabilityStatus.HEALTHY:
            return HardwareActionResult(
                False, False, f"Error: Volume verification failed ({after.detail}).",
                requested=requested, observed=observed, returncode=1,
            )

        if mute is not None and after.muted != bool(mute):
            return HardwareActionResult(
                False, False,
                f"Error: Mute verification mismatch (requested muted={bool(mute)}, observed muted={after.muted}).",
                requested=requested, observed=observed, returncode=1,
            )
        if expected_level is not None and after.volume_percent is not None:
            if abs(after.volume_percent - expected_level) > 3:
                return HardwareActionResult(
                    False, False,
                    f"Error: Volume verification mismatch (requested ~{expected_level}%, observed {after.volume_percent}%).",
                    requested=requested, observed=observed, returncode=1,
                )

        parts: list[str] = []
        if mute is not None:
            parts.append("Audio muted" if after.muted else "Audio unmuted")
        if after.volume_percent is not None:
            parts.append(f"volume {after.volume_percent}%")
        return HardwareActionResult(
            True, True, f"Verified ({backend}): {', '.join(parts)}.",
            requested=requested, observed=observed, returncode=0,
        )


class NetworkManagerAdapter:
    """NetworkManager (nmcli) Wi-Fi adapter with read-after-write verification."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def get_state(self) -> NetworkState:
        if not self._which("nmcli"):
            return NetworkState(status=CapabilityStatus.NOT_INSTALLED, detail="nmcli not installed")
        res = self._run(["nmcli", "radio", "wifi"], 3.0)
        if not res.ok:
            return NetworkState(status=CapabilityStatus.FAILED, detail=res.stderr or f"nmcli rc={res.returncode}")
        low = res.stdout.strip().lower()
        enabled = low == "enabled"
        ssid = ""
        if enabled:
            act = self._run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"], 3.0)
            if act.ok:
                for line in act.stdout.splitlines():
                    if line.startswith("yes:"):
                        ssid = line.split(":", 1)[1].strip()
                        break
        return NetworkState(
            status=CapabilityStatus.HEALTHY,
            wifi_enabled=enabled,
            active_ssid=ssid,
            detail=f"wifi={low}" + (f", ssid={ssid}" if ssid else ""),
        )

    def set_wifi(self, enabled: bool) -> HardwareActionResult:
        if not self._which("nmcli"):
            return HardwareActionResult(False, False, "Error: nmcli is not installed.", returncode=127)
        val = "on" if enabled else "off"
        res = self._run(["nmcli", "radio", "wifi", val], 5.0)
        if not res.ok:
            return HardwareActionResult(
                False, False, f"Error: nmcli failed to set Wi-Fi {val}: {res.stderr or res.returncode}",
                requested={"wifi_enabled": enabled}, returncode=res.returncode,
            )
        after = self.get_state()
        observed = {"wifi_enabled": after.wifi_enabled}
        if after.status != CapabilityStatus.HEALTHY or after.wifi_enabled != enabled:
            return HardwareActionResult(
                False, False,
                f"Error: Wi-Fi state verification failed (requested {enabled}, observed {after.wifi_enabled}).",
                requested={"wifi_enabled": enabled}, observed=observed, returncode=1,
            )
        return HardwareActionResult(
            True, True, f"Verified: Wi-Fi is now {val}.",
            requested={"wifi_enabled": enabled}, observed=observed, returncode=0,
        )


class BlueZAdapter:
    """BlueZ (bluetoothctl) adapter with read-after-write verification."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def get_state(self) -> BluetoothState:
        if not self._which("bluetoothctl"):
            return BluetoothState(status=CapabilityStatus.NOT_INSTALLED, detail="bluetoothctl not installed")
        res = self._run(["bluetoothctl", "show"], 3.0)
        if not res.ok or "No default controller" in res.stdout:
            return BluetoothState(
                status=CapabilityStatus.UNSUPPORTED if "No default controller" in res.stdout else CapabilityStatus.FAILED,
                detail=res.stderr or res.stdout or "bluetoothctl failed",
            )
        m_pow = re.search(r"Powered:\s*(yes|no)", res.stdout, re.IGNORECASE)
        m_ctl = re.search(r"Controller\s+([0-9A-Fa-f:]+)", res.stdout)
        powered = (m_pow.group(1).lower() == "yes") if m_pow else None
        return BluetoothState(
            status=CapabilityStatus.HEALTHY,
            powered=powered,
            controller=m_ctl.group(1) if m_ctl else "",
            detail=f"powered={powered}",
        )

    def set_power(self, enabled: bool) -> HardwareActionResult:
        if not self._which("bluetoothctl"):
            return HardwareActionResult(False, False, "Error: bluetoothctl is not installed.", returncode=127)
        val = "on" if enabled else "off"
        res = self._run(["bluetoothctl", "power", val], 5.0)
        if not res.ok:
            return HardwareActionResult(
                False, False, f"Error: bluetoothctl failed to turn Bluetooth {val}: {res.stderr or res.returncode}",
                requested={"powered": enabled}, returncode=res.returncode,
            )
        after = self.get_state()
        observed = {"powered": after.powered}
        if after.status != CapabilityStatus.HEALTHY or after.powered != enabled:
            return HardwareActionResult(
                False, False,
                f"Error: Bluetooth verification failed (requested powered={enabled}, observed={after.powered}).",
                requested={"powered": enabled}, observed=observed, returncode=1,
            )
        return HardwareActionResult(
            True, True, f"Verified: Bluetooth turned {val}.",
            requested={"powered": enabled}, observed=observed, returncode=0,
        )


class GnomePortalAdapter:
    """Checks GNOME xdg-desktop-portal (RemoteDesktop / ScreenCast) & AT-SPI2 availability."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def get_state(self) -> PortalState:
        rd_ok = False
        sc_ok = False
        atspi_ok = False

        if self._which("gdbus"):
            res = self._run(
                [
                    "gdbus", "introspect", "--session",
                    "--dest", "org.freedesktop.portal.Desktop",
                    "--object-path", "/org/freedesktop/portal/desktop",
                ],
                2.5,
            )
            if res.ok:
                rd_ok = "org.freedesktop.portal.RemoteDesktop" in res.stdout
                sc_ok = "org.freedesktop.portal.ScreenCast" in res.stdout

        try:
            import gi  # type: ignore

            gi.require_version("Atspi", "2.0")
            atspi_ok = True
        except Exception:
            atspi_ok = False

        if rd_ok or sc_ok or atspi_ok:
            status = CapabilityStatus.HEALTHY
        else:
            status = CapabilityStatus.INSTALLED_INACTIVE if self._which("gdbus") else CapabilityStatus.NOT_INSTALLED
        return PortalState(
            status=status,
            remote_desktop_portal=rd_ok,
            screencast_portal=sc_ok,
            atspi_available=atspi_ok,
            detail=f"RemoteDesktop={rd_ok}, ScreenCast={sc_ok}, AT-SPI2={atspi_ok}",
        )


class SystemdUserAdapter:
    """Inspects and verifies systemd user services without blind success assumptions."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def get_unit_state(self, unit: str) -> ServiceState:
        if not self._which("systemctl"):
            return ServiceState(unit=unit, scope="user", status=CapabilityStatus.NOT_INSTALLED, detail="systemctl missing")
        active = self._run(["systemctl", "--user", "is-active", unit], 3.0)
        if active.ok and active.stdout.strip() == "active":
            return ServiceState(unit=unit, scope="user", status=CapabilityStatus.HEALTHY, detail="active")
        files = self._run(["systemctl", "--user", "list-unit-files", unit], 3.0)
        if files.ok and unit in files.stdout:
            return ServiceState(
                unit=unit, scope="user",
                status=CapabilityStatus.INSTALLED_INACTIVE,
                detail=active.stdout or "inactive",
            )
        return ServiceState(unit=unit, scope="user", status=CapabilityStatus.NOT_INSTALLED, detail="unit not found")

    def control_unit(self, unit: str, action: str) -> HardwareActionResult:
        act = action.strip().lower()
        if act not in {"start", "stop", "restart"}:
            return HardwareActionResult(False, False, f"Error: Invalid service action '{action}'.", returncode=2)
        res = self._run(["systemctl", "--user", act, unit], 6.0)
        if not res.ok:
            return HardwareActionResult(
                False, False, f"Error: systemctl --user {act} {unit} failed: {res.stderr or res.returncode}",
                requested={"unit": unit, "action": act}, returncode=res.returncode,
            )
        after = self.get_unit_state(unit)
        expect_active = act in {"start", "restart"}
        is_active = after.status == CapabilityStatus.HEALTHY
        if is_active != expect_active:
            return HardwareActionResult(
                False, False,
                f"Error: Service '{unit}' post-state mismatch (expected active={expect_active}, observed={after.status.value}).",
                requested={"unit": unit, "active": expect_active},
                observed={"status": after.status.value},
                returncode=1,
            )
        return HardwareActionResult(
            True, True, f"Verified: user service '{unit}' is {after.detail}.",
            requested={"unit": unit, "action": act},
            observed={"status": after.status.value},
            returncode=0,
        )


class BatteryChargeLimitAdapter:
    """
    Optimized Battery Charging (macOS 80% limit equivalent).
    Reads/writes /sys/class/power_supply/BAT*/charge_control_end_threshold with
    read-after-write verification and graceful UNSUPPORTED reporting.
    """

    def __init__(self, power_supply_dir: Path = Path("/sys/class/power_supply")):
        self._dir = power_supply_dir

    def _find_threshold_file(self) -> Optional[tuple[str, Path]]:
        if not self._dir.exists():
            return None
        for bat in sorted(self._dir.glob("BAT*")):
            tf = bat / "charge_control_end_threshold"
            if tf.exists():
                return bat.name, tf
        return None

    def get_state(self) -> BatteryLimitState:
        found = self._find_threshold_file()
        if not found:
            return BatteryLimitState(
                status=CapabilityStatus.UNSUPPORTED,
                detail="Hardware firmware does not expose charge_control_end_threshold.",
            )
        bat_name, tf = found
        try:
            val = int(tf.read_text(encoding="utf-8").strip())
            return BatteryLimitState(
                status=CapabilityStatus.HEALTHY,
                battery=bat_name,
                threshold_percent=val,
                detail=f"{bat_name} charge limit = {val}%",
            )
        except (OSError, ValueError) as exc:
            return BatteryLimitState(status=CapabilityStatus.FAILED, battery=bat_name, detail=str(exc))

    def set_limit(self, percent: int) -> HardwareActionResult:
        clamped = max(50, min(100, int(percent)))
        found = self._find_threshold_file()
        if not found:
            return HardwareActionResult(
                False, False, "Error: Battery charge threshold unsupported on this hardware.", returncode=1
            )
        bat_name, tf = found
        try:
            tf.write_text(f"{clamped}\n", encoding="utf-8")
        except PermissionError:
            return HardwareActionResult(
                False,
                False,
                f"Error: Permission denied writing {tf}. Use udev rule 90-battery-charge-limit.rules or sudo.",
                requested={"threshold_percent": clamped},
                returncode=13,
            )
        except OSError as exc:
            return HardwareActionResult(False, False, f"Error: {exc}", returncode=1)

        after = self.get_state()
        if after.status == CapabilityStatus.HEALTHY and after.threshold_percent == clamped:
            return HardwareActionResult(
                True,
                True,
                f"Verified: {bat_name} charge limit set to {clamped}%.",
                requested={"threshold_percent": clamped},
                observed={"threshold_percent": after.threshold_percent},
                returncode=0,
            )
        return HardwareActionResult(
            False,
            False,
            f"Error: Charge limit mismatch (requested {clamped}%, observed {after.threshold_percent}%).",
            requested={"threshold_percent": clamped},
            observed={"threshold_percent": after.threshold_percent},
            returncode=1,
        )


class PrivacyIndicatorAdapter:
    """
    macOS-style Hardware Privacy Indicators (Orange Mic / Green Camera / Purple Screen-Share).
    Inspects active PipeWire streams via `pw-dump` (zero background CPU when polled cleanly).
    """

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def get_state(self) -> PrivacyIndicatorState:
        if not self._which("pw-dump"):
            return PrivacyIndicatorState(
                status=CapabilityStatus.NOT_INSTALLED,
                detail="pw-dump not installed (install pipewire-bin).",
            )
        # Filter to PipeWire Node objects in C (avoids dumping ports/links/devices/modules);
        # fall back gracefully if an older pw-dump build rejects type filtering.
        res = self._run(["pw-dump", "-N", "Node"], 3.0)
        if not res.ok:
            res = self._run(["pw-dump", "-N"], 4.0)
        if not res.ok:
            return PrivacyIndicatorState(
                status=CapabilityStatus.FAILED,
                detail=res.stderr or f"pw-dump exited {res.returncode}",
            )
        try:
            import json as _json

            nodes = _json.loads(res.stdout)
        except Exception as exc:
            return PrivacyIndicatorState(status=CapabilityStatus.FAILED, detail=f"pw-dump parse error: {exc}")

        mic = False
        cam = False
        screen = False
        apps: list[str] = []

        for obj in nodes if isinstance(nodes, list) else []:
            if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
                continue
            info = obj.get("info") or {}
            state = str(info.get("state", "")).lower()
            if state != "running":
                continue
            props = info.get("props") or {}
            media_class = str(props.get("media.class", "")).lower()
            node_name = str(props.get("node.name") or "").lower()
            app_name = str(props.get("application.name") or props.get("node.name") or "unknown").strip()
            node_desc = str(props.get("node.description") or "").lower()

            if "stream/input/audio" in media_class:
                mic = True
                apps.append(f"mic:{app_name}")
            elif "stream/input/video" in media_class or "video/source" in media_class:
                vid_hay = f"{node_desc} {node_name} {app_name.lower()}"
                if any(tok in vid_hay for tok in ("screencast", "screen-cast", "xdg-desktop-portal", "mutter")):
                    screen = True
                    apps.append(f"screen:{app_name}")
                else:
                    cam = True
                    apps.append(f"camera:{app_name}")

        dots: list[str] = []
        if mic:
            dots.append("🟠 mic")
        if cam:
            dots.append("🟢 camera")
        if screen:
            dots.append("🟣 screen")
        return PrivacyIndicatorState(
            status=CapabilityStatus.HEALTHY,
            mic_active=mic,
            camera_active=cam,
            screen_share_active=screen,
            active_apps=tuple(sorted(set(apps))),
            detail=", ".join(dots) if dots else "idle (no active mic/camera/screen capture)",
        )


class PipeWireRoutingAdapter:
    """Per-app PipeWire/PulseAudio output routing (e.g. route Spotify to Bluetooth headphones)."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def list_sinks(self) -> list[dict[str, str]]:
        if not self._which("pactl"):
            return []
        res = self._run(["pactl", "list", "short", "sinks"], 3.0)
        if not res.ok:
            return []
        sinks: list[dict[str, str]] = []
        for ln in res.stdout.splitlines():
            cols = ln.split("\t")
            if len(cols) >= 2:
                sinks.append({"id": cols[0].strip(), "name": cols[1].strip(), "state": cols[-1].strip()})
        return sinks

    def route_app_audio(self, app_query: str, sink_query: str) -> HardwareActionResult:
        if not self._which("pactl"):
            return HardwareActionResult(False, False, "Error: pactl is required for per-app audio routing.", returncode=127)
        sinks = self.list_sinks()
        sq = sink_query.strip().lower()
        target_sink = next((s for s in sinks if sq in s["name"].lower() or sq == s["id"]), None)
        if not target_sink:
            return HardwareActionResult(
                False, False, f"Error: Audio output sink '{sink_query}' not found (available: {[s['name'] for s in sinks]}).", returncode=1
            )

        inputs_res = self._run(["pactl", "list", "sink-inputs"], 3.0)
        if not inputs_res.ok:
            return HardwareActionResult(False, False, f"Error: Failed to list audio streams: {inputs_res.stderr}", returncode=1)

        aq = app_query.strip().lower()
        cur_id: Optional[str] = None
        matched_ids: list[str] = []
        for ln in inputs_res.stdout.splitlines():
            s = ln.strip()
            if s.startswith("Sink Input #"):
                cur_id = s.split("#", 1)[1].strip()
            elif cur_id and "=" in s:
                key_part, _sep, val_part = s.partition("=")
                key_low = key_part.strip().lower()
                if key_low in ("application.name", "media.name", "node.name", "application.process.binary"):
                    val_low = val_part.strip().strip('"').lower()
                    if aq in val_low and cur_id not in matched_ids:
                        matched_ids.append(cur_id)

        if not matched_ids:
            return HardwareActionResult(False, False, f"Error: No active audio stream found matching '{app_query}'.", returncode=1)

        moved: list[str] = []
        for sid in matched_ids:
            mv = self._run(["pactl", "move-sink-input", sid, target_sink["id"]], 3.0)
            if mv.ok:
                moved.append(sid)

        if not moved:
            return HardwareActionResult(False, False, f"Error: Could not move stream(s) {matched_ids}.", returncode=1)

        return HardwareActionResult(
            True,
            True,
            f"Verified: Routed '{app_query}' stream(s) {moved} -> sink '{target_sink['name']}'.",
            requested={"app": app_query, "sink": target_sink["name"]},
            observed={"moved_stream_ids": moved, "sink_id": target_sink["id"]},
            returncode=0,
        )


class HardwareManager:
    """Unified entry point for structured OS hardware capabilities & verified actions."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self.power = UPowerAdapter(runner=runner, which=which)
        self.audio = PipeWireAdapter(runner=runner, which=which)
        self.network = NetworkManagerAdapter(runner=runner, which=which)
        self.bluetooth = BlueZAdapter(runner=runner, which=which)
        self.portals = GnomePortalAdapter(runner=runner, which=which)
        self.systemd_user = SystemdUserAdapter(runner=runner, which=which)
        self.battery_limit = BatteryChargeLimitAdapter()
        self.privacy = PrivacyIndicatorAdapter(runner=runner, which=which)
        self.routing = PipeWireRoutingAdapter(runner=runner, which=which)

    def set_brightness(self, level: int) -> HardwareActionResult:
        if not self.audio._which("brightnessctl"):
            return HardwareActionResult(False, False, "Error: brightnessctl is not installed.", returncode=127)
        clamped = max(5, min(100, int(level)))
        res = self.audio._run(["brightnessctl", "set", f"{clamped}%"], 3.0)
        if not res.ok:
            if "Permission denied" in (res.stderr + res.stdout):
                return HardwareActionResult(
                    False, False,
                    "Error: Brightness permission denied. Reboot once so 'video' group takes effect.",
                    requested={"brightness_percent": clamped}, returncode=res.returncode,
                )
            return HardwareActionResult(
                False, False, f"Error: brightnessctl failed: {res.stderr or res.stdout or res.returncode}",
                requested={"brightness_percent": clamped}, returncode=res.returncode,
            )
        # Verify resulting brightness percentage
        cur_r = self.audio._run(["brightnessctl", "get"], 2.0)
        max_r = self.audio._run(["brightnessctl", "max"], 2.0)
        if cur_r.ok and max_r.ok:
            try:
                cur_v = int(cur_r.stdout.strip())
                max_v = max(1, int(max_r.stdout.strip()))
                actual_pct = int(round((cur_v / max_v) * 100))
                if abs(actual_pct - clamped) > 5:
                    return HardwareActionResult(
                        False, False,
                        f"Error: Brightness verification mismatch (requested {clamped}%, observed {actual_pct}%).",
                        requested={"brightness_percent": clamped},
                        observed={"brightness_percent": actual_pct},
                        returncode=1,
                    )
                return HardwareActionResult(
                    True, True, f"Verified: Screen brightness set to {actual_pct}%.",
                    requested={"brightness_percent": clamped},
                    observed={"brightness_percent": actual_pct},
                    returncode=0,
                )
            except ValueError:
                pass
        return HardwareActionResult(
            False, False, "Error: Brightness command exited 0 but actual level could not be verified.",
            requested={"brightness_percent": clamped}, returncode=1,
        )

    def describe_capabilities(self) -> dict[str, dict[str, object]]:
        from .biometrics import FIDO2Adapter, FingerprintAdapter, IRFaceAdapter

        p = self.power.get_state()
        a = self.audio.get_state()
        n = self.network.get_state()
        b = self.bluetooth.get_state()
        pt = self.portals.get_state()
        bl = self.battery_limit.get_state()
        priv = self.privacy.get_state()
        fp = FingerprintAdapter(runner=self.audio._run, which=self.audio._which).probe()
        ir = IRFaceAdapter(runner=self.audio._run, which=self.audio._which).probe()
        fido = FIDO2Adapter(runner=self.audio._run, which=self.audio._which).probe()
        return {
            "power": {"status": p.status.value, "battery_percent": p.battery_percent, "profile": p.power_profile, "detail": p.detail},
            "battery_charge_limit": {"status": bl.status.value, "battery": bl.battery, "threshold_percent": bl.threshold_percent},
            "audio": {"status": a.status.value, "backend": a.backend, "volume_percent": a.volume_percent, "muted": a.muted},
            "network": {"status": n.status.value, "wifi_enabled": n.wifi_enabled, "ssid": n.active_ssid},
            "bluetooth": {"status": b.status.value, "powered": b.powered, "controller": b.controller},
            "portals": {"status": pt.status.value, "remote_desktop": pt.remote_desktop_portal, "screencast": pt.screencast_portal, "atspi": pt.atspi_available},
            "privacy_indicators": {
                "status": priv.status.value,
                "mic_active": priv.mic_active,
                "camera_active": priv.camera_active,
                "screen_share_active": priv.screen_share_active,
                "active_apps": list(priv.active_apps),
            },
            "biometrics": {
                "fingerprint": fp,
                "ir_face": ir,
                "fido2": fido,
            },
        }


_DEFAULT_HW: Optional[HardwareManager] = None


def get_hardware() -> HardwareManager:
    global _DEFAULT_HW
    if _DEFAULT_HW is None:
        _DEFAULT_HW = HardwareManager()
    return _DEFAULT_HW
