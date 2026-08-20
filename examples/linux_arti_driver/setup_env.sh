#!/usr/bin/env bash
#
# setup_env.sh — One-click environment setup for ARTI examples.
#
# This script downloads, builds, and installs everything needed to run
# the ARTI Linux driver examples from a clean environment:
#
#   1. Build tools (ninja via pip)
#   2. QEMU (with embedded arti-rtl device + SLIRP networking)
#   3. Linux kernel (AArch64, with virtio-net + modules + ext4)
#   4. Busybox (static binary for initramfs)
#   5. Debian 12 cloud rootfs (qcow2 + cloud-init)
#   6. .ko driver module (arti_rtl_test.ko)
#   7. Embedded RTL model (simple_gpio compiled into QEMU)
#
# After this script completes, run:
#   ./run_linux_test.sh    — automated end-to-end test
#   ./run_debian.sh        — full Debian dev environment with networking
#   ./run_interactive.sh   — interactive busybox shell
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
WORK_DIR="${WORK_DIR:-/tmp}"
QEMU_SRC="${QEMU_SRC:-}"
# Auto-detect existing QEMU source
for d in "$ARTI_DIR/../qemu" "$WORK_DIR/qemu-src"; do
    if [ -f "$d/configure" ]; then QEMU_SRC="$d"; break; fi
done
QEMU_SRC="${QEMU_SRC:-$WORK_DIR/qemu-src}"
QEMU_BUILD="${QEMU_BUILD:-}"
[ -f "$WORK_DIR/qemu-arti-build/qemu-system-aarch64" ] && QEMU_BUILD="${QEMU_BUILD:-$WORK_DIR/qemu-arti-build}"
QEMU_BUILD="${QEMU_BUILD:-$WORK_DIR/qemu-arti-build}"
QEMU_TOOLS="${QEMU_TOOLS:-}"
[ -f "$WORK_DIR/qemu-build-tools/bin/ninja" ] && QEMU_TOOLS="${QEMU_TOOLS:-$WORK_DIR/qemu-build-tools}"
QEMU_TOOLS="${QEMU_TOOLS:-$WORK_DIR/qemu-build-tools}"
LINUX_SRC="${LINUX_SRC:-}"
# Auto-detect existing Linux source
for d in "$ARTI_DIR/../linux" "$WORK_DIR/linux-src"; do
    if [ -f "$d/Makefile" ]; then LINUX_SRC="$d"; break; fi
done
LINUX_SRC="${LINUX_SRC:-$WORK_DIR/linux-src}"
LINUX_BUILD="${LINUX_BUILD:-}"
[ -f "$WORK_DIR/arti-linux-build/arch/arm64/boot/Image" ] && LINUX_BUILD="${LINUX_BUILD:-$WORK_DIR/arti-linux-build}"
LINUX_BUILD="${LINUX_BUILD:-$WORK_DIR/arti-linux-build}"
SLIRP_INSTALL="${SLIRP_INSTALL:-}"
[ -f "$WORK_DIR/slirp-install/lib/libslirp.a" ] && SLIRP_INSTALL="${SLIRP_INSTALL:-$WORK_DIR/slirp-install}"
SLIRP_INSTALL="${SLIRP_INSTALL:-$WORK_DIR/slirp-install}"
BUSYBOX_DIR="${BUSYBOX_DIR:-}"
[ -f "$WORK_DIR/busybox-1.36.1/busybox" ] && BUSYBOX_DIR="${BUSYBOX_DIR:-$WORK_DIR/busybox-1.36.1}"
BUSYBOX_DIR="${BUSYBOX_DIR:-$WORK_DIR/busybox-1.36.1}"
DEBIAN_QCOW2="${DEBIAN_QCOW2:-}"
[ -f "$WORK_DIR/arti-dev.qcow2" ] && DEBIAN_QCOW2="${DEBIAN_QCOW2:-$WORK_DIR/arti-dev.qcow2}"
DEBIAN_QCOW2="${DEBIAN_QCOW2:-$WORK_DIR/arti-dev.qcow2}"
CLOUD_INIT_ISO="${CLOUD_INIT_ISO:-}"
[ -f "$WORK_DIR/cloud-init.iso" ] && CLOUD_INIT_ISO="${CLOUD_INIT_ISO:-$WORK_DIR/cloud-init.iso}"
CLOUD_INIT_ISO="${CLOUD_INIT_ISO:-$WORK_DIR/cloud-init.iso}"

# QEMU and Linux versions
QEMU_VERSION="${QEMU_VERSION:-11.1.0}"
LINUX_VERSION="${LINUX_VERSION:-7.2}"

# Print detected paths
echo "Detected paths:"
echo "  ARTI_DIR      : $ARTI_DIR"
echo "  QEMU_SRC      : $QEMU_SRC"
echo "  QEMU_BUILD    : $QEMU_BUILD"
echo "  QEMU_TOOLS    : $QEMU_TOOLS"
echo "  LINUX_SRC     : $LINUX_SRC"
echo "  LINUX_BUILD   : $LINUX_BUILD"
echo "  SLIRP_INSTALL : $SLIRP_INSTALL"
echo "  BUSYBOX_DIR   : $BUSYBOX_DIR"
echo "  DEBIAN_QCOW2  : $DEBIAN_QCOW2"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

