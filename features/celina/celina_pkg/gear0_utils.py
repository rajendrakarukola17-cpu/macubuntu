"""Celina AI (Celly) - Gear 0 Utilities (Update 19)

Zero-token, zero-AI-RAM, sub-20ms deterministic utilities for Gear 0 (Reflex).
All functions are pure Python stdlib — no LLM, no network, no heavy dependencies.

Categories:
  - Gear 0c: Time & Date Math + Unit Conversion
  - Gear 0-Dev: Encoding, Hashing, UUIDs, JSON, Text transforms
  - Gear 0-Privacy: PII redaction, API key blocking, password generation
  - Gear 0-Hardware: Battery, disk, IP, clipboard
  - Gear 0-Router: Radix tree, fuzzy matching, miss logging
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import re
import secrets
import shutil
import socket
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Rust FFI (optional) — gear0_utils compute-intensive functions
#
# Loads libcelina_gear0_ffi.so if available. Falls back to Python stdlib
# implementations when the shared library is missing or fails to load.
# Public function signatures are unchanged — 102-tool contract preserved.
# ---------------------------------------------------------------------------

_GEAR0_FFI_LIB: ctypes.CDLL | None = None
_GEAR0_FFI_AVAILABLE = False


def _load_gear0_ffi() -> bool:
    """Load the Rust FFI shared library if present. Returns True on success."""
    global _GEAR0_FFI_LIB, _GEAR0_FFI_AVAILABLE
    if _GEAR0_FFI_AVAILABLE:
        return True
    import ctypes  # lazy import — only used for optional Rust FFI
    try:
        lib_path = Path(__file__).parent / "celina_gear0_ffi.so"
        if not lib_path.exists():
            _GEAR0_FFI_AVAILABLE = False
            return False
        _GEAR0_FFI_LIB = ctypes.CDLL(str(lib_path))
        # Set up argument/return types
        _GEAR0_FFI_LIB.gear0_sha256.argtypes = [ctypes.c_char_p]
        _GEAR0_FFI_LIB.gear0_sha256.restype = ctypes.c_char_p
        _GEAR0_FFI_LIB.gear0_md5.argtypes = [ctypes.c_char_p]
        _GEAR0_FFI_LIB.gear0_md5.restype = ctypes.c_char_p
        _GEAR0_FFI_LIB.gear0_base64_encode.argtypes = [ctypes.c_char_p]
        _GEAR0_FFI_LIB.gear0_base64_encode.restype = ctypes.c_char_p
        _GEAR0_FFI_LIB.gear0_base64_decode.argtypes = [ctypes.c_char_p]
        _GEAR0_FFI_LIB.gear0_base64_decode.restype = ctypes.c_char_p
        _GEAR0_FFI_LIB.gear0_ffi_free.argtypes = [ctypes.c_char_p]
        _GEAR0_FFI_LIB.gear0_ffi_free.restype = None
        _GEAR0_FFI_AVAILABLE = True
    except Exception:
        _GEAR0_FFI_LIB = None
        _GEAR0_FFI_AVAILABLE = False
    return _GEAR0_FFI_AVAILABLE


def _ffi_call(fn_name: str, input_str: str) -> str | None:
    """Call a Rust FFI function with input_str, return decoded result or None."""
    if not _GEAR0_FFI_AVAILABLE or _GEAR0_FFI_LIB is None:
        return None
    import ctypes  # lazy import — needed for c_void_p/c_char_p
    try:
        fn = getattr(_GEAR0_FFI_LIB, fn_name)
        fn.restype = ctypes.c_void_p  # Keep raw pointer for free()
        result_ptr = fn(input_str.encode("utf-8"))
        if not result_ptr:
            return None
        result = ctypes.cast(result_ptr, ctypes.c_char_p).value
        if result is None:
            _GEAR0_FFI_LIB.gear0_ffi_free(result_ptr)
            return None
        decoded = result.decode("utf-8")
        _GEAR0_FFI_LIB.gear0_ffi_free(result_ptr)
        return decoded
    except Exception:
        return None


# Mark FFI as attempted at import time (non-fatal if missing)
_load_gear0_ffi()


try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None  # type: ignore

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns (compiled once at import, never at runtime)
# ---------------------------------------------------------------------------

_PII_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_PII_PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
_PII_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
_API_KEY_RE = re.compile(r"\b(?:sk-[A-Za-z0-9\-_]{12,}|ghp_[A-Za-z0-9\-_]{20,}|AIza[0-9A-Za-z\\-_]{20,})\b")
_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")

# Unit conversion patterns
_UNIT_RE = re.compile(
    r"^(?:convert\s+)?(?P<value>[\d.]+)\s*(?P<from_unit>km|miles|mi|c|celsius|f|fahrenheit|lb|lbs|kg|kilograms?)\s*"
    r"(?:to|in|=>)\s*(?P<to_unit>km|miles|mi|c|celsius|f|fahrenheit|lb|lbs|kg|kilograms?)$",
    re.IGNORECASE,
)

# Time math patterns
_DAYS_FROM_NOW_RE = re.compile(
    r"^(?:what(?:'s| is| was)|calculate|compute)?\s*(\d+)\s*days?\s*from\s*now\??$",
    re.IGNORECASE,
)
_DAYS_UNTIL_RE = re.compile(
    r"^(?:how\s+many\s+days?\s+until|days?\s+until|until)\s+(.+?)\??$",
    re.IGNORECASE,
)
_TIME_IN_RE = re.compile(
    r"^(?:what\s+time\s+is\s+it\s+in|time\s+in)\s+(.+?)\??$",
    re.IGNORECASE,
)

# Hashing patterns
_SHA256_RE = re.compile(r"^(?:sha256|sha-256)\s+(?:of|hash)\s+(.+)$", re.IGNORECASE)
_MD5_RE = re.compile(r"^(?:md5)\s+(?:of|hash)\s+(.+)$", re.IGNORECASE)

# Base64 patterns
_BASE64_ENC_RE = re.compile(r"^(?:base64\s+)?encode\s+(.+)$", re.IGNORECASE)
_BASE64_DEC_RE = re.compile(r"^(?:base64\s+)?decode\s+(.+)$", re.IGNORECASE)

# Text transform patterns
_UPPER_RE = re.compile(r"^(?:make\s+)?(?:this\s+)?(?:uppercase|upper\s+case)\s*(.+)?$", re.IGNORECASE)
_LOWER_RE = re.compile(r"^(?:make\s+)?(?:this\s+)?(?:lowercase|lower\s+case)\s*(.+)?$", re.IGNORECASE)
_TITLE_RE = re.compile(r"^(?:make\s+)?(?:this\s+)?(?:title\s+case)\s*(.+)?$", re.IGNORECASE)
_WORD_COUNT_RE = re.compile(r"^(?:word\s+count|count\s+words)\s*(.+)?$", re.IGNORECASE)
_CHAR_COUNT_RE = re.compile(r"^(?:character\s+count|count\s+characters)\s*(.+)?$", re.IGNORECASE)

# UUID / timestamp patterns
_UUID_RE = re.compile(r"^(?:generate\s+)?(?:a\s+)?(?:uuid|uuidv4)\??$", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"^(?:unix\s+)?(?:timestamp|epoch)\??$", re.IGNORECASE)

# Password pattern
_PASSWORD_RE = re.compile(r"^(?:generate\s+)?(?:a\s+)?(?:password|passwd)\s*(?:of\s+)?(\d+)?\s*(?:char|chars|character)?\??$", re.IGNORECASE)

# JSON patterns
_JSON_PRETTIFY_RE = re.compile(r"^(?:prettify|format|validate)\s+this\s+json\??$", re.IGNORECASE)
_JSON_MINIFY_RE = re.compile(r"^(?:minify|compress)\s+this\s+json\??$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Gear 0c: Time & Date Math
# ---------------------------------------------------------------------------

def gear0_days_from_now(days: int) -> str:
    """Calculate the date N days from now."""
    try:
        target = datetime.date.today() + datetime.timedelta(days=days)
        return target.strftime("%A, %B %d, %Y")
    except Exception:
        return "Unable to calculate date."


def gear0_days_until(target_date_str: str) -> str:
    """Calculate days until a target date (e.g. 'December 25', 'Dec 25')."""
    try:
        today = datetime.date.today()
        # Try parsing common date formats
        for fmt in ("%B %d", "%b %d", "%m/%d", "%m-%d"):
            try:
                target = datetime.datetime.strptime(target_date_str.strip(), fmt).date()
                target = target.replace(year=today.year)
                if target < today:
                    target = target.replace(year=today.year + 1)
                delta = (target - today).days
                return f"{delta} days"
            except ValueError:
                continue
        return "Unable to parse date. Try 'December 25' or '12/25'."
    except Exception:
        return "Unable to calculate."


def gear0_time_in_timezone(tz_name: str) -> str:
    """Get current time in a timezone (e.g. 'Tokyo', 'New York', 'UTC')."""
    if ZoneInfo is None:
        return "Timezone support not available on this Python version."
    try:
        tz = ZoneInfo(tz_name)
        now = datetime.datetime.now(tz)
        return now.strftime("%I:%M %p %Z on %A, %B %d")
    except Exception:
        return f"Unknown timezone: {tz_name}. Try 'Tokyo', 'New York', 'London', or 'UTC'."


# ---------------------------------------------------------------------------
# Gear 0c: Unit Conversion
# ---------------------------------------------------------------------------

def gear0_convert(value: float, from_unit: str, to_unit: str) -> str:
    """Convert between common units. Returns formatted string."""
    from_unit = from_unit.lower()
    to_unit = to_unit.lower()

    # Normalize aliases
    unit_aliases = {
        "km": "km", "kilometer": "km", "kilometers": "km",
        "miles": "mi", "mile": "mi", "mi": "mi",
        "c": "c", "celsius": "c", "°c": "c",
        "f": "f", "fahrenheit": "f", "°f": "f",
        "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
        "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    }
    from_norm = unit_aliases.get(from_unit, from_unit)
    to_norm = unit_aliases.get(to_unit, to_unit)

    try:
        # Distance: km <-> miles
        if from_norm == "km" and to_norm == "mi":
            result = value * 0.621371
            return f"{value} km = {result:.2f} miles"
        if from_norm == "mi" and to_norm == "km":
            result = value / 0.621371
            return f"{value} miles = {result:.2f} km"

        # Temperature: C <-> F
        if from_norm == "c" and to_norm == "f":
            result = value * 9 / 5 + 32
            return f"{value}°C = {result:.1f}°F"
        if from_norm == "f" and to_norm == "c":
            result = (value - 32) * 5 / 9
            return f"{value}°F = {result:.1f}°C"

        # Weight: lb <-> kg
        if from_norm == "kg" and to_norm == "lb":
            result = value * 2.20462
            return f"{value} kg = {result:.2f} lbs"
        if from_norm == "lb" and to_norm == "kg":
            result = value / 2.20462
            return f"{value} lbs = {result:.2f} kg"

        if from_norm == to_norm:
            return f"{value} {from_unit} = {value} {to_unit}"

        return f"Unsupported conversion: {from_unit} to {to_unit}. Try km↔miles, C↔F, or lb↔kg."
    except Exception:
        return "Conversion failed."


# ---------------------------------------------------------------------------
# Gear 0-Dev: Encoding, Hashing, UUID, JSON, Text
# ---------------------------------------------------------------------------

def gear0_sha256(text: str) -> str:
    """SHA-256 hash of text. Uses Rust FFI when available, falls back to Python stdlib."""
    ffi_result = _ffi_call("gear0_sha256", text)
    if ffi_result is not None:
        return ffi_result
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def gear0_md5(text: str) -> str:
    """MD5 hash of text. Uses Rust FFI when available, falls back to Python stdlib."""
    ffi_result = _ffi_call("gear0_md5", text)
    if ffi_result is not None:
        return ffi_result
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def gear0_base64_encode(text: str) -> str:
    """Base64 encode text. Uses Rust FFI when available, falls back to Python stdlib."""
    ffi_result = _ffi_call("gear0_base64_encode", text)
    if ffi_result is not None:
        return ffi_result
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


def gear0_base64_decode(text: str) -> str:
    """Base64 decode text. Uses Rust FFI when available, falls back to Python stdlib."""
    ffi_result = _ffi_call("gear0_base64_decode", text)
    if ffi_result is not None:
        return ffi_result
    try:
        return base64.b64decode(text).decode("utf-8")
    except Exception:
        return "Invalid base64 input."


def gear0_uuid() -> str:
    """Generate UUIDv4."""
    return str(uuid.uuid4())


def gear0_unix_timestamp() -> str:
    """Current Unix timestamp."""
    return str(int(datetime.datetime.now().timestamp()))


def gear0_password(length: int = 16) -> str:
    """Generate cryptographically secure random password."""
    try:
        length = max(8, min(128, int(length)))
    except Exception:
        length = 16
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def gear0_prettify_json(text: str) -> str:
    """Validate and pretty-print JSON."""
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"


def gear0_minify_json(text: str) -> str:
    """Minify JSON."""
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"


def gear0_uppercase(text: str) -> str:
    return text.upper()


def gear0_lowercase(text: str) -> str:
    return text.lower()


def gear0_titlecase(text: str) -> str:
    return text.title()


def gear0_word_count(text: str) -> str:
    return f"{len(text.split())} words"


def gear0_char_count(text: str) -> str:
    return f"{len(text)} characters"


# ---------------------------------------------------------------------------
# Gear 0-Privacy: PII redaction, API key blocking, password generation
# ---------------------------------------------------------------------------

def gear0_redact_pii(text: str) -> str:
    """Redact emails, phones, credit cards from text."""
    text = _PII_EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PII_PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _PII_CC_RE.sub("[REDACTED_CARD]", text)
    return text


def gear0_detect_api_key(text: str) -> str | None:
    """Return API key pattern if found, else None."""
    if _API_KEY_RE.search(text):
        return "sk- or ghp_ or AIza API key pattern"
    return None


# ---------------------------------------------------------------------------
# Gear 0-Hardware: Battery, disk, IP, clipboard
# ---------------------------------------------------------------------------

def gear0_battery() -> str:
    """Get battery percentage."""
    try:
        import psutil
        batt = psutil.sensors_battery()
        if batt is None:
            return "Battery information not available."
        return f"{batt.percent}% {'(charging)' if batt.power_plugged else ''}"
    except Exception:
        return "Unable to read battery."


def gear0_disk_free(path: str = "/") -> str:
    """Get free disk space."""
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        pct = (usage.free / usage.total) * 100
        return f"{free_gb:.1f} GB free of {total_gb:.1f} GB ({pct:.0f}% free)"
    except Exception:
        return "Unable to read disk usage."


def gear0_local_ip() -> str:
    """Get local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "Unable to determine local IP."


