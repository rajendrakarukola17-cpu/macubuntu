"""
Mac-Level System & Cross-Platform Ecosystem Integration Tests (Tracks P1-P5):
  - BatteryChargeLimitAdapter (80% threshold read-after-write verification)
  - PrivacyIndicatorAdapter (PipeWire live mic/cam/screen-share dots)
  - PipeWireRoutingAdapter (per-app audio sink routing)
  - FocusModeManager & AutoAppNapPolicy & CgroupFreezer
  - Safe AST CoreSpotlight Calculator, Unit Converter & Private 0600 FTS5 Index
  - Continuity Hub, TCC Privacy Auditor, Universal Menu Palette & scrcpy approval gate
  - Native GNOME RemoteDesktop Portal lifecycle & Esc stop teardown
"""

import json
import stat
from pathlib import Path
from unittest import mock

import pytest

from celina_hardware.adapters import (
    BatteryChargeLimitAdapter,
    ExecResult,
    PipeWireRoutingAdapter,
    PrivacyIndicatorAdapter,
)
from celina_hardware.models import CapabilityStatus
from celina_pkg import automation, command_runner, continuity, ios_ancs, os_events, portal_input, spotlight_index, tools


class TestNewHardwareAdapters:
    def test_battery_charge_limit_verified_write_and_unsupported(self, tmp_path: Path):
        # Unsupported firmware returns graceful UNSUPPORTED
        empty_ad = BatteryChargeLimitAdapter(power_supply_dir=tmp_path / "none")
        assert empty_ad.get_state().status == CapabilityStatus.UNSUPPORTED

        # Supported firmware reads/writes & verifies
        bat0 = tmp_path / "BAT0"
        bat0.mkdir()
        tf = bat0 / "charge_control_end_threshold"
        tf.write_text("100\n", encoding="utf-8")

        ad = BatteryChargeLimitAdapter(power_supply_dir=tmp_path)
        assert ad.get_state().threshold_percent == 100
        res = ad.set_limit(80)
        assert res.ok and res.verified
        assert ad.get_state().threshold_percent == 80

    def test_privacy_indicator_detects_live_mic_camera_and_screenshare(self):
        fake_dump = json.dumps([
            {
                "type": "PipeWire:Interface:Node",
                "info": {
                    "state": "running",
                    "props": {"media.class": "Stream/Input/Audio", "application.name": "Zoom"},
                },
            },
            {
                "type": "PipeWire:Interface:Node",
                "info": {
                    "state": "running",
                    "props": {"media.class": "Stream/Input/Video", "application.name": "Firefox", "node.description": "Webcam"},
                },
            },
            {
                "type": "PipeWire:Interface:Node",
                "info": {
                    "state": "running",
                    "props": {"media.class": "Stream/Input/Video", "application.name": "xdg-desktop-portal", "node.description": "Mutter ScreenCast"},
                },
            },
        ])
        priv = PrivacyIndicatorAdapter(
            runner=lambda _c, _t: ExecResult(0, fake_dump, ""),
            which=lambda b: "/usr/bin/pw-dump" if b == "pw-dump" else None,
        )
        st = priv.get_state()
        assert st.status == CapabilityStatus.HEALTHY
        assert st.mic_active and st.camera_active and st.screen_share_active
        assert "mic:Zoom" in st.active_apps
        assert "camera:Firefox" in st.active_apps

    def test_pipewire_per_app_audio_routing(self):
        moved_calls: list[list[str]] = []

        def fake_run(cmd, _t):
            if cmd == ["pactl", "list", "short", "sinks"]:
                return ExecResult(0, "42\talsa_output.pci\tRUNNING\n99\tbluez_output.WH1000XM5\tIDLE\n", "")
            if cmd == ["pactl", "list", "sink-inputs"]:
                return ExecResult(0, 'Sink Input #107\n\tapplication.name = "Spotify"\n', "")
            if cmd[:2] == ["pactl", "move-sink-input"]:
                moved_calls.append(cmd)
                return ExecResult(0, "", "")
            return ExecResult(1, "", "err")

        router = PipeWireRoutingAdapter(runner=fake_run, which=lambda b: "/usr/bin/pactl" if b == "pactl" else None)
        res = router.route_app_audio("spotify", "bluez")
        assert res.ok and res.verified
        assert moved_calls == [["pactl", "move-sink-input", "107", "99"]]


