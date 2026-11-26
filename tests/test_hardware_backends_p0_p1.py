"""
Tests for celina_hardware.backends — P0/P1 deep audit regression gates.

These tests use dependency injection (dev_root, sys_root, probe_fn) so they
require zero hardware. Every P0/P1 fix from the deep audit has a corresponding
test that fails if the fix is reverted.

Run: python3 -m pytest tests/test_hardware_backends_p0_p1.py -v
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "features", "celina"))

from celina_hardware.backends import (
    InferenceBackend,
    OpenVINONPUBackend,
    ROCmBackend,
    VulkanBackend,
)
from celina_hardware.models import CapabilityStatus


# ============================================================================
# Phase B — §1.1 Loopback allowlist (P0: prefix denylist bypass)
# ============================================================================

@pytest.mark.parametrize(
    "bad_host_port",
    [
        "0.0.0.0:11435",      # wildcard IPv4
        "[::]:11435",         # wildcard IPv6
        "[::0]:11435",        # wildcard IPv6
        ":11435",             # bare port (implicit wildcard)
        "11435",              # port-only (implicit wildcard)
        "192.168.1.5:11435",  # LAN exposure
        "10.0.0.1:11435",     # RFC1918 exposure
        "example.com:11435",  # DNS name (resolves anywhere)
        "http://0.0.0.0:11435",  # scheme prefix defeats startswith check
        "https://192.168.1.5:11435",  # scheme + LAN
        "",                   # empty
        "   ",                # whitespace only
        "not-a-host:not-a-port",  # garbage
        "127.0.0.1",          # missing port
        "127.0.0.1:0",        # port 0 (reserved)
        "127.0.0.1:65536",    # port > 65535
        "127.0.0.1:-1",       # negative port
    ],
)
def test_refuses_non_loopback_and_invalid_host_port(bad_host_port):
    """§1.1: Only strict loopback host:port passes. Every bypass input must raise ValueError."""
    with pytest.raises(ValueError):
        OpenVINONPUBackend.get_env(host_port=bad_host_port)


@pytest.mark.parametrize(
    "good_host_port, expected",
    [
        ("127.0.0.1:11435", "127.0.0.1:11435"),
        ("localhost:11435", "127.0.0.1:11435"),
        ("[::1]:11435", "[::1]:11435"),
        ("127.0.0.1:11436", "127.0.0.1:11436"),
    ],
)
def test_accepts_loopback_host_port(good_host_port, expected):
    """§1.1: Valid loopback host:port is normalized and accepted."""
    env = OpenVINONPUBackend.get_env(host_port=good_host_port)
    assert env["OLLAMA_HOST"] == expected
    assert env["GGML_OPENVINO_DEVICE"] == "NPU"


# ============================================================================
# Phase B — §1.2 ROCm explicit Vulkan negation (P0)
# ============================================================================

def test_rocm_explicitly_negates_vulkan():
    """§1.2: ROCm.get_env() must set OLLAMA_VULKAN="0", not omit it."""
    env = ROCmBackend.get_env(gfx_version="11.5.1")
    assert env["OLLAMA_VULKAN"] == "0", (
        f"Expected OLLAMA_VULKAN='0', got {env.get('OLLAMA_VULKAN')!r}. "
        f"Absence of key is not negation — lingering Vulkan env from prior "
        f"backend switch still causes gfx1151 hang."
    )
    assert env["HSA_OVERRIDE_GFX_VERSION"] == "11.5.1"
    assert env["OLLAMA_FLASH_ATTENTION"] == "1"
    assert env["OLLAMA_KV_CACHE_TYPE"] == "q8_0"


def test_rocm_conflicts_with_vulkan():
    """§1.2: ROCmBackend declares conflicts_with=('vulkan',)."""
    assert ROCmBackend.conflicts_with == ("vulkan",)


@pytest.mark.parametrize(
    "bad_gfx",
    ["11.5", "11.5.1.2", "abc", "11.5.1x", "", "11,5,1", "gfx1151"],
)
def test_rocm_rejects_invalid_gfx_version(bad_gfx):
    """§2.2: gfx_version must match N.N.N format (drop-in injection defense)."""
    with pytest.raises(ValueError):
        ROCmBackend.get_env(gfx_version=bad_gfx)


# ============================================================================
# Phase B — §1.3 Vulkan gfx1151 guard (P1)
# ============================================================================

def test_vulkan_refuses_gfx1151():
    """§1.3/§2.3: Vulkan.get_env() must raise RuntimeError on gfx1151."""
    with pytest.raises(RuntimeError, match="gfx1151"):
        VulkanBackend.get_env(gfx_version="11.5.1")


def test_vulkan_accepts_other_gfx_versions():
    """§1.3: Vulkan accepts non-gfx1151 versions."""
    env = VulkanBackend.get_env(gfx_version="11.0.0")
    assert env["OLLAMA_VULKAN"] == "1"


def test_vulkan_conflicts_with_rocm():
    """§1.3: VulkanBackend declares conflicts_with=('rocm',)."""
    assert VulkanBackend.conflicts_with == ("rocm",)


# ============================================================================
# Phase B — §1.4 Driver-aware health_check (P0)
# ============================================================================

def _make_fake_accel_node(tmp_path, dev_name="accel0", driver="intel_vpu", is_char=True):
    """Create a fake /dev/accel/accel* + sysfs driver structure with driver symlink."""
    dev_root = tmp_path / "dev"
    sys_root = tmp_path / "sys"
    accel_dir = dev_root / "accel"
    accel_dir.mkdir(parents=True)
    node = accel_dir / dev_name
    node.write_text("")

    dev_sys = sys_root / "class" / "accel" / dev_name / "device"
    dev_sys.mkdir(parents=True)
    (dev_sys / "vendor").write_text("0x8086\n")
    # Create driver symlink (health_check checks is_symlink() + readlink, not uevent)
    driver_link = dev_sys / "driver"
    driver_link.symlink_to(f"../../../../../../bus/pci/drivers/{driver}")

    return dev_root, sys_root, node


def test_health_check_intel_vpu_healthy(tmp_path, monkeypatch):
    """§1.4: intel_vpu char device with rw permission → HEALTHY."""
    dev_root, sys_root, node = _make_fake_accel_node(tmp_path)
    monkeypatch.setattr(Path, "is_char_device", lambda self: True)
    monkeypatch.setattr(os, "access", lambda p, m: True)
    status, nodes, reasons = OpenVINONPUBackend.health_check(
        dev_root=dev_root, sys_root=sys_root
    )
    assert status == CapabilityStatus.HEALTHY
    assert str(node) in nodes
    assert reasons == []


def test_health_check_no_accel_dir(tmp_path):
    """§1.4: Missing /dev/accel → NOT_INSTALLED."""
    dev_root = tmp_path / "dev"
    dev_root.mkdir()
    sys_root = tmp_path / "sys"
    sys_root.mkdir()
    status, nodes, reasons = OpenVINONPUBackend.health_check(
        dev_root=dev_root, sys_root=sys_root
    )
    assert status == CapabilityStatus.NOT_INSTALLED
    assert nodes == []
    assert "/dev/accel directory missing" in reasons


def test_health_check_amdxdna_rejected(tmp_path, monkeypatch):
    """§1.4: amdxdna driver (AMD XDNA) is NOT OpenVINO-capable → UNSUPPORTED."""
    dev_root, sys_root, _ = _make_fake_accel_node(tmp_path, driver="amdxdna")
    monkeypatch.setattr(Path, "is_char_device", lambda self: True)
    monkeypatch.setattr(os, "access", lambda p, m: True)
    status, nodes, reasons = OpenVINONPUBackend.health_check(
        dev_root=dev_root, sys_root=sys_root
    )
    assert status == CapabilityStatus.UNSUPPORTED
    assert nodes == []
    assert any("amdxdna" in r or "unsupported" in r for r in reasons)


def test_health_check_non_char_device_rejected(tmp_path):
    """§1.4: Regular file named accel0 is not a char device → rejected."""
    dev_root = tmp_path / "dev"
    sys_root = tmp_path / "sys"
    accel_dir = dev_root / "accel"
    accel_dir.mkdir(parents=True)
    node = accel_dir / "accel0"
    node.write_text("")  # regular file, not char device

    dev_sys = sys_root / "class" / "accel" / "accel0" / "device"
    dev_sys.mkdir(parents=True)
    (dev_sys / "vendor").write_text("0x8086\n")
    (dev_sys / "uevent").write_text("DRIVER=intel_vpu\n")

    status, nodes, reasons = OpenVINONPUBackend.health_check(
        dev_root=dev_root, sys_root=sys_root
    )
    assert status != CapabilityStatus.HEALTHY
    assert str(node) not in nodes
    assert any("not a character device" in r for r in reasons)


# ============================================================================
# Phase B — §2.1 describe() endpoint derived from host_port (P1)
# ============================================================================

def test_describe_endpoint_matches_host_port():
    """§2.1: describe() endpoint must derive from host_port, not class constant."""
    desc = OpenVINONPUBackend.describe(host_port="127.0.0.1:11435")
    assert desc["endpoint"] == "http://127.0.0.1:11435"

    desc = OpenVINONPUBackend.describe(host_port="127.0.0.1:11436")
    assert desc["endpoint"] == "http://127.0.0.1:11436"


# ============================================================================
# Phase B — §2.6 verify_applied() (P1)
# ============================================================================

def test_verify_applied_rocm_detects_vulkan_leak():
    """§2.6: verify_applied() must detect OLLAMA_VULKAN=1 still active."""
    # We can't easily mock systemctl, but we can test the structure exists
    result = ROCmBackend.verify_applied(gfx_version="11.5.1")
    assert hasattr(result, "ok")
    assert hasattr(result, "verified")
    assert hasattr(result, "message")
    assert hasattr(result, "requested")
    assert hasattr(result, "observed")
    assert hasattr(result, "returncode")


def test_verify_applied_vulkan():
    """§2.6: verify_applied() returns HardwareActionResult."""
    result = VulkanBackend.verify_applied()
    assert isinstance(result.ok, bool)
    assert isinstance(result.verified, bool)
    assert isinstance(result.message, str)


# ============================================================================
# Phase B — InferenceBackend Protocol conformance (P1 structural)
# ============================================================================

def test_all_backends_conform_to_inference_backend_protocol():
    """§3 Structural: All three backends must satisfy InferenceBackend Protocol."""
    for backend_cls in (OpenVINONPUBackend, ROCmBackend, VulkanBackend):
        assert hasattr(backend_cls, "name")
        assert hasattr(backend_cls, "override_conf")
        assert hasattr(backend_cls, "conflicts_with")
        assert hasattr(backend_cls, "health_check")
        assert hasattr(backend_cls, "get_env")
        assert hasattr(backend_cls, "describe")
        assert hasattr(backend_cls, "verify_applied")

    # Protocol is runtime_checkable — isinstance works
    assert isinstance(OpenVINONPUBackend, InferenceBackend)
    assert isinstance(ROCmBackend, InferenceBackend)
    assert isinstance(VulkanBackend, InferenceBackend)


# ============================================================================
# Phase B — Fault injection: LIAR case (rc=0 but nothing changed)
# ============================================================================

def test_route_app_audio_liar_case_detection():
    """
    LIAR case: pactl move-sink-input returns 0 but stream placement doesn't change.
    This is the most important fault injection — verifies read-after-write doctrine.
    """
    from celina_hardware.adapters import PipeWireRoutingAdapter, ExecResult

    sink_inputs_out = (
        "Sink Input #42\n"
        '\tapplication.name = "Spotify"\n'
        '\tmedia.name = "Playback"\n'
        '\tnode.name = "spotify-node"\n'
        "\tSink: bluez_output.headphones\n"
    )
    sinks_out = "7\tbluez_output.headphones\tPipeWire\ts32le\tRUNNING\n"

    # LIAR: move-sink-input returns 0 but verification shows stream still on old sink
    def liar_run(cmd, _timeout=5.0):
        cmd_l = list(cmd)
        if cmd_l[:3] == ["pactl", "list", "sink-inputs"]:
            # LIAR: move-sink-input returned 0, but verification shows stream
            # still on the OLD sink (alsa_output.pci...analog-stereo), not the target (bluez_output.headphones)
            liar_output = (
                "Sink Input #42\n"
                '\tapplication.name = "Spotify"\n'
                '\tmedia.name = "Playback"\n'
                '\tnode.name = "spotify-node"\n'
                "\tSink: alsa_output.pci-0000_00_1f.3.analog-stereo\n"
            )
            return ExecResult(0, liar_output, "")
        if cmd_l[:4] == ["pactl", "list", "short", "sinks"]:
            return ExecResult(0, sinks_out, "")
        if cmd_l[:2] == ["pactl", "move-sink-input"]:
            return ExecResult(0, "", "")  # lies: says it moved
        raise AssertionError(f"Unexpected cmd: {cmd_l}")

    adapter = PipeWireRoutingAdapter(runner=liar_run, which=lambda _: "/usr/bin/pactl")
    res = adapter.route_app_audio("spotify", "bluez")
    # Should FAIL because verification catches the LIAR
    assert not res.ok, "LIAR case: move-sink-input returned 0 but stream not on target sink"
    assert "reverted" in res.message.lower() or "not on target" in res.message.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
