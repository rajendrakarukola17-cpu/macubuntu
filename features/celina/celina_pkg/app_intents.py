"""
Celina AI - Native OS App Intents: GNOME Contacts (vCard/Evolution) & Browser Bookmarks (v4.3)
Gives Siri/Celina structured, read-only access to contacts and browser bookmarks with:
  - Zero fragile GUI clicking
  - Zero live browser SQLite lock contention (copies places.sqlite to a 0600 temp file & unlinks)
  - Zero exposure of cookies, saved logins, passwords, or browsing history
  - Pure Python stdlib (no extra pip dependencies)
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config

EVOLUTION_CONTACTS_DB = config.HOME / ".local" / "share" / "evolution" / "addressbook" / "system" / "contacts.db"
CELINA_VCARDS_DIR = config.BASE_DIR / "contacts"


@dataclass(frozen=True)
class ContactRecord:
    name: str
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    org: str = ""


@dataclass(frozen=True)
class BookmarkRecord:
    title: str
    url: str
    browser: str


def parse_vcards(raw_vcf: str) -> list[ContactRecord]:
    """Pure-Python RFC 6350 / 2426 vCard parser."""
    records: list[ContactRecord] = []
    unfolded: list[str] = []
    for line in raw_vcf.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if (line.startswith(" ") or line.startswith("\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)

    in_card = False
    fn = ""
    org = ""
    emails: list[str] = []
    phones: list[str] = []
    for line in unfolded:
        up = line.strip().upper()
        if up == "BEGIN:VCARD":
            in_card = True
            fn, org, emails, phones = "", "", [], []
            continue
        if up == "END:VCARD":
            if in_card and (fn or emails or phones):
                records.append(
                    ContactRecord(
                        name=fn or (emails[0] if emails else phones[0]),
                        emails=tuple(dict.fromkeys(emails)),
                        phones=tuple(dict.fromkeys(phones)),
                        org=org,
                    )
                )
            in_card = False
            continue
        if not in_card or ":" not in line:
            continue
        key_part, val_part = line.split(":", 1)
        prop = key_part.split(";", 1)[0].strip().upper()
        val = val_part.strip()
        if not val:
            continue
        if prop == "FN":
            fn = val
        elif prop == "ORG":
            org = val.replace(";", " ").strip()
        elif prop == "EMAIL":
            emails.append(val)
        elif prop == "TEL":
            phones.append(val)
    return records


def _snapshot_sqlite_wal_safe(db_path: Path, prefix: str, suffix: str) -> tuple[Path, Path]:
    """
    Creates an atomic, WAL-consistent read-only snapshot of a live SQLite database
    (e.g. Firefox `places.sqlite` or Evolution `contacts.db`).
    Prefers SQLite's online `backup()` API so active `-wal` frames are folded into
    the snapshot cleanly; falls back to copying main + `-wal` + `-shm` into a private
    0700 temp directory if read-only URI open encounters a lock or permission error.
    Returns `(tmp_dir, snapshot_db_path)`.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(tmp_dir, 0o700)
    tmp_db = tmp_dir / f"snapshot{suffix}"
    try:
        src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
        dst_conn = sqlite3.connect(str(tmp_db))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        os.chmod(tmp_db, 0o600)
        return tmp_dir, tmp_db
    except sqlite3.Error:
        # Fallback: copy main DB + companion -wal / -shm sidecars so SQLite replays WAL cleanly
        tmp_db.unlink(missing_ok=True)
        shutil.copy2(db_path, tmp_db)
        os.chmod(tmp_db, 0o600)
        for sidecar in ("-wal", "-shm"):
            sc_src = Path(f"{db_path}{sidecar}")
            if sc_src.exists():
                sc_dst = Path(f"{tmp_db}{sidecar}")
                shutil.copy2(sc_src, sc_dst)
                os.chmod(sc_dst, 0o600)
        return tmp_dir, tmp_db


