"""Celina AI — LibreOffice UNO Bridge with Automatic Wayland GUI Fallback (files_office)

Prefers structured headless UNO edits over fragile pixel/GUI automation when
`python3-uno` and the local LibreOffice UNO socket (`localhost:2002`) are available:
  libreoffice --headless --accept="socket,host=localhost,port=2002;urp;"

Automatically falls back to `wayland_gui` GUI automation if UNO is unavailable.
"""

from __future__ import annotations

import importlib.util
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config

DEFAULT_UNO_HOST = "localhost"
SUPPORTED_UNO_ACTIONS: frozenset[str] = frozenset({
    "replace_text",
    "append_text",
    "set_cell",
    "export_pdf",
})


class UnoUnavailableError(RuntimeError):
    """Raised when `python3-uno` or the LibreOffice UNO socket is unavailable."""


def get_uno_port() -> int:
    return config.get_libreoffice_uno_port()


def build_uno_autostart_command(port: int | None = None) -> list[str]:
    p = int(port) if port is not None else get_uno_port()
    return [
        "libreoffice",
        "--headless",
        f"--accept=socket,host={DEFAULT_UNO_HOST},port={p};urp;",
    ]


def is_uno_module_installed() -> bool:
    try:
        return importlib.util.find_spec("uno") is not None
    except Exception:
        return False


def is_uno_socket_open(
    host: str = DEFAULT_UNO_HOST,
    port: int | None = None,
    timeout: float = 0.5,
) -> bool:
    p = int(port) if port is not None else get_uno_port()
    try:
        with socket.create_connection((host, p), timeout=float(timeout)):
            return True
    except OSError:
        return False


def is_uno_available(
    host: str = DEFAULT_UNO_HOST,
    port: int | None = None,
    timeout: float = 0.5,
) -> bool:
    """Returns True only when both `python3-uno` and the UNO TCP socket are ready."""
    return is_uno_module_installed() and is_uno_socket_open(host=host, port=port, timeout=timeout)


def _to_file_url(path: Path) -> str:
    return path.expanduser().resolve().as_uri()


def connect_uno_desktop(host: str = DEFAULT_UNO_HOST, port: int | None = None) -> tuple[Any, Any]:
    """Connects to the running headless LibreOffice UNO instance and returns (uno_mod, desktop)."""
    p = int(port) if port is not None else get_uno_port()
    try:
        import uno  # type: ignore[import-not-found]
    except Exception as exc:
        raise UnoUnavailableError(
            "python3-uno is not installed (install via: sudo apt install python3-uno).",
        ) from exc

    if not is_uno_socket_open(host=host, port=p, timeout=0.5):
        raise UnoUnavailableError(
            f"LibreOffice UNO socket {host}:{p} is not listening. "
            f'Start via: libreoffice --headless --accept="socket,host={host},port={p};urp;"',
        )

    try:
        local_ctx = uno.getComponentContext()
        resolver = local_ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local_ctx,
        )
        ctx = resolver.resolve(f"uno:socket,host={host},port={p};urp;StarOffice.ComponentContext")
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        return uno, desktop
    except Exception as exc:
        raise UnoUnavailableError(f"Failed to connect to LibreOffice UNO ({host}:{p}): {exc}") from exc


