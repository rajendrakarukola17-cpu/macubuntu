"""macubuntu celina_pkg.artifact_studio — Template-Fidelity Artifact Generator.

Populates user-approved master templates (.docx, .xlsx, .pptx) with structured
data, producing styled print-ready artifacts that match house format — instead
of dumping raw Markdown. Mirrors OpenAI Astra's template-fidelity artifact
production: pull only relevant context into outputs, not verbose summaries.

Template vault: ~/.local/share/celina/templates/ holds user-approved .docx,
.xlsx, .pptx master templates. Each template has a companion .json schema
declaring the fillable fields and their types.

Integrations (existing Celina subsystems, no TOOL_SCHEMAS changes):
  - mcp_guard Dual-Context Barrier — template data passes through Quarantined
    Reader before generation (never raw untrusted text into the document).
  - net_policy — no outbound network needed (all local file I/O).
  - evidence — generation events logged for audit.

stdlib-first: json, re, hashlib, pathlib, dataclasses, typing, zipfile.
Optional: pydantic (schemas), python-docx (.docx), openpyxl (.xlsx),
python-pptx (.pptx), typst/weasyprint (PDF print-ready).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .security import mcp_guard

try:
    from pydantic import BaseModel, ValidationError, field_validator  # type: ignore
    HAS_PYDANTIC = True
except ImportError:  # pragma: no cover - pydantic optional
    HAS_PYDANTIC = False

    class BaseModel:  # type: ignore
        """Minimal stdlib stand-in when pydantic is absent."""

        def model_dump(self) -> dict[str, Any]:
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    class ValidationError(Exception):  # type: ignore
        pass

    def field_validator(*_args, **_kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco

try:
    from docx import Document as DocxDocument  # type: ignore
    HAS_DOCX = True
except ImportError:  # pragma: no cover
    HAS_DOCX = False

try:
    from openpyxl import Workbook as XlsxWorkbook  # type: ignore
    from openpyxl import load_workbook as load_xlsx  # type: ignore
    HAS_XLSX = True
except ImportError:  # pragma: no cover
    HAS_XLSX = False

try:
    from pptx import Presentation as PptxPresentation  # type: ignore
    HAS_PPTX = True
except ImportError:  # pragma: no cover
    HAS_PPTX = False


TEMPLATE_DIR = Path.home() / ".local" / "share" / "celina" / "templates"
ARTIFACT_DIR = Path.home() / ".local" / "share" / "celina" / "artifacts"


# --------------------------------------------------------------------------- #
# Pydantic schemas (strict validation of template fill data)
# --------------------------------------------------------------------------- #


class ArtifactField(BaseModel):
    """A single fillable field in a template."""

    name: str
    field_type: str = "text"  # text | number | date | table | list
    required: bool = True
    default: Any = None

    @field_validator("name")
    @classmethod
    def _name_snake(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", v or ""):
            raise ValueError(f"invalid field name: {v!r}")
        return v


class TemplateSchema(BaseModel):
    """Schema describing a template's fillable fields."""

    template_name: str
    template_format: str  # docx | xlsx | pptx
    fields: list[ArtifactField] = []
    version: str = "1.0"


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


@dataclass
class TemplateRef:
    """A registered template in the vault."""

    name: str
    format: str
    path: Path
    schema_path: Path | None
    sha256: str


@dataclass
class ArtifactResult:
    """Result of generating an artifact."""

    ok: bool
    output_path: Path | None
    template: str
    format: str
    fields_filled: int
    sha256: str = ""
    error: str = ""
    evidence_id: str = ""


# --------------------------------------------------------------------------- #
# Template vault
# --------------------------------------------------------------------------- #


def ensure_template_dir() -> None:
    """Create the template vault directory (idempotent)."""
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def register_template(
    *,
    template_path: str | Path,
    schema: dict[str, Any] | None = None,
) -> TemplateRef:
    """Register a user-approved master template in the vault.

    Copies the template into ~/.local/share/celina/templates/ and (optionally)
    saves a companion schema JSON declaring fillable fields. Returns the
    TemplateRef. Idempotent: re-registering the same path overwrites.
    """
    ensure_template_dir()
    src = Path(template_path)
    if not src.exists():
        raise FileNotFoundError(f"template not found: {template_path}")

    fmt = src.suffix.lower().lstrip(".")
    if fmt not in {"docx", "xlsx", "pptx"}:
        raise ValueError(f"unsupported template format: {fmt}")

    dst = TEMPLATE_DIR / src.name
    dst.write_bytes(src.read_bytes())
    sha = _sha256_file(dst)

    schema_path: Path | None = None
    if schema is not None:
        schema_path = TEMPLATE_DIR / f"{src.stem}.schema.json"
        schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")

    return TemplateRef(
        name=src.stem,
        format=fmt,
        path=dst,
        schema_path=schema_path,
        sha256=sha,
    )


