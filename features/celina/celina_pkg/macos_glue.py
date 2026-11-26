"""
Celina AI — macOS Muscle-Memory & System-Polish Coordination Glue (`macos_glue.py`)

Unifies the four cross-subsystem orchestration contracts that make Linux feel as
coherent as macOS without replacing native tools:
  1. `FocusModeController`: toggles GNOME DND via `gsettings org.gnome.desktop.notifications
     show-banners`, evaluates calendar/active-app meeting state, and filters per-app
     `org.freedesktop.Notifications` payloads against active Focus allowlists.
  2. `WorkspaceMemoryManager`: records `(app_class -> workspace_index)` spatial memory
     (paired with `org.gnome.mutter dynamic-workspaces=false`) and generates login
     restore actions so apps return to their assigned Spaces.
  3. `FinderTagStore` + `FileHabitAdvisor`: stores macOS-style colored Finder tags in
     Linux `user.xdg.tags` xattrs with automatic SQLite fallback (`finder_tags.db`) for
     FAT32/exFAT/network mounts, and proposes Quick Actions / tags for frequently opened files.
  4. `TerminalKeyboardContext`: detects focused terminal emulators so `Cmd+C/V` maps to
     `Ctrl+Shift+C/V` while physical `Ctrl+C` always sends `SIGINT`.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import config

FINDER_TAG_COLORS: tuple[str, ...] = (
    "Red",
    "Orange",
    "Yellow",
    "Green",
    "Blue",
    "Purple",
    "Gray",
)

TERMINAL_APP_CLASSES: frozenset[str] = frozenset({
    "org.gnome.terminal",
    "org.gnome.console",
    "org.gnome.ptyxis",
    "kitty",
    "alacritty",
    "org.wezfurlong.wezterm",
    "wezterm",
    "foot",
    "org.kde.konsole",
    "konsole",
    "tilix",
    "terminator",
    "xterm",
    "ghostty",
})


# ---------------------------------------------------------------------------
# 1. Focus Modes & Per-App Notification Filter (gsettings show-banners + D-Bus)
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class FocusProfile:
    name: str
    show_banners: bool
    allowed_apps: tuple[str, ...] = ()
    allow_critical_urgency: bool = True


DEFAULT_FOCUS_PROFILES: dict[str, FocusProfile] = {
    "off": FocusProfile(name="off", show_banners=True, allowed_apps=(), allow_critical_urgency=True),
    "work": FocusProfile(
        name="work",
        show_banners=False,
        allowed_apps=("celina", "calendar", "gnome-calendar", "slack", "linear", "thunderbird"),
        allow_critical_urgency=True,
    ),
    "meeting": FocusProfile(
        name="meeting",
        show_banners=False,
        allowed_apps=("celina", "gnome-calendar"),
        allow_critical_urgency=True,
    ),
    "sleep": FocusProfile(
        name="sleep",
        show_banners=False,
        allowed_apps=("celina", "clock", "gnome-clocks"),
        allow_critical_urgency=True,
    ),
    "personal": FocusProfile(
        name="personal",
        show_banners=False,
        allowed_apps=("celina", "signal", "whatsapp", "messages", "telegram"),
        allow_critical_urgency=True,
    ),
}


class FocusModeController:
    """Orchestrates GNOME `show-banners` (DND) + per-app notification filtering."""

    def __init__(
        self,
        *,
        profiles: Optional[dict[str, FocusProfile]] = None,
        gsettings_runner: Optional[Any] = None,
    ):
        self.profiles = dict(DEFAULT_FOCUS_PROFILES)
        if profiles:
            self.profiles.update(profiles)
        self.active_mode: str = "off"
        self._runner = gsettings_runner

    def build_gsettings_cmd(self, show_banners: bool) -> list[str]:
        return [
            "gsettings",
            "set",
            "org.gnome.desktop.notifications",
            "show-banners",
            "true" if show_banners else "false",
        ]

    def set_mode(self, mode: str, *, apply_gsettings: bool = True) -> dict[str, Any]:
        key = (mode or "off").strip().lower()
        if key not in self.profiles:
            raise ValueError(f"Unknown Focus mode '{mode}'. Allowed: {sorted(self.profiles)}")
        prof = self.profiles[key]
        self.active_mode = key
        cmd = self.build_gsettings_cmd(prof.show_banners)
        applied = False
        if apply_gsettings:
            try:
                if self._runner is not None:
                    self._runner(cmd)
                    applied = True
                else:
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, shell=False)
                    applied = res.returncode == 0
            except Exception:
                applied = False
        return {
            "mode": key,
            "show_banners": prof.show_banners,
            "allowed_apps": list(prof.allowed_apps),
            "gsettings_cmd": cmd,
            "applied": applied,
        }

    def allow_notification(self, app_name: str, *, urgency: int = 1) -> bool:
        """
        Per-app filter for `org.freedesktop.Notifications` interception:
          - urgency: 0=low, 1=normal, 2=critical
        """
        prof = self.profiles.get(self.active_mode, DEFAULT_FOCUS_PROFILES["off"])
        if prof.show_banners:
            return True
        if urgency >= 2 and prof.allow_critical_urgency:
            return True
        norm = (app_name or "").strip().lower()
        return any(allowed in norm or norm == allowed for allowed in prof.allowed_apps)

    def evaluate_calendar_focus(
        self,
        *,
        active_events: list[dict[str, Any]],
        active_app: str = "",
        apply_gsettings: bool = False,
    ) -> dict[str, Any]:
        """Auto-enables `meeting` Focus when an active calendar meeting or call app is running."""
        call_apps = ("zoom", "meet", "teams", "jitsi", "obs")
        in_meeting = bool(active_events) or any(c in (active_app or "").lower() for c in call_apps)
        target = "meeting" if in_meeting else "off"
        if target != self.active_mode:
            return self.set_mode(target, apply_gsettings=apply_gsettings)
        return {"mode": self.active_mode, "changed": False}


# ---------------------------------------------------------------------------
# 2. Persistent Workspace Spatial Memory (macOS Spaces App Assignments)
# ---------------------------------------------------------------------------
class WorkspaceMemoryManager:
    """Records which application belongs to which fixed GNOME workspace and restores on login."""

    def __init__(self, state_path: Optional[Path] = None, *, num_workspaces: int = 4):
        self.state_path = (
            Path(state_path)
            if state_path is not None
            else (config.BASE_DIR / "workspace-memory.json")
        )
        self.num_workspaces = max(1, int(num_workspaces))
        self._lock = threading.RLock()
        self._map: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._map = {str(k).lower(): int(v) for k, v in raw.items()}
        except Exception:
            self._map = {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._map, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def assign_app(self, app_class: str, workspace_index: int) -> dict[str, Any]:
        app = (app_class or "").strip().lower()
        if not app:
            raise ValueError("app_class must be non-empty")
        ws = max(0, min(self.num_workspaces - 1, int(workspace_index)))
        with self._lock:
            self._map[app] = ws
            self._save()
        return {"app_class": app, "workspace": ws}

    def workspace_for_app(self, app_class: str) -> Optional[int]:
        with self._lock:
            return self._map.get((app_class or "").strip().lower())

    def build_restore_plan(self, running_windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Given currently open windows (`[{"id": "...", "app_class": "firefox", "workspace": 0}]`),
        returns deterministic workspace-move actions to restore macOS Spaces spatial memory.
        """
        actions: list[dict[str, Any]] = []
        with self._lock:
            for win in running_windows:
                app = str(win.get("app_class") or "").strip().lower()
                cur_ws = int(win.get("workspace", 0))
                target_ws = self._map.get(app)
                if target_ws is not None and target_ws != cur_ws:
                    actions.append(
                        {
                            "window_id": str(win.get("id") or ""),
                            "app_class": app,
                            "from_workspace": cur_ws,
                            "to_workspace": target_ws,
                        }
                    )
        return actions


