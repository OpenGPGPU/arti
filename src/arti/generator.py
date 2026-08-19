import json
import shutil
from pathlib import Path

from .config import Config
from .model import ModuleSignature
from .qemu_backend import render_qemu_sysbus, render_socket_adapter, render_embedded_model


def _sc_type(width: int) -> str:
    if width == 1:
        return "bool"
    if width <= 32:
        return "uint32_t"
    if width <= 64:
        return "uint64_t"
    return f"sc_dt::sc_bv<{width}>"




def _render_axi_lite_bridge(config: Config, signature: ModuleSignature, mapping: dict, port_by_name: dict) -> str:
    signals = {key: value for key, value in mapping.items() if key not in ("ACLK", "ARESETN")}
    mapped_ports = set(mapping.values())
    extra_ports = [port for port in signature.ports if port.name not in mapped_ports]
    extra_declarations = [
        f'    sc_core::sc_signal<{_sc_type(port.width)}> sig_extra_{port.name}{{"sig_extra_{port.name}"}};'
        for port in extra_ports
    ]
    declarations = [
        f'    sc_core::sc_signal<{_sc_type(port_by_name[actual].width)}> sig_{logical.lower()}{{"sig_{logical.lower()}"}};'
        for logical, actual in signals.items()
    ]
    bindings = [f"        rtl.{mapping['ACLK']}(clk);", f"        rtl.{mapping['ARESETN']}(rst_n);"]
    bindings.extend(f"        rtl.{actual}(sig_{logical.lower()});" for logical, actual in signals.items())
    bindings.extend(f"        rtl.{port.name}(sig_extra_{port.name});" for port in extra_ports)
    wstrb = "sig_wstrb.write(strobe);" if "WSTRB" in mapping else "(void)strobe;"
    return f"""#pragma once
#include <algorithm>
#include <cstdint>
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_target_socket.h>
#include "V{signature.module_name}.h"

SC_MODULE(BridgeTop) {{
    static constexpr uint64_t BASE_ADDRESS = 0x{config.base_address:x}ULL;
    static constexpr unsigned BUS_BYTES = {config.data_width // 8};
    static constexpr unsigned TIMEOUT_CYCLES = {config.timeout_cycles};
    tlm_utils::simple_target_socket<BridgeTop> target_socket{{"target_socket"}};
    sc_core::sc_clock clk{{"clk", {1000.0 / config.clk_freq_mhz:.6f}, sc_core::SC_NS}};
    sc_core::sc_signal<bool> rst_n{{"rst_n"}};
    V{signature.module_name} rtl{{"rtl"}};
{chr(10).join(declarations + extra_declarations)}

    SC_CTOR(BridgeTop) {{
        target_socket.register_b_transport(this, &BridgeTop::b_transport);
        drive_idle();
{chr(10).join(bindings)}
        SC_THREAD(reset_thread);
    }}

    void reset_thread() {{
        rst_n.write(false);
        for (unsigned i = 0; i < 4; ++i) wait(clk.posedge_event());
        rst_n.write(true);
    }}

    void b_transport(tlm::tlm_generic_payload& trans, sc_core::sc_time& delay) {{
        if (!trans.get_data_ptr() || !trans.get_data_length() ||
            trans.get_streaming_width() < trans.get_data_length()) {{
            trans.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return;
        }}
        uint64_t address = trans.get_address();
        if (address >= BASE_ADDRESS) address -= BASE_ADDRESS;
        unsigned completed = 0;
        while (completed < trans.get_data_length()) {{
            const unsigned lane = static_cast<unsigned>(address % BUS_BYTES);
            const unsigned length = std::min(BUS_BYTES - lane, trans.get_data_length() - completed);
            const bool ok = trans.is_write()
                ? write_beat(address - lane, trans.get_data_ptr() + completed,
                             trans.get_byte_enable_ptr(), trans.get_byte_enable_length(),
                             completed, lane, length)
                : read_beat(address - lane, trans.get_data_ptr() + completed, lane, length);
            if (!ok) {{ trans.set_response_status(tlm::TLM_GENERIC_ERROR_RESPONSE); return; }}
            address += length; completed += length;
        }}
        delay += sc_core::sc_time(completed / BUS_BYTES + 1, sc_core::SC_NS);
        trans.set_response_status(tlm::TLM_OK_RESPONSE);
    }}

private:
    void drive_idle() {{
        sig_awvalid.write(false); sig_wvalid.write(false); sig_bready.write(false);
        sig_arvalid.write(false); sig_rready.write(false);
    }}

    bool write_beat(uint64_t address, const unsigned char* data,
                    const unsigned char* byte_enable, unsigned byte_enable_length,
                    unsigned data_offset, unsigned lane, unsigned length) {{
        uint64_t word = 0, strobe = 0;
        for (unsigned i = 0; i < length; ++i) {{
            const bool enabled = !byte_enable || !byte_enable_length ||
                byte_enable[(data_offset + i) % byte_enable_length] != 0;
            if (enabled) {{
                word |= static_cast<uint64_t>(data[i]) << (8 * (lane + i));
                strobe |= uint64_t{{1}} << (lane + i);
            }}
        }}
        sig_awaddr.write(address); sig_wdata.write(word); __WSTRB__
        sig_awvalid.write(true); sig_wvalid.write(true); sig_bready.write(true);
        bool aw_done = false, w_done = false;
        for (unsigned cycle = 0; cycle < TIMEOUT_CYCLES; ++cycle) {{
            wait(clk.posedge_event());
            if (!aw_done && sig_awready.read()) {{ aw_done = true; sig_awvalid.write(false); }}
            if (!w_done && sig_wready.read()) {{ w_done = true; sig_wvalid.write(false); }}
            if (aw_done && w_done && sig_bvalid.read()) {{
                const bool okay = sig_bresp.read() == 0; sig_bready.write(false); return okay;
            }}
        }}
        drive_idle(); return false;
    }}

    bool read_beat(uint64_t address, unsigned char* data, unsigned lane, unsigned length) {{
        sig_araddr.write(address); sig_arvalid.write(true); sig_rready.write(true);
        bool ar_done = false;
        for (unsigned cycle = 0; cycle < TIMEOUT_CYCLES; ++cycle) {{
            wait(clk.posedge_event());
            if (!ar_done && sig_arready.read()) {{ ar_done = true; sig_arvalid.write(false); }}
            if (ar_done && sig_rvalid.read()) {{
                const uint64_t word = sig_rdata.read();
                for (unsigned i = 0; i < length; ++i)
                    data[i] = static_cast<unsigned char>(word >> (8 * (lane + i)));
                const bool okay = sig_rresp.read() == 0; sig_rready.write(false); return okay;
            }}
        }}
        drive_idle(); return false;
    }}
}};
""".replace("__WSTRB__", wstrb)



