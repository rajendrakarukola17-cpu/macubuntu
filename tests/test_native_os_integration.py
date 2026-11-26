"""
Suite 11 — Native OS & Hardware Integration Tests (v4.4.0)

Verifies all 6 native Ubuntu OS integration layers end-to-end:
  1. systemd user control-plane services (userns-safe default + opt-in strict-FS drop-in + decoupled NPU)
  2. Session D-Bus nervous system (org.macubuntu.Celina policy, peer-UID enforcement, fail-closed AskPrompt)
  3. Native GNOME Shell 48/49/50 ESM extension (GSettings strict compilation, ESM JS check, D-Bus proxy, clean disable())
  4. CO-RE eBPF kernel sensor & opt-in fail-open LSM (BTF/LSM/lockdown probes, privilege separation, fallback)
  5. Hardware compute offload (Intel/AMD NPU /dev/accel -> :11435 decoupled from Ollama :11434, GPU Vulkan/ROCm/CUDA, SSRF guard)
  6. Native Debian (.deb) package build & FHS/maintainer script verification (dpkg-deb --info & --contents)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_hardware.compute import (  # noqa: E402
    DEFAULT_NPU_ENDPOINT,
    DEFAULT_OLLAMA_ENDPOINT,
    detect_compute_topology,
    resolve_inference_endpoint,
)
from celina_pkg.dbus_service import (  # noqa: E402
    ALLOWED_FOCUS_MODES,
    BUS_NAME,
    INTERFACE_NAME,
    INTROSPECTION_XML,
    OBJECT_PATH,
    CelinaDBusBackend,
)
from celina_pkg.ebpf_sensor import EbpfEventClient, probe_ebpf_support  # noqa: E402
from celina_pkg.net_policy import URLPolicyError, validate_iot_url  # noqa: E402


# ---------------------------------------------------------------------------
# Layer 1: systemd User Control Plane & Ubuntu 26.04 UserNS Safety
# ---------------------------------------------------------------------------
def test_systemd_control_plane_units_hardened_and_userns_safe():
    celina_unit = (REPO_ROOT / "system" / "systemd" / "celina.service").read_text(encoding="utf-8")
    strict_dropin = (REPO_ROOT / "system" / "systemd" / "celina-strict-fs.conf").read_text(encoding="utf-8")
    npu_unit = (REPO_ROOT / "system" / "systemd" / "celina-npu-inference.service").read_text(encoding="utf-8")
    rocm_dropin = (REPO_ROOT / "system" / "systemd" / "ollama-amd-rocm.conf").read_text(encoding="utf-8")

    # Main user control-plane unit must NOT trigger 218/CAPABILITIES or 226/NAMESPACE on Ubuntu 26.04 --user systemd
    active_celina = "\n".join(
        ln.strip() for ln in celina_unit.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    )
    assert "Restart=on-failure" in active_celina
    assert "NoNewPrivileges=true" in active_celina
    assert "UMask=0077" in active_celina
    assert "SystemCallFilter=@system-service" in active_celina
    assert "CapabilityBoundingSet=" not in active_celina
    assert "AmbientCapabilities=" not in active_celina
    assert "PrivateTmp=" not in active_celina
    assert "RemoveIPC=" not in active_celina
    assert "ProtectSystem=strict" not in active_celina
    assert "ProtectHome=read-only" not in active_celina

    # Opt-in drop-in carries strict mount-namespace isolation explicitly
    assert "ProtectSystem=strict" in strict_dropin
    assert "ProtectHome=read-only" in strict_dropin
    assert "PrivateTmp=yes" in strict_dropin
    assert "CapabilityBoundingSet=" not in strict_dropin
    assert "ReadWritePaths=" in strict_dropin

    # NPU unit is gated by /dev/accel/accel0 and decoupled onto port 11435 (never touching ollama.service)
    assert "ConditionPathExists=/dev/accel/accel0" in npu_unit
    assert "11435" in npu_unit

    # AMD ROCm drop-in documents HSA_OVERRIDE_GFX_VERSION safely
    assert "HSA_OVERRIDE_GFX_VERSION" in rocm_dropin


# ---------------------------------------------------------------------------
# Layer 2: Session D-Bus Nervous System (`org.macubuntu.Celina`)
# ---------------------------------------------------------------------------
def test_dbus_policy_activation_and_peer_uid_enforcement(tmp_path: Path, monkeypatch):
    conf_path = REPO_ROOT / "system" / "dbus" / "org.macubuntu.Celina.conf"
    svc_path = REPO_ROOT / "system" / "dbus" / "org.macubuntu.Celina.service"
    assert conf_path.is_file() and svc_path.is_file()

    # Validate XML policy & D-Bus introspection XML well-formedness
    policy_root = ET.fromstring(conf_path.read_text(encoding="utf-8"))
    assert policy_root.tag == "busconfig"
    intro_root = ET.fromstring(INTROSPECTION_XML)
    iface = intro_root.find(f"./interface[@name='{INTERFACE_NAME}']")
    assert iface is not None
    method_names = {m.attrib["name"] for m in iface.findall("method")}
    assert {"GetStatus", "GetPrivacyState", "GetFocusMode", "SetFocusMode", "UndoLast", "AskPrompt"} <= method_names
    signal_names = {s.attrib["name"] for s in iface.findall("signal")}
    assert {"StatusChanged", "PrivacyDotsChanged"} <= signal_names

    svc_txt = svc_path.read_text(encoding="utf-8")
    assert f"Name={BUS_NAME}" in svc_txt
    assert "SystemdService=celina.service" in svc_txt
    assert OBJECT_PATH == "/org/macubuntu/Celina"

    # Isolate state file directory cleanly into tmp_path
    from celina_pkg import config as celina_config

    monkeypatch.setattr(celina_config, "BASE_DIR", tmp_path)

    # Peer-UID security boundary + state transitions + fail-closed AskPrompt
    backend = CelinaDBusBackend(expected_uid=1000)
    assert backend.verify_caller_uid(1000)[0] is True
    assert backend.verify_caller_uid(1001)[0] is False
    assert backend.verify_caller_uid(None)[0] is False

    denied_status = json.loads(backend.get_status(caller_uid=1001))
    assert denied_status["ok"] is False
    assert "Denied" in denied_status["error"]

    denied_focus = json.loads(backend.set_focus_mode("work", caller_uid=0))
    assert denied_focus["ok"] is False

    st = json.loads(backend.get_status(caller_uid=1000))
    assert st["ok"] is True
    assert st["bus_name"] == BUS_NAME
    assert st["focus_mode"] == "off"

    assert "work" in ALLOWED_FOCUS_MODES
    focus_res = json.loads(backend.set_focus_mode("work", caller_uid=1000))
    assert focus_res["ok"] is True
    assert focus_res["focus_mode"] == "work"
    assert backend.get_focus_mode(caller_uid=1000) == "work"
    assert (tmp_path / "focus_mode.json").is_file()

    # Privacy state reads cleanly from isolated BASE_DIR
    (tmp_path / "privacy_state.json").write_text(json.dumps({"mic": True, "cam": False, "screen": True}), encoding="utf-8")
    dots = json.loads(backend.get_privacy_state(caller_uid=1000))
    assert dots == {"ok": True, "mic": True, "cam": False, "screen": True}

    # AskPrompt strictly rejects empty or >4000-char prompts and wrong caller UID
    assert json.loads(backend.ask_prompt("   ", caller_uid=1000))["ok"] is False
    assert json.loads(backend.ask_prompt("x" * 4001, caller_uid=1000))["ok"] is False
    assert json.loads(backend.ask_prompt("hello", caller_uid=2000))["ok"] is False

    # Invalid focus mode rejected cleanly
    bad_focus = json.loads(backend.set_focus_mode("InvalidMode!", caller_uid=1000))
    assert bad_focus["ok"] is False


# ---------------------------------------------------------------------------
# Layer 3: Native GNOME Shell 48/49/50 ESM Extension
# ---------------------------------------------------------------------------
def test_gnome_shell_extension_schema_esm_and_lifecycle():
    ext_dir = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org"
    meta = json.loads((ext_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["uuid"] == "celina-island@macubuntu.org"
    assert set(meta["shell-version"]) >= {"48", "49", "50"}
    assert meta["settings-schema"] == "org.gnome.shell.extensions.celina-island"

    js_text = (ext_dir / "extension.js").read_text(encoding="utf-8")
    assert "export default class CelinaIslandExtension extends Extension" in js_text
    assert "Main.panel.addToStatusArea" in js_text
    assert "org.macubuntu.Celina" in js_text
    assert "global.workspace_manager" in js_text
    assert "global.display" in js_text
    assert "disable()" in js_text

    # Strict GSettings XML compilation check
    if shutil.which("glib-compile-schemas"):
        res = subprocess.run(
            ["glib-compile-schemas", "--strict", "--dry-run", str(ext_dir / "schemas")],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res.returncode == 0, res.stderr

    # Strict ESM JavaScript syntax check via Node
    if shutil.which("node"):
        res = subprocess.run(
            ["node", "--input-type=module", "--check"],
            input=js_text,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res.returncode == 0, res.stderr


# ---------------------------------------------------------------------------
# Layer 4: CO-RE eBPF Kernel Sensor & Optional Fail-Open LSM
# ---------------------------------------------------------------------------
def test_ebpf_sensor_source_privilege_separation_and_fallback(tmp_path: Path):
    bpf_c = (REPO_ROOT / "system" / "ebpf" / "celina_sensor.bpf.c").read_text(encoding="utf-8")
    bpf_svc = (REPO_ROOT / "system" / "ebpf" / "celina-ebpf-sensor.service").read_text(encoding="utf-8")

    # CO-RE ringbuf tracepoints + opt-in fail-open LSM hook
    assert "BPF_MAP_TYPE_RINGBUF" in bpf_c
    assert 'SEC("tp/sched/sched_process_exec")' in bpf_c
    assert 'SEC("tp/syscalls/sys_enter_openat")' in bpf_c
    assert "#ifdef ENABLE_CELINA_BPF_LSM" in bpf_c
    assert 'SEC("lsm/socket_connect")' in bpf_c
    assert "if (!active || *active == 0)\n        return 0;" in bpf_c

    # Dedicated system unit isolates CAP_BPF/CAP_PERFMON away from Celina Python
    assert "CAP_BPF" in bpf_svc
    assert "CAP_PERFMON" in bpf_svc
    assert "NoNewPrivileges=true" in bpf_svc
    assert "ConditionPathExists=/sys/kernel/btf/vmlinux" in bpf_svc

    # Probe 1: Missing BTF -> graceful userspace D-Bus fallback
    caps_no_btf = probe_ebpf_support(sys_root=tmp_path / "sys_empty", stream_path=tmp_path / "none.jsonl")
    assert caps_no_btf.btf_available is False
    assert caps_no_btf.recommended_mode == "userspace_dbus_fallback"

    # Probe 2: BTF + BPF LSM active
    sys_lsm = tmp_path / "sys_lsm"
    (sys_lsm / "kernel" / "btf").mkdir(parents=True)
    (sys_lsm / "kernel" / "btf" / "vmlinux").write_bytes(b"BTF")
    (sys_lsm / "kernel" / "security").mkdir(parents=True)
    (sys_lsm / "kernel" / "security" / "lsm").write_text("lockdown,capability,apparmor,bpf\n", encoding="utf-8")
    (sys_lsm / "kernel" / "security" / "lockdown").write_text("none [integrity] confidentiality\n", encoding="utf-8")

    stream = tmp_path / "events.jsonl"
    stream.write_text(
        json.dumps({"uid": 1000, "pid": 42, "comm": "firefox", "event_type": 1}) + "\n"
        + json.dumps({"uid": 0, "pid": 1, "comm": "systemd", "event_type": 1}) + "\n",
        encoding="utf-8",
    )
    caps_lsm = probe_ebpf_support(sys_root=sys_lsm, stream_path=stream)
    assert caps_lsm.btf_available is True
    assert caps_lsm.bpf_lsm_enabled is True
    assert caps_lsm.lockdown_mode == "integrity"
    assert caps_lsm.sensor_stream_active is True
    assert caps_lsm.recommended_mode == "ebpf_lsm"

    # Unprivileged stream reader filters strictly by UID
    events = EbpfEventClient(stream).read_recent_events(limit=10, uid_filter=1000)
    assert len(events) == 1
    assert events[0]["comm"] == "firefox"

    # CLI check & dry-run tools exit 0 with zero side effects
    for flag in ("--check", "--dry-run"):
        res = subprocess.run(
            ["bash", str(REPO_ROOT / "tools" / "build-ebpf.sh"), flag],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res.returncode == 0, res.stderr


# ---------------------------------------------------------------------------
# Layer 5: Hardware Compute Offload (NPU /dev/accel + GPU Vulkan/ROCm/CUDA)
# ---------------------------------------------------------------------------
def test_compute_topology_npu_decoupled_routing_and_ssrf_guard(tmp_path: Path):
    dev_root = tmp_path / "dev"
    sys_root = tmp_path / "sys"

    # Simulate Intel Core Ultra NPU (/dev/accel/accel0) + AMD Radeon iGPU (/dev/dri/renderD128)
    (dev_root / "accel").mkdir(parents=True)
    (dev_root / "accel" / "accel0").write_text("", encoding="utf-8")
    (sys_root / "class" / "accel" / "accel0" / "device").mkdir(parents=True)
    (sys_root / "class" / "accel" / "accel0" / "device" / "vendor").write_text("0x8086\n", encoding="utf-8")
    (sys_root / "class" / "accel" / "accel0" / "device" / "uevent").write_text("DRIVER=intel_vpu\n", encoding="utf-8")

    (dev_root / "dri").mkdir(parents=True)
    (dev_root / "dri" / "renderD128").write_text("", encoding="utf-8")
    (sys_root / "class" / "drm" / "renderD128" / "device").mkdir(parents=True)
    (sys_root / "class" / "drm" / "renderD128" / "device" / "vendor").write_text("0x1002\n", encoding="utf-8")
    (sys_root / "class" / "drm" / "renderD128" / "device" / "uevent").write_text("DRIVER=amdgpu\n", encoding="utf-8")

    topo = detect_compute_topology(dev_root=dev_root, sys_root=sys_root)
    assert topo.has_npu is True
    assert topo.has_gpu is True
    assert topo.recommended_backend == "npu_decoupled"
    assert any(a.kind == "npu" and a.backend == "openvino-npu" for a in topo.accelerators)
    assert any(a.kind == "gpu" and a.backend == "rocm-vulkan" for a in topo.accelerators)
    # Ollama env overrides contain safe Vulkan flag only — never unsafe NPU env vars
    assert topo.ollama_env_overrides == {"OLLAMA_VULKAN": "1"}

    # When NPU daemon (:11435) is reachable -> route to decoupled NPU service
    route_npu = resolve_inference_endpoint(topology=topo, probe_fn=lambda url: url == DEFAULT_NPU_ENDPOINT)
    assert route_npu["endpoint"] == DEFAULT_NPU_ENDPOINT
    assert route_npu["service"] == "celina-npu-inference.service"
    assert route_npu["fallback_used"] is False

    # When NPU daemon (:11435) is NOT running -> transparently fall back to Ollama (:11434)
    route_fallback = resolve_inference_endpoint(topology=topo, probe_fn=lambda _url: False)
    assert route_fallback["endpoint"] == DEFAULT_OLLAMA_ENDPOINT
    assert route_fallback["service"] == "ollama.service"
    assert route_fallback["fallback_used"] is True

    # SSRF / IoT webhook policy must block BOTH Ollama (11434) and NPU daemon (11435)
    allowlist = {"allowed_hosts": ["127.0.0.1"], "allowed_ports": [11434, 11435], "allowed_methods": ["POST"]}
    for blocked_port in (11434, 11435):
        with pytest.raises(URLPolicyError):
            validate_iot_url(f"http://127.0.0.1:{blocked_port}/api/generate", "POST", allowlist)

    # detect-compute.sh produces valid JSON with zero writes to HOME
    clean_home = tmp_path / "clean_home"
    clean_home.mkdir()
    env = dict(os.environ, HOME=str(clean_home))
    res = subprocess.run(
        ["bash", str(REPO_ROOT / "tools" / "detect-compute.sh"), "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert res.returncode == 0, res.stderr
    parsed = json.loads(res.stdout)
    assert "topology" in parsed and "active_route" in parsed
    assert list(clean_home.iterdir()) == []


# ---------------------------------------------------------------------------
# Layer 6: Native Debian (.deb) Package Build & Verification
# ---------------------------------------------------------------------------
def test_debian_package_build_and_fhs_contents(tmp_path: Path):
    if not shutil.which("dpkg-deb") or not shutil.which("glib-compile-schemas"):
        pytest.skip("dpkg-deb or glib-compile-schemas not installed in environment")

    out_dir = tmp_path / "deb_out"
    res = subprocess.run(
        ["bash", str(REPO_ROOT / "tools" / "build-deb.sh"), "--out", str(out_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, f"build-deb.sh failed: {res.stderr}"

    deb_files = list(out_dir.glob("macubuntu_*_all.deb"))
    assert len(deb_files) == 1
    deb_path = deb_files[0]

    info = subprocess.run(["dpkg-deb", "--info", str(deb_path)], capture_output=True, text=True, timeout=15)
    assert info.returncode == 0
    assert "Package: macubuntu" in info.stdout
    assert "Architecture: all" in info.stdout
    assert "systemd" in info.stdout
    assert "dbus" in info.stdout
    assert "libglib2.0-bin" in info.stdout
    assert "bubblewrap" in info.stdout

    assert "Conflicts: macubuntu-old" in info.stdout

    contents = subprocess.run(["dpkg-deb", "--contents", str(deb_path)], capture_output=True, text=True, timeout=15)
    assert contents.returncode == 0
    expected_paths = [
        "./usr/lib/systemd/user/celina.service",
        "./usr/lib/systemd/user/celina-eventd.service",
        "./usr/lib/systemd/user/celina-router.service",
        "./usr/lib/systemd/user/celina-voice.service",
        "./usr/lib/systemd/user/celina-memory.service",
        "./usr/lib/systemd/user/celina-npu-inference.service",
        "./usr/lib/systemd/system/celina-ebpf-sensor.service",
        "./etc/dbus-1/session.d/org.macubuntu.Celina.conf",
        "./etc/dbus-1/session.d/org.macubuntu.Celina.TierB.conf",
        "./usr/share/dbus-1/services/org.macubuntu.Celina.service",
        "./usr/share/gnome-shell/extensions/celina-island@macubuntu.org/extension.js",
        "./usr/share/gnome-shell/extensions/celina-island@macubuntu.org/dbus.js",
        "./usr/share/gnome-shell/extensions/celina-island@macubuntu.org/prefs.js",
        "./usr/share/gnome-shell/extensions/celina-island@macubuntu.org/schemas/gschemas.compiled",
        "./usr/share/icons/hicolor/scalable/apps/celina.svg",
        "./usr/share/applications/celina-island.desktop",
        "./usr/bin/macubuntu-install",
        "./usr/bin/macubuntu-doctor",
        "./usr/bin/macubuntu-compute",
        "./usr/bin/celina-dbus",
    ]
    for path in expected_paths:
        assert path in contents.stdout, f"Missing expected FHS path in .deb: {path}"


def test_split_systemd_microdaemons_and_strict_sandbox_dropin():
    for unit_name in ("celina-router.service", "celina-voice.service", "celina-memory.service"):
        txt = (REPO_ROOT / "system" / "systemd" / unit_name).read_text(encoding="utf-8")
        assert "NoNewPrivileges=yes" in txt
        assert "RestrictAddressFamilies=AF_UNIX" in txt
        assert "SystemCallFilter=@system-service" in txt
        assert "SystemCallErrorNumber=EPERM" in txt
        assert "ProtectSystem=strict" not in txt  # userns-safe default on Ubuntu 26.04

    strict_txt = (REPO_ROOT / "system" / "systemd" / "celina-strict-fs.conf").read_text(encoding="utf-8")
    for directive in (
        "ProtectSystem=strict",
        "PrivateDevices=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectKernelLogs=yes",
        "ProtectControlGroups=yes",
        "RestrictNamespaces=yes",
        "StateDirectory=celina",
    ):
        assert directive in strict_txt


def test_modular_ebpf_signed_policy_and_prometheus_metrics(tmp_path: Path):
    import hashlib
    from celina_pkg.ebpf_sensor import format_prometheus_metrics, verify_signed_policy

    exec_c = (REPO_ROOT / "system" / "ebpf" / "sensors" / "exec.bpf.c").read_text(encoding="utf-8")
    net_c = (REPO_ROOT / "system" / "ebpf" / "sensors" / "net.bpf.c").read_text(encoding="utf-8")
    focus_c = (REPO_ROOT / "system" / "ebpf" / "enforce" / "focus.bpf.c").read_text(encoding="utf-8")
    policy_yaml = REPO_ROOT / "system" / "ebpf" / "policy" / "focus.policy.yaml"
    assert 'SEC("tp/sched/sched_process_exec")' in exec_c
    assert 'SEC("tp/syscalls/sys_enter_connect")' in net_c
    assert 'SEC("tp/syscalls/sys_enter_bind")' in net_c
    assert 'SEC("lsm/socket_connect")' in focus_c
    assert 'SEC("lsm/socket_bind")' in focus_c
    assert 'SEC("lsm/file_open")' in focus_c
    assert policy_yaml.is_file()

    # Signed policy verification: valid SHA-256 envelope passes; tampered policy fails closed
    pol_copy = tmp_path / "focus.policy.yaml"
    pol_copy.write_bytes(policy_yaml.read_bytes())
    sig_file = tmp_path / "policy.signed"
    digest = hashlib.sha256(pol_copy.read_bytes()).hexdigest()
    sig_file.write_text(f"sha256:{digest}\n", encoding="utf-8")

    ok, msg = verify_signed_policy(pol_copy, sig_file)
    assert ok is True, msg

    pol_copy.write_text("# tampered\n", encoding="utf-8")
    ok_bad, _ = verify_signed_policy(pol_copy, sig_file)
    assert ok_bad is False

    # Prometheus metrics exposition
    prom = format_prometheus_metrics(total_blocks=7, net_blocks=5)
    assert "celina_ebpf_blocks_total 7" in prom
    assert "celina_ebpf_net_blocks_total 5" in prom


def test_gnome_extension_modular_dbus_and_adw_prefs():
    ext_dir = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org"
    meta = json.loads((ext_dir / "metadata.json").read_text(encoding="utf-8"))
    assert "51" in meta["shell-version"]
    dbus_js = (ext_dir / "dbus.js").read_text(encoding="utf-8")
    prefs_js = (ext_dir / "prefs.js").read_text(encoding="utf-8")
    assert "export class CelinaDBusClient" in dbus_js
    assert "OffloadRemote" in dbus_js
    assert "Adw.PreferencesPage" in prefs_js
    assert "ExtensionPreferences" in prefs_js

    # Tier B session D-Bus policy is valid XML & isolates org.macubuntu.Celina.TierB
    tier_b_conf = REPO_ROOT / "system" / "dbus" / "org.macubuntu.Celina.TierB.conf"
    tier_b_root = ET.fromstring(tier_b_conf.read_text(encoding="utf-8"))
    assert tier_b_root.tag == "busconfig"
    assert "org.macubuntu.Celina.TierB" in tier_b_conf.read_text(encoding="utf-8")

    if shutil.which("node"):
        for fname in ("dbus.js", "prefs.js"):
            r = subprocess.run(
                ["node", "--input-type=module", "--check"],
                input=(ext_dir / fname).read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert r.returncode == 0, f"{fname}: {r.stderr}"


def test_strix_halo_rocm_guard_npu_localhost_and_why_attribution(tmp_path: Path):
    from celina_hardware.backends import OpenVINONPUBackend
    from celina_hardware.detect_gpu import select_compute_backend
    from celina_pkg.router import TurnTrace

    # Strix Halo (gfx1151 / Radeon 8060S) MUST pick ROCm 11.5.1 and NEVER set OLLAMA_VULKAN=1
    strix = select_compute_backend(
        lspci_text="c1:00.0 VGA compatible controller [0300]: AMD/ATI Strix Halo Radeon 8060S [1002:1586]",
        dev_root=tmp_path,
        sys_root=tmp_path,
    )
    assert strix.backend == "rocm"
    assert strix.env["HSA_OVERRIDE_GFX_VERSION"] == "11.5.1"
    assert strix.env["OLLAMA_FLASH_ATTENTION"] == "1"
    assert strix.env["OLLAMA_KV_CACHE_TYPE"] == "q8_0"
    assert "OLLAMA_VULKAN" not in strix.env

    # Non-Strix-Halo AMD RDNA3+ selects Vulkan
    rdna3 = select_compute_backend(
        lspci_text="c1:00.0 VGA compatible controller [0300]: AMD/ATI Radeon 780M [1002:15bf]",
        dev_root=tmp_path,
        sys_root=tmp_path,
    )
    assert rdna3.backend == "vulkan"
    assert rdna3.env["OLLAMA_VULKAN"] == "1"

    # Intel Arc selects Vulkan
    arc = select_compute_backend(
        lspci_text="00:02.0 VGA compatible controller [0300]: Intel Corporation Arc Graphics [8086:7d55]",
        dev_root=tmp_path,
        sys_root=tmp_path,
    )
    assert arc.backend == "vulkan"

    # NVIDIA selects CUDA
    nv = select_compute_backend(
        lspci_text="01:00.0 3D controller [0302]: NVIDIA Corporation RTX 4060 [10de:28e0]",
        dev_root=tmp_path,
        sys_root=tmp_path,
    )
    assert nv.backend == "cuda"

    # OpenVINONPUBackend enforces localhost-only bind (refuses 0.0.0.0) & builds systemd-run --user --scope
    with pytest.raises(ValueError):
        OpenVINONPUBackend.get_env(host_port="0.0.0.0:11435")

    (tmp_path / "accel").mkdir()
    (tmp_path / "accel" / "accel0").write_text("", encoding="utf-8")
    cmd = OpenVINONPUBackend.build_scoped_launch_cmd(dev_root=tmp_path)
    assert cmd[:3] == ["systemd-run", "--user", "--scope"]
    assert "GGML_OPENVINO_DEVICE=NPU" in cmd
    assert "OLLAMA_HOST=127.0.0.1:11435" in cmd

    # /why TurnTrace includes compute_backend provenance
    receipt = TurnTrace(prompt_preview="hi", compute_backend=strix.to_why_line()).format_receipt()
    assert "• Compute: compute_backend=rocm" in receipt

    # Ollama override drop-ins exist and enforce Strix Halo safety
    rocm_conf = (REPO_ROOT / "system" / "ollama" / "rocm-gfx1151.override.conf").read_text(encoding="utf-8")
    assert 'HSA_OVERRIDE_GFX_VERSION=11.5.1' in rocm_conf
    assert 'Environment="OLLAMA_VULKAN=1"' not in rocm_conf
    vulkan_conf = (REPO_ROOT / "system" / "ollama" / "vulkan-igpu.override.conf").read_text(encoding="utf-8")
    assert 'Environment="OLLAMA_VULKAN=1"' in vulkan_conf

