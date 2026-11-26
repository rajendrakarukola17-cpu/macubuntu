"""
Celina AI - 60 FPS Apple-Style Face ID & Touch ID Dynamic Island Animator (v4.3)
Pure Cairo vector geometry + spring physics driven by REAL OS D-Bus signals:
  - System Bus  : `net.reactivated.Fprint.Device` (`VerifyFingerSelected`, `VerifyStatus`)
                  for live Touch ID scan / match / shake-on-fail across sudo, Polkit & approvals.
  - Session Bus : `org.gnome.ScreenSaver` (`ActiveChanged(false)`) for instant Dynamic Island
                  Face ID / Touch ID unlock confirmation when the desktop unlocks.

Design & Security Invariants:
  - Zero GIFs/Lottie/bitmaps — crisp HiDPI vector Cairo rendering at 60 FPS.
  - Strictly 0.0% idle CPU: frame ticker runs ONLY while an auth animation is active and
    removes itself automatically on collapse.
  - Never fakes or bypasses authentication; strictly reflects real OS/hardware outcomes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

BiometricKind = Literal["face", "touch"]
BiometricPhase = Literal["idle", "scanning", "success", "failure", "collapsing"]

# Apple Human Interface Colors (RGB 0..1)
COLOR_BLUE = (0.04, 0.52, 1.00)     # #0A84FF (Face ID scan)
COLOR_ROSE = (1.00, 0.22, 0.37)     # #FF375F (Touch ID ridge glow)
COLOR_GREEN = (0.19, 0.82, 0.35)    # #30D158 (Match checkmark)
COLOR_RED = (1.00, 0.27, 0.23)      # #FF453A (Mismatch shake)
COLOR_MUTED = (0.58, 0.62, 0.70)    # Inactive ridge track

FPRINTD_BUS = "net.reactivated.Fprint"
FPRINTD_DEV_IFACE = "net.reactivated.Fprint.Device"
SCREENSAVER_BUS = "org.gnome.ScreenSaver"
SCREENSAVER_PATH = "/org/gnome/ScreenSaver"
SCREENSAVER_IFACE = "org.gnome.ScreenSaver"


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _ease_out_cubic(t: float) -> float:
    t = _clamp01(t)
    return 1.0 - ((1.0 - t) ** 3)


def classify_fprintd_status(result: str, done: bool) -> str:
    """
    Classifies a real `net.reactivated.Fprint.Device.VerifyStatus(result, done)` signal.
    Returns: 'success' | 'failure' | 'retry' | 'ignore'.
    """
    code = (result or "").strip().lower()
    if code == "verify-match":
        return "success"
    if code in {"verify-no-match", "verify-disconnected", "verify-unknown-error"}:
        return "failure"
    if code.startswith("verify-retry") or code in {
        "verify-swipe-too-short",
        "verify-finger-not-centered",
        "verify-remove-and-retry",
    }:
        return "failure" if done else "retry"
    return "ignore"


@dataclass
class BiometricVisualFrame:
    """Pure numeric snapshot produced by BiometricAnimState for Cairo rendering & unit testing."""

    phase: BiometricPhase
    kind: BiometricKind
    expand: float          # 0.0 (collapsed) .. 1.0 (full squircle)
    scan_phase: float      # continuous radians while scanning
    ridge_fill: float      # 0.0 .. 1.0 Touch ID concentric illumination
    check_progress: float  # 0.0 .. 1.0 trim-path draw for checkmark / X
    shake_x: float         # horizontal damped spring offset in logical pixels
    color_rgb: tuple[float, float, float]
    label: str


class BiometricAnimState:
    """
    Deterministic 60 FPS animation state machine for Apple Face ID & Touch ID visuals.
    100% independent of GTK/Cairo so every curve and state transition is unit-testable.
    """

    def __init__(self) -> None:
        self.kind: BiometricKind = "face"
        self.phase: BiometricPhase = "idle"
        self.label: str = ""
        self._t: float = 0.0
        self._phase_t: float = 0.0
        self._expand: float = 0.0
        self._auto_resolve: Optional[tuple[float, bool]] = None

    @property
    def is_active(self) -> bool:
        return self.phase != "idle"

    def start(
        self,
        kind: BiometricKind = "face",
        *,
        label: str = "",
        auto_resolve_after: Optional[float] = None,
        auto_matched: bool = True,
    ) -> None:
        self.kind = "touch" if kind == "touch" else "face"
        self.phase = "scanning"
        self.label = label or ("Touch ID" if self.kind == "touch" else "Face ID")
        self._t = 0.0
        self._phase_t = 0.0
        self._expand = max(self._expand, 0.05)
        self._auto_resolve = (
            (max(0.15, float(auto_resolve_after)), bool(auto_matched))
            if auto_resolve_after is not None
            else None
        )

    def resolve(self, matched: bool, *, label: str = "") -> None:
        if self.phase == "idle":
            return
        self.phase = "success" if matched else "failure"
        self._phase_t = 0.0
        self._auto_resolve = None
        if label:
            self.label = label
        else:
            self.label = "Unlocked" if matched else "Try Again"

    def pulse_retry(self, label: str = "Try Again") -> None:
        """Brief visual nudge during a non-terminal fprintd retry without ending the scan."""
        if self.phase == "scanning":
            self.label = label
            self._phase_t = 0.0

    def reset(self) -> None:
        self.phase = "idle"
        self._t = 0.0
        self._phase_t = 0.0
        self._expand = 0.0
        self._auto_resolve = None
        self.label = ""

    def step(self, dt: float) -> bool:
        """Advances animation by `dt` seconds. Returns True while active, False once idle."""
        if self.phase == "idle":
            return False
        step_dt = max(0.0, min(0.1, float(dt)))
        self._t += step_dt
        self._phase_t += step_dt

        if self.phase == "scanning":
            self._expand = _ease_out_cubic(min(1.0, self._t / 0.24))
            if self._auto_resolve is not None:
                delay, matched = self._auto_resolve
                if self._t >= delay:
                    self.resolve(matched)
        elif self.phase == "success":
            self._expand = 1.0
            if self._phase_t >= 0.90:
                self.phase = "collapsing"
                self._phase_t = 0.0
        elif self.phase == "failure":
            self._expand = 1.0
            if self._phase_t >= 0.95:
                self.phase = "collapsing"
                self._phase_t = 0.0
        elif self.phase == "collapsing":
            self._expand = 1.0 - _ease_out_cubic(min(1.0, self._phase_t / 0.26))
            if self._phase_t >= 0.26:
                self.reset()
                return False

        return self.phase != "idle"

    def frame(self) -> BiometricVisualFrame:
        scan_phase = self._t * 4.2
        ridge_fill = 0.5 + 0.5 * math.sin(self._t * 3.8 - math.pi / 2.0)
        check_progress = 0.0
        shake_x = 0.0

        if self.phase == "success":
            color = COLOR_GREEN
            check_progress = _ease_out_cubic(min(1.0, self._phase_t / 0.34))
            ridge_fill = 1.0
        elif self.phase == "failure":
            color = COLOR_RED
            check_progress = _ease_out_cubic(min(1.0, self._phase_t / 0.28))
            # Classic Apple horizontal head-shake damped spring
            shake_x = 11.5 * math.exp(-6.2 * self._phase_t) * math.sin(34.0 * self._phase_t)
        elif self.phase == "collapsing":
            color = COLOR_GREEN if self.label == "Unlocked" else COLOR_BLUE
            check_progress = 1.0
        else:
            color = COLOR_ROSE if self.kind == "touch" else COLOR_BLUE

        return BiometricVisualFrame(
            phase=self.phase,
            kind=self.kind,
            expand=self._expand,
            scan_phase=scan_phase,
            ridge_fill=ridge_fill,
            check_progress=check_progress,
            shake_x=shake_x,
            color_rgb=color,
            label=self.label,
        )


def render_biometric_cairo(cr: Any, width: float, height: float, state: BiometricAnimState) -> None:
    """Draws crisp HiDPI Apple Face ID / Touch ID animation onto a GTK/Cairo context."""
    import cairo

    fr = state.frame()
    if fr.phase == "idle" or fr.expand <= 0.01:
        return

    cx = (width / 2.0) + fr.shake_x
    cy = (height / 2.0) - 6.0
    radius = min(width, height) * 0.30 * (0.65 + 0.35 * fr.expand)
    r, g, b = fr.color_rgb
    alpha = _clamp01(fr.expand)

    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    # Soft ambient biometric glow behind glyph
    pat = cairo.RadialGradient(cx, cy, radius * 0.15, cx, cy, radius * 1.45)
    pat.add_color_stop_rgba(0.0, r, g, b, 0.24 * alpha)
    pat.add_color_stop_rgba(1.0, r, g, b, 0.0)
    cr.set_source(pat)
    cr.arc(cx, cy, radius * 1.45, 0, 2.0 * math.pi)
    cr.fill()

    if fr.phase in {"success", "collapsing"} and fr.label == "Unlocked":
        _draw_success_ring_and_check(cr, cx, cy, radius, fr.check_progress, alpha)
    elif fr.phase == "failure":
        _draw_failure_ring_and_cross(cr, cx, cy, radius, fr.check_progress, alpha)
    elif fr.kind == "touch":
        _draw_touch_id_ridges(cr, cx, cy, radius, fr.ridge_fill, fr.scan_phase, alpha)
    else:
        _draw_face_id_glyph(cr, cx, cy, radius, fr.scan_phase, alpha)

    # Minimal crisp Apple caption underneath glyph
    if fr.label:
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11.5)
        ext = cr.text_extents(fr.label)
        cr.set_source_rgba(0.95, 0.96, 1.0, 0.90 * alpha)
        cr.move_to(cx - ext.width / 2.0, height - 6.0)
        cr.show_text(fr.label)

    cr.restore()


def _draw_face_id_glyph(cr: Any, cx: float, cy: float, rad: float, scan_phase: float, alpha: float) -> None:
    """Draws Apple's 4 rounded Face ID corner brackets + minimalist face + scanning beam."""
    r, g, b = COLOR_BLUE
    cr.set_line_width(3.0)
    cr.set_source_rgba(r, g, b, 0.95 * alpha)

    box = rad * 0.92
    arm = box * 0.42
    cr_r = box * 0.24

    # 4 rounded corner brackets
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x0, y0 = cx + sx * box, cy + sy * box
        cr.move_to(x0 - sx * arm, y0)
        cr.line_to(x0 - sx * cr_r, y0)
        cr.curve_to(x0, y0, x0, y0, x0, y0 - sy * cr_r)
        cr.line_to(x0, y0 - sy * arm)
        cr.stroke()

    # Minimalist eyes, nose hook & gentle smile
    cr.set_line_width(2.6)
    cr.set_source_rgba(0.92, 0.96, 1.0, 0.92 * alpha)
    eye_dx = box * 0.34
    eye_y1 = cy - box * 0.28
    eye_y2 = cy - box * 0.08
    for sx in (-1, 1):
        cr.move_to(cx + sx * eye_dx, eye_y1)
        cr.line_to(cx + sx * eye_dx, eye_y2)
        cr.stroke()

    # Nose
    cr.move_to(cx, cy - box * 0.24)
    cr.line_to(cx, cy + box * 0.06)
    cr.line_to(cx - box * 0.09, cy + box * 0.09)
    cr.stroke()

    # Smile arc
    cr.arc(cx, cy + box * 0.08, box * 0.34, 0.20 * math.pi, 0.80 * math.pi)
    cr.stroke()

    # Smooth vertical IR scan sweep line
    sweep_y = cy + math.sin(scan_phase) * (box * 0.72)
    cr.set_line_width(1.8)
    cr.set_source_rgba(0.32, 0.84, 1.0, 0.75 * alpha)
    cr.move_to(cx - box * 0.78, sweep_y)
    cr.line_to(cx + box * 0.78, sweep_y)
    cr.stroke()


