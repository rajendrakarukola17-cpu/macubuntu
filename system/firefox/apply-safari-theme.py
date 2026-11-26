#!/usr/bin/env python3
"""
macubuntu — Firefox -> macOS Safari Theme & Compass Logo Installer/Reverter
Supports all Ubuntu Firefox install types cleanly:
  - Snap Firefox   : ~/snap/firefox/common/.mozilla/firefox
  - DEB/APT Firefox: ~/.mozilla/firefox
  - Flatpak Firefox: ~/.var/app/org.mozilla.firefox/.mozilla/firefox

Invariants:
  - 100% offline/self-contained (zero remote downloads)
  - Backs up pre-existing userChrome.css / userContent.css before modifying
  - Updates ONLY a delimited marker block in user.js (never clobbers user prefs)
  - Installs Safari-style compass SVG icon & .desktop overrides (preserving exact Exec=)
  - True zero-side-effect --dry-run and clean --revert for uninstall.sh
"""

from __future__ import annotations

import argparse
import configparser
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

MARKER_CSS = "MACUBUNTU-SAFARI-THEME"
USERJS_START = "// >>> MACUBUNTU-SAFARI-START"
USERJS_END = "// <<< MACUBUNTU-SAFARI-END"

USERJS_BLOCK = f"""{USERJS_START}
// --- macOS Safari Chrome & Native Wayland GTK Integration ---
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
user_pref("svg.context-properties.content.enabled", true);
user_pref("browser.tabs.inTitlebar", 1);
user_pref("browser.uidensity", 0);
user_pref("widget.gtk.rounded-bottom-corners.enabled", true);
user_pref("widget.use-xdg-desktop-portal.file-picker", 1);
user_pref("browser.theme.content-theme", 2);
user_pref("browser.theme.toolbar-theme", 2);

// --- GPU WebRender Compositor & Hardware VA-API Video Decode (zero-stutter YouTube/4K) ---
user_pref("gfx.webrender.all", true);
user_pref("gfx.webrender.layer-compositor", true);
user_pref("media.ffmpeg.vaapi.enabled", true);
user_pref("media.hardware-video-decoding.force-enabled", true);
user_pref("gfx.content.skia-font-cache-size", 20);

// --- Fastfox Page Load, Image Decode & Low-Latency Networking ---
user_pref("content.notify.interval", 100000);
user_pref("image.mem.decode_bytes_at_a_time", 32768);
user_pref("network.buffer.cache.size", 65535);
user_pref("network.buffer.cache.count", 48);
user_pref("network.http.max-connections", 1800);
user_pref("network.http.max-persistent-connections-per-server", 10);
user_pref("network.http.max-urgent-start-excessive-connections-per-host", 5);
user_pref("network.http.request.max-start-delay", 5);
user_pref("network.dnsCacheExpiration", 3600);

// --- macOS Safari Elastic Overscroll & Mass-Spring-Damper Smooth Scrolling ---
user_pref("apz.overscroll.enabled", true);
user_pref("general.smoothScroll", true);
user_pref("general.smoothScroll.msdPhysics.enabled", true);
user_pref("general.smoothScroll.msdPhysics.continuousMotionMaxDeltaMS", 12);
user_pref("general.smoothScroll.msdPhysics.motionBeginSpringConstant", 600);
user_pref("general.smoothScroll.msdPhysics.regularSpringConstant", 650);
user_pref("general.smoothScroll.msdPhysics.slowdownMinDeltaMS", 25);
user_pref("general.smoothScroll.msdPhysics.slowdownMinDeltaRatio", "2");
user_pref("general.smoothScroll.msdPhysics.slowdownSpringConstant", 250);
user_pref("general.smoothScroll.currentVelocityWeighting", "1");
user_pref("general.smoothScroll.stopDecelerationWeighting", "1");

// --- 8GB-Friendly Memory Saver, Reduced SSD Churn & Telemetry Cleanup ---
user_pref("browser.tabs.unloadOnLowMemory", true);
user_pref("browser.tabs.min_inactive_duration_before_unload", 300000);
user_pref("browser.sessionstore.interval", 60000);
user_pref("browser.sessionstore.restore_on_demand", true);
user_pref("browser.sessionstore.restore_tabs_lazily", true);
user_pref("toolkit.telemetry.enabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("extensions.pocket.enabled", false);
{USERJS_END}
"""