def _render_generic_bridge(config, signature, mapping, port_by_name, protocol):
    """Generate a SystemC bridge for protocols other than axi-lite."""
    mod = signature.module_name
    port_decls = []
    port_binds = []
    for p in signature.ports:
        sc_t = _sc_type(p.width)
        port_decls.append('    sc_core::sc_signal<' + sc_t + '> sig_' + p.name + '{"sig_' + p.name + '"};')
        port_binds.append('        rtl.' + p.name + '(sig_' + p.name + ');')

    lines = []
    lines.append('#pragma once')
    lines.append('#include <algorithm>')
    lines.append('#include <cstdint>')
    lines.append('#include <systemc>')
    lines.append('#include <tlm>')
    lines.append('#include <tlm_utils/simple_target_socket.h>')
    lines.append('#include "V' + mod + '.h"')
    lines.append('')
    lines.append('SC_MODULE(BridgeTop) {')
    lines.append('    static constexpr uint64_t BASE_ADDRESS = 0x{:x}ULL;'.format(config.base_address))
    lines.append('    static constexpr unsigned BUS_BYTES = {};'.format(config.data_width // 8))
    lines.append('    static constexpr unsigned TIMEOUT_CYCLES = {};'.format(config.timeout_cycles))
    lines.append('    tlm_utils::simple_target_socket<BridgeTop> target_socket{"target_socket"};')
    lines.append('    sc_core::sc_clock clk{{"clk", {:.6f}, sc_core::SC_NS}};'.format(1000.0 / config.clk_freq_mhz))
    lines.append('    sc_core::sc_signal<bool> rst_n{"rst_n"};')
    lines.append('    V' + mod + ' rtl{"rtl"};')
    lines.extend(port_decls)
    lines.append('')
    lines.append('    SC_CTOR(BridgeTop) {')
    lines.append('        target_socket.register_b_transport(this, &BridgeTop::b_transport);')
    lines.extend(port_binds)
    lines.append('        SC_THREAD(reset_thread);')
    lines.append('    }')
    lines.append('')
    lines.append('    void reset_thread() {')
    lines.append('        rst_n.write(false);')
    lines.append('        for (unsigned i = 0; i < 4; ++i) wait(clk.posedge_event());')
    lines.append('        rst_n.write(true);')
    lines.append('    }')
    lines.append('')
    lines.append('    void b_transport(tlm::tlm_generic_payload& trans, sc_core::sc_time& delay) {')
    lines.append('        if (!trans.get_data_ptr() || !trans.get_data_length()) {')
    lines.append('            trans.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE); return;')
    lines.append('        }')
    lines.append('        uint64_t address = trans.get_address();')
    lines.append('        if (address >= BASE_ADDRESS) address -= BASE_ADDRESS;')
    lines.append('        if (trans.is_write()) {')
    lines.append('            uint64_t word = 0;')
    lines.append('            for (unsigned i = 0; i < trans.get_data_length() && i < sizeof(word); ++i)')
    lines.append('                word |= static_cast<uint64_t>(trans.get_data_ptr()[i]) << (8 * i);')
    lines.append('            write_single(address, word);')
    lines.append('        } else {')
    lines.append('            uint64_t word = read_single(address);')
    lines.append('            for (unsigned i = 0; i < trans.get_data_length() && i < sizeof(word); ++i)')
    lines.append('                trans.get_data_ptr()[i] = static_cast<unsigned char>(word >> (8 * i));')
    lines.append('        }')
    lines.append('        delay += sc_core::sc_time(BUS_BYTES, sc_core::SC_NS);')
    lines.append('        trans.set_response_status(tlm::TLM_OK_RESPONSE);')
    lines.append('    }')
    lines.append('')
    lines.append('private:')
    lines.append('    void write_single(uint64_t addr, uint64_t data) {')
    lines.append('        (void)addr; (void)data;')
    lines.append('        for (unsigned i = 0; i < TIMEOUT_CYCLES; ++i) wait(clk.posedge_event());')
    lines.append('    }')
    lines.append('')
    lines.append('    uint64_t read_single(uint64_t addr) {')
    lines.append('        (void)addr;')
    lines.append('        for (unsigned i = 0; i < TIMEOUT_CYCLES; ++i) wait(clk.posedge_event());')
    lines.append('        return 0;')
    lines.append('    }')
    lines.append('};')
    lines.append('')

    return '\n'.join(lines)


def _render_local_testbench(config: Config) -> str:
    return f"""#pragma once
#include <array>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_initiator_socket.h>

SC_MODULE(LocalTestbench) {{
    tlm_utils::simple_initiator_socket<LocalTestbench> initiator{{"initiator"}};

    SC_CTOR(LocalTestbench) {{
        SC_THREAD(run);
    }}

private:
    bool transfer(tlm::tlm_command command, uint64_t address, unsigned char* data, unsigned length,
                  unsigned char* byte_enable = nullptr, unsigned byte_enable_length = 0) {{
        tlm::tlm_generic_payload payload;
        sc_core::sc_time delay = sc_core::SC_ZERO_TIME;
        payload.set_command(command);
        payload.set_address(address);
        payload.set_data_ptr(data);
        payload.set_data_length(length);
        payload.set_streaming_width(length);
        payload.set_byte_enable_ptr(byte_enable);
        payload.set_byte_enable_length(byte_enable_length);
        payload.set_response_status(tlm::TLM_INCOMPLETE_RESPONSE);
        initiator->b_transport(payload, delay);
        if (delay != sc_core::SC_ZERO_TIME) wait(delay);
        return payload.is_response_ok();
    }}

    void run() {{
        wait(100, sc_core::SC_NS);
        uint32_t value = 0x123456a5;
        if (!transfer(tlm::TLM_WRITE_COMMAND, 0x{config.base_address:x}ULL,
                      reinterpret_cast<unsigned char*>(&value), sizeof(value))) {{
            fail("word write failed"); return;
        }}
        uint32_t observed = 0;
        if (!transfer(tlm::TLM_READ_COMMAND, 0x{config.base_address:x}ULL,
                      reinterpret_cast<unsigned char*>(&observed), sizeof(observed)) ||
            observed != value) {{
            fail("word readback mismatch"); return;
        }}
        unsigned char byte = 0x5a;
        if (!transfer(tlm::TLM_WRITE_COMMAND, 0x{config.base_address + 1:x}ULL, &byte, 1)) {{
            fail("byte write failed"); return;
        }}
        observed = 0;
        if (!transfer(tlm::TLM_READ_COMMAND, 0x{config.base_address:x}ULL,
                      reinterpret_cast<unsigned char*>(&observed), sizeof(observed)) ||
            observed != 0x12345aa5U) {{
            fail("byte-enable readback mismatch"); return;
        }}
        std::cout << "ARTI COSIM PASS" << std::endl;
        sc_core::sc_stop();
    }}

    void fail(const char* message) {{
        std::cerr << "ARTI COSIM FAIL: " << message << std::endl;
        sc_core::sc_stop();
    }}
}};
"""


def generate_project(config: Config, signature: ModuleSignature, inference: dict, output: str | Path) -> Path:
    if config.mode in ("remote-port", "vfio-user"):
        raise ValueError("remote-port and vfio-user are unsupported; use qemu-sysbus with upstream QEMU")
    if config.mode not in ("local", "qemu-sysbus", "qemu-embedded"):
        raise ValueError("bridge.mode must be local, qemu-sysbus, or qemu-embedded")
    root = Path(output)
    if config.data_width < 8 or config.data_width > 64 or config.data_width % 8:
        raise ValueError("bridge.data_width must be a byte-aligned value between 8 and 64")
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"output directory is not empty: {root}")
    for directory in ("rtl", "bridge", "qemu", "build", "tb/baremetal", "reports", "embedded"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    source_paths = [Path(item) for item in config.source_files or []]
    for source in source_paths:
        shutil.copy2(source, root / "rtl" / source.name)
    (root / "reports/inference_report.json").write_text(
        json.dumps({"signature": signature.to_dict(), "inference": inference}, indent=2) + "\n", encoding="utf-8"
    )
    mapping = inference["port_mapping"]
    port_by_name = {p.name: p for p in signature.ports}
    protocol = inference["protocol"]
    interrupts = inference.get("interrupts", [])
    if protocol == "axi-lite":
        header = _render_axi_lite_bridge(config, signature, mapping, port_by_name)
    else:
        header = _render_generic_bridge(config, signature, mapping, port_by_name, protocol)
    (root / "bridge/bridge_top.h").write_text(header, encoding="utf-8")
    (root / "bridge/signal_map.h").write_text(
        "#pragma once\n" + "\n".join(f'#define ARTI_{k} "{v}"' for k, v in mapping.items()) + "\n", encoding="utf-8"
    )
    sources = " ".join(f"${{CMAKE_SOURCE_DIR}}/rtl/{p.name}" for p in source_paths)
    if config.mode == "qemu-embedded":
        for relative, content in render_embedded_model(config, signature, mapping, port_by_name, protocol, interrupts).items():
            (root / relative).write_text(content, encoding="utf-8")
        (root / "embedded/build_embedded.sh").chmod(0o755)
        return root
    if config.mode == "qemu-sysbus":
        for relative, content in (render_qemu_sysbus(config) | render_socket_adapter(config)).items():
            (root / relative).write_text(content, encoding="utf-8")
        remote_cmake = ""
    else:
        (root / "tb/local_testbench.h").write_text(_render_local_testbench(config), encoding="utf-8")
        (root / "sc_main.cpp").write_text('''#include <systemc>
#include "bridge/bridge_top.h"
#include "tb/local_testbench.h"

int sc_main(int argc, char** argv) {
    (void)argc; (void)argv;
    BridgeTop top{"top"};
    LocalTestbench testbench{"testbench"};
    testbench.initiator.bind(top.target_socket);
    sc_core::sc_start();
    return 0;
}
''', encoding="utf-8")
        runner = root / "build/run_cosim.sh"
        runner.write_text('''#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "$0")/.." && pwd)"
cmake -S "$root_dir" -B "$root_dir/build/cmake"
cmake --build "$root_dir/build/cmake" --parallel
exec "$root_dir/build/cmake/cosim"
''', encoding="utf-8")
        remote_cmake = ""
    cmake = f'''cmake_minimum_required(VERSION 3.16)
project({config.project_name} LANGUAGES C CXX)
find_package(verilator REQUIRED HINTS $ENV{{VERILATOR_ROOT}})
find_package(PkgConfig REQUIRED)
pkg_check_modules(SYSTEMC REQUIRED IMPORTED_TARGET systemc)
add_executable(cosim ${{CMAKE_SOURCE_DIR}}/sc_main.cpp)
target_include_directories(cosim PRIVATE ${{CMAKE_SOURCE_DIR}})
target_link_libraries(cosim PRIVATE PkgConfig::SYSTEMC)
{remote_cmake}
verilate(cosim SYSTEMC TOP_MODULE {signature.module_name} PREFIX V{signature.module_name} SOURCES {sources})
'''
    (root / "CMakeLists.txt").write_text(cmake, encoding="utf-8")
    runner = root / "build/run_cosim.sh"
    runner.chmod(0o755)
    return root
