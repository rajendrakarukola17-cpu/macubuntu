#!/usr/bin/env bash
# ============================================================================
# Stage 70: Built-In Feature — Celina AI (Siri / Apple Intelligence Subsystem)
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[70] Built-In Feature: Celina AI (${CELINA_NAME} / ${CELINA_NICKNAME} | ENABLE_CELINA=$ENABLE_CELINA)"

if ! is_true "$ENABLE_CELINA"; then
    echo "    Skipping built-in Celina feature (ENABLE_CELINA=false)."
    exit 0
fi

bash "$REPO_DIR/features/celina/install.sh"
