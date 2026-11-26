"""
Celina Turbo / Zero-Lag 8 GB Performance Mode (`turbo_mode.py` + `celina-turbo` CLI)

Eliminates desktop lag on 8 GB Ubuntu 26.04 laptops running macOS-style GNOME effects
alongside local AI by tackling all three real bottlenecks at once:
  1. Ollama + ZRAM Swap Spiral:
     - Immediately ejects local Ollama from RAM (`keep_alive: 0`) & trims caches/heap.
     - Persists `CELINA_OLLAMA_KEEP_ALIVE=0` so Ollama frees 2-4 GB the instant any
       private local reply finishes (and non-private chat uses Cloud OpenRouter at 0 MB RAM).
  2. Background AT-SPI2 Polling:
     - Persists `CELINA_LITE_MODE=1` so `celina-eventd` stops background AT-SPI2 App Nap
       polling and uses 60s cached health checks.
  3. Heavy GNOME Blur / Compositor Lag:
     - Disables `blur-my-shell@aunetx` & heavy GNOME shell animations on integrated GPUs
       (cleanly reversible anytime with `celina-turbo off` / `/turbo off`).

CLI usage:
    celina-turbo on       # Instant lag-fix + eject Ollama RAM + disable heavy blur
    celina-turbo off      # Restore Blur My Shell + animations + standard keep_alive
    celina-turbo status   # Inspect live RAM, ZRAM, Ollama, Cloud Offload & GNOME blur
"""

from __future__ import annotations

import argparse
import gc
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import requests

from . import command_runner, config

log = logging.getLogger("celina.turbo")


def _eject_ollama_now() -> tuple[bool, str]:
    """Requests immediate RAM unload (`keep_alive: 0`) for active local Ollama model(s)."""
    ejected: list[str] = []
    models_to_eject = {config.LOCAL_MODEL}
    try:
        r_ps = requests.get(f"{config.OLLAMA_API_BASE}/api/ps", timeout=1.5)
        if r_ps.status_code == 200:
            for m in r_ps.json().get("models", []):
                name = str(m.get("name") or "").strip()
                if name:
                    models_to_eject.add(name)
    except Exception as exc:
        log.debug("Ollama /api/ps probe skipped: %s", exc)

    for model in sorted(m for m in models_to_eject if m):
        try:
            r = requests.post(
                f"{config.OLLAMA_API_BASE}/api/generate",
                json={"model": model, "keep_alive": 0},
                timeout=2.0,
            )
            if r.status_code < 400:
                ejected.append(model)
        except Exception as exc:
            log.debug("Ollama eject for %s skipped: %s", model, exc)
            continue
    if ejected:
        return True, f"Ejected Ollama from RAM ({', '.join(ejected)})"
    return False, "Ollama already idle (0 MB loaded)"


def _read_zram_summary() -> str:
    swaps = Path("/proc/swaps")
    try:
        if swaps.is_file():
            for ln in swaps.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
                parts = ln.split()
                if len(parts) >= 4 and "zram" in parts[0]:
                    size_mb = int(parts[2]) // 1024
                    used_mb = int(parts[3]) // 1024
                    pct = int((used_mb * 100) / max(1, size_mb))
                    return f"{used_mb} MiB / {size_mb} MiB ({pct}%)"
    except Exception as exc:
        log.debug("ZRAM swap read failed: %s", exc)
    return "inactive"


def _set_gnome_effects(*, lite: bool) -> list[str]:
    notes: list[str] = []
    if shutil.which("gnome-extensions"):
        action = "disable" if lite else "enable"
        res = command_runner.run_command(["gnome-extensions", action, "blur-my-shell@aunetx"], timeout=4.0)
        if res.ok:
            notes.append(f"Blur My Shell {'disabled (GPU smooth)' if lite else 're-enabled'}")
    if shutil.which("gsettings"):
        val = "false" if lite else "true"
        res2 = command_runner.run_command(
            ["gsettings", "set", "org.gnome.desktop.interface", "enable-animations", val],
            timeout=3.0,
        )
        if res2.ok:
            notes.append(f"GNOME animations {'off (instant)' if lite else 'on (macOS smooth)'}")
    return notes