def _draw_touch_id_ridges(
    cr: Any, cx: float, cy: float, rad: float, ridge_fill: float, scan_phase: float, alpha: float
) -> None:
    """Draws 6 concentric biometric fingerprint ridge arcs illuminating from center outward."""
    cr.set_line_width(2.5)
    ring_count = 6
    lit_rings = ridge_fill * ring_count

    for idx in range(ring_count):
        rr = rad * (0.20 + 0.14 * idx)
        # Subtle biometric gap offset per ridge
        gap_shift = 0.45 * idx + (0.15 * math.sin(scan_phase + idx))
        intensity = _clamp01(lit_rings - idx)
        mr, mg, mb = COLOR_MUTED
        rr_c, rg_c, rb_c = COLOR_ROSE
        col_r = mr + (rr_c - mr) * intensity
        col_g = mg + (rg_c - mg) * intensity
        col_b = mb + (rb_c - mb) * intensity
        cr.set_source_rgba(col_r, col_g, col_b, (0.45 + 0.55 * intensity) * alpha)

        # Draw two broken organic arcs per ring
        cr.arc(cx, cy, rr, gap_shift, gap_shift + math.pi * 0.82)
        cr.stroke()
        cr.arc(cx, cy, rr, gap_shift + math.pi * 1.05, gap_shift + math.pi * 1.88)
        cr.stroke()


