"""Protocol-specific embedded model renderers.

Each function returns a C++ wrapper string that drives the correct bus
handshake on a Verilated RTL model, exposing a uniform C API:

    void arti_rtl_model_init(void);
    int  arti_rtl_model_write(uint64_t addr, uint64_t data, unsigned size);
    int  arti_rtl_model_read(uint64_t addr, uint64_t *data, unsigned size);
    int  arti_rtl_model_check_irq(unsigned index);   // only if interrupts exist
"""


def _common_header(irq_count):
    irq_decl = ""
    if irq_count > 0:
        irq_decl = "int arti_rtl_model_check_irq(unsigned index);\n"
    return (
        "#ifndef ARTI_RTL_MODEL_H\n"
        "#define ARTI_RTL_MODEL_H\n"
        "#include <stdint.h>\n"
        "#ifdef __cplusplus\n"
        'extern "C" {\n'
        "#endif\n"
        "void arti_rtl_model_init(void);\n"
        "int arti_rtl_model_write(uint64_t addr, uint64_t data, unsigned size);\n"
        "int arti_rtl_model_read(uint64_t addr, uint64_t *data, unsigned size);\n"
        "typedef int (*arti_mem_read_cb)(uint64_t addr, uint8_t *data, unsigned size, uint64_t transaction_id);\n"
        "typedef int (*arti_mem_write_cb)(uint64_t addr, const uint8_t *data, unsigned size, uint64_t byte_mask, uint64_t transaction_id);\n"
        "void arti_rtl_model_set_memory_callbacks(arti_mem_read_cb read_cb, arti_mem_write_cb write_cb);\n"
        + irq_decl +
        "#ifdef __cplusplus\n"
        "}\n"
        "#endif\n"
        "#endif\n"
    )


def _irq_check_func(interrupts):
    if not interrupts:
        return ""
    lines = []
    lines.append("")
    lines.append('extern "C" int arti_rtl_model_check_irq(unsigned index)')
    lines.append("{")
    lines.append("    if (!g_rtl)")
    lines.append("        return 0;")
    lines.append("    switch (index) {")
    for idx, irq in enumerate(interrupts):
        lines.append("        case {}: tick(); return g_rtl->{} ? 1 : 0;".format(idx, irq["name"]))
    lines.append("        default: return 0;")
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _memory_bridge_for_axi4(signature):
    """Render adapters from common Decoupled memory clients to one callback ABI."""
    names = {p.name for p in signature.ports}
    word_channels = []
    for prefix in ("io_cbMem", "io_fbMem", "io_texMem"):
        required = {
            f"{prefix}_req_ready", f"{prefix}_req_valid",
            f"{prefix}_req_bits_write", f"{prefix}_req_bits_addr",
            f"{prefix}_req_bits_data", f"{prefix}_resp_ready",
            f"{prefix}_resp_valid", f"{prefix}_resp_bits_data",
            f"{prefix}_resp_bits_write",
        }
        if required <= names:
            word_channels.append(prefix)

    line_channels = []
    for stem in ("io_kernelMem", "io_kernelWordMem"):
        req = stem + "Req"
        resp = stem + "Resp"
        required = {
            f"{req}_ready", f"{req}_valid", f"{req}_bits_address",
            f"{req}_bits_writeData", f"{req}_bits_byteMask",
            f"{req}_bits_isWrite", f"{req}_bits_sizeLog2",
            f"{req}_bits_transactionId", f"{resp}_ready",
            f"{resp}_valid", f"{resp}_bits_readData",
            f"{resp}_bits_fault", f"{resp}_bits_transactionId",
        }
        if required <= names:
            line_channels.append((req, resp))

    if not word_channels and not line_channels:
        return "", "", ""

    globals_ = [
        "struct ArtiWordPending { bool valid; uint32_t data; bool write; };",
        "struct ArtiLinePending { bool valid; uint32_t data[16]; uint8_t fault; uint8_t id; };",
    ]
    drive = []
    capture = []
    for prefix in word_channels:
        ident = prefix.removeprefix("io_")
        globals_.append(f"static ArtiWordPending g_{ident}_pending = {{false, 0, false}};")
        drive.extend([
            f"    g_rtl->{prefix}_req_ready = !g_{ident}_pending.valid;",
            f"    g_rtl->{prefix}_resp_valid = g_{ident}_pending.valid;",
            f"    g_rtl->{prefix}_resp_bits_data = g_{ident}_pending.data;",
            f"    g_rtl->{prefix}_resp_bits_write = g_{ident}_pending.write;",
        ])
        capture.extend([
            f"    if (g_{ident}_pending.valid && g_rtl->{prefix}_resp_ready)",
            f"        g_{ident}_pending.valid = false;",
            f"    if (!g_{ident}_pending.valid && g_rtl->{prefix}_req_valid && g_rtl->{prefix}_req_ready) {{",
            "        uint8_t bytes[4] = {0, 0, 0, 0};",
            f"        uint64_t addr = g_rtl->{prefix}_req_bits_addr;",
            f"        bool write = g_rtl->{prefix}_req_bits_write;",
            f"        uint32_t word = g_rtl->{prefix}_req_bits_data;",
            "        int status = -1;",
            "        if (write && g_mem_write_cb) {",
            "            memcpy(bytes, &word, sizeof(word));",
            "            status = g_mem_write_cb(addr, bytes, 4, 0xf, 0);",
            "        } else if (!write && g_mem_read_cb) {",
            "            status = g_mem_read_cb(addr, bytes, 4, 0);",
            "            memcpy(&word, bytes, sizeof(word));",
            "        }",
            f"        g_{ident}_pending.data = word;",
            f"        g_{ident}_pending.write = write;",
            f"        g_{ident}_pending.valid = status == 0;",
            "    }",
        ])

    for req, resp in line_channels:
        ident = req.removeprefix("io_")
        globals_.append(f"static ArtiLinePending g_{ident}_pending = {{false, {{0}}, 0, 0}};")
        drive.extend([
            f"    g_rtl->{req}_ready = !g_{ident}_pending.valid;",
            f"    g_rtl->{resp}_valid = g_{ident}_pending.valid;",
            f"    for (unsigned i = 0; i < 16; i++) g_rtl->{resp}_bits_readData[i] = g_{ident}_pending.data[i];",
            f"    g_rtl->{resp}_bits_fault = g_{ident}_pending.fault;",
            f"    g_rtl->{resp}_bits_transactionId = g_{ident}_pending.id;",
        ])
        capture.extend([
            f"    if (g_{ident}_pending.valid && g_rtl->{resp}_ready)",
            f"        g_{ident}_pending.valid = false;",
            f"    if (!g_{ident}_pending.valid && g_rtl->{req}_valid && g_rtl->{req}_ready) {{",
            "        uint8_t bytes[64] = {0};",
            f"        uint64_t addr = g_rtl->{req}_bits_address;",
            f"        unsigned size = 1u << g_rtl->{req}_bits_sizeLog2;",
            "        if (size > 64) size = 64;",
            f"        uint64_t id = g_rtl->{req}_bits_transactionId;",
            f"        bool write = g_rtl->{req}_bits_isWrite;",
            "        int status = -1;",
            "        if (write && g_mem_write_cb) {",
            f"            memcpy(bytes, &g_rtl->{req}_bits_writeData, sizeof(bytes));",
            f"            status = g_mem_write_cb(addr, bytes, size, g_rtl->{req}_bits_byteMask, id);",
            "        } else if (!write && g_mem_read_cb) {",
            "            status = g_mem_read_cb(addr, bytes, size, id);",
            "        }",
            f"        memcpy(g_{ident}_pending.data, bytes, sizeof(bytes));",
            f"        g_{ident}_pending.fault = status != 0;",
            f"        g_{ident}_pending.id = id;",
            f"        g_{ident}_pending.valid = true;",
            "    }",
        ])
    return "\n".join(globals_), "\n".join(drive), "\n".join(capture)


