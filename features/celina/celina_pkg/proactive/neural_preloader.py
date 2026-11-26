"""macubuntu celina_pkg.proactive.neural_preloader — Userspace Predictive Preloader.

Replaces static preload guesswork with a time-and-sequence decayed Markov +
time-of-day habit predictor that warms predicted binaries and Celina indexes
into page cache via vmtouch, bounded by the active RAM tier and PSI memory
pressure.

Integrates with proactive/daemon.py to listen to GNOME app-launch events.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import subprocess
import time
from collections import defaultdict
from pathlib import Path


class PredictivePreloader:
    """Lightweight decayed Markov + time-of-day predictor that pre-warms upcoming
    app binaries and Celina indexes using vmtouch while honoring RAM tier caps.

    Observes app launches (prev_app -> launched_binary) in 2-hour daily
    buckets. Predicts top-K next binaries for the current hour bucket and
    current app. Warms them with vmtouch -t -m ${VMTOUCH_MAX_MB}M.

    Aborts warming immediately when /proc/pressure/memory "some avg10" >= 8.0
    (memory pressure threshold).
    """

    def __init__(
        self,
        max_vmtouch_mb: int = 256,
        half_life_days: float = 14.0,
        psi_threshold: float = 8.0,
    ) -> None:
        self.max_vmtouch_mb = max_vmtouch_mb
        self.half_life_days = half_life_days
        self.psi_threshold = psi_threshold
        self.decay_lambda = math.log(2.0) / (half_life_days * 86400.0)
        # (hour_bucket, prev_app) -> {next_path: (weighted_score, last_ts)}
        self._weights: dict[tuple[int, str], dict[str, tuple[float, float]]] = (
            defaultdict(dict)
        )

    def _decayed(self, score: float, last_ts: float, now: float) -> float:
        return score * math.exp(-self.decay_lambda * max(0.0, now - last_ts))

    def observe_launch(self, prev_app: str, launched_binary: str) -> None:
        """Record an app-launch transition (prev_app -> launched_binary)."""
        now = time.time()
        bucket = dt.datetime.now().hour // 2  # 12 two-hour daily windows
        key = (bucket, prev_app)
        old_score, old_ts = self._weights[key].get(launched_binary, (0.0, now))
        updated = self._decayed(old_score, old_ts, now) + 1.0
        self._weights[key][launched_binary] = (updated, now)

    def predict_top_paths(
        self, current_app: str, top_k: int = 3, min_score: float = 1.5,
    ) -> list[str]:
        """Return top-K predicted binary paths for current_app in this hour bucket."""
        now = time.time()
        bucket = dt.datetime.now().hour // 2
        candidates = self._weights.get((bucket, current_app), {})
        scored = [
            (path, self._decayed(sc, ts, now))
            for path, (sc, ts) in candidates.items()
            if Path(path).exists()
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [p for p, s in scored[:top_k] if s >= min_score]

    @staticmethod
    def _mem_psi_some_avg10() -> float:
        """Read /proc/pressure/memory 'some avg10' value (0.0 if unavailable)."""
        psi = Path("/proc/pressure/memory")
        if not psi.exists():
            return 0.0
        try:
            for line in psi.read_text(encoding="utf-8").splitlines():
                if line.startswith("some "):
                    return float(line.split("avg10=")[1].split()[0])
        except Exception:
            pass
        return 0.0

    def _under_pressure(self) -> bool:
        return self._mem_psi_some_avg10() >= self.psi_threshold

    async def warm_predicted(self, current_app: str) -> list[str]:
        """Warm predicted binaries via vmtouch. Returns list of warmed paths.

        Aborts immediately under memory pressure (avg10 >= psi_threshold).
        Bounded by max_vmtouch_mb.
        """
        if self._under_pressure() or self.max_vmtouch_mb <= 0:
            return []

        paths = self.predict_top_paths(current_app)
        if not paths:
            return []

        cmd = [
            "vmtouch",
            "-t",
            "-m",
            f"{self.max_vmtouch_mb}M",
            *paths,
        ]
        await asyncio.to_thread(
            subprocess.run,
            cmd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return paths


def preloader_config_for_tier(ai_tier: str) -> dict:
    """Return PredictivePreloader config for a given ai_tier."""
    return {
        "8gb": {"max_vmtouch_mb": 128, "half_life_days": 14.0, "psi_threshold": 8.0},
        "16gb": {"max_vmtouch_mb": 256, "half_life_days": 14.0, "psi_threshold": 8.0},
        "32gb": {"max_vmtouch_mb": 512, "half_life_days": 14.0, "psi_threshold": 8.0},
    }.get(
        ai_tier,
        {"max_vmtouch_mb": 128, "half_life_days": 14.0, "psi_threshold": 8.0},
    )
