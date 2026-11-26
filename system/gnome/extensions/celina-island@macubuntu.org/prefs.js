/**
 * Celina Dynamic Island — GNOME Shell 48–51 Preferences Window (`prefs.js`)
 *
 * Uses Libadwaita (Adw.PreferencesPage / Adw.PreferencesGroup) bound directly
 * to `org.gnome.shell.extensions.celina-island` GSettings keys.
 */

import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const PANEL_POSITIONS = ['left', 'center', 'right'];
const BADGE_POSITIONS = ['left', 'right'];

export default class CelinaIslandPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();

        const page = new Adw.PreferencesPage({
            title: 'Celina Island',
            icon_name: 'preferences-desktop-display-symbolic',
        });
        window.add(page);

        const layoutGroup = new Adw.PreferencesGroup({
            title: 'Top Bar Placement & Geometry',
            description: 'Configure how the native Dynamic Island pill sits in GNOME Shell.',
        });
        page.add(layoutGroup);

        const posModel = Gtk.StringList.new(PANEL_POSITIONS);
        const posRow = new Adw.ComboRow({
            title: 'Panel Slot',
            subtitle: 'Horizontal slot in the GNOME top panel',
            model: posModel,
            selected: Math.max(0, PANEL_POSITIONS.indexOf(settings.get_string('panel-position'))),
        });
        posRow.connect('notify::selected', row => {
            const val = PANEL_POSITIONS[row.selected] ?? 'center';
            settings.set_string('panel-position', val);
        });
        layoutGroup.add(posRow);

        const scaleRow = new Adw.SpinRow({
            title: 'Pill Scale Factor',
            subtitle: 'Visual scale between 0.8x and 1.4x',
            digits: 2,
            adjustment: new Gtk.Adjustment({
                lower: 0.8,
                upper: 1.4,
                step_increment: 0.05,
                page_increment: 0.1,
                value: settings.get_double('pill-scale'),
            }),
        });
        settings.bind('pill-scale', scaleRow, 'value', Gio.SettingsBindFlags.DEFAULT);
        layoutGroup.add(scaleRow);

        const indicatorsGroup = new Adw.PreferencesGroup({
            title: 'Privacy Indicators & Focus Mode',
            description: 'State streamed from celina.service over session D-Bus (org.macubuntu.Celina).',
        });
        page.add(indicatorsGroup);

        const privacyRow = new Adw.SwitchRow({
            title: 'Show Privacy Dots',
            subtitle: 'Display live microphone, camera, and screencast indicator dots',
        });
        settings.bind('show-privacy-dots', privacyRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        indicatorsGroup.add(privacyRow);

        const focusRow = new Adw.SwitchRow({
            title: 'Show Focus Mode Badge',
            subtitle: 'Display active Focus Mode badge inside the island pill',
        });
        settings.bind('show-focus-badge', focusRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        indicatorsGroup.add(focusRow);

        const badgeModel = Gtk.StringList.new(BADGE_POSITIONS);
        const badgePosRow = new Adw.ComboRow({
            title: 'Focus Badge Side',
            subtitle: 'Alignment of the Focus badge relative to the island label',
            model: badgeModel,
            selected: Math.max(0, BADGE_POSITIONS.indexOf(settings.get_string('focus-badge-position'))),
        });
        badgePosRow.connect('notify::selected', row => {
            const val = BADGE_POSITIONS[row.selected] ?? 'right';
            settings.set_string('focus-badge-position', val);
        });
        indicatorsGroup.add(badgePosRow);
    }
}
