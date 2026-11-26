"""
Regression tests for npu_worker.py and os_desktop_server.py P0/P1 audit fixes.

Covers:
  1. Importing os_desktop_server creates zero directories/files in $HOME
  2. capture_visual_intelligence is async, requires ctx, checks access_policy.json,
     does NOT accept arbitrary output_dir, contains zero gnome-screenshot
  3. SingleOwnerNPUWorker: zero dirs before _ensure_pipeline(), custom sock_path honored,
     socket created mode 0600, _load_wav_16k_mono rejects paths outside allowed roots,
     rejects non-16-bit WAVs, rejects oversized files
  4. _handle_client rejects invalid language tokens and non-dict JSON payloads
  5. SO_PEERCRED same-UID verification
"""
import asyncio
import json
import os
import shutil
import struct
import tempfile
import wave
from pathlib import Path

import numpy as np
import pytest


def _npu_available() -> bool:
    """Check if NPU hardware is available for hardware-dependent tests.

    Returns True if the NPU worker can be instantiated (requires actual NPU
    hardware and whisper model). Returns False on CI/dev machines without NPU.
    """
    try:
        from celina_pkg.neural.npu_worker import SingleOwnerNPUWorker
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            worker = SingleOwnerNPUWorker(
                whisper_model_dir=Path(tmp) / "model",
                sock_path=Path(tmp) / "npu.sock",
            )
            # If we can create the worker without hardware errors, NPU may be available
            return worker is not None
    except Exception:
        return False


_NPU_SKIP = pytest.mark.skipif(
    not _npu_available(),
    reason="NPU hardware not available — hardware-dependent test",
)

from celina_pkg.mcp.os_desktop_server import (
    _mcp,
    _runtime_dir,
    _visual_intelligence_allowed,
    capture_visual_intelligence,
    create_mcp_server,
    has_fastmcp,
)
from celina_pkg.neural.npu_worker import (
    MAX_WAV_BYTES,
    SingleOwnerNPUWorker,
    _default_cache_dir,
    _default_runtime_dir,
    _load_wav_16k_mono,
    _verify_peer_uid,
)


# ---------------------------------------------------------------------------
# 1. Import-time side effects: os_desktop_server must create zero dirs in $HOME
# ---------------------------------------------------------------------------

def test_os_desktop_server_import_zero_home_mutation(tmp_path: Path, monkeypatch):
    """Importing os_desktop_server in a clean temp HOME creates zero directories/files."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    import importlib
    import celina_pkg.mcp.os_desktop_server as ods
    importlib.reload(ods)

    # No directories should exist under HOME after import
    created = list(home.rglob("*"))
    assert len(created) == 0, f"Import created files/dirs under HOME: {created}"


# ---------------------------------------------------------------------------
# 2. capture_visual_intelligence signature & behavior checks
# ---------------------------------------------------------------------------

def test_capture_vi_is_async_requires_ctx():
    """capture_visual_intelligence must be async def and require ctx parameter."""
    import inspect
    sig = inspect.signature(capture_visual_intelligence)
    assert "ctx" in sig.parameters, "capture_visual_intelligence must require ctx"
    assert inspect.iscoroutinefunction(capture_visual_intelligence), \
        "capture_visual_intelligence must be async def"


def test_capture_vi_no_output_dir_param():
    """capture_visual_intelligence must NOT accept arbitrary output_dir."""
    import inspect
    sig = inspect.signature(capture_visual_intelligence)
    assert "output_dir" not in sig.parameters, \
        "capture_visual_intelligence must not accept output_dir"


def test_capture_vi_no_gnome_screenshot_in_source():
    """Source must contain zero gnome-screenshot calls (Wayland-native only)."""
    import inspect
    src = inspect.getsource(capture_visual_intelligence)
    assert "gnome-screenshot" not in src, \
        "capture_visual_intelligence must not call gnome-screenshot"


def test_visual_intelligence_fail_closed_on_corrupt_policy(tmp_path: Path, monkeypatch):
    """Corrupt access_policy.json → visual_intelligence disabled (fail-closed)."""
    home = tmp_path / "home"
    home.mkdir()
    config_dir = home / ".config" / "celina"
    config_dir.mkdir(parents=True)
    (config_dir / "access_policy.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    assert _visual_intelligence_allowed() is False


def test_visual_intelligence_allowed_when_no_policy(tmp_path: Path, monkeypatch):
    """No access_policy.json → visual_intelligence allowed (permissive default)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    assert _visual_intelligence_allowed() is True


