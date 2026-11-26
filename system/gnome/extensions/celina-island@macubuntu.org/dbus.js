/**
 * Celina Dynamic Island — Modular Session D-Bus Client (`dbus.js`)
 *
 * Wraps Gio.DBusProxy for `org.macubuntu.Celina` (`org.macubuntu.Celina1`).
 * Keeps all business logic in celina.service; the GNOME Shell extension only
 * renders state and forwards UI actions over the authenticated session bus.
 */

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const BUS_NAME = 'org.macubuntu.Celina';
export const OBJECT_PATH = '/org/macubuntu/Celina';
export const INTERFACE_NAME = 'org.macubuntu.Celina1';
export const AUTONOMY_INTERFACE_NAME = 'org.macubuntu.Celina.Autonomy';

export const AUTONOMY_IFACE_XML = `
<node>
  <interface name="${AUTONOMY_INTERFACE_NAME}">
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
</node>`;

export const CELINA_IFACE_XML = `
<node>
  <interface name="${INTERFACE_NAME}">
    <method name="GetStatus">
      <arg type="s" name="status_json" direction="out"/>
    </method>
    <method name="GetState">
      <arg type="s" name="status_json" direction="out"/>
    </method>
    <method name="GetPrivacyState">
      <arg type="s" name="privacy_json" direction="out"/>
    </method>
    <method name="SetFocusMode">
      <arg type="s" name="mode" direction="in"/>
      <arg type="s" name="result_json" direction="out"/>
    </method>
    <method name="Offload">
      <arg type="s" name="result_json" direction="out"/>
    </method>
    <method name="UndoLast">
      <arg type="s" name="result_json" direction="out"/>
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
</node>`;

const CelinaProxy = Gio.DBusProxy.makeProxyWrapper(CELINA_IFACE_XML);
const AutonomyProxy = Gio.DBusProxy.makeProxyWrapper(AUTONOMY_IFACE_XML);

export class CelinaDBusClient {
    constructor({onStatus, onPrivacy, onTaskState, onMCPAction, onEdgeGlow, onOffline} = {}) {
        this._onStatus = onStatus ?? null;
        this._onPrivacy = onPrivacy ?? null;
        this._onTaskState = onTaskState ?? null;
        this._onMCPAction = onMCPAction ?? null;
        this._onEdgeGlow = onEdgeGlow ?? null;
        this._onOffline = onOffline ?? null;
        this._proxy = null;
        this._autonomyProxy = null;
        this._signalIds = [];
        this._gObjectSignalIds = [];
        this._autonomySignalIds = [];
        this._retrySourceId = 0;
        this._destroyed = false;

        this._connectProxies();
    }

    _connectProxies() {
        if (this._destroyed)
            return;

        new CelinaProxy(
            Gio.DBus.session,
            BUS_NAME,
            OBJECT_PATH,
            (proxy, error) => {
                if (this._destroyed)
                    return;
                if (error || !proxy) {
                    this._onOffline?.(error);
                    this._scheduleRetry();
                    return;
                }
                this._proxy = proxy;
                this._signalIds.push(
                    proxy.connectSignal('StatusChanged', (_p, _s, [jsonStr]) => {
                        this._emitJson(this._onStatus, jsonStr);
                    })
                );
                this._signalIds.push(
                    proxy.connectSignal('PrivacyDotsChanged', (_p, _s, [mic, cam, screen]) => {
                        this._onPrivacy?.({mic, cam, screen});
                    })
                );
                this._signalIds.push(
                    proxy.connectSignal('EdgeGlowChanged', (_p, _s, [active, mode]) => {
                        this._onEdgeGlow?.({active: Boolean(active), mode: String(mode || 'task')});
                    })
                );
                this._gObjectSignalIds.push(
                    proxy.connect('notify::g-name-owner', () => {
                        if (this._destroyed)
                            return;
                        if (proxy.g_name_owner)
                            this.refresh();
                        else
                            this._onOffline?.(null);
                    })
                );
                if (proxy.g_name_owner)
                    this.refresh();
                else
                    this._onOffline?.(null);
            }
        );

        new AutonomyProxy(
            Gio.DBus.session,
            BUS_NAME,
            OBJECT_PATH,
            (proxy, error) => {
                if (this._destroyed || error || !proxy)
                    return;
                this._autonomyProxy = proxy;
                this._autonomySignalIds.push(
                    proxy.connectSignal('TaskStateChanged', (_p, _s, [taskId, status]) => {
                        this._onTaskState?.({taskId, status});
                    })
                );
                this._autonomySignalIds.push(
                    proxy.connectSignal('MCPActionStarted', (_p, _s, [server, tool]) => {
                        this._onMCPAction?.({server, tool});
                    })
                );
            }
        );
    }

