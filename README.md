# ARTI

ARTI is an initial implementation of the automatic RTL-to-QEMU integration framework
described in `design_report.md`. The current M1 milestone parses ANSI-style Verilog
top-level ports, ranks five standard bus protocols (AXI-Lite, AXI4, AHB, APB, and
AXI-Stream), and generates both a SystemC/Verilator co-simulation project and an
embedded QEMU device model for the inferred protocol. Given an RTL design as input,
users can quickly run full-system simulation or functional tests: the generated RTL
model can be integrated into QEMU as an `arti-rtl` SysBus device; in embedded mode,
Verilated RTL is compiled directly into QEMU so a Linux guest can exercise the RTL
through MMIO without an external cosim process or Unix socket.

## Quick start (one-click)

Install all dependencies and run the examples in one step (suitable for a fresh
environment):

```bash
# Install environment + run end-to-end test
./examples/linux_arti_driver/run.sh

# Install environment + interactive busybox shell
./examples/linux_arti_driver/run.sh interactive

# Install environment + full Debian development environment (with network)
./examples/linux_arti_driver/run.sh debian
```

`run.sh` automatically invokes `setup_env.sh`, which performs the following steps:

1. Install the ninja build tool
2. Download and build QEMU (with SLIRP network support + the arti-rtl device)
3. Download and build the Linux kernel (AArch64, with virtio-net + module loading + ext4)
4. Build Busybox (static binary, used for the initramfs)
5. Download the Debian 12 cloud rootfs + generate the cloud-init ISO
6. Build the driver module `.ko`
7. Generate the embedded RTL model and compile it into QEMU

You can also run `setup_env.sh` on its own to perform only the installation:

```bash
./examples/linux_arti_driver/setup_env.sh
```

After installation, all artifacts are placed under `/tmp`. The run scripts automatically
detect already-installed components and only install what is missing. `setup_env.sh`
automatically selects the package manager for the current OS to install missing
dependencies: Homebrew on macOS (including the AArch64 Linux cross-toolchain tap), and
apt/dnf/yum/pacman/zypper/apk on Linux.

## Quick start

No third-party Python package is required:

```bash
PYTHONPATH=src python3 -m arti.cli inspect examples/simple_gpio/simple_gpio.v
PYTHONPATH=src python3 -m arti.cli generate examples/simple_gpio/config.yaml --output /tmp/simple_gpio_cosim
```

Build the generated project on a machine with SystemC and Verilator installed:

```bash
/tmp/simple_gpio_cosim/build/run_cosim.sh
```

Run the framework tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

