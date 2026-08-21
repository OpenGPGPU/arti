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
. "$SCRIPT_DIR/integration_env.sh"
arti_load_integration_config || { echo "FAIL: cannot load integration config"; exit 1; }

QEMU="${QEMU:-/tmp/qemu-arti-build/qemu-system-aarch64}"
KERNEL="${KERNEL:-/tmp/arti-linux-build/arch/arm64/boot/Image}"
LINUX_BUILD="${LINUX_BUILD:-/tmp/arti-linux-build}"
WORK="${WORK:-/tmp/arti-linux-test}"
TIMEOUT="${TIMEOUT:-60}"
SERIAL_LOG="$WORK/serial.log"
GPU_DRM_TEST="${GPU_DRM_TEST:-0}"
GPU_REFERENCE="${GPU_REFERENCE:-0}"
DRIVER_KO="${DRIVER_KO:-}"
DRIVER_DEPS="${DRIVER_DEPS:-}"
DRIVER_MANIFEST="${DRIVER_MANIFEST:-}"
DRIVER_MARKER="${DRIVER_MARKER:-ARTI EXTERNAL DRIVER PASS}"
ARTI_DISPLAY="${ARTI_DISPLAY:-0}"

if [ "$GPU_DRM_TEST" = "1" ] && [ "$GPU_REFERENCE" != "1" ]; then
    echo "FAIL: GPU_DRM_TEST=1 requires GPU_REFERENCE=1"
    exit 1
fi
if [ -n "$DRIVER_KO" ] && [ ! -f "$DRIVER_KO" ]; then
    echo "FAIL: external driver not found at $DRIVER_KO"
    exit 1
fi
if [ -n "$DRIVER_KO" ] && [ "$GPU_REFERENCE" = "1" ]; then
    echo "FAIL: choose DRIVER_KO or GPU_REFERENCE=1; they must not bind the same DT node"
    exit 1
fi
if [ "$GPU_REFERENCE" = "1" ] || [ -n "$DRIVER_KO" ]; then
    SKIP_GENERIC_TEST="${SKIP_GENERIC_TEST:-1}"
else
    SKIP_GENERIC_TEST="${SKIP_GENERIC_TEST:-0}"
fi

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
echo "GPU ref  : $GPU_REFERENCE"
[ -z "$DRIVER_KO" ] || echo "Driver   : $DRIVER_KO (marker: $DRIVER_MARKER)"
[ -z "$DRIVER_MANIFEST" ] || [ ! -f "$DRIVER_MANIFEST" ] || echo "Manifest : $DRIVER_MANIFEST"
[ -z "$DRIVER_DEPS" ] || echo "Deps     : $DRIVER_DEPS"
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

KERNEL_RELEASE_FILE="$LINUX_BUILD/include/config/kernel.release"
KERNEL_RELEASE=""
if [ -f "$KERNEL_RELEASE_FILE" ]; then
    KERNEL_RELEASE="$(tr -d '[:space:]' < "$KERNEL_RELEASE_FILE")"
fi

if [ -n "$DRIVER_KO" ]; then
    DRIVER_MANIFEST="${DRIVER_MANIFEST:-${DRIVER_KO%.ko}.deps}"
    if [ -f "$DRIVER_MANIFEST" ]; then
        manifest_release="$(sed -n 's/^kernel_release=//p' "$DRIVER_MANIFEST" | head -1)"
        if [ -n "$manifest_release" ] && [ -n "$KERNEL_RELEASE" ] && \
           [ "$manifest_release" != "$KERNEL_RELEASE" ]; then
            echo "FAIL: driver manifest kernel release mismatch: $manifest_release != $KERNEL_RELEASE"
            echo "  Rebuild the driver with build_driver.sh against $LINUX_BUILD"
            exit 1
        fi
        while IFS='=' read -r manifest_key manifest_value; do
            [ "$manifest_key" = "dependency" ] || continue
            manifest_path="${manifest_value#*:}"
            [ -f "$manifest_path" ] || continue
            DRIVER_DEPS="${DRIVER_DEPS:+$DRIVER_DEPS:}$manifest_path"
        done < "$DRIVER_MANIFEST"
    fi
fi

