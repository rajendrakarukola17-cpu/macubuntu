"""
Capability-Gated Biometrics & High-Assurance Auth Policy (Phase 8)
Strict Security Invariants:
  1. Celina NEVER captures, stores, or uploads fingerprint images, templates, or face frames.
     All biometric enrollment & matching live strictly inside OS services (fprintd/PAM/Howdy).
  2. Password fallback MUST ALWAYS remain enabled for recovery, boot, and policy changes.
  3. Ordinary RGB webcams are NOT trusted as secure face unlock; IR capability is probed explicitly.
     Face unlock is NEVER accepted for LUKS disk recovery, key export, payments, or account changes.
  4. FIDO2 security keys handle high-assurance sensitive actions.
  5. Bluetooth proximity is a convenience signal ONLY — NEVER accepted as authentication alone.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .adapters import ExecResult, RunnerFn, WhichFn, default_runner
from .models import CapabilityStatus


class AuthAssuranceTier(str, Enum):
    PASSWORD_RECOVERY = "password_recovery"      # Boot, disk encryption recovery, policy changes
    FIDO2_HIGH_ASSURANCE = "fido2_high_assurance"  # Key export, payments, account/security mutations
    LOCAL_CONVENIENCE = "local_convenience"      # Local lockscreen / sudo convenience (fprintd / IR face)
    PROXIMITY_SIGNAL_ONLY = "proximity_signal_only"  # Bluetooth proximity (NEVER sole auth)


@dataclass(frozen=True)
class BiometricCapabilities:
    fingerprint_status: CapabilityStatus
    fingerprint_enrolled: bool
    password_fallback_present: bool
    ir_camera_detected: bool
    howdy_status: CapabilityStatus
    fido2_status: CapabilityStatus
    fido2_devices: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    required_tier: AuthAssuranceTier
    reason: str
    accepted_methods: tuple[str, ...] = ()


# Known USB fingerprint sensor vendor substrings (for lsusb capability probing)
_FP_USB_HINTS = (
    "goodix", "synaptics", "elan", "validity", "fingerprint", "focaltech", "authentec", "egis",
)

# Physical camera driver allowlist — closes Threat T3 (v4l2loopback IR spoofing).
# Any video4linux device without a physical driver symlink resolving to one of these
# is treated as virtual and NEVER added to ir_devices.
_PHYSICAL_CAM_DRIVERS = frozenset({
    "uvcvideo", "intel-ipu6", "ipu6-isys", "intel-ipu7", "amd-isp", "rkisp1", "unicam",
})
_VIRTUAL_CAM_RE = re.compile(
    r"\b(v4l2loopback|v4l2loopback_dc1394|vivid|akvcam|droidcam|scrcpy|dummy|virtual)\b",
    re.IGNORECASE,
)


def _resolve_physical_video_driver(dev: Path) -> tuple[bool, str]:
    """Fail-closed physical camera verification (closes Threat T3 v4l2loopback spoofing).

    Returns (is_physical, driver_name_or_reason).
    """
    try:
        real_dev = os.path.realpath(dev)
    except OSError:
        return False, "unreadable"
    # Reject /sys/devices/virtual/... outright (where v4l2loopback lives on real Linux!)
    if "/devices/virtual/" in real_dev:
        return False, "virtual"
    driver_link = dev / "device" / "driver"
    if not driver_link.is_symlink():
        return False, ""
    try:
        driver_name = Path(os.readlink(driver_link)).name.lower()
    except OSError:
        return False, ""
    if not driver_name or _VIRTUAL_CAM_RE.search(driver_name):
        return False, driver_name
    if driver_name not in _PHYSICAL_CAM_DRIVERS:
        return False, driver_name
    return True, driver_name


class FingerprintAdapter:
    """Probes fprintd/libfprint + PAM password fallback; zero template access in Celina."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def probe(self, username: Optional[str] = None, pam_dir: Path = Path("/etc/pam.d")) -> dict[str, Any]:
        # Resolve the target user: when running as root via sudo, SUDO_USER is the
        # real user; never fall back to pwd.getpwuid(os.getuid()) which returns "root".
        if username is None:
            if os.geteuid() == 0:
                username = os.environ.get("SUDO_USER") or os.environ.get("USER") or getpass.getuser()
            else:
                username = os.environ.get("USER") or getpass.getuser()
        user = username
        usb_hint = False
        if self._which("lsusb"):
            ls = self._run(["lsusb"], 3.0)
            if ls.ok:
                low = ls.stdout.lower()
                usb_hint = any(h in low for h in _FP_USB_HINTS)

        pam_password_ok = False
        common_auth = pam_dir / "common-auth"
        if common_auth.exists():
            try:
                pam_txt = common_auth.read_text(encoding="utf-8", errors="ignore")
                pam_auth_re = re.compile(
                    r"^\s*auth\s+(?:required|sufficient|requisite|\[[^]]*success=[0-9][^]]*])\s+pam_unix\.so\b"
                )
                pam_password_ok = any(
                    pam_auth_re.search(ln.split("#", 1)[0])
                    for ln in pam_txt.splitlines()
                )
            except OSError:
                pam_password_ok = False

        if not self._which("fprintd-list"):
            return {
                "status": CapabilityStatus.NOT_INSTALLED.value,
                "hardware_detected": usb_hint,
                "enrolled": False,
                "password_fallback_enabled": pam_password_ok,
                "stores_templates_in_celina": False,
                "detail": "fprintd not installed (run: sudo apt install fprintd libpam-fprintd)",
            }

        res = self._run(["fprintd-list", user], 4.0)
        if not res.ok:
            unsupported = "no devices available" in (res.stdout + res.stderr).lower()
            return {
                "status": CapabilityStatus.UNSUPPORTED.value if unsupported else CapabilityStatus.INSTALLED_INACTIVE.value,
                "hardware_detected": usb_hint and not unsupported,
                "enrolled": False,
                "password_fallback_enabled": pam_password_ok,
                "stores_templates_in_celina": False,
                "detail": (res.stderr or res.stdout or "fprintd unavailable")[:160],
            }

        low_out = res.stdout.lower()
        has_finger_line = any(
            ln.strip().startswith("-") and "finger" in ln.lower() for ln in res.stdout.splitlines()
        )
        enrolled = ("no fingers" not in low_out) and (has_finger_line or "fingers enrolled" in low_out)
        return {
            "status": CapabilityStatus.HEALTHY.value if enrolled else CapabilityStatus.INSTALLED_INACTIVE.value,
            "hardware_detected": True,
            "enrolled": enrolled,
            "password_fallback_enabled": pam_password_ok,
            "stores_templates_in_celina": False,
            "detail": "OS fprintd enrolled" if enrolled else "fprintd ready (no fingers enrolled yet; run fprintd-enroll)",
        }


