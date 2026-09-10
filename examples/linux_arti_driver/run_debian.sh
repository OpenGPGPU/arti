#!/usr/bin/env bash
# Boot a full Debian 12 (bookworm) ARM64 environment with the ARTI device.
#
# Features:
#   - Real Debian rootfs on 10GB qcow2 disk (persistent)
#   - Full systemd, apt, insmod/lsmod/rmmod, gcc, etc.
#   - ARTI embedded device at MMIO 0x0B000000
#   - Root login (password: arti)
#
# Prerequisites live under $ARTI_WORK (../arti-work from the ARTI repo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
. "$SCRIPT_DIR/integration_env.sh"
arti_load_integration_config || { echo "FAIL: cannot load integration config"; exit 1; }

ARTI_WORK="${ARTI_WORK:-$(arti_default_work_dir)}"
QEMU="${QEMU:-$ARTI_WORK/qemu-arti-build/qemu-system-aarch64}"
KERNEL="${KERNEL:-$ARTI_WORK/arti-linux-build/arch/arm64/boot/Image}"
DISK="${DISK:-$ARTI_WORK/arti-dev.qcow2}"
CIDATA="${CIDATA:-$ARTI_WORK/cloud-init.iso}"
MODULES_ISO="${MODULES_ISO:-$ARTI_WORK/opengpu-modules.iso}"
GPU_REFERENCE="${GPU_REFERENCE:-0}"
# Prefer an explicit DRIVER_KO; otherwise use the OpenGPU build under ARTI_WORK.
if [ -z "${DRIVER_KO:-}" ] && [ -f "$ARTI_WORK/opengpu-driver/gpu_drv.ko" ]; then
    DRIVER_KO="$ARTI_WORK/opengpu-driver/gpu_drv.ko"
    DRIVER_MANIFEST="${DRIVER_MANIFEST:-$ARTI_WORK/opengpu-driver/gpu_drv.deps}"
fi
DRIVER_KO="${DRIVER_KO:-}"
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
# Auto-build cloud-init + modules ISO if missing/stale.
if [ ! -f "$CIDATA" ] || [ ! -f "$MODULES_ISO" ] || \
   { [ -f "$SCRIPT_DIR/arti_rtl_test.ko" ] && [ "$SCRIPT_DIR/arti_rtl_test.ko" -nt "$CIDATA" ]; } || \
   [ "$GPU_REFERENCE" = "1" ] || [ -n "$DRIVER_KO" ] || \
   { [ -n "$DRIVER_KO" ] && [ -f "$DRIVER_KO" ] && \
     { [ "$DRIVER_KO" -nt "$CIDATA" ] || [ "$DRIVER_KO" -nt "$MODULES_ISO" ]; }; }; then
    echo "  cloud-init / modules ISO missing or stale, building..."
    GPU_REFERENCE="$GPU_REFERENCE" DRIVER_KO="$DRIVER_KO" \
    DRIVER_MANIFEST="${DRIVER_MANIFEST:-}" \
    OUTPUT="$CIDATA" MODULES_ISO="$MODULES_ISO" \
        bash "$SCRIPT_DIR/build_cloudinit.sh" || { echo "FAIL: cannot build cloud-init ISO"; exit 1; }
fi
[ -f "$CIDATA" ] || { echo "FAIL: cloud-init not found at $CIDATA"; exit 1; }
[ -f "$MODULES_ISO" ] || { echo "FAIL: modules ISO not found at $MODULES_ISO"; exit 1; }

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
echo "  Modules : $MODULES_ISO"
echo "  Device  : embedded RTL model (FlashSim if that QEMU was linked)"
echo "  Display : $QEMU_DISPLAY (serial console is primary; window may be tiny)"
echo "  Login   : root (password: arti)"
echo "  Network : user-mode (SLIRP) - apt/DNS via 10.0.2.2"
echo "  SSH     : ssh root@localhost -p $SSH_PORT"
echo "  GPU     : after boot run  /root/load_opengpu.sh  or  /root/load_opengpu.sh test"
echo "  Exit    : poweroff -f  or  Ctrl+A then X"
echo ""

# virtio-mmio on mach-virt registers -device virtio-blk-device nodes in reverse
# creation order, so attach the root disk LAST to get /dev/vda (ISOs → vdb/vdc).
# OPENGPU modules are still found via /dev/disk/by-label/OPENGPU.
QEMU_ARGS=()
if [ -n "${QEMU_FW_DIR:-}" ] && [ -d "$QEMU_FW_DIR" ]; then
    QEMU_ARGS+=(-L "$QEMU_FW_DIR")
fi

exec "$QEMU" \
  "${QEMU_ARGS[@]}" \
  -machine virt -cpu cortex-a53 -m 1G -smp 2 \
  "${DISPLAY_ARGS[@]}" -serial mon:stdio \
  -global virtio-mmio.force-legacy=false \
  -drive if=none,file="$MODULES_ISO",format=raw,id=opengpu,read-only=on \
  -device virtio-blk-device,drive=opengpu \
  -drive if=none,file="$CIDATA",format=raw,id=cidata,read-only=on \
  -device virtio-blk-device,drive=cidata \
  -drive if=none,file="$DISK",format=qcow2,id=hd0 \
  -device virtio-blk-device,drive=hd0 \
  -device virtio-keyboard-device \
  -device virtio-tablet-device \
  -netdev user,id=net0,hostfwd=tcp::${SSH_PORT}-:22 \
  -device virtio-net-device,netdev=net0 \
  -kernel "$KERNEL" \
  -append "root=/dev/vda1 console=tty0 console=ttyAMA0 rw rootwait systemd.mask=systemd-resolved.service systemd.mask=systemd-networkd-wait-online.service systemd.mask=boot-efi.mount"