def _preamble(mod, clk, rst, memory_globals="", memory_drive="", memory_capture=""):
    lines = []
    lines.append("// Auto-generated by arti \u2014 do not edit.")
    lines.append('#include "V{}.h"'.format(mod))
    lines.append('#include "verilated.h"')
    lines.append('#include "arti_rtl_model.h"')
    lines.append("#include <memory>")
    lines.append("#include <cstring>")
    lines.append("")
    lines.append("static VerilatedContext *g_ctx = nullptr;")
    lines.append("static V{} *g_rtl = nullptr;".format(mod))
    lines.append("static arti_mem_read_cb g_mem_read_cb = nullptr;")
    lines.append("static arti_mem_write_cb g_mem_write_cb = nullptr;")
    if memory_globals:
        lines.append(memory_globals)
    lines.append("")
    lines.append("extern \"C\" void arti_rtl_model_set_memory_callbacks(arti_mem_read_cb read_cb, arti_mem_write_cb write_cb)")
    lines.append("{")
    lines.append("    g_mem_read_cb = read_cb;")
    lines.append("    g_mem_write_cb = write_cb;")
    lines.append("}")
    lines.append("static void tick(void)")
    lines.append("{")
    if memory_drive:
        lines.append(memory_drive)
    lines.append("    g_rtl->{} = 0;".format(clk))
    lines.append("    g_rtl->eval();")
    if memory_capture:
        lines.append(memory_capture)
    lines.append("    g_rtl->{} = 1;".format(clk))
    lines.append("    g_rtl->eval();")
    lines.append("}")
    return "\n".join(lines)


def _init_func(mod, clk, rst, idle_body):
    lines = []
    lines.append("")
    lines.append('extern "C" void arti_rtl_model_init(void)')
    lines.append("{")
    lines.append("    if (g_rtl)")
    lines.append("        return;")
    lines.append("    g_ctx = new VerilatedContext;")
    lines.append("    const char *argv[] = {nullptr};")
    lines.append("    g_ctx->commandArgs(0, argv);")
    lines.append('    g_rtl = new V{}{{g_ctx, "ARTI_RTL"}};'.format(mod))
    lines.append("    g_rtl->{} = 0;".format(clk))
    reset_asserted = 1 if rst == "reset" else 0
    reset_deasserted = 0 if rst == "reset" else 1
    lines.append("    g_rtl->{} = {};".format(rst, reset_asserted))
    lines.append(idle_body)
    lines.append("    g_rtl->eval();")
    lines.append("    tick();")
    lines.append("    g_rtl->{} = {};".format(rst, reset_deasserted))
    lines.append("    g_rtl->eval();")
    lines.append("    tick();")
    lines.append("}")
    return "\n".join(lines)


