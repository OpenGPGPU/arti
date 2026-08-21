#!/usr/bin/env bash
# Validate an ARTI Linux/QEMU integration without starting QEMU.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
. "$SCRIPT_DIR/integration_env.sh"
. "$SCRIPT_DIR/driver_preflight.sh"
arti_load_integration_config || { echo "FAIL: cannot load integration config"; exit 1; }

QEMU="${QEMU:-/tmp/qemu-arti-build/qemu-system-aarch64}"
KERNEL="${KERNEL:-/tmp/arti-linux-build/arch/arm64/boot/Image}"
LINUX_BUILD="${LINUX_BUILD:-/tmp/arti-linux-build}"
GPU_REFERENCE="${GPU_REFERENCE:-0}"
GPU_DRM_TEST="${GPU_DRM_TEST:-0}"
DRIVER_KO="${DRIVER_KO:-}"
DRIVER_DEPS="${DRIVER_DEPS:-}"
DRIVER_MANIFEST="${DRIVER_MANIFEST:-}"
if [ "$GPU_REFERENCE" = "1" ] || [ -n "$DRIVER_KO" ]; then
    SKIP_GENERIC_TEST="${SKIP_GENERIC_TEST:-1}"
else
    SKIP_GENERIC_TEST="${SKIP_GENERIC_TEST:-0}"
fi

fail=0
check_file() {
    local label="$1" path="$2"
    if [ ! -f "$path" ]; then
        echo "FAIL: $label not found at $path"
        fail=1
    fi
}

echo "=== ARTI Linux integration preflight ==="
echo "Profile : $INTEGRATION_CONFIG"
echo "RTL     : ${ARTI_RTL_TOP:-unknown}"
echo "QEMU    : $QEMU"
echo "Kernel  : $KERNEL"

check_file "RTL source" "${ARTI_RTL_SOURCE:-}"
check_file "QEMU" "$QEMU"
check_file "kernel Image" "$KERNEL"
check_file "kernel release" "$LINUX_BUILD/include/config/kernel.release"

if [ "$SKIP_GENERIC_TEST" != "1" ]; then
    check_file "generic driver" "$SCRIPT_DIR/arti_rtl_test.ko"
fi
if [ "$GPU_REFERENCE" = "1" ]; then
    if [ "$GPU_DRM_TEST" = "1" ]; then
        check_file "reference GPU DRM driver" "$SCRIPT_DIR/arti_gpu_drm.ko"
    else
        check_file "reference GPU probe driver" "$SCRIPT_DIR/arti_gpu_probe.ko"
    fi
fi

if [ "$fail" = "0" ]; then
    KERNEL_RELEASE_FILE="$LINUX_BUILD/include/config/kernel.release"
    KERNEL_RELEASE="$(tr -d '[:space:]' < "$KERNEL_RELEASE_FILE")"
    arti_driver_preflight || fail=1
fi

if [ "$fail" = "0" ]; then
    echo "=== ARTI Linux integration preflight PASS ==="
else
    echo "=== ARTI Linux integration preflight FAILED ==="
    exit 1
fi
