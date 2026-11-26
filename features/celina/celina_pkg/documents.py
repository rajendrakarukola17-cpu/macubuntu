"""Celina AI (Celly) - Document & LibreOffice Mastery Engine (0 MB Idle RAM)
Lazy-loads python-docx, openpyxl, python-pptx, odfpy, and pypdf ONLY when invoked.
Supports reading, creating, surgically editing, converting (via LibreOffice headless),
and opening documents natively in LibreOffice Writer / Calc / Impress.
"""

import csv
import io
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path

from . import config, state_ops

ApprovalCallback = Callable[[str, str], bool]

_WRITER_EXTS = {".docx", ".odt", ".doc", ".rtf"}
_CALC_EXTS = {".xlsx", ".ods", ".xls", ".csv"}
_IMPRESS_EXTS = {".pptx", ".odp", ".ppt"}


def _is_within_home(path: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(config.HOME.resolve())
        return True
    except ValueError:
        return False


def _is_blocked_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    if resolved == config.ENV_FILE.resolve():
        return True
    return any(part in config.PROTECTED_PATHS for part in resolved.parts)


def backup_file(target: Path) -> Path | None:
    """Saves a timestamped reversible backup in ~/.local/share/celina/backups/."""
    if not target.is_file():
        return None
    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_name = f"{stamp}_{target.name}"
    dest = config.BACKUPS_DIR / backup_name
    shutil.copy2(target, dest)

    # Keep backup folder bounded
    backups = sorted(config.BACKUPS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[config.MAX_FILE_BACKUPS :]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def _read_odt_text(target: Path) -> str:
    """Extracts text from an OpenDocument (.odt) using stdlib zipfile + XML (zero deps!)."""
    with zipfile.ZipFile(target, "r") as zf:
        xml_bytes = zf.read("content.xml")
    root = ET.fromstring(xml_bytes)  # nosec B314 — ODT content.xml is local zip entry; ElementTree does not resolve external entities
    lines: list[str] = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag in {"p", "h"}:
            txt = "".join(elem.itertext()).strip()
            if txt:
                lines.append(txt)
    return "\n".join(lines)


def read_document(path: str, max_chars: int = 4000) -> str:
    """Reads text/data from .docx, .odt, .xlsx, .pptx, .pdf, .csv, or plain text inside ~."""
    target = Path(path).expanduser().resolve()
    if not _is_within_home(target):
        return "Error: Document access is restricted to files inside your home directory."
    if _is_blocked_path(target):
        return "Error: Access to sensitive/protected files is blocked."
    if not target.is_file():
        return f"Error: Document not found: {target}"

    ext = target.suffix.lower()
    cap = max(300, min(10000, int(max_chars)))
    try:
        if ext == ".docx":
            from docx import Document

            doc = Document(str(target))
            parts = [p.text for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
            text = "\n".join(parts)

        elif ext == ".odt":
            text = _read_odt_text(target)

        elif ext == ".xlsx":
            import openpyxl

            wb = openpyxl.load_workbook(str(target), data_only=True, read_only=True)
            parts: list[str] = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                parts.append(f"=== Sheet: {sheet_name} ===")
                row_count = 0
                for row in ws.iter_rows(values_only=True):
                    if any(v is not None for v in row):
                        parts.append(" | ".join("" if v is None else str(v) for v in row))
                        row_count += 1
                        if row_count >= 100:
                            parts.append("...(sheet truncated at 100 rows)")
                            break
            wb.close()
            text = "\n".join(parts)

        elif ext == ".pptx":
            from pptx import Presentation

            prs = Presentation(str(target))
            parts = []
            for idx, slide in enumerate(prs.slides, 1):
                slide_lines = []
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                        slide_lines.append(shape.text_frame.text.strip())
                parts.append(f"--- Slide {idx} ---\n" + "\n".join(slide_lines))
            text = "\n\n".join(parts)

        elif ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(target))
            parts = []
            for idx, page in enumerate(reader.pages[:30], 1):
                ptxt = (page.extract_text() or "").strip()
                if ptxt:
                    parts.append(f"--- Page {idx} ---\n{ptxt}")
            text = "\n\n".join(parts)

        else:
            text = target.read_text(encoding="utf-8", errors="replace")

        if not text.strip():
            return "(Document is empty)"
        return text[:cap] + (f"\n...[truncated at {cap} chars]" if len(text) > cap else "")
    except ImportError as exc:
        return f"Missing document library ({exc}). Run Module 06 to install Office/PDF packages."
    except Exception as exc:
        return f"Failed to read document '{target.name}': {exc}"


def open_document(path: str) -> str:
    """Opens a document directly in LibreOffice Writer/Calc/Impress or the default app."""
    target = Path(path).expanduser().resolve()
    if not _is_within_home(target) or not target.exists():
        return f"Error: File not found inside home directory: {target}"

    ext = target.suffix.lower()
    cmd: list[str]
    if shutil.which("libreoffice") and ext in _WRITER_EXTS:
        cmd = ["libreoffice", "--writer", str(target)]
    elif shutil.which("libreoffice") and ext in _CALC_EXTS:
        cmd = ["libreoffice", "--calc", str(target)]
    elif shutil.which("libreoffice") and ext in _IMPRESS_EXTS:
        cmd = ["libreoffice", "--impress", str(target)]
    elif shutil.which("xdg-open"):
        cmd = ["xdg-open", str(target)]
    else:
        return "Error: Neither libreoffice nor xdg-open found."

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Opened '{target.name}' with {cmd[0]}."
    except Exception as exc:
        return f"Failed to open document: {exc}"


def create_document(
    path: str,
    title: str = "",
    content: str = "",
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """Creates a formatted .docx, .odt, .xlsx, .pptx, or plain-text document after approval."""
    target = Path(path).expanduser().resolve()
    if not _is_within_home(target):
        return "Error: Can only create documents inside your home directory."
    if _is_blocked_path(target):
        return "Error: Target path is protected."
    if approval_cb is None:
        return "Denied: Creating documents requires interactive user approval."

    preview = f"Create: {target}\nTitle: {title or '(none)'}\nContent ({len(content)} chars):\n{content[:300]}"
    if not approval_cb("Approve Document Creation?", preview):
        return "Cancelled by user."

    target.parent.mkdir(parents=True, exist_ok=True)
    aid = state_ops.get_undo_journal().record_before_mutation("create_document", target)
    if target.exists():
        backup_file(target)

    ext = target.suffix.lower()
    try:
        if ext == ".docx":
            from docx import Document

            doc = Document()
            if title:
                doc.add_heading(title, level=1)
            for block in content.splitlines():
                if block.startswith("## "):
                    doc.add_heading(block[3:].strip(), level=2)
                elif block.startswith("- "):
                    doc.add_paragraph(block[2:].strip(), style="List Bullet")
                elif block.strip():
                    doc.add_paragraph(block)
            doc.save(str(target))

        elif ext == ".odt":
            from odf.opendocument import OpenDocumentText
            from odf.text import H, P

            doc = OpenDocumentText()
            if title:
                doc.text.addElement(H(outlinelevel=1, text=title))
            for line in content.splitlines():
                if line.strip():
                    doc.text.addElement(P(text=line))
            doc.save(str(target))

        elif ext == ".xlsx":
            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            if title:
                ws.title = title[:31]
            reader = csv.reader(io.StringIO(content))
            for row in reader:
                if row:
                    ws.append(row)
            wb.save(str(target))

        elif ext == ".pptx":
            from pptx import Presentation

            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = title or "Presentation"
            body = slide.placeholders[1].text_frame
            lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
            if lines:
                body.text = lines[0]
                for ln in lines[1:]:
                    p = body.add_paragraph()
                    p.text = ln.lstrip("- ")
            prs.save(str(target))

        else:
            full = f"# {title}\n\n{content}" if title and ext == ".md" else content
            target.write_text(full, encoding="utf-8")

        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=True, final_path=target)
        return f"Created document: {target} [undo: {aid}]"
    except ImportError as exc:
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
        return f"Missing document library ({exc}). Run Module 06 to install."
    except Exception as exc:
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
        return f"Failed to create document: {exc}"


def edit_document(
    path: str,
    find_text: str = "",
    replace_text: str = "",
    append_text: str = "",
    sheet: str = "",
    cell: str = "",
    cell_value: str = "",
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """Surgically edits .docx, .odt, .xlsx, or .pptx preserving formatting,
    with automatic backup and 1-click approval.
    """
    target = Path(path).expanduser().resolve()
    if not _is_within_home(target) or not target.is_file():
        return f"Error: Document not found inside home directory: {target}"
    if _is_blocked_path(target):
        return "Error: Target path is protected."
    if approval_cb is None:
        return "Denied: Editing documents requires interactive user approval."

    desc_parts = [f"File: {target}"]
    if find_text:
        desc_parts.append(f"Replace: '{find_text[:120]}' -> '{replace_text[:120]}'")
    if append_text:
        desc_parts.append(f"Append: '{append_text[:180]}'")
    if cell:
        desc_parts.append(f"Cell {sheet or 'active'}!{cell} = '{cell_value}'")

    if not approval_cb("Approve Document Edit?", "\n".join(desc_parts)):
        return "Cancelled by user."

    aid = state_ops.get_undo_journal().record_before_mutation("edit_document", target)
    bak = backup_file(target)
    ext = target.suffix.lower()
    succeeded = False
    try:
        if ext == ".docx":
            from docx import Document

            doc = Document(str(target))
            replacements = 0
            if find_text:
                for p in doc.paragraphs:
                    if find_text in p.text:
                        # Prefer run-level replacement to keep inline styles intact
                        replaced_in_run = False
                        for run in p.runs:
                            if find_text in run.text:
                                run.text = run.text.replace(find_text, replace_text)
                                replacements += 1
                                replaced_in_run = True
                        if not replaced_in_run:
                            p.text = p.text.replace(find_text, replace_text)
                            replacements += 1
                for table in doc.tables:
                    for row in table.rows:
                        for c in row.cells:
                            if find_text in c.text:
                                c.text = c.text.replace(find_text, replace_text)
                                replacements += 1
            if append_text:
                for line in append_text.splitlines():
                    if line.strip():
                        doc.add_paragraph(line)
            doc.save(str(target))
            succeeded = True
            return f"Updated '{target.name}' (replacements={replacements}, backup={bak.name if bak else 'none'}). [undo: {aid}]"

        if ext == ".xlsx":
            import openpyxl

            wb = openpyxl.load_workbook(str(target))
            ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
            changes = 0
            if cell:
                # Coerce numeric strings cleanly when appropriate
                val: object = cell_value
                try:
                    val = int(cell_value) if cell_value.isdigit() else float(cell_value)
                except ValueError:
                    val = cell_value
                ws[cell.upper()] = val
                changes += 1
            if find_text:
                for row in ws.iter_rows():
                    for c in row:
                        if isinstance(c.value, str) and find_text in c.value:
                            c.value = c.value.replace(find_text, replace_text)
                            changes += 1
            if append_text:
                for row in csv.reader(io.StringIO(append_text)):
                    if row:
                        ws.append(row)
                        changes += 1
            wb.save(str(target))
            succeeded = True
            return f"Updated spreadsheet '{target.name}' (changes={changes}, backup={bak.name if bak else 'none'}). [undo: {aid}]"

        if ext == ".pptx":
            from pptx import Presentation

            prs = Presentation(str(target))
            changes = 0
            if find_text:
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if getattr(shape, "has_text_frame", False):
                            for p in shape.text_frame.paragraphs:
                                if find_text in p.text:
                                    p.text = p.text.replace(find_text, replace_text)
                                    changes += 1
            if append_text:
                slide = prs.slides.add_slide(prs.slide_layouts[1])
                lines = [ln.strip() for ln in append_text.splitlines() if ln.strip()]
                if lines:
                    slide.shapes.title.text = lines[0]
                    if len(lines) > 1:
                        tf = slide.placeholders[1].text_frame
                        tf.text = lines[1]
                        for ln in lines[2:]:
                            tf.add_paragraph().text = ln
                    changes += 1
            prs.save(str(target))
            succeeded = True
            return f"Updated presentation '{target.name}' (changes={changes}, backup={bak.name if bak else 'none'}). [undo: {aid}]"

        if ext == ".odt":
            # Safe XML DOM text update inside ODT zip archive (never mutates tags or attributes!)
            with zipfile.ZipFile(target, "r") as zin:
                items = {name: zin.read(name) for name in zin.namelist()}
            raw_xml = items["content.xml"]
            # Preserve all original ODF namespace prefixes before serializing back
            for _ev, (prefix, uri) in ET.iterparse(io.BytesIO(raw_xml), events=("start-ns",)):  # nosec B314 — ODT content.xml is local zip entry; namespace registration only
                if prefix:
                    ET.register_namespace(prefix, uri)
            root = ET.fromstring(raw_xml)  # nosec B314 — ODT content.xml is local zip entry; ElementTree does not resolve external entities
            changes = 0
            if find_text:
                for elem in root.iter():
                    if elem.text and find_text in elem.text:
                        changes += elem.text.count(find_text)
                        elem.text = elem.text.replace(find_text, replace_text)
                    if elem.tail and find_text in elem.tail:
                        changes += elem.tail.count(find_text)
                        elem.tail = elem.tail.replace(find_text, replace_text)
            if append_text:
                office_ns = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
                office_text = root.find(f".//{{{office_ns}}}text")
                if office_text is not None:
                    for line in append_text.splitlines():
                        p_elem = ET.SubElement(office_text, f"{{{text_ns}}}p")
                        p_elem.text = line
                        changes += 1
            items["content.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            tmp = target.with_suffix(".odt.tmp")
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                if "mimetype" in items:
                    zout.writestr("mimetype", items["mimetype"], compress_type=zipfile.ZIP_STORED)
                for name, data in items.items():
                    if name == "mimetype":
                        continue
                    zout.writestr(name, data)
            os.replace(tmp, target)
            succeeded = True
            return f"Updated ODT '{target.name}' (changes={changes}, backup={bak.name if bak else 'none'}). [undo: {aid}]"

        # Plain text / Markdown / code
        orig = target.read_text(encoding="utf-8", errors="replace")
        updated = orig.replace(find_text, replace_text) if find_text else orig
        if append_text:
            updated = updated.rstrip("\n") + "\n" + append_text + "\n"
        tmp = target.with_name(f".{target.name}.celina.tmp")
        tmp.write_text(updated, encoding="utf-8")
        os.replace(tmp, target)
        succeeded = True
        return f"Updated '{target.name}' (backup={bak.name if bak else 'none'}). [undo: {aid}]"

    except ImportError as exc:
        return f"Missing document library ({exc}). Run Module 06 to install."
    except Exception as exc:
        return f"Failed to edit document: {exc}"
    finally:
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=succeeded, final_path=target)


def convert_document(
    path: str,
    target_format: str = "pdf",
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """Converts a document using LibreOffice headless (e.g. docx -> pdf, odt -> docx)."""
    target = Path(path).expanduser().resolve()
    if not _is_within_home(target) or not target.is_file():
        return f"Error: Document not found inside home directory: {target}"
    fmt = target_format.lower().strip().lstrip(".")
    if fmt not in {"pdf", "docx", "odt", "xlsx", "ods", "pptx", "odp", "txt", "html"}:
        return f"Unsupported conversion format '{target_format}'."
    if not shutil.which("libreoffice"):
        return "Error: libreoffice is not installed."
    if approval_cb is None:
        return "Denied: Document conversion requires user approval."
    if not approval_cb("Approve LibreOffice Conversion?", f"Convert {target.name} → .{fmt}"):
        return "Cancelled by user."

    out_file = target.with_suffix(f".{fmt}")
    aid = state_ops.get_undo_journal().record_before_mutation("convert_document", out_file)
    try:
        res = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", fmt, "--outdir", str(target.parent), str(target)],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if res.returncode != 0:
            state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
            return f"Conversion failed: {(res.stderr or res.stdout).strip()}"
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=True, final_path=out_file)
        return f"Converted successfully: {out_file} [undo: {aid}]"
    except subprocess.TimeoutExpired:
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
        return "Error: LibreOffice conversion timed out."
    except Exception as exc:
        state_ops.get_undo_journal().finalize_mutation(aid, succeeded=False)
        return f"Conversion error: {exc}"
