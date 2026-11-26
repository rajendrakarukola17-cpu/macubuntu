#!/usr/bin/env python3
"""Read-only Linux listener inventory for the current network namespace.

This is a point-in-time socket inventory, not a firewall, reachability,
process ownership, container-wide, or sandbox assessment. UDP entries are
unconnected bound sockets, which may receive datagrams without a listen call.
"""
from __future__ import annotations

import ipaddress
import json
import os
import stat
import sys
from pathlib import Path


def _address(hex_address: str) -> str:
    raw = bytes.fromhex(hex_address)
    if len(raw) not in (4, 16):
        raise ValueError("unexpected address length")
    # Linux procfs prints each native-endian 32-bit word in hexadecimal.
    raw = b"".join(int.from_bytes(raw[i:i + 4], "big").to_bytes(4, sys.byteorder)
                   for i in range(0, len(raw), 4))
    return str(ipaddress.ip_address(raw))


def inventory(proc_net: Path = Path("/proc/net")) -> dict:
    """Enumerate TCP listeners and unconnected bound UDP sockets; report errors."""
    listeners = []
    errors = {}
    for protocol in ("tcp", "tcp6", "udp", "udp6"):
        try:
            lines = (proc_net / protocol).read_text(encoding="ascii").splitlines()
            if not lines:
                raise ValueError("missing header")
            for line in lines[1:]:
                fields = line.split()
                local, remote, state = fields[1:4]
                if protocol.startswith("tcp"):
                    if state != "0A":
                        continue
                elif state != "07" or int(remote.split(":")[1], 16) != 0:
                    continue
                addr, port = local.split(":")
                listeners.append({"protocol": protocol, "address": _address(addr),
                                  "port": int(port, 16), "uid": int(fields[7]),
                                  "inode": fields[9]})
        except (OSError, UnicodeError, ValueError, IndexError) as exc:
            errors[protocol] = str(exc)
    return {"scope": "current Linux network namespace; point-in-time; no process or firewall verification",
            "complete": not errors, "listeners": listeners, "errors": errors}


def socket_mode_check(path: Path) -> dict:
    """Inspect this socket only; mode alone does not validate ownership or ACLs."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {"path": str(path), "status": "SKIP_HOST_REQUIRED", "reason": "not present"}
    except OSError as exc:
        return {"path": str(path), "status": "FAIL", "reason": str(exc)}
    if not stat.S_ISSOCK(mode):
        return {"path": str(path), "status": "FAIL", "reason": "not a socket"}
    return {"path": str(path), "status": "FAIL" if mode & stat.S_IWOTH else "PASS",
            "mode": oct(stat.S_IMODE(mode)), "scope": "mode only; ACLs and owner not checked"}


def yama_check(path: Path = Path("/proc/sys/kernel/yama/ptrace_scope")) -> dict:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        return {"path": str(path), "status": "SKIP_HOST_REQUIRED", "reason": "not present"}
    except (OSError, UnicodeError, ValueError) as exc:
        return {"path": str(path), "status": "FAIL", "reason": str(exc)}
    return {"path": str(path), "status": "PASS" if value >= 1 else "FAIL", "value": value}


def main() -> int:
    result = inventory()
    paths = {Path("/run/ydotoold.socket"), Path("/run/keyd.socket")}
    if os.environ.get("YDOTOOL_SOCKET"):
        paths.add(Path(os.environ["YDOTOOL_SOCKET"]))
    result["input_socket_modes"] = [socket_mode_check(p) for p in sorted(paths)]
    result["yama_ptrace_scope"] = yama_check()
    print(json.dumps(result, indent=2))
    checks = result["input_socket_modes"] + [result["yama_ptrace_scope"]]
    return 0 if result["complete"] and all(c["status"] == "PASS" for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
