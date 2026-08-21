#!/usr/bin/env bash
# Build an external AArch64 Linux driver against the exact kernel used by QEMU.
#
# Examples:
#   ./build_driver.sh --source /path/to/my_gpu.c --name my_gpu
#   ./build_driver.sh --dir /path/to/my_gpu_driver --module my_gpu --output /tmp/my_gpu-ko
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
LINUX_BUILD="${LINUX_BUILD:-/tmp/arti-linux-build}"
DRIVER_DIR="${DRIVER_DIR:-}"
DRIVER_SRC="${DRIVER_SRC:-}"
DRIVER_NAME="${DRIVER_NAME:-}"
DRIVER_MODULE="${DRIVER_MODULE:-}"
GPU_REFERENCE="${GPU_REFERENCE:-0}"
OUTPUT="${OUTPUT:-/tmp/arti-driver-ko}"
HOSTCFLAGS="${HOSTCFLAGS:-}"

usage() {
    cat >&2 <<'EOF'
Usage:
  build_driver.sh --source DRIVER.c --name MODULE [--output DIR]
  build_driver.sh --dir DRIVER_DIR [--module MODULE] [--output DIR]

Environment overrides: LINUX_BUILD, CROSS_COMPILE, DRIVER_SRC, DRIVER_NAME,
DRIVER_DIR, DRIVER_MODULE, GPU_REFERENCE, OUTPUT.
EOF
    exit 2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) [ "$#" -ge 2 ] || usage; DRIVER_SRC="$2"; shift 2 ;;
        --name) [ "$#" -ge 2 ] || usage; DRIVER_NAME="$2"; shift 2 ;;
        --dir) [ "$#" -ge 2 ] || usage; DRIVER_DIR="$2"; shift 2 ;;
        --module) [ "$#" -ge 2 ] || usage; DRIVER_MODULE="$2"; shift 2 ;;
        --output) [ "$#" -ge 2 ] || usage; OUTPUT="$2"; shift 2 ;;
        --help|-h) usage ;;
        *) usage ;;
    esac
done

[ -d "$LINUX_BUILD" ] || { echo "FAIL: kernel build directory not found: $LINUX_BUILD" >&2; exit 1; }
[ -f "$LINUX_BUILD/Makefile" ] || { echo "FAIL: $LINUX_BUILD is not a kernel build directory" >&2; exit 1; }
[ -f "$LINUX_BUILD/include/config/kernel.release" ] || {
    echo "FAIL: kernel is not configured: $LINUX_BUILD/include/config/kernel.release is missing" >&2
    echo "      Run setup_env.sh or build the kernel first." >&2
    exit 1
}

if [ -n "$DRIVER_SRC" ] && [ -n "$DRIVER_DIR" ]; then
    echo "FAIL: choose --source or --dir, not both" >&2
    exit 2
fi
[ -n "$DRIVER_SRC" ] || [ -n "$DRIVER_DIR" ] || usage

resolve_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$PWD" "$1" ;;
    esac
}

if [ -n "$DRIVER_SRC" ]; then
    DRIVER_SRC="$(resolve_path "$DRIVER_SRC")"
    [ -f "$DRIVER_SRC" ] || { echo "FAIL: driver source not found: $DRIVER_SRC" >&2; exit 1; }
    DRIVER_NAME="${DRIVER_NAME:-$(basename "$DRIVER_SRC" .c)}"
    case "$DRIVER_NAME" in
        ''|*[!A-Za-z0-9_+-]*) echo "FAIL: invalid DRIVER_NAME: $DRIVER_NAME" >&2; exit 2 ;;
    esac
else
    DRIVER_DIR="$(resolve_path "$DRIVER_DIR")"
    [ -d "$DRIVER_DIR" ] || { echo "FAIL: driver directory not found: $DRIVER_DIR" >&2; exit 1; }
fi

if [ -n "$DRIVER_MODULE" ]; then
    DRIVER_MODULE="$(basename "$DRIVER_MODULE" .ko)"
    case "$DRIVER_MODULE" in
        ''|*[!A-Za-z0-9_+-]*) echo "FAIL: invalid DRIVER_MODULE: $DRIVER_MODULE" >&2; exit 2 ;;
    esac
fi

if [ -n "${CROSS_COMPILE:-}" ]; then
    case "$CROSS_COMPILE" in
        *-) CROSS_GCC="${CROSS_COMPILE}gcc" ;;
        *) CROSS_GCC="${CROSS_COMPILE}-gcc" ;;
    esac
else
    CROSS_GCC=""
    for candidate in aarch64-linux-gnu-gcc aarch64-unknown-linux-gnu-gcc \
                    aarch64-none-linux-gnu-gcc aarch64-linux-musl-gcc; do
        if command -v "$candidate" >/dev/null 2>&1; then
            CROSS_GCC="$candidate"
            CROSS_COMPILE="${candidate%-gcc}-"
            break
        fi
    done
fi
[ -n "$CROSS_GCC" ] && command -v "$CROSS_GCC" >/dev/null 2>&1 || {
    echo "FAIL: AArch64 cross compiler not found; set CROSS_COMPILE." >&2
    exit 1
}
command -v strings >/dev/null 2>&1 || { echo "FAIL: strings not found" >&2; exit 1; }