def enable_turbo_mode(*, dry_run: bool = False, env_path: Optional[Path] = None) -> str:
    """Activates Zero-Lag 8 GB Turbo Mode immediately and persists it to ~/.config/celina/env."""
    if dry_run:
        return (
            "🧪 Dry-Run [TurboMode]: Would eject Ollama from RAM (keep_alive=0), persist "
            "CELINA_LITE_MODE=1 + CELINA_OLLAMA_KEEP_ALIVE=0, disable heavy GNOME blur/animations, "
            "and stop background AT-SPI2 polling."
        )

    steps: list[str] = []
    before_mb = config.detect_available_ram_mb()

    _ok, ol_msg = _eject_ollama_now()
    steps.append(ol_msg)

    # Persist Lite/Turbo settings cleanly (0600)
    try:
        config.set_env_pairs(
            {"CELINA_LITE_MODE": "1", "CELINA_OLLAMA_KEEP_ALIVE": "0"},
            env_path=env_path,
        )
        steps.append("Saved CELINA_LITE_MODE=1 & CELINA_OLLAMA_KEEP_ALIVE=0 (instant RAM eviction + zero background AT-SPI2)")
    except OSError as exc:
        steps.append(f"Warning: could not persist env ({exc})")

    # Disable heavy GPU blur/animations when running inside a real graphical session
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        steps.extend(_set_gnome_effects(lite=True))

    gc.collect()
    after_mb = config.detect_available_ram_mb()
    cloud_active = bool((os.environ.get("OPENROUTER_API_KEY") or config.ENV.get("OPENROUTER_API_KEY") or "").strip())
    cloud_line = (
        "☁️ Cloud AI Offload: ACTIVE via OPENROUTER_API_KEY (0 MB local RAM for non-private chat!)."
        if cloud_active
        else "💡 Tip: Add OPENROUTER_API_KEY in ~/.config/celina/env (or set OLLAMA_HOST to a Pi 5/mini-PC) for 0 MB local AI RAM."
    )

    return (
        "⚡ Zero-Lag Turbo Mode ENABLED!\n"
        + "\n".join(f"  • {s}" for s in steps)
        + f"\n  • Available RAM: ~{after_mb} MiB (was ~{before_mb} MiB) | ZRAM used: {_read_zram_summary()}\n"
        + f"  {cloud_line}\n"
        + "  (Restore macOS blur & animations anytime with: /turbo off  or  celina-turbo off)"
    )


def disable_turbo_mode(*, dry_run: bool = False, env_path: Optional[Path] = None) -> str:
    """Restores standard macOS visual effects & 45s Ollama keep-alive."""
    if dry_run:
        return (
            "🧪 Dry-Run [TurboMode]: Would re-enable Blur My Shell & GNOME animations and restore "
            "CELINA_LITE_MODE=0 + CELINA_OLLAMA_KEEP_ALIVE=45s."
        )

    steps: list[str] = []
    try:
        config.set_env_pairs(
            {"CELINA_LITE_MODE": "0", "CELINA_OLLAMA_KEEP_ALIVE": "45s"},
            env_path=env_path,
        )
        steps.append("Restored CELINA_LITE_MODE=0 & CELINA_OLLAMA_KEEP_ALIVE=45s")
    except OSError as exc:
        steps.append(f"Warning: could not persist env ({exc})")

    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        steps.extend(_set_gnome_effects(lite=False))

    return "✨ Standard macOS Visual Mode RESTORED:\n" + "\n".join(f"  • {s}" for s in steps)


def get_turbo_status() -> str:
    """Returns a concise live diagnostic of RAM, ZRAM, Ollama, Cloud Offload, and Lite Mode."""
    tot = config.detect_system_ram_gb()
    avail = config.detect_available_ram_mb()
    lite = config.is_lite_mode()
    cloud_active = bool((os.environ.get("OPENROUTER_API_KEY") or config.ENV.get("OPENROUTER_API_KEY") or "").strip())
    ollama_loaded = "none (0 MB)"
    try:
        r = requests.get(f"{config.OLLAMA_API_BASE}/api/ps", timeout=1.0)
        if r.status_code == 200:
            ms = [str(m.get("name")) for m in r.json().get("models", []) if m.get("name")]
            if ms:
                ollama_loaded = ", ".join(ms)
    except Exception:
        ollama_loaded = "stopped / idle"

    return (
        f"⚡ Celina Performance & Lag Status:\n"
        f"  • Turbo / Lite Mode:    {'ON (zero background AT-SPI2 · keep_alive=0)' if lite else 'OFF (standard macOS mode)'}\n"
        f"  • Available RAM:        ~{avail} MiB free / ~{tot} GiB total\n"
        f"  • ZRAM Swap Usage:      {_read_zram_summary()}\n"
        f"  • Ollama Loaded in RAM: {ollama_loaded} (keep_alive={config.OLLAMA_KEEP_ALIVE})\n"
        f"  • Cloud AI Offload:     {'ACTIVE (OpenRouter — 0 MB local RAM)' if cloud_active else 'OFF (local Ollama only)'}\n"
        f"  • Tip: Run '/turbo on' (or 'celina-turbo on') to instantly fix lag, or '/offload' to clear AI RAM."
    )


