"""
celina_hardware — Structured OS Hardware Capability & Biometric Adapter Layer (Phase 8)
Decouples Celina from raw device files by querying standard Linux/GNOME subsystems:
  - UPower         -> battery & power profile
  - PipeWire       -> audio sink state & verified volume/mute control
  - NetworkManager -> Wi-Fi state & verified radio control
  - BlueZ          -> Bluetooth power & verified state transitions
  - fprintd / PAM  -> capability-gated fingerprint (zero template storage, password fallback)
  - IR Face / Howdy -> optional IR-camera-only convenience unlock (never for recovery/keys/payments)
  - FIDO2          -> high-assurance security keys for sensitive actions
  - GNOME Portals  -> screen/input/remote-desktop capability detection
  - systemd --user -> user service lifecycle & verification
"""

from .adapters import (
    BatteryChargeLimitAdapter,
    BlueZAdapter,
    GnomePortalAdapter,
    HardwareManager,
    NetworkManagerAdapter,
    PipeWireAdapter,
    PipeWireRoutingAdapter,
    PrivacyIndicatorAdapter,
    SystemdUserAdapter,
    UPowerAdapter,
    get_hardware,
)
from .biometrics import (
    AuthAssuranceTier,
    AuthDecision,
    AuthPolicyEngine,
    BiometricCapabilities,
    FIDO2Adapter,
    FingerprintAdapter,
    IRFaceAdapter,
)
from .backends import OpenVINONPUBackend, ROCmBackend, VulkanBackend
from .compute import (
    DEFAULT_NPU_ENDPOINT,
    DEFAULT_OLLAMA_ENDPOINT,
    ComputeAccelerator,
    ComputeTopology,
    detect_compute_topology,
    resolve_inference_endpoint,
)
from .detect_gpu import BackendDecision, select_backend, select_compute_backend
from .models import (
    AudioState,
    BatteryLimitState,
    BluetoothState,
    CapabilityStatus,
    HardwareActionResult,
    NetworkState,
    PortalState,
    PowerState,
    PrivacyIndicatorState,
    ServiceState,
)

__all__ = [
    "AudioState",
    "AuthAssuranceTier",
    "AuthDecision",
    "AuthPolicyEngine",
    "BackendDecision",
    "BatteryChargeLimitAdapter",
    "BatteryLimitState",
    "BiometricCapabilities",
    "BlueZAdapter",
    "BluetoothState",
    "CapabilityStatus",
    "ComputeAccelerator",
    "ComputeTopology",
    "DEFAULT_NPU_ENDPOINT",
    "DEFAULT_OLLAMA_ENDPOINT",
    "FIDO2Adapter",
    "FingerprintAdapter",
    "GnomePortalAdapter",
    "HardwareActionResult",
    "HardwareManager",
    "IRFaceAdapter",
    "NetworkManagerAdapter",
    "NetworkState",
    "OpenVINONPUBackend",
    "PipeWireAdapter",
    "PipeWireRoutingAdapter",
    "PortalState",
    "PowerState",
    "PrivacyIndicatorAdapter",
    "PrivacyIndicatorState",
    "ROCmBackend",
    "ServiceState",
    "SystemdUserAdapter",
    "UPowerAdapter",
    "VulkanBackend",
    "detect_compute_topology",
    "get_hardware",
    "resolve_inference_endpoint",
    "select_backend",
    "select_compute_backend",
]
