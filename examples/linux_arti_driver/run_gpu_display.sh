#!/usr/bin/env bash
# Boot the embedded GPU, render the driver self-test triangle, and keep the
# QEMU display open long enough for visual inspection.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

: "${INTEGRATION_CONFIG:?set INTEGRATION_CONFIG to the GPU integration profile}"
: "${QEMU_DISPLAY:=cocoa}"
: "${HOLD_AFTER_TEST:=30}"
: "${TIMEOUT:=45}"
: "${ARTI_DISPLAY:=1}"
: "${ARTI_DISPLAY_SOURCE:=guest-memory}"

export INTEGRATION_CONFIG QEMU_DISPLAY HOLD_AFTER_TEST TIMEOUT
export ARTI_DISPLAY ARTI_DISPLAY_SOURCE
exec "$SCRIPT_DIR/run_linux_test.sh"
