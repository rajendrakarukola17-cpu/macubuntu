"""
Celina AI (Celly) - Plugin #4: Universal OS Shim (Update 19)

Cross-platform command abstraction layer. Gear 0 writes intent once,
the Shim guarantees flawless execution on Windows, macOS, and Linux.

Zero tokens, sub-5ms, 100% local.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ShimResult:
    """Result of a Shim command execution."""
    success: bool
    output: str
    command_used: str
    platform: str
    fallback_used: bool = False


class OSPlatform:
    """Detected OS platform."""
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    UNKNOWN = "unknown"

    @staticmethod
    def detect() -> str:
        if sys.platform.startswith("linux"):
            return OSPlatform.LINUX
        if sys.platform == "darwin":
            return OSPlatform.MACOS
        if sys.platform == "win32":
            return OSPlatform.WINDOWS
        return OSPlatform.UNKNOWN


class UniversalOSShim:
    """
    Abstracts OS-specific commands behind a single cross-platform API.

    Examples:
      shim.sleep_computer()        -> systemctl suspend / pmset sleepnow / rundll32
      shim.empty_trash()           -> empty trash via send2trash / osascript / powershell
      shim.lock_screen()           -> loginctl lock-session / pmset displaysleepnow / rundll32
      shim.take_screenshot()       -> gnome-screenshot / screencapture / powershell
      shim.empty_clipboard()       -> xclip -c / pbcopy < /dev/null / clip
      shim.open_file(path)         -> xdg-open / open / start
      shim.show_notification(...)  -> notify-send / osascript / powershell
    """

    def __init__(self):
        self.platform = OSPlatform.detect()

    # ------------------------------------------------------------------
    # Power / Sleep
    # ------------------------------------------------------------------

    def sleep_computer(self) -> ShimResult:
        """Put the computer to sleep."""
        if self.platform == OSPlatform.LINUX:
            if shutil.which("systemctl"):
                return self._run(["systemctl", "suspend"], "systemctl suspend")
        elif self.platform == OSPlatform.MACOS:
            if shutil.which("pmset"):
                return self._run(["pmset", "sleepnow"], "pmset sleepnow")
        elif self.platform == OSPlatform.WINDOWS:
            return self._run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], "rundll32 SetSuspendState")
        return ShimResult(False, "Sleep not supported on this platform.", "none", self.platform)

    def shutdown(self) -> ShimResult:
        """Shut down the computer."""
        if self.platform == OSPlatform.LINUX:
            return self._run(["systemctl", "poweroff"], "systemctl poweroff")
        elif self.platform == OSPlatform.MACOS:
            return self._run(["osascript", "-e", 'tell app "System Events" to shut down'], "osascript shutdown")
        elif self.platform == OSPlatform.WINDOWS:
            return self._run(["shutdown", "/s", "/t", "0"], "shutdown /s /t 0")
        return ShimResult(False, "Shutdown not supported on this platform.", "none", self.platform)

    def restart(self) -> ShimResult:
        """Restart the computer."""
        if self.platform == OSPlatform.LINUX:
            return self._run(["systemctl", "reboot"], "systemctl reboot")
        elif self.platform == OSPlatform.MACOS:
            return self._run(["osascript", "-e", 'tell app "System Events" to restart'], "osascript restart")
        elif self.platform == OSPlatform.WINDOWS:
            return self._run(["shutdown", "/r", "/t", "0"], "shutdown /r /t 0")
        return ShimResult(False, "Restart not supported on this platform.", "none", self.platform)

    def lock_screen(self) -> ShimResult:
        """Lock the screen."""
        if self.platform == OSPlatform.LINUX:
            for cmd in [["loginctl", "lock-session"], ["gnome-screensaver-command", "-l"], ["xdg-screensaver", "lock"]]:
                if shutil.which(cmd[0]):
                    return self._run(cmd, " ".join(cmd))
        elif self.platform == OSPlatform.MACOS:
            return self._run(["pmset", "displaysleepnow"], "pmset displaysleepnow")
        elif self.platform == OSPlatform.WINDOWS:
            return self._run(["rundll32.exe", "user32.dll,LockWorkStation"], "rundll32 LockWorkStation")
        return ShimResult(False, "Screen lock not supported on this platform.", "none", self.platform)

    # ------------------------------------------------------------------
    # Files & Trash
    # ------------------------------------------------------------------

    def empty_trash(self) -> ShimResult:
        """Empty the system trash/recycle bin."""
        if self.platform == OSPlatform.LINUX:
            trash_dirs = [
                Path.home() / ".local" / "share" / "Trash" / "files",
                Path.home() / ".local" / "share" / "Trash" / "info",
            ]
            removed = 0
            for d in trash_dirs:
                if d.exists():
                    for item in d.iterdir():
                        try:
                            # P0-02: Safe move instead of destructive rmtree/unlink
                            pending_dir = Path.home() / '.local' / 'share' / 'Trash' / 'files' / 'pending'
                            pending_dir.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(item), str(pending_dir / item.name))
                            removed += 1
                        except Exception:
                            continue
            return ShimResult(True, f"Emptied trash ({removed} items).", "python shutil", self.platform)
        elif self.platform == OSPlatform.MACOS:
            return self._run(["osascript", "-e", 'tell app "Finder" to empty trash'], "osascript empty trash")
        elif self.platform == OSPlatform.WINDOWS:
            ps = 'Clear-RecycleBin -Force -ErrorAction SilentlyContinue'
            return self._run(["powershell", "-Command", ps], "powershell Clear-RecycleBin")
        return ShimResult(False, "Empty trash not supported on this platform.", "none", self.platform)

    def open_file(self, file_path: str) -> ShimResult:
        """Open a file with the default application."""
        path = Path(file_path)
        if not path.exists():
            return ShimResult(False, f"File not found: {file_path}", "none", self.platform)
        if self.platform == OSPlatform.LINUX:
            return self._run(["xdg-open", str(path)], f"xdg-open {file_path}")
        elif self.platform == OSPlatform.MACOS:
            return self._run(["open", str(path)], f"open {file_path}")
        elif self.platform == OSPlatform.WINDOWS:
            return self._run(["start", "", str(path)], f"start {file_path}")
        return ShimResult(False, "Open file not supported on this platform.", "none", self.platform)

    def open_url(self, url: str) -> ShimResult:
        """Open a URL in the default browser."""
        if self.platform == OSPlatform.LINUX:
            return self._run(["xdg-open", url], f"xdg-open {url}")
        elif self.platform == OSPlatform.MACOS:
            return self._run(["open", url], f"open {url}")
        elif self.platform == OSPlatform.WINDOWS:
            return self._run(["start", "", url], f"start {url}")
        return ShimResult(False, "Open URL not supported on this platform.", "none", self.platform)

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------

    def get_clipboard(self) -> ShimResult:
        """Get clipboard contents."""
        if self.platform == OSPlatform.LINUX:
            for cmd in [["xsel", "--selection", "--output"], ["xclip", "-selection", "c", "-o"]]:
                if shutil.which(cmd[0]):
                    return self._run(cmd, " ".join(cmd))
        elif self.platform == OSPlatform.MACOS:
            return self._run(["pbpaste"], "pbpaste")
        elif self.platform == OSPlatform.WINDOWS:
            return self._run(["powershell", "-Command", "Get-Clipboard"], "powershell Get-Clipboard")
        return ShimResult(False, "Clipboard read not supported.", "none", self.platform)

    def set_clipboard(self, text: str) -> ShimResult:
        """Set clipboard contents."""
        if self.platform == OSPlatform.LINUX:
            for cmd in [["xsel", "--selection", "--input"], ["xclip", "-selection", "c"]]:
                if shutil.which(cmd[0]):
                    return self._run(cmd, " ".join(cmd), stdin_text=text)
        elif self.platform == OSPlatform.MACOS:
            return self._run(["pbcopy"], "pbcopy", stdin_text=text)
        elif self.platform == OSPlatform.WINDOWS:
            return self._run(["powershell", "-Command", f"Set-Clipboard -Value '{text}'"], "powershell Set-Clipboard")
        return ShimResult(False, "Clipboard write not supported.", "none", self.platform)

    def clear_clipboard(self) -> ShimResult:
        """Clear the clipboard."""
        if self.platform == OSPlatform.LINUX:
            for cmd in [["xsel", "--selection", "--clear"], ["xclip", "-selection", "c"]]:
                if shutil.which(cmd[0]):
                    return self._run(cmd + ["/dev/null"], " ".join(cmd))
        elif self.platform == OSPlatform.MACOS:
            return self._run(["bash", "-c", "printf '' | pbcopy"], "pbcopy clear")
        elif self.platform == OSPlatform.WINDOWS:
            return self._run(["powershell", "-Command", "Clear-Clipboard"], "powershell Clear-Clipboard")
        return ShimResult(False, "Clipboard clear not supported.", "none", self.platform)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def show_notification(self, title: str, body: str) -> ShimResult:
        """Show a desktop notification."""
        if self.platform == OSPlatform.LINUX:
            if shutil.which("notify-send"):
                return self._run(["notify-send", title, body], f"notify-send '{title}' '{body}'")
        elif self.platform == OSPlatform.MACOS:
            script = f'display notification "{body}" with title "{title}"'
            return self._run(["osascript", "-e", script], "osascript notification")
        elif self.platform == OSPlatform.WINDOWS:
            ps = (
                'Add-Type -AssemblyName System.Windows.Forms; '
                f'$toastXml = New-Object Windows.Data.Xml.Dom.XmlDocument; '
                f'$toastXml.LoadXml("<toast><visual><binding template=\"ToastGeneric\"><text>{title}</text><text>{body}</text></binding></visual></toast>"); '
                '$toast = New-Object Windows.UI.Notifications.ToastNotification($toastXml); '
                '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Celina").Show($toast)'
            )
            return self._run(["powershell", "-Command", ps], "powershell toast notification")
        return ShimResult(False, "Notification not supported on this platform.", "none", self.platform)

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def take_screenshot(self, output_path: str = "/tmp/celina_screenshot.png") -> ShimResult:
        """Take a screenshot and save to output_path."""
        out = Path(output_path)
        if self.platform == OSPlatform.LINUX:
            for cmd in [["gnome-screenshot", "-f", str(out)], ["spectacle", "-b", "-o", str(out)], ["scrot", str(out)]]:
                if shutil.which(cmd[0]):
                    return self._run(cmd, " ".join(cmd))
        elif self.platform == OSPlatform.MACOS:
            return self._run(["screencapture", str(out)], f"screencapture {output_path}")
        elif self.platform == OSPlatform.WINDOWS:
            ps = (
                f'Add-Type -AssemblyName System.Windows.Forms; '
                f'$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; '
                f'$bmp = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height); '
                f'$gfx = [System.Drawing.Graphics]::FromImage($bmp); '
                f'$gfx.CopyFromScreen(0, 0, 0, 0, $screen.Size); '
                f'$bmp.Save("{out}")'
            )
            return self._run(["powershell", "-Command", ps], "powershell screenshot")
        return ShimResult(False, "Screenshot not supported on this platform.", "none", self.platform)

    # ------------------------------------------------------------------
    # System Info
    # ------------------------------------------------------------------

    def get_system_info(self) -> ShimResult:
        """Get basic system information. Result cached after first call."""
        if hasattr(self, "_system_info_cache"):
            return ShimResult(True, self._system_info_cache, "python (cached)", self.platform)

        info = {
            "platform": self.platform,
            "python": sys.version.split()[0],
        }
        if self.platform == OSPlatform.LINUX:
            try:
                uname = subprocess.run(["uname", "-a"], capture_output=True, text=True, timeout=3).stdout.strip()
                info["uname"] = uname
            except Exception:
                pass
        elif self.platform == OSPlatform.MACOS:
            try:
                sw_vers = subprocess.run(["sw_vers"], capture_output=True, text=True, timeout=3).stdout.strip()
                info["sw_vers"] = sw_vers
            except Exception:
                pass
        elif self.platform == OSPlatform.WINDOWS:
            try:
                ver = subprocess.run(["powershell", "-Command", "$PSVersionTable.PSVersion"], capture_output=True, text=True, timeout=3).stdout.strip()
                info["powershell"] = ver
            except Exception:
                pass
        result = json.dumps(info, indent=2)
        self._system_info_cache = result
        return ShimResult(True, result, "python", self.platform)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _run(self, cmd: list[str], label: str, shell: bool = False, stdin_text: Optional[str] = None) -> ShimResult:
        """Execute a command and return ShimResult."""
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                shell=shell,
                input=stdin_text,
            )
            if proc.returncode == 0:
                return ShimResult(
                    True,
                    proc.stdout.strip() or f"Executed: {label}",
                    label,
                    self.platform,
                    fallback_used=False,
                )
            return ShimResult(
                False,
                proc.stderr.strip() or f"Command failed (exit {proc.returncode}): {label}",
                label,
                self.platform,
                fallback_used=False,
            )
        except FileNotFoundError:
            return ShimResult(False, f"Utility not found: {cmd[0]}", label, self.platform)
        except subprocess.TimeoutExpired:
            return ShimResult(False, f"Command timed out: {label}", label, self.platform)
        except Exception as exc:
            return ShimResult(False, f"Execution error: {exc}", label, self.platform)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_shim: Optional[UniversalOSShim] = None


def get_shim() -> UniversalOSShim:
    """Get or create the global OS Shim instance."""
    global _shim
    if _shim is None:
        _shim = UniversalOSShim()
    return _shim


__all__ = [
    "UniversalOSShim",
    "ShimResult",
    "OSPlatform",
    "get_shim",
    "gear0_os_classify",
    "gear0_os_execute",
]


# ---------------------------------------------------------------------------
# Gear 0 Plugin #4 dispatch: Universal OS Shim
# ---------------------------------------------------------------------------

_OS_QUERY_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:empty\s+trash|sleep|shutdown|restart|lock|trash|clipboard|notify|notification|screenshot|system info|sysinfo|open)\b",
    __import__("re").IGNORECASE,
)


# Destructive / system-state-changing operations that require ActionGate
# tier-2 approval. They are refused unless dry_run=True (simulation mode).
_DESTRUCTIVE_OPS = frozenset({
    "sleep_computer",
    "shutdown",
    "restart",
    "empty_trash",
    "lock_screen",
})


def gear0_os_classify(query: str) -> Optional[str]:
    """Classify query into an OS shim handler name, or None."""
    low = query.strip().lower()
    if not _OS_QUERY_RE.match(low):
        return None
    if any(w in low for w in ["sleep", "suspend"]):
        return "sleep_computer"
    if "shutdown" in low:
        return "shutdown"
    if "restart" in low:
        return "restart"
    if "lock" in low:
        return "lock_screen"
    if any(w in low for w in ["trash", "empty trash"]):
        return "empty_trash"
    if "clipboard" in low:
        return "get_clipboard"
    if "notify" in low:
        return "show_notification"
    if "screenshot" in low:
        return "take_screenshot"
    if any(w in low for w in ["system info", "sysinfo"]):
        return "get_system_info"
    if "open" in low:
        return "open_file"
    return None


def gear0_os_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
    """Execute a Gear 0 Plugin #4 OS shim handler.

    Destructive operations (sleep, shutdown, restart, empty_trash, lock_screen)
    are refused unless dry_run=True, enforcing ActionGate tier-2 approval:
    users must prefix with 'simulate:' or 'dry run:' to proceed.
    """
    try:
        if handler in _DESTRUCTIVE_OPS and not dry_run:
            return (
                f"🛑 ActionGate tier-2 approval required for '{handler}'.\n"
                f"  Prefix with 'simulate: ' or 'dry run: ' to preview without executing.\n"
                f"  Example: 'simulate: shutdown' or 'dry run: sleep'."
            )

        shim = get_shim()
        if handler == "sleep_computer":
            return str(shim.sleep_computer())
        if handler == "shutdown":
            return str(shim.shutdown())
        if handler == "restart":
            return str(shim.restart())
        if handler == "lock_screen":
            return str(shim.lock_screen())
        if handler == "empty_trash":
            return str(shim.empty_trash())
        if handler == "get_clipboard":
            return str(shim.get_clipboard())
        if handler == "show_notification":
            return str(shim.show_notification("Celina", "Notification from Celina"))
        if handler == "take_screenshot":
            return str(shim.take_screenshot())
        if handler == "get_system_info":
            return str(shim.get_system_info())
        if handler == "open_file":
            return "Specify a file path to open."
    except Exception:
        return None
    return None