step()   { echo -e "\n${GREEN}========================================${NC}"; echo -e "${GREEN}  $*${NC}"; echo -e "${GREEN}========================================${NC}"; }

OS_NAME="$(uname -s)"
PKG_MANAGER=""
case "$OS_NAME" in
    Darwin)
        PKG_MANAGER="brew"
        ;;
    Linux)
        if command -v apt-get >/dev/null 2>&1; then
            PKG_MANAGER="apt-get"
        elif command -v dnf >/dev/null 2>&1; then
            PKG_MANAGER="dnf"
        elif command -v yum >/dev/null 2>&1; then
            PKG_MANAGER="yum"
        elif command -v pacman >/dev/null 2>&1; then
            PKG_MANAGER="pacman"
        elif command -v zypper >/dev/null 2>&1; then
            PKG_MANAGER="zypper"
        elif command -v apk >/dev/null 2>&1; then
            PKG_MANAGER="apk"
        fi
        ;;
esac

if [ "$PKG_MANAGER" = "brew" ] && ! command -v brew >/dev/null 2>&1; then
    fail "Homebrew not found. Install it from https://brew.sh and re-run."
fi
if [ "$PKG_MANAGER" = "brew" ]; then
    export HOMEBREW_NO_AUTO_UPDATE=1
fi

# Map a command to the package name used by the detected package manager.
pkg_name() {
    case "$PKG_MANAGER:$1" in
        dnf:g++|zypper:g++) echo "gcc-c++" ;;
        pacman:g++)          echo "gcc" ;;
        pacman:python3)      echo "python" ;;
        pacman:xorriso|apk:xorriso) echo "libisoburn" ;;
        *)                   echo "$1" ;;
    esac
}

run_pkg_install() {
    local label="$1"
    shift
    local pkg_prefix=""
    if [ "$(id -u)" -ne 0 ] && [ "$PKG_MANAGER" != "brew" ] && command -v sudo >/dev/null 2>&1; then
        pkg_prefix="sudo"
    fi
    info "Installing $label via $PKG_MANAGER..."
    case "$PKG_MANAGER" in
        brew)
            if ! brew install "$@"; then
                fail "Failed to install $label via brew"
            fi
            ;;
        apt-get)
            if ! $pkg_prefix apt-get install -y "$@"; then
                fail "Failed to install $label via apt-get"
            fi
            ;;
        dnf)
            if ! $pkg_prefix dnf install -y "$@"; then
                fail "Failed to install $label via dnf"
            fi
            ;;
        yum)
            if ! $pkg_prefix yum install -y "$@"; then
                fail "Failed to install $label via yum"
            fi
            ;;
        pacman)
            if ! $pkg_prefix pacman -S --noconfirm --needed "$@"; then
                fail "Failed to install $label via pacman"
            fi
            ;;
        zypper)
            if ! $pkg_prefix zypper --non-interactive install "$@"; then
                fail "Failed to install $label via zypper"
            fi
            ;;
        apk)
            if ! $pkg_prefix apk add "$@"; then
                fail "Failed to install $label via apk"
            fi
            ;;
        *)
            fail "$label not found. Install it with your package manager (package: $*) and re-run."
            ;;
    esac
}

check_cmd() {
    local cmd="$1"
    local pkg
    pkg="$(pkg_name "$cmd")"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        if [ -z "$PKG_MANAGER" ]; then
            fail "$cmd not found. Install it on your OS (package: $pkg) and re-run."
        fi
        run_pkg_install "$cmd" "$pkg"
        command -v "$cmd" >/dev/null 2>&1 || fail "$cmd still not found after installation"
    fi
}

# Linux kernel builds need the GNU versions of these tools on macOS.
ensure_macos_gnu_tools() {
    local pkg missing dir brew_bin
    [ "$OS_NAME" = "Darwin" ] || return 0

    missing=""
    for pkg in coreutils findutils gnu-sed grep make gnu-tar; do
        if ! brew list "$pkg" >/dev/null 2>&1; then
            missing="$missing $pkg"
        fi
    done
    if [ -n "$missing" ]; then
        run_pkg_install "macOS GNU build tools" $missing
    fi

    for pkg in coreutils findutils gnu-sed grep make gnu-tar; do
        dir="$(brew --prefix "$pkg" 2>/dev/null || true)/libexec/gnubin"
        if [ -d "$dir" ]; then
            PATH="$dir:$PATH"
        fi
    done
    brew_bin="$(brew --prefix 2>/dev/null || true)/bin"
    PATH="$brew_bin:$PATH"
    export PATH
}

