"""Tests for Item 3, 5, 9: LibreOffice UNO bridge edit, wayland_gui fallback, router UNO preference, Stage 10 & Stage 45."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_pkg import libreoffice_uno, router, state_ops, tools  # noqa: E402


class _FakeReplaceDesc:
    SearchString = ""
    ReplaceString = ""


class _FakeCursor:
    def __init__(self):
        self.ended = False

    def gotoEnd(self, _expand):
        self.ended = True


class _FakeText:
    def __init__(self):
        self.inserted: list[str] = []

    def createTextCursor(self):
        return _FakeCursor()

    def insertString(self, _cursor, text, _absorb):
        self.inserted.append(text)


class _FakeCell:
    def __init__(self):
        self.val = None

    def setValue(self, v):
        self.val = v

    def setString(self, s):
        self.val = s


class _FakeSheet:
    def __init__(self):
        self.cells: dict[str, _FakeCell] = {}

    def getCellRangeByName(self, ref):
        return self.cells.setdefault(ref, _FakeCell())


class _FakeSheets:
    def __init__(self):
        self.sheet = _FakeSheet()

    def getByIndex(self, _idx):
        return self.sheet


class _FakeUnoDoc:
    def __init__(self):
        self.Text = _FakeText()
        self.Sheets = _FakeSheets()
        self.stored = False
        self.closed = False
        self.exported_urls: list[str] = []
        self.replaced: list[tuple[str, str]] = []

    def createReplaceDescriptor(self):
        return _FakeReplaceDesc()

    def replaceAll(self, rd):
        self.replaced.append((rd.SearchString, rd.ReplaceString))
        return 2

    def store(self):
        self.stored = True

    def storeToURL(self, url, _props):
        self.exported_urls.append(url)

    def close(self, _deliver):
        self.closed = True


class _FakeDesktop:
    def __init__(self, doc: _FakeUnoDoc):
        self.doc = doc
        self.loaded_urls: list[str] = []

    def loadComponentFromURL(self, url, _target, _flags, _props):
        self.loaded_urls.append(url)
        return self.doc


class _FakeUnoMod:
    pass


def test_libreoffice_edit_metadata_approval_and_uno_operations(tmp_path: Path, monkeypatch):
    # Verify @tool metadata
    assert tools.libreoffice_edit.tool_name == "libreoffice_edit"
    assert tools.libreoffice_edit.tier == 2
    assert tools.libreoffice_edit.domain == "files_office"

    # Isolate home & undo journal inside tmp_path
    doc_file = tmp_path / "quarterly.odt"
    doc_file.write_text("draft content", encoding="utf-8")
    monkeypatch.setattr(tools, "_is_within_home", lambda p: str(p).startswith(str(tmp_path)))
    monkeypatch.setattr(state_ops, "get_undo_journal", lambda: state_ops.UndoJournal(tmp_path / "undo"))

    # 1. Fail-closed when approval is denied
    denied = tools.libreoffice_edit(
        str(doc_file),
        "replace_text",
        {"find": "draft", "replace": "final"},
        approval_cb=lambda _t, _d: False,
    )
    assert "cancelled" in denied.lower() or "denied" in denied.lower()

    # 2. Mock UNO desktop -> replace_text, append_text, set_cell, export_pdf all succeed + record undo handle
    fake_doc = _FakeUnoDoc()
    fake_desktop = _FakeDesktop(fake_doc)
    monkeypatch.setattr(libreoffice_uno, "connect_uno_desktop", lambda **_kw: (_FakeUnoMod(), fake_desktop))

    out_rep = tools.libreoffice_edit(
        str(doc_file),
        "replace_text",
        {"find": "draft", "replace": "final"},
        approval_cb=lambda _t, _d: True,
    )
    assert "UNO edited" in out_rep and "[undo:" in out_rep
    assert fake_doc.replaced == [("draft", "final")]
    assert fake_doc.stored is True and fake_doc.closed is True

    out_app = tools.libreoffice_edit(
        str(doc_file),
        "append_text",
        {"text": "\nSigned off."},
        approval_cb=lambda _t, _d: True,
    )
    assert "appended" in out_app
    assert fake_doc.Text.inserted == ["\nSigned off."]

    out_cell = tools.libreoffice_edit(
        str(doc_file),
        "set_cell",
        {"sheet": 0, "cell": "B2", "value": 42.5},
        approval_cb=lambda _t, _d: True,
    )
    assert "B2" in out_cell
    assert fake_doc.Sheets.sheet.cells["B2"].val == 42.5

    pdf_out = tmp_path / "quarterly.pdf"
    out_pdf = tools.libreoffice_edit(
        str(doc_file),
        "export_pdf",
        {"output_path": str(pdf_out)},
        approval_cb=lambda _t, _d: True,
    )
    assert "UNO exported" in out_pdf and fake_doc.exported_urls


def test_libreoffice_edit_falls_back_to_wayland_gui_when_uno_unavailable(tmp_path: Path, monkeypatch):
    doc_file = tmp_path / "budget.ods"
    doc_file.write_text("sheet", encoding="utf-8")
    monkeypatch.setattr(tools, "_is_within_home", lambda p: str(p).startswith(str(tmp_path)))
    monkeypatch.setattr(state_ops, "get_undo_journal", lambda: state_ops.UndoJournal(tmp_path / "undo"))

    def _no_uno(**_kw):
        raise libreoffice_uno.UnoUnavailableError("UNO socket 2002 closed")

    monkeypatch.setattr(libreoffice_uno, "connect_uno_desktop", _no_uno)
    monkeypatch.setattr(tools.documents, "open_document", lambda p: f"Opened {Path(p).name}")
    monkeypatch.setattr(tools.automation, "inspect_ui_elements", lambda max_chars=800: "# A11Y(window|Calc)")

    res = tools.libreoffice_edit(
        str(doc_file),
        "replace_text",
        {"find": "old", "replace": "new"},
        approval_cb=lambda _t, _d: True,
    )
    assert "[fallback=wayland_gui]" in res
    assert "UNO unavailable" in res
    assert "[undo:" in res


def test_router_prefers_uno_over_gui_and_stage10_stage45_patched():
    # Router prefers UNO (`libreoffice_edit`) over `wayland_gui` when UNO is available
    gui_dec = router.RouteDecision(tool_name="autonomous_gui_task", domain="wayland_gui", tier=2)
    pref_uno = router.apply_offline_and_uno_policy(
        gui_dec,
        goal="edit report.odt in LibreOffice Writer",
        uno_probe=lambda: True,
    )
    assert isinstance(pref_uno, router.RouteDecision)
    assert pref_uno.tool_name == "libreoffice_edit"
    assert pref_uno.domain == "files_office"
    assert pref_uno.preferred_backend == "uno"

    # When UNO is unavailable, falls back to wayland_gui
    pref_gui = router.apply_offline_and_uno_policy(
        gui_dec,
        goal="edit report.odt in LibreOffice Writer",
        uno_probe=lambda: False,
    )
    assert isinstance(pref_gui, router.RouteDecision)
    assert pref_gui.tool_name == "autonomous_gui_task"
    assert pref_gui.preferred_backend == "wayland_gui"

    # Stage 10 includes python3-uno & Stage 45 writes ~/.config/autostart/libreoffice-uno.desktop
    st10 = (REPO_ROOT / "stages" / "10-base-deps.sh").read_text(encoding="utf-8")
    assert "python3-uno" in st10
    st45 = (REPO_ROOT / "stages" / "45-apps-backups-search.sh").read_text(encoding="utf-8")
    assert "libreoffice-uno.desktop" in st45
    assert 'libreoffice --headless --accept="socket,host=localhost,port=2002;urp;"' in st45
