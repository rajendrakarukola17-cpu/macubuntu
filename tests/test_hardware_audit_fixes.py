"""
Regression tests for the celina_hardware P0/P1 audit fixes.

Covers:
  1. T3: v4l2loopback virtual camera hard-rejected; physical IR camera accepted
  2. Audio routing: pactl numeric Sink: ID matching; empty app_query rejected
  3. Mojibake: correct UTF-8 emoji (🟠 mic / 🟢 camera / 🟣 screen), no private-use chars
  4. GPU selection: Intel 8086:1586 is NOT Strix Halo; hybrid Intel+NVIDIA picks NVIDIA
  5. Endpoint sanitizer: localhost gets default port; :: → loopback default; non-loopback → default
  6. systemd unit injection: unit starting with '-' is rejected
"""
import inspect
import os
from pathlib import Path

from celina_hardware.adapters import (
    ExecResult,
    HardwareManager,
    PipeWireRoutingAdapter,
    PrivacyIndicatorAdapter,
    SystemdUserAdapter,
)
from celina_hardware.biometrics import AuthPolicyEngine, IRFaceAdapter
from celina_hardware.compute import DEFAULT_OLLAMA_ENDPOINT, _sanitize_local_endpoint
from celina_hardware.detect_gpu import select_compute_backend


# ---------------------------------------------------------------------------
# 1. T3 Regression: v4l2loopback virtual camera rejection
# ---------------------------------------------------------------------------

def test_t3_v4l2loopback_virtual_camera_rejected(tmp_path: Path):
    """v4l2loopback virtual camera named 'IR Camera' must be hard-rejected (Threat T3)."""
    vdir = tmp_path / "video4linux"
    v0 = vdir / "video0"
    v0.mkdir(parents=True)
    (v0 / "name").write_text("IR Camera\n", encoding="utf-8")
    dev_dir = v0 / "device"
    dev_dir.mkdir(parents=True)
    os.symlink("/sys/module/v4l2loopback", str(dev_dir / "driver"))

    ir = IRFaceAdapter(which=lambda _b: None)
    cap = ir.probe(sys_video_dir=vdir)
    assert cap["ir_camera_detected"] is False, f"v4l2loopback must be rejected, got: {cap}"
    assert "ir_devices" not in cap or len(cap.get("ir_devices", [])) == 0


def test_t3_physical_ir_camera_accepted(tmp_path: Path):
    """A physical IR camera with uvcvideo driver must be accepted."""
    vdir = tmp_path / "video4linux"
    v0 = vdir / "video0"
    v0.mkdir(parents=True)
    (v0 / "name").write_text("Integrated IR Camera\n", encoding="utf-8")
    dev_dir = v0 / "device"
    dev_dir.mkdir(parents=True)
    os.symlink("/sys/module/uvcvideo", str(dev_dir / "driver"))

    ir = IRFaceAdapter(which=lambda _b: None)
    cap = ir.probe(sys_video_dir=vdir)
    assert cap["ir_camera_detected"] is True, f"Physical IR camera must be accepted, got: {cap}"
    assert len(cap.get("ir_devices", [])) == 1


def test_t3_rgb_webcam_without_driver_is_rgb_not_ir(tmp_path: Path):
    """An RGB webcam with no driver symlink is RGB (not IR) — not skipped entirely."""
    vdir = tmp_path / "video4linux"
    v0 = vdir / "video0"
    v0.mkdir(parents=True)
    (v0 / "name").write_text("Integrated RGB Webcam\n", encoding="utf-8")
    # No device/driver symlink — like real v4l2loopback on Linux, but named as RGB

    ir = IRFaceAdapter(which=lambda _b: None)
    cap = ir.probe(sys_video_dir=vdir)
    assert cap["ir_camera_detected"] is False
    assert cap["rgb_webcam_only"] is True
    assert cap["uploads_frames"] is False


# ---------------------------------------------------------------------------
# 2. Audio Routing Regression: pactl numeric Sink: ID matching
# ---------------------------------------------------------------------------

def test_audio_routing_matches_numeric_sink_id():
    """pactl 'Sink:' outputs numeric ID; route_app_audio must accept ID or name."""
    def fake_run(cmd, _timeout):
        if cmd == ["pactl", "list", "short", "sinks"]:
            return ExecResult(0, "42\talsa_output.pci-0000_00_1f.3.analog-stereo\trunning\n", "")
        if cmd == ["pactl", "list", "sink-inputs"]:
            return ExecResult(0, "Sink Input #123\n\tapplication.name = \"Spotify\"\n\tSink: 42\n", "")
        if cmd[:4] == ["pactl", "move-sink-input", "123", "42"]:
            return ExecResult(0, "", "")
        return ExecResult(0, "", "")

    router = PipeWireRoutingAdapter(
        runner=fake_run, which=lambda b: "/usr/bin/pactl" if b == "pactl" else None
    )
    res = router.route_app_audio("spotify", "alsa_output.pci-0000_00_1f.3.analog-stereo")
    assert res.ok is True, f"route_app_audio should succeed, got: {res.message}"