_AUTOPLAY_START = "// >>> CELINA-AUTOPLAY >>>"
_AUTOPLAY_END = "// <<< CELINA-AUTOPLAY <<<"
_AUTOPLAY_BLOCK = (
    f"{_AUTOPLAY_START}\n"
    '// Allow hands-free YouTube/Spotify playback when Celina opens a song/video\n'
    'user_pref("media.autoplay.default", 0);\n'
    'user_pref("media.autoplay.blocking_policy", 0);\n'
    f"{_AUTOPLAY_END}\n"
)


def _discover_firefox_profiles(home: Optional[Path] = None) -> list[Path]:
    """Finds Firefox profile directories across Ubuntu Snap, DEB, and Flatpak."""
    base = (home or Path.home()).resolve()
    roots = (
        base / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        base / ".mozilla" / "firefox",
        base / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
    )
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for cand in sorted(root.glob("*.default*")):
            if cand.is_dir():
                rp = cand.resolve()
                if rp not in seen:
                    seen.add(rp)
                    found.append(cand)
    return found


def configure_firefox_autoplay(*, enable: bool = True, dry_run: bool = False, home: Optional[Path] = None) -> str:
    """
    Safely updates ONLY a delimited Celina block inside Firefox profile user.js
    so YouTube/Spotify tabs opened by Celina start playing with sound automatically
    (instead of Firefox's default 'Block Audio' shield pausing every new tab).
    """
    profiles = _discover_firefox_profiles(home=home)
    if dry_run:
        act = "enable" if enable else "restore default"
        return f"🧪 Dry-Run [TurboMode]: Would {act} Firefox audio/video autoplay across {len(profiles)} profile(s)."
    if not profiles:
        return (
            "ℹ️ No Firefox profile folder found automatically. In Firefox, open YouTube, "
            "click the padlock/autoplay icon next to the URL bar, and set Autoplay → Allow Audio and Video."
        )

    updated: list[str] = []
    for prof in profiles:
        ujs = prof / "user.js"
        existing = ujs.read_text(encoding="utf-8", errors="replace") if ujs.is_file() else ""
        # Strip any prior Celina autoplay block cleanly
        if _AUTOPLAY_START in existing and _AUTOPLAY_END in existing:
            pre = existing.split(_AUTOPLAY_START, 1)[0].rstrip()
            post = existing.split(_AUTOPLAY_END, 1)[1].lstrip()
            cleaned = (pre + ("\n\n" if pre and post else "") + post).strip()
        else:
            cleaned = existing.strip()

        if enable:
            final_txt = (cleaned + "\n\n" if cleaned else "") + _AUTOPLAY_BLOCK
        else:
            final_txt = (cleaned + "\n") if cleaned else ""

        ujs.write_text(final_txt, encoding="utf-8")
        updated.append(prof.name)

    if enable:
        return (
            f"🔊 Enabled hands-free Firefox audio/video autoplay in {len(updated)} profile(s): {', '.join(updated)}.\n"
            "  • Restart Firefox ONCE so the setting takes effect — after that, every 'play <song>' starts playing with sound automatically!\n"
            "  • Revert anytime with: celina-turbo autoplay-off"
        )
    return f"🔇 Restored Firefox default autoplay blocking in {len(updated)} profile(s): {', '.join(updated)} (restart Firefox to apply)."


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="celina-turbo",
        description="Toggle Celina Zero-Lag 8 GB Turbo Mode & Firefox hands-free YouTube autoplay.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=("on", "off", "status", "autoplay", "autoplay-off"),
        help="on = Zero-Lag mode, off = restore blur/animations, status = RAM/ZRAM diagnostics, autoplay = allow Firefox hands-free media playback",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying system settings.")
    args = parser.parse_args(argv)

    if args.action == "on":
        print(enable_turbo_mode(dry_run=args.dry_run))
    elif args.action == "off":
        print(disable_turbo_mode(dry_run=args.dry_run))
    elif args.action == "autoplay":
        print(configure_firefox_autoplay(enable=True, dry_run=args.dry_run))
    elif args.action == "autoplay-off":
        print(configure_firefox_autoplay(enable=False, dry_run=args.dry_run))
    else:
        print(get_turbo_status())
    return 0


if __name__ == "__main__":
    sys.exit(main())