ensure_qemu_build_deps() {
    if pkg-config --exists glib-2.0 pixman-1 2>/dev/null; then
        return 0
    fi

    case "$PKG_MANAGER" in
        brew)
            run_pkg_install "QEMU build dependencies" glib pixman
            ;;
        apt-get)
            run_pkg_install "QEMU build dependencies" libglib2.0-dev libpixman-1-dev
            ;;
        dnf|yum)
            run_pkg_install "QEMU build dependencies" glib2-devel pixman-devel
            ;;
        pacman)
            run_pkg_install "QEMU build dependencies" glib2 pixman
            ;;
        zypper)
            run_pkg_install "QEMU build dependencies" glib2-devel pixman-devel
            ;;
        apk)
            run_pkg_install "QEMU build dependencies" glib-dev pixman-dev
            ;;
        *)
            fail "glib-2.0/pixman-1 not found. Install their development packages, then re-run."
            ;;
    esac

    pkg-config --exists glib-2.0 pixman-1 2>/dev/null || \
        fail "glib-2.0/pixman-1 still not found after installation"
}

patch_qemu_for_arti() {
    local qemu_src="$1"
    python3 - "$qemu_src" <<'PY' || fail "Failed to patch QEMU for arti-rtl"
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])

meson = root / "hw/misc/meson.build"
text = meson.read_text()
if "CONFIG_ARTI_RTL" not in text:
    text += """
arti_rtl_model_dep = cc.find_library('arti_rtl_model', dirs: meson.current_source_dir(), required: true)
if host_machine.system() == 'darwin'
  arti_rtl_cxx_dep = cc.find_library('c++', required: true)
else
  arti_rtl_cxx_dep = cc.find_library('stdc++', required: true)
endif
system_ss.add(when: 'CONFIG_ARTI_RTL', if_true: [files('arti-rtl.c'), arti_rtl_model_dep, arti_rtl_cxx_dep])
"""
    meson.write_text(text)

kconfig = root / "hw/misc/Kconfig"
text = kconfig.read_text()
if "config ARTI_RTL" not in text:
    text += """
config ARTI_RTL
    bool
    default y
"""
    kconfig.write_text(text)

virt = root / "hw/arm/virt.c"
text = virt.read_text()
stub_text = (root / "hw/misc/arti-rtl.c").read_text()
has_display = "GraphicHwOps" in stub_text
marker = "/* ARTI embedded RTL device (auto-generated by setup_env.sh) */"
fb_marker = "/* ARTI framebuffer DT (auto-generated by setup_env.sh) */"
if marker not in text:
    func = """
static void create_arti_rtl(void)
{
    DeviceState *dev = qdev_new("arti-rtl");
    SysBusDevice *sbdev = SYS_BUS_DEVICE(dev);
    sysbus_realize_and_unref(sbdev, &error_fatal);
    sysbus_mmio_map(sbdev, 0, 0x0b000000);
}
"""
    needle = "static void machvirt_init(MachineState *machine)"
    if needle not in text:
        raise SystemExit("cannot find machvirt_init in " + str(virt))
    text = text.replace(needle, marker + func + "\n" + needle, 1)

    needle_call = "    create_platform_bus(vms);"
    if needle_call not in text:
        raise SystemExit("cannot find create_platform_bus in " + str(virt))
    text = text.replace(needle_call,
                        "    create_arti_rtl();\n\n" + needle_call, 1)
    virt.write_text(text)

if has_display:
    if fb_marker in text and ("__FB_BASE__" in text or
                              "{fb_base:x}" in text or "0x0x" in text):
        start = text.index(fb_marker)
        end = text.index("static void machvirt_init", start)
        text = text[:start] + text[end:]
        text = text.replace("    create_arti_rtl_fb_dt(machine);\n", "", 1)
        virt.write_text(text)
    if fb_marker not in text:
        fb_offset = int(re.search(
            r"#define ARTI_FB_OFFSET 0x([0-9a-fA-F]+)u", stub_text).group(1), 16)
        fb_width = int(re.search(
            r"#define ARTI_FB_WIDTH (\d+)u", stub_text).group(1))
        fb_height = int(re.search(
            r"#define ARTI_FB_HEIGHT (\d+)u", stub_text).group(1))
        fb_size = int(re.search(
            r"#define ARTI_FB_SIZE 0x([0-9a-fA-F]+)u", stub_text).group(1), 16)
        fb_base = 0x0b000000 + fb_offset
        fb_stride = fb_width * 4
        fb_func = """
static void create_arti_rtl_fb_dt(MachineState *ms)
{
    if (!ms->fdt) {
        return;
    }
    qemu_fdt_add_subnode(ms->fdt, "/framebuffer");
    qemu_fdt_setprop_string(ms->fdt, "/framebuffer",
                            "compatible", "simple-framebuffer");
    qemu_fdt_setprop_cells(ms->fdt, "/framebuffer", "reg",
                           0x0, 0x__FB_BASE__, 0x0, 0x__FB_SIZE__);
    qemu_fdt_setprop_cells(ms->fdt, "/framebuffer", "width", 0x__FB_WIDTH__);
    qemu_fdt_setprop_cells(ms->fdt, "/framebuffer", "height", 0x__FB_HEIGHT__);
    qemu_fdt_setprop_cells(ms->fdt, "/framebuffer", "stride", 0x__FB_STRIDE__);
    qemu_fdt_setprop_string(ms->fdt, "/framebuffer", "format", "a8r8g8b8");
}
"""
        fb_func = fb_func.replace("__FB_BASE__", format(fb_base, "x"))
        fb_func = fb_func.replace("__FB_SIZE__", format(fb_size, "x"))
        fb_func = fb_func.replace("__FB_WIDTH__", format(fb_width, "x"))
        fb_func = fb_func.replace("__FB_HEIGHT__", format(fb_height, "x"))
        fb_func = fb_func.replace("__FB_STRIDE__", format(fb_stride, "x"))
        needle = "static void machvirt_init(MachineState *machine)"
        if needle not in text:
            raise SystemExit("cannot find machvirt_init in " + str(virt))
        text = text.replace(needle, fb_marker + fb_func + "\n" + needle, 1)
        call_needle = "    create_arti_rtl();"
        if call_needle in text:
            text = text.replace(call_needle,
                                call_needle + "\n    create_arti_rtl_fb_dt(machine);", 1)
        else:
            fallback = "    create_platform_bus(vms);"
            if fallback not in text:
                raise SystemExit("cannot find arti-rtl call in " + str(virt))
            text = text.replace(fallback,
                                "    create_arti_rtl_fb_dt(machine);\n\n" + fallback, 1)
        virt.write_text(text)
