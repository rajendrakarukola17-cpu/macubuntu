"""
Celina AI - Apple Intelligence Writing Tools, Live Text Region OCR & Active Context (v4.3)
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
from pathlib import Path
from typing import Callable, Optional

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
    """Reads the Wayland primary selection first, falling back to the standard clipboard."""
    if shutil.which("wl-paste"):
        for args in (["wl-paste", "--primary", "--no-newline"], ["wl-paste", "--no-newline"]):
            res = command_runner.run_command(args, timeout=2.0)
            if res.ok and res.stdout.strip():
                return res.stdout.strip()
    if shutil.which("xclip"):
        for sel in ("primary", "clipboard"):
            res = command_runner.run_command(["xclip", "-o", "-selection", sel], timeout=2.0)
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
    """Runs strictly on local Ollama (127.0.0.1) so Writing Tools text never leaves the machine."""
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


def writing_tool_transform(
    mode: str = "proofread",
    text: str = "",
    copy_result: bool = True,
    llm_fn: Optional[Callable[[str, str], tuple[bool, str]]] = None,
) -> str:
    """
    Apple Intelligence Writing Tools equivalent (100% local Ollama by default).
    If `text` is omitted, reads the currently highlighted Wayland selection / clipboard.
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
    if copy_result and write_wayland_clipboard(result):
        copied_note = "\n\n📋 Copied transformed text to clipboard (press Ctrl+V to replace)."
    return f"✍️ Writing Tool [{m}] (local):\n{result}{copied_note}"


def live_text_ocr_region(
    copy_to_clipboard: bool = True,
    lang: str = "eng",
    capture_fn: Optional[Callable[[Path], bool]] = None,
    ocr_fn: Optional[Callable[[Path, str], tuple[bool, str]]] = None,
) -> str:
    """
    macOS Live Text equivalent (`Super+Shift+T`):
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
    """
    Returns a tiny non-sensitive context header (`Active app: ... | Window: ...`) when the user
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
    # Never leak password-manager window titles into prompts
    if any(kw in f"{app} {title}".lower() for kw in ("keepass", "bitwarden", "1password", "password")):
        return ""
    if not app and not title:
        return ""
    return f"[Active Desktop Context: app='{app}', window='{title[:120]}']"
