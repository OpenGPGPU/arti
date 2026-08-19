# ARTI

ARTI is the initial implementation of the automatic RTL-to-QEMU integration
framework described in `自动化通用RTL接入框架设计报告.md`. The current M1 slice
parses ANSI-style Verilog top-level ports, ranks five standard bus protocols, and
generates a SystemC/Verilator project for an AXI-Lite target. With an RTL design
as the input, users can quickly run full-system simulation or functional trials:
the generated RTL model can be integrated into QEMU as an `arti-rtl` SysBus
device; in embedded mode, Verilated RTL is compiled directly into QEMU so a
Linux guest can exercise the RTL through MMIO without an external cosim process
or Unix socket.

## Quick start (one-click)

一键安装全部依赖并运行示例（适合新环境）：

```bash
# 安装环境 + 运行端到端测试
./examples/linux_arti_driver/run.sh

# 安装环境 + 交互式 busybox shell
./examples/linux_arti_driver/run.sh interactive

# 安装环境 + 完整 Debian 开发环境（含网络）
./examples/linux_arti_driver/run.sh debian
```

`run.sh` 会自动调用 `setup_env.sh`，该脚本完成以下步骤：

1. 安装 ninja 构建工具
2. 下载并编译 QEMU（含 SLIRP 网络支持 + arti-rtl 设备）
3. 下载并编译 Linux 内核（AArch64，含 virtio-net + 模块加载 + ext4）
4. 编译 Busybox（静态二进制，用于 initramfs）
5. 下载 Debian 12 cloud rootfs + 生成 cloud-init ISO
6. 编译驱动模块 `.ko`
7. 生成嵌入式 RTL 模型并编译进 QEMU

也可以单独运行 `setup_env.sh` 只做安装：

```bash
./examples/linux_arti_driver/setup_env.sh
```

安装完成后，所有产物在 `/tmp` 下，运行脚本会自动检测已安装的组件（重复运行只补缺失项）。

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