elif fb_marker in text:
    start = text.index(fb_marker)
    end = text.index("static void machvirt_init", start)
    text = text[:start] + text[end:]
    text = text.replace("    create_arti_rtl_fb_dt(machine);\n", "", 1)
    virt.write_text(text)
PY
    grep -q "CONFIG_ARTI_RTL" "$qemu_src/hw/misc/meson.build" || \
        fail "arti-rtl meson integration missing"
    grep -q "create_arti_rtl" "$qemu_src/hw/arm/virt.c" || \
        fail "arti-rtl virt integration missing"
    if grep -q "GraphicHwOps" "$qemu_src/hw/misc/arti-rtl.c"; then
        grep -q "create_arti_rtl_fb_dt" "$qemu_src/hw/arm/virt.c" || \
            fail "arti-rtl framebuffer DT integration missing"
    fi
}

ensure_macos_bee_headers() {
    [ "$OS_NAME" = "Darwin" ] || return 0
    if brew list bee-headers >/dev/null 2>&1; then
        return 0
    fi
    info "Installing bee-headers for Linux kernel host tools..."
    if ! brew tap bee-headers/bee-headers; then
        fail "Failed to add bee-headers/bee-headers tap"
    fi
    brew trust --formula bee-headers/bee-headers/bee-headers || true
    if ! brew install bee-headers; then
        fail "Failed to install bee-headers"
    fi
}

patch_linux_for_macos() {
    [ "$OS_NAME" = "Darwin" ] || return 0
    python3 - "$LINUX_SRC" <<'PY' || fail "Failed to patch Linux source for macOS"
import pathlib
import sys

root = pathlib.Path(sys.argv[1])

file2alias = root / "scripts/mod/file2alias.c"
text = file2alias.read_text()
if "#define uuid_t arti_uuid_t" not in text:
    old = "typedef struct {\n\t__u8 b[16];\n} uuid_t;"
    new = ("#ifdef __APPLE__\n#define uuid_t arti_uuid_t\n#endif\n"
           "typedef struct {\n\t__u8 b[16];\n} uuid_t;")
    text = text.replace(old, new, 1)
    file2alias.write_text(text)

modpost = root / "scripts/mod/modpost.c"
text = modpost.read_text()
old = "sep = strchrnul(namespace, ',');"
new = ("sep = strchr(namespace, ',');\n"
       "\t\tif (!sep)\n"
       "\t\t\tsep = namespace + strlen(namespace);")
if old in text:
    text = text.replace(old, new, 1)
    modpost.write_text(text)

gen_init_cpio = root / "usr/gen_init_cpio.c"
text = gen_init_cpio.read_text()
if "#define copy_file_range(a, b, c, d, e, f) (-1)" not in text:
    marker = "#include <limits.h>\n"
    compat = (marker +
              "#ifdef __APPLE__\n"
              "#define O_LARGEFILE 0\n"
              "#define copy_file_range(a, b, c, d, e, f) (-1)\n"
              "#endif\n")
    text = text.replace(marker, compat, 1)
    gen_init_cpio.write_text(text)
PY
}

find_cross_compiler() {
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

install_cross_compiler() {
    case "$PKG_MANAGER" in
        brew)
            info "Installing AArch64 Linux cross compiler via Homebrew tap..."
            if ! brew tap messense/macos-cross-toolchains; then
                fail "Failed to add messense/macos-cross-toolchains tap"
            fi
            if ! brew install aarch64-unknown-linux-gnu; then
                fail "Failed to install aarch64-unknown-linux-gnu"
            fi
            ;;
        apt-get|dnf|yum)
            run_pkg_install "AArch64 Linux cross compiler" gcc-aarch64-linux-gnu
            ;;
        pacman)
            run_pkg_install "AArch64 Linux cross compiler" aarch64-linux-gnu-gcc
            ;;
        zypper)
            run_pkg_install "AArch64 Linux cross compiler" cross-aarch64-gcc
            ;;
        apk)
            run_pkg_install "AArch64 Linux cross compiler" aarch64-linux-gnu-gcc
            ;;
        *)
            fail "AArch64 Linux cross compiler not found. Install aarch64-linux-gnu-gcc, aarch64-unknown-linux-gnu-gcc, aarch64-none-linux-gnu-gcc, or aarch64-linux-musl-gcc; then re-run."
            ;;
    esac
    find_cross_compiler || fail "AArch64 Linux cross compiler installation failed"
}

