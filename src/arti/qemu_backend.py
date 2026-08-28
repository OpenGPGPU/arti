from .protocols import render_model, render_header, render_qemu_stub

def render_qemu_sysbus(config) -> dict[str, str]:
    protocol = """#pragma once
#include <stdint.h>
#define ARTI_WIRE_MAGIC 0x41525449u
#define ARTI_WIRE_READ 1u
#define ARTI_WIRE_WRITE 2u
typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t command;
    uint16_t size;
    uint64_t address;
    uint64_t data;
} ArtiWireRequest;
typedef struct __attribute__((packed)) {
    uint32_t magic;
    int32_t status;
    uint64_t data;
} ArtiWireResponse;
"""
    stub = f"""#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "qapi/error.h"
#include "qemu/module.h"
#include "qemu/sockets.h"
#include "chardev/char-fe.h"
#include "hw/core/qdev-properties-system.h"
#include "arti_wire.h"

#define TYPE_ARTI_RTL "arti-rtl"
OBJECT_DECLARE_SIMPLE_TYPE(ArtiRtlState, ARTI_RTL)
struct ArtiRtlState {{
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    CharFrontend chr;
}};
static int arti_xfer(ArtiRtlState *s, uint16_t command, hwaddr address,
                     uint64_t *data, unsigned size)
{{
    ArtiWireRequest req = {{ ARTI_WIRE_MAGIC, command, size, address, *data }};
    ArtiWireResponse rsp;
    if (qemu_chr_fe_write_all(&s->chr, (uint8_t *)&req, sizeof(req)) != sizeof(req) ||
        qemu_chr_fe_read_all(&s->chr, (uint8_t *)&rsp, sizeof(rsp)) != sizeof(rsp) ||
        rsp.magic != ARTI_WIRE_MAGIC || rsp.status != 0)
        return -1;
    *data = rsp.data;
    return 0;
}}
static uint64_t arti_read(void *opaque, hwaddr offset, unsigned size)
{{
    uint64_t data = 0;
    arti_xfer(opaque, ARTI_WIRE_READ, offset, &data, size);
    return data;
}}
static void arti_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{{
    arti_xfer(opaque, ARTI_WIRE_WRITE, offset, &value, size);
}}
static const MemoryRegionOps arti_ops = {{
    .read = arti_read, .write = arti_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {{ .min_access_size = 1, .max_access_size = 8 }},
}};
static void arti_realize(DeviceState *dev, Error **errp)
{{
    ArtiRtlState *s = ARTI_RTL(dev);
    if (!qemu_chr_fe_backend_connected(&s->chr)) {{
        error_setg(errp, "arti-rtl requires a connected chardev");
        return;
    }}
    memory_region_init_io(&s->mmio, OBJECT(s), &arti_ops, s, TYPE_ARTI_RTL,
                          0x{config.mmio_size:x});
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mmio);
}}
static const Property arti_properties[] = {{
    DEFINE_PROP_CHR("chardev", ArtiRtlState, chr),
}};
static void arti_class_init(ObjectClass *klass, const void *data)
{{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = arti_realize;
    device_class_set_props_n(dc, arti_properties, ARRAY_SIZE(arti_properties));
}}
static const TypeInfo arti_info = {{
    .name = TYPE_ARTI_RTL, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ArtiRtlState), .class_init = arti_class_init,
}};
static void arti_register_types(void) {{ type_register_static(&arti_info); }}
type_init(arti_register_types)
"""
    args = (
        f"-machine {config.qemu_machine}\n"
        f"-chardev socket,id=arti,path={config.socket_path}\n"
        "-nographic\n"
    )
    integration = (
        "Copy arti-rtl.c and arti_wire.h into upstream QEMU hw/misc.\n"
        "Add arti-rtl.c to hw/misc/meson.build and CONFIG_ARTI_RTL to Kconfig.\n"
        "The virt machine must instantiate arti-rtl and map it with sysbus_mmio_map.\nThis is a SysBus MMIO device and has no PCI or VFIO dependency.\n"
    )
    return {
        "qemu/arti_wire.h": protocol,
        "qemu/arti-rtl.c": stub,
        "qemu/qemu_args.txt": args,
        "qemu/INTEGRATION.txt": integration,
    }

