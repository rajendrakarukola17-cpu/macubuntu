"""
Bucket 1 & Bucket 2 macOS + Siri Parity Tests:
  - DynamicThemeScheduler (solar Light <-> Dark GNOME switching, zero network)
  - AutoAppNapCoordinator (occlusion tracking, focus/audio auto-thaw, thaw_all safety)
  - OSEventMonitor full 0600 live_os_events.json payload
  - Apple Intelligence Writing Tools (local Ollama transform + clipboard)
  - macOS Live Text Region OCR (temp PNG deleted immediately, zero disk trail)
  - Lightweight Active Desktop Context (skips password managers!)
  - Native GNOME Calendar / RFC 5545 .ics bridge (parse, upcoming filter, approval-gated 0600 write)
  - Sentence-Streamed Piper TTS (`SentenceStreamSpeaker`) + barge-in cancellation
  - CoreSpotlight Rofi inline script mode (`format_rofi_spotlight_entries`)
"""

import datetime
import json
import stat
import threading
from pathlib import Path
from unittest import mock

import pytest

from celina_pkg import (
    app_intents,
    automation,
    biometric_anim,
    calendar_bridge,
    command_runner,
    config,
    memory,
    os_events,
    spotlight_index,
    voice,
    writing_tools,
)


class TestDynamicThemeAndAutoAppNap:
    def test_dynamic_theme_scheduler_switches_day_and_night_without_redundant_calls(self):
        calls: list[list[str]] = []

        def fake_run(cmd):
            calls.append(cmd)
            return command_runner.CommandResult(returncode=0, stdout="", stderr="", command=tuple(cmd))

        sched = os_events.DynamicThemeScheduler(sunrise_hour=6.5, sunset_hour=18.5, runner=fake_run, which=lambda _: "/usr/bin/gsettings")
        noon = datetime.datetime(2026, 11, 14, 12, 0)
        night = datetime.datetime(2026, 11, 14, 21, 30)

        assert sched.apply_if_needed(now=noon) == "default"
        assert sched.apply_if_needed(now=noon) == "default"  # idempotent: no 2nd call
        assert sched.apply_if_needed(now=night) == "prefer-dark"
        assert len(calls) == 2
        assert calls[0][-1] == "'default'"
        assert calls[1][-1] == "'prefer-dark'"

    def test_auto_app_nap_coordinator_freezes_idle_app_and_thaws_on_focus_or_shutdown(self, tmp_path: Path):
        cg_dir = tmp_path / "app-gimp.scope"
        cg_dir.mkdir()
        ff = cg_dir / "cgroup.freeze"
        ff.write_text("0\n", encoding="utf-8")

        freezer = os_events.CgroupFreezer(cgroup_root=tmp_path)
        freezer.find_app_cgroup = lambda pid: cg_dir if pid == 4242 else None  # type: ignore[method-assign]
        coord = os_events.AutoAppNapCoordinator(
            policy=os_events.AutoAppNapPolicy(idle_threshold_sec=60.0),
            freezer=freezer,
        )

        # First observation starts occlusion timer
        assert coord.step(active_app="code", active_audio_apps=set(), candidate_pids={4242: "gimp"}, now=1000.0) == []
        # After 90s occluded -> freezes cgroup
        assert coord.step(active_app="code", active_audio_apps=set(), candidate_pids={4242: "gimp"}, now=1090.0) == ["gimp"]
        assert ff.read_text(encoding="utf-8").strip() == "1"

        # Returning focus immediately thaws gimp
        assert coord.step(active_app="gimp", active_audio_apps=set(), candidate_pids={4242: "gimp"}, now=1100.0) == []
        assert ff.read_text(encoding="utf-8").strip() == "0"

        # Freeze again & verify thaw_all() clears on SIGTERM
        coord.step(active_app="code", active_audio_apps=set(), candidate_pids={4242: "gimp"}, now=2000.0)
        coord.step(active_app="code", active_audio_apps=set(), candidate_pids={4242: "gimp"}, now=2100.0)
        assert ff.read_text(encoding="utf-8").strip() == "1"
        assert coord.thaw_all() == 1
        assert ff.read_text(encoding="utf-8").strip() == "0"

    def test_os_event_monitor_writes_0600_enriched_snapshot(self, tmp_path: Path):
        st_file = tmp_path / "live_os_events.json"
        mon = os_events.OSEventMonitor(state_file=st_file, enable_dynamic_appearance=False, enable_auto_app_nap=False)
        snap = mon.poll_once()
        assert st_file.exists()
        assert stat.S_IMODE(st_file.stat().st_mode) == 0o600
        loaded = json.loads(st_file.read_text(encoding="utf-8"))
        assert "focus_mode" in loaded and "appearance_mode" in loaded and "napped_apps" in loaded
        assert snap["appearance_mode"] == "manual"


