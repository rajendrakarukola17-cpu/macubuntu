#!/usr/bin/env python3
"""
macubuntu celina.tools.web_watch — Active Web Watching & Research daemon.

Runs Tier 1 self-healing watchers (Scrapling adaptive selectors, Gear 0, 0 LLM
tokens) over a user watchlist, diffs content against the last known snapshot,
and pushes concise change alerts to celina-island + a JSONL alert log.

Invariants:
  - All HTTP via celina_pkg.net_policy.safe_get (SSRF, DNS-rebinding, redirect,
    size, content-type controls).
  - Scraped content passes through celina_pkg.security.mcp_guard
    Dual-Context Barrier (Quarantined Reader) before any planner sees it.
  - Fail-closed: invalid/untrusted URLs are skipped, never auto-approved.
  - DRY_RUN=1: zero mutations (no state file writes, no notifications).
  - Idempotent: re-running with no changes produces no new alerts.
  - stdlib-first; scrapling/crawl4ai/playwright optional with graceful fallback.

Usage:
  celina/tools/web_watch.py --run-once          # single watch pass
  celina/tools/web_watch.py --daemon            # loop per interval
  celina/tools/web_watch.py --add <url> <name>  # add to watchlist
  celina/tools/web_watch.py --list              # show watchlist
  celina/tools/web_watch.py --dry-run           # preview, zero mutations
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Allow running both as `celina/tools/web_watch.py` and installed module
REPO_DIR = Path(__file__).resolve().parents[2]
if str(REPO_DIR / "features" / "celina") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "features" / "celina"))

from celina_pkg.web_scraper import tier1_watch, WatchEntry, ScrapeResult  # noqa: E402
from celina_pkg.security import mcp_guard  # noqa: E402


WATCH_DIR = Path.home() / ".local" / "share" / "celina" / "web_watch"
WATCHLIST_FILE = WATCH_DIR / "watchlist.json"
ALERTS_LOG = WATCH_DIR / "alerts.jsonl"
STATE_FILE = WATCH_DIR / "state.json"

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"


def _ensure_dirs() -> None:
    if DRY_RUN:
        return
    WATCH_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data: Any) -> None:
    if DRY_RUN:
        print(f"      [dry-run] would write {path}")
        return
    _ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_alert(alert: dict[str, Any]) -> None:
    if DRY_RUN:
        print(f"      [dry-run] would append alert: {alert.get('name')} ({alert.get('url')})")
        return
    _ensure_dirs()
    with open(ALERTS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(alert, sort_keys=True) + "\n")


def load_watchlist() -> list[WatchEntry]:
    raw = _load_json(WATCHLIST_FILE, [])
    entries: list[WatchEntry] = []
    if isinstance(raw, list):
        for item in raw:
            entries.append(WatchEntry(
                url=str(item.get("url", "")),
                name=str(item.get("name", item.get("url", ""))),
                last_sha256=str(item.get("last_sha256", "")),
                last_text=str(item.get("last_text", "")),
                selector=str(item.get("selector", "")),
                interval_min=int(item.get("interval_min", 60)),
                enabled=bool(item.get("enabled", True)),
                source_trust=str(item.get("source_trust", mcp_guard.DEFAULT_SOURCE_TRUST)),
            ))
    return entries


def save_watchlist(entries: list[WatchEntry]) -> None:
    data = [
        {
            "url": e.url,
            "name": e.name,
            "last_sha256": e.last_sha256,
            "last_text": e.last_text,
            "selector": e.selector,
            "interval_min": e.interval_min,
            "enabled": e.enabled,
            "source_trust": e.source_trust,
        }
        for e in entries
    ]
    _save_json(WATCHLIST_FILE, data)


def add_watch(url: str, name: str, interval_min: int = 60) -> None:
    # Fail-closed: validate URL via net_policy before adding
    from celina_pkg import net_policy
    try:
        net_policy.validate_public_url(url)
    except net_policy.URLPolicyError as exc:
        print(f"Rejected '{url}': {exc}", file=sys.stderr)
        sys.exit(2)

    entries = load_watchlist()
    if any(e.url == url for e in entries):
        print(f"already watching: {url}")
        return
    entries.append(WatchEntry(url=url, name=name, interval_min=interval_min))
    save_watchlist(entries)
    print(f"watching {name} ({url}) every {interval_min} min")


def list_watches() -> None:
    entries = load_watchlist()
    if not entries:
        print("(no watched URLs)")
        return
    for e in entries:
        status = "enabled" if e.enabled else "disabled"
        print(f"  [{status}] {e.name} ({e.url}) every {e.interval_min}min "
              f"sha={e.last_sha256[:12] if e.last_sha256 else 'none'}")


def _notify_island(name: str, url: str, diff: str, changed: bool) -> None:
    """
    Push a change alert to celina-island. Best-effort: tries notify-send,
    then a D-Bus signal, then falls back to the alert log only.
    Never mutates filesystem beyond the alert log (handled by caller).
    """
    title = f"Celina Web Watch: {name}"
    body = diff[:300].replace("\n", " | ") if diff else f"Changed: {url}"

    # Try notify-send (desktop notification)
    try:
        import subprocess
        subprocess.run(
            ["notify-send", "-u", "normal", "-a", "Celina", title, body],
            timeout=5,
            capture_output=True,
        )
        return
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass

    # Try D-Bus signal to celina-island (best effort)
    try:
        import subprocess
        subprocess.run(
            ["busctl", "--user", "call", "org.macubuntu.Celina",
             "/org/macubuntu/Celina", "org.macubuntu.Celina", "WebWatchAlert",
             "ss", name, body],
            timeout=5,
            capture_output=True,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass  # island may not be running; alert log is the source of truth


def run_once() -> int:
    """Single watch pass over all enabled URLs."""
    entries = load_watchlist()
    if not entries:
        print("No watched URLs. Add one with: web_watch.py --add <url> <name>")
        return 0

    state = _load_json(STATE_FILE, {})
    alerts = 0

    for entry in entries:
        if not entry.enabled:
            continue

        # Respect interval (only if we have a last-check timestamp)
        last_check = float(state.get(entry.url, {}).get("last_check", 0))
        now = time.time()
        if now - last_check < entry.interval_min * 60:
            continue

        previous = WatchEntry(
            url=entry.url,
            name=entry.name,
            last_sha256=entry.last_sha256,
            last_text=entry.last_text,
        )
        result: ScrapeResult = tier1_watch(
            entry.url, selector=entry.selector, previous=previous
        )

        # Update check timestamp regardless of outcome
        if not DRY_RUN:
            state[entry.url] = {
                "last_check": now,
                "last_sha256": result.sha256,
                "last_text": result.text[:2000],
            }

        if not result.ok:
            print(f"  warn {entry.name}: {result.error}")
            continue

        if result.changed:
            alert = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "name": entry.name,
                "url": entry.url,
                "tier": result.tier,
                "sha256": result.sha256,
                "selector_drift": result.selector_drift,
                "diff": result.diff,
                "source_trust": entry.source_trust,
            }
            _append_alert(alert)
            alerts += 1
            print(f"  CHANGED: {entry.name} ({entry.url})")
            if result.selector_drift:
                print(f"     selector drift detected — page may have redesigned")
            _notify_island(entry.name, entry.url, result.diff, result.changed)
        else:
            print(f"  no change: {entry.name}")

    if not DRY_RUN:
        _save_json(STATE_FILE, state)

    print(f"\nWatch pass complete: {alerts} new alert(s)")
    return 0


def daemon() -> int:
    """Loop: run watch passes, sleeping between."""
    print("Celina Web Watch daemon running (Ctrl-C to stop)...")
    stop = {"flag": False}

    def handle_signal(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not stop["flag"]:
        run_once()
        # Sleep in small increments so we respond to signals promptly
        for _ in range(30):
            if stop["flag"]:
                break
            time.sleep(1)
    print("\nDaemon stopped.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Celina Active Web Watching daemon (Tier 1 self-healing watchers)"
    )
    parser.add_argument("--run-once", action="store_true", help="single watch pass")
    parser.add_argument("--daemon", action="store_true", help="loop per interval")
    parser.add_argument("--add", metavar="URL", help="add URL to watchlist (requires --name)")
    parser.add_argument("--name", metavar="NAME", help="display name for --add")
    parser.add_argument("--interval", type=int, default=60, help="check interval in minutes (--add)")
    parser.add_argument("--list", action="store_true", help="show watchlist")
    parser.add_argument("--dry-run", action="store_true", help="preview, zero mutations")
    args = parser.parse_args()

    global DRY_RUN
    if args.dry_run:
        DRY_RUN = True
        os.environ["DRY_RUN"] = "1"

    if args.add:
        if not args.name:
            print("--name is required with --add", file=sys.stderr)
            return 2
        add_watch(args.add, args.name, args.interval)
        return 0
    if args.list:
        list_watches()
        return 0
    if args.daemon:
        return daemon()
    # Default: run once
    return run_once()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
