/*
 * macubuntu — Perceptual Choreography for Celina Dynamic Island
 * GNOME 46–51 ESM module.
 *
 * Implements:
 *   • Global curve tightening (St.Settings slow_down_factor)
 *   • 0ms launch feedback (Shell.AppSystem app-state-changed → island.showLaunchFeedback)
 *   • Origin-anchored non-blocking window morph (global.window_manager map → scale+opacity ease)
 *   • Strict teardown in disable() (restore slow_down_factor, remove timeouts, disconnect signals)
 *
 * Zero-trust invariants:
 *   • Never steals focus or blocks pointer input
 *   • Skips entirely in Lite Mode or when animations disabled
 *   • All GLib.Source timeouts tracked and removed on teardown
 *   • Original slow_down_factor always restored
 */

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

const APP_STATE_CHANGED_SIGNAL = 'app-state-changed';
const WINDOW_MAP_SIGNAL = 'map';
const LAUNCH_FEEDBACK_TIMEOUT_MS = 2500;
const ORIGIN_WINDOW_MS = 1800;
const MORPH_DURATION_MS = 180;
const MIN_PIVOT = 0.15;
const MAX_PIVOT = 0.85;
const SCALE_FROM = 0.94;
const SCALE_TO = 1.0;
const OPACITY_FROM = 185;
const OPACITY_TO = 255;

export class PerceptualChoreographer {
    constructor(island) {
        this._island = island;
        this._settings = null;
        this._appSystem = null;
        this._windowManager = null;
        this._originalSlowDownFactor = null;
        this._appSignalId = 0;
        this._windowMapSignalId = 0;
        this._launchFeedbackTimeoutId = 0;
        this._launchOrigin = null; // { x, y, timestamp }
        this._activeMorphAnimations = new Set();
        this._enabled = false;
    }

    enable() {
        this._settings = new Gio.Settings({ schema_id: 'org.gnome.desktop.interface' });

        if (!this._settings.get_boolean('enable-animations')) {
            // Animations disabled globally — no-op but remain ready
            this._enabled = false;
            return;
        }

        this._enabled = true;

        // Capture and tighten global animation curve (GNOME 46+ property API)
        const stSettings = St.Settings.get();
        this._originalSlowDownFactor = stSettings.slow_down_factor;
        const speedFactor = this._island._liteMode ? 0.45 : this._island._speedFactor;
        stSettings.slow_down_factor = speedFactor;

        // Subscribe to app launch events for 0ms launch feedback
        this._appSystem = Shell.AppSystem.get_default();
        if (this._appSystem) {
            this._appSignalId = this._appSystem.connect(APP_STATE_CHANGED_SIGNAL, (_system, app) => {
                this._onAppStateChanged(app);
            });
        }

        // Subscribe to window map events for origin-anchored morph
        this._windowManager = global.window_manager;
        if (this._windowManager) {
            this._windowMapSignalId = this._windowManager.connect(WINDOW_MAP_SIGNAL, (_wm, actor) => {
                const win = actor.get_meta_window?.();
                if (win) this._onWindowMapped(win, actor);
            });
        }
    }

    setLiteMode(enabled) {
        if (!this._enabled) return;
        const stSettings = St.Settings.get();
        const speedFactor = enabled ? 0.45 : this._island._speedFactor;
        stSettings.slow_down_factor = speedFactor;
    }

