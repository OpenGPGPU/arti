#!/usr/bin/env bash
# Boot an interactive Alpine Linux shell with the ARTI embedded device.
#
# The guest has full busybox + kmod (insmod/lsmod/rmmod) + devmem,
# so you can manually interact with the RTL device at 0x0B000000.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
. "$SCRIPT_DIR/integration_env.sh"
ARTI_WORK="${ARTI_WORK:-$(arti_default_work_dir)}"

QEMU="${QEMU:-$ARTI_WORK/qemu-arti-build/qemu-system-aarch64}"
KERNEL="${KERNEL:-$ARTI_WORK/arti-linux-build/arch/arm64/boot/Image}"
ROOTFS="${ROOTFS:-$ARTI_WORK/arti-alpine.cpio.gz}"

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
