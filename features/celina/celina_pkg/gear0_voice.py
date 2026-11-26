"""
Celina AI (Celly) - Gear 0 Voice Pipeline (Update 19)

Local-only voice I/O — zero cloud audio transmission, sub-100ms latency.
Components:
  1. Wake word: Porcupine (Picovoice) — <10MB, local "Hey Celina" detection
  2. VAD: Silero VAD (ONNX) — ~1MB, end-of-speech detection
  3. STT: Whisper.cpp (tiny/base) — local transcription, <100ms
  4. TTS: Piper or Kokoro — local natural speech, faster than real-time

All components are optional — Gear 0 works without them.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Voice component availability
try:
    import pvporcupine  # type: ignore
    _HAS_PORCUPINE = True
except Exception:
    _HAS_PORCUPINE = False

try:
    import onnxruntime  # type: ignore
    _HAS_ONNX = True
except Exception:
    _HAS_ONNX = False

try:
    import whisper  # type: ignore
    _HAS_WHISPER = True
except Exception:
    _HAS_WHISPER = False

try:
    import piper  # type: ignore
    _HAS_PIPER = True
except Exception:
    _HAS_PIPER = False

try:
    import vosk  # type: ignore
    _HAS_VOSK = True
except Exception:
    _HAS_VOSK = False


@dataclass
class VoiceResult:
    text: str
    intent: Optional[str] = None
    confidence: float = 0.0
    source: str = "voice"
    latency_ms: int = 0


class WakeWordDetector:
    """Porcupine-based local wake word detection."""

    def __init__(self, access_key: Optional[str] = None, keyword_paths: Optional[list[str]] = None):
        self.access_key = access_key
        self.keyword_paths = keyword_paths or []
        self._porcupine = None
        self._initialized = False

    def initialize(self) -> bool:
        if not _HAS_PORCUPINE:
            return False
        try:
            self._porcupine = pvporcupine.create(
                access_key=self.access_key or "",
                keyword_paths=self.keyword_paths,
            )
            self._initialized = True
            return True
        except Exception:
            self._porcupine = None
            return False

    def process(self, pcm_frame: list[int]) -> int:
        """Process a PCM frame. Returns keyword index or -1."""
        if not self._initialized or self._porcupine is None:
            return -1
        try:
            return self._porcupine.process(pcm_frame)
        except Exception:
            return -1

    def is_available(self) -> bool:
        return _HAS_PORCUPINE and self._initialized


class VADDetector:
    """Silero VAD-based voice activity detection."""

    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path or Path.home() / ".celina" / "models" / "silero_vad.onnx"
        self._session = None
        self._initialized = False

    def initialize(self) -> bool:
        if not _HAS_ONNX or not self.model_path.exists():
            return False
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(str(self.model_path))
            self._initialized = True
            return True
        except Exception:
            self._session = None
            return False

    def is_speech(self, audio_samples: list[float], sample_rate: int = 16000) -> bool:
        if not self._initialized or self._session is None:
            return True  # Default: assume speech if VAD unavailable
        try:
            import numpy as np
            input_tensor = np.array(audio_samples, dtype=np.float32).reshape(1, -1)
            # Silero VAD expects specific input shape
            result = self._session.run(None, {"input": input_tensor, "sr": np.array([sample_rate], dtype=np.int64)})
            return float(result[0][0]) > 0.5
        except Exception:
            return True


class STTEngine:
    """Local speech-to-text via Whisper.cpp or Vosk."""

    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path or Path.home() / ".celina" / "models"
        self._whisper = None
        self._vosk = None
        self._initialized = False

    def initialize(self) -> bool:
        self._initialized = True
        if _HAS_WHISPER:
            try:
                model_file = self.model_path / "whisper-tiny" / "ggml-tiny.bin"
                if model_file.exists():
                    self._whisper = whisper.load_model(str(model_file))
                    return True
            except Exception:
                self._whisper = None
        if _HAS_VOSK:
            try:
                model_dir = self.model_path / "vosk-small"
                if model_dir.exists():
                    self._vosk = vosk.Model(str(model_dir))
                    return True
            except Exception:
                self._vosk = None
        return self._whisper is not None or self._vosk is not None

    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """Transcribe audio bytes to text. Returns empty string on failure."""
        if self._whisper is not None:
            try:
                import numpy as np
                audio = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
                result = self._whisper.transcribe(audio, fp16=False)
                return str(result.get("text", "")).strip()
            except Exception:
                pass
        if self._vosk is not None:
            try:
                rec = vosk.KaldiRecognizer(self._vosk, sample_rate)
                rec.AcceptWaveform(audio_data)
                result = rec.FinalResult()
                import json as _json
                parsed = _json.loads(result)
                return str(parsed.get("text", "")).strip()
            except Exception:
                pass
        return ""

    def is_available(self) -> bool:
        return self._whisper is not None or self._vosk is not None


class TTSEngine:
    """Local text-to-speech via Piper or Kokoro."""

    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path or Path.home() / ".celina" / "models"
        self._piper = None
        self._initialized = False

    def initialize(self) -> bool:
        if not _HAS_PIPER:
            return False
        try:
            model_file = self.model_path / "piper" / "en_US-lessac-medium.onnx"
            if model_file.exists():
                self._piper = piper.PiperVoice.load(str(model_file))
                self._initialized = True
                return True
        except Exception:
            self._piper = None
        return False

    def speak(self, text: str, output_device: Optional[str] = None) -> bool:
        """Speak text via local TTS. Returns True if spoken."""
        if not self._initialized or self._piper is None:
            return False
        try:
            import io
            import soundfile as sf  # type: ignore
            import sounddevice as sd  # type: ignore

            audio = self._piper.synthesize(text)
            sd.play(audio.audio_samples, audio.sample_rate)
            sd.wait()
            return True
        except Exception:
            return False

    def is_available(self) -> bool:
        return _HAS_PIPER and self._initialized


# Global voice components (lazy-initialized)
_wake_word: Optional[WakeWordDetector] = None
_vad: Optional[VADDetector] = None
_stt: Optional[STTEngine] = None
_tts: Optional[TTSEngine] = None
_voice_lock = threading.Lock()


def get_wake_word() -> WakeWordDetector:
    global _wake_word
    if _wake_word is None:
        _wake_word = WakeWordDetector()
        _wake_word.initialize()
    return _wake_word


def get_vad() -> VADDetector:
    global _vad
    if _vad is None:
        _vad = VADDetector()
        _vad.initialize()
    return _vad


def get_stt() -> STTEngine:
    global _stt
    if _stt is None:
        _stt = STTEngine()
        _stt.initialize()
    return _stt


def get_tts() -> TTSEngine:
    global _tts
    if _tts is None:
        _tts = TTSEngine()
        _tts.initialize()
    return _tts


def voice_pipeline_available() -> dict[str, bool]:
    """Return availability of each voice component."""
    return {
        "wake_word": _HAS_PORCUPINE,
        "vad": _HAS_ONNX,
        "stt": _HAS_WHISPER or _HAS_VOSK,
        "tts": _HAS_PIPER,
    }


__all__ = [
    "WakeWordDetector",
    "VADDetector",
    "STTEngine",
    "TTSEngine",
    "VoiceResult",
    "get_wake_word",
    "get_vad",
    "get_stt",
    "get_tts",
    "voice_pipeline_available",
]
