"""Celina AI (Celly) — Lightweight Hybrid Embedding & Semantic Engine (v4.4)
Preserves ~18 MB boot RAM & 0.0% idle CPU invariants on 8 GB RAM laptops:
  - Lazy-loaded on demand (never imported by celina-island at login)
  - Zero PyTorch, zero separate vector database servers (Qdrant/Chroma/Milvus)
  - Pluggable multilingual backends (English + Telugu + Hindi):
      1. Ollama `/api/embed` (default: `embeddinggemma` 308M QAT / `qwen3-embedding:0.6b`)
         with `keep_alive="30s"` so idle RAM returns to 0 MB automatically.
      2. Model2Vec (`minishlab/potion-multilingual-128M`, 101 languages, pure NumPy
         static embeddings, ~6,700 chunks/sec on CPU) when installed.
  - Compact Matryoshka truncation (default 256 dims) + L2-normalized `float16` BLOBs
    (512 bytes/vector — 10,000 vectors fit in ~5 MB RAM and search in <2 ms via NumPy).
  - Pure Reciprocal Rank Fusion (`rrf_fuse`) combining SQLite FTS5 BM25 + Cosine vectors.
  - Optional `sqlite-vec` extension loader with transparent NumPy / pure-Python fallback.
  - 100% graceful offline degradation: if no embedding model is available, every caller
    falls back cleanly to FTS5 BM25 + lexical ranking with zero errors and <0.05 ms overhead.
"""

from __future__ import annotations

import array
import collections
import math
import sqlite3
import struct
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import requests

from . import config

try:
    import numpy as _np  # type: ignore[import-not-found]
except Exception:
    _np = None  # type: ignore[assignment]

EmbedCallback = Callable[[list[str]], list[list[float]] | None]

_BLOB_MAGIC_F16 = b"CF16"
_BLOB_MAGIC_F32 = b"CF32"


def l2_normalize(vec: Sequence[float], target_dim: int | None = None) -> list[float]:
    """Optional Matryoshka prefix truncation followed by exact L2 unit normalization."""
    vals = [float(x) for x in (vec[:target_dim] if (target_dim and target_dim > 0) else vec)]
    norm = math.sqrt(sum(x * x for x in vals))
    if norm <= 1e-12:
        return vals
    inv = 1.0 / norm
    return [x * inv for x in vals]


def pack_vector(vec: Sequence[float], target_dim: int | None = None) -> bytes:
    """Packs an L2-normalized vector into compact float16 BLOB (NumPy) or float32 BLOB (stdlib)."""
    unit = l2_normalize(vec, target_dim=target_dim)
    if _np is not None:
        arr = _np.asarray(unit, dtype=_np.float16)
        return _BLOB_MAGIC_F16 + arr.tobytes()
    arr_f = array.array("f", unit)
    return _BLOB_MAGIC_F32 + arr_f.tobytes()


def unpack_vector(blob: bytes) -> list[float]:
    """Unpacks a float16 or float32 vector BLOB back into a list of floats."""
    if not blob or len(blob) < 8:
        return []
    magic = blob[:4]
    payload = blob[4:]
    if magic == _BLOB_MAGIC_F16:
        if _np is not None:
            return _np.frombuffer(payload, dtype=_np.float16).astype(_np.float32).tolist()
        # Pure-Python IEEE-754 half-precision fallback (`struct` supports 'e' since Python 3.6!)
        count = len(payload) // 2
        return list(struct.unpack(f"<{count}e", payload[: count * 2]))
    if magic == _BLOB_MAGIC_F32:
        arr_f = array.array("f")
        arr_f.frombytes(payload)
        return list(arr_f)
    return []


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Computes cosine similarity between two equal-length vectors (handles unnormalized inputs)."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    if _np is not None:
        va = _np.asarray(a[:n], dtype=_np.float32)
        vb = _np.asarray(b[:n], dtype=_np.float32)
        denom = float(_np.linalg.norm(va) * _np.linalg.norm(vb))
        return float(_np.dot(va, vb) / denom) if denom > 1e-12 else 0.0
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = math.sqrt(sum(float(a[i]) ** 2 for i in range(n)))
    nb = math.sqrt(sum(float(b[i]) ** 2 for i in range(n)))
    return (dot / (na * nb)) if (na > 1e-12 and nb > 1e-12) else 0.0


