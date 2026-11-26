"""Celina AI — Zero-LLM-Exposure GNOME Keyring Auto-Login Helper (`celina-login-save`)
Stores service credentials directly in GNOME Keyring via `secret-tool` (never in chat,
logs, cache, or LLM prompts) and fills them ONLY after explicit Tier-2 user approval.

CLI usage (run once per service in your Ubuntu terminal):
  celina-login-save spotify
  celina-login-save github
  celina-login-save --list
"""

from __future__ import annotations

import argparse
import getpass
import re
import shutil
import subprocess
import time
from collections.abc import Callable

from . import approval, automation, command_runner, tools

ApprovalCallback = Callable[[str, str], bool]
_SAFE_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,40}$")


def normalize_service_name(raw: str) -> str:
    return re.sub(r"\s+", "-", (raw or "").strip().lower()).strip("-")


def lookup_saved_login(service: str) -> tuple[str | None, str | None]:
    """Looks up `(username, secret)` in GNOME Keyring via `secret-tool` strictly in Python memory.
    Returns `(None, None)` if `secret-tool` is unavailable or no entry exists.
    """
    svc = normalize_service_name(service)
    if not _SAFE_SERVICE_RE.fullmatch(svc) or not shutil.which("secret-tool"):
        return None, None

    # Fetch metadata (username attribute) without printing secrets
    meta_res = command_runner.run_command(
        ["secret-tool", "search", "--all", "celina_service", svc],
        timeout=5.0,
    )
    username: str | None = None
    if meta_res.ok or meta_res.stdout or meta_res.stderr:
        combined = f"{meta_res.stdout}\n{meta_res.stderr}"
        m_user = re.search(r"attribute\.username\s*=\s*(.+)", combined)
        if m_user:
            username = m_user.group(1).strip()

    sec_res = command_runner.run_command(
        ["secret-tool", "lookup", "celina_service", svc],
        timeout=5.0,
    )
    secret = sec_res.stdout.rstrip("\r\n") if sec_res.ok and sec_res.stdout else None
    if not secret:
        return None, None
    return username, secret


def list_saved_services() -> list[tuple[str, str]]:
    """Lists saved service names & usernames from GNOME Keyring (NEVER returns passwords)."""
    if not shutil.which("secret-tool"):
        return []
    res = command_runner.run_command(
        ["secret-tool", "search", "--all", "celina_vault", "login"],
        timeout=5.0,
    )
    combined = f"{res.stdout}\n{res.stderr}"
    entries: list[tuple[str, str]] = []
    cur_svc = ""
    cur_usr = ""
    for line in combined.splitlines():
        if line.startswith("[/"):
            if cur_svc:
                entries.append((cur_svc, cur_usr))
            cur_svc, cur_usr = "", ""
        elif "attribute.celina_service" in line:
            cur_svc = line.split("=", 1)[-1].strip()
        elif "attribute.username" in line:
            cur_usr = line.split("=", 1)[-1].strip()
    if cur_svc:
        entries.append((cur_svc, cur_usr))
    return sorted(set(entries))


def save_login_to_keyring(service: str, username: str, secret: str) -> tuple[bool, str]:
    """Stores a service login securely in GNOME Keyring via `secret-tool store` (stdin pipe, never argv)."""
    svc = normalize_service_name(service)
    if not _SAFE_SERVICE_RE.fullmatch(svc):
        return False, f"Invalid service name '{service}'. Use letters/digits/hyphens (e.g. spotify, github)."
    if not shutil.which("secret-tool"):
        return False, "GNOME Keyring CLI `secret-tool` is not installed. Run: sudo apt-get install -y libsecret-tools"
    if not secret:
        return False, "Password cannot be empty."

    proc = subprocess.run(
        [
            "secret-tool",
            "store",
            f"--label=Celina Login: {svc}",
            "celina_vault",
            "login",
            "celina_service",
            svc,
            "username",
            username.strip(),
        ],
        input=secret,
        text=True,
        capture_output=True,
        timeout=10.0,
    )
    if proc.returncode != 0:
        return False, f"Failed to save to GNOME Keyring: {(proc.stderr or 'unknown error').strip()[:200]}"
    return True, f"🔐 Saved '{svc}' login (user: {username.strip() or '(password-only)'}) securely in GNOME Keyring."