module_vermagic() {
    local module="$1" value=""
    if command -v modinfo >/dev/null 2>&1; then
        value="$(modinfo -F vermagic "$module" 2>/dev/null || true)"
    fi
    if [ -z "$value" ]; then
        value="$(strings "$module" 2>/dev/null | sed -n 's/^vermagic=//p' | head -1 || true)"
    fi
    printf '%s\n' "$value" | awk '{print $1}'
}

check_module_kernel() {
    local module="$1" actual
    [ -n "$KERNEL_RELEASE" ] || {
        echo "FAIL: kernel release metadata not found at $KERNEL_RELEASE_FILE"
        echo "  Set LINUX_BUILD to the configured kernel build directory"
        exit 1
    }
    actual="$(module_vermagic "$module")"
    [ -n "$actual" ] || {
        echo "FAIL: cannot read vermagic from $module"
        exit 1
    }
    [ "$actual" = "$KERNEL_RELEASE" ] || {
        echo "FAIL: vermagic mismatch for $(basename "$module"): $actual != $KERNEL_RELEASE"
        echo "  Rebuild the module with build_driver.sh against $LINUX_BUILD"
        exit 1
    }
}

if [ -n "$DRIVER_KO" ]; then
    check_module_kernel "$DRIVER_KO"
fi

if [ "$SKIP_GENERIC_TEST" != "1" ] && [ ! -f "$SCRIPT_DIR/arti_rtl_test.ko" ]; then
    echo "FAIL: arti_rtl_test.ko not found at $SCRIPT_DIR/"
    echo "  Build it first (see README section 5.3), or set SKIP_GENERIC_TEST=1 with DRIVER_KO"
    exit 1
fi
if [ "$SKIP_GENERIC_TEST" = "1" ] && [ -z "$DRIVER_KO" ] && [ "$GPU_REFERENCE" != "1" ]; then
    echo "FAIL: SKIP_GENERIC_TEST=1 requires DRIVER_KO or GPU_REFERENCE=1"
    exit 1
fi

echo "--- prerequisites OK ---"
echo ""

# 1. Build the init binary and initramfs
echo "--- building initramfs ---"
mkdir -p "$WORK/proc" "$WORK/sys" "$WORK/dev"
rm -f "$WORK/arti_rtl_test.ko" "$WORK/arti_driver.ko" "$WORK/arti_gpu_probe.ko" \
      "$WORK/arti_driver_deps" "$WORK"/arti_dep_*.ko \
      "$WORK/arti_gpu_drm.ko" "$WORK/backlight.ko" "$WORK/drm.ko" \
      "$WORK/drm_kms_helper.ko" "$WORK/drm_client_lib.ko" \
      "$WORK/drm_shmem_helper.ko"
"$CROSS_GCC" -static -O2 \
    -o "$WORK/init" "$SCRIPT_DIR/arti-linux-init.c"
chmod +x "$WORK/init"
if [ "$SKIP_GENERIC_TEST" != "1" ]; then
    cp "$SCRIPT_DIR/arti_rtl_test.ko" "$WORK/"
fi
if [ "$GPU_DRM_TEST" = "1" ]; then
    [ -f "$SCRIPT_DIR/arti_gpu_drm.ko" ] || { echo "FAIL: reference GPU DRM module not found"; exit 1; }
    cp "$SCRIPT_DIR/arti_gpu_drm.ko" "$WORK/"
    for drm_module in backlight drm drm_kms_helper drm_client_lib drm_shmem_helper; do
        drm_path="$(find "$LINUX_BUILD/drivers" -name "$drm_module.ko" -print -quit 2>/dev/null || true)"
        [ -n "$drm_path" ] || { echo "FAIL: $drm_module.ko not found under $LINUX_BUILD"; exit 1; }
        cp "$drm_path" "$WORK/"
    done
elif [ "$GPU_REFERENCE" = "1" ]; then
    [ -f "$SCRIPT_DIR/arti_gpu_probe.ko" ] || { echo "FAIL: reference GPU probe module not found"; exit 1; }
    cp "$SCRIPT_DIR/arti_gpu_probe.ko" "$WORK/"
