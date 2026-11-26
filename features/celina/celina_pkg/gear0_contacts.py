"""Celina AI (Celly) - Gear 0 Contacts & Communication (Update 19)

Siri-style personal context — local address book, communication routing.
Zero LLM tokens, sub-5ms contact lookup, 100% local.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_CONTACTS_DB_PATH = Path.home() / ".celina" / "storage" / "shared" / "contacts.db"


def _get_conn() -> sqlite3.Connection:
    _CONTACTS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_CONTACTS_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS contacts (
        name TEXT PRIMARY KEY COLLATE NOCASE,
        phone TEXT DEFAULT '',
        email TEXT DEFAULT '',
        address TEXT DEFAULT '',
        kind TEXT DEFAULT 'contact',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)
    conn.commit()
    return conn


_CALL_RE = re.compile(
    r"^(?:please\s+)?call\s+(?P<alias>.+)$", re.IGNORECASE,
)
_TEXT_RE = re.compile(
    r"^(?:please\s+)?(?:text|message)\s+(?P<alias>.+?)\s+(?:saying\s+)?(?P<msg>.+)$", re.IGNORECASE,
)
_EMAIL_RE = re.compile(
    r"^(?:please\s+)?(?:email|mail)\s+(?P<alias>.+?)\s+(?:about\s+)?(?P<subject>.+)?\??$", re.IGNORECASE,
)
_SAVE_CONTACT_RE = re.compile(
    r"^(?:please\s+)?save\s+contact\s+(?P<name>.+?)\s+(?:phone\s+)?(?P<phone>.+?)(?:\s+email\s+(?P<email>.+))?$", re.IGNORECASE,
)


def contact_save(name: str, phone: str = "", email: str = "", address: str = "") -> str:
    """Save a contact to the local address book."""
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO contacts (name, phone, email, address)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                phone = excluded.phone,
                email = excluded.email,
                address = excluded.address
            """,
            (name.strip(), phone, email, address),
        )
        conn.commit()
        conn.close()
        return f"Saved contact: {name} ({phone})"
    except Exception as exc:
        return f"Failed to save contact: {exc}"


def contact_lookup(name: str) -> dict[str, str] | None:
    """Look up a contact by name."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT phone, email, address FROM contacts WHERE name = ?",
            (name.strip(),),
        ).fetchone()
        conn.close()
        if row:
            return {"phone": row[0] or "", "email": row[1] or "", "address": row[2] or ""}
        return None
    except Exception:
        return None


def contact_list() -> list[dict[str, str]]:
    """List all contacts."""
    try:
        conn = _get_conn()
        rows = conn.execute("SELECT name, phone, email FROM contacts ORDER BY name").fetchall()
        conn.close()
        return [{"name": r[0], "phone": r[1], "email": r[2]} for r in rows]
    except Exception:
        return []


def handle_call(alias: str, dry_run: bool = False) -> str:
    """Handle 'Call Mom' — resolve contact, return tel: URL."""
    entry = contact_lookup(alias)
    if not entry:
        return f"I don't have a contact named '{alias}'. Say 'save contact {alias} as <phone>' to add one."
    phone = entry["phone"]
    if not phone:
        return f"'{alias}' has no phone number saved."
    if dry_run:
        return f"🧪 Dry-Run [Gear 0-Contacts]: Would call {alias} at {phone} (tel:{phone})."
    try:
        import subprocess
        subprocess.Popen(["xdg-open", f"tel:{phone}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"📞 Calling {alias} at {phone}."
    except Exception:
        return f"📞 {alias}: {phone} (tel:{phone}) — click to call."


def handle_text(alias: str, message: str, dry_run: bool = False) -> str:
    """Handle 'Text Sarah saying hello' — resolve contact, pre-fill SMS."""
    entry = contact_lookup(alias)
    if not entry:
        return f"I don't have a contact named '{alias}'."
    phone = entry["phone"]
    if not phone:
        return f"'{alias}' has no phone number saved."
    if dry_run:
        return f"🧪 Dry-Run [Gear 0-Contacts]: Would text {alias} ({phone}): {message}."
    try:
        import subprocess
        import urllib.parse
        encoded = urllib.parse.quote(message)
        subprocess.Popen(["xdg-open", f"sms:{phone}?body={encoded}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"💬 Texting {alias} ({phone}): {message}."
    except Exception:
        return f"💬 {alias} ({phone}): {message} — ready to send."


def handle_email(alias: str, subject: str = "", dry_run: bool = False) -> str:
    """Handle 'Email Sarah about meeting' — resolve contact, pre-fill email."""
    entry = contact_lookup(alias)
    if not entry:
        return f"I don't have a contact named '{alias}'."
    email_addr = entry["email"]
    if not email_addr:
        return f"'{alias}' has no email address saved."
    if dry_run:
        return f"🧪 Dry-Run [Gear 0-Contacts]: Would email {alias} ({email_addr}) about {subject or 'no subject'}."
    try:
        import subprocess
        import urllib.parse
        subject_encoded = urllib.parse.quote(subject or "No Subject")
        subprocess.Popen(["xdg-open", f"mailto:{email_addr}?subject={subject_encoded}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"✉️ Emailing {alias} ({email_addr})."
    except Exception:
        return f"✉️ {alias}: {email_addr} — ready to email."


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

def gear0_contacts_classify(query: str) -> str | None:
    """Classify query into a contact handler name, or None."""
    low = query.strip().lower()

    if _CALL_RE.match(low):
        return "call"
    if _TEXT_RE.match(low):
        return "text"
    if _EMAIL_RE.match(low):
        return "email"
    if _SAVE_CONTACT_RE.match(low):
        return "save_contact"

    return None


def gear0_contacts_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Contacts handler."""
    try:
        if handler == "call":
            m = _CALL_RE.match(query.strip())
            if m:
                return handle_call(m.group("alias"), dry_run=dry_run)
        if handler == "text":
            m = _TEXT_RE.match(query.strip())
            if m:
                return handle_text(m.group("alias"), m.group("msg"), dry_run=dry_run)
        if handler == "email":
            m = _EMAIL_RE.match(query.strip())
            if m:
                return handle_email(m.group("alias"), m.group("subject") or "", dry_run=dry_run)
        if handler == "save_contact":
            m = _SAVE_CONTACT_RE.match(query.strip())
            if m:
                return contact_save(m.group("name"), m.group("phone"), m.group("email") or "")
    except Exception:
        return None
    return None


__all__ = [
    "contact_list",
    "contact_lookup",
    "contact_save",
    "gear0_contacts_classify",
    "gear0_contacts_execute",
    "handle_call",
    "handle_email",
    "handle_text",
]
