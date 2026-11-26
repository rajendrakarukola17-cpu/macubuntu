"""
Phase 8 Hardware Capability Layer & Biometrics Test Suite:
  - UPower, PipeWire (read-after-write verification), NetworkManager, BlueZ, Portals, systemd user
  - Capability-gated Fingerprint (fprintd/PAM, no template storage, password fallback retained)
  - Optional IR Face Unlock guardrails (rejects standard RGB webcams, never uploads frames)
  - FIDO2 high-assurance authentication & AuthPolicyEngine enforcement
"""

import os
from pathlib import Path

from celina_hardware.adapters import (
    BlueZAdapter,
    ExecResult,
    GnomePortalAdapter,
    HardwareManager,
    NetworkManagerAdapter,
    PipeWireAdapter,
    SystemdUserAdapter,
    UPowerAdapter,
)
from celina_hardware.biometrics import AuthPolicyEngine, FIDO2Adapter, FingerprintAdapter, IRFaceAdapter
from celina_hardware.models import CapabilityStatus


class TestHardwareAdapters:
    def test_pipewire_volume_read_after_write_verification_and_mismatch_detection(self):
        vol = {"pct": 0.40, "muted": False}

        def fake_run(cmd, _timeout):
            if cmd[:2] == ["wpctl", "get-volume"]:
                m = " [MUTED]" if vol["muted"] else ""
                return ExecResult(0, f"Volume: {vol['pct']:.2f}{m}", "")
            if cmd[:2] == ["wpctl", "set-volume"]:
                vol["pct"] = int(cmd[-1].rstrip("%")) / 100.0
                return ExecResult(0, "", "")
            if cmd[:2] == ["wpctl", "set-mute"]:
                vol["muted"] = cmd[-1] == "1"
                return ExecResult(0, "", "")
            return ExecResult(1, "", "unexpected")

        audio = PipeWireAdapter(runner=fake_run, which=lambda b: f"/usr/bin/{b}" if b == "wpctl" else None)
        res = audio.set_volume(level=65, mute=False)
        assert res.ok and res.verified and res.observed.get("volume_percent") == 65

        # Silent hardware drift (command exits 0 but sink stays at 20%) MUST fail verification!
        def drift_run(cmd, _timeout):
            if cmd[:2] == ["wpctl", "get-volume"]:
                return ExecResult(0, "Volume: 0.20", "")
            return ExecResult(0, "", "")

        drift_audio = PipeWireAdapter(runner=drift_run, which=lambda b: f"/usr/bin/{b}" if b == "wpctl" else None)
        bad = drift_audio.set_volume(level=80)
        assert not bad.ok and not bad.verified

    def test_wifi_and_bluetooth_read_after_write_verification(self):
        wifi_on = {"state": "disabled"}

        def nm_run(cmd, _timeout):
            if cmd[:3] == ["nmcli", "radio", "wifi"] and len(cmd) == 4:
                wifi_on["state"] = "enabled" if cmd[3] == "on" else "disabled"
                return ExecResult(0, "", "")
            if cmd[:3] == ["nmcli", "radio", "wifi"]:
                return ExecResult(0, wifi_on["state"], "")
            return ExecResult(0, "yes:HomeWiFi\n", "")

        nm = NetworkManagerAdapter(runner=nm_run, which=lambda b: "/usr/bin/nmcli" if b == "nmcli" else None)
        res = nm.set_wifi(True)
        assert res.ok and res.verified and res.observed == {"wifi_enabled": True}

        # Bluetooth verification mismatch detection (command exits 0, controller stays Powered: no)
        def bt_drift(cmd, _timeout):
            if cmd == ["bluetoothctl", "show"]:
                return ExecResult(0, "Controller AA:BB:CC:DD:EE:FF\n\tPowered: no\n", "")
            return ExecResult(0, "", "")

        bt = BlueZAdapter(runner=bt_drift, which=lambda b: "/usr/bin/bluetoothctl" if b == "bluetoothctl" else None)
        bad = bt.set_power(True)
        assert not bad.ok and not bad.verified

    def test_brightness_read_after_write_verification(self):
        hw = HardwareManager(
            runner=lambda cmd, _t: (
                ExecResult(0, "70", "") if cmd == ["brightnessctl", "get"]
                else ExecResult(0, "100", "") if cmd == ["brightnessctl", "max"]
                else ExecResult(0, "", "")
            ),
            which=lambda b: "/usr/bin/brightnessctl" if b == "brightnessctl" else None,
        )
        ok = hw.set_brightness(70)
        assert ok.ok and ok.verified

        mismatch = hw.set_brightness(20)
        assert not mismatch.ok and not mismatch.verified

    def test_upower_portals_and_systemd_user_adapters(self):
        up = UPowerAdapter()
        st = up.get_state()
        assert isinstance(st.on_battery, bool)

        pt = GnomePortalAdapter()
        pst = pt.get_state()
        assert isinstance(pst.remote_desktop_portal, bool)

        sd = SystemdUserAdapter(
            runner=lambda cmd, _t: ExecResult(0, "active", ""),
            which=lambda b: "/usr/bin/systemctl" if b == "systemctl" else None,
        )
        s_state = sd.get_unit_state("pipewire.service")
        assert s_state.status == CapabilityStatus.HEALTHY


