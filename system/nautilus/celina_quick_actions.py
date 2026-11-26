"""
macubuntu Finder Quick Actions for Ubuntu Files (Nautilus Python Extension)
Adds macOS-style right-click Quick Actions directly inside Files:
  - Ask Celina About File
  - Summarize Document (Offline/Privacy-Gated)
  - Convert Document to PDF
  - Send via LocalSend (Cross-OS AirDrop)
Runs strictly as the current user with zero background RAM overhead when idle.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    import gi

    gi.require_version("Nautilus", "4.0")
    from gi.repository import GObject, Nautilus
except Exception:
    # Graceful fallback for Nautilus 3.x or non-desktop test environments
    try:
        import gi

        gi.require_version("Nautilus", "3.0")
        from gi.repository import GObject, Nautilus
    except Exception:
        GObject = None  # type: ignore[assignment]
        Nautilus = None  # type: ignore[assignment]


_CONVERTIBLE_EXTS = frozenset({
    ".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".pptx", ".ppt", ".odp", ".txt", ".md",
})
_READABLE_EXTS = _CONVERTIBLE_EXTS | frozenset({".pdf", ".csv", ".json", ".py", ".sh", ".yaml", ".yml"})


def _uri_to_local_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return ""
    return unquote(parsed.path)


def _celina_bin() -> str:
    local = Path.home() / ".local" / "bin" / "celina"
    if local.exists():
        return str(local)
    return shutil.which("celina") or ""


def _spawn_detached(argv: list[str]) -> None:
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


if GObject is not None and Nautilus is not None:

    class CelinaQuickActionsProvider(GObject.GObject, Nautilus.MenuProvider):  # type: ignore[misc]
        """Provides macOS-style Quick Actions in Nautilus context menus."""

        def get_file_items(self, *args):
            files = args[-1] if args else []
            if not files or len(files) != 1:
                return []
            fpath = _uri_to_local_path(files[0].get_uri())
            if not fpath or not os.path.isfile(fpath):
                return []

            ext = Path(fpath).suffix.lower()
            items = []

            celina = _celina_bin()
            if celina and ext in _READABLE_EXTS:
                m_sum = Nautilus.MenuItem(
                    name="MacubuntuQuickActions::Summarize",
                    label="✨ Quick Action: Summarize with Celina",
                    tip="Read and summarize this file locally with Celina",
                )
                m_sum.connect("activate", self._on_summarize, celina, fpath)
                items.append(m_sum)

            if ext in _CONVERTIBLE_EXTS and shutil.which("libreoffice"):
                m_pdf = Nautilus.MenuItem(
                    name="MacubuntuQuickActions::ConvertPDF",
                    label="📄 Quick Action: Convert to PDF",
                    tip="Convert this document to PDF via LibreOffice",
                )
                m_pdf.connect("activate", self._on_convert_pdf, fpath)
                items.append(m_pdf)

            if shutil.which("flatpak"):
                m_ls = Nautilus.MenuItem(
                    name="MacubuntuQuickActions::LocalSend",
                    label="📡 Quick Action: Share via LocalSend (AirDrop)",
                    tip="Send this file to iPhone, Android, Windows, Mac, or Linux on LAN",
                )
                m_ls.connect("activate", self._on_localsend, fpath)
                items.append(m_ls)

            return items

        def _on_summarize(self, _menu, celina: str, fpath: str) -> None:
            island = Path.home() / ".local" / "bin" / "celina-island"
            if island.exists():
                _spawn_detached([str(island), "--ask", f"Summarize this file concisely: {fpath}"])
            else:
                _spawn_detached([celina, f"Summarize this file concisely: {fpath}"])

        def _on_convert_pdf(self, _menu, fpath: str) -> None:
            if shutil.which("libreoffice"):
                out_dir = str(Path(fpath).parent)
                _spawn_detached(["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", out_dir, fpath])

        def _on_localsend(self, _menu, fpath: str) -> None:
            _spawn_detached(["flatpak", "run", "org.localsend.localsend_app", fpath])
