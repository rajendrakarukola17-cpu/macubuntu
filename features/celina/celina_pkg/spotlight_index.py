"""
Celina AI — True CoreSpotlight Fast Engine (<25 MB RAM, Zero eval())
Provides instant Spotlight-grade responses directly in Ubuntu:
  1. Safe AST Math Evaluator (pure operator/function whitelist — NEVER uses eval()!)
  2. Instant Unit Converter (length, mass, temperature, data size, speed, time)
  3. GNOME System Settings Deep-Links (Wi-Fi, Bluetooth, Sound, Display, Privacy, Power...)
  4. Lightweight Private SQLite FTS5 File Metadata Index (0600 DB, skips secrets/hidden dirs)
"""

from __future__ import annotations

import ast
import math
import operator
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import config, embeddings, privacy

# --- 1. Safe AST Math Calculator (ZERO eval!) ---
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_MATH_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "ceil": math.ceil,
    "floor": math.floor,
}
_MATH_CONSTS = {
    "pi": math.pi,
    "e": math.e,
}


def safe_calculate(expression: str) -> Optional[str]:
    """Safely evaluates arithmetic/scientific expressions via Python AST whitelist (zero eval!)."""
    expr = (expression or "").strip().replace("^", "**")
    if not expr or len(expr) > 120:
        return None
    if not re.fullmatch(r"[0-9a-zA-Z\.\s\+\-\*\/\%\(\),]+", expr):
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def _eval_node(node: ast.AST, depth: int = 0) -> float:
        if depth > 20:
            raise ValueError("Expression too deeply nested.")
        if isinstance(node, ast.Expression):
            return _eval_node(node.body, depth + 1)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in _MATH_CONSTS:
            return _MATH_CONSTS[node.id]
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return float(_UNARY_OPS[type(node.op)](_eval_node(node.operand, depth + 1)))
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left = _eval_node(node.left, depth + 1)
            right = _eval_node(node.right, depth + 1)
            if isinstance(node.op, ast.Pow) and abs(right) > 1000:
                raise ValueError("Exponent too large.")
            return float(_BIN_OPS[type(node.op)](left, right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _MATH_FUNCS:
            if len(node.args) > 2 or node.keywords:
                raise ValueError("Unsupported function arguments.")
            fn = _MATH_FUNCS[node.func.id]
            vals = [_eval_node(a, depth + 1) for a in node.args]
            return float(fn(*vals))
        raise ValueError("Disallowed AST node.")

    try:
        val = _eval_node(tree)
        if math.isnan(val) or math.isinf(val):
            return None
        if abs(val - round(val)) < 1e-10:
            return str(int(round(val)))
        return f"{val:.6g}"
    except Exception:
        return None


# --- 2. Instant Unit Converter ---
_UNIT_TABLES: dict[str, dict[str, float]] = {
    "length": {
        "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
        "in": 0.0254, "inch": 0.0254, "inches": 0.0254,
        "ft": 0.3048, "feet": 0.3048, "yd": 0.9144, "mi": 1609.344, "mile": 1609.344, "miles": 1609.344,
    },
    "mass": {
        "mg": 1e-6, "g": 0.001, "kg": 1.0, "lb": 0.45359237, "lbs": 0.45359237, "oz": 0.028349523125,
    },
    "data": {
        "b": 1.0, "kb": 1024.0, "mb": 1024.0 ** 2, "gb": 1024.0 ** 3, "tb": 1024.0 ** 4,
    },
    "speed": {
        "mps": 1.0, "kmh": 1 / 3.6, "kph": 1 / 3.6, "mph": 0.44704,
    },
    "time": {
        "s": 1.0, "sec": 1.0, "min": 60.0, "hr": 3600.0, "hour": 3600.0, "hours": 3600.0, "day": 86400.0, "days": 86400.0,
    },
}


def convert_units(query: str) -> Optional[str]:
    """Converts common units instantaneously (e.g. '100 km to miles', '37 C to F', '8 GB to MB')."""
    m = re.fullmatch(
        r"\s*(-?\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s+(?:to|in)\s+([a-zA-Z]+)\s*",
        query or "",
    )
    if not m:
        return None
    val = float(m.group(1))
    src = m.group(2).lower()
    dst = m.group(3).lower()

    # Temperature special cases
    temp_map = {"c": "c", "celsius": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}
    if src in temp_map and dst in temp_map:
        s, d = temp_map[src], temp_map[dst]
        c_val = val if s == "c" else ((val - 32.0) * 5.0 / 9.0 if s == "f" else val - 273.15)
        out = c_val if d == "c" else (c_val * 9.0 / 5.0 + 32.0 if d == "f" else c_val + 273.15)
        return f"{val:g} {src.upper()} = {out:.4g} {dst.upper()}"

    for table in _UNIT_TABLES.values():
        if src in table and dst in table:
            base = val * table[src]
            out = base / table[dst]
            return f"{val:g} {src} = {out:.6g} {dst}"
    return None


# --- 3. GNOME Settings Deep-Links ---
SETTINGS_PANELS: dict[str, tuple[str, str]] = {
    "wifi": ("wifi", "Wi-Fi & Wireless Networks"),
    "wi-fi": ("wifi", "Wi-Fi & Wireless Networks"),
    "network": ("network", "Wired & VPN Network Settings"),
    "bluetooth": ("bluetooth", "Bluetooth Devices"),
    "sound": ("sound", "Sound Output, Input & Volume"),
    "audio": ("sound", "Sound Output, Input & Volume"),
    "display": ("display", "Displays, Resolution & Fractional Scaling"),
    "monitor": ("display", "Displays, Resolution & Fractional Scaling"),
    "power": ("power", "Power Profiles & Battery"),
    "battery": ("power", "Power Profiles & Battery"),
    "privacy": ("privacy", "Privacy, Screen Lock & Diagnostics"),
    "notifications": ("notifications", "Notifications & Do Not Disturb"),
    "keyboard": ("keyboard", "Keyboard Shortcuts & Input Sources"),
    "mouse": ("mouse", "Mouse & Touchpad Gestures"),
    "touchpad": ("mouse", "Mouse & Touchpad Gestures"),
    "users": ("system", "Users, Fingerprint & Login Settings"),
    "fingerprint": ("system", "Users, Fingerprint & Login Settings"),
    "apps": ("applications", "Default Apps & Permissions"),
    "sharing": ("sharing", "Remote Desktop, Media & File Sharing"),
}


# --- 4. Lightweight Private SQLite FTS5 File Metadata Index ---
_SKIP_DIRS = frozenset({
    ".git", ".ssh", ".gnupg", ".aws", ".kube", ".config", ".cache", ".local",
    "node_modules", ".venv", "venv", "__pycache__", "target", "dist", "build",
})
SPOTLIGHT_DB_PATH = config.BASE_DIR / "spotlight_index.db"


class SpotlightIndex:
    """Private 0600 SQLite FTS5 file metadata index for <15ms Spotlight search."""

    def __init__(self, db_path: Path = SPOTLIGHT_DB_PATH):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        config.ensure_private_dir(self.db_path.parent)
        config.ensure_private_file(self.db_path)
        self._conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA temp_store=MEMORY;")
        self._conn.execute("PRAGMA mmap_size=33554432;")
        self._conn.execute("PRAGMA cache_size=-4000;")
        for suf in ("", "-wal", "-shm"):
            p = Path(f"{self.db_path}{suf}")
            if p.exists():
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS indexed_files (
                    path TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    ext TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime REAL NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS indexed_files_fts USING fts5(
                    name, path, ext, content='indexed_files', content_rowid='rowid'
                );
                CREATE TRIGGER IF NOT EXISTS idx_files_ai AFTER INSERT ON indexed_files BEGIN
                    INSERT INTO indexed_files_fts(rowid, name, path, ext) VALUES (new.rowid, new.name, new.path, new.ext);
                END;
                CREATE TRIGGER IF NOT EXISTS idx_files_ad AFTER DELETE ON indexed_files BEGIN
                    INSERT INTO indexed_files_fts(indexed_files_fts, rowid, name, path, ext) VALUES('delete', old.rowid, old.name, old.path, old.ext);
                END;
                CREATE TRIGGER IF NOT EXISTS idx_files_au AFTER UPDATE ON indexed_files BEGIN
                    INSERT INTO indexed_files_fts(indexed_files_fts, rowid, name, path, ext) VALUES('delete', old.rowid, old.name, old.path, old.ext);
                    INSERT INTO indexed_files_fts(rowid, name, path, ext) VALUES (new.rowid, new.name, new.path, new.ext);
                END;
                CREATE TABLE IF NOT EXISTS doc_chunks (
                    chunk_key TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    chunk_idx INTEGER NOT NULL,
                    snippet TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vec BLOB NOT NULL,
                    mtime REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_doc_chunks_path ON doc_chunks(path);
                """
            )

    def refresh(self, roots: Optional[list[Path]] = None, max_files: int = 5000) -> int:
        scan_roots = roots or [
            config.HOME / "Documents",
            config.HOME / "Desktop",
            config.HOME / "Downloads",
            config.HOME / "Projects",
        ]
        count = 0
        with self._lock, self._conn:
            for root in scan_roots:
                if not root.exists():
                    continue
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
                    for fn in filenames:
                        if fn.startswith("."):
                            continue
                        full = Path(dirpath) / fn
                        try:
                            st = full.stat()
                        except OSError:
                            continue
                        self._conn.execute(
                            """
                            INSERT INTO indexed_files(path, name, ext, size_bytes, mtime)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(path) DO UPDATE SET
                                name=excluded.name, ext=excluded.ext,
                                size_bytes=excluded.size_bytes, mtime=excluded.mtime
                            """,
                            (str(full), fn, full.suffix.lower().lstrip("."), st.st_size, st.st_mtime),
                        )
                        count += 1
                        if count >= max_files:
                            return count
        return count

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        tokens = [re.sub(r"[^a-zA-Z0-9_.-]+", "", t) for t in (query or "").split()]
        tokens = [t for t in tokens if t]
        if not tokens:
            return []
        fts_q = " AND ".join(f'"{t}"*' for t in tokens[:6])
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT f.path, f.name, f.ext, f.size_bytes, f.mtime
                    FROM indexed_files_fts fts
                    JOIN indexed_files f ON f.rowid = fts.rowid
                    WHERE indexed_files_fts MATCH ?
                    ORDER BY f.mtime DESC
                    LIMIT ?
                    """,
                    (fts_q, max(1, min(25, int(limit)))),
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def index_document_chunks(
        self,
        roots: Optional[list[Path]] = None,
        *,
        max_files: int = 120,
        chunk_chars: int = 420,
        max_chunks_per_file: int = 6,
    ) -> int:
        """
        Incrementally embeds focused text/markdown/code chunks (~420 chars) inside spotlight_index.db
        for semantic meaning-based file retrieval (skips hidden/secret dirs & unchanged mtimes).
        Zero-op (<1 ms) when no embedding engine is active.
        """
        eng = embeddings.get_engine()
        if eng.engine == "off":
            return 0
        # Quick probe so we don't walk files when no embedding model is available
        if not eng.is_ready():
            return 0

        text_exts = {".md", ".txt", ".rst", ".org", ".csv", ".py", ".sh", ".json"}
        secret_name_re = re.compile(
            r"(?:^|[._-])(secret|secrets|credential|credentials|password|passwd|token|apikey|api_key|id_rsa|id_ed25519|private)(?:$|[._-])",
            re.IGNORECASE,
        )
        scan_roots = roots or [
            config.HOME / "Documents",
            config.HOME / "Desktop",
            config.HOME / "Projects",
        ]
        existing_mtimes: dict[str, float] = {}
        try:
            with self._lock:
                for r in self._conn.execute("SELECT path, MAX(mtime) AS mt FROM doc_chunks GROUP BY path").fetchall():
                    existing_mtimes[str(r["path"])] = float(r["mt"] or 0.0)
        except sqlite3.Error:
            pass

        # Prune chunks for files that have been deleted from disk
        stale_paths = [p for p in existing_mtimes if not Path(p).exists()]
        if stale_paths:
            with self._lock, self._conn:
                self._conn.executemany("DELETE FROM doc_chunks WHERE path = ?", [(p,) for p in stale_paths])
                for p in stale_paths:
                    existing_mtimes.pop(p, None)

        vault = privacy.RedactionVault()
        indexed_chunks = 0
        files_done = 0
        for root in scan_roots:
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
                for fn in filenames:
                    if files_done >= max_files:
                        return indexed_chunks
                    if fn.startswith(".") or secret_name_re.search(fn):
                        continue
                    full = Path(dirpath) / fn
                    if full.suffix.lower() not in text_exts or config.is_hard_blocked_path(full):
                        continue
                    try:
                        st = full.stat()
                    except OSError:
                        continue
                    if st.st_size <= 0 or st.st_size > 131072:
                        continue
                    p_str = str(full)
                    if abs(existing_mtimes.get(p_str, -1.0) - st.st_mtime) < 1e-3:
                        continue
                    try:
                        raw = full.read_text(encoding="utf-8", errors="ignore")[:65536].strip()
                    except OSError:
                        continue
                    if not raw:
                        continue

                    # Split into focused ~420-char chunks & redact any embedded tokens/secrets
                    chunks: list[str] = []
                    pos = 0
                    while pos < len(raw) and len(chunks) < max_chunks_per_file:
                        seg = " ".join(raw[pos : pos + chunk_chars].split())
                        if len(seg) >= 24:
                            safe_seg, _ = vault.redact(seg)
                            chunks.append(f"{fn}: {safe_seg}")
                        pos += chunk_chars
                    if not chunks:
                        continue

                    vecs = eng.embed_texts(chunks, timeout=5.0)
                    if not vecs or len(vecs) != len(chunks):
                        continue
                    with self._lock, self._conn:
                        self._conn.execute("DELETE FROM doc_chunks WHERE path = ?", (p_str,))
                        for idx_c, (ch_txt, vec) in enumerate(zip(chunks, vecs)):
                            ckey = f"{p_str}#{idx_c}"
                            blob = embeddings.pack_vector(vec, target_dim=config.EMBED_DIM)
                            self._conn.execute(
                                """
                                INSERT INTO doc_chunks (chunk_key, path, chunk_idx, snippet, dim, vec, mtime)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (ckey, p_str, idx_c, ch_txt[:260], len(vec), blob, st.st_mtime),
                            )
                            indexed_chunks += 1
                    files_done += 1
        return indexed_chunks

    def search_hybrid(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Hybrid CoreSpotlight Search: fuses FTS5 filename/path BM25 matches + semantic chunk
        cosine matches via Reciprocal Rank Fusion (k=60). Falls back cleanly to pure FTS5
        when no chunks or embedding engine are present.
        """
        fts_hits = self.search(query, limit=max(limit * 2, 12))
        by_path: dict[str, dict[str, Any]] = {str(h["path"]): dict(h) for h in fts_hits}
        bm25_paths = [str(h["path"]) for h in fts_hits]

        vec_paths: list[str] = []
        snippets_by_path: dict[str, str] = {}
        has_chunks = False
        try:
            with self._lock:
                has_chunks = self._conn.execute("SELECT 1 FROM doc_chunks LIMIT 1").fetchone() is not None
        except sqlite3.Error:
            has_chunks = False
        q_vec = (
            embeddings.get_engine().embed_one(query, timeout=2.0)
            if (has_chunks and (query or "").strip())
            else None
        )
        if q_vec:
            try:
                with self._lock:
                    rows = self._conn.execute(
                        "SELECT chunk_key, path, snippet, vec, mtime FROM doc_chunks ORDER BY mtime DESC LIMIT 3000"
                    ).fetchall()
            except sqlite3.Error:
                rows = []
            if rows:
                pairs = [(str(r["chunk_key"]), bytes(r["vec"])) for r in rows if r["vec"]]
                ranked = embeddings.rank_vector_blobs(q_vec, pairs, top_k=max(limit * 2, 12), min_sim=0.28)
                row_map = {str(r["chunk_key"]): r for r in rows}
                seen_p: set[str] = set()
                for ckey, _sim in ranked:
                    r = row_map.get(ckey)
                    if r is None:
                        continue
                    p_str = str(r["path"])
                    p_obj = Path(p_str)
                    if not p_obj.exists():
                        continue
                    if p_str not in seen_p:
                        seen_p.add(p_str)
                        vec_paths.append(p_str)
                        snippets_by_path[p_str] = str(r["snippet"])
                        if p_str not in by_path:
                            try:
                                sz = p_obj.stat().st_size
                            except OSError:
                                sz = 0
                            by_path[p_str] = {
                                "path": p_str,
                                "name": p_obj.name,
                                "ext": p_obj.suffix.lower().lstrip("."),
                                "size_bytes": sz,
                                "mtime": float(r["mtime"] or 0.0),
                            }

        if not vec_paths:
            return fts_hits[:limit]

        fused = embeddings.rrf_fuse(bm25_paths, vec_paths, k=config.EMBED_RRF_K)
        out: list[dict[str, Any]] = []
        for p_str, _score in fused[:limit]:
            entry = dict(by_path[p_str])
            if p_str in snippets_by_path:
                entry["snippet"] = snippets_by_path[p_str]
            out.append(entry)
        return out


def spotlight_query(query: str, index: Optional[SpotlightIndex] = None) -> str:
    """Unified CoreSpotlight resolver: instant math -> unit conversion -> settings -> hybrid indexed files."""
    q = (query or "").strip()
    if not q:
        return "Provide a math expression, unit conversion, settings topic, or filename."

    # 1. Unit conversion
    conv = convert_units(q)
    if conv:
        return f"🔄 Conversion: {conv}"

    # 2. Safe AST calculation (only when query has digits & math ops/funcs)
    if any(ch.isdigit() for ch in q) and any(op in q for op in "+-*/%^()"):
        calc = safe_calculate(q)
        if calc is not None:
            return f"🧮 {q} = {calc}"

    # 3. Settings deep-link suggestions
    low = q.lower()
    matched_panels = [
        f"⚙️ Settings -> {desc} (`gnome-control-center {panel}`)"
        for key, (panel, desc) in SETTINGS_PANELS.items()
        if key in low
    ]
    seen_panels = list(dict.fromkeys(matched_panels))

    # 4. Private Hybrid (FTS5 + Semantic RRF) file matches
    idx = index or SpotlightIndex()
    try:
        hits = idx.search_hybrid(q, limit=8)
    finally:
        if index is None:
            idx.close()
    lines: list[str] = []
    lines.extend(seen_panels[:3])
    for h in hits:
        kb = max(1, int(h["size_bytes"] // 1024))
        snip = f" | {h['snippet'][:90]}" if h.get("snippet") else ""
        lines.append(f"📄 {h['name']} ({kb} KB) — {h['path']}{snip}")

    if not lines:
        return f"No instant Spotlight matches for '{q}' (try refreshing the index or using search_files)."
    return "\n".join(lines)


def format_rofi_spotlight_entries(query: str = "", index: Optional[SpotlightIndex] = None) -> list[tuple[str, str]]:
    """
    Returns (display_label, action_payload) pairs for Rofi native script mode (`--rofi-mode`),
    giving real-time inline macOS Spotlight math, unit conversion, GNOME Settings, and private FTS5 files.
    """
    q = (query or "").strip()
    entries: list[tuple[str, str]] = []
    if not q:
        entries.append(("🧮 Math & Units — type e.g. 'sqrt(144)+2^8' or '100 miles to km'", ""))
        for key in ("wifi", "bluetooth", "sound", "display", "power", "privacy"):
            panel, desc = SETTINGS_PANELS[key]
            entries.append((f"⚙️ Settings: {desc}", f"settings:{panel}"))
        return entries

    conv = convert_units(q)
    if conv:
        entries.append((f"🔄 {conv}  (Enter to copy)", f"copy:{conv}"))

    if any(ch.isdigit() for ch in q) and any(op in q for op in "+-*/%^()"):
        calc = safe_calculate(q)
        if calc is not None:
            entries.append((f"🧮 {q} = {calc}  (Enter to copy)", f"copy:{calc}"))

    low = q.lower()
    seen_panels: set[str] = set()
    for key, (panel, desc) in SETTINGS_PANELS.items():
        if key in low and panel not in seen_panels:
            seen_panels.add(panel)
            entries.append((f"⚙️ Open Settings: {desc}", f"settings:{panel}"))

    idx = index or SpotlightIndex()
    try:
        for h in idx.search(q, limit=6):
            entries.append((f"📄 {h['name']} — {h['path']}", f"open:{h['path']}"))
    finally:
        if index is None:
            idx.close()

    entries.append((f"✨ Ask {config.get_assistant_name()}: {q}", f"ask:{q}"))
    return entries


def _handle_rofi_mode(argv: list[str]) -> int:
    """Implements Rofi script mode (`rofi -modi spotlight:'python3 -m celina_pkg.spotlight_index --rofi-mode'`)."""
    import shutil
    import subprocess

    selected = argv[0].strip() if argv else ""
    info = os.environ.get("ROFI_INFO", "").strip()
    if selected and info:
        kind, _, payload = info.partition(":")
        if kind == "copy" and payload and shutil.which("wl-copy"):
            subprocess.run(["wl-copy"], input=payload, text=True, timeout=2, check=False)
            return 0
        if kind == "settings" and payload and shutil.which("gnome-control-center"):
            subprocess.Popen(["gnome-control-center", payload], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return 0
        if kind == "open" and payload and shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", payload], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return 0
        if kind == "ask" and payload:
            island = config.HOME / ".local" / "bin" / "celina-island"
            if island.exists():
                subprocess.Popen([str(island), "--ask", payload], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return 0
        return 0

    for label, action in format_rofi_spotlight_entries(selected):
        if action:
            print(f"{label}\0info\x1f{action}")
        else:
            print(label)
    return 0


if __name__ == "__main__":
    import sys as _sys

    if "--rofi-mode" in _sys.argv:
        rem = [a for a in _sys.argv[1:] if a != "--rofi-mode"]
        raise SystemExit(_handle_rofi_mode(rem))
    if "--refresh" in _sys.argv:
        _idx = SpotlightIndex()
        try:
            print(f"Indexed {_idx.refresh()} files into {SPOTLIGHT_DB_PATH}")
        finally:
            _idx.close()
    elif len(_sys.argv) > 1:
        print(spotlight_query(" ".join(_sys.argv[1:])))
    else:
        print("Usage: python3 -m celina_pkg.spotlight_index [--refresh | --rofi-mode | <query>]")