def gear0_clipboard_read() -> str:
    """Read clipboard (best-effort, may return empty on headless)."""
    try:
        import pyperclip  # type: ignore
        return pyperclip.paste()
    except Exception:
        return "Clipboard not available on this system."


def gear0_clipboard_write(text: str) -> str:
    """Write to clipboard (best-effort)."""
    try:
        import pyperclip  # type: ignore
        pyperclip.copy(text)
        return f"Copied to clipboard: {text[:50]}..."
    except Exception:
        return "Clipboard not available on this system."


# ---------------------------------------------------------------------------
# Gear 0-Router: Fuzzy matching, miss logging
# ---------------------------------------------------------------------------

def gear0_damerau_levenshtein(s1: str, s2: str) -> int:
    """Damerau-Levenshtein distance (includes transpositions)."""
    if len(s1) < len(s2):
        return gear0_damerau_levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
            if i > 0 and j > 0 and c1 == s2[j - 1] and c2 == s1[i - 1]:
                current_row[-1] = min(current_row[-1], previous_row[j - 1] + 1)
        previous_row = current_row
    return previous_row[-1]


def gear0_fuzzy_match(query: str, candidates: list[str], threshold: float = 0.85) -> str | None:
    """Return best fuzzy match above threshold, else None."""
    if not candidates:
        return None
    best: str | None = None
    best_score = 0.0
    q = query.lower().strip()
    for cand in candidates:
        c = cand.lower().strip()
        max_len = max(len(q), len(c))
        if max_len == 0:
            continue
        dist = gear0_damerau_levenshtein(q, c)
        score = 1.0 - (dist / max_len)
        if score > best_score:
            best_score = score
            best = cand
    if best_score >= threshold:
        return best
    return None


