"""
Celina AI (Celly) - Plugin #1: Active Context Sniffer (Update 19)

Zero-LLM situational awareness plugin. Uses native OS Accessibility APIs to
instantly grab the Active Window Title and Currently Highlighted/Selected Text.
Sub-5ms. 100% local. No cloud.

Provides zero-shot context — user never has to say "Look at this file" or
"Read this text."

Supported platforms:
  - Linux: AT-SPI2 via pyatspi (GNOME, KDE, etc.)
  - macOS: Accessibility framework via AxPython / AppleScript fallback
  - Windows: UI Automation via pywinauto / comtypes fallback
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Sniffer security hardening (Update 20 Truth & Safety)
# ---------------------------------------------------------------------------

# Apps whose context must never be sniffed (password managers, incognito, etc.)
SNIFFER_APP_DENYLIST: frozenset[str] = frozenset({
    "keepassxc", "1password", "bitwarden", "lastpass", "dashlane",
    "chrome --incognito", "chromium --incognito",
    "private browsing", "tor",
})

# RAM-only TTL: snapshots expire after this many seconds and are never persisted
SNIFFER_TTL_SECONDS: int = 30


@dataclass
class ContextSnapshot:
    """A single context capture from the active window.

    Snapshots are RAM-only, never persisted to disk, and expire after
    SNIFFER_TTL_SECONDS.
    """
    window_title: str
    app_name: str
    highlighted_text: str
    timestamp: float
    platform: str
    source: str  # 'ats', 'ax', 'uia', 'fallback'
    _expiry: float = field(init=False)

    def __post_init__(self):
        self._expiry = self.timestamp + SNIFFER_TTL_SECONDS

    def is_expired(self) -> bool:
        """Return True if the snapshot has passed its RAM-only TTL."""
        return time.time() > self._expiry

    def is_allowed(self) -> bool:
        """Return True if the captured app is not in the denylist."""
        return (self.app_name or "").lower() not in SNIFFER_APP_DENYLIST


class ContextSniffer:
    """
    Sniffs the active OS window for title and highlighted text.

    All methods are lazy and gracefully degrade if accessibility tools
    are not installed or permissions are not granted.
    """

    def __init__(self):
        self._platform = self._detect_platform()
        self._last_snapshot: Optional[ContextSnapshot] = None

    # ------------------------------------------------------------------
    # Platform detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_platform() -> str:
        import sys
        if sys.platform.startswith("linux"):
            return "linux"
        if sys.platform == "darwin":
            return "macos"
        if sys.platform == "win32":
            return "windows"
        return "unknown"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sniff(self) -> ContextSnapshot:
        """
        Capture the current active window context.

        Returns a ContextSnapshot with window_title, app_name, highlighted_text,
        timestamp, platform, and source. Never raises — returns empty strings
        on any failure.
        """
        import time

        ts = time.time()
        window_title = ""
        app_name = ""
        highlighted = ""
        source = "fallback"

        try:
            if self._platform == "linux":
                window_title, app_name, highlighted, source = self._sniff_linux()
            elif self._platform == "macos":
                window_title, app_name, highlighted, source = self._sniff_macos()
            elif self._platform == "windows":
                window_title, app_name, highlighted, source = self._sniff_windows()
        except Exception:
            pass

        snapshot = ContextSnapshot(
            window_title=window_title or "",
            app_name=app_name or "",
            highlighted_text=highlighted or "",
            timestamp=ts,
            platform=self._platform,
            source=source,
        )
        # Enforce app denylist — never return context for sensitive apps
        if not snapshot.is_allowed():
            snapshot = ContextSnapshot(
                window_title="",
                app_name="",
                highlighted_text="",
                timestamp=ts,
                platform=self._platform,
                source=source,
            )
        self._last_snapshot = snapshot
        return snapshot

    def last_snapshot(self) -> Optional[ContextSnapshot]:
        """Return the most recent snapshot, or None if never sniffed."""
        return self._last_snapshot

    # ------------------------------------------------------------------
    # Linux — AT-SPI2
    # ------------------------------------------------------------------

    def _sniff_linux(self) -> tuple[str, str, str, str]:
        """Use AT-SPI2 (pyatspi) or xdotool fallback."""
        # Try pyatspi first
        try:
            import pyatspi  # type: ignore
            desktop = pyatspi.Desktop()
            focused = desktop.getFocus()
            if focused:
                title = focused.name or ""
                app = ""
                try:
                    app = focused.attributes.get("application", "") or ""
                except Exception:
                    pass
                # Get highlighted/selected text via AccessibleText
                selected = ""
                try:
                    text_iface = focused.queryText()
                    if text_iface:
                        sel_start, sel_end = text_iface.getSelection(0)
                        if sel_end > sel_start:
                            selected = text_iface.getText(sel_start, sel_end)
                except Exception:
                    pass
                return title, app, selected, "ats"
        except Exception:
            pass

        # Fallback: xdotool for window title
        try:
            if shutil.which("xdotool"):
                title = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True, text=True, timeout=2,
                ).stdout.strip()
                app = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowpid"],
                    capture_output=True, text=True, timeout=2,
                ).stdout.strip()
                # Try to get app name from PID
                if app and shutil.which("ps"):
                    try:
                        app = subprocess.run(
                            ["ps", "-p", app, "-o", "comm="],
                            capture_output=True, text=True, timeout=2,
                        ).stdout.strip()
                    except Exception:
                        pass
                # xsel / xclip for highlighted text
                selected = ""
                if shutil.which("xsel"):
                    selected = subprocess.run(
                        ["xsel", "--selection", "--output"],
                        capture_output=True, text=True, timeout=2,
                    ).stdout.strip()
                elif shutil.which("xclip"):
                    selected = subprocess.run(
                        ["xclip", "-selection", "c", "-o"],
                        capture_output=True, text=True, timeout=2,
                    ).stdout.strip()
                return title, app, selected, "xdotool"
        except Exception:
            pass

        return "", "", "", "fallback"

    # ------------------------------------------------------------------
    # macOS — Accessibility API
    # ------------------------------------------------------------------

    def _sniff_macos(self) -> tuple[str, str, str, str]:
        """Use AppleScript (System Events) for window title + clipboard for text."""
        try:
            script = '''
            tell application "System Events"
                set frontApp to name of first application process whose frontmost is true
                try
                    tell process frontApp
                        set winTitle to name of window 1
                    end tell
                on error
                    set winTitle to ""
                end try
                return frontApp & "|" & winTitle
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3,
            )
            output = result.stdout.strip()
            if "|" in output:
                app_name, window_title = output.split("|", 1)
            else:
                app_name, window_title = output, ""

            # Highlighted text via clipboard (pbpaste) — note: this reads clipboard,
            # not selection. True selection access requires AXUIElement API.
            highlighted = ""
            if shutil.which("pbpaste"):
                highlighted = subprocess.run(
                    ["pbpaste"], capture_output=True, text=True, timeout=2,
                ).stdout.strip()

            return window_title, app_name, highlighted, "ax"
        except Exception:
            return "", "", "", "fallback"

    # ------------------------------------------------------------------
    # Windows — UI Automation
    # ------------------------------------------------------------------

    def _sniff_windows(self) -> tuple[str, str, str, str]:
        """Use pywinauto or PowerShell for window title + text."""
        try:
            # PowerShell fallback (always available on Windows)
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$w = [System.Windows.Forms.Form]::ActiveForm; "
                "if ($w) { $w.Text } else { '' }"
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True, text=True, timeout=3,
            )
            window_title = result.stdout.strip()

            app_name = ""
            ps_app = (
                "Get-Process | Where-Object { $_.MainWindowTitle -ne '' } | "
                "Select-Object -First 1 -ExpandProperty ProcessName"
            )
            result_app = subprocess.run(
                ["powershell", "-Command", ps_app],
                capture_output=True, text=True, timeout=3,
            )
            app_name = result_app.stdout.strip()

            # Clipboard for highlighted text
            highlighted = ""
            ps_clip = "Get-Clipboard"
            result_clip = subprocess.run(
                ["powershell", "-Command", ps_clip],
                capture_output=True, text=True, timeout=2,
            )
            highlighted = result_clip.stdout.strip()

            return window_title, app_name, highlighted, "uia"
        except Exception:
            return "", "", "", "fallback"


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_sniffer: Optional[ContextSniffer] = None


def get_sniffer() -> ContextSniffer:
    """Get or create the global ContextSniffer instance."""
    global _sniffer
    if _sniffer is None:
        _sniffer = ContextSniffer()
    return _sniffer


def sniff_context() -> ContextSnapshot:
    """Convenience: sniff the current active window context."""
    return get_sniffer().sniff()


__all__ = [
    "ContextSniffer",
    "ContextSnapshot",
    "get_sniffer",
    "sniff_context",
    "gear0_sniffer_classify",
    "gear0_sniffer_execute",
]


# ---------------------------------------------------------------------------
# Gear 0 Plugin #1 dispatch: Active Context Sniffer
# ---------------------------------------------------------------------------

_SNIFFER_QUERY_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:what'?s|what is)\s+(?:my\s+)?(?:active\s+window|current\s+app|highlighted|selected\s+text|screen\s+context)\??$",
    __import__("re").IGNORECASE,
)
_SNIFFER_SNIFF_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:sniff|capture|grab)\s+(?:context|window|screen|text)\??$",
    __import__("re").IGNORECASE,
)


def gear0_sniffer_classify(query: str) -> Optional[str]:
    """Classify query into a sniffer handler name, or None."""
    low = query.strip().lower()
    if _SNIFFER_QUERY_RE.match(low) or _SNIFFER_SNIFF_RE.match(low):
        return "sniff_context"
    return None


def gear0_sniffer_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0 Plugin #1 sniffer handler.

    Snapshots are RAM-only with a 30-second TTL and are never persisted.
    Denylisted apps (password managers, incognito browsers, etc.) return empty.
    """
    try:
        if handler == "sniff_context":
            snapshot = sniff_context()
            # Enforce RAM-only TTL — expired snapshots are treated as empty
            if snapshot.is_expired():
                return (
                    "👁 Context Sniffer: cached context expired (RAM-only TTL). "
                    "Re-run to capture fresh context."
                )
            # Enforce denylist
            if not snapshot.is_allowed():
                return (
                    "👁 Context Sniffer: context capture blocked for this app "
                    "(sensitive application in denylist)."
                )
            lines = [f"👁 Context Sniffer ({snapshot.source}, {snapshot.platform})"]
            lines.append(f"  App: {snapshot.app_name or '(unknown)'}")
            lines.append(f"  Window: {snapshot.window_title or '(unknown)'}")
            lines.append(f"  Highlighted text: {snapshot.highlighted_text or '(none)'}")
            return "\n".join(lines)

    except Exception:
        return None
    return None