def _load_evolution_contacts(db_path: Path) -> list[ContactRecord]:
    if not db_path.exists():
        return []
    tmp_dir: Optional[Path] = None
    try:
        tmp_dir, tmp_db = _snapshot_sqlite_wal_safe(db_path, prefix="celina_contacts_", suffix=".db")
        conn = sqlite3.connect(str(tmp_db), timeout=3.0)
        try:
            cur = conn.cursor()
            tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            records: list[ContactRecord] = []
            if "folder_id" in tables:
                for row in cur.execute("SELECT vcard FROM folder_id WHERE vcard IS NOT NULL LIMIT 500").fetchall():
                    if row and row[0]:
                        records.extend(parse_vcards(str(row[0])))
            return records
        finally:
            conn.close()
    except Exception:
        return []
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def search_contacts(
    query: str,
    *,
    contacts_db: Path = EVOLUTION_CONTACTS_DB,
    vcards_dir: Path = CELINA_VCARDS_DIR,
    limit: int = 10,
) -> str:
    """Searches GNOME Contacts (Evolution addressbook) & local .vcf vCards read-only."""
    q = (query or "").strip().lower()
    if not q:
        return "Provide a contact name, email, phone, or organization to search."

    all_contacts: list[ContactRecord] = []
    all_contacts.extend(_load_evolution_contacts(contacts_db))
    if vcards_dir.exists() and vcards_dir.is_dir():
        for vcf in sorted(vcards_dir.glob("*.vcf"))[:50]:
            try:
                all_contacts.extend(parse_vcards(vcf.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue

    matches = [
        c
        for c in all_contacts
        if q in c.name.lower()
        or q in c.org.lower()
        or any(q in e.lower() for e in c.emails)
        or any(q in p.lower() for p in c.phones)
    ][: max(1, min(25, int(limit)))]

    if not matches:
        return f"No contacts found matching '{query}'."

    lines = [f"👤 Contacts matching '{query}':"]
    for c in matches:
        bits = [c.name]
        if c.org:
            bits.append(f"({c.org})")
        if c.emails:
            bits.append("✉️ " + ", ".join(c.emails[:2]))
        if c.phones:
            bits.append("📞 " + ", ".join(c.phones[:2]))
        lines.append("  • " + " — ".join(bits))
    return "\n".join(lines)


def _walk_chromium_bookmark_node(node: Any, browser: str, out: list[BookmarkRecord]) -> None:
    if not isinstance(node, dict):
        return
    ntype = node.get("type")
    if ntype == "url":
        url = str(node.get("url") or "").strip()
        title = str(node.get("name") or url).strip()
        if url.startswith(("http://", "https://")):
            out.append(BookmarkRecord(title=title, url=url, browser=browser))
    for child in node.get("children") or []:
        _walk_chromium_bookmark_node(child, browser, out)


def _read_chromium_bookmarks(bookmarks_json: Path, browser: str) -> list[BookmarkRecord]:
    if not bookmarks_json.exists():
        return []
    try:
        data = json.loads(bookmarks_json.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    out: list[BookmarkRecord] = []
    roots = data.get("roots") if isinstance(data, dict) else None
    if isinstance(roots, dict):
        for root_node in roots.values():
            _walk_chromium_bookmark_node(root_node, browser, out)
    return out


def _read_firefox_bookmarks(places_sqlite: Path) -> list[BookmarkRecord]:
    if not places_sqlite.exists():
        return []
    tmp_dir: Optional[Path] = None
    try:
        tmp_dir, tmp_db = _snapshot_sqlite_wal_safe(places_sqlite, prefix="celina_ff_bm_", suffix=".sqlite")
        conn = sqlite3.connect(str(tmp_db), timeout=3.0)
        try:
            rows = conn.execute(
                """
                SELECT COALESCE(b.title, p.title, p.url), p.url
                FROM moz_bookmarks b
                JOIN moz_places p ON b.fk = p.id
                WHERE b.type = 1 AND (p.url LIKE 'http://%' OR p.url LIKE 'https://%')
                LIMIT 2000
                """
            ).fetchall()
            return [BookmarkRecord(title=str(r[0] or r[1]), url=str(r[1]), browser="Firefox") for r in rows]
        finally:
            conn.close()
    except Exception:
        return []
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _default_bookmark_sources(home: Path = config.HOME) -> tuple[list[tuple[str, Path]], list[Path]]:
    chromium_paths = [
        ("Chrome", home / ".config" / "google-chrome" / "Default" / "Bookmarks"),
        ("Chromium", home / ".config" / "chromium" / "Default" / "Bookmarks"),
        ("Brave", home / ".config" / "BraveSoftware" / "Brave-Browser" / "Default" / "Bookmarks"),
    ]
    firefox_dbs: list[Path] = []
    for base in (home / ".mozilla" / "firefox", home / "snap" / "firefox" / "common" / ".mozilla" / "firefox"):
        if base.exists():
            firefox_dbs.extend(sorted(base.glob("*/places.sqlite"))[:4])
    return chromium_paths, firefox_dbs


def search_browser_bookmarks(
    query: str,
    *,
    chromium_files: Optional[Iterable[tuple[str, Path]]] = None,
    firefox_dbs: Optional[Iterable[Path]] = None,
    limit: int = 12,
) -> str:
    """Read-only search across Firefox & Chromium/Chrome/Brave bookmarks (zero passwords/cookies/history)."""
    q = (query or "").strip().lower()
    if not q:
        return "Provide a keyword or domain to search browser bookmarks."

    if chromium_files is None or firefox_dbs is None:
        def_cr, def_ff = _default_bookmark_sources()
        chromium_files = def_cr if chromium_files is None else chromium_files
        firefox_dbs = def_ff if firefox_dbs is None else firefox_dbs

    all_bm: list[BookmarkRecord] = []
    for browser, path in chromium_files:
        all_bm.extend(_read_chromium_bookmarks(Path(path), browser))
    for db in firefox_dbs:
        all_bm.extend(_read_firefox_bookmarks(Path(db)))

    seen_urls: set[str] = set()
    hits: list[BookmarkRecord] = []
    for bm in all_bm:
        if bm.url in seen_urls:
            continue
        if q in bm.title.lower() or q in bm.url.lower():
            seen_urls.add(bm.url)
            hits.append(bm)
            if len(hits) >= max(1, min(25, int(limit))):
                break

    if not hits:
        return f"No browser bookmarks found matching '{query}'."

    lines = [f"🔖 Bookmarks matching '{query}':"]
    for h in hits:
        lines.append(f"  • [{h.browser}] {h.title[:80]} — {h.url}")
    return "\n".join(lines)


# ============================================================================
# Compound Cross-App "App Intent" Pipeline (v4.4 — Zero-LLM-Token Meeting Brief)
# ============================================================================

_MEETING_STOPWORDS = frozenset({
    "meeting", "sync", "call", "review", "chat", "catchup", "weekly", "daily",
    "standup", "session", "with", "about", "for", "the", "and", "next", "today",
    "tomorrow", "team", "project", "update", "prep", "prepare", "notes", "agenda",
    "zoom", "meet", "google", "teams", "online", "room", "office",
})
_DOC_EXTENSIONS = frozenset({".docx", ".pdf", ".md", ".txt", ".odt", ".pptx", ".xlsx"})


def _extract_meeting_keywords(text: str) -> list[str]:
    import re as _re

    tokens = _re.findall(r"[A-Za-z0-9\u0900-\u097f\u0c00-\u0c7f][A-Za-z0-9._@+\-\u0900-\u097f\u0c00-\u0c7f]{2,}", text or "")
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        low = tok.lower().strip(".,:;()[]")
        if len(low) < 3 or low in _MEETING_STOPWORDS or low in seen:
            continue
        seen.add(low)
        out.append(tok.strip(".,:;()[]"))
        if len(out) >= 12:
            break
    return out


def _find_matching_local_docs(keywords: list[str], search_dirs: Iterable[Path], limit: int = 5) -> list[Path]:
    if not keywords:
        return []
    low_kws = [k.lower() for k in keywords if len(k) >= 3]
    matches: list[tuple[int, float, Path]] = []
    scanned = 0
    skip_dirs = {"node_modules", "__pycache__", ".venv", "venv", ".git", ".cache", "Trash"}
    for root in search_dirs:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune hidden/heavy folders while supporting deep folders up to depth 8
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in skip_dirs]
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth > 8:
                dirnames[:] = []
                continue
            for fn in filenames:
                scanned += 1
                if scanned > 6000:
                    break
                p = Path(dirpath) / fn
                fn_low = fn.lower()
                score = sum(1 for kw in low_kws if kw in fn_low)
                if score > 0:
                    # Slight boost for known office/doc files while still finding ALL file extensions
                    if p.suffix.lower() in _DOC_EXTENSIONS:
                        score += 1
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    matches.append((score, mtime, p))
            if scanned > 6000:
                break
    matches.sort(key=lambda item: (-item[0], -item[1]))
    return [p for _sc, _mt, p in matches[:limit]]


def prepare_meeting_context(
    query: str = "",
    days: int = 7,
    *,
    ics_path: Optional[Path] = None,
    evolution_db: Path = EVOLUTION_CONTACTS_DB,
    vcard_dir: Path = CELINA_VCARDS_DIR,
    search_dirs: Optional[Iterable[Path]] = None,
    db: Optional[Any] = None,
    now: Optional[Any] = None,
) -> str:
    """
    Zero-LLM-token Compound App Intent Pipeline (<35ms):
      1. Finds the next upcoming (or query-matching) GNOME Calendar event.
      2. Extracts attendee names/emails/topics from SUMMARY + DESCRIPTION and looks up matching Contacts.
      3. Recalls relevant Ebbinghaus-ranked Memory facts, episodes & Knowledge Graph triples.
      4. Finds matching local notes/docs (.docx, .pdf, .md, .txt, .odt) in ~/Documents, ~/Desktop, ~/Downloads.
    Returns a unified 1-call executive brief without spending multiple LLM agent turns!
    """
    from . import calendar_bridge, memory

    upcoming = calendar_bridge.load_upcoming_events(days=days, ics_path=ics_path, now=now)
    q_low = (query or "").strip().lower()
    target_ev = None
    if upcoming:
        if q_low:
            for ev in upcoming:
                blob = f"{ev.summary} {ev.description} {ev.location}".lower()
                if q_low in blob or any(tok in blob for tok in _extract_meeting_keywords(q_low)):
                    target_ev = ev
                    break
        if target_ev is None:
            target_ev = upcoming[0]

    search_seed = " ".join(
        part for part in [
            query,
            target_ev.summary if target_ev else "",
            target_ev.description if target_ev else "",
            target_ev.location if target_ev else "",
        ]
        if part
    )
    keywords = _extract_meeting_keywords(search_seed)

    # 1. Contacts matching attendees/keywords
    all_contacts: list[ContactRecord] = []
    all_contacts.extend(_load_evolution_contacts(evolution_db))
    if vcard_dir.exists():
        for vf in sorted(vcard_dir.glob("*.vcf"))[:50]:
            try:
                all_contacts.extend(parse_vcards(vf.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue

    matched_contacts: list[ContactRecord] = []
    seen_c: set[str] = set()
    for kw in keywords:
        kl = kw.lower()
        for c in all_contacts:
            hay = " ".join([c.name, c.org, *c.emails, *c.phones]).lower()
            if kl in hay and c.name.lower() not in seen_c:
                seen_c.add(c.name.lower())
                matched_contacts.append(c)
                if len(matched_contacts) >= 5:
                    break
        if len(matched_contacts) >= 5:
            break

    # 2. Memory facts, KG triples & past episodes
    mem_db = db if db is not None else memory.get_db()
    mem_query = " ".join(keywords) or query or (target_ev.summary if target_ev else "")
    recalled_facts: list[tuple[str, str]] = []
    recalled_eps: list[str] = []
    kg_hits: list[str] = []
    if mem_query.strip():
        try:
            recalled_facts, recalled_eps = mem_db.recall_relevant(
                mem_query, max_facts=4, max_episodes=2, cloud_only=False
            )
            kg_hits = mem_db.recall_knowledge_triples(mem_query, limit=4, cloud_only=False)
        except Exception:
            pass

    # 3. Matching local documents
    doc_dirs = list(search_dirs) if search_dirs is not None else [
        config.HOME / "Documents",
        config.HOME / "Desktop",
        config.HOME / "Downloads",
    ]
    matched_docs = _find_matching_local_docs(keywords, doc_dirs, limit=5)

    # 4. Assemble unified executive brief
    lines: list[str] = ["📋 Executive Meeting Brief (Compound App Intent):"]
    if target_ev is not None:
        when = target_ev.start.strftime("%a %b %d, %H:%M")
        loc = f" @ {target_ev.location}" if target_ev.location else ""
        lines.append(f"• Next Event: {target_ev.summary} ({when}{loc})")
        if target_ev.description:
            lines.append(f"  Agenda/Notes: {target_ev.description[:220]}")
    elif query:
        lines.append(f"• Topic Focus: {query} (no matching upcoming calendar event in next {days}d)")
    else:
        lines.append(f"• Calendar: No upcoming events found in the next {days} day(s).")

    if matched_contacts:
        lines.append("• Matching Contacts:")
        for c in matched_contacts:
            bits = [c.name]
            if c.org:
                bits.append(f"({c.org})")
            if c.emails:
                bits.append(f"✉ {c.emails[0]}")
            if c.phones:
                bits.append(f"📞 {c.phones[0]}")
            lines.append("  - " + " ".join(bits))
    else:
        lines.append("• Matching Contacts: none matched")

    if recalled_facts or kg_hits or recalled_eps:
        lines.append("• Relevant Memory & Knowledge Graph:")
        for k, v in recalled_facts[:4]:
            lines.append(f"  - [fact] {k}: {v}")
        for kg in kg_hits[:4]:
            lines.append(f"  - [graph] {kg}")
        for ep in recalled_eps[:2]:
            lines.append(f"  - [episode] {ep[:160]}")

    if matched_docs:
        lines.append("• Related Local Files:")
        for p in matched_docs:
            lines.append(f"  - {p}")
    else:
        lines.append("• Related Local Files: none matched")

    return "\n".join(lines)