class TestWritingToolsAndLiveText:
    def test_writing_tool_transform_runs_local_and_rejects_invalid_mode(self):
        assert "Unknown Writing Tool mode" in writing_tools.writing_tool_transform("nonsense", "hello")
        out = writing_tools.writing_tool_transform(
            "professional",
            "hey send me that file asap",
            copy_result=False,
            llm_fn=lambda _sys, _usr: (True, "Could you please share that file at your earliest convenience?"),
        )
        assert "Writing Tool [professional]" in out
        assert "earliest convenience" in out

    def test_live_text_ocr_extracts_text_and_deletes_temp_screenshot(self):
        created_paths: list[Path] = []

        def fake_capture(p: Path) -> bool:
            created_paths.append(p)
            p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            return True

        with mock.patch.object(writing_tools, "write_wayland_clipboard", return_value=True):
            out = writing_tools.live_text_ocr_region(
                copy_to_clipboard=True,
                capture_fn=fake_capture,
                ocr_fn=lambda _p, _l: (True, "Invoice #4092\nTotal: $120.00"),
            )
        assert "Invoice #4092" in out and "Copied to clipboard" in out
        # Zero disk trail: temporary capture PNG MUST be deleted immediately
        assert len(created_paths) == 1 and not created_paths[0].exists()

    def test_lightweight_active_context_ignores_non_deictic_and_password_managers(self):
        assert writing_tools.get_lightweight_active_context("what is 2+2") == ""
        with mock.patch.object(automation, "get_active_window_info", return_value={"app": "Firefox", "title": "Ubuntu Docs"}):
            ctx = writing_tools.get_lightweight_active_context("summarize this page")
        assert "Firefox" in ctx and "Ubuntu Docs" in ctx

        with mock.patch.object(automation, "get_active_window_info", return_value={"app": "KeePassXC", "title": "vault.kdbx"}):
            assert writing_tools.get_lightweight_active_context("summarize this window") == ""


class TestNativeCalendarBridge:
    def test_ics_create_requires_approval_persists_0600_and_reads_back(self, tmp_path: Path):
        ics = tmp_path / "calendar.ics"
        # Denied without approval callback
        denied = calendar_bridge.create_calendar_event("Design Review", "2026-11-16 10:00", ics_path=ics, approval_cb=None)
        assert denied.startswith("Denied:")
        assert not ics.exists()

        # Approved -> atomic 0600 write & readable via get_upcoming_events
        created = calendar_bridge.create_calendar_event(
            "Design Review, Q4",
            "2026-11-16 10:00",
            duration_minutes=45,
            location="Room 4B; HQ",
            ics_path=ics,
            approval_cb=lambda _t, _d: True,
        )
        assert "Created calendar event" in created
        assert stat.S_IMODE(ics.stat().st_mode) == 0o600

        upcoming = calendar_bridge.get_upcoming_events(
            days=7,
            ics_path=ics,
            now=datetime.datetime(2026, 11, 14, 12, 0),
        )
        assert "Design Review, Q4" in upcoming
        assert "Room 4B; HQ" in upcoming


