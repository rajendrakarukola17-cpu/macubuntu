"""Celina AI — 6 Specialized Everyday Desktop Agents (Update 03)
Fast, deterministic, zero-hallucination everyday automation covering:
  1. MediaAgent        — Play songs/videos (YouTube autoplay, Spotify, JioSaavn, Netflix...), volume, brightness, media controls
  2. WebAgent          — Open any URL/domain/site, search 15+ sites, login pages, Maps directions, Google Translate, weather
  3. FilesNotesAgent   — Open common folders, create folders, persistent Quick Notes & To-Do list, approval-gated Downloads Organizer, find files
  4. SystemAgent       — Battery/RAM/CPU/Disk/IP/Internet health, running apps, close app, safe power controls, Settings, dark/light mode, app installer
  5. RoutineAgent      — Natural timers/reminders ("remind me in 10 minutes to..."), Good Morning / Good Night / Study / Chill daily routines
  6. GuiControlAgent   — Quick desktop/browser screen controls ("read my screen", "click play", "scroll down", "fullscreen", "new tab", "type ...")

Every mutating/destructive action is either non-destructive (opening a URL/folder, saving a note)
or strictly guarded by Tier-2 user approval and zero-side-effect `dry_run=True`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import (
    approval,
    command_runner,
    config,
    keychain_login,
    memory,
    os_events,
    tools,
    turbo_mode,
)

ApprovalCallback = Callable[[str, str], bool]
StatusCallback = Callable[[str], None]

NOTES_DB_PATH = config.BASE_DIR / "notes.json"

COMMON_FOLDERS: dict[str, str] = {
    "downloads": "Downloads",
    "download": "Downloads",
    "downloads folder": "Downloads",
    "desktop": "Desktop",
    "desktop folder": "Desktop",
    "documents": "Documents",
    "documents folder": "Documents",
    "docs": "Documents",
    "pictures": "Pictures",
    "photos": "Pictures",
    "images": "Pictures",
    "videos": "Videos",
    "music folder": "Music",
    "home": "",
    "home folder": "",
}

TRANSLATE_LANG_CODES: dict[str, str] = {
    "telugu": "te",
    "hindi": "hi",
    "english": "en",
    "tamil": "ta",
    "kannada": "kn",
    "malayalam": "ml",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "japanese": "ja",
    "korean": "ko",
    "arabic": "ar",
    "chinese": "zh-CN",
    "marathi": "mr",
    "bengali": "bn",
    "gujarati": "gu",
}

DOWNLOAD_CATEGORIES: dict[str, set[str]] = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".heic", ".ico"},
    "Documents": {".pdf", ".doc", ".docx", ".odt", ".txt", ".md", ".rtf", ".xls", ".xlsx", ".ods", ".csv", ".ppt", ".pptx", ".odp"},
    "Videos": {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv"},
    "Audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"},
    "Archives": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"},
    "Installers": {".deb", ".rpm", ".appimage", ".flatpakref", ".iso", ".sh"},
}

QUICK_HOTKEYS: dict[str, tuple[str, str]] = {
    "press enter": ("Return", "Enter"),
    "hit enter": ("Return", "Enter"),
    "press space": ("space", "Space"),
    "hit space": ("space", "Space"),
    "press escape": ("Escape", "Escape"),
    "press esc": ("Escape", "Escape"),
    "fullscreen": ("f", "Fullscreen (F)"),
    "full screen": ("f", "Fullscreen (F)"),
    "exit fullscreen": ("Escape", "Exit Fullscreen (Esc)"),
    "new tab": ("ctrl+t", "New Tab (Ctrl+T)"),
    "open new tab": ("ctrl+t", "New Tab (Ctrl+T)"),
    "close tab": ("ctrl+w", "Close Tab (Ctrl+W)"),
    "reopen tab": ("ctrl+shift+t", "Reopen Closed Tab (Ctrl+Shift+T)"),
    "refresh page": ("ctrl+r", "Refresh (Ctrl+R)"),
    "reload page": ("ctrl+r", "Reload (Ctrl+R)"),
    "copy that": ("ctrl+c", "Copy (Ctrl+C)"),
    "paste that": ("ctrl+v", "Paste (Ctrl+V)"),
    "undo that": ("ctrl+z", "Undo (Ctrl+Z)"),
    "select all": ("ctrl+a", "Select All (Ctrl+A)"),
}

_SAFE_FOLDER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,60}$")
_INFO_QUESTION_RE = re.compile(
    r"^(?:how|what|why|when|where|who|explain|tell\s+me\s+(?:about|how|why)|can\s+you\s+explain|define|compare)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Persistent Quick Notes & To-Do Store (~/.local/share/celina/notes.json)
# ---------------------------------------------------------------------------
def _notes_path(base_dir: Path | None = None) -> Path:
    return (base_dir or config.BASE_DIR) / "notes.json"


def _load_notes_store(base_dir: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    p = _notes_path(base_dir)
    if not p.exists():
        return {"todos": [], "notes": []}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {
                "todos": list(raw.get("todos") or []),
                "notes": list(raw.get("notes") or []),
            }
    except (OSError, json.JSONDecodeError):
        return {"todos": [], "notes": []}
    return {"todos": [], "notes": []}


def _save_notes_store(data: dict[str, list[dict[str, Any]]], base_dir: Path | None = None) -> None:
    p = _notes_path(base_dir)
    config.atomic_write_private_text(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def add_todo(task_text: str, *, dry_run: bool = False, base_dir: Path | None = None) -> str:
    clean = task_text.strip().rstrip(".")
    if not clean:
        return "Tell me what task to add (e.g., 'add todo buy milk')."
    if dry_run:
        return f"🧪 Dry-Run [FilesNotesAgent]: Would add to-do '{clean}'."
    store = _load_notes_store(base_dir)
    store["todos"].append({
        "id": int(time.time() * 1000) % 10_000_000,
        "text": clean,
        "done": False,
        "created_at": datetime.datetime.now().isoformat(timespec="minutes"),
    })
    _save_notes_store(store, base_dir)
    open_cnt = sum(1 for t in store["todos"] if not t.get("done"))
    return f'✅ Added to-do: "{clean}" ({open_cnt} pending task(s)).'


def list_todos(*, base_dir: Path | None = None) -> str:
    store = _load_notes_store(base_dir)
    pending = [t for t in store["todos"] if not t.get("done")]
    done_cnt = len(store["todos"]) - len(pending)
    if not pending:
        return f"📋 Your to-do list is clear ({done_cnt} completed). Say 'add todo <task>' to add one."
    lines = [f"📋 Pending To-Dos ({len(pending)}):"]
    for idx, item in enumerate(pending, 1):
        lines.append(f"  {idx}. [ ] {item.get('text', '')}")
    if done_cnt:
        lines.append(f"  ({done_cnt} completed)")
    return "\n".join(lines)


def complete_todo(selector: str, *, dry_run: bool = False, base_dir: Path | None = None) -> str:
    sel = selector.strip().lower()
    if dry_run:
        return f"🧪 Dry-Run [FilesNotesAgent]: Would mark to-do '{selector.strip()}' completed."
    store = _load_notes_store(base_dir)
    pending = [t for t in store["todos"] if not t.get("done")]
    if not pending:
        return "📋 You have no pending to-dos."
    target = None
    if sel.isdigit():
        idx = int(sel)
        if 1 <= idx <= len(pending):
            target = pending[idx - 1]
    else:
        for item in pending:
            if sel in str(item.get("text", "")).lower():
                target = item
                break
    if target is None:
        return f"Could not find a pending to-do matching '{selector.strip()}'."
    target["done"] = True
    target["completed_at"] = datetime.datetime.now().isoformat(timespec="minutes")
    _save_notes_store(store, base_dir)
    return f'✅ Completed to-do: "{target.get('text', '')}".'


def add_note(note_text: str, *, dry_run: bool = False, base_dir: Path | None = None) -> str:
    clean = note_text.strip()
    if not clean:
        return "Tell me what to save (e.g., 'take a note: call dentist at 4pm')."
    if dry_run:
        return f"🧪 Dry-Run [FilesNotesAgent]: Would save quick note '{clean[:60]}'."
    store = _load_notes_store(base_dir)
    store["notes"].append({
        "text": clean,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    _save_notes_store(store, base_dir)
    return f'📝 Saved note ({len(store['notes'])} total): "{clean}"'


def list_notes(*, base_dir: Path | None = None) -> str:
    store = _load_notes_store(base_dir)
    notes = store["notes"]
    if not notes:
        return "📝 You have no saved notes yet. Say 'take a note: <text>' to save one."
    lines = [f"📝 Saved Notes (latest {min(10, len(notes))} of {len(notes)}):"]
    for idx, n in enumerate(notes[-10:], 1):
        lines.append(f"  {idx}. [{n.get('created_at', '')}] {n.get('text', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smart Downloads Organizer (Approval-Gated, Collision-Safe, Zero Overwrite)
# ---------------------------------------------------------------------------
def _plan_downloads_organization(downloads_dir: Path) -> list[tuple[Path, Path]]:
    if not downloads_dir.exists() or not downloads_dir.is_dir():
        return []
    plan: list[tuple[Path, Path]] = []
    for entry in sorted(downloads_dir.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        ext = entry.suffix.lower()
        cat = "Other"
        for cname, exts in DOWNLOAD_CATEGORIES.items():
            if ext in exts:
                cat = cname
                break
        dest_dir = downloads_dir / cat
        dest = dest_dir / entry.name
        if dest.exists():
            stem = entry.stem
            dest = dest_dir / f"{stem}_{int(time.time())}{entry.suffix}"
        plan.append((entry, dest))
    return plan


def organize_downloads(
    *,
    downloads_dir: Path | None = None,
    approval_cb: ApprovalCallback | None = None,
    dry_run: bool = False,
) -> str:
    dl = downloads_dir or (Path.home() / "Downloads")
    plan = _plan_downloads_organization(dl)
    if not plan:
        return f"📁 {dl} is already clean (no loose top-level files to sort)."

    counts: dict[str, int] = {}
    for _src, dst in plan:
        cat = dst.parent.name
        counts[cat] = counts.get(cat, 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))

    if dry_run:
        return f"🧪 Dry-Run [FilesNotesAgent]: Would ask approval to sort {len(plan)} file(s) in {dl} ({summary})."
    if approval_cb is None:
        return f"Organizing {len(plan)} file(s) in {dl} ({summary}) requires interactive confirmation."

    args = {"directory": str(dl), "file_count": len(plan), "categories": counts}
    token, deny_err = approval.request_bound_approval(
        "move_file",
        args,
        approval_cb,
        title=f"Organize {len(plan)} file(s) in Downloads?",
        target=f"{dl} ({summary})",
    )
    if deny_err:
        return deny_err
    ok, err = approval.verify_and_consume(token, "move_file", args)
    if not ok:
        return err

    moved = 0
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1
    return f"✅ Organized {moved} file(s) in {dl} into subfolders ({summary})."


# ---------------------------------------------------------------------------
# 3-Stage Full-Home "Where Is My File?" Locator (Stage A Filename + Stage B Content + Stage C Rich Receipt)
# ---------------------------------------------------------------------------
_SKIP_LOCATE_DIRS: frozenset[str] = frozenset({
    ".cache", ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".npm", ".cargo", ".rustup", ".local", ".mozilla", ".config", "snap", "Trash",
})
_FILE_QUERY_HINTS: tuple[str, ...] = (
    "file", "folder", "doc", "docx", "pdf", "xlsx", "pptx", "csv", "txt", "md",
    "py", "sh", "mp4", "mp3", "mkv", "zip", "tar", "gz", "jpg", "jpeg", "png",
    "resume", "report", "notes", "script", "code", "project", "backup", "invoice",
    "presentation", "spreadsheet", "photo", "video", "song", "archive", "download",
    "my ",
)


def _format_human_size(num_bytes: int) -> str:
    val = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024.0 or unit == "GB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{num_bytes} B"


def _format_rel_mtime(mtime: float) -> str:
    if mtime <= 0:
        return "unknown"
    delta = max(0.0, time.time() - mtime)
    if delta < 3600:
        return f"{max(1, int(delta // 60))} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} hr ago"
    days = int(delta // 86400)
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def locate_files_smart(
    query: str,
    *,
    base_dir: Path | None = None,
    include_content: bool = True,
    limit: int = 8,
    reveal_first: bool = False,
    dry_run: bool = False,
) -> str:
    """3-Stage Full-Home File Locator Pipeline:
    Stage A: Exact & fuzzy filename scan across ALL extensions up to depth 8 (via fd/fdfind or bounded os.walk).
    Stage B: Content search via ripgrep (`rg -i --files-with-matches`) + `spotlight_index` FTS5 if < 3 hits.
    Stage C: Rich location receipt (~/relative path, folder breadcrumb, file size, modified age) + optional Nautilus reveal.
    """
    raw_q = query.strip().strip("\"'?.!")
    # Strip natural filler like "the file about ", "my ", "file called ", "file named "
    clean_q = re.sub(
        r"^(?:the\s+)?(?:my\s+)?(?:file|document|doc|folder)\s+(?:about|called|named|for|with|containing)\s+",
        "",
        raw_q,
        flags=re.IGNORECASE,
    ).strip()
    clean_q = re.sub(r"\s+(?:file|document|folder)$", "", clean_q, flags=re.IGNORECASE).strip() or raw_q

    root = (base_dir or Path.home()).resolve()
    if dry_run:
        act = "locate & reveal in Files" if reveal_first else "locate (filename + content)"
        return f"🧪 Dry-Run [FilesNotesAgent]: Would {act} '{clean_q}' across {root} (depth 8, all extensions + content search)."

    hits: dict[Path, str] = {}
    q_low = clean_q.lower()
    q_tokens = [tok for tok in re.split(r"[\s_\-]+", q_low) if len(tok) >= 2]

    # Stage A: Fast full-home filename scan across ALL extensions (depth <= 8)
    fd_bin = shutil.which("fdfind") or shutil.which("fd")
    if fd_bin and base_dir is None:
        cmd = [
            fd_bin, "--type", "f", "--max-depth", "8", "--max-results", str(limit * 3), "--ignore-case",
            "--exclude", ".cache", "--exclude", "node_modules", "--exclude", ".venv",
            "--exclude", ".git", "--exclude", "__pycache__", "--exclude", ".local",
            clean_q, str(root),
        ]
        res = command_runner.run_command(cmd, timeout=5.0)
        if res.ok and res.stdout:
            for ln in res.stdout.splitlines():
                p = Path(ln.strip())
                if p.is_file() and not tools._is_sensitive_path(p):
                    hits[p] = "filename"

    if len(hits) < limit:
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_LOCATE_DIRS]
            try:
                depth = len(Path(dirpath).relative_to(root).parts)
            except ValueError:
                depth = 9
            if depth > 8:
                dirnames[:] = []
                continue
            for fn in filenames:
                scanned += 1
                if scanned > 12000:
                    break
                fn_low = fn.lower()
                if q_low in fn_low or (q_tokens and all(t in fn_low for t in q_tokens)):
                    p = Path(dirpath) / fn
                    if not tools._is_sensitive_path(p):
                        hits.setdefault(p, "filename")
                        if len(hits) >= limit * 2:
                            break
            if scanned > 12000 or len(hits) >= limit * 2:
                break

    # Stage B: Content search via ripgrep (`rg`) & spotlight_index FTS5 if filename hits < 3
    if include_content and len(hits) < 3 and len(clean_q) >= 3:
        rg_bin = shutil.which("rg")
        if rg_bin:
            rg_cmd = [
                rg_bin, "--files-with-matches", "--ignore-case", "--max-depth", "8", "--max-filesize", "5M",
                "--glob", "!.cache/**", "--glob", "!node_modules/**", "--glob", "!.venv/**",
                "--glob", "!.git/**", "--glob", "!.local/**",
                clean_q, str(root),
            ]
            rg_res = command_runner.run_command(rg_cmd, timeout=5.0)
            if rg_res.stdout:
                for ln in rg_res.stdout.splitlines()[:limit]:
                    p = Path(ln.strip())
                    if p.is_file() and not tools._is_sensitive_path(p):
                        hits.setdefault(p, "content")

        # Pure-Python text content fallback for small readable text/code files if rg missing
        if not rg_bin and len(hits) < 3:
            text_exts = {".txt", ".md", ".py", ".sh", ".json", ".yaml", ".yml", ".csv", ".log", ".ini", ".cfg", ".html"}
            checked = 0
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_LOCATE_DIRS]
                if len(Path(dirpath).relative_to(root).parts) > 8:
                    dirnames[:] = []
                    continue
                for fn in filenames:
                    p = Path(dirpath) / fn
                    if p.suffix.lower() not in text_exts or p in hits:
                        continue
                    checked += 1
                    if checked > 400:
                        break
                    try:
                        if p.stat().st_size <= 512_000 and not tools._is_sensitive_path(p):
                            if q_low in p.read_text(encoding="utf-8", errors="ignore").lower():
                                hits.setdefault(p, "content")
                    except OSError:
                        continue
                if checked > 400 or len(hits) >= limit:
                    break

    if not hits:
        return f"🔍 No files matching '{clean_q}' found anywhere in {root} (checked filenames up to depth 8 & file contents)."

    # Stage C: Rich Location Receipt sorted by filename match first, then newest mtime
    scored: list[tuple[int, float, Path, str]] = []
    for p, kind in hits.items():
        try:
            st = p.stat()
            mtime, sz = st.st_mtime, st.st_size
        except OSError:
            mtime, sz = 0.0, 0
        pri = 0 if kind == "filename" else 1
        scored.append((pri, -mtime, p, f"{kind}|{_format_human_size(sz)}|{_format_rel_mtime(mtime)}"))
    scored.sort(key=lambda x: (x[0], x[1]))
    top = scored[:limit]

    home_resolved = Path.home().resolve()
    lines = [f"📍 Found {len(top)} match(es) for '{clean_q}':"]
    for idx, (_pri, _nm, p, meta) in enumerate(top, 1):
        kind, sz_s, age_s = meta.split("|")
        try:
            disp = f"~/{p.relative_to(home_resolved)}"
        except ValueError:
            disp = str(p)
        lines.append(f"  {idx}. {disp}  ({sz_s} · modified {age_s} · match: {kind})")

    first_path = top[0][2]
    if reveal_first:
        if shutil.which("nautilus") and tools.launch_detached(["nautilus", "--select", str(first_path)]):
            lines.append(f"📂 Revealed in Files: {first_path.parent}")
        elif tools.launch_detached(["xdg-open", str(first_path.parent)]):
            lines.append(f"📂 Opened parent folder: {first_path.parent}")
    else:
        lines.append(f"💡 Say 'reveal {clean_q} in folder' to highlight it in Files.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quick System Helpers (Disk / IP / Internet / Battery)
# ---------------------------------------------------------------------------
def _quick_disk_space() -> str:
    usage = shutil.disk_usage(str(Path.home()))
    tot = usage.total / (1024 ** 3)
    used = usage.used / (1024 ** 3)
    free = usage.free / (1024 ** 3)
    pct = (usage.used / max(1, usage.total)) * 100.0
    return f"💾 Disk Space ({Path.home()}): {free:.1f} GB free / {tot:.1f} GB total ({used:.1f} GB used · {pct:.0f}%)."


def _quick_ip_and_network() -> str:
    local_ip = "unknown"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(1.5)
            s.connect(("1.1.1.1", 80))
            local_ip = s.getsockname()[0]
    except OSError:
        local_ip = "offline / no default route"
    online = local_ip not in {"unknown", "offline / no default route"}
    wifi_stat = tools.control_connectivity("wifi", "status")
    return f"🌐 Network: {'Online' if online else 'Offline'} · Local IP: {local_ip} · {wifi_stat}"


def set_desktop_theme(mode: str, *, dry_run: bool = False) -> str:
    """Switches GNOME desktop Dark / Light mode (color-scheme + paired WhiteSur GTK theme if installed)."""
    want_dark = str(mode or "").strip().lower() == "dark"
    scheme = "prefer-dark" if want_dark else "prefer-light"
    label = "Dark Mode" if want_dark else "Light Mode"
    icon = "🌙" if want_dark else "☀️"
    if dry_run:
        return f"🧪 Dry-Run [SystemAgent]: Would switch desktop to {label} (color-scheme={scheme})."
    if not shutil.which("gsettings"):
        return f"Cannot switch {label}: `gsettings` not found on PATH."
    r = command_runner.run_command(
        ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", scheme],
        timeout=4.0,
    )
    if not r.ok:
        return f"Failed to switch {label}: {(r.stderr or r.stdout or 'gsettings error').strip()}"

    # Also flip paired GTK3 theme (e.g. WhiteSur-Light <-> WhiteSur-Dark) if installed
    gtk_note = ""
    cur_r = command_runner.run_command(
        ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
        timeout=3.0,
    )
    if cur_r.ok and cur_r.stdout:
        cur_gtk = cur_r.stdout.strip().strip("'\"")
        cand_gtk = ""
        if want_dark and "-Light" in cur_gtk:
            cand_gtk = cur_gtk.replace("-Light", "-Dark", 1)
        elif want_dark and "-light" in cur_gtk:
            cand_gtk = cur_gtk.replace("-light", "-dark", 1)
        elif (not want_dark) and "-Dark" in cur_gtk:
            cand_gtk = cur_gtk.replace("-Dark", "-Light", 1)
        elif (not want_dark) and "-dark" in cur_gtk:
            cand_gtk = cur_gtk.replace("-dark", "-light", 1)
        if cand_gtk and cand_gtk != cur_gtk:
            theme_dirs = (
                Path.home() / ".themes" / cand_gtk,
                Path.home() / ".local/share/themes" / cand_gtk,
                Path("/usr/share/themes") / cand_gtk,
            )
            if any(p.is_dir() for p in theme_dirs):
                if command_runner.run_command(
                    ["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", cand_gtk],
                    timeout=3.0,
                ).ok:
                    gtk_note = f", gtk-theme={cand_gtk}"

    return f"{icon} Switched desktop to {label} (color-scheme={scheme}{gtk_note})."


def set_night_light(enabled: bool, *, dry_run: bool = False) -> str:
    state = "true" if enabled else "false"
    label = "ON" if enabled else "OFF"
    if dry_run:
        return f"🧪 Dry-Run [SystemAgent]: Would turn Night Light {label}."
    if not shutil.which("gsettings"):
        return "Cannot toggle Night Light: `gsettings` not found."
    r = command_runner.run_command(
        ["gsettings", "set", "org.gnome.settings-daemon.plugins.color", "night-light-enabled", state],
        timeout=4.0,
    )
    if not r.ok:
        return f"Failed to turn Night Light {label}: {(r.stderr or r.stdout).strip()}"
    return f"🌇 Night Light turned {label}."


def set_dnd_mode(enabled: bool, *, dry_run: bool = False) -> str:
    # show-banners=false means Do Not Disturb is ON
    show_banners = "false" if enabled else "true"
    label = "ON (notifications muted)" if enabled else "OFF (notifications enabled)"
    if dry_run:
        return f"🧪 Dry-Run [SystemAgent]: Would turn Do Not Disturb {label}."
    if not shutil.which("gsettings"):
        return "Cannot toggle Do Not Disturb: `gsettings` not found."
    r = command_runner.run_command(
        ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", show_banners],
        timeout=4.0,
    )
    if not r.ok:
        return f"Failed to toggle Do Not Disturb: {(r.stderr or r.stdout).strip()}"
    return f"🔕 Do Not Disturb is now {label}."


def _parse_duration_seconds(amount_str: str, unit_str: str) -> int:
    val = max(1, int(amount_str))
    u = unit_str.lower()
    if u.startswith("h"):
        return min(val * 3600, 86400)
    if u.startswith("m"):
        return min(val * 60, 86400)
    return min(val, 86400)


# ---------------------------------------------------------------------------
# Daily Routines (Good Morning / Good Night / Study / Chill)
# ---------------------------------------------------------------------------
def run_daily_routine(routine: str, *, dry_run: bool = False) -> str:
    now_str = datetime.datetime.now().strftime("%A, %d %b %Y · %I:%M %p")
    todos_summary = list_todos()

    if routine == "good_morning":
        if dry_run:
            return "🧪 Dry-Run [RoutineAgent]: Would run Good Morning routine (Focus->default, Brightness->70%, unmute, time + battery + to-dos)."
        os_events.set_focus_mode("default")
        tools.set_brightness(70)
        tools.set_volume(mute=False)
        stats = tools.get_system_stats()
        first_stat = stats.splitlines()[0] if stats else ""
        return (
            f"☀️ Good Morning! It's {now_str}.\n"
            f"• Focus Mode: Normal (DND off) · Brightness: 70% · Audio unmuted\n"
            f"• System: {first_stat}\n"
            f"{todos_summary}"
        )

    if routine == "good_night":
        if dry_run:
            return "🧪 Dry-Run [RoutineAgent]: Would run Good Night routine (Focus->sleep, pause media, Brightness->20%, Volume->15%, battery + to-dos)."
        os_events.set_focus_mode("sleep")
        tools.control_media("pause")
        tools.set_brightness(20)
        tools.set_volume(level=15)
        stats = tools.get_system_stats()
        first_stat = stats.splitlines()[0] if stats else ""
        return (
            f"🌙 Good Night! ({now_str})\n"
            f"• Focus Mode: Sleep (DND + Night Light on) · Media paused · Brightness: 20% · Volume: 15%\n"
            f"• System: {first_stat}\n"
            f"{todos_summary}\n"
            f"• Sweet dreams! Say 'good morning' tomorrow to reset."
        )

    if routine == "study_mode":
        if dry_run:
            return "🧪 Dry-Run [RoutineAgent]: Would activate Study/Work Focus Mode (DND on) and display pending to-dos."
        os_events.set_focus_mode("work")
        return f"🎯 Study / Work Mode active (Do Not Disturb ON).\n{todos_summary}"

    if routine == "chill_mode":
        if dry_run:
            return "🧪 Dry-Run [RoutineAgent]: Would activate Chill Mode (Focus->default, Volume->50%)."
        os_events.set_focus_mode("default")
        tools.set_volume(level=50)
        return "🎧 Chill Mode active (DND off · Volume 50%). Say 'play lofi beats on youtube' or 'play telugu songs on spotify' anytime!"

    return ""


# ---------------------------------------------------------------------------
# Persistent Teaching / Learning & Zero-Login Local MCP Enabler
# ---------------------------------------------------------------------------
def _compile_taught_action_steps(action_text: str) -> list[dict[str, Any]]:
    """Converts a natural taught action phrase into real runnable Celina skill steps."""
    act = action_text.strip().rstrip(".!?")
    low = act.lower()

    m_open = re.fullmatch(r"(?:open|launch|go\s+to)\s+(.+)", low)
    if m_open:
        return [{"tool": "open_application", "args": {"app_name": m_open.group(1).strip()}}]

    m_play = re.search(r"\b(?:play|watch|listen\s+to)\s+(.+)$", low)
    if m_play:
        q, plat = tools._clean_media_query(m_play.group(1))
        chosen = plat or "youtube"
        if chosen in {"youtube", "yt"}:
            url = tools.SITE_SEARCH_URLS["youtube"].format(q=quote(q)) if q else "https://www.youtube.com"
        else:
            tmpl = tools.SITE_SEARCH_URLS.get(chosen, tools.SITE_SEARCH_URLS["spotify"])
            url = tmpl.format(q=quote(q)) if q else tools.WEB_APPS.get(chosen, "https://open.spotify.com")
        return [{"tool": "open_application", "args": {"app_name": url}}]

    m_vol = re.fullmatch(r"(?:set\s+)?volume(?:\s+to)?\s+(\d{1,3})%?", low)
    if m_vol:
        return [{"tool": "set_volume", "args": {"level": int(m_vol.group(1))}}]

    m_focus = re.fullmatch(r"(?:enable|set|switch\s+to)?\s*(work|coding|sleep|presentation|default)\s+(?:focus\s*)?mode", low)
    if m_focus:
        return [{"tool": "set_focus_mode", "args": {"mode": m_focus.group(1)}}]

    return [{"tool": "open_application", "args": {"app_name": act}}]


def teach_custom_skill(
    trigger_phrase: str,
    action_phrase: str,
    *,
    db: memory.MemoryDB | None = None,
    dry_run: bool = False,
) -> str:
    trig = trigger_phrase.strip().strip("\"'").lower()
    act = action_phrase.strip().strip("\"'")
    if not trig or not act:
        return "Format: 'when I say <trigger>, <action>' (e.g., 'when I say telugu vibe, play dude telugu songs on youtube')."
    steps = _compile_taught_action_steps(act)
    slug = re.sub(r"[^a-z0-9]+", "_", trig).strip("_")[:36] or "custom_skill"
    if dry_run:
        return f"🧪 Dry-Run [LearningAgent]: Would save learned skill '{slug}' (trigger='{trig}' -> {steps[0]['tool']}({steps[0]['args']})) into memory.db & Knowledge Graph."
    mdb = db or memory.get_db()
    mdb.save_skill(
        name=slug,
        title=trig.title(),
        description=f"User-taught skill: when user says '{trig}' -> {act}",
        triggers=[trig],
        steps=steps,
    )
    mdb.add_knowledge_triple(trig, "triggers_skill", act)
    return (
        f"🧠 Learned & saved permanently to memory.db!\n"
        f"• Skill: `{slug}` (visible in `/skills` & `/kg`)\n"
        f'• Trigger: say **"{trig}"** anytime → runs `{steps[0]['tool']}({steps[0]['args']})`.'
    )


def remember_user_fact(
    fact_text: str,
    *,
    db: memory.MemoryDB | None = None,
    dry_run: bool = False,
) -> str:
    clean = fact_text.strip().rstrip(".")
    low = clean.lower()

    # Special structured preference: favorite music platform ("my favorite music app is spotify")
    m_pref = re.search(
        r"(?:favorite|preferred|default)\s+(?:music|song|songs|audio|streaming)\s+(?:app|player|platform|service)\s+(?:is\s+)?([a-z ]+)|(?:always\s+use|prefer)\s+(spotify|youtube|youtube\s+music|jiosaavn|gaana|soundcloud)\s+(?:for\s+)?(?:music|songs)",
        low,
    )
    if m_pref:
        plat = (m_pref.group(1) or m_pref.group(2) or "").strip()
        if plat in tools.MEDIA_PLATFORMS:
            if dry_run:
                return f"🧪 Dry-Run [LearningAgent]: Would save preference favorite_music_app='{plat}' into memory.db facts & Knowledge Graph."
            mdb = db or memory.get_db()
            mdb.set_fact("favorite_music_app", plat)
            mdb.add_knowledge_triple("user", "favorite_music_app", plat)
            return f"🧠 Saved permanently: your default music app is **{plat}** (saved in `/facts` & `/kg`). Future 'play <song>' requests will use {plat}!"

    key = f"learned_{hashlib.sha1(low.encode('utf-8'), usedforsecurity=False).hexdigest()[:8]}"
    if dry_run:
        return f"🧪 Dry-Run [LearningAgent]: Would save fact '{clean}' into memory.db facts & Knowledge Graph."
    mdb = db or memory.get_db()
    mdb.set_fact(key, clean)
    mdb.add_knowledge_triple("user", "remembered", clean)
    return f'🧠 Saved permanently to memory & Knowledge Graph (`/facts` & `/kg`): "{clean}"'


def enable_local_zero_login_mcp(*, mcp_path: Path | None = None, dry_run: bool = False) -> str:
    """Simulate enabling local MCP servers; live config changes require a broker."""
    if not dry_run:
        return "Denied: enabling MCP servers requires a trusted approval broker, which is not available."
    cfg_path = mcp_path or config.MCP_CONFIG_FILE
    if not cfg_path.exists():
        return f"MCP config not found at {cfg_path}."
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"Could not read {cfg_path}: {exc}"

    runtime_checks = {
        "swadb-memory": shutil.which("celina-memory-mcp") or (str(Path.home() / ".local/bin/celina-memory-mcp") if (Path.home() / ".local/bin/celina-memory-mcp").exists() else None),
        "playwright": shutil.which("npx"),
        "git": shutil.which("uvx"),
        "sqlite": shutil.which("uvx"),
        "time": shutil.which("uvx"),
    }
    enabled_now: list[str] = []
    missing_rt: list[str] = []

    for section in ("servers", "mcpServers"):
        sec = data.get(section)
        if not isinstance(sec, dict):
            continue
        for name, rt in runtime_checks.items():
            if name in sec and isinstance(sec[name], dict):
                if rt:
                    sec[name]["enabled"] = True
                    enabled_now.append(name)
                else:
                    missing_rt.append(name)

    if dry_run:
        return f"🧪 Dry-Run [SystemAgent]: Would enable zero-login local MCP servers: {enabled_now or ['none']} (missing runtimes: {missing_rt})."
    if enabled_now:
        config.atomic_write_private_text(cfg_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    msg = f"🔌 Enabled {len(enabled_now)} zero-login local MCP server(s) in {cfg_path}: {', '.join(enabled_now) if enabled_now else 'none'}."
    if missing_rt:
        msg += f"\n• Skipped (runtime not installed yet): {', '.join(missing_rt)} (install `nodejs/npm` for Playwright or `uv` for uvx)."
    return msg


# ---------------------------------------------------------------------------
# Master 6-Agent Everyday Dispatcher
# Returns (agent_name, reply_text) if matched, else None
# ---------------------------------------------------------------------------
def dispatch_everyday_task(
    text: str,
    *,
    approval_cb: ApprovalCallback | None = None,
    status_cb: StatusCallback | None = None,
    dry_run: bool = False,
    db: memory.MemoryDB | None = None,
) -> tuple[str, str] | None:
    """Route everyday requests in simulation only while no trusted broker exists.

    For live requests, return None without executing an everyday handler. This
    only disables this route: callers may still try other routing paths. A
    dry-run is not an OS isolation boundary and may inspect local state.
    """
    if not dry_run:
        return None
    clean = (text or "").strip()
    if not clean:
        return None
    low = clean.lower().strip().rstrip(".!?")

    # Restraint guard: never hijack conceptual Q&A except explicit status questions & 'when i say...' skill teaching
    if (
        _INFO_QUESTION_RE.match(low)
        and not low.startswith("when i say ")
        and not any(
            k in low
            for k in (
                "what time", "what is the time", "what is my ip", "whats my ip", "what's my ip",
                "what is on my screen", "what's on my screen", "whats on my screen",
                "what are my tasks", "what are my todos", "what apps are open", "what's playing", "whats playing",
                "where is ", "what is the weather", "whats the weather", "what's the weather",
            )
        )
    ):
        return None

    # ── AGENT 5: RoutineAgent (Daily Routines + Natural Timers / Reminders) ──
    if low in {"good morning", "good morning celina", "morning routine", "start my day"}:
        return "RoutineAgent", run_daily_routine("good_morning", dry_run=dry_run)
    if low in {"good night", "good night celina", "goodnight", "night routine", "bedtime mode", "sleep routine"}:
        return "RoutineAgent", run_daily_routine("good_night", dry_run=dry_run)
    if low in {"study mode", "start study mode", "enable study mode", "focus time"}:
        return "RoutineAgent", run_daily_routine("study_mode", dry_run=dry_run)
    if low in {"chill mode", "relax mode", "break time"}:
        return "RoutineAgent", run_daily_routine("chill_mode", dry_run=dry_run)

    m_rem = re.fullmatch(
        r"(?:please\s+)?remind\s+me\s+in\s+(\d{1,4})\s*(sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours)\s+(?:to\s+)?(.+)",
        low,
    )
    if m_rem:
        secs = _parse_duration_seconds(m_rem.group(1), m_rem.group(2))
        msg = m_rem.group(3).strip()
        if dry_run:
            return "RoutineAgent", f"🧪 Dry-Run [RoutineAgent]: Would set reminder in {secs}s: '{msg}'."
        return "RoutineAgent", tools.set_timer(secs, msg)

    m_timer = re.fullmatch(
        r"(?:please\s+)?(?:set\s+(?:a\s+)?)?timer\s+(?:for\s+)?(\d{1,4})\s*(sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours)(?:\s+(?:for|to)\s+(.+))?",
        low,
    )
    if m_timer:
        secs = _parse_duration_seconds(m_timer.group(1), m_timer.group(2))
        msg = (m_timer.group(3) or "Timer finished!").strip()
        if dry_run:
            return "RoutineAgent", f"🧪 Dry-Run [RoutineAgent]: Would set {secs}s timer ('{msg}')."
        return "RoutineAgent", tools.set_timer(secs, msg)

    # ── AGENT 3: FilesNotesAgent (Notes, To-Dos, Folders, Downloads Organizer, Find File) ──
    m_todo_add = re.fullmatch(
        r"(?:please\s+)?(?:add\s+(?:to\s+(?:my\s+)?)?(?:todo|to-do)(?:\s+list)?|todo\s*:?)\s+(.+)",
        low,
    )
    if m_todo_add:
        orig_tail = clean[len(clean) - len(m_todo_add.group(1)) :].strip()
        return "FilesNotesAgent", add_todo(orig_tail or m_todo_add.group(1), dry_run=dry_run)

    if low in {"show my todos", "show todos", "my todo list", "todo list", "to-do list", "what are my tasks", "what are my todos", "list todos"}:
        return "FilesNotesAgent", list_todos()

    m_todo_done = re.fullmatch(r"(?:complete|finish|done|check off)\s+(?:todo|to-do|task)\s+(.+)", low)
    if m_todo_done:
        return "FilesNotesAgent", complete_todo(m_todo_done.group(1), dry_run=dry_run)

    m_note_add = re.fullmatch(r"(?:please\s+)?(?:take\s+a\s+note|save\s+(?:a\s+)?note|add\s+(?:a\s+)?note|note)\s*:?\s+(.+)", low)
    if m_note_add:
        return "FilesNotesAgent", add_note(m_note_add.group(1), dry_run=dry_run)

    if low in {"show my notes", "read my notes", "my notes", "list notes"}:
        return "FilesNotesAgent", list_notes()

    if low in {"organize downloads", "organize my downloads", "clean downloads", "clean my downloads", "clean my downloads folder", "sort my downloads"}:
        return "FilesNotesAgent", organize_downloads(approval_cb=approval_cb, dry_run=dry_run)

    m_mkdir = re.fullmatch(
        r"(?:please\s+)?(?:create|make)\s+(?:a\s+)?(?:new\s+)?folder\s+(?:called\s+|named\s+)?([a-z0-9][a-z0-9 _.-]{0,50}?)(?:\s+(?:on|in)\s+(desktop|downloads|documents|pictures|videos|home))?",
        low,
    )
    if m_mkdir:
        fname = m_mkdir.group(1).strip()
        loc = (m_mkdir.group(2) or "desktop").strip()
        if _SAFE_FOLDER_NAME_RE.fullmatch(fname) and ".." not in fname:
            parent_rel = COMMON_FOLDERS.get(loc, "Desktop")
            target_dir = (Path.home() / parent_rel / fname) if parent_rel else (Path.home() / fname)
            if dry_run:
                return "FilesNotesAgent", f"🧪 Dry-Run [FilesNotesAgent]: Would create folder {target_dir}."
            target_dir.mkdir(parents=True, exist_ok=True)
            return f"📁 Created folder: {target_dir}"

    m_open_folder = re.fullmatch(r"(?:please\s+)?open\s+(?:my\s+)?(downloads|download|downloads folder|desktop|desktop folder|documents|documents folder|pictures|photos|videos|music folder|home|home folder)", low)
    if m_open_folder:
        rel = COMMON_FOLDERS[m_open_folder.group(1)]
        target = (Path.home() / rel) if rel else Path.home()
        if dry_run:
            return "FilesNotesAgent", f"🧪 Dry-Run [FilesNotesAgent]: Would open folder {target}."
        if tools.launch_detached(["xdg-open", str(target)]):
            return f"📁 Opened folder: {target}"
        return f"Could not open folder {target}."

    m_find = re.fullmatch(
        r"(?:please\s+)?(?:(?:find|locate)\s+(?:my\s+)?(?:file\s+)?|where\s+is\s+(?:the\s+|my\s+)?)(.+)",
        low,
    )
    if m_find:
        raw_target = m_find.group(1).strip()
        is_explicit_find = low.startswith(("find ", "please find ", "locate ", "please locate "))
        looks_like_file = is_explicit_find or any(h in f" {low} " for h in _FILE_QUERY_HINTS) or "." in raw_target
        if raw_target and looks_like_file:
            return "FilesNotesAgent", locate_files_smart(raw_target, dry_run=dry_run)

    m_reveal = re.fullmatch(
        r"(?:please\s+)?(?:reveal|show)\s+(?:my\s+)?(.+?)\s+in\s+(?:folder|files|nautilus|file\s+manager)",
        low,
    )
    if m_reveal:
        return "FilesNotesAgent", locate_files_smart(m_reveal.group(1).strip(), reveal_first=True, dry_run=dry_run)

    # ── AGENT 2b: Zero-LLM-Exposure GNOME Keyring Auto-Login ("log me into spotify", "auto login github") ──
    m_klogin = re.fullmatch(
        r"(?:please\s+)?(?:log\s+me\s+(?:in|into)|sign\s+me\s+(?:in|into)|auto[\s-]*login(?:\s+(?:to|in|into))?|fill\s+saved\s+login(?:\s+for)?)\s+([a-z0-9][a-z0-9 ._+-]{1,35}?)(?:\s+(?:account|web|website|app|with\s+keychain))?",
        low,
    )
    if m_klogin:
        return "WebAgent", keychain_login.fill_saved_login(m_klogin.group(1).strip(), approval_cb=approval_cb, dry_run=dry_run)

    # ── AGENT 5b: LearningAgent (Teach Custom Skills, Preferences, Facts & Zero-Login Local MCP) ──
    m_teach = re.fullmatch(
        r"(?:please\s+)?(?:teach(?:\s+me|\s+celina)?\s*:?\s*)?when\s+i\s+say\s+(.+?)(?:\s*,\s*|\s*->\s*|\s+then\s+|\s+do\s+)(.+)",
        low,
    )
    if m_teach:
        return "LearningAgent", teach_custom_skill(m_teach.group(1), m_teach.group(2), db=db, dry_run=dry_run)

    m_rem_fact = re.fullmatch(
        r"(?:please\s+)?(?:remember|learn)\s+(?:that\s+)?(.+)",
        low,
    )
    if m_rem_fact and not low.startswith("remind me"):
        orig_tail = clean[len(clean) - len(m_rem_fact.group(1)) :].strip()
        return "LearningAgent", remember_user_fact(orig_tail or m_rem_fact.group(1), db=db, dry_run=dry_run)

    if re.search(r"\b(?:always\s+use|prefer)\s+(?:spotify|youtube|youtube\s+music|jiosaavn|gaana|soundcloud)\s+(?:for\s+)?(?:music|songs)\b", low):
        return "LearningAgent", remember_user_fact(clean, db=db, dry_run=dry_run)

    if low in {"enable local mcp", "connect local mcp", "turn on local mcp", "enable mcp"}:
        return "SystemAgent", enable_local_zero_login_mcp(dry_run=dry_run)

    # ── AGENT 1: MediaAgent (extra media, autoplay fix, brightness & relative volume controls) ──
    if re.fullmatch(
        r"(?:please\s+)?(?:fix|enable|allow)\s+(?:youtube\s+|browser\s+|firefox\s+)?autoplay",
        low,
    ):
        return "MediaAgent", turbo_mode.configure_firefox_autoplay(enable=True, dry_run=dry_run)
    if low in {"play now", "play it", "hit play", "press play", "start playing", "play", "resume music", "resume video", "resume spotify", "continue playing"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would trigger MPRIS Play/Resume on active media player."
        return "MediaAgent", f"▶️ {tools.control_media('play')}"
    if low in {"pause video", "pause spotify", "pause youtube", "stop playing"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would pause active media."
        return "MediaAgent", tools.control_media("pause")
    if low in {"stop music", "stop media"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would stop active media."
        return "MediaAgent", tools.control_media("stop")
    if low in {"brightness up", "bright up", "brighter", "increase brightness", "inc brightness", "screen brighter"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would increase screen brightness to 85%."
        return "MediaAgent", tools.set_brightness(85)
    if low in {"brightness down", "bright down", "dimmer", "decrease brightness", "dec brightness", "reduce brightness", "dim screen", "low brightness", "min brightness", "brightness low"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would lower screen brightness to 35%."
        return "MediaAgent", tools.set_brightness(35)
    if low in {"max brightness", "full brightness", "brightness max", "brightness full"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would set brightness to 100%."
        return "MediaAgent", tools.set_brightness(100)
    m_bri_num = re.fullmatch(r"(?:please\s+)?(?:set\s+|change\s+)?(?:brightness|bright)\s+(?:to\s+)?(\d{1,3})%?", low)
    if m_bri_num:
        lvl = max(5, min(100, int(m_bri_num.group(1))))
        if dry_run:
            return "MediaAgent", f"🧪 Dry-Run [MediaAgent]: Would set screen brightness to {lvl}%."
        return "MediaAgent", tools.set_brightness(lvl)
    if low in {"vol up", "inc volume", "inc vol", "increase vol", "vol +", "volume +"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would increase volume by +10%."
        return "MediaAgent", tools.set_volume(delta=10)
    if low in {"vol down", "dec volume", "dec vol", "decrease vol", "reduce volume", "lower volume", "vol -", "volume -"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would decrease volume by -10%."
        return "MediaAgent", tools.set_volume(delta=-10)
    if low in {"max volume", "full volume", "volume max", "volume full"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would set volume to 100%."
        return "MediaAgent", tools.set_volume(level=100)
    if low in {"min volume", "low volume", "volume low"}:
        if dry_run:
            return "MediaAgent", "🧪 Dry-Run [MediaAgent]: Would set volume to 20%."
        return "MediaAgent", tools.set_volume(level=20)
    m_vol_num = re.fullmatch(r"(?:please\s+)?(?:set\s+|change\s+)?vol(?:ume)?\s+(?:to\s+)?(\d{1,3})%?", low)
    if m_vol_num:
        lvl = max(0, min(150, int(m_vol_num.group(1))))
        if dry_run:
            return "MediaAgent", f"🧪 Dry-Run [MediaAgent]: Would set volume to {lvl}%."
        return "MediaAgent", tools.set_volume(level=lvl)

    # ── AGENT 2: WebAgent (Maps Directions, Translate, Quick Weather) ──
    m_maps = re.fullmatch(r"(?:directions\s+to|navigate\s+to|route\s+to|where\s+is)\s+(.+)", low)
    if m_maps:
        place = m_maps.group(1).strip()
        if place and len(place) <= 80:
            url = f"https://www.google.com/maps/search/{quote(place)}"
            if dry_run:
                return "WebAgent", f"🧪 Dry-Run [WebAgent]: Would open Google Maps for '{place}' ({url})."
            return "WebAgent", tools.open_url_safe(url)

    m_tr = re.fullmatch(
        r"(?:please\s+)?translate\s+(.+?)\s+(?:to|into|in)\s+([a-z]+)",
        low,
    )
    if m_tr and m_tr.group(2) in TRANSLATE_LANG_CODES:
        phrase = m_tr.group(1).strip().strip("\"'")
        lang_name = m_tr.group(2)
        tl = TRANSLATE_LANG_CODES[lang_name]
        url = f"https://translate.google.com/?sl=auto&tl={tl}&text={quote(phrase)}&op=translate"
        if dry_run:
            return "WebAgent", f"🧪 Dry-Run [WebAgent]: Would open Google Translate ({lang_name}: {tl}) for '{phrase}' ({url})."
        return "WebAgent", f"🌐 Translating '{phrase}' to {lang_name.title()} — {tools.open_url_safe(url)}"

    m_weather = re.fullmatch(r"(?:check\s+|what(?:'s|\s+is)\s+the\s+)?weather(?:\s+in\s+(.+))?", low)
    if m_weather:
        city = (m_weather.group(1) or "").strip()
        q = f"weather {city}".strip()
        url = f"https://www.google.com/search?q={quote(q)}"
        if dry_run:
            return "WebAgent", f"🧪 Dry-Run [WebAgent]: Would open live weather ({url})."
        return "WebAgent", tools.open_url_safe(url)

    # ── AGENT 4: SystemAgent (Theme, Night Light, DND, Wi-Fi/BT, Lock, Screenshot, Turbo Mode, Health, Disk, IP, Close App, Power) ──
    if re.fullmatch(
        r"(?:please\s+)?(?:"
        r"(?:(?:turn|switch|put|set|change|make)\s+(?:on|to|into|it\s+to|it)?\s*)?(?:enable\s+|activate\s+|on\s+)?dark\s*mode(?:\s+on)?|"
        r"(?:turn|switch)\s+dark\s*mode\s+on|"
        r"(?:turn\s+off|switch\s+off|disable|off)\s+light\s*mode|"
        r"light\s*mode\s+off|"
        r"make\s+(?:it|screen|desktop|pc|ubuntu)\s+dark"
        r")",
        low,
    ):
        return "SystemAgent", set_desktop_theme("dark", dry_run=dry_run)

    if re.fullmatch(
        r"(?:please\s+)?(?:"
        r"(?:(?:turn|switch|put|set|change|make)\s+(?:on|to|into|it\s+to|it)?\s*)?(?:enable\s+|activate\s+|on\s+)?light\s*mode(?:\s+on)?|"
        r"(?:turn|switch)\s+light\s*mode\s+on|"
        r"(?:turn\s+off|switch\s+off|disable|off)\s+dark\s*mode|"
        r"(?:turn|switch)\s+dark\s*mode\s+off|"
        r"dark\s*mode\s+off|"
        r"make\s+(?:it|screen|desktop|pc|ubuntu)\s+light"
        r")",
        low,
    ):
        return "SystemAgent", set_desktop_theme("light", dry_run=dry_run)

    m_nl = re.fullmatch(
        r"(?:please\s+)?(?:(?:turn|switch|put)?\s*(on|off|enable|disable)\s+night\s*light|(?:turn|switch)?\s*night\s*light\s*(on|off)?)",
        low,
    )
    if m_nl:
        nl_act = (m_nl.group(1) or m_nl.group(2) or "on").lower()
        return "SystemAgent", set_night_light(nl_act in {"on", "enable"}, dry_run=dry_run)

    m_dnd = re.fullmatch(
        r"(?:please\s+)?(?:(?:turn|switch|put)?\s*(on|off|enable|disable)\s+(?:dnd|do\s+not\s+disturb)|(?:turn|switch)?\s*(?:dnd|do\s+not\s+disturb)\s*(on|off)?)",
        low,
    )
    if m_dnd:
        dnd_act = (m_dnd.group(1) or m_dnd.group(2) or "on").lower()
        return "SystemAgent", set_dnd_mode(dnd_act in {"on", "enable"}, dry_run=dry_run)

    m_radio_ev = re.fullmatch(
        r"(?:please\s+)?(?:(?:turn|switch|put)?\s*(on|off|enable|disable)\s+(wifi|wi-fi|wi\s+fi|bluetooth|bt)|(?:turn|switch)?\s*(wifi|wi-fi|wi\s+fi|bluetooth|bt)\s+(on|off))",
        low,
    )
    if m_radio_ev:
        r_act_raw = (m_radio_ev.group(1) or m_radio_ev.group(4) or "on").lower()
        r_tgt_raw = (m_radio_ev.group(2) or m_radio_ev.group(3) or "wifi").lower().replace("-", "").replace(" ", "")
        r_tgt = "bluetooth" if r_tgt_raw in {"bt", "bluetooth"} else "wifi"
        r_act = "on" if r_act_raw in {"on", "enable"} else "off"
        if dry_run:
            return "SystemAgent", f"🧪 Dry-Run [SystemAgent]: Would turn {r_tgt} {r_act}."
        return "SystemAgent", tools.control_connectivity(r_tgt, r_act)

    if low in {"lock", "lock screen", "lock my screen", "lock pc", "lock my pc", "lock laptop", "lock my laptop"}:
        if dry_run:
            return "SystemAgent", "🧪 Dry-Run [SystemAgent]: Would lock desktop screen via loginctl."
        if shutil.which("loginctl"):
            command_runner.run_command(["loginctl", "lock-session"], timeout=4.0)
            return "SystemAgent", "🔒 Locked screen."
        return "SystemAgent", "Could not lock screen (`loginctl` not found)."

    if low in {"screenshot", "take screenshot", "take a screenshot", "capture screen"}:
        if dry_run:
            return "SystemAgent", "🧪 Dry-Run [SystemAgent]: Would launch desktop screenshot capture."
        if shutil.which("gnome-screenshot") and tools.launch_detached(["gnome-screenshot", "-i"]):
            return "SystemAgent", "📸 Opened Screenshot tool."
        return "SystemAgent", "Press PrintScreen (or install `gnome-screenshot`) to capture a screenshot."

    if re.fullmatch(
        r"(?:please\s+)?(?:fix\s+(?:my\s+)?lag|my\s+(?:pc|laptop|ubuntu|computer|os)\s+is\s+lagging(?:\s+fix\s+it)?|enable\s+(?:turbo|lite|performance)\s+mode|turn\s+on\s+(?:turbo|lite|performance)\s+mode|speed\s+up\s+(?:my\s+)?(?:pc|laptop|ubuntu)|turbo\s+mode(?:\s+on)?)",
        low,
    ):
        return "SystemAgent", turbo_mode.enable_turbo_mode(dry_run=dry_run)

    if re.fullmatch(
        r"(?:please\s+)?(?:disable\s+(?:turbo|lite)\s+mode|turn\s+off\s+(?:turbo|lite)\s+mode|restore\s+(?:mac|macos|gnome)\s+(?:animations|blur|effects)|turbo\s+mode\s+off)",
        low,
    ):
        return "SystemAgent", turbo_mode.disable_turbo_mode(dry_run=dry_run)

    if low in {"turbo status", "lite status", "check lag", "why is my pc lagging", "why is my laptop lagging", "lag status"}:
        if dry_run:
            return "SystemAgent", "🧪 Dry-Run [SystemAgent]: Would inspect RAM, ZRAM, Ollama, Cloud Offload & Turbo/Lite Mode status."
        return "SystemAgent", turbo_mode.get_turbo_status()

    if low in {"check battery", "battery", "battery status", "battery percentage", "check ram", "ram usage", "memory usage", "cpu usage", "system stats", "pc status", "laptop status"}:
        if dry_run:
            return "SystemAgent", "🧪 Dry-Run [SystemAgent]: Would inspect live CPU, RAM, ZRAM & battery stats."
        return "SystemAgent", tools.get_system_stats()

    if low in {"disk space", "check disk", "check storage", "storage status", "free space"}:
        return "SystemAgent", _quick_disk_space()

    if low in {"my ip", "my ip address", "what is my ip", "whats my ip", "what's my ip", "check internet", "internet status", "am i online", "wifi status"}:
        if dry_run:
            return "SystemAgent", "🧪 Dry-Run [SystemAgent]: Would check local IP & Wi-Fi connectivity."
        return "SystemAgent", _quick_ip_and_network()

    if low in {"list running apps", "running apps", "what apps are open", "open apps"}:
        if dry_run:
            return "SystemAgent", "🧪 Dry-Run [SystemAgent]: Would list running desktop applications."
        return "SystemAgent", tools.list_running_apps()

    m_close = re.fullmatch(r"(?:please\s+)?(?:close|quit|kill)\s+(?:app\s+)?([a-z0-9][a-z0-9 ._-]{1,35})", low)
    if m_close and m_close.group(1) not in {"tab", "this tab", "window"}:
        target_app = m_close.group(1).strip()
        if dry_run:
            return "SystemAgent", f"🧪 Dry-Run [SystemAgent]: Would ask approval to close app '{target_app}'."
        return "SystemAgent", tools.execute_tool("close_application", {"app_name": target_app}, approval_cb=approval_cb, status_cb=status_cb)

    if low in {"sleep pc", "sleep laptop", "suspend pc", "suspend laptop", "put pc to sleep"}:
        if dry_run:
            return "SystemAgent", "🧪 Dry-Run [SystemAgent]: Would ask approval to suspend/sleep this PC."
        if approval_cb is None:
            return "SystemAgent", "Suspending PC requires confirmation. Run: systemctl suspend"
        args = {"command": "systemctl suspend", "action": "suspend"}
        tok, derr = approval.request_bound_approval("run_shell_command", args, approval_cb, title="Put PC to Sleep (Suspend)?", target="systemctl suspend")
        if derr:
            return "SystemAgent", derr
        ok, err = approval.verify_and_consume(tok, "run_shell_command", args)
        if not ok:
            return "SystemAgent", err
        command_runner.run_command(["systemctl", "suspend"], timeout=10.0)
        return "SystemAgent", "💤 Putting PC to sleep..."

    # ── AGENT 6: GuiControlAgent (Screen Reading, Click Text, Scroll, Quick Hotkeys, Type) ──
    if low in {"read my screen", "read screen", "what is on my screen", "what's on my screen", "whats on my screen"}:
        if dry_run:
            return "GuiControlAgent", "🧪 Dry-Run [GuiControlAgent]: Would capture & OCR the current screen."
        return "GuiControlAgent", tools.execute_tool("read_screen", {}, approval_cb=approval_cb, status_cb=status_cb)

    if low in QUICK_HOTKEYS:
        hk, label = QUICK_HOTKEYS[low]
        if dry_run:
            return "GuiControlAgent", f"🧪 Dry-Run [GuiControlAgent]: Would ask approval to press hotkey {label} ({hk})."
        return "GuiControlAgent", tools.execute_tool("press_hotkey", {"keys": hk}, approval_cb=approval_cb, status_cb=status_cb)

    if low in {"scroll down", "scroll up"}:
        direction = "down" if "down" in low else "up"
        if dry_run:
            return "GuiControlAgent", f"🧪 Dry-Run [GuiControlAgent]: Would ask approval to scroll {direction}."
        return "GuiControlAgent", tools.execute_tool("scroll_screen", {"direction": direction, "amount": 5}, approval_cb=approval_cb, status_cb=status_cb)

    m_click = re.fullmatch(r"(?:please\s+)?click\s+(?:on\s+)?(?:the\s+)?(.+)", low)
    if m_click:
        label = m_click.group(1).strip()
        if label and len(label) <= 50:
            if dry_run:
                return "GuiControlAgent", f"🧪 Dry-Run [GuiControlAgent]: Would ask approval to find & click '{label}' on screen."
            return "GuiControlAgent", tools.execute_tool("click_on_text", {"text": label}, approval_cb=approval_cb, status_cb=status_cb)

    m_type = re.fullmatch(r"(?:please\s+)?type\s+(.+)", low)
    if m_type:
        txt_to_type = clean[len(clean) - len(m_type.group(1)) :].strip()
        if txt_to_type and len(txt_to_type) <= 200:
            if dry_run:
                return "GuiControlAgent", f"🧪 Dry-Run [GuiControlAgent]: Would ask approval to type '{txt_to_type[:40]}'."
            return "GuiControlAgent", tools.execute_tool("type_text", {"text": txt_to_type}, approval_cb=approval_cb, status_cb=status_cb)

    return None
