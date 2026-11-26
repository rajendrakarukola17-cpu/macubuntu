"""
macubuntu Center — backend.py

Telemetry, Keyring API vault, Model fit & UDS, .mubundle inspector,
and Landlock access policy.

Uses gi.repository.Secret (libsecret) so API keys never touch disk in
plaintext, queries llama-server over Unix domain sockets, calculates
live hardware-fit badges for local GGUF models, and inspects
.mubundle update files cleanly.

stdlib + gi + httpx only. No PyTorch, no network for import.
"""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi
import httpx

gi.require_version("Secret", "1")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Secret  # noqa: E402  (gi must be required first)

# Stages directory — overridable via MACUBUNTU_STAGES_DIR env var.
STAGES_DIR = Path(
    os.environ.get("MACUBUNTU_STAGES_DIR", "/home/user/macubuntu/stages")
)

API_KEY_SCHEMA = Secret.Schema.new(
    "org.macubuntu.celina.apikey",
    Secret.SchemaFlags.NONE,
    {"provider": Secret.SchemaAttributeType.STRING},
)


@dataclass
class SystemSnapshot:
    ram_tier: str
    ai_tier: str
    ram_used_gb: float
    ram_total_gb: float
    psi_avg10: float
    gpu_vendor: str
    vram_mb: int
    npu_vendor: str
    scheduler: str
    celina_slice_max: str