if [ -n "${JOBS:-}" ]; then
    BUILD_JOBS="$JOBS"
elif command -v nproc >/dev/null 2>&1; then
    BUILD_JOBS="$(nproc)"
elif [ "$OS_NAME" = "Darwin" ]; then
    BUILD_JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 1)"
else
    BUILD_JOBS=1
fi

sedi() {
    if sed --version >/dev/null 2>&1; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}

# ---------------------------------------------------------------------------
# 0. Check system dependencies
# ---------------------------------------------------------------------------
step "Step 0/7: Checking system dependencies"

check_cmd verilator
check_cmd python3
check_cmd g++
check_cmd git
check_cmd curl
check_cmd xorriso
check_cmd cpio
check_cmd pkg-config
check_cmd meson
check_cmd make

ensure_macos_gnu_tools
ensure_qemu_build_deps

if ! find_cross_compiler; then
    install_cross_compiler
fi
info "Cross compiler: $CROSS_GCC (CROSS_COMPILE=$CROSS_COMPILE)"

# Check Python yaml module for cloud-init validation
python3 -c "import yaml" 2>/dev/null || warn "PyYAML not found (cloud-init YAML validation will be skipped)"

info "System dependencies OK"

# ---------------------------------------------------------------------------
# 1. Build tools (ninja via pip --target)
# ---------------------------------------------------------------------------
step "Step 1/7: Installing build tools (ninja)"

if [ -f "$QEMU_TOOLS/bin/ninja" ]; then
    info "ninja already installed at $QEMU_TOOLS/bin/ninja"
else
    info "Installing ninja via pip --target..."
    pip3 install --target="$QEMU_TOOLS" ninja 2>/dev/null || \
        python3 -m pip install --target="$QEMU_TOOLS" ninja || \
        fail "Failed to install ninja. Try: pip3 install --target=$QEMU_TOOLS ninja"
    [ -f "$QEMU_TOOLS/bin/ninja" ] || fail "ninja installation failed"
fi
export PATH="$QEMU_TOOLS/bin:$PATH"
info "ninja version: $(ninja --version)"

# ---------------------------------------------------------------------------
# 2. QEMU (with embedded arti-rtl device + SLIRP)
# ---------------------------------------------------------------------------
step "Step 2/7: Building QEMU (with SLIRP + arti-rtl device)"

# 2a. Download/clone QEMU source
if [ -f "$QEMU_SRC/configure" ]; then
    info "QEMU source already exists at $QEMU_SRC"
else
    info "Downloading QEMU v$QEMU_VERSION source..."
    mkdir -p "$(dirname "$QEMU_SRC")"
    curl -sSL "https://download.qemu.org/qemu-${QEMU_VERSION}.tar.xz" | tar xJ -C "$(dirname "$QEMU_SRC")"
    mv "$(dirname "$QEMU_SRC")/qemu-${QEMU_VERSION}" "$QEMU_SRC" 2>/dev/null || true
    [ -f "$QEMU_SRC/configure" ] || fail "QEMU source download failed"
fi

# 2b. Install arti-rtl device into QEMU source
info "Installing arti-rtl device into QEMU source..."
cp "$SCRIPT_DIR/../../src/arti/qemu_backend.py" /dev/null 2>/dev/null || true
# Generate the arti-rtl.c and arti_rtl_model.h from the arti framework
# using simple_gpio as the default RTL model
GEN_DIR="$WORK_DIR/arti-embedded-gen"
mkdir -p "$GEN_DIR"
cat > "$GEN_DIR/config.yaml" << YAMLEOF
project:
  name: arti_embedded
rtl:
  top_module: simple_gpio
  source_files: [$ARTI_DIR/examples/simple_gpio/simple_gpio.v]
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

EXPECT_DISPLAY=0
if [ "${ARTI_DISPLAY:-0}" = "1" ] || [ "${ARTI_DISPLAY:-0}" = "true" ]; then
    EXPECT_DISPLAY=1
fi
STUB_HAS_DISPLAY=0
if grep -q "GraphicHwOps" "$GEN_DIR/generated/qemu/arti-rtl.c" 2>/dev/null; then
    STUB_HAS_DISPLAY=1
fi

if [ -f "$GEN_DIR/generated/embedded/arti_rtl_model.cpp" ] && \
   grep -q "SKIP_QEMU_REBUILD" "$GEN_DIR/generated/embedded/build_embedded.sh" && \
   grep -q "sc_time_stamp" "$GEN_DIR/generated/embedded/arti_rtl_model.cpp" && \
   grep -q "arti-qemu-stub-v3" "$GEN_DIR/generated/qemu/arti-rtl.c" && \
   [ "$STUB_HAS_DISPLAY" = "$EXPECT_DISPLAY" ]; then
    info "Embedded model already generated"
else
    info "Generating embedded model..."
    rm -rf "$GEN_DIR/generated"
    PYTHONPATH="$ARTI_DIR/src" python3 -c "