DESKTOP_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "firefox_firefox.desktop",
        (
            "/var/lib/snapd/desktop/applications/firefox_firefox.desktop",
            "/snap/firefox/current/firefox.desktop",
        ),
    ),
    (
        "firefox.desktop",
        (
            "/usr/share/applications/firefox.desktop",
            "/usr/local/share/applications/firefox.desktop",
        ),
    ),
    (
        "org.mozilla.firefox.desktop",
        (
            "/var/lib/flatpak/exports/share/applications/org.mozilla.firefox.desktop",
        ),
    ),
)


def discover_firefox_profiles(home: Path) -> list[Path]:
    """Finds all Firefox profile directories across Snap, DEB, and Flatpak roots."""
    roots = [
        home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        home / ".mozilla" / "firefox",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
    ]
    found: list[Path] = []
    seen: set[Path] = set()

    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        ini_path = root / "profiles.ini"
        if ini_path.exists():
            cp = configparser.RawConfigParser()
            try:
                cp.read(ini_path, encoding="utf-8")
                for sec in cp.sections():
                    if not cp.has_option(sec, "Path"):
                        continue
                    rel = cp.get(sec, "Path").strip()
                    is_rel = cp.get(sec, "IsRelative", fallback="1").strip() == "1"
                    prof = (root / rel) if is_rel else Path(rel)
                    if prof.is_dir():
                        rp = prof.resolve()
                        if rp not in seen:
                            seen.add(rp)
                            found.append(prof)
            except Exception:
                pass
        # Fallback glob for default profiles if profiles.ini was missing/unreadable
        for cand in sorted(root.glob("*.default*")):
            if cand.is_dir():
                rp = cand.resolve()
                if rp not in seen:
                    seen.add(rp)
                    found.append(cand)
    return found


def _update_user_js(
    user_js_path: Path, *, remove: bool = False, dry_run: bool = False, vaapi_enabled: bool = True
) -> str:
    existing = user_js_path.read_text(encoding="utf-8", errors="replace") if user_js_path.exists() else ""
    start_idx = existing.find(USERJS_START)
    end_idx = existing.find(USERJS_END)
    if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
        after_end = end_idx + len(USERJS_END)
        if after_end < len(existing) and existing[after_end : after_end + 1] == "\n":
            after_end += 1
        stripped = (existing[:start_idx].rstrip("\n") + "\n" + existing[after_end:].lstrip("\n")).strip()
    else:
        stripped = existing.strip()

    if remove:
        if dry_run:
            return f"[dry-run] strip Safari block from {user_js_path}"
        if not user_js_path.exists():
            return f"no user.js at {user_js_path}"
        if stripped:
            user_js_path.write_text(stripped + "\n", encoding="utf-8")
            os.chmod(user_js_path, 0o600)
        else:
            user_js_path.unlink(missing_ok=True)
        return f"cleaned Safari prefs in {user_js_path}"

    block = USERJS_BLOCK
    if not vaapi_enabled:
        block = block.replace(
            'user_pref("media.ffmpeg.vaapi.enabled", true);',
            'user_pref("media.ffmpeg.vaapi.enabled", false);',
        ).replace(
            'user_pref("media.hardware-video-decoding.force-enabled", true);',
            'user_pref("media.hardware-video-decoding.force-enabled", false);',
        )
    new_content = (stripped + "\n\n" + block) if stripped else block
    if dry_run:
        tag = " (VA-API fallback disabled)" if not vaapi_enabled else ""
        return f"[dry-run] write Safari prefs block{tag} -> {user_js_path}"
    user_js_path.write_text(new_content, encoding="utf-8")
    os.chmod(user_js_path, 0o600)
    return f"updated {user_js_path}{' (VA-API disabled)' if not vaapi_enabled else ''}"


def _install_css_file(src: Path, dst: Path, *, dry_run: bool = False) -> str:
    if dry_run:
        return f"[dry-run] install {src.name} -> {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        cur = dst.read_text(encoding="utf-8", errors="replace")
        bak = dst.with_suffix(dst.suffix + ".macubuntu.bak")
        if MARKER_CSS not in cur and not bak.exists():
            shutil.copy2(dst, bak)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o600)
    return f"installed {dst}"