class TestFocusModesAndAutoAppNap:
    def test_focus_mode_manager_persists_0600_and_applies_gnome_and_power(self, tmp_path: Path):
        calls: list[list[str]] = []

        def fake_run(cmd):
            calls.append(cmd)
            return command_runner.CommandResult(returncode=0, stdout="", stderr="", command=tuple(cmd))

        st_file = tmp_path / "focus.json"
        mgr = os_events.FocusModeManager(state_path=st_file, runner=fake_run, which=lambda b: f"/usr/bin/{b}")
        out = mgr.set_mode("coding")
        assert "Focus Mode set to 'coding'" in out
        assert mgr.get_current() == "coding"
        assert stat.S_IMODE(st_file.stat().st_mode) == 0o600
        assert any("show-banners" in c and "false" in c for c in calls)
        assert any("powerprofilesctl" in c and "performance" in c for c in calls)

    def test_auto_app_nap_policy_respects_protected_foreground_and_audio_assertions(self):
        pol = os_events.AutoAppNapPolicy(idle_threshold_sec=120.0)
        assert not pol.evaluate(app_name="gnome-shell", active_app="code", occluded_seconds=500, active_audio_apps=set()).should_nap
        assert not pol.evaluate(app_name="firefox", active_app="firefox", occluded_seconds=500, active_audio_apps=set()).should_nap
        assert not pol.evaluate(app_name="spotify", active_app="code", occluded_seconds=500, active_audio_apps={"Spotify"}).should_nap
        assert not pol.evaluate(app_name="gimp", active_app="code", occluded_seconds=60, active_audio_apps=set()).should_nap
        assert pol.evaluate(app_name="gimp", active_app="code", occluded_seconds=200, active_audio_apps=set()).should_nap

    def test_cgroup_v2_freezer_writes_and_verifies(self, tmp_path: Path):
        cg_dir = tmp_path / "app.scope"
        cg_dir.mkdir()
        ff = cg_dir / "cgroup.freeze"
        ff.write_text("0\n", encoding="utf-8")

        freezer = os_events.CgroupFreezer(cgroup_root=tmp_path)
        assert freezer.set_frozen(cg_dir, True) is True
        assert ff.read_text(encoding="utf-8").strip() == "1"
        assert freezer.set_frozen(cg_dir, False) is True
        assert ff.read_text(encoding="utf-8").strip() == "0"


class TestCoreSpotlightEngine:
    def test_safe_ast_calculator_blocks_code_execution_and_solves_math(self):
        assert spotlight_index.safe_calculate("sqrt(144) + 2^8") == "268"
        assert spotlight_index.safe_calculate("(15 * 4) / 3") == "20"
        # Injection attempts MUST return None (zero eval!)
        assert spotlight_index.safe_calculate("__import__('os').system('id')") is None
        assert spotlight_index.safe_calculate("open('/etc/passwd').read()") is None
        assert spotlight_index.safe_calculate("2 ** 100000") is None

    def test_unit_converter_and_fast_intent(self):
        assert "160.9" in (spotlight_index.convert_units("100 miles to km") or "")
        assert "98.6" in (spotlight_index.convert_units("37 C to F") or "")
        assert "8192" in (spotlight_index.convert_units("8 GB to MB") or "")
        fast = tools.handle_fast_intent("12 * 12")
        assert fast is not None and "144" in fast

    def test_private_fts5_spotlight_index_skips_secrets_and_searches_fast(self, tmp_path: Path):
        docs = tmp_path / "Documents"
        docs.mkdir()
        (docs / "Architecture_Notes.pdf").write_bytes(b"%PDF-1.4 demo")
        ssh = docs / ".ssh"
        ssh.mkdir()
        (ssh / "id_ed25519").write_text("SECRET", encoding="utf-8")

        db_p = tmp_path / "spot.db"
        idx = spotlight_index.SpotlightIndex(db_path=db_p)
        try:
            assert stat.S_IMODE(db_p.stat().st_mode) == 0o600
            n = idx.refresh(roots=[docs])
            assert n == 1
            hits = idx.search("Architecture")
            assert len(hits) == 1 and hits[0]["name"] == "Architecture_Notes.pdf"
            # Hidden .ssh directory MUST NOT be indexed
            assert idx.search("id_ed25519") == []
            out = spotlight_index.spotlight_query("wifi", index=idx)
            assert "gnome-control-center wifi" in out
        finally:
            idx.close()


