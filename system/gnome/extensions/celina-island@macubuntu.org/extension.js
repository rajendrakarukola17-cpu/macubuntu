/*
 * macubuntu — Native GNOME Shell 48/49/50/51 Extension: Celina Dynamic Island
 * Communicates with celina.service strictly over session D-Bus (org.macubuntu.Celina)
 * via the modular `./dbus.js` proxy client; zero business logic lives in Shell JS.
 */

import Cairo from 'cairo';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {CelinaDBusClient} from './dbus.js';

const EDGE_PALETTES = {
    responding: [
        [0.96, 0.36, 0.68],
        [0.66, 0.33, 0.97],
        [0.22, 0.74, 0.97],
        [0.98, 0.57, 0.24],
        [0.96, 0.36, 0.68],
    ],
    task: [
        [0.22, 0.74, 0.97],
        [0.66, 0.33, 0.97],
        [0.95, 0.28, 0.58],
        [0.99, 0.62, 0.22],
        [0.22, 0.74, 0.97],
    ],
    listening: [
        [0.96, 0.25, 0.44],
        [0.92, 0.38, 0.82],
        [0.55, 0.36, 0.96],
        [0.98, 0.45, 0.35],
        [0.96, 0.25, 0.44],
    ],
    recording: [
        [0.94, 0.27, 0.27],
        [0.98, 0.45, 0.18],
        [0.94, 0.27, 0.52],
        [0.94, 0.27, 0.27],
    ],
};

function samplePalette(mode, t) {
    const pal = EDGE_PALETTES[mode] || EDGE_PALETTES.responding;
    const n = pal.length - 1;
    const norm = ((t % 1.0) + 1.0) % 1.0;
    const pos = norm * n;
    const idx = Math.floor(pos) % n;
    const frac = pos - Math.floor(pos);
    const c1 = pal[idx];
    const c2 = pal[idx + 1];
    return [
        c1[0] + (c2[0] - c1[0]) * frac,
        c1[1] + (c2[1] - c1[1]) * frac,
        c1[2] + (c2[2] - c1[2]) * frac,
    ];
}

/**
 * Native GNOME Wayland (Mutter) click-through fullscreen Apple Intelligence Edge Glow.
 * Runs directly on Main.layoutManager.uiGroup so Mutter composites it cleanly over all
 * Wayland windows without being blocked by Wayland client positioning restrictions.
 */
class WaylandEdgeGlowOverlay {
    constructor() {
        this._phase = 0.0;
        this._alpha = 0.0;
        this._targetAlpha = 0.0;
        this._mode = 'responding';
        this._timerId = 0;
        this._pulseTimerId = 0;
        this._lastTickUs = 0;

        this._actor = new St.DrawingArea({
            reactive: false,
            can_focus: false,
            visible: false,
        });
        this._repaintId = this._actor.connect('repaint', area => this._onRepaint(area));
        Main.layoutManager.uiGroup.add_child(this._actor);
        this._syncMonitorGeometry();
    }

    _syncMonitorGeometry() {
        const mon = Main.layoutManager.primaryMonitor;
        if (!mon || !this._actor)
            return;
        this._actor.set_position(mon.x, mon.y);
        this._actor.set_size(mon.width, mon.height);
    }

    setGlow(active, mode = 'task') {
        if (this._pulseTimerId) {
            GLib.source_remove(this._pulseTimerId);
            this._pulseTimerId = 0;
        }
        if (active) {
            this._mode = EDGE_PALETTES[mode] ? mode : 'task';
            this._targetAlpha = 1.0;
            this._syncMonitorGeometry();
            this._actor.visible = true;
            this._ensureTick();
        } else {
            this._targetAlpha = 0.0;
            if (this._actor.visible)
                this._ensureTick();
        }
    }

