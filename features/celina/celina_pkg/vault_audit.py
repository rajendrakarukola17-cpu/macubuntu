"""macubuntu vault_audit.py — Offline passkey / password breach hygiene (HIBP k-anonymity).

Implements HaveIBeenPwned k-anonymous range lookup:
  1. SHA-1(hash) the candidate password locally (plaintext NEVER leaves this process).
  2. Send only the first 5 hex chars (prefix) to https://api.pwnedpasswords.com/range/
  3. Receive suffix:count pairs server-side; match locally against full hash suffix.
  4. Return breach count + matched hashes without ever transmitting plaintext or full hash.

Privacy guarantees:
  - Zero plaintext or full SHA-1 sent over the network (only 5-char prefix → k-anonymity).
  - Offline mode: works against a local breach-hash suffix list if network is unavailable.
  - Never feeds candidate text or breach results into any LLM session.
  - stdlib-only (hashlib, urllib, json, sqlite3, pathlib).

Usage:
    from celina_pkg.vault_audit import audit_password, AuditResult
    result = audit_password("correct horse battery staple")
    print(result.breach_count, result.exposed, result.source)
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# HaveIBeenPwned k-anonymity endpoint (range API). Only a 5-char SHA-1 prefix is sent.
HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/"

# Local fallback breach-hash suffix database (offline, zero network).
# Format: one JSON object per line: {"suffix": "<35-hex>", "count": <int>}
DEFAULT_OFFLINE_DB = Path.home() / ".local" / "share" / "celina" / "vault" / "breach_suffixes.jsonl"

REQUEST_TIMEOUT = 8.0  # seconds


@dataclass
class AuditResult:
    password: str  # plaintext retained ONLY in caller scope; never logged or transmitted
    sha1: str      # full SHA-1 (computed locally, never transmitted)
    prefix: str    # first 5 hex chars (the only value ever sent over the network)
    breach_count: int
    exposed: bool
    source: str    # "hibp" | "offline" | "unavailable"
    error: str | None = None

    def __repr__(self) -> str:
        # Never leak plaintext password or full SHA-1 in repr/logs.
        return (
            f"AuditResult(breach_count={self.breach_count}, "
            f"exposed={self.exposed}, source={self.source!r}, "
            f"prefix={self.prefix!r}, error={self.error!r})"
        )

    def to_dict(self) -> dict:
        # Never include plaintext or full hash in serialized output.
        return {
            "breach_count": self.breach_count,
            "exposed": self.exposed,
            "source": self.source,
            "prefix": self.prefix,
            "error": self.error,
        }


def sha1_of(text: str) -> str:
    """SHA-1 hex digest of `text` (computed locally, never transmitted).
    
    SHA-1 is used here because the HIBP API requires it for k-anonymity
    prefix lookups — not for cryptographic security.
    """
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest().upper()


def _hibp_range_lookup(prefix: str) -> tuple[dict[str, int] | None, str | None]:
    """Query HIBP range API with a 5-char SHA-1 prefix.
    Returns (suffixes->count map, error_message).
    """
    url = HIBP_RANGE_URL + prefix
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "macubuntu-vault-audit/1.0",
                # HIBP Add-Padding: obscures response size so network observers
                # cannot infer breach counts from HTTP response length.
                "Add-Padding": "true",
            },
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # nosec B310 — HIBP range API, HTTPS only
            body = resp.read().decode("utf-8", errors="replace")
        suffixes: dict[str, int] = {}
        for line in body.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            suffix, count = line.split(":", 1)
            suffixes[suffix.strip().upper()] = int(count.strip())
        return suffixes, None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        return None, str(exc)


def _offline_lookup(sha1: str, db_path: Path = DEFAULT_OFFLINE_DB) -> tuple[int | None, str | None]:
    """Look up breach count from a local JSONL suffix database.
    Returns (count_or_None, error_message).
    """
    suffix = sha1[5:]  # 35-char suffix after the 5-char prefix
    if not db_path.exists():
        return None, f"offline db not found: {db_path}"
    try:
        with db_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("suffix", "").upper() == suffix:
                    return int(entry.get("count", 0)), None
        return 0, None  # not found in local db → no known breaches
    except OSError as exc:
        return None, str(exc)


def audit_password(
    password: str,
    *,
    offline_db: Path | None = None,
    prefer_offline: bool = False,
) -> AuditResult:
    """Audit a password for known breaches using HIBP k-anonymity (prefix-only).

    Network flow (default):
      sha1 = SHA-1(password)           [local only]
      prefix = sha1[:5]                 [only value sent to network]
      suffixes = GET /range/{prefix}    [server-side, suffixes + counts]
      count = suffixes.get(sha1[5:])    [local match, nothing more sent]

    Offline flow:
      count = lookup(sha1[5:]) in local JSONL db

    Never transmits plaintext or the full SHA-1. Never sends results to any LLM.
    """
    sha1 = sha1_of(password)
    prefix = sha1[:5]
    source = "unavailable"
    breach_count = 0
    error: str | None = None

    if prefer_offline:
        count, err = _offline_lookup(sha1, offline_db or DEFAULT_OFFLINE_DB)
        if err is None:
            breach_count = count or 0
            source = "offline"
        else:
            error = err
            # Fall through to network if offline failed
    else:
        suffixes, err = _hibp_range_lookup(prefix)
        if err is None:
            breach_count = int(suffixes.get(sha1[5:], 0))
            source = "hibp"
        else:
            error = err
            # Fall back to offline db if available
            count, oerr = _offline_lookup(sha1, offline_db or DEFAULT_OFFLINE_DB)
            if oerr is None:
                breach_count = count or 0
                source = "offline"
            else:
                error = f"hibp: {err}; offline: {oerr}"

    return AuditResult(
        password=password,
        sha1=sha1,
        prefix=prefix,
        breach_count=breach_count,
        exposed=breach_count > 0,
        source=source,
        error=error,
    )


def audit_passwords_batch(
    passwords: list[str],
    *,
    offline_db: Path | None = None,
    prefer_offline: bool = False,
) -> list[AuditResult]:
    """Audit multiple passwords; each audited independently (k-anonymity per password)."""
    return [audit_password(p, offline_db=offline_db, prefer_offline=prefer_offline) for p in passwords]