For the full Linux kernel driver end-to-end test (guest `insmod` -> MMIO -> QEMU -> RTL loopback), see [section 5](#5-linux-内核驱动端到端测试). Quick run after prerequisites are built:

```bash
examples/linux_arti_driver/run_linux_test.sh
```

## Current scope

- Verilog-2001 ANSI port declarations and integer parameters
- Naming-based AXI-Lite, AXI4, AXI-Stream, AHB, and APB protocol detection
- Confidence, missing-signal, unknown-port, signal-mapping, and interrupt reports
- **Full multi-protocol embedded model generation** for AXI-Lite, AXI4, APB, AHB, and AXI-Stream
- **Automatic interrupt support**: detects IRQ output ports, generates `arti_rtl_model_check_irq()` API, and wires QEMU SysBus IRQ with polling timer
- SystemC/Verilator AXI-Lite runtime bridge with generated local TLM self-test (local mode)
- Upstream QEMU SysBus MMIO stub and Unix-socket-to-TLM transport generation
- Full QEMU embedded mode: Verilated RTL compiled directly into QEMU, no IPC needed

A successful generated simulation prints `ARTI COSIM PASS`.


## 完整使用教程

### 环境准备

Python 仅使用标准库。普通仿真需要 Verilator、SystemC、CMake 和 C++ 编译器；QEMU 联调还需要官方 AArch64 GNU 交叉编译器：

```bash
verilator --version
pkg-config --modversion systemc
cmake --version
aarch64-linux-gnu-gcc --version
```

原生 QEMU 源码目录由环境变量 `$QEMU_SRC` 指定，已编译的 QEMU 位于 `/tmp/qemu-arti-build/qemu-system-aarch64`。设置变量后重新构建：

```bash
export QEMU_SRC=${QEMU_SRC:-$(pwd)/../qemu}
mkdir -p /tmp/qemu-arti-build
PATH=/tmp/qemu-build-tools/bin:$PATH $QEMU_SRC/configure \
  --target-list=aarch64-softmmu --disable-werror --disable-docs \
  --disable-gtk --disable-sdl --disable-opengl --disable-virglrenderer
PATH=/tmp/qemu-build-tools/bin:$PATH ninja -C /tmp/qemu-arti-build qemu-system-aarch64
```

### 1. 检查 RTL

```bash
PYTHONPATH=src python3 -m arti.cli inspect examples/simple_gpio/simple_gpio.v --top simple_gpio
```

输出 JSON，包含端口签名、协议推断、信号映射、缺失信号和未知端口。

### 2. 本地 SystemC/Verilator 仿真

```bash
PYTHONPATH=src python3 -m arti.cli generate \
  examples/simple_gpio/config.yaml --output /tmp/simple_gpio_cosim
/tmp/simple_gpio_cosim/build/run_cosim.sh
```

成功时输出 `ARTI COSIM PASS`。生成目录中的 `bridge/bridge_top.h` 是 AXI-Lite TLM 到 RTL 的桥，`reports/inference_report.json` 是推断报告。

### 3. 原生 QEMU SysBus 联调（socket 模式）

> **提示**：如需在 Linux 内核中测试驱动，推荐使用 [第 5 节](#5-linux-内核驱动端到端测试) 的嵌入式模式（RTL 直接编译进 QEMU），无需 cosim 进程或 socket。本节描述的是 socket 模式，适用于需要 SystemC 精确周期仿真的场景。

ARTI 不依赖 PCI、VFIO、Xilinx QEMU 或 remote-port，数据路径为：

```text
AArch64 guest MMIO -> QEMU arti-rtl SysBus -> Unix socket
-> SystemC/TLM -> AXI-Lite -> Verilated RTL
```

先创建 QEMU 模式配置并编译 cosim：

```bash
cp examples/simple_gpio/config.yaml /tmp/simple_gpio_qemu.yaml
sed -i '/bridge:/a\  mode: qemu-sysbus' /tmp/simple_gpio_qemu.yaml
PYTHONPATH=src python3 -m arti.cli generate /tmp/simple_gpio_qemu.yaml --output /tmp/simple_gpio_qemu
cmake -S /tmp/simple_gpio_qemu -B /tmp/simple_gpio_qemu/build/cmake
cmake --build /tmp/simple_gpio_qemu/build/cmake --parallel
```

终端 A 启动 SystemC socket 服务端：

```bash
rm -f /tmp/arti-qemu.sock
/tmp/simple_gpio_qemu/build/cmake/cosim /tmp/arti-qemu.sock
```

终端 B 启动原生 QEMU 客户端：

```bash
/tmp/qemu-arti-build/qemu-system-aarch64 \
  -machine virt -cpu cortex-a53 -nographic \
  -chardev socket,id=arti,path=/tmp/arti-qemu.sock \
  -kernel /tmp/arti-aarch64/guest.elf
```

`virt` 设备的 MMIO 地址是 `0x0B000000`，窗口大小为 `0x1000`。

### 4. 构建最小 AArch64 guest

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

### 5. Linux 内核驱动端到端测试

本节验证完整链路：真实 Linux 内核加载 `arti_rtl_test.ko` 驱动，驱动 probe 函数通过 MMIO 向 RTL 设备写入并读回数据。

RTL 模型（Verilated `simple_gpio`）直接编译进 QEMU 设备，**无需外部 cosim 进程或 Unix socket**：

```text
Linux guest insmod arti_rtl_test.ko
  -> 驱动 probe: iowrite32(0x123456a5) @0x0B000000
  -> QEMU arti-rtl SysBus（嵌入式 Verilated 模型）
  -> AXI-Lite 握手 -> simple_gpio RTL
  -> ioread32 读回 0x123456a5 -> ARTI LINUX PASS
```

涉及文件（均在 `examples/linux_arti_driver/`）：

- `arti_rtl_test.c` — 平台驱动，probe 中 write+read 验证
- `arti-linux-init.c` — 静态 init 程序，mount + finit_module + poweroff
- `build_embedded_qemu.sh` — 一键生成嵌入式模型并重编 QEMU（支持任意 AXI-Lite RTL）
- `run_linux_test.sh` — 一键测试脚本

`arti_rtl_model.cpp` 和 `arti_rtl_model.h` 由 arti 框架根据 RTL 自动生成，无需手写。

#### 5.0 前置条件

本节使用以下环境变量指定源码路径，请根据实际位置设置：

```bash
export ARTI_DIR=${ARTI_DIR:-$(pwd)}            # ARTI 项目根目录
export QEMU_SRC=${QEMU_SRC:-$(pwd)/../qemu}    # QEMU 源码目录
export LINUX_SRC=${LINUX_SRC:-$(pwd)/../linux}  # Linux 内核源码目录
```

需要以下工具，确认全部就绪后再继续：

```bash
# 交叉编译器
aarch64-linux-gnu-gcc --version

# Verilator（嵌入式模型构建依赖）
verilator --version

# QEMU 源码树（已集成 arti-rtl 设备）
test -f "$QEMU_SRC"/hw/misc/arti-rtl.c && echo "arti-rtl.c OK"

# Linux 内核源码树
test -f "$LINUX_SRC"/Makefile && echo "linux tree OK"
```

#### 5.1 编译嵌入式 QEMU（含 Verilated RTL 模型）

arti 框架根据 RTL 自动生成 C++ 包装器（`arti_rtl_model.cpp`），驱动 AXI-Lite 握手。
支持任意 AXI-Lite RTL 模块——端口名、地址宽度、数据宽度均自动适配。

**5.1.1 一键构建（支持任意 RTL）：**

```bash
cd "$ARTI_DIR"
# 默认使用 simple_gpio；更换 RTL 只需改 RTL 和 TOP 变量
examples/linux_arti_driver/build_embedded_qemu.sh

# 例如使用 reg_file RTL（不同的端口名 m_axi_、4 位地址、多寄存器）
RTL=examples/reg_file/reg_file.v TOP=reg_file \
  examples/linux_arti_driver/build_embedded_qemu.sh
```

脚本自动完成：
1. arti CLI 解析 RTL → 推断 AXI-Lite 协议 → 生成端口映射 → 生成 C++ 包装器
2. Verilator 编译 RTL 为 C++ 模型
3. 编译 `V*.cpp` + `verilated.cpp` + `arti_rtl_model.cpp` 为 `libarti_rtl_model.a`
4. 安装到 `$QEMU_SRC/hw/misc/` 并用 ninja 重编 QEMU

**5.1.2 从零编译 QEMU（首次构建）：**

如果 `/tmp/qemu-arti-build/qemu-system-aarch64` 不存在：

```bash
mkdir -p /tmp/qemu-arti-build
cd "$QEMU_SRC"
PATH=/tmp/qemu-build-tools/bin:$PATH ./configure \
  --target-list=aarch64-softmmu --disable-werror --disable-docs \
  --disable-gtk --disable-sdl --disable-opengl --disable-virglrenderer
PATH=/tmp/qemu-build-tools/bin:$PATH ninja -C /tmp/qemu-arti-build qemu-system-aarch64
```

验证 QEMU 已集成嵌入式设备：

```bash
grep "arti" "$QEMU_SRC"/hw/misc/meson.build
grep "create_arti_rtl" "$QEMU_SRC"/hw/arm/virt.c
ls -lh /tmp/qemu-arti-build/qemu-system-aarch64
```

#### 5.2 编译最小 AArch64 内核

```bash
cd "$LINUX_SRC"
make ARCH=arm64 defconfig CROSS_COMPILE=aarch64-linux-gnu- O=/tmp/arti-linux-build
make ARCH=arm64 Image CROSS_COMPILE=aarch64-linux-gnu- O=/tmp/arti-linux-build -j$(nproc)
```

完成后确认产物存在：

```bash
ls -lh /tmp/arti-linux-build/arch/arm64/boot/Image
head -1 /tmp/arti-linux-build/include/config/kernel.release
```

#### 5.3 编译驱动模块

```bash
cd "$ARTI_DIR"
make -C /tmp/arti-linux-build \
    M=$(pwd)/examples/linux_arti_driver \
    ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- modules
```

确认 `.ko` 生成且 vermagic 与内核一致：

```bash
ls -lh examples/linux_arti_driver/arti_rtl_test.ko
strings examples/linux_arti_driver/arti_rtl_test.ko | grep vermagic
# 应输出: vermagic=7.2.0-rc6-... SMP preempt aarch64
# 与 kernel.release 内容对应
```

#### 5.4 一键运行端到端测试

以上 5.1–5.3 全部完成后，执行：

```bash
cd "$ARTI_DIR"
examples/linux_arti_driver/run_linux_test.sh
```

脚本自动完成以下步骤：

1. 用 `aarch64-linux-gnu-gcc` 编译 `arti-linux-init.c` 为静态 ARM64 init
2. 打包 init + `.ko` 为 initramfs（cpio.gz）
3. 启动 QEMU 加载内核 Image + initramfs，guest 自动 `insmod` 并验证 MMIO
4. 30 秒超时后检查输出

**无需启动外部 cosim 进程**，QEMU 内嵌的 Verilated 模型直接处理 MMIO 请求。

成功时输出：

```
ARTI Linux init: loading module...
arti_rtl_test: loading out-of-tree module taints kernel.
arti-rtl-test arti-rtl-test: ARTI LINUX PASS: read back 0x123456a5
ARTI Linux init: finit_module returned 0x00000000
ARTI Linux init: done, powering off
reboot: Power down
=== ARTI LINUX TEST COMPLETE (PASS) ===
```

脚本支持环境变量自定义路径：

```bash
QEMU=/tmp/qemu-arti-build/qemu-system-aarch64 \
KERNEL=/tmp/arti-linux-build/arch/arm64/boot/Image \
examples/linux_arti_driver/run_linux_test.sh
```

#### 5.5 手动运行（调试用）

直接启动 QEMU，无需任何额外进程或参数：

```bash
/tmp/qemu-arti-build/qemu-system-aarch64 \
  -machine virt -cpu cortex-a53 -m 512M -nographic \
  -kernel /tmp/arti-linux-build/arch/arm64/boot/Image \
  -initrd /tmp/arti-initramfs.cpio.gz \
  -append "console=ttyAMA0"
```

注意：嵌入式模式下不再需要 `-chardev socket` 参数。MMIO 地址为 `0x0B000000`，窗口大小 `0x1000`。


### 5.6 完整 Debian 开发环境（含网络）

如果需要真实可用的 Linux 环境（systemd、apt、gcc 等），可以启动完整的 Debian rootfs：

```bash
./examples/linux_arti_driver/run_debian.sh
```

功能：

- Debian 12 (bookworm) ARM64，10GB 持久化 qcow2 磁盘
- SLIRP 用户态网络，支持 `apt update` / `apt install`、DNS 解析
- SSH 端口转发：宿主机 `ssh -p 2222 root@localhost`（密码 `arti`）
- ARTI 嵌入式设备在 MMIO `0x0B000000`
- 退出：在 VM 内执行 `poweroff -f`，或按 `Ctrl+A` 然后按 `X`

网络配置通过 cloud-init 自动完成：首次启动时创建 `arti-net.service`（systemd oneshot），配置 SLIRP 静态 IP（`10.0.2.15/24`，网关 `10.0.2.2`，DNS `10.0.2.3`）。之后每次启动自动生效，无需 DHCP。

cloud-init ISO 由 `build_cloudinit.sh` 自动生成（首次启动时 `run_debian.sh` 会检测并自动构建），内嵌 `.ko` 模块和网络服务配置。

#### 前置条件：内核需包含 virtio-net 驱动

内核默认配置未启用网络设备子系统。需要手动启用：

```bash
cd "$LINUX_SRC"
sed -i 's/# CONFIG_NETDEVICES is not set/CONFIG_NETDEVICES=y/' .config
echo "CONFIG_VIRTIO_NET=y" >> .config
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc) Image
```

#### 前置条件：QEMU 需编译 SLIRP 支持

SLIRP 需要单独编译后链接进 QEMU。关键步骤：

```bash
# 1. 构建 libslirp 静态库
cd /tmp && curl -sSL https://gitlab.freedesktop.org/slirp/libslirp/-/archive/v4.8.0/libslirp-v4.8.0.tar.gz | tar xz
cd libslirp-v4.8.0 && mkdir build && cd build
meson setup --default-library=static --prefix=/tmp/slirp-install .
ninja -C . && ninja -C . install

# 2. 告诉 meson 在哪里找 slirp（关键：meson 会覆盖 PKG_CONFIG_PATH，必须用内置选项）
cd /tmp/qemu-arti-build
meson configure . -Dpkg_config_path=/tmp/slirp-install/lib/pkgconfig -Dslirp=enabled
meson setup --reconfigure --clearcache $QEMU_SRC /tmp/qemu-arti-build

# 3. 重新编译
ninja -C /tmp/qemu-arti-build qemu-system-aarch64

# 4. 验证
/tmp/qemu-arti-build/qemu-system-aarch64 -machine virt -netdev help 2>&1 | grep user
```

> 注意：meson 的 pkg-config 探测会用机器文件中的 `pkg_config_path` 属性**覆盖**环境变量 `PKG_CONFIG_PATH`。
> 如果没有 native 机器文件，该属性为空，导致 slirp 找不到。
> 解决方法是用 `meson configure -Dpkg_config_path=...` 直接设置内置选项。

### 5.7 支持的总线协议和中断

ARTI 自动检测以下 5 种总线协议并生成对应的嵌入式模型代码：

| 协议 | 检测信号 | 状态 |
|------|---------|------|
| AXI-Lite | AWADDR/AWVALID/WDATA/BRESP/ARADDR/RDATA 等 | 完整支持 |
| AXI4 | 含 AWLEN/AWSIZE/AWBURST/WLAST/ARLEN/RLAST 等突发信号 | 完整支持 |
| APB | PADDR/PWDATA/PRDATA/PWRITE/PSEL/PENABLE/PREADY | 完整支持 |
| AHB | HADDR/HWDATA/HRDATA/HWRITE/HTRANS/HREADY | 完整支持 |
| AXI-Stream | TDATA/TVALID/TREADY (TLAST/TKEEP 等可选) | 完整支持 |

**协议自动检测**：无需在 config.yaml 中指定协议（设 `protocol: auto` 即可）。框架根据端口名匹配最优协议，并输出置信度报告。

**切换 RTL 只需改配置文件**：

```bash
# 使用 APB 设备
arti generate examples/apb_gpio/config.yaml --output /tmp/apb_project

# 使用 AXI4 设备
arti generate examples/axi4_periph/config.yaml --output /tmp/axi4_project

# 使用 AHB 设备
arti generate examples/ahb_gpio/config.yaml --output /tmp/ahb_project
```

#### 中断自动支持

如果 RTL 有中断输出端口（端口名包含 `irq`、`interrupt`、`intr`、`int` 等模式），框架会自动：

1. **检测中断端口**：通过 `inference.py` 中的 `_detect_interrupts()` 函数，自动识别 1-bit 输出端口中的中断信号
2. **生成 IRQ 检查 API**：在 `arti_rtl_model.h` 中生成 `arti_rtl_model_check_irq(unsigned index)` 函数
3. **注册 QEMU SysBus IRQ**：在 `arti-rtl.c` 中调用 `sysbus_init_irq()` 注册 IRQ 输出
4. **轮询中断状态**：创建 100μs 周期的 `QEMUTimer`，通过 `qemu_set_irq()` 向 guest 发送中断

示例 RTL（`examples/irq_timer/`）演示了一个带中断输出的 AXI-Lite 定时器，框架自动检测到 `irq` 端口并生成完整的中断支持代码。

```bash
# 查看中断检测结果
PYTHONPATH=src python3 -c "
from arti.parser import parse_verilog
from arti.inference import infer_protocol
sig = parse_verilog('examples/irq_timer/irq_timer.v', 'irq_timer')
print(infer_protocol(sig)['interrupts'])
"
# 输出: [{'name': 'irq', 'width': 1}]
```

### 6. 回归测试和排错

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

#### 通用问题

- `ARTI COSIM PASS` 后立即退出：这是 local 模式自测（`mode: local`）；Linux 驱动测试使用嵌入式模式，不需要 cosim。
- 没有 `ARTI LINUX PASS`：确认 guest 访问 `0x0B000000`，并使用本项目编译的 QEMU（含嵌入式 Verilated 模型）。

#### Linux 驱动测试问题

- **`insmod` 失败 / vermagic 不匹配**：`.ko` 必须用与内核 Image 相同的源码树和编译器构建。确认 `strings arti_rtl_test.ko | grep vermagic` 输出与 `cat /tmp/arti-linux-build/include/config/kernel.release` 一致。
- **`finit_module` 返回非零**：检查 dmesg 输出。常见原因是 vermagic 不匹配或内核未启用模块加载（`CONFIG_MODULES=y`）。
- **QEMU 启动后无输出**：确认使用的 QEMU 是本项目编译的版本（`/tmp/qemu-arti-build/qemu-system-aarch64`），且 `libarti_rtl_model.a` 已安装到 `$QEMU_SRC/hw/misc/`。重新运行 `build_embedded_qemu.sh` 重建。
- **链接错误 `undefined reference to VerilatedContext`**：静态库未包含 `verilated.o` 和 `verilated_threads.o`。重新运行 `build_embedded_qemu.sh`。