    testPulse(seconds = 5) {
        this.setGlow(true, 'responding');
        this._pulseTimerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, seconds, () => {
            this._pulseTimerId = 0;
            this.setGlow(false, 'responding');
            return GLib.SOURCE_REMOVE;
        });
    }

    _ensureTick() {
        if (this._timerId)
            return;
        this._lastTickUs = GLib.get_monotonic_time();
        this._timerId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 20, () => this._tick());
    }

    _tick() {
        const nowUs = GLib.get_monotonic_time();
        const dt = Math.max(0.004, Math.min(0.05, (nowUs - this._lastTickUs) / 1e6));
        this._lastTickUs = nowUs;

        this._phase = (this._phase + 0.28 * dt) % 1.0;
        const speed = this._targetAlpha > this._alpha ? 3.2 : 2.2;
        const delta = speed * dt;
        if (Math.abs(this._targetAlpha - this._alpha) <= delta)
            this._alpha = this._targetAlpha;
        else if (this._targetAlpha > this._alpha)
            this._alpha += delta;
        else
            this._alpha -= delta;

        if (this._alpha <= 0.005 && this._targetAlpha === 0.0) {
            this._alpha = 0.0;
            this._actor.visible = false;
            this._timerId = 0;
            return GLib.SOURCE_REMOVE;
        }
        this._actor.queue_repaint();
        return GLib.SOURCE_CONTINUE;
    }

    _onRepaint(area) {
        const [w, h] = area.get_surface_size();
        const cr = area.get_context();
        try {
            cr.setOperator(Cairo.Operator.CLEAR);
            cr.paint();
            if (this._alpha <= 0.01 || w < 64 || h < 64)
                return;

            cr.setOperator(Cairo.Operator.OVER);
            const pulse = 0.85 + 0.15 * Math.sin(this._phase * 3.0 * Math.PI);
            const eff = this._alpha * pulse;
            const depth = 30.0;

            const cTop = samplePalette(this._mode, this._phase);
            const cRight = samplePalette(this._mode, this._phase + 0.25);
            const cBottom = samplePalette(this._mode, this._phase + 0.50);
            const cLeft = samplePalette(this._mode, this._phase + 0.75);

            const strips = [
                [0, 0, 0, depth, cTop, [0, 0, w, depth]],
                [w, 0, w - depth, 0, cRight, [w - depth, 0, depth, h]],
                [0, h, 0, h - depth, cBottom, [0, h - depth, w, depth]],
                [0, 0, depth, 0, cLeft, [0, 0, depth, h]],
            ];
            for (const [x0, y0, x1, y1, rgb, rect] of strips) {
                const g = new Cairo.LinearGradient(x0, y0, x1, y1);
                g.addColorStopRGBA(0.0, rgb[0], rgb[1], rgb[2], 0.55 * eff);
                g.addColorStopRGBA(0.45, rgb[0], rgb[1], rgb[2], 0.18 * eff);
                g.addColorStopRGBA(1.0, rgb[0], rgb[1], rgb[2], 0.0);
                cr.setSource(g);
                cr.rectangle(rect[0], rect[1], rect[2], rect[3]);
                cr.fill();
            }

            // Crisp glowing outer perimeter rim
            const ang = this._phase * 2.0 * Math.PI;
            const cx = w * 0.5;
            const cy = h * 0.5;
            const rad = Math.hypot(cx, cy);
            const rim = new Cairo.LinearGradient(
                cx + Math.cos(ang) * rad,
                cy + Math.sin(ang) * rad,
                cx - Math.cos(ang) * rad,
                cy - Math.sin(ang) * rad
            );
            for (const stop of [0.0, 0.25, 0.5, 0.75, 1.0]) {
                const rgb = samplePalette(this._mode, this._phase + stop);
                rim.addColorStopRGBA(stop, rgb[0], rgb[1], rgb[2], 0.92 * eff);
            }
            cr.setSource(rim);
            cr.setLineWidth(4.0);
            cr.rectangle(2.0, 2.0, w - 4.0, h - 4.0);
            cr.stroke();
        } finally {
            cr.$dispose();
        }
    }

    destroy() {
        if (this._timerId) {
            GLib.source_remove(this._timerId);
            this._timerId = 0;
        }
        if (this._pulseTimerId) {
            GLib.source_remove(this._pulseTimerId);
            this._pulseTimerId = 0;
        }
        if (this._actor) {
            if (this._repaintId) {
                this._actor.disconnect(this._repaintId);
                this._repaintId = 0;
            }
            const parent = this._actor.get_parent();
            if (parent)
                parent.remove_child(this._actor);
            this._actor.destroy();
            this._actor = null;
        }
    }
}