# ---------------------------------------------------------------------------
# 3. Finder-Style Colored Tags (xattr + Automatic SQLite Fallback) &
#    File Action Suggestions (`FileHabitAdvisor`)
# ---------------------------------------------------------------------------
class FinderTagStore:
    """
    FrostFinder / macOS Finder-compatible colored file tags:
      - Stores tags in Linux extended attribute `user.xdg.tags` whenever supported.
      - Automatically falls back to local SQLite (`finder_tags.db`) for FAT32/exFAT,
        network mounts (SMB/WebDAV), or containers without user xattr support.
    """

    XATTR_KEY = "user.xdg.tags"

    def __init__(self, db_path: Optional[Path] = None, *, prefer_xattr: bool = True):
        self.db_path = Path(db_path) if db_path is not None else (config.BASE_DIR / "finder_tags.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.prefer_xattr = bool(prefer_xattr)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS file_tags (
                    file_path TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (file_path, tag)
                );
                CREATE TABLE IF NOT EXISTS file_open_events (
                    file_path TEXT NOT NULL,
                    opened_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_foe_path ON file_open_events (file_path, opened_at);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def _canonical_path(self, path: Path | str) -> str:
        return str(Path(path).expanduser().resolve())

    def add_tag(self, path: Path | str, tag: str) -> dict[str, Any]:
        p = self._canonical_path(path)
        clean_tag = (tag or "").strip()
        if not clean_tag:
            raise ValueError("tag must be non-empty")
        existing = self.get_tags(p)
        updated = sorted(set(existing) | {clean_tag})
        backend = "sqlite_fallback"

        if self.prefer_xattr and hasattr(os, "setxattr") and Path(p).exists():
            try:
                os.setxattr(p, self.XATTR_KEY, ",".join(updated).encode("utf-8"))
                backend = "xattr"
            except OSError:
                backend = "sqlite_fallback"

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO file_tags (file_path, tag, updated_at) VALUES (?, ?, ?)",
                (p, clean_tag, time.time()),
            )
            self._conn.commit()
        return {"file_path": p, "tags": updated, "storage_backend": backend}

    def get_tags(self, path: Path | str) -> list[str]:
        p = self._canonical_path(path)
        tags: set[str] = set()
        if self.prefer_xattr and hasattr(os, "getxattr") and Path(p).exists():
            try:
                raw = os.getxattr(p, self.XATTR_KEY).decode("utf-8", errors="ignore")
                tags.update(t.strip() for t in raw.split(",") if t.strip())
            except OSError:
                pass
        with self._lock:
            rows = self._conn.execute("SELECT tag FROM file_tags WHERE file_path = ?", (p,)).fetchall()
            tags.update(str(r["tag"]) for r in rows)
        return sorted(tags)

    def find_by_tag(self, tag: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path FROM file_tags WHERE lower(tag) = lower(?) ORDER BY updated_at DESC",
                ((tag or "").strip(),),
            ).fetchall()
            return [str(r["file_path"]) for r in rows]

    def record_file_open(
        self,
        path: Path | str,
        *,
        suggest_threshold: int = 3,
        window_sec: float = 86400.0 * 7,
    ) -> dict[str, Any]:
        """
        File habit tracker: when a file is opened >= `suggest_threshold` times within
        the rolling window and has no Finder tag yet, proposes a colored tag / Quick Action.
        """
        p = self._canonical_path(path)
        now = time.time()
        with self._lock:
            self._conn.execute("INSERT INTO file_open_events (file_path, opened_at) VALUES (?, ?)", (p, now))
            self._conn.commit()
            cnt = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM file_open_events WHERE file_path = ? AND opened_at >= ?",
                    (p, now - float(window_sec)),
                ).fetchone()[0]
            )
        cur_tags = self.get_tags(p)
        suggestion = None
        if cnt >= max(2, int(suggest_threshold)) and not cur_tags:
            ext = Path(p).suffix.lower()
            quick_act = "Convert to PDF" if ext in {".docx", ".odt", ".pptx", ".md"} else "Pin with Blue Tag"
            suggestion = {
                "file_path": p,
                "open_count": cnt,
                "suggested_tag": "Blue",
                "suggested_quick_action": quick_act,
                "message": f"You opened '{Path(p).name}' {cnt} times recently — tag it 'Blue' or run '{quick_act}'?",
            }
        return {"file_path": p, "open_count": cnt, "tags": cur_tags, "suggestion": suggestion}


