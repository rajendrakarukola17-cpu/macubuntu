"""Celina AI (Celly) - Apple Intelligence-Style Edge Lighting Engine (v4.2)
Native GTK3 + Cairo RGBA composited aurora animations (0% CPU & 0 MB extra RAM when idle):
  1. Dynamic Island Pill Glow: flowing iridescent spectrum halo around the pill while
     listening, thinking, or responding.
  2. Full-Screen Edge Glow Overlay: click-through transparent fullscreen monitor border
     that shimmers around all 4 edges while Celina executes OS, LibreOffice, MCP, Skill,
     or Project Astra autonomous tasks, then fades out smoothly on completion.
"""

import math
import time
from typing import Any

try:
    import cairo  # type: ignore[import-not-found]
except ImportError:  # Headless CI / unit-test fallback for pure-math helpers
    cairo = None  # type: ignore[assignment]

try:
    import gi  # type: ignore[import-not-found]

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gdk, GLib, Gtk  # type: ignore[import-not-found]
except Exception:
    gi = None  # type: ignore[assignment]
    Gdk = GLib = None  # type: ignore[assignment]
    Gtk = None  # type: ignore[assignment]

EDGE_DIRTY_BORDER_PX = 36


def compute_edge_dirty_rects(
    width: int, height: int, border_px: int = EDGE_DIRTY_BORDER_PX,
) -> list[tuple[int, int, int, int]]:
    """Computes 4 non-overlapping edge rectangles (x, y, w, h) covering ONLY the screen perimeter.
    Using queue_draw_area() on these strips cuts fullscreen Cairo/Wayland pixel upload by >90%
    on 1080p/4K displays compared to invalidating the entire monitor surface every frame.
    """
    w = max(0, int(width))
    h = max(0, int(height))
    if w == 0 or h == 0:
        return []
    b = max(1, min(int(border_px), min(w, h) // 2))
    mid_h = max(0, h - 2 * b)
    rects: list[tuple[int, int, int, int]] = [
        (0, 0, w, b),          # Top strip
        (0, h - b, w, b),      # Bottom strip
    ]
    if mid_h > 0:
        rects.append((0, b, b, mid_h))          # Left strip (excluding top/bottom corners)
        rects.append((w - b, b, b, mid_h))      # Right strip (excluding top/bottom corners)
    return rects

# Authentic Apple Intelligence aurora palettes (R, G, B in 0..1)
_PALETTES: dict[str, list[tuple[float, float, float]]] = {
    # Signature Apple Intelligence iridescent flow (pink -> violet -> cyan -> coral-amber)
    "responding": [
        (0.96, 0.36, 0.68),  # Apple Pink / Magenta
        (0.66, 0.33, 0.97),  # Electric Violet
        (0.22, 0.74, 0.97),  # Sky Cyan
        (0.98, 0.57, 0.24),  # Warm Peach / Amber
        (0.96, 0.36, 0.68),  # Seamless wrap
    ],
    # Full-screen autonomous task execution (richer violet-cyan-magenta-gold aurora)
    "task": [
        (0.22, 0.74, 0.97),  # Cyan
        (0.66, 0.33, 0.97),  # Violet
        (0.95, 0.28, 0.58),  # Magenta-Rose
        (0.99, 0.62, 0.22),  # Warm Amber
        (0.22, 0.74, 0.97),
    ],
    # Voice listening (vivid Siri coral-magenta-violet pulse)
    "listening": [
        (0.96, 0.25, 0.44),  # Coral Rose
        (0.92, 0.38, 0.82),  # Orchid
        (0.55, 0.36, 0.96),  # Indigo Violet
        (0.98, 0.45, 0.35),  # Warm Coral
        (0.96, 0.25, 0.44),
    ],
    # Watch & Learn recording mode
    "recording": [
        (0.94, 0.27, 0.27),
        (0.98, 0.45, 0.18),
        (0.94, 0.27, 0.52),
        (0.94, 0.27, 0.27),
    ],
}


def _sample_palette(mode: str, t: float) -> tuple[float, float, float]:
    pal = _PALETTES.get(mode, _PALETTES["responding"])
    n = len(pal) - 1
    pos = (t % 1.0) * n
    idx = int(pos) % n
    frac = pos - int(pos)
    c1, c2 = pal[idx], pal[idx + 1]
    return (
        c1[0] + (c2[0] - c1[0]) * frac,
        c1[1] + (c2[1] - c1[1]) * frac,
        c1[2] + (c2[2] - c1[2]) * frac,
    )


def _rounded_rect_path(
    cr: Any, x: float, y: float, w: float, h: float, r: float,
) -> None:
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2.0, 0.0)
    cr.arc(x + w - r, y + h - r, r, 0.0, math.pi / 2.0)
    cr.arc(x + r, y + h - r, r, math.pi / 2.0, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3.0 * math.pi / 2.0)
    cr.close_path()


def _make_orbit_gradient(
    width: float, height: float, phase: float, mode: str, alpha: float,
) -> Any:
    """Creates a smoothly orbiting diagonal linear gradient across the bounding box."""
    cx, cy = width / 2.0, height / 2.0
    ang = phase * 2.0 * math.pi
    dx = math.cos(ang) * width * 0.58
    dy = math.sin(ang) * height * 0.58
    grad = cairo.LinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
    for stop_idx in range(5):
        frac = stop_idx / 4.0
        r, g, b = _sample_palette(mode, frac + phase)
        grad.add_color_stop_rgba(frac, r, g, b, alpha)
    return grad


def paint_apple_pill_glow(
    cr: Any,
    width: float,
    height: float,
    radius: float,
    phase: float,
    intensity: float,
    mode: str = "responding",
) -> None:
    """Paints an Apple Intelligence iridescent halo around the Dynamic Island rounded pill.
    Call inside a GTK draw callback with intensity in (0.0..1.0].
    """
    if intensity <= 0.01 or width < 20 or height < 20:
        return

    cr.save()
    pulse = 0.85 + 0.15 * math.sin(phase * 4.0 * math.pi)
    eff = max(0.0, min(1.0, intensity * pulse))

    # Multi-layer soft bloom (outer diffused halo -> mid glow -> bright crisp rim)
    layers = [
        (10.0, 0.18 * eff, 4.0),
        (5.5, 0.36 * eff, 2.5),
        (2.4, 0.92 * eff, 1.4),
    ]
    for stroke_w, alpha, inset in layers:
        grad = _make_orbit_gradient(width, height, phase, mode, alpha)
        cr.set_source(grad)
        cr.set_line_width(stroke_w)
        _rounded_rect_path(
            cr, inset, inset, width - 2.0 * inset, height - 2.0 * inset, max(6.0, radius - inset),
        )
        cr.stroke()

    cr.restore()


_GTK_WINDOW_BASE = Gtk.Window if Gtk is not None else object


class EdgeGlowOverlay(_GTK_WINDOW_BASE):  # type: ignore[misc]
    """Click-through fullscreen Apple Intelligence edge-lighting overlay.
    Lights up all 4 edges of the primary monitor while Celina executes desktop tasks,
    and automatically hides at 0% CPU once faded out.
    """

    def __init__(self):
        if Gtk is None or cairo is None:
            raise RuntimeError("GTK3 and PyCairo are required for EdgeGlowOverlay.")
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._phase = 0.0
        self._alpha = 0.0
        self._target_alpha = 0.0
        self._mode = "task"
        self._timer_id: int | None = None
        self._last_tick_ts: float = 0.0
        self._mon_w = 1920
        self._mon_h = 1080

        from . import config

        self.set_title(f"{config.get_assistant_name()} Edge Glow")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_app_paintable(True)
        self.set_resizable(False)
        self.stick()

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        self.connect("draw", self._on_draw)
        self.connect("realize", self._on_realize)
        self._sync_geometry()

    def _sync_geometry(self) -> None:
        display = Gdk.Display.get_default()
        if not display or display.get_n_monitors() == 0:
            return
        mon = display.get_primary_monitor() or display.get_monitor(0)
        if mon:
            geo = mon.get_geometry()
            self._mon_w = max(320, int(geo.width))
            self._mon_h = max(240, int(geo.height))
            self.set_default_size(self._mon_w, self._mon_h)
            self.set_size_request(self._mon_w, self._mon_h)
            self.move(int(geo.x), int(geo.y))

    def _make_click_through(self) -> None:
        """Sets an empty input region so ALL clicks & ydotool events pass right through!"""
        empty_region = cairo.Region()
        self.input_shape_combine_region(empty_region)
        gdk_win = self.get_window()
        if gdk_win is not None:
            gdk_win.input_shape_combine_region(empty_region, 0, 0)
            gdk_win.set_pass_through(True)

    def _on_realize(self, _widget) -> None:
        self._make_click_through()

    @staticmethod
    def _animations_enabled() -> bool:
        try:
            settings = Gtk.Settings.get_default()
            if settings is not None:
                return bool(settings.get_property("gtk-enable-animations"))
        except Exception:
            pass
        return True

    def _queue_edge_redraw(self) -> None:
        """Invalidates ONLY the 4 thin perimeter strips (>90% less pixel fill than full-screen redraw)."""
        for x, y, rw, rh in compute_edge_dirty_rects(self._mon_w, self._mon_h):
            self.queue_draw_area(x, y, rw, rh)

    def activate(self, mode: str = "task") -> None:
        """Smoothly fades in the full-screen Apple Intelligence edge lighting at native 60 FPS."""
        self._mode = mode
        self._target_alpha = 1.0
        # Notify native GNOME Shell Wayland compositor overlay via session D-Bus first
        try:
            from . import dbus_service

            dbus_service.emit_edge_glow(True, mode)
        except Exception:
            pass
        # On GNOME Wayland, normal GTK3 windows cannot stay always-on-top/fullscreen;
        # the GNOME Shell extension handles native Mutter composited rendering.
        import os as _os

        if _os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return
        self._sync_geometry()
        if not self.get_visible():
            self.show_all()
            self._make_click_through()
        if not self._animations_enabled():
            self._alpha = 1.0
            self._queue_edge_redraw()
            return
        if self._timer_id is None:
            self._last_tick_ts = time.monotonic()
            # Native 60 FPS (~16ms) delta-time normalized; auto-stops when faded out
            self._timer_id = GLib.timeout_add(16, self._tick)

    def deactivate(self) -> None:
        """Smoothly fades out the full-screen edge glow and hides window (0% CPU)."""
        self._target_alpha = 0.0
        try:
            from . import dbus_service

            dbus_service.emit_edge_glow(False, self._mode)
        except Exception:
            pass
        if not self._animations_enabled():
            self._alpha = 0.0
            self.hide()
            return
        if self._timer_id is None and self.get_visible():
            self._last_tick_ts = time.monotonic()
            self._timer_id = GLib.timeout_add(16, self._tick)

    def _tick(self) -> bool:
        now = time.monotonic()
        dt = max(0.001, min(0.05, now - self._last_tick_ts)) if self._last_tick_ts > 0 else 0.016
        self._last_tick_ts = now

        # Frame-rate-independent velocity (matches original smooth visual cadence at 60 FPS)
        self._phase = (self._phase + 0.255 * dt) % 1.0
        step = (2.7 if self._target_alpha > self._alpha else 2.1) * dt
        if abs(self._target_alpha - self._alpha) <= step:
            self._alpha = self._target_alpha
        elif self._target_alpha > self._alpha:
            self._alpha += step
        else:
            self._alpha -= step

        if self._alpha <= 0.005 and self._target_alpha == 0.0:
            self._alpha = 0.0
            self.hide()
            self._timer_id = None
            return False

        self._queue_edge_redraw()
        return True

    def _on_draw(self, _widget, cr: Any) -> bool:
        alloc = self.get_allocation()
        w, h = float(alloc.width), float(alloc.height)

        # Clear completely transparent background
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
        cr.paint()
        cr.restore()

        if self._alpha <= 0.01 or w < 50 or h < 50:
            return False

        pulse = 0.86 + 0.14 * math.sin(self._phase * 3.0 * math.pi)
        eff = self._alpha * pulse

        # 1. Soft feathered inner aura along all 4 screen edges (Apple Intelligence look)
        edge_depth = 26.0
        c_top = _sample_palette(self._mode, self._phase)
        c_right = _sample_palette(self._mode, self._phase + 0.25)
        c_bottom = _sample_palette(self._mode, self._phase + 0.50)
        c_left = _sample_palette(self._mode, self._phase + 0.75)

        edges = [
            (0.0, 0.0, 0.0, edge_depth, c_top, (0.0, 0.0, w, edge_depth)),
            (w, 0.0, w - edge_depth, 0.0, c_right, (w - edge_depth, 0.0, edge_depth, h)),
            (0.0, h, 0.0, h - edge_depth, c_bottom, (0.0, h - edge_depth, w, edge_depth)),
            (0.0, 0.0, edge_depth, 0.0, c_left, (0.0, 0.0, edge_depth, h)),
        ]
        for x0, y0, x1, y1, rgb, rect in edges:
            g = cairo.LinearGradient(x0, y0, x1, y1)
            g.add_color_stop_rgba(0.0, rgb[0], rgb[1], rgb[2], 0.42 * eff)
            g.add_color_stop_rgba(0.45, rgb[0], rgb[1], rgb[2], 0.14 * eff)
            g.add_color_stop_rgba(1.0, rgb[0], rgb[1], rgb[2], 0.0)
            cr.set_source(g)
            cr.rectangle(*rect)
            cr.fill()

        # 2. Flowing crisp iridescent perimeter strokes (outer glow + bright rim)
        for stroke_w, a, inset in ((12.0, 0.24 * eff, 4.0), (5.0, 0.55 * eff, 2.5), (2.2, 0.95 * eff, 1.2)):
            grad = _make_orbit_gradient(w, h, self._phase, self._mode, a)
            cr.set_source(grad)
            cr.set_line_width(stroke_w)
            _rounded_rect_path(cr, inset, inset, w - 2.0 * inset, h - 2.0 * inset, 14.0)
            cr.stroke()

        return False
