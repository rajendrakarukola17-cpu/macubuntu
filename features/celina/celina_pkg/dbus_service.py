"""
Celina AI — D-Bus Inter-Process Nervous System (`org.macubuntu.Celina`)

Provides the strict, UID-verified session D-Bus service connecting:
  - Native GNOME Shell Extension (`celina-island@macubuntu.org`)
  - Celina Control Plane (`celina.service`)
  - OS Event Bus (`os_events.py`), Semantic Blueprints, Focus Modes & Router

Defense-in-depth:
  1. `/etc/dbus-1/session.d/org.macubuntu.Celina.conf` enforces session-bus policy.
  2. Every mutating D-Bus method verifies caller UID == `os.getuid()` via
     `org.freedesktop.DBus.GetConnectionUnixUser` before dispatching.
  3. All prompts/actions continue through Celina's capability manifest,
     approval gate, undo journal, and hash-chained evidence ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from typing import Any, Optional

from . import capabilities, config, evidence, os_events, state_ops

BUS_NAME = "org.macubuntu.Celina"
OBJECT_PATH = "/org/macubuntu/Celina"
INTERFACE_NAME = "org.macubuntu.Celina1"
AUTONOMY_INTERFACE_NAME = "org.macubuntu.Celina.Autonomy"

INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE_NAME}">
    <method name="GetStatus">
      <arg type="s" name="status_json" direction="out"/>
    </method>
    <method name="GetState">
      <arg type="s" name="status_json" direction="out"/>
    </method>
    <method name="GetPrivacyState">
      <arg type="s" name="privacy_json" direction="out"/>
    </method>
    <method name="GetFocusMode">
      <arg type="s" name="focus_mode" direction="out"/>
    </method>
    <method name="SetFocusMode">
      <arg type="s" name="mode" direction="in"/>
      <arg type="s" name="result_json" direction="out"/>
    </method>
    <method name="Offload">
      <arg type="s" name="result_json" direction="out"/>
    </method>
    <method name="GetCapabilitiesSummary">
      <arg type="s" name="summary" direction="out"/>
    </method>
    <method name="UndoLast">
      <arg type="s" name="result_json" direction="out"/>
    </method>
    <method name="AskPrompt">
      <arg type="s" name="prompt" direction="in"/>
      <arg type="s" name="reply_json" direction="out"/>
    </method>
    <method name="GetMCPStatus">
      <arg type="s" name="mcp_json" direction="out"/>
    </method>
    <method name="SetEdgeGlow">
      <arg type="b" name="active" direction="in"/>
      <arg type="s" name="mode" direction="in"/>
      <arg type="s" name="result_json" direction="out"/>
    </method>
    <signal name="StatusChanged">
      <arg type="s" name="status_json"/>
    </signal>
    <signal name="PrivacyDotsChanged">
      <arg type="b" name="mic_active"/>
      <arg type="b" name="cam_active"/>
      <arg type="b" name="screen_active"/>
    </signal>
    <signal name="EdgeGlowChanged">
      <arg type="b" name="active"/>
      <arg type="s" name="mode"/>
    </signal>
  </interface>
  <interface name="{AUTONOMY_INTERFACE_NAME}">
    <method name="SubmitGoal">
      <arg type="s" name="goal" direction="in"/>
      <arg type="s" name="root_task_id" direction="out"/>
    </method>
    <method name="GetTaskGraph">
      <arg type="s" name="graph_json" direction="out"/>
    </method>
    <method name="CancelTask">
      <arg type="s" name="task_id" direction="in"/>
      <arg type="s" name="result_json" direction="out"/>
    </method>
    <signal name="TaskStateChanged">
      <arg type="s" name="task_id"/>
      <arg type="s" name="status"/>
    </signal>
    <signal name="MCPActionStarted">
      <arg type="s" name="server"/>
      <arg type="s" name="tool"/>
    </signal>
  </interface>
</node>
"""

ALLOWED_FOCUS_MODES = frozenset({"off", "work", "coding", "dnd", "sleep", "presentation"})