class TestContinuityPrivacyAuditAndPortal:
    def test_tcc_privacy_auditor_flags_broad_flatpak_overrides_and_autostarts(self, tmp_path: Path):
        ov_dir = tmp_path / "overrides"
        ov_dir.mkdir()
        (ov_dir / "org.example.App").write_text("[Context]\nfilesystems=home;filesystem=host\n", encoding="utf-8")
        auto_dir = tmp_path / "autostart"
        auto_dir.mkdir()
        (auto_dir / "celina-island.desktop").write_text("[Desktop Entry]\nName=Celina\n", encoding="utf-8")

        report = continuity.audit_app_permissions(autostart_dir=auto_dir, flatpak_overrides_dir=ov_dir)
        assert "org.example.App" in report and "BROAD" in report
        assert "celina-island" in report

    def test_android_mirror_requires_approval(self):
        with mock.patch("celina_pkg.continuity.shutil.which", return_value="/usr/bin/scrcpy"):
            res = continuity.launch_android_mirror(approval_cb=None)
            assert res.startswith("Denied:")

    def test_universal_menu_palette_searches_atspi_elements(self):
        fake_els = [
            {"role": "menu item", "label": "Export as PDF", "x": 120, "y": 40},
            {"role": "menu item", "label": "Close Tab", "x": 120, "y": 80},
        ]
        with mock.patch.object(automation, "get_active_window_info", return_value={"app": "LibreOffice", "title": "Doc"}):
            with mock.patch.object(automation, "get_atspi_elements", return_value=fake_els):
                out = continuity.search_active_app_menus("PDF")
        assert "Export as PDF" in out and "Close Tab" not in out

    def test_portal_client_closes_cleanly_on_request_stop(self):
        client = portal_input.RemoteDesktopPortalClient(bus=mock.MagicMock())
        client._session_handle = "/org/freedesktop/portal/desktop/session/1_1/test"
        with mock.patch.object(portal_input, "get_portal_client", return_value=client):
            automation.request_stop()
        try:
            assert client.has_active_session is False
        finally:
            automation.clear_stop()

    def test_continuity_status_handles_nonzero_grdctl_without_attribute_error(self):
        def fake_run(cmd, timeout=3.0):
            if cmd and cmd[0] == "grdctl":
                return command_runner.CommandResult(returncode=1, stdout="", stderr="RDP disabled", command=tuple(cmd))
            return command_runner.CommandResult(returncode=0, stdout="", stderr="", command=tuple(cmd))

        with mock.patch("celina_pkg.continuity.shutil.which", side_effect=lambda b: f"/usr/bin/{b}" if b == "grdctl" else None):
            with mock.patch.object(command_runner, "run_command", side_effect=fake_run):
                out = continuity.get_continuity_status()
        assert "sidecar_gnome_rdp: installed (RDP disabled)" in out

    def test_privacy_indicator_uses_pwdump_node_and_falls_back_cleanly(self):
        seen_cmds: list[list[str]] = []

        def fake_run(cmd, _t):
            seen_cmds.append(list(cmd))
            if cmd == ["pw-dump", "-N", "Node"]:
                return ExecResult(1, "", "unknown arg")
            return ExecResult(0, "[]", "")

        priv = PrivacyIndicatorAdapter(runner=fake_run, which=lambda b: "/usr/bin/pw-dump" if b == "pw-dump" else None)
        st = priv.get_state()
        assert st.status == CapabilityStatus.HEALTHY
        assert seen_cmds == [["pw-dump", "-N", "Node"], ["pw-dump", "-N"]]

    def test_wayland_low_level_helpers_enforce_uinput_opt_in_and_linear_portal_scroll(self):
        # 1. With Portal unavailable & uinput NOT opted-in, ALL 4 helpers must refuse ydotool
        fake_portal_off = mock.MagicMock()
        fake_portal_off.is_available.return_value = False
        with mock.patch.object(automation, "_is_wayland", return_value=True), \
             mock.patch.object(automation, "is_experimental_uinput_enabled", return_value=False), \
             mock.patch.object(portal_input, "get_portal_client", return_value=fake_portal_off):
            assert "disabled by default" in automation._exec_scroll("down", 5)
            assert "disabled by default" in automation._exec_drag(10, 10, 50, 50)
            assert "disabled by default" in automation._exec_hotkey("ctrl+c")
            assert "disabled by default" in automation._exec_type("hello")

        # 2. With Portal available, scroll uses LINEAR Wayland sign (+dy = down, -dy = up) & passes x, y
        scroll_calls: list[tuple] = []
        fake_portal_on = mock.MagicMock()
        fake_portal_on.is_available.return_value = True

        def fake_scroll(dx, dy, *, x=None, y=None):
            scroll_calls.append((dx, dy, x, y))
            return portal_input.PortalActionResult(True, "ok", True)

        fake_portal_on.scroll.side_effect = fake_scroll
        with mock.patch.object(automation, "_is_wayland", return_value=True), \
             mock.patch.object(portal_input, "get_portal_client", return_value=fake_portal_on):
            r_down = automation._exec_scroll("down", 5, x=300, y=400)
            r_up = automation._exec_scroll("up", 5, x=300, y=400)
        assert "GNOME RemoteDesktop Portal" in r_down
        assert "GNOME RemoteDesktop Portal" in r_up
        assert scroll_calls == [(0.0, 75.0, 300, 400), (0.0, -75.0, 300, 400)]

    def test_is_own_window_filters_cross_pid_dynamic_island(self):
        fake_app = mock.MagicMock()
        fake_app.get_process_id.return_value = 999999  # Different PID
        fake_app.get_name.return_value = "celina-island"
        assert automation._is_own_window(fake_app) is True

    def test_mcp_stdio_does_not_leak_ambient_api_keys(self, monkeypatch):
        from celina_pkg import mcp_client

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-super-secret")
        captured_env: dict[str, str] = {}

        def fake_popen(_cmd, **kwargs):
            captured_env.update(kwargs.get("env") or {})
            raise OSError("stop-after-env-check")

        proc = mcp_client._StdioMCPProcess("demo", ["python3", "-V"])
        with mock.patch("celina_pkg.mcp_client.shutil.which", return_value="/usr/bin/python3"), \
             mock.patch("celina_pkg.mcp_client.subprocess.Popen", side_effect=fake_popen):
            proc._ensure_started()
        assert "OPENROUTER_API_KEY" not in captured_env


