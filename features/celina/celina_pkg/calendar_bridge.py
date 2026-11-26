"""Celina AI - Native GNOME Calendar / Evolution RFC 5545 (.ics) App Intent Bridge (v4.3)
Gives Celina genuine Siri-style Calendar App Intents without fragile GUI clicking or cloud lock-in:
  - Reads GNOME Calendar's native local backend (`~/.local/share/evolution/calendar/system/calendar.ics`)
    plus a private fallback `~/.local/share/celina/calendar.ics` (`0600`) when Evolution isn't initialized.
  - Pure Python stdlib RFC 5545 parser & writer (zero third-party dependencies).
  - `create_calendar_event` is approval-gated with cryptographic argument binding and atomic `0600` writes.
"""

from __future__ import annotations

import datetime
import fcntl
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import approval, config

ApprovalCallback = Callable[[str, str], bool]

EVOLUTION_DEFAULT_ICS = config.HOME / ".local" / "share" / "evolution" / "calendar" / "system" / "calendar.ics"
CELINA_FALLBACK_ICS = config.BASE_DIR / "calendar.ics"


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    summary: str
    start: datetime.datetime
    end: datetime.datetime | None = None
    location: str = ""
    description: str = ""


def _resolve_calendar_path(ics_path: Path | None = None) -> Path:
    if ics_path is not None:
        return Path(ics_path)
    if EVOLUTION_DEFAULT_ICS.exists():
        return EVOLUTION_DEFAULT_ICS
    return CELINA_FALLBACK_ICS