    _scheduleRetry() {
        if (this._destroyed || this._retrySourceId)
            return;
        this._retrySourceId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
            this._retrySourceId = 0;
            if (!this._destroyed && !this._proxy)
                this._connectProxies();
            return GLib.SOURCE_REMOVE;
        });
    }

    _emitJson(cb, jsonStr) {
        if (!cb || !jsonStr)
            return;
        try {
            cb(JSON.parse(jsonStr));
        } catch (_e) {
            // Ignore malformed status frame
        }
    }

    refresh() {
        if (!this._proxy) {
            this._connectProxies();
            return;
        }
        this._proxy.GetStatusRemote((res, err) => {
            if (!err && res?.[0])
                this._emitJson(this._onStatus, res[0]);
            else
                this._onOffline?.(err);
        });
        this._proxy.GetPrivacyStateRemote((res, err) => {
            if (!err && res?.[0])
                this._emitJson(this._onPrivacy, res[0]);
        });
    }

    setEdgeGlow(active, mode = 'task', cb = null) {
        if (!this._proxy)
            return;
        this._proxy.SetEdgeGlowRemote(Boolean(active), String(mode || 'task'), (res, err) => {
            if (!err && res?.[0])
                this._emitJson(cb, res[0]);
        });
    }

    setFocusMode(mode, cb = null) {
        if (!this._proxy)
            return;
        this._proxy.SetFocusModeRemote(mode, (res, err) => {
            if (!err && res?.[0])
                this._emitJson(cb, res[0]);
            this.refresh();
        });
    }

    offload(cb = null) {
        if (!this._proxy)
            return;
        this._proxy.OffloadRemote((res, err) => {
            if (!err && res?.[0])
                this._emitJson(cb, res[0]);
            this.refresh();
        });
    }

    undoLast(cb = null) {
        if (!this._proxy)
            return;
        this._proxy.UndoLastRemote((res, err) => {
            if (!err && res?.[0])
                this._emitJson(cb, res[0]);
        });
    }

    submitGoal(goal, cb = null) {
        if (!this._autonomyProxy)
            return;
        this._autonomyProxy.SubmitGoalRemote(goal, (res, err) => {
            if (!err && res?.[0])
                cb?.(res[0]);
        });
    }

    getTaskGraph(cb = null) {
        if (!this._autonomyProxy)
            return;
        this._autonomyProxy.GetTaskGraphRemote((res, err) => {
            if (!err && res?.[0])
                this._emitJson(cb, res[0]);
        });
    }

    cancelTask(taskId, cb = null) {
        if (!this._autonomyProxy)
            return;
        this._autonomyProxy.CancelTaskRemote(taskId, (res, err) => {
            if (!err && res?.[0])
                this._emitJson(cb, res[0]);
        });
    }

    destroy() {
        this._destroyed = true;
        if (this._retrySourceId) {
            GLib.source_remove(this._retrySourceId);
            this._retrySourceId = 0;
        }
        if (this._proxy) {
            for (const id of this._signalIds)
                this._proxy.disconnectSignal(id);
            for (const id of this._gObjectSignalIds)
                this._proxy.disconnect(id);
        }
        if (this._autonomyProxy) {
            for (const id of this._autonomySignalIds)
                this._autonomyProxy.disconnectSignal(id);
        }
        this._signalIds = [];
        this._gObjectSignalIds = [];
        this._autonomySignalIds = [];
        this._proxy = null;
        this._autonomyProxy = null;
    }
}