For the full Linux kernel driver end-to-end test (guest `insmod` -> MMIO -> QEMU -> RTL
loopback), see [section 5](#5-linux-kernel-driver-end-to-end-test). Quick run after
prerequisites are built:

```bash
examples/linux_arti_driver/run_linux_test.sh
```

## Current scope

- Verilog-2001 ANSI port declarations and integer parameters
- Naming-based AXI-Lite, AXI4, AXI-Stream, AHB, and APB protocol detection
- Confidence, missing-signal, unknown-port, signal-mapping, and interrupt reports
- **Full multi-protocol embedded model generation** for AXI-Lite, AXI4, APB, AHB, and
  AXI-Stream
- **Automatic interrupt support**: detects IRQ output ports, generates
  `arti_rtl_model_check_irq()` API, and wires QEMU SysBus IRQ with polling timer
- SystemC/Verilator AXI-Lite runtime bridge with generated local TLM self-test (local mode)
- Upstream QEMU SysBus MMIO stub and Unix-socket-to-TLM transport generation
- Full QEMU embedded mode: Verilated RTL compiled directly into QEMU, no IPC needed

A successful generated simulation prints `ARTI COSIM PASS`.


## Full usage tutorial

### Environment preparation

Python uses only the standard library. Regular simulation requires Verilator, SystemC,
CMake, and a C++ compiler; QEMU co-simulation additionally requires the official AArch64
GNU cross-compiler:

```bash
verilator --version
pkg-config --modversion systemc
cmake --version
aarch64-linux-gnu-gcc --version
meson --version
pkg-config --exists glib-2.0 pixman-1 && echo "QEMU build deps OK"
```

If the cross-compiler is missing, `setup_env.sh` installs it automatically; you can also
install it manually per platform:

```bash
# macOS
brew tap messense/macos-cross-toolchains
brew install aarch64-unknown-linux-gnu

# Debian / Ubuntu
sudo apt-get install -y gcc-aarch64-linux-gnu

# Fedora / RHEL
sudo dnf install -y gcc-aarch64-linux-gnu

# Arch Linux
sudo pacman -S --needed aarch64-linux-gnu-gcc

# openSUSE
sudo zypper --non-interactive install cross-aarch64-gcc
```

After installation on macOS the command is named `aarch64-unknown-linux-gnu-gcc`;
`setup_env.sh` and `run_linux_test.sh` automatically detect it and set the correct
`CROSS_COMPILE` prefix. On macOS, `setup_env.sh` also automatically installs the GNU
toolchain and `bee-headers` (which provide the `elf.h` / `byteswap.h` / `endian.h`
headers needed for host-side Linux kernel builds).

### Generic framebuffer display extension

After adding a `display` section to the config, the generated `arti-rtl.c` includes
`GraphicHwOps` and exposes a guest-writable framebuffer. The display device is at
`0x0B000000`, the framebuffer defaults to `0x0B100000`, and the format is 32bpp
`a8r8g8b8`. ARTI also automatically adds a `/framebuffer` node (`simple-framebuffer`) to
the virt device tree, so with `CONFIG_FB_SIMPLE` / `CONFIG_FRAMEBUFFER_CONSOLE` enabled,
Linux can use it as the boot display.

```yaml
display:
  enabled: true
  width: 1024
  height: 768
  format: a8r8g8b8
  framebuffer_offset: 0x100000
  framebuffer_size: 0x800000
```

`ARTI_DISPLAY=1` enables the framebuffer device in the embedded QEMU model. It is read
while the model is generated (during `setup_env.sh` or `build_embedded_qemu.sh`), not at
runtime. If you have already built the model without display support, rerun the setup with
`ARTI_DISPLAY=1` to regenerate it.

The `test` and `interactive` modes of `run.sh` always run with `-display none` and exit
after the test or shell session, so `ARTI_DISPLAY` alone does not open a graphics window.
To see the framebuffer, boot the Debian environment and set a QEMU UI backend:

```bash
# macOS
ARTI_DISPLAY=1 QEMU_DISPLAY=cocoa ./examples/linux_arti_driver/run.sh debian

# Linux
ARTI_DISPLAY=1 QEMU_DISPLAY=gtk ./examples/linux_arti_driver/run.sh debian
```

The Debian environment opens a QEMU graphics window by default (Cocoa on macOS, GTK on
Linux); you can override this with `QEMU_DISPLAY`, for example `QEMU_DISPLAY=none` to run
headless:

```bash
QEMU_DISPLAY=cocoa ./examples/linux_arti_driver/run.sh debian
QEMU_DISPLAY=none  ./examples/linux_arti_driver/run.sh debian
```

You can also generate directly from the example config:

```bash
PYTHONPATH=src python3 -m arti.cli generate \
  examples/simple_fb/config.yaml --output /tmp/simple_fb
```

The upstream QEMU source directory is specified by the `$QEMU_SRC` environment variable;
the built QEMU binary is at `/tmp/qemu-arti-build/qemu-system-aarch64`. After setting
the variable, rebuild:

```bash
export QEMU_SRC=${QEMU_SRC:-$(pwd)/../qemu}
mkdir -p /tmp/qemu-arti-build
PATH=/tmp/qemu-build-tools/bin:$PATH $QEMU_SRC/configure \
  --target-list=aarch64-softmmu --disable-werror --disable-docs \
  --disable-gtk --disable-sdl --disable-opengl --disable-virglrenderer
PATH=/tmp/qemu-build-tools/bin:$PATH ninja -C /tmp/qemu-arti-build qemu-system-aarch64
```

### 1. Inspect the RTL

```bash
PYTHONPATH=src python3 -m arti.cli inspect examples/simple_gpio/simple_gpio.v --top simple_gpio
```

Outputs JSON containing the port signature, protocol inference, signal mapping, missing
signals, and unknown ports.

### 2. Local SystemC/Verilator simulation

```bash
PYTHONPATH=src python3 -m arti.cli generate \
  examples/simple_gpio/config.yaml --output /tmp/simple_gpio_cosim
/tmp/simple_gpio_cosim/build/run_cosim.sh
```

On success it prints `ARTI COSIM PASS`. In the generated directory,
`bridge/bridge_top.h` is the AXI-Lite TLM-to-RTL bridge, and
`reports/inference_report.json` is the inference report.

### 3. Upstream QEMU SysBus co-simulation (socket mode)

> **Tip**: to test the driver in the Linux kernel, the embedded mode described in
> [section 5](#5-linux-kernel-driver-end-to-end-test) is recommended (RTL compiled
> directly into QEMU), with no cosim process or socket needed. This section describes
> socket mode, which is suitable when cycle-accurate SystemC simulation is required.

ARTI does not depend on PCI, VFIO, Xilinx QEMU, or remote-port; the data path is:

```text
AArch64 guest MMIO -> QEMU arti-rtl SysBus -> Unix socket
-> SystemC/TLM -> AXI-Lite -> Verilated RTL
```

First create a QEMU-mode config and build the cosim:

```bash
cp examples/simple_gpio/config.yaml /tmp/simple_gpio_qemu.yaml
sed -i '/bridge:/a\  mode: qemu-sysbus' /tmp/simple_gpio_qemu.yaml
PYTHONPATH=src python3 -m arti.cli generate /tmp/simple_gpio_qemu.yaml --output /tmp/simple_gpio_qemu
cmake -S /tmp/simple_gpio_qemu -B /tmp/simple_gpio_qemu/build/cmake
cmake --build /tmp/simple_gpio_qemu/build/cmake --parallel
```

Terminal A: start the SystemC socket server:

```bash
rm -f /tmp/arti-qemu.sock
/tmp/simple_gpio_qemu/build/cmake/cosim /tmp/arti-qemu.sock
```

Terminal B: start the upstream QEMU client:

```bash
/tmp/qemu-arti-build/qemu-system-aarch64 \
  -machine virt -cpu cortex-a53 -nographic \
  -chardev socket,id=arti,path=/tmp/arti-qemu.sock \
  -kernel /tmp/arti-aarch64/guest.elf
```

The MMIO address of the `virt` device is `0x0B000000`, with a window size of `0x1000`.

### 4. Build a minimal AArch64 guest

```bash
mkdir -p /tmp/arti-aarch64
cat >/tmp/arti-aarch64/main.c <<'EOF'
#include <stdint.h>
static inline void putc_uart(char c) { *(volatile uint8_t *)0x09000000 = (uint8_t)c; }
static void puts_uart(const char *s) { while (*s) putc_uart(*s++); }
void main(void) {
    volatile uint32_t *reg = (volatile uint32_t *)0x0b000000;
    *reg = 0x123456a5u;
    if (*reg == 0x123456a5u) puts_uart("ARTI GUEST PASS\r\n");
    else puts_uart("ARTI GUEST FAIL\r\n");
    for (;;) __asm__ volatile ("wfe");
}
EOF
cat >/tmp/arti-aarch64/start.S <<'EOF'
.section .text.boot
.global _start
_start:
    ldr x0, =stack_top
    mov sp, x0
    bl main
1:  wfe
    b 1b
.section .bss
.align 12
stack: .skip 65536
stack_top:
EOF
cat >/tmp/arti-aarch64/link.ld <<'EOF'
SECTIONS { . = 0x40080000; .text : { *(.text.boot) *(.text*) } .rodata : { *(.rodata*) } .data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } }
EOF
aarch64-linux-gnu-gcc -ffreestanding -nostdlib -nostartfiles -O2 -c /tmp/arti-aarch64/start.S -o /tmp/arti-aarch64/start.o
aarch64-linux-gnu-gcc -ffreestanding -nostdlib -nostartfiles -O2 -c /tmp/arti-aarch64/main.c -o /tmp/arti-aarch64/main.o
aarch64-linux-gnu-ld -T /tmp/arti-aarch64/link.ld /tmp/arti-aarch64/start.o /tmp/arti-aarch64/main.o -o /tmp/arti-aarch64/guest.elf
```

### 5. Linux kernel driver end-to-end test

This section verifies the full chain: a real Linux kernel loads the `arti_rtl_test.ko`
driver, whose probe function writes data to the RTL device over MMIO and reads it back.

The RTL model (Verilated `simple_gpio`) is compiled directly into the QEMU device — **no
external cosim process or Unix socket is needed**:

```text
Linux guest insmod arti_rtl_test.ko
  -> driver probe: iowrite32(0x123456a5) @0x0B000000
  -> QEMU arti-rtl SysBus (embedded Verilated model)
  -> AXI-Lite handshake -> simple_gpio RTL
  -> ioread32 reads back 0x123456a5 -> ARTI LINUX PASS
```

Files involved (all under `examples/linux_arti_driver/`):

- `arti_rtl_test.c` — platform driver with write+read verification in probe
- `arti-linux-init.c` — static init program: mount + finit_module + poweroff
- `build_embedded_qemu.sh` — one-click script to generate the embedded model and rebuild
  QEMU (supports any AXI-Lite RTL)
- `run_linux_test.sh` — one-click test script

`arti_rtl_model.cpp` and `arti_rtl_model.h` are generated automatically by the arti
framework from the RTL; no hand-writing is needed.

#### 5.0 Prerequisites

This section uses the following environment variables for source paths; set them
according to your actual locations:

```bash
export ARTI_DIR=${ARTI_DIR:-$(pwd)}            # ARTI project root directory
export QEMU_SRC=${QEMU_SRC:-$(pwd)/../qemu}    # QEMU source directory
export LINUX_SRC=${LINUX_SRC:-$(pwd)/../linux}  # Linux kernel source directory
```

The following tools are required; confirm they are all ready before continuing:

```bash
# Cross-compiler
aarch64-linux-gnu-gcc --version

# Verilator (required to build the embedded model)
verilator --version

# QEMU source tree (with the arti-rtl device integrated)
test -f "$QEMU_SRC"/hw/misc/arti-rtl.c && echo "arti-rtl.c OK"

# Linux kernel source tree
test -f "$LINUX_SRC"/Makefile && echo "linux tree OK"
```

#### 5.1 Build the embedded QEMU (with the Verilated RTL model)

The arti framework automatically generates a C++ wrapper (`arti_rtl_model.cpp`) from the
RTL that drives the AXI-Lite handshake. Any AXI-Lite RTL module is supported — port
names, address width, and data width are all adapted automatically.

**5.1.1 One-click build (supports any RTL):**

```bash
cd "$ARTI_DIR"
# Uses simple_gpio by default; to switch RTL, only change the RTL and TOP variables
examples/linux_arti_driver/build_embedded_qemu.sh

# For example, using the reg_file RTL (different port names m_axi_, 4-bit address, multiple registers)
RTL=examples/reg_file/reg_file.v TOP=reg_file \
  examples/linux_arti_driver/build_embedded_qemu.sh
```

The script automatically:
1. Uses the arti CLI to parse the RTL -> infer the AXI-Lite protocol -> generate the
   port mapping -> generate the C++ wrapper
2. Uses Verilator to compile the RTL into a C++ model
3. Compiles `V*.cpp` + `verilated.cpp` + `arti_rtl_model.cpp` into `libarti_rtl_model.a`
4. Installs it into `$QEMU_SRC/hw/misc/` and rebuilds QEMU with ninja

**5.1.2 Build QEMU from scratch (first-time build):**

If `/tmp/qemu-arti-build/qemu-system-aarch64` does not exist:

```bash
mkdir -p /tmp/qemu-arti-build
cd "$QEMU_SRC"
PATH=/tmp/qemu-build-tools/bin:$PATH ./configure \
  --target-list=aarch64-softmmu --disable-werror --disable-docs \
  --disable-gtk --disable-sdl --disable-opengl --disable-virglrenderer
PATH=/tmp/qemu-build-tools/bin:$PATH ninja -C /tmp/qemu-arti-build qemu-system-aarch64
```

Verify that QEMU has the embedded device integrated:

```bash
grep "arti" "$QEMU_SRC"/hw/misc/meson.build
grep "create_arti_rtl" "$QEMU_SRC"/hw/arm/virt.c
ls -lh /tmp/qemu-arti-build/qemu-system-aarch64
```

#### 5.2 Build a minimal AArch64 kernel

```bash
cd "$LINUX_SRC"
make ARCH=arm64 defconfig CROSS_COMPILE=aarch64-linux-gnu- O=/tmp/arti-linux-build
make ARCH=arm64 Image CROSS_COMPILE=aarch64-linux-gnu- O=/tmp/arti-linux-build -j$(nproc)
```

When finished, confirm the artifacts exist:

```bash
ls -lh /tmp/arti-linux-build/arch/arm64/boot/Image
head -1 /tmp/arti-linux-build/include/config/kernel.release
```

#### 5.3 Build the driver module

```bash
cd "$ARTI_DIR"
make -C /tmp/arti-linux-build \
    M=$(pwd)/examples/linux_arti_driver \
    ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- modules
```

Confirm the `.ko` is generated and that vermagic matches the kernel:

```bash
ls -lh examples/linux_arti_driver/arti_rtl_test.ko
strings examples/linux_arti_driver/arti_rtl_test.ko | grep vermagic
# Should output: vermagic=7.2.0-rc6-... SMP preempt aarch64
# matching the contents of kernel.release
```

#### 5.4 Run the end-to-end test in one click

After all of 5.1–5.3 above are complete, run:

```bash
cd "$ARTI_DIR"
examples/linux_arti_driver/run_linux_test.sh
```

The script automatically performs the following steps:

1. Compiles `arti-linux-init.c` with `aarch64-linux-gnu-gcc` into a static AArch64 init program
2. Packages init + the `.ko` into an initramfs (cpio.gz)
3. Boots QEMU with the kernel Image + initramfs; the guest automatically runs `insmod`
   and verifies MMIO
4. Checks the output after a 30-second timeout

**No external cosim process needs to be started** — the Verilated model embedded in QEMU
handles MMIO requests directly.

On success it prints:

```
ARTI Linux init: loading module...
arti_rtl_test: loading out-of-tree module taints kernel.
arti-rtl-test arti-rtl-test: ARTI LINUX PASS: read back 0x123456a5
ARTI Linux init: finit_module returned 0x00000000
ARTI Linux init: done, powering off
reboot: Power down
=== ARTI LINUX TEST COMPLETE (PASS) ===
```

The script supports environment variables to customize paths:

```bash
QEMU=/tmp/qemu-arti-build/qemu-system-aarch64 \
KERNEL=/tmp/arti-linux-build/arch/arm64/boot/Image \
examples/linux_arti_driver/run_linux_test.sh
```

#### 5.5 Run manually (for debugging)

Start QEMU directly, without any extra processes or parameters:

```bash
/tmp/qemu-arti-build/qemu-system-aarch64 \
  -machine virt -cpu cortex-a53 -m 512M -nographic \
  -kernel /tmp/arti-linux-build/arch/arm64/boot/Image \
  -initrd /tmp/arti-initramfs.cpio.gz \
  -append "console=ttyAMA0"
```

Note: in embedded mode the `-chardev socket` parameter is no longer needed. The MMIO
address is `0x0B000000`, with a window size of `0x1000`.


### 5.6 Full Debian development environment (with network)

If you need a real, usable Linux environment (systemd, apt, gcc, etc.), you can boot a
full Debian rootfs:

```bash
./examples/linux_arti_driver/run_debian.sh
```

Features:

- Debian 12 (bookworm) ARM64, 10GB persistent qcow2 disk
- SLIRP user-mode networking, supporting `apt update` / `apt install` and DNS resolution
- SSH port forwarding: `ssh -p 2222 root@localhost` from the host (password `arti`)
- ARTI embedded device at MMIO `0x0B000000`
- To exit: run `poweroff -f` inside the VM, or press `Ctrl+A` then `X`

Network configuration is applied automatically via cloud-init: on first boot an
`arti-net.service` (systemd oneshot) is created to configure the SLIRP static IP
(`10.0.2.15/24`, gateway `10.0.2.2`, DNS `10.0.2.3`). The service is enabled and runs on
every subsequent boot, so no DHCP is needed.

The cloud-init ISO is generated automatically by `build_cloudinit.sh` (on first boot
`run_debian.sh` detects it and builds it automatically); it embeds the `.ko` module and
the network service configuration.

#### Prerequisite: the kernel must include the virtio-net driver

The default kernel configuration does not enable the network device subsystem. Enable it
manually:

```bash
cd "$LINUX_SRC"
sed -i 's/# CONFIG_NETDEVICES is not set/CONFIG_NETDEVICES=y/' .config
echo "CONFIG_VIRTIO_NET=y" >> .config
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc) Image
```

#### Prerequisite: QEMU must be built with SLIRP support

SLIRP must be built separately and linked into QEMU. Key steps:

```bash
# 1. Build the libslirp static library
cd /tmp && curl -sSL https://gitlab.freedesktop.org/slirp/libslirp/-/archive/v4.8.0/libslirp-v4.8.0.tar.gz | tar xz
cd libslirp-v4.8.0 && mkdir build && cd build
meson setup --default-library=static --prefix=/tmp/slirp-install .
ninja -C . && ninja -C . install

# 2. Tell meson where to find slirp (key point: meson overrides PKG_CONFIG_PATH, so the built-in option must be used)
cd /tmp/qemu-arti-build
meson configure . -Dpkg_config_path=/tmp/slirp-install/lib/pkgconfig -Dslirp=enabled
meson setup --reconfigure --clearcache $QEMU_SRC /tmp/qemu-arti-build

# 3. Rebuild
ninja -C /tmp/qemu-arti-build qemu-system-aarch64

# 4. Verify
/tmp/qemu-arti-build/qemu-system-aarch64 -machine virt -netdev help 2>&1 | grep user
```

> Note: meson's pkg-config probing **overrides** the `PKG_CONFIG_PATH` environment
> variable with the `pkg_config_path` property from the machine file. If there is no
> native machine file, that property is empty, which causes slirp not to be found.
> The solution is to set the built-in option directly with
> `meson configure -Dpkg_config_path=...`.

### 5.7 Supported bus protocols and interrupts

ARTI automatically detects the following 5 bus protocols and generates the matching
embedded model code:

| Protocol | Detection signals | Status |
|------|---------|------|
| AXI-Lite | AWADDR/AWVALID/WDATA/BRESP/ARADDR/RDATA etc. | Supported |
| AXI4 | Burst signals including AWLEN/AWSIZE/AWBURST/WLAST/ARLEN/RLAST etc. | Supported |
| APB | PADDR/PWDATA/PRDATA/PWRITE/PSEL/PENABLE/PREADY | Supported |
| AHB | HADDR/HWDATA/HRDATA/HWRITE/HTRANS/HREADY | Supported |
| AXI-Stream | TDATA/TVALID/TREADY (TLAST/TKEEP etc. optional) | Supported |

**Automatic protocol detection**: no need to specify the protocol in config.yaml (just
set `protocol: auto`). The framework matches the best protocol based on port names and
outputs a confidence report.

**Switching RTL only requires changing the config file**:

```bash
# Use an APB device
arti generate examples/apb_gpio/config.yaml --output /tmp/apb_project

# Use an AXI4 device
arti generate examples/axi4_periph/config.yaml --output /tmp/axi4_project

# Use an AHB device
arti generate examples/ahb_gpio/config.yaml --output /tmp/ahb_project
```

#### Automatic interrupt support

If the RTL has interrupt output ports (port names matching patterns such as `irq`,
`interrupt`, `intr`, `int`), the framework automatically:

1. **Detects interrupt ports**: via the `_detect_interrupts()` function in `inference.py`,
   automatically identifying interrupt signals among 1-bit output ports
2. **Generates the IRQ check API**: generates the `arti_rtl_model_check_irq(unsigned index)`
   function in `arti_rtl_model.h`
3. **Registers the QEMU SysBus IRQ**: calls `sysbus_init_irq()` in `arti-rtl.c` to register
   the IRQ output
4. **Polls interrupt status**: creates a `QEMUTimer` with a 100μs period, sending
   interrupts to the guest via `qemu_set_irq()`

The example RTL (`examples/irq_timer/`) demonstrates an AXI-Lite timer with an interrupt
output; the framework automatically detects the `irq` port and generates complete
interrupt support code.

```bash
# View the interrupt detection result
PYTHONPATH=src python3 -c "
from arti.parser import parse_verilog
from arti.inference import infer_protocol
sig = parse_verilog('examples/irq_timer/irq_timer.v', 'irq_timer')
print(infer_protocol(sig)['interrupts'])
"
# Output: [{'name': 'irq', 'width': 1}]
```

### 6. Regression tests and troubleshooting

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

#### General issues

- Exits immediately after `ARTI COSIM PASS`: this is the local-mode self-test
  (`mode: local`); the Linux driver test uses embedded mode and does not need the cosim.
- No `ARTI LINUX PASS`: confirm the guest accesses `0x0B000000` and that you are using the
  QEMU built by this project (with the embedded Verilated model).

#### Linux driver test issues

- **`insmod` fails / vermagic mismatch**: the `.ko` must be built with the same source
  tree and compiler as the kernel Image. Confirm the output of
  `strings arti_rtl_test.ko | grep vermagic` matches
  `cat /tmp/arti-linux-build/include/config/kernel.release`.
- **`finit_module` returns non-zero**: check the dmesg output. Common causes are a
  vermagic mismatch or module loading not enabled in the kernel (`CONFIG_MODULES=y`).
- **No output after QEMU starts**: confirm the QEMU in use is the version built by this
  project (`/tmp/qemu-arti-build/qemu-system-aarch64`) and that `libarti_rtl_model.a` has
  been installed into `$QEMU_SRC/hw/misc/`. Re-run `build_embedded_qemu.sh` to rebuild.
- **Link error `undefined reference to VerilatedContext`**: the static library does not
  include `verilated.o` and `verilated_threads.o`. Re-run `build_embedded_qemu.sh`.
