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
# Tee the guest console to a file. -serial mon:stdio writes to the terminal
# only, so a hang leaves nothing behind to diagnose. Set DEBIAN_MON_STDIO=1 to
# get the monitor back on stdio instead (and lose the log).
SERIAL_LOG="${SERIAL_LOG:-$ARTI_WORK/debian-serial.log}"
DEBIAN_MON_STDIO="${DEBIAN_MON_STDIO:-0}"
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

# Both backends install to the same path under the same name
# (hw/misc/libarti_rtl_model.a), and build_embedded.sh skips the copy when the
# archive is byte-identical, so the last build wins silently and GPU_SIM at boot
# time says nothing about what is actually linked in. Probe the binary itself;
# the archive only decides what was available at link time.
detect_backend() {
    local syms archive n_fs n_vlt
    # Capture nm output first: under `set -o pipefail`, `nm | grep -q` fails
    # when grep exits early (nm takes SIGPIPE) even on a real match.
    syms="$(nm "$QEMU" 2>/dev/null || true)"
    case "$syms" in
        *GpuHostSystemAxiDut*) BACKEND="flashsim" ;;
        *VGpuHostSystemAxi*)   BACKEND="verilator" ;;
        *)                     BACKEND="unknown" ;;
    esac
    BACKEND_STAMP="$(date -r "$QEMU" '+%Y-%m-%d %H:%M')"
    # No pipeline here on purpose: `ls | head -1` gets SIGPIPE on ls, which
    # under `set -o pipefail` fails the whole assignment and, with `set -e`,
    # kills the script with no output at all.
    archive=""
    for cand in "${QEMU_SRC:-$ARTI_WORK/qemu-src}/hw/misc/libarti_rtl_model.a" \
                "$ARTI_WORK"/qemu-*/hw/misc/libarti_rtl_model.a; do
        if [ -f "$cand" ]; then archive="$cand"; break; fi
    done
    if [ -n "$archive" ]; then
        n_fs="$(ar t "$archive" 2>/dev/null | grep -c '^dut_' || true)"
        n_vlt="$(ar t "$archive" 2>/dev/null | grep -c '^VGpu' || true)"
        BACKEND_ARCHIVE="$archive ($n_fs dut_ / $n_vlt VGpu)"
    else
        BACKEND_ARCHIVE="archive not found"
    fi
}
detect_backend
# Auto-build cloud-init + modules ISO if missing/stale.
if [ ! -f "$CIDATA" ] || [ ! -f "$MODULES_ISO" ] || \
   { [ -f "$SCRIPT_DIR/arti_rtl_test.ko" ] && [ "$SCRIPT_DIR/arti_rtl_test.ko" -nt "$CIDATA" ]; } || \
   [ "$GPU_REFERENCE" = "1" ] || \
   { [ -n "$DRIVER_KO" ] && [ -f "$DRIVER_KO" ] && \
     { [ "$DRIVER_KO" -nt "$CIDATA" ] || [ "$DRIVER_KO" -nt "$MODULES_ISO" ]; }; }; then
    echo "  cloud-init / modules ISO missing or stale, building..."
    GPU_REFERENCE="$GPU_REFERENCE" DRIVER_KO="$DRIVER_KO" \
    DRIVER_MANIFEST="${DRIVER_MANIFEST:-}" \
    OPENGPU_AUTO_DISPLAY="${OPENGPU_AUTO_DISPLAY:-0}" \
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
echo "  Device  : embedded RTL model"
echo "  Backend  : $BACKEND (QEMU built ${BACKEND_STAMP:-?}; archive $BACKEND_ARCHIVE)"
echo "  Display : $QEMU_DISPLAY (serial console for login; GPU mode may be tiny)"
echo "  Login   : root (password: arti)"
echo "  Network : user-mode (SLIRP) - apt/DNS via 10.0.2.2"
echo "  SSH     : ssh root@localhost -p $SSH_PORT"
if [ "${OPENGPU_AUTO_DISPLAY:-0}" = "1" ]; then
    echo "  GPU     : OpenGPU loads and presents a KMS frame at boot"
else
    echo "  GPU     : after boot run /root/load_opengpu.sh or /root/load_opengpu.sh test"
fi
if [ "$DEBIAN_MON_STDIO" = "1" ]; then
    echo "  Exit    : poweroff -f  or  Ctrl+A then X"
else
    echo "  Exit    : poweroff -f  or  Ctrl+C (guest keeps stdin; monitor is off)"
    echo "  Console : also written to $SERIAL_LOG"
fi
echo ""

# virtio-mmio on mach-virt registers -device virtio-blk-device nodes in reverse
# creation order, so attach the root disk LAST to get /dev/vda (ISOs → vdb/vdc).
# OPENGPU modules are still found via /dev/disk/by-label/OPENGPU.
QEMU_ARGS=()
if [ -n "${QEMU_FW_DIR:-}" ] && [ -d "$QEMU_FW_DIR" ]; then
    QEMU_ARGS+=(-L "$QEMU_FW_DIR")
fi

# -serial mon:stdio writes the guest console to the terminal only, so a wedged
# guest leaves no artifact at all. With DEBIAN_MON_STDIO unset, mirror the same
# stream into $SERIAL_LOG while keeping it on screen; signal=on keeps Ctrl+C
# going to the guest. DEBIAN_MON_STDIO=1 restores the plain monitor-on-stdio
# setup and gives up the log.
SERIAL_ARGS=()
if [ "$DEBIAN_MON_STDIO" = "1" ]; then
    SERIAL_ARGS=(-serial mon:stdio)
else
    mkdir -p "$(dirname "$SERIAL_LOG")"
    SERIAL_ARGS=(-chardev "stdio,id=arti0,signal=on,logfile=$SERIAL_LOG"
                 -serial chardev:arti0 -monitor none)
fi

# Replace this script so QEMU stays in the terminal's foreground process
# group. A timeout(1) parent calls setpgid and backgrounds QEMU, which
# stops the Cocoa window on SIGTTIN.
exec "$QEMU" \
  "${QEMU_ARGS[@]+"${QEMU_ARGS[@]}"}" \
  -machine virt -cpu cortex-a53 -m 1G -smp 2 \
  "${DISPLAY_ARGS[@]+"${DISPLAY_ARGS[@]}"}" \
  "${SERIAL_ARGS[@]+"${SERIAL_ARGS[@]}"}" \
  -global virtio-mmio.force-legacy=false \
  -drive if=none,file="$MODULES_ISO",format=raw,id=opengpu,read-only=on \
  -device virtio-blk-device,drive=opengpu \
  -drive if=none,file="$CIDATA",format=raw,id=cidata,read-only=on \
  -device virtio-blk-device,drive=cidata \
  -drive if=none,file="$DISK",format=qcow2,id=hd0 \
  -device virtio-blk-device,drive=hd0 \
  -device virtio-keyboard-device \
  -device virtio-tablet-device \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22 \
  -device virtio-net-device,netdev=net0 \
  -kernel "$KERNEL" \
  -append "root=/dev/vda1 console=tty0 console=ttyAMA0 rw rootwait systemd.mask=systemd-resolved.service systemd.mask=systemd-networkd-wait-online.service systemd.mask=boot-efi.mount"
