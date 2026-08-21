#!/usr/bin/env bash
#
# run.sh — One-click setup + run.
#
# Usage:
#   ./run.sh              # setup env + run automated end-to-end test
#   ./run.sh test         # same as above
#   ./run.sh interactive  # setup env + interactive busybox shell
#   ./run.sh debian       # setup env + full Debian dev environment
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-test}"

# Step 1: Ensure environment is set up
if [ ! -f /tmp/qemu-arti-build/qemu-system-aarch64 ] || \
   [ ! -f /tmp/arti-linux-build/arch/arm64/boot/Image ] || \
   [ ! -f "$SCRIPT_DIR/arti_rtl_test.ko" ] || \
   [ -n "$INTEGRATION_CONFIG" ] || \
   { [ "${GPU_REFERENCE:-0}" = "1" ] && \
     [ ! -f "$SCRIPT_DIR/arti_gpu_probe.ko" ]; } || \
   { [ "${GPU_DRM_TEST:-0}" = "1" ] && [ ! -f "$SCRIPT_DIR/arti_gpu_drm.ko" ]; }; then
    echo "=== Environment not ready, running setup_env.sh ==="
    bash "$SCRIPT_DIR/setup_env.sh"
fi

# Step 2: Run the requested mode
case "$MODE" in
    test)
        echo ""
        echo "=== Running automated end-to-end test ==="
        bash "$SCRIPT_DIR/run_linux_test.sh"
        ;;
    interactive)
        echo ""
        echo "=== Launching interactive shell ==="
        bash "$SCRIPT_DIR/run_interactive.sh"
        ;;
    debian)
        echo ""
        echo "=== Launching Debian dev environment ==="
        bash "$SCRIPT_DIR/run_debian.sh"
        ;;
    *)
        echo "Usage: $0 [test|interactive|debian]"
        exit 1
        ;;
esac