def edit_document_via_uno(
    path: Path,
    action: str,
    params: dict[str, Any] | None = None,
    *,
    host: str = DEFAULT_UNO_HOST,
    port: int | None = None,
) -> str:
    """Executes a structured document edit through the LibreOffice UNO bridge."""
    act = (action or "").strip().lower()
    if act not in SUPPORTED_UNO_ACTIONS:
        raise ValueError(
            f"Unsupported libreoffice_edit action '{action}'. Supported: {sorted(SUPPORTED_UNO_ACTIONS)}",
        )
    pdict = dict(params or {})
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Document not found: {resolved}")

    uno_mod, desktop = connect_uno_desktop(host=host, port=port)

    def _prop(name: str, value: Any) -> Any:
        prop_cls = getattr(uno_mod, "createUnoStruct", None)
        if callable(prop_cls):
            pv = prop_cls("com.sun.star.beans.PropertyValue")
            pv.Name = name
            pv.Value = value
            return pv
        return {"Name": name, "Value": value}

    hidden_props = (_prop("Hidden", True),)
    doc = desktop.loadComponentFromURL(_to_file_url(resolved), "_blank", 0, hidden_props)
    if doc is None:
        raise UnoUnavailableError(f"LibreOffice UNO could not open document: {resolved}")

    try:
        if act == "replace_text":
            find_str = str(pdict.get("find", ""))
            replace_str = str(pdict.get("replace", ""))
            if not find_str:
                raise ValueError("replace_text requires non-empty params['find'].")
            rd = doc.createReplaceDescriptor()
            rd.SearchString = find_str
            rd.ReplaceString = replace_str
            count = int(doc.replaceAll(rd))
            doc.store()
            return f"UNO edited '{resolved.name}': replaced {count} occurrence(s) of '{find_str}'."

        if act == "append_text":
            text = str(pdict.get("text", ""))
            if not text:
                raise ValueError("append_text requires non-empty params['text'].")
            cursor = doc.Text.createTextCursor()
            cursor.gotoEnd(False)
            doc.Text.insertString(cursor, text, False)
            doc.store()
            return f"UNO edited '{resolved.name}': appended {len(text)} character(s)."

        if act == "set_cell":
            sheet_idx = int(pdict.get("sheet", 0))
            cell_ref = str(pdict.get("cell", "A1")).strip().upper()
            val = pdict.get("value", "")
            sheet = doc.Sheets.getByIndex(sheet_idx)
            cell = sheet.getCellRangeByName(cell_ref)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cell.setValue(float(val))
            else:
                cell.setString(str(val))
            doc.store()
            return f"UNO edited '{resolved.name}': set sheet[{sheet_idx}]!{cell_ref} = {val!r}."

        # export_pdf
        out_raw = pdict.get("output_path")
        out_path = Path(str(out_raw)).expanduser().resolve() if out_raw else resolved.with_suffix(".pdf")
        filter_name = str(pdict.get("filter_name") or "writer_pdf_Export")
        doc.storeToURL(_to_file_url(out_path), (_prop("FilterName", filter_name),))
        return f"UNO exported '{resolved.name}' to PDF: {out_path}"
    finally:
        try:
            doc.close(True)
        except Exception:
            pass


def fallback_wayland_gui_edit(
    path: Path,
    action: str,
    params: dict[str, Any] | None = None,
    *,
    open_doc_fn: Callable[[str], str] | None = None,
    gui_runner: Callable[[str, dict[str, Any]], str] | None = None,
    reason: str = "",
) -> str:
    """Falls back to `wayland_gui` GUI automation when the UNO socket/module is unavailable.
    Never crashes in headless environments; delegates cleanly to `wayland_gui` hooks.
    """
    pdict = dict(params or {})
    resolved = path.expanduser().resolve()
    open_note = ""
    if callable(open_doc_fn):
        try:
            open_note = str(open_doc_fn(str(resolved)))
        except Exception as exc:
            open_note = f"open_document error: {exc}"

    goal = (
        f"Edit LibreOffice document '{resolved.name}' using wayland_gui fallback "
        f"(action={action}, params={pdict})"
    )
    if callable(gui_runner):
        gui_out = str(gui_runner(goal, {"path": str(resolved), "action": action, "params": pdict}))
        return (
            f"[fallback=wayland_gui] UNO unavailable ({reason or 'socket/module missing'}); "
            f"delegated to wayland_gui: {gui_out}"
        )

    from . import automation

    try:
        blueprint = automation.inspect_ui_elements(max_chars=800)
    except Exception as exc:
        blueprint = f"(wayland_gui blueprint unavailable: {exc})"
    return (
        f"[fallback=wayland_gui] UNO unavailable ({reason or 'socket/module missing'}). "
        f"Opened '{resolved.name}' ({open_note or 'ready'}) for action '{action}'. "
        f"UI context: {str(blueprint)[:240]}"
    )
