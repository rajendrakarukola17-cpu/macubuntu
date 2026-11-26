"""
Regression & parity tests for the 7 deep-audit bug fixes and 4 macOS/iOS parity upgrades:
  1. BiometricAnim: FingerprintAdapter 'enrolled' key -> Touch ID animation
  2. iOS ANCS Bridge: BlueZ hotplug startup with 0 iPhones connected + Actionable CommandID=2 + AirPods Battery1
  3. InputBroker: click_position & AT-SPI2 click_on_text consume 1 rate-limit slot (no double-counting)
  4. CalendarBridge: all-day events today & ongoing >1h meetings stay visible in get_upcoming_events()
  5. PipeWireRoutingAdapter: matches application.process.binary & node.name (Firefox AudioIPC Server)
  6. EdgeGlow dynamic assistant title & common.sh GVariant-safe register_gnome_shortcut quoting
  7. SQLite Memory FTS5: backfills kg_triples even when memory_fts already has fact/episode rows
  8. Nautilus Quick Look thumbnailers (gnome-sushi, evince-thumbnailer, ffmpegthumbnailer)
  9. Opt-in terminal-aware keyd macOS Cmd remapping (--macos-cmd-keys) & Continuity hub fields
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import stat
import struct
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from celina_hardware.adapters import ExecResult, PipeWireRoutingAdapter
from celina_pkg import automation, biometric_anim, calendar_bridge, continuity, ios_ancs, memory


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. Bug 1: Touch ID detection via FingerprintAdapter's actual `enrolled` key
# ---------------------------------------------------------------------------
def test_biometric_detect_unlock_kind_reads_enrolled_key():
    fake_hw = mock.MagicMock()
    fake_hw.describe_capabilities.return_value = {
        "biometrics": {
            "ir_face": {"ir_camera_detected": False},
            "fingerprint": {"present": True, "enrolled": True, "detail": "1 finger enrolled"},
        }
    }
    with mock.patch("celina_hardware.get_hardware", return_value=fake_hw):
        assert biometric_anim.BiometricDBusObserver._detect_unlock_kind() == "touch"

    fake_hw.describe_capabilities.return_value = {
        "biometrics": {
            "ir_face": {"ir_camera_detected": True},
            "fingerprint": {"present": True, "enrolled": True},
        }
    }
    with mock.patch("celina_hardware.get_hardware", return_value=fake_hw):
        assert biometric_anim.BiometricDBusObserver._detect_unlock_kind() == "face"


# ---------------------------------------------------------------------------
# 2. Bug 2 + Parity 1: iOS ANCS hotplug, Actionable CommandID=2 & AirPods Battery1
# ---------------------------------------------------------------------------
def test_ios_ancs_hotplug_actionable_and_airpods_battery():
    # BlueZ Battery1 discovery distinguishes AirPods/headphones vs generic peripherals
    managed = {
        "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF": {
            ios_ancs.BLUEZ_DEV_IFACE: {
                "Alias": "AirPods Pro",
                "Connected": True,
                "Icon": "audio-headset",
            },
            ios_ancs.BLUEZ_BATTERY_IFACE: {"Percentage": 88},
        },
        "/org/bluez/hci0/dev_11_22_33_44_55_66": {
            ios_ancs.BLUEZ_DEV_IFACE: {
                "Alias": "Magic Mouse",
                "Connected": True,
                "Icon": "input-mouse",
            },
            ios_ancs.BLUEZ_BATTERY_IFACE: {"Percentage": 64},
        },
    }
    bats = ios_ancs.discover_bluetooth_batteries(managed)
    assert len(bats) == 2
    airpods = next(b for b in bats if b.device_name == "AirPods Pro")
    mouse = next(b for b in bats if b.device_name == "Magic Mouse")
    assert airpods.is_audio is True and airpods.percentage == 88
    assert mouse.is_audio is False and mouse.percentage == 64

    # on_bt_battery callback deduplicates repeated identical battery readings
    bt_events = []
    bridge = ios_ancs.IOSAncsBridge(
        on_bt_battery=lambda name, pct, is_audio: bt_events.append((name, pct, is_audio))
    )
    bridge._emit_bt_battery_if_new(airpods)
    bridge._emit_bt_battery_if_new(airpods)
    assert bt_events == [("AirPods Pro", 88, True)]

    # Actionable iOS notification tracking & PerformNotificationAction (CommandID=2)
    ns_pkt = struct.pack(
        "<BBBBI",
        int(ios_ancs.ANCSEventID.ADDED),
        int(ios_ancs.ANCSEventFlag.POSITIVE_ACTION | ios_ancs.ANCSEventFlag.NEGATIVE_ACTION),
        int(ios_ancs.ANCSCategoryID.INCOMING_CALL),
        1,
        4242,
    )
    bridge.handle_notification_source_bytes(ns_pkt)
    assert bridge.last_notification_uid == 4242

    sent = []
    res = bridge.perform_last_notification_action(positive=True, write_control_point=sent.append)
    assert "Accepted/Answered" in res
    assert sent == [struct.pack("<BIB", int(ios_ancs.ANCSCommandID.PERFORM_NOTIFICATION_ACTION), 4242, 0)]
    assert bridge.last_notification_uid is None

    # Hotplug start(): succeeds even when 0 iPhones are connected at boot
    started_loop = []
    with (
        mock.patch.object(ios_ancs, "_get_bluez_managed_objects", return_value=({}, None)),
        mock.patch.object(bridge, "_run_dbus_loop", side_effect=lambda: started_loop.append(True)),
    ):
        assert bridge.start() is True
        bridge._thread.join(timeout=2.0)
    assert started_loop == [True]


# ---------------------------------------------------------------------------
# 3. Bug 3: InputBroker single rate-limit slot per click (no double-counting)
# ---------------------------------------------------------------------------
def test_click_position_consumes_single_rate_limit_slot():
    broker = automation.InputBroker(max_events=5, window_seconds=10.0)
    fake_portal = mock.MagicMock()
    fake_portal.is_available.return_value = True
    fake_portal.click.return_value = mock.MagicMock(ok=True, message="Verified: Portal clicked (100, 200)")
    with (
        mock.patch.object(automation, "input_broker", broker),
        mock.patch.object(automation, "is_stop_requested", return_value=False),
        mock.patch.object(automation, "is_focused_password_field", return_value=False),
        mock.patch.object(automation, "get_active_window_info", return_value={"title": "Demo", "app": "demo"}),
        mock.patch.object(automation, "_is_wayland", return_value=True),
        mock.patch.object(automation.portal_input, "get_portal_client", return_value=fake_portal),
    ):
        out = automation.click_position(100, 200, approval_cb=lambda _t, _d: True)
    assert "Verified:" in out
    assert len(broker._timestamps) == 1


# ---------------------------------------------------------------------------
# 4. Bug 4: Calendar keeps all-day events today & ongoing >1h meetings visible
# ---------------------------------------------------------------------------
def test_calendar_includes_all_day_and_long_ongoing_events(tmp_path):
    ics = tmp_path / "cal.ics"
    ics.write_text(
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "DTSTART;VALUE=DATE:20261115\n"
        "SUMMARY:Diwali Holiday\n"
        "END:VEVENT\n"
        "BEGIN:VEVENT\n"
        "DTSTART:20261115T090000\n"
        "DTEND:20261115T130000\n"
        "SUMMARY:Deep Architecture Workshop\n"
        "END:VEVENT\n"
        "BEGIN:VEVENT\n"
        "DTSTART:20261115T070000\n"
        "DTEND:20261115T080000\n"
        "SUMMARY:Finished Early Standup\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n",
        encoding="utf-8",
    )
    now = datetime.datetime(2026, 11, 15, 11, 30, 0)
    out = calendar_bridge.get_upcoming_events(days=3, ics_path=ics, now=now)
    assert "Diwali Holiday" in out
    assert "Deep Architecture Workshop" in out
    assert "Finished Early Standup" not in out


# ---------------------------------------------------------------------------
# 5. Bug 5: PipeWire routing matches Firefox AudioIPC Server via binary/node.name
# ---------------------------------------------------------------------------
def test_pipewire_route_app_audio_matches_process_binary_and_node_name():
    sink_inputs_stdout = (
        "Sink Input #77\n"
        "\tProperties:\n"
        '\t\tapplication.name = "AudioIPC Server"\n'
        '\t\tapplication.process.binary = "firefox"\n'
        '\t\tnode.name = "Firefox"\n'
    )
    sinks_stdout = "52\talsa_output.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts32le\tRUNNING\n"

    def fake_run(cmd, _timeout=5.0):
        cmd_l = list(cmd)
        if cmd_l[:3] == ["pactl", "list", "sink-inputs"]:
            return ExecResult(returncode=0, stdout=sink_inputs_stdout, stderr="")
        if cmd_l[:4] == ["pactl", "list", "short", "sinks"]:
            return ExecResult(returncode=0, stdout=sinks_stdout, stderr="")
        if cmd_l[:2] == ["pactl", "move-sink-input"]:
            return ExecResult(returncode=0, stdout="", stderr="")
        raise AssertionError(f"Unexpected cmd: {cmd_l}")

    adapter = PipeWireRoutingAdapter(runner=fake_run, which=lambda _: "/usr/bin/pactl")
    res = adapter.route_app_audio("firefox", "analog-stereo")
    assert res.ok is True
    assert res.verified is True
    assert res.observed["moved_stream_ids"] == ["77"]
    assert "Routed 'firefox' stream(s) ['77']" in res.message


# ---------------------------------------------------------------------------
# 6. Bug 6: EdgeGlow dynamic title & GVariant-safe register_gnome_shortcut()
# ---------------------------------------------------------------------------
def test_edge_glow_dynamic_title_and_gvariant_shortcut_escaping(tmp_path):
    edge_src = (REPO_ROOT / "features/celina/celina_pkg/edge_glow.py").read_text(encoding="utf-8")
    assert 'set_title("Celina Edge Glow")' not in edge_src
    assert "config.get_assistant_name()" in edge_src

    # Execute register_gnome_shortcut against a fake `dconf` capturing exact arguments
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_file = tmp_path / "dconf.log"
    fake_dconf = fake_bin / "dconf"
    fake_dconf.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "read" ]]; then echo "@as []"; exit 0; fi\n'
        f'printf "%s\\t%s\\t%s\\n" "$1" "$2" "$3" >> "{log_file}"\n',
        encoding="utf-8",
    )
    fake_dconf.chmod(fake_dconf.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["DRY_RUN"] = "0"
    res = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{REPO_ROOT}/core/common.sh"; '
            'register_gnome_shortcut "celina-test" "O\'Reilly\'s \\"AI\\" Helper" "/usr/bin/echo hi" "<Super>c"',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    lines = log_file.read_text(encoding="utf-8").splitlines()
    name_line = next(ln for ln in lines if ln.startswith("write\t") and "/name\t" in ln)
    val = name_line.split("\t", 2)[2]
    assert val == '"O\'Reilly\'s \\"AI\\" Helper"'


# ---------------------------------------------------------------------------
# 7. Bug 7: Memory FTS5 backfills kg_triples on upgraded DB with existing facts
# ---------------------------------------------------------------------------
def test_memory_fts_backfills_kg_triples_on_upgraded_db(tmp_path):
    db_path = tmp_path / "upgraded_memory.db"
    # First create a normal DB with a fact + a KG triple
    mem = memory.MemoryDB(db_path=db_path)
    mem.set_fact("favorite_editor", "Neovim")
    mem.add_knowledge_triple("Alice", "builds", "QuantumCompiler")
    mem.close()

    # Simulate an upgraded legacy DB: facts are already in memory_fts, but kg rows were never indexed
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM memory_fts WHERE category = 'kg'")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM memory_fts WHERE category = 'fact'").fetchone()[0] >= 1
    assert conn.execute("SELECT COUNT(*) FROM memory_fts WHERE category = 'kg'").fetchone()[0] == 0
    conn.close()

    # Re-opening MemoryDB must backfill kg_triples into memory_fts!
    mem2 = memory.MemoryDB(db_path=db_path)
    try:
        kg_rows = mem2._conn.execute("SELECT content FROM memory_fts WHERE category = 'kg'").fetchall()
        assert any("QuantumCompiler" in r[0] for r in kg_rows)
        kg_hits = mem2.recall_knowledge_triples("QuantumCompiler")
        assert any("QuantumCompiler" in str(h) for h in kg_hits)
    finally:
        mem2.close()


# ---------------------------------------------------------------------------
# 8-11. Parity 2, 3 & 4: Quick Look thumbnailers, opt-in keyd & Continuity Hub
# ---------------------------------------------------------------------------
def test_nautilus_quick_look_keyd_and_continuity_hub(tmp_path):
    stage45 = (REPO_ROOT / "stages/45-apps-backups-search.sh").read_text(encoding="utf-8")
    for pkg in ("gnome-sushi", "evince-thumbnailer", "ffmpegthumbnailer"):
        assert pkg in stage45

    keyd_main = (REPO_ROOT / "system/keyd/macubuntu.conf").read_text(encoding="utf-8")
    keyd_app = (REPO_ROOT / "system/keyd/app.conf").read_text(encoding="utf-8")
    assert "leftmeta = layer(macos_cmd)" in keyd_main
    assert "macos_cmd.c = C-S-c" in keyd_app
    assert "[org.gnome.Terminal]" in keyd_app

    # Dry-run with --macos-cmd-keys stays 100% side-effect-free on clean HOME
    clean_home = tmp_path / "clean_home"
    clean_home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(clean_home)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "install.sh"), "--dry-run", "--only", "60", "--macos-cmd-keys"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert "MACOS_CMD_KEYS=true" in r.stdout
    assert "apt install keyd" in r.stdout
    assert list(clean_home.iterdir()) == []

    # Continuity status reports Bluetooth batteries, USB iPhone Sync & Continuity Camera
    with (
        mock.patch.object(ios_ancs, "get_ios_ancs_status", return_value="no ANCS iPhone connected"),
        mock.patch.object(ios_ancs, "get_bluetooth_battery_status", return_value="🎧 AirPods Pro (92%)"),
    ):
        status = continuity.get_continuity_status()
    assert "bluetooth_batteries: 🎧 AirPods Pro (92%)" in status
    assert "ios_usb_sync:" in status
    assert "continuity_camera_v4l2:" in status


def test_pre_upgrade_end_to_end_wiring_connections(tmp_path):
    # 1. Nautilus Quick Actions passes --ask (matching island_ui.py) and island_ui accepts both --ask and --prompt
    naut_src = (REPO_ROOT / "system/nautilus/celina_quick_actions.py").read_text(encoding="utf-8")
    assert '"--ask"' in naut_src
    assert '"--prompt"' not in naut_src
    island_src = (REPO_ROOT / "features/celina/celina_pkg/island_ui.py").read_text(encoding="utf-8")
    assert '("--ask", "--prompt")' in island_src

    # 2. Default local model synced to qwen3:1.7b across config.py, neural-schema.json, env.template
    import json
    from celina_pkg import config, router

    disk_schema = json.loads((REPO_ROOT / "features/celina/config/neural-schema.json").read_text(encoding="utf-8"))
    assert disk_schema["routing"]["local_model"] == "qwen3:1.7b"
    assert config.DEFAULT_NEURAL_SCHEMA["routing"]["local_model"] == "qwen3:1.7b"
    # Live installs may have upgraded CELINA_LOCAL_MODEL (e.g. qwen2.5:3b via --upgrade-brain)
    if not config.ENV.get("CELINA_LOCAL_MODEL") and not os.environ.get("CELINA_LOCAL_MODEL"):
        assert config.LOCAL_MODEL == "qwen3:1.7b"
    else:
        assert bool(config.LOCAL_MODEL.strip())

    # 3. /help responds instantaneously at 0ms without calling Ollama
    mem = memory.MemoryDB(db_path=tmp_path / "help_test.db")
    try:
        rtr = router.CelinaRouter(db=mem)
        reply, src = rtr.ask("/help")
        assert src == "system"
        assert "/model" in reply and "/ram" in reply and "/offload" in reply
    finally:
        mem.close()

    # 4. features/celina/install.sh restarts celina-eventd.service on upgrade
    inst_src = (REPO_ROOT / "features/celina/install.sh").read_text(encoding="utf-8")
    assert "systemctl --user restart celina-eventd.service" in inst_src


def test_llm_pipeline_tool_routing_qwen3_think_coercion_and_privacy():
    from celina_pkg import config, router, tools

    # 1. All 78 tools partitioned across 8 domains & real macOS/Siri prompts resolve to exact target tools
    for prompt, expected in [
        ("check my upcoming calendar meetings tomorrow", "get_upcoming_events"),
        ("schedule a calendar meeting with Raj at 3pm", "create_calendar_event"),
        ("proofread my selected text", "writing_tool_transform"),
        ("extract text from screen region using live text ocr", "live_text_ocr"),
        ("check my iPhone continuity and AirPods battery status", "get_continuity_status"),
        ("route firefox audio to bluetooth headphones", "route_app_audio"),
        ("turn on coding focus mode", "set_focus_mode"),
        ("audit which apps have microphone or camera access", "audit_app_permissions"),
        ("search my browser bookmarks for github", "search_browser_bookmarks"),
        ("find Raj phone number in my contacts", "search_contacts"),
        ("mirror my android phone screen with scrcpy", "launch_android_mirror"),
        ("start airplay screen mirroring with uxplay", "control_airplay_mirror"),
        ("search active app menus for export pdf", "search_active_app_menus"),
        ("check hardware capabilities and biometrics", "get_hardware_capabilities"),
        ("मेरी कैलेंडर मीटिंग दिखाओ", "get_upcoming_events"),
    ]:
        pruned, _doms = tools.get_contextual_tool_schemas(prompt, gear="tactical")
        names = {t["function"]["name"] for t in pruned}
        assert expected in names, f"Tool '{expected}' missing for prompt {prompt!r}"

    # 2. Word-boundary matching prevents 'read' from false-triggering files_office on 'proofread'
    _pruned, proof_doms = tools.get_contextual_tool_schemas("proofread my selected text", gear="tactical")
    assert "files_office" not in proof_doms

    # 3. Qwen3 <think>...</think> stripped in both final & streaming preview paths
    assert router._strip_analysis_tags("<think>step 1\nstep 2</think>\nDone!") == "Done!"
    assert router._format_stream_preview("<think>still reasoning...") == ""
    assert router._format_stream_preview("<think>ok</think>Ready.") == "Ready."

    # 4. Small-model BFCL arg coercion & hallucinated-kwarg stripping
    def _dummy_vol(level: int = 50, mute: bool = False):
        return (level, mute)

    cleaned = tools._coerce_and_filter_tool_args(
        "set_volume", _dummy_vol, {"level": "65%", "mute": "false", "reason": "extra"}
    )
    assert cleaned == {"level": 65, "mute": False}

    # 5. Privacy Firewall protects all 6 new PIM/OCR/permission tools
    for pim in (
        "get_upcoming_events", "search_contacts", "search_browser_bookmarks",
        "writing_tool_transform", "live_text_ocr", "audit_app_permissions",
    ):
        assert pim in config.LOCAL_SENSITIVE_TOOLS

    # 6. Expanded Gear 0 0ms Siri reflexes execute without waking Ollama
    assert tools.handle_fast_intent("focus status") is not None
    assert "Cross-Platform Continuity Hub" in (tools.handle_fast_intent("continuity status") or "")
    assert "Privacy & Permissions Audit" in (tools.handle_fast_intent("privacy audit") or "")


def test_action_gate_pipewire_ducker_silero_vad_and_kokoro_guard():
    from celina_pkg import config, router, tools, voice

    # 1. ActionGate suppresses mutating tools on informational/how-to questions, never on real commands
    assert router.is_informational_only_prompt("how do you delete a file in linux?") is True
    assert router.is_informational_only_prompt("explain how focus mode works") is True
    assert router.is_informational_only_prompt("what does rm -rf do?") is True
    assert router.is_informational_only_prompt("delete my draft.txt file") is False
    assert router.is_informational_only_prompt("turn on coding focus mode") is False
    assert router.is_informational_only_prompt("please set volume to 50%") is False

    pruned, _ = tools.get_contextual_tool_schemas("how do you delete a file in linux?", gear="tactical")
    gated, suppressed = router.apply_action_gate("how do you delete a file in linux?", pruned)
    assert suppressed > 0
    gated_names = {t["function"]["name"] for t in gated}
    assert "trash_file" not in gated_names
    assert "write_file" not in gated_names
    assert "run_shell_command" not in gated_names
    # Read-only lookup tools remain available
    assert "search_files" in gated_names

    # 2. PipeWireAudioDucker ducks background music, skips Celina's own audio & corked streams, and ALWAYS restores!
    pactl_sample = (
        "Sink Input #11\n"
        "\tCorked: no\n"
        "\tVolume: front-left: 52428 / 80% / -5.81 dB\n"
        '\tProperties:\n\t\tapplication.name = "Spotify"\n'
        "Sink Input #12\n"
        "\tCorked: no\n"
        "\tVolume: front-left: 65536 / 100% / 0.00 dB\n"
        '\tProperties:\n\t\tapplication.name = "aplay"\n'
        "Sink Input #13\n"
        "\tCorked: yes\n"
        "\tVolume: front-left: 58982 / 90% / -2.74 dB\n"
        '\tProperties:\n\t\tapplication.name = "Firefox"\n'
        "Sink Input #14\n"
        "\tCorked: no\n"
        "\tVolume: front-left: 13107 / 20% / -41.94 dB\n"
        '\tProperties:\n\t\tapplication.name = "VLC"\n'
    )
    calls = []

    def fake_pactl(cmd):
        calls.append(cmd)
        if cmd[:3] == ["pactl", "list", "sink-inputs"]:
            return 0, pactl_sample
        return 0, ""

    try:
        with voice.PipeWireAudioDucker(enabled=True, duck_pct=25, runner=fake_pactl, has_pactl=True):
            raise RuntimeError("simulated barge-in")
    except RuntimeError:
        pass
    # Only Sink Input #11 (Spotify @ 80%) should be ducked to 25% and then restored to 80%!
    assert ["pactl", "set-sink-input-volume", "11", "25%"] in calls
    assert ["pactl", "set-sink-input-volume", "11", "80%"] in calls
    assert not any("12" in c for c in calls)  # skipped self aplay
    assert not any("13" in c for c in calls)  # skipped corked stream
    assert not any("14" in c for c in calls)  # already below 25%

    # 3. SileroOnnxVAD handles 512-sample frame slicing & state updates (v5 session mock)
    class _FakeInput:
        def __init__(self, name): self.name = name

    class _FakeSileroV5:
        def __init__(self, prob):
            self.prob = prob
            self.calls = 0

        def get_inputs(self):
            return [_FakeInput("input"), _FakeInput("state"), _FakeInput("sr")]

        def run(self, _out, feeds):
            self.calls += 1
            assert feeds["input"].shape == (1, 512)
            return ([self.prob], feeds["state"])

    try:
        import numpy as np

        sess_hi = _FakeSileroV5(0.85)
        vad = voice.SileroOnnxVAD(sess_hi, threshold=0.50, sample_rate=16000)
        assert vad.is_speech(np.zeros(1600, dtype=np.float32)) is True
        assert sess_hi.calls == 3  # 1600 // 512 = 3 frames (64 tail samples carried over)

        sess_lo = _FakeSileroV5(0.12)
        vad_lo = voice.SileroOnnxVAD(sess_lo, threshold=0.50, sample_rate=16000)
        assert vad_lo.is_speech(np.zeros(1600, dtype=np.float32)) is False
    except ImportError:
        pass

    # 4. Kokoro opt-in guard: NEVER intercepts Telugu ('te'), safely returns False when uninstalled
    assert voice._try_speak_kokoro("te", "నమస్కారం", gen=0) is False
    assert voice._try_speak_kokoro("en", "Hello", gen=0) is False
    assert config.ACTION_GATE_ENABLED is True


def test_neural_schema_v42_to_v43_upgrade_preserves_user_and_unions_new_tools(tmp_path):
    import json
    from celina_pkg import config

    # Simulate an existing user's customized v4.2 schema on disk
    old_path = tmp_path / "neural-schema.json"
    old_path.write_text(
        json.dumps(
            {
                "schema_version": "4.2",
                "persona": {"name": "Nova", "nickname": "Novi"},
                "routing": {"local_model": "qwen3:4b"},
                "security_policy": {
                    "tier1_auto_tools": ["get_system_stats", "set_volume"],
                    "tier2_approval_tools": ["write_file"],
                },
                "privacy": {
                    "local_sensitive_tools": ["read_file"],
                },
            }
        ),
        encoding="utf-8",
    )
    migrated = config.load_neural_schema(old_path)
    # User customizations preserved
    assert migrated["persona"]["name"] == "Nova"
    assert migrated["persona"]["nickname"] == "Novi"
    assert migrated["routing"]["local_model"] == "qwen3:4b"
    # Schema upgraded to 4.4 with all 88 tools, 21 sensitive local tools & 8 clean domains!
    assert migrated["schema_version"] == "4.4"
    assert len(migrated["security_policy"]["tier1_auto_tools"]) == 60
    assert len(migrated["security_policy"]["tier2_approval_tools"]) == 42
    assert "get_upcoming_events" in migrated["privacy"]["local_sensitive_tools"]
    assert "prepare_meeting_context" in migrated["privacy"]["local_sensitive_tools"]
    assert "transcribe_system_audio" in migrated["privacy"]["local_sensitive_tools"]
    assert "apple_pim_writing" in migrated["tool_domains"]
    assert "continuity_audio_privacy" in migrated["tool_domains"]

    # Current v4.4 user policy edits (e.g. intentionally disabling a tool) ARE strictly respected!
    v44_path = tmp_path / "v44.json"
    v44_path.write_text(
        json.dumps(
            {
                "schema_version": "4.4",
                "security_policy": {
                    "tier1_auto_tools": ["get_system_stats"],
                    "tier2_approval_tools": [],
                },
            }
        ),
        encoding="utf-8",
    )
    strict_v44 = config.load_neural_schema(v44_path)
    assert strict_v44["security_policy"]["tier1_auto_tools"] == ["get_system_stats"]


def test_ebbinghaus_memory_decay_and_kg_contradiction_resolution(tmp_path):
    import time
    from celina_pkg.memory import MemoryDB, compute_ebbinghaus_decay

    now = time.time()
    # Fresh fact decays much slower than 180-day-old stale fact
    fresh_score = compute_ebbinghaus_decay(updated_at=now - 86400, use_count=1, now=now)
    stale_score = compute_ebbinghaus_decay(updated_at=now - 180 * 86400, use_count=5, now=now)
    assert fresh_score > stale_score * 3.0

    db = MemoryDB(db_path=tmp_path / "mem.db")
    try:
        db.set_fact("deploy_target_old", "production server runs Ubuntu 22.04")
        db.set_fact("deploy_target_new", "production server runs Ubuntu 26.04 Wayland")
        # Age the old fact 240 days with high historical use_count
        with db._lock, db._conn:
            db._conn.execute(
                "UPDATE facts SET updated_at = ?, last_used_at = ?, use_count = 40 WHERE key = ?",
                (now - 240 * 86400, now - 240 * 86400, "deploy_target_old"),
            )
            db._conn.execute(
                "UPDATE facts SET updated_at = ?, last_used_at = ?, use_count = 1 WHERE key = ?",
                (now - 3600, now - 3600, "deploy_target_new"),
            )
        recalled_facts, _ = db.recall_relevant("production server Ubuntu", max_facts=2, now=now)
        assert recalled_facts[0][0] == "deploy_target_new"

        # Functional KG predicate auto-superseding: old conflicting status demoted to confidence=0.2
        r1 = db.add_knowledge_triple("ProjectApollo", "status", "in progress")
        assert r1["superseded"] == 0
        r2 = db.add_knowledge_triple("ProjectApollo", "status", "shipped to production")
        assert r2["superseded"] == 1

        active_links = db.query_knowledge_graph("ProjectApollo", hops=1)
        assert len(active_links) == 1
        assert active_links[0]["object"] == "shipped to production"
        assert active_links[0]["confidence"] == 1.0

        # Superseded truth preserved in history when explicitly requested
        all_links = db.query_knowledge_graph("ProjectApollo", hops=1, include_superseded=True)
        assert len(all_links) == 2
        old_entry = [x for x in all_links if x["object"] == "in progress"][0]
        assert abs(old_entry["confidence"] - 0.2) < 1e-6
    finally:
        db.close()


def test_compound_meeting_brief_app_intent_zero_llm_tokens(tmp_path):
    import datetime
    from celina_pkg import app_intents
    from celina_pkg.memory import MemoryDB

    # 1. Synthetic upcoming .ics event
    ics = tmp_path / "calendar.ics"
    now = datetime.datetime(2026, 11, 14, 9, 0)
    ics.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
        "UID:mtg-1@celina\n"
        "DTSTART:20261114T110000\n"
        "DTEND:20261114T120000\n"
        "SUMMARY:Atlas Architecture Review with Priya\n"
        "LOCATION:Zoom Room B\n"
        "DESCRIPTION:Finalize Q4 latency roadmap with Priya Nair.\n"
        "END:VEVENT\nEND:VCALENDAR\n",
        encoding="utf-8",
    )
    # 2. Synthetic vCard contact
    vdir = tmp_path / "contacts"
    vdir.mkdir()
    (vdir / "priya.vcf").write_text(
        "BEGIN:VCARD\nVERSION:3.0\nFN:Priya Nair\nORG:Atlas Systems\n"
        "EMAIL:priya@atlas.example\nTEL:+91-9876543210\nEND:VCARD\n",
        encoding="utf-8",
    )
    # 3. Synthetic memory fact & KG link
    db = MemoryDB(db_path=tmp_path / "mem.db")
    db.set_fact("atlas_target", "Keep Atlas P95 latency under 40ms")
    db.add_knowledge_triple("Atlas", "status", "ready for Q4 review")
    # 4. Synthetic local notes file
    docs = tmp_path / "Documents"
    docs.mkdir()
    note_file = docs / "Atlas_Architecture_Notes.md"
    note_file.write_text("# Atlas Notes\n", encoding="utf-8")

    try:
        brief = app_intents.prepare_meeting_context(
            query="Atlas",
            ics_path=ics,
            evolution_db=tmp_path / "missing_evo.db",
            vcard_dir=vdir,
            search_dirs=[docs],
            db=db,
            now=now,
        )
        assert "Atlas Architecture Review with Priya" in brief
        assert "Priya Nair" in brief and "priya@atlas.example" in brief
        assert "40ms" in brief
        assert "Atlas_Architecture_Notes.md" in brief
    finally:
        db.close()


def test_python_sandbox_self_verifying_and_system_audio_loopback(tmp_path, monkeypatch):
    from celina_pkg import router, tools, voice

    # 1. Sandboxed Python denies without approval_cb
    assert tools.run_python_sandbox("print(1+1)", approval_cb=None).startswith("Denied:")

    # 2. Approved Python math executes cleanly & returns exit=0
    ok_out = tools.run_python_sandbox("print(sum(range(1, 11)))", timeout=5, approval_cb=lambda _t, _d: True)
    assert "Verified OK (exit=0)" in ok_out
    assert "55" in ok_out

    # 3. Runtime traceback returns Error: (...) and triggers Router Engineer self-correction hint!
    err_out = tools.run_python_sandbox("print(1 / 0)", timeout=5, approval_cb=lambda _t, _d: True)
    assert err_out.startswith("Error:")
    assert "ZeroDivisionError" in err_out
    assert router._is_tool_error(err_out) is True
    hint = router._build_self_correction_hint("run_python_sandbox", err_out, dup_failures=1)
    assert "ENGINEER CODE SELF-CORRECTION" in hint

    # 4. PipeWire loopback audio transcriber cleans up temp WAV and requires approval via tool
    fake_wav = tmp_path / "loopback.wav"
    fake_wav.write_bytes(b"RIFF" + b"\x00" * 64)
    monkeypatch.setattr(voice, "record_system_audio", lambda seconds=30, monitor_source=None: (str(fake_wav), None))
    monkeypatch.setattr(voice, "transcribe_in_subprocess", lambda p, lang_hint=None, timeout=60: ("Launch date moved to Friday.", "en", None))

    # Denied without approval
    denied = tools.transcribe_system_audio_tool(seconds=10, approval_cb=lambda _t, _d: False)
    assert "Cancelled" in denied or "Denied" in denied

    # Approved runs offline whisper and deletes temp wav
    transcript = tools.transcribe_system_audio_tool(seconds=10, approval_cb=lambda _t, _d: True)
    assert "Launch date moved to Friday." in transcript
    assert not fake_wav.exists()


def test_deeper_ubuntu_os_integration_systemd_vault_usb_mime(tmp_path):
    from celina_pkg import command_runner, os_events

    # 1. Systemd Crash & Journal Doctor validates unit names & redacts secrets from logs
    assert "Invalid systemd unit name" in os_events.diagnose_systemd_service("foo; rm -rf /")

    def fake_sys_runner(cmd):
        if cmd[:3] == ["systemctl", "--user", "--failed"]:
            return command_runner.CommandResult(0, "uxplay-user.service loaded failed failed AirPlay\n", "", command=tuple(cmd))
        if cmd[:3] == ["systemctl", "--user", "status"]:
            return command_runner.CommandResult(3, "Active: failed (Result: exit-code)", "", command=tuple(cmd))
        if cmd[0] == "journalctl":
            return command_runner.CommandResult(0, "Failed with token sk-or-v1-" + "1234567890abcdef" * 4, "", command=tuple(cmd))
        return command_runner.CommandResult(0, "", "", command=tuple(cmd))

    failed = os_events.get_failed_user_services(runner=fake_sys_runner, which=lambda b: f"/usr/bin/{b}")
    assert failed == ["uxplay-user.service"]
    diag = os_events.diagnose_systemd_service("uxplay-user.service", runner=fake_sys_runner, which=lambda b: f"/usr/bin/{b}")
    assert "uxplay-user.service" in diag
    assert "sk-or-v1-1234567890" not in diag  # secret redacted!

    # 2. Freedesktop Secret Service status (strictly Locked boolean, zero secret reads)
    def fake_gdbus(cmd):
        joined = " ".join(cmd)
        if "Collections" in joined:
            return command_runner.CommandResult(0, "([objectpath '/org/freedesktop/secrets/collection/login'],)", "", command=tuple(cmd))
        if "Locked" in joined:
            return command_runner.CommandResult(0, "(<true>,)", "", command=tuple(cmd))
        if "Label" in joined:
            return command_runner.CommandResult(0, "(<'KeePassXC'>,)", "", command=tuple(cmd))
        if "Service.Unlock" in joined:
            return command_runner.CommandResult(0, "([], objectpath '/org/freedesktop/secrets/prompt/p1')", "", command=tuple(cmd))
        return command_runner.CommandResult(1, "", "err", command=tuple(cmd))

    vault_txt = os_events.get_secret_vault_status(unlock=True, runner=fake_gdbus, which=lambda b: f"/usr/bin/{b}")
    assert "KeePassXC" in vault_txt and "LOCKED" in vault_txt
    assert "Triggered native OS vault unlock prompt" in vault_txt

    # 3. UDisks2 USB Drive Alert formatting & live_os_events.json emission
    mon = os_events.OSEventMonitor(state_file=tmp_path / "live_os_events.json")
    alert = mon.record_usb_alert("SanDisk_Backup", 32_000_000_000, "/media/user/SanDisk")
    assert "32 GB" in alert["banner"]
    mon.record_failed_unit("syncthing.service", failed=True)
    snap = mon.poll_once()
    assert snap["usb_alert"]["size_human"] == "32 GB"
    assert "syncthing.service" in snap["failed_services"]

    # 4. MIME & Default Handler Inspector (friendly aliases + bound approval on set)
    assert os_events.resolve_mime_type("web") == "x-scheme-handler/https"
    assert os_events.resolve_mime_type("pdf") == "application/pdf"
    calls = []

    def fake_gio(cmd):
        calls.append(cmd)
        if cmd == ["gio", "mime", "application/pdf"]:
            return command_runner.CommandResult(0, "Default application for 'application/pdf': org.gnome.Evince.desktop", "", command=tuple(cmd))
        return command_runner.CommandResult(0, "", "", command=tuple(cmd))

    got = os_events.manage_default_apps("get", "pdf", runner=fake_gio, which=lambda b: f"/usr/bin/{b}")
    assert "org.gnome.Evince.desktop" in got
    assert "Invalid MIME" in os_events.manage_default_apps("get", "not a mime;rm", runner=fake_gio, which=lambda b: f"/usr/bin/{b}")
    denied_set = os_events.manage_default_apps("set", "pdf", "firefox.desktop", runner=fake_gio, which=lambda b: f"/usr/bin/{b}", approval_cb=lambda _t, _d: False)
    assert "Cancelled" in denied_set or "Denied" in denied_set
    ok_set = os_events.manage_default_apps("set", "pdf", "firefox.desktop", runner=fake_gio, which=lambda b: f"/usr/bin/{b}", approval_cb=lambda _t, _d: True)
    assert "Verified:" in ok_set
    assert ["gio", "mime", "application/pdf", "firefox.desktop"] in calls


def test_complete_ios_continuity_usb_camera_weylus_ancs_and_88_tool_routing(monkeypatch):
    from celina_pkg import continuity, ios_ancs, tools

    # 1. iOS USB mount blocks path escape outside $HOME
    assert "must be inside your home directory" in continuity.mount_ios_device("mount", "/tmp/escape")

    # 2. Continuity Camera validates V4L2 device node strictly
    monkeypatch.setattr(continuity.shutil, "which", lambda b: f"/usr/bin/{b}")
    assert "must be a valid V4L2 node" in continuity.start_ios_continuity_camera("/dev/sda; id")

    # 3. Weylus Sidecar approval guard & status
    denied_w = continuity.start_ipad_sidecar_weylus("start", 1701, approval_cb=lambda _t, _d: False)
    assert "Cancelled" in denied_w or "Denied" in denied_w

    # 4. ANCS respond_ios_notification requires approval and dispatches positive/negative
    called = []
    monkeypatch.setattr(ios_ancs, "perform_ios_notification_action", lambda positive=False: (called.append(positive), f"ANCS positive={positive}")[1])
    res_ans = continuity.respond_ios_notification("accept", approval_cb=lambda _t, _d: True)
    assert "positive=True" in res_ans and called == [True]

    # 5. All 10 new v4.4 tools route accurately through get_contextual_tool_schemas!
    v44_prompts = [
        ("prepare for my next meeting with Priya", "prepare_meeting_context"),
        ("transcribe what was just said in this meeting audio", "transcribe_system_audio"),
        ("run python code in sandbox to verify my formula", "run_python_sandbox"),
        ("diagnose failed systemd service uxplay", "diagnose_systemd_service"),
        ("is my keepassxc secret vault locked?", "get_secret_vault_status"),
        ("make firefox my default pdf app", "manage_default_apps"),
        ("mount my iphone over usb into ~/iPhone", "mount_ios_device"),
        ("start iphone continuity camera webcam", "start_ios_continuity_camera"),
        ("start weylus ipad sidecar with apple pencil", "start_ipad_sidecar_weylus"),
        ("dismiss my iphone notification", "respond_ios_notification"),
    ]
    for prompt, expected_tool in v44_prompts:
        pruned, _doms = tools.get_contextual_tool_schemas(prompt, gear="tactical")
        names = {t["function"]["name"] for t in pruned}
        assert expected_tool in names, f"Expected '{expected_tool}' for prompt '{prompt}', got {sorted(names)}"


def _make_semantic_test_embedder():
    """Deterministic lightweight semantic embedder mapping conceptual clusters across EN/TE/HI."""
    clusters = [
        # 0: auto/car/insurance (EN + Telugu + Hindi)
        ("car", "auto", "vehicle", "insurance", "policy", "renewal", "కారు", "పాలసీ", "బీమా", "गाड़ी", "बीमा"),
        # 1: display brightness / glare
        ("brightness", "blinding", "glare", "dim", "backlight", "luminance"),
        # 2: git / rebase / version control
        ("git", "rebase", "commit", "branch", "merge"),
        # 3: cloud / infrastructure / budget / spend
        ("cloud", "infra", "infrastructure", "budget", "spend", "expense", "cost", "quarterly"),
        # 4: secret / private medical
        ("medical", "prescription", "diagnosis", "blood"),
    ]

    def _embed(texts):
        out = []
        for t in texts:
            low = (t or "").lower()
            vec = [0.0] * 16
            for idx, kws in enumerate(clusters):
                hits = sum(1 for kw in kws if kw in low)
                if hits:
                    vec[idx] = float(hits)
            # Ensure non-zero vector for unrelated text using hash bucket in high dims
            if not any(vec):
                h = sum(ord(ch) for ch in low) % 10
                vec[6 + h] = 1.0
            out.append(vec)
        return out

    return _embed


def test_embeddings_core_rrf_float16_and_offline_fallback():
    from celina_pkg import embeddings

    # 1. Float16 pack/unpack round-trip & Matryoshka truncation
    raw = [3.0, 4.0, 0.0, 0.0, 1.0, 2.0]
    blob = embeddings.pack_vector(raw, target_dim=4)
    unpacked = embeddings.unpack_vector(blob)
    assert len(unpacked) == 4
    assert abs(unpacked[0] - 0.6) < 0.01
    assert abs(unpacked[1] - 0.8) < 0.01

    # 2. RRF fusion promotes consensus items ranked high by both BM25 and Vector legs
    fused = embeddings.rrf_fuse(["docA", "docC", "docB"], ["docB", "docA", "docD"], k=60)
    top_ids = [doc_id for doc_id, _ in fused]
    assert top_ids[0] in {"docA", "docB"}
    assert set(top_ids[:2]) == {"docA", "docB"}

    # 3. Engine mode='off' degrades silently in <1ms
    off_eng = embeddings.EmbeddingEngine(engine="off")
    assert off_eng.embed_one("hello") is None


def test_hybrid_memory_cross_lingual_recall_and_privacy_isolation(tmp_path):
    from celina_pkg import embeddings
    from celina_pkg.memory import MemoryDB

    eng = embeddings.EmbeddingEngine(embed_fn=_make_semantic_test_embedder(), dim=16)
    embeddings.set_default_engine(eng)
    db = MemoryDB(db_path=tmp_path / "hybrid_mem.db")
    try:
        db.set_fact("car_insurance", "car insurance expires Dec 5", cloud_safe=True)
        db.set_fact("private_medical", "medical prescription refill dosage", cloud_safe=False)
        db.add_knowledge_triple("Civic", "status", "car insurance active", cloud_safe=True)

        # Zero-shared-word Telugu query matches English fact purely via Vector + RRF leg!
        recalled_facts, _ = db.recall_relevant("కారు పాలసీ ఎప్పుడు?", max_facts=2, cloud_only=True)
        assert recalled_facts[0][0] == "car_insurance"

        # Strict privacy isolation: cloud_only=True MUST NEVER leak private_medical via vectors!
        priv_cloud, _ = db.recall_relevant("diagnosis blood prescription", max_facts=3, cloud_only=True)
        assert all(k != "private_medical" for k, _ in priv_cloud)
        priv_local, _ = db.recall_relevant("diagnosis blood prescription", max_facts=3, cloud_only=False)
        assert any(k == "private_medical" for k, _ in priv_local)

        # Superseded KG triple deletes old vector so stale fact doesn't leak back via vectors
        db.add_knowledge_triple("Civic", "status", "sold vehicle", cloud_safe=True)
        kg_hits = db.recall_knowledge_triples("policy renewal", limit=3, cloud_only=True)
        assert not any("car insurance active" in h for h in kg_hits)
    finally:
        db.close()
        embeddings.set_default_engine(None)


def test_semantic_tool_router_semantic_cache_and_hybrid_spotlight(tmp_path):
    from celina_pkg import cache, embeddings, spotlight_index, tools

    eng = embeddings.EmbeddingEngine(embed_fn=_make_semantic_test_embedder(), dim=16)
    embeddings.set_default_engine(eng)
    tools._SEMANTIC_TOOL_INDEX.invalidate()
    try:
        # 1. Semantic Tool Router rescues `set_brightness` for paraphrase with ZERO keyword overlap!
        pruned, doms = tools.get_contextual_tool_schemas("this glare is blinding me", gear="fast")
        names = {t["function"]["name"] for t in pruned}
        assert "set_brightness" in names
        assert "semantic_top" in doms or "os_hardware(fast)" in doms

        # 2. Semantic Paraphrase Cache hits on rephrased non-private query, still blocks secrets/PII
        sc = cache.SmartCache(db_path=tmp_path / "sem_cache.db")
        try:
            assert sc.set("llm_fast", "explain how git rebase works", "Git rebase replays commits onto a new base.") is True
            hit = sc.get("llm_fast", "can you explain git rebase and branch commits")
            assert hit is not None
            assert "Git rebase replays commits" in hit[0]
            assert "semantic sim=" in hit[1]
            # Secrets still strictly refused
            assert sc.set("llm_fast", "my key", "sk-or-v1-" + "1234567890abcdef" * 4) is False
        finally:
            sc.close()

        # 3. Hybrid CoreSpotlight retrieves file by chunk meaning even when filename has zero word overlap
        docs = tmp_path / "Docs"
        docs.mkdir()
        target = docs / "Q4_memo.md"
        target.write_text("Quarterly cloud infrastructure budget cap is twelve thousand dollars.\n", encoding="utf-8")
        idx = spotlight_index.SpotlightIndex(db_path=tmp_path / "spot.db")
        try:
            idx.refresh(roots=[docs])
            n_chunks = idx.index_document_chunks(roots=[docs])
            assert n_chunks >= 1
            hits = idx.search_hybrid("monthly expense cost", limit=5)
            assert any(h["name"] == "Q4_memo.md" for h in hits)
        finally:
            idx.close()
    finally:
        tools._SEMANTIC_TOOL_INDEX.invalidate()
        embeddings.set_default_engine(None)


def test_v44_audit_edge_cases_lang_cache_uxplay_ocr_window_infer_target(tmp_path, monkeypatch):
    from celina_pkg import approval, automation, cache, continuity, embeddings

    # 1. Semantic Cache: language-partitioned (EN never collides with TE/HI) + 1 embed call on miss -> set!
    embed_calls = []

    def counting_embed(texts):
        embed_calls.extend(texts)
        return _make_semantic_test_embedder()(texts)

    embeddings.set_default_engine(embeddings.EmbeddingEngine(embed_fn=counting_embed, dim=16))
    sc = cache.SmartCache(db_path=tmp_path / "lang_cache.db")
    try:
        # Miss embeds once; subsequent set() reuses memoized vector (still 1 embed call total!)
        assert sc.get("llm_fast", "fast:en:explain how git rebase works") is None
        assert len(embed_calls) == 1
        assert sc.set("llm_fast", "fast:en:explain how git rebase works", "English git rebase answer") is True
        assert len(embed_calls) == 1  # zero duplicate embed call!

        # Same-language English paraphrase hits cleanly
        hit_en = sc.get("llm_fast", "fast:en:explain git rebase and branch commits")
        assert hit_en is not None and "English git rebase answer" in hit_en[0]

        # Different language ('te' / 'hi') MUST NOT return cached English answer!
        assert sc.get("llm_fast", "fast:te:explain how git rebase works") is None
        assert sc.get("llm_fast", "fast:hi:explain git rebase and branch commits") is None
    finally:
        sc.close()
        embeddings.set_default_engine(None)

    # 2. Continuity Camera pauses active uxplay-user.service before spawning v4l2sink & supports stop
    stopped_units = []
    spawned_cmds = []

    class _FakeStatus:
        value = "healthy"

    class _FakeState:
        status = _FakeStatus()

    class _FakeSD:
        def get_unit_state(self, u):
            return _FakeState()

        def control_unit(self, u, act):
            stopped_units.append((u, act))

    class _FakeHW:
        systemd_user = _FakeSD()

    class _FakeHWMod:
        @staticmethod
        def get_hardware():
            return _FakeHW()

    monkeypatch.setattr(continuity, "_celina_hardware", _FakeHWMod)
    monkeypatch.setattr(continuity.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(continuity.Path, "exists", lambda self: str(self) == "/dev/video10")
    monkeypatch.setattr(continuity.subprocess, "Popen", lambda cmd, **kw: spawned_cmds.append(cmd))
    cam_msg = continuity.start_ios_continuity_camera("/dev/video10", approval_cb=lambda _t, _d: True)
    assert ("uxplay-user.service", "stop") in stopped_units
    assert "paused uxplay-user.service" in cam_msg
    assert spawned_cmds and "v4l2sink device=/dev/video10" in spawned_cmds[0]

    # 3. OCR fallback in click_on_text enforces expected_window=target_window & passes _broker_checked=True
    automation.clear_stop()
    monkeypatch.setattr(automation, "get_atspi_elements", lambda max_elements=100: [])
    monkeypatch.setattr(
        automation,
        "_run_ocr_on_screen",
        lambda: ([automation.OcrBox("Submit", 120, 240, 60, 20, 95.0, (1, 1, 1))], (1920, 1080), None),
    )
    monkeypatch.setattr(automation, "get_active_window_info", lambda: {"app": "Firefox", "title": "Wrong Window"})
    ocr_blocked = automation.click_on_text(
        "Submit", target_window="LibreOffice", approval_cb=lambda _t, _d: True
    )
    assert "expected" in ocr_blocked.lower() or "window" in ocr_blocked.lower() or "mismatch" in ocr_blocked.lower()

    # 4. infer_target() resolves v4.4 tool parameter keys cleanly
    assert approval.infer_target("mount_ios_device", {"mount_dir": "/home/user/iPhone"}) == "/home/user/iPhone"
    assert approval.infer_target("start_ios_continuity_camera", {"video_device": "/dev/video10"}) == "/dev/video10"
    assert approval.infer_target("manage_default_apps", {"mime_type": "application/pdf"}) == "application/pdf"
    assert approval.infer_target("create_calendar_event", {"title": "Sprint Review"}) == "Sprint Review"
    assert approval.infer_target("run_python_sandbox", {"code": "print(1)"}) == "sandbox-shell"


def test_v44_deep_hardening_microcache_spotlight_secrets_empty_fastpath_and_targets(tmp_path):
    from celina_pkg import approval, embeddings, memory, spotlight_index

    embed_calls: list[str] = []

    def counting_embed(texts):
        embed_calls.extend(texts)
        return _make_semantic_test_embedder()(texts)

    eng = embeddings.EmbeddingEngine(embed_fn=counting_embed, dim=16)
    embeddings.set_default_engine(eng)
    db = memory.MemoryDB(db_path=tmp_path / "empty_fast.db")
    idx = spotlight_index.SpotlightIndex(db_path=tmp_path / "spot_sec.db")
    try:
        # 1. Empty memory_vectors & empty doc_chunks MUST NOT trigger any embedding calls!
        db.format_relevant_memory_for_prompt("car policy check", cloud_only=True)
        idx.search_hybrid("monthly expense cost", limit=5)
        assert len(embed_calls) == 0

        # 2. Per-turn micro-cache deduplicates repeated embed_one() across memory + KG + offload clears it
        db.set_fact("car_insurance", "car insurance expires Dec 5", cloud_safe=True)
        db.add_knowledge_triple("Civic", "status", "car insurance active", cloud_safe=True)
        before = len(embed_calls)
        db.format_relevant_memory_for_prompt("car insurance renewal", cloud_only=True)
        db.recall_relevant("car insurance renewal", cloud_only=True)
        db.recall_knowledge_triples("car insurance renewal", cloud_only=True)
        assert len(embed_calls) - before == 1  # single embed shared across all 3 calls!

        assert eng.offload() is True  # clears micro-cache cleanly
        before2 = len(embed_calls)
        eng.embed_one("car insurance renewal")
        assert len(embed_calls) - before2 == 1  # re-embedded after offload

        # 3. model_fingerprint tracks resolved active_backend (prevents auto-mode cross-space mismatch)
        auto_eng = embeddings.EmbeddingEngine(engine="auto", model="embeddinggemma", dim=256)
        auto_eng._active_backend = "ollama:embeddinggemma"
        fp1 = auto_eng.model_fingerprint
        auto_eng._active_backend = "model2vec:minishlab/potion-multilingual-128M"
        fp2 = auto_eng.model_fingerprint
        assert fp1 != fp2 and "ollama:embeddinggemma:256" == fp1

        # 4. Spotlight chunk indexer skips secret files, redacts inline secrets & prunes deleted files
        docs = tmp_path / "Docs"
        docs.mkdir()
        (docs / "aws-credentials.json").write_text('{"secret": "super-secret-value-123456"}\n', encoding="utf-8")
        (docs / "notes.md").write_text(
            f"Quarterly cloud infrastructure budget cap with token {"sk-or-v1-" + "1234567890abcdef" * 4}.\n",
            encoding="utf-8",
        )
        ephemeral = docs / "temp_memo.md"
        ephemeral.write_text("Monthly cloud expense report for October operations.\n", encoding="utf-8")

        n_chunks = idx.index_document_chunks(roots=[docs])
        assert n_chunks >= 2
        rows = idx._conn.execute("SELECT path, snippet FROM doc_chunks").fetchall()
        paths = {str(r["path"]) for r in rows}
        snippets = " ".join(str(r["snippet"]) for r in rows)
        assert not any("aws-credentials.json" in p for p in paths)
        assert "sk-or-v1-" not in snippets  # secret redacted before SQLite persistence!

        # Delete temp_memo.md -> search_hybrid must immediately stop returning it & re-index prunes it
        ephemeral.unlink()
        hits_after_del = idx.search_hybrid("monthly expense report", limit=5)
        assert all(h["name"] != "temp_memo.md" for h in hits_after_del)
        idx.index_document_chunks(roots=[docs])
        rem_paths = {str(r["path"]) for r in idx._conn.execute("SELECT path FROM doc_chunks").fetchall()}
        assert not any("temp_memo.md" in p for p in rem_paths)

        # 5. Expanded approval.infer_target() coverage
        assert approval.infer_target("diagnose_systemd_service", {"unit_name": "uxplay-user.service"}) == "uxplay-user.service"
        assert approval.infer_target("approve_quarantined_skill", {"slug": "my_skill"}) == "my_skill"
        assert approval.infer_target("launch_android_mirror", {"serial": "emulator-5554"}) == "emulator-5554"
        assert approval.infer_target("autonomous_gui_task", {"goal": "Open LibreOffice Calc"}) == "Open LibreOffice Calc"
        assert approval.infer_target("transcribe_system_audio", {"seconds": 30}) == "system-audio"
        assert approval.infer_target("start_ipad_sidecar_weylus", {"action": "start"}) == "ipad-sidecar"
        assert approval.infer_target("control_airplay_mirror", {"action": "start"}) == "airplay-mirror"
        assert approval.infer_target("respond_ios_notification", {"action": "dismiss"}) == "ios-ancs"
    finally:
        idx.close()
        db.close()
        embeddings.set_default_engine(None)








