"""Celina AI — Offline Knowledge Seeder (v0.9.1-seed-knowledge)

Deterministic offline seed pipeline for Celina's long-term memory layer.
Zero external dependencies (stdlib only: sqlite3, re, json, csv, pathlib,
datetime, hashlib). Strict zero-trust: secrets are never written to memory.db,
and all non-user-direct entries carry source_trust="local_file".

File allowlist (only these files may be touched):
  features/celina/celina_pkg/seed_knowledge.py   (new)
  features/celina/celina_pkg/memory.py            (helper hooks only)
  features/celina/celina_pkg/router.py            (/seed slash command only)
  tests/test_seed_knowledge.py                    (new)
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import memory, privacy

# ---------------------------------------------------------------------------
# Task 1 — Canonical Starter Ontology
# ---------------------------------------------------------------------------

STARTER_NODE_TYPES: frozenset[str] = frozenset({
    "Person",
    "Project",
    "Task",
    "Device",
    "Preference",
    "Location",
    "Event",
})

STARTER_PREDICATES: frozenset[str] = frozenset({
    "WORKS_ON",
    "PREFERS",
    "DEADLINE_FOR",
    "LOCATED_IN",
    "DEPENDS_ON",
})

# Controlled alias map: common natural-language variants -> canonical predicate.
# Unknown predicates are kept verbatim but tagged with care (source_trust
# remains "local_file" for corpus, "user_direct" only for SOUL/USER.md).
_PREDICATE_ALIASES: dict[str, str] = {
    "USES": "DEPENDS_ON",
    "BUILT_WITH": "DEPENDS_ON",
    "LIVES_IN": "LOCATED_IN",
    "BASED_IN": "LOCATED_IN",
    "LIKES": "PREFERS",
    "WANTS": "PREFERS",
    "OWNS": "WORKS_ON",
    "MAINTAINS": "WORKS_ON",
    "DUE_ON": "DEADLINE_FOR",
}

# Hard path denylist — never read or index these locations.
_SENSITIVE_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r"\.ssh[/\\]"),
    re.compile(r"\.gnupg[/\\]"),
    re.compile(r"\.aws[/\\]"),
    re.compile(r"\.kube[/\\]"),
    re.compile(r"\.netrc[/\\]"),
    re.compile(r"\.git-credentials[/\\]"),
    re.compile(r"keyrings[/\\]"),
    re.compile(r"[Ll]ogin Data[/\\]"),
    re.compile(r"cookies\.sqlite[/\\]"),
    re.compile(r"\.env[/\\]"),
    re.compile(r"\.env\."),
    re.compile(r"\.pem[/\\]"),
    re.compile(r"\.key[/\\]"),
    re.compile(r"id_rsa[/\\]"),
    re.compile(r"id_rsa\."),
    re.compile(r"\.git[/\\]"),
]

# Hard content denylist — never write raw secrets to memory.db.
# Matches sk-..., ghp_..., AKIA..., password=..., secret_key=..., api_key=...
_SECRET_CONTENT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:password|passwd|secret[_-]?key|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\",;]{6,}['\"]?"),
]


def normalize_node_type(raw: str) -> str:
    """Normalize a node type string to TitleCase. Fallback 'Entity' if unknown."""
    if not raw or not raw.strip():
        return "Entity"
    cleaned = " ".join(w.capitalize() for w in raw.strip().split())
    if cleaned in STARTER_NODE_TYPES:
        return cleaned
    # Also accept exact lowercase matches
    for nt in STARTER_NODE_TYPES:
        if cleaned.lower() == nt.lower():
            return nt
    return "Entity"


def normalize_predicate(raw: str) -> str:
    """Normalize a predicate string to UPPER_UNDERSCORE canonical form.

    Applies controlled alias mapping (e.g. USES -> DEPENDS_ON). Unknown
    predicates are returned as-is (uppercased, underscored) so callers can
    tag them carefully without mutating the kg_triples schema.
    """
    if not raw or not raw.strip():
        return ""
    norm = re.sub(r"[^A-Za-z0-9]+", "_", raw.strip()).upper().strip("_")
    if norm in STARTER_PREDICATES:
        return norm
    return _PREDICATE_ALIASES.get(norm, norm)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _is_sensitive_path(file_path: Path) -> bool:
    """Return True if the file path matches any sensitive-path pattern."""
    rel = str(file_path)
    # Also match bare sensitive filenames (e.g. id_rsa, .env) without trailing separator
    name = file_path.name
    sensitive_names = {
        "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
        ".env", ".netrc", ".git-credentials",
    }
    if name in sensitive_names:
        return True
    for pat in _SENSITIVE_PATH_PATTERNS:
        if pat.search(rel):
            return True
    return False


def _strip_content_secrets(text: str) -> tuple[str, list[str]]:
    """Strip hard secrets from text using RedactionVault + extra content patterns.

    Returns (cleaned_text, list_of_secret_labels_found).
    """
    vault = privacy.RedactionVault()
    cleaned, found = vault.strip_hard_secrets(text)
    # Also run our own content patterns for additional coverage
    for pat in _SECRET_CONTENT_PATTERNS:
        if pat.search(cleaned):
            found.append("SECRET_CONTENT")
            cleaned = pat.sub("[REDACTED_SECRET]", cleaned)
    return cleaned, found


# ---------------------------------------------------------------------------
# Task 2 — Seed Pack 1: Identity & Persona (SOUL.md & USER.md)
# ---------------------------------------------------------------------------

def _parse_markdown_sections(text: str) -> dict[str, list[str]]:
    """Parse a Markdown file into {heading: [lines]} sections.

    Handles ## Heading and # Heading style. Lines under each heading are
    collected until the next heading. Returns a dict mapping normalized
    heading text (lowercased) to a list of non-empty bullet/paragraph lines.
    """
    sections: dict[str, list[str]] = {}
    current_heading = "general"
    sections[current_heading] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Match ## Heading or # Heading
        m = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if m:
            current_heading = m.group(1).strip().lower()
            sections.setdefault(current_heading, [])
            continue
        # Treat non-heading lines as content under current heading
        sections.setdefault(current_heading, []).append(stripped)
    return sections


def seed_soul_md(db: memory.MemoryDB, soul_path: Path, *,
                 source_ref: str = "SOUL.md") -> dict[str, int]:
    """Parse SOUL.md and persist voice/style/brevity directives as preferences.

    Returns {"preferences": N, "errors": 0}.
    """
    prefs = 0
    errors = 0
    try:
        text = soul_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"preferences": 0, "errors": 1}

    sections = _parse_markdown_sections(text)
    # Collect directive-style lines from voice/style/brevity/behavior sections
    directive_keys: set[str] = set()
    for heading, lines in sections.items():
        is_directive = any(k in heading for k in (
            "voice", "style", "brevity", "tone", "behavior", "preference",
            "directive", "guideline", "instruction",
        ))
        for line in lines:
            clean = line.strip(" -•*").strip()
            if not clean or clean in directive_keys:
                continue
            key = f"soul_{heading}:{clean[:60]}"
            if key in directive_keys:
                continue
            directive_keys.add(key)
            try:
                db.set_fact(
                    key=key,
                    value=clean,
                    source_trust="user_direct",
                    source_ref=source_ref,
                    category="preference",
                )
                prefs += 1
            except Exception:
                errors += 1
    return {"preferences": prefs, "errors": errors}


def seed_user_md(db: memory.MemoryDB, user_path: Path, *,
                 source_ref: str = "USER.md") -> dict[str, int]:
    """Parse USER.md and persist profile facts, hardware/location/schedule,
    and hard boundaries.

    Stores:
      - Explicit boundaries/preferences in facts & kg_triples with
        source_trust="user_direct" and source_ref="USER.md".
      - Never stores sensitive government IDs or raw secrets.

    Returns {"facts": N, "triples": N, "errors": 0}.
    """
    facts = 0
    triples = 0
    errors = 0
    try:
        text = user_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"facts": 0, "triples": 0, "errors": 1}

    # First, strip any hard secrets from the raw text so they never reach DB
    clean_text, found_secrets = _strip_content_secrets(text)

    sections = _parse_markdown_sections(clean_text)
    for heading, lines in sections.items():
        for line in lines:
            clean = line.strip(" -•*").strip()
            if not clean:
                continue

            # Boundary / hard constraint lines
            is_boundary = any(k in clean.lower() for k in (
                "never", "must not", "do not", "do-not", "boundary", "hard limit",
                "without asking", "prohibited", "forbidden",
            ))

            if is_boundary:
                key = f"user_boundary:{hashlib.sha256(clean.encode()).hexdigest()[:12]}"
                try:
                    db.set_fact(
                        key=key,
                        value=clean,
                        source_trust="user_direct",
                        source_ref=source_ref,
                        category="preference",
                    )
                    facts += 1
                except Exception:
                    errors += 1
                continue

            # General profile fact: store as a fact with the heading as context
            key = f"user_{heading}:{clean[:80]}"
            try:
                db.set_fact(
                    key=key,
                    value=clean,
                    source_trust="user_direct",
                    source_ref=source_ref,
                    category="fact",
                )
                facts += 1
            except Exception:
                errors += 1

            # Try to extract a (subject, predicate, object) triple from lines
            # like "Alice works on Project X" or "Lives in Bangalore"
            triple = _extract_triple_from_line(clean)
            if triple:
                subj, pred, obj = triple
                canon_pred = normalize_predicate(pred)
                try:
                    db.add_knowledge_triple(
                        subject=subj,
                        predicate=canon_pred,
                        obj=obj,
                        source_trust="user_direct",
                        source_ref=source_ref,
                        category="context",
                    )
                    triples += 1
                except Exception:
                    errors += 1

    return {"facts": facts, "triples": triples, "errors": errors}


def _extract_triple_from_line(line: str) -> tuple[str, str, str] | None:
    """Best-effort extraction of (subject, predicate, object) from a line.

    Handles patterns like:
      - "Alice works on Project X"
      - "Lives in Bangalore"
      - "Deadline for Project X is Friday"
      - "Prefers dark mode"
    """
    low = line.lower().strip()
    # Predicate phrase -> canonical mapping
    patterns = [
        (r"^(.+?)\s+(?:works on|owns|maintains)\s+(.+)$", "WORKS_ON"),
        (r"^(.+?)\s+(?:lives in|based in|located in)\s+(.+)$", "LOCATED_IN"),
        (r"^(.+?)\s+(?:prefers|likes|wants)\s+(.+)$", "PREFERS"),
        (r"^(.+?)\s+(?:deadline for|due on)\s+(.+)$", "DEADLINE_FOR"),
        (r"^(.+?)\s+(?:depends on|uses|built with)\s+(.+)$", "DEPENDS_ON"),
        (r"^(.+?)\s+(?:is located in)\s+(.+)$", "LOCATED_IN"),
    ]
    for pat, default_pred in patterns:
        m = re.match(pat, low)
        if m:
            subj = m.group(1).strip().title()
            obj = m.group(2).strip().title()
            if subj and obj:
                return (subj, default_pred, obj)
    return None


# ---------------------------------------------------------------------------
# Task 3 — Seed Pack 2 & 4: Personal Corpus & Optional Offline Reference Packs
# ---------------------------------------------------------------------------

_SUPPORTED_TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".markdown", ".txt", ".rst",
})
_SUPPORTED_REF_EXTENSIONS: frozenset[str] = frozenset({
    ".json", ".vcf", ".csv",
})


def _should_skip_file(file_path: Path) -> bool:
    """Return True if the file should be skipped (sensitive path or extension)."""
    if _is_sensitive_path(file_path):
        return True
    # Skip hidden files/dirs (except explicitly allowed)
    parts = file_path.parts
    for part in parts:
        if part.startswith(".") and part not in {".", ".."}:
            # Allow seed dir itself if it's hidden? No—skip hidden components
            return True
    return False


def _read_text_file(file_path: Path, max_bytes: int) -> tuple[str, bool]:
    """Read a text file up to max_bytes. Returns (text, was_truncated)."""
    try:
        data = file_path.read_bytes()[:max_bytes]
        truncated = file_path.stat().st_size > max_bytes
        return data.decode("utf-8", errors="replace"), truncated
    except OSError:
        return "", False


def _parse_json_ref(file_path: Path) -> list[tuple[str, str, str]]:
    """Extract (name, role/org, topic) triples from a JSON reference file.

    Skips raw credential fields (password, secret, key, token).
    """
    triples: list[tuple[str, str, str]] = []
    try:
        data = json.loads(file_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return triples

    def _walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            # Skip if this looks like a credential object
            keys = {str(k).lower() for k in obj.keys()}
            if keys & {"password", "passwd", "secret", "secret_key", "api_key", "access_token", "token"}:
                return
            name = obj.get("name") or obj.get("title") or obj.get("id") or path
            role = obj.get("role") or obj.get("org") or obj.get("organization") or ""
            topic = obj.get("topic") or obj.get("description") or obj.get("desc") or ""
            if name:
                triples.append((str(name), str(role), str(topic)))
            for k, v in obj.items():
                _walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{path}[{i}]")

    _walk(data)
    return triples


def _parse_vcf_ref(file_path: Path) -> list[tuple[str, str, str]]:
    """Extract (name, role/org, topic) triples from a VCF contact file."""
    triples: list[tuple[str, str, str]] = []
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return triples

    current_name = ""
    current_org = ""
    current_role = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("BEGIN:VCARD"):
            current_name = ""
            current_org = ""
            current_role = ""
            continue
        if stripped.startswith("END:VCARD"):
            if current_name:
                triples.append((current_name, current_org, current_role))
            continue
        if stripped.startswith("FN:"):
            current_name = stripped[3:].strip()
        elif stripped.startswith("ORG:"):
            current_org = stripped[4:].strip()
        elif stripped.startswith("TITLE:"):
            current_role = stripped[6:].strip()
    if current_name:
        triples.append((current_name, current_org, current_role))
    return triples


def _parse_csv_ref(file_path: Path) -> list[tuple[str, str, str]]:
    """Extract (name, role/org, topic) triples from a CSV reference file."""
    triples: list[tuple[str, str, str]] = []
    try:
        with file_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # Skip rows with credential fields
                if any(k and any(s in k.lower() for s in (
                    "password", "secret", "key", "token",
                )) for k in row.keys()):
                    continue
                name = (row.get("name") or row.get("Name") or row.get("title") or "").strip()
                role = (row.get("role") or row.get("Role") or row.get("org") or "").strip()
                topic = (row.get("topic") or row.get("Topic") or row.get("description") or "").strip()
                if name:
                    triples.append((name, role, topic))
    except (OSError, csv.Error):
        pass
    return triples


def seed_corpus(
    corpus_dir: Path,
    *,
    max_files: int = 200,
    max_bytes_per_file: int = 262_144,
    include_reference: bool = False,
    reference_dir: Path | None = None,
    dry_run: bool = False,
    db: memory.MemoryDB | None = None,
) -> dict[str, Any]:
    """Seed an offline personal corpus (and optional reference packs) into memory.

    All entries are persisted with source_trust="local_file" and
    source_ref="<relative-or-filename>".

    Returns a summary dict:
      files_scanned, facts_seeded, triples_seeded,
      skipped_sensitive, errors, skipped_binary, reference_packs
    """
    # Opening the default database can create files and run migrations; a
    # simulation must not obtain it merely to scan the corpus.
    if db is None and not dry_run:
        db = memory.get_db()

    summary: dict[str, Any] = {
        "files_scanned": 0,
        "facts_seeded": 0,
        "triples_seeded": 0,
        "skipped_sensitive": 0,
        "errors": 0,
        "skipped_binary": 0,
        "reference_packs": 0,
    }

    vault = privacy.RedactionVault()
    seen_keys: set[str] = set()

    def _seed_snippet(label: str, text: str, source_ref: str) -> None:
        """Seed a cleaned snippet as a fact + optional triple."""
        if not text or not text.strip():
            return
        clean_text, found = _strip_content_secrets(text)
        if not clean_text or not clean_text.strip():
            return
        # Truncate to reasonable size for storage
        snippet = clean_text.strip()[:500]
        key = f"corpus_{hashlib.sha256((source_ref + snippet).encode()).hexdigest()[:16]}"
        if key in seen_keys:
            return  # idempotent: skip duplicates
        seen_keys.add(key)
        if not dry_run:
            assert db is not None
            try:
                db.set_fact(
                    key=key,
                    value=snippet,
                    source_trust="local_file",
                    source_ref=source_ref,
                    category="context",
                )
                summary["facts_seeded"] += 1
            except Exception:
                summary["errors"] += 1

    def _process_text_file(file_path: Path, rel_name: str) -> None:
        text, truncated = _read_text_file(file_path, max_bytes_per_file)
        if not text.strip():
            return
        # Seed title-like first line + body snippets
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines:
            _seed_snippet(f"title_{rel_name}", lines[0], rel_name)
        # Seed body in chunks
        chunk = "\n".join(lines[1:6])  # up to 5 body lines
        if chunk:
            _seed_snippet(f"body_{rel_name}", chunk, rel_name)

    # --- Scan primary corpus directory ---
    if corpus_dir and corpus_dir.is_dir():
        count = 0
        for file_path in sorted(corpus_dir.rglob("*")):
            if count >= max_files:
                break
            if not file_path.is_file():
                continue
            if _should_skip_file(file_path):
                summary["skipped_sensitive"] += 1
                continue
            ext = file_path.suffix.lower()
            if ext in _SUPPORTED_TEXT_EXTENSIONS:
                rel_name = str(file_path.relative_to(corpus_dir))
                _process_text_file(file_path, rel_name)
                count += 1
                summary["files_scanned"] += 1
            elif ext == ".pdf" or ext == ".docx":
                # Record skip without crashing; stdlib cannot parse these natively
                summary["skipped_binary"] += 1
                count += 1
                summary["files_scanned"] += 1
            else:
                summary["skipped_binary"] += 1

    # --- Scan optional reference directory ---
    ref_dir = reference_dir if (include_reference and reference_dir) else None
    if ref_dir and ref_dir.is_dir():
        for file_path in sorted(ref_dir.rglob("*")):
            if not file_path.is_file():
                continue
            if _should_skip_file(file_path):
                summary["skipped_sensitive"] += 1
                continue
            ext = file_path.suffix.lower()
            rel_name = str(file_path.relative_to(ref_dir))
            if ext == ".json":
                triples = _parse_json_ref(file_path)
                for name, role, topic in triples:
                    if not dry_run:
                        assert db is not None
                        try:
                            db.add_knowledge_triple(
                                subject=name,
                                predicate="WORKS_ON" if role else "DEPENDS_ON",
                                obj=topic or role or "reference",
                                source_trust="local_file",
                                source_ref=rel_name,
                                category="context",
                            )
                            summary["triples_seeded"] += 1
                        except Exception:
                            summary["errors"] += 1
                summary["reference_packs"] += 1
            elif ext == ".vcf":
                triples = _parse_vcf_ref(file_path)
                for name, role, topic in triples:
                    if not dry_run:
                        assert db is not None
                        try:
                            db.add_knowledge_triple(
                                subject=name,
                                predicate="LOCATED_IN" if role else "DEPENDS_ON",
                                obj=topic or role or "contact",
                                source_trust="local_file",
                                source_ref=rel_name,
                                category="context",
                            )
                            summary["triples_seeded"] += 1
                        except Exception:
                            summary["errors"] += 1
                summary["reference_packs"] += 1
            elif ext == ".csv":
                triples = _parse_csv_ref(file_path)
                for name, role, topic in triples:
                    if not dry_run:
                        assert db is not None
                        try:
                            db.add_knowledge_triple(
                                subject=name,
                                predicate="WORKS_ON" if role else "DEPENDS_ON",
                                obj=topic or role or "reference",
                                source_trust="local_file",
                                source_ref=rel_name,
                                category="context",
                            )
                            summary["triples_seeded"] += 1
                        except Exception:
                            summary["errors"] += 1
                summary["reference_packs"] += 1
            # Non-reference text file: treat as snippet
            elif ext in _SUPPORTED_TEXT_EXTENSIONS:
                _process_text_file(file_path, rel_name)
                summary["files_scanned"] += 1

    return summary


# ---------------------------------------------------------------------------
# Task 4 — Bundle orchestrator
# ---------------------------------------------------------------------------

def run_seed_bundle(
    seed_dir: Path,
    *,
    include_reference: bool = False,
    reference_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the full offline seed bundle from a seed directory.

    Expected layout (all optional):
      <seed_dir>/
        SOUL.md          -> preferences (source_trust=user_direct)
        USER.md          -> facts + triples (source_trust=user_direct)
        corpus/          -> personal corpus (source_trust=local_file)
        reference/       -> optional JSON/VCF/CSV reference packs (local_file)

    Returns a combined summary dict.
    """
    result: dict[str, Any] = {
        "soul": {"preferences": 0, "errors": 0},
        "user": {"facts": 0, "triples": 0, "errors": 0},
        "corpus": {
            "files_scanned": 0, "facts_seeded": 0, "triples_seeded": 0,
            "skipped_sensitive": 0, "errors": 0, "skipped_binary": 0,
            "reference_packs": 0,
        },
        "dry_run": dry_run,
        "total_writes": 0,
        "errors": 0,
    }

    # Opening the DB can create files and run migrations; simulations scan
    # without acquiring a handle. Live bundle callers retain one shared DB.
    bundle_db = None if dry_run else memory.get_db()

    soul_path = seed_dir / "SOUL.md"
    if soul_path.is_file():
        if not dry_run:
            assert bundle_db is not None
            result["soul"] = seed_soul_md(db=bundle_db, soul_path=soul_path)
        else:
            result["soul"] = {"preferences": 0, "errors": 0, "dry_run": True}

    user_path = seed_dir / "USER.md"
    if user_path.is_file():
        if not dry_run:
            assert bundle_db is not None
            result["user"] = seed_user_md(db=bundle_db, user_path=user_path)
        else:
            result["user"] = {"facts": 0, "triples": 0, "errors": 0, "dry_run": True}

    corpus_dir = seed_dir / "corpus"
    if corpus_dir.is_dir():
        result["corpus"] = seed_corpus(
            corpus_dir=corpus_dir,
            include_reference=include_reference,
            reference_dir=reference_dir,
            dry_run=dry_run,
            db=bundle_db,
        )

    ref_dir = seed_dir / "reference"
    if ref_dir.is_dir() and include_reference:
        ref_summary = seed_corpus(
            corpus_dir=ref_dir,
            include_reference=True,
            reference_dir=None,
            dry_run=dry_run,
            db=bundle_db,
        )
        result["corpus"]["reference_packs"] += ref_summary.get("reference_packs", 0)
        result["corpus"]["triples_seeded"] += ref_summary.get("triples_seeded", 0)
        result["corpus"]["files_scanned"] += ref_summary.get("files_scanned", 0)
        result["corpus"]["skipped_sensitive"] += ref_summary.get("skipped_sensitive", 0)
        result["corpus"]["errors"] += ref_summary.get("errors", 0)
        result["corpus"]["skipped_binary"] += ref_summary.get("skipped_binary", 0)

    result["total_writes"] = (
        result["soul"].get("preferences", 0)
        + result["user"].get("facts", 0)
        + result["user"].get("triples", 0)
        + result["corpus"].get("facts_seeded", 0)
        + result["corpus"].get("triples_seeded", 0)
    ) if not dry_run else 0

    result["errors"] = (
        result["soul"].get("errors", 0)
        + result["user"].get("errors", 0)
        + result["corpus"].get("errors", 0)
    )

    return result
