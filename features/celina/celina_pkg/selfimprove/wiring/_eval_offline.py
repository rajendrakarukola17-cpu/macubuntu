# Copyright 2026 Celina contributors.
"""Linux syscall filter for offline evaluator workers; not a general sandbox."""

from __future__ import annotations

import ctypes
import errno
import platform
import sys


class _Instruction(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]


class _Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("instructions", ctypes.POINTER(_Instruction))]


def _architecture() -> tuple[int, tuple[int, ...]]:
    if sys.platform != "linux":
        msg = "offline evaluator requires Linux seccomp"
        raise OSError(msg)
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        numbers = (41, 42, 44, 46, 307, 425)
        return 0xC000003E, numbers + tuple(n | 0x40000000 for n in numbers)
    if machine in {"aarch64", "arm64"}:
        return 0xC00000B7, (198, 203, 206, 211, 269, 425)
    msg = f"unsupported seccomp architecture: {machine}"
    raise OSError(msg)


def _program(architecture: int, numbers: tuple[int, ...]) -> tuple[_Program, object]:
    load, equal, ret = 0x20, 0x15, 0x06
    instructions = [
        _Instruction(load, 0, 0, 4),
        _Instruction(equal, 1, 0, architecture),
        _Instruction(ret, 0, 0, 0x80000000),
        _Instruction(load, 0, 0, 0),
    ]
    for number in numbers:
        instructions.extend((
            _Instruction(equal, 0, 1, number),
            _Instruction(ret, 0, 0, 0x00050000 | errno.EPERM),
        ))
    instructions.append(_Instruction(ret, 0, 0, 0x7FFF0000))
    array = (_Instruction * len(instructions))(*instructions)
    return _Program(len(array), array), array


def install_network_filter() -> None:
    """Permanently deny network syscalls in this Linux process and its children.

    This blocks ordinary socket creation, connection and transmission, including
    io_uring setup, but is not a filesystem, process or general security sandbox.
    Call only in a dedicated trusted worker; failure raises before Celina imports.
    """
    architecture, numbers = _architecture()
    program, backing = _program(architecture, numbers)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, "cannot enable no-new-privileges")
    if libc.prctl(22, 2, ctypes.addressof(program), 0, 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, "cannot install offline seccomp filter")
    del backing