import sys; sys.path.insert(0, '$ARTI_DIR/src')
from arti.cli import main
main(['generate', '$GEN_DIR/config.yaml', '--output', '$GEN_DIR/generated'])
" || fail "Failed to generate embedded model"
fi

# Copy arti-rtl.c and arti_rtl_model.h into QEMU source tree
cp "$GEN_DIR/generated/qemu/arti-rtl.c" "$QEMU_SRC/hw/misc/arti-rtl.c"
cp "$GEN_DIR/generated/embedded/arti_rtl_model.h" "$QEMU_SRC/hw/misc/arti_rtl_model.h"

# 2c. Build the Verilated static library
info "Building Verilated RTL model..."
VERILATOR_INC="${VERILATOR_INC:-$(dirname $(dirname $(which verilator)))/share/verilator/include}"
if ! QEMU_SRC="$QEMU_SRC" QEMU_BUILD="$QEMU_BUILD" VERILATOR_INC="$VERILATOR_INC" \
        SKIP_QEMU_REBUILD=1 bash "$GEN_DIR/generated/embedded/build_embedded.sh" 2>&1 | tail -5; then
    fail "Failed to build embedded RTL model"
fi

# Ensure the library and header are in place
cp "$GEN_DIR/generated/embedded/arti_rtl_model.h" "$QEMU_SRC/hw/misc/arti_rtl_model.h"
patch_qemu_for_arti "$QEMU_SRC"

# 2d. Build libslirp
if [ -f "$SLIRP_INSTALL/lib/libslirp.a" ]; then
    info "libslirp already built at $SLIRP_INSTALL"
else
    info "Building libslirp (static)..."
    SLIRP_SRC="$WORK_DIR/libslirp-4.8.0"
    if [ ! -d "$SLIRP_SRC" ]; then
        curl -sSL "https://gitlab.freedesktop.org/slirp/libslirp/-/archive/v4.8.0/libslirp-v4.8.0.tar.gz" | tar xz -C "$WORK_DIR"
        mv "$WORK_DIR/libslirp-v4.8.0" "$SLIRP_SRC" 2>/dev/null || true
    fi
    mkdir -p "$SLIRP_SRC/build"
    cd "$SLIRP_SRC/build"
    PKG_CONFIG_PATH="" meson setup --default-library=static --prefix="$SLIRP_INSTALL" . .. 2>&1 | tail -3
    ninja -C . 2>&1 | tail -3
    ninja -C . install 2>&1 | tail -3
    [ -f "$SLIRP_INSTALL/lib/libslirp.a" ] || fail "libslirp build failed"
    cd -
fi

# 2e. Configure and build QEMU
if [ -f "$QEMU_BUILD/qemu-system-aarch64" ] && \
   [ ! "$QEMU_SRC/hw/misc/arti-rtl.c" -nt "$QEMU_BUILD/qemu-system-aarch64" ]; then
    info "QEMU already built at $QEMU_BUILD/qemu-system-aarch64"
else
    info "Configuring QEMU..."
    mkdir -p "$QEMU_BUILD"
    cd "$QEMU_BUILD"
    export PATH="$QEMU_TOOLS/bin:$PATH"
    export NINJA="$QEMU_TOOLS/bin/ninja"
    export PKG_CONFIG_PATH="$SLIRP_INSTALL/lib/pkgconfig"
    "$QEMU_SRC/configure" \
        --target-list=aarch64-softmmu \
        --disable-werror --disable-docs \
        --disable-gtk --disable-sdl \
        --disable-opengl --disable-virglrenderer \
        2>&1 | tail -5

    # Set SLIRP path via meson (meson overwrites PKG_CONFIG_PATH)
    info "Enabling SLIRP in meson..."
    "$QEMU_BUILD/pyvenv/bin/meson" configure . \
        -Dpkg_config_path="$SLIRP_INSTALL/lib/pkgconfig" \
        -Dslirp=enabled 2>&1 | tail -3
    "$QEMU_BUILD/pyvenv/bin/meson" setup --reconfigure --clearcache "$QEMU_SRC" "$QEMU_BUILD" 2>&1 | tail -3

    info "Building QEMU (this takes a few minutes)..."
    ninja -C "$QEMU_BUILD" qemu-system-aarch64 qemu-img 2>&1 | tail -5
    [ -f "$QEMU_BUILD/qemu-system-aarch64" ] || fail "QEMU build failed"
    cd -
fi

# Verify QEMU
if "$QEMU_BUILD/qemu-system-aarch64" -machine virt -netdev help 2>&1 | grep -q "^user$"; then
    info "QEMU built with SLIRP networking support"
else
    warn "QEMU built without SLIRP (networking will not work, MMIO tests still OK)"
fi

# ---------------------------------------------------------------------------
# 3. Linux kernel (AArch64)
# ---------------------------------------------------------------------------
step "Step 3/7: Building Linux kernel (AArch64)"

LINUX_HOST_CFLAGS="${HOSTCFLAGS:-}"
if [ "$OS_NAME" = "Darwin" ]; then
    LINUX_HOST_CFLAGS="$LINUX_HOST_CFLAGS -I$(brew --prefix 2>/dev/null || true)/include"
fi

