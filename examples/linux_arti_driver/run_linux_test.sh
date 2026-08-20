#!/usr/bin/env bash
# End-to-end Linux driver test for ARTI embedded QEMU SysBus device.
#
# The RTL model (Verilated simple_gpio) is compiled directly into QEMU,
# so no external cosim process or Unix socket is needed.
#
# Data path: guest MMIO -> QEMU arti-rtl (embedded Verilated model) -> RTL
#
# Prerequisites:
#   - QEMU binary:   QEMU=/tmp/qemu-arti-build/qemu-system-aarch64
#   - Kernel Image:  KERNEL=/tmp/arti-linux-build/arch/arm64/boot/Image
#   - Cross GCC:     aarch64-linux-gnu-gcc (or aarch64-unknown-linux-gnu-gcc)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

QEMU="${QEMU:-/tmp/qemu-arti-build/qemu-system-aarch64}"
KERNEL="${KERNEL:-/tmp/arti-linux-build/arch/arm64/boot/Image}"
WORK="${WORK:-/tmp/arti-linux-test}"
TIMEOUT="${TIMEOUT:-60}"
SERIAL_LOG="$WORK/serial.log"

if [ "$(uname -s)" = "Darwin" ]; then
    case ":$PATH:" in
        *":/opt/homebrew/bin:"*|*":/usr/local/bin:"*) ;;
        *) PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" ;;
    esac
    export PATH
fi

echo "=== ARTI Linux driver end-to-end test (embedded model) ==="
echo "ARTI_DIR : $ARTI_DIR"
echo "QEMU     : $QEMU"
echo "KERNEL   : $KERNEL"
echo ""

# 0. Check prerequisites
echo "--- checking prerequisites ---"

find_cross_gcc() {
    local cc
    if [ -n "${CROSS_COMPILE:-}" ]; then
        case "$CROSS_COMPILE" in
            *-) CROSS_GCC="${CROSS_COMPILE}gcc" ;;
            *) CROSS_GCC="${CROSS_COMPILE}-gcc" ;;
        esac
        command -v "$CROSS_GCC" >/dev/null 2>&1 || return 1
        return 0
    fi
    for cc in aarch64-linux-gnu-gcc aarch64-unknown-linux-gnu-gcc \
              aarch64-none-linux-gnu-gcc aarch64-linux-musl-gcc; do
        if command -v "$cc" >/dev/null 2>&1; then
            CROSS_GCC="$cc"
            CROSS_COMPILE="${cc%-gcc}-"
            return 0
        fi
    done
    return 1
}

if ! find_cross_gcc; then
    echo "FAIL: AArch64 cross compiler not found. Run setup_env.sh first."
    exit 1
fi
echo "CROSS_GCC: $CROSS_GCC"

TIMEOUT_BIN="$(command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null || true)"
if [ -z "$TIMEOUT_BIN" ]; then
    echo "FAIL: timeout/gtimeout not found. Run setup_env.sh first."
    exit 1
fi

[ -f "$QEMU" ] || { echo "FAIL: QEMU binary not found at $QEMU"; echo "  Build it first (see README section 5.1)"; exit 1; }

[ -f "$KERNEL" ] || { echo "FAIL: kernel Image not found at $KERNEL"; echo "  Build it first (see README section 5.2)"; exit 1; }

[ -f "$SCRIPT_DIR/arti_rtl_test.ko" ] || { echo "FAIL: arti_rtl_test.ko not found at $SCRIPT_DIR/"; echo "  Build it first (see README section 5.3)"; exit 1; }

echo "--- prerequisites OK ---"
echo ""

# 1. Build the init binary and initramfs
echo "--- building initramfs ---"
mkdir -p "$WORK/proc" "$WORK/sys" "$WORK/dev"
"$CROSS_GCC" -static -O2 \
    -o "$WORK/init" "$SCRIPT_DIR/arti-linux-init.c"
chmod +x "$WORK/init"
cp "$SCRIPT_DIR/arti_rtl_test.ko" "$WORK/"
( cd "$WORK" && find . | cpio -o -H newc 2>/dev/null ) | gzip > "$WORK/initramfs.cpio.gz"
echo "--- initramfs built ($(wc -c < "$WORK/initramfs.cpio.gz") bytes) ---"

# 2. Boot QEMU (embedded model — no cosim, no socket needed)
#    Use -serial file: instead of -nographic to avoid stdio/TTY issues
echo "--- launching QEMU (timeout ${TIMEOUT}s, no external cosim) ---"
rm -f "$SERIAL_LOG"
set +e
"$TIMEOUT_BIN" "$TIMEOUT" "$QEMU" \
    -machine virt -cpu cortex-a53 -m 512M \
    -display none -monitor none \
    -serial file:"$SERIAL_LOG" \
    -kernel "$KERNEL" \
    -initrd "$WORK/initramfs.cpio.gz" \
    -append "console=ttyAMA0" < /dev/null
QEMU_RC=$?
set -e

# 3. Check the result
echo ""
echo "=== Serial output (tail) ==="
tail -12 "$SERIAL_LOG" 2>/dev/null || echo "(no serial output)"

if grep -q "ARTI LINUX PASS" "$SERIAL_LOG" 2>/dev/null; then
    echo ""
    echo "=== ARTI LINUX TEST COMPLETE (PASS) ==="
    exit 0
else
    echo ""
    echo "=== ARTI LINUX TEST FAILED ==="
    echo "--- QEMU exit code: $QEMU_RC ---"
    echo "--- serial log size: $(wc -c < "$SERIAL_LOG" 2>/dev/null || echo 0) bytes ---"
    echo "--- full serial log ---"
    cat "$SERIAL_LOG" 2>/dev/null || echo "(empty)"
    exit 1
fi
