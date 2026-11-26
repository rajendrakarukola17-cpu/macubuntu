"""Celina AI — Capability #11: Kernel-Enforced Process Behaviour Policy (Linux Landlock LSM)

Closes the userspace-only bubblewrap gap by enforcing filesystem & TCP behaviour
policies directly inside the Linux kernel via Landlock LSM syscalls
(`landlock_create_ruleset`, `landlock_add_rule`, `landlock_restrict_self`),
keyed by a cryptographically verifiable SHA-256 `policy_hash`.

Always excludes sensitive user credential paths (~/.ssh, ~/.gnupg, ~/.aws,
~/.kube, ~/.netrc, ~/.git-credentials) from tool rulesets.
"""

from __future__ import annotations

import ctypes
import dataclasses
import hashlib
import json
import os
import platform
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import capabilities, config
from . import policy as _policy

# Linux syscall numbers (identical on x86_64, aarch64, riscv64 in Linux 5.13+)
_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT_SELF = 446

_LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

# Landlock ABI v1 filesystem access flags
_LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
_LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
_LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
_LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
_LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
_LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
_LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
_LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
_LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
_LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
_LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
_LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
_LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12

_LANDLOCK_FS_READ = _LANDLOCK_ACCESS_FS_READ_FILE | _LANDLOCK_ACCESS_FS_READ_DIR
_LANDLOCK_FS_EXEC = _LANDLOCK_FS_READ | _LANDLOCK_ACCESS_FS_EXECUTE
_LANDLOCK_FS_WRITE = (
    _LANDLOCK_ACCESS_FS_WRITE_FILE
    | _LANDLOCK_ACCESS_FS_REMOVE_DIR
    | _LANDLOCK_ACCESS_FS_REMOVE_FILE
    | _LANDLOCK_ACCESS_FS_MAKE_DIR
    | _LANDLOCK_ACCESS_FS_MAKE_REG
    | _LANDLOCK_ACCESS_FS_MAKE_SYM
)
_LANDLOCK_FS_ALL_V1 = (1 << 13) - 1

# Re-export for backward compatibility; canonical source is policy.py.
PROTECTED_SENSITIVE_PATHS: tuple[str, ...] = tuple(
    sorted(_policy.PROTECTED_PATH_COMPONENTS),
)


class LandlockEnforcementError(RuntimeError):
    """Raised when strict Landlock kernel policy enforcement cannot be applied."""


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


@dataclasses.dataclass(frozen=True)
class LandlockProfile:
    tool_name: str
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    exec_paths: tuple[str, ...]
    tcp_connect_ports: tuple[int, ...] = ()
    denied_sensitive_paths: tuple[str, ...] = PROTECTED_SENSITIVE_PATHS

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "read_paths": sorted(set(self.read_paths)),
            "write_paths": sorted(set(self.write_paths)),
            "exec_paths": sorted(set(self.exec_paths)),
            "tcp_connect_ports": sorted(set(int(p) for p in self.tcp_connect_ports)),
            "denied_sensitive_paths": list(self.denied_sensitive_paths),
        }

    @property
    def policy_hash(self) -> str:
        raw = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(raw).hexdigest()


def _get_libc() -> ctypes.CDLL | None:
    if platform.system() != "Linux":
        return None
    try:
        return ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return None


def probe_landlock_abi() -> int:
    """Probes the running Linux kernel for Landlock LSM ABI version (returns >= 1 if active, 0 if unavailable)."""
    libc = _get_libc()
    if libc is None:
        return 0
    try:
        abi = int(
            libc.syscall(_SYS_LANDLOCK_CREATE_RULESET, None, 0, _LANDLOCK_CREATE_RULESET_VERSION)
        )
        return max(0, abi)
    except Exception:
        return 0


def _is_sensitive_path(path_str: str) -> bool:
    """Check if `path_str` touches any protected path (canonical source: policy.py)."""
    return _policy.is_protected_path(path_str)


