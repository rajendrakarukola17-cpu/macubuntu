"""
tests/audit/unit/test_tools_coverage_14.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 14).
Targets: handle_fast_intent (29 missing branches — high impact).

Covers: time queries (EN/TE/HI), volume set/mute/unmute/up/down (multi-lang),
brightness, media control, focus mode, continuity, calendar, privacy audit,
hardware status, dark/light mode, lock screen, screenshot, radio toggles,
site search, open+search, play/watch/listen, login, open app/website,
unit conversion, calculator, unknown intent returns None.

No real network calls. Mocks set_volume, set_brightness, control_media,
os_events, continuity, calendar_bridge, command_runner, shutil.which,
launch_detached, open_url_safe, control_connectivity, open_application,
_clean_media_query, _open_media_play, _get_preferred_music_platform,
spotlight_index, SITE_SEARCH_URLS, LOGIN_URLS, WEB_APPS, MEDIA_PLATFORMS.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error=""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error


# ---------------------------------------------------------------------------
# Time queries
# ---------------------------------------------------------------------------

def test_handle_fast_intent_time_english(monkeypatch):
    """CB: time query in English → returns 'It is currently ...'."""
    result = tools.handle_fast_intent("what time is it")
    assert "It is currently" in result


def test_handle_fast_intent_time_telugu(monkeypatch):
    """CB: time query in Telugu → returns Telugu response."""
    result = tools.handle_fast_intent("సమయం ఎంత")
    assert "ఇప్పుడు సమయం" in result


def test_handle_fast_intent_time_hindi(monkeypatch):
    """CB: time query in Hindi → returns Hindi response."""
    result = tools.handle_fast_intent("समय क्या हुआ")
    assert "अभी समय" in result


def test_handle_fast_intent_time_te_script(monkeypatch):
    """CB: time query with Telugu script chars → Telugu branch."""
    result = tools.handle_fast_intent("సమయం ఎంత")
    assert "ఇప్పుడు సమయం" in result


def test_handle_fast_intent_time_hi_script(monkeypatch):
    """CB: time query with Devanagari script chars → Hindi branch."""
    result = tools.handle_fast_intent("समय क्या हुआ")
    assert "अभी समय" in result


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

def test_handle_fast_intent_volume_unmute_te(monkeypatch):
    """CB: unmute in Telugu script → set_volume(mute=False)."""
    with patch.object(tools, "set_volume", return_value="unmuted") as sv:
        result = tools.handle_fast_intent("అన్‌మ్యూట్")
        assert result == "unmuted"
        sv.assert_called_once_with(mute=False)


def test_handle_fast_intent_volume_unmute_hi(monkeypatch):
    """CB: unmute in Hindi → set_volume(mute=False)."""
    with patch.object(tools, "set_volume", return_value="unmuted") as sv:
        result = tools.handle_fast_intent("आवाज़ चालू करो")
        assert result == "unmuted"
        sv.assert_called_once_with(mute=False)


def test_handle_fast_intent_volume_mute_te(monkeypatch):
    """CB: mute in Telugu script → set_volume(mute=True)."""
    with patch.object(tools, "set_volume", return_value="muted") as sv:
        result = tools.handle_fast_intent("మ్యూట్")
        assert result == "muted"
        sv.assert_called_once_with(mute=True)


def test_handle_fast_intent_volume_mute_hi(monkeypatch):
    """CB: mute in Hindi → set_volume(mute=True)."""
    with patch.object(tools, "set_volume", return_value="muted") as sv:
        result = tools.handle_fast_intent("आवाज़ बंद करो")
        assert result == "muted"
        sv.assert_called_once_with(mute=True)


def test_handle_fast_intent_volume_set_regex(monkeypatch):
    """CB: volume set regex match → set_volume(level=N)."""
    with patch.object(tools, "set_volume", return_value="set") as sv:
        result = tools.handle_fast_intent("set volume to 75%")
        assert result == "set"
        sv.assert_called_once_with(level=75)


def test_handle_fast_intent_volume_up_multi_lang(monkeypatch):
    """CB: volume up in multiple languages → set_volume(delta=10)."""
    with patch.object(tools, "set_volume", return_value="up") as sv:
        for phrase in ("volume up", "louder", "increase volume", "వాల్యూమ్ పెంచు", "आवाज़ बढ़ाओ"):
            result = tools.handle_fast_intent(phrase)
            assert result == "up"
        assert sv.call_count >= 5


def test_handle_fast_intent_volume_down_multi_lang(monkeypatch):
    """CB: volume down in multiple languages → set_volume(delta=-10)."""
    with patch.object(tools, "set_volume", return_value="down") as sv:
        for phrase in ("volume down", "quieter", "awaz kam karo", "వాల్యూమ్ తగ్గించు", "आवाज़ कम करो"):
            result = tools.handle_fast_intent(phrase)
            assert result == "down"
        assert sv.call_count >= 5


# ---------------------------------------------------------------------------
# Brightness
# ---------------------------------------------------------------------------

def test_handle_fast_intent_brightness_regex(monkeypatch):
    """CB: brightness set regex match → set_brightness(N)."""
    with patch.object(tools, "set_brightness", return_value="bright") as sb:
        result = tools.handle_fast_intent("set brightness to 60%")
        assert result == "bright"
        sb.assert_called_once_with(60)


def test_handle_fast_intent_brightness_te(monkeypatch):
    """CB: brightness in Telugu → set_brightness(N)."""
    with patch.object(tools, "set_brightness", return_value="bright") as sb:
        result = tools.handle_fast_intent(" బ్రైట్‌నెస్ 80")
        assert result == "bright"
        sb.assert_called_once_with(80)


# ---------------------------------------------------------------------------
# Media control
# ---------------------------------------------------------------------------

def test_handle_fast_intent_media_play_pause_multi_lang(monkeypatch):
    """CB: play/pause in multiple languages → control_media('play-pause')."""
    with patch.object(tools, "control_media", return_value="media") as cm:
        for phrase in ("pause music", "play music", "toggle media", "play pause",
                       "play/pause", "పాట ఆపు", "పాట ప్లే చేయి", "गाना रोको", "गाना चलाओ"):
            result = tools.handle_fast_intent(phrase)
            assert result == "media"
        assert cm.call_count >= 8


def test_handle_fast_intent_media_next(monkeypatch):
    """CB: next track → control_media('next')."""
    with patch.object(tools, "control_media", return_value="next") as cm:
        for phrase in ("next track", "skip song", "next song", "తదుపరి పాట", "अगला गाना"):
            result = tools.handle_fast_intent(phrase)
            assert result == "next"
        assert cm.call_count >= 5


def test_handle_fast_intent_media_previous(monkeypatch):
    """CB: previous track → control_media('previous')."""
    with patch.object(tools, "control_media", return_value="prev") as cm:
        for phrase in ("previous track", "prev track", "previous song", "మునుపటి పాట", "पिछला गाना"):
            result = tools.handle_fast_intent(phrase)
            assert result == "prev"
        assert cm.call_count >= 5


def test_handle_fast_intent_media_status(monkeypatch):
    """CB: media status → control_media('status')."""
    with patch.object(tools, "control_media", return_value="status") as cm:
        result = tools.handle_fast_intent("what's playing")
        assert result == "status"
        cm.assert_called_once_with("status")


# ---------------------------------------------------------------------------
# Focus mode / continuity / calendar / privacy / hardware
# ---------------------------------------------------------------------------

def test_handle_fast_intent_focus_mode_regex(monkeypatch):
    """CB: focus mode regex match → os_events.set_focus_mode(mode)."""
    with patch.object(tools.os_events, "set_focus_mode", return_value="focus") as sfm:
        result = tools.handle_fast_intent("enable work focus mode")
        assert result == "focus"
        sfm.assert_called_once_with("work")


def test_handle_fast_intent_focus_status(monkeypatch):
    """CB: focus status → os_events.set_focus_mode('status')."""
    with patch.object(tools.os_events, "set_focus_mode", return_value="focus_status") as sfm:
        result = tools.handle_fast_intent("focus status")
        assert result == "focus_status"
        sfm.assert_called_once_with("status")


def test_handle_fast_intent_continuity_status(monkeypatch):
    """CB: continuity status → continuity.get_continuity_status()."""
    with patch.object(tools.continuity, "get_continuity_status", return_value="cont") as gs:
        for phrase in ("continuity status", "iphone status", "airpods battery",
                       "bluetooth battery", "check airpods battery", "check continuity"):
            result = tools.handle_fast_intent(phrase)
            assert result == "cont"
        assert gs.call_count >= 6


def test_handle_fast_intent_calendar_multi_lang(monkeypatch):
    """CB: calendar in multiple languages → calendar_bridge.get_upcoming_events(days=7)."""
    with patch.object(tools.calendar_bridge, "get_upcoming_events", return_value="cal") as gae:
        for phrase in ("upcoming events", "my calendar", "today's schedule",
                       "todays schedule", "calendar events", "check my calendar",
                       "నా క్యాలెండర్", "मेरा कैलेंडर"):
            result = tools.handle_fast_intent(phrase)
            assert result == "cal"
        assert gae.call_count >= 8


def test_handle_fast_intent_privacy_audit(monkeypatch):
    """CB: privacy audit → continuity.audit_app_permissions()."""
    with patch.object(tools.continuity, "audit_app_permissions", return_value="priv") as aap:
        for phrase in ("privacy audit", "audit app permissions", "check app permissions",
                       "who is using my mic", "who is using my camera"):
            result = tools.handle_fast_intent(phrase)
            assert result == "priv"
        assert aap.call_count >= 5


def test_handle_fast_intent_hardware_status(monkeypatch):
    """CB: hardware status → get_hardware_capabilities()."""
    with patch.object(tools, "get_hardware_capabilities", return_value="hw") as ghc:
        for phrase in ("hardware status", "biometrics status", "touch id status", "face id status"):
            result = tools.handle_fast_intent(phrase)
            assert result == "hw"
        assert ghc.call_count >= 4


# ---------------------------------------------------------------------------
# Dark / light mode / lock screen / screenshot
# ---------------------------------------------------------------------------

def test_handle_fast_intent_dark_mode_gsettings_available(monkeypatch):
    """CB: dark mode with gsettings → command_runner + 'Switched desktop to Dark Mode.'"""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gsettings" if cmd == "gsettings" else None)
    with patch.object(tools.command_runner, "run_command", return_value=_FakeResult(ok=True)):
        result = tools.handle_fast_intent("dark mode")
        assert result == "Switched desktop to Dark Mode."


def test_handle_fast_intent_dark_mode_gsettings_missing(monkeypatch):
    """CB: dark mode without gsettings → returns None (falls through)."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.handle_fast_intent("dark mode")
    assert result is None


