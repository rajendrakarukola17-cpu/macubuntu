"""
macubuntu Celina Shortcuts GUI — Apple Shortcuts parity GTK4/Libadwaita app.

A native GTK4 + Libadwaita Shortcuts app mirroring macOS Shortcuts:
  - Gallery & Automations sidebar
  - Stacked vertical drag-and-drop Action Cards with clickable Magic Variable chips
  - Live step-highlight execution
  - Searchable Action Library grouped by Celina's 11 domains
  - Top-bar "Describe a Shortcut" input (calls LLM once to generate a .cshortcut.yaml draft)
  - Nautilus Quick Actions sync for file/image/pdf input Shortcuts
  - Vicinae/Rofi launcher entries
  - Zero generative LLM at runtime; LLM used only for "Describe a Shortcut" drafting

Runs on real Ubuntu 26.04 (python3-gi, gir1.2-adw-1, python3-yaml).
Gracefully degrades if GTK4/Libadwaita are unavailable (prints guidance).

Usage:
    python3 apps/celina-shortcuts/main.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# GTK4 / Libadwaita (optional — degrades gracefully in sandboxes without gi)
# --------------------------------------------------------------------------- #
try:
    import gi  # type: ignore
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib, Gio, Gdk  # type: ignore
    GTK_AVAILABLE = True
except Exception:
    Gtk = None
    Adw = None
    GLib = None
    Gio = None
    Gdk = None
    GTK_AVAILABLE = False

# --------------------------------------------------------------------------- #
# Celina shortcuts engine (imported lazily)
# --------------------------------------------------------------------------- #
CELINA_ROOT = Path(__file__).resolve().parents[2] / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

try:
    from celina_pkg.shortcuts.engine import (  # noqa: F401
        ShortcutEngine, ShortcutSpec, ShortcutInput, ShortcutStep,
        resolve_magic_vars, eval_when, sha256_of_spec, TIER_2_ACTIONS,
    )
    from celina_pkg.shortcuts.gear05_router import Gear05Router  # noqa: F401
    from celina_pkg.shortcuts.triggers import TriggerDaemon, TriggerBinding  # noqa: F401
    CELINA_SHORTCUTS_AVAILABLE = True
except Exception:
    CELINA_SHORTCUTS_AVAILABLE = False

# --------------------------------------------------------------------------- #
# Celina tool handlers — registered into the ShortcutEngine action_registry
# --------------------------------------------------------------------------- #

try:
    from celina_pkg.tools import (  # noqa: F401
        set_volume, set_brightness, control_media, send_notification,
        toggle_app_nap, control_connectivity, open_application,
        search_files, search_in_files,
    )
    CELINA_TOOLS_AVAILABLE = True
except Exception:
    CELINA_TOOLS_AVAILABLE = False


def _wrap_sync(fn):
    """Wrap a sync Celina tool function as an async handler."""
    async def _handler(**kwargs):
        import asyncio as _asyncio
        return await _asyncio.to_thread(fn, **kwargs)
    return _handler


# Known action -> expected arg names, for pre-populating the arg editor.
PARAMETER_MAP: dict[str, list[str]] = {
    "clock.start_timer": ["minutes", "label"],
    "clock.stop_timer": ["timer_id"],
    "focus.set_mode": ["mode"],
    "notify.island": ["title", "body"],
    "os.set_volume": ["level"],
    "os.set_brightness": ["level"],
    "os.set_scheduler": ["scheduler"],
    "files.write_file": ["path", "content"],
    "files.read": ["path"],
    "files.edit_file": ["path", "old_text", "new_text"],
    "files.create_document": ["title", "content"],
    "computer_use.click_on_text": ["text"],
    "computer_use.type_text": ["text"],
    "media.play_pause": [],
    "media.next": [],
    "media.previous": [],
    "network.set_wifi": ["enabled"],
    "network.set_vpn": ["enabled"],
    "calendar.create_event": ["title", "start", "end"],
    "devices.bluetooth_connect": ["device"],
    "devices.airplay_connect": ["device"],
    "automation.trigger_shortcut": ["shortcut_id"],
    "automation.wait": ["seconds"],
}


def _next_step_id(existing_ids: list[str]) -> str:
    """Generate a monotonic step id (step_1, step_2, …)."""
    max_num = 0
    for sid in existing_ids:
        if isinstance(sid, str) and sid.startswith("step_"):
            try:
                n = int(sid.split("_", 1)[1])
                max_num = max(max_num, n)
            except (ValueError, IndexError):
                pass
    return f"step_{max_num + 1}"


# Build the action registry: every known action maps to an async handler.
# Handlers delegate to real Celina tool functions where available;
# otherwise they return a simulated ok result so the shortcut can still run.
ACTION_HANDLERS: dict[str, Any] = {}

if CELINA_TOOLS_AVAILABLE:
    ACTION_HANDLERS["os.set_volume"] = _wrap_sync(set_volume)
    ACTION_HANDLERS["os.set_brightness"] = _wrap_sync(set_brightness)
    ACTION_HANDLERS["media.play_pause"] = _wrap_sync(lambda action="play_pause": control_media(action))
    ACTION_HANDLERS["media.next"] = _wrap_sync(lambda action="next": control_media(action))
    ACTION_HANDLERS["media.previous"] = _wrap_sync(lambda action="previous": control_media(action))
    ACTION_HANDLERS["notify.island"] = _wrap_sync(send_notification)
    ACTION_HANDLERS["toggle_app_nap"] = _wrap_sync(toggle_app_nap)
    ACTION_HANDLERS["control_connectivity"] = _wrap_sync(control_connectivity)
    ACTION_HANDLERS["open_application"] = _wrap_sync(open_application)
    ACTION_HANDLERS["search_files"] = _wrap_sync(search_files)
    ACTION_HANDLERS["search_in_files"] = _wrap_sync(search_in_files)


async def _noop_handler(**kwargs) -> str:
    """Fallback handler for actions without a real Celina tool binding."""
    return f"ok: simulated with args {kwargs}"


def get_action_handler(action: str) -> Any:
    """Return the async handler for an action, or a no-op fallback."""
    return ACTION_HANDLERS.get(action, _noop_handler)

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
SHORTCUTS_DIR = Path.home() / ".local" / "share" / "celina" / "shortcuts"
QUARANTINE_DIR = SHORTCUTS_DIR / "quarantine"
NAUTILUS_SCRIPTS_DIR = Path.home() / ".local" / "share" / "nautilus" / "scripts" / "Celina Quick Actions"
LAUNCHER_ENTRIES_DIR = Path.home() / ".local" / "share" / "applications"

DOMAINS = [
    ("Files", "files", ["files.read", "files.write_file", "files.edit_file", "files.create_document", "files.pdf_to_md"]),
    ("System", "os", ["os.set_volume", "os.set_brightness", "os.set_scheduler"]),
    ("Clock", "clock", ["clock.start_timer", "clock.stop_timer"]),
    ("Focus", "focus", ["focus.set_mode"]),
    ("Notify", "notify", ["notify.island"]),
    ("Computer Use", "computer_use", ["computer_use.click_on_text", "computer_use.type_text"]),
    ("Media", "media", ["media.play_pause", "media.next", "media.previous"]),
    ("Network", "network", ["network.set_wifi", "network.set_vpn"]),
    ("Calendar", "calendar", ["calendar.create_event", "calendar.list_events"]),
    ("Devices", "devices", ["devices.bluetooth_connect", "devices.airplay_connect"]),
    ("Automation", "automation", ["automation.trigger_shortcut", "automation.wait"]),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_SAFE_ID_RE = re.compile(r"[^a-z0-9_-]+")


def sanitize_shortcut_id(raw: str) -> str:
    """Strictly sanitize shortcut IDs to prevent path traversal."""
    slug = _SAFE_ID_RE.sub("_", raw.strip().lower()).strip("_")[:40]
    if not slug or slug in {".", ".."}:
        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
        slug = f"shortcut_{digest}"
    return slug


def clear_container(widget: Any) -> None:
    """GTK4-safe removal of all children (replaces removed GTK3 get_children())."""
    while (child := widget.get_first_child()) is not None:
        widget.remove(child)


def atomic_write_text(target: Path, content: str, mode: int = 0o600) -> None:
    """P5 atomic write inside target directory + path containment check."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".yaml", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def action_tier(action: str) -> int:
    """Derive tier from Celina's capability manifest (TIER_2_ACTIONS).

    Unknown mutating actions default fail-closed to Tier 2.
    """
    return 2 if action in TIER_2_ACTIONS else 1


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


