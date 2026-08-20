#!/usr/bin/env bash
# Boot an interactive Alpine Linux shell with the ARTI embedded device.
#
# The guest has full busybox + kmod (insmod/lsmod/rmmod) + devmem,
# so you can manually interact with the RTL device at 0x0B000000.
#
# Prerequisites:
#   - QEMU binary:   QEMU=/tmp/qemu-arti-build/qemu-system-aarch64
#   - Kernel Image:  KERNEL=/tmp/arti-linux-build/arch/arm64/boot/Image
#   - Alpine rootfs: ROOTFS=/tmp/arti-alpine.cpio.gz
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

QEMU="${QEMU:-/tmp/qemu-arti-build/qemu-system-aarch64}"
KERNEL="${KERNEL:-/tmp/arti-linux-build/arch/arm64/boot/Image}"
ROOTFS="${ROOTFS:-/tmp/arti-alpine.cpio.gz}"

[ -f "$QEMU" ]  || { echo "FAIL: QEMU not found at $QEMU"; exit 1; }
[ -f "$KERNEL" ] || { echo "FAIL: kernel not found at $KERNEL"; exit 1; }
[ -f "$ROOTFS" ] || { echo "FAIL: rootfs not found at $ROOTFS"; exit 1; }

echo "=== ARTI Interactive Shell (Alpine Linux) ==="
echo "  RTL device: MMIO 0x0B000000 (embedded Verilated model)"
echo "  Commands: insmod, lsmod, rmmod, devmem, dmesg"
echo "  Exit:     poweroff -f  or  Ctrl+A then X"
echo ""

exec "$QEMU" \
  -machine virt -cpu cortex-a53 -m 512M \
  -display none -monitor none -serial stdio \
  -kernel "$KERNEL" \
  -initrd "$ROOTFS" \
  -append "console=ttyAMA0"