def render_axi_lite_model(config, signature, mapping, port_by_name, interrupts):
    mod = signature.module_name
    addr_width = port_by_name[mapping["AWADDR"]].width
    addr_mask = (1 << addr_width) - 1
    data_width = config.data_width
    data_type = "uint32_t" if data_width <= 32 else "uint64_t"
    max_bytes = data_width // 8
    max_bytes_m1 = max_bytes - 1
    has_wstrb = "WSTRB" in mapping
    timeout = config.timeout_cycles
    clk = mapping["ACLK"]
    rst = mapping["ARESETN"]
    awaddr = mapping["AWADDR"]
    awvalid = mapping["AWVALID"]
    awready = mapping["AWREADY"]
    wdata = mapping["WDATA"]
    wvalid = mapping["WVALID"]
    wready = mapping["WREADY"]
    bvalid = mapping["BVALID"]
    bready = mapping["BREADY"]
    araddr = mapping["ARADDR"]
    arvalid = mapping["ARVALID"]
    arready = mapping["ARREADY"]
    rdata = mapping["RDATA"]
    rvalid = mapping["RVALID"]
    rready = mapping["RREADY"]
    wstrb = mapping.get("WSTRB", "")

    idle_body = "\n".join([
        "    g_rtl->{} = 0;".format(awvalid),
        "    g_rtl->{} = 0;".format(wvalid),
        "    g_rtl->{} = 0;".format(bready),
        "    g_rtl->{} = 0;".format(arvalid),
        "    g_rtl->{} = 0;".format(rready),
    ])

    lines = [_preamble(mod, clk, rst)]
    lines.append("")
    lines.append(_init_func(mod, clk, rst, idle_body))
    lines.append("")
    lines.append("static void idle(void)")
    lines.append("{")
    lines.append(idle_body)
    lines.append("}")
    lines.append("")
    # write function
    lines.append('extern "C" int arti_rtl_model_write(uint64_t addr, uint64_t data, unsigned size)')
    lines.append("{")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    uint8_t addr_val = (uint8_t)(addr & {:#04x});".format(addr_mask))
    lines.append("    unsigned lane_shift = addr_val & ({});".format(max_bytes_m1))
    lines.append("    {} word = ({})(({})data << (lane_shift * 8));".format(
        data_type, data_type, data_type))
    lines.append("    uint8_t wstrb = (uint8_t)((1u << size) - 1u) << lane_shift;")
    lines.append("    idle();")
    lines.append("    g_rtl->{} = addr_val;".format(awaddr))
    lines.append("    g_rtl->{} = word;".format(wdata))
    if has_wstrb:
        lines.append("    g_rtl->{} = wstrb;".format(wstrb))
    else:
        lines.append("    (void)wstrb;")
    lines.append("    g_rtl->{} = 1;".format(awvalid))
    lines.append("    g_rtl->{} = 1;".format(wvalid))
    lines.append("    g_rtl->eval();")
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{} && g_rtl->{}) {{ tick(); break; }}".format(awready, wready))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->{} = 1;".format(bready))
    lines.append("    g_rtl->eval();")
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ tick(); break; }}".format(bvalid))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    return 0;")
    lines.append("}")
    lines.append("")
    # read function
    lines.append('extern "C" int arti_rtl_model_read(uint64_t addr, uint64_t *data, unsigned size)')
    lines.append("{")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    uint8_t addr_val = (uint8_t)(addr & {:#04x});".format(addr_mask))
    lines.append("    idle();")
    lines.append("    g_rtl->{} = addr_val;".format(araddr))
    lines.append("    g_rtl->{} = 1;".format(arvalid))
    lines.append("    g_rtl->eval();")
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ tick(); break; }}".format(arready))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->{} = 1;".format(rready))
    lines.append("    g_rtl->eval();")
    lines.append("    {} rdata_val = 0;".format(data_type))
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ rdata_val = g_rtl->{}; break; }}".format(rvalid, rdata))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    tick();")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    *data = rdata_val;")
    lines.append("    return 0;")
    lines.append("}")
    lines.append(_irq_check_func(interrupts))
    return "\n".join(lines)