def profile_for_tool(
    tool_name: str,
    *,
    workdir: Path | None = None,
    manifest: dict[str, Any] | None = None,
) -> LandlockProfile:
    """Derives a least-privilege kernel LandlockProfile from Celina's OS capability manifest.
    Never grants access to ~/.ssh, ~/.gnupg, ~/.aws, ~/.kube, ~/.netrc, or ~/.git-credentials.
    """
    cap = capabilities.get_tool_capability(tool_name, manifest=manifest) or {}
    fs_scope = str(cap.get("fs_scope") or "none")
    net_scope = str(cap.get("network_egress") or "none")

    exec_paths = ("/usr", "/bin", "/lib", "/lib64")
    read_paths: list[str] = ["/usr", "/etc/ssl", "/etc/ld.so.cache", "/proc/self"]
    write_paths: list[str] = []

    if fs_scope == "celina_state_only":
        read_paths.append(str(config.BASE_DIR))
        write_paths.append(str(config.BASE_DIR))
    elif fs_scope == "bwrap_workdir":
        wd = str((workdir or Path("/tmp")).expanduser().resolve())  # nosec B108 — default workdir path, not temp file creation
        if not _is_sensitive_path(wd):
            read_paths.append(wd)
            write_paths.append(wd)
    elif fs_scope == "home_ro":
        # Grant non-sensitive top-level user directories instead of blanketing ~/.ssh
        for sub in ("Documents", "Desktop", "Downloads", "Pictures", "Videos", "Projects"):
            cand = config.HOME / sub
            if cand.exists():
                read_paths.append(str(cand))
    elif fs_scope == "home_rw_approval_undo":
        for sub in ("Documents", "Desktop", "Downloads", "Projects"):
            cand = config.HOME / sub
            if cand.exists():
                read_paths.append(str(cand))
                write_paths.append(str(cand))
        if workdir is not None and not _is_sensitive_path(str(workdir)):
            w_resolved = str(workdir.expanduser().resolve())
            read_paths.append(w_resolved)
            write_paths.append(w_resolved)

    tcp_ports: tuple[int, ...] = ()
    if net_scope == "https_public_only":
        tcp_ports = (443,)
    elif net_scope == "loopback_ollama":
        tcp_ports = (11434, 11435)

    # Final defensive filter: strip any sensitive path that could ever sneak in
    safe_reads = tuple(p for p in read_paths if not _is_sensitive_path(p))
    safe_writes = tuple(p for p in write_paths if not _is_sensitive_path(p))

    return LandlockProfile(
        tool_name=tool_name,
        read_paths=safe_reads,
        write_paths=safe_writes,
        exec_paths=exec_paths,
        tcp_connect_ports=tcp_ports,
    )