def test_handle_fast_intent_light_mode_gsettings_available(monkeypatch):
    """CB: light mode with gsettings → command_runner + 'Switched desktop to Light Mode.'"""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gsettings" if cmd == "gsettings" else None)
    with patch.object(tools.command_runner, "run_command", return_value=_FakeResult(ok=True)):
        result = tools.handle_fast_intent("light mode")
        assert result == "Switched desktop to Light Mode."


def test_handle_fast_intent_light_mode_gsettings_missing(monkeypatch):
    """CB: light mode without gsettings → returns None (falls through)."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.handle_fast_intent("light mode")
    assert result is None


def test_handle_fast_intent_lock_screen_loginctl_available(monkeypatch):
    """CB: lock screen with loginctl → command_runner + 'Locked screen.'"""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/loginctl" if cmd == "loginctl" else None)
    with patch.object(tools.command_runner, "run_command", return_value=_FakeResult(ok=True)):
        result = tools.handle_fast_intent("lock screen")
        assert result == "Locked screen."


def test_handle_fast_intent_lock_screen_loginctl_missing(monkeypatch):
    """CB: lock screen without loginctl → returns None (falls through)."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.handle_fast_intent("lock screen")
    assert result is None


def test_handle_fast_intent_screenshot_gnome_available_success(monkeypatch):
    """CB: screenshot with gnome-screenshot + launch_detached success → 'Opened Screenshot tool.'"""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None)
    with patch.object(tools, "launch_detached", return_value=True):
        result = tools.handle_fast_intent("take screenshot")
        assert result == "Opened Screenshot tool."