# ---------------------------------------------------------------------------
# 4. Terminal-Aware Keyboard Context (Cmd+C -> Ctrl+Shift+C, Ctrl+C == SIGINT)
# ---------------------------------------------------------------------------
def is_terminal_window(active_window: str) -> bool:
    low = (active_window or "").strip().lower()
    if not low:
        return False
    return any(term in low for term in TERMINAL_APP_CLASSES) or "terminal" in low or "bash" in low


def resolve_cmd_c_behavior(active_window: str, *, terminal_mode: str = "insert") -> dict[str, Any]:
    """
    Returns the exact key-remapping contract for the focused window:
      - Inside terminals: physical Ctrl+C ALWAYS remains SIGINT; Cmd+C/V/X map to
        either universal `C-insert` / `S-insert` / `S-delete` (`terminal_mode='insert'`)
        or `C-S-c` / `C-S-v` (`terminal_mode='ctrl_shift'`).
      - Outside terminals: Cmd+C -> Ctrl+C, Cmd+V -> Ctrl+V, Cmd+X -> Ctrl+X.
    """
    in_term = is_terminal_window(active_window)
    use_insert = (terminal_mode or "insert").strip().lower() == "insert"
    return {
        "active_window": active_window,
        "is_terminal": in_term,
        "cmd_c_sequence": ("C-insert" if use_insert else "C-S-c") if in_term else "C-c",
        "cmd_v_sequence": ("S-insert" if use_insert else "C-S-v") if in_term else "C-v",
        "cmd_x_sequence": "S-delete" if in_term else "C-x",
        "cmd_c_ctrl_shift": "C-S-c" if in_term else "C-c",
        "cmd_c_insert": "C-insert" if in_term else "C-c",
        "physical_ctrl_c": "SIGINT" if in_term else "C-c",
        "suppress_cmd_c_to_ctrl_c": in_term,
    }


