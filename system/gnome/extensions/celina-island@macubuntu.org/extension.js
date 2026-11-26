/*
 * macubuntu — Native GNOME Shell 46–51 Extension: Celina Dynamic Island
 * ESM module, Clutter.ShaderEffect GPU glow, SpringGroup frame-locked animations,
 * async Gio.DBusProxy session bus client, strict teardown on disable().
 *
 * Communicates with celina.service strictly over session D-Bus
 * (org.macubuntu.Celina). Zero business logic lives in Shell JS.
 */

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';
import { ExtensionState } from 'resource:///org/gnome/shell/misc/extensionUtils.js';

import { CelinaGlowEffect } from './shaderGlow.js';
import { SpringGroup, SPRING_EXPAND, SPRING_COLLAPSE } from './spring.js';
import { PerceptualChoreographer } from './choreography.js';
import { CelinaDBusClient } from './dbus.js';
import { CelinaApprovalController } from './approvalCard.js';

const STATE_PROFILES = {
    idle:      { speed: 0.0,  intensity: 0.0,  tint: [0.30, 0.65, 0.98] },
    listening: { speed: 0.35, intensity: 0.95, tint: [0.25, 0.90, 0.55] },
    thinking:  { speed: 0.70, intensity: 1.00, tint: [0.65, 0.35, 0.98] },
    speaking:  { speed: 0.25, intensity: 0.75, tint: [0.95, 0.95, 1.00] },
    approval:  { speed: 0.45, intensity: 1.00, tint: [0.98, 0.68, 0.18] },
};

function createSafeBlur(radiusPx = 18) {
    const blur = new Shell.BlurEffect({
        name: 'celina-blur',
        mode: Shell.BlurMode.BACKGROUND,
        brightness: 0.85,
    });
    // GNOME 46+ uses radius; GNOME 45 used sigma
    if ('radius' in blur) blur.radius = radiusPx;
    else if ('sigma' in blur) blur.sigma = Math.round(radiusPx * 0.5);
    return blur;
}

export default class CelinaIslandExtension extends Extension {
    enable() {
        this._liteMode = false;
        this._state = 'idle';
        this._phase = 0.0;
        this._width = 240;
        this._height = 36;
        this._radius = 18;
        this._speedFactor = 0.65; // CHOREOGRAPHY_SPEED_FACTOR default
        this._glowTimeline = null;
        this._dbusProxy = null;
        this._signalIds = [];
        this._approval = null;

        this._ifaceSettings = new Gio.Settings({ schema_id: 'org.gnome.desktop.interface' });

        // Guardrail: warn if basskibo dynamic-island is also active (no vertical stacking)
        const basskibo = Main.extensionManager?.lookup('dynamic-island@basskibo');
        this._basskiboActive = Boolean(basskibo && basskibo.state === ExtensionState.ACTIVE);

        this._pill = new St.BoxLayout({
            name: 'celina-dynamic-island',
            orientation: Clutter.Orientation.VERTICAL, // GNOME 48-51 compliant
            reactive: true,
            track_hover: true,
            width: this._width,
            height: this._height,
        });

        this._glowEffect = new CelinaGlowEffect();
        this._blurEffect = createSafeBlur(18);
        this._pill.add_effect(this._blurEffect);
        this._pill.add_effect(this._glowEffect);

        // Chat / Voice action buttons on the pill
        this._btnChat = new St.Button({
            label: 'Open Celina Chat (Text)',
            x_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
            can_focus: true,
            reactive: true,
        });
        this._btnChat.connect('clicked', () => {
            // Real action via D-Bus: enter work focus mode (chat-ready)
            this._dbusClient?.setFocusMode('work');
        });
        this._pill.add_child(this._btnChat);

        this._btnVoice = new St.Button({
            label: 'Talk to Celina (Voice)',
            x_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
            can_focus: true,
            reactive: true,
        });
        this._btnVoice.connect('clicked', () => {
            // Real action via D-Bus: signal listening/voice mode to the island
            this._dbusClient?.setEdgeGlow(true, 'listening');
        });
        this._pill.add_child(this._btnVoice);

        this._spring = new SpringGroup(this._pill, ({ width, height, radius }) => {
            if (width !== undefined) this._width = width;
            if (height !== undefined) this._height = height;
            if (radius !== undefined) this._radius = radius;
            this._pill.set_size(Math.round(this._width), Math.round(this._height));
            this._reposition();
            this._syncShader();
        });

        // Add above windows, below overview/lock screen; never blocks desktop chrome.
        // trackFullscreen: true auto-hides on fullscreen apps -> keeps direct scanout intact.
        Main.layoutManager.addChrome(this._pill, {
            affectsInputRegion: true,
            trackFullscreen: true,
        });

        this._pill.connect('button-press-event', () => {
            this._toggleExpanded();
            return Clutter.EVENT_STOP;
        });

        this._monitorsChangedId = Main.layoutManager.connect('monitors-changed', () => this._reposition());
        this._wsChangedId = global.workspace_manager?.connect('active-workspace-changed', () => this._onWindowChanged());
        this._focusChangedId = global.display?.connect('notify::focus-window', () => this._onWindowChanged());
        this._reposition();
        this._syncShader();

        // Perceptual choreography (global curve tightening + launch feedback + window morph)
        this._choreographer = new PerceptualChoreographer(this);
        this._choreographer.enable();

        this._connectDBusAsync();

        // Instantiate the GNOME 50 approval card controller
        this._approval = new CelinaApprovalController();
    }