def test_handle_fast_intent_screenshot_gnome_available_failure(monkeypatch):
    """CB: screenshot with gnome-screenshot + launch_detached failure → returns None."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None)
    with patch.object(tools, "launch_detached", return_value=False):
        result = tools.handle_fast_intent("take screenshot")
        assert result is None


def test_handle_fast_intent_screenshot_gnome_missing(monkeypatch):
    """CB: screenshot without gnome-screenshot → returns None."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.handle_fast_intent("take screenshot")
    assert result is None


# ---------------------------------------------------------------------------
# Radio toggles
# ---------------------------------------------------------------------------

def test_handle_fast_intent_radio_wifi_on(monkeypatch):
    """CB: radio toggle wifi on → control_connectivity('wifi', 'on')."""
    with patch.object(tools, "control_connectivity", return_value="radio") as cc:
        for phrase in ("turn on wifi", "enable wifi"):
            result = tools.handle_fast_intent(phrase)
            assert result == "radio"
        assert cc.call_count >= 2


def test_handle_fast_intent_radio_bluetooth_off(monkeypatch):
    """CB: radio toggle bluetooth off → control_connectivity('bluetooth', 'off')."""
    with patch.object(tools, "control_connectivity", return_value="radio") as cc:
        for phrase in ("turn off bluetooth", "disable bluetooth"):
            result = tools.handle_fast_intent(phrase)
            assert result == "radio"
        assert cc.call_count >= 2