fi
if [ -n "$DRIVER_KO" ]; then
    STAGED_DEP_NAMES=""

    dependency_path() {
        local name="$1" candidate
        local -a candidates=()
        IFS=: read -r -a candidates <<< "$DRIVER_DEPS"
        for candidate in "${candidates[@]-}"; do
            [ -n "$candidate" ] || continue
            if [ "$(basename "$candidate" .ko)" = "$name" ] && [ -f "$candidate" ]; then
                printf '%s\n' "$candidate"
                return 0
            fi
        done
        find "$LINUX_BUILD" -type f -name "$name.ko" -print -quit 2>/dev/null
    }

    stage_dependency() {
        local name="$1" path dep_line dep dep_path
        case ",$STAGED_DEP_NAMES," in
            *,"$name",*) return 0 ;;
        esac
        path="$(dependency_path "$name")"
        [ -n "$path" ] || { echo "FAIL: dependency $name.ko not found; set DRIVER_DEPS"; exit 1; }
        check_module_kernel "$path"
        dep_line="$(strings "$path" | sed -n 's/^depends=//p' | head -1 || true)"
        if [ -n "$dep_line" ]; then
            IFS=',' read -r -a deps <<< "$dep_line"
            for dep in "${deps[@]-}"; do
                dep="${dep//[[:space:]]/}"
                [ -n "$dep" ] || continue
                stage_dependency "$dep"
            done
        fi
        cp "$path" "$WORK/arti_dep_${name}.ko"
        printf '/arti_dep_%s.ko\n' "$name" >> "$WORK/arti_driver_deps"
        STAGED_DEP_NAMES="${STAGED_DEP_NAMES:+$STAGED_DEP_NAMES,}$name"
    }

    driver_dep_line="$(strings "$DRIVER_KO" | sed -n 's/^depends=//p' | head -1 || true)"
    if [ -n "$driver_dep_line" ]; then
        IFS=',' read -r -a driver_deps <<< "$driver_dep_line"
        for dep in "${driver_deps[@]-}"; do
            dep="${dep//[[:space:]]/}"
            [ -n "$dep" ] || continue
            stage_dependency "$dep"
        done
    fi
    cp "$DRIVER_KO" "$WORK/arti_driver.ko"
fi
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

GENERIC_PASS=0
REFERENCE_PASS=0
EXTERNAL_PASS=0
grep -Eq "ARTI LINUX PASS|ARTI GPU ABI PASS" "$SERIAL_LOG" 2>/dev/null && GENERIC_PASS=1
grep -Eq "ARTI GPU PROBE PASS|ARTI GPU DRM PASS" "$SERIAL_LOG" 2>/dev/null && REFERENCE_PASS=1
if [ -n "$DRIVER_KO" ] && grep -qF "$DRIVER_MARKER" "$SERIAL_LOG" 2>/dev/null; then
    EXTERNAL_PASS=1
fi
if [ "$GENERIC_PASS" = "1" ] || [ "$REFERENCE_PASS" = "1" ] || [ "$EXTERNAL_PASS" = "1" ]; then
    if { [ "$ARTI_DISPLAY" = "1" ] || [ "$ARTI_DISPLAY" = "true" ]; } && \
       ! grep -q "simplefb registered" "$SERIAL_LOG" 2>/dev/null; then
        echo "=== SIMPLEFB TEST FAILED ==="
        echo "--- simplefb registration marker not found ---"
        exit 1
    fi
    if [ "$GPU_DRM_TEST" = "1" ] && \
       ! grep -q "ARTI GPU DRM PASS" "$SERIAL_LOG" 2>/dev/null; then
        echo "=== GPU DRM TEST FAILED ==="
        echo "--- DRM takeover marker not found ---"
        exit 1
    elif [ "$GPU_DRM_TEST" != "1" ] && [ "$GPU_REFERENCE" = "1" ] && \
       ! grep -q "ARTI GPU PROBE PASS" "$SERIAL_LOG" 2>/dev/null; then
        echo "=== GPU PROBE TEST FAILED ==="
        echo "--- GPU probe marker not found ---"
        exit 1
    elif [ "$GPU_DRM_TEST" != "1" ] && [ "$GPU_REFERENCE" = "1" ] && \
       ! grep -q "ARTI GPU IRQ PASS" "$SERIAL_LOG" 2>/dev/null; then
        echo "=== GPU IRQ TEST FAILED ==="
        echo "--- GPU VSYNC IRQ marker not found ---"
        exit 1
    fi
    if [ -n "$DRIVER_KO" ] && [ "$EXTERNAL_PASS" != "1" ]; then
        echo "=== EXTERNAL DRIVER TEST FAILED ==="
        echo "--- marker not found: $DRIVER_MARKER ---"
        exit 1
    fi
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