def render_apb_model(config, signature, mapping, port_by_name, interrupts):
    mod = signature.module_name
    addr_width = port_by_name[mapping["PADDR"]].width
    addr_mask = (1 << addr_width) - 1
    data_width = config.data_width
    data_type = "uint32_t" if data_width <= 32 else "uint64_t"
    max_bytes = data_width // 8
    timeout = config.timeout_cycles
    clk = mapping["PCLK"]
    rst = mapping["PRESETN"]
    addr = mapping["PADDR"]
    wdata = mapping["PWDATA"]
    rdata = mapping["PRDATA"]
    write = mapping["PWRITE"]
    sel = mapping["PSEL"]
    enable = mapping["PENABLE"]
    ready = mapping.get("PREADY", "")
    has_ready = bool(ready)

    idle_body = "\n".join([
        "    g_rtl->{} = 0;".format(sel),
        "    g_rtl->{} = 0;".format(enable),
        "    g_rtl->{} = 0;".format(write),
    ])

    lines = [_preamble(mod, clk, rst)]
    lines.append("")
    lines.append(_init_func(mod, clk, rst, idle_body))
    lines.append("")
    lines.append("static void idle(void)")
    lines.append("{")
    lines.append(idle_body)
    lines.append("}")
    lines.append("")
    # write function
    lines.append('extern "C" int arti_rtl_model_write(uint64_t addr, uint64_t data, unsigned size)')
    lines.append("{")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    {} word = ({})data;".format(data_type, data_type))
    lines.append("    uint8_t addr_val = (uint8_t)(addr & {:#04x});".format(addr_mask))
    lines.append("    idle();")
    lines.append("    g_rtl->{} = addr_val;".format(addr))
    lines.append("    g_rtl->{} = word;".format(wdata))
    lines.append("    g_rtl->{} = 1;".format(write))
    lines.append("    g_rtl->{} = 1;".format(sel))
    lines.append("    g_rtl->{} = 0;".format(enable))
    lines.append("    g_rtl->eval();")
    lines.append("    tick();")
    lines.append("    g_rtl->{} = 1;".format(enable))
    lines.append("    g_rtl->eval();")
    if has_ready:
        lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
        lines.append("        if (g_rtl->{}) {{ tick(); break; }}".format(ready))
        lines.append("        tick();")
        lines.append("    }")
    else:
        lines.append("    tick();")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    return 0;")
    lines.append("}")
    lines.append("")
    # read function
    lines.append('extern "C" int arti_rtl_model_read(uint64_t addr, uint64_t *data, unsigned size)')
    lines.append("{")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    uint8_t addr_val = (uint8_t)(addr & {:#04x});".format(addr_mask))
    lines.append("    idle();")
    lines.append("    g_rtl->{} = addr_val;".format(addr))
    lines.append("    g_rtl->{} = 0;".format(write))
    lines.append("    g_rtl->{} = 1;".format(sel))
    lines.append("    g_rtl->{} = 0;".format(enable))
    lines.append("    g_rtl->eval();")
    lines.append("    tick();")
    lines.append("    g_rtl->{} = 1;".format(enable))
    lines.append("    g_rtl->eval();")
    lines.append("    {} rdata_val = 0;".format(data_type))
    if has_ready:
        lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
        lines.append("        if (g_rtl->{}) {{ rdata_val = g_rtl->{}; tick(); break; }}".format(ready, rdata))
        lines.append("        tick();")
        lines.append("    }")
    else:
        lines.append("    rdata_val = g_rtl->{};".format(rdata))
        lines.append("    tick();")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    *data = rdata_val;")
    lines.append("    return 0;")
    lines.append("}")
    lines.append(_irq_check_func(interrupts))
    return "\n".join(lines)


def render_axi4_model(config, signature, mapping, port_by_name, interrupts):
    mod = signature.module_name
    addr_width = port_by_name[mapping["AWADDR"]].width
    addr_mask = (1 << addr_width) - 1
    data_width = config.data_width
    data_type = "uint32_t" if data_width <= 32 else "uint64_t"
    max_bytes = data_width // 8
    max_bytes_m1 = max_bytes - 1
    has_wstrb = "WSTRB" in mapping
    timeout = config.timeout_cycles
    clk = mapping["ACLK"]
    rst = mapping["ARESETN"]
    awaddr = mapping["AWADDR"]
    awvalid = mapping["AWVALID"]
    awready = mapping["AWREADY"]
    wdata = mapping["WDATA"]
    wvalid = mapping["WVALID"]
    wready = mapping["WREADY"]
    bvalid = mapping["BVALID"]
    bready = mapping["BREADY"]
    araddr = mapping["ARADDR"]
    arvalid = mapping["ARVALID"]
    arready = mapping["ARREADY"]
    rdata = mapping["RDATA"]
    rvalid = mapping["RVALID"]
    rready = mapping["RREADY"]
    awlen = mapping["AWLEN"]
    awsize = mapping["AWSIZE"]
    awburst = mapping["AWBURST"]
    wlast = mapping["WLAST"]
    arlen = mapping["ARLEN"]
    arsize = mapping["ARSIZE"]
    arburst = mapping["ARBURST"]
    rlast = mapping["RLAST"]
    wstrb = mapping.get("WSTRB", "")

    # Keep unbridged client request channels quiescent but ready. This avoids
    # an accidental X/0 ready input stalling the RTL before a future guest-
    # memory callback bridge is installed.
    mapped_ports = set(mapping.values())
    client_defaults = []
    for port in signature.ports:
        if port.name in mapped_ports or port.direction != "input":
            continue
        canon = port.name.upper()
        if canon.endswith("_READY") or canon.endswith("READY"):
            client_defaults.append("    g_rtl->{} = 1;".format(port.name))
        elif any(token in canon for token in ("_VALID", "VALID", "_FAULT", "FAULT")):
            client_defaults.append("    g_rtl->{} = 0;".format(port.name))
    idle_body = "\n".join([
        "    g_rtl->{} = 0;".format(awvalid),
        "    g_rtl->{} = 0;".format(wvalid),
        "    g_rtl->{} = 0;".format(bready),
        "    g_rtl->{} = 0;".format(arvalid),
        "    g_rtl->{} = 0;".format(rready),
    ] + client_defaults)

    memory_globals, memory_drive, memory_capture = _memory_bridge_for_axi4(signature)
    lines = [_preamble(mod, clk, rst, memory_globals, memory_drive, memory_capture)]
    lines.append("")
    lines.append(_init_func(mod, clk, rst, idle_body))
    lines.append("")
    lines.append("static void idle(void)")
    lines.append("{")
    lines.append(idle_body)
    lines.append("}")
    lines.append("")
    # write function
    lines.append('extern "C" int arti_rtl_model_write(uint64_t addr, uint64_t data, unsigned size)')
    lines.append("{")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    {} word = ({})data;".format(data_type, data_type))
    lines.append("    uint8_t addr_val = (uint8_t)(addr & {:#04x});".format(addr_mask))
    lines.append("    uint8_t wstrb = (uint8_t)((1u << size) - 1u) << (addr_val & ({}));".format(max_bytes_m1))
    lines.append("    idle();")
    lines.append("    g_rtl->{} = addr_val;".format(awaddr))
    lines.append("    g_rtl->{} = 0;".format(awlen))
    lines.append("    g_rtl->{} = 2;".format(awsize))
    lines.append("    g_rtl->{} = 1;".format(awburst))
    lines.append("    g_rtl->{} = word;".format(wdata))
    lines.append("    g_rtl->{} = 1;".format(wlast))
    if has_wstrb:
        lines.append("    g_rtl->{} = wstrb;".format(wstrb))
    else:
        lines.append("    (void)wstrb;")
    lines.append("    g_rtl->{} = 1;".format(awvalid))
    lines.append("    g_rtl->{} = 1;".format(wvalid))
    lines.append("    g_rtl->eval();")
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{} && g_rtl->{}) {{ tick(); break; }}".format(awready, wready))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->{} = 1;".format(bready))
    lines.append("    g_rtl->eval();")
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ tick(); break; }}".format(bvalid))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    return 0;")
    lines.append("}")
    lines.append("")
    # read function
    lines.append('extern "C" int arti_rtl_model_read(uint64_t addr, uint64_t *data, unsigned size)')
    lines.append("{")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    uint8_t addr_val = (uint8_t)(addr & {:#04x});".format(addr_mask))
    lines.append("    idle();")
    lines.append("    g_rtl->{} = addr_val;".format(araddr))
    lines.append("    g_rtl->{} = 0;".format(arlen))
    lines.append("    g_rtl->{} = 2;".format(arsize))
    lines.append("    g_rtl->{} = 1;".format(arburst))
    lines.append("    g_rtl->{} = 1;".format(arvalid))
    lines.append("    g_rtl->eval();")
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ tick(); break; }}".format(arready))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->{} = 1;".format(rready))
    lines.append("    g_rtl->eval();")
    lines.append("    {} rdata_val = 0;".format(data_type))
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ rdata_val = g_rtl->{}; break; }}".format(rvalid, rdata))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    tick();")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    *data = rdata_val;")
    lines.append("    return 0;")
    lines.append("}")
    lines.append(_irq_check_func(interrupts))
    return "\n".join(lines)