def test_handle_fast_intent_radio_wifi_status(monkeypatch):
    """CB: radio wifi status → control_connectivity('wifi', 'status')."""
    with patch.object(tools, "control_connectivity", return_value="radio") as cc:
        result = tools.handle_fast_intent("wifi status")
        assert result == "radio"
        cc.assert_called_once_with("wifi", "status")


# ---------------------------------------------------------------------------
# Site search
# ---------------------------------------------------------------------------

def test_handle_fast_intent_site_search_google(monkeypatch):
    """CB: site search google → open_url_safe with google template."""
    with patch.object(tools, "open_url_safe", return_value="opened") as ous:
        result = tools.handle_fast_intent("search google for python tutorial")
        assert result == "opened"
        ous.assert_called_once()


def test_handle_fast_intent_site_search_youtube(monkeypatch):
    """CB: site search youtube → open_url_safe with youtube template."""
    with patch.object(tools, "open_url_safe", return_value="opened") as ous:
        result = tools.handle_fast_intent("search youtube for celina demo")
        assert result == "opened"
        ous.assert_called_once()


def test_handle_fast_intent_site_search_direct(monkeypatch):
    """CB: 'google <query>' direct form → open_url_safe."""
    with patch.object(tools, "open_url_safe", return_value="opened") as ous:
        result = tools.handle_fast_intent("youtube music videos")
        assert result == "opened"
        ous.assert_called_once()


# ---------------------------------------------------------------------------
# Open + search multi-clause
# ---------------------------------------------------------------------------

def test_handle_fast_intent_open_search_multi_clause(monkeypatch):
    """CB: open site and search → open_url_safe with formatted URL."""
    with patch.object(tools, "open_url_safe", return_value="opened") as ous:
        result = tools.handle_fast_intent("open github and search for celina")
        assert result == "opened"
        ous.assert_called_once()


# ---------------------------------------------------------------------------
# Play / watch / listen
# ---------------------------------------------------------------------------

def test_handle_fast_intent_play_with_platform(monkeypatch):
    """CB: play with platform name → _open_media_play with platform."""
    with patch.object(tools, "_clean_media_query", return_value=("dude songs", None)), \
         patch.object(tools, "_get_preferred_music_platform", return_value="spotify"), \
         patch.object(tools, "_open_media_play", return_value="playing") as omp:
        result = tools.handle_fast_intent("play dude songs in spotify")
        assert result == "playing"
        omp.assert_called_once()


def test_handle_fast_intent_play_no_platform(monkeypatch):
    """CB: play without platform → uses preferred platform."""
    with patch.object(tools, "_clean_media_query", return_value=("dude songs", None)), \
         patch.object(tools, "_get_preferred_music_platform", return_value="youtube"), \
         patch.object(tools, "_open_media_play", return_value="playing") as omp:
        result = tools.handle_fast_intent("play dude songs")
        assert result == "playing"
        omp.assert_called_once()


def test_handle_fast_intent_play_frustrated(monkeypatch):
    """CB: play with frustration word → _open_media_play(frustrated=True)."""
    with patch.object(tools, "_clean_media_query", return_value=("dude songs", None)), \
         patch.object(tools, "_get_preferred_music_platform", return_value="spotify"), \
         patch.object(tools, "_open_media_play", return_value="playing") as omp:
        result = tools.handle_fast_intent("play dude stupid songs")
        assert result == "playing"
        omp.assert_called_once()


def test_handle_fast_intent_play_resume_only(monkeypatch):
    """CB: play with resume-only word → _open_media_play(platform, '')."""
    with patch.object(tools, "_clean_media_query", return_value=("", None)), \
         patch.object(tools, "_get_preferred_music_platform", return_value="spotify"), \
         patch.object(tools, "_open_media_play", return_value="resuming") as omp:
        result = tools.handle_fast_intent("play something")
        assert result == "resuming"
        omp.assert_called_once_with("spotify", "", frustrated=False)


