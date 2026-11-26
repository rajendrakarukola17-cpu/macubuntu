#!/usr/bin/env bash
# ============================================================================
# macubuntu — NPU / GPU / Vulkan / ROCm Hardware Compute Probe
#
# Discovers /dev/accel/accel* (Intel Core Ultra / AMD XDNA NPU) and
# /dev/dri/renderD* (NVIDIA CUDA / AMD ROCm+Vulkan / Intel Vulkan) and prints
# the decoupled inference routing table without mutating $HOME or ollama.service.
#
# Usage:
#   ./tools/detect-compute.sh [--json]
# ============================================================================
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPATH="$REPO_DIR/features/celina${PYTHONPATH:+:$PYTHONPATH}" \
    exec python3 -m celina_hardware.compute "$@"