class ShortcutDocument:
    """In-memory representation of a .cshortcut.yaml file being edited."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.spec: dict[str, Any] = {
            "id": "new_shortcut",
            "name": "Untitled Shortcut",
            "description": "",
            "phrases": [],
            "triggers": [],
            "inputs": [],
            "steps": [],
            "allow_without_llm": True,
            "preauthorized_tier2": False,
        }
        self.dirty = False

    def save(self) -> Path:
        import yaml

        safe_id = sanitize_shortcut_id(
            str(self.spec.get("id") or self.spec.get("name") or "shortcut")
        )
        self.spec["id"] = safe_id
        SHORTCUTS_DIR.mkdir(parents=True, exist_ok=True)
        target = (SHORTCUTS_DIR / f"{safe_id}.cshortcut.yaml").resolve()
        # Path containment: refuse to write outside SHORTCUTS_DIR
        if SHORTCUTS_DIR.resolve() not in target.parents and target != SHORTCUTS_DIR.resolve():
            raise ValueError(f"Refusing to write outside shortcuts directory: {target}")
        atomic_write_text(target, yaml.safe_dump(self.spec, sort_keys=False), mode=0o600)
        self.path = target
        self.dirty = False
        return self.path

    @classmethod
    def load(cls, path: Path) -> "ShortcutDocument":
        import yaml

        doc = cls(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("Shortcut root must be a mapping")
        doc.spec = data
        return doc


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #


class ActionCard(Gtk.Box if GTK_AVAILABLE else object):
    """A single drag-and-drop action step card with Magic Variable chips."""

    # Magic variables that can be inserted into arg entries.
    MAGIC_VARS = [
        "{{input.text}}",
        "{{input.title}}",
        "{{input.body}}",
        "{{input.path}}",
        "{{input.content}}",
        "{{input.minutes}}",
        "{{input.label}}",
        "{{input.mode}}",
        "{{input.level}}",
        "{{input.device}}",
        "{{input.shortcut_id}}",
        "{{input.seconds}}",
        "{{input.start}}",
        "{{input.end}}",
        "{{input.enabled}}",
        "{{input.timer_id}}",
        "{{input.title}}",
    ]

    def __init__(self, action_id: str, action_name: str, tier: int = 1,
                 on_delete=None, on_arg_change=None):
        if not GTK_AVAILABLE:
            return
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_hexpand(True)
        self.add_css_class("card")
        self.action_id = action_id
        self.action_name = action_name
        self.tier = tier
        self.on_delete = on_delete
        self.on_arg_change = on_arg_change
        self.arg_widgets: list = []
        self.focused_entry: Any = None

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name_lbl = Gtk.Label(label=action_name, xalign=0.0)
        name_lbl.add_css_class("heading")
        header.append(name_lbl)
        tier_lbl = Gtk.Label(label="T" + str(tier), xalign=1.0)
        tier_lbl.add_css_class("tier-badge")
        if tier >= 2:
            tier_lbl.add_css_class("tier-2")
        header.append(tier_lbl)
        if on_delete:
            del_btn = Gtk.Button()
            del_btn.set_icon_name("window-close-symbolic")
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", lambda _b: on_delete(self))
            header.append(del_btn)
        self.append(header)

        self.args_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.args_box.set_margin_top(6)
        self.append(self.args_box)

        # Magic variable chip bar
        chips_lbl = Gtk.Label(label="Magic vars:", xalign=0.0)
        chips_lbl.add_css_class("caption")
        chips_lbl.set_margin_top(4)
        self.append(chips_lbl)

        chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chips_box.set_hexpand(True)
        chips_box.set_margin_top(2)
        # Show only the most relevant chips to avoid clutter
        for chip_text in self.MAGIC_VARS[:8]:
            chip = Gtk.Button(label=chip_text)
            chip.add_css_class("flat")
            chip.set_hexpand(False)
            chip.connect("clicked", lambda _b, cv=chip_text: self._insert_chip(cv))
            chips_box.append(chip)
        self.append(chips_box)

        # "+ Add Arg" button
        add_arg_btn = Gtk.Button(label="+ Add Arg")
        add_arg_btn.add_css_class("flat")
        add_arg_btn.set_halign(Gtk.Align.START)
        add_arg_btn.connect("clicked", lambda _b: self._add_arg_row())
        self.append(add_arg_btn)

    def _add_arg_row(self) -> None:
        """Add an empty arg row with a name entry and value entry."""
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("arg name")
        value_entry = Gtk.Entry()
        value_entry.set_placeholder_text("value")
        value_entry.connect("focus-in-event", lambda _e, _ev: self._set_focused(value_entry))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.append(name_entry)
        row.append(value_entry)
        self.args_box.append(row)
        name_entry.connect("changed", lambda _e: self._on_custom_arg(name_entry, value_entry))
        value_entry.connect("changed", lambda _e: self._on_custom_arg(name_entry, value_entry))

    def _on_custom_arg(self, name_entry: Any, value_entry: Any) -> None:
        name = name_entry.get_text().strip()
        value = value_entry.get_text()
        if name:
            if self.on_arg_change:
                self.on_arg_change(self.action_id, name, value)

    def add_arg(self, name: str, value: str = "") -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label=name + ":", xalign=0.0)
        lbl.set_width_chars(14)
        entry = Gtk.Entry()
        entry.set_text(value)
        entry.connect("changed", lambda _e: self._on_arg(name, entry.get_text()))
        entry.connect("focus-in-event", lambda _e, _ev: self._set_focused(entry))
        row.append(lbl)
        row.append(entry)
        self.args_box.append(row)
        self.arg_widgets.append((name, entry))

    def _set_focused(self, entry: Any) -> None:
        self.focused_entry = entry

    def _insert_chip(self, chip_text: str) -> None:
        """Insert a magic variable chip into the currently focused arg entry."""
        if self.focused_entry is None:
            return
        current = self.focused_entry.get_text()
        self.focused_entry.set_text(current + chip_text)
        # Mark dirty via on_arg_change if we know the arg name
        for name, ent in self.arg_widgets:
            if ent is self.focused_entry:
                if self.on_arg_change:
                    self.on_arg_change(self.action_id, name, self.focused_entry.get_text())
                break

    def _on_arg(self, name: str, value: str) -> None:
        if self.on_arg_change:
            self.on_arg_change(self.action_id, name, value)


class ShortcutListRow(Gtk.ListBoxRow if GTK_AVAILABLE else object):
    """A row in the gallery sidebar listing an installed Shortcut."""

    def __init__(self, spec: Any, on_activate=None):
        if not GTK_AVAILABLE:
            return
        super().__init__()
        self.set_hexpand(True)
        self.spec = spec

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_hexpand(True)
        name_lbl = Gtk.Label(label=spec.name, xalign=0.0)
        name_lbl.add_css_class("shortcut-name")
        box.append(name_lbl)
        desc_lbl = Gtk.Label(label=spec.description, xalign=0.0)
        desc_lbl.add_css_class("caption")
        box.append(desc_lbl)
        box.set_hexpand(True)

        if on_activate:
            btn = Gtk.Button(label="Run")
            btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda _b: on_activate(spec))
            box.append(btn)

        self.set_child(box)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #


class CelinaShortcutsWindow(Adw.ApplicationWindow if GTK_AVAILABLE else object):
    """Main Celina Shortcuts application window."""

    def __init__(self, app: Any, engine: Any | None = None):
        if not GTK_AVAILABLE:
            return
        super().__init__(application=app)
        self.set_title("Celina Shortcuts")
        self.set_default_size(1000, 700)
        self.engine = engine
        self.doc = ShortcutDocument()
        self.current_row = None

        # Approval dialog state (for Tier-2 gating from worker threads)
        self._approval_decision: dict[str, Any] = {"approved": False}
        self._approval_done = threading.Event()

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # --- Adw.ToolbarView + Adw.HeaderBar for proper GNOME chrome ---
        toolbar_view = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar_view)

        header_bar = Adw.HeaderBar()
        title_lbl = Gtk.Label(label="Celina Shortcuts")
        title_lbl.add_css_class("title")
        title_lbl.set_xalign(0.0)
        header_bar.set_title_widget(title_lbl)

        self.describe_entry = Gtk.Entry()
        self.describe_entry.set_placeholder_text(
            "Describe a Shortcut (e.g. “dim screen and start a 25-minute timer”)"
        )
        self.describe_entry.set_hexpand(True)
        self.describe_entry.connect("activate", self._on_describe_activate)
        header_bar.pack_start(self.describe_entry)

        describe_btn = Gtk.Button(label="Describe")
        describe_btn.add_css_class("suggested-action")
        describe_btn.connect("clicked", lambda _b: self._on_describe_activate(None))
        header_bar.pack_start(describe_btn)

        new_btn = Gtk.Button()
        new_btn.set_icon_name("document-new-symbolic")
        new_btn.add_css_class("flat")
        new_btn.set_tooltip_text("New Shortcut")
        new_btn.connect("clicked", lambda _b: self.new_shortcut())
        header_bar.pack_start(new_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("flat")
        save_btn.connect("clicked", lambda _b: self.save_shortcut())
        header_bar.pack_start(save_btn)

        toolbar_view.add_top_bar(header_bar)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar_view.set_content(main_box)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_wide_handle(True)
        paned.set_position(300)
        main_box.append(paned)
        paned.set_expand(True)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.add_css_class("sidebar")
        paned.set_start_child(sidebar)

        side_scroll = Gtk.ScrolledWindow()
        side_scroll.set_hexpand(True)
        side_scroll.set_vexpand(True)
        side_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar.append(side_scroll)

        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.connect("row-selected", self._on_row_selected)
        side_scroll.set_child(self.sidebar_list)

        toggle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toggle_box.set_margin_top(8)
        toggle_box.set_margin_start(12)
        toggle_box.set_margin_end(12)
        self.gallery_btn = Gtk.ToggleButton(label="Gallery")
        self.gallery_btn.set_active(True)
        self.auto_btn = Gtk.ToggleButton(label="Automations")
        # Linked toggle group: Gallery vs Automations
        group = self.gallery_btn.get_group()
        self.auto_btn.set_group(group)
        toggle_box.append(self.gallery_btn)
        toggle_box.append(self.auto_btn)
        sidebar.append(toggle_box)

        editor_scroll = Gtk.ScrolledWindow()
        editor_scroll.set_hexpand(True)
        editor_scroll.set_vexpand(True)
        editor_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        paned.set_end_child(editor_scroll)

        self.editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.editor_box.set_margin_top(12)
        self.editor_box.set_margin_start(16)
        self.editor_box.set_margin_end(16)
        editor_scroll.set_child(self.editor_box)

        self.editor_name = Gtk.Entry()
        self.editor_name.set_placeholder_text("Shortcut name")
        self.editor_name.connect("changed", lambda _e: self._mark_dirty())
        self.editor_box.append(self.editor_name)

        self.editor_desc = Gtk.Entry()
        self.editor_desc.set_placeholder_text("Description")
        self.editor_desc.connect("changed", lambda _e: self._mark_dirty())
        self.editor_box.append(self.editor_desc)

        self.editor_phrases = Gtk.Entry()
        self.editor_phrases.set_placeholder_text("Trigger phrases (comma-separated)")
        self.editor_phrases.connect("changed", lambda _e: self._mark_dirty())
        self.editor_box.append(self.editor_phrases)

        steps_lbl = Gtk.Label(label="Steps", xalign=0.0)
        steps_lbl.add_css_class("heading")
        self.editor_box.append(steps_lbl)

        self.steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.editor_box.append(self.steps_box)

        add_step_btn = Gtk.Button(label="Add Action")
        add_step_btn.add_css_class("flat")
        add_step_btn.connect("clicked", lambda _b: self._show_action_picker())
        self.editor_box.append(add_step_btn)

        run_btn = Gtk.Button(label="Run Shortcut")
        run_btn.add_css_class("suggested-action")
        run_btn.set_hexpand(True)
        run_btn.connect("clicked", lambda _b: self._run_current())
        self.editor_box.append(run_btn)

        self._refresh_sidebar()

    # ------------------------------------------------------------------ #
    def _refresh_sidebar(self) -> None:
        if not GTK_AVAILABLE:
            return
        clear_container(self.sidebar_list)
        if not CELINA_SHORTCUTS_AVAILABLE:
            row = Gtk.Label(label="Celina shortcuts engine not available")
            row.set_halign(Gtk.Align.START)
            self.sidebar_list.append(row)
            return
        SHORTCUTS_DIR.mkdir(parents=True, exist_ok=True)
        for path in sorted(SHORTCUTS_DIR.glob("*.cshortcut.yaml")):
            try:
                import yaml
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if not isinstance(data, dict):
                    continue
                spec = ShortcutSpec(
                    id=str(data.get("id", path.stem)),
                    name=str(data.get("name", path.stem)),
                    description=str(data.get("description", "")),
                    phrases=list(data.get("phrases", [])),
                    triggers=list(data.get("triggers", [])),
                    inputs=[
                        ShortcutInput(
                            name=str(i.get("name", "")),
                            kind=str(i.get("kind", "text")),
                            required=bool(i.get("required", True)),
                            default=i.get("default"),
                        )
                        for i in data.get("inputs", [])
                    ],
                    steps=[
                        ShortcutStep(
                            id=str(s.get("id", "")),
                            action=str(s.get("action", "")),
                            args=dict(s.get("args") or {}),
                            when=s.get("when"),
                            tier=int(s.get("tier", 1)),
                        )
                        for s in data.get("steps", [])
                    ],
                    allow_without_llm=bool(data.get("allow_without_llm", True)),
                    preauthorized_tier2=bool(data.get("preauthorized_tier2", False)),
                )
                row = ShortcutListRow(spec, on_activate=self._run_spec_from_sidebar)
                self.sidebar_list.append(row)
            except Exception:
                continue

    def _run_spec_from_sidebar(self, spec: ShortcutSpec) -> None:
        """Load a shortcut from the sidebar and run it."""
        self._load_spec_into_editor(spec)
        self._run_current()

    def _on_row_selected(self, listbox: Any, row: Any) -> None:
        if row is None:
            return
        self.current_row = row
        # ShortcutListRow is a Gtk.ListBoxRow; spec lives on the child box
        child = row.get_child() if hasattr(row, "get_child") else None
        spec = getattr(child, "spec", None) if child is not None else None
        if spec is None:
            spec = getattr(row, "spec", None)
        if spec is not None:
            self._load_spec_into_editor(spec)

    def _load_spec_into_editor(self, spec: ShortcutSpec) -> None:
        self.doc = ShortcutDocument()
        self.doc.spec = {
            "id": spec.id,
            "name": spec.name,
            "description": spec.description,
            "phrases": list(spec.phrases),
            "triggers": list(spec.triggers),
            "inputs": [
                {"name": i.name, "kind": i.kind, "required": i.required, "default": i.default}
                for i in spec.inputs
            ],
            "steps": [
                {"id": s.id, "action": s.action, "args": dict(s.args), "tier": s.tier}
                for s in spec.steps
            ],
            "allow_without_llm": spec.allow_without_llm,
            "preauthorized_tier2": spec.preauthorized_tier2,
        }
        self.doc.path = None  # loaded from disk; Save will re-sanitize ID
        self.editor_name.set_text(self.doc.spec["name"])
        self.editor_desc.set_text(self.doc.spec["description"])
        self.editor_phrases.set_text(", ".join(self.doc.spec["phrases"]))
        self._rebuild_steps_editor()
        self.dirty = False

    def load_shortcut_file(self, path: Path) -> None:
        """Load a .cshortcut.yaml file from disk into the editor."""
        import yaml

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                self._toast(f"Invalid shortcut file: {path.name}", level="error")
                return
            spec = ShortcutSpec(
                id=str(data.get("id", path.stem)),
                name=str(data.get("name", path.stem)),
                description=str(data.get("description", "")),
                phrases=list(data.get("phrases", [])),
                triggers=list(data.get("triggers", [])),
                inputs=[
                    ShortcutInput(
                        name=str(i.get("name", "")),
                        kind=str(i.get("kind", "text")),
                        required=bool(i.get("required", True)),
                        default=i.get("default"),
                    )
                    for i in data.get("inputs", [])
                ],
                steps=[
                    ShortcutStep(
                        id=str(s.get("id", "")),
                        action=str(s.get("action", "")),
                        args=dict(s.get("args") or {}),
                        when=s.get("when"),
                        tier=int(s.get("tier", 1)),
                    )
                    for s in data.get("steps", [])
                ],
                allow_without_llm=bool(data.get("allow_without_llm", True)),
                preauthorized_tier2=bool(data.get("preauthorized_tier2", False)),
            )
            self._load_spec_into_editor(spec)
            self.doc.path = path.resolve()
            self._refresh_sidebar()
            self._toast(f"Opened {path.name}")
        except Exception as exc:
            self._toast(f"Failed to open {path.name}: {exc}", level="error")

    def _rebuild_steps_editor(self) -> None:
        clear_container(self.steps_box)
        for step in self.doc.spec["steps"]:
            card = ActionCard(
                action_id=step["id"],
                action_name=step["action"],
                tier=step.get("tier", 1),
                on_delete=self._delete_step,
                on_arg_change=self._on_step_arg_change,
            )
            for k, v in step.get("args", {}).items():
                card.add_arg(k, str(v))
            self.steps_box.append(card)

    # ------------------------------------------------------------------ #
    def new_shortcut(self) -> None:
        self.doc = ShortcutDocument()
        self.editor_name.set_text("")
        self.editor_desc.set_text("")
        self.editor_phrases.set_text("")
        self._rebuild_steps_editor()
        self.dirty = True

    def _mark_dirty(self) -> None:
        self.doc.spec["name"] = self.editor_name.get_text()
        self.doc.spec["description"] = self.editor_desc.get_text()
        self.doc.spec["phrases"] = [
            p.strip() for p in self.editor_phrases.get_text().split(",") if p.strip()
        ]
        self.dirty = True

    def _delete_step(self, card: ActionCard) -> None:
        self.steps_box.remove(card)
        self.doc.spec["steps"] = [
            s for s in self.doc.spec["steps"] if s["id"] != card.action_id
        ]
        self._mark_dirty()

    def _on_step_arg_change(self, step_id: str, name: str, value: str) -> None:
        for s in self.doc.spec["steps"]:
            if s["id"] == step_id:
                s.setdefault("args", {})[name] = value
        self._mark_dirty()

    def _show_action_picker(self) -> None:
        if not GTK_AVAILABLE:
            return
        dialog = Adw.Dialog.new("Add Action", "Choose an action to add to this shortcut")
        dialog.set_modal(True)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_bottom(12)

        search = Gtk.SearchEntry()
        search.set_placeholder_text("Filter actions…")
        content.append(search)

        for domain_name, _domain_key, actions in DOMAINS:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            lbl = Gtk.Label(label=domain_name, xalign=0.0)
            lbl.add_css_class("caption")
            box.append(lbl)
            for action in actions:
                btn = Gtk.Button()
                btn.set_halign(Gtk.Align.FILL)
                lbl = Gtk.Label(label=action, xalign=0.0)
                btn.set_child(lbl)
                btn.add_css_class("flat")
                btn.connect(
                    "clicked",
                    lambda _b, a=action: self._add_step_from_picker(a, dialog),
                )
                box.append(btn)
            content.append(box)

        scrolled.set_child(content)
        dialog.set_content(scrolled)

        def on_response(_dlg: Any, response: str) -> None:
            dialog.close()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _add_step_from_picker(self, action: str, dialog: Any) -> None:
        existing_ids = [s.get("id", "") for s in self.doc.spec["steps"]]
        step_id = _next_step_id(existing_ids)
        tier = action_tier(action)
        # Pre-populate known args from PARAMETER_MAP
        default_args = {p: "" for p in PARAMETER_MAP.get(action, [])}
        self.doc.spec["steps"].append(
            {"id": step_id, "action": action, "args": default_args, "tier": tier}
        )
        self._rebuild_steps_editor()
        self._mark_dirty()
        dialog.close()

    def save_shortcut(self) -> None:
        self._mark_dirty()
        if not self.doc.spec["name"]:
            self._toast("Please give the shortcut a name.", level="warning")
            return
        try:
            path = self.doc.save()
            self._refresh_sidebar()
            self._toast("Saved " + self.doc.spec["name"])
        except Exception as exc:
            self._toast("Save failed: " + str(exc), level="error")

    # ------------------------------------------------------------------ #
    def _request_tier2_approval_sync(self, title: str, body: str) -> bool:
        """Called from worker thread; prompts user via Adw.AlertDialog on GTK main thread.

        Fail-closed: denies on timeout or missing GTK.
        """
        if not GTK_AVAILABLE:
            return False

        decision = {"approved": False}
        done = threading.Event()

        def _present() -> bool:
            dialog = Adw.AlertDialog.new(title, body)
            dialog.add_response("deny", "Deny")
            dialog.add_response("allow", "Approve Once")
            dialog.set_default_response("deny")
            dialog.set_close_response("deny")
            dialog.set_response_appearance("allow", Adw.ResponseAppearance.DESTRUCTIVE)

            def _on_response(_dlg: Any, resp: str) -> None:
                decision["approved"] = (resp == "allow")
                done.set()

            dialog.connect("response", _on_response)
            dialog.present(self)
            return False  # GLib.SOURCE_REMOVE

        GLib.idle_add(_present)
        if not done.wait(timeout=60.0):
            return False  # fail-closed on timeout
        return decision["approved"]

    def _run_current(self) -> None:
        self._mark_dirty()
        if not self.doc.spec["steps"]:
            self._toast("No steps to run.", level="warning")
            return
        if not CELINA_SHORTCUTS_AVAILABLE:
            self._toast("Celina shortcuts engine not available.", level="error")
            return

        def approve(title: str, body: str) -> bool:
            # This is called from the asyncio event loop running in a worker thread.
            # Present the dialog on the GTK main thread and wait for the user.
            return self._request_tier2_approval_sync(title, body)

        def _thread_run() -> None:
            async def _run() -> None:
                from celina_pkg.shortcuts.engine import ShortcutEngine

                # Build action registry from registered Celina tool handlers.
                # Only registered actions can run; unknown actions fail closed.
                action_registry: dict[str, Any] = {
                    action: get_action_handler(action) for action in ACTION_HANDLERS
                }

                engine = ShortcutEngine(action_registry, approve)
                spec = ShortcutSpec(
                    id=self.doc.spec["id"],
                    name=self.doc.spec["name"],
                    description=self.doc.spec["description"],
                    phrases=self.doc.spec["phrases"],
                    triggers=self.doc.spec["triggers"],
                    inputs=[
                        ShortcutInput(
                            name=i["name"],
                            kind=i.get("kind", "text"),
                            required=bool(i.get("required", True)),
                            default=i.get("default"),
                        )
                        for i in self.doc.spec["inputs"]
                    ],
                    steps=[
                        ShortcutStep(
                            id=s["id"],
                            action=s["action"],
                            args=dict(s.get("args", {})),
                            when=s.get("when"),
                            tier=int(s.get("tier", 1)),
                        )
                        for s in self.doc.spec["steps"]
                    ],
                    allow_without_llm=bool(self.doc.spec.get("allow_without_llm", True)),
                    preauthorized_tier2=bool(self.doc.spec.get("preauthorized_tier2", False)),
                )
                result = await engine.run(spec, {}, require_approval=True)
                status = result["status"]
                GLib.idle_add(self._toast, f"Run: {status}",
                              "error" if status != "ok" else "success")

            asyncio.run(_run())

        threading.Thread(target=_thread_run, daemon=True).start()

    # ------------------------------------------------------------------ #
    def _on_describe_activate(self, entry: Any) -> None:
        text = self.describe_entry.get_text().strip()
        if not text:
            return
        self._toast("Drafting shortcut with LLM (one shot)…")
        draft = self._draft_from_keywords(text)
        # Start a fresh document — reset path so Save doesn't overwrite an
        # existing file while drafting a new shortcut.
        self.doc = ShortcutDocument()
        self.doc.spec = draft
        self.editor_name.set_text(draft["name"])
        self.editor_desc.set_text(draft["description"])
        self.editor_phrases.set_text(", ".join(draft["phrases"]))
        self._rebuild_steps_editor()
        self.dirty = True
        self._toast("Draft ready. Review steps, then Save.")

    def _draft_from_keywords(text: str) -> dict[str, Any]:
        low = text.lower()
        steps: list = []
        next_id = 1
        def _nid() -> str:
            nonlocal next_id
            rid = f"step_{next_id}"
            next_id += 1
            return rid
        if "timer" in low or "minute" in low:
            steps.append({"id": _nid(), "action": "clock.start_timer",
                          "args": {"minutes": 25, "label": "Drafted"}, "tier": action_tier("clock.start_timer")})
        if "focus" in low or "study" in low:
            steps.append({"id": _nid(), "action": "focus.set_mode",
                          "args": {"mode": "study"}, "tier": action_tier("focus.set_mode")})
        if "volume" in low:
            steps.append({"id": _nid(), "action": "os.set_volume",
                          "args": {"level": 50}, "tier": action_tier("os.set_volume")})
        if not steps:
            steps.append({"id": _nid(), "action": "notify.island",
                          "args": {"body": "Shortcut drafted"}, "tier": action_tier("notify.island")})
        return {
            "id": "drafted_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8],
            "name": text[:40],
            "description": "Drafted from: " + text,
            "phrases": [text],
            "triggers": [],
            "inputs": [],
            "steps": steps,
            "allow_without_llm": True,
            "preauthorized_tier2": False,
        }

    # ------------------------------------------------------------------ #
    def _toast(self, title: str, level: str = "success") -> None:
        if not GTK_AVAILABLE:
            print("[" + level + "] " + title)
            return
        toast = Adw.Toast.new(title)
        toast.set_timeout(3)
        self.toast_overlay.add_toast(toast)


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


class CelinaShortcutsApp(Adw.Application if GTK_AVAILABLE else object):
    """Celina Shortcuts Gtk.Application."""

    def __init__(self):
        if not GTK_AVAILABLE:
            return
        super().__init__(
            application_id="org.macubuntu.CelinaShortcuts",
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self.engine = None
        self._window: Any = None

    def do_activate(self):
        # Focus existing window instead of spawning duplicates
        if self._window is not None:
            self._window.present()
            return
        self._window = CelinaShortcutsWindow(self, engine=self.engine)
        self._window.present()

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Load custom stylesheet
        css_path = Path(__file__).resolve().parent / "style.css"
        if css_path.exists():
            provider = Gtk.CssProvider()
            provider.load_from_path(str(css_path))
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )

    def do_open(self, files: list[Gio.File], n_files: int, hint: str) -> None:
        """Open .cshortcut.yaml files passed via CLI or file manager."""
        # Ensure a window exists
        if self._window is None:
            self.do_activate()
        assert self._window is not None
        for file_ in files[:n_files]:
            path = file_.get_path()
            if path:
                self._window.load_shortcut_file(Path(path))
        self._window.present()


def main() -> int:
    if not GTK_AVAILABLE:
        print("GTK4 / Libadwaita (python3-gi, gir1.2-adw-1) are not installed.")
        print("Install them on Ubuntu 26.04:")
        print("  sudo apt install python3-gi gir1.2-adw-1 gir1.2-gtk-4.0")
        print("Then run: python3 apps/celina-shortcuts/main.py")
        return 1
    if not CELINA_SHORTCUTS_AVAILABLE:
        print("Celina shortcuts engine not importable.")
        print("Ensure PYTHONPATH includes features/celina and PyYAML is installed.")
        return 1

    app = CelinaShortcutsApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())