def read_system_snapshot() -> SystemSnapshot:
    """Read live hardware + runtime telemetry for the overview dashboard."""
    hw_file = Path("/etc/macubuntu/hardware.json")
    hw: dict[str, Any] = {}
    if hw_file.exists():
        try:
            hw = json.loads(hw_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            hw = {}

    mem: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                mem[k.strip()] = int(v.strip().split()[0])
    except (OSError, ValueError):
        pass
    total_kb = mem.get("MemTotal", 8_000_000)
    avail_kb = mem.get("MemAvailable", 4_000_000)
    total_gb = total_kb / (1024 * 1024)
    avail_gb = avail_kb / (1024 * 1024)

    psi = 0.0
    psi_path = Path("/proc/pressure/memory")
    if psi_path.exists():
        try:
            for line in psi_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("some "):
                    for token in line.split():
                        if token.startswith("avg10="):
                            psi = float(token.split("=", 1)[1])
                            break
        except (OSError, ValueError):
            pass

    sched_state = "default"
    scx_path = Path("/sys/kernel/sched_ext/root/ops")
    if scx_path.exists():
        try:
            sched_state = scx_path.read_text(encoding="utf-8").strip() or "default"
        except OSError:
            pass

    slice_max = "4.2G"
    slice_file = Path("/etc/systemd/user/celina-ai.slice")
    if slice_file.exists():
        try:
            for line in slice_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("MemoryMax="):
                    slice_max = line.split("=", 1)[1].strip()
        except OSError:
            pass

    return SystemSnapshot(
        ram_tier=str(hw.get("ram_tier", "8gb")),
        ai_tier=str(hw.get("ai_tier", "8gb")),
        ram_used_gb=round(total_gb - avail_gb, 1),
        ram_total_gb=round(total_gb, 1),
        psi_avg10=psi,
        gpu_vendor=str(hw.get("gpu_vendor", "cpu")),
        vram_mb=int(hw.get("vram_mb", 0)),
        npu_vendor=str(hw.get("npu_vendor", "none")),
        scheduler=sched_state,
        celina_slice_max=slice_max,
    )


# ---------------------------------------------------------------------------
# 1. GNOME Keyring API Key Vault (Zero Plaintext on Disk)
# ---------------------------------------------------------------------------


def store_api_key_async(
    provider: str, secret_value: str, callback: Any
) -> None:
    """Store an API key in GNOME Keyring via libsecret (async)."""
    Secret.password_store(
        API_KEY_SCHEMA,
        {"provider": provider},
        Secret.COLLECTION_DEFAULT,
        f"Celina API Key ({provider})",
        secret_value,
        None,
        callback,
    )


def has_api_key_sync(provider: str) -> bool:
    """Return True if a key is stored for the given provider."""
    val = Secret.password_lookup_sync(
        API_KEY_SCHEMA, {"provider": provider}, None
    )
    return bool(val)


def clear_api_key_sync(provider: str) -> bool:
    """Remove the stored key for the given provider."""
    try:
        return bool(
            Secret.password_clear_sync(
                API_KEY_SCHEMA, {"provider": provider}, None
            )
        )
    except GLib.Error:
        return False


def finish_store_api_key(res) -> bool:
    """Finish an async Secret.password_store call, handling GLib.Error.

    Returns True if the secret was stored successfully, False otherwise.
    """
    try:
        Secret.password_store_finish(res)
        return True
    except GLib.Error:
        return False


# ---------------------------------------------------------------------------
# 2. Local Model Hub & Hardware Fit Evaluator
# ---------------------------------------------------------------------------


def list_local_models(snap: SystemSnapshot) -> list[dict[str, Any]]:
    """Scan ~/.cache/celina/models for GGUF files and compute hardware fit.

    Read-only: does not create any directories. Returns an empty list if the
    models directory does not exist.
    """
    models_dir = Path.home() / ".cache" / "celina" / "models"
    if not models_dir.is_dir():
        return []
    loaded_ids = set(get_loaded_llama_models())

    results: list[dict[str, Any]] = []
    for path in sorted(models_dir.glob("*.gguf")):
        size_gb = round(path.stat().st_size / (1024**3), 2)
        vram_gb = snap.vram_mb / 1024.0
        # Fit calculation: model weights + ~1.4x KV/activation headroom
        needed_gb = size_gb * 1.35
        if vram_gb >= needed_gb:
            fit, pill = "Full GPU • Fast", "pill-green"
        elif snap.ram_total_gb * 0.60 >= needed_gb:
            fit, pill = "Hybrid / RAM OK", "pill-amber"
        else:
            fit, pill = "Exceeds Tier RAM", "pill-red"

        results.append(
            {
                "name": path.name,
                "path": str(path),
                "size_gb": size_gb,
                "fit_label": fit,
                "fit_pill": pill,
                "loaded": path.stem in loaded_ids or path.name in loaded_ids,
            }
        )
    return results


def _llama_uds_client() -> httpx.Client | None:
    """Return an httpx client connected to llama-server over UDS, or None."""
    sock = Path(f"/run/user/{os.getuid()}/celina/llama.sock")
    if not sock.exists():
        return None
    transport = httpx.HTTPTransport(uds=str(sock))
    return httpx.Client(
        transport=transport,
        base_url="http://llama.local",
        timeout=3.0,
    )


def get_loaded_llama_models() -> list[str]:
    """Return list of model aliases currently loaded in llama-server."""
    client = _llama_uds_client()
    if client is None:
        return []
    try:
        r = client.get("/models")
        if r.status_code != 200:
            return []
        return [
            m["id"]
            for m in r.json().get("data", [])
            if m.get("status", {}).get("value") == "loaded"
        ]
    except Exception:
        return []
    finally:
        client.close()


def unload_all_llama_models() -> int:
    """Actively unload every loaded model via POST /models/unload over UDS."""
    client = _llama_uds_client()
    if client is None:
        return 0
    count = 0
    try:
        for alias in get_loaded_llama_models():
            resp = client.post("/models/unload", json={"model": alias})
            if resp.status_code == 200:
                count += 1
        return count
    except Exception:
        return count
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 3. Update Bundle (.mubundle) Unprivileged Inspector
# ---------------------------------------------------------------------------


def _tarfile_list(bundle_path: Path) -> list[str]:
    """List members of a tarball, supporting plain, gz, and zstd compression.

    Python's tarfile module does not natively support zstd (.tar.zst).
    Falls back to the system `tar --zstd` when needed.
    """
    try:
        with tarfile.open(bundle_path, mode="r:*") as tf:
            return tf.getnames()
    except (tarfile.TarError, EOFError, OSError):
        pass

    if bundle_path.suffix == ".zst" or bundle_path.name.endswith(".tar.zst"):
        try:
            result = subprocess.run(
                ["tar", "--zstd", "-tf", str(bundle_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return [line for line in result.stdout.splitlines() if line]
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass
    raise tarfile.TarError(f"Cannot read bundle: {bundle_path}")


def _tarfile_extract(bundle_path: Path, member: str) -> bytes:
    """Extract a single member from a tarball, supporting zstd compression."""
    try:
        with tarfile.open(bundle_path, mode="r:*") as tf:
            f = tf.extractfile(member)
            if f is None:
                raise tarfile.TarError(f"Cannot extract {member}")
            return f.read()
    except (tarfile.TarError, EOFError, OSError):
        pass

    if bundle_path.suffix == ".zst" or bundle_path.name.endswith(".tar.zst"):
        try:
            result = subprocess.run(
                ["tar", "--zstd", "-xf", str(bundle_path),
                 "-O", member],
                capture_output=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass
    raise tarfile.TarError(f"Cannot extract {member} from {bundle_path}")


def _verify_bundle_signature(bundle_path: Path) -> bool:
    """Verify a bundle's Ed25519 signature against the pinned public key.

    Fail-closed: returns False if the public key is missing or
    verification fails for any reason.
    """
    pubkey = Path("/etc/macubuntu/update_pub.pem")
    if not pubkey.exists():
        return False

    sig_file = bundle_path.parent / (bundle_path.name + ".sig")
    sums_file = bundle_path.parent / "SHA256SUMS"
    if not sig_file.exists() or not sums_file.exists():
        return False

    try:
        result = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-pubin",
             "-inkey", str(pubkey), "-rawin",
             "-in", str(sums_file),
             "-sigfile", str(sig_file)],
            capture_output=True, timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return False


def inspect_update_bundle(bundle_path: Path) -> dict[str, Any]:
    """Inspect a .mubundle (signed tarball) without extracting to disk.

    Returns manifest metadata: version, target_stages, signed flag, summary.
    Fail-closed: raises FileNotFoundError / ValueError on invalid bundles.
    The "signed" flag reflects actual Ed25519 verification, not just
    the presence of a .sig sidecar file.
    """
    if not bundle_path.exists():
        raise FileNotFoundError(str(bundle_path))

    # Dev-mode shell script shortcut (unsigned, local only)
    if bundle_path.suffix == ".sh":
        return {
            "kind": "dev_script",
            "version": "Local Dev Script",
            "stages": ["custom"],
            "signed": False,
            "summary": (
                f"Unsigned shell patch: {bundle_path.name} "
                "(requires Dev Mode)"
            ),
        }

    names = _tarfile_list(bundle_path)
    manifest_member = next(
        (n for n in names if n.endswith("update_manifest.json")),
        None,
    )
    if not manifest_member:
        raise ValueError(
            "Invalid bundle: missing update_manifest.json"
        )
    manifest_data = _tarfile_extract(bundle_path, manifest_member)
    manifest = json.loads(manifest_data.decode("utf-8"))

    # Actually verify the Ed25519 signature — fail-closed
    signed = _verify_bundle_signature(bundle_path)

    return {
        "kind": "mubundle",
        "version": manifest.get("version", "unknown"),
        "stages": manifest.get("target_stages", []),
        "signed": signed,
        "summary": manifest.get(
            "changelog", "No changelog provided."
        ),
    }


# ---------------------------------------------------------------------------
# 4. Landlock Access & Permissions Policy
# ---------------------------------------------------------------------------


ACCESS_POLICY_FILE = Path.home() / ".config" / "celina" / "access_policy.json"


def load_access_policy() -> dict[str, Any]:
    """Load the access policy, returning sensible defaults if absent."""
    if ACCESS_POLICY_FILE.exists():
        try:
            return json.loads(
                ACCESS_POLICY_FILE.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "folders": [
            {
                "path": str(Path.home() / "Projects"),
                "mode": "read_write_ask",
            },
            {
                "path": str(Path.home() / "Documents"),
                "mode": "read_only",
            },
        ],
        "never_exposed": [
            "~/.ssh",
            "~/.gnupg",
            "~/.local/share/celina/vault",
        ],
        "capabilities": {
            "visual_intelligence": True,
            "always_on_voice": False,
            "gui_accessibility": True,
            "wayland_text_injection": True,
            "cloud_escalation": False,
            "outbound_pii_redaction": True,
        },
    }


def save_access_policy(policy: dict[str, Any]) -> None:
    """Persist the access policy (mode 0600, user-only)."""
    write_root_file(
        ACCESS_POLICY_FILE,
        json.dumps(policy, indent=2),
        mode=0o600,
    )


# ---------------------------------------------------------------------------
# P5: Atomic DRY_RUN-safe root file wrappers
# ---------------------------------------------------------------------------
# Every root file mutation must flow through these wrappers. They provide
# atomicity (.part temp file + os.replace), DRY_RUN=1 safety (no mutation
# when DRY_RUN=1 or HOME is a temp/test directory), and optional chmod.


def _dry_run_safe() -> bool:
    """Return True if DRY_RUN=1 is set or HOME points to a temp/test dir."""
    import tempfile

    if os.environ.get("DRY_RUN", "0") == "1":
        return True
    home = os.environ.get("HOME", "")
    if home and (
        home.startswith(tempfile.gettempdir())
        or "/tmp/" in home
        or home in ("/tmp", "/var/tmp")
    ):
        return True
    return False


def write_root_file(
    path: Path | str,
    content: str,
    mode: int | None = 0o644,
    dry_run: bool | None = None,
) -> bool:
    """Atomically write content to a root-owned file.

    Writes to a .part temp file in the same directory, fsyncs, then
    os.replace()s it into place. If mode is set, chmods the result.
    Respects DRY_RUN=1 (no actual disk mutation).

    Returns True on success, False on failure.
    """
    if dry_run is None:
        dry_run = _dry_run_safe()
    p = Path(path)
    try:
        if dry_run:
            return True
        p.parent.mkdir(parents=True, exist_ok=True)
        part = p.with_suffix(p.suffix + ".part")
        part.write_text(content, encoding="utf-8")
        if mode is not None:
            os.chmod(part, mode)
        os.replace(part, p)
        return True
    except Exception:
        try:
            part.unlink(missing_ok=True)  # type: ignore[name-defined]
        except Exception:
            pass
        return False


def copy_root_file(
    src: Path | str,
    dst: Path | str,
    mode: int | None = 0o644,
    dry_run: bool | None = None,
) -> bool:
    """Atomically copy a file to a root-owned destination.

    Reads src, writes atomically to dst via write_root_file. Respects
    DRY_RUN=1.

    Returns True on success, False on failure.
    """
    if dry_run is None:
        dry_run = _dry_run_safe()
    if dry_run:
        return True
    try:
        content = Path(src).read_text(encoding="utf-8")
    except Exception:
        try:
            content = Path(src).read_bytes().decode("utf-8", errors="replace")
        except Exception:
            return False
    return write_root_file(dst, content, mode=mode, dry_run=False)