ARTI_KERNEL_MARKER="$LINUX_BUILD/.arti-kernel-options-v3"
if [ -f "$LINUX_BUILD/arch/arm64/boot/Image" ] && [ -f "$ARTI_KERNEL_MARKER" ]; then
    info "Kernel already built at $LINUX_BUILD/arch/arm64/boot/Image"
else
    info "Downloading Linux v$LINUX_VERSION source..."
    if [ ! -f "$LINUX_SRC/Makefile" ]; then
        curl -sSL "https://cdn.kernel.org/pub/linux/kernel/v${LINUX_VERSION%%.*}.x/linux-${LINUX_VERSION}.tar.xz" | tar xJ -C "$(dirname "$LINUX_SRC")"
        mv "$(dirname "$LINUX_SRC")/linux-${LINUX_VERSION}" "$LINUX_SRC" 2>/dev/null || true
    fi
    [ -f "$LINUX_SRC/Makefile" ] || fail "Linux source download failed"

    ensure_macos_bee_headers
    patch_linux_for_macos

    mkdir -p "$LINUX_BUILD"
    cd "$LINUX_SRC"

    info "Configuring kernel (defconfig + ARTI options)..."
    make O="$LINUX_BUILD" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
        HOSTCFLAGS="$LINUX_HOST_CFLAGS" defconfig 2>&1 | tail -3

    # Enable required kernel options
    cd "$LINUX_BUILD"
    sedi 's/# CONFIG_NETDEVICES is not set/CONFIG_NETDEVICES=y/' .config
    echo "CONFIG_VIRTIO_NET=y" >> .config
    echo "CONFIG_EXT4_FS=y" >> .config
    echo "CONFIG_ISO9660_FS=y" >> .config
    echo "CONFIG_TMPFS=y" >> .config
    echo "CONFIG_MODULE_UNLOAD=y" >> .config
    echo "CONFIG_VIRTIO_BLK=y" >> .config
    echo "CONFIG_DEVTMPFS=y" >> .config
    echo "CONFIG_DEVTMPFS_MOUNT=y" >> .config
    echo "CONFIG_FB=y" >> .config
    echo "CONFIG_FB_SIMPLE=y" >> .config
    echo "CONFIG_FRAMEBUFFER_CONSOLE=y" >> .config
    echo "CONFIG_VIRTIO_INPUT=y" >> .config
    make ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
        HOSTCFLAGS="$LINUX_HOST_CFLAGS" olddefconfig 2>&1 | tail -3

    info "Building kernel (this takes a few minutes)..."
    make ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
        HOSTCFLAGS="$LINUX_HOST_CFLAGS" -j"$BUILD_JOBS" Image modules 2>&1 | tail -5
    [ -f "$LINUX_BUILD/arch/arm64/boot/Image" ] || fail "Kernel build failed"
    touch "$ARTI_KERNEL_MARKER"
    cd -
fi
info "Kernel: $LINUX_BUILD/arch/arm64/boot/Image ($(du -sh "$LINUX_BUILD/arch/arm64/boot/Image" | cut -f1))"

# ---------------------------------------------------------------------------
# 4. Busybox (static binary for initramfs)
# ---------------------------------------------------------------------------
step "Step 4/7: Building Busybox (static, AArch64)"

if [ -f "$BUSYBOX_DIR/busybox" ]; then
    info "Busybox already built at $BUSYBOX_DIR/busybox"
else
    if [ ! -d "$BUSYBOX_DIR" ]; then
        info "Downloading Busybox 1.36.1..."
        curl -sSL "https://busybox.net/downloads/busybox-1.36.1.tar.bz2" | tar xj -C "$WORK_DIR"
    fi
    cd "$BUSYBOX_DIR"
    info "Configuring busybox (static)..."
    make ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" defconfig 2>&1 | tail -3
    sedi 's/# CONFIG_STATIC is not set/CONFIG_STATIC=y/' .config
    make ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" -j"$BUILD_JOBS" 2>&1 | tail -5
    [ -f "$BUSYBOX_DIR/busybox" ] || fail "Busybox build failed"
    cd -
fi
info "Busybox: $BUSYBOX_DIR/busybox ($(du -sh "$BUSYBOX_DIR/busybox" | cut -f1))"

# Build Alpine-style initramfs for interactive mode
ALPINE_CPIO="${ALPINE_CPIO:-}"
[ -f "$WORK_DIR/arti-alpine.cpio.gz" ] && ALPINE_CPIO="${ALPINE_CPIO:-$WORK_DIR/arti-alpine.cpio.gz}"
ALPINE_CPIO="${ALPINE_CPIO:-$WORK_DIR/arti-alpine.cpio.gz}"
if [ ! -f "$ALPINE_CPIO" ]; then
    info "Building Alpine-style initramfs..."
    INITRAMFS_DIR="$WORK_DIR/alpine-initramfs"
    rm -rf "$INITRAMFS_DIR"
    mkdir -p "$INITRAMFS_DIR"/{bin,sbin,proc,sys,dev,lib,usr/bin,usr/sbin}
    cp "$BUSYBOX_DIR/busybox" "$INITRAMFS_DIR/bin/busybox"
    ln -sf busybox "$INITRAMFS_DIR/bin/sh"

    cat > "$INITRAMFS_DIR/init" << 'INIT'
