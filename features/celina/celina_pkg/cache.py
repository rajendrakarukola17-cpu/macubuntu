"""
Celina AI (Celly) - Privacy-Safe 2-Tier Response & Tool Cache with Timely Offload (v4.2)
- Hot Layer: Tiny bounded in-RAM LRU dict (auto-offloaded to 0 MB after idle timeout)
- Warm Layer: Dedicated SQLite WAL cache.db with per-category TTLs + incremental_vacuum
- Privacy Rule: NEVER caches prompts/outputs containing detected secrets or PII
- Auto-Invalidation: File/document cache keys include (mtime_ns, size) so edits invalidate instantly
"""

import contextlib
import hashlib
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from . import config, embeddings, privacy

# Namespaces & keywords strictly forbidden from ever being cached (Phase 2 privacy hardening)
_FORBIDDEN_CACHE_NAMESPACES = frozenset({
    "clipboard",
    "get_clipboard",
    "biometric",
    "biometrics",
    "fingerprint",
    "face_unlock",
    "password",
    "passwords",
    "screenshot",
    "screen_capture",
    "read_screen",
})
_FORBIDDEN_CACHE_VALUE_MARKERS = (
    "data:image/",
    "-----begin ",
    "fingerprint_template",
    "biometric_template",
    "face_encoding",
    "password=",
    "passwd=",
)

# Tools whose outputs change live or have side effects -> NEVER cache
UNCACHEABLE_TOOLS = {
    "get_system_stats",
    "set_volume",
    "set_brightness",
    "control_media",
    "control_connectivity",
    "open_application",
    "open_document",
    "list_running_apps",
    "get_clipboard",
    "set_clipboard",
    "set_timer",
    "remember_fact",
    "read_screen",
    "find_text_on_screen",
    "click_on_text",
    "click_position",
    "type_text",
    "press_hotkey",
    "write_file",
    "edit_file",
    "edit_document",
    "create_document",
    "convert_document",
    "move_file",
    "trash_file",
    "close_application",
    "compose_message",
    "run_shell_command",
    "run_skill",
    "learn_skill",
    "save_last_actions_as_skill",
    "start_recording",
    "stop_recording",
    "update_skill",
    "forget_skill",
    "mcp_call_tool",
}

# Prompt keywords that imply live / stateful / time-dependent queries -> skip LLM response cache
_LIVE_PROMPT_KEYWORDS = (
    "today", "now", "current", "latest", "right now", "weather", "price", "score",
    "news", "time", "battery", "ram", "cpu", "open ", "click ", "edit ", "create ",
    "delete ", "trash ", "move ", "send ", "remind", "timer", "volume", "brightness",
    "screen", "clipboard", "record", "learn", "skill", "run ", "file", "document",
    "libreoffice", "mcp", "search my", "find my",
)


def _hash_key(namespace: str, raw_key: str) -> str:
    norm = " ".join(raw_key.strip().lower().split())
    return hashlib.sha256(f"{namespace}::{norm}".encode("utf-8")).hexdigest()


def is_cacheable_prompt(prompt: str) -> bool:
    low = prompt.strip().lower()
    if len(low) < 4 or len(low) > 600:
        return False
    if any(kw in low for kw in _LIVE_PROMPT_KEYWORDS):
        return False
    # Never cache anything containing secrets or personal PII
    rep = privacy.scan_sensitive(prompt)
    if rep.has_secrets or rep.has_pii:
        return False
    return True


def file_fingerprint(path: str) -> str:
    """Returns an mtime+size key component so cached reads auto-invalidate on any file edit."""
    try:
        st = Path(path).expanduser().resolve().stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "missing"