_GEAR0_MISS_LOG = Path.home() / ".celina" / "storage" / "shared" / "gear0_misses.log"


def gear0_log_miss(query: str) -> None:
    """Log a Gear 0 miss for later analysis/promotion."""
    try:
        _GEAR0_MISS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_GEAR0_MISS_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat()}\t{query}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Master dispatch: classify Gear 0 request type
# ---------------------------------------------------------------------------

def gear0_classify(query: str) -> str | None:
    """Classify a query into a Gear 0 handler name, or None if not a Gear 0 match.

    Returns one of:
      'time_days_from_now', 'time_days_until', 'time_in_tz',
      'convert', 'sha256', 'md5', 'base64_encode', 'base64_decode',
      'uuid', 'timestamp', 'password', 'json_prettify', 'json_minify',
      'uppercase', 'lowercase', 'titlecase', 'word_count', 'char_count',
      'battery', 'disk', 'ip', 'clipboard_read', 'clipboard_write',
      None
    """
    low = query.strip().lower()

    # Time math
    m = _DAYS_FROM_NOW_RE.match(low)
    if m:
        return "time_days_from_now"
    m = _DAYS_UNTIL_RE.match(low)
    if m:
        return "time_days_until"
    m = _TIME_IN_RE.match(low)
    if m:
        return "time_in_tz"

    # Unit conversion
    m = _UNIT_RE.match(low)
    if m:
        return "convert"

    # Hashing
    m = _SHA256_RE.match(low)
    if m:
        return "sha256"
    m = _MD5_RE.match(low)
    if m:
        return "md5"

    # Base64
    m = _BASE64_ENC_RE.match(low)
    if m:
        return "base64_encode"
    m = _BASE64_DEC_RE.match(low)
    if m:
        return "base64_decode"

    # UUID / timestamp / password
    if _UUID_RE.match(low):
        return "uuid"
    if _TIMESTAMP_RE.match(low):
        return "timestamp"
    m = _PASSWORD_RE.match(low)
    if m:
        return "password"

    # JSON
    if _JSON_PRETTIFY_RE.match(low):
        return "json_prettify"
    if _JSON_MINIFY_RE.match(low):
        return "json_minify"

    # Text transforms
    if _UPPER_RE.match(low):
        return "uppercase"
    if _LOWER_RE.match(low):
        return "lowercase"
    if _TITLE_RE.match(low):
        return "titlecase"
    if _WORD_COUNT_RE.match(low):
        return "word_count"
    if _CHAR_COUNT_RE.match(low):
        return "char_count"

    # Hardware
    if low in {"battery", "check battery", "battery status", "battery percentage"}:
        return "battery"
    if low in {"disk space", "check disk", "check storage", "storage status", "free space"}:
        return "disk"
    if low in {"my ip", "my ip address", "what is my ip", "whats my ip", "what's my ip"}:
        return "ip"

    return None


