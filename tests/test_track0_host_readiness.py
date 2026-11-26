"""Offline tests for optional continuity defaults and listener inventory."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("host_readiness", ROOT / "tools/verify_host_readiness.py")
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)
FLAGS = ("ENABLE_AIRPLAY_VIDEO", "ENABLE_ANDROID_MIRROR", "ENABLE_SYNCTHING", "ENABLE_UNIVERSAL_CONTROL")


def test_continuity_defaults_and_explicit_override():
    env = {k: v for k, v in os.environ.items() if k not in FLAGS}
    code = 'source core/common.sh; init_defaults; printf "%s\\n" "$ENABLE_AIRPLAY_VIDEO" "$ENABLE_ANDROID_MIRROR" "$ENABLE_SYNCTHING" "$ENABLE_UNIVERSAL_CONTROL"'
    result = subprocess.run(["bash", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ["false"] * 4
    env.update({k: "true" for k in FLAGS})
    result = subprocess.run(["bash", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ["true"] * 4
    for path in (ROOT / "profiles").glob("*.env"):
        for line in path.read_text().splitlines():
            for flag in FLAGS:
                if line.startswith(flag + "="):
                    assert line.split("=", 1)[1].split("#")[0].strip().strip('"') == "false"


def test_listener_inventory_tcp_udp_ipv4_ipv6_and_failure(tmp_path):
    header = "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
    (tmp_path / "tcp").write_text(header + "0: 00000000:1F90 00000000:0000 0A 0:0 0:0 0 1000 0 42\n1: 0100007F:0100 0100007F:0200 01 0:0 0:0 0 1000 0 43\n")
    (tmp_path / "tcp6").write_text(header + "0: 00000000000000000000000001000000:01BB 00000000000000000000000000000000:0000 0A 0:0 0:0 0 1000 0 44\n")
    (tmp_path / "udp").write_text(header + "0: 0100007F:14E9 00000000:0000 07 0:0 0:0 0 1000 0 45\n")
    # udp6 intentionally missing: inventory must explicitly say it is incomplete.
    result = readiness.inventory(tmp_path)
    assert result["complete"] is False
    assert "udp6" in result["errors"]
    assert {(x["protocol"], x["address"], x["port"]) for x in result["listeners"]} == {
        ("tcp", "0.0.0.0", 8080), ("tcp6", "::1", 443), ("udp", "127.0.0.1", 5353),
    }
    assert json.loads(json.dumps(result))["scope"]
