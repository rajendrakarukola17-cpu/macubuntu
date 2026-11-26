"""macubuntu celina_pkg.neural.npu_worker — Single-Owner NPU Worker.

Runs as a single systemd --user service inside celina-ai.slice, holding one
persistent NPU/CPU context over /run/user/$UID/celina/npu.sock with
ahead-of-time blob caching.

Ubuntu 26.04 constraint: Intel intel_vpu caps concurrent device contexts at
64 (ivpu_open(): -16). This single-owner worker holds exactly one context,
preventing context exhaustion from multiple processes opening /dev/accel/accel0.

stdlib-only for the socket server; openvino-genai is lazily imported so the
base daemon stays light when no NPU is present.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import socket
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any

MAX_WAV_BYTES = 32 * 1024 * 1024  # 32 MiB (~17 min of 16 kHz mono PCM16)
MAX_REQ_BYTES = 16 * 1024  # 16 KiB max JSON control request
_LANG_RE = re.compile(r"^<\|[a-z]{2,3}\|>$")


def _default_runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "celina"


def _default_cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "celina" / "npu_blobs"


def _load_wav_16k_mono(
    wav_path: Path, allowed_roots: tuple[Path, ...],
) -> list[float]:
    """Safely load a bounded 16 kHz mono 16-bit PCM WAV file.

    Path containment + size bounds prevent OOM DoS and arbitrary file reads.
    Rejects FIFOs/symlinks and non-PCM16 formats to avoid hangs/corruption.
    """
    resolved = wav_path.expanduser().resolve()
    if allowed_roots and not any(
        resolved == root or root in resolved.parents for root in allowed_roots
    ):
        raise PermissionError(f"wav_path outside allowed directories: {resolved}")
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError("wav_path must be an existing regular file")
    size = resolved.stat().st_size
    if size <= 0 or size > MAX_WAV_BYTES:
        raise ValueError(f"WAV size {size} bytes outside allowed range (1..{MAX_WAV_BYTES})")

    np = __import__("numpy")
    with wave.open(str(resolved), "rb") as wf:
        if wf.getcomptype() != "NONE" or wf.getsampwidth() != 2:
            raise ValueError("Audio must be uncompressed 16-bit PCM WAV")
        if wf.getframerate() != 16000 or wf.getnchannels() != 1:
            raise ValueError("Audio must be 16 kHz mono PCM WAV")
        max_frames = MAX_WAV_BYTES // 2
        nframes = min(wf.getnframes(), max_frames)
        pcm = np.frombuffer(wf.readframes(nframes), dtype=np.int16)
        return (pcm.astype(np.float32) / 32768.0).tolist()


def _verify_peer_uid(writer: asyncio.StreamWriter) -> bool:
    """Enforce same-UID peer authentication over UDS via SO_PEERCRED."""
    sock = writer.get_extra_info("socket")
    if sock is None or not hasattr(socket, "SO_PEERCRED"):
        return False
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return False
    _pid, peer_uid, _gid = struct.unpack("3i", creds)
    return peer_uid == os.getuid()


class SingleOwnerNPUWorker:
    """Holds a single persistent OpenVINO GenAI WhisperPipeline context and
    serves transcription over a 0700 Unix domain socket.

    Lazy-imports openvino_genai so the module imports cleanly even when the
    package is not installed (NPU absent). Falls back to CPU transparently.

    Zero filesystem mutations at import time or in __init__; directories and
    sockets are created lazily under os.umask(0o077) with strict 0700/0600
    modes (no TOCTOU race).
    """

    def __init__(
        self,
        whisper_model_dir: Path,
        hw_profile_path: Path = Path("/etc/macubuntu/hardware.json"),
        cache_dir: Path | None = None,
        sock_path: Path | None = None,
        allowed_wav_roots: tuple[Path, ...] | None = None,
    ) -> None:
        self.whisper_model_dir = Path(whisper_model_dir)
        self.hw_profile_path = Path(hw_profile_path)
        # Cache dir created lazily (respect XDG_CACHE_HOME); never at __init__.
        self.cache_dir = cache_dir or _default_cache_dir()
        # Socket path: CLI arg > env > XDG_RUNTIME_DIR default.
        if sock_path is not None:
            self.sock_path = Path(sock_path)
        else:
            self.sock_path = _default_runtime_dir() / "npu.sock"
        # Allowed WAV roots: defaults to XDG_RUNTIME_DIR/celina, XDG_DATA_HOME/celina, tempdir.
        if allowed_wav_roots is not None:
            self.allowed_wav_roots = tuple(Path(r) for r in allowed_wav_roots)
        else:
            data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
            self.allowed_wav_roots = (
                _default_runtime_dir(),
                data_home / "celina",
                Path(tempfile.gettempdir()),
            )

        # Read hardware profile for NPU vendor info (fail-closed to CPU).
        self.device = "CPU"
        try:
            hw = json.loads(self.hw_profile_path.read_text(encoding="utf-8"))
            if (
                hw.get("npu_vendor") == "intel"
                or hw.get("npu_driver") == "intel_vpu"
                or hw.get("npu_backend") == "openvino-npu"
            ):
                self.device = "NPU"
        except Exception:
            pass

        self._pipe: Any = None
        self._lock = asyncio.Lock()
        self._started = False
        self._server: asyncio.AbstractServer | None = None

    def _ensure_cache_dir(self) -> None:
        """Create the cache dir lazily with 0700 perms (respects XDG_CACHE_HOME)."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cache_dir, 0o700)

    def _ensure_pipeline(self) -> Any:
        """Create the WhisperPipeline once, then reuse it (single context).

        Runs inside asyncio.to_thread() so OpenVINO graph compilation never
        blocks the event loop.
        """
        if self._pipe is not None:
            return self._pipe

        import openvino_genai as ov_genai  # Lazy import

        if not self.whisper_model_dir.is_dir():
            raise FileNotFoundError(
                f"Whisper model dir not found: {self.whisper_model_dir}",
            )

        self._ensure_cache_dir()
        cfg = {
            "CACHE_DIR": str(self.cache_dir),
            "CACHE_MODE": "OPTIMIZE_SPEED",
        }
        try:
            self._pipe = ov_genai.WhisperPipeline(
                str(self.whisper_model_dir), self.device, **cfg,
            )
        except RuntimeError:
            # Graceful fallback to CPU if NPU firmware/driver is unavailable
            self.device = "CPU"
            self._pipe = ov_genai.WhisperPipeline(
                str(self.whisper_model_dir), "CPU", **cfg,
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"NPU pipeline init failed: {exc}") from exc
        return self._pipe

    async def transcribe_file(
        self, wav_path: Path, language: str = "<|en|>",
    ) -> dict[str, Any]:
        """Transcribe a WAV file using the single persistent NPU/CPU context.

        All blocking work (model compilation, WAV I/O, inference) runs inside
        asyncio.to_thread() so the asyncio event loop never stalls.
        """
        async with self._lock:

            def _infer() -> dict[str, Any]:
                pipe = self._ensure_pipeline()
                audio = _load_wav_16k_mono(Path(wav_path), self.allowed_wav_roots)
                gen_cfg = pipe.get_generation_config()
                gen_cfg.language = language
                gen_cfg.task = "transcribe"
                res = pipe.generate(audio, gen_cfg)
                return {"device": self.device, "text": str(res).strip()}

            return await asyncio.to_thread(_infer)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one MCP-style JSON request over the UDS socket.

        Enforces SO_PEERCRED same-UID verification, validates request shape,
        and returns quietly on EOF/disconnect.
        """
        # Same-UID peer auth — close immediately on mismatch.
        if not _verify_peer_uid(writer):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return

        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=30.0)
            if not raw:
                # Clean EOF / disconnect — return silently, no error to closed peer.
                return
            if len(raw) > MAX_REQ_BYTES:
                resp = {"error": "request too large"}
                writer.write((json.dumps(resp) + "\n").encode("utf-8"))
                await writer.drain()
                return
            try:
                req = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                resp = {"error": "invalid JSON"}
                writer.write((json.dumps(resp) + "\n").encode("utf-8"))
                await writer.drain()
                return
            if not isinstance(req, dict):
                resp = {"error": "request must be a JSON object"}
                writer.write((json.dumps(resp) + "\n").encode("utf-8"))
                await writer.drain()
                return

            op = req.get("op")
            if op == "transcribe":
                wav_path = req.get("wav_path")
                if not isinstance(wav_path, str) or not wav_path:
                    resp = {"error": "wav_path must be a non-empty string"}
                else:
                    language = req.get("language", "<|en|>")
                    if not isinstance(language, str) or not _LANG_RE.match(language):
                        resp = {"error": f"invalid language token: {language!r}"}
                    else:
                        try:
                            resp = await self.transcribe_file(Path(wav_path), language)
                        except (RuntimeError, OSError, ValueError, PermissionError) as exc:
                            resp = {"error": str(exc)}
            elif op == "health":
                resp = {"status": "ok", "device": self.device}
            else:
                resp = {"error": f"unknown op: {op}"}
            writer.write((json.dumps(resp) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception as exc:
            try:
                writer.write((json.dumps({"error": str(exc)}) + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def serve_unix_socket(self) -> None:
        """Serve forever on self.sock_path (mode 0600, parent 0700).

        Binds under os.umask(0o077) with no TOCTOU race, cleans up stale
        sockets, and unlinks on shutdown.
        """
        sock_path = self.sock_path
        sock_dir = sock_path.parent
        sock_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(sock_dir, 0o700)

        # Remove stale socket before binding.
        sock_path.unlink(missing_ok=True)

        old_umask = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client, path=str(sock_path), limit=MAX_REQ_BYTES,
            )
            os.chmod(sock_path, 0o600)
        finally:
            os.umask(old_umask)

        self._started = True

        # Install signal handlers for clean socket cleanup on shutdown.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown)

        async with self._server:
            await self._server.serve_forever()

    def _shutdown(self) -> None:
        """Clean shutdown: stop server and unlink socket."""
        if self._server is not None:
            self._server.close()
        try:
            self.sock_path.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:  # pragma: no cover - CLI entry point
    import argparse

    parser = argparse.ArgumentParser(description="Celina single-owner NPU worker")
    parser.add_argument(
        "--whisper-model-dir",
        required=True,
        help="Directory containing OpenVINO Whisper IR model files",
    )
    parser.add_argument(
        "--hw-profile",
        default="/etc/macubuntu/hardware.json",
        help="Path to hardware.json for NPU vendor detection",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Directory for ahead-of-time compiled NPU blobs",
    )
    parser.add_argument(
        "--sock-path",
        default=None,
        help="Unix domain socket path (default: $XDG_RUNTIME_DIR/celina/npu.sock)",
    )
    args = parser.parse_args()

    worker = SingleOwnerNPUWorker(
        whisper_model_dir=Path(args.whisper_model_dir),
        hw_profile_path=Path(args.hw_profile),
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        sock_path=Path(args.sock_path) if args.sock_path else None,
    )
    asyncio.run(worker.serve_unix_socket())


if __name__ == "__main__":  # pragma: no cover
    main()