def fill_saved_login(
    service: str,
    *,
    approval_cb: ApprovalCallback | None = None,
    dry_run: bool = False,
    open_page: bool = True,
) -> str:
    """Zero-LLM-Exposure Auto-Login:
    - Looks up saved credentials in GNOME Keyring (`secret-tool`).
    - If not stored yet, opens the login page safely and tells the user how to save once via `celina-login-save`.
    - If stored, asks strict Tier-2 approval (displaying ONLY service & username, NEVER the secret)
      and types directly via Wayland input broker without ever exposing the password to the LLM/logs.
    """
    svc = normalize_service_name(service)
    if not _SAFE_SERVICE_RE.fullmatch(svc):
        return f"Invalid service name '{service}'."

    login_url = tools.LOGIN_URLS.get(svc) or tools.WEB_APPS.get(svc)

    if dry_run:
        return (
            f"🧪 Dry-Run [KeychainLogin]: Would check GNOME Keyring for '{svc}', "
            f"open {login_url or svc}, ask Tier-2 approval, and auto-fill directly from Keyring (0 password exposure to LLM)."
        )

    username, secret = lookup_saved_login(svc)
    if not secret:
        opened = tools.open_url_safe(login_url) if login_url else tools.open_application(f"{svc} in web")
        return (
            f"🔐 {opened}\n"
            f"• No saved GNOME Keyring login found for '{svc}' yet.\n"
            f"• Sign in directly in your browser — OR save it once securely (never shown to AI) by running in terminal:\n"
            f"    celina-login-save {svc}"
        )

    if approval_cb is None:
        secret = ""
        return (
            f"🔐 Found saved GNOME Keyring login for '{svc}' (user: {username or 'saved'}), "
            f"but auto-fill requires interactive Tier-2 approval."
        )

    # Strict Tier-2 argument-bound approval — ONLY service & username are ever shown or hashed
    args = {"service": svc, "username": username or "(saved)", "source": "gnome-keyring"}
    token, deny_err = approval.request_bound_approval(
        "type_text",
        args,
        approval_cb,
        title=f"Auto-fill saved GNOME Keyring login for '{svc}'?",
        target=f"keyring:{svc} (user={username or 'saved'})",
        extra_warning="Password stays in Python memory only — never sent to LLM, logs, or chat history.",
    )
    if deny_err:
        secret = ""
        return deny_err
    ok, verr = approval.verify_and_consume(token, "type_text", args)
    if not ok:
        secret = ""
        return verr

    opened_note = ""
    if open_page and login_url:
        opened_note = tools.open_url_safe(login_url) + " "
        time.sleep(1.5)

    try:
        typed_ok, typed_msg = automation.type_keychain_secret_approved(
            username=username or "",
            secret=secret,
            submit=True,
        )
    finally:
        secret = ""

    if not typed_ok:
        return (
            f"🔐 {opened_note}Could not auto-type into login form ({typed_msg}).\n"
            f"• Make sure the login field is focused and Wayland input portal/ydotool is active, or sign in directly."
        )
    return f"🔐 {opened_note}Auto-filled saved GNOME Keyring login for '{svc}' (password never exposed to AI)."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Save service logins safely into GNOME Keyring for Celina auto-login (never exposed to AI).")
    parser.add_argument("service", nargs="?", help="Service name (e.g. spotify, github, gmail, notion)")
    parser.add_argument("--username", "-u", default="", help="Username or email (prompted if omitted)")
    parser.add_argument("--list", action="store_true", help="List saved service names (never shows passwords)")
    args = parser.parse_args(argv)

    if args.list:
        items = list_saved_services()
        if not items:
            print("No saved Celina Keyring logins yet. Run: celina-login-save spotify")
            return 0
        print("🔐 Saved GNOME Keyring Logins (passwords hidden):")
        for svc, usr in items:
            print(f"  • {svc:<18} user={usr or '(password-only)'}")
        return 0

    if not args.service:
        parser.print_help()
        return 1

    svc = normalize_service_name(args.service)
    usr = args.username.strip() or input(f"Username / Email for '{svc}' (press Enter to skip): ").strip()
    pw = getpass.getpass(f"Password for '{svc}' (hidden, stored only in GNOME Keyring): ")
    ok, msg = save_login_to_keyring(svc, usr, pw)
    pw = ""
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