def render_socket_adapter(config) -> dict[str, str]:
    adapter = r"""#pragma once
#include <cerrno>
#include <cstring>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#ifdef __APPLE__
#include <fcntl.h>
#endif
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_initiator_socket.h>
#include "qemu/arti_wire.h"

#ifdef __APPLE__
static void arti_set_nonblock(int fd)
{
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) {
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    }
}

static int arti_accept_nonblock(int fd)
{
    int client = accept(fd, nullptr, nullptr);
    if (client >= 0) {
        arti_set_nonblock(client);
    }
    return client;
}
#endif

SC_MODULE(QemuSocketAdapter) {
    tlm_utils::simple_initiator_socket<QemuSocketAdapter> initiator{"initiator"};
    SC_HAS_PROCESS(QemuSocketAdapter);
    QemuSocketAdapter(sc_core::sc_module_name name, const char* path)
        : sc_core::sc_module(name), path_(path) { SC_THREAD(run); }
    ~QemuSocketAdapter() override {
        if (client_ >= 0) close(client_);
        if (server_ >= 0) close(server_);
        unlink(path_);
    }
private:
    const char* path_;
    int server_{-1};
    int client_{-1};
    ArtiWireRequest request_{};
    size_t received_{0};
    bool receive_request() {
        auto* pos = reinterpret_cast<unsigned char*>(&request_);
        ssize_t count = recv(client_, pos + received_, sizeof(request_) - received_, MSG_DONTWAIT);
        if (count > 0) {
            received_ += static_cast<size_t>(count);
            return received_ == sizeof(request_);
        }
        if (count == 0) { close(client_); client_ = -1; received_ = 0; }
        return false;
    }
    static bool send_all(int fd, const void* data, size_t size) {
        auto* pos = static_cast<const unsigned char*>(data);
        size_t done = 0;
        while (done < size) {
            ssize_t count = send(fd, pos + done, size - done, MSG_NOSIGNAL);
            if (count <= 0) return false;
            done += static_cast<size_t>(count);
        }
        return true;
    }
    void transact(const ArtiWireRequest& request, ArtiWireResponse& response) {
        response = {ARTI_WIRE_MAGIC, -1, 0};
        if (request.magic != ARTI_WIRE_MAGIC || request.size == 0 || request.size > 8 ||
            (request.command != ARTI_WIRE_READ && request.command != ARTI_WIRE_WRITE)) return;
        uint64_t data = request.data;
        tlm::tlm_generic_payload payload;
        sc_core::sc_time delay = sc_core::SC_ZERO_TIME;
        payload.set_command(request.command == ARTI_WIRE_WRITE ?
                            tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
        payload.set_address(request.address);
        payload.set_data_ptr(reinterpret_cast<unsigned char*>(&data));
        payload.set_data_length(request.size);
        payload.set_streaming_width(request.size);
        initiator->b_transport(payload, delay);
        if (delay != sc_core::SC_ZERO_TIME) wait(delay);
        response.status = payload.is_response_ok() ? 0 : -1;
        response.data = data;
    }
    void run() {
#ifdef __APPLE__
        server_ = socket(AF_UNIX, SOCK_STREAM, 0);
        if (server_ >= 0) {
            arti_set_nonblock(server_);
        }
#else
        server_ = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK, 0);
#endif
        if (server_ < 0) SC_REPORT_FATAL("QemuSocketAdapter", "socket failed");
        sockaddr_un address{}; address.sun_family = AF_UNIX;
        if (std::strlen(path_) >= sizeof(address.sun_path))
            SC_REPORT_FATAL("QemuSocketAdapter", "socket path too long");
        std::strcpy(address.sun_path, path_); unlink(path_);
        if (bind(server_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0 ||
            listen(server_, 1) < 0)
            SC_REPORT_FATAL("QemuSocketAdapter",
                            (std::string("bind/listen failed: ") +
                             std::strerror(errno)).c_str());
        while (true) {
#ifdef __APPLE__
            if (client_ < 0) client_ = arti_accept_nonblock(server_);
#else
            if (client_ < 0) client_ = accept4(server_, nullptr, nullptr, SOCK_NONBLOCK);
#endif
            if (client_ >= 0) {

                if (receive_request()) {
                    ArtiWireResponse response; transact(request_, response);
                    received_ = 0;
                    if (!send_all(client_, &response, sizeof(response))) {
                        close(client_); client_ = -1;
                    }
                }
            }
            wait(1, sc_core::SC_US);
        }
    }
};
"""
    main = f"""#include <systemc>
#include "bridge/bridge_top.h"
#include "bridge/qemu_socket_adapter.h"
int sc_main(int argc, char** argv) {{
    const char* path = argc > 1 ? argv[1] : "{config.socket_path}";
    BridgeTop top{{"top"}};
    QemuSocketAdapter adapter{{"adapter", path}};
    adapter.initiator.bind(top.target_socket);
    sc_core::sc_start();
    return 0;
}}
"""
    runner = f"""#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "$0")/.." && pwd)"
cmake -S "$root_dir" -B "$root_dir/build/cmake"
cmake --build "$root_dir/build/cmake" --parallel
rm -f "{config.socket_path}"
exec "$root_dir/build/cmake/cosim" "{config.socket_path}"
"""
    return {
        "bridge/qemu_socket_adapter.h": adapter,
        "sc_main.cpp": main,
        "build/run_cosim.sh": runner,
    }