def _revert_css_file(dst: Path, *, dry_run: bool = False) -> str:
    bak = dst.with_suffix(dst.suffix + ".macubuntu.bak")
    if dry_run:
        return f"[dry-run] revert {dst}"
    if bak.exists():
        shutil.move(str(bak), str(dst))
        return f"restored backup {dst}"
    if dst.exists() and MARKER_CSS in dst.read_text(encoding="utf-8", errors="replace"):
        dst.unlink(missing_ok=True)
        return f"removed {dst}"
    return f"untouched {dst}"


def _build_safari_desktop_override(source_text: Optional[str], icon_path: Path, default_exec: str) -> str:
    """Produces a valid XDG .desktop override keeping the system's Exec= while applying Safari name + compass icon."""
    if not source_text:
        return (
            f"# {MARKER_CSS}\n"
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=Safari\n"
            "GenericName=Web Browser\n"
            "Comment=Firefox styled as macOS Safari (macubuntu)\n"
            f"Exec={default_exec}\n"
            f"Icon={icon_path}\n"
            "Terminal=false\n"
            "Categories=Network;WebBrowser;\n"
            "Keywords=firefox;safari;browser;web;internet;\n"
            "StartupWMClass=firefox\n"
        )

    out_lines: list[str] = [f"# {MARKER_CSS}"]
    in_main = False
    replaced_name = False
    replaced_icon = False
    replaced_kw = False
    for raw_line in source_text.splitlines():
        s = raw_line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_main:
                if not replaced_name:
                    out_lines.append("Name=Safari")
                    replaced_name = True
                if not replaced_icon:
                    out_lines.append(f"Icon={icon_path}")
                    replaced_icon = True
                if not replaced_kw:
                    out_lines.append("Keywords=firefox;safari;browser;web;internet;")
                    replaced_kw = True
            in_main = s == "[Desktop Entry]"
            out_lines.append(raw_line)
            continue
        if in_main:
            if s.startswith("Name="):
                out_lines.append("Name=Safari")
                replaced_name = True
                continue
            if s.startswith("Name["):
                # Avoid locale-specific overrides reverting "Safari" back to "Firefox"
                continue
            if s.startswith("Icon="):
                out_lines.append(f"Icon={icon_path}")
                replaced_icon = True
                continue
            if s.startswith("Keywords="):
                kw = s.split("=", 1)[1].rstrip(";")
                if "safari" not in kw.lower():
                    kw = f"{kw};safari" if kw else "firefox;safari;browser"
                out_lines.append(f"Keywords={kw};")
                replaced_kw = True
                continue
        out_lines.append(raw_line)

    if in_main:
        if not replaced_name:
            out_lines.append("Name=Safari")
        if not replaced_icon:
            out_lines.append(f"Icon={icon_path}")
        if not replaced_kw:
            out_lines.append("Keywords=firefox;safari;browser;web;internet;")
    return "\n".join(out_lines) + "\n"