def rank_vector_blobs(
    query_vec: Sequence[float],
    candidates: Sequence[tuple[str, bytes]],
    *,
    top_k: int = 10,
    min_sim: float = 0.22,
) -> list[tuple[str, float]]:
    """Fast cosine ranking over (item_key, packed_blob) rows.
    Uses vectorized NumPy float32 matrix multiply (`M @ q`) when NumPy is installed,
    or pure-Python fallback otherwise.
    """
    if not query_vec or not candidates:
        return []
    q_unit = l2_normalize(query_vec)
    q_dim = len(q_unit)

    if _np is not None:
        keys: list[str] = []
        rows: list[Any] = []
        for k, blob in candidates:
            if not blob or len(blob) < 8:
                continue
            magic, payload = blob[:4], blob[4:]
            if magic == _BLOB_MAGIC_F16:
                v = _np.frombuffer(payload, dtype=_np.float16).astype(_np.float32)
            elif magic == _BLOB_MAGIC_F32:
                v = _np.frombuffer(payload, dtype=_np.float32)
            else:
                continue
            if v.shape[0] != q_dim:
                continue
            keys.append(k)
            rows.append(v)
        if not rows:
            return []
        mat = _np.vstack(rows)
        qv = _np.asarray(q_unit, dtype=_np.float32)
        sims = mat @ qv
        scored = [
            (keys[i], max(-1.0, min(1.0, float(sims[i]))))
            for i in range(len(keys))
            if float(sims[i]) >= min_sim
        ]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[: max(1, int(top_k))]

    scored_py: list[tuple[str, float]] = []
    for k, blob in candidates:
        vec = unpack_vector(blob)
        if len(vec) != q_dim:
            continue
        sim = max(-1.0, min(1.0, sum(q_unit[i] * vec[i] for i in range(q_dim))))
        if sim >= min_sim:
            scored_py.append((k, float(sim)))
    scored_py.sort(key=lambda item: (-item[1], item[0]))
    return scored_py[: max(1, int(top_k))]