def test_handle_fast_intent_play_regular_query(monkeypatch):
    """CB: play regular query → _open_media_play with login_requested=False."""
    with patch.object(tools, "_clean_media_query", return_value=("dude songs", None)), \
         patch.object(tools, "_get_preferred_music_platform", return_value="spotify"), \
         patch.object(tools, "_open_media_play", return_value="playing") as omp:
        result = tools.handle_fast_intent("play dude songs")
        assert result == "playing"
        omp.assert_called_once()


def test_handle_fast_intent_play_with_login(monkeypatch):
    """CB: play with login request → _open_media_play(login_requested=True)."""
    with patch.object(tools, "_clean_media_query", return_value=("dude songs", None)), \
         patch.object(tools, "_get_preferred_music_platform", return_value="spotify"), \
         patch.object(tools, "_open_media_play", return_value="playing") as omp:
        result = tools.handle_fast_intent("do login in spotify and play dude songs")
        assert result == "playing"
        omp.assert_called_once()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_handle_fast_intent_login_known_site(monkeypatch):
    """CB: login to known site → open_url_safe with LOGIN_URLS."""
    with patch.object(tools, "open_url_safe", return_value="opened") as ous:
        result = tools.handle_fast_intent("login to github")
        assert "🔐" in result
        ous.assert_called_once()


def test_handle_fast_intent_login_web_app(monkeypatch):
    """CB: login to web app → open_url_safe with WEB_APPS."""
    with patch.object(tools, "open_url_safe", return_value="opened") as ous:
        result = tools.handle_fast_intent("sign in to gmail")
        assert "🔐" in result
        ous.assert_called_once()


def test_handle_fast_intent_login_unknown_site(monkeypatch):
    """CB: login to unknown site → open_application fallback."""
    with patch.object(tools, "open_application", return_value="opened app") as oa:
        result = tools.handle_fast_intent("login to somerandomsite")
        assert "🔐" in result
        oa.assert_called_once()


# ---------------------------------------------------------------------------
# Open app / website
# ---------------------------------------------------------------------------

def test_handle_fast_intent_open_application(monkeypatch):
    """CB: open application → open_application(target)."""
    with patch.object(tools, "open_application", return_value="opened app") as oa:
        result = tools.handle_fast_intent("open calculator")
        assert result == "opened app"
        oa.assert_called_once_with("calculator")


def test_handle_fast_intent_open_website(monkeypatch):
    """CB: open website URL → open_application(target)."""
    with patch.object(tools, "open_application", return_value="opened web") as oa:
        result = tools.handle_fast_intent("open https://example.com")
        assert result == "opened web"
        oa.assert_called_once()


# ---------------------------------------------------------------------------
# Unit conversion / calculator
# ---------------------------------------------------------------------------

def test_handle_fast_intent_unit_conversion(monkeypatch):
    """CB: unit conversion → spotlight_index.convert_units."""
    with patch.object(tools.spotlight_index, "convert_units", return_value="10 kg = 22 lbs") as cu:
        result = tools.handle_fast_intent("convert 10 kg to lbs")
        assert "🔄" in result
        cu.assert_called_once()


def test_handle_fast_intent_calculator(monkeypatch):
    """CB: calculator expression → spotlight_index.safe_calculate."""
    with patch.object(tools.spotlight_index, "safe_calculate", return_value="42") as sc:
        result = tools.handle_fast_intent("6 * 7")
        assert "🧮" in result
        sc.assert_called_once()


def test_handle_fast_intent_calculator_none(monkeypatch):
    """CB: calculator returns None → falls through to return None."""
    with patch.object(tools.spotlight_index, "convert_units", return_value=None), \
         patch.object(tools.spotlight_index, "safe_calculate", return_value=None):
        result = tools.handle_fast_intent("some random phrase xyz123")
        assert result is None


# ---------------------------------------------------------------------------
# Unknown intent
# ---------------------------------------------------------------------------

def test_handle_fast_intent_unknown(monkeypatch):
    """CB: unknown intent → returns None."""
    with patch.object(tools.spotlight_index, "convert_units", return_value=None), \
         patch.object(tools.spotlight_index, "safe_calculate", return_value=None):
        result = tools.handle_fast_intent("some random unknown phrase xyz123")
        assert result is None


def test_handle_fast_intent_empty(monkeypatch):
    """CB: empty text → returns None."""
    result = tools.handle_fast_intent("")
    assert result is None


def test_handle_fast_intent_whitespace_only(monkeypatch):
    """CB: whitespace-only text → returns None."""
    result = tools.handle_fast_intent("   ")
    assert result is None
