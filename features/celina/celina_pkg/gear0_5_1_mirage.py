"""Celina Gear 0.5.1 — Mirage (22 MB CPU Vision Perception Adapter)
================================================================
Wraps Cactus Compute's 51.3M-parameter / 22 MB 2-bit Mirage VLM as an optional,
low-RAM visual perception triage layer before heavy Ollama/llama-server VLMs.

Strict Invariants:
  - Telemetry disabled BEFORE import (`CACTUS_TELEMETRY=0`, `DO_NOT_TRACK=1`).
  - Fail-closed offline when weights are not pre-cached locally.
  - Optional SHA-256 verification of `mirage.cact` via `CELINA_MIRAGE_SHA256`.
  - Automatically flags `escalate_to_vlm=True` for Telugu/Hindi (`te`/`hi`) OCR
    (Mirage's built-in reader is Latin-script only) or low-confidence scenes.
  - Tags all output as `source_trust="untrusted_screen_perception"` (T1 defense).
  - Never touches ~/.ssh, ~/.gnupg, or persists screenshots to disk.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any

# Lock down telemetry BEFORE importing cactus-compute
os.environ.setdefault("CACTUS_TELEMETRY", "0")
os.environ.setdefault("DO_NOT_TRACK", "1")

try:
    import cactus as _cactus  # type: ignore

    MIRAGE_AVAILABLE = True
except ImportError:
    _cactus = None
    MIRAGE_AVAILABLE = False

_NON_LATIN_LANGS = frozenset({"te", "hi", "tel", "hin", "eng+tel", "eng+hin"})
_DEFAULT_CACHE_DIR = Path(
    os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"),
) / "celina" / "models" / "mirage"
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MiB hard cap against image bombs


@dataclasses.dataclass(frozen=True)
class MiragePerceptionResult:
    ok: bool
    tier: str = "gear0_5_1_mirage"
    source_trust: str = "untrusted_screen_perception"
    summary: str = ""
    objects: tuple[dict[str, Any], ...] = ()
    text_regions: tuple[dict[str, Any], ...] = ()
    relations: tuple[dict[str, Any], ...] = ()
    confidence: float = 0.0
    latency_ms: int = 0
    escalate_to_vlm: bool = False
    escalation_reason: str = ""
    error: str = ""

    def to_compact_llm_record(self) -> dict[str, Any]:
        """Drops heavy segmentation bitmaps so main LLM / Headroom sees clean scene graph JSON."""
        return {
            "source_trust": self.source_trust,
            "summary": self.summary[:600],
            "objects": [
                {k: v for k, v in obj.items() if k not in {"mask", "bitmap", "polygon"}}
                for obj in self.objects[:25]
            ],
            "text_regions": list(self.text_regions[:30]),
            "relations": list(self.relations[:20]),
            "confidence": round(self.confidence, 3),
            "escalate_to_vlm": self.escalate_to_vlm,
        }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Gear051MirageRouter:
    """Thread-safe lazy singleton wrapper for 22 MB CPU Mirage vision perception."""

    def __init__(
        self,
        *,
        model_dir: Path | None = None,
        min_confidence: float = 0.55,
        allow_download: bool = False,
    ) -> None:
        self.model_dir = Path(model_dir) if model_dir is not None else _DEFAULT_CACHE_DIR
        self.min_confidence = max(0.1, min(0.95, float(min_confidence)))
        self.allow_download = bool(allow_download)
        self._lock = threading.RLock()
        self._model: Any = None
        self._initialized = False

    def _verify_cached_weights(self) -> tuple[bool, str]:
        weights = self.model_dir / "mirage.cact"
        expected_sha = (os.environ.get("CELINA_MIRAGE_SHA256") or "").strip().lower()
        if not weights.is_file():
            if self.allow_download:
                return True, "download_permitted"
            return False, f"Mirage weights not cached at {weights} (offline fail-closed)"
        if expected_sha:
            actual = _sha256_file(weights).lower()
            if actual != expected_sha:
                return False, f"Mirage SHA-256 mismatch (expected={expected_sha[:16]}, actual={actual[:16]})"
        return True, "verified"

    def initialize(self, *, force: bool = False) -> tuple[bool, str]:
        with self._lock:
            if self._initialized and self._model is not None and not force:
                return True, "ready"
            if not MIRAGE_AVAILABLE or _cactus is None:
                return False, "cactus-compute not installed (pip install cactus-compute inside Celina venv)"
            ok, reason = self._verify_cached_weights()
            if not ok:
                return False, reason
            try:
                self._model = _cactus.Mirage()
                self._initialized = True
                return True, "initialized"
            except Exception as exc:
                self._model = None
                self._initialized = False
                return False, f"Mirage init failed: {exc}"

    def unload(self) -> None:
        """Releases CPU model handle immediately (called by Turbo Mode or memory pressure)."""
        with self._lock:
            self._model = None
            self._initialized = False

    def perceive(
        self,
        image_path: Path | str,
        *,
        lang: str = "en",
        ram_tier: str = "16gb",
    ) -> MiragePerceptionResult:
        """Runs 22 MB Mirage CPU perception on a bounded local image file.
        Automatically flags escalation for non-Latin scripts (Telugu/Hindi) or low confidence.
        """
        clean_lang = (lang or "en").strip().lower()
        if clean_lang in _NON_LATIN_LANGS:
            return MiragePerceptionResult(
                ok=False,
                escalate_to_vlm=True,
                escalation_reason=f"Mirage OCR is Latin-script only; routing lang='{clean_lang}' to Tesseract/VLM",
            )

        img = Path(image_path).expanduser().resolve()
        if not img.is_file() or img.is_symlink():
            return MiragePerceptionResult(ok=False, error=f"Invalid image path: {img}")
        size = img.stat().st_size
        if size <= 0 or size > MAX_IMAGE_BYTES:
            return MiragePerceptionResult(ok=False, error=f"Image size {size} exceeds {MAX_IMAGE_BYTES} byte cap")

        init_ok, init_msg = self.initialize()
        if not init_ok:
            return MiragePerceptionResult(
                ok=False,
                escalate_to_vlm=True,
                escalation_reason=init_msg,
                error=init_msg,
            )

        t0 = time.monotonic()
        try:
            with self._lock:
                raw = self._model.describe(str(img))
        except Exception as exc:
            return MiragePerceptionResult(
                ok=False,
                latency_ms=int((time.monotonic() - t0) * 1000),
                escalate_to_vlm=True,
                escalation_reason=f"Mirage inference error: {exc}",
                error=str(exc),
            )

        lat_ms = int((time.monotonic() - t0) * 1000)
        summary, objs, texts, rels, conf = self._normalize_output(raw)

        # Tier-aware confidence threshold: 8 GB tolerates slightly lower confidence before loading a 3.5 GB VLM
        threshold = 0.45 if ram_tier == "8gb" else self.min_confidence
        needs_escalation = (conf > 0.0 and conf < threshold) or (not summary and not objs and not texts)
        esc_reason = (
            f"Low Mirage confidence ({conf:.2f} < {threshold:.2f}) on {ram_tier} tier"
            if needs_escalation
            else ""
        )

        # On 8 GB tier, keep RAM clean after one-shot inference if requested
        if ram_tier == "8gb" and os.environ.get("CELINA_LITE_MODE") == "1":
            self.unload()

        return MiragePerceptionResult(
            ok=True,
            summary=summary,
            objects=tuple(objs),
            text_regions=tuple(texts),
            relations=tuple(rels),
            confidence=conf,
            latency_ms=lat_ms,
            escalate_to_vlm=needs_escalation,
            escalation_reason=esc_reason,
        )

    @staticmethod
    def _normalize_output(raw: Any) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], float]:
        if isinstance(raw, str):
            return raw.strip(), [], [], [], 0.65 if raw.strip() else 0.0
        if not isinstance(raw, dict):
            txt = str(raw or "").strip()
            return txt, [], [], [], 0.60 if txt else 0.0

        summary = str(raw.get("scene") or raw.get("description") or raw.get("summary") or "").strip()
        objects = [o for o in (raw.get("objects") or []) if isinstance(o, dict)]
        texts = [t for t in (raw.get("text") or raw.get("ocr") or []) if isinstance(t, dict)]
        relations = [r for r in (raw.get("relations") or []) if isinstance(r, dict)]

        scores: list[float] = []
        for item in objects + texts:
            s = item.get("score") or item.get("confidence")
            if isinstance(s, (int, float)):
                scores.append(float(s))
        avg_conf = (sum(scores) / len(scores)) if scores else (0.65 if (summary or objects or texts) else 0.0)
        return summary, objects, texts, relations, avg_conf


_MIRAGE_SINGLETON: Gear051MirageRouter | None = None


def get_gear051_mirage(*, force: bool = False) -> Gear051MirageRouter:
    global _MIRAGE_SINGLETON
    if _MIRAGE_SINGLETON is None or force:
        _MIRAGE_SINGLETON = Gear051MirageRouter()
    return _MIRAGE_SINGLETON
