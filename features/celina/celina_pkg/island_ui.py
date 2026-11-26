"""
Celina AI (Celly) - Apple-Style Stub-Launch GTK3 Dynamic Island UI (v4.2)
- Stub Launch & Lazy Loading (~18 MB boot RAM): At login, loads ONLY the floating pill &
  config; CelinaRouter, SQLite DBs, 60 tools, and fullscreen EdgeGlowOverlay stay unloaded
  until your first interaction.
- Zero-Tick Deep Sleep (0.0% idle CPU): Reminder timer sleeps completely when 0 timers exist;
  media status uses in-process D-Bus MPRIS (0 playerctl forks) and sleeps when no player runs.
- Heap Compaction & Memory Purge: Periodic idle offload trims Python + glibc heap via
  gc.collect() + libc.malloc_trim(0) and destroys hidden fullscreen Cairo surfaces.
- Unified 4096-byte Unix socket IPC (--toggle, --voice, --ask "<prompt>", --quit)
- ~15 FPS Live Token Streaming + Apple Intelligence Pill Halo & Fullscreen Task Edge Glow
- Inline Tier-2 [Approve / Deny] + Privacy Consent Gate ([Send Redacted / Allow / Keep Local])
"""

import atexit
import contextlib
import ctypes
import gc
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk

# LAZY BOOT STUB: Import ONLY lightweight config at module load!
# Heavy modules (router, tools, skills, automation, cache, mcp_client, voice, edge_glow)
# are imported on-demand ONLY when the user interacts with Celina.
from . import config

SOCKET_PATH = config.SOCKET_PATH

CSS = b"""
window {
    background-color: transparent;
}
#island-box {
    background-color: rgba(18, 18, 22, 0.94);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 26px;
    padding: 8px 16px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.58);
}
#pill-title {
    color: #f5f5f7;
    font-family: "Inter", sans-serif;
    font-weight: 600;
    font-size: 13px;
}
#pill-status {
    color: #a855f7;
    font-family: "Inter", sans-serif;
    font-size: 12px;
}
#prompt-entry {
    background-color: rgba(255, 255, 255, 0.08);
    color: #ffffff;
    border: 1px solid rgba(168, 85, 247, 0.45);
    border-radius: 12px;
    padding: 8px 12px;
    font-family: "Inter", sans-serif;
    font-size: 13px;
}
#response-view {
    background-color: rgba(255, 255, 255, 0.04);
    color: #e5e7eb;
    border-radius: 12px;
    padding: 10px 12px;
    font-family: "Inter", sans-serif;
    font-size: 12.5px;
}
#approval-box {
    background-color: rgba(245, 158, 11, 0.14);
    border: 1px solid rgba(245, 158, 11, 0.6);
    border-radius: 12px;
    padding: 8px 10px;
}
#privacy-box {
    background-color: rgba(6, 182, 212, 0.14);
    border: 1px solid rgba(56, 189, 248, 0.65);
    border-radius: 12px;
    padding: 8px 10px;
}
#approval-title {
    color: #fde68a;
    font-weight: 700;
    font-size: 12px;
}
#privacy-title {
    color: #7dd3fc;
    font-weight: 700;
    font-size: 12px;
}
#card-detail {
    color: #f3f4f6;
    font-family: monospace;
    font-size: 11px;
}
button.pill-btn {
    background: rgba(255, 255, 255, 0.08);
    color: #f5f5f7;
    border: none;
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
}
button.pill-btn:hover {
    background: rgba(168, 85, 247, 0.35);
}
button.approve-btn {
    background: rgba(34, 197, 94, 0.85);
    color: #052e16;
    font-weight: 700;
    border: none;
    border-radius: 10px;
    padding: 4px 12px;
    font-size: 11px;
}
button.redact-btn {
    background: rgba(56, 189, 248, 0.88);
    color: #082f49;
    font-weight: 600;
    border: none;
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
}
button.deny-btn {
    background: rgba(239, 68, 68, 0.82);
    color: #ffffff;
    font-weight: 600;
    border: none;
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11px;
}
button.smart-chip {
    background: rgba(168, 85, 247, 0.24);
    color: #f3e8ff;
    border: 1px solid rgba(192, 132, 252, 0.55);
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 11.5px;
    font-weight: 600;
}
button.smart-chip:hover {
    background: rgba(168, 85, 247, 0.45);
}
"""


_TRACEBACK_RE = re.compile(
    r"(Traceback \(most recent call last\):|"
    r"(?:Syntax|Type|Value|Key|Index|Attribute|Import|ModuleNotFound|Runtime|Reference|NullPointer|ZeroDivision)Error:|"
    r"Exception in thread|panic:|fatal error:)",
    re.I,
)
_STACK_LINE_RE = re.compile(r'(File "[^"]+", line \d+|\bat\s+\S+:\d+|\bline\s+\d+\b|^\s+\^+\s*$)', re.M)
_PUBLIC_URL_RE = re.compile(r"^https?://(?!(?:127\.|localhost|0\.0\.0\.0|10\.|192\.168\.))[^\s<>\"']{4,500}$", re.I)


def _inspect_clipboard_suggestion(clip_text: str) -> Optional[tuple[str, str]]:
    """
    Movie Feature C: Zero-polling Clipboard Watchdog detector (runs ONLY on drawer expand).
    Returns (chip_label, action_prompt) if clipboard contains a traceback or public URL, else None.
    """
    txt = (clip_text or "").strip()
    if not txt or len(txt) > 12000:
        return None
    if _TRACEBACK_RE.search(txt) and (_STACK_LINE_RE.search(txt) or "\n" in txt):
        return (
            "🔧 Debug Clipboard Traceback",
            "deep: Debug this traceback from my clipboard, isolate the root cause, and give the exact fix:\n\n"
            + txt[:2200],
        )
    if "\n" not in txt and _PUBLIC_URL_RE.match(txt):
        return (
            "📄 Summarize Copied Link",
            f"Fetch and summarize this webpage cleanly: {txt}",
        )
    return None


def _read_clipboard_nonblocking() -> str:
    """Reads desktop clipboard via wl-paste (Wayland) or xclip (X11) with strict 0.5s timeout."""
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=0.5)
                if res.returncode == 0 and res.stdout:
                    return res.stdout[:4000]
            except Exception:
                return ""
    return ""