#!/bin/sh
/bin/busybox --install -s /bin
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev 2>/dev/null
echo ""
echo "=== ARTI Linux (Alpine busybox) ==="
echo "RTL device at MMIO 0x0B000000"
echo "Commands: insmod, lsmod, rmmod, devmem, dmesg, cat"
echo ""
exec /bin/sh
INIT
    chmod +x "$INITRAMFS_DIR/init"
    (cd "$INITRAMFS_DIR" && find . | cpio -H newc -o 2>/dev/null) | gzip -9 > "$ALPINE_CPIO"
    info "Initramfs: $ALPINE_CPIO ($(du -sh "$ALPINE_CPIO" | cut -f1))"
fi

# ---------------------------------------------------------------------------
# 5. Debian 12 cloud rootfs (for full dev environment)
# ---------------------------------------------------------------------------
step "Step 5/7: Downloading Debian 12 cloud rootfs"

DEBIAN_BASE="${DEBIAN_BASE:-}"
[ -f "$WORK_DIR/debian-arm64.qcow2" ] && DEBIAN_BASE="${DEBIAN_BASE:-$WORK_DIR/debian-arm64.qcow2}"
DEBIAN_BASE="${DEBIAN_BASE:-$WORK_DIR/debian-arm64-base.qcow2}"
if [ -f "$DEBIAN_QCOW2" ]; then
    info "Debian rootfs already exists at $DEBIAN_QCOW2"
else
    if [ ! -f "$DEBIAN_BASE" ]; then
        info "Downloading Debian 12 bookworm ARM64 cloud image (~330MB)..."
        curl -sSL -o "$DEBIAN_BASE" \
            "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-arm64.qcow2"
        [ -f "$DEBIAN_BASE" ] || fail "Debian image download failed"
    fi
    info "Creating 10GB working copy..."
    cp "$DEBIAN_BASE" "$DEBIAN_QCOW2"
    qemu-img resize "$DEBIAN_QCOW2" 10G 2>/dev/null || \
        "$QEMU_BUILD/qemu-img" resize "$DEBIAN_QCOW2" 10G || \
        warn "qemu-img not found, disk not resized (still usable)"
fi
info "Debian rootfs: $DEBIAN_QCOW2 ($(du -sh "$DEBIAN_QCOW2" | cut -f1))"

# Build cloud-init ISO
if [ -f "$CLOUD_INIT_ISO" ] && [ -f "$SCRIPT_DIR/build_cloudinit.sh" ]; then
    info "Cloud-init ISO already exists at $CLOUD_INIT_ISO"
elif [ -f "$SCRIPT_DIR/build_cloudinit.sh" ]; then
    info "Building cloud-init ISO..."
    OUTPUT="$CLOUD_INIT_ISO" bash "$SCRIPT_DIR/build_cloudinit.sh" 2>&1 | tail -3
else
    warn "build_cloudinit.sh not found, skipping cloud-init ISO"
fi

# ---------------------------------------------------------------------------
# 6. Driver module (.ko)
# ---------------------------------------------------------------------------
step "Step 6/7: Building driver module (.ko)"

info "Building arti_rtl_test.ko..."
grep -q "^obj-m" "$SCRIPT_DIR/Makefile" 2>/dev/null || \
    printf 'obj-m += arti_rtl_test.o\n' > "$SCRIPT_DIR/Makefile"
make -C "$LINUX_BUILD" M="$SCRIPT_DIR" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
    HOSTCFLAGS="$LINUX_HOST_CFLAGS" modules 2>&1 | tail -5
[ -f "$SCRIPT_DIR/arti_rtl_test.ko" ] || fail "Driver module build failed"
info "Driver: $SCRIPT_DIR/arti_rtl_test.ko ($(du -sh "$SCRIPT_DIR/arti_rtl_test.ko" | cut -f1))"

# ---------------------------------------------------------------------------
# 7. Summary
# ---------------------------------------------------------------------------
step "Step 7/7: Setup complete!"

echo ""
echo "Build artifacts:"
echo "  QEMU       : $QEMU_BUILD/qemu-system-aarch64"
echo "  Kernel     : $LINUX_BUILD/arch/arm64/boot/Image"
echo "  Busybox    : $BUSYBOX_DIR/busybox"
echo "  Initramfs  : $ALPINE_CPIO"
echo "  Debian disk: $DEBIAN_QCOW2"
echo "  Cloud-init : $CLOUD_INIT_ISO"
echo "  Driver .ko : $SCRIPT_DIR/arti_rtl_test.ko"
echo ""
echo "Run examples:"
echo "  $SCRIPT_DIR/run_linux_test.sh    # automated end-to-end test"
echo "  $SCRIPT_DIR/run_interactive.sh   # interactive busybox shell"
echo "  $SCRIPT_DIR/run_debian.sh        # full Debian dev environment"
echo ""
echo "To use a different RTL:"
echo "  RTL=examples/irq_timer/irq_timer.v TOP=irq_timer \\"
echo "    $SCRIPT_DIR/build_embedded_qemu.sh"
echo "  # then rebuild .ko and run test"