class TestBiometricGuardrailsAndAuthPolicy:
    def test_fingerprint_adapter_never_stores_templates_and_keeps_password_fallback(self, tmp_path: Path):
        pam_dir = tmp_path / "pam.d"
        pam_dir.mkdir()
        (pam_dir / "common-auth").write_text("auth required pam_unix.so\n", encoding="utf-8")

        fp = FingerprintAdapter(
            runner=lambda cmd, _t: (
                ExecResult(0, "Bus 001 Device 003: ID 27c6:538d Goodix Fingerprint", "")
                if cmd == ["lsusb"]
                else ExecResult(0, "Fingerprints for user alice:\n - #0: right-index-finger\n", "")
            ),
            which=lambda b: f"/usr/bin/{b}",
        )
        cap = fp.probe("alice", pam_dir=pam_dir)
        assert cap["status"] == CapabilityStatus.HEALTHY.value
        assert cap["enrolled"] is True
        assert cap["stores_templates_in_celina"] is False
        assert cap["password_fallback_enabled"] is True

    def test_ir_face_unlock_blocks_standard_rgb_webcam_and_never_uploads(self, tmp_path: Path):
        vdir = tmp_path / "video4linux"
        v0 = vdir / "video0"
        v0.mkdir(parents=True)
        (v0 / "name").write_text("Integrated RGB Webcam\n", encoding="utf-8")

        ir = IRFaceAdapter(which=lambda _b: None)
        cap = ir.probe(sys_video_dir=vdir)
        assert cap["status"] == CapabilityStatus.UNSUPPORTED.value
        assert cap["ir_camera_detected"] is False
        assert cap["rgb_webcam_only"] is True
        assert cap["uploads_frames"] is False

    def test_ir_face_rejects_v4l2loopback_virtual_camera(self, tmp_path: Path):
        """T3: v4l2loopback virtual camera named 'IR Camera' must be hard-rejected."""
        vdir = tmp_path / "video4linux"
        v0 = vdir / "video0"
        v0.mkdir(parents=True)
        (v0 / "name").write_text("IR Camera\n", encoding="utf-8")
        # Simulate v4l2loopback driver via /sys/class/video4linux/video0/device/driver
        dev_dir = v0 / "device"
        dev_dir.mkdir(parents=True)
        # v4l2loopback driver symlink (name of the target dir is the driver name)
        os.symlink(f"/sys/module/v4l2loopback", str(dev_dir / "driver"))

        ir = IRFaceAdapter(which=lambda _b: None)
        cap = ir.probe(sys_video_dir=vdir)
        assert cap["ir_camera_detected"] is False, \
            f"v4l2loopback virtual camera must be rejected, got: {cap}"
        assert "ir_devices" not in cap or len(cap.get("ir_devices", [])) == 0, \
            f"v4l2loopback must not appear in ir_devices, got: {cap.get('ir_devices')}"

    def test_fido2_adapter_probes_tokens(self):
        fido = FIDO2Adapter(
            runner=lambda cmd, _t: ExecResult(0, "/dev/hidraw2: vendor=0x1050 product=0x0407 (YubiKey)", ""),
            which=lambda b: "/usr/bin/fido2-token" if b == "fido2-token" else None,
        )
        cap = fido.probe()
        assert cap["status"] == CapabilityStatus.HEALTHY.value
        assert cap["tokens_present"] is True

    def test_auth_policy_never_allows_face_or_bluetooth_for_high_assurance_or_recovery(self):
        policy = AuthPolicyEngine()

        # Bluetooth proximity NEVER allowed for ANY operation!
        for op in ("screen_unlock", "key_export", "payment", "luks_recovery"):
            dec = policy.evaluate(op, "bluetooth_proximity")
            assert not dec.allowed
            assert "never accepted" in dec.reason.lower()

        # High-assurance ops: face & fingerprint refused; FIDO2 & password allowed!
        for op in ("key_export", "payment", "account_change", "approve_quarantined_skill"):
            assert not policy.evaluate(op, "ir_face", ir_camera_verified=True).allowed
            assert not policy.evaluate(op, "fingerprint").allowed
            assert policy.evaluate(op, "fido2").allowed
            assert policy.evaluate(op, "password").allowed

        # Recovery / boot / policy changes: password ONLY!
        for op in ("luks_recovery", "boot_unlock", "security_policy_change"):
            assert not policy.evaluate(op, "fido2").allowed
            assert not policy.evaluate(op, "fingerprint").allowed
            assert not policy.evaluate(op, "ir_face", ir_camera_verified=True).allowed
            assert policy.evaluate(op, "password").allowed

        # Local convenience unlock: IR camera required for face; password fallback required for biometrics!
        assert not policy.evaluate("screen_unlock", "rgb_face").allowed
        assert not policy.evaluate("screen_unlock", "ir_face", ir_camera_verified=False).allowed
        assert not policy.evaluate("screen_unlock", "fingerprint", password_fallback_configured=False).allowed
        assert policy.evaluate("screen_unlock", "ir_face", ir_camera_verified=True, password_fallback_configured=True).allowed
        assert policy.evaluate("screen_unlock", "fingerprint", password_fallback_configured=True).allowed