def apply_safari_theme(
    *,
    home: Path = Path.home(),
    assets_dir: Optional[Path] = None,
    desktop_sources: Optional[Iterable[tuple[str, tuple[str, ...]]]] = None,
    dry_run: bool = False,
    vaapi_enabled: bool = True,
) -> list[str]:
    """Installs Safari userChrome/userContent/user.js across Firefox profiles + Safari compass icon & desktop entries."""
    adir = assets_dir or Path(__file__).resolve().parent
    src_chrome = adir / "userChrome.css"
    src_content = adir / "userContent.css"
    src_icon = adir / "safari-compass.svg"

    log: list[str] = []
    icon_dest = home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "macubuntu-safari.svg"
    apps_dir = home / ".local" / "share" / "applications"

    # 1. Install Safari-style compass SVG icon
    if dry_run:
        log.append(f"[dry-run] install Safari compass icon -> {icon_dest}")
    else:
        icon_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_icon, icon_dest)
        os.chmod(icon_dest, 0o644)
        log.append(f"installed icon {icon_dest}")

    # 2. Create Safari desktop entry overrides (for Snap, DEB, and/or Flatpak Firefox)
    entries = list(desktop_sources if desktop_sources is not None else DESKTOP_CANDIDATES)
    matched_any = False
    for filename, sys_paths in entries:
        src_text: Optional[str] = None
        for sp in sys_paths:
            p = Path(sp)
            if p.exists():
                try:
                    src_text = p.read_text(encoding="utf-8", errors="replace")
                    matched_any = True
                    break
                except OSError:
                    continue
        if src_text is None:
            continue
        dst = apps_dir / filename
        if dry_run:
            log.append(f"[dry-run] write Safari launcher override -> {dst}")
        else:
            apps_dir.mkdir(parents=True, exist_ok=True)
            dst.write_text(_build_safari_desktop_override(src_text, icon_dest, "firefox %u"), encoding="utf-8")
            os.chmod(dst, 0o644)
            log.append(f"wrote desktop override {dst}")

    if not matched_any:
        dst = apps_dir / "firefox.desktop"
        if dry_run:
            log.append(f"[dry-run] write fallback Safari launcher -> {dst}")
        else:
            apps_dir.mkdir(parents=True, exist_ok=True)
            dst.write_text(_build_safari_desktop_override(None, icon_dest, "firefox %u"), encoding="utf-8")
            os.chmod(dst, 0o644)
            log.append(f"wrote fallback desktop override {dst}")

    # 3. Apply Safari userChrome.css, userContent.css & delimited user.js to every Firefox profile
    profiles = discover_firefox_profiles(home)
    if not profiles:
        log.append("no existing Firefox profile directory found yet (launch Firefox once, then re-run Stage 20)")
    for prof in profiles:
        log.append(_install_css_file(src_chrome, prof / "chrome" / "userChrome.css", dry_run=dry_run))
        log.append(_install_css_file(src_content, prof / "chrome" / "userContent.css", dry_run=dry_run))
        log.append(_update_user_js(prof / "user.js", remove=False, dry_run=dry_run, vaapi_enabled=vaapi_enabled))

    return log


def revert_safari_theme(*, home: Path = Path.home(), dry_run: bool = False) -> list[str]:
    """Reverts Firefox Safari CSS, delimited user.js prefs, desktop overrides, and compass SVG icon."""
    log: list[str] = []
    icon_dest = home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "macubuntu-safari.svg"
    apps_dir = home / ".local" / "share" / "applications"

    if icon_dest.exists() or dry_run:
        if dry_run:
            log.append(f"[dry-run] remove {icon_dest}")
        else:
            icon_dest.unlink(missing_ok=True)
            log.append(f"removed {icon_dest}")

    for filename, _ in DESKTOP_CANDIDATES:
        dst = apps_dir / filename
        if dry_run:
            log.append(f"[dry-run] remove macubuntu desktop override {dst} (if present)")
        elif dst.exists():
            try:
                if MARKER_CSS in dst.read_text(encoding="utf-8", errors="replace"):
                    dst.unlink(missing_ok=True)
                    log.append(f"removed {dst}")
            except OSError:
                pass

    for prof in discover_firefox_profiles(home):
        log.append(_revert_css_file(prof / "chrome" / "userChrome.css", dry_run=dry_run))
        log.append(_revert_css_file(prof / "chrome" / "userContent.css", dry_run=dry_run))
        log.append(_update_user_js(prof / "user.js", remove=True, dry_run=dry_run))

    return log


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply or revert macubuntu Safari theme & compass icon for Firefox")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions with zero filesystem changes")
    parser.add_argument("--revert", action="store_true", help="Cleanly restore Firefox defaults and remove overrides")
    parser.add_argument(
        "--disable-vaapi",
        action="store_true",
        help="Disable Firefox VA-API hardware decode prefs (safe fallback when vainfo lacks H.264/VP9)",
    )
    args = parser.parse_args(argv)

    if args.revert:
        lines = revert_safari_theme(dry_run=args.dry_run)
    else:
        lines = apply_safari_theme(dry_run=args.dry_run, vaapi_enabled=not args.disable_vaapi)
    for ln in lines:
        print(f"      {ln}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