if command -v gmake >/dev/null 2>&1; then
    MAKE="$(command -v gmake)"
else
    MAKE="$(command -v make 2>/dev/null || true)"
fi
[ -n "$MAKE" ] || { echo "FAIL: make not found" >&2; exit 1; }
KERNEL_RELEASE="$("$MAKE" -s -C "$LINUX_BUILD" kernelrelease)"
[ -n "$KERNEL_RELEASE" ] || { echo "FAIL: cannot determine kernel release" >&2; exit 1; }

TEMP_DIR=""
BUILD_DIR="$DRIVER_DIR"
cleanup() { [ -z "$TEMP_DIR" ] || rm -rf "$TEMP_DIR"; }
trap cleanup EXIT
if [ -n "$DRIVER_SRC" ]; then
    TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/arti-driver.XXXXXX")"
    cp "$DRIVER_SRC" "$TEMP_DIR/$DRIVER_NAME.c"
    find "$(dirname "$DRIVER_SRC")" -maxdepth 1 -type f -name '*.h' -exec cp {} "$TEMP_DIR/" \;
    printf 'obj-m += %s.o\n' "$DRIVER_NAME" > "$TEMP_DIR/Makefile"
    BUILD_DIR="$TEMP_DIR"
fi

echo "=== Building external Linux driver ==="
echo "Driver       : ${DRIVER_SRC:-$DRIVER_DIR}"
echo "Kernel build : $LINUX_BUILD"
echo "Kernel       : $KERNEL_RELEASE"
echo "Compiler     : $CROSS_GCC"
echo "Output       : $OUTPUT"
[ -z "$DRIVER_MODULE" ] || echo "Module       : $DRIVER_MODULE.ko"
echo "GPU reference: $GPU_REFERENCE"

BUILD_TARGET="modules"
[ -z "$DRIVER_MODULE" ] || BUILD_TARGET="$DRIVER_MODULE.ko"
"$MAKE" -C "$LINUX_BUILD" M="$BUILD_DIR" ARCH=arm64 \
    CROSS_COMPILE="$CROSS_COMPILE" GPU_REFERENCE="$GPU_REFERENCE" \
    HOSTCFLAGS="$HOSTCFLAGS" "$BUILD_TARGET"

modules=()
if [ -n "$DRIVER_MODULE" ]; then
    [ -f "$BUILD_DIR/$DRIVER_MODULE.ko" ] || {
        echo "FAIL: requested module was not produced: $DRIVER_MODULE.ko" >&2
        exit 1
    }
    modules+=("$BUILD_DIR/$DRIVER_MODULE.ko")
else
    while IFS= read -r module; do
        modules+=("$module")
    done < <(find "$BUILD_DIR" -maxdepth 1 -type f -name '*.ko' -print)
fi
[ "${#modules[@]}" -gt 0 ] || { echo "FAIL: no .ko was produced" >&2; exit 1; }

mkdir -p "$OUTPUT"
for module in "${modules[@]}"; do
    cp "$module" "$OUTPUT/"
done

echo "=== Driver modules built ==="
for module in "${modules[@]}"; do
    artifact="$OUTPUT/$(basename "$module")"
    echo "  $artifact"
    module_name="$(basename "$module" .ko)"
    depends="$(strings "$artifact" 2>/dev/null | sed -n 's/^depends=//p' | head -1)"
    manifest="$OUTPUT/$module_name.deps"
    {
        echo "module=$(basename "$artifact")"
        echo "kernel_release=$KERNEL_RELEASE"
        echo "depends=${depends}"
    } > "$manifest"
    if [ -n "$depends" ]; then
        IFS=',' read -r -a dependency_names <<< "$depends"
        for dependency in "${dependency_names[@]-}"; do
            dependency="${dependency//[[:space:]]/}"
            [ -n "$dependency" ] || continue
            dependency_path="$(find "$LINUX_BUILD" -type f -name "$dependency.ko" -print -quit 2>/dev/null || true)"
            if [ -n "$dependency_path" ]; then
                echo "dependency=$dependency:$dependency_path" >> "$manifest"
            else
                echo "WARNING: dependency $dependency.ko was not found under $LINUX_BUILD" >&2
            fi
        done
    fi
    actual_release="$(strings "$artifact" 2>/dev/null | sed -n 's/^vermagic=//p' | awk '{print $1}' | head -1)"
    if command -v modinfo >/dev/null 2>&1; then
        actual_release="$(modinfo -F vermagic "$artifact" 2>/dev/null | awk '{print $1}')"
    fi
    [ -n "$actual_release" ] || {
        echo "FAIL: cannot read vermagic from $artifact" >&2
        exit 1
    }
    [ "$actual_release" = "$KERNEL_RELEASE" ] || {
        echo "FAIL: vermagic mismatch: $actual_release != $KERNEL_RELEASE" >&2
        exit 1
    }
    echo "  Dependencies: ${depends:-none}"
    echo "  Manifest    : $manifest"
done
echo "Kernel release match: $KERNEL_RELEASE"
