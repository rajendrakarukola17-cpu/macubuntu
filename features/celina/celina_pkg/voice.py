"""
Celina AI (Celly) - Zero-Idle-RAM Multilingual Neural Voice Engine (EN / TE / HI)
- Script-aware Piper neural TTS via short-lived subprocess (0 MB idle RAM)
- Automatic Unicode detection for Telugu (తెలుగు), Hindi (हिन्दी), and English
- Mixed-language sentence segmentation so each language speaks with its native voice
- Auto-stop silence-detecting microphone capture (RMS VAD)
- Ephemeral faster-whisper transcription subprocess (100% RAM reclaimed on exit)
- Optional openWakeWord listener when CELINA_WAKE_WORD=1 (supports ~/.local/share/celina/wakeword/*.onnx)
"""

import argparse
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Optional

from . import config

_TTS_LOCK = threading.Lock()
_ACTIVE_TTS_PROCS: list[subprocess.Popen] = []
_TTS_GEN = 0

_SELF_AUDIO_APPS = frozenset({
    "aplay", "paplay", "pw-play", "pw-cat", "piper", "espeak-ng", "espeak",
    "speech-dispatcher", "celina", "celina-island", "python3",
})


class PipeWireAudioDucker:
    """
    deskvoice-inspired PipeWire audio ducker:
    Smoothly lowers active background music/video sink-inputs (e.g. Spotify, Firefox, VLC)
    to `duck_pct`% while Celina listens or speaks, then ALWAYS restores exact original volumes.
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        duck_pct: Optional[int] = None,
        runner: Optional[Callable[[list[str]], tuple[int, str]]] = None,
        has_pactl: Optional[bool] = None,
    ):
        self.enabled = config.AUDIO_DUCKING_ENABLED if enabled is None else bool(enabled)
        self.duck_pct = max(5, min(90, int(config.AUDIO_DUCK_PCT if duck_pct is None else duck_pct)))
        self._runner = runner or self._default_runner
        self._has_pactl = (shutil.which("pactl") is not None) if has_pactl is None else bool(has_pactl)
        self._saved_volumes: list[tuple[str, int]] = []

    @staticmethod
    def _default_runner(cmd: list[str]) -> tuple[int, str]:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
            return int(res.returncode), res.stdout or ""
        except Exception:
            return 1, ""

    @staticmethod
    def parse_duckable_sink_inputs(pactl_output: str, target_pct: int) -> list[tuple[str, int]]:
        """Parses `pactl list sink-inputs` -> [(sink_input_id, original_pct)] for non-Celina streams."""
        results: list[tuple[str, int]] = []
        cur_id: Optional[str] = None
        cur_vol: Optional[int] = None
        cur_apps: list[str] = []
        cur_corked = False

        def _flush() -> None:
            if cur_id is None or cur_vol is None or cur_corked or cur_vol <= target_pct:
                return
            joined = " ".join(cur_apps).lower()
            if any(app in joined for app in _SELF_AUDIO_APPS):
                return
            results.append((cur_id, cur_vol))

        for raw in (pactl_output or "").splitlines():
            s = raw.strip()
            if s.startswith("Sink Input #"):
                _flush()
                cur_id = s.split("#", 1)[1].strip()
                cur_vol = None
                cur_apps = []
                cur_corked = False
            elif cur_id is not None:
                low = s.lower()
                if low.startswith("corked:"):
                    cur_corked = "yes" in low
                elif low.startswith("volume:") and cur_vol is None:
                    m = re.search(r"(\d{1,3})%", s)
                    if m:
                        cur_vol = min(150, int(m.group(1)))
                elif any(k in low for k in ("application.name", "application.process.binary", "media.name")):
                    cur_apps.append(s)
        _flush()
        return results

    def duck(self) -> list[tuple[str, int]]:
        if not self.enabled or not self._has_pactl:
            return []
        rc, out = self._runner(["pactl", "list", "sink-inputs"])
        if rc != 0 or not out:
            return []
        targets = self.parse_duckable_sink_inputs(out, self.duck_pct)
        ducked: list[tuple[str, int]] = []
        for sid, orig_pct in targets:
            d_rc, _ = self._runner(["pactl", "set-sink-input-volume", sid, f"{self.duck_pct}%"])
            if d_rc == 0:
                ducked.append((sid, orig_pct))
        self._saved_volumes = ducked
        return ducked

    def restore(self) -> None:
        if not self._saved_volumes or not self._has_pactl:
            return
        saved = list(self._saved_volumes)
        self._saved_volumes.clear()
        for sid, orig_pct in saved:
            try:
                self._runner(["pactl", "set-sink-input-volume", sid, f"{orig_pct}%"])
            except Exception:
                pass

    def __enter__(self) -> "PipeWireAudioDucker":
        try:
            self.duck()
        except Exception:
            pass
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.restore()


class SileroOnnxVAD:
    """
    Zero-PyTorch Silero VAD v5 (and v4 fallback) via ONNX Runtime (<2 MB RAM).
    Rejects fan noise, keyboard typing & background TV far better than raw RMS energy thresholds.
    """

    FRAME_SAMPLES = 512  # 32ms @ 16kHz (Silero v5 standard)

    def __init__(self, session: Any, threshold: float = 0.50, sample_rate: int = 16000):
        import numpy as np

        self._np = np
        self._sess = session
        self.threshold = float(threshold)
        self.sample_rate = int(sample_rate)
        input_names = [inp.name for inp in session.get_inputs()]
        self._is_v5 = "state" in input_names
        if self._is_v5:
            self._state = np.zeros((2, 1, 128), dtype=np.float32)
        else:
            self._h = np.zeros((2, 1, 64), dtype=np.float32)
            self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)

    @classmethod
    def try_load(
        cls,
        model_path: Optional[str] = None,
        threshold: Optional[float] = None,
        sample_rate: int = 16000,
    ) -> Optional["SileroOnnxVAD"]:
        engine = config.VAD_ENGINE
        if engine == "rms":
            return None
        mpath = Path(model_path or config.SILERO_VAD_MODEL)
        if not mpath.exists():
            return None
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            sess = ort.InferenceSession(str(mpath), sess_options=opts, providers=["CPUExecutionProvider"])
            thr = config.SILERO_VAD_THRESHOLD if threshold is None else threshold
            return cls(sess, threshold=thr, sample_rate=sample_rate)
        except Exception:
            return None

    def score_frame(self, frame_512: Any) -> float:
        np = self._np
        arr = np.asarray(frame_512, dtype=np.float32).reshape(1, self.FRAME_SAMPLES)
        sr = np.array(self.sample_rate, dtype=np.int64)
        if self._is_v5:
            out, self._state = self._sess.run(None, {"input": arr, "state": self._state, "sr": sr})
        else:
            out, self._h, self._c = self._sess.run(None, {"input": arr, "sr": sr, "h": self._h, "c": self._c})
        return float(np.asarray(out).reshape(-1)[0])

    def speech_probability(self, mono_chunk: Any) -> float:
        """Scores an arbitrary mono float32 chunk by slicing into 512-sample Silero frames."""
        np = self._np
        buf = np.concatenate([self._tail, np.asarray(mono_chunk, dtype=np.float32).reshape(-1)])
        n_frames = len(buf) // self.FRAME_SAMPLES
        if n_frames == 0:
            self._tail = buf
            return 0.0
        max_prob = 0.0
        for i in range(n_frames):
            frame = buf[i * self.FRAME_SAMPLES : (i + 1) * self.FRAME_SAMPLES]
            p = self.score_frame(frame)
            if p > max_prob:
                max_prob = p
        self._tail = buf[n_frames * self.FRAME_SAMPLES :]
        return max_prob

    def is_speech(self, mono_chunk: Any) -> bool:
        return self.speech_probability(mono_chunk) >= self.threshold


def _clean_for_speech(text: str) -> str:
    """Strips markdown noise and URLs while preserving Unicode scripts (Telugu/Hindi/English)."""
    cleaned = re.sub(r"```[\s\S]*?```", " code snippet omitted. ", text)
    cleaned = re.sub(r"[`*_#>\[\]]", " ", cleaned)
    cleaned = re.sub(r"https?://\S+", " link ", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned[:550]


# Curated Romanized Telugu (Tenglish) & Hindi (Hinglish) particle lexicons
_TENGLISH_STRONG = {
    "cheppu", "cheppandi", "cheyi", "cheyyi", "chey", "cheyandi", "cheyyandi",
    "ippudu", "entha", "ekkada", "chupinchu", "chupinchandi", "chudandi",
    "kavali", "kavalli", "kosam", "naku", "manchi", "chudu", "teeyi", "teeyandi",
    "pettu", "pettandi", "ivvu", "ivvandi", "unnaru", "emiti", "enduku",
}
_TENGLISH_PARTICLES = _TENGLISH_STRONG | {
    "undi", "undhi", "naa", "nee", "ela", "emi", "avunu", "kadhu", "kadu",
    "ledu", "roju", "repu", "ninna", "ra", "andi", "gaaru", "garu", "lo", "ee", "ni",
}
_HINGLISH_STRONG = {
    "batao", "bata", "chahiye", "samjhao", "kholo", "khol", "dekho", "mujhe",
    "kaise", "kahan", "karo", "karna", "nahin", "kya", "dikhao", "likho",
}
_HINGLISH_PARTICLES = _HINGLISH_STRONG | {
    "hai", "hain", "mera", "meri", "mere", "abhi", "kab", "tum", "aaj", "kal",
    "nahi", "band", "mein", "pe", "se", "ka", "ki", "ke", "bhai", "yaar",
}


def detect_language_detail(text: str, default: str = "en") -> tuple[str, str]:
    """
    Detects Telugu ('te'), Hindi ('hi'), or English ('en') AND script style ('native' | 'romanized').
    Checks Unicode ranges first, then lightweight Tenglish / Hinglish particle lexicons.
    """
    if not text:
        return default, "native"

    counts: dict[str, int] = {}
    ranges: list[tuple[str, int, int]] = []
    for code, meta in config.LANGUAGES.items():
        urange = meta.get("unicode_range")
        if isinstance(urange, list) and len(urange) == 2:
            try:
                ranges.append((code, int(str(urange[0]), 16), int(str(urange[1]), 16)))
            except ValueError:
                continue

    for ch in text:
        cp = ord(ch)
        for code, lo, hi in ranges:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
                break

    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0], "native"

    # Romanized Tenglish / Hinglish particle detection for Latin-script prompts
    tokens = re.findall(r"[a-z]+", text.lower())
    if tokens:
        te_hits = sum(1 for tok in tokens if tok in _TENGLISH_PARTICLES)
        te_strong = sum(1 for tok in tokens if tok in _TENGLISH_STRONG)
        hi_hits = sum(1 for tok in tokens if tok in _HINGLISH_PARTICLES)
        hi_strong = sum(1 for tok in tokens if tok in _HINGLISH_STRONG)

        te_score = te_hits + te_strong
        hi_score = hi_hits + hi_strong
        if te_score >= 2 and te_score > hi_score:
            return "te", "romanized"
        if hi_score >= 2 and hi_score >= te_score:
            return "hi", "romanized"

    return default, "native"


def detect_language(text: str, default: str = "en") -> str:
    """
    Detects Telugu ('te'), Hindi ('hi'), or English ('en') via Unicode script ranges
    plus Romanized Tenglish / Hinglish particle lexicons.
    """
    code, _style = detect_language_detail(text, default=default)
    return code


def split_by_language_segments(text: str, force_lang: Optional[str] = None) -> list[tuple[str, str]]:
    """
    Splits text on sentence boundaries (including Devanagari danda '।') and groups
    consecutive sentences sharing the same detected language for seamless multilingual TTS.
    """
    cleaned = _clean_for_speech(text)
    if not cleaned:
        return []
    if force_lang and force_lang != "auto":
        return [(force_lang, cleaned)]

    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", cleaned) if s.strip()]
    if not raw_sentences:
        return [(detect_language(cleaned), cleaned)]

    grouped: list[tuple[str, str]] = []
    for sent in raw_sentences:
        lang = detect_language(sent)
        if grouped and grouped[-1][0] == lang:
            grouped[-1] = (lang, f"{grouped[-1][1]} {sent}")
        else:
            grouped.append((lang, sent))
    return grouped


def stop_speaking() -> None:
    """Stops any currently playing TTS audio immediately."""
    global _TTS_GEN
    with _TTS_LOCK:
        _TTS_GEN += 1
        for proc in _ACTIVE_TTS_PROCS:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
        _ACTIVE_TTS_PROCS.clear()


def _find_piper_cmd() -> Optional[list[str]]:
    venv_piper = Path(sys.executable).parent / "piper"
    if venv_piper.exists() and os.access(venv_piper, os.X_OK):
        return [str(venv_piper)]
    sys_piper = shutil.which("piper")
    if sys_piper:
        return [sys_piper]
    return None


def _find_audio_player(wav_path: str) -> Optional[list[str]]:
    if shutil.which("pw-play"):
        return ["pw-play", wav_path]
    if shutil.which("paplay"):
        return ["paplay", wav_path]
    if shutil.which("aplay"):
        return ["aplay", "-q", wav_path]
    return None


def _piper_model_for_lang(lang_code: str) -> Optional[Path]:
    model_name = config.get_voice_for_lang(lang_code)
    candidate = config.VOICES_DIR / f"{model_name}.onnx"
    if candidate.exists():
        return candidate
    # Fallback to English Piper model only when language is English
    if lang_code == "en":
        fallback = config.VOICES_DIR / f"{config.VOICE_MODEL}.onnx"
        if fallback.exists():
            return fallback
    return None


def _try_speak_kokoro(lang_code: str, text: str, gen: int) -> bool:
    """
    Opt-in Kokoro-82M ONNX TTS for studio-grade English & Hindi voices (Apache-2.0, CPU-friendly).
    Telugu ('te') always bypasses Kokoro and uses native Piper te_IN-maya-medium.
    """
    if config.TTS_ENGINE == "piper" or lang_code not in {"en", "hi"}:
        return False
    kokoro_dir = config.VOICES_DIR / "kokoro"
    model_onnx = kokoro_dir / "kokoro-v1.0.onnx"
    voices_bin = kokoro_dir / "voices-v1.0.bin"
    if not (model_onnx.exists() and voices_bin.exists()):
        return False
    try:
        from kokoro_onnx import Kokoro
        import numpy as np
    except Exception:
        return False

    with _TTS_LOCK:
        if gen != _TTS_GEN:
            return True

    fd, wav_path = tempfile.mkstemp(prefix="celina_kokoro_", suffix=".wav")
    os.close(fd)
    try:
        kokoro = Kokoro(str(model_onnx), str(voices_bin))
        voice_name = config.KOKORO_VOICE_EN if lang_code == "en" else config.KOKORO_VOICE_HI
        kokoro_lang = "en-us" if lang_code == "en" else "hi"
        samples, sr = kokoro.create(text, voice=voice_name, speed=1.0, lang=kokoro_lang)
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sr))
            wf.writeframes(pcm16.tobytes())

        with _TTS_LOCK:
            if gen != _TTS_GEN:
                return True

        player_cmd = _find_audio_player(wav_path)
        if not player_cmd:
            return False
        player = subprocess.Popen(player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with _TTS_LOCK:
            if gen != _TTS_GEN:
                player.terminate()
                return True
            _ACTIVE_TTS_PROCS[:] = [p for p in _ACTIVE_TTS_PROCS if p.poll() is None]
            _ACTIVE_TTS_PROCS.append(player)
        player.wait(timeout=45)
        with _TTS_LOCK:
            _ACTIVE_TTS_PROCS[:] = [p for p in _ACTIVE_TTS_PROCS if p.poll() is None]
        return True
    except Exception:
        return False
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass


def _speak_segment(lang_code: str, text: str, gen: int) -> None:
    """Synthesizes and plays one language segment via Kokoro (opt-in EN/HI), Piper (default EN/TE/HI), or espeak-ng."""
    with _TTS_LOCK:
        if gen != _TTS_GEN:
            return

    if _try_speak_kokoro(lang_code, text, gen):
        return

    piper_cmd = _find_piper_cmd()
    model_path = _piper_model_for_lang(lang_code)

    if piper_cmd and model_path:
        fd, wav_path = tempfile.mkstemp(prefix="celina_tts_", suffix=".wav")
        os.close(fd)
        try:
            synth = subprocess.Popen(
                [*piper_cmd, "--model", str(model_path), "--output_file", wav_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with _TTS_LOCK:
                if gen != _TTS_GEN:
                    synth.terminate()
                    return
                _ACTIVE_TTS_PROCS[:] = [p for p in _ACTIVE_TTS_PROCS if p.poll() is None]
                _ACTIVE_TTS_PROCS.append(synth)
            synth.communicate(input=text.encode("utf-8"), timeout=20)
            with _TTS_LOCK:
                _ACTIVE_TTS_PROCS[:] = [p for p in _ACTIVE_TTS_PROCS if p.poll() is None]

            with _TTS_LOCK:
                if gen != _TTS_GEN:
                    return

            if synth.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 44:
                player_cmd = _find_audio_player(wav_path)
                if player_cmd:
                    player = subprocess.Popen(
                        player_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    with _TTS_LOCK:
                        if gen != _TTS_GEN:
                            player.terminate()
                            return
                        _ACTIVE_TTS_PROCS[:] = [p for p in _ACTIVE_TTS_PROCS if p.poll() is None]
                        _ACTIVE_TTS_PROCS.append(player)
                    player.wait(timeout=45)
                    with _TTS_LOCK:
                        _ACTIVE_TTS_PROCS[:] = [p for p in _ACTIVE_TTS_PROCS if p.poll() is None]
                    return
        except Exception:
            pass
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

    # Fallback: lightweight espeak-ng with matching language voice (-v te / -v hi / -v en-gb)
    if shutil.which("espeak-ng"):
        espeak_voice = config.get_espeak_for_lang(lang_code)
        try:
            proc = subprocess.Popen(
                ["espeak-ng", "-v", espeak_voice, "-s", "160", text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with _TTS_LOCK:
                if gen != _TTS_GEN:
                    proc.terminate()
                    return
                _ACTIVE_TTS_PROCS[:] = [p for p in _ACTIVE_TTS_PROCS if p.poll() is None]
                _ACTIVE_TTS_PROCS.append(proc)
            proc.wait(timeout=30)
            with _TTS_LOCK:
                _ACTIVE_TTS_PROCS[:] = [p for p in _ACTIVE_TTS_PROCS if p.poll() is None]
        except Exception:
            pass


def is_speaking() -> bool:
    """Returns True if any Piper/player/espeak TTS subprocess is currently speaking."""
    with _TTS_LOCK:
        _ACTIVE_TTS_PROCS[:] = [p for p in _ACTIVE_TTS_PROCS if p.poll() is None]
        return bool(_ACTIVE_TTS_PROCS)


def speak_text(
    text: str,
    enabled: Optional[bool] = None,
    lang: Optional[str] = None,
    on_done: Optional[Callable[[], None]] = None,
) -> None:
    """
    Speaks text asynchronously using per-language Piper neural voices (0 MB idle RAM),
    automatically switching between English, Telugu (తెలుగు), and Hindi (हिन्दी).
    Invokes optional `on_done()` once playback completes or is interrupted (for echo-free wake-word resume).
    """
    should_speak = config.SPEAK_ENABLED if enabled is None else enabled
    if not should_speak or not text:
        if on_done is not None:
            on_done()
        return

    segments = split_by_language_segments(text, force_lang=lang)
    if not segments:
        if on_done is not None:
            on_done()
        return

    stop_speaking()
    with _TTS_LOCK:
        my_gen = _TTS_GEN

    def worker() -> None:
        aborted = False
        try:
            with PipeWireAudioDucker():
                for seg_lang, seg_text in segments:
                    with _TTS_LOCK:
                        if my_gen != _TTS_GEN:
                            aborted = True
                            break
                    _speak_segment(seg_lang, seg_text, my_gen)
                if not aborted:
                    time.sleep(0.25)  # Let room speaker reverb decay before resuming wake-word mic
        finally:
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass

    threading.Thread(target=worker, daemon=True).start()


_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?।])\s+|\n+")


class SentenceStreamSpeaker:
    """
    Siri-grade Sentence-Streamed Piper TTS player:
      - Consumes live streamed cumulative text (`feed_partial(partial_text)`)
      - Immediately speaks each completed sentence in order while the LLM is still generating
      - Honors `stop_speaking()` barge-in (`Esc`) and fires `on_done()` once all queued audio finishes
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        lang: Optional[str] = None,
        on_done: Optional[Callable[[], None]] = None,
        speak_fn: Optional[Callable[[str, str, int], None]] = None,
    ):
        import queue as _queue

        self._enabled = config.SPEAK_ENABLED if enabled is None else bool(enabled)
        self._lang = lang
        self._on_done = on_done
        self._speak_fn = speak_fn or _speak_segment
        self._queue: "_queue.Queue[Optional[str]]" = _queue.Queue()
        self._consumed_len = 0
        self._started = False
        self._finished = False
        self._cancelled = False
        self._lock = threading.Lock()
        self._gen = 0

    def _ensure_worker(self) -> None:
        if self._started or not self._enabled:
            return
        self._started = True
        stop_speaking()
        with _TTS_LOCK:
            self._gen = _TTS_GEN
        threading.Thread(target=self._worker, name="celina-stream-tts", daemon=True).start()

    def feed_partial(self, cumulative_text: str) -> list[str]:
        """Extracts newly completed sentences from cumulative streamed text and enqueues them."""
        if not self._enabled or self._cancelled or self._finished:
            return []
        text = cumulative_text or ""
        if len(text) <= self._consumed_len:
            return []
        unseen = text[self._consumed_len :]
        parts = _SENTENCE_BOUNDARY_RE.split(unseen)
        if len(parts) <= 1:
            return []
        # Everything except the last (potentially incomplete) trailing fragment is a finished sentence
        completed: list[str] = []
        advance = 0
        for sent in parts[:-1]:
            idx = unseen.find(sent, advance)
            if idx != -1:
                advance = idx + len(sent)
            clean = sent.strip()
            if len(clean) >= 2:
                completed.append(clean)
        if not completed:
            return []
        # Advance past trailing whitespace after the last completed sentence
        while advance < len(unseen) and unseen[advance].isspace():
            advance += 1
        self._consumed_len += advance
        self._ensure_worker()
        for sent in completed:
            self._queue.put(sent)
        return completed

    def finish(self, final_text: str = "") -> None:
        """Flushes any remaining unsaid tail and signals worker shutdown."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
        if not self._enabled or self._cancelled:
            if self._on_done is not None:
                try:
                    self._on_done()
                except Exception:
                    pass
            return
        tail = (final_text or "")[self._consumed_len :].strip()
        if tail:
            self._ensure_worker()
            self._queue.put(tail)
        if self._started:
            self._queue.put(None)
        elif self._on_done is not None:
            try:
                self._on_done()
            except Exception:
                pass

    def cancel(self) -> None:
        self._cancelled = True
        stop_speaking()
        if self._started:
            self._queue.put(None)

    def _worker(self) -> None:
        aborted = False
        try:
            with PipeWireAudioDucker():
                while True:
                    item = self._queue.get()
                    if item is None or self._cancelled:
                        aborted = self._cancelled
                        break
                    segments = split_by_language_segments(item, force_lang=self._lang)
                    for seg_lang, seg_text in segments:
                        with _TTS_LOCK:
                            if self._gen != _TTS_GEN or self._cancelled:
                                aborted = True
                                break
                        self._speak_fn(seg_lang, seg_text, self._gen)
                    if aborted:
                        break
                if not aborted:
                    time.sleep(0.2)
        finally:
            if self._on_done is not None:
                try:
                    self._on_done()
                except Exception:
                    pass


def listen_followup_window(
    timeout_sec: float = 5.0,
    language: str = "auto",
) -> tuple[Optional[str], Optional[str]]:
    """
    Siri-style hands-free follow-up mic window after Celina finishes speaking:
    listens briefly (`timeout_sec`, default 5s) and returns (transcript, None) if the user
    speaks, or (None, None) silently if no follow-up speech occurs.
    """
    wav_path, rec_err = record_until_silence(
        start_timeout=max(2.0, min(8.0, float(timeout_sec))),
        max_duration=15.0,
    )
    if rec_err or not wav_path:
        return None, None
    try:
        text, _detected_lang, tr_err = transcribe_in_subprocess(wav_path, lang_hint=language)
        if tr_err or not text or not text.strip():
            return None, None
        return text.strip(), None
    finally:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except OSError:
            pass


def record_until_silence(
    sample_rate: int = 16000,
    max_duration: Optional[float] = None,
    start_timeout: Optional[float] = None,
    silence_duration: Optional[float] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Records mono 16kHz audio and automatically stops after silence (Siri-style VAD).
    Returns (wav_file_path, error_message).
    """
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        return None, f"Voice capture packages missing ({exc}). Run Module 06 to install."

    max_dur = config.STT_MAX_DURATION if max_duration is None else max_duration
    start_to = config.STT_START_TIMEOUT if start_timeout is None else start_timeout
    sil_dur = config.STT_SILENCE_SECONDS if silence_duration is None else silence_duration

    chunk_sec = 0.1
    chunk_frames = int(sample_rate * chunk_sec)
    recorded_chunks: list = []
    calibration_rms: list[float] = []

    speech_started = False
    quiet_sec = 0.0
    elapsed_sec = 0.0
    threshold = 0.015
    silero_vad = SileroOnnxVAD.try_load(sample_rate=sample_rate)

    if on_status:
        on_status("Listening (Silero VAD)..." if silero_vad is not None else "Listening...")

    try:
        with PipeWireAudioDucker():
            with sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=chunk_frames,
            ) as stream:
                while elapsed_sec < max_dur:
                    data, _ = stream.read(chunk_frames)
                    mono = data[:, 0].copy()
                    rms = float(np.sqrt(np.mean(mono * mono)))
                    elapsed_sec += chunk_sec

                    if silero_vad is None and elapsed_sec <= 0.35:
                        calibration_rms.append(rms)
                        recorded_chunks.append(mono)
                        if len(calibration_rms) >= 3:
                            ambient = sum(calibration_rms) / len(calibration_rms)
                            threshold = min(max(ambient * 2.5, 0.012), 0.05)
                        continue

                    recorded_chunks.append(mono)
                    is_voice = silero_vad.is_speech(mono) if silero_vad is not None else (rms >= threshold)

                    if is_voice:
                        if not speech_started:
                            speech_started = True
                            if on_status:
                                on_status("Hearing speech...")
                        quiet_sec = 0.0
                    else:
                        if speech_started:
                            quiet_sec += chunk_sec
                            if quiet_sec >= sil_dur:
                                break
                        elif elapsed_sec >= start_to:
                            break
    except Exception as exc:
        return None, f"Microphone error: {exc}"

    if not speech_started or not recorded_chunks:
        return None, "No speech detected."

    audio = np.concatenate(recorded_chunks)
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)

    fd, wav_path = tempfile.mkstemp(prefix="celina_mic_", suffix=".wav")
    os.close(fd)
    try:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16.tobytes())
        return wav_path, None
    except Exception as exc:
        try:
            os.remove(wav_path)
        except OSError:
            pass
        return None, f"Failed to write audio buffer: {exc}"