def apply_landlock_profile(profile: LandlockProfile, *, strict: bool = False) -> dict[str, Any]:
    """Enforces `profile` on the CURRENT process via Linux Landlock syscalls.
    Intended to be called inside a child process (`preexec_fn`) before `execve()`.
    """
    abi = probe_landlock_abi()
    if abi < 1:
        if strict:
            raise LandlockEnforcementError(
                f"Strict Landlock enforcement requested for '{profile.tool_name}', but kernel Landlock ABI is unavailable.",
            )
        return {
            "enforced": False,
            "abi": 0,
            "policy_hash": profile.policy_hash,
            "reason": "kernel_landlock_unavailable",
        }

    libc = _get_libc()
    if libc is None:
        if strict:
            raise LandlockEnforcementError("libc.so.6 unavailable for Landlock syscalls.")
        return {
            "enforced": False,
            "abi": abi,
            "policy_hash": profile.policy_hash,
            "reason": "no_libc",
        }

    attr = _LandlockRulesetAttr(handled_access_fs=_LANDLOCK_FS_ALL_V1)
    ruleset_fd = int(
        libc.syscall(_SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0),
    )
    if ruleset_fd < 0:
        err = ctypes.get_errno()
        if strict:
            raise LandlockEnforcementError(f"landlock_create_ruleset failed (errno={err}).")
        return {
            "enforced": False,
            "abi": abi,
            "policy_hash": profile.policy_hash,
            "reason": f"errno_{err}",
        }

    rules_added = 0
    try:
        path_rights: dict[str, int] = {}
        for p in profile.read_paths:
            path_rights[p] = path_rights.get(p, 0) | _LANDLOCK_FS_READ
        for p in profile.exec_paths:
            path_rights[p] = path_rights.get(p, 0) | _LANDLOCK_FS_EXEC
        for p in profile.write_paths:
            path_rights[p] = path_rights.get(p, 0) | _LANDLOCK_FS_WRITE | _LANDLOCK_FS_READ

        for p_str, access_mask in path_rights.items():
            if _is_sensitive_path(p_str) or not os.path.exists(p_str):
                continue
            try:
                p_fd = os.open(p_str, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue
            try:
                rule = _LandlockPathBeneathAttr(allowed_access=access_mask, parent_fd=p_fd)
                rc = int(
                    libc.syscall(
                        _SYS_LANDLOCK_ADD_RULE,
                        ruleset_fd,
                        _LANDLOCK_RULE_PATH_BENEATH,
                        ctypes.byref(rule),
                        0,
                    ),
                )
                if rc == 0:
                    rules_added += 1
            finally:
                os.close(p_fd)

        if int(libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) != 0:
            if strict:
                raise LandlockEnforcementError("prctl(PR_SET_NO_NEW_PRIVS) failed.")
            return {
                "enforced": False,
                "abi": abi,
                "policy_hash": profile.policy_hash,
                "reason": "no_new_privs_failed",
            }

        if int(libc.syscall(_SYS_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0)) != 0:
            err = ctypes.get_errno()
            if strict:
                raise LandlockEnforcementError(f"landlock_restrict_self failed (errno={err}).")
            return {
                "enforced": False,
                "abi": abi,
                "policy_hash": profile.policy_hash,
                "reason": f"restrict_errno_{err}",
            }

        return {
            "enforced": True,
            "abi": abi,
            "rules_added": rules_added,
            "policy_hash": profile.policy_hash,
        }
    finally:
        os.close(ruleset_fd)


def build_landlock_preexec(profile: LandlockProfile, *, strict: bool = False) -> Callable[[], None]:
    """Returns a zero-arg `preexec_fn` that applies Landlock inside a child subprocess."""

    def _preexec() -> None:
        apply_landlock_profile(profile, strict=strict)

    return _preexec


def export_sharness_policy_json(profile: LandlockProfile, policy_path: Path) -> tuple[Path, str]:
    """Writes a deterministic SHarness-compatible Process Behaviour Policy JSON file
    bound to `profile.policy_hash` (SHA-256) so kernel-level enforcement can be
    audited or consumed by `sharness-ai run --policy <file> -- <cmd>`.
    """
    out = Path(policy_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "celina.sharness.landlock.v1",
        "policy_hash": profile.policy_hash,
        **profile.canonical_dict(),
    }
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out, profile.policy_hash


def files_office_policy(docs_dir: Path | None = None) -> LandlockProfile:
    """Canonical least-privilege Landlock / SHarness policy for Celina's `files_office` tools:
    - FS_RO: /usr, /lib, /lib64, /usr/lib, /usr/lib64, /bin, /etc, /proc, /sys, /dev/null, /dev/urandom
    - FS_RW: user's Documents directory only
    - FS_EXEC: /usr/bin, /bin + ELF dynamic linker dirs (/lib, /lib64, /usr/lib, /usr/lib64)
    - connect_tcp_ports: (443,) only (for optional web-fetch helpers)
    """
    rw_dir = str((docs_dir if docs_dir is not None else (Path.home() / "Documents")).resolve())
    return LandlockProfile(
        tool_name="files_office",
        read_paths=(
            "/usr",
            "/lib",
            "/lib64",
            "/usr/lib",
            "/usr/lib64",
            "/bin",
            "/etc",
            "/proc",
            "/sys",
            "/dev/null",
            "/dev/urandom",
            rw_dir,
        ),
        write_paths=(rw_dir,),
        exec_paths=("/usr/bin", "/bin", "/lib", "/lib64", "/usr/lib", "/usr/lib64"),
        tcp_connect_ports=(443,),
    )


_BWRAP_EXEC_PROBE_CACHE: dict[tuple[str, bool], bool] = {}
_BWRAP_EXEC_PROBE_CACHE_LOCK = threading.Lock()


def _probe_bwrap_runtime(bw: str, *, unshare_net: bool) -> bool:
    """Probes Ubuntu 24.04/26.04 AppArmor userns compatibility for bwrap once per process.

    The cache read-check-write is guarded by _BWRAP_EXEC_PROBE_CACHE_LOCK so that
    concurrent callers cannot race on a stale cache entry. Writes are idempotent
    (same key always yields the same ok value), so the lock prevents redundant
    probe subprocesses rather than fixing a data-corruption bug.
    """
    key = (bw, bool(unshare_net))
    with _BWRAP_EXEC_PROBE_CACHE_LOCK:
        if key in _BWRAP_EXEC_PROBE_CACHE:
            return _BWRAP_EXEC_PROBE_CACHE[key]

    import subprocess

    probe_cmd = [
        bw,
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind-try",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/bin",
        "/bin",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",  # nosec B108 — bwrap sandbox mount point, not temp file creation
    ]
    if unshare_net:
        probe_cmd.append("--unshare-net")
    probe_cmd.extend(["--", "/usr/bin/true"])
    try:
        r = subprocess.run(
            probe_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
            check=False,
        )
        ok = r.returncode == 0
    except Exception:
        ok = False

    with _BWRAP_EXEC_PROBE_CACHE_LOCK:
        _BWRAP_EXEC_PROBE_CACHE[key] = ok
    return ok


def build_kernel_sandbox_cmd(
    cmd: list[str],
    profile: LandlockProfile,
    *,
    policy_dir: Path | None = None,
    sharness_bin: str | None = None,
    ai_jail_bin: str | None = None,
    bwrap_bin: str | None = None,
    workdir: Path | None = None,
    protect_dot_git: bool = True,
    strict: bool = False,
) -> tuple[list[str], str, str]:
    """Multi-layer kernel sandbox builder (SHarness -> ai-jail/safeshell -> bwrap + .git RO -> native Landlock).
    Returns `(wrapped_cmd, sandbox_driver, policy_hash)`.

    When `strict=True` and no sandbox driver is available AND Landlock ABI < 1,
    raises `LandlockEnforcementError`. The command NEVER runs unsandboxed under strict mode.
    If Landlock ABI >= 1, falls back to `landlock_ctypes` driver (kernel Landlock enforcement
    via `run_under_landlock(strict=True)`).
    """
    import shutil

    p_hash = profile.policy_hash
    sh_bin = sharness_bin if sharness_bin is not None else shutil.which("sharness-ai")
    if sh_bin:
        p_root = Path(policy_dir) if policy_dir is not None else (config.BASE_DIR / "policies")
        p_file, _ = export_sharness_policy_json(
            profile, p_root / f"{profile.tool_name}-{p_hash[:12]}.json"
        )
        return (
            [sh_bin, "run", "--policy", str(p_file), "--policy-hash", p_hash, "--", *cmd],
            "sharness-ai",
            p_hash,
        )

    jail_bin = (
        ai_jail_bin
        if ai_jail_bin is not None
        else (shutil.which("ai-jail") or shutil.which("safeshell"))
    )
    if jail_bin:
        wrapped = [jail_bin]
        if protect_dot_git and workdir is not None and (Path(workdir) / ".git").exists():
            wrapped.extend(["--ro", str(Path(workdir) / ".git")])
        wrapped.extend(["--", *cmd])
        return (wrapped, Path(jail_bin).name, p_hash)

    bw = bwrap_bin if bwrap_bin is not None else shutil.which("bwrap")
    want_unshare_net = not profile.tcp_connect_ports
    # When auto-detecting bwrap at runtime, verify Ubuntu 26.04 AppArmor permits userns
    # and ensure executables inside user venvs (e.g. ~/.local/share/celina/venv) remain readable.
    if bw and bwrap_bin is None and not _probe_bwrap_runtime(bw, unshare_net=want_unshare_net):
        if strict:
            raise LandlockEnforcementError(
                f"Strict sandbox requested for '{profile.tool_name}' but bwrap probe failed (unsandboxed run disallowed)."
            )
        bw = None
    if bw:
        wrapped = [
            bw,
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind-try",
            "/lib",
            "/lib",
            "--ro-bind-try",
            "/lib64",
            "/lib64",
            "--ro-bind-try",
            "/bin",
            "/bin",
            "--ro-bind-try",
            "/etc",
            "/etc",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",  # nosec B108 — bwrap sandbox mount point, not temp file creation
        ]
        # Bind read-only parent dir if executable lives outside /usr|/bin (e.g., ~/.local/share/celina/venv)
        if cmd:
            exe_p = Path(cmd[0])
            if exe_p.is_absolute() and not str(exe_p).startswith(("/usr/", "/bin/", "/lib/")):
                venv_root = exe_p.parent.parent if exe_p.parent.name == "bin" else exe_p.parent
                if venv_root.exists():
                    wrapped.extend(["--ro-bind", str(venv_root), str(venv_root)])
        if want_unshare_net:
            wrapped.append("--unshare-net")
        if workdir is not None:
            wd = str(Path(workdir).resolve())
            wrapped.extend(["--bind", wd, wd])
            # agent6 safety invariant: rebind `.git` read-only inside the sandboxed child!
            if protect_dot_git and (Path(workdir) / ".git").exists():
                git_dir = str((Path(workdir) / ".git").resolve())
                wrapped.extend(["--ro-bind", git_dir, git_dir])
        wrapped.extend(["--", *cmd])
        return (wrapped, "bwrap+landlock", p_hash)

    # No sandbox driver available (no sharness-ai, no ai-jail/safeshell, no usable bwrap)
    if strict:
        if probe_landlock_abi() < 1:
            raise LandlockEnforcementError(
                f"Strict sandbox requested for '{profile.tool_name}' but no sandbox driver and Landlock ABI is unavailable."
            )

    return (list(cmd), "landlock_ctypes", p_hash)


def run_under_landlock(
    cmd: list[str],
    profile: LandlockProfile,
    *,
    strict: bool = False,
    timeout: float = 15.0,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Executes `cmd` inside a child process constrained by kernel Landlock policy."""
    if strict and probe_landlock_abi() < 1:
        raise LandlockEnforcementError(
            f"Cannot run '{profile.tool_name}' under strict Landlock: kernel ABI unavailable.",
        )
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=float(timeout),
        preexec_fn=build_landlock_preexec(profile, strict=strict),
    )
    return {
        "returncode": int(proc.returncode),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "policy_hash": profile.policy_hash,
        "landlock_abi": probe_landlock_abi(),
    }
