"""
Suite 20: OS-Level Kernel & Systems Hardening Tests (7-Layer Stack).

Verifies:
  1. sched_ext + scx_lavd / scx_rusty config, polkit rule, voice lifecycle hooks, & status probe
  2. cgroup v2 hard memory ceiling (local-inference.slice, ollama-8gb-ram.conf, ollama-hardening.conf)
  3. PSI (/proc/pressure/memory) + kv-psi 25% num_ctx trimming in inference_adaptive & CelinaRouter
  4. Landlock LSM / SHarness canonical files_office policy
  5. PREEMPT_RT opt-in assets (99-celina-rt.conf, 99-celina-rt.cfg, celina-voice-rt.conf) & probe
  6. TPM 2.0 measured boot (--tpm2-public-key surviving kernel updates + systemd-creds sealing)
  7. io_uring readiness, select_backend() NPU/GPU offload, and os-hardening-setup.sh --dry-run
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_hardware.detect_gpu import select_backend  # noqa: E402
from celina_pkg import inference_adaptive, landlock_policy, os_hardening, router  # noqa: E402


def test_sched_ext_configs_polkit_voice_hooks_and_probe(tmp_path: Path):
    # 1. Valid TOML for /etc/scx_loader/config.toml
    scx_cfg = tomllib.loads((REPO_ROOT / "system/scx/config.toml").read_text(encoding="utf-8"))
    assert scx_cfg["default_sched"] == "scx_bpfland"
    assert scx_cfg["default_mode"] == "Auto"
    assert "--pinned-slice-us" in scx_cfg["scheds"]["scx_lavd"]["lowlatency_mode"]
    assert "500" in scx_cfg["scheds"]["scx_lavd"]["lowlatency_mode"]

    # 2. Polkit rule grants passwordless org.scx.* for sudo/wheel
    polkit_txt = (REPO_ROOT / "system/polkit/99-scx-loader.rules").read_text(encoding="utf-8")
    assert 'org.scx.' in polkit_txt and 'polkit.Result.YES' in polkit_txt

    # 3. celina-voice.service gates opt-in scx_lavd & reverts to native EEVDF (`scxctl stop`) on stop
    voice_unit = (REPO_ROOT / "system/systemd/celina-voice.service").read_text(encoding="utf-8")
    assert "scxctl switch -s lavd --mode lowlatency" in voice_unit
    assert "CELINA_ENABLE_SCX_LAVD" in voice_unit
    assert "scxctl stop" in voice_unit
    assert "ProtectSystem=strict" not in voice_unit

    # 3b. SchedExtWatchdog detects compositor-freeze ejections & locks fallback to native EEVDF
    wd = os_hardening.SchedExtWatchdog(max_ejections=2, cooldown_sec=3600.0)
    empty_sys = tmp_path / "sys_ejected"
    (empty_sys / "kernel" / "sched_ext").mkdir(parents=True)
    (empty_sys / "kernel" / "sched_ext" / "state").write_text("disabled\n", encoding="utf-8")
    h1 = wd.evaluate_health(expected_active=True, sys_root=empty_sys, now=1000.0)
    assert h1["healthy"] is False and h1["action"] == "warn_ejection"
    h2 = wd.evaluate_health(expected_active=True, sys_root=empty_sys, now=1010.0)
    assert h2["action"] == "revert_to_eevdf" and h2["locked_to_eevdf"] is True
    assert h2["fallback_cmd"] == ["/usr/bin/scxctl", "stop"]
    allowed, reason = wd.allow_scx_lavd(now=1020.0)
    assert allowed is False and "EEVDF" in reason

    # 4. Runtime probe & safe argv builder
    (tmp_path / "kernel" / "sched_ext" / "root").mkdir(parents=True)
    (tmp_path / "kernel" / "sched_ext" / "state").write_text("enabled\n", encoding="utf-8")
    (tmp_path / "kernel" / "sched_ext" / "root" / "ops").write_text("lavd_1.0\n", encoding="utf-8")
    st = os_hardening.detect_sched_ext(sys_root=tmp_path, scxctl_bin="/usr/bin/scxctl")
    assert st.supported is True and st.enabled is True and st.active_ops == "lavd_1.0"

    assert os_hardening.build_scx_switch_cmd("lavd", mode="lowlatency") == [
        "/usr/bin/scxctl",
        "switch",
        "-s",
        "lavd",
        "--mode",
        "lowlatency",
    ]
    assert os_hardening.build_scx_switch_cmd("rusty") == ["/usr/bin/scxctl", "switch", "-s", "rusty"]
    with pytest.raises(ValueError):
        os_hardening.build_scx_switch_cmd("unknown_sched")


def test_cgroup_v2_slice_ollama_hardening_and_psi_kv_trimming(tmp_path: Path):
    # 1. local-inference.slice + ollama-hardening.conf (ollama-8gb-ram.conf removed; unified into Stage 35 tier-adapted 90-macubuntu-qos.conf)
    sl = (REPO_ROOT / "system/systemd/local-inference.slice").read_text(encoding="utf-8")
    assert "CPUQuota=250%" in sl and "MemoryHigh=5G" in sl and "MemoryMax=6G" in sl and "MemorySwapMax=2G" in sl

    hard = (REPO_ROOT / "system/systemd/ollama-hardening.conf").read_text(encoding="utf-8")
    for directive in (
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        "DevicePolicy=closed",
        "DeviceAllow=/dev/dri rw",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    ):
        assert directive in hard

    # 2. Cgroup v2 memory state reader
    cg = tmp_path / "local-inference.slice"
    cg.mkdir()
    (cg / "memory.current").write_text("4294967296\n", encoding="utf-8")
    (cg / "memory.events").write_text("low 0\nhigh 7\nmax 0\noom 0\n", encoding="utf-8")
    (cg / "memory.pressure").write_text(
        "some avg10=12.40 avg60=6.10 avg300=2.00 total=5000\n"
        "full avg10=14.20 avg60=5.50 avg300=1.20 total=2100\n",
        encoding="utf-8",
    )
    snap = os_hardening.read_cgroup_memory_state(cg)
    assert snap.available is True and snap.events["high"] == 7 and snap.pressure_full_avg10 == pytest.approx(14.20)

    # 3. PSI kv-psi 25% num_ctx trim in both inference_adaptive & CelinaRouter
    psi_file = tmp_path / "memory.psi"
    psi_file.write_text(
        "some avg10=22.50 avg60=8.00 avg300=1.00 total=100\n"
        "full avg10=15.40 avg60=4.00 avg300=0.50 total=50\n",
        encoding="utf-8",
    )
    psi = os_hardening.read_memory_psi(psi_path=psi_file)
    assert psi.available is True and psi.full_avg10 == pytest.approx(15.40)

    trimmed_ctx, did_trim, reason = os_hardening.compute_psi_trimmed_ctx(4096, psi)
    assert did_trim is True and trimmed_ctx == 3072 and "15.4" in reason

    prof_hi = inference_adaptive.build_adaptive_inference_profile(
        base_num_ctx=4096, ram_gb=8, avail_ram_mb=3200, psi_full_avg10=18.0
    )
    # CONSTRAINED tier caps num_ctx at 2048, then PSI trims 25% → 1536
    assert prof_hi.num_ctx == 1536

    fake_router = object.__new__(router.CelinaRouter)
    assert fake_router._read_psi(psi_path=psi_file) == pytest.approx(15.40)
    opts = fake_router._apply_psi_kv_trim({"num_ctx": 4096}, psi_path=psi_file)
    assert opts["num_ctx"] == 3072


def test_landlock_files_office_preempt_rt_tpm2_iouring_and_npu_backend(tmp_path: Path):
    # 1. Landlock / SHarness canonical files_office policy
    docs = tmp_path / "Documents"
    docs.mkdir()
    prof = landlock_policy.files_office_policy(docs_dir=docs)
    assert "/usr" in prof.read_paths and str(docs.resolve()) in prof.write_paths
    assert prof.tcp_connect_ports == (443,)
    pol_json = json.loads(
        (REPO_ROOT / "features/celina/config/policies/celina-files-office.policy.json").read_text(encoding="utf-8")
    )
    assert pol_json["network"]["connect_tcp_ports"] == [443]

    # 2. PREEMPT_RT opt-in configs & probe
    rt_sysctl = (REPO_ROOT / "system/sysctl/99-celina-rt.conf").read_text(encoding="utf-8")
    assert "kernel.sched_rt_runtime_us = 1000000" in rt_sysctl
    rt_grub = (REPO_ROOT / "system/grub/99-celina-rt.cfg").read_text(encoding="utf-8")
    assert "processor.max_cstate=1" in rt_grub and "intel_idle.max_cstate=1" in rt_grub
    rt_voice = (REPO_ROOT / "system/systemd/celina-voice-rt.conf").read_text(encoding="utf-8")
    assert "CPUSchedulingPolicy=fifo" in rt_voice and "CPUSchedulingPriority=90" in rt_voice

    rt_flag = tmp_path / "realtime"
    rt_flag.write_text("1\n", encoding="utf-8")
    rt_st = os_hardening.detect_preempt_rt(uname_v="#1 SMP PREEMPT_RT Ubuntu", realtime_path=rt_flag)
    assert rt_st.is_realtime is True and rt_st.sys_realtime_flag == 1
    assert os_hardening.build_chrt_voice_cmd(1234, priority=90) == ["chrt", "-f", "-p", "90", "1234"]

    # 3. TPM 2.0 measured boot using --tpm2-public-key (NOT brittle literal --tpm2-pcrs=7,11)
    dev_dir = tmp_path / "dev"
    dev_dir.mkdir()
    (dev_dir / "tpmrm0").write_text("", encoding="utf-8")
    assert os_hardening.detect_tpm2(dev_root=dev_dir).present is True

    sign_cmd = os_hardening.build_tpm2_pcr_sign_cmd("6.14.0-22-generic")
    assert sign_cmd[0:2] == ["systemd-measure", "sign"] and "--public-key=/etc/celina/tierb-pcr.pub" in sign_cmd

    enroll_cmd = os_hardening.build_tpm2_cryptenroll_cmd("/dev/nvme0n1p4")
    assert "--tpm2-public-key=/etc/celina/tierb-pcr.pub" in enroll_cmd
    assert not any(a.startswith("--tpm2-pcrs") for a in enroll_cmd)

    seal_cmd = os_hardening.build_tpm2_seal_secret_cmd(Path("/tmp/identity.cred"))
    assert seal_cmd[:3] == ["systemd-creds", "encrypt", "--with-key=tpm2"]

    # 4. io_uring readiness + unified report
    iou = tmp_path / "io_uring_disabled"
    iou.write_text("0\n", encoding="utf-8")
    assert os_hardening.detect_io_uring(sys_path=iou).available is True

    # 5. select_backend() NPU / GPU selector
    (dev_dir / "accel").mkdir()
    (dev_dir / "accel" / "accel0").write_text("", encoding="utf-8")
    assert select_backend(lspci_text="", dev_root=dev_dir, sys_root=tmp_path) == "openvino_npu"
    assert (
        select_backend(
            lspci_text="c1:00.0 VGA compatible controller [0300]: AMD/ATI Strix Halo Radeon 8060S [1002:1586]",
            dev_root=dev_dir,
            sys_root=tmp_path,
            prefer_npu=False,
        )
        == "rocm"
    )

    # 6. os-hardening-setup.sh --dry-run runs cleanly with zero filesystem mutation
    dry_home = tmp_path / "dry_home"
    dry_home.mkdir()
    proc = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "tools/os-hardening-setup.sh"),
            "--dry-run",
            "--enable-ollama-hardening",
            "--enable-preempt-rt",
            "--seal-tpm2-tierb",
            "--tpm2-device",
            "/dev/nvme0n1p4",
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(dry_home)},
        timeout=15,
    )
    assert proc.returncode == 0
    assert "scx_loader/config.toml" in proc.stdout
    assert "local-inference.slice" in proc.stdout
    assert "systemd-oomd" in proc.stdout
    assert "--tpm2-public-key=/etc/celina/tierb-pcr.pub" in proc.stdout
    assert list(dry_home.iterdir()) == []
