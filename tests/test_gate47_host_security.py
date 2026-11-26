"""Read-only host readiness checks using local fixture paths only."""
import importlib.util
import os
from pathlib import Path
import socket

SPEC = importlib.util.spec_from_file_location("readiness47", Path(__file__).resolve().parents[1] / "tools/verify_host_readiness.py")
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)


def test_socket_modes_and_absence(tmp_path):
    path = tmp_path / "input.sock"
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        os.chmod(path, 0o666)
        assert readiness.socket_mode_check(path)["status"] == "FAIL"
        os.chmod(path, 0o660)
        assert readiness.socket_mode_check(path)["status"] == "PASS"
    assert readiness.socket_mode_check(tmp_path / "missing")["status"] == "SKIP_HOST_REQUIRED"
    regular = tmp_path / "file"
    regular.touch()
    assert readiness.socket_mode_check(regular)["status"] == "FAIL"


def test_yama_scope(tmp_path):
    path = tmp_path / "ptrace_scope"
    assert readiness.yama_check(path)["status"] == "SKIP_HOST_REQUIRED"
    path.write_text("0\n")
    assert readiness.yama_check(path)["status"] == "FAIL"
    path.write_text("1\n")
    assert readiness.yama_check(path)["status"] == "PASS"
    path.write_text("invalid\n")
    assert readiness.yama_check(path)["status"] == "FAIL"
