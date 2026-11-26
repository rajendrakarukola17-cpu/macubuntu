"""macubuntu celina_pkg.shortcuts.gear05_router — Sub-LLM Shortcut router.

Routes every prompt through Gear 0 (exact match, <1 ms) then Gear 0.5
(Model2Vec semantic + dateparser/rapidfuzz slot extraction, ~0.2 ms) before
ever touching the generative LLM. Falls back to stdlib difflib/regex when
model2vec/dateparser/rapidfuzz are not installed.

stdlib + optional model2vec/dateparser/rapidfuzz. No PyTorch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from celina_pkg.shortcuts.engine import ShortcutSpec

try:
    from celina_pkg.runtime.tier_policy import CelinaTierPolicy
except Exception:  # pragma: no cover - runtime package import fallback
    CelinaTierPolicy = None  # type: ignore

NUM_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")
URL_RE = re.compile(r"https?://\S+")
PATH_RE = re.compile(r"(?:~|/)[A-Za-z0-9._/\-]+")


@dataclass(frozen=True)
class RouteDecision:
    gear: str
    shortcut: ShortcutSpec | None
    confidence: float
    extracted_inputs: dict[str, Any]
    missing_inputs: list[str]


class _EmbeddingIndex:
    """Static embedding index. Uses model2vec when available, else difflib fallback."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self._model = None
        self._matrix = None
        try:
            from model2vec import StaticModel  # type: ignore
            self._model = StaticModel.from_pretrained("minishlab/potion-base-8M")
            import numpy as np
            vecs = self._model.encode(texts)
            norms = (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
            self._matrix = vecs / norms
        except ImportError:
            self._model = None

    def best_match(self, query: str) -> tuple[int, float]:
        if self._matrix is not None:
            import numpy as np
            q = self._model.encode([query])[0]
            q = q / (np.linalg.norm(q) + 1e-9)
            scores = self._matrix @ q
            bi = int(np.argmax(scores))
            return bi, float(scores[bi])
        best_i, best_s = 0, 0.0
        qlow = query.lower()
        for i, t in enumerate(self.texts):
            s = SequenceMatcher(None, qlow, t.lower()).ratio()
            if s > best_s:
                best_i, best_s = i, s
        return best_i, best_s


class Gear05Router:
    """Deterministic (Gear 0) + semantic (Gear 0.5) router in front of the LLM."""

    def __init__(
        self,
        shortcuts: list[ShortcutSpec],
        *,
        auto_threshold: float = 0.88,
        confirm_threshold: float = 0.68,
        tier_policy: Any = None,
    ) -> None:
        # tier_policy adjusts routing aggressiveness:
        #  - enable_gliner_slots  -> lower confirm threshold (better slot extraction)
        #  - serialize_heavy_models -> raise thresholds (prefer local shortcuts over LLM)
        at = auto_threshold
        ct = confirm_threshold
        if tier_policy is not None:
            if getattr(tier_policy, "enable_gliner_slots", False):
                ct = max(0.55, ct - 0.05)
            if getattr(tier_policy, "serialize_heavy_models", False):
                at = min(0.98, at + 0.04)
                ct = min(0.95, ct + 0.06)
        self.auto_threshold = at
        self.confirm_threshold = ct
        self.tier_policy = tier_policy
        self.shortcuts = shortcuts
        self._exact: dict[str, ShortcutSpec] = {}
        corpus: list[str] = []
        self._owners: list[ShortcutSpec] = []
        for sc in shortcuts:
            keys = [sc.id, sc.name, *sc.phrases]
            for k in keys:
                kn = k.strip().lower()
                self._exact[kn] = sc
                corpus.append(kn)
                self._owners.append(sc)
            desc_key = (sc.name + ". " + sc.description).strip().lower()
            corpus.append(desc_key)
            self._owners.append(sc)
        self._index = _EmbeddingIndex(corpus)

    def extract_slots(
        self, query: str, sc: ShortcutSpec,
    ) -> tuple[dict[str, Any], list[str]]:
        found: dict[str, Any] = {}
        missing: list[str] = []
        for inp in sc.inputs:
            val: Any = None
            if inp.kind == "number":
                m = NUM_RE.search(query)
                if m:
                    val = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
            elif inp.kind == "url":
                m = URL_RE.search(query)
                if m:
                    val = m.group(0)
            elif inp.kind == "file":
                m = PATH_RE.search(query)
                if m:
                    val = m.group(0)
            elif inp.kind == "datetime":
                val = self._extract_datetime(query)
            if val is not None:
                found[inp.name] = val
            elif inp.required and inp.default is None:
                missing.append(inp.name)
        return found, missing

    @staticmethod
    def _extract_datetime(query: str) -> str | None:
        try:
            from dateparser.search import search_dates  # type: ignore
            found = search_dates(query, settings={"PREFER_DATES_FROM": "future"})
            if found:
                return found[0][1].isoformat()
        except ImportError:
            pass
        qlow = query.lower()
        m = re.search(r"in\s+(\d+)\s+min", qlow)
        if m:
            from datetime import datetime, timedelta
            return (datetime.now() + timedelta(minutes=int(m.group(1)))).isoformat()
        if "tomorrow" in qlow:
            from datetime import datetime, timedelta
            return (datetime.now() + timedelta(days=1)).replace(
                hour=9, minute=0, second=0, microsecond=0,
            ).isoformat()
        return None

    def route(self, query: str) -> RouteDecision:
        norm = query.strip().lower()
        # 1. Gear 0: exact match (<1 ms)
        if norm in self._exact:
            sc = self._exact[norm]
            inputs, missing = self.extract_slots(query, sc)
            gear = "gear0" if not missing else "gear0.5_confirm"
            return RouteDecision(gear, sc, 1.0, inputs, missing)
        # 1b. Gear 0: prefix match — query starts with a known phrase
        for phrase, sc in self._exact.items():
            if norm.startswith(phrase) and len(phrase) >= 3:
                inputs, missing = self.extract_slots(query, sc)
                gear = "gear0" if not missing else "gear0.5_confirm"
                return RouteDecision(gear, sc, 1.0, inputs, missing)
        best_i, best_score = self._index.best_match(query)
        best_sc = self._owners[best_i]
        if best_score >= self.auto_threshold:
            inputs, missing = self.extract_slots(query, best_sc)
            gear = "gear0.5" if not missing else "gear0.5_confirm"
            return RouteDecision(gear, best_sc, best_score, inputs, missing)
        if best_score >= self.confirm_threshold:
            inputs, missing = self.extract_slots(query, best_sc)
            return RouteDecision("gear0.5_confirm", best_sc, best_score, inputs, missing)
        return RouteDecision("llm", None, best_score, {}, [])
