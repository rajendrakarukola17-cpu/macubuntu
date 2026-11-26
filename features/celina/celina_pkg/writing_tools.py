"""Celina AI - Apple Intelligence Writing Tools, Live Text Region OCR & Active Context (v4.3)
Privacy-first local-by-default desktop intelligence:
  - Writing Tools (`Super+Shift+W`): grabs Wayland primary selection / clipboard, transforms text
    locally via Ollama (proofread, rewrite, concise, friendly, professional, summarize, key_points,
    table), and never sends unredacted secrets to cloud APIs.
  - Live Text OCR (`Super+Shift+T`): macOS-style interactive region snip -> Tesseract (EN/TE/HI)
    -> Wayland clipboard -> immediate secure deletion of the temporary capture PNG.
  - Lightweight Active Context: supplies active app name & window title (<5 ms via AT-SPI2) for
    deictic prompts like "summarize this" or "reply politely" without taking full screenshots.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import automation, command_runner, config

WRITING_MODES: dict[str, str] = {
    "proofread": "Fix grammar, spelling, and punctuation while preserving the original meaning and tone. Return ONLY the corrected text.",
    "rewrite": "Rewrite the text clearly and naturally while keeping the exact meaning. Return ONLY the rewritten text.",
    "concise": "Make the text significantly shorter and tighter without losing key facts. Return ONLY the concise text.",
    "friendly": "Rewrite the text in a warm, friendly, conversational tone. Return ONLY the rewritten text.",
    "professional": "Rewrite the text in a clear, polished, professional tone. Return ONLY the rewritten text.",
    "summarize": "Summarize the text concisely in 2-4 crisp bullet points. Return ONLY the summary.",
    "key_points": "Extract the key takeaways and action items as a clean bullet list. Return ONLY the bullet list.",
    "table": "Convert the information into a clean Markdown table. Return ONLY the Markdown table.",
}

_DEICTIC_HINTS = (
    "this page", "this window", "this app", "this email", "this message",
    "this document", "this code", "this text", "summarize this", "proofread this",
    "rewrite this", "explain this", "reply to this", "what is this", "what's this",
)


def read_wayland_selection() -> str:
    """Reads ONLY the Wayland primary selection (highlighted text).

    Deliberately does NOT read the standard clipboard (Ctrl+C buffer) —
    users copy API keys, passwords, and 2FA codes there, and auto-reading
    leaks them into the model's KV cache and request logs.
    """
    if shutil.which("wl-paste"):
        res = command_runner.run_command(
            ["wl-paste", "--primary", "--no-newline"], timeout=2.0,
        )
        if res.ok and res.stdout.strip():
            return res.stdout.strip()
    return ""


def write_wayland_clipboard(text: str) -> bool:
    if not text:
        return False
    if shutil.which("wl-copy"):
        try:
            proc = subprocess.run(["wl-copy"], input=text, text=True, timeout=3, check=False)
            return proc.returncode == 0
        except Exception:
            return False
    if shutil.which("xclip"):
        try:
            proc = subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, timeout=3, check=False)
            return proc.returncode == 0
        except Exception:
            return False
    return False


def _default_local_llm(system_prompt: str, user_text: str) -> tuple[bool, str]:
    """Runs strictly on local Ollama (127.0.0.1) so Writing Tools text never leaves the machine.

    Enforces loopback-only OLLAMA_API_BASE — a user-configured remote endpoint
    would otherwise silently leak all Writing Tools text off-device.
    """
    parsed = urlparse(config.OLLAMA_API_BASE)
    host = parsed.hostname or ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host in ("localhost", "localhost.localdomain")
    if not is_loopback:
        return (
            False,
            f"Writing Tools refused: OLLAMA_API_BASE ({parsed.netloc}) is not loopback. "
            "Local-only policy — text must never leave the machine.",
        )
    url = f"{config.OLLAMA_API_BASE.rstrip('/')}/api/chat"
    payload = {
        "model": config.LOCAL_MODEL,
        "stream": False,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text[:6000]},
        ],
        "options": {"temperature": 0.25, "num_predict": 700},
    }
    try:
        resp = requests.post(url, json=payload, timeout=config.LOCAL_TIMEOUT)
        if resp.status_code != 200:
            return False, f"Local Ollama HTTP {resp.status_code}"
        data = resp.json()
        content = str((data.get("message") or {}).get("content", "")).strip()
        if not content:
            return False, "Local Ollama returned an empty response."
        return True, content
    except Exception as exc:
        return False, f"Local Ollama unavailable: {exc}"


def inject_text_via_ydotool(text: str) -> bool:
    """Inject transformed text directly into the active Wayland text field via ydotool.
    System-wide text injection (no clipboard required) — macOS-style "replace in place".
    Fail-closed: returns False if ydotool is unavailable or injection fails.
    Never logs or leaks the injected text.
    """
    if not text:
        return False
    if not shutil.which("ydotool"):
        return False
    try:
        proc = subprocess.run(
            ["ydotool", "type", "--", text],
            text=True,
            timeout=10,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def writing_tool_transform(
    mode: str = "proofread",
    text: str = "",
    copy_result: bool = True,
    inject: bool = False,
    llm_fn: Callable[[str, str], tuple[bool, str]] | None = None,
) -> str:
    """Apple Intelligence Writing Tools equivalent (100% local Ollama by default).
    If `text` is omitted, reads the currently highlighted Wayland selection / clipboard.
    When `inject=True`, types the transformed text directly into the active Wayland
    field via ydotool (system-wide text injection, Stage 30 / ENABLE_WAYLAND_TEXT_INJECTION).
    """
    m = (mode or "proofread").strip().lower()
    if m not in WRITING_MODES:
        return f"Error: Unknown Writing Tool mode '{mode}'. Choose from: {', '.join(sorted(WRITING_MODES))}."

    source_text = (text or "").strip() or read_wayland_selection()
    if not source_text:
        return "Error: No text provided and Wayland selection/clipboard is empty. Highlight text first."

    # Never pass extremely large binary/blob clipboard payloads
    if len(source_text) > 12000:
        source_text = source_text[:12000]

    runner = llm_fn or _default_local_llm
    ok, result = runner(WRITING_MODES[m], source_text)
    if not ok:
        return f"Writing Tool ({m}) error: {result}"

    copied_note = ""
    injected_note = ""
    if copy_result and write_wayland_clipboard(result):
        copied_note = "\n\n📋 Copied transformed text to clipboard (press Ctrl+V to replace)."
    if inject:
        # P0: Never inject into password fields — refuse password-field typing.
        if automation.is_focused_password_field():
            injected_note = "\n\n🔒 Injection blocked: active field is a password field (refused to type secrets)."
        else:
            injected_ok = inject_text_via_ydotool(result)
            if injected_ok:
                injected_note = "\n\n⌨️  Injected transformed text directly into the active field (ydotool)."
            else:
                injected_note = "\n\n⚠️  ydotool injection unavailable (install ydotool + enable ENABLE_WAYLAND_TEXT_INJECTION)."

    return f"✍️ Writing Tool [{m}] (local):\n{result}{copied_note}{injected_note}"


def live_text_ocr_region(
    copy_to_clipboard: bool = True,
    lang: str = "eng",
    capture_fn: Callable[[Path], bool] | None = None,
    ocr_fn: Callable[[Path, str], tuple[bool, str]] | None = None,
) -> str:
    """MacOS Live Text equivalent (`Super+Shift+T`):
    1. Captures an interactive screen region into a private 0600 temp PNG
    2. Runs local Tesseract OCR
    3. Copies extracted text straight to the Wayland clipboard
    4. Deletes the temporary screenshot immediately (zero disk trail!)
    """
    if ocr_fn is None and not shutil.which("tesseract"):
        return "Error: tesseract-ocr is not installed (run Stage 10 or `sudo apt install tesseract-ocr`)."

    safe_lang = "".join(c for c in (lang or "eng") if c.isalnum() or c in "+_")[:32] or "eng"
    fd, tmp_name = tempfile.mkstemp(prefix="celina_livetext_", suffix=".png")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        os.chmod(tmp_path, 0o600)
        if capture_fn is not None:
            captured = bool(capture_fn(tmp_path))
        else:
            captured = _capture_region_png(tmp_path)
        if not captured or not tmp_path.exists() or tmp_path.stat().st_size == 0:
            return "Live Text cancelled (no screen region captured)."

        if ocr_fn is not None:
            ok, extracted = ocr_fn(tmp_path, safe_lang)
        else:
            res = command_runner.run_command(
                ["tesseract", str(tmp_path), "stdout", "-l", safe_lang, "--psm", "6"],
                timeout=15.0,
            )
            ok = res.ok
            extracted = res.stdout.strip() if res.ok else (res.stderr or "tesseract failed")

        if not ok:
            return f"Live Text OCR error: {extracted}"
        cleaned = "\n".join(ln.rstrip() for ln in extracted.splitlines() if ln.strip()).strip()
        if not cleaned:
            return "Live Text found no readable text in the selected region."

        copied = bool(copy_to_clipboard and write_wayland_clipboard(cleaned))
        suffix = "\n\n📋 Copied to clipboard." if copied else ""
        return f"🔍 Live Text OCR ({len(cleaned)} chars):\n{cleaned}{suffix}"
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _capture_region_png(out_path: Path) -> bool:
    """Captures a user-selected screen region using GNOME/Wayland native area capture tools."""
    if shutil.which("gnome-screenshot"):
        res = command_runner.run_command(["gnome-screenshot", "-a", "-f", str(out_path)], timeout=30.0)
        if res.ok and out_path.exists() and out_path.stat().st_size > 0:
            return True
    if shutil.which("grim") and shutil.which("slurp"):
        sel = command_runner.run_command(["slurp"], timeout=30.0)
        if sel.ok and sel.stdout.strip():
            g = command_runner.run_command(["grim", "-g", sel.stdout.strip(), str(out_path)], timeout=10.0)
            return g.ok and out_path.exists() and out_path.stat().st_size > 0
    return False


def get_lightweight_active_context(user_prompt: str = "") -> str:
    """Returns a tiny non-sensitive context header (`Active app: ... | Window: ...`) when the user
    references on-screen context ("summarize this", "rewrite this", etc.), without taking screenshots.
    """
    low = (user_prompt or "").strip().lower()
    if low and not any(hint in low for hint in _DEICTIC_HINTS):
        return ""
    info = automation.get_active_window_info()
    if not info:
        return ""
    app = str(info.get("app") or "").strip()
    title = str(info.get("title") or "").strip()
    # Never leak password-manager window titles into prompts.
    # Reuse the AT-SPI2-aware password-field detector instead of a fragile denylist.
    if automation.is_focused_password_field():
        return ""
    # Also suppress known password-manager window titles as a defense-in-depth hint.
    if any(kw in f"{app} {title}".lower() for kw in ("keepass", "bitwarden", "1password", "password", "passwd")):
        return ""
    if not app and not title:
        return ""
    return f"[Active Desktop Context: app='{app}', window='{title[:120]}']"