def gear0_execute(handler: str, query: str) -> str | None:
    """Execute a Gear 0 handler and return the result string, or None on failure."""
    try:
        if handler == "time_days_from_now":
            m = _DAYS_FROM_NOW_RE.match(query.strip().lower())
            if m:
                return gear0_days_from_now(int(m.group(1)))
        elif handler == "time_days_until":
            m = _DAYS_UNTIL_RE.match(query.strip().lower())
            if m:
                return gear0_days_until(m.group(1))
        elif handler == "time_in_tz":
            m = _TIME_IN_RE.match(query.strip().lower())
            if m:
                return gear0_time_in_timezone(m.group(1))
        elif handler == "convert":
            m = _UNIT_RE.match(query.strip().lower())
            if m:
                return gear0_convert(float(m.group("value")), m.group("from_unit"), m.group("to_unit"))
        elif handler == "sha256":
            m = _SHA256_RE.match(query.strip().lower())
            if m:
                return gear0_sha256(m.group(1))
        elif handler == "md5":
            m = _MD5_RE.match(query.strip().lower())
            if m:
                return gear0_md5(m.group(1))
        elif handler == "base64_encode":
            m = _BASE64_ENC_RE.match(query.strip().lower())
            if m:
                return gear0_base64_encode(m.group(1))
        elif handler == "base64_decode":
            m = _BASE64_DEC_RE.match(query.strip().lower())
            if m:
                return gear0_base64_decode(m.group(1))
        elif handler == "uuid":
            return gear0_uuid()
        elif handler == "timestamp":
            return gear0_unix_timestamp()
        elif handler == "password":
            m = _PASSWORD_RE.match(query.strip().lower())
            length = int(m.group(1)) if m and m.group(1) else 16
            return gear0_password(length)
        elif handler == "json_prettify":
            # Extract JSON from after "prettify this json" — user may paste on next line
            # For now, try to find JSON-like content in the query
            return gear0_prettify_json(query)
        elif handler == "json_minify":
            return gear0_minify_json(query)
        elif handler == "uppercase":
            m = _UPPER_RE.match(query.strip().lower())
            if m and m.group(1):
                return gear0_uppercase(m.group(1))
            return gear0_uppercase(query)
        elif handler == "lowercase":
            m = _LOWER_RE.match(query.strip().lower())
            if m and m.group(1):
                return gear0_lowercase(m.group(1))
            return gear0_lowercase(query)
        elif handler == "titlecase":
            m = _TITLE_RE.match(query.strip().lower())
            if m and m.group(1):
                return gear0_titlecase(m.group(1))
            return gear0_titlecase(query)
        elif handler == "word_count":
            m = _WORD_COUNT_RE.match(query.strip().lower())
            if m and m.group(1):
                return gear0_word_count(m.group(1))
            return gear0_word_count(query)
        elif handler == "char_count":
            m = _CHAR_COUNT_RE.match(query.strip().lower())
            if m and m.group(1):
                return gear0_char_count(m.group(1))
            return gear0_char_count(query)
        elif handler == "battery":
            return gear0_battery()
        elif handler == "disk":
            return gear0_disk_free()
        elif handler == "ip":
            return gear0_local_ip()
    except Exception:
        return None
    return None


__all__ = [
    "gear0_battery",
    "gear0_classify",
    "gear0_detect_api_key",
    "gear0_disk_free",
    "gear0_execute",
    "gear0_fuzzy_match",
    "gear0_local_ip",
    "gear0_log_miss",
    "gear0_redact_pii",
]