def render_embedded_model(config, signature, mapping, port_by_name, protocol, interrupts=None) -> dict[str, str]:
    """Generate the embedded Verilated model wrapper + QEMU device stub.

    Unlike the socket mode, the RTL model is compiled directly into QEMU,
    so MMIO accesses are in-process C++ function calls with no IPC.

    Supports multiple bus protocols (axi-lite, axi4, apb, ahb, axi-stream)
    and automatic interrupt wiring when interrupt output ports are detected.
    """
    mod = signature.module_name
    interrupts = interrupts or []

    wrapper = render_model(protocol, config, signature, mapping, port_by_name, interrupts)
    header = render_header(len(interrupts))
    qemu_stub = render_qemu_stub(config.mmio_size, interrupts, config)

    build_script = f"""#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERILATOR_INC=${{VERILATOR_INC:-/usr/share/verilator/include}}
QEMU_SRC=${{QEMU_SRC:?must be set}}
QEMU_BUILD=${{QEMU_BUILD:-/tmp/qemu-arti-build}}
TOP_MODULE={mod}

echo "=== Building embedded RTL model for {mod} ({protocol}) ==="
mkdir -p "$SCRIPT_DIR/verilated"

# 1. Generate Verilated C++ sources
shopt -s nullglob
RTL_SOURCES=("$SCRIPT_DIR"/../rtl/*.v "$SCRIPT_DIR"/../rtl/*.sv)
if (( ${{#RTL_SOURCES[@]}} == 0 )); then
    echo "no Verilog/SystemVerilog sources found under $SCRIPT_DIR/../rtl" >&2
    exit 1
fi
verilator --cc --Mdir "$SCRIPT_DIR/verilated" \
  --CFLAGS "-Wno-undefined-bool-conversion" \
  "${{RTL_SOURCES[@]}}" --top-module "$TOP_MODULE"

# 2. Compile all sources into a static library
cd "$SCRIPT_DIR/verilated"
for f in V{mod}*.cpp; do
    g++ -std=gnu++17 -fPIC -fPIE -O2 -w -I. -I"$VERILATOR_INC" -c "$f" -o "${{f%.cpp}}.o"
done
g++ -std=gnu++17 -fPIC -fPIE -O2 -w -I"$VERILATOR_INC" -c "$VERILATOR_INC/verilated.cpp" -o verilated.o
g++ -std=gnu++17 -fPIC -fPIE -O2 -w -I"$VERILATOR_INC" -c "$VERILATOR_INC/verilated_threads.cpp" -o verilated_threads.o
g++ -std=gnu++17 -fPIC -fPIE -O2 -w -I. -I"$VERILATOR_INC" -c "$SCRIPT_DIR/arti_rtl_model.cpp" -o arti_rtl_model.o
ar rcs libarti_rtl_model.a *.o
ls -lh libarti_rtl_model.a

# 3. Install into QEMU source tree
cp libarti_rtl_model.a "$QEMU_SRC/hw/misc/"
cp "$SCRIPT_DIR/arti_rtl_model.h" "$QEMU_SRC/hw/misc/"

# 4. Rebuild QEMU
echo "=== Rebuilding QEMU ==="
if [ "${{SKIP_QEMU_REBUILD:-}}" != "1" ]; then
    if [ ! -f "$QEMU_BUILD/build.ninja" ]; then
        echo "QEMU build directory not configured yet; run setup_env.sh first"
        exit 1
    fi
    PATH=/tmp/qemu-build-tools/bin:$PATH ninja -C "$QEMU_BUILD" qemu-system-aarch64
    echo "=== Done ==="
    ls -lh "$QEMU_BUILD/qemu-system-aarch64"
else
    echo "SKIP_QEMU_REBUILD=1, leaving QEMU build to setup_env.sh"
fi
"""

    irq_info = f" Interrupts: {len(interrupts)} ({', '.join(i['name'] for i in interrupts)})" if interrupts else ""
    integration = (
        f"Embedded mode: RTL model ({mod}) is compiled directly into QEMU.\n"
        f"Protocol: {protocol}.{irq_info}\n"
        "No external cosim process or Unix socket is needed.\n"
        "Run build_embedded.sh to compile the Verilated model and rebuild QEMU.\n"
        "The arti-rtl.c device stub calls arti_rtl_model_init/write/read directly.\n"
    )

    return {
        "embedded/arti_rtl_model.h": header,
        "embedded/arti_rtl_model.cpp": wrapper,
        "qemu/arti-rtl.c": qemu_stub,
        "embedded/build_embedded.sh": build_script,
        "qemu/INTEGRATION.txt": integration,
    }
