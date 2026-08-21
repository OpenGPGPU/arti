#!/usr/bin/env bash
# Boot a full Debian 12 (bookworm) ARM64 environment with the ARTI device.
#
# Features:
#   - Real Debian rootfs on 10GB qcow2 disk (persistent)
#   - Full systemd, apt, insmod/lsmod/rmmod, gcc, etc.
#   - ARTI embedded device at MMIO 0x0B000000
#   - Root login (password: arti)
#
# Prerequisites:
#   - QEMU binary:   QEMU=/tmp/qemu-arti-build/qemu-system-aarch64
#   - Kernel Image:  KERNEL=/tmp/arti-linux-build/arch/arm64/boot/Image
#   - Debian disk:   DISK=/tmp/arti-dev.qcow2
#   - Cloud-init:    CIDATA=/tmp/cloud-init.iso
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

QEMU="${QEMU:-/tmp/qemu-arti-build/qemu-system-aarch64}"
KERNEL="${KERNEL:-/tmp/arti-linux-build/arch/arm64/boot/Image}"
DISK="${DISK:-/tmp/arti-dev.qcow2}"
CIDATA="${CIDATA:-/tmp/cloud-init.iso}"
SSH_PORT="${SSH_PORT:-}"

find_free_port() {
    python3 - <<'PY'
import socket

sock = socket.socket()
try:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
except OSError:
    print(2222)
finally:
    sock.close()
PY
}
if [ -z "$SSH_PORT" ]; then
    SSH_PORT="$(find_free_port)"
fi

[ -f "$QEMU" ]  || { echo "FAIL: QEMU not found at $QEMU"; exit 1; }
[ -f "$KERNEL" ] || { echo "FAIL: kernel not found at $KERNEL"; exit 1; }
[ -f "$DISK" ]   || { echo "FAIL: disk not found at $DISK"; exit 1; }
# Auto-build cloud-init ISO if missing or stale relative to driver modules.
if [ ! -f "$CIDATA" ] || \
   { [ -f "$SCRIPT_DIR/arti_rtl_test.ko" ] && [ "$SCRIPT_DIR/arti_rtl_test.ko" -nt "$CIDATA" ]; } || \
   { [ -f "$SCRIPT_DIR/arti_gpu_probe.ko" ] && [ "$SCRIPT_DIR/arti_gpu_probe.ko" -nt "$CIDATA" ]; } || \
   { [ -f "$SCRIPT_DIR/arti_gpu_drm.ko" ] && [ "$SCRIPT_DIR/arti_gpu_drm.ko" -nt "$CIDATA" ]; }; then
    echo "  cloud-init ISO missing or stale, building..."
    bash "$SCRIPT_DIR/build_cloudinit.sh" || { echo "FAIL: cannot build cloud-init ISO"; exit 1; }
fi
[ -f "$CIDATA" ] || { echo "FAIL: cloud-init not found at $CIDATA"; exit 1; }

if [ -z "${QEMU_DISPLAY:-}" ]; then
    case "$(uname -s)" in
        Darwin) QEMU_DISPLAY="cocoa" ;;
        *)      QEMU_DISPLAY="gtk" ;;
    esac
fi
DISPLAY_ARGS=(-display "$QEMU_DISPLAY")

echo "=== ARTI Debian Dev Environment ==="
echo "  Disk    : $DISK (persistent)"
echo "  Kernel  : $KERNEL"
echo "  Device  : MMIO 0x0B000000 (embedded Verilated model)"
echo "  Display : $QEMU_DISPLAY"
echo "  Login   : root (password: arti)"
echo "  Network : user-mode (SLIRP) - apt/DNS via 10.0.2.2"
echo "  SSH     : ssh root@localhost -p $SSH_PORT"
echo "  Exit    : poweroff -f  or  Ctrl+A then X"
echo ""

exec "$QEMU" \
  -machine virt -cpu cortex-a53 -m 1G -smp 2 \
  "${DISPLAY_ARGS[@]}" -serial mon:stdio \
  -global virtio-mmio.force-legacy=false \
  -drive if=none,file="$CIDATA",format=raw,id=cidata \
  -device virtio-blk-device,drive=cidata \
  -drive if=none,file="$DISK",format=qcow2,id=hd0 \
  -device virtio-blk-device,drive=hd0 \
  -device virtio-keyboard-device \
  -device virtio-tablet-device \
  -netdev user,id=net0,hostfwd=tcp::${SSH_PORT}-:22 \
  -device virtio-net-device,netdev=net0 \
  -kernel "$KERNEL" \
  -append "root=/dev/vda1 console=tty0 console=ttyAMA0 rw rootwait systemd.mask=systemd-resolved.service systemd.mask=systemd-networkd-wait-online.service systemd.mask=boot-efi.mount"