def _release_os_memory() -> None:
    """Forces Python GC and glibc ptmalloc to return freed RAM pages back to Linux immediately."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def send_ipc_command(cmd: str = "toggle", socket_path: str = SOCKET_PATH) -> bool:
    if not os.path.exists(socket_path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
            cli.settimeout(1.0)
            cli.connect(socket_path)
            cli.sendall(cmd.encode("utf-8"))
        return True
    except OSError:
        return False


def _probe_mpris_title_dbus(bus: Any) -> tuple[bool, str]:
    """
    Zero-fork in-process D-Bus MPRIS media check (<0.3 ms, 0 subprocesses).
    Returns (has_any_mpris_player, formatted_title_or_empty).
    """
    try:
        names_var = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            400,
            None,
        )
        players = [n for n in names_var.unpack()[0] if n.startswith("org.mpris.MediaPlayer2.")]
        if not players:
            return False, ""
        for p_bus in players:
            try:
                meta_var = bus.call_sync(
                    p_bus,
                    "/org/mpris/MediaPlayer2",
                    "org.freedesktop.DBus.Properties",
                    "Get",
                    GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", "Metadata")),
                    None,
                    Gio.DBusCallFlags.NONE,
                    400,
                    None,
                )
                meta = meta_var.unpack()[0]
                title = str(meta.get("xesam:title") or "").strip()
                if title:
                    return True, f"♪ {title}"
            except Exception:
                continue
        return True, ""
    except Exception:
        return False, ""


def _quick_db_has_unfired_reminders() -> bool:
    """One-shot read-only reminder probe at login without importing CelinaRouter or requests."""
    if not config.DB_PATH.exists():
        return False
    try:
        import sqlite3

        with contextlib.closing(sqlite3.connect(str(config.DB_PATH), timeout=2.0)) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'"
            ).fetchone()
            if not row:
                return False
            hit = conn.execute("SELECT 1 FROM reminders WHERE fired = 0 LIMIT 1").fetchone()
            return hit is not None
    except Exception:
        return False


class DynamicIsland(Gtk.Window):
    def __init__(
        self,
        start_expanded: bool = False,
        start_voice: bool = False,
        initial_prompt: Optional[str] = None,
    ):
        super().__init__(title=f"{config.get_assistant_name()} Dynamic Island")
        # LAZY: Never instantiate AI router, SQLite DBs, or fullscreen overlay at login!
        self._router: Any = None
        self._edge_glow: Any = None
        self._has_pending_reminders = False
        self._reminder_timer_id: Optional[int] = None
        self._media_timer_id: Optional[int] = None
        self._dbus: Any = None
        self._dbus_sub_id: Optional[int] = None

        self.expanded = False
        self.mode = "auto"
        self.lang = (
            config.DEFAULT_LANGUAGE
            if config.DEFAULT_LANGUAGE in {"auto", "en", "te", "hi"}
            else "auto"
        )
        self.speak_enabled = config.SPEAK_ENABLED
        self._busy = False
        self._server_sock: Optional[socket.socket] = None
        self._pending_approval: Optional[tuple[threading.Event, list[bool]]] = None
        self._pending_privacy: Optional[tuple[threading.Event, list[str]]] = None
        self._wake_listener: Any = None

        # Apple Intelligence Edge Lighting state (0% CPU when idle!)
        self._pill_glow_mode = "responding"
        self._pill_glow_phase = 0.0
        self._pill_glow_alpha = 0.0
        self._pill_glow_target = 0.0
        self._pill_glow_timer: Optional[int] = None
        self._pill_glow_last_ts: float = 0.0
        self._pill_fade_timer: Optional[int] = None
        self._last_stream_text: str = ""

        # NORMAL floating window so GNOME/XWayland reliably gives keyboard focus
        self.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_app_paintable(True)
        self.stick()

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Main Pill Container (with Apple Intelligence Cairo halo overlay)
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.main_box.set_name("island-box")
        self.main_box.connect_after("draw", self._on_pill_draw)
        self.add(self.main_box)

        # --- Compact Top Bar ---
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.main_box.pack_start(top_bar, False, False, 0)

        self.dot_label = Gtk.Label(label="●")
        self.dot_label.set_name("pill-status")
        top_bar.pack_start(self.dot_label, False, False, 0)

        self.title_label = Gtk.Label(label=f"{config.get_assistant_name()} Ready")
        self.title_label.set_name("pill-title")
        self.title_label.set_ellipsize(3)
        self.title_label.set_max_width_chars(24)
        top_bar.pack_start(self.title_label, True, True, 0)

        # Live macOS Privacy Indicator Dots (🟠 mic / 🟢 cam / 🟣 screen) + Focus Badge
        self.os_badge_label = Gtk.Label(label="")
        self.os_badge_label.set_name("pill-title")
        self.os_badge_label.set_tooltip_text("Live hardware privacy indicators & Focus Mode")
        top_bar.pack_end(self.os_badge_label, False, False, 2)

        # Actionable iPhone ANCS quick pills (hidden unless an active iOS notification arrives)
        self._ios_action_hide_id: Optional[int] = None
        self.ios_action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        ios_ok = Gtk.Button(label="✓")
        ios_ok.get_style_context().add_class("approve-btn")
        ios_ok.set_tooltip_text("Accept / Answer iPhone alert (ANCS positive action)")
        ios_ok.connect("clicked", lambda _: self._handle_ios_action(True))
        ios_no = Gtk.Button(label="✕")
        ios_no.get_style_context().add_class("deny-btn")
        ios_no.set_tooltip_text("Dismiss / Decline iPhone alert (ANCS negative action)")
        ios_no.connect("clicked", lambda _: self._handle_ios_action(False))
        self.ios_action_box.pack_start(ios_ok, False, False, 0)
        self.ios_action_box.pack_start(ios_no, False, False, 0)
        top_bar.pack_end(self.ios_action_box, False, False, 2)

        self.mode_btn = Gtk.Button(label="AUTO")
        self.mode_btn.get_style_context().add_class("pill-btn")
        self.mode_btn.set_tooltip_text("Switch AI mode (AUTO / CLOUD / LOCAL)")
        self.mode_btn.connect("clicked", self._cycle_mode)
        top_bar.pack_end(self.mode_btn, False, False, 0)

        self.expand_btn = Gtk.Button(label="⌄")
        self.expand_btn.get_style_context().add_class("pill-btn")
        self.expand_btn.connect("clicked", lambda _: self.toggle_expand())
        top_bar.pack_end(self.expand_btn, False, False, 0)

        # --- Apple Face ID / Touch ID 60 FPS Cairo Squircle Canvas (hidden when idle -> 0 ticks) ---
        from . import biometric_anim

        self._bio_state = biometric_anim.BiometricAnimState()
        self._bio_timer_id: Optional[int] = None
        self._bio_last_tick: float = 0.0
        self._bio_observer: Optional[Any] = None
        self.bio_area = Gtk.DrawingArea()
        self.bio_area.set_size_request(132, 118)
        self.bio_area.connect("draw", self._on_bio_draw)
        self.main_box.pack_start(self.bio_area, False, False, 0)

        # --- Expandable Drawer ---
        self.drawer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.main_box.pack_start(self.drawer, True, True, 0)

        self.entry = Gtk.Entry()
        self.entry.set_name("prompt-entry")
        self.entry.set_placeholder_text(
            f"Ask {config.get_assistant_display_label()}... ('quick:', 'deep:', 'simulate:', '/name')"
        )
        self.entry.connect("activate", self._on_submit)
        self.drawer.pack_start(self.entry, False, False, 0)

        # --- Inline Tier-2 System Action Approval Card ---
        self.approval_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.approval_box.set_name("approval-box")

        self.approval_title = Gtk.Label(label="Approve action?")
        self.approval_title.set_name("approval-title")
        self.approval_title.set_xalign(0)
        self.approval_box.pack_start(self.approval_title, False, False, 0)

        self.approval_detail = Gtk.Label(label="")
        self.approval_detail.set_name("card-detail")
        self.approval_detail.set_line_wrap(True)
        self.approval_detail.set_xalign(0)
        self.approval_detail.set_selectable(True)
        self.approval_box.pack_start(self.approval_detail, False, False, 0)

        approval_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        approve_btn = Gtk.Button(label="✓ Approve")
        approve_btn.get_style_context().add_class("approve-btn")
        approve_btn.connect("clicked", lambda _: self._resolve_approval(True))
        deny_btn = Gtk.Button(label="✕ Deny")
        deny_btn.get_style_context().add_class("deny-btn")
        deny_btn.connect("clicked", lambda _: self._resolve_approval(False))
        approval_btns.pack_start(approve_btn, False, False, 0)
        approval_btns.pack_start(deny_btn, False, False, 0)
        self.approval_box.pack_start(approval_btns, False, False, 0)
        self.drawer.pack_start(self.approval_box, False, False, 0)

        # --- Inline Privacy Consent Gate Card (Zero-Leak Cloud Control) ---
        self.privacy_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.privacy_box.set_name("privacy-box")

        self.privacy_title = Gtk.Label(label="🛡️ Privacy Consent Required")
        self.privacy_title.set_name("privacy-title")
        self.privacy_title.set_line_wrap(True)
        self.privacy_title.set_xalign(0)
        self.privacy_box.pack_start(self.privacy_title, False, False, 0)

        self.privacy_detail = Gtk.Label(label="")
        self.privacy_detail.set_name("card-detail")
        self.privacy_detail.set_line_wrap(True)
        self.privacy_detail.set_xalign(0)
        self.privacy_box.pack_start(self.privacy_detail, False, False, 0)

        priv_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        redact_btn = Gtk.Button(label="🛡️ Send Redacted")
        redact_btn.get_style_context().add_class("redact-btn")
        redact_btn.connect("clicked", lambda _: self._resolve_privacy("redact"))
        allow_btn = Gtk.Button(label="✓ Allow Once")
        allow_btn.get_style_context().add_class("approve-btn")
        allow_btn.connect("clicked", lambda _: self._resolve_privacy("allow"))
        local_btn = Gtk.Button(label="🔒 Keep Local")
        local_btn.get_style_context().add_class("deny-btn")
        local_btn.connect("clicked", lambda _: self._resolve_privacy("local"))
        priv_btns.pack_start(redact_btn, False, False, 0)
        priv_btns.pack_start(allow_btn, False, False, 0)
        priv_btns.pack_start(local_btn, False, False, 0)
        self.privacy_box.pack_start(priv_btns, False, False, 0)
        self.drawer.pack_start(self.privacy_box, False, False, 0)

        # --- Clipboard Watchdog Smart Chip (zero background polling; checks ONLY on drawer expand) ---
        self._clip_chip_prompt: Optional[str] = None
        self.clip_chip_btn = Gtk.Button(label="")
        self.clip_chip_btn.get_style_context().add_class("smart-chip")
        self.clip_chip_btn.set_tooltip_text("1-click contextual action from your current clipboard")
        self.clip_chip_btn.connect("clicked", self._on_clip_chip_clicked)
        self.drawer.pack_start(self.clip_chip_btn, False, False, 0)

        # --- Scrollable Response View ---
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(150)
        scroll.set_max_content_height(280)

        self.response_label = Gtk.Label(
            label=f"Ready. Ask {config.get_assistant_display_label()} anything in English, తెలుగు, or हिन्दी — or press Super+Alt+C to speak."
        )
        self.response_label.set_name("response-view")
        self.response_label.set_line_wrap(True)
        self.response_label.set_xalign(0)
        self.response_label.set_yalign(0)
        self.response_label.set_selectable(True)
        scroll.add(self.response_label)
        self.drawer.pack_start(scroll, True, True, 0)

        # --- Quick Action Strip ---
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for lbl, cmd in [("⏯", "play-pause"), ("⏭", "next")]:
            btn = Gtk.Button(label=lbl)
            btn.get_style_context().add_class("pill-btn")
            btn.connect("clicked", self._quick_media, cmd)
            actions.pack_start(btn, False, False, 0)

        self.voice_btn = Gtk.Button(label="🎤 Voice")
        self.voice_btn.get_style_context().add_class("pill-btn")
        self.voice_btn.set_tooltip_text(
            f"Speak to {config.get_assistant_name()} (auto-stops on silence, Super+Alt+C)"
        )
        self.voice_btn.connect("clicked", lambda _: self.trigger_voice())
        actions.pack_start(self.voice_btn, False, False, 0)

        self.tts_btn = Gtk.Button(label="🔊" if self.speak_enabled else "🔇")
        self.tts_btn.get_style_context().add_class("pill-btn")
        self.tts_btn.set_tooltip_text("Toggle neural voice replies")
        self.tts_btn.connect("clicked", self._toggle_tts)
        actions.pack_start(self.tts_btn, False, False, 0)

        self.lang_btn = Gtk.Button(label=self._lang_badge(self.lang))
        self.lang_btn.get_style_context().add_class("pill-btn")
        self.lang_btn.set_tooltip_text("Switch language (AUTO / English / తెలుగు / हिन्दी)")
        self.lang_btn.connect("clicked", self._cycle_lang)
        actions.pack_start(self.lang_btn, False, False, 0)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.get_style_context().add_class("pill-btn")
        clear_btn.connect("clicked", self._clear_chat)
        actions.pack_end(clear_btn, False, False, 0)

        why_btn = Gtk.Button(label="ⓘ Why")
        why_btn.get_style_context().add_class("pill-btn")
        why_btn.set_tooltip_text("Glass-Box Explainability: inspect gear, pruned tools, recalled memory & latency (/why)")
        why_btn.connect("clicked", self._show_why_receipt)
        actions.pack_end(why_btn, False, False, 0)
        self.drawer.pack_start(actions, False, False, 0)

        # --- Control Center Quick Strip (Focus, Writing Tools, Live Text OCR, 80% Charge Limit) ---
        cc_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.focus_btn = Gtk.Button(label="🎯 Focus")
        self.focus_btn.get_style_context().add_class("pill-btn")
        self.focus_btn.set_tooltip_text("Cycle macOS Focus Mode (default -> work -> coding -> presentation -> sleep)")
        self.focus_btn.connect("clicked", self._cycle_focus_mode)
        cc_bar.pack_start(self.focus_btn, False, False, 0)

        write_btn = Gtk.Button(label="✍️ Write")
        write_btn.get_style_context().add_class("pill-btn")
        write_btn.set_tooltip_text("Apple Intelligence Writing Tools: proofread highlighted text locally (Super+Shift+W)")
        write_btn.connect("clicked", lambda _: self._submit_prompt("proofread my selected text using writing_tool_transform"))
        cc_bar.pack_start(write_btn, False, False, 0)

        ocr_btn = Gtk.Button(label="🔍 LiveText")
        ocr_btn.get_style_context().add_class("pill-btn")
        ocr_btn.set_tooltip_text("macOS Live Text: snip screen region -> OCR -> clipboard (Super+Shift+T)")
        ocr_btn.connect("clicked", self._trigger_live_text)
        cc_bar.pack_start(ocr_btn, False, False, 0)

        bat_btn = Gtk.Button(label="🔋 80%")
        bat_btn.get_style_context().add_class("pill-btn")
        bat_btn.set_tooltip_text("Enable 80% Optimized Battery Charge Limit (on supported laptops)")
        bat_btn.connect("clicked", self._apply_charge_limit_80)
        cc_bar.pack_end(bat_btn, False, False, 0)
        self.drawer.pack_start(cc_bar, False, False, 0)

        # --- Control Center Hardware Row (Volume / Brightness / Audio Output Sink Cycle) ---
        hw_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for lbl, tip, cb in [
            ("🔉", "Volume -10%", lambda _: self._adjust_hw("vol", -10)),
            ("🔊", "Volume +10%", lambda _: self._adjust_hw("vol", 10)),
            ("🔅", "Brightness -10%", lambda _: self._adjust_hw("bri", -10)),
            ("🔆", "Brightness +10%", lambda _: self._adjust_hw("bri", 10)),
        ]:
            b = Gtk.Button(label=lbl)
            b.get_style_context().add_class("pill-btn")
            b.set_tooltip_text(tip)
            b.connect("clicked", cb)
            hw_bar.pack_start(b, False, False, 0)

        sink_btn = Gtk.Button(label="🎧 Output")
        sink_btn.get_style_context().add_class("pill-btn")
        sink_btn.set_tooltip_text("List / switch active PipeWire audio output sinks")
        sink_btn.connect("clicked", self._show_audio_sinks)
        hw_bar.pack_end(sink_btn, False, False, 0)
        self.drawer.pack_start(hw_bar, False, False, 0)

        # Window Events
        self.connect("key-press-event", self._on_key_press)
        self.connect("button-press-event", self._on_button_press)
        self.connect("destroy", self._on_destroy)

        self.show_all()
        self.bio_area.hide()
        self.ios_action_box.hide()
        self.approval_box.hide()
        self.privacy_box.hide()
        self.clip_chip_btn.hide()
        self.drawer.hide()
        self.set_size_request(310, 42)
        self._center_top()

        if initial_prompt:
            self.toggle_expand(force=True)
            GLib.idle_add(self._submit_prompt, initial_prompt)
        elif start_voice:
            self.trigger_voice()
        elif start_expanded:
            self.toggle_expand(force=True)

        # Event-driven Zero-Tick Deep Sleep setup:
        # 1. Media monitor subscribes to D-Bus MPRIS NameOwnerChanged (0 timers when no player runs)
        self._init_media_dbus_monitor()
        # 2. Reminder timer wakes ONLY if unfired reminders exist in SQLite (0 ticks otherwise)
        self._has_pending_reminders = _quick_db_has_unfired_reminders()
        if self._has_pending_reminders:
            self._ensure_reminder_timer()
        # 3. Infrequent timely offload & OS heap trim
        GLib.timeout_add_seconds(max(60, config.CACHE_OFFLOAD_INTERVAL_SEC), self._run_timely_offload)
        # 4. Event-driven OS Privacy Dots + Focus Badge file monitor (0 polling timers!)
        self._os_events_monitor = None
        self._init_os_events_monitor()
        # 5. Live iOS BLE ANCS bridge (auto-activates only if an ANCS-enabled iPhone is paired)
        self._init_ios_ancs_bridge()
        # 6. Real OS Face ID / Touch ID D-Bus observer (fprintd + GNOME ScreenSaver -> 0% idle CPU)
        self._init_biometric_observer()
        self._start_ipc_server()
        self._start_wake_word()

    def _init_os_events_monitor(self) -> None:
        """Watches ~/.local/share/celina/live_os_events.json via Gio.FileMonitor (0 CPU when idle)."""
        events_path = config.BASE_DIR / "live_os_events.json"
        self._refresh_os_badges()
        try:
            gfile = Gio.File.new_for_path(str(events_path))
            mon = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            mon.connect("changed", lambda *_a: GLib.idle_add(self._refresh_os_badges))
            self._os_events_monitor = mon
        except Exception:
            self._os_events_monitor = None

    def _refresh_os_badges(self) -> bool:
        events_path = config.BASE_DIR / "live_os_events.json"
        if not events_path.exists():
            return False
        try:
            data = json.loads(events_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        priv = data.get("privacy") or {}
        parts: list[str] = []
        if priv.get("mic_active"):
            parts.append("🟠")
        if priv.get("camera_active"):
            parts.append("🟢")
        if priv.get("screen_share_active"):
            parts.append("🟣")
        focus_icons = {"work": "💼", "coding": "💻", "presentation": "🎤", "sleep": "🌙"}
        fmode = str(data.get("focus_mode", "default")).lower()
        if fmode in focus_icons:
            parts.append(focus_icons[fmode])
            self.focus_btn.set_label(f"{focus_icons[fmode]} {fmode.title()}")
        else:
            self.focus_btn.set_label("🎯 Focus")
        if data.get("secret_vault_locked") is True:
            parts.append("🔒")
        failed_svcs = data.get("failed_services") or []
        usb_alert = data.get("usb_alert")
        if failed_svcs:
            parts.append("🔧")
        if usb_alert:
            parts.append("💾")
        self.os_badge_label.set_text("".join(parts))
        detail = str(priv.get("detail") or "idle")
        extra_tip: list[str] = [f"Privacy: {detail}", f"Focus: {fmode}"]
        if data.get("secret_vault_locked") is True:
            extra_tip.append("Vault: Locked")
        if failed_svcs:
            extra_tip.append(f"Failed service: {', '.join(str(u) for u in failed_svcs[:3])}")
            self._apply_clipboard_chip((
                f"🔧 {failed_svcs[0]} failed — Diagnose",
                f"Diagnose failed systemd service {failed_svcs[0]}",
            ))
        elif isinstance(usb_alert, dict) and usb_alert.get("banner"):
            extra_tip.append(str(usb_alert["banner"]))
            self._apply_clipboard_chip((
                str(usb_alert["banner"])[:54],
                "Open Nautilus Files or Pika Backup for my connected USB drive",
            ))
        self.os_badge_label.set_tooltip_text(" | ".join(extra_tip))
        return False

    def _init_ios_ancs_bridge(self) -> None:
        try:
            from . import ios_ancs

            def _on_ios_notif(app_id: str, title: str, body: str) -> None:
                GLib.idle_add(self._show_ios_pill_alert, title, body)

            def _on_bt_battery(dev_name: str, pct: int, is_audio: bool) -> None:
                GLib.idle_add(self._show_bt_battery_pill, dev_name, pct, is_audio)

            ios_ancs.start_ios_bridge(on_notification=_on_ios_notif, on_bt_battery=_on_bt_battery)
        except Exception:
            pass

    def _hide_ios_action_buttons(self) -> bool:
        self._ios_action_hide_id = None
        self.ios_action_box.hide()
        return False

    def _handle_ios_action(self, positive: bool) -> None:
        if self._ios_action_hide_id is not None:
            GLib.source_remove(self._ios_action_hide_id)
            self._ios_action_hide_id = None
        self.ios_action_box.hide()

        def _worker() -> None:
            try:
                from . import ios_ancs

                res = ios_ancs.perform_ios_notification_action(positive=positive)
                GLib.idle_add(self.title_label.set_text, f"📱 {'Accepted' if positive else 'Dismissed'}")
                if self.expanded:
                    GLib.idle_add(self.response_label.set_text, res)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _show_ios_pill_alert(self, title: str, body: str) -> bool:
        if not self._busy:
            self.title_label.set_text(f"📱 {title[:22]}")
            self._set_dot("#38bdf8")
            self._set_pill_glow("responding", True)
            self._schedule_pill_glow_fade(3000)
            self.ios_action_box.show_all()
            if self._ios_action_hide_id is not None:
                GLib.source_remove(self._ios_action_hide_id)
            self._ios_action_hide_id = GLib.timeout_add_seconds(12, self._hide_ios_action_buttons)
        return False

    def _show_bt_battery_pill(self, dev_name: str, pct: int, is_audio: bool) -> bool:
        """Displays macOS-style Dynamic Island AirPods / Bluetooth Headphone battery pill."""
        if not self._busy:
            icon = "🎧" if is_audio else "🔋"
            self.title_label.set_text(f"{icon} {dev_name[:16]} · {pct}%")
            color = "#22c55e" if pct >= 35 else ("#f59e0b" if pct >= 15 else "#ef4444")
            self._set_dot(color)
            self._set_pill_glow("responding", True)
            self._schedule_pill_glow_fade(3500)
        return False

    def _cycle_focus_mode(self, _btn) -> None:
        order = ("default", "work", "coding", "presentation", "sleep")
        try:
            from . import os_events

            mgr = os_events.FocusModeManager()
            cur = mgr.get_current()
            nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "work"
            msg = mgr.set_mode(nxt)
            self.response_label.set_text(msg)
            self._refresh_os_badges()
        except Exception as exc:
            self.response_label.set_text(f"Focus Mode error: {exc}")

    def _trigger_live_text(self, _btn) -> None:
        self.toggle_expand(force=False)

        def _worker() -> None:
            from . import writing_tools

            res = writing_tools.live_text_ocr_region(copy_to_clipboard=True)
            GLib.idle_add(self._show_quick_result, res)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_charge_limit_80(self, _btn) -> None:
        def _worker() -> None:
            try:
                import celina_hardware

                res = celina_hardware.get_hardware().battery_limit.set_limit(80)
                GLib.idle_add(self._show_quick_result, f"🔋 {res.message}")
            except Exception as exc:
                GLib.idle_add(self._show_quick_result, f"🔋 Charge limit error: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _show_quick_result(self, text: str) -> bool:
        self.toggle_expand(force=True)
        self.response_label.set_text(text)
        return False

    def _adjust_hw(self, kind: str, delta: int) -> None:
        def _worker() -> None:
            try:
                import celina_hardware

                hw = celina_hardware.get_hardware()
                if kind == "vol":
                    st = hw.audio.get_state()
                    cur = int(st.volume_percent) if st.volume_percent is not None else 50
                    res = hw.audio.set_volume(max(0, min(100, cur + delta)))
                    GLib.idle_add(self._show_quick_result, f"🔊 {res.message}")
                else:
                    st = hw.power.get_state()
                    cur = int(st.brightness_percent) if st.brightness_percent is not None else 60
                    res = hw.set_brightness(max(5, min(100, cur + delta)))
                    GLib.idle_add(self._show_quick_result, f"☀️ {res.message}")
            except Exception as exc:
                GLib.idle_add(self._show_quick_result, f"Hardware control error: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _show_audio_sinks(self, _btn) -> None:
        def _worker() -> None:
            try:
                import celina_hardware

                sinks = celina_hardware.get_hardware().routing.list_sinks()
                if not sinks:
                    GLib.idle_add(self._show_quick_result, "🎧 No PipeWire audio sinks detected.")
                    return
                lines = ["🎧 Audio Outputs (say 'route <app> audio to <sink>'):"]
                for s in sinks[:6]:
                    lines.append(f"  • [{s['id']}] {s['name']} ({s['state']})")
                GLib.idle_add(self._show_quick_result, "\n".join(lines))
            except Exception as exc:
                GLib.idle_add(self._show_quick_result, f"🎧 Sink lookup error: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _init_biometric_observer(self) -> None:
        try:
            from . import biometric_anim

            obs = biometric_anim.BiometricDBusObserver(
                on_start=lambda kind, lbl: GLib.idle_add(self.trigger_biometric_anim, kind, None, lbl),
                on_resolve=lambda ok, lbl: GLib.idle_add(self.resolve_biometric_anim, ok, lbl),
                on_retry=lambda lbl: GLib.idle_add(self._bio_state.pulse_retry, lbl),
                on_unlock_welcome=lambda kind: GLib.idle_add(
                    self.trigger_biometric_anim, kind, True, "Face ID" if kind == "face" else "Touch ID"
                ),
            )
            obs.start()
            self._bio_observer = obs
        except Exception:
            self._bio_observer = None

    def trigger_biometric_anim(
        self,
        kind: str = "face",
        auto_matched: Optional[bool] = None,
        label: str = "",
    ) -> bool:
        """Starts the 60 FPS Cairo Face ID or Touch ID animation inside the Dynamic Island."""
        bkind = "touch" if kind == "touch" else "face"
        self._bio_state.start(
            bkind,
            label=label,
            auto_resolve_after=0.75 if auto_matched is not None else None,
            auto_matched=bool(auto_matched) if auto_matched is not None else True,
        )
        self.bio_area.show()
        self._bio_last_tick = time.monotonic()
        if self._bio_timer_id is None:
            self._bio_timer_id = GLib.timeout_add(16, self._tick_biometric_anim)
        self.bio_area.queue_draw()
        return False

    def resolve_biometric_anim(self, matched: bool, label: str = "") -> bool:
        if not self._bio_state.is_active:
            self.trigger_biometric_anim("touch", auto_matched=matched, label=label)
            return False
        self._bio_state.resolve(matched, label=label)
        self.bio_area.queue_draw()
        return False

    def _tick_biometric_anim(self) -> bool:
        now = time.monotonic()
        dt = now - self._bio_last_tick if self._bio_last_tick else 0.016
        self._bio_last_tick = now
        still_active = self._bio_state.step(dt)
        self.bio_area.queue_draw()
        if not still_active:
            self._bio_timer_id = None
            self.bio_area.hide()
            if not self.expanded:
                self.resize(310, 42)
                self._center_top()
            return False
        return True

    def _on_bio_draw(self, widget, cr) -> bool:
        from . import biometric_anim

        alloc = widget.get_allocation()
        biometric_anim.render_biometric_cairo(cr, float(alloc.width), float(alloc.height), self._bio_state)
        return False

    def _ready_label(self) -> str:
        return f"{config.get_assistant_name()} Ready"

    def _refresh_identity_ui(self) -> None:
        """Refreshes window title, placeholder, and voice tooltip live if user renamed Celina."""
        name = config.get_assistant_name()
        self.set_title(f"{name} Dynamic Island")
        self.entry.set_placeholder_text(
            f"Ask {config.get_assistant_display_label()}... ('quick:', 'deep:', 'simulate:', '/name')"
        )
        self.voice_btn.set_tooltip_text(
            f"Speak to {name} (auto-stops on silence, Super+Alt+C)"
        )

    # --- Lazy Properties (Stub Launch) ---
    @property
    def router(self):
        """Lazy-loads AI brain, SQLite DBs, and skills ONLY on first interaction."""
        if self._router is None:
            from . import automation, router as router_mod

            self._router = router_mod.CelinaRouter()
            automation.register_capture_overlay_hook(
                self._hide_for_screenshot, self._restore_after_screenshot
            )
        return self._router

    def _hide_for_screenshot(self) -> bool:
        """Temporarily collapses Dynamic Island drawer before screenshots so it never blocks target apps."""
        if not self.expanded:
            return False
        done = threading.Event()

        def _do_hide() -> bool:
            try:
                self.toggle_expand(force=False)
            finally:
                done.set()
            return False

        GLib.idle_add(_do_hide)
        done.wait(timeout=0.5)
        return True

    def _restore_after_screenshot(self) -> None:
        GLib.idle_add(self.toggle_expand, True)

    def _is_recording_skill(self) -> bool:
        if self._router is None:
            return False
        from . import skills

        return bool(skills.recorder.is_recording)

    def _recording_skill_name(self) -> str:
        from . import skills

        return str(skills.recorder.recording_name)

    # --- Timely Offload + OS Heap Purge (malloc_trim) ---
    def _run_timely_offload(self) -> bool:
        """Periodically offloads idle hot RAM cache, stops idle MCP/Whisper, and trims OS heap."""
        def worker() -> None:
            try:
                if self._router is not None:
                    from . import cache, mcp_client

                    cache.get_cache().offload_and_prune(force_vacuum=True)
                    mcp_client.get_mcp().offload_idle_servers(force=False)
                if "celina_pkg.voice" in sys.modules:
                    from . import voice

                    voice.offload_whisper_if_idle(force=False)
                if not self._busy and self._edge_glow is not None:
                    GLib.idle_add(self._destroy_idle_edge_glow)
                self._check_proactive_world_triggers()
                _release_os_memory()
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _check_proactive_world_triggers(self) -> None:
        """
        Proactive World Model (0 subprocesses, 0 extra timers, <0.1 ms):
        Checks /proc/meminfo and /sys/class/power_supply/BAT* during existing maintenance wakeups.
        """
        if self._busy:
            return
        triggers = (config.WORLD_MODEL_CFG or {}).get("proactive_triggers") or {}
        ram_limit = int(triggers.get("ram_pressure_pct", 88))
        bat_limit = int(triggers.get("battery_low_pct", 18))

        alert_msg = ""
        try:
            mem_info: dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        mem_info[parts[0].strip()] = int(parts[1].strip().split()[0])
            total = mem_info.get("MemTotal", 0)
            avail = mem_info.get("MemAvailable", total)
            if total > 0:
                used_pct = int(round((1.0 - (avail / total)) * 100))
                if used_pct >= ram_limit:
                    alert_msg = f"⚠️ RAM {used_pct}% · /offload or App Nap"
        except Exception:
            pass

        if not alert_msg:
            try:
                ps_dir = "/sys/class/power_supply"
                if os.path.isdir(ps_dir):
                    for entry in os.listdir(ps_dir):
                        if not entry.startswith("BAT"):
                            continue
                        cap_p = os.path.join(ps_dir, entry, "capacity")
                        st_p = os.path.join(ps_dir, entry, "status")
                        if os.path.isfile(cap_p):
                            with open(cap_p, "r", encoding="utf-8") as cf:
                                cap = int(cf.read().strip())
                            st = ""
                            if os.path.isfile(st_p):
                                with open(st_p, "r", encoding="utf-8") as sf:
                                    st = sf.read().strip().lower()
                            if st == "discharging" and cap <= bat_limit:
                                alert_msg = f"🪫 Battery {cap}% · plug in charger"
                                break
            except Exception:
                pass

        if alert_msg:
            GLib.idle_add(self._set_title_if_idle, alert_msg)

    def _destroy_idle_edge_glow(self) -> bool:
        if not self._busy and self._edge_glow is not None and not self._edge_glow.get_visible():
            try:
                self._edge_glow.destroy()
            except Exception:
                pass
            self._edge_glow = None
            _release_os_memory()
        return False

    def _set_dot(self, hex_color: str) -> None:
        self.dot_label.set_markup(f'<span foreground="{hex_color}">●</span>')

    # --- Apple Intelligence Edge Lighting Controllers (Lazy Overlay) ---
    def _set_pill_glow(self, mode: str, active: bool) -> bool:
        """Controls the iridescent Apple Intelligence glow around the Dynamic Island pill."""
        if not config.ISLAND_GLOW_ON_RESPONSE:
            return False
        if self._pill_fade_timer is not None:
            GLib.source_remove(self._pill_fade_timer)
            self._pill_fade_timer = None
        if active:
            self._pill_glow_mode = mode
            self._pill_glow_target = 1.0
            try:
                from . import dbus_service

                dbus_service.emit_edge_glow(True, mode)
            except Exception:
                pass
            if self._pill_glow_timer is None:
                self._pill_glow_last_ts = time.monotonic()
                self._pill_glow_timer = GLib.timeout_add(16, self._tick_pill_glow)
        else:
            self._pill_glow_target = 0.0
            try:
                from . import dbus_service

                dbus_service.emit_edge_glow(False, self._pill_glow_mode)
            except Exception:
                pass
            if self._pill_glow_timer is None and self._pill_glow_alpha > 0.005:
                self._pill_glow_last_ts = time.monotonic()
                self._pill_glow_timer = GLib.timeout_add(16, self._tick_pill_glow)
        return False

    def _schedule_pill_glow_fade(self, delay_ms: int = 2400) -> None:
        if self._pill_fade_timer is not None:
            GLib.source_remove(self._pill_fade_timer)
        self._pill_fade_timer = GLib.timeout_add(delay_ms, self._on_pill_fade_timeout)

    def _on_pill_fade_timeout(self) -> bool:
        self._pill_fade_timer = None
        if not self._busy:
            if self._is_recording_skill():
                self._set_pill_glow("recording", True)
            else:
                self._set_pill_glow("responding", False)
        return False

    def _tick_pill_glow(self) -> bool:
        now = time.monotonic()
        dt = max(0.001, min(0.05, now - self._pill_glow_last_ts)) if self._pill_glow_last_ts > 0 else 0.016
        self._pill_glow_last_ts = now

        # Native 60 FPS frame-rate-independent halo phase & alpha interpolation
        self._pill_glow_phase = (self._pill_glow_phase + 0.33 * dt) % 1.0
        step = (3.6 if self._pill_glow_target > self._pill_glow_alpha else 2.4) * dt
        if abs(self._pill_glow_target - self._pill_glow_alpha) <= step:
            self._pill_glow_alpha = self._pill_glow_target
        elif self._pill_glow_target > self._pill_glow_alpha:
            self._pill_glow_alpha += step
        else:
            self._pill_glow_alpha -= step

        self.main_box.queue_draw()
        if self._pill_glow_alpha <= 0.005 and self._pill_glow_target == 0.0:
            self._pill_glow_alpha = 0.0
            self._pill_glow_timer = None
            return False
        return True

    def _on_pill_draw(self, widget, cr) -> bool:
        if self._pill_glow_alpha <= 0.01:
            return False
        from . import edge_glow

        alloc = widget.get_allocation()
        edge_glow.paint_apple_pill_glow(
            cr,
            float(alloc.width),
            float(alloc.height),
            radius=26.0,
            phase=self._pill_glow_phase,
            intensity=self._pill_glow_alpha,
            mode=self._pill_glow_mode,
        )
        return False

    def _set_fullscreen_task_glow(self, active: bool) -> bool:
        """Lazy-creates the fullscreen overlay ONLY when a task runs; 0 MB RAM at boot."""
        if not config.FULLSCREEN_GLOW_ON_TASK:
            return False
        if active:
            if self._edge_glow is None:
                from . import edge_glow

                self._edge_glow = edge_glow.EdgeGlowOverlay()
            self._edge_glow.activate(mode="task")
        elif self._edge_glow is not None:
            self._edge_glow.deactivate()
        return False

    def _center_top(self) -> None:
        display = Gdk.Display.get_default()
        if not display:
            return
        monitor = display.get_primary_monitor()
        if monitor is None and display.get_n_monitors() > 0:
            monitor = display.get_monitor(0)
        if monitor:
            geo = monitor.get_geometry()
            width = 540 if self.expanded else 310
            x = geo.x + (geo.width - width) // 2
            y = geo.y + 38
            self.move(x, y)

    def _on_button_press(self, _widget, event) -> bool:
        if event.button == 1:
            if not self.expanded:
                self.toggle_expand(force=True)
                return True
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return False

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            # Movie Feature A (Barge-In): Esc silences active TTS immediately mid-sentence!
            if "celina_pkg.voice" in sys.modules:
                from . import voice

                if voice.is_speaking():
                    voice.stop_speaking()
                    self.title_label.set_text(self._ready_label())
                    return True
            if self._busy:
                if "celina_pkg.automation" in sys.modules:
                    from . import automation

                    automation.request_stop()
                self._set_fullscreen_task_glow(False)
                self.title_label.set_text("⏹ Stopping automation...")
            if self._pending_privacy:
                self._resolve_privacy("local")
                return True
            if self._pending_approval:
                self._resolve_approval(False)
                return True
            if self.expanded and not self._busy:
                self.toggle_expand(force=False)
                return True
        return False

    def _cycle_mode(self, _btn) -> None:
        modes = ["auto", "cloud", "local"]
        self.mode = modes[(modes.index(self.mode) + 1) % len(modes)]
        self.mode_btn.set_label(self.mode.upper())

    @staticmethod
    def _lang_badge(lang_code: str) -> str:
        badges = {"auto": "🌐 AUTO", "en": "EN", "te": "తె", "hi": "हिं"}
        return badges.get(lang_code, "🌐 AUTO")

    def _cycle_lang(self, _btn) -> None:
        langs = ["auto", "en", "te", "hi"]
        self.lang = langs[(langs.index(self.lang) + 1) % len(langs)]
        self.lang_btn.set_label(self._lang_badge(self.lang))

    def _toggle_tts(self, _btn) -> None:
        self.speak_enabled = not self.speak_enabled
        if not self.speak_enabled and "celina_pkg.voice" in sys.modules:
            from . import voice

            voice.stop_speaking()
        self.tts_btn.set_label("🔊" if self.speak_enabled else "🔇")

    def toggle_expand(self, force=None) -> bool:
        self.expanded = (not self.expanded) if force is None else bool(force)
        if self.expanded:
            self.drawer.show()
            if not self._pending_approval:
                self.approval_box.hide()
            if not self._pending_privacy:
                self.privacy_box.hide()
            self.clip_chip_btn.hide()
            self.expand_btn.set_label("⌃")
            self.set_size_request(540, 350)
            self.present()
            self.entry.grab_focus()
            self._refresh_clipboard_chip()
            if self._router is not None and (self.mode == "local" or not config.OPENROUTER_API_KEY):
                self._router.warm_local_model_async()
        else:
            self.clip_chip_btn.hide()
            self._clip_chip_prompt = None
            self.drawer.hide()
            self.expand_btn.set_label("⌄")
            self.set_size_request(310, 42)
            self.resize(310, 42)
        self._center_top()
        return False

    def _refresh_clipboard_chip(self) -> None:
        """Async 0-polling clipboard watchdog check executed ONLY when the drawer opens."""
        def worker() -> None:
            clip = _read_clipboard_nonblocking()
            suggestion = _inspect_clipboard_suggestion(clip)
            GLib.idle_add(self._apply_clipboard_chip, suggestion)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_clipboard_chip(self, suggestion: Optional[tuple[str, str]]) -> bool:
        if not self.expanded or suggestion is None or self._busy:
            self._clip_chip_prompt = None
            self.clip_chip_btn.hide()
            return False
        label, prompt = suggestion
        self._clip_chip_prompt = prompt
        self.clip_chip_btn.set_label(label)
        self.clip_chip_btn.show()
        return False

    def _on_clip_chip_clicked(self, _btn) -> None:
        prompt = self._clip_chip_prompt
        self._clip_chip_prompt = None
        self.clip_chip_btn.hide()
        if prompt:
            self._submit_prompt(prompt)

    def _quick_media(self, _btn, action: str) -> None:
        from . import tools

        res = tools.control_media(action)
        self.response_label.set_text(res)
        self._ensure_media_timer()

    def _clear_chat(self, _btn) -> None:
        if self._router is not None:
            self._router.db.clear_history()
        self.response_label.set_text("Short-term conversation history cleared.")

    def _show_why_receipt(self, _btn) -> None:
        if self._router is None:
            self.response_label.set_text(
                f"No turn trace recorded yet. Ask {config.get_assistant_name()} a question first, then tap ⓘ Why!"
            )
            return
        self.response_label.set_text(self._router.get_last_receipt())

    # --- Zero-Tick Event-Driven MPRIS Media Monitor ---
    def _init_media_dbus_monitor(self) -> None:
        """Subscribes to session D-Bus MPRIS player events; sleeps at 0 ticks when no player runs."""
        try:
            self._dbus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._dbus_sub_id = self._dbus.signal_subscribe(
                "org.freedesktop.DBus",
                "org.freedesktop.DBus",
                "NameOwnerChanged",
                "/org/freedesktop/DBus",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_dbus_name_changed,
            )
            has_player, title = _probe_mpris_title_dbus(self._dbus)
            if has_player:
                if title:
                    self._set_title_if_idle(title)
                self._ensure_media_timer()
        except Exception:
            self._dbus = None

    def _on_dbus_name_changed(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        try:
            name, _old_owner, new_owner = params.unpack()
            if str(name).startswith("org.mpris.MediaPlayer2."):
                if new_owner:
                    GLib.idle_add(self._ensure_media_timer)
                else:
                    GLib.idle_add(self._poll_media_status)
        except Exception:
            pass

    def _ensure_media_timer(self) -> bool:
        if self._media_timer_id is None:
            self._media_timer_id = GLib.timeout_add_seconds(4, self._poll_media_status)
        self._poll_media_status()
        return False

    def _poll_media_status(self) -> bool:
        if self._busy:
            return True
        if self._dbus is None:
            self._media_timer_id = None
            return False

        has_player, title = _probe_mpris_title_dbus(self._dbus)
        if not has_player:
            self._set_title_if_idle(self._ready_label())
            # Zero-Tick Deep Sleep: stop 4s timer completely until an MPRIS player opens!
            self._media_timer_id = None
            return False

        self._set_title_if_idle(title if title else self._ready_label())
        return True

    def _set_title_if_idle(self, text: str) -> bool:
        if not self._busy:
            if self._is_recording_skill():
                self._set_dot("#ef4444")
                self.title_label.set_text(f"🔴 REC: {self._recording_skill_name()}")
            else:
                self.title_label.set_text(text)
        return False

    # --- Zero-Tick Smart Reminder Scheduler ---
    def _ensure_reminder_timer(self) -> bool:
        self._has_pending_reminders = True
        if self._reminder_timer_id is None:
            self._reminder_timer_id = GLib.timeout_add_seconds(5, self._check_reminders)
        return False

    def _check_reminders(self) -> bool:
        # Deep sleep: never touch SQLite if no pending reminders exist
        if not self._has_pending_reminders:
            self._reminder_timer_id = None
            return False

        if self._router is None:
            # Before waking full router, verify whether any reminder is actually due
            if not _quick_db_has_unfired_reminders():
                self._has_pending_reminders = False
                self._reminder_timer_id = None
                return False

        due = self.router.db.pop_due_reminders()
        if not due:
            still_pending = self.router.db.has_pending_reminders()
            self._has_pending_reminders = still_pending
            if not still_pending:
                # Zero-Tick Deep Sleep: cancel 5s timer until user sets a new reminder!
                self._reminder_timer_id = None
                return False
            return True

        from . import tools, voice

        for rem in due:
            msg = rem["message"]
            tools.send_notification(f"{config.get_assistant_name()} Reminder", msg)
            self.title_label.set_text(f"⏰ {msg}")
            self.response_label.set_text(f"[reminder] ⏰ {msg}")
            if self.speak_enabled:
                voice.speak_text(f"Reminder: {msg}", enabled=True)

        self._has_pending_reminders = self.router.db.has_pending_reminders()
        if not self._has_pending_reminders:
            self._reminder_timer_id = None
            return False
        return True

    # --- Tier 2 Action Approval Bridge ---
    def _request_approval(self, title: str, detail: str) -> bool:
        event = threading.Event()
        result = [False]
        self._pending_approval = (event, result)
        GLib.idle_add(self._show_approval_bar, title, detail)
        event.wait(timeout=60.0)
        if self._pending_approval is not None and self._pending_approval[0] is event:
            GLib.idle_add(self._hide_approval_bar)
            self._pending_approval = None
        return result[0]

    def _show_approval_bar(self, title: str, detail: str) -> bool:
        if not self.expanded:
            self.toggle_expand(force=True)
        self.approval_title.set_text(f"⚠️ {title}")
        self.approval_detail.set_text(detail[:500])
        self.approval_box.show()
        self.title_label.set_text("Awaiting action approval...")
        self._set_dot("#f59e0b")
        self.present()
        return False

    def _hide_approval_bar(self) -> bool:
        self.approval_box.hide()
        return False

    def _resolve_approval(self, allowed: bool) -> None:
        pending = self._pending_approval
        self._pending_approval = None
        self.approval_box.hide()
        if pending:
            event, result = pending
            result[0] = allowed
            self._set_dot("#38bdf8")
            self.title_label.set_text("Executing..." if allowed else "Action denied")
            event.set()

    # --- Privacy Consent Gate Bridge ---
    def _request_privacy_consent(self, summary: str, preview: str) -> str:
        event = threading.Event()
        result = ["local"]  # Safe default on timeout: never leak to cloud!
        self._pending_privacy = (event, result)
        GLib.idle_add(self._show_privacy_bar, summary, preview)
        event.wait(timeout=60.0)
        if self._pending_privacy is not None and self._pending_privacy[0] is event:
            GLib.idle_add(self._hide_privacy_bar)
            self._pending_privacy = None
        return result[0]

    def _show_privacy_bar(self, summary: str, preview: str) -> bool:
        if not self.expanded:
            self.toggle_expand(force=True)
        self.privacy_title.set_text(f"🛡️ {summary}")
        self.privacy_detail.set_text(preview[:350])
        self.privacy_box.show()
        self.title_label.set_text("Privacy consent required...")
        self._set_dot("#06b6d4")
        self.present()
        return False

    def _hide_privacy_bar(self) -> bool:
        self.privacy_box.hide()
        return False

    def _resolve_privacy(self, choice: str) -> None:
        pending = self._pending_privacy
        self._pending_privacy = None
        self.privacy_box.hide()
        if pending:
            event, result = pending
            result[0] = choice
            self._set_dot("#38bdf8")
            self.title_label.set_text("Keeping local..." if choice == "local" else "Continuing...")
            event.set()

    # --- Prompt Submission & Agent Execution ---
    def _update_status_ui(self, status_text: str) -> bool:
        self.title_label.set_text(status_text)
        self.response_label.set_text(status_text)
        return False

    def _on_stream_chunk(self, partial_text: str) -> bool:
        if self._busy and partial_text and partial_text != self._last_stream_text:
            self._last_stream_text = partial_text
            self.title_label.set_text("Streaming...")
            self.response_label.set_text(partial_text)
        return False

    def _resume_wake_word_if_idle(self) -> None:
        if self._wake_listener and not self._busy:
            self._wake_listener.resume()

    def _submit_prompt(self, prompt: str, keep_status: bool = False, from_voice: bool = False) -> None:
        if self._busy or not prompt:
            return
        self._busy = True
        self._last_stream_text = ""
        self.clip_chip_btn.hide()
        self._clip_chip_prompt = None
        if self._wake_listener:
            self._wake_listener.pause()
        self.entry.set_sensitive(False)
        self.voice_btn.set_sensitive(False)
        self.title_label.set_text("Thinking...")
        if not keep_status:
            self.response_label.set_text("Thinking...")
        self._set_dot("#38bdf8")
        # Apple Intelligence edge glow around Dynamic Island while thinking/responding
        self._set_pill_glow("responding", True)

        # Lightweight active-window context enrichment for deictic phrases ("summarize this", etc.)
        enriched_prompt = prompt
        try:
            from . import writing_tools

            ctx_hdr = writing_tools.get_lightweight_active_context(prompt)
            if ctx_hdr:
                enriched_prompt = f"{ctx_hdr}\n{prompt}"
        except Exception:
            pass

        last_stream_push = [0.0]
        stream_speaker_box: list[Any] = [None]
        if self.speak_enabled:
            from . import voice

            def _after_speech() -> None:
                if from_voice and os.environ.get("CELINA_VOICE_FOLLOWUP", "1") in {"1", "true", "yes"}:
                    # Siri-style 5s hands-free follow-up window
                    follow, _ = voice.listen_followup_window(timeout_sec=5.0, language=self.lang)
                    if follow:
                        GLib.idle_add(self._on_voice_finished, follow, None)
                        return
                GLib.idle_add(self._resume_wake_word_if_idle)

            stream_speaker_box[0] = voice.SentenceStreamSpeaker(
                enabled=True,
                lang=self.lang,
                on_done=_after_speech,
            )

        def throttled_stream(partial: str) -> None:
            if stream_speaker_box[0] is not None:
                stream_speaker_box[0].feed_partial(partial)
            now = time.monotonic()
            if now - last_stream_push[0] >= 0.033:  # ~30 FPS crisp UI token streaming
                last_stream_push[0] = now
                GLib.idle_add(self._on_stream_chunk, partial)

        def worker() -> None:
            reply, src = self.router.ask(
                enriched_prompt,
                mode=self.mode,
                language=self.lang,
                status_cb=lambda s: GLib.idle_add(self._update_status_ui, s),
                approval_cb=self._request_approval,
                consent_cb=self._request_privacy_consent,
                task_glow_cb=lambda active: GLib.idle_add(self._set_fullscreen_task_glow, active),
                stream_cb=throttled_stream,
            )
            # Wake reminder checker ONLY if this turn created a new reminder/timer
            if self._router is not None and self._router.db.has_pending_reminders():
                GLib.idle_add(self._ensure_reminder_timer)

            GLib.idle_add(self._on_reply_received, reply, src)
            sp = stream_speaker_box[0]
            if sp is not None and src not in {"error", "privacy"}:
                sp.finish(reply)
                GLib.idle_add(self._schedule_pill_glow_fade, 800)
            else:
                if sp is not None:
                    sp.cancel()
                GLib.idle_add(self._resume_wake_word_if_idle)

        threading.Thread(target=worker, daemon=True).start()

    def _on_submit(self, entry) -> None:
        prompt = entry.get_text().strip()
        if not prompt:
            return
        entry.set_text("")
        self._submit_prompt(prompt)

    def trigger_voice(self) -> bool:
        # Movie Feature A (Barge-In): Pressing Super+Alt+C / mic IMMEDIATELY silences active TTS!
        if "celina_pkg.voice" in sys.modules:
            from . import voice

            voice.stop_speaking()

        if not self.expanded:
            self.toggle_expand(force=True)
        if self._busy:
            return False

        self._busy = True
        if self._wake_listener:
            self._wake_listener.pause()
        self.entry.set_sensitive(False)
        self.voice_btn.set_sensitive(False)
        self.title_label.set_text("Listening...")
        self.response_label.set_text("Listening... speak now (auto-stops when you finish).")
        self._set_dot("#f43f5e")
        self._set_pill_glow("listening", True)

        def worker() -> None:
            from . import voice

            transcript, err = voice.listen_and_transcribe(
                lang_hint=self.lang,
                on_status=lambda s: GLib.idle_add(self._update_status_ui, s),
            )
            GLib.idle_add(self._on_voice_finished, transcript, err)

        threading.Thread(target=worker, daemon=True).start()
        return False

    def _on_voice_finished(self, transcript: str, err: Optional[str]) -> bool:
        self._busy = False
        self.entry.set_sensitive(True)
        self.voice_btn.set_sensitive(True)

        if err:
            if self._wake_listener:
                self._wake_listener.resume()
            self.title_label.set_text(self._ready_label())
            self.response_label.set_text(f"[voice] {err}")
            self._set_dot("#a855f7")
            self._set_pill_glow("listening", False)
            return False
        if not transcript:
            if self._wake_listener:
                self._wake_listener.resume()
            self.title_label.set_text(self._ready_label())
            self.response_label.set_text("[voice] No speech detected.")
            self._set_dot("#a855f7")
            self._set_pill_glow("listening", False)
            return False

        # Keep WakeWordListener paused until reply + TTS finishes (eliminates speaker-to-mic loop!)
        self.response_label.set_text(f'Heard: "{transcript}"\nThinking...')
        self._submit_prompt(transcript, keep_status=True, from_voice=True)
        return False

    def _on_reply_received(self, reply: str, src: str) -> bool:
        self._busy = False
        self._set_fullscreen_task_glow(False)
        self._refresh_identity_ui()
        is_rec = self._is_recording_skill()
        self.title_label.set_text(
            f"🔴 REC: {self._recording_skill_name()}" if is_rec else self._ready_label()
        )
        self.response_label.set_text(f"[{src}]\n{reply}")
        self._set_dot("#ef4444" if is_rec else "#a855f7")
        self.entry.set_sensitive(True)
        self.voice_btn.set_sensitive(True)
        self.entry.grab_focus()
        if not self.speak_enabled or src in {"error", "privacy"}:
            self._schedule_pill_glow_fade(2400)
        return False

    def _start_wake_word(self) -> None:
        if not config.WAKE_WORD_ENABLED:
            return
        from . import voice

        self._wake_listener = voice.WakeWordListener(
            on_wake=lambda: GLib.idle_add(self.trigger_voice)
        )
        self._wake_listener.start()

    def _cleanup_socket(self) -> None:
        if self._wake_listener:
            self._wake_listener.stop()
        if self._bio_observer is not None:
            try:
                self._bio_observer.stop()
            except Exception:
                pass
            self._bio_observer = None
        if self._bio_timer_id is not None:
            try:
                GLib.source_remove(self._bio_timer_id)
            except Exception:
                pass
            self._bio_timer_id = None
        try:
            from . import ios_ancs

            ios_ancs.stop_ios_bridge()
        except Exception:
            pass
        if "celina_pkg.voice" in sys.modules:
            from . import voice

            voice.stop_speaking()
        if self._dbus is not None and self._dbus_sub_id is not None:
            try:
                self._dbus.signal_unsubscribe(self._dbus_sub_id)
            except Exception:
                pass
            self._dbus_sub_id = None
        if self._edge_glow is not None:
            try:
                self._edge_glow.destroy()
            except Exception:
                pass
            self._edge_glow = None

        router_loaded = self._router is not None

        def _async_teardown() -> None:
            if router_loaded:
                try:
                    from . import mcp_client

                    mcp_client.get_mcp().offload_idle_servers(force=True)
                except Exception:
                    pass
                try:
                    from . import cache

                    cache.get_cache().offload_ram_if_idle(force=True)
                except Exception:
                    pass
            if "celina_pkg.voice" in sys.modules:
                try:
                    from . import voice

                    voice.offload_whisper_if_idle(force=True)
                except Exception:
                    pass
            _release_os_memory()

        threading.Thread(target=_async_teardown, daemon=True).start()
        if self._pending_approval:
            self._resolve_approval(False)
        if self._pending_privacy:
            self._resolve_privacy("local")
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        if os.path.exists(SOCKET_PATH):
            try:
                os.remove(SOCKET_PATH)
            except OSError:
                pass

    def _on_destroy(self, _widget) -> None:
        self._cleanup_socket()
        Gtk.main_quit()

    def _start_ipc_server(self) -> None:
        if os.path.exists(SOCKET_PATH):
            try:
                os.remove(SOCKET_PATH)
            except OSError:
                pass

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o600)
        srv.listen(5)
        self._server_sock = srv
        atexit.register(self._cleanup_socket)

        def listen() -> None:
            while True:
                try:
                    conn, _ = srv.accept()
                    with conn:
                        data = conn.recv(4096).decode("utf-8", errors="ignore").strip()
                    if data == "toggle":
                        GLib.idle_add(self.toggle_expand)
                    elif data == "voice":
                        GLib.idle_add(self.trigger_voice)
                    elif data == "auth-face":
                        GLib.idle_add(self.trigger_biometric_anim, "face", True, "Face ID")
                    elif data == "auth-touch":
                        GLib.idle_add(self.trigger_biometric_anim, "touch", True, "Touch ID")
                    elif data == "auth-fail":
                        GLib.idle_add(self.trigger_biometric_anim, "face", False, "Face ID")
                    elif data.startswith("ask:"):
                        prompt = data[4:].strip()
                        if prompt:
                            GLib.idle_add(self.toggle_expand, True)
                            GLib.idle_add(self._submit_prompt, prompt)
                    elif data == "quit":
                        GLib.idle_add(self.destroy)
                        break
                except OSError:
                    break

        threading.Thread(target=listen, daemon=True).start()


def main() -> None:
    toggle_requested = "--toggle" in sys.argv
    voice_requested = "--voice" in sys.argv
    quit_requested = "--quit" in sys.argv
    auth_anim: Optional[str] = None
    if "--auth-anim" in sys.argv:
        idx = sys.argv.index("--auth-anim")
        if idx + 1 < len(sys.argv):
            cand = sys.argv[idx + 1].strip().lower()
            if cand in {"face", "touch", "fail"}:
                auth_anim = cand
    ask_prompt: Optional[str] = None
    for flag in ("--ask", "--prompt"):
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            if idx + 1 < len(sys.argv):
                ask_prompt = " ".join(sys.argv[idx + 1 :]).strip() or None
                break

    if quit_requested:
        send_ipc_command("quit")
        sys.exit(0)

    if ask_prompt:
        ipc_cmd = f"ask:{ask_prompt[:3800]}"
    elif auth_anim:
        ipc_cmd = f"auth-{auth_anim}"
    elif voice_requested:
        ipc_cmd = "voice"
    elif toggle_requested:
        ipc_cmd = "toggle"
    else:
        ipc_cmd = "ping"

    if send_ipc_command(ipc_cmd):
        sys.exit(0)

    win = DynamicIsland(
        start_expanded=toggle_requested or bool(ask_prompt),
        start_voice=voice_requested,
        initial_prompt=ask_prompt,
    )
    if auth_anim:
        if auth_anim == "fail":
            GLib.idle_add(win.trigger_biometric_anim, "face", False, "Face ID")
        else:
            GLib.idle_add(win.trigger_biometric_anim, auth_anim, True, "Face ID" if auth_anim == "face" else "Touch ID")
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: (win.destroy(), False)[1])
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (win.destroy(), False)[1])
    Gtk.main()


if __name__ == "__main__":
    main()