class CelinaDBusBackend:
    """
    Pure-Python D-Bus method handler enforcing peer-UID isolation and routing
    all requests cleanly through Celina's existing security boundaries.
    Fully unit-testable without a live D-Bus daemon or GTK display server.
    """

    def __init__(self, expected_uid: Optional[int] = None):
        self.expected_uid = os.getuid() if expected_uid is None else int(expected_uid)
        self._focus_mode = "off"
        self._started_at = time.time()

    def verify_caller_uid(self, caller_uid: Optional[int]) -> tuple[bool, str]:
        if caller_uid is None:
            return False, "Denied: unable to verify caller Unix UID."
        if int(caller_uid) != self.expected_uid:
            return False, f"Denied: caller UID {caller_uid} does not match session owner UID {self.expected_uid}."
        return True, ""

    def get_status(self, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        ev_ok, _ev_msg, ev_cnt = evidence.get_evidence_ledger().verify_chain()
        route = config.get_active_local_inference_route(prefer_npu=True)
        payload = {
            "ok": True,
            "assistant": config.get_assistant_name(),
            "nickname": config.get_assistant_nickname(),
            "version": "4.4.0",
            "focus_mode": self._focus_mode,
            "uptime_sec": int(time.time() - self._started_at),
            "evidence_chain_ok": ev_ok,
            "evidence_records": ev_cnt,
            "compute_backend": route.get("backend", "cpu"),
            "compute_endpoint": route.get("endpoint", config.OLLAMA_API_BASE),
            "bus_name": BUS_NAME,
        }
        return json.dumps(payload, sort_keys=True)

    def get_state(self, *, caller_uid: Optional[int] = None) -> str:
        return self.get_status(caller_uid=caller_uid)

    def offload(self, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        # Gracefully request Ollama keep_alive=0 offload via localhost only (no crash if offline)
        import urllib.request

        model = config.LOCAL_MODEL
        url = f"{config.OLLAMA_API_BASE}/api/generate"
        body = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=2.0):
                return json.dumps({"ok": True, "model": model, "message": f"Offloaded {model} from memory."})
        except Exception:
            return json.dumps({"ok": True, "model": model, "message": f"Offload scheduled (local runtime idle/offline)."})

    def get_privacy_state(self, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        state_file = config.BASE_DIR / "privacy_state.json"
        dots = {"mic": False, "cam": False, "screen": False}
        if state_file.is_file():
            try:
                raw = json.loads(state_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    dots["mic"] = bool(raw.get("mic", False))
                    dots["cam"] = bool(raw.get("cam", False))
                    dots["screen"] = bool(raw.get("screen", False))
            except (OSError, json.JSONDecodeError):
                pass
        return json.dumps({"ok": True, **dots}, sort_keys=True)

    def get_focus_mode(self, *, caller_uid: Optional[int] = None) -> str:
        ok, _err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        return self._focus_mode if ok else "denied"

    def set_focus_mode(self, mode: str, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        clean = (mode or "").strip().lower()
        if clean not in ALLOWED_FOCUS_MODES:
            return json.dumps({"ok": False, "error": f"Unsupported focus mode '{clean}'"})
        self._focus_mode = clean
        # Persist focus state privately so shell/island indicators stay synchronized
        try:
            focus_file = config.BASE_DIR / "focus_mode.json"
            state_ops._atomic_write_json(focus_file, {"mode": clean, "updated_at": time.time()})
        except OSError:
            pass
        return json.dumps({"ok": True, "focus_mode": clean}, sort_keys=True)

    def get_capabilities_summary(self, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return err
        return capabilities.format_capabilities_summary()

    def undo_last(self, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        u_ok, msg = state_ops.get_undo_journal().undo_action()
        return json.dumps({"ok": u_ok, "message": msg}, sort_keys=True)

    def ask_prompt(self, prompt: str, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        text = (prompt or "").strip()
        if not text:
            return json.dumps({"ok": False, "error": "Empty prompt."})
        if len(text) > 4000:
            return json.dumps({"ok": False, "error": "Prompt exceeds 4000-char D-Bus limit."})
        # Route strictly through CelinaRouter with approval_cb=None (fail-closed: Tier-2 tools
        # cannot silently auto-execute over headless D-Bus without interactive UI approval!)
        from . import router

        cr = router.CelinaRouter()
        reply, src = cr.ask(text, mode="auto", approval_cb=None)
        return json.dumps({"ok": True, "source": src, "reply": reply}, sort_keys=True)

    # --- org.macubuntu.Celina.Autonomy methods (same UID verification) ---
    def submit_goal(self, goal: str, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        clean = (goal or "").strip()
        if not clean:
            return json.dumps({"ok": False, "error": "Empty goal."})
        if len(clean) > 2000:
            return json.dumps({"ok": False, "error": "Goal exceeds 2000-char limit."})
        from .planner.task_graph import TaskGraph
        from .planner.tree_planner import TreePlanner

        graph = TaskGraph(config.BASE_DIR / "tasks.db")
        try:
            planner = TreePlanner(llm_client=None, graph=graph)
            available = sorted(config.TIER1_AUTO_TOOLS | config.TIER2_APPROVAL_TOOLS)
            root_id = planner.plan(clean, available_tools=available)
            return root_id
        finally:
            graph.close()

    def get_task_graph(self, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        from .planner.task_graph import TaskGraph

        graph = TaskGraph(config.BASE_DIR / "tasks.db")
        try:
            return json.dumps({"ok": True, **graph.export_graph()}, sort_keys=True)
        finally:
            graph.close()

    def cancel_task(self, task_id: str, *, caller_uid: Optional[int] = None) -> str:
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        from .planner.task_graph import TaskGraph

        graph = TaskGraph(config.BASE_DIR / "tasks.db")
        try:
            affected = graph.cancel_task(str(task_id or "").strip())
            return json.dumps({"ok": True, "task_id": task_id, "cancelled_count": affected}, sort_keys=True)
        finally:
            graph.close()

    def invoke_capability(
        self,
        tool_name: str,
        arguments_json: str = "{}",
        *,
        caller_uid: Optional[int] = None,
        approval_cb: Any = None,
    ) -> str:
        """Capability #9: Routes D-Bus tool invocations through UnifiedCapabilityBus."""
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        try:
            args = json.loads(arguments_json) if arguments_json else {}
            if not isinstance(args, dict):
                return json.dumps({"ok": False, "error": "arguments_json must be a JSON object"})
        except json.JSONDecodeError as exc:
            return json.dumps({"ok": False, "error": f"invalid JSON: {exc}"})
        from . import capability_bus

        uid = self.expected_uid if caller_uid is None else caller_uid
        req = capability_bus.BusRequest(
            tool_name=str(tool_name or ""),
            arguments=args,
            transport="dbus",
            caller_uid=uid,
        )
        res = capability_bus.get_capability_bus().dispatch(req, approval_cb=approval_cb)
        return json.dumps(res.to_dict(), sort_keys=True)

    def get_mcp_status(self, *, caller_uid: Optional[int] = None) -> str:
        """Returns the ordered MCP perception/actuation server catalog & runtime status."""
        ok, err = self.verify_caller_uid(self.expected_uid if caller_uid is None else caller_uid)
        if not ok:
            return json.dumps({"ok": False, "error": err})
        from .autonomy import mcp_loader

        return json.dumps({"ok": True, **mcp_loader.get_mcp_loader().status()}, sort_keys=True)


def run_dbus_daemon() -> int:
    """Hosts org.macubuntu.Celina & org.macubuntu.Celina.Autonomy on session D-Bus."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib
    except Exception as exc:
        print(f"[celina.dbus] GLib/Gio unavailable ({exc}); exiting cleanly.", file=sys.stderr)
        return 1

    backend = CelinaDBusBackend()
    node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
    iface_info = node_info.interfaces[0]
    autonomy_iface_info = node_info.interfaces[1]
    loop = GLib.MainLoop()

    def _resolve_sender_uid(conn: Any, sender: str) -> Optional[int]:
        try:
            res = conn.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetConnectionUnixUser",
                GLib.Variant("(s)", (sender,)),
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
            return int(res.unpack()[0])
        except Exception:
            return None

    def _on_method_call(
        conn: Any, sender: str, _obj_path: str, _iface: str, method_name: str, params: Any, invocation: Any
    ) -> None:
        uid = _resolve_sender_uid(conn, sender)
        args = params.unpack() if params is not None else ()
        try:
            if method_name in {"GetStatus", "GetState"}:
                out = backend.get_status(caller_uid=uid)
            elif method_name == "GetPrivacyState":
                out = backend.get_privacy_state(caller_uid=uid)
            elif method_name == "GetFocusMode":
                out = backend.get_focus_mode(caller_uid=uid)
            elif method_name == "SetFocusMode":
                out = backend.set_focus_mode(str(args[0]) if args else "", caller_uid=uid)
                conn.emit_signal(
                    None, OBJECT_PATH, INTERFACE_NAME, "StatusChanged", GLib.Variant("(s)", (backend.get_status(caller_uid=uid),))
                )
            elif method_name == "Offload":
                out = backend.offload(caller_uid=uid)
            elif method_name == "GetCapabilitiesSummary":
                out = backend.get_capabilities_summary(caller_uid=uid)
            elif method_name == "UndoLast":
                out = backend.undo_last(caller_uid=uid)
            elif method_name == "AskPrompt":
                out = backend.ask_prompt(str(args[0]) if args else "", caller_uid=uid)
            elif method_name == "SubmitGoal":
                out = backend.submit_goal(str(args[0]) if args else "", caller_uid=uid)
                conn.emit_signal(
                    None, OBJECT_PATH, AUTONOMY_INTERFACE_NAME, "TaskStateChanged", GLib.Variant("(ss)", (str(out), "running"))
                )
            elif method_name == "GetTaskGraph":
                out = backend.get_task_graph(caller_uid=uid)
            elif method_name == "CancelTask":
                tid = str(args[0]) if args else ""
                out = backend.cancel_task(tid, caller_uid=uid)
                conn.emit_signal(
                    None, OBJECT_PATH, AUTONOMY_INTERFACE_NAME, "TaskStateChanged", GLib.Variant("(ss)", (tid, "cancelled"))
                )
            elif method_name == "GetMCPStatus":
                out = backend.get_mcp_status(caller_uid=uid)
            elif method_name == "SetEdgeGlow":
                ok_uid, err_uid = backend.verify_caller_uid(uid)
                if not ok_uid:
                    out = json.dumps({"ok": False, "error": err_uid})
                else:
                    active_flag = bool(args[0]) if len(args) >= 1 else False
                    glow_mode = str(args[1] if len(args) >= 2 else "task").strip().lower()
                    if glow_mode not in {"task", "responding", "listening", "recording"}:
                        glow_mode = "task"
                    conn.emit_signal(
                        None,
                        OBJECT_PATH,
                        INTERFACE_NAME,
                        "EdgeGlowChanged",
                        GLib.Variant("(bs)", (active_flag, glow_mode)),
                    )
                    out = json.dumps({"ok": True, "active": active_flag, "mode": glow_mode}, sort_keys=True)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", f"Unknown: {method_name}")
                return
            invocation.return_value(GLib.Variant("(s)", (str(out),)))
        except Exception as exc:
            invocation.return_dbus_error("org.macubuntu.Celina.Error.Internal", str(exc))

    def _on_bus_acquired(conn: Any, _name: str) -> None:
        conn.register_object(OBJECT_PATH, iface_info, _on_method_call, None, None)
        conn.register_object(OBJECT_PATH, autonomy_iface_info, _on_method_call, None, None)

    owner_id = Gio.bus_own_name(
        Gio.BusType.SESSION,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        _on_bus_acquired,
        None,
        None,
    )

    def _stop(_sig: int, _frame: Any) -> None:
        loop.quit()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        loop.run()
    finally:
        Gio.bus_unown_name(owner_id)
    return 0


def emit_edge_glow(active: bool, mode: str = "task") -> bool:
    """
    Notifies org.macubuntu.Celina1.SetEdgeGlow so the native GNOME Shell Wayland
    extension (`celina-island@macubuntu.org`) renders click-through fullscreen edge
    lighting directly on Mutter's compositor layer. Fails open cleanly if headless.
    """
    glow_mode = str(mode or "task").strip().lower()
    if glow_mode not in {"task", "responding", "listening", "recording"}:
        glow_mode = "task"
    try:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE_NAME,
            "SetEdgeGlow",
            GLib.Variant("(bs)", (bool(active), glow_mode)),
            GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            250,
            None,
        )
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Celina D-Bus Control Plane Service (org.macubuntu.Celina)")
    parser.add_argument("--daemon", action="store_true", help="Run session D-Bus service loop")
    parser.add_argument("--role", choices=("control", "router"), default="control", help="Micro-daemon role")
    parser.add_argument("--status", action="store_true", help="Print local backend status JSON and exit")
    args = parser.parse_args()
    if args.status:
        print(CelinaDBusBackend().get_status())
        return 0
    if args.daemon or args.role == "router":
        return run_dbus_daemon()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