def render_ahb_model(config, signature, mapping, port_by_name, interrupts):
    mod = signature.module_name
    addr_width = port_by_name[mapping["HADDR"]].width
    addr_mask = (1 << addr_width) - 1
    data_width = config.data_width
    data_type = "uint32_t" if data_width <= 32 else "uint64_t"
    max_bytes = data_width // 8
    timeout = config.timeout_cycles
    clk = mapping["HCLK"]
    rst = mapping["HRESETN"]
    addr = mapping["HADDR"]
    wdata = mapping["HWDATA"]
    rdata = mapping["HRDATA"]
    write = mapping["HWRITE"]
    trans = mapping["HTRANS"]
    ready = mapping["HREADY"]

    idle_body = "\n".join([
        "    g_rtl->{} = 0;".format(trans),
        "    g_rtl->{} = 0;".format(write),
    ])

    lines = [_preamble(mod, clk, rst)]
    lines.append("")
    lines.append(_init_func(mod, clk, rst, idle_body))
    lines.append("")
    lines.append("static void idle(void)")
    lines.append("{")
    lines.append(idle_body)
    lines.append("}")
    lines.append("")
    # write function
    lines.append('extern "C" int arti_rtl_model_write(uint64_t addr, uint64_t data, unsigned size)')
    lines.append("{")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    {} word = ({})data;".format(data_type, data_type))
    lines.append("    uint8_t addr_val = (uint8_t)(addr & {:#04x});".format(addr_mask))
    lines.append("    idle();")
    lines.append("    g_rtl->{} = addr_val;".format(addr))
    lines.append("    g_rtl->{} = 1;".format(write))
    lines.append("    g_rtl->{} = 2;".format(trans))
    lines.append("    g_rtl->eval();")
    lines.append("    tick();")
    lines.append("    g_rtl->{} = word;".format(wdata))
    lines.append("    g_rtl->{} = 0;".format(trans))
    lines.append("    g_rtl->eval();")
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ tick(); break; }}".format(ready))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    return 0;")
    lines.append("}")
    lines.append("")
    # read function
    lines.append('extern "C" int arti_rtl_model_read(uint64_t addr, uint64_t *data, unsigned size)')
    lines.append("{")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    uint8_t addr_val = (uint8_t)(addr & {:#04x});".format(addr_mask))
    lines.append("    idle();")
    lines.append("    g_rtl->{} = addr_val;".format(addr))
    lines.append("    g_rtl->{} = 0;".format(write))
    lines.append("    g_rtl->{} = 2;".format(trans))
    lines.append("    g_rtl->eval();")
    lines.append("    tick();")
    lines.append("    g_rtl->{} = 0;".format(trans))
    lines.append("    g_rtl->eval();")
    lines.append("    {} rdata_val = 0;".format(data_type))
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ rdata_val = g_rtl->{}; tick(); break; }}".format(ready, rdata))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    *data = rdata_val;")
    lines.append("    return 0;")
    lines.append("}")
    lines.append(_irq_check_func(interrupts))
    return "\n".join(lines)