def _transcribe_wav_inline(
    wav_path: str, lang_hint: Optional[str] = None, model: object = None
) -> tuple[str, str, Optional[str]]:
    """Runs multilingual faster-whisper inside the worker subprocess."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        return "", "en", f"faster-whisper not installed ({exc})."

    try:
        wm = model if model is not None else WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        kwargs: dict = {"vad_filter": True}
        if lang_hint and lang_hint != "auto":
            kwargs["language"] = lang_hint
        segments, info = wm.transcribe(wav_path, **kwargs)
        transcript = " ".join(seg.text.strip() for seg in segments).strip()
        detected = getattr(info, "language", None) or detect_language(transcript)
        return transcript, str(detected), None
    except Exception as exc:
        return "", "en", f"Transcription failed: {exc}"


class _WarmWhisperWorker:
    """
    Warm-standby faster-whisper subprocess with 180-second idle auto-offload (0 MB idle RAM):
    - Never launches at boot; spins up on first voice command.
    - Stays warm during back-to-back voice conversations (0s cold-start delay).
    - Auto-terminates & closes pipes after WHISPER_IDLE_OFFLOAD_SEC of silence so Linux
      reclaims 100% of Whisper RAM.
    """

    def __init__(self, idle_sec: Optional[float] = None):
        self._idle_sec = float(idle_sec if idle_sec is not None else config.WHISPER_IDLE_OFFLOAD_SEC)
        self._proc: Optional[subprocess.Popen] = None
        self._read_buf = bytearray()
        self._lock = threading.Lock()
        self._last_used = 0.0
        self._reaper: Optional[threading.Timer] = None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _schedule_reaper_locked(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        if self._idle_sec > 0:
            self._reaper = threading.Timer(self._idle_sec + 1.0, self.offload_if_idle, kwargs={"force": False})
            self._reaper.daemon = True
            self._reaper.start()

    def _stop_locked(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        if self._proc is not None:
            proc = self._proc
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
            finally:
                for stream in (proc.stdin, proc.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
                self._proc = None
                self._read_buf.clear()

    def offload_if_idle(self, force: bool = False) -> bool:
        with self._lock:
            if not self.is_alive():
                self._stop_locked()
                return False
            if force or (time.time() - self._last_used) >= self._idle_sec:
                self._stop_locked()
                return True
            self._schedule_reaper_locked()
            return False

    def transcribe(
        self, wav_path: str, lang_hint: Optional[str] = None, timeout: float = 60.0
    ) -> tuple[str, str, Optional[str]]:
        with self._lock:
            self._last_used = time.time()
            if not self.is_alive():
                self._stop_locked()
                cmd = [sys.executable, "-m", "celina_pkg.voice", "--worker"]
                try:
                    self._proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        bufsize=0,
                    )
                except Exception as exc:
                    return "", "en", f"Failed to start Whisper worker: {exc}"

            assert self._proc is not None and self._proc.stdin and self._proc.stdout
            req = json.dumps({"wav": wav_path, "lang": lang_hint or "auto"}) + "\n"
            try:
                self._proc.stdin.write(req.encode("utf-8"))
                self._proc.stdin.flush()
            except OSError as exc:
                self._stop_locked()
                return "", "en", f"Whisper worker write error: {exc}"

            deadline = time.time() + timeout
            stdout_fd = self._proc.stdout.fileno()
            sel = selectors.DefaultSelector()
            sel.register(stdout_fd, selectors.EVENT_READ)
            try:
                while True:
                    nl_idx = self._read_buf.find(b"\n")
                    if nl_idx != -1:
                        raw_line = bytes(self._read_buf[:nl_idx])
                        del self._read_buf[: nl_idx + 1]
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                            self._last_used = time.time()
                            self._schedule_reaper_locked()
                            return (
                                str(payload.get("text", "")),
                                str(payload.get("language", "en")),
                                payload.get("error"),
                            )
                        except json.JSONDecodeError:
                            continue

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        self._stop_locked()
                        return "", "en", "Transcription timed out."
                    events = sel.select(timeout=remaining)
                    if not events:
                        self._stop_locked()
                        return "", "en", "Transcription timed out."
                    try:
                        chunk = os.read(stdout_fd, 4096)
                    except OSError as exc:
                        self._stop_locked()
                        return "", "en", f"Whisper worker read error: {exc}"
                    if not chunk:
                        self._stop_locked()
                        return "", "en", "Whisper worker exited unexpectedly."
                    self._read_buf.extend(chunk)
            finally:
                sel.close()


_WHISPER_WORKER = _WarmWhisperWorker()


def offload_whisper_if_idle(force: bool = False) -> bool:
    """Terminates the warm-standby Whisper subprocess if idle (or immediately when force=True)."""
    return _WHISPER_WORKER.offload_if_idle(force=force)


def transcribe_in_subprocess(
    wav_path: str, lang_hint: Optional[str] = None, timeout: int = 60
) -> tuple[str, str, Optional[str]]:
    """
    Transcribes via the warm-standby Whisper subprocess (auto-offloads after 180s idle to 0 MB RAM).
    Returns (transcript, detected_language, error).
    """
    return _WHISPER_WORKER.transcribe(wav_path, lang_hint=lang_hint, timeout=float(timeout))


def resolve_default_monitor_source() -> str:
    """
    Resolves PipeWire / PulseAudio default output sink monitor source
    (e.g. `alsa_output.pci-...monitor` or `@DEFAULT_AUDIO_SINK@.monitor`)
    so Celina can capture loopback meeting/video audio 100% offline.
    """
    if shutil.which("pactl"):
        try:
            r = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            sink = (r.stdout or "").strip()
            if r.returncode == 0 and sink:
                return f"{sink}.monitor"
        except Exception:
            pass
    return "@DEFAULT_AUDIO_SINK@.monitor"


def record_system_audio(
    seconds: int = 30,
    sample_rate: int = 16000,
    monitor_source: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Captures live desktop/meeting loopback audio from the PipeWire output sink `.monitor`
    into a private 0600 temporary WAV file (never ducks playback since we are recording it).
    Returns (wav_path, error_message).
    """
    dur = max(3, min(120, int(seconds)))
    monitor = (monitor_source or resolve_default_monitor_source()).strip()
    fd, wav_path = tempfile.mkstemp(prefix="celina_loopback_", suffix=".wav")
    os.close(fd)
    try:
        os.chmod(wav_path, 0o600)
    except OSError:
        pass

    cmd: Optional[list[str]] = None
    if shutil.which("pw-record"):
        cmd = [
            "pw-record",
            "--target", monitor,
            "--rate", str(sample_rate),
            "--channels", "1",
            "--format", "s16",
            wav_path,
        ]
    elif shutil.which("parec"):
        cmd = [
            "parec",
            "-d", monitor,
            f"--rate={sample_rate}",
            "--channels=1",
            "--format=s16le",
            "--file-format=wav",
            wav_path,
        ]
    else:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        return None, "Error: Neither `pw-record` (pipewire-bin) nor `parec` (pulseaudio-utils) is installed."

    proc: Optional[subprocess.Popen] = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            proc.wait(timeout=dur)
        except subprocess.TimeoutExpired:
            # Send SIGINT so pw-record/parec cleanly flushes the WAV header before exiting
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 44:
            stderr_txt = ""
            if proc.stderr is not None:
                try:
                    stderr_txt = proc.stderr.read().decode("utf-8", errors="ignore").strip()[:200]
                except Exception:
                    pass
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            return None, f"Error: No loopback audio captured from '{monitor}' ({stderr_txt or 'empty audio stream'})."
        return wav_path, None
    except Exception as exc:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        return None, f"Error capturing system audio: {exc}"