class TestIOSAncsBridge:
    def test_official_apple_ancs_uuids_and_packet_codec(self):
        import struct

        # Official developer.apple.com ANCS v1.0 UUIDs
        assert ios_ancs.ANCS_SERVICE_UUID == "7905f431-b5ce-4e99-a40f-4b1e122d00d0"
        assert ios_ancs.ANCS_NOTIFICATION_SOURCE_UUID == "9fbf120d-6301-42d9-8c58-25e699a21dbd"
        assert ios_ancs.ANCS_CONTROL_POINT_UUID == "69d1d8f3-45e1-49a8-9821-9bbdfdaad9d9"
        assert ios_ancs.ANCS_DATA_SOURCE_UUID == "22eac6e9-24d6-4bb5-be44-b36ace7c7bfb"

        # 8-byte Notification Source packet: Added(0), Important(2), IncomingCall(1), count=1, uid=0x11223344
        raw = struct.pack("<BBBBI", 0, 0x02, 1, 1, 0x11223344)
        ev = ios_ancs.parse_notification_source(raw)
        assert ev is not None
        assert ev.is_added and not ev.is_pre_existing
        assert ev.category_name == "Incoming Call"
        assert ev.notification_uid == 0x11223344

        cmd, attrs = ios_ancs.build_get_notification_attributes_cmd(0x11223344)
        assert cmd[:5] == struct.pack("<BI", 0, 0x11223344)
        assert attrs == (0, 1, 2, 3)

        act_cmd = ios_ancs.build_perform_action_cmd(0x11223344, positive=True)
        assert act_cmd == struct.pack("<BIB", 2, 0x11223344, 0)

    def test_ancs_mtu_reassembly_preexisting_filter_and_otp_redaction(self):
        import struct

        received: list[tuple[str, str, str]] = []
        written_cp: list[bytes] = []
        bridge = ios_ancs.IOSAncsBridge(
            on_notification=lambda app, title, body: received.append((app, title, body)),
            desktop_notify=False,
        )

        # 1. Pre-existing historical notification must NOT trigger Control Point fetch or banner spam
        old_pkt = struct.pack("<BBBBI", 0, int(ios_ancs.ANCSEventFlag.PRE_EXISTING), 4, 1, 99)
        bridge.handle_notification_source_bytes(old_pkt, write_control_point=written_cp.append)
        assert written_cp == [] and received == []

        # 2. Live notification triggers Control Point write & reassembles fragmented BLE Data Source chunks
        live_pkt = struct.pack("<BBBBI", 0, 0, 4, 1, 1001)
        bridge.handle_notification_source_bytes(live_pkt, write_control_point=written_cp.append)
        assert len(written_cp) == 1

        def tlv(aid: int, txt: str) -> bytes:
            b = txt.encode("utf-8")
            return struct.pack("<BH", aid, len(b)) + b

        full_ds = (
            struct.pack("<BI", 0, 1001)
            + tlv(0, "com.apple.MobileSMS")
            + tlv(1, "Bank Alert")
            + tlv(2, "")
            + tlv(3, "Your verification code is 482910. Do not share.")
        )
        # Split into 20-byte BLE MTU fragments
        for i in range(0, len(full_ds), 20):
            bridge.handle_data_source_bytes(full_ds[i : i + 20])

        assert len(received) == 1
        app_id, title, body = received[0]
        assert app_id == "com.apple.MobileSMS"
        assert title == "Bank Alert"
        # 2FA OTP MUST be redacted!
        assert "482910" not in body and "verification code hidden" in body

    def test_bluez_discovery_ignores_headphones_and_finds_ancs_iphone(self):
        managed = {
            "/org/bluez/hci0/dev_AA_BB": {
                "org.bluez.Device1": {"Alias": "Sony WH-1000XM5", "Connected": True},
            },
            "/org/bluez/hci0/dev_11_22": {
                "org.bluez.Device1": {"Alias": "Alex's iPhone", "Connected": True},
            },
            "/org/bluez/hci0/dev_11_22/service0010": {
                "org.bluez.GattService1": {
                    "UUID": ios_ancs.ANCS_SERVICE_UUID,
                    "Device": "/org/bluez/hci0/dev_11_22",
                },
            },
            "/org/bluez/hci0/dev_11_22/service0010/char0011": {
                "org.bluez.GattCharacteristic1": {
                    "UUID": ios_ancs.ANCS_NOTIFICATION_SOURCE_UUID,
                    "Service": "/org/bluez/hci0/dev_11_22/service0010",
                },
            },
            "/org/bluez/hci0/dev_11_22/service0010/char0012": {
                "org.bluez.GattCharacteristic1": {
                    "UUID": ios_ancs.ANCS_CONTROL_POINT_UUID,
                    "Service": "/org/bluez/hci0/dev_11_22/service0010",
                },
            },
            "/org/bluez/hci0/dev_11_22/service0010/char0013": {
                "org.bluez.GattCharacteristic1": {
                    "UUID": ios_ancs.ANCS_DATA_SOURCE_UUID,
                    "Service": "/org/bluez/hci0/dev_11_22/service0010",
                },
            },
        }
        eps = ios_ancs.discover_ancs_endpoints(managed)
        assert len(eps) == 1
        assert eps[0].device_name == "Alex's iPhone"
        assert eps[0].control_point_path.endswith("char0012")
        assert eps[0].data_source_path.endswith("char0013")


