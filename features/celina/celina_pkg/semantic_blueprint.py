"""Celina AI — LUMOS-Style Semantic Blueprint Perception Layer (v4.4)

Replaces raw screenshot-and-OCR scraping with a structured accessibility blueprint:
  - Stable deterministic element IDs (`el_<8hex>`) anchored to (app, role, name, occurrence)
  - Canonical non-localized AT-SPI2 roles + current values + UI states
  - Explicit action affordances (`click`, `activate`, `toggle`, `type`, `focus`)
  - Per-application thread-safe blueprint cache invalidated on window-change events
    or immediately after any GUI actuation
  - Prompt-injection surface reduction: native GTK/Qt/Firefox/Electron apps are
    perceived purely from trusted accessibility metadata; Tesseract OCR is demoted
    to an explicitly tagged `# UNTRUSTED_OCR_FALLBACK` only for opaque/canvas windows.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import threading
import time
from collections.abc import Callable
from typing import Any

_COMPACT_ROLES: dict[str, str] = {
    "push button": "btn",
    "toggle button": "tbtn",
    "menu item": "mi",
    "check menu item": "cmi",
    "radio menu item": "rmi",
    "check box": "chk",
    "radio button": "rad",
    "combo box": "cmb",
    "page tab": "tab",
    "link": "lnk",
    "entry": "entry",
    "text": "txt",
    "password text": "pwd",
    "spin button": "spin",
    "list item": "li",
    "table cell": "cell",
    "tree item": "tree",
    "icon": "ico",
    "slider": "sld",
}

_ROLE_DEFAULT_AFFORDANCES: dict[str, tuple[str, ...]] = {
    "push button": ("click",),
    "toggle button": ("toggle", "click"),
    "menu item": ("activate", "click"),
    "check menu item": ("toggle",),
    "radio menu item": ("select",),
    "check box": ("toggle",),
    "radio button": ("select",),
    "combo box": ("select", "click"),
    "page tab": ("activate", "click"),
    "link": ("activate", "click"),
    "entry": ("type", "focus"),
    "text": ("type", "focus"),
    "password text": ("focus",),
    "spin button": ("type", "click"),
    "list item": ("select", "click"),
    "table cell": ("select", "click"),
    "tree item": ("select", "activate"),
    "icon": ("click",),
    "slider": ("adjust",),
}

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
# BiDi controls + zero-width chars — prompt-injection surface via AT-SPI tree.
# "OK\u202eCANCEL" displays as LLECNAC visually; an LLM sees a truncated label
# and cannot tell which button is which. No screenshot needed to exploit.
_BIDI_RE = re.compile(r"[\u202a-\u202e\u200b-\u200f\u2066-\u2069\ufeff]+")


def _clean_text(raw: str, max_len: int = 80) -> str:
    txt = _CTRL_RE.sub(" ", raw or "")
    txt = _BIDI_RE.sub("", txt).replace("|", "/").strip()
    return " ".join(txt.split())[:max_len]


def make_stable_element_id(
    app: str,
    role: str,
    name: str,
    occurrence: int = 0,
    *,
    bounds: tuple[int, int, int, int] | None = None,
) -> str:
    """Deterministic 8-hex element identifier stable across blueprint refreshes.

    Anchored on (app, role, name, bounds) so that opening a modal elsewhere on
    screen does not shift occurrence indices and retarget a stable ID to a
    different element — a real privilege-escalation path in a security product.
    """
    bx, by, bw, bh = bounds or (0, 0, 0, 0)
    key = (
        f"{(app or '').strip().lower()}|{(role or '').strip().lower()}"
        f"|{(name or '').strip().lower()}|{bx},{by},{bw},{bh}"
    )
    return "el_" + hashlib.sha1(key.encode("utf-8", errors="ignore"), usedforsecurity=False).hexdigest()[:8]


@dataclasses.dataclass(frozen=True)
class SemanticElement:
    id: str
    role: str
    name: str
    value: str
    bounds: tuple[int, int, int, int]  # (x, y, width, height)
    center: tuple[int, int]            # (cx, cy)
    states: tuple[str, ...] = ()
    actions: tuple[str, ...] = ("click",)

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": "a11y_blueprint",
            "role": self.role,
            "label": self.name,
            "value": self.value,
            "states": list(self.states),
            "actions": list(self.actions),
            "x": self.center[0],
            "y": self.center[1],
            "bbox": self.bounds,
        }

    def to_blueprint_row(self) -> str:
        rtag = _COMPACT_ROLES.get(self.role, self.role[:6])
        acts = ",".join(self.actions[:3])
        val_part = f"={self.value[:28]}" if self.value else ""
        st_part = f"[{','.join(self.states[:2])}]" if self.states else ""
        return f"{self.id}|{rtag}|{self.name}{val_part}{st_part}|{acts}|{self.center[0]},{self.center[1]}"


@dataclasses.dataclass
class SemanticBlueprint:
    app: str
    window_title: str
    elements: list[SemanticElement]
    captured_at: float = dataclasses.field(default_factory=time.monotonic)

    @property
    def window_key(self) -> str:
        return f"{self.app.lower().strip()}::{self.window_title.strip()}"

    @property
    def fingerprint(self) -> str:
        sig = self.window_key + "\n" + "\n".join(el.to_blueprint_row() for el in self.elements)
        return hashlib.sha1(sig.encode("utf-8", errors="ignore"), usedforsecurity=False).hexdigest()[:12]

    def find(self, target: str, occurrence: int = 1) -> SemanticElement | None:
        q = (target or "").strip()
        if not q:
            return None
        q_low = q.lower()
        # 1. Exact stable ID lookup (`el_a1b2c3d4`)
        for el in self.elements:
            if el.id.lower() == q_low:
                return el
        # 2. Exact label match, then substring match
        exact = [el for el in self.elements if el.name.lower() == q_low]
        matches = exact or [el for el in self.elements if q_low in el.name.lower()]
        if not matches:
            return None
        idx = max(0, min(len(matches) - 1, int(occurrence) - 1))
        return matches[idx]

    def to_compact_text(self, max_chars: int = 2400) -> str:
        header = (
            f"# SEMANTIC_BLUEPRINT app={_clean_text(self.app, 32)} "
            f"win={_clean_text(self.window_title, 48)} count={len(self.elements)}\n"
            "# COLS: id|role|name[=val][states]|actions|x,y"
        )
        rows = [el.to_blueprint_row() for el in self.elements]
        text = header + ("\n" + "\n".join(rows) if rows else "\n(no accessible controls)")
        return text[:max_chars]


def build_blueprint_from_records(
    app: str,
    window_title: str,
    raw_nodes: list[dict[str, Any]],
    *,
    max_elements: int = 80,
) -> SemanticBlueprint:
    """Converts raw AT-SPI2 node dictionaries into a normalized `SemanticBlueprint`
    with deterministic stable IDs, sanitized labels/values, and action affordances.
    Pure Python — testable without GTK/AT-SPI2 display servers.
    """
    elements: list[SemanticElement] = []
    seen_counts: dict[tuple[str, str], int] = {}

    for node in raw_nodes:
        if len(elements) >= max_elements:
            break
        role = _clean_text(str(node.get("role", "")), 40).lower()
        name = _clean_text(str(node.get("label") or node.get("name") or ""), 80)
        if not role or not name:
            continue

        # Never expose raw password characters in semantic blueprints.
        # Electron/Qt apps report password fields as role="entry" with
        # StateType.PROTECTED set — checking role alone misses them.
        states_set = {str(s).strip().lower() for s in (node.get("states") or ()) if str(s).strip()}
        is_password = role == "password text" or "protected" in states_set
        raw_val = "" if is_password else _clean_text(str(node.get("value", "")), 60)

        bbox = node.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            bx, by, bw, bh = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
            cx, cy = int(bx + bw // 2), int(by + bh // 2)
        else:
            cx, cy = int(node.get("x", 0)), int(node.get("y", 0))
            bx, by, bw, bh = cx, cy, 0, 0

        occ_key = (role, name.lower())
        occ = seen_counts.get(occ_key, 0)
        seen_counts[occ_key] = occ + 1

        states_raw = node.get("states") or ()
        states = tuple(
            _clean_text(str(s), 20).lower()
            for s in states_raw
            if str(s).strip()
        )[:4]

        acts_raw = node.get("actions") or ()
        acts = tuple(
            _clean_text(str(a), 20).lower()
            for a in acts_raw
            if str(a).strip()
        )
        if not acts:
            acts = _ROLE_DEFAULT_AFFORDANCES.get(role, ("click",))

        el_id = make_stable_element_id(app, role, name, occ, bounds=(bx, by, bw, bh))
        elements.append(
            SemanticElement(
                id=el_id,
                role=role,
                name=name,
                value=raw_val,
                bounds=(bx, by, bw, bh),
                center=(cx, cy),
                states=states,
                actions=acts,
            ),
        )

    return SemanticBlueprint(app=app or "unknown", window_title=window_title or "", elements=elements)


class SemanticBlueprintCache:
    """Per-application semantic blueprint cache invalidated automatically on:
    - Active window change (`window_key` mismatch),
    - TTL expiry (`ttl_sec`, default 2.5s),
    - Explicit invalidation (`invalidate()`) after any GUI actuation or window event.
    """

    def __init__(self, ttl_sec: float = 2.5, max_apps: int = 16):
        self.ttl_sec = max(0.1, float(ttl_sec))
        self.max_apps = max(2, int(max_apps))
        self._lock = threading.RLock()
        self._by_window: dict[str, SemanticBlueprint] = {}
        self._active_key: str = ""
        self._hits = 0
        self._misses = 0

    def get_or_build(
        self,
        app: str,
        window_title: str,
        builder: Callable[[], SemanticBlueprint],
        *,
        force_refresh: bool = False,
        now: float | None = None,
    ) -> SemanticBlueprint:
        ts = time.monotonic() if now is None else now
        wkey = f"{(app or 'unknown').lower().strip()}::{(window_title or '').strip()}"

        with self._lock:
            # Window-switch event invalidates stale focus assumptions
            if self._active_key and self._active_key != wkey:
                self._by_window.pop(self._active_key, None)
            self._active_key = wkey

            cached = self._by_window.get(wkey)
            if not force_refresh and cached is not None and (ts - cached.captured_at) <= self.ttl_sec:
                self._hits += 1
                return cached
            self._misses += 1

        bp = builder()
        bp.captured_at = ts
        with self._lock:
            self._by_window[wkey] = bp
            if len(self._by_window) > self.max_apps:
                oldest_k = min(self._by_window, key=lambda k: self._by_window[k].captured_at)
                self._by_window.pop(oldest_k, None)
        return bp

    def latest(self) -> SemanticBlueprint | None:
        with self._lock:
            return self._by_window.get(self._active_key)

    def invalidate(self, app: str | None = None) -> None:
        with self._lock:
            if app is None:
                self._by_window.clear()
                self._active_key = ""
            else:
                prefix = f"{app.lower().strip()}::"
                for k in [k for k in self._by_window if k.startswith(prefix)]:
                    self._by_window.pop(k, None)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "cached_windows": len(self._by_window),
                "hits": self._hits,
                "misses": self._misses,
            }


BLUEPRINT_CACHE = SemanticBlueprintCache(ttl_sec=2.5)