def transcribe_system_audio(
    seconds: int = 30,
    lang_hint: Optional[str] = None,
    *,
    monitor_source: Optional[str] = None,
) -> str:
    """
    100% offline PipeWire meeting / lecture / video audio transcriber:
    Captures `seconds` (3..120s) from `@DEFAULT_AUDIO_SINK@.monitor` and transcribes via
    Celina's warm-standby `faster-whisper` worker, then immediately deletes the temporary WAV.
    """
    dur = max(3, min(120, int(seconds)))
    wav_path, rec_err = record_system_audio(seconds=dur, monitor_source=monitor_source)
    if rec_err or not wav_path:
        return rec_err or "Error: Could not capture system audio."
    try:
        hint = None if (not lang_hint or lang_hint.strip().lower() == "auto") else lang_hint.strip().lower()
        text, det_lang, stt_err = transcribe_in_subprocess(wav_path, lang_hint=hint, timeout=max(60, dur * 2))
        if stt_err:
            return f"Error transcribing system audio: {stt_err}"
        clean = (text or "").strip()
        if not clean:
            return f"No speech detected in the {dur}s system audio capture."
        return f"🎙️ Offline System Audio Transcript ({dur}s, lang={det_lang}):\n{clean}"
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def listen_and_transcribe(
    lang_hint: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[str, Optional[str]]:
    """Full Siri-like capture: stop TTS -> record until silence -> ephemeral multilingual transcribe."""
    stop_speaking()
    wav_path, err = record_until_silence(on_status=on_status)
    if err or not wav_path:
        return "", err

    if on_status:
        on_status("Transcribing...")

    try:
        text, _detected, t_err = transcribe_in_subprocess(wav_path, lang_hint=lang_hint)
        return text, t_err
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass


class WakeWordListener:
    """
    Optional hands-free wake-word detector using openWakeWord (~45 MB RAM).
    Only active when CELINA_WAKE_WORD=1 in ~/.config/celina/env.
    Uses CELINA_WAKE_MODEL or any custom .onnx in ~/.local/share/celina/wakeword/
    (e.g. hey_celina.onnx / hey_celly.onnx), falling back to openWakeWord's bundled 'alexa' model.
    """

    def __init__(self, on_wake: Callable[[], None], threshold: float = 0.5):
        self.on_wake = on_wake
        self.threshold = threshold
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def pause(self) -> None:
        self._pause_event.set()

    def resume(self) -> None:
        self._pause_event.clear()

    def stop(self) -> None:
        self._stop_event.set()

    def start(self) -> bool:
        if not config.WAKE_WORD_ENABLED:
            return False
        try:
            import numpy as np  # noqa: F401
            import sounddevice as sd  # noqa: F401
            from openwakeword.model import Model as OWWModel  # noqa: F401
        except ImportError:
            return False

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        import numpy as np
        import sounddevice as sd
        from openwakeword.model import Model as OWWModel

        try:
            wake_models: list[str] = []
            if config.WAKE_WORD_MODEL and Path(config.WAKE_WORD_MODEL).expanduser().is_file():
                wake_models = [str(Path(config.WAKE_WORD_MODEL).expanduser())]
            elif config.WAKEWORD_DIR.is_dir():
                custom_onnx = sorted(config.WAKEWORD_DIR.glob("*.onnx"))
                if custom_onnx:
                    wake_models = [str(p) for p in custom_onnx[:2]]
            if not wake_models:
                wake_models = ["alexa"]
            oww = OWWModel(wakeword_models=wake_models, inference_framework="onnx")
        except Exception:
            return

        sample_rate = 16000
        blocksize = 1280  # 80ms chunks required by openWakeWord

        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(0.2)
                continue
            try:
                with sd.InputStream(
                    samplerate=sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=blocksize,
                ) as stream:
                    while not self._stop_event.is_set() and not self._pause_event.is_set():
                        data, _ = stream.read(blocksize)
                        preds = oww.predict(np.squeeze(data))
                        for score in preds.values():
                            if float(score) >= self.threshold:
                                oww.reset()
                                self._pause_event.set()
                                self.on_wake()
                                break
            except Exception:
                time.sleep(2.0)


def _run_persistent_worker() -> None:
    """Persistent stdin/stdout JSON-lines worker: loads WhisperModel ONCE, reused until offloaded."""
    model = None
    load_err: Optional[str] = None
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    except Exception as exc:
        load_err = f"faster-whisper load error: {exc}"

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"text": "", "language": "en", "error": "Invalid JSON request"}), flush=True)
            continue
        if load_err is not None:
            print(json.dumps({"text": "", "language": "en", "error": load_err}), flush=True)
            continue
        text, detected, err = _transcribe_wav_inline(
            str(req.get("wav", "")), lang_hint=req.get("lang"), model=model
        )
        print(json.dumps({"text": text, "language": detected, "error": err}, ensure_ascii=False), flush=True)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Celina Voice Subprocess Worker")
    parser.add_argument("--transcribe", help="WAV file path to transcribe and exit")
    parser.add_argument("--worker", action="store_true", help="Run persistent warm-standby JSON-lines STT worker")
    parser.add_argument("--lang", default="auto", help="Optional Whisper language code (en/te/hi)")
    args = parser.parse_args()
    if args.worker:
        _run_persistent_worker()
    elif args.transcribe:
        text, detected, err = _transcribe_wav_inline(args.transcribe, lang_hint=args.lang)
        print(json.dumps({"text": text, "language": detected, "error": err}, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