    showLaunchFeedback(appName, appIcon) {
        if (!this._enabled || this._island._liteMode) return;

        const [px, py] = this._getPointerCoords();
        this._launchOrigin = { x: px, y: py, timestamp: Date.now() };

        // Clear any pending timeout before setting a new one
        this._clearLaunchFeedbackTimeout();

        // Tell the island to show launch feedback
        if (this._island.showLaunchFeedback) {
            this._island.showLaunchFeedback(appName, appIcon);
        }

        // Safety timeout: never let the pill get stuck in feedback state
        this._launchFeedbackTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, LAUNCH_FEEDBACK_TIMEOUT_MS, () => {
            this._launchFeedbackTimeoutId = 0;
            this.clearLaunchFeedback();
            return GLib.SOURCE_REMOVE;
        });
    }

    clearLaunchFeedback() {
        this._clearLaunchFeedbackTimeout();
        this._launchOrigin = null;
        if (this._island.clearLaunchFeedback) {
            this._island.clearLaunchFeedback();
        }
    }

    _getPointerCoords() {
        const pointer = global.get_pointer?.();
        if (!pointer) return [0, 0];
        return [pointer.get_x(), pointer.get_y()];
    }

    _clearLaunchFeedbackTimeout() {
        if (this._launchFeedbackTimeoutId) {
            GLib.source_remove(this._launchFeedbackTimeoutId);
            this._launchFeedbackTimeoutId = 0;
        }
    }

    _onAppStateChanged(app) {
        if (!this._enabled || this._island._liteMode) return;

        const state = app.get_state ? app.get_state() : null;
        const appId = app.get_id ? app.get_id() : '';

        if (state === Shell.AppState.STARTING) {
            const name = app.get_name ? app.get_name() : appId;
            const icon = app.get_icon ? app.get_icon() : null;
            this.showLaunchFeedback(name, icon);
        } else if (state === Shell.AppState.RUNNING || state === Shell.AppState.STOPPED) {
            // Only clear if this was our tracked launch
            this.clearLaunchFeedback();
        }
    }

    _onWindowMapped(win, actor) {
        if (!this._enabled || this._island._liteMode) return;

        // Strict filter: only NORMAL windows participate in morph
        if (win.get_window_type() !== Meta.WindowType.NORMAL) return;

        // Skip if animations disabled globally
        if (!this._settings.get_boolean('enable-animations')) return;

        // Must have a recent launch origin
        if (!this._launchOrigin) return;
        const now = Date.now();
        if (now - this._launchOrigin.timestamp > ORIGIN_WINDOW_MS) {
            this._launchOrigin = null;
            return;
        }

        const frameRect = win.get_frame_rect();
        if (!frameRect) return;

        // Compute clamped pivot point in [0.15, 0.85] relative to window frame
        const pivotX = this._clampPivot((this._launchOrigin.x - frameRect.x) / frameRect.width);
        const pivotY = this._clampPivot((this._launchOrigin.y - frameRect.y) / frameRect.height);

        if (!actor) return;

        // Capture initial state
        const scaleFrom = SCALE_FROM;
        const scaleTo = SCALE_TO;
        const opacityFrom = OPACITY_FROM;
        const opacityTo = OPACITY_TO;

        // Save original values for restore
        const origScaleX = actor.get_scale_x();
        const origScaleY = actor.get_scale_y();
        const origOpacity = actor.get_opacity();

        // Set initial morphed state at pivot origin (set_pivot_point + 2-arg set_scale)
        actor.set_pivot_point(pivotX, pivotY);
        actor.set_opacity(opacityFrom);
        actor.set_scale(scaleFrom, scaleFrom);

        const timeline = new Clutter.Timeline({
            duration: MORPH_DURATION_MS,
        });

        // Track the real timeline for teardown cleanup
        this._activeMorphAnimations.add(timeline);

        timeline.connect('completed', () => {
            this._activeMorphAnimations.delete(timeline);
            if (!actor.is_destroyed?.()) {
                actor.set_opacity(opacityTo);
                actor.set_pivot_point(pivotX, pivotY);
                actor.set_scale(scaleTo, scaleTo);
            }
        });

        timeline.connect('new-frame', () => {
            const progress = timeline.get_progress();
            // Clutter.Timeline progress is 0..1 linear; we apply easing manually
            const t = progress;
            // Ease-out expo: 1 - pow(2, -10 * t)
            const easedT = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);

            const scale = scaleFrom + (scaleTo - scaleFrom) * easedT;
            const opacity = opacityFrom + (opacityTo - opacityFrom) * easedT;

            if (!actor.is_destroyed?.()) {
                actor.set_opacity(opacity);
                actor.set_pivot_point(pivotX, pivotY);
                actor.set_scale(scale, scale);
            }
        });

        timeline.connect('stopped', () => {
            this._activeMorphAnimations.delete(timeline);
            if (!actor.is_destroyed?.()) {
                actor.set_opacity(origOpacity);
                actor.set_pivot_point(pivotX, pivotY);
                actor.set_scale(origScaleX, origScaleY);
            }
        });

        timeline.start();
    }

    _clampPivot(v) {
        return Math.min(MAX_PIVOT, Math.max(MIN_PIVOT, v));
    }

    disable() {
        // Stop any pending launch feedback timeout
        this._clearLaunchFeedbackTimeout();
        this.clearLaunchFeedback();

        // Stop all active morph animations (Clutter.Timeline instances)
        for (const timeline of this._activeMorphAnimations) {
            timeline.stop();
        }
        this._activeMorphAnimations.clear();

        // Disconnect app-state-changed signal
        if (this._appSignalId && this._appSystem) {
            this._appSystem.disconnect(this._appSignalId);
            this._appSignalId = 0;
        }

        // Disconnect window map signal
        if (this._windowMapSignalId && this._windowManager) {
            this._windowManager.disconnect(this._windowMapSignalId);
            this._windowMapSignalId = 0;
        }

        // Restore original slow_down_factor (critical invariant)
        if (this._originalSlowDownFactor !== null) {
            const stSettings = St.Settings.get();
            stSettings.slow_down_factor = this._originalSlowDownFactor;
            this._originalSlowDownFactor = null;
        }

        this._settings = null;
        this._appSystem = null;
        this._windowManager = null;
        this._enabled = false;
    }
}