function spawnLocalBin(binName, args = []) {
    try {
        const binPath = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', binName]);
        Gio.Subprocess.new([binPath, ...args], Gio.SubprocessFlags.NONE);
    } catch (e) {
        Main.notify('Celina', `Could not launch ${binName}: ${e.message}`);
    }
}

const CelinaIslandButton = GObject.registerClass(
class CelinaIslandButton extends PanelMenu.Button {
    _init(settings, openPrefsCb, edgeGlow) {
        super._init(0.5, 'Celina Dynamic Island', false);
        this._settings = settings;
        this._openPrefsCb = openPrefsCb;
        this._edgeGlow = edgeGlow;

        this._pill = new St.BoxLayout({
            style_class: 'celina-island-pill',
            y_align: Clutter.ActorAlign.CENTER,
        });

        this._titleLabel = new St.Label({
            text: '✦ Celina',
            style_class: 'celina-island-label',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._focusBadge = new St.Label({
            text: '',
            style_class: 'celina-island-focus-badge',
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });
        this._micDot = new St.Label({
            text: '●',
            style_class: 'celina-dot-mic',
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });
        this._camDot = new St.Label({
            text: '●',
            style_class: 'celina-dot-cam',
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });
        this._screenDot = new St.Label({
            text: '●',
            style_class: 'celina-dot-screen',
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });

        this._pill.add_child(this._titleLabel);
        this._pill.add_child(this._focusBadge);
        this._pill.add_child(this._micDot);
        this._pill.add_child(this._camDot);
        this._pill.add_child(this._screenDot);
        this.add_child(this._pill);

        this._buildMenu();
        this._dbus = new CelinaDBusClient({
            onStatus: st => this._applyStatus(st),
            onPrivacy: p => this._applyPrivacyDots(Boolean(p.mic), Boolean(p.cam), Boolean(p.screen)),
            onTaskState: ev => this._applyTaskState(ev),
            onMCPAction: ev => this._applyMCPAction(ev),
            onEdgeGlow: ev => this._edgeGlow?.setGlow(Boolean(ev.active), ev.mode || 'task'),
            onOffline: () => this._statusItem.label.set_text('Status: celina.service offline'),
        });
    }

    _buildMenu() {
        const chatItem = new PopupMenu.PopupMenuItem('💬 Open Celina Chat (Text)');
        chatItem.connect('activate', () => spawnLocalBin('celina-island', ['--toggle']));
        this.menu.addMenuItem(chatItem);

        const voiceItem = new PopupMenu.PopupMenuItem('🎙️ Talk to Celina (Voice)');
        voiceItem.connect('activate', () => spawnLocalBin('celina-island', ['--voice']));
        this.menu.addMenuItem(voiceItem);

        const glowItem = new PopupMenu.PopupMenuItem('✨ Test Edge Lighting (5s)');
        glowItem.connect('activate', () => this._edgeGlow?.testPulse(5));
        this.menu.addMenuItem(glowItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._statusItem = new PopupMenu.PopupMenuItem('Status: connecting to org.macubuntu.Celina…', {
            reactive: false,
        });
        this.menu.addMenuItem(this._statusItem);
        this._autonomyItem = new PopupMenu.PopupMenuItem('Autonomy: idle (observe-only ready)', {
            reactive: false,
        });
        this.menu.addMenuItem(this._autonomyItem);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const focusSub = new PopupMenu.PopupSubMenuMenuItem('Focus Mode');
        for (const mode of ['off', 'work', 'coding', 'dnd', 'presentation']) {
            const item = new PopupMenu.PopupMenuItem(mode.toUpperCase());
            item.connect('activate', () => this._dbus?.setFocusMode(mode));
            focusSub.menu.addMenuItem(item);
        }
        this.menu.addMenuItem(focusSub);

        const offloadItem = new PopupMenu.PopupMenuItem('Offload Idle Local Model (Free RAM)');
        offloadItem.connect('activate', () => {
            this._dbus?.offload(res => {
                Main.notify('Celina Compute', res?.message || 'Local model offload requested.');
            });
        });
        this.menu.addMenuItem(offloadItem);

        const undoItem = new PopupMenu.PopupMenuItem('Undo Last Tier-2 File Action');
        undoItem.connect('activate', () => {
            this._dbus?.undoLast(res => {
                Main.notify('Celina Undo', res?.message || 'Undo processed.');
            });
        });
        this.menu.addMenuItem(undoItem);

        const refreshItem = new PopupMenu.PopupMenuItem('Refresh Daemon Status');
        refreshItem.connect('activate', () => this._dbus?.refresh());
        this.menu.addMenuItem(refreshItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const prefsItem = new PopupMenu.PopupMenuItem('Island Preferences…');
        prefsItem.connect('activate', () => this._openPrefsCb?.());
        this.menu.addMenuItem(prefsItem);
    }

    _applyStatus(st) {
        if (!st || typeof st !== 'object')
            return;
        const name = st.assistant || 'Celina';
        const mode = st.focus_mode || 'off';
        const evOk = st.evidence_chain_ok ? 'chain OK' : 'chain WARN';
        const backend = st.compute_backend ? ` · ${st.compute_backend}` : '';
        this._titleLabel.set_text(`✦ ${name}`);
        this._statusItem.label.set_text(
            `${name} v${st.version || '4.4'} · Focus: ${mode} · Audit: ${evOk}${backend}`
        );

        const showBadge = this._settings ? this._settings.get_boolean('show-focus-badge') : true;
        if (showBadge && mode !== 'off') {
            this._focusBadge.set_text(`[${mode}]`);
            this._focusBadge.visible = true;
        } else {
            this._focusBadge.visible = false;
        }
    }

    _applyPrivacyDots(mic, cam, screen) {
        const enabled = this._settings ? this._settings.get_boolean('show-privacy-dots') : true;
        this._micDot.visible = Boolean(enabled && mic);
        this._camDot.visible = Boolean(enabled && cam);
        this._screenDot.visible = Boolean(enabled && screen);
    }

    _applyTaskState(ev) {
        if (!ev || !this._autonomyItem)
            return;
        const shortId = String(ev.taskId || '').slice(0, 8);
        const status = String(ev.status || 'unknown');
        this._autonomyItem.label.set_text(`Autonomy: task ${shortId} -> ${status}`);
    }

    _applyMCPAction(ev) {
        if (!ev)
            return;
        const server = String(ev.server || 'mcp');
        const tool = String(ev.tool || 'action');
        if (this._focusBadge) {
            this._focusBadge.text = `MCP:${server}`;
            this._focusBadge.visible = true;
        }
        if (this._autonomyItem)
            this._autonomyItem.label.set_text(`MCP Action: ${server} · ${tool}`);
    }

    onWorkspaceOrWindowChanged() {
        this._dbus?.refresh();
    }

    destroy() {
        this._dbus?.destroy();
        this._dbus = null;
        super.destroy();
    }
});

export default class CelinaIslandExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._edgeGlow = new WaylandEdgeGlowOverlay();
        this._indicator = new CelinaIslandButton(
            this._settings,
            () => this.openPreferences(),
            this._edgeGlow
        );
        const pos = this._settings.get_string('panel-position') || 'center';
        const box = ['left', 'center', 'right'].includes(pos) ? pos : 'center';
        Main.panel.addToStatusArea(this.uuid, this._indicator, 0, box);

        this._wsSignal = global.workspace_manager.connect('active-workspace-changed', () => {
            this._indicator?.onWorkspaceOrWindowChanged();
        });
        this._focusSignal = global.display.connect('notify::focus-window', () => {
            this._indicator?.onWorkspaceOrWindowChanged();
        });
        this._monitorsSignal = Main.layoutManager.connect('monitors-changed', () => {
            this._edgeGlow?._syncMonitorGeometry();
        });
    }

    disable() {
        if (this._monitorsSignal) {
            Main.layoutManager.disconnect(this._monitorsSignal);
            this._monitorsSignal = null;
        }
        if (this._wsSignal) {
            global.workspace_manager.disconnect(this._wsSignal);
            this._wsSignal = null;
        }
        if (this._focusSignal) {
            global.display.disconnect(this._focusSignal);
            this._focusSignal = null;
        }
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
        if (this._edgeGlow) {
            this._edgeGlow.destroy();
            this._edgeGlow = null;
        }
        this._settings = null;
    }
}