class IRFaceAdapter:
    """
    Capability probe for IR camera + optional OS PAM face unlock (e.g. Howdy).
    Strictly warns against ordinary RGB webcams and NEVER uploads/stores frames.
    """

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def probe(self, sys_video_dir: Path = Path("/sys/class/video4linux")) -> dict[str, Any]:
        ir_devices: list[str] = []
        rgb_devices: list[str] = []
        ir_name_re = re.compile(r"\b(ir|infrared|depth|windows\s*hello)\b", re.IGNORECASE)
        if sys_video_dir.exists():
            for dev in sorted(sys_video_dir.glob("video*")):
                name_f = dev / "name"
                try:
                    name = name_f.read_text(encoding="utf-8", errors="ignore").strip() if name_f.exists() else dev.name
                except OSError:
                    name = dev.name
                # Resolve device driver via /sys/class/video4linux/<dev>/device/driver
                driver_link = dev / "device" / "driver"
                driver_name = ""
                if driver_link.is_symlink():
                    try:
                        driver_name = Path(os.readlink(driver_link)).name.lower()
                    except OSError:
                        driver_name = ""
                real_dev = ""
                try:
                    real_dev = os.path.realpath(dev)
                except OSError:
                    pass
                # Hard-reject virtual devices: under /sys/devices/virtual/ OR driver
                # matches virtual pattern — skip entirely, never add to any list.
                if "/devices/virtual/" in real_dev or _VIRTUAL_CAM_RE.search(driver_name or name):
                    continue
                # Devices without a physical driver allowlist match are RGB (not IR).
                # v4l2loopback on real Linux has no driver symlink → lands here as RGB,
                # but its name won't match the IR name regex so it won't be IR.
                is_physical = driver_name in _PHYSICAL_CAM_DRIVERS
                if ir_name_re.search(name) and is_physical:
                    ir_devices.append(f"{dev.name}: {name} (driver={driver_name or 'unknown'})")
                else:
                    rgb_devices.append(f"{dev.name}: {name} (driver={driver_name or 'unknown'})")

        howdy_installed = bool(self._which("howdy"))
        if not ir_devices:
            return {
                "status": CapabilityStatus.UNSUPPORTED.value,
                "ir_camera_detected": False,
                "rgb_webcam_only": bool(rgb_devices),
                "howdy_installed": howdy_installed,
                "uploads_frames": False,
                "detail": (
                    "Only ordinary RGB webcam detected (spoofable; IR camera strongly required for face unlock)."
                    if rgb_devices
                    else "No IR camera detected."
                ),
            }

        return {
            "status": CapabilityStatus.HEALTHY.value if howdy_installed else CapabilityStatus.NOT_INSTALLED.value,
            "ir_camera_detected": True,
            "ir_devices": ir_devices,
            "howdy_installed": howdy_installed,
            "uploads_frames": False,
            "detail": "IR camera + Howdy available (convenience unlock only; never for LUKS/keys/payments)"
            if howdy_installed
            else "IR camera detected; optional OS PAM face unlock (Howdy) not installed.",
        }