def _draw_success_ring_and_check(cr: Any, cx: float, cy: float, rad: float, progress: float, alpha: float) -> None:
    r, g, b = COLOR_GREEN
    cr.set_line_width(3.4)
    cr.set_source_rgba(r, g, b, 0.96 * alpha)
    cr.arc(cx, cy, rad * 0.92, -math.pi / 2.0, -math.pi / 2.0 + 2.0 * math.pi * _clamp01(progress * 1.25))
    cr.stroke()

    # Trim-path animated checkmark (two segments: down-right then up-right)
    p0 = (cx - rad * 0.40, cy + rad * 0.02)
    p1 = (cx - rad * 0.10, cy + rad * 0.34)
    p2 = (cx + rad * 0.44, cy - rad * 0.28)
    t = _clamp01(progress)
    if t > 0.0:
        cr.set_line_width(3.8)
        cr.move_to(*p0)
        if t <= 0.42:
            f = t / 0.42
            cr.line_to(p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f)
        else:
            f = (t - 0.42) / 0.58
            cr.line_to(*p1)
            cr.line_to(p1[0] + (p2[0] - p1[0]) * f, p1[1] + (p2[1] - p1[1]) * f)
        cr.stroke()


def _draw_failure_ring_and_cross(cr: Any, cx: float, cy: float, rad: float, progress: float, alpha: float) -> None:
    r, g, b = COLOR_RED
    cr.set_line_width(3.4)
    cr.set_source_rgba(r, g, b, 0.96 * alpha)
    cr.arc(cx, cy, rad * 0.92, 0.0, 2.0 * math.pi)
    cr.stroke()

    arm = rad * 0.34 * _clamp01(progress)
    if arm > 0.5:
        cr.set_line_width(3.6)
        cr.move_to(cx - arm, cy - arm)
        cr.line_to(cx + arm, cy + arm)
        cr.stroke()
        cr.move_to(cx + arm, cy - arm)
        cr.line_to(cx - arm, cy + arm)
        cr.stroke()


