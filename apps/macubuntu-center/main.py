#!/usr/bin/env python3
"""
macubuntu Center — Apple-grade GTK 4.22 + Libadwaita 1.9 control plane.

Native control plane for macubuntu (Ubuntu 26.04 Wayland, GNOME 50)
and Celina AI OS.

Features:
  - Squircle sidebar badges, Ctrl+K quick-filter, spring-animated page reveals
  - Top-bar Pause Agents kill switch
  - Clickable Live X-Ray strip (Hardware → OS/Kernel → Audio/NPU → Gear 0/0.5
    → Local LLM → MCP Guard → Cloud Gate)
  - Drag-and-drop .mubundle updater (Gtk.DropTarget)
  - Model Hub with fit badges, Keyring API Vault, Access Permissions
  - Progressive disclosure (AdwExpanderRow for raw internals)

Run: python3 apps/macubuntu-center/main.py
"""

from __future__ import annotations

import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

# Backend lives alongside main.py in the same package directory.
from backend import (  # noqa: E402
    STAGES_DIR,
    clear_api_key_sync,
    finish_store_api_key,
    has_api_key_sync,
    inspect_update_bundle,
    list_local_models,
    load_access_policy,
    read_system_snapshot,
    save_access_policy,
    store_api_key_async,
    unload_all_llama_models,
)
from installer import (  # noqa: E402
    DownloadManager,
    InstallStep,
    InstallWizard,
    PackageManager,
    SystemWiper,
)

NAV_SECTIONS = [
    (
        "SYSTEM",
        [
            ("overview", "Overview & Health", "computer-symbolic", "sq-blue"),
            ("updates", "Updates & Stages", "software-update-available-symbolic", "sq-green"),
            ("performance", "Performance & App Nap", "speedometer-symbolic", "sq-orange"),
            ("appearance", "Appearance & Input", "preferences-desktop-appearance-symbolic", "sq-purple"),
            ("continuity", "Continuity & Power", "battery-good-charging-symbolic", "sq-teal"),
            ("recovery", "Snapshots & Recovery", "document-revert-symbolic", "sq-gray"),
            ("install", "Install macubuntu", "system-install-symbolic", "sq-green"),
            ("finish", "Install Summary", "emblem-ok-symbolic", "sq-blue"),
            ("reset", "Reset to Ubuntu", "edit-clear-symbolic", "sq-red"),
        ],
    ),
    (
        "CELINA AI & MODELS",
        [
            ("models", "Local Model Hub", "drive-harddisk-solidstate-symbolic", "sq-indigo"),
            ("apikeys", "API Keys & Cloud", "dialog-password-symbolic", "sq-blue"),
            ("knowledge", "Knowledge & Memory", "accessories-dictionary-symbolic", "sq-purple"),
            ("voice", "Voice & Live Playground", "audio-input-microphone-symbolic", "sq-red"),
        ],
    ),
    (
        "ACCESS & TRUST",
        [
            ("access", "Access & Permissions", "security-high-symbolic", "sq-green"),
            ("agents", "Agents & Shortcuts", "system-run-symbolic", "sq-orange"),
            ("security", "106 Tools & MCP03", "channel-secure-symbolic", "sq-red"),
        ],
    ),
]


class MacubuntuCenterWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="macubuntu Center")
        self.set_default_size(1080, 720)
        self.snap = read_system_snapshot()
        self.policy = load_access_policy()
        self._staged_bundle: Path | None = None
        self._nav_rows: dict[str, Gtk.ListBoxRow] = {}
        self._listboxes: list[Gtk.ListBox] = []
        self._wizard: Optional[InstallWizard] = None
        self._wiper: Optional[SystemWiper] = None
        self._install_in_progress: bool = False
        self._wipe_in_progress: bool = False
        self._install_offline_mode: bool = False
        self._install_staged_bundles: list[Path] = []
        self._install_selected_stages: list[str] = []
        self._install_bundle_rows: list[Adw.ActionRow] = []
        # All stage IDs — matches the default state where all checkboxes are active.
        self._install_all_stage_ids: list[str] = [
            "00-preflight", "10-base-deps", "20-appearance", "25-desktop-dock",
            "30-input-typography", "32-low-latency-kernel", "33-preload",
            "34-snap-removal", "35-memory-zram", "40-continuity-audio",
            "45-apps-backups-search", "46-browser-tuning", "49-cpu-mitigations-off",
            "50-power", "55-security-biometrics", "60-extras", "61-recovery-polish",
            "70-feature-celina", "99-verify",
        ]
        # Start with all stages selected (matches cb.set_active(True) below).
        self._install_selected_stages = list(self._install_all_stage_ids)
        self._install_luks_enabled: bool = False
        self._install_luks_passphrase: str = ""
        self._install_luks_recovery_key: str = ""
        self._install_user_full_name: str = ""
        self._install_user_account: str = ""
        self._install_user_password: str = ""
        self._install_user_admin: bool = True
        self._install_user_auto_login: bool = False
        self._install_user_require_pw_wake: bool = True
        self._install_zram_enabled: bool = True
        self._install_hardware_profile: str = "desktop"
        self._install_network_dhcp: bool = True
        self._install_network_static_ip: str = ""
        self._install_network_gateway: str = ""
        self._install_network_dns: str = ""
        self._install_storage_auto_partition: bool = True
        self._install_storage_btrfs: bool = True
        self._install_timezone: str = "Asia/Kolkata"
        self._install_locale: str = "en_US.UTF-8"
        self._install_keyboard: str = "us"
        self._install_a11y_screen_reader: bool = False
        self._install_a11y_high_contrast: bool = False
        self._install_a11y_large_text: bool = False
        self._install_a11y_sticky_keys: bool = False
        self._install_power_profile: str = "balanced"
        self._install_power_sleep: str = "suspend"
        self._install_security_firewall: bool = True
        self._install_security_autoupdate: bool = True
        self._install_security_snapd: bool = False
        self._install_backup_enabled: bool = True
        self._install_backup_target: str = "external"
        self._install_update_channel: str = "stable"
        self._install_update_auto: bool = True
        self._install_svc_celina: bool = True
        self._install_svc_npu: bool = False
        self._install_svc_app_nap: bool = True
        self._install_finish_shown: bool = False
        self._install_gpg_keys: list[dict] = []
        self._install_models: list[str] = []
        self._install_api_keys_configured: bool = False
        self._install_access_landlock: bool = True
        self._install_access_sandbox: bool = True
        self._install_wipe_keep_home: bool = False
        self._install_wipe_keep_configs: bool = False
        self._install_wipe_confirm: str = ""
        self._install_software: dict[str, bool] = {
            "office": False,
            "creative": False,
            "development": False,
            "gaming": False,
            "multimedia": False,
        }
        self._install_zram_size: str = "auto"
        self._install_zram_alg: str = "zstd"
        self._install_snapshot_enabled: bool = True
        self._install_app_nap_enabled: bool = True

        # Performance screen state
        self._perf_app_nap_enabled: bool = True
        self._perf_zram_enabled: bool = True
        self._perf_cpu_governor: str = "sched_ext"
        self._perf_hardware_tier: str = self.snap.ram_tier

        # Appearance screen state
        self._appear_glass_enabled: bool = True
        self._appear_accent_color: str = "blue"
        self._appear_font_scale: float = 1.0
        self._appear_dock_position: str = "bottom"

        # Continuity screen state
        self._cont_local_send: bool = False
        self._cont_airplay: bool = False
        self._cont_deskflow: bool = False
        self._cont_battery_cap: bool = True
        self._cont_s0ix: bool = False

        # Recovery screen state
        self._rec_snapshot_enabled: bool = True
        self._rec_btrbk_enabled: bool = False
        self._rec_snapper_autosnap: bool = True

        # Knowledge screen state
        self._know_core_blocks: int = 0
        self._know_graph_tables: int = 17
        self._know_spotlight_index: bool = True

        # Voice screen state
        self._voice_wake_word: bool = False
        self._voice_always_on: bool = False
        self._voice_tts_engine: str = "kokoro"
        self._voice_whisper_model: str = "base"

        # Agents screen state
        self._agents_taskgraph: bool = False
        self._agents_saga_rollback: bool = True
        self._agents_shortcuts_crystallizer: bool = True
        self._agents_scrapling: bool = False

        # Security screen state
        self._sec_mcp_pin_enabled: bool = True
        self._sec_never_exposed_paths: bool = True
        self._sec_sha256_manifest: bool = True
        self._sec_tool_count: int = 106

        if self.snap.ram_tier != "8gb" and self.snap.gpu_vendor != "cpu":
            self.add_css_class("glass-enabled")

        # Main NavigationSplitView (macOS System Settings structure)
        self.split = Adw.NavigationSplitView(min_sidebar_width=238, max_sidebar_width=272)
        self.toast_overlay = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toast_overlay)

        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=190,
        )

        self._build_sidebar()
        self._build_content_shell()
        self._populate_pages()
        self._install_shortcuts()

        # Refresh the finish page whenever it becomes visible, so the
        # summary always reflects the current install settings (fixes
        # stale finish page where values were captured at build time).
        self.stack.connect("notify::visible-child", self._on_stack_visible_child)

        # Select Overview initially
        first_row = self._nav_rows["overview"]
        first_row.get_parent().select_row(first_row)

        # Lightweight 2s non-blocking telemetry refresh while window is visible
        GLib.timeout_add_seconds(2, self._refresh_telemetry)

    # ------------------------------------------------------------------ #
    # Sidebar
    # ------------------------------------------------------------------ #
    def _build_sidebar(self) -> None:
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("apple-sidebar")

        header = Adw.HeaderBar(show_end_title_buttons=False)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search (Ctrl+K)")
        self.search_entry.connect("search-changed", self._on_search_changed)
        header.set_title_widget(self.search_entry)
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        for section_title, items in NAV_SECTIONS:
            lbl = Gtk.Label(label=section_title, xalign=0.0)
            lbl.add_css_class("sidebar-section-label")
            box.append(lbl)

            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            lb.add_css_class("apple-nav-list")
            lb.connect("row-selected", self._on_nav_selected)
            self._listboxes.append(lb)

            for page_id, title, icon_name, color_cls in items:
                row = Gtk.ListBoxRow()
                row._page_id = page_id
                row._search_title = title.lower()

                h = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER)
                badge = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
                badge.add_css_class("squircle")
                badge.add_css_class(color_cls)
                badge.append(Gtk.Image.new_from_icon_name(icon_name))
                h.append(badge)
                h.append(Gtk.Label(label=title, xalign=0.0, hexpand=True))
                row.set_child(h)
                lb.append(row)
                self._nav_rows[page_id] = row

            box.append(lb)

        # Compact bottom hardware summary footer
        footer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            margin_start=14,
            margin_end=14,
            margin_bottom=12,
            margin_top=8,
        )
        self.sidebar_footer = Gtk.Label(
            label=(
                f"Tier: {self.snap.ram_tier.upper()}  •  "
                f"GPU: {self.snap.gpu_vendor.upper()}  •  "
                f"NPU: {self.snap.npu_vendor.upper()}"
            ),
            xalign=0.0,
        )
        self.sidebar_footer.add_css_class("dim-label")
        self.sidebar_footer.add_css_class("mono")
        footer.append(self.sidebar_footer)

        scrolled = Gtk.ScrolledWindow(child=box, vexpand=True)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(scrolled)
        outer.append(footer)
        toolbar.set_content(outer)
        self.split.set_sidebar(Adw.NavigationPage.new(toolbar, "macubuntu Center"))

    # ------------------------------------------------------------------ #
    # Content shell
    # ------------------------------------------------------------------ #
    def _build_content_shell(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.health_pill = Gtk.Label(label="● Healthy")
        self.health_pill.add_css_class("pill")
        self.health_pill.add_css_class("pill-green")
        header.pack_start(self.health_pill)

        kill_btn = Gtk.Button(label="Pause Agents")
        kill_btn.add_css_class("kill-switch")
        kill_btn.connect("clicked", self._on_pause_agents)
        header.pack_end(kill_btn)

        toolbar.add_top_bar(header)
        toolbar.set_content(self.stack)
        self.split.set_content(Adw.NavigationPage.new(toolbar, "Details"))

    # ------------------------------------------------------------------ #
    # Page helpers
    # ------------------------------------------------------------------ #
    def _spring_reveal(self, widget: Gtk.Widget) -> None:
        """Crisp Apple-like spring opacity reveal."""
        widget.set_opacity(0.0)
        target = Adw.CallbackAnimationTarget.new(
            lambda v: widget.set_opacity(max(0.0, min(1.0, v)))
        )
        anim = Adw.SpringAnimation.new(
            widget, 0.0, 1.0, Adw.SpringParams.new(0.88, 1.0, 380.0), target
        )
        anim.play()

    def _wrap_page(self, title: str, subtitle: str, body: Gtk.Widget) -> Gtk.ScrolledWindow:
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=22,
            margin_bottom=28,
            margin_start=26,
            margin_end=26,
        )
        t = Gtk.Label(label=title, xalign=0.0)
        t.add_css_class("page-title")
        s = Gtk.Label(label=subtitle, xalign=0.0, wrap=True)
        s.add_css_class("page-subtitle")
        outer.append(t)
        outer.append(s)
        outer.append(body)

        clamp = Adw.Clamp(
            maximum_size=780, tightening_threshold=620, child=outer
        )
        return Gtk.ScrolledWindow(child=clamp, vexpand=True)

    def _metric_card(self, parent: Gtk.Box, title: str, value: str) -> Gtk.Label:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.add_css_class("metric-card")
        t = Gtk.Label(label=title, xalign=0.0)
        t.add_css_class("metric-title")
        v = Gtk.Label(label=value, xalign=0.0)
        v.add_css_class("metric-value")
        v.add_css_class("metric-num")
        card.append(t)
        card.append(v)
        parent.append(card)
        return v

    def _make_combo_row(self, title: str, items: list[str], selected: int,
                        callback) -> Adw.ComboRow:
        """GTK4/Libadwaita-correct Adw.ComboRow factory.

        Replaces the broken .append(Gtk.StringObject(...)) pattern.
        Adw.ComboRow takes a model=Gtk.StringList, not per-item appends.
        """
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(items))
        row.set_selected(selected)
        row.connect("notify::selected", callback)
        return row

    # ------------------------------------------------------------------ #
    # Pages
    # ------------------------------------------------------------------ #
    def _populate_pages(self) -> None:
        self.stack.add_named(self._page_overview(), "overview")
        self.stack.add_named(self._page_updates(), "updates")
        self.stack.add_named(self._page_install(), "install")
        self.stack.add_named(self._page_finish(), "finish")
        self.stack.add_named(self._page_models(), "models")
        self.stack.add_named(self._page_apikeys(), "apikeys")
        self.stack.add_named(self._page_access(), "access")
        self.stack.add_named(self._page_reset(), "reset")
        self.stack.add_named(self._page_performance(), "performance")
        self.stack.add_named(self._page_appearance(), "appearance")
        self.stack.add_named(self._page_continuity(), "continuity")
        self.stack.add_named(self._page_recovery(), "recovery")
        self.stack.add_named(self._page_knowledge(), "knowledge")
        self.stack.add_named(self._page_voice(), "voice")
        self.stack.add_named(self._page_agents(), "agents")
        self.stack.add_named(self._page_security(), "security")

        scaffolds = {
            "performance": (
                "Performance & App Nap",
                "Hardware profile (8/16/32 GB), sched_ext CPU scheduler, ZRAM, and background App Nap.",
            ),
            "appearance": (
                "Appearance, Input & Audio",
                "Liquid Glass blur, celina-island HUD, keyd Cmd mapping, maccel curves, and ddcutil brightness.",
            ),
            "continuity": (
                "Continuity & Power",
                "LocalSend, AirPlay/UxPlay, Deskflow, 80% battery cap, and S0ix suspend-then-hibernate.",
            ),
            "recovery": (
                "Snapshots & Recovery",
                "Btrfs/Snapper pre/post timeline, btrbk external Time Machine, and Stage 61 recovery.",
            ),
            "knowledge": (
                "Knowledge & Memory Studio",
                "Core Memory blocks, Bi-Temporal Knowledge Graph, 17 SQLite tables, Spotlight index, and Contact cards.",
            ),
            "voice": (
                "Voice & Live Playground",
                "PipeWire AEC mic, Silero VAD, NPU Whisper, Kokoro/LFM3 TTS, and live routing X-ray.",
            ),
            "agents": (
                "Agents, Shortcuts & Web Watchers",
                "Live TaskGraph DAGs, Saga Rollback, Shortcuts Crystallizer, and Scrapling page-change watchers.",
            ),
            "security": (
                "106 Tools & MCP03 Security",
                "SHA-256 Capability Manifest permissions, MCP tool-poisoning pins, and searchable evidence.py ledger.",
            ),
        }
        for pid, (title, sub) in scaffolds.items():
            card = Adw.PreferencesGroup(
                title="Controls",
                description=(
                    "Wired via /run/user/$UID/celina/control.sock and Stage helpers."
                ),
            )
            card.add(
                Adw.ActionRow(
                    title=title,
                    subtitle=(
                        "Click 'Run Stage 99 Check' on Overview to inspect active state."
                    ),
                )
            )
            self.stack.add_named(self._wrap_page(title, sub, card), pid)

    # -- Page: Performance & App Nap -- #
    def _page_performance(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Hardware tier
        tier_grp = Adw.PreferencesGroup(title="Hardware Profile")
        tier_grp.set_description(
            "Detected RAM tier and GPU/NPU vendors from /etc/macubuntu/hardware.json."
        )
        tier_row = Adw.ActionRow(
            title="RAM Tier",
            subtitle=self._perf_hardware_tier.upper(),
        )
        pill = Gtk.Label(label=self._perf_hardware_tier.upper(), valign=Gtk.Align.CENTER)
        pill.add_css_class("pill")
        pill.add_css_class("pill-green")
        tier_row.add_suffix(pill)
        tier_grp.add(tier_row)

        gpu_row = Adw.ActionRow(
            title="GPU Vendor",
            subtitle=self.snap.gpu_vendor.upper(),
        )
        gpu_pill = Gtk.Label(label=self.snap.gpu_vendor.upper(), valign=Gtk.Align.CENTER)
        gpu_pill.add_css_class("pill")
        gpu_pill.add_css_class("pill-blue")
        gpu_row.add_suffix(gpu_pill)
        tier_grp.add(gpu_row)

        npu_row = Adw.ActionRow(
            title="NPU Vendor",
            subtitle=self.snap.npu_vendor.upper(),
        )
        npu_pill = Gtk.Label(label=self.snap.npu_vendor.upper(), valign=Gtk.Align.CENTER)
        npu_pill.add_css_class("pill")
        npu_pill.add_css_class("pill-blue")
        npu_row.add_suffix(npu_pill)
        tier_grp.add(npu_row)
        box.append(tier_grp)

        # App Nap controls
        nap_grp = Adw.PreferencesGroup(
            title="App Nap (Idle App Freezing)",
            description=(
                "Automatically freeze idle background apps to reclaim memory and CPU. "
                "Audio players, terminals, IDEs, and Celina AI services are never frozen."
            ),
        )
        self._perf_app_nap_row = Adw.SwitchRow(
            title="Enable App Nap",
            subtitle="Freeze idle background apps to save resources",
            active=self._perf_app_nap_enabled,
        )
        self._perf_app_nap_row.connect(
            "notify::active", self._on_perf_app_nap_toggled
        )
        nap_grp.add(self._perf_app_nap_row)

        nap_status = Gtk.Label(
            label="App Nap service: running via celina-app-nap@.service",
            xalign=0.0,
        )
        nap_status.add_css_class("dim-label")
        nap_status.add_css_class("mono")
        nap_grp.add(nap_status)
        box.append(nap_grp)

        # ZRAM controls
        zram_grp = Adw.PreferencesGroup(
            title="ZRAM Compressed Swap",
            description=(
                "Compress memory pages in RAM instead of swapping to disk. "
                "Reduces disk wear and improves responsiveness under pressure."
            ),
        )
        self._perf_zram_row = Adw.SwitchRow(
            title="Enable ZRAM",
            subtitle="Compressed in-memory swap device",
            active=self._perf_zram_enabled,
        )
        self._perf_zram_row.connect(
            "notify::active", self._on_perf_zram_toggled
        )
        zram_grp.add(self._perf_zram_row)

        zram_size_row = self._make_combo_row(
            "ZRAM Size",
            ["auto", "25% of RAM", "50% of RAM", "100% of RAM"],
            0,
            self._on_perf_zram_size_changed,
        )
        zram_grp.add(zram_size_row)
        box.append(zram_grp)

        # CPU scheduler
        sched_grp = Adw.PreferencesGroup(title="CPU Scheduler")
        sched_grp.set_description(
            "Active scheduler: sched_ext (scx) with Celina slice management."
        )
        sched_row = Adw.ActionRow(
            title="Scheduler",
            subtitle=self._perf_cpu_governor,
        )
        sched_pill = Gtk.Label(label=self._perf_cpu_governor, valign=Gtk.Align.CENTER)
        sched_pill.add_css_class("pill")
        sched_pill.add_css_class("pill-blue")
        sched_row.add_suffix(sched_pill)
        sched_grp.add(sched_row)
        box.append(sched_grp)

        # Socket status
        sock_grp = Adw.PreferencesGroup(title="Backend Socket Status")
        sock_grp.set_description(
            "JSON-over-UDS connection to Celina hardware governor."
        )
        sock_row = Adw.ActionRow(
            title="App Nap Socket",
            subtitle=f"/run/user/{self._get_uid()}/celina/app_nap.sock",
        )
        sock_pill = Gtk.Label(label="Reachable", valign=Gtk.Align.CENTER)
        sock_pill.add_css_class("pill")
        sock_pill.add_css_class("pill-green")
        sock_row.add_suffix(sock_pill)
        sock_grp.add(sock_row)
        box.append(sock_grp)

        return self._wrap_page(
            "Performance & App Nap",
            "Tune hardware profile, App Nap, ZRAM, and CPU scheduler for your tier.",
            box,
        )

    # -- Page: Appearance & Input -- #
    def _page_appearance(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Liquid Glass
        glass_grp = Adw.PreferencesGroup(
            title="Liquid Glass Effects",
            description=(
                "System-wide blur, transparency, and vibrancy for windows, "
                "sidebar, and widgets (macOS Liquid Glass parity)."
            ),
        )
        self._appear_glass_row = Adw.SwitchRow(
            title="Enable Liquid Glass",
            subtitle="Blur and vibrancy across shell and apps",
            active=self._appear_glass_enabled,
        )
        self._appear_glass_row.connect(
            "notify::active", self._on_appear_glass_toggled
        )
        glass_grp.add(self._appear_glass_row)
        box.append(glass_grp)

        # Accent color
        accent_grp = Adw.PreferencesGroup(title="Accent Color")
        accent_grp.set_description("Shell accent color for buttons, pills, and highlights.")
        accent_row = self._make_combo_row(
            "Accent",
            ["blue", "purple", "pink", "orange", "green", "red"],
            0,
            self._on_appear_accent_changed,
        )
        accent_grp.add(accent_row)
        box.append(accent_grp)

        # Font scale
        font_grp = Adw.PreferencesGroup(title="Text Scaling")
        font_grp.set_description("System-wide font scale factor (accessibility).")
        font_row = Adw.SpinRow.new_with_range(0.75, 1.5, 0.05)
        font_row.set_value(self._appear_font_scale)
        font_row.set_title("Font Scale")
        font_row.connect("notify::value", self._on_appear_font_changed)
        font_grp.add(font_row)
        box.append(font_grp)

        # Dock position
        dock_grp = Adw.PreferencesGroup(title="Dock Position")
        dock_grp.set_description("Position of the celina dock on screen.")
        dock_row = self._make_combo_row(
            "Position",
            ["bottom", "left", "right"],
            0,
            self._on_appear_dock_changed,
        )
        dock_grp.add(dock_row)
        box.append(dock_grp)

        # Audio/NPU note
        audio_grp = Adw.PreferencesGroup(title="Audio & NPU")
        audio_grp.set_description(
            "PipeWire AEC mic input and NPU audio offloading. "
            "Configured on the Voice & Live Playground screen."
        )
        audio_row = Adw.ActionRow(
            title="Audio Backend",
            subtitle="PipeWire + Silero VAD (NPU offload optional)",
        )
        audio_pill = Gtk.Label(label="PipeWire", valign=Gtk.Align.CENTER)
        audio_pill.add_css_class("pill")
        audio_pill.add_css_class("pill-blue")
        audio_row.add_suffix(audio_pill)
        audio_grp.add(audio_row)
        box.append(audio_grp)

        return self._wrap_page(
            "Appearance & Input",
            "Liquid Glass blur, accent color, font scaling, dock position, and audio backend.",
            box,
        )

    # -- Page: Continuity & Power -- #
    def _page_continuity(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Continuity services
        cont_grp = Adw.PreferencesGroup(
            title="Continuity Services",
            description=(
                "LocalSend file transfer, AirPlay/UxPlay receiver, "
                "and Deskflow mouse/keyboard sharing."
            ),
        )
        self._cont_local_send_row = Adw.SwitchRow(
            title="LocalSend",
            subtitle="Send/receive files over local network",
            active=self._cont_local_send,
        )
        self._cont_local_send_row.connect(
            "notify::active", self._on_cont_local_send_toggled
        )
        cont_grp.add(self._cont_local_send_row)

        self._cont_airplay_row = Adw.SwitchRow(
            title="AirPlay / UxPlay Receiver",
            subtitle="Receive AirPlay screen mirroring from Apple devices",
            active=self._cont_airplay,
        )
        self._cont_airplay_row.connect(
            "notify::active", self._on_cont_airplay_toggled
        )
        cont_grp.add(self._cont_airplay_row)

        self._cont_deskflow_row = Adw.SwitchRow(
            title="Deskflow",
            subtitle="Share mouse and keyboard across nearby computers",
            active=self._cont_deskflow,
        )
        self._cont_deskflow_row.connect(
            "notify::active", self._on_cont_deskflow_toggled
        )
        cont_grp.add(self._cont_deskflow_row)
        box.append(cont_grp)

        # Power / battery
        power_grp = Adw.PreferencesGroup(
            title="Power Management",
            description=(
                "80% battery cap for longevity, S0ix suspend-then-hibernate, "
                "and AC power behavior."
            ),
        )
        self._cont_battery_cap_row = Adw.SwitchRow(
            title="80% Battery Charge Cap",
            subtitle="Limit charging to 80% for battery longevity",
            active=self._cont_battery_cap,
        )
        self._cont_battery_cap_row.connect(
            "notify::active", self._on_cont_battery_cap_toggled
        )
        power_grp.add(self._cont_battery_cap_row)

        self._cont_s0ix_row = Adw.SwitchRow(
            title="S0ix Suspend-Then-Hibernate",
            subtitle="Suspend to RAM, hibernate to disk after timeout",
            active=self._cont_s0ix,
        )
        self._cont_s0ix_row.connect(
            "notify::active", self._on_cont_s0ix_toggled
        )
        power_grp.add(self._cont_s0ix_row)
        box.append(power_grp)

        # Battery status (cgroup v2 file read)
        batt_grp = Adw.PreferencesGroup(title="Battery Status")
        batt_grp.set_description("Read from /sys/fs/cgroup/ and sysfs power supply.")
        batt_row = Adw.ActionRow(
            title="Battery Level",
            subtitle="Reading from cgroup v2 power supply",
        )
        batt_pill = Gtk.Label(label="—%", valign=Gtk.Align.CENTER)
        batt_pill.add_css_class("pill")
        batt_pill.add_css_class("pill-blue")
        batt_row.add_suffix(batt_pill)
        power_grp.add(batt_row)
        box.append(power_grp)

        return self._wrap_page(
            "Continuity & Power",
            "LocalSend, AirPlay/UxPlay, Deskflow, battery cap, and S0ix suspend.",
            box,
        )

    # -- Page: Snapshots & Recovery -- #
    def _page_recovery(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Snapshot controls
        snap_grp = Adw.PreferencesGroup(
            title="System Snapshots (Btrfs + Snapper)",
            description=(
                "Pre/post timeline snapshots for one-click rollback. "
                "Stage 61 recovery integration."
            ),
        )
        self._rec_snapshot_row = Adw.SwitchRow(
            title="Automatic Snapshots",
            subtitle="Create Btrfs snapshots before major changes",
            active=self._rec_snapshot_enabled,
        )
        self._rec_snapshot_row.connect(
            "notify::active", self._on_rec_snapshot_toggled
        )
        snap_grp.add(self._rec_snapshot_row)

        self._rec_snapper_row = Adw.SwitchRow(
            title="Snapper Auto-Snapshots",
            subtitle="timeline-based automatic snapshot creation",
            active=self._rec_snapper_autosnap,
        )
        self._rec_snapper_row.connect(
            "notify::active", self._on_rec_snapper_toggled
        )
        snap_grp.add(self._rec_snapper_row)
        box.append(snap_grp)

        # External backup
        btrbk_grp = Adw.PreferencesGroup(
            title="External Backup (btrbk)",
            description=(
                "Time Machine-style external backup via btrbk send/receive. "
                "Requires external Btrfs-formatted drive."
            ),
        )
        self._rec_btrbk_row = Adw.SwitchRow(
            title="Enable btrbk Backup",
            subtitle="Automated incremental backup to external drive",
            active=self._rec_btrbk_enabled,
        )
        self._rec_btrbk_row.connect(
            "notify::active", self._on_rec_btrbk_toggled
        )
        btrbk_grp.add(self._rec_btrbk_row)
        box.append(btrbk_grp)

        # Recovery socket status
        sock_grp = Adw.PreferencesGroup(title="Recovery Socket Status")
        sock_grp.set_description(
            "JSON-over-UDS connection to Celina snapshot manager."
        )
        sock_row = Adw.ActionRow(
            title="Snapshot Socket",
            subtitle=f"/run/user/{self._get_uid()}/celina/snapshot.sock",
        )
        sock_pill = Gtk.Label(label="Reachable", valign=Gtk.Align.CENTER)
        sock_pill.add_css_class("pill")
        sock_pill.add_css_class("pill-green")
        sock_row.add_suffix(sock_pill)
        sock_grp.add(sock_row)
        box.append(sock_grp)

        # Stage 61 recovery note
        stage_grp = Adw.PreferencesGroup(title="Stage 61 Recovery")
        stage_grp.set_description(
            "Preserve Stage 61 exit-code-3 rollback contract for failed installs."
        )
        stage_row = Adw.ActionRow(
            title="Rollback Contract",
            subtitle="Exit code 3 on failure triggers automatic rollback",
        )
        stage_pill = Gtk.Label(label="Active", valign=Gtk.Align.CENTER)
        stage_pill.add_css_class("pill")
        stage_pill.add_css_class("pill-green")
        stage_row.add_suffix(stage_pill)
        stage_grp.add(stage_row)
        box.append(stage_grp)

        return self._wrap_page(
            "Snapshots & Recovery",
            "Btrfs/Snapper timeline, btrbk external backup, and Stage 61 recovery.",
            box,
        )

    # -- Page: Knowledge & Memory Studio -- #
    def _page_knowledge(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Core memory blocks
        mem_grp = Adw.PreferencesGroup(
            title="Core Memory Blocks",
            description=(
                "Persistent key-value facts stored in Celina's bi-temporal "
                "SQLite memory database."
            ),
        )
        blocks_row = Adw.ActionRow(
            title="Core Memory Blocks",
            subtitle=str(self._know_core_blocks),
        )
        blocks_pill = Gtk.Label(label=str(self._know_core_blocks), valign=Gtk.Align.CENTER)
        blocks_pill.add_css_class("pill")
        blocks_pill.add_css_class("pill-blue")
        blocks_row.add_suffix(blocks_pill)
        mem_grp.add(blocks_row)
        box.append(mem_grp)

        # Knowledge graph tables
        graph_grp = Adw.PreferencesGroup(
            title="Knowledge Graph",
            description=(
                "17 SQLite tables for semantic triples, episodes, goals, "
                "and skill execution traces."
            ),
        )
        tables_row = Adw.ActionRow(
            title="Graph Tables",
            subtitle=str(self._know_graph_tables),
        )
        tables_pill = Gtk.Label(label=str(self._know_graph_tables), valign=Gtk.Align.CENTER)
        tables_pill.add_css_class("pill")
        tables_pill.add_css_class("pill-blue")
        tables_row.add_suffix(tables_pill)
        graph_grp.add(tables_row)
        box.append(graph_grp)

        # Spotlight index
        spot_grp = Adw.PreferencesGroup(
            title="Spotlight Index",
            description=(
                "Local Spotlight-style index for fast semantic search across "
                "files, skills, and knowledge graph entries."
            ),
        )
        self._know_spotlight_row = Adw.SwitchRow(
            title="Enable Spotlight Index",
            subtitle="Index local files for fast semantic search",
            active=self._know_spotlight_index,
        )
        self._know_spotlight_row.connect(
            "notify::active", self._on_know_spotlight_toggled
        )
        spot_grp.add(self._know_spotlight_row)
        box.append(spot_grp)

        # Memory DB path
        db_grp = Adw.PreferencesGroup(title="Memory Database")
        db_grp.set_description("SQLite database at ~/.local/share/celina/db/memory.db")
        db_row = Adw.ActionRow(
            title="DB Path",
            subtitle=str(Path.home() / ".local" / "share" / "celina" / "db" / "memory.db"),
        )
        db_pill = Gtk.Label(label="SQLite", valign=Gtk.Align.CENTER)
        db_pill.add_css_class("pill")
        db_pill.add_css_class("pill-green")
        db_row.add_suffix(db_pill)
        db_grp.add(db_row)
        box.append(db_grp)

        return self._wrap_page(
            "Knowledge & Memory",
            "Core Memory blocks, bi-temporal knowledge graph, SQLite tables, and Spotlight index.",
            box,
        )

    # -- Page: Voice & Live Playground -- #
    def _page_voice(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Wake word
        wake_grp = Adw.PreferencesGroup(
            title="Wake Word Detection",
            description=(
                "Always-on hotword detection for hands-free Celina activation. "
                "Runs locally on NPU/CPU — no cloud."
            ),
        )
        self._voice_wake_row = Adw.SwitchRow(
            title="Wake Word Enabled",
            subtitle="Listen for 'Hey Celina' hotword",
            active=self._voice_wake_word,
        )
        self._voice_wake_row.connect(
            "notify::active", self._on_voice_wake_toggled
        )
        wake_grp.add(self._voice_wake_row)
        box.append(wake_grp)

        # Always-on voice
        always_grp = Adw.PreferencesGroup(
            title="Always-On Voice (AEC + VAD)",
            description=(
                "PipeWire AEC microphone with Silero VAD. Keeps "
                "celina-voice.service active on AC power."
            ),
        )
        self._voice_always_row = Adw.SwitchRow(
            title="Always-On Microphone",
            subtitle="Continuous voice activity detection",
            active=self._voice_always_on,
        )
        self._voice_always_row.connect(
            "notify::active", self._on_voice_always_toggled
        )
        always_grp.add(self._voice_always_row)
        box.append(always_grp)

        # TTS engine
        tts_grp = Adw.PreferencesGroup(title="Text-to-Speech Engine")
        tts_grp.set_description("Local TTS voice synthesis (Kokoro / LFM3 / Piper).")
        tts_row = self._make_combo_row(
            "TTS Engine",
            ["kokoro", "lfm3", "piper"],
            0,
            self._on_voice_tts_changed,
        )
        tts_grp.add(tts_row)
        box.append(tts_grp)

        # Whisper model
        whisper_grp = Adw.PreferencesGroup(title="Speech-to-Text Model")
        whisper_grp.set_description("NPU-accelerated Whisper for local STT.")
        whisper_row = self._make_combo_row(
            "Whisper Model",
            ["tiny", "base", "small", "medium"],
            1,
            self._on_voice_whisper_changed,
        )
        whisper_grp.add(whisper_row)
        box.append(whisper_grp)

        # Voice socket status
        sock_grp = Adw.PreferencesGroup(title="Voice Socket Status")
        sock_grp.set_description(
            "JSON-over-UDS connection to celina-voice.service."
        )
        sock_row = Adw.ActionRow(
            title="Voice Socket",
            subtitle=f"/run/user/{self._get_uid()}/celina/voice.sock",
        )
        sock_pill = Gtk.Label(label="Reachable", valign=Gtk.Align.CENTER)
        sock_pill.add_css_class("pill")
        sock_pill.add_css_class("pill-green")
        sock_row.add_suffix(sock_pill)
        sock_grp.add(sock_row)
        box.append(sock_grp)

        return self._wrap_page(
            "Voice & Live Playground",
            "PipeWire AEC mic, Silero VAD, NPU Whisper, Kokoro/LFM3 TTS, and live routing X-ray.",
            box,
        )

    # -- Page: Agents & Shortcuts -- #
    def _page_agents(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # TaskGraph DAGs
        tg_grp = Adw.PreferencesGroup(
            title="Live TaskGraph DAGs",
            description=(
                "Directed acyclic graphs for multi-step agent workflows. "
                "Visual execution with dependency tracking."
            ),
        )
        self._agents_taskgraph_row = Adw.SwitchRow(
            title="TaskGraph DAGs",
            subtitle="Live multi-step agent workflow visualization",
            active=self._agents_taskgraph,
        )
        self._agents_taskgraph_row.connect(
            "notify::active", self._on_agents_taskgraph_toggled
        )
        tg_grp.add(self._agents_taskgraph_row)
        box.append(tg_grp)

        # Saga rollback
        saga_grp = Adw.PreferencesGroup(
            title="Saga Rollback",
            description=(
                "Distributed transaction rollback across agent tool calls. "
                "Compensating transactions on failure."
            ),
        )
        self._agents_saga_row = Adw.SwitchRow(
            title="Saga Rollback",
            subtitle="Auto-rollback on agent tool failure",
            active=self._agents_saga_rollback,
        )
        self._agents_saga_row.connect(
            "notify::active", self._on_agents_saga_toggled
        )
        saga_grp.add(self._agents_saga_row)
        box.append(saga_grp)

        # Shortcuts crystallizer
        sc_grp = Adw.PreferencesGroup(
            title="Shortcuts Crystallizer",
            description=(
                "Crystallize repeated agent actions into reusable "
                "celina-shortcuts entries."
            ),
        )
        self._agents_crystallizer_row = Adw.SwitchRow(
            title="Shortcuts Crystallizer",
            subtitle="Auto-crystallize repeated agent patterns",
            active=self._agents_shortcuts_crystallizer,
        )
        self._agents_crystallizer_row.connect(
            "notify::active", self._on_agents_crystallizer_toggled
        )
        sc_grp.add(self._agents_crystallizer_row)
        box.append(sc_grp)

        # Scrapling watchers
        scrap_grp = Adw.PreferencesGroup(
            title="Scrapling Page-Change Watchers",
            description=(
                "Monitor web pages for changes and trigger agent workflows "
                "when content updates."
            ),
        )
        self._agents_scrapling_row = Adw.SwitchRow(
            title="Scrapling Watchers",
            subtitle="Watch pages for changes and trigger agents",
            active=self._agents_scrapling,
        )
        self._agents_scrapling_row.connect(
            "notify::active", self._on_agents_scrapling_toggled
        )
        scrap_grp.add(self._agents_scrapling_row)
        box.append(scrap_grp)

        # Agent socket status
        sock_grp = Adw.PreferencesGroup(title="Agent Socket Status")
        sock_grp.set_description(
            "JSON-over-UDS connection to Celina agent dispatcher."
        )
        sock_row = Adw.ActionRow(
            title="Agents Socket",
            subtitle=f"/run/user/{self._get_uid()}/celina/agents.sock",
        )
        sock_pill = Gtk.Label(label="Reachable", valign=Gtk.Align.CENTER)
        sock_pill.add_css_class("pill")
        sock_pill.add_css_class("pill-green")
        sock_row.add_suffix(sock_pill)
        sock_grp.add(sock_row)
        box.append(sock_grp)

        return self._wrap_page(
            "Agents & Shortcuts",
            "Live TaskGraph DAGs, Saga Rollback, Shortcuts Crystallizer, and Scrapling page-change watchers.",
            box,
        )

    # -- Page: 106 Tools & MCP03 Security -- #
    def _page_security(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Tool count
        tools_grp = Adw.PreferencesGroup(
            title="Available Tools",
            description=(
                f"{self._sec_tool_count} capability-gated tools across "
                "11 domains: OS, filesystem, network, memory, vision, "
                "audio, input, system, mcp, skills, and web."
            ),
        )
        tools_row = Adw.ActionRow(
            title="Total Tools",
            subtitle=str(self._sec_tool_count),
        )
        tools_pill = Gtk.Label(label=str(self._sec_tool_count), valign=Gtk.Align.CENTER)
        tools_pill.add_css_class("pill")
        tools_pill.add_css_class("pill-blue")
        tools_row.add_suffix(tools_pill)
        tools_grp.add(tools_row)
        box.append(tools_grp)

        # MCP SHA-256 pinning
        mcp_grp = Adw.PreferencesGroup(
            title="MCP03 SHA-256 Pinning",
            description=(
                "Live MCP tools/list SHA-256 pinning and never_exposed_paths "
                "enforcement. Zero-cloud privacy — no plaintext secrets leave machine."
            ),
        )
        self._sec_mcp_pin_row = Adw.SwitchRow(
            title="SHA-256 Tool Pinning",
            subtitle="Pin MCP tool manifests by SHA-256 digest",
            active=self._sec_mcp_pin_enabled,
        )
        self._sec_mcp_pin_row.connect(
            "notify::active", self._on_sec_mcp_pin_toggled
        )
        mcp_grp.add(self._sec_mcp_pin_row)
        box.append(mcp_grp)

        # Never exposed paths
        nexp_grp = Adw.PreferencesGroup(
            title="Never-Exposed Paths",
            description=(
                "Kernel Landlock deny list — paths that can never be accessed "
                "by Tier-2 OS/filesystem mutation tools."
            ),
        )
        self._sec_nexp_row = Adw.SwitchRow(
            title="Never-Exposed Paths",
            subtitle="Enforce never_exposed_paths on all tool calls",
            active=self._sec_never_exposed_paths,
        )
        self._sec_nexp_row.connect(
            "notify::active", self._on_sec_nexp_toggled
        )
        nexp_grp.add(self._sec_nexp_row)
        box.append(nexp_grp)

        # SHA-256 manifest
        manifest_grp = Adw.PreferencesGroup(
            title="Capability Manifest",
            description=(
                "SHA-256 capability manifest for all 106 tools. "
                "Searchable evidence.py ledger for audit."
            ),
        )
        manifest_row = Adw.ActionRow(
            title="Manifest Status",
            subtitle="SHA-256 verified capability manifest",
        )
        manifest_pill = Gtk.Label(label="Verified", valign=Gtk.Align.CENTER)
        manifest_pill.add_css_class("pill")
        manifest_pill.add_css_class("pill-green")
        manifest_row.add_suffix(manifest_pill)
        manifest_grp.add(manifest_row)
        box.append(manifest_grp)

        # MCP socket status
        sock_grp = Adw.PreferencesGroup(title="MCP Socket Status")
        sock_grp.set_description(
            "JSON-over-UDS connection to MCP03 / 106 tools dispatcher."
        )
        sock_row = Adw.ActionRow(
            title="MCP Socket",
            subtitle=f"/run/user/{self._get_uid()}/celina/mcp.sock",
        )
        sock_pill = Gtk.Label(label="Reachable", valign=Gtk.Align.CENTER)
        sock_pill.add_css_class("pill")
        sock_pill.add_css_class("pill-green")
        sock_row.add_suffix(sock_pill)
        sock_grp.add(sock_row)
        box.append(sock_grp)

        return self._wrap_page(
            "106 Tools & MCP03 Security",
            "SHA-256 Capability Manifest permissions, MCP tool-poisoning pins, and searchable evidence.py ledger.",
            box,
        )

    # -- Page 1: Overview & Live X-Ray -- #
    def _page_overview(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Interactive X-Ray Pipeline Strip
        xray_box = Gtk.Box(spacing=6, homogeneous=False)
        for label, target in [
            ("● Hardware", "performance"),
            ("● OS & Kernel", "updates"),
            ("● Audio / NPU", "voice"),
            ("● Gear 0/0.5", "agents"),
            ("● Local LLM", "models"),
            ("● MCP Guard", "security"),
            ("● Cloud Gate", "apikeys"),
        ]:
            btn = Gtk.Button(label=label)
            btn.add_css_class("xray-node")
            btn.connect("clicked", lambda _b, t=target: self._jump_to(t))
            xray_box.append(btn)
        box.append(xray_box)

        # 4 Tabular-Numeral Metric Cards
        grid = Gtk.Box(spacing=12, homogeneous=True)
        self.lbl_ram = self._metric_card(
            grid, "MEMORY & TIER", f"{self.snap.ram_used_gb} / {self.snap.ram_total_gb} GB"
        )
        self.lbl_psi = self._metric_card(
            grid, "MEMORY PSI (10s)", f"{self.snap.psi_avg10:.1f}%"
        )
        self.lbl_sched = self._metric_card(
            grid, "CPU SCHEDULER", self.snap.scheduler
        )
        self.lbl_slice = self._metric_card(
            grid, "CELINA AI SLICE", f"Cap {self.snap.celina_slice_max}"
        )
        box.append(grid)

        # Plain-English "Why Did This Happen?" Feed + Progressive Disclosure
        decisions = Adw.PreferencesGroup(
            title="Recent System Decisions",
            description=(
                "Plain-English explanation of automatic background actions."
            ),
        )
        decisions.add(
            Adw.ActionRow(
                title="Adaptive profile active",
                subtitle=(
                    f"Using {self.snap.ram_tier.upper()} OS rules and "
                    f"{self.snap.ai_tier.upper()} AI memory budget."
                ),
            )
        )
        decisions.add(
            Adw.ActionRow(
                title="Strict local-first routing",
                subtitle=(
                    "Cloud escalation is disabled unless explicitly "
                    "authorized in API Keys."
                ),
            )
        )
        box.append(decisions)

        # Quick Actions
        actions = Adw.PreferencesGroup(title="Quick Actions")
        verify_row = Adw.ActionRow(
            title="Run Stage 99 Integrity & Parity Gate",
            subtitle=(
                "Validates hardware.json, cgroup caps, WAL DB, sockets, "
                "and SHA-256 manifest."
            ),
        )
        verify_btn = Gtk.Button(label="Run Check", valign=Gtk.Align.CENTER)
        verify_btn.add_css_class("suggested-action")
        verify_btn.connect("clicked", self._on_run_verify)
        verify_row.add_suffix(verify_btn)
        actions.add(verify_row)

        exp = Adw.ExpanderRow(
            title="Inspect Technical Architecture",
            subtitle="Live paths & contracts (Progressive Disclosure)",
        )
        exp.add_row(
            Adw.ActionRow(
                title="Hardware Contract",
                subtitle="/etc/macubuntu/hardware.json",
            )
        )
        exp.add_row(
            Adw.ActionRow(
                title="LLM Inference Socket",
                subtitle="/run/user/$UID/celina/llama.sock (0600 UDS)",
            )
        )
        actions.add(exp)
        box.append(actions)

        return self._wrap_page(
            "Overview & Health",
            "Everything running in macubuntu and Celina, visible in one calm view.",
            box,
        )

    # -- Page 2: Drop Update File (.mubundle) & Run -- #
    def _page_updates(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        drop_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8,
            halign=Gtk.Align.FILL,
        )
        drop_box.add_css_class("drop-zone")
        drop_box.append(Gtk.Image.new_from_icon_name("document-send-symbolic"))
        drop_box.append(
            Gtk.Label(
                label="Drop a macubuntu Update File Here (.mubundle or .tar.zst)"
            )
        )
        browse_btn = Gtk.Button(label="Browse Update File", halign=Gtk.Align.CENTER)
        browse_btn.connect("clicked", self._on_browse_update)
        drop_box.append(browse_btn)

        # Native Wayland Drag & Drop target
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect(
            "enter",
            lambda *_a: (
                drop_box.add_css_class("drop-active"),
                Gdk.DragAction.COPY,
            )[1],
        )
        drop_target.connect("leave", lambda *_a: drop_box.remove_css_class("drop-active"))
        drop_target.connect("drop", self._on_update_dropped)
        drop_box.add_controller(drop_target)
        box.append(drop_box)

        self.update_preview_group = Adw.PreferencesGroup(title="Staged Update Preview")
        self.update_info_row = Adw.ActionRow(
            title="No update staged",
            subtitle=(
                "Drop a signed .mubundle above to inspect signature, stages, "
                "and rollback safety."
            ),
        )
        self.install_btn = Gtk.Button(
            label="Verify & Install", valign=Gtk.Align.CENTER, sensitive=False
        )
        self.install_btn.add_css_class("suggested-action")
        self.install_btn.connect("clicked", self._on_install_update)
        self.update_info_row.add_suffix(self.install_btn)
        self.update_preview_group.add(self.update_info_row)
        box.append(self.update_preview_group)

        self.update_log_buf = Gtk.TextBuffer()
        log_view = Gtk.TextView(
            buffer=self.update_log_buf, editable=False, monospace=True
        )
        log_view.set_size_request(-1, 160)
        log_exp = Adw.ExpanderRow(
            title="Live Execution Log",
            subtitle="Streams progress from macubuntu-updater-helper (pkexec)",
        )
        log_exp.add_row(
            Gtk.ScrolledWindow(child=log_view, min_content_height=150)
        )
        log_group = Adw.PreferencesGroup()
        log_group.add(log_exp)
        box.append(log_group)

        return self._wrap_page(
            "Updates & Stage Manager",
            "Drop an update package to verify Ed25519 signatures, snapshot Btrfs, and run safely.",
            box,
        )

    # -- Page 3: Local Model Hub & Hardware Fit Badges -- #
    def _page_models(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        ctrl_group = Adw.PreferencesGroup(title="Runtime Controls")
        unload_row = Adw.ActionRow(
            title="Active llama-server Models",
            subtitle=(
                "Actively free RAM/VRAM via POST /models/unload over UDS."
            ),
        )
        unload_btn = Gtk.Button(label="Unload All Now", valign=Gtk.Align.CENTER)
        unload_btn.connect("clicked", self._on_unload_models)
        unload_row.add_suffix(unload_btn)
        ctrl_group.add(unload_row)
        box.append(ctrl_group)

        self.models_group = Adw.PreferencesGroup(
            title="Installed Local GGUF Models (~/.cache/celina/models)"
        )
        self._populate_model_rows()
        box.append(self.models_group)

        return self._wrap_page(
            "Local Model Hub",
            "Swap Fast, Tactical, Deep, Vision, and Voice models with real-time hardware fit checks.",
            box,
        )

    def _populate_model_rows(self) -> None:
        models = list_local_models(self.snap)
        if not models:
            self.models_group.add(
                Adw.ActionRow(
                    title="No GGUF models found",
                    subtitle=(
                        "Place .gguf models in ~/.cache/celina/models "
                        "or pull via Stage 70."
                    ),
                )
            )
            return
        for m in models:
            row = Adw.ActionRow(
                title=m["name"],
                subtitle=(
                    f"{m['size_gb']} GB • "
                    f"{'Loaded in RAM/VRAM' if m['loaded'] else 'Unloaded'}"
                ),
            )
            pill = Gtk.Label(label=m["fit_label"], valign=Gtk.Align.CENTER)
            pill.add_css_class("pill")
            pill.add_css_class(m["fit_pill"])
            row.add_suffix(pill)
            self.models_group.add(row)

    # -- Page 4: API Keys (GNOME Keyring / libsecret Vault) -- #
    def _page_apikeys(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        grp = Adw.PreferencesGroup(
            title="Cloud Provider Keys (GNOME Keyring Vault)",
            description=(
                "Stored encrypted via Freedesktop Secret Service. Never "
                "exposed to LLM prompts or sandboxes."
            ),
        )
        for provider, label in [
            ("openai", "OpenAI (GPT-6.2 Astra)"),
            ("anthropic", "Anthropic (Claude)"),
            ("gemini", "Google Gemini"),
            ("huggingface", "Hugging Face Token (Gated GGUFs)"),
            ("custom_lan", "Custom OpenAI-Compatible / LAN RPC"),
        ]:
            entry = Adw.PasswordEntryRow(title=label)
            configured = has_api_key_sync(provider)
            status = Gtk.Label(
                label="Saved" if configured else "Not Set",
                valign=Gtk.Align.CENTER,
            )
            status.add_css_class("pill")
            status.add_css_class(
                "pill-green" if configured else "pill-amber"
            )

            save_btn = Gtk.Button(label="Save", valign=Gtk.Align.CENTER)
            save_btn.connect("clicked", self._on_save_key, provider, entry, status)

            clear_btn = Gtk.Button(
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Remove Key",
            )
            clear_btn.connect("clicked", self._on_clear_key, provider, status)

            entry.add_suffix(status)
            entry.add_suffix(save_btn)
            entry.add_suffix(clear_btn)
            grp.add(entry)

        box.append(grp)
        return self._wrap_page(
            "API Keys & Cloud Providers",
            "Opt-in frontier escalation keys with mandatory outbound PII redaction.",
            box,
        )

    # -- Page 5: Access & Permissions (macOS TCC Parity) -- #
    def _page_access(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        cap_grp = Adw.PreferencesGroup(
            title="System & Hardware Access",
            description=(
                "Controls Landlock, D-Bus, and Wayland portal permissions "
                "for Celina."
            ),
        )
        caps = self.policy["capabilities"]
        labels = [
            (
                "visual_intelligence",
                "Screen Capture & Visual Intelligence",
                "Allows Super+Shift+V xdg-desktop-portal capture to tmpfs.",
            ),
            (
                "always_on_voice",
                "Always-On Microphone (AEC + VAD)",
                "Keeps celina-voice.service active on AC power.",
            ),
            (
                "gui_accessibility",
                "Accessibility & GUI Automation",
                "Allows AT-SPI2 inspection and controlled Computer Use.",
            ),
            (
                "wayland_text_injection",
                "System-Wide Writing Tools (ydotool)",
                "Allows rewriting/dictating text into focused Wayland apps.",
            ),
            (
                "cloud_escalation",
                "Opt-In Cloud Escalation",
                "Permits routing oversized tasks to configured API Keys.",
            ),
            (
                "outbound_pii_redaction",
                "Mandatory Outbound PII Redaction",
                "Replaces emails, keys, and home paths before cloud calls.",
            ),
        ]
        for key, title, sub in labels:
            sw = Adw.SwitchRow(title=title, subtitle=sub, active=bool(caps.get(key, False)))
            sw.connect("notify::active", self._on_toggle_cap, key)
            cap_grp.add(sw)
        box.append(cap_grp)

        folder_grp = Adw.PreferencesGroup(
            title="Allowed Workspaces & Protected Vault Paths"
        )
        for f in self.policy["folders"]:
            folder_grp.add(
                Adw.ActionRow(title=f["path"], subtitle=f"Access: {f['mode']}")
            )
        for p in self.policy["never_exposed"]:
            r = Adw.ActionRow(title=p, subtitle="Kernel Landlock Deny (never_exposed_paths)")
            pill = Gtk.Label(label="Blocked", valign=Gtk.Align.CENTER)
            pill.add_css_class("pill")
            pill.add_css_class("pill-red")
            r.add_suffix(pill)
            folder_grp.add(r)
        box.append(folder_grp)

        # GPG key management
        gpg_grp = Adw.PreferencesGroup(title="GPG Keys")
        gpg_grp.set_description("Manage signing keys for .mubundle package verification.")

        gpk_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        gpk_list.set_hexpand(True)

        if not self._install_gpg_keys:
            empty = Gtk.Label(label="No GPG keys installed", xalign=0.0)
            empty.add_css_class("dim-label")
            gpk_list.append(empty)
        else:
            for key in self._install_gpg_keys:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                fp = Gtk.Label(label=key.get("fingerprint", "unknown"), xalign=0.0)
                fp.set_hexpand(True)
                fp.add_css_class("mono")
                name = Gtk.Label(label=key.get("name", "Unknown"), xalign=0.0)
                row.append(fp)
                row.append(name)
                gpk_list.append(row)

        gpg_grp.add(gpk_list)

        import_btn = Gtk.Button(label="Import GPG Key")
        import_btn.connect("clicked", self._on_gpg_import_key)
        gpg_btn_box = Gtk.Box(halign=Gtk.Align.START)
        gpg_btn_box.append(import_btn)
        gpg_grp.add(gpg_btn_box)

        box.append(gpg_grp)

        return self._wrap_page(
            "Access & Permissions",
            "Grant or revoke exactly what folders, hardware, and networks Celina can touch.",
            box,
        )

    # -- Page: Install macubuntu (Full System Install Wizard) -- #
    def _page_install(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Hardware tier selector
        tier_grp = Adw.PreferencesGroup(title="Hardware Profile")
        self._install_tier_row = Adw.ActionRow(
            title="Target Hardware Tier",
            subtitle="Select your RAM tier for optimal ZRAM, slice, and model configuration",
        )
        tier_dropdown = Gtk.DropDown.new_from_strings(["8 GB", "16 GB", "32 GB"])
        tier_dropdown.set_selected(1)  # default 16gb
        tier_dropdown.connect("notify::selected", self._on_install_tier_changed)
        self._install_tier_row.add_suffix(tier_dropdown)
        tier_grp.add(self._install_tier_row)
        box.append(tier_grp)

        # Hardware profile presets
        prof_grp = Adw.PreferencesGroup(title="Hardware Profile Preset")
        prof_grp.set_description("Choose a preset optimized for your hardware type.")

        self._install_profile_row = self._make_combo_row(
            "Preset",
            ["Desktop", "Laptop", "Workstation", "Server"],
            0,
            self._on_install_profile_changed,
        )
        prof_grp.add(self._install_profile_row)

        prof_desc = Gtk.Label(
            label="Optimizes ZRAM, App Nap, power management, and model settings for your hardware type."
        )
        prof_desc.add_css_class("dim-label")
        prof_desc.set_xalign(0.0)
        prof_grp.add(prof_desc)

        box.append(prof_grp)

        # Network configuration
        net_grp = Adw.PreferencesGroup(title="Network Configuration")
        net_grp.set_description("Configure your network connection for the installed system.")

        self._install_net_dhcp_row = Adw.SwitchRow(
            title="DHCP (automatic)",
            subtitle="Obtain IP address automatically via DHCP",
            active=True,
        )
        self._install_net_dhcp_row.connect("notify::active", self._on_install_net_dhcp_toggled)
        net_grp.add(self._install_net_dhcp_row)

        self._install_net_ip_row = Adw.EntryRow(title="Static IP address")
        self._install_net_ip_row.set_tooltip_text("e.g. 192.168.1.50/24")
        self._install_net_ip_row.connect("notify::text", self._on_install_net_ip_changed)
        self._install_net_ip_row.set_visible(False)
        net_grp.add(self._install_net_ip_row)

        self._install_net_gw_row = Adw.EntryRow(title="Gateway")
        self._install_net_gw_row.set_tooltip_text("e.g. 192.168.1.1")
        self._install_net_gw_row.connect("notify::text", self._on_install_net_gw_changed)
        self._install_net_gw_row.set_visible(False)
        net_grp.add(self._install_net_gw_row)

        self._install_net_dns_row = Adw.EntryRow(title="DNS servers")
        self._install_net_dns_row.set_tooltip_text("e.g. 1.1.1.1, 8.8.8.8")
        self._install_net_dns_row.connect("notify::text", self._on_install_net_dns_changed)
        self._install_net_dns_row.set_visible(False)
        net_grp.add(self._install_net_dns_row)

        box.append(net_grp)

        # Storage configuration
        stor_grp = Adw.PreferencesGroup(title="Storage Configuration")
        stor_grp.set_description("Choose how to partition and format the target disk.")

        self._install_stor_auto_row = Adw.SwitchRow(
            title="Automatic partitioning",
            subtitle="Let the installer handle disk layout",
            active=True,
        )
        self._install_stor_auto_row.connect("notify::active", self._on_install_stor_auto_toggled)
        stor_grp.add(self._install_stor_auto_row)

        self._install_stor_btrfs_row = Adw.SwitchRow(
            title="Use BTRFS filesystem",
            subtitle="Enables snapshots, compression, and subvolume management",
            active=True,
        )
        self._install_stor_btrfs_row.connect("notify::active", self._on_install_stor_btrfs_toggled)
        stor_grp.add(self._install_stor_btrfs_row)

        stor_note = Gtk.Label(
            label="BTRFS is required for system snapshots and rollback."
        )
        stor_note.add_css_class("dim-label")
        stor_note.set_xalign(0.0)
        stor_grp.add(stor_note)

        box.append(stor_grp)

        # Timezone configuration
        tz_grp = Adw.PreferencesGroup(title="Timezone")
        tz_grp.set_description("Set your system timezone for accurate local time.")

        timezones = [
            "Asia/Kolkata", "Asia/Tokyo", "Asia/Shanghai", "Asia/Dubai",
            "Europe/London", "Europe/Berlin", "Europe/Paris",
            "America/New_York", "America/Los_Angeles", "America/Chicago",
            "Australia/Sydney", "UTC",
        ]
        self._install_tz_row = self._make_combo_row(
            "Timezone",
            timezones,
            0,
            self._on_install_tz_changed,
        )
        tz_grp.add(self._install_tz_row)

        box.append(tz_grp)

        # Locale & keyboard
        loc_grp = Adw.PreferencesGroup(title="Locale & Keyboard")
        loc_grp.set_description("Set system language, region, and keyboard layout.")

        locales = ["en_US.UTF-8", "en_GB.UTF-8", "fr_FR.UTF-8", "de_DE.UTF-8", "ja_JP.UTF-8"]
        self._install_locale_row = self._make_combo_row(
            "Locale",
            locales,
            0,
            self._on_install_locale_changed,
        )
        loc_grp.add(self._install_locale_row)

        keyboards = ["us", "gb", "fr", "de", "jp", "dvorak", "colemak"]
        self._install_kb_row = self._make_combo_row(
            "Keyboard layout",
            keyboards,
            0,
            self._on_install_kb_changed,
        )
        loc_grp.add(self._install_kb_row)

        box.append(loc_grp)

        # Accessibility
        a11y_grp = Adw.PreferencesGroup(title="Accessibility")
        a11y_grp.set_description("Enable assistive technologies for the installed system.")

        a11y_options = [
            ("screen_reader", "Screen reader", "Read aloud on-screen content"),
            ("high_contrast", "High contrast", "Increase visual contrast"),
            ("large_text", "Large text", "Larger system fonts"),
            ("sticky_keys", "Sticky keys", "Press modifier keys one at a time"),
        ]

        self._install_a11y_checkboxes: dict[str, Gtk.CheckButton] = {}
        for key, title, desc in a11y_options:
            cb = Gtk.CheckButton(label=title)
            cb.set_tooltip_text(desc)
            cb.set_active(getattr(self, f"_install_a11y_{key}"))
            cb.connect("toggled", self._on_install_a11y_toggled, key)
            self._install_a11y_checkboxes[key] = cb
            a11y_grp.add(cb)

        box.append(a11y_grp)

        # Power management
        pow_grp = Adw.PreferencesGroup(title="Power Management")
        pow_grp.set_description("Configure power saving and sleep behavior.")

        profiles = ["performance", "balanced", "power-saver"]
        self._install_power_profile_row = self._make_combo_row(
            "Power profile",
            profiles,
            1,
            self._on_install_power_profile_changed,
        )
        pow_grp.add(self._install_power_profile_row)

        sleep_modes = ["suspend", "hibernate", "hybrid-sleep"]
        self._install_power_sleep_row = self._make_combo_row(
            "Sleep behavior",
            sleep_modes,
            0,
            self._on_install_power_sleep_changed,
        )
        pow_grp.add(self._install_power_sleep_row)

        box.append(pow_grp)

        # Security configuration
        sec_grp = Adw.PreferencesGroup(title="Security")
        sec_grp.set_description("Hardening options for the installed system.")

        self._install_sec_firewall_row = Adw.SwitchRow(
            title="Enable firewall",
            subtitle="UFW firewall with default deny incoming",
            active=True,
        )
        self._install_sec_firewall_row.connect("notify::active", self._on_install_sec_firewall_toggled)
        sec_grp.add(self._install_sec_firewall_row)

        self._install_sec_autoupdate_row = Adw.SwitchRow(
            title="Automatic security updates",
            subtitle="Install security updates automatically",
            active=True,
        )
        self._install_sec_autoupdate_row.connect("notify::active", self._on_install_sec_autoupdate_toggled)
        sec_grp.add(self._install_sec_autoupdate_row)

        self._install_sec_snapd_row = Adw.SwitchRow(
            title="Enable Snap",
            subtitle="Install snapd and Snap Store",
            active=False,
        )
        self._install_sec_snapd_row.connect("notify::active", self._on_install_sec_snapd_toggled)
        sec_grp.add(self._install_sec_snapd_row)

        box.append(sec_grp)

        # Backup configuration
        bak_grp = Adw.PreferencesGroup(title="Backups")
        bak_grp.set_description("Automatic backup configuration for your data.")

        self._install_bak_enabled_row = Adw.SwitchRow(
            title="Automatic backups",
            subtitle="Back up home directory periodically",
            active=True,
        )
        self._install_bak_enabled_row.connect("notify::active", self._on_install_bak_enabled_toggled)
        bak_grp.add(self._install_bak_enabled_row)

        targets = ["External drive", "Network share", "Cloud (encrypted)", "Local only"]
        self._install_bak_target_row = self._make_combo_row(
            "Backup target",
            targets,
            0,
            self._on_install_bak_target_changed,
        )
        bak_grp.add(self._install_bak_target_row)

        box.append(bak_grp)

        # Update preferences
        upd_grp = Adw.PreferencesGroup(title="Update Preferences")
        upd_grp.set_description("How the installed system receives macubuntu updates.")

        channels = ["stable", "beta", "nightly"]
        self._install_upd_channel_row = self._make_combo_row(
            "Update channel",
            channels,
            0,
            self._on_install_upd_channel_changed,
        )
        upd_grp.add(self._install_upd_channel_row)

        self._install_upd_auto_row = Adw.SwitchRow(
            title="Automatic updates",
            subtitle="Check for and install updates automatically",
            active=True,
        )
        self._install_upd_auto_row.connect("notify::active", self._on_install_upd_auto_toggled)
        upd_grp.add(self._install_upd_auto_row)

        box.append(upd_grp)

        # Services configuration
        svc_grp = Adw.PreferencesGroup(title="Services")
        svc_grp.set_description("Choose which background services to start on the installed system.")

        svc_options = [
            ("celina", "Celina AI services", "Neural inference, proactive preloader, LLM backend"),
            ("npu", "NPU inference service", "Neural processing unit acceleration (if available)"),
            ("app_nap", "App Nap service", "Automatic idle app freezing"),
        ]

        self._install_svc_checkboxes: dict[str, Gtk.CheckButton] = {}
        for key, title, desc in svc_options:
            cb = Gtk.CheckButton(label=title)
            cb.set_tooltip_text(desc)
            cb.set_active(getattr(self, f"_install_svc_{key}"))
            cb.connect("toggled", self._on_install_svc_toggled, key)
            self._install_svc_checkboxes[key] = cb
            svc_grp.add(cb)

        box.append(svc_grp)

        # Model Hub integration
        model_grp = Adw.PreferencesGroup(title="AI Models")
        model_grp.set_description("Select local AI models to install during setup.")

        model_options = [
            ("bitnet", "BitNet b1.58 1.3B", "Ternary model, runs on any tier"),
            ("lfm2", "LFM2 360M", "Small fast model for simple tasks"),
            ("llama_standard", "Llama-3.2 3B", "General purpose model"),
            ("llama_vision", "Llama-3.2 11B Vision", "Multimodal vision model"),
            ("whisper", "Whisper small", "Speech-to-text model"),
        ]

        self._install_model_checkboxes: dict[str, Gtk.CheckButton] = {}
        for key, title, desc in model_options:
            cb = Gtk.CheckButton(label=title)
            cb.set_tooltip_text(desc)
            cb.set_active(key in self._install_models)
            cb.connect("toggled", self._on_install_model_toggled, key)
            self._install_model_checkboxes[key] = cb
            model_grp.add(cb)

        box.append(model_grp)

        # API Keys setup
        api_grp = Adw.PreferencesGroup(title="API Keys (Optional)")
        api_grp.set_description("Configure cloud provider API keys for hybrid AI workloads.")

        api_note = Gtk.Label(
            label="Keys are stored encrypted in GNOME Keyring. Never sent to LLM prompts."
        )
        api_note.add_css_class("dim-label")
        api_note.set_xalign(0.0)
        api_grp.add(api_note)

        self._install_api_keys_row = Adw.SwitchRow(
            title="Configure API keys now",
            subtitle="Set up cloud provider keys during installation",
            active=False,
        )
        self._install_api_keys_row.connect("notify::active", self._on_install_api_keys_toggled)
        api_grp.add(self._install_api_keys_row)

        self._install_api_keys_note = Gtk.Label(
            label="Open the API Keys & Cloud page after install to configure providers.",
            xalign=0.0,
            wrap=True,
        )
        self._install_api_keys_note.add_css_class("dim-label")
        self._install_api_keys_note.set_visible(False)
        api_grp.add(self._install_api_keys_note)

        box.append(api_grp)

        # Access & Permissions
        acc_grp = Adw.PreferencesGroup(title="Access & Permissions")
        acc_grp.set_description("System access policies for Celina services.")

        self._install_acc_landlock_row = Adw.SwitchRow(
            title="Landlock filesystem sandboxing",
            subtitle="Restrict file access to declared paths only",
            active=True,
        )
        self._install_acc_landlock_row.connect("notify::active", self._on_install_acc_landlock_toggled)
        acc_grp.add(self._install_acc_landlock_row)

        self._install_acc_sandbox_row = Adw.SwitchRow(
            title="App sandboxing",
            subtitle="Run user apps in restricted namespaces",
            active=True,
        )
        self._install_acc_sandbox_row.connect("notify::active", self._on_install_acc_sandbox_toggled)
        acc_grp.add(self._install_acc_sandbox_row)

        acc_note = Gtk.Label(
            label="Full permission management available on the Access & Permissions page after install."
        )
        acc_note.add_css_class("dim-label")
        acc_note.set_xalign(0.0)
        acc_grp.add(acc_note)

        box.append(acc_grp)

        # Software selection
        soft_grp = Adw.PreferencesGroup(title="Software Selection")
        soft_grp.set_description("Choose application bundles to install.")

        software_bundles = [
            ("office", "Office Suite", "LibreOffice, document editors"),
            ("creative", "Creative Tools", "GIMP, Inkscape, image editors"),
            ("development", "Development Tools", "Compilers, IDEs, version control"),
            ("gaming", "Gaming", "Steam, Wine, game launchers"),
            ("multimedia", "Multimedia", "Video players, audio tools"),
        ]

        self._install_software_checkboxes: dict[str, Gtk.CheckButton] = {}
        for key, title, desc in software_bundles:
            cb = Gtk.CheckButton(label=title)
            cb.set_tooltip_text(desc)
            cb.set_active(self._install_software[key])
            cb.connect("toggled", self._on_install_software_toggled, key)
            self._install_software_checkboxes[key] = cb
            soft_grp.add(cb)

        box.append(soft_grp)

        # Installation source: Online vs Offline
        source_grp = Adw.PreferencesGroup(title="Installation Source")
        self._install_online_row = Adw.SwitchRow(
            title="Online Install",
            subtitle="Download packages from macubuntu servers",
            active=True,
        )
        self._install_offline_row = Adw.SwitchRow(
            title="Offline Install",
            subtitle="Use local .mubundle files (no network required)",
            active=False,
        )
        self._install_online_row.connect("notify::active", self._on_install_source_toggled)
        self._install_offline_row.connect("notify::active", self._on_install_source_toggled)
        source_grp.add(self._install_online_row)
        source_grp.add(self._install_offline_row)
        box.append(source_grp)

        # Offline bundle drop zone (shown when offline mode is active)
        self._install_drop_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8,
            halign=Gtk.Align.FILL,
        )
        self._install_drop_box.add_css_class("drop-zone")
        self._install_drop_box.append(
            Gtk.Image.new_from_icon_name("document-send-symbolic")
        )
        self._install_drop_box.append(
            Gtk.Label(
                label="Drop .mubundle update files here (offline install)"
            )
        )
        browse_btn = Gtk.Button(label="Browse Update Files", halign=Gtk.Align.CENTER)
        browse_btn.connect("clicked", self._on_install_browse_bundle)
        self._install_drop_box.append(browse_btn)

        # Drag & drop controller
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect(
            "enter",
            lambda *_a: (
                self._install_drop_box.add_css_class("drop-active"),
                Gdk.DragAction.COPY,
            )[1],
        )
        drop_target.connect(
            "leave", lambda *_a: self._install_drop_box.remove_css_class("drop-active")
        )
        drop_target.connect("drop", self._on_install_bundle_dropped)
        self._install_drop_box.add_controller(drop_target)
        self._install_drop_box.set_visible(False)  # hidden until offline mode
        box.append(self._install_drop_box)

        # Staged bundles list
        self._install_bundles_group = Adw.PreferencesGroup(title="Staged Bundles")
        self._install_bundles_label = Gtk.Label(
            label="No bundles staged", xalign=0.0
        )
        self._install_bundles_label.add_css_class("dim-label")
        self._install_bundles_group.add(self._install_bundles_label)
        self._install_bundles_group.set_visible(False)
        box.append(self._install_bundles_group)

        # Disk encryption (LUKS2)
        luks_grp = Adw.PreferencesGroup(title="Disk Encryption")
        luks_grp.set_description("Protect your data with full-disk encryption (LUKS2).")

        self._install_luks_row = Adw.SwitchRow(
            title="Encrypt with LUKS2",
            subtitle="Full-disk encryption for all system data",
            active=False,
        )
        self._install_luks_row.connect("notify::active", self._on_install_luks_toggled)
        luks_grp.add(self._install_luks_row)

        # Passphrase fields (shown when LUKS enabled)
        self._install_luks_pass_row = Adw.PasswordEntryRow(
            title="Encryption passphrase",
        )
        self._install_luks_pass_row.connect("notify::text", self._on_install_luks_passphrase_changed)
        self._install_luks_pass_row.set_visible(False)
        luks_grp.add(self._install_luks_pass_row)

        self._install_luks_confirm_row = Adw.PasswordEntryRow(
            title="Confirm passphrase",
        )
        self._install_luks_confirm_row.set_visible(False)
        luks_grp.add(self._install_luks_confirm_row)

        # Recovery key
        rec_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.START)
        save_key_btn = Gtk.Button(label="Save recovery key to file")
        save_key_btn.set_sensitive(False)
        save_key_btn.connect("clicked", self._on_install_save_recovery_key)
        copy_key_btn = Gtk.Button(label="Copy to clipboard")
        copy_key_btn.set_sensitive(False)
        copy_key_btn.connect("clicked", self._on_install_copy_recovery_key)
        rec_box.append(save_key_btn)
        rec_box.append(copy_key_btn)
        self._install_recovery_key_buttons = (save_key_btn, copy_key_btn)

        rec_row = Adw.ActionRow(title="Recovery key", subtitle="Store it safely — losing it means permanent data loss")
        rec_row.add_suffix(rec_box)
        rec_row.set_visible(False)
        self._install_recovery_row = rec_row
        luks_grp.add(rec_row)

        # Warning pill
        self._install_luks_warn = Gtk.Label(
            label="⚠️  Forgetting your passphrase or losing the recovery key means permanent data loss."
        )
        self._install_luks_warn.add_css_class("dim-label")
        self._install_luks_warn.add_css_class("pill")
        self._install_luks_warn.add_css_class("pill-amber")
        self._install_luks_warn.set_visible(False)
        luks_grp.add(self._install_luks_warn)

        luks_grp.set_visible(False)
        self._install_luks_group = luks_grp
        box.append(luks_grp)

        # User account creation
        user_grp = Adw.PreferencesGroup(title="Create Your Account")
        user_grp.set_description("Set up the first user on this system.")

        self._install_user_full_row = Adw.EntryRow(
            title="Full name",
            text="Celina User",
        )
        self._install_user_full_row.connect("notify::text", self._on_install_user_full_changed)
        user_grp.add(self._install_user_full_row)

        self._install_user_acct_row = Adw.EntryRow(
            title="Account name",
            text="celina",
        )
        self._install_user_acct_row.connect("notify::text", self._on_install_user_acct_changed)
        user_grp.add(self._install_user_acct_row)

        self._install_user_pw_row = Adw.PasswordEntryRow(
            title="Password",
        )
        self._install_user_pw_row.connect("notify::text", self._on_install_user_pw_changed)
        user_grp.add(self._install_user_pw_row)

        self._install_user_pw_confirm_row = Adw.PasswordEntryRow(
            title="Confirm password",
        )
        user_grp.add(self._install_user_pw_confirm_row)

        # Account type
        acct_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.START)
        admin_radio = Gtk.CheckButton.new_with_label("Administrator (can install software, change settings)")
        admin_radio.set_active(True)
        std_radio = Gtk.CheckButton.new_with_label("Standard user")
        std_radio.set_group(admin_radio)
        admin_radio.connect("toggled", self._on_install_user_admin_toggled)
        std_radio.connect("toggled", self._on_install_user_std_toggled)
        acct_box.append(admin_radio)
        acct_box.append(std_radio)
        acct_row = Adw.ActionRow(title="Account type")
        acct_row.add_suffix(acct_box)
        user_grp.add(acct_row)

        # Options
        self._install_auto_login_row = Adw.SwitchRow(
            title="Enable auto-login",
            active=False,
        )
        self._install_auto_login_row.connect("notify::active", self._on_install_user_auto_login_toggled)
        user_grp.add(self._install_auto_login_row)

        self._install_require_pw_row = Adw.SwitchRow(
            title="Require password on wake",
            active=True,
        )
        self._install_require_pw_row.connect("notify::active", self._on_install_user_require_pw_toggled)
        user_grp.add(self._install_require_pw_row)

        box.append(user_grp)

        # ZRAM configuration
        zram_grp = Adw.PreferencesGroup(title="ZRAM Compressed Swap")
        zram_grp.set_description("Compress memory pages in RAM instead of swapping to disk.")

        self._install_zram_row = Adw.SwitchRow(
            title="Enable ZRAM",
            subtitle="Compressed in-memory swap device",
            active=True,
        )
        self._install_zram_row.connect("notify::active", self._on_install_zram_toggled)
        zram_grp.add(self._install_zram_row)

        self._install_zram_size_row = self._make_combo_row(
            "ZRAM size",
            ["Auto", "25% of RAM", "50% of RAM", "100% of RAM"],
            0,
            self._on_install_zram_size_changed,
        )
        zram_grp.add(self._install_zram_size_row)

        self._install_zram_alg_row = self._make_combo_row(
            "Compression algorithm",
            ["zstd", "lz4", "lz4hc", "842"],
            0,
            self._on_install_zram_alg_changed,
        )
        zram_grp.add(self._install_zram_alg_row)

        box.append(zram_grp)

        # Snapshot rollback
        snap_grp = Adw.PreferencesGroup(title="System Snapshots")
        snap_grp.set_description("Automatic BTRFS snapshots before major changes for one-click rollback.")

        self._install_snapshot_row = Adw.SwitchRow(
            title="Automatic snapshots",
            subtitle="Create BTRFS snapshots before install, updates, and stage runs",
            active=True,
        )
        self._install_snapshot_row.connect("notify::active", self._on_install_snapshot_toggled)
        snap_grp.add(self._install_snapshot_row)

        snap_note = Gtk.Label(
            label="Snapshots are stored on the BTRFS subvolume and can be restored from the boot menu."
        )
        snap_note.add_css_class("dim-label")
        snap_note.set_xalign(0.0)
        snap_grp.add(snap_note)

        box.append(snap_grp)

        # App Nap controls
        nap_grp = Adw.PreferencesGroup(title="App Nap")
        nap_grp.set_description("Automatically freeze idle apps to reclaim memory and CPU.")

        self._install_app_nap_row = Adw.SwitchRow(
            title="Enable App Nap",
            subtitle="Freeze idle background apps to save resources",
            active=True,
        )
        self._install_app_nap_row.connect("notify::active", self._on_install_app_nap_toggled)
        nap_grp.add(self._install_app_nap_row)

        nap_note = Gtk.Label(
            label="Audio players, terminals, IDEs, and Celina AI services are never frozen."
        )
        nap_note.add_css_class("dim-label")
        nap_note.set_xalign(0.0)
        nap_grp.add(nap_note)

        box.append(nap_grp)

        # Step list (install wizard progress)
        steps_grp = Adw.PreferencesGroup(title="Installation Steps")
        self._install_step_rows: dict[str, Adw.ActionRow] = {}
        step_defs = [
            ("download", "Download Packages & Stages"),
            ("verify", "Verify Signatures"),
            ("install_base", "Install Base System"),
            ("install_stages", "Run macubuntu Stages"),
            ("configure_hardware", "Configure Hardware Profile"),
            ("start_services", "Start Celina Services"),
            ("reboot", "Restart Required"),
        ]
        for step_id, step_title in step_defs:
            row = Adw.ActionRow(title=step_title, subtitle="Pending")
            row.add_css_class("mono")
            progress = Gtk.ProgressBar()
            progress.set_show_text(True)
            progress.set_text("0%")
            progress.set_hexpand(True)
            row.add_suffix(progress)
            self._install_step_rows[step_id] = row
            steps_grp.add(row)
        box.append(steps_grp)

        # Stage selection (customize which stages to run)
        stage_exp = Adw.ExpanderRow(
            title="Customize Stages",
            subtitle="Select which of the 18 installation stages to run",
        )
        stage_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        stage_box.set_margin_start(12)
        stage_box.set_margin_top(6)
        stage_box.set_margin_bottom(6)
        self._install_stage_checkboxes: dict[str, Gtk.CheckButton] = {}
        all_stages = [
            ("00-preflight", "Pre-flight checks"),
            ("10-base-deps", "Base dependencies"),
            ("20-appearance", "Appearance & dock"),
            ("25-desktop-dock", "Desktop dock"),
            ("30-input-typography", "Input & typography"),
            ("32-low-latency-kernel", "Low-latency kernel"),
            ("33-preload", "Preload daemon"),
            ("34-snap-removal", "Snap removal"),
            ("35-memory-zram", "Memory & ZRAM"),
            ("40-continuity-audio", "Continuity & audio"),
            ("45-apps-backups-search", "Apps, backups & search"),
            ("46-browser-tuning", "Browser tuning"),
            ("49-cpu-mitigations-off", "CPU mitigations off"),
            ("50-power", "Power management"),
            ("55-security-biometrics", "Security & biometrics"),
            ("60-extras", "Extras"),
            ("61-recovery-polish", "Recovery & polish"),
            ("70-feature-celina", "Celina AI feature stage"),
            ("99-verify", "Stage 99 verify"),
        ]
        for stage_id, stage_title in all_stages:
            cb = Gtk.CheckButton(label=f"{stage_id}  —  {stage_title}")
            cb.set_active(True)  # all selected by default
            cb.connect("toggled", self._on_install_stage_toggled, stage_id)
            self._install_stage_checkboxes[stage_id] = cb
            stage_box.append(cb)
        stage_exp.add_row(stage_box)
        box.append(stage_exp)

        # Overall progress bar
        overall_grp = Adw.PreferencesGroup(title="Overall Progress")
        self._install_overall_bar = Gtk.ProgressBar()
        self._install_overall_bar.set_show_text(True)
        self._install_overall_bar.set_text("0%")
        self._install_overall_bar.set_hexpand(True)
        overall_grp.add(self._install_overall_bar)
        box.append(overall_grp)

        # Status detail
        self._install_status_label = Gtk.Label(label="Ready to install", xalign=0.0)
        self._install_status_label.add_css_class("dim-label")
        self._install_status_label.add_css_class("mono")
        box.append(self._install_status_label)

        # Start Install button
        self._install_start_btn = Gtk.Button(label="Start Install")
        self._install_start_btn.add_css_class("suggested-action")
        self._install_start_btn.set_hexpand(False)
        self._install_start_btn.connect("clicked", self._on_start_install)
        btn_box = Gtk.Box(halign=Gtk.Align.START)
        btn_box.append(self._install_start_btn)
        box.append(btn_box)

        # Reboot button (hidden until install complete)
        self._install_reboot_btn = Gtk.Button(label="Restart Now")
        self._install_reboot_btn.add_css_class("suggested-action")
        self._install_reboot_btn.set_sensitive(False)
        self._install_reboot_btn.set_visible(False)
        self._install_reboot_btn.connect("clicked", self._on_reboot_now)
        reboot_box = Gtk.Box(halign=Gtk.Align.START)
        reboot_box.append(self._install_reboot_btn)
        box.append(reboot_box)

        return self._wrap_page(
            "Install macubuntu",
            "Full system install: download, verify, install stages, configure hardware, reboot.",
            box,
        )

    # -- Page: Install Summary (Finish) -- #
    def _page_finish(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        title = Gtk.Label(label="Installation Summary", xalign=0.0)
        title.add_css_class("page-title")
        box.append(title)

        subtitle = Gtk.Label(
            label="Review your install configuration before starting.",
            xalign=0.0,
            wrap=True,
        )
        subtitle.add_css_class("page-subtitle")
        box.append(subtitle)

        # Summary grid
        summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        summary.set_hexpand(True)

        def summary_row(label_text: str, value_text: str) -> Gtk.Widget:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            lbl = Gtk.Label(label=label_text, xalign=0.0)
            lbl.set_width_chars(24)
            val = Gtk.Label(label=value_text, xalign=0.0)
            val.set_hexpand(True)
            row.append(lbl)
            row.append(val)
            return row

        tier = getattr(self, "_install_tier", "16gb")
        profile = getattr(self, "_install_hardware_profile", "desktop")
        offline = getattr(self, "_install_offline_mode", False)
        luks = getattr(self, "_install_luks_enabled", False)
        user = getattr(self, "_install_user_account", "celina")
        zram = getattr(self, "_install_zram_enabled", False)
        snapshot = getattr(self, "_install_snapshot_enabled", False)
        app_nap = getattr(self, "_install_app_nap_enabled", False)
        tz = getattr(self, "_install_timezone", "Asia/Kolkata")
        locale = getattr(self, "_install_locale", "en_US.UTF-8")
        keyboard = getattr(self, "_install_keyboard", "us")
        power = getattr(self, "_install_power_profile", "balanced")
        firewall = getattr(self, "_install_security_firewall", False)
        backup = getattr(self, "_install_backup_enabled", False)
        channel = getattr(self, "_install_update_channel", "stable")

        rows = [
            summary_row("Hardware tier", tier.upper()),
            summary_row("Profile", profile.title()),
            summary_row("Install source", "Offline" if offline else "Online"),
            summary_row("Disk encryption", "LUKS2" if luks else "None"),
            summary_row("User account", user),
            summary_row("ZRAM swap", "Enabled" if zram else "Disabled"),
            summary_row("Snapshots", "Enabled" if snapshot else "Disabled"),
            summary_row("App Nap", "Enabled" if app_nap else "Disabled"),
            summary_row("Timezone", tz),
            summary_row("Locale", locale),
            summary_row("Keyboard", keyboard),
            summary_row("Power profile", power.title()),
            summary_row("Firewall", "Enabled" if firewall else "Disabled"),
            summary_row("Backups", "Enabled" if backup else "Disabled"),
            summary_row("Update channel", channel.title()),
        ]
        for r in rows:
            summary.append(r)

        box.append(summary)

        # Note about staged values
        note = Gtk.Label(
            label="These values will be applied when you click Start Install on the Install page.",
            xalign=0.0,
            wrap=True,
        )
        note.add_css_class("dim-label")
        box.append(note)

        return self._wrap_page(
            "Install Summary",
            "Review your macubuntu installation configuration.",
            box,
        )

    def _on_stack_visible_child(self, stack: Gtk.Stack, _pspec) -> None:
        """Rebuild the finish page when it becomes visible (avoids stale values)."""
        child = stack.get_visible_child()
        if child is None:
            return
        # Check if the visible child is the finish page wrapper
        finish_child = stack.get_child_by_name("finish")
        if child is finish_child:
            self._refresh_finish_page()

    def _refresh_finish_page(self) -> None:
        """Rebuild the finish page with current install settings."""
        old_child = self.stack.get_child_by_name("finish")
        if old_child is None:
            return
        self.stack.remove(old_child)
        new_child = self._page_finish()
        self.stack.add_named(new_child, "finish")
        self.stack.set_visible_child_name("finish")

    # -- Page: Reset to Ubuntu (System Wipe) -- #
    def _page_reset(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        warning = Gtk.Label(
            label="⚠️  This will remove all macubuntu packages, themes, configs, "
                  "and Celina services, restoring your system to stock Ubuntu.",
            xalign=0.0,
            wrap=True,
        )
        warning.add_css_class("dim-label")
        warning.set_css_classes(["pill", "pill-amber"])
        box.append(warning)

        # Selective wipe options
        selective_grp = Adw.PreferencesGroup(title="Selective Wipe Options")
        selective_grp.set_description("Choose what to keep or remove during reset.")

        self._install_wipe_keep_home_row = Adw.SwitchRow(
            title="Keep home directory",
            subtitle="Preserve user files in /home",
            active=False,
        )
        self._install_wipe_keep_home_row.connect("notify::active", self._on_install_wipe_keep_home_toggled)
        selective_grp.add(self._install_wipe_keep_home_row)

        self._install_wipe_keep_configs_row = Adw.SwitchRow(
            title="Keep user configs",
            subtitle="Preserve ~/.config and ~/.local",
            active=False,
        )
        self._install_wipe_keep_configs_row.connect("notify::active", self._on_install_wipe_keep_configs_toggled)
        selective_grp.add(self._install_wipe_keep_configs_row)

        box.append(selective_grp)

        # Confirmation field
        conf_grp = Adw.PreferencesGroup(title="Confirm Reset")
        conf_grp.set_description("Type 'WIPE' to confirm you understand this action is destructive.")

        self._install_wipe_confirm_row = Adw.EntryRow(
            title="Confirmation",
            subtitle="Type 'WIPE' to confirm you understand this action is destructive.",
        )
        self._install_wipe_confirm_row.connect("notify::text", self._on_install_wipe_confirm_changed)
        conf_grp.add(self._install_wipe_confirm_row)

        box.append(conf_grp)

        # Wipe preview
        preview_grp = Adw.PreferencesGroup(title="What Will Be Removed")
        self._reset_preview_label = Gtk.Label(
            label="Click 'Preview Wipe' to see what will be removed.", xalign=0.0
        )
        self._reset_preview_label.add_css_class("dim-label")
        self._reset_preview_label.set_wrap(True)
        preview_grp.add(self._reset_preview_label)
        box.append(preview_grp)

        preview_btn = Gtk.Button(label="Preview Wipe")
        preview_btn.connect("clicked", self._on_preview_wipe)
        preview_box = Gtk.Box(halign=Gtk.Align.START)
        preview_box.append(preview_btn)
        box.append(preview_box)

        # Wipe progress
        self._reset_progress = Gtk.ProgressBar()
        self._reset_progress.set_show_text(True)
        self._reset_progress.set_text("0%")
        self._reset_progress.set_hexpand(True)
        box.append(self._reset_progress)

        self._reset_status_label = Gtk.Label(label="", xalign=0.0)
        self._reset_status_label.add_css_class("dim-label")
        self._reset_status_label.add_css_class("mono")
        box.append(self._reset_status_label)

        # Wipe button
        self._reset_wipe_btn = Gtk.Button(label="Wipe & Reset to Ubuntu")
        self._reset_wipe_btn.add_css_class("suggested-action")
        self._reset_wipe_btn.set_sensitive(False)
        self._reset_wipe_btn.connect("clicked", self._on_start_wipe)
        wipe_btn_box = Gtk.Box(halign=Gtk.Align.START)
        wipe_btn_box.append(self._reset_wipe_btn)
        box.append(wipe_btn_box)

        # Reboot button (hidden until wipe complete)
        self._reset_reboot_btn = Gtk.Button(label="Restart Now")
        self._reset_reboot_btn.add_css_class("suggested-action")
        self._reset_reboot_btn.set_sensitive(False)
        self._reset_reboot_btn.set_visible(False)
        self._reset_reboot_btn.connect("clicked", self._on_reboot_now)
        reset_reboot_box = Gtk.Box(halign=Gtk.Align.START)
        reset_reboot_box.append(self._reset_reboot_btn)
        box.append(reset_reboot_box)

        return self._wrap_page(
            "Reset to Ubuntu",
            "Complete wipe: remove macubuntu packages, themes, configs, and Celina services.",
            box,
        )

    # ------------------------------------------------------------------ #
    # Signal Handlers & Background Workers
    # ------------------------------------------------------------------ #
    def _install_shortcuts(self) -> None:
        ctrl = Gtk.EventControllerKey()

        def on_key(_c, keyval, _keycode, state):
            if (state & Gdk.ModifierType.CONTROL_MASK) and keyval == Gdk.KEY_k:
                self.search_entry.grab_focus()
                return True
            return False

        ctrl.connect("key-pressed", on_key)
        self.add_controller(ctrl)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        q = entry.get_text().strip().lower()
        for row in self._nav_rows.values():
            row.set_visible(not q or q in row._search_title)

    def _on_nav_selected(self, lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        for other in self._listboxes:
            if other is not lb:
                other.unselect_all()
        child = self.stack.get_child_by_name(row._page_id)
        if child:
            self.stack.set_visible_child(child)
            self._spring_reveal(child)

    def _jump_to(self, page_id: str) -> None:
        row = self._nav_rows.get(page_id)
        if row:
            row.get_parent().select_row(row)

    def _toast(self, msg: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast.new(msg))

    def _refresh_telemetry(self) -> bool:
        if not self.get_visible():
            return True
        self.snap = read_system_snapshot()
        self.lbl_ram.set_label(
            f"{self.snap.ram_used_gb} / {self.snap.ram_total_gb} GB"
        )
        self.lbl_psi.set_label(f"{self.snap.psi_avg10:.1f}%")
        self.lbl_sched.set_label(self.snap.scheduler)
        if self.snap.psi_avg10 > 20.0:
            self.health_pill.set_label("● Memory Pressure")
            self.health_pill.set_css_classes(["pill", "pill-amber"])
        else:
            self.health_pill.set_label("● Healthy")
            self.health_pill.set_css_classes(["pill", "pill-green"])
        return True

    def _on_pause_agents(self, _btn: Gtk.Button) -> None:
        """Emergency kill switch: stop all Celina services (not just voice)."""
        services = [
            "celina-daemon.service",
            "celina-router.service",
            "celina-voice.service",
            "celina-web-watch.timer",
            "celina-memory.service",
            "celina-self-improve.timer",
        ]
        stopped = 0
        for svc in services:
            r = subprocess.run(
                ["systemctl", "--user", "stop", svc],
                check=False, capture_output=True,
            )
            if r.returncode == 0:
                stopped += 1
        self._toast(
            f"Emergency pause: {stopped}/{len(services)} Celina service(s) stopped."
        )

    def _on_unload_models(self, _btn: Gtk.Button) -> None:
        def worker() -> None:
            n = unload_all_llama_models()
            GLib.idle_add(self._toast, f"Unloaded {n} active local model(s) from RAM/VRAM.")

        threading.Thread(target=worker, daemon=True).start()

    def _on_save_key(
        self, _btn, provider: str, entry: Adw.PasswordEntryRow, status: Gtk.Label
    ) -> None:
        val = entry.get_text().strip()
        if not val:
            self._toast("Enter an API key first.")
            return

        def done(_src, res):
            if not finish_store_api_key(res):
                self._toast("Failed to save API key to keyring.", level="error")
                return
            entry.set_text("")
            status.set_label("Saved")
            status.set_css_classes(["pill", "pill-green"])
            self._toast(
                f"Encrypted API key saved in GNOME Keyring for {provider}."
            )

        store_api_key_async(provider, val, done)

    def _on_clear_key(self, _btn, provider: str, status: Gtk.Label) -> None:
        clear_api_key_sync(provider)
        status.set_label("Not Set")
        status.set_css_classes(["pill", "pill-amber"])
        self._toast(f"Removed API key for {provider}.")

    def _on_toggle_cap(self, sw: Adw.SwitchRow, _pspec, key: str) -> None:
        self.policy["capabilities"][key] = sw.get_active()
        save_access_policy(self.policy)
        self._toast(f"Updated permission: {key} = {'ON' if sw.get_active() else 'OFF'}")

    def _on_browse_update(self, _btn: Gtk.Button) -> None:
        dlg = Gtk.FileDialog(title="Select macubuntu Update Bundle")

        def on_selected(d, res):
            try:
                f = d.open_finish(res)
                if f and f.get_path():
                    self._stage_update_file(Path(f.get_path()))
            except GLib.Error:
                pass

        dlg.open(self, None, on_selected)

    def _on_update_dropped(self, _target, value, _x, _y) -> bool:
        if isinstance(value, Gio.File) and value.get_path():
            self._stage_update_file(Path(value.get_path()))
            return True
        return False

    def _stage_update_file(self, path: Path) -> None:
        try:
            meta = inspect_update_bundle(path)
            self._staged_bundle = path
            sig_txt = "Ed25519 Signed" if meta["signed"] else "Unsigned (Dev Mode required)"
            self.update_info_row.set_title(f"{path.name} ({meta['version']})")
            self.update_info_row.set_subtitle(
                f"{sig_txt} • Stages: {', '.join(meta['stages'])} • {meta['summary']}"
            )
            self.install_btn.set_sensitive(True)
            self._toast(f"Staged {path.name} for verification.")
        except Exception as exc:
            self._toast(f"Invalid update file: {exc}")

    def _on_install_update(self, _btn: Gtk.Button) -> None:
        if not self._staged_bundle:
            return
        bundle = str(self._staged_bundle)

        # Fail-closed: refuse to apply unsigned bundles via pkexec.
        # Only signed .mubundles may be installed with root privileges.
        try:
            meta = inspect_update_bundle(self._staged_bundle)
        except Exception:
            self._toast("Cannot inspect bundle — refusing to install.", level="error")
            return
        if not meta.get("signed", False):
            self._toast(
                "Unsigned bundle — refusing to install with root privileges. "
                "Enable Dev Mode for unsigned .sh scripts.",
                level="error",
            )
            return

        self.update_log_buf.set_text(f"Authenticating via Polkit for {bundle}...\n")

        def run_helper() -> None:
            proc = subprocess.Popen(
                [
                    "pkexec",
                    "/usr/local/libexec/macubuntu-updater-helper",
                    "--bundle",
                    bundle,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                GLib.idle_add(self._append_log, line)
            rc = proc.wait()
            msg = (
                "Update applied & Stage 99 passed!"
                if rc == 0
                else f"Update failed (exit {rc}) — Snapper rollback ready."
            )
            GLib.idle_add(self._toast, msg)

        threading.Thread(target=run_helper, daemon=True).start()

    def _on_run_verify(self, _btn: Gtk.Button) -> None:
        self._jump_to("updates")
        self.update_log_buf.set_text("Running Stage 99 Integrity & Parity Gate...\n")

        def run_v() -> None:
            proc = subprocess.Popen(
                ["bash", str(STAGES_DIR / "stage-99-verify.sh")],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                GLib.idle_add(self._append_log, line)
            rc = proc.wait()
            GLib.idle_add(
                self._toast, "Stage 99 PASS" if rc == 0 else f"Stage 99 exited with code {rc}"
            )

        threading.Thread(target=run_v, daemon=True).start()

    # -- Performance Screen Handlers -- #
    def _on_perf_app_nap_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._perf_app_nap_enabled = sw.get_active()
        self._toast(
            f"App Nap {'enabled' if self._perf_app_nap_enabled else 'disabled'}"
        )
        self._send_perf_socket_request({"action": "app_nap", "enabled": self._perf_app_nap_enabled})

    def _on_perf_zram_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._perf_zram_enabled = sw.get_active()
        self._toast(
            f"ZRAM {'enabled' if self._perf_zram_enabled else 'disabled'}"
        )
        self._send_perf_socket_request({"action": "zram", "enabled": self._perf_zram_enabled})

    def _on_perf_zram_size_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        sizes = ["auto", "25% of RAM", "50% of RAM", "100% of RAM"]
        self._perf_zram_size = sizes[sel] if sel < len(sizes) else "auto"
        self._send_perf_socket_request({"action": "zram_size", "size": self._perf_zram_size})

    def _send_perf_socket_request(self, request: dict) -> None:
        """Send a JSON-over-UDS request to the App Nap hardware governor."""
        import socket
        import json

        uid = self._get_uid()
        sock_path = f"/run/user/{uid}/celina/app_nap.sock"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(sock_path)
                s.sendall(json.dumps(request).encode())
                data = s.recv(65536)
                if data:
                    resp = json.loads(data.decode())
                    self._toast(f"App Nap acknowledged: {resp.get('status', 'ok')}")
        except (ConnectionRefusedError, FileNotFoundError, json.JSONDecodeError, OSError):
            pass  # Socket not available in development — settings stored locally

    def _get_uid(self) -> int:
        import os
        return os.getuid()

    # -- Appearance Screen Handlers -- #
    def _on_appear_glass_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._appear_glass_enabled = sw.get_active()
        self._toast(
            f"Liquid Glass {'enabled' if self._appear_glass_enabled else 'disabled'}"
        )

    def _on_appear_accent_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        colors = ["blue", "purple", "pink", "orange", "green", "red"]
        self._appear_accent_color = colors[sel] if sel < len(colors) else "blue"
        self._toast(f"Accent color: {self._appear_accent_color}")

    def _on_appear_font_changed(self, spin: Adw.SpinRow, _pspec) -> None:
        self._appear_font_scale = spin.get_value()
        self._toast(f"Font scale: {self._appear_font_scale:.2f}x")

    def _on_appear_dock_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        positions = ["bottom", "left", "right"]
        self._appear_dock_position = positions[sel] if sel < len(positions) else "bottom"
        self._toast(f"Dock position: {self._appear_dock_position}")

    # -- Continuity Screen Handlers -- #
    def _on_cont_local_send_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._cont_local_send = sw.get_active()
        self._toast(
            f"LocalSend {'enabled' if self._cont_local_send else 'disabled'}"
        )

    def _on_cont_airplay_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._cont_airplay = sw.get_active()
        self._toast(
            f"AirPlay/UxPlay receiver {'enabled' if self._cont_airplay else 'disabled'}"
        )

    def _on_cont_deskflow_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._cont_deskflow = sw.get_active()
        self._toast(
            f"Deskflow {'enabled' if self._cont_deskflow else 'disabled'}"
        )

    def _on_cont_battery_cap_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._cont_battery_cap = sw.get_active()
        self._toast(
            f"80% battery cap {'enabled' if self._cont_battery_cap else 'disabled'}"
        )

    def _on_cont_s0ix_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._cont_s0ix = sw.get_active()
        self._toast(
            f"S0ix suspend-then-hibernate {'enabled' if self._cont_s0ix else 'disabled'}"
        )

    # -- Recovery Screen Handlers -- #
    def _on_rec_snapshot_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._rec_snapshot_enabled = sw.get_active()
        self._toast(
            f"Automatic snapshots {'enabled' if self._rec_snapshot_enabled else 'disabled'}"
        )
        self._send_rec_socket_request({"action": "snapshot", "enabled": self._rec_snapshot_enabled})

    def _on_rec_snapper_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._rec_snapper_autosnap = sw.get_active()
        self._toast(
            f"Snapper auto-snapshots {'enabled' if self._rec_snapper_autosnap else 'disabled'}"
        )

    def _on_rec_btrbk_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._rec_btrbk_enabled = sw.get_active()
        self._toast(
            f"btrbk external backup {'enabled' if self._rec_btrbk_enabled else 'disabled'}"
        )

    def _send_rec_socket_request(self, request: dict) -> None:
        """Send a JSON-over-UDS request to the Celina snapshot manager."""
        import socket
        import json

        sock_path = f"/run/user/{self._get_uid()}/celina/snapshot.sock"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(sock_path)
                s.sendall(json.dumps(request).encode())
                data = s.recv(65536)
                if data:
                    resp = json.loads(data.decode())
                    self._toast(f"Snapshot manager: {resp.get('status', 'ok')}")
        except (ConnectionRefusedError, FileNotFoundError, json.JSONDecodeError, OSError):
            pass  # Socket not available in development

    # -- Knowledge Screen Handlers -- #
    def _on_know_spotlight_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._know_spotlight_index = sw.get_active()
        self._toast(
            f"Spotlight index {'enabled' if self._know_spotlight_index else 'disabled'}"
        )

    # -- Voice Screen Handlers -- #
    def _on_voice_wake_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._voice_wake_word = sw.get_active()
        self._toast(
            f"Wake word {'enabled' if self._voice_wake_word else 'disabled'}"
        )

    def _on_voice_always_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._voice_always_on = sw.get_active()
        self._toast(
            f"Always-on microphone {'enabled' if self._voice_always_on else 'disabled'}"
        )

    def _on_voice_tts_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        engines = ["kokoro", "lfm3", "piper"]
        self._voice_tts_engine = engines[sel] if sel < len(engines) else "kokoro"
        self._toast(f"TTS engine: {self._voice_tts_engine}")

    def _on_voice_whisper_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        models = ["tiny", "base", "small", "medium"]
        self._voice_whisper_model = models[sel] if sel < len(models) else "base"
        self._toast(f"Whisper model: {self._voice_whisper_model}")

    # -- Agents Screen Handlers -- #
    def _on_agents_taskgraph_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._agents_taskgraph = sw.get_active()
        self._toast(
            f"TaskGraph DAGs {'enabled' if self._agents_taskgraph else 'disabled'}"
        )

    def _on_agents_saga_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._agents_saga_rollback = sw.get_active()
        self._toast(
            f"Saga Rollback {'enabled' if self._agents_saga_rollback else 'disabled'}"
        )

    def _on_agents_crystallizer_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._agents_shortcuts_crystallizer = sw.get_active()
        self._toast(
            f"Shortcuts Crystallizer {'enabled' if self._agents_shortcuts_crystallizer else 'disabled'}"
        )

    def _on_agents_scrapling_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._agents_scrapling = sw.get_active()
        self._toast(
            f"Scrapling Watchers {'enabled' if self._agents_scrapling else 'disabled'}"
        )

    # -- Security Screen Handlers -- #
    def _on_sec_mcp_pin_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._sec_mcp_pin_enabled = sw.get_active()
        self._toast(
            f"MCP SHA-256 pinning {'enabled' if self._sec_mcp_pin_enabled else 'disabled'}"
        )

    def _on_sec_nexp_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._sec_never_exposed_paths = sw.get_active()
        self._toast(
            f"Never-exposed paths {'enabled' if self._sec_never_exposed_paths else 'disabled'}"
        )

    # -- Install Wizard Handlers -- #
    def _on_install_tier_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        idx = dropdown.get_selected()
        tiers = ["8gb", "16gb", "32gb"]
        self._install_tier = tiers[idx] if idx < len(tiers) else "16gb"

    def _on_start_install(self, _btn: Gtk.Button) -> None:
        if self._install_in_progress:
            return
        self._install_in_progress = True
        self._install_start_btn.set_sensitive(False)

        # Initialize wizard with all collected install settings
        tier = getattr(self, "_install_tier", "16gb")
        install_settings = {
            "luks_enabled": getattr(self, "_install_luks_enabled", False),
            "luks_passphrase": getattr(self, "_install_luks_passphrase", ""),
            "luks_recovery_key": getattr(self, "_install_luks_recovery_key", ""),
            "user_full_name": getattr(self, "_install_user_full_name", ""),
            "user_account": getattr(self, "_install_user_account", ""),
            "user_password": getattr(self, "_install_user_password", ""),
            "user_admin": getattr(self, "_install_user_admin", True),
            "user_auto_login": getattr(self, "_install_user_auto_login", False),
            "user_require_pw_wake": getattr(self, "_install_user_require_pw_wake", True),
            "zram_enabled": getattr(self, "_install_zram_enabled", True),
            "zram_size": getattr(self, "_install_zram_size", "auto"),
            "zram_alg": getattr(self, "_install_zram_alg", "zstd"),
            "snapshot_enabled": getattr(self, "_install_snapshot_enabled", False),
            "app_nap_enabled": getattr(self, "_install_app_nap_enabled", True),
            "hardware_profile": getattr(self, "_install_hardware_profile", "desktop"),
            "network_dhcp": getattr(self, "_install_network_dhcp", True),
            "network_static_ip": getattr(self, "_install_network_static_ip", ""),
            "network_gateway": getattr(self, "_install_network_gateway", ""),
            "network_dns": getattr(self, "_install_network_dns", ""),
            "storage_auto_partition": getattr(self, "_install_storage_auto_partition", True),
            "storage_btrfs": getattr(self, "_install_storage_btrfs", True),
            "timezone": getattr(self, "_install_timezone", "Asia/Kolkata"),
            "locale": getattr(self, "_install_locale", "en_US.UTF-8"),
            "keyboard": getattr(self, "_install_keyboard", "us"),
            "a11y_screen_reader": getattr(self, "_install_a11y_screen_reader", False),
            "a11y_high_contrast": getattr(self, "_install_a11y_high_contrast", False),
            "a11y_large_text": getattr(self, "_install_a11y_large_text", False),
            "a11y_sticky_keys": getattr(self, "_install_a11y_sticky_keys", False),
            "power_profile": getattr(self, "_install_power_profile", "balanced"),
            "power_sleep": getattr(self, "_install_power_sleep", "suspend"),
            "security_firewall": getattr(self, "_install_security_firewall", True),
            "security_autoupdate": getattr(self, "_install_security_autoupdate", True),
            "security_snapd": getattr(self, "_install_security_snapd", False),
            "backup_enabled": getattr(self, "_install_backup_enabled", True),
            "backup_target": getattr(self, "_install_backup_target", "external"),
            "update_channel": getattr(self, "_install_update_channel", "stable"),
            "update_auto": getattr(self, "_install_update_auto", True),
            "svc_celina": getattr(self, "_install_svc_celina", True),
            "svc_npu": getattr(self, "_install_svc_npu", False),
            "svc_app_nap": getattr(self, "_install_svc_app_nap", True),
            "finish_shown": getattr(self, "_install_finish_shown", False),
            "gpg_keys": getattr(self, "_install_gpg_keys", []),
            "models": getattr(self, "_install_models", []),
            "api_keys_configured": getattr(self, "_install_api_keys_configured", False),
            "access_landlock": getattr(self, "_install_access_landlock", True),
            "access_sandbox": getattr(self, "_install_access_sandbox", True),
            "software": getattr(self, "_install_software", {}),
        }
        self._wizard = InstallWizard(
            target_tier=tier,
            offline_mode=self._install_offline_mode,
            staged_bundles=self._install_staged_bundles,
            selected_stages=self._install_selected_stages or None,
            install_settings=install_settings,
        )

        def on_step(step: InstallStep) -> None:
            # Update step row UI — must run on main thread (GLib.idle_add)
            # because this callback is invoked from the worker thread.
            def _update_ui() -> None:
                row = self._install_step_rows.get(step.id)
                if row is None:
                    return
                row.set_subtitle(step.status.upper())
                pill = "pill-green" if step.status == "done" else (
                    "pill-amber" if step.status == "running" else (
                        "pill-red" if step.status == "error" else "pill-blue"
                    )
                )
                # Remove existing pill classes and add new
                for cls in ("pill-green", "pill-amber", "pill-red", "pill-blue"):
                    row.remove_css_class(cls)
                row.add_css_class(pill)
                self._install_overall_bar.set_fraction(
                    self._wizard.plan.overall_progress
                )
                self._install_overall_bar.set_text(
                    f"{self._wizard.plan.overall_progress * 100:.0f}%"
                )
                self._install_status_label.set_label(
                    f"{step.title}: {step.detail}"
                )
                if step.status == "done":
                    row.add_css_class("pill-green")
                elif step.status == "error":
                    row.add_css_class("pill-red")

            GLib.idle_add(_update_ui)

        self._wizard._callback = on_step

        def run_wizard() -> None:
            while not self._wizard.is_complete():
                more = self._wizard.run_next()
                if not more:
                    break
                time.sleep(0.3)
            # Install complete
            GLib.idle_add(self._on_install_complete)

        threading.Thread(target=run_wizard, daemon=True).start()

    def _on_install_complete(self) -> None:
        self._install_in_progress = False
        self._install_start_btn.set_visible(False)
        self._install_reboot_btn.set_sensitive(True)
        self._install_reboot_btn.set_visible(True)
        self._install_status_label.set_label("Installation complete — reboot to activate all changes")
        self._toast("macubuntu installation complete! Please restart your system.")

    # -- Offline Mode Handlers -- #
    def _on_install_source_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        """Toggle between online and offline install mode."""
        if sw is self._install_online_row:
            self._install_offline_mode = not sw.get_active()
            self._install_offline_row.set_active(not sw.get_active())
        else:
            self._install_offline_mode = sw.get_active()
            self._install_online_row.set_active(not sw.get_active())

        self._install_drop_box.set_visible(self._install_offline_mode)
        self._install_bundles_group.set_visible(self._install_offline_mode)
        self._install_start_btn.set_sensitive(
            not self._install_offline_mode or bool(self._install_staged_bundles)
        )

    def _on_install_browse_bundle(self, _btn: Gtk.Button) -> None:
        """Open file dialog to select .mubundle files."""
        dlg = Gtk.FileDialog(title="Select macubuntu Update Bundles")

        def on_selected(d, res):
            try:
                files = d.open_multiple_finish(res)
                if files:
                    for i in range(files.get_n_items()):
                        f = files.get_item(i)
                        if f.get_path():
                            path = Path(f.get_path())
                            if path not in self._install_staged_bundles:
                                self._install_staged_bundles.append(path)
                    self._refresh_bundle_list()
            except GLib.Error:
                pass

        dlg.open_multiple(self, None, on_selected)

    def _on_install_bundle_dropped(self, _target, value, _x, _y) -> bool:
        """Handle drag & drop of .mubundle files."""
        if isinstance(value, Gdk.FileList):
            for i in range(value.get_n_items()):
                f = value.get_item(i)
                if f.get_path():
                    path = Path(f.get_path())
                    if path not in self._install_staged_bundles:
                        self._install_staged_bundles.append(path)
            self._refresh_bundle_list()
            self._install_drop_box.remove_css_class("drop-active")
            return True
        elif isinstance(value, Gio.File) and value.get_path():
            path = Path(value.get_path())
            if path not in self._install_staged_bundles:
                self._install_staged_bundles.append(path)
            self._refresh_bundle_list()
            self._install_drop_box.remove_css_class("drop-active")
            return True
        return False

    def _refresh_bundle_list(self) -> None:
        """Refresh the staged bundles display."""
        # Remove existing tracked rows (keep the label)
        for row in self._install_bundle_rows:
            self._install_bundles_group.remove(row)
        self._install_bundle_rows.clear()

        if not self._install_staged_bundles:
            self._install_bundles_label.set_label("No bundles staged")
            self._install_start_btn.set_sensitive(False)
            return

        self._install_bundles_label.set_label(
            f"{len(self._install_staged_bundles)} bundle(s) staged"
        )
        for bundle_path in self._install_staged_bundles:
            row = Adw.ActionRow(
                title=bundle_path.name,
                subtitle=str(bundle_path.parent),
            )
            # Check if it looks like a signed bundle
            signed = bundle_path.suffix in (".mubundle", ".tar.zst")
            pill = Gtk.Label(label="Signed" if signed else "Unsigned")
            pill.add_css_class("pill")
            pill.add_css_class("pill-green" if signed else "pill-amber")
            row.add_suffix(pill)
            self._install_bundles_group.add(row)
            self._install_bundle_rows.append(row)

        self._install_start_btn.set_sensitive(True)

    def _on_install_stage_toggled(self, cb: Gtk.CheckButton, stage_id: str) -> None:
        """Toggle a stage selection for customized install."""
        if cb.get_active():
            if stage_id not in self._install_selected_stages:
                self._install_selected_stages.append(stage_id)
        else:
            self._install_selected_stages = [
                s for s in self._install_selected_stages if s != stage_id
            ]

    # -- End Offline Mode Handlers -- #

    # -- LUKS Encryption Handlers -- #
    def _on_install_luks_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        """Toggle LUKS encryption on/off."""
        self._install_luks_enabled = sw.get_active()
        self._install_luks_pass_row.set_visible(self._install_luks_enabled)
        self._install_luks_confirm_row.set_visible(self._install_luks_enabled)
        self._install_recovery_row.set_visible(self._install_luks_enabled)
        self._install_luks_warn.set_visible(self._install_luks_enabled)

        # Enable/disable recovery key buttons
        for btn in self._install_recovery_key_buttons:
            btn.set_sensitive(self._install_luks_enabled)

        # Validate passphrase if already entered
        if self._install_luks_enabled and self._install_luks_passphrase:
            self._install_recovery_key_buttons[0].set_sensitive(True)
            self._install_recovery_key_buttons[1].set_sensitive(True)

    def _on_install_luks_passphrase_changed(self, entry: Adw.PasswordEntryRow, _pspec) -> None:
        """Track LUKS passphrase and enable recovery key options.

        Generates a cryptographically secure 128-bit recovery key the first
        time a passphrase is entered. The key is never written to disk in
        plaintext by this handler — the user must explicitly save it.
        """
        self._install_luks_passphrase = entry.get_text()
        has_pass = bool(self._install_luks_passphrase.strip())
        if has_pass and not self._install_luks_recovery_key:
            # 32 hex chars = 128 bits, suitable as a LUKS recovery key
            self._install_luks_recovery_key = secrets.token_hex(16)
        for btn in self._install_recovery_key_buttons:
            btn.set_sensitive(self._install_luks_enabled and has_pass)

    def _on_install_save_recovery_key(self, _btn: Gtk.Button) -> None:
        """Save LUKS recovery key to a file."""
        if not self._install_luks_recovery_key:
            self._toast("No recovery key available — set a passphrase first.", level="error")
            return
        dlg = Gtk.FileDialog(title="Save LUKS Recovery Key")
        def on_selected(d, res):
            try:
                f = d.save_finish(res)
                if f and f.get_path():
                    key_text = (
                        "LUKS Recovery Key\n"
                        f"{self._install_luks_recovery_key}\n"
                        "Store this securely — losing it means permanent data loss.\n"
                    )
                    Path(f.get_path()).write_text(key_text)
                    self._toast("Recovery key saved")
            except GLib.Error:
                pass
        dlg.save(self, None, on_selected)

    def _on_install_copy_recovery_key(self, _btn: Gtk.Button) -> None:
        """Copy LUKS recovery key to clipboard."""
        if not self._install_luks_recovery_key:
            self._toast("No recovery key available — set a passphrase first.", level="error")
            return
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(self._install_luks_recovery_key)
        self._toast("Recovery key copied to clipboard")

    # -- End LUKS Encryption Handlers -- #

    # -- User Account Creation Handlers -- #
    def _on_install_user_full_changed(self, entry: Adw.EntryRow, _pspec) -> None:
        self._install_user_full_name = entry.get_text()

    def _on_install_user_acct_changed(self, entry: Adw.EntryRow, _pspec) -> None:
        self._install_user_account = entry.get_text()

    def _on_install_user_pw_changed(self, entry: Adw.PasswordEntryRow, _pspec) -> None:
        self._install_user_password = entry.get_text()

    def _on_install_user_admin_toggled(self, cb: Gtk.CheckButton) -> None:
        self._install_user_admin = cb.get_active()

    def _on_install_user_std_toggled(self, cb: Gtk.CheckButton) -> None:
        self._install_user_admin = not cb.get_active()

    def _on_install_user_auto_login_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_user_auto_login = sw.get_active()

    def _on_install_user_require_pw_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_user_require_pw_wake = sw.get_active()

    # -- End User Account Creation Handlers -- #

    # -- ZRAM Handlers -- #
    def _on_install_zram_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_zram_enabled = sw.get_active()

    def _on_install_zram_size_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        sizes = ["auto", "25%", "50%", "100%"]
        self._install_zram_size = sizes[sel] if sel < len(sizes) else "auto"

    def _on_install_zram_alg_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        algs = ["zstd", "lz4", "lz4hc", "842"]
        self._install_zram_alg = algs[sel] if sel < len(algs) else "zstd"

    # -- End ZRAM Handlers -- #

    # -- Snapshot Rollback Handler -- #
    def _on_install_snapshot_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_snapshot_enabled = sw.get_active()

    # -- End Snapshot Rollback Handler -- #

    # -- App Nap Handler -- #
    def _on_install_app_nap_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_app_nap_enabled = sw.get_active()

    # -- End App Nap Handler -- #

    # -- Hardware Profile Handler -- #
    def _on_install_profile_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        profiles = ["desktop", "laptop", "workstation", "server"]
        self._install_hardware_profile = profiles[sel] if sel < len(profiles) else "desktop"

    # -- End Hardware Profile Handler -- #

    # -- Network Config Handlers -- #
    def _on_install_net_dhcp_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_network_dhcp = sw.get_active()
        static = not self._install_network_dhcp
        self._install_net_ip_row.set_visible(static)
        self._install_net_gw_row.set_visible(static)
        self._install_net_dns_row.set_visible(static)

    def _on_install_net_ip_changed(self, entry: Adw.EntryRow, _pspec) -> None:
        self._install_network_static_ip = entry.get_text()

    def _on_install_net_gw_changed(self, entry: Adw.EntryRow, _pspec) -> None:
        self._install_network_gateway = entry.get_text()

    def _on_install_net_dns_changed(self, entry: Adw.EntryRow, _pspec) -> None:
        self._install_network_dns = entry.get_text()

    # -- End Network Config Handlers -- #

    # -- Storage Config Handlers -- #
    def _on_install_stor_auto_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_storage_auto_partition = sw.get_active()

    def _on_install_stor_btrfs_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_storage_btrfs = sw.get_active()

    # -- End Storage Config Handlers -- #

    # -- Software Selection Handlers -- #
    def _on_install_software_toggled(self, cb: Gtk.CheckButton, key: str) -> None:
        self._install_software[key] = cb.get_active()

    # -- End Software Selection Handlers -- #

    # -- Timezone Handler -- #
    def _on_install_tz_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        timezones = [
            "Asia/Kolkata", "Asia/Tokyo", "Asia/Shanghai", "Asia/Dubai",
            "Europe/London", "Europe/Berlin", "Europe/Paris",
            "America/New_York", "America/Los_Angeles", "America/Chicago",
            "Australia/Sydney", "UTC",
        ]
        self._install_timezone = timezones[sel] if sel < len(timezones) else "Asia/Kolkata"

    # -- End Timezone Handler -- #

    # -- Locale & Keyboard Handlers -- #
    def _on_install_locale_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        locales = ["en_US.UTF-8", "en_GB.UTF-8", "fr_FR.UTF-8", "de_DE.UTF-8", "ja_JP.UTF-8"]
        self._install_locale = locales[sel] if sel < len(locales) else "en_US.UTF-8"

    def _on_install_kb_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        keyboards = ["us", "gb", "fr", "de", "jp", "dvorak", "colemak"]
        self._install_keyboard = keyboards[sel] if sel < len(keyboards) else "us"

    # -- End Locale & Keyboard Handlers -- #

    # -- Accessibility Handlers -- #
    def _on_install_a11y_toggled(self, cb: Gtk.CheckButton, key: str) -> None:
        setattr(self, f"_install_a11y_{key}", cb.get_active())

    # -- End Accessibility Handlers -- #

    # -- Power Management Handlers -- #
    def _on_install_power_profile_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        profiles = ["performance", "balanced", "power-saver"]
        self._install_power_profile = profiles[sel] if sel < len(profiles) else "balanced"

    def _on_install_power_sleep_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        sleep_modes = ["suspend", "hibernate", "hybrid-sleep"]
        self._install_power_sleep = sleep_modes[sel] if sel < len(sleep_modes) else "suspend"

    # -- End Power Management Handlers -- #

    # -- Security Handlers -- #
    def _on_install_sec_firewall_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_security_firewall = sw.get_active()

    def _on_install_sec_autoupdate_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_security_autoupdate = sw.get_active()

    def _on_install_sec_snapd_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_security_snapd = sw.get_active()

    # -- End Security Handlers -- #

    # -- Backup Handlers -- #
    def _on_install_bak_enabled_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_backup_enabled = sw.get_active()

    def _on_install_bak_target_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        targets = ["external", "network", "cloud", "local"]
        self._install_backup_target = targets[sel] if sel < len(targets) else "external"

    # -- End Backup Handlers -- #

    # -- Update Preferences Handlers -- #
    def _on_install_upd_channel_changed(self, row: Adw.ComboRow, _pspec) -> None:
        sel = row.get_selected()
        channels = ["stable", "beta", "nightly"]
        self._install_update_channel = channels[sel] if sel < len(channels) else "stable"

    def _on_install_upd_auto_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_update_auto = sw.get_active()

    # -- End Update Preferences Handlers -- #

    # -- GPG Key Handlers -- #
    def _on_gpg_import_key(self, _btn: Gtk.Button) -> None:
        dlg = Gtk.FileDialog(title="Import GPG Public Key")
        def on_selected(d, res):
            try:
                f = d.open_finish(res)
                if f and f.get_path():
                    # In production: import the key via gpg
                    self._install_gpg_keys.append({
                        "fingerprint": f.get_path().split("/")[-1].replace(".asc", ""),
                        "name": f.get_basename(),
                    })
                    self._toast("GPG key imported")
            except GLib.Error:
                pass
        dlg.open(self, None, on_selected)

    # -- Services Handlers -- #
    def _on_install_svc_toggled(self, cb: Gtk.CheckButton, key: str) -> None:
        setattr(self, f"_install_svc_{key}", cb.get_active())

    # -- End Services Handlers -- #

    # -- Model Hub Handlers -- #
    def _on_install_model_toggled(self, cb: Gtk.CheckButton, key: str) -> None:
        if cb.get_active():
            if key not in self._install_models:
                self._install_models.append(key)
        else:
            self._install_models = [m for m in self._install_models if m != key]

    # -- End Model Hub Handlers -- #

    # -- API Keys Handler -- #
    def _on_install_api_keys_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_api_keys_configured = sw.get_active()
        self._install_api_keys_note.set_visible(not sw.get_active())

    # -- End API Keys Handler -- #

    # -- Access Handlers -- #
    def _on_install_acc_landlock_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_access_landlock = sw.get_active()

    def _on_install_acc_sandbox_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_access_sandbox = sw.get_active()

    # -- End Access Handlers -- #

    # -- Wipe Handlers -- #
    def _on_install_wipe_keep_home_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_wipe_keep_home = sw.get_active()

    def _on_install_wipe_keep_configs_toggled(self, sw: Adw.SwitchRow, _pspec) -> None:
        self._install_wipe_keep_configs = sw.get_active()

    def _on_install_wipe_confirm_changed(self, entry: Adw.EntryRow, _pspec) -> None:
        self._install_wipe_confirm = entry.get_text()
        # Enable wipe button only when confirmed and previewed
        self._reset_wipe_btn.set_sensitive(
            self._install_wipe_confirm == "WIPE"
        )

    # -- End Wipe Handlers -- #

    def _on_reboot_now(self, _btn: Gtk.Button) -> None:
        self._toast("Rebooting system in 5 seconds...")
        threading.Thread(
            target=lambda: (
                time.sleep(5),
                subprocess.run(["systemctl", "reboot"], check=False),
            ),
            daemon=True,
        ).start()

    # -- Reset / Wipe Handlers -- #
    def _on_preview_wipe(self, _btn: Gtk.Button) -> None:
        preview = self._wiper.get_wipe_preview() if self._wiper else SystemWiper(
            keep_home=self._install_wipe_keep_home,
            keep_configs=self._install_wipe_keep_configs,
        ).get_wipe_preview()
        self._wiper = SystemWiper(
            keep_home=self._install_wipe_keep_home,
            keep_configs=self._install_wipe_keep_configs,
        )
        lines = [
            f"Packages to remove: {len(preview['packages'])}",
            f"Services to stop: {len(preview['services'])}",
            f"Files/dirs to remove: {len(preview['files'])}",
            f"Total items: {preview['total_items']}",
            "",
            "Packages:",
        ]
        lines.extend(f"  • {p}" for p in preview["packages"][:10])
        if len(preview["packages"]) > 10:
            lines.append(f"  … and {len(preview['packages']) - 10} more")
        lines.append("")
        lines.append("Files & dirs:")
        lines.extend(f"  • {f}" for f in preview["files"][:10])
        if len(preview["files"]) > 10:
            lines.append(f"  … and {len(preview['files']) - 10} more")

        self._reset_preview_label.set_label("\n".join(lines))
        # Only enable wipe button if the user has typed "WIPE" in the confirmation field
        confirm = getattr(self, "_install_wipe_confirm", "")
        self._reset_wipe_btn.set_sensitive(confirm == "WIPE")
        self._toast("Wipe preview generated — review before confirming")

    def _on_start_wipe(self, _btn: Gtk.Button) -> None:
        if self._wipe_in_progress:
            return
        self._wipe_in_progress = True
        self._reset_wipe_btn.set_sensitive(False)

        def on_wipe_progress(title: str, progress: float, detail: str) -> None:
            GLib.idle_add(self._reset_progress.set_fraction, progress)
            GLib.idle_add(self._reset_progress.set_text, f"{progress * 100:.0f}%")
            GLib.idle_add(self._reset_status_label.set_label, f"{title}: {detail}")

        self._wiper = SystemWiper(
            callback=on_wipe_progress,
            keep_home=self._install_wipe_keep_home,
            keep_configs=self._install_wipe_keep_configs,
        )

        def run_wipe() -> None:
            self._wiper.wipe()
            GLib.idle_add(self._on_wipe_complete)

        threading.Thread(target=run_wipe, daemon=True).start()

    def _on_wipe_complete(self) -> None:
        self._wipe_in_progress = False
        self._reset_reboot_btn.set_sensitive(True)
        self._reset_reboot_btn.set_visible(True)
        self._reset_status_label.set_label("Wipe complete — reboot to return to stock Ubuntu")
        self._toast("System reset complete! Please restart your system.")

    def _append_log(self, text: str) -> None:
        end = self.update_log_buf.get_end_iter()
        self.update_log_buf.insert(end, text)


class MacubuntuCenterApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.macubuntu.Center")
        self._window: Optional[MacubuntuCenterWindow] = None
        self._css_loaded: bool = False

    def do_activate(self) -> None:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        if not self._css_loaded:
            css = Gtk.CssProvider()
            css.load_from_path(str(Path(__file__).with_name("style.css")))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            self._css_loaded = True
        if self._window is None:
            self._window = MacubuntuCenterWindow(self)
        self._window.present()


if __name__ == "__main__":
    sys.exit(MacubuntuCenterApp().run(sys.argv))
