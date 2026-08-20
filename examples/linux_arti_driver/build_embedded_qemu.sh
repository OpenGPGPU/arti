#!/usr/bin/env bash
# Generate and build an embedded RTL model into QEMU.
#
# Uses the arti framework to dynamically generate the C++ wrapper
# that drives AXI-Lite handshaking for any supported RTL module,
# then compiles it into QEMU — no socket or cosim process needed.
#
# Usage:
#   RTL=examples/reg_file/reg_file.v TOP=reg_file \
#     QEMU_SRC=/path/to/qemu ./build_embedded_qemu.sh
#
# After this, run run_linux_test.sh to verify with a Linux guest.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
RTL_INPUT="${RTL:-examples/simple_gpio/simple_gpio.v}"
TOP="${TOP:-simple_gpio}"
QEMU_SRC="${QEMU_SRC:-$(cd "$ARTI_DIR/../qemu" && pwd)}"
QEMU_BUILD="${QEMU_BUILD:-/tmp/qemu-arti-build}"
OUTPUT="${OUTPUT:-/tmp/arti-embedded-gen}"

# Resolve RTL to absolute path (relative paths are resolved from ARTI_DIR)
if [[ "$RTL_INPUT" = /* ]]; then
    RTL="$RTL_INPUT"
else
    RTL="$(cd "$ARTI_DIR" && realpath "$RTL_INPUT")"
fi

echo "=== Generating embedded model for $TOP ==="
echo "RTL       : $RTL"
echo "Top module: $TOP"
echo "QEMU src  : $QEMU_SRC"
echo ""

# 1. Create config and generate project via arti CLI
CONFIG="$OUTPUT/config.yaml"
mkdir -p "$OUTPUT"
rm -rf "$OUTPUT/generated"
cat > "$CONFIG" << YAMLEOF
project:
  name: ${TOP}_embedded

rtl:
  top_module: ${TOP}
  source_files: [${RTL}]
  clk_freq_mhz: 100

bridge:
  protocol: auto
  base_address: "0x0B00_0000"
  data_width: 32
  mode: qemu-embedded

advanced:
  timeout_cycles: 1000
display:
  enabled: ${ARTI_DISPLAY:-false}
  width: ${ARTI_DISPLAY_WIDTH:-1024}
  height: ${ARTI_DISPLAY_HEIGHT:-768}
  format: ${ARTI_DISPLAY_FORMAT:-a8r8g8b8}
  framebuffer_offset: ${ARTI_DISPLAY_FB_OFFSET:-0x100000}
  framebuffer_size: ${ARTI_DISPLAY_FB_SIZE:-0x800000}
YAMLEOF

PYTHONPATH="$ARTI_DIR/src" python3 -m arti.cli generate "$CONFIG" --output "$OUTPUT/generated"
echo "=== arti generate done ==="

# 2. Run the generated build script (Verilator + compile + install + QEMU rebuild)
echo "=== Building embedded model and rebuilding QEMU ==="
QEMU_SRC="$QEMU_SRC" QEMU_BUILD="$QEMU_BUILD" \
  "$OUTPUT/generated/embedded/build_embedded.sh"

echo ""
echo "=== Embedded model built and QEMU updated ==="
echo "Now run: $SCRIPT_DIR/run_linux_test.sh"