class SmartCache:
    """2-tier privacy-safe cache with strict TTL expiry and timely RAM/disk offload."""

    def __init__(self, db_path: Path = config.CACHE_DB_PATH):
        self.db_path = Path(db_path)
        config.ensure_private_dir(self.db_path.parent)
        config.ensure_private_file(self.db_path)
        self._lock = threading.Lock()
        self._hot: OrderedDict[str, tuple[str, float, str]] = OrderedDict()
        self._hot_max = int(config.CACHE_CFG.get("ram_hot_max_items", 32))
        self._ram_idle_offload_sec = float(config.CACHE_CFG.get("ram_idle_offload_sec", 300))
        self._max_entries = int(config.CACHE_CFG.get("max_entries", 300))
        self._max_size_bytes = int(float(config.CACHE_CFG.get("max_size_mb", 10)) * 1024 * 1024)
        self._last_access = time.time()
        self._hits = 0
        self._misses = 0
        self._last_embed_pair: Optional[tuple[str, list[float]]] = None
        self._conn = sqlite3.connect(str(self.db_path), timeout=4.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        for suffix in ("", "-wal", "-shm"):
            p = Path(f"{self.db_path}{suffix}")
            if p.exists():
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
        self._init_db()
        self.offload_and_prune(force_vacuum=False)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    value TEXT NOT NULL,
                    meta TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    last_hit_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    prompt_vec BLOB,
                    lang_prefix TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache_entries(expires_at);
                CREATE INDEX IF NOT EXISTS idx_cache_lru ON cache_entries(last_hit_at);
                """
            )
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(cache_entries)").fetchall()}
            if "prompt_vec" not in cols:
                self._conn.execute("ALTER TABLE cache_entries ADD COLUMN prompt_vec BLOB")
            if "lang_prefix" not in cols:
                self._conn.execute("ALTER TABLE cache_entries ADD COLUMN lang_prefix TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _split_lang_prefix(raw_key: str) -> tuple[str, str]:
        """Splits 'gear:lang:prompt' into ('gear:lang', 'prompt') to prevent EN/TE/HI cache collisions."""
        parts = (raw_key or "").split(":", 2)
        if len(parts) == 3 and parts[0] in {"fast", "tactical", "deep"} and 2 <= len(parts[1]) <= 8:
            return f"{parts[0]}:{parts[1]}", parts[2]
        return "", raw_key

    def _embed_prompt_cached(self, clean_text: str) -> Optional[list[float]]:
        """Memoizes the most recent query embedding so a get() miss -> set() embeds only once."""
        norm = " ".join((clean_text or "").strip().split())
        if not norm:
            return None
        with self._lock:
            if self._last_embed_pair and self._last_embed_pair[0] == norm:
                return self._last_embed_pair[1]
        vec = embeddings.get_engine().embed_one(norm, timeout=2.0)
        if vec:
            with self._lock:
                self._last_embed_pair = (norm, vec)
        return vec

    def ttl_for(self, category: str) -> int:
        ttls = config.CACHE_CFG.get("ttl_seconds", {})
        defaults = {
            "llm_fast": 21600,       # 6 hours
            "llm_tactical": 7200,    # 2 hours
            "llm_deep": 3600,        # 1 hour
            "web_search": 900,       # 15 minutes
            "fetch_webpage": 1800,   # 30 minutes
            "document": 43200,       # 12 hours (invalidated instantly via mtime!)
        }
        return int(ttls.get(category, defaults.get(category, 1800)))

    def get(self, namespace: str, raw_key: str) -> Optional[tuple[str, str]]:
        """Returns (cached_value, meta) if valid and unexpired, else None."""
        if not config.CACHE_ENABLED:
            return None
        key = _hash_key(namespace, raw_key)
        now = time.time()

        with self._lock:
            self._last_access = now
            # 1. Check tiny in-RAM hot cache
            hot_item = self._hot.get(key)
            if hot_item is not None:
                val, exp, meta = hot_item
                if exp > now:
                    self._hot.move_to_end(key)
                    self._hits += 1
                    return val, meta
                self._hot.pop(key, None)

            # 2. Check SQLite warm cache (exact hash match)
            with self._conn:
                row = self._conn.execute(
                    "SELECT value, meta, expires_at FROM cache_entries WHERE cache_key = ?",
                    (key,),
                ).fetchone()
                if row is not None:
                    if float(row["expires_at"]) <= now:
                        self._conn.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))
                    else:
                        self._conn.execute(
                            "UPDATE cache_entries SET last_hit_at = ?, hit_count = hit_count + 1 WHERE cache_key = ?",
                            (now, key),
                        )
                        val, meta, exp = str(row["value"]), str(row["meta"]), float(row["expires_at"])
                        self._put_hot_locked(key, val, exp, meta)
                        self._hits += 1
                        return val, meta

        # 3. Privacy-safe Semantic Paraphrase Cache lookup (non-private llm_* prompts only, language-partitioned!)
        lang_prefix, prompt_body = self._split_lang_prefix(raw_key)
        if config.SEMANTIC_CACHE_ENABLED and namespace.startswith("llm_") and is_cacheable_prompt(prompt_body):
            q_vec = self._embed_prompt_cached(prompt_body)
            if q_vec:
                with self._lock:
                    rows = self._conn.execute(
                        """
                        SELECT cache_key, value, meta, expires_at, prompt_vec
                        FROM cache_entries
                        WHERE namespace = ? AND lang_prefix = ? AND expires_at > ? AND prompt_vec IS NOT NULL
                        ORDER BY last_hit_at DESC
                        LIMIT 200
                        """,
                        (namespace, lang_prefix, now),
                    ).fetchall()
                    if rows:
                        pairs = [(str(r["cache_key"]), bytes(r["prompt_vec"])) for r in rows]
                        best = embeddings.rank_vector_blobs(
                            q_vec,
                            pairs,
                            top_k=1,
                            min_sim=config.SEMANTIC_CACHE_THRESHOLD,
                        )
                        if best:
                            best_key, best_sim = best[0]
                            hit_row = next((r for r in rows if str(r["cache_key"]) == best_key), None)
                            if hit_row is not None:
                                with self._conn:
                                    self._conn.execute(
                                        "UPDATE cache_entries SET last_hit_at = ?, hit_count = hit_count + 1 WHERE cache_key = ?",
                                        (now, best_key),
                                    )
                                val = str(hit_row["value"])
                                base_meta = str(hit_row["meta"])
                                sem_meta = f"{base_meta} [semantic sim={best_sim:.2f}]".strip()
                                self._put_hot_locked(key, val, float(hit_row["expires_at"]), sem_meta)
                                self._hits += 1
                                return val, sem_meta

        with self._lock:
            self._misses += 1
        return None

    def set(
        self,
        namespace: str,
        raw_key: str,
        value: str,
        ttl_seconds: Optional[int] = None,
        meta: str = "",
    ) -> bool:
        """Stores non-sensitive value with strict TTL. Returns True if cached."""
        if not config.CACHE_ENABLED or not value or len(value) > 40000:
            return False
        ns_low = (namespace or "").strip().lower()
        if ns_low in _FORBIDDEN_CACHE_NAMESPACES:
            return False
        val_low = value.lower()
        if any(m in val_low for m in _FORBIDDEN_CACHE_VALUE_MARKERS):
            return False
        if value.startswith(("Error:", "Blocked:", "Denied:", "Cancelled")):
            return False
        # Privacy firewall: NEVER cache secrets or personal PII
        rep = privacy.scan_sensitive(value)
        if rep.has_secrets or rep.has_pii:
            return False

        ttl = int(ttl_seconds) if ttl_seconds is not None else self.ttl_for(namespace)
        if ttl <= 0:
            return False

        key = _hash_key(namespace, raw_key)
        now = time.time()
        expires_at = now + ttl
        size_bytes = len(value.encode("utf-8"))
        lang_prefix, prompt_body = self._split_lang_prefix(raw_key)
        prompt_blob: Optional[bytes] = None
        if config.SEMANTIC_CACHE_ENABLED and namespace.startswith("llm_") and is_cacheable_prompt(prompt_body):
            q_vec = self._embed_prompt_cached(prompt_body)
            if q_vec:
                prompt_blob = embeddings.pack_vector(q_vec, target_dim=config.EMBED_DIM)

        with self._lock:
            self._last_access = now
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO cache_entries
                        (cache_key, namespace, value, meta, size_bytes, created_at, last_hit_at, expires_at, hit_count, prompt_vec, lang_prefix)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        value=excluded.value,
                        meta=excluded.meta,
                        size_bytes=excluded.size_bytes,
                        last_hit_at=excluded.last_hit_at,
                        expires_at=excluded.expires_at,
                        prompt_vec=COALESCE(excluded.prompt_vec, cache_entries.prompt_vec),
                        lang_prefix=excluded.lang_prefix
                    """,
                    (key, namespace, value, meta, size_bytes, now, now, expires_at, prompt_blob, lang_prefix),
                )
            self._put_hot_locked(key, value, expires_at, meta)

        self.offload_and_prune(force_vacuum=False)
        return True

    def _put_hot_locked(self, key: str, value: str, expires_at: float, meta: str) -> None:
        self._hot[key] = (value, expires_at, meta)
        self._hot.move_to_end(key)
        while len(self._hot) > self._hot_max:
            self._hot.popitem(last=False)

    def offload_ram_if_idle(self, force: bool = False) -> int:
        """Offloads in-RAM hot cache back to 0 MB after idle period."""
        with self._lock:
            idle_for = time.time() - self._last_access
            if force or idle_for >= self._ram_idle_offload_sec:
                count = len(self._hot)
                self._hot.clear()
                return count
            return 0

    def offload_and_prune(self, force_vacuum: bool = True) -> dict[str, int]:
        """
        Timely maintenance:
          1. Drops in-RAM hot entries if idle
          2. Deletes expired TTL rows from SQLite
          3. LRU-evicts oldest rows exceeding max_entries or max_size_mb
          4. Runs PRAGMA incremental_vacuum to keep cache.db tiny on disk
        """
        now = time.time()
        ram_cleared = self.offload_ram_if_idle(force=False)
        expired_deleted = 0
        lru_evicted = 0

        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM cache_entries WHERE expires_at <= ?", (now,))
            expired_deleted = int(cur.rowcount or 0)

            # Cap total entry count (LRU eviction)
            total_rows = int(self._conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0])
            if total_rows > self._max_entries:
                excess = total_rows - self._max_entries
                cur2 = self._conn.execute(
                    """
                    DELETE FROM cache_entries WHERE cache_key IN (
                        SELECT cache_key FROM cache_entries ORDER BY last_hit_at ASC LIMIT ?
                    )
                    """,
                    (excess,),
                )
                lru_evicted += int(cur2.rowcount or 0)

            # Cap total byte footprint (LRU eviction)
            total_bytes = int(
                self._conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM cache_entries").fetchone()[0]
            )
            if total_bytes > self._max_size_bytes:
                rows = self._conn.execute(
                    "SELECT cache_key, size_bytes FROM cache_entries ORDER BY last_hit_at ASC"
                ).fetchall()
                to_drop: list[tuple[str]] = []
                for r in rows:
                    to_drop.append((str(r["cache_key"]),))
                    total_bytes -= int(r["size_bytes"])
                    if total_bytes <= int(self._max_size_bytes * 0.8):
                        break
                if to_drop:
                    self._conn.executemany("DELETE FROM cache_entries WHERE cache_key = ?", to_drop)
                    lru_evicted += len(to_drop)

        # Run incremental_vacuum outside Python self._lock with guaranteed handle close
        if force_vacuum or expired_deleted > 0 or lru_evicted > 0:
            try:
                with contextlib.closing(sqlite3.connect(str(self.db_path), timeout=4.0)) as vac_conn:
                    vac_conn.execute("PRAGMA incremental_vacuum(64);")
            except sqlite3.Error:
                pass

        return {
            "ram_cleared": ram_cleared,
            "expired_deleted": expired_deleted,
            "lru_evicted": lru_evicted,
        }

    def clear(self) -> None:
        with self._lock:
            self._hot.clear()
            with self._conn:
                self._conn.execute("DELETE FROM cache_entries")
            self._conn.execute("VACUUM")

    def stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt, COALESCE(SUM(size_bytes), 0) AS sz FROM cache_entries"
            ).fetchone()
            disk_entries = int(row["cnt"])
            disk_kb = round(int(row["sz"]) / 1024.0, 1)
            hot_entries = len(self._hot)
            total_req = self._hits + self._misses
            hit_rate = round((self._hits / total_req) * 100.0, 1) if total_req else 0.0
        return {
            "enabled": config.CACHE_ENABLED,
            "hot_ram_entries": hot_entries,
            "disk_entries": disk_entries,
            "disk_kb": disk_kb,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_pct": hit_rate,
        }

    def format_stats(self) -> str:
        s = self.stats()
        return (
            f"Smart Cache ({'ON' if s['enabled'] else 'OFF'}): "
            f"{s['disk_entries']} disk entries ({s['disk_kb']} KB), "
            f"{s['hot_ram_entries']} hot RAM entries, "
            f"hit rate {s['hit_rate_pct']}% ({s['hits']} hits / {s['misses']} misses). "
            f"Auto-offloads idle RAM after {int(self._ram_idle_offload_sec)}s."
        )


_DEFAULT_CACHE: Optional[SmartCache] = None


def get_cache() -> SmartCache:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = SmartCache()
    return _DEFAULT_CACHE