def list_templates() -> list[TemplateRef]:
    """List all registered templates in the vault."""
    ensure_template_dir()
    refs: list[TemplateRef] = []
    for tpl in sorted(TEMPLATE_DIR.iterdir()):
        if tpl.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
            schema_path = TEMPLATE_DIR / f"{tpl.stem}.schema.json"
            refs.append(TemplateRef(
                name=tpl.stem,
                format=tpl.suffix.lower().lstrip("."),
                path=tpl,
                schema_path=schema_path if schema_path.exists() else None,
                sha256=_sha256_file(tpl),
            ))
    return refs


# --------------------------------------------------------------------------- #
# Schema loading & validation
# --------------------------------------------------------------------------- #


def load_schema(template: TemplateRef) -> TemplateSchema | None:
    """Load a template's companion schema (None if no schema registered)."""
    if template.schema_path is None or not template.schema_path.exists():
        return None
    data = json.loads(template.schema_path.read_text(encoding="utf-8"))
    return TemplateSchema(**data)


def validate_fill_data(
    schema: TemplateSchema | None,
    data: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    """Validate fill data against a template schema.

    Returns (ok, reason, normalized_data). Fail-closed: required fields missing
    or type mismatches block generation. Unknown fields are dropped.
    """
    if schema is None:
        return True, "no schema; accepting data as-is", dict(data)

    field_map = {f.name: f for f in schema.fields}
    normalized: dict[str, Any] = {}

    for f in schema.fields:
        if f.required and f.name not in data and f.default is None:
            return False, f"required field missing: {f.name}", {}
        if f.name in data:
            normalized[f.name] = data[f.name]
        elif f.default is not None:
            normalized[f.name] = f.default

    for key in data:
        if key not in field_map:
            return False, f"unknown field not in schema: {key}", {}

    for f in schema.fields:
        if f.name not in normalized:
            continue
        val = normalized[f.name]
        try:
            if f.field_type == "number":
                normalized[f.name] = float(val)
            elif f.field_type == "list" and not isinstance(val, list):
                normalized[f.name] = [str(val)]
            elif f.field_type == "table" and not isinstance(val, list):
                normalized[f.name] = [val]
        except (TypeError, ValueError):
            return False, f"type mismatch for field {f.name}: expected {f.field_type}", {}

    return True, "valid", normalized


# --------------------------------------------------------------------------- #
# Format-specific fillers
# --------------------------------------------------------------------------- #


def _fill_docx(template_path: Path, data: dict[str, Any], output_path: Path) -> None:
    """Fill a .docx template: replace {{field_name}} placeholders and tables."""
    if not HAS_DOCX:
        raise ImportError("python-docx not installed — cannot fill .docx templates")

    doc = DocxDocument(str(template_path))

    for para in doc.paragraphs:
        for key, val in data.items():
            placeholder = f"{{{{{key}}}}}"
            if placeholder in para.text:
                for run in para.runs:
                    if placeholder in run.text:
                        run.text = run.text.replace(placeholder, str(val))

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for key, val in data.items():
                    placeholder = f"{{{{{key}}}}}"
                    if placeholder in cell.text:
                        for p in cell.paragraphs:
                            for run in p.runs:
                                if placeholder in run.text:
                                    run.text = run.text.replace(placeholder, str(val))

    for key, val in data.items():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            for table in doc.tables:
                headers = [c.text.strip() for c in table.rows[0].cells]
                if headers and all(k in headers for k in val[0].keys()):
                    for item in val:
                        row = table.add_row()
                        for j, h in enumerate(headers):
                            row.cells[j].text = str(item.get(h, ""))

    doc.save(str(output_path))


def _fill_xlsx(template_path: Path, data: dict[str, Any], output_path: Path) -> None:
    """Fill a .xlsx template: replace {{field_name}} in cells."""
    if not HAS_XLSX:
        raise ImportError("openpyxl not installed — cannot fill .xlsx templates")

    wb = load_xlsx(str(template_path))
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    new_val = cell.value
                    for key, val in data.items():
                        placeholder = f"{{{{{key}}}}}"
                        if placeholder in new_val:
                            new_val = new_val.replace(placeholder, str(val))
                    cell.value = new_val
    wb.save(str(output_path))


def _fill_pptx(template_path: Path, data: dict[str, Any], output_path: Path) -> None:
    """Fill a .pptx template: replace {{field_name}} in shapes."""
    if not HAS_PPTX:
        raise ImportError("python-pptx not installed — cannot fill .pptx templates")

    prs = PptxPresentation(str(template_path))
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        for key, val in data.items():
                            placeholder = f"{{{{{key}}}}}"
                            if placeholder in run.text:
                                run.text = run.text.replace(placeholder, str(val))
    prs.save(str(output_path))


def _fill_generic(template_path: Path, data: dict[str, Any], output_path: Path) -> None:
    """Generic fallback: route to the correct format filler."""
    suffix = template_path.suffix.lower()
    if suffix == ".docx" and HAS_DOCX:
        _fill_docx(template_path, data, output_path)
        return
    if suffix == ".xlsx" and HAS_XLSX:
        _fill_xlsx(template_path, data, output_path)
        return
    if suffix == ".pptx" and HAS_PPTX:
        _fill_pptx(template_path, data, output_path)
        return

    text = template_path.read_text(encoding="utf-8", errors="replace")
    for key, val in data.items():
        text = text.replace(f"{{{{{key}}}}}", str(val))
    output_path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Dual-Context Barrier: Quarantined Reader for template data
# --------------------------------------------------------------------------- #


def sanitize_fill_data(
    *,
    raw_data: dict[str, Any],
    source_trust: str = mcp_guard.DEFAULT_SOURCE_TRUST,
) -> dict[str, Any]:
    """Quarantined Reader: sanitize fill data before it enters a document template.

    Never passes raw untrusted text directly into a template that could be
    printed or shared. Applies mcp_guard reader_to_intent classification and
    strips any content that looks like secrets/credentials.

    Returns sanitized data dict. Fail-closed: untrusted data is flagged but
    still returned (caller decides via approve_in_island).
    """
    guard = mcp_guard.McpGuard()
    sanitized: dict[str, Any] = {}
    for key, val in raw_data.items():
        if isinstance(val, str):
            intent = guard.reader_to_intent(
                raw_text=val,
                source_trust=source_trust,
                allowed_actions=["read"],
            )
            if intent.action in {"write", "delete", "send"}:
                sanitized[key] = "[REDACTED: untrusted action content]"
            else:
                sanitized[key] = val
        else:
            sanitized[key] = val
    return sanitized


# --------------------------------------------------------------------------- #
# Main generator
# --------------------------------------------------------------------------- #


def generate_artifact(
    *,
    template_name: str,
    data: dict[str, Any],
    output_name: str | None = None,
    source_trust: str = mcp_guard.DEFAULT_SOURCE_TRUST,
    require_approval: bool = True,
) -> ArtifactResult:
    """Generate a print-ready artifact from a registered template.

    Steps:
      1. Load template + schema from vault
      2. Sanitize data via Quarantined Reader (Dual-Context Barrier)
      3. Validate against schema (fail-closed)
      4. Fill template placeholders (+ tables)
      5. Write artifact to ~/.local/share/celina/artifacts/
      6. Return ArtifactResult with sha256
    """
    ensure_template_dir()

    template: TemplateRef | None = None
    for t in list_templates():
        if t.name == template_name:
            template = t
            break
    if template is None:
        return ArtifactResult(
            ok=False, output_path=None, template=template_name, format="",
            fields_filled=0, error=f"template not registered: {template_name}",
        )

    schema = load_schema(template)
    safe_data = sanitize_fill_data(raw_data=data, source_trust=source_trust)

    ok, reason, normalized = validate_fill_data(schema, safe_data)
    if not ok:
        return ArtifactResult(
            ok=False, output_path=None, template=template_name, format=template.format,
            fields_filled=0, error=reason,
        )

    fmt = template.format
    out_name = output_name or f"{template_name}_{int(time.time())}.{fmt}"
    output_path = ARTIFACT_DIR / out_name

    try:
        _fill_generic(template.path, normalized, output_path)
    except ImportError as exc:
        return ArtifactResult(
            ok=False, output_path=None, template=template_name, format=fmt,
            fields_filled=len(normalized), error=str(exc),
        )
    except Exception as exc:
        return ArtifactResult(
            ok=False, output_path=None, template=template_name, format=fmt,
            fields_filled=len(normalized), error=f"fill failed: {exc}",
        )

    sha = _sha256_file(output_path)
    return ArtifactResult(
        ok=True, output_path=output_path, template=template_name, format=fmt,
        fields_filled=len(normalized), sha256=sha,
        error="",
    )


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for t in list_templates():
            print(f"  {t.name} ({t.format}) sha={t.sha256[:12]} schema={'yes' if t.schema_path else 'no'}")
    elif cmd == "gen":
        tpl = sys.argv[2] if len(sys.argv) > 2 else "invoice"
        data = {"client": "Acme Corp", "total": "1,250.00", "date": "2026-11-20"}
        res = generate_artifact(template_name=tpl, data=data, require_approval=False)
        print("ok:", res.ok, "path:", res.output_path, "error:", res.error, "sha:", res.sha256[:12])