def render_axi_stream_model(config, signature, mapping, port_by_name, interrupts):
    mod = signature.module_name
    data_width = port_by_name[mapping["TDATA"]].width
    data_type = "uint32_t" if data_width <= 32 else "uint64_t"
    max_bytes = data_width // 8
    timeout = config.timeout_cycles
    clk = mapping["ACLK"]
    rst = mapping["ARESETN"]
    tdata = mapping["TDATA"]
    tvalid = mapping["TVALID"]
    tready = mapping["TREADY"]

    idle_body = "\n".join([
        "    g_rtl->{} = 0;".format(tvalid),
        "    g_rtl->{} = 0;".format(tready),
    ])

    lines = [_preamble(mod, clk, rst)]
    lines.append("")
    lines.append(_init_func(mod, clk, rst, idle_body))
    lines.append("")
    lines.append("static void idle(void)")
    lines.append("{")
    lines.append(idle_body)
    lines.append("}")
    lines.append("")
    # write function (push TX data)
    lines.append('extern "C" int arti_rtl_model_write(uint64_t addr, uint64_t data, unsigned size)')
    lines.append("{")
    lines.append("    (void)addr;")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    {} word = ({})data;".format(data_type, data_type))
    lines.append("    idle();")
    lines.append("    g_rtl->{} = word;".format(tdata))
    lines.append("    g_rtl->{} = 1;".format(tvalid))
    lines.append("    g_rtl->eval();")
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ tick(); break; }}".format(tready))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    return 0;")
    lines.append("}")
    lines.append("")
    # read function (pull RX data)
    lines.append('extern "C" int arti_rtl_model_read(uint64_t addr, uint64_t *data, unsigned size)')
    lines.append("{")
    lines.append("    (void)addr;")
    lines.append("    if (!g_rtl || size == 0 || size > {})".format(max_bytes))
    lines.append("        return -1;")
    lines.append("    idle();")
    lines.append("    g_rtl->{} = 1;".format(tready))
    lines.append("    g_rtl->eval();")
    lines.append("    {} rdata_val = 0;".format(data_type))
    lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(timeout))
    lines.append("        if (g_rtl->{}) {{ rdata_val = g_rtl->{}; tick(); break; }}".format(tvalid, tdata))
    lines.append("        tick();")
    lines.append("    }")
    lines.append("    idle();")
    lines.append("    g_rtl->eval();")
    lines.append("    *data = rdata_val;")
    lines.append("    return 0;")
    lines.append("}")
    lines.append(_irq_check_func(interrupts))
    return "\n".join(lines)


MODEL_RENDERERS = {
    "axi-lite": render_axi_lite_model,
    "axi4": render_axi4_model,
    "apb": render_apb_model,
    "ahb": render_ahb_model,
    "axi-stream": render_axi_stream_model,
}


def render_model(protocol, config, signature, mapping, port_by_name, interrupts):
    renderer = MODEL_RENDERERS.get(protocol)
    if not renderer:
        raise ValueError("unsupported protocol for embedded model: {}".format(protocol))
    source = renderer(config, signature, mapping, port_by_name, interrupts)
    source += """
// Verilator's non-SystemC runtime may reference this legacy weak symbol.
double sc_time_stamp() { return 0; }
"""
    return source


def render_header(irq_count):
    return _common_header(irq_count)