    _onWindowChanged() {
        // Workspace / focus changes — refresh state from Celina daemon asynchronously
        this._dbusClient?.refresh?.();
    }

    _reposition() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._pill) return;
        const x = monitor.x + Math.round((monitor.width - this._width) / 2);
        // Never vertically stack below another island; offset horizontally if basskibo owns center
        const offsetX = this._basskiboActive ? 220 : 0;
        const y = monitor.y + 4;
        this._pill.set_position(x + offsetX, y);
    }

    _animationsAllowed() {
        return !this._liteMode && this._ifaceSettings.get_boolean('enable-animations');
    }

    _setState(newState) {
        const profile = STATE_PROFILES[newState] ?? STATE_PROFILES.idle;
        this._state = newState;

        if (!this._animationsAllowed() || profile.speed === 0.0) {
            this._stopGlowLoop();
            this._syncShader();
            return;
        }
        this._startGlowLoop(profile.speed);
    }

    _startGlowLoop(speed) {
        this._stopGlowLoop();
        let last = Date.now();
        this._glowTimeline = new Clutter.Timeline({
            actor: this._pill,
            duration: 10000,
            repeat_count: -1,
        });
        this._glowTimeline.connect('new-frame', () => {
            const now = Date.now();
            const dt = Math.min(0.05, (now - last) / 1000);
            last = now;
            this._phase = (this._phase + speed * dt) % 1.0;
            this._syncShader();
        });
        this._glowTimeline.start();
    }

    _stopGlowLoop() {
        if (this._glowTimeline) {
            this._glowTimeline.stop();
            this._glowTimeline = null;
        }
    }

    _syncShader() {
        const profile = STATE_PROFILES[this._state] ?? STATE_PROFILES.idle;
        this._glowEffect.setParams({
            width: this._width,
            height: this._height,
            radius: this._radius,
            phase: this._phase,
            intensity: this._liteMode ? Math.min(profile.intensity, 0.35) : profile.intensity,
            tint: profile.tint,
        });
        this._blurEffect.set_enabled(!this._liteMode && this._state !== 'idle');
    }

    // Handle MCP action approval receipts over D-Bus from celina.service
    // Called when Celina daemon notifies the island of an MCP tool invocation
    _applyMCPAction(server, tool) {
        // Visual acknowledgment: approval color pulse
        if (this._glowEffect) {
            const prevIntensity = this._glowEffect._intensity || 0;
            this._glowEffect.setParams({
                width: this._width,
                height: this._height,
                radius: this._radius,
                phase: this._phase,
                intensity: Math.max(prevIntensity, 0.85),
                tint: [0.98, 0.68, 0.18], // approval amber
            });
        }
        // Auto-revert after pulse
        if (this._clearLaunchFeedbackSourceId) {
            GLib.source_remove(this._clearLaunchFeedbackSourceId);
        }
        this._clearLaunchFeedbackSourceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 800, () => {
            this._clearLaunchFeedbackSourceId = 0;
            this._syncShader();
            return GLib.SOURCE_REMOVE;
        });
    }

    // Pill hooks wired by PerceptualChoreographer for 0ms launch feedback
    showLaunchFeedback(appName, appIcon) {
        // Guard: clear any existing pending timeout before scheduling a new one
        if (this._clearLaunchFeedbackSourceId) {
            GLib.source_remove(this._clearLaunchFeedbackSourceId);
            this._clearLaunchFeedbackSourceId = 0;
        }
        // Visual feedback: brief glow pulse on the pill
        const prevIntensity = this._glowEffect ? this._glowEffect._intensity : 0;
        if (this._glowEffect) {
            this._glowEffect.setParams({
                width: this._width,
                height: this._height,
                radius: this._radius,
                phase: this._phase,
                intensity: Math.max(prevIntensity, 0.9),
                tint: [0.25, 0.90, 0.55],
            });
        }
        // Auto-revert after a short pulse via GLib source
        this._clearLaunchFeedbackSourceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 600, () => {
            this._clearLaunchFeedbackSourceId = 0;
            this._syncShader();
            return GLib.SOURCE_REMOVE;
        });
    }

    clearLaunchFeedback() {
        if (this._clearLaunchFeedbackSourceId) {
            GLib.source_remove(this._clearLaunchFeedbackSourceId);
            this._clearLaunchFeedbackSourceId = 0;
        }
        this._syncShader();
    }

    _toggleExpanded() {
        const expanded = this._width < 400;
        const instant = !this._animationsAllowed();
        // Show chat/voice action buttons only when expanded; hide when collapsed
        this._btnChat?.set_visible(expanded);
        this._btnVoice?.set_visible(expanded);
        this._spring.animateTo(
            expanded
                ? { width: { from: this._width, to: 520 }, height: { from: this._height, to: 260 }, radius: { from: this._radius, to: 28 } }
                : { width: { from: this._width, to: 240 }, height: { from: this._height, to: 36 },  radius: { from: this._radius, to: 18 } },
            expanded ? SPRING_EXPAND : SPRING_COLLAPSE,
            instant
        );
    }

    _connectDBusAsync() {
        this._dbusClient = new CelinaDBusClient({
            onStatus: (status) => {
                try {
                    // StatusChanged carries the full status JSON from dbus_service.py.
                    // Extract focus_mode as the island state (falls back to idle profile if unknown).
                    const state = String((status && status.focus_mode) || "idle");
                    this._setState(state);
                } catch (_) { /* ignore malformed payload */ }
            },
            onActivity: (activity) => {
                // ActivityChanged — currently handled by _setState via state.profile
                this._setState(this._state);
            },
            onLiteMode: (enabled) => {
                this._liteMode = Boolean(enabled);
                this._setState(this._state);
                this._choreographer?.setLiteMode(this._liteMode);
            },
            onMCPAction: ({server, tool}) => {
                this._applyMCPAction(server, tool);
            },
        });
    }

    disable() {
        // Strict teardown required for lock-screen transitions & zero leaks
        this._stopGlowLoop();
        this.clearLaunchFeedback();
        this._spring?.destroy();
        this._spring = null;

        if (this._dbusClient) {
            this._dbusClient.destroy();
            this._dbusClient = null;
        }
        if (this._dbusProxy) {
            for (const id of this._signalIds) this._dbusProxy.disconnectSignal(id);
            this._dbusProxy = null;
        }

        this._approval?.destroy();
        this._approval = null;
        this._signalIds = [];

        if (this._monitorsChangedId) {
            Main.layoutManager.disconnect(this._monitorsChangedId);
            this._monitorsChangedId = null;
        }
        if (this._wsChangedId) {
            global.workspace_manager.disconnect(this._wsChangedId);
            this._wsChangedId = null;
        }
        if (this._focusChangedId) {
            global.display.disconnect(this._focusChangedId);
            this._focusChangedId = null;
        }

        if (this._pill) {
            Main.layoutManager.removeChrome(this._pill);
            this._pill.destroy();
            this._pill = null;
        }

        this._choreographer?.disable();
        this._choreographer = null;

        this._glowEffect = null;
        this._blurEffect = null;
        this._ifaceSettings = null;
    }
}