class TestNewPinsAndStagesDryRun:
    def test_new_oss_pins_are_exact_40_hex_commits_in_lockfile(self):
        repo_root = Path(__file__).resolve().parents[1]
        lock_text = (repo_root / "system" / "download-locks.sha256").read_text(encoding="utf-8")
        for key in ("uxplay", "lan-mouse", "deskflow", "scrcpy"):
            lines = [ln for ln in lock_text.splitlines() if ln.startswith(f"{key}|")]
            assert len(lines) == 1, f"Missing lockfile entry for {key}"
            parts = lines[0].split("|")
            assert parts[1] == "git"
            assert len(parts[3]) == 40 and all(c in "0123456789abcdef" for c in parts[3])

    def test_stage_55_and_new_stages_zero_side_effect_dry_run(self, tmp_path: Path):
        import os
        import subprocess

        repo_root = Path(__file__).resolve().parents[1]
        fake_home = tmp_path / "clean_home"
        fake_home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(fake_home)
        env["XDG_CONFIG_HOME"] = str(fake_home / ".config")
        env["XDG_DATA_HOME"] = str(fake_home / ".local" / "share")

        res = subprocess.run(
            ["bash", str(repo_root / "install.sh"), "--dry-run", "--only", "40,50,55,99"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert res.returncode == 0, f"Dry-run failed:\n{res.stdout}\n{res.stderr}"
        assert "[40] All-System Continuity Hub" in res.stdout
        assert "[50] Power Management" in res.stdout
        assert "[55] Hardware Security, TPM 2.0 LUKS Advisor & Capability-Gated Biometrics" in res.stdout
        assert list(fake_home.iterdir()) == [], f"Dry-run created files in HOME: {list(fake_home.iterdir())}"