def rrf_fuse(
    *ranked_lists: Sequence[str],
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion (RRF):
      score(doc) = sum_i  weight_i / (k + rank_i(doc))
    Combines unbounded BM25 ranks and cosine similarity ranks without brittle score normalization.
    """
    rrf_k = max(1, int(k))
    scores: dict[str, float] = {}
    for idx, lst in enumerate(ranked_lists):
        w = float(weights[idx]) if (weights is not None and idx < len(weights)) else 1.0
        seen_in_leg: set[str] = set()
        rank = 0
        for item_id in lst:
            if item_id in seen_in_leg:
                continue
            seen_in_leg.add(item_id)
            rank += 1
            scores[item_id] = scores.get(item_id, 0.0) + (w / (rrf_k + rank))
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Optionally loads `sqlite_vec` C extension on a SQLite connection if installed."""
    try:
        import sqlite_vec  # type: ignore[import-not-found]

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        try:
            conn.enable_load_extension(False)
        except Exception:
            pass
        return False


class EmbeddingEngine:
    """Lazy, zero-idle-RAM multilingual embedding provider supporting:
      - Ollama `/api/embed` (default: `embeddinggemma`, keep_alive="30s")
      - Model2Vec static embeddings (`minishlab/potion-multilingual-128M`)
      - Custom injectable `embed_fn` (for deterministic tests / custom providers)
    Caches negative availability probes (`unavailable_cooldown_sec=120s`) so offline systems
    never stall waiting on localhost timeouts.
    """

    def __init__(
        self,
        *,
        engine: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        keep_alive: str | None = None,
        ollama_base: str | None = None,
        model2vec_model: str | None = None,
        embed_fn: EmbedCallback | None = None,
        unavailable_cooldown_sec: float = 120.0,
    ):
        self.engine = (engine if engine is not None else config.EMBED_ENGINE).strip().lower()
        self.model = (model if model is not None else config.EMBED_MODEL).strip()
        self.dim = max(32, int(dim if dim is not None else config.EMBED_DIM))
        self.keep_alive = str(keep_alive if keep_alive is not None else config.EMBED_KEEP_ALIVE)
        self.ollama_base = (ollama_base or config.OLLAMA_API_BASE).rstrip("/")
        self.model2vec_model = (model2vec_model or config.EMBED_MODEL2VEC_MODEL).strip()
        self._custom_embed_fn = embed_fn
        self._unavailable_cooldown = float(unavailable_cooldown_sec)

        self._lock = threading.Lock()
        self._m2v_instance: Any = None
        self._m2v_last_used: float = 0.0
        self._ollama_down_until: float = 0.0
        self._m2v_unavailable: bool = False
        self._active_backend: str = "custom" if embed_fn is not None else "none"
        self._tls = threading.local()
        # Bounded ~16 KB per-turn LRU/TTL micro-cache: eliminates duplicate embeds across
        # cache.get -> memory.recall -> kg.recall -> semantic_tool_router -> cache.set!
        self._one_cache: collections.OrderedDict[str, tuple[float, list[float]]] = collections.OrderedDict()
        self._one_cache_max = 32
        self._one_cache_ttl = 120.0

    @property
    def active_backend(self) -> str:
        return self._active_backend

    @property
    def model_fingerprint(self) -> str:
        if self._custom_embed_fn is not None:
            return f"custom:{self.dim}"
        if self._active_backend not in {"none", "custom"}:
            return f"{self._active_backend}:{self.dim}"
        return f"{self.engine}:{self.model}:{self.model2vec_model}:{self.dim}"

    def _get_session(self) -> requests.Session:
        sess = getattr(self._tls, "session", None)
        if sess is None:
            sess = requests.Session()
            self._tls.session = sess
        return sess

    def _embed_via_model2vec(self, texts: list[str]) -> list[list[float]] | None:
        if self._m2v_unavailable:
            return None
        with self._lock:
            if self._m2v_instance is None:
                try:
                    from model2vec import StaticModel  # type: ignore[import-not-found]

                    self._m2v_instance = StaticModel.from_pretrained(self.model2vec_model)
                except Exception:
                    self._m2v_unavailable = True
                    return None
            try:
                vecs = self._m2v_instance.encode(texts)
                self._m2v_last_used = time.time()
                self._active_backend = f"model2vec:{self.model2vec_model}"
                return [l2_normalize(v.tolist() if hasattr(v, "tolist") else list(v), self.dim) for v in vecs]
            except Exception:
                return None

    def _embed_via_ollama(self, texts: list[str], timeout: float = 8.0) -> list[list[float]] | None:
        now = time.time()
        if now < self._ollama_down_until:
            return None
        try:
            sess = self._get_session()
            r = sess.post(
                f"{self.ollama_base}/api/embed",
                json={
                    "model": self.model,
                    "input": texts,
                    "truncate": True,
                    "keep_alive": self.keep_alive,
                },
                timeout=timeout,
            )
            if r.status_code == 200:
                data = r.json()
                embs = data.get("embeddings")
                if isinstance(embs, list) and len(embs) == len(texts) and embs and isinstance(embs[0], list):
                    self._active_backend = f"ollama:{self.model}"
                    return [l2_normalize(v, self.dim) for v in embs]
            # Model not pulled (404) or API error -> cool down to keep queries instant
            self._ollama_down_until = now + self._unavailable_cooldown
            return None
        except requests.Timeout:
            # Short cooldown on timeout so cold model load on 8 GB RAM can finish cleanly
            self._ollama_down_until = now + min(15.0, self._unavailable_cooldown)
            return None
        except Exception:
            self._ollama_down_until = now + self._unavailable_cooldown
            return None

    def embed_texts(self, texts: Iterable[str], *, timeout: float = 8.0) -> list[list[float]] | None:
        """Embeds a batch of strings into L2-normalized vectors of length `<= self.dim`.
        Returns None cleanly when disabled or no backend is ready.
        """
        clean = [str(t or "").strip()[:2048] for t in texts]
        if not clean:
            return []
        if self._custom_embed_fn is not None:
            raw = self._custom_embed_fn(clean)
            if not raw or len(raw) != len(clean):
                return None
            return [l2_normalize(v, self.dim) for v in raw]

        if self.engine == "off":
            return None

        if self.engine == "model2vec":
            return self._embed_via_model2vec(clean)

        if self.engine == "ollama":
            return self._embed_via_ollama(clean, timeout=timeout)

        # engine == "auto": prefer zero-swap Model2Vec if installed, otherwise Ollama embeddinggemma
        if not self._m2v_unavailable:
            vecs = self._embed_via_model2vec(clean)
            if vecs is not None:
                return vecs
        return self._embed_via_ollama(clean, timeout=timeout)

    def embed_one(self, text: str, *, timeout: float = 6.0) -> list[float] | None:
        clean = (text or "").strip()[:2048]
        if not clean:
            return None
        now = time.time()
        with self._lock:
            cached = self._one_cache.get(clean)
            if cached is not None:
                ts, vec = cached
                if now - ts <= self._one_cache_ttl:
                    self._one_cache.move_to_end(clean)
                    return list(vec)
                self._one_cache.pop(clean, None)

        res = self.embed_texts([clean], timeout=timeout)
        vec_out = res[0] if (res and len(res) == 1) else None
        if vec_out is not None:
            with self._lock:
                self._one_cache[clean] = (time.time(), list(vec_out))
                self._one_cache.move_to_end(clean)
                while len(self._one_cache) > self._one_cache_max:
                    self._one_cache.popitem(last=False)
        return vec_out

    def is_ready(self) -> bool:
        """Fast probe whether an embedding backend is currently usable."""
        return self.embed_one("ping", timeout=2.0) is not None

    def offload(self) -> bool:
        """Releases in-process Model2Vec RAM, clears per-turn vector micro-cache, resets
        cooldowns, and sends keep_alive=0 to Ollama when Ollama was active.
        Called by `/offload` and idle maintenance to keep idle RAM at 0 MB.
        """
        released = False
        was_ollama = False
        with self._lock:
            if self._m2v_instance is not None:
                self._m2v_instance = None
                released = True
            if self._one_cache:
                self._one_cache.clear()
                released = True
            was_ollama = self._active_backend.startswith("ollama:")
            self._ollama_down_until = 0.0
        if self._custom_embed_fn is None and (was_ollama or self.engine == "ollama"):
            try:
                r = requests.post(
                    f"{self.ollama_base}/api/embed",
                    json={"model": self.model, "input": "", "keep_alive": 0},
                    timeout=1.5,
                )
                if r.status_code < 400:
                    released = True
            except Exception:
                pass
        return released


_ENGINE_LOCK = threading.Lock()
_DEFAULT_ENGINE: EmbeddingEngine | None = None


def get_engine() -> EmbeddingEngine:
    global _DEFAULT_ENGINE
    with _ENGINE_LOCK:
        if _DEFAULT_ENGINE is None:
            _DEFAULT_ENGINE = EmbeddingEngine()
        return _DEFAULT_ENGINE


def set_default_engine(engine: EmbeddingEngine | None) -> None:
    """Overrides the process-wide default embedding engine (useful for tests & runtime reconfiguration)."""
    global _DEFAULT_ENGINE
    with _ENGINE_LOCK:
        _DEFAULT_ENGINE = engine