class TestSentenceStreamVoiceAndInlineSpotlight:
    def test_sentence_stream_speaker_streams_each_sentence_in_order(self):
        spoken: list[str] = []
        done_ev = threading.Event()
        sp = voice.SentenceStreamSpeaker(
            enabled=True,
            lang="en",
            on_done=done_ev.set,
            speak_fn=lambda _lang, txt, _gen: spoken.append(txt),
        )
        sp.feed_partial("Hello there. How are")
        sp.feed_partial("Hello there. How are you today? Great news")
        sp.finish("Hello there. How are you today? Great news!")
        assert done_ev.wait(timeout=3.0)
        assert spoken == ["Hello there.", "How are you today?", "Great news!"]

    def test_rofi_inline_spotlight_returns_math_units_settings_and_files(self, tmp_path: Path):
        docs = tmp_path / "Docs"
        docs.mkdir()
        (docs / "Roadmap_2026.md").write_text("hi", encoding="utf-8")
        idx = spotlight_index.SpotlightIndex(db_path=tmp_path / "spot.db")
        try:
            idx.refresh(roots=[docs])
            math_entries = spotlight_index.format_rofi_spotlight_entries("sqrt(144) + 2^8", index=idx)
            assert any("268" in lbl and act == "copy:268" for lbl, act in math_entries)

            unit_entries = spotlight_index.format_rofi_spotlight_entries("100 miles to km", index=idx)
            assert any("160.9" in lbl and act.startswith("copy:") for lbl, act in unit_entries)

            file_entries = spotlight_index.format_rofi_spotlight_entries("Roadmap", index=idx)
            assert any("Roadmap_2026.md" in lbl and act.startswith("open:") for lbl, act in file_entries)
        finally:
            idx.close()

    def test_listen_followup_window_unpacks_three_tuple_and_cleans_wav(self, tmp_path: Path):
        wav = tmp_path / "follow.wav"
        wav.write_bytes(b"RIFFdemo")
        with mock.patch.object(voice, "record_until_silence", return_value=(str(wav), None)), \
             mock.patch.object(voice, "transcribe_in_subprocess", return_value=("set volume to 40", "en", None)) as tr_mock:
            text, err = voice.listen_followup_window(timeout_sec=5.0, language="en")
        assert text == "set volume to 40" and err is None
        tr_mock.assert_called_once_with(str(wav), lang_hint="en")
        assert not wav.exists()


class TestNativeContactsAndBookmarkIntents:
    def test_search_contacts_parses_vcards_and_evolution_sqlite(self, tmp_path: Path):
        import sqlite3

        vcards_dir = tmp_path / "contacts"
        vcards_dir.mkdir()
        (vcards_dir / "team.vcf").write_text(
            "BEGIN:VCARD\nVERSION:3.0\nFN:Priya Rao\nORG:CoreOS\nEMAIL:priya@example.com\nTEL:+91-9000011111\nEND:VCARD\n",
            encoding="utf-8",
        )
        evo_db = tmp_path / "contacts.db"
        conn = sqlite3.connect(str(evo_db))
        conn.execute("CREATE TABLE folder_id (uid TEXT, vcard TEXT)")
        conn.execute(
            "INSERT INTO folder_id VALUES (?, ?)",
            ("1", "BEGIN:VCARD\nFN:Arjun Kumar\nEMAIL:arjun@example.org\nEND:VCARD"),
        )
        conn.commit()
        conn.close()

        r1 = app_intents.search_contacts("Priya", contacts_db=evo_db, vcards_dir=vcards_dir)
        assert "priya@example.com" in r1 and "+91-9000011111" in r1
        r2 = app_intents.search_contacts("Arjun", contacts_db=evo_db, vcards_dir=vcards_dir)
        assert "arjun@example.org" in r2

    def test_search_browser_bookmarks_reads_chromium_json_and_firefox_sqlite(self, tmp_path: Path):
        import sqlite3

        cr_bm = tmp_path / "Bookmarks"
        cr_bm.write_text(
            json.dumps({
                "roots": {
                    "bookmark_bar": {
                        "type": "folder",
                        "children": [
                            {"type": "url", "name": "Ubuntu 26.04 Release Notes", "url": "https://ubuntu.com/docs"},
                        ],
                    }
                }
            }),
            encoding="utf-8",
        )
        ff_db = tmp_path / "places.sqlite"
        conn = sqlite3.connect(str(ff_db))
        conn.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
        conn.execute("CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INTEGER, fk INTEGER, title TEXT)")
        conn.execute("INSERT INTO moz_places VALUES (1, 'https://github.com/macubuntu', 'macubuntu Repo')")
        conn.execute("INSERT INTO moz_bookmarks VALUES (10, 1, 1, 'macubuntu GitHub')")
        conn.commit()
        conn.close()

        out_cr = app_intents.search_browser_bookmarks("Ubuntu", chromium_files=[("Chrome", cr_bm)], firefox_dbs=[ff_db])
        assert "[Chrome]" in out_cr and "https://ubuntu.com/docs" in out_cr
        out_ff = app_intents.search_browser_bookmarks("macubuntu", chromium_files=[("Chrome", cr_bm)], firefox_dbs=[ff_db])
        assert "[Firefox]" in out_ff and "https://github.com/macubuntu" in out_ff