def render_qemu_stub(mmio_size, interrupts, config=None):
    has_irq = bool(interrupts)
    irq_count = len(interrupts)
    has_display = bool(config and config.display_enabled)
    has_guest_scanout = bool(has_display and config.display_source == "guest-memory")
    has_mmio_vram = bool(has_display and not has_guest_scanout)

    lines = []
    lines.append("/* arti-qemu-stub-v4 */")
    lines.append('#include "qemu/osdep.h"')
    lines.append('#include "hw/core/sysbus.h"')
    lines.append('#include "qapi/error.h"')
    lines.append('#include "qemu/module.h"')
    lines.append('#include "arti_rtl_model.h"')
    lines.append('#include "system/address-spaces.h"')
    if has_display:
        lines.append('#include "ui/console.h"')

    if has_irq:
        lines.append('#include "qemu/timer.h"')
        lines.append('#include "hw/core/irq.h"')

    lines.append("")
    lines.append("#define ARTI_MMIO_SIZE 0x{}u".format(format(mmio_size, "x")))
    lines.append("#define ARTI_IRQ_COUNT {}u".format(irq_count))
    lines.append('#define TYPE_ARTI_RTL "arti-rtl"')
    lines.append("OBJECT_DECLARE_SIMPLE_TYPE(ArtiRtlState, ARTI_RTL)")
    lines.append("struct ArtiRtlState {")
    lines.append("    SysBusDevice parent_obj;")
    lines.append("    MemoryRegion mmio;")
    if has_irq:
        lines.append("    qemu_irq irq[{}];".format(irq_count))
        lines.append("    QEMUTimer *irq_timer;")
        lines.append("    int irq_prev[{}];".format(irq_count))
    if has_display:
        lines.append("    QemuConsole *con;")
        lines.append("    bool invalidate;")
    if has_mmio_vram:
        lines.append("    uint8_t *vram;")
    if has_guest_scanout:
        lines.append("    uint64_t scanout_addr;")
        lines.append("    uint32_t scanout_stride;")
    lines.append("};")
    lines.append("")

    lines.append("static int arti_guest_read(uint64_t addr, uint8_t *data,")
    lines.append("                           unsigned size, uint64_t transaction_id)")
    lines.append("{")
    lines.append("    (void)transaction_id;")
    lines.append("    if (!size || size > 64) return -1;")
    lines.append("    return address_space_read(&address_space_memory, addr,")
    lines.append("                              MEMTXATTRS_UNSPECIFIED, data, size) == MEMTX_OK ? 0 : -1;")
    lines.append("}")
    lines.append("")
    lines.append("static int arti_guest_write(uint64_t addr, const uint8_t *data,")
    lines.append("                            unsigned size, uint64_t byte_mask,")
    lines.append("                            uint64_t transaction_id)")
    lines.append("{")
    lines.append("    (void)transaction_id;")
    lines.append("    if (!size || size > 64) return -1;")
    lines.append("    for (unsigned i = 0; i < size; i++) {")
    lines.append("        if (!(byte_mask & (UINT64_C(1) << i))) continue;")
    lines.append("        if (address_space_write(&address_space_memory, addr + i,")
    lines.append("                                MEMTXATTRS_UNSPECIFIED, data + i, 1) != MEMTX_OK)")
    lines.append("            return -1;")
    lines.append("    }")
    lines.append("    return 0;")
    lines.append("}")
    lines.append("")

    if has_irq:
        poll_ns = 100000
        lines.append("static void arti_update_irqs(ArtiRtlState *s)")
        lines.append("{")
        lines.append("    for (unsigned i = 0; i < {}; i++) {{".format(irq_count))
        lines.append("        int level = arti_rtl_model_check_irq(i);")
        lines.append("        if (level != s->irq_prev[i]) {")
        lines.append("            s->irq_prev[i] = level;")
        lines.append("            qemu_set_irq(s->irq[i], level);")
        lines.append("        }")
        lines.append("    }")
        lines.append("}")
        lines.append("")
        lines.append("static void arti_irq_timer(void *opaque)")
        lines.append("{")
        lines.append("    ArtiRtlState *s = opaque;")
        lines.append("    arti_update_irqs(s);")
        lines.append("    timer_mod(s->irq_timer, qemu_clock_get_ns(QEMU_CLOCK_HOST) + {});".format(poll_ns))
        lines.append("}")
        lines.append("")

    if has_display:
        lines.append("#define ARTI_FB_OFFSET 0x{:x}u".format(config.display_framebuffer_offset))
        lines.append("#define ARTI_FB_WIDTH {}u".format(config.display_width))
        lines.append("#define ARTI_FB_HEIGHT {}u".format(config.display_height))
        lines.append("#define ARTI_FB_BPP 32u")
        lines.append("#define ARTI_FB_STRIDE (ARTI_FB_WIDTH * 4u)")
        lines.append("#define ARTI_FB_SIZE 0x{:x}u".format(config.display_framebuffer_size))
        if has_mmio_vram:
            lines.append("#define ARTI_MMIO_EXTENT (ARTI_FB_OFFSET + ARTI_FB_SIZE)")
        if has_guest_scanout:
            lines.append("#define ARTI_SCANOUT_ADDR_REG 0x{:x}u".format(config.display_address_register))
            lines.append("#define ARTI_SCANOUT_STRIDE_REG 0x{:x}u".format(config.display_stride_register))
        lines.append("")
        lines.append("static void arti_gfx_invalidate(void *opaque)")
        lines.append("{")
        lines.append("    ArtiRtlState *s = opaque;")
        lines.append("    s->invalidate = true;")
        lines.append("}")
        lines.append("")
        lines.append("static bool arti_gfx_update(void *opaque)")
        lines.append("{")
        lines.append("    ArtiRtlState *s = opaque;")
        lines.append("    DisplaySurface *surface = qemu_console_surface(s->con);")
        lines.append("    unsigned y;")
        lines.append("")
        lines.append("    if (surface_width(surface) != ARTI_FB_WIDTH ||")
        lines.append("        surface_height(surface) != ARTI_FB_HEIGHT) {")
        lines.append("        qemu_console_resize(s->con, ARTI_FB_WIDTH, ARTI_FB_HEIGHT);")
        lines.append("        surface = qemu_console_surface(s->con);")
        lines.append("    }")
        if has_guest_scanout:
            lines.append("    if (!s->scanout_addr) return false;")
            lines.append("    uint32_t *dst;")
            lines.append("    uint32_t src[ARTI_FB_WIDTH];")
            lines.append("    unsigned stride = s->scanout_stride ? s->scanout_stride : ARTI_FB_STRIDE;")
            lines.append("    for (y = 0; y < ARTI_FB_HEIGHT; y++) {")
            lines.append("        if (address_space_read(&address_space_memory, s->scanout_addr + y * stride,")
            lines.append("                               MEMTXATTRS_UNSPECIFIED, src, ARTI_FB_STRIDE) != MEMTX_OK)")
            lines.append("            return false;")
            lines.append("        dst = (uint32_t *)(surface_data(surface) + y * surface_stride(surface));")
            lines.append("        for (unsigned x = 0; x < ARTI_FB_WIDTH; x++) dst[x] = src[x] >> 8;")
            lines.append("    }")
        else:
            lines.append("    for (y = 0; y < ARTI_FB_HEIGHT; y++) {")
            lines.append("        memcpy(surface_data(surface) + y * surface_stride(surface),")
            lines.append("               s->vram + y * ARTI_FB_STRIDE, ARTI_FB_STRIDE);")
            lines.append("    }")
        lines.append("    qemu_console_update_full(s->con);")
        lines.append("    s->invalidate = false;")
        lines.append("    return true;")
        lines.append("}")
        lines.append("")
        lines.append("static const GraphicHwOps arti_gfx_ops = {")
        lines.append("    .invalidate = arti_gfx_invalidate,")
        lines.append("    .gfx_update = arti_gfx_update,")
        lines.append("};")
        lines.append("")

    lines.append("static uint64_t arti_read(void *opaque, hwaddr offset, unsigned size)")
    lines.append("{")
    if has_display or has_irq:
        lines.append("    ArtiRtlState *s = opaque;")
    lines.append("    uint64_t data = 0;")
    if has_mmio_vram:
        lines.append("    if (offset >= ARTI_FB_OFFSET &&")
        lines.append("        offset + size <= ARTI_FB_OFFSET + ARTI_FB_SIZE) {")
        lines.append("        memcpy(&data, s->vram + (offset - ARTI_FB_OFFSET), size);")
        lines.append("        return data;")
        lines.append("    }")
    lines.append("    if (arti_rtl_model_read(offset, &data, size) != 0) {")
    if has_irq:
        lines.append("        arti_update_irqs(s);")
    lines.append("        return 0;")
    lines.append("    }")
    if has_irq:
        lines.append("    arti_update_irqs(s);")
    lines.append("    return data;")
    lines.append("}")
    lines.append("static void arti_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)")
    lines.append("{")
    if has_display:
        lines.append("    ArtiRtlState *s = opaque;")
    if has_mmio_vram:
        lines.append("    if (offset >= ARTI_FB_OFFSET &&")
        lines.append("        offset + size <= ARTI_FB_OFFSET + ARTI_FB_SIZE) {")
        lines.append("        memcpy(s->vram + (offset - ARTI_FB_OFFSET), &value, size);")
        lines.append("        s->invalidate = true;")
        lines.append("        return;")
        lines.append("    }")
    if has_guest_scanout:
        lines.append("    if (offset == ARTI_SCANOUT_ADDR_REG && size == 4) {")
        lines.append("        s->scanout_addr = (uint32_t)value;")
        lines.append("        s->invalidate = true;")
        lines.append("    } else if (offset == ARTI_SCANOUT_STRIDE_REG && size == 4) {")
        lines.append("        s->scanout_stride = (uint32_t)value;")
        lines.append("        s->invalidate = true;")
        lines.append("    }")
    elif has_irq and not has_display:
        lines.append("    ArtiRtlState *s = opaque;")
    lines.append("    arti_rtl_model_write(offset, value, size);")
    if has_irq:
        lines.append("    arti_update_irqs(s);")
    lines.append("}")
    lines.append("static const MemoryRegionOps arti_ops = {")
    lines.append("    .read = arti_read, .write = arti_write,")
    lines.append("    .endianness = DEVICE_LITTLE_ENDIAN,")
    lines.append("    .valid = { .min_access_size = 1, .max_access_size = 8 },")
    lines.append("};")
    lines.append("static void arti_realize(DeviceState *dev, Error **errp)")
    lines.append("{")
    lines.append("    ArtiRtlState *s = ARTI_RTL(dev);")
    lines.append("    arti_rtl_model_init();")
    lines.append("    arti_rtl_model_set_memory_callbacks(arti_guest_read, arti_guest_write);")
    if has_mmio_vram:
        lines.append("    s->vram = g_malloc0(ARTI_FB_SIZE);")
    if has_guest_scanout:
        lines.append("    s->scanout_addr = 0;")
        lines.append("    s->scanout_stride = ARTI_FB_STRIDE;")
    lines.append("    memory_region_init_io(&s->mmio, OBJECT(s), &arti_ops, s,")
    if has_mmio_vram:
        lines.append("                          TYPE_ARTI_RTL, ARTI_MMIO_EXTENT);")
    else:
        lines.append("                          TYPE_ARTI_RTL, ARTI_MMIO_SIZE);")
    if has_irq:
        lines.append("    for (int i = 0; i < {}; i++) {{".format(irq_count))
        lines.append("        sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->irq[i]);")
        lines.append("        s->irq_prev[i] = -1;")
        lines.append("    }")
        lines.append("    s->irq_timer = timer_new_ns(QEMU_CLOCK_HOST, arti_irq_timer, s);")
        lines.append("    timer_mod(s->irq_timer, qemu_clock_get_ns(QEMU_CLOCK_HOST) + {});".format(poll_ns))
    lines.append("    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mmio);")
    if has_display:
        lines.append("    s->con = qemu_graphic_console_create(dev, 0, &arti_gfx_ops, s);")
    lines.append("}")
    if has_mmio_vram:
        lines.append("static void arti_unrealize(DeviceState *dev)")
        lines.append("{")
        lines.append("    ArtiRtlState *s = ARTI_RTL(dev);")
        lines.append("    g_free(s->vram);")
        lines.append("}")
    lines.append("static void arti_class_init(ObjectClass *klass, const void *data)")
    lines.append("{")
    lines.append("    DeviceClass *dc = DEVICE_CLASS(klass);")
    lines.append("    dc->realize = arti_realize;")
    if has_mmio_vram:
        lines.append("    dc->unrealize = arti_unrealize;")
    lines.append("}")
    lines.append("static const TypeInfo arti_info = {")
    lines.append("    .name = TYPE_ARTI_RTL, .parent = TYPE_SYS_BUS_DEVICE,")
    lines.append("    .instance_size = sizeof(ArtiRtlState), .class_init = arti_class_init,")
    lines.append("};")
    lines.append("static void arti_register_types(void) { type_register_static(&arti_info); }")
    lines.append("type_init(arti_register_types)")

    return "\n".join(lines) + "\n"