# ---------------------------------------------------------------------------
# 5. Ubuntu 26.04 Verification Contracts:
#    - GNOME Sushi >= 50.0 version gate (fixes Resolute Nautilus 50 preview breakage)
#    - Extension ABI Pinning & Simple Taskbar Dock-Mode magnification constraint
#    - Dual-Timer Backup Health Monitor (Timeshift + Borgmatic/Borg > 48h Island Badge)
# ---------------------------------------------------------------------------
def check_gnome_sushi_version(version_str: str) -> dict[str, Any]:
    """
    Ubuntu 26.04 (Resolute) Launchpad SRU contract:
    `gnome-sushi` < 50.0 is incompatible with Nautilus 50 ("not usable at all").
    Requires version >= 50.0 (or FrostFinder's built-in Quick Look).
    """
    import re as _re

    m = _re.search(r"(\d+)\.(\d+)", (version_str or "").strip())
    if not m:
        return {
            "ok": False,
            "version": version_str,
            "reason": "Unable to parse gnome-sushi version; verify >= 50.0 or use FrostFinder Quick Look.",
        }
    major, minor = int(m.group(1)), int(m.group(2))
    ok = major >= 50
    return {
        "ok": ok,
        "version": f"{major}.{minor}",
        "reason": (
            f"gnome-sushi {major}.{minor} >= 50.0 (compatible with Ubuntu 26.04 Nautilus 50)"
            if ok
            else f"gnome-sushi {major}.{minor} < 50.0 is broken on Ubuntu 26.04; upgrade to >= 50.0 or use FrostFinder"
        ),
    }


@dataclasses.dataclass(frozen=True)
class ExtensionPin:
    uuid: str
    pinned_version: str
    supported_gnome_majors: tuple[int, ...]
    notes: str = ""


PINNED_MACOS_EXTENSIONS: dict[str, ExtensionPin] = {
    "stage-manager-for-linux": ExtensionPin(
        "stage-manager-for-linux",
        "pinned-2026",
        (48, 49, 50),
        "Requires Mutter ABI lock; pin before GNOME Shell major upgrades",
    ),
    "nexstage": ExtensionPin(
        "nexstage",
        "pinned-2026",
        (46, 47, 48, 49, 50),
        "Arc/vertical Stage Manager with multi-monitor support",
    ),
    "simple-taskbar": ExtensionPin(
        "simple-taskbar",
        "7.3.0",
        (48, 49, 50, 51),
        "Icon magnification + Magic Lamp requires Dock mode with app labels hidden",
    ),
    "global-menu": ExtensionPin(
        "global-menu",
        "2026.1",
        (48, 49, 50),
        "Exports native D-Bus menus + 292 shortcut-based Electron app menus (GTK4 apps lack menu bar)",
    ),
    "tiling-assistant@leleat-on-github": ExtensionPin(
        "tiling-assistant@leleat-on-github",
        "2026.1",
        (46, 47, 48, 49, 50),
        "Includes GNOME Shell 50 Wayland compositor fix & gap-preserving semi-maximize",
    ),
}