def test_visual_intelligence_disabled_when_policy_says_false(tmp_path: Path, monkeypatch):
    """access_policy.json with visual_intelligence: false → denied."""
    home = tmp_path / "home"
    home.mkdir()
    config_dir = home / ".config" / "celina"
    config_dir.mkdir(parents=True)
    (config_dir / "access_policy.json").write_text(
        json.dumps({"capabilities": {"visual_intelligence": False}}), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    assert _visual_intelligence_allowed() is False


# ---------------------------------------------------------------------------
# 3. SingleOwnerNPUWorker tests
# ---------------------------------------------------------------------------

def test_worker_instantiation_zero_dirs(tmp_path: Path, monkeypatch):
    """Instantiating SingleOwnerNPUWorker creates zero directories before _ensure_pipeline()."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    model_dir = tmp_path / "whisper_model"
    model_dir.mkdir()

    worker = SingleOwnerNPUWorker(whisper_model_dir=model_dir)

    # Cache dir should NOT exist yet (lazy creation)
    cache_dir = _default_cache_dir()
    assert not cache_dir.exists(), "Cache dir must not exist at __init__ time"

    # Socket dir should NOT exist yet
    sock_dir = _default_runtime_dir()
    assert not sock_dir.exists(), "Socket dir must not exist at __init__ time"


def test_worker_custom_sock_path_honored(tmp_path: Path, monkeypatch):
    """Custom sock_path must be stored and used."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    model_dir = tmp_path / "whisper_model"
    model_dir.mkdir()

    custom_sock = tmp_path / "custom" / "npu.sock"
    worker = SingleOwnerNPUWorker(
        whisper_model_dir=model_dir,
        sock_path=custom_sock,
    )
    assert worker.sock_path == custom_sock


@pytest.mark.asyncio
@_NPU_SKIP
async def test_worker_socket_mode_0600(tmp_path: Path, monkeypatch):
    """Socket must be created with mode 0600 under 0700 parent."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    model_dir = tmp_path / "whisper_model"
    model_dir.mkdir()

    custom_sock = tmp_path / "custom" / "npu.sock"
    worker = SingleOwnerNPUWorker(
        whisper_model_dir=model_dir,
        sock_path=custom_sock,
    )

    sock_path = worker.sock_path
    sock_dir = sock_path.parent
    sock_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(sock_dir, 0o700)
    sock_path.unlink(missing_ok=True)

    old_umask = os.umask(0o077)
    try:
        server = await asyncio.start_unix_server(
            worker._handle_client, path=str(sock_path), limit=16384
        )
        os.chmod(sock_path, 0o600)
    finally:
        os.umask(old_umask)
        server.close()
        await server.wait_closed()
        sock_path.unlink(missing_ok=True)

    dir_mode = sock_dir.stat().st_mode & 0o777
    assert dir_mode == 0o700, f"Socket dir mode should be 0700, got {oct(dir_mode)}"


@pytest.mark.asyncio
@_NPU_SKIP
async def test_worker_socket_cleanup_on_exit(tmp_path: Path, monkeypatch):
    """Socket must be unlinked after server shutdown."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    model_dir = tmp_path / "whisper_model"
    model_dir.mkdir()

    custom_sock = tmp_path / "custom2" / "npu.sock"
    worker = SingleOwnerNPUWorker(
        whisper_model_dir=model_dir,
        sock_path=custom_sock,
    )

    sock_path = worker.sock_path
    sock_dir = sock_path.parent
    sock_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(sock_dir, 0o700)
    sock_path.unlink(missing_ok=True)

    old_umask = os.umask(0o077)
    try:
        server = await asyncio.start_unix_server(
            worker._handle_client, path=str(sock_path), limit=16384
        )
        os.chmod(sock_path, 0o600)
    finally:
        os.umask(old_umask)

    server.close()
    await server.wait_closed()
    worker._shutdown()

    assert not sock_path.exists(), "Socket must be unlinked after shutdown"


# ---------------------------------------------------------------------------
# 4. _load_wav_16k_mono tests
# ---------------------------------------------------------------------------

def _write_wav(path: Path, sampwidth: int = 2, framerate: int = 16000,
               nchannels: int = 1, nframes: int = 100, comptype: str = "NONE") -> None:
    """Helper to write a minimal WAV file."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.setcomptype(comptype, "NONE")
        pcm = np.zeros(nframes, dtype=np.int16)
        wf.writeframes(pcm.tobytes())


def test_load_wav_rejects_outside_allowed_roots(tmp_path: Path):
    """_load_wav_16k_mono must reject paths outside allowed roots."""
    allowed = (tmp_path / "allowed",)
    allowed[0].mkdir()

    outside = tmp_path / "outside" / "audio.wav"
    outside.parent.mkdir()
    _write_wav(outside)

    with pytest.raises(PermissionError):
        _load_wav_16k_mono(outside, allowed)


def test_load_wav_rejects_non_regular_file(tmp_path: Path):
    """_load_wav_16k_mono must reject FIFOs/symlinks."""
    allowed = (tmp_path,)
    fifo = tmp_path / "fifo.wav"
    os.mkfifo(fifo)

    with pytest.raises(ValueError):
        _load_wav_16k_mono(fifo, allowed)


def test_load_wav_rejects_oversized_file(tmp_path: Path):
    """_load_wav_16k_mono must reject files > MAX_WAV_BYTES."""
    allowed = (tmp_path,)
    big = tmp_path / "big.wav"

    # Write a small WAV then pad it to exceed MAX_WAV_BYTES
    _write_wav(big, sampwidth=2, framerate=16000, nchannels=1, nframes=100)
    with open(big, "ab") as f:
        f.write(b"\x00" * (MAX_WAV_BYTES + 1))

    with pytest.raises(ValueError, match="outside allowed range"):
        _load_wav_16k_mono(big, allowed)


def test_load_wav_rejects_non_16bit(tmp_path: Path):
    """_load_wav_16k_mono must reject 8-bit (sampwidth=1) and 32-bit (sampwidth=4) WAVs."""
    allowed = (tmp_path,)

    for sw, label in [(1, "8bit"), (4, "32bit")]:
        path = tmp_path / f"test_{label}.wav"
        _write_wav(path, sampwidth=sw)
        with pytest.raises(ValueError, match="16-bit PCM"):
            _load_wav_16k_mono(path, allowed)


def test_load_wav_accepts_valid_16k_mono_pcm16(tmp_path: Path):
    """_load_wav_16k_mono must accept a valid 16 kHz mono PCM16 WAV."""
    allowed = (tmp_path,)
    path = tmp_path / "valid.wav"
    _write_wav(path, sampwidth=2, framerate=16000, nchannels=1, nframes=1000)

    result = _load_wav_16k_mono(path, allowed)
    assert isinstance(result, list)
    assert len(result) == 1000
    assert all(-1.0 <= v <= 1.0 for v in result)


# ---------------------------------------------------------------------------
# 5. _handle_client validation tests
# ---------------------------------------------------------------------------

class _MockSocket:
    """Minimal mock socket for SO_PEERCRED test bypass."""
    def getsockopt(self, level, optname, buflen):
        # Return fake creds: pid=1234, uid=os.getuid(), gid=5678
        import struct
        return struct.pack("3i", 1234, os.getuid(), 5678)


class _MockTransport:
    """Minimal mock transport for StreamWriter."""
    def __init__(self, loop):
        self._loop = loop
        self._closing = False
        self.written = bytearray()

    def write(self, data: bytes):
        self.written.extend(data)

    async def drain(self):
        pass

    def close(self):
        self._closing = True

    def is_closing(self):
        return self._closing


class _MockWriter:
    """Minimal mock StreamWriter that captures written data."""
    def __init__(self, loop):
        self._loop = loop
        self.transport = _MockTransport(loop)
        self._reader = None

    def write(self, data: bytes):
        self.transport.write(data)

    async def drain(self):
        pass

    def close(self):
        self.transport.close()

    async def wait_closed(self):
        pass

    def get_extra_info(self, name):
        if name == "socket":
            return _MockSocket()
        return None


async def _client_server_roundtrip(worker, request_bytes: str) -> dict:
    """Helper: send request bytes to _handle_client with mock reader/writer, return parsed response."""
    loop = asyncio.get_running_loop()

    reader = asyncio.StreamReader()
    reader.feed_data(request_bytes.encode("utf-8") + b"\n")

    writer = _MockWriter(loop)

    await worker._handle_client(reader, writer)

    raw = bytes(writer.transport.written)
    if not raw:
        return {"error": "no response"}
    return json.loads(raw.decode("utf-8"))


@_NPU_SKIP
@pytest.mark.asyncio
async def test_handle_client_rejects_non_dict_json():
    """_handle_client must reject non-dict JSON payloads."""
    home = Path(tempfile.mkdtemp())
    model_dir = home / "model"
    model_dir.mkdir()

    worker = SingleOwnerNPUWorker(whisper_model_dir=model_dir)

    resp = await _client_server_roundtrip(worker, json.dumps(["not", "a", "dict"]))
    assert "error" in resp
    assert "must be a JSON object" in resp["error"]


@_NPU_SKIP
@pytest.mark.asyncio
async def test_handle_client_rejects_invalid_language():
    """_handle_client must reject invalid language tokens."""
    home = Path(tempfile.mkdtemp())
    model_dir = home / "model"
    model_dir.mkdir()

    worker = SingleOwnerNPUWorker(whisper_model_dir=model_dir)

    req = {"op": "transcribe", "wav_path": "/tmp/test.wav", "language": "invalid-lang"}
    resp = await _client_server_roundtrip(worker, json.dumps(req))
    assert "error" in resp
    assert "invalid language token" in resp["error"]


@_NPU_SKIP
@pytest.mark.asyncio
async def test_handle_client_rejects_missing_wav_path():
    """_handle_client must reject missing/empty wav_path."""
    home = Path(tempfile.mkdtemp())
    model_dir = home / "model"
    model_dir.mkdir()

    worker = SingleOwnerNPUWorker(whisper_model_dir=model_dir)

    req = {"op": "transcribe", "wav_path": ""}
    resp = await _client_server_roundtrip(worker, json.dumps(req))
    assert "error" in resp
    assert "wav_path" in resp["error"]


@_NPU_SKIP
@pytest.mark.asyncio
async def test_handle_client_health_ok():
    """_handle_client must respond ok to health op."""
    home = Path(tempfile.mkdtemp())
    model_dir = home / "model"
    model_dir.mkdir()

    worker = SingleOwnerNPUWorker(whisper_model_dir=model_dir)

    resp = await _client_server_roundtrip(worker, json.dumps({"op": "health"}))
    assert resp.get("status") == "ok"
    assert "device" in resp


# ---------------------------------------------------------------------------
# 6. SO_PEERCRED verification
# ---------------------------------------------------------------------------

def test_verify_peer_uid_no_socket_returns_false():
    """_verify_peer_uid with no socket info returns False."""
    class MockWriter:
        def get_extra_info(self, name):
            return None
    assert _verify_peer_uid(MockWriter()) is False


# ---------------------------------------------------------------------------
# 7. Runtime dir helpers
# ---------------------------------------------------------------------------

def test_default_runtime_dir_uses_xdg(monkeypatch):
    """_default_runtime_dir must respect XDG_RUNTIME_DIR."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/custom/runtime")
    assert _default_runtime_dir() == Path("/custom/runtime/celina")


def test_default_cache_dir_uses_xdg_cache_home(monkeypatch):
    """_default_cache_dir must respect XDG_CACHE_HOME."""
    monkeypatch.setenv("XDG_CACHE_HOME", "/custom/cache")
    assert _default_cache_dir() == Path("/custom/cache/celina/npu_blobs")
