"""Celina AI (Celly) - Zero-Leak Privacy Firewall
Scans outbound cloud payloads for secrets, PII (emails, phones, cards, Aadhaar, PAN,
home paths), and private local file contents—enforcing redaction or explicit user
consent before a single byte leaves the laptop.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import config

# Callback returns one of: "redact" | "allow" | "local"
PrivacyConsentCallback = Callable[[str, str], str]

# --- Hard-Stripped Secrets (NEVER allowed to reach external cloud APIs) ---
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----")),
    ("API_KEY", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16})\b")),
    ("CREDENTIAL", re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?([^\s'\",;]{6,})['\"]?")),
]

# --- Reversible PII Patterns (Redacted before cloud, restored locally in reply) ---
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_AADHAAR_RE = re.compile(r"\b[2-9][0-9]{3}[\s-]?[0-9]{4}[\s-]?[0-9]{4}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?[6-9][0-9]{4}[\s-]?[0-9]{5}(?!\d)")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _passes_luhn(digits: str) -> bool:
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    rev = digits[::-1]
    for i, ch in enumerate(rev):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


@dataclass
class PrivacyReport:
    secrets: list[str] = field(default_factory=list)
    pii_counts: dict[str, int] = field(default_factory=dict)

    @property
    def has_secrets(self) -> bool:
        return bool(self.secrets)

    @property
    def has_pii(self) -> bool:
        return bool(self.pii_counts)

    @property
    def summary(self) -> str:
        parts: list[str] = []
        if self.secrets:
            parts.append(f"secrets blocked ({', '.join(sorted(set(self.secrets)))})")
        if self.pii_counts:
            pii_str = ", ".join(f"{cnt} {kind.lower()}" for kind, cnt in self.pii_counts.items())
            parts.append(f"PII detected ({pii_str})")
        return "; ".join(parts) if parts else "clean"


class RedactionVault:
    """Reversible PII redactor for a single conversation turn (hard-strips raw secrets)."""

    def __init__(self):
        self._to_token: dict[str, str] = {}
        self._to_real: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._hard_secret_count: int = 0

    def has_redactions(self) -> bool:
        return bool(self._to_real) or self._hard_secret_count > 0

    def redaction_summary(self) -> str:
        """Returns a compact audit string for /why receipts (e.g. '1 EMAIL, 1 HOME_PATH')."""
        items: list[str] = [f"{cnt} {kind}" for kind, cnt in sorted(self._counters.items())]
        if "[USER_HOME]" in self._to_real and "HOME_PATH" not in self._counters:
            items.append("1 HOME_PATH")
        if self._hard_secret_count > 0:
            items.append(f"{self._hard_secret_count} SECRET")
        return f"PII redacted ({', '.join(items)})" if items else "clean"

    def _token_for(self, kind: str, original: str) -> str:
        if original in self._to_token:
            return self._to_token[original]
        idx = self._counters.get(kind, 0) + 1
        self._counters[kind] = idx
        tok = f"[{kind}_{idx}]"
        self._to_token[original] = tok
        self._to_real[tok] = original
        return tok

    def strip_hard_secrets(self, text: str) -> tuple[str, list[str]]:
        """Always strips cryptographic keys & API tokens—never reversible into cloud."""
        found: list[str] = []
        out = text
        for label, pat in _SECRET_PATTERNS:
            if pat.search(out):
                found.append(label)
                self._hard_secret_count += 1
                out = pat.sub(f"[REDACTED_{label}]", out)
        return out, found

    def redact(self, text: str) -> tuple[str, PrivacyReport]:
        """Strips secrets and replaces PII/home paths with reversible placeholders."""
        report = PrivacyReport()
        out, secrets = self.strip_hard_secrets(text)
        report.secrets.extend(secrets)

        home_str = str(config.HOME)
        if home_str and home_str in out:
            out = out.replace(home_str, "[USER_HOME]")
            self._to_real["[USER_HOME]"] = home_str
            report.pii_counts["HOME_PATH"] = report.pii_counts.get("HOME_PATH", 0) + 1

        def _sub(kind: str, pattern: re.Pattern, target: str) -> str:
            def repl(match: re.Match) -> str:
                val = match.group(0)
                if kind == "CARD":
                    digits = re.sub(r"\D", "", val)
                    if not _passes_luhn(digits):
                        return val
                report.pii_counts[kind] = report.pii_counts.get(kind, 0) + 1
                return self._token_for(kind, val)

            return pattern.sub(repl, target)

        out = _sub("EMAIL", _EMAIL_RE, out)
        out = _sub("PAN", _PAN_RE, out)
        out = _sub("CARD", _CARD_RE, out)
        out = _sub("AADHAAR", _AADHAAR_RE, out)
        out = _sub("PHONE", _PHONE_RE, out)
        return out, report

    def restore(self, text: str) -> str:
        """Restores reversible benign placeholders locally after receiving cloud response."""
        out = text
        for tok, real in self._to_real.items():
            if tok in out:
                out = out.replace(tok, real)
        return out


def scan_sensitive(text: str) -> PrivacyReport:
    """Non-destructive scan for secrets and PII."""
    _, report = RedactionVault().redact(text)
    return report


def evaluate_cloud_outbound(
    text: str,
    source_label: str,
    vault: RedactionVault,
    has_local_data: bool = False,
    consent_cb: PrivacyConsentCallback | None = None,
) -> tuple[str, str]:
    """Evaluates whether text may be sent to an external cloud model.
    Returns (decision, text_to_send) where decision is 'allow' | 'redact' | 'local'.
    """
    redacted_text, report = vault.redact(text)
    safe_raw_text, _ = vault.strip_hard_secrets(text)

    if not config.STRICT_PRIVACY:
        return ("redact", redacted_text) if report.has_secrets else ("allow", safe_raw_text)

    needs_consent = False
    reasons: list[str] = []

    if report.has_secrets:
        needs_consent = True
        reasons.append(f"Secrets stripped ({', '.join(sorted(set(report.secrets)))})")
    if report.has_pii and config.PRIVACY_CFG.get("require_consent_for_cloud_pii", True):
        needs_consent = True
        pii_desc = ", ".join(f"{c} {k.lower()}" for k, c in report.pii_counts.items())
        reasons.append(f"Personal info detected ({pii_desc})")
    if has_local_data and config.PRIVACY_CFG.get("require_consent_for_cloud_local_data", True):
        needs_consent = True
        reasons.append(f"Local data from {source_label} ({len(text)} chars)")

    if not needs_consent:
        return "allow", safe_raw_text

    # Safe default when no interactive UI/CLI consent prompt is available: keep 100% local!
    if consent_cb is None:
        return "local", safe_raw_text

    summary = f"Cloud Share Request → {config.CLOUD_MODEL}\n" + " • ".join(reasons)
    preview = redacted_text[:350] + ("..." if len(redacted_text) > 350 else "")
    choice = (consent_cb(summary, preview) or "local").strip().lower()

    if choice == "redact":
        return "redact", redacted_text
    if choice == "allow":
        # Even when user clicks 'Allow Once', hard-stripped private keys/API tokens stay redacted!
        return "allow", safe_raw_text
    return "local", safe_raw_text
