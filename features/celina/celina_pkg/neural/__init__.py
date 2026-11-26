"""macubuntu celina_pkg.neural — Neural acceleration layer.

Provides NPU worker, adaptive neural backend launcher, and predictive
preloader for low-power on-device inference on commodity x86_64 hardware.
"""

from .npu_worker import SingleOwnerNPUWorker

__all__ = ["SingleOwnerNPUWorker"]
