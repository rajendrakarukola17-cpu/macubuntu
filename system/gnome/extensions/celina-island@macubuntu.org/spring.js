/**
 * macubuntu — spring.js
 * Frame-Locked Asymmetric Spring Engine (Zero Drift, Auto-Sleep).
 *
 * Drives width, height, and shader radius simultaneously on one Clutter.Timeline
 * driven by Mutter's compositor frame clock. Destroys itself the instant velocity
 * settles so idle cost is 0%.
 */

import Clutter from 'gi://Clutter';

export const SPRING_EXPAND = { stiffness: 340, damping: 22, mass: 1.0 };   // subtle Apple bounce
export const SPRING_COLLAPSE = { stiffness: 420, damping: 32, mass: 1.0 }; // crisp snap, no overshoot

export class SpringGroup {
    constructor(actor, onUpdate) {
        this._actor = actor;
        this._onUpdate = onUpdate;
        this._channels = new Map();
        this._timeline = null;
        this._lastMs = 0;
    }

    animateTo(targets, preset = SPRING_EXPAND, instant = false) {
        for (const [key, { from, to }] of Object.entries(targets)) {
            if (instant) {
                this._channels.set(key, { value: to, target: to, velocity: 0, preset });
            } else {
                const existing = this._channels.get(key);
                this._channels.set(key, {
                    value: existing ? existing.value : from,
                    target: to,
                    velocity: existing ? existing.velocity : 0,
                    preset,
                });
            }
        }

        if (instant) {
            this._emitValues();
            this.stop();
            return;
        }
        this._ensureRunning();
    }

    _ensureRunning() {
        if (this._timeline) return;
        this._lastMs = Date.now();
        // Driven by Mutter's compositor frame clock, not drift-prone GLib timers
        this._timeline = new Clutter.Timeline({
            actor: this._actor,
            duration: 10000,
            repeat_count: -1,
        });
        this._timeline.connect('new-frame', () => this._tick());
        this._timeline.start();
    }

    _tick() {
        const now = Date.now();
        // Clamp dt to 33ms so a momentary stall never explodes spring integration
        const dt = Math.min(0.033, Math.max(0.004, (now - this._lastMs) / 1000));
        this._lastMs = now;

        let allSettled = true;
        for (const ch of this._channels.values()) {
            const { stiffness, damping, mass } = ch.preset;
            const displacement = ch.value - ch.target;
            const accel = (-stiffness * displacement - damping * ch.velocity) / mass;
            ch.velocity += accel * dt;
            ch.value += ch.velocity * dt;

            if (Math.abs(ch.velocity) >= 0.25 || Math.abs(displacement) >= 0.25) {
                allSettled = false;
            } else {
                ch.value = ch.target;
                ch.velocity = 0;
            }
        }

        this._emitValues();
        if (allSettled) this.stop();
    }

    _emitValues() {
        const snapshot = {};
        for (const [k, ch] of this._channels.entries()) snapshot[k] = ch.value;
        this._onUpdate(snapshot);
    }

    stop() {
        if (this._timeline) {
            this._timeline.stop();
            this._timeline = null;
        }
    }

    destroy() {
        this.stop();
        this._channels.clear();
    }
}