class FIDO2Adapter:
    """Probes FIDO2 / U2F security keys for high-assurance sensitive actions."""

    def __init__(self, runner: RunnerFn = default_runner, which: WhichFn = shutil.which):
        self._run = runner
        self._which = which

    def probe(self) -> dict[str, Any]:
        if not self._which("fido2-token"):
            return {
                "status": CapabilityStatus.NOT_INSTALLED.value,
                "tokens_present": False,
                "devices": [],
                "detail": "fido2-tools not installed (install: sudo apt install fido2-tools libpam-u2f)",
            }
        res = self._run(["fido2-token", "-L"], 3.0)
        if not res.ok:
            return {
                "status": CapabilityStatus.FAILED.value,
                "tokens_present": False,
                "devices": [],
                "detail": res.stderr or "fido2-token failed",
            }
        devs = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
        return {
            "status": CapabilityStatus.HEALTHY.value if devs else CapabilityStatus.INSTALLED_INACTIVE.value,
            "tokens_present": bool(devs),
            "devices": devs,
            "detail": f"{len(devs)} FIDO2 key(s) detected" if devs else "fido2-tools ready (insert FIDO2 security key)",
        }


class AuthPolicyEngine:
    """
    Enforces strict multi-tier authentication policy:
      - Password:           recovery / boot / disk encryption / policy changes
      - Fingerprint / IR:   convenient local unlock ONLY (with mandatory password fallback)
      - FIDO2 key:          high-assurance sensitive actions (key export, payment, account change)
      - Bluetooth proximity: convenience signal ONLY — NEVER sole authentication
    """

    _PASSWORD_ONLY_OPS = frozenset({
        "disk_encryption_recovery",
        "luks_recovery",
        "boot_unlock",
        "security_policy_change",
        "pam_config_change",
        "password_reset",
    })
    _HIGH_ASSURANCE_OPS = frozenset({
        "key_export",
        "payment",
        "account_change",
        "approve_quarantined_skill",
        "install_community_skill",
        "host_shell_override",
        "credential_export",
    })
    _LOCAL_UNLOCK_OPS = frozenset({
        "screen_unlock",
        "local_sudo",
        "polkit_local",
        "assistant_unlock",
    })

    def evaluate(
        self,
        operation: str,
        presented_method: str,
        *,
        password_fallback_configured: bool = False,
        ir_camera_verified: bool = False,
    ) -> AuthDecision:
        op = (operation or "").strip().lower()
        method = (presented_method or "").strip().lower()

        # Resolve the operation tier FIRST. Method constraints only NARROW accepted
        # methods — they never widen beyond what the operation tier permits.
        if op in self._PASSWORD_ONLY_OPS:
            tier, accepted = AuthAssuranceTier.PASSWORD_RECOVERY, ("password",)
        elif op in self._HIGH_ASSURANCE_OPS:
            tier, accepted = AuthAssuranceTier.FIDO2_HIGH_ASSURANCE, ("fido2", "password")
        elif op in self._LOCAL_UNLOCK_OPS:
            tier, accepted = AuthAssuranceTier.LOCAL_CONVENIENCE, ("password", "fido2", "fingerprint", "ir_face")
        else:
            tier, accepted = AuthAssuranceTier.FIDO2_HIGH_ASSURANCE, ("fido2", "password")

        # Invariant: Bluetooth proximity is NEVER sufficient authentication for ANY operation
        if method in {"bluetooth", "bluetooth_proximity", "bt_proximity"}:
            return AuthDecision(
                allowed=False,
                required_tier=tier,
                reason="Bluetooth proximity is a convenience signal only and is NEVER accepted as authentication alone.",
                accepted_methods=accepted,
            )

        # Invariant: Password fallback must remain configured before allowing biometric methods.
        # Default False = fail-closed: without a verified OS password, biometrics are refused.
        if method in {"fingerprint", "ir_face", "face"} and not password_fallback_configured:
            return AuthDecision(
                allowed=False,
                required_tier=AuthAssuranceTier.PASSWORD_RECOVERY,
                reason="Biometric unlock refused because mandatory OS password fallback is not verified.",
                accepted_methods=("password",),
            )

        # Ordinary RGB webcam face matching is never accepted.
        # When rejecting ir_face/face on LOCAL_CONVENIENCE, preserve fingerprint
        # alongside password and fido2 as still-acceptable methods.
        if method in {"face", "rgb_face", "webcam_face"} or (method == "ir_face" and not ir_camera_verified):
            preserved = tuple(m for m in accepted if m in ("password", "fido2", "fingerprint"))
            return AuthDecision(
                allowed=False,
                required_tier=tier,
                reason="Face unlock requires a verified IR-capable camera; ordinary RGB webcams are spoofable.",
                accepted_methods=preserved,
            )

        ok = method in accepted
        return AuthDecision(
            allowed=ok,
            required_tier=tier,
            reason="Accepted." if ok else f"'{method}' not permitted for '{op}'.",
            accepted_methods=accepted,
        )