def _unfold_ics_lines(raw: str) -> list[str]:
    """Unfolds RFC 5545 continuation lines (CRLF + space/tab)."""
    unfolded: list[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if (line.startswith(" ") or line.startswith("\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _unescape_ics(val: str) -> str:
    return (
        val.replace("\\\\", "\\")     # escape first: literal backslash -> single backslash
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .strip()
    )

def _escape_ics(val: str) -> str:
    return (
        (val or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .strip()
    )


def _fold_ics_line(line: str, limit: int = 75) -> str:
    """Fold an RFC 5545 line at `limit` octets (CRLF + single space)."""
    if len(line.encode("utf-8")) <= limit:
        return line
    out = [line[:limit]]
    rest = line[limit:]
    while rest:
        chunk = rest[:limit - 1]
        out.append(" " + chunk)
        rest = rest[limit - 1:]
    return "\r\n".join(out)


def _parse_ics_datetime(raw_val: str, tzid: str = "") -> datetime.datetime | None:
    v = (raw_val or "").strip()
    if not v:
        return None
    # Strip trailing Z (treat as naive local-comparable if UTC or convert cleanly)
    is_utc = v.endswith("Z")
    core = v[:-1] if is_utc else v
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            dt = datetime.datetime.strptime(core, fmt)
            if is_utc:
                dt = dt.replace(tzinfo=datetime.UTC).astimezone().replace(tzinfo=None)
            elif tzid:
                try:
                    from zoneinfo import ZoneInfo
                    dt = dt.replace(tzinfo=ZoneInfo(tzid)).astimezone().replace(tzinfo=None)
                except Exception:
                    pass  # unknown TZ -> keep naive (documented tradeoff)
            return dt
        except ValueError:
            continue
    return None


def parse_ics_events(raw_ics: str) -> list[CalendarEvent]:
    """Parses VEVENT blocks from an RFC 5545 .ics string."""
    events: list[CalendarEvent] = []
    in_event = False
    cur: dict[str, str] = {}
    for line in _unfold_ics_lines(raw_ics):
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            in_event = True
            cur = {}
            continue
        if upper == "END:VEVENT":
            if in_event:
                dt_start = _parse_ics_datetime(
                    cur.get("DTSTART", ""), tzid=cur.get("_DTSTART_TZID", ""),
                )
                if dt_start is not None:
                    events.append(
                        CalendarEvent(
                            uid=cur.get("UID", ""),
                            summary=_unescape_ics(cur.get("SUMMARY", "(Untitled Event)")),
                            start=dt_start,
                            end=_parse_ics_datetime(
                                cur.get("DTEND", ""), tzid=cur.get("_DTEND_TZID", ""),
                            ),
                            location=_unescape_ics(cur.get("LOCATION", "")),
                            description=_unescape_ics(cur.get("DESCRIPTION", "")),
                        ),
                    )
            in_event = False
            cur = {}
            continue
        if not in_event or ":" not in line:
            continue
        key_part, val_part = line.split(":", 1)
        prop_name = key_part.split(";", 1)[0].strip().upper()
        if prop_name in {"UID", "SUMMARY", "DTSTART", "DTEND", "LOCATION", "DESCRIPTION"}:
            cur[prop_name] = val_part.strip()
            # Capture TZID parameter for timezone-aware datetime parsing
            for piece in key_part.split(";")[1:]:
                if "=" in piece:
                    k, v = piece.split("=", 1)
                    if k.strip().upper() == "TZID":
                        cur["_" + prop_name + "_TZID"] = v.strip()
    events.sort(key=lambda e: e.start)
    return events


def _event_end_or_default(ev: CalendarEvent) -> datetime.datetime:
    """Computes effective event end time so ongoing meetings and all-day events today stay visible."""
    if ev.end is not None and ev.end >= ev.start:
        return ev.end
    if ev.start.hour == 0 and ev.start.minute == 0 and ev.start.second == 0:
        return ev.start + datetime.timedelta(days=1)
    return ev.start + datetime.timedelta(hours=1)


def load_upcoming_events(
    days: int = 7,
    *,
    ics_path: Path | None = None,
    now: datetime.datetime | None = None,
) -> list[CalendarEvent]:
    """Returns parsed upcoming CalendarEvent objects sorted chronologically."""
    path = _resolve_calendar_path(ics_path)
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ref = now or datetime.datetime.now()
    horizon = ref + datetime.timedelta(days=max(1, min(90, int(days))))
    return [
        e for e in parse_ics_events(raw)
        if _event_end_or_default(e) >= ref - datetime.timedelta(minutes=15) and e.start <= horizon
    ]


def get_upcoming_events(
    days: int = 7,
    *,
    ics_path: Path | None = None,
    now: datetime.datetime | None = None,
) -> str:
    """Lists upcoming GNOME Calendar / local .ics events within the next `days` days."""
    path = _resolve_calendar_path(ics_path)
    if not path.exists():
        return f"No calendar events found yet ({path} does not exist)."
    upcoming = load_upcoming_events(days=days, ics_path=path, now=now)
    if not upcoming:
        return f"No upcoming events in the next {days} day(s)."

    lines = [f"📅 Upcoming Events (next {days} day(s)):"]
    for ev in upcoming[:20]:
        when = ev.start.strftime("%a %b %d, %H:%M")
        loc = f" @ {ev.location}" if ev.location else ""
        lines.append(f"  • {when} — {ev.summary}{loc}")
    return "\n".join(lines)


def _parse_user_datetime(text: str) -> datetime.datetime | None:
    s = (text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def create_calendar_event(
    title: str,
    start_time: str,
    duration_minutes: int = 60,
    location: str = "",
    description: str = "",
    *,
    ics_path: Path | None = None,
    approval_cb: ApprovalCallback | None = None,
) -> str:
    """Creates a native RFC 5545 VEVENT in GNOME Calendar (or private Celina .ics),
    protected by argument-bound approval and atomic 0600 file persistence.
    `start_time` format: 'YYYY-MM-DD HH:MM' (e.g. '2026-11-15 14:30').
    """
    clean_title = (title or "").strip()[:160]
    if not clean_title:
        return "Error: Event title is required."
    dt_start = _parse_user_datetime(start_time)
    if dt_start is None:
        return "Error: Invalid start_time. Use 'YYYY-MM-DD HH:MM' (e.g. '2026-11-15 14:30')."
    dur = max(5, min(1440, int(duration_minutes)))
    dt_end = dt_start + datetime.timedelta(minutes=dur)
    path = _resolve_calendar_path(ics_path)

    args = {
        "title": clean_title,
        "start_time": dt_start.strftime("%Y-%m-%d %H:%M"),
        "duration_minutes": dur,
        "location": (location or "").strip()[:160],
        "path": str(path),
    }
    token, deny_err = approval.request_bound_approval(
        "create_calendar_event",
        args,
        approval_cb,
        title=f"Approve Creating Calendar Event '{clean_title}'?",
        target=f"{dt_start.strftime('%Y-%m-%d %H:%M')} ({dur}m) -> {path.name}",
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "create_calendar_event", args)
    if not ok:
        return err

    now_utc = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    ev_uid = f"{uuid.uuid4()}@celina.local"
    vevent_lines = [
        "BEGIN:VEVENT",
        f"UID:{ev_uid}",
        f"DTSTAMP:{now_utc}",
        f"DTSTART:{dt_start.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND:{dt_end.strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:{_escape_ics(clean_title)}",
    ]
    if location.strip():
        vevent_lines.append(f"LOCATION:{_escape_ics(location)}")
    if description.strip():
        vevent_lines.append(f"DESCRIPTION:{_escape_ics(description[:500])}")
    vevent_lines.append("END:VEVENT")
    vevent_block = "\r\n".join(_fold_ics_line(l) for l in vevent_lines) + "\r\n"

    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                if path.exists():
                    existing = path.read_text(encoding="utf-8", errors="replace")
                    idx = existing.upper().rfind("END:VCALENDAR")
                    if idx != -1:
                        updated = existing[:idx] + vevent_block + existing[idx:]
                    else:
                        updated = existing.rstrip() + "\r\n" + vevent_block + "END:VCALENDAR\r\n"
                else:
                    updated = (
                        "BEGIN:VCALENDAR\r\n"
                        "VERSION:2.0\r\n"
                        "PRODID:-//macubuntu//Celina Calendar Bridge//EN\r\n"
                        + vevent_block
                        + "END:VCALENDAR\r\n"
                    )
                config.atomic_write_private_text(path, updated)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except OSError as exc:
        return f"Error saving calendar event to {path}: {exc}"

    return f"📅 Created calendar event '{clean_title}' on {dt_start.strftime('%Y-%m-%d %H:%M')} ({dur} min)."