def validate_simple_taskbar_config(*, mode: str, show_app_labels: bool) -> dict[str, Any]:
    """
    Simple Taskbar 7.3.0 constraint: macOS icon magnification is ONLY active in
    Dock mode with application labels hidden (unavailable in Default GNOME Panel mode).
    """
    m = (mode or "").strip().lower()
    ok = (m == "dock") and (not bool(show_app_labels))
    return {
        "magnification_available": ok,
        "mode": m,
        "show_app_labels": bool(show_app_labels),
        "reason": (
            "Simple Taskbar Dock mode + hidden labels -> icon magnification & Magic Lamp active"
            if ok
            else "Simple Taskbar magnification unavailable: switch from panel mode to 'dock' and hide app labels"
        ),
    }


def check_extension_abi(ext_key: str, gnome_major: int) -> dict[str, Any]:
    pin = PINNED_MACOS_EXTENSIONS.get(ext_key)
    if pin is None:
        return {"known": False, "compatible": False, "reason": f"Unpinned extension '{ext_key}'"}
    compat = int(gnome_major) in pin.supported_gnome_majors
    return {
        "known": True,
        "uuid": pin.uuid,
        "pinned_version": pin.pinned_version,
        "compatible": compat,
        "notes": pin.notes,
    }


class BackupHealthMonitor:
    """
    Celina Backup Health contract:
    Inspects `systemctl list-timers` (Timeshift + Borgmatic / `celina-borg-backup.timer`)
    and emits a Dynamic Island warning badge if either backup timer has not fired in > 48 hours.
    """

    def __init__(self, *, stale_threshold_hours: float = 48.0):
        self.stale_threshold_sec = float(stale_threshold_hours) * 3600.0

    def evaluate_timers(
        self,
        timers: list[dict[str, Any]],
        *,
        now_epoch: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        `timers`: list of dicts such as `[{"unit": "celina-borg-backup.timer", "last_epoch": ...}]`
        (matching `systemctl list-timers --output=json` after converting usec -> epoch sec).
        """
        now = float(now_epoch) if now_epoch is not None else time.time()
        required_families = {
            "timeshift": ("timeshift",),
            "borg": ("borgmatic", "celina-borg-backup"),
        }
        overdue: list[dict[str, Any]] = []
        healthy: list[str] = []

        for family, keywords in required_families.items():
            matched = [
                t
                for t in timers
                if any(kw in str(t.get("unit") or t.get("activates") or "").lower() for kw in keywords)
            ]
            if not matched:
                overdue.append({"family": family, "reason": "timer_missing", "hours_since_last": None})
                continue
            # Pick the most recent run within this backup family
            last_epochs = [float(t.get("last_epoch") or 0.0) for t in matched]
            latest = max(last_epochs) if last_epochs else 0.0
            if latest <= 0.0:
                overdue.append({"family": family, "reason": "never_fired", "hours_since_last": None})
                continue
            age_sec = max(0.0, now - latest)
            if age_sec > self.stale_threshold_sec:
                overdue.append(
                    {
                        "family": family,
                        "reason": "stale_over_48h",
                        "hours_since_last": round(age_sec / 3600.0, 1),
                    }
                )
            else:
                healthy.append(family)

        island_badge = None
        if overdue:
            fams = ", ".join(o["family"] for o in overdue)
            island_badge = {
                "badge": "backup_overdue",
                "severity": "warning",
                "title": f"Backup Overdue ({fams})",
                "message": f"Backup timer(s) [{fams}] have not fired in >{int(self.stale_threshold_sec // 3600)}h.",
                "overdue_timers": overdue,
            }
        return {
            "healthy": len(overdue) == 0,
            "healthy_families": healthy,
            "overdue_timers": overdue,
            "island_badge": island_badge,
        }