class BiometricDBusObserver:
    """
    Zero-polling D-Bus observer connecting real OS authentication signals to Dynamic Island:
      1. `net.reactivated.Fprint.Device` (`VerifyFingerSelected`, `VerifyStatus`) -> live Touch ID
      2. `org.gnome.ScreenSaver` (`ActiveChanged(False)`) -> live unlock confirmation animation
    """

    def __init__(
        self,
        *,
        on_start: Callable[[BiometricKind, str], None],
        on_resolve: Callable[[bool, str], None],
        on_retry: Optional[Callable[[str], None]] = None,
        on_unlock_welcome: Optional[Callable[[BiometricKind], None]] = None,
        preferred_unlock_kind: Optional[Callable[[], BiometricKind]] = None,
        system_bus: Any = None,
        session_bus: Any = None,
    ):
        self._on_start = on_start
        self._on_resolve = on_resolve
        self._on_retry = on_retry
        self._on_unlock_welcome = on_unlock_welcome
        self._preferred_unlock_kind = preferred_unlock_kind or self._detect_unlock_kind
        self._sys_bus = system_bus
        self._ses_bus = session_bus
        self._sys_subs: list[int] = []
        self._ses_subs: list[int] = []

    @staticmethod
    def _detect_unlock_kind() -> BiometricKind:
        try:
            import celina_hardware

            caps = celina_hardware.get_hardware().describe_capabilities()
            bio = caps.get("biometrics") or {}
            if (bio.get("ir_face") or {}).get("ir_camera_detected"):
                return "face"
            fp = bio.get("fingerprint") or {}
            if fp.get("enrolled") or fp.get("enrolled_fingers"):
                return "touch"
        except Exception:
            pass
        return "face"

    def handle_fprintd_selected(self, finger_name: str = "") -> None:
        lbl = f"Touch ID ({finger_name})" if finger_name else "Touch ID"
        self._on_start("touch", lbl)

    def handle_fprintd_status(self, result: str, done: bool) -> None:
        outcome = classify_fprintd_status(result, done)
        if outcome == "success":
            self._on_resolve(True, "Unlocked")
        elif outcome == "failure":
            self._on_resolve(False, "Try Again")
        elif outcome == "retry" and self._on_retry is not None:
            self._on_retry("Hold Finger")

    def handle_screensaver_changed(self, is_locked: bool) -> None:
        if not is_locked and self._on_unlock_welcome is not None:
            self._on_unlock_welcome(self._preferred_unlock_kind())

    def start(self) -> bool:
        """Subscribes to System & Session D-Bus signals via Gio (0.0% idle CPU)."""
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except Exception:
            return False

        subscribed = False
        # 1. System bus: fprintd live Touch ID signals
        try:
            sbus = self._sys_bus or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._sys_bus = sbus
            self._sys_subs.append(
                sbus.signal_subscribe(
                    FPRINTD_BUS,
                    FPRINTD_DEV_IFACE,
                    "VerifyFingerSelected",
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    lambda _c, _s, _p, _i, _sig, params: self.handle_fprintd_selected(str(params.unpack()[0])),
                )
            )
            self._sys_subs.append(
                sbus.signal_subscribe(
                    FPRINTD_BUS,
                    FPRINTD_DEV_IFACE,
                    "VerifyStatus",
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    lambda _c, _s, _p, _i, _sig, params: self.handle_fprintd_status(
                        str(params.unpack()[0]), bool(params.unpack()[1])
                    ),
                )
            )
            subscribed = True
        except Exception:
            pass

        # 2. Session bus: GNOME ScreenSaver unlock event
        try:
            ubus = self._ses_bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._ses_bus = ubus
            self._ses_subs.append(
                ubus.signal_subscribe(
                    SCREENSAVER_BUS,
                    SCREENSAVER_IFACE,
                    "ActiveChanged",
                    SCREENSAVER_PATH,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    lambda _c, _s, _p, _i, _sig, params: self.handle_screensaver_changed(bool(params.unpack()[0])),
                )
            )
            subscribed = True
        except Exception:
            pass
        return subscribed

    def stop(self) -> None:
        if self._sys_bus is not None:
            for sid in self._sys_subs:
                try:
                    self._sys_bus.signal_unsubscribe(sid)
                except Exception:
                    pass
        self._sys_subs.clear()
        if self._ses_bus is not None:
            for sid in self._ses_subs:
                try:
                    self._ses_bus.signal_unsubscribe(sid)
                except Exception:
                    pass
        self._ses_subs.clear()