def test_audio_routing_empty_app_query_rejected():
    """Empty app_query must be rejected with returncode=2."""
    router = PipeWireRoutingAdapter(
        runner=lambda c, _t: ExecResult(0, "", ""),
        which=lambda b: "/usr/bin/pactl" if b == "pactl" else None,
    )
    res = router.route_app_audio("", "alsa_output.test")
    assert res.ok is False
    assert res.returncode == 2


# ---------------------------------------------------------------------------
# 3. Mojibake Regression: correct UTF-8 emoji, no private-use chars
# ---------------------------------------------------------------------------

def test_privacy_indicator_correct_emoji_no_mojibake():
    """PrivacyIndicatorAdapter get_state source must contain real emoji, not tofu/private-use."""
    src = inspect.getsource(PrivacyIndicatorAdapter.get_state)
    assert "\\ue05e" not in src
    assert "\ue05e" not in src
    assert "🟠 mic" in src
    assert "🟢 camera" in src
    assert "🟣 screen" in src


# ---------------------------------------------------------------------------
# 4. GPU Selection Regression: Strix Halo anchor + hybrid Intel+NVIDIA
# ---------------------------------------------------------------------------

def test_gpu_intel_1586_is_not_strix_halo():
    """Intel GPU with subsystem ID 1586 must NOT be classified as AMD Strix Halo/ROCm."""
    lspci_text = (
        "00:02.0 VGA compatible controller [8086:1586] (rev 02)\n"
        "    Subsystem: [8086:1586]\n"
    )
    decision = select_compute_backend(lspci_text=lspci_text)
    assert decision.backend == "vulkan", f"Intel 8086:1586 should be Vulkan, got {decision.backend}"
    assert decision.gpu_vendor == "intel", f"Intel 8086:1586 vendor should be intel, got {decision.gpu_vendor}"
    assert decision.backend != "rocm", "Intel 1586 must not be ROCm/Strix Halo"


def test_gpu_hybrid_intel_nvidia_picks_nvidia():
    """Hybrid Intel+NVIDIA laptop: NVIDIA wins for backend, NVIDIA model reported."""
    lspci_text = (
        "00:02.0 VGA compatible controller: Intel Corporation Tiger Lake-LP GT2 [Iris Xe Graphics]\n"
        "01:00.0 3D controller: NVIDIA Corporation GA107M [GeForce RTX 3050 Mobile] (rev a1)\n"
    )
    decision = select_compute_backend(lspci_text=lspci_text)
    assert decision.backend == "cuda", f"Hybrid Intel+NVIDIA should use CUDA, got {decision.backend}"
    assert decision.gpu_vendor == "nvidia", f"Hybrid vendor should be nvidia, got {decision.gpu_vendor}"
    assert "NVIDIA" in decision.gpu_model or "GeForce" in decision.gpu_model, \
        f"Hybrid model should mention NVIDIA/GeForce, got {decision.gpu_model}"


# ---------------------------------------------------------------------------
# 5. Endpoint Sanitizer Regression
# ---------------------------------------------------------------------------

def test_endpoint_sanitizer_preserves_default_port():
    """localhost without port → default port from default_url."""
    result = _sanitize_local_endpoint("localhost", DEFAULT_OLLAMA_ENDPOINT)
    assert result == "http://localhost:11434", f"Expected http://localhost:11434, got {result}"


def test_endpoint_sanitizer_bare_ipv6_wildcard():
    """Bare :: → default loopback endpoint."""
    result = _sanitize_local_endpoint("::", DEFAULT_OLLAMA_ENDPOINT)
    assert result == DEFAULT_OLLAMA_ENDPOINT, f"Expected {DEFAULT_OLLAMA_ENDPOINT}, got {result}"


def test_endpoint_sanitizer_rejects_non_loopback():
    """Non-loopback host (metadata service) → default_url (anti-SSRF)."""
    result = _sanitize_local_endpoint("http://169.254.169.254:11434", DEFAULT_OLLAMA_ENDPOINT)
    assert result == DEFAULT_OLLAMA_ENDPOINT, f"Expected {DEFAULT_OLLAMA_ENDPOINT}, got {result}"


# ---------------------------------------------------------------------------
# 6. Systemd Unit Injection
# ---------------------------------------------------------------------------

def test_systemd_unit_injection_rejected():
    """Unit name starting with '-' must be rejected (option injection)."""
    sd = SystemdUserAdapter(
        runner=lambda c, _t: ExecResult(0, "", ""),
        which=lambda b: "/usr/bin/systemctl" if b == "systemctl" else None,
    )
    res = sd.control_unit("--all", "stop")
    assert res.ok is False
    assert "Invalid unit name" in res.message
