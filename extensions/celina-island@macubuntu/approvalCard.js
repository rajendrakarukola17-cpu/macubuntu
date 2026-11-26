/*
 * macubuntu — Celina Island GNOME 50 Approval Card Module
 * Exports /org/macubuntu/CelinaIsland on the session D-Bus, rendering an
 * interactive glass approval pill for Visual Intelligence actions and
 * FastMCP Tier-2 tool confirmations.
 */

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const DBUS_IFACE = `
<node>
  <interface name="org.macubuntu.CelinaIsland">
    <method name="RequestApproval">
      <arg type="s" direction="in" name="request_id"/>
      <arg type="s" direction="in" name="title"/>
      <arg type="s" direction="in" name="summary"/>
      <arg type="s" direction="in" name="action_label"/>
    </method>
    <signal name="ApprovalResolved">
      <arg type="s" name="request_id"/>
      <arg type="b" name="accepted"/>
    </signal>
  </interface>
</node>`;

export class CelinaApprovalController {
    constructor() {
        this._activeCard = null;
        this._timeoutId = 0;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(DBUS_IFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/macubuntu/CelinaIsland');
    }

    RequestApproval(requestId, title, summary, actionLabel) {
        this._dismissCurrent(null, false);

        const card = new St.BoxLayout({
            vertical: true,
            style_class: 'celina-island-card',
            reactive: true,
            opacity: 0,
            translation_y: -16,
        });

        card.add_child(new St.Label({
            text: title,
            style_class: 'celina-island-title',
        }));

        card.add_child(new St.Label({
            text: summary,
            style_class: 'celina-island-summary',
        }));

        const btnRow = new St.BoxLayout({
            style_class: 'celina-island-actions',
            x_expand: true,
        });

        const approveBtn = new St.Button({
            label: actionLabel || 'Approve',
            style_class: 'celina-btn-approve',
            can_focus: true,
            reactive: true,
        });
        approveBtn.connect('clicked', () => this._resolve(requestId, true));

        const denyBtn = new St.Button({
            label: 'Dismiss',
            style_class: 'celina-btn-deny',
            can_focus: true,
            reactive: true,
        });
        denyBtn.connect('clicked', () => this._resolve(requestId, false));

        btnRow.add_child(approveBtn);
        btnRow.add_child(denyBtn);
        card.add_child(btnRow);

        const monitor = Main.layoutManager.primaryMonitor;
        Main.layoutManager.addChrome(card, { affectsInputRegion: true });
        card.set_position(
            Math.round(monitor.x + (monitor.width - 380) / 2),
            monitor.y + Main.panel.height + 10
        );
        this._activeCard = card;

        card.easeAsync({
            opacity: 255,
            translation_y: 0,
            duration: 220,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        }).catch(() => {});

        // Auto-dismiss after 45s using GNOME 50 one-shot GLib timer
        this._timeoutId = GLib.timeout_add_seconds_once(GLib.PRIORITY_DEFAULT, 45, () => {
            this._timeoutId = 0;
            this._resolve(requestId, false);
        });
    }

    _resolve(requestId, accepted) {
        this._dbus.emit_signal(
            'ApprovalResolved',
            new GLib.Variant('(sb)', [requestId, accepted])
        );
        this._dismissCurrent(requestId, accepted);
    }

    _dismissCurrent() {
        if (this._timeoutId) {
            GLib.Source.remove(this._timeoutId);
            this._timeoutId = 0;
        }
        if (!this._activeCard)
            return;

        const card = this._activeCard;
        this._activeCard = null;
        card.easeAsync({
            opacity: 0,
            translation_y: -12,
            duration: 160,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
        }).catch(() => {}).finally(() => {
            Main.layoutManager.removeChrome(card);
            card.destroy();
        });
    }

    destroy() {
        this._dismissCurrent();
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
    }
}
