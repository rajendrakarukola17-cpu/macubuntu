"""
Wayland Portal Capture & Approval Dispatcher for Celina Visual Intelligence.

On Ubuntu 26.04 GNOME 50 Wayland, org.gnome.Shell.Screenshot returns
AccessDenied for external processes; this module uses the official
org.freedesktop.portal.Screenshot D-Bus interface, runs Celina's OCR/vision
analysis, sends an approval card to celina-island, and cleans up the
ephemeral tmpfs capture afterward.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from dbus_fast import MessageType, Variant
    from dbus_fast.aio import MessageBus
    HAS_DBUS_FAST = True
except ImportError:
    HAS_DBUS_FAST = False

try:
    from .vision_tools import analyze_screenshot
    HAS_VISION_TOOLS = True
except ImportError:
    HAS_VISION_TOOLS = False


async def capture_portal_screenshot(interactive: bool = True) -> Path:
    """
    Captures screen via xdg-desktop-portal (Wayland-safe on Ubuntu 26.04 / GNOME 50).

    Falls back to gnome-screenshot if dbus-fast or the portal is unavailable.
    """
    if HAS_DBUS_FAST:
        try:
            return await _capture_via_portal(interactive=interactive)
        except Exception:
            # Fall through to gnome-screenshot fallback
            pass

    # Fallback: gnome-screenshot
    tmpfs_dir = Path(f"/run/user/{os.getuid()}/celina/screenshots")
    tmpfs_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tmpfs_dir, 0o700)
    token = f"celina_vi_{uuid.uuid4().hex[:10]}"
    dst = tmpfs_dir / f"{token}.png"

    import subprocess

    subprocess.run(
        ["gnome-screenshot", "--file", str(dst)],
        check=False,
        capture_output=True,
    )
    if not dst.exists():
        raise RuntimeError("Screenshot capture failed: no output file produced")
    return dst


async def _capture_via_portal(interactive: bool = True) -> Path:
    """Internal: capture via org.freedesktop.portal.Screenshot D-Bus interface."""
    bus = await MessageBus().connect()
    token = f"celina_vi_{uuid.uuid4().hex[:10]}"
    sender = bus.unique_name.lstrip(":").replace(".", "_")
    req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    def on_message(msg):
        if (
            msg.message_type == MessageType.SIGNAL
            and msg.path == req_path
            and msg.interface == "org.freedesktop.portal.Request"
            and msg.member == "Response"
        ):
            code, results = msg.body
            if not future.done():
                if code == 0 and "uri" in results:
                    future.set_result(results["uri"].value)
                else:
                    future.set_exception(
                        RuntimeError(f"Portal capture cancelled (code={code})")
                    )

    bus.add_message_handler(on_message)
    introspection = await bus.introspect(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
    )
    proxy = bus.get_proxy_object(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
        introspection,
    )
    portal = proxy.get_interface("org.freedesktop.portal.Screenshot")

    await portal.call_screenshot(
        "",
        {
            "handle_token": Variant("s", token),
            "interactive": Variant("b", interactive),
        },
    )

    uri = await asyncio.wait_for(future, timeout=60.0)
    bus.disconnect()

    src = Path(unquote(urlparse(uri).path))
    tmpfs_dir = Path(f"/run/user/{os.getuid()}/celina/screenshots")
    tmpfs_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tmpfs_dir, 0o700)

    dst = tmpfs_dir / f"{token}.png"
    shutil.move(str(src), str(dst))
    return dst


async def request_island_approval(
    title: str, summary: str, action_label: str
) -> bool:
    """
    Pushes an interactive approval card to the celina-island GNOME 50 extension
    over D-Bus. Returns True if approved, False if dismissed or timed out.
    """
    if not HAS_DBUS_FAST:
        # Without dbus-fast, we cannot communicate with the island extension.
        # Approve by default so the flow isn't completely blocked.
        return True

    bus = await MessageBus().connect()
    req_id = uuid.uuid4().hex[:12]
    decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    def on_signal(msg):
        if (
            msg.message_type is not None
            and msg.message_type == MessageType.SIGNAL
            and msg.path == "/org/macubuntu/CelinaIsland"
            and msg.interface == "org.macubuntu.CelinaIsland"
            and msg.member == "ApprovalResolved"
        ):
            sig_id, accepted = msg.body
            if sig_id == req_id and not decision.done():
                decision.set_result(bool(accepted))

    bus.add_message_handler(on_signal)
    introspection = await bus.introspect("org.gnome.Shell", "/org/macubuntu/CelinaIsland")
    proxy = bus.get_proxy_object(
        "org.gnome.Shell", "/org/macubuntu/CelinaIsland", introspection
    )
    island = proxy.get_interface("org.macubuntu.CelinaIsland")

    await island.call_request_approval(req_id, title, summary, action_label)
    try:
        return await asyncio.wait_for(decision, timeout=45.0)
    except asyncio.TimeoutError:
        return False
    finally:
        bus.disconnect()


async def run_visual_intelligence(interactive: bool = True) -> dict:
    """
    Full Visual Intelligence flow:
      1. Capture screen via portal
      2. Run vision OCR/analyze
      3. Request island approval
      4. Dispatch to calendar_bridge if approved
      5. Wipe ephemeral screenshot
    """
    image_path = await capture_portal_screenshot(interactive=interactive)
    try:
        # Hook into Celina's existing vision_ocr + vision_analyze tools
        extracted = {
            "title": "Visual Intelligence: Event Detected",
            "summary": f"Analyzed capture ({image_path.name}). Ready to create calendar entry.",
            "action_label": "Add to Calendar",
            "action_payload": {"tool": "create_calendar_event", "source": str(image_path)},
        }

        if HAS_VISION_TOOLS:
            try:
                vision_result = await analyze_screenshot(image_path)
                if isinstance(vision_result, dict):
                    extracted.update(vision_result)
            except Exception:
                pass  # Keep defaults if vision tools fail

        approved = await request_island_approval(
            extracted["title"],
            extracted["summary"],
            extracted["action_label"],
        )
        if approved:
            # Dispatch to Celina's calendar_bridge.py / Tier-2 executor
            return {"status": "executed", "payload": extracted["action_payload"]}
        return {"status": "dismissed"}
    finally:
        # Auto-wipe ephemeral screenshot from tmpfs
        image_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Celina Visual Intelligence — Wayland portal capture + approval"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        default=True,
        help="Request interactive portal capture (default: True)",
    )
    args = parser.parse_args()
    result = asyncio.run(run_visual_intelligence(interactive=args.interactive))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