class TestSafariFirefoxThemeAndIcon:
    def _load_safari_module(self):
        import importlib.util

        mod_path = Path(__file__).resolve().parents[1] / "system" / "firefox" / "apply-safari-theme.py"
        spec = importlib.util.spec_from_file_location("apply_safari_theme", mod_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_safari_compass_svg_is_valid_xml_with_squircle_and_needle(self):
        import xml.etree.ElementTree as ET

        svg_path = Path(__file__).resolve().parents[1] / "system" / "firefox" / "safari-compass.svg"
        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
        assert root.tag.endswith("svg")
        raw = svg_path.read_text(encoding="utf-8")
        assert "needleRedLight" in raw and "dialGrad" in raw and "<polygon" in raw

    def test_safari_theme_apply_dryrun_and_clean_revert_across_snap_and_deb(self, tmp_path: Path):
        mod = self._load_safari_module()
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        # Simulate Snap Firefox profile with pre-existing userChrome.css and user.js
        snap_root = fake_home / "snap" / "firefox" / "common" / ".mozilla" / "firefox"
        prof = snap_root / "abcd1234.default"
        (prof / "chrome").mkdir(parents=True)
        (snap_root / "profiles.ini").write_text("[Profile0]\nName=default\nIsRelative=1\nPath=abcd1234.default\n", encoding="utf-8")
        (prof / "chrome" / "userChrome.css").write_text("/* user original css */\n", encoding="utf-8")
        (prof / "user.js").write_text('user_pref("custom.keep", 42);\n', encoding="utf-8")

        # Simulate system Snap firefox desktop file
        sys_desktop = tmp_path / "firefox_firefox.desktop"
        sys_desktop.write_text(
            "[Desktop Entry]\nName=Firefox Web Browser\nName[fr]=Navigateur Web Firefox\nExec=env BAMF_DESKTOP_FILE_HINT=/var/lib/snapd/desktop/applications/firefox_firefox.desktop /snap/bin/firefox %u\nIcon=/snap/firefox/icon.png\nKeywords=web;browser;\n",
            encoding="utf-8",
        )
        sources = [("firefox_firefox.desktop", (str(sys_desktop),))]

        # 1. Dry-run MUST make zero changes
        mod.apply_safari_theme(home=fake_home, desktop_sources=sources, dry_run=True)
        assert not (fake_home / ".local" / "share" / "icons").exists()
        assert (prof / "chrome" / "userChrome.css").read_text(encoding="utf-8") == "/* user original css */\n"

        # 2. Apply installs SVG icon, .desktop override (preserving Exec=), Safari CSS + delimited user.js
        mod.apply_safari_theme(home=fake_home, desktop_sources=sources, dry_run=False)
        icon = fake_home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "macubuntu-safari.svg"
        desktop_out = fake_home / ".local" / "share" / "applications" / "firefox_firefox.desktop"
        assert icon.exists() and desktop_out.exists()
        dt = desktop_out.read_text(encoding="utf-8")
        assert "Name=Safari\n" in dt
        assert "Name[fr]=" not in dt
        assert f"Icon={icon}\n" in dt
        assert "/snap/bin/firefox %u" in dt
        assert "safari" in dt.lower()

        assert "MACUBUNTU-SAFARI-THEME" in (prof / "chrome" / "userChrome.css").read_text(encoding="utf-8")
        assert (prof / "chrome" / "userChrome.css.macubuntu.bak").exists()
        ujs = (prof / "user.js").read_text(encoding="utf-8")
        assert 'user_pref("custom.keep", 42);' in ujs
        assert "toolkit.legacyUserProfileCustomizations.stylesheets" in ujs

        # 3. Revert restores original userChrome.css, strips only Safari user.js block, removes icon & desktop override
        mod.revert_safari_theme(home=fake_home, dry_run=False)
        assert not icon.exists()
        assert not desktop_out.exists()
        assert (prof / "chrome" / "userChrome.css").read_text(encoding="utf-8") == "/* user original css */\n"
        ujs_after = (prof / "user.js").read_text(encoding="utf-8")
        assert 'user_pref("custom.keep", 42);' in ujs_after
        assert "MACUBUNTU-SAFARI-START" not in ujs_after

    def test_top_panel_css_eliminates_black_bar_and_dryrun_accepts_hidden_and_transparent(self, tmp_path: Path):
        import os
        import subprocess

        repo_root = Path(__file__).resolve().parents[1]
        css = (repo_root / "system" / "gnome" / "panel-glass.css").read_text(encoding="utf-8")
        assert "#panel.solid" in css
        assert "background-color: transparent !important" in css
        assert "border-bottom: none !important" in css
        assert "box-shadow: none !important" in css

        for mode in ("transparent", "hidden", "glass"):
            fake_home = tmp_path / f"home_{mode}"
            fake_home.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(fake_home)
            res = subprocess.run(
                ["bash", str(repo_root / "install.sh"), "--dry-run", "--top-panel", mode, "--only", "20,25"],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert res.returncode == 0, f"--top-panel {mode} failed:\n{res.stdout}\n{res.stderr}"
            assert f"TOP_PANEL_STYLE={mode}" in res.stdout
            assert list(fake_home.iterdir()) == []


class TestAppleFaceAndTouchIDAnimation:
    def test_biometric_state_machine_face_success_and_auto_collapse(self):
        st = biometric_anim.BiometricAnimState()
        assert not st.is_active
        st.start("face", auto_resolve_after=0.3, auto_matched=True)
        assert st.phase == "scanning"
        assert st.frame().color_rgb == biometric_anim.COLOR_BLUE

        # Step through scan -> success checkmark draw -> collapse -> idle
        for _ in range(25):
            st.step(0.02)
        assert st.phase == "success"
        fr = st.frame()
        assert fr.color_rgb == biometric_anim.COLOR_GREEN
        assert fr.check_progress > 0.0

        # Continue stepping until auto-collapse finishes (0% idle CPU invariant!)
        still_running = True
        for _ in range(100):
            still_running = st.step(0.02)
            if not still_running:
                break
        assert not still_running and st.phase == "idle"

    def test_biometric_state_machine_touch_failure_damped_spring_shake(self):
        st = biometric_anim.BiometricAnimState()
        st.start("touch")
        assert st.frame().color_rgb == biometric_anim.COLOR_ROSE
        st.step(0.1)
        st.resolve(False)
        assert st.phase == "failure"
        # Sample damped horizontal shake early vs late
        st.step(0.05)
        early_shake = abs(st.frame().shake_x)
        assert early_shake > 2.0
        for _ in range(30):
            st.step(0.02)
        late_shake = abs(st.frame().shake_x)
        assert late_shake < early_shake

    def test_fprintd_status_classifier_and_dbus_observer_dispatch(self):
        assert biometric_anim.classify_fprintd_status("verify-match", True) == "success"
        assert biometric_anim.classify_fprintd_status("verify-no-match", True) == "failure"
        assert biometric_anim.classify_fprintd_status("verify-retry-scan", False) == "retry"
        assert biometric_anim.classify_fprintd_status("verify-retry-scan", True) == "failure"
        assert biometric_anim.classify_fprintd_status("verify-unknown-code", False) == "ignore"

        events: list[tuple] = []
        obs = biometric_anim.BiometricDBusObserver(
            on_start=lambda k, l: events.append(("start", k, l)),
            on_resolve=lambda ok, l: events.append(("resolve", ok, l)),
            on_retry=lambda l: events.append(("retry", l)),
            on_unlock_welcome=lambda k: events.append(("unlock", k)),
            preferred_unlock_kind=lambda: "face",
        )
        obs.handle_fprintd_selected("right-index-finger")
        obs.handle_fprintd_status("verify-retry-scan", False)
        obs.handle_fprintd_status("verify-match", True)
        obs.handle_screensaver_changed(True)   # locking does NOT play unlock welcome
        obs.handle_screensaver_changed(False)  # unlocking DOES play Face ID welcome
        assert events == [
            ("start", "touch", "Touch ID (right-index-finger)"),
            ("retry", "Hold Finger"),
            ("resolve", True, "Unlocked"),
            ("unlock", "face"),
        ]


class TestFivePillarSmoothnessOptimizations:
    def test_apps_kernel_io_wayland_and_sqlite_smoothness_optimizations(self, tmp_path: Path):
        repo_root = Path(__file__).resolve().parents[1]

        sysctl_txt = (repo_root / "system" / "sysctl" / "99-macubuntu-vm.conf").read_text(encoding="utf-8")
        for expected in (
            "vm.dirty_background_bytes=67108864",
            "vm.dirty_bytes=268435456",
            "kernel.sched_autogroup_enabled=1",
            "fs.inotify.max_user_watches=524288",
            "net.core.default_qdisc=fq",
            "net.ipv4.tcp_congestion_control=bbr",
            "net.ipv4.tcp_fastopen=3",
        ):
            assert expected in sysctl_txt, f"Missing sysctl smoothness setting: {expected}"

        io_rules = (repo_root / "system" / "udev" / "60-macubuntu-io-scheduler.rules").read_text(encoding="utf-8")
        assert 'ATTR{queue/scheduler}="none"' in io_rules
        assert 'ATTR{queue/scheduler}="mq-deadline"' in io_rules
        assert 'ATTR{queue/scheduler}="bfq"' in io_rules

        env_conf = (repo_root / "system" / "environment.d" / "90-macubuntu-smooth.conf").read_text(encoding="utf-8")
        assert "ELECTRON_OZONE_PLATFORM_HINT=auto" in env_conf
        assert "MOZ_ENABLE_WAYLAND=1" in env_conf
        assert "QT_QPA_PLATFORM=wayland;xcb" in env_conf

        db = memory.MemoryDB(tmp_path / "mem.db")
        try:
            assert db._conn.execute("PRAGMA journal_mode;").fetchone()[0].lower() == "wal"
            assert db._conn.execute("PRAGMA temp_store;").fetchone()[0] == 2  # MEMORY
            assert db._conn.execute("PRAGMA mmap_size;").fetchone()[0] >= 33554432
        finally:
            db.close()

        idx = spotlight_index.SpotlightIndex(tmp_path / "spot.db")
        try:
            assert idx._conn.execute("PRAGMA journal_mode;").fetchone()[0].lower() == "wal"
            assert idx._conn.execute("PRAGMA temp_store;").fetchone()[0] == 2
        finally:
            idx.close()

    def test_browsing_firefox_fastfox_smoothfox_vaapi_and_gpu_css(self):
        import importlib.util

        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "system" / "firefox" / "apply-safari-theme.py"
        spec = importlib.util.spec_from_file_location("apply_safari_theme_perf", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        for pref in (
            'user_pref("gfx.webrender.all", true);',
            'user_pref("gfx.webrender.layer-compositor", true);',
            'user_pref("media.ffmpeg.vaapi.enabled", true);',
            'user_pref("gfx.content.skia-font-cache-size", 20);',
            'user_pref("apz.overscroll.enabled", true);',
            'user_pref("general.smoothScroll.msdPhysics.enabled", true);',
            'user_pref("browser.tabs.unloadOnLowMemory", true);',
            'user_pref("browser.tabs.min_inactive_duration_before_unload", 300000);',
            'user_pref("browser.sessionstore.interval", 60000);',
        ):
            assert pref in mod.USERJS_BLOCK, f"Missing Safari Firefox perf pref: {pref}"

        css = (repo_root / "system" / "firefox" / "userChrome.css").read_text(encoding="utf-8")
        assert "contain: layout style" in css
        assert "prefers-reduced-motion: reduce" in css
        assert "filter: brightness" not in css

    def test_ai_router_keepalive_pool_all_gear_streaming_and_ollama_tuning(self, tmp_path: Path):
        from celina_pkg import cache as cache_mod, router as router_mod

        repo_root = Path(__file__).resolve().parents[1]
        ollama_conf = (repo_root / "system" / "systemd" / "ollama-8gb-ram.conf").read_text(encoding="utf-8")
        assert 'OLLAMA_KEEP_ALIVE=45s' in ollama_conf
        assert 'Nice=10' in ollama_conf
        assert 'IOWeight=30' in ollama_conf
        assert 'OLLAMA_FLASH_ATTENTION=1' in ollama_conf
        assert 'OLLAMA_KV_CACHE_TYPE=q8_0' in ollama_conf
        amd_conf = (repo_root / "system" / "systemd" / "ollama-amd-vulkan.conf").read_text(encoding="utf-8")
        assert 'OLLAMA_VULKAN=1' in amd_conf
        assert 'OLLAMA_IGPU_ENABLE=1' in amd_conf

        assert router_mod._recommended_ollama_threads(16) == 8
        assert router_mod._recommended_ollama_threads(4) == 4
        # 8GB RAM laptops automatically cap context to 1536/2048 to avoid swap/disk 100% thrashing
        assert router_mod._ollama_ctx_for_gear("fast", 4096, total_ram_gb=8) == 1536
        assert router_mod._ollama_ctx_for_gear("deep", 4096, total_ram_gb=8) == 2048
        # 16GB+ laptops keep 2048 fast / full 4096 deep
        assert router_mod._ollama_ctx_for_gear("fast", 4096, total_ram_gb=16) == 2048
        assert router_mod._ollama_ctx_for_gear("deep", 4096, total_ram_gb=16) == 4096
        # Dynamic RAM-pressure keep_alive evicts immediately (<1200MB) or after 15s (<2200MB)
        # (Temporarily clear live CELINA_LITE_MODE so test verifies standard RAM-pressure curve)
        prev_lite = config.LITE_MODE
        prev_env_lite = config.ENV.pop("CELINA_LITE_MODE", None)
        config.LITE_MODE = False
        try:
            assert router_mod._effective_ollama_keep_alive(900) == 0
            assert router_mod._effective_ollama_keep_alive(1800) == "15s"
            assert router_mod._effective_ollama_keep_alive(3600) == config.OLLAMA_KEEP_ALIVE
        finally:
            config.LITE_MODE = prev_lite
            if prev_env_lite is not None:
                config.ENV["CELINA_LITE_MODE"] = prev_env_lite

        db = memory.MemoryDB(tmp_path / "r.db")
        rc = cache_mod.SmartCache(tmp_path / "c.db")
        r = router_mod.CelinaRouter(db=db, response_cache=rc)
        try:
            assert isinstance(r._http, router_mod.requests.Session)
            assert "https://" in r._http.adapters and "http://" in r._http.adapters
            # Pre-warming MUST be skipped on 8GB RAM or low available memory (zero wasted disk I/O!)
            assert r.warm_local_model_async(total_ram_gb=8, available_ram_mb=4000) is False
            assert r.warm_local_model_async(total_ram_gb=16, available_ram_mb=2000) is False

            captured: dict = {}

            class _FakeStreamResp:
                status_code = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def raise_for_status(self):
                    pass

                def iter_lines(self, decode_unicode=True):
                    yield '{"message":{"content":"<analysis>1) check</analysis>Smooth "},"done":false}'
                    yield '{"message":{"content":"answer!"},"done":true}'

            def _fake_post(url, headers=None, json=None, timeout=None, stream=False):
                captured["payload"] = json
                captured["stream"] = stream
                return _FakeStreamResp()

            r._http.post = _fake_post  # type: ignore[method-assign]
            chunks: list[str] = []
            res = r._post_ollama(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
                gear="deep",
                use_tools=False,
                stream_cb=chunks.append,
            )
            assert captured["stream"] is True
            assert captured["payload"]["stream"] is True
            assert captured["payload"]["keep_alive"] == router_mod._effective_ollama_keep_alive()
            base_deep_ctx = router_mod._ollama_ctx_for_gear("deep")
            # On real 8 GB laptops under live memory PSI or kv-psi trimming, num_ctx may tighten below base_deep_ctx
            assert 512 <= captured["payload"]["options"]["num_ctx"] <= base_deep_ctx
            assert captured["payload"]["options"]["num_thread"] >= 1
            assert any("Smooth" in c for c in chunks)
            assert all("<analysis>" not in c for c in chunks)
            assert "Smooth answer!" in res["message"]["content"]
            # /ram reflex gives instant 0-LLM RAM & keep-alive telemetry
            ram_reply, ram_src = r.ask("/ram")
            assert ram_src == "system" and "RAM Guard:" in ram_reply
        finally:
            db.close()
            rc.close()

    def test_animations_and_themes_60fps_dirty_rects_and_cli_dryrun(self, tmp_path: Path):
        import os
        import subprocess
        from celina_pkg import edge_glow

        # Verify >88% pixel-fill savings via 4-strip edge invalidation on 1080p & 4K screens
        for w, h in ((1920, 1080), (3840, 2160)):
            rects = edge_glow.compute_edge_dirty_rects(w, h, border_px=36)
            assert len(rects) == 4
            dirty_px = sum(rw * rh for _, _, rw, rh in rects)
            assert dirty_px < int(w * h * 0.12)

        repo_root = Path(__file__).resolve().parents[1]
        stage25 = (repo_root / "stages" / "25-desktop-dock.sh").read_text(encoding="utf-8")
        assert "/org/gnome/shell/extensions/blur-my-shell/applications/blur false" in stage25
        assert "/org/gnome/shell/extensions/blur-my-shell/dash-to-dock/static-blur true" in stage25
        assert "/org/gnome/shell/extensions/blur-my-shell/power-saver true" in stage25
        assert "/org/gnome/shell/extensions/just-perfection/switcher-popup-delay false" in stage25
        assert "/org/gnome/shell/extensions/just-perfection/panel-corner-size 1" in stage25
        assert "variable-refresh-rate" in stage25

        for spd in ("fast", "normal", "instant"):
            fake_home = tmp_path / f"home_anim_{spd}"
            fake_home.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(fake_home)
            res = subprocess.run(
                ["bash", str(repo_root / "install.sh"), "--dry-run", "--animation-speed", spd, "--only", "25,35"],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert res.returncode == 0, f"--animation-speed {spd} failed:\n{res.stdout}\n{res.stderr}"
            assert f"ANIMATION_SPEED={spd}" in res.stdout
            assert list(fake_home.iterdir()) == []

    def test_live_model_switcher_moe_catalog_and_writing_tools_config(self, tmp_path: Path):
        from celina_pkg import cache as cache_mod, router as router_mod

        prev_model = config.LOCAL_MODEL
        env_file = tmp_path / "env"
        env_file.write_text('CELINA_NAME="Celina"\nCELINA_LOCAL_MODEL="qwen3:4b"\n', encoding="utf-8")
        try:
            # 1. Catalog formats IN-RAM vs NVMe mmap honestly by RAM tier
            cat_8 = config.format_model_catalog(ram_gb=8)
            assert "granite3.1-moe:3b" in cat_8
            assert "gemma4:26b" in cat_8
            assert "NVMe mmap" in cat_8

            # 2. Switching to large MoE on 8GB persists atomically at 0600 AND emits honest NVMe mmap warning
            tag, warn = config.set_local_model("gemma4:26b", env_path=env_file, ram_gb=8)
            assert tag == "gemma4:26b"
            assert warn is not None and "NVMe mmap" in warn
            assert 'CELINA_LOCAL_MODEL="gemma4:26b"' in env_file.read_text(encoding="utf-8")
            assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

            # 3. Switching on 32GB emits no RAM warning
            tag2, warn2 = config.set_local_model("qwen3.5:35b-a3b", env_path=env_file, ram_gb=32)
            assert tag2 == "qwen3.5:35b-a3b" and warn2 is None

            # 4. Unsafe shell injection tag is strictly rejected
            with pytest.raises(ValueError):
                config.set_local_model("qwen3:4b; rm -rf /", env_path=env_file, ram_gb=16)

            # 5. Router /model reflex lists curated Dense & Sparse MoE models cleanly
            db = memory.MemoryDB(tmp_path / "m.db")
            rc = cache_mod.SmartCache(tmp_path / "c.db")
            r = router_mod.CelinaRouter(db=db, response_cache=rc)
            try:
                reply, src = r.ask("/model")
                assert src == "system"
                assert "gemma4:26b" in reply and "qwen3:30b-a3b" in reply
            finally:
                db.close()
                rc.close()

            # 6. Verify writing_tools references valid config.OLLAMA_API_BASE & config.LOCAL_MODEL
            wt_src = (
                Path(__file__).resolve().parents[1]
                / "features" / "celina" / "celina_pkg" / "writing_tools.py"
            ).read_text(encoding="utf-8")
            assert "config.OLLAMA_API_BASE" in wt_src
            assert "config.LOCAL_MODEL" in wt_src
            assert "config.OLLAMA_HOST" not in wt_src
            assert "config.OLLAMA_MODEL" not in wt_src
        finally:
            config.LOCAL_MODEL = prev_model